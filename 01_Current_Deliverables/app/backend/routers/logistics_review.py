# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-09-28 | Author: Claude Opus 4.8 | Version: V2.646
# Description: 【物流账单复核】路由（新工具线在后端的落点）。复核=核价(合同价格卡)×核量(金蝶数量)→归一态，接计提。
#   端点：取数说明读 / 导入合同价格卡 / 价格卡读 / 上传账单解析落中间表 / 接金蝶回填数量 / 出复核结果(费用项汇总+逐单)。
#   算法在 kernels/logistics_price + logistics_review + logistics_intake；表在 kernels/logistics_review_store；金蝶只读走 kingdee_client。
#   pilot=迅鸽（取数说明已种子，价格卡导《附件二》，核量取 XQLCK 出库数量）。计提行=复用 logistics_bills 聚合，另接。
import json
import re
import calendar
from datetime import datetime

from fastapi import APIRouter, Request, Response
from sqlalchemy import select, insert, delete, update, func

from core import JSONResponse, _require_perm, db
import kingdee_client as kc
from kernels import logistics_review_store as store
from kernels import logistics_price as lp
from kernels import logistics_review as lr
from kernels import logistics_intake as intake
from kernels import logistics_recon as lrc

router = APIRouter()
BL, PC, SP = store.bill_lines, store.price_card, store.intake_spec

_DETAIL_KEYS = ("period", "carrier", "grain", "subject", "doc_no", "annot", "fee", "bizline",
                "fee_item", "qty", "unit", "amount", "carrier_sub", "prov", "charge_wt",
                "sub_fees", "src_sheet", "src_row", "note")


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _perm(request):
    return _require_perm(request, "logistics_upload")


async def _read_upload(request):
    ctype = request.headers.get("content-type", "")
    if "multipart/form-data" in ctype:
        form = await request.form()
        f = form.get("file") or form.get("files")
        if f is not None and hasattr(f, "read"):
            return await f.read()
    return await request.body()


def _load_spec(carrier):
    with db._engine.connect() as c:
        r = c.execute(select(SP.c.spec_json).where(SP.c.carrier == carrier)).first()
    return json.loads(r[0]) if r else None


def _load_card(carrier):
    with db._engine.connect() as c:
        rows = c.execute(select(PC).where(PC.c.carrier == carrier)).mappings().all()
    return lp.load_card([dict(r) for r in rows]), len(rows)


# ---------- 取数说明 ----------
@router.get("/api/logistics-review/spec")
def review_spec(request: Request, carrier: str = "迅鸽"):
    if not _perm(request):
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    return {"ok": True, "carrier": carrier, "spec": _load_spec(carrier)}


# ---------- 本月有计提的承运商（金蝶 2241 供应商往来·计提凭证汇总）----------
@router.get("/api/logistics-review/carriers")
def review_carriers(request: Request, period: str = ""):
    if not _perm(request):
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    accr = {}
    if period and "-" in period:
        y, m = period.split("-")[:2]
        try:
            accr = lrc.accrued_by_carrier(kc.fetch_gl_voucher(int(y), int(m), prefix="2241"))
        except Exception:
            accr = {}
    sup = db.list_logi_suppliers() or []
    full2short = {s.get("full"): s.get("short") for s in sup if s.get("full")}

    def match_short(name):
        # 金蝶摘要提取的承运商名常截在「供应链/物流」，与档案全名「…有限公司」不全等 → 前缀/包含兜底
        if name in full2short:
            return full2short[name]
        for sfull, sshort in full2short.items():
            if sfull and (sfull.startswith(name) or name.startswith(sfull) or name in sfull or sfull in name):
                return sshort
        return None

    with db._engine.connect() as c:
        specs = {r[0] for r in c.execute(select(SP.c.carrier)).all()}
    out = []
    for full, amt in accr.items():
        short = match_short(full) or full
        out.append({"short": short, "full": full, "accrued": round(amt or 0, 2), "has_spec": short in specs})
    # 有计提但没配取数说明的也列出来（灰示"未配"）；再补上已配却本月无计提的（如 pilot 迅鸽），排在后
    listed = {x["short"] for x in out}
    for sc in specs:
        if sc not in listed:
            out.append({"short": sc, "full": "", "accrued": None, "has_spec": True})
    out.sort(key=lambda x: (-(x["accrued"] or -1), x["short"]))
    return {"ok": True, "period": period, "carriers": out, "kd_ok": bool(accr)}


# ---------- 第一页总览：承运商 × 主体 的 计提/付款/差异（金蝶 2241）----------
_SUBJECTS = ["深圳星期零", "深圳星期九", "孝感星期九"]   # 固定三列（其余账簿归「其它」不单列）


@router.get("/api/logistics-review/overview")
def review_overview(request: Request, period: str = ""):
    if not _perm(request):
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    if not period or "-" not in period:
        return JSONResponse({"ok": False, "msg": "缺账期"}, status_code=400)
    y, m = period.split("-")[:2]
    orgs = db.list_orgs() or []
    book2short = {o.get("full_name"): o.get("short_name") for o in orgs if o.get("full_name")}
    sup = db.list_logi_suppliers() or []
    full2short = {s.get("full"): s.get("short") for s in sup if s.get("full")}

    def sup_short(name):
        if name in full2short:
            return full2short[name]
        for sfull, sshort in full2short.items():
            if sfull and (sfull.startswith(name) or name.startswith(sfull) or name in sfull or sfull in name):
                return sshort
        return name

    fields = list(kc.GL_VOUCHER_FIELDS) + [("FACCOUNTBOOKID.FName", "账簿")]
    rows = []
    try:
        s, conf = kc.login()
        rows = kc._query(s, conf, "GL_VOUCHER", fields,
                         "FAccountID.FNumber like '2241%%' and FYear=%d and FPeriod=%d" % (int(y), int(m)))
    except Exception:
        rows = []
    # 计提=贷方(摘要「计提…运费/仓储费/装卸/搬运/物流」)；付款=借方(摘要含某承运商名)
    accr, paid = {}, {}
    carriers = set()
    for r in rows:
        z = str(r.get("FEXPLANATION") or "")
        book = book2short.get(str(r.get("账簿") or ""), None)
        cr = r.get("FCREDIT") or 0
        if cr and "计提" in z and any(k in z for k in lrc._ACCR_KW):
            mo = lrc._ACCR_RE.search(z)
            if mo:
                cf = mo.group(1)
                carriers.add(cf)
                accr[(book, cf)] = accr.get((book, cf), 0.0) + float(cr)
    for r in rows:
        dr = r.get("FDEBIT") or 0
        if not dr:
            continue
        z = str(r.get("FEXPLANATION") or "")
        book = book2short.get(str(r.get("账簿") or ""), None)
        for cf in carriers:
            if cf in z:
                paid[(book, cf)] = paid.get((book, cf), 0.0) + float(dr)
                break
    with db._engine.connect() as c:
        specs = {r[0] for r in c.execute(select(SP.c.carrier)).all()}
    out = {}
    for cf in carriers:
        short = sup_short(cf)
        cells = {}
        tot_accr = 0.0
        for subj in _SUBJECTS:
            a = round(accr.get((subj, cf), 0.0), 2)
            p = round(paid.get((subj, cf), 0.0), 2)
            cells[subj] = {"accr": a, "paid": p, "diff": round(a - p, 2)}
            tot_accr += a
        out[cf] = {"carrier": short, "full": cf, "has_spec": short in specs, "cells": cells,
                   "total_accr": round(tot_accr, 2)}
    rowlist = sorted(out.values(), key=lambda x: -x["total_accr"])
    return {"ok": True, "period": period, "subjects": _SUBJECTS, "rows": rowlist, "kd_ok": bool(rows)}


# ---------- 价格卡：导入合同价目表 / 读 ----------
@router.post("/api/logistics-review/price-card/import")
async def price_card_import(request: Request, carrier: str = "迅鸽"):
    u = _perm(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    data = await _read_upload(request)
    if not data:
        return JSONResponse({"ok": False, "msg": "未收到文件"}, status_code=400)
    try:
        rows = lp.parse_contract_xlsx(data, carrier)
    except Exception:
        return JSONResponse({"ok": False, "msg": "价目表解析失败，请确认是合同价目表原格式"}, status_code=400)
    with db._engine.begin() as c:
        c.execute(delete(PC).where(PC.c.carrier == carrier))
        for r in rows:
            c.execute(insert(PC).values(updated_by=u["name"], updated_at=_now(), **r))
    db.audit(u["name"], "物流复核-导入价格卡", carrier, "%d 行；源合同价目表（已签署）" % len(rows))
    return {"ok": True, "rows": len(rows)}


@router.get("/api/logistics-review/price-card")
def price_card_read(request: Request, carrier: str = "迅鸽"):
    if not _perm(request):
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    with db._engine.connect() as c:
        rows = [dict(r) for r in c.execute(select(PC).where(PC.c.carrier == carrier)).mappings().all()]
    return {"ok": True, "carrier": carrier, "rows": rows}


# ---------- 上传账单解析 → 中间表 ----------
@router.post("/api/logistics-review/parse")
async def review_parse(request: Request, carrier: str = "迅鸽", period: str = ""):
    u = _perm(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    spec = _load_spec(carrier)
    if not spec:
        return JSONResponse({"ok": False, "msg": "该承运商还没配取数说明"}, status_code=400)
    spec["period"] = period
    data = await _read_upload(request)
    if not data:
        return JSONResponse({"ok": False, "msg": "未收到文件"}, status_code=400)
    try:
        res = intake.parse_bill(spec, data)
    except Exception:
        return JSONResponse({"ok": False, "msg": "账单解析失败，请核对取数说明与账单格式"}, status_code=400)
    batch = "%s|%s|%s" % (carrier, period, datetime.now().strftime("%Y%m%d%H%M%S"))
    with db._engine.begin() as c:
        c.execute(delete(BL).where((BL.c.carrier == carrier) & (BL.c.period == period)))
        for grain in ("detail", "accrual"):
            for r in res[grain]:
                vals = {k: r.get(k) for k in _DETAIL_KEYS}
                vals["batch_id"] = batch
                vals["review_mode"] = "audit"
                vals["created_at"] = _now()
                c.execute(insert(BL).values(**vals))
    db.audit(u["name"], "物流复核-解析账单", "%s %s" % (carrier, period),
             "明细 %d 行 / 计提 %d 行 / 跳过 %d 表" % (len(res["detail"]), len(res["accrual"]), len(res["skipped"])))
    return {"ok": True, "detail": len(res["detail"]), "accrual": len(res["accrual"]), "skipped": res["skipped"]}


# ---------- 接金蝶回填出库数量（核量）----------
# 按重量核量的承运商（干线/冷运：核量比金蝶出库重量kg，不是件数）
_WEIGHT_CARRIERS = {"顺丰冷运", "天鹰物流"}


def _kd_weight_by_doc(s, conf, docs):
    """按单号→金蝶单据取货物出库重量kg（千克计量物料的基本数量之和）。返回 {单号: kg}。只读。"""
    by_form = {}
    for no in docs:
        d0 = (no or "").split("+")[0]
        if not d0 or d0 == "无单据":
            continue
        pre = "".join(ch for ch in d0 if ch.isalpha())
        for form in _FORM_BY_PREFIX.get(pre, ["SAL_OUTSTOCK"])[:1]:
            by_form.setdefault(form, set()).add(d0)
    mats = _fetch_doc_materials(s, conf, by_form) if by_form else {}
    out = {}
    for no, ms in mats.items():
        kg = 0.0
        for m in ms:
            u = str(m.get("基本单位") or "")
            if "千克" in u or "kg" in u.lower():
                try:
                    kg += float(m.get("基本数量") or 0)
                except (TypeError, ValueError):
                    pass
        out[no] = round(kg, 2)
    return out


@router.post("/api/logistics-review/kingdee-qty")
def review_kingdee_qty(request: Request, carrier: str = "迅鸽", period: str = ""):
    u = _perm(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    if not period or "-" not in period:
        return JSONResponse({"ok": False, "msg": "缺账期"}, status_code=400)
    # 取本批 detail 单号前缀（迅鸽=XQLCK），按月拉出库单聚合数量（货品，剔包装）
    with db._engine.connect() as c:
        docs = [r[0] for r in c.execute(select(BL.c.doc_no).where(
            (BL.c.carrier == carrier) & (BL.c.period == period) & (BL.c.grain == "detail"))).all()]
    # 按重量核量的承运商（顺丰冷运/天鹰）：逐单取金蝶出库重量kg 回填 kd_qty
    if carrier in _WEIGHT_CARRIERS:
        try:
            s, conf = kc.login()
            wmap = _kd_weight_by_doc(s, conf, docs)
        except Exception:
            return JSONResponse({"ok": False, "msg": "金蝶取数失败，稍后重试；不影响已存复核结果"}, status_code=502)
        hit = 0
        with db._engine.begin() as c:
            for no, w in wmap.items():
                res = c.execute(update(BL).where(
                    (BL.c.carrier == carrier) & (BL.c.period == period) &
                    (func.substr(BL.c.doc_no, 1, len(no)) == no)).values(kd_qty=w))
                hit += res.rowcount or 0
        db.audit(u["name"], "物流复核-接金蝶核量(重量)", "%s %s" % (carrier, period),
                 "只读取数；单据 %d，回填 %d 行(kg)" % (len(wmap), hit))
        return {"ok": True, "kd_docs": len(wmap), "filled": hit, "by": "weight"}
    prefixes = sorted({"".join(ch for ch in (d.split("+")[0]) if ch.isalpha()) for d in docs if d and d != "无单据"})
    prefixes = [p for p in prefixes if p]
    y, m = period.split("-")[:2]
    last = calendar.monthrange(int(y), int(m))[1]
    d0, d1 = "%s-%s-01" % (y, m), "%s-%s-%02d" % (y, m, last)
    PACK = ("纸箱", "电商专供袋", "拉链", "气泡", "胶带", "气枕", "葫芦膜", "编织袋", "文件封")
    fields = [("FBillNo", "单号"), ("FRealQty", "数量"), ("FMaterialID.FName", "物料")]
    try:
        s, conf = kc.login()
        qty = {}
        for pre in prefixes:
            filt = "FDate>='%s' and FDate<='%s' and FBillNo like '%s%%'" % (d0, d1, pre)
            for r in kc._query(s, conf, "SAL_OUTSTOCK", fields, filt):
                no = r["单号"]
                if not no:
                    continue
                nm = r.get("物料") or ""
                if any(p in nm for p in PACK):
                    continue
                try:
                    qty[no] = qty.get(no, 0.0) + float(r["数量"] or 0)
                except (TypeError, ValueError):
                    pass
    except Exception:
        return JSONResponse({"ok": False, "msg": "金蝶取数失败，稍后重试；不影响已存复核结果"}, status_code=502)
    hit = 0
    with db._engine.begin() as c:
        for no, q in qty.items():
            res = c.execute(update(BL).where(
                (BL.c.carrier == carrier) & (BL.c.period == period) &
                (func.substr(BL.c.doc_no, 1, len(no)) == no)).values(kd_qty=round(q, 2)))
            hit += res.rowcount or 0
    db.audit(u["name"], "物流复核-接金蝶核量", "%s %s" % (carrier, period),
             "只读取数；出库单 %d 单，回填 %d 行" % (len(qty), hit))
    return {"ok": True, "kd_docs": len(qty), "filled": hit}


# ---------- 登记制（议价/报销：货拉拉等）：单据运费·其他单据 ----------
_FORM_BY_PREFIX = {
    "FBDR": ["STK_TransferIn", "STK_TransferOut"], "FBDC": ["STK_TransferOut", "STK_TransferIn"],
    "CGRK": ["STK_InStock"], "XSCKD": ["SAL_OUTSTOCK"], "XQLCK": ["SAL_OUTSTOCK"],
    "QTCK": ["STK_MisDelivery"], "RK": ["SAL_RETURNSTOCK"], "CGTL": ["PUR_MRB"],
}
# 各单据的「基本单位数量」字段 Key 大小写不同（逐单据写死，缺列降级）
_QTYFIELD_BY_FORM = {
    "STK_TransferIn": "FBaseQty", "STK_TransferOut": "FBaseQty", "STK_InStock": "FBaseUnitQty",
    "SAL_OUTSTOCK": "FBaseUnitQty", "STK_MisDelivery": "FBaseQty", "SAL_RETURNSTOCK": "FBaseunitQty",
    "PUR_MRB": "FBASEUNITQTY",
}


@router.post("/api/logistics-review/register")
async def review_register_add(request: Request):
    u = _perm(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    try:
        b = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "msg": "请求格式错误"}, status_code=400)
    carrier, period, doc_no = (b.get("carrier") or "").strip(), (b.get("period") or "").strip(), (b.get("doc_no") or "").strip()
    amount = b.get("amount")
    if not carrier or not period or amount in (None, ""):
        return JSONResponse({"ok": False, "msg": "承运商、账期、金额必填"}, status_code=400)
    try:
        amount = round(float(amount), 2)
    except Exception:
        return JSONResponse({"ok": False, "msg": "金额须为数字"}, status_code=400)
    note = _reg_note(b.get("dept"), b.get("source"), b.get("date"), b.get("note"))
    with db._engine.begin() as c:
        c.execute(insert(BL).values(
            batch_id="register|%s|%s" % (carrier, period), period=period, carrier=carrier,
            grain="detail", review_mode="register", subject=(b.get("subject") or "").strip(),
            doc_no="+".join([p for p in re.split(r"[+/,，、;；\s]+", doc_no) if p]) if doc_no else "",
            annot=(b.get("annot") or "").strip(), fee_item=(b.get("fee_item") or "运费").strip(),
            amount=amount, unit=(b.get("unit") or "").strip(), note=note, created_at=_now()))
    db.audit(u["name"], "物流复核-登记单据运费", "%s %s" % (carrier, period), "%s %.2f元" % (doc_no or "无单号", amount))
    return {"ok": True}


@router.post("/api/logistics-review/register/delete")
async def review_register_delete(request: Request):
    u = _perm(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    b = await request.json()
    rid = b.get("id")
    with db._engine.begin() as c:
        c.execute(delete(BL).where((BL.c.id == rid) & (BL.c.review_mode == "register")))
    return {"ok": True}


_REG_TPL_COLS = ["日期", "费用主体", "费用类型", "业务线", "ERP单据号", "需求部门", "承运商", "运费", "备注"]


def _reg_note(dept, source, date, extra=""):
    return "；".join(x for x in [
        ("需求部门 " + dept) if dept else "",
        ("来源 " + source) if source else "",
        ("日期 " + date) if date else "", (extra or "")] if x)


def _cell_str(v):
    if v is None:
        return ""
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v).strip()


@router.get("/api/logistics-review/register/template")
def review_register_template(request: Request):
    """下载「其他单据（登记制）」批量导入模板 xlsx。列＝用户现有表：日期/费用主体/费用类型/业务线/ERP单据号/需求部门/承运商/运费/备注。"""
    if not _perm(request):
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from io import BytesIO
    wb = Workbook(); ws = wb.active; ws.title = "单据运费登记"
    ws.append(_REG_TPL_COLS)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="1F6E8C")
    ws.append(["2026-08-29", "深圳星期零", "销售出库运费", "星期零电商", "FBDR075727", "电商部门", "货拉拉", 347.4, "示例行，可删"])
    ws.append(["2026-08-06", "深圳星期零", "销售出库运费", "星期零电商", "FBDR074944", "电商部门", "货拉拉", 197.99, ""])
    for i, wd in enumerate([12, 12, 14, 14, 16, 12, 10, 10, 18], start=1):
        ws.column_dimensions[chr(64 + i)].width = wd
    bio = BytesIO(); wb.save(bio)
    return Response(content=bio.getvalue(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=logistics_register_template.xlsx"})


@router.post("/api/logistics-review/register/import")
async def review_register_import(request: Request, period: str = ""):
    """批量导入其他单据运费（登记制）。解析用户模板：承运商+运费必填，账期按「日期」列取 YYYY-MM（缺则用当前账期）。"""
    u = _perm(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    data = await _read_upload(request)
    if not data:
        return JSONResponse({"ok": False, "msg": "未收到文件"}, status_code=400)
    try:
        import openpyxl
        from io import BytesIO
        wb = openpyxl.load_workbook(BytesIO(data), read_only=True, data_only=True)
        ws = wb.active
        grid = [list(row) for row in ws.iter_rows(values_only=True)]
    except Exception:
        return JSONResponse({"ok": False, "msg": "Excel 读取失败，请用下载的模板另存 .xlsx 再传"}, status_code=400)
    # 找表头行（含「承运商」且含「运费」或「金额」）
    hdr_i, hdr = -1, []
    for i, row in enumerate(grid[:8]):
        txt = [_cell_str(c) for c in row]
        if any("承运商" in t for t in txt) and any(("运费" in t or "金额" in t) for t in txt):
            hdr_i, hdr = i, txt; break
    if hdr_i < 0:
        return JSONResponse({"ok": False, "msg": "没找到表头行（需含「承运商」和「运费」列），请用下载模板"}, status_code=400)

    def col(*keys):
        for j, h in enumerate(hdr):
            if any(k in h for k in keys):
                return j
        return -1
    ci = {"date": col("日期"), "subject": col("费用主体", "主体"), "fee_item": col("费用类型", "类型"),
          "bizline": col("业务线"), "doc_no": col("单据号", "单号", "ERP"), "dept": col("需求部门", "部门"),
          "carrier": col("承运商"), "amount": col("运费", "金额"), "note": col("备注")}
    if ci["carrier"] < 0 or ci["amount"] < 0:
        return JSONResponse({"ok": False, "msg": "表头缺「承运商」或「运费」列"}, status_code=400)

    def get(row, key):
        j = ci[key]
        return _cell_str(row[j]) if 0 <= j < len(row) else ""
    added, skipped, errs = 0, 0, []
    with db._engine.begin() as c:
        for r in grid[hdr_i + 1:]:
            if not any(_cell_str(x) for x in r):
                continue
            carrier = get(r, "carrier"); amt_s = get(r, "amount")
            if not carrier or amt_s == "":
                skipped += 1; continue
            try:
                amount = round(float(str(amt_s).replace(",", "").replace("￥", "").replace("¥", "")), 2)
            except Exception:
                skipped += 1; errs.append("金额非数字：%s" % amt_s); continue
            date = get(r, "date")
            per = period
            m = re.match(r"(\d{4})[-/年.](\d{1,2})", date or "")
            if m:
                per = "%s-%s" % (m.group(1), m.group(2).zfill(2))
            if not per:
                skipped += 1; errs.append("缺账期且日期不可解析"); continue
            doc_no = get(r, "doc_no")
            c.execute(insert(BL).values(
                batch_id="register|%s|%s" % (carrier, per), period=per, carrier=carrier,
                grain="detail", review_mode="register", subject=get(r, "subject"),
                doc_no="+".join([p for p in re.split(r"[+/,，、;；\s]+", doc_no) if p]) if doc_no else "",
                annot=get(r, "bizline"), fee_item=get(r, "fee_item") or "运费",
                amount=amount, unit="", note=_reg_note(get(r, "dept"), "", date, get(r, "note")), created_at=_now()))
            added += 1
    db.audit(u["name"], "物流复核-导入单据运费", period or "多期", "新增 %d 行 / 跳过 %d 行" % (added, skipped))
    return {"ok": True, "added": added, "skipped": skipped, "errs": errs[:5]}


@router.get("/api/logistics-review/register-list")
def review_register_list(request: Request, period: str = ""):
    if not _perm(request):
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    with db._engine.connect() as c:
        rows = [dict(r) for r in c.execute(select(BL).where(
            (BL.c.review_mode == "register") & (BL.c.period == period)).order_by(BL.c.id.desc())).mappings().all()]
    lr.review_details(rows, {"express": {}, "unit": {}}, {})
    view = [{k: r.get(k) for k in ("id", "carrier", "subject", "doc_no", "annot", "fee_item",
             "amount", "unit", "kd_kg", "qty_state", "verdict", "note")} for r in rows]
    total = round(sum((r.get("amount") or 0) for r in rows), 2)
    real = sum(1 for r in rows if r.get("qty_state") == "ok")
    miss = sum(1 for r in rows if r.get("qty_state") == "miss")
    return {"ok": True, "period": period, "rows": view, "count": len(rows),
            "total": total, "doc_real": real, "doc_miss": miss}


# 各单据的物料明细字段（编码/名称/基本单位数量/基本单位/往来/销售额），大小写逐单据写死，缺列降级
_DOC_MAT_FIELDS = {
    "SAL_OUTSTOCK": [("FBillNo", "单号"), ("FMaterialID.FNumber", "编码"), ("FMaterialID.FName", "名称"),
                     ("FBaseUnitQty", "基本数量"), ("FBaseUnitId.FName", "基本单位"),
                     ("FCustomerID.FName", "往来"), ("FAllAmount", "销售额")],
    "STK_TransferIn": [("FBillNo", "单号"), ("FMaterialId.FNumber", "编码"), ("FMaterialId.FName", "名称"),
                       ("FBaseQty", "基本数量"), ("FBaseUnitId.FName", "基本单位"), ("FStockOrgId.FName", "往来")],
    "STK_TransferOut": [("FBillNo", "单号"), ("FMaterialId.FNumber", "编码"), ("FMaterialId.FName", "名称"),
                        ("FBaseQty", "基本数量"), ("FBaseUnitId.FName", "基本单位"), ("FStockOrgId.FName", "往来")],
    "STK_InStock": [("FBillNo", "单号"), ("FMaterialId.FNumber", "编码"), ("FMaterialId.FName", "名称"),
                    ("FBaseUnitQty", "基本数量"), ("FBaseUnitId.FName", "基本单位"), ("FSupplierId.FName", "往来")],
    "STK_MisDelivery": [("FBillNo", "单号"), ("FMaterialID.FNumber", "编码"), ("FMaterialID.FName", "名称"),
                        ("FBaseQty", "基本数量"), ("FBaseUnitId.FName", "基本单位"), ("FDeptId.FName", "往来")],
}


def _fetch_doc_materials(s, conf, docs_by_form):
    """按 单据前缀→form 分组，查金蝶物料明细。返回 {单号: [{编码,名称,基本数量,基本单位,往来,销售额}]}。只读，缺列降级。"""
    out = {}
    for form, docs in docs_by_form.items():
        fields = _DOC_MAT_FIELDS.get(form)
        if not fields:
            continue
        docs = list(docs)
        for i in range(0, len(docs), 200):
            inlist = ",".join("'%s'" % d.replace("'", "") for d in docs[i:i + 200])
            cols = list(fields)
            rr = []
            while cols:
                try:
                    rr = kc._query(s, conf, form, cols, "FBillNo in (%s)" % inlist)
                    break
                except Exception:
                    cols = cols[:-1]
            for r in rr:
                no = r.get("单号")
                if not no:
                    continue
                out.setdefault(str(no), []).append({
                    "编码": r.get("编码"), "名称": r.get("名称"),
                    "基本数量": r.get("基本数量"), "基本单位": r.get("基本单位"),
                    "往来": r.get("往来"), "销售额": r.get("销售额")})
    return out


def _bizline_of(annot):
    """从费用标注取业务线段（植物肉/鲜食/零售/小料/豆蛋制品/电商/山姆零售/kikiherb/海外）。"""
    for b in ("植物肉", "鲜食", "山姆", "零售", "小料", "豆蛋制品", "电商", "kikiherb", "海外"):
        if b in (annot or ""):
            return "山姆零售" if b == "山姆" else b
    return ""


@router.get("/api/logistics-review/doc-freight")
def review_doc_freight(request: Request, period: str = "", mode: str = "other", q: str = "", page: int = 1, size: int = 80):
    """物料级单据运费（两 tab 同一套列）：一行=单据的一个物料行，运费按基本数量摊，单位运费=摊得运费/基本数量，费比=运费/销售额。
    mode='other' 登记制(其他单据) / 'sales' 销售出库(已解析 audit 明细)。"""
    if not _perm(request):
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    with db._engine.connect() as c:
        if mode == "sales":
            src = [dict(r) for r in c.execute(select(BL).where(
                (BL.c.period == period) & (BL.c.grain == "detail") & (BL.c.review_mode == "audit") &
                (BL.c.fee == "销售出库费用"))).mappings().all()]
        else:
            src = [dict(r) for r in c.execute(select(BL).where(
                (BL.c.period == period) & (BL.c.review_mode == "register")).order_by(BL.c.id.desc())).mappings().all()]
    # 按单据汇总运费（一单号多行=累加）
    docfee = {}
    for r in src:
        for no in [p for p in (r.get("doc_no") or "").split("+") if p] or ["（无单号）"]:
            g = docfee.setdefault(no, {"运费": 0.0, "subject": r.get("subject"), "annot": r.get("annot"),
                                       "fee_item": r.get("fee_item"), "carrier": r.get("carrier"),
                                       "dept": (r.get("note") or ""), "qty_state": r.get("qty_state"),
                                       "reg_id": r.get("id") if mode != "sales" else None})
            n = len([p for p in (r.get("doc_no") or "").split("+") if p]) or 1
            g["运费"] += (r.get("amount") or 0) / n
    # 全量口径：总运费/单据数在取金蝶物料前算好（销售出库单可达数千张，不能每次翻页全量拉金蝶）
    all_total = round(sum(g["运费"] for g in docfee.values()), 2)
    # 单据号搜索先按单号过滤（物料名/编码搜索仅在本页已取物料内二次过滤）
    doc_items = sorted(docfee.items(), key=lambda kv: kv[0])
    if q:
        doc_items = [kv for kv in doc_items if q in (kv[0] or "")]
    doc_total = len(doc_items)
    page = max(1, int(page))
    page_items = doc_items[(page - 1) * size: page * size]  # 只取本页这一批单据
    # 只对本页单据取金蝶物料明细
    by_form = {}
    for no, _g in page_items:
        if no == "（无单号）":
            continue
        pre = "".join(ch for ch in no if ch.isalpha())
        for form in _FORM_BY_PREFIX.get(pre, ["SAL_OUTSTOCK"])[:1]:
            by_form.setdefault(form, set()).add(no)
    mats = {}
    if by_form:
        try:
            s, conf = kc.login()
            mats = _fetch_doc_materials(s, conf, by_form)
        except Exception:
            mats = {}
    rows = []
    for no, g in page_items:
        fee = round(g["运费"], 2)
        biz = _bizline_of(g["annot"])
        lines = mats.get(no) or []
        kgsum = sum(float(m["基本数量"] or 0) for m in lines if m.get("基本数量") not in (None, ""))
        if not lines:
            rows.append({"subject": g["subject"], "carrier": g["carrier"], "fee_item": g["fee_item"], "bizline": biz, "doc_no": no,
                         "party": g["dept"] if mode == "other" else "", "code": "", "name": "（金蝶无此单据物料）",
                         "baseqty": None, "baseunit": "", "fee": fee, "unitfee": None, "sales": None, "ratio": None,
                         "reg_id": g.get("reg_id")})
            continue
        for m in lines:
            bq = None
            try:
                bq = float(m["基本数量"]) if m.get("基本数量") not in (None, "") else None
            except (TypeError, ValueError):
                bq = None
            share = (bq / kgsum) if (kgsum and bq) else (1.0 / len(lines))
            fline = round(fee * share, 2)
            sales_amt = None
            try:
                sales_amt = float(m["销售额"]) if m.get("销售额") not in (None, "") else None
            except (TypeError, ValueError):
                sales_amt = None
            rows.append({
                "subject": g["subject"], "carrier": g["carrier"], "fee_item": g["fee_item"], "bizline": biz, "doc_no": no,
                "party": (m.get("往来") or "") if mode == "sales" else (g["dept"] or ""),
                "code": m.get("编码"), "name": m.get("名称"), "baseqty": bq, "baseunit": m.get("基本单位"),
                "fee": fline, "unitfee": round(fline / bq, 4) if bq else None,
                "sales": round(sales_amt, 2) if sales_amt is not None else None,
                "ratio": round(fline / sales_amt, 4) if sales_amt else None,
                "reg_id": g.get("reg_id")})
    # 本页物料名/编码二次过滤（仅在已取物料内，跨页搜索请用单据号）
    if q:
        rows = [r for r in rows if q in (r["doc_no"] or "") or q in (r.get("name") or "") or q in (r.get("code") or "")]
    pages = max(1, (doc_total + size - 1) // size)
    return {"ok": True, "period": period, "mode": mode, "count": len(rows), "total": all_total,
            "doc_count": doc_total, "rows": rows, "page": page, "pages": pages, "size": size}


# 单据运费·销售出库 tab（旧·按单据汇总，保留兼容）：
@router.get("/api/logistics-review/doc-sales")
def review_doc_sales(request: Request, period: str = "", q: str = "", page: int = 1, size: int = 50):
    if not _perm(request):
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    with db._engine.connect() as c:
        rows = [dict(r) for r in c.execute(select(BL).where(
            (BL.c.period == period) & (BL.c.grain == "detail") & (BL.c.review_mode == "audit") &
            (BL.c.fee == "销售出库费用")).mappings().all())]
    # 按单据汇总
    docs = {}
    for r in rows:
        key = r.get("doc_no") or "无单号"
        g = docs.setdefault(key, {"doc_no": key, "carrier": r.get("carrier"), "subject": r.get("subject"),
                                  "annot": r.get("annot"), "amount": 0.0, "charge_wt": r.get("charge_wt"),
                                  "kd_qty": r.get("kd_qty"), "n": 0})
        g["amount"] += r.get("amount") or 0
        g["n"] += 1
    out = sorted(docs.values(), key=lambda x: -x["amount"])
    for g in out:
        g["amount"] = round(g["amount"], 2)
    if q:
        out = [g for g in out if q in (g["doc_no"] or "") or q in (g["carrier"] or "")]
    page = max(1, int(page))
    total_amt = round(sum(g["amount"] for g in out), 2)
    return {"ok": True, "period": period, "count": len(out), "total": total_amt,
            "rows": out[(page - 1) * size: page * size], "page": page, "size": size, "detail_total": len(out)}


@router.post("/api/logistics-review/register/kingdee-check")
def review_register_kingdee_check(request: Request, period: str = ""):
    u = _perm(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    with db._engine.connect() as c:
        rows = [dict(r) for r in c.execute(select(BL.c.id, BL.c.doc_no).where(
            (BL.c.review_mode == "register") & (BL.c.period == period)).mappings().all())]
    # 按前缀分组单号 → 查对应金蝶单据存在性（只读）
    form_docs = {}
    for r in rows:
        for no in [p for p in (r["doc_no"] or "").split("+") if p]:
            pre = "".join(ch for ch in no if ch.isalpha())
            for form in _FORM_BY_PREFIX.get(pre, ["SAL_OUTSTOCK"]):
                form_docs.setdefault(form, set()).add(no)
    exists = set()
    kg = {}   # 单号 → 金蝶基本单位数量之和（kg/吨/件，看物料基本单位）
    try:
        s, conf = kc.login()
        for form, docs in form_docs.items():
            qf = _QTYFIELD_BY_FORM.get(form, "FBaseQty")
            docs = list(docs)
            for i in range(0, len(docs), 200):
                chunk = docs[i:i + 200]
                inlist = ",".join("'%s'" % d.replace("'", "") for d in chunk)
                try:
                    rr_list = kc._query(s, conf, form, [("FBillNo", "单号"), (qf, "基本数量")], "FBillNo in (%s)" % inlist)
                except Exception:
                    try:
                        rr_list = kc._query(s, conf, form, [("FBillNo", "单号")], "FBillNo in (%s)" % inlist)
                    except Exception:
                        rr_list = []
                for rr in rr_list:
                    no = rr.get("单号")
                    if not no:
                        continue
                    exists.add(str(no))
                    try:
                        kg[str(no)] = kg.get(str(no), 0.0) + float(rr.get("基本数量") or 0)
                    except (TypeError, ValueError):
                        pass
    except Exception:
        return JSONResponse({"ok": False, "msg": "金蝶取数失败，稍后重试"}, status_code=502)
    real = miss = 0
    with db._engine.begin() as c:
        for r in rows:
            nos = [p for p in (r["doc_no"] or "").split("+") if p]
            st = "ok" if (nos and all(n in exists for n in nos)) else "miss"
            kd = round(sum(kg.get(n, 0.0) for n in nos), 2) if nos else None
            if st == "ok":
                real += 1
            else:
                miss += 1
            c.execute(update(BL).where(BL.c.id == r["id"]).values(qty_state=st, kd_kg=kd))
    db.audit(u["name"], "物流复核-登记轻核单号", period, "单号真实 %d / 查无 %d（只读）" % (real, miss))
    return {"ok": True, "real": real, "miss": miss}


# ---------- 复核结果（费用项汇总 + 逐单）----------
@router.get("/api/logistics-review/result")
def review_result(request: Request, carrier: str = "迅鸽", period: str = "",
                  group: str = "ex", page: int = 1, size: int = 50, q: str = ""):
    if not _perm(request):
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    card, ncard = _load_card(carrier)
    with db._engine.connect() as c:
        rows = [dict(r) for r in c.execute(select(BL).where(
            (BL.c.carrier == carrier) & (BL.c.period == period) & (BL.c.grain == "detail")).mappings().all())]
        accr = [dict(r) for r in c.execute(select(BL).where(
            (BL.c.carrier == carrier) & (BL.c.period == period) & (BL.c.grain == "accrual")).mappings().all())]
    kdmap = {(r.get("doc_no") or "").split("+")[0]: r["kd_qty"] for r in rows if r.get("kd_qty") is not None}
    lr.review_details(rows, card, kdmap)
    summary = lr.summarize(rows)
    counts = lr.verdict_counts(rows)
    total_bill = round(sum((a.get("amount") or 0) for a in accr) or sum((r.get("amount") or 0) for r in rows), 2)

    def keep(r):
        if q:
            s = q.strip()
            if s not in (r.get("doc_no") or "") and s not in (r.get("prov") or ""):
                return False
        if group == "all":
            return True
        if group == "ex":
            return r["price_state"] in ("gap", "free", "over") or r["qty_state"] in ("miss", "qtydiff")
        if group in ("miss", "qtydiff"):
            return r["qty_state"] == group
        if group in ("gap", "free", "over"):
            return r["price_state"] == group
        if group == "pass":
            return r["verdict"] == "pass"
        return True

    filt = [r for r in rows if keep(r)]
    page = max(1, int(page))
    sl = filt[(page - 1) * size: page * size]
    view = [{k: r.get(k) for k in ("doc_no", "carrier_sub", "prov", "charge_wt", "qty", "kd_qty",
             "amount", "base_amount", "std_amount", "price_diff", "price_state", "qty_diff",
             "qty_state", "tier", "verdict", "fee_item")} for r in sl]
    return {"ok": True, "carrier": carrier, "period": period, "price_card_rows": ncard,
            "total_bill": total_bill, "summary": summary, "accrual": accr, "counts": counts,
            "detail_total": len(filt), "detail": view, "page": page, "size": size}
