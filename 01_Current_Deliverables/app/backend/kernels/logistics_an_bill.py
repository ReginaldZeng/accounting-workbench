from io import BytesIO
import math
import re

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill


HEADERS = ["主体", "金蝶系统单据编号", "费用类型", "数量计算标准", "账单数量", "数量单位", "费用总额",
           "运费", "送货/入仓费", "提货费", "卸货费", "上楼费", "其他", "备注"]
FIELDS = ["subject", "doc_no", "fee_item", "qty_standard", "qty", "unit", "amount",
          "freight", "delivery", "pickup", "unload", "upstairs", "other", "note"]
SUMMARY = re.compile(r"^(?:合计|总计|小计|汇总)(?:\s|[:：]|$)")


def _h(value):
    return "".join(str(value or "").split())


def _number(value, sheet, row, label, required=False):
    if value is None or str(value).strip() == "":
        if required:
            raise ValueError("%s 第 %d 行：%s不能为空" % (sheet, row, label))
        return None
    if isinstance(value, bool):
        raise ValueError("%s 第 %d 行：%s必须为数值" % (sheet, row, label))
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("%s 第 %d 行：%s必须为数值" % (sheet, row, label)) from exc
    if not math.isfinite(number):
        raise ValueError("%s 第 %d 行：%s必须为有限数值" % (sheet, row, label))
    return number


def _standard_start(row):
    for col, value in enumerate(row[:-1]):
        if _h(value) == "主体" and _h(row[col + 1]) == "金蝶系统单据编号":
            return col
    return None


def _component_map(sheet, start, header):
    header_rows = header if isinstance(header, list) else [header]
    if start == 0 and [_h(v) for v in header_rows[-1][:len(HEADERS)]] == [_h(v) for v in HEADERS]:
        return dict(zip(FIELDS[7:13], range(7, 13)))
    if sheet == "4.山姆":
        names = {"freight": "干线", "delivery": "送货费", "pickup": "提货费", "unload": "交仓卸货费",
                 "upstairs": None, "other": "暂存费"}
    elif sheet == "5.外仓":
        names = {"freight": "运费", "delivery": None, "pickup": None, "unload": "合计", "upstairs": None, "other": None}
    else:
        names = {"freight": "运费", "delivery": "送货/入仓费", "pickup": "提货费", "unload": "卸货费",
                 "upstairs": "上楼费", "other": "其他"}
    out = {}
    for field, expected in names.items():
        if expected is None:
            out[field] = None
            continue
        hits = [i for row in reversed(header_rows) for i in range(start) if expected in _h(row[i])]
        if not hits:
            raise ValueError("%s：找不到费用列「%s」" % (sheet, expected))
        out[field] = hits[0]
    return out


def convert_rows(source):
    wb = load_workbook(source, read_only=True, data_only=True)
    rows = []
    try:
        for ws in wb.worksheets:
            start = None
            header = None
            header_row = 0
            components = None
            previous_rows = []
            for src_row, raw in enumerate(ws.iter_rows(values_only=True), 1):
                if start is None:
                    found = _standard_start(raw)
                    if found is not None:
                        start, header, header_row = found, raw, src_row
                        try:
                            components = _component_map(ws.title, start, previous_rows + [raw])
                        except ValueError:
                            if ws.title != "5.外仓":
                                raise
                    else:
                        previous_rows.append(raw)
                    continue
                if ws.title == "5.外仓" and src_row == header_row + 1:
                    components = _component_map(ws.title, start, previous_rows + [header, raw])
                block = tuple(raw[start + i] if start + i < len(raw) else None for i in range(10))
                if not any(str(v or "").strip() for v in block):
                    continue
                if any(SUMMARY.match(str(v or "").strip()) for v in block[:2]):
                    continue
                if not str(block[0] or "").strip() or not str(block[1] or "").strip():
                    continue
                amount = _number(block[6], ws.title, src_row, "费用总额", required=True)
                values = {}
                for field, col in components.items():
                    values[field] = _number(raw[col] if col is not None and col < len(raw) else None,
                                            ws.title, src_row, field) if col is not None else None
                parts = [values[k] or 0.0 for k in ("freight", "delivery", "pickup", "unload", "upstairs", "other")]
                if abs(amount - sum(parts)) >= 0.005:
                    raise ValueError("%s 第 %d 行：费用总额 %.2f 与六项费用合计 %.2f 不一致" % (
                        ws.title, src_row, amount, sum(parts)))
                row = dict(zip(FIELDS, list(block[:7]) + [values["freight"], values["delivery"], values["pickup"],
                                                   values["unload"], values["upstairs"], values["other"], block[9]]))
                row["source_sheet"] = ws.title
                row["source_row"] = src_row
                rows.append(row)
    finally:
        wb.close()
    return rows


def convert_bytes(data):
    return convert_rows(BytesIO(data))


def write_workbook(rows, carrier, period, source=None):
    wb = load_workbook(source) if source is not None else Workbook()
    wb.properties.title = "%s %s A-N 标准逐笔账单" % (carrier, period)
    wb.properties.subject = "%s %s 标准逐笔账单" % (carrier, period)
    wb.properties.description = "A-N 十四列；G=H+I+J+K+L+M；不代表已完成复核登记"
    ws = wb.create_sheet("标准逐笔", 0) if source is not None else wb.active
    ws.title = "标准逐笔"
    ws.append(HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for row in rows:
        ws.append([row[k] for k in FIELDS])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = "A1:N1"
    for col, width in zip("ABCDEFGHIJKLMN", [30, 26, 16, 16, 14, 12, 14, 14, 16, 14, 14, 14, 14, 32]):
        ws.column_dimensions[col].width = width
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()


def convert_to_xlsx(source, carrier, period):
    rows = convert_rows(source)
    if not rows:
        raise ValueError("没有找到可转换的标准逐笔明细")
    return write_workbook(rows, carrier, period, source), rows
