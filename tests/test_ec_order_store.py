"""Persistent result contracts against disposable SQLite, without core or Kingdee."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import create_engine,select,update,func
from sqlalchemy.exc import IntegrityError
from kernels import ec_order_store as store,ec_workbench as model
from tests.test_ecom_workbench_model import sources


def data(count=65):
    raw=sources();one=model.build_orders(raw,{})[0]
    rows=[]
    for i in range(count):
        r=copy.deepcopy(one);r.update(order_no=str(1000000000000000000+i),created_at='2026-08-02 12:00:00')
        r['items']=[{'sku':'sku-'+str(i),'name':'测试商品 100%_real' if i==2 else '测试商品'}]
        rows.append(r)
    return dict(rows=rows,sources=raw,provenance={},kingdee={'available':False},rule={'recognition':'shipment'},cash_available=True)


class OrderStoreTests(unittest.TestCase):
    def test_date_and_status_filters_use_saved_rows_and_clamp_jump(self):
        dataset=data(3)
        for r,stamp,status in zip(dataset['rows'],['2026-08-01 23:59:59','2026-08-02 00:00:00','2026-08-02 23:59:59'],['unknown','partial','settled']):
            r.update(created_at=stamp,settlement_state=status)
        self.build(dataset=dataset)
        with patch.object(model,'build_orders',side_effect=AssertionError('must not rebuild')):
            result=store.page(self.engine,'2026-08','shop',start_date='2026-08-02',end_date='2026-08-02',page=999)
            self.assertEqual((result['total'],result['page']),(2,1))
            self.assertEqual(store.page(self.engine,'2026-08','shop',settlement='settled')['total'],1)
            self.assertEqual(store.page(self.engine,'2026-08','shop',shipment='shipped')['total'],3)
            self.assertEqual(store.page(self.engine,'2026-08','shop',refund_state='none')['total'],3)
            self.assertEqual(store.page(self.engine,'2026-08','shop',start_date='2026-08-03')['total'],0)
        for start,end in [('2026-08-03','2026-08-02'),('invalid',''),('','2026-02-30')]:
            with self.assertRaises(ValueError):store.page(self.engine,'2026-08','shop',start_date=start,end_date=end)
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='order-results-test-')
        self.url='sqlite:///'+(Path(self.tmp.name)/'test.sqlite').as_posix()
        self.engine=create_engine(self.url);store.md.create_all(self.engine)
    def tearDown(self):self.engine.dispose();self.tmp.cleanup()
    def build(self,version='v1',dataset=None,shop='shop'):
        store.observe(self.engine,'2026-08',shop,version)
        job=store.claim(self.engine,'2026-08',shop)
        return store.publish(self.engine,job,dataset or data())
    def test_paging_search_and_detail_never_recompute(self):
        original=data();build=self.build(dataset=original)
        with patch.object(model,'build_orders',side_effect=AssertionError('unexpected rebuild')),patch.object(model,'metrics',side_effect=AssertionError('unexpected summary')):
            first=store.page(self.engine,'2026-08','shop');second=store.page(self.engine,'2026-08','shop',page=2)
            self.assertEqual((first['total'],first['pages'],len(first['rows']),len(second['rows'])),(65,3,30,30))
            self.assertFalse({r['order_no'] for r in first['rows']} & {r['order_no'] for r in second['rows']})
            self.assertEqual(store.page(self.engine,'2026-08','shop',q='100%_real')['total'],1)
            self.assertEqual(store.page(self.engine,'2026-08','shop',q='sku-64')['total'],1)
            self.assertEqual(store.page(self.engine,'2026-08','shop',page=999)['page'],3)
            row,status,_=store.detail(self.engine,'2026-08','shop',original['rows'][2]['order_no'])
            self.assertEqual(row,original['rows'][2]);self.assertEqual(status['build_id'],build)
    def test_persists_across_restart_and_summaries_equal_existing_model(self):
        original=data();self.build(dataset=original)
        expected=model.metrics(original['rows'],True)
        self.engine.dispose();self.engine=create_engine(self.url)
        summary,_,status=store.summary(self.engine,'2026-08','shop')
        self.assertEqual(summary,expected);self.assertEqual(status['status'],'ready')
        store.observe(self.engine,'2026-08','shop','v1')
        self.assertIsNone(store.claim(self.engine,'2026-08','shop'))
    def test_new_versions_do_not_mix_pages_or_publish_half_a_build(self):
        first=self.build()
        store.observe(self.engine,'2026-08','shop','v2');job=store.claim(self.engine,'2026-08','shop')
        self.assertIsNone(store.claim(self.engine,'2026-08','shop'))
        before=store.page(self.engine,'2026-08','shop')
        self.assertEqual(before['result']['status'],'building');self.assertTrue(before['result']['stale'])
        changed=data(2);second=store.publish(self.engine,job,changed)
        self.assertEqual(store.page(self.engine,'2026-08','shop')['total'],2)
        pinned=store.page(self.engine,'2026-08','shop',version=first,page=2)
        self.assertEqual(pinned['total'],65);self.assertTrue(pinned['result']['stale'])
        self.assertNotEqual(first,second)
        with self.assertRaises(ValueError):store.page(self.engine,'2026-08','another-shop',version=first)
    def test_source_change_during_build_and_expired_owner_are_fenced(self):
        first=self.build();store.observe(self.engine,'2026-08','shop','v2');old=store.claim(self.engine,'2026-08','shop')
        store.observe(self.engine,'2026-08','shop','v3')
        with self.assertRaises(store.Superseded):store.publish(self.engine,old,data(2))
        store.fail(self.engine,old,superseded=True)
        job=store.claim(self.engine,'2026-08','shop')
        with self.engine.begin() as cx:cx.execute(update(store.STATES).values(lease_until=0))
        successor=store.claim(self.engine,'2026-08','shop')
        with self.assertRaises(store.Superseded):store.publish(self.engine,job,data(2))
        self.assertFalse(store.heartbeat(self.engine,job))
        self.assertEqual(store.page(self.engine,'2026-08','shop')['result']['build_id'],first)
        store.publish(self.engine,successor,data(3))
        self.assertEqual(store.page(self.engine,'2026-08','shop')['total'],3)
    def test_failed_write_preserves_previous_snapshot_and_can_retry(self):
        first=self.build();store.observe(self.engine,'2026-08','shop','v2');job=store.claim(self.engine,'2026-08','shop')
        bad=data(2);bad['rows'][1]=bad['rows'][0]
        with self.assertRaises(IntegrityError):store.publish(self.engine,job,bad)
        store.fail(self.engine,job,'test failure')
        result=store.page(self.engine,'2026-08','shop')
        self.assertEqual(result['result']['status'],'error');self.assertEqual(result['result']['build_id'],first)
        with self.engine.connect() as cx:self.assertEqual(cx.execute(select(func.count()).select_from(store.BUILDS)).scalar(),1)
        store.observe(self.engine,'2026-08','shop','v2')
        self.assertIsNone(store.claim(self.engine,'2026-08','shop'))
        store.observe(self.engine,'2026-08','shop','v2',force=True)
        self.assertIsNotNone(store.claim(self.engine,'2026-08','shop'))
    def test_filters_preserve_previous_semantics_and_empty_is_not_ready(self):
        empty=store.page(self.engine,'2026-08','shop')
        self.assertIsNone(empty['total']);self.assertEqual(empty['result']['status'],'missing')
        raw=data(2);raw['rows'][1].update(destination='支付宝、聚合账户',business_type='ufirst',fees=None,refund_only=3,refund=3)
        self.build(dataset=raw)
        self.assertEqual(store.page(self.engine,'2026-08','shop',channel='alipay')['total'],2)
        self.assertEqual(store.page(self.engine,'2026-08','shop',channel='fund')['total'],1)
        self.assertEqual(store.page(self.engine,'2026-08','shop',flag='fees')['total'],1)
        self.assertEqual(store.page(self.engine,'2026-08','shop',flag='refund_only',business='ufirst')['total'],1)
        self.assertIsNone(store.summary(self.engine,'2026-08','shop')[0]['fees'])
