"""Only stubbed writes; this suite never logs in to Kingdee."""
import os, sys, tempfile, unittest, uuid, copy, json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['DB_URL']='sqlite:///'+tempfile.mktemp(suffix='.db').replace('\\','/')
import voucher_review as v
from sqlalchemy import select, delete

class Fake:
    row={k:None for k in v.FIELDS}
    row.update(id='123',book='107',status='A',invalid='0',poster='-1',debit=10,credit=10,modified='one')
    calls=[]; failure=None
    def __init__(self): pass
    def header(self,vid): return copy.deepcopy(self.row)
    def mutate(self,action,vid):
        self.calls.append(action)
        if self.failure: raise self.failure
        if action=='Submit': self.row.update(status='B',modified='two')
        if action=='Audit': self.row.update(status='C',modified='three',audited='audit-one',checker_id='10')
        if action=='UnAudit': self.row.update(status='B',modified='four',audited=None,checker_id='0')

class Tests(unittest.TestCase):
    def setUp(self):
        Fake.row={k:None for k in v.FIELDS};Fake.row.update(id='123',book='107',status='A',invalid='0',poster='-1',debit=10,credit=10,modified='one');Fake.calls=[];Fake.failure=None
        with v.db._engine.begin() as c:
            for t in (v.jobs,v.owners,v.cache): c.execute(delete(t))
    def runbatch(self,action='audit',fingerprint=None):
        payload={'request_id':str(uuid.uuid4()),'action':action,'items':[{'id':123,'fingerprint':fingerprint or v.fingerprint(Fake.row)}]}
        with patch.object(v,'Client',Fake),patch.object(v.pool,'submit') as submit:
            jid=v.enqueue('tester',payload)
            v.run_job(jid,'tester',payload)
        with v.db._engine.connect() as c: row=c.execute(select(v.jobs).where(v.jobs.c.id==jid)).mappings().one()
        return json.loads(row['result'])[0]
    def test_submit_audit_and_partial_unaudit(self):
        self.assertEqual(self.runbatch()['status'],'success');self.assertEqual(Fake.calls,['Submit','Audit']);self.assertTrue(v.ownership(Fake.row))
        self.assertEqual(self.runbatch('unaudit')['status'],'success');self.assertFalse(v.ownership(Fake.row));self.assertEqual(Fake.calls[-1],'UnAudit')
    def test_subset_does_not_unaudit_other_vouchers(self):
        self.runbatch()
        other=copy.deepcopy(Fake.row);other['id']='124'
        with v.db._engine.begin() as c:c.execute(v.owners.insert().values(voucher='124',fingerprint=v.fingerprint(other),lock='',operator='tester'))
        self.assertEqual(self.runbatch('unaudit')['status'],'success')
        self.assertTrue(v.ownership(other));self.assertFalse(v.ownership(Fake.row))
    def test_recovery_never_replays_or_adopts_external_audit(self):
        payload={'request_id':str(uuid.uuid4()),'action':'audit','items':[{'id':123,'fingerprint':v.fingerprint(Fake.row)}]}
        with patch.object(v.pool,'submit') as submit:v.enqueue('tester',payload)
        v.claim('123',payload['request_id'],'tester')
        v.recover_interrupted()
        self.assertFalse(v.ownership(Fake.row))
        with self.assertRaises(RuntimeError):v.claim('123','new','tester')
        self.assertEqual(Fake.calls,[])
    def test_original_currency_separate_debit_credit(self):
        key='summary-test'
        with v.db._engine.begin() as c:
            c.execute(v.summaries.delete())
            c.execute(v.summaries.insert().values(key=key,status='running',result='{}'))
        class Aggregator(Fake):
            def query(self,*a,**k):return [[1,'1001','CNY',1,10,10,0],[2,'1002','CNY',1,10,0,10],[3,'1003','USD',7,2,14,0]]
        with patch.object(v,'Client',Aggregator):v.aggregate(key,Fake.row)
        with v.db._engine.connect() as c:result=json.loads(c.execute(select(v.summaries.c.result).where(v.summaries.c.key==key)).scalar_one())
        self.assertIn('CNY 借 10.00',result['original']);self.assertIn('CNY 贷 10.00',result['original']);self.assertIn('USD 借 2.00',result['original'])
    def test_b_only_audit(self):
        Fake.row['status']='B';self.assertEqual(self.runbatch()['status'],'success');self.assertEqual(Fake.calls,['Audit'])
    def test_external_cycle_not_owned(self):
        self.runbatch();Fake.row['audited']='external-re-audit';self.assertFalse(v.ownership(Fake.row));self.assertEqual(self.runbatch('unaudit')['status'],'failed');self.assertNotIn('UnAudit',Fake.calls)
    def test_external_audit_no_ownership(self):
        Fake.row['status']='C';self.assertEqual(self.runbatch('unaudit')['status'],'failed');self.assertEqual(Fake.calls,[])
    def test_stale_snapshot(self):
        self.assertEqual(self.runbatch(fingerprint='0'*64)['status'],'failed');self.assertEqual(Fake.calls,[])
    def test_timeout_blocks_replay(self):
        Fake.failure=TimeoutError('timeout');self.assertEqual(self.runbatch()['status'],'uncertain');self.assertFalse(v.ownership(Fake.row));Fake.failure=None;self.assertEqual(self.runbatch()['status'],'failed');self.assertEqual(Fake.calls,['Submit'])
    def test_explicit_rejection_unlocks(self):
        Fake.failure=v.Rejected('closed period');self.assertEqual(self.runbatch()['status'],'failed');Fake.failure=None;self.assertEqual(self.runbatch()['status'],'success')
    def test_posted_and_void(self):
        Fake.row['posted']='date';self.assertEqual(self.runbatch()['status'],'failed');self.assertEqual(Fake.calls,[])
        Fake.row['posted']=None;Fake.row['invalid']='1';self.assertEqual(self.runbatch()['status'],'failed')
    def test_idempotency(self):
        p={'request_id':str(uuid.uuid4()),'action':'audit','items':[{'id':123,'fingerprint':v.fingerprint(Fake.row)}]}
        with patch.object(v.pool,'submit') as s:
            v.enqueue('tester',p);v.enqueue('tester',p);self.assertEqual(s.call_count,1)
            with self.assertRaises(ValueError):v.enqueue('other',p)
    def test_atomic_lock(self):
        v.claim('123','a','tester')
        with self.assertRaises(RuntimeError):v.claim('123','b','tester')
    def test_exact_count_with_gaps(self):
        client=object.__new__(v.Client)
        def q(fields,where,order,start=0,limit=1000):
            seq=[1,3,8];return [[8]] if 'DESC' in order else [[x] for x in seq[start:start+limit]]
        client.query=q;self.assertEqual(client.count(Fake.row),3)
    def test_permissions_and_module_gate(self):
        from routers import voucher_review as r
        from fastapi import HTTPException
        with patch.object(r.core,'_current_user',return_value={'name':'x'}),patch.object(r.db,'user_can',return_value=False):
            with self.assertRaises(HTTPException):r.access(None,'audit')
        with patch.object(r.core,'_current_user',return_value={'name':'x'}),patch.object(r.db,'user_can',return_value=True),patch.object(r,'module_open',return_value=False):
            with self.assertRaises(HTTPException):r.access(None)
        with patch.object(r.core,'_current_user',return_value={'name':'x'}),patch.object(r.db,'user_can',side_effect=lambda u,cap:cap=='enter:voucherreview'):
            with self.assertRaises(HTTPException):r.access(None,'unaudit')

if __name__=='__main__':unittest.main()
