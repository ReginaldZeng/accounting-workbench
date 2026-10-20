"""隔离外部系统验证导出接口，避免导入 core 时初始化真实数据库。"""
import ast
import asyncio
import json
import sys
import unittest
from pathlib import Path
from starlette.responses import Response, JSONResponse
from test_temp_attendance_export import fixture

class Req:
 def __init__(self, data): self.data=data
 async def form(self): return self.data

class RouteTests(unittest.IsolatedAsyncioTestCase):
 def setup_route(self, allowed=True):
  source=Path('02_Revision_History/20260703_V1.2_接真数据/app/backend/routers/temp_attendance.py').read_text(encoding='utf-8-sig')
  fn=next(n for n in ast.parse(source).body if isinstance(n,ast.AsyncFunctionDef) and n.name=='tempatt_export_detail')
  fn.decorator_list=[]
  async def read(req):return None,None,{},None,'2026-08',{}
  self.audits=[]
  class DB:
   def audit(_, *args): self.audits.append(args)
  ctx={'Request':Req,'Response':Response,'JSONResponse':JSONResponse,'CAP':'tempatt_review','json':json,
       '_require_perm':lambda *_:{'name':'测试'} if allowed else None,'_read_two':read,
       '_load_period':lambda _:({**fixture(),'month':'2026-08'},{}),'_period_files':lambda _:(None,None),
       '_apply_acks':lambda *_:None,'db':DB()}
  exec(compile(ast.Module(body=[fn],type_ignores=[]),'<isolated export route>','exec'),ctx)
  return ctx['tempatt_export_detail']
 async def test_permission(self):
  response=await self.setup_route(False)(Req({}))
  self.assertEqual(response.status_code,403)
 async def test_export_and_audit(self):
  response=await self.setup_route()(Req({'scope':json.dumps({'mode':'all'})}))
  self.assertEqual(response.status_code,200);self.assertTrue(response.body.startswith(b'PK'))
  self.assertEqual(len(self.audits),1)
 async def test_bad_scope(self):
  response=await self.setup_route()(Req({'scope':'{"mode":"agency","agencies":"服务甲"}'}))
  self.assertEqual(response.status_code,400)
 async def test_empty_person_scope(self):
  response=await self.setup_route()(Req({'scope':'{"mode":"person","people":[]}'}))
  self.assertEqual(response.status_code,400)

if __name__=='__main__':unittest.main()
