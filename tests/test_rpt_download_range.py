# [Change Log] Date: 2026-10-23 | Author: Kimi / Reginald Zeng | Version: V2.1001
# V2.1001 报表下载接口 Range 断点续传：_parse_range 解析的正确性闸。
import importlib.util
import sys
import types
import unittest
from pathlib import Path

from starlette.responses import JSONResponse

BACKEND = Path(__file__).resolve().parents[1] / "01_Current_Deliverables/app/backend"
sys.path.insert(0, str(BACKEND))

# 路由模块的 core 依赖用桩替掉（只测纯函数，不拉起整个后端）
core = types.ModuleType("core")
core.JSONResponse = JSONResponse
core.db = types.SimpleNamespace()
import datetime as _dt  # noqa: E402
core.datetime = _dt
core.pull_token = lambda: ""
core.pull_token_ok = lambda r: False
core._require_perm = lambda *a, **k: None
sys.modules.setdefault("core", core)

spec = importlib.util.spec_from_file_location("rptexport_route", BACKEND / "routers/rptexport.py")
rx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rx)


class ParseRangeTests(unittest.TestCase):
    def test_no_header_means_full_file(self):
        self.assertIsNone(rx._parse_range(None, 1000))
        self.assertIsNone(rx._parse_range("", 1000))

    def test_valid_resume_offset(self):
        self.assertEqual(rx._parse_range("bytes=100-", 1000), 100)

    def test_zero_or_out_of_bounds_falls_back_to_full(self):
        self.assertIsNone(rx._parse_range("bytes=0-", 1000))        # 从头=整文件
        self.assertIsNone(rx._parse_range("bytes=1000-", 1000))     # 越界
        self.assertIsNone(rx._parse_range("bytes=5000-", 1000))

    def test_garbage_ignored(self):
        self.assertIsNone(rx._parse_range("bytes=abc-", 1000))
        self.assertIsNone(rx._parse_range("something-else", 1000))
        self.assertIsNone(rx._parse_range("bytes=-100", 1000))      # 不支持尾段形式


if __name__ == "__main__":
    unittest.main()
