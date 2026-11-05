import json
import unittest
from io import BytesIO

from openpyxl import Workbook

from kernels import logistics_standard_intake as intake


BASE_HEADERS = ["主体", "金蝶系统单据编号", "费用类型", "数量计算标准", "账单数量", "数量单位", "费用总额"]
BASE_ROW = ["深圳市星期零食品科技有限公司", "XSCKD219348", "出库运费", "按重量", 2, "吨"]
SPEC = {"carrier": "极鲜达", "period": "2026-09", "standard_bill_src": "标准逐笔"}


def workbook_bytes(sheets):
    wb = Workbook()
    wb.remove(wb.active)
    for title, headers, rows in sheets:
        ws = wb.create_sheet(title)
        ws.append(headers)
        for row in rows:
            ws.append(row)
    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()


def one_sheet(fee_headers, fee_values, amount=125, title="标准逐笔"):
    return workbook_bytes([(title, BASE_HEADERS + fee_headers + ["备注"], [BASE_ROW + [amount] + fee_values + ["动态费用"]])])


class StandardIntakeTests(unittest.TestCase):
    def test_legacy_aj_remains_compatible(self):
        data = one_sheet(["费用拆分1\n运费", "费用拆分2\n附加费"], [100, 25], title="明细")
        result = intake.parse_standard_bill(SPEC, data)
        self.assertEqual(len(result["detail"]), 1)
        row = result["detail"][0]
        self.assertEqual(row["amount"], 125)
        self.assertEqual(row["charge_wt"], 2000)
        self.assertEqual(json.loads(row["sub_fees"]), {"运费": 100, "附加费": 25})
        report = intake.standard_sheet_report(SPEC, data)
        self.assertEqual([m["name"] for m in report["sheets"][0]["money"]], ["费用总额", "运费", "附加费"])

    def test_dynamic_fees_keep_names_order_and_blank_zero(self):
        data = one_sheet(["卸货费", "运费", "暂存费", "送货/入仓费"], [20, 100, 5, None])
        row = intake.parse_standard_bill(SPEC, data)["detail"][0]
        self.assertEqual(json.loads(row["sub_fees"]), {"卸货费": 20, "运费": 100, "暂存费": 5, "送货/入仓费": 0})
        self.assertEqual(row["note"], "动态费用")
        report = intake.standard_sheet_report(SPEC, data)
        sheet = report["sheets"][0]
        self.assertEqual(sheet["format"], "dynamic")
        self.assertEqual([m["name"] for m in sheet["money"]],
                         ["费用总额", "卸货费", "运费", "暂存费", "送货/入仓费"])
        self.assertEqual([m["sum"] for m in sheet["money"]], [125, 20, 100, 5, 0])

        fewer = intake.parse_standard_bill(SPEC, one_sheet(["运费"], [125]))
        self.assertEqual(json.loads(fewer["detail"][0]["sub_fees"]), {"运费": 125})

    def test_primary_standard_sheet_does_not_duplicate_original_pages(self):
        primary = (BASE_HEADERS + ["运费", "卸货费", "备注"], [BASE_ROW + [125, 100, 25, "首页"]])
        original = (BASE_HEADERS + ["费用拆分1\n运费", "费用拆分2\n附加费", "备注"],
                    [BASE_ROW + [125, 100, 25, "原页旧标准块"]])
        data = workbook_bytes([("标准逐笔", *primary), ("原账单", *original)])
        result = intake.parse_standard_bill(SPEC, data)
        self.assertEqual(len(result["detail"]), 1)
        self.assertEqual(result["detail"][0]["note"], "首页")
        self.assertEqual(result["skipped"], ["原账单"])
        report = intake.standard_sheet_report(SPEC, data)
        self.assertEqual(report["n"], 1)
        self.assertEqual(report["amount"], 125)
        self.assertEqual(report["sheets"][1]["role"], "source")
        self.assertEqual(report["sheets"][1]["verdict"], "source")

    def test_invalid_dynamic_headers_are_actionable(self):
        cases = [
            (["运费"], "备注"),
            ([None, "备注"], "不能为空"),
            (["运费", "运 费", "备注"], "重复"),
            (["备注"], "至少需要一列"),
        ]
        for headers, message in cases:
            with self.subTest(headers=headers):
                data = workbook_bytes([("标准逐笔", BASE_HEADERS + headers, [BASE_ROW + [125, 125, "备注"]])])
                with self.assertRaisesRegex(ValueError, message):
                    intake.parse_standard_bill(SPEC, data)

    def test_invalid_dynamic_amounts_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "总额勾稽"):
            intake.parse_standard_bill(SPEC, one_sheet(["运费", "卸货费"], [100, 20], amount=125))
        with self.assertRaisesRegex(ValueError, "必须为数值"):
            intake.parse_standard_bill(SPEC, one_sheet(["运费", "卸货费"], [100, "坏数据"], amount=125))


if __name__ == "__main__":
    unittest.main()
