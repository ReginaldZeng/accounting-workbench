# [Change Log] Date: 2026-10-19 | Author: Codex | Version: V2.994 | 总账凭证批量审核与选择部分反审
"""凭证审核 API；所有路由独立执行菜单与动作权限检查。"""
import json
import re
from fastapi import APIRouter, Request, HTTPException
from sqlalchemy import select
from pydantic import BaseModel, Field
import db
import core
import voucher_review as vr
from sqlalchemy.exc import IntegrityError

router=APIRouter(prefix='/api/voucher-review',tags=['voucher-review'])
module_open=lambda: True


def access(request, action=None):
    u=core._current_user(request)
    if not u: raise HTTPException(401,'请先登录')
    if not module_open() or not db.user_can(u,'enter:voucherreview'):
        raise HTTPException(403,'没有凭证审核页面权限，或模块未开放')
    if action and not db.user_can(u,'voucher_'+action): raise HTTPException(403,'没有此操作权限')
    if action and core.CFG.get('source')!='kingdee': raise HTTPException(409,'当前工作台为样例模式，禁止写入金蝶，请先切换金蝶数据源')
    return u


@router.get('/books')
def books(request:Request):
    access(request)
    c=vr.Client()
    return {'books':vr.kd.fetch_account_books(c.s,c.conf)}


class Search(BaseModel):
    books:list[str]=Field(min_length=1,max_length=50)
    month:str
    status:str='pending'
    creators:list[str]=Field(default_factory=list,max_length=100)
    summary:str=Field(default='',max_length=120)
    page:int=Field(default=1,ge=1,le=100000)
    size:int=Field(default=20,ge=1,le=100)


@router.post('/search')
def search(request:Request, body:Search):
    access(request)
    if not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])',body.month): raise HTTPException(400,'期间格式应为 YYYY-MM')
    if any(not re.fullmatch(r'[\w.-]{1,40}',b) for b in body.books): raise HTTPException(400,'主体编码无效')
    year,period=map(int,body.month.split('-'))
    where=f'FYEAR={year} and FPERIOD={period} and FAccountBookID.FNumber in ('+','.join(vr.quote(b) for b in body.books)+')'
    c=vr.Client()
    # 不带分录字段，避免海量分录扩张。摘要命中 ID 单独查，最终显示完整凭证金额/行数。
    rows=c.headers(where)
    creators=sorted({str(r['creator'] or '') for r in rows})
    counts={s:sum(r['status']==s for r in rows) for s in ('A','B','C')}
    allowed={'pending':('A','B'),'A':('A',),'B':('B',),'C':('C',),'all':('A','B','C','D','Z')}.get(body.status)
    if allowed is None: raise HTTPException(400,'状态无效')
    rows=[r for r in rows if r['status'] in allowed and (not body.creators or r['creator'] in body.creators)]
    if body.summary.strip():
        keyword=body.summary.strip().replace('[','[[]').replace('%','[%]').replace('_','[_]')
        ids={r['id'] for r in c.headers(where+' and FEXPLANATION like '+vr.quote('%'+keyword+'%'))}
        rows=[r for r in rows if r['id'] in ids]
    rows.sort(key=lambda r:(r['date'] or '',str(r['group']),int(r['number'] or 0),int(r['id'])))
    total=len(rows)
    rows=rows[(body.page-1)*body.size:body.page*body.size]
    for r in rows:
        r['fingerprint']=vr.fingerprint(r)
        r['tool_owned']=vr.ownership({k:r[k] for k in vr.FIELDS})
        r['lines']=c.count({k:r[k] for k in vr.FIELDS})
    return {'rows':rows,'total':total,'creators':creators,'counts':counts}


@router.get('/{vid}/entries')
def entries(request:Request,vid:int,page:int=1,size:int=50):
    access(request)
    if vid<=0 or page<1 or not 1<=size<=200: raise HTTPException(400,'分页参数无效')
    c=vr.Client(); header=c.header(vid)
    rows=c.query(vr.ENTRY.values(),f'FVOUCHERID={vid}','FEntity_FEntrySeq',(page-1)*size,size)
    return {'header':header,'total':c.count(header),'rows':[dict(zip(vr.ENTRY,r)) for r in rows]}


@router.get('/{vid}/summary')
def summary(request:Request,vid:int):
    access(request)
    row=vr.Client().header(vid); key=row['id']+':'+vr.fingerprint(row)
    with db._engine.connect() as c: old=c.execute(select(vr.summaries).where(vr.summaries.c.key==key)).mappings().first()
    if old and old['status']!='failed': return {'status':old['status'],**json.loads(old['result'])}
    if old:
        with db._engine.begin() as c: c.execute(vr.summaries.delete().where(vr.summaries.c.key==key,vr.summaries.c.status=='failed'))
    try:
        with db._engine.begin() as c: c.execute(vr.summaries.insert().values(key=key,status='running',result='{}'))
    except IntegrityError: return {'status':'running'}
    vr.summary_pool.submit(vr.aggregate,key,row)
    return {'status':'running'}


class Item(BaseModel):
    id:int=Field(gt=0)
    fingerprint:str=Field(pattern=r'^[a-f0-9]{64}$')


class Batch(BaseModel):
    request_id:str
    action:str
    items:list[Item]=Field(min_length=1,max_length=100)


@router.post('/jobs')
def start(request:Request,body:Batch):
    if body.action not in ('audit','unaudit'): raise HTTPException(400,'操作无效')
    user=access(request,body.action)
    try: return {'id':vr.enqueue(user['name'],body.model_dump())}
    except ValueError as e: raise HTTPException(400,str(e))


@router.get('/jobs/{jid}')
def job(request:Request,jid:str):
    u=access(request)
    with db._engine.connect() as c: row=c.execute(select(vr.jobs).where(vr.jobs.c.id==jid)).mappings().first()
    if not row: raise HTTPException(404,'任务不存在')
    if row['operator']!=u['name'] and u.get('role')!='admin': raise HTTPException(403,'没有任务查看权限')
    return {'id':jid,'status':row['status'],'results':json.loads(row['result']),'created':row['created']}


@router.get('/jobs')
def history(request:Request):
    u=access(request)
    q=select(vr.jobs).order_by(vr.jobs.c.created.desc()).limit(30)
    if u.get('role')!='admin': q=q.where(vr.jobs.c.operator==u['name'])
    with db._engine.connect() as c: rows=c.execute(q).mappings().all()
    return {'jobs':[{'id':r['id'],'operator':r['operator'],'created':r['created'],'status':r['status'], 'action':json.loads(r['payload'])['action'],'results':json.loads(r['result'])} for r in rows]}
