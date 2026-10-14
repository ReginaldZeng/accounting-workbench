# [Change Log] Date: 2026-10-13 | Author: Claude / c | Version: V2.948（钉钉流程里后到的发票走后补单）
# Description: Owner 2026-10-13：还欠票的单子（有没收齐后补单的付款单、自动接入还在收票的票夹）每轮顺带再去钉钉流程里看一眼
#   （recheck_flows：同一张单至少隔 60 分钟、一轮最多 30 张、只取上次看过之后发到评论区的新文件）；财务初审通过后才到的发票
#   记到后补单上进审核（invoice.flow_recheck / link_late）。后补登记建的票夹（source=later）票齐了也自己进审核。
# [Change Log] Date: 2026-10-01 | Author: Claude Opus 5.5 | Version: V2.736（发票管家·钉钉自动接入）
# Description: Owner 2026-10-01 定：发票管家要集成全公司进项票，先接钉钉；只接「审批流走到接入审批人节点」的单子
#   （审批单 tasks 里有他的 userid）。模板＝设置里的审批模板（付款申请（公对公）、费用报销），起始日 9/1 往前补。
#   每 20 分钟扫一轮：首扫（或改了起始日）列 since～今天的单子，之后只列最近 3 天新提交的；
#   走到了 → 建票夹拉附件（复用 invoice.open_approval_folder，source=dingtalk，记在"系统"名下）；
#   还在审批、没走到 → 记进 waiting，下一轮再取；撤回/拒绝/审完了也没走到 → 跳过。已有票夹的单子不再取。
#   扫描状态存 app_settings: inv_intake_state（不新增表）。SQLite 库（本机/测试）不起定时线程，测试直接调 scan_once。
# [Change Log] Date: 2026-10-02 | Author: Claude Opus 5.5 | Version: V2.739
# Description: Owner 定：票据齐全的直接进审核（不用收票台点提交），纸质件月末一次性查验；流程里没传/没传齐的留收票台，
#   财务补传后在收票台提交，审核才看得全。「齐全」＝提交检查（submit_check）既不拦也没有任何提示：附件拉全、票都识别完、
#   没重复票、有发票且付款金额−专票−普票−后补未到＝0。每 2 分钟看一次（auto_submit_ready），提交人记「系统」。
#   收票台「钉钉接入」列表每行带「为什么还留在这」（why）。
# [Change Log] Date: 2026-10-13 | Author: Claude Opus 5.5 | Version: V-draft（发票管家·审票分工）
# Description: 用户 2026-10-13 定：发票审核按人分。①每位接入审批人带自己的启用日（设置 intake.approvers[].since，留空＝还没启用）：
#   只有「提交日」在这个人启用日当天及以后的单，才因为走到他的节点而接进来；②票夹记在「最先走到、且对这张单已启用」的那位名下
#   （inv_folder.owner_*，按钉钉 tasks 的先后）；③老的全局起始日不再参与判断；谁的启用日改了，只从他的启用日起重列一遍
#   （别人不受影响：已有票夹的单不会再建）。切换期四位会计还在用票总管时启用日留空，单不会往他们名下堆。
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
AUTO_SUBMIT_SOURCES = (SOURCE, "later")   # 票齐了自己进审核的票夹来源：钉钉自动接入的、后补登记时系统建的
RECHECK_KEY = "inv_flow_recheck"         # {票夹 id: 上次去钉钉流程里看的时间}
RECHECK_MIN = 60                         # 同一张单最少隔多少分钟再看一次
RECHECK_MAX = 30                         # 一轮最多看几张（一张一次钉钉接口）
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


# 找「归哪位接入审批人」的两个函数在 routers/invoice.py（收票台扫付款单、后补单、物流对账建票夹时也用同一条规矩）
enabled_approvers = inv.enabled_approvers
first_owner = inv.first_owner


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


def ready_check(folder, items):
    """自动接入的票夹票据齐全吗 → (齐全?, 不齐的原因)。齐全＝提交检查既不拦也没提示、有发票/收据、付款金额有值。"""
    if folder.get("attach_status") in ("pending", "running", "none", None):
        return False, "附件还在拉"
    act = [i for i in items if i.get("status") != "removed" and i.get("review") != "void"]
    bills = [i for i in act if i.get("kind") in ("invoice", "receipt")]
    blockers, warnings = inv.submit_check(folder, items)
    if blockers:
        return False, blockers[0]["msg"]
    if not bills:
        return False, "流程里没传发票，要财务补传"
    if folder.get("amount") is None:
        return False, "审批单上读不出付款金额，金额核对做不了"
    if warnings:
        return False, warnings[0]["msg"]
    return True, ""


def auto_submit_ready(refs=None):
    """自动接入、还在收票中的票夹：票据齐全的直接提交进审核（提交人「系统」）。→ 这轮提交了几个。
    被审核退回的（returned）不动：退回后由财务在收票台处理。
    refs 传一个列表进来 → 把这轮送审的票夹的钉钉审批编号放进去（数字员工办公室记干活记录用）。"""
    e = inv.E()
    with e.connect() as c:
        rows = [S._row(r) for r in c.execute(select(S.FOLDER).where(
            S.FOLDER.c.source.in_(AUTO_SUBMIT_SOURCES) & (S.FOLDER.c.status == "collecting")
            & (S.FOLDER.c.attach_status == "done")))]
    n = 0
    for f in rows:
        ok, _ = ready_check(f, S.folder_items(e, f["id"]))
        if not ok:
            continue
        out, bad = inv._submit_sync({"name": inv.SYSTEM_USER}, f["id"])
        if out:
            n += 1
            if refs is not None and f.get("business_id"):
                refs.append(str(f["business_id"]))
    return n


def recheck_flows(refs=None, now=None):
    """还欠票的单子再去钉钉流程里找新发票 → {checked, new, linked}。
    范围：有没收齐后补单的付款单 ＋ 自动接入、还在收票的票夹。同一张单至少隔 RECHECK_MIN 分钟，一轮最多 RECHECK_MAX 张
    （最久没看的先看）；只取上次看过之后发到评论区的新文件。refs 传列表 → 放进这轮记上后补单的钉钉审批编号。"""
    e = inv.E()
    with e.connect() as c:
        ids = {r[0] for r in c.execute(select(S.LATER.c.folder_id).where(S.LATER.c.status.in_(("open", "partial"))))}
        ids |= {r[0] for r in c.execute(select(S.FOLDER.c.id).where(
            (S.FOLDER.c.source == SOURCE) & (S.FOLDER.c.status == "collecting")))}
    ids = {int(i) for i in ids if i}
    st = db.get_setting(RECHECK_KEY, None)
    st = {str(k): str(v) for k, v in (st if isinstance(st, dict) else {}).items() if str(k).isdigit() and int(k) in ids}
    now = now or inv.now_s()
    edge = (datetime.strptime(now[:19], "%Y-%m-%d %H:%M:%S") - timedelta(minutes=RECHECK_MIN)).strftime("%Y-%m-%d %H:%M:%S")
    due = sorted((i for i in ids if st.get(str(i), "") <= edge), key=lambda i: (st.get(str(i), ""), i))[:RECHECK_MAX]
    out = {"checked": 0, "new": 0, "linked": 0}
    for fid in due:
        f = S.folder_get(e, fid)
        if not f or not f.get("inst_id"):
            st[str(fid)] = now                      # 手工票夹没有流程可看：记一下，免得每轮都排在前面
            continue
        r = inv.flow_recheck(fid, manual=False, since=st.get(str(fid)) or f.get("created_at") or "")
        if not r.get("ok"):
            continue                                # 没看成（钉钉没给/正在拉附件）：不记时间，下一轮再看
        st[str(fid)] = now
        out["checked"] += 1
        out["new"] += r.get("new") or 0
        out["linked"] += r.get("linked") or 0
        if r.get("linked") and refs is not None and f.get("business_id"):
            refs.append(str(f["business_id"]))
    db.set_setting(RECHECK_KEY, st, inv.SYSTEM_USER)
    return out


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
    if not [a for a in cfg.get("approvers") or [] if a.get("dtUserid")]:
        return {"ok": False, "msg": "还没设接入审批人：在发票管家设置 › 钉钉自动接入里加人"}
    aps = enabled_approvers(cfg)
    if not aps:
        return {"ok": False, "msg": "接入审批人都还没填启用日：在发票管家设置 › 钉钉自动接入里，给开始用发票管家的人填上启用日"}
    tpls = [t["name"] for t in st.get("templates") or [] if t.get("name")]
    if not tpls:
        return {"ok": False, "msg": "还没设审批模板"}
    conf = idt._load_conf()
    if not conf:
        return {"ok": False, "msg": "未配置钉钉（conf.ini [dingtalk]）"}
    state = _load_state()
    since_by = {a["dtUserid"]: a["since"] for a in aps}
    floor = min(since_by.values())                      # 最早的启用日：比它还早提交的单谁都不接
    prev = state.get("sinceBy")
    if not isinstance(prev, dict):
        # 升级前的状态只有一个全局起始日：当时名单里的人都按它算（升级这一下不触发整段重列）
        prev = {u: state.get("since") for u in since_by} if state.get("last") else {}
    changed = [u for u, d in since_by.items() if prev.get(u) != d]     # 新启用的、或改了启用日的人
    now = datetime.now(idt.CN_TZ)
    full = bool(changed) or not state.get("last")       # 首扫/有人的启用日变了：从受影响那几位里最早的启用日起整段列
    since = min(since_by[u] for u in changed) if changed else floor
    redo = str(state.pop("rescanFrom", "") or "")       # 清空试运行数据以后：从指定那天起整段重列一遍（各人的启用日照旧管着）
    if redo:
        full, since = True, max(redo, floor)
        floor = since                                   # 这一轮里比它还早提交的不接
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
    fails, new_ids = [], []
    for iid, x in got:
        if not x:
            n_fail += 1
            continue
        ct = _ct(x)
        if ct[:10] and ct[:10] < floor:              # waiting 里留着的、启用日往后挪了的
            waiting.pop(iid, None)
            continue
        owner = None if dead(x) else first_owner(x, aps)
        if dead(x):
            waiting.pop(iid, None)
            skip[iid] = ct
            n_skip += 1
        elif owner:
            r = inv.open_approval_folder(user=inv.SYSTEM_USER, source=SOURCE, inst_id=iid, inst=x)
            if r.get("ok"):
                waiting.pop(iid, None)
                n_new += 1
                if x.get("business_id"):
                    new_ids.append(str(x["business_id"]))
            else:
                n_fail += 1
                fails.append("%s：%s" % (x.get("business_id") or iid, r.get("msg")))
        elif str(x.get("status") or "").upper() == "RUNNING":
            waiting[iid] = ct
            n_wait += 1
        else:                                         # 审完了也没走到（对它已启用的）接入审批人
            waiting.pop(iid, None)
            skip[iid] = ct
            n_skip += 1
    cut = (now - timedelta(days=SKIP_KEEP_DAYS)).strftime("%Y-%m-%d")
    state["skip"] = {k: v for k, v in skip.items() if str(v)[:10] >= cut}
    state["waiting"] = waiting
    state["since"] = floor
    state["sinceBy"] = since_by
    last = {"at": inv.now_s(), "trigger": trigger, "full": full, "from": start.strftime("%Y-%m-%d"),
            "listed": len(ids), "fetched": len(todo), "created": n_new, "waiting": len(waiting), "skipped": n_skip,
            "failed": n_fail, "notes": "；".join(notes)[:400], "fails": fails[:10], "sec": round(time.time() - t0, 1)}
    state["last"] = last
    db.set_setting(STATE_KEY, state, inv.SYSTEM_USER)
    last["autoSubmitted"] = auto_submit_ready()
    last["newIds"] = new_ids[:8]                 # 这一轮新接的钉钉审批编号（数字员工办公室的干活记录用；设置里不存）
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
    e = inv.E()
    rows, total = S.folders_auto_open(e, SOURCE, limit=300)
    views = inv.folder_views(rows)
    for f, v in zip(rows, views):
        if f.get("status") == "returned":
            v["why"] = "审核退回：" + (f.get("review_note") or "看退回意见")
        else:
            v["why"] = ready_check(f, S.folder_items(e, f["id"]))[1]
    return {"ok": True, "rows": views, "total": total}


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

TICK_S = 120                     # 每 2 分钟看一次齐全的票夹（识别完就尽快进审核）；整轮扫钉钉仍 20 分钟一次


def _loop():
    time.sleep(90)                     # 开机先让别的线程起来
    n = 0
    while True:
        try:
            import worker_store      # 数字员工办公室·发票接收员：每圈报到；真接了单 / 出了错才记一笔（只写张数和钉钉单号）
            icfg = inv.get_settings().get("intake") or {}
            has_cfg = bool(enabled_approvers(icfg))
            per = EVERY_MIN * 60 // TICK_S           # 每 per 圈整扫一次钉钉；报到时顺带说离下一次整扫还有多久
            off = "" if has_cfg else ("接入审批人都还没填启用日" if icfg.get("approvers") else "还没设接入审批人")
            worker_store.beat("inv_intake", off=off, next_in=(per - n % per) * TICK_S)
            if n % per == 0:
                ids2 = []
                rc = recheck_flows(ids2)              # 还欠票的单子：再去钉钉流程里找新发票（不用设接入审批人也看）
                if rc.get("linked"):
                    worker_store.record("inv_intake", n=rc["linked"], refs=ids2,
                                        summary="钉钉流程里后到的发票 %d 张，已记到后补单、进审核" % rc["linked"])
                try:                                   # 后补票「暂估转待认证」的定时兜底（排队漏了的、开关刚切到真做的）
                    from routers import invoice_later_voucher as L3
                    L3.run_due()
                except Exception:
                    _STATE["lastError"] = traceback.format_exc()[-2000:]
                if has_cfg:
                    r = scan_once("定时") or {}
                    if not r.get("ok") and "还在扫" not in str(r.get("msg") or ""):
                        worker_store.record("inv_intake", ok=False, summary="扫钉钉单出错", error=str(r.get("msg") or ""))
                    elif r.get("created") or r.get("failed"):
                        worker_store.record("inv_intake", n=r.get("created") or 0, ok=not r.get("failed"),
                                            summary="已接入 %d 张钉钉单" % (r.get("created") or 0), refs=r.get("newIds"),
                                            error=("%d 张没接成：%s" % (r["failed"], r.get("notes") or "")) if r.get("failed") else "")
            else:
                ids = []
                k = auto_submit_ready(ids)
                if k:
                    worker_store.record("inv_intake", summary="已送审 %d 个票夹（票齐了）" % k, refs=ids)
        except Exception:
            _STATE["lastError"] = traceback.format_exc()[-2000:]
        n += 1
        _WAKE.wait(TICK_S)
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
