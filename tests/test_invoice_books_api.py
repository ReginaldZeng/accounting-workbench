# [Change Log]
# Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家）
# Description: 发票管家台账与后补池路由（routers/invoice_books.py）接口测试：临时 SQLite + 临时上传目录，只挂两个发票路由到裸 FastAPI，
#   core 用桩（x-test-user 头＝登录名，真账号真权限走 db.user_can）。全部合成数据（税局清单/抵扣清单/期初清单用 openpyxl 现造，
#   电子发票用合成 XML）；钉钉全部 mock，INV_DRY_SEND=1 绝不真发；INV_WORKER_OFF=1 不起后台线程；SQLite 下催票线程本就不起。
#   运行：repo 根目录 PYTHONPATH=01_Current_Deliverables/app/backend python -m unittest tests.test_invoice_books_api -v
import io
import json
import os
import sys
import tempfile
import types
import unittest
import zipfile
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock
from urllib.parse import quote

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


HAS_XL = _has("openpyxl")
ENTERS = ("enter:invdesk", "enter:invlater", "enter:invaudit", "enter:invledger")
MODS = ("db", "core", "routers.invoice", "routers.invoice_books", "routers.invoice_self")


def qr(num, total, qtype="31", dt="20260901"):
    """合成发票二维码（数电 20 位号码，金额＝价税合计）。"""
    return "01,%s,,%s,%.2f,%s,,ABCD" % (qtype, num, total, dt)


def num(tag):
    """测试用 20 位数电票号码（6449 开头避开别的测试）。"""
    return "26449900000000%06d" % tag


def xlsx(headers, rows, title=None):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    if title:
        ws.append([title])
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def einv_xml(number, seller, seller_tid, buyer, buyer_tid, amount, tax, total, day="2026-09-01"):
    """合成数电 XML 原件（结构照 kernels/test_invoice_parse 的 test_xml）。"""
    x = """<?xml version="1.0" encoding="UTF-8"?>
<EInvoice xmlns="urn:test:einvoice">
  <Header><EIid>{n}</EIid><InherentLabel><GeneralOrSpecialVAT><LabelCode>01</LabelCode>
    <LabelName>增值税专用发票</LabelName></GeneralOrSpecialVAT></InherentLabel></Header>
  <EInvoiceData>
    <SellerInformation><SellerIdNum>{st}</SellerIdNum><SellerName>{s}</SellerName></SellerInformation>
    <BuyerInformation><BuyerIdNum>{bt}</BuyerIdNum><BuyerName>{b}</BuyerName></BuyerInformation>
    <BasicInformation><TotalAmWithoutTax>{a:.2f}</TotalAmWithoutTax><TotalTaxAm>{t:.2f}</TotalTaxAm>
      <TotalTax-includedAmount>{tt:.2f}</TotalTax-includedAmount>
      <RequestTime>{d} 10:11:12</RequestTime></BasicInformation>
    <IssuItemInformation><ItemName>*信息技术服务*软件维护服务费</ItemName><SpecMod>V1</SpecMod><MeaUnits>项</MeaUnits>
      <Quantity>1</Quantity><UnPrice>{a:.2f}</UnPrice><Amount>{a:.2f}</Amount><TaxRate>0.06</TaxRate><ComTaxAm>{t:.2f}</ComTaxAm></IssuItemInformation>
    <IssuItemInformation><ItemName>*现代服务*咨询费</ItemName><Quantity>1</Quantity><UnPrice>0</UnPrice>
      <Amount>0.00</Amount><TaxRate>6%</TaxRate><ComTaxAm>0.00</ComTaxAm></IssuItemInformation>
  </EInvoiceData>
  <TaxSupervisionInfo><InvoiceNumber>{n}</InvoiceNumber><IssueTime>{d}</IssueTime></TaxSupervisionInfo>
</EInvoice>""".format(n=number, s=seller, st=seller_tid, b=buyer, bt=buyer_tid, a=amount, t=tax, tt=total, d=day)
    return x.encode("utf-8")


class InvoiceBooksApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="inv-books-")
        tmp = Path(cls.tmp.name)
        cls.env = {k: os.environ.get(k) for k in ("DB_URL", "INV_WORKER_OFF", "INV_DRY_SEND")}
        os.environ["DB_URL"] = "sqlite:///" + (tmp / "t.sqlite").as_posix()
        os.environ["INV_WORKER_OFF"] = "1"
        os.environ["INV_DRY_SEND"] = "1"
        cls.mods = {k: sys.modules.get(k) for k in MODS}
        for k in MODS:
            sys.modules.pop(k, None)
        # 同一进程里别的测试可能已把 routers.invoice 挂在包上：from routers import invoice 会直接拿旧模块，先摘掉
        pkg = sys.modules.get("routers")
        cls.pkg_attrs = {}
        if pkg is not None:
            for a in ("invoice", "invoice_books", "invoice_self"):
                if hasattr(pkg, a):
                    cls.pkg_attrs[a] = getattr(pkg, a)
                    delattr(pkg, a)
        import db
        cls.db = db
        db.set_nav_caps_provider(lambda: [{"key": k, "label": k, "ws": "accounting", "kind": "nav", "tier": "nav",
                                           "mod": k.split(":")[1]} for k in ENTERS])

        def cur(request):
            name = request.headers.get("x-test-user")
            return db.get_user(name) if name else None
        core = types.ModuleType("core")
        core.db = db
        core.JSONResponse = JSONResponse
        core._current_user = cur
        core._require_perm = lambda request, cap: (lambda u: u if db.user_can(u, cap) else None)(cur(request))
        sys.modules["core"] = core
        from routers import invoice as inv
        from routers import invoice_books as books
        from routers import invoice_self as sf
        cls.sf = sf
        from kernels import invoice_store as S
        from kernels import invoice_excel as ie
        from kernels import test_invoice_parse as tip
        cls.inv, cls.books, cls.S, cls.ie, cls.tip = inv, books, S, ie, tip
        inv.UPLOAD_DIR = str(tmp / "inv_uploads")
        app = FastAPI()
        app.include_router(inv.router)
        app.include_router(books.router)
        app.include_router(sf.router)
        cls.c = TestClient(app)

        def mk(name, perms=None, role="normal"):
            db.create_user(name, "x-test-pwd", grp="核算组", role=role, perms={k: True for k in (perms or [])})
        mk("boss", role="admin")
        mk("intern", ["enter_accounting", "enter:invdesk", "inv_intake"])
        mk("acct", ["enter_accounting", "enter:invaudit", "inv_audit", "enter:invledger", "inv_deduct"])
        mk("keeper", ["enter_accounting", "enter:invledger", "inv_unbind", "inv_opening"])
        mk("recv", ["enter_accounting", "enter:invlater", "inv_receive"])
        mk("recv2", ["enter_accounting", "enter:invlater", "inv_receive"])
        mk("viewer", ["enter_accounting", "enter:invledger"])
        mk("nogate", ["enter_accounting", "inv_deduct", "inv_unbind", "inv_opening", "inv_receive"])
        f = tip.FAKE
        cls.OUR, cls.OUR_TID = f["buyer"], f["buyer_tid"]
        cls.OTHER_TID = tip._uscc("91440300MA99WXYW9")
        db.set_setting("inv_config", {
            "company": [{"name": cls.OUR, "taxId": cls.OUR_TID}],
            "people": [{"account": "intern", "dtUserid": "dt-intern", "dtName": "实习生甲", "receiver": False},
                       {"account": "recv", "dtUserid": "dt-recv", "dtName": "接收人甲", "receiver": True},
                       {"account": "recv2", "dtUserid": "dt-recv2", "dtName": "接收人乙", "receiver": True}]}, "test")
        cls.e = db._engine

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
            for a in ("invoice", "invoice_books", "invoice_self"):
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
    def H(self, user):
        return {"x-test-user": user}

    def post(self, url, user, body=None):
        return self.c.post(url, json=body if body is not None else {}, headers=self.H(user))

    def get(self, url, user):
        return self.c.get(url, headers=self.H(user))

    def upload(self, url, user, files, field="files"):
        return self.c.post(url, files=[(field, f) for f in files], headers=self.H(user))

    def ok(self, r):
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def item(self, iid):
        return self.S.item_get(self.e, iid)

    def make_item(self, n, total, qtype="31", dt="20260901", fields=None, approve=True, decisions=None, submit=True):
        """收票工作台走一遍：手工票夹 → 扫发票码 →（可选改字段）→ 提交 → 会计审核通过。→ (票 id, 票夹 id)。"""
        fo = self.ok(self.post("/api/inv/folder/manual", "intern", {"title": "测试票夹-" + n[-6:], "amount": total,
                                                                   "company": self.OUR}))["folder"]
        s = self.ok(self.post("/api/inv/desk/scan", "intern", {"code": qr(n, total, qtype, dt)}))
        self.assertEqual(s["action"], "item", s)
        iid = s["item"]["id"]
        if fields:
            self.S.item_update(self.e, iid, **fields)
        if submit:
            self.ok(self.post("/api/inv/folder/%d/submit" % fo["id"], "intern"))
        if approve:
            body = {"folderId": fo["id"], "itemIds": [iid]}           # C2：审核要带上看到的待审票
            if decisions is not None:
                body["decisions"] = {str(iid): decisions}
            self.ok(self.post("/api/inv/audit/approve", "acct", body))
        return iid, fo["id"]

    def later_row(self, **kw):
        base = dict(inst_id="", business_id="", template="付款申请（公对公）", applicant="申请人丁", payee_name="催票测试供应商",
                    pay_amount=500, expect_amount=500, inv_kind="special", receiver="recv2", receiver_uid="dt-recv2",
                    receiver_name="接收人乙", filed_by="recv", filed_via="proxy", applicant_uid="dt-app2")
        base.update(kw)
        return self.S.later_insert(self.e, **base)

    # ── 权限 ──
    def test_01_perms(self):
        self.assertEqual(self.c.get("/api/inv/ledger").status_code, 401)
        self.assertEqual(self.get("/api/inv/ledger", "nogate").status_code, 403)
        self.assertEqual(self.get("/api/inv/ledger", "viewer").status_code, 200)
        self.assertEqual(self.post("/api/inv/item/1/void", "viewer", {"note": "x"}).status_code, 403)
        self.assertEqual(self.post("/api/inv/item/1/void", "nogate", {"note": "x"}).status_code, 403)
        r = self.upload("/api/inv/taxlist/import", "keeper", [("a.xlsx", b"x", "application/octet-stream")], "file")
        self.assertEqual(r.status_code, 403)
        self.assertIn("税局对账", r.json()["msg"])
        self.assertEqual(self.upload("/api/inv/opening/preview", "acct", [("a.xlsx", b"x", "x")], "file").status_code, 403)
        self.assertEqual(self.get("/api/inv/later", "viewer").status_code, 403)
        self.assertEqual(self.get("/api/inv/later", "recv").status_code, 200)
        self.assertEqual(self.post("/api/inv/later/create", "nogate", {}).status_code, 403)
        self.assertEqual(self.post("/api/inv/later/resolve", "viewer", {"code": "x"}).status_code, 403)
        self.assertEqual(self.get("/api/inv/sellers", "viewer").status_code, 200)
        self.assertEqual(self.post("/api/inv/sellers/check", "viewer", {"taxId": "X"}).status_code, 403)
        self.assertEqual(self.get("/api/inv/deduct/download?token=abc", "acct").status_code, 400)
        self.assertEqual(self.get("/api/inv/opening/stats", "viewer").status_code, 200)

    # ── 台账 ──
    @unittest.skipUnless(HAS_XL, "缺 openpyxl")
    def test_02_ledger_filters_paging_export(self):
        a, _ = self.make_item(num(2001), 100, "31", "20260902", {"seller_name": "台账销方甲"})
        b, _ = self.make_item(num(2002), 200, "32", "20260905", {"seller_name": "台账销方乙"})
        c, _ = self.make_item(num(2003), 300, "31", "20260910", {"seller_name": "台账销方甲"})
        self.make_item(num(2004), 400, approve=False, submit=False)
        q = "2644990000000000200"
        j = self.ok(self.get("/api/inv/ledger?q=%s" % q, "viewer"))
        self.assertEqual(j["total"], 3)
        self.assertEqual([x["id"] for x in j["rows"]], [c, b, a])          # 开票日期倒序
        self.assertEqual(j["sum"]["total"], 600.0)
        self.assertEqual(j["rows"][0]["folder"]["title"], "测试票夹-002003")
        self.assertEqual(j["rows"][0]["review"], "approved")
        self.assertEqual(self.ok(self.get("/api/inv/ledger?q=%s&review=all" % q, "viewer"))["total"], 4)
        j = self.ok(self.get("/api/inv/ledger?q=%s&from=2026/9/3&to=2026-09-30" % q, "viewer"))
        self.assertEqual([x["id"] for x in j["rows"]], [c, b])
        self.assertEqual([x["id"] for x in self.ok(self.get("/api/inv/ledger?q=%s&invType=normal" % q, "viewer"))["rows"]], [b])
        j = self.ok(self.get("/api/inv/ledger?q=%s&seller=%s" % (q, quote("台账销方甲")), "viewer"))
        self.assertEqual(sorted(x["id"] for x in j["rows"]), sorted([a, c]))
        p1 = self.ok(self.get("/api/inv/ledger?q=%s&page=1&size=2" % q, "viewer"))
        p2 = self.ok(self.get("/api/inv/ledger?q=%s&page=2&size=2" % q, "viewer"))
        self.assertEqual((p1["total"], len(p1["rows"]), len(p2["rows"])), (3, 2, 1))
        self.assertEqual(self.get("/api/inv/ledger?from=abc", "viewer").status_code, 400)
        r = self.get("/api/inv/ledger/export?q=%s" % q, "viewer")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("filename*=UTF-8''" + quote("发票台账_"), r.headers["content-disposition"])
        from openpyxl import load_workbook
        ws = load_workbook(io.BytesIO(r.content)).active
        vals = list(ws.values)
        cols = self.ie.LEDGER_COLUMNS + self.books.LEDGER_EXTRA_COLUMNS
        self.assertEqual(list(vals[0]), cols)
        self.assertEqual(len(vals), 1 + 3 + 1)                              # 表头 + 3 行 + 合计行
        self.assertEqual(vals[1][cols.index("发票号码")], num(2003))
        self.assertEqual(vals[1][cols.index("审核状态")], "已审核")
        self.assertEqual(vals[-1][cols.index("价税合计")], 600)

    def test_03_void_frees_dedup(self):
        a, _ = self.make_item(num(3001), 88)
        self.ok(self.post("/api/inv/folder/manual", "intern", {"title": "第二张单", "company": self.OUR}))
        d = self.ok(self.post("/api/inv/desk/scan", "intern", {"code": qr(num(3001), 88)}))
        self.assertEqual(d["action"], "dup", d)
        dup_id = d["item"]["id"]
        self.assertEqual(self.post("/api/inv/item/%d/void" % a, "keeper", {"note": " "}).status_code, 400)
        j = self.ok(self.post("/api/inv/item/%d/void" % a, "keeper", {"note": "重复报销，作废原登记"}))
        self.assertEqual((j["item"]["review"], j["item"]["voidBy"]), ("void", "keeper"))
        self.assertIn("号码已放出来", j["msg"])
        self.assertNotIn("dup", self.item(dup_id)["flags_json"])            # 另一张不再算重复
        self.assertEqual(self.post("/api/inv/item/%d/void" % a, "keeper", {"note": "再来"}).status_code, 400)
        self.assertEqual(self.post("/api/inv/item/99999999/void", "keeper", {"note": "x"}).status_code, 404)
        self.assertEqual(self.ok(self.get("/api/inv/ledger?q=%s" % num(3001), "viewer"))["total"], 0)
        self.assertEqual(self.ok(self.get("/api/inv/ledger?q=%s&review=void" % num(3001), "viewer"))["total"], 1)
        logs = self.S.logs_of(self.e, item_id=a)
        self.assertIn("作废票据", [x["action"] for x in logs])

    # ── 税局对账 ──
    @unittest.skipUnless(HAS_XL, "缺 openpyxl")
    def test_04_taxlist_colors(self):
        S, e, OUR, TID = self.S, self.e, self.OUR, self.OUR_TID
        ours = {"buyer_tax_id": TID, "buyer_name": OUR}
        g, _ = self.make_item(num(4001), 106, "31", "20260810", ours)
        r, _ = self.make_item(num(4002), 212, "31", "20260812", ours)
        y, _ = self.make_item(num(4003), 318, "31", "20260815", ours)
        yout, _ = self.make_item(num(4004), 50, "31", "20260701", dict(ours, verify="green"))
        qd, _ = self.make_item(num(4005), 20, "31", "20260816", dict(ours, inv_type="quota"))
        ob, _ = self.make_item(num(4006), 30, "31", "20260817", {"buyer_tax_id": self.OTHER_TID, "buyer_name": "别家公司"})
        fo = S.folder_create_manual(e, "收据票夹", 50, OUR, "intern")
        rc = S.item_insert(e, folder_id=fo, kind="receipt", origin="upload", issue_date="2026-08-18", total=50,
                           review="approved", created_by="intern")
        later = self.later_row(payee_name="后补供应商有限公司", business_id="202608010000000001")
        S.opening_insert_many(e, [{"dup_key": "N:" + num(4011), "number": num(4011), "total": 9}], 0, "票总管", "test")
        H = ["数电票号码", "开票日期", "销方识别号", "销方名称", "购方识别号", "购方名称", "金额", "税额", "价税合计", "发票状态", "发票票种"]
        rows = [
            [num(4001), "2026-08-10", self.tip.FAKE["seller_tid"], "对账销方甲有限公司", TID, OUR, 100, 6, 106, "正常", "数电专票"],
            [num(4002), "2026-08-12", "91X", "对账销方乙", TID, OUR, 200, 12, 212, "已作废", "数电专票"],
            [num(4009), "2026-08-31", "91Y", "后补供应商有限公司", TID, OUR, 500, 0, 500, "正常", "数电普票"],   # 税局有台账没有
            [num(4010), "2026-08-01", "91Z", "别家销方", self.OTHER_TID, "别家公司", 10, 0, 10, "正常", "数电普票"],
            [num(4011), "2026-08-20", "91W", "期初销方", TID, OUR, 9, 0, 9, "正常", "数电普票"],          # 期初里有
            [num(4012), "2026-08-21", "91V", "作废销方", TID, OUR, 9, 0, 9, "已作废", "数电普票"],
            [num(4013), "2026-08-22", "91U", "红字销方", TID, OUR, -9, 0, -9, "正常", "数电普票"],
            [float(num(4014)), "2026-08-23", "91T", "丢位销方", TID, OUR, 9, 0, 9, "正常", "数电普票"],  # 号码按数字存：丢位
        ]
        data = xlsx(H, rows, title="全量发票查询 取得发票")
        up = self.upload("/api/inv/taxlist/import", "acct", [("取得发票.xlsx", data, "application/octet-stream")], "file")
        j = self.ok(up)
        rep = j["report"]
        self.assertEqual(rep["counts"], {"green": 1, "red": 1, "yellow": 1, "gray": 3}, rep)
        self.assertEqual([x["number"] for x in rep["notInLedger"]], [num(4009)])
        self.assertEqual([h["laterId"] for h in rep["laterHints"] if h["number"] == num(4009)], [later])
        self.assertEqual([x["id"] for x in rep["notInList"]], [y])
        self.assertEqual([x["id"] for x in rep["red"]], [r])
        self.assertEqual(rep["red"][0]["status"], "已作废")
        self.assertTrue(any("15 位" in w for w in rep["warnings"]), rep["warnings"])
        self.assertEqual((rep["listStats"]["dateFrom"], rep["listStats"]["dateTo"]), ("2026-08-01", "2026-08-31"))
        self.assertTrue(rep["mappingLines"])
        self.assertEqual(j["batch"]["rows"], 8)
        vg = self.item(g)
        self.assertEqual((vg["verify"], vg["seller_name"], vg["amount"], vg["tax"]), ("green", "对账销方甲有限公司", 100.0, 6.0))
        self.assertEqual(vg["field_src_json"]["sellerName"]["src"], "taxlist")
        self.assertEqual(vg["field_src_json"]["number"]["src"], "qr")      # 二维码来的不动
        self.assertEqual(self.item(r)["verify"], "red")
        self.assertEqual(self.item(y)["verify"], "yellow")
        self.assertEqual(self.item(yout)["verify"], "green")                # 清单日期范围外：保持原样
        for x in (qd, ob, rc):
            self.assertEqual(self.item(x)["verify"], "gray", x)
        self.assertIn("不是本公司", self.item(ob)["verify_note"])
        self.assertEqual([x["id"] for x in self.ok(self.get("/api/inv/ledger?verify=red", "viewer"))["rows"]], [r])
        self.assertTrue(any(x["action"] == "税局对账" for x in S.logs_of(e, item_id=y)))
        # 报告能再取（最近一批 / 指定批次）
        rr = self.ok(self.get("/api/inv/taxlist/report", "viewer"))
        self.assertEqual(rr["report"]["counts"], rep["counts"])
        self.assertEqual(rr["batch"]["id"], j["batch"]["id"])
        self.assertEqual(self.ok(self.get("/api/inv/taxlist/report?batch=%d" % j["batch"]["id"], "viewer"))["batch"]["name"],
                         "取得发票.xlsx")
        self.assertEqual(self.get("/api/inv/taxlist/report?batch=999999", "viewer").status_code, 404)
        # 清单没导出购方列：全部按开给本公司的票处理（税局有台账没有照样列出来）
        nb = xlsx(["数电票号码", "开票日期", "销方名称", "价税合计", "发票状态"],
                  [[num(4001), "2026-08-10", "对账销方甲有限公司", 106, "正常"], [num(4020), "2026-08-11", "无购方列销方", 7, "正常"]])
        j2 = self.ok(self.upload("/api/inv/taxlist/import", "acct", [("取得发票2.xlsx", nb, "application/octet-stream")], "file"))
        self.assertEqual([x["number"] for x in j2["report"]["notInLedger"]], [num(4020)])
        self.assertEqual(j2["report"]["counts"]["green"], 1)
        self.assertTrue(any("没有购方列" in w for w in j2["report"]["warnings"]), j2["report"]["warnings"])
        # 读不出来的文件：400 且说明原因
        bad = self.upload("/api/inv/taxlist/import", "acct", [("x.xlsx", b"not an excel", "application/octet-stream")], "file")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("税局清单没导进去", bad.json()["msg"])

    # ── 税局文件包 ──
    def test_05_taxpack_fill(self):
        f = self.tip.FAKE
        t, fo = self.make_item(num(5001), 1272)
        self.assertIsNone(self.item(t)["seller_name"])
        good = einv_xml(num(5001), f["seller"], f["seller_tid"], f["buyer"], f["buyer_tid"], 1200, 72, 1272)
        other = einv_xml(num(5999), f["seller"], f["seller_tid"], f["buyer"], f["buyer_tid"], 10, 0.6, 10.6)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("发票包/%s.xml" % num(5001), good)
            z.writestr("发票包/说明.txt", "只是说明")
        files = [("税局下载.zip", buf.getvalue(), "application/zip"), ("另一张.xml", other, "application/xml")]
        self.assertEqual(self.upload("/api/inv/taxpack/import", "keeper", files).status_code, 403)
        j = self.ok(self.upload("/api/inv/taxpack/import", "acct", files))
        self.assertEqual(j["filled"], [t], j)
        self.assertEqual([u["number"] for u in j["unmatched"]], [num(5999)])
        self.assertTrue(any("说明.txt" in s["name"] for s in j["skipped"]), j["skipped"])
        it = self.item(t)
        self.assertEqual((it["seller_name"], it["seller_tax_id"], it["amount"], it["tax"]),
                         (f["seller"], f["seller_tid"], 1200.0, 72.0))
        self.assertEqual(it["field_src_json"]["sellerName"]["src"], "taxpack")
        self.assertEqual(it["field_src_json"]["number"]["src"], "qr")
        self.assertEqual(len(it["lines_json"]), 2)
        fl = self.S.file_get(self.e, it["file_id"])
        self.assertEqual((fl["origin"], fl["mime"], fl["item_id"]), ("taxpack", "application/xml", t))
        self.assertEqual(it["review"], "approved")
        self.assertTrue(any(x["action"] == "税局文件包补全" for x in self.S.logs_of(self.e, item_id=t)))
        again = self.ok(self.upload("/api/inv/taxpack/import", "acct", files[:1]))
        self.assertEqual((again["filled"], again["already"]), ([], [t]))

    # ── 抵扣勾选 ──
    @unittest.skipUnless(HAS_XL, "缺 openpyxl")
    def test_06_deduct_roundtrip(self):
        a, _ = self.make_item(num(6001), 106)                                  # 专票，系统建议可抵、会计没改
        b, _ = self.make_item(num(6002), 106, decisions={"deductible": False})   # 会计判不可抵
        c, _ = self.make_item(num(6003), 106, approve=False)                   # 已提交、没审核
        H = ["序号", "数电票号码", "开票日期", "销方纳税人识别号", "销方名称", "金额", "税额", "有效抵扣税额", "是否勾选"]
        rows = [[1, num(6001), "2026-09-01", "91A", "甲", 100, 6, 6, None],
                [2, num(6002), "2026-09-01", "91B", "乙", 100, 6, 6, None],
                [3, num(6003), "2026-09-01", "91C", "丙", 100, 6, 6, None],
                [4, num(6004), "2026-09-01", "91D", "丁", 100, 6, 6, None],
                [5, float(num(6005)), "2026-09-01", "91E", "戊", 100, 6, 6, None]]
        data = xlsx(H, rows, title="抵扣类勾选 未勾选清单")
        j = self.ok(self.upload("/api/inv/deduct/prepare", "acct", [("未勾选.xlsx", data, "application/octet-stream")], "file"))
        s = j["summary"]
        self.assertEqual((s["rows"], s["yes"], s["no"], s["unknown"], s["notInLedger"], s["notApproved"], s["numberLost"]),
                         (5, 1, 1, 3, 2, 1, 1), s)
        dec = {p["number"]: (p["decision"], p["reason"]) for p in j["preview"]}
        self.assertEqual(dec[num(6001)][0], "是")
        self.assertEqual(dec[num(6002)][0], "否")
        self.assertIn("会计判定不可抵扣", dec[num(6002)][1])
        self.assertIn("审核", dec[num(6003)][1])
        self.assertIn("台账里没有", dec[num(6004)][1])
        self.assertEqual(self.item(a)["deduct_status"], "")                  # 下载前不动
        self.assertEqual(self.get("/api/inv/deduct/download?token=%s" % j["token"], "viewer").status_code, 403)
        r = self.get("/api/inv/deduct/download?token=%s" % j["token"], "acct")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn(quote("抵扣勾选_已标注_"), r.headers["content-disposition"])
        from openpyxl import load_workbook
        ws = load_workbook(io.BytesIO(r.content)).active
        vals = list(ws.values)
        self.assertEqual(vals[0][0], "抵扣类勾选 未勾选清单")                 # 原样：标题行还在
        tick = {v[1] if isinstance(v[1], str) else "lost": v[8] for v in vals[2:]}
        self.assertEqual((tick[num(6001)], tick[num(6002)], tick[num(6003)], tick[num(6004)]), ("是", "否", None, None))
        self.assertEqual(self.item(a)["deduct_status"], "marked")
        self.assertEqual(self.item(b)["deduct_status"], "")
        self.assertEqual(self.item(c)["deduct_status"], "")
        self.assertEqual([x["id"] for x in self.ok(self.get("/api/inv/ledger?deduct=marked&q=%s" % num(6001)[:-1], "viewer"))["rows"]], [a])
        # 凭证过期 / 乱写
        meta_p = os.path.join(self.inv.UPLOAD_DIR, "_tmp", j["token"] + ".json")
        m = json.loads(Path(meta_p).read_text(encoding="utf-8"))
        m["created"] -= 3 * 3600
        Path(meta_p).write_text(json.dumps(m), encoding="utf-8")
        r = self.get("/api/inv/deduct/download?token=%s" % j["token"], "acct")
        self.assertEqual(r.status_code, 400)
        self.assertIn("过期", r.json()["msg"])
        self.assertEqual(self.get("/api/inv/deduct/download?token=../../x", "acct").status_code, 400)

    # ── 新销方 ──
    @unittest.skipUnless(HAS_XL, "缺 openpyxl")
    def test_07_sellers(self):
        tid = self.tip._uscc("91330000MA77XYTW7")
        iid, _ = self.make_item(num(7001), 66)
        self.S.item_update(self.e, iid, seller_tax_id=tid, seller_name="新销方丁有限公司")
        self.inv.refresh_item(iid)
        month = date.today().strftime("%Y-%m")
        rows = self.ok(self.get("/api/inv/sellers?month=%s" % month, "viewer"))["rows"]
        row = [x for x in rows if x["taxId"] == tid][0]
        self.assertEqual((row["name"], row["items"], row["total"], row["checkResult"]), ("新销方丁有限公司", 1, 66.0, "未查"))
        self.assertIn(tid, [x["taxId"] for x in self.ok(self.get("/api/inv/sellers?status=unchecked", "viewer"))["rows"]])
        self.assertEqual(self.get("/api/inv/sellers?month=2026-9", "viewer").status_code, 400)
        body = {"taxId": tid, "date": date.today().isoformat(), "channel": "信用中国", "result": "命中", "note": "测试"}
        self.assertEqual(self.post("/api/inv/sellers/check", "acct", dict(body, result="可疑")).status_code, 400)
        self.assertEqual(self.post("/api/inv/sellers/check", "acct", dict(body, channel="")).status_code, 400)
        j = self.ok(self.post("/api/inv/sellers/check", "acct", body))
        self.assertEqual((j["seller"]["checkResult"], j["seller"]["checkedBy"]), ("命中", "acct"))
        self.assertIn("不自动拒付", j["msg"])
        self.assertIn(tid, [x["taxId"] for x in self.ok(self.get("/api/inv/sellers?status=hit", "viewer"))["rows"]])
        self.assertNotIn(tid, [x["taxId"] for x in self.ok(self.get("/api/inv/sellers?status=unchecked", "viewer"))["rows"]])
        r = self.get("/api/inv/sellers/export?month=%s" % month, "viewer")
        self.assertEqual(r.status_code, 200, r.text)
        from openpyxl import load_workbook
        vals = list(load_workbook(io.BytesIO(r.content)).active.values)
        self.assertEqual(list(vals[0]), self.ie.SELLER_COLUMNS)
        self.assertIn(tid, [v[0] for v in vals[1:]])

    # ── 期初导入 ──
    @unittest.skipUnless(HAS_XL, "缺 openpyxl")
    def test_08_opening_preview_commit(self):
        k2, _ = self.make_item(num(8002), 20)
        self.S.opening_insert_many(self.e, [{"dup_key": "N:" + num(8003), "number": num(8003), "total": 30}], 0, "票总管", "t")
        H = ["发票号码", "开票日期", "价税合计", "销方名称", "报销单号"]
        rows = [[num(8001), "2026-05-01", 10, "期初销方", "BX-1"], [num(8001), "2026-05-01", 10, "期初销方", "BX-1"],
                [num(8002), "2026-05-02", 20, "期初销方", "BX-2"], [num(8003), "2026-05-03", 30, "期初销方", "BX-3"],
                [float(num(8004)), "2026-05-04", 40, "期初销方", "BX-4"]]
        data = xlsx(H, rows)
        f = [("票总管导出.xlsx", data, "application/octet-stream")]
        self.assertEqual(self.upload("/api/inv/opening/preview", "acct", f, "file").status_code, 403)
        pv = self.ok(self.upload("/api/inv/opening/preview", "keeper", f, "file"))
        self.assertEqual((pv["rows"], pv["dupWithin"], pv["alreadyIn"], pv["numberLost"], pv["willInsert"]), (5, 1, 2, 1, 1), pv)
        self.assertEqual(len(pv["sample"]), 5)
        self.assertTrue(pv["sample"][4]["numberLost"])
        self.assertTrue(pv["mappingLines"])
        self.assertEqual(self.post("/api/inv/opening/commit", "acct", {"token": pv["token"]}).status_code, 403)
        self.assertEqual(self.post("/api/inv/opening/commit", "keeper", {"token": "x" * 22}).status_code, 400)
        j = self.ok(self.post("/api/inv/opening/commit", "keeper", {"token": pv["token"], "source": "票总管"}))
        self.assertEqual((j["inserted"], j["skippedLive"]), (1, 1), j)
        self.assertEqual(self.post("/api/inv/opening/commit", "keeper", {"token": pv["token"]}).status_code, 400)
        st = self.ok(self.get("/api/inv/opening/stats", "viewer"))
        self.assertGreaterEqual(st["total"], 2)
        self.assertIn(j["batch"]["id"], [b["id"] for b in st["batches"]])
        # 期初里有的票再登记 → 当场标重复（期初）
        self.ok(self.post("/api/inv/folder/manual", "intern", {"title": "期初撞号", "company": self.OUR}))
        d = self.ok(self.post("/api/inv/desk/scan", "intern", {"code": qr(num(8001), 10)}))
        self.assertEqual(d["action"], "dup", d)
        self.assertEqual(d["item"]["flags"]["dup"]["kind"], "opening")
        # 台账里本来就有的票没进期初：它自己重算也不会被判成和期初重复
        self.assertNotIn("dup", self.inv.refresh_item(k2)["flags_json"])

    # ── 发票后补池 ──
    def _norm(self, **kw):
        n = {"instId": "PI-LATER-1", "businessId": "202609240900000009001", "template": "付款申请（公对公）",
             "title": "申请人丙提交的付款申请（公对公）", "applicant": "申请人丙", "applicantUid": "dt-app", "dept": "采购部",
             "company": self.OUR, "amount": 1000.0, "payeeName": "后补收款方有限公司", "payeeBank": "测试银行",
             "payeeAccount": "6222000000009001", "reason": "预付货款", "erpNo": "PO-9001", "approvalStatus": "COMPLETED",
             "approvalResult": "agree", "createTime": "", "relate": [], "form": [], "attachments": [], "photos": [],
             "hasAttachments": False}
        n.update(kw)
        return n

    def test_09_later_flow(self):
        inv, S, e = self.inv, self.S, self.e
        f = self.tip.FAKE
        code = "https://aflow.dingtalk.com/qr/latertest"
        sent = MagicMock(return_value={"sent": True, "msg": "已发送"})
        with patch.object(inv.idt, "resolve_link", MagicMock(return_value={"ok": True, "procInstId": "PI-LATER-1", "corpId": "", "msg": ""})), \
                patch.object(inv.idt, "get_instance", MagicMock(return_value={"ok": True, "inst": {"x": 1}, "msg": ""})), \
                patch.object(inv.idt, "normalize_instance", MagicMock(return_value=self._norm())), \
                patch.object(inv.idt, "send_text", MagicMock(side_effect=AssertionError("不许真发"))), \
                patch.object(inv, "notify_dt", sent):
            p = self.ok(self.post("/api/inv/later/resolve", "recv", {"code": code}))["prefill"]
            self.assertEqual((p["payee"]["name"], p["amount"], p["hasInvoice"], p["existingLaterId"]),
                             ("后补收款方有限公司", 1000.0, False, None))
            r = self.post("/api/inv/later/resolve", "recv", {"code": qr(num(9001), 1)})
            self.assertEqual(r.status_code, 400)
            self.assertIn("发票的二维码", r.json()["msg"])
            day = (date.today() + timedelta(days=5)).isoformat()
            body = {"instId": "PI-LATER-1", "invKind": "special", "taxRate": "13%", "expectDate": day, "expectAmount": 1000,
                    "receiver": "recv2", "note": "先付款后开票"}
            for bad, word in ((dict(body, receiver="intern"), "接收人"), (dict(body, receiver="ghost"), "接收人"),
                              (dict(body, expectDate="下周"), "预计到票日期"), (dict(body, invKind="x"), "发票种类"),
                              (dict(body, expectAmount="abc"), "金额"), (dict(body, instId=""), "付款单"),
                              (dict(body, taxRate=""), "税率"), (dict(body, taxRate="七个点"), "税率"), (dict(body, taxRate="12%"), "税率")):
                r = self.post("/api/inv/later/create", "recv", bad)
                self.assertEqual(r.status_code, 400, bad)
                self.assertIn(word, r.json()["msg"])
            self.assertEqual(sent.call_count, 0)
            j = self.ok(self.post("/api/inv/later/create", "recv", body))
            lid = j["later"]["id"]
            self.assertEqual((j["later"]["status"], j["later"]["receiver"], j["later"]["filedBy"], j["later"]["filedVia"]),
                             ("open", "recv2", "recv", "proxy"))
            uids, text = sent.call_args[0]
            self.assertEqual(uids, ["dt-recv2"])
            for w in ("后补收款方有限公司", "1000.00", day, "#/invlater?id=%d" % lid):
                self.assertIn(w, text)
            fo = S.folder_by_inst(e, "PI-LATER-1")
            self.assertEqual((fo["source"], fo["payee_name"]), ("later", "后补收款方有限公司"))
            r = self.post("/api/inv/later/create", "recv", body)
            self.assertEqual(r.status_code, 400)
            self.assertEqual(r.json()["existingLaterId"], lid)
            self.assertEqual(self.ok(self.post("/api/inv/later/resolve", "recv", {"code": code}))["prefill"]["existingLaterId"], lid)
            # 扫纸质票收到一部分
            rc = self.ok(self.post("/api/inv/later/%d/receive" % lid, "recv2", {"code": qr(num(9001), 300)}))
            self.assertEqual(rc["action"], "item", rc)
            self.assertEqual((rc["item"]["laterId"], rc["item"]["review"], rc["item"]["paper"]), (lid, "pending", True))
            self.assertEqual((rc["later"]["status"], rc["later"]["receivedAmount"], rc["later"]["remaining"]), ("partial", 300.0, 700.0))
            again = self.ok(self.post("/api/inv/later/%d/receive" % lid, "recv2", {"code": qr(num(9001), 300)}))
            self.assertEqual(again["action"], "confirm")
            self.assertEqual(again["later"]["receivedAmount"], 300.0)
            self.assertEqual(self.post("/api/inv/later/%d/receive" % lid, "recv2", {"code": code}).status_code, 400)
            # 电子票上传：销方≠收款方 → 标红
            x = einv_xml(num(9002), f["seller"], f["seller_tid"], f["buyer"], f["buyer_tid"], 283.02, 16.98, 300)
            up = self.ok(self.upload("/api/inv/later/%d/receive-upload" % lid, "recv2", [("电子票.xml", x, "application/xml")]))
            it2 = up["results"][0]["item"]
            self.assertEqual((up["results"][0]["action"], it2["laterId"]), ("item", lid))
            self.assertEqual(it2["flags"]["sellerMismatch"], {"seller": f["seller"], "payee": "后补收款方有限公司"})
            self.assertTrue(up["later"]["sellerMismatch"])
            self.assertEqual((up["later"]["status"], up["later"]["receivedAmount"]), ("partial", 600.0))
            # 实在来不及扫：先点"收到"标黄 → 收齐 → 通知申请人已签收
            sent.reset_mock()
            self.assertEqual(self.post("/api/inv/later/%d/mark" % lid, "recv2", {"amount": 0}).status_code, 400)
            mk = self.ok(self.post("/api/inv/later/%d/mark" % lid, "recv2", {"amount": 400, "note": "纸票已收，号码稍后补"}))
            self.assertEqual((mk["later"]["status"], mk["later"]["unregisteredAmount"], mk["later"]["remaining"]), ("done", 400.0, 0.0))
            self.assertEqual(sent.call_count, 1)
            uids, text = sent.call_args[0]
            self.assertEqual(uids, ["dt-app"])
            self.assertIn("已签收", text)
            self.assertEqual(self.post("/api/inv/later/%d/mark" % lid, "recv2", {"amount": 1}).status_code, 400)
            self.assertEqual(self.post("/api/inv/later/%d/remind" % lid, "recv2").status_code, 400)
            # 列表：我接收的 / 我代填的 / 全部 / 状态
            ids = lambda u, qs: [x["id"] for x in self.ok(self.get("/api/inv/later?" + qs, u))["rows"]]
            self.assertIn(lid, ids("recv2", "scope=mine"))
            self.assertIn(lid, ids("recv", "scope=mine"))
            self.assertNotIn(lid, ids("boss", "scope=mine"))
            self.assertIn(lid, ids("boss", "scope=all&status=done"))
            self.assertNotIn(lid, ids("boss", "scope=all&status=open"))
            self.assertEqual(self.get("/api/inv/later?status=bad", "recv").status_code, 400)
            # 资料：只留存不识别，不算到票
            docs = self.ok(self.upload("/api/inv/later/%d/docs" % lid, "recv", [("合同.txt", "合同内容 金额 1000 元".encode("utf-8"), "text/plain"),
                                                                                 ("合同副本.txt", "合同内容 金额 1000 元".encode("utf-8"), "text/plain")]))
            self.assertEqual([x["action"] for x in docs["results"]], ["doc", "same"])
            self.assertEqual(len(docs["docs"]), 1)
            dv = self.ok(self.get("/api/inv/later/%d" % lid, "recv"))
            self.assertEqual(sorted(i["number"] for i in dv["items"]), [num(9001), num(9002)])
            self.assertEqual(dv["docs"][0]["name"], "合同.txt")
            self.assertTrue(dv["docs"][0]["url"].startswith("/api/inv/file/"))
            self.assertEqual(self.get(dv["docs"][0]["url"], "keeper").status_code, 200)
            self.assertEqual(dv["later"]["receivedAmount"], 600.0)
            acts = [x["action"] for x in dv["logs"]]
            for a in ("登记发票后补", "登记票据", "标记已收到（未登记号码）", "后补收齐通知", "上传后补资料"):
                self.assertIn(a, acts)
            # 导出欠票清单
            r = self.get("/api/inv/later/export?status=all", "recv")
            self.assertEqual(r.status_code, 200, r.text)
            from openpyxl import load_workbook
            self.assertEqual(load_workbook(io.BytesIO(r.content)).sheetnames, ["按供应商汇总", "明细"])
            # 作废一张到了的票 → 已到金额回退、重新变成部分到票
            self.ok(self.post("/api/inv/item/%d/void" % it2["id"], "keeper", {"note": "销方开错，已红冲"}))
            l = self.ok(self.get("/api/inv/later/%d" % lid, "recv"))["later"]
            self.assertEqual((l["status"], l["receivedAmount"], l["unregisteredAmount"]), ("partial", 300.0, 400.0))
            # 改接收人 → 通知新接收人；关闭要写原因
            sent.reset_mock()
            self.assertEqual(self.post("/api/inv/later/%d/update" % lid, "recv", {"receiver": "ghost"}).status_code, 400)
            up = self.ok(self.post("/api/inv/later/%d/update" % lid, "recv", {"receiver": "recv", "expectDate": "2026/12/1"}))
            self.assertEqual((up["later"]["receiver"], up["later"]["expectDate"]), ("recv", "2026-12-01"))
            self.assertEqual(sent.call_args[0][0], ["dt-recv"])
            self.assertEqual(self.post("/api/inv/later/%d/close" % lid, "recv", {"note": ""}).status_code, 400)
            cl = self.ok(self.post("/api/inv/later/%d/close" % lid, "recv", {"note": "供应商不开票，已退款"}))
            self.assertEqual(cl["later"]["status"], "closed")
            self.assertEqual(self.post("/api/inv/later/%d/receive" % lid, "recv2", {"code": qr(num(9003), 5)}).status_code, 400)
            # 设置里没勾「允许后补」的模板不走后补（V2.621 起费用报销默认允许，这里临时关掉它）
            st0 = inv.get_settings()
            st1 = dict(st0, templates=[dict(t, allowLater=(t["name"] != "费用报销")) for t in st0["templates"]])
            with patch.object(inv.idt, "normalize_instance", MagicMock(return_value=self._norm(instId="PI-LATER-2", template="费用报销"))),                     patch.object(inv, "get_settings", MagicMock(return_value=st1)):
                r = self.post("/api/inv/later/resolve", "recv", {"code": code})
            self.assertEqual(r.status_code, 400)
            self.assertIn("不走发票后补", r.json()["msg"])

    def test_10_remind_manual(self):
        S, e = self.S, self.e
        lid = self.later_row(expect_date=(date.today() + timedelta(days=2)).isoformat())
        sent = MagicMock(return_value={"sent": True, "msg": "已发送"})
        with patch.object(self.inv, "notify_dt", sent):
            j = self.ok(self.post("/api/inv/later/%d/remind" % lid, "recv"))
        self.assertTrue(j["sent"])
        uids, text = sent.call_args[0]
        self.assertEqual(uids, ["dt-app2", "dt-recv2"])
        self.assertIn("催票提醒", text)
        self.assertIn("还有 2 天", text)
        l = S.later_get(e, lid)
        self.assertEqual(l["remind_count"], 1)
        self.assertTrue(l["last_remind_at"])
        # 不 mock：INV_DRY_SEND 闸住，钉钉一条都不发，但算"催过"
        with patch.object(self.inv.idt, "send_text", MagicMock(side_effect=AssertionError("不许真发"))) as st:
            j = self.ok(self.post("/api/inv/later/%d/remind" % lid, "recv"))
        st.assert_not_called()
        self.assertEqual((j["sent"], j["msg"]), (False, "dry-run"))
        self.assertEqual(S.later_get(e, lid)["remind_count"], 2)
        # 两边都没钉钉身份：没发出去，也不记"催过"
        lid2 = self.later_row(applicant_uid="", receiver="nobody", receiver_uid="", expect_date="2026-01-01")
        j = self.ok(self.post("/api/inv/later/%d/remind" % lid2, "recv"))
        self.assertFalse(j["sent"])
        self.assertIn("没有钉钉身份", j["msg"])
        self.assertEqual(S.later_get(e, lid2)["remind_count"], 0)
        self.assertEqual(self.post("/api/inv/later/%d/remind" % lid, "viewer").status_code, 403)
        self.assertEqual(self.post("/api/inv/later/999999/remind", "recv").status_code, 404)

    def test_11_remind_tick_due_logic(self):
        books, S, e = self.books, self.S, self.e
        today = date.today()
        d = lambda n: (today + timedelta(days=n)).isoformat()
        ts = lambda n: (today + timedelta(days=n)).isoformat() + " 10:00:00"
        soon = self.later_row(expect_date=d(2), payee_name="快到期供应商")
        far = self.later_row(expect_date=d(20))
        recent = self.later_row(expect_date=d(-10), last_remind_at=ts(-3), remind_count=1)
        old = self.later_row(expect_date=d(-10), last_remind_at=ts(-8), remind_count=1)
        done = self.later_row(expect_date=d(-10), status="done", unregistered_amount=500)   # 真收齐了（不是旧状态）
        closed = self.later_row(expect_date=d(-10), status="closed")
        self.assertFalse(books._REMIND["started"])                       # SQLite：催票线程不起
        self.assertFalse(books.start_reminder())
        books._REMIND["lastDay"] = ""
        sent = MagicMock(return_value={"sent": True, "msg": "已发送"})
        with patch.object(self.inv, "notify_dt", sent):
            early = books.remind_tick(now=datetime.combine(today, dtime(8, 0)))
            self.assertEqual(early, {"ran": False, "reason": "early"})
            res = books.remind_tick(now=datetime.combine(today, dtime(11, 0)))
            self.assertTrue(res["ran"], res)
            for lid in (soon, old):
                self.assertIn(lid, res["ids"])
            for lid in (far, recent, done, closed):
                self.assertNotIn(lid, res["ids"])
            self.assertEqual(S.later_get(e, soon)["remind_count"], 1)
            self.assertEqual(S.later_get(e, old)["remind_count"], 2)
            self.assertEqual(S.later_get(e, far)["remind_count"], 0)
            texts = [c[0][1] for c in sent.call_args_list]
            self.assertTrue(any("快到期供应商" in t and "还有 2 天" in t for t in texts))
            self.assertTrue(any("已超期 10 天" in t for t in texts))
            self.assertTrue(any(x["action"] == "自动催票" for x in S.logs_of(e, later_id=soon)))
            n = sent.call_count
            self.assertEqual(books.remind_tick(now=datetime.combine(today, dtime(15, 0))), {"ran": False, "reason": "doneToday"})
            # 第二天：快到期的窗口里已经催过一次，不再催；超期的离上次不到 7 天，也不催
            books._REMIND["lastDay"] = ""
            res2 = books.remind_tick(now=datetime.combine(today, dtime(16, 0)), force=True)
            self.assertNotIn(soon, res2["ids"])
            self.assertNotIn(old, res2["ids"])
            self.assertEqual(sent.call_count, n + len(res2["ids"]))
            # 关掉催票：一条都不发
            st = self.inv.get_settings()
            self.inv.save_settings({"remind": dict(st["remind"], enabled=False)}, "test")
            try:
                books._REMIND["lastDay"] = ""
                self.assertEqual(books.remind_tick(now=datetime.combine(today, dtime(11, 0))), {"ran": False, "reason": "disabled"})
            finally:
                self.inv.save_settings({"remind": st["remind"]}, "test")

    # ── 审查修复回归 ──
    def xl_rows(self, r):
        self.assertEqual(r.status_code, 200, r.text)
        from openpyxl import load_workbook
        return list(load_workbook(io.BytesIO(r.content)).active.values)

    @unittest.skipUnless(HAS_XL, "缺 openpyxl")
    def test_12_ledger_withvoid_and_export_totals(self):
        """F5/C3：「含作废」＝已审核＋已作废（草稿/待审不进）；页面合计、导出合计都不含作废；导出边读边写（不走分页查询）。"""
        a, _ = self.make_item(num(12001), 100)
        d, _ = self.make_item(num(12002), 400, approve=False, submit=False)
        v, _ = self.make_item(num(12003), 50)
        self.ok(self.post("/api/inv/item/%d/void" % v, "keeper", {"note": "开错抬头，已红冲"}))
        q = "2644990000000001200"
        j = self.ok(self.get("/api/inv/ledger?q=%s&review=withvoid" % q, "viewer"))
        self.assertEqual(sorted(x["id"] for x in j["rows"]), sorted([a, v]))            # 草稿不进台账
        self.assertEqual({x["id"]: x["review"] for x in j["rows"]}[v], "void")
        self.assertEqual(j["sum"]["total"], 100.0)                                       # 合计不含作废
        self.assertNotIn(d, [x["id"] for x in j["rows"]])
        self.assertEqual(self.ok(self.get("/api/inv/ledger?q=%s&review=void" % q, "viewer"))["sum"]["total"], 50.0)
        # 导出：同一口径；不再走 ledger_query 分页（边读边写）
        with patch.object(self.books.S, "ledger_query", MagicMock(side_effect=AssertionError("导出不该分页全量取"))):
            vals = self.xl_rows(self.get("/api/inv/ledger/export?q=%s&review=withvoid" % q, "viewer"))
        cols = self.ie.LEDGER_COLUMNS + self.books.LEDGER_EXTRA_COLUMNS
        self.assertEqual(len(vals), 1 + 2 + 1)
        self.assertEqual(sorted(r[cols.index("审核状态")] for r in vals[1:-1]), ["已作废", "已审核"])
        self.assertEqual(vals[-1][cols.index("价税合计")], 100)
        self.assertIn("不含作废 1 行", vals[-1][0])
        vv = self.xl_rows(self.get("/api/inv/ledger/export?q=%s&review=void" % q, "viewer"))
        self.assertEqual(vv[-1][cols.index("价税合计")], 50)                              # 筛的就是作废：合计算作废

    @unittest.skipUnless(HAS_XL, "缺 openpyxl")
    def test_13_split_invoice_counts_allocation(self):
        """F6：一张 1000 元的票拆给两张单（600/400）：台账合计、导出合计、新销方金额都只算 1000，不按票面算两遍。"""
        S, e = self.S, self.e
        n = num(13001)
        tid = self.tip._uscc("91330000MA13XYTW1")
        ids = []
        for k, alloc in ((1, 600), (2, 400)):
            fid = S.folder_create_manual(e, "拆分票夹%d" % k, alloc, self.OUR, "intern")
            ids.append(S.item_insert(e, folder_id=fid, kind="invoice", origin="upload", inv_type="special", number=n,
                                     issue_date="2026-09-03", total=1000, amount=943.40, tax=56.60, split=1, alloc=alloc,
                                     review="approved", seller_tax_id=tid, seller_name="拆分销方有限公司",
                                     dup_key="N:" + n, created_by="intern"))
        S.seller_seen(e, tid, "拆分销方有限公司", date.today().isoformat(), ids[0])
        j = self.ok(self.get("/api/inv/ledger?q=%s" % n, "viewer"))
        self.assertEqual((j["total"], j["sum"]["total"]), (2, 1000.0))
        self.assertAlmostEqual(j["sum"]["tax"], 56.60, places=2)
        vals = self.xl_rows(self.get("/api/inv/ledger/export?q=%s" % n, "viewer"))
        cols = self.ie.LEDGER_COLUMNS + self.books.LEDGER_EXTRA_COLUMNS
        self.assertEqual(sorted(r[cols.index("本单分摊额")] for r in vals[1:-1]), [400, 600])
        self.assertEqual(vals[-1][cols.index("价税合计")], 1000)
        self.assertAlmostEqual(vals[-1][cols.index("税额")], 56.60, places=2)
        self.assertIn("拆分票按本单分摊额计", vals[-1][0])
        rows = self.ok(self.get("/api/inv/sellers?month=%s" % date.today().strftime("%Y-%m"), "viewer"))["rows"]
        self.assertEqual([x["total"] for x in rows if x["taxId"] == tid], [1000.0])

    def test_14_void_settles_folder_and_recalcs_later(self):
        """F2/F3：作废已提交票夹里最后一张待审票 → 票夹不再卡在审核队列；作废挂着后补单的票 → 后补单退回未收齐。"""
        S, e = self.S, self.e
        _, fo1 = self.make_item(num(14001), 70, approve=False)                      # 已提交、1 张待审
        pend = [i for i in S.folder_items(e, fo1) if i.get("review") == "pending"]
        j = self.ok(self.post("/api/inv/item/%d/void" % pend[0]["id"], "keeper", {"note": "扫错了"}))
        self.assertEqual(S.folder_get(e, fo1)["status"], "collecting")
        self.assertIn("收票中", j["msg"])
        fo2 = S.folder_create_manual(e, "已审一张待审一张", 200, self.OUR, "intern")
        S.folder_update(e, fo2, status="submitted", submitted_by="intern")
        S.item_insert(e, folder_id=fo2, kind="invoice", origin="upload", number=num(14002), total=100,
                      review="approved", created_by="intern")
        p2 = S.item_insert(e, folder_id=fo2, kind="invoice", origin="upload", number=num(14003), total=100,
                           review="pending", created_by="intern")
        j = self.ok(self.post("/api/inv/item/%d/void" % p2, "keeper", {"note": "重复报销"}))
        self.assertEqual(S.folder_get(e, fo2)["status"], "approved")
        self.assertIn("已自动记为已审核", j["msg"])
        # 后补单：收齐后作废那张票 → 已到金额退回、状态回到待收
        fo3 = S.folder_create_manual(e, "后补作废", 300, self.OUR, "recv")
        lid = self.later_row(folder_id=fo3, expect_amount=300, pay_amount=300, expect_date="2026-12-31")
        r = self.inv.ingest_qr(fo3, qr(num(14004), 300), "recv", "scanner", later_id=lid)
        self.assertEqual(r["action"], "item", r)
        self.books._later_recalc(lid)
        self.assertEqual(S.later_get(e, lid)["status"], "done")
        self.ok(self.post("/api/inv/item/%d/void" % r["itemId"], "keeper", {"note": "销方开错，已红冲"}))
        l = S.later_get(e, lid)
        self.assertEqual((l["status"], l["received_amount"]), ("open", 0.0))

    @unittest.skipUnless(HAS_XL, "缺 openpyxl")
    def test_15_taxlist_only_judges_its_own_company(self):
        """F4：两家公司各导各的「取得发票」清单：别家公司的票不判黄、绿的不降黄；号码被截断的行能对上日期金额的不判黄；
        清单没有购方列又配了两家公司 → 分不清是谁的，不判黄并提示。"""
        S, e, TID, OUR = self.S, self.e, self.OUR_TID, self.OUR
        TID2, OUR2 = self.tip._uscc("91440300MA15XYTW2"), "测试第二主体有限公司"
        st0 = self.inv.get_settings()
        self.inv.save_settings({"company": [{"name": OUR, "taxId": TID}, {"name": OUR2, "taxId": TID2}]}, "test")
        try:
            c1 = {"buyer_tax_id": TID, "buyer_name": OUR}
            c2 = {"buyer_tax_id": TID2, "buyer_name": OUR2}
            hit, _ = self.make_item(num(15001), 10, "31", "20250310", c1)
            miss, _ = self.make_item(num(15002), 20, "31", "20250315", c1)
            c2g, _ = self.make_item(num(15003), 30, "31", "20250316", dict(c2, verify="green"))
            c2n, _ = self.make_item(num(15004), 40, "31", "20250317", c2)
            c1g, _ = self.make_item(num(15005), 50, "31", "20250318", dict(c1, verify="green"))
            lost, _ = self.make_item(num(15006), 77, "31", "20250319", c1)
            H = ["数电票号码", "开票日期", "购方识别号", "购方名称", "价税合计", "发票状态", "发票票种"]
            rows = [[num(15001), "2025-03-10", TID, OUR, 10, "正常", "数电专票"],
                    [num(15090), "2025-03-01", TID, OUR, 1, "正常", "数电普票"],
                    [num(15091), "2025-03-31", TID, OUR, 1, "正常", "数电普票"],
                    [float(num(15092)), "2025-03-19", TID, OUR, 77, "正常", "数电专票"]]   # 号码被截断
            rep = self.ok(self.upload("/api/inv/taxlist/import", "acct",
                                      [("取得发票-公司一.xlsx", xlsx(H, rows), "application/octet-stream")], "file"))["report"]
            self.assertEqual(self.item(hit)["verify"], "green")
            self.assertEqual(self.item(miss)["verify"], "yellow")
            self.assertEqual(self.item(c2g)["verify"], "green")            # 别家公司的绿票不降黄
            self.assertEqual(self.item(c2n)["verify"] or "", "")          # 别家公司的票这份清单管不到
            self.assertEqual(self.item(c1g)["verify"], "green")            # 以前绿的不降黄
            self.assertEqual(self.item(lost)["verify"] or "", "")         # 可能就是被截断号码的那行
            self.assertEqual([x["id"] for x in rep["notInList"]], [miss])
            self.assertTrue(any("保持绿色" in w for w in rep["warnings"]), rep["warnings"])
            self.assertTrue(any("截断" in w and "没标黄" in w for w in rep["warnings"]), rep["warnings"])
            # 没有购方列、两家公司：分不清是谁的清单 → 不判黄
            nb = xlsx(["数电票号码", "开票日期", "价税合计", "发票状态"],
                      [[num(15093), "2025-03-01", 1, "正常"], [num(15094), "2025-03-31", 1, "正常"]])
            rep2 = self.ok(self.upload("/api/inv/taxlist/import", "acct",
                                       [("取得发票-无购方.xlsx", nb, "application/octet-stream")], "file"))["report"]
            self.assertEqual(rep2["counts"]["yellow"], 0, rep2)
            self.assertEqual(self.item(c2n)["verify"] or "", "")
            self.assertTrue(any("分不清" in w for w in rep2["warnings"]), rep2["warnings"])
        finally:
            self.inv.save_settings({"company": st0["company"]}, "test")

    @unittest.skipUnless(HAS_XL, "缺 openpyxl")
    def test_16_taxlist_partial_red_and_checked_state(self):
        """F7/F11：部分红冲不算作废（绿＋说明，抵扣标注仍为「是」并提示按有效抵扣税额）；全额红冲才红；
        清单显示已勾选 → 记"已勾选"，作废时提醒做进项税额转出。"""
        TID, OUR = self.OUR_TID, self.OUR
        c1 = {"buyer_tax_id": TID, "buyer_name": OUR}
        p, _ = self.make_item(num(16001), 106, "31", "20250410", c1)
        fr, _ = self.make_item(num(16002), 106, "31", "20250411", c1)
        t, _ = self.make_item(num(16003), 106, "31", "20250412", c1)
        nt, _ = self.make_item(num(16004), 106, "31", "20250413", c1)
        H = ["数电票号码", "开票日期", "购方识别号", "购方名称", "价税合计", "发票状态", "发票票种", "勾选状态"]
        rows = [[num(16001), "2025-04-10", TID, OUR, 106, "已红冲-部分", "数电专票", "已勾选"],
                [num(16002), "2025-04-11", TID, OUR, 106, "已红冲-全额", "数电专票", "未勾选"],
                [num(16003), "2025-04-12", TID, OUR, 106, "正常", "数电专票", "已勾选"],
                [num(16004), "2025-04-13", TID, OUR, 106, "正常", "数电专票", "未勾选"]]
        rep = self.ok(self.upload("/api/inv/taxlist/import", "acct",
                                  [("取得发票-红冲.xlsx", xlsx(H, rows), "application/octet-stream")], "file"))["report"]
        ip_, ifr, it_, int_ = self.item(p), self.item(fr), self.item(t), self.item(nt)
        self.assertEqual((ip_["verify"], ifr["verify"], it_["verify"], int_["verify"]), ("green", "red", "green", "green"))
        self.assertIn("部分红冲", ip_["verify_note"])
        self.assertEqual((ip_["deduct_status"], it_["deduct_status"], int_["deduct_status"] or ""), ("checked", "checked", ""))
        self.assertEqual(rep["listStats"]["partialRed"], 1)
        self.assertEqual([x["id"] for x in self.ok(self.get("/api/inv/ledger?deduct=checked&q=2644990000000001600",
                                                             "viewer"))["rows"]], [t, p])
        # 抵扣标注：部分红冲的票照样标「是」，并提示只按税局有效抵扣税额勾
        D = ["数电票号码", "开票日期", "销方纳税人识别号", "销方名称", "金额", "税额", "有效抵扣税额", "是否勾选"]
        dj = self.ok(self.upload("/api/inv/deduct/prepare", "acct",
                                 [("抵扣.xlsx", xlsx(D, [[num(16001), "2025-04-10", "91A", "甲", 100, 6, 3, None],
                                                         [num(16002), "2025-04-11", "91B", "乙", 100, 6, 6, None]],
                                                     title="抵扣类勾选"), "application/octet-stream")], "file"))
        dec = {x["number"]: (x["decision"], x["reason"]) for x in dj["preview"]}
        self.assertEqual(dec[num(16001)][0], "是", dec)
        self.assertIn("有效抵扣税额 ¥3.00", dec[num(16001)][1])
        self.assertEqual(dec[num(16002)][0], "否")
        # 已勾选的票作废 → 提醒进项税额转出
        j = self.ok(self.post("/api/inv/item/%d/void" % t, "keeper", {"note": "销方要求退票"}))
        self.assertIn("进项税额转出", j["msg"])

    def test_17_taxpack_never_rewrites_approved_invoice(self):
        """SEC-1（税局文件包）：已审核的票只补空字段；和上传的"税局文件"对不上 → 票面、主文件都不改，标出来留痕。
        待审的票：二维码来的值不被文件改掉，放进待核并标 fileMismatch。"""
        f = self.tip.FAKE
        t, _ = self.make_item(num(17001), 1272)
        self.S.item_update(self.e, t, seller_name="照片识别销方", field_src_json=dict(
            self.item(t)["field_src_json"], sellerName={"src": "ocr", "page": 0, "box": None}))
        pt, _ = self.make_item(num(17002), 106, approve=False)
        fake = einv_xml(num(17001), f["seller"], f["seller_tid"], f["buyer"], f["buyer_tid"], 9000, 999, 9999)
        fake2 = einv_xml(num(17002), f["seller"], f["seller_tid"], f["buyer"], f["buyer_tid"], 400, 100, 500)
        j = self.ok(self.upload("/api/inv/taxpack/import", "acct", [("a.xml", fake, "application/xml"),
                                                                     ("b.xml", fake2, "application/xml")]))
        it = self.item(t)
        self.assertEqual((it["total"], it["seller_name"], it["review"]), (1272.0, "照片识别销方", "approved"))
        self.assertIsNone(it["seller_tax_id"])                                    # 对不上的文件：空字段也不拿它补
        self.assertIsNone(it["file_id"])                                          # 主文件不换
        self.assertIn("total", it["flags_json"]["postApprovalMismatch"])
        self.assertIn("sellerName", it["flags_json"]["postApprovalMismatch"])
        att = [x for x in self.S.files_of_folder(self.e, it["folder_id"]) if x.get("item_id") == t]
        self.assertEqual([x["role"] for x in att], ["attachment"])
        self.assertEqual(sorted(m["id"] for m in j["mismatch"]), sorted([t, pt]))
        self.assertIn("对不上", j["msg"])
        p2 = self.item(pt)
        self.assertEqual(p2["total"], 106.0)
        self.assertIn("total", p2["pending_json"])
        self.assertIn("total", p2["flags_json"]["fileMismatch"])
        self.assertTrue(any(x["action"] == "税局文件和票面对不上" for x in self.S.logs_of(self.e, item_id=t)))
        # 再导一次同一份：待核的二维码值仍然不会被改掉
        self.ok(self.upload("/api/inv/taxpack/import", "acct", [("b.xml", fake2, "application/xml")]))
        self.assertEqual(self.item(pt)["total"], 106.0)

    def test_18_taxpack_zips_one_at_a_time(self):
        """robustness F10/SEC-6：压缩包解一个处理一个（不先全摊在内存里），整次导入共用一份解开总量额度。"""
        f = self.tip.FAKE
        ip, order = self.books.ip, []
        real_unpack, real_xml = ip.unpack_zip, ip.extract_xml

        def fake_unpack(data, *a, **kw):
            order.append(("unpack", kw.get("max_total")))
            return real_unpack(data, *a, **kw)

        def fake_xml(data, *a, **kw):
            order.append(("xml", None))
            return real_xml(data, *a, **kw)
        zips = []
        for k in (1, 2):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr("包%d/%s.xml" % (k, num(18000 + k)),
                           einv_xml(num(18000 + k), f["seller"], f["seller_tid"], f["buyer"], f["buyer_tid"], 10, 0.6, 10.6))
            zips.append(("税局%d.zip" % k, buf.getvalue(), "application/zip"))
        with patch.object(ip, "unpack_zip", fake_unpack), patch.object(ip, "extract_xml", fake_xml):
            j = self.ok(self.upload("/api/inv/taxpack/import", "acct", zips))
        kinds = [k for k, _ in order]
        last_unpack = max(i for i, k in enumerate(kinds) if k == "unpack")
        self.assertLess(kinds.index("xml"), last_unpack, order)                   # 第一个包处理完才解第二个
        self.assertEqual(order[0][1], self.inv.MAX_TOTAL)
        self.assertLess(order[last_unpack][1], self.inv.MAX_TOTAL)                # 额度是整次共用的
        self.assertEqual(len(j["unmatched"]), 2)

    def test_19_later_status_healed_before_filtering(self):
        """F3：库里状态旧了（写着已收齐、其实一张票都没有）→ 按状态筛、导出欠票清单、每日催票前都先纠正，不漏催。"""
        S, e = self.S, self.e
        y = (date.today() - timedelta(days=1)).isoformat()
        stale = lambda tag: self.later_row(status="done", received_amount=300, expect_amount=300, pay_amount=300,
                                           expect_date=y, payee_name="自愈供应商" + tag)
        l1 = stale("甲")
        ids = [x["id"] for x in self.ok(self.get("/api/inv/later?scope=all&status=open", "recv"))["rows"]]
        self.assertIn(l1, ids)
        self.assertEqual((S.later_get(e, l1)["status"], S.later_get(e, l1)["received_amount"]), ("open", 0.0))
        l2 = stale("乙")
        vals = None
        r = self.get("/api/inv/later/export?status=open", "recv")
        self.assertEqual(r.status_code, 200, r.text)
        from openpyxl import load_workbook
        vals = list(load_workbook(io.BytesIO(r.content))["明细"].values)
        self.assertIn(l2, [v[0] for v in vals[1:]])
        l3 = stale("丙")
        with patch.object(self.inv, "notify_dt", MagicMock(return_value={"sent": True, "msg": "已发送"})):
            res = self.books.remind_tick(now=datetime.combine(date.today(), dtime(11, 0)), force=True)
        self.assertIn(l3, res["ids"])

    def test_20_remind_retries_after_query_error(self):
        """robustness F11：取待催清单出错 → 当天不算催过，下一轮（10 分钟后）重试。"""
        books = self.books
        books._REMIND["lastDay"] = ""
        calls = {"n": 0}
        real = self.S.laters_due_for_remind

        def flaky(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("数据库断了一下")
            return real(*a, **kw)
        today = date.today()
        with patch.object(books.S, "laters_due_for_remind", flaky), \
                patch.object(self.inv, "notify_dt", MagicMock(return_value={"sent": True, "msg": "已发送"})):
            with self.assertRaises(RuntimeError):
                books.remind_tick(now=datetime.combine(today, dtime(11, 0)))
            res = books.remind_tick(now=datetime.combine(today, dtime(11, 10)))
        self.assertTrue(res["ran"], res)
        self.assertEqual(books.remind_tick(now=datetime.combine(today, dtime(11, 20))), {"ran": False, "reason": "doneToday"})

    def test_21_later_notices_off_loop_and_reported(self):
        """C5 + P1/F3：登记后补回 notified/notifyMsg（页面照实说）；指派、收齐签收、换接收人三条钉钉都在线程池里发。"""
        import asyncio
        S, e = self.S, self.e
        for inst in ("PI-LATER-21A", "PI-LATER-21B"):
            S.folder_upsert_from_approval(e, self._norm(instId=inst, businessId="2026092421000" + inst[-1]), "recv", "later")
        seen = []

        def fake_notify(uids, text):
            try:
                asyncio.get_running_loop()
                seen.append("loop")
            except RuntimeError:
                seen.append("thread")
            return {"sent": True, "msg": "已发送"}
        day = (date.today() + timedelta(days=5)).isoformat()
        body = {"instId": "PI-LATER-21A", "invKind": "special", "taxRate": "13%", "expectDate": day, "expectAmount": 300, "receiver": "recv2"}
        with patch.object(self.inv, "notify_dt", fake_notify):
            j = self.ok(self.post("/api/inv/later/create", "recv", body))
            self.assertIs(j["notified"], True)
            self.assertIn("接收人乙", j["notifyMsg"])
            lid = j["later"]["id"]
            rc = self.ok(self.post("/api/inv/later/%d/receive" % lid, "recv2", {"code": qr(num(21001), 300)}))
            self.assertEqual(rc["later"]["status"], "done")                       # 收齐 → 发"已签收"
            up = self.ok(self.post("/api/inv/later/%d/update" % lid, "recv", {"receiver": "recv"}))
            self.assertIs(up["notified"], True)
        self.assertEqual(seen, ["thread", "thread", "thread"])
        # 不 mock：INV_DRY_SEND 闸住没真发 → notified=False，页面不能说"已通知"
        j2 = self.ok(self.post("/api/inv/later/create", "recv", dict(body, instId="PI-LATER-21B")))
        self.assertIs(j2["notified"], False)
        self.assertIn("dry-run", j2["notifyMsg"])

    def test_22_disk_full_and_preview_pages(self):
        """SEC-6/F11(C8)：磁盘快满（StoreRefused）→ 税局文件包停下后面的文件、后补资料逐个回错误，不 500；
        税局 PDF 预览要画到有票的最后一页（第 6 张以后的票不能显示成第 1 页）。"""
        inv, books = self.inv, self.books
        f = self.tip.FAKE
        t1, _ = self.make_item(num(22001), 10.6)
        t2, _ = self.make_item(num(22002), 10.6)
        x1 = einv_xml(num(22001), f["seller"], f["seller_tid"], f["buyer"], f["buyer_tid"], 10, 0.6, 10.6)
        x2 = einv_xml(num(22002), f["seller"], f["seller_tid"], f["buyer"], f["buyer_tid"], 10, 0.6, 10.6)
        full = MagicMock(side_effect=inv.StoreRefused("服务器磁盘剩余空间不足，先不收文件"))
        with patch.object(inv, "store_file", full):
            j = self.ok(self.upload("/api/inv/taxpack/import", "acct", [("1.xml", x1, "application/xml"),
                                                                         ("2.xml", x2, "application/xml")]))
            self.assertEqual(full.call_count, 1)                                   # 第一份撞上就停
            self.assertTrue(any("磁盘" in x["msg"] for x in j["errors"]), j["errors"])
            self.assertTrue(any(x["name"] == "2.xml" and "没处理" in x["msg"] for x in j["errors"]), j["errors"])
            lid = self.later_row(folder_id=self.S.folder_create_manual(self.e, "资料盘满", 1, self.OUR, "recv"))
            r = self.upload("/api/inv/later/%d/docs" % lid, "recv", [("合同.txt", "合同".encode("utf-8"), "text/plain")])
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual([x["action"] for x in r.json()["results"]], ["error"])
        self.assertIsNone(self.item(t1)["file_id"])
        seen = {}

        def fake_render(data, max_pages=5, long_side=1600):
            seen["max_pages"] = max_pages
            return []
        with patch.object(books.ip, "render_pdf", fake_render), patch.object(inv, "store_file", MagicMock(return_value={"id": 1})):
            books._pack_file(self.e, {}, None, "合并.pdf", b"%PDF-1.4 x", "pdf", [{"page": 0}, {"page": 7}], "acct")
        self.assertEqual(seen["max_pages"], 8)

    def test_23_applicant_self_registers_later(self):
        """申请人自助登记发票后补（V2.621）：不走工作台登录，钉钉验证码认人（重名先选部门、60 秒不重发、错码计次、一码一用）；
        只列/只能登记自己发起的单；登记后进同一个后补池（filedVia=self），财务审核时按审批单关联；只能给自己的后补单传资料。人名单号都是编的。"""
        inv, books, sf, S, e = self.inv, self.books, self.sf, self.S, self.e
        self.assertEqual(self.c.get("/api/inv/s/payments").status_code, 401)
        self.assertIsNone(self.c.get("/api/inv/s/hello").json()["me"])
        roster = {"ok": True, "msg": "", "rows": [
            {"userid": "dt-app", "name": "申请人丙", "title": "采购", "dept": "公司-采购部"},
            {"userid": "dt-dup1", "name": "重名人", "title": "", "dept": "公司-销售部"},
            {"userid": "dt-dup2", "name": "重名人", "title": "", "dept": "公司-生产部"}]}
        sf._IP_HITS.clear()
        with patch.object(inv.idt, "roster", MagicMock(return_value=roster)), \
                patch.object(inv.idt, "send_text", MagicMock(side_effect=AssertionError("不许真发"))):
            r = self.c.post("/api/inv/s/login/send", json={"name": "查无此人"})
            self.assertEqual(r.status_code, 404)
            r = self.ok(self.c.post("/api/inv/s/login/send", json={"name": "重名人"}))
            self.assertEqual((r["need"], [c["dept"] for c in r["choices"]]), ("pick", ["公司-销售部", "公司-生产部"]))
            r = self.ok(self.c.post("/api/inv/s/login/send", json={"name": "重名人", "pick": 1}))
            self.assertIn("生产部", r["to"])
            r = self.ok(self.c.post("/api/inv/s/login/send", json={"name": "申请人丙"}))
            ticket, code = r["ticket"], r["devCode"]
            self.assertEqual(len(code), 6)
            self.assertEqual(self.c.post("/api/inv/s/login/send", json={"name": "申请人丙"}).status_code, 429)   # 60 秒内不重发
        bad = "%06d" % ((int(code) + 1) % 1000000)
        r = self.c.post("/api/inv/s/login/verify", json={"ticket": ticket, "code": bad})
        self.assertEqual(r.status_code, 400)
        self.assertIn("不对", r.json()["msg"])
        j = self.ok(self.c.post("/api/inv/s/login/verify", json={"ticket": ticket, "code": code}))
        tok = j["token"]
        self.assertEqual((j["me"]["name"], j["me"]["dept"]), ("申请人丙", "公司-采购部"))
        self.assertEqual(self.c.post("/api/inv/s/login/verify", json={"ticket": ticket, "code": code}).status_code, 400)  # 一码一用
        row = S.self_by_hash(e, "session", sf._h(tok))
        self.assertEqual(row["dt_userid"], "dt-app")
        self.assertNotIn(tok, json.dumps(row, default=str))                  # 库里只存哈希
        H = {"X-Inv-Self": tok}
        self.assertEqual(self.c.get("/api/inv/s/hello", headers=H).json()["me"]["name"], "申请人丙")
        self.assertEqual([x["name"] for x in self.ok(self.c.get("/api/inv/s/receivers", headers=H))["rows"]], ["接收人甲", "接收人乙"])
        # 我的审批单：只按我的钉钉 userid 查、只查允许后补的模板
        pays = MagicMock(return_value={"ok": True, "msg": "", "rows": [
            {"procInstId": "PI-SELF-1", "businessId": "202609250900000009101", "title": "申请人丙提交的付款申请（公对公）",
             "createTime": "2026-09-20 10:00", "amount": 800.0, "payeeName": "自助收款方有限公司", "hasAttachments": False}]})
        with patch.object(inv.idt, "list_user_payments", pays):
            r = self.ok(self.c.get("/api/inv/s/payments", headers=H))
            self.ok(self.c.get("/api/inv/s/payments", headers=H))              # 3 分钟内同一范围走缓存
            self.assertEqual(pays.call_count, 1)
            self.ok(self.c.get("/api/inv/s/payments?days=120&fresh=1", headers=H))
            self.assertEqual((pays.call_count, pays.call_args[1]["days"], pays.call_args[1]["limit"]), (2, 120, sf.PAY_LIMIT))
            self.assertEqual(self.ok(self.c.get("/api/inv/s/payments?days=999", headers=H))["days"], 60)   # 只认 30/60/120
        sf._PAY_CACHE.clear()
        self.assertEqual(pays.call_args[0][0], "dt-app")
        self.assertIn("费用报销", pays.call_args[0][1])                      # 费用报销也能登记后补（V2.621 起默认允许）
        self.assertEqual((r["rows"][0]["laterId"], r["rows"][0]["hasInvoice"]), (None, False))
        day = (date.today() + timedelta(days=15)).isoformat()
        body = {"instId": "PI-SELF-1", "invKind": "normal", "taxRate": "3", "expectDate": day, "expectAmount": 800, "receiver": "recv"}
        r = self.c.post("/api/inv/s/later", json=dict(body, taxRate=""), headers=H)
        self.assertEqual(r.status_code, 400)                                  # 税率必填（V2.622）
        self.assertIn("税率", r.json()["msg"])
        sent = MagicMock(return_value={"sent": True, "msg": "已发送"})
        norm = self._norm(instId="PI-SELF-1", applicantUid="dt-app", payeeName="自助收款方有限公司", amount=800.0)
        with patch.object(books, "_cached_norm", MagicMock(return_value=dict(norm, applicantUid="dt-other", applicant="别人"))):
            r = self.c.post("/api/inv/s/later", json=body, headers=H)
            self.assertEqual(r.status_code, 403)                              # 别人发起的单不能登记
            self.assertIn("别人", r.json()["msg"])
        with patch.object(books, "_cached_norm", MagicMock(return_value=norm)), patch.object(inv, "notify_dt", sent):
            j = self.ok(self.c.post("/api/inv/s/later", json=body, headers=H))
        lid = j["later"]["id"]
        self.assertEqual((j["later"]["filedBy"], j["later"]["filedVia"], j["later"]["receiver"]), ("申请人丙", "self", "recv"))
        self.assertEqual(j["later"]["taxRate"], "3%")                         # 3 → 3%
        self.assertEqual(sent.call_args[0][0], ["dt-recv"])
        l = S.later_get(e, lid)
        self.assertEqual((l["filed_uid"], l["applicant_uid"]), ("dt-app", "dt-app"))
        fo = S.folder_by_inst(e, "PI-SELF-1")
        self.assertEqual([x["id"] for x in inv.folder_laters(e, fo)], [lid])  # 财务审核这张单时关联得到
        mine = self.ok(self.c.get("/api/inv/s/laters", headers=H))["rows"]
        self.assertEqual(mine[0]["id"], lid)                                   # 新登记的在最上面
        self.assertTrue(all(S.later_get(e, x["id"])["applicant_uid"] == "dt-app" for x in mine))
        with patch.object(inv.idt, "list_user_payments", pays):
            self.assertEqual(self.ok(self.c.get("/api/inv/s/payments", headers=H))["rows"][0]["laterId"], lid)
            # 已收齐也算「已登记」（V2.624：收齐后不能又显示成未登记）；关闭的不算
            S.later_update(e, lid, status="done")
            row = self.ok(self.c.get("/api/inv/s/payments", headers=H))["rows"][0]
            self.assertEqual((row["laterId"], row["laterStatus"]), (lid, "done"))
            S.later_update(e, lid, status="closed")
            self.assertIsNone(self.ok(self.c.get("/api/inv/s/payments", headers=H))["rows"][0]["laterId"])
            S.later_update(e, lid, status="open")
        # 资料：自己的能传，别人的看不到
        r = self.c.post("/api/inv/s/later/%d/docs" % lid, files=[("files", ("承诺函.txt", "承诺".encode("utf-8"), "text/plain"))], headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        other = self.later_row(folder_id=fo["id"], inst_id="PI-SELF-X", applicant_uid="dt-someone")
        r = self.c.post("/api/inv/s/later/%d/docs" % other, files=[("files", ("x.txt", b"x", "text/plain"))], headers=H)
        self.assertEqual(r.status_code, 404)
        # 入口网址：要后补池权限
        self.assertTrue(self.ok(self.get("/api/inv/s/link", "recv"))["url"].endswith("/#/invself"))
        self.assertEqual(self.get("/api/inv/s/link", "intern").status_code, 403)
        # 退出后令牌作废
        self.ok(self.c.post("/api/inv/s/logout", json={}, headers=H))
        self.assertEqual(self.c.get("/api/inv/s/laters", headers=H).status_code, 401)

    def test_24_self_login_code_limits(self):
        """验证码：输错 5 次作废；过期作废；钉钉免登认得出就直接发会话。"""
        inv, sf, S, e = self.inv, self.sf, self.S, self.e
        roster = {"ok": True, "msg": "", "rows": [{"userid": "dt-lim", "name": "限次人", "title": "", "dept": "公司-行政部"}]}
        sf._IP_HITS.clear()
        with patch.object(inv.idt, "roster", MagicMock(return_value=roster)):
            r = self.ok(self.c.post("/api/inv/s/login/send", json={"name": "限次人"}))
        bad = "%06d" % ((int(r["devCode"]) + 7) % 1000000)
        for _ in range(sf.CODE_TRIES):
            self.assertEqual(self.c.post("/api/inv/s/login/verify", json={"ticket": r["ticket"], "code": bad}).status_code, 400)
        r2 = self.c.post("/api/inv/s/login/verify", json={"ticket": r["ticket"], "code": r["devCode"]})
        self.assertEqual(r2.status_code, 400)                                 # 次数用完，对的码也不行了
        row = S.self_by_hash(e, "code", sf._h(r["ticket"]))
        S.self_update(e, row["id"], used=0, tries=0, expires_at="2000-01-01 00:00:00")
        self.assertIn("过期", self.c.post("/api/inv/s/login/verify", json={"ticket": r["ticket"], "code": r["devCode"]}).json()["msg"])
        with patch.object(inv.idt, "userinfo_by_code", MagicMock(return_value={"ok": True, "userid": "dt-lim", "name": "限次人"})), \
                patch.object(inv.idt, "roster", MagicMock(return_value=roster)):
            j = self.ok(self.c.post("/api/inv/s/login/dd", json={"code": "authcode"}))
        self.assertEqual((j["me"]["via"], j["me"]["dept"]), ("dingtalk", "公司-行政部"))
        with patch.object(inv.idt, "userinfo_by_code", MagicMock(return_value={"ok": False, "msg": "码无效"})):
            self.assertEqual(self.c.post("/api/inv/s/login/dd", json={"code": "x"}).status_code, 403)

if __name__ == "__main__":
    unittest.main()
