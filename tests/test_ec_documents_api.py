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
