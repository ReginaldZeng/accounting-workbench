# [Change Log]
# Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家）
# Description: 发票管家菜单与权限登记的静态核对——不 import app（它会建库、起埋点线程、跑迁移）：
#   用 ast 字面量读 app.py 的 NAV_SECTIONS / NAV_MODULES / NAV_POST_TEMPLATES_DEFAULT / _INV_TPL_ADD，
#   db 用临时 SQLite 导入后核 CAP_META_STATIC 与 default_perms；再按源码文本核登录门例外与路由注册顺序。
#   运行：repo 根目录 PYTHONPATH=01_Current_Deliverables/app/backend python -m unittest tests.test_inv_nav_perms -v
import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "01_Current_Deliverables" / "app" / "backend"
sys.path.insert(0, str(BACKEND))

INV_MODS = {"invdesk", "invlater", "invaudit", "invledger"}
INV_CAPS = {"inv_intake": ("invdesk", False), "inv_receive": ("invlater", False), "inv_deduct": ("invledger", False),
            "inv_audit": ("invaudit", True), "inv_unbind": ("invledger", True), "inv_opening": ("invledger", True),
            "inv_config": ("invlater", True)}


def _literals(src, names):
    out = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in names:
                out[node.targets[0].id] = ast.literal_eval(node.value)
    return out


class InvNavPermTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = (BACKEND / "app.py").read_text(encoding="utf-8")
        cls.lit = _literals(cls.src, {"NAV_SECTIONS", "NAV_MODULES", "NAV_POST_TEMPLATES_DEFAULT", "_INV_TPL_ADD"})
        cls.tmp = tempfile.TemporaryDirectory(prefix="inv-nav-")
        cls.prev_env = os.environ.get("DB_URL")
        os.environ["DB_URL"] = "sqlite:///" + (Path(cls.tmp.name) / "nav.sqlite").as_posix()
        cls.saved = {k: sys.modules.get(k) for k in ("db",)}
        sys.modules.pop("db", None)
        import db
        cls.db = db

    @classmethod
    def tearDownClass(cls):
        cls.db._engine.dispose()
        for k, v in cls.saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        if cls.prev_env is None:
            os.environ.pop("DB_URL", None)
        else:
            os.environ["DB_URL"] = cls.prev_env
        cls.tmp.cleanup()

    def test_section_and_modules(self):
        secs = {s["key"]: s for s in self.lit["NAV_SECTIONS"]}
        self.assertEqual(secs["inv"]["label"], "发票管家")
        self.assertEqual(secs["inv"]["order"], 55)
        self.assertLess(secs["ar"]["order"], 55)
        self.assertLess(55, secs["misc"]["order"])
        mods = {m["key"]: m for m in self.lit["NAV_MODULES"]}
        want = {"invdesk": ("收票工作台", 10), "invlater": ("发票后补池", 20), "invaudit": ("发票审核", 30),
                "invledger": ("发票台账", 40)}
        for k, (label, order) in want.items():
            m = mods[k]
            self.assertEqual((m["label"], m["sec"], m["order"], m["default"]), (label, "inv", order, "开发中"))
            # 叶子：不挂父项、不是纯分组、不复用别的准入点 → 自动生成 enter:<key>
            for bad in ("parent", "group_only", "cap", "always"):
                self.assertNotIn(bad, m, (k, bad))
        keys = [m["key"] for m in self.lit["NAV_MODULES"]]
        self.assertEqual(len(keys), len(set(keys)), "菜单 key 重复")

    def test_caps_registered(self):
        meta = {c["key"]: c for c in self.db.CAP_META_STATIC}
        for key, (mod, sensitive) in INV_CAPS.items():
            c = meta[key]
            self.assertEqual((c["ws"], c["group"], c["tier"], c["mod"]), ("accounting", "发票管家", "act", mod), key)
            self.assertEqual(bool(c.get("sensitive")), sensitive, key)
            self.assertIn(c["mod"], INV_MODS)
            self.assertTrue(c["label"].startswith("发票管家·"), key)
            self.assertNotIn("kind", c)
        self.assertEqual(self.db.is_sensitive("inv_audit"), True)
        self.assertEqual(self.db.is_sensitive("inv_intake"), False)

    def test_default_perms(self):
        p = self.db.default_perms("核算组")
        for key, (_mod, sensitive) in INV_CAPS.items():
            # 敏感点新账号默认不给；非敏感点照平台规则会默认给（所以接口要叠加页面准入点）
            self.assertEqual(p.get(key), not sensitive, key)
        for k in ("enter:invdesk", "enter:invlater", "enter:invaudit", "enter:invledger"):
            self.assertFalse(p.get(k, False), k)

    def test_templates(self):
        tpl = self.lit["NAV_POST_TEMPLATES_DEFAULT"]
        meta = {c["key"]: c for c in self.db.CAP_META_STATIC}
        sec_keys = {s["key"] for s in self.lit["NAV_SECTIONS"]}
        mod_keys = {m["key"] for m in self.lit["NAV_MODULES"]}
        self.assertIn("inv", tpl["fin_mgr"]["secs"])
        self.assertIn("inv", tpl["ap_acc"]["secs"])
        for post in ("fin_mgr", "ap_acc"):
            self.assertTrue({"inv_intake", "inv_receive", "inv_deduct"} <= set(tpl[post]["acts"]), post)
        self.assertTrue({"invaudit", "invledger"} <= set(tpl["tax_acc"]["mods"]))
        self.assertIn("inv_deduct", tpl["tax_acc"]["acts"])
        self.assertIn("invdesk", tpl["intern"]["mods"])
        self.assertIn("inv_intake", tpl["intern"]["acts"])
        for post, t in tpl.items():
            for s in t.get("secs") or []:
                self.assertIn(s, sec_keys, (post, s))
            for m in t.get("mods") or []:
                if m.startswith("inv"):
                    self.assertIn(m, mod_keys, (post, m))
            for a in t.get("acts") or []:
                if a.startswith("inv_"):
                    self.assertIn(a, meta, (post, a))
                    self.assertFalse(meta[a].get("sensitive"), "敏感点不能进模板：%s" % a)
        # 一次性并入 DB 模板的条目与种子一致（种子里有的它都有）
        add = self.lit["_INV_TPL_ADD"]
        for post, parts in add.items():
            for k, vals in parts.items():
                self.assertTrue(set(vals) <= set(tpl[post].get(k) or []), (post, k))

    def test_auth_gate_and_router_wiring(self):
        src = self.src
        gate = src[src.index("async def _auth_gate"):src.index("app.add_middleware(ops.OpsMiddleware)")]
        self.assertIn('p.startswith("/api/inv/m/")', gate)
        self.assertIn("invoice.pair_token_ok(request)", gate)
        self.assertIn("not inv_pair", gate)
        reg = src.index("app.include_router(invoice.router)")
        self.assertLess(src.index("from routers import invoice"), reg)
        self.assertLess(reg, src.index('@app.get("/{full_path:path}")'))
        for k in INV_MODS:
            self.assertIn('"%s": "发票管家"' % k, src)
        ops_src = (BACKEND / "ops.py").read_text(encoding="utf-8")
        self.assertIn('"/api/inv": "发票管家"', ops_src)
        gi = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("inv_uploads/", [x.strip() for x in gi])


if __name__ == "__main__":
    unittest.main()
