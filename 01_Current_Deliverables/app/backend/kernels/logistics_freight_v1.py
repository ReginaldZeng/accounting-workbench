# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-26 | Author: Kimi | Version: V-draft（逐单运费 V1）
# Description: 六类运费原表按表头解析：销售成品装货、分步调拨、植物肉/小料卸货、全零/非零退货、金蝶销售其他/山姆。
#   合并单元格只补单号等元数据，绝不向下复制金额；保留重复原始行，输出明细重算、原表合计勾稽与追溯信息。
import datetime
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from io import BytesIO

import openpyxl
import xlrd
from openpyxl.utils import get_column_letter, range_boundaries

ZERO = Decimal("0")


def _text(value):
    if value is None:
        return ""
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _number(value):
    if value in (None, ""):
        return ZERO
    if isinstance(value, bool):
        raise InvalidOperation
    text = str(value).strip().replace(",", "").replace("，", "")
    if not text:
        return ZERO
    result = Decimal(text)
    if not result.is_finite():
        raise InvalidOperation
    return result


def _money(value):
    return format(value.normalize(), "f") if value else "0"


def _norm(value):
    return re.sub(r"[\s（）()【】\[\]_\-—/\\]", "", _text(value)).lower()


def _json_value(value):
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, Decimal):
        return format(value, "f")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class _Sheet:
    def __init__(self, name, rows, merged=None):
        self.name = name
        self.rows = rows
        self.merged = merged or {}


def _open_xlsx(data):
    wb = openpyxl.load_workbook(BytesIO(data), data_only=True, read_only=False)
    result = []
    for ws in wb.worksheets:
        rows = [[ws.cell(r, c).value for c in range(1, ws.max_column + 1)]
                for r in range(1, ws.max_row + 1)]
        merged = {}
        for merged_range in ws.merged_cells.ranges:
            c1, r1, c2, r2 = range_boundaries(str(merged_range))
            value = ws.cell(r1, c1).value
            for r in range(r1, r2 + 1):
                for c in range(c1, c2 + 1):
                    merged[(r, c)] = (value, str(merged_range))
        result.append(_Sheet(ws.title, rows, merged))
    return result


def _open_xls(data):
    wb = xlrd.open_workbook(file_contents=data)
    return [_Sheet(ws.name, [ws.row_values(r) for r in range(ws.nrows)]) for ws in wb.sheets()]


def _cell(sheet, row_index, col_index):
    try:
        value = sheet.rows[row_index][col_index]
    except IndexError:
        value = None
    merged = sheet.merged.get((row_index + 1, col_index + 1))
    return (merged[0] if merged else value), (merged[1] if merged else "")


def _header(sheet):
    aliases = {
        "doc_no": ("金蝶单据编号", "金蝶入库单号", "金蝶单号", "单据编号"),
        "amount": ("含税金额", "运费"),
        "carrier": ("物流商", "承运商"),
        "subject": ("销售组织", "费用主体", "主体"),
        "doc_date": ("装货日期", "卸货日期", "日期"),
        "material_code": ("物料编码",),
        "material_name": ("产品名称", "物料名称"),
        "customer": ("客户", "客户名称"),
        "sales_amount_tax": ("价税合计（本位币）", "价税合计"),
        "quantity": ("销售基本数量", "吨数(T)", "实发数量", "数量(箱）", "数量（KG）", "数量（千克）", "吨数"),
        "quantity_unit": ("库存单位", "基本单位"),
        "biz_line": ("业务线", "物料分组", "简称"),
    }
    best = None
    for r, values in enumerate(sheet.rows[:12]):
        normalized = [_norm(v) for v in values]
        found = {}
        for field, names in aliases.items():
            for c, heading in enumerate(normalized):
                if not heading:
                    continue
                for name in names:
                    target = _norm(name)
                    if heading == target or (name == "含税金额" and heading.startswith(target)):
                        found[field] = c
                        break
                if field in found:
                    break
        score = len(found) + (10 if "doc_no" in found and "amount" in found else 0)
        if best is None or score > best[0]:
            best = (score, r, found)
    if not best or best[0] < 12:
        return None
    return best[1], best[2]


def _issue(issues, severity, code, message, sheet, row=0, doc_no="", **extra):
    item = {"severity": severity, "code": code, "message": message,
            "sheet": sheet, "row": row, "doc_no": doc_no}
    item.update(extra)
    issues.append(item)


def _kind(sheet):
    name = sheet.name
    if "退货" in name:
        return "return"
    if "分步调拨" in name:
        return "transfer"
    if "装货" in name:
        return "sale_loading"
    if "卸货" in name:
        return "inbound"
    if "其他" in name or "山姆" in name:
        return "kingdee_sales"
    return "ignore"


def _fee_type(kind):
    return {"sale_loading": "销售成品装货费", "transfer": "分步调拨卸货费",
            "inbound": "原辅料卸货费", "kingdee_sales": "销售出库运费"}.get(kind, "")


def _default_biz_line(sheet, kind):
    if kind == "kingdee_sales":
        return "山姆" if "山姆" in sheet.name else "其他"
    if "小料" in sheet.name:
        return "小料"
    if "植物肉" in sheet.name:
        return "植物肉"
    return ""


def _is_total(sheet, r, columns):
    probes = [0, columns.get("doc_no", -1), columns.get("material_name", -1),
              columns.get("material_code", -1)]
    values = [_text(_cell(sheet, r, c)[0]) for c in probes if c >= 0]
    return (any(v in ("合计", "总计") for v in values)
            or any(_norm(v) == _norm("6%含税金额") for v in sheet.rows[r]))


def _row_empty(sheet, r):
    return not any(_text(v) for v in sheet.rows[r])


def _raw(sheet, header_row, r):
    headings = sheet.rows[header_row]
    out = {}
    for c, value in enumerate(sheet.rows[r]):
        key = _text(headings[c]) if c < len(headings) else ""
        key = key or ("列" + get_column_letter(c + 1))
        if key in out:
            key += "_" + get_column_letter(c + 1)
        out[key] = _json_value(value)
    return out


def _duplicate_key(sheet, header_row, r):
    values = [_json_value(v) for c, v in enumerate(sheet.rows[r])
              if _norm(sheet.rows[header_row][c]) != "序号"]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode("utf-8")).hexdigest()


def _parse_return(sheet, header_row, columns, issues):
    total = ZERO
    meaningful = False
    for r in range(header_row + 1, len(sheet.rows)):
        if _row_empty(sheet, r) or _is_total(sheet, r, columns):
            continue
        try:
            amount = _number(_cell(sheet, r, columns["amount"])[0])
        except (InvalidOperation, ValueError):
            amount = Decimal("1")
        if amount or any(_text(_cell(sheet, r, c)[0]) for c in range(len(sheet.rows[r])) if c != columns["amount"]):
            meaningful = True
        total += amount
    if not meaningful and total == 0:
        _issue(issues, "info", "zero_return_skipped", "全零退货页已跳过", sheet.name)
        return [], ZERO, False
    _issue(issues, "error", "nonzero_return_blocked", "退货页存在非零退货费用，V1 不支持，已阻断", sheet.name)
    return [], ZERO, True


def _parse_sheet(sheet, context, issues):
    kind = _kind(sheet)
    if kind == "ignore":
        return [], ZERO, False
    located = _header(sheet)
    if not located:
        _issue(issues, "error", "header_not_found", "按表头找不到单号列和金额/运费列", sheet.name)
        return [], ZERO, True
    header_row, columns = located
    if kind == "return":
        return _parse_return(sheet, header_row, columns, issues)
    output = []
    source_total = None
    duplicates = {}
    current = {"doc_no": "", "carrier": "", "subject": "", "doc_date": "", "merged_range": ""}
    for r in range(header_row + 1, len(sheet.rows)):
        if _row_empty(sheet, r):
            continue
        if _is_total(sheet, r, columns):
            try:
                value = _number(_cell(sheet, r, columns["amount"])[0])
            except (InvalidOperation, ValueError):
                _issue(issues, "warning", "source_total_invalid", "原表合计不是可识别数字", sheet.name, r + 1)
            else:
                source_total = (source_total or ZERO) + value
            continue
        raw_doc, raw_doc_merge = _cell(sheet, r, columns["doc_no"])
        raw_amount = _cell(sheet, r, columns["amount"])[0]
        meaningful = bool(_text(raw_doc) or _text(raw_amount) or _text(_cell(sheet, r, columns.get("material_code", 0))[0]))
        if not meaningful:
            continue
        fingerprint = _duplicate_key(sheet, header_row, r)
        if fingerprint in duplicates:
            _issue(issues, "warning", "duplicate_rows", "与原表第 %d 行完全相同，两行均保留，请人工确认" % duplicates[fingerprint],
                   sheet.name, r + 1, _text(raw_doc), duplicate_of=duplicates[fingerprint])
        else:
            duplicates[fingerprint] = r + 1
        for field in current:
            if field in columns:
                value, merged_range = _cell(sheet, r, columns[field])
                if _text(value):
                    current[field] = _text(value)
                if field == "doc_no" and merged_range:
                    current["merged_range"] = merged_range
        doc_no = current["doc_no"] if kind == "kingdee_sales" else _text(raw_doc)
        if not doc_no:
            _issue(issues, "error", "missing_doc_no", "缺单号", sheet.name, r + 1)
        carrier = _text(_cell(sheet, r, columns["carrier"])[0]) if "carrier" in columns else ""
        carrier = carrier or current["carrier"] or str(context.get("carrier") or "").strip()
        subject = _text(_cell(sheet, r, columns["subject"])[0]) if "subject" in columns else ""
        subject = subject or current["subject"] or str(context.get("subject") or "").strip()
        biz_line = (_text(_cell(sheet, r, columns["biz_line"])[0]) if "biz_line" in columns else "")
        biz_line = biz_line or str(context.get("biz_line") or "").strip() or _default_biz_line(sheet, kind)
        fee_type = str(context.get("fee_type") or "").strip() or _fee_type(kind)
        for field, value, code, label in (("carrier", carrier, "missing_carrier", "承运商"),
                                          ("subject", subject, "missing_subject", "主体"),
                                          ("fee_type", fee_type, "missing_fee_type", "费用类型")):
            if not value:
                _issue(issues, "error", code, "缺" + label, sheet.name, r + 1, doc_no)
        try:
            amount = _number(raw_amount)
        except (InvalidOperation, ValueError):
            amount = ZERO
            _issue(issues, "error", "invalid_amount", "金额不是可识别数字", sheet.name, r + 1, doc_no)
        # 金蝶续行沿用单号/承运商等元数据，但运费永远只读当前原始行（空即零），不复制首行运费。
        merged_ranges = [raw_doc_merge] if raw_doc_merge else []
        quantity = None
        if "quantity" in columns:
            try:
                quantity = _number(_cell(sheet, r, columns["quantity"])[0])
            except (InvalidOperation, ValueError):
                _issue(issues, "warning", "invalid_quantity", "吨数不是可识别数字", sheet.name, r + 1, doc_no)
        sales_amount = None
        if "sales_amount_tax" in columns:
            try:
                sales_amount = _number(_cell(sheet, r, columns["sales_amount_tax"])[0])
            except (InvalidOperation, ValueError):
                _issue(issues, "warning", "invalid_sales_amount", "销售额不是可识别数字", sheet.name, r + 1, doc_no)
        quantity_unit = _text(_cell(sheet, r, columns["quantity_unit"])[0]) if "quantity_unit" in columns else ""
        if not quantity_unit and "quantity" in columns:
            heading = _norm(sheet.rows[header_row][columns["quantity"]])
            quantity_unit = "t" if "吨数" in heading else ("kg" if "kg" in heading or "千克" in heading else ("箱" if "箱" in heading else ""))
        output.append({
            "doc_no": doc_no, "carrier": carrier, "subject": subject, "biz_line": biz_line,
            "fee_type": fee_type, "amount": _money(amount),
            "doc_date": (_text(_cell(sheet, r, columns["doc_date"])[0]) if "doc_date" in columns else current["doc_date"]),
            "material_code": (_text(_cell(sheet, r, columns["material_code"])[0]) if "material_code" in columns else ""),
            "material_name": (_text(_cell(sheet, r, columns["material_name"])[0]) if "material_name" in columns else ""),
            "customer": (_text(_cell(sheet, r, columns["customer"])[0]) if "customer" in columns else ""),
            "sales_amount_tax": (_money(sales_amount) if sales_amount is not None else None),
            "quantity": (_money(quantity) if quantity is not None else None), "quantity_unit": quantity_unit,
            "sheet": sheet.name, "row": r + 1, "raw": _raw(sheet, header_row, r),
            "merged_range": ";".join(dict.fromkeys(merged_ranges)),
        })
    return output, source_total, True


def parse_workbook(data, filename="", context=None):
    context = context or {}
    data = bytes(data)
    try:
        sheets = _open_xls(data) if data[:8] == bytes.fromhex("d0cf11e0a1b11ae1") else _open_xlsx(data)
    except Exception as exc:
        raise ValueError("无法读取 Excel 原件：%s" % exc) from exc
    lines, issues, sheet_summaries = [], [], []
    total = ZERO
    source_total = ZERO
    has_source_total = False
    recognized = 0
    for sheet in sheets:
        parsed, sheet_source_total, used = _parse_sheet(sheet, context, issues)
        if not used:
            continue
        recognized += 1
        lines.extend(parsed)
        amount = sum((_number(x["amount"]) for x in parsed), ZERO)
        total += amount
        if sheet_source_total is not None:
            source_total += sheet_source_total
            has_source_total = True
        sheet_summaries.append({"sheet": sheet.name, "kind": _kind(sheet), "line_count": len(parsed),
                                "amount": _money(amount),
                                "source_total": (_money(sheet_source_total) if sheet_source_total is not None else None)})
    if not recognized:
        _issue(issues, "error", "unsupported_workbook", "没有识别到六类 V1 费用明细页", "", 0)
    if not lines and not any(x["severity"] == "error" for x in issues):
        _issue(issues, "error", "no_lines", "没有可保存的费用明细", "", 0)
    difference = total - source_total if has_source_total else ZERO
    if has_source_total and difference:
        _issue(issues, "warning", "reconciliation_diff",
               "明细重算与原表合计不一致：差额 %s" % _money(difference), "", 0)
    error_count = sum(x["severity"] == "error" for x in issues)
    category_amounts = {}
    for line in lines:
        name = line["fee_type"]
        category_amounts[name] = category_amounts.get(name, ZERO) + _number(line["amount"])
    summary = {
        "status": "blocked" if error_count else "ready",
        "template": "、".join(dict.fromkeys(x["kind"] for x in sheet_summaries)),
        "category_amounts": {k: _money(v) for k, v in category_amounts.items()},
        "line_count": len(lines), "doc_count": len({x["doc_no"] for x in lines if x["doc_no"]}),
        "total_amount": _money(total), "source_total": (_money(source_total) if has_source_total else None),
        "difference": (_money(difference) if has_source_total else "0"),
        "issue_count": len(issues), "error_count": error_count,
        "warning_count": sum(x["severity"] == "warning" for x in issues),
        "sheets": sheet_summaries, "sha256": hashlib.sha256(data).hexdigest(),
    }
    return {"summary": summary, "lines": lines, "issues": issues,
            "period": str(context.get("period") or "").strip(),
            "carrier": str(context.get("carrier") or "").strip(),
            "subject": str(context.get("subject") or "").strip(),
            "biz_line": str(context.get("biz_line") or "").strip()}


_GENERIC_CARRIER_WORDS = {"物流", "运输", "供应链", "速运", "快递", "货运", "公司", "有限公司", "集团"}


def normalize_carrier_name(value, catalog):
    raw = _text(value)
    text = _norm(raw)
    if not text:
        return {"status": "missing", "carrier": "", "raw": raw, "candidates": []}
    if "自提" in text or "到付" in text:
        return {"status": "exact", "carrier": "自提" if "自提" in text else "到付", "raw": raw, "candidates": []}
    exact = {}
    for item in catalog:
        name = _text(item.get("name") or item.get("short"))
        if not name:
            continue
        names = [name, item.get("full_name") or item.get("full"), *(item.get("aliases") or [])]
        for alias in names:
            key = _norm(alias)
            if key:
                exact.setdefault(key, set()).add(name)
    if text in exact and len(exact[text]) == 1:
        return {"status": "exact", "carrier": next(iter(exact[text])), "raw": raw, "candidates": []}
    candidates = set()
    for item in catalog:
        name = _text(item.get("name") or item.get("short"))
        names = [name, item.get("full_name") or item.get("full"), *(item.get("aliases") or [])]
        for alias in names:
            key = _norm(alias)
            if len(key) < 2 or key in _GENERIC_CARRIER_WORDS:
                continue
            if key in text or (len(text) >= 2 and text not in _GENERIC_CARRIER_WORDS and text in key):
                candidates.add(name)
                break
    if len(candidates) == 1:
        return {"status": "keyword", "carrier": next(iter(candidates)), "raw": raw, "candidates": sorted(candidates)}
    return {"status": "conflict" if candidates else "unknown", "carrier": "", "raw": raw,
            "candidates": sorted(candidates)}


def normalize_parsed_carriers(parsed, catalog):
    issues = parsed.setdefault("issues", [])
    for line in parsed.get("lines") or []:
        result = normalize_carrier_name(line.get("carrier"), catalog)
        if result["status"] in ("missing", "exact"):
            continue
        if result["status"] == "keyword":
            line["carrier"] = result["carrier"]
            _issue(issues, "info", "carrier_normalized", "物流商已按唯一关键字标准化：%s → %s" % (result["raw"], result["carrier"]),
                   line.get("sheet"), line.get("row"), line.get("doc_no"), raw_carrier=result["raw"])
        else:
            line["carrier"] = ""
            message = "物流商无法识别，请补充标准物流商：%s" % result["raw"]
            if result["candidates"]:
                message += "；候选：" + "、".join(result["candidates"])
            _issue(issues, "error", "unknown_carrier" if result["status"] == "unknown" else "conflict_carrier",
                   message, line.get("sheet"), line.get("row"), line.get("doc_no"),
                   raw_carrier=result["raw"], candidates=result["candidates"])
    carriers = {x.get("carrier") for x in parsed.get("lines") or [] if x.get("carrier")}
    parsed["carrier"] = next(iter(carriers)) if len(carriers) == 1 else ""
    summary = parsed.get("summary") or {}
    parsed["summary"] = summary
    summary.update(status="blocked" if any(x.get("severity") == "error" for x in issues) else "ready",
                   issue_count=len(issues), error_count=sum(x.get("severity") == "error" for x in issues),
                   warning_count=sum(x.get("severity") == "warning" for x in issues))
    return parsed


def _filename_period(filename, selected_period):
    name = _text(filename)
    match = re.search(r"(20\d{2})[年._-]?(\d{1,2})月", name)
    if match:
        return "%s-%02d" % (match.group(1), int(match.group(2)))
    match = re.search(r"(?<!\d)(1[0-2]|0?[1-9])月(?:份)?", name)
    if match and re.match(r"^20\d{2}-", selected_period or ""):
        return "%s-%02d" % (selected_period[:4], int(match.group(1)))
    return ""


def _date_period(value):
    text = _text(value)
    match = re.match(r"^(20\d{2})-(\d{2})-", text)
    if match:
        return "%s-%s" % (match.group(1), match.group(2))
    match = re.match(r"^(20\d{2})年(\d{1,2})月", text)
    if match:
        return "%s-%02d" % (match.group(1), int(match.group(2)))
    if re.fullmatch(r"\d+(?:\.0)?", text):
        number = int(float(text))
        if 20000 <= number <= 80000:
            from openpyxl.utils.datetime import from_excel
            return from_excel(number).strftime("%Y-%m")
    return ""


def validate_parsed_period(parsed, filename, selected_period):
    issues = parsed.setdefault("issues", [])
    mismatches = []
    file_period = _filename_period(filename, selected_period)
    if file_period and file_period != selected_period:
        mismatches.append("文件名识别为 %s" % file_period)
    row_periods = {}
    for line in parsed.get("lines") or []:
        period = _date_period(line.get("doc_date"))
        if period and period != selected_period:
            row_periods.setdefault(period, []).append(line.get("doc_no"))
    for period, docs in sorted(row_periods.items()):
        mismatches.append("%d 行单据日期属于 %s（如 %s）" % (len(docs), period, docs[0] or "未标单号"))
    if mismatches:
        _issue(issues, "error", "period_mismatch",
               "账期不一致：当前选择 %s；%s。传错期间请取消批次后重选；确属跨期须人工确认后继续。" %
               (selected_period, "；".join(mismatches)), "", 0, selected_period=selected_period,
               file_period=file_period, detail_periods=sorted(row_periods))
    summary = parsed.get("summary") or {}
    parsed["summary"] = summary
    summary.update(status="blocked" if any(x.get("severity") == "error" for x in issues) else "ready",
                   issue_count=len(issues), error_count=sum(x.get("severity") == "error" for x in issues),
                   warning_count=sum(x.get("severity") == "warning" for x in issues))
    return parsed
