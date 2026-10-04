# [Change Log] Date: 2026-10-02 | Author: Codex | Version: V2.746
# Description: 发票号在金蝶摘要精确匹配，核对账簿，主动/20分钟同步；结果变化才落库。
import re
import threading
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

def sync_once(item_id=None):
    if not _LOCK.acquire(blocking=False):
        return {"ok": False, "msg": "已有凭证刷新在运行，请稍后重试"}
    try:
        cond = [S._item_active(), S.ITEM.c.review.in_(("pending", "approved")), S.ITEM.c.kind == "invoice"]
        if item_id is not None:
            cond.append(S.ITEM.c.id == item_id)
        with inv.E().connect() as cx:
            items = [S._row(r) for r in cx.execute(select(S.ITEM).where(*cond))]
        numbers = sorted({str(i.get("number") or "").strip() for i in items if re.fullmatch(r"\d{8,20}", str(i.get("number") or "").strip())})
        rows = fetch_rows(numbers) if numbers else []  # 任一查询失败即中止，旧记录不变。
        changed = matched = 0
        for item in items:
            vouchers = match_vouchers(item, rows)
            matched += bool(vouchers)
            status = "booked" if vouchers else "unknown"
            with inv.E().begin() as cx:
                current = S._row(cx.execute(select(S.ITEM).where(S.ITEM.c.id == item["id"]).with_for_update()).first())
                if not current or current.get("review") not in ("pending", "approved") or current.get("status") == "removed":
                    continue
                flags = dict(current.get("flags_json") or {})
                old = flags.get("_bookkeeping") or {}
                if (old.get("status", "unknown"), old.get("vouchers", []), old.get("source")) == (status, vouchers, "kingdee"):
                    continue
                new = {"status": status, "vouchers": vouchers, "source": "kingdee", "by": "金蝶同步", "at": inv.now_s()}
                flags["_bookkeeping"] = new
                cx.execute(update(S.ITEM).where(S.ITEM.c.id == item["id"]).values(flags_json=S._dumps(flags), updated_at=inv.now_s()))
            inv.log(inv.SYSTEM_USER, "刷新发票凭证", folder_id=item.get("folder_id"), item_id=item["id"], detail={"before": old, "after": new})
            changed += 1
        return {"ok": True, "total": len(items), "matched": matched, "changed": changed}
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
        worker_store.beat("inv_voucher")
        while True:
            threading.Event().wait(20 * 60)
            try:
                r = sync_once() or {}
                worker_store.beat("inv_voucher")
                if r.get("changed"):
                    worker_store.record("inv_voucher", n=r["changed"], summary="已对上 %d 张发票的金蝶凭证" % r["changed"])
            except Exception as e:
                import logging
                logging.getLogger(__name__).exception("发票凭证定时刷新失败，保留旧记录")
                worker_store.beat("inv_voucher")
                worker_store.record("inv_voucher", ok=False, summary="回查金蝶凭证出错", error=str(e))
    threading.Thread(target=loop, name="inv-vouchers", daemon=True).start()

start_timer()
