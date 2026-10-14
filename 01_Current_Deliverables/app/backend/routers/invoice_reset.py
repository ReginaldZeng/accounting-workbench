# [Change Log] Date: 2026-10-13 | Author: Claude / c | Version: V2.943（发票管家·清空试运行数据）
# Description: Owner 2026-10-13 定：试运行的数据清掉重新开始，后补单留着，钉钉审批单从指定日期起重新接。
#   只有主管理员能点（设置 › 高级）；不是直接删——先把要清的每一行存成一份留底（inv_uploads/_archive/<时间>/rows.json.gz），
#   库里清掉以后再把对应的原件挪进同一个留底目录（挪，不删：原件是凭证）。留底目录不在取图接口的路径规则里，外面拿不到。
#   留下的：后补单、后补单挂的票夹（连同票夹里的票、文件、经办记录）、不挂票夹的资料、期初导入、设置、手机配对/自助登录会话。
#   清掉的：其余票夹、票和附件、文件、操作记录、新销方名单、税局清单和导入批次、工作台"当前票夹"指针。
#   清完把钉钉自动接入的起始日改成指定那天，并让下一轮整段重扫（已有票夹的单子不会重复接）。
"""发票管家：清空试运行数据（主管理员）。GET /api/inv/admin/reset 看会清多少；POST 同一地址真清。"""
import gzip
import json
import os
import shutil
import threading
from datetime import datetime, timedelta

from fastapi import APIRouter, Request
from sqlalchemy import select, delete
from starlette.concurrency import run_in_threadpool

from core import db
from kernels import invoice_store as S
from routers import invoice as inv
from routers import invoice_intake as intake

router = APIRouter()

PHRASE = "清零"                    # 真清之前要人手打的确认字样
ARCHIVE_DIR = "_archive"           # 留底目录（在 inv_uploads 下；下划线开头，取图接口的路径规则不认）
_LOCK = threading.Lock()


def _chunks(xs, n=500):
    xs = list(xs)
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def _plan(cx):
    """现在库里的东西按「留 / 清」分开 → 各表要清的主键、要留的张数、现在还不能清的原因。"""
    I, F, L = S.ITEM.c, S.FILE.c, S.LOG.c
    keep_f = set(cx.execute(select(S.LATER.c.folder_id).where(S.LATER.c.folder_id.isnot(None))).scalars())
    keep_f |= set(cx.execute(select(I.folder_id).where(I.later_id.isnot(None), I.folder_id.isnot(None))).scalars())
    folders = [dict(r) for r in cx.execute(select(S.FOLDER.c.id, S.FOLDER.c.attach_status)).mappings()]
    items = [dict(r) for r in cx.execute(select(I.id, I.folder_id, I.later_id, I.kind, I.proc_status, I.seller_tax_id)).mappings()]
    files = [dict(r) for r in cx.execute(select(F.id, F.folder_id, F.item_id, F.size)).mappings()]
    logs = [dict(r) for r in cx.execute(select(L.id, L.folder_id, L.item_id, L.later_id)).mappings()]

    def gone(fid):                     # 这个票夹号在清的范围里（没挂票夹的另算）
        return fid is not None and fid not in keep_f

    clear_f = [f for f in folders if f["id"] not in keep_f]
    clear_i = [x for x in items if x["later_id"] is None and (x["folder_id"] is None or gone(x["folder_id"]))]
    ci = {x["id"] for x in clear_i}
    keep_i = [x for x in items if x["id"] not in ci]
    ki = {x["id"] for x in keep_i}
    # 不挂票夹也不挂票的文件＝后补单的补传资料等：留
    clear_fl = [x for x in files if gone(x["folder_id"]) or (x["folder_id"] is None and x["item_id"] in ci)]
    clear_lg = [x for x in logs if x["later_id"] is None and x["item_id"] not in ki
                and (x["folder_id"] is None or gone(x["folder_id"]))]
    keep_tax = {str(x["seller_tax_id"] or "").strip().upper() for x in keep_i if x["kind"] == "invoice"} - {""}
    sellers = [dict(r) for r in cx.execute(select(S.SELLER.c.id, S.SELLER.c.tax_id)).mappings()]
    clear_s = [x for x in sellers if str(x["tax_id"] or "").strip().upper() not in keep_tax]
    busy = []
    n = sum(1 for f in clear_f if f["attach_status"] in ("pending", "running"))
    if n:
        busy.append("还有 %d 个票夹正在拉钉钉附件" % n)
    n = sum(1 for x in clear_i if x["proc_status"] in ("pending", "running"))
    if n:
        busy.append("还有 %d 张票正在识别" % n)
    ids = {
        "inv_log": [x["id"] for x in clear_lg],
        "inv_file": [x["id"] for x in clear_fl],
        "inv_item": [x["id"] for x in clear_i],
        "inv_seller": [x["id"] for x in clear_s],
        "inv_taxlist": list(cx.execute(select(S.TAXLIST.c.id)).scalars()),
        "inv_batch": list(cx.execute(select(S.BATCH.c.id).where(S.BATCH.c.kind != "opening")).scalars()),
        "inv_folder": [f["id"] for f in clear_f],
    }
    clear = {"folders": len(clear_f), "invoices": sum(1 for x in clear_i if x["kind"] == "invoice"),
             "others": sum(1 for x in clear_i if x["kind"] != "invoice"), "files": len(clear_fl),
             "bytes": int(sum(int(x["size"] or 0) for x in clear_fl)), "logs": len(clear_lg), "sellers": len(clear_s),
             "taxlist": len(ids["inv_taxlist"])}
    keep = {"laters": len(cx.execute(select(S.LATER.c.id)).scalars().all()), "folders": len(folders) - len(clear_f),
            "items": len(keep_i), "files": len(files) - len(clear_fl), "logs": len(logs) - len(clear_lg)}
    return {"ids": ids, "clear": clear, "keep": keep, "busy": busy}


_TABLES = {"inv_log": S.LOG, "inv_file": S.FILE, "inv_item": S.ITEM, "inv_seller": S.SELLER, "inv_taxlist": S.TAXLIST,
           "inv_batch": S.BATCH, "inv_folder": S.FOLDER}
_ORDER = ("inv_log", "inv_file", "inv_item", "inv_seller", "inv_taxlist", "inv_batch", "inv_folder")


def _dump(cx, ids, desk_folders):
    """要清的行原样读出来 → {表名: [行…]}（留底用）。"""
    out = {}
    for name in _ORDER:
        t, rows = _TABLES[name], []
        for ch in _chunks(ids[name]):
            rows += [dict(r) for r in cx.execute(select(t).where(t.c.id.in_(ch)).order_by(t.c.id)).mappings()]
        out[name] = rows
    desk = []
    for ch in _chunks(desk_folders):
        desk += [dict(r) for r in cx.execute(select(S.DESK).where(S.DESK.c.folder_id.in_(ch))).mappings()]
    out["inv_desk"] = desk
    return out


def _write_dump(root, meta, tables):
    """留底写盘并读回来核一遍行数；对不上就抛（后面不会去清库）。→ 文件路径。"""
    p = os.path.join(root, "rows.json.gz")
    with gzip.open(p + ".part", "wb") as fh:
        fh.write(json.dumps({"meta": meta, "tables": tables}, ensure_ascii=False, default=str).encode("utf-8"))
    os.replace(p + ".part", p)
    with gzip.open(p, "rb") as fh:
        back = json.loads(fh.read().decode("utf-8"))["tables"]
    for name, rows in tables.items():
        if len(back.get(name) or []) != len(rows):
            raise RuntimeError("留底没写全（%s）" % name)
    return p


def _move_files(root, file_rows):
    """已清掉的文件行对应的原件/预览/缩略图 → 留底目录（同一块盘上是改名，不占空间）。→ (挪了几个, 没挪成的说明[])。"""
    base = os.path.realpath(inv.UPLOAD_DIR)
    todo, moved, fails = {}, 0, []
    for r in file_rows:
        for rel in (r.get("orig_path"), r.get("preview_path"), r.get("thumb_path")):
            rel = str(rel or "").replace("\\", "/")
            if inv._REL_RE.match(rel):
                todo.setdefault(rel.rsplit("/", 1)[0], set()).add(int(r["id"]))
    for d, fids in sorted(todo.items()):
        src = os.path.join(base, *d.split("/"))
        try:
            names = os.listdir(src)
        except OSError:
            continue
        dst = os.path.join(root, "files", *d.split("/"))
        for nm in names:
            head = nm.split("_", 1)[0]
            if not head.isdigit() or int(head) not in fids:
                continue
            try:
                os.makedirs(dst, exist_ok=True)
                try:
                    os.replace(os.path.join(src, nm), os.path.join(dst, nm))
                except OSError:
                    shutil.move(os.path.join(src, nm), os.path.join(dst, nm))
                moved += 1
            except OSError as ex:
                fails.append("%s/%s：%s" % (d, nm, ex))
        try:
            os.rmdir(src)              # 挪空了的票夹目录顺手收掉；里面还有东西就留着
        except OSError:
            pass
    return moved, fails


def _do(user, since):
    e = inv.E()
    who = inv._uname(user)
    with e.connect() as cx:
        plan = _plan(cx)
        if plan["busy"]:
            return None, "；".join(plan["busy"]) + "——等它跑完（一两分钟）再点"
        ids = plan["ids"]
        tables = _dump(cx, ids, ids["inv_folder"])
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = os.path.join(inv.UPLOAD_DIR, ARCHIVE_DIR, stamp)
    os.makedirs(root, exist_ok=True)
    meta = {"at": inv.now_s(), "by": who, "since": since, "clear": plan["clear"], "keep": plan["keep"]}
    _write_dump(root, meta, tables)
    with e.begin() as cx:              # 只删留底里有的那些行（按主键），一个事务：要么全清要么不动
        for name in _ORDER:
            t = _TABLES[name]
            for ch in _chunks(ids[name]):
                cx.execute(delete(t).where(t.c.id.in_(ch)))
        for ch in _chunks(ids["inv_folder"]):
            cx.execute(delete(S.DESK).where(S.DESK.c.folder_id.in_(ch)))
    moved, fails = _move_files(root, tables["inv_file"])
    # 钉钉自动接入：起始日改成指定那天，下一轮从那天起整段重扫
    cur = inv.get_settings().get("intake") or {}
    inv.save_settings({"intake": {"approvers": cur.get("approvers") or [], "since": since}}, who)
    st = intake._load_state()
    st["since"] = ""
    # 审票分工以后，接哪些单看每位接入审批人自己的启用日：清零不改各人的启用日，只告诉下一轮「从这天起整段重列一遍」
    # （比某位的启用日还早的那一段，照他的启用日算，不会多接）。
    st["rescanFrom"] = since
    db.set_setting(intake.STATE_KEY, st, who)
    rel = "inv_uploads/%s/%s" % (ARCHIVE_DIR, stamp)
    c, k = plan["clear"], plan["keep"]
    inv.audit(user, "清空试运行数据", "从 %s 起重新接钉钉审批单" % since,
              "清掉：票夹 %d 个、发票 %d 张、其他附件 %d 条、文件 %d 个、操作记录 %d 条、新销方 %d 个；"
              "留下：后补单 %d 张、票夹 %d 个、票据 %d 条、文件 %d 个；留底在 %s（挪过去原件 %d 个%s）" % (
                  c["folders"], c["invoices"], c["others"], c["files"], c["logs"], c["sellers"],
                  k["laters"], k["folders"], k["items"], k["files"], rel, moved,
                  "，%d 个没挪成" % len(fails) if fails else ""))
    return {"ok": True, "cleared": c, "kept": k, "since": since, "archive": rel, "movedFiles": moved, "fileErrors": fails[:20],
            "msg": "已清空：票夹 %d 个、发票 %d 张、文件 %d 个。留底在服务器 %s。钉钉审批单从 %s 起重新接，过几分钟到收票工作台看。" % (
                c["folders"], c["invoices"], c["files"], rel, since)}, None


def _run(user, since):
    if not _LOCK.acquire(blocking=False):
        return None, "正在清，别重复点"
    try:
        if not intake._LOCK.acquire(timeout=90):
            return None, "钉钉自动接入正在扫单，过一两分钟再点"
        try:
            return _do(user, since)
        finally:
            intake._LOCK.release()
    finally:
        _LOCK.release()


def _super(request):
    u, bad = inv.need(request, None, inv.CAP_CONFIG)
    if bad:
        return None, bad
    if not db.is_super(u):
        return None, inv.err("只有主管理员能清空数据", 403)
    return u, None


def _preview():
    with inv.E().connect() as cx:
        plan = _plan(cx)
    cur = inv.get_settings().get("intake") or {}
    return {"ok": True, "clear": plan["clear"], "keep": plan["keep"], "busy": plan["busy"], "phrase": PHRASE,
            "since": cur.get("since") or "", "approvers": len(cur.get("approvers") or []),
            "scanning": intake._LOCK.locked(), "maxDays": intake.MAX_WINDOW_DAYS}


@router.get("/api/inv/admin/reset")
async def reset_preview(request: Request):
    """现在点下去会清多少、留多少（只读）。"""
    u, bad = _super(request)
    if bad:
        return bad
    return await run_in_threadpool(_preview)


@router.post("/api/inv/admin/reset")
async def reset_run(request: Request):
    """真清：要手打确认字样、给重新接钉钉审批单的起始日。先留底再清，原件挪进留底目录不删。"""
    u, bad = _super(request)
    if bad:
        return bad
    body = await inv.body_json(request)
    if inv._s(body.get("phrase"), 20) != PHRASE:
        return inv.err("确认字样不对：请在框里打「%s」两个字" % PHRASE, 400)
    try:
        since = inv._norm_date(body.get("since"))
    except ValueError:
        since = ""
    if not since:
        return inv.err("请填从哪天起重新接钉钉审批单（例如 2026-09-01）", 400)
    today = datetime.now().date()
    d = datetime.strptime(since, "%Y-%m-%d").date()
    if d > today:
        return inv.err("起始日不能晚于今天", 400)
    if d < today - timedelta(days=intake.MAX_WINDOW_DAYS):
        return inv.err("钉钉最多只能往前取 %d 天的审批单，起始日最早填 %s" % (
            intake.MAX_WINDOW_DAYS, (today - timedelta(days=intake.MAX_WINDOW_DAYS)).isoformat()), 400)
    res, msg = await run_in_threadpool(_run, u, since)
    if msg:
        return inv.err("没清：" + msg, 409)
    if intake._STATE.get("started"):      # 线上：不等下一轮，马上从新起始日重扫一遍（本机/测试不起定时线程，也不去扫真钉钉）
        threading.Thread(target=intake.scan_once, args=("清零后重新接入",), name="inv-intake-rescan", daemon=True).start()
    return res
