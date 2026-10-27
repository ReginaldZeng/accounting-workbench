# -*- coding: utf-8 -*-
# V2.749 付款做账单测：样本＝易风达 8 月孝感(审批 202609111542000383471，87,168)——
# 和用户 2026-10-01 逐条定下来的那张凭证逐行对：记-565 设备转移费 300 计提 6%、发票 9% → 红冲+更正；其余四张核销；支付 87,168。
import unittest

from kernels import logistics_voucher as V

SUP = "武汉易风达冷链物流有限公司"
SC = {"sup_code": "物流运输服务027", "sup_name": SUP}
D5101_IN = {"dept_code": "0030301", "dept": "仓储物流部", "fee_code": "FYXM002.002运费成本002", "fee": "入库运费"}
D5101_BY = {"dept_code": "0030301", "dept": "仓储物流部", "fee_code": "FYXM005.003其他业务活动005", "fee": "搬运费"}
D6601_OUT = {"dept_code": "0030301", "dept": "仓储物流部", "fee_code": "FYXM008.002其他物流费用002", "fee": "出库运费", "biz_code": "CPFL009", "biz": "小料"}
D6601_CC = {"dept_code": "0030301", "dept": "仓储物流部", "fee_code": "FYXM008.002其他物流费用001", "fee": "货物仓储费", "biz_code": "CPFL009", "biz": "小料"}


def acc(vno, expl, acct, aname, net, tax, gross, dims):
    lines = [{"acct": acct, "acct_name": aname, "dr": net, "cr": 0, "expl": expl, **dims},
             {"acct": "2221.01.07", "acct_name": "暂估进项税", "dr": tax, "cr": 0, "expl": expl, **SC},
             {"acct": "2241.02", "acct_name": "供应商往来", "dr": 0, "cr": gross, "expl": expl, **SC}]
    return V.acc_voucher(vno, lines, 2026, 8)


def vouchers():
    return [
        acc("552", "计提%s8月线下孝感工厂入库运费" % SUP, "5101", "制造费用", 366.97, 33.03, 400, D5101_IN),
        acc("553", "计提%s8月线下小料出库-装卸费运费" % SUP, "6601", "销售费用", 5900.94, 354.06, 6255, D6601_OUT),
        acc("563", "计提%s8月线下仓储费" % SUP, "6601", "销售费用", 10672.64, 640.36, 11313, D6601_CC),
        acc("565", "计提%s8月线下设备转移费" % SUP, "5101", "制造费用", 283.02, 16.98, 300, D5101_BY),
        acc("566", "计提%s8月线下小料出库运费" % SUP, "6601", "销售费用", 63211.01, 5688.99, 68900, D6601_OUT),
    ]


INV = [{"number": "26422000003235560856", "rate": "6%", "gross": 1302.00, "tax": 73.70},
       {"number": "26422000003236119231", "rate": "6%", "gross": 10011.00, "tax": 566.66},
       {"number": "26422000003235839961", "rate": "9%", "gross": 69600.00, "tax": 5746.79},
       {"number": "26422000003235460401", "rate": "6%", "gross": 6255.00, "tax": 354.06}]
CTX = {"supplier": SUP, "applicant": "陈慧娴", "pay_year": 2026, "pay_month": 9, "pay_amount": 87168, "bank": "宁波银行86041110000117736", "paid": True}


class T(unittest.TestCase):
    def test_plan_yfd(self):
        vs = vouchers()
        pl = V.plan(vs, INV)
        self.assertEqual(pl["status"], "ok", pl["msgs"])
        self.assertEqual({k: p["mode"] for k, p in pl["per"].items()},
                         {"552": "hx", "553": "hx", "563": "hx", "565": "rate", "566": "hx"})
        self.assertEqual(pl["per"]["565"]["new_rate"], 0.09)
        self.assertEqual(pl["tails"], {})

    def test_build_yfd_matches_agreed_voucher(self):
        vs = vouchers()
        pl = V.plan(vs, INV)
        ls = V.build(CTX, vs, INV, pl)
        dr, cr = V.balance(ls)
        self.assertEqual((dr, cr), (93909.21, 93909.21))            # 用户 10-01 定稿那张：借 93,909.21 = 贷 93,909.21
        blocks = [l["block"] for l in ls]
        self.assertEqual(blocks, sorted(blocks, key=["红冲", "更正", "核销", "支付"].index))   # 顺序 红冲→更正→核销→支付
        red = [l for l in ls if l["block"] == "红冲"]
        self.assertEqual([(l["acct"], l["dr"], l["cr"]) for l in red], [("5101", -283.02, 0), ("2221.01.07", -16.98, 0), ("2241.02", 0, -300)])
        self.assertTrue(red[0]["expl"].startswith("红冲8/565#计提武汉易风达"))
        self.assertEqual(red[0]["dims"]["fee"], "搬运费")
        fix = [l for l in ls if l["block"] == "更正"]
        self.assertEqual([(l["acct"], l["dr"], l["cr"]) for l in fix], [("5101", 275.23, 0), ("2221.01.07", 24.77, 0), ("2241.02", 0, 300)])
        hx = [l for l in ls if l["block"] == "核销"]
        dz = [l for l in hx if l["acct"] == "2221.01.06"]
        self.assertEqual([l["dr"] for l in dz], [73.70, 566.66, 5746.79, 354.06])
        self.assertTrue(dz[2]["expl"].startswith("26422000003235839961核销8/552#、8/553#、8/563#、9/□#、8/566#计提武汉易风达冷链物流有限公司8月线下"))
        self.assertEqual(sorted(l["cr"] for l in hx if l["acct"] == "2221.01.07"), sorted([33.03, 354.06, 640.36, 24.77, 5688.99]))
        pay = [l for l in ls if l["block"] == "支付"]
        self.assertEqual([(l["acct"], l["dr"], l["cr"]) for l in pay], [("2241.02", 87168, 0), ("1002", 0, 87168)])
        self.assertEqual(pay[0]["expl"], "陈慧娴提起支付武汉易风达冷链物流有限公司8月线下孝感工厂入库运费、小料出库-装卸费运费、仓储费、设备转移费、小料出库运费")
        self.assertEqual(pay[1]["dims"]["bank"], "宁波银行86041110000117736")

    def test_all_match_only_hx_and_pay(self):
        vs = vouchers()[:3]
        inv = [{"number": "1", "rate": "9%", "gross": 400, "tax": 33.03}, {"number": "2", "rate": "6%", "gross": 6255, "tax": 354.06},
               {"number": "3", "rate": "6%", "gross": 11313, "tax": 640.36}]
        pl = V.plan(vs, inv)
        ls = V.build({**CTX, "pay_amount": 17968}, vs, inv, pl)
        self.assertEqual({l["block"] for l in ls}, {"核销", "支付"})
        self.assertEqual(V.balance(ls)[0], V.balance(ls)[1])

    def test_tail_rounding(self):
        vs = [acc("555", "计提%s7月线下仓储费" % SUP, "6601", "销售费用", 11024.42, 661.47, 11685.89, D6601_CC)]
        inv = [{"number": "9", "rate": "6%", "gross": 11685.89, "tax": 661.48}]
        pl = V.plan(vs, inv)
        self.assertEqual(pl["status"], "ok")
        self.assertEqual(pl["tails"], {"555": 0.01})
        self.assertEqual(pl["per"]["555"]["mode"], "tail")
        ls = V.build(CTX, vs, inv, pl)
        self.assertEqual(V.balance(ls)[0], V.balance(ls)[1])
        red = [(l["acct"], l["dr"], l["cr"]) for l in ls if l["block"] == "红冲"]
        fix = [(l["acct"], l["dr"], l["cr"]) for l in ls if l["block"] == "更正"]
        self.assertEqual(red, [("6601", -11024.42, 0), ("2221.01.07", -661.47, 0), ("2241.02", 0, -11685.89)])
        self.assertEqual(fix, [("6601", 11024.41, 0), ("2221.01.07", 661.48, 0), ("2241.02", 0, 11685.89)])
        hx = [l for l in ls if l["block"] == "核销" and l["acct"] == "2221.01.07"]
        self.assertEqual([l["cr"] for l in hx], [661.48])
        self.assertIn("9/□#", hx[0]["expl"])

    def test_amount_mismatch_is_manual(self):
        pl = V.plan(vouchers(), INV[:3])
        self.assertEqual(pl["status"], "manual")

    def test_ref_cross_year(self):
        v = V.acc_voucher("424", [], 2025, 12)
        self.assertEqual(V.ref_of(v, 2026), "2025-12/424#")

    def test_plain_invoice_not_deductible(self):
        # 恒茂 8 月孝感：计提按 0% 全额进费用；发票是普票 1%(不能抵扣，调用方传 rate 0 / tax 0 / deduct False) → 只核销，不红冲更正
        e = "计提孝感市恒茂食品有限责任公司8月线下仓储费"
        v = V.acc_voucher("564", [{"acct": "6601", "acct_name": "销售费用", "dr": 78733.53, "cr": 0, "expl": e, **D6601_CC},
                                  {"acct": "2241.02", "acct_name": "供应商往来", "dr": 0, "cr": 78733.53, "expl": e, **SC}], 2026, 8)
        inv = [{"number": "26422000003323832436", "rate": 0, "gross": 78733.53, "tax": 0.0, "deduct": False}]
        pl = V.plan([v], inv, {})
        self.assertEqual(pl["status"], "ok")
        self.assertEqual(pl["per"]["564"]["mode"], "hx")
        ctx = dict(CTX, supplier="孝感市恒茂食品有限责任公司", pay_amount=78733.53)
        ls = V.build(ctx, [v], inv, pl)
        self.assertEqual([l["block"] for l in ls], ["支付", "支付"])
        self.assertIn("普票26422000003323832436", ls[0]["expl"])
        # 计提错分了税(1%)：要红冲后按 0% 更正
        v2 = V.acc_voucher("564", [{"acct": "6601", "acct_name": "销售费用", "dr": 77953.99, "cr": 0, "expl": e, **D6601_CC},
                                   {"acct": "2221.01.07", "acct_name": "暂估进项税", "dr": 779.54, "cr": 0, "expl": e, **SC},
                                   {"acct": "2241.02", "acct_name": "供应商往来", "dr": 0, "cr": 78733.53, "expl": e, **SC}], 2026, 8)
        pl2 = V.plan([v2], inv, {})
        self.assertEqual(pl2["per"]["564"]["mode"], "rate")
        ls2 = V.build(ctx, [v2], inv, pl2)
        self.assertFalse([l for l in ls2 if l["acct"] == "2221.01.06"])
        self.assertFalse([l for l in ls2 if not l["dr"] and not l["cr"]])
        self.assertEqual(V.balance(ls2)[0], V.balance(ls2)[1])

    def test_move_subject(self):
        """V2.798 主体更正(丰源 8 月：深圳星期九付 918.93，计提记在深圳星期零 记-390)：
        本张不红冲，更正段在本主体补提(部门换成本主体的)，核销引用本凭证号；原主体的红冲分录另出。"""
        sup = "湖北丰源物流供应链管理有限公司"
        sc = {"sup_code": "物流运输服务055", "sup_name": sup, "sup_grp": "供应商009"}
        e = "计提%s8月线下小料出库运费" % sup
        d0 = {"dept_code": "0011401", "dept": "永续物流中心", "fee_code": "FYXM008.002其他物流费用002", "fee": "出库运费", "biz_code": "CPFL009", "biz": "小料"}
        v = V.acc_voucher("390", [{"acct": "6601", "acct_name": "销售费用", "dr": 843.06, "cr": 0, "expl": e, **d0},
                                  {"acct": "2221.01.07", "acct_name": "暂估进项税", "dr": 75.87, "cr": 0, "expl": e, **sc},
                                  {"acct": "2241.02", "acct_name": "供应商往来", "dr": 0, "cr": 918.93, "expl": e, **sc}], 2026, 8)
        v = dict(v, exp_orig=v["exp_lines"], exp_lines=[dict(v["exp_lines"][0], dept_code="0010401", dept="永续供应中心")])
        v["from"] = {"short": "深圳星期零", "full": "深圳市星期零食品科技有限公司"}
        inv = [{"number": "26422000003380468116", "rate": "9%", "gross": 918.93, "tax": 75.87}]
        ctx = {"supplier": sup, "applicant": "陈慧娴", "pay_year": 2026, "pay_month": 9, "pay_amount": 918.93, "bank": "x", "paid": True, "self_vno": "88"}
        pl = V.plan([v], inv, {"390": [{"to_rate": "6%"}]})      # 别的主体同号凭证的更正不能套到这张上
        self.assertEqual(pl["status"], "ok")
        self.assertEqual(pl["per"]["390"]["mode"], "move")
        ls = V.build(ctx, [v], inv, pl)
        self.assertFalse([l for l in ls if l["block"] == "红冲"])
        fx = [l for l in ls if l["block"] == "更正"]
        self.assertEqual([(l["acct"], l["dr"], l["cr"]) for l in fx], [("6601", 843.06, 0), ("2221.01.07", 75.87, 0), ("2241.02", 0, 918.93)])
        self.assertEqual(fx[0]["dims"]["dept_code"], "0010401")
        self.assertTrue(fx[0]["expl"].startswith("更正深圳星期零8/390#计提"))
        hx = [l for l in ls if l["block"] == "核销"]
        self.assertIn("核销9/88#计提", hx[0]["expl"])
        self.assertEqual(V.balance(ls)[0], V.balance(ls)[1])
        red = V.red_lines(v, 2026)
        self.assertEqual([(l["acct"], l["dr"], l["cr"]) for l in red], [("6601", -843.06, 0), ("2221.01.07", -75.87, 0), ("2241.02", 0, -918.93)])
        self.assertEqual(red[0]["dims"]["dept_code"], "0011401")       # 红冲用原主体自己的部门
        # 主体更正的同时税率也不对(计提 6%、发票 9%)：补提直接按发票税率
        v6 = V.acc_voucher("391", [{"acct": "6601", "acct_name": "销售费用", "dr": 866.92, "cr": 0, "expl": e, **d0},
                                   {"acct": "2221.01.07", "acct_name": "暂估进项税", "dr": 52.01, "cr": 0, "expl": e, **sc},
                                   {"acct": "2241.02", "acct_name": "供应商往来", "dr": 0, "cr": 918.93, "expl": e, **sc}], 2026, 8)
        v6["from"] = {"short": "深圳星期零", "full": "x"}
        pl6 = V.plan([v6], inv, {})
        self.assertEqual((pl6["status"], pl6["per"]["391"]["mode"], pl6["per"]["391"]["new_rate"]), ("ok", "move", 0.09))
        ls6 = V.build(ctx, [v6], inv, pl6)
        self.assertEqual([l["dr"] for l in ls6 if l["block"] == "更正" and l["acct"] == "2221.01.07"], [75.87])
        self.assertEqual(V.balance(ls6)[0], V.balance(ls6)[1])

    def test_amount_diff(self):
        """V2.818 金额有差：默认红冲、按应为金额更正；特殊的部分核销(摘要写说明)。样本＝迅鸽 8 月 记-393 计提 29,792.38、发票 29,746.39。"""
        sup = "武汉顺鸽科技有限公司"
        sc = {"sup_code": "物流运输服务075", "sup_name": sup, "sup_grp": "供应商009"}
        e = "计提%s8月线上零售出库运费" % sup
        d0 = {"dept_code": "0011401", "dept": "永续物流中心", "fee_code": "FYXM008.002其他物流费用002", "fee": "出库运费", "biz_code": "CPFL003", "biz": "电商"}
        mk = lambda: V.acc_voucher("393", [{"acct": "6601", "acct_name": "销售费用", "dr": 28106.02, "cr": 0, "expl": e, **d0},
                                            {"acct": "2221.01.07", "acct_name": "暂估进项税", "dr": 1686.36, "cr": 0, "expl": e, **sc},
                                            {"acct": "2241.02", "acct_name": "供应商往来", "dr": 0, "cr": 29792.38, "expl": e, **sc}], 2026, 8)
        inv = [{"number": "26422000003391316191", "rate": "6%", "gross": 29746.39, "tax": 1683.76}]
        ctx = {"supplier": sup, "applicant": "陈慧娴", "pay_year": 2026, "pay_month": 10, "pay_amount": 29746.39, "bank": "x", "paid": True, "self_vno": "77"}
        # 不给应为金额：还是判人工
        self.assertEqual(V.plan([mk()], inv, {})["status"], "manual")
        # ① 红冲更正到发票金额
        v = mk()
        pl = V.plan([v], inv, {}, {"393": {"gross": 29746.39}})
        self.assertEqual((pl["status"], pl["per"]["393"]["mode"]), ("ok", "amt"))
        ls = V.build(ctx, [v], inv, pl)
        self.assertEqual([(l["acct"], l["dr"], l["cr"]) for l in ls if l["block"] == "红冲"],
                         [("6601", -28106.02, 0), ("2221.01.07", -1686.36, 0), ("2241.02", 0, -29792.38)])
        fx = [(l["acct"], l["dr"], l["cr"]) for l in ls if l["block"] == "更正"]
        self.assertEqual(fx, [("6601", 28062.63, 0), ("2221.01.07", 1683.76, 0), ("2241.02", 0, 29746.39)])
        self.assertEqual([l["cr"] for l in ls if l["block"] == "核销" and l["acct"] == "2221.01.07"], [1683.76])
        self.assertIn("核销10/77#计提", [l for l in ls if l["block"] == "核销"][0]["expl"])
        self.assertEqual(V.balance(ls)[0], V.balance(ls)[1])
        # ② 部分核销：不红冲不更正，只转出本次那部分暂估税，摘要写明
        v = mk()
        pl = V.plan([v], inv, {}, {"393": {"gross": 29746.39, "part": True, "memo": "余款对方下月开票"}})
        self.assertEqual((pl["status"], pl["per"]["393"]["mode"]), ("ok", "part"))
        ls = V.build(ctx, [v], inv, pl)
        self.assertFalse([l for l in ls if l["block"] in ("红冲", "更正")])
        hx = [l for l in ls if l["block"] == "核销"]
        self.assertEqual([(l["acct"], l["dr"], l["cr"]) for l in hx], [("2221.01.06", 1683.76, 0), ("2221.01.07", 0, 1683.76)])
        self.assertIn("核销8/393#（部分）计提", hx[1]["expl"])
        self.assertIn("部分核销：计提29792.38，本次核销29746.39，余45.99未核销；余款对方下月开票", hx[1]["expl"])
        self.assertEqual([(l["acct"], l["dr"], l["cr"]) for l in ls if l["block"] == "支付"], [("2241.02", 29746.39, 0), ("1002", 0, 29746.39)])
        self.assertEqual(V.balance(ls)[0], V.balance(ls)[1])
        # ③ 部分核销不能比计提多
        self.assertEqual(V.plan([mk()], [dict(inv[0], gross=30000, tax=1698.11)], {}, {"393": {"gross": 30000, "part": True}})["status"], "manual")
        # ④ 发票比计提多：红冲更正照样行
        v = mk()
        inv2 = [dict(inv[0], gross=30000.00, tax=1698.11)]
        pl = V.plan([v], inv2, {}, {"393": {"gross": 30000.00}})
        ls = V.build(dict(ctx, pay_amount=30000.00), [v], inv2, pl)
        self.assertEqual([l["cr"] for l in ls if l["block"] == "更正" and l["acct"] == "2241.02"], [30000.00])
        self.assertEqual(V.balance(ls)[0], V.balance(ls)[1])
        # ⑤ 复核台登记的金额更正(to_amt_tax)：plan 现在按更正后的金额比
        v = mk()
        fixes = {"393": [{"to_amt_tax": "29746.39", "snap": {"amt": 29792.38}}]}
        pl = V.plan([v], inv, fixes)
        self.assertEqual((pl["status"], pl["per"]["393"]["mode"]), ("ok", "fix"))
        ls = V.build(ctx, [v], inv, pl, fixes)
        self.assertEqual([l["cr"] for l in ls if l["block"] == "更正" and l["acct"] == "2241.02"], [29746.39])
        self.assertEqual(V.balance(ls)[0], V.balance(ls)[1])

    def test_invoice_later(self):
        """V2.832 发票后补(禾享 9月记-196：直接做账的费用凭证，税挂暂估 13.09)：付款时没票只出支付两行；发票到了单独出暂估转待认证。"""
        sup = "深圳市禾享国际物流有限公司"
        sc = {"sup_code": "物流运输服务057", "sup_name": sup, "sup_grp": "供应商009"}
        e = "陈慧娴提起支付%s新加坡样品运费" % sup
        d0 = {"dept_code": "0011401", "dept": "永续物流中心", "fee_code": "FYXM008.001", "fee": "快递费", "biz_code": "CPFL000", "biz": "综合产品"}
        v = V.acc_voucher("196", [{"acct": "6601", "acct_name": "销售费用", "dr": 1308.91, "cr": 0, "expl": e, **d0},
                                  {"acct": "2221.01.07", "acct_name": "暂估进项税", "dr": 13.09, "cr": 0, "expl": e, **sc},
                                  {"acct": "2241.02", "acct_name": "供应商往来", "dr": 0, "cr": 1322.0, "expl": e, **sc}], 2026, 9)
        v["direct"] = True
        self.assertEqual(v["tax06"], 0)
        ctx = {"supplier": sup, "applicant": "陈慧娴", "pay_year": 2026, "pay_month": 9, "pay_amount": 1322.0, "bank": "x", "paid": True, "self_vno": "300",
               "tax_later": True}
        pl = {"status": "ok", "msgs": [], "per": {"196": {"mode": "hx", "new_rate": v["rate"], "gross": 1322.0}}, "tails": {}}
        ls = V.build(ctx, [v], [], pl)
        self.assertEqual([(l["block"], l["acct"], l["dr"], l["cr"]) for l in ls], [("支付", "2241.02", 1322.0, 0), ("支付", "1002", 0, 1322.0)])
        self.assertEqual(ls[0]["expl"], "核销9/196#" + e)
        inv = [{"number": "26952000004000000001", "rate": "1%", "gross": 1322.0, "tax": 13.09}]
        l3, d = V.later_lines([v], inv, 2026, sup)
        self.assertEqual(d, 0)
        self.assertEqual([(l["acct"], l["dr"], l["cr"]) for l in l3], [("2221.01.06", 13.09, 0), ("2221.01.07", 0, 13.09)])
        self.assertEqual(l3[0]["expl"], "26952000004000000001核销9/196#" + e)
        self.assertEqual(l3[1]["expl"], "核销9/196#" + e)
        self.assertEqual(l3[1]["dims"]["sup_code"], "物流运输服务057")
        # 发票税额比暂估多 0.01：尾差调到费用行(借方负数)，借贷仍平
        l3b, d = V.later_lines([v], [dict(inv[0], tax=13.10)], 2026, sup)
        self.assertEqual(d, 0.01)
        self.assertEqual([(l["acct"], l["dr"], l["cr"]) for l in l3b], [("2221.01.06", 13.10, 0), ("2221.01.07", 0, 13.09), ("6601", -0.01, 0)])
        self.assertEqual(V.balance(l3b)[0], V.balance(l3b)[1])
        # 有票的时候(没后补)：直接做账的凭证照常在付款凭证里核销，摘要用「核销M/N#＋原摘要」
        pl2 = V.plan([v], inv, {})
        ls2 = V.build(dict(ctx, tax_later=False), [v], inv, pl2)
        self.assertEqual([(l["block"], l["acct"]) for l in ls2], [("核销", "2221.01.06"), ("核销", "2221.01.07"), ("支付", "2241.02"), ("支付", "1002")])
        self.assertEqual(ls2[0]["expl"], "26952000004000000001核销9/196#" + e)
        self.assertEqual(V.balance(ls2)[0], V.balance(ls2)[1])
    def test_invoice_later_with_diff(self):
        """V2.929 发票后补、计提和请款又有差(天鹰：复核完、付了款才开票)：付款凭证里照样红冲更正(税按原税率、仍挂暂估)，不出核销；
        到票时拿「付款凭证里更正后的样子」和发票比——对得上只核销，对不上再整笔红冲、更正、核销(冲的是付款凭证里的更正分录)。"""
        sup = "湖北天鹰物流有限公司"
        sc = {"sup_code": "物流运输服务044", "sup_name": sup, "sup_grp": "供应商009"}
        d1 = {"dept_code": "0030301", "dept": "仓储物流部", "fee_code": "FYXM002.002运费成本002", "fee": "入库运费"}
        d2 = {"dept_code": "0030902", "dept": "茶饮小料部", "fee_code": "FYXM002.002运费成本002", "fee": "入库运费"}

        def mk(vno, item, net, tax, gross, dims):
            e = "计提%s8月线下%s" % (sup, item)
            return V.acc_voucher(vno, [{"acct": "5101", "acct_name": "制造费用", "dr": net, "cr": 0, "expl": e, **dims},
                                       {"acct": "2221.01.07", "acct_name": "暂估进项税", "dr": tax, "cr": 0, "expl": e, **sc},
                                       {"acct": "2241.02", "acct_name": "供应商往来", "dr": 0, "cr": gross, "expl": e, **sc}], 2026, 8)
        vs = lambda: [mk("559", "孝感工厂入库运费", 2682.12, 160.93, 2843.05, d1), mk("560", "小料入库运费", 38064.22, 2283.85, 40348.07, d2)]
        ctx = {"supplier": sup, "applicant": "陈慧娴", "pay_year": 2026, "pay_month": 9, "pay_amount": 42441.29, "bank": "x", "paid": True, "self_vno": "264",
               "tax_later": True}
        # 没差的：和原来一样，全是核销(只做支付)
        pl0 = V.plan_later(vs())
        self.assertEqual((pl0["status"], {k: p["mode"] for k, p in pl0["per"].items()}, pl0["tails"]), ("ok", {"559": "hx", "560": "hx"}, {}))
        self.assertEqual([l["block"] for l in V.build(dict(ctx, pay_amount=43191.12), vs(), [], pl0)], ["支付", "支付"])
        # 有差：人给了每张「本次按多少」→ 两张都红冲更正，税按 6% 重算挂暂估，不出核销
        amts = {"559": {"gross": 833.90}, "560": {"gross": 41607.39}}
        v = vs()
        pl = V.plan_later(v, {}, amts)
        self.assertEqual((pl["status"], {k: p["mode"] for k, p in pl["per"].items()}, pl["tails"]), ("ok", {"559": "amt", "560": "amt"}, {}))
        ls = V.build(ctx, v, [], pl)
        self.assertEqual([b for b in dict.fromkeys(l["block"] for l in ls)], ["红冲", "更正", "支付"])
        self.assertEqual([(l["acct"], l["dr"], l["cr"]) for l in ls if l["block"] == "更正"],
                         [("5101", 786.70, 0), ("2221.01.07", 47.20, 0), ("2241.02", 0, 833.90), ("5101", 39252.25, 0), ("2221.01.07", 2355.14, 0), ("2241.02", 0, 41607.39)])
        self.assertEqual([(l["acct"], l["dr"], l["cr"]) for l in ls if l["block"] == "支付"], [("2241.02", 42441.29, 0), ("1002", 0, 42441.29)])
        self.assertEqual(V.balance(ls)[0], V.balance(ls)[1])
        # 复核台登记的金额更正也一样认
        fixes = {"559": [{"to_amt_tax": "833.90", "snap": {"amt": 2843.05}}], "560": [{"to_amt_tax": "41607.39", "snap": {"amt": 40348.07}}]}
        plf = V.plan_later(vs(), fixes)
        self.assertEqual((plf["status"], round(sum(p["gross"] for p in plf["per"].values()), 2)), ("ok", 42441.29))
        # 部分核销在没票时不做
        self.assertEqual(V.plan_later(vs(), {}, {"559": {"gross": 833.90, "part": True}})["status"], "manual")
        # —— 到票 ——账上现在是付款凭证 9/264# 里更正后的样子
        new = {"559": {"gross": 833.90, "tax": 47.20, "exp": [dict(d1, acct="5101", acct_name="制造费用", dr=786.70)]},
               "560": {"gross": 41607.39, "tax": 2355.14, "exp": [dict(d2, acct="5101", acct_name="制造费用", dr=39252.25)]}}
        eff = lambda: [V.renewed(x, new[x["vno"]], "9/264#") for x in vs()]
        e0 = eff()
        self.assertEqual([(x["gross"], x["tax"], x["rate"], V.ref_of(x, 2026)) for x in e0], [(833.90, 47.20, 0.06, "9/264#"), (41607.39, 2355.14, 0.06, "9/264#")])
        self.assertEqual(e0[1]["exp_lines"][0]["dept"], "茶饮小料部")
        ctx3 = {"supplier": sup, "applicant": "陈慧娴", "pay_year": 2026, "pay_month": 10, "pay_amount": 0, "bank": "", "paid": False}
        # ① 发票和账上一致：只核销
        inv = [{"number": "26422000009990000001", "rate": "6%", "gross": 42441.29, "tax": 2402.34}]
        p3 = V.plan(e0, inv, {})
        l3 = V.build(ctx3, e0, inv, p3)
        self.assertEqual([(l["block"], l["acct"], l["dr"], l["cr"]) for l in l3],
                         [("核销", "2221.01.06", 2402.34, 0), ("核销", "2221.01.07", 0, 47.20), ("核销", "2221.01.07", 0, 2355.14)])
        self.assertTrue(l3[0]["expl"].startswith("26422000009990000001核销9/264#计提"))
        self.assertEqual(l3[0]["expl"].count("9/264#"), 1)
        # ② 发票税额多 0.01：整笔红冲(冲付款凭证里那几行更正)、更正、再核销，核销引用本凭证
        e1 = eff()
        inv1 = [dict(inv[0], tax=2402.35)]
        p3 = V.plan(e1, inv1, {})
        self.assertEqual((p3["status"], p3["per"]["560"]["mode"], p3["per"]["559"]["mode"]), ("ok", "tail", "hx"))
        l3 = V.build(ctx3, e1, inv1, p3)
        red = [l for l in l3 if l["block"] == "红冲"]
        self.assertEqual([(l["acct"], l["dr"], l["cr"]) for l in red], [("5101", -39252.25, 0), ("2221.01.07", -2355.14, 0), ("2241.02", 0, -41607.39)])
        self.assertTrue(all(l["expl"].startswith("红冲9/264#计提") for l in red))
        self.assertEqual([(l["acct"], l["dr"], l["cr"]) for l in l3 if l["block"] == "更正"], [("5101", 39252.24, 0), ("2221.01.07", 2355.15, 0), ("2241.02", 0, 41607.39)])
        hx = [l for l in l3 if l["block"] == "核销"]
        self.assertEqual([(l["acct"], l["dr"], l["cr"]) for l in hx], [("2221.01.06", 2402.35, 0), ("2221.01.07", 0, 47.20), ("2221.01.07", 0, 2355.15)])
        self.assertIn("核销9/264#、10/□#计提", hx[0]["expl"])
        self.assertEqual(V.balance(l3)[0], V.balance(l3)[1])
        # ③ 发票金额和付款不一样(多开 100)：只有一张时按发票金额红冲更正，往来会留 100
        one = [V.renewed(vs()[0], new["559"], "9/264#")]
        inv2 = [{"number": "N2", "rate": "6%", "gross": 933.90, "tax": 52.86}]
        p3 = V.plan(one, inv2, {}, {"559": {"gross": 933.90}})
        l3 = V.build(ctx3, one, inv2, p3)
        self.assertEqual(p3["per"]["559"]["mode"], "amt")
        self.assertEqual(round(sum(l["cr"] for l in l3 if l["acct"] == "2241.02"), 2), 100.00)
        self.assertEqual(V.balance(l3)[0], V.balance(l3)[1])

    def test_fix_split_part(self):
        """V2.855 只改其中一部分：丰源 深圳星期零 8 月记-389 出库运费·零售 4,117.46，其中调拨的 1,450 应是电商——
        红冲整笔后重提成两行：零售 2,447.21(含税 2,667.46) + 电商 1,330.28(含税 1,450)，税额、应付不变。"""
        sup = "湖北丰源物流供应链管理有限公司"
        sc = {"sup_code": "物流运输服务055", "sup_name": sup}
        d = {"dept_code": "0011401", "dept": "永续物流中心", "fee_code": "FYXM008.002其他物流费用002", "fee": "出库运费", "biz_code": "CPFL011", "biz": "零售"}
        e = "计提%s8月线下零售出库运费" % sup
        v = V.acc_voucher("389", [{"acct": "6601", "acct_name": "销售费用", "dr": 3777.49, "cr": 0, "expl": e, **d},
                                  {"acct": "2221.01.07", "acct_name": "暂估进项税", "dr": 339.97, "cr": 0, "expl": e, **sc},
                                  {"acct": "2241.02", "acct_name": "供应商往来", "dr": 0, "cr": 4117.46, "expl": e, **sc}], 2026, 8)
        inv = [{"number": "26422000003380174971", "rate": "9%", "gross": 4117.46, "tax": 339.97}]
        fx = {"389": [{"to_acct": "", "to_fee": "", "to_dept": "", "to_biz": "CPFL002 电商", "to_proj": "", "to_amt_tax": "", "to_rate": "", "to_amt": "", "memo": "调拨到电商仓",
                       "split_amt": "1450.00", "snap": {"acct": "6601", "fee_code": d["fee_code"], "dept_code": d["dept_code"], "biz_code": "CPFL011", "amt": 4117.46}}]}
        pl = V.plan([v], inv, fx)
        self.assertEqual(pl["per"]["389"]["mode"], "fix")
        ctx = dict(CTX, supplier=sup, pay_amount=4117.46)
        ls = V.build(ctx, [v], inv, pl, fx)
        fixl = [l for l in ls if l["block"] == "更正" and l["acct"] == "6601"]
        self.assertEqual([(l["dims"].get("biz"), l["dr"]) for l in fixl], [("零售", 2447.21), ("电商", 1330.28)])
        self.assertTrue(all(l["dims"].get("dept_code") == "0011401" and l["dims"].get("fee") == "出库运费" for l in fixl))
        self.assertEqual(round(sum(l["dr"] for l in ls if l["block"] == "更正"), 2), 4117.46)
        self.assertEqual(round(sum(l["dr"] - l["cr"] for l in ls), 2), 0)
        changed_pl = {"status": "ok", "per": {"389": dict(pl["per"]["389"], gross=41607.40)}, "tails": {}}
        changed = V.build(ctx, [v], inv, changed_pl, fx)
        self.assertEqual([(l["dims"].get("biz"), l["dr"]) for l in changed if l["block"] == "更正" and l["acct"] == "6601"],
                         [("零售", 36841.65), ("电商", 1330.28)], "更正总额后，含税拆分额不能再次按新净额比例放大")
        changed_pl6 = {"status": "ok", "per": {"389": dict(pl["per"]["389"], gross=18222.50, new_rate=0.06)}, "tails": {}}
        changed6 = V.build(ctx, [v], inv, changed_pl6, fx)
        self.assertEqual([(l["dims"].get("biz"), l["dr"]) for l in changed6 if l["block"] == "更正" and l["acct"] == "6601"],
                         [("零售", 15823.12), ("电商", 1367.92)])
        self.assertEqual([l["dr"] for l in changed6 if l["block"] == "更正" and l["acct"] == "2221.01.07"], [1031.46])
        # 不填 / 填得不小于整笔 → 还是整笔改
        fx2 = {"389": [dict(fx["389"][0], split_amt="4117.46")]}
        ls2 = V.build(ctx, [v], inv, V.plan([v], inv, fx2), fx2)
        self.assertEqual([(l["dims"].get("biz"), l["dr"]) for l in ls2 if l["block"] == "更正" and l["acct"] == "6601"], [("电商", 3777.49)])

    def test_fix_split_clear_dim_and_invoice_rate(self):
        """V2.869 恒茂 孝感星期九 8 月记-564：78,733.53 整笔记在 6601 货物仓储费·小料、计提没拆税，发票 1%。
        其中入库处置＋冷藏 78,442.53 因工厂爆仓改记 5101 入库运费·茶饮小料部、产品分类清空；出库装卸 291 留原处。
        要点：①「（清空）」是把维度拿掉，不是当编码；②登记了更正、没另填税率的，税率也跟发票(0%→1%)；③拆成两行后税额、应付不变。"""
        sup = "孝感市恒茂食品有限责任公司"
        sc = {"sup_code": "物流运输服务082", "sup_name": sup}
        d = {"dept_code": "0030301", "dept": "仓储物流部", "fee_code": "FYXM008.002其他物流费用001", "fee": "货物仓储费", "biz_code": "CPFL009", "biz": "小料"}
        e = "计提%s8月线下仓储费" % sup
        v = V.acc_voucher("564", [{"acct": "6601", "acct_name": "销售费用", "dr": 78733.53, "cr": 0, "expl": e, **d},
                                  {"acct": "2241.02", "acct_name": "供应商往来", "dr": 0, "cr": 78733.53, "expl": e, **sc}], 2026, 8)
        inv = [{"number": "26422000003323832436", "rate": "1%", "gross": 78733.53, "tax": 779.54}]
        fx = {"564": [{"to_acct": "5101 制造费用", "to_fee": "FYXM002.002运费成本002 入库运费", "to_dept": "0030902 茶饮小料部", "to_biz": "（清空）", "to_proj": "",
                       "to_amt_tax": "", "to_rate": "", "to_amt": "", "memo": "工厂爆仓", "split_amt": "78442.53",
                       "snap": {"acct": "6601", "fee_code": d["fee_code"], "dept_code": d["dept_code"], "biz_code": "CPFL009", "amt": 78733.53}}]}
        pl = V.plan([v], inv, fx)
        self.assertEqual(pl["status"], "ok", pl["msgs"])
        self.assertEqual((pl["per"]["564"]["mode"], pl["per"]["564"]["new_rate"]), ("fix", 0.01))
        ls = V.build(dict(CTX, supplier=sup, pay_amount=78733.53), [v], inv, pl, fx)
        fixl = [l for l in ls if l["block"] == "更正" and l["acct"][:1] in ("5", "6")]
        self.assertEqual([(l["acct"], l["dims"].get("fee"), l["dims"].get("dept"), l["dims"].get("biz_code"), l["dr"]) for l in fixl],
                         [("6601", "货物仓储费", "仓储物流部", "CPFL009", 288.12), ("5101", "入库运费", "茶饮小料部", None, 77665.87)])
        self.assertEqual([l["dr"] for l in ls if l["block"] == "更正" and l["acct"] == "2221.01.07"], [779.54])
        self.assertEqual(round(sum(l["dr"] - l["cr"] for l in ls), 2), 0)
        # 自己填了税率的更正，不被发票税率覆盖
        fx3 = {"564": [dict(fx["564"][0], split_amt="", to_rate="0.0600")]}
        self.assertEqual(V.plan([v], inv, fx3)["status"], "manual")


    def test_carry_overcharge_and_settle(self):
        """V2.896 供应商多收、等后面月份账单扣回：多收月按正确金额重提(费用、暂估税都按正确金额)，核销按发票税额、支付按实付；
        扣回月反过来。两个月合起来 供应商往来、暂估进项税 都归零。合成数据。"""
        # 科目余额(借－贷)＝原计提凭证的分录 ＋ 付款凭证的分录
        bal = lambda ls, acct, *orig: V.r2(sum(l["dr"] - l["cr"] for l in ls if l["acct"] == acct) + sum(l["dr"] - l["cr"] for o in orig for l in o["lines"] if l["acct"] == acct))
        # 多收月：计提 1,090(9%)，发票 1,090(税 90)，其中多收 109(含税)
        v = acc("386", "计提%s8月线下零售出库运费" % SUP, "6601", "销售费用", 1000.00, 90.00, 1090, D6601_OUT)
        inv = [{"number": "26420000000000000001", "rate": "9%", "gross": 1090.00, "tax": 90.00}]
        self.assertEqual(V.plan([v], inv)["per"]["386"]["mode"], "hx")               # 不登记多收：照旧只核销
        pl = V.plan([v], inv, carry={"386": [{"gross": -109.00, "memo": "多算 7 件"}]})
        self.assertEqual(pl["status"], "ok", pl["msgs"])
        p = pl["per"]["386"]
        self.assertEqual((p["mode"], p["gross"], p["gross0"], p["carry"]), ("amt", 981.00, 1090.00, -109.00))
        self.assertIn("多算 7 件", p["why"])
        ctx = dict(CTX, pay_amount=1090.00)
        a = V.build(ctx, [v], inv, pl)
        self.assertEqual(V.balance(a)[0], V.balance(a)[1])
        fix = [(l["acct"], l["dr"], l["cr"]) for l in a if l["block"] == "更正"]
        self.assertEqual(fix, [("6601", 900.00, 0), ("2221.01.07", 81.00, 0), ("2241.02", 0, 981.00)])        # 费用、暂估税都按正确金额
        hx = [(l["acct"], l["dr"], l["cr"]) for l in a if l["block"] == "核销"]
        self.assertEqual(hx, [("2221.01.06", 90.00, 0), ("2221.01.07", 0, 81.00), ("2221.01.07", 0, 9.00)])   # 待认证按发票税额；多认证的 9 另挂一行
        self.assertIn("供应商多收109.00元", [l for l in a if l["block"] == "核销"][-1]["expl"])
        self.assertEqual([(l["acct"], l["dr"], l["cr"]) for l in a if l["block"] == "支付"], [("2241.02", 1090.00, 0), ("1002", 0, 1090.00)])
        # 扣回月：真实费用 2,180(9%)，账单扣了 109，计提、发票都是 2,071(税 171)
        w = acc("401", "计提%s9月线下零售出库运费" % SUP, "6601", "销售费用", 1900.00, 171.00, 2071, D6601_OUT)
        w["month"] = 9
        inv2 = [{"number": "26420000000000000002", "rate": "9%", "gross": 2071.00, "tax": 171.00}]
        pl2 = V.plan([w], inv2, carry={"401": [{"gross": 109.00, "memo": "扣回 8 月多收"}]})
        self.assertEqual(pl2["status"], "ok", pl2["msgs"])
        self.assertEqual((pl2["per"]["401"]["mode"], pl2["per"]["401"]["gross"]), ("amt", 2180.00))
        b = V.build(dict(CTX, pay_amount=2071.00, pay_month=10), [w], inv2, pl2)
        self.assertEqual(V.balance(b)[0], V.balance(b)[1])
        self.assertEqual([(l["acct"], l["dr"], l["cr"]) for l in b if l["block"] == "更正"], [("6601", 2000.00, 0), ("2221.01.07", 180.00, 0), ("2241.02", 0, 2180.00)])
        self.assertEqual([(l["acct"], l["dr"], l["cr"]) for l in b if l["block"] == "核销"], [("2221.01.06", 171.00, 0), ("2221.01.07", 0, 180.00), ("2221.01.07", 0, -9.00)])
        # 两个月合起来：往来、暂估税、待认证之外不留尾巴；费用＝真实数(900＋2,000)
        both = a + b
        self.assertEqual(bal(both, "2241.02", v, w), 0.0)
        self.assertEqual(bal(both, "2221.01.07", v, w), 0.0)
        self.assertEqual(bal(both, "6601", v, w), 2900.00)
        self.assertEqual(bal(a, "6601", v), 900.00)              # 多收月费用＝正确金额
        self.assertEqual(bal(a, "2241.02", v), 109.00)           # 多收月底：供应商欠我们 109(含税)，挂在往来借方
        self.assertEqual(bal(a, "2221.01.07", v), -9.00)         # 暂估进项税贷方 9：随票多认证的税

    def test_carry_with_other_vouchers_and_guards(self):
        """多张计提里只动登记的那张；部分核销的、减完不剩的 → 人工。"""
        vs = vouchers()
        pl = V.plan(vs, INV, carry={"566": [{"gross": -218.00}]})
        self.assertEqual(pl["status"], "ok", pl["msgs"])
        self.assertEqual({k: p["mode"] for k, p in pl["per"].items()}, {"552": "hx", "553": "hx", "563": "hx", "565": "rate", "566": "amt"})
        self.assertEqual(pl["per"]["566"]["gross"], 68682.00)
        ls = V.build(CTX, vs, INV, pl)
        self.assertEqual(V.balance(ls)[0], V.balance(ls)[1])
        ap = V.r2(sum(l["dr"] - l["cr"] for l in ls if l["acct"] == "2241.02") + sum(l["dr"] - l["cr"] for v in vs for l in v["lines"] if l["acct"] == "2241.02"))
        self.assertEqual(ap, 218.00)                             # 连原计提一起看：往来借方余 218
        self.assertEqual(V.r2(sum(l["dr"] for l in ls if l["acct"] == "2221.01.06")), V.r2(sum(i["tax"] for i in INV)))          # 待认证仍是发票税额
        v = acc("386", "计提%s8月线下零售出库运费" % SUP, "6601", "销售费用", 1000.00, 90.00, 1090, D6601_OUT)
        inv = [{"number": "26420000000000000001", "rate": "9%", "gross": 1090.00, "tax": 90.00}]
        self.assertEqual(V.plan([v], inv, carry={"386": [{"gross": -1090.00}]})["status"], "manual")
        inv_p = [{"number": "26420000000000000003", "rate": "9%", "gross": 545.00, "tax": 45.00}]
        self.assertEqual(V.plan([v], inv_p, amts={"386": {"gross": 545.00, "part": True}}, carry={"386": [{"gross": -10.00}]})["status"], "manual")
        self.assertEqual(V.plan([v], inv, carry={"999": [{"gross": -10.00}]})["per"]["386"]["mode"], "hx")     # 登记在别的凭证上的不影响这张

    def test_carry_amt_carries_mode0_for_auto_guard(self):
        """V2.993 自动做账护栏的契约：复核台登记的多收待扣回把 hx/tail 顶成 amt 时，per 里带 mode0(hx/tail)+carry——
        付款做账的自动轮据此认定「金额更正来自复核台登记＝人已确认」，不再拦；系统按发票/请款金额猜的 amt 没有这对标记，照拦。"""
        v = acc("386", "计提%s8月线下零售出库运费" % SUP, "6601", "销售费用", 1000.00, 90.00, 1090, D6601_OUT)
        inv = [{"number": "26420000000000000001", "rate": "9%", "gross": 1090.00, "tax": 90.00}]
        p = V.plan([v], inv, carry={"386": [{"gross": -109.00}]})["per"]["386"]
        self.assertEqual((p["mode"], p["mode0"]), ("amt", "hx"))
        self.assertTrue(p.get("carry"))
        # 系统猜的应为金额(amts)叠上登记的扣回：mode0 是 amt 不是 hx/tail，护栏不认作「复核台确认过」，仍要人点
        # （plan 先按 amts 对发票平账、再落扣回，所以发票按 981 开）
        inv3 = [{"number": "26420000000000000004", "rate": "9%", "gross": 981.00, "tax": 81.00}]
        p2 = V.plan([v], inv3, amts={"386": {"gross": 981.00}}, carry={"386": [{"gross": -10.00}]})["per"]["386"]
        self.assertEqual((p2["mode"], p2["mode0"]), ("amt", "amt"))
        self.assertTrue(p2.get("carry"))


class TestResume(unittest.TestCase):
    """V2.908 做到一半接着做：样本＝2026-10-10 被重启掐断的那张(顺丰冷运·深圳星期九 622.00，付款单 FKD00008357)。"""
    PAY = [{"acct": "2241.02", "dr": 622.00, "cr": 0, "sup_code": "物流运输服务011"}, {"acct": "1002.03", "dr": 0, "cr": 622.00, "sup_code": ""}]
    ADD = [{"acct": "2221.01.06", "dr": 51.36, "cr": 0}, {"acct": "2221.01.07", "dr": 0, "cr": 51.36}]

    def step(self, ents, status, add=None, sup="物流运输服务011"):
        return V.resume_step(ents, status, self.ADD if add is None else add, 622.00, sup)[0]

    def test_raw_voucher_gets_amended(self):
        """金蝶刚出的原样两行、没提交 → 补分录。暂存(Z)和创建(A)都算没提交。"""
        self.assertEqual(self.step(self.PAY, "A"), "amend")
        self.assertEqual(self.step(self.PAY, "Z"), "amend")
        self.assertEqual(self.step(self.PAY, "A", add=[]), "amend")           # 发票后补只做支付：没有要补的行，只改摘要

    def test_already_amended_is_not_amended_twice(self):
        """上次分录已经补进去了(补完没来得及记就断了)：绝不能再补一遍。"""
        self.assertEqual(self.step(self.PAY + self.ADD, "A"), "submit")       # 还没提交 → 提交、落记录
        self.assertEqual(self.step(self.PAY + self.ADD, "B"), "record")       # 也提交了 → 只落记录
        self.assertEqual(self.step(self.PAY + self.ADD, "C"), "record")       # 连审核都过了 → 只落记录
        self.assertEqual(self.step(list(reversed(self.PAY + self.ADD)), "A"), "submit")      # 和分录顺序无关

    def test_same_amount_lines_are_counted_not_just_present(self):
        """要补两行一样的(两张票同税额)，凭证里只有一行 → 不算补过。"""
        two = [dict(self.ADD[0]), dict(self.ADD[0]), {"acct": "2221.01.07", "dr": 0, "cr": 102.72}]
        self.assertEqual(self.step(self.PAY + two, "A", add=two), "submit")
        self.assertEqual(self.step(self.PAY + two[1:], "A", add=two), "stop")

    def test_stop_when_not_ours_to_touch(self):
        self.assertEqual(self.step(self.PAY, "B"), "stop")                    # 原样就被人提交了：系统不改已提交的凭证
        self.assertEqual(self.step(self.PAY, "C"), "stop")
        self.assertEqual(self.step(self.PAY + self.ADD[:1], "A"), "stop")     # 只补了一半 / 有人改过
        self.assertEqual(self.step(self.PAY + self.ADD + [{"acct": "6601", "dr": 1, "cr": 0}], "A"), "stop")     # 多了别的行
        self.assertEqual(self.step(self.PAY, "A", sup="物流运输服务027"), "stop")       # 同一天同金额、别家的付款凭证
        self.assertEqual(self.step([{"acct": "2241.02", "dr": 600.00, "cr": 0}, {"acct": "1002.03", "dr": 0, "cr": 600.00}], "A"), "stop")   # 金额不是这笔
        self.assertEqual(self.step([], "A"), "stop")
        self.assertIn("撤销提交", V.resume_step(self.PAY, "B", self.ADD, 622.00)[1])          # 告诉人下一步怎么办

    def test_pay_only_submitted_is_recorded(self):
        """发票后补只做支付、凭证已提交：内容本来就只有这两行 → 落记录，不去改已提交的凭证。"""
        self.assertEqual(self.step(self.PAY, "B", add=[]), "record")

    def test_supplier_unknown_does_not_block(self):
        """凭证分录上没带供应商(科目不挂这个维度)：没法比，不因此拦。"""
        pay = [dict(self.PAY[0], sup_code=""), self.PAY[1]]
        self.assertEqual(self.step(pay, "A"), "amend")


class CashflowSplit(unittest.TestCase):
    """V2.926 付款凭证的现金流量按计提性质拆。样本取自 2026-10-12 实际改过的那 20 张。"""

    def test_single_and_split(self):
        rows, why = V.cashflow_split([{"fee": "出库运费", "expl": "计提顺丰冷运8月线下植物肉出库运费", "gross": 17630.95}], 17630.95)
        self.assertEqual((why, [(x["name"], x["amount"]) for x in rows]), ("", [("运费", 17630.95)]))
        rows, why = V.cashflow_split([{"fee": "入库运费", "expl": "计提链盟8月线下小料入库运费", "gross": 36500},
                                      {"fee": "出库运费", "expl": "计提链盟8月线下小料出库运费", "gross": 409795}], 446295)
        self.assertEqual((why, {x["name"]: x["amount"] for x in rows}), ("", {"成本运费": 36500.0, "运费": 409795.0}))

    def test_other_kinds(self):
        one = lambda fee, expl="": V.cashflow_split([{"fee": fee, "expl": expl, "gross": 100}], 100)[0][0]["name"]
        self.assertEqual(one("货物仓储费"), "仓储费")
        self.assertEqual(one("快递费", "陈慧娴提起支付禾享新加坡样品运费"), "其他业务活动费用")
        self.assertEqual(one("出库运费", "计提顺捷8月线下研发样品出库运费"), "研发类")           # 摘要里带研发的归研发类
        self.assertEqual(one("制造费用", "计提丰源8月线下孝感工厂入库运费"), "成本运费")           # 费用项目没写、摘要写了入库

    def test_refuses_to_guess(self):
        """归不到项目的(搬运费)、合计对不上的，都不猜——返回原因，由人处理。「搬运费」里有「运费」两个字，不能被当成运费。"""
        rows, why = V.cashflow_split([{"fee": "出库运费", "gross": 75155}, {"fee": "搬运费", "expl": "计提易风达8月线下设备转移费", "gross": 300}], 75455)
        self.assertEqual(rows, [])
        self.assertIn("搬运费", why)
        rows, why = V.cashflow_split([{"fee": "出库运费", "gross": 100}], 120)
        self.assertEqual(rows, [])
        self.assertIn("对不上", why)
        self.assertEqual(V.cashflow_split([], 100)[0], [])

    def test_custom_rules(self):
        rules = [{"kw": "搬运", "expl": False, "code": "CI01.02.01.00202", "name": "成本运费"}] + V.CASHFLOW_RULES
        rows, why = V.cashflow_split([{"fee": "搬运费", "gross": 300}, {"fee": "入库运费", "gross": 400}], 700, rules)
        self.assertEqual((why, [(x["name"], x["amount"]) for x in rows]), ("", [("成本运费", 700.0)]))


class NoticeText(unittest.TestCase):
    """V2.910 自动做账做完的提醒话术。"""
    HX = {"subject": "深圳星期零", "payee": "上海顺丰冷运", "amount": 17630.95, "kind": "hx", "text": "计提与发票一致，只核销", "vno": "330", "done": True}
    FIX = {"subject": "深圳星期零", "payee": "东莞链盟", "amount": 19820, "kind": "tail", "text": "计提与发票一致，尾差 0.01：记-378 整笔红冲后更正", "vno": "331", "done": True}
    XO = {"subject": "深圳星期零", "payee": "湖北丰源", "amount": 5000, "kind": "redo", "vno": "332", "done": True,
          "text": "本主体的计提与发票一致；另红冲 记-390 918.93（记错主体，应是深圳星期九的）", "xout": [{"vno": "390", "gross": 918.93, "to": "深圳星期九"}]}
    BAD = {"subject": "深圳星期零", "payee": "路凯", "amount": 1002.09, "kind": "hx", "done": False, "msg": "付款单审核失败：已审核"}
    PEND = {"subject": "深圳星期九", "payee": "湖北丰源", "amount": 918.93, "why": "计提记错主体：要在别的账簿建红冲凭证，留给人单张做",
            "text": "计提记在了「深圳星期零」的账上（记-390 918.93），主体记错：本张凭证里补提到深圳星期九再核销"}

    def test_all_plain(self):
        s, st = V.notice_text("2026-10-11 14:20", [self.HX])
        self.assertEqual(st, {"ok": 1, "bad": 0, "fix": 0, "multi": 0, "pending": 0, "later": 0, "later3": 0})
        self.assertIn("刚做了 1 张付款凭证，请到金蝶审核", s)
        self.assertIn("深圳星期零｜上海顺丰冷运｜17,630.95　记-330", s)
        self.assertNotIn("计提更正单", s)
        self.assertNotIn("两个主体", s)

    def test_fix_only_reminds_not_print(self):
        """有红冲更正：只提醒装订时附更正单(复核时打印的)，不写去哪打印。"""
        s, st = V.notice_text("2026-10-11 14:20", [self.HX, self.FIX])
        self.assertEqual((st["fix"], st["multi"]), (1, 0))
        self.assertIn("其中 1 张有红冲更正", s)
        self.assertIn("装订时记得附计提更正单（复核时打印的那张）", s)
        self.assertNotIn("打印本月计提更正单", s)
        self.assertLess(s.index("有红冲更正（1 张）"), s.index("只核销（1 张）"))

    def test_multi_subject_stands_out(self):
        """涉及两个主体的红冲：排在最前、写明红冲了哪张、该哪个主体补提、两边对着审；系统不做的那张列进「要人来做」。"""
        s, st = V.notice_text("2026-10-11 14:20", [self.HX, self.FIX, self.XO], pending=[self.PEND], left=3)
        self.assertEqual(st, {"ok": 3, "bad": 0, "fix": 2, "multi": 1, "pending": 1, "later": 0, "later3": 0})
        self.assertIn("其中 2 张有红冲更正（1 张涉及两个主体）", s)
        self.assertIn("另外整笔红冲了 记-390 918.93：这笔计提记错了主体，应是深圳星期九的", s)
        self.assertEqual(s.count("另红冲"), 0)                                   # 做账类型里那半句不重复写
        self.assertLess(s.index("涉及两个主体的红冲（1 张）"), s.index("有红冲更正（1 张）"))
        self.assertIn("要人来做（1 张，涉及两个主体，系统不自动做）", s)
        self.assertIn("计提记在了「深圳星期零」的账上（记-390 918.93）", s)             # 写怎么回事，不是写「为什么不自动做」
        self.assertIn("两个主体的凭证对着审", s)
        self.assertIn("还有 3 张这一轮没排上", s)

    def test_failed_first_and_pending_alone(self):
        s, st = V.notice_text("2026-10-11 14:20", [self.HX, self.BAD])
        self.assertIn("做成 1 张，1 张没做成，要人处理", s)
        self.assertLess(s.index("没做成（1 张）"), s.index("记-330"))
        self.assertIn("付款单审核失败：已审核", s)
        s, st = V.notice_text("2026-10-11 14:20", [], pending=[self.PEND])
        self.assertIn("有 1 张涉及两个主体的红冲，系统不自动做，要人来做", s)
        self.assertNotIn("到金蝶审核", s)

    def test_invoice_later(self):
        """V2.919 发票后补：第一笔只做支付、第二笔暂估转待认证，各列一段；不算进红冲更正。"""
        a = {"subject": "深圳星期零", "payee": "禾享国际物流", "amount": 1322, "kind": "later", "text": "发票后补", "vno": "361", "done": True}
        b = {"subject": "深圳星期零", "payee": "禾享国际物流", "amount": 1322, "kind": "later3", "vno": "402", "done": True,
             "text": "发票 2644 到了，暂估进项税 13.09 转待认证"}
        s, st = V.notice_text("2026-10-12 14:20", [self.HX, a])
        self.assertEqual((st["fix"], st["later"], st["later3"]), (0, 1, 0))
        self.assertIn("发票后补，只做了支付（1 张）", s)
        self.assertIn("暂估进项税还挂着", s)
        self.assertNotIn("红冲更正", s)
        s, st = V.notice_text("2026-10-20 09:00", [b])
        self.assertEqual((st["fix"], st["later3"]), (0, 1))
        self.assertIn("刚做了 1 张凭证", s)
        self.assertIn("发票到了，暂估转待认证（1 张）", s)
        self.assertIn("暂估进项税 13.09 转待认证", s)

    def test_trial_and_long_folds(self):
        s, _ = V.notice_text("2026-10-11 14:20", [dict(self.HX, vno="")], trial=True)
        self.assertTrue(s.startswith("【试发】"))
        self.assertIn("记-（待出）", s)
        many = [dict(self.HX, payee="某某某某某某供应链管理有限责任公司%d" % i, vno=str(i)) for i in range(60)]
        s, st = V.notice_text("2026-10-11 14:20", many + [self.XO])
        self.assertLess(len(s), 1800)
        self.assertIn("只核销 60 张，合计", s)
        self.assertIn("记-390", s)                                               # 要人留意的不折叠


if __name__ == "__main__":
    unittest.main()
