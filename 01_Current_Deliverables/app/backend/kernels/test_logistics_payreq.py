# -*- coding: utf-8 -*-
# V2.730 钉钉请款单纯函数单测（样本取自易风达 202609111542000383471 实单结构）
import json
import unittest

from kernels import logistics_payreq as P

_BANK = json.dumps([{"rowValue": [
    {"label": "收款方名称（工商名）", "value": "武汉易风达冷链物流有限公司"},
    {"label": "银行账号", "value": "1279 0841 2410 401"},
    {"label": "对方收款所属银行", "value": "招商银行"}]}], ensure_ascii=False)
INST = {"form_component_values": [
    {"name": "公司主体", "value": "孝感市星期九食品科技有限公司"},
    {"name": "付款事由", "value": "2026年8月，易风达\n小料仓储费：玉湖仓10011"},
    {"name": "付款总额", "value": "87168"},
    {"name": "实际付款总额", "value": "87168"},
    {"name": "银行信息", "value": _BANK}],
    "tasks": [{"userid": "u1", "task_status": "COMPLETED"}, {"userid": "me", "task_status": "RUNNING"},
              {"userid": "u3", "task_status": "CANCELED"}],
    "operation_records": [{"userid": "u0", "operation_type": "START_PROCESS_INSTANCE", "operation_result": "NONE", "date": "2026-09-11 15:42:38"},
                          {"userid": "u0", "operation_type": "PROCESS_CC", "operation_result": "NONE", "date": "2026-09-11 15:42:38"},
                          {"userid": "u1", "operation_type": "EXECUTE_TASK_NORMAL", "operation_result": "AGREE", "date": "2026-09-22 17:51:10",
                           "remark": "计提数各小项与账单数据一致"}]}


class T(unittest.TestCase):
    def test_parse_form(self):
        f = P.parse_form(INST)
        self.assertEqual(f["subject_full"], "孝感市星期九食品科技有限公司")
        self.assertEqual(f["payee"], "武汉易风达冷链物流有限公司")
        self.assertEqual(f["payee_account"], "127908412410401")
        self.assertEqual(f["amount"], 87168.0)
        self.assertTrue(f["reason"].startswith("2026年8月"))

    def test_actual_amount_wins(self):
        inst = {"form_component_values": [{"name": "付款总额", "value": "1000"}, {"name": "实际付款总额", "value": "800"}]}
        self.assertEqual(P.parse_form(inst)["amount"], 800.0)

    def test_classify(self):
        self.assertEqual(P.classify_file("8月易风达87968(1).xlsx", "dingtalk_form"), "bill")
        self.assertEqual(P.classify_file("易风达2026年8月-8月账单复核V1(4).xlsx", "dingtalk_comment"), "review")
        self.assertEqual(P.classify_file("8月星期零-易风达账单、盘点表（盖章版）(1).pdf", "dingtalk_form"), "stamp")
        self.assertEqual(P.classify_file("8月易风达-孝感星期九1302.pdf", "dingtalk_form"), "invoice")

    def test_period_by_amount_first(self):
        amap = {"2026-08": {("孝感星期九", "物流运输服务027"): 87168.0}}
        self.assertEqual(P.infer_period(87168, "孝感星期九", "物流运输服务027", "2026-09-11 15:42", amap, ["7月"]), ("2026-08", "amount"))

    def test_period_by_text(self):
        self.assertEqual(P.infer_period(5, "孝感星期九", "x", "2026-09-11 15:42", {}, ["2026年8月，易风达"]), ("2026-08", "text"))
        self.assertEqual(P.infer_period(5, "孝感星期九", "x", "2026-01-05 10:00", {}, ["", "12月账单.xlsx"]), ("2025-12", "text"))
        self.assertEqual(P.infer_period(5, "孝感星期九", "x", "2026-09-11 15:42", {}, ["运费"]), ("", ""))

    def test_prev_periods(self):
        self.assertEqual(P.prev_periods("2026-01-05 10:00", 3), ["2025-12", "2025-11", "2026-01"])

    def test_status(self):
        names = {"me": "曾禹锡", "u1": "张三"}
        cur = P.current_tasks(INST, names)
        self.assertEqual(cur, [{"userid": "me", "name": "曾禹锡"}])
        self.assertEqual(P.status_view({"dt_status": "RUNNING", "cur": cur}, "me")["key"], "mine")
        self.assertEqual(P.status_view({"dt_status": "RUNNING", "cur": cur}, "u1")["label"], "待曾禹锡审批")
        self.assertEqual(P.status_view({"dt_status": "COMPLETED", "dt_result": "agree"})["key"], "agreed")
        self.assertEqual(P.status_view({"dt_status": "COMPLETED", "dt_result": "agree", "kd_paid": "2026-09-28|Z|9"})["date"], "2026-09-28")
        self.assertEqual(P.status_view({"dt_status": "TERMINATED"})["key"], "void")
        ops = P.ops_view(INST, names)
        self.assertEqual([o["type"] for o in ops], ["发起", "审批"])       # 抄送去掉
        self.assertEqual(ops[1]["result"], "同意")

    def test_match_paybills(self):
        reqs = [{"inst_id": "A", "sup_code": "物流运输服务027", "subject_full": "孝感", "amount": 87168, "create_time": "2026-09-11 15:42"},
                {"inst_id": "B", "sup_code": "物流运输服务027", "subject_full": "深圳", "amount": 800, "create_time": "2026-09-11 15:43"},
                {"inst_id": "C", "sup_code": "物流运输服务027", "subject_full": "深圳", "amount": 800, "create_time": "2026-09-20 10:00"}]
        pbs = [{"id": 1, "code": "物流运输服务027", "org": "深圳", "amount": 800, "date": "2026-09-28", "status": "Z"},
               {"id": 2, "code": "物流运输服务027", "org": "孝感", "amount": 87168, "date": "2026-09-28", "status": "Z"},
               {"id": 3, "code": "物流运输服务027", "org": "孝感", "amount": 87168, "date": "2026-09-01", "status": "Z"}]
        m = P.match_paybills(reqs, pbs)
        self.assertEqual(m["A"], "2026-09-28|Z|2")
        self.assertEqual(m["B"], "2026-09-28|Z|1")
        self.assertNotIn("C", m)                       # 一张付款单只配一张请款单
        self.assertNotIn("A", P.match_paybills(reqs, pbs, taken={2}))


if __name__ == "__main__":
    unittest.main()
