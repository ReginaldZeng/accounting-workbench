# [Change Log]
# Date: 2026-10-04 | Author: Claude Opus 5.5 | Version: V2.796（数字员工办公室）
# Description: 数字员工办公室的后端核对——工位状态怎么判（没上岗 / 正常 / 出错 / 停了）、干活记录、大屏口令。
#   不 import app；db 用临时 SQLite。重点：没报到 ≠ 停了；同一个错连着出只留一条；值班表里不带出错原文；
#   大屏口令没生成过＝通道关着。
#   运行：repo 根目录 PYTHONPATH=01_Current_Deliverables/app/backend python -m unittest tests.test_worker_office -v
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "01_Current_Deliverables" / "app" / "backend"
sys.path.insert(0, str(BACKEND))

_TMP = tempfile.TemporaryDirectory(prefix="worker-office-", ignore_cleanup_errors=True)   # Windows 下 sqlite 文件退出时还被占着
os.environ.setdefault("DB_URL", "sqlite:///" + (Path(_TMP.name) / "office.sqlite").as_posix())

import db                      # noqa: E402
import worker_store as ws      # noqa: E402
from sqlalchemy import delete, update   # noqa: E402


class _Req:
    def __init__(self, tok=""):
        self.headers = {"X-Office-Token": tok} if tok else {}


class WorkerOfficeTests(unittest.TestCase):
    def setUp(self):
        with db._engine.begin() as c:
            c.execute(delete(ws.worker_run))
            c.execute(delete(ws.worker_beat))
        self._prov = list(ws.PROVIDERS)
        ws.PROVIDERS.clear()

    def tearDown(self):
        ws.PROVIDERS[:] = self._prov

    def _desk(self, key):
        return next(d for d in ws.roster()["desks"] if d["key"] == key)

    def _age_beat(self, key, minutes):
        t = (datetime.now() - timedelta(minutes=minutes)).strftime(ws._FMT)
        with db._engine.begin() as c:
            c.execute(update(ws.worker_beat).where(ws.worker_beat.c.desk == key).values(ts=t))

    def test_never_reported_is_off_not_down(self):
        d = self._desk("payreq")
        self.assertEqual(d["status"], "off")
        self.assertIn("没报到过", d["statusText"])

    def test_off_reason_is_shown(self):
        ws.beat("fx", off="自动录入开关没开")
        d = self._desk("fx")
        self.assertEqual((d["status"], d["statusText"]), ("off", "没上岗：自动录入开关没开"))
        ws.beat("fx")                                      # 开关打开后再报到 → 正常
        self.assertEqual(self._desk("fx")["status"], "ok")

    def test_stale_beat_is_down(self):
        ws.beat("payreq")
        self.assertEqual(self._desk("payreq")["status"], "ok")
        self._age_beat("payreq", 200)                      # 容忍 70 分钟
        d = self._desk("payreq")
        self.assertEqual(d["status"], "down")
        self.assertIn("3 小时没动静", d["statusText"])

    def test_daily_desk_tolerates_a_day(self):
        ws.beat("fx")
        self._age_beat("fx", 26 * 60)                      # 一天才转一圈，26 小时没报到不算停
        self.assertEqual(self._desk("fx")["status"], "ok")
        self._age_beat("fx", 60 * 60)
        self.assertEqual(self._desk("fx")["status"], "down")

    def test_last_error_marks_err_and_next_success_clears(self):
        ws.beat("inv_intake")
        ws.record("inv_intake", ok=False, summary="扫钉钉单出错", error="某供应商有限公司 的单读不出来")
        d = self._desk("inv_intake")
        self.assertEqual(d["status"], "err")
        self.assertNotIn("供应商", str(d))                  # 值班表里不带出错原文
        ws.record("inv_intake", n=3, summary="新接 3 张钉钉单")
        d = self._desk("inv_intake")
        self.assertEqual((d["status"], d["lastText"], d["monthN"]), ("ok", "新接 3 张钉钉单", 3))

    def test_same_error_repeating_keeps_one_row(self):
        for _ in range(5):
            ws.record("payreq", ok=False, summary="扫钉钉请款单出错", error="连接超时")
        ws.record("payreq", ok=False, summary="扫钉钉请款单出错", error="换了个错")
        r = ws.runs("payreq", show_error=True)["runs"]
        self.assertEqual([x["error"] for x in r], ["换了个错", "连接超时"])

    def test_runs_hide_error_text_unless_admin(self):
        ws.record("fx", ok=False, summary="9 月被闸门拦下，没写", error="偏离闸门：美元 7.30 vs 7.10")
        self.assertEqual(ws.runs("fx")["runs"][0]["error"], "出错原因只有管理员能看")
        self.assertIn("偏离闸门", ws.runs("fx", show_error=True)["runs"][0]["error"])
        self.assertIsNone(ws.runs("nope"))

    def test_month_total_and_kpi(self):
        ws.beat("fx"); ws.beat("payreq")
        ws.record("fx", n=8, summary="9 月写入 8 条")
        ws.record("payreq", n=2, summary="新接 2 张请款单")
        ws.record("payreq", n=3, summary="新接 3 张请款单")
        old = (datetime.now() - timedelta(days=70)).strftime(ws._FMT)
        ws.record("payreq", n=100, summary="两个多月前的")
        with db._engine.begin() as c:
            c.execute(update(ws.worker_run).where(ws.worker_run.c.summary == "两个多月前的").values(ts=old))
        k = ws.roster()["kpi"]
        self.assertEqual((k["on"], k["monthN"], k["err"], k["down"]), (2, 13, 0, 0))
        self.assertEqual((k["monthRuns"], k["todayRuns"]), (3, 3))      # 干活次数：一笔记录算一次（两个多月前那笔不算）
        self.assertEqual(self._desk("payreq")["monthN"], 5)

    def test_static_desk_and_provider(self):
        self.assertEqual(self._desk("contract")["status"], "off")
        ws.PROVIDERS.append(lambda: [{"key": "pull_x", "name": "某取件机", "what": "搬文件", "cadence": "每分钟",
                                      "handoff": "送达收件人", "status": "down", "statusText": "2 天没动静", "lastAt": "10-02 18:03", "lastText": ""}])
        ws.PROVIDERS.append(lambda: 1 / 0)                 # 外部工位取数炸了不拖垮整张值班表
        r = ws.roster()
        self.assertEqual(self._desk("pull_x")["status"], "down")
        self.assertEqual(r["kpi"]["down"], 1)

    def test_screen_can_tell_new_work_from_a_patrol(self):
        # 大屏靠这两个时间戳判断：干活记录变了＝来了新活（弹派工单）；只有报到变了＝巡了一圈没新活（不弹）
        ws.beat("inv_intake")
        ws.record("inv_intake", n=1, summary="新接 1 张钉钉单")
        old = (datetime.now() - timedelta(minutes=20)).strftime(ws._FMT)
        with db._engine.begin() as c:
            c.execute(update(ws.worker_run).values(ts=old))
        self._age_beat("inv_intake", 20)
        a = self._desk("inv_intake")
        self.assertEqual((a["lastTs"], a["beatTs"]), (old, old))
        ws.beat("inv_intake")                              # 又巡了一圈，没有新活
        b = self._desk("inv_intake")
        self.assertEqual(b["lastTs"], a["lastTs"])
        self.assertNotEqual(b["beatTs"], a["beatTs"])
        ws.record("inv_intake", n=2, summary="新接 2 张钉钉单")
        self.assertNotEqual(self._desk("inv_intake")["lastTs"], a["lastTs"])
        self.assertEqual(self._desk("contract")["lastTs"], "")

    def test_refs_are_kept_short_and_shown_in_feed_and_runs(self):
        # 单号：去空去重、最多 6 个；大屏的「刚干完的活」和登录后的干活记录里都带得出来
        ws.record("inv_intake", n=8, summary="已接入 8 张钉钉单",
                  refs=["2026100400%02d" % i for i in range(8)] + ["", None, "202610040000"])
        f = ws.roster()["feed"][0]
        self.assertEqual((f["key"], f["n"], f["refLabel"]), ("inv_intake", 8, "钉钉单号"))
        self.assertEqual(f["refs"], ["2026100400%02d" % i for i in range(6)])
        self.assertEqual(ws.runs("inv_intake")["runs"][0]["refs"], f["refs"])
        ws.record("fx", n=8, summary="9 月已写入 8 条")      # 没有单号的活
        self.assertEqual(ws.roster()["feed"][0]["refs"], [])

    def test_next_round_is_reported_only_for_desks_on_duty(self):
        # 定时任务排队：任务报到时说「下一轮还有多久」→ 值班表带出 nextTs；没上岗、停了的不排队；不传就沿用上次报的
        with db._engine.begin() as c:
            c.execute(delete(ws.worker_next))
        ws.beat("payreq", next_in=20 * 60)
        r = ws.roster()
        d = next(x for x in r["desks"] if x["key"] == "payreq")
        gap = (datetime.strptime(d["nextTs"], ws._FMT) - datetime.strptime(r["nowTs"], ws._FMT)).total_seconds()
        self.assertTrue(20 * 60 - 5 <= gap <= 20 * 60 + 5, gap)
        ws.beat("payreq")                                   # 不带 next_in：不动上次报的
        self.assertEqual(self._desk("payreq")["nextTs"], d["nextTs"])
        ws.beat("fx", off="自动录入开关没开", next_in=3600)   # 没上岗的不排队
        self.assertEqual(self._desk("fx")["nextTs"], "")
        self._age_beat("payreq", 200)                       # 停了的不排队
        self.assertEqual((self._desk("payreq")["status"], self._desk("payreq")["nextTs"]), ("down", ""))
        ws.beat("todo_check", next_in="不是数字")            # 入参再怪也不往外抛
        self.assertEqual(self._desk("todo_check")["nextTs"], "")
        # 只有按钟点跑的算定时任务（汇率、催票）；每隔二十分钟去看一眼的是巡检，大屏不把它们排进定时任务
        self.assertEqual(sorted(x["key"] for x in ws.roster()["desks"] if x.get("sched")), ["bp_ads", "bp_flash", "bp_sentinel", "fx", "inv_remind"])

    def _bp(self, runs=None, env_on=True, running=True, flash_on=True):
        # 一份 BP「系统设置 › 定时任务」接口的样子（照它 routers/ops.py schedule_get 的返回写的）
        jobs = [{"id": "sentinel-am", "kind": "sentinel", "name": "驾驶舱值守 · 早", "label": "早", "time": "09:00",
                 "when": {"type": "weekly", "dows": [1, 2, 3, 4, 5]}, "enabled": True, "nextDue": "2026-10-06 09:00"},
                {"id": "sentinel-pm", "kind": "sentinel", "name": "驾驶舱值守 · 下午", "label": "下午", "time": "14:00",
                 "when": {"type": "weekly", "dows": [1, 2, 3, 4, 5, 6, 7]}, "enabled": True, "nextDue": "2026-10-05 14:00"},
                {"id": "flash-week", "kind": "flash", "flashKind": "week", "name": "业绩快报 · 周报推送", "time": "09:10",
                 "when": {"type": "weekly", "dows": [1]}, "enabled": flash_on, "nextDue": "2026-10-12 09:10"},
                {"id": "flash-quarter", "kind": "flash", "flashKind": "quarter", "name": "业绩快报 · 季报推送", "time": "09:30",
                 "when": {"type": "monthly", "dom": 1, "months": [1, 4, 7, 10]}, "enabled": flash_on, "nextDue": "2027-01-01 09:30"}]
        return {"jobs": jobs, "runs": runs or {}, "scheduler": {"envOn": env_on, "running": running}}

    def test_bp_jobs_become_two_desks_with_their_own_queue(self):
        import office_bp_bridge as bp
        with db._engine.begin() as c:
            c.execute(delete(ws.worker_next))
        old = {"flash-week": {"lastKey": "2026-09-28 09:10", "status": "ok", "note": "39周 → 3/3 个目标已推"}}
        seen = bp.sync(self._bp(old), None)                 # 第一次连上：只记进度，不把以前跑的补记成刚干完
        self.assertEqual(seen, {"flash-week": "2026-09-28 09:10|ok", "sentinel-am": "", "sentinel-pm": "", "flash-quarter": ""})
        r = ws.roster()
        self.assertEqual([f for f in r["feed"] if f["key"].startswith("bp_")], [])
        self.assertEqual((self._desk("bp_sentinel")["status"], self._desk("bp_flash")["status"]), ("ok", "ok"))
        q = [(x["key"], x["name"], x["cadence"], x["nextTs"]) for x in r["queue"] if x["key"].startswith("bp_")]
        self.assertEqual(q, [("bp_sentinel", "驾驶舱值守 · 下午", "每天 14:00", "2026-10-05 14:00:00"),
                             ("bp_sentinel", "驾驶舱值守 · 早", "工作日 09:00", "2026-10-06 09:00:00"),
                             ("bp_flash", "业绩快报 · 周报", "每周一 09:10", "2026-10-12 09:10:00"),
                             ("bp_flash", "业绩快报 · 季报", "1、4、7、10 月 1 日 09:30", "2027-01-01 09:30:00")])
        # 周报推完（3 个群里 1 个没推出去）、值守起了一次、季报还在推 → 记两笔；群名只进出错原文，不上大屏
        runs = {"flash-week": {"lastKey": "2026-10-05 09:10", "status": "failed", "note": "40周 → 2/3 个目标已推；失败：华东大区群：超时"},
                "sentinel-pm": {"lastKey": "2026-10-05 14:00", "status": "ok", "note": "已起子进程 PID 123"},
                "flash-quarter": {"lastKey": "2026-10-01 09:30", "status": "running", "note": "推送中…"}}
        seen = bp.sync(self._bp(runs), seen)
        feed = {f["key"]: f for f in ws.roster()["feed"]}
        self.assertEqual((feed["bp_flash"]["text"], feed["bp_flash"]["ok"], feed["bp_flash"]["n"]), ("业绩快报 · 周报已推 2/3 个群", False, 2))
        self.assertEqual((feed["bp_sentinel"]["text"], feed["bp_sentinel"]["ok"]), ("驾驶舱值守 · 下午已开跑", True))
        self.assertNotIn("华东", str(ws.roster()))
        self.assertIn("华东大区群", ws.runs("bp_flash", show_error=True)["runs"][0]["error"])
        n = len(ws.runs("bp_flash")["runs"])
        self.assertEqual(bp.sync(self._bp(runs), seen), seen)   # 同一份结果再读一遍：不重复记
        self.assertEqual(len(ws.runs("bp_flash")["runs"]), n)

    def test_bp_ads_desk_keeps_money_off_the_screen_and_patrol_out_of_the_schedule(self):
        # 投放数据员：千川拉数 / 投放ROI快报重建 / 推送是定时任务；千川实时预警是巡检（不进定时排队、正常的一轮不记账）。
        # 重建的备注里有投放金额和 ROI——大屏、干活记录、出错原文里都不许出现。
        import office_bp_bridge as bp
        with db._engine.begin() as c:
            c.execute(delete(ws.worker_next))
        ev = {"type": "weekly", "dows": [1, 2, 3, 4, 5, 6, 7]}
        jobs = [{"id": "qianchuan-pull", "kind": "qianchuan", "name": "千川数据 · 每日拉取", "time": "08:00", "when": ev, "enabled": True, "nextDue": "2026-10-06 08:00"},
                {"id": "adroi-daily", "kind": "adroi", "name": "投放ROI快报 · 每日重建当月", "time": "08:40", "when": ev, "enabled": True, "nextDue": "2026-10-06 08:40"},
                {"id": "adroi-push", "kind": "adroipush", "name": "投放ROI快报 · 定时推送", "time": "08:50", "when": ev, "enabled": True, "nextDue": "2026-10-06 08:50"},
                {"id": "qc-watch", "kind": "qcwatch", "name": "千川实时预警（余额 / 爆量）", "time": "08:05", "enabled": True, "nextDue": "2026-10-05 13:05",
                 "when": {"type": "interval", "every": 30, "start": "08:05", "end": "23:35", "dows": [1, 2, 3, 4, 5, 6, 7]}},
                {"id": "x-new", "kind": "somethingnew", "name": "BP 以后加的新任务", "time": "07:00", "when": ev, "enabled": True, "nextDue": "2026-10-06 07:00"}]
        sch = {"envOn": True, "running": True}
        old = {"qianchuan-pull": {"lastKey": "2026-10-05 08:00", "status": "ok", "note": "3/3 账户、128 行"},
               "adroi-daily": {"lastKey": "2026-10-05 08:40", "status": "failed", "note": "10-01~10-04 投放 12.34 万 · ROI 2.15 · 要处理 3 条；告警 1 条：某某"},
               "qc-watch": {"lastKey": "2026-10-05 12:35", "status": "ok", "note": "余额 5.6 万，正常"}}
        # 办公室以前只认识值守和快报：这四条是刚接进来的，今天早上已经跑过的不补记
        seen = bp.sync({"jobs": jobs, "runs": old, "scheduler": sch}, {"flash-week": "2026-09-28 09:10|ok"})
        self.assertEqual([f for f in ws.roster()["feed"] if f["key"] == "bp_ads"], [])
        self.assertEqual(self._desk("bp_ads")["status"], "ok")
        q = [(x["name"], x["cadence"], x["sched"]) for x in ws.roster()["queue"] if x["key"] == "bp_ads"]
        self.assertEqual(q, [("千川实时预警", "08:05–23:35 每 30 分钟", False), ("千川数据拉取", "每天 08:00", True),
                             ("投放ROI快报 · 重建", "每天 08:40", True), ("投放ROI快报 · 推送", "每天 08:50", True)])
        new = {"qianchuan-pull": {"lastKey": "2026-10-06 08:00", "status": "ok", "note": "3/3 账户、131 行"},
               "adroi-daily": {"lastKey": "2026-10-06 08:40", "status": "failed", "note": "10-01~10-05 投放 15.67 万 · ROI 2.08 · 要处理 4 条；告警 2 条：某某"},
               "adroi-push": {"lastKey": "2026-10-06 08:50", "status": "ok", "note": "10-01~10-05 完整版 → 2/2 个对象已推"},
               "qc-watch": {"lastKey": "2026-10-06 08:05", "status": "ok", "note": "余额 4.9 万，正常"}}
        seen = bp.sync({"jobs": jobs, "runs": new, "scheduler": sch}, seen)
        texts = [r["summary"] for r in ws.runs("bp_ads", show_error=True)["runs"]]
        self.assertEqual(sorted(texts), sorted(["千川数据已拉取 3/3 个账户", "投放ROI快报已重建，要处理 4 条，有 2 条告警", "投放ROI快报已推 2/2 个对象"]))
        everything = str(ws.roster()) + str(ws.runs("bp_ads", show_error=True))
        for money in ("15.67", "2.08", "4.9 万", "12.34"):
            self.assertNotIn(money, everything)
        self.assertEqual(self._desk("bp_ads")["status"], "err")          # 重建有告警 → 上一轮出错
        bad = dict(new, **{"qc-watch": {"lastKey": "2026-10-06 08:35", "status": "failed", "note": "Traceback …"}})
        bp.sync({"jobs": jobs, "runs": bad, "scheduler": sch}, seen)     # 巡检出错才记一笔
        self.assertEqual(ws.runs("bp_ads")["runs"][0]["summary"], "千川实时预警这一轮没查成")

    def test_bp_scheduler_off_or_jobs_disabled_is_off_not_down(self):
        import office_bp_bridge as bp
        bp.sync(self._bp(env_on=False), {})
        self.assertEqual((self._desk("bp_flash")["status"], self._desk("bp_flash")["statusText"]), ("off", "没上岗：BP 工作台的定时调度没开"))
        bp.sync(self._bp(flash_on=False), {})
        self.assertEqual(self._desk("bp_sentinel")["status"], "ok")
        self.assertEqual(self._desk("bp_flash")["statusText"], "没上岗：这类定时任务在 BP 里都停用了")
        self.assertEqual([x for x in ws.roster()["queue"] if x["key"] == "bp_flash"], [])
        self._age_beat("bp_sentinel", 30)                   # BP 连不上（没人替它报到）超过十分钟 → 停了
        bp.sync(self._bp(running=False), {})
        self.assertEqual(self._desk("bp_sentinel")["status"], "down")

    def test_record_and_beat_never_raise(self):
        ws.record("fx", n="不是数字", summary=None)          # 入参再怪也不往外抛
        ws.beat(None)

    def test_screen_token_closed_until_generated(self):
        from routers import office
        db.set_setting(office._TOKEN_KEY, {}, "t")
        self.assertFalse(office.screen_token_ok(_Req()))
        self.assertFalse(office.screen_token_ok(_Req("")))
        db.set_setting(office._TOKEN_KEY, {"token": "abc123"}, "t")
        self.assertTrue(office.screen_token_ok(_Req("abc123")))
        self.assertFalse(office.screen_token_ok(_Req("abc124")))
        db.set_setting(office._TOKEN_KEY, {"token": ""}, "t")   # 停用
        self.assertFalse(office.screen_token_ok(_Req("abc123")))


if __name__ == "__main__":
    unittest.main()
