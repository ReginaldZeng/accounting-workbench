"""Isolated registry tests: never imports core/db or touches Kingdee."""
import io
import json
import unittest
from decimal import Decimal
import openpyxl
from sqlalchemy import create_engine,MetaData,Table,Column,Integer,String,Text,select,insert,func
from kernels import ec_documents as d

class DocumentsTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://')
        d.md.create_all(self.engine)
        md=MetaData()
        self.accounts=Table('accounts',md,Column('id',String,primary_key=True),Column('kind',String),Column('shops',Text))
        self.flows=Table('flows',md,Column('id',Integer,primary_key=True),Column('account_id',String),Column('payload',Text))
        md.create_all(self.engine)
        with self.engine.begin() as cx:cx.execute(insert(self.accounts),[{'id':'a','kind':'alipay','shops':'["shop"]'},{'id':'b','kind':'fund','shops':'["shop"]'}])
    def tearDown(self):self.engine.dispose()
    def book(self,heads,rows):
        b=openpyxl.Workbook();s=b.active;s.append(heads)
        for row in rows:s.append(row)
        buf=io.BytesIO();b.save(buf);b.close();return buf.getvalue()
    def order(self,no='1234567890123456789',paid='10',shop='shop'):
        blob=self.book(['订单编号','订单创建时间','买家实付金额','确认收货打款金额','收件人'],[[no,'2026-07-31 12:00:00',paid,paid,'DO_NOT_STORE']])
        return d.parse_documents(blob,'random.xlsx',shop)
    def addflow(self,no='1234567890123456789',income='10',account='a',**values):
        v=dict(order_no=no,income=income,outgo='0',bucket='receipt',code='0010001',ts='2026-08-02 00:00:00',raw={},**values)
        with self.engine.begin() as cx:return cx.execute(insert(self.flows).values(account_id=account,payload=d.pack(v))).inserted_primary_key[0]
    def results(self):
        with self.engine.connect() as cx:return list(cx.execute(select(d.ec_flow_matches)))
    def test_cross_period_split_and_idempotency(self):
        p=self.order();self.addflow(income='6');self.addflow(income='4')
        before=self.originals()
        d.reconcile(self.engine,self.flows,self.accounts)
        self.assertEqual({r.status for r in self.results()},{'missing_document'})
        r=d.store_documents(self.engine,'shop',[p],'test');self.assertEqual(r['added'],1)
        d.reconcile(self.engine,self.flows,self.accounts,r['affected'])
        self.assertEqual([r.status for r in self.results()],['amount_equal']*2)
        self.assertEqual(json.loads(self.results()[0].payload)['documents'][0]['period'],'2026-07')
        self.assertNotIn('DO_NOT_STORE',p['docs'][0]['payload'])
        renamed=dict(p,filename='renamed.xlsx')
        r=d.store_documents(self.engine,'shop',[renamed],'test');self.assertEqual(r['added'],0)
        self.assertEqual(d.reconcile(self.engine,self.flows,self.accounts,r['affected'])['updated'],0)
        self.assertEqual(self.originals(),before)
    def originals(self):
        with self.engine.connect() as cx:return list(cx.execute(select(self.flows)))
    def test_version_conflicts_do_not_overwrite(self):
        self.addflow();d.store_documents(self.engine,'shop',[self.order()],'test')
        r=d.store_documents(self.engine,'shop',[self.order(paid='11')],'test')
        self.assertEqual(r['conflicts'],1)
        d.reconcile(self.engine,self.flows,self.accounts,r['affected'])
        self.assertEqual(self.results()[0].status,'conflict')
        with self.engine.connect() as cx:
            head=cx.execute(select(d.ec_document_heads)).one()
            self.assertEqual(json.loads(head.payload)['确认收货打款金额'],'10')
            self.assertEqual(cx.execute(select(func.count()).select_from(d.ec_document_versions)).scalar(),2)
    def test_child_order_and_shop_isolation(self):
        no='1234567890123456789';child='2234567890123456789'
        p=d.parse_documents(self.book(['主订单编号','子订单编号','订单创建时间'],[[no,child,'2026-07-31']]),'nonsense.xlsx','shop')
        self.addflow(child);d.store_documents(self.engine,'shop',[p],'test')
        d.reconcile(self.engine,self.flows,self.accounts)
        self.assertEqual(self.results()[0].order_no,no);self.assertEqual(self.results()[0].status,'linked')
        d.store_documents(self.engine,'other',[self.order(shop='other')],'test')
        d.reconcile(self.engine,self.flows,self.accounts)
        self.assertEqual(self.results()[0].status,'linked')
    def test_aggregate_copy_excluded(self):
        self.addflow(chan='聚合结算渠道');self.addflow(account='b')
        d.store_documents(self.engine,'shop',[self.order()],'test');d.reconcile(self.engine,self.flows,self.accounts)
        self.assertEqual([r.status for r in self.results()],['linked','amount_equal'])
        self.assertEqual(json.loads(self.results()[1].payload)['amount_check']['received'],'10')
    def test_new_cash_updates_existing_order_result(self):
        self.addflow(income='6');d.store_documents(self.engine,'shop',[self.order()],'test')
        d.reconcile(self.engine,self.flows,self.accounts)
        self.assertEqual(self.results()[0].status,'amount_pending')
        self.addflow(income='4');d.reconcile(self.engine,self.flows,self.accounts)
        self.assertEqual({r.status for r in self.results()},{'amount_equal'})
    def test_reject_numeric_id_and_wrong_shop(self):
        with self.assertRaises(ValueError):self.order(no=1234567890123456789)
        blob=self.book(['订单编号','订单创建时间','买家实付金额','店铺名称'],[['1234567890123456789','2026-07-31','10','other']])
        with self.assertRaises(ValueError):d.parse_documents(blob,'random.xlsx','shop')
        self.assertEqual(len(d.parse_documents(blob,'random.xlsx','shop',['other'])['docs']),1)

if __name__=='__main__':unittest.main()
