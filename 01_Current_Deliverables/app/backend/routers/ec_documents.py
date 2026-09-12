"""Authenticated historical document import and persisted flow reconciliation."""
import json
import threading
import uuid
import logging
from pathlib import Path
from fastapi import APIRouter, Request, Form, File, UploadFile, HTTPException
from sqlalchemy import select,func
from core import db
from routers.ec_workbench import require,check_shop,ec
from kernels import ec_documents as docs

router=APIRouter(prefix='/api/ec/documents')
_jobs={};_lock=threading.Lock()

def reconcile(affected=None):
    return docs.reconcile(db._engine,db.ec_flow_rows,db.ec_flow_accounts,affected)

def import_blobs(shop,blobs,operator):
    aliases=db.get_setting('ec_document_shop_aliases',{'星期零STARFIELD 天猫官旗店':['STARFIELD星期零旗舰店']}) or {}
    parsed=[docs.parse_documents(blob,name,shop,aliases.get(shop,[])) for name,blob in blobs]
    result=docs.store_documents(db._engine,shop,parsed,operator)
    result['reconcile']=reconcile(result.pop('affected'))
    db.audit(operator,'ec_documents_import',target=shop,detail=json.dumps(result,ensure_ascii=False))
    return result

def start_job(fn,operator):
    with _lock:
        if any(j['status']=='running' for j in _jobs.values()):raise HTTPException(409,'历史资料正在解析或核对，请稍后查看结果')
        if len(_jobs)>40:_jobs.clear()
        jid=uuid.uuid4().hex;_jobs[jid]={'status':'running'}
    def run():
        try:
            result=fn()
            with _lock:_jobs[jid]={'status':'complete','result':result}
        except Exception as exc:
            logging.getLogger(__name__).error('Historical document job failed: %s',type(exc).__name__)
            error=str(exc)[:200] if isinstance(exc,(ValueError,HTTPException)) else '后台处理失败，请查看服务器日志；已保存资料保留，可重新核对'
            with _lock:_jobs[jid]={'status':'failed','error':error}
    threading.Thread(target=run,daemon=True).start()
    return {'ok':True,'job_id':jid}

@router.get('/jobs/{jid}')
def job(request:Request,jid:str):
    require(request)
    with _lock:
        if jid not in _jobs:raise HTTPException(404,'任务记录已失效，请刷新资料列表；可重新核对，无需重复上传')
        return dict(ok=True,**_jobs[jid])

@router.post('/import')
async def upload(request:Request,shop:str=Form(...),files:list[UploadFile]=File(...)):
    user=require(request,True);selected=check_shop(shop)
    if selected['platform'] not in ('天猫','淘宝'):raise HTTPException(400,'本轮先接入天猫/淘宝资料')
    if not files or len(files)>20:raise HTTPException(400,'每批1—20个文件')
    blobs=[];total=0
    for f in files:
        blob=await f.read(tm_limit:=25*1024*1024+1);total+=len(blob)
        if len(blob)>=tm_limit or total>200*1024*1024:raise HTTPException(413,'单个文件最多25MB，每批最多200MB')
        blobs.append((Path((f.filename or '').replace('\\','/')).name[:180],blob))
    return start_job(lambda:import_blobs(shop,blobs,user['name']),user['name'])

@router.post('/reconcile')
def rebuild(request:Request):
    user=require(request,True)
    return start_job(lambda:reconcile(),user['name'])

@router.get('')
def status(request:Request,shop:str=''):
    require(request)
    if shop:check_shop(shop)
    with db._engine.connect() as cx:
        where=[docs.ec_document_files.c.shop==shop] if shop else []
        files=[dict(r._mapping) for r in cx.execute(select(docs.ec_document_files).where(*where).order_by(docs.ec_document_files.c.id.desc()))]
        q=select(docs.ec_document_heads.c.kind,docs.ec_document_heads.c.period,func.count()).group_by(docs.ec_document_heads.c.kind,docs.ec_document_heads.c.period)
        if shop:q=q.where(docs.ec_document_heads.c.shop==shop)
        coverage=[{'kind':r[0],'period':r[1],'rows':r[2]} for r in cx.execute(q)]
    return {'ok':True,'files':files,'coverage':coverage,'statuses':docs.STATUSES}
