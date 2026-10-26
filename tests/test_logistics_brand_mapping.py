# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-26 | Author: Kimi Code | Version: V-draft
# Description: BP 客户物料映射返回结构、字段别名及空映射诊断测试；只读模拟，不访问外部服务。
import json
import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "app" / "backend"
if not BACKEND.exists():
    BACKEND = ROOT / "01_Current_Deliverables" / "app" / "backend"
sys.path.insert(0, str(BACKEND))

from routers import logistics_review as lrv


class BrandMappingTests(unittest.TestCase):
    def setUp(self):
        lrv._BPMAP_CACHE.clear()

    def test_wrapped_rows_and_snake_case_fields(self):
        payload = {"items": [{"material_code": "M1", "customer_name": "客户甲", "brand_name": "品牌甲", "status": "启用"}]}
        with patch("urllib.request.urlopen", return_value=BytesIO(json.dumps(payload, ensure_ascii=False).encode())):
            combo, mat, err = lrv._bp_mapping(fresh=True)
        self.assertEqual(err, "")
        self.assertEqual(combo[("客户甲", "M1")]["brand"], "品牌甲")
        self.assertEqual(mat["M1"]["brand"], "品牌甲")

    def test_rows_envelope_and_disabled_rows(self):
        payload = {"rows": [{"materialCode": "M2", "customerName": "客户乙", "brand": "品牌乙", "status": "已停用"}]}
        with patch("urllib.request.urlopen", return_value=BytesIO(json.dumps(payload, ensure_ascii=False).encode())):
            combo, mat, err = lrv._bp_mapping(fresh=True)
        self.assertEqual((combo, mat), ({}, {}))
        self.assertIn("为空或未启用", err)

    def test_plain_array_still_works(self):
        payload = [{"materialCode": "M3", "customerName": "客户丙", "brand": "品牌丙"}]
        with patch("urllib.request.urlopen", return_value=BytesIO(json.dumps(payload, ensure_ascii=False).encode())):
            combo, mat, err = lrv._bp_mapping(fresh=True)
        self.assertEqual(err, "")
        self.assertEqual(mat["M3"]["brand"], "品牌丙")


if __name__ == "__main__":
    unittest.main()
