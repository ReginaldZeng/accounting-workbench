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
