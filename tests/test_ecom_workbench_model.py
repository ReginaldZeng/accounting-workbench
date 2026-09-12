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
    def test_fee_categories_returns_and_kingdee_amount_not_reverse_calculated(self):
        data=sources();flow=data['alipay']['rows'];flow[1]['code']='routine'
        flow.extend([dict(flow[1],code='commission',expense=1),dict(flow[1],income=.5,expense=0),dict(flow[1],code='new',expense=.2)])
        ar={'o1':{'amount':20,'documents':[{'bill_no':'AR1','amount':20}]}}
        row=model.build_orders(data,ar,fee_categories={'routine':'routine','commission':'commission'})[0]
        self.assertEqual((row['routine_fee'],row['commission_fee'],row['unclassified_fee']),(1.31,1,.2))
        self.assertEqual(row['ar_amount'],20)
        self.assertEqual(row['ar_diff'],4.19)
        self.assertIn('费用分类待设置',row['issues'])
        self.assertEqual(row['settlement_state'],'settled')
        data['alipay']['rows']=[]
        row=model.build_orders(data,ar)[0]
        self.assertEqual(row['settlement_state'],'unknown')
        self.assertEqual(row['ar_amount'],20)
    def test_settlement_is_independent_of_ufirst_and_refunds_need_cash_evidence(self):
        data=sources('ufirst');row=model.build_orders(data,{})[0]
        self.assertEqual(row['settlement_state'],'settled')
        self.assertEqual(row['destination'],'支付宝')
        data['order']['rows'][0]['refund']=2
        self.assertEqual(model.build_orders(data,{})[0]['settlement_state'],'review')
        data['order']['rows'][0]['refund']=0;data['alipay']['rows'][0]['income']=5
        self.assertEqual(model.build_orders(data,{})[0]['settlement_state'],'partial')
    def test_fee_without_code_uses_saved_flow_rule_classification(self):
        data=sources();data['alipay']['rows'][1]['rule_id']='trial_fee'
        row=model.build_orders(data,{},fee_categories={'rule:trial_fee':'routine'})[0]
        self.assertEqual((row['routine_fee'],row['commission_fee'],row['unclassified_fee']),(1.81,0,0))
    def test_linked_fee_keeps_actual_wallet_and_business_label(self):
        no='3316393238010038983'
        row={'order_no':'','remark':'先用后付技术服务费('+no+')扣款',
             'mch_no':'T200P'+no,'serial':'fee-1','income':0,'outgo':'0.19','desc':'','btype':'分账'}
        for channel in ('alipay','fund'):
            result=model.event(row,channel)
            self.assertEqual(result['channel'],channel)
            self.assertEqual(result['label'],'先用后付技术服务费扣款')
            self.assertEqual(result['kind'],'fee')
            self.assertEqual(result['expense'],0.19)
        self.assertEqual(row['order_no'],'')

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
