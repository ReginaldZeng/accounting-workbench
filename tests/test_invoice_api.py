# [Change Log]
# Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家）
# Description: 发票管家核心路由（routers/invoice.py）接口测试：临时 SQLite + 临时上传目录，只挂本路由到裸 FastAPI，
#   （审查修复回归 test_16 起：锁定票夹、查重先来后到、自审、配对码一次性、审核只过看到的票、票夹不卡队列等）
#   core 用桩（x-test-user 头＝登录名，真账号真权限走 db.user_can；x-test-perms 头可临时覆盖权限）。
#   全部合成夹具（复用 kernels/test_invoice_parse 的合成数电票）；钉钉全部 mock，INV_DRY_SEND=1 绝不真发；
#   INV_WORKER_OFF=1 不起后台线程，识别/拉附件直接调 worker_step()。
#   运行：repo 根目录 PYTHONPATH=01_Current_Deliverables/app/backend python -m unittest tests.test_invoice_api -v
import json
import os
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "01_Current_Deliverables" / "app" / "backend"
sys.path.insert(0, str(BACKEND))

from fastapi import FastAPI                      # noqa: E402
from fastapi.responses import JSONResponse       # noqa: E402
from fastapi.testclient import TestClient        # noqa: E402


def _has(mod):
    try:
        __import__(mod)
        return True
    except Exception:
        return False


HAS_IMG = _has("fitz") and _has("cv2") and _has("numpy") and _has("PIL")
ENTERS = ("enter:invdesk", "enter:invlater", "enter:invaudit", "enter:invledger")


def qr(num, total, qtype="31", date="20260901"):
    """合成发票二维码（数电 20 位号码，金额＝价税合计）。"""
    return "01,%s,,%s,%.2f,%s,,ABCD" % (qtype, num, total, date)


class InvoiceApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="inv-api-")
        tmp = Path(cls.tmp.name)
        cls.env = {k: os.environ.get(k) for k in ("DB_URL", "INV_WORKER_OFF", "INV_DRY_SEND")}
        os.environ["DB_URL"] = "sqlite:///" + (tmp / "t.sqlite").as_posix()
        os.environ["INV_WORKER_OFF"] = "1"
        os.environ["INV_DRY_SEND"] = "1"
        cls.mods = {k: sys.modules.get(k) for k in ("db", "core", "routers.invoice", "routers.invoice_books")}
        for k in cls.mods:
            sys.modules.pop(k, None)
        # 同一进程里别的测试可能已把 routers.invoice(_books) 挂在包上：先摘掉，免得拿到旧模块（后补重算会延迟导入 invoice_books）
        pkg = sys.modules.get("routers")
        cls.pkg_attrs = {}
        if pkg is not None:
            for a in ("invoice", "invoice_books"):
                if hasattr(pkg, a):
                    cls.pkg_attrs[a] = getattr(pkg, a)
                    delattr(pkg, a)
        import db
        cls.db = db
        # 测试不导入 app：菜单准入点由这里注册（否则 parse_perms 会丢掉 enter:inv*）
        db.set_nav_caps_provider(lambda: [{"key": k, "label": k, "ws": "accounting", "kind": "nav", "tier": "nav",
                                           "mod": k.split(":")[1]} for k in ENTERS])

        def cur(request):
            name = request.headers.get("x-test-user")
            u = db.get_user(name) if name else None
            if u is not None and request.headers.get("x-test-perms") is not None:
                u = dict(u, perms=json.loads(request.headers["x-test-perms"]))
            return u
        core = types.ModuleType("core")
        core.db = db
        core.JSONResponse = JSONResponse
        core._current_user = cur
        core._require_perm = lambda request, cap: (lambda u: u if db.user_can(u, cap) else None)(cur(request))
        sys.modules["core"] = core
        from routers import invoice as inv
        from kernels import invoice_store as S
        from kernels import test_invoice_parse as tip
        cls.inv, cls.S, cls.tip = inv, S, tip
        inv.UPLOAD_DIR = str(tmp / "inv_uploads")
        app = FastAPI()
        app.include_router(inv.router)
        cls.c = TestClient(app)

        def mk(name, perms=None, role="normal"):
            db.create_user(name, "x-test-pwd", grp="核算组", role=role, perms={k: True for k in (perms or [])})
            ok, msg = db.change_own_pwd(name, "x-test-pwd", "x-test-pwd-2")   # 新账号 must_change_pwd=1：测试账号当已改过密
            assert ok, msg
        mk("boss", role="admin")
        mk("intern", ["enter_accounting", "enter:invdesk", "inv_intake"])
        mk("maker", ["enter_accounting", "enter:invdesk", "inv_intake", "enter:invaudit", "inv_audit"])
        mk("acct", ["enter_accounting", "enter:invaudit", "inv_audit", "enter:invdesk"])
        mk("nogate", ["enter_accounting", "inv_intake", "inv_audit", "inv_config"])   # 有动作点、没开页面
        mk("viewer", ["enter_accounting", "enter:invdesk"])
        mk("phoneguy", ["enter_accounting", "enter:invdesk", "inv_intake"])
        f = tip.FAKE
        db.set_setting("inv_config", {
            "company": [{"name": f["buyer"], "taxId": f["buyer_tid"]}],
            "people": [{"account": "intern", "dtUserid": "dt-intern", "dtName": "实习生甲", "receiver": False},
                       {"account": "maker", "dtUserid": "dt-maker", "dtName": "造单人", "receiver": True}]}, "test")
        cls.pdf = tip.build_pdf(tip._page_items_digital("split"))

    @classmethod
    def tearDownClass(cls):
        cls.c.close()
        cls.db._engine.dispose()
        for k, v in cls.mods.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        pkg = sys.modules.get("routers")
        if pkg is not None:
            for a in ("invoice", "invoice_books"):
                if a in cls.pkg_attrs:
                    setattr(pkg, a, cls.pkg_attrs[a])
                elif hasattr(pkg, a):
                    delattr(pkg, a)
        for k, v in cls.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cls.tmp.cleanup()

    # ── 小工具 ──
    def H(self, user, perms=None):
        h = {"x-test-user": user}
        if perms is not None:
            h["x-test-perms"] = json.dumps(perms)
        return h

    def post(self, url, user, body=None, **kw):
        return self.c.post(url, json=body if body is not None else {}, headers=self.H(user), **kw)

    def get(self, url, user):
        return self.c.get(url, headers=self.H(user))

    def manual(self, user, title, amount=None, company=None):
        r = self.post("/api/inv/folder/manual", user, {"title": title, "amount": amount,
                                                       "company": company or self.tip.FAKE["buyer"]})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["folder"]

    def scan(self, user, code):
        r = self.post("/api/inv/desk/scan", user, {"code": code})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def items(self, fid, user="boss"):
        r = self.get("/api/inv/folder/%d" % fid, user)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def upload(self, user, name, data, mime="application/octet-stream", origin="upload", folder_id=None, url="/api/inv/desk/upload"):
        form = {"origin": origin}
        if folder_id:
            form["folderId"] = str(folder_id)
        return self.c.post(url, files=[("files", (name, data, mime))], data=form, headers=self.H(user))

    # ── 权限 ──
    def test_01_auth_gates(self):
        self.assertEqual(self.c.get("/api/inv/desk").status_code, 401)
        r = self.get("/api/inv/desk", "nogate")
        self.assertEqual(r.status_code, 403)
        self.assertIn("收票工作台", r.json()["msg"])
        # 有动作点、没开页面 → 写接口一样挡（非敏感动作点会被自动补给全员，只靠它拦不住人）
        self.assertEqual(self.post("/api/inv/desk/scan", "nogate", {"code": "x"}).status_code, 403)
        self.assertEqual(self.post("/api/inv/audit/approve", "nogate", {"folderId": 1}).status_code, 403)
        self.assertEqual(self.get("/api/inv/settings", "nogate").status_code, 403)
        self.assertEqual(self.get("/api/inv/config", "nogate").status_code, 403)
        r = self.c.post("/api/inv/desk/scan", json={"code": "x"}, headers=self.H("intern", {"inv_intake": True}))
        self.assertEqual(r.status_code, 403)
        # 只开了页面、没动作点：能看不能写
        self.assertEqual(self.get("/api/inv/desk", "viewer").status_code, 200)
        r = self.post("/api/inv/desk/scan", "viewer", {"code": "x"})
        self.assertEqual(r.status_code, 403)
        self.assertIn("收票", r.json()["msg"])
        cfg = self.get("/api/inv/config", "intern").json()
        self.assertTrue(cfg["can"]["desk"] and cfg["can"]["intake"])
        self.assertFalse(cfg["can"]["auditAct"] or cfg["can"]["config"] or cfg["can"]["audit"])
        cfg = self.get("/api/inv/config", "acct").json()
        self.assertTrue(cfg["can"]["auditAct"])
        self.assertFalse(cfg["can"]["intake"])
        cfg = self.get("/api/inv/config", "boss").json()
        self.assertTrue(all(cfg["can"].values()))
        self.assertTrue(cfg["me"]["isSuper"])
        self.assertEqual([p["account"] for p in cfg["receivers"]], ["maker"])

    def test_02_settings_roundtrip(self):
        self.assertEqual(self.get("/api/inv/settings", "intern").status_code, 403)
        st = self.get("/api/inv/settings", "boss").json()["settings"]
        self.assertEqual(st["templates"], self.inv.DEFAULT_SETTINGS["templates"])
        self.assertEqual(st["remind"], {"enabled": True, "beforeDays": 3, "everyDays": 7, "hour": 10})
        self.assertEqual(st["portalUrl"], "https://finance.starfieldsz.com")
        self.assertFalse(st["blockNoInvoice"])
        r = self.post("/api/inv/settings", "boss", {"settings": {"remind": {"beforeDays": 5, "hour": 30},
                                                                 "portalUrl": "https://example.test/"}})
        self.assertEqual(r.status_code, 200, r.text)
        st2 = self.get("/api/inv/settings", "boss").json()["settings"]
        self.assertEqual(st2["remind"], {"enabled": True, "beforeDays": 5, "everyDays": 7, "hour": 23})
        self.assertEqual(st2["portalUrl"], "https://example.test")
        self.assertEqual(st2["company"], st["company"])      # 没给的键不动
        self.assertEqual(st2["people"], st["people"])
        r = self.post("/api/inv/settings", "boss", {"settings": {"portalUrl": "ftp://x"}})
        self.assertEqual(r.status_code, 400)
        r = self.post("/api/inv/settings", "boss", {"settings": {"people": [{"dtUserid": "x"}]}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("工作台账号", r.json()["msg"])
        self.post("/api/inv/settings", "boss", {"settings": {"remind": st["remind"], "portalUrl": st["portalUrl"]}})
        rows = self.get("/api/inv/accounts", "boss").json()["rows"]
        self.assertIn("intern", [x["name"] for x in rows])
        self.assertEqual(set(rows[0]), {"name", "grp", "post"})

    # ── 收票工作台 ──
    @unittest.skipUnless(HAS_IMG, "缺 PyMuPDF/opencv/Pillow")
    def test_03_pdf_then_scanner_confirm(self):
        f = self.tip.FAKE
        fo = self.manual("intern", "测试票夹A", "1,272.00")
        r = self.upload("intern", "发票.pdf", self.pdf, "application/pdf")
        self.assertEqual(r.status_code, 200, r.text)
        res = r.json()["results"]
        self.assertEqual([x["action"] for x in res], ["item"], res)
        it = res[0]["item"]
        self.assertEqual(it["number"], f["number"])
        self.assertEqual(it["sellerName"], f["seller"])
        self.assertEqual(it["total"], f["total"])
        self.assertEqual(it["fieldSrc"]["number"]["src"], "pdf")
        self.assertFalse(it["paper"])
        self.assertEqual(it["flags"], {})
        self.assertEqual(it["deductSuggest"], "yes")
        self.assertEqual(it["review"], "draft")
        self.assertEqual(it["file"]["mime"], "application/pdf")
        self.assertEqual(len(it["file"]["preview"]), 1)
        t = self.get(it["file"]["thumb"], "intern")
        self.assertEqual(t.status_code, 200)
        self.assertEqual(t.headers["content-type"], "image/jpeg")
        o = self.get(it["file"]["orig"], "acct")          # 任一 inv 页面都能看原件
        self.assertEqual(o.status_code, 200)
        self.assertEqual(o.content, self.pdf)
        self.assertIn("attachment", o.headers["content-disposition"])
        self.assertIn("filename*=UTF-8''", o.headers["content-disposition"])
        self.assertEqual(self.get(it["file"]["orig"], "nogate").status_code, 403)
        # 同一份文件再放 → 不重复登记
        r = self.upload("intern", "发票-副本.pdf", self.pdf, "application/pdf")
        self.assertEqual(r.json()["results"][0]["action"], "same")
        # 扫码枪扫纸质票 → 纸质件到件，不新建
        s = self.scan("intern", self.tip.FAKE_QR)
        self.assertEqual(s["action"], "confirm", s)
        self.assertTrue(s["item"]["paper"])
        d = self.items(fo["id"])
        self.assertEqual(len(d["items"]), 1)
        st = d["folder"]["stats"]
        self.assertEqual((st["invoices"], st["dup"], st["diff"], st["processing"]), (1, 0, 0.0, 0))
        self.assertIn("纸质件到件", [x["action"] for x in d["logs"]])
        # 字段编辑：金额乱填 400；改名→来源变人工；核对
        iid = it["id"]
        self.assertEqual(self.post("/api/inv/item/%d/update" % iid, "intern", {"fields": {"amount": "abc"}}).status_code, 400)
        self.assertEqual(self.post("/api/inv/item/%d/update" % iid, "intern", {"fields": {"date": "2026-13-45"}}).status_code, 400)
        r = self.post("/api/inv/item/%d/update" % iid, "intern", {"fields": {"remark": "补一句备注"}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["item"]["remark"], "补一句备注")
        self.assertEqual(r.json()["item"]["fieldSrc"]["remark"]["src"], "manual")
        self.assertEqual(self.post("/api/inv/item/%d/update" % iid, "viewer", {"fields": {"remark": "x"}}).status_code, 403)

    def test_04_dup_blocks_submit_until_removed(self):
        x = self.manual("intern", "票夹X", "100")
        a = self.scan("intern", qr("26440000000000000401", 100))
        self.assertEqual(a["action"], "item", a)
        y = self.manual("intern", "票夹Y", "150")
        d = self.scan("intern", qr("26440000000000000401", 100))
        self.assertEqual(d["action"], "dup", d)
        self.assertIn("重复票", d["msg"])
        self.assertEqual(d["item"]["flags"]["dup"]["folderId"], x["id"])
        self.assertEqual(d["item"]["flags"]["dup"]["kind"], "item")
        ok = self.scan("intern", qr("26440000000000000402", 50))
        self.assertEqual(ok["action"], "item")
        r = self.post("/api/inv/folder/%d/submit" % y["id"], "intern")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["blockers"][0]["code"], "dup")
        self.assertEqual(r.json()["blockers"][0]["itemIds"], [d["item"]["id"]])
        r = self.post("/api/inv/item/%d/remove" % d["item"]["id"], "intern")
        self.assertEqual(r.status_code, 200, r.text)
        r = self.post("/api/inv/folder/%d/submit" % y["id"], "intern")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["folder"]["status"], "submitted")
        self.assertEqual([i["review"] for i in r.json()["items"]], ["pending"])
        # 提交后收票人不能再改待审的票
        self.assertEqual(self.post("/api/inv/item/%d/remove" % ok["item"]["id"], "intern").status_code, 403)
        # 空的手工票夹不能提交
        e = self.manual("intern", "空票夹")
        r = self.post("/api/inv/folder/%d/submit" % e["id"], "intern")
        self.assertEqual(r.json()["blockers"][0]["code"], "empty")

    def test_05_split_allows_shared_invoice(self):
        self.manual("intern", "拆分1", "60")
        a = self.scan("intern", qr("26440000000000000501", 100))["item"]
        self.manual("intern", "拆分2", "40")
        b = self.scan("intern", qr("26440000000000000501", 100))
        self.assertEqual(b["action"], "dup")
        b = b["item"]
        self.assertEqual(self.post("/api/inv/item/%d/split" % b["id"], "intern", {"split": True, "alloc": 200}).status_code, 400)
        r = self.post("/api/inv/item/%d/split" % b["id"], "intern", {"split": True, "alloc": 40})
        self.assertIn("dup", r.json()["item"]["flags"])          # 先登记的那张没拆分 → 还是重复
        self.post("/api/inv/item/%d/split" % a["id"], "intern", {"split": True, "alloc": 60})
        b2 = [i for i in self.items(b["folderId"])["items"] if i["id"] == b["id"]][0]
        self.assertNotIn("dup", b2["flags"])                     # 双方都拆分、合计不超票面 → 放行
        self.assertEqual(self.items(b["folderId"])["folder"]["stats"]["sumTotal"], 40.0)
        self.post("/api/inv/item/%d/split" % a["id"], "intern", {"split": True, "alloc": 70})
        b3 = [i for i in self.items(b["folderId"])["items"] if i["id"] == b["id"]][0]
        self.assertIn("dup", b3["flags"])                        # 分摊超票面 → 又算重复

    # ── 审核 ──
    def test_06_audit_self_review_and_super(self):
        m = self.manual("maker", "造单人的票夹", "80")
        it = self.post("/api/inv/desk/scan", "maker", {"code": qr("26440000000000000601", 80)}).json()["item"]
        self.assertEqual(self.post("/api/inv/folder/%d/submit" % m["id"], "maker").status_code, 200)
        r = self.post("/api/inv/audit/approve", "maker", {"folderId": m["id"], "itemIds": [it["id"]]})
        self.assertEqual(r.status_code, 403)
        self.assertIn("提交人不能审核自己提交的票夹", r.json()["msg"])
        q = self.get("/api/inv/audit/queue?tab=pending", "acct").json()
        row = [x for x in q["rows"] if x["id"] == m["id"]][0]
        self.assertTrue(row["clean"], row["anomalies"])
        self.assertEqual(row["pendingItems"], 1)
        self.assertFalse(row["mine"])
        r = self.post("/api/inv/audit/approve", "acct", {"folderId": m["id"], "decisions": {str(it["id"]): {"deductible": False}},
                                                         "note": "ok", "itemIds": [it["id"]]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["folder"]["status"], "approved")
        d = self.items(m["id"])["items"][0]
        self.assertEqual((d["review"], d["reviewBy"], d["deductible"], d["selfReview"]), ("approved", "acct", False, False))
        # 已审核的票不能再改
        self.assertEqual(self.post("/api/inv/item/%d/update" % d["id"], "acct", {"fields": {"remark": "x"}}).status_code, 400)
        # 主管理员：自己提交自己审，放行但打「自审」
        b = self.manual("boss", "主管理员票夹", "90")
        bi = self.post("/api/inv/desk/scan", "boss", {"code": qr("26440000000000000602", 90)}).json()["item"]
        self.assertEqual(self.post("/api/inv/folder/%d/submit" % b["id"], "boss").status_code, 200)
        r = self.post("/api/inv/audit/approve", "boss", {"folderId": b["id"], "itemIds": [bi["id"]]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["selfReview"])
        self.assertTrue(r.json()["folder"]["selfReview"])
        det = self.items(b["id"])
        self.assertTrue(det["items"][0]["selfReview"])
        self.assertTrue(det["items"][0]["deductible"])          # 专票：按系统建议可抵
        self.assertIn("审核·自审", [x["action"] for x in det["logs"]])
        done = self.get("/api/inv/audit/queue?tab=done", "acct").json()
        self.assertIn(b["id"], [x["id"] for x in done["rows"]])

    def test_07_batch_only_clean(self):
        c = self.manual("intern", "干净票夹", "70")
        self.scan("intern", qr("26440000000000000701", 70))
        self.post("/api/inv/folder/%d/submit" % c["id"], "intern")
        dd = self.manual("intern", "金额对不上", "999")
        self.scan("intern", qr("26440000000000000702", 70))
        self.post("/api/inv/folder/%d/submit" % dd["id"], "intern")
        q = self.get("/api/inv/audit/queue", "acct").json()
        row = [x for x in q["rows"] if x["id"] == dd["id"]][0]
        self.assertFalse(row["clean"])
        self.assertIn("amountDiff", [a["code"] for a in row["anomalies"]])
        r = self.post("/api/inv/audit/batch", "acct", {"folderIds": [c["id"], dd["id"], 999999]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["done"], [c["id"]])
        self.assertEqual(sorted(str(s["id"]) for s in r.json()["skipped"]), sorted([str(dd["id"]), "999999"]))

    def test_08_return_requires_note_and_notifies_dry(self):
        f = self.manual("intern", "要退回的", "60")
        self.scan("intern", qr("26440000000000000801", 60))
        self.post("/api/inv/folder/%d/submit" % f["id"], "intern")
        self.assertEqual(self.post("/api/inv/audit/return", "acct", {"folderId": f["id"], "note": " "}).status_code, 400)
        with patch.object(self.inv.idt, "send_text", MagicMock(side_effect=AssertionError("不许真发"))) as m:
            r = self.post("/api/inv/audit/return", "acct", {"folderId": f["id"], "note": "缺原件"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["notify"], {"sent": False, "msg": "dry-run"})
        m.assert_not_called()
        d = self.items(f["id"])
        self.assertEqual(d["folder"]["status"], "returned")
        self.assertEqual(d["folder"]["reviewNote"], "缺原件")
        self.assertEqual(d["items"][0]["review"], "returned")
        # 退回后收票人能改、能再提交
        self.assertEqual(self.post("/api/inv/item/%d/update" % d["items"][0]["id"], "intern",
                                   {"fields": {"remark": "已补"}}).status_code, 200)
        r = self.post("/api/inv/folder/%d/submit" % f["id"], "intern")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["items"][0]["review"], "pending")
        # 不绑钉钉也不真发：notify_dt 的 dry-run 闸
        self.assertEqual(self.inv.notify_dt(["u1"], "x"), {"sent": False, "msg": "dry-run"})

    # ── 手机配对 ──
    def _pair(self, user):
        r = self.post("/api/inv/pair", user)
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        tok = re.search(r"#/invpair\?t=([A-Za-z0-9_\-]+)$", j["url"]).group(1)
        return j, tok

    def P(self, tok):
        return {"X-Inv-Pair": tok}

    def test_09_pairing(self):
        inv = self.inv
        f = self.manual("phoneguy", "手机票夹", "100")
        j, tok = self._pair("phoneguy")
        self.assertEqual(j["pair"]["expiresIn"], 600)
        # 不是本机访问 → 给门户正式网址（手机相机要 https）
        self.assertTrue(j["url"].startswith("https://finance.starfieldsz.com/#/invpair?t="), j["url"])
        self.assertEqual(j["httpsHint"], "")
        png = self.get(j["qr"], "phoneguy")
        self.assertEqual(png.status_code, 200)
        self.assertTrue(png.content.startswith(b"\x89PNG"))
        self.assertEqual(self.get(j["qr"], "intern").status_code, 404)             # 只有本人能看自己的配对码
        self.assertEqual(self.c.get("/api/inv/m/hello", headers=self.P("wrong-token")).status_code, 401)
        self.assertEqual(self.c.get("/api/inv/m/hello").status_code, 401)
        h = self.c.get("/api/inv/m/hello", headers=self.P(tok)).json()
        self.assertEqual((h["bound"], h["user"]), (False, "phoneguy"))
        self.assertEqual(self.c.get("/api/inv/m/state", headers=self.P(tok)).status_code, 403)   # 没绑定不能用
        b = self.c.post("/api/inv/m/bind", json={"device": "测试手机"}, headers=self.P(tok)).json()
        self.assertTrue(b["ok"])
        self.assertFalse(b["identified"])
        self.assertIn("没认出", b["msg"])
        sess = b["session"]
        self.assertNotEqual(sess, tok)
        self.assertEqual(self.get(j["qr"], "phoneguy").status_code, 404)           # 扫过就失效
        st = self.c.get("/api/inv/m/state", headers=self.P(sess)).json()
        self.assertEqual(st["folder"]["id"], f["id"])
        tok = sess                                                                # 之后手机都带会话令牌
        s = self.c.post("/api/inv/m/scan", json={"code": qr("26440000000000000901", 100)}, headers=self.P(tok)).json()
        self.assertEqual(s["action"], "item", s)
        self.assertTrue(s["item"]["paper"])
        self.assertTrue(s["item"]["origin"] == "phone")
        desk = self.get("/api/inv/desk", "phoneguy").json()
        self.assertTrue(desk["pair"]["bound"])
        self.assertEqual(desk["pair"]["device"], "测试手机")
        logs = self.items(f["id"])["logs"]
        self.assertTrue(any(x["action"] == "登记票据" and (x["detail"] or {}).get("via") == "手机" for x in logs))
        # 配对令牌校验函数（登录门用）
        req = types.SimpleNamespace(headers={"X-Inv-Pair": tok})
        self.assertTrue(inv.pair_token_ok(req))
        self.assertFalse(inv.pair_token_ok(types.SimpleNamespace(headers={"X-Inv-Pair": ""})))
        self.assertFalse(inv.pair_token_ok(types.SimpleNamespace(headers={"X-Inv-Pair": "中文令牌"})))
        self.assertFalse(inv.pair_token_ok(types.SimpleNamespace(headers={})))
        # 断开 → 立即失效
        self.assertEqual(self.post("/api/inv/pair/revoke", "phoneguy").status_code, 200)
        self.assertEqual(self.c.get("/api/inv/m/hello", headers=self.P(tok)).status_code, 401)
        self.assertFalse(inv.pair_token_ok(req))
        # 绑定期限过了
        j2, tok2 = self._pair("phoneguy")
        self.S.pair_update(self.db._engine, j2["pair"]["id"], bind_deadline="2000-01-01 00:00:00")
        self.assertEqual(self.c.get("/api/inv/m/hello", headers=self.P(tok2)).status_code, 401)
        # 会话过期
        j3, tok3 = self._pair("phoneguy")
        tok3 = self.c.post("/api/inv/m/bind", json={}, headers=self.P(tok3)).json()["session"]
        self.assertEqual(self.c.get("/api/inv/m/state", headers=self.P(tok3)).status_code, 200)
        self.S.pair_update(self.db._engine, j3["pair"]["id"], session_expires="2000-01-01 00:00:00")
        self.assertEqual(self.c.get("/api/inv/m/state", headers=self.P(tok3)).status_code, 401)
        # 账号没了收票权限 → 手机也立刻不能用
        j4, tok4 = self._pair("phoneguy")
        tok4 = self.c.post("/api/inv/m/bind", json={}, headers=self.P(tok4)).json()["session"]
        self.db.set_user_perms("phoneguy", {"enter_accounting": True, "enter:invdesk": True})
        try:
            r = self.c.get("/api/inv/m/state", headers=self.P(tok4))
            self.assertEqual(r.status_code, 403)
            self.assertIn("收票", r.json()["msg"])
        finally:
            self.db.set_user_perms("phoneguy", {"enter_accounting": True, "enter:invdesk": True, "inv_intake": True})

    def test_10_pair_identity_mismatch(self):
        j, tok = self._pair("intern")
        who = {"ok": True, "userid": "dt-someone-else", "name": "别人", "msg": ""}
        with patch.object(self.inv.idt, "userinfo_by_code", MagicMock(return_value=who)):
            r = self.c.post("/api/inv/m/bind", json={"code": "authcode"}, headers=self.P(tok))
        self.assertEqual(r.status_code, 403)
        self.assertIn("不是同一个", r.json()["msg"])
        self.assertFalse(self.c.get("/api/inv/m/hello", headers=self.P(tok)).json()["bound"])
        who = {"ok": True, "userid": "dt-intern", "name": "实习生甲", "msg": ""}
        with patch.object(self.inv.idt, "userinfo_by_code", MagicMock(return_value=who)):
            r = self.c.post("/api/inv/m/bind", json={"code": "authcode"}, headers=self.P(tok))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["identified"])
        self.assertEqual(self.get("/api/inv/desk", "intern").json()["pair"]["dtName"], "实习生甲")
        self.post("/api/inv/pair/revoke", "intern")

    @unittest.skipUnless(HAS_IMG, "缺 PyMuPDF/opencv/Pillow")
    def test_11_phone_upload_and_file_scope(self):
        tip = self.tip
        f = self.manual("phoneguy", "手机拍照票夹", "1272")
        j, tok = self._pair("phoneguy")
        tok = self.c.post("/api/inv/m/bind", json={}, headers=self.P(tok)).json()["session"]
        v = dict(tip.FAKE, number="26440000000000001101")
        pdf = tip.build_pdf(tip._page_items_digital("split", v=v), qr=qr(v["number"], v["total"]))
        jpg, _ = tip._render_jpeg(pdf, zoom=2.5)
        r = self.c.post("/api/inv/m/upload", files=[("files", ("p.jpg", jpg, "image/jpeg"))], data={"purpose": "invoice"},
                        headers=self.P(tok))
        self.assertEqual(r.status_code, 200, r.text)
        it = r.json()["results"][0]["item"]
        self.assertEqual((r.json()["results"][0]["action"], it["origin"], it["paper"]), ("item", "phone", True))
        self.assertEqual(it["number"], v["number"])
        self.assertIn(it["procStatus"], ("pending", "done"))
        self.assertTrue(it["file"]["thumb"].startswith("/api/inv/m/file/"))
        self.assertEqual(it["file"]["preview"], [])
        self.assertIsNone(it["file"]["orig"])
        ok = self.c.get(it["file"]["thumb"], headers=self.P(tok))
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.c.get("/api/inv/m/file/%d?v=o" % it["file"]["id"], headers=self.P(tok)).status_code, 400)
        # 别人票夹里的文件：手机上看不到
        self.manual("boss", "别人的票夹")
        other = self.upload("boss", "note.txt", b"just a note", "text/plain").json()["results"][0]
        self.assertEqual(other["action"], "other")
        self.assertEqual(self.c.get("/api/inv/m/file/%d?v=t" % other["item"]["file"]["id"], headers=self.P(tok)).status_code, 404)
        # 「扫审批单」拍到的是发票：不当票留存
        r = self.c.post("/api/inv/m/upload", files=[("files", ("s.jpg", jpg, "image/jpeg"))], data={"purpose": "scan"},
                        headers=self.P(tok))
        self.assertEqual(r.json()["results"][0]["action"], "unknown")
        self.assertEqual(len(self.items(f["id"])["items"]), 1)
        self.post("/api/inv/pair/revoke", "phoneguy")

    # ── 文件安全 ──
    def test_12_file_path_safety(self):
        inv, S, e = self.inv, self.S, self.db._engine
        secret = Path(self.tmp.name) / "secret.txt"
        secret.write_text("secret", encoding="utf-8")
        for bad in ("../secret.txt", "../../secret.txt", str(secret), "2026-09/1/../../../secret.txt",
                    "2026-09/1/2_o.pdf/../../../../secret.txt", "C:/Windows/win.ini"):
            fid = S.file_insert(e, folder_id=1, role="original", origin="upload", name="x.pdf", mime="application/pdf",
                                ext="pdf", size=1, orig_path=bad, thumb_path=bad, preview_path=bad)
            for v in ("o", "t", "p"):
                r = self.get("/api/inv/file/%d?v=%s" % (fid, v), "boss")
                self.assertEqual(r.status_code, 404, (bad, v, r.text))
                self.assertNotIn(b"secret", r.content)
        self.assertEqual(self.get("/api/inv/file/%d?v=zz" % fid, "boss").status_code, 400)
        self.assertEqual(self.get("/api/inv/file/99999999", "boss").status_code, 404)
        self.assertIsNone(inv.abs_path("../x_o.pdf"))
        self.assertIsNone(inv.abs_path("2026-09/1/2_o.pdf/.."))
        p = inv.abs_path("2026-09/1/2_o.pdf")
        self.assertTrue(p.startswith(os.path.realpath(inv.UPLOAD_DIR)))

    # ── 后台线程：识别 / 拉附件 ──
    @unittest.skipUnless(HAS_IMG, "缺 PyMuPDF/opencv/Pillow")
    def test_13_worker_ocr_merge(self):
        tip, inv = self.tip, self.inv
        self.manual("intern", "照片识别票夹", "1272")
        v = dict(tip.FAKE, number="26440000000000001301")
        pdf = tip.build_pdf(tip._page_items_digital("split", v=v), qr=qr(v["number"], v["total"]))
        jpg, _ = tip._render_jpeg(pdf, zoom=2.5)
        r = self.upload("intern", "拍的.jpg", jpg, "image/jpeg", origin="camera").json()["results"][0]
        self.assertEqual(r["action"], "item", r)
        it = r["item"]
        self.assertEqual(it["procStatus"], "pending")
        self.assertEqual(it["fieldSrc"]["number"]["src"], "qr")
        self.assertTrue(it["paper"])

        def fake_ocr(data, qr_doc=None):
            d = dict(qr_doc)
            d["fieldSrc"] = dict(qr_doc["fieldSrc"])
            d["pending"] = list(qr_doc.get("pending") or [])
            for k, val in (("sellerName", v["seller"]), ("sellerTaxId", v["seller_tid"]), ("buyerName", v["buyer"]),
                           ("buyerTaxId", v["buyer_tid"]), ("amount", 1200.0), ("tax", 72.0), ("number", "99999999999999999999")):
                if d.get(k) in (None, ""):
                    d[k] = val
                    d["fieldSrc"][k] = {"src": "ocr", "page": 0, "box": [0.5, 0.2, 0.9, 0.25]}
                    d["pending"].append(k)
            d["needOcr"] = False
            return d
        with patch.object(inv.ip, "extract_image_ocr", side_effect=fake_ocr):
            self.assertGreaterEqual(inv.worker_step(), 1)
        got = [i for i in self.items(it["folderId"])["items"] if i["id"] == it["id"]][0]
        self.assertEqual(got["procStatus"], "done")
        self.assertEqual(got["number"], v["number"])                # 二维码来的号码不被识别结果覆盖
        self.assertEqual(got["fieldSrc"]["number"]["src"], "qr")
        self.assertEqual(got["sellerName"], v["seller"])
        self.assertEqual(got["fieldSrc"]["sellerName"]["src"], "ocr")
        self.assertTrue({"sellerName", "sellerTaxId", "amount", "tax"} <= set(got["pending"]))
        self.assertEqual(got["deductSuggest"], "yes")
        # 核对：改一个、确认一个 → 都不再待核
        r = self.post("/api/inv/item/%d/update" % got["id"], "intern",
                      {"fields": {"sellerName": v["seller"] + "（改）"}, "confirm": ["tax"]})
        self.assertEqual(r.status_code, 200, r.text)
        g2 = r.json()["item"]
        self.assertEqual(g2["fieldSrc"]["sellerName"]["src"], "manual")
        self.assertNotIn("sellerName", g2["pending"])
        self.assertNotIn("tax", g2["pending"])
        r = self.post("/api/inv/item/%d/update" % got["id"], "intern", {"confirm": "all"})
        self.assertEqual(r.json()["item"]["pending"], [])
        # 识别组件缺失 → 失败并写明原因，不拖死线程
        v3 = dict(tip.FAKE, number="26440000000000001302")
        pdf3 = tip.build_pdf(tip._page_items_digital("split", v=v3), qr=qr(v3["number"], v3["total"]))
        jpg3, _ = tip._render_jpeg(pdf3, zoom=2.5)
        it3 = self.upload("intern", "拍的3.jpg", jpg3, "image/jpeg", origin="camera").json()["results"][0]["item"]
        with patch.object(inv.ip, "extract_image_ocr", side_effect=inv.ip.OCRUnavailable("服务器缺少离线识别组件（rapidocr_onnxruntime）")):
            inv.worker_step()
        g3 = [i for i in self.items(it3["folderId"])["items"] if i["id"] == it3["id"]][0]
        self.assertEqual(g3["procStatus"], "failed")
        self.assertIn("缺少离线识别组件", g3["procError"])
        # 识别失败的票不挡提交，只提示审核时对图补字段
        r = self.post("/api/inv/folder/%d/submit" % it3["folderId"], "intern")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("procFailed", [w["code"] for w in r.json()["warnings"]])

    @unittest.skipUnless(HAS_IMG, "缺 PyMuPDF/opencv/Pillow")
    def test_14_scan_approval_and_pull_attachments(self):
        tip, inv = self.tip, self.inv
        v = dict(tip.FAKE, number="26440000000000001401")
        pdf = tip.build_pdf(tip._page_items_digital("split", v=v), qr=qr(v["number"], v["total"]))
        norm = {"instId": "PI-TEST-1", "businessId": "202609241000000001401", "template": "付款申请（公对公）",
                "title": "测试甲提交的付款申请（公对公）", "applicant": "测试甲", "applicantUid": "u-a", "dept": "测试部",
                "company": v["buyer"], "amount": 1272.0, "payeeName": "另一家供应商有限公司", "payeeBank": "测试银行",
                "payeeAccount": "6222000000000001", "reason": "测试事由", "erpNo": "", "approvalStatus": "RUNNING",
                "approvalResult": "agree", "createTime": "", "relate": [], "form": [],
                "attachments": [{"fileId": "F-1", "fileName": "电子发票.pdf", "spaceId": "", "fileSize": len(pdf),
                                 "source": "form", "label": "附件"}],
                "photos": [], "hasAttachments": True}
        dl = MagicMock(return_value={"ok": True, "bytes": pdf, "via": "v2", "msg": ""})
        with patch.object(inv.idt, "resolve_link", MagicMock(return_value={"ok": True, "procInstId": "PI-TEST-1",
                                                                           "corpId": "dingcorp-test", "msg": ""})), \
                patch.object(inv.idt, "get_instance", MagicMock(return_value={"ok": True, "inst": {"x": 1}, "msg": ""})), \
                patch.object(inv.idt, "normalize_instance", MagicMock(return_value=norm)), \
                patch.object(inv.idt, "download_attachment", dl), \
                patch.object(inv.idt, "send_text", MagicMock(side_effect=AssertionError("不许真发"))):
            s = self.scan("intern", "https://aflow.dingtalk.com/qr/testcode")
            self.assertEqual(s["action"], "folder", s)
            fo = s["folder"]
            self.assertEqual((fo["attachStatus"], fo["businessId"], fo["allowLater"]), ("pending", norm["businessId"], True))
            self.assertEqual(self.get("/api/inv/settings", "boss").json()["settings"]["corpId"], "dingcorp-test")
            self.assertGreaterEqual(inv.worker_step(), 1)
            d = self.items(fo["id"])
            self.assertEqual(d["folder"]["attachStatus"], "done")
            self.assertEqual(d["folder"]["attachMsg"], "附件 1 个，发票 1 张")
            it = d["items"][0]
            self.assertEqual((it["origin"], it["paper"], it["number"]), ("attachment", False, v["number"]))
            self.assertEqual(it["createdBy"], "系统")          # 后台拉的附件不算打开票夹的人登记的（审核自审判定用）
            self.assertEqual(it["flags"]["sellerMismatch"], {"seller": v["seller"], "payee": "另一家供应商有限公司"})
            self.assertEqual(d["folder"]["stats"]["paperMissing"], 1)
            # 纸质件到 → 不算重复
            c = self.scan("intern", qr(v["number"], v["total"]))
            self.assertEqual(c["action"], "confirm", c)
            self.assertEqual(self.items(fo["id"])["folder"]["stats"]["paperMissing"], 0)
            # 刷新：已拉过的附件不再下载
            r = self.post("/api/inv/folder/%d/refresh" % fo["id"], "intern")
            self.assertEqual(r.status_code, 200, r.text)
            inv.worker_step()
            self.assertEqual(dl.call_count, 1)
            # 再扫同一张审批单：打开已有票夹，不重复拉
            s2 = self.scan("intern", "https://aflow.dingtalk.com/qr/testcode")
            self.assertEqual(s2["folder"]["id"], fo["id"])
            self.assertEqual(s2["folder"]["attachStatus"], "done")
            # 审批单打印件（照片里只有审批码）→ 当"扫审批单"
            self.post("/api/inv/desk/close", "intern")
            png = tip._qr_png("https://aflow.dingtalk.com/qr/testcode")
            r = self.upload("intern", "审批单.png", png, "image/png").json()
            self.assertEqual(r["results"][0]["action"], "folder", r)
            self.assertEqual(r["folder"]["id"], fo["id"])
        # 钉钉没配置 → 400 且原话告诉人
        with patch.object(inv.idt, "resolve_link", MagicMock(return_value={"ok": True, "procInstId": "PI-2", "corpId": "", "msg": ""})), \
                patch.object(inv.idt, "get_instance", MagicMock(return_value={"ok": False, "inst": None,
                                                                             "msg": "未配置钉钉（conf.ini [dingtalk]），无法从钉钉取单"})):
            r = self.post("/api/inv/desk/scan", "intern", {"code": "https://aflow.dingtalk.com/qr/other"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("未配置钉钉（conf.ini [dingtalk]）", r.json()["msg"])
        # 没打开票夹就扫发票 → 软提示，不报错
        self.post("/api/inv/desk/close", "intern")
        r = self.scan("intern", qr("26440000000000001499", 10))
        self.assertEqual(r["action"], "noFolder")
        self.assertEqual(self.scan("intern", "随便什么")["action"], "unknown")

    def test_15_upload_limits(self):
        self.manual("intern", "上传上限")
        with patch.object(self.inv, "MAX_FILE", 1000):
            r = self.upload("intern", "big.bin", b"x" * 2000)
            self.assertEqual(r.status_code, 413)
            self.assertIn("超过", r.json()["msg"])
            r = self.c.post("/api/inv/desk/upload", headers=self.H("intern"), data={"origin": "upload"},
                            files=[("files", ("big.bin", b"x" * 2000, "application/octet-stream")),
                                   ("files", ("ok.txt", b"small note", "text/plain"))])
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(sorted(x["action"] for x in r.json()["results"]), ["error", "other"])
        with patch.object(self.inv, "MAX_TOTAL", 1000):
            r = self.upload("intern", "mid.bin", b"y" * 1500)
            self.assertEqual(r.status_code, 413)


    # ═════════════ 审查修复回归（全部合成数据；钉钉全 mock、INV_DRY_SEND 不真发） ═════════════
    @staticmethod
    def N(n):
        return "26441700%012d" % n

    def _pending_ids(self, fid):
        return [i["id"] for i in self.items(fid)["items"] if i["review"] == "pending"]

    def _approve(self, fid, user="acct", decisions=None):
        body = {"folderId": fid, "itemIds": self._pending_ids(fid)}
        if decisions is not None:
            body["decisions"] = decisions
        return self.post("/api/inv/audit/approve", user, body)

    def _submit_approve(self, fid):
        r = self.post("/api/inv/folder/%d/submit" % fid, "intern")
        self.assertEqual(r.status_code, 200, r.text)
        r = self._approve(fid)
        self.assertEqual(r.status_code, 200, r.text)

    def _item(self, iid):
        return self.S.item_get(self.db._engine, iid)

    def _bind(self, user, body=None):
        j, tok = self._pair(user)
        r = self.c.post("/api/inv/m/bind", json=body or {}, headers=self.P(tok))
        self.assertEqual(r.status_code, 200, r.text)
        return j, tok, r.json()["session"]

    def test_16_locked_folder_rejects_desk_and_phone_ingest(self):
        """SEC-1：已审核的票夹，收票工作台/手机不能再放票；扫到同一张票只确认纸质件，票面一概不动。"""
        N, e = self.N, self.db._engine
        fo = self.manual("intern", "SEC1-已审票夹", "100")
        it = self.scan("intern", qr(N(1), 100))["item"]
        self._submit_approve(fo["id"])
        r = self.upload("intern", "note.txt", b"a note for SEC1 locked folder", "text/plain", folder_id=fo["id"])
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["results"][0]["action"], "error")
        self.assertIn("不能再往里放票", r.json()["results"][0]["msg"])
        self.post("/api/inv/desk/open", "intern", {"folderId": fo["id"]})
        s = self.scan("intern", qr(N(2), 50))
        self.assertEqual(s["action"], "error", s)
        # 同号但金额被改大的码：只确认纸质件，金额不变，标出来给审核看
        s = self.scan("intern", qr(N(1), 10000))
        self.assertEqual(s["action"], "confirm", s)
        got = self._item(it["id"])
        self.assertEqual((got["total"], got["review"], got["paper"]), (100.0, "approved", 1))
        self.assertEqual(got["flags_json"]["postApprovalMismatch"], ["total"])
        self.assertEqual(len(self.items(fo["id"])["items"]), 1)
        self.assertIn("postMismatch", [a["code"] for a in self.items(fo["id"])["folder"]["anomalies"]])
        # 后补池那条路（带 later_id，不受锁）合并到已审票：一样只挂不改
        r = self.inv.ingest_qr(fo["id"], qr(N(1), 5000), "intern", "scanner", later_id=987654)
        self.assertEqual(r["action"], "confirm")
        self.assertEqual(self._item(it["id"])["total"], 100.0)
        # 带文件合并进已审票：文件只挂成附件，主文件和票面不变
        fid = self.S.file_insert(e, folder_id=fo["id"], role="original", origin="upload", name="fake.xml",
                                 mime="application/xml", ext="xml", sha256="sec1")
        d = self.inv.ip.new_doc(0)
        d.update(kind="invoice", number=N(1), total=10000.0, sellerName="Fake Seller Ltd",
                 fieldSrc={"total": {"src": "xml", "page": 0, "box": None}})
        self.inv._merge_same(e, self._item(it["id"]), self.S.file_get(e, fid), d, "upload", "intern", False, "x", None)
        got = self._item(it["id"])
        self.assertEqual((got["total"], got["file_id"], got["seller_name"]), (100.0, None, it["sellerName"]))
        self.assertEqual((self.S.file_get(e, fid)["item_id"], self.S.file_get(e, fid)["role"]), (it["id"], "attachment"))

    def test_17_pending_item_keeps_trusted_values(self):
        """SEC-1：待审的票，后来的同号文件/码值对不上时不覆盖二维码读到的值，改放"待核"交审核看。"""
        N = self.N
        fo = self.manual("intern", "SEC1-待审票夹", "100")
        it = self.scan("intern", qr(N(3), 100))["item"]
        self.assertEqual(self.post("/api/inv/folder/%d/submit" % fo["id"], "intern").status_code, 200)
        r = self.inv.ingest_qr(fo["id"], qr(N(3), 999), "maker", "scanner", later_id=987655)
        self.assertEqual(r["action"], "confirm")
        got = self._item(it["id"])
        self.assertEqual(got["total"], 100.0)
        self.assertIn("total", got["pending_json"])
        self.assertEqual(got["flags_json"]["fileMismatch"], ["total"])

    def test_18_self_review_includes_registrant(self):
        """SEC-3：待审票里有审核人自己登记的 → 算自审，不许自己审。"""
        N, S, e = self.N, self.S, self.db._engine
        fo = self.manual("intern", "SEC3-票夹", "100")
        self.scan("intern", qr(N(4), 100))
        self.assertEqual(self.post("/api/inv/folder/%d/submit" % fo["id"], "intern").status_code, 200)
        S.item_insert(e, folder_id=fo["id"], kind="invoice", number=N(5), dup_key="N:" + N(5),
                      total=50000, review="pending", created_by="maker")
        r = self._approve(fo["id"], "maker")
        self.assertEqual(r.status_code, 403, r.text)
        self.assertIn("自己登记的票", r.json()["msg"])
        q = self.get("/api/inv/audit/queue?tab=pending", "maker").json()
        self.assertTrue([x for x in q["rows"] if x["id"] == fo["id"]][0]["mine"])
        r = self._approve(fo["id"], "acct")
        self.assertEqual(r.status_code, 200, r.text)

    def test_19_kind_relabel_and_receipt_dup(self):
        """SEC-4：二维码读出号码的发票不许改成收据；收据填了号码也查重；人手改机读字段亮给审核。"""
        N, S, e = self.N, self.S, self.db._engine
        fa = self.manual("intern", "SEC4-A", "100")
        a = self.scan("intern", qr(N(6), 100))["item"]
        fb = self.manual("intern", "SEC4-B", "100")
        d = self.scan("intern", qr(N(6), 100))
        self.assertEqual(d["action"], "dup")
        r = self.post("/api/inv/item/%d/update" % d["item"]["id"], "intern", {"fields": {"kind": "receipt"}})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("不能改成收据", r.json()["msg"])
        fc = self.manual("intern", "SEC4-C", "100")
        rid = S.item_insert(e, folder_id=fc["id"], kind="receipt", total=100, created_by="intern")
        r = self.post("/api/inv/item/%d/update" % rid, "intern", {"fields": {"number": N(6)}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["item"]["flags"]["dup"]["folderId"], fa["id"])
        r = self.post("/api/inv/item/%d/update" % a["id"], "intern", {"fields": {"total": "101"}})
        self.assertEqual(r.status_code, 200, r.text)
        mo = r.json()["item"]["flags"]["manualOverride"]["total"]
        self.assertEqual((mo["from"], mo["src"]), ("100.0", "qr"))
        self.assertEqual(self.post("/api/inv/folder/%d/submit" % fa["id"], "intern").status_code, 200)
        row = [x for x in self.get("/api/inv/audit/queue?tab=pending", "acct").json()["rows"] if x["id"] == fa["id"]][0]
        ov = [x for x in row["anomalies"] if x["code"] == "override"][0]
        self.assertEqual(ov["level"], "err")
        self.assertFalse(row["clean"])
        # 号码是识别来的发票（不是二维码/原件）：可以改成收据，但亮"错"给审核
        fd = self.manual("intern", "SEC4-D", "10")
        oid = S.item_insert(e, folder_id=fd["id"], kind="invoice", number=N(19), dup_key="N:" + N(19), total=10,
                            field_src_json={"number": {"src": "ocr", "page": 0, "box": None}}, created_by="intern")
        r = self.post("/api/inv/item/%d/update" % oid, "intern", {"fields": {"kind": "receipt"}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["item"]["flags"]["kindChanged"]["to"], "receipt")
        self.assertEqual(self.post("/api/inv/folder/%d/submit" % fd["id"], "intern").status_code, 200)
        row = [x for x in self.get("/api/inv/audit/queue?tab=pending", "acct").json()["rows"] if x["id"] == fd["id"]][0]
        self.assertEqual([x["level"] for x in row["anomalies"] if x["code"] == "kindChanged"], ["err"])

    def test_20_pairing_code_is_one_time_and_identity_checked(self):
        """SEC-5/C1：配对码只能用一次（绑定换发会话令牌）；钉钉已配置且账号绑了钉钉 → 必须钉钉扫且是本人。"""
        inv = self.inv
        j, tok, sess = self._bind("phoneguy")
        r = self.c.post("/api/inv/m/bind", json={}, headers=self.P(tok))
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("这个配对码已经用过了", r.json()["msg"])
        self.assertEqual(self.c.get("/api/inv/m/hello", headers=self.P(tok)).status_code, 401)
        self.assertEqual(self.c.get("/api/inv/m/state", headers=self.P(tok)).status_code, 401)
        self.assertTrue(self.c.get("/api/inv/m/hello", headers=self.P(sess)).json()["bound"])
        # 登录门：用过的配对码只放进 /api/inv/m/bind（好回 409），别的路径不放；并记下是谁的手机
        req = types.SimpleNamespace(headers={"X-Inv-Pair": tok}, url=types.SimpleNamespace(path="/api/inv/m/bind"),
                                    state=types.SimpleNamespace())
        self.assertTrue(inv.pair_token_ok(req))
        self.assertEqual(inv.pair_ops_user(req), "phoneguy·手机")
        req2 = types.SimpleNamespace(headers={"X-Inv-Pair": tok}, url=types.SimpleNamespace(path="/api/inv/m/state"))
        self.assertFalse(inv.pair_token_ok(req2))
        self.post("/api/inv/pair/revoke", "phoneguy")
        with patch.object(inv.idt, "configured", MagicMock(return_value=True)):
            j, tok = self._pair("intern")
            r = self.c.post("/api/inv/m/bind", json={}, headers=self.P(tok))
            self.assertEqual(r.status_code, 403)
            self.assertIn("请用手机钉钉「扫一扫」", r.json()["msg"])
            with patch.object(inv.idt, "userinfo_by_code", MagicMock(return_value={"ok": False, "msg": "免登码过期"})):
                r = self.c.post("/api/inv/m/bind", json={"code": "x"}, headers=self.P(tok))
            self.assertEqual(r.status_code, 403)
            self.assertIn("没认出", r.json()["msg"])
            who = {"ok": True, "userid": "dt-intern", "name": "实习生甲", "msg": ""}
            with patch.object(inv.idt, "userinfo_by_code", MagicMock(return_value=who)):
                r = self.c.post("/api/inv/m/bind", json={"code": "x"}, headers=self.P(tok))
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()["identified"])
            # 账号没绑钉钉：放行但提示没认出身份
            j, tok = self._pair("phoneguy")
            r = self.c.post("/api/inv/m/bind", json={}, headers=self.P(tok))
            self.assertEqual(r.status_code, 200, r.text)
            self.assertFalse(r.json()["identified"])
        self.post("/api/inv/pair/revoke", "intern")
        self.post("/api/inv/pair/revoke", "phoneguy")

    def test_21_phone_rejected_after_password_reset_and_revoke(self):
        """GOV-3：管理员重置密码后手机配对立即失效；账号重置/禁用/删除时 app.py 吊销配对；手机请求按"<账号>·手机"记埋点。"""
        j, tok, sess = self._bind("phoneguy")
        self.assertEqual(self.c.get("/api/inv/m/state", headers=self.P(sess)).status_code, 200)
        self.db.reset_pwd("phoneguy", "reset-pass-21")
        try:
            r = self.c.get("/api/inv/m/state", headers=self.P(sess))
            self.assertEqual(r.status_code, 403)
            self.assertIn("密码已被重置", r.json()["msg"])
        finally:
            ok, msg = self.db.change_own_pwd("phoneguy", "reset-pass-21", "x-test-pwd-21")
            self.assertTrue(ok, msg)
        self.assertEqual(self.c.get("/api/inv/m/state", headers=self.P(sess)).status_code, 200)
        self.assertGreaterEqual(self.inv.revoke_pairs_for("phoneguy"), 1)
        self.assertEqual(self.c.get("/api/inv/m/state", headers=self.P(sess)).status_code, 401)
        src = (BACKEND / "app.py").read_text(encoding="utf-8")
        for fn in ("def api_user_active", "def api_user_reset", "def api_user_delete"):
            body = src[src.index(fn):]
            body = body[:body.index("\n@app.")]
            self.assertIn("_inv_revoke_pairs(name)", body, fn)
        self.assertIn("invoice.revoke_pairs_for(name)", src)
        gate = src[src.index("async def _auth_gate"):src.index("app.add_middleware(ops.OpsMiddleware)")]
        self.assertIn("invoice.pair_ops_user(request)", gate)

    def test_22_ops_skips_poll_endpoints(self):
        """C4：两个只读轮询（精确路径＋GET）不进运维埋点，其余照记。"""
        import ops
        self.assertTrue(ops._skip("GET", "/api/inv/desk"))
        self.assertTrue(ops._skip("GET", "/api/inv/m/state"))
        self.assertFalse(ops._skip("POST", "/api/inv/desk"))
        self.assertFalse(ops._skip("GET", "/api/inv/desk/scan"))
        self.assertFalse(ops._skip("POST", "/api/inv/desk/scan"))
        self.assertFalse(ops._skip("GET", "/api/inv/m/hello"))
        self.assertTrue(ops._skip("GET", "/api/ops/live"))

    def test_23_desk_pair_active_and_cheap_poll(self):
        """C4/F6：电脑端拿到手机 lastSeen/active；收票台轮询的查库次数不随"最近票夹"个数增长。"""
        from sqlalchemy import event
        j, tok, sess = self._bind("phoneguy")
        self.c.get("/api/inv/m/state", headers=self.P(sess))
        p = self.get("/api/inv/desk", "phoneguy").json()["pair"]
        self.assertTrue(p["active"])
        self.assertTrue(p["lastSeen"])
        self.S.pair_update(self.db._engine, j["pair"]["id"], last_seen="2000-01-01 00:00:00")
        self.assertFalse(self.get("/api/inv/desk", "phoneguy").json()["pair"]["active"])
        self.post("/api/inv/pair/revoke", "phoneguy")
        eng, n = self.db._engine, {"q": 0}

        def cnt(*a, **k):
            n["q"] += 1

        def count_desk():
            n["q"] = 0
            event.listen(eng, "before_cursor_execute", cnt)
            try:
                self.assertEqual(self.get("/api/inv/desk", "maker").status_code, 200)
            finally:
                event.remove(eng, "before_cursor_execute", cnt)
            return n["q"]
        for i in range(2):
            self.manual("maker", "轮询票夹%d" % i)
        few = count_desk()
        for i in range(2, 8):
            self.manual("maker", "轮询票夹%d" % i)
        self.assertEqual(count_desk(), few)

    def test_24_late_number_does_not_steal_ownership(self):
        """F1：号码后补的票（行 id 更小）不能把已审的同号票挤成重复；审核还有硬闸。"""
        N, S, e = self.N, self.S, self.db._engine
        fa = self.manual("intern", "F1-照片先到", "500")
        ph = S.item_insert(e, folder_id=fa["id"], kind="other", origin="camera", created_by="intern")
        fb = self.manual("intern", "F1-先审", "500")
        b = self.scan("intern", qr(N(7), 500))["item"]
        self._submit_approve(fb["id"])
        r = self.post("/api/inv/item/%d/update" % ph, "intern",
                      {"fields": {"number": N(7), "total": "500", "date": "2026-09-01", "kind": "invoice"}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["item"]["flags"]["dup"]["folderId"], fb["id"])
        self.assertNotIn("dup", self._item(b["id"])["flags_json"])
        r = self.post("/api/inv/folder/%d/submit" % fa["id"], "intern")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["blockers"][0]["code"], "dup")
        # 硬闸：就算重复标记没亮（陈旧），同号票已在别的单审核通过也过不了审
        S.item_update(e, ph, flags_json={}, review="pending")
        S.folder_update(e, fa["id"], status="submitted", submitted_by="intern")
        r = self._approve(fa["id"])
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("已在别的单上审核通过", r.json()["msg"])

    def test_25_split_overallocation_flags_newcomer_only(self):
        """F8：拆分超额只标超额的那张后来者，先前合法拆分的不受牵连。"""
        N = self.N
        ids = []
        for t, alloc in (("拆A", 600), ("拆B", 400), ("拆C", 300)):
            self.manual("intern", "F8-" + t, str(alloc))
            ids.append((self.scan("intern", qr(N(8), 1000))["item"]["id"], alloc))
        for iid, alloc in ids:
            r = self.post("/api/inv/item/%d/split" % iid, "intern", {"split": True, "alloc": alloc})
            self.assertEqual(r.status_code, 200, r.text)
        flags = [bool(self._item(iid)["flags_json"].get("dup")) for iid, _ in ids]
        self.assertEqual(flags, [False, False, True])

    def test_26_same_folder_late_number_merges(self):
        """F9：同票夹里照片后来才认出号码 → 和扫码登记的那张并成一张，不标重复、不挡提交。"""
        N, S, e = self.N, self.S, self.db._engine
        fo = self.manual("intern", "F9-同夹", "88")
        q = self.scan("intern", qr(N(9), 88))["item"]
        ph = S.item_insert(e, folder_id=fo["id"], kind="other", origin="camera", paper=1, created_by="intern")
        r = self.post("/api/inv/item/%d/update" % ph, "intern", {"fields": {"number": N(9)}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["mergedInto"], q["id"])
        live = self.items(fo["id"])["items"]
        self.assertEqual([i["id"] for i in live], [q["id"]])
        self.assertNotIn("dup", live[0]["flags"])
        self.assertEqual(self._item(ph)["status"], "removed")
        self.assertEqual(self.post("/api/inv/folder/%d/submit" % fo["id"], "intern").status_code, 200)

    def test_27_approve_only_items_the_auditor_saw(self):
        """C2/F10：审核只通过页面上看到的票；审核期间又进了新票 → 409；老页面没带 itemIds → 400。"""
        N, S, e = self.N, self.S, self.db._engine
        fo = self.manual("intern", "F10-票夹", "60")
        it = self.scan("intern", qr(N(10), 60))["item"]
        self.assertEqual(self.post("/api/inv/folder/%d/submit" % fo["id"], "intern").status_code, 200)
        r = self.post("/api/inv/audit/approve", "acct", {"folderId": fo["id"]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("请刷新页面后再审", r.json()["msg"])
        new = S.item_insert(e, folder_id=fo["id"], kind="invoice", number=N(11), dup_key="N:" + N(11),
                            total=900, review="pending", created_by="recv")
        r = self.post("/api/inv/audit/approve", "acct", {"folderId": fo["id"], "itemIds": [it["id"]]})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["newItems"], [new])
        self.assertEqual(self._item(new)["review"], "pending")
        self.assertEqual(self._item(it["id"])["review"], "pending")
        r = self.post("/api/inv/audit/approve", "acct", {"folderId": fo["id"], "itemIds": [it["id"], new]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["msg"], "已通过 2 张")

    def test_28_no_stuck_submitted_folders(self):
        """F2：没有要审的票不会挂成"已提交"；已提交的票夹待审票没了会自动调整，也能直接通过/退回。"""
        N, S, e = self.N, self.S, self.db._engine
        fo = self.manual("intern", "F2-票都审过")
        S.item_insert(e, folder_id=fo["id"], kind="invoice", number=N(12), dup_key="N:" + N(12), total=10,
                      review="approved", created_by="recv")
        r = self.post("/api/inv/folder/%d/submit" % fo["id"], "intern")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["folder"]["status"], "approved")
        fo2 = self.manual("intern", "F2-只有资料")
        S.item_insert(e, folder_id=fo2["id"], kind="other", origin="later", review="approved", created_by="recv")
        r = self.post("/api/inv/folder/%d/submit" % fo2["id"], "intern")
        self.assertEqual(r.status_code, 400)
        self.assertIn("没有要审核的票", r.json()["msg"])
        self.assertEqual(S.folder_get(e, fo2["id"])["status"], "collecting")
        fo3 = self.manual("intern", "F2-移除最后一张")
        x = self.scan("intern", qr(N(13), 20))["item"]
        self.assertEqual(self.post("/api/inv/folder/%d/submit" % fo3["id"], "intern").status_code, 200)
        r = self.post("/api/inv/item/%d/remove" % x["id"], "acct", {"note": "扫错了"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["folder"]["status"], "collecting")
        self.assertIn("收票中", r.json()["msg"])
        fo4 = self.manual("intern", "F2-空的已提交")
        S.folder_update(e, fo4["id"], status="submitted", submitted_by="intern")
        r = self.post("/api/inv/audit/approve", "acct", {"folderId": fo4["id"], "itemIds": []})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(S.folder_get(e, fo4["id"])["status"], "approved")
        fo5 = self.manual("intern", "F2-空的已提交2")
        S.folder_update(e, fo5["id"], status="submitted", submitted_by="intern")
        r = self.post("/api/inv/audit/return", "acct", {"folderId": fo5["id"], "note": "缺票"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(S.folder_get(e, fo5["id"])["status"], "returned")

    def test_29_nan_and_huge_money_rejected(self):
        """P2：NaN/无穷大/超界金额一律 400，存不进库，票夹照常能打开。"""
        N = self.N
        fo = self.manual("intern", "P2-票夹", "30")
        it = self.scan("intern", qr(N(14), 30))["item"]
        url = "/api/inv/item/%d/update" % it["id"]
        for body in ({"lines": [{"name": "x", "qty": "nan"}]}, {"lines": [{"name": "x", "price": "inf"}]},
                     {"lines": [{"name": "x", "amount": "NaN"}]}, {"amount": "nan"}, {"total": "1e17"}, {"tax": "Infinity"}):
            r = self.post(url, "intern", {"fields": body})
            self.assertEqual(r.status_code, 400, (body, r.text))
        self.assertEqual(self.get("/api/inv/folder/%d" % fo["id"], "intern").status_code, 200)
        self.assertEqual(self.get("/api/inv/desk", "intern").status_code, 200)
        for bad in ("nan", "inf", "1e17"):
            r = self.inv.ingest_qr(fo["id"], "01,31,,%s,%s,20260901,,X" % (N(15), bad), "intern")
            self.assertEqual(r["action"], "unknown", bad)

    def test_30_poison_attachment_is_skipped(self):
        """robustness F2：上次拉某个附件时进程被杀（占位留着）→ 这次跳过它并写明原因，其余附件照拉；领取次数拉完清零。"""
        inv, S, e = self.inv, self.S, self.db._engine
        norm = {"instId": "PI-POISON", "businessId": "202609241000000003001", "template": "付款申请（公对公）",
                "title": "测试乙提交的付款申请（公对公）", "applicant": "测试乙", "company": self.tip.FAKE["buyer"],
                "amount": 10.0, "form": [], "photos": [], "hasAttachments": True,
                "attachments": [{"fileId": "F-P1", "fileName": "坏的.pdf"}, {"fileId": "F-P2", "fileName": "说明.txt"}]}
        fid = S.folder_upsert_from_approval(e, norm, "intern")
        S.folder_update(e, fid, attach_status="pending")
        S.file_insert(e, folder_id=fid, role="marker", origin="attachment", status="pulling", dt_file_id="F-P1")
        dl = MagicMock(side_effect=lambda iid, a, inst: {"ok": True, "bytes": b"plain note from dingtalk", "msg": ""})
        self.assertTrue(S.folder_claim_attach(e, fid))
        self.assertEqual(S.folder_get(e, fid)["attach_tries"], 1)
        with patch.object(inv.idt, "get_instance", MagicMock(return_value={"ok": True, "inst": {"x": 1}, "msg": ""})), \
                patch.object(inv.idt, "normalize_instance", MagicMock(return_value=norm)), \
                patch.object(inv.idt, "download_attachment", dl):
            inv._pull_attachments(fid)
        self.assertEqual([c.args[1]["fileId"] for c in dl.call_args_list], ["F-P2"])
        f = S.folder_get(e, fid)
        self.assertEqual((f["attach_status"], f["attach_tries"]), ("failed", 0))
        self.assertIn("已跳过", f["attach_msg"])
        self.assertEqual(S.file_by_dt(e, fid, "F-P1")["status"], "poison")
        items = S.folder_items(e, fid)
        self.assertEqual([(i["kind"], i["created_by"]) for i in items], [("other", "系统")])

    def test_31_failed_ingest_can_be_retried(self):
        """robustness F4：登记半路出错不留挡路的文件行，同一份文件重传能正常登记。"""
        inv = self.inv
        fo = self.manual("intern", "F4-重传")
        with patch.object(inv, "_register_doc", MagicMock(side_effect=RuntimeError("库连接断了"))):
            with self.assertRaises(RuntimeError):
                inv.ingest_bytes(fo["id"], "n.txt", b"retry me after failure 31", "upload", "intern")
        rs = inv.ingest_bytes(fo["id"], "n.txt", b"retry me after failure 31", "upload", "intern")
        self.assertEqual(rs[0]["action"], "other", rs)

    def test_32_redo_pdf_parses_file_once(self):
        """robustness F5：同一份多页扫描 PDF 的几张票，第二拨识别只解析一次、只渲染一次。"""
        inv, ip = self.inv, self.inv.ip
        calls = {"x": 0, "r": 0}
        docs = [dict(ip.new_doc(p), needOcr=True, isInvoice=True, kind="invoice") for p in range(3)]

        def fx(data):
            calls["x"] += 1
            return [dict(d) for d in docs]

        def fr(data, max_pages=5, long_side=1600):
            calls["r"] += 1
            return [{"jpeg": b"p%d" % i, "w": 1, "h": 1} for i in range(max_pages)]
        f = {"id": 32032, "sha256": "redo-once"}
        with patch.object(ip, "extract_pdf", side_effect=fx), patch.object(ip, "render_pdf", side_effect=fr), \
                patch.object(ip, "extract_image_ocr", side_effect=lambda jpeg, qr_doc=None: {"jpeg": jpeg}):
            outs = [inv._redo_pdf(b"%PDF-fake", {"page": p}, f) for p in (0, 1, 2)]
        self.assertEqual((calls["x"], calls["r"]), (1, 1))
        self.assertEqual([o["jpeg"] for o in outs], [b"p0", b"p1", b"p2"])

    def test_33_pdf_previews_cover_every_invoice_page(self):
        """C8/F11：合并 PDF 第 6 张以后的票也有自己那页的预览，不再显示成第 1 页。"""
        inv, ip = self.inv, self.inv.ip
        self.assertEqual(inv._pdf_preview_pages([{"page": 0}]), 5)
        self.assertEqual(inv._pdf_preview_pages([{"page": 7}, {"page": 2}]), 8)
        self.assertEqual(inv._pdf_preview_pages([{"page": 40}]), 30)
        fo = self.manual("intern", "C8-多页")
        docs = []
        for p in range(7):
            d = ip.new_doc(p)
            d.update(isInvoice=True, kind="invoice", number=self.N(3300 + p), total=10.0 + p, date="2026-09-01")
            docs.append(d)
        seen = {}

        def fr(data, max_pages=5, long_side=1600):
            seen["max"] = max_pages
            return [{"jpeg": b"\xff\xd8\xff-page%d" % i, "w": 10, "h": 10} for i in range(max_pages)]
        with patch.object(ip, "extract_pdf", MagicMock(return_value=docs)), patch.object(ip, "render_pdf", side_effect=fr):
            rs = inv.ingest_bytes(fo["id"], "合并.pdf", b"%PDF-1.4 fake merged 33", "upload", "intern")
        self.assertEqual(seen["max"], 7)
        inv.attach_views(rs)
        last = rs[-1]["item"]
        self.assertEqual(last["page"], 6)
        self.assertEqual(len(last["file"]["preview"]), 7)
        self.assertTrue(last["file"]["preview"][6].endswith("page=6"))

    def test_34_view_only_user_can_open_folder(self):
        """C6/F14：只开了收票工作台页面的人能点开票夹看，但不能放票。"""
        fo = self.manual("intern", "C6-只看")
        r = self.post("/api/inv/desk/open", "viewer", {"folderId": fo["id"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["folder"]["id"], fo["id"])
        self.assertEqual(self.post("/api/inv/desk/close", "viewer").status_code, 200)
        self.assertEqual(self.upload("viewer", "v.txt", b"viewer upload", "text/plain").status_code, 403)
        self.assertEqual(self.post("/api/inv/desk/open", "viewer", {"folderId": 99999999}).status_code, 404)

    def test_35_folder_logs_include_item_entries(self):
        """C7：票夹详情的留痕并上逐张票自己的留痕，带 itemId/laterId。"""
        fo = self.manual("intern", "C7-留痕")
        it = self.scan("intern", qr(self.N(16), 12))["item"]
        self.S.log_add(self.db._engine, "acct", "税局对账", item_id=it["id"], detail={"verify": "green"})
        logs = self.items(fo["id"])["logs"]
        hit = [x for x in logs if x["action"] == "税局对账"]
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["itemId"], it["id"])
        self.assertIn("laterId", hit[0])

    def test_36_dingtalk_send_runs_off_event_loop(self):
        """P1/F3：退回发钉钉在线程池里发，不占住整站的事件循环。"""
        import asyncio
        fo = self.manual("intern", "P1-退回")
        self.scan("intern", qr(self.N(17), 40))
        self.assertEqual(self.post("/api/inv/folder/%d/submit" % fo["id"], "intern").status_code, 200)
        seen = []

        def fake_notify(uids, text):
            try:
                asyncio.get_running_loop()
                seen.append("loop")
            except RuntimeError:
                seen.append("thread")
            return {"sent": False, "msg": "dry-run"}
        with patch.object(self.inv, "notify_dt", fake_notify):
            r = self.post("/api/inv/audit/return", "acct", {"folderId": fo["id"], "note": "缺原件"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(seen, ["thread"])

    def test_37_later_recalculated_when_item_removed(self):
        """money F3：挂着后补单的票被移除 → 后补单已到金额、收齐状态跟着退回（不会停在"已收齐"漏催）。"""
        S, e = self.S, self.db._engine
        fo = self.manual("intern", "F3-后补")
        lid = S.later_insert(e, folder_id=fo["id"], inst_id="", expect_amount=300, expect_date="2026-12-31",
                             receiver="maker", filed_by="maker")
        r = self.inv.ingest_qr(fo["id"], qr(self.N(18), 300), "maker", "scanner", later_id=lid)
        self.assertEqual(r["action"], "item", r)
        S.later_update(e, lid, status="done", received_amount=300)
        r = self.post("/api/inv/item/%d/remove" % r["itemId"], "acct", {"note": "扫错了"})
        self.assertEqual(r.status_code, 200, r.text)
        l = S.later_get(e, lid)
        self.assertEqual((l["status"], l["received_amount"]), ("open", 0.0))

    def test_38_corp_id_and_template_guard(self):
        """SEC-7/SEC-8：corpId 只从钉钉服务器回来的跳转里学、只在空时学；非发票管家模板的审批单不许建票夹。"""
        inv, S, e = self.inv, self.S, self.db._engine
        keep = inv.get_settings().get("corpId") or ""
        inv.save_settings({"corpId": ""}, "test")
        try:
            with patch.object(inv.idt, "get_instance", MagicMock(return_value={"ok": False, "inst": None, "msg": "nope"})):
                r = self.post("/api/inv/desk/scan", "intern",
                              {"code": "https://evil.example/?procInstId=abc123&corpid=dingEVILCORP"})
            self.assertEqual(r.status_code, 400)
            self.assertEqual(inv.get_settings()["corpId"], "")
            norm = {"instId": "PI-SEC7", "businessId": "202609241000000003801", "template": "付款申请（公对公）",
                    "title": "测试丙提交的付款申请（公对公）", "applicant": "测试丙", "form": [], "attachments": [],
                    "photos": [], "hasAttachments": False}
            ok = MagicMock(return_value={"ok": True, "inst": {"x": 1}, "msg": ""})
            with patch.object(inv.idt, "get_instance", ok), \
                    patch.object(inv.idt, "normalize_instance", MagicMock(return_value=norm)):
                s = self.scan("intern", "https://aflow.dingtalk.com/dingtalk/web/query?procInstId=PI-SEC7&corpid=dingSELF")
                self.assertEqual(s["action"], "folder", s)
                self.assertEqual(inv.get_settings()["corpId"], "")          # 原文自带的 corpid 不学
                with patch.object(inv.idt, "resolve_link", MagicMock(return_value={"ok": True, "procInstId": "PI-SEC7",
                                                                                   "corpId": "dingSERVER", "msg": ""})):
                    self.scan("intern", "https://aflow.dingtalk.com/qr/sec7")
                self.assertEqual(inv.get_settings()["corpId"], "dingSERVER")
                with patch.object(inv.idt, "resolve_link", MagicMock(return_value={"ok": True, "procInstId": "PI-SEC7",
                                                                                   "corpId": "dingOTHER", "msg": ""})):
                    self.scan("intern", "https://aflow.dingtalk.com/qr/sec7b")
                self.assertEqual(inv.get_settings()["corpId"], "dingSERVER")  # 已有值不自动改
            hr = dict(norm, instId="PI-SEC8", template="调薪申请", title="测试丙提交的调薪申请")
            with patch.object(inv.idt, "resolve_link", MagicMock(return_value={"ok": True, "procInstId": "PI-SEC8",
                                                                               "corpId": "", "msg": ""})), \
                    patch.object(inv.idt, "get_instance", ok), \
                    patch.object(inv.idt, "normalize_instance", MagicMock(return_value=hr)):
                r = self.post("/api/inv/desk/scan", "intern", {"code": "https://aflow.dingtalk.com/qr/sec8"})
            self.assertEqual(r.status_code, 400)
            self.assertIn("不是发票管家接的审批模板", r.json()["msg"])
            self.assertIsNone(S.folder_by_inst(e, "PI-SEC8"))
        finally:
            inv.save_settings({"corpId": keep}, "test")

    def test_39_zip_budget_and_disk_floor(self):
        """SEC-6：一次请求/一次拉附件里的压缩包解开总量共用一份额度；磁盘快满就不落文件（回中文错误、不留半截行）。"""
        import io
        import zipfile
        inv, S, e = self.inv, self.S, self.db._engine
        fo = self.manual("intern", "SEC6-压缩包")

        def zbytes(files):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for n, b in files:
                    z.writestr(n, b)
            return buf.getvalue()
        z1 = zbytes([("a.txt", b"a" * 1500 + b"-39-1")])
        budget = {"left": 10 ** 6}
        rs = inv.ingest_bytes(fo["id"], "z1.zip", z1, "upload", "intern", budget=budget)
        self.assertEqual([r["action"] for r in rs], ["other"])
        self.assertEqual(budget["left"], 10 ** 6 - 1505)
        rs = inv.ingest_bytes(fo["id"], "z2.zip", zbytes([("b.txt", b"b" * 50)]), "upload", "intern", budget={"left": 0})
        self.assertEqual(rs[0]["action"], "error")
        self.assertIn("解开后太大", rs[0]["msg"])
        # 同一次上传的几个压缩包共用额度：额度按整次请求算，不是每个包各 200MB
        with patch.object(inv, "MAX_TOTAL", 1500):
            r = self.c.post("/api/inv/desk/upload", headers=self.H("intern"), data={"origin": "upload"},
                            files=[("files", ("z3.zip", zbytes([("c.txt", b"c" * 1500)]), "application/zip")),
                                   ("files", ("z4.zip", zbytes([("d.txt", b"d" * 20)]), "application/zip"))])
        self.assertEqual(r.status_code, 200, r.text)
        acts = [(x["name"], x["action"]) for x in r.json()["results"]]
        self.assertIn(("z4.zip", "error"), acts)
        self.assertNotIn("d.txt", " ".join(x["name"] for x in r.json()["results"] if x["action"] == "other"))
        # 磁盘快满：不落文件、回中文错误、库里不留行
        before = len(S.files_of_folder(e, fo["id"], include_removed=True))
        with patch.object(inv, "MIN_FREE_MB", 10 ** 12):
            rs = inv.ingest_bytes(fo["id"], "n.txt", b"disk nearly full 39", "upload", "intern")
        self.assertEqual(rs[0]["action"], "error")
        self.assertIn("磁盘剩余空间不足", rs[0]["msg"])
        self.assertEqual(len(S.files_of_folder(e, fo["id"], include_removed=True)), before)

    def test_40_upload_request_limits(self):
        """SEC-6：不带长度的上传（分块传输）直接拒；一次文件数超上限拒。"""
        self.manual("intern", "SEC6-请求上限")

        def gen():
            yield b"--x\r\nContent-Disposition: form-data; name=\"files\"; filename=\"a.txt\"\r\n\r\nhello\r\n--x--\r\n"
        r = self.c.post("/api/inv/desk/upload", content=gen(),
                        headers=dict(self.H("intern"), **{"content-type": "multipart/form-data; boundary=x"}))
        self.assertEqual(r.status_code, 411, r.text)
        with patch.object(self.inv, "MAX_FILES", 3):
            r = self.c.post("/api/inv/desk/upload", headers=self.H("intern"), data={"origin": "upload"},
                            files=[("files", ("f%d.txt" % i, b"n%d" % i, "text/plain")) for i in range(5)])
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("一次最多传", r.json()["msg"])


if __name__ == "__main__":
    unittest.main()
