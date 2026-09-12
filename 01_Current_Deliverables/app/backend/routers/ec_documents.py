"""Authenticated historical document import and persisted flow reconciliation."""
import json
import threading
import uuid
import logging
import hashlib
import gzip
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


def backfill_snapshots():
    """Idempotent bridge, retaining the existing imports and all original ledger rows."""
    table=db.ec_workbench_imports
    affected=set();added=0
    with db._engine.connect() as cx:
        latest=select(func.max(table.c.id)).group_by(table.c.shop,table.c.period,table.c.kind)
        records=list(cx.execute(select(table).where(table.c.id.in_(latest),table.c.kind.in_(['order','item','refund','wdt']))))
        known=set(cx.execute(select(docs.ec_document_files.c.digest)).scalars())
    for r in records:
        source_id=f'ec_workbench_imports:{r.id}'
        if docs.digest(['snapshot-v1',r.shop,source_id]) in known:continue
        payload=json.loads(gzip.decompress(r.payload))
        batch=docs.snapshot_batch(r.shop,r.period,r.kind,payload.get('rows',[]),source_id,json.loads(r.filenames or '[]'))
        if batch:
            result=docs.store_documents(db._engine,r.shop,[batch],'历史资料自动承接')
            affected.update(result['affected']);added+=result['added']
    # The earliest platform importer saved original order IDs in a separate table.
    from kernels.ec_preparation import TARGET
    migrated_periods={r.period for r in records if r.shop==TARGET and r.kind=='order'}
    legacy=db.ec_tmall_import_runs
    with db._engine.connect() as cx:
        latest=select(func.max(legacy.c.id)).where(legacy.c.kind=='order').group_by(legacy.c.period)
        old_runs=list(cx.execute(select(legacy).where(legacy.c.id.in_(latest))))
    for r in old_runs:
        source_id=f'ec_tmall_import_runs:{r.id}'
        if r.period in migrated_periods or docs.digest(['snapshot-v1',TARGET,source_id]) in known:continue
        with db._engine.connect() as cx:
            rows=[dict(v._mapping) for v in cx.execute(select(db.ec_tmall_order_details).where(db.ec_tmall_order_details.c.run_id==r.id))]
        batch=docs.snapshot_batch(TARGET,r.period,'order',rows,source_id,[])
        if batch:
            result=docs.store_documents(db._engine,TARGET,[batch],'历史资料自动承接')
            affected.update(result['affected']);added+=result['added']
    return {'added':added,'reconcile':reconcile(affected) if affected else {'updated':0}}


@router.on_event('startup')
def bridge_saved_evidence():
    start_job(backfill_snapshots,'历史资料自动承接')

def import_blobs(shop,blobs,operator):
    aliases=db.get_setting('ec_document_shop_aliases',{'星期零STARFIELD 天猫官旗店':['STARFIELD星期零旗舰店']}) or {}
    with db._engine.connect() as cx:
        known={r.digest:r.row_count for r in cx.execute(select(docs.ec_document_files.c.digest,docs.ec_document_files.c.row_count).where(docs.ec_document_files.c.shop==shop))}
    parsed=[];duplicate_rows=0
    for name,blob in blobs:
        fingerprint=hashlib.sha256(blob).hexdigest()
        if fingerprint in known:
            duplicate_rows+=known[fingerprint];continue
        result=docs.parse_documents(blob,name,shop,aliases.get(shop,[]))
        parsed.append(result);known[fingerprint]=len(result['docs'])
    result=docs.store_documents(db._engine,shop,parsed,operator)
    result['duplicates']+=duplicate_rows
    affected=result.pop('affected')
    result['reconcile']=reconcile(affected) if affected else {'updated':0,'statuses':{}}
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

@router.post('/import',summary='按内容指纹去重后解析历史资料')
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
    def run():
        backfill_snapshots()
        return reconcile()
    return start_job(run,user['name'])

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
