# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-03 | Author: Claude Opus 5.5 | Version: V2.785
# Description: 【BP 工作台读物流账单】内部只读口 GET /api/internal/bp-logistics-lines?period=YYYY-MM
#   BP「STEP5 出库费用归属」原来靠人工上传「销售出库单（含运费）」和装货表，现在改成读这里已经进了复核台的账单。
#   · 只读、不取金蝶、不分页：一次给出该**账单月**全部逐单明细行（grain=detail，含没写单号的）。
#     —— 单据运费页是「出库单所在月 × 账期不限」，BP 要和序时账的计提对，所以按账单月取（7 月发货 8 月结的算 8 月）。
#   · 每行带归口后的费用类型 / 主体（_eff_fee / _eff_subject）和 运费·装卸费·其他 三类拆分（_line_buckets），
#     口径直接复用 routers/logistics_review.py 里页面用的那几个函数，这里不另写一套。
#   · 每家承运商带 是否已登记复核 / 复核人 / 时间 / 税率维表里的税率，BP 自己换不含税。
#   鉴权同 /api/internal/bp-identity：X-Internal-Token + 回环来源（bom_quote.internal_token_ok），登录门由 app.py 的
#   BP_INTERNAL_PREFIX 放行。金额一律是账单上的**含税**数。
from datetime import datetime

from fastapi import APIRouter, Request
from sqlalchemy import select

from core import JSONResponse, db
from kernels import logistics_review_store as store
from routers import bom_quote
from routers import logistics_review as lrv

router = APIRouter()
BL, SG = store.bill_lines, store.review_sign


def build_feed(period, bl_rows, signs, suppliers, tax_rows, eff_subject, eff_fee, line_buckets):
    """账单明细行 → 给 BP 的结构。纯函数（不碰库），口径函数由调用方传进来。"""
    signed = {s["carrier"]: s for s in signs if s.get("status") == "signed"}
    full_of = {x.get("short"): (x.get("full") or "") for x in suppliers}
    tax_of = {}
    for t in tax_rows:
        try:
            tax_of.setdefault(t.get("supplier") or "", {})[t.get("fee_type") or ""] = float(t.get("rate") or 0)
        except (TypeError, ValueError):
            continue
    lines, cars = [], {}
    for r in bl_rows:
        car = r.get("carrier") or ""
        amt = float(r.get("amount") or 0)
        bk = line_buckets(amt, r.get("fee_item"), r.get("sub_fees"))
        nos = [p for p in str(r.get("doc_no") or "").split("+") if p and p != "无单据"]
        mode = r.get("review_mode") or "audit"
        ok = mode == "register" or car in signed
        lines.append({"id": r.get("id"), "carrier": car, "subject": eff_subject(r), "feeType": eff_fee(r),
                      "feeItem": r.get("fee_item") or "", "annot": r.get("annot") or "", "docNos": nos,
                      "amount": round(amt, 4), "tr": round(bk["tr"], 4), "ld": round(bk["ld"], 4), "ot": round(bk["ot"], 4),
                      "reviewed": ok, "reviewMode": mode, "billSrc": r.get("bill_src") or "",
                      "srcSheet": r.get("src_sheet") or "", "srcRow": r.get("src_row")})
        c = cars.setdefault(car, {"carrier": car, "full": full_of.get(car, ""), "reviewModes": set(), "lines": 0,
                                  "amount": 0.0, "tr": 0.0, "ld": 0.0, "ot": 0.0})
        c["reviewModes"].add(mode)
        c["lines"] += 1
        c["amount"] += amt
        for k in ("tr", "ld", "ot"):
            c[k] += bk[k]
    out = []
    for car, c in sorted(cars.items()):
        sg = signed.get(car) or {}
        modes = c.pop("reviewModes")
        out.append({**c, "amount": round(c["amount"], 2), "tr": round(c["tr"], 2), "ld": round(c["ld"], 2), "ot": round(c["ot"], 2),
                    "reviewMode": "register" if modes == {"register"} else "audit",
                    "signed": bool(sg), "signedBy": sg.get("reviewer") or "", "signedAt": sg.get("signed_at") or "",
                    "taxRates": tax_of.get(c["full"], {})})
    return {"ok": True, "period": period, "stamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "carriers": out, "lines": lines}


@router.get("/api/internal/bp-logistics-lines")
def bp_logistics_lines(request: Request, period: str = ""):
    if not bom_quote.internal_token_ok(request):
        return JSONResponse({"ok": False, "msg": "仅限内部调用"}, status_code=401)
    if not period or len(period) != 7:
        return JSONResponse({"ok": False, "msg": "缺账期（YYYY-MM）"}, status_code=400)
    with db._engine.connect() as c:
        bl = [dict(r) for r in c.execute(select(BL).where((BL.c.period == period) & (BL.c.grain == "detail"))
                                         .order_by(BL.c.carrier, BL.c.id)).mappings().all()]
        signs = [dict(r) for r in c.execute(select(SG).where(SG.c.period == period)).mappings().all()]
    return build_feed(period, bl, signs, db.list_logi_suppliers() or [], db.list_tax_rates() or [],
                      lrv._eff_subject, lrv._eff_fee, lrv._line_buckets)
