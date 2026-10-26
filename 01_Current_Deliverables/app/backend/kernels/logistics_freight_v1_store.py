# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-26 | Author: Kimi | Version: V-draft（逐单运费 V1）
# Description: 独立批次/原件/明细三表；一文件一批次，原件保留 BLOB 与 SHA256，金额使用 Numeric(18,6)。
#   确认、替换、撤回、恢复均在本模块事务内完成；替换失败时旧 active 批次不变，不读写 V2 物流表。
import datetime
import hashlib
import json
from decimal import Decimal

from sqlalchemy import (Column, Index, Integer, LargeBinary, MetaData, Numeric, String,
                        Table, Text, UniqueConstraint, insert, select, update, or_)
from sqlalchemy.dialects.mysql import LONGTEXT

md = MetaData()

batches = Table(
    "logistics_freight_v1_batch", md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("status", String(12), nullable=False),
    Column("filename", String(240), nullable=False),
    Column("operation", String(12), nullable=False),
    Column("period", String(7), nullable=False),
    Column("replace_id", Integer),
    Column("replaced_by", Integer),
    Column("carrier", String(120)),
    Column("subject", String(120)),
    Column("biz_line", String(80)),
    Column("summary_json", Text().with_variant(LONGTEXT(), "mysql")),
    Column("issues_json", Text().with_variant(LONGTEXT(), "mysql")),
    Column("created_by", String(50)), Column("created_at", String(20)),
    Column("confirmed_by", String(50)), Column("confirmed_at", String(20)),
    Column("withdrawn_by", String(50)), Column("withdrawn_at", String(20)),
    Column("withdraw_reason", String(500)),
    Column("restored_by", String(50)), Column("restored_at", String(20)),
    Index("ix_lfv1_batch_status", "status"),
    Index("ix_lfv1_batch_replace", "replace_id"),
)

files = Table(
    "logistics_freight_v1_file", md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("batch_id", Integer, nullable=False),
    Column("filename", String(240), nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("size", Integer, nullable=False),
    Column("content", LargeBinary(2 ** 32 - 1), nullable=False),
    Column("created_by", String(50)), Column("created_at", String(20)),
    UniqueConstraint("batch_id", name="uq_lfv1_file_batch"),
    Index("ix_lfv1_file_sha", "sha256"),
)

lines = Table(
    "logistics_freight_v1_line", md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("batch_id", Integer, nullable=False),
    Column("line_no", Integer, nullable=False),
    Column("doc_no", String(120), nullable=False),
    Column("carrier", String(120), nullable=False),
    Column("subject", String(120), nullable=False),
    Column("biz_line", String(80)),
    Column("fee_type", String(80), nullable=False),
    Column("amount", Numeric(18, 6), nullable=False),
    Column("doc_date", String(30)),
    Column("material_code", String(80)),
    Column("material_name", String(240)),
    Column("quantity", Numeric(18, 6)),
    Column("sheet", String(120), nullable=False),
    Column("row_number", Integer, nullable=False),
    Column("raw_json", Text().with_variant(LONGTEXT(), "mysql")),
    Column("merged_range", String(500)),
    UniqueConstraint("batch_id", "line_no", name="uq_lfv1_line_batch_no"),
    Index("ix_lfv1_line_overlap", "doc_no", "carrier", "fee_type"),
    Index("ix_lfv1_line_batch", "batch_id"),
)

TABLES = [batches, files, lines]
STATUSES = {"draft", "active", "replaced", "withdrawn", "failed"}


class BatchError(ValueError):
    status_code = 409

    def __init__(self, message, **payload):
        super().__init__(message)
        self.status_code = int(payload.pop("status_code", self.status_code))
        self.payload = payload


class OverlapError(BatchError):
    pass


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _json(value):
    return json.dumps(value if value is not None else [], ensure_ascii=False, allow_nan=False)


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def _decimal(value):
    if value in (None, ""):
        return Decimal("0")
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _batch_dict(row, detail=None):
    if row is None:
        return None
    out = dict(row)
    out["summary"] = _loads(out.pop("summary_json", None), {})
    out["issues"] = _loads(out.pop("issues_json", None), [])
    if detail is not None:
        out["lines"] = detail
    return out


def list_batches(engine, period="", status="", carrier=""):
    stmt = select(batches)
    if period:
        stmt = stmt.where(batches.c.period == period)
    if status:
        stmt = stmt.where(batches.c.status == status)
    if carrier:
        stmt = stmt.where(batches.c.carrier == carrier)
    with engine.connect() as cx:
        rows = cx.execute(stmt.order_by(batches.c.id.desc())).mappings().all()
    return [_batch_dict(row) for row in rows]


def get_batch(engine, batch_id, with_lines=True):
    with engine.connect() as cx:
        row = cx.execute(select(batches).where(batches.c.id == int(batch_id))).mappings().first()
        detail = None
        if row and with_lines:
            detail = [_line_dict(r) for r in cx.execute(
                select(lines).where(lines.c.batch_id == int(batch_id)).order_by(lines.c.line_no)
            ).mappings().all()]
    return _batch_dict(row, detail) if row else None


def _line_dict(row):
    out = dict(row)
    for key in ("amount", "quantity"):
        if out.get(key) is not None:
            out[key] = format(out[key], "f")
    out["row"] = out.pop("row_number")
    out["raw"] = _loads(out.pop("raw_json", None), {})
    return out


def get_file(engine, batch_id):
    with engine.connect() as cx:
        row = cx.execute(select(files).where(files.c.batch_id == int(batch_id))).mappings().first()
    return dict(row) if row else None


def create_draft(engine, filename, content, parsed, operator, operation="new", replace_id=None):
    content = bytes(content)
    operation = "replace" if operation == "replace" else "new"
    replace_id = int(replace_id) if operation == "replace" else None
    now = _now()
    summary = dict(parsed.get("summary") or {})
    issues = list(parsed.get("issues") or [])
    failed = summary.get("status") == "failed"
    status = "failed" if failed else "draft"
    with engine.begin() as cx:
        if replace_id is not None:
            old = cx.execute(select(batches.c.status).where(batches.c.id == replace_id)).first()
            if not old or old[0] != "active":
                raise BatchError("被替换批次不存在或已不是 active", status_code=404)
        bid = cx.execute(insert(batches).values(
            status=status, filename=str(filename or "未命名")[:240], operation=operation,
            period=str(parsed.get("period") or "")[:7], replace_id=replace_id, carrier=str(parsed.get("carrier") or "")[:120],
            subject=str(parsed.get("subject") or "")[:120], biz_line=str(parsed.get("biz_line") or "")[:80],
            summary_json=_json(summary), issues_json=_json(issues),
            created_by=str(operator or "")[:50], created_at=now,
        )).inserted_primary_key[0]
        cx.execute(insert(files).values(
            batch_id=bid, filename=str(filename or "未命名")[:240],
            sha256=hashlib.sha256(content).hexdigest(), size=len(content), content=content,
            created_by=str(operator or "")[:50], created_at=now,
        ))
        for no, line in enumerate(parsed.get("lines") or [], 1):
            cx.execute(insert(lines).values(
                batch_id=bid, line_no=no, doc_no=str(line.get("doc_no") or "")[:120],
                carrier=str(line.get("carrier") or "")[:120], subject=str(line.get("subject") or "")[:120],
                biz_line=str(line.get("biz_line") or "")[:80], fee_type=str(line.get("fee_type") or "")[:80],
                amount=_decimal(line.get("amount")), doc_date=str(line.get("doc_date") or "")[:30],
                material_code=str(line.get("material_code") or "")[:80],
                material_name=str(line.get("material_name") or "")[:240],
                quantity=(_decimal(line.get("quantity")) if line.get("quantity") not in (None, "") else None),
                sheet=str(line.get("sheet") or "")[:120], row_number=int(line.get("row") or 0),
                raw_json=_json(line.get("raw") or {}), merged_range=str(line.get("merged_range") or "")[:500],
            ))
    return get_batch(engine, bid)


def _overlap_in_conn(cx, batch_id, exclude_ids=()):
    keys = cx.execute(select(lines.c.doc_no, lines.c.carrier, lines.c.fee_type).where(
        lines.c.batch_id == int(batch_id)).distinct()).all()
    wanted = {(str(d), str(c), str(f)) for d, c, f in keys if str(d).strip()}
    if not wanted:
        return []
    docs = sorted({key[0] for key in wanted})
    active = cx.execute(select(
        lines.c.batch_id, lines.c.doc_no, lines.c.carrier, lines.c.fee_type, batches.c.filename
    ).join(batches, batches.c.id == lines.c.batch_id).where(
        (batches.c.status == "active") & (lines.c.doc_no.in_(docs)) &
        (~lines.c.batch_id.in_([int(batch_id), *[int(x) for x in exclude_ids]]))
    )).all()
    grouped = {}
    for other_id, doc, carrier, fee_type, filename in active:
        key = (str(doc), str(carrier), str(fee_type))
        if key not in wanted:
            continue
        hit = grouped.setdefault(key, {"doc_no": key[0], "carrier": key[1], "fee_type": key[2], "batches": []})
        if not any(x["id"] == other_id for x in hit["batches"]):
            hit["batches"].append({"id": other_id, "filename": filename})
    return list(grouped.values())


def overlaps(engine, batch_id, exclude_ids=()):
    with engine.connect() as cx:
        return _overlap_in_conn(cx, batch_id, exclude_ids)


def confirm_batch(engine, batch_id, operator, allow_overlap=False):
    now = _now()
    with engine.begin() as cx:
        row = cx.execute(select(batches).where(batches.c.id == int(batch_id))).mappings().first()
        if not row:
            raise BatchError("批次不存在", status_code=404)
        if row["status"] != "draft":
            raise BatchError("只有 draft 批次可以确认")
        issues = _loads(row["issues_json"], [])
        blockers = [x for x in issues if x.get("severity") == "error"]
        if blockers:
            raise BatchError("批次有阻断问题，不能确认", issues=blockers)
        replace_id = row["replace_id"] if row["operation"] == "replace" else None
        if replace_id:
            old = cx.execute(select(batches.c.status).where(batches.c.id == int(replace_id))).first()
            if not old or old[0] != "active":
                raise BatchError("被替换批次已变化，请重新 preview", status_code=409)
        hits = _overlap_in_conn(cx, batch_id, [replace_id] if replace_id else [])
        if hits and not allow_overlap:
            raise OverlapError("与 active 批次存在同单号+承运商+费用类型重叠", overlaps=hits)
        changed = cx.execute(update(batches).where(
            (batches.c.id == int(batch_id)) & (batches.c.status == "draft")
        ).values(status="active", confirmed_by=str(operator or "")[:50], confirmed_at=now)).rowcount
        if changed != 1:
            raise BatchError("批次状态已变化，请刷新")
        if replace_id:
            changed = cx.execute(update(batches).where(
                (batches.c.id == int(replace_id)) & (batches.c.status == "active")
            ).values(status="replaced", replaced_by=int(batch_id))).rowcount
            if changed != 1:
                raise BatchError("替换旧批次失败，事务已回滚")
    return get_batch(engine, batch_id)


def withdraw_batch(engine, batch_id, operator, reason):
    reason = str(reason or "").strip()
    if not reason:
        raise BatchError("撤回原因必填", status_code=400)
    now = _now()
    with engine.begin() as cx:
        changed = cx.execute(update(batches).where(
            (batches.c.id == int(batch_id)) & (batches.c.status == "active")
        ).values(status="withdrawn", withdrawn_by=str(operator or "")[:50], withdrawn_at=now,
                 withdraw_reason=reason[:500])).rowcount
        if changed != 1:
            exists = cx.execute(select(batches.c.id).where(batches.c.id == int(batch_id))).first()
            raise BatchError("批次不存在" if not exists else "只有 active 批次可以撤回",
                             status_code=404 if not exists else 409)
    return get_batch(engine, batch_id)


def restore_batch(engine, batch_id, operator, allow_overlap=False):
    now = _now()
    with engine.begin() as cx:
        row = cx.execute(select(batches.c.status).where(batches.c.id == int(batch_id))).first()
        if not row:
            raise BatchError("批次不存在", status_code=404)
        if row[0] != "withdrawn":
            raise BatchError("只有 withdrawn 批次可以恢复")
        hits = _overlap_in_conn(cx, batch_id)
        if hits and not allow_overlap:
            raise OverlapError("恢复会与 active 批次重叠", overlaps=hits)
        changed = cx.execute(update(batches).where(
            (batches.c.id == int(batch_id)) & (batches.c.status == "withdrawn")
        ).values(status="active", restored_by=str(operator or "")[:50], restored_at=now)).rowcount
        if changed != 1:
            raise BatchError("批次状态已变化，请刷新")
    return get_batch(engine, batch_id)


def cancel_draft(engine, batch_id, operator):
    now = _now()
    with engine.begin() as cx:
        changed = cx.execute(update(batches).where(
            (batches.c.id == int(batch_id)) & (batches.c.status.in_(["draft", "failed"]))
        ).values(status="withdrawn", withdrawn_by=str(operator or "")[:50], withdrawn_at=now,
                 withdraw_reason="取消预览")).rowcount
        if changed != 1:
            raise BatchError("只有待确认或解析失败的草稿可以取消")
    return get_batch(engine, batch_id)


def supplement_draft(engine, batch_id, carrier="", subject="", biz_line=""):
    values = {"carrier": str(carrier or "").strip(), "subject": str(subject or "").strip(),
              "biz_line": str(biz_line or "").strip()}
    supplied = {k: v for k, v in values.items() if v}
    if not supplied:
        raise BatchError("至少补充一项承运商、主体或业务线", status_code=400)
    with engine.begin() as cx:
        row = cx.execute(select(batches).where(batches.c.id == int(batch_id))).mappings().first()
        if not row:
            raise BatchError("批次不存在", status_code=404)
        if row["status"] != "draft":
            raise BatchError("只有待确认草稿可以补充信息")
        cx.execute(update(batches).where(batches.c.id == int(batch_id)).values(**supplied))
        for field, value in supplied.items():
            cx.execute(update(lines).where((lines.c.batch_id == int(batch_id)) &
                                           (or_(lines.c[field] == "", lines.c[field].is_(None)))).values(**{field: value}))
        issues = _loads(row["issues_json"], [])
        codes = {"carrier": "missing_carrier", "subject": "missing_subject", "biz_line": "missing_biz_line"}
        resolved_codes = {codes[k] for k in supplied}
        issues = [x for x in issues if x.get("code") not in resolved_codes]
        summary = _loads(row["summary_json"], {})
        errors = [x for x in issues if x.get("severity") == "error"]
        summary.update(status="blocked" if errors else "ready", issue_count=len(issues),
                       error_count=len(errors), warning_count=sum(x.get("severity") == "warning" for x in issues))
        cx.execute(update(batches).where(batches.c.id == int(batch_id)).values(
            summary_json=_json(summary), issues_json=_json(issues)))
    return get_batch(engine, batch_id)


def supplement_many(engine, batch_ids, carrier):
    ids = sorted({int(x) for x in batch_ids})
    carrier = str(carrier or "").strip()
    if not ids or not carrier:
        raise BatchError("请选择批次并指定承运商", status_code=400)
    with engine.begin() as cx:
        rows = cx.execute(select(batches).where(batches.c.id.in_(ids))).mappings().all()
        if len(rows) != len(ids):
            raise BatchError("有批次不存在，请刷新后重试", status_code=404)
        if any(x["status"] != "draft" for x in rows):
            raise BatchError("只能批量指定待确认批次；已生效或已撤回批次未修改")
        for row in rows:
            bid = row["id"]
            cx.execute(update(batches).where(batches.c.id == bid).values(carrier=carrier))
            cx.execute(update(lines).where((lines.c.batch_id == bid) &
                                           (or_(lines.c.carrier == "", lines.c.carrier.is_(None)))).values(carrier=carrier))
            issues = [x for x in _loads(row["issues_json"], []) if x.get("code") != "missing_carrier"]
            summary = _loads(row["summary_json"], {})
            errors = [x for x in issues if x.get("severity") == "error"]
            summary.update(status="blocked" if errors else "ready", issue_count=len(issues),
                           error_count=len(errors), warning_count=sum(x.get("severity") == "warning" for x in issues))
            cx.execute(update(batches).where(batches.c.id == bid).values(
                summary_json=_json(summary), issues_json=_json(issues)))
    return [get_batch(engine, bid) for bid in ids]
