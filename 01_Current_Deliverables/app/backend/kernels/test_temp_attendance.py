# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-08-18 | Author: Claude / c | Version: V2.318
# Description: 临时工考勤内核单测。合成夹具（内存造 xlsx，不依赖真表）覆盖：
#   姓名归一与歧义不猜、日期列按位置定锚（周末表头写「六」「日」）、取整方向、
#   白班/夜班切班（含跨零点缝合与归班日＝上班日）、四档判定与弹性、计价规则。
#   末尾 TestRealSample 是真表回归（7 月两表），文件不在时自动跳过。
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kernels import temp_attendance as ta

from openpyxl import Workbook


def _book(rows):
    """rows: [[...], ...] → xlsx 字节。"""
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def _punch_book(people, day_labels=None):
    """造一份打卡表：第3行表头、第4行日期行、第5行起数据。
    day_labels 默认模拟真表——周末写「六」「日」而不是数字。"""
    labels = day_labels or ["1", "2", "3", "六", "日", "6", "7"]
    rows = [["打卡时间 统计日期：2026-07-01 至 2026-08-01"], ["报表生成时间"],
            ["姓名", "考勤组", "部门", "打卡时间"] + [""] * (len(labels) - 1),
            ["", "", ""] + labels]
    for nm, grp, dept, cells in people:
        rows.append([nm, grp, dept] + list(cells))
    return _book(rows)


def _summary_book(people, kind_label="备注"):
    rows = [["2026年7月临时工劳务明细汇总表"],
            ["白班19/小时/人，夜班服务费调整为22元/小时/人。"],
            ["序号", "姓名", "部门", "归属", kind_label] + ["星期"] * 7 + ["白班\n工时", "夜班\n工时", "总工时"],
            ["", "", "", "", ""] + [1, 2, 3, 4, 5, 6, 7]]
    for i, (nm, dept, agency, kind, days, dh, nh) in enumerate(people, 1):
        rows.append([i, nm, dept, agency, kind] + list(days) + [dh, nh, (dh or 0) + (nh or 0)])
    return _book(rows)


def _summary_with_pay(people, kind_label="备注"):
    """带金额区的汇总表——真表右侧那 13 列（单价/工资/补贴奖罚/员工工资/管理费/合计）。
    列头是两行：上行大类（跨列合并成一个标题），下行小类。
    ⚠「白班单价」在工资和管理费两个大类下各出现一次，是本夹具的关键——只认小类必然张冠李戴。
    people 元素：(姓名, 部门, 归属, 岗位, days, 白班时, 夜班时, pay dict)"""
    top = ["序号", "姓名", "部门", "归属", kind_label] + ["星期"] * 7 + ["白班\n工时", "夜班\n工时", "总工时"]
    top += ["工时工资", "", "", "", "蒸练\n补贴", "奖", "罚", "员工工资", "管理费", "", "", "", "合计"]
    sub = ["", "", "", "", ""] + [1, 2, 3, 4, 5, 6, 7] + ["", "", ""]
    sub += ["白班\n单价", "白班\n工资", "夜班\n单价", "夜班\n工资", "", "", "", "",
            "白班\n单价", "白班\n管理费", "夜班\n单价", "夜班\n管理费", ""]
    rows = [["2026年7月临时工劳务明细汇总表"],
            ["白班19/小时/人，夜班服务费调整为22元/小时/人。\n"
             "华顺、恒祺、锦绣白班员工工资16.5元/H，管理费2.5元/H；夜班员工工资19元/H；管理费3元/H."],
            top, sub]
    for i, (nm, dept, agency, kind, days, dh, nh, pay) in enumerate(people, 1):
        g = lambda k: pay.get(k, 0)
        rows.append([i, nm, dept, agency, kind] + list(days) + [dh, nh, (dh or 0) + (nh or 0)]
                    + [g("白单"), g("白工资"), g("夜单"), g("夜工资"),
                       g("补贴"), g("奖"), g("罚"), g("员工工资"),
                       g("白管单"), g("白管理"), g("夜管单"), g("夜管理"), g("合计")])
    return _book(rows)


class TestNameMatch(unittest.TestCase):
    def test_norm_strips_tails(self):
        self.assertEqual(ta.norm_name("张红霞（离职）"), "张红霞")
        self.assertEqual(ta.norm_name("黄亚军0415"), "黄亚军")
        self.assertEqual(ta.norm_name("鲁南阳（驻场）（离职）"), "鲁南阳")

    def test_exact_raw_wins_over_normalized(self):
        pk = ta.parse_punch(_punch_book([
            ("黄亚军", "组A", "临时普工-锦绣人力", ["08:00\n17:30"] + [""] * 6),
            ("黄亚军0415", "组B", "临时普工-锦绣人力", ["07:00\n18:00"] + [""] * 6),
        ]))
        rec, cand = ta.match_punch(pk, "黄亚军0415")
        self.assertIsNone(cand)
        self.assertEqual(rec["组"], "组B")          # 命中原名那一行，不被归一带偏

    def test_ambiguous_returns_candidates_not_a_guess(self):
        """归一后撞上两个人时必须返回候选交人工，不能随便挑一个——猜错就是算错工资。"""
        pk = ta.parse_punch(_punch_book([
            ("张博", "组A", "临时普工-锦绣人力", ["08:00\n17:30"] + [""] * 6),
            ("张博G（离职）", "组B", "临时普工-锦绣人力", ["08:00\n17:30"] + [""] * 6),
        ]))
        rec, cand = ta.match_punch(pk, "张博X")
        self.assertIsNone(rec)
        self.assertEqual(sorted(cand), ["张博", "张博G（离职）"])


class TestDayColumns(unittest.TestCase):
    def test_weekend_headers_do_not_shift_dates(self):
        """真表把周末表头写成「六」「日」。只挑数字列会让整段日期错位（上线前实测误报 25 人）。"""
        pk = ta.parse_punch(_punch_book([
            ("甲", "组", "临时普工-锦绣人力", ["08:00\n17:30", "", "", "", "", "08:00\n17:30", ""]),
        ]))
        days = pk["by_raw"]["甲"][0]["days"]
        self.assertEqual(sorted(days), [1, 6])      # 第6列标「6」，不是第4天

    def test_all_numeric_headers(self):
        pk = ta.parse_punch(_punch_book(
            [("甲", "组", "临时普工", ["08:00\n17:30"] * 7)],
            day_labels=[str(i) for i in range(1, 8)]))
        self.assertEqual(sorted(pk["by_raw"]["甲"][0]["days"]), list(range(1, 8)))


class TestRounding(unittest.TestCase):
    def test_floor_is_the_verified_rule(self):
        p = dict(ta.DEFAULT_PARAMS)
        self.assertEqual(ta.round_step(8.7, p), 8.5)
        self.assertEqual(ta.round_step(8.99, p), 8.5)
        self.assertEqual(ta.round_step(9.0, p), 9.0)

    def test_round_mode_switch(self):
        p = dict(ta.DEFAULT_PARAMS, round_mode="round")
        self.assertEqual(ta.round_step(8.7, p), 8.5)
        self.assertEqual(ta.round_step(8.8, p), 9.0)


class TestShifts(unittest.TestCase):
    P = dict(ta.DEFAULT_PARAMS)

    def test_day_shift_span_minus_lunch(self):
        # 07:20–17:32 = 10.2h → 向下取整 10.0 → 扣 1 小时 = 9.0
        sh = ta.compute_shifts({1: [7 * 60 + 20, 17 * 60 + 32]}, "day", self.P)
        self.assertEqual(sh[1]["hours"], 9.0)

    def test_cleaner_53_day_rule(self):
        """保洁实证：向下取整跨度 − 1 小时。07:21–17:31＝10.17→10.0→9.0"""
        sh = ta.compute_shifts({1: [7 * 60 + 21, 17 * 60 + 31]}, "day", self.P)
        self.assertEqual(sh[1]["hours"], 9.0)

    def test_night_shift_stitches_across_midnight(self):
        """19:45 上班 → 次日 08:33 下班 = 12.8h，扣 0.5 夜宵 → 向下取整 12.0；归班日＝上班日。"""
        days = {4: [19 * 60 + 45, 23 * 60 + 22], 5: [8 * 60 + 33]}
        sh = ta.compute_shifts(days, "night", self.P)
        self.assertEqual(sorted(sh), [4])           # 只有 4 日成班，5 日被并入
        self.assertEqual(sh[4]["hours"], 12.0)

    def test_night_end_takes_last_punch_before_window(self):
        """次日 00:25 是夜宵卡、08:41 才是下班。取「窗口内最后一次」而不是第一次——
        取第一次会把 12.6 小时算成 4.6，凭空造出一堆假的『多记』。"""
        days = {7: [20 * 60 + 4], 8: [25, 3 * 60 + 12, 8 * 60 + 41]}
        sh = ta.compute_shifts(days, "night", self.P)
        self.assertEqual(sh[7]["hours"], 12.0)

    def test_night_early_off_at_0549_still_counts(self):
        days = {12: [19 * 60 + 51], 13: [5 * 60 + 49]}
        sh = ta.compute_shifts(days, "night", self.P)
        self.assertEqual(sh[12]["hours"], 9.0)      # 9.97 − 0.5 = 9.47 → 9.0

    def test_single_punch_makes_no_shift(self):
        self.assertEqual(ta.compute_shifts({1: [8 * 60]}, "day", self.P), {})


RULES_TEXT = ('白班19/小时/人，夜班服务费调整为22元/小时/人。           \n'
              '华顺、恒祺、锦绣白班员工工资16.5元/H，管理费2.5元/H；夜班员工工资19元/H；管理费3元/H.\n'
              '成达、广才、天幕、鑫路达白班员工工资17元/H，管理费2元/H；夜班员工工资19元/H；管理费3元/H.\n'
              '锦绣保洁：员工工资15元/小时，无管理费无夜班')


T = None   # 在 TestRate 里赋值：观察表只是测试夹具，不参与任何判定


class TestRate(unittest.TestCase):
    """rate_of 是纯查表：给什么表查什么表，没有默认值。"""

    @classmethod
    def setUpClass(cls):
        global T
        T = ta.RATE_TABLE_OBSERVED

    def test_day_and_night_are_different_bands(self):
        """单价按「派遣方 × 岗位 × 班次」取。拿白班单价顶夜班正是成本会计底表算多钱的原因之一。"""
        self.assertEqual(ta.rate_of({"agency": "锦绣", "kind": ""}, "day", T)[:3], (16.5, 2.5, 19.0))
        self.assertEqual(ta.rate_of({"agency": "锦绣", "kind": ""}, "night", T)[:3], (19.0, 3.0, 22.0))
        self.assertEqual(ta.rate_of({"agency": "广才", "kind": ""}, "day", T)[:3], (17.0, 2.0, 19.0))
        self.assertEqual(ta.rate_of({"agency": "天募", "kind": ""}, "day", T)[2], 19.0)   # 天幕/天募两种写法

    def test_post_is_scoped_to_its_agency(self):
        """保洁是【锦绣的】保洁，不是全局保洁——规则原文写的就是「锦绣保洁」。
        华顺若也有保洁，必须单独建档；拿锦绣的 15 元套上去就是算错钱。"""
        self.assertEqual(ta.rate_of({"agency": "锦绣", "kind": "保洁"}, "day", T)[:3], (15.0, 0.0, 15.0))
        w, m, r, why = ta.rate_of({"agency": "华顺", "kind": "保洁"}, "day", T)
        self.assertEqual(r, 0.0)
        self.assertIn("华顺", why)
        self.assertIn("保洁", why)

    def test_unknown_post_does_not_fall_back_to_default(self):
        """岗位不在该派遣方名下时**不回落普工**——保洁按普工价算是每小时多 4 元。"""
        w, m, r, why = ta.rate_of({"agency": "锦绣", "kind": "保安"}, "day", T)
        self.assertEqual(r, 0.0)
        self.assertIn("补一行", why)

    def test_empty_post_means_default(self):
        self.assertEqual(ta.rate_of({"agency": "锦绣", "kind": ""}, "day", T)[3], f"锦绣·{ta.POST_DEFAULT}·白班")

    def test_post_without_night_returns_zero_with_reason(self):
        w, m, r, why = ta.rate_of({"agency": "锦绣", "kind": "保洁"}, "night", T)
        self.assertEqual(r, 0.0)
        self.assertIn("无夜班", why)

    def test_unknown_agency_returns_zero_with_reason_not_a_guess(self):
        w, m, r, why = ta.rate_of({"agency": "某新公司", "kind": ""}, "day", T)
        self.assertEqual(r, 0.0)
        self.assertIn("不在单价表", why)


class TestRateRules(unittest.TestCase):
    def test_parse_header_rules(self):
        t, n, h = ta.parse_rate_rules(RULES_TEXT)
        self.assertEqual(t["华顺"][ta.POST_DEFAULT]["day"], (16.5, 2.5))
        self.assertEqual(t["华顺"][ta.POST_DEFAULT]["night"], (19.0, 3.0))
        self.assertEqual(t["广才"][ta.POST_DEFAULT]["day"], (17.0, 2.0))
        self.assertEqual(h, {"day": 19.0, "night": 22.0})
        self.assertEqual(n, [])                       # 表头三行全部认得，无「没看懂」

    def test_cleaner_is_parsed_under_its_own_agency(self):
        """「锦绣保洁：…」必须落到 锦绣 名下，不能变成全局保洁。"""
        t, n, h = ta.parse_rate_rules(RULES_TEXT)
        self.assertEqual(t["锦绣"]["保洁"]["day"], (15.0, 0.0))
        self.assertIsNone(t["锦绣"]["保洁"]["night"])
        self.assertNotIn("保洁", t.get("华顺", {}))

    def test_parsed_header_is_reference_only(self):
        """表头解析出来的表**不参与计算**：compute 的 rates 段只把它原样带回去作参考。"""
        sm = ta.parse_summary(_summary_book([("甲", "植物肉", "锦绣", "", [10] * 7, 70, 0)]))
        pk = ta.parse_punch(_punch_book([("甲", "组", "临时普工-锦绣人力", ["07:00\n19:00"] * 7)]))
        res = ta.compute(sm, pk)                    # 不传合同价
        self.assertEqual(res["rates"]["合同表"], {})
        self.assertIn("表头解析", res["rates"])
        self.assertIsNone(res["people"][0]["应付合计"])   # 表头再清楚，也不能拿来算应付


class TestContractShapes(unittest.TestCase):
    """合同价表进内核前要过 contract_only → _as_nested，三种历史形态都得认，脏值不能炸。"""

    def test_page_whole_state_shape_does_not_crash(self):
        """前端曾把整个 state 发过来（含「默认岗位」这种字符串字段），后端拿它当派遣方遍历，
        直接炸 'str' object has no attribute 'items' —— 服务器上一上传就「解析失败」。"""
        whole = {"table": {"锦绣": {ta.POST_DEFAULT: {"day": [20, 1], "night": None}}},
                 "默认岗位": "普工", "解析自表头": True}
        table = ta.contract_only(whole)
        self.assertEqual(table["锦绣"][ta.POST_DEFAULT]["day"], (20, 1))

    def test_dirty_values_are_skipped_not_fatal(self):
        """单价表是人手填的，遇到脏值宁可少收一格，也不能让整页打不开。"""
        table = ta.contract_only({"锦绣": "普工", "华顺": {ta.POST_DEFAULT: "x"}, "空": None,
                                  "恒祺": {ta.POST_DEFAULT: {"day": [16.5, 2.5]}}})
        self.assertEqual(set(table), {"恒祺"})

    def test_day_missing_is_none_not_zero(self):
        """只登记夜班价的行：白班必须是 None（缺档），不能造出 (0,0) 让对比页报「不符 合同 0+0」。"""
        t = ta.contract_only({"锦绣": {"普工": {"night": [19, 3]}}})
        self.assertIsNone(t["锦绣"]["普工"]["day"])
        p = ta.payable({"agency": "锦绣", "kind": "", "白班": 10, "夜班": 0, "总工时": 10}, t)
        self.assertIsNone(p["应付合计"])
        self.assertIn("没有白班价", p["合同缺档"])

    def test_legacy_saved_shape_is_migrated(self):
        """V2.344 之前「按月保存」的旧表是 {"agencies":…, "kinds":…}。只读回退仍要认得它。"""
        legacy = {"agencies": {"华顺": {"day": [16, 3], "night": [19, 3]}},
                  "kinds": {"保洁": {"day": [15, 0], "night": None}}}
        table = ta.contract_only(legacy)
        self.assertEqual(table["华顺"][ta.POST_DEFAULT]["day"], (16, 3))
        self.assertEqual(table["锦绣"]["保洁"]["day"], (15, 0))        # 旧的全局保洁归到锦绣


class TestNightPricing(unittest.TestCase):
    def test_night_worker_is_priced_at_night_rate(self):
        """夜班的人必须按夜班单价（22）算钱，不能拿白班的 19 顶——7 月没夜班掩盖了这个错，6 月小料一上就露。"""
        # 4 日 19:45 上班 → 5 日 08:33 下班 ＝ 12.8h，扣 0.5 → 12.0；人力只报 11 → 少记 1.0
        sm = ta.parse_summary(_summary_book([("甲", "小料", "华顺", "", [0, 0, 0, 11, 0, 0, 0], 0, 11)]))
        pk = ta.parse_punch(_punch_book(
            [("甲", "组", "临时普工-华顺人力", ["", "", "", "19:45\n23:22", "08:33", "", ""])],
            day_labels=[str(i) for i in range(1, 8)]))
        res = ta.compute(sm, pk, contract=ta.RATE_TABLE_OBSERVED)
        row = [r for r in res["rows"] if r["日"] == 4][0]
        self.assertEqual(row["班型"], "夜班")
        self.assertEqual(row["重算工时"], 12.0)
        self.assertEqual(row["单价"], 22.0)                 # 19 员工 + 3 管理费
        self.assertEqual(row["金额影响"], 22.0)             # 少记 1 小时 × 22
        self.assertEqual(res["people"][0]["含管理费单价"], 22.0)


class TestSplitByLine(unittest.TestCase):
    """同名同派遣方多行，先问一句「钱会不会被算两次」——日期不重叠就不算风险。

    2026-06 全量实证：116 组同名同派遣方多行，全部是「小料＋植物肉」且日期不重叠。
    工资按业务线分摊到车间，同一个人当月在两个车间干过就必须拆行，是正常成本归集。
    7 月那张拆分表只有植物肉一个车间，所以这条从没触发过——全量数据才照出来。"""

    def _sm(self, rows):
        return ta.parse_summary(_summary_book(rows))

    def test_split_across_lines_is_not_a_risk(self):
        sm = self._sm([("甲", "小料", "锦绣", "", [8, 8, 0, 0, 0, 0, 0], 16, 0),
                       ("甲", "植物肉", "锦绣", "", [0, 0, 8, 8, 0, 0, 0], 16, 0)])
        d = ta.cross_agency(sm)[0]
        self.assertTrue(d["按业务线拆行"])
        self.assertFalse(d["高风险"])
        self.assertEqual(d["重叠日"], [])
        self.assertIn("正常成本归集", d["风险"])

    def test_same_day_on_two_rows_is_high_risk(self):
        """同一天两行都记了工时——这才是钱可能被算两次。"""
        sm = self._sm([("甲", "小料", "锦绣", "", [8, 8, 0, 0, 0, 0, 0], 16, 0),
                       ("甲", "植物肉", "锦绣", "", [0, 8, 8, 0, 0, 0, 0], 16, 0)])
        d = ta.cross_agency(sm)[0]
        self.assertFalse(d["按业务线拆行"])
        self.assertTrue(d["高风险"])
        self.assertEqual(d["重叠日"], [2])
        self.assertIn("算了两次", d["风险"])

    def test_same_dept_two_rows_still_asked(self):
        """部门也相同却拆两行——没有正当理由，仍要问一句。"""
        sm = self._sm([("甲", "小料", "锦绣", "", [8, 0, 0, 0, 0, 0, 0], 8, 0),
                       ("甲", "小料", "锦绣", "", [0, 8, 0, 0, 0, 0, 0], 8, 0)])
        d = ta.cross_agency(sm)[0]
        self.assertFalse(d["按业务线拆行"])
        self.assertTrue(d["高风险"])

    def test_overlap_is_judged_within_the_same_raw_name_only(self):
        """⚠ 公司用后缀区分同名者（黄亚军0415 / 黄亚军7327）。重叠日只能在**同一个原名**内比——
        跨原名比等于拿甲的上班日去撞乙的上班日（2026-06 实测误报过一次）。"""
        sm = self._sm([("黄亚军0415", "小料", "锦绣", "", [8, 8, 0, 0, 0, 0, 0], 16, 0),
                       ("黄亚军0415", "植物肉", "锦绣", "", [0, 0, 0, 8, 0, 0, 0], 8, 0),
                       ("黄亚军7327", "植物肉", "锦绣", "", [8, 8, 0, 0, 0, 0, 0], 16, 0)])
        d = ta.cross_agency(sm)[0]
        self.assertEqual(d["重叠日"], [], "0415 与 7327 是两个人，他们同一天上班不是重叠")
        self.assertFalse(d["高风险"])
        self.assertTrue(d["按业务线拆行"])

    def test_overlap_inside_one_raw_name_is_still_high_risk(self):
        """同一个原名、同一天两行都记了工时——这才是真的可能算两次。"""
        sm = self._sm([("黄亚军0415", "小料", "锦绣", "", [8, 8, 0, 0, 0, 0, 0], 16, 0),
                       ("黄亚军0415", "植物肉", "锦绣", "", [0, 8, 8, 0, 0, 0, 0], 16, 0)])
        d = ta.cross_agency(sm)[0]
        self.assertEqual(d["重叠日"], [2])
        self.assertTrue(d["高风险"])

    def test_cross_agency_still_flagged(self):
        """跨派遣方仍然报——那是两家同时计费，跟按车间拆行是两回事。"""
        sm = self._sm([("甲", "小料", "锦绣", "", [8, 0, 0, 0, 0, 0, 0], 8, 0),
                       ("甲", "植物肉", "广才", "", [0, 8, 0, 0, 0, 0, 0], 8, 0)])
        d = ta.cross_agency(sm)[0]
        self.assertTrue(d["跨派遣方"])
        self.assertFalse(d["按业务线拆行"])
        self.assertTrue(d["高风险"])


class TestReportBasis(unittest.TestCase):
    """上报工时的口径。2026-06 全量 448 人实证：打卡跨度 12.5h 与 13.0h 的日子上报**同为 11.0h**
    （夜班 452/542 天、白班 590/900 天都报 11.0＝标准班扣 1 小时休息）——
    上报的是**排班班次时长**，不是从打卡算出来的。所以判定只该问「打卡撑不撑得起这个班」。"""

    def _one(self, rep_h, punches, basis="shift"):
        sm = ta.parse_summary(_summary_book([("甲", "植物肉", "锦绣", "", [rep_h] + [0] * 6, rep_h, 0)]))
        pk = ta.parse_punch(_punch_book([("甲", "组", "临时普工-锦绣人力", [punches] + [""] * 6)]))
        r = ta.compute(sm, pk, {"report_basis": basis}, contract=ta.RATE_TABLE_OBSERVED)
        return r["rows"][0] if r["rows"] else None

    def test_shift_basis_backs_reported_hours(self):
        """在厂 12h、上报 11h：班次制下**撑得住**，不是「少记 1 小时」。"""
        row = self._one(11, "07:00\n20:00")          # 跨度 13h，扣午休 1h → 在厂 12h
        self.assertEqual(row["档"], "ok")
        self.assertIn("撑得住", row["判定"])
        self.assertEqual(row["差异"], 1.0)            # 差额仍如实留着，供参考

    def test_shift_basis_flags_when_punches_cannot_back_it(self):
        """在厂 3h、上报 11h：撑不起——这才是唯一必须查的一档。"""
        row = self._one(11, "19:00\n23:00")
        self.assertEqual(row["档"], "over_out")
        self.assertIn("撑不起上报", row["判定"])

    def test_punch_basis_still_reproduces_old_verdict(self):
        """切回 punch 口径，同一条数据要判回「少记 1 小时」。"""
        row = self._one(11, "07:00\n20:00", basis="punch")
        self.assertEqual(row["档"], "under")
        self.assertIn("少记", row["判定"])

    def test_punched_but_unbilled_is_neutral_not_pending(self):
        """有打卡、当天没算工时：打卡表是全厂门禁数据，这多半是这人在别的名目下上班。
        归中性档「未计工时」，**不计入待查**——6 月全量实测 1,169 条，标红会把 17 条真问题全淹了。"""
        sm = ta.parse_summary(_summary_book([("甲", "植物肉", "锦绣", "", [0, 8] + [0] * 5, 8, 0)]))
        pk = ta.parse_punch(_punch_book([("甲", "组", "临时普工-锦绣人力", ["07:00\n20:00", "07:00\n20:00"] + [""] * 5)]))
        r = ta.compute(sm, pk, contract=ta.RATE_TABLE_OBSERVED)
        d1 = next(x for x in r["rows"] if x["日"] == 1)
        self.assertEqual(d1["档"], "unbilled")
        self.assertEqual(r["stats"]["待查日次"], 0)
        self.assertEqual(r["stats"]["未计工时日次"], 1)

    def test_reported_without_any_punch_stays_pending(self):
        """报了工时、却一次卡都没有——这个仍然要红，它是「拿了钱没证据」。"""
        sm = ta.parse_summary(_summary_book([("甲", "植物肉", "锦绣", "", [8] + [0] * 6, 8, 0)]))
        pk = ta.parse_punch(_punch_book([("甲", "组", "临时普工-锦绣人力", [""] * 7)]))
        r = ta.compute(sm, pk, contract=ta.RATE_TABLE_OBSERVED)
        self.assertEqual(r["rows"][0]["档"], "hard")
        self.assertEqual(r["stats"]["待查日次"], 1)


class TestAggregateTolerance(unittest.TestCase):
    """与成本会计底表一致：异常按**人整期净多记**判（白/夜各自，弹性 0.5），逐日只作明细。
    整期没超 → 逐日那几天的「超弹性异常」降级为中性「已消化」，逐人不报异常；整期超 → 逐日保留供定位。"""

    def _person(self, days):
        reps = [d[0] for d in days] + [0] * (7 - len(days))
        puns = [d[1] for d in days] + [""] * (7 - len(days))
        sm = ta.parse_summary(_summary_book([("甲", "植物肉", "锦绣", "", reps, round(sum(reps), 1), 0)]))
        pk = ta.parse_punch(_punch_book([("甲", "组", "临时普工-锦绣人力", puns)]))
        return ta.compute(sm, pk, contract=ta.RATE_TABLE_OBSERVED)

    def test_within_tolerance_downgrades_the_day(self):
        # 1日 上报11/在厂10 → 逐日多记1(超弹性)；2日 上报10/在厂11 → 撑得住。整期净多记 = 1−1 = 0 ≤ 0.5
        r = self._person([(11, "07:00\n18:00"), (10, "07:00\n19:00")])
        d1 = next(x for x in r["rows"] if x["日"] == 1)
        self.assertEqual(d1["档"], "over_absorbed")            # 逐日降级
        self.assertIn("整期已消化", d1["判定"])
        p = r["people"][0]
        self.assertFalse(p["超弹性"])                           # 逐人不报异常
        self.assertEqual(p["异常多记小时"], 0.0)
        self.assertEqual(r["stats"]["超弹性人数"], 0)
        self.assertEqual(r["stats"]["异常多记日次"], 0)
        self.assertEqual(r["stats"]["整期已消化多记日次"], 1)

    def test_exceeds_tolerance_stays_abnormal(self):
        # 1日 多记1；2日 上报8/在厂8 撑得住(净0)。整期净多记 = 1 > 0.5 → 真异常，逐日保留
        r = self._person([(11, "07:00\n18:00"), (8, "07:00\n16:00")])
        d1 = next(x for x in r["rows"] if x["日"] == 1)
        self.assertEqual(d1["档"], "over_out")
        p = r["people"][0]
        self.assertTrue(p["超弹性"])
        self.assertAlmostEqual(p["异常多记小时"], 1.0, places=1)
        self.assertEqual(r["stats"]["超弹性人数"], 1)


class TestMixedShift(unittest.TestCase):
    def test_mixed_is_flagged(self):
        """同月既有白班又有夜班的人，切班规则未定，必须显式标出来而不是硬算。"""
        sm = ta.parse_summary(_summary_book([("甲", "小料", "华顺", "", [8, 0, 0, 0, 0, 0, 0], 8, 11)]))
        pk = ta.parse_punch(_punch_book([("甲", "组", "临时普工-华顺人力", ["08:00\n17:30"] + [""] * 6)],
                                        day_labels=[str(i) for i in range(1, 8)]))
        res = ta.compute(sm, pk)
        self.assertEqual(res["stats"]["白夜混合人数"], 1)
        self.assertEqual(res["people"][0]["班型"], "白夜混合")

    def test_mixed_days_are_sliced_by_shift_window(self):
        """白夜混合逐日按**切班窗口**判白/夜，不再整档甩人工（V2.369）。

        场景就是当初出问题的那一套：4日 19:45 上、5日 08:33 下（一个夜班），
        5日 19:50 再上、6日 08:40 下（又一个夜班），另外 1 日是白班。
        · 早先按白班逻辑切 → 4 日「无下班卡」→ 判「多记 11 小时·异常」（假的）；
        · 中间一版整档标「切班规则未定，待人工」→ 6 月 102 人日全堆给人看；
        · 现在：首卡 ≥ night_start_from 就按夜班切（下班到次日 night_end_by 之前的末卡），
          4 日、5 日各自成一个夜班，都判「打卡撑得住上报」。
        切班规则是 2026-06 全量实证出来的，不是猜的。"""
        sm = ta.parse_summary(_summary_book([("甲", "小料", "华顺", "", [8, 0, 0, 11, 11, 0, 0], 8, 22)]))
        pk = ta.parse_punch(_punch_book(
            [("甲", "组", "临时普工-华顺人力", ["08:00\n17:30", "", "", "19:45", "08:33\n19:50", "08:40", ""])],
            day_labels=[str(i) for i in range(1, 8)]))
        res = ta.compute(sm, pk, contract=ta.RATE_TABLE_OBSERVED)
        self.assertEqual(res["people"][0]["班型"], "白夜混合")        # 人还是混合的
        by = {r["日"]: r for r in res["rows"]}
        for d in (4, 5):                                            # 两个夜班都要切得出来
            self.assertEqual(by[d]["档"], "ok", by[d])
            self.assertGreaterEqual(by[d]["重算工时"], 11.0)         # 19:45→次日08:33 扣 0.5
        self.assertEqual(res["stats"]["白夜混合日次"], 0)            # 不再有「待人工」这一档
        self.assertEqual(res["stats"]["异常多记日次"], 0)            # 更不能冒出假的「多记 11 小时」
        self.assertEqual(res["stats"]["异常多记金额"], 0)
        ta.settle_verdict(res)
        self.assertEqual(res["settle"]["明细"][0]["结论"], "正常")

    def test_mixed_still_deferred_under_punch_basis(self):
        """切回 punch 口径时仍旧整档交人工——那个口径要精确到小时的少记/多记，
        混合日的重算值撑不起那种精度。"""
        sm = ta.parse_summary(_summary_book([("甲", "小料", "华顺", "", [8, 0, 0, 11, 11, 0, 0], 8, 22)]))
        pk = ta.parse_punch(_punch_book(
            [("甲", "组", "临时普工-华顺人力", ["08:00\n17:30", "", "", "19:45", "08:33\n19:50", "08:40", ""])],
            day_labels=[str(i) for i in range(1, 8)]))
        res = ta.compute(sm, pk, {"report_basis": "punch"}, contract=ta.RATE_TABLE_OBSERVED)
        self.assertEqual({r["档"] for r in res["rows"]}, {"mixed"})
        self.assertEqual(res["stats"]["异常多记日次"], 0)


# ---------------- 真表回归（样本不在就跳过）----------------
_SAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))), "03_Source_Materials", "20260810_临时工考勤", "样本_202607")


@unittest.skipUnless(os.path.isdir(_SAMPLE), "真表样本不在本机，跳过")
class TestRealSample(unittest.TestCase):
    """2026 年 7 月植物肉 40 人。基准来自《核对报告 v3.0》，口径变动会在这里立刻暴露。"""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(_SAMPLE, "2026年7月临时工考勤汇总表-拆分.xlsx"), "rb") as f:
            sm = ta.parse_summary(f.read())
        with open(os.path.join(_SAMPLE, "打卡时间_20260701-20260801.xlsx"), "rb") as f:
            pk = ta.parse_punch(f.read())
        cls.sm, cls.pk = sm, pk
        # 以观察表当合同价——这组测试钉的是「合同价＝实际计价时，付款数分毫不差」这个结论本身
        cls.res = ta.compute(sm, pk, contract=ta.RATE_TABLE_OBSERVED)

    def test_totals(self):
        s = self.res["stats"]
        self.assertEqual(s["人数"], 40)
        self.assertEqual(s["比对人日"], 299)
        self.assertEqual(s["上报总工时"], 2811.0)
        self.assertEqual(s["重算总工时"], 2987.0)
        self.assertEqual(s["差异小时"], 176.0)
        self.assertEqual(s["差额金额"], 3344.0)

    def test_bands(self):
        """默认口径 shift（上报＝排班班次时长）：只问「打卡撑不撑得起上报」。
        原来判「少记」的 207 天，在这个口径下都是「撑得住」——上报是班次时长，本就小于在厂时长。"""
        s = self.res["stats"]
        self.assertEqual(s["上报口径"], "shift")
        self.assertEqual(s["一致日次"], 298)                 # 91 一致 + 207 原「少记」
        self.assertEqual(s["打卡多于上报日次"], 207)         # 仍作为参考量留着
        self.assertEqual(s["少记日次"], 0)                   # shift 口径下不再有这一档
        self.assertEqual(s["异常多记日次"], 0)               # 7 月一条撑不起的都没有
        self.assertEqual(s["待查日次"], 0)                   # 苏兵那天没记工时 → 归「未计工时」
        self.assertEqual(s["未计工时日次"], 1)

    def test_bands_under_punch_basis(self):
        """切回 punch 口径应当复现历史数字——这条钉住「口径开关真的在起作用」。"""
        s = ta.compute(self.sm, self.pk, {"report_basis": "punch"},
                       contract=ta.RATE_TABLE_OBSERVED)["stats"]
        self.assertEqual(s["一致日次"], 91)
        self.assertEqual(s["少记日次"], 207)
        self.assertEqual(s["异常多记日次"], 0)
        self.assertEqual(s["弹性内多记日次"], 0)
        self.assertEqual(s["未计工时日次"], 1)               # 这一档两种口径下都成立

    def test_no_unmatched_and_rates_all_hit(self):
        self.assertEqual(self.res["stats"]["未匹配人数"], 0)
        self.assertFalse([p for p in self.res["people"] if p["含管理费单价"] == 0])

    def test_round_mode_alternative(self):
        alt = ta.compute(self.sm, self.pk, {"round_mode": "round"}, contract=ta.RATE_TABLE_OBSERVED)
        self.assertEqual(alt["stats"]["重算总工时"], 3052.0)

    def test_outsiders(self):
        o = ta.outsiders(self.sm, self.pk)
        self.assertEqual(len(o), 7)
        self.assertEqual(o[0]["姓名"], "洪杰")

    def test_settle_ties_to_the_actual_payment(self):
        """7 月实付 51,496 元。按合同单价重算是 51,501，差的 5 元是一笔罚款（表里记作 −5）。
        这条钉住的是「付款数没算错」这个结论本身——一旦哪次改动让它对不上，就是真出事了。"""
        t = self.res["settle"]["合计"]
        self.assertEqual(t["应付工资"], 45703.50)
        self.assertEqual(t["应付管理费"], 5797.50)
        self.assertEqual(t["应付合计"], 51501.00)
        self.assertEqual(t["表上合计"], 51496.00)
        self.assertEqual(t["补贴奖罚"], -5.0)
        self.assertEqual(t["应付偏差"], 0.0)          # 剔掉罚款后分毫不差

    def test_agency_subtotals_equal_the_actual_payment_requests(self):
        """7 月 OA 里五张请款单（2026-08-17 提交，收款方即五家派遣方）：

            湖北广才 674.50 ｜杭州华顺孝感分 7,752 ｜湖北恒祺 3,439
            湖北锦绣孝感分 38,880 ｜湖北成达 750.50   合计 51,496

        本工具的「派遣方小计 · 结算表合计」必须逐家等于它——付款是按派遣方一家一张单出的，
        全表合计对得上而分家对不上，等于没核。"""
        OA = {"广才": 674.50, "华顺": 7752.00, "恒祺": 3439.00, "锦绣": 38880.00, "成达": 750.50}
        got = {r["归属"]: r["表上合计"] for r in self.res["settle"]["派遣方小计"]}
        self.assertEqual(got, OA)
        self.assertEqual(round(sum(OA.values()), 2), self.res["settle"]["合计"]["表上合计"])

    def test_settle_carries_the_tables_own_wage_fee_split(self):
        """请款金额要能拆成工资 + 管理费，且用**结算表自己的**数，不是重算值——
        锦绣那家两者差 5 元（罚款），拿重算值去填请款拆分就会对不上。"""
        jx = next(r for r in self.res["settle"]["派遣方小计"] if r["归属"] == "锦绣")
        self.assertEqual(jx["表上工资"], 34705.00)          # 重算是 34,710
        self.assertEqual(jx["表上管理费"], 4175.00)
        self.assertEqual(round(jx["表上工资"] + jx["表上管理费"], 2), jx["表上合计"])

    def test_settle_splits_by_agency_and_post(self):
        det = {(r["归属"], r["岗位"]): r for r in self.res["settle"]["明细"]}
        self.assertEqual(det[("锦绣", "保洁")]["人数"], 2)
        self.assertEqual(det[("锦绣", "保洁")]["应付管理费"], 0.0)     # 保洁无管理费
        self.assertEqual(det[("华顺", "普工")]["应付合计"], 7752.0)

    def test_settlement_table_self_check_is_clean(self):
        """单价 40/40 与合同一致、金额＝工时×单价、内部勾稽平——7 月结算表自查零异常。"""
        self.assertEqual(self.res["stats"]["金额核对条数"], 0)
        self.assertEqual(self.res["stats"]["有表上金额"], 40)

    def test_without_contract_everything_is_pending_not_normal(self):
        """⚠ 真 bug 的回归：一行合同价都没登记时，6 家派遣方曾全部显示「正常」。
        现在：应付为空、结论全部「待核」、请款金额照常带出（那是人力的数，不依赖合同价）。"""
        res = ta.compute(self.sm, self.pk)
        st, se = res["stats"], res["settle"]
        self.assertEqual(st["缺合同价人数"], 40)
        self.assertIsNone(se["合计"]["应付合计"])
        self.assertEqual(se["合计"]["表上合计"], 51496.00)
        self.assertEqual(se["合计"]["缺合同价人数"], 40)
        # 偏离金额照算——用的是结算表上各人实际的单价，不依赖合同价
        self.assertEqual(st["差额金额"], 3344.0)
        self.assertEqual(st["金额核对条数"], 0)             # 金额×单价、勾稽两道仍在核；单价那道没基准就不报
        ta.settle_verdict(res)
        # 7 月有两条真实的「归属与打卡不符」（程爱莲/周文秀，落在锦绣·保洁那一格），所以那一格是异常；
        # 其余所有格子都该是「待核」——一个「正常」都不许出现
        self.assertEqual({r["结论"] for r in se["明细"]}, {"待核", "异常"})
        bj = next(r for r in se["明细"] if (r["归属"], r["岗位"]) == ("锦绣", "保洁"))
        self.assertEqual(bj["结论"], "异常")
        self.assertTrue(any("归属与打卡不符" in w for w in bj["异常原因"]))
        self.assertTrue(any("合同价缺档" in w for w in bj["异常原因"]))   # 缺档仍作为原因附在后面
        self.assertEqual(se["合计"]["结论"], "异常")
        self.assertEqual(se["合计"]["异常派遣方"], ["锦绣"])
        self.assertEqual(se["合计"]["待核派遣方"], ["华顺", "广才", "恒祺", "成达"])

    def test_partial_contract_marks_only_the_rest_pending(self):
        """只登记锦绣：锦绣那几格能核（单价对得上→正常），其余四家待核，合计也待核（不是完整的数）。"""
        res = ta.compute(self.sm, self.pk, contract={"锦绣": ta.RATE_TABLE_OBSERVED["锦绣"]})
        ta.settle_verdict(res)
        sub = {r["归属"]: r for r in res["settle"]["派遣方小计"]}
        self.assertEqual({sub[a]["结论"] for a in ("华顺", "广才", "成达", "恒祺")}, {"待核"})
        self.assertIsNone(res["settle"]["合计"]["应付合计"])
        jx = sub["锦绣"]
        self.assertEqual(jx["应付合计"], 38885.0)            # 锦绣按合同价算得出来：表上 38,880 + 罚 5
        self.assertEqual(jx["缺合同价人数"], 0)
        self.assertFalse(any("合同价缺档" in w for w in jx["异常原因"]))
        # 锦绣这家是「异常」而不是「待核」——因为它有两条真实的归属与打卡不符，跟合同价无关
        self.assertEqual(jx["结论"], "异常")
        self.assertTrue(all("归属与打卡不符" in w for w in jx["异常原因"]))


class TestPayable(unittest.TestCase):
    """应付薪资：基数是上报工时，工资与管理费分开算。"""

    def test_base_is_reported_hours_not_recomputed(self):
        """⚠ 定位所系：应付按**上报**工时算。拿打卡重算的工时去算应付，就是自说自话另立一套账。"""
        sm = ta.parse_summary(_summary_book([("甲", "植物肉", "锦绣", "", [10] * 7, 70, 0)]))
        pk = ta.parse_punch(_punch_book([("甲", "组", "临时普工-锦绣人力", ["07:00\n19:00"] * 7)]))
        res = ta.compute(sm, pk, contract=ta.RATE_TABLE_OBSERVED)
        row = res["people"][0]
        self.assertGreater(row["重算总工时"], row["上报总工时"])       # 打卡口径算出来更多
        self.assertEqual(row["应付合计"], round(70 * 19.0, 2))        # 应付仍按上报的 70 小时

    def test_wage_and_fee_are_separate(self):
        """19 元/时里 16.5 是工人工资、2.5 是给派遣方的管理费，合同里就是两条，不能并成一个数。"""
        p = ta.payable({"agency": "锦绣", "kind": "", "白班": 100, "夜班": 0, "总工时": 100}, ta.RATE_TABLE_OBSERVED)
        self.assertEqual((p["应付工资"], p["应付管理费"], p["应付合计"]), (1650.0, 250.0, 1900.0))

    def test_mixed_shift_uses_each_shift_own_rate(self):
        """白夜混合的人：白班 16.5+2.5、夜班 19+3，各乘各的。整体套白班价会算少钱。"""
        p = ta.payable({"agency": "锦绣", "kind": "", "白班": 10, "夜班": 10, "总工时": 20}, ta.RATE_TABLE_OBSERVED)
        self.assertEqual(p["应付工资"], 10 * 16.5 + 10 * 19.0)
        self.assertEqual(p["应付管理费"], 10 * 2.5 + 10 * 3.0)

    def test_post_price_is_not_the_default_one(self):
        p = ta.payable({"agency": "锦绣", "kind": "保洁", "白班": 100, "夜班": 0, "总工时": 100}, ta.RATE_TABLE_OBSERVED)
        self.assertEqual((p["应付工资"], p["应付管理费"]), (1500.0, 0.0))

    def test_missing_contract_gives_none_not_a_guess(self):
        """合同价没登记：应付是 None 并写明原因，不是 0、更不是拿别的价算出来的数。"""
        p = ta.payable({"agency": "新家", "kind": "", "白班": 100, "夜班": 0, "总工时": 100}, {})
        self.assertIsNone(p["应付合计"])
        self.assertIsNone(p["应付偏差"])
        self.assertIn("新家", p["合同缺档"])
        # 只缺夜班价、而这个人根本没上夜班 → 不算缺
        p = ta.payable({"agency": "锦绣", "kind": "保洁", "白班": 100, "夜班": 0, "总工时": 100}, ta.RATE_TABLE_OBSERVED)
        self.assertIsNone(p["合同缺档"])
        # 上了夜班但保洁档没有夜班价 → 缺
        p = ta.payable({"agency": "锦绣", "kind": "保洁", "白班": 0, "夜班": 10, "总工时": 10}, ta.RATE_TABLE_OBSERVED)
        self.assertIn("没有夜班价", p["合同缺档"])


class TestPayCheck(unittest.TestCase):
    """对结算表自己填的金额做三道核对。"""

    OK = {"白单": 16.5, "白工资": 165.0, "夜单": 0, "夜工资": 0, "补贴": 0, "奖": 0, "罚": 0,
          "员工工资": 165.0, "白管单": 2.5, "白管理": 25.0, "夜管单": 0, "夜管理": 0, "合计": 190.0}

    def _sm(self, pay, dh=10):
        return ta.parse_summary(_summary_with_pay(
            [("甲", "植物肉", "锦绣", "", [dh] + [0] * 6, dh, 0, pay)]))

    def test_clean_table_reports_nothing(self):
        self.assertEqual(ta.pay_check(self._sm(self.OK)["people"], ta.RATE_TABLE_OBSERVED), [])

    def test_catches_the_cost_accountant_rate_error(self):
        """成本会计底表把单价写成 17+4=21，而合同是 16.5+2.5=19。这一条就是为抓它写的。"""
        bad = dict(self.OK, 白单=17.0, 白工资=170.0, 白管单=4.0, 白管理=40.0, 员工工资=170.0, 合计=210.0)
        hits = ta.pay_check(self._sm(bad)["people"], ta.RATE_TABLE_OBSERVED)
        self.assertEqual({h["项目"] for h in hits if h["类型"] == "单价"},
                         {"白班工资单价", "白班管理费单价"})
        self.assertFalse([h for h in hits if h["类型"] in ("金额", "勾稽")])   # 它自己内部是自洽的

    def test_zero_fee_in_contract_still_gets_compared(self):
        """保洁合同管理费登记为 0：人力若多收了 2 元管理费，必须报出来——「登记为 0」也是登记，不是缺档。"""
        con = {"锦绣": {"普工": {"day": (15.0, 0.0)}}}
        bad = dict(self.OK, 白单=15.0, 白工资=150.0, 白管单=2.0, 白管理=20.0, 员工工资=150.0, 合计=170.0)
        hits = ta.pay_check(self._sm(bad)["people"], con)
        self.assertIn("白班管理费单价", [h["项目"] for h in hits if h["类型"] == "单价"])

    def test_rate_gaps_marks_the_person_not_just_the_cell(self):
        """②总览只说「N 格不符、涉及 M 人」，点进第④步逐人核对得能认出是哪几个人。
        rate_gaps 是「单价对不对」的唯一实现，pay_check 与逐人标记都走它。"""
        con = {"锦绣": {"普工": {"day": (10.0, 2.0), "night": None}}}
        bad = dict(self.OK)                                  # 表上 16.5+2.5，合同 10+2
        p = self._sm(bad)["people"][0]
        g = ta.rate_gaps(p, con)
        self.assertEqual([(x["项目"], x["表上"], x["合同"], x["差"]) for x in g],
                         [("白班工资单价", 16.5, 10.0, 6.5), ("白班管理费单价", 2.5, 2.0, 0.5)])
        # 与 pay_check 的第①道同源：条数、项目名都得对得上
        self.assertEqual([h["项目"] for h in ta.pay_check([p], con) if h["类型"] == "单价"],
                         [x["项目"] for x in g])

    def test_rate_gaps_skips_shifts_not_worked_and_undeclared_bands(self):
        """没上过的班不谈他的价；合同没登记的档是「缺档」不是「不符」，两者不能混。"""
        p = self._sm(dict(self.OK))["people"][0]             # 只上白班
        # 夜班价登记得再离谱，他没上夜班就不该报
        self.assertEqual(ta.rate_gaps(p, {"锦绣": {"普工": {"day": (16.5, 2.5), "night": (99.0, 99.0)}}}), [])
        # 白班档没登记 → 跳过（缺档另有判定），不报「不符」
        self.assertEqual(ta.rate_gaps(p, {"锦绣": {"普工": {"night": (19.0, 3.0)}}}), [])
        self.assertEqual(ta.rate_gaps(p, {}), [])
        # 登记为 0 的管理费也要比（保洁那种）
        g = ta.rate_gaps(p, {"锦绣": {"普工": {"day": (16.5, 0.0)}}})
        self.assertEqual([x["项目"] for x in g], ["白班管理费单价"])

    def test_compute_hangs_rate_gaps_on_each_person(self):
        """人数要与②总览「不符」那几格的人数对得上——两边说的是同一批人。"""
        sm = ta.parse_summary(_summary_with_pay(
            [("甲", "植物肉", "锦绣", "", [10] + [0] * 6, 10, 0, self.OK),
             ("乙", "植物肉", "华顺", "", [10] + [0] * 6, 10, 0, self.OK)]))
        pk = ta.parse_punch(_punch_book([("甲", "组", "临时普工-锦绣人力", ["07:00\n19:00"] + [""] * 6),
                                         ("乙", "组", "临时普工-华顺人力", ["07:00\n19:00"] + [""] * 6)]))
        res = ta.compute(sm, pk, contract={"锦绣": {"普工": {"day": (10.0, 2.0)}},
                                           "华顺": {"普工": {"day": (16.5, 2.5)}}})
        by = {x["姓名"]: x for x in res["people"]}
        self.assertTrue(by["甲"]["单价不符"])            # 锦绣合同 10+2、表上 16.5+2.5
        self.assertFalse(by["乙"]["单价不符"])           # 华顺对得上
        self.assertEqual(res["stats"]["单价不符人数"], 1)
        cell = next(x for x in res["stats"]["单价核对"]["明细"] if x["状态"] == "⚠不符")
        self.assertEqual(cell["人数"], res["stats"]["单价不符人数"])

    def test_rate_check_needs_a_contract_but_arithmetic_does_not(self):
        """没登记合同价：单价那一道没基准就不报（缺档另由单价核对按格报），
        但「金额＝工时×表上单价」「勾稽」两道不靠合同价，照常报。"""
        bad = dict(self.OK, 白单=17.0, 白工资=170.0, 白管单=4.0, 白管理=40.0, 员工工资=170.0, 合计=210.0)
        self.assertEqual(ta.pay_check(self._sm(bad)["people"], {}), [])
        bad2 = dict(self.OK, 白工资=999.0, 员工工资=999.0, 合计=1024.0)
        hits = ta.pay_check(self._sm(bad2)["people"], {})
        self.assertIn("白班工资", [h["项目"] for h in hits if h["类型"] == "金额"])

    def test_catches_amount_not_equal_hours_times_rate(self):
        bad = dict(self.OK, 白工资=999.0, 员工工资=999.0, 合计=1024.0)
        hits = ta.pay_check(self._sm(bad)["people"], ta.RATE_TABLE_OBSERVED)
        self.assertIn("白班工资", [h["项目"] for h in hits if h["类型"] == "金额"])

    def test_penalty_is_already_negative_and_must_be_added(self):
        """「罚」在表里就是负数。再减一次会让每一个有罚款的人都被误报勾稽不平
        （7 月实测：全表只有一笔 −5 元罚款，减法版本会把它报成异常）。"""
        pay = dict(self.OK, 罚=-5.0, 员工工资=160.0, 合计=185.0)
        self.assertEqual(ta.pay_check(self._sm(pay)["people"], ta.RATE_TABLE_OBSERVED), [])

    def test_skips_tables_without_money_columns(self):
        """按派遣方拆出来的简表没有金额列，整段跳过，不报假异常。"""
        sm = ta.parse_summary(_summary_book([("甲", "植物肉", "锦绣", "", [10] * 7, 70, 0)]))
        self.assertEqual(ta.pay_check(sm["people"], ta.RATE_TABLE_OBSERVED), [])


class TestAdjustCheck(unittest.TestCase):
    """奖 / 罚 / 蒸练补贴——全表唯一没有对照源的钱。验不了金额，只验符号、占比、有无出处。"""

    def _p(self, name, wage, **kw):
        t = {"员工工资": wage}
        t.update({k: v for k, v in kw.items()})
        return {"name": name, "agency": "锦绣", "dept": "小料", "kind": "", "表上": t}

    def test_penalty_recorded_as_positive_is_flagged(self):
        """8 月 李秀英 罚记成 +5：本该扣 5 变成多发 5，一来一回差 10 元。
        金额虽小，但说明这个符号没人管——换成 −500 写成 +500 也一样漏得过去。"""
        hit = ta.adjust_check([self._p("李秀英", 5000, **{"罚": 5})])
        self.assertEqual(hit[0]["级别"], "异常")
        self.assertIn("符号反了", hit[0]["说明"])
        self.assertIn("10", hit[0]["说明"])          # 影响是金额的两倍

    def test_bonus_with_zero_wage_is_flagged(self):
        """1 月 胡桂华：当月工资 0 却拿了 60 元奖金。"""
        hit = ta.adjust_check([self._p("胡桂华", 0, **{"奖": 60})])
        self.assertEqual(hit[0]["级别"], "异常")
        self.assertIsNone(hit[0]["占工资"])           # 分母为 0，不硬算一个比例出来

    def test_oversized_single_item_is_queried(self):
        """1 月 桂丽：奖 75 元、当月工资 135 元，占 55.6%。"""
        hit = ta.adjust_check([self._p("桂丽", 135, **{"奖": 75})])
        self.assertEqual(hit[0]["级别"], "存疑")
        self.assertEqual(hit[0]["占工资"], 55.6)

    def test_ratio_cap_is_configurable(self):
        p = [self._p("某人", 1000, **{"奖": 250})]
        self.assertEqual(ta.adjust_check(p, 0.2)[0]["级别"], "存疑")
        self.assertEqual(ta.adjust_check(p, 0.5)[0]["级别"], "提示")

    def test_normal_items_are_listed_not_judged(self):
        """正常的奖罚也要逐笔列出来——工具验不了金额，列出来才能让人去要审批单。"""
        hit = ta.adjust_check([self._p("正常人", 5000, **{"奖": 10, "罚": -5})])
        self.assertEqual([h["级别"] for h in hit], ["提示", "提示"])
        self.assertIn("需附审批依据", hit[0]["说明"])

    def test_zero_items_are_not_listed(self):
        self.assertEqual(ta.adjust_check([self._p("干净", 5000, **{"奖": 0, "罚": 0})]), [])

    def test_abnormal_sorts_before_normal(self):
        hit = ta.adjust_check([self._p("甲", 5000, **{"奖": 10}), self._p("乙", 5000, **{"罚": 5})])
        self.assertEqual([h["级别"] for h in hit], ["异常", "提示"])

    def test_total_splits_by_item(self):
        rows = ta.adjust_check([self._p("甲", 5000, **{"奖": 10, "罚": -5}),
                                self._p("乙", 5000, **{"补贴": 20})])
        self.assertEqual(ta.adjust_total(rows),
                         {"蒸练补贴": 20.0, "奖": 10.0, "罚": -5.0, "净额": 25.0,
                          "笔数": 3, "异常": 0, "存疑": 0})


class TestSettle(unittest.TestCase):
    def test_groups_by_agency_line_and_post(self):
        """岗位不能并：锦绣的保洁 15 元和普工 19 元并一格，单价就看不出来了。"""
        people = [
            {"归属": "锦绣", "部门": "植物肉", "岗位": "普工", "上报总工时": 100,
             "上报白班工时": 100, "上报夜班工时": 0, "应付工资": 1650, "应付管理费": 250,
             "应付合计": 1900, "补贴奖罚": 0, "表上合计": 1900, "应付偏差": 0},
            {"归属": "锦绣", "部门": "植物肉", "岗位": "保洁", "上报总工时": 100,
             "上报白班工时": 100, "上报夜班工时": 0, "应付工资": 1500, "应付管理费": 0,
             "应付合计": 1500, "补贴奖罚": 0, "表上合计": 1500, "应付偏差": 0},
            {"归属": "华顺", "部门": "小料", "岗位": "普工", "上报总工时": 10,
             "上报白班工时": 10, "上报夜班工时": 0, "应付工资": 165, "应付管理费": 25,
             "应付合计": 190, "补贴奖罚": 0, "表上合计": 190, "应付偏差": 0},
        ]
        se = ta.settle(people)
        self.assertEqual(len(se["明细"]), 3)                      # 保洁与普工各占一行
        self.assertEqual(se["合计"]["应付合计"], 3590)
        self.assertEqual({r["归属"]: r["应付合计"] for r in se["派遣方小计"]},
                         {"锦绣": 3400, "华顺": 190})
        self.assertEqual({r["部门"]: r["人数"] for r in se["业务线小计"]}, {"植物肉": 2, "小料": 1})

    def test_no_money_columns_leaves_rollup_table_amounts_none(self):
        """简表没有金额列：小计/合计的「表上合计」也得是 None——路由和看板用 is None 判「有没有请款金额」，
        给 0.0 会被当成"请款 0 元"。"""
        people = [{"归属": "锦绣", "部门": "植物肉", "岗位": "普工", "上报总工时": 10,
                   "上报白班工时": 10, "上报夜班工时": 0, "应付工资": 165, "应付管理费": 25,
                   "应付合计": 190, "补贴奖罚": None, "表上合计": None, "应付偏差": None}]
        se = ta.settle(people)
        self.assertIsNone(se["明细"][0]["表上合计"])
        self.assertIsNone(se["派遣方小计"][0]["表上合计"])
        self.assertIsNone(se["合计"]["表上合计"])
        self.assertEqual(se["合计"]["应付合计"], 190)

    def test_missing_contract_blanks_the_cell_and_every_rollup_it_touches(self):
        """一格里有人缺合同价：这一格、它所在的派遣方小计、全表合计的「按合同价应付」都留空——
        不拿少算了一家的部分和冒充合计。别家的格子不受影响。"""
        people = [
            {"归属": "锦绣", "部门": "植物肉", "岗位": "普工", "上报总工时": 100,
             "上报白班工时": 100, "上报夜班工时": 0, "应付工资": 1650, "应付管理费": 250,
             "应付合计": 1900, "补贴奖罚": 0, "表上合计": 1900, "应付偏差": 0},
            {"归属": "新家", "部门": "植物肉", "岗位": "普工", "上报总工时": 10,
             "上报白班工时": 10, "上报夜班工时": 0, "应付工资": None, "应付管理费": None,
             "应付合计": None, "合同缺档": "合同价登记表里没有「新家」覆盖本期的行",
             "补贴奖罚": 0, "表上合计": 190, "应付偏差": None},
        ]
        se = ta.settle(people)
        by = {r["归属"]: r for r in se["明细"]}
        self.assertEqual(by["锦绣"]["应付合计"], 1900)
        self.assertIsNone(by["新家"]["应付合计"])
        self.assertEqual(by["新家"]["缺合同价人数"], 1)
        self.assertEqual(by["新家"]["表上合计"], 190)          # 请款金额照常带出
        sub = {r["归属"]: r for r in se["派遣方小计"]}
        self.assertEqual(sub["锦绣"]["应付合计"], 1900)
        self.assertIsNone(sub["新家"]["应付合计"])
        self.assertIsNone(se["合计"]["应付合计"])
        self.assertEqual(se["合计"]["表上合计"], 2090)
        self.assertEqual(se["合计"]["缺合同价人数"], 1)


class TestContractVsActual(unittest.TestCase):
    """合同价 vs 人力实际计价。合同价＝成本会计维护的那张表，
    **不是**汇总表表头解析出来的那张——表头是人力自己写的，拿它当基准就是人力跟自己比。"""

    CON = {"锦绣": {"普工": {"day": (16.5, 2.5), "night": (19.0, 3.0)},
                    "保洁": {"day": (15.0, 0.0), "night": None}}}

    def _p(self, agency, post, dh=100, nh=0, dw=16.5, dm=2.5, nw=19.0, nm=3.0):
        return {"name": "某人", "agency": agency, "kind": post, "白班": dh, "夜班": nh,
                "表上": {"白班工资单价": dw, "白班管理费单价": dm,
                         "夜班工资单价": nw, "夜班管理费单价": nm}}

    def _row(self, r, agency, post, shift):
        return next(x for x in r["明细"]
                    if (x["派遣方"], x["岗位"], x["班次"]) == (agency, post, shift))

    def test_match_is_green(self):
        r = ta.contract_vs_actual([self._p("锦绣", "")], self.CON)
        self.assertTrue(r["全对"])
        self.assertEqual(self._row(r, "锦绣", "普工", "白班")["状态"], "一致")

    def test_mismatch_is_flagged_with_delta(self):
        r = ta.contract_vs_actual([self._p("锦绣", "", dw=17.0, dm=2.0)], self.CON)
        self.assertFalse(r["全对"])
        x = self._row(r, "锦绣", "普工", "白班")
        self.assertEqual(x["状态"], "⚠不符")
        # 合计都是 19，但工资与管理费各差 0.5——合计相等不代表没事
        self.assertEqual((x["差额"]["员工工资"], x["差额"]["管理费"], x["差额"]["合计"]),
                         (0.5, -0.5, 0.0))

    def test_shift_without_hours_is_not_compared(self):
        """这个人没排夜班，就谈不上「他用了什么夜班价」——不该拿他的空单价去比。"""
        r = ta.contract_vs_actual([self._p("锦绣", "", dh=100, nh=0)], self.CON)
        self.assertEqual(self._row(r, "锦绣", "普工", "夜班")["状态"], "本期无人")

    def test_agency_missing_from_contract(self):
        r = ta.contract_vs_actual([self._p("新家", "", dw=16.0, dm=3.0)], self.CON)
        x = self._row(r, "新家", "普工", "白班")
        self.assertEqual(x["状态"], "合同缺档")
        self.assertIn("依据不明", x["说明"])

    def test_two_prices_in_one_cell_is_its_own_verdict(self):
        """同一格里人力用了两种价——本身就是问题，但**不是「与合同价不符」**：
        它可能每种都对得上合同（期中调价），也可能都对不上。混进「不符」会让②按格报的人数
        把整格的人算进去，而第④步逐人一个也标不出来（V2.349 审出）。"""
        r = ta.contract_vs_actual([self._p("锦绣", ""), self._p("锦绣", "", dw=17.0)], self.CON)
        x = self._row(r, "锦绣", "普工", "白班")
        self.assertEqual(x["状态"], "⚠同格多价")
        self.assertIn("2 种单价", x["说明"])
        self.assertEqual(r["同格多价"], 1)
        self.assertEqual(r["不符"], 0)          # 不再混进「不符」
        self.assertFalse(r["全对"])             # 但仍然算「没全对」，不能放过

    def test_one_tolerance_for_cell_and_person(self):
        """逐格与逐人必须同一把尺：差 0.008 时若两处容差不同，会出现「②说不符、④标不出人」。"""
        self.assertEqual(ta.RATE_TOL, 0.005)
        p = self._p("锦绣", "", dw=16.5 + 0.008)
        r = ta.contract_vs_actual([p], self.CON)
        self.assertEqual(self._row(r, "锦绣", "普工", "白班")["状态"], "⚠不符")
        self.assertTrue(ta.rate_gaps(p, self.CON))          # 逐人也要认出来

    def test_missing_price_columns_are_not_treated_as_zero(self):
        """结算表有金额列、却没解析出单价列时，不能把「没这一列」当成「单价 0」去跟合同比——
        那会把全表误报成单价不符（_pay_cols 允许部分命中，不是每个月的表都长一样）。"""
        p = {"name": "甲", "agency": "锦绣", "kind": "", "白班": 100, "夜班": 0,
             "表上": {"员工工资": 1650.0, "合计": 1900.0}}      # 有金额、无单价列
        self.assertEqual(ta.rate_gaps(p, self.CON), [])

    def test_gap_amount_is_attributed_to_price_only(self):
        """单价算错造成的钱＝逐项差价 × 该班工时，不掺补贴奖罚等别的原因。"""
        p = self._p("锦绣", "", dh=100, dw=17.0, dm=2.0)        # 合同 16.5+2.5
        p["单价不符"] = ta.rate_gaps(p, self.CON)
        self.assertEqual([g["金额"] for g in p["单价不符"]], [50.0, -50.0])
        self.assertEqual(ta.rate_gap_amount(p), 0.0)            # 工资多 0.5、管理费少 0.5，净额为 0

    def test_post_is_scoped(self):
        r = ta.contract_vs_actual([self._p("锦绣", "保洁", dw=15.0, dm=0.0)], self.CON)
        self.assertEqual(self._row(r, "锦绣", "保洁", "白班")["状态"], "一致")

    def test_no_money_columns(self):
        r = ta.contract_vs_actual([{"name": "甲", "agency": "锦绣", "kind": "", "白班": 100, "夜班": 0}], self.CON)
        self.assertFalse(r["有人力数据"])
        self.assertTrue(r["全对"])          # 没得比就不算错

    def test_counts_people_and_hours(self):
        r = ta.contract_vs_actual([self._p("锦绣", "", dh=100), self._p("锦绣", "", dh=50)], self.CON)
        x = self._row(r, "锦绣", "普工", "白班")
        self.assertEqual((x["人数"], x["工时"]), (2, 150.0))


class TestSettleVerdict(unittest.TestCase):
    """复核结论表的「正常 / 异常」由工具给，不让人看着「偏差 ¥0」自己推断。
    而且结论不能只看偏差——单价不符、奖罚异常、超弹性多记、同名重复计费都要收进来。"""

    def _res(self, **kw):
        people = [{"姓名": "甲", "归属": "锦绣", "部门": "植物肉", "岗位": "普工"},
                  {"姓名": "乙", "归属": "华顺", "部门": "小料", "岗位": "普工"}]
        se = {"明细": [{"归属": "锦绣", "部门": "植物肉", "岗位": "普工", "应付偏差": 0.0},
                       {"归属": "华顺", "部门": "小料", "岗位": "普工", "应付偏差": 0.0}],
              "派遣方小计": [{"归属": "锦绣", "应付偏差": 0.0}, {"归属": "华顺", "应付偏差": 0.0}],
              "业务线小计": [{"部门": "植物肉", "应付偏差": 0.0}, {"部门": "小料", "应付偏差": 0.0}],
              "合计": {"应付偏差": 0.0}}
        st = {"金额核对": [], "合同外调整": [], "归属与打卡不符": [], "同名多行": []}
        st.update(kw.pop("stats", {}))
        return ta.settle_verdict({"people": people, "settle": se, "stats": st,
                                  "rows": kw.pop("rows", [])})

    def _cell(self, res, agency):
        return next(x for x in res["settle"]["明细"] if x["归属"] == agency)

    def test_clean_is_normal(self):
        r = self._res()
        self.assertEqual([x["结论"] for x in r["settle"]["明细"]], ["正常", "正常"])
        self.assertEqual(r["settle"]["合计"]["结论"], "正常")

    def test_deviation_alone_makes_it_abnormal(self):
        r = self._res()
        r["settle"]["明细"][0]["应付偏差"] = 5.0
        r = ta.settle_verdict(r)
        self.assertEqual(self._cell(r, "锦绣")["结论"], "异常")
        self.assertIn("结算与按合同价重算差", self._cell(r, "锦绣")["异常原因"][0])

    def test_zero_deviation_but_other_problems_still_abnormal(self):
        """⚠ 这是本函数存在的理由：偏差为 0 不等于这一格干净。"""
        r = self._res(rows=[{"姓名": "甲", "归属": "锦绣", "档": "over_out"}])
        self.assertEqual(self._cell(r, "锦绣")["结论"], "异常")
        self.assertEqual(self._cell(r, "锦绣")["异常原因"], ["超弹性多记 1 处"])
        self.assertEqual(self._cell(r, "华顺")["结论"], "正常")     # 不误伤别家

    def test_all_check_kinds_are_collected(self):
        r = self._res(
            stats={"金额核对": [{"姓名": "甲", "归属": "锦绣", "项目": "白班工资单价"}],
                   "合同外调整": [{"姓名": "甲", "归属": "锦绣", "级别": "异常", "项目": "罚"}],
                   "归属与打卡不符": [{"姓名": "甲", "结算归属": "锦绣"}],
                   "同名多行": [{"原名": ["甲"], "派遣方": ["锦绣"]}]},
            rows=[{"姓名": "甲", "归属": "锦绣", "档": "over_out"}])
        why = self._cell(r, "锦绣")["异常原因"]
        self.assertEqual(len(why), 5)
        for lab in ("单价/金额对不上", "奖罚异常", "归属与打卡不符", "同名重复计费存疑", "超弹性多记"):
            self.assertTrue(any(w.startswith(lab) for w in why), lab)

    def test_acked_items_do_not_make_it_abnormal(self):
        """已认定的不算异常——人已经看过并说明过了，再报一次就是噪音；
        但要留一句「另有 N 项已认定」，不让它彻底消失。"""
        r = self._res(rows=[{"姓名": "甲", "归属": "锦绣", "档": "over_out",
                             "已认定": {"理由": "漏打卡", "认定人": "管理员"}}])
        c = self._cell(r, "锦绣")
        self.assertEqual(c["结论"], "正常")
        self.assertEqual(c["已认定"], 1)

    def test_subtotal_and_total_roll_up(self):
        r = self._res(rows=[{"姓名": "甲", "归属": "锦绣", "档": "over_out"}])
        sub = {x["归属"]: x["结论"] for x in r["settle"]["派遣方小计"]}
        self.assertEqual(sub, {"锦绣": "异常", "华顺": "正常"})
        self.assertEqual(r["settle"]["合计"]["结论"], "异常")
        self.assertEqual(r["settle"]["合计"]["异常派遣方"], ["锦绣"])

    def test_empty_settle_is_noop(self):
        self.assertEqual(ta.settle_verdict({"settle": {}, "stats": {}})["settle"], {})

    def test_same_name_across_agencies_lands_on_the_right_cell(self):
        """review 实测：同名挂两家派遣方（正是结算风险盯的场景），只按姓名定位会把华顺那条多记
        记到锦绣头上——有问题那家判正常、没问题那家判异常。"""
        people = [{"姓名": "甲", "归属": "锦绣", "部门": "植物肉", "岗位": "普工"},
                  {"姓名": "甲", "归属": "华顺", "部门": "小料", "岗位": "普工"}]
        se = {"明细": [{"归属": "锦绣", "部门": "植物肉", "岗位": "普工", "应付偏差": 0.0},
                       {"归属": "华顺", "部门": "小料", "岗位": "普工", "应付偏差": 0.0}],
              "派遣方小计": [{"归属": "锦绣", "应付偏差": 0.0}, {"归属": "华顺", "应付偏差": 0.0}],
              "业务线小计": [], "合计": {"应付偏差": 0.0}}
        st = {"金额核对": [], "合同外调整": [], "归属与打卡不符": [], "同名多行": []}
        r = ta.settle_verdict({"people": people, "settle": se, "stats": st,
                               "rows": [{"姓名": "甲", "归属": "华顺", "档": "over_out"}]})
        by = {x["归属"]: x["结论"] for x in r["settle"]["明细"]}
        self.assertEqual(by, {"锦绣": "正常", "华顺": "异常"})

    def test_detail_deviation_rolls_up_even_if_subtotal_nets_to_zero(self):
        """两格一正一负抵成 0：小计自己的偏差是 0，但明细有差就不能说小计正常。"""
        r = self._res()
        r["settle"]["明细"][0]["应付偏差"] = 5.0
        r["settle"]["明细"][1]["应付偏差"] = -5.0
        r["settle"]["派遣方小计"][0]["应付偏差"] = 0.0
        r["settle"]["合计"]["应付偏差"] = 0.0
        r = ta.settle_verdict(r)
        self.assertEqual(r["settle"]["合计"]["结论"], "异常")
        self.assertTrue(any("明细格" in w for w in r["settle"]["合计"]["异常原因"]))

    def test_missing_contract_is_pending_not_normal(self):
        """⚠ 合同价缺档、其它都干净 → 「待核」。不是正常（没法核），也不是异常（没发现问题）。"""
        r = self._res()
        r["settle"]["明细"][1].update({"应付偏差": None, "缺合同价人数": 1})
        r["settle"]["派遣方小计"][1].update({"应付偏差": None, "缺合同价人数": 1})
        r["settle"]["合计"].update({"应付偏差": None, "缺合同价人数": 1})
        r = ta.settle_verdict(r)
        self.assertEqual(self._cell(r, "华顺")["结论"], "待核")
        self.assertIn("合同价缺档", self._cell(r, "华顺")["异常原因"][0])
        self.assertEqual(self._cell(r, "锦绣")["结论"], "正常")
        self.assertEqual(r["settle"]["合计"]["结论"], "待核")
        self.assertEqual(r["settle"]["合计"]["待核派遣方"], ["华顺"])
        self.assertEqual(r["settle"]["合计"]["异常派遣方"], [])

    def test_missing_contract_plus_a_real_problem_is_abnormal(self):
        """缺档不是免死金牌：同一格里还有超弹性多记，结论仍是异常，缺档只作为一条原因附在后面。"""
        r = self._res(rows=[{"姓名": "乙", "归属": "华顺", "档": "over_out"}])
        r["settle"]["明细"][1].update({"应付偏差": None, "缺合同价人数": 1})
        r = ta.settle_verdict(r)
        c = self._cell(r, "华顺")
        self.assertEqual(c["结论"], "异常")
        self.assertTrue(any("超弹性多记" in w for w in c["异常原因"]))
        self.assertTrue(any("合同价缺档" in w for w in c["异常原因"]))


class TestBoardFromPeriods(unittest.TestCase):
    """看板数据源＝历次复核留档，不是另外上传结构表。输出结构必须与 parse_structure 一致，
    否则前端那五张图就得跟着改——那正是这次要避免的。"""

    def _period(self, month, people, tot=None):
        return (month, {"people": people, "stats": {"人数": len(people), "比对人日": len(people) * 20},
                        "settle": {"合计": tot or {}}})

    def _p(self, agency, dept, dh, nh, dp=(16.5, 2.5), np_=(19.0, 3.0), amt=None, extra=0.0):
        return {"归属": agency, "部门": dept, "岗位": "普工",
                "上报白班工时": dh, "上报夜班工时": nh, "上报总工时": dh + nh,
                "白班工资单价": dp[0], "白班管理费单价": dp[1],
                "夜班工资单价": np_[0], "夜班管理费单价": np_[1],
                "表上合计": amt, "补贴奖罚": extra}

    def test_shape_matches_parse_structure(self):
        """键必须对齐——前端五张图直接吃这个结构。"""
        b = ta.board_from_periods([self._period("2026-07", [self._p("锦绣", "植物肉", 100, 0)])])
        for k in ("months", "depts", "monthly", "company", "kpi", "标准单价", "残缺月"):
            self.assertIn(k, b)
        for k in ("m", "白班金额", "夜班金额", "补贴金额", "合计金额",
                  "白班工时", "夜班工时", "总工时", "夜班工时占比", "有效单价", "部门"):
            self.assertIn(k, b["monthly"][0])
        for k in ("c", "月", "合计", "工时", "有效单价", "夜班占比", "活跃月数"):
            self.assertIn(k, b["company"][0])

    def test_day_night_split_uses_each_persons_own_rate(self):
        """白/夜金额按各人各自的单价拆——全表套一个单价会把保洁那种特殊岗位算错。"""
        b = ta.board_from_periods([self._period("2026-07", [
            self._p("锦绣", "植物肉", 100, 0),                       # 100 × 19 = 1900
            self._p("锦绣", "植物肉", 0, 100),                       # 100 × 22 = 2200
            self._p("锦绣", "植物肉", 100, 0, dp=(15.0, 0.0)),       # 保洁 100 × 15 = 1500
        ])])
        m = b["monthly"][0]
        self.assertEqual(m["白班金额"], 3400)
        self.assertEqual(m["夜班金额"], 2200)
        self.assertEqual(m["总工时"], 300.0)

    def test_amount_prefers_settlement_table(self):
        """钱取结算表自己的合计（＝请款额），与复核结论页主列一致；没有才回落重算值。"""
        b = ta.board_from_periods([self._period(
            "2026-07", [self._p("锦绣", "植物肉", 100, 0, amt=1895.0, extra=-5.0)],
            tot={"表上合计": 1895.0})])
        self.assertEqual(b["monthly"][0]["合计金额"], 1895)
        self.assertEqual(b["monthly"][0]["补贴金额"], -5)
        self.assertEqual(b["期次"][0]["金额来源"], "结算表")

    def test_falls_back_when_no_settlement_amount(self):
        b = ta.board_from_periods([self._period(
            "2026-07", [self._p("锦绣", "植物肉", 100, 0)], tot={"应付合计": 1900.0})])
        self.assertEqual(b["monthly"][0]["合计金额"], 1900)
        self.assertEqual(b["期次"][0]["金额来源"], "按合同价重算")

    def test_multi_period_accumulates_and_sorts(self):
        b = ta.board_from_periods([
            self._period("2026-07", [self._p("锦绣", "植物肉", 100, 0, amt=1900.0)], {"表上合计": 1900.0}),
            self._period("2026-06", [self._p("锦绣", "植物肉", 200, 0, amt=3800.0)], {"表上合计": 3800.0}),
        ])
        self.assertEqual(b["months"], ["2026-06", "2026-07"])       # 乱序传入也要按月排好
        self.assertEqual(b["company"][0]["月"], [3800, 1900])
        self.assertEqual(b["kpi"]["全年金额"], 5700)
        self.assertEqual(b["kpi"]["期数"], 2)

    def test_all_depts_filled_on_every_month(self):
        """某期没有的部门要补 0，否则堆叠图会错位。"""
        b = ta.board_from_periods([
            self._period("2026-06", [self._p("锦绣", "植物肉", 100, 0, amt=1900.0)], {"表上合计": 1900.0}),
            self._period("2026-07", [self._p("锦绣", "小料", 100, 0, amt=1900.0)], {"表上合计": 1900.0}),
        ])
        self.assertEqual(sorted(b["depts"]), ["小料", "植物肉"])
        for m in b["monthly"]:
            self.assertEqual(sorted(m["部门"]), ["小料", "植物肉"])

    def test_standard_price_is_amount_weighted_mode(self):
        """标准单价按金额加权取众数，别让个别特殊岗位当选。"""
        b = ta.board_from_periods([self._period("2026-07", [
            self._p("锦绣", "植物肉", 1000, 0),                      # 19 元，权重大
            self._p("锦绣", "植物肉", 10, 0, dp=(15.0, 0.0)),        # 15 元，权重小
        ])])
        self.assertEqual(b["标准单价"]["白班"], 19.0)

    def test_period_status_exposes_anomalies(self):
        """异常不藏——把出过问题的月份盖掉，比看不到还糟。"""
        m, res = self._period("2026-07", [self._p("锦绣", "植物肉", 100, 0)])
        res["stats"].update({"异常多记日次": 3, "金额核对条数": 2,
                             "合同外调整合计": {"异常": 1}, "同名跨派遣方数": 1})
        b = ta.board_from_periods([(m, res)])
        s = b["期次"][0]
        self.assertEqual((s["异常多记日次"], s["金额核对条数"], s["奖罚异常"], s["同名跨派遣方"]), (3, 2, 1, 1))

    def test_empty_input(self):
        b = ta.board_from_periods([])
        self.assertEqual(b["months"], [])
        self.assertEqual(b["kpi"]["期数"], 0)


class TestContractOnly(unittest.TestCase):
    """合同价必须「成本会计登记了什么就是什么」。

    曾经的真 bug（V2.342–V2.345）：合同价表拿内置观察表打底，结果成本会计一行没登记，
    也满屏绿色「一致」——那张表本来就是从人力自己的数据里反推出来的，等于白送一个刚好对得上的"合同价"。
    """

    def test_登记表之外一律不补(self):
        self.assertEqual(ta.contract_only(None), {})
        self.assertEqual(ta.contract_only({}), {})
        only = ta.contract_only({"锦绣": {"普工": {"day": (10.0, 2.0), "night": (10.0, 2.0)}}})
        self.assertEqual(set(only), {"锦绣"}, "登记表之外的派遣方冒出来了")
        self.assertIn("华顺", ta.RATE_TABLE_OBSERVED,
                      "前提：观察表里确实有华顺，这条测试才有意义")

    def test_没登记的派遣方判成合同缺档而不是一致(self):
        people = [
            {"name": "甲", "agency": "锦绣", "kind": "普工", "白班": 100.0, "夜班": 0.0,
             "表上": {"白班工资单价": 16.5, "白班管理费单价": 2.5}},
            {"name": "乙", "agency": "华顺", "kind": "普工", "白班": 80.0, "夜班": 0.0,
             "表上": {"白班工资单价": 16.5, "白班管理费单价": 2.5}},
        ]
        r = ta.contract_vs_actual(people, ta.contract_only({"锦绣": {"普工": {"day": (10.0, 2.0)}}}))
        by = {(x["派遣方"], x["班次"]): x["状态"] for x in r["明细"]}
        self.assertEqual(by[("锦绣", "白班")], "⚠不符")     # 登记 10+2，人力用 16.5+2.5
        self.assertEqual(by[("华顺", "白班")], "合同缺档")   # 没登记 → 缺档，绝不能是「一致」
        self.assertEqual(r["一致"], 0)
        self.assertEqual(r["合同缺档"], 1)
        self.assertFalse(r["全对"])

    def test_一行都没登记时全部缺档(self):
        people = [{"name": "甲", "agency": "锦绣", "kind": "普工", "白班": 10.0, "夜班": 0.0,
                   "表上": {"白班工资单价": 16.5, "白班管理费单价": 2.5}}]
        r = ta.contract_vs_actual(people, ta.contract_only(None))
        self.assertEqual(r["一致"], 0)
        self.assertEqual(r["合同缺档"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
