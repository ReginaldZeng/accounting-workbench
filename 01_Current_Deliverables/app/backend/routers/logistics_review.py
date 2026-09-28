# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-09-28 | Author: Claude Opus 4.8 | Version: V2.660
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
    # 本月付款（按复核结果）：本月已复核账单应付合计，按承运商×主体（权责发生制·同期间比，非金蝶跨月现金借方）
    with db._engine.connect() as c:
        specs = {r[0] for r in c.execute(select(SP.c.carrier)).all()}
        billrows = c.execute(select(BL.c.subject, BL.c.carrier, func.sum(BL.c.amount)).where(
            (BL.c.period == period) & (BL.c.grain == "detail")).group_by(BL.c.subject, BL.c.carrier)).all()
    billmap = {(str(subj), str(car)): round(float(amt or 0), 2) for subj, car, amt in billrows}
    out = {}
    for cf in carriers:
        short = sup_short(cf)
        cells = {}
        tot_accr = 0.0
        for subj in _SUBJECTS:
            a = round(accr.get((subj, cf), 0.0), 2)
            p = billmap.get((subj, short), 0.0)      # 本月复核应付（该承运商本月账单复核后金额）
            cells[subj] = {"accr": a, "paid": p, "diff": round(a - p, 2)}
            tot_accr += a
        out[cf] = {"carrier": cf, "short": short, "full": cf, "has_spec": short in specs, "cells": cells,
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
                     ("FCustomerID.FName", "往来"), ("FAllAmount", "销售额"),
                     ("FMaterialID.FSpecification", "规格"), ("FRealQty", "数量件"), ("FUnitID.FName", "计价单位")],
    "STK_TransferIn": [("FBillNo", "单号"), ("FMaterialId.FNumber", "编码"), ("FMaterialId.FName", "名称"),
                       ("FBaseQty", "基本数量"), ("FBaseUnitId.FName", "基本单位"), ("FStockOrgId.FName", "往来"),
                       ("FMaterialId.FSpecification", "规格"), ("FQty", "数量件"), ("FUnitId.FName", "计价单位")],
    "STK_TransferOut": [("FBillNo", "单号"), ("FMaterialId.FNumber", "编码"), ("FMaterialId.FName", "名称"),
                        ("FBaseQty", "基本数量"), ("FBaseUnitId.FName", "基本单位"), ("FStockOrgId.FName", "往来"),
                        ("FMaterialId.FSpecification", "规格"), ("FQty", "数量件"), ("FUnitId.FName", "计价单位")],
    "STK_InStock": [("FBillNo", "单号"), ("FMaterialId.FNumber", "编码"), ("FMaterialId.FName", "名称"),
                    ("FBaseUnitQty", "基本数量"), ("FBaseUnitId.FName", "基本单位"), ("FSupplierId.FName", "往来"),
                    ("FMaterialId.FSpecification", "规格"), ("FRealQty", "数量件"), ("FUnitId.FName", "计价单位")],
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
                    "往来": r.get("往来"), "销售额": r.get("销售额"),
                    "规格": r.get("规格"), "数量件": r.get("数量件"), "计价单位": r.get("计价单位")})
    return out


_BOX_CARRIERS = {"丰源"}  # 按件数/箱核对：金蝶数量(袋)÷规格箱规=箱数，整车比箱、打托倒算托规


def _box_reg(spec):
    """从规格型号解析箱规（N袋/箱）。返回 int 或 None。"""
    m = re.search(r"(\d+)\s*袋/箱", str(spec or ""))
    return int(m.group(1)) if m else None


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
            (BL.c.fee == "销售出库费用"))).mappings().all()]
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
            (BL.c.review_mode == "register") & (BL.c.period == period))).mappings().all()]
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
            (BL.c.carrier == carrier) & (BL.c.period == period) & (BL.c.grain == "detail"))).mappings().all()]
        accr = [dict(r) for r in c.execute(select(BL).where(
            (BL.c.carrier == carrier) & (BL.c.period == period) & (BL.c.grain == "accrual"))).mappings().all()]
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

    by_weight = carrier in _WEIGHT_CARRIERS
    filt = [r for r in rows if keep(r)]
    page = max(1, int(page))
    sl = filt[(page - 1) * size: page * size]
    by_box = carrier in _BOX_CARRIERS
    if by_box:
        # 按件数(箱)：金蝶数量(袋)÷规格箱规=金蝶箱数；整车比箱、打托倒算托规；账单件数/运费按箱数摊到物料。整车议价单只登记不核件数。
        by_form = {}
        for r in sl:
            d0 = (r.get("doc_no") or "").split("+")[0]
            if not d0:
                continue
            pre = "".join(ch for ch in d0 if ch.isalpha())
            for form in _FORM_BY_PREFIX.get(pre, ["SAL_OUTSTOCK"])[:1]:
                by_form.setdefault(form, set()).add(d0)
        mats = {}
        if by_form:
            try:
                s2, conf2 = kc.login()
                mats = _fetch_doc_materials(s2, conf2, by_form)
            except Exception:
                mats = {}
        view = []
        for r in sl:
            d0 = (r.get("doc_no") or "").split("+")[0]
            lines = mats.get(d0) or []
            billcnt = float(r.get("qty") or 0)         # 账单件数
            fee = float(r.get("amount") or 0)
            nego = (r.get("carrier_sub") == "整车议价")
            biz = r.get("bizline") or _bizline_of(r.get("annot"))
            # 每物料箱数=数量件÷箱规(袋/箱)；箱规解析不出时退化用基本数量
            boxes = []
            for m in lines:
                br = _box_reg(m.get("规格"))
                try:
                    qcnt = float(m.get("数量件") or 0)
                except (TypeError, ValueError):
                    qcnt = 0.0
                boxes.append((qcnt / br) if (br and qcnt) else 0.0)
            boxsum = round(sum(boxes), 2)
            # 计费方式：整车议价→议价(不核)；否则件数≈箱数→整车按箱，件数远小→打托(倒算托规)
            if nego:
                mode_cn, tuo = "整车议价", None
                cnt_state = "na"
            elif boxsum and abs(billcnt - boxsum) <= max(1.0, 0.02 * boxsum):
                mode_cn, tuo, cnt_state = "整车按箱", None, ("ok" if abs(billcnt - boxsum) <= max(1.0, 0.02 * boxsum) else "qtydiff")
            elif boxsum and billcnt and boxsum > billcnt:
                tuo = round(boxsum / billcnt, 1)
                mode_cn, cnt_state = "打托(托规%s)" % tuo, "na"
            else:
                mode_cn, tuo, cnt_state = "待核", None, "qtydiff"
            conv = round(boxsum / billcnt, 2) if billcnt else None  # 换算系数(账单件↔金蝶箱)
            base = {"subject": r.get("subject"), "carrier": carrier, "fee_item": r.get("fee_item"),
                    "bizline": biz, "doc_no": d0, "bill_cnt": billcnt, "box_sum": boxsum,
                    "mode_cn": mode_cn, "conv": conv, "qty_state": cnt_state}
            if not lines:
                view.append({**base, "party": r.get("note") or "", "code": "", "name": "（金蝶无此单据物料）",
                             "base_qty": None, "base_unit": "", "box": None, "fee": round(fee, 2),
                             "unit_fee": None, "sales": None, "ratio": None})
                continue
            for i, m in enumerate(lines):
                bx = round(boxes[i], 2)
                share = (bx / boxsum) if boxsum else (1.0 / len(lines))
                fline = round(fee * share, 2)
                try:
                    sales = float(m.get("销售额")) if m.get("销售额") not in (None, "") else None
                except (TypeError, ValueError):
                    sales = None
                try:
                    qn = float(m.get("数量件") or 0)
                except (TypeError, ValueError):
                    qn = 0.0
                view.append({**base, "party": m.get("往来") or "", "code": m.get("编码"), "name": m.get("名称"),
                             "base_qty": qn or None, "base_unit": m.get("计价单位") or m.get("基本单位"),
                             "box": bx or None, "spec": m.get("规格"),
                             "fee": fline, "unit_fee": round(fline / bx, 2) if bx else None,
                             "sales": round(sales, 2) if sales is not None else None,
                             "ratio": round(fline / sales, 4) if sales else None})
        return {"ok": True, "carrier": carrier, "period": period, "price_card_rows": ncard,
                "total_bill": total_bill, "summary": summary, "accrual": accr, "counts": counts,
                "by_box": True, "material": True, "detail_total": len(filt), "detail": view,
                "page": page, "size": size}
    if by_weight:
        # 物料级：每单拆金蝶物料，运费/账单重量按金蝶基本单位重量摊；换算系数＝账单计费重量÷金蝶重量(毛重比)
        by_form = {}
        for r in sl:
            d0 = (r.get("doc_no") or "").split("+")[0]
            if not d0:
                continue
            pre = "".join(ch for ch in d0 if ch.isalpha())
            for form in _FORM_BY_PREFIX.get(pre, ["SAL_OUTSTOCK"])[:1]:
                by_form.setdefault(form, set()).add(d0)
        mats = {}
        if by_form:
            try:
                s2, conf2 = kc.login()
                mats = _fetch_doc_materials(s2, conf2, by_form)
            except Exception:
                mats = {}
        view = []
        for r in sl:
            d0 = (r.get("doc_no") or "").split("+")[0]
            lines = mats.get(d0) or []
            chg = float(r.get("charge_wt") or 0)
            fee = float(r.get("amount") or 0)
            biz = r.get("bizline") or _bizline_of(r.get("annot"))   # 优先用中间表已存业务线
            kgs = []
            for m in lines:
                u = str(m.get("基本单位") or "")
                try:
                    bw = float(m.get("基本数量") or 0)
                except (TypeError, ValueError):
                    bw = 0.0
                kgs.append(bw if ("千克" in u or "kg" in u.lower()) else 0.0)
            kgsum = sum(kgs)
            conv = round(chg / kgsum, 3) if kgsum else None
            base = {"subject": r.get("subject"), "carrier": carrier, "fee_item": r.get("fee_item"),
                    "bizline": biz, "doc_no": d0, "bill_qty_doc": round(chg, 2), "conv": conv,
                    "qty_state": r.get("qty_state")}
            if not lines:
                view.append({**base, "party": "", "code": "", "name": "（金蝶无此单据物料）",
                             "base_wt": None, "base_unit": "", "fee": round(fee, 2), "unit_fee": None,
                             "bill_qty": round(chg, 2), "bill_unit": "千克", "sales": None, "ratio": None})
                continue
            for i, m in enumerate(lines):
                bw = kgs[i]
                share = (bw / kgsum) if kgsum else (1.0 / len(lines))
                fline = round(fee * share, 2)
                bqty = round(chg * share, 2)
                try:
                    sales = float(m.get("销售额")) if m.get("销售额") not in (None, "") else None
                except (TypeError, ValueError):
                    sales = None
                view.append({**base, "party": m.get("往来") or "", "code": m.get("编码"), "name": m.get("名称"),
                             "base_wt": bw or None, "base_unit": m.get("基本单位"),
                             "fee": fline, "unit_fee": round(fline / bw, 4) if bw else None,
                             "bill_qty": bqty, "bill_unit": "千克",
                             "sales": round(sales, 2) if sales is not None else None,
                             "ratio": round(fline / sales, 4) if sales else None})
        return {"ok": True, "carrier": carrier, "period": period, "price_card_rows": ncard,
                "total_bill": total_bill, "summary": summary, "accrual": accr, "counts": counts,
                "by_weight": True, "material": True, "detail_total": len(filt), "detail": view,
                "page": page, "size": size}
    view = [{k: r.get(k) for k in ("doc_no", "carrier_sub", "prov", "charge_wt", "qty", "kd_qty",
             "amount", "base_amount", "std_amount", "price_diff", "price_state", "qty_diff",
             "qty_state", "tier", "verdict", "fee_item")} for r in sl]
    return {"ok": True, "carrier": carrier, "period": period, "price_card_rows": ncard,
            "total_bill": total_bill, "summary": summary, "accrual": accr, "counts": counts,
            "by_weight": by_weight, "detail_total": len(filt), "detail": view, "page": page, "size": size}


_PSTATE_CN = {"ok": "通过", "over": "多收", "under": "账单少收", "free": "账单未收·我方有利",
              "gap": "价卡缺·待确认", "na": "待补价卡"}
_QSTATE_CN = {"ok": "一致", "qtydiff": "不符", "miss": "金蝶查无", "na": "—"}
_VERDICT_CN = {"pass": "两轴通过", "price": "核价多收", "gap": "核价待补", "free": "账单未收",
               "qty": "核量存疑", "registered": "已登记", "doc_miss": "单号查无"}


@router.get("/api/logistics-review/export")
def review_export(request: Request, carrier: str = "迅鸽", period: str = ""):
    """导出该承运商本月复核结果 xlsx：费用项汇总 + 逐单/物料级复核明细（按重量承运商=物料级17列）。"""
    if not _perm(request):
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    res = review_result(request, carrier=carrier, period=period, group="all", page=1, size=1000000, q="")
    if isinstance(res, JSONResponse):
        return res
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from io import BytesIO
    from urllib.parse import quote
    HFONT = Font(bold=True, color="FFFFFF"); HFILL = PatternFill("solid", fgColor="1F6E8C")
    wb = Workbook()
    # sheet1 费用项汇总
    ws = wb.active; ws.title = "费用项汇总"
    ws.append(["承运商", carrier, "账期", period, "账单合计", res.get("total_bill")])
    ws.append([])
    sumcols = ["费用项", "账单额", "标准额(核价)", "差", "核价·一致", "多收", "缺价", "核量·一致", "不符", "查无"]
    ws.append(sumcols)
    for c in ws[3]:
        c.font = HFONT; c.fill = HFILL
    for f in res.get("summary", []):
        ws.append([f.get("fee_item"), f.get("bill"), f.get("std"), f.get("diff"),
                   f.get("price_ok"), f.get("over"), f.get("gap"), f.get("qty_ok"), f.get("qtydiff"), f.get("miss")])
    for i, w in enumerate([16, 12, 12, 10, 9, 7, 7, 9, 7, 7], 1):
        ws.column_dimensions[chr(64 + i)].width = w
    # sheet2 复核明细
    ws2 = wb.create_sheet("复核明细")
    from openpyxl.utils import get_column_letter
    if res.get("material"):
        # 接上原账单：每单费用分项(sub_fees)+运输方式(carrier_sub)+账单计入金额+账单计费重量，doc级仅首个物料行填
        with db._engine.connect() as c:
            braw = {r[0]: (r[1], r[2], r[3], r[4]) for r in c.execute(select(
                BL.c.doc_no, BL.c.carrier_sub, BL.c.sub_fees, BL.c.amount, BL.c.charge_wt).where(
                (BL.c.carrier == carrier) & (BL.c.period == period) & (BL.c.grain == "detail"))).all()}
        feekeys = []
        for _no, (cs, sf, amt, cw) in braw.items():
            if sf:
                try:
                    for k in json.loads(sf):
                        if k not in feekeys:
                            feekeys.append(k)
                except Exception:
                    pass
        feekeys.sort(key=lambda k: (0 if k == "运费" else 1, k))
        # 分组：ERP/金蝶数据、账单原始数据、复核数据分开配色；★=重点关注列。(组名, 组色, 列头浅色, doc级仅首行填, [(列头, 取值键)])
        groups = [
            ("归属·计提", "5E6B78", "E7ECEF", False, [("费用主体", "subject"), ("承运商", "carrier"),
                ("费用类型", "fee_item"), ("业务线", "bizline"), ("单据号", "doc_no")]),
            ("ERP·金蝶数据", "2E7D57", "DCEFE4", False, [("客户/需求部门", "party"), ("物料编码", "code"),
                ("物料名称", "name"), ("基本单位重量", "base_wt"), ("基本单位", "base_unit"), ("销售额", "sales")]),
            ("账单·%s原账单" % carrier, "B06A12", "FBF0DA", True, [("运输方式", "_cs")] +
                [(k, "_fee:" + k) for k in feekeys] + [("账单计入金额", "_amt"), ("账单计费重量", "_cw")]),
            ("复核数据", "B23B2E", "F8DDD8", False, [("运费(分摊)", "fee"), ("单位运费", "unit_fee"),
                ("账单数量(分摊)", "bill_qty"), ("账单单位", "bill_unit"), ("★换算系数", "conv"), ("★费比", "ratio")]),
        ]
        col = 1
        for name, gc, hc, _dl, cols in groups:
            span = len(cols)
            ws2.merge_cells(start_row=1, start_column=col, end_row=1, end_column=col + span - 1)
            gcell = ws2.cell(row=1, column=col, value=name)
            gcell.font = HFONT; gcell.fill = PatternFill("solid", fgColor=gc)
            gcell.alignment = Alignment(horizontal="center")
            for j, (h, _k) in enumerate(cols):
                hc2 = ws2.cell(row=2, column=col + j, value=h)
                hc2.font = Font(bold=True, color="B23B2E" if h.startswith("★") else "1B2733")
                hc2.fill = PatternFill("solid", fgColor=hc)
            col += span
        rownum = 3
        prev = None
        for r in res.get("detail", []):
            d0 = r.get("doc_no"); firstdoc = (d0 != prev)
            cs, sf, amt, cw = braw.get(d0, (None, None, None, None))
            sfd = {}
            if sf:
                try:
                    sfd = json.loads(sf)
                except Exception:
                    sfd = {}
            col = 1
            for name, gc, hc, doclvl, cols in groups:
                for (_h, k) in cols:
                    if doclvl and not firstdoc:
                        val = None
                    elif k == "_cs":
                        val = cs
                    elif k == "_amt":
                        val = amt
                    elif k == "_cw":
                        val = cw
                    elif k.startswith("_fee:"):
                        val = sfd.get(k[5:])
                    elif k == "ratio":
                        val = round(r["ratio"], 4) if r.get("ratio") is not None else None
                    else:
                        val = r.get(k)
                    ws2.cell(row=rownum, column=col, value=val)
                    col += 1
            prev = d0
            rownum += 1
        ws2.freeze_panes = "F3"
        widths = ([12, 14, 12, 10, 15] + [16, 12, 22, 11, 8, 10] + [14] + [10] * len(feekeys) + [13, 13] +
                  [10, 10, 10, 8, 10, 8])
        for i, w in enumerate(widths, 1):
            ws2.column_dimensions[get_column_letter(i)].width = w
    else:
        cols = ["金蝶单号", "快递/子类", "省", "计费重量", "账单数量", "金蝶数量", "账单金额", "标准费",
                "核价差", "核价", "核量", "归一态", "计价档"]
        ws2.append(cols)
        for r in res.get("detail", []):
            ws2.append([r.get("doc_no"), r.get("carrier_sub"), r.get("prov"), r.get("charge_wt"), r.get("qty"),
                        r.get("kd_qty"), r.get("amount"), r.get("std_amount"), r.get("price_diff"),
                        _PSTATE_CN.get(r.get("price_state"), r.get("price_state")),
                        _QSTATE_CN.get(r.get("qty_state"), r.get("qty_state")),
                        _VERDICT_CN.get(r.get("verdict"), r.get("verdict")), r.get("tier")])
        for c in ws2[1]:
            c.font = HFONT; c.fill = HFILL
        ws2.freeze_panes = "A2"
        for i, w in enumerate([15, 16, 8, 10, 10, 10, 11, 10, 9, 9, 8, 10, 14], 1):
            ws2.column_dimensions[get_column_letter(i)].width = w
    # sheet3+ 原账单（可追溯）：若已存原始账单，逐 sheet 原样附上，右侧接复核列（金蝶重量/换算系数/核量结论），按金蝶单号匹配
    import os as _os
    raw_fp = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "..", "raw_bills",
                           "%s_%s.json" % (carrier, period))
    if _os.path.exists(raw_fp):
        try:
            raw = json.load(open(raw_fp, encoding="utf-8"))
        except Exception:
            raw = None
        if raw:
            with db._engine.connect() as c:
                rv = {r[0]: (r[1], r[2], r[3]) for r in c.execute(select(
                    BL.c.doc_no, BL.c.kd_qty, BL.c.charge_wt, BL.c.qty_state).where(
                    (BL.c.carrier == carrier) & (BL.c.period == period) & (BL.c.grain == "detail"))).all()}
            for sh in raw.get("sheets", []):
                title = ("原账单-" + sh.get("name", ""))[:31]
                ws3 = wb.create_sheet(title)
                ws3.append(list(sh.get("header", [])) + ["金蝶出库重量kg", "换算系数(账单/金蝶)", "核量结论"])
                for c in ws3[1]:
                    c.font = HFONT; c.fill = HFILL
                ki = sh.get("kidx")
                for row in sh.get("rows", []):
                    no = str(row[ki]).strip() if (ki is not None and ki < len(row) and row[ki] not in (None, "")) else ""
                    kq, cw, qs = rv.get(no, (None, None, None))
                    conv = round(cw / kq, 3) if (cw and kq) else None
                    concl = "" if not no else ("重量一致" if qs == "ok" else "顺丰多报" if (conv and conv > 1) else "顺丰少报" if (conv and conv < 1) else "待核")
                    ws3.append(list(row) + [kq, conv, concl])
                ws3.freeze_panes = "A2"
    bio = BytesIO(); wb.save(bio)
    fn = "%s_%s_复核结果.xlsx" % (carrier, period)
    return Response(content=bio.getvalue(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename*=UTF-8''%s" % quote(fn)})
