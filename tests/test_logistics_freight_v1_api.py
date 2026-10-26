# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-26 | Author: Kimi | Version: V-draft（逐单运费 V1）
# Description: 开发夹批次 API 测试；临时 SQLite、内存 Excel、桩权限，不读生产库、不连金蝶。
import hashlib
import sys
import tempfile
import types
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "app" / "backend"
if not BACKEND.exists():
    BACKEND = ROOT / "01_Current_Deliverables" / "app" / "backend"
sys.path.insert(0, str(BACKEND))

from kernels import logistics_freight_v1_store as store


def workbook(doc="D1", amount=10, marker="有效", carrier=None, rich=False):
    wb = Workbook()
    ws = wb.active
    ws.title = "销售成品装货明细"
    ws.append(["标题"])
    extra = ["客户", "价税合计", "销售基本数量", "库存单位"] if rich else []
    ws.append(["含税金额（18.5元/吨）", "物料编码", "金蝶单据编号"] + (["物流商"] if carrier else []) + extra)
    ws.append([amount, "M1", doc if marker == "有效" else None] + ([carrier] if carrier else []) +
              (["客户甲", 1000, 2, "袋"] if rich else []))
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audit = []
        fake_db = types.SimpleNamespace(audit=lambda *args: cls.audit.append(args), permissions=set(),
                                        list_logi_suppliers=lambda: [{"short": "天鹰", "full": "天鹰物流"}])
        core = types.ModuleType("core")
        core.JSONResponse = __import__("fastapi.responses", fromlist=["JSONResponse"]).JSONResponse
        core.db = fake_db
        core._require_perm = lambda _request, cap: ({"name": "测试员"} if cap in fake_db.permissions else None)
        review = types.ModuleType("routers.logistics_review")
        review._bp_mapping = lambda: ({("客户甲", "M1"): {"brand": "品牌甲"}}, {}, "")
        with patch.dict(sys.modules, {"core": core, "routers.logistics_review": review}):
            from routers import logistics_freight_v1
        cls.router_module = logistics_freight_v1
        cls.fake_db = fake_db
        app = FastAPI()
        app.include_router(logistics_freight_v1.router)
        cls.client = TestClient(app)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine("sqlite:///" + self.temp.name.replace("\\", "/") + "/test.db",
                                    connect_args={"check_same_thread": False})
        store.md.create_all(self.engine)
        self.fake_db._engine = self.engine
        self.fake_db.permissions = {"logistics_upload", "logistics_freight_write"}
        self.audit.clear()

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def preview(self, data=None, doc="D1", amount=10, operation="new", replace_id=None, marker="有效"):
        form = {"operation": operation, "period": "2026-09", "carrier": "承运甲", "subject": "主体甲", "biz_line": "小料"}
        if replace_id:
            form["replace_id"] = str(replace_id)
        return self.client.post("/api/logistics-freight/v1/preview", data=form,
                                files={"files": ("合成.xlsx", data if data is not None else workbook(doc, amount, marker),
                                                 "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})

    def test_read_and_write_permissions_are_separate(self):
        self.fake_db.permissions = set()
        self.assertEqual(self.client.get("/api/logistics-freight/v1/batches").status_code, 403)
        self.fake_db.permissions = {"logistics_upload"}
        self.assertEqual(self.client.get("/api/logistics-freight/v1/batches").status_code, 200)
        self.assertEqual(self.preview().status_code, 403)

    def test_multifile_preview_creates_independent_drafts_and_failed_original(self):
        response = self.client.post("/api/logistics-freight/v1/preview",
                                    data={"period": "2026-09", "carrier": "承运甲", "subject": "主体甲"},
                                    files=[("files", ("good.xlsx", workbook(), "application/octet-stream")),
                                           ("files", ("bad.xlsx", b"not excel", "application/octet-stream"))])
        self.assertEqual(response.status_code, 200, response.text)
        batches = response.json()["batches"]
        self.assertEqual([x["status"] for x in batches], ["draft", "failed"])
        self.assertEqual(len(batches), 2)
        self.assertEqual(batches[0]["summary"]["line_count"], 1)
        self.assertEqual(batches[1]["summary"]["error_count"], 1)
        original = self.client.get("/api/logistics-freight/v1/batches/%d/file" % batches[1]["id"])
        self.assertEqual(original.content, b"not excel")
        self.assertEqual(original.headers["x-content-sha256"], hashlib.sha256(b"not excel").hexdigest())

    def test_confirm_overlap_withdraw_restore_and_file(self):
        content = workbook()
        first = self.preview(data=content).json()["batches"][0]
        confirmed = self.client.post("/api/logistics-freight/v1/batches/%d/confirm" % first["id"], json={})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(confirmed.json()["batch"]["status"], "active")
        second = self.preview(amount=20).json()["batches"][0]
        self.assertEqual(second["summary"]["overlap_count"], 1)
        blocked = self.client.post("/api/logistics-freight/v1/batches/%d/confirm" % second["id"], json={})
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["overlaps"][0]["doc_no"], "D1")
        allowed = self.client.post("/api/logistics-freight/v1/batches/%d/confirm" % second["id"],
                                   json={"allow_overlap": True})
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(self.client.post("/api/logistics-freight/v1/batches/%d/withdraw" % first["id"],
                                         json={"reason": ""}).status_code, 400)
        withdrawn = self.client.post("/api/logistics-freight/v1/batches/%d/withdraw" % first["id"],
                                     json={"reason": "账单版本错误"})
        self.assertEqual(withdrawn.json()["batch"]["status"], "withdrawn")
        restored = self.client.post("/api/logistics-freight/v1/batches/%d/restore" % first["id"],
                                    json={"allow_overlap": True})
        self.assertEqual(restored.json()["batch"]["status"], "active")
        downloaded = self.client.get("/api/logistics-freight/v1/batches/%d/file" % first["id"])
        self.assertEqual(downloaded.content, content)
        self.assertEqual(len(downloaded.headers["x-content-sha256"]), 64)
        self.assertGreaterEqual(len(self.audit), 6)

    def test_replacement_is_atomic_when_other_active_overlap_blocks(self):
        base = self.preview(doc="BASE").json()["batches"][0]
        self.client.post("/api/logistics-freight/v1/batches/%d/confirm" % base["id"], json={})
        other = self.preview(doc="OTHER").json()["batches"][0]
        self.client.post("/api/logistics-freight/v1/batches/%d/confirm" % other["id"], json={})
        replacement = self.preview(doc="OTHER", operation="replace", replace_id=base["id"]).json()["batches"][0]
        self.assertEqual(replacement["summary"]["overlap_count"], 1)
        failed = self.client.post("/api/logistics-freight/v1/batches/%d/confirm" % replacement["id"], json={})
        self.assertEqual(failed.status_code, 409)
        old = self.client.get("/api/logistics-freight/v1/batches/%d" % base["id"]).json()["batch"]
        new = self.client.get("/api/logistics-freight/v1/batches/%d" % replacement["id"]).json()["batch"]
        self.assertEqual(old["status"], "active")
        self.assertIsNone(old["replaced_by"])
        self.assertEqual(new["status"], "draft")
        confirmed = self.client.post("/api/logistics-freight/v1/batches/%d/confirm" % replacement["id"],
                                     json={"allow_overlap": True})
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(self.client.get("/api/logistics-freight/v1/batches/%d" % base["id"]).json()["batch"]["status"],
                         "replaced")

    def test_successful_replacement_and_list_detail(self):
        base = self.preview(doc="OLD").json()["batches"][0]
        self.client.post("/api/logistics-freight/v1/batches/%d/confirm" % base["id"], json={})
        replacement = self.preview(doc="NEW", operation="replace", replace_id=base["id"]).json()["batches"][0]
        response = self.client.post("/api/logistics-freight/v1/batches/%d/confirm" % replacement["id"], json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["batch"]["status"], "active")
        listing = self.client.get("/api/logistics-freight/v1/batches").json()["batches"]
        self.assertEqual({x["status"] for x in listing}, {"active", "replaced"})
        detail = self.client.get("/api/logistics-freight/v1/batches/%d" % replacement["id"]).json()["batch"]
        self.assertEqual(detail["lines"][0]["doc_no"], "NEW")
        self.assertEqual(detail["lines"][0]["raw"]["金蝶单据编号"], "NEW")

    def test_preview_business_fields_and_collect_on_delivery(self):
        response = self.client.post("/api/logistics-freight/v1/preview",
                                    data={"period": "2026-09", "subject": "孝感星期九"},
                                    files={"files": ("9月销售.xlsx", workbook(carrier="到付", rich=True), "application/octet-stream")})
        draft = response.json()["batches"][0]
        line = draft["lines"][0]
        self.assertEqual(line["brand"], "品牌甲")
        self.assertEqual(line["customer"], "客户甲")
        self.assertEqual(line["sales_amount_tax"], "1000.00")
        self.assertEqual(line["quantity"], "2.000000")
        self.assertEqual(line["quantity_unit"], "袋")
        self.assertEqual(line["carrier"], "到付")
        self.assertEqual(draft["summary"]["status"], "ready")

    def test_unique_keyword_normalizes_before_draft_is_saved(self):
        response = self.client.post("/api/logistics-freight/v1/preview",
                                    data={"period": "2026-09", "subject": "孝感星期九"},
                                    files={"files": ("天鹰.xlsx", workbook(carrier="天鹰货运"), "application/octet-stream")})
        draft = response.json()["batches"][0]
        self.assertEqual(draft["lines"][0]["carrier"], "天鹰")
        self.assertEqual(draft["lines"][0]["raw"]["物流商"], "天鹰货运")
        self.assertEqual(draft["summary"]["status"], "ready")
        self.assertTrue(any(x["code"] == "carrier_normalized" for x in draft["issues"]))

    def test_supplement_missing_carrier_in_preview(self):
        response = self.client.post("/api/logistics-freight/v1/preview",
                                    data={"period": "2026-09", "subject": "孝感星期九"},
                                    files={"files": ("天鹰.xlsx", workbook(), "application/octet-stream")})
        draft = response.json()["batches"][0]
        self.assertEqual(draft["summary"]["status"], "blocked")
        self.assertTrue(any(x["code"] == "missing_carrier" for x in draft["issues"]))
        response = self.client.post("/api/logistics-freight/v1/batches/%d" % draft["id"],
                                    json={"operation": "supplement", "carrier": "天鹰"})
        self.assertEqual(response.status_code, 200, response.text)
        supplemented = response.json()["batch"]
        self.assertEqual(supplemented["summary"]["status"], "ready")
        self.assertEqual(supplemented["lines"][0]["carrier"], "天鹰")
        confirmed = self.client.post("/api/logistics-freight/v1/batches/%d/confirm" % draft["id"], json={})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)

    def test_carrier_catalog_and_bulk_assign_are_atomic(self):
        catalog = self.client.get("/api/logistics-freight/v1/carriers").json()["carriers"]
        self.assertEqual(catalog, [{"name": "天鹰", "full_name": "天鹰物流"}])
        ids = []
        for name in ("A", "B"):
            response = self.client.post("/api/logistics-freight/v1/preview",
                                        data={"period": "2026-09", "subject": "孝感星期九"},
                                        files={"files": (name + ".xlsx", workbook(), "application/octet-stream")})
            ids.append(response.json()["batches"][0]["id"])
        response = self.client.post("/api/logistics-freight/v1/bulk-supplement",
                                    json={"batch_ids": ids, "carrier": "天鹰"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(all(x["carrier"] == "天鹰" and x["summary"]["status"] == "ready"
                            for x in response.json()["batches"]))
        self.client.post("/api/logistics-freight/v1/batches/%d/confirm" % ids[0], json={})
        rejected = self.client.post("/api/logistics-freight/v1/bulk-supplement",
                                    json={"batch_ids": ids, "carrier": "跨越"})
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(store.get_batch(self.engine, ids[1], False)["carrier"], "天鹰")

    def test_wrong_period_is_blocked_until_explicit_override(self):
        response = self.client.post("/api/logistics-freight/v1/preview",
                                    data={"period": "2026-08", "subject": "孝感星期九"},
                                    files={"files": ("9月份天鹰.xlsx", workbook(carrier="天鹰"), "application/octet-stream")})
        draft = response.json()["batches"][0]
        self.assertEqual(draft["summary"]["status"], "blocked")
        self.assertTrue(any(x["code"] == "period_mismatch" for x in draft["issues"]))
        rejected = self.client.post("/api/logistics-freight/v1/batches/%d/confirm" % draft["id"], json={})
        self.assertEqual(rejected.status_code, 409)
        response = self.client.post("/api/logistics-freight/v1/batches/%d" % draft["id"],
                                    json={"operation": "period_override"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["batch"]["summary"]["status"], "ready")
        confirmed = self.client.post("/api/logistics-freight/v1/batches/%d/confirm" % draft["id"], json={})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)

    def test_cancel_draft_and_period_filter(self):
        draft = self.preview().json()["batches"][0]
        self.assertEqual(draft["period"], "2026-09")
        response = self.client.post("/api/logistics-freight/v1/batches/%d" % draft["id"], json={"operation": "cancel"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["batch"]["status"], "withdrawn")
        self.assertEqual(self.client.get("/api/logistics-freight/v1/batches?period=2026-10").json()["batches"], [])
        self.assertEqual(len(self.client.get("/api/logistics-freight/v1/batches?period=2026-09").json()["batches"]), 1)

    def test_replace_requires_existing_active_target(self):
        response = self.preview(operation="replace", replace_id=999)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get("/api/logistics-freight/v1/batches").json()["batches"], [])


if __name__ == "__main__":
    unittest.main()
