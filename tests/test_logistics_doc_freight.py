# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-26 | Author: Kimi Code | Version: V-draft
# Description: 当前有效逐单费用纯函数测试：V2优先、零值优先、混合费用键、待复核不双计、跨期读取。
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "app" / "backend"
if not BACKEND.exists():
    BACKEND = ROOT / "01_Current_Deliverables" / "app" / "backend"
sys.path.insert(0, str(BACKEND))

from kernels import logistics_doc_freight as ldf


def buckets(amount, fee_item, _sub):
    key = "ld" if any(x in str(fee_item) for x in ("装", "卸")) else "tr"
    return {"tr": 0.0, "ld": 0.0, "ot": 0.0, key: float(amount)}


class ResolveTests(unittest.TestCase):
    def test_v2_wins_per_fee_key_and_pending_is_not_added(self):
        v1 = [
            {"doc_no": "D1", "carrier": "C", "fee_type": "销售出库运费", "amount": 20, "period": "2026-09"},
            {"doc_no": "D1", "carrier": "C", "fee_type": "销售成品装货费", "amount": 10, "period": "2026-09"},
        ]
        bills = [
            {"id": 1, "doc_no": "D1", "carrier": "C", "period": "2026-09", "review_mode": "audit", "fee_item": "运费", "amount": 30},
            {"id": 2, "doc_no": "D1", "carrier": "C", "period": "2026-08", "review_mode": "audit", "fee_item": "装货费", "amount": 5},
        ]
        resolved = ldf.resolve_fee_lines(v1, bills, [{"carrier": "C", "period": "2026-09", "status": "signed"}], buckets)
        tr = resolved[("D1", "C", "运费")]
        ld = resolved[("D1", "C", "装卸费")]
        self.assertEqual((tr["effective_version"], tr["effective"], tr["difference"]), ("v2", 30, 10))
        self.assertEqual((ld["effective_version"], ld["effective"], ld["pending"]), ("v1", 10, 5))
        doc = ldf.document_view(resolved)["D1"]
        self.assertEqual(doc["tr"] + doc["ld"] + doc["ot"], 40)
        self.assertEqual(doc["pending"], 5)
        self.assertEqual(doc["effective_version"], "mixed")

    def test_signed_zero_only_overrides_its_own_fee_type(self):
        v1 = [{"doc_no": "D1", "carrier": "C", "fee_type": "销售出库运费", "amount": 20},
              {"doc_no": "D1", "carrier": "C", "fee_type": "原辅料卸货费", "amount": 8}]
        bills = [{"doc_no": "D1", "carrier": "C", "period": "2026-09", "review_mode": "audit", "fee_item": "运费", "amount": 0}]
        resolved = ldf.resolve_fee_lines(v1, bills, [{"carrier": "C", "period": "2026-09", "status": "signed"}], buckets)
        self.assertEqual(resolved[("D1", "C", "运费")]["effective"], 0)
        self.assertEqual(resolved[("D1", "C", "运费")]["effective_version"], "v2")
        self.assertEqual(resolved[("D1", "C", "装卸费")]["effective"], 8)

    def test_pending_only_is_unsubmitted_not_v1(self):
        bills = [{"doc_no": "D1", "carrier": "C", "period": "2026-09", "review_mode": "audit", "fee_item": "运费", "amount": 20}]
        resolved = ldf.resolve_fee_lines([], bills, [], buckets)
        doc = ldf.document_view(resolved)["D1"]
        self.assertEqual(doc["effective_version"], "unsubmitted")
        self.assertTrue(doc["unsubmitted"])
        self.assertEqual(doc["tr"] + doc["ld"] + doc["ot"], 0)
        self.assertEqual(doc["pending"], 20)

    def test_multi_doc_bill_is_split_and_register_is_v2(self):
        bills = [{"doc_no": "D1+D2", "carrier": "C", "period": "2026-08", "review_mode": "register", "fee_item": "运费", "amount": 30}]
        resolved = ldf.resolve_fee_lines([], bills, [], buckets)
        self.assertEqual(resolved[("D1", "C", "运费")]["effective"], 15)
        self.assertEqual(resolved[("D2", "C", "运费")]["effective_version"], "v2")


if __name__ == "__main__":
    unittest.main()
