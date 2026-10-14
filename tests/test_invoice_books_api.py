# [Change Log]
# Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家）
# Description: 发票管家台账与后补池路由（routers/invoice_books.py）接口测试：临时 SQLite + 临时上传目录，只挂两个发票路由到裸 FastAPI，
#   core 用桩（x-test-user 头＝登录名，真账号真权限走 db.user_can）。全部合成数据（税局清单/抵扣清单/期初清单用 openpyxl 现造，
#   电子发票用合成 XML）；钉钉全部 mock，INV_DRY_SEND=1 绝不真发；INV_WORKER_OFF=1 不起后台线程；SQLite 下催票线程本就不起。
#   运行：repo 根目录 PYTHONPATH=01_Current_Deliverables/app/backend python -m unittest tests.test_invoice_books_api -v
import io
import json
import os
import re
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
from sqlalchemy import select                    # noqa: E402


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
        from routers import invoice_paper
        app.include_router(invoice_paper.router)
        from routers import invoice_vouchers
        cls.vouchers = invoice_vouchers
        app.include_router(invoice_vouchers.router)
        # 清空试运行数据（连带钉钉自动接入模块）：摘掉别的测试留下的旧模块，让它们绑到这次新导入的 routers.invoice 上
        for a in ("invoice_intake", "invoice_reset", "invoice_later_voucher", "logistics_voucher"):
            sys.modules.pop("routers." + a, None)
            if pkg is not None and hasattr(pkg, a):
                delattr(pkg, a)
        from routers import invoice_reset
        cls.reset = invoice_reset
        app.include_router(invoice_reset.router)
        from routers import invoice_later_voucher
        cls.l3 = invoice_later_voucher
        app.include_router(invoice_later_voucher.router)
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
        mk("cfg", ["enter_accounting", "enter:invledger", "inv_config"])      # 有发票管家设置权限、但不是主管理员
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
        for a in ("invoice_intake", "invoice_reset"):       # 这两个绑着本次的 routers.invoice，别留给后面的测试
            sys.modules.pop("routers." + a, None)
            if sys.modules.get("routers") is not None and hasattr(sys.modules["routers"], a):
                delattr(sys.modules["routers"], a)
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

    # 老用例不关心审票分工：做审核动作前，替这个人把还没人名下的票夹先认领了（分工本身在专门的用例里测，那里把它关掉）
    AUTO_CLAIM = True

    def _claim_for(self, user, url, body):
        ids = [body.get("folderId")] + list(body.get("folderIds") or [])
        m = re.match(r"/api/inv/(?:audit/)?item/(\d+)/", url)
        if m:
            it = self.S.item_get(self.db._engine, int(m.group(1))) or {}
            if it.get("review") == "pending":
                ids.append(it.get("folder_id"))
        for fid in ids:
            if isinstance(fid, int):
                self.c.post("/api/inv/audit/claim", json={"folderId": fid}, headers=self.H(user))

    def post(self, url, user, body=None):
        if self.AUTO_CLAIM and re.match(r"/api/inv/(audit/(approve|return|batch|items?/)|item/)", url):
            self._claim_for(user, url, body or {})
        if self.AUTO_CLAIM and url in ("/api/inv/folder/manual", "/api/inv/desk/scan") and "auditor" not in (body or {}):
            body = dict(body or {}, auditor="acct")      # 老用例：建票夹（含扫付款单建的）一律指定 acct 来审（建的时候必须指派清楚）
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

    def test_ledger_subject_month_paper_and_export(self):
        a, _ = self.make_item(num(9748), 100, fields={"buyer_name": "主体甲", "paper": 0})
        self.make_item(num(9749), 200, dt="20260801", fields={"buyer_name": "主体甲", "paper": 1})
        c, _ = self.make_item(num(9750), 300, fields={"buyer_name": "主体乙", "paper": 0})
        # 2026-10-13 起审核＝纸质票已收到，审核通过时都记成已到了；这里把两张改回没到，当作新规则之前审的老票
        self.S.item_update(self.e, a, paper=0)
        self.S.item_update(self.e, c, paper=0)
        q = "?subject=" + quote("主体甲") + "&month=2026-09&paper=missing"
        r = self.ok(self.get("/api/inv/ledger" + q, "viewer"))
        self.assertEqual(r["total"], 1)
        self.assertEqual(r["sum"]["total"], 100)
        self.assertIn("主体甲", r["subjects"])
        self.assertIn("主体乙", r["subjects"])
        self.assertEqual(r["rows"][0]["number"], num(9748))
        self.assertEqual(self.get("/api/inv/ledger?month=2026-13", "viewer").status_code, 400)
        self.assertEqual(self.get("/api/inv/ledger?paper=bad", "viewer").status_code, 400)
        from openpyxl import load_workbook
        ws = load_workbook(io.BytesIO(self.get("/api/inv/ledger/export" + q, "viewer").content)).active
        headers = [c.value for c in ws[1]]
        self.assertEqual(ws.cell(2, headers.index("纸质票") + 1).value, "未收到")
        self.assertEqual(ws.max_row, 3)  # 表头 + 一张票 + 合计

    def test_vouchers_exact_match_persistence_and_failure(self):
        iid, _ = self.make_item(num(9746), 345, fields={"buyer_name": "测试公司"})
        row = {"book": "测试公司会计账簿", "year": 2026, "month": 10, "group": "记", "number": 31, "summary": "转待认证 发票" + num(9746)}
        self.assertEqual(self.vouchers.match_vouchers(self.item(iid), [{**row, "summary": num(9746) + "1"}]), [])
        self.assertEqual(self.vouchers.match_vouchers(self.item(iid), [{**row, "book": "其他公司"}]), [])
        url = "/api/inv/vouchers/refresh"
        self.assertEqual(self.post(url, "viewer", {"itemId": iid}).status_code, 403)
        with patch.object(self.vouchers, "fetch_rows", return_value=[row, row]):
            first = self.ok(self.post(url, "acct", {"itemId": iid}))
            self.assertEqual(first["changed"], 1)
            self.assertEqual(len(first["item"]["bookkeeping"]["vouchers"]), 1)
            before = first["item"]["bookkeeping"]
            self.assertEqual(self.ok(self.post(url, "acct", {"itemId": iid}))["changed"], 0)
        with patch.object(self.vouchers, "fetch_rows", side_effect=RuntimeError("query error")):
            self.assertEqual(self.post(url, "acct", {"itemId": iid}).status_code, 502)
            self.assertEqual(self.item(iid)["flags_json"]["_bookkeeping"], before)
        with patch.object(self.vouchers, "fetch_rows", return_value=[{**row, "number": 32}]):
            self.assertEqual(self.ok(self.post(url, "acct", {"itemId": iid}))["changed"], 1)

    def test_vouchers_timer_round_saves_kingdee_calls(self):
        """V2.877 定时同步省金蝶调用：夜里不跑；当天第一轮全量(已对上的成批核实、没对上的才逐张查)；
        之后每轮只查「最近新建/改过的凭证」；那种查法失败就退回逐张查没对上的，一小时最多一次。"""
        from datetime import datetime as _dt
        V = self.vouchers
        a, _ = self.make_item(num(9741), 100, fields={"buyer_name": "测试公司"})
        b, _ = self.make_item(num(9742), 200, fields={"buyer_name": "测试公司"})
        row = lambda n, no: {"book": "测试公司会计账簿", "year": 2026, "month": 10, "group": "记", "number": no, "summary": "转待认证 发票" + n}
        bk = lambda i: self.item(i)["flags_json"].get("_bookkeeping") or {}
        self.db.set_setting(V._STATE_KEY, {})
        log = {"rows": [], "byv": [], "mod": []}

        def mk(key, ret):
            def f(arg, since=""):
                log[key].append(arg)
                if isinstance(ret, Exception):
                    raise ret
                return list(ret)
            return f

        def run(when, rows=(), byv=(), mod=()):
            for k in log:
                log[k].clear()
            with patch.object(V, "fetch_rows", mk("rows", rows)), patch.object(V, "fetch_by_vouchers", mk("byv", byv)), patch.object(V, "fetch_modified", mk("mod", mod)):
                return V.timer_round(when)

        # 夜里：一次金蝶都不调
        self.assertEqual(run(_dt(2026, 10, 9, 23, 5))["how"], "night")
        self.assertEqual(run(_dt(2026, 10, 9, 6, 59))["how"], "night")
        self.assertEqual((log["rows"], log["byv"], log["mod"]), ([], [], []))
        # 当天第一轮：全量。a 对上，b 没对上
        r = run(_dt(2026, 10, 9, 9, 0), rows=[row(num(9741), 31)])
        self.assertEqual((r["how"], bk(a).get("status"), bk(b).get("status")), ("full", "booked", "unknown"))
        self.assertIn(num(9741), log["rows"][0]); self.assertIn(num(9742), log["rows"][0])
        self.assertEqual(log["mod"], [])
        # 同一天后面的轮次：只查最近改过的凭证，不再逐张查；b 的凭证出现了就对上，a 不受影响
        r = run(_dt(2026, 10, 9, 9, 20), mod=[row(num(9742), 32)])
        # 已经查过的 a、b 不再逐张查（别的用例留下的「从没查过」的票会被顺手查掉，那是 V2.966 新加的，不算）
        self.assertFalse(any(num(9741) in x or num(9742) in x for x in log["rows"]))
        self.assertEqual((r["how"], bk(b).get("status"), bk(a).get("status")), ("inc", "booked", "booked"))
        self.assertEqual(log["mod"], ["2026-10-09 08:50:00"])            # 从上一轮往回多看 10 分钟
        r = run(_dt(2026, 10, 9, 9, 40), mod=[])                         # 没有新凭证：什么都不动(不会把已对上的改回未知)
        self.assertEqual((r["changed"], bk(a).get("status"), bk(b).get("status")), (0, "booked", "booked"))
        r = run(_dt(2026, 10, 9, 10, 0), mod=[row(num(9741), 40)])       # a 又多了一张凭证：并进去
        self.assertEqual([v["number"] for v in bk(a)["vouchers"]], ["记-31", "记-40"])
        # 第二天第一轮：已对上的按凭证号成批核实。a 的凭证还在 → 不逐张查；b 的凭证没了 → 才逐张查，查不到就改回未知
        r = run(_dt(2026, 10, 10, 9, 0), byv=[row(num(9741), 31), row(num(9741), 40)], rows=[])
        self.assertEqual(r["how"], "full")
        self.assertTrue(any(v["number"] == "记-32" for v in log["byv"][0]))
        self.assertNotIn(num(9741), log["rows"][0]); self.assertIn(num(9742), log["rows"][0])
        self.assertEqual((bk(a).get("status"), bk(b).get("status")), ("booked", "unknown"))
        # 「最近改过的凭证」查不了：退回逐张查没对上的；一小时内不重复
        r = run(_dt(2026, 10, 10, 9, 20), mod=RuntimeError("字段不支持"), rows=[row(num(9742), 33)])
        self.assertEqual((r["how"], bk(b).get("status")), ("fallback", "booked"))
        self.assertNotIn(num(9741), log["rows"][0])
        r = run(_dt(2026, 10, 10, 9, 40), mod=RuntimeError("字段不支持"), rows=[])
        self.assertEqual((r["how"], log["rows"]), ("wait", []))

    def test_fetch_rows_bulk_query(self):
        """V2.973：20 位数电号码一次查「摘要里带这两位开头的 20 位数字」（带期间下限），本地挑出含这些号码的行；老式号码照旧一个一次。"""
        import types
        V = self.vouchers
        calls = []
        n1, n2, n3 = "26329116565000301244", "26422000002552095516", "25442000000000000001"
        data = [{"summary": "火车票 " + n1}, {"summary": "住宿 " + n2}, {"summary": "别人的 26999999999999999999"}, {"summary": "去年的 " + n3}]

        def q(s, conf, form, fields, filt, order):
            calls.append(filt)
            if "like '%12345678%'" in filt:
                return [{"summary": "老票 12345678"}]
            return [r for r in data if ("like '%26[0-9]" in filt and "26" in r["summary"]) or ("like '%25[0-9]" in filt and n3 in r["summary"])]
        fake = types.SimpleNamespace(login=lambda: (None, None), _query=q)
        with patch.dict(sys.modules, {"kingdee_client": fake}):
            rows = V.fetch_rows([n1, n2, n3, "12345678"], since="2026-07")
        self.assertEqual(sorted(r["summary"] for r in rows), sorted(["火车票 " + n1, "住宿 " + n2, "去年的 " + n3, "老票 12345678"]))
        self.assertEqual(len(calls), 3)                                        # 26 开头一次、25 开头一次、8 位老号码一次
        self.assertTrue(all("FPeriod >= 7" in c for c in calls[:2]), calls)
        with patch.dict(sys.modules, {"kingdee_client": fake}):
            calls.clear()
            V.fetch_rows([n1])                                                 # 只有 1 张：照旧按号码查
        self.assertEqual(calls, ["(FEXPLANATION like '%%%s%%')" % n1])
        self.assertEqual(V._since([{"issue_date": "2026-09-10"}, {"issue_date": "2026-07-15"}]), "2026-07")
        self.assertEqual(V._since([{"issue_date": "2026-09-10"}, {"issue_date": None}]), "")

    def test_vouchers_refresh_all_runs_in_background(self):
        """V2.966：「刷新全部金蝶凭证」后台跑、带进度：已对上的成批核实，只逐张查没对上的；查完一张就落库；
        正在跑时再点只回进度不报错；定时同步正占着时让人等一下；定时那一轮顺手查「从没查过」的票（有上限）。"""
        import time as _t
        from datetime import datetime as _dt
        V = self.vouchers
        a, _ = self.make_item(num(9661), 100, fields={"buyer_name": "测试公司"})
        b, _ = self.make_item(num(9662), 200, fields={"buyer_name": "测试公司"})
        row = lambda n, no: {"book": "测试公司会计账簿", "year": 2026, "month": 10, "group": "记", "number": no, "summary": "转待认证 发票" + n}
        bk = lambda i: self.item(i)["flags_json"].get("_bookkeeping") or {}
        fl = dict(self.item(a)["flags_json"] or {})
        fl["_bookkeeping"] = {"status": "booked", "source": "kingdee", "vouchers": [{"book": "测试公司会计账簿", "period": "2026-10", "number": "记-31"}]}
        self.S.item_update(self.e, a, flags_json=fl)
        asked, gate = [], __import__("threading").Event()

        def rows_of(numbers, since=""):
            asked.extend(numbers)
            gate.wait(5)
            return [row(num(9662), 44)] if num(9662) in numbers else []
        url = "/api/inv/vouchers/refresh"
        self.assertEqual(self.get(url, "viewer").status_code, 200)            # 进度谁都能看（有台账页就行）
        with patch.object(V, "fetch_by_vouchers", return_value=[row(num(9661), 31)]), patch.object(V, "fetch_rows", side_effect=rows_of):
            r = self.ok(self.post(url, "acct", {}))
            self.assertTrue(r["job"]["running"])
            r2 = self.ok(self.post(url, "acct", {}))                          # 正在跑：回进度，不报错
            self.assertTrue(r2["job"]["running"])
            gate.set()
            for _ in range(100):
                if not self.ok(self.get(url, "acct"))["job"]["running"]:
                    break
                _t.sleep(0.05)
        j = self.ok(self.get(url, "acct"))["job"]
        self.assertFalse(j["running"])
        self.assertEqual((j["by"], j["error"]), ("acct", ""))
        self.assertGreaterEqual(j["verified"], 1)                             # a 是成批核实的，没逐张搜
        self.assertNotIn(num(9661), asked)
        self.assertIn(num(9662), asked)
        self.assertEqual(j["checked"], j["total"])
        self.assertEqual(j["ledgerN"] + j["auditN"], j["total"])             # V2.968：台账几张＋还在审核的几张
        self.assertEqual((bk(a).get("status"), bk(b).get("status"), bk(b)["vouchers"][0]["number"]), ("booked", "booked", "记-44"))
        # 定时同步正占着：让人等一下
        V._LOCK.acquire()
        try:
            r = self.post(url, "acct", {}).json()
            self.assertFalse(r["ok"])
            self.assertIn("定时同步正在查金蝶", r["msg"])
        finally:
            V._LOCK.release()
        # 单张刷新照旧当场查
        with patch.object(V, "fetch_rows", return_value=[row(num(9662), 45)]):
            self.assertEqual(self.ok(self.post(url, "acct", {"itemId": b}))["changed"], 1)
        # 定时增量那一轮：顺手查从没查过的票，每轮有上限
        c, _ = self.make_item(num(9663), 300, fields={"buyer_name": "测试公司"})
        self.assertEqual(bk(c), {})
        self.db.set_setting(V._STATE_KEY, {"last_full": "2026-10-14", "last_inc": "2026-10-14 09:00:00"})
        seen = []
        with patch.object(V, "fetch_modified", return_value=[]), patch.object(V, "fetch_rows", side_effect=lambda ns, since="": seen.append(list(ns)) or [row(num(9663), 50)]):
            r = V.timer_round(_dt(2026, 10, 14, 9, 20))
        self.assertEqual(r["how"], "inc")
        self.assertTrue(seen and len(seen[0]) <= V.NEW_PER_ROUND)
        if num(9663) in seen[0]:
            self.assertEqual(bk(c).get("status"), "booked")

    def test_normal_invoice_not_booked(self):
        """普票不入账：刷新金蝶凭证不去查普票；台账那一栏写「普票不入账」（noBook），「未匹配 / 待刷新」筛选里不算它；导出写「普票不入账」。"""
        V = self.vouchers
        a, _ = self.make_item(num(9671), 100, fields={"buyer_name": "测试公司", "inv_type": "normal"})
        b, _ = self.make_item(num(9672), 200, fields={"buyer_name": "测试公司"})
        self.assertNotIn(a, {i["id"] for i in V._active_items()})
        self.assertIn(b, {i["id"] for i in V._active_items()})
        rows = {r["id"]: r for r in self.ok(self.get("/api/inv/ledger?size=200&q=" + num(9671), "acct"))["rows"]}
        self.assertTrue(rows[a]["noBook"])
        unk = {r["id"] for r in self.ok(self.get("/api/inv/ledger?size=200&booked=unknown", "acct"))["rows"]}
        self.assertNotIn(a, unk)
        self.assertIn(b, unk)
        it = self.item(a)
        it["folder"] = {}
        self.assertIn("普票不入账", self.books._ledger_row_values(it))
        # V2.972：旅客运输服务普票（货拉拉那种）会计判了不可抵扣 → 也算普票不入账；判了可抵扣的照旧去金蝶找
        t, _ = self.make_item(num(9673), 300, fields={"buyer_name": "测试公司", "inv_type": "travel", "deductible": 0})
        self.assertNotIn(t, {i["id"] for i in V._active_items()})
        self.assertTrue(self.ok(self.get("/api/inv/ledger?size=200&q=" + num(9673), "acct"))["rows"][0]["noBook"])
        self.S.item_update(self.e, t, deductible=1)
        self.assertIn(t, {i["id"] for i in V._active_items()})
        # 台账筛选「票总管」：记了「票总管已有」的算在
        self.S.item_update(self.e, b, deduct_status="other")
        yes = {r["id"] for r in self.ok(self.get("/api/inv/ledger?size=500&other=yes", "acct"))["rows"]}
        no = {r["id"] for r in self.ok(self.get("/api/inv/ledger?size=500&other=no", "acct"))["rows"]}
        self.assertIn(b, yes)
        self.assertNotIn(b, no)
        self.assertIn(t, no)

    def test_audit_records_paper_and_voucher_before_approval(self):
        iid, _ = self.make_item(num(9745), 321, approve=False)
        self.assertEqual(self.post("/api/inv/paper/mark", "viewer", {"itemId": iid, "paper": True}).status_code, 403)
        self.ok(self.post("/api/inv/paper/mark", "acct", {"itemId": iid, "paper": True}))
        self.assertTrue(self.item(iid)["paper"])
        self.ok(self.post(f"/api/inv/item/{iid}/bookkeeping", "acct", {"status": "booked", "vouchers": [{"book": "测试账簿", "period": "2026-10", "number": "记-1"}]}))
        self.assertEqual(self.item(iid)["review"], "pending")
        self.assertEqual(self.item(iid)["flags_json"]["_bookkeeping"]["status"], "booked")

    def test_bookkeeping_record_filter_export(self):
        iid, _ = self.make_item(num(9744), 123)
        url = f"/api/inv/item/{iid}/bookkeeping"
        body = {"status": "booked", "vouchers": [{"book": "测试账簿", "period": "2026-09", "number": "记-123"}, {"book": "另一账簿", "period": "2026-10", "number": "记-456"}]}
        self.assertEqual(self.post(url, "viewer", body).status_code, 403)
        self.assertEqual(self.post(url, "acct", {"status": "booked"}).status_code, 400)
        self.assertEqual(self.post(url, "acct", {"status": "booked", "vouchers": [{"book": "A", "period": "2026-13", "number": "1"}]}).status_code, 400)
        self.S.item_update(self.e, iid, flags_json={"_warnings": ["keep"]})
        r = self.ok(self.post(url, "acct", body))
        self.assertEqual(len(r["item"]["bookkeeping"]["vouchers"]), 2)
        self.assertEqual(r["item"]["warnings"], ["keep"])
        q = "?q=" + num(9744)
        self.assertEqual(self.ok(self.get("/api/inv/ledger" + q + "&booked=booked", "viewer"))["total"], 1)
        self.assertEqual(self.ok(self.get("/api/inv/ledger" + q + "&booked=unknown", "viewer"))["total"], 0)
        from openpyxl import load_workbook
        ws = load_workbook(io.BytesIO(self.get("/api/inv/ledger/export" + q, "viewer").content)).active
        headers = [c.value for c in ws[1]]
        self.assertEqual(ws.cell(2, headers.index("做账状态") + 1).value, "已做账")
        self.assertIn("记-456", ws.cell(2, headers.index("凭证信息") + 1).value)
        self.ok(self.post(url, "acct", {"status": "unbooked"}))
        self.assertEqual(self.ok(self.get("/api/inv/ledger" + q + "&booked=unbooked", "viewer"))["total"], 1)
        self.S.item_update(self.e, iid, review="void")
        self.assertEqual(self.post(url, "acct", body).status_code, 409)

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
            for w in ("有一张发票后补单交给你接收", "收款方：后补收款方有限公司", "付款金额：¥1,000.00", day, "申请人：申请人丙"):
                self.assertIn(w, text)
            self.assertTrue(("后补单 #%d" % lid) in text or ("#/invlater?id=%d" % lid) in text, text)
            self.assertNotIn("：/#/", text)                                 # 不发点不开的半截地址
            self.assertEqual(text.count("\n"), 6)                           # 标题一行 + 一行一件事
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
        (a_uids, a_text), (r_uids, r_text) = [c[0] for c in sent.call_args_list]
        self.assertEqual((a_uids, r_uids), (["dt-app2"], ["dt-recv2"]))     # 申请人一条、接收人一条
        for w in ("催票提醒", "还有 2 天", "还差：¥500.00 的专票没到", "再把纸质的交给 接收人乙", "上传：钉钉工作台"):
            self.assertIn(w, a_text)
        self.assertNotIn("发票后补池", a_text)                              # 财务的页面不发给申请人
        for w in ("已向申请人催票", "申请人：申请人丁", "还差 ¥500.00"):
            self.assertIn(w, r_text)
        self.assertNotIn("上传：", r_text)
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
        """申请人自助登记发票后补（V2.621）：不走工作台登录，钉钉验证码认人（手机号查姓名不发码、确认后发码、60 秒不重发、错码计次、一码一用）；
        只列/只能登记自己发起的单；登记后进同一个后补池（filedVia=self），财务审核时按审批单关联；只能给自己的后补单传资料。人名单号都是编的。"""
        inv, books, sf, S, e = self.inv, self.books, self.sf, self.S, self.e
        self.assertEqual(self.c.get("/api/inv/s/payments").status_code, 401)
        self.assertIsNone(self.c.get("/api/inv/s/hello").json()["me"])
        roster = {"ok": True, "msg": "", "rows": [
            {"userid": "dt-app", "name": "申请人丙", "title": "采购", "dept": "公司-采购部"},
            {"userid": "dt-dup1", "name": "重名人", "title": "", "dept": "公司-销售部"},
            {"userid": "dt-dup2", "name": "重名人", "title": "", "dept": "公司-生产部"}]}
        sf._IP_HITS.clear()
        person = dict(roster["rows"][0], ok=True)
        with patch.object(inv.idt, "userinfo_by_mobile", MagicMock(return_value=person)) as lookup, \
                patch.object(inv, "notify_dt", wraps=inv.notify_dt) as notify:
            self.assertEqual(self.c.post("/api/inv/s/login/send", json={"name": "申请人丙"}).status_code, 400)
            for mobile in ("", "123", "1380000000x", 13800000001):
                self.assertEqual(self.c.post("/api/inv/s/login/lookup", json={"mobile": mobile}).status_code, 400)
            lookup.assert_not_called()
            who = self.ok(self.c.post("/api/inv/s/login/lookup", json={"mobile": "13800000001"}))
            self.assertEqual(who["name"], "申请人丙")
            self.assertNotIn("userid", who)
            notify.assert_not_called()  # 查姓名绝不发验证码
            confirm = {"confirmation": who["confirmation"]}
            self.assertEqual(self.c.post("/api/inv/s/login/send", json=confirm, headers={"x-real-ip": "other"}).status_code, 400)
            r = self.ok(self.c.post("/api/inv/s/login/send", json=dict(confirm, userid="dt-other", name="别人")))
            self.assertEqual(notify.call_args[0][0], ["dt-app"])  # 收件人只认服务端确认令牌
            ticket, code = r["ticket"], r["devCode"]
            self.assertEqual(len(code), 6)
            self.assertEqual(self.c.post("/api/inv/s/login/send", json=confirm).status_code, 429)
            row = S.self_by_hash(e, "identity", sf._h(who["confirmation"]))
            S.self_update(e, row["id"], expires_at="2000-01-01 00:00:00")
            self.assertEqual(self.c.post("/api/inv/s/login/send", json=confirm).status_code, 400)
            with patch.object(inv.idt, "userinfo_by_mobile", return_value={"ok": False, "msg": "未找到对应员工"}):
                self.assertEqual(self.c.post("/api/inv/s/login/lookup", json={"mobile": "13800000002"}).status_code, 400)
            sf._IP_HITS["lookup:testclient"] = [__import__("time").time()] * sf.IP_MAX
            self.assertEqual(self.c.post("/api/inv/s/login/lookup", json={"mobile": "13800000001"}).status_code, 429)
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

    def test_qr_login_requires_approval_and_is_single_use(self):
        sf = self.sf
        sf._IP_HITS.clear()
        qr = self.ok(self.c.post("/api/inv/s/login/qr", json={}))
        row = self.S.self_by_hash(self.e, "qr", sf._h(qr["ticket"]))
        self.assertTrue(self.ok(self.c.post("/api/inv/s/login/qr/check", json={"ticket": qr["ticket"]}))["pending"])
        self.assertEqual(self.c.post("/api/inv/s/login/qr/check", json={"ticket": qr["ticket"]}, headers={"x-real-ip": "other"}).status_code, 410)
        # 单独创建合成扫描令牌，绑定真实接口生成的电脑挑战。
        scan = "synthetic-scan-secret"
        self.S.self_insert(self.e, kind="qrscan", token_hash=sf._h(scan), code_hash=sf._h(qr["ticket"]),
                           via=qr["number"], used=0, created_at=self.inv.now_s(), expires_at=sf._at(minutes=5))
        self.assertEqual(self.c.post("/api/inv/s/login/qr/approve", json={"scan": scan, "confirm": True}).status_code, 401)
        token = sf._new_session("dt-qr-user", "扫码员工", "采购", "dingtalk", "mobile")
        H = {"X-Inv-Self": token}
        preview = self.ok(self.c.post("/api/inv/s/login/qr/approve", json={"scan": scan}, headers=H))
        self.assertEqual(preview["number"], qr["number"])
        self.assertTrue(self.ok(self.c.post("/api/inv/s/login/qr/check", json={"ticket": qr["ticket"]}))["pending"])
        self.ok(self.c.post("/api/inv/s/login/qr/approve", json={"scan": scan, "confirm": True, "userid": "attacker"}, headers=H))
        self.assertEqual(self.c.post("/api/inv/s/login/qr/approve", json={"scan": scan, "confirm": True}, headers=H).status_code, 410)
        login = self.ok(self.c.post("/api/inv/s/login/qr/check", json={"ticket": qr["ticket"]}))
        self.assertEqual(login["me"]["name"], "扫码员工")
        self.assertEqual(self.S.self_by_hash(self.e, "session", sf._h(login["token"]))["dt_userid"], "dt-qr-user")
        self.assertEqual(self.c.post("/api/inv/s/login/qr/check", json={"ticket": qr["ticket"]}).status_code, 410)
        qr2 = self.ok(self.c.post("/api/inv/s/login/qr", json={}))
        row2 = self.S.self_by_hash(self.e, "qr", sf._h(qr2["ticket"]))
        self.S.self_update(self.e, row2["id"], expires_at="2000-01-01 00:00:00")
        self.assertEqual(self.c.post("/api/inv/s/login/qr/check", json={"ticket": qr2["ticket"]}).status_code, 410)

    def test_24_self_login_code_limits(self):
        """验证码：输错 5 次作废；过期作废；钉钉免登认得出就直接发会话。"""
        inv, sf, S, e = self.inv, self.sf, self.S, self.e
        roster = {"ok": True, "msg": "", "rows": [{"userid": "dt-lim", "name": "限次人", "title": "", "dept": "公司-行政部"}]}
        sf._IP_HITS.clear()
        with patch.object(inv.idt, "userinfo_by_mobile", return_value=dict(roster["rows"][0], ok=True)):
            who = self.ok(self.c.post("/api/inv/s/login/lookup", json={"mobile": "13800000003"}))
            r = self.ok(self.c.post("/api/inv/s/login/send", json={"confirmation": who["confirmation"]}))
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

    def test_25_admin_preview_as_person(self):
        """管理员「以某人身份预览」：输入钉钉姓名→拉这个人能选的后补可登记审批单（同他本人所见）；重名先选；要设置权限。号都是编的。"""
        idt = self.inv.idt
        self.assertEqual(self.get("/api/inv/s/preview?name=张三", "recv").status_code, 403)
        self.assertEqual(self.c.get("/api/inv/s/preview?name=张三").status_code, 401)
        roster = {"ok": True, "msg": "", "rows": [
            {"userid": "dt-app", "name": "申请人丙", "title": "采购", "dept": "公司-采购部"},
            {"userid": "dt-d1", "name": "重名", "title": "", "dept": "公司-A部"},
            {"userid": "dt-d2", "name": "重名", "title": "", "dept": "公司-B部"}]}
        pays = MagicMock(return_value={"ok": True, "msg": "", "rows": [
            {"procInstId": "PI-PV-1", "businessId": "202610050001", "title": "预览单",
             "template": "付款申请（公对公）", "createTime": "2026-10-01 09:00",
             "amount": 500.0, "payeeName": "预览供应商", "hasAttachments": False,
             "approvalStatus": "COMPLETED", "approvalResult": "agree"}]})
        with patch.object(idt, "roster", MagicMock(return_value=roster)), patch.object(idt, "list_user_payments", pays):
            self.assertEqual(self.get("/api/inv/s/preview?name=查无此人", "boss").status_code, 404)
            r = self.ok(self.get("/api/inv/s/preview?name=重名", "boss"))
            self.assertEqual((r["need"], len(r["choices"])), ("pick", 2))
            r = self.ok(self.get("/api/inv/s/preview?name=重名&pick=1", "boss"))
            self.assertTrue(r["person"]["dept"].endswith("B部"))
            r = self.ok(self.get("/api/inv/s/preview?name=申请人丙&days=120", "boss"))
        self.assertEqual((pays.call_args[0][0], pays.call_args[1]["days"]), ("dt-app", 120))
        self.assertIn("付款申请（公对公）", pays.call_args[0][1])
        self.assertEqual((r["person"]["name"], r["days"]), ("申请人丙", 120))
        row = r["rows"][0]
        self.assertEqual((row["procInstId"], row["laterId"], row["hasInvoice"]), ("PI-PV-1", None, False))

    @unittest.skipUnless(HAS_XL, "缺 openpyxl")
    def test_26_deduct_tick_export(self):
        """直接从台账出税局勾选清单：审核通过＋可抵扣的才进；按购方主体分开出；下载才记"已标注"，下次默认不再带。
        「金额」列照用户给的实物：专票写价税合计、行程单写不含税、铁路客票票面没有税额的金额也留空。
        开票日期都放在 2024-02（别的用例不用这个月），号码、税号都是编的。"""
        OUR, TID, OTHER = self.OUR, self.OUR_TID, self.OTHER_TID
        face = dict(amount=100, tax=6, total=106, buyer_name=OUR, buyer_tax_id=TID, seller_name="勾选销方甲", seller_tax_id="91tick0a")
        a, _ = self.make_item(num(26001), 106, "31", "20240202", face)                             # 专票：进
        b, _ = self.make_item(num(26002), 106, "31", "20240203", face, decisions={"deductible": False})   # 会计判不可抵：不进也不提
        c, _ = self.make_item(num(26003), 106, "31", "20240204", face, approve=False)               # 没审核：不进，报张数
        d, _ = self.make_item(num(26004), 300, "51", "20240205", dict(buyer_name=OUR, buyer_tax_id=""))   # 铁路客票：票面没税号没税额
        e, _ = self.make_item(num(26005), 212, "31", "20240206", dict(face, amount=200, tax=12, total=212,
                                                                     buyer_name="别家公司", buyer_tax_id=OTHER))
        g, _ = self.make_item(num(26006), 530, "31", "20240207", dict(face, amount=None, tax=30, total=530))   # 没读到不含税金额也不碍事
        h, _ = self.make_item(num(26007), 530, "31", "20240207", dict(face, amount=None, tax=30, total=530))
        self.S.item_update(self.e, h, number=num(26006), dup_key=self.item(g)["dup_key"])           # 同一张票拆到两张单上
        k, _ = self.make_item(num(26008), 1090, "61", "20240208", dict(amount=1000, tax=90, total=1090, buyer_name=OUR, buyer_tax_id=TID))   # 行程单
        url, rng = "/api/inv/deduct/tick/prepare", {"from": "2024-02-01", "to": "2024/2/29"}
        self.assertEqual(self.c.post(url, json=rng).status_code, 401)
        self.assertEqual(self.post(url, "viewer", rng).status_code, 403)
        self.assertEqual(self.post(url, "nogate", rng).status_code, 403)
        self.assertEqual(self.post(url, "acct", {"from": "二月"}).status_code, 400)
        j = self.ok(self.post(url, "acct", rng))
        s = j["summary"]
        self.assertEqual((s["rows"], s["tax"], s["marked"]), (5, 138.0, 0), s)
        self.assertEqual([(x["key"], x["count"]) for x in s["skipped"]], [("notApproved", 1)])
        self.assertEqual([(x["taxId"], x["count"]) for x in j["buyers"]], [(TID, 4), (OTHER, 1)])
        self.assertEqual({x["type"]: x["count"] for x in s["byType"]}, {"电子发票(增值税专用发票)": 3, "电子发票(铁路电子客票)": 1,
                                                                        "电子发票(航空运输电子客票行程单)": 1})
        pv = {x["number"]: x for x in j["preview"]}
        self.assertEqual(sorted(pv), [num(26001), num(26004), num(26005), num(26006), num(26008)])
        self.assertIn("不在本公司抬头清单", pv[num(26005)]["warn"])
        self.assertEqual((pv[num(26004)]["buyerTaxId"], pv[num(26004)]["amount"]), (TID, None))    # 抬头是本公司 → 补设置里的税号
        self.assertIn("没有税额", pv[num(26004)]["warn"])
        self.assertEqual((pv[num(26001)]["amount"], pv[num(26006)]["amount"], pv[num(26008)]["amount"]), (106.0, 530.0, 1000.0))
        # 税局按纳税人分别导入：选一家再出
        j = self.ok(self.post(url, "acct", dict(rng, subject=TID.lower())))
        self.assertEqual((j["summary"]["rows"], len(j["buyers"])), (4, 2))
        self.assertEqual(self.item(a)["deduct_status"], "")                                        # 下载前不动
        self.assertEqual(self.get("/api/inv/deduct/download?token=%s" % j["token"], "viewer").status_code, 403)
        r = self.get("/api/inv/deduct/download?token=%s" % j["token"], "acct")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn(quote("增值税发票勾选认证-"), r.headers["content-disposition"])          # V2.972 文件名：增值税发票勾选认证-做账月份-导出人
        from openpyxl import load_workbook
        ws = load_workbook(io.BytesIO(r.content))["invoices"]
        vals = [[x or "" for x in row] for row in ws.values]
        self.assertEqual(vals[0], self.ie.TAX_TICK_COLUMNS)
        self.assertEqual([v[:13] for v in vals[1:]], [
            ["是", num(26001), "", num(26001), "2024-02-02", "106.00", "6.00", "6.00", TID, "勾选销方甲", "91TICK0A", "",
             "电子发票(增值税专用发票)"],
            ["是", num(26004), "", num(26004), "2024-02-05", "", "", "", TID, "", "", "", "电子发票(铁路电子客票)"],
            ["是", num(26006), "", num(26006), "2024-02-07", "530.00", "30.00", "30.00", TID, "勾选销方甲", "91TICK0A", "",
             "电子发票(增值税专用发票)"],
            ["是", num(26008), "", num(26008), "2024-02-08", "1000.00", "90.00", "90.00", TID, "", "", "",
             "电子发票(航空运输电子客票行程单)"]])
        st = lambda *ids: [self.item(i)["deduct_status"] or "" for i in ids]
        self.assertEqual(st(a, b, c, d, e, g, h, k), ["marked", "", "", "marked", "", "marked", "marked", "marked"])
        self.assertTrue(any(x["action"] == "标注抵扣勾选" for x in self.S.logs_of(self.e, item_id=h)))
        # 导出过的下次默认不带；勾「之前导出过的也带上」才带
        j = self.ok(self.post(url, "acct", dict(rng, subject=TID)))
        self.assertEqual((j["summary"]["rows"], j["token"]), (0, ""))
        self.assertEqual([(x["key"], x["count"]) for x in j["summary"]["skipped"]], [("notApproved", 1), ("marked", 4)])
        j = self.ok(self.post(url, "acct", dict(rng, subject=TID, marked=True)))
        self.assertEqual((j["summary"]["rows"], j["summary"]["marked"]), (4, 4))
        self.assertTrue(j["token"])
        # 税局已显示勾选过的、显示作废红冲的不进
        self.S.item_update(self.e, a, deduct_status="checked")
        self.S.item_update(self.e, d, verify="red")
        j = self.ok(self.post(url, "acct", dict(rng, subject=TID, marked=True)))
        self.assertEqual(j["summary"]["rows"], 2)
        self.assertEqual({x["key"]: x["count"] for x in j["summary"]["skipped"]}, {"notApproved": 1, "checked": 1, "red": 1})

    def test_28_applicant_uploads_invoice_to_later(self):
        """申请人自己把发票传到后补单上：当场识别登记，但财务确认前不算已到、不发"已签收"；
        财务点「确认收到」、财务自己扫到同一张票、这张票审核通过——三种都算确认。别人的单传不了，够数了不再催申请人。
        人名、单号、发票都是编的。"""
        inv, books, sf, S, e = self.inv, self.books, self.sf, self.S, self.e
        f = self.tip.FAKE
        for i, inst in enumerate(("PI-SELFUP-A", "PI-SELFUP-B", "PI-SELFUP-C")):
            S.folder_upsert_from_approval(e, self._norm(instId=inst, businessId="20261012280000%d" % i, applicant="申请人戊",
                                                        applicantUid="dt-up", payeeName=f["seller"], amount=600.0), "recv", "later")
        H = {"X-Inv-Self": sf._new_session("dt-up", "申请人戊", "公司-销售部", "dingtalk", "t")}
        other = {"X-Inv-Self": sf._new_session("dt-else", "路人己", "公司-别的部", "dingtalk", "t")}
        day = (date.today() + timedelta(days=2)).isoformat()
        body = {"invKind": "special", "taxRate": "6%", "expectDate": day, "expectAmount": 600, "receiver": "recv2"}
        up = lambda lid, files, h=H: self.c.post("/api/inv/s/later/%d/invoices" % lid, files=[("files", x) for x in files], headers=h)
        xml = lambda n, total: ("票%s.xml" % n[-4:], einv_xml(n, f["seller"], f["seller_tid"], f["buyer"], f["buyer_tid"],
                                                              round(total / 1.06, 2), round(total - total / 1.06, 2), total), "application/xml")
        sent = MagicMock(return_value={"sent": True, "msg": "已发送"})
        with patch.object(inv, "notify_dt", sent):
            A = self.ok(self.post("/api/inv/later/create", "recv", dict(body, instId="PI-SELFUP-A")))["later"]["id"]
            sent.reset_mock()
            # 没登录 / 别人的单 / 没带文件
            self.assertEqual(self.c.post("/api/inv/s/later/%d/invoices" % A, files=[("files", xml(num(28001), 300))]).status_code, 401)
            self.assertEqual(up(A, [xml(num(28001), 300)], other).status_code, 404)
            self.assertEqual(self.c.post("/api/inv/s/later/%d/invoices" % A, headers=H).status_code, 400)
            # 传一张 300：登记了、挂在这张后补单上，但不算已到
            j = self.ok(up(A, [xml(num(28001), 300)]))
            it = j["results"][0]["item"]
            self.assertEqual((j["uploaded"], j["results"][0]["action"], it["origin"], it["laterId"], it["selfPending"], it["paper"]),
                             (1, "item", "self", A, True, False))
            lv = j["later"]
            self.assertEqual((lv["status"], lv["receivedAmount"], lv["remaining"], lv["selfCount"], lv["selfAmount"]), ("open", 0.0, 600.0, 1, 300.0))
            uids, text = sent.call_args[0]                                   # 钉钉告诉接收人去确认，不告诉申请人"已签收"
            self.assertEqual(uids, ["dt-recv2"])
            for w in ("申请人传了发票，等你确认", "申请人：申请人戊", "这次上传 1 张，现在共 ¥300.00 等确认", "确认："):
                self.assertIn(w, text)
            # 同一份再传一次不重复登记
            again = self.ok(up(A, [xml(num(28001), 300)]))
            self.assertEqual((again["uploaded"], again["results"][0]["action"], again["later"]["selfCount"]), (0, "same", 1))
            # 申请人自己看：我的后补单里带着"已上传、等确认"
            mine = [x for x in self.ok(self.c.get("/api/inv/s/laters", headers=H))["rows"] if x["id"] == A][0]
            self.assertEqual((mine["selfCount"], mine["selfAmount"], mine["status"]), (1, 300.0, "open"))
            # 财务详情里看得到这张票，标着等确认；别的权限点不了确认
            d = self.ok(self.get("/api/inv/later/%d" % A, "recv2"))
            self.assertEqual([(x["number"], x["selfPending"]) for x in d["items"]], [(num(28001), True)])
            self.assertEqual(self.post("/api/inv/later/%d/confirm-self" % A, "viewer").status_code, 403)
            # 传错了自己删：只能删自己这张单上、财务还没确认的；别人删不了
            wrong = self.ok(up(A, [xml(num(28008), 50)]))["results"][0]["item"]["id"]
            rows = self.ok(self.c.get("/api/inv/s/later/%d/invoices" % A, headers=H))["rows"]
            self.assertEqual([(x["number"], x["mine"], x["pending"], x["canRemove"]) for x in rows],
                             [(num(28001), True, True, True), (num(28008), True, True, True)])
            self.assertEqual(self.c.get("/api/inv/s/later/%d/invoices" % A, headers=other).status_code, 404)
            rm = lambda iid, h=H: self.c.post("/api/inv/s/later/%d/invoices/%d/remove" % (A, iid), headers=h)
            self.assertEqual(rm(wrong, other).status_code, 404)
            self.assertEqual(rm(999999).status_code, 404)
            j = self.ok(rm(wrong))
            self.assertEqual((j["later"]["selfCount"], j["later"]["selfAmount"]), (1, 300.0))
            self.assertEqual(S.item_get(e, wrong)["status"], "removed")
            self.assertEqual(rm(wrong).status_code, 404)                                              # 删过了
            self.assertTrue(any(x["action"] == "移除票据" and x["user"] == "申请人戊" for x in S.logs_of(e, item_id=wrong)))
            self.assertEqual(self.ok(up(A, [xml(num(28008), 50)]))["uploaded"], 1)                     # 删掉的那份可以重新传
            self.ok(rm([x["id"] for x in self.ok(self.c.get("/api/inv/s/later/%d/invoices" % A, headers=H))["rows"]][-1]))
            sent.reset_mock()
            # 再传一张 300 凑够数：还是不算收齐，也不发"已签收"
            sent.reset_mock()
            j = self.ok(up(A, [xml(num(28002), 300)]))
            self.assertEqual((j["later"]["status"], j["later"]["selfCount"], j["later"]["selfAmount"]), ("open", 2, 600.0))
            self.assertEqual([c[0][0] for c in sent.call_args_list], [["dt-recv2"]])
            # 够数了、只差财务确认：每日催票不再催申请人
            S.later_update(e, A, expect_date=(date.today() - timedelta(days=3)).isoformat())
            sent.reset_mock()
            books._REMIND["lastDay"] = ""
            res = books.remind_tick(now=datetime.combine(date.today(), dtime(11, 0)), force=True)
            self.assertNotIn(A, res["ids"])
            # 财务确认：两张都算进已到 → 收齐 → 这时才告诉申请人"已签收"
            sent.reset_mock()
            c = self.ok(self.post("/api/inv/later/%d/confirm-self" % A, "recv2"))
            self.assertEqual((c["later"]["status"], c["later"]["receivedAmount"], c["later"]["selfCount"]), ("done", 600.0, 0))
            self.assertIn("2 张", c["msg"])
            self.assertEqual(sent.call_args[0][0], ["dt-up"])
            self.assertIn("已签收", sent.call_args[0][1])
            self.assertEqual(self.post("/api/inv/later/%d/confirm-self" % A, "recv2").status_code, 400)       # 没有等确认的了
            r = rm(it["id"])                                                                          # 财务确认了就不能删
            self.assertEqual(r.status_code, 400)
            self.assertIn("财务已经确认", r.json()["msg"])
            self.assertEqual([x["canRemove"] for x in self.ok(self.c.get("/api/inv/s/later/%d/invoices" % A, headers=H))["rows"]], [False, False])
            self.assertEqual(up(A, [xml(num(28009), 10)]).status_code, 400)                                    # 已收齐不用再传
            self.assertTrue(any(x["action"] == "确认收到申请人上传的发票" for x in S.logs_of(e, later_id=A)))
            self.assertTrue(S.item_get(e, it["id"])["flags_json"]["selfOk"]["by"] == "recv2")
            # 第二张单：申请人先传电子件，财务后来扫到同一张纸质票 → 等于确认
            B = self.ok(self.post("/api/inv/later/create", "recv", dict(body, instId="PI-SELFUP-B")))["later"]["id"]
            j = self.ok(up(B, [xml(num(28003), 600)]))
            self.assertEqual((j["later"]["status"], j["later"]["selfAmount"]), ("open", 600.0))
            rc = self.ok(self.post("/api/inv/later/%d/receive" % B, "recv2", {"code": qr(num(28003), 600)}))
            self.assertEqual((rc["action"], rc["later"]["status"], rc["later"]["receivedAmount"], rc["later"]["selfCount"]),
                             ("confirm", "done", 600.0, 0))
            # 第三张单：没人点确认，但这张票审核通过了 → 也算已到
            C = self.ok(self.post("/api/inv/later/create", "recv", dict(body, instId="PI-SELFUP-C")))["later"]["id"]
            j = self.ok(up(C, [xml(num(28004), 600)]))
            iid = j["results"][0]["item"]["id"]
            self.assertEqual(self.ok(self.get("/api/inv/later/%d" % C, "recv2"))["later"]["status"], "open")
            S.item_update(e, iid, review="approved", review_by="acct", review_at=inv.now_s())
            lv = self.ok(self.get("/api/inv/later/%d" % C, "recv2"))["later"]
            self.assertEqual((lv["status"], lv["receivedAmount"], lv["selfCount"]), ("done", 600.0, 0))

    def test_29_later_messages_are_cards(self):
        """后补单的钉钉推送：每条消息既是纯文字（留痕、退路），又带一张钉钉标准卡片（标题条、红字、按钮）；
        卡片发不出去就给那个人补发文字。数据都是编的，钉钉全部 mock。"""
        from urllib.parse import quote as q
        inv, books, idt = self.inv, self.books, self.inv.idt
        today = date.today()
        l = {"id": 77, "business_id": "202610120000000029001", "payee_name": "卡片收款方有限公司", "pay_amount": 1322, "expect_amount": 1322,
             "received_amount": 0, "unregistered_amount": 0, "expect_date": (today - timedelta(days=4)).isoformat(), "inv_kind": "special",
             "applicant": "申请人庚", "receiver_name": "接收人乙", "receiver": "recv2"}
        md = lambda m: [c["text"] for c in m.card["contents"] if c["type"] == "markdown"]
        btn = lambda m: next((c["actions"][0] for c in m.card["contents"] if c["type"] == "action"), None)
        with patch.object(inv, "portal_link", lambda p: "http://wb.test" + p):
            a, cc, asg = books._remind_text(l, today), books._remind_cc_text(l, today), books._assign_text(l)
        # 文字版：干净的字，没有红字记号和加粗符号
        self.assertIsInstance(a, str)
        self.assertIn("预计到票：%s（已超期 4 天）" % l["expect_date"], a)
        self.assertIn("还差：¥1,322.00 的专票没到", a)
        for mark in ("\u27e6", "\u27e8", "\u27e7", "**", "<font"):
            self.assertNotIn(mark, a)
        # 卡片：标题条、超期和还差金额标红、按钮在钉钉里面打开后补登记页
        self.assertEqual(a.card["header"]["title"]["text"], "发票管家 · 催票提醒")
        rows = md(a)
        self.assertIn("<font color=common_red1_color>**（已超期 4 天）**</font>", rows[2])
        self.assertIn("<font color=common_red1_color>**¥1,322.00**</font>", rows[3])
        self.assertEqual(rows[4], "**发票到了请先上传，再把纸质的交给 接收人乙。**")
        b = btn(a)
        self.assertEqual((b["label"]["text"], b["actionType"]), ("上传发票", "openLink"))
        self.assertEqual(b["url"]["all"], "dingtalk://dingtalkclient/page/link?url=%s&pc_slide=true" % q("http://wb.test/#/invself", safe=""))
        self.assertFalse(any("上传：" in x for x in rows))                    # 有按钮了，卡片里不再重复写去处
        # 颜色跟着状态走：超期红、3 天内橙、还早不上色只加粗；已签收绿、等确认橙
        soon, far = dict(l, expect_date=(today + timedelta(days=2)).isoformat()), dict(l, expect_date=(today + timedelta(days=20)).isoformat())
        rs, rf = md(books._remind_text(soon, today)), md(books._remind_text(far, today))
        self.assertIn("<font color=common_orange1_color>**（还有 2 天）**</font>", rs[2])
        self.assertIn("<font color=common_orange1_color>**¥1,322.00**</font>", rs[3])
        self.assertEqual((rf[2], rf[3]), ("预计到票：%s（还有 20 天）" % far["expect_date"], "还差：**¥1,322.00** 的专票没到"))
        self.assertEqual(books._rich("共到票：" + books._tone("green", "¥1.00")), "共到票：<font color=common_green1_color>**¥1.00**</font>")
        self.assertEqual(books._plain("共到票：" + books._tone("green", "¥1.00") + "，" + books._tone("orange", "等确认")), "共到票：¥1.00，等确认")
        self.assertEqual(books._tone("pink", "x"), "x")                       # 不认识的颜色不上色，也不留记号
        # 给财务的：按钮用浏览器开后补池（不走钉钉内打开）
        self.assertEqual((btn(cc)["label"]["text"], btn(cc)["url"]["all"]), ("查看后补单", "http://wb.test/#/invlater?id=77"))
        self.assertEqual(asg.card["header"]["title"]["text"], "发票管家 · 有一张发票后补单交给你接收")
        # 不知道门户网址：不给按钮（给了也点不开），卡片最后一行写清楚在哪个页面
        with patch.object(inv, "portal_link", lambda p: p):
            a0, cc0 = books._remind_text(l, today), books._remind_cc_text(l, today)
        self.assertIsNone(btn(a0)); self.assertIsNone(btn(cc0))
        self.assertIn("钉钉工作台 →「%s」→ 发票后补单" % books.DT_APP, md(a0)[-1])
        self.assertIn("工作台 → 发票后补池 → 后补单 #77", md(cc0)[-1])
        self.assertNotIn("/#/", a0 + cc0)
        # 出口：消息带卡片就发卡片，普通文字还是发文字
        old = os.environ.pop("INV_DRY_SEND", None)
        try:
            with patch.object(idt, "send_card", MagicMock(return_value={"sent": True, "msg": "已发送", "via": "card"})) as sc, \
                    patch.object(idt, "send_text", MagicMock(return_value={"sent": True, "msg": "已发送"})) as stx:
                self.assertEqual(inv.notify_dt(["dt-x"], a)["via"], "card")
                self.assertEqual(sc.call_args[0], (["dt-x"], a.card, str(a)))
                stx.assert_not_called()
                inv.notify_dt(["dt-x"], "普通一句话")
                self.assertEqual(stx.call_args[0], (["dt-x"], "普通一句话"))
                self.assertEqual(sc.call_count, 1)
            # 卡片接口：发成了回 card；钉钉拒了（比如没权限）→ 给这个人补发文字
            resp = MagicMock()
            with patch.object(idt, "_load_conf", MagicMock(return_value={"appkey": "robot-k", "appsecret": "s"})), \
                    patch.object(idt.notifier, "_dt_v2_token", MagicMock(return_value="tok")), \
                    patch.object(idt.requests, "post", MagicMock(return_value=resp)) as post, \
                    patch.object(idt, "send_text", MagicMock(return_value={"sent": True, "msg": "已发送"})) as stx:
                resp.json.return_value = {"processQueryKey": "pk"}
                self.assertEqual(idt.send_card(["dt-x", "dt-x"], a.card, str(a)), {"sent": True, "msg": "已发送", "via": "card"})
                self.assertEqual(post.call_count, 1)                                # 同一个人只发一张
                sentb = json.loads(post.call_args[1]["data"].decode("utf-8"))
                self.assertEqual((sentb["cardTemplateId"], sentb["robotCode"], json.loads(sentb["singleChatReceiver"])), ("StandardCard", "robot-k", {"userId": "dt-x"}))
                self.assertEqual(json.loads(sentb["cardData"]), a.card)
                stx.assert_not_called()
                resp.json.return_value = {"code": "Forbidden.AccessDenied", "message": "没有调用该接口的权限"}
                r = idt.send_card(["dt-y"], a.card, str(a))
                self.assertEqual((r["sent"], r["via"]), (True, "text"))
                self.assertIn("改发了文字", r["msg"])
                self.assertEqual(stx.call_args[0], (["dt-y"], str(a)))
        finally:
            if old is not None:
                os.environ["INV_DRY_SEND"] = old

    def test_39_ticklist_by_book_month_and_paper_check(self):
        """认证跟着做账月走（用户 2026-10-14 定）：清单按做账月份出——3 月开、4 月才做账的票进 4 月的清单；还没做账的、别的月做账的不进。
        每次导出存成一份认证清单（能翻出来、能重新下载）；对纸质票可以对着一份清单：清单里的念「确认」，别的月做账的念「几月的」，
        还没做账的念「还没做账」。开票日期放在 2023-03/04（别的用例不用），号码、税号都是编的。"""
        OUR, TID = self.OUR, self.OUR_TID
        face = dict(amount=100, tax=6, total=106, buyer_name=OUR, buyer_tax_id=TID, seller_name="做账月销方", seller_tax_id="91tick0b")

        def book(iid, period, no):
            fl = dict(self.item(iid)["flags_json"] or {})
            fl["_bookkeeping"] = {"status": "booked", "source": "kingdee", "vouchers": [{"book": OUR, "period": period, "number": "记-%d" % no}]}
            self.S.item_update(self.e, iid, flags_json=fl)
        a, _ = self.make_item(num(39001), 106, "31", "20230310", face)          # 3 月开、4 月做账（记-3）
        b, _ = self.make_item(num(39002), 106, "31", "20230402", face)          # 4 月开、4 月做账（记-1）
        c, _ = self.make_item(num(39003), 106, "31", "20230320", face)          # 3 月开、3 月做账
        d, _ = self.make_item(num(39004), 106, "31", "20230405", face)          # 还没做账
        book(a, "2023-04", 3); book(b, "2023-04", 1); book(c, "2023-03", 7)
        url, rng = "/api/inv/deduct/tick/prepare", {"from": "2023-03-01", "to": "2023-04-30", "subject": TID}
        self.assertEqual(self.post(url, "acct", dict(rng, bookMonth="6月")).status_code, 400)
        # 不筛做账月份：照旧四张都进
        self.assertEqual(self.ok(self.post(url, "acct", rng))["summary"]["rows"], 4)
        # 4 月的清单：a、b 进；c 是别的月做账的、d 还没做账，各写一条原因
        j = self.ok(self.post(url, "acct", dict(rng, bookMonth="2023-04")))
        self.assertEqual(sorted(x["number"] for x in j["preview"]), [num(39001), num(39002)])
        self.assertEqual({x["key"]: x["count"] for x in j["summary"]["skipped"]}, {"unbooked": 1, "otherMonth": 1})
        pv = {x["number"]: x for x in j["preview"]}
        self.assertEqual(pv[num(39001)]["bookMonth"], "2023-04")
        self.assertTrue(pv[num(39001)]["voucher"].endswith("-3#"), pv[num(39001)]["voucher"])
        # V2.969 主体下拉：本公司抬头一家一行＋这个做账月份能进清单几张（不用开票日期）
        ou = self.ok(self.get("/api/inv/deduct/tick/options?bookMonth=2023-04", "acct"))
        sub = {x["taxId"]: x for x in ou["subjects"]}
        self.assertIn(TID, sub)
        self.assertGreaterEqual(sub[TID]["count"], 2)
        self.assertTrue(sub[TID]["short"])
        self.assertEqual(ou["total"], sum(x["count"] for x in ou["subjects"]))
        self.assertEqual(self.get("/api/inv/deduct/tick/options?bookMonth=6月", "acct").status_code, 400)
        self.assertEqual(self.get("/api/inv/deduct/tick/options?bookMonth=2023-04", "viewer").status_code, 403)
        # 不带开票日期也能按做账月份出
        j2 = self.ok(self.post(url, "acct", {"subject": TID, "bookMonth": "2023-04"}))
        self.assertTrue({num(39001), num(39002)} <= {x["number"] for x in j2["preview"]})
        # 下载＝存成一份认证清单；同一份再下一次不重复存
        n0 = len(self.ok(self.get("/api/inv/ticklists", "acct"))["rows"])
        for _ in range(2):
            r = self.get("/api/inv/deduct/download?token=%s" % j["token"], "acct")
            self.assertEqual((r.status_code, r.content[:2]), (200, b"PK"), r.text[:200])
        from urllib.parse import quote as _q
        self.assertIn(_q("增值税发票勾选认证-202304-acct.xlsx"), r.headers.get("content-disposition", ""))     # V2.972 文件名
        lists = self.ok(self.get("/api/inv/ticklists", "acct"))["rows"]
        self.assertEqual(len(lists), n0 + 1)
        lst = lists[0]
        self.assertEqual((lst["bookMonth"], lst["rows"], lst["items"], lst["by"], lst["subject"]), ("2023-04", 2, 2, "acct", TID))
        self.assertIn("2023-04 认证清单", lst["name"])
        r = self.get("/api/inv/ticklists/%d/download" % lst["id"], "acct")
        self.assertEqual((r.status_code, r.content[:2]), (200, b"PK"))
        self.assertEqual(self.get("/api/inv/ticklists/%d/download" % lst["id"], "viewer").status_code, 403)
        self.assertEqual(self.get("/api/inv/ticklists/999999/download", "acct").status_code, 404)
        # 对着这份清单对纸质票
        sys.modules.pop("routers.invoice_check", None)
        from routers import invoice_check as ck_
        u = {"name": "acct"}
        v, bad = ck_.start_list(u, lst["id"], "ledger")
        self.assertIsNone(bad)
        self.assertEqual((v["check"]["listId"], v["check"]["listName"], v["folders"][0]["isList"], v["stats"]["total"]), (lst["id"], lst["name"], True, 2))
        self.assertEqual([i["id"] for i in v["items"]], [b, a])               # 按凭证号排：记-1 在记-3 前面
        self.assertTrue(v["items"][0]["voucher"].endswith("-1#"))
        ck = self.S.check_get(self.e, v["check"]["id"])
        r = ck_.scan(u, ck, qr(num(39001), 106, "31", "20230310"), "scanner")
        self.assertEqual((r["result"], r["say"], r["stats"]), ("ok", "确认", {"total": 2, "hit": 1, "left": 1}))
        self.assertEqual((r["show"]["tone"], r["show"]["big"]), ("ok", "这一批"))           # V2.971 电脑上的大卡片
        r = ck_.scan(u, ck, qr(num(39003), 106, "31", "20230320"), "phone")   # 同主体、3 月做账的：念「3 月」
        self.assertEqual((r["result"], r["say"], r["show"]["big"]), ("other", "3 月", "3 月"))
        self.assertIn("2023-03 做账的", r["msg"])
        r = ck_.scan(u, ck, qr(num(39004), 106, "31", "20230405"), "phone")   # 还没做账
        self.assertEqual((r["result"], r["say"]), ("other", "没做账"))
        r = ck_.scan(u, ck, qr(num(39099), 106, "31", "20230405"), "phone")
        self.assertEqual((r["result"], r["say"], r["show"]["tone"]), ("none", "没有这张", "bad"))
        # V2.971 先判主体：别的主体的票，就算同一个月做账也念「主体，几月」
        oth, _ = self.make_item(num(39005), 106, "31", "20230402", dict(face, buyer_name="测试别家主体有限公司", buyer_tax_id="91OTHERSUBJ0X"))
        book(oth, "2023-04", 9)
        r = ck_.scan(u, ck, qr(num(39005), 106, "31", "20230402"), "phone")
        self.assertEqual(r["result"], "other")
        self.assertTrue(r["say"].endswith("，4 月"), r["say"])
        self.assertIn("不是这个主体", r["msg"])
        # 同主体同月、但没进这份清单（之前导出过）→ 说原因
        sm, _ = self.make_item(num(39006), 106, "31", "20230403", face)
        book(sm, "2023-04", 11)
        self.S.item_update(self.e, sm, deduct_status="marked")
        r = ck_.scan(u, ck, qr(num(39006), 106, "31", "20230403"), "phone")
        self.assertEqual((r["result"], r["say"]), ("other", "导出过"))
        self.S.item_update(self.e, sm, deduct_status=None)
        r = ck_.scan(u, ck, qr(num(39006), 106, "31", "20230403"), "phone")
        self.assertEqual((r["result"], r["say"]), ("other", "不在清单"))
        out, bad = ck_.mark(u, ck, c, True)                                    # 不在清单里的不能手工勾
        self.assertIsNotNone(bad)
        v2 = ck_.view(self.S.check_get(self.e, ck["id"]))
        self.assertEqual((v2["stats"]["hit"], v2["stats"]["strangers"], [x.get("bookMonth") for x in v2["strangers"][:2]]), (1, 6, ["2023-03", ""]))
        self.assertEqual((v2["last"]["show"]["big"], v2["last"]["viaLabel"]), ("不在清单", "手机"))   # 电脑上据此弹手机扫的那张
        sys.modules.pop("routers.invoice_check", None)

    def test_27_tax_tick_type(self):
        """票种的税局写法：有二维码票种代码就用官方名（半角括号），没有就按票种＋是不是数电票推。"""
        t = self.books.tax_tick_type
        n20 = num(27001)
        self.assertEqual(t({"qr_type": "31", "inv_type": "special", "number": n20}), "电子发票(增值税专用发票)")
        self.assertEqual(t({"qr_type": "61", "inv_type": "flight", "number": n20}), "电子发票(航空运输电子客票行程单)")
        self.assertEqual(t({"qr_type": "32", "inv_type": "travel", "number": n20}), "电子发票(普通发票)")
        self.assertEqual(t({"qr_type": "01", "inv_type": "special", "number": "12345678", "code": "044001900111"}), "增值税专用发票")
        self.assertEqual(t({"qr_type": "", "inv_type": "special", "number": n20}), "电子发票(增值税专用发票)")
        self.assertEqual(t({"inv_type": "special", "number": "12345678", "code": "044001900111"}), "增值税专用发票")
        self.assertEqual(t({"inv_type": "train", "number": n20}), "电子发票(铁路电子客票)")
        self.assertEqual(t({"inv_type": "travel", "number": n20}), "电子发票(普通发票)")

    def test_30_other_system_list(self):
        """过渡期票总管、发票管家并行：把票总管导出的清单传进来，系统记住；票号对得上的票自动记「票总管已有」——
        现在已有的马上记，以后接进来的（钉钉自动接入的也一样）一送审就记；有记号的出勾选清单不带。
        人工取消过的不再自动记；会计判了不抵扣的、之前已从这边出过清单的不记，只列出来。
        开票日期都放在 2024-03，号码、税号都是编的。"""
        S, e = self.S, self.e
        OUR, TID, OTHER = self.OUR, self.OUR_TID, self.OTHER_TID
        face = dict(amount=100, tax=6, total=106, buyer_name=OUR, buyer_tax_id=TID, seller_name="过渡销方甲", seller_tax_id="91both0a")
        a, _ = self.make_item(num(30001), 106, "31", "20240302", face)                             # 两边都有、已审核：记上
        b, _ = self.make_item(num(30002), 106, "31", "20240303", face)                             # 只在发票管家
        c, _ = self.make_item(num(30003), 106, "31", "20240304", face, approve=False)               # 两边都有、还没审核：也记上
        d, _ = self.make_item(num(30004), 106, "31", "20240305", face, decisions={"deductible": False})   # 两边都有、这边判不抵扣：不记
        g, _ = self.make_item(num(30005), 106, "31", "20240306", face)                             # 两边都有、之前已从这边出过清单：不记，提醒
        S.item_update(e, g, deduct_status="marked")
        lst = lambda nums, tid=TID: ("增值税发票勾选抵扣(默认)_20240331.xlsx", self.ie.export_tax_tick([
            {"number": n, "date": "2024-03-01", "amount": 106, "tax": 6, "buyerTaxId": tid, "sellerName": "过渡销方甲",
             "taxType": "电子发票(增值税专用发票)"} for n in nums]), "application/octet-stream")
        imp = lambda f, user="acct": self.upload("/api/inv/other/import", user, [f], "file")
        st = lambda *ids: [self.item(i)["deduct_status"] or "" for i in ids]
        nums = [num(30001), num(30003), num(30004), num(30005), num(30099), num(30098), num(30097)]   # 后三张现在只在票总管
        self.assertEqual(imp(lst(nums), "viewer").status_code, 403)
        r = imp(("乱写的.xlsx", b"not an excel", "application/octet-stream"))
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("票总管清单没读出来", r.json()["msg"])
        self.assertEqual(self.ok(self.get("/api/inv/other", "viewer"))["count"], 0)
        j = self.ok(imp(lst(nums)))
        self.assertEqual((j["rows"], j["keys"], j["added"], j["total"], j["both"], j["marked"], j["warnings"]), (7, 7, 7, 7, 4, 2, []), j)
        self.assertEqual({k: v for k, v in j["counts"].items() if v}, {"new": 2, "notDeduct": 1, "marked": 1})
        self.assertEqual({x["number"]: (x["state"], x["approved"]) for x in j["list"]},
                         {num(30001): ("new", True), num(30003): ("new", False), num(30004): ("notDeduct", True), num(30005): ("marked", True)})
        self.assertEqual(st(a, b, c, d, g), ["other", "", "other", "", "marked"])
        logs = S.logs_of(e, item_id=c)
        self.assertTrue(any(x["action"] == "记为票总管已有" and "票总管清单" in json.dumps(x, ensure_ascii=False, default=str) for x in logs), logs)
        s0 = self.ok(self.get("/api/inv/other", "viewer"))
        self.assertEqual((s0["count"], s0["marked"], s0["lastBy"]), (7, 2, "acct"))
        # 同一份再传一遍：不重复记
        j = self.ok(imp(lst(nums)))
        self.assertEqual((j["added"], j["marked"], j["counts"]["flagged"], j["counts"]["new"]), (0, 0, 2, 0))
        # 出勾选清单：有记号的不带（不用再拖什么文件）
        url, rng = "/api/inv/deduct/tick/prepare", {"from": "2024-03-01", "to": "2024-03-31", "subject": TID}
        j = self.ok(self.post(url, "acct", rng))
        self.assertEqual([x["number"] for x in j["preview"]], [num(30002)])
        self.assertEqual([(x["key"], x["count"]) for x in j["summary"]["skipped"]], [("inOther", 1), ("notApproved", 1), ("marked", 1)])
        # 以后接进来的票（票号在清单里）：一送审就自动记上，审核的人看到的就是勾好的；批量通过也不会丢
        h, fh = self.make_item(num(30099), 106, "31", "20240307", face, approve=False)
        self.assertEqual(st(h), ["other"])
        self.ok(self.post("/api/inv/audit/approve", "acct", {"folderId": fh, "itemIds": [h]}))       # 不带这张的勾选决定
        self.assertEqual((st(h), self.item(h)["review"]), (["other"], "approved"))
        # 审核的人把系统记的号取消了：这张以后不再自动记
        k, fk = self.make_item(num(30098), 106, "31", "20240308", face, approve=False)
        self.assertEqual(st(k), ["other"])
        self.ok(self.post("/api/inv/audit/approve", "acct", {"folderId": fk, "itemIds": [k],
                                                             "decisions": {str(k): {"deductible": True, "otherSystem": False}}}))
        self.assertEqual(st(k), [""])
        self.assertEqual((self.books.other_apply(), st(k)), (0, [""]))
        # 没送审就先放着的票：审核通过前、出清单前也会对一次
        m, fm = self.make_item(num(30097), 106, "31", "20240309", face, approve=False, submit=False)
        self.assertEqual(st(m), [""])
        j = self.ok(self.post(url, "acct", rng))
        self.assertEqual(st(m), ["other"])
        self.assertEqual(sorted(x["number"] for x in j["preview"]), [num(30002), num(30098)])
        # 台账里手工取消：不再自动记；再传清单时列为「手工取消过」；重新记上后恢复
        u = lambda i: "/api/inv/item/%d/other-system" % i
        self.ok(self.post(u(a), "acct", {"on": False}))
        self.assertEqual((self.books.other_apply(), st(a)), (0, [""]))
        j = self.ok(imp(lst(nums)))
        self.assertEqual(({x["number"]: x["state"] for x in j["list"]}[num(30001)], j["marked"], st(a)), ("off", 0, [""]))
        self.ok(self.post(u(a), "acct", {"on": True}))
        self.assertEqual(st(a), ["other"])
        self.ok(self.post(u(a), "acct", {"on": False}))
        S.other_set_off(e, self.item(a)["dup_key"], 0)
        self.assertEqual((self.books.other_apply(), st(a)), (1, ["other"]))
        # 传错了别家公司的清单：提醒
        j = self.ok(imp(lst([num(30096)], OTHER)))
        self.assertIn("不在本公司抬头清单里", j["warnings"][0])

    def test_31_other_system_mark(self):
        """过渡期「票总管已有」记号：审核时逐张勾 → 出勾选清单不带、台账里看得到、能按它筛；台账里事后能补记 / 取消；
        方式二在税局未勾选清单上标注时也不替它标「是」。开票日期都放在 2024-05，号码、税号都是编的。"""
        OUR, TID = self.OUR, self.OUR_TID
        face = dict(amount=100, tax=6, total=106, buyer_name=OUR, buyer_tax_id=TID, seller_name="过渡销方乙", seller_tax_id="91mark0a")
        a, fa = self.make_item(num(31001), 106, "31", "20240502", face, approve=False)             # 审核时勾「票总管已有」
        b, fb = self.make_item(num(31002), 106, "31", "20240503", face, approve=False)             # 审核时不勾
        self.ok(self.post("/api/inv/audit/approve", "acct", {"folderId": fa, "itemIds": [a],
                                                             "decisions": {str(a): {"deductible": True, "otherSystem": True}}}))
        self.ok(self.post("/api/inv/audit/approve", "acct", {"folderId": fb, "itemIds": [b],
                                                             "decisions": {str(b): {"deductible": True, "otherSystem": False}}}))
        st = lambda *ids: [self.item(i)["deduct_status"] or "" for i in ids]
        self.assertEqual((st(a, b), self.item(a)["deductible"]), (["other", ""], 1))              # 可抵扣照常判，只是勾选归票总管
        rng = {"from": "2024-05-01", "to": "2024-05-31", "subject": TID}
        j = self.ok(self.post("/api/inv/deduct/tick/prepare", "acct", rng))
        self.assertEqual(([x["number"] for x in j["preview"]], [(x["key"], x["count"]) for x in j["summary"]["skipped"]]),
                         ([num(31002)], [("inOther", 1)]))
        # 台账：显示这个记号、能按它筛、导出的 Excel 里也写
        led = self.ok(self.get("/api/inv/ledger?from=2024-05-01&to=2024-05-31&deduct=other", "viewer"))
        self.assertEqual([(r["id"], r["deductStatus"]) for r in led["rows"]], [(a, "other")])
        self.assertEqual(self.ie.DEDUCT_STATUS_LABELS["other"], "票总管已有")
        # 台账里事后取消 / 补记：要抵扣勾选权限
        u = lambda i: "/api/inv/item/%d/other-system" % i
        self.assertEqual(self.post(u(a), "viewer", {"on": False}).status_code, 403)
        self.assertEqual(self.post(u(999999), "acct", {"on": True}).status_code, 404)
        r = self.ok(self.post(u(a), "acct", {"on": False}))
        self.assertEqual((r["changed"], r["item"]["deductStatus"], st(a)), (1, "", [""]))
        r = self.ok(self.post(u(b), "acct", {"on": True}))
        self.assertEqual((r["changed"], r["item"]["deductStatus"], st(b)), (1, "other", ["other"]))
        self.assertEqual(self.ok(self.post(u(b), "acct", {"on": True}))["changed"], 0)             # 再点一次不重复记
        self.assertTrue(any(x["action"] == "记为票总管已有" for x in self.S.logs_of(self.e, item_id=b)))
        self.assertTrue(any(x["action"] == "取消票总管已有" for x in self.S.logs_of(self.e, item_id=a)))
        j = self.ok(self.post("/api/inv/deduct/tick/prepare", "acct", rng))
        self.assertEqual([x["number"] for x in j["preview"]], [num(31001)])
        # 税局已显示勾选过的：不让改
        self.S.item_update(self.e, a, deduct_status="checked")
        r = self.post(u(a), "acct", {"on": True})
        self.assertEqual(r.status_code, 400)
        self.assertIn("勾选过", r.json()["msg"])
        # 方式二（在税局未勾选清单上标注）：有记号的不替它标「是」
        data = xlsx(["发票号码", "开票日期", "销方名称", "金额", "税额", "价税合计", "是否勾选"],
                    [[num(31002), "2024-05-03", "过渡销方乙", 100, 6, 106, ""]])
        j = self.ok(self.upload("/api/inv/deduct/prepare", "acct", [("未勾选.xlsx", data, "application/octet-stream")], "file"))
        self.assertEqual((j["summary"]["yes"], j["preview"][0]["decision"] or ""), (0, ""), j["preview"])
        self.assertIn("票总管已有", j["preview"][0]["reason"])

    def test_32_audit_means_paper_received(self):
        """审核＝纸质票已收到（用户 2026-10-13 定）：单张点「审核」（弹窗右上角或已收发票表上，同一个接口）、整单审核通过，
        都把还没到的纸质件记为已到并留痕；记疑问、清标记不算。号码是编的。"""
        S, e = self.S, self.e
        face = dict(buyer_name=self.OUR, buyer_tax_id=self.OUR_TID, origin="attachment", paper=0)
        a, fa = self.make_item(num(32001), 106, "31", "20240602", face, approve=False)
        b, fb = self.make_item(num(32002), 106, "31", "20240603", face, approve=False)
        mark = lambda i, body: self.post("/api/inv/audit/item/%d/mark" % i, "acct", body)
        self.ok(mark(a, {"mark": "doubt", "text": "金额对不上"}))
        self.assertEqual(self.item(a)["paper"], 0)
        self.ok(mark(a, {"mark": "clear"}))
        self.assertEqual(self.item(a)["paper"], 0)
        r = self.ok(mark(a, {"mark": "ok"}))
        self.assertEqual((bool(r["item"]["paper"]), self.item(a)["paper"], bool(r["item"]["auditOk"])), (True, 1, True))
        got = [x for x in S.logs_of(e, item_id=a) if x["action"] == "纸质件到件（审核时确认）"]
        self.assertEqual(len(got), 1)
        self.ok(mark(a, {"mark": "ok"}))                                                    # 再点一次不重复留痕
        self.assertEqual(len([x for x in S.logs_of(e, item_id=a) if x["action"] == "纸质件到件（审核时确认）"]), 1)
        # 整单审核通过：没单张点过「审核」的票也记
        self.ok(self.post("/api/inv/audit/approve", "acct", {"folderId": fb, "itemIds": [b]}))
        self.assertEqual((self.item(b)["review"], self.item(b)["paper"]), ("approved", 1))
        self.assertTrue(any(x["action"] == "纸质件到件（审核时确认）" for x in S.logs_of(e, item_id=b)))
        # 只审其中一两张：这张勾的「可抵扣」「票总管」当场存下（过几天回来接着审不会丢）；整单通过时没再带判定的沿用已存的，不被系统建议盖掉
        c, fc = self.make_item(num(32003), 106, "31", "20240604", face, approve=False)
        d, _ = self.S.item_insert(e, folder_id=fc, kind="invoice", origin="attachment", inv_type="special", number=num(32004),
                                  dup_key="N:" + num(32004), total=50, tax=3, deduct_suggest="yes", review="pending", proc_status="done"), None
        r = self.ok(mark(c, {"mark": "ok", "deductible": False, "otherSystem": True}))
        self.assertEqual((self.item(c)["deductible"], self.item(c)["deduct_status"], self.item(c)["review"]), (0, "other", "pending"))
        self.assertEqual((r["item"]["deductible"], r["item"]["deductStatus"]), (False, "other"))
        self.assertEqual((self.item(d)["deductible"], self.item(d)["review"]), (None, "pending"))      # 没审的那张原样
        self.assertEqual(S.folder_get(e, fc)["status"], "submitted")                                    # 单子还在待审核
        self.ok(self.post("/api/inv/audit/approve", "acct", {"folderId": fc, "itemIds": [c, d], "gapNote": "测试：只为走通"}))
        self.assertEqual((self.item(c)["deductible"], self.item(c)["deduct_status"], self.item(d)["deductible"]), (0, "other", 1))
        # 审核过的票不再亮「纸质件还没到」
        self.ok(self.post("/api/inv/audit/approve", "acct", {"folderId": fa, "itemIds": [a]}))
        an, _ = self.inv.folder_anomalies(S.folder_get(e, fa), S.folder_items(e, fa), "done")
        self.assertNotIn("paper", [x["code"] for x in an])

    def test_33_approve_invoice_one_by_one(self):
        """审一张过一张（用户 2026-10-13 定）：勾的票当场审核通过、进台账、纸质票记已到，待核字段一并确认，付款单留在待审核；
        最后一张也过了、金额一致 → 整单自动结掉（非发票附件跟着过）；金额有差额的票照样能过但整单不自动结，结单时写的差额说明要记下；
        记了存疑的不能过（一起勾的也不过）。号码是编的。"""
        S, e = self.S, self.e
        OUR, TID = self.OUR, self.OUR_TID
        face = dict(amount=100, tax=6, total=106, buyer_name=OUR, buyer_tax_id=TID, origin="attachment", paper=0)
        fo = self.ok(self.post("/api/inv/folder/manual", "intern", {"title": "逐张审的付款单", "amount": 318, "company": OUR}))["folder"]
        ids = []
        for n in (33001, 33002, 33003):
            s = self.ok(self.post("/api/inv/desk/scan", "intern", {"code": qr(num(n), 106, "31", "20240702")}))
            S.item_update(e, s["item"]["id"], **face)
            ids.append(s["item"]["id"])
        a, b, c = ids
        att = S.item_insert(e, folder_id=fo["id"], kind="other", origin="attachment", proc_status="done")
        self.ok(self.post("/api/inv/folder/%d/submit" % fo["id"], "intern"))
        url = "/api/inv/audit/items/approve"
        self.assertEqual(self.post(url, "viewer", {"folderId": fo["id"], "itemIds": [a]}).status_code, 403)
        self.assertEqual(self.post(url, "acct", {"folderId": fo["id"], "itemIds": []}).status_code, 400)
        # 只审第 1 张：当场通过、进台账；这张勾的「不可抵」「票总管」存下；别的票和整张单不动
        S.item_update(e, a, pending_json=["sellerName"])
        r = self.ok(self.post(url, "acct", {"folderId": fo["id"], "itemIds": [a],
                                            "decisions": {str(a): {"deductible": False, "otherSystem": True}}}))
        self.assertEqual((r["folderDone"], [i["id"] for i in r["items"]], r["items"][0]["review"]), (False, [a], "approved"))
        ia = self.item(a)
        self.assertEqual((ia["review"], ia["deductible"], ia["deduct_status"], ia["paper"], ia["pending_json"] or []),
                         ("approved", 0, "other", 1, []))
        self.assertEqual((self.item(b)["review"], S.folder_get(e, fo["id"])["status"]), ("pending", "submitted"))
        led = self.ok(self.get("/api/inv/ledger?from=2024-07-01&to=2024-07-31", "viewer"))
        self.assertEqual([x["id"] for x in led["rows"]], [a])                       # 审过的这张已经在台账里，没审的不在
        self.assertTrue(any(x["action"] == "审核通过" for x in S.logs_of(e, item_id=a)))
        self.assertEqual(self.post(url, "acct", {"folderId": fo["id"], "itemIds": [a]}).status_code, 400)   # 审过的再点：提示刷新
        # 记了存疑的不能过，一起勾的也不过
        self.ok(self.post("/api/inv/audit/item/%d/mark" % b, "acct", {"mark": "doubt", "text": "金额不对"}))
        r = self.post(url, "acct", {"folderId": fo["id"], "itemIds": [b, c]})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("存疑", r.json()["msg"])
        self.assertEqual(self.item(c)["review"], "pending")
        self.ok(self.post("/api/inv/audit/item/%d/mark" % b, "acct", {"mark": "clear"}))
        # 剩下两张一起过：票都过完、金额一致 → 整单自动结掉，非发票附件跟着过
        r = self.ok(self.post(url, "acct", {"folderId": fo["id"], "itemIds": [b, c]}))
        self.assertEqual((r["folderDone"], r["folder"]["status"]), (True, "approved"))
        self.assertEqual((S.folder_get(e, fo["id"])["status"], S.item_get(e, att)["review"], self.item(b)["deductible"]),
                         ("approved", "approved", 1))
        # 金额有差额的单：票照样一张张过，但整单不自动结；结单时写的差额说明记下
        g, fg = self.make_item(num(33004), 106, "31", "20240703", face, approve=False)
        S.folder_update(e, fg, amount=200)
        r = self.ok(self.post(url, "acct", {"folderId": fg, "itemIds": [g]}))
        self.assertEqual((r["folderDone"], self.item(g)["review"], S.folder_get(e, fg)["status"]), (False, "approved", "submitted"))
        self.ok(self.post("/api/inv/audit/approve", "acct", {"folderId": fg, "itemIds": [], "gapNote": "多付的下月抵"}))
        f2 = S.folder_get(e, fg)
        self.assertEqual(f2["status"], "approved")
        self.assertIn("差额说明", f2["review_note"] or "")
        self.assertIn("多付的下月抵", f2["review_note"] or "")

    def test_34_scan_invoice_tells_voucher_number(self):
        """扫发票，报凭证号（用户 2026-10-13 要：纸质发票是原始凭证，贴票时要知道它在哪张凭证里）。
        发票管家里已经对上的直接报、不去问金蝶；没对上的去金蝶现查一次并顺手记到票上；没登记过的票按摘要里的发票号也查得到；
        扫的不是发票、金蝶查不了，都说清楚。号码和凭证都是编的。"""
        V = self.vouchers
        url = "/api/inv/vouchers/scan"
        a, _ = self.make_item(num(34001), 106, "31", "20240802", dict(buyer_name="测试公司", buyer_tax_id=self.OUR_TID))
        row = lambda n, no, book="测试公司会计账簿", extra="": {"book": book, "year": 2024, "month": 8, "group": "记", "number": no,
                                                            "summary": n + "支付某某报销差旅费" + extra}
        self.assertEqual(self.c.post(url, json={"code": num(34001)}).status_code, 401)
        # 没对上过：去金蝶查一次，报出来，并记到这张票上
        with patch.object(V, "fetch_rows", return_value=[row(num(34001), 230), row(num(34001), 230)]) as fr:
            j = self.ok(self.post(url, "viewer", {"code": qr(num(34001), 106, "31", "20240802")}))
            self.assertEqual(fr.call_count, 1)
        self.assertEqual((j["action"], j["inLedger"], j["vouchers"], j["say"]),
                         ("found", True, [{"book": "测试公司会计账簿", "period": "2024-08", "number": "记-230"}], "8 月，记 230 号"))
        self.assertEqual((j["item"]["id"], j["item"]["reviewText"], j["item"]["total"]), (a, "已审核", 106))
        self.assertEqual(self.item(a)["flags_json"]["_bookkeeping"]["vouchers"][0]["number"], "记-230")
        # 已经对上的：直接报，不再去问金蝶；直接敲发票号码也行
        with patch.object(V, "fetch_rows", side_effect=AssertionError("不该再查金蝶")):
            j = self.ok(self.post(url, "viewer", {"code": " " + num(34001) + " "}))
        self.assertEqual((j["action"], j["say"]), ("found", "8 月，记 230 号"))
        # 发票管家里没登记过的票：按摘要里的发票号也查得到（两张凭证都报）
        with patch.object(V, "fetch_rows", return_value=[row(num(34009), 12, "别的账簿"), row(num(34009), 391), row(num(34009) + "1", 5)]):
            j = self.ok(self.post(url, "viewer", {"code": num(34009)}))
        self.assertEqual((j["action"], j["inLedger"], [v["number"] for v in j["vouchers"]], "item" in j), ("found", False, ["记-12", "记-391"], False))
        self.assertTrue(j["say"].startswith("有 2 张凭证"))
        # 哪儿都没有
        with patch.object(V, "fetch_rows", return_value=[]):
            j = self.ok(self.post(url, "viewer", {"code": num(34008)}))
        self.assertEqual((j["action"], j["say"], j["vouchers"]), ("none", "没找到凭证", []))
        self.assertIn("没有登记这张票", j["msg"])
        # 扫的不是发票
        j = self.ok(self.post(url, "viewer", {"code": "https://aflow.dingtalk.com/qr/abc"}))
        self.assertEqual((j["action"], j["say"]), ("notInvoice", "这是审批单"))
        self.assertEqual(self.ok(self.post(url, "viewer", {"code": "随便打的字"}))["action"], "notInvoice")
        # 金蝶查不了：不报错，照实说
        b, _ = self.make_item(num(34002), 106, "31", "20240803", dict(buyer_name="测试公司", buyer_tax_id=self.OUR_TID))
        with patch.object(V, "fetch_rows", side_effect=RuntimeError("超时")):
            j = self.ok(self.post(url, "viewer", {"code": num(34002)}))
        self.assertEqual((j["action"], j["inLedger"]), ("none", True))
        self.assertIn("金蝶现在查不了", j["msg"])
        # 「凭证装订」扫码页用的样子：和扫付款单同一套字段（主体简称、凭证号不带「记-」、凭证月份）；扫的不是发票 → ok False
        bv = V.bind_view(V._scan(num(34001)), {"测试公司会计账簿": "测试主体"})
        self.assertEqual((bv["ok"], bv["is_invoice"], bv["inst"], bv["subject"], bv["payee"], bv["amount"]),
                         (True, True, "发票:" + num(34001), "测试主体", self.item(a)["seller_name"] or "", 106))
        self.assertEqual([(v["subject"], v["month"], v["vno"]) for v in bv["vouchers"]], [("测试主体", "2024-08", "230")])
        self.assertEqual((bv["invoice"]["number"], bv["invoice"]["inLedger"], bv["invoice"]["reviewText"]), (num(34001), True, "已审核"))
        with patch.object(V, "fetch_rows", return_value=[]):
            bv = V.bind_view(V._scan(num(34008)))
        self.assertEqual((bv["ok"], bv["vouchers"], bv["state"]), (True, [], "这张发票还没做账"))
        self.assertEqual(V.bind_view(V._scan("随便打的字"))["ok"], False)

    def test_35_late_invoice_in_dingtalk_flow_goes_to_later(self):
        """财务初审这条线（用户 2026-10-13 定）：钉钉流程里的发票，财务初审人点同意之前就有的算这张单的正常票；
        之后才发到评论区的算后补票——记到这张单没收齐的后补单上、进发票审核，审核通过才算已到。
        以前就取进来、当时没分的老票，再看一次流程时补分。人名、单号、发票都是编的。"""
        inv, S, e = self.inv, self.S, self.e
        from routers import invoice_intake as intake
        f = self.tip.FAKE

        def op(uid, typ, res, at):
            return {"userid": uid, "operation_type": typ, "operation_result": res, "date": at}
        # ① 纯判断：初审人退回重审过 → 以最后一次同意为准；他名下还有待办＝没过；流程没经过他 → 看接入审批人；都没有 → 看发票管家审没审
        st = {"intake": {"firstReviewers": [{"dtUserid": "dt-first"}], "approvers": [{"dtUserid": "dt-gate"}]}}
        ops = [op("dt-late", "START_PROCESS_INSTANCE", "NONE", "2026-09-20 13:23:00"),
               op("dt-first", "EXECUTE_TASK_NORMAL", "AGREE", "2026-09-29 15:25:00"),
               op("dt-x", "REDIRECT_PROCESS", "NONE", "2026-09-29 15:58:00"),
               op("dt-first", "EXECUTE_TASK_NORMAL", "AGREE", "2026-10-12 13:51:00"),
               op("dt-late", "ADD_REMARK", "NONE", "2099-01-01 10:00:00")]
        inst = {"operation_records": ops, "tasks": []}
        fr = inv.first_review(inst, None, st)
        self.assertEqual(fr, {"passed": True, "at": "2026-10-12 13:51:00", "by": "first"})
        self.assertFalse(inv.first_review(dict(inst, tasks=[{"userid": "dt-first", "task_status": "RUNNING"}]), None, st)["passed"])
        gate = {"operation_records": [op("dt-gate", "EXECUTE_TASK_NORMAL", "AGREE", "2026-10-01 09:00:00")], "tasks": []}
        self.assertEqual(inv.first_review(gate, None, st), {"passed": True, "at": "2026-10-01 09:00:00", "by": "approver"})
        other = {"operation_records": [op("dt-x", "EXECUTE_TASK_NORMAL", "AGREE", "2026-10-01 09:00:00")], "tasks": []}
        self.assertFalse(inv.first_review(other, {"status": "collecting"}, st)["passed"])
        self.assertEqual(inv.first_review(other, {"status": "approved", "reviewed_at": "2026-10-05 10:00:00"}, st),
                         {"passed": True, "at": "2026-10-05 10:00:00", "by": "audit"})
        self.assertEqual(inv._late_info({"source": "dingtalk_comment", "at": "2099-01-01 10:00:00"}, fr),
                         {"at": "2099-01-01 10:00:00", "after": "2026-10-12 13:51:00", "by": "first"})
        self.assertIsNone(inv._late_info({"source": "dingtalk_comment", "at": "2026-10-11 10:00:00"}, fr))   # 初审前发的
        self.assertIsNone(inv._late_info({"source": "dingtalk_form", "at": "2099-01-01 10:00:00"}, fr))      # 表单里带的
        # ② 设置里存财务初审人：去重，接入审批人和起始日不动
        before = self.ok(self.get("/api/inv/settings", "cfg"))["settings"]["intake"]
        self.ok(self.post("/api/inv/settings", "cfg", {"settings": {"intake": {
            "firstReviewers": [{"dtUserid": "dt-first", "dtName": "初审甲"}, {"dtUserid": "dt-first", "dtName": "重复的"}]}}}))
        got = self.ok(self.get("/api/inv/settings", "cfg"))["settings"]["intake"]
        self.assertEqual(got["firstReviewers"], [{"dtUserid": "dt-first", "dtName": "初审甲"}])
        self.assertEqual((got["approvers"], got["since"]), (before["approvers"], before["since"]))
        # ③ 一张付款单 900：表单里带 300 的票（初审前就有），后补单等 600；初审通过后评论区来了那张 600 的
        norm = dict(instId="PI-LATE-1", businessId="202610139900000000035", applicant="申请人辛", applicantUid="dt-late",
                    payeeName=f["seller"], amount=900.0, hasAttachments=True)
        S.folder_upsert_from_approval(e, self._norm(**norm), "recv", "later")
        fid = S.folder_by_inst(e, "PI-LATE-1")["id"]
        lid = self.later_row(inst_id="PI-LATE-1", folder_id=fid, payee_name=f["seller"], pay_amount=900, expect_amount=600)
        blobs = {"F-FORM": einv_xml(num(35001), f["seller"], f["seller_tid"], f["buyer"], f["buyer_tid"], 283.02, 16.98, 300),
                 "F-LATE": einv_xml(num(35002), f["seller"], f["seller_tid"], f["buyer"], f["buyer_tid"], 566.04, 33.96, 600),
                 "F-OLD": einv_xml(num(35003), f["seller"], f["seller_tid"], f["buyer"], f["buyer_tid"], 471.70, 28.30, 500)}
        a_form = {"fileId": "F-FORM", "fileName": "表单里的票.xml", "source": "dingtalk_form"}
        a_late = {"fileId": "F-LATE", "fileName": "评论区的票.xml", "source": "dingtalk_comment", "at": "2099-01-01 10:00:00"}
        a_old = {"fileId": "F-OLD", "fileName": "早先取过的票.xml", "source": "dingtalk_comment", "at": "2099-01-01 10:00:00"}
        flow = {"PI-LATE-1": (inst, [a_form])}

        def fake_get(iid):
            return {"ok": True, "inst": dict(flow.get(str(iid), ({}, []))[0], _iid=str(iid)), "msg": ""}

        def fake_norm(x, iid=None, tpl=None):
            return self._norm(instId=str(iid), attachments=list(flow.get(str(iid), ({}, []))[1]), hasAttachments=True)

        def by_num(n):
            return next(i for i in S.folder_items(e, fid) if i.get("number") == n)
        with patch.object(inv.idt, "get_instance", MagicMock(side_effect=fake_get)), \
                patch.object(inv.idt, "normalize_instance", MagicMock(side_effect=fake_norm)), \
                patch.object(inv.idt, "download_attachment", MagicMock(side_effect=lambda i, a, x=None: {
                    "ok": True, "bytes": blobs[a["fileId"]], "via": "", "msg": ""})), \
                patch.object(inv, "notify_dt", MagicMock(return_value={"sent": True, "msg": "已发送"})), \
                patch.object(intake, "RECHECK_MAX", 10000):
            # 头一回取附件：只有表单里那张 → 正常票，不沾后补单
            S.folder_update(e, fid, attach_status="pending")
            inv.worker_step()
            a = by_num(num(35001))
            self.assertEqual((a["later_id"], a["review"], (a["flags_json"] or {}).get("lateFlow")), (None, "draft", None))
            # 后补单上手动「去钉钉流程里找发票」：没有新东西；没权限的点不了
            self.assertEqual(self.post("/api/inv/later/%d/flow-check" % lid, "viewer").status_code, 403)
            j = self.ok(self.post("/api/inv/later/%d/flow-check" % lid, "recv2"))
            self.assertEqual((j["new"], j["linked"]), (0, 0))
            self.assertIn("没有新的文件", j["msg"])
            # 评论区来了一张：定时那一轮取到 → 记到后补单上、待审核；审核前不算已到
            flow["PI-LATE-1"] = (inst, [a_form, a_late])
            rc = intake.recheck_flows()
            self.assertGreaterEqual(rc["checked"], 1)
            self.assertEqual((rc["new"], rc["linked"]), (1, 1))
            b = by_num(num(35002))
            self.assertEqual((b["later_id"], b["review"], b["origin"]), (lid, "pending", "attachment"))
            self.assertEqual(b["flags_json"]["lateFlow"], {"at": "2099-01-01 10:00:00", "after": "2026-10-12 13:51:00", "by": "first"})
            v = inv.item_view(b)
            self.assertEqual((v["selfPending"], v["fromFlow"]), (True, True))
            l = S.later_get(e, lid)
            self.assertEqual((float(l["received_amount"] or 0), l["status"]), (0.0, "open"))
            lv = self.books._lv(l)
            self.assertEqual((lv["selfCount"], lv["flowCount"], lv["selfAmount"]), (1, 1, 600.0))
            a = by_num(num(35001))
            self.assertEqual((a["later_id"], a["review"]), (None, "draft"))                      # 表单里那张没被动
            # 刚看过的单 60 分钟内不再看（不白打钉钉接口）
            self.assertEqual(intake.recheck_flows()["checked"], 0)
            # 重算票面不丢「初审后才到」的标记
            self.assertIn("lateFlow", inv.refresh_item(b["id"])["flags_json"])
            # 进了发票审核：会计在待审核里搜得到，审核通过 → 这才算已到、后补单收齐
            q = self.ok(self.get("/api/inv/audit/queue?tab=pending&q=" + num(35002), "acct"))
            self.assertIn(fid, [r["id"] for r in q["rows"]])
            r = self.ok(self.post("/api/inv/audit/items/approve", "acct", {"folderId": fid, "itemIds": [b["id"]]}))
            self.assertEqual(r["items"][0]["review"], "approved")
            l = S.later_get(e, lid)
            self.assertEqual((float(l["received_amount"]), l["status"]), (600.0, "done"))
            # ④ 老票补分：以前就取进来、当时初审还没过的票（没标记、停在付款单名下）→ 后补单上点一次就分过去
            n2 = dict(norm, instId="PI-LATE-2", businessId="202610139900000000036", amount=500.0)
            S.folder_upsert_from_approval(e, self._norm(**n2), "recv", "later")
            fid2 = S.folder_by_inst(e, "PI-LATE-2")["id"]
            flow["PI-LATE-2"] = ({"operation_records": ops[:1], "tasks": []}, [a_old])          # 那时初审人还没审
            S.folder_update(e, fid2, attach_status="pending")
            inv.worker_step()
            old = next(i for i in S.folder_items(e, fid2) if i.get("number") == num(35003))
            self.assertEqual((old["later_id"], old["review"], (old["flags_json"] or {}).get("lateFlow")), (None, "draft", None))
            lid2 = self.later_row(inst_id="PI-LATE-2", folder_id=fid2, payee_name=f["seller"], pay_amount=500, expect_amount=500)
            flow["PI-LATE-2"] = (inst, [a_old])                                                # 现在看：初审早过了，这张是之后才发的
            j = self.ok(self.post("/api/inv/later/%d/flow-check" % lid2, "recv2"))
            self.assertEqual((j["new"], j["linked"], j["later"]["selfCount"], j["later"]["flowCount"]), (0, 1, 1, 1))
            self.assertIn("已记到后补单上", j["msg"])
            old = self.item(old["id"])
            self.assertEqual((old["later_id"], old["review"]), (lid2, "pending"))
            # 没有没收齐的后补单 → 不硬挂：票留在付款单名下，只标着「初审后才到」
            n3 = dict(norm, instId="PI-LATE-3", businessId="202610139900000000037", amount=600.0)
            S.folder_upsert_from_approval(e, self._norm(**n3), "recv", "later")
            fid3 = S.folder_by_inst(e, "PI-LATE-3")["id"]
            blobs["F-LATE3"] = einv_xml(num(35004), f["seller"], f["seller_tid"], f["buyer"], f["buyer_tid"], 566.04, 33.96, 600)
            flow["PI-LATE-3"] = (inst, [dict(a_late, fileId="F-LATE3")])
            S.folder_update(e, fid3, attach_status="pending")
            inv.worker_step()
            lone = next(i for i in S.folder_items(e, fid3) if i.get("number") == num(35004))
            self.assertEqual((lone["later_id"], lone["review"], bool(lone["flags_json"].get("lateFlow"))), (None, "draft", True))
            self.assertFalse(inv.self_pending(lone))
        # 新建后补单会排队再看一次流程（后台线程取走）
        inv.queue_recheck(fid3)
        self.assertIn(fid3, inv._RECHECK_Q)
        inv._RECHECK_Q.discard(fid3)
        self.ok(self.post("/api/inv/settings", "cfg", {"settings": {"intake": {"firstReviewers": []}}}))

    def test_36_gap_does_not_count_waiting_later_invoice_twice(self):
        """金额核对：后补票已经交来、还在等确认时，票已经算进专票了，后补单那头不能再算它「还没到」——
        线上 2026-10-13 一张 1,322 的单（申请人登记了后补单、又自己把票传上来）报出假差额 −1,322。号码、单号是编的。"""
        inv, S, e = self.inv, self.S, self.e
        f = self.tip.FAKE
        S.folder_upsert_from_approval(e, self._norm(instId="PI-GAP-1", businessId="202610149900000000036", applicant="申请人壬",
                                                    applicantUid="dt-gap", payeeName=f["seller"], amount=600.0), "recv", "later")
        fo = S.folder_by_inst(e, "PI-GAP-1")
        fid = fo["id"]
        lid = self.later_row(inst_id="PI-GAP-1", folder_id=fid, payee_name=f["seller"], pay_amount=600, expect_amount=600)

        def gap():
            return inv.folder_gap(S.folder_get(e, fid), S.folder_items(e, fid), inv.folder_laters(e, S.folder_get(e, fid)))

        def bill(n, total, **kw):
            cols = dict(folder_id=fid, kind="invoice", inv_type="special", origin="self", later_id=lid, review="pending", number=num(n),
                        total=total, amount=round(total / 1.06, 2), tax=round(total - total / 1.06, 2), proc_status="done",
                        buyer_name=self.OUR, buyer_tax_id=self.OUR_TID, seller_name=f["seller"], created_by="申请人壬")
            cols.update(kw)
            return S.item_insert(e, **cols)
        # 还没交票：后补 600 没到，平
        self.assertEqual([gap()[k] for k in ("special", "later", "laterWait", "gap")], [0.0, 600.0, 0.0, 0.0])
        # 申请人自己传来 200（等确认）：票算进专票 200，后补还没到只剩 400，仍然平；后补单本身的「已到」不变（审核通过才算）
        a = bill(36001, 200)
        self.books._later_recalc(lid)
        self.assertEqual([gap()[k] for k in ("special", "later", "laterWait", "gap")], [200.0, 400.0, 200.0, 0.0])
        l = S.later_get(e, lid)
        self.assertEqual((float(l["received_amount"] or 0), inv.later_left(l)), (0.0, 600.0))
        self.assertEqual(inv.later_row_view(l, S.folder_items(e, fid))["waiting"], 200.0)
        self.assertEqual(inv.later_row_view(l)["waiting"], 0.0)
        # 剩下 400 也传来了（钉钉流程里后到的那种）：整张单平，后补「还没到」是 0，不再报票比付款多
        b = bill(36002, 400, origin="attachment", flags_json={"lateFlow": {"at": "2099-01-01 10:00:00", "after": "2026-10-12 13:51:00", "by": "first"}})
        self.books._later_recalc(lid)
        g = gap()
        self.assertEqual([g[k] for k in ("special", "later", "laterWait", "gap")], [600.0, 0.0, 600.0, 0.0])
        self.assertFalse(inv.gap_off(g))
        # 交多了（多传一张 100）：等确认的最多顶掉后补单还没到的 600，多出来的照实报「票比付款多 100」
        c = bill(36003, 100)
        self.assertEqual([gap()[k] for k in ("special", "later", "laterWait", "gap")], [700.0, 0.0, 600.0, -100.0])
        S.item_update(e, c, status="removed")
        # 审核通过一张：这张转成「已到」，等确认的少了它，两头加起来还是平
        with patch.object(inv, "notify_dt", MagicMock(return_value={"sent": True, "msg": "已发送"})):
            self.ok(self.post("/api/inv/audit/items/approve", "acct", {"folderId": fid, "itemIds": [a]}))
            l = S.later_get(e, lid)
            self.assertEqual((float(l["received_amount"]), l["status"]), (200.0, "partial"))
            self.assertEqual([gap()[k] for k in ("special", "later", "laterWait", "gap")], [600.0, 0.0, 400.0, 0.0])
            self.ok(self.post("/api/inv/audit/items/approve", "acct", {"folderId": fid, "itemIds": [b]}))
        l = S.later_get(e, lid)
        self.assertEqual((float(l["received_amount"]), l["status"]), (600.0, "done"))
        self.assertEqual([gap()[k] for k in ("special", "later", "laterWait", "gap")], [600.0, 0.0, 0.0, 0.0])
        # 接收人自己扫到的票（当场就算已到）不受影响：没有「等确认」，不会被多减
        S.folder_upsert_from_approval(e, self._norm(instId="PI-GAP-2", businessId="202610149900000000037", applicant="申请人壬",
                                                    applicantUid="dt-gap", payeeName=f["seller"], amount=500.0), "recv", "later")
        fo2 = S.folder_by_inst(e, "PI-GAP-2")
        lid2 = self.later_row(inst_id="PI-GAP-2", folder_id=fo2["id"], payee_name=f["seller"], pay_amount=500, expect_amount=500)
        S.item_insert(e, folder_id=fo2["id"], kind="invoice", inv_type="special", origin="scanner", later_id=lid2, review="pending",
                      number=num(36004), total=300, amount=283.02, tax=16.98, proc_status="done", created_by="接收人乙")
        self.books._later_recalc(lid2)
        g2 = inv.folder_gap(fo2, S.folder_items(e, fo2["id"]), inv.folder_laters(e, fo2))
        self.assertEqual([g2[k] for k in ("special", "later", "laterWait", "gap")], [300.0, 200.0, 0.0, 0.0])

    def test_37_later_invoice_auto_transfer_voucher(self):
        """后补票审核通过后系统自己做「暂估转待认证」（用户 2026-10-14 定）：到金蝶找这张付款单当时的费用凭证（供应商往来贷方＝付款金额、
        挂同一家供应商），发票税额＝账上暂估就新建 借待认证／贷暂估 的凭证并提交；默认演练不写金蝶，真做只有主管理员能开；
        对不上、找到多张、做到一半中断的不自动做；金蝶里已有人做了、物流请款单、原凭证没有暂估的不重复做。金蝶全是假的，号码是编的。"""
        inv, S, e, L3 = self.inv, self.S, self.e, self.l3
        from routers import logistics_voucher as LVR
        from routers import invoice_vouchers as iv
        seller = self.tip.FAKE["seller"]
        HEAD = {k: "" for _, k in LVR._F}
        books, heads, calls = {}, [], {"save": [], "submit": []}

        def row(vno, acct, name, dr=0.0, cr=0.0, expl="", sup=True, **kw):
            r = dict(HEAD, **{"号": vno, "摘要": expl, "科目": acct, "科目名": name, "借": dr, "贷": cr})
            if sup:
                r.update({"供应商码": "S001", "供应商": seller})
            r.update(kw)
            return r

        def voucher(vno, gross, tax, expl, tax_sup=True):
            books[vno] = [row(vno, "6601.05", "快递费", dr=round(gross - tax, 2), expl=expl, sup=False, **{"部门码": "D01", "费用码": "F01"})]
            if tax:
                books[vno].append(row(vno, "2221.01.07", "暂估进项税", dr=tax, expl=expl, sup=tax_sup))
            books[vno].append(row(vno, "2241.02", "供应商往来", cr=gross, expl=expl))
            heads.append((vno, gross))
            L3._CACHE.clear()

        def q(s_, conf, form, fields, filt, order=""):
            if "FVOUCHERID=" in filt:                   # 这张凭证是谁、什么时候审核的
                return [{"审核人": "审核人甲", "审核时间": "2026-10-14T10:15:22.92"}]
            if "FCREDIT=" in filt:
                amt = float(filt.split("FCREDIT=")[1])
                return [dict(books[v][-1], **{"年": 2026, "期": 9}) for v, g in heads if abs(g - amt) < 0.005]
            return books.get(filt.split("FVOUCHERGROUPNO='")[1].split("'")[0], [])

        def save(model, s_=None, conf=None):
            calls["save"].append(model)
            return {"id": 9000 + len(calls["save"]), "billno": "PZ%d" % len(calls["save"])}

        def mk(tag, amount, tax, n):
            S.folder_upsert_from_approval(e, self._norm(instId="PI-L3-" + tag, businessId="20260923172100039%04d" % n, applicant="申请人癸",
                                                        applicantUid="dt-l3", payeeName=seller, amount=amount), "recv", "later")
            fo = S.folder_by_inst(e, "PI-L3-" + tag)
            lid = self.later_row(inst_id=fo["inst_id"], folder_id=fo["id"], business_id=fo["business_id"], company=self.OUR,
                                 payee_name=seller, pay_amount=amount, expect_amount=amount)
            iid = S.item_insert(e, folder_id=fo["id"], kind="invoice", inv_type="special", origin="self", later_id=lid, review="pending",
                                number=num(37000 + n), total=amount, amount=round(amount - tax, 2), tax=tax, tax_rate="1%", proc_status="done",
                                buyer_name=self.OUR, buyer_tax_id=self.OUR_TID, seller_name=seller, created_by="申请人癸")
            self.books._later_recalc(lid)
            return fo["id"], lid, iid

        def approve(fid, iid):
            self.ok(self.post("/api/inv/audit/items/approve", "acct", {"folderId": fid, "itemIds": [iid]}))

        sent = MagicMock(return_value={"sent": True, "msg": "已发送"})
        booked = {"rows": []}
        gone = {"deleted": set(), "unsub": [], "status": {}}

        def told():                 # 「没自动做」的钉钉提醒发了几条（后补单收齐给申请人的「已签收」不算）
            return sum(1 for c in sent.call_args_list if "没自动做" in c[0][1])
        with patch.object(LVR.kc, "_query", q), patch.object(LVR, "_kd", lambda: (None, {})), \
                patch.object(LVR.kc, "login", lambda s_=None, conf=None: (None, {})), patch.object(LVR.kc, "save_voucher", save), \
                patch.object(LVR.kc, "view_voucher", lambda vid, s_=None, conf=None: {
                    "exists": vid not in gone["deleted"], "vno": str(vid - 8770), "status": gone["status"].get(vid, "B")}), \
                patch.object(LVR.kc, "unsubmit_bill", lambda form, ids, s_=None, conf=None: gone["unsub"].append(ids)), \
                patch.object(LVR.kc, "delete_vouchers", lambda ids, s_=None, conf=None: gone["deleted"].add(ids)), \
                patch.object(LVR.kc, "submit_bill", lambda form, ids, s_=None, conf=None: calls["submit"].append(ids)), \
                patch.object(LVR, "_xred_date", lambda book, d, s_, conf: ("2026-10-14", 2026, 10)), \
                patch.object(iv, "fetch_rows", lambda numbers, since="": list(booked["rows"])), \
                patch.object(self.db, "list_orgs", lambda: [{"full_name": self.OUR, "book_code": "101"}]), \
                patch.object(inv, "notify_dt", sent):
            # ① 默认是演练：审核前只能看打算怎么做；审核通过后排上队，演练不写金蝶
            self.assertEqual(L3.cfg()["mode"], "dry")
            fa, la, ia = mk("A", 1322.0, 13.09, 1)
            voucher("196", 1322.0, 13.09, "禾享提起支付新加坡样品运费")
            self.assertEqual(L3.run_one(la)["status"], "wait")
            self.assertEqual(self.get("/api/inv/later/%d/voucher-plan" % la, "viewer").status_code, 403)
            v = self.ok(self.get("/api/inv/later/%d/voucher-plan" % la, "acct"))
            self.assertEqual((v["mode"], v["ready"], v["plan"]["kind"], v["plan"]["taxInv"], v["plan"]["taxEst"], v["plan"]["src"]),
                             ("dry", False, "exact", 13.09, 13.09, ["2026-09-196"]))
            self.assertEqual([(x["dc"], x["acct"], x["amount"]) for x in v["lines"]], [("借", "2221.01.06", 13.09), ("贷", "2221.01.07", 13.09)])
            self.assertTrue(v["lines"][0]["expl"].startswith(num(37001) + "核销9/196#"), v["lines"][0]["expl"])
            self.assertEqual(v["lines"][1]["supplier"], seller)
            S.item_insert(e, folder_id=fa, kind="other", origin="attachment", review="draft", proc_status="done")     # 付款单上的附件（草稿）
            approve(fa, ia)
            self.assertIn(la, L3._Q)
            # 后补票审完，付款单还在收票中、附件是草稿：「已通过」里要搜得到（线上审完三个页签都搜不到，没法回来点「撤回重做」）；待审核里没有了
            self.assertIn(fa, [r["id"] for r in self.ok(self.get("/api/inv/audit/queue?tab=done&q=" + num(37001), "acct"))["rows"]])
            self.assertNotIn(fa, [r["id"] for r in self.ok(self.get("/api/inv/audit/queue?tab=pending&q=" + num(37001), "acct"))["rows"]])
            L3.drain()
            st = L3.state_of(la)
            self.assertEqual(st["status"], "dry")
            self.assertIn("演练（没写金蝶）", st["msg"])
            self.assertEqual(calls["save"], [])
            # 演练不打扰人：对不上的只把原因记在后补票上，不发钉钉
            fz, lz, iz = mk("Z", 4000.0, 226.42, 9)
            voucher("990", 4000.0, 40.0, "壬提起支付服务费")
            approve(fz, iz)
            L3.drain()
            self.assertEqual((L3.state_of(lz)["status"], L3.state_of(lz)["kind"], told()), ("manual", "diff", 0))
            # ② 开关：切真做只有主管理员能点。不翻旧账：切之前审核通过的票，定时那一轮不自动补做；人在弹窗里点「现在做」才做
            self.assertEqual(self.post("/api/inv/later-voucher/config", "cfg", {"mode": "x"}).status_code, 400)
            self.assertEqual(self.post("/api/inv/later-voucher/config", "cfg", {"mode": "on"}).status_code, 403)
            self.db.set_setting(L3.CFG_KEY, {"mode": "on", "by": "boss", "at": "2099-01-01 00:00:00"}, "boss")      # 开关是「以后」才开的
            L3.run_due()
            self.assertEqual((L3.state_of(la)["status"], calls["save"]), ("old", []))
            self.assertEqual(L3.run_due(), 0)
            self.assertEqual(self.post("/api/inv/later/%d/voucher-run" % la, "recv2").status_code, 403)
            r = self.ok(self.post("/api/inv/later/%d/voucher-run" % la, "acct"))
            self.assertEqual((r["status"], r["state"]["vno"]), ("done", "231"))
            self.assertEqual(len(calls["save"]), 1)
            self.db.set_setting(L3.CFG_KEY, {"mode": "dry"}, "boss")
            self.assertEqual(self.ok(self.post("/api/inv/later-voucher/config", "boss", {"mode": "on"}))["mode"], "on")   # 现在起开：下面审的都自动做
            m = calls["save"][0]
            self.assertEqual((m["FACCOUNTBOOKID"], m["FDate"], m["FYear"], m["FPeriod"]), ({"FNumber": "101"}, "2026-10-14", 2026, 10))
            self.assertEqual([(x["FACCOUNTID"]["FNumber"], x["FDEBIT"], x["FCREDIT"]) for x in m["FEntity"]],
                             [("2221.01.06", 13.09, 0.0), ("2221.01.07", 0.0, 13.09)])
            self.assertEqual(m["FEntity"][1]["FDetailID"], {"FDETAILID__FFLEX4": {"FNumber": "S001"}})
            self.assertNotIn("FDetailID", m["FEntity"][0])
            self.assertEqual(calls["submit"], [9001])
            st = L3.state_of(la)
            self.assertEqual((st["status"], st["vno"], st["submitted"], st["vyear"], st["vperiod"]), ("done", "231", True, 2026, 10))
            bk = self.item(ia)["flags_json"]["_bookkeeping"]
            self.assertEqual((bk["status"], bk["vouchers"]), ("booked", [{"book": self.OUR, "period": "2026-10", "number": "记-231"}]))
            L3.run_due()
            L3.run_one(la)
            self.assertEqual(len(calls["save"]), 1)                                     # 做过了不重复建
            self.assertEqual(self.post("/api/inv/later/%d/voucher-source" % la, "acct", {"picks": []}).status_code, 400)
            # 撤回重做（用户 2026-10-14：系统建的那张不对，他在金蝶里改不了）：已审核的不撤；没审核的撤销提交→删除→按现在的规则重建，票上的凭证号换成新的
            self.assertEqual(self.post("/api/inv/later/%d/voucher-redo" % la, "recv2").status_code, 403)
            gone["status"][9001] = "C"
            r = self.post("/api/inv/later/%d/voucher-redo" % la, "acct")
            self.assertEqual((r.status_code, "已经审核了" in r.json()["msg"], gone["deleted"], len(calls["save"])), (400, True, set(), 1))
            gone["status"][9001] = "B"
            v = self.ok(self.get("/api/inv/later/%d/voucher-plan" % la, "acct"))
            self.assertTrue(v["canRedo"])
            r = self.ok(self.post("/api/inv/later/%d/voucher-redo" % la, "acct"))
            self.assertEqual((r["status"], r["withdrawn"], gone["unsub"], gone["deleted"], len(calls["save"])), ("done", "记-231", [9001], {9001}, 2))
            self.assertEqual((r["state"]["vno"], r["state"]["redoOf"]), ("232", "记-231"))
            self.assertEqual(self.item(ia)["flags_json"]["_bookkeeping"]["vouchers"], [{"book": self.OUR, "period": "2026-10", "number": "记-232"}])
            self.assertEqual(self.post("/api/inv/later/%d/voucher-redo" % 999999, "acct").status_code, 404)
            # 这一栏跟着金蝶走（用户在金蝶审核了，弹窗里还写「等人在金蝶审核」、还摆着撤回重做）：已提交→等审核、可撤回；
            # 已审核→写「金蝶里已审核」、不给撤回重做，并记下来以后不再问金蝶
            L3._LIVE.clear()
            v = self.ok(self.get("/api/inv/later/%d/voucher-plan" % la, "acct"))
            self.assertEqual((v["voucherStatus"], v["canRedo"], "记-232" in v["state"]["msg"], "等人在金蝶审核" in v["state"]["msg"]), ("B", True, True, True))
            gone["status"][9002] = "C"
            L3._LIVE.clear()
            v = self.ok(self.get("/api/inv/later/%d/voucher-plan" % la, "acct"))
            self.assertEqual((v["voucherStatus"], v["canRedo"], "金蝶里已审核" in v["state"]["msg"], "等人" in v["state"]["msg"]), ("C", False, True, False))
            self.assertTrue(L3.state_of(la)["audited"])
            self.assertIn("金蝶里已审核（审核人甲 10-14 10:15）", v["state"]["msg"])           # 谁、什么时候审核的也带出来
            self.assertEqual((v["state"]["auditedBy"], v["state"]["auditedAt"]), ("审核人甲", "2026-10-14 10:15"))
            gone["status"][9002] = "B"                                                   # 记下来了：不再去问金蝶
            self.assertEqual(self.ok(self.get("/api/inv/later/%d/voucher-plan" % la, "acct"))["voucherStatus"], "C")
            # ③ 对不上（票面税额 ≠ 账上暂估）：不自动做，写明差多少，提醒一次
            fb, lb, ib = mk("B", 2000.0, 113.21, 2)
            voucher("300", 2000.0, 20.0, "某某提起支付服务费")
            approve(fb, ib)
            L3.drain()
            st = L3.state_of(lb)
            self.assertEqual((st["status"], st["kind"]), ("manual", "diff"))
            self.assertIn("发票税额 113.21 ≠ 账上暂估进项税 20.00", st["msg"])
            self.assertEqual(told(), 1)
            L3._put(lb, status="")
            L3.run_one(lb)
            self.assertEqual(told(), 1)                                                 # 同一个原因不重复提醒
            # ④ 找到两张都对得上：要审核人选；选定后「现在做」
            fc, lc, ic = mk("C", 500.0, 5.0, 3)
            voucher("401", 500.0, 5.0, "甲提起支付")
            voucher("402", 500.0, 5.0, "乙提起支付")
            approve(fc, ic)
            L3.drain()
            self.assertEqual(L3.state_of(lc)["kind"], "multi")
            v = self.ok(self.get("/api/inv/later/%d/voucher-plan" % lc, "acct"))
            self.assertEqual((v["plan"]["kind"], len(v["plan"]["cands"])), ("multi", 2))
            self.assertEqual(self.post("/api/inv/later/%d/voucher-source" % lc, "recv2", {"picks": ["2026-09-402"]}).status_code, 403)
            v = self.ok(self.post("/api/inv/later/%d/voucher-source" % lc, "acct", {"picks": ["2026-09-402"]}))
            self.assertEqual((v["plan"]["kind"], v["plan"]["src"], [c["picked"] for c in v["plan"]["cands"]]), ("exact", ["2026-09-402"], [False, True]))
            r = self.ok(self.post("/api/inv/later/%d/voucher-run" % lc, "acct"))
            self.assertEqual((r["status"], r["state"]["status"], len(calls["save"])), ("done", "done", 3))
            self.assertIn("9/402#", calls["save"][2]["FEntity"][1]["FEXPLANATION"])
            # 暂估进项税那一行一定挂供应商：原凭证的暂估行当时没挂（线上 2026-10-14 禾享 9月记-196 就是这样，建出来的贷方没有维度，
            # 用户「不太对，暂估是有维度的」）→ 用同一张凭证供应商往来行上的那家，并在计划里提醒原凭证也补上
            fh, lh, ih = mk("H", 660.0, 6.6, 8)
            voucher("480", 660.0, 6.6, "辛提起支付", tax_sup=False)
            v = self.ok(self.get("/api/inv/later/%d/voucher-plan" % lh, "acct"))
            self.assertEqual((v["plan"]["kind"], v["lines"][1]["supplier"]), ("exact", seller))
            self.assertIn("暂估那一行当时没挂供应商", v["plan"]["why"])
            approve(fh, ih)
            L3.drain()
            self.assertEqual((L3.state_of(lh)["status"], len(calls["save"])), ("done", 4))
            self.assertEqual(calls["save"][3]["FEntity"][1]["FDetailID"], {"FDETAILID__FFLEX4": {"FNumber": "S001"}})
            # ⑤ 物流请款单不归这里；原凭证没有暂估的不用做；上次做到一半的不重复建；金蝶里已有人做了的不再做
            fd, ld, idd = mk("D", 800.0, 8.0, 4)
            voucher("450", 800.0, 8.0, "丁提起支付")
            l3_lines = [{"block": "核销", "expl": num(37004) + "核销9/450#丁提起支付", "acct": "2221.01.06", "acct_name": "待认证进项税额", "dr": 8.0, "cr": 0.0, "dims": {}},
                        {"block": "核销", "expl": "核销9/450#丁提起支付", "acct": "2221.01.07", "acct_name": "暂估进项税", "dr": 0.0, "cr": 8.0, "dims": {}}]
            logi = {"cfg": {"mode": "on", "kinds": ["later"]}, "l3": {"ok": True, "lines": l3_lines, "tax": 8.0, "redo": 0, "gross_diff": 0.0, "msg": ""}}
            with patch.object(L3, "_is_logistics", lambda i: True), patch.object(LVR, "_auto_cfg", lambda: logi["cfg"]), \
                    patch.object(LVR, "_preview_data", lambda inst: ({"later3": logi["l3"]}, 200)):
                # 物流请款单：这一栏要把「付款做账」那条线的打算说清楚，不能只说"这里不做"
                p = L3.plan(ld)
                self.assertEqual((p["kind"], p["ok"]), ("logistics", False))
                self.assertIn("付款凭证还没做", p["why"])
                self.db.set_setting(LVR._POSTED_KEY, {"PI-L3-D": {"vno": "391", "tax_later": True}}, "test")
                # 开关是真做：物流请款单的后补票也由发票后补单这边做（用户 2026-10-14「付款凭证已经做完了，不可能在付款那条线转待认证」），
                # 分录按付款做账那条线的算法；那条线定时那一轮对登记了后补单的请款单让开
                v = self.ok(self.get("/api/inv/later/%d/voucher-plan" % ld, "acct"))
                self.assertEqual((v["plan"]["kind"], v["plan"]["by"], v["plan"]["ok"], [(x["dc"], x["acct"], x["amount"]) for x in v["lines"]]),
                                 ("logistics", "later", True, [("借", "2221.01.06", 8.0), ("贷", "2221.01.07", 8.0)]))
                for w in ("由发票后补单这边做", "这边马上建凭证", "对得上，转待认证税额 8.00"):
                    self.assertIn(w, v["plan"]["why"])
                self.assertTrue(LVR._later3_elsewhere())
                logi["l3"] = {"ok": False, "lines": [], "msg": "发票税额和挂着的暂估进项税对不上"}
                p = L3.plan(ld)
                self.assertEqual((p["ok"], "现在不会自动做" in p["why"]), (False, True))
                logi["l3"] = {"ok": True, "lines": l3_lines, "tax": 8.0, "redo": 0, "gross_diff": 0.0, "msg": ""}
                posts = []

                def fake_post_later(inst, user):
                    posts.append((inst, user))
                    self.db.set_setting(LVR._POSTED_KEY, {inst: {"vno": "391", "tax_later": True, "later3": {
                        "vid": 7001, "vno": "402", "date": "2026-10-14", "book": self.OUR, "tax": 8.0, "submitted": True, "by": user}}}, "test")
                    return ["暂估转待认证凭证 记-402 已建"]
                with patch.object(LVR, "_post_later", fake_post_later):
                    approve(fd, idd)
                    L3.drain()
                    st = L3.state_of(ld)
                    self.assertEqual((st["status"], st["kind"], st["vno"], st["vyear"], st["vperiod"], posts), ("done", "logistics", "402", 2026, 10, [("PI-L3-D", "系统自动")]))
                    self.assertEqual(self.item(idd)["flags_json"]["_bookkeeping"]["vouchers"], [{"book": self.OUR, "period": "2026-10", "number": "记-402"}])
                    L3.run_one(ld)
                    L3.run_due()
                    self.assertEqual(len(posts), 1)                                     # 做过了不重复做
                self.assertIn("已经建好了，记-402", L3.plan(ld)["why"])
                # 开关不是真做：不接管，那条线自己做；这边只把它的打算亮出来
                self.db.set_setting(L3.CFG_KEY, {"mode": "dry"}, "boss")
                self.db.set_setting(LVR._POSTED_KEY, {"PI-L3-D": {"vno": "391", "tax_later": True}}, "test")
                p = L3.plan(ld)
                self.assertEqual((p["by"], p["ok"], "由「付款做账」那条线做" in p["why"], "那边下一轮" in p["why"]), ("logistics", True, True, True))
                self.assertFalse(LVR._later3_elsewhere())
                logi["cfg"] = {"mode": "dry", "kinds": ["later"]}                       # 那边也没开真做：要人去付款做账页点
                p = L3.plan(ld)
                self.assertEqual((p["ok"], "手动点" in p["why"]), (False, True))
                self.db.set_setting(L3.CFG_KEY, {"mode": "on", "by": "boss", "at": "2026-10-01 00:00:00"}, "boss")
            self.db.set_setting(LVR._POSTED_KEY, {}, "test")
            fe, le, ie = mk("E", 300.0, 3.0, 5)
            voucher("500", 300.0, 0, "戊提起支付")
            approve(fe, ie)
            L3.drain()
            self.assertEqual((L3.state_of(le)["status"], "没有暂估进项税" in L3.state_of(le)["msg"]), ("none", True))
            ff, lf, i_f = mk("F", 900.0, 9.0, 6)
            voucher("600", 900.0, 9.0, "己提起支付")
            L3._put(lf, status="posting")
            approve(ff, i_f)
            L3.drain()
            self.assertEqual((L3.state_of(lf)["status"], L3.state_of(lf)["kind"]), ("manual", "half"))
            fg, lg, ig = mk("G", 700.0, 7.0, 7)
            voucher("700", 700.0, 7.0, "庚提起支付")
            booked["rows"] = [{"book": self.OUR, "year": 2026, "month": 10, "group": "记", "number": "88", "summary": num(37007) + "核销9/700#庚提起支付"}]
            approve(fg, ig)
            L3.drain()
            self.assertEqual((L3.state_of(lg)["status"], L3.state_of(lg)["kind"]), ("done", "byhand"))
            self.assertEqual(self.item(ig)["flags_json"]["_bookkeeping"]["vouchers"], [{"book": self.OUR, "period": "2026-10", "number": "记-88"}])
            self.assertEqual(len(calls["save"]), 4)                                     # 这四种都没有新建凭证
            # ⑥ 关掉以后什么都不做
            self.ok(self.post("/api/inv/later-voucher/config", "boss", {"mode": "off"}))
            self.assertEqual(L3.run_one(lb)["status"], "skip")
            self.assertEqual(L3.run_due(), 0)
        self.db.set_setting(L3.CFG_KEY, {"mode": "dry"}, "test")

    def test_38_item_checks_for_audit_dialog(self):
        """审核弹窗「系统核对」（用户 2026-10-14：「比如购买方税号已查验，销方税号与库里一致」）：系统替人核过的结论一行一句——
        购买方对抬头清单、销售方对销方档案（第几次来票／新销方／名称变了）、收款方、金额、查重、来源、税局查验。号码、公司是编的。"""
        inv, S, e = self.inv, self.S, self.e
        f = self.tip.FAKE
        st = inv.get_settings()
        folder = {"id": 0, "payee_name": f["seller"], "company": self.OUR}
        tid = self.tip._uscc("91440300MA38CHK01")          # 这条用例自己的销方税号：别的用例没用过，档案从零开始
        base = dict(folder_id=None, kind="invoice", inv_type="special", origin="upload", review="pending", proc_status="done",
                    buyer_name=self.OUR, buyer_tax_id=self.OUR_TID, seller_name=f["seller"], seller_tax_id=tid,
                    amount=283.02, tax=16.98, total=300, tax_rate="6%", field_src_json={"number": {"src": "xml"}})
        fo = self.ok(self.post("/api/inv/folder/manual", "intern", {"title": "系统核对测试", "amount": 300, "company": self.OUR}))["folder"]
        a = S.item_insert(e, **dict(base, folder_id=fo["id"], number=num(38001)))
        S.seller_seen(e, tid, f["seller"], "2026-10-01", a)

        def checks(iid, fd=folder):
            return {c["key"]: c for c in inv.item_checks(e, self.item(iid), fd, st)}
        c = checks(a)
        self.assertEqual([c[k]["tone"] for k in ("buyer", "payee", "amount", "dup", "source", "verify")], ["ok", "ok", "ok", "ok", "ok", "todo"])
        self.assertEqual(c["buyer"]["text"], "名称、税号都和本公司抬头清单一致")
        self.assertIn("电子原件", c["source"]["text"])
        self.assertEqual((c["seller"]["tone"], "新销方" in c["seller"]["text"]), ("info", True))      # 第一次来票
        b = S.item_insert(e, **dict(base, folder_id=fo["id"], number=num(38002)))
        self.assertEqual(checks(b)["seller"]["tone"], "ok")
        self.assertIn("和销方档案一致 · 这家第 2 次来票", checks(b)["seller"]["text"])
        # 有问题的：购方税号不在清单、销方名称和档案不一样、开票的不是收款方、金额加不上、重复、税局清单里没有、拍照识别还有待核
        bad = S.item_insert(e, **dict(base, folder_id=fo["id"], number=num(38003), buyer_tax_id=self.OTHER_TID, seller_name="另一个名字有限公司",
                                      amount=280, tax=16.98, total=300, verify="yellow", field_src_json={"number": {"src": "ocr"}},
                                      pending_json=["sellerName", "amount"],
                                      flags_json={"dup": {"kind": "opening", "source": "期初导入"}, "sellerMismatch": {"seller": "另一个名字有限公司", "payee": f["seller"]}}))
        c = checks(bad)
        self.assertEqual([c[k]["tone"] for k in ("buyer", "seller", "payee", "amount", "dup", "source", "verify")],
                         ["warn", "warn", "warn", "warn", "err", "warn", "warn"])
        self.assertIn("不在清单里", c["buyer"]["text"])
        self.assertIn("销方档案里的名称是「%s」" % f["seller"], c["seller"]["text"])
        self.assertIn("差 3.02", c["amount"]["text"])
        self.assertIn("2 个字段要你对着图核一下", c["source"]["text"])
        # 收据不出核对；票夹详情接口里每张票带 checks
        r = S.item_insert(e, folder_id=fo["id"], kind="receipt", origin="upload", review="pending", proc_status="done", total=50)
        self.assertEqual(inv.item_checks(e, self.item(r), folder, st), [])
        d = self.ok(self.get("/api/inv/folder/%d" % fo["id"], "acct"))
        got = {i["id"]: i["checks"] for i in d["items"]}
        self.assertEqual(got[r], [])
        self.assertEqual([x["label"] for x in got[a]], ["购买方", "销售方", "金额", "查重", "来源", "税局查验"])    # 手工票夹没有收款方，不出那一行

    def test_99_reset_trial_data(self):
        """清空试运行数据（名字排最后跑：它会把前面用例攒的数据清掉）。只有主管理员能点、要手打「清零」；
        先留底再清，原件挪进留底目录不删；后补单和它挂的票夹（连同里面的票、文件、经办记录）整体保留；
        清完钉钉自动接入的起始日改成指定那天、下一轮整段重扫。人名、单号、发票都是编的。"""
        inv, S, e, rs = self.inv, self.S, self.e, self.reset
        f = self.tip.FAKE
        url = "/api/inv/admin/reset"
        # 一个要被清掉的票夹：带一张电子票原件
        fo = self.ok(self.post("/api/inv/folder/manual", "intern", {"title": "清零测试票夹", "amount": 300, "company": self.OUR}))["folder"]
        x = einv_xml(num(99001), f["seller"], f["seller_tid"], f["buyer"], f["buyer_tid"], 283.02, 16.98, 300)
        up = self.ok(self.upload("/api/inv/desk/upload", "intern", [("清零测试.xml", x, "application/xml")]))
        gone_item = up["results"][0]["item"]["id"]
        gone_file = S.item_get(e, gone_item)["file_id"]
        gone_path = inv.abs_path(S.file_get(e, gone_file)["orig_path"])
        self.assertTrue(os.path.isfile(gone_path))
        # 一张要留下的后补单：挂着票夹、收到过一张电子票
        S.folder_upsert_from_approval(e, self._norm(instId="PI-RESET-KEEP", businessId="202610139900000000001", applicant="申请人庚",
                                                    applicantUid="dt-keep", payeeName=f["seller"], amount=600.0), "recv", "later")
        kf = S.folder_by_inst(e, "PI-RESET-KEEP")["id"]
        lid = self.later_row(inst_id="PI-RESET-KEEP", folder_id=kf, payee_name=f["seller"], pay_amount=600, expect_amount=600)
        x2 = einv_xml(num(99002), f["seller"], f["seller_tid"], f["buyer"], f["buyer_tid"], 283.02, 16.98, 300)
        with patch.object(inv, "notify_dt", MagicMock(return_value={"sent": True, "msg": "已发送"})):
            kept = self.ok(self.upload("/api/inv/later/%d/receive-upload" % lid, "recv2", [("留下的票.xml", x2, "application/xml")]))
        kept_item = kept["results"][0]["item"]["id"]
        kept_path = inv.abs_path(S.file_get(e, S.item_get(e, kept_item)["file_id"])["orig_path"])
        with e.connect() as cx:
            n_later = len(cx.execute(select(S.LATER.c.id)).scalars().all())
            later_items = set(cx.execute(select(S.ITEM.c.id).where(S.ITEM.c.later_id.isnot(None))).scalars())
            n_opening = len(cx.execute(select(S.OPENING.c.id)).scalars().all())
        # 权限：没登录 / 有设置权限但不是主管理员 / 主管理员
        self.assertEqual(self.c.get(url).status_code, 401)
        self.assertEqual(self.get(url, "acct").status_code, 403)
        r = self.get(url, "cfg")
        self.assertEqual(r.status_code, 403)
        self.assertIn("主管理员", r.json()["msg"])
        self.assertEqual(self.post(url, "cfg", {"phrase": "清零", "since": "2026-09-01"}).status_code, 403)
        pv = self.ok(self.get(url, "boss"))
        self.assertEqual((pv["phrase"], pv["busy"], pv["keep"]["laters"]), ("清零", [], n_later))
        self.assertGreaterEqual(pv["clear"]["folders"], 1)             # 单独跑这一条时只有上面造的那一个
        self.assertGreaterEqual(pv["clear"]["invoices"], 1)
        # 没打确认字样 / 没填日期 / 日期不对：不清
        since = (date.today() - timedelta(days=30)).isoformat()
        for body, word in (({"phrase": "", "since": since}, "清零"), ({"phrase": "删除", "since": since}, "清零"),
                           ({"phrase": "清零"}, "从哪天起"), ({"phrase": "清零", "since": "上个月"}, "从哪天起"),
                           ({"phrase": "清零", "since": (date.today() + timedelta(days=1)).isoformat()}, "不能晚于今天"),
                           ({"phrase": "清零", "since": (date.today() - timedelta(days=200)).isoformat()}, "最多只能往前取")):
            r = self.post(url, "boss", body)
            self.assertEqual(r.status_code, 400, body)
            self.assertIn(word, r.json()["msg"])
        # 还有票夹在拉附件：先不清
        S.folder_update(e, fo["id"], attach_status="running")
        r = self.post(url, "boss", {"phrase": "清零", "since": since})
        self.assertEqual(r.status_code, 409)
        self.assertIn("正在拉钉钉附件", r.json()["msg"])
        S.folder_update(e, fo["id"], attach_status="done")
        self.assertTrue(os.path.isfile(gone_path))                      # 前面几次都没动东西
        self.assertIsNotNone(S.item_get(e, gone_item))
        self.db.set_setting(rs.intake.STATE_KEY, {"since": "2026-09-09", "waiting": {"PI-W": "2026-10-01"}, "skip": {},
                                                   "last": {"at": "x"}}, "test")
        j = self.ok(self.post(url, "boss", {"phrase": "清零", "since": since}))
        self.assertEqual((j["cleared"]["folders"], j["cleared"]["invoices"], j["kept"]["laters"], j["since"], j["fileErrors"]),
                         (pv["clear"]["folders"], pv["clear"]["invoices"], n_later, since, []))
        # 清掉的：行没了，原件挪进留底目录（不是删）
        self.assertIsNone(S.item_get(e, gone_item))
        self.assertIsNone(S.folder_get(e, fo["id"]))
        self.assertFalse(os.path.exists(gone_path))
        root = os.path.join(inv.UPLOAD_DIR, *j["archive"].split("/")[1:])
        rel = os.path.relpath(gone_path, os.path.realpath(inv.UPLOAD_DIR))
        self.assertTrue(os.path.isfile(os.path.join(root, "files", rel)), rel)
        import gzip
        with gzip.open(os.path.join(root, "rows.json.gz"), "rb") as fh:
            dump = json.loads(fh.read().decode("utf-8"))
        self.assertEqual(len(dump["tables"]["inv_folder"]), pv["clear"]["folders"])
        self.assertEqual(len(dump["tables"]["inv_item"]), pv["clear"]["invoices"] + pv["clear"]["others"])
        self.assertIn(num(99001), [r_["number"] for r_ in dump["tables"]["inv_item"]])
        self.assertEqual(dump["meta"]["since"], since)
        # 留下的：后补单、它挂的票夹、收到的票和原件、经办记录、期初
        with e.connect() as cx:
            self.assertEqual(len(cx.execute(select(S.LATER.c.id)).scalars().all()), n_later)
            self.assertEqual(set(cx.execute(select(S.ITEM.c.id).where(S.ITEM.c.later_id.isnot(None))).scalars()), later_items)
            keep_f = set(cx.execute(select(S.LATER.c.folder_id).where(S.LATER.c.folder_id.isnot(None))).scalars())
            keep_f |= set(cx.execute(select(S.ITEM.c.folder_id).where(S.ITEM.c.later_id.isnot(None))).scalars())
            self.assertEqual(set(cx.execute(select(S.FOLDER.c.id)).scalars()), keep_f - {None})
            left = cx.execute(select(S.ITEM.c.id, S.ITEM.c.folder_id, S.ITEM.c.later_id)).all()
            self.assertTrue(all(r_[2] is not None or r_[1] in keep_f for r_ in left), left)
            self.assertEqual(len(cx.execute(select(S.OPENING.c.id)).scalars().all()), n_opening)
            self.assertEqual(len(cx.execute(select(S.TAXLIST.c.id)).scalars().all()), 0)
            orphan = cx.execute(select(S.FILE.c.id).where(S.FILE.c.folder_id.isnot(None), S.FILE.c.folder_id.notin_(keep_f))).all()
            self.assertEqual(orphan, [])
        self.assertTrue(os.path.isfile(kept_path))
        d = self.ok(self.get("/api/inv/later/%d" % lid, "recv2"))
        self.assertEqual((d["later"]["id"], [i["id"] for i in d["items"]]), (lid, [kept_item]))
        self.assertGreater(len(d["logs"]), 0)
        # 钉钉自动接入：起始日改了，下一轮整段重扫；还在审批中的单子照样跟着
        self.assertEqual(inv.get_settings()["intake"]["since"], since)
        st = rs.intake._load_state()
        self.assertEqual((st["since"], st["waiting"]), ("", {"PI-W": "2026-10-01"}))
        # 再看一眼：没有可清的了；清完照常能收票、进台账
        pv2 = self.ok(self.get(url, "boss"))
        self.assertEqual((pv2["clear"]["folders"], pv2["clear"]["invoices"], pv2["clear"]["files"], pv2["since"]), (0, 0, 0, since))
        a, _ = self.make_item(num(99003), 106, "31", "20240402", dict(amount=100, tax=6, total=106, buyer_name=self.OUR,
                                                                      buyer_tax_id=self.OUR_TID))
        led = self.ok(self.get("/api/inv/ledger?from=2024-04-01&to=2024-04-30", "viewer"))
        self.assertEqual([r_["id"] for r_ in led["rows"]], [a])

if __name__ == "__main__":
    unittest.main()
