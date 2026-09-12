"""Account statement search, scoped to existing ecommerce permissions. No Kingdee writes."""
import hashlib
import json
import threading
import uuid
from pathlib import Path
from decimal import Decimal
from fastapi import APIRouter, Request, Form, File, UploadFile, HTTPException
from sqlalchemy import select, insert, update, delete, func, or_, and_
from core import db, _require_perm
from routers.ec_workbench import require, shops, check_shop, check_period, ec
from kernels import ec_flow_ledger as ledger

router=APIRouter(prefix='/api/ec/flows')
_import_lock=threading.Lock()
_jobs_lock=threading.Lock()
_jobs={}
A,F,R,O=db.ec_flow_accounts,db.ec_flow_files,db.ec_flow_rows,db.ec_flow_origins


def configured_rules():
    return ledger.normalize_rules(db.get_setting('ec_flow_class_rules',ledger.DEFAULT_CLASS_RULES) or [])


def account(cx, account_id):
    row=cx.execute(select(A).where(A.c.id==account_id)).first()
    if not row:raise HTTPException(404,'账户不存在，请先登记账户')
    return row


@router.get('/accounts')
def accounts(request:Request):
    require(request)
    result=[]
    with db._engine.connect() as cx:
        for row in cx.execute(select(A).order_by(A.c.name)):
            as_of=cx.execute(select(func.max(R.c.occurred_at)).where(R.c.account_id==row.id,R.c.balance.is_not(None))).scalar()
            last=list(cx.execute(select(R.c.balance,R.c.payload).where(R.c.account_id==row.id,R.c.occurred_at==as_of,R.c.balance.is_not(None)))) if as_of else []
            balances={r.balance for r in last}
            ambiguous=len(balances)>1 or any('同一流水号内容冲突' in r.payload for r in last)
            count=cx.execute(select(func.count()).select_from(R).where(R.c.account_id==row.id)).scalar()
            result.append({'id':row.id,'kind':row.kind,'name':row.name,'suffix':row.suffix,
                'shops':json.loads(row.shops or '[]'),'rows':count,
                'balance':float(next(iter(balances))) if balances and not ambiguous else None,'as_of':as_of,
                'balance_basis':'末笔余额有冲突或同时间多值，待核对' if ambiguous else '流水末笔余额，不代表当前实时余额' if last else '原文件未提供余额'})
    return {'ok':True,'accounts':result,'shops':shops(),'buckets':ledger.BUCKETS}


@router.post('/accounts')
async def create_account(request:Request):
    user=require(request,True);body=await request.json()
    name=str(body.get('name') or '').strip();kind=body.get('kind','alipay')
    suffix=str(body.get('suffix') or '').strip();ids=body.get('shops',[])
    if not name or len(name)>120 or kind not in ('alipay','fund'):raise HTTPException(400,'请填写账户名称和支持的账户类型')
    if suffix and (not suffix.isdigit() or len(suffix)>8):raise HTTPException(400,'账户尾号最多 8 位数字')
    if not isinstance(ids,list) or not ids:raise HTTPException(400,'请至少关联一家店铺')
    for sid in ids:check_shop(sid)
    aid=uuid.uuid4().hex
    with db._engine.begin() as cx:
        cx.execute(insert(A).values(id=aid,name=name,kind=kind,suffix=suffix,shops=json.dumps(sorted(set(ids)),ensure_ascii=False),operator=user['name'],ts=ec._now()))
    db.audit(user['name'],'ec_flow_account',target=aid,detail='登记工作台账户；金蝶未写入')
    return {'ok':True,'id':aid}


def import_files(account_id, files, operator):
    """One transaction per selected batch. Identical file retries are idempotent."""
    seed={code:{'label':label,'category':category,'account':'待定'} for code,label,category in ec.es.FEE_MAP_SEED}
    with db._engine.connect() as cx:
        acc=account(cx,account_id)
        if acc.kind not in ('alipay','fund'):raise HTTPException(400,'不支持的账户类型')
        for r in cx.execute(select(db.ec_fee_map)):
            record=dict(r._mapping);code=record['code']
            seed.setdefault(code,{}).update(record)
    rules=configured_rules();parsed=[]; total_size=0;total_rows=0
    for filename,blob in files:
        total_size+=len(blob)
        if total_size>200*1024*1024:raise HTTPException(413,'本次文件合计超过 200 MB')
        digest=hashlib.sha256(blob).hexdigest()
        rows=(ledger.parse if acc.kind=='alipay' else ledger.parse_fund)(blob,filename,seed)
        rows=[ledger.apply_configured_rules(row,rules,acc.kind) for row in rows];total_rows+=len(rows)
        if total_rows>500000:raise HTTPException(413,'本次最多合并 500,000 行')
        parsed.append((filename,digest,rows))
    return store_parsed(account_id, parsed, operator)


def store_parsed(account_id, parsed, operator):
    total_rows=sum(len(rows) for _,_,rows in parsed)
    identities={r['account'] for _,_,rows in parsed for r in rows if r['account']}
    if len(identities)>1:raise HTTPException(400,'选中文件属于多个支付宝账户，请分别导入')
    actual=next(iter(identities),'');identity_hash=hashlib.sha256(actual.encode()).hexdigest() if actual else ''
    pids={r.get('account_pid') or ledger.merchant_id(filename) for filename,_,rows in parsed for r in rows}
    pids.discard('')
    if len(pids)>1:raise HTTPException(400,'选中文件包含多个支付宝商户 ID，请分账户导入')
    pid=next(iter(pids),'') or (actual if actual.startswith('2088') and len(actual)==16 else '')
    added=duplicate=conflicts=0
    with _import_lock, db._engine.begin() as cx:
        acc=account(cx,account_id)
        if pid and acc.suffix and not pid.endswith(acc.suffix):raise HTTPException(400,'原文件商户 ID 与登记尾号不一致，已拦截')
        if identity_hash and acc.identity_hash and identity_hash!=acc.identity_hash:raise HTTPException(400,'原文件账号与该账户历史流水不一致')
        if identity_hash:
            owner=cx.execute(select(A.c.id).where(A.c.identity_hash==identity_hash,A.c.id!=account_id)).first()
            if owner:raise HTTPException(400,'这个支付宝账户已登记，请使用已有账户以免重复统计')
            cx.execute(update(A).where(A.c.id==account_id).values(identity_hash=identity_hash,suffix=(pid[-4:] or acc.suffix) if not acc.suffix else acc.suffix))
        for filename,digest,rows in parsed:
            old=cx.execute(select(F).where(F.c.account_id==account_id,F.c.digest==digest)).first()
            if old:
                aliases=json.loads(old.aliases or '[]')
                if filename!=old.filename and filename not in aliases:
                    cx.execute(update(F).where(F.c.id==old.id).values(aliases=json.dumps(aliases+[filename],ensure_ascii=False)))
                duplicate+=len(rows);continue
            fid=cx.execute(insert(F).values(account_id=account_id,digest=digest,filename=filename,row_count=len(rows),operator=operator,ts=ec._now())).inserted_primary_key[0]
            existing={}
            # Load compact identity metadata once instead of one SELECT per source row.
            for previous in cx.execute(select(R.c.id,R.c.identity_key,R.c.fingerprint).where(R.c.account_id==account_id)):
                existing.setdefault(previous.identity_key,{})[previous.fingerprint]=(previous.id,None)
            for row in rows:
                origin=row.pop('source');row.pop('account',None)
                identity=ledger.fingerprint(['serial',row['serial']]) if row['serial'] else ledger.fingerprint([digest,origin['sheet'],origin['row']])
                variants=existing.setdefault(identity,{})
                prior=variants.get(row['fingerprint'])
                if prior: rid=prior[0];duplicate+=1
                else:
                    if variants:
                        conflicts+=1;row['flags'].append('同一流水号内容冲突：各版本保留待核对')
                        for old_id,old_payload in variants.values():
                            old_value=json.loads(old_payload or cx.execute(select(R.c.payload).where(R.c.id==old_id)).scalar_one())
                            flag='同一流水号内容冲突：各版本保留待核对'
                            if flag not in old_value['flags']:old_value['flags'].append(flag)
                            cx.execute(update(R).where(R.c.id==old_id).values(abnormal=1,payload=json.dumps(old_value,ensure_ascii=False)))
                    safe={k:v for k,v in row.items() if k!='fingerprint'}
                    payload=json.dumps(safe,ensure_ascii=False,default=str)
                    rid=cx.execute(insert(R).values(account_id=account_id,identity_key=identity,fingerprint=row['fingerprint'],
                        serial=row['serial'],txn=row['txn'],mch_no=row['mch_no'],order_no=row['order_no'],occurred_at=row['ts'],period=row['ts'][:7],
                        income=row['income'],outgo=row['outgo'],balance=row['balance'],bucket=row['bucket'],code=row['code'],channel=row['chan'],
                        abnormal=int(bool(row['flags'])),search_text='\n'.join(row['raw'].values()),payload=payload)).inserted_primary_key[0]
                    variants[row['fingerprint']]=(rid,payload);added+=1
                cx.execute(insert(O).values(flow_id=rid,file_id=fid,sheet=origin['sheet'],row_number=origin['row']))
    from routers import ec_workbench
    with ec_workbench._lock: ec_workbench._cache.clear()
    from routers.ec_documents import reconcile
    reconcile()
    return {'ok':True,'added':added,'duplicates':duplicate,'conflicts':conflicts,'source_rows':total_rows}


@router.post('/import')
async def upload(request:Request,account_id:str=Form(...),files:list[UploadFile]=File(...),background:bool=Form(False)):
    user=require(request,True)
    if not files or len(files)>20:raise HTTPException(400,'一次可选择 1–20 个文件')
    blobs=[];total_bytes=0
    for f in files:
        data=await f.read(30*1024*1024+1)
        if len(data)>30*1024*1024:raise HTTPException(413,'单个文件超过 30 MB')
        total_bytes+=len(data)
        if total_bytes>200*1024*1024:raise HTTPException(413,'本次文件合计超过 200 MB')
        blobs.append((Path((f.filename or '').replace('\\','/')).name[:180],data))
    if background:
        return start_import_job(account_id,blobs,user['name'])
    try:
        from starlette.concurrency import run_in_threadpool
        result=await run_in_threadpool(import_files,account_id,blobs,user['name'])
    except (ValueError,KeyError) as exc:raise HTTPException(400,str(exc)[:180])
    db.audit(user['name'],'ec_flow_import',target=account_id,detail=json.dumps(result))
    return result


def start_import_job(account_id,blobs,operator):
    with db._engine.connect() as cx:account(cx,account_id)
    with _jobs_lock:
        if any(j['status']=='running' for j in _jobs.values()):
            raise HTTPException(409,'已有流水导入在后台处理，请完成后再提交，避免重复排队')
        if len(_jobs)>=50:_jobs.clear()
        job_id=uuid.uuid4().hex
        _jobs[job_id]={'id':job_id,'account_id':account_id,'status':'running','files':len(blobs),'started_at':ec._now()}
    def run():
        try:
            result=import_files(account_id,blobs,operator)
            db.audit(operator,'ec_flow_import',target=account_id,detail=json.dumps(result))
            with _jobs_lock:_jobs[job_id].update(status='complete',result=result,finished_at=ec._now())
        except Exception as error:
            message=str(error.detail) if isinstance(error,HTTPException) else str(error) if isinstance(error,ValueError) else '导入失败：'+type(error).__name__
            with _jobs_lock:_jobs[job_id].update(status='failed',error=message[:200],finished_at=ec._now())
    threading.Thread(target=run,daemon=True).start()
    return {'ok':True,'job_id':job_id,'status':'running'}


@router.get('/import-status/{job_id}')
def import_status(request:Request,job_id:str):
    require(request)
    with _jobs_lock:
        if job_id not in _jobs:
            raise HTTPException(404,'导入进度记录已失效（可能服务重启）；请先刷新流水核对结果，再用原文件重试，已入库文件会去重')
        return dict(_jobs[job_id],ok=True)


@router.get('')
def search(request:Request,account_id:str='',period:str='',q:str='',bucket:str='',abnormal:bool=False,review_status:str='',match_status:str='',
           date_from:str='',date_to:str='',direction:str='',amount_min:str='',amount_max:str='',page:int=1,size:int=50):
    require(request);where=[]
    M=db.ec_flow_matches
    if match_status:
        if match_status=='pending_index':where.append(R.c.id.not_in(select(M.c.flow_id)))
        else:where.append(R.c.id.in_(select(M.c.flow_id).where(M.c.status==match_status)))
    if account_id:where.append(R.c.account_id==account_id)
    if period:
        check_period(period);where.append(R.c.period==period)
    if date_from:where.append(R.c.occurred_at>=date_from)
    if date_to:where.append(R.c.occurred_at<date_to[:10]+' 24:00:00')
    if q:
        q=q.strip()[:200]
        where.append(or_(R.c.serial==q,R.c.txn==q,R.c.order_no==q,R.c.mch_no==q,R.c.search_text.contains(q,autoescape=True),R.c.id.in_(select(M.c.flow_id).where(M.c.order_no==q))))
    if abnormal:where.append(R.c.abnormal==1)
    if review_status=='待核对':where.append(R.c.id.not_in(select(db.ec_flow_reviews.c.flow_id).where(db.ec_flow_reviews.c.verdict.in_(['正常','待追查']))))
    elif review_status in ('正常','待追查'):where.append(R.c.id.in_(select(db.ec_flow_reviews.c.flow_id).where(db.ec_flow_reviews.c.verdict==review_status)))
    if direction=='income':where.append(R.c.income!=0)
    elif direction=='outgo':where.append(R.c.outgo!=0)
    amount_col=R.c.income if direction=='income' else R.c.outgo if direction=='outgo' else func.abs(R.c.income-R.c.outgo)
    try:
        if amount_min:where.append(amount_col>=ledger.decimal(amount_min))
        if amount_max:where.append(amount_col<=ledger.decimal(amount_max))
    except ValueError:raise HTTPException(400,'金额筛选格式错误')
    bucket_where=list(where)
    if bucket:where.append(R.c.bucket==bucket)
    page=max(1,page);size=max(10,min(100,size))
    with db._engine.connect() as cx:
        stats=cx.execute(select(func.count(),func.sum(R.c.income),func.sum(R.c.outgo),func.sum(R.c.abnormal)).where(*where)).first()
        records=cx.execute(select(R.c.id,R.c.account_id,R.c.payload,A.c.name).join(A,A.c.id==R.c.account_id).where(*where).order_by(R.c.occurred_at.desc(),R.c.id.desc()).offset((page-1)*size).limit(size)).fetchall()
        buckets=cx.execute(select(R.c.bucket,func.count()).where(*bucket_where).group_by(R.c.bucket)).fetchall()
    rows=[]
    with db._engine.connect() as cx:
        matches={m.flow_id:dict(m._mapping) for m in cx.execute(select(M).where(M.c.flow_id.in_([v.id for v in records])))} if records else {}
        reviews={r.flow_id:dict(r._mapping) for r in cx.execute(select(db.ec_flow_reviews).where(db.ec_flow_reviews.c.flow_id.in_([v.id for v in records])))} if records else {}
    for r in records:
        payload=ledger.resolve_order(json.loads(r.payload));payload.pop('raw',None)
        match=matches.get(r.id)
        if match:match.pop('payload',None)
        rows.append(dict(payload,id=r.id,account_id=r.account_id,account_name=r.name,review=reviews.get(r.id),document_match=match))
    return {'ok':True,'rows':rows,'total':stats[0],'income':float(stats[1] or 0),'outgo':float(stats[2] or 0),
        'flagged':stats[3] or 0,'page':page,'pages':max(1,(stats[0]+size-1)//size),'buckets':dict(buckets),
        'notice':'同流水号冲突版本保留在搜索结果中；搜索合计不是已确认账户余额或可入账金额。'}


@router.get('/detail/{row_id}')
def detail(request:Request,row_id:int):
    user=require(request)
    with db._engine.connect() as cx:
        row=cx.execute(select(R).where(R.c.id==row_id)).first()
        if not row:raise HTTPException(404,'流水不存在')
        sources=cx.execute(select(O.c.sheet,O.c.row_number,F.c.filename,F.c.aliases,F.c.ts).join(F,F.c.id==O.c.file_id).where(O.c.flow_id==row_id)).fetchall()
        acc=account(cx,row.account_id)
        match=cx.execute(select(db.ec_flow_matches).where(db.ec_flow_matches.c.flow_id==row_id)).first()
        match=dict(match._mapping) if match else None
        if match:
            match['evidence']=json.loads(match.pop('payload'))
            file_ids={d['file_id'] for d in match['evidence']['documents']}
            names={f.id:f.filename for f in cx.execute(select(db.ec_document_files).where(db.ec_document_files.c.id.in_(file_ids)))} if file_ids else {}
            for d in match['evidence']['documents']:d['filename']=names.get(d['file_id'],'')
        historical=cx.execute(select(db.ec_excl_notes).where(db.ec_excl_notes.c.period==row.period,db.ec_excl_notes.c.serial==row.serial,
            db.ec_excl_notes.c.shop.in_(json.loads(acc.shops or '[]')))).fetchall() if row.serial else []
        review=cx.execute(select(db.ec_flow_reviews).where(db.ec_flow_reviews.c.flow_id==row_id)).first()
    db.audit(user['name'],'ec_flow_detail',target=str(row_id),detail='查看流水原始字段；未写金蝶')
    return {'ok':True,'row':dict(ledger.resolve_order(json.loads(row.payload)),document_match=match),'sources':[dict(r._mapping,aliases=json.loads(r.aliases or '[]')) for r in sources],
        'review':dict(review._mapping) if review else None,
        'historical_reviews':[dict(r._mapping) for r in historical],
        'history_note':'旧记录按店铺、期间和流水号关联，未含账户维度；请核实后使用，不自动沿用旧定性。'}


@router.post('/review')
async def review_save(request:Request):
    user=require(request,True);body=await request.json()
    ids=body.get('ids',[]);verdict=body.get('verdict');note=str(body.get('note') or '').strip()
    if not isinstance(ids,list) or not ids or len(ids)>500 or not all(isinstance(v,int) and v>0 for v in ids):raise HTTPException(400,'每次选择 1–500 笔流水')
    if verdict not in ('正常','待追查','待核对') or not note or len(note)>1000:raise HTTPException(400,'请选择定性并填写活动、推广或资金去向依据（1–1000 字）')
    ids=sorted(set(ids))
    with db._engine.begin() as cx:
        existing=set(cx.execute(select(R.c.id).where(R.c.id.in_(ids))).scalars())
        if existing!=set(ids):raise HTTPException(400,'部分流水已不存在，未保存')
        cx.execute(delete(db.ec_flow_reviews).where(db.ec_flow_reviews.c.flow_id.in_(ids)))
        cx.execute(insert(db.ec_flow_reviews),[dict(flow_id=i,verdict=verdict,note=note,operator=user['name'],ts=ec._now()) for i in ids])
    db.audit(user['name'],'ec_flow_review',target=','.join(map(str,ids)),detail=verdict+'；'+note)
    return {'ok':True,'count':len(ids)}


def _rule(rule_id):
    rule=next((r for r in configured_rules() if r['id']==rule_id),None)
    if not rule:raise HTTPException(404,'流水分类规则不存在，请刷新基础资料')
    if not rule['enabled']:raise HTTPException(400,'该规则已停用')
    return rule


def _rule_matches(cx,rule):
    reviewed=select(db.ec_flow_reviews.c.flow_id)
    candidates=cx.execute(select(R.c.id,R.c.payload,R.c.income,R.c.outgo,A.c.name,A.c.kind)
        .join(A,A.c.id==R.c.account_id).where(R.c.bucket=='unknown').order_by(R.c.occurred_at.desc(),R.c.id.desc())).fetchall()
    matched=[];skipped=conflicts=0;rules=configured_rules()
    review_ids=set(cx.execute(reviewed).scalars())
    for row in candidates:
        payload=json.loads(row.payload)
        if not ledger.rule_matches(payload,rule,row.kind):continue
        if row.id in review_ids:skipped+=1;continue
        if len([candidate for candidate in rules if ledger.rule_matches(payload,candidate,row.kind)])>1:
            conflicts+=1;continue
        matched.append((row,payload))
    return matched,skipped,conflicts


@router.post('/rules/preview')
async def rule_preview(request:Request):
    require(request);body=await request.json();rule=_rule(str(body.get('id') or ''))
    with db._engine.connect() as cx:matched,skipped,conflicts=_rule_matches(cx,rule)
    return {'ok':True,'rule':rule,'matched':len(matched),'skipped_reviewed':skipped,'skipped_conflicts':conflicts,
        'income':float(sum((r.income or 0 for r,_ in matched),Decimal('0'))),
        'outgo':float(sum((r.outgo or 0 for r,_ in matched),Decimal('0'))),
        'samples':[{'id':r.id,'account_name':r.name,'ts':p.get('ts'),'serial':p.get('serial'),
            'order_no':p.get('order_no') or p.get('mch_no'),'text':p.get(rule['field']),'income':float(r.income or 0),'outgo':float(r.outgo or 0)}
            for r,p in matched[:10]]}


@router.post('/rules/apply')
async def rule_apply(request:Request):
    user=_require_perm(request,'ec_base_edit')
    if not user:raise HTTPException(403,'需要「维护基础资料」权限')
    body=await request.json();rule=_rule(str(body.get('id') or ''))
    if body.get('confirmed') is not True:raise HTTPException(400,'请先预览并确认命中结果')
    expected=body.get('expected_count')
    if not isinstance(expected,int) or expected<0:raise HTTPException(400,'预览笔数无效，请重新预览')
    with db._engine.begin() as cx:
        matched,skipped,conflicts=_rule_matches(cx,rule)
        if len(matched)!=expected:raise HTTPException(409,'待处理流水已变化，请重新预览后确认')
        for row,payload in matched:
            updated=ledger.apply_rule(payload,rule)
            cx.execute(update(R).where(R.c.id==row.id).values(bucket=updated['bucket'],
                abnormal=int(bool(updated.get('flags'))),payload=json.dumps(updated,ensure_ascii=False)))
    db.audit(user['name'],'ec_flow_rule_apply',target=rule['id'],
        detail='批量归类 %d 笔；跳过人工定性 %d 笔；规则冲突 %d 笔；未写金蝶' % (len(matched),skipped,conflicts))
    from routers import ec_workbench
    with ec_workbench._lock:ec_workbench._cache.clear()
    return {'ok':True,'applied':len(matched),'skipped_reviewed':skipped,'skipped_conflicts':conflicts,'bucket':rule['bucket'],'label':rule['label']}
