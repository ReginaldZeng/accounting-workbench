# -*- coding: utf-8 -*-
# V2.908 「保存到金蝶」做到一半接着做——拿一个假的金蝶，把整条流程在每一步掐断一次，再点一次，看最后的账对不对。
#   样本＝2026-10-10 17:42 被自动部署重启掐断的那张：顺丰冷运·深圳星期九 622.00，付款单 FKD00008357(内码 108743)。
#   要守住的三条：付款单只审核一次；分录只补一次；凭证做完系统一定记成「已做账」。
#   假金蝶只认这条流程用到的几个接口(查付款单/查凭证/看凭证/保存/提交/审核)，凭证生成、行号、状态流转照真金蝶的样子来。
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine

import busy
import kingdee_client as kc
from core import db
from routers import logistics_voucher as R

# 这组测试会清空留痕表和请款单表：只在自己建的临时库里跑。不管这台机器的 DB_URL 指着哪(本地开发库、甚至真库)，
# 跑之前把 db 模块的连接换成临时库、跑完换回去——setUp 里再核一遍，连的不是临时库就直接报错，一行都不删。
_TMP = tempfile.mkdtemp()

INST, FID, BILL, AMT = "inst-sf-0929", 108743, "FKD00008357", 622.00
SUP, BOOK = "物流运输服务011", "深圳市星期九食品科技有限公司"
ADD = [{"block": "核销", "expl": "26440000000000000001核销8/101#计提顺丰冷运8月运费", "acct": "2221.01.06", "dr": 51.36, "cr": 0.0, "dims": {}},
       {"block": "核销", "expl": "核销8/101#计提顺丰冷运8月运费", "acct": "2221.01.07", "dr": 0.0, "cr": 51.36, "dims": {"sup_code": SUP}}]
PAY = [{"block": "支付", "expl": "陈慧娴提起支付顺丰冷运8月运费", "acct": "2241.02", "dr": AMT, "cr": 0.0, "dims": {"sup_code": SUP}},
       {"block": "支付", "expl": "陈慧娴提起支付顺丰冷运8月运费", "acct": "1002.03", "dr": 0.0, "cr": AMT, "dims": {}}]


class Killed(BaseException):
    """后端被重启掐断。用 BaseException：真被掐断时任何 except Exception 都来不及执行。"""


class FakeKingdee:
    def __init__(self, auto_voucher=True):
        self.bill = {"单号": "", "状态": "Z", "金额": AMT, "码": SUP, "日期": "2026-09-29T00:00:00", "审核时间": None}
        self.vouchers = {}            # vid -> {no, status, ents:[{Id, acct, dr, cr, sup, expl}], created}
        self.auto_voucher = auto_voucher
        self.calls = []               # (接口, 单据)
        self.kill_after = None        # (接口, 单据, 第几次)：这次调用金蝶照做，回来的路上后端被掐断
        self.kill_before = None       # 这次调用还没发出去就被掐断
        self._eid = 100

    # ---- 人在金蝶里做的动作 ----
    def gen_voucher(self, sup=SUP, amount=AMT, status="A", no=None):
        vid = 9000 + len(self.vouchers) + 1
        self._eid += 2
        self.vouchers[vid] = {"no": no or str(330 + len(self.vouchers) + 1), "status": status, "created": "2026-10-10 17:41:49",
                              "ents": [{"Id": self._eid - 1, "acct": "2241.02", "dr": amount, "cr": 0.0, "sup": sup, "expl": "金蝶原摘要"},
                                       {"Id": self._eid, "acct": "1002.03", "dr": 0.0, "cr": amount, "sup": "", "expl": "金蝶原摘要"}]}
        return vid

    # ---- 接口 ----
    def n(self, op, form):
        return sum(1 for c in self.calls if c == (op, form))

    def query(self, s, conf, form, fields, flt, order=""):
        if form == "AP_PAYBILL":
            return [dict(self.bill)]
        assert form == "GL_VOUCHER", form
        rows = []
        for vid, v in self.vouchers.items():
            for e in v["ents"]:
                rows.append({"id": vid, "号": v["no"], "状态": v["status"], "科目": e["acct"], "借": e["dr"], "贷": e["cr"], "供应商码": e["sup"]})
        if "FVOUCHERID in" in flt:
            ids = {int(x) for x in flt.split("(")[1].split(")")[0].split(",")}
            rows = [r for r in rows if r["id"] in ids]
        else:                         # 按 贷 1002 = 金额 找：只回银行存款那一行
            assert BOOK in flt and "2026-09-29" in flt and "FCREDIT=622.00" in flt, flt
            rows = [r for r in rows if r["科目"].startswith("1002") and abs(r["贷"] - AMT) < 0.005]
        keys = [lab for _f, lab in fields]
        return [{k: r.get(k) for k in keys} for r in rows]

    def post(self, s, conf, svc, params):
        op = svc.split(".")[-3]
        form, body = params[0], json.loads(params[1])
        key = (op, form)
        nth = self.n(op, form) + 1
        if self.kill_before == key + (nth,):
            raise Killed()
        self.calls.append(key)
        res = self._do(op, form, body)
        if self.kill_after == key + (nth,):
            raise Killed()

        class Resp:
            def json(self_):
                return res
        return Resp()

    def _ok(self, **kw):
        return {"Result": dict({"ResponseStatus": {"IsSuccess": True}}, **kw)}

    def _do(self, op, form, body):
        if form == "AP_PAYBILL":
            if op == "Save":
                self.bill.update({"单号": BILL, "状态": "A"})
            elif op == "Submit":
                self.bill["状态"] = "B"
            elif op == "Audit":
                assert self.bill["状态"] == "B", "金蝶不会审核一张没提交的付款单"
                self.bill.update({"状态": "C", "审核时间": "2026-10-10T17:41:46.303"})
                if self.auto_voucher:
                    self.gen_voucher()
            return self._ok()
        v = self.vouchers[int(body.get("Id") or body.get("Ids") or body["Model"]["FVOUCHERID"])]
        if op == "View":
            return self._ok(Result={"Id": 1, "VOUCHERGROUPNO": v["no"], "DocumentStatus": v["status"],
                                    "DEBITTOTAL": round(sum(e["dr"] for e in v["ents"]), 2), "FCREDITTOTAL": round(sum(e["cr"] for e in v["ents"]), 2),
                                    "GL_VOUCHERENTRY": [{"Id": e["Id"], "FACCOUNTID": {"Number": e["acct"]}} for e in v["ents"]]})
        if op == "Save":
            assert v["status"] in ("A", "Z"), "金蝶不让改已提交的凭证"
            for x in body["Model"]["FEntity"]:
                if "FEntryID" in x:
                    next(e for e in v["ents"] if e["Id"] == x["FEntryID"])["expl"] = x["FEXPLANATION"]
                else:
                    self._eid += 1
                    v["ents"].append({"Id": self._eid, "acct": x["FACCOUNTID"]["FNumber"], "dr": x["FDEBIT"], "cr": x["FCREDIT"],
                                      "sup": ((x.get("FDetailID") or {}).get("FDETAILID__FFLEX4") or {}).get("FNumber", ""), "expl": x["FEXPLANATION"]})
            return self._ok()
        if op == "Submit":
            v["status"] = "B"
            return self._ok()
        raise AssertionError("假金蝶没有这个接口：%s %s" % (op, form))


_PATCHED = [(kc, "login"), (kc, "_query"), (kc, "_post"), (R.time, "sleep"), (R, "_preview_data"), (R, "_fixes"),
            (R.todo_scenes, "voucher_touch"), (R.todo_scenes, "fix_touch")]
_ORIG = [(o, n, getattr(o, n)) for o, n in _PATCHED]


class TestPostResume(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.real_engine = db._engine
        cls.engine = create_engine("sqlite:///" + os.path.join(_TMP, "t.db").replace(os.sep, "/"), future=True)
        db._md.create_all(cls.engine)
        db._engine = cls.engine

    @classmethod
    def tearDownClass(cls):
        db._engine = cls.real_engine
        cls.engine.dispose()

    def setUp(self):
        assert db._engine is self.engine and str(self.engine.url).startswith("sqlite") and _TMP.replace(os.sep, "/") in str(self.engine.url)
        self.kd = FakeKingdee()
        os.environ["WB_DRAIN_FILE"] = os.path.join(_TMP, ".deploy_draining")
        busy._holds.clear()
        busy._last_write[0] = 0.0          # 别的测试刚「写过金蝶」留下的 20 秒占线不带进来
        kc.login = lambda s=None, conf=None: (object(), {})
        kc._query = self.kd.query
        kc._post = self.kd.post
        R.time.sleep = lambda x: None
        R._preview_data = self.preview
        R._fixes = lambda *a, **k: {}
        R.todo_scenes.voucher_touch = lambda *a, **k: None
        R.todo_scenes.fix_touch = lambda *a, **k: None
        for k in (R._POSTED_KEY, R._PENDING_KEY):
            db.set_setting(k, {}, "t")
        with db._engine.begin() as c:
            c.execute(R.text("delete from audit_log"))
            c.execute(R.text("delete from logistics_payreq"))
            c.execute(R.text("insert into logistics_payreq(inst_id, kd_paid) values(:i, :k)"), {"i": INST, "k": "2026-09-29|Z|%d" % FID})
        self.add = [dict(l) for l in ADD]
        try:
            os.remove(busy.drain_path())
        except OSError:
            pass

    def tearDown(self):
        for o, n, f in _ORIG:
            setattr(o, n, f)
        os.environ.pop("WB_DRAIN_FILE", None)

    def preview(self, inst, self_vno=None, _nest=False):
        posted = (db.get_setting(R._POSTED_KEY, None) or {}).get(inst)
        return {"req": {"inst": inst, "status": "booked" if posted else "ready", "posted": posted, "bill_id": str(FID), "code": SUP, "amount": AMT,
                        "subject": "深圳星期九", "subject_full": BOOK, "payee": "上海顺丰冷运供应链有限公司", "carrier": "顺丰冷运", "period": "2026-08"},
                "plan": {"status": "ok", "msgs": [], "pay_only": ""}, "voucher": {"lines": self.add + PAY, "date": "2026-09-29"},
                "xout": [], "adjust": [], "carry": [], "accruals": [], "xbook": []}, 200

    # ---- 断言用 ----
    def posted(self):
        return (db.get_setting(R._POSTED_KEY, None) or {}).get(INST)

    def pending(self):
        return (db.get_setting(R._PENDING_KEY, None) or {}).get(INST)

    def audits(self, action):
        with db._engine.connect() as c:
            return c.execute(R.text("select count(*) from audit_log where action=:a"), {"a": action}).scalar()

    def assert_done_once(self, res):
        """账做完了、而且只做了一遍。"""
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.kd.n("Audit", "AP_PAYBILL"), 1, "付款单只能审核一次")
        self.assertEqual(len(self.kd.vouchers), 1)
        v = next(iter(self.kd.vouchers.values()))
        self.assertEqual(sorted((e["acct"], e["dr"], e["cr"]) for e in v["ents"]),
                         sorted((l["acct"], l["dr"], l["cr"]) for l in ADD + PAY), "凭证里正好是支付两行＋要补的两行，一行不多")
        self.assertEqual(v["status"], "B", "凭证提交了、没审核")
        self.assertEqual({e["expl"] for e in v["ents"] if e["acct"] in ("2241.02", "1002.03")}, {PAY[0]["expl"]}, "支付两行的摘要改了")
        rec = self.posted()
        self.assertEqual((rec["bill_no"], rec["vno"], rec["lines"], rec["submitted"]), (BILL, v["no"], 4, True))
        self.assertEqual((rec["dr"], rec["cr"]), (673.36, 673.36))
        self.assertIsNone(self.pending(), "做完了，「做到一半」的登记要清掉")
        self.assertEqual(self.audits("物流付款做账-写入金蝶凭证"), 1)
        with db._engine.connect() as c:
            self.assertEqual(c.execute(R.text("select kd_paid from logistics_payreq where inst_id=:i"), {"i": INST}).scalar(), "2026-09-29|C|%d" % FID)

    def killed_at(self, **kw):
        for k, v in kw.items():
            setattr(self.kd, k, v)
        with self.assertRaises(Killed):
            R._post(INST, "曾禹锡")
        self.kd.kill_after = self.kd.kill_before = None
        self.assertEqual(busy.snapshot()["what"], [], "被掐断后牌子不能留着")

    # ---- 正常路 ----
    def test_straight_through(self):
        res = R._post(INST, "曾禹锡")
        self.assert_done_once(res)
        self.assertEqual(self.kd.n("Save", "GL_VOUCHER"), 1)
        self.assertEqual(self.audits("物流付款做账-系统审核付款单"), 1)
        again = R._post(INST, "曾禹锡")
        self.assertFalse(again["ok"])
        self.assertIn("已经写过金蝶", again["msg"])

    def test_busy_sign_is_up_while_writing(self):
        seen = []
        real = self.kd.post
        kc._post = lambda *a: (seen.append(busy.snapshot()["busy"]), real(*a))[1]
        R._post(INST, "曾禹锡")
        self.assertTrue(seen and all(seen), "整段保存期间都得挂着占线")
        self.assertEqual(busy.snapshot()["what"], [])

    def test_draining_refuses_before_touching_kingdee(self):
        open(busy.drain_path(), "w").close()
        res = R._post(INST, "曾禹锡")
        self.assertEqual((res["ok"], res.get("draining")), (False, True))
        self.assertIn("系统正在更新", res["msg"])
        self.assertEqual(self.kd.calls, [], "系统准备重启时，一个请求都不能发给金蝶")
        self.assertIsNone(self.pending())
        os.remove(busy.drain_path())
        self.assert_done_once(R._post(INST, "曾禹锡"))

    # ---- 在每一步掐断，再点一次 ----
    def test_killed_right_after_audit(self):
        """2026-10-10 那次：付款单刚审核完就被掐断。"""
        self.killed_at(kill_after=("Audit", "AP_PAYBILL", 1))
        self.assertEqual(self.kd.bill["状态"], "C")
        self.assertIsNone(self.posted())
        self.assertEqual(self.pending()["bill_no"], BILL, "审核前就记了一笔，所以认得这是系统审核的")
        res = R._post(INST, "曾禹锡")
        self.assert_done_once(res)
        self.assertIn("接着做", res["steps"][0])

    def test_killed_before_audit_reaches_kingdee(self):
        """记了「要审核」但审核没发出去：付款单还是没审核，再点照常从审核做起。"""
        self.killed_at(kill_before=("Audit", "AP_PAYBILL", 1))
        self.assertEqual(self.kd.bill["状态"], "B")
        self.assert_done_once(R._post(INST, "曾禹锡"))

    def test_killed_while_reading_the_new_voucher(self):
        self.killed_at(kill_after=("View", "GL_VOUCHER", 1))
        self.assert_done_once(R._post(INST, "曾禹锡"))

    def test_killed_before_amend_reaches_kingdee(self):
        self.killed_at(kill_before=("Save", "GL_VOUCHER", 1))
        self.assertEqual(len(next(iter(self.kd.vouchers.values()))["ents"]), 2)
        self.assert_done_once(R._post(INST, "曾禹锡"))
        self.assertEqual(self.kd.n("Save", "GL_VOUCHER"), 1)

    def test_killed_right_after_amend(self):
        """分录已经补进金蝶、系统还没记：再点绝不能补第二遍。"""
        self.killed_at(kill_after=("Save", "GL_VOUCHER", 1))
        self.assertEqual(len(next(iter(self.kd.vouchers.values()))["ents"]), 4)
        res = R._post(INST, "曾禹锡")
        self.assert_done_once(res)
        self.assertEqual(self.kd.n("Save", "GL_VOUCHER"), 1, "没有再发第二次保存")
        self.assertTrue(any("没有重复补" in x for x in res["steps"]), res["steps"])

    def test_killed_right_after_amend_and_preview_has_drifted(self):
        """补完分录后重算出来的分录可能变样(自己补的那几行被当成新的计提/红冲)：拿当时发出去的那几行对，照样认得、照样做完。"""
        self.killed_at(kill_after=("Save", "GL_VOUCHER", 1))
        self.add = [dict(ADD[0], dr=99.99), dict(ADD[1], cr=99.99)]
        res = R._post(INST, "曾禹锡")
        self.assert_done_once(res)
        self.assertEqual(self.kd.n("Save", "GL_VOUCHER"), 1)

    def test_killed_right_after_submit(self):
        """凭证提交了、系统还没记：再点只补记录，不再提交。"""
        self.killed_at(kill_after=("Submit", "GL_VOUCHER", 1))
        res = R._post(INST, "曾禹锡")
        self.assert_done_once(res)
        self.assertEqual((self.kd.n("Save", "GL_VOUCHER"), self.kd.n("Submit", "GL_VOUCHER")), (1, 1))
        self.assertIn("只补了系统里的做账记录", res["steps"][-1])

    def test_every_single_cut_point_recovers(self):
        """穷举：流程里每一次发给金蝶的调用，分别在「发出前」「回来后」掐断一次，再点都要做完且只做一遍。"""
        R._post(INST, "曾禹锡")
        script = list(self.kd.calls)
        self.assertEqual(script, [("Save", "AP_PAYBILL"), ("Submit", "AP_PAYBILL"), ("Audit", "AP_PAYBILL"), ("View", "GL_VOUCHER"),
                                  ("Save", "GL_VOUCHER"), ("Submit", "GL_VOUCHER"), ("View", "GL_VOUCHER")])
        for i, (op, form) in enumerate(script):
            nth = sum(1 for c in script[:i + 1] if c == (op, form))
            for when in ("kill_before", "kill_after"):
                with self.subTest(cut="%s %s %s 第%d次" % (when, op, form, nth)):
                    self.setUp()
                    self.killed_at(**{when: (op, form, nth)})
                    self.assert_done_once(R._post(INST, "曾禹锡"))

    def test_killed_twice(self):
        self.killed_at(kill_after=("Audit", "AP_PAYBILL", 1))
        self.killed_at(kill_after=("Save", "GL_VOUCHER", 1))
        self.assert_done_once(R._post(INST, "曾禹锡"))

    # ---- 金蝶没出凭证(深圳星期九那张的真实情况) ----
    def test_no_voucher_then_human_generates_it(self):
        self.kd.auto_voucher = False
        res = R._post(INST, "曾禹锡")
        self.assertFalse(res["ok"])
        self.assertIn("再点一次", res["msg"])
        self.assertEqual(self.kd.bill["状态"], "C")
        res = R._post(INST, "曾禹锡")                      # 还没人去点凭证生成：告诉人去哪点，什么都不动
        self.assertFalse(res["ok"])
        self.assertIn("凭证生成", res["msg"])
        self.assertEqual(self.kd.n("Audit", "AP_PAYBILL"), 1)
        self.kd.gen_voucher()                              # 出纳在金蝶点了「凭证生成」
        self.assert_done_once(R._post(INST, "曾禹锡"))

    def test_resume_recognised_from_audit_trail_alone(self):
        """V2.908 上线前断掉的单(FKD00008357 本尊)：没有「做到一半」的登记，只有留痕里那条「系统审核付款单」——也要认。"""
        self.kd.bill.update({"单号": BILL, "状态": "C", "审核时间": "2026-10-10T17:41:46.303"})
        self.kd.calls.append(("Audit", "AP_PAYBILL"))
        db.audit("曾禹锡", "物流付款做账-系统审核付款单", BILL, "深圳星期九 上海顺丰冷运供应链有限公司 622.00")
        self.assertIsNone(self.pending())
        self.assertIn("凭证生成", R._post(INST, "曾禹锡")["msg"])
        self.kd.gen_voucher()
        self.assert_done_once(R._post(INST, "曾禹锡"))

    # ---- 不该碰的不碰 ----
    def test_bill_audited_by_someone_else_is_left_alone(self):
        self.kd.bill.update({"单号": BILL, "状态": "C", "审核时间": "2026-10-10T09:00:00"})
        self.kd.gen_voucher()
        res = R._post(INST, "曾禹锡")
        self.assertFalse(res["ok"])
        self.assertIn("已经被人审核过", res["msg"])
        self.assertEqual([c for c in self.kd.calls if c[0] in ("Save", "Submit", "Audit")], [])

    def test_raw_voucher_submitted_by_human_is_left_alone(self):
        self.killed_at(kill_after=("Audit", "AP_PAYBILL", 1))
        next(iter(self.kd.vouchers.values()))["status"] = "B"       # 有人把金蝶原样的两行凭证直接提交了
        res = R._post(INST, "曾禹锡")
        self.assertFalse(res["ok"])
        self.assertIn("撤销提交", res["msg"])
        self.assertEqual(self.kd.n("Save", "GL_VOUCHER"), 0)
        self.assertIsNone(self.posted())

    def test_voucher_edited_by_human_is_left_alone(self):
        self.killed_at(kill_after=("Audit", "AP_PAYBILL", 1))
        v = next(iter(self.kd.vouchers.values()))
        v["ents"].append({"Id": 999, "acct": "6601", "dr": 10.0, "cr": 0.0, "sup": "", "expl": "人手加的"})
        res = R._post(INST, "曾禹锡")
        self.assertFalse(res["ok"])
        self.assertIn("对不上", res["msg"])
        self.assertEqual(self.kd.n("Save", "GL_VOUCHER"), 0)

    def test_two_same_day_same_amount_vouchers(self):
        """同一天同金额的付款凭证有两张：按往来单位认出自己那张；认不出就不猜。"""
        self.killed_at(kill_after=("Audit", "AP_PAYBILL", 1))
        other = self.kd.gen_voucher(sup="物流运输服务027")
        res = R._post(INST, "曾禹锡")
        self.assertTrue(res["ok"], res)
        self.assertEqual(len(self.kd.vouchers[other]["ents"]), 2, "别家的那张一行没动")
        self.setUp()
        self.killed_at(kill_after=("Audit", "AP_PAYBILL", 1))
        self.kd.gen_voucher()                                       # 同一家、同一天、同金额又一张
        res = R._post(INST, "曾禹锡")
        self.assertFalse(res["ok"])
        self.assertIn("分不清", res["msg"])
        self.assertEqual(self.kd.n("Save", "GL_VOUCHER"), 0)

    def test_audit_rejected_by_kingdee_is_not_half_done(self):
        real = self.kd._do

        def do(op, form, body):
            if (op, form) == ("Audit", "AP_PAYBILL"):
                return {"Result": {"ResponseStatus": {"IsSuccess": False, "Errors": [{"Message": "我方银行账号必录"}]}}}
            return real(op, form, body)
        self.kd._do = do
        res = R._post(INST, "曾禹锡")
        self.assertIn("付款单审核失败：我方银行账号必录", res["msg"])
        self.assertIsNone(self.pending(), "金蝶明确说没审核成，不算做到一半")

    def test_amend_rejected_then_retry(self):
        """补分录被金蝶拒了(比如核算维度没填全)：凭证还是原样，问题解决后再点一次接着补。"""
        real, fail = self.kd._do, [True]

        def do(op, form, body):
            if (op, form) == ("Save", "GL_VOUCHER") and fail[0]:
                return {"Result": {"ResponseStatus": {"IsSuccess": False, "Errors": [{"Message": "费用项目必录"}]}}}
            return real(op, form, body)
        self.kd._do = do
        res = R._post(INST, "曾禹锡")
        self.assertIn("补分录失败：费用项目必录", res["msg"])
        self.assertIn("再点一次", res["msg"])
        fail[0] = False
        self.assert_done_once(R._post(INST, "曾禹锡"))


if __name__ == "__main__":
    unittest.main()
