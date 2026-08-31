# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-07-09 | Author: Claude / c | Version: V2.57
# Description: 成本台账内核单元测试（合成数据，不连金蝶）。覆盖：解析、收发存自平、两表互勾、
#   账实勾稽（含对照缺失）、四类异常扫描（负结存/挂账尾差镜像/对照缺失/成本调整）、损益归集。
#   5 月真底稿端到端 DoD 另见 tools/run_cost_ledger_5月.py。
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kernels import cost_ledger as cl

CFG = cl.load_config(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "sample_data", "cost_ledger_config.json"))

# 合成"金蝶跨维度导出"行：前两行元数据 + 表头 + 数据（模拟真实导出结构）
HEADER = ["物料编码", "物料名称", "存货类别", "物料分组", "规格型号", "库存状态", "批号", "仓库",
          "核算范围编码", "核算范围名称", "基本单位",
          "期初数量", "期初单价", "期初金额", "收入数量", "收入单价", "收入金额",
          "发出数量", "发出单价", "发出金额", "结存数量", "结存单价", "结存金额"]


def row(code, name, cat, wh, oq, oa, iq, ia, dq, da, eq, ea, batch="2026"):
    return [code, name, cat, "", "", "可用", batch, wh, "HSFW", "孝感星期九", "千克",
            oq, 0, oa, iq, 0, ia, dq, 0, da, eq, 0, ea]


def make_rows(data_rows):
    return [["核算体系:财务会计核算体系", "核算组织:孝感星期九"], ["会计期间:2026年第5期"], HEADER] + data_rows


class TestParse(unittest.TestCase):
    def test_parse_and_skip_meta_total(self):
        rows = make_rows([row("A", "甲", "产成品", "孝感成品仓", 0, 0, 10, 100, 4, 40, 6, 60),
                          ["合计", None, None, None, None, None, None, None, None, None, None,
                           0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]])
        recs = cl.parse_cross_report(rows)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["code"], "A")
        self.assertAlmostEqual(recs[0]["ea"], 60)
        self.assertEqual(recs[0]["wh"], "孝感成品仓")

    def test_missing_header_raises(self):
        with self.assertRaises(ValueError):
            cl.parse_cross_report([["随便", "什么"], ["没有表头"]])


class TestSelfBalance(unittest.TestCase):
    def test_self_balance_pass(self):
        recs = cl.parse_cross_report(make_rows([
            row("A", "甲", "产成品", "W", 100, 1000, 50, 500, 30, 300, 120, 1200),
            row("B", "乙", "原材料", "W", 0, 0, 20, 200, 20, 200, 0, 0)]))
        t = cl.tie_receipt_balance(recs)
        self.assertTrue(t["pass"])
        self.assertTrue(t["产成品"]["pass"])
        self.assertAlmostEqual(t["合计"]["ea"], 1200)

    def test_self_balance_fail_detected(self):
        recs = cl.parse_cross_report(make_rows([
            row("A", "甲", "产成品", "W", 100, 1000, 0, 0, 0, 0, 0, 900)]))  # 1000≠900
        t = cl.tie_receipt_balance(recs)
        self.assertFalse(t["pass"])
        self.assertFalse(t["产成品"]["pass"])
        self.assertAlmostEqual(t["产成品"]["diff"], 100)


class TestTwoReports(unittest.TestCase):
    def test_two_reports_tie(self):
        cross = cl.parse_cross_report(make_rows([
            row("A", "甲", "产成品", "W1", 0, 0, 10, 100, 4, 40, 6, 60),
            row("A", "甲", "产成品", "W2", 0, 0, 5, 50, 0, 0, 5, 50)]))
        bydate = cl.parse_cross_report(make_rows([
            row("A", "甲", "产成品", "", 0, 0, 15, 150, 4, 40, 11, 110)]))  # 物料级合并
        t = cl.tie_two_reports(cross, bydate)
        self.assertTrue(t["pass"])
        self.assertAlmostEqual(t["结存金额"]["cross"], 110)


class TestBookVsActual(unittest.TestCase):
    def test_mapping_and_diff_zero(self):
        recs = cl.parse_cross_report(make_rows([
            row("A", "甲", "产成品", "W", 0, 0, 0, 0, 0, 0, 10, 4000),
            row("B", "乙", "自制半成品", "W", 0, 0, 0, 0, 0, 0, 5, 200),
            row("C", "丙", "原材料", "W", 0, 0, 0, 0, 0, 0, 8, 6000),
            row("D", "丁", "包材", "W", 0, 0, 0, 0, 0, 0, 3, 400)]))
        gl = {"库存商品": 4200, "原材料": 6400, "周转材料": 0, "在途物资": 4465.13, "委托加工物资": 31519.64}
        t = cl.tie_book_vs_actual(recs, gl, CFG)
        self.assertTrue(t["pass"])
        self.assertAlmostEqual(t["subjects"]["库存商品"]["actual"], 4200)
        self.assertAlmostEqual(t["subjects"]["库存商品"]["diff"], 0)
        self.assertAlmostEqual(t["extra"]["在途物资"], 4465.13)
        self.assertEqual(t["unmapped"], [])

    def test_unmapped_category_flagged(self):
        recs = cl.parse_cross_report(make_rows([
            row("X", "新", "新类别未配", "W", 0, 0, 0, 0, 0, 0, 1, 99)]))
        gl = {"库存商品": 0, "原材料": 0, "周转材料": 0}
        t = cl.tie_book_vs_actual(recs, gl, CFG)
        self.assertEqual(len(t["unmapped"]), 1)
        self.assertEqual(t["unmapped"][0]["cat"], "新类别未配")


class TestAnomalies(unittest.TestCase):
    def test_negative_balance(self):
        recs = cl.parse_cross_report(make_rows([
            row("A", "甲", "产成品", "孝感成品仓", 0, 0, 0, 0, 6, 208.61, -6, -208.61)]))
        a = cl.scan_anomalies(recs, CFG)
        self.assertEqual(a["counts"][cl.ST_NEG], 1)
        self.assertEqual(a["items"][0]["status"], cl.ST_NEG)

    def test_tail_diff_mirror(self):
        recs = cl.parse_cross_report(make_rows([
            row("P", "料", "产成品", "昆山吉波（新）", 0, 0, 0, 0, 0, 0, 0, 0.0011),
            row("P", "料", "产成品", "山东新飞达仓", 0, 0, 0, 0, 0, 0, 0, -0.0011)]))
        a = cl.scan_anomalies(recs, CFG)
        self.assertEqual(a["counts"][cl.ST_TAILDIFF], 2)
        tail = [it for it in a["items"] if it["status"] == cl.ST_TAILDIFF]
        self.assertIn("mirror", tail[0])

    def test_cost_adjust_not_anomaly_but_flagged(self):
        recs = cl.parse_cross_report(make_rows([
            row("M", "马蹄", "产成品", "W", 0, 0, 2990, 42155.26, 0, -1996.53, 2990, 44151.79)]))
        a = cl.scan_anomalies(recs, CFG)
        self.assertEqual(a["counts"][cl.ST_COSTADJ], 1)

    def test_normal_not_flagged(self):
        recs = cl.parse_cross_report(make_rows([
            row("N", "正常", "产成品", "W", 100, 1000, 0, 0, 20, 200, 80, 800)]))
        a = cl.scan_anomalies(recs, CFG)
        self.assertEqual(a["counts"][cl.ST_OK], 1)
        self.assertEqual(len(a["items"]), 0)

    def test_counts_sum_to_total(self):
        recs = cl.parse_cross_report(make_rows([
            row("A", "甲", "产成品", "孝感成品仓", 0, 0, 0, 0, 1, 30, -1, -30),
            row("N", "正", "产成品", "W", 100, 1000, 0, 0, 0, 0, 100, 1000)]))
        a = cl.scan_anomalies(recs, CFG)
        self.assertEqual(sum(a["counts"].values()), a["total_rows"])


class TestPnl(unittest.TestCase):
    def test_loss_and_disposal(self):
        loss = [{"cat": "包材盘盈亏", "amount": -2430.82}, {"cat": "产品盘盈亏", "amount": -2166.30},
                {"cat": "原辅料盘盈亏", "amount": -365.65}, {"cat": "产品盘盈亏", "amount": 88.25}]
        disp = [{"amount": 6062.83}, {"amount": 1874.63}, {"amount": 880.09},
                {"amount": 72.96}, {"amount": 21.86}]
        p = cl.collect_pnl(loss, disp)
        self.assertAlmostEqual(p["loss"]["total"], -4874.52, places=2)   # 4 行合成样本合计
        self.assertAlmostEqual(p["disposal"]["total"], 8912.37, places=1)
        self.assertAlmostEqual(p["loss"]["by_cat"]["产品盘盈亏"], -2078.05, places=2)


class TestPivotWhCategory(unittest.TestCase):
    """仓库 × 存货类别 交叉透视（V2.118）。"""

    def _recs(self):
        # 孝感成品仓: 产成品 120/1200 ; 孝感原料仓: 原材料 50/500 + 包材 10/100 ; 外仓(上海诚煜仓): 产成品 5/50
        return cl.parse_cross_report(make_rows([
            row("A", "甲", "产成品", "孝感成品仓", 0, 0, 0, 0, 0, 0, 120, 1200),
            row("B", "乙", "原材料", "孝感原料仓", 0, 0, 0, 0, 0, 0, 50, 500),
            row("C", "丙", "包材", "孝感原料仓", 0, 0, 0, 0, 0, 0, 10, 100),
            row("D", "丁", "产成品", "上海诚煜仓", 0, 0, 0, 0, 0, 0, 5, 50)]))

    def test_cells_rows_and_totals(self):
        p = cl.pivot_wh_category(self._recs(), CFG["warehouse_attr"])
        by_wh = {r["wh"]: r for r in p["rows"]}
        self.assertEqual(by_wh["孝感原料仓"]["cells"]["原材料"], {"eq": 50.0, "ea": 500.0})
        self.assertEqual(by_wh["孝感原料仓"]["cells"]["包材"], {"eq": 10.0, "ea": 100.0})
        self.assertEqual(by_wh["孝感原料仓"]["total"], {"eq": 60.0, "ea": 600.0})
        # 仓库类型取自对照表
        self.assertEqual(by_wh["孝感成品仓"]["type"], "孝感内仓")
        self.assertEqual(by_wh["上海诚煜仓"]["type"], "外仓")
        # 列合计 / 总计
        self.assertEqual(p["cat_total"]["产成品"], {"eq": 125.0, "ea": 1250.0})
        self.assertEqual(p["total"], {"eq": 185.0, "ea": 1850.0})

    def test_type_subtotal_closes(self):
        p = cl.pivot_wh_category(self._recs(), CFG["warehouse_attr"])
        t = {x["type"]: x for x in p["types"]}
        self.assertEqual(t["孝感内仓"]["total"], {"eq": 180.0, "ea": 1800.0})   # 成品仓+原料仓
        self.assertEqual(t["外仓"]["total"], {"eq": 5.0, "ea": 50.0})
        self.assertEqual(t["孝感内仓"]["cells"]["原材料"], {"eq": 50.0, "ea": 500.0})
        # 类型小计之和 = 总计（横竖闭合）
        self.assertAlmostEqual(sum(x["total"]["ea"] for x in p["types"]), p["total"]["ea"], places=2)
        self.assertAlmostEqual(sum(r["total"]["ea"] for r in p["rows"]), p["total"]["ea"], places=2)
        self.assertAlmostEqual(sum(v["ea"] for v in p["cat_total"].values()), p["total"]["ea"], places=2)

    def test_missing_attr_and_no_wh_are_not_hard_mapped(self):
        recs = cl.parse_cross_report(make_rows([
            row("E", "戊", "产成品", "查无此仓", 0, 0, 0, 0, 0, 0, 1, 10),
            row("F", "己", "", "", 0, 0, 0, 0, 0, 0, 2, 20)]))
        p = cl.pivot_wh_category(recs, CFG["warehouse_attr"])
        by_wh = {r["wh"]: r for r in p["rows"]}
        self.assertEqual(by_wh["查无此仓"]["type"], "（属性缺失）")   # 不硬归到某个类型
        self.assertEqual(by_wh["（无仓库）"]["type"], "（无仓库）")
        self.assertIn("（未分类）", p["cats"])
        self.assertEqual(p["total"], {"eq": 3.0, "ea": 30.0})       # 缺属性/无仓库仍计入总计


class TestBuild(unittest.TestCase):
    def test_build_credible_when_all_tie(self):
        recs = cl.parse_cross_report(make_rows([
            row("A", "甲", "产成品", "孝感成品仓", 100, 1000, 50, 500, 30, 300, 120, 1200)]))
        gl = {"库存商品": 1200, "原材料": 0, "周转材料": 0}
        r = cl.build_cost_ledger(recs, CFG, gl_balance=gl, bydate=recs,
                                 loss_rows=[], disposal_rows=[])
        self.assertTrue(r["credible"])
        self.assertIn("孝感成品仓", r["pivot_warehouse"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

class TestCategoryDrift(unittest.TestCase):
    """类别漂移（V2.282）。真实场景：2026-3 有 18 个物料被重分类，总额未变但类别归属变了，
    导致账实勾稽差 ±1.92，而这事三个月后才被人拿旧底稿对出来。"""

    def _rows(self, cat_a="原材料", cat_b="低值易耗品"):
        return [{"code": "A001", "name": "纸箱", "cat": cat_a, "grp": "包装", "ea": 1000.0},
                {"code": "B002", "name": "手套", "cat": cat_b, "grp": "劳保", "ea": 50.0}]

    def test_map_and_no_drift(self):
        rows = self._rows()
        m = cl.build_cat_map(rows)
        self.assertEqual(m["A001"], ["原材料", "包装"])
        d = cl.scan_category_drift(rows, m, "上期")
        self.assertEqual(d["n"], 0)

    def test_detects_category_change(self):
        prev = cl.build_cat_map(self._rows())
        d = cl.scan_category_drift(self._rows(cat_a="包材"), prev, "2026年2期")
        self.assertEqual(d["n"], 1)
        it = d["items"][0]
        self.assertEqual((it["code"], it["old_cat"], it["new_cat"]), ("A001", "原材料", "包材"))
        self.assertTrue(it["cat_changed"])
        self.assertEqual(d["amount"], 1000.0)
        self.assertEqual(d["prev"], "2026年2期")

    def test_new_and_gone_materials_are_not_drift(self):
        """新物料没有"变过"可言，上期有本期没有的也无从比——都不该报。"""
        prev = cl.build_cat_map([{"code": "OLD", "cat": "原材料", "grp": "", "ea": 1.0}])
        d = cl.scan_category_drift([{"code": "NEW", "name": "新料", "cat": "包材", "grp": "", "ea": 9.0}],
                                    prev, "上期")
        self.assertEqual(d["n"], 0)

    def test_no_prev_map_is_silent(self):
        """没有上期基线时不报——首次使用不该刷一屏"漂移"。"""
        d = cl.scan_category_drift(self._rows(), None, "")
        self.assertEqual(d["n"], 0)

    def test_sorted_by_amount(self):
        prev = cl.build_cat_map(self._rows())
        d = cl.scan_category_drift(self._rows(cat_a="包材", cat_b="广宣品"), prev, "上期")
        self.assertEqual([x["code"] for x in d["items"]], ["A001", "B002"])   # 1000 在 50 之前

