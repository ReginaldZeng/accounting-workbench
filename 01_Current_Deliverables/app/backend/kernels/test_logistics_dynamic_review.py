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


def build(rows, documents, confirmations=None, source=None, material_rows=None, form_labels=None):
    return review.build_workbook(rows, documents, confirmations or {}, buckets,
                                 lambda r: r["subject"], lambda r: r["fee_item"], lambda r: r["qty_standard"],
                                 "极鲜达", "2026-09", source, material_rows, form_labels)


def material(doc_no, form, carrier, code="3001"):
    return {"subject": "深圳市星期零食品科技有限公司", "form": form, "form_label": "销售出库单" if form == "SAL_OUTSTOCK" else "其它出库单",
            "doc_no": doc_no, "party": "广州文蔬餐饮有限公司", "material_group": "植物肉", "code": code, "name": "植物鸡肉酥排",
            "base_qty": 2000, "base_unit": "千克", "sales": 60000, "carrier": carrier,
            "fee_tr": 1515, "fee_ld": 0, "unit_fee": 0.7575, "ratio": 0.0253}


class DynamicReviewTests(unittest.TestCase):
    def test_detail_blocks_and_document_sheets(self):
        current = row(1, "XS1", 3030, {"运费": 2600, "送货/入仓费": 430}, 4000)
        documents = [{"lid": 1, "doc_no": "XS1", "kd_sum": 4000, "qty_state": "ok", "state": "ok",
                      "q_diff": 0, "bill_amt": 4000, "bill_unit": "千克", "unit_fee": 0.7575,
                      "doc_fee": 3030, "fee_item": "出库运费", "mode_cn": "按重量一致"}]
        wb = Workbook()
        ws = wb.active
        ws.title = "1.深圳星期零"
        ws["A1"] = "原始内容保留"
        headers = review.BASE_HEADERS + ["费用拆分1\n运费", "费用拆分2\n附加费", "备注"]
        for offset, value in enumerate(headers, 3):
            ws.cell(2, offset, value)
        values = ["深圳市星期零食品科技有限公司", "XS1", "出库运费", "按重量", 4000, "kg", 3030, 2600, 430, "供应商原备注"]
        for offset, value in enumerate(values, 3):
            ws.cell(3, offset, value)
        ws.cell(3, 14, "=1/0")
        ws.cell(4, 13, "=1/0")
        bio = BytesIO()
        wb.save(bio)
        bio.seek(0)
        materials = [material("XS1", "SAL_OUTSTOCK", "极鲜达"), material("XS1", "SAL_OUTSTOCK", "天鹰物流"),
                     material("QT1", "STK_MisDelivery", "极鲜达", "3002")]
        data, meta = build([current], documents, {"XS1": {"by": "曾禹锡", "at": "2026-11-05 12:00"}}, bio,
                           materials, {"SAL_OUTSTOCK": "销售出库单", "STK_MisDelivery": "其它出库单"})
        self.assertEqual(meta["enriched_sheets"], ["1.深圳星期零"])
        self.assertEqual(meta["enriched_rows"], 1)
        self.assertEqual(meta["document_sheets"], {"销售出库单列表": 1, "其它出库单列表": 1})
        wb = load_workbook(BytesIO(data), data_only=True)
        self.assertEqual(wb["1.深圳星期零"]["A1"].value, "原始内容保留")
        self.assertEqual([wb["1.深圳星期零"].cell(2, col).value for col in range(13, 18)],
                         ["金蝶数量", "差异", "毛重比", "单位运费", "备注"])
        self.assertEqual([wb["1.深圳星期零"].cell(3, col).value for col in range(13, 17)], [4000, 0, 1, 0.7575])
        self.assertIn("人工确认：曾禹锡", wb["1.深圳星期零"].cell(3, 17).value)
        self.assertTrue(wb["1.深圳星期零"].cell(2, 13).fill.fgColor.rgb.endswith("FFF200"))
        self.assertTrue(all(wb["1.深圳星期零"].cell(r, c).data_type != "f"
                            for r in (3, 4) for c in range(13, 18)))
        home_headers = [cell.value for cell in wb["标准逐笔"][1]]
        self.assertEqual(home_headers[-1], "备注")
        self.assertNotIn("金蝶数量", home_headers)
        sales = wb["销售出库单列表"]
        self.assertEqual([cell.value for cell in sales[1]], review.MATERIAL_HEADERS)
        self.assertEqual(sales.max_row, 2)
        self.assertEqual(sales.cell(2, 11).value, "极鲜达")
        self.assertEqual(sales.cell(2, 12).value, 1515)
        self.assertEqual(wb["其它出库单列表"].max_row, 2)

        parsed = intake.parse_standard_bill({"carrier": "极鲜达", "period": "2026-09", "standard_bill_src": "标准逐笔"}, data)
        self.assertEqual(len(parsed["detail"]), 1)
        self.assertEqual(parsed["detail"][0]["amount"], 3030)
        self.assertEqual(len(parsed["skipped"]), 3)

    def test_missing_and_na_results_leave_numbers_blank(self):
        current = row(1, "无单据", 30, {"其他": 30})
        values, check = review.review_values(current)
        self.assertEqual(values, [None, None, None, None, "无金蝶单号，不逐单核量"])
        self.assertEqual(check, "无金蝶单号")
        current["doc_no"] = "XS2"
        values, check = review.review_values(current)
        self.assertEqual(check, "待取数")
        self.assertEqual(values[:4], [None, None, None, None])
        doc = {"kd_sum": 500, "qty_state": "na", "state": "info", "mode_cn": "按车免核"}
        values, check = review.review_values(current, doc)
        self.assertEqual(check, "免核")
        self.assertEqual(values[:4], [None, None, None, None])

    def test_metadata_and_changed_amount_use_current_buckets(self):
        current = row(1, "XS1", 100, {"标准": 90, "核价差": 10, "__original_amount": 90})
        _, output, _ = review.build_rows([current], [], {}, lambda amount, fee, sub: {"tr": 60, "ld": 25, "ot": 15},
                                         lambda r: r["subject"], lambda r: r["fee_item"], lambda r: r["qty_standard"])
        self.assertEqual(output[0][7:10], [60, 25, 15])
        self.assertEqual(output[0][6], 100)

    def test_old_review_tail_still_accepted_and_unknown_tail_rejected(self):
        wb = Workbook()
        ws = wb.active
        ws.title = "标准逐笔"
        ws.append(review.BASE_HEADERS + ["运费", "账单备注", "金蝶数量", "check", "复核备注"])
        ws.append(["深圳", "XS1", "出库运费", "按重量", 1, "kg", 10, 10, "", 1, "通过", "一致"])
        bio = BytesIO()
        wb.save(bio)
        parsed = intake.parse_standard_bill({"carrier": "极鲜达", "period": "2026-09"}, bio.getvalue())
        self.assertEqual(len(parsed["detail"]), 1)

        wb = Workbook()
        ws = wb.active
        ws.title = "标准逐笔"
        ws.append(review.BASE_HEADERS + ["运费", "账单备注", "金蝶数量", "错误列", "复核备注"])
        ws.append(["深圳", "XS1", "出库运费", "按重量", 1, "kg", 10, 10, "", 1, "x", ""])
        bio = BytesIO()
        wb.save(bio)
        with self.assertRaisesRegex(ValueError, "系统复核结果"):
            intake.parse_standard_bill({"carrier": "极鲜达", "period": "2026-09"}, bio.getvalue())

    def test_pending_current_carrier_is_included_in_document_lists(self):
        from unittest.mock import patch
        from routers import logistics_review as router

        doc = {"doc_no": "QTCK011653", "subject": "深圳星期零", "fee_item": "出库运费",
               "materials": [{"base_kg": 100, "base_qty": 100, "base_unit": "千克", "sales": 1000,
                              "code": "3001", "name": "物料", "party": "领用部门"}]}
        freight = {"QTCK011653": {"parts": {
            ("极鲜达", "2026-09", False, "pending"): {"tr": 100, "ld": 20, "ot": 0},
            ("天鹰物流", "2026-09", True, "v1"): {"tr": 50, "ld": 0, "ot": 0},
        }}}
        with patch.object(router, "_outstock_freight", return_value=freight), \
             patch.object(router, "_kd_shared_materials", return_value={"heads": {}, "groups": {}}):
            default_rows = router._review_material_rows("极鲜达", "2026-09", [doc])
            export_rows = router._review_material_rows("极鲜达", "2026-09", [doc], include_pending=True)
        self.assertEqual({row["carrier"] for row in default_rows}, {"天鹰物流"})
        self.assertEqual({row["carrier"] for row in export_rows}, {"极鲜达", "天鹰物流"})
        pending = next(row for row in export_rows if row["carrier"] == "极鲜达")
        self.assertEqual((pending["fee_tr"], pending["fee_ld"]), (100, 20))


if __name__ == "__main__":
    unittest.main()
