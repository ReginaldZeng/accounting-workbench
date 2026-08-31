# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-08-01 | Author: Claude / c | Version: V2.157
# Description: 汇率录入·P1 内核单元测试。固定夹具（注入 fetch，不联网）覆盖：详情页解析（含日元100/反向口径）、
#   四舍五入 ROUND_HALF_UP、交叉汇率算式、规则引擎出 8 条、四道机器闸门、历史复核（含已知豁免）。
#   另有一个联网冒烟测（TestLive）抓 2026-06-30 与 2026-07-01，对回样机真值；无网络则自动跳过。
import os
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kernels import fx_rate as fx

D = Decimal


# ---- 合成人行页面夹具 ----
def _anchor(datestr, num):
    t = f"{datestr}中国外汇交易中心受权公布人民币汇率中间价公告"
    href = f"/zhengcehuobisi/125207/125217/125925/{num}/index.html"
    return f'<a href="{href}" title="{t}">{t}</a>'


# 列表页：7月回到5月，跨过 6-01 与 7-01 两个边界
FAKE_LIST = "<html><body>" + "".join([
    _anchor("2026年7月2日", "20260702000000001"),
    _anchor("2026年7月1日", "20260701000000002"),
    _anchor("2026年6月30日", "20260630000000003"),
    _anchor("2026年6月29日", "20260629000000004"),
    _anchor("2026年5月29日", "20260529000000005"),
]) + "</body></html>"

def _detail(datestr, usd, hkd, gbp, extra=""):
    return ("<div>中国人民银行授权中国外汇交易中心公布，" + datestr +
            f"银行间外汇市场人民币汇率中间价为1美元对人民币{usd}元，100日元对人民币4.2045元，"
            f"1港元对人民币{hkd}元，1英镑对人民币{gbp}元，人民币1元对1.1905澳门元。{extra}</div>")

FAKE_DETAILS = {
    "https://www.pbc.gov.cn/zhengcehuobisi/125207/125217/125925/20260630000000003/index.html":
        _detail("2026年6月30日", "6.8109", "0.86855", "9.0145"),
    "https://www.pbc.gov.cn/zhengcehuobisi/125207/125217/125925/20260701000000002/index.html":
        _detail("2026年7月1日", "6.8067", "0.86784", "9.0076"),
    "https://www.pbc.gov.cn/zhengcehuobisi/125207/125217/125925/20260702000000001/index.html":
        _detail("2026年7月2日", "6.8100", "0.86800", "9.0100"),
    "https://www.pbc.gov.cn/zhengcehuobisi/125207/125217/125925/20260629000000004/index.html":
        _detail("2026年6月29日", "6.8120", "0.86900", "9.0200"),
    "https://www.pbc.gov.cn/zhengcehuobisi/125207/125217/125925/20260529000000005/index.html":
        _detail("2026年5月29日", "6.8176", "0.87030", "9.1335"),
}

def fake_fetch(url):
    if url in FAKE_DETAILS:
        return FAKE_DETAILS[url]
    if url.endswith("/125925/index.html"):
        return FAKE_LIST          # 第 1 页
    if "17105-" in url:
        return "<html></html>"    # 第 2 页起为空，collect 停止
    return "<html></html>"


class TestRounding(unittest.TestCase):
    def test_half_up_not_bankers(self):
        self.assertEqual(fx.round4("0.86855"), D("0.8686"))      # 5 进位
        self.assertEqual(fx.round4("1.33745"), D("1.3375"))      # 内建 round 会给 1.3374

    def test_cross_discriminators(self):
        # 记忆里 5 条能区分四舍五入/截断的历史交叉汇率精确值 → 全部四舍五入
        cases = [("1.342277", "1.3423"), ("1.339184", "1.3392"), ("1.317585", "1.3176"),
                 ("0.127653", "0.1277"), ("1.339694", "1.3397")]
        for precise, expect in cases:
            self.assertEqual(fx.round4(precise), D(expect))
            self.assertNotEqual(fx.round4(precise), D(precise[:6]))  # 截断会不同


class TestParseDetail(unittest.TestCase):
    def setUp(self):
        self.d = fx.parse_detail_html(FAKE_DETAILS[
            "https://www.pbc.gov.cn/zhengcehuobisi/125207/125217/125925/20260630000000003/index.html"])

    def test_date_and_forward(self):
        self.assertEqual(self.d["date"].isoformat(), "2026-06-30")
        self.assertEqual(self.d["rates"]["美元"]["raw"], D("6.8109"))
        self.assertEqual(self.d["rates"]["英镑"]["raw"], D("9.0145"))

    def test_jpy_per_100(self):
        # 100 日元对人民币 4.2045 → 1 日元 = 0.042045
        self.assertEqual(self.d["rates"]["日元"]["raw"], D("4.2045") / D("100"))
        self.assertEqual(self.d["rates"]["日元"]["unit"], 100)

    def test_reverse_reciprocal(self):
        # 人民币1元对1.1905澳门元 → 1 澳门元 = 1/1.1905 人民币
        self.assertEqual(self.d["rates"]["澳门元"]["raw"], D(1) / D("1.1905"))


class TestCrossRate(unittest.TestCase):
    def test_hkd_usd(self):
        val, formula = fx.cross_rate(D("0.86855"), D("6.8109"))
        self.assertEqual(val, D("0.1275"))
        self.assertIn("0.86855 ÷ 6.8109", formula)

    def test_gbp_usd(self):
        val, formula = fx.cross_rate(D("9.0145"), D("6.8109"))
        self.assertEqual(val, D("1.3235"))


class TestGenerateRows(unittest.TestCase):
    def setUp(self):
        self.res = fx.generate_rows(2026, 6, org="101", fetch=fake_fetch)
        self.rows = self.res["rows"]

    def test_eight_rows(self):
        self.assertEqual(len(self.rows), 8)
        self.assertEqual(sum(1 for r in self.rows if r["kind"] == "month_end"), 5)
        self.assertEqual(sum(1 for r in self.rows if r["kind"] == "next_range"), 3)

    def test_month_end_values(self):
        me = {(r["from_name"], r["to_name"]): r for r in self.rows if r["kind"] == "month_end"}
        self.assertEqual(me[("美元", "人民币")]["rate"], D("6.8109"))
        self.assertEqual(me[("港币", "人民币")]["rate"], D("0.8686"))   # 0.86855→4位
        self.assertEqual(me[("英镑", "人民币")]["rate"], D("9.0145"))
        self.assertEqual(me[("港币", "美元")]["rate"], D("0.1275"))
        self.assertEqual(me[("英镑", "美元")]["rate"], D("1.3235"))
        self.assertTrue(me[("港币", "美元")]["is_cross"])
        self.assertIn("÷", me[("港币", "美元")]["basis"])

    def test_month_end_dates(self):
        me = [r for r in self.rows if r["kind"] == "month_end"][0]
        self.assertEqual(me["beg_date"], "2026-06-30")
        self.assertEqual(me["end_date"], "2026-06-30")
        self.assertEqual(me["source_date"], "2026-06-30")

    def test_next_range(self):
        nr = {(r["from_name"], r["to_name"]): r for r in self.rows if r["kind"] == "next_range"}
        self.assertEqual(len(nr), 3)  # 只对人民币，不建交叉
        self.assertEqual(nr[("美元", "人民币")]["rate"], D("6.8067"))
        self.assertEqual(nr[("港币", "人民币")]["rate"], D("0.8678"))
        r = nr[("美元", "人民币")]
        self.assertEqual(r["beg_date"], "2026-07-01")
        self.assertEqual(r["end_date"], "2026-07-30")   # 次月最后一天(31)减1
        self.assertEqual(r["source_date"], "2026-07-01")

    def test_all_carry_source(self):
        for r in self.rows:
            self.assertTrue(r["source_url"].startswith("http"))
            self.assertTrue(r["basis"])


class TestGates(unittest.TestCase):
    def setUp(self):
        self.res = fx.generate_rows(2026, 6, org="101", fetch=fake_fetch)

    def test_all_green(self):
        prev = {(r["from_code"], r["to_code"]): r["rate"] for r in self.res["rows"]}
        g = fx.run_gates(self.res, prev_rates=prev)
        self.assertTrue(g["passed"], g["gates"])

    def test_missing_currency_blocks(self):
        res = dict(self.res)
        res["warnings"] = ["缺 英镑 中间价（2026-06-30），缺数即停"]
        g = fx.run_gates(res)
        self.assertFalse(g["passed"])
        self.assertEqual([x["status"] for x in g["gates"] if x["name"] == "缺数"][0], "block")

    def test_wrong_count_blocks(self):
        res = dict(self.res); res["rows"] = self.res["rows"][:6]
        g = fx.run_gates(res)
        self.assertFalse(g["passed"])

    def test_deviation_holds(self):
        # 上月美元给个离谱值，触发偏离闸门 hold
        prev = {("PRE007", "PRE001"): D("5.0000")}
        g = fx.run_gates(self.res, prev_rates=prev, deviation=D("0.03"))
        self.assertFalse(g["passed"])
        self.assertEqual([x["status"] for x in g["gates"] if x["name"] == "偏离上月"][0], "hold")


class TestHistoryCompare(unittest.TestCase):
    def test_known_exempt_and_deviation(self):
        kd = [
            {"org": "101", "from_name": "英镑", "to_name": "美元", "beg_date": "2026-01-31", "rate": D("1.375")},
            {"org": "107", "from_name": "美元", "to_name": "人民币", "beg_date": "2026-06-30", "rate": D("6.8109")},
        ]
        truth = {("英镑", "美元", "2026-01-31"): D("1.3789"),
                 ("美元", "人民币", "2026-06-30"): D("6.8109")}
        out = fx.compare_history(kd, lambda r: truth.get((r["from_name"], r["to_name"], r["beg_date"])))
        self.assertEqual(out[0]["verdict"], "已知豁免")   # 101 的错值在豁免名单
        self.assertEqual(out[1]["verdict"], "一致")


class TestModelAndDedup(unittest.TestCase):
    def test_build_model(self):
        row = {"from_code": "PRE007", "from_name": "美元", "to_code": "PRE001", "to_name": "人民币",
               "rate": D("6.8109"), "beg_date": "2026-06-30", "end_date": "2026-06-30"}
        m = fx.build_rate_model(row, "101")
        self.assertEqual(m["FCreateOrgId"], {"FNumber": "101"})
        self.assertEqual(m["FUseOrgId"], {"FNumber": "101"})
        self.assertEqual(m["FRATETYPEID"], {"FNumber": "HLTX01_SYS"})
        self.assertEqual(m["FCyForID"], {"FNumber": "PRE007"})
        self.assertEqual(m["FExchangeRate"], 6.8109)
        self.assertEqual(m["FBegDate"], "2026-06-30")
        self.assertEqual(list(m.keys())[0], "FCreateOrgId")   # 组织字段在最前
        self.assertIn(fx.FX_MARK, m["FDescription"])          # 描述带来源标记

    def test_build_desc_cross(self):
        row = {"from_code": "PRE002", "from_name": "港币", "to_code": "PRE007", "to_name": "美元",
               "rate": D("0.1275"), "beg_date": "2026-06-30", "end_date": "2026-06-30",
               "basis": "0.86855 ÷ 6.8109 = 0.127524", "source_date": "2026-06-30"}
        d = fx.build_rate_model(row, "101")["FDescription"]
        self.assertIn(fx.FX_MARK, d)
        self.assertIn("0.86855 ÷ 6.8109", d)     # 算式盖进金蝶
        self.assertIn("人行2026-06-30", d)        # 出处盖进金蝶

    def test_find_existing(self):
        existing = [{"使用组织": "101", "原币码": "PRE007", "目标币码": "PRE001",
                     "生效": "2026-06-30T00:00:00", "失效": "2026-06-30T00:00:00", "汇率": "6.8109"}]
        row = {"from_code": "PRE007", "to_code": "PRE001", "beg_date": "2026-06-30", "end_date": "2026-06-30"}
        self.assertIsNotNone(fx.find_existing(existing, row, "101"))          # 同币对同区间命中
        self.assertIsNotNone(fx.find_existing(existing, row, "107"))          # 跨组织：107 也命中(汇率全集团共享,不重叠约束跨组织)
        row2 = dict(row, beg_date="2026-05-31", end_date="2026-05-31")
        self.assertIsNone(fx.find_existing(existing, row2, "101"))           # 不同生效区间→不命中


class TestLive(unittest.TestCase):
    """联网冒烟：抓真实人行公告，对回样机真值。无网络自动跳过。"""
    def test_june_close_real(self):
        try:
            res = fx.generate_rows(2026, 6, org="101")
        except Exception as e:
            self.skipTest(f"无网络/人行不可达，跳过：{e}")
        me = {(r["from_name"], r["to_name"]): r["rate"] for r in res["rows"] if r["kind"] == "month_end"}
        nr = {(r["from_name"], r["to_name"]): r["rate"] for r in res["rows"] if r["kind"] == "next_range"}
        self.assertEqual(me[("美元", "人民币")], D("6.8109"))
        self.assertEqual(me[("港币", "人民币")], D("0.8686"))
        self.assertEqual(me[("英镑", "人民币")], D("9.0145"))
        self.assertEqual(me[("港币", "美元")], D("0.1275"))
        self.assertEqual(me[("英镑", "美元")], D("1.3235"))
        self.assertEqual(nr[("美元", "人民币")], D("6.8067"))
        self.assertEqual(nr[("英镑", "人民币")], D("9.0076"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
