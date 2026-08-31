# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-08-07 | Author: Claude / c | Version: V2.238
# Description: 资金看板内核单测。锁死两件事：①三段六列(期初/本期变动/期末 × 原币/本位币)自洽；
#   ②「只在序时账里出现的本期新开维度」必须计入——真实事故：2026-07 新买理财 1.1 亿因余额表
#   尚无该维度行而在看板上凭空消失，被误读成"7月资金大降"。
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kernels import balance_dashboard as bd


def bal_row(book, code, dim, begin, cur="人民币", year=2026, period=7):
    return {"账簿": book, "科目编码": code, "科目名称": "测试科目", "币别": cur,
            "核算维度.银行账号.编码": dim, "期初原币": begin, "期初本位币": begin,
            "期末原币": begin, "期末本位币": begin, "年": year, "期": period}


def vou_row(book, code, dim, debit=0.0, credit=0.0, cur="人民币", vno="37", with_book=True):
    r = {"科目编码": code, "科目名称": "测试科目", "FVOUCHERGROUPID.FName": "记",
         "FVOUCHERGROUPNO": vno, "FDATE": "2026-07-02",
         "FDetailID.FF100002.FNumber": dim, "FDEBIT": debit, "FCREDIT": credit,
         "FAMOUNTFOR": (debit or credit), "FCURRENCYID.FName": cur}
    if with_book:
        r["账簿"] = book                      # 旧定格数据没有这列（V2.238 之前的取数）
    return r


class TestThreeSegments(unittest.TestCase):
    """三段：期末 = 期初 + 本期变动，且原币/本位币各自成立。"""

    def test_begin_plus_move_equals_end(self):
        rows = [bal_row("甲公司", "1002", "6001", 1000.0)]
        vous = [vou_row("甲公司", "1002", "6001", debit=500.0),
                vou_row("甲公司", "1002", "6001", credit=200.0)]
        recs = bd.load_balance(rows, vous)
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r.begin_base, 1000.0)
        self.assertEqual(r.move_base, 300.0)
        self.assertEqual(r.end_base, 1300.0)
        self.assertEqual(round(r.begin_base + r.move_base, 2), r.end_base)

    def test_no_voucher_mode_move_from_fields(self):
        # 样例/已过账模式（不传序时账）：本期变动 = 期末 − 期初
        rows = [{"账簿": "甲公司", "科目编码": "1002", "科目名称": "银行存款", "币别": "人民币",
                 "核算维度.银行账号.编码": "6001", "期初原币": 100.0, "期初本位币": 100.0,
                 "期末原币": 260.0, "期末本位币": 260.0, "年": 2026, "期": 7}]
        r = bd.load_balance(rows, None)[0]
        self.assertEqual(r.begin_base, 100.0)
        self.assertEqual(r.move_base, 160.0)
        self.assertEqual(r.end_base, 260.0)


class TestVoucherOnlyDimension(unittest.TestCase):
    """V2.238 事故复刻：本期新开维度（余额表无此行）必须计入，不能凭空消失。"""

    def test_new_dimension_counted(self):
        rows = [bal_row("孝感九", "1002", "117736", 100000000.0)]      # 银行存款期初 1 亿
        vous = [vou_row("孝感九", "1002", "117736", credit=80000000.0),  # 划走 8000 万买理财
                vou_row("孝感九", "1012", "24135013B华夏理财悦慧最短持有7天C款B", debit=80000000.0)]
        recs = bd.load_balance(rows, vous)
        total_end = round(sum(r.end_base for r in recs), 2)
        self.assertEqual(total_end, 100000000.0, "钱只是换了科目，集团总额不该变")
        newr = [r for r in recs if r.vou_only]
        self.assertEqual(len(newr), 1)
        self.assertEqual(newr[0].cat_code, "1012")
        self.assertEqual(newr[0].end_base, 80000000.0)
        self.assertEqual(newr[0].begin_base, 0.0)
        self.assertEqual(newr[0].entity, "孝感九", "新维度要认得出主体（取自序时账账簿列）")

    def test_entity_fallback_when_book_column_missing(self):
        # 旧定格数据（序时账无「账簿」列）：从同一张凭证的其它分录反查主体——
        # 记-37 的 1002 付款腿维度 117736 在余额表里，主体=孝感九，新维度据此继承
        rows = [bal_row("孝感九", "1002", "117736", 100000000.0)]
        vous = [vou_row("孝感九", "1002", "117736", credit=80000000.0, with_book=False),
                vou_row("孝感九", "1012", "24135013B华夏理财", debit=80000000.0, with_book=False)]
        recs = bd.load_balance(rows, vous)
        newr = [r for r in recs if r.vou_only]
        self.assertEqual(len(newr), 1)
        self.assertEqual(newr[0].entity, "孝感九", "无账簿列时应由同凭证反查出主体")

    def test_entity_left_blank_when_ambiguous(self):
        # 反查有歧义（同字号同日的凭证跨两个主体）→ 不猜，给出待刷新占位
        rows = [bal_row("甲公司", "1002", "A001", 100.0), bal_row("乙公司", "1002", "B001", 100.0)]
        vous = [vou_row("", "1002", "A001", credit=50.0, vno="9", with_book=False),
                vou_row("", "1002", "B001", credit=50.0, vno="9", with_book=False),
                vou_row("", "1012", "新产品Z", debit=100.0, vno="9", with_book=False)]
        recs = bd.load_balance(rows, vous)
        newr = [r for r in recs if r.vou_only]
        self.assertEqual(len(newr), 1)
        self.assertEqual(newr[0].entity, "（主体待刷新）")

    def test_new_dimension_net_zero_skipped(self):
        # 当月买入又赎回、净额为 0 的新维度不列（不制造零余额噪声行）
        rows = [bal_row("甲公司", "1002", "6001", 500.0)]
        vous = [vou_row("甲公司", "1012", "临时产品X", debit=1000.0),
                vou_row("甲公司", "1012", "临时产品X", credit=1000.0)]
        recs = bd.load_balance(rows, vous)
        self.assertEqual([r for r in recs if r.vou_only], [])

    def test_dashboard_guardrail_and_totals(self):
        rows = [bal_row("孝感九", "1002", "117736", 100000000.0)]
        vous = [vou_row("孝感九", "1002", "117736", credit=80000000.0),
                vou_row("孝感九", "1012", "24135013B华夏理财", debit=80000000.0)]
        d = bd.build_dashboard(bd.load_balance(rows, vous))
        self.assertEqual(d["集团期初"], 100000000.0)
        self.assertEqual(d["集团本期变动"], 0.0)
        self.assertEqual(d["集团合计"], 100000000.0)
        self.assertTrue(d["guardrail"]["期初+本期变动=期末"])
        self.assertEqual(d["guardrail"]["本期新开维度账户"], 1)
        a = [x for x in d["accounts"] if x["本期新开"]][0]
        self.assertEqual(a["期初余额(本位币)"], 0.0)
        self.assertEqual(a["本期变动(本位币)"], 80000000.0)
        self.assertEqual(a["期末余额(本位币)"], 80000000.0)
        self.assertIn("华夏理财", a["账号"], "理财维度要显示产品名，不能抽成纯数字")


if __name__ == "__main__":
    unittest.main(verbosity=2)
