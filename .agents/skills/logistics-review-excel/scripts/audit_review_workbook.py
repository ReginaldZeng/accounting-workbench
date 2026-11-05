#!/usr/bin/env python3
import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

try:
    import openpyxl
except ImportError as exc:
    raise SystemExit("缺少 openpyxl，请先按项目 requirements.txt 安装依赖") from exc

ERROR_VALUES = {"#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "#NUM!", "#NULL!"}
STANDARD_SHEETS = ["01复核说明书", "02单据运费", "03计提对账单"]
LEGACY_SHEETS = ["复核表", "复核明细"]
AJ_SHEET = "标准逐笔"
AJ_HEADERS = ["主体", "金蝶系统单据编号", "费用类型", "数量计算标准", "账单数量", "数量单位", "费用总额", "费用拆分1", "费用拆分2", "备注"]
AN_HEADERS = ["主体", "金蝶系统单据编号", "费用类型", "数量计算标准", "账单数量", "数量单位", "费用总额", "运费", "送货/入仓费", "提货费", "卸货费", "上楼费", "其他", "备注"]


def text(value):
    return "" if value is None else str(value).strip()


def number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def used_cells(ws):
    cells = getattr(ws, "_cells", None)
    if cells is not None:
        return cells.values()
    return (cell for row in ws.iter_rows() for cell in row)


def sheet_text(ws):
    return "\n".join(text(cell.value) for cell in used_cells(ws) if cell.value is not None)


def find_cell(ws, needle):
    for cell in used_cells(ws):
        if needle in text(cell.value):
            return cell
    return None


def header_columns(ws, header_row):
    result = {}
    for cell in ws[header_row]:
        value = text(cell.value).replace("\n", "")
        if value:
            result[value] = cell.column
    return result


def total_row(ws):
    for cell in ws["A"]:
        if text(cell.value) == "合计":
            return cell.row
    return None


def check_totals(ws, header_row, diff_names, errors):
    columns = header_columns(ws, header_row)
    accr_col = columns.get("计提金额(含税)")
    bill_col = columns.get("账单金额")
    diff_col = next((columns[name] for name in diff_names if name in columns), None)
    if not all((accr_col, bill_col, diff_col)):
        errors.append(f"{ws.title} 缺少计提、账单或差异列表头")
        return None
    row = total_row(ws)
    if row is None:
        errors.append(f"{ws.title} 缺少合计行")
        return None
    accr = number(ws.cell(row, accr_col).value)
    bill = number(ws.cell(row, bill_col).value)
    diff = number(ws.cell(row, diff_col).value)
    if None in (accr, bill, diff):
        errors.append(f"{ws.title} 合计行的计提、账单和差异必须是数值，公式未计算时不能视同通过")
        return None
    if abs((accr - bill) - diff) > 0.01:
        errors.append(f"{ws.title} 合计不勾稽：计提 {accr:.2f} - 账单 {bill:.2f} ≠ 差异 {diff:.2f}")
    return {"accrual": round(accr, 2), "bill": round(bill, 2), "difference": round(diff, 2)}


def check_standard_aj(ws, errors):
    if ws.max_column != 10:
        errors.append(f"{AJ_SHEET} 必须严格只有 A-J 十列，实际为 {ws.max_column} 列")
    actual = [text(ws.cell(1, col).value).replace("\n", "") for col in range(1, 11)]
    headers_ok = all(actual[index] == expected for index, expected in enumerate(AJ_HEADERS) if index not in (7, 8))
    headers_ok = headers_ok and actual[7].startswith(AJ_HEADERS[7]) and actual[8].startswith(AJ_HEADERS[8])
    if not headers_ok:
        errors.append(f"{AJ_SHEET} 第一行表头不符合 A-J 契约：{actual}")
        return None
    totals = [0.0, 0.0, 0.0]
    count = 0
    for row in range(2, ws.max_row + 1):
        values = [ws.cell(row, col).value for col in range(1, 11)]
        if not any(text(value) for value in values):
            continue
        count += 1
        for col, name in ((1, "主体"), (2, "金蝶系统单据编号"), (3, "费用类型"), (4, "数量计算标准"), (6, "数量单位")):
            if not text(values[col - 1]):
                errors.append(f"{AJ_SHEET}!{ws.cell(row, col).coordinate} {name}不能为空")
        if values[4] not in (None, "") and number(values[4]) is None:
            errors.append(f"{AJ_SHEET}!E{row} 账单数量必须是数值")
        amounts = [number(values[col]) for col in (6, 7, 8)]
        if any(value is None for value in amounts):
            errors.append(f"{AJ_SHEET}!G{row}:I{row} 费用总额及两项拆分必须是数值")
            continue
        amount, freight, surcharge = amounts
        if abs(amount - freight - surcharge) > 0.01:
            errors.append(f"{AJ_SHEET}!G{row} 不满足 G=H+I：{amount} ≠ {freight} + {surcharge}")
        totals[0] += amount
        totals[1] += freight
        totals[2] += surcharge
    if not count:
        errors.append(f"{AJ_SHEET} 没有逐笔数据")
    return {"rows": count, "bill": round(totals[0], 2), "freight": round(totals[1], 2), "surcharge": round(totals[2], 2)}


def check_standard_an(ws, errors):
    if ws.max_column != 14:
        errors.append(f"{AJ_SHEET} 必须严格只有 A-N 十四列，实际为 {ws.max_column} 列")
    actual = [text(ws.cell(1, col).value).replace("\n", "") for col in range(1, 15)]
    if actual != AN_HEADERS:
        errors.append(f"{AJ_SHEET} 第一行表头不符合 A-N 契约：{actual}")
        return None
    totals = [0.0] * 7
    count = 0
    for row in range(2, ws.max_row + 1):
        values = [ws.cell(row, col).value for col in range(1, 15)]
        if not any(text(value) for value in values):
            continue
        count += 1
        for col, name in ((1, "主体"), (2, "金蝶系统单据编号"), (3, "费用类型"), (4, "数量计算标准"), (6, "数量单位")):
            if not text(values[col - 1]):
                errors.append(f"{AJ_SHEET}!{ws.cell(row, col).coordinate} {name}不能为空")
        if values[4] not in (None, "") and number(values[4]) is None:
            errors.append(f"{AJ_SHEET}!E{row} 账单数量必须是数值")
        amounts = [number(values[col]) if values[col] not in (None, "") else 0.0 for col in range(6, 13)]
        if any(value is None for value in amounts):
            errors.append(f"{AJ_SHEET}!G{row}:M{row} 费用总额及六项拆分必须是数值或留空")
            continue
        amount = amounts[0]
        if abs(amount - sum(amounts[1:])) > 0.01:
            errors.append(f"{AJ_SHEET}!G{row} 不满足 G=H+I+J+K+L+M：{amount} ≠ {sum(amounts[1:]):.2f}")
        for index, value in enumerate(amounts):
            totals[index] += value
    if not count:
        errors.append(f"{AJ_SHEET} 没有逐笔数据")
    return {"rows": count, "bill": round(totals[0], 2), "freight": round(totals[1], 2),
            "delivery": round(totals[2], 2), "pickup": round(totals[3], 2), "unload": round(totals[4], 2),
            "upstairs": round(totals[5], 2), "other": round(totals[6], 2)}


def business_status(wb, profile):
    if profile in ("standard-aj", "standard-an"):
        return "标准账单（不代表已复核）"
    if profile == "standard":
        cell = find_cell(wb["01复核说明书"], "八、提交结论")
        if cell:
            return text(wb["01复核说明书"].cell(cell.row, cell.column + 1).value)
        return ""
    value = wb["复核表"]["A2"].value
    return text(value).replace("复核状态：", "", 1)


def is_final(status, profile):
    if profile in ("standard-aj", "standard-an"):
        return False
    if profile == "standard":
        return status.startswith("复核完成") and "草稿" not in status
    return "已复核" in status and "待复核" not in status


def scan_cells(wb, cached=False):
    formulas = 0
    errors = []
    for ws in wb.worksheets:
        for cell in used_cells(ws):
            value = cell.value
            if not cached and cell.data_type == "f":
                formulas += 1
            if cell.data_type == "e" or text(value).upper() in ERROR_VALUES:
                errors.append(f"{ws.title}!{cell.coordinate}={text(value)}")
            elif not cached and isinstance(value, str) and value.startswith("="):
                for error in ERROR_VALUES:
                    if error in value.upper():
                        errors.append(f"{ws.title}!{cell.coordinate} 公式含 {error}")
    return formulas, sorted(set(errors))


def audit(path, carrier="", period="", require_final=False):
    path = Path(path)
    errors = []
    warnings = []
    result = {
        "ok": False,
        "file": str(path.resolve()),
        "profile": None,
        "business_status": None,
        "final": False,
        "totals": None,
        "sheets": [],
        "formula_cells": 0,
        "errors": errors,
        "warnings": warnings,
    }
    if not path.is_file():
        errors.append("文件不存在")
        return result
    result["size_bytes"] = path.stat().st_size
    result["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    if path.suffix.lower() != ".xlsx":
        errors.append("交付格式必须是 .xlsx")
        return result
    try:
        with zipfile.ZipFile(path) as archive:
            broken = archive.testzip()
            if broken:
                errors.append(f"XLSX ZIP 成员损坏：{broken}")
                return result
    except (zipfile.BadZipFile, OSError) as exc:
        errors.append(f"不是有效的 XLSX 文件：{exc}")
        return result
    try:
        wb = openpyxl.load_workbook(path, data_only=False, read_only=False)
        cached = openpyxl.load_workbook(path, data_only=True, read_only=False)
    except Exception as exc:
        errors.append(f"openpyxl 回读失败：{exc}")
        return result
    names = wb.sheetnames
    result["sheets"] = names
    if names and names[0] == AJ_SHEET:
        actual = [text(wb[AJ_SHEET].cell(1, col).value).replace("\n", "") for col in range(1, 15)]
        profile = "standard-an" if actual == AN_HEADERS else "standard-aj"
        expected = [AJ_SHEET]
    elif all(name in names for name in STANDARD_SHEETS):
        profile = "standard"
        expected = STANDARD_SHEETS
    elif all(name in names for name in LEGACY_SHEETS):
        profile = "legacy"
        expected = LEGACY_SHEETS
    else:
        errors.append("无法识别工作簿：需要严格 A-J 或 A-N 标准逐笔、恒茂四段式页签，或现有复核表/复核明细页签")
        return result
    result["profile"] = profile
    if names[:len(expected)] != expected:
        errors.append(f"核心页签顺序应为：{'、'.join(expected)}")
    identity_text = "\n".join([sheet_text(wb[expected[0]]), path.name,
                               text(wb.properties.title), text(wb.properties.subject), text(wb.properties.description)])
    if carrier and carrier not in identity_text:
        errors.append(f"首页或文件属性未找到承运商：{carrier}")
    if period and period not in identity_text:
        errors.append(f"首页或文件属性未找到账期：{period}")
    if profile == "standard-an":
        totals = check_standard_an(wb[AJ_SHEET], errors)
    elif profile == "standard-aj":
        totals = check_standard_aj(wb[AJ_SHEET], errors)
    elif profile == "standard":
        totals = check_totals(wb["03计提对账单"], 4, ["计提差异(计提-账单)"], errors)
        required_headers = ["费用行ID", "核对方案", "单据编码", "账单金额", "标准金额", "金额差异"]
        columns = header_columns(wb["02单据运费"], 4)
        missing = [name for name in required_headers if name not in columns]
        if missing:
            errors.append("02单据运费 缺少关键列：" + "、".join(missing))
    else:
        totals = check_totals(wb["复核表"], 5, ["差异"], errors)
    result["totals"] = totals
    status = business_status(wb, profile)
    result["business_status"] = status
    result["final"] = is_final(status, profile)
    if not status:
        warnings.append("未识别到业务状态，需回系统确认是否已闭环")
    if require_final and not result["final"]:
        errors.append(f"不是最终闭环版：{status or '状态缺失'}")
    formula_count, formula_errors = scan_cells(wb)
    _, cached_errors = scan_cells(cached, cached=True)
    result["formula_cells"] = formula_count
    errors.extend("公式/单元格错误：" + item for item in formula_errors)
    errors.extend("公式缓存错误：" + item for item in cached_errors)
    if profile not in ("standard-aj", "standard-an") and not any(name.startswith("原账单-") for name in names):
        warnings.append("未附原账单页；如系统应具备原件，请回导出链路核查")
    if profile == "standard-an" and len(names) == 1:
        warnings.append("未附原账单页；如系统应具备原件，请回导出链路核查")
    calculation = getattr(wb, "calculation", None)
    if formula_count and calculation is not None and getattr(calculation, "calcMode", None) not in (None, "auto"):
        warnings.append("工作簿计算模式不是自动，打开后请手动重算并检查公式显示值")
    if formula_count:
        warnings.append("openpyxl 只扫描公式和已有缓存，不执行公式重算；必要时尚需 Excel/WPS 重算与视觉验收")
    result["ok"] = not errors
    return result


def print_human(result):
    print(f"文件：{result['file']}")
    print(f"结构验收：{'通过' if result['ok'] else '未通过'}")
    if result.get("profile"):
        print(f"格式：{result['profile']}；业务状态：{result.get('business_status') or '未识别'}")
    if result.get("totals"):
        totals = result["totals"]
        if result.get("profile") == "standard-an":
            print(f"逐笔：{totals['rows']} 行；费用总额 {totals['bill']:,.2f}；运费 {totals['freight']:,.2f}；送货/入仓费 {totals['delivery']:,.2f}；提货费 {totals['pickup']:,.2f}；卸货费 {totals['unload']:,.2f}；上楼费 {totals['upstairs']:,.2f}；其他 {totals['other']:,.2f}")
        elif result.get("profile") == "standard-aj":
            print(f"逐笔：{totals['rows']} 行；费用总额 {totals['bill']:,.2f}；运费 {totals['freight']:,.2f}；附加费 {totals['surcharge']:,.2f}")
        else:
            print(f"合计：计提 {totals['accrual']:,.2f}；账单 {totals['bill']:,.2f}；差异 {totals['difference']:,.2f}")
    for item in result["errors"]:
        print(f"错误：{item}")
    for item in result["warnings"]:
        print(f"提示：{item}")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="只读验收物流账单复核表 xlsx")
    parser.add_argument("workbook")
    parser.add_argument("--carrier", default="")
    parser.add_argument("--period", default="")
    parser.add_argument("--require-final", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    result = audit(args.workbook, args.carrier, args.period, args.require_final)
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_human(result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
