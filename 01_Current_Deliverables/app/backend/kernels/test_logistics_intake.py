# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-08 | Author: Claude Opus 5.5 | Version: V2.864
# Description: 物流账单取数(logistics_intake)单测——单号列合并单元格：合并块下半截沿用首行单号(不看这行写没写日期序号)。合成数据。
import io
import unittest

import openpyxl

from kernels import logistics_intake as li

SPEC = {"carrier": "天鹰物流", "period": "2026-08", "sheets": [{
    "name": "蜜雪装货明细*", "role": "detail", "header_row": 2, "row_re": {"col": "产品名称", "re": "^(?!合计|总计|小计).+"},
    "doc_col": ["金蝶单据编号", "金蝶单号"], "doc_ffill": True, "doc_default": "无单据", "merge_doc": True,
    "amount_col": "含税金额*", "wt_col": "吨数(T)", "wt_scale": 1000, "amount_dp": 6, "doc_ffill_if_blank": ["装货日期"]}]}


def _book(merge=True):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "蜜雪装货明细 (3)"
    ws.append(["蜜雪装货记录表"])
    ws.append(["序号", "产品名称", "吨数(T)", "装货日期", "金蝶单据编号", "含税金额（18.5元/吨）"])
    ws.append([1, "冷冻米麻薯", 8, 46265, "XSCKD219507", 148])
    ws.append([2, "冷冻米麻薯", 8, 46265, None, 148])            # 第 4 行：单号格和上一行合并，自己写了序号、日期
    ws.append([3, "冷冻米麻薯", 8, 46265, "XSCKD219505", 148])
    ws.append([4, "冷冻米麻薯", 8, 46265, None, 148])            # 第 6 行：没合并、日期齐全却没单号 → 漏填
    ws.append([5, "冷冻米麻薯", 15, 46265, "XSCKD219513", 277.5])
    ws.append([None, "冷冻米麻薯", 2, None, None, 37])           # 第 8 行：没合并、没日期 → 续行(老规则)
    ws.merge_cells("A1:F1")
    if merge:
        ws.merge_cells("E3:E4")
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


class MergedDocCell(unittest.TestCase):
    def test_merged_rows(self):
        self.assertEqual(li.merged_rows(_book()), {"蜜雪装货明细 (3)": {4: {4: 3}}})    # 横向合并(标题行)不算
        self.assertEqual(li.merged_rows(_book(False)), {})
        self.assertEqual(li.merged_rows(b"not a zip"), {})

    def test_merged_cell_inherits_doc(self):
        by = {}
        for r in li.parse_bill(dict(SPEC), _book())["detail"]:
            by.setdefault(r["doc_no"], []).append(r)
        self.assertAlmostEqual(by["XSCKD219507"][0]["charge_wt"], 16000)     # 两行并成一单：8 吨 + 8 吨
        self.assertAlmostEqual(by["XSCKD219507"][0]["amount"], 296)
        self.assertAlmostEqual(by["XSCKD219505"][0]["charge_wt"], 8000)      # 没合并的那行不并进来
        self.assertEqual(len(by["无单据"]), 1)                               # 它还是按漏填单列
        self.assertAlmostEqual(by["XSCKD219513"][0]["charge_wt"], 17000)     # 没日期的续行照旧沿用上一单

    def test_without_merge_same_as_before(self):
        by = {}
        for r in li.parse_bill(dict(SPEC), _book(False))["detail"]:
            by.setdefault(r["doc_no"], []).append(r)
        self.assertAlmostEqual(by["XSCKD219507"][0]["charge_wt"], 8000)
        self.assertEqual(len(by["无单据"]), 2)


class DimCol(unittest.TestCase):
    """V2.888 按体积计费：备注列里的箱子尺寸读进分项(文字，不进金额)；不是尺寸的备注不收。合成数据。"""

    def test_dim_col(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "零担"
        ws.append(["合成账单"])
        ws.append(["金蝶单号", "总件数", "计重", "运费", "送货费", "备注"])
        ws.append(["XSCKD000001", 41, 158.91, 286.05, 100, "38*25.5*12"])
        ws.append(["XSCKD000002", 6, 30, 165, None, "起步价"])
        ws.append(["XSCKD000003", 2, 7.75, 20, None, " 38 × 25.5 × 12 cm "])
        bio = io.BytesIO()
        wb.save(bio)
        spec = {"carrier": "合成", "period": "2026-08", "sheets": [{
            "name": "零担", "role": "detail", "header_row": 2, "doc_col": ["金蝶单号"], "qty_col": "总件数", "wt_col": "计重",
            "fee_parts": {"运费": ["运费"], "送货费": ["送货费"]}, "dim_col": "备注"}]}
        got = {r["doc_no"]: r for r in li.parse_bill(spec, bio.getvalue())["detail"]}
        import json as _j
        sf = {k: _j.loads(v.get("sub_fees") or "{}") for k, v in got.items()}
        self.assertEqual(sf["XSCKD000001"].get("箱子尺寸"), "38*25.5*12")
        self.assertEqual(got["XSCKD000001"]["amount"], 386.05)          # 尺寸是文字，不进金额
        self.assertNotIn("箱子尺寸", sf["XSCKD000002"])
        self.assertEqual(sf["XSCKD000003"].get("箱子尺寸"), "38×25.5×12cm")
        self.assertTrue(li.DIM_RE.match(sf["XSCKD000003"]["箱子尺寸"]))


class TotalCol(unittest.TestCase):
    """V2.987 跨越 9 月：账单新加了 夜间取货费/退仓费/资源调配费 三列，取数说明只认老列名，拆项求和悄悄少读 81 元。
    配了 total_col(账单自己的应付合计)就以它为准，对不上的差额记「其他费用（未拆）」。合成数据。"""

    def _bill(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "帐单-深圳星期零"
        ws.append(["合成"])
        ws.append(["金蝶单号", "折后运费", "保费", "退仓费", "应付金额\n（元）"])
        ws.append(["CGRK000001", 54.78, 2, 60, 116.78])
        ws.append(["QTCK000002", 89.6, 2, 0, 91.6])
        bio = io.BytesIO()
        wb.save(bio)
        return bio.getvalue()

    def _spec(self, **kw):
        sp = {"name": "深圳星期零|孝感星期九", "role": "detail", "header_row": 2, "doc_col": "金蝶单号",
              "fee_parts": {"运费": ["折后运费"], "保费": ["保费"]}}
        sp.update(kw)
        return {"carrier": "合成", "period": "2026-09", "sheets": [sp]}

    def test_without_total_drops_new_column(self):
        got = {r["doc_no"]: r for r in li.parse_bill(self._spec(), self._bill())["detail"]}
        self.assertEqual(got["CGRK000001"]["amount"], 56.78)            # 老行为：新列没进拆项就丢了

    def test_total_col_keeps_money_and_shows_gap(self):
        import json as _j
        got = {r["doc_no"]: r for r in li.parse_bill(self._spec(total_col="应付金额*"), self._bill())["detail"]}
        self.assertEqual(got["CGRK000001"]["amount"], 116.78)
        self.assertEqual(_j.loads(got["CGRK000001"]["sub_fees"])["其他费用（未拆）"], 60.0)
        self.assertNotIn("其他费用（未拆）", _j.loads(got["QTCK000002"]["sub_fees"]))
        self.assertEqual(got["QTCK000002"]["amount"], 91.6)


class SheetReport(unittest.TestCase):
    """V2.989 复核台第①步「读全没有」：算钱的列加起来对账单自己的合计列。合成数据(仿跨越 9 月新加退仓费那一页)。"""

    def _bill(self, with_total=True):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "帐单-深圳星期零"
        ws.append(["合成"])
        ws.append(["序号", "金蝶单号", "计费重量（公斤）", "运单运费", "折后运费", "保费", "退仓费"] + (["应付金额\n（元）"] if with_total else []))
        ws.append([1, "CGRK000001", 30, 78.26, 54.78, 2, 60] + ([116.78] if with_total else []))
        ws.append([2, "QTCK000002", 50, 128, 89.6, 2, 0] + ([91.6] if with_total else []))
        ws.append(["合计", None, 80, 206.26, 144.38, 4, 60] + ([208.38] if with_total else []))
        ws2 = wb.create_sheet("Sheet1")
        ws2.append(["透视"])
        bio = io.BytesIO()
        wb.save(bio)
        return bio.getvalue()

    def _spec(self, **kw):
        sp = {"name": "深圳星期零|孝感星期九", "role": "detail", "header_row": 2, "doc_col": "金蝶单号", "wt_col": "计费重量（公斤）", "summary_marker": "合计",
              "fee_parts": {"运费": ["折后运费"], "保费": ["保费"]}}
        sp.update(kw)
        return {"carrier": "合成", "sheets": [sp]}

    def test_missing_column_shows_gap(self):
        r = li.sheet_report(self._spec(), self._bill(), "2026-09")
        s = r["sheets"][0]
        self.assertEqual(s["verdict"], "gap")
        self.assertEqual(s["total"]["how"], "guess")                 # 没配合计列：按表头认出「应付金额」只拿来比
        self.assertEqual(s["total"]["sum"], 208.38)                   # 合计行不算进去
        self.assertEqual(s["gap"], -60.0)
        self.assertEqual([u["name"] for u in s["unread"]], ["运单运费", "退仓费"])
        self.assertEqual(r["sheets"][1]["verdict"], "skip")
        self.assertEqual(r["verdict"], "gap")

    def test_all_columns_ok(self):
        r = li.sheet_report(self._spec(fee_parts={"运费": ["折后运费"], "保费": ["保费"], "退仓费": ["退仓费"]}), self._bill(), "2026-09")
        s = r["sheets"][0]
        self.assertEqual((s["verdict"], s["amount"], s["n"]), ("ok", 208.38, 2))
        self.assertNotIn("计费重量（公斤）", [u["name"] for u in s["unread"] + s["others"]])   # 用到的列不算没读
        self.assertEqual(r["verdict"], "ok")

    def test_total_col_keeps_money_but_flags_unsplit(self):
        s = li.sheet_report(self._spec(total_col="应付金额*"), self._bill(), "2026-09")["sheets"][0]
        self.assertEqual(s["verdict"], "unsplit")
        self.assertEqual((s["amount"], s["unsplit"]), (208.38, 60.0))

    def test_single_amount_col_is_its_own_total(self):
        s = li.sheet_report(self._spec(fee_parts=None, amount_col="应付金额*"), self._bill(), "2026-09")["sheets"][0]
        self.assertEqual((s["verdict"], s["total"]["how"], s["amount"]), ("ok", "self", 208.38))

    def test_no_total_but_unread_fee(self):
        s = li.sheet_report(self._spec(), self._bill(with_total=False), "2026-09")["sheets"][0]
        self.assertEqual(s["verdict"], "maybe")
        self.assertIn("退仓费", [u["name"] for u in s["unread"]])

    def test_rows_merged_into_one_doc_still_counted(self):
        """天鹰：一张单拆几行，解析时并成一行(只剩首行行号)。续行的钱也要加进来比，不能报对不上。"""
        s = li.sheet_report(dict(SPEC), _book(), "2026-08")["sheets"][0]
        self.assertEqual(s["verdict"], "ok")
        self.assertEqual(s["total"]["sum"], s["amount"])

    def test_configured_column_missing_this_month(self):
        s = li.sheet_report(self._spec(fee_parts={"运费": ["折后运费"], "保费": ["保费"], "退仓费": ["退仓费"], "回单费": ["回单费"]}), self._bill(), "2026-09")["sheets"][0]
        self.assertEqual([m["name"] for m in s["money"] if not m["found"]], ["回单费"])
        self.assertEqual(s["verdict"], "ok")


if __name__ == "__main__":
    unittest.main()
