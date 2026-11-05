from collections import Counter
from io import BytesIO
import json

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from kernels import logistics_standard_intake as stdintake


BASE_HEADERS = ["主体", "金蝶系统单据编号", "费用类型", "数量计算标准", "账单数量", "数量单位", "费用总额"]
REVIEW_HEADERS = ["金蝶数量", "差异", "毛重比", "单位运费", "备注"]
MATERIAL_HEADERS = ["主体", "类型", "单号", "往来/领用部门", "物料分类", "物料编码", "物料名称",
                    "基本数量", "基本数量单位", "销售额", "承运商", "运输费用", "装卸费用", "合计单位费用", "费比"]
MATERIAL_FIELDS = ["subject", "form_label", "doc_no", "party", "material_group", "code", "name",
                   "base_qty", "base_unit", "sales", "carrier", "fee_tr", "fee_ld", "unit_fee", "ratio"]
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
        return [None, None, None, None, "无金蝶单号，不逐单核量"], "无金蝶单号"
    if doc is None:
        return [None, None, None, None, "当前账单行尚未取得金蝶复核结果，请先在逐单核价核量中刷新"], "待取数"
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
    difference = doc.get("q_diff") if kd is not None and _number(doc.get("q_diff")) else None
    ratio = None
    if kd and str(doc.get("bill_unit") or "") == "千克" and _number(doc.get("bill_amt")):
        ratio = round(float(doc["bill_amt"]) / float(kd), 4)
    unit_fee = doc.get("unit_fee") if _number(doc.get("unit_fee")) else None
    note = str(doc.get("mode_cn") or doc.get("mode_short") or "已取得金蝶复核结果")
    if check == "数量不符" and difference is not None:
        note += "；数量差异（账单-金蝶）：%s%s" % (difference, doc.get("bill_unit") or row.get("unit") or "")
    if confirmed:
        check = "人工确认"
        note += "；人工确认：%s %s" % (confirmed.get("by") or "", confirmed.get("at") or "")
    return [kd, difference, ratio, unit_fee, note.strip("；")], check


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
        _, check = review_values(row, doc, confirmed)
        checks[check] += 1
        values = [subject_fn(row), doc_no or "无金蝶单号", fee_fn(row), qty_standard_fn(row),
                  row.get("qty"), row.get("unit") or "", float(row.get("amount") or 0)]
        values += [fees.get(name, 0.0) for name in fee_names]
        values += [row.get("note") or ""]
        output.append(values)
    return fee_names, output, checks


def _match_document(row, documents):
    doc_no = str(row.get("doc_no") or "").split("+")[0]
    if not doc_no or doc_no in ("无单据", "无金蝶单号"):
        return None
    amount = float(row.get("amount") or 0)
    candidates = [doc for doc in documents if str(doc.get("doc_no") or "") == doc_no
                  and abs(float(doc.get("doc_fee") or 0) - amount) < 0.005]
    exact = [doc for doc in candidates if str(doc.get("fee_item") or "") == str(row.get("fee_item") or "")]
    candidates = exact or candidates
    if not candidates:
        return None
    target = row.get("charge_wt") if _number(row.get("charge_wt")) else row.get("qty")
    if _number(target):
        return min(candidates, key=lambda doc: abs(float(doc.get("bill_amt") or 0) - float(target)))
    return candidates[0]


def _standard_layout(ws, header_row):
    values = [cell.value for cell in ws[header_row]]
    for col in range(len(values) - 1):
        if stdintake._h(values[col]) == "主体" and stdintake._h(values[col + 1]) == "金蝶系统单据编号":
            return stdintake._check_headers(values, col, ws.title, header_row)
    return None


def enrich_detail_sheets(wb, documents, confirmations, carrier, period):
    enriched_sheets = []
    enriched_rows = 0
    checks = Counter()
    for ws in wb.worksheets:
        if ws.title == "标准逐笔" or ws.title.endswith("列表"):
            continue
        _, info = stdintake._parse_sheet(ws, period, carrier, "")
        if info is None:
            continue
        layout = _standard_layout(ws, info["header_row"])
        if layout is None:
            continue
        header_col = layout["note_col"] + 2
        for offset, name in enumerate(REVIEW_HEADERS):
            cell = ws.cell(info["header_row"], header_col + offset, name)
            cell.fill = PatternFill("solid", fgColor="FFF200")
            cell.font = Font(bold=True, color="000000")
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for row in info["rows"]:
            doc = _match_document(row, documents)
            confirmed = confirmations.get(str((doc or {}).get("doc_no") or str(row.get("doc_no") or "").split("+")[0]))
            values, check = review_values(row, doc, confirmed)
            checks[check] += 1
            for offset, value in enumerate(values):
                ws.cell(row["src_row"], header_col + offset, value)
                ws.cell(row["src_row"], header_col + offset).alignment = Alignment(vertical="top", wrap_text=True)
            enriched_rows += 1
        for offset, width in enumerate([14, 14, 12, 14, 60]):
            ws.column_dimensions[get_column_letter(header_col + offset)].width = width
        enriched_sheets.append(ws.title)
    return enriched_sheets, enriched_rows, checks


def add_document_sheets(wb, material_rows, form_labels, carrier):
    counts = {}
    for form, label in form_labels.items():
        title = label + "列表"
        if title in wb.sheetnames:
            del wb[title]
    for form, label in form_labels.items():
        rows = [row for row in material_rows if row.get("form") == form and row.get("carrier") == carrier]
        if not rows:
            continue
        rows.sort(key=lambda row: (str(row.get("doc_no") or ""), str(row.get("code") or "")))
        title = label + "列表"
        ws = wb.create_sheet(title[:31])
        ws.append(MATERIAL_HEADERS)
        for row in rows:
            ws.append([row.get(field) for field in MATERIAL_FIELDS])
        for cell in ws[1]:
            cell.fill = PatternFill("solid", fgColor="1F4E78")
            cell.font = Font(bold=True, color="FFFFFF")
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for values in ws.iter_rows(min_row=2):
            for cell in values:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = "A1:%s%d" % (get_column_letter(len(MATERIAL_HEADERS)), ws.max_row)
        for index, width in enumerate([28, 16, 22, 28, 16, 18, 36, 14, 16, 14, 14, 14, 14, 16, 12], 1):
            ws.column_dimensions[get_column_letter(index)].width = width
        counts[ws.title] = len(rows)
    return counts


def has_detail_source(data):
    if not data:
        return False
    wb = load_workbook(BytesIO(data), read_only=True)
    try:
        return len(wb.sheetnames) > 1
    finally:
        wb.close()


def build_workbook(rows, documents, confirmations, bucket_fn, subject_fn, fee_fn, qty_standard_fn,
                   carrier, period, source=None, material_rows=None, form_labels=None):
    fee_names, output, checks = build_rows(rows, documents, confirmations, bucket_fn, subject_fn, fee_fn, qty_standard_fn)
    headers = BASE_HEADERS + fee_names + ["备注"]
    wb = load_workbook(source) if source is not None else Workbook()
    if source is not None and "标准逐笔" in wb.sheetnames:
        del wb["标准逐笔"]
    ws = wb.create_sheet("标准逐笔", 0) if source is not None else wb.active
    ws.title = "标准逐笔"
    wb.properties.title = "%s %s 动态费用系统复核表" % (carrier, period)
    wb.properties.subject = "%s %s 原始明细及单据列表系统复核导出" % (carrier, period)
    wb.properties.description = "原始明细页含金蝶数量、差异、毛重比、单位运费、备注；附各类型单据列表；不代表已登记复核"
    ws.append(headers)
    for values in output:
        ws.append(values)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 32
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = "A1:%s%d" % (get_column_letter(len(headers)), max(1, ws.max_row))
    for index, width in enumerate([30, 26, 16, 16, 14, 12, 14] + [16] * len(fee_names) + [32], 1):
        ws.column_dimensions[get_column_letter(index)].width = width
    enriched_sheets, enriched_rows, detail_checks = enrich_detail_sheets(wb, documents, confirmations, carrier, period)
    document_sheets = add_document_sheets(wb, material_rows or [], form_labels or {}, carrier)
    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue(), {"rows": len(output), "fee_names": fee_names, "headers": headers, "checks": dict(checks),
                            "detail_checks": dict(detail_checks), "enriched_sheets": enriched_sheets,
                            "enriched_rows": enriched_rows, "document_sheets": document_sheets}
