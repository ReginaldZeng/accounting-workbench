# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-04 | Author: Claude / c | Version: V2.789
# Description: 全成本内核自检（合成小样本，不连金蝶）——委外产品进表的口径、零成本委外不列、
#   没归类的费用项目与没配系数的产品分组要一次报全。真数据回归（8 月 79 个产品、合计 26,401,179.2869）
#   在 02_Revision_History/20261002_金蝶成本取数对照 的离线校验里，这里只守住口径不被改坏。
import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kernels.actual_cost import prepare, MissingConfig, OUTSOURCED  # noqa: E402

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERIOD = '2026-07'


def header(cc, code, name, wo, bill_type, qty, amount):
    return [cc, code, name, wo, bill_type, '', '', amount, qty, amount]


def item(name, amount):
    return ['', '', '', '', '', name, '', amount, '', amount]


def expense(name, amount):
    return ['', '', '', '', '', '', name, amount, '', amount]


def fixture(cost):
    seed = json.load(open(os.path.join(BACKEND, 'actual_cost_107_2026_08.json'), encoding='utf8'))
    rules = copy.deepcopy(seed['rules'])
    rules.update(effective_from=PERIOD, effective_to=PERIOD)
    supplement = dict(copy.deepcopy(seed['supplement']), period=PERIOD)
    codes = {str(r[1]) for r in cost if r[3]}
    sources = dict(org='107', period=PERIOD, fetched_at='2026-08-05T00:00:00+00:00', book_current_period='2026-08',
                   cost=cost, outbound=[], ledger=[],
                   materials={c: dict(code=c, name=c, spec='', unit='千克', group='植物肉') for c in codes})
    return sources, rules, supplement


HOUSE = [header('植物肉车间', 'A001', '自制品', 'MO001', '普通生产', 10, 130),
         item('直接材料', 100), item('直接人工', 20), item('制造费用', 10), expense('车间耗材', 10)]
SUB = [header('', 'B001', '委外品', 'SUB001', '普通委外订单', 5, 70), item('直接材料', 50), item('委外加工费', 20)]


class TestOutsourced(unittest.TestCase):
    def test_outsourced_listed_separately_without_plant_allocation(self):
        result = prepare(*fixture(HOUSE + SUB))
        by = {(p['cc'], p['code']): p for p in result['products']}
        sub = by[(OUTSOURCED, 'B001')]
        self.assertAlmostEqual(sub['material'], 50)
        self.assertAlmostEqual(sub['subcontract'], 20)
        self.assertAlmostEqual(sub['total'], 70)
        self.assertAlmostEqual(sub['unit'], 14)
        for field in ('labor', 'indirect', 'water', 'power', 'gas', 'depreciation', 'rent', 'other', 'wip'):
            self.assertEqual(sub[field], 0, field)          # 不在厂内生产，不分摊厂内费用
        self.assertEqual(by[('植物肉车间', 'A001')]['subcontract'], 0)
        controls = result['controls']
        self.assertAlmostEqual(controls['gold_total'], 200)   # 金蝶完工成本含委外，合计仍对平
        self.assertAlmostEqual(controls['total'], 200)
        self.assertAlmostEqual(controls['outsourced_total'], 70)
        # 委外不计入共享分摊产量：分母只有自制的 10 kg
        self.assertAlmostEqual(controls['shared_quantity']['total'], 10)

    def test_zero_cost_outsourced_order_not_listed(self):
        zero = [header('', 'B002', '零成本委外', 'SUB002', '普通委外订单', '', '')]
        result = prepare(*fixture(HOUSE + zero))
        self.assertEqual([(p['cc'], p['code']) for p in result['products']], [('植物肉车间', 'A001')])

    def test_outsourced_with_plant_cost_item_is_rejected(self):
        bad = [header('', 'B001', '委外品', 'SUB001', '普通委外订单', 5, 70), item('直接材料', 50), item('直接人工', 20)]
        with self.assertRaises(ValueError) as cm:
            prepare(*fixture(HOUSE + bad))
        self.assertIn('委外工单出现未配置成本项目', str(cm.exception))


class TestMissingConfig(unittest.TestCase):
    def test_unmapped_expenses_reported_all_at_once(self):
        cost = [header('植物肉车间', 'A001', '自制品', 'MO001', '普通生产', 10, 30),
                item('制造费用', 30), expense('桶装水', 10), expense('招聘费', 10), expense('桶装水', 10)]
        with self.assertRaises(MissingConfig) as cm:
            prepare(*fixture(cost))
        self.assertEqual((cm.exception.kind, cm.exception.names), ('expense', ['桶装水', '招聘费']))
        self.assertTrue(str(cm.exception).startswith('未配置费用项目：'))

    def test_missing_group_weights_reported_with_names(self):
        sources, rules, supplement = fixture(HOUSE)
        sources['materials']['A001']['group'] = '鲜食山姆'
        with self.assertRaises(MissingConfig) as cm:
            prepare(sources, rules, supplement)
        self.assertEqual((cm.exception.kind, cm.exception.names), ('group', ['鲜食山姆']))


if __name__ == '__main__':
    unittest.main()
