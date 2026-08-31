# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-08-03 | Author: Claude / c | Version: V2.168
# Description: 账户台账内核·归行单元测试。重点回归 bank_of_row：数据源覆盖按【账号→官方开户行】归行，
#   主数据/台账优先、行内"银行"标签兜底——财资平台混合标签"宁波/招商"不再把招商笔数全归宁波。
import os
import sys
import unittest
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kernels import account_ledger as al


class TestBankOfRow(unittest.TestCase):
    """V2.168：招商账号挂着"宁波/招商"混合标签时，按账号查官方开户行必须归到招商。"""

    def test_master_wins_over_mixed_label(self):
        master = {"755953100010001": {"开户行": "招商银行东莞分行", "账户类型": "一般户"}}
        self.assertEqual(al.bank_of_row("755953100010001", "宁波/招商", master, {}), "招商银行")

    def test_master_legacy_string_format(self):
        master = {"755953100010001": "招商银行"}   # 旧格式：只存开户行字符串
        self.assertEqual(al.bank_of_row("755953100010001", "宁波/招商", master, {}), "招商银行")

    def test_ledger_fallback_raw_key(self):
        lmap = {"73110122000157061": {"开户行": "宁波银行孝感支行"}}
        self.assertEqual(al.bank_of_row("73110122000157061", "宁波/招商", {}, lmap), "宁波银行")

    def test_ledger_fallback_norm_key(self):
        lmap = {"121941077310901": {"开户行": "招商银行上海分行"}}
        # 账号被 Excel 存成数值带 .0 时，原样键查无、按数字键应命中
        self.assertEqual(al.bank_of_row("121941077310901.0", "宁波/招商", {}, lmap), "招商银行")
        self.assertEqual(al.bank_of_row("121941077310901", "宁波/招商", {}, lmap), "招商银行")

    def test_no_map_falls_back_to_label(self):
        # 查无账号时保持既有行为：按标签认字，"宁波/招商"因别名表顺序归宁波（已知偏置，锁行为）
        self.assertEqual(al.bank_of("宁波/招商"), "宁波银行")
        self.assertEqual(al.bank_of_row("999999999999", "宁波/招商", {}, {}), "宁波银行")
        self.assertEqual(al.bank_of_row("999999999999", "中国银行", {}, {}), "中国银行")

    def test_all_empty(self):
        self.assertEqual(al.bank_of_row("", "", {}, {}), "")
        self.assertEqual(al.bank_of_row("999999999999", "", None, None), "")

    def test_coverage_split_aggregate(self):
        """混合标签下按主数据分账归堆：招商笔数从宁波里拆出来（复刻覆盖表场景）。"""
        master = {"73110122000157061": {"开户行": "宁波银行"},
                  "755953100010001": {"开户行": "招商银行"},
                  "712900412410308": {"开户行": "招商银行"}}
        rows = [{"账号": "73110122000157061", "银行": "宁波/招商"},
                {"账号": "73110122000157061", "银行": "宁波/招商"},
                {"账号": "755953100010001", "银行": "宁波/招商"},
                {"账号": "712900412410308", "银行": "宁波/招商"},
                {"账号": "88888888", "银行": "花旗银行"}]
        c = Counter(al.bank_of_row(r["账号"], r["银行"], master, {}) or "其他" for r in rows)
        self.assertEqual(c, Counter({"宁波银行": 2, "招商银行": 2, "花旗银行": 1}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
