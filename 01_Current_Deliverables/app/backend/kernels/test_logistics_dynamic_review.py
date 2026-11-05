import json
import unittest
from io import BytesIO

from openpyxl import Workbook, load_workbook

from kernels import logistics_dynamic_review as review
from kernels import logistics_standard_intake as intake


def buckets(amount, fee_item, sub_fees):
    return {"tr": amount, "ld": 0.0, "ot": 0.0}


def row(lid, doc_no, amount, fees, qty=1):
    return {"id": lid, "doc_no": doc_no, "subject": "深圳星期零", "fee_item": "出库运费",
            "qty_standard": "按重量", "qty": qty, "unit": "kg", "amount": amount,
            "sub_fees": json.dumps(fees, ensure_ascii=False), "note": "供应商原备注"}


def build(rows, documents, confirmations=None, source=None):
    return review.build_workbook(rows, documents, confirmations or {}, buckets,
                                 lambda r: r["subject"], lambda r: r["fee_item"], lambda r: r["qty_standard"],
                                 "极鲜达", "2026-09", source)


class DynamicReviewTests(unittest.TestCase):
    def test_review_workbook_and_reupload_ignore_review_columns(self):
        rows = [
            row(1, "XS1", 3030, {"运费": 2600, "送货/入仓费": 430}, 4000),
            row(2, "XS2", 120, {"运费": 100, "卸货费": 20}, 10),
            row(3, "XS3", 500, {"运费": 500}),
            row(4, "XS4", 80, {"运费": 80}),
            row(5, "无单据", 30, {"其他": 30}),
            row(6, "XS6", 70, {"运费": 70}),
            row(7, "XS7", 90, {"运费": 90}, 100),
        ]
        documents = [
            {"lid": 1, "doc_no": "XS1", "kd_sum": 4000, "qty_state": "ok", "state": "ok", "mode_cn": "按重量一致"},
            {"lid": 2, "doc_no": "XS2", "kd_sum": 8, "qty_state": "qtydiff", "state": "qtydiff", "q_diff": 2, "bill_unit": "kg", "mode_cn": "按重量不符"},
            {"lid": 3, "doc_no": "XS3", "kd_sum": 500, "qty_state": "na", "state": "info", "mode_cn": "按车免核"},
            {"lid": 6, "doc_no": "XS6", "kd_sum": 0, "qty_state": "miss", "state": "miss", "mode_cn": "金蝶查无"},
            {"lid": 7, "doc_no": "XS7", "kd_sum": 80, "qty_state": "qtydiff", "state": "qtydiff", "q_diff": 20, "bill_unit": "kg", "mode_cn": "按重量不符"},
        ]
        original = Workbook()
        original.active.title = "原始页"
        original.active["A1"] = "保留我"
        bio = BytesIO()
        original.save(bio)
        bio.seek(0)
        data, meta = build(rows, documents, {"XS7": {"by": "曾禹锡", "at": "2026-11-05 12:00"}}, bio)
        self.assertEqual(meta["fee_names"], ["运费", "送货/入仓费", "卸货费", "其他"])
        self.assertEqual(meta["checks"], {"通过": 1, "数量不符": 1, "免核": 1, "待取数": 1,
                                            "无金蝶单号": 1, "金蝶无此单": 1, "人工确认": 1})
        wb = load_workbook(BytesIO(data), data_only=True)
        self.assertEqual(wb.sheetnames, ["标准逐笔", "原始页"])
        self.assertEqual(wb["原始页"]["A1"].value, "保留我")
        ws = wb["标准逐笔"]
        headers = [cell.value for cell in ws[1]]
        self.assertEqual(headers[-4:], ["账单备注", "金蝶数量", "check", "复核备注"])
        note_col = headers.index("账单备注")
        for values in ws.iter_rows(min_row=2, values_only=True):
            self.assertAlmostEqual(values[6], sum(values[7:note_col]))
            self.assertEqual(values[note_col], "供应商原备注")
        self.assertEqual(ws.cell(2, headers.index("check") + 1).value, "通过")
        self.assertEqual(ws.cell(3, headers.index("金蝶数量") + 1).value, 8)
        self.assertIsNone(ws.cell(4, headers.index("金蝶数量") + 1).value)
        self.assertEqual(ws.cell(5, headers.index("check") + 1).value, "待取数")
        self.assertIn("人工确认：曾禹锡", ws.cell(8, headers.index("复核备注") + 1).value)

        parsed = intake.parse_standard_bill({"carrier": "极鲜达", "period": "2026-09", "standard_bill_src": "标准逐笔"}, data)
        self.assertEqual(len(parsed["detail"]), 7)
        self.assertEqual(round(sum(r["amount"] for r in parsed["detail"]), 2), 3920)
        self.assertEqual(parsed["skipped"], ["原始页"])
        self.assertEqual(parsed["detail"][0]["note"], "供应商原备注")
        self.assertEqual(json.loads(parsed["detail"][0]["sub_fees"]),
                         {"运费": 2600, "送货/入仓费": 430, "卸货费": 0, "其他": 0})

    def test_metadata_and_changed_amount_use_current_buckets(self):
        current = row(1, "XS1", 100, {"标准": 90, "核价差": 10, "__original_amount": 90})
        _, output, _ = review.build_rows([current], [], {}, lambda amount, fee, sub: {"tr": 60, "ld": 25, "ot": 15},
                                         lambda r: r["subject"], lambda r: r["fee_item"], lambda r: r["qty_standard"])
        self.assertEqual(output[0][7:10], [60, 25, 15])
        self.assertEqual(output[0][6], 100)

    def test_invalid_review_tail_is_rejected(self):
        wb = Workbook()
        ws = wb.active
        ws.title = "标准逐笔"
        ws.append(review.BASE_HEADERS + ["运费", "账单备注", "金蝶数量", "错误列", "复核备注"])
        ws.append(["深圳", "XS1", "出库运费", "按重量", 1, "kg", 10, 10, "", 1, "x", ""])
        bio = BytesIO()
        wb.save(bio)
        with self.assertRaisesRegex(ValueError, "系统复核结果"):
            intake.parse_standard_bill({"carrier": "极鲜达", "period": "2026-09"}, bio.getvalue())


if __name__ == "__main__":
    unittest.main()
