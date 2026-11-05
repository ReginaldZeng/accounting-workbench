from collections import Counter
from io import BytesIO
import json

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


BASE_HEADERS = ["主体", "金蝶系统单据编号", "费用类型", "数量计算标准", "账单数量", "数量单位", "费用总额"]
REVIEW_HEADERS = ["账单备注", "金蝶数量", "check", "复核备注"]
META_KEYS = {"标准", "核价差", "公式", "箱型", "箱子尺寸", "跨越计价"}


def _json(value):
    try:
        return json.loads(value) if isinstance(value, str) else (value or {})
    except (TypeError, ValueError):
        return {}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _fee_values(row, bucket_fn):
    amount = float(row.get("amount") or 0)
    source = _json(row.get("sub_fees"))
    fees = {}
    for name, value in source.items():
        if name in META_KEYS or name.startswith("__") or name.startswith("含税金额") or not _number(value):
            continue
        fees[str(name)] = float(value)
    if fees and abs(amount - sum(fees.values())) <= 0.005:
        return fees
    buckets = bucket_fn(amount, row.get("fee_item"), row.get("sub_fees"))
    return {"运费": float(buckets["tr"]), "装卸费": float(buckets["ld"]), "其他费用": float(buckets["ot"])}


def review_values(row, doc=None, confirmed=None):
    doc_no = str(row.get("doc_no") or "").strip()
    if not doc_no or doc_no in ("无单据", "无金蝶单号"):
        return None, "无金蝶单号", "无金蝶单号，不逐单核量"
    if doc is None:
        return None, "待取数", "当前账单行尚未取得金蝶复核结果，请先在逐单核价核量中刷新"
    qty_state = str(doc.get("qty_state") or "")
    state = str(doc.get("state") or "")
    kd = doc.get("kd_sum") if qty_state not in ("na", "miss", "price") and state != "miss" else None
    if not _number(kd):
        kd = None
    if qty_state == "ok" or (not qty_state and state == "ok"):
        check = "通过"
    elif qty_state == "na" or state == "info":
        check, kd = "免核", None
    elif qty_state == "miss" or state == "miss":
        check, kd = "金蝶无此单", None
    elif qty_state == "qtydiff" or state == "qtydiff":
        check = "数量不符"
    else:
        check = "待确认"
    note = str(doc.get("mode_cn") or doc.get("mode_short") or "已取得金蝶复核结果")
    if check == "数量不符" and _number(doc.get("q_diff")):
        note += "；数量差异（账单-金蝶）：%s%s" % (doc["q_diff"], doc.get("bill_unit") or row.get("unit") or "")
    if confirmed:
        check = "人工确认"
        note += "；人工确认：%s %s" % (confirmed.get("by") or "", confirmed.get("at") or "")
    return kd, check, note.strip("；")


def build_rows(rows, documents, confirmations, bucket_fn, subject_fn, fee_fn, qty_standard_fn):
    fee_rows = [_fee_values(row, bucket_fn) for row in rows]
    fee_names = []
    for fees in fee_rows:
        for name in fees:
            if name not in fee_names:
                fee_names.append(name)
    docs_by_lid = {doc.get("lid"): doc for doc in documents if doc.get("lid") is not None}
    output = []
    checks = Counter()
    for row, fees in zip(rows, fee_rows):
        doc_no = str(row.get("doc_no") or "").strip()
        first_doc = doc_no.split("+")[0]
        doc = docs_by_lid.get(row.get("id"))
        confirmed = confirmations.get(str((doc or {}).get("doc_no") or first_doc))
        kd, check, review_note = review_values(row, doc, confirmed)
        checks[check] += 1
        values = [subject_fn(row), doc_no or "无金蝶单号", fee_fn(row), qty_standard_fn(row),
                  row.get("qty"), row.get("unit") or "", float(row.get("amount") or 0)]
        values += [fees.get(name, 0.0) for name in fee_names]
        values += [row.get("note") or "", kd, check, review_note]
        output.append(values)
    return fee_names, output, checks


def build_workbook(rows, documents, confirmations, bucket_fn, subject_fn, fee_fn, qty_standard_fn,
                   carrier, period, source=None):
    fee_names, output, checks = build_rows(rows, documents, confirmations, bucket_fn, subject_fn, fee_fn, qty_standard_fn)
    headers = BASE_HEADERS + fee_names + REVIEW_HEADERS
    wb = load_workbook(source) if source is not None else Workbook()
    if source is not None and "标准逐笔" in wb.sheetnames:
        del wb["标准逐笔"]
    ws = wb.create_sheet("标准逐笔", 0) if source is not None else wb.active
    ws.title = "标准逐笔"
    wb.properties.title = "%s %s 动态费用系统复核表" % (carrier, period)
    wb.properties.subject = "%s %s 系统复核导出" % (carrier, period)
    wb.properties.description = "动态费用列；金蝶数量、check、复核备注来自当前系统复核结果；不代表已登记复核"
    ws.append(headers)
    for values in output:
        ws.append(values)
    review_start = len(headers) - 2
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2F6B54" if cell.column >= review_start else "1F4E78")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 32
    check_col = len(headers) - 1
    fills = {"通过": "E2F0D9", "数量不符": "FCE4D6", "金蝶无此单": "FCE4D6",
             "待确认": "FFF2CC", "待取数": "FFF2CC", "人工确认": "DDEBF7",
             "免核": "E7E6E6", "无金蝶单号": "E7E6E6"}
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        check = ws.cell(row[0].row, check_col)
        check.fill = PatternFill("solid", fgColor=fills.get(check.value, "FFFFFF"))
        check.font = Font(bold=True)
    ws.freeze_panes = "A2"
    last_col = get_column_letter(len(headers))
    ws.auto_filter.ref = "A1:%s%d" % (last_col, max(1, ws.max_row))
    widths = [30, 26, 16, 16, 14, 12, 14] + [16] * len(fee_names) + [32, 14, 16, 70]
    for index, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(index)].width = width
    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue(), {"rows": len(output), "fee_names": fee_names, "headers": headers, "checks": dict(checks)}
