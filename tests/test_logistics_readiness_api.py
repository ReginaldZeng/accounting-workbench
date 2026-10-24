# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-24 | Author: Kimi | Version: V2.1004
# Description: 物流 readiness/sign 与金蝶异常传播接口测试；全部使用合成数据和桩，不连接真实数据库、金蝶或钉钉。
import asyncio
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "01_Current_Deliverables" / "app" / "backend"
sys.path.insert(0, str(BACKEND))


class _Conn:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.writes = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, stmt, *args, **kwargs):
        self.writes.append(stmt)
        row = self.rows.pop(0) if self.rows else []
        return _Result(row)


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class _Engine:
    def __init__(self):
        self.rows = []
        self.connections = []

    def _conn(self):
        c = _Conn(self.rows)
        self.connections.append(c)
        return c

    def connect(self):
        return self._conn()

    def begin(self):
        return self._conn()


class _Request:
    def __init__(self, body=None):
        self.body = body or {}

    async def json(self):
        return self.body


def _load_module():
    engine = _Engine()
    db = types.SimpleNamespace(_engine=engine)
    core = types.ModuleType("core")
    core.JSONResponse = JSONResponse
    core._require_perm = lambda *_: {"name": "测试"}
    core.db = db
    kc = types.ModuleType("kingdee_client")
    kc.login = lambda: (object(), {})
    kc._query = lambda *args: []
    kc.GL_VOUCHER_FIELDS = []
    todo = types.ModuleType("todo_scenes")
    todo.fix_touch = lambda *args: None
    with patch.dict(sys.modules, {"core": core, "kingdee_client": kc, "todo_scenes": todo}):
        from routers import logistics_review as lr
    return lr, engine


class ReadinessApiTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.lr, cls.engine = _load_module()

    def setUp(self):
        self.lr._ACCR_CACHE.clear()
        self.engine.rows = []

    async def test_sign_uses_readiness_and_snapshots_summary(self):
        steps = {k: {"status": "ok", "label": k, "count": 1, "blockers": []} for k in ("intake", "lines", "docs", "sign")}
        ready = {"ok": True, "carrier": "甲", "period": "2026-09", "mode": "normal", "steps": steps,
                 "can_sign": True, "blockers": [], "signed": None}
        lines = {"accr_total": 100, "bill_total": 100, "diff_total": 0, "n_unexplained": 0, "bill_src": "detail"}
        with patch.object(self.lr, "_perm", return_value={"name": "测试"}), \
             patch.object(self.lr, "_signed", return_value=None), \
             patch.object(self.lr, "_collect_readiness", return_value=(ready, lines, {"req_total": 0})) as collect:
            out = await self.lr.review_sign_month(_Request({"carrier": "甲", "period": "2026-09", "note": "已核"}))
        self.assertTrue(out["ok"])
        collect.assert_called_once()
        self.assertEqual(collect.call_args.args[-1], "已核")
        snap = json.loads(out["signed"]["snap_json"])
        self.assertTrue(snap["readiness"]["can_sign"])
        self.assertEqual(snap["readiness"]["steps"], steps)

    async def test_sign_returns_409_with_steps_and_does_not_write(self):
        steps = {k: {"status": "ok", "label": k, "count": 0, "blockers": []} for k in ("intake", "docs", "sign")}
        steps["lines"] = {"status": "pending", "label": "登记制汇总已就绪", "count": 1, "blockers": ["差异必须写 note"]}
        ready = {"mode": "register", "steps": steps, "can_sign": False, "blockers": ["差异必须写 note"]}
        with patch.object(self.lr, "_perm", return_value={"name": "测试"}), \
             patch.object(self.lr, "_signed", return_value=None), \
             patch.object(self.lr, "_collect_readiness", return_value=(ready, None, {"req_total": 100})):
            out = await self.lr.review_sign_month(_Request({"carrier": "甲", "period": "2026-09", "note": ""}))
        self.assertIsInstance(out, JSONResponse)
        self.assertEqual(out.status_code, 409)
        body = json.loads(out.body)
        self.assertFalse(body["ok"])
        self.assertEqual(body["steps"], steps)
        self.assertFalse(any(c.writes for c in self.engine.connections))

    async def test_readiness_endpoint_returns_shared_contract(self):
        ready = {"ok": True, "carrier": "甲", "period": "2026-09", "mode": "normal", "steps": {},
                 "can_sign": False, "blockers": [], "signed": None}
        with patch.object(self.lr, "_perm", return_value={"name": "测试"}), \
             patch.object(self.lr, "_collect_readiness", return_value=(ready, None, {})):
            out = await self.lr.review_readiness(_Request(), carrier="甲", period="2026-09")
        self.assertEqual(out, ready)

    async def test_register_comparison_aggregates_requests_by_subject(self):
        overview = {"ok": True, "rows": [{"short": "甲", "cells": {"深圳星期零": {
            "accr": 100, "reqs": [{"amount": 60, "st": {"key": "approved"}},
                                   {"amount": 40, "st": {"key": "approved"}}]}}}]}
        with patch.object(self.lr, "review_overview", return_value=overview):
            data, total = self.lr._register_readiness(_Request(), "甲", "2026-09")
        self.assertEqual(total, 100)
        self.assertEqual(data["cells"][0]["diff"], 0)
        self.assertTrue(data["cells"][0]["has_accr"])


class FailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lr, cls.engine = _load_module()

    def setUp(self):
        self.lr._ACCR_CACHE.clear()
        self.engine.rows = []

    def test_accrual_login_failure_is_explicit(self):
        with patch.object(self.lr.kc, "login", side_effect=RuntimeError("连接超时")):
            with self.assertRaisesRegex(RuntimeError, "金蝶读取逐笔计提失败.*连接超时"):
                self.lr._accr_entries_raw("甲", "2026-09")

    def test_lines_failure_returns_502_and_does_not_cache(self):
        with patch.object(self.lr, "_perm", return_value={"name": "测试"}), \
             patch.object(self.lr, "_build_lines", side_effect=RuntimeError("金蝶失败")):
            out = self.lr.review_lines(_Request(), "甲", "2026-09")
        self.assertEqual(out.status_code, 502)
        self.assertIn("金蝶失败", json.loads(out.body)["msg"])
        self.assertNotIn(("lines", "甲", "2026-09"), self.lr._ACCR_CACHE)

    def test_box_docs_material_failure_propagates(self):
        row = {"doc_no": "XSCKD1"}
        with patch.object(self.lr.kc, "login", side_effect=RuntimeError("物料服务不可用")):
            with self.assertRaisesRegex(RuntimeError, "金蝶读取单据物料失败"):
                self.lr._box_docs([row], "丰源")
        with patch.object(self.lr.kc, "login", return_value=(object(), {})), \
             patch.object(self.lr.kc, "_query", side_effect=RuntimeError("查询失败")):
            with self.assertRaisesRegex(RuntimeError, "金蝶查询SAL_OUTSTOCK物料失败"):
                self.lr._box_docs([row], "丰源")

    def test_result_material_failure_is_502_and_not_cached(self):
        self.engine.rows = [[{"id": 1, "doc_no": "XSCKD1", "grain": "detail", "amount": 10, "qty": 1}], [], []]
        with patch.object(self.lr, "_perm", return_value={"name": "测试"}), \
             patch.object(self.lr, "_load_card", return_value=({}, 0)), \
             patch.object(self.lr, "_doc_ok", return_value={}), \
             patch.object(self.lr, "_code_maps", return_value={}), \
             patch.object(self.lr, "_box_docs", side_effect=RuntimeError("金蝶读取单据物料失败")):
            out = self.lr.review_result(_Request(), carrier="丰源", period="2026-09", group="all")
        self.assertEqual(out.status_code, 502)
        self.assertIn("物料", json.loads(out.body)["msg"])
        self.assertNotIn(("view", "丰源", "2026-09"), self.lr._ACCR_CACHE)


if __name__ == "__main__":
    unittest.main()
