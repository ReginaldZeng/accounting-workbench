# [Change Log]
# Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家）
# Description: 发票管家·台账与后补池路由（B2）——发票台账（查询/导出/作废）、税局对账（清单四色验真＋文件包补全）、
#   抵扣勾选标注、新销方核查、期初导入（票总管）、发票后补池（代填/收票/标记/催票/修改/关闭/导出）、每日催票线程。
#   权限闸、设置、视图、进票流水线、文件落盘与唯一发消息出口 notify_dt 全部复用 routers/invoice.py，不另起一套。
#   催票线程只在非 SQLite 库起（本地/测试不催）；测试直接调 remind_tick()。
# Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家·审查修复）
# Description: 代码审查修复——台账「含作废」只取已审核＋已作废、导出边读边写且合计不含作废/拆分票按分摊额；
#   税局对账只判清单所属公司的票、绿不降黄、部分红冲不算作废、清单显示已勾选记"已勾选"；税局文件包逐包处理、
#   已审票只补空字段（对不上只标出来）；作废/收票/改票后后补单统一重算（另有兜底自愈），发钉钉与重算一律进线程池；
#   催票"今天已跑"取清单成功后才算；登记后补回 notified/notifyMsg。
"""发票管家（Invoice Butler）台账与后补池路由。接口契约见 docs/20260923_发票管家/发票管家_技术方案 §5.2。"""
import os
import re
import json
import math
import time
import hashlib
import secrets
import threading
import traceback
from datetime import datetime, date, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy import select, update, insert, or_, func
from starlette.concurrency import run_in_threadpool

from core import db
from routers import invoice as inv
from kernels import invoice_parse as ip
from kernels import invoice_store as S
from kernels import invoice_excel as ie

router = APIRouter()

E = inv.E
err = inv.err
need = inv.need
ENTER_LEDGER, ENTER_LATER = inv.ENTER_LEDGER, inv.ENTER_LATER
CAP_DEDUCT, CAP_UNBIND, CAP_OPENING, CAP_RECEIVE = inv.CAP_DEDUCT, inv.CAP_UNBIND, inv.CAP_OPENING, inv.CAP_RECEIVE

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TMP_TTL = 2 * 3600                  # 抵扣标注件/期初预览只留 2 小时：过了就要重新拖，免得拿旧判断去税局勾选
EXPORT_CAP = 100000                 # 导出上限（一年发票量的几倍）；导出边读边写，行数多也不占多少内存
REPORT_CAP = 2000                   # 对账报告每张清单最多存这么多行（前端只列前 200）
LIST_COVER = ("special", "normal", "travel", "toll", "train", "flight", "vehicle")   # 税局「取得发票」清单能覆盖的票种
# 台账审核状态筛选：approved（默认）/ withvoid（页面「含作废」＝已审核＋已作废）/ void / pending / draft / returned / all（仅内部用）
LEDGER_REVIEWS = S.LEDGER_REVIEWS
LEDGER_EXTRA_COLUMNS = ["审核状态", "本单分摊额"]   # 导出在票面列后加两列：含作废时看得出哪行作废；拆分票看得出本单算多少
REVIEW_CN = {"approved": "已审核", "void": "已作废", "pending": "待审核", "draft": "收票中", "returned": "已退回"}
LATER_ACTIVE = ("open", "partial")
LATER_STATUSES = ("open", "partial", "done", "closed", "overdue")
LATER_KIND_CN = {"special": "专票", "normal": "普票", "receipt": "收据"}
LATER_TAX_RATES = ("13%", "9%", "6%", "5%", "3%", "1%", "0%", "免税", "不征税")


def norm_tax_rate(v):
    """后补单税率 → 标准写法（13 / 13% / 0.13 → 13%；免税、不征税原样）；认不出 → ""。"""
    s = str(v or "").strip().replace("％", "%").replace(" ", "")
    if s in LATER_TAX_RATES:
        return s
    m = re.fullmatch(r"(\d+(?:\.\d+)?)%?", s)
    if not m:
        return ""
    x = float(m.group(1))
    if x < 1 and "%" not in s and x > 0:
        x *= 100
    r = ("%g" % round(x, 2)) + "%"
    return r if r in LATER_TAX_RATES else ""
LATER_STATUS_CN = {"open": "待收", "partial": "部分到票", "done": "已收齐", "closed": "已关闭"}
SELLER_RESULTS = ("未查", "无记录", "命中")
MSG_HEAD = "【核算工作台·发票管家】"
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{16,64}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
_LATER_LOCK = threading.RLock()     # 后补单"重算已到金额＋改状态＋发签收通知"要原子：两个请求同时收票只能发一次签收
_RESOLVED = {}                      # instId → (过期 epoch, 规范化审批单)：扫付款单时取过一次，登记时不再打钉钉
_RESOLVED_LOCK = threading.Lock()


# ───────────────────────── 通用小工具 ─────────────────────────

def _page_args(request, dflt=50, cap=500):
    qp = request.query_params
    return inv._int(qp.get("page"), 1, 1000000, 1), inv._int(qp.get("size"), 1, cap, dflt)


def _stamp():
    return datetime.now().strftime("%Y%m%d")


def _xlsx(data, filename, ascii_name):
    """xlsx 下载响应：中文文件名走 RFC5987，另给一个 ASCII 名兜老浏览器。"""
    disp = "attachment; filename=%s; filename*=UTF-8''%s" % (ascii_name, quote(filename))
    return Response(content=data, media_type=XLSX_MIME,
                    headers={"Content-Disposition": disp, "X-Content-Type-Options": "nosniff"})


def _active():
    return or_(S.ITEM.c.status.is_(None), S.ITEM.c.status != "removed")


def _not_void():
    return func.coalesce(S.ITEM.c.review, "") != "void"


def _chunks(xs, n=500):
    xs = list(xs)
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def _f(v):
    # 金额 → float（两位）；读不出、NaN/无穷大、超出库里装得下的（±1e16）一律当没有
    try:
        x = None if v is None else round(float(v), 2)
    except (TypeError, ValueError):
        return None
    if x is None or not math.isfinite(x) or abs(x) >= 1e16:
        return None
    return x


def _ours(settings):
    """本公司抬头 → (税号集合, 规范化名称集合)。"""
    comps = (settings or {}).get("company") or []
    tids = {str(c.get("taxId") or "").strip().upper() for c in comps if c.get("taxId")}
    names = {ip.normalize_name(c.get("name")) for c in comps if c.get("name")}
    return tids, names


def _is_ours(tax_id, name, ours):
    # 有税号只认税号（名称常有错字）；票上没读到税号时才退到名称
    t = str(tax_id or "").strip().upper()
    if t:
        return t in ours[0]
    return bool(name) and ip.normalize_name(name) in ours[1]


def _row_ours(r, ours):
    # 「取得发票」清单本来就是开给我们的票：清单没导出购方列（行里没有购方）时按本公司算
    if not r.get("buyerTaxId") and not r.get("buyerName"):
        return True
    return _is_ours(r.get("buyerTaxId"), r.get("buyerName"), ours)


def _folder_brief(f):
    f = f or {}
    if not f.get("id"):
        return None
    return {"id": f.get("id"), "businessId": f.get("business_id") or "", "title": f.get("title") or "",
            "applicant": f.get("applicant") or "", "template": f.get("template") or "", "dept": f.get("dept") or "",
            "payee": {"name": f.get("payee_name") or ""}}


def _folders_map(e, ids):
    ids = [i for i in set(ids or []) if i]
    out = {}
    if ids:
        with e.connect() as cx:
            for ch in _chunks(ids):
                for r in cx.execute(select(S.FOLDER).where(S.FOLDER.c.id.in_(ch))):
                    r = S._row(r)
                    out[r["id"]] = r
    return out


def _batch_view(b):
    if not b:
        return None
    return {"id": b["id"], "kind": b.get("kind") or "", "name": b.get("name") or "", "rows": int(b.get("rows") or 0),
            "createdBy": b.get("created_by") or "", "createdAt": b.get("created_at") or ""}


def _log_many(rows):
    """inv_log 批量写（对账一次改上千张票的状态，逐条开事务太慢）。rows=[dict(user,action,folder_id,item_id,later_id,detail)]。"""
    if not rows:
        return
    ts = inv.now_s()
    try:
        with E().begin() as cx:
            for ch in _chunks(rows):
                cx.execute(insert(S.LOG), [S._prep(S.LOG, dict(r, ts=ts, action=(r.get("action") or "")[:40])) for r in ch])
    except Exception as ex:   # 留痕失败不能把已做成的对账打回去
        print("[发票管家] 批量写票据留痕失败：%s" % ex)


async def _one_upload(request):
    """单文件导入（表单字段 file）→ ((文件名, 字节), None) 或 (None, 错误响应)。"""
    files, fields, errors, bad = await inv.read_upload_files(request)
    if bad:
        return None, bad
    if not files:
        return None, err(errors[0]["msg"] if errors else "没收到文件：请把 Excel 拖进来", 400)
    return files[0], None


# ───────────────────────── 临时件（抵扣标注结果 / 期初预览） ─────────────────────────

def _tmp_dir():
    d = os.path.join(inv.UPLOAD_DIR, "_tmp")      # _tmp 不匹配取图接口的路径规则，外面拿不到
    os.makedirs(d, exist_ok=True)
    return d


def _tmp_clean():
    try:
        d = _tmp_dir()
        now = time.time()
        for fn in os.listdir(d):
            p = os.path.join(d, fn)
            try:
                if os.path.isfile(p) and now - os.path.getmtime(p) > TMP_TTL:
                    os.remove(p)
            except OSError:
                pass
    except OSError:
        pass


def _tmp_path(token, ext):
    if not token or not _TOKEN_RE.match(str(token)):
        return None
    return os.path.join(_tmp_dir(), str(token) + ext)


def _tmp_write(path, data):
    part = path + ".part"
    with open(part, "wb") as fh:
        fh.write(data)
    os.replace(part, path)


def _tmp_meta(token, kind):
    """→ (meta, None) 或 (None, 中文原因)。"""
    p = _tmp_path(token, ".json")
    if not p:
        return None, "凭证不对，请重新拖一次文件"
    try:
        with open(p, "rb") as fh:
            meta = json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError):
        return None, "文件已过期（只保留 2 小时）或已经用过了，请重新拖一次"
    if meta.get("kind") != kind:
        return None, "凭证不对，请重新拖一次文件"
    if time.time() - float(meta.get("created") or 0) > TMP_TTL:
        return None, "文件已过期（只保留 2 小时），请重新拖一次"
    return meta, None


# ═══════════════════════════ 发票台账 ═══════════════════════════

def _ledger_filters(qp):
    """查询参数 → (store.ledger_query 的 filters, 错误原因)。日期统一成 YYYY-MM-DD。"""
    f = {}
    for k in ("q", "invType", "verify", "deduct", "seller", "kind"):
        v = inv._s(qp.get(k), 100)
        if v:
            f[k] = v
    for k in ("from", "to"):
        raw = inv._s(qp.get(k), 20)
        if raw:
            try:
                f[k] = inv._norm_date(raw)
            except ValueError:
                return None, "日期格式不对（要 2026-09-01 这样）：%s" % raw
    rv = inv._s(qp.get("review"), 12) or "approved"
    f["review"] = rv if rv in LEDGER_REVIEWS else "approved"
    return f, None


def _ledger_row_values(r):
    """台账导出一行（S.ledger_iter 的库行，snake_case，附 folder）→ 按 ie.LEDGER_COLUMNS + LEDGER_EXTRA_COLUMNS 排好的值。"""
    f = r.get("folder") or {}
    kind = r.get("kind") or "invoice"
    if kind != "invoice":
        typ = ie.KIND_LABELS.get(kind, kind)
    else:
        typ = r.get("type_label") or ie.INV_TYPE_LABELS.get(r.get("inv_type") or "", r.get("inv_type") or "")
    split = bool(r.get("split")) and r.get("alloc") is not None
    return [f.get("business_id"), f.get("template"), f.get("applicant"), f.get("dept"),
            f.get("payee_name") or f.get("title"), typ, r.get("code"), r.get("number"), r.get("issue_date"),
            r.get("seller_name"), r.get("seller_tax_id"), r.get("buyer_name"), r.get("buyer_tax_id"),
            r.get("amount"), r.get("tax"), r.get("total"), r.get("tax_rate"), r.get("category"),
            ie.VERIFY_LABELS.get(r.get("verify") or "", r.get("verify")),
            ie._yesno_deductible(r.get("deductible")),
            ie.DEDUCT_STATUS_LABELS.get(r.get("deduct_status") or "", r.get("deduct_status")),
            r.get("created_by"), r.get("created_at"), r.get("review_by"), r.get("review_at"),
            ie.ORIGIN_LABELS.get(r.get("origin") or "", r.get("origin")),
            REVIEW_CN.get(r.get("review") or "", r.get("review") or ""), r.get("alloc") if split else None]


def _ledger_export_bytes(filters):
    """台账导出：S.ledger_iter 边读边写（只写模式），几万行也不把整表连同 JSON 大字段装进内存。
    合计行口径和页面一致：不含已作废（除非筛的就是作废）；拆分票按本单分摊额算（金额、税额同比例折算），
    一张票拆给几张单不会按票面重复累加。→ (xlsx 字节, 行数)。"""
    cols = list(ie.LEDGER_COLUMNS) + LEDGER_EXTRA_COLUMNS
    wb = ie._new_wb()
    sw = ie._SheetWriter(wb, "发票台账", cols, money=("金额", "税额", "价税合计", "本单分摊额"),
                         text=("审批编号", "发票代码", "发票号码", "销方税号", "购方税号"),
                         widths={"审批编号": 22, "收款方/事由": 30, "发票号码": 24, "发票代码": 14, "开票日期": 12,
                                 "销方名称": 32, "购方名称": 32, "销方税号": 22, "购方税号": 22, "项目类别": 20,
                                 "登记时间": 19, "审核时间": 19, "票种": 16, "验真": 12, "本单分摊额": 14})
    with_void = (filters or {}).get("review") == "void"
    sums = {"金额": 0.0, "税额": 0.0, "价税合计": 0.0, "本单分摊额": 0.0}
    n_void = n_split = 0
    for r in S.ledger_iter(E(), filters, cap=EXPORT_CAP):
        sw.add(_ledger_row_values(r))
        if r.get("review") == "void" and not with_void:
            n_void += 1
            continue
        split = bool(r.get("split")) and r.get("alloc") is not None
        total, alloc = _f(r.get("total")), _f(r.get("alloc"))
        ratio = (alloc / total) if (split and alloc is not None and total) else 1.0
        for col, k in (("金额", "amount"), ("税额", "tax")):
            x = _f(r.get(k))
            if x is not None:
                sums[col] += x * ratio
        share = alloc if split else total
        if share is not None:
            sums["价税合计"] += share
        if split:
            n_split += 1
            sums["本单分摊额"] += alloc or 0.0
    sw.sums = {cols.index(c): round(v, 2) for c, v in sums.items()}
    notes = []
    if n_void:
        notes.append("不含作废 %d 行" % n_void)
    if n_split:
        notes.append("拆分票按本单分摊额计")
    sw.finish(total_label="合计" + (("（%s）" % "；".join(notes)) if notes else ""))
    return ie._save(wb), sw.n


def _ledger_page(filters, page, size):
    total, sums, rows = S.ledger_query(E(), filters, page, size)
    views = inv.item_views(rows)
    for v, r in zip(views, rows):
        v["folder"] = _folder_brief(r.get("folder"))
    return total, sums, views


@router.get("/api/inv/ledger")
async def ledger_list(request: Request):
    u, bad = need(request, ENTER_LEDGER)
    if bad:
        return bad
    filters, msg = _ledger_filters(request.query_params)
    if msg:
        return err(msg, 400)
    page, size = _page_args(request, 50, 500)
    total, sums, views = await run_in_threadpool(_ledger_page, filters, page, size)
    return {"ok": True, "total": total, "sum": sums, "rows": views, "page": page, "size": size}


@router.get("/api/inv/ledger/export")
async def ledger_export(request: Request):
    u, bad = need(request, ENTER_LEDGER)
    if bad:
        return bad
    filters, msg = _ledger_filters(request.query_params)
    if msg:
        return err(msg, 400)
    data, n = await run_in_threadpool(_ledger_export_bytes, filters)
    inv.audit(u, "导出发票台账", "发票台账", "%d 行%s；筛选：%s" % (
        n, "（到上限，后面的没导出）" if n >= EXPORT_CAP else "", json.dumps(filters, ensure_ascii=False)))
    return _xlsx(data, "发票台账_%s.xlsx" % _stamp(), "invoice_ledger_%s.xlsx" % _stamp())


def _void_sync(u, iid, note):
    """作废一张票（线程池里跑：重判重复、重算后补单都可能发钉钉）→ (响应 dict, None) 或 (None, 错误响应)。
    作废后：号码放出来（同号票重判重复）、挂着后补单的重算已到金额、已提交票夹里待审的票没了就自动调整票夹状态。"""
    e = E()
    it = S.item_get(e, iid)
    if not it or it.get("status") == "removed":
        return None, err("票不存在（或已被移除）", 404)
    if it.get("review") == "void":
        return None, err("这张票已经作废过了", 400)
    ts = inv.now_s()
    c = S.ITEM.c
    with e.begin() as cx:        # 带条件改：两个人同时点作废只算一次
        n = cx.execute(update(S.ITEM).where(c.id == iid, _not_void(), _active()).values(
            **S._prep(S.ITEM, {"review": "void", "void_by": u["name"], "void_at": ts, "void_note": note,
                               "updated_at": ts}))).rowcount
    if n != 1:
        return None, err("这张票已经作废过了（或刚被移除）", 400)
    # 号码放出来：别的票夹里因它被标"重复"的票重判（作废票不再参与查重）
    if it.get("dup_key"):
        inv.recheck_dup_group(it["dup_key"])
    if it.get("later_id"):
        _later_recalc(it["later_id"], u["name"])
    settled = inv.folder_settle(it["folder_id"], u["name"]) if it.get("folder_id") else None
    det = {"number": it.get("number") or "", "note": note, "before": it.get("review") or "",
           "total": inv._money_str(it.get("total")) if it.get("total") is not None else ""}
    if settled:
        det["folderStatus"] = settled
    inv.log(u, "作废票据", it.get("folder_id"), iid, it.get("later_id"), det)
    inv.audit(u, "作废票据", "票#%d %s" % (iid, it.get("number") or ""), det)
    msg = "已作废，号码已放出来（以后这张票可以重新登记）"
    if it.get("deduct_status") == "checked":
        msg += "；这张票已在税局勾选抵扣，记得在税局做进项税额转出"
    elif it.get("deduct_status") == "marked":
        msg += "；这张票已标注勾选，如已导入税局请撤销勾选"
    if settled == "approved":
        msg += "；票夹里没有待审的票了，已自动记为已审核"
    elif settled == "collecting":
        msg += "；票夹里没有待审的票了，已退回「收票中」"
    return {"ok": True, "msg": msg, "item": inv.item_view(S.item_get(e, iid))}, None


@router.post("/api/inv/item/{iid}/void")
async def item_void(iid: int, request: Request):
    u, bad = need(request, ENTER_LEDGER, CAP_UNBIND)
    if bad:
        return bad
    body = await inv.body_json(request)
    note = inv._s(body.get("note"), 500)
    if not note:
        return err("作废要写原因（比如：重复报销、开错抬头已红冲），留痕备查", 400)
    out, bad = await run_in_threadpool(_void_sync, u, iid, note)
    return bad if bad else out


# ═══════════════════════════ 税局对账（清单四色验真） ═══════════════════════════

_TL_FILL = (("sellerName", "seller_name"), ("sellerTaxId", "seller_tax_id"), ("buyerName", "buyer_name"),
            ("buyerTaxId", "buyer_tax_id"), ("amount", "amount"), ("tax", "tax"), ("total", "total"),
            ("date", "issue_date"))


def _partial_red(r):
    """清单行是"部分红冲"的蓝字票（如 已红冲-部分）：原票还有效，只冲掉了一部分。"""
    st = str(r.get("status") or "")
    return "红冲" in st and "部分" in st and "全额" not in st


def _row_bad(r):
    """清单行是作废/全额红冲/负数（红字）票。部分红冲不算：原票仍有效，没冲掉的那部分照样能抵扣。"""
    st = str(r.get("status") or "")
    if "作废" in st or ("红冲" in st and not _partial_red(r)) or r.get("positive") is False:
        return True
    t = _f(r.get("total"))
    return t is not None and t < 0


_TICK_WORDS = ("勾选", "确认", "抵扣", "认证")


def _row_ticked(r):
    """清单行的勾选/用途确认状态显示"已勾选抵扣"（已勾选、已确认、已抵扣、已认证、是）。未勾选/不抵扣/撤销都不算。"""
    s = str(r.get("checkState") or "").strip()
    if not s or any(w in s for w in ("未", "不", "否", "撤销", "取消")):
        return False
    if s in ("是", "Y", "y", "√"):
        return True
    return s.startswith("已") and any(w in s for w in _TICK_WORDS)


def _list_scope(rows, st):
    """这份清单是本公司哪家（哪几家）的 → (税号集合, 规范化名称集合)；分不清 → None。
    税局「取得发票」清单按纳税人分别导出：黄（本该在清单里却没有）只能判清单所属公司的票——
    另一家公司的票这份清单本来就不会有。清单行带购方税号/名称 → 取其中是本公司的那几家；
    没有购方列 → 设置里只有一家公司时就是它，有多家就分不清。"""
    comps = (st or {}).get("company") or []
    lt = {str(r.get("buyerTaxId") or "").strip().upper() for r in rows if r.get("buyerTaxId")}
    ln = {ip.normalize_name(r.get("buyerName")) for r in rows if r.get("buyerName")}
    ln.discard("")
    if not lt and not ln:
        return _ours(st) if len(comps) <= 1 else None
    tids, names = set(), set()
    for c in comps:
        t = str(c.get("taxId") or "").strip().upper()
        n = ip.normalize_name(c.get("name")) if c.get("name") else ""
        if (t and t in lt) or (n and n in ln):
            if t:
                tids.add(t)
            if n:
                names.add(n)
    return tids, names


def _bad_label(r):
    st = str(r.get("status") or "")
    return st if ("作废" in st or "红冲" in st) else "负数发票（红字）"


def _fill_from_list(it, ref):
    """清单里有这张票：台账缺的销方/购方名称税号、金额税额、日期用清单补（来源记 taxlist，不覆盖已有值）。"""
    fs = inv._copy(it.get("field_src_json") or {})
    pend = list(it.get("pending_json") or [])
    upd, got = {}, []
    for fld, col in _TL_FILL:
        nv = ref.get(fld)
        if inv._empty(nv) or not inv._empty(it.get(col)):
            continue
        if fld in inv.MONEY_FIELDS:
            nv = _f(nv)
            if nv is None:
                continue
        upd[col] = nv
        fs[fld] = {"src": "taxlist", "page": int(it.get("page") or 0), "box": None}
        if fld in pend:
            pend.remove(fld)
        got.append(fld)
    if upd:
        upd["field_src_json"] = fs
        upd["pending_json"] = pend
    return upd, got


def _item_brief(it, fmap):
    f = fmap.get(it.get("folder_id")) or {}
    return {"id": it["id"], "folderId": it.get("folder_id"), "number": it.get("number") or "", "code": it.get("code") or "",
            "date": it.get("issue_date") or "", "sellerName": it.get("seller_name") or "",
            "sellerTaxId": it.get("seller_tax_id") or "", "buyerTaxId": it.get("buyer_tax_id") or "",
            "total": it.get("total"), "invType": it.get("inv_type") or "", "typeLabel": it.get("type_label") or "",
            "review": it.get("review") or "", "laterId": it.get("later_id"), "folder": _folder_brief(f),
            "businessId": f.get("business_id") or "", "applicant": f.get("applicant") or ""}


def _list_row_brief(r):
    return {"number": r.get("number") or "", "code": r.get("code") or "", "date": r.get("date") or "",
            "sellerName": r.get("sellerName") or "", "sellerTaxId": r.get("sellerTaxId") or "",
            "buyerName": r.get("buyerName") or "", "buyerTaxId": r.get("buyerTaxId") or "",
            "amount": r.get("amount"), "tax": r.get("tax"), "total": r.get("total"), "status": r.get("status") or "",
            "invType": r.get("invType") or "", "rowNo": r.get("rowNo")}


def _later_remaining(l):
    base = l.get("expect_amount") if l.get("expect_amount") is not None else l.get("pay_amount")
    if base is None:
        return None
    return round(max(0.0, float(base) - float(l.get("received_amount") or 0) - float(l.get("unregistered_amount") or 0)), 2)


def _taxlist_verify(e, rows, bid, user, st):
    """按一批税局清单给台账里的票标四色，并出对账报告。
    绿＝清单有且正常（部分红冲也算绿，另记一句）；红＝清单有但作废/全额红冲/负数；
    黄＝本该在清单里却没有（购方是这份清单所属的公司、开票日期在清单覆盖范围内、票种清单能覆盖）；
    灰＝清单本来覆盖不到（定额/出租车/通用机打/收据、购方不是本公司或没读到购方税号）。
    不动颜色的：清单日期范围外又没对上号码的；本公司另一家主体的票（这份清单管不到）；以前已绿的（绿不降黄）；
    开票日期＋价税合计和"号码被 Excel 截断"的清单行对得上的（可能就是它，判不了）。
    清单行显示已勾选/已确认用途 → 这张票记"已勾选"（deduct_status=checked，只升不降）。"""
    ours = _ours(st)
    scope = _list_scope(rows, st)
    by_key = {}
    for r in rows:
        if r.get("_key"):
            by_key.setdefault(r["_key"], []).append(r)
    dates = sorted(r["date"] for r in rows if r.get("date"))
    d1, d2 = (dates[0], dates[-1]) if dates else ("", "")
    # 号码被截断的清单行比不了号码，但开票日期＋价税合计还在：台账里这两样对得上的票不判黄
    lost_sig = {(r.get("date"), _f(r.get("total"))) for r in rows if r.get("numberLost") and r.get("date")}
    cand = {}
    base = [S.ITEM.c.kind.in_(("invoice", "receipt")), _active(), _not_void()]
    with e.connect() as cx:
        if d1:
            for x in cx.execute(select(S.ITEM).where(S.ITEM.c.issue_date >= d1, S.ITEM.c.issue_date <= d2, *base)):
                x = S._row(x)
                cand[x["id"]] = x
        for ch in _chunks(by_key.keys()):
            for x in cx.execute(select(S.ITEM).where(S.ITEM.c.dup_key.in_(ch), *base)):
                x = S._row(x)
                cand[x["id"]] = x
    ts = inv.now_s()
    counts = {"green": 0, "red": 0, "yellow": 0, "gray": 0}
    seen_keys = {x.get("dup_key") for x in cand.values() if x.get("dup_key")}
    updates, fills, yellow, red, logs = [], {}, [], [], []
    kept_green = lost_skip = other_comp = checked = partial = 0
    for it in sorted(cand.values(), key=lambda x: x["id"]):
        key = it.get("dup_key") or ""
        hits = by_key.get(key) if (it.get("kind") == "invoice" and key) else None
        in_range = bool(d1 and it.get("issue_date") and d1 <= it["issue_date"] <= d2)
        upd = {}
        if hits:
            bad = [h for h in hits if _row_bad(h)]
            ref = ([h for h in hits if not _row_bad(h)] or hits)[0]
            if bad:
                color = "red"
                note = "税局清单显示：%s" % _bad_label(bad[0])
            else:
                color = "green"
                part = [h for h in hits if _partial_red(h)]
                if part:
                    partial += 1
                    note = ("税局清单显示：%s（部分红冲，原票仍有效；没冲掉的部分照样能抵扣，"
                            "勾选时以税局「有效抵扣税额」为准）" % (part[0].get("status") or "部分红冲"))
                else:
                    note = "税局清单里有，状态%s" % (ref.get("status") or "正常")
                a, b = _f(it.get("total")), _f(ref.get("total"))
                if a is not None and b is not None and abs(a - b) > 0.01:
                    note += "；但价税合计对不上（台账 %s，清单 %s），请核对" % (inv._money_str(a), inv._money_str(b))
            fu, got = _fill_from_list(it, ref)
            if got:
                upd.update(fu)
                fills[it["id"]] = got
            if any(_row_ticked(h) for h in hits) and it.get("deduct_status") != "checked":
                upd["deduct_status"] = "checked"       # 税局显示已勾选抵扣：作废时要提醒做进项税额转出
                checked += 1
        elif it.get("kind") == "receipt":
            color, note = "gray", "收据不在税局发票清单里"
        elif (it.get("inv_type") or "") not in LIST_COVER:
            color = "gray"
            note = ("定额、出租车、通用机打等票种，税局清单覆盖不到" if it.get("inv_type")
                    else "票种没认出来，清单比对不了，请人工核")
        elif not _is_ours(it.get("buyer_tax_id"), it.get("buyer_name"), ours):
            color = "gray"
            note = (("购方税号 %s 不是本公司，清单里本来就不会有" % it["buyer_tax_id"]) if it.get("buyer_tax_id")
                    else "票上没读到购方税号，清单比对不了")
        elif not in_range:
            continue
        elif scope is None or not _is_ours(it.get("buyer_tax_id"), it.get("buyer_name"), scope):
            other_comp += 1          # 本公司另一家主体的票（或分不清清单是哪家的）：这份清单管不到，颜色不动
            continue
        elif (it.get("verify") or "") == "green":
            kept_green += 1          # 以前已对上过清单：这份清单里没有也不降成黄（可能只导了一部分）
            continue
        elif (it.get("issue_date"), _f(it.get("total"))) in lost_sig:
            lost_skip += 1
            continue
        else:
            color = "yellow"
            note = "税局清单（%s～%s）里没有：可能还没上传、抬头税号填错，或是假票，要逐张核" % (d1, d2)
        counts[color] += 1
        upd.update(verify=color, verify_at=ts, verify_note=note[:200])
        updates.append((it["id"], upd))
        if color == "yellow":
            yellow.append(dict(it, verify_note=note))
        elif color == "red":
            red.append((it, hits))
        if (it.get("verify") or "") != color or it["id"] in fills or "deduct_status" in upd:
            det = {"verify": color, "note": note[:200], "batch": bid}
            if it["id"] in fills:
                det["filled"] = fills[it["id"]]
            if "deduct_status" in upd:
                det["deductStatus"] = "checked"
            logs.append({"user": user, "action": "税局对账", "folder_id": it.get("folder_id"), "item_id": it["id"],
                         "later_id": it.get("later_id"), "detail": det})
    with e.begin() as cx:
        for ch in _chunks(updates):
            for iid, upd in ch:
                upd["updated_at"] = ts
                cx.execute(update(S.ITEM).where(S.ITEM.c.id == iid).values(**S._prep(S.ITEM, upd)))
    for iid in fills:
        try:
            inv.refresh_item(iid, st)     # 补了税额/购方：重算抵扣建议、抬头校验、销方档案
        except Exception as ex:
            print("[发票管家] 对账补字段后重算失败 #%s：%s" % (iid, ex))
    _log_many(logs)

    # 税局有、台账没有：购方是本公司、状态正常、号码没丢位，台账（任何状态的有效票）和期初里都没有
    miss, mk = [], set()
    for r in rows:
        k = r.get("_key")
        if not k or k in seen_keys or k in mk or _row_bad(r):
            continue
        if not _row_ours(r, ours):
            continue
        mk.add(k)
        miss.append(r)
    op = set()
    if mk:
        with e.connect() as cx:
            for ch in _chunks(mk):
                op.update(cx.execute(select(S.OPENING.c.dup_key).where(S.OPENING.c.dup_key.in_(ch))).scalars())
    miss = [r for r in miss if r["_key"] not in op]
    # 顺手和后补池里没收齐的单比一比：销方名称＝后补单收款方 → 可能就是那笔的票
    hints = []
    if miss:
        with e.connect() as cx:
            laters = [S._row(x) for x in cx.execute(select(S.LATER).where(S.LATER.c.status.in_(LATER_ACTIVE)))]
        by_payee = {}
        for l in laters:
            n = ip.normalize_name(l.get("payee_name"))
            if n:
                by_payee.setdefault(n, []).append(l)
        for r in miss:
            for l in by_payee.get(ip.normalize_name(r.get("sellerName")), [])[:3]:
                hints.append({"number": r.get("number") or "", "laterId": l["id"], "payee": l.get("payee_name") or "",
                              "businessId": l.get("business_id") or "", "applicant": l.get("applicant") or "",
                              "expectDate": l.get("expect_date") or "", "remaining": _later_remaining(l)})
    fmap = _folders_map(e, [x.get("folder_id") for x in yellow] + [x[0].get("folder_id") for x in red])
    not_in_list = []
    for it in yellow[:REPORT_CAP]:
        b = _item_brief(it, fmap)
        b["verifyNote"] = it.get("verify_note") or ""
        not_in_list.append(b)
    red_rows = []
    for it, hits in red[:REPORT_CAP]:
        b = _item_brief(it, fmap)
        b["status"] = next((_bad_label(h) for h in hits if _row_bad(h)), "作废/红冲")
        red_rows.append(b)
    warns = []
    if not d1:
        warns.append("清单里没读到开票日期：只能标绿/红，判断不了哪些票本该在清单里（黄）")
    elif scope is None:
        warns.append("清单里没有购方列、设置里又配了 %d 家公司：分不清这份清单是哪家公司的，只标绿/红，没判黄"
                     "（请从税局导出带购方税号的清单）" % len((st or {}).get("company") or []))
    if lost_skip:
        warns.append("有 %d 张票的开票日期和价税合计，和号码被 Excel 截断的清单行对得上，判不了是不是同一张，没标黄"
                     "（请把号码列设成文本重新导出）" % lost_skip)
    if kept_green:
        warns.append("有 %d 张票以前对上过税局清单、这份清单里没有：保持绿色没改（可能这份清单只导了一部分）" % kept_green)
    stats = {"rows": len(rows), "ours": len([r for r in rows if _row_ours(r, ours)]),
             "negative": len([r for r in rows if _row_bad(r)]), "numberLost": len([r for r in rows if r.get("numberLost")]),
             "matched": len([k for k in by_key if k in seen_keys]), "dateFrom": d1, "dateTo": d2,
             "partialRed": partial, "checked": checked, "keptGreen": kept_green, "otherCompany": other_comp,
             "lostSkip": lost_skip}
    return {"counts": counts, "notInLedger": [_list_row_brief(r) for r in miss[:REPORT_CAP]],
            "notInLedgerTotal": len(miss), "notInList": not_in_list, "red": red_rows, "laterHints": hints[:REPORT_CAP],
            "listStats": stats, "warnings": warns, "filled": len(fills)}


def _taxlist_run(name, data, user):
    """税局清单导入（线程池里跑）→ ({batch, report}, None) 或 (None, 中文原因)。"""
    p = ie.parse_taxlist(data, name)
    if not p.get("ok"):
        lines = ie.mapping_lines(p.get("mapping") or {})
        return None, (p.get("msg") or "清单读不出来") + ("" if not lines or "识别到的列" in (p.get("msg") or "")
                                                     else "（识别到的列：%s）" % "、".join(lines))
    e = E()
    st = inv.get_settings()
    rows = p["rows"]
    store_rows = []
    for r in rows:
        # 号码丢位（Excel 按数字存了 20 位号）的行不参与比对：它的号码已经不是真号码了
        r["_key"] = "" if r.get("numberLost") else (ip.dup_key(r.get("code"), r.get("number"), r.get("date"), r.get("total")) or "")
        store_rows.append({"number": r.get("number"), "code": r.get("code"), "issue_date": r.get("date") or "",
                           "seller_tax_id": r.get("sellerTaxId"), "seller_name": r.get("sellerName"),
                           "buyer_tax_id": r.get("buyerTaxId"), "buyer_name": r.get("buyerName"),
                           "amount": _f(r.get("amount")), "tax": _f(r.get("tax")), "total": _f(r.get("total")),
                           "status": r.get("status"), "inv_type": r.get("invType"), "check_state": r.get("checkState"),
                           "raw": r.get("raw"), "dup_key": r["_key"] or "-"})
    bid = S.batch_insert(e, "taxlist", name, len(rows), {}, user)
    S.taxlist_insert_many(e, bid, store_rows, user)
    rep = _taxlist_verify(e, rows, bid, user, st)
    mp = p.get("mapping") or {}
    if "buyerTaxId" not in mp and "buyerName" not in mp:
        rep["warnings"].append("清单里没有购方列：按「取得发票」清单处理，里面的票都当作开给本公司的")
    rep["warnings"] = list(p.get("warnings") or []) + rep["warnings"]
    rep["mapping"] = p.get("mapping") or {}
    rep["mappingLines"] = ie.mapping_lines(p.get("mapping") or {})
    rep["msg"] = p.get("msg") or ""
    S.batch_update(e, bid, summary_json=rep)
    return {"batch": _batch_view(S.batch_get(e, bid)), "report": rep}, None


@router.post("/api/inv/taxlist/import")
async def taxlist_import(request: Request):
    u, bad = need(request, ENTER_LEDGER, CAP_DEDUCT)
    if bad:
        return bad
    f, bad = await _one_upload(request)
    if bad:
        return bad
    name, data = f
    res, msg = await run_in_threadpool(_taxlist_run, name, data, u["name"])
    if msg:
        return err("税局清单没导进去：" + msg, 400)
    c = res["report"]["counts"]
    inv.audit(u, "税局对账", name, "清单 %d 行；绿 %d 红 %d 黄 %d 灰 %d；税局有台账没有 %d 张" % (
        res["batch"]["rows"], c["green"], c["red"], c["yellow"], c["gray"], res["report"]["notInLedgerTotal"]))
    return {"ok": True, "batch": res["batch"], "report": res["report"]}


@router.get("/api/inv/taxlist/report")
async def taxlist_report(request: Request):
    u, bad = need(request, ENTER_LEDGER)
    if bad:
        return bad
    e = E()
    raw = inv._s(request.query_params.get("batch"), 20)
    if raw:
        bid = inv._int_or_none(raw)
        b = S.batch_get(e, bid) if bid else None
        if not b or b.get("kind") != "taxlist":
            return err("没有这一批税局清单", 404)
    else:
        bs = S.batches_list(e, "taxlist", 1)
        b = bs[0] if bs else None
    if not b:
        return {"ok": True, "report": None, "batch": None}
    rep = b.get("summary_json") if isinstance(b.get("summary_json"), dict) else {}
    return {"ok": True, "report": rep or None, "batch": _batch_view(b)}


# ═══════════════════════════ 税局文件包补全 ═══════════════════════════

def _pack_file(e, cache, folder_id, name, data, ftype, docs, user):
    """同一份税局文件在同一个票夹里只存一次（多张票共用一份 PDF 时复用）。→ 文件行。
    磁盘快满时 store_file 抛 inv.StoreRefused（调用方接住、停下后面的文件）。"""
    sha = hashlib.sha256(data).hexdigest()
    key = (folder_id, sha)
    if key in cache:
        return cache[key]
    f = S.file_by_sha(e, folder_id, sha) if folder_id else None
    if not f:
        previews = []
        try:
            if ftype == "pdf":
                # 有票的页都要有预览（合并 PDF 第 6 张以后的票不能显示成第 1 页）
                previews = [p["jpeg"] for p in ip.render_pdf(data, max_pages=inv._pdf_preview_pages(docs))]
            else:
                previews = [ip.render_virtual(d) if d.get("isInvoice") else b"" for d in docs]
                if not all(previews):
                    previews = []   # 一张没画出来就都不出预览：页码和票得一一对应
        except Exception:
            previews = []
        ext, mime = inv._ext_mime(ftype, name)
        f = inv.store_file(folder_id, name, data, "taxpack", user, role="original", mime=mime, ext=ext, previews=previews)
    cache[key] = f
    return f


def _taxpack_fill(e, it, d, get_file, user, st, name):
    """税局电子原件补全一张台账票 → (补了什么, 对不上的字段)（补了什么为空列表＝已经齐了）。
    没审的票：补空字段、以及识别（OCR）来的/待核的字段；二维码、原件、人工核过、税局清单来的值不动——
      和税局文件对不上的，放进"待核"并标 fileMismatch，交审核人对。
    已审核的票：只补空字段；有值的对不上 → 这份文件一样都不采信（空字段也不补），标 postApprovalMismatch 并留痕
      （审过的金额不能被一份上传的文件悄悄改掉）。
    这张票还没有电子原件（只有二维码，或只有照片）→ 税局文件挂成主文件、照片降为纸质件；
    已审的票和税局文件对不上时主文件不换，文件只挂成附件给会计看。"""
    frozen = (it.get("review") or "") in ("approved", "void")
    fs = inv._copy(it.get("field_src_json") or {})
    pend = list(it.get("pending_json") or [])
    upd, got, diffs = {}, [], {}
    page0 = int(it.get("page") or 0)
    for fld, col in inv.FIELD_COLS:
        nv = d.get(fld)
        if inv._empty(nv):
            continue
        if fld in inv.MONEY_FIELDS:
            nv = _f(nv)
            if nv is None:
                continue
        cur = it.get(col)
        src = (fs.get(fld) or {}).get("src")
        if frozen and not inv._empty(cur):
            if not inv._same(cur, nv):
                diffs[fld] = [str(cur), str(nv)]
            continue
        trusted = src not in (None, "", "ocr")
        if not inv._empty(cur) and trusted and not inv._same(cur, nv):
            diffs[fld] = [str(cur), str(nv)]         # 二维码/原件/人工核过的值和税局文件不一样：不改，交审核对
            if fld not in pend:
                pend.append(fld)
            continue
        if not inv._empty(cur) and fld not in pend and trusted:
            continue
        if not inv._empty(cur) and inv._same(cur, nv):
            if fld in pend:           # 值一样：税局原件等于替人核过了
                pend.remove(fld)
                fs[fld] = {"src": "taxpack", "page": page0, "box": None}
                got.append(fld)
            continue
        upd[col] = nv
        fs[fld] = {"src": "taxpack", "page": page0, "box": None}
        if fld in pend:
            pend.remove(fld)
        got.append(fld)
    ex = S.file_get(e, it["file_id"]) if it.get("file_id") else None
    no_orig = ex is None or ex.get("status") == "removed" or (ex.get("mime") or "").startswith("image/")
    if frozen and diffs:
        # 已审的票和这份文件对不上：这份文件一样都不采信（空字段也不拿它补），主文件不换，只挂成附件给会计看
        upd, got = {}, []
        fs = inv._copy(it.get("field_src_json") or {})
        if no_orig:
            f, _page = get_file(it.get("folder_id"))
            if f and not f.get("item_id"):
                S.file_update(e, f["id"], item_id=it["id"], role="attachment")
    else:
        if not it.get("lines_json") and d.get("lines"):
            upd["lines_json"] = d["lines"]
            got.append("lines")
        if (not it.get("inv_type") or (it.get("inv_type") == "other" and not frozen)) and d.get("invType"):
            upd["inv_type"] = d["invType"]
        if not it.get("type_label") and d.get("typeLabel"):
            upd["type_label"] = d["typeLabel"]
        if not it.get("qr_type") and d.get("qrType"):
            upd["qr_type"] = d["qrType"]
    if no_orig and not (frozen and diffs):
        f, page = get_file(it.get("folder_id"))
        if f:
            upd["file_id"] = f["id"]
            upd["page"] = page
            has_prev = bool(f.get("preview_path"))
            nsrc = d.get("fieldSrc") or {}
            for fld, col in inv.FIELD_COLS:
                meta = fs.get(fld)
                if not isinstance(meta, dict):
                    continue
                dm = nsrc.get(fld) if isinstance(nsrc.get(fld), dict) else None
                final = upd.get(col, it.get(col))
                if has_prev and dm and dm.get("box") and inv._same(final, d.get(fld)):
                    meta["box"], meta["page"] = dm["box"], page     # 主图换成税局原件：框画在原件上
                elif meta.get("box"):
                    meta["box"] = None                              # 旧照片上的框对不上新主图了
            if ex and ex.get("id") != f["id"] and ex.get("status") != "removed":
                S.file_update(e, ex["id"], role="paper")
            if not f.get("item_id"):
                S.file_update(e, f["id"], item_id=it["id"])
            if it.get("proc_status") in ("pending", "failed"):
                upd.update(proc_status="done", proc_error="")     # 有了税局原件，不用再对着照片识别
            got.append("file")
    if upd and not got:
        got.append("type")                  # 只补了票种/票种名称
    if diffs:
        fl = dict(it.get("flags_json") or {})
        flag = "postApprovalMismatch" if frozen else "fileMismatch"
        fl[flag] = sorted(set(fl.get(flag) or []) | set(diffs))
        upd["flags_json"] = fl
        inv.log(user, "税局文件和票面对不上", it.get("folder_id"), it["id"], it.get("later_id"),
                {"name": name, "diff": diffs, "review": it.get("review") or "", "changed": False})
    if not got and not diffs:
        return [], {}
    upd["field_src_json"] = fs
    upd["pending_json"] = pend
    S.item_update(e, it["id"], **upd)
    inv.refresh_item(it["id"], st)
    if got:
        inv.log(user, "税局文件包补全", it.get("folder_id"), it["id"], it.get("later_id"), {"name": name, "filled": got})
    return got, diffs


def _taxpack_one(ctx, name, data):
    """税局文件包里的一份文件（压缩包已解开）→ 补全台账里同号的票，结果记进 ctx。磁盘快满抛 inv.StoreRefused。"""
    e, st, user, acc = ctx["e"], ctx["st"], ctx["user"], ctx["acc"]
    ctx["n"] += 1
    name = inv._clean_name(name)
    try:
        t = ip.sniff_type(name, data)
    except Exception:
        t = "other"
    if t not in ("pdf", "ofd", "xml"):
        acc["skipped"].append({"name": name, "msg": "不是电子发票文件（PDF/OFD/XML），跳过"})
        return
    try:
        docs = ip.extract_pdf(data) if t == "pdf" else (ip.extract_ofd(data) if t == "ofd" else ip.extract_xml(data))
    except Exception as ex:
        acc["errors"].append({"name": name, "msg": "读不出来：%s" % ex})
        return
    invs = [(i, d) for i, d in enumerate(docs) if d.get("kind") == "invoice"]
    if not invs:
        acc["skipped"].append({"name": name, "msg": "文件里没读到发票"})
        return
    for i, d in invs:
        key = ip.dup_key(d.get("code"), d.get("number"), d.get("date"), d.get("total"))
        targets = S.items_by_dup(e, key) if key else []
        if not targets:
            acc["unmatched"].append({"name": name, "number": d.get("number") or ""})
            continue

        def get_file(folder_id, _n=name, _b=data, _t=t, _docs=docs, _i=i, _d=d):
            f = _pack_file(e, ctx["cache"], folder_id, _n, _b, _t, _docs, user)
            if _t == "pdf":
                page = int(_d.get("page") or 0)
            else:
                page = _i if (f.get("preview_path") and int(f.get("pages") or 1) > _i) else 0
            return f, page
        for it in targets:
            try:
                got, diffs = _taxpack_fill(e, it, d, get_file, user, st, name)
            except inv.StoreRefused:
                raise
            except Exception as ex:
                acc["errors"].append({"name": name, "msg": "补全票#%d 出错：%s" % (it["id"], ex)})
                continue
            if diffs:
                acc["mismatch"].append({"id": it["id"], "number": it.get("number") or "", "name": name,
                                        "review": it.get("review") or "", "fields": sorted(diffs)})
            if got:
                acc["filled"].append(it["id"])
            elif not diffs:
                acc["already"].append(it["id"])


def _taxpack_run(files, user):
    """税局文件包补全（线程池里跑）。压缩包一个一个解、解一个处理一个再解下一个（不把所有包同时摊在内存里）；
    整次导入的压缩包共用一份解开总量额度（inv.MAX_TOTAL），防几个很小的压缩炸弹撑满内存。"""
    acc = {"filled": [], "already": [], "unmatched": [], "errors": [], "skipped": [], "mismatch": []}
    ctx = {"e": E(), "st": inv.get_settings(), "user": user, "acc": acc, "cache": {}, "n": 0}
    budget = inv.MAX_TOTAL
    stop = ""
    for name, data in files:
        if stop:
            acc["errors"].append({"name": name, "msg": "没处理：" + stop})
            continue
        try:
            t = ip.sniff_type(name, data)
        except Exception:
            t = "other"
        try:
            if t != "zip":
                _taxpack_one(ctx, name, data)
                continue
            if budget <= 0:
                acc["errors"].append({"name": name, "msg": "这一批压缩包解开后太大（超过 %dMB），这个包没处理：请分几次导入"
                                      % (inv.MAX_TOTAL // inv.MB)})
                continue
            try:
                zf = ip.unpack_zip(data, max_total=budget)
            except Exception as ex:
                acc["errors"].append({"name": name, "msg": "压缩包打不开：%s" % ex})
                continue
            budget -= sum(len(b) for _n, b in zf)
            for w in getattr(zf, "warnings", None) or []:
                acc["errors"].append({"name": name, "msg": w})
            entries = list(zf)
            zf = None
            entries.reverse()
            while entries:                       # 处理完一份就放掉一份
                n2, b2 = entries.pop()
                _taxpack_one(ctx, n2, b2)
        except inv.StoreRefused as ex:
            stop = str(ex)
            acc["errors"].append({"name": name, "msg": stop})
    e = ctx["e"]
    filled = sorted(set(acc["filled"]))
    already = sorted(set(acc["already"]) - set(filled))
    mism, unmatched, errors, skipped = acc["mismatch"], acc["unmatched"], acc["errors"], acc["skipped"]
    summary = {"filled": len(filled), "already": len(already), "unmatched": len(unmatched), "errors": len(errors),
               "skipped": len(skipped), "mismatch": len(mism)}
    bid = S.batch_insert(e, "taxpack", "、".join(n for n, _ in files)[:255], ctx["n"], summary, user)
    msg = "补全 %d 张" % len(filled) + ("，%d 张本来就齐了" % len(already) if already else "") +           ("，%d 张和税局文件对不上（票面没改，已标出来请核对）" % len({m["id"] for m in mism}) if mism else "") +           ("，%d 张台账里没找到" % len(unmatched) if unmatched else "") + ("，%d 个文件出错" % len(errors) if errors else "")
    return {"ok": True, "msg": msg, "filled": filled, "already": already, "unmatched": unmatched, "errors": errors,
            "skipped": skipped, "mismatch": mism, "batch": _batch_view(S.batch_get(e, bid))}


@router.post("/api/inv/taxpack/import")
async def taxpack_import(request: Request):
    u, bad = need(request, ENTER_LEDGER, CAP_DEDUCT)
    if bad:
        return bad
    files, fields, errors, bad = await inv.read_upload_files(request)
    if bad:
        return bad
    if not files:
        return err(errors[0]["msg"] if errors else "没收到文件：请把税局下载的发票文件（或压缩包）拖进来", 400)
    res = await run_in_threadpool(_taxpack_run, files, u["name"])
    res["errors"] = list(errors) + res["errors"]
    inv.audit(u, "税局文件包补全", "、".join(n for n, _ in files)[:160], res["msg"])
    return res


# ═══════════════════════════ 抵扣勾选 ═══════════════════════════

def _deduct_index(e):
    """台账里全部有效发票按查重键分组（一次查出来，标注几千行时不逐行查库）。"""
    c = S.ITEM.c
    cols = [c.id, c.dup_key, c.review, c.deductible, c.deduct_suggest, c.deduct_reason, c.verify, c.verify_note,
            c.deduct_status, c.folder_id, c.later_id]
    idx = {}
    with e.connect() as cx:
        for r in cx.execute(select(*cols).where(c.kind == "invoice", _active(), _not_void(),
                                                func.coalesce(c.dup_key, "") != "").order_by(c.id)).mappings():
            idx.setdefault(r["dup_key"], []).append(dict(r))
    return idx


def _deduct_run(name, data, user):
    e = E()
    idx = _deduct_index(e)
    stats = {"notInLedger": 0, "notRegistered": 0, "notApproved": 0, "numberLost": 0}
    yes_ids = []

    def decide(row):
        if row.get("numberLost"):
            stats["numberLost"] += 1
            return None, "号码被 Excel 截断了，比对不了（请把号码列设成文本重新导出）"
        key = ip.dup_key(row.get("code"), row.get("number"), row.get("date"), row.get("total"))
        cands = (idx.get(key) or []) if key else []
        if not cands:
            stats["notInLedger"] += 1
            stats["notRegistered"] += 1
            op = S.opening_match(e, key) if key else None
            return None, "台账里没有" + ("（期初/票总管里有这张，请人工判断）" if op else "（还没登记）")
        appr = [x for x in cands if x.get("review") == "approved"]
        if not appr:
            stats["notInLedger"] += 1
            stats["notApproved"] += 1
            return None, "还没审核通过，审核后再勾"
        x = appr[0]
        if any(a.get("verify") == "red" for a in appr):
            return "否", "税局显示已作废/全额红冲"
        ded = x.get("deductible")
        if ded == 1 or (ded is None and x.get("deduct_suggest") == "yes"):
            yes_ids.extend(a["id"] for a in appr)
            why = "已审核，会计判定可抵扣" if ded == 1 else "已审核，系统建议可抵扣（会计没改）"
            if x.get("verify") == "green":
                why += "，已验真"
            if any("部分红冲" in (a.get("verify_note") or "") for a in appr):
                dt = _f(row.get("deductTax"))
                why += "；部分红冲：只按税局有效抵扣税额%s勾选" % ((" ¥" + inv._money_str(dt)) if dt is not None else "")
            return "是", why
        if ded == 0:
            return "否", "会计判定不可抵扣" + (("：" + x["deduct_reason"]) if x.get("deduct_reason") else "")
        return "否", x.get("deduct_reason") or "系统建议不可抵扣"

    res = ie.mark_deduct_file(data, name, decide)
    if not res.get("ok"):
        lines = ie.mapping_lines(res.get("mapping") or {})
        return None, (res.get("msg") or "清单读不出来") + ("" if not lines or "识别到的列" in (res.get("msg") or "")
                                                       else "（识别到的列：%s）" % "、".join(lines))
    _tmp_clean()
    token = secrets.token_urlsafe(16)
    summary = dict(res.get("summary") or {}, **stats)
    meta = {"kind": "deduct", "user": user, "name": name, "created": time.time(), "yes": sorted(set(yes_ids)),
            "summary": summary}
    _tmp_write(_tmp_path(token, ".xlsx"), res["bytes"])
    _tmp_write(_tmp_path(token, ".json"), json.dumps(meta, ensure_ascii=False).encode("utf-8"))
    return {"ok": True, "token": token, "summary": summary, "preview": res.get("preview") or [],
            "warnings": res.get("warnings") or [], "mapping": res.get("mapping") or {},
            "mappingLines": ie.mapping_lines(res.get("mapping") or {}), "tickColumn": res.get("tickColumn") or "",
            "msg": res.get("msg") or "", "expiresAt": (datetime.now() + timedelta(seconds=TMP_TTL)).strftime("%Y-%m-%d %H:%M:%S")}, None


@router.post("/api/inv/deduct/prepare")
async def deduct_prepare(request: Request):
    u, bad = need(request, ENTER_LEDGER, CAP_DEDUCT)
    if bad:
        return bad
    f, bad = await _one_upload(request)
    if bad:
        return bad
    name, data = f
    res, msg = await run_in_threadpool(_deduct_run, name, data, u["name"])
    if msg:
        return err("清单没标注成功：" + msg, 400)
    s = res["summary"]
    inv.audit(u, "抵扣勾选标注", name, "共 %s 张：是 %s、否 %s、没表态 %s（台账里没有 %s）" % (
        s.get("rows"), s.get("yes"), s.get("no"), s.get("unknown"), s.get("notInLedger")))
    return res


def _mark_items(ids, user, name):
    """下载标注件＝准备去税局勾选：标「是」的票记为"已标注勾选"（已勾选的不回退）。→ 实际改了几张。"""
    e = E()
    ids = sorted(set(int(i) for i in ids or []))
    if not ids:
        return 0
    c = S.ITEM.c
    ts = inv.now_s()
    changed = []
    with e.begin() as cx:
        for ch in _chunks(ids):
            rows = cx.execute(select(c.id, c.folder_id, c.later_id).where(
                c.id.in_(ch), func.coalesce(c.deduct_status, "") == "", _not_void(), _active())).mappings().all()
            if rows:
                cx.execute(update(S.ITEM).where(c.id.in_([r["id"] for r in rows])).values(deduct_status="marked", updated_at=ts))
                changed.extend(dict(r) for r in rows)
    _log_many([{"user": user, "action": "标注抵扣勾选", "folder_id": r["folder_id"], "item_id": r["id"],
                "later_id": r["later_id"], "detail": {"file": name}} for r in changed])
    return len(changed)


@router.get("/api/inv/deduct/download")
async def deduct_download(request: Request):
    u, bad = need(request, ENTER_LEDGER, CAP_DEDUCT)
    if bad:
        return bad
    token = inv._s(request.query_params.get("token"), 80)
    meta, msg = _tmp_meta(token, "deduct")
    if msg:
        return err(msg, 400)
    p = _tmp_path(token, ".xlsx")
    try:
        with open(p, "rb") as fh:
            data = fh.read()
    except OSError:
        return err("文件已过期（只保留 2 小时），请重新拖一次", 400)
    n = await run_in_threadpool(_mark_items, meta.get("yes") or [], u["name"], meta.get("name") or "")
    inv.audit(u, "下载抵扣勾选标注件", meta.get("name") or "", "标「是」%d 张，本次记为已标注 %d 张" % (len(meta.get("yes") or []), n))
    return _xlsx(data, "抵扣勾选_已标注_%s.xlsx" % _stamp(), "deduct_marked_%s.xlsx" % _stamp())


# ═══════════════════════════ 新销方核查 ═══════════════════════════

def _seller_view(r):
    return {"taxId": r.get("tax_id") or "", "name": r.get("name") or "", "firstSeen": r.get("first_seen") or "",
            "firstItemId": r.get("first_item_id"), "items": int(r.get("items") or 0), "total": _f(r.get("total")) or 0.0,
            "checkDate": r.get("check_date") or "", "checkChannel": r.get("check_channel") or "",
            "checkResult": r.get("check_result") or "未查", "checkNote": r.get("check_note") or "",
            "checkedBy": r.get("checked_by") or "", "updatedAt": r.get("updated_at") or ""}


def _seller_args(request):
    month = inv._s(request.query_params.get("month"), 7)
    if month and not _MONTH_RE.match(month):
        return None, None, "月份要写成 2026-09 这样"
    status = inv._s(request.query_params.get("status"), 20)
    if status == "all":
        status = ""
    return month or None, status or None, None


@router.get("/api/inv/sellers")
async def sellers_list(request: Request):
    u, bad = need(request, ENTER_LEDGER)
    if bad:
        return bad
    month, status, msg = _seller_args(request)
    if msg:
        return err(msg, 400)
    rows = await run_in_threadpool(S.sellers_query, E(), month, status)
    return {"ok": True, "rows": [_seller_view(r) for r in rows]}


@router.get("/api/inv/sellers/export")
async def sellers_export(request: Request):
    u, bad = need(request, ENTER_LEDGER)
    if bad:
        return bad
    month, status, msg = _seller_args(request)
    if msg:
        return err(msg, 400)
    rows = await run_in_threadpool(S.sellers_query, E(), month, status)
    data = await run_in_threadpool(ie.export_sellers, [_seller_view(r) for r in rows])
    inv.audit(u, "导出新销方核查", month or "全部", "%d 家" % len(rows))
    tag = (month or _stamp()).replace("-", "")
    return _xlsx(data, "新销方核查_%s.xlsx" % tag, "new_sellers_%s.xlsx" % tag)


@router.post("/api/inv/sellers/check")
async def sellers_check(request: Request):
    u, bad = need(request, ENTER_LEDGER, CAP_DEDUCT)
    if bad:
        return bad
    body = await inv.body_json(request)
    tax_id = inv._s(body.get("taxId"), 32).upper()
    if not tax_id:
        return err("缺少销方税号", 400)
    try:
        d = inv._norm_date(body.get("date")) or date.today().isoformat()
    except ValueError:
        return err("查询日期格式不对（要 2026-09-01 这样）", 400)
    result = inv._s(body.get("result"), 20)
    if result not in SELLER_RESULTS:
        return err("查询结果只能是：%s" % "、".join(SELLER_RESULTS), 400)
    channel, note = inv._s(body.get("channel"), 60), inv._s(body.get("note"), 500)
    if result != "未查" and not channel:
        return err("请写查询渠道（如：信用中国、省税务局重大税收违法失信公布栏）", 400)
    row = S.seller_check(E(), tax_id, d, channel, result, note, u["name"])
    det = {"taxId": tax_id, "name": (row or {}).get("name") or "", "date": d, "channel": channel, "result": result, "note": note}
    inv.log(u, "新销方核查", detail=det)
    inv.audit(u, "新销方核查", "%s %s" % (tax_id, det["name"]), det)
    msg = "已记下核查结果"
    if result == "命中":
        msg += "：这家在失信名单上——只提示，不自动拒付，请和业务、财务负责人商量怎么处理"
    return {"ok": True, "msg": msg, "seller": _seller_view(row or {})}


# ═══════════════════════════ 期初导入（票总管历史清单） ═══════════════════════════

def _live_keys(e, keys):
    """这些查重键里，台账已有有效票（未作废、未移除）的那些。"""
    out = set()
    with e.connect() as cx:
        for ch in _chunks(keys):
            out.update(cx.execute(select(S.ITEM.c.dup_key).where(S.ITEM.c.dup_key.in_(ch), _active(), _not_void())).scalars())
    return out


def _opening_keys(e, keys):
    out = set()
    with e.connect() as cx:
        for ch in _chunks(keys):
            out.update(cx.execute(select(S.OPENING.c.dup_key).where(S.OPENING.c.dup_key.in_(ch))).scalars())
    return out


def _opening_preview_run(name, data, user):
    p = ie.parse_opening(data, name)
    if not p.get("ok"):
        lines = ie.mapping_lines(p.get("mapping") or {})
        return None, (p.get("msg") or "清单读不出来") + ("" if not lines or "识别到的列" in (p.get("msg") or "")
                                                     else "（识别到的列：%s）" % "、".join(lines))
    rows, lost, nokey = [], 0, 0
    for r in p["rows"]:
        if r.get("numberLost"):
            lost += 1
            continue
        k = ip.dup_key(r.get("code"), r.get("number"), r.get("date"), r.get("total"))
        if not k:
            nokey += 1
            continue
        rows.append({"dup_key": k, "number": r.get("number") or "", "code": r.get("code") or "",
                     "issue_date": r.get("date") or "", "total": _f(r.get("total")), "seller_name": r.get("sellerName") or "",
                     "buyer_name": r.get("buyerName") or "", "ref": r.get("ref") or ""})
    keys = sorted({r["dup_key"] for r in rows})
    e = E()
    already = _live_keys(e, keys) | _opening_keys(e, keys)
    _tmp_clean()
    token = secrets.token_urlsafe(16)
    meta = {"kind": "opening", "user": user, "name": name, "created": time.time(), "rows": rows, "lost": lost, "nokey": nokey}
    _tmp_write(_tmp_path(token, ".json"), json.dumps(meta, ensure_ascii=False).encode("utf-8"))
    warns = list(p.get("warnings") or [])
    if lost:
        warns.append("有 %d 行发票号码被 Excel 截断（按数字存的 20 位号），这些行不会导入；请把号码列设成文本重新导出" % lost)
    if nokey:
        warns.append("有 %d 行只有号码、没有发票代码/日期/金额，凑不成查重依据，不会导入" % nokey)
    sample = [{"number": r.get("number") or "", "code": r.get("code") or "", "date": r.get("date") or "",
               "total": r.get("total"), "sellerName": r.get("sellerName") or "", "buyerName": r.get("buyerName") or "",
               "ref": r.get("ref") or "", "numberLost": bool(r.get("numberLost"))} for r in p["rows"][:20]]
    return {"ok": True, "token": token, "mapping": p.get("mapping") or {}, "mappingLines": ie.mapping_lines(p.get("mapping") or {}),
            "headers": p.get("headers") or [], "sample": sample, "rows": len(p["rows"]),
            "dupWithin": len(rows) - len(keys), "alreadyIn": len(already), "numberLost": lost, "noKey": nokey,
            "willInsert": len(keys) - len(already), "warnings": warns, "msg": p.get("msg") or ""}, None


@router.post("/api/inv/opening/preview")
async def opening_preview(request: Request):
    u, bad = need(request, ENTER_LEDGER, CAP_OPENING)
    if bad:
        return bad
    f, bad = await _one_upload(request)
    if bad:
        return bad
    name, data = f
    res, msg = await run_in_threadpool(_opening_preview_run, name, data, u["name"])
    if msg:
        return err("期初清单读不出来：" + msg, 400)
    return res


def _opening_commit_run(token, source, user):
    meta, msg = _tmp_meta(token, "opening")
    if msg:
        return None, msg
    if meta.get("user") != user:
        return None, "这份预览是别人做的，请自己重新拖一次文件"
    # 先把凭证改名占住：同一份预览只能导一次（两个人同时点"确认导入"只算一次）
    p = _tmp_path(token, ".json")
    claimed = p + ".used"
    try:
        os.replace(p, claimed)
    except OSError:
        return None, "这份预览已经导入过了"
    try:
        e = E()
        rows = meta.get("rows") or []
        keys = sorted({r["dup_key"] for r in rows})
        live = _live_keys(e, keys)       # 台账里已有的票不进期初：否则它自己会被判成"和期初重复"
        fresh = [r for r in rows if r["dup_key"] not in live]
        bid = S.batch_insert(e, "opening", meta.get("name") or "", 0, {}, user)
        n = S.opening_insert_many(e, fresh, bid, source, user)
        summary = {"file": meta.get("name") or "", "source": source, "inserted": n, "skippedLive": len(live),
                   "skippedExisting": len({r["dup_key"] for r in fresh}) - n, "numberLost": meta.get("lost") or 0,
                   "noKey": meta.get("nokey") or 0}
        S.batch_update(e, bid, rows=n, summary_json=summary)
        return dict(summary, batch=_batch_view(S.batch_get(e, bid))), None
    finally:
        try:
            os.remove(claimed)
        except OSError:
            pass


@router.post("/api/inv/opening/commit")
async def opening_commit(request: Request):
    u, bad = need(request, ENTER_LEDGER, CAP_OPENING)
    if bad:
        return bad
    body = await inv.body_json(request)
    token = inv._s(body.get("token"), 80)
    source = inv._s(body.get("source"), 40) or "票总管"
    res, msg = await run_in_threadpool(_opening_commit_run, token, source, u["name"])
    if msg:
        return err(msg, 400)
    inv.log(u, "期初导入", detail=dict((k, v) for k, v in res.items() if k != "batch"))
    inv.audit(u, "期初导入", res.get("file") or source, "来源 %s：导入 %d 张；台账已有 %d、底子已有 %d 未导" % (
        source, res["inserted"], res["skippedLive"], res["skippedExisting"]))
    res["msg"] = "已导入 %d 张期初票，之后登记的票会和它们查重" % res["inserted"]
    return dict(res, ok=True)


@router.get("/api/inv/opening/stats")
async def opening_stats(request: Request):
    u, bad = need(request, ENTER_LEDGER)
    if bad:
        return bad
    s = S.opening_stats(E())
    bs = [{"id": b.get("batch_id"), "batchId": b.get("batch_id"), "name": b.get("name") or "", "source": b.get("source") or "",
           "rows": int(b.get("rows") or 0), "importedBy": b.get("imported_by") or "", "importedAt": b.get("imported_at") or ""}
          for b in s.get("batches") or []]
    return {"ok": True, "total": s.get("total") or 0, "batches": bs}


# ═══════════════════════════ 发票后补池 ═══════════════════════════

def _is_doc(it):
    # 后补单"资料"（合同、对账单等）：非发票、来源 later；和收到的票分开列
    return it.get("kind") == "other" and it.get("origin") == "later"


def _counted(items):
    """算进"已到票"的：发票/收据、没移除没作废、不是重复票。"""
    return [i for i in items or [] if i.get("kind") in ("invoice", "receipt") and i.get("status") != "removed"
            and i.get("review") != "void" and not ((i.get("flags_json") or {}).get("dup"))]


def _status_of(l, received, unreg):
    base = l.get("expect_amount") if l.get("expect_amount") is not None else l.get("pay_amount")
    got = float(received or 0) + float(unreg or 0)
    if base is not None and float(base) > 0 and got >= float(base) - 0.01:
        return "done"
    return "partial" if got > 0.004 else "open"


def _later_title(l):
    if l.get("business_id"):
        return "审批编号 %s" % l["business_id"]
    return "后补单#%d" % l["id"]


def _later_link(l):
    return inv.portal_link("/#/invlater?id=%d" % l["id"])


def _notify_done(l, user):
    """收齐 → 钉钉告诉申请人"已签收"（不发给没绑钉钉的人；测试环境 dry-run）。"""
    text = MSG_HEAD + "你的付款单（%s，收款方「%s」）的发票财务已签收：共到票 ¥%s。" % (
        _later_title(l), l.get("payee_name") or "", inv._money_str(float(l.get("received_amount") or 0) + float(l.get("unregistered_amount") or 0)))
    res = inv.notify_dt([l.get("applicant_uid")], text)
    inv.log(user, "后补收齐通知", l.get("folder_id"), None, l["id"],
            {"to": l.get("applicant") or "", "sent": bool(res.get("sent")), "msg": res.get("msg") or ""})
    return res


def _later_recalc(lid, user="系统"):
    """按挂在后补单上的票重算"已到金额"与状态（open/partial/done；closed 不动状态）→ 最新行。
    新登记的票先冲掉"已收到、号码未登记"（标黄）那部分：那多半就是补扫的同一张票。
    由未收齐变成收齐时发"已签收"通知（锁内判定，只发一次）。票在锁内现取：用锁外取的旧快照会把刚收的票算丢。
    这是后补单重算的唯一入口：收票/上传/标记/作废在本模块调，移除/改票/拆分/识别完成/重复标记变化由 routers/invoice
    的 _later_sync 调（位置参数 (lid, user)，别改签名）。可能发钉钉：只能在线程池/后台线程里调，别在 async 路由里直接调。"""
    e = E()
    notify = None
    with _LATER_LOCK:
        l = S.later_get(e, lid)
        if not l:
            return None
        its = S.items_of_later(e, lid)
        rec = round(sum(inv._share(i) for i in _counted(its)), 2)
        old = float(l.get("received_amount") or 0)
        un = float(l.get("unregistered_amount") or 0)
        upd = {}
        if abs(rec - old) >= 0.005:
            upd["received_amount"] = rec
            if rec > old and un > 0:
                un = round(max(0.0, un - (rec - old)), 2)
                upd["unregistered_amount"] = un
        st = l.get("status") or "open"
        if st != "closed":
            ns = _status_of(l, rec, un)
            if ns != st:
                upd["status"] = ns
                if ns == "done":
                    notify = True
        if upd:
            S.later_update(e, lid, **upd)
            l = S.later_get(e, lid)
            if "status" in upd:
                inv.log(user, "后补单状态", l.get("folder_id"), None, lid,
                        {"from": st, "to": upd["status"], "received": rec, "unregistered": un})
    if notify:
        _notify_done(l, user)
    return l


def _later_heal():
    """后补单"已到金额/状态"兜底自愈：一次查出全部未关闭的后补单和挂在上面的票，按 _later_recalc 同一口径算一遍；
    和库里对不上的（某条改票路径漏了重算、老数据）逐张走 _later_recalc 纠正（变成收齐的照发"已签收"）。
    按状态筛后补单、导出欠票清单、每日催票之前先调：库里状态旧了会把欠票的单漏掉（该催的不催）。
    两条查询算完，只有对不上的才逐张重算；可能发钉钉，只在线程里调。→ 纠正了的后补单 id 列表。"""
    e = E()
    lc, ic = S.LATER.c, S.ITEM.c
    with e.connect() as cx:
        ls = [S._row(r) for r in cx.execute(select(
            lc.id, lc.status, lc.received_amount, lc.unregistered_amount, lc.expect_amount, lc.pay_amount)
            .where(lc.status.in_(LATER_ACTIVE + ("done",))))]
        by = {}
        for ch in _chunks([l["id"] for l in ls]):
            for r in cx.execute(select(ic.id, ic.later_id, ic.kind, ic.status, ic.review, ic.flags_json, ic.split,
                                       ic.alloc, ic.total, ic.amount).where(ic.later_id.in_(ch), _active())):
                r = S._row(r)
                by.setdefault(r["later_id"], []).append(r)
    fixed = []
    for l in ls:
        rec = round(sum(inv._share(i) for i in _counted(by.get(l["id"]))), 2)
        old = float(l.get("received_amount") or 0)
        un = float(l.get("unregistered_amount") or 0)
        if abs(rec - old) < 0.005 and _status_of(l, rec, un) == (l.get("status") or "open"):
            continue
        try:
            _later_recalc(l["id"], "系统")
            fixed.append(l["id"])
        except Exception as ex:
            print("[发票管家] 后补单#%s 自愈重算失败：%s" % (l["id"], ex))
    return fixed


def _items_by_later(e, ids):
    out = {}
    ids = [i for i in ids or [] if i]
    if ids:
        with e.connect() as cx:
            for ch in _chunks(ids):
                for r in cx.execute(select(S.ITEM).where(S.ITEM.c.later_id.in_(ch), _active()).order_by(S.ITEM.c.id)):
                    r = S._row(r)
                    out.setdefault(r["later_id"], []).append(r)
    return out


def _later_views(rows, recalc=True):
    """后补单行 → 前端 Later 列表（批量取票算 sellerMismatch；顺手重算已到金额，识别完成后的照片票也能及时算进来）。"""
    e = E()
    by = _items_by_later(e, [r["id"] for r in rows])
    out = []
    for r in rows:
        its = by.get(r["id"], [])
        if recalc and r.get("status") != "closed":
            r = _later_recalc(r["id"], "系统") or r
        out.append(inv.later_view(r, [i for i in its if i.get("kind") in ("invoice", "receipt")]))
    return out


def _later_all(scope_user, status, q):
    e = E()
    out, page = [], 1
    while True:
        total, rows = S.later_list(e, scope_user=scope_user, status=status, q=q, page=page, size=500)
        out.extend(rows)
        if len(rows) < 500 or len(out) >= total or len(out) >= EXPORT_CAP:
            return out
        page += 1


def _cache_norm(n):
    now = time.time()
    with _RESOLVED_LOCK:
        for k in [k for k, v in _RESOLVED.items() if v[0] < now]:
            _RESOLVED.pop(k, None)
        _RESOLVED[n["instId"]] = (now + 1800, n)


def _cached_norm(iid):
    with _RESOLVED_LOCK:
        v = _RESOLVED.get(iid)
    return v[1] if v and v[0] >= time.time() else None


def _norm_of_folder(f):
    return {"instId": f.get("inst_id"), "businessId": f.get("business_id") or "", "template": f.get("template") or "",
            "title": f.get("title") or "", "applicant": f.get("applicant") or "", "applicantUid": f.get("applicant_uid") or "",
            "dept": f.get("dept") or "", "company": f.get("company") or "", "amount": f.get("amount"),
            "payeeName": f.get("payee_name") or "", "payeeBank": f.get("payee_bank") or "",
            "payeeAccount": f.get("payee_account") or "", "reason": f.get("reason") or "", "erpNo": f.get("erp_no") or ""}


def _later_tpl_msg(tpl):
    return ("这张单是「%s」，不走发票后补：只有付款申请这类先付款、后补票的单子才登记"
            "（如确需登记，请管理员在设置 › 审批模板里把它设为「允许后补」）" % (tpl or "未知模板"))


def _folder_for_later(n, user):
    """付款单 → 票夹 id（没有就建，来源记 later；新建且审批单有附件就排队拉附件，和收票工作台扫单一致）。"""
    e = E()
    existed = S.folder_by_inst(e, n["instId"])
    fid = S.folder_upsert_from_approval(e, n, user, source="later")
    if existed is None:
        if n.get("hasAttachments"):
            S.folder_update(e, fid, attach_status="pending", attach_msg="正在拉取审批单附件…")
            inv.wake()
        else:
            S.folder_update(e, fid, attach_status="done", attach_msg="审批单上没有附件")
        inv.log(user, "后补登记建票夹", fid, detail={"businessId": n.get("businessId"), "template": n.get("template")})
    return fid


def _prefill(n, e):
    f = S.folder_by_inst(e, n["instId"])
    has_inv = False
    if f:
        has_inv = any(i.get("kind") == "invoice" and i.get("review") != "void" for i in S.folder_items(e, f["id"]))
    ex = S.later_open_by_inst(e, n["instId"])
    return {"instId": n["instId"], "businessId": n.get("businessId") or "", "template": n.get("template") or "",
            "title": n.get("title") or "", "applicant": n.get("applicant") or "", "applicantUid": n.get("applicantUid") or "",
            "dept": n.get("dept") or "", "company": n.get("company") or "",
            "payee": {"name": n.get("payeeName") or "", "bank": n.get("payeeBank") or "", "account": n.get("payeeAccount") or ""},
            "amount": n.get("amount"), "reason": n.get("reason") or "", "erpNo": n.get("erpNo") or "",
            "folderId": f["id"] if f else None, "hasInvoice": has_inv, "existingLaterId": ex["id"] if ex else None}


def _receiver_of(account, st):
    p = inv.person_of(account, st) if account else None
    if not p or not p.get("receiver"):
        return None
    return p


def _assign_text(l):
    rem = l.get("expect_amount") if l.get("expect_amount") is not None else l.get("pay_amount")
    return MSG_HEAD + "有一张发票后补单指派给你接收：收款方「%s」，付款金额 ¥%s，预计 %s 到票 ¥%s（%s，申请人 %s）。" \
        "发票到了在「发票后补池」点「收到」→ %s" % (
            l.get("payee_name") or "", inv._money_str(l.get("pay_amount")), l.get("expect_date") or "",
            inv._money_str(rem), _later_title(l), l.get("applicant") or "", _later_link(l))


def _lv(l):
    """单张后补单视图（带上它的票，才算得出"销方与收款方不一致"）。"""
    if not l:
        return None
    its = S.items_of_later(E(), l["id"])
    return inv.later_view(l, [i for i in its if i.get("kind") in ("invoice", "receipt")])


def _later_docs(e, items):
    """后补单资料 → 文件视图列表（附 itemId、url、上传人和时间）。"""
    docs = []
    for i in items:
        if _is_doc(i) and i.get("file_id"):
            f = S.file_get(e, i["file_id"])
            if f and f.get("status") != "removed":
                v = inv.file_view(f)
                v.update(itemId=i["id"], url=v.get("orig"), createdAt=f.get("created_at") or "",
                         createdBy=f.get("created_by") or "")
                docs.append(v)
    return docs


def _later_get_or_404(lid):
    l = S.later_get(E(), lid)
    if not l:
        return None, err("后补单不存在", 404)
    return l, None


@router.get("/api/inv/later")
async def later_list(request: Request):
    u, bad = need(request, ENTER_LATER)
    if bad:
        return bad
    qp = request.query_params
    scope = qp.get("scope") if qp.get("scope") in ("mine", "all") else "mine"
    status = inv._s(qp.get("status"), 12)
    if status in ("", "all"):
        status = None
    elif status not in LATER_STATUSES:
        return err("状态只能是：待收 open / 部分到票 partial / 已收齐 done / 已关闭 closed / 超期 overdue / 全部 all", 400)
    page, size = _page_args(request, 50, 500)
    q = inv._s(qp.get("q"), 100)

    def run():
        # 先把"已到金额/状态"对一遍再按状态筛（库里状态旧了，欠票的单会从"待收/超期"里漏掉）；对完就不用逐行再算
        _later_heal()
        total, rows = S.later_list(E(), scope_user=u["name"] if scope == "mine" else None, status=status,
                                   q=q, page=page, size=size)
        return total, _later_views(rows, recalc=False)
    total, views = await run_in_threadpool(run)
    return {"ok": True, "total": total, "rows": views, "page": page, "size": size}


@router.get("/api/inv/later/export")
async def later_export(request: Request):
    u, bad = need(request, ENTER_LATER)
    if bad:
        return bad
    qp = request.query_params
    status = inv._s(qp.get("status"), 12) or "open"
    scope_user = u["name"] if qp.get("scope") == "mine" else None
    q = inv._s(qp.get("q"), 100)

    def run():
        _later_heal()        # 先对一遍已到金额/状态：旧的"已收齐"会让欠票的单漏出欠票清单
        # 欠票清单＝没收齐的（待收＋部分到票）；其它状态按原样筛
        if status in ("open", "unfinished"):
            rows = _later_all(scope_user, "open", q) + _later_all(scope_user, "partial", q)
        else:
            rows = _later_all(scope_user, None if status == "all" else status, q)
        return ie.export_later(_later_views(rows, recalc=False)), len(rows)
    if status not in ("open", "unfinished", "all") and status not in LATER_STATUSES:
        return err("导出状态不对", 400)
    data, n = await run_in_threadpool(run)
    inv.audit(u, "导出欠票清单", "发票后补池", "%d 笔（%s）" % (n, status))
    name = ("欠票清单_%s.xlsx" if status in ("open", "unfinished") else "发票后补池_%s.xlsx") % _stamp()
    return _xlsx(data, name, "invoice_later_%s.xlsx" % _stamp())


@router.get("/api/inv/later/{lid}")
async def later_detail(lid: int, request: Request):
    u, bad = need(request, ENTER_LATER)
    if bad:
        return bad
    l, bad = _later_get_or_404(lid)
    if bad:
        return bad

    def run(l=l):
        e = E()
        l = _later_recalc(lid, "系统") or l
        items = S.items_of_later(e, lid)
        tickets = [i for i in items if not _is_doc(i)]
        return {"ok": True, "later": inv.later_view(l, [i for i in tickets if i.get("kind") in ("invoice", "receipt")]),
                "items": inv.item_views(tickets), "docs": _later_docs(e, items),
                "logs": [inv.log_view(r) for r in S.logs_of(e, later_id=lid, limit=200)]}
    return await run_in_threadpool(run)


@router.post("/api/inv/later/resolve")
async def later_resolve(request: Request):
    u, bad = need(request, ENTER_LATER, CAP_RECEIVE)
    if bad:
        return bad
    body = await inv.body_json(request)
    code = inv._s(body.get("code"), 500)
    if not code:
        return err("请扫付款单（审批单）右上角的二维码，或输入审批编号", 400)
    c = ip.classify_code(code)
    if c["kind"] == "invoice_qr":
        return err("这是发票的二维码：登记后补要扫付款单（审批单）右上角的二维码，或输入审批编号", 400)
    if c["kind"] not in ("approval_link", "business_id"):
        return err("没认出付款单：请扫审批单右上角的二维码，或输入 20 位左右的审批编号", 400)
    r = await run_in_threadpool(inv.resolve_approval, code)
    if not r.get("ok"):
        return err(r.get("msg") or "付款单没取到", 400)
    n = r["norm"]
    cfg = inv.template_cfg(n.get("template"))
    if not (cfg and cfg.get("allowLater")):
        return err(_later_tpl_msg(n.get("template")), 400)
    _cache_norm(n)
    return {"ok": True, "prefill": _prefill(n, E())}


def _notify_outcome(sent):
    """发钉钉结果 → (是否真发出去, 给人看的一句话)。"""
    ok = bool((sent or {}).get("sent"))
    m = (sent or {}).get("msg") or ""
    if ok:
        return True, "已发钉钉通知"
    if m == "dry-run":
        return False, "测试环境没有真发钉钉（dry-run）"
    return False, "钉钉消息没发出去：%s" % (m or "原因不明")


def _later_create_sync(u, body, via="proxy", self_uid=None):
    """登记发票后补（线程池里跑：可能要去钉钉取审批单、发指派通知）→ (响应 dict, None) 或 (None, 错误响应)。
    via=proxy 财务在后补池代填（u＝工作台账号）；via=self 申请人自己登记（u＝{"name": 钉钉姓名}，self_uid＝钉钉 userid，
    只能登记自己发起的单子）。"""
    e = E()
    st = inv.get_settings()
    iid = inv._s(body.get("instId"), 80)
    if not iid:
        return None, err("先扫付款单（或输入审批编号）带出单据信息，再登记", 400)
    kind = inv._s(body.get("invKind"), 12)
    if kind not in LATER_KIND_CN:
        return None, err("发票种类只能选：专票 / 普票 / 收据", 400)
    # V2.622 用户定：税率必填（专票、普票）；收据没有税率不用填
    rate = "" if kind == "receipt" else norm_tax_rate(body.get("taxRate"))
    if kind != "receipt" and not rate:
        return None, err("请选税率（%s）" % " / ".join(LATER_TAX_RATES), 400)
    try:
        exp_date = inv._norm_date(body.get("expectDate"))
    except ValueError:
        exp_date = None
    if not exp_date:
        return None, err("预计到票日期要写成 2026-10-15 这样", 400)
    person = _receiver_of(inv._s(body.get("receiver"), 50), st)
    if not person:
        return None, err("「%s」不在财务接收人名单里：请选名单里的人（名单在设置 › 财务人员名单，勾上「接收人」）"
                         % (inv._s(body.get("receiver"), 50) or "未选"), 400)
    try:
        exp_amt = S.money(body.get("expectAmount"))
    except ValueError:
        return None, err("预计到票金额格式不对：%s" % inv._s(body.get("expectAmount"), 40), 400)
    n = _cached_norm(iid)
    from_dt = n is not None
    if n is None:
        f0 = S.folder_by_inst(e, iid)
        if f0:
            n = _norm_of_folder(f0)
        else:
            r = inv.resolve_approval(None, iid)
            if not r.get("ok"):
                return None, err(r.get("msg") or "付款单没取到", 400)
            n, from_dt = r["norm"], True
    cfg = inv.template_cfg(n.get("template"), st)
    if not (cfg and cfg.get("allowLater")):
        return None, err(_later_tpl_msg(n.get("template")), 400)
    if self_uid and (n.get("applicantUid") or "") != self_uid:
        return None, err("只能登记你自己发起的付款单/报销单：这张的申请人是 %s" % (n.get("applicant") or "别人"), 403)
    if exp_amt is None:
        try:
            exp_amt = S.money(n.get("amount")) if n.get("amount") is not None else None
        except ValueError:
            exp_amt = None
    if exp_amt is None or exp_amt <= 0:
        return None, err("请填预计到票金额（大于 0）", 400)
    with _LATER_LOCK:
        ex = S.later_open_by_inst(e, iid)
        if ex:
            return None, err("这张付款单已经登记过后补单（#%d），还没收齐：到原单上点「收到」就行；确需重登请先关闭原单" % ex["id"],
                             400, existingLaterId=ex["id"])
        fid = _folder_for_later(n, u["name"]) if from_dt else S.folder_by_inst(e, iid)["id"]
        lid = S.later_insert(
            e, folder_id=fid, inst_id=iid, business_id=n.get("businessId") or "", template=n.get("template") or "",
            applicant=n.get("applicant") or "", applicant_uid=n.get("applicantUid") or "", dept=n.get("dept") or "",
            company=n.get("company") or "", payee_name=n.get("payeeName") or "", payee_bank=n.get("payeeBank") or "",
            payee_account=n.get("payeeAccount") or "", pay_amount=n.get("amount"), reason=n.get("reason") or "",
            erp_no=n.get("erpNo") or "", inv_kind=kind, tax_rate=rate, expect_date=exp_date,
            expect_amount=exp_amt, receiver=person["account"], receiver_uid=person.get("dtUserid") or "",
            receiver_name=person.get("dtName") or person["account"], filed_by=u["name"],
            filed_uid=self_uid or inv.dt_uid_of(u["name"], st), filed_via=via, note=inv._s(body.get("note"), 500))
    l = S.later_get(e, lid)
    sent = inv.notify_dt([person.get("dtUserid")], _assign_text(l))
    notified, nmsg = _notify_outcome(sent)
    det = {"businessId": l.get("business_id") or "", "payee": l.get("payee_name") or "", "expectDate": exp_date,
           "expectAmount": inv._money_str(exp_amt), "receiver": person["account"], "invKind": kind,
           "notify": notified, "notifyMsg": sent.get("msg") or "", "via": via}
    act = "申请人自助登记发票后补" if via == "self" else "登记发票后补"
    inv.log(u, act, fid, None, lid, det)
    inv.audit(u, act, "后补单#%d %s" % (lid, l.get("payee_name") or ""), det)
    rn = l.get("receiver_name") or person["account"]
    return {"ok": True, "later": inv.later_view(l, []), "notify": sent, "notified": notified,
            "notifyMsg": ("已通过钉钉通知接收人 %s" % rn) if notified else ("接收人 %s 没收到通知：%s" % (rn, nmsg)),
            "msg": ("已登记后补单 #%d，已通知接收人 %s" % (lid, rn)) if notified else
            ("已登记后补单 #%d（接收人没收到钉钉消息：%s）" % (lid, sent.get("msg") or ""))}, None


@router.post("/api/inv/later/create")
async def later_create(request: Request):
    u, bad = need(request, ENTER_LATER, CAP_RECEIVE)
    if bad:
        return bad
    body = await inv.body_json(request)
    out, bad = await run_in_threadpool(_later_create_sync, u, body)
    return bad if bad else out


def _later_writable(l, allow_done=True):
    if l.get("status") == "closed":
        return err("这张后补单已关闭，不能再操作", 400)
    if not allow_done and l.get("status") == "done":
        return err("这张后补单已收齐，不用再操作", 400)
    if not l.get("folder_id"):
        return err("这张后补单没挂上付款单票夹，没法收票：请重新登记", 400)
    return None


def _link_confirmed(results, lid):
    """同票夹里本来就有这张票（审批附件带过来的）、这次扫到了实物：票挂到这张后补单上，算进已到。"""
    e = E()
    for r in results:
        if r.get("action") != "confirm" or not r.get("itemId"):
            continue
        it = S.item_get(e, r["itemId"])
        if it and not it.get("later_id") and it.get("kind") in ("invoice", "receipt") \
                and not (it.get("flags_json") or {}).get("dup") and it.get("review") != "void":
            S.item_update(e, it["id"], later_id=lid)


@router.post("/api/inv/later/{lid}/docs")
async def later_docs(lid: int, request: Request):
    u, bad = need(request, ENTER_LATER, CAP_RECEIVE)
    if bad:
        return bad
    l, bad = _later_get_or_404(lid)
    if bad:
        return bad
    g = _later_writable(l)
    if g:
        return g
    files, fields, results, bad = await inv.read_upload_files(request)
    if bad:
        return bad
    if not files and not results:
        return err("没收到文件", 400)

    out, docs = await run_in_threadpool(store_later_docs, l, lid, files, u["name"])
    results.extend(out)
    inv.audit(u, "上传后补资料", "后补单#%d" % lid, "、".join(n for n, _ in files)[:400])
    return {"ok": True, "results": results, "docs": docs}


def store_later_docs(l, lid, files, uname):
    """后补资料只留存、不识别（线程池里跑）→ (逐个结果, 这张后补单现有资料列表)。财务上传、申请人自助上传共用。"""
    e = E()
    out = []
    for i, (name, data) in enumerate(files):
        same = S.file_by_sha(e, l["folder_id"], hashlib.sha256(data).hexdigest())
        if same:
            out.append(inv._res(name, "same", "这份资料已经传过了"))
            continue
        try:
            t = ip.sniff_type(name, data)
        except Exception:
            t = "other"
        ext, mime = inv._ext_mime(t if t in ("pdf", "ofd", "xml") else "other", name)
        # 资料只留存、不识别：合同、对账单里常有金额，当票识别会被误算成"已到票"
        try:
            f = inv.store_file(l["folder_id"], name, data, "later", uname, role="doc", mime=mime, ext=ext)
        except inv.StoreRefused as ex:      # 磁盘快满：这份和后面的都不存
            out.extend(inv._res(n, "error", str(ex)) for n, _d in files[i:])
            break
        except Exception as ex:
            out.append(inv._res(name, "error", "「%s」没存上：%s" % (name, ex)))
            continue
        try:
            iid = S.item_insert(e, folder_id=l["folder_id"], file_id=f["id"], kind="other", origin="later",
                                type_label=inv._clean_name(name)[:60], later_id=lid, review="approved",
                                review_by="系统", review_at=inv.now_s(), created_by=uname)
            # 资料不是票、不用审：直接记"已通过"，免得它进审核队列、挡住整夹批量通过
            S.file_update(e, f["id"], item_id=iid)
        except Exception as ex:
            inv._fail_orphan_file(f)       # 没挂上：文件标失败，重传同一份时不会被"已经传过了"挡住
            out.append(inv._res(name, "error", "「%s」没存上：%s" % (name, ex)))
            continue
        inv.log(uname, "上传后补资料", l["folder_id"], iid, lid, {"name": name, "size": len(data)})
        out.append(inv._res(name, "doc", "已留存", itemId=iid))
    return out, _later_docs(e, S.items_of_later(e, lid))


def _after_receive(results, lid, user):
    """后补收票之后（线程池里跑）：同夹已有的票挂上这张后补单 → 重算已到金额/状态（收齐发"已签收"）→ 补视图。→ 后补单视图。"""
    _link_confirmed(results, lid)
    l2 = _later_recalc(lid, user)
    inv.attach_views(results)
    return _lv(l2)


@router.post("/api/inv/later/{lid}/receive")
async def later_receive(lid: int, request: Request):
    u, bad = need(request, ENTER_LATER, CAP_RECEIVE)
    if bad:
        return bad
    l, bad = _later_get_or_404(lid)
    if bad:
        return bad
    g = _later_writable(l)
    if g:
        return g
    body = await inv.body_json(request)
    code = inv._s(body.get("code"), 500)
    if not code:
        return err("没收到扫码内容", 400)
    c = ip.classify_code(code)
    if c["kind"] in ("approval_link", "business_id"):
        return err("这是审批单的二维码：收票请扫发票左上角的二维码", 400)
    if c["kind"] != "invoice_qr":
        return err("没认出发票二维码：请扫发票左上角的二维码（电子发票请用「上传」）", 400)
    extra = {"via": "后补收票", "laterId": lid}
    r = await run_in_threadpool(inv.ingest_qr, l["folder_id"], c["value"], u["name"], "scanner", lid, None, extra)
    if r.get("action") in ("noFolder", "unknown"):
        return err(r.get("msg") or "没登记上", 400)
    lv = await run_in_threadpool(_after_receive, [r], lid, u["name"])
    inv.audit(u, "后补收票", "后补单#%d %s" % (lid, l.get("payee_name") or ""), r.get("msg") or "")
    return {"ok": True, "action": r.get("action"), "msg": r.get("msg") or "", "item": r.get("item"),
            "later": lv}


@router.post("/api/inv/later/{lid}/receive-upload")
async def later_receive_upload(lid: int, request: Request):
    u, bad = need(request, ENTER_LATER, CAP_RECEIVE)
    if bad:
        return bad
    l, bad = _later_get_or_404(lid)
    if bad:
        return bad
    g = _later_writable(l)
    if g:
        return g
    files, fields, results, bad = await inv.read_upload_files(request)
    if bad:
        return bad
    if not files and not results:
        return err("没收到文件", 400)
    extra = {"via": "后补收票", "laterId": lid}
    budget = {"left": inv.MAX_TOTAL}     # 这一次上传的压缩包共用一份解开总量额度
    for name, data in files:
        # 后补收票里夹着审批单打印件也只留存，不切换任何人的当前票夹
        rs = await run_in_threadpool(inv.ingest_bytes, l["folder_id"], name, data, "upload", u["name"],
                                     later_id=lid, allow_switch=False, log_extra=extra, budget=budget)
        results.extend(rs)
    inv.wake()
    lv = await run_in_threadpool(_after_receive, results, lid, u["name"])
    if files:
        inv.audit(u, "后补收票", "后补单#%d %s" % (lid, l.get("payee_name") or ""),
                  "%d 个文件：%s" % (len(files), inv._upload_summary(results)))
    return {"ok": True, "results": results, "later": lv}


def _mark_sync(u, l, lid, amt, note):
    with _LATER_LOCK:
        cur = S.later_get(E(), lid)
        S.later_update(E(), lid, unregistered_amount=round(float(cur.get("unregistered_amount") or 0) + float(amt), 2))
    det = {"amount": inv._money_str(amt), "note": note}
    inv.log(u, "标记已收到（未登记号码）", l["folder_id"], None, lid, det)
    inv.audit(u, "后补标记收到", "后补单#%d %s" % (lid, l.get("payee_name") or ""), det)
    return _lv(_later_recalc(lid, u["name"]))


@router.post("/api/inv/later/{lid}/mark")
async def later_mark(lid: int, request: Request):
    u, bad = need(request, ENTER_LATER, CAP_RECEIVE)
    if bad:
        return bad
    l, bad = _later_get_or_404(lid)
    if bad:
        return bad
    g = _later_writable(l, allow_done=False)
    if g:
        return g
    body = await inv.body_json(request)
    try:
        amt = S.money(body.get("amount"))
    except ValueError:
        return err("金额格式不对：%s" % inv._s(body.get("amount"), 40), 400)
    if amt is None or amt <= 0:
        return err("请填收到的金额（大于 0）", 400)
    note = inv._s(body.get("note"), 200)
    lv = await run_in_threadpool(_mark_sync, u, l, lid, amt, note)
    return {"ok": True, "msg": "已标记收到 ¥%s，号码还没登记（标黄），之后扫码或上传补上" % inv._money_str(amt),
            "later": lv}


def _days_left(l, today=None):
    try:
        return (date.fromisoformat(str(l.get("expect_date"))[:10]) - (today or date.today())).days
    except (TypeError, ValueError):
        return None


def _remind_text(l, today=None):
    days = _days_left(l, today)
    when = "预计 %s 到票" % (l.get("expect_date") or "—")
    if days is not None:
        when += ("（已超期 %d 天）" % -days) if days < 0 else ("（就是今天）" if days == 0 else "（还有 %d 天）" % days)
    rem = _later_remaining(l)
    rn = l.get("receiver_name") or l.get("receiver") or ""
    return MSG_HEAD + "催票提醒：%s 付给「%s」的款 ¥%s，%s，还差 ¥%s 的%s没到。请尽快把发票交给财务%s。财务查看 → %s" % (
        _later_title(l), l.get("payee_name") or "", inv._money_str(l.get("pay_amount")), when,
        inv._money_str(rem if rem is not None else 0), LATER_KIND_CN.get(l.get("inv_kind") or "", "发票"),
        ("（接收人 %s）" % rn) if rn else "", _later_link(l))


def send_remind(l, user, auto=False, reason="", st=None, today=None):
    """催票：发给申请人＋抄送接收人（一条消息两个人）。发出去（或测试 dry-run）才记"上次催票/次数"，
    没发出去不记——免得自动催票以为催过了就不再催。→ notify_dt 的结果 {sent, msg}。"""
    st = st or inv.get_settings()
    uids = [l.get("applicant_uid") or "", l.get("receiver_uid") or inv.dt_uid_of(l.get("receiver"), st)]
    uids = [x for x in uids if x]
    if not uids:
        res = {"sent": False, "msg": "申请人和接收人都没有钉钉身份（申请人取自审批单，接收人要在设置里绑定钉钉），没发出去"}
    else:
        res = inv.notify_dt(uids, _remind_text(l, today))
    ok = bool(res.get("sent")) or res.get("msg") == "dry-run"
    if ok:
        S.later_update(E(), l["id"], last_remind_at=inv.now_s(), remind_count=int(l.get("remind_count") or 0) + 1)
    inv.log(user, "自动催票" if auto else "催票", l.get("folder_id"), None, l["id"],
            {"to": [l.get("applicant") or "", l.get("receiver_name") or l.get("receiver") or ""], "sent": bool(res.get("sent")),
             "msg": res.get("msg") or "", "reason": reason})
    return res


@router.post("/api/inv/later/{lid}/remind")
async def later_remind(lid: int, request: Request):
    u, bad = need(request, ENTER_LATER, CAP_RECEIVE)
    if bad:
        return bad
    l, bad = _later_get_or_404(lid)
    if bad:
        return bad
    def run(l=l):
        l = _later_recalc(lid, u["name"]) or l       # 可能刚好收齐了（收齐会发"已签收"）：线程里算
        if l.get("status") not in LATER_ACTIVE:
            return None, err("这张后补单%s，不用再催" % LATER_STATUS_CN.get(l.get("status"), ""), 400)
        res = send_remind(l, u["name"])
        inv.audit(u, "催票", "后补单#%d %s" % (lid, l.get("payee_name") or ""), res.get("msg") or "")
        return {"ok": True, "sent": bool(res.get("sent")), "msg": res.get("msg") or "",
                "later": _lv(S.later_get(E(), lid))}, None
    out, bad = await run_in_threadpool(run)
    return bad if bad else out


@router.post("/api/inv/later/{lid}/update")
async def later_update(lid: int, request: Request):
    u, bad = need(request, ENTER_LATER, CAP_RECEIVE)
    if bad:
        return bad
    l, bad = _later_get_or_404(lid)
    if bad:
        return bad
    if l.get("status") == "closed":
        return err("这张后补单已关闭，不能再改", 400)
    body = await inv.body_json(request)
    st = inv.get_settings()
    upd, changes = {}, {}
    if "expectDate" in body:
        try:
            d = inv._norm_date(body.get("expectDate"))
        except ValueError:
            d = None
        if not d:
            return err("预计到票日期要写成 2026-10-15 这样", 400)
        upd["expect_date"] = d
    if "expectAmount" in body:
        try:
            a = S.money(body.get("expectAmount"))
        except ValueError:
            return err("预计到票金额格式不对：%s" % inv._s(body.get("expectAmount"), 40), 400)
        if a is None or a <= 0:
            return err("预计到票金额要大于 0", 400)
        upd["expect_amount"] = a
    if "invKind" in body:
        k = inv._s(body.get("invKind"), 12)
        if k not in LATER_KIND_CN:
            return err("发票种类只能选：专票 / 普票 / 收据", 400)
        upd["inv_kind"] = k
    if "taxRate" in body:
        upd["tax_rate"] = inv._s(body.get("taxRate"), 20)
    if "note" in body:
        upd["note"] = inv._s(body.get("note"), 500)
    new_recv = None
    if "receiver" in body and inv._s(body.get("receiver"), 50) != (l.get("receiver") or ""):
        new_recv = _receiver_of(inv._s(body.get("receiver"), 50), st)
        if not new_recv:
            return err("「%s」不在财务接收人名单里：请选名单里的人" % (inv._s(body.get("receiver"), 50) or "未选"), 400)
        upd.update(receiver=new_recv["account"], receiver_uid=new_recv.get("dtUserid") or "",
                   receiver_name=new_recv.get("dtName") or new_recv["account"])
    for col, v in upd.items():
        old = l.get(col)
        if not inv._same(old, float(v) if col == "expect_amount" else v):
            changes[col] = [str(old) if old is not None else "", str(v)]
    if not changes:
        return {"ok": True, "msg": "没有改动", "later": _lv(l)}

    def run():
        # 改预计金额可能收齐/不齐（收齐发"已签收"）、换接收人要发指派通知：都在线程里做，不占住整站
        S.later_update(E(), lid, **{k: v for k, v in upd.items() if k in changes or k in ("receiver_uid", "receiver_name")})
        l2 = _later_recalc(lid, u["name"])
        sent = None
        if new_recv:
            sent = inv.notify_dt([new_recv.get("dtUserid")], _assign_text(l2))
        det = {"changes": changes}
        if sent is not None:
            det.update(notify=bool(sent.get("sent")), notifyMsg=sent.get("msg") or "")
        inv.log(u, "修改后补单", l.get("folder_id"), None, lid, det)
        inv.audit(u, "修改后补单", "后补单#%d %s" % (lid, l.get("payee_name") or ""), det)
        out = {"ok": True, "msg": "已保存", "later": _lv(l2)}
        if sent is not None:
            out["notify"] = sent
            out["notified"], out["notifyMsg"] = _notify_outcome(sent)
        return out
    return await run_in_threadpool(run)


@router.post("/api/inv/later/{lid}/close")
async def later_close(lid: int, request: Request):
    u, bad = need(request, ENTER_LATER, CAP_RECEIVE)
    if bad:
        return bad
    l, bad = _later_get_or_404(lid)
    if bad:
        return bad
    if l.get("status") not in LATER_ACTIVE:
        return err("这张后补单%s，不用关闭" % LATER_STATUS_CN.get(l.get("status"), ""), 400)
    body = await inv.body_json(request)
    note = inv._s(body.get("note"), 500)
    if not note:
        return err("关闭要写原因（比如：供应商不开票了、已退款），留痕备查", 400)
    S.later_update(E(), lid, status="closed", closed_by=u["name"], closed_at=inv.now_s(), close_note=note)
    det = {"note": note, "from": l.get("status"), "remaining": _later_remaining(l)}
    inv.log(u, "关闭后补单", l.get("folder_id"), None, lid, det)
    inv.audit(u, "关闭后补单", "后补单#%d %s" % (lid, l.get("payee_name") or ""), det)
    return {"ok": True, "msg": "已关闭，不再催票", "later": _lv(S.later_get(E(), lid))}


# ═══════════════════════════ 催票线程 ═══════════════════════════
_REMIND = {"started": False, "lastDay": "", "lastRun": "", "lastError": "", "lastResult": None}
_REMIND_LOCK = threading.Lock()
_REMIND_WAKE = threading.Event()


def remind_tick(now=None, force=False):
    """每日催票一轮（线程每 10 分钟调一次；测试直接调）。当天过了设置的整点才跑、一天只跑一轮（force 跳过这两条）。
    挑出 store.laters_due_for_remind 的单：先重算（可能刚好收齐了），仍未收齐的发申请人＋接收人。单张出错不影响别的。"""
    now = now or datetime.now()
    st = inv.get_settings()
    r = st.get("remind") or {}
    if not r.get("enabled", True):
        return {"ran": False, "reason": "disabled"}
    today = now.strftime("%Y-%m-%d")
    if not force:
        if now.hour < int(r.get("hour", 10)):
            return {"ran": False, "reason": "early"}
        with _REMIND_LOCK:
            if _REMIND["lastDay"] == today:
                return {"ran": False, "reason": "doneToday"}
            _REMIND["lastDay"] = today      # 先占住今天（同进程别的调用不再跑）；取待催清单失败就退掉，下一轮重试
    try:
        try:
            _later_heal()                   # 先对一遍已到金额/状态：旧的"已收齐"会让欠票的单漏催
        except Exception as ex:
            _REMIND["lastError"] = "后补单自愈重算失败：%s" % ex
        due = S.laters_due_for_remind(E(), today, r.get("beforeDays", 3), r.get("everyDays", 7))
    except Exception:
        if not force:
            with _REMIND_LOCK:
                if _REMIND["lastDay"] == today:
                    _REMIND["lastDay"] = ""   # 今天没催成：10 分钟后下一轮再试，不白白跳过一天
        raise
    sent, failed, skipped, ids = 0, 0, 0, []
    for d in due:
        try:
            l = _later_recalc(d["id"], "系统")
            if not l or l.get("status") not in LATER_ACTIVE:
                skipped += 1
                continue
            res = send_remind(l, "系统", auto=True, reason=d.get("remind_reason") or "", st=st, today=now.date())
            if res.get("sent") or res.get("msg") == "dry-run":
                sent += 1
                ids.append(l["id"])
            else:
                failed += 1
        except Exception as ex:
            failed += 1
            _REMIND["lastError"] = "后补单#%s：%s" % (d.get("id"), ex)
    out = {"ran": True, "day": today, "due": len(due), "sent": sent, "failed": failed, "skipped": skipped, "ids": ids}
    _REMIND["lastRun"] = inv.now_s()
    _REMIND["lastResult"] = out
    if due:
        inv.audit("系统", "自动催票", today, "该催 %d 张：发出 %d、没发出 %d、刚好收齐 %d" % (len(due), sent, failed, skipped))
    return out


def _remind_loop():
    while True:
        try:
            remind_tick()
        except Exception:
            _REMIND["lastError"] = traceback.format_exc()[-2000:]    # 绝不让线程死掉：记下来，下一轮再试
        _REMIND_WAKE.wait(600)
        _REMIND_WAKE.clear()


def start_reminder():
    """起催票线程（一个进程一次）；本地/测试用 SQLite 时不起——免得开发机给真人发催票。"""
    try:
        if str(db.DB_URL).startswith("sqlite"):
            return False
    except Exception:
        return False
    with _REMIND_LOCK:
        if _REMIND["started"]:
            return False
        threading.Thread(target=_remind_loop, name="inv-remind", daemon=True).start()
        _REMIND["started"] = True
    return True


start_reminder()
