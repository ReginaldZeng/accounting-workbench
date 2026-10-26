# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-26 | Author: Kimi Code | Version: V-draft
# Description: BP 当前有效费用接口契约、内部令牌和旧账单 feed 兼容测试；全部使用桩。
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "app" / "backend"
if not BACKEND.exists():
    BACKEND = ROOT / "01_Current_Deliverables" / "app" / "backend"
sys.path.insert(0, str(BACKEND))


class BpFeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.token = True
        core = types.ModuleType("core")
        core.JSONResponse = JSONResponse
        core.db = types.SimpleNamespace(_engine=object())
        bom = types.ModuleType("routers.bom_quote")
        bom.internal_token_ok = lambda _request: cls.token
        review = types.ModuleType("routers.logistics_review")
        review._eff_subject = lambda row: row.get("subject")
        review._eff_fee = lambda row: row.get("fee_item")
        review._line_buckets = lambda amount, fee_item, sub: {"tr": float(amount), "ld": 0, "ot": 0}
        with patch.dict(sys.modules, {"core": core, "routers.bom_quote": bom, "routers.logistics_review": review}):
            from routers import bp_logistics_feed
        cls.module = bp_logistics_feed
        cls.db = core.db

    def resolved(self):
        return {("D1", "C", "运费"): {
            "doc_no": "D1", "carrier": "C", "fee_type": "运费", "v1": 20, "v2": 30, "pending": 5,
            "has_v1": True, "has_v2": True, "has_effective": True, "effective": 30,
            "effective_version": "v2", "difference": 10, "subjects": ["主体甲"], "biz_lines": ["小料"],
            "fee_items": ["运费"], "sources": [{"version": "v2", "period": "2026-09", "amount": 30}],
        }}

    def test_internal_token_required(self):
        type(self).token = False
        response = self.module.bp_doc_freight(object(), "2026-09")
        self.assertEqual(response.status_code, 401)
        type(self).token = True

    def test_effective_contract(self):
        from kernels import logistics_doc_freight as ldf
        with patch.object(ldf, "load_resolved", return_value=self.resolved()) as load:
            result = self.module.bp_doc_freight(object(), "2026-09")
        load.assert_called_once()
        self.assertEqual(result["periodMode"], "fee_period")
        self.assertEqual(result["summary"]["effectiveAmount"], 30)
        self.assertEqual(result["summary"]["versionDiff"], 10)
        line = result["lines"][0]
        self.assertEqual((line["effectiveVersion"], line["effectiveAmount"], line["tr"]), ("v2", 30, 30))
        self.assertEqual((line["v1Amount"], line["v2Amount"], line["pendingAmount"]), (20, 30, 5))

    def test_old_build_feed_still_returns_bill_lines(self):
        result = self.module.build_feed("2026-09", [], [], [], [], lambda x: x, lambda x: x,
                                        self.module.lrv._line_buckets)
        self.assertEqual(result["lines"], [])
        self.assertEqual(result["period"], "2026-09")


if __name__ == "__main__":
    unittest.main()
