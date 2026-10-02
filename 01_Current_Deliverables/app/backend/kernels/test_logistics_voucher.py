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


if __name__ == "__main__":
    unittest.main()
