# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-09-28 | Author: Claude Opus 4.8 | Version: V2.639
# Description: 【物流账单复核】路由（新工具线在后端的落点）。复核=核价(合同价格卡)×核量(金蝶数量)→归一态，接计提。
#   端点：取数说明读 / 导入合同价格卡 / 价格卡读 / 上传账单解析落中间表 / 接金蝶回填数量 / 出复核结果(费用项汇总+逐单)。
#   算法在 kernels/logistics_price + logistics_review + logistics_intake；表在 kernels/logistics_review_store；金蝶只读走 kingdee_client。
#   pilot=迅鸽（取数说明已种子，价格卡导《附件二》，核量取 XQLCK 出库数量）。计提行=复用 logistics_bills 聚合，另接。
import json
import calendar
from datetime import datetime

from fastapi import APIRouter, Request
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
@router.post("/api/logistics-review/kingdee-qty")
def review_kingdee_qty(request: Request, carrier: str = "迅鸽", period: str = ""):
    u = _perm(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    # 取本批 detail 单号前缀（迅鸽=XQLCK），按月拉出库单聚合数量（货品，剔包装）
    with db._engine.connect() as c:
        docs = [r[0] for r in c.execute(select(BL.c.doc_no).where(
            (BL.c.carrier == carrier) & (BL.c.period == period) & (BL.c.grain == "detail"))).all()]
    prefixes = sorted({"".join(ch for ch in (d.split("+")[0]) if ch.isalpha()) for d in docs if d and d != "无单据"})
    prefixes = [p for p in prefixes if p]
    if not period or "-" not in period:
        return JSONResponse({"ok": False, "msg": "缺账期"}, status_code=400)
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
    note = "；".join(x for x in [
        ("需求部门 " + b["dept"]) if b.get("dept") else "",
        ("来源 " + b["source"]) if b.get("source") else "",
        ("日期 " + b["date"]) if b.get("date") else "", (b.get("note") or "")] if x)
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
    # 取物料明细
    by_form = {}
    for no in docfee:
        if no == "（无单号）":
            continue
        pre = "".join(ch for ch in no if ch.isalpha())
        for form in _FORM_BY_PREFIX.get(pre, ["SAL_OUTSTOCK"])[:1]:
            by_form.setdefault(form, set()).add(no)
    mats = {}
    try:
        s, conf = kc.login()
        mats = _fetch_doc_materials(s, conf, by_form)
    except Exception:
        mats = {}
    rows = []
    for no, g in docfee.items():
        fee = round(g["运费"], 2)
        biz = _bizline_of(g["annot"])
        lines = mats.get(no) or []
        kgsum = sum(float(m["基本数量"] or 0) for m in lines if m.get("基本数量") not in (None, ""))
        if not lines:
            rows.append({"subject": g["subject"], "fee_item": g["fee_item"], "bizline": biz, "doc_no": no,
                         "party": g["dept"] if mode == "other" else "", "code": "", "name": "（金蝶无此单据物料）",
                         "baseqty": None, "baseunit": "", "fee": fee, "unitfee": None, "ratio": None,
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
                "subject": g["subject"], "fee_item": g["fee_item"], "bizline": biz, "doc_no": no,
                "party": (m.get("往来") or "") if mode == "sales" else (g["dept"] or ""),
                "code": m.get("编码"), "name": m.get("名称"), "baseqty": bq, "baseunit": m.get("基本单位"),
                "fee": fline, "unitfee": round(fline / bq, 4) if bq else None,
                "ratio": round(fline / sales_amt, 4) if sales_amt else None,
                "reg_id": g.get("reg_id")})
    if q:
        rows = [r for r in rows if q in (r["doc_no"] or "") or q in (r["name"] or "") or q in (r["code"] or "")]
    total = round(sum(r["fee"] for r in rows), 2)
    page = max(1, int(page))
    return {"ok": True, "period": period, "mode": mode, "count": len(rows), "total": total,
            "doc_count": len(docfee), "rows": rows[(page - 1) * size: page * size], "page": page, "size": size}


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
