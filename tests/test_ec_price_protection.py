"""Price protection is explanatory evidence, never a second refund or expense."""
import io
import sys
import unittest
from pathlib import Path
from openpyxl import Workbook
from sqlalchemy import create_engine, select
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'01_Current_Deliverables/app/backend'))
from kernels import ec_documents as docs, ec_workbench as model, ec_preparation as prep

NO='5127635376487030348'
HEAD=['主订单ID','子订单ID','购买商品信息',None,None,None,'价差金额','逆向退款金额（含退还天猫积分类服务费）','申请时间','状态','备注']
OLD='2026年7月15日价保系统升级，在7月15日之前的申请订单不会展示逆向退款金额'

def blob(reverse='¥21.00', duplicate=False, order=NO):
    w=Workbook();s=w.active;s.title='价保订单赔付详情';s.append(HEAD)
    s.append([None,None,'商品标题','商品ID','SKUID','具体型号'])
    row=[order,order,'商品','item','sku','规格','¥21.00',reverse,'2026-08-19 17:33:23','一键价保自动通过','不应保留的自由备注']
    s.append(row)
    if duplicate:s.append(row)
    b=io.BytesIO();w.save(b);return b.getvalue()

class PriceProtectionTests(unittest.TestCase):
    def test_columns_not_filename_two_headers_and_dedup(self):
        result=docs.parse_documents(blob(duplicate=True),'random.xlsx','shop')
        self.assertEqual(len(result['docs']),1)
        d=result['docs'][0]
        self.assertEqual((d['kind'],d['order_no'],d['row_number']),('price_protection',NO,3))
        self.assertNotIn('自由备注',d['payload'])
        engine=create_engine('sqlite://');docs.md.create_all(engine)
        self.assertEqual(docs.store_documents(engine,'shop',[result],'test')['added'],1)
        self.assertEqual(docs.store_documents(engine,'shop',[result],'test')['duplicates'],1)
        with engine.connect() as cx:self.assertEqual(len(cx.execute(select(docs.ec_document_heads)).all()),1)
        engine.dispose()

    def test_unknown_not_zero_and_invalid_rejected(self):
        for reverse in ('',OLD):
            parsed=model.parse_source('price_protection',[blob(reverse)],'2026-08','shop')
            self.assertIsNone(parsed['rows'][0]['reverse_refund'])
            self.assertTrue(parsed['warnings'])
        for reverse in ('oops','¥-1','NaN'):
            with self.assertRaises(ValueError):model.parse_source('price_protection',[blob(reverse)],'2026-08','shop')
        with self.assertRaises(ValueError):docs.parse_documents(blob(order=int(NO)),'any.xlsx','shop')

    def test_explains_but_never_changes_financial_totals(self):
        sources={'order':{'period':'2026-08','rows':[dict(order_no=NO,order_key='key',current_paid=69,refund=21,paid_at='2026-08-17',status='交易成功')]}}
        before=model.build_orders(sources,{})[0]
        sources['price_protection']=model.parse_source('price_protection',[blob()],'2026-08','shop')
        after=model.build_orders(sources,{})[0]
        self.assertEqual(after['price_protection_count'],1)
        self.assertEqual(after['price_protections'][0]['reverse_refund'],21)
        for k in ('refund','gsv','fees','net_receipt','ar_amount','settlement_state'):
            self.assertEqual(before[k],after[k],k)
        self.assertIn('退款类型待分类',after['issues'])  # Approval is not a refund-type/settlement proof.

    def test_optional_does_not_change_readiness(self):
        self.assertNotIn('price_protection',prep.requirements({},prep.TARGET))
        progress=prep.progress([dict(available=True,state='ready'),dict(optional=True,available=False)])
        self.assertEqual((progress['ready'],progress['required']),(1,1))

if __name__=='__main__':unittest.main()
