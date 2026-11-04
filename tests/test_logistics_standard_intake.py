# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-11-04 | Author: Kimi | Version: V-draft
# Description: A-J 标准物流账单真实样例与独立工作簿解析、勾稽及数据接入报告测试。
import io
import json
import sys
import unittest
from collections import defaultdict
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "01_Current_Deliverables" / "app" / "backend"
sys.path.insert(0, str(BACKEND))

from kernels import logistics_standard_intake as lsi

SAMPLE = ROOT / "output" / "logistics-review-excel" / "2026-09" / "极鲜达_2026-09_标准逐笔账单_已重算.xlsx"
HEADERS = [
    "主体", "金蝶系统单据编号", "费用类型", "数量计算标准", "账单数量",
    "数量单位", "费用总额", "费用拆分1\n运费", "费用拆分2\n附加费", "备注",
]


def _workbook(rows, sheet="标准", original_total=False):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    if original_total:
        ws.append(["原账单总运费"] + HEADERS)
        for row in rows:
            ws.append([row[6]] + row)
    else:
        ws.append(HEADERS)
        for row in rows:
            ws.append(row)
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def _row(**changes):
    row = ["深圳市星\u671f零食品科技有限公司", "XS1", "出库运费", "按件数", 2, "件", 10, 7, 3, "备注"]
    for key, value in changes.items():
        row[HEADERS.index(key)] = value
    return row


class RealStandardWorkbook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = {"carrier": "极鲜达", "period": "2026-09", "standard_bill_src": "极鲜达九月标准账单"}
        cls.result = lsi.parse_standard_bill(cls.spec, str(SAMPLE))
        cls.report = lsi.standard_sheet_report(cls.spec, str(SAMPLE), "2026-09")

    def test_five_sheets_144_rows_and_physical_sheet_rounding(self):
        self.assertIsNotNone(self.result)
        self.assertEqual(len(self.result["detail"]), 144)
        self.assertEqual(self.result["accrual"], [])
        self.assertEqual(self.result["duplicates"], [])
        self.assertEqual(self.result["skipped"], ["汇总", "零担、快运汇总"])
        amounts = defaultdict(float)
        for row in self.result["detail"]:
            amounts[row["src_sheet"]] += row["amount"]
        total = round(sum(round(value, 2) for value in amounts.values()), 2)
        self.assertEqual(total, 214484.59)

    def test_subject_totals_and_first_weight_row(self):
        by_sheet_subject = defaultdict(float)
        for row in self.result["detail"]:
            by_sheet_subject[(row["src_sheet"], row["subject"])] += row["amount"]
        subjects = defaultdict(float)
        for (_, subject), amount in by_sheet_subject.items():
            subjects[subject] += round(amount, 2)
        self.assertEqual({key: round(value, 2) for key, value in subjects.items()}, {
            "深圳市星\u671f零食品科技有限公司": 123576.67,
            "深圳市星\u671f九食品科技有限公司": 22538.80,
            "孝感市星\u671f九食品科技有限公司": 68369.12,
        })
        first = self.result["detail"][0]
        self.assertEqual(first["doc_no"], "XSCKD219348")
        self.assertEqual(first["amount"], 3030)
        self.assertEqual(first["qty_standard"], "按重量")
        self.assertEqual((first["qty"], first["unit"], first["charge_wt"]), (4000, "kg", 4000))
        self.assertEqual(first["subject"], "深圳市星\u671f零食品科技有限公司")
        self.assertEqual(first["bill_src"], "极鲜达九月标准账单")

    def test_missing_and_multiple_docs_and_total_rows(self):
        rows = self.result["detail"]
        self.assertEqual(sum(row["doc_no"] == "无金蝶单号" for row in rows), 2)
        multi = next(row for row in rows if row["src_row"] == 19 and row["src_sheet"] == "5.外仓")
        self.assertEqual(multi["doc_no"], "ZJDB006780+ZJDB006783")
        self.assertFalse(any(row["subject"] in ("合计", "总计") for row in rows))
        self.assertEqual(max(row["src_row"] for row in rows if row["src_sheet"] == "5.外仓"), 19)

    def test_every_split_balances_without_rounding_amount(self):
        for row in self.result["detail"]:
            with self.subTest(sheet=row["src_sheet"], row=row["src_row"]):
                sub = json.loads(row["sub_fees"])
                self.assertLessEqual(abs(row["amount"] - sub["运费"] - sub["附加费"]), 0.005)
                self.assertEqual(set(sub), {"运费", "附加费"})
        self.assertTrue(any(row["amount"] != round(row["amount"], 2) for row in self.result["detail"]))

    def test_report_matches_real_sheet_totals(self):
        details = [sheet for sheet in self.report["sheets"] if sheet["role"] == "detail"]
        self.assertEqual([sheet["sheet"] for sheet in self.report["sheets"]], [
            "汇总", "零担、快运汇总", "1.深圳星\u671f零", "2.深圳星\u671f九", "3.孝感星\u671f九", "4.山姆", "5.外仓",
        ])
        self.assertEqual([sheet["amount"] for sheet in details], [56086.17, 22538.80, 50854.12, 67490.50, 17515.00])
        self.assertEqual([sheet["n"] for sheet in details], [68, 16, 11, 33, 16])
        self.assertEqual(self.report["n"], 144)
        self.assertEqual(self.report["amount"], 214484.59)
        self.assertEqual([sheet["money"][0]["name"] for sheet in details], ["费用总额"] * 5)


class IndependentStandardWorkbook(unittest.TestCase):
    def setUp(self):
        self.spec = {"carrier": "合成承运商", "period": "2026-10", "bill_src": "合成账单"}

    def test_multiple_separators_blank_doc_and_summary_row(self):
        data = _workbook([
            _row(**{"金蝶系统单据编号": "A,B，C;D；E/F\nG+H"}),
            _row(**{"金蝶系统单据编号": None, "数量计算标准": "按重量", "账单数量": 1.2,
                    "数量单位": "吨", "费用总额": 20, "费用拆分1\n运费": 18, "费用拆分2\n附加费": 2}),
            _row(**{"主体": "合计", "金蝶系统单据编号": None, "费用总额": 999}),
        ])
        result = lsi.parse_standard_bill(self.spec, data)
        self.assertEqual(len(result["detail"]), 2)
        self.assertEqual(result["detail"][0]["doc_no"], "A+B+C+D+E+F+G+H")
        self.assertEqual(result["detail"][1]["doc_no"], "无金蝶单号")
        self.assertEqual(result["detail"][1]["charge_wt"], 1200)
        self.assertEqual(result["detail"][0]["annot"], "出库运费")
        self.assertEqual(result["bill_src"], "合成账单")
        report = lsi.standard_sheet_report(self.spec, data, "2026-10")
        self.assertEqual(report["sheets"][0]["total"], None)
        self.assertEqual(report["sheets"][0]["verdict"], "nocheck")
        self.assertEqual(report["verdict"], "nocheck")

    def test_report_rounds_each_physical_sheet_before_total(self):
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        for name in ("甲页", "乙页"):
            ws = wb.create_sheet(name)
            ws.append(HEADERS)
            ws.append(_row(**{"费用总额": 0.004, "费用拆分1\n运费": 0.004, "费用拆分2\n附加费": 0}))
        bio = io.BytesIO()
        wb.save(bio)
        data = bio.getvalue()
        result = lsi.parse_standard_bill(self.spec, data)
        report = lsi.standard_sheet_report(self.spec, data)
        self.assertEqual(round(sum(row["amount"] for row in result["detail"]), 2), 0.01)
        self.assertEqual(report["amount"], 0.0)

    def test_original_total_column_is_checked(self):
        data = _workbook([_row(), _row(**{"金蝶系统单据编号": "XS2"})], original_total=True)
        sheet = lsi.standard_sheet_report(self.spec, data, "2026-10")["sheets"][0]
        self.assertEqual(sheet["total"], {"name": "原账单总运费", "sum": 20, "how": "guess"})
        self.assertEqual((sheet["gap"], sheet["verdict"]), (0, "ok"))

    def test_validation_errors_include_sheet_and_row(self):
        cases = {
            "unknown_standard": _row(**{"数量计算标准": "按未知"}),
            "missing_unit": _row(**{"数量单位": None}),
            "missing_amount": _row(**{"费用总额": None}),
            "invalid_amount": _row(**{"费用总额": "不是数值"}),
            "split_mismatch": _row(**{"费用拆分2\n附加费": 2}),
        }
        for name, row in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(ValueError) as ctx:
                    lsi.parse_standard_bill(self.spec, _workbook([row], sheet="错误页"))
                message = str(ctx.exception)
                self.assertIn("错误页", message)
                self.assertIn("2", message)

    def test_no_standard_header_returns_none(self):
        wb = openpyxl.Workbook()
        wb.active.title = "汇总"
        wb.active.append(["费用", 100])
        bio = io.BytesIO()
        wb.save(bio)
        self.assertIsNone(lsi.parse_standard_bill(self.spec, bio.getvalue()))
        report = lsi.standard_sheet_report(self.spec, bio.getvalue())
        self.assertEqual(report["sheets"], [{"sheet": "汇总", "role": "skip", "verdict": "skip"}])
        self.assertEqual(report["verdict"], "nocheck")


if __name__ == "__main__":
    unittest.main()
