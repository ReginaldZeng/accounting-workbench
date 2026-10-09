# [Change Log] Date: 2026-10-02 | Author: Codex | Version: V2.746
# Description: 发票号在金蝶摘要精确匹配，核对账簿，主动/20分钟同步；结果变化才落库。
# [Change Log] Date: 2026-10-09 | Author: Claude Opus 5.5 | Version: V2.877
# Description: 定时同步省金蝶调用(每天上限 50,000 次)。原来每 20 分钟把每张在途发票各查一次金蝶(35 张票一天约 1,900 次，随发票张数线性涨，
#   500 张就是 3.6 万次)。改成：①每天一次全量——已对上的按它记下的凭证号成批核实(几次调用)，还没对上的才逐张查；
#   ②夜里 22 点到 7 点不跑；③白天每轮只问金蝶「上一轮以来新建/改过哪些凭证」(1～2 次调用，和发票张数无关)，拿回来在本地和发票号比；
#   ④这种查法哪天不行了，退回逐张查「还没对上的」，一小时最多一次。审核页上人点的「刷新」(sync_once)一个字没动，照旧立即逐张查。
import re
import threading
from datetime import datetime, timedelta
from fastapi import APIRouter, Request
from sqlalchemy import select, update
from starlette.concurrency import run_in_threadpool
from routers import invoice as inv
from kernels import invoice_store as S

router = APIRouter()
_LOCK = threading.Lock()

def match_vouchers(item, rows):
    number = str(item.get("number") or "").strip()
    buyer = str(item.get("buyer_name") or "").strip()
    if not re.fullmatch(r"\d{8,20}", number) or not buyer:
        return []
    pattern = re.compile(r"(?<!\d)" + re.escape(number) + r"(?!\d)")
    out = []
    for row in rows:
        book = str(row.get("book") or "")
        if buyer not in book or not pattern.search(str(row.get("summary") or "")):
            continue
        year, month = str(row.get("year") or ""), str(row.get("month") or "")
        try:
            period = "%04d-%02d" % (int(year), int(month))
            if not 1 <= int(month) <= 12:
                continue
        except (ValueError, TypeError):
            continue
        no = str(row.get("number") or "")
        if not no:
            continue
        v = {"book": book, "period": period, "number": str(row.get("group") or "") + "-" + no}
        if v not in out:
            out.append(v)
    return sorted(out, key=lambda v: (v["book"], v["period"], v["number"]))

def fetch_rows(numbers):
    import kingdee_client as kc
    s, conf = kc.login()
    fields = [("FAccountBookID.FName", "book"), ("FYear", "year"), ("FPeriod", "month"),
              ("FVOUCHERGROUPID.FName", "group"), ("FVOUCHERGROUPNO", "number"), ("FEXPLANATION", "summary")]
    rows = []
    # ponytail: 单号查询避免金蝶多条件摘要扫描超时；发票量大时改接有索引的关联字段。
    for number in numbers:
        filt = "FEXPLANATION like '%%%s%%'" % number
        rows.extend(kc._query(s, conf, "GL_VOUCHER", fields, "(" + filt + ")", "FAccountBookID,FYear,FPeriod,FVOUCHERGROUPNO,FEntity_FEntrySeq"))
    return rows

def _sync_items(items, rows, merge=False):
    """按取回来的凭证分录给每张发票定「对上了哪几张凭证」，有变化才落库。→ (changed, matched)。
    merge=False(逐张查过、结果是全的)：对上就标已做账、对不上就标未知——和原来 sync_once 一样；
    merge=True(只拿到「最近改过的凭证」，不是全的)：只往上加——对上了新凭证就并进去，没对上的不动，不会把已做账的改回未知。"""
    changed = matched = 0
    for item in items:
        vouchers = match_vouchers(item, rows)
        matched += bool(vouchers)
        if merge and not vouchers:
            continue
        status = "booked" if vouchers else "unknown"
        with inv.E().begin() as cx:
            current = S._row(cx.execute(select(S.ITEM).where(S.ITEM.c.id == item["id"]).with_for_update()).first())
            if not current or current.get("review") not in ("pending", "approved") or current.get("status") == "removed":
                continue
            flags = dict(current.get("flags_json") or {})
            old = flags.get("_bookkeeping") or {}
            if merge and old.get("status") == "booked":
                for v in old.get("vouchers") or []:
                    if v not in vouchers:
                        vouchers = vouchers + [v]
                vouchers = sorted(vouchers, key=lambda v: (v.get("book") or "", v.get("period") or "", v.get("number") or ""))
            if (old.get("status", "unknown"), old.get("vouchers", []), old.get("source")) == (status, vouchers, "kingdee"):
                continue
            new = {"status": status, "vouchers": vouchers, "source": "kingdee", "by": "金蝶同步", "at": inv.now_s()}
            flags["_bookkeeping"] = new
            cx.execute(update(S.ITEM).where(S.ITEM.c.id == item["id"]).values(flags_json=S._dumps(flags), updated_at=inv.now_s()))
        inv.log(inv.SYSTEM_USER, "刷新发票凭证", folder_id=item.get("folder_id"), item_id=item["id"], detail={"before": old, "after": new})
        changed += 1
    return changed, matched


def _active_items(item_id=None):
    cond = [S._item_active(), S.ITEM.c.review.in_(("pending", "approved")), S.ITEM.c.kind == "invoice"]
    if item_id is not None:
        cond.append(S.ITEM.c.id == item_id)
    with inv.E().connect() as cx:
        return [S._row(r) for r in cx.execute(select(S.ITEM).where(*cond))]


def _numbers(items):
    return sorted({str(i.get("number") or "").strip() for i in items if re.fullmatch(r"\d{8,20}", str(i.get("number") or "").strip())})


def sync_once(item_id=None):
    """人点的刷新(单张或全部)：逐张去金蝶查，结果是全的。定时的不走这里，走 timer_round。"""
    if not _LOCK.acquire(blocking=False):
        return {"ok": False, "msg": "已有凭证刷新在运行，请稍后重试"}
    try:
        items = _active_items(item_id)
        numbers = _numbers(items)
        rows = fetch_rows(numbers) if numbers else []  # 任一查询失败即中止，旧记录不变。
        changed, matched = _sync_items(items, rows)
        return {"ok": True, "total": len(items), "matched": matched, "changed": changed}
    finally:
        _LOCK.release()


# ── 定时同步(V2.877)：少调金蝶 ─────────────────────────────────────────────────────────
_VFIELDS = [("FAccountBookID.FName", "book"), ("FYear", "year"), ("FPeriod", "month"),
            ("FVOUCHERGROUPID.FName", "group"), ("FVOUCHERGROUPNO", "number"), ("FEXPLANATION", "summary")]
_VORDER = "FAccountBookID,FYear,FPeriod,FVOUCHERGROUPNO,FEntity_FEntrySeq"
_STATE_KEY = "inv_voucher_sync"      # {last_full: 哪天做过全量, last_inc: 上一轮增量从几点查到的, last_fallback: 上次退回逐张查的时间}
NIGHT_FROM, NIGHT_TO = 22, 7         # 夜里这段不跑(没人做账)
_FALLBACK_GAP_S = 3600


def fetch_modified(since):
    """金蝶里 since 以来新建或改过的凭证分录(真机 2026-10-09 实测 FCreateDate / FModifyDate 都能当条件)。一轮 1 次登录 + 1 次查询(量大时多翻几页)。"""
    import kingdee_client as kc
    s, conf = kc.login()
    return kc._query(s, conf, "GL_VOUCHER", _VFIELDS, "(FModifyDate >= '%s' or FCreateDate >= '%s')" % (since, since), _VORDER)


def fetch_by_vouchers(vouchers):
    """按记下的凭证(账簿、期间、凭证号)成批取分录，用来核实「已对上的」凭证还在不在、摘要里发票号还在不在。一个账簿一个期间 1 次查询。"""
    import kingdee_client as kc
    groups = {}
    for v in vouchers:
        try:
            y, m = int(str(v.get("period"))[:4]), int(str(v.get("period"))[5:7])
            no = str(v.get("number") or "").rsplit("-", 1)[-1].strip()
        except (ValueError, TypeError):
            continue
        if v.get("book") and no:
            groups.setdefault((str(v["book"]), y, m), set()).add(no)
    if not groups:
        return []
    s, conf = kc.login()
    rows = []
    for (book, y, m), nos in sorted(groups.items()):
        nos = sorted(nos)
        for i in range(0, len(nos), 200):
            rows.extend(kc._query(s, conf, "GL_VOUCHER", _VFIELDS, "FAccountBookID.FName='%s' and FYear=%d and FPeriod=%d and FVOUCHERGROUPNO in (%s)" % (
                book.replace("'", ""), y, m, ",".join("'%s'" % n.replace("'", "") for n in nos[i:i + 200])), _VORDER))
    return rows


def _bk(item):
    return (item.get("flags_json") or {}).get("_bookkeeping") or {}


def _full_round(items):
    """每天一次的全量：已对上的(金蝶同步对上的)先按记下的凭证号成批核实，还在就不逐张查了；其余(没对上的、人工标的、核实不到的)才逐张查。"""
    kd = [i for i in items if _bk(i).get("status") == "booked" and _bk(i).get("source") == "kingdee" and _bk(i).get("vouchers")]
    rows_v = fetch_by_vouchers([v for i in kd for v in _bk(i)["vouchers"]]) if kd else []
    still = {i["id"] for i in kd if match_vouchers(i, rows_v)}
    numbers = _numbers([i for i in items if i["id"] not in still])
    rows_n = fetch_rows(numbers) if numbers else []
    changed, matched = _sync_items(items, rows_v + rows_n)
    return {"ok": True, "how": "full", "total": len(items), "matched": matched, "changed": changed, "verified": len(still), "queried": len(numbers)}


def timer_round(now=None):
    """定时的一轮。夜里不跑；当天第一轮做全量；之后每轮只查「上一轮以来新建/改过的凭证」；这种查法失败就退回逐张查没对上的(一小时最多一次)。"""
    from core import db
    now = now or datetime.now()
    if now.hour >= NIGHT_FROM or now.hour < NIGHT_TO:
        return {"ok": True, "how": "night"}
    if not _LOCK.acquire(blocking=False):
        return {"ok": False, "msg": "已有凭证刷新在运行"}
    try:
        st = dict(db.get_setting(_STATE_KEY, None) or {})
        today, stamp = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d %H:%M:%S")
        items = _active_items()
        if not _numbers(items):
            return {"ok": True, "how": "empty", "total": len(items), "changed": 0}
        if st.get("last_full") != today:
            r = _full_round(items)
            st.update(last_full=today, last_inc=stamp)
            db.set_setting(_STATE_KEY, st)
            return r
        try:
            since = datetime.strptime(str(st.get("last_inc") or ""), "%Y-%m-%d %H:%M:%S") - timedelta(minutes=10)     # 往回多看 10 分钟，宁可重复不漏
        except ValueError:
            since = now.replace(hour=0, minute=0, second=0)
        try:
            rows = fetch_modified(since.strftime("%Y-%m-%d %H:%M:%S"))
        except Exception as e:
            last_fb = float(st.get("last_fallback") or 0)
            if now.timestamp() - last_fb < _FALLBACK_GAP_S:
                return {"ok": True, "how": "wait", "err": str(e)[:160]}
            todo = [i for i in items if _bk(i).get("status") != "booked"]
            numbers = _numbers(todo)
            rows = fetch_rows(numbers) if numbers else []
            changed, matched = _sync_items(todo, rows)
            st["last_fallback"] = now.timestamp()
            db.set_setting(_STATE_KEY, st)
            return {"ok": True, "how": "fallback", "total": len(todo), "matched": matched, "changed": changed, "queried": len(numbers), "err": str(e)[:160]}
        changed, matched = _sync_items(items, rows, merge=True)
        st["last_inc"] = stamp
        db.set_setting(_STATE_KEY, st)
        return {"ok": True, "how": "inc", "total": len(items), "matched": matched, "changed": changed, "rows": len(rows)}
    finally:
        _LOCK.release()


@router.post("/api/inv/vouchers/refresh")
async def refresh(request: Request):
    u, bad = inv.need_any(request, [(inv.ENTER_LEDGER, inv.CAP_AUDIT), (inv.ENTER_AUDIT, inv.CAP_AUDIT)])
    if bad:
        return bad
    body = await inv.body_json(request)
    iid = body.get("itemId")
    if iid is not None and (type(iid) is not int or iid <= 0):
        return inv.err("票据编号不正确", 400)
    try:
        result = await run_in_threadpool(sync_once, iid)
    except Exception:
        return inv.err("金蝶凭证查询失败，原有记录已保留，请稍后重试", 502)
    if iid is not None:
        result["item"] = inv.item_view(S.item_get(inv.E(), iid))
    return result

def start_timer():
    from core import db
    if str(db.DB_URL).startswith("sqlite"):
        return
    def loop():
        import worker_store      # 数字员工办公室·发票凭证同步员：每圈报到；真对上了凭证 / 出了错才记一笔（只写张数）
        worker_store.beat("inv_voucher", next_in=20 * 60)
        while True:
            threading.Event().wait(20 * 60)
            try:
                r = timer_round() or {}          # V2.877：定时的走省调用的那条路；人点的刷新仍是 sync_once
                worker_store.beat("inv_voucher", next_in=20 * 60, off=("夜里 %d 点到 %d 点不查金蝶" % (NIGHT_FROM, NIGHT_TO)) if r.get("how") == "night" else "")
                if r.get("changed"):
                    worker_store.record("inv_voucher", n=r["changed"], summary="已对上 %d 张发票的金蝶凭证" % r["changed"])
            except Exception as e:
                import logging
                logging.getLogger(__name__).exception("发票凭证定时刷新失败，保留旧记录")
                worker_store.beat("inv_voucher", next_in=20 * 60)
                worker_store.record("inv_voucher", ok=False, summary="回查金蝶凭证出错", error=str(e))
    threading.Thread(target=loop, name="inv-vouchers", daemon=True).start()

start_timer()
