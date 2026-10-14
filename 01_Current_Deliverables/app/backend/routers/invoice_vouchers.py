# [Change Log] Date: 2026-10-02 | Author: Codex | Version: V2.746
# Description: 发票号在金蝶摘要精确匹配，核对账簿，主动/20分钟同步；结果变化才落库。
# [Change Log] Date: 2026-10-09 | Author: Claude Opus 5.5 | Version: V2.877
# Description: 定时同步省金蝶调用(每天上限 50,000 次)。原来每 20 分钟把每张在途发票各查一次金蝶(35 张票一天约 1,900 次，随发票张数线性涨，
#   500 张就是 3.6 万次)。改成：①每天一次全量——已对上的按它记下的凭证号成批核实(几次调用)，还没对上的才逐张查；
#   ②夜里 22 点到 7 点不跑；③白天每轮只问金蝶「上一轮以来新建/改过哪些凭证」(1～2 次调用，和发票张数无关)，拿回来在本地和发票号比；
#   ④这种查法哪天不行了，退回逐张查「还没对上的」，一小时最多一次。审核页上人点的「刷新」(sync_once)一个字没动，照旧立即逐张查。
# [Change Log] Date: 2026-10-13 | Author: Claude / c | Version: V2.946（发票管家·扫票报凭证号）
# Description: 用户定：扫一下纸质发票，系统报出它在金蝶哪张凭证里（纸质发票是原始凭证，贴票/装订时用）；入口放在「其它 › 凭证装订」的扫码里
#   （routers/logistics_voucher._scan_invoice 调这里的 _scan ＋ bind_view），发票台账不放。POST /api/inv/vouchers/scan {code} 留作接口：
#   发票二维码或直接敲发票号码 → 先看发票管家里已经对上的凭证；没对上、或这张票压根没在发票管家登记过，拿发票号去金蝶摘要里现查一次
#   （你们的凭证摘要以发票号开头）。回凭证号和一句给页面念出来的话（say）。只查不写金蝶；查到新对上的顺手记到那张票上。
# [Change Log] Date: 2026-10-14 | Author: Claude Opus 5.5 | Version: V2.966（发票管家·刷新金蝶凭证提速）
# Description: 用户 2026-10-14 嫌「刷新全部金蝶凭证」太久（30 张票跑了 249 秒：一张票一次「摘要里包含号码」的查询，金蝶这种查法慢，一张 8 秒多），
#   中途再点一下还被报成「台账没读出来」。改：①「刷新全部」放到后台跑，页面看进度（查了几张/一共几张），查到一张那张就落库，
#   再点只告诉进度不报错；②已经对上凭证的票按记下的凭证号成批核实（几次调用），只有没对上的才逐张搜（和每天早上的全量一个做法）；
#   ③定时那一轮顺手把「从没查过」的票查掉（每轮最多 NEW_PER_ROUND 张），新进台账的票不用等到第二天早上。单张的「刷新」照旧当场查。
import re
import threading
from datetime import datetime, timedelta
from fastapi import APIRouter, Request
from sqlalchemy import select, update, or_
from starlette.concurrency import run_in_threadpool
from routers import invoice as inv
from kernels import invoice_parse as ip
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
    cond = [S._item_active(), S.ITEM.c.review.in_(("pending", "approved")), S.ITEM.c.kind == "invoice",
            or_(S.ITEM.c.inv_type.is_(None), S.ITEM.c.inv_type != "normal")]      # 普票不入账（inv.no_book），不去金蝶找
    if item_id is not None:
        cond.append(S.ITEM.c.id == item_id)
    with inv.E().connect() as cx:
        return [S._row(r) for r in cx.execute(select(S.ITEM).where(*cond))]


def _numbers(items):
    return sorted({str(i.get("number") or "").strip() for i in items if re.fullmatch(r"\d{8,20}", str(i.get("number") or "").strip())})


# ── 人点的「刷新全部」：后台跑、带进度 ──
_JOB = {"running": False, "total": 0, "checked": 0, "verified": 0, "queried": 0, "toQuery": 0, "matched": 0, "changed": 0,
        "by": "", "startedAt": "", "finishedAt": "", "error": ""}
NEW_PER_ROUND = 10                   # 定时每一轮最多顺手查几张「从没查过」的票（一张一次查询，免得一轮拖太久、吃额度）


def job_status():
    return dict(_JOB)


def _run_job():
    """后台线程里跑：先成批核实已对上的，再一张张查没对上的；每查完一张就落库、更新进度。出错停下，已查的照样算数。"""
    try:
        items = _active_items()
        kd = [i for i in items if _bk(i).get("status") == "booked" and _bk(i).get("source") == "kingdee" and _bk(i).get("vouchers")]
        rows_v = fetch_by_vouchers([v for i in kd for v in _bk(i)["vouchers"]]) if kd else []
        still = [i for i in kd if match_vouchers(i, rows_v)]
        ch, mt = _sync_items(still, rows_v)
        _JOB.update(verified=len(still), checked=len(still), matched=mt, changed=ch)
        rest = [i for i in items if i["id"] not in {x["id"] for x in still}]
        by_no = {}
        for i in rest:
            n = str(i.get("number") or "").strip()
            if re.fullmatch(r"\d{8,20}", n):
                by_no.setdefault(n, []).append(i)
        _JOB.update(toQuery=len(by_no), total=len(still) + len(rest))
        for no, its in sorted(by_no.items()):
            rows = fetch_rows([no])                      # 一个号码一次查询（金蝶摘要模糊查，一次一个才不超时）
            ch, mt = _sync_items(its, rows)
            _JOB["queried"] += 1
            _JOB["checked"] += len(its)
            _JOB["matched"] += mt
            _JOB["changed"] += ch
        _JOB["checked"] = _JOB["total"]                  # 没有号码的票查不了，也算过了
    except Exception as e:
        _JOB["error"] = "金蝶查询出错，已查的照样记下了：%s" % str(e)[:160]
    finally:
        _JOB.update(running=False, finishedAt=inv.now_s())
        _LOCK.release()


def start_job(user):
    """开始一轮「刷新全部」→ (状态, 中文原因或 '')。已经在跑 → 回进度；定时同步正占着 → 让人等一下。"""
    if _JOB["running"]:
        return job_status(), ""
    if not _LOCK.acquire(blocking=False):
        return job_status(), "系统自己的定时同步正在查金蝶，一两分钟后再点"
    _JOB.update(running=True, total=0, checked=0, verified=0, queried=0, toQuery=0, matched=0, changed=0,
                by=inv._uname(user), startedAt=inv.now_s(), finishedAt="", error="")
    threading.Thread(target=_run_job, name="inv-vouchers-refresh", daemon=True).start()
    return job_status(), ""


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
        # 新进台账、从没查过的票（清零重接、刚审核过的……）：只问「最近改过的凭证」问不到它们挂的老凭证，这里顺手逐张查掉，每轮有个上限
        fresh = [i for i in items if not _bk(i)][:NEW_PER_ROUND]
        nos = _numbers(fresh)
        if nos:
            c2, m2 = _sync_items(fresh, fetch_rows(nos))
            changed, matched = changed + c2, matched + m2
        return {"ok": True, "how": "inc", "total": len(items), "matched": matched, "changed": changed, "rows": len(rows), "fresh": len(nos)}
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
    if iid is None:                  # 刷新全部：后台跑，页面拿进度（GET /api/inv/vouchers/refresh）
        st, why = start_job(u)
        return {"ok": not why, "msg": why, "job": st}
    try:
        result = await run_in_threadpool(sync_once, iid)
    except Exception:
        return inv.err("金蝶凭证查询失败，原有记录已保留，请稍后重试", 502)
    if iid is not None:
        result["item"] = inv.item_view(S.item_get(inv.E(), iid))
    return result

@router.get("/api/inv/vouchers/refresh")
async def refresh_status(request: Request):
    """「刷新全部」的进度：running、total、checked、matched、changed、startedAt、finishedAt、by、error。"""
    u, bad = inv.need_any(request, [(inv.ENTER_LEDGER, None), (inv.ENTER_AUDIT, None)])
    if bad:
        return bad
    return {"ok": True, "job": job_status()}

REVIEW_CN = {"draft": "还在收票、没送审", "pending": "已送审、还没审核", "approved": "已审核", "returned": "被退回了", "void": "已作废"}


def _loose_vouchers(number, rows):
    """发票管家里没登记过这张票（不知道购方是谁）时：摘要里有这个发票号的凭证都算，不比账簿。"""
    pattern = re.compile(r"(?<!\d)" + re.escape(number) + r"(?!\d)")
    out = []
    for row in rows:
        if not pattern.search(str(row.get("summary") or "")):
            continue
        try:
            period = "%04d-%02d" % (int(row.get("year")), int(row.get("month")))
        except (ValueError, TypeError):
            continue
        no = str(row.get("number") or "")
        v = {"book": str(row.get("book") or ""), "period": period, "number": str(row.get("group") or "") + "-" + no}
        if no and v not in out:
            out.append(v)
    return sorted(out, key=lambda v: (v["book"], v["period"], v["number"]))


def _say(vouchers):
    """给页面念出来的话：「9 月，记 230 号」。几张凭证都念；一张没有就说没找到。"""
    def one(v):
        m = re.fullmatch(r"\d{4}-(\d{2})", v.get("period") or "")
        g, _, n = (v.get("number") or "").partition("-")
        return "%s%s %s 号" % ("%d 月，" % int(m.group(1)) if m else "", g or "记", n)
    if not vouchers:
        return "没找到凭证"
    if len(vouchers) == 1:
        return one(vouchers[0])
    return "有 %d 张凭证：" % len(vouchers) + "；".join(one(v) for v in vouchers[:4])


def _scan(text):
    """扫发票查凭证号 → 响应 dict。action：found 找到了 / none 没找到 / notInvoice 扫的不是发票。"""
    raw = str(text or "").strip()
    c = ip.classify_code(raw)
    number = code = ""
    if c["kind"] == "invoice_qr":
        q = ip.parse_invoice_qr(c["value"]) or ip.parse_invoice_qr(raw) or {}
        number, code = str(q.get("number") or ""), str(q.get("code") or "")
        if not number:
            return {"ok": True, "action": "notInvoice", "say": "读不出发票号码", "msg": "二维码读不出发票号码，可以直接敲发票号码再按回车"}
    elif c["kind"] in ("approval_link", "business_id"):
        return {"ok": True, "action": "notInvoice", "say": "这是审批单", "msg": "这是审批单的码：请扫发票上的二维码"}
    else:
        digits = re.sub(r"\s+", "", raw)
        if not re.fullmatch(r"\d{8,20}", digits):
            return {"ok": True, "action": "notInvoice", "say": "这不是发票", "msg": "这不是发票二维码；也可以直接敲发票号码（8～20 位数字）再按回车"}
        number = digits
    I = S.ITEM.c
    with inv.E().connect() as cx:
        items = [S._row(r) for r in cx.execute(select(S.ITEM).where(
            I.number == number, I.kind == "invoice", S._item_active(), S._not_void()).order_by(I.id))]
    if code:
        items = [i for i in items if not i.get("code") or i.get("code") == code] or items
    vouchers, note = [], ""
    for it in items:
        for v in (_bk(it).get("vouchers") or []):
            if v not in vouchers:
                vouchers.append(v)
    if not vouchers:               # 发票管家里还没对上（或没登记过这张票）：去金蝶现查一次
        try:
            rows = fetch_rows([number])
            if items:
                _sync_items(items, rows)
                for it in items:
                    for v in match_vouchers(it, rows):
                        if v not in vouchers:
                            vouchers.append(v)
            if not vouchers:
                vouchers = _loose_vouchers(number, rows)
        except Exception as ex:
            note = "金蝶现在查不了（%s），只能告诉你发票管家里记着的" % str(ex)[:80]
    out = {"ok": True, "number": number, "inLedger": bool(items), "vouchers": vouchers, "say": _say(vouchers)}
    if items:
        it = items[0]
        f = S.folder_get(inv.E(), it.get("folder_id")) if it.get("folder_id") else None
        out["item"] = {"id": it["id"], "sellerName": it.get("seller_name") or "", "buyerName": it.get("buyer_name") or "",
                       "total": it.get("total"), "tax": it.get("tax"), "date": it.get("issue_date") or "",
                       "review": it.get("review") or "", "reviewText": REVIEW_CN.get(it.get("review") or "", ""),
                       "paper": bool(it.get("paper")), "folderTitle": (f or {}).get("title") or "",
                       "businessId": (f or {}).get("business_id") or "", "applicant": (f or {}).get("applicant") or "",
                       "noBook": inv.no_book(it)}
    if vouchers:
        out["action"] = "found"
        out["msg"] = "、".join("%s %s %s" % (v["book"], v["period"], v["number"]) for v in vouchers)
    else:
        out["action"] = "none"
        why = ("这是普票，不入账、不认证：金蝶里不会有摘要带这个发票号的凭证" if items and out["item"]["noBook"]
               else "发票管家里有这张票（%s），但金蝶里还没有摘要带这个发票号的凭证：可能还没做账" % out["item"]["reviewText"] if items
               else "发票管家里没有登记这张票，金蝶里也没有摘要带这个发票号的凭证")
        out["msg"] = note or why
    if note and vouchers:
        out["msg"] += "；" + note
    return out


def bind_view(r, short_of=None):
    """把 _scan 的结果换成「凭证装订」扫码页认的样子（和扫付款单出来的是同一套字段：主体简称、凭证号、凭证月份）。
    short_of＝{账簿全称: 主体简称}。扫的不是发票 → ok False、msg 说明。"""
    if r.get("action") == "notInvoice" or not r.get("number"):
        return {"ok": False, "msg": r.get("msg") or "这不是发票二维码"}
    it = r.get("item") or {}
    vs = [{"subject": (short_of or {}).get(v.get("book")) or v.get("book") or "", "month": str(v.get("period") or "")[:7],
           "vno": re.sub(r"^记-?", "", str(v.get("number") or "")), "what": "这张发票所在的凭证", "src": "金蝶已有"}
          for v in r.get("vouchers") or []]
    return {"ok": True, "is_invoice": True, "inst": "发票:" + r["number"], "kind": "发票", "subject": vs[0]["subject"] if vs else "",
            "payee": it.get("sellerName") or "", "amount": it.get("total"), "bid": it.get("businessId") or "", "vouchers": vs,
            "state": "" if vs else ("普票不入账" if it.get("noBook") else "这张发票还没做账"), "msg": r.get("msg") or "",
            "invoice": {"number": r["number"], "inLedger": bool(r.get("inLedger")), "sellerName": it.get("sellerName") or "",
                        "total": it.get("total"), "tax": it.get("tax"), "reviewText": it.get("reviewText") or "",
                        "paper": bool(it.get("paper")), "folderTitle": it.get("folderTitle") or ""}}


@router.post("/api/inv/vouchers/scan")
async def voucher_scan(request: Request):
    """扫发票，报凭证号。body：{code: 发票二维码原文 或 发票号码}。发票管家任一页面的人都能用（只查不写金蝶）。"""
    u, bad = inv.need(request, None)
    if bad:
        return bad
    body = await inv.body_json(request)
    return await run_in_threadpool(_scan, str(body.get("code") or "")[:2000])


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
