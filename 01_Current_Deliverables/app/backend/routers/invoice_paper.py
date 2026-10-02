# [Change Log] Date: 2026-10-02 | Author: Claude Opus 5.5 | Version: V2.740（发票管家·月末纸质件查验）
# Description: Owner 2026-10-02 定：纸质件不卡审核，月末一次性查验哪些收到了、哪些没收到；「这个月」按**审核通过的月份**。
#   某月审核通过的发票/收据（未作废、未移除）列成清单，标纸质件到没到；扫发票二维码＝按号码在全部票里找到这张、记纸质件已到
#   （不用先打开票夹）；没二维码的（收据、老式票）手工勾；导出未到清单去催。纸质件状态就是 inv_item.paper，和收票台扫码同一个字段。
import re
from datetime import datetime

from fastapi import APIRouter, Request
from sqlalchemy import select, and_
from starlette.concurrency import run_in_threadpool

from kernels import invoice_parse as ip
from kernels import invoice_store as S
from kernels import invoice_excel as X
from routers import invoice as inv

router = APIRouter()
BILL_KINDS = ("invoice", "receipt")
EXPORT_COLUMNS = ["审核月份", "审批编号", "单据", "申请人", "收款方/事由", "票种", "发票代码", "发票号码", "开票日期",
                  "销方名称", "价税合计", "审核人", "审核时间", "纸质件"]


def _month_ok(m):
    return bool(re.fullmatch(r"\d{4}-\d{2}", str(m or "")))


def _rows(month):
    """某月审核通过的发票/收据 + 所在票夹摘要 → [dict]（按审核时间排）。"""
    I, F = S.ITEM.c, S.FOLDER.c
    stmt = (select(S.ITEM, F.business_id.label("f_bid"), F.title.label("f_title"), F.applicant.label("f_app"),
                   F.template.label("f_tpl"), F.payee_name.label("f_payee"))
            .select_from(S.ITEM.outerjoin(S.FOLDER, F.id == I.folder_id))
            .where(and_(I.kind.in_(BILL_KINDS), I.review == "approved", I.review_at.like(month + "%"),
                        S._item_active()))
            .order_by(I.review_at, I.id))
    with inv.E().connect() as c:
        rows = [dict(r._mapping) for r in c.execute(stmt)]
    return [_view(r) for r in rows]


def _type_label(r):
    if r.get("kind") != "invoice":
        return X.KIND_LABELS.get(r.get("kind"), r.get("kind") or "")
    return r.get("type_label") or X.INV_TYPE_LABELS.get(r.get("inv_type") or "", r.get("inv_type") or "")


def _view(r):
    return {"id": r["id"], "folderId": r.get("folder_id"), "businessId": r.get("f_bid") or "", "title": r.get("f_title") or "",
            "applicant": r.get("f_app") or "", "template": r.get("f_tpl") or "", "payee": r.get("f_payee") or "",
            "kind": r.get("kind"), "typeLabel": _type_label(r), "code": r.get("code") or "", "number": r.get("number") or "",
            "date": r.get("issue_date") or "", "sellerName": r.get("seller_name") or "",
            "total": inv._money_val(r.get("total")), "reviewBy": r.get("review_by") or "", "reviewAt": r.get("review_at") or "",
            "paper": bool(r.get("paper"))}


def _list(month):
    rows = _rows(month)
    got = sum(1 for r in rows if r["paper"])
    return {"ok": True, "month": month, "rows": rows, "total": len(rows), "arrived": got, "missing": len(rows) - got}


def _find(code, number):
    """按号码找有效的发票/收据（没作废、没移除）；老版票号码只有 8 位，带代码的再比代码。"""
    I = S.ITEM.c
    with inv.E().connect() as c:
        rows = [S._row(r) for r in c.execute(select(S.ITEM).where(and_(
            I.number == number, I.kind.in_(BILL_KINDS), S._item_active(), S._not_void())))]
    if code:
        rows = [r for r in rows if not r.get("code") or r.get("code") == code] or rows
    return rows


def _mark(u, it, paper, how):
    S.item_update(inv.E(), it["id"], paper=1 if paper else 0)
    inv.log(u, ("纸质件到件" if paper else "撤销纸质件到件") + "（月末查验）", it.get("folder_id"), it["id"],
            detail={"number": it.get("number"), "via": how})


def _scan(u, text):
    c = ip.classify_code(text or "")
    if c["kind"] != "invoice_qr":
        if c["kind"] in ("approval_link", "business_id"):
            return {"ok": True, "action": "notInvoice", "msg": "这是审批单的码：月末查验请扫发票上的二维码"}
        return {"ok": True, "action": "notInvoice", "msg": "这不是发票二维码"}
    q = ip.parse_invoice_qr(c["value"]) or ip.parse_invoice_qr(text)
    if not q or not q.get("number"):
        return {"ok": True, "action": "notInvoice", "msg": "二维码读不出发票号码"}
    hits = _find(q.get("code") or "", q["number"])
    if not hits:
        return {"ok": True, "action": "notFound", "number": q["number"],
                "msg": "发票管家里没有这张票（号码 %s）：还没登记，到收票台打开对应的单子再扫" % q["number"]}
    it = hits[0]
    v = _view({**it, "f_bid": "", "f_title": ""})
    f = S.folder_get(inv.E(), it.get("folder_id")) if it.get("folder_id") else None
    v.update(title=(f or {}).get("title") or "", applicant=(f or {}).get("applicant") or "")
    where = "（%s）" % v["title"] if v["title"] else ""
    month = (it.get("review_at") or "")[:7] if it.get("review") == "approved" else ""
    tail = "；这张是 %s 审核通过的" % month if month else ("；这张还没审核通过" if it.get("review") != "approved" else "")
    if it.get("paper"):
        return {"ok": True, "action": "already", "item": v, "month": month,
                "msg": "这张早就记过纸质件已到：%s%s%s" % (q["number"], where, tail)}
    _mark(u, it, True, "扫码")
    v["paper"] = True
    return {"ok": True, "action": "confirm", "item": v, "month": month,
            "msg": "纸质件已到：%s%s%s" % (q["number"], where, tail)}


def _export(month, only):
    rows = _rows(month)
    if only == "missing":
        rows = [r for r in rows if not r["paper"]]
    wb = X._new_wb()
    sw = X._SheetWriter(wb, "纸质件%s%s" % (month, "未到" if only == "missing" else ""), EXPORT_COLUMNS,
                        money=("价税合计",), text=("审批编号", "发票代码", "发票号码"),
                        widths={"审批编号": 22, "收款方/事由": 30, "发票号码": 24, "销方名称": 32, "审核时间": 19})
    for r in rows:
        sw.add([month, r["businessId"], r["template"], r["applicant"], r["payee"] or r["title"], r["typeLabel"], r["code"],
                r["number"], r["date"], r["sellerName"], r["total"], r["reviewBy"], r["reviewAt"], "已到" if r["paper"] else "未到"])
    sw.finish()
    return X._save(wb), len(rows)


# ───────────────────────── 接口 ─────────────────────────

@router.get("/api/inv/paper")
async def paper_list(request: Request):
    u, bad = inv.need(request, inv.ENTER_DESK)
    if bad:
        return bad
    m = request.query_params.get("month") or datetime.now().strftime("%Y-%m")
    if not _month_ok(m):
        return inv.err("月份写成 2026-09 这样", 400)
    return await run_in_threadpool(_list, m)


@router.post("/api/inv/paper/scan")
async def paper_scan(request: Request):
    u, bad = inv.need(request, inv.ENTER_DESK, inv.CAP_INTAKE)
    if bad:
        return bad
    b = await inv.body_json(request)
    return await run_in_threadpool(_scan, u, str(b.get("code") or "")[:2000])


@router.post("/api/inv/paper/mark")
async def paper_mark(request: Request):
    """没二维码的（收据、老式纸票）手工勾「纸质件已到」，勾错了可撤。"""
    u, bad = inv.need_any(request, [(inv.ENTER_DESK, inv.CAP_INTAKE), (inv.ENTER_AUDIT, inv.CAP_AUDIT)])
    if bad:
        return bad
    b = await inv.body_json(request)
    try:
        iid = int(b.get("itemId"))
    except (TypeError, ValueError):
        return inv.err("缺票据 id", 400)
    it = S.item_get(inv.E(), iid)
    if not it or it.get("status") == "removed" or it.get("kind") not in BILL_KINDS:
        return inv.err("票不存在（或已被移除）", 404)
    paper = bool(b.get("paper"))
    await run_in_threadpool(_mark, u, it, paper, "手工勾")
    return {"ok": True, "itemId": iid, "paper": paper}


@router.get("/api/inv/paper/export")
async def paper_export(request: Request):
    u, bad = inv.need(request, inv.ENTER_DESK)
    if bad:
        return bad
    m = request.query_params.get("month") or ""
    if not _month_ok(m):
        return inv.err("月份写成 2026-09 这样", 400)
    only = "missing" if request.query_params.get("only") == "missing" else "all"
    data, n = await run_in_threadpool(_export, m, only)
    inv.audit(u, "导出纸质件查验", m, "%d 行（%s）" % (n, "只导未到" if only == "missing" else "全部"))
    from routers.invoice_books import _xlsx
    name = "纸质件%s_%s.xlsx" % ("未到清单" if only == "missing" else "查验", m)
    return _xlsx(data, name, "paper_%s_%s.xlsx" % (only, m))
