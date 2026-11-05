# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-11-05 | Author: Kimi | Version: V2.1054
# Description: 支持 A-G 固定业务字段、动态费用列和末列备注；兼容 A-J，首页标准逐笔存在时只读首页。
# Date: 2026-11-04 | Author: Kimi | Version: V-draft
# Description: 独立解析 A-J 标准物流账单，支持原明细后追加的标准块、分项勾稽和按物理页取整报告。
import json
import math
import re
from io import BytesIO


_STANDARD_HEADERS = (
    "主体", "金蝶系统单据编号", "费用类型", "数量计算标准", "账单数量",
    "数量单位", "费用总额",
)
_QTY_STANDARDS = {"按重量", "按件数", "按箱数", "按体积", "按托", "按单", "按车", "按天", "按趟", "免核"}
_WEIGHT_UNITS = {
    "kg": 1.0, "㎏": 1.0, "公斤": 1.0, "千克": 1.0,
    "g": 0.001, "克": 0.001, "斤": 0.5,
    "t": 1000.0, "吨": 1000.0,
}
_TOTAL_HINT = re.compile(r"应付|合计|总运费|总金额|总费用|费用合计|总计|含税金额")
_SUMMARY = re.compile(r"^(?:合计|总计|小计|汇总)(?:\s|[:：]|$)")


def _s(v):
    return "" if v is None else str(v).strip()


def _h(v):
    return "".join(_s(v).split())


def _cell(row, col):
    return row[col] if col < len(row) else None


def _error(sheet, row, message):
    raise ValueError("%s!%s：%s" % (sheet, row, message))


def _number(value, label, sheet, row, required=False):
    if value is None or _s(value) == "":
        if required:
            _error(sheet, row, "%s不能为空" % label)
        return None
    if isinstance(value, bool):
        _error(sheet, row, "%s必须为数值" % label)
    try:
        number = float(value)
    except (TypeError, ValueError):
        _error(sheet, row, "%s必须为数值" % label)
    if not math.isfinite(number):
        _error(sheet, row, "%s必须为有限数值" % label)
    return number


def _split_docs(value):
    doc = _s(value)
    if not doc or doc in ("无金蝶单号", "无单据"):
        return "无单据"
    return "+".join(p for p in re.split(r"[+＋/,，、;；\s]+", doc) if p)


def _check_headers(header, start, sheet, row):
    for offset, expected in enumerate(_STANDARD_HEADERS):
        actual = _h(_cell(header, start + offset))
        if actual != expected:
            _error(sheet, row, "标准块第%s列表头应为%s，实际为%s" % (
                chr(ord("A") + offset), expected, actual or "空"))
    legacy = (_h(_cell(header, start + 7)).startswith("费用拆分1")
              and _h(_cell(header, start + 8)).startswith("费用拆分2")
              and _h(_cell(header, start + 9)) == "备注")
    if legacy:
        return {"fee_cols": [start + 7, start + 8], "fee_names": ["运费", "附加费"],
                "note_col": start + 9, "legacy": True}
    note_col = next((col for col in range(start + 7, len(header)) if _h(header[col]) == "备注"), None)
    if note_col is None:
        _error(sheet, row, "动态费用列之后必须有备注列")
    if note_col == start + 7:
        _error(sheet, row, "费用总额与备注之间至少需要一列费用拆分")
    fee_cols = list(range(start + 7, note_col))
    fee_names = []
    for index, col in enumerate(fee_cols, 1):
        name = _h(header[col])
        if not name:
            _error(sheet, row, "第%d个费用列表头不能为空" % index)
        if name in _STANDARD_HEADERS or name == "备注":
            _error(sheet, row, "费用列名称不能与固定业务字段重复：%s" % name)
        if name in fee_names:
            _error(sheet, row, "费用列表头重复：%s" % name)
        fee_names.append(name)
    return {"fee_cols": fee_cols, "fee_names": fee_names, "note_col": note_col, "legacy": False}


def _original_total_cols(header, start, stop):
    cols = []
    for col, value in enumerate(header):
        if start <= col < stop:
            continue
        if _TOTAL_HINT.search(_h(value)):
            cols.append(col)
    return cols[:1]


def _charge_weight(standard, qty, unit, sheet, row):
    if standard != "按重量" or qty is None:
        return None
    factor = _WEIGHT_UNITS.get(unit.lower())
    if factor is None:
        _error(sheet, row, "按重量时无法把数量单位%s换算为kg" % unit)
    return round(qty * factor, 6)


def _parse_sheet(ws, period, carrier, bill_src):
    start = None
    header_row = None
    header = None
    layout = None
    total_cols = []
    rows = []
    money = [0.0]
    fee_sums = []
    total_sums = []
    blank = 0
    for src_row, raw in enumerate(ws.iter_rows(values_only=True), 1):
        blank = 0 if any(v not in (None, "") for v in raw) else blank + 1
        if blank >= 300:
            break
        if start is None:
            texts = [_h(v) for v in raw]
            for col in range(len(texts) - 1):
                if texts[col] == "主体" and texts[col + 1] == "金蝶系统单据编号":
                    start, header_row, header = col, src_row, raw
                    layout = _check_headers(header, start, ws.title, src_row)
                    fee_sums = [0.0] * len(layout["fee_cols"])
                    total_cols = _original_total_cols(header, start, layout["note_col"] + 1)
                    total_sums = [0.0] * len(total_cols)
                    break
            continue
        block = tuple(_cell(raw, start + i) for i in range(7))
        fee_raw = [_cell(raw, col) for col in layout["fee_cols"]]
        note = _cell(raw, layout["note_col"])
        if not any(_s(v) for v in block + tuple(fee_raw) + (note,)):
            continue
        if any(_SUMMARY.match(_s(v)) for v in block[:2]):
            continue
        subject, doc, fee, standard, qty_raw, unit, amount_raw = block
        for value, label in ((subject, "主体"), (fee, "费用类型"), (standard, "数量计算标准"), (unit, "数量单位")):
            if not _s(value):
                _error(ws.title, src_row, "%s不能为空" % label)
        standard = _s(standard)
        if standard not in _QTY_STANDARDS:
            _error(ws.title, src_row, "未知数量计算标准%s" % standard)
        qty = _number(qty_raw, "账单数量", ws.title, src_row)
        amount = _number(amount_raw, "费用总额", ws.title, src_row)
        has_split = any(_s(value) for value in fee_raw)
        fee_values = []
        for name, value in zip(layout["fee_names"], fee_raw):
            number = _number(value, "费用列（%s）" % name, ws.title, src_row)
            fee_values.append(0.0 if number is None else number)
        split_total = sum(fee_values)
        if amount is None:
            if not has_split:
                _error(ws.title, src_row, "费用总额不能为空")
            amount = split_total
        if has_split and abs(amount - split_total) > 0.005:
            message = ("费用拆分不满足G=H+I：费用总额%s，运费%s，附加费%s" % (amount, fee_values[0], fee_values[1])
                       if layout["legacy"] else "费用拆分不满足总额勾稽：费用总额%s，全部费用列合计%s" % (amount, split_total))
            _error(ws.title, src_row, message)
        unit = _s(unit)
        sub_fees = dict(zip(layout["fee_names"], fee_values))
        row = {
            "period": period, "carrier": carrier, "grain": "detail",
            "subject": _s(subject), "doc_no": _split_docs(doc),
            "annot": _s(fee), "fee": _s(fee), "fee_item": _s(fee),
            "qty": qty, "unit": unit, "amount": amount,
            "charge_wt": _charge_weight(standard, qty, unit, ws.title, src_row),
            "qty_standard": standard,
            "sub_fees": json.dumps(sub_fees, ensure_ascii=False),
            "src_sheet": ws.title, "src_row": src_row, "note": _s(note),
            "bill_src": bill_src,
        }
        rows.append(row)
        money[0] += amount
        for i, value in enumerate(fee_values):
            fee_sums[i] += value
        for i, col in enumerate(total_cols):
            value = _cell(raw, col)
            if value is not None and not isinstance(value, bool):
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(value):
                    total_sums[i] += value
    if start is None:
        return None, None
    info = {
        "sheet": ws.title, "header_row": header_row, "rows": rows,
        "money": money + fee_sums, "fee_names": layout["fee_names"], "format": "aj" if layout["legacy"] else "dynamic",
        "total_cols": total_cols, "total_sums": total_sums,
        "total_names": [_h(_cell(header, col)) for col in total_cols],
    }
    return rows, info


def _open_book(data):
    import openpyxl

    src = BytesIO(data) if isinstance(data, (bytes, bytearray)) else data
    return openpyxl.load_workbook(src, read_only=True, data_only=True)


def _parse_book(spec, data, period=None):
    wb = _open_book(data)
    period = spec.get("period", "") if period is None else period
    carrier = spec.get("carrier", "")
    bill_src = _s(spec.get("standard_bill_src") or spec.get("bill_src") or "")
    infos, skipped, order = [], [], []
    try:
        primary_only = bool(wb.worksheets and wb.worksheets[0].title == "标准逐笔")
        for index, ws in enumerate(wb.worksheets):
            order.append(ws.title)
            if primary_only and index:
                skipped.append(ws.title)
                continue
            _, info = _parse_sheet(ws, period, carrier, bill_src)
            if info is None:
                skipped.append(ws.title)
            else:
                infos.append(info)
    finally:
        wb.close()
    return infos, skipped, bill_src, order


def parse_standard_bill(spec, data):
    """解析 A-J 或动态费用列标准账单；没有相邻标准表头时返回 None。"""
    infos, skipped, bill_src, _ = _parse_book(spec, data)
    if not infos:
        return None
    detail = [row for info in infos for row in info["rows"]]
    return {
        "detail": detail, "accrual": [], "skipped": skipped, "duplicates": [],
        "carrier": spec.get("carrier", ""), "period": spec.get("period", ""), "bill_src": bill_src,
    }


def standard_sheet_report(spec, data, period=""):
    """输出数据接入报告；首页为标准逐笔时，其余页只作原始追溯。"""
    infos, _, bill_src, order = _parse_book(spec, data, period)
    detail_sheets = []
    for info in infos:
        amount = round(info["money"][0], 2)
        money = [
            {"name": name, "sum": round(value, 2), "found": True}
            for name, value in zip(["费用总额"] + info["fee_names"], info["money"])
        ]
        if info["total_cols"]:
            total_sum = round(sum(info["total_sums"]), 2)
            total = {"name": "＋".join(info["total_names"]), "sum": total_sum, "how": "guess"}
            gap = round(amount - total_sum, 2)
            verdict = "ok" if abs(gap) < 0.01 else "gap"
        else:
            total, gap, verdict = None, None, "nocheck"
        rows = info["rows"]
        detail_sheets.append({
            "sheet": info["sheet"], "role": "detail", "n": len(rows),
            "docs": len({r["doc_no"] for r in rows if r.get("doc_no")}),
            "amount": amount, "money": money, "total": total, "gap": gap,
            "fee_names": info["fee_names"], "format": info["format"],
            "verdict": verdict, "header_row": info["header_row"],
        })
    by_sheet = {s["sheet"]: s for s in detail_sheets}
    primary_only = bool(order and order[0] == "标准逐笔")
    sheets = [by_sheet.get(name, {"sheet": name, "role": "source" if primary_only else "skip",
                                  "verdict": "source" if primary_only else "skip"}) for name in order]
    rank = {"gap": 2, "nocheck": 1, "ok": 0}
    verdicts = [s["verdict"] for s in detail_sheets]
    verdict = max(verdicts, key=lambda v: rank[v]) if verdicts else "nocheck"
    if detail_sheets and all(s["verdict"] in ("ok", "nocheck") for s in detail_sheets):
        verdict = "ok"
    return {
        "sheets": sheets, "n": sum(s["n"] for s in detail_sheets),
        "amount": round(sum(s["amount"] for s in detail_sheets), 2),
        "accrual": 0.0, "dup_rows": 0, "duplicates": [], "bill_src": bill_src,
        "verdict": verdict,
    }
