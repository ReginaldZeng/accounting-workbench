# -*- coding: utf-8 -*-
# V2.908 占线登记单测：自动部署重启前看的那块牌子——在写金蝶时挂得上、做完摘得掉、准备重启时新的进不来而已经开始的做得完。
import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import busy
import kingdee_client as kc


class TestBusy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.flag = os.path.join(self.tmp, ".deploy_draining")
        os.environ["WB_DRAIN_FILE"] = self.flag
        busy._holds.clear()
        busy._last_write[0] = 0.0
        busy._tls.depth = 0

    def tearDown(self):
        os.environ.pop("WB_DRAIN_FILE", None)
        try:
            os.remove(self.flag)
        except OSError:
            pass
        os.rmdir(self.tmp)

    def drain(self, age=0):
        open(self.flag, "w").close()
        if age:
            t = time.time() - age
            os.utime(self.flag, (t, t))

    def test_idle_by_default(self):
        self.assertEqual(busy.snapshot(), {"busy": False, "what": [], "draining": False})

    def test_hold_shows_and_clears(self):
        with busy.hold("物流付款做账·保存到金蝶"):
            s = busy.snapshot()
            self.assertTrue(s["busy"])
            self.assertIn("物流付款做账·保存到金蝶", s["what"][0])
        self.assertFalse(busy.snapshot()["busy"])

    def test_hold_clears_when_the_work_raises(self):
        with self.assertRaises(ValueError):
            with busy.hold("x"):
                raise ValueError("金蝶报错")
        self.assertFalse(busy.snapshot()["busy"])

    def test_draining_blocks_new_work_but_not_started_work(self):
        with busy.hold("已经开始的"):
            self.drain()                                    # 做到一半，部署脚本放了「准备重启」标记
            self.assertTrue(busy.snapshot()["busy"])        # 脚本看到占线 → 等
            with busy.hold("里面再套一步（建红冲凭证）"):      # 已经开始的要做完，里面那层不拦
                pass
            with busy.writing("写金蝶"):                     # 中间的写请求也不拦
                pass
        ran = []
        with self.assertRaises(busy.Draining):
            with busy.hold("新来的"):
                ran.append(1)
        self.assertEqual(ran, [])                           # 新来的一步都没做
        self.assertEqual([w for w in busy.snapshot()["what"] if "新来的" in w], [])     # 被拦的不留牌子

    def test_draining_blocks_other_threads(self):
        self.drain()
        got = []

        def work():
            try:
                with busy.hold("另一个线程"):
                    got.append("ran")
            except busy.Draining as e:
                got.append(str(e))
        t = threading.Thread(target=work)
        t.start()
        t.join()
        self.assertEqual(got, [busy.DRAIN_MSG])

    def test_stale_drain_flag_expires(self):
        """部署脚本放了标记后自己死了：标记过期后不能一直拦着人做账。"""
        self.drain(age=busy.DRAIN_TTL_S + 5)
        self.assertFalse(busy.draining())
        with busy.hold("照常做"):
            pass

    def test_linger_after_a_write(self):
        """两次写请求之间的空档也算占线(没有显式挂牌子的多步写入靠这个护着)。"""
        busy.wrote()
        s = busy.snapshot()
        self.assertTrue(s["busy"])
        self.assertIn("刚写过金蝶", s["what"][0])
        busy._last_write[0] = time.time() - busy.LINGER_S - 1
        self.assertFalse(busy.snapshot()["busy"])

    def test_leaked_hold_stops_blocking(self):
        """牌子漏摘了(线程卡死)：超过时限就不再挡部署，否则永远上不了线。"""
        k = busy._enter("卡死的")
        busy._holds[k]["since"] = time.time() - busy.STALE_S - 1
        self.assertFalse(busy.snapshot()["busy"])

    def test_snapshot_carries_no_business_data(self):
        with busy.hold("物流付款做账·保存到金蝶"):
            self.assertRegex(busy.snapshot()["what"][0], r"^物流付款做账·保存到金蝶（已 \d+ 秒）$")


class TestKingdeeHook(unittest.TestCase):
    """金蝶客户端自动挂牌子：写请求算、读请求不算。"""
    def setUp(self):
        busy._holds.clear()
        busy._last_write[0] = 0.0

    def test_which_calls_are_writes(self):
        for svc in (kc.SAVE_SVC, kc.SUBMIT_SVC, kc.DELETE_SVC, kc.CANCELASSIGN_SVC,
                    "Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.Audit.common.kdsvc",
                    "Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.Push.common.kdsvc",
                    "Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.Draft.common.kdsvc"):
            self.assertTrue(kc._is_write(svc), svc)
        for svc in (kc.LOGIN_SVC, kc.QUERY_SVC, kc.META_SVC, kc.VIEW_SVC, kc.GETRPT_SVC):
            self.assertFalse(kc._is_write(svc), svc)

    def _fake_session(self, seen):
        class R:
            def raise_for_status(self):
                pass

        class S:
            def post(self, url, **kw):
                seen.append(busy.snapshot()["busy"])        # 请求在路上的那一刻占不占线
                return R()
        return S()

    def test_write_call_is_busy_in_flight_and_lingers(self):
        seen = []
        kc._post(self._fake_session(seen), {"server_url": "http://kd"}, kc.SAVE_SVC, [])
        self.assertEqual(seen, [True])
        self.assertTrue(busy.snapshot()["busy"])            # 回来之后一小段时间仍算占线
        self.assertEqual(busy._holds, {})                   # 牌子本身摘了

    def test_read_call_never_busy(self):
        seen = []
        kc._post(self._fake_session(seen), {"server_url": "http://kd"}, kc.QUERY_SVC, [])
        self.assertEqual(seen, [False])
        self.assertFalse(busy.snapshot()["busy"])

    def test_bypassing_callers_still_linger(self):
        """绕开 kc._post 自己发请求、只调 count_call 的：也要算刚写过。"""
        kc.count_call("Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.Push.common.kdsvc")
        self.assertTrue(busy.snapshot()["busy"])

    def test_ec_long_push_is_busy_in_flight(self):
        """电商下推自己发请求(一次能跑几分钟)：在路上要占线，回来、报错都要摘牌。"""
        from routers import ec_douyin as E
        seen = []
        ok = self._fake_session(seen)
        ok.post = (lambda post: lambda url, **kw: type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: {}})() if post(url, **kw) else None)(ok.post)
        E._post_long(ok, {"server_url": "http://kd"}, E._PUSH_SVC, [])
        self.assertEqual(seen, [True])
        self.assertEqual(busy._holds, {})

        class Boom:
            def post(self, url, **kw):
                raise RuntimeError("断网")
        with self.assertRaises(RuntimeError):
            E._post_long(Boom(), {"server_url": "http://kd"}, E._DRAFT_SVC, [])
        self.assertEqual(busy._holds, {})


if __name__ == "__main__":
    unittest.main()
