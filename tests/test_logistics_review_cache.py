# -*- coding: utf-8 -*-
# [Change Log] Date:2026-11-04 Author:Kimi Version:V-draft 逐单金蝶限流时最近成功快照回退测试。
import json
import unittest
from unittest.mock import patch

from tests.test_logistics_readiness_api import _load_module, _Request


class ReviewDocsCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lr, cls.engine = _load_module()

    def setUp(self):
        self.lr._ACCR_CACHE.clear()
        self.engine.rows = [[{"id": 1, "doc_no": "XSCKD1", "grain": "detail", "amount": 10, "qty": 1}], [], []]
        self.doc = {"doc_no": "XSCKD1", "state": "ok", "materials": [], "subject": "深圳星期零", "fee_item": "出库运费", "bbiz": "植物肉", "doc_fee": 10}

    def result(self):
        with patch.object(self.lr, "_perm", return_value={"name": "测试"}), \
             patch.object(self.lr, "_load_card", return_value=({}, 0)), \
             patch.object(self.lr, "_doc_ok", return_value={}), \
             patch.object(self.lr, "_code_maps", return_value={}), \
             patch.object(self.lr, "_load_spec", return_value={}), \
             patch.object(self.lr, "_box_docs", side_effect=RuntimeError("金蝶读取单据物料失败：WebAPI限流")):
            out = self.lr.review_result(_Request(), carrier="丰源", period="2026-09", group="all")
            return json.loads(out.body) if hasattr(out, "body") else out

    def test_persistent_snapshot_returns_rows_and_source(self):
        rec = {"payload": {"fingerprint": "same", "docs": [self.doc]}, "updated_at": "2026-11-04 12:00"}
        with patch.object(self.lr, "_docs_fingerprint", return_value="same"), \
             patch.object(self.lr.db, "get_period_input", create=True, side_effect=lambda source, year, period, kind: rec if kind == "docs" else None):
            out = self.result()
        self.assertTrue(out["ok"])
        self.assertEqual(out["cached_at"], "2026-11-04 12:00")
        self.assertEqual(out["snapshot_sources"]["legacy"]["source"], "kingdee")
        self.assertEqual(out["docs"][0]["doc_no"], "XSCKD1")

    def test_expired_memory_is_used_when_no_persistent_snapshot(self):
        self.lr._ACCR_CACHE[("view", "丰源", "2026-09")] = ([self.doc], None, 1)
        with patch.object(self.lr.db, "get_period_input", create=True, return_value=None):
            out = self.result()
        self.assertTrue(out["ok"])
        self.assertEqual(out["docs"][0]["doc_no"], "XSCKD1")
        self.assertEqual(out["snapshot_sources"]["view"]["source"], "kingdee")

    def test_mismatched_snapshot_requires_refresh_instead_of_empty_success(self):
        rec = {"payload": {"fingerprint": "other-bill", "docs": [self.doc]}, "updated_at": "2026-11-04 12:00"}
        with patch.object(self.lr, "_docs_fingerprint", return_value="current-bill"), \
             patch.object(self.lr.db, "get_period_input", create=True, return_value=rec):
            out = self.result()
        self.assertTrue(out["needs_refresh"])
        self.assertEqual(out["docs"], [])
        self.assertIn("人工刷新", out["warning"])

    def test_successful_refresh_writes_snapshot(self):
        report = {"refreshed": [{"form": "SAL_OUTSTOCK", "n": 1}], "errors": {}, "fingerprint": "fp"}
        with patch.object(self.lr, "_perm", return_value={"name": "测试"}), \
             patch.object(self.lr, "_load_card", return_value=({}, 0)), \
             patch.object(self.lr, "_doc_ok", return_value={}), \
             patch.object(self.lr, "_code_maps", return_value={}), \
             patch.object(self.lr, "_load_spec", return_value={}), \
             patch.object(self.lr, "_locked", return_value=None), \
             patch.object(self.lr, "_refresh_documents", return_value=report) as refresh, \
             patch.object(self.lr, "_docs_snapshot_merged", return_value=([self.doc], [], {"SAL_OUTSTOCK": {"at": "2026-11-04 12:00", "source": "kingdee"}})):
            out = self.lr.review_result(_Request(), carrier="丰源", period="2026-09", group="all", fresh=True)
        self.assertTrue(out["ok"])
        self.assertNotIn("stale", out)
        self.assertEqual(out["docs"][0]["doc_no"], "XSCKD1")
        self.assertIn("SAL_OUTSTOCK", refresh.call_args.args[2])

    def test_fingerprint_covers_bill_and_review_settings(self):
        rows = [{"id": 1, "amount": 10}]
        first = self.lr._docs_fingerprint(rows, {}, {"scheme": "weight"}, {})
        self.assertNotEqual(first, self.lr._docs_fingerprint([{"id": 1, "amount": 11}], {}, {"scheme": "weight"}, {}))
        self.assertNotEqual(first, self.lr._docs_fingerprint(rows, {}, {"scheme": "box"}, {}))
        self.assertNotEqual(first, self.lr._docs_fingerprint(rows, {"price": 1}, {"scheme": "weight"}, {}))
        self.assertNotEqual(first, self.lr._docs_fingerprint(rows, {}, {"scheme": "weight"}, {"formula": "new"}))


if __name__ == "__main__":
    unittest.main()
