# -*- coding: utf-8 -*-
# [Change Log] Date:2026-11-04 Author:Kimi Version:V-draft 分类型人工刷新与补核附件零金蝶调用测试。
import unittest
from io import BytesIO
from unittest.mock import patch

from openpyxl import Workbook
from tests.test_logistics_readiness_api import _load_module


class ManualRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lr, cls.engine = _load_module()

    def setUp(self):
        self.lr._ACCR_CACHE.clear()
        self.engine.rows = [[
            {"id": 1, "doc_no": "XSCKD1", "grain": "detail", "amount": 10, "qty": 1, "subject": "深圳星期零"},
            {"id": 2, "doc_no": "QTCK1", "grain": "detail", "amount": 20, "qty": 2, "subject": "深圳星期零"},
            {"id": 3, "doc_no": "FBDC1", "grain": "detail", "amount": 30, "qty": 3, "subject": "深圳星期零"},
        ], [], []]

    def test_partial_refresh_writes_only_selected_partition(self):
        calls, writes = [], []
        def fake_box(rows, _carrier):
            calls.append([r["doc_no"] for r in rows])
            return [{"doc_no": rows[0]["doc_no"], "state": "ok", "materials": []}]
        with patch.object(self.lr, "_load_card", return_value=({}, 0)), \
             patch.object(self.lr, "_load_spec", return_value={}), \
             patch.object(self.lr, "_box_docs", side_effect=fake_box), \
             patch.object(self.lr, "_refresh_classify_snapshot", return_value={"ok": True}), \
             patch.object(self.lr, "_bust"), \
             patch.object(self.lr.db, "set_period_input", create=True, side_effect=lambda *args: writes.append(args)), \
             patch.object(self.lr.db, "get_period_input", create=True, return_value=None):
            result = self.lr._refresh_documents("丰源", "2026-09", ["SAL_OUTSTOCK"])
        self.assertEqual(calls, [["XSCKD1"]])
        self.assertEqual(result["refreshed"][0]["form"], "SAL_OUTSTOCK")
        self.assertEqual([args[3] for args in writes], ["docs:SAL_OUTSTOCK"])

    def test_fallback_xlsx_and_material_override_never_login_kingdee(self):
        wb = Workbook()
        ws = wb.active
        ws.append(self.lr._FALLBACK_HEADERS)
        ws.append(["XSCKD1", "M1", "物料一", 10, "千克", "客户甲", 100, "1kg/箱", 10, "箱", "SRC1"])
        ws.append(["QTCK1", "M2", "物料二", 20, "千克", "客户乙", 200, "2kg/箱", 10, "箱", "SRC2"])
        bio = BytesIO()
        wb.save(bio)
        grouped, _ = self.lr._fallback_materials(bio.getvalue())
        self.assertEqual(grouped["SAL_OUTSTOCK"]["XSCKD1"][0]["基本数量"], 10)
        self.assertEqual(grouped["STK_MisDelivery"]["QTCK1"][0]["名称"], "物料二")
        rows = [{"doc_no": "XSCKD1", "subject": "", "amount": 10, "charge_wt": 10, "unit": "千克", "fee_item": "出库运费"}]
        with patch.object(self.lr.kc, "login", side_effect=AssertionError("人工补核不得调用金蝶"), create=True), \
             patch.object(self.lr, "_load_spec", return_value={}):
            docs = self.lr._box_docs(rows, "丰源", materials_override=grouped["SAL_OUTSTOCK"])
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["materials"][0]["code"], "M1")
        self.assertEqual(docs[0]["kd_sum"], 10)

    def test_empty_quantity_is_rejected(self):
        wb = Workbook()
        ws = wb.active
        ws.append(self.lr._FALLBACK_HEADERS)
        ws.append(["XSCKD1", "M1", "物料一", None, "千克"])
        bio = BytesIO()
        wb.save(bio)
        with self.assertRaisesRegex(ValueError, "基本数量为空"):
            self.lr._fallback_materials(bio.getvalue())


class FallbackUploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lr, cls.engine = _load_module()

    def setUp(self):
        self.lr._ACCR_CACHE.clear()
        self.engine.rows = [[
            {"id": 1, "doc_no": "XSCKD1", "grain": "detail", "amount": 10, "qty": 1, "subject": "深圳星期零"},
        ], [], []]
        wb = Workbook()
        ws = wb.active
        ws.append(self.lr._FALLBACK_HEADERS)
        ws.append(["XSCKD1", "M1", "物料一", 10, "千克"])
        ws.append(["UNKNOWN1", "M2", "未知物料", 20, "千克"])
        bio = BytesIO()
        wb.save(bio)
        self.data = bio.getvalue()

    def request(self, name="9月金蝶补核.xlsx"):
        import types
        req = types.SimpleNamespace(query_params={"carrier": "丰源", "period": "2026-09"}, headers={},
                                    state=types.SimpleNamespace(upload_name=name))
        async def body():
            return self.data
        req.body = body
        return req

    def test_upload_saves_original_snapshot_and_unknown_without_kingdee(self):
        import asyncio
        writes = []
        with patch.object(self.lr, "_perm", return_value={"name": "测试"}), \
             patch.object(self.lr, "_locked", return_value=None), \
             patch.object(self.lr, "_load_card", return_value=({}, 0)), \
             patch.object(self.lr, "_load_spec", return_value={}), \
             patch.object(self.lr, "_box_docs", return_value=[{"doc_no": "XSCKD1", "materials": []}]), \
             patch.object(self.lr, "_bust"), \
             patch.object(self.lr.kc, "login", side_effect=AssertionError("补核上传不得调用金蝶")), \
             patch.object(self.lr.db, "set_period_input", create=True, side_effect=lambda *args: writes.append(args)):
            out = asyncio.run(self.lr.review_material_fallback(self.request()))
        self.assertTrue(out["ok"])
        self.assertEqual(out["unknown"], ["UNKNOWN1"])
        self.assertEqual(out["refreshed"][0]["form"], "SAL_OUTSTOCK")
        self.assertEqual(writes[0][0], "logistics_review:丰源")
        self.assertEqual(writes[0][3], "docs:SAL_OUTSTOCK")
        raw_insert = self.engine.connections[-1].writes[-1]
        params = raw_insert.compile().params
        self.assertEqual(params["name"], "9月金蝶补核.xlsx")
        self.assertEqual(params["role"], "kingdee_fallback")
        self.assertEqual(params["data"], self.data)

    def test_locked_month_rejects_before_parsing_or_writing(self):
        import asyncio
        from fastapi.responses import JSONResponse
        locked = JSONResponse({"ok": False, "msg": "已锁月"}, status_code=409)
        with patch.object(self.lr, "_perm", return_value={"name": "测试"}), \
             patch.object(self.lr, "_locked", return_value=locked), \
             patch.object(self.lr, "_fallback_materials", side_effect=AssertionError("锁月不应解析")):
            out = asyncio.run(self.lr.review_material_fallback(self.request()))
        self.assertIs(out, locked)
        self.assertFalse(any(c.writes for c in self.engine.connections))


class SnapshotScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lr, cls.engine = _load_module()

    def test_carriers_have_independent_storage_sources(self):
        writes = []
        with patch.object(self.lr.db, "set_period_input", create=True, side_effect=lambda *args: writes.append(args)):
            self.lr._docs_snapshot_write("甲", "2026-09", "a", [], "SAL_OUTSTOCK")
            self.lr._docs_snapshot_write("乙", "2026-09", "b", [], "SAL_OUTSTOCK")
        self.assertEqual([args[0] for args in writes], ["logistics_review:甲", "logistics_review:乙"])

    def test_changing_one_form_does_not_invalidate_other_form(self):
        rows = [{"doc_no": "XSCKD1", "amount": 10}, {"doc_no": "QTCK1", "amount": 20}]
        before = self.lr._docs_fingerprints(rows, {}, {}, {})
        changed = [dict(rows[0], amount=11), rows[1]]
        after = self.lr._docs_fingerprints(changed, {}, {}, {})
        self.assertNotEqual(before["SAL_OUTSTOCK"], after["SAL_OUTSTOCK"])
        self.assertEqual(before["STK_MisDelivery"], after["STK_MisDelivery"])


class SnapshotBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lr, cls.engine = _load_module()

    def setUp(self):
        self.lr._ACCR_CACHE.clear()
        self.engine.rows = [[{"id": 1, "doc_no": "XSCKD1", "grain": "detail", "amount": 10, "qty": 1}], [], []]

    def test_unrelated_form_does_not_require_refresh(self):
        from tests.test_logistics_readiness_api import _Request
        rec = {"payload": {"fingerprint": "same", "docs": [{"doc_no": "XSCKD1", "state": "ok", "materials": []}]}, "updated_at": "2026-11-04 12:00"}
        with patch.object(self.lr, "_perm", return_value={"name": "测试"}), \
             patch.object(self.lr, "_load_card", return_value=({}, 0)), \
             patch.object(self.lr, "_doc_ok", return_value={}), \
             patch.object(self.lr, "_code_maps", return_value={}), \
             patch.object(self.lr, "_load_spec", return_value={}), \
             patch.object(self.lr, "_docs_fingerprint", return_value="same"), \
             patch.object(self.lr.db, "get_period_input", create=True,
                          side_effect=lambda source, year, period, kind: rec if kind == "docs:SAL_OUTSTOCK" else None):
            out = self.lr.review_result(_Request(), carrier="丰源", period="2026-09", group="all")
        self.assertEqual(out["needs_refresh"], [])
        self.assertEqual(list(out["snapshot_sources"]), ["SAL_OUTSTOCK"])

    def test_overview_default_reads_snapshot_without_login(self):
        rec = {"payload": {"rows": [], "refreshed_at": "2026-11-04 12:00"}, "updated_at": "2026-11-04 12:00"}
        with patch.object(self.lr.db, "get_period_input", create=True, return_value=rec), \
             patch.object(self.lr.kc, "login", side_effect=AssertionError("总表默认读取不得登录金蝶")):
            rows, at, cached = self.lr._ov_kd_rows("2026-09")
        self.assertEqual((rows, at, cached), ([], "2026-11-04 12:00", True))

    def test_missing_snapshots_block_readiness(self):
        from kernels import logistics_readiness as kernel
        lines = kernel._normal_lines_step({"needs_refresh": True})
        docs = kernel._docs_step({"needs_refresh": ["SAL_OUTSTOCK"]})
        self.assertEqual(lines["status"], "pending")
        self.assertEqual(docs["status"], "pending")
        self.assertIn("SAL_OUTSTOCK", docs["blockers"][0])

    def test_classify_default_reads_matching_snapshot_without_build(self):
        from routers import logistics_classify as classify
        from tests.test_logistics_readiness_api import _Request
        rec = {"payload": {"fingerprint": "same", "result": {"ok": True, "docs": [{"key": "XSCKD1"}]}, "refreshed_at": "2026-11-04 12:00"},
               "updated_at": "2026-11-04 12:00"}
        with patch.object(classify, "db", self.lr.db), \
             patch.object(classify, "LR", self.lr), \
             patch.object(classify, "_snapshot_fingerprint", return_value="same"), \
             patch.object(classify, "_build", side_effect=AssertionError("默认归类不得实时构建")), \
             patch.object(self.lr.db, "get_period_input", create=True, return_value=rec):
            out = classify.classify(_Request(), carrier="丰源", period="2026-09")
        self.assertTrue(out["ok"])
        self.assertEqual(out["cached_at"], "2026-11-04 12:00")
        self.assertEqual(out["docs"][0]["key"], "XSCKD1")


if __name__ == "__main__":
    unittest.main()
