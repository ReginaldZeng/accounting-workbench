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
        # 不填 / 填得不小于整笔 → 还是整笔改
        fx2 = {"389": [dict(fx["389"][0], split_amt="4117.46")]}
        ls2 = V.build(ctx, [v], inv, V.plan([v], inv, fx2), fx2)
        self.assertEqual([(l["dims"].get("biz"), l["dr"]) for l in ls2 if l["block"] == "更正" and l["acct"] == "6601"], [("电商", 3777.49)])


if __name__ == "__main__":
    unittest.main()
