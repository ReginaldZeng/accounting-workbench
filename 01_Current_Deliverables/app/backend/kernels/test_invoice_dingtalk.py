# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家）
# Description: 发票管家钉钉内核单测。全部合成夹具（假名字/假金额/假账号），不联网：
#   requests 换成假对象、配置读取打桩；覆盖短链接解析（含 corpid）、两种模板的表单规范化
#   （收款方/金额/公司主体/图片/关联审批/"null" 值）、按审批编号找单的时间窗与翻页、
#   错误串抹密钥、未配置时发消息一个请求都不发、token 缓存。
#   审查修复（同日）：整天兜底逐张取单封顶/超时/缓存续查、外站链接带不出 corpid。
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernels import invoice_dingtalk as d  # noqa: E402

FAKE_CONF = {"appkey": "dingFAKEKEY123", "appsecret": "FAKESECRETabc987", "agentid": "1",
             "mobiles": [], "userids": []}


class FakeResp(object):
    def __init__(self, js=None, text="", status=200, headers=None, content=None):
        self._js = js
        self.text = text
        self.status_code = status
        self.headers = headers or {}
        self.content = content if content is not None else text.encode("utf-8")

    def json(self):
        return self._js if self._js is not None else {}

    def iter_content(self, n):
        for i in range(0, len(self.content), n):
            yield self.content[i:i + n]


class FakeRequests(object):
    """按 URL 路由的假 requests：post_handler(path, params, json) / get_handler(url, kw) 由各用例给。"""

    def __init__(self, post_handler=None, get_handler=None):
        self.post_handler = post_handler
        self.get_handler = get_handler
        self.calls = []

    def post(self, url, params=None, json=None, **kw):
        self.calls.append(("POST", url, params, json))
        return self.post_handler(url, params or {}, json or {})

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw.get("params"), None))
        return self.get_handler(url, kw)


def _payment_inst():
    """付款申请（公对公）——结构照实测，值全是假的。"""
    bank_rows = [{"rowValue": [
        {"componentType": "TextField", "label": "收款方名称（工商名）", "value": "测试供应商有限公司", "key": "k1"},
        {"componentType": "TextField", "label": "银行账号", "value": "6222000011112222", "key": "k2"},
        {"componentType": "TextField", "label": "对方收款所属银行", "value": "测试银行", "key": "k3"},
        {"componentType": "TextField", "label": "银行所属支行", "value": "甲城乙区支行", "key": "k4"},
        {"componentType": "TextField", "label": "对方收款银行所在省市", "value": "甲省甲城", "key": "k5"}]}]
    atts = [{"spaceId": "900", "fileName": "测试发票.pdf", "fileSize": 1234, "fileType": "pdf", "fileId": "F001"}]
    return {
        "title": "张三提交的付款申请（公对公）",
        "originator_userid": "u_zhang",
        "originator_dept_name": "测试公司-财务中心-核算部",
        "business_id": "202601020304000011111",
        "status": "COMPLETED", "result": "agree", "create_time": "2026-01-02 03:04:05",
        "form_component_values": [
            {"name": "说明", "component_type": "TextNote", "id": "t0", "value": "请如实填写"},
            {"name": "公司主体", "component_type": "DDSelectField", "id": "c1", "value": "甲城测试有限公司"},
            {"name": "付款事由", "component_type": "TextareaField", "id": "c2", "value": "支付测试服务费"},
            {"name": "付款总额", "component_type": "MoneyField", "id": "c3", "value": "1234.56"},
            {"name": "实际付款总额", "component_type": "CalculateField", "id": "c4", "value": "1,000.50"},
            {"name": "ERP订单编号", "component_type": "TextField", "id": "c5", "value": "PO-TEST-001"},
            {"name": "银行信息", "component_type": "TableField", "id": "c6", "value": json.dumps(bank_rows, ensure_ascii=False)},
            {"name": "关联相关流程审批", "component_type": "RelateField", "id": "c7", "value": '["李四提交的事项审批"]'},
            {"name": "附件", "component_type": "DDAttachment", "id": "c8", "value": json.dumps(atts, ensure_ascii=False)},
        ],
        "operation_records": [], "tasks": [],
    }


def _expense_inst():
    """费用报销——无顶层合计，明细行同时有「金额」「报销金额」（只取报销金额，不重复加）。"""
    detail = [
        {"rowValue": [{"label": "费用明细", "value": "差旅住宿"}, {"label": "金额", "value": "300"},
                      {"label": "报销金额", "value": "280.5", "componentType": "MoneyField"}]},
        {"rowValue": [{"label": "费用明细", "value": "市内交通"}, {"label": "报销金额", "value": "19.5"}]},
    ]
    bank = [{"rowValue": [{"label": "银行账户名", "value": "王五"}, {"label": "开户行", "value": "测试银行甲城分行"},
                          {"label": "银行账号", "value": "6217000099998888"}]}]
    return {
        "title": "王五提交的费用报销",
        "originator_userid": "u_wang", "originator_dept_name": "测试公司-销售中心",
        "business_id": "202602030405000022222", "status": "RUNNING", "result": "", "create_time": "2026-02-03 04:05:06",
        "form_component_values": [
            {"name": "公司主体", "component_type": "DDSelectField", "value": "乙城测试有限公司"},
            {"name": "费用类型", "component_type": "DDSelectField", "value": "差旅费"},
            {"name": "报销明细", "component_type": "TableField", "value": json.dumps(detail, ensure_ascii=False)},
            {"name": "银行信息", "component_type": "TableField", "value": json.dumps(bank, ensure_ascii=False)},
            {"name": "图片", "component_type": "DDPhotoField",
             "value": '["https://static.dingtalk.com/media/fake1.jpg","https://static.dingtalk.com/media/fake2.jpg"]'},
            {"name": "附件", "component_type": "DDAttachment", "value": "null"},
            {"name": "关联审批", "component_type": "RelateField", "value": "null"},
        ],
        "operation_records": [], "tasks": [],
    }


class Base(unittest.TestCase):
    def setUp(self):
        d._reset_caches()
        self._orig = (d.requests, d._load_conf, d.notifier)
        d._load_conf = lambda: dict(FAKE_CONF)

    def tearDown(self):
        d.requests, d._load_conf, d.notifier = self._orig
        d._reset_caches()


class TestResolveLink(Base):
    HTML = ("<!DOCTYPE html>\n<html>\n<body>\n<script>\n        var qrTargetUrl='https://aflow.dingtalk.com/dingtalk/mobile/"
            "homepage.htm?dd_share=false&showmenu=true&back=native#/approval?corpid=dingFAKECORP0001&amp;procInstId=Ab_Cd-123XyZ';\n"
            "        location.href=qrTargetUrl;\n</script>\n</body>\n</html>\n")

    def test_short_link_html(self):
        fake = FakeRequests(get_handler=lambda url, kw: FakeResp(text=self.HTML))
        d.requests = fake
        r = d.resolve_link("https://aflow.dingtalk.com/qr/FAKECODE")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["procInstId"], "Ab_Cd-123XyZ")
        self.assertEqual(r["corpId"], "dingFAKECORP0001")
        self.assertEqual(fake.calls[0][1], "https://aflow.dingtalk.com/qr/FAKECODE")

    def test_full_url_no_network(self):
        d.requests = FakeRequests(get_handler=lambda url, kw: self.fail("不该联网"))
        r = d.resolve_link("https://aflow.dingtalk.com/x#/approval?corpid=dingC1&procInstId=INST-9")
        self.assertEqual((r["ok"], r["procInstId"], r["corpId"]), (True, "INST-9", "dingC1"))
        r = d.resolve_link("https://aflow.dingtalk.com/x?procInstId%3DINST-8")
        self.assertEqual(r["procInstId"], "INST-8")

    def test_redirect_location(self):
        d.requests = FakeRequests(get_handler=lambda url, kw: FakeResp(
            status=302, headers={"Location": "https://aflow.dingtalk.com/a?corpid=dingZ&procInstId=LOC1"}))
        r = d.resolve_link("https://aflow.dingtalk.com/qr/Z")
        self.assertEqual((r["procInstId"], r["corpId"]), ("LOC1", "dingZ"))

    def test_rejects_non_dingtalk_and_empty_page(self):
        d.requests = FakeRequests(get_handler=lambda url, kw: self.fail("不该联网"))
        self.assertFalse(d.resolve_link("https://evil.example.com/qr/x")["ok"])
        self.assertFalse(d.resolve_link("https://dingtalk.com.evil.io/qr/x")["ok"])
        self.assertFalse(d.resolve_link("")["ok"])
        d.requests = FakeRequests(get_handler=lambda url, kw: FakeResp(text="<html>login</html>"))
        r = d.resolve_link("https://aflow.dingtalk.com/qr/old")
        self.assertFalse(r["ok"])
        self.assertIn("没有审批单号", r["msg"])

    def test_corp_only_from_dingtalk_host(self):
        # 外站随手拼的 ?procInstId=..&corpid=.. 不能带出企业 ID（路由会把它记成全局设置，手机免登就坏了）
        d.requests = FakeRequests(get_handler=lambda url, kw: self.fail("不该联网"))
        for u in ("https://evil.example/?procInstId=abc123&corpid=dingEVILCORP",
                  "https://aflow.dingtalk.com.evil.io/x?procInstId=abc123&corpid=dingEVILCORP",
                  "https://aflow.dingtalk.com@evil.io/x?procInstId=abc123&corpid=dingEVILCORP",
                  "procInstId=abc123&corpid=dingEVILCORP",
                  "ftp://aflow.dingtalk.com/x?procInstId=abc123&corpid=dingEVILCORP"):
            r = d.resolve_link(u)
            self.assertEqual((r["ok"], r["procInstId"], r["corpId"]), (True, "abc123", ""), u)
        r = d.resolve_link("https://aflow.dingtalk.com/x#/approval?corpid=dingC1&procInstId=INST-9")
        self.assertEqual(r["corpId"], "dingC1")

    def test_network_error_never_raises(self):
        def boom(url, kw):
            raise IOError("connection reset")
        d.requests = FakeRequests(get_handler=boom)
        r = d.resolve_link("https://aflow.dingtalk.com/qr/x")
        self.assertFalse(r["ok"])
        self.assertIn("connection reset", r["msg"])


class TestNormalize(unittest.TestCase):
    CFG = [{"name": "付款申请（公对公）", "amountFields": ["实际付款总额", "付款总额"]},
           {"name": "费用报销", "amountFields": ["报销总额", "合计金额", "报销金额", "实际报销金额"]}]

    def test_payment(self):
        n = d.normalize_instance(_payment_inst(), "INST-P", self.CFG)
        self.assertEqual(n["instId"], "INST-P")
        self.assertEqual(n["template"], "付款申请（公对公）")
        self.assertEqual(n["applicant"], "张三")
        self.assertEqual(n["applicantUid"], "u_zhang")
        self.assertEqual(n["dept"], "测试公司-财务中心-核算部")
        self.assertEqual(n["company"], "甲城测试有限公司")
        self.assertAlmostEqual(n["amount"], 1000.50)          # 按配置顺序先取「实际付款总额」，千分位也认
        self.assertEqual(n["payeeName"], "测试供应商有限公司")
        self.assertEqual(n["payeeAccount"], "6222000011112222")
        self.assertEqual(n["payeeBank"], "测试银行甲城乙区支行")  # 所属银行 + 支行；省市那格不能被当成银行
        self.assertEqual(n["reason"], "支付测试服务费")
        self.assertEqual(n["erpNo"], "PO-TEST-001")
        self.assertEqual(n["relate"], ["李四提交的事项审批"])
        self.assertEqual(n["photos"], [])
        self.assertEqual(len(n["attachments"]), 1)
        self.assertEqual(n["attachments"][0]["fileId"], "F001")
        self.assertEqual(n["attachments"][0]["label"], "附件")
        self.assertTrue(n["hasAttachments"])
        self.assertEqual((n["approvalStatus"], n["approvalResult"]), ("COMPLETED", "agree"))
        names = [f["name"] for f in n["form"]]
        self.assertNotIn("说明", names)                       # TextNote 跳过
        bank = [f for f in n["form"] if f["name"] == "银行信息"][0]
        self.assertIn("银行账号：6222000011112222", bank["value"])
        self.assertTrue(all(isinstance(f["value"], str) and len(f["value"]) <= 500 for f in n["form"]))

    def test_payment_default_amount_order(self):
        inst = _payment_inst()
        for c in inst["form_component_values"]:
            if c["name"] == "实际付款总额":
                c["value"] = "null"
        n = d.normalize_instance(inst, "X", None)             # 无配置 → 默认顺序；实际付款总额为 "null" → 取付款总额
        self.assertAlmostEqual(n["amount"], 1234.56)
        self.assertEqual(n["template"], "付款申请（公对公）")
        self.assertEqual(n["applicant"], "张三")

    def test_expense(self):
        n = d.normalize_instance(_expense_inst(), "INST-E", self.CFG)
        self.assertEqual(n["template"], "费用报销")
        self.assertEqual(n["applicant"], "王五")
        self.assertEqual(n["company"], "乙城测试有限公司")
        self.assertAlmostEqual(n["amount"], 300.0)            # 280.5 + 19.5，不把同行「金额 300」再加进去
        self.assertEqual(n["payeeName"], "王五")
        self.assertEqual(n["payeeBank"], "测试银行甲城分行")
        self.assertEqual(n["payeeAccount"], "6217000099998888")
        self.assertEqual(n["photos"], ["https://static.dingtalk.com/media/fake1.jpg",
                                       "https://static.dingtalk.com/media/fake2.jpg"])
        self.assertEqual(n["relate"], [])                     # "null" → 空
        self.assertEqual(n["attachments"], [])
        self.assertTrue(n["hasAttachments"])                  # 只有图片也算有附件
        self.assertIn("差旅住宿", n["reason"])
        photo = [f for f in n["form"] if f["name"] == "图片"][0]
        self.assertEqual(photo["value"], "2 张图片")
        att = [f for f in n["form"] if f["name"] == "附件"][0]
        self.assertEqual(att["value"], "")

    def test_garbage_does_not_raise(self):
        n = d.normalize_instance({"title": None, "form_component_values": [
            {"name": "报销明细", "component_type": "TableField", "value": "{bad json"},
            {"name": "付款总额", "component_type": "MoneyField", "value": "abc"},
            "not-a-dict"]}, "", {})
        self.assertIsNone(n["amount"])
        self.assertEqual(n["applicant"], "")
        self.assertFalse(n["hasAttachments"])
        n = d.normalize_instance(None, "Z", None)
        self.assertEqual(n["instId"], "Z")

    def test_long_value_cut(self):
        inst = _payment_inst()
        inst["form_component_values"][2]["value"] = "长" * 900
        n = d.normalize_instance(inst, "X", None)
        f = [x for x in n["form"] if x["name"] == "付款事由"][0]
        self.assertEqual(len(f["value"]), 500)


class TestWindows(unittest.TestCase):
    def test_minute_then_day(self):
        w = d.business_id_windows("202609111425000012345")
        self.assertEqual([x[2] for x in w], ["minute", "day"])
        # 2026-09-11 14:25 北京时间 = 06:25Z
        base = 1789107900000  # 2026-09-11T06:25:00Z
        self.assertEqual(w[0][0], base - 2 * 60000)
        self.assertEqual(w[0][1], base + 3 * 60000)
        day0 = 1789056000000  # 2026-09-11T00:00+08:00 = 2026-09-10T16:00Z
        self.assertEqual(w[1], (day0, day0 + 86400000 - 1, "day"))

    def test_20_digit_and_bad(self):
        self.assertEqual([x[2] for x in d.business_id_windows("20260911142500001234")], ["minute", "day"])
        self.assertEqual([x[2] for x in d.business_id_windows("20260911999900001234")], ["day"])   # 时刻不合法 → 只按天
        self.assertEqual(d.business_id_windows("20261399142500001234"), [])
        self.assertEqual(d.business_id_windows("12345"), [])
        self.assertEqual(d.business_id_windows("2026091114250000123a"), [])


class TestFindByBusinessId(Base):
    BID = "202601020304000011111"

    CAPS = ("FIND_MAX_GETS", "FIND_BUDGET_S", "DAY_LIST_MAX_PAGES")

    def setUp(self):
        Base.setUp(self)
        self._caps = {k: getattr(d, k, None) for k in self.CAPS}

    def tearDown(self):
        for k, v in self._caps.items():
            if v is None:
                if hasattr(d, k):
                    delattr(d, k)
            else:
                setattr(d, k, v)
        Base.tearDown(self)

    def _handler(self, pages_minute, pages_day, target_iid, unknown=("不存在的模板",)):
        state = {"listids": [], "get": []}

        def post(url, params, body):
            if url.endswith("/v1.0/oauth2/accessToken"):
                return FakeResp({"accessToken": "V2TOK", "expireIn": 7200})
            path = url.replace(d.OAPI, "")
            if path == "topapi/process/get_by_name":
                if body["name"] in unknown:
                    return FakeResp({"errcode": 820004, "errmsg": "process not found"})
                return FakeResp({"errcode": 0, "process_code": "PROC-" + ("PAY" if "付款" in body["name"] else "EXP")})
            if path == "topapi/processinstance/listids":
                state["listids"].append(dict(body))
                span = body["end_time"] - body["start_time"]
                pages = pages_minute if span < 3600000 else pages_day
                pages = pages.get(body["process_code"], [[]])
                idx = body["cursor"]
                res = {"list": pages[idx]}
                if idx + 1 < len(pages):
                    res["next_cursor"] = idx + 1
                return FakeResp({"errcode": 0, "result": res})
            if path == "topapi/processinstance/get":
                iid = body["process_instance_id"]
                state["get"].append(iid)
                inst = _payment_inst()
                inst["business_id"] = self.BID if iid == target_iid else "202601020304000099999"
                return FakeResp({"errcode": 0, "process_instance": inst})
            return FakeResp({"errcode": 99999, "errmsg": "unexpected " + path})

        def get(url, kw):
            return FakeResp({"errcode": 0, "access_token": "OLDTOK", "expires_in": 7200})
        return FakeRequests(post, get), state

    def test_minute_window_with_pagination(self):
        fake, st = self._handler({"PROC-PAY": [["i1", "i2"], ["i3"]]}, {}, "i3")
        d.requests = fake
        r = d.find_by_business_id(self.BID, ["不存在的模板", "付款申请（公对公）", "费用报销"])
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["procInstId"], r["template"]), ("i3", "付款申请（公对公）"))
        self.assertEqual(r["inst"]["business_id"], self.BID)
        first = st["listids"][0]
        self.assertEqual(first["end_time"] - first["start_time"], 5 * 60000)
        self.assertEqual([x["cursor"] for x in st["listids"][:2]], [0, 1])
        self.assertEqual(st["get"], ["i1", "i2", "i3"])
        # token 只取一次（缓存）
        self.assertEqual(sum(1 for c in fake.calls if c[0] == "GET"), 1)

    def test_day_fallback_skips_seen(self):
        fake, st = self._handler({"PROC-PAY": [["i1"]]}, {"PROC-EXP": [["i1", "i9"]]}, "i9")
        d.requests = fake
        r = d.find_by_business_id(self.BID, ["付款申请（公对公）", "费用报销"])
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["procInstId"], r["template"]), ("i9", "费用报销"))
        self.assertEqual(st["get"], ["i1", "i9"])            # i1 分钟窗口已看过，整天窗口不重复取
        self.assertTrue(any(x["end_time"] - x["start_time"] == 86400000 - 1 for x in st["listids"]))

    def test_day_scan_capped(self):
        # 编号打错一位：分钟窗口没有 → 整天兜底；当天 300 张单子也只逐张取 200 张就停，明说"没找到、请扫二维码"
        day = [["d%03d" % (p * 20 + i) for i in range(20)] for p in range(15)]
        fake, st = self._handler({}, {"PROC-PAY": day}, "none")
        d.requests = fake
        r = d.find_by_business_id(self.BID, ["付款申请（公对公）"])
        self.assertFalse(r["ok"])
        self.assertEqual(len(st["get"]), 200)
        self.assertEqual(d.FIND_MAX_GETS, 200)
        for s in ("没找到", "太多", "二维码", self.BID):
            self.assertIn(s, r["msg"])
        self.assertLessEqual(sum(1 for x in st["listids"] if x["end_time"] - x["start_time"] > 3600000),
                             d.DAY_LIST_MAX_PAGES)

    def test_day_listing_truncated_reported(self):
        # 当天单子多到列表都没翻完：不能说成"没有这张单"，要提示扫二维码
        d.DAY_LIST_MAX_PAGES = 2
        day = [["d%03d" % (p * 20 + i) for i in range(20)] for p in range(5)]
        fake, st = self._handler({}, {"PROC-PAY": day}, "d090")
        d.requests = fake
        r = d.find_by_business_id(self.BID, ["付款申请（公对公）"])
        self.assertFalse(r["ok"])
        self.assertIn("太多", r["msg"])
        self.assertIn("二维码", r["msg"])
        self.assertEqual(len(st["get"]), 40)

    def test_day_scan_resumes_with_cache(self):
        # 第一次查到上限停下；再扫一次：取过的不再取，接着往下查到
        d.FIND_MAX_GETS = 5
        fake, st = self._handler({}, {"PROC-PAY": [["i%d" % i for i in range(1, 9)]]}, "i8")
        d.requests = fake
        r = d.find_by_business_id(self.BID, ["付款申请（公对公）"])
        self.assertFalse(r["ok"])
        self.assertEqual(st["get"], ["i1", "i2", "i3", "i4", "i5"])
        r = d.find_by_business_id(self.BID, ["付款申请（公对公）"])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["procInstId"], "i8")
        self.assertEqual(st["get"], ["i%d" % i for i in range(1, 9)])      # 没有重复取

    def test_day_scan_time_budget(self):
        # 钉钉慢：整天兜底超时就停（分钟窗口不受影响）
        d.FIND_BUDGET_S = -1
        fake, st = self._handler({"PROC-PAY": [["m1"]]}, {"PROC-PAY": [["m1", "d1"]]}, "d1")
        d.requests = fake
        r = d.find_by_business_id(self.BID, ["付款申请（公对公）"])
        self.assertFalse(r["ok"])
        self.assertIn("响应慢", r["msg"])
        self.assertEqual(st["get"], ["m1"])
        d._reset_caches()
        fake, st = self._handler({"PROC-PAY": [["m1"]]}, {}, "m1")
        d.requests = fake
        self.assertTrue(d.find_by_business_id(self.BID, ["付款申请（公对公）"])["ok"])

    def test_not_found_and_unknown_templates(self):
        fake, st = self._handler({}, {}, "none")
        d.requests = fake
        r = d.find_by_business_id(self.BID, ["付款申请（公对公）"])
        self.assertFalse(r["ok"])
        self.assertIn("没找到", r["msg"])
        r = d.find_by_business_id(self.BID, ["不存在的模板"])
        self.assertFalse(r["ok"])
        self.assertIn("没有叫「不存在的模板」", r["msg"])
        self.assertFalse(d.find_by_business_id("123", ["费用报销"])["ok"])
        self.assertFalse(d.find_by_business_id(self.BID, [])["ok"])

    def test_not_configured(self):
        d._load_conf = lambda: None
        d.requests = FakeRequests(lambda *a: self.fail("不该联网"), lambda *a: self.fail("不该联网"))
        r = d.find_by_business_id(self.BID, ["费用报销"])
        self.assertFalse(r["ok"])
        self.assertIn("未配置钉钉", r["msg"])
        self.assertFalse(d.get_instance("X")["ok"])
        self.assertFalse(d.configured())


class TestScrubAndToken(Base):
    def test_exception_scrubbed(self):
        def get(url, kw):
            raise IOError("HTTPSConnectionPool: /gettoken?appkey=%s&appsecret=%s failed"
                          % (FAKE_CONF["appkey"], FAKE_CONF["appsecret"]))
        d.requests = FakeRequests(lambda *a: self.fail("不该到这"), get)
        r = d.get_instance("INST-1")
        self.assertFalse(r["ok"])
        self.assertNotIn(FAKE_CONF["appkey"], r["msg"])
        self.assertNotIn(FAKE_CONF["appsecret"], r["msg"])

    def test_access_token_scrubbed(self):
        def post(url, params, body):
            raise IOError("POST %s?access_token=OLDTOK123 timed out" % url)
        d.requests = FakeRequests(post, lambda url, kw: FakeResp({"errcode": 0, "access_token": "OLDTOK123", "expires_in": 7200}))
        r = d.get_instance("INST-1")
        self.assertFalse(r["ok"])
        self.assertNotIn("OLDTOK123", r["msg"])

    def test_token_retry_on_expired(self):
        seq = {"get": 0, "post": 0}

        def get(url, kw):
            seq["get"] += 1
            return FakeResp({"errcode": 0, "access_token": "T%d" % seq["get"], "expires_in": 7200})

        def post(url, params, body):
            seq["post"] += 1
            if params.get("access_token") == "T1":
                return FakeResp({"errcode": 40014, "errmsg": "invalid access_token"})
            return FakeResp({"errcode": 0, "process_instance": {"title": "甲提交的费用报销"}})
        d.requests = FakeRequests(post, get)
        r = d.get_instance("INST-1")
        self.assertTrue(r["ok"], r)
        self.assertEqual(seq, {"get": 2, "post": 2})
        d.get_instance("INST-2")
        self.assertEqual(seq["get"], 2)                       # 新 token 已缓存

    def test_userinfo_by_code(self):
        def post(url, params, body):
            self.assertTrue(url.endswith("topapi/v2/user/getuserinfo"))
            self.assertEqual(body, {"code": "AUTHCODE"})
            return FakeResp({"errcode": 0, "result": {"userid": "u1", "name": "赵六"}})
        d.requests = FakeRequests(post, lambda url, kw: FakeResp({"errcode": 0, "access_token": "T", "expires_in": 7200}))
        self.assertEqual(d.userinfo_by_code("AUTHCODE"), {"ok": True, "userid": "u1", "name": "赵六", "msg": ""})
        self.assertFalse(d.userinfo_by_code("")["ok"])


class FakeNotifier(object):
    def __init__(self):
        self.sent = []
        self.roster_calls = 0

    def load_dingtalk_conf(self):
        return None

    def send_dingtalk_to(self, userids, text, conf=None):
        self.sent.append((userids, text))
        return {"sent": True, "via": "robot"}

    def dt_roster(self, conf=None):
        self.roster_calls += 1
        return {"ok": True, "people": [{"userid": "u1", "name": "赵六", "title": "会计", "dept": "核算部"}]}


class TestSendAndRoster(Base):
    def test_send_not_configured_never_sends(self):
        fn = FakeNotifier()
        d.notifier = fn
        d._load_conf = lambda: None
        d.requests = FakeRequests(lambda *a: self.fail("不该联网"), lambda *a: self.fail("不该联网"))
        r = d.send_text(["u1"], "测试")
        self.assertFalse(r["sent"])
        self.assertIn("未配置", r["msg"])
        self.assertEqual(fn.sent, [])
        self.assertFalse(d.send_text([], "x")["sent"])

    def test_send_configured_uses_notifier(self):
        fn = FakeNotifier()
        d.notifier = fn
        d.requests = FakeRequests()
        r = d.send_text(["u1", "u1", ""], "提醒")
        self.assertTrue(r["sent"])
        self.assertEqual(fn.sent, [(["u1"], "提醒")])

    def test_send_exception_scrubbed(self):
        class Boom(FakeNotifier):
            def send_dingtalk_to(self, userids, text, conf=None):
                raise RuntimeError("key=%s" % FAKE_CONF["appsecret"])
        d.notifier = Boom()
        d.requests = FakeRequests()
        r = d.send_text(["u1"], "x")
        self.assertFalse(r["sent"])
        self.assertNotIn(FAKE_CONF["appsecret"], r["msg"])

    def test_roster_cache(self):
        fn = FakeNotifier()
        d.notifier = fn
        d.requests = FakeRequests()
        self.assertEqual(d.roster()["rows"][0]["name"], "赵六")
        d.roster()
        self.assertEqual(fn.roster_calls, 1)
        d.roster(fresh=True)
        self.assertEqual(fn.roster_calls, 2)


class TestDownloads(Base):
    def test_photo_host_whitelist(self):
        d.requests = FakeRequests(get_handler=lambda url, kw: FakeResp(content=b"JPEGDATA"))
        r = d.download_photo("https://static.dingtalk.com/media/x.jpg")
        self.assertEqual((r["ok"], r["bytes"]), (True, b"JPEGDATA"))
        self.assertTrue(d.download_photo("http://img.alicdn.com/a.png")["ok"])     # http 升级成 https
        for bad in ("https://127.0.0.1/a.jpg", "https://dingtalk.com.evil.io/a.jpg", "ftp://static.dingtalk.com/a",
                    "file:///etc/passwd", ""):
            self.assertFalse(d.download_photo(bad)["ok"], bad)

    def test_photo_redirect_checked(self):
        def get(url, kw):
            if "dingtalk" in url:
                return FakeResp(status=302, headers={"Location": "http://10.0.0.1/secret"})
            self.fail("不该访问内网")
        d.requests = FakeRequests(get_handler=get)
        self.assertFalse(d.download_photo("https://static.dingtalk.com/a.jpg")["ok"])

    def test_photo_size_cap(self):
        d.requests = FakeRequests(get_handler=lambda url, kw: FakeResp(headers={"Content-Length": str(d.PHOTO_CAP + 1)}))
        r = d.download_photo("https://static.dingtalk.com/a.jpg")
        self.assertFalse(r["ok"])
        self.assertIn("太大", r["msg"])

    def test_attachment_via_bom_and_storage_fallback(self):
        class FakeBom(object):
            def __init__(self):
                self.storage = 0

            def _scrub(self, msg, ak=None, sk=None):
                return d._bom._scrub(msg, ak, sk)

            def download_url(self, tv2, told, iid, fid):
                return None, "400 用户不存在"

            def storage_download(self, tv2, told, iid, fid, space, inst):
                self.storage += 1
                return "https://down.dingtalk.com/f", {"h": "1"}, "storage(代下载·某人)"

            def collect_attachments(self, inst):
                return []
        orig_bom = d._bom
        fb = FakeBom()
        d._bom = fb
        try:
            def post(url, params, body):
                return FakeResp({"accessToken": "V2", "expireIn": 7200})

            def get(url, kw):
                if "gettoken" in url:
                    return FakeResp({"errcode": 0, "access_token": "OLD", "expires_in": 7200})
                return FakeResp(content=b"%PDF-fake")
            d.requests = FakeRequests(post, get)
            r = d.download_attachment("INST", {"fileId": "F1", "spaceId": "9", "fileSize": 10}, {})
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["bytes"], b"%PDF-fake")
            self.assertIn("storage", r["via"])
            self.assertEqual(fb.storage, 1)
            big = d.download_attachment("INST", {"fileId": "F1", "fileSize": d.ATTACH_CAP + 1}, {})
            self.assertFalse(big["ok"])
            self.assertFalse(d.download_attachment("INST", {}, {})["ok"])
        finally:
            d._bom = orig_bom


@unittest.skipUnless(os.environ.get("INV_DT_LIVE") == "1", "真钉钉只读冒烟：设 INV_DT_LIVE=1 且 KD_CONF_PATH 指向真 conf 才跑")
class TestLiveReadOnly(unittest.TestCase):
    def test_link_and_business_id(self):
        link = os.environ.get("INV_DT_LINK", "")
        bid = os.environ.get("INV_DT_BID", "")
        if link:
            r = d.resolve_link(link)
            self.assertTrue(r["ok"], r["msg"])
            g = d.get_instance(r["procInstId"])
            self.assertTrue(g["ok"], g["msg"])
        if bid:
            f = d.find_by_business_id(bid, ["付款申请（公对公）", "费用报销"])
            self.assertTrue(f["ok"], f["msg"])


if __name__ == "__main__":
    unittest.main()
