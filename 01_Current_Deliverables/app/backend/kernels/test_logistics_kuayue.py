# -*- coding: utf-8 -*-
# 跨越官网计价核价核量内核单测：用 2026-09 真实账单 11 单做基准（样机定稿口径，用户 10-17 逐条看过）。
# 数据：output/kuayue-price-prototype/bill_rows.json + kd_docs.json（金蝶真实净重）；不连金蝶。
import unittest

from kernels import logistics_kuayue as ky


def _row(seq, no, formula, orig, net, pay, bw, minwt, aw, sur, note="", disc=70.0):
    return {"seq": seq, "no": no, "formula": formula, "orig": orig, "disc": disc, "net": net,
            "sur": sur, "pay": pay, "bw": bw, "minwt": minwt, "aw": aw, "note": note}


# 2026-09 跨越账单 11 单（真单字段）+ 金蝶净重合计(kg，Pcs 计量=0)
CASES = [
    # no, kd_sum, row...
    ("CGRK183533", 20.0, _row(1, "CGRK183533", "10.0+19.5*3.5", 78.25, 54.78, 116.78, 20.5, 20.0, 20.5, 62.0)),
    ("XSCKD217640", 240.0, _row(2, "XSCKD217640", "11.0+59.0*1.8", 117.2, 82.04, 84.04, 60.0, 5.0, 60.0, 2.0,
                                "8月26日订单，路凯卡板签收后退回")),
    ("QTCK011678", 0.0, _row(3, "QTCK011678", "10.0+29.0*3.5,运费最低收费112.0（四舍五入取整）", 112.0, 78.4, 80.4,
                              30.0, 30.0, 10.8, 2.0, "最低收费")),
    ("QTCK011865", 5.0, _row(4, "QTCK011865", "20.0+9.0*12.0,运费最低收费128.0（四舍五入取整）", 128.0, 89.6, 91.6,
                              10.0, 10.0, 6.6, 2.0, "最低收费")),
    ("QTCK011696", 4.76, _row(5, "QTCK011696", "20.0+9.0*12.0,运费最低收费128.0（四舍五入取整）", 128.0, 89.6, 91.6,
                               10.0, 10.0, 7.8, 2.0, "最低收费")),
    ("QTCK011740", 5.0, _row(6, "QTCK011740", "20.0+9.0*12.0+2.35*12.0", 156.2, 109.34, 111.34, 12.35, 10.0, 12.35, 2.0)),
    ("QTCK011768", 5.0, _row(7, "QTCK011768", "20.0+29.0*10.0,运费最低收费310.0（四舍五入取整）", 310.0, 217.0, 219.0,
                              30.0, 30.0, 7.2, 2.0, "最低收费，紧急单，业务端要求当日达9点前送达")),
    ("QTCK011794", 4.8, _row(8, "QTCK011794", "10.0+15.0*4.2", 73.0, 51.1, 53.1, 16.0, 15.0, 8.0, 2.0,
                              "体积40*40*25")),
    ("CGRK213414", 0.0, _row(9, "CGRK213414", "10.0+415.0*2.8", 1172.0, 820.4, 822.4, 416.0, 20.0, 416.0, 2.0,
                              "系统为PCS,单箱18.9kg/箱")),
    ("XSCKD257540", 20.0, _row(10, "XSCKD257540", "20.0+9.0*7.0+20.0*7.0,运费最低收费223.0（四舍五入取整）",
                                223.0, 156.1, 178.1, 30.0, 30.0, 22.5, 22.0, "最低收费")),
    ("QTCK011852", 10.0, _row(11, "QTCK011852", "20.0+9.0*8.0+1.6*8.0", 104.8, 73.36, 76.36, 11.6, 10.0, 11.6, 3.0)),
]


class TestFormula(unittest.TestCase):
    def test_plain(self):
        f = ky.parse_freight_formula("10.0+19.5*3.5")
        self.assertEqual(f["calc"], 78.25)
        self.assertFalse(f["rounded"])
        self.assertIsNone(f["minfee"])

    def test_minfee_round_up(self):
        # 10+29×3.5=111.5 → 四舍五入取整 112，再和最低收费 112 取大
        f = ky.parse_freight_formula("10.0+29.0*3.5,运费最低收费112.0（四舍五入取整）")
        self.assertEqual(f["calc"], 112.0)
        self.assertTrue(f["rounded"])
        self.assertEqual(f["minfee"], 112.0)

    def test_minfee_floor(self):
        # 20+9×12=128 正好等于最低收费
        f = ky.parse_freight_formula("20.0+9.0*12.0,运费最低收费128.0（四舍五入取整）")
        self.assertEqual(f["calc"], 128.0)

    def test_minfee_beats_sum(self):
        # 310 > 最低收费 310 取大；当天达 20+29×10=310
        f = ky.parse_freight_formula("20.0+29.0*10.0,运费最低收费310.0（四舍五入取整）")
        self.assertEqual(f["calc"], 310.0)

    def test_js_rounding_not_bankers(self):
        # 54.775 分位取整 → 54.78（JS 半进位；Python 银行家舍入会给 54.78 以外的结果，内核必须按账单口径）
        self.assertEqual(ky._r2(54.775), 54.78)
        self.assertEqual(ky._r0(111.5), 112.0)

    def test_garbage(self):
        self.assertIsNone(ky.parse_freight_formula(""))
        self.assertIsNone(ky.parse_freight_formula("abc"))


class TestVerdicts(unittest.TestCase):
    def _judge(self, no, kd, row, lo=0.95, hi=1.20):
        d = {"formula": row["formula"], "orig": row["orig"], "disc": row["disc"], "net": row["net"],
             "sur": row["sur"], "pay": row["pay"], "bw": row["bw"], "minwt": row["minwt"],
             "aw": row["aw"], "note": row["note"]}
        p = ky.price_check(d)
        q = ky.qty_check(d, kd, lo, hi)
        return p, q, ky.verdict(p, q)

    def test_all_price_ok(self):
        for no, kd, row in CASES:
            p, q, v = self._judge(no, kd, row)
            self.assertTrue(p["ok_all"], "%s 价三样应全对: %s" % (no, p["bads"]))

    def test_minfee_five(self):
        # 按最低计费 5 单（含 QTCK011678 金蝶全 Pcs 折不了千克——账单计费正好＝起重，照样放行）
        for no in ("QTCK011678", "QTCK011865", "QTCK011696", "QTCK011768", "XSCKD257540"):
            kd = dict((n, k) for n, k, _ in CASES)[no]
            row = dict((r["no"], r) for _, _, r in CASES)[no]
            p, q, v = self._judge(no, kd, row)
            self.assertEqual(q["label"], "按最低计费", no)
            self.assertEqual(v["verdict"], "一致", no)
            self.assertEqual(v["diff"], 0.0, no)

    def test_overcharge_11740(self):
        row = dict((r["no"], r) for _, _, r in CASES)["QTCK011740"]
        p, q, v = self._judge("QTCK011740", 5.0, row)
        self.assertEqual(q["label"], "多报")
        self.assertEqual(q["diff_kg"], 2.35)          # 12.35 − max(5×1.2, 起重10)
        self.assertEqual(v["verdict"], "多收")
        self.assertEqual(v["diff"], 19.74)            # 2.35kg × 12元/kg × 70%

    def test_inrange_11852(self):
        row = dict((r["no"], r) for _, _, r in CASES)["QTCK011852"]
        p, q, v = self._judge("QTCK011852", 10.0, row)
        self.assertEqual(q["label"], "量对")
        self.assertEqual(q["conv"], 1.16)
        self.assertEqual(v["verdict"], "一致")

    def test_pcs_cgrk(self):
        # CGRK213414 包装袋 44000 Pcs：计费 416 ≠ 起重 20，金蝶折不了千克 → 量核不了
        row = dict((r["no"], r) for _, _, r in CASES)["CGRK213414"]
        p, q, v = self._judge("CGRK213414", 0.0, row)
        self.assertEqual(q["label"], "量核不了")
        self.assertEqual(v["verdict"], "量核不了")
        self.assertIsNone(v["diff"])

    def test_return_doc(self):
        # XSCKD217640：金蝶查到 8 月原出库 240kg，账单 60kg 是退回运输 → 量核不了（不机械判少收 168kg）
        row = dict((r["no"], r) for _, _, r in CASES)["XSCKD217640"]
        p, q, v = self._judge("XSCKD217640", 240.0, row)
        self.assertEqual(q["label"], "量核不了·退回")
        self.assertEqual(v["verdict"], "量核不了")

    def test_vol_weight(self):
        # QTCK011794 备注带「体积」→ 只核了价
        row = dict((r["no"], r) for _, _, r in CASES)["QTCK011794"]
        p, q, v = self._judge("QTCK011794", 4.8, row)
        self.assertEqual(q["label"], "量核不了·体积重")
        self.assertEqual(v["verdict"], "只核了价")

    def test_price_bad(self):
        # 账单单价算错：原价 156.2 被账单写成 160 → 折后/应付全跟着错，判多收
        row = dict((r["no"], r) for _, _, r in CASES)["QTCK011740"]
        bad = dict(row, orig=160.0)
        p, q, v = self._judge("QTCK011740", 5.0, bad)
        self.assertFalse(p["ok_all"])
        # 应付 111.34 与公式重算一致，差的只是「原价」这一列 → 差额按公式口径＝0，判少收（照样机口径：价差按公式重算折）
        self.assertEqual(v["verdict"], "少收")
        self.assertEqual(v["diff"], 0.0)

    def test_range_configurable(self):
        # 区间收紧到 1.10：QTCK011852 系数 1.16 超上限 → 多收
        row = dict((r["no"], r) for _, _, r in CASES)["QTCK011852"]
        p, q, v = self._judge("QTCK011852", 10.0, row, lo=0.95, hi=1.10)
        self.assertEqual(q["label"], "多报")
        self.assertEqual(v["verdict"], "多收")

    def test_total_tieout(self):
        tot = round(sum(r["pay"] for _, _, r in CASES), 2)
        self.assertEqual(tot, 1924.72)      # 与账单「本期应付总额 1,924.72」勾稽


class TestIntakeKyprice(unittest.TestCase):
    """取数说明配 kyprice 块时，解析层把跨越计价的原始字段收进 sub_fees["跨越计价"](JSON 字符串)——
    非数字、不进费用求和；核价核量由内核在复核时现算。"""

    def _book(self):
        import io
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "账单"
        hdr = ["序号", "寄件日期", "单 号", "计费重量（公斤）", "运单运费", "折扣(%)", "折后运费",
               "夜间取货费", "保费", "退仓费", "资源调配费", "应付金额\n（元）", "金蝶单号", "备注", "起重", "实际重量", "运费公式"]
        ws.append(["本期应付总额：1,924.72元"])
        ws.append(hdr)
        ws.append([1, "2026-09-15", "KY4001151264656", 12.35, 156.2, 70, 109.34, 0, 2, 0, 0, 111.34,
                   "QTCK011740", "", 10, 12.35, "20.0+9.0*12.0+2.35*12.0"])
        ws.append([2, "2026-09-05", "KY4001133178870", 30, 112, 70, 78.4, 0, 2, 0, 0, 80.4,
                   "QTCK011678", "最低收费", 30, 10.8, "10.0+29.0*3.5,运费最低收费112.0（四舍五入取整）"])
        b = io.BytesIO()
        wb.save(b)
        return b.getvalue()

    def test_kyprice_captured(self):
        import openpyxl
        from io import BytesIO
        from kernels import logistics_intake as li
        SPEC = {"carrier": "跨越物流", "period": "2026-09", "sheets": [{
            "name": "账单", "role": "detail", "header_row": 2,
            "doc_col": "金蝶单号", "qty_col": "件数", "wt_col": "实际重量", "carrier_sub_col": "服务方式",
            "total_col": "应付金额*",
            "fee_parts": {"折后运费": ["折后运费"], "夜间取货费": ["夜间取货费"], "保费": ["保费"],
                          "退仓费": ["退仓费"], "资源调配费": ["资源调配费"]},
            "kyprice": {"formula_col": "运费公式", "orig_col": "运单运费", "disc_col": "折扣*",
                        "net_col": "折后运费", "bw_col": "计费重量*", "aw_col": "实际重量",
                        "minwt_col": "起重", "pay_col": "应付金额*",
                        "sur_cols": ["夜间取货费", "保费", "退仓费", "资源调配费"], "note_col": "备注"}}]}
        wb = openpyxl.load_workbook(BytesIO(self._book()), data_only=True)
        rows = li.parse_detail_sheet(SPEC["sheets"][0], wb["账单"], "2026-09", "跨越物流")
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(rows[0]["amount"], 111.34, places=2)   # total_col 应付金额* 为准
        import json
        sf0 = json.loads(rows[0]["sub_fees"])
        d = json.loads(sf0[ky.KY_SUBKEY])           # JSON 字符串里再套 JSON——解析层只存数，判定在复核时算
        self.assertEqual(d["formula"], "20.0+9.0*12.0+2.35*12.0")
        self.assertEqual(d["orig"], 156.2)
        self.assertEqual(d["disc"], 70.0)
        self.assertEqual(d["net"], 109.34)
        self.assertEqual(d["sur"], 2.0)             # 0+2+0+0
        self.assertEqual(d["pay"], 111.34)
        self.assertEqual(d["bw"], 12.35)
        self.assertEqual(d["minwt"], 10.0)
        # 费用分项求和不含「跨越计价」字符串（base 只加数字项）＝ total_col 应付金额
        nums = [v for v in sf0.values() if isinstance(v, (int, float))]
        self.assertAlmostEqual(sum(nums), 111.34, places=2)
        # 内核直接吃解析产物，判定和单测基准一致
        p = ky.price_check(d)
        self.assertTrue(p["ok_all"])
        d1 = json.loads(json.loads(rows[1]["sub_fees"])[ky.KY_SUBKEY])
        q = ky.qty_check(d1, 0.0)                   # QTCK011678 金蝶全 Pcs → 折不了千克
        self.assertEqual(q["label"], "按最低计费")


if __name__ == "__main__":
    unittest.main()
