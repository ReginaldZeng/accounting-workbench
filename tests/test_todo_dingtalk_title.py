# [Change Log]
# Date: 2026-10-13 | Author: Claude Opus 5.5 | Version: V2.933（钉钉待办那一行写清楚）
# Description: 钉钉待办列表只显示一行——核对这一行按「【在哪办的什么事】什么业务来的｜哪个主体哪张｜哪家｜多少」拼得对不对。
#   四个环节各一条，外加：提交没成功挂回做账人、金蝶里找不到、还是草稿审不了、认不出的老格式原样用。
#   事项 / 说明用 todo_scenes 现在真实写出来的样子（付款做账那条照线上 2026-10-13 的实物）。不连钉钉、不连金蝶。
#   运行：repo 根目录 PYTHONPATH=01_Current_Deliverables/app/backend python -m unittest tests.test_todo_dingtalk_title -v
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "01_Current_Deliverables" / "app" / "backend"
sys.path.insert(0, str(BACKEND))

_TMP = tempfile.TemporaryDirectory(prefix="todo-dt-title-", ignore_cleanup_errors=True)   # Windows 下 sqlite 文件退出时还被占着
os.environ.setdefault("DB_URL", "sqlite:///" + (Path(_TMP.name) / "t.sqlite").as_posix())

import todo_dingtalk as dt     # noqa: E402


def _it(scene, title, sub, payload=None, state="已提交，等审核", warn="", holder="scene"):
    return {"scene": scene, "title": title, "sub": sub, "payload": payload or {}, "state": state, "warn": warn, "holder": holder}


class DingTalkTodoTitleTests(unittest.TestCase):
    def test_payment_voucher_waiting_audit(self):
        it = _it("logi_voucher_audit", "付款做账 · 记-1　湖北极鲜达供应链有限责任公司 2026-08", "深圳星期九 · 共 4 行 · 借贷 6,093.62",
                 {"inst": "x", "vid": 1, "vno": "1", "ym": "2026-10"})
        self.assertEqual(dt.title_of(it), "【金蝶凭证审核】物流付款做账｜深圳星期九 2026 年 10 月 记-1｜湖北极鲜达供应链有限责任公司 2026-08 账单｜金额 6,093.62")

    def test_payment_voucher_month_not_known_yet_and_no_bill_period(self):
        # 早先挂上的还没补到凭证月份：月份那段不出，不瞎写；收款方后面没有账期的不加「账单」
        it = _it("logi_voucher_audit", "付款做账 · 记-7　某承运商", "深圳星期零 · 共 3 行 · 借贷 100.00")
        self.assertEqual(dt.title_of(it), "【金蝶凭证审核】物流付款做账｜深圳星期零 记-7｜某承运商｜金额 100.00")

    def test_payment_voucher_not_submitted_goes_back_to_maker(self):
        it = _it("logi_voucher_audit", "付款做账 · 记-12　武汉顺鸽科技有限公司 2026-08", "孝感星期九 · 共 4 行 · 借贷 786.11",
                 state="保存了，没提交上", warn="请到金蝶手动提交，提交后才会转给审核人", holder="origin")
        self.assertEqual(dt.title_of(it), "【金蝶凭证提交】物流付款做账｜孝感星期九 记-12｜武汉顺鸽科技有限公司 2026-08 账单｜金额 786.11｜⚠ 请到金蝶手动提交，提交后才会转给审核人")

    def test_fx_rate(self):
        it = _it("fx_audit", "汇率录入 · 2026 年 9 月（组织 101）", "8 条汇率 · 金蝶里已审 0 条", {"year": 2026, "month": 9, "org": "101"})
        self.assertEqual(dt.title_of(it), "【金蝶汇率审核】汇率录入｜2026 年 9 月｜深圳星期零｜8 条汇率，金蝶里已审 0 条")

    def test_fx_rate_gone_in_kingdee(self):
        it = _it("fx_audit", "汇率录入 · 2026 年 9 月（组织 107）", "8 条汇率 · 金蝶里已审 0 条", {"year": 2026, "month": 9, "org": "107"},
                 state="金蝶里已找不到", warn="金蝶里已找不到这批汇率（可能被人删了）。要重录，先在汇率录入页把这批撤销", holder="origin")
        self.assertTrue(dt.title_of(it).startswith("【金蝶汇率核实】汇率录入｜2026 年 9 月｜孝感星期九｜8 条汇率，金蝶里已审 0 条｜⚠ 金蝶里已找不到这批汇率"))

    def test_accrual_still_draft(self):
        it = _it("logi_accrual_audit", "物流计提凭证 · 2026 年 9 月", "12 张", {"year": 2026, "period": 9},
                 state="录进金蝶的是草稿，等提交", warn="这 12 张还是草稿、没提交，现在审不了")
        self.assertEqual(dt.title_of(it), "【金蝶凭证审核】物流计提｜2026 年 9 月｜12 张｜⚠ 这 12 张还是草稿、没提交，现在审不了")

    def test_accrual_fix(self):
        it = _it("logi_fix", "计提更正 · 恒茂 2026-08", "1 笔要改 · 还剩 1 笔没办", {"carrier": "恒茂", "period": "2026-08"}, state="还没合进付款凭证")
        self.assertEqual(dt.title_of(it), "【计提更正】物流对账复核｜恒茂 2026-08｜1 笔要改，还剩 1 笔没办｜在工作台「付款做账」处理")

    def test_unrecognised_old_format_kept_as_is(self):
        it = _it("logi_voucher_audit", "某种以后改了的写法", "说明")
        self.assertEqual(dt.title_of(it), "【金蝶凭证审核】物流付款做账｜某种以后改了的写法｜说明")

    def test_capped_at_180(self):
        it = _it("logi_fix", "x", "y", {"carrier": "很长的名字" * 60, "period": "2026-08"})
        self.assertEqual(len(dt.title_of(it)), 180)


if __name__ == "__main__":
    unittest.main()
