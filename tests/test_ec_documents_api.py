"""Real router/SQL contracts in a disposable database, with authentication and Kingdee isolated."""
import os,sys,tempfile,types,threading,unittest,json,io,time
from unittest.mock import patch
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'01_Current_Deliverables/app/backend'))
from sqlalchemy import insert,select
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from kernels import ec_documents as docs,ec_settle

class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='ec-docs-api-')
        cls.previous_env=os.environ.get('DB_URL')
        os.environ['DB_URL']='sqlite:///'+(Path(cls.tmp.name)/'test.sqlite').as_posix()
        cls.modules={k:sys.modules.get(k) for k in ('db','core','routers.ec_workbench','routers.ec_flow_ledger','routers.ec_documents')}
        for k in cls.modules:sys.modules.pop(k,None)
        import db
        cls.db=db
        def require(request,write=False):
            if request.headers.get('x-test-role') not in (('admin',) if write else ('admin','reader')):raise HTTPException(403,'测试权限拦截')
            return {'name':'test','role':request.headers['x-test-role']}
        def check_shop(shop):
            if shop!='shop':raise HTTPException(400,'未知店铺')
            return {'id':shop,'platform':'天猫'}
        core=types.ModuleType('core');core.db=db;core._require_perm=lambda request,permission:require(request,permission=='ec_settle_upload')
        sys.modules['core']=core
        wb=types.ModuleType('routers.ec_workbench');wb.require=require;wb.check_shop=check_shop;wb.shops=lambda:[{'id':'shop','name':'shop'}];wb.check_period=lambda period:None
        wb.ec=types.SimpleNamespace(_now=lambda:'2026-09-12 12:00:00',es=ec_settle);wb._lock=threading.RLock();wb._cache={}
        wb.mark_order_inputs_changed=lambda ids:None
        sys.modules['routers.ec_workbench']=wb
        from routers import ec_documents,ec_flow_ledger
        cls.router=ec_documents
        app=FastAPI();app.include_router(ec_documents.router);app.include_router(ec_flow_ledger.router)
        cls.client=TestClient(app)
        with db._engine.begin() as cx:
            cx.execute(insert(db.ec_flow_accounts).values(id='a',kind='alipay',name='test',shops='["shop"]'))
            p={'order_no':'1234567890123456789','serial':'test','income':'10','outgo':'0','bucket':'receipt','code':'0010001','raw':{'业务描述':'交易收款'},'flags':[]}
            cx.execute(insert(db.ec_flow_rows).values(account_id='a',identity_key='a',fingerprint='a',serial='test',order_no=p['order_no'],occurred_at='2026-08-02 00:00:00',period='2026-08',income=10,outgo=0,abnormal=0,bucket='receipt',search_text='交易收款',payload=docs.pack(p)))
    @classmethod
    def tearDownClass(cls):
        cls.client.close();cls.db._engine.dispose()
        for k,v in cls.modules.items():
            if v is None:sys.modules.pop(k,None)
            else:sys.modules[k]=v
        if cls.previous_env is None:os.environ.pop('DB_URL',None)
        else:os.environ['DB_URL']=cls.previous_env
        cls.tmp.cleanup()
    def test_preparation_uses_saved_sources_not_order_rebuild(self):
        import importlib.util,routers
        from kernels import ec_preparation
        fake=types.SimpleNamespace(fulfillment=types.SimpleNamespace(TARGET_SHOP=ec_preparation.TARGET),
            es=ec_settle,_now=lambda:'2026-09-12 12:00:00',_KD_REFRESH={},_kd_cache_meta=lambda p:None)
        spec=importlib.util.spec_from_file_location('preparation_router_test',ROOT/'01_Current_Deliverables/app/backend/routers/ec_workbench.py')
        module=importlib.util.module_from_spec(spec)
        with patch.object(routers,'ec',fake,create=True):spec.loader.exec_module(module)
        self.db.set_setting('ec_preparation_rules',{'shop':['order','alipay']})
        module.save_source('2026-08','shop','order','test-prep',['test.xlsx'],{'rows':[{'order_no':'123'}],'status':'ready'},'test')
        with patch.object(module,'compute_data',side_effect=AssertionError('must not rebuild orders')):
            cards=module.preparation_cards('2026-08','shop')
        self.assertEqual(ec_preparation.progress(cards)['ready'],2)
        with self.db._engine.begin() as cx:
            cx.execute(insert(self.db.ec_flow_accounts).values(id='missing-account',kind='alipay',name='second',shops='["shop"]'))
        cards=module.preparation_cards('2026-08','shop')
        self.assertEqual(ec_preparation.progress(cards)['ready'],1)
        self.assertEqual(cards[1]['rows'],1)
        self.assertIn('1 个关联账户',cards[1]['warnings'][0])
        self.assertEqual(module.preparation_cards('2026-08','unconfigured'),[])

    def test_saved_order_http_reads_and_worker_version_contract(self):
        import importlib.util,routers
        from kernels import ec_order_store as store,ec_workbench as model
        from tests.test_ec_order_store import data
        fake=types.SimpleNamespace(fulfillment=types.SimpleNamespace(TARGET_SHOP='shop'),es=ec_settle,
            _kd_cache_path=lambda p:str(Path(self.tmp.name)/'absent.json'))
        spec=importlib.util.spec_from_file_location('order_router_test',ROOT/'01_Current_Deliverables/app/backend/routers/ec_workbench.py')
        module=importlib.util.module_from_spec(spec)
        with patch.object(routers,'ec',fake,create=True):spec.loader.exec_module(module)
        app=FastAPI();app.include_router(module.router);client=TestClient(app)
        selected={'id':'shop','name':'shop','platform':'天猫','kd_name':'shop'}
        with patch.object(module,'shops',return_value=[selected]),patch.object(module,'preparation_cards',return_value=[]):
            version=module.input_version('2026-08','shop')
            store.observe(self.db._engine,'2026-08','shop',version)
            with patch.object(module,'compute_data',return_value=data()) as compute:
                module.build_result('2026-08','shop');module.build_result('2026-08','shop')
                self.assertEqual(compute.call_count,1)
            headers={'x-test-role':'reader'}
            with patch.object(module,'compute_data',side_effect=AssertionError('HTTP must not build')),patch.object(model,'metrics',side_effect=AssertionError('HTTP must not aggregate all orders')):
                for path in ('orders?page=2&','order?order_no=1000000000000000002&','overview?','preview?','results/status?'):
                    response=client.get('/api/ec/workbench/'+path+'period=2026-08&shop=shop',headers=headers)
                    self.assertEqual(response.status_code,200,response.text)
                page=client.get('/api/ec/workbench/orders?period=2026-08&shop=shop&page=2',headers=headers).json()
                self.assertEqual((page['total'],page['page'],len(page['rows'])),(65,2,30))
                filtered=client.get('/api/ec/workbench/orders?period=2026-08&shop=shop&start_date=2026-08-02&end_date=2026-08-02&shipment=shipped&refund_state=none&page=3',headers=headers).json()
                self.assertEqual((filtered['total'],filtered['page'],len(filtered['rows'])),(65,3,5))
                self.assertEqual(client.get('/api/ec/workbench/orders?period=2026-08&shop=shop&start_date=2026-08-03',headers=headers).json()['total'],0)
                self.assertEqual(client.get('/api/ec/workbench/orders?period=2026-08&shop=shop&settlement=invalid',headers=headers).status_code,400)
                self.assertEqual(client.get('/api/ec/workbench/orders?period=2026-08&shop=shop').status_code,403)
                self.assertEqual(client.post('/api/ec/workbench/results/refresh',json={'period':'2026-08','shop':'shop'},headers=headers).status_code,403)
            self.db.set_setting('ec_income_recognition_rules',{'shop':'confirmed'})
            self.db.set_setting('ec_fee_display_categories',{'fee1':'routine'})
            changed=module.input_version('2026-08','shop');self.assertNotEqual(version,changed)
            store.observe(self.db._engine,'2026-08','shop',changed)
            with patch.object(module,'compute_data',side_effect=ValueError('private payload must not leak')):
                module.build_result('2026-08','shop')
            status=store.page(self.db._engine,'2026-08','shop')['result']
            self.assertEqual(status['status'],'error');self.assertEqual(status['build_id'],page['result']['build_id'])
            self.assertNotIn('private',status['error'])
            original=module.input_version('2026-08','shop')
            module.mark_order_inputs_changed(['a'])
            self.assertNotEqual(original,module.input_version('2026-08','shop'))
            with patch.object(module,'compute_data',return_value=data(7)) as compute,TestClient(app):
                for _ in range(100):
                    if store.state(self.db._engine,'2026-08','shop')['status']=='ready':break
                    time.sleep(.02)
                self.assertEqual(store.page(self.db._engine,'2026-08','shop')['total'],7)
                self.assertEqual(compute.call_count,1)
        client.close()

    def test_router_import_filter_detail_and_permissions(self):
        from kernels.test_ec_documents import DocumentsTests
        headers={'x-test-role':'admin'}
        self.assertEqual(self.client.get('/api/ec/documents').status_code,403)
        self.assertEqual(self.client.post('/api/ec/documents/reconcile',headers={'x-test-role':'reader'}).status_code,403)
        r=self.client.get('/api/ec/flows?match_status=pending_index',headers=headers).json();self.assertEqual(r['total'],1)
        blob=DocumentsTests().book(['订单编号','订单创建时间','买家实付金额','确认收货打款金额'],[['1234567890123456789','2026-07-31','10','10']])
        r=self.client.post('/api/ec/documents/import',data={'shop':'shop'},files=[('files',('random.xlsx',blob,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'))],headers=headers)
        self.assertEqual(r.status_code,200,r.text)
        jid=r.json()['job_id']
        for _ in range(100):
            job=self.client.get('/api/ec/documents/jobs/'+jid,headers=headers).json()
            if job['status']!='running':break
            time.sleep(.02)
        self.assertEqual(job['status'],'complete',job)
        r=self.client.get('/api/ec/flows?period=2026-08&match_status=amount_equal&q=1234567890123456789',headers=headers)
        self.assertEqual(r.status_code,200,r.text);v=r.json()
        self.assertEqual(v['total'],1);self.assertEqual(v['income'],10);self.assertEqual(v['outgo'],0)
        self.assertEqual(v['rows'][0]['document_match']['status'],'amount_equal')
        self.assertEqual(self.client.get('/api/ec/flows?match_status=missing_document',headers=headers).json()['total'],0)
        self.assertEqual(self.client.get('/api/ec/flows?match_status=pending_index',headers=headers).json()['total'],0)
        v=self.client.get('/api/ec/flows/detail/1',headers=headers).json()['row']
        self.assertEqual(v['document_match']['evidence']['documents'][0]['filename'],'random.xlsx')
        self.assertEqual(v['document_match']['evidence']['documents'][0]['period'],'2026-07')
        self.assertEqual(v['raw'],{'业务描述':'交易收款'})
        self.assertEqual(self.client.get('/api/ec/documents?shop=shop',headers=headers).json()['coverage'][0]['period'],'2026-07')
        with patch.object(docs,'parse_documents',side_effect=AssertionError('duplicate file must not be parsed')):
            result=self.router.import_blobs('shop',[('renamed.xlsx',blob)],'test')
            self.assertEqual(result['added'],0)
            self.assertEqual(result['duplicates'],1)
            self.assertEqual(result['reconcile']['updated'],0)

if __name__=='__main__':unittest.main()
