# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-30 | Author: Kimi | Version: V2.1032
# Description: 物流数据接入按主体勾稽：迅鸽星期零/星期九请款不合并，账单汇总/明细粒度不重复。合成数据。
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "01_Current_Deliverables" / "app" / "backend"
sys.path.insert(0, str(BACKEND))

from routers import logistics_intake_check as lic


class SubjectReconciliation(unittest.TestCase):
    def setUp(self):
        self.reqs = [
            {"subject": "星期零", "amount": 139849.68},
            {"subject": "星期零", "amount": 224.08},
            {"subject": "星期九", "amount": 6471},
            {"subject": "星期九", "amount": 720},
        ]
        self.subject_patches = [
            patch.object(lic.LR, "_eff_subject", lambda r: str(r.get("subj_ovr") or r.get("subject") or "")),
            patch.object(lic.LR, "_short_subject", lambda s: s),
        ]
        for p in self.subject_patches:
            p.start()

    def tearDown(self):
        for p in self.subject_patches:
            p.stop()

    def test_xunge_reqs_are_summed_per_subject(self):
        rows = [{"bill_src": "星期零-starfield", "grain": "detail", "subject": "星期零",
                 "src_sheet": "9月发货明细", "amount": 156982.01}]
        got = {r["subject"]: r for r in lic._subject_reconciliations(rows, self.reqs)}
        self.assertEqual(got["星期零"]["req_total"], 140073.76)
        self.assertEqual(got["星期零"]["n_req"], 2)
        self.assertEqual(got["星期零"]["diff"], 16908.25)
        self.assertEqual(got["星期九"]["req_total"], 7191.00)
        self.assertEqual(got["星期九"]["n_req"], 2)
        self.assertIsNone(got["星期九"]["bill_total"])
        self.assertIsNone(got["星期九"]["diff"])

    def test_each_source_uses_accrual_or_detail_not_both(self):
        rows = [
            {"bill_src": "甲", "grain": "detail", "subject": "星期零", "src_sheet": "明细", "amount": 100},
            {"bill_src": "甲", "grain": "accrual", "subject": "星期零", "src_sheet": "汇总", "amount": 140},
            {"bill_src": "乙", "grain": "detail", "subject": "星期九", "src_sheet": "明细", "amount": 20},
        ]
        got = {r["subject"]: r for r in lic._subject_reconciliations(rows, [])}
        self.assertEqual(got["星期零"]["bill_total"], 140)
        self.assertEqual(got["星期九"]["bill_total"], 20)

    def test_manual_subject_override_applies_to_bill(self):
        rows = [{"bill_src": "甲", "grain": "detail", "subject": "原主体", "subj_ovr": "星期零",
                 "src_sheet": "明细", "amount": 140073.76}]
        got = lic._subject_reconciliations(rows, self.reqs[:2])
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["subject"], "星期零")
        self.assertEqual(got[0]["diff"], 0)


if __name__ == "__main__":
    unittest.main()
