# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-26 | Author: Kimi | Version: V-draft（逐单运费 V1）
# Description: 多文件 preview 独立服务端草稿、批次查询/确认/撤回/恢复/原件下载；替换经 preview 表单并在确认事务内完成。
#   读权限沿用 logistics_upload；所有写操作服务端校验敏感权限 logistics_freight_write 并写审计，不调用金蝶或 V2。
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import Response

from core import JSONResponse, _require_perm, db
from kernels import logistics_freight_v1 as parser
from kernels import logistics_freight_v1_store as store
from routers import logistics_review as lrv

router = APIRouter(prefix="/api/logistics-freight/v1", tags=["logistics-freight-v1"])


def _read_user(request):
    return _require_perm(request, "logistics_upload")


def _write_user(request):
    return _require_perm(request, "logistics_freight_write")


def _forbidden():
    return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)


def _error(exc):
    return JSONResponse({"ok": False, "msg": str(exc), **exc.payload},
                        status_code=getattr(exc, "status_code", 409))


def _enrich_brand(parsed, combo, matmap, bp_err):
    for line in parsed.get("lines") or []:
        customer = str(line.get("customer") or "").strip()
        code = str(line.get("material_code") or "").strip()
        mapped = combo.get((customer, code)) or matmap.get(code) or {}
        line["brand"] = mapped.get("brand") or ""
    if bp_err:
        parsed.setdefault("issues", []).append({"severity": "warning", "code": "brand_mapping_unavailable",
                                               "message": bp_err, "sheet": "", "row": 0, "doc_no": ""})
        parsed["summary"]["warning_count"] = int(parsed["summary"].get("warning_count") or 0) + 1
        parsed["summary"]["issue_count"] = int(parsed["summary"].get("issue_count") or 0) + 1
    return parsed


def _with_overlaps(batch, operation="new", replace_id=None):
    if batch and batch.get("status") in ("draft", "active", "withdrawn"):
        exclude = [replace_id] if operation == "replace" and replace_id else []
        batch["overlaps"] = store.overlaps(db._engine, batch["id"], exclude)
        batch["summary"]["overlap_count"] = len(batch["overlaps"])
    return batch


@router.get("/batches")
def batches_read(request: Request, period: str = "", status: str = "", carrier: str = ""):
    if not _read_user(request):
        return _forbidden()
    return {"ok": True, "batches": store.list_batches(db._engine, period, status, carrier)}


@router.get("/carriers")
def carriers_read(request: Request):
    if not _read_user(request):
        return _forbidden()
    rows = db.list_logi_suppliers() or []
    return {"ok": True, "carriers": [{"name": str(x.get("short") or "").strip(),
                                      "full_name": str(x.get("full") or "").strip()} for x in rows if x.get("short")]}


@router.post("/bulk-supplement")
async def bulk_supplement(request: Request):
    user = _write_user(request)
    if not user:
        return _forbidden()
    try:
        body = await request.json()
        ids = [int(x) for x in body.get("batch_ids") or []]
    except Exception:
        return JSONResponse({"ok": False, "msg": "请求格式不正确"}, status_code=400)
    try:
        batches = store.supplement_many(db._engine, ids, body.get("carrier"))
    except store.BatchError as exc:
        return _error(exc)
    db.audit(user["name"], "逐单运费V1-批量指定物流商", ",".join(map(str, ids)), str(body.get("carrier") or ""))
    return {"ok": True, "batches": [_with_overlaps(x, x.get("operation"), x.get("replace_id")) for x in batches]}


@router.post("/preview")
async def preview(request: Request):
    user = _write_user(request)
    if not user:
        return _forbidden()
    form = await request.form()
    operation = str(form.get("operation") or "new").strip()
    if operation not in ("new", "replace"):
        return JSONResponse({"ok": False, "msg": "operation 只能是 new/replace"}, status_code=400)
    try:
        replace_id = int(form.get("replace_id") or 0)
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "msg": "replace_id 必须是批次编号"}, status_code=400)
    if operation == "replace" and not replace_id:
        return JSONResponse({"ok": False, "msg": "替换必须传 replace_id"}, status_code=400)
    uploads = [x for key in ("files", "file") for x in form.getlist(key) if hasattr(x, "read")]
    if not uploads:
        return JSONResponse({"ok": False, "msg": "未收到文件"}, status_code=400)
    if operation == "replace" and len(uploads) != 1:
        return JSONResponse({"ok": False, "msg": "一次替换只能上传一个文件"}, status_code=400)
    context = {key: str(form.get(key) or "").strip() for key in ("period", "carrier", "subject", "biz_line", "fee_type")}
    period = context["period"]
    if len(period) != 7 or period[4] != "-" or not (period[:4] + period[5:]).isdigit():
        return JSONResponse({"ok": False, "msg": "账期必须是 YYYY-MM"}, status_code=400)
    catalog = [{"name": str(x.get("short") or "").strip(), "full_name": str(x.get("full") or "").strip()}
               for x in (db.list_logi_suppliers() or []) if x.get("short")]
    if context["carrier"] and all(x["name"] != context["carrier"] for x in catalog):
        catalog.append({"name": context["carrier"], "full_name": ""})
    combo, matmap, bp_err = lrv._bp_mapping()
    output = []
    for upload in uploads:
        filename = str(upload.filename or "未命名.xlsx")
        content = await upload.read()
        try:
            parsed = parser.parse_workbook(content, filename, context)
            parsed = parser.validate_parsed_period(parsed, filename, period)
            parsed = parser.normalize_parsed_carriers(parsed, catalog)
            parsed = _enrich_brand(parsed, combo, matmap, bp_err)
        except Exception as exc:
            parsed = {"summary": {"status": "failed", "line_count": 0, "doc_count": 0,
                                  "total_amount": "0", "error_count": 1},
                      "lines": [], "issues": [{"severity": "error", "code": "parse_failed",
                                               "message": str(exc), "sheet": "", "row": 0, "doc_no": ""}],
                      **context}
        try:
            batch = store.create_draft(db._engine, filename, content, parsed, user["name"], operation, replace_id)
        except store.BatchError as exc:
            return _error(exc)
        batch = _with_overlaps(batch, operation, replace_id)
        output.append(batch)
        db.audit(user["name"], "逐单运费V1-预览", str(batch["id"]),
                 "%s；%s；明细 %s 行" % (filename, batch["status"], batch["summary"].get("line_count", 0)))
    return {"ok": True, "batches": output}


@router.get("/batches/{batch_id}")
def batch_read(batch_id: int, request: Request):
    if not _read_user(request):
        return _forbidden()
    batch = store.get_batch(db._engine, batch_id)
    if not batch:
        return JSONResponse({"ok": False, "msg": "批次不存在"}, status_code=404)
    batch = _with_overlaps(batch, batch.get("operation"), batch.get("replace_id"))
    return {"ok": True, "batch": batch}


@router.post("/batches/{batch_id}")
async def batch_update(batch_id: int, request: Request):
    user = _write_user(request)
    if not user:
        return _forbidden()
    try:
        body = await request.json()
    except Exception:
        body = {}
    operation = body.get("operation")
    if operation not in ("cancel", "supplement", "period_override"):
        return JSONResponse({"ok": False, "msg": "仅支持 operation=cancel/supplement/period_override"}, status_code=400)
    try:
        if operation == "supplement":
            batch = store.supplement_draft(db._engine, batch_id, body.get("carrier"),
                                           body.get("subject"), body.get("biz_line"))
        elif operation == "period_override":
            batch = store.override_period(db._engine, batch_id)
        else:
            batch = store.cancel_draft(db._engine, batch_id, user["name"])
    except store.BatchError as exc:
        return _error(exc)
    action = {"supplement": "补充草稿信息", "period_override": "确认跨期", "cancel": "取消预览"}[operation]
    detail = {"supplement": "补充承运商/主体/业务线", "period_override": "人工确认账期不一致仍继续", "cancel": "取消草稿，不影响有效 V1"}[operation]
    db.audit(user["name"], "逐单运费V1-" + action, str(batch_id), detail)
    return {"ok": True, "batch": _with_overlaps(batch, batch.get("operation"), batch.get("replace_id"))}


@router.post("/batches/{batch_id}/confirm")
async def batch_confirm(batch_id: int, request: Request):
    user = _write_user(request)
    if not user:
        return _forbidden()
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        batch = store.confirm_batch(db._engine, batch_id, user["name"], bool(body.get("allow_overlap")))
    except store.BatchError as exc:
        db.audit(user["name"], "逐单运费V1-确认失败", str(batch_id), str(exc))
        return _error(exc)
    db.audit(user["name"], "逐单运费V1-确认", str(batch_id),
             "允许重叠" if body.get("allow_overlap") else "无重叠放行")
    return {"ok": True, "batch": batch}


@router.post("/batches/{batch_id}/withdraw")
async def batch_withdraw(batch_id: int, request: Request):
    user = _write_user(request)
    if not user:
        return _forbidden()
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        batch = store.withdraw_batch(db._engine, batch_id, user["name"], body.get("reason"))
    except store.BatchError as exc:
        return _error(exc)
    db.audit(user["name"], "逐单运费V1-撤回", str(batch_id), str(body.get("reason") or "").strip())
    return {"ok": True, "batch": batch}


@router.post("/batches/{batch_id}/restore")
async def batch_restore(batch_id: int, request: Request):
    user = _write_user(request)
    if not user:
        return _forbidden()
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        batch = store.restore_batch(db._engine, batch_id, user["name"], bool(body.get("allow_overlap")))
    except store.BatchError as exc:
        return _error(exc)
    db.audit(user["name"], "逐单运费V1-恢复", str(batch_id), "")
    return {"ok": True, "batch": batch}


@router.get("/batches/{batch_id}/file")
def batch_file(batch_id: int, request: Request):
    if not _read_user(request):
        return _forbidden()
    item = store.get_file(db._engine, batch_id)
    if not item:
        return JSONResponse({"ok": False, "msg": "原件不存在"}, status_code=404)
    filename = item["filename"]
    media_type = ("application/vnd.ms-excel" if filename.lower().endswith(".xls")
                  else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return Response(content=bytes(item["content"]), media_type=media_type, headers={
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename),
        "X-Content-SHA256": item["sha256"],
    })
