# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-26 | Author: Kimi Code | Version: V-draft
# Description: 逐单费用统一读模型：V1 生效批次与 V2 已复核账单按 单号+承运商+费用类型 选择；待复核只提示不双计。
from sqlalchemy import select

from kernels import logistics_freight_v1_store as v1store
from kernels import logistics_review_store as billstore

BUCKETS = (("tr", "运费"), ("ld", "装卸费"), ("ot", "其他"))


def v1_fee_type(raw):
    text = str(raw or "")
    if any(x in text for x in ("装货", "卸货", "装卸")):
        return "装卸费"
    if any(x in text for x in ("运费", "运输")):
        return "运费"
    if "仓储" in text:
        return "仓储费"
    if "操作" in text:
        return "操作费"
    if "包材" in text:
        return "包材费"
    return "其他"


def _amount(value):
    return float(value or 0)


def _source(entry, version, period, amount, **extra):
    item = {"version": version, "period": period or "", "amount": amount}
    item.update({k: v for k, v in extra.items() if v not in (None, "")})
    entry["sources"].append(item)


def resolve_fee_lines(v1_rows, bill_rows, sign_rows, line_buckets, eff_subject=None, eff_fee=None):
    signed = {(str(x.get("carrier") or ""), str(x.get("period") or ""))
              for x in sign_rows if x.get("status") == "signed"}
    out = {}

    def get(doc, carrier, fee_type):
        key = (str(doc or "").strip(), str(carrier or "").strip(), fee_type)
        if not key[0] or not key[1]:
            return None
        return out.setdefault(key, {"doc_no": key[0], "carrier": key[1], "fee_type": key[2],
                                    "v1": 0.0, "v2": 0.0, "pending": 0.0,
                                    "has_v1": False, "has_v2": False, "sources": [],
                                    "subjects": [], "biz_lines": [], "fee_items": []})

    for row in v1_rows:
        fee_type = v1_fee_type(row.get("fee_type"))
        entry = get(row.get("doc_no"), row.get("carrier"), fee_type)
        if entry is None:
            continue
        amount = _amount(row.get("amount"))
        entry["v1"] += amount
        entry["has_v1"] = True
        for field, target in (("subject", "subjects"), ("biz_line", "biz_lines"), ("fee_type", "fee_items")):
            value = str(row.get(field) or "")
            if value and value not in entry[target]:
                entry[target].append(value)
        _source(entry, "v1", row.get("period"), amount, batch_id=row.get("batch_id"),
                filename=row.get("filename"), sheet=row.get("sheet"), row=row.get("row_number"))

    for row in bill_rows:
        doc_field = str(row.get("doc_no") or "")
        docs = [x.strip() for x in doc_field.split("+") if x.strip() and x.strip() != "无单据"]
        if not docs:
            continue
        carrier = str(row.get("carrier") or "")
        period = str(row.get("period") or "")
        reviewed = row.get("review_mode") == "register" or (carrier, period) in signed
        amount = _amount(row.get("amount"))
        buckets = line_buckets(amount, row.get("fee_item"), row.get("sub_fees"))
        raw_fee = str(row.get("fee_item") or "")
        zero_type = ("装卸费" if any(x in raw_fee for x in ("装货", "卸货", "装卸")) else
                     "其他" if any(x in raw_fee for x in ("仓储", "操作", "包材")) else "运费")
        for bucket, fee_type in BUCKETS:
            bucket_amount = _amount(buckets.get(bucket)) / len(docs)
            if abs(bucket_amount) < 0.0000005 and (abs(amount) > 0.0000005 or fee_type != zero_type):
                continue
            for doc in docs:
                entry = get(doc, carrier, fee_type)
                if entry is None:
                    continue
                version = "v2" if reviewed else "pending"
                entry[version] += bucket_amount
                if reviewed:
                    entry["has_v2"] = True
                subject = eff_subject(row) if eff_subject else row.get("subject")
                fee_item = eff_fee(row) if eff_fee else row.get("fee_item")
                for value, target in ((subject, "subjects"), (row.get("bizline"), "biz_lines"), (fee_item, "fee_items")):
                    value = str(value or "")
                    if value and value not in entry[target]:
                        entry[target].append(value)
                _source(entry, version, period, bucket_amount, bill_id=row.get("id"),
                        sheet=row.get("src_sheet"), row=row.get("src_row"))

    for entry in out.values():
        entry["has_effective"] = entry["has_v1"] or entry["has_v2"]
        entry["effective_version"] = "v2" if entry["has_v2"] else ("v1" if entry["has_v1"] else "unsubmitted")
        entry["effective"] = entry["v2"] if entry["has_v2"] else entry["v1"]
        entry["difference"] = entry["v2"] - entry["v1"] if entry["has_v1"] and entry["has_v2"] else None
    return out


def document_view(resolved):
    docs = {}
    bucket_of = dict((name, bucket) for bucket, name in BUCKETS)
    for entry in resolved.values():
        doc = docs.setdefault(entry["doc_no"], {"ok": 0.0, "pending": 0.0, "tr": 0.0, "ld": 0.0, "ot": 0.0,
                                                "v1": 0.0, "v2": 0.0, "difference": 0.0, "has_difference": False,
                                                "v1_keys": 0, "v2_keys": 0, "keys": 0, "parts": {}, "sources": []})
        doc["keys"] += 1
        doc["v1"] += entry["v1"]
        doc["v2"] += entry["v2"]
        doc["pending"] += entry["pending"]
        doc["v1_keys"] += int(entry["has_v1"])
        doc["v2_keys"] += int(entry["has_v2"])
        if entry["difference"] is not None:
            doc["difference"] += entry["difference"]
            doc["has_difference"] = True
        doc["ok"] += entry["v2"]
        bucket = bucket_of[entry["fee_type"]] if entry["fee_type"] in bucket_of else "ot"
        doc[bucket] += entry["effective"]
        for source in entry["sources"]:
            doc["sources"].append(dict(source, fee_type=entry["fee_type"], carrier=entry["carrier"]))
            if source["version"] == "pending" or source["version"] != entry["effective_version"]:
                continue
            key = (entry["carrier"], source.get("period") or "", source["version"] == "v2", source["version"])
            part = doc["parts"].setdefault(key, {"tr": 0.0, "ld": 0.0, "ot": 0.0})
            part[bucket] += _amount(source.get("amount"))
        for source in entry["sources"]:
            if source["version"] != "pending":
                continue
            key = (entry["carrier"], source.get("period") or "", False, "pending")
            part = doc["parts"].setdefault(key, {"tr": 0.0, "ld": 0.0, "ot": 0.0})
            part[bucket] += _amount(source.get("amount"))
    for doc in docs.values():
        doc["effective_version"] = ("unsubmitted" if not doc["v1_keys"] and not doc["v2_keys"] else
                                    "mixed" if doc["v1_keys"] and doc["v2_keys"] else
                                    "v2" if doc["v2_keys"] else "v1")
        doc["unsubmitted"] = not doc["v1_keys"] and not doc["v2_keys"]
    return docs


def load_resolved(engine, period, line_buckets, eff_subject=None, eff_fee=None, include_register=True):
    v1stmt = select(v1store.lines, v1store.batches.c.period, v1store.batches.c.filename).join(
        v1store.batches, v1store.batches.c.id == v1store.lines.c.batch_id).where(v1store.batches.c.status == "active")
    blstmt = select(billstore.bill_lines).where((billstore.bill_lines.c.grain == "detail") &
                                                (billstore.bill_lines.c.doc_no != "") &
                                                (billstore.bill_lines.c.doc_no != "无单据") &
                                                billstore.bill_lines.c.doc_no.isnot(None))
    if not include_register:
        blstmt = blstmt.where(billstore.bill_lines.c.review_mode == "audit")
    sgstmt = select(billstore.review_sign)
    if period:
        v1stmt = v1stmt.where(v1store.batches.c.period == period)
        blstmt = blstmt.where(billstore.bill_lines.c.period == period)
        sgstmt = sgstmt.where(billstore.review_sign.c.period == period)
    with engine.connect() as cx:
        v1rows = [dict(x) for x in cx.execute(v1stmt).mappings().all()]
        blrows = [dict(x) for x in cx.execute(blstmt).mappings().all()]
        signs = [dict(x) for x in cx.execute(sgstmt).mappings().all()]
    return resolve_fee_lines(v1rows, blrows, signs, line_buckets, eff_subject, eff_fee)
