import io
import sys
import unittest
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "01_Current_Deliverables" / "app" / "backend"))
from kernels import logistics_intake as li


class NoDocumentBillTest(unittest.TestCase):
    def test_preserve_nonstandard_document_but_skip_total_and_zero_rows(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "账单明细汇总"
        ws.append(["合成账单"])
        ws.append(["序号", "金蝶单号", "应付金额"])
        ws.append([1, "QTCK011655", 220])
        ws.append([8, "202609111546000153529", 260])
        ws.append(["小 计", None, 480])
        ws.append(["合 计", None, 480])
        ws.append([None, "深圳星期零", 0])
        bio = io.BytesIO()
        wb.save(bio)
        spec = {"carrier": "顺丰冷运", "period": "2026-09", "sheets": [{
            "name": "账单明细*", "role": "detail", "header_row": 2, "doc_col": "金蝶单号",
            "doc_re": "^[A-Z]{2,6}\\d", "doc_re_else": "default", "doc_default": "无单据",
            "amount_col": "应付金额", "summary_marker": "计", "skip_zero": True}]}
        rows = li.parse_bill(spec, bio.getvalue())["detail"]
        self.assertEqual([r["doc_no"] for r in rows], ["QTCK011655", "无单据"])
        self.assertEqual(sum(r["amount"] for r in rows), 480)


if __name__ == "__main__":
    unittest.main()
