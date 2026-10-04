# [Change Log]
# Date: 2026-10-04 | Author: Claude Opus 5.5 | Version: V2.790（首页待办区）
# Description: 首页待办区的后端核对——待办表（记一笔 / 撤回 / 办结 / 挂给谁）＋四个环节的核对逻辑。
#   不 import app（它会建库、起线程、跑迁移）；db 用临时 SQLite，金蝶一律打桩（不连真金蝶、不发任何通知）。
#   重点验「销账看事实」：金蝶没审完不销、连不上不当成已审也不当成已删、源头撤光才撤回、换处理人待办跟着走。
#   运行：repo 根目录 PYTHONPATH=01_Current_Deliverables/app/backend python -m unittest tests.test_todo_area -v
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "01_Current_Deliverables" / "app" / "backend"
sys.path.insert(0, str(BACKEND))

_TMP = tempfile.TemporaryDirectory(prefix="todo-area-", ignore_cleanup_errors=True)   # Windows 下 sqlite 文件退出时还被占着
os.environ["DB_URL"] = "sqlite:///" + (Path(_TMP.name) / "todo.sqlite").as_posix()

import db                      # noqa: E402
import todo_store as ts        # noqa: E402
import todo_scenes as sc       # noqa: E402
from sqlalchemy import delete, insert, update   # noqa: E402


class FakeKd:
    """打桩金蝶：rates={FRATEID: 状态}；vouchers={内码: 状态 或 None(金蝶明确答复没有)}；down=True 模拟连不上。"""
    def __init__(self):
        self.rates, self.vouchers, self.down, self.views = {}, {}, False, 0

    def login(self, *a, **k):
        if self.down:
            raise sc.kc.KingdeeError("连接金蝶失败（打桩）")
        return object(), {}

    def query(self, s, conf, form, fields, flt, order=""):
        return [{"id": int(k), "st": v} for k, v in self.rates.items() if v is not None]

    def status(self, vid, s, conf):
        self.views += 1
        return self.vouchers.get(str(vid))


class TodoAreaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.seed_admin()
        for n in ("审核甲", "审核乙", "做账丙"):
            if not db.get_user(n):
                db.create_user(n, "Passw0rd!x", "核算组", "normal", None, "会计")
        cls.admin = next(u["name"] for u in db.list_users() if u["role"] == "admin")

    def setUp(self):
        with db._engine.begin() as c:
            c.execute(delete(ts.todo_item))
            c.execute(delete(db.fx_post_log))
            c.execute(delete(db.post_log))
            c.execute(delete(sc.FIXT))
        db.set_setting(ts.ASSIGN_KEY, {}, "test")
        db.set_setting(sc.VOUCHER_POSTED_KEY, {}, "test")
        self.kd = FakeKd()
        self._orig = (sc.kc.login, sc.kc._query, sc._voucher_status)
        sc.kc.login, sc.kc._query, sc._voucher_status = self.kd.login, self.kd.query, self.kd.status

    def tearDown(self):
        sc.kc.login, sc.kc._query, sc._voucher_status = self._orig

    def _user(self, name):
        return db.get_user(name)

    def _mine(self, name):
        return ts.for_user(self._user(name))

    def _fx_logs(self, n=8, y=2026, m=9, org="101"):
        for i in range(n):
            db.log_fx_post(y, m, org, "美元→人民币", "USD", "CNY", "7.1", "2026-09-%02d" % (i + 1), "2026-09-30", "month_end", 1000 + i, "做账丙")
            self.kd.rates[str(1000 + i)] = "B"

    # ---------- 待办表本身 ----------
    def test_no_assignee_falls_back_to_super_and_says_so(self):
        ts.open_item("fx_audit", "2026-09:101", "汇率", origin="做账丙")
        m = self._mine(self.admin)["mine"]
        self.assertEqual(len(m), 1)
        self.assertIn("还没指定处理人", m[0]["note"])
        self.assertEqual(self._mine("审核甲")["mine"], [])

    def test_reassign_follows_the_scene_not_the_person(self):
        ts.set_assignees({"fx_audit": ["审核甲"]}, "test")
        ts.open_item("fx_audit", "2026-09:101", "汇率", origin="做账丙")
        self.assertEqual(len(self._mine("审核甲")["mine"]), 1)
        self.assertEqual(self._mine("审核甲")["mine"][0]["note"], "")
        ts.set_assignees({"fx_audit": ["审核乙"]}, "test")
        self.assertEqual(self._mine("审核甲")["mine"], [])
        self.assertEqual(len(self._mine("审核乙")["mine"]), 1)

    def test_sent_tab_and_bot_origin(self):
        ts.set_assignees({"fx_audit": ["审核甲"]}, "test")
        ts.open_item("fx_audit", "a", "人交的", origin="做账丙")
        ts.open_item("fx_audit", "b", "机器交的", origin=sc.BOT_FX, bot=True)
        d = self._mine("做账丙")
        self.assertEqual([v["title"] for v in d["sent"]], ["人交的"])
        self.assertEqual(d["counts"], {"mine": 0, "late": 0, "waiting": 1})
        self.assertTrue(next(v for v in self._mine("审核甲")["mine"] if v["title"] == "机器交的")["bot"])

    def test_same_ref_is_one_item_and_reopens_after_done(self):
        a = ts.open_item("fx_audit", "x", "汇率", sub="8 条")
        b = ts.open_item("fx_audit", "x", "汇率", sub=None)          # None＝保持原样
        self.assertEqual(a, b)
        self.assertEqual(ts.get_by_id(a)["sub"], "8 条")
        ts.finish(a, "", "已审核")
        self.assertFalse(ts.finish(a, "", "再销一次"))               # 办结的不会被二次改写
        c = ts.open_item("fx_audit", "x", "汇率", sub="又录了")
        self.assertEqual(c, a)
        self.assertEqual(ts.get_by_id(a)["status"], "open")
        self.assertEqual(ts.get_by_id(a)["done_how"], "")

    def test_late_after_three_workdays(self):
        i = ts.open_item("fx_audit", "x", "汇率")
        old = (datetime.now() - timedelta(days=9)).strftime(ts._FMT)
        with db._engine.begin() as c:
            c.execute(update(ts.todo_item).where(ts.todo_item.c.id == i).values(created_ts=old))
        v = self._mine(self.admin)["mine"][0]
        self.assertTrue(v["late"])
        self.assertEqual(v["age"], "9 天")

    def test_unknown_scene_rejected_but_safe_open_swallows(self):
        with self.assertRaises(ValueError):
            ts.open_item("nope", "x", "t")
        self.assertIsNone(ts.safe_open("nope", "x", "t"))

    # ---------- ① 汇率 ----------
    def test_fx_only_closes_when_all_audited(self):
        self._fx_logs(8)
        sc.fx_touch(2026, 9, "101", "做账丙")
        it = ts.get("fx_audit", "2026-09:101")
        self.assertEqual((it["status"], it["sub"]), ("open", "8 条汇率"))
        for k in list(self.kd.rates)[:3]:
            self.kd.rates[k] = "C"
        r = sc.run_checks()
        it = ts.get("fx_audit", "2026-09:101")
        self.assertEqual((r["done"], it["status"], it["sub"]), (0, "open", "8 条汇率 · 金蝶里已审 3 条"))
        for k in self.kd.rates:
            self.kd.rates[k] = "C"
        self.assertEqual(sc.run_checks()["done"], 1)
        it = ts.get("fx_audit", "2026-09:101")
        self.assertEqual(it["status"], "done")
        self.assertIn("已全部审核", it["done_how"])

    def test_fx_kingdee_down_changes_nothing(self):
        self._fx_logs(2)
        sc.fx_touch(2026, 9, "101", "做账丙")
        self.kd.down = True
        r = sc.run_checks()
        it = ts.get("fx_audit", "2026-09:101")
        self.assertEqual((r["failed"], it["status"], it["holder"]), (1, "open", "scene"))
        self.assertIn("没连上金蝶", it["check_msg"])

    def test_fx_bot_origin_and_partial_unpost_keeps_item(self):
        self._fx_logs(8)
        sc.fx_touch(2026, 9, "101", sc.BOT_FX, bot=True)
        with db._engine.begin() as c:                                  # 撤了 1 条，还剩 7 条
            c.execute(delete(db.fx_post_log).where(db.fx_post_log.c.kd_id == "1000"))
        sc.fx_touch(2026, 9, "101", posted=False)
        it = ts.get("fx_audit", "2026-09:101")
        self.assertEqual((it["status"], it["origin"], it["origin_bot"]), ("open", sc.BOT_FX, 1))
        with db._engine.begin() as c:                                  # 全撤光
            c.execute(delete(db.fx_post_log))
        sc.fx_touch(2026, 9, "101", posted=False)
        self.assertEqual(ts.get("fx_audit", "2026-09:101")["status"], "withdrawn")

    def test_fx_unpost_does_not_create_item_out_of_thin_air(self):
        self._fx_logs(3)                                               # 上线前就录好的，没有待办
        sc.fx_touch(2026, 9, "101", posted=False)
        self.assertIsNone(ts.get("fx_audit", "2026-09:101"))

    def test_fx_deleted_in_kingdee_goes_back_to_origin_not_closed(self):
        ts.set_assignees({"fx_audit": ["审核甲"]}, "test")
        self._fx_logs(2)
        sc.fx_touch(2026, 9, "101", "做账丙")
        self.kd.rates = {k: None for k in self.kd.rates}
        sc.run_checks()
        it = ts.get("fx_audit", "2026-09:101")
        self.assertEqual((it["status"], it["holder"]), ("open", "origin"))
        self.assertIn("已找不到", it["warn"])
        self.assertEqual(len(self._mine("做账丙")["mine"]), 1)
        self.assertEqual(self._mine("审核甲")["mine"], [])

    # ---------- ② 物流计提凭证 ----------
    def _accrual_logs(self, n, y=2026, p=9):
        for i in range(n):
            db.log_logistics_post(y, p, "摘要%d" % i, "B%d" % i, 2000 + i, str(300 + i), "做账丙")
            self.kd.vouchers[str(2000 + i)] = "A"

    def test_accrual_drafts_are_called_out_and_audited_ones_not_re_asked(self):
        self._accrual_logs(4)
        sc.accrual_touch(2026, 9, "做账丙")
        it = ts.get("logi_accrual_audit", "2026-09")
        self.assertIn("草稿", it["warn"])
        self.kd.vouchers.update({"2000": "C", "2001": "B"})
        sc.run_checks()
        it = ts.get("logi_accrual_audit", "2026-09")
        self.assertEqual(it["sub"], "4 张 · 金蝶里已审 1 张")
        self.assertEqual(it["state"], "已提交 1 张，等审核")
        self.assertIn("其中 2 张还是草稿", it["warn"])
        self.assertEqual(self.kd.views, 4)
        sc.run_checks()                                                # 已审的那张不再去问金蝶
        self.assertEqual(self.kd.views, 7)
        for k in self.kd.vouchers:
            self.kd.vouchers[k] = "C"
        sc.run_checks()
        self.assertEqual(ts.get("logi_accrual_audit", "2026-09")["status"], "done")

    def test_accrual_withdrawn_when_all_unposted(self):
        self._accrual_logs(2)
        sc.accrual_touch(2026, 9, "做账丙")
        with db._engine.begin() as c:
            c.execute(delete(db.post_log))
        sc.accrual_touch(2026, 9, posted=False)
        self.assertEqual(ts.get("logi_accrual_audit", "2026-09")["status"], "withdrawn")

    # ---------- ③ 付款做账凭证 ----------
    def _voucher(self, inst="inst1", submitted=True):
        rec = {"vid": 5001, "vno": "312", "dr": 48260.0, "lines": 5, "submitted": submitted}
        db.set_setting(sc.VOUCHER_POSTED_KEY, {inst: rec}, "做账丙")
        self.kd.vouchers["5001"] = "B" if submitted else "A"
        sc.voucher_touch(inst, rec, {"payee": "甲物流公司", "period": "2026-09", "subject": "深圳主体"}, "做账丙")
        return ts.get("logi_voucher_audit", inst)

    def test_voucher_submit_failed_goes_to_maker_then_to_reviewer(self):
        ts.set_assignees({"logi_voucher_audit": ["审核甲"]}, "test")
        it = self._voucher(submitted=False)
        self.assertEqual(it["holder"], "origin")
        self.assertEqual(self._mine("做账丙")["mine"][0]["kind"], "do")
        self.assertEqual(self._mine("审核甲")["mine"], [])
        self.kd.vouchers["5001"] = "B"                                 # 做账人去金蝶手动提交了
        sc.run_checks()
        self.assertEqual(len(self._mine("审核甲")["mine"]), 1)
        self.assertEqual(self._mine("做账丙")["mine"], [])
        self.assertEqual(self._mine("做账丙")["counts"]["waiting"], 1)
        self.kd.vouchers["5001"] = "C"
        sc.run_checks()
        self.assertEqual(ts.get("logi_voucher_audit", "inst1")["status"], "done")
        self.assertEqual([v["status"] for v in self._mine("做账丙")["sent"]], ["done"])

    def test_voucher_title_and_amount(self):
        it = self._voucher()
        self.assertEqual(it["title"], "付款做账 · 记-312　甲物流公司 2026-09")
        self.assertIn("借贷 48,260.00", it["sub"])

    # ---------- ④ 计提更正 ----------
    def _fix(self, key, carrier="乙物流", period="2026-08"):
        with db._engine.begin() as c:
            return c.execute(insert(sc.FIXT).values(carrier=carrier, period=period, line_key=key, to_fee="6601 运费",
                                                    snap_json="{}")).inserted_primary_key[0]

    def test_fix_closes_when_merged_into_posted_voucher(self):
        a, b = self._fix("k1"), self._fix("k2")
        sc.fix_touch("乙物流", "2026-08", "做账丙")
        it = ts.get("logi_fix", "乙物流|2026-08")
        self.assertEqual(it["sub"], "2 笔要改 · 还剩 2 笔没办")
        db.set_setting(sc.VOUCHER_POSTED_KEY, {"i1": {"vid": 1, "fix_ids": [a]}}, "t")
        sc.fix_touch("乙物流", "2026-08", "做账丙")
        self.assertEqual(ts.get("logi_fix", "乙物流|2026-08")["sub"], "2 笔要改 · 还剩 1 笔没办")
        db.set_setting(sc.VOUCHER_POSTED_KEY, {"i1": {"vid": 1, "fix_ids": [a, b]}}, "t")
        sc.fix_touch("乙物流", "2026-08", "做账丙")
        it = ts.get("logi_fix", "乙物流|2026-08")
        self.assertEqual(it["status"], "done")
        self.assertIn("已合进付款凭证", it["done_how"])

    def test_fix_manual_close_then_new_fix_reopens(self):
        self._fix("k1")
        sc.fix_touch("乙物流", "2026-08", "做账丙")
        it = ts.get("logi_fix", "乙物流|2026-08")
        sc.fix_manual_close(it, "审核甲", "记-401")
        it = ts.get("logi_fix", "乙物流|2026-08")
        self.assertEqual((it["status"], it["done_by"]), ("done", "审核甲"))
        self.assertIn("记-401", it["done_how"])
        self._fix("k2")                                                # 办结后又登记了一笔新的
        sc.fix_touch("乙物流", "2026-08", "做账丙")
        it = ts.get("logi_fix", "乙物流|2026-08")
        self.assertEqual((it["status"], it["sub"]), ("open", "2 笔要改 · 还剩 1 笔没办"))

    def test_fix_withdrawn_when_all_removed_and_no_kingdee_needed(self):
        self._fix("k1")
        sc.fix_touch("乙物流", "2026-08", "做账丙")
        self.kd.down = True                                            # 计提更正不用连金蝶
        self.assertEqual(sc.run_checks()["failed"], 0)
        with db._engine.begin() as c:
            c.execute(delete(sc.FIXT))
        sc.fix_touch("乙物流", "2026-08", "做账丙")
        self.assertEqual(ts.get("logi_fix", "乙物流|2026-08")["status"], "withdrawn")

    # ---------- 核对出错 ----------
    def test_checker_exception_is_recorded_not_raised(self):
        self._fx_logs(1)
        sc.fx_touch(2026, 9, "101", "做账丙")

        def boom(*a, **k):
            raise sc.kc.KingdeeError("查询报错(打桩)")
        sc.kc._query = boom
        r = sc.run_checks()
        it = ts.get("fx_audit", "2026-09:101")
        self.assertEqual((r["failed"], it["status"]), (1, "open"))
        self.assertIn("这次没核对成", it["check_msg"])


if __name__ == "__main__":
    unittest.main()
