"""Deterministic financial evidence tests; no database or Kingdee connection."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'01_Current_Deliverables/app/backend'))
from kernels import ec_workbench as model

def sources(kind='normal',ship_date='2026-08-02'):
    return {'order':{'period':'2026-08','rows':[{'order_no':'o1','order_key':'k1','paid_at':'2026-08-01','current_paid':15.81,'refund':0,'status':'卖家已发货'}]},
        'item':{'business':{'k1':kind},'rows':[]},
        'wdt':{'rows':[{'order_no':'o1','internal_order':'w1','shipment_no':'s1','date':ship_date,'status':'已发货','receivable':15.81}]},
        'alipay':{'rows':[{'order_key':'k1','kind':'receipt','channel':'alipay','income':15.81,'expense':0},
                          {'order_key':'k1','kind':'fee','channel':'alipay','income':0,'expense':1.81}]}}

class ModelTests(unittest.TestCase):
    def test_gsv_is_before_fees(self):
        row=model.build_orders(sources(),{})[0]
        self.assertEqual((row['paid'],row['fees'],row['net_receipt'],row['gsv']),(15.81,1.81,14,15.81))
    def test_future_shipment_not_recognized(self):
        row=model.build_orders(sources(ship_date='2026-09-01'),{})[0]
        self.assertFalse(row['should_ar'])
        self.assertNotIn('已发货未匹配应收',row['issues'])
    def test_ufirst_bypasses_ar(self):
        row=model.build_orders(sources('ufirst'),{})[0]
        self.assertFalse(row['should_ar'])
        self.assertNotIn('已发货未匹配应收',row['issues'])
    def test_shared_internal_order_not_double_assigned(self):
        data=sources();data['wdt']['rows'].append(dict(data['wdt']['rows'][0],order_no='o2'))
        row=model.build_orders(data,{})[0]
        self.assertIsNone(row['ar_expected'])
        self.assertIn('合并出库订单：应收金额需分摊核对',row['issues'])
    def test_aggregation_copy_not_double_counted(self):
        data=sources();data['alipay']['rows'][0]['aggregate_copy']=True
        data['fund']={'rows':[{'order_key':'k1','kind':'receipt','channel':'fund','income':15.81,'expense':0}]}
        row=model.build_orders(data,{})[0]
        self.assertEqual(row['receipt_amount'],15.81)
        self.assertEqual(row['destination'],'聚合账户')
    def test_unknown_refund_not_claimed_as_refund_only(self):
        data=sources();data['order']['rows'][0]['refund']=5
        row=model.build_orders(data,{})[0]
        self.assertEqual((row['gsv'],row['unknown_refund'],row['refund_only']),(10.81,5,0))
    def test_missing_fee_not_reported_as_zero_total(self):
        row=model.build_orders(sources(),{})[0];row['fees']=None
        self.assertIsNone(model.metrics([row])['fees'])

if __name__=='__main__':unittest.main()
