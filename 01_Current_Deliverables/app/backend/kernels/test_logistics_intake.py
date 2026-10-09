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


if __name__ == "__main__":
    unittest.main()
