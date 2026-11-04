# -*- coding: utf-8 -*-
# [Change Log] Date:2026-11-04 Author:Kimi Version:V-draft 公共金蝶数据中心与共享派生测试。
import asyncio
import json
import types
import unittest
from io import BytesIO
from unittest.mock import patch

from openpyxl import Workbook
from tests.test_logistics_readiness_api import _load_module, _Request


class KingdeeDataCenterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lr, cls.engine = _load_module()

    def setUp(self):
        self.lr._ACCR_CACHE.clear()
        self.engine.rows = [[("XSCKD1",), ("RK1",), ("QTCK1",), ("CGRK1",), ("CGTL1",), ("FBDR1",), ("FBDC1",)]]

    def test_eight_forms_refresh_with_one_login_and_independent_partitions(self):
        writes = []
        def materials(_s, _c, by_form):
            form = next(iter(by_form))
            no = next(iter(by_form[form]))
            return {no: [{"编码": "M1", "名称": "物料", "基本数量": 1, "基本单位": "千克", "_form": form}]}
        with patch.object(self.lr.kc, "login", return_value=(object(), {})) as login, \
             patch.object(self.lr, "_fetch_doc_materials", side_effect=materials), \
             patch.object(self.lr, "_fetch_doc_heads", return_value={"XSCKD1": {"销售组织": "深圳"}}), \
             patch.object(self.lr.kc, "fetch_material_attrs", create=True, return_value={"M1": {"grp": "植物肉"}}), \
             patch.object(self.lr.db, "set_period_input", create=True, side_effect=lambda *args: writes.append(args)):
            out = self.lr._kd_refresh_public("2026-09", list(self.lr._DOC_TYPES))
        self.assertEqual(login.call_count, 1)
        self.assertEqual(len(out["refreshed"]), 8)
        self.assertEqual(out["errors"], {})
        self.assertEqual([args[3] for args in writes], ["kd:materials:" + form for form in self.lr._DOC_TYPES])
        self.assertTrue(all(args[0] == "kingdee" for args in writes))
        self.assertEqual(writes[0][4]["groups"], {"M1": "植物肉"})
        self.assertEqual(writes[0][4]["material_version"], writes[0][4]["material_version"])

    def test_public_upload_never_logs_in_and_uses_global_unknown_set(self):
        wb = Workbook()
        ws = wb.active
        ws.append(self.lr._FALLBACK_HEADERS + ["物料分组"] + list(self.lr._KD_HEAD_FIELDS))
        ws.append(["XSCKD1", "M1", "物料一", 10, "千克", "客户", 100, "1kg", 10, "箱", "SRC", "植物肉", "深圳", "", "", "", "", ""])
        ws.append(["UNKNOWN1", "M2", "未知", 20, "千克"])
        bio = BytesIO()
        wb.save(bio)
        req = types.SimpleNamespace(query_params={"period": "2026-09"}, headers={}, state=types.SimpleNamespace(upload_name="公共补核.xlsx"))
        async def body():
            return bio.getvalue()
        req.body = body
        writes = []
        with patch.object(self.lr, "_perm", return_value={"name": "测试"}), \
             patch.object(self.lr.kc, "login", side_effect=AssertionError("公共补核不得登录金蝶")), \
             patch.object(self.lr.db, "set_period_input", create=True, side_effect=lambda *args: writes.append(args)):
            out = asyncio.run(self.lr.review_kingdee_data_fallback(req))
        self.assertTrue(out["ok"])
        self.assertEqual(out["unknown"], ["UNKNOWN1"])
        self.assertEqual(out["refreshed"][0]["form"], "SAL_OUTSTOCK")
        self.assertEqual(writes[0][0], "kingdee")
        self.assertEqual(writes[0][3], "kd:materials:SAL_OUTSTOCK")
        self.assertEqual(writes[0][4]["source"], "manual_attachment")
        self.assertEqual(writes[0][4]["groups"]["M1"], "植物肉")
        raw = self.engine.connections[-1].writes[-1].compile().params
        self.assertEqual(raw["carrier"], "")
        self.assertEqual(raw["role"], "kingdee_fallback_shared")
        self.assertEqual(raw["name"], "公共补核.xlsx")

    def test_derive_uses_shared_material_and_records_version_without_login(self):
        self.engine.rows = [[{"id": 1, "doc_no": "XSCKD1", "grain": "detail", "amount": 10, "qty": 1}], []]
        public = {"payload": {"materials": {"XSCKD1": [{"编码": "M1", "_form": "SAL_OUTSTOCK"}]},
                              "material_version": "v1", "source": "manual_attachment"}, "updated_at": "2026-11-04 12:00"}
        writes = []
        with patch.object(self.lr, "_signed", return_value=None), \
             patch.object(self.lr, "_load_card", return_value=({}, 0)), \
             patch.object(self.lr, "_load_spec", return_value={}), \
             patch.object(self.lr, "_box_docs", return_value=[{"doc_no": "XSCKD1", "materials": []}]), \
             patch.object(self.lr, "_bust"), \
             patch.object(self.lr.kc, "login", side_effect=AssertionError("派生不得登录金蝶")), \
             patch.object(self.lr.db, "get_period_input", create=True, side_effect=lambda source, y, p, kind: public if source == "kingdee" else None), \
             patch.object(self.lr.db, "set_period_input", create=True, side_effect=lambda *args: writes.append(args)):
            out = self.lr._derive_documents("丰源", "2026-09", ["SAL_OUTSTOCK"])
        self.assertEqual(out["refreshed"][0]["version"], "v1")
        self.assertEqual(writes[0][4]["material_version"], "v1")
        self.assertEqual(writes[0][4]["source"], "manual_attachment")

    def test_signed_derived_snapshot_stays_frozen(self):
        self.engine.rows = [[{"id": 1, "doc_no": "XSCKD1", "grain": "detail", "amount": 10, "qty": 1}], []]
        old = None
        public = {"payload": {"materials": {"XSCKD1": []}, "material_version": "new"}}
        with patch.object(self.lr, "_signed", return_value={"status": "signed"}), \
             patch.object(self.lr, "_docs_fingerprints", return_value={"SAL_OUTSTOCK": "fp", "legacy": "fp"}), \
             patch.object(self.lr, "_load_card", return_value=({}, 0)), \
             patch.object(self.lr, "_load_spec", return_value={}), \
             patch.object(self.lr, "_box_docs", side_effect=AssertionError("已登记不得重建")), \
             patch.object(self.lr.db, "get_period_input", create=True,
                          side_effect=lambda source, y, p, kind: public if source == "kingdee" else old):
            out = self.lr._derive_documents("丰源", "2026-09", ["SAL_OUTSTOCK"])
        self.assertEqual(out["refreshed"], [])

    def test_classify_build_uses_shared_material_heads_and_groups(self):
        from routers import logistics_classify as classify
        shared = {"materials": {"XSCKD1": [{"编码": "M1", "往来": "客户", "_form": "SAL_OUTSTOCK"}]},
                  "heads": {"XSCKD1": {"销售组织": "深圳"}}, "groups": {"M1": "植物肉"},
                  "versions": {"SAL_OUTSTOCK": "v1"}, "missing": []}
        with patch.object(classify, "LR", self.lr), \
             patch.object(self.lr, "_kd_shared_materials", return_value=shared), \
             patch.object(classify, "_bill_docs", return_value=[{"key": "XSCKD1", "docs": ["XSCKD1"], "subject": "深圳星期零", "amount": 10, "manual": ""}]), \
             patch.object(self.lr, "_accr_entries", return_value={"entries": []}), \
             patch.object(classify.lc, "classify", return_value={"docs": [], "tab": []}), \
             patch.object(classify.kc, "login", side_effect=AssertionError("归类不得登录金蝶")):
            out = classify._build("丰源", "2026-09")
        self.assertTrue(out["ok"])
        self.assertEqual(out["material_versions"], {"SAL_OUTSTOCK": "v1"})


class PublicDocumentListTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lr, cls.engine = _load_module()

    def test_purchase_order_full_month_and_operator(self):
        writes = []
        with patch.object(self.lr.kc, "login", return_value=(object(), {})), \
             patch.object(self.lr.kc, "_query", return_value=[{"单号": "PO1", "日期": "2026-09-12", "编码": "M1", "名称": "物料",
                                                               "基本数量": 3, "基本单位": "千克", "往来": "供应商", "销售额": 300,
                                                               "采购组织": "深圳采购"}]), \
             patch.object(self.lr, "_fetch_doc_heads", return_value={}), \
             patch.object(self.lr.kc, "fetch_material_attrs", create=True, return_value={}), \
             patch.object(self.lr.db, "set_period_input", create=True, side_effect=lambda *args: writes.append(args)):
            out = self.lr._kd_refresh_public("2026-09", ["PUR_PurchaseOrder"], operator="测试")
        self.assertEqual(out["refreshed"][0]["n"], 1)
        self.assertEqual(writes[0][-1], "测试")
        self.assertEqual(writes[0][4]["materials"]["PO1"][0]["日期"], "2026-09-12")
        self.assertEqual(writes[0][4]["materials"]["PO1"][0]["采购组织"], "深圳采购")
        self.assertIn("PUR_MRB", self.lr._DOC_MAT_FIELDS)

    def test_documents_aggregate_search_and_page(self):
        rec = {"payload": {"materials": {
            "PO1": [{"编码": "M1", "名称": "植物肉", "基本数量": 2, "基本单位": "千克", "销售额": 100, "往来": "供应商甲", "日期": "2026-09-01"},
                    {"编码": "M2", "名称": "小料", "基本数量": 3, "基本单位": "千克", "销售额": 50, "往来": "供应商甲"}],
            "PO2": [{"编码": "M3", "名称": "鲜食", "基本数量": 1, "基本单位": "箱", "销售额": 80, "往来": "供应商乙"}]},
            "heads": {"PO1": {"销售组织": "深圳"}}, "groups": {"M1": "植物肉", "M2": "小料"},
            "source": "manual_attachment", "material_version": "v1"}, "updated_at": "2026-11-04 12:00", "updated_by": "测试"}
        freight = {"PO1": {"parts": {("承运商甲", "2026-09", True, "v2"): {"tr": 100, "ld": 20, "ot": 0}}}}
        with patch.object(self.lr.db, "get_period_input", create=True, return_value=rec), \
             patch.object(self.lr, "_outstock_freight", return_value=freight):
            out = self.lr._kd_documents("2026-09", ["PUR_PurchaseOrder"], q="", page=1, size=10)
        self.assertEqual(out["total"], 3)
        first = out["rows"][0]
        self.assertEqual(first["subject"], "深圳")
        self.assertEqual(first["material_group"], "植物肉")
        self.assertEqual(first["carrier"], "承运商甲")
        self.assertEqual(first["fee_tr"], 40)
        self.assertEqual(first["fee_ld"], 8)
        self.assertEqual(first["fee"], 48)
        self.assertEqual(first["unit_fee"], 24)
        self.assertEqual(first["ratio"], 0.48)
        self.assertEqual(out["sources"]["PUR_PurchaseOrder"]["by"], "测试")


class StandardBillAndFeeEditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lr, cls.engine = _load_module()

    def test_standard_parser_applies_to_every_carrier(self):
        parsed = {"detail": [], "accrual": []}
        with patch.object(self.lr.stdintake, "parse_standard_bill", return_value=parsed) as standard, \
             patch.object(self.lr.intake, "parse_bill") as legacy:
            out = self.lr._parse_bill_any({"carrier": "链盟"}, b"standard")
        self.assertIs(out, parsed)
        standard.assert_called_once()
        legacy.assert_not_called()

    def test_standard_bill_keeps_aj_and_balances(self):
        self.engine.rows = [[{"id": 7, "doc_no": "XSCKD1", "grain": "detail", "subject": "深圳星期零", "fee_item": "出库运费",
                              "qty": 10, "unit": "千克", "amount": 100, "qty_standard": "按重量", "sub_fees": '{"运费":60,"装卸费":20,"其他费用":20}'}]]
        rows = self.lr._standard_bill_rows("链盟", "2026-09")
        self.assertEqual(len(rows[0]), 10)
        self.assertEqual(rows[0][2], "出库运费")
        self.assertEqual(rows[0][3], "按重量")
        self.assertEqual(rows[0][4], 10)
        self.assertEqual(rows[0][6], 100)
        self.assertEqual(rows[0][7], 60)
        self.assertEqual(rows[0][8], 40)
        self.assertEqual(rows[0][6], rows[0][7] + rows[0][8])

    def test_standard_bill_preserves_source_split_and_precision(self):
        amount = 407.0132418319999
        freight = 400.0132418319999
        self.engine.rows = [[{"id": 8, "doc_no": "XSCKD2", "grain": "detail", "subject": "深圳星期零", "fee_item": "出库运费",
                              "qty": 10, "unit": "kg", "amount": amount, "qty_standard": "按重量",
                              "sub_fees": json.dumps({"运费": freight, "附加费": 7})}]]
        row = self.lr._standard_bill_rows("极鲜达", "2026-09")[0]
        self.assertEqual(row[6], amount)
        self.assertEqual(row[7], freight)
        self.assertEqual(row[8], 7)
        self.assertAlmostEqual(row[6], row[7] + row[8])

    def test_doc_fee_edit_preserves_original_metadata(self):
        import asyncio
        self.engine.rows = [[{"id": 7, "doc_no": "XSCKD1", "grain": "detail", "amount": 100, "sub_fees": '{"标准":100,"运费":100}'}], [], []]
        with patch.object(self.lr, "_perm", return_value={"name": "测试"}), \
             patch.object(self.lr, "_locked", return_value=None), \
             patch.object(self.lr, "_derive_documents", return_value={"refreshed": [], "errors": {}}), \
             patch.object(self.lr, "_bust"), \
             patch.object(self.lr.kc, "login", side_effect=AssertionError("费用编辑不得登录金蝶")):
            out = asyncio.run(self.lr.review_doc_fees(_Request({"carrier": "链盟", "period": "2026-09", "lid": 7,
                                                                  "fee_tr": 50, "fee_ld": 20, "fee_other": 10})))
        self.assertEqual(out["amount"], 80)
        writes = [stmt for conn in self.engine.connections for stmt in conn.writes]
        params = writes[-1].compile().params
        self.assertEqual(params["amount"], 80)
        import json
        meta = json.loads(params["sub_fees"])
        self.assertEqual(meta["__original_amount"], 100)
        self.assertEqual(meta["__original_sub_fees"], '{"标准":100,"运费":100}')
        self.assertEqual((meta["运费"], meta["装卸费"], meta["其他费用"]), (50, 20, 10))

    def test_review_material_rows_split_by_quantity(self):
        docs = [{"lid": 7, "doc_no": "XSCKD1", "subject": "深圳星期零", "parties": ["客户甲"], "materials": [
            {"code": "M1", "name": "植物肉", "base_kg": 2, "kg_unit": "千克", "sales": 100},
            {"code": "M2", "name": "小料", "base_kg": 3, "kg_unit": "千克", "sales": 50}]}]
        freight = {"XSCKD1": {"parts": {("链盟", "2026-09", "signed", "v2"): {"tr": 100, "ld": 20, "ot": 0}}}}
        shared = {"heads": {"XSCKD1": {"销售组织": "深圳"}}, "groups": {"M1": "植物肉", "M2": "小料"}}
        with patch.object(self.lr, "_outstock_freight", return_value=freight), \
             patch.object(self.lr, "_kd_shared_materials", return_value=shared):
            rows = self.lr._review_material_rows("链盟", "2026-09", docs)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["material_group"], "植物肉")
        self.assertEqual(rows[0]["fee_tr"], 40)
        self.assertEqual(rows[0]["fee_ld"], 8)
        self.assertEqual(rows[0]["unit_fee"], 24)
        self.assertEqual(rows[0]["ratio"], 0.48)
        self.assertEqual(rows[1]["unit_fee"], 24)


if __name__ == "__main__":
    unittest.main()
