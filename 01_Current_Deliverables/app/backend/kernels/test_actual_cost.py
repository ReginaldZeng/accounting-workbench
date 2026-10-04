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


class TestMaterialBreakdown(unittest.TestCase):
    """直接材料拆分（V2.791）：按子项物料汇总到每公斤产品；底稿对比的差异率与含税口径。"""
    PRODUCT = dict(cc='植物肉车间', code='A001', qty=100.0, unit=2.0)
    DETAIL = dict(
        movements=[dict(code='M1', unit='千克', name='大豆油', net_qty=30.0, net_amount=90.0, bill='L1'),
                   dict(code='M1', unit='千克', name='大豆油', net_qty=-3.0, net_amount=-9.0, bill='T1'),   # 退料冲减
                   dict(code='M2', unit='Pcs', name='纸箱', net_qty=20.0, net_amount=19.0, bill='L2')],
        prices=[dict(code='M1', date='2026-07-05', tax_rate=13.0)],
        controls=dict(complete_material=98.0))

    def test_breakdown_without_standard(self):
        import actual_cost_standard as standard
        out = standard.breakdown(self.PRODUCT, self.DETAIL)
        self.assertTrue(out['ready'])
        oil, box = out['rows']                                # 按成本从大到小
        self.assertEqual((oil['code'], oil['unit']), ('M1', 'kg'))
        self.assertAlmostEqual(oil['actual_qty'], 0.27)       # (30−3)/100
        self.assertAlmostEqual(oil['actual_price'], 3.0)
        self.assertAlmostEqual(oil['actual_cost'], 0.81)
        self.assertAlmostEqual(oil['actual_cost_incl'], 0.81 * 1.13)   # 有应付单税率才折含税
        self.assertIsNone(box['actual_cost_incl'])                     # 没有税率依据就不折
        self.assertAlmostEqual(sum(r['actual_share'] for r in out['rows']), 1)
        self.assertAlmostEqual(out['actual_material_input_per_kg'], 1.0)
        self.assertAlmostEqual(out['material_timing_bridge'], -0.02)   # (98−100)/100，单列不摊到物料
        self.assertEqual(out['actual_incl_unknown'], 1)

    def test_breakdown_needs_detail_and_output(self):
        import actual_cost_standard as standard
        self.assertFalse(standard.breakdown(self.PRODUCT, None)['ready'])
        self.assertIn('完工量', standard.breakdown(dict(self.PRODUCT, qty=0), self.DETAIL)['issue'])

    def test_compare_rates_and_tax_basis(self):
        import actual_cost_standard as standard
        entry = dict(erp_code='A001', status='已定稿', materials=[
            dict(seg='原料', matCode='M1', matName='大豆油', unit='kg', qtyPerKg=0.30, priceIncl=3.39, taxRate=0.13, costExcl=0.90),
            dict(seg='包材', matCode='M2', matName='纸箱', unit='个', qtyPerKg=0.20, priceIncl=1.00, taxRate=0.13, costExcl=0.20)])  # 普票：含税价＝计价
        out = standard.compare(entry, self.PRODUCT, self.DETAIL)
        oil, box = out['rows']
        self.assertAlmostEqual(oil['qty_rate'], 0.27 / 0.30 - 1)       # 用量差异率
        self.assertAlmostEqual(oil['price_rate'], 0.0)                 # 实际 3.0 对底稿计价 3.0
        self.assertAlmostEqual(oil['cost_rate'], 0.81 / 0.90 - 1)      # 整体差异率
        self.assertAlmostEqual(oil['standard_cost_incl'], 0.30 * 3.39)
        self.assertAlmostEqual(oil['actual_cost_incl'], 0.81 * 3.39 / 3.0)   # 按底稿自己的含税÷不含税倍率折
        self.assertAlmostEqual(box['actual_cost_incl'], box['actual_cost'])  # 普票倍率为 1，不重复加税
        self.assertAlmostEqual(out['material_cost_rate'], 1.0 / 1.10 - 1)
        self.assertAlmostEqual(out['standard_material_incl'], 0.30 * 3.39 + 0.20 * 1.00)


class TestMaterialMapping(unittest.TestCase):
    """人工设定的匹配（V2.792）：单位换算、替代料。不设不并；设了只在对得上时才并；合计不变。"""
    PRODUCT = dict(cc='小料车间', code='A001', qty=100.0, unit=2.0)
    DETAIL = dict(
        movements=[dict(code='CREAM', unit='升', name='稀奶油', net_qty=8.0, net_amount=264.0, bill='L1'),
                   dict(code='BAG1', unit='Pcs', name='包装袋', net_qty=70.0, net_amount=21.0, bill='L2'),
                   dict(code='BAG2', unit='Pcs', name='包装袋', net_qty=30.0, net_amount=9.0, bill='L3')],
        prices=[], controls=dict(complete_material=294.0))
    ENTRY = dict(erp_code='A001', status='已定稿', materials=[
        dict(seg='原料', matCode='CREAM', matName='稀奶油', unit='kg', qtyPerKg=0.0824, priceIncl=36.0, taxRate=0.13, costExcl=2.6368),
        dict(seg='包材', matCode='BAG1', matName='包装袋', unit='pcs', qtyPerKg=1.0, priceIncl=0.339, taxRate=0.13, costExcl=0.30)])

    def rows(self, mapping=None):
        import actual_cost_standard as standard
        out = standard.compare(self.ENTRY, self.PRODUCT, self.DETAIL, mapping)
        return out, {(r['code'], r['unit'], r['section']): r for r in out['rows']}

    def test_nothing_merged_without_setting(self):
        out, by = self.rows()
        self.assertEqual(by[('CREAM', 'kg', '原料')]['status'], '单位不一致，待换算')
        self.assertEqual(by[('CREAM', '升', '实际新增')]['status'], '单位不一致，待换算')
        self.assertEqual(by[('BAG2', 'pcs', '实际新增')]['status'], '实际新增用料')
        self.assertAlmostEqual(by[('BAG1', 'pcs', '包材')]['qty_rate'], -0.30)       # 只看到一半的袋子，看着像省了

    def test_unit_conversion_and_substitute(self):
        base, _ = self.rows()
        out, by = self.rows(dict(units={'CREAM': {'from': '升', 'to': 'kg', 'factor': 1.03}}, aliases={'BAG2': {'to': 'BAG1'}}))
        self.assertEqual(len(out['rows']), 2)                                          # 两行并回底稿行，「实际新增」清空
        cream, bag = by[('CREAM', 'kg', '原料')], by[('BAG1', 'pcs', '包材')]
        self.assertEqual((cream['status'], bag['status']), ('匹配', '匹配'))
        self.assertAlmostEqual(cream['actual_qty'], 8.0 * 1.03 / 100)                  # 数量×系数
        self.assertAlmostEqual(cream['actual_cost'], 2.64)                             # 金额不变
        self.assertAlmostEqual(cream['merged'][0]['factor'], 1.03)
        self.assertAlmostEqual(bag['actual_qty'], 1.0)                                 # 70＋30 个，实际没省
        self.assertAlmostEqual(bag['qty_rate'], 0.0)
        self.assertEqual((bag['merged'][0]['code'], bag['merged'][0]['alias']), ('BAG2', True))
        self.assertAlmostEqual(out['actual_material_input_per_kg'], base['actual_material_input_per_kg'])   # 合计不因合并而变
        self.assertAlmostEqual(sum(r['actual_share'] for r in out['rows']), 1)

    def test_setting_that_does_not_fit_is_ignored(self):
        # 换算到底稿里没有的单位、替代到底稿里没有的编码：都不并，保持原样提示
        out, by = self.rows(dict(units={'CREAM': {'from': '升', 'to': '箱', 'factor': 2}}, aliases={'BAG2': {'to': 'NOPE'}}))
        self.assertEqual(len(out['rows']), 4)
        self.assertEqual(by[('CREAM', '升', '实际新增')]['status'], '单位不一致，待换算')
        self.assertEqual(by[('BAG2', 'pcs', '实际新增')]['merged'], [])


if __name__ == '__main__':
    unittest.main()
