# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-25 | Author: Kimi | Version: V2.1007
# Description: 物流四步 readiness 纯函数分支测试：普通/登记制、核量核价独立开关、人工确认、后补与取数故障。
import unittest

from kernels import logistics_readiness as rd


def fixture():
    return {
        "intake": {"has_spec": True, "bills": [{"src": "a"}], "verdict": "ok", "diff": 0},
        "lines": {"ok": True, "n_unexplained": 0, "rows": [
            {"kind": "accr", "subject": "深圳星期零", "vno": "记-1", "note": ""},
            {"kind": "gtotal", "subject": "深圳星期零", "diff": 0, "mark": {"status": "ok", "dv": ""}},
        ]},
        "docs": {"docs": [{"doc_no": "XSCKD1", "state": "ok", "fee_chk": "ok", "sub_fees": {"标准": 100}}],
                 "classify": {"XSCKD1": {"v": "归类一致"}}, "dups": {}},
        "sign": {"blocks": [{"inst": "i1", "bid": "B1", "amount": 100, "payee": "承运 商（深圳）",
                             "subject_full": "星期零（深圳）", "invoices": [
                                 {"number": "N1", "gross": 100, "review": "approved", "paper": False,
                                  "seller": "承运商(深圳)", "buyer": "星期零(深圳)"}]}]},
    }


class ReadinessTests(unittest.TestCase):
    def evaluate(self, f, **kw):
        return rd.evaluate("测试承运商", "2026-09", intake=f["intake"], lines=f["lines"],
                           docs=f["docs"], sign=f["sign"], **kw)

    def test_normal_ready_contract(self):
        r = self.evaluate(fixture())
        self.assertTrue(r["can_sign"])
        self.assertEqual(r["mode"], "normal")
        self.assertEqual(list(r["steps"]), ["intake", "docs", "lines", "sign"])
        self.assertEqual(r["blockers"], [])
        self.assertEqual(r["steps"]["intake"]["count"], 1)
        self.assertIsNone(r["signed"])

    def test_intake_requires_bill_spec_ok_and_zero_diff(self):
        f = fixture()
        f["intake"].update(has_spec=False, bills=[], verdict="gap", diff=0.01)
        s = self.evaluate(f)["steps"]["intake"]
        self.assertEqual(s["status"], "pending")
        self.assertEqual(len(s["blockers"]), 4)

    def test_lines_marks_diff_and_department(self):
        f = fixture()
        f["lines"]["n_unexplained"] = 1
        f["lines"]["rows"][0]["dept_warn"] = {"want": "仓储物流部"}
        f["lines"]["rows"][1].update(diff=10, mark={"status": "question", "dv": ""})
        s = self.evaluate(f)["steps"]["lines"]
        self.assertEqual(s["status"], "pending")
        self.assertEqual(len(s["blockers"]), 4)
        f["lines"]["n_unexplained"] = 0
        f["lines"]["rows"][0]["fix"] = {"to_dept": "001 仓储物流部"}
        f["lines"]["rows"][1]["mark"] = {"status": "ok", "dv": "fix"}
        self.assertEqual(self.evaluate(f)["steps"]["lines"]["status"], "ok")

    def test_docs_system_axes_or_manual_confirmation(self):
        f = fixture()
        f["docs"]["docs"][0]["state"] = "info"
        f["docs"]["classify"]["XSCKD1"]["v"] = "判不了"
        f["docs"]["dups"] = {"XSCKD1": {"same": [100, 100]}}
        s = self.evaluate(f)["steps"]["docs"]
        self.assertEqual(len(s["blockers"]), 2)
        f["docs"]["docs"][0]["confirmed"] = {"by": "财务"}
        self.assertEqual(self.evaluate(f)["steps"]["docs"]["status"], "ok")

    def test_price_pending_requires_manual_confirmation(self):
        f = fixture()
        f["docs"]["docs"][0]["state"] = "price"
        blockers = self.evaluate(f)["steps"]["docs"]["blockers"]
        self.assertIn("数量", blockers[0])
        f["docs"]["docs"][0]["state"] = "ok"
        f["docs"]["docs"][0]["sub_fees"] = {}
        self.assertIn("价格无依据", self.evaluate(f)["steps"]["docs"]["blockers"][0])

    def test_qty_and_price_switches_are_independent(self):
        f = fixture()
        doc = f["docs"]["docs"][0]
        doc.update(state="qtydiff", sub_fees={}, qty_enabled=False, price_enabled=True)
        blockers = self.evaluate(f)["steps"]["docs"]["blockers"]
        self.assertEqual(len(blockers), 1)
        self.assertIn("价格", blockers[0])
        doc.update(qty_enabled=True, price_enabled=False)
        blockers = self.evaluate(f)["steps"]["docs"]["blockers"]
        self.assertEqual(len(blockers), 1)
        self.assertIn("数量", blockers[0])
        doc.update(qty_enabled=False, price_enabled=True, sub_fees={"标准": 100}, fee_chk="diff")
        f["docs"]["classify"]["XSCKD1"]["v"] = "判不了"
        f["docs"]["dups"] = {"XSCKD1": {"same": [100, 100]}}
        blockers = self.evaluate(f)["steps"]["docs"]["blockers"]
        self.assertEqual(len(blockers), 3)
        self.assertFalse(any("数量" in x or "价格" in x for x in blockers))
        doc.update(qty_enabled=False, price_enabled=False)
        step = self.evaluate(f)["steps"]["docs"]
        self.assertEqual(step["status"], "error")
        f["docs"]["docs"] = []
        f["docs"]["settings"] = {"qty_enabled": False, "price_enabled": False}
        self.assertEqual(self.evaluate(f)["steps"]["docs"]["status"], "error")

    def test_invoice_amount_review_names_and_paper(self):
        f = fixture()
        f["sign"]["blocks"][0]["invoices"][0].update(review="pending", seller="别人", buyer="别的主体")
        f["sign"]["blocks"][0]["amount"] = 101
        s = self.evaluate(f)["steps"]["sign"]
        self.assertEqual(len(s["blockers"]), 4)
        f = fixture()
        f["sign"]["blocks"][0]["invoices"][0]["paper"] = False
        self.assertTrue(self.evaluate(f)["can_sign"])

    def test_no_invoice_requires_later(self):
        f = fixture()
        f["sign"]["blocks"] = []
        self.assertEqual(self.evaluate(f)["steps"]["sign"]["status"], "pending")
        f = fixture()
        f["sign"]["blocks"][0]["invoices"] = []
        self.assertEqual(self.evaluate(f)["steps"]["sign"]["status"], "pending")
        f["sign"]["blocks"][0]["later"] = {"by": "财务"}
        self.assertTrue(self.evaluate(f)["can_sign"])

    def test_register_exempts_intake_docs_and_requires_note_for_diff(self):
        f = fixture()
        reg = {"cells": [{"subject": "深圳星期零", "has_accr": True, "has_req": True, "diff": 2}]}
        r = rd.evaluate("测试承运商", "2026-09", mode="register", lines=reg, sign=f["sign"])
        self.assertEqual(r["steps"]["intake"], {"status": "ok", "label": "登记制免核", "count": 0, "blockers": []})
        self.assertEqual(r["steps"]["docs"]["label"], "登记制免核")
        self.assertEqual(r["steps"]["lines"]["status"], "pending")
        r = rd.evaluate("测试承运商", "2026-09", mode="register", lines=reg, sign=f["sign"], note="次月调整")
        self.assertTrue(r["can_sign"])

    def test_register_requires_comparable_numbers(self):
        f = fixture()
        reg = {"cells": [{"subject": "深圳星期零", "has_accr": False, "has_req": True, "diff": -100}]}
        r = rd.evaluate("测试承运商", "2026-09", mode="register", lines=reg, sign=f["sign"], note="直接费用")
        self.assertFalse(r["can_sign"])
        self.assertIn("缺费用凭证/计提", r["steps"]["lines"]["blockers"][0])

    def test_carry_and_fetch_errors(self):
        f = fixture()
        r = self.evaluate(f, carry=["2026-08 XSCKD1 多收 5 元"])
        self.assertFalse(r["can_sign"])
        self.assertIn("历史待扣回", r["steps"]["sign"]["blockers"][0])
        f["lines"] = {"error": "金蝶读取逐笔计提失败"}
        s = self.evaluate(f)["steps"]["lines"]
        self.assertEqual(s["status"], "error")
        self.assertFalse(self.evaluate(f)["can_sign"])

    def test_signed_is_returned_and_cannot_sign_again(self):
        r = self.evaluate(fixture(), signed={"reviewer": "财务"})
        self.assertFalse(r["can_sign"])
        self.assertEqual(r["signed"]["reviewer"], "财务")


if __name__ == "__main__":
    unittest.main()
