"""Tests actual schema and store/search implementation in an isolated in-memory database."""
import ast
import asyncio
import io
import json
import sys
import types
import threading
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BACK=ROOT/'01_Current_Deliverables/app/backend'
sys.path.insert(0,str(BACK))
sys.path.insert(0,str(ROOT/'02_Revision_History/20260909_V2.x_电商对账月结工作台一期/app/backend/.pydeps'))
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import LONGTEXT
from kernels import ec_settle as es
import openpyxl

namespace={k:getattr(sa,k) for k in ('Table','Column','String','Text','Integer','Float','Numeric','Index','UniqueConstraint','LargeBinary')}
namespace.update(_md=sa.MetaData(),LONGTEXT=LONGTEXT)
tree=ast.parse((BACK/'db.py').read_text(encoding='utf-8'))
names={'ec_flow_accounts','ec_flow_files','ec_flow_rows','ec_flow_origins','ec_flow_reviews','ec_excl_notes','ec_fee_map'}
for node in tree.body:
    if isinstance(node,ast.Assign) and isinstance(node.targets[0],ast.Name) and node.targets[0].id in names:
        exec(compile(ast.Module(body=[node],type_ignores=[]),'actual-schema','exec'),namespace)
fake=types.SimpleNamespace(**{name:namespace[name] for name in names},audit=lambda *a,**k:None)
fake._engine=sa.create_engine('sqlite://')
namespace['_md'].create_all(fake._engine)
sys.modules['core']=types.SimpleNamespace(db=fake)
sys.modules['routers.ec_workbench']=types.SimpleNamespace(_lock=threading.RLock(),_cache={},require=lambda *a,**k:{'name':'test'},shops=lambda:[],check_shop=lambda x:{},check_period=lambda x:None,ec=types.SimpleNamespace(es=es,_now=lambda:'2026-09-11 00:00:00'))
from routers import ec_flow_ledger as module


def file(serial='s1',amount=1.81,extra='first'):
    book=openpyxl.Workbook();sheet=book.active
    sheet.append(['入账时间','账务类型','收入（+元）','支出（-元）','业务描述','支付宝流水号','业务基础订单号','新列'])
    sheet.append(['2026-08-01 12:00:00','收费',0,amount,'0030003|技术费',serial,'5127484536168046206','kept'])
    # A non-data note makes source hashes differ without changing transaction content.
    sheet.append([extra]);buf=io.BytesIO();book.save(buf);book.close();return buf.getvalue()


class StoreTests(unittest.TestCase):
    def setUp(self):
        with fake._engine.begin() as cx:
            for name in ('ec_flow_reviews','ec_flow_origins','ec_flow_rows','ec_flow_files','ec_flow_accounts'):cx.execute(sa.delete(namespace[name]))
            for aid in ('a1','a2'):cx.execute(sa.insert(module.A).values(id=aid,kind='alipay',name=aid,shops='["test"]'))

    def test_retry_and_cross_file_origins(self):
        blob=file();self.assertEqual(module.import_files('a1',[('first.xlsx',blob)],'test')['added'],1)
        self.assertEqual(module.import_files('a1',[('first.xlsx',blob)],'test')['duplicates'],1)
        self.assertEqual(module.import_files('a1',[('second.xlsx',file(extra='second'))],'test')['added'],0)
        with fake._engine.connect() as cx:
            self.assertEqual(cx.execute(sa.select(sa.func.count()).select_from(module.R)).scalar(),1)
            self.assertEqual(cx.execute(sa.select(sa.func.count()).select_from(module.O)).scalar(),2)

    def test_conflict_preserved(self):
        module.import_files('a1',[('one.xlsx',file())],'test')
        self.assertEqual(module.import_files('a1',[('two.xlsx',file(amount=2))],'test')['conflicts'],1)
        result=module.search(None,account_id='a1',q='5127484536168046206')
        self.assertEqual(result['total'],2)
        self.assertTrue(all(any('冲突' in f for f in r['flags']) for r in result['rows']))

    def test_no_serial_not_discarded(self):
        module.import_files('a1',[('one.xlsx',file(serial='')),('two.xlsx',file(serial='',extra='second'))],'test')
        self.assertEqual(module.search(None,account_id='a1')['total'],2)

    def test_scope_and_unknown_column_search(self):
        module.import_files('a1',[('one.xlsx',file())],'test')
        self.assertEqual(module.search(None,account_id='a2')['total'],0)
        self.assertEqual(module.search(None,q='kept')['total'],1)
        self.assertEqual(module.search(None,q='%')['total'],0)
        self.assertEqual(module.search(None,direction='outgo',amount_min='1.80',amount_max='1.82')['total'],1)
        row=module.search(None)['rows'][0]
        self.assertNotIn('raw',row)
        self.assertEqual(module.detail(None,row['id'])['row']['raw']['新列'],'kept')

    def test_batch_error_rolls_back(self):
        with self.assertRaises(Exception):module.import_files('a1',[('one.xlsx',file()),('bad.xlsx',b'bad')],'test')
        self.assertEqual(module.search(None)['total'],0)

    def test_identical_file_keeps_alias_without_double_count(self):
        blob=file()
        module.import_files('a1',[('original.xlsx',blob)],'test')
        module.import_files('a1',[('copy.xlsx',blob)],'test')
        row=module.search(None)['rows'][0]
        self.assertEqual(module.search(None)['total'],1)
        self.assertEqual(module.detail(None,row['id'])['sources'][0]['aliases'],['copy.xlsx'])

    def test_review_persists_and_filters(self):
        module.import_files('a1',[('one.xlsx',file())],'test')
        rid=module.search(None)['rows'][0]['id']
        class Request:
            async def json(self):return {'ids':[rid],'verdict':'正常','note':'测试活动依据'}
        asyncio.run(module.review_save(Request()))
        self.assertEqual(module.search(None,review_status='正常')['total'],1)
        self.assertEqual(module.search(None,review_status='待核对')['total'],0)
        self.assertEqual(module.detail(None,rid)['review']['note'],'测试活动依据')

    def test_conflicting_latest_balance_not_chosen(self):
        module.import_files('a1',[('one.xlsx',file())],'test')
        with fake._engine.begin() as cx:
            cx.execute(sa.update(module.R).values(balance=14))
        self.assertEqual(module.accounts(None)['accounts'][0]['balance'],14)
        module.import_files('a1',[('two.xlsx',file(amount=2))],'test')
        with fake._engine.begin() as cx:cx.execute(sa.update(module.R).values(balance=15))
        self.assertIsNone(module.accounts(None)['accounts'][0]['balance'])

    def test_statement_account_is_not_merchant_suffix(self):
        from kernels import ec_flow_ledger as ledger
        with fake._engine.begin() as cx:cx.execute(sa.update(module.A).where(module.A.c.id=='a1').values(suffix='4680'))
        rows=ledger.parse(file(),'2088141335094680-export.xlsx',{})
        for row in rows:row['account']='10000000000000000156'
        result=module.store_parsed('a1',[('2088141335094680-export.xlsx','test-hash',rows)],'test')
        self.assertEqual(result['added'],1)
        self.assertEqual(module.accounts(None)['accounts'][0]['suffix'],'4680')

    def test_wrong_merchant_still_rejected(self):
        with fake._engine.begin() as cx:cx.execute(sa.update(module.A).where(module.A.c.id=='a1').values(suffix='9999'))
        with self.assertRaises(Exception):module.import_files('a1',[('2088141335094680-export.xlsx',file())],'test')
        self.assertEqual(module.search(None)['total'],0)


if __name__=='__main__':unittest.main()
