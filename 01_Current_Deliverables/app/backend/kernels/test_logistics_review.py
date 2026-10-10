# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-09 | Author: Claude Opus 5.5 | Version: V2.882
# Description: 物流复核(logistics_review)核量单测——按件数核的快递不再「差 1 件算一致」：50 件以下必须一件不差，50 件以上给 2%。合成数据。
import unittest

from kernels import logistics_review as lr


class QtyCheck(unittest.TestCase):
    def test_default_keeps_one_piece_tolerance(self):
        self.assertEqual(lr.qty_check({"doc_no": "XSCKD1", "qty": 2}, 1)[2], "ok")
        self.assertEqual(lr.qty_check({"doc_no": "XSCKD1", "qty": 4}, 1)[2], "qtydiff")

    def test_exact_small_parcel(self):
        self.assertEqual(lr.qty_check({"doc_no": "XQLCK1", "qty": 2}, 1, True)[2], "qtydiff")
        self.assertEqual(lr.qty_check({"doc_no": "XQLCK1", "qty": 1}, 2, True)[2], "qtydiff")
        self.assertEqual(lr.qty_check({"doc_no": "XQLCK1", "qty": 2}, 2, True)[2], "ok")
        self.assertEqual(lr.qty_check({"doc_no": "XQLCK1", "qty": 40}, 39, True)[2], "qtydiff")

    def test_exact_big_doc_also_exact(self):
        """V2.907：数个数的，大单也必须一个不差(原来 50 件以上留着 2%)。按重量的默认容差不变。"""
        self.assertEqual(lr.qty_check({"doc_no": "QTCK1", "qty": 101}, 100, True)[2], "qtydiff")
        self.assertEqual(lr.qty_check({"doc_no": "QTCK1", "qty": 10399}, 10431, True)[2], "qtydiff")
        self.assertEqual(lr.qty_check({"doc_no": "QTCK1", "qty": 10431}, 10431, True)[2], "ok")
        self.assertEqual(lr.qty_check({"doc_no": "XSCKD1", "qty": 10399}, 10431)[2], "ok")

    def test_review_details_passes_exact(self):
        rows = [{"doc_no": "XQLCK1", "qty": 2, "amount": 4.0}]
        self.assertEqual(lr.review_details([dict(rows[0])], {"express": {}, "unit": {}}, {"XQLCK1": 1})[0]["qty_state"], "ok")
        self.assertEqual(lr.review_details([dict(rows[0])], {"express": {}, "unit": {}}, {"XQLCK1": 1}, True)[0]["qty_state"], "qtydiff")


if __name__ == "__main__":
    unittest.main()
