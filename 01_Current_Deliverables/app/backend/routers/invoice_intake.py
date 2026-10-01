# [Change Log] Date: 2026-10-01 | Author: Claude Opus 5.5 | Version: V2.736（发票管家·钉钉自动接入）
# Description: Owner 2026-10-01 定：发票管家要集成全公司进项票，先接钉钉；只接「审批流走到接入审批人节点」的单子
#   （审批单 tasks 里有他的 userid）。模板＝设置里的审批模板（付款申请（公对公）、费用报销），起始日 9/1 往前补。
#   每 20 分钟扫一轮：首扫（或改了起始日）列 since～今天的单子，之后只列最近 3 天新提交的；
#   走到了 → 建票夹拉附件（复用 invoice.open_approval_folder，source=dingtalk，记在"系统"名下）；
#   还在审批、没走到 → 记进 waiting，下一轮再取；撤回/拒绝/审完了也没走到 → 跳过。已有票夹的单子不再取。
#   扫描状态存 app_settings: inv_intake_state（不新增表）。SQLite 库（本机/测试）不起定时线程，测试直接调 scan_once。
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from fastapi import APIRouter, Request
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from core import db
from kernels import invoice_dingtalk as idt
from kernels import invoice_store as S
from routers import invoice as inv

router = APIRouter()

STATE_KEY = "inv_intake_state"
SOURCE = "dingtalk"
EVERY_MIN = 20
RECENT_DAYS = 3                  # 首扫之后每轮只列最近几天新提交的（更早的靠 waiting 跟）
SKIP_KEEP_DAYS = RECENT_DAYS + 2  # 判过"不接"的单子记几天（列表窗口滑过去就不会再出现，不用一直记）
MAX_WINDOW_DAYS = 119            # 钉钉 listids 时间窗上限 120 天
_LOCK = threading.Lock()
_STATE = {"started": False, "lastError": ""}
_WAKE = threading.Event()


def reached(inst, uids):
    """审批流走到了 uids 里某人的节点 → True。钉钉只给已生成的任务：tasks 里出现他＝流程到过他这一步
    （待他审 NEW/RUNNING、他审过 COMPLETED、或签被别人先审了 CANCELED 都算）。纯函数，供单测。"""
    uids = set(uids or ())
    return bool(uids) and any(str((t or {}).get("userid") or "") in uids for t in (inst or {}).get("tasks") or [])


def dead(inst):
    """撤回或被拒的单子（不建票夹）。"""
    st = str((inst or {}).get("status") or "").upper()
    res = str((inst or {}).get("result") or "").lower()
    return st == "TERMINATED" or res == "refuse"


def _ct(inst):
    return str((inst or {}).get("create_time") or "")[:19]


def _load_state():
    s = db.get_setting(STATE_KEY, None)
    s = s if isinstance(s, dict) else {}
    for k in ("waiting", "skip"):
        if not isinstance(s.get(k), dict):
            s[k] = {}
    return s


def _folder_insts(ids):
    if not ids:
        return set()
    out = set()
    ids = list(ids)
    with inv.E().connect() as c:
        for i in range(0, len(ids), 500):
            out |= {r[0] for r in c.execute(select(S.FOLDER.c.inst_id).where(S.FOLDER.c.inst_id.in_(ids[i:i + 500])))}
    return out


def scan_once(trigger="定时"):
    if not _LOCK.acquire(blocking=False):
        return {"ok": False, "msg": "上一轮还在扫，稍后再试"}
    try:
        return _scan(trigger)
    except Exception as ex:
        _STATE["lastError"] = traceback.format_exc()[-2000:]
        return {"ok": False, "msg": "扫描出错：%s" % ex}
    finally:
        _LOCK.release()


def _scan(trigger):
    t0 = time.time()
    st = inv.get_settings()
    cfg = st.get("intake") or {}
    uids = [a["dtUserid"] for a in cfg.get("approvers") or [] if a.get("dtUserid")]
    if not uids:
        return {"ok": False, "msg": "还没设接入审批人：在发票管家设置 › 钉钉自动接入里加人"}
    tpls = [t["name"] for t in st.get("templates") or [] if t.get("name")]
    if not tpls:
        return {"ok": False, "msg": "还没设审批模板"}
    conf = idt._load_conf()
    if not conf:
        return {"ok": False, "msg": "未配置钉钉（conf.ini [dingtalk]）"}
    state = _load_state()
    since = cfg.get("since") or "2026-09-01"
    now = datetime.now(idt.CN_TZ)
    full = state.get("since") != since or not state.get("last")      # 首扫/改了起始日：从起始日整段列
    start = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=idt.CN_TZ)
    if not full:
        start = max(start, now - timedelta(days=RECENT_DAYS))
    start = max(start, now - timedelta(days=MAX_WINDOW_DAYS))
    ids, notes = [], []
    for nm in tpls:
        pc, why = idt._process_code(conf, nm)
        if not pc:
            notes.append(why)
            continue
        got, lerr = idt._list_ids(conf, pc, idt._ms(start), idt._ms(now), max_pages=300)
        if lerr:
            notes.append("%s：%s" % (nm, lerr))
        ids += [i for i in got if i not in ids]
    waiting, skip = state["waiting"], state["skip"]
    cand = [i for i in ids if i not in skip] + [i for i in waiting if i not in ids]
    have = _folder_insts(cand)
    for i in have:
        waiting.pop(i, None)
    todo = [i for i in cand if i not in have]

    def get(i):
        try:
            r = idt._oapi(conf, "topapi/processinstance/get", {"process_instance_id": i})
            x = (r.get("process_instance") or r.get("result")) if r.get("errcode") == 0 else None
            return i, (x if isinstance(x, dict) else None)
        except Exception:
            return i, None

    with ThreadPoolExecutor(8) as ex:
        got = list(ex.map(get, todo))
    got = [(i, x) if x else get(i) for i, x in got]     # 并发偶尔被钉钉限流回空：逐张再取一次
    n_new = n_wait = n_skip = n_fail = 0
    fails = []
    for iid, x in got:
        if not x:
            n_fail += 1
            continue
        ct = _ct(x)
        if ct[:10] and ct[:10] < since:              # waiting 里留着的、起始日往后挪了的
            waiting.pop(iid, None)
            continue
        if dead(x):
            waiting.pop(iid, None)
            skip[iid] = ct
            n_skip += 1
        elif reached(x, uids):
            r = inv.open_approval_folder(user=inv.SYSTEM_USER, source=SOURCE, inst_id=iid, inst=x)
            if r.get("ok"):
                waiting.pop(iid, None)
                n_new += 1
            else:
                n_fail += 1
                fails.append("%s：%s" % (x.get("business_id") or iid, r.get("msg")))
        elif str(x.get("status") or "").upper() == "RUNNING":
            waiting[iid] = ct
            n_wait += 1
        else:                                         # 审完了也没走到接入审批人
            waiting.pop(iid, None)
            skip[iid] = ct
            n_skip += 1
    cut = (now - timedelta(days=SKIP_KEEP_DAYS)).strftime("%Y-%m-%d")
    state["skip"] = {k: v for k, v in skip.items() if str(v)[:10] >= cut}
    state["waiting"] = waiting
    state["since"] = since
    last = {"at": inv.now_s(), "trigger": trigger, "full": full, "from": start.strftime("%Y-%m-%d"),
            "listed": len(ids), "fetched": len(todo), "created": n_new, "waiting": len(waiting), "skipped": n_skip,
            "failed": n_fail, "notes": "；".join(notes)[:400], "fails": fails[:10], "sec": round(time.time() - t0, 1)}
    state["last"] = last
    db.set_setting(STATE_KEY, state, inv.SYSTEM_USER)
    if n_new:
        inv.audit(inv.SYSTEM_USER, "钉钉自动接入", trigger, "新建票夹 %d 个；还没走到接入审批人的 %d 张" % (n_new, len(waiting)))
    return {"ok": True, **last}


def status():
    st = _load_state()
    return {"last": st.get("last") or None, "waiting": len(st.get("waiting") or {}), "since": st.get("since") or "",
            "running": _LOCK.locked(), "everyMin": EVERY_MIN, "timer": _STATE["started"],
            "lastError": _STATE["lastError"][-400:]}


# ───────────────────────── 接口 ─────────────────────────

@router.get("/api/inv/intake")
async def intake_status(request: Request):
    u, bad = inv.need(request, None, inv.CAP_CONFIG)
    if bad:
        return bad
    return {"ok": True, **status()}


def _auto_folders():
    rows, total = S.folders_auto_open(inv.E(), SOURCE, limit=300)
    return {"ok": True, "rows": inv.folder_views(rows), "total": total}


@router.get("/api/inv/desk/auto")
async def desk_auto_folders(request: Request):
    """V2.737 收票工作台「钉钉接入·待收票」：自动接入建的、还在收票/被退回的票夹（记在系统名下，「我的票夹」里看不到）。"""
    u, bad = inv.need(request, inv.ENTER_DESK)
    if bad:
        return bad
    return await run_in_threadpool(_auto_folders)


@router.post("/api/inv/intake/scan")
async def intake_scan(request: Request):
    u, bad = inv.need(request, None, inv.CAP_CONFIG)
    if bad:
        return bad
    r = await run_in_threadpool(scan_once, "手动·" + inv._uname(u))
    if not r.get("ok"):
        return inv.err(r.get("msg") or "扫描失败", 400)
    return r


# ───────────────────────── 定时线程 ─────────────────────────

def _loop():
    time.sleep(90)                     # 开机先让别的线程起来
    while True:
        try:
            if (inv.get_settings().get("intake") or {}).get("approvers"):
                scan_once("定时")
        except Exception:
            _STATE["lastError"] = traceback.format_exc()[-2000:]
        _WAKE.wait(EVERY_MIN * 60)
        _WAKE.clear()


def start_timer():
    """起定时线程（一个进程一次）；SQLite 库（本机/测试）不起，免得开发机去扫真钉钉。"""
    try:
        if str(db.DB_URL).startswith("sqlite"):
            return False
    except Exception:
        return False
    if _STATE["started"]:
        return False
    threading.Thread(target=_loop, name="inv-intake", daemon=True).start()
    _STATE["started"] = True
    return True


start_timer()
