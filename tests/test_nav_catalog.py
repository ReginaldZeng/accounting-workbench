# [Change Log] Date: 2026-09-12 | Author: Codex | Version: V2.564
# Description: Execute real catalog functions against isolated settings; never import the live database.
import ast
import copy
import importlib.util
import json
from pathlib import Path
import re
import unittest

BASE = Path(__file__).resolve().parents[1] / '01_Current_Deliverables' / 'app' / 'backend'
spec = importlib.util.spec_from_file_location('nav_layout', BASE / 'nav_layout.py')
layout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(layout)


class Store:
    def __init__(self): self.data = {}
    def get_setting(self, key, default=None): return copy.deepcopy(self.data.get(key, default))
    def set_setting(self, key, value, operator=''): self.data[key] = copy.deepcopy(value)
    def audit(self, *args): pass


def catalog():
    constants = {'NAV_STATUSES', '_NAV_LEGACY', 'NAV_OPEN', 'NAV_OPEN_ADMIN', 'NAV_SECTIONS', '_SEC_KEYS', 'NAV_LABELS',
                 'NAV_MODULES', '_NAV_KEY', '_SEC_OVR_KEY', '_USEC_KEY', '_MOD_OVR_KEY', '_UMOD_KEY', '_KEY_RE', '_BUILTIN_KEYS'}
    functions = {'_nav_norm', '_user_sections', '_all_sections', '_user_modules', '_all_modules', '_mod_by_key',
                 '_enter_cap', '_nav_one', '_nav_state', 'nav_module_move', 'nav_module_add', 'nav_module_del', 'nav_sections_save'}
    selected = []
    for node in ast.parse((BASE / 'app.py').read_text(encoding='utf-8-sig')).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in constants for t in node.targets): selected.append(node)
        if isinstance(node, ast.FunctionDef) and node.name in functions:
            node.decorator_list = []; selected.append(node)
    store = Store()
    env = {'re': re, 'db': store, 'Request': object, 'JSONResponse': lambda value, **kwargs: value,
           'numbered_modules': layout.numbered_modules, 'layout_patch': layout.layout_patch,
           '_nav_cached': lambda key, build: build(), '_nav_cache_clear': lambda: None,
           '_current_user': lambda request: request, '_nav_can_edit': lambda u: u and u.get('role') == 'admin',
           'can_enter_dev': lambda u: u and u.get('role') == 'admin', '_post_keys': lambda: {'gl', 'ap'}, '_POST_ALIAS': {}}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(BASE / 'app.py'), 'exec'), env)
    return env, store


ADMIN = {'name': 'test-admin', 'role': 'admin'}


class CatalogTests(unittest.TestCase):
    def setUp(self): self.n, self.db = catalog()
    def modules(self): return self.n['_all_modules']()
    def find(self, key): return next(m for m in self.modules() if m['key'] == key)
    def move(self, key, **patch): return self.n['nav_module_move']({'key': key, **patch}, ADMIN)

    def test_row_save_roundtrip_and_permissions(self):
        original = self.find('fxrate')
        r = self.move('fxrate', label='汇率维护测试', status='人工并行', posts=['gl'], show_home=False, show_sidebar=True)
        self.assertTrue(r['ok'], r)
        self.assertEqual(self.find('fxrate')['label'], '汇率维护测试')
        self.assertFalse(self.find('fxrate')['show_home'])
        self.assertEqual(self.find('fxrate')['cap'], original['cap'])
        self.assertEqual(self.n['_nav_state'](True)['fxrate']['posts'], ['gl'])

    def test_insert_shifts_siblings_and_keeps_identity(self):
        original = self.find('fxrate')
        parent = self.find('bankrecon')['layout']
        r = self.move('fxrate', layout=parent + '-001')
        self.assertTrue(r['ok'], r)
        children = [m for m in self.modules() if m.get('parent') == 'bankrecon']
        self.assertEqual(children[0]['key'], 'fxrate')
        self.assertEqual(len({m['layout'] for m in self.modules()}), len(self.modules()))
        self.assertEqual(self.find('fxrate')['cap'], original['cap'])

    def test_move_group_moves_entire_subtree(self):
        before = self.find('clexport')['cap']
        r = self.move('costledger', layout='B1-01')
        self.assertTrue(r['ok'], r)
        self.assertEqual(self.find('clexport')['sec'], 'gl')
        self.assertTrue(self.find('clexport')['layout'].startswith('B1-01-'))
        self.assertEqual(self.find('clexport')['cap'], before)

    def test_leaf_move_does_not_inherit_destination_permission(self):
        before = self.find('fxrate')['cap']
        r = self.move('fxrate', layout=self.find('costledger')['layout'] + '-001')
        self.assertTrue(r['ok'], r)
        self.assertEqual(self.find('fxrate')['cap'], before)

    def test_invalid_input_has_no_writes(self):
        for patch in ({'layout': 'B1-00'}, {'layout': 'B1-99-001'}, {'posts': ['unknown']}, {'show_home': 'false'}, {'label': ''}):
            before = copy.deepcopy(self.db.data)
            self.assertFalse(self.move('fxrate', **patch)['ok'])
            self.assertEqual(self.db.data, before)
        self.assertFalse(self.move('bankrecon', layout=self.find('costledger')['layout'] + '-001')['ok'])

    def test_add_end_edit_delete_and_recreate(self):
        r = self.n['nav_module_add']({'key': 'test_tool', 'label': '测试功能', 'parent': 'bankrecon', 'status': '敬请期待'}, ADMIN)
        self.assertTrue(r['ok'], r)
        children = [m for m in self.modules() if m.get('parent') == 'bankrecon']
        self.assertEqual(children[-1]['key'], 'test_tool')
        self.assertTrue(self.move('test_tool', label='已修改', show_home=False)['ok'])
        self.assertEqual(self.find('test_tool')['label'], '已修改')
        self.assertTrue(self.n['nav_module_del']({'key': 'test_tool'}, ADMIN)['ok'])
        self.n['nav_module_add']({'key': 'test_tool', 'label': '重建', 'sec': 'report'}, ADMIN)
        self.assertEqual(self.find('test_tool')['label'], '重建')
        self.assertNotIn('show_home', self.find('test_tool'))

    def test_legacy_state_and_access(self):
        m = self.find('fxrate')
        for status, expected in [('已上线', True), ('等待部署', True), ('隐藏', False), ('开发中', False)]:
            r = self.n['_nav_one'](m, {'fxrate': {'status': status}}, {'gl'}, False)
            self.assertEqual(r['可进入'], expected)
        self.assertTrue(self.n['_nav_one'](m, {'fxrate': {'status': '开发中'}}, {'gl'}, True)['可进入'])

    def test_nonadmin_and_fixed_entries(self):
        self.assertFalse(self.n['nav_module_move']({'key': 'fxrate', 'label': 'x'}, {'role': 'user'})['ok'])
        self.assertFalse(self.move('settings', show_sidebar=False)['ok'])
        self.assertFalse(self.n['nav_module_del']({'key': 'fxrate'}, ADMIN)['ok'])


if __name__ == '__main__': unittest.main()
