# V2.556: unified ecommerce workbench. Kingdee access is explicitly read-only.
import datetime
import gzip
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from fastapi import APIRouter, Request, UploadFile, File, Form, HTTPException
from sqlalchemy import select, insert
from sqlalchemy.exc import IntegrityError
from core import db, _require_perm
from routers import ec
from kernels import ec_workbench as model
from kernels import ec_flow_ledger as ledger

router = APIRouter(prefix='/api/ec/workbench')
TABLE = db.ec_workbench_imports
_cache = {}
_lock = threading.RLock()
_sync = {}
TARGET = ec.fulfillment.TARGET_SHOP


def require(request, write=False):
    user = _require_perm(request, 'ec_settle_upload' if write else 'enter:ecomsettle')
    if not user: raise HTTPException(403, '无电商工作台权限')
    return user


def check_period(period):
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', period or ''): raise HTTPException(400, '期间格式 YYYY-MM')


def shops():
    with db._engine.connect() as cx:
        rows = [dict(r._mapping) for r in cx.execute(select(db.ec_shop_map))]
    if not rows:
        rows = [{'wdt_name':w,'kd_name':k,'platform':p} for k,w,p in ec.es.SHOP_MAP_SEED]
    if not any(r['wdt_name']==TARGET for r in rows):
        rows.append({'wdt_name':TARGET,'kd_name':TARGET,'platform':'天猫'})
    return [dict(id=r['wdt_name'], name=r.get('mgmt_name') or r['wdt_name'], platform=r.get('platform') or '未设置',
                 kd_name=r.get('kd_name') or r['wdt_name'], account_key=hashlib.sha256(str(r.get('alipay_acct') or r['wdt_name']).encode()).hexdigest()[:16]) for r in rows]


def check_shop(shop):
    selected = next((s for s in shops() if s['id']==shop), None)
    if not selected: raise HTTPException(400, '店铺不在基础资料中')
    return selected


def save_source(period, shop, kind, digest, filenames, payload, operator):
    packed = gzip.compress(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode())
    try:
        with db._engine.begin() as cx:
            rid = cx.execute(insert(TABLE).values(period=period, shop=shop, kind=kind, digest=digest,
                filenames=json.dumps(filenames, ensure_ascii=False), payload=packed, operator=operator, ts=ec._now())).inserted_primary_key[0]
    except IntegrityError:
        with db._engine.connect() as cx:
            rid = cx.execute(select(TABLE.c.id).where(TABLE.c.period==period, TABLE.c.shop==shop,
                TABLE.c.kind==kind, TABLE.c.digest==digest)).scalar_one()
        return {'id':rid,'duplicate':True}
    with _lock: _cache.clear()
    return {'id':rid,'duplicate':False}


def load_sources(period, shop):
    with db._engine.connect() as cx:
        records = cx.execute(select(TABLE).where(TABLE.c.period==period, TABLE.c.shop==shop).order_by(TABLE.c.id.desc())).fetchall()
    sources, provenance = {}, {}
    for r in records:
        if r.kind in sources: continue
        sources[r.kind] = json.loads(gzip.decompress(r.payload))
        provenance[r.kind] = {'id':r.id,'imported_at':r.ts,'filenames':json.loads(r.filenames or '[]'), 'legacy':False}
    # Existing evidence remains readable without a destructive migration. Only the original target shop owns it.
    if shop == TARGET:
        legacy = ec._tmall_latest_rows(period)
        for kind, run in legacy.items():
            if kind in sources: continue
            sources[kind] = {'rows':[], 'summary':json.loads(run.summary or '{}'), 'status':run.status,
                             'index':ec._unpack_tmall_index(run.match_index), 'business':ec._tmall_business_index(run) if kind=='item' else {}}
            provenance[kind] = {'id':run.id, 'imported_at':run.ts,'filenames':[], 'legacy':True}
            if kind=='order':
                with db._engine.connect() as cx:
                    sources[kind]['rows'] = [dict(x._mapping) for x in cx.execute(select(db.ec_tmall_order_details).where(db.ec_tmall_order_details.c.run_id==run.id))]
        if 'wdt' not in sources:
            with db._engine.connect() as cx:
                old = cx.execute(select(db.ec_wdt_import_runs).where(db.ec_wdt_import_runs.c.period==period).order_by(db.ec_wdt_import_runs.c.id.desc()).limit(1)).first()
            if old:
                snap = ec._wdt_snapshot(period, old.id)
                order_map = {r['order_key']:r['order_no'] for r in sources.get('order',{}).get('rows',[])}
                rows = []
                for key, value in snap.get('orders',{}).items():
                    for doc in value.get('documents',[]):
                        for item in doc.get('items',[]):
                            rows.append(dict(item,order_no=order_map.get(key,''),shipment_no=doc['shipment_no'],date=(doc.get('dates') or [''])[0],status='已完成',receivable=None))
                sources['wdt']={'rows':rows,'status':'warning','summary':json.loads(old.summary or '{}')}
                provenance['wdt']={'id':old.id,'imported_at':old.ts,'filenames':[],'legacy':True}
    # Dedicated account ledger is the cash evidence source once imported. The account may
    # serve multiple shops; exact platform order IDs keep allocation separate from balances.
    with db._engine.connect() as cx:
        accounts=[dict(a._mapping) for a in cx.execute(select(db.ec_flow_accounts)) if shop in json.loads(a.shops or '[]')]
        for kind in ('alipay','fund'):
            ids=[a['id'] for a in accounts if a['kind']==kind]
            if not ids:continue
            statement_rows=cx.execute(select(db.ec_flow_rows).where(db.ec_flow_rows.c.account_id.in_(ids),db.ec_flow_rows.c.period==period))
            events=[];conflicted=set()
            for record in statement_rows:
                value=ledger.resolve_order(json.loads(record.payload))
                linked=value['order_link']['order_no']
                key=model.tm._order_key(linked) if linked else ''
                if value['order_link']['status']=='conflict':
                    conflicted.update(model.tm._order_key(no) for no in
                        [value.get('order_no','')]+[e['order_no'] for e in value['supplement']['evidence']] if no)
                    continue
                if any('同一流水号内容冲突' in flag for flag in value.get('flags',[])):
                    if key:conflicted.add(key)
                    continue
                event=model.event(value,kind)
                event['order_link']=value['order_link']
                event.update(order_key=key,account_id=record.account_id,account_name=next(a['name'] for a in accounts if a['id']==record.account_id),
                    ledger_id=record.id,aggregate_copy=kind=='alipay' and value.get('chan')=='聚合结算渠道')
                if value['bucket']=='receipt':event['kind']='receipt'
                elif value['bucket']=='refund':event['kind']='refund'
                elif value['bucket']=='adjustment':event['kind']='adjustment'
                elif value['bucket'] in ('fee','ufirst_fee') and event['kind'] not in ('receipt','refund','transfer','adjustment'):event['kind']='fee'
                elif value['bucket'] in ('transfer','recharge'):event['kind']='transfer'
                events.append(event)
            if not events and not conflicted:continue
            files=list(cx.execute(select(db.ec_flow_files).where(db.ec_flow_files.c.id.in_(
                select(db.ec_flow_origins.c.file_id).join(db.ec_flow_rows,db.ec_flow_rows.c.id==db.ec_flow_origins.c.flow_id)
                .where(db.ec_flow_rows.c.account_id.in_(ids),db.ec_flow_rows.c.period==period)))))
            sources[kind]={'rows':events,'status':'warning' if conflicted else 'ready','conflicted_keys':list(conflicted),
                'summary':{'file_count':len(files)},'warnings':['存在流水号内容冲突，相关订单金额不下结论'] if conflicted else []}
            provenance[kind]={'id':'account-ledger','imported_at':max((f.ts for f in files),default=''),'filenames':[f.filename for f in files], 'legacy':False}
    return sources, provenance


def get_data(period, shop):
    # Bound memory; input and cache metadata changes invalidate within 20 seconds at most.
    cache_key = (period,shop)
    with _lock:
        hit = _cache.get(cache_key)
        if hit and time.monotonic()-hit[0]<20: return hit[1]
    sources, provenance = load_sources(period,shop)
    for source in sources.values(): source.setdefault('period',period)
    ar, ar_state = ec._month_ar_cache(period)
    selected = check_shop(shop)
    # Kingdee customer is part of the join, not only the platform order string.
    if ar is not None:
        try:
            raw = json.loads(Path(ec._kd_cache_path(period)).read_text(encoding='utf-8'))
            customer_fields = ('客户','客户名称')
            if raw and any(f in raw[0] for f in customer_fields):
                raw = [r for r in raw if any(str(r.get(f) or '')==selected['kd_name'] for f in customer_fields)]
                ar, _ = ec.month_ar.index_receivables(raw)
        except (OSError, ValueError):
            ar = None
    rules = db.get_setting('ec_workbench_rules',{}) or {}
    recognition_rules = db.get_setting('ec_income_recognition_rules',{}) or {}
    rule = rules.get(period,{}).get(shop) or {'recognition':recognition_rules.get(shop,'shipment')}
    rows = model.build_orders(sources,ar,rule.get('recognition','shipment'))
    conflicted=set(sources.get('alipay',{}).get('conflicted_keys',[])+sources.get('fund',{}).get('conflicted_keys',[]))
    for row in rows:
        if row['order_key'] in conflicted:
            row.update(fees=None,net_receipt=None,manual=True)
            row['issues'].append('流水内容或订单关联冲突：费用与净收待核对')
    cash_available = any(sources.get(k,{}).get('rows') for k in ('alipay','fund'))
    if not cash_available:
        for r in rows: r['fees']=None
    data = {'sources':sources,'provenance':provenance,'rows':rows,'kingdee':ar_state,'rule':rule,'cash_available':cash_available}
    with _lock:
        if len(_cache)>=12: _cache.clear()
        _cache[cache_key]=(time.monotonic(),data)
    return data


def selected_rows(rows,business):
    return [r for r in rows if not business or r['business_type']==business]


def source_cards(data):
    out=[]
    for kind,label in model.KINDS.items():
        s=data['sources'].get(kind)
        p=data['provenance'].get(kind,{})
        out.append({'kind':kind,'label':label,'available':bool(s),'state':s.get('status','warning') if s else 'missing',
            'rows':len(s.get('rows',[])) if s else None,'warnings':s.get('warnings',[]) if s else [],
            'files':p.get('filenames',[]),'file_count':s.get('summary',{}).get('file_count',1) if s else 0,
            'imported_at':p.get('imported_at'),'legacy':p.get('legacy',False),'version':p.get('id')})
    return out


@router.get('/shops')
def shops_view(request:Request):
    """Lightweight shop selector sourced only from controlled basic data."""
    require(request)
    return {'ok':True,'shops':shops()}


@router.get('/overview')
def overview(request:Request,period:str,business:str=''):
    require(request); check_period(period)
    result=[]; all_rows=[]; accounts={}; any_cash=False
    for shop in shops():
        data=get_data(period,shop['id']); rows=selected_rows(data['rows'],business)
        cards=source_cards(data); ready=sum(c['available'] and c['state']=='ready' for c in cards)
        present=sum(c['available'] for c in cards)
        any_cash |= data['cash_available']
        all_rows.extend(rows)
        result.append(dict(shop,available='order' in data['sources'],metrics=model.metrics(rows,data['cash_available']) if 'order' in data['sources'] else None,
            ready=ready,required=len(cards),files=sum(c['file_count'] for c in cards),
            readiness='missing' if not present else 'ready' if ready==len(cards) else 'warning'))
        if shop['platform'] in ('天猫','淘宝'):
            for kind,label in (('alipay','支付宝'),('fund','聚合账户')):
                key=kind+':'+(shop['account_key'] if kind=='alipay' else shop['id'])
                entry=accounts.setdefault(key,{'id':key,'label':label,'shops':[],'balance':None})
                entry['shops'].append(shop['name'])
                bal=data['sources'].get(kind,{}).get('balance')
                if bal and (not entry['balance'] or bal['as_of']>entry['balance']['as_of']): entry['balance']=bal
    return {'ok':True,'period':period,'shops':result,'totals':model.metrics(all_rows,any_cash),
        'coverage':{'available':sum(s['available'] for s in result),'total':len(result)},'accounts':list(accounts.values()),
        'kingdee_read_only':True,'basis':'订单按创建月归属；GMV为该批订单支付成功金额，GSV扣除对应订单已成功退款。资金仅覆盖已导入流水期间。'}


@router.get('/sources')
def sources_view(request:Request,period:str,shop:str):
    require(request); check_period(period); selected=check_shop(shop)
    data=get_data(period,shop)
    return {'ok':True,'shop':selected,'sources':source_cards(data),'kingdee':data['kingdee'],
        'rule':data['rule'],'collector_configured':bool(os.environ.get('EC_INBOX_ROOT')),
        'voucher_sync':_sync.get((period,shop),{}),'kingdee_read_only':True}


@router.get('/orders')
def orders_view(request:Request,period:str,shop:str,q:str='',business:str='',channel:str='',flag:str='',page:int=1,size:int=30):
    require(request); check_period(period); check_shop(shop)
    data=get_data(period,shop); rows=selected_rows(data['rows'],business)
    if channel=='alipay': rows=[r for r in rows if '支付宝' in r['destination']]
    elif channel=='fund': rows=[r for r in rows if '聚合账户' in r['destination']]
    elif channel=='pending': rows=[r for r in rows if r['destination']=='待结算 / 待补流水']
    elif channel=='multiple': rows=[r for r in rows if '、' in r['destination']]
    if q:
        q=q.strip().casefold()
        rows=[r for r in rows if q in r['order_no'].casefold() or any(q in str(x.get('sku','')).casefold() or q in str(x.get('name','')).casefold() for x in r['items'])]
    if flag=='manual': rows=[r for r in rows if r['manual']]
    elif flag=='unshipped': rows=[r for r in rows if r['unshipped']]
    elif flag in ('refund_only','return_refund','unknown_refund','refund'): rows=[r for r in rows if r[flag]>0]
    elif flag=='fees': rows=[r for r in rows if r['fees'] not in (None,0)]
    elif flag=='ar_missing': rows=[r for r in rows if r['should_ar'] and not r['ar_documents']]
    rows=sorted(rows,key=lambda r:r.get('created_at',''),reverse=True)
    page=max(1,page);size=min(100,max(10,size));count=len(rows)
    visible=[{k:v for k,v in r.items() if k not in ('events','fee_events','items','shipments','refunds','order_key','id','run_id')} for r in rows[(page-1)*size:page*size]]
    return {'ok':True,'rows':visible,'total':count,'page':page,'pages':max(1,(count+size-1)//size),'metrics':model.metrics(rows,data['cash_available'])}


@router.get('/order')
def order_view(request:Request,period:str,shop:str,order_no:str):
    require(request);check_period(period);check_shop(shop)
    data=get_data(period,shop)
    row=next((r for r in data['rows'] if r['order_no']==order_no),None)
    if not row: raise HTTPException(404,'本期本店未找到订单')
    # Summary vouchers are period/shop references, NOT exact per-order links.
    docs=data['sources'].get('kingdee_docs',{})
    return {'ok':True,'order':row,'provenance':data['provenance'],
        'vouchers':docs.get('vouchers',[]) if row['business_type'] in ('ufirst','mixed') else [],
        'voucher_relation':'本店本期汇总参考，尚未建立逐单凭证关联',
        'receipt_relation':'未获取单据来源链，不能仅凭摘要确认收款单与订单关系',
        'kingdee_read_only':True}


@router.post('/upload')
async def upload(request:Request,period:str=Form(...),shop:str=Form(...),kind:str=Form(...),files:list[UploadFile]=File(...)):
    user=require(request,True);check_period(period);s=check_shop(shop)
    if kind in ('alipay','fund'):
        raise HTTPException(400,'资金流水请在账户流水页按账户导入；数据准备会自动引用，避免两套金额来源')
    if s['platform'] not in ('天猫','淘宝') and kind not in ('wdt',):
        raise HTTPException(400,'当前先验收天猫；其他平台不得用天猫字段模板强行导入')
    if not files or len(files)>20: raise HTTPException(400,'每次最多 20 个文件')
    blobs=[]
    for f in files:
        blob=await f.read(model.tm.MAX_ALIPAY_FILE_BYTES+1)
        if len(blob)>model.tm.MAX_ALIPAY_FILE_BYTES: raise HTTPException(413,'单个文件超过 30 MB')
        blobs.append(blob)
    try:
        from starlette.concurrency import run_in_threadpool
        parsed=await run_in_threadpool(model.parse_source,kind,blobs,period,shop)
    except (ValueError,TypeError,KeyError) as exc:
        raise HTTPException(400,str(exc)[:200])
    digest=hashlib.sha256(''.join(sorted(hashlib.sha256(b).hexdigest() for b in blobs)).encode()).hexdigest()
    filenames=[Path(f.filename.replace('\\','/')).name[:180] for f in files]
    result=save_source(period,shop,kind,digest,filenames,parsed,user['name'])
    db.audit(user['name'],'ec_workbench_import',target=period,detail=f"{kind} 快照 {result['id']}")
    return dict(result,ok=True,rows=len(parsed['rows']),warnings=parsed['warnings'])


@router.post('/rules')
async def rules_save(request:Request):
    user=require(request,True);body=await request.json();period=body.get('period');shop=body.get('shop')
    check_period(period);check_shop(shop)
    recognition=body.get('recognition')
    if recognition not in ('shipment','confirmed'):raise HTTPException(400,'不支持的收入确认口径')
    rules=db.get_setting('ec_workbench_rules',{}) or {}
    rules.setdefault(period,{})[shop]={'recognition':recognition,'operator':user['name'],'updated_at':ec._now()}
    db.set_setting('ec_workbench_rules',rules,operator=user['name'])
    with _lock:_cache.clear()
    return {'ok':True}


def sync_vouchers(period,shop,operator):
    selected=check_shop(shop);s,conf=ec.kc.login()
    # Explicitly use ExecuteBillQuery only. Never call generic save/submit helpers.
    fields=[('FBillNo','bill'),('FDATE','date'),('FVOUCHERGROUPID.FName','group'),('FVOUCHERGROUPNO','number'),
        ('FACCOUNTBOOKID.FNumber','book'),('FEXPLANATION','memo'),('FACCOUNTID.FNumber','account'),
        ('FACCOUNTID.FName','account_name'),('FDEBIT','debit'),('FCREDIT','credit'),('FDOCUMENTSTATUS','status')]
    book=str((db.get_setting('ec_voucher_cfg',{}) or {}).get('book_code') or '101').replace("'",'')
    scope=f"FYEAR={int(period[:4])} and FPERIOD={int(period[5:])} and FACCOUNTBOOKID.FNumber='{book}'"
    name=selected['kd_name'].replace("'","''").replace('%','').replace('_','')
    hits=ec.kc._query(s,conf,'GL_VOUCHER',fields,scope+f" and FEXPLANATION like '%{name}%'",'FBillNo')
    vouchers=[]
    for bill in sorted({str(r['bill']) for r in hits}):
        safe_bill=bill.replace("'","''")
        lines=ec.kc._query(s,conf,'GL_VOUCHER',fields,scope+f" and FBillNo='{safe_bill}'",'FBillNo')
        # Only accounting summaries; long customer/order numbers in arbitrary memo are redacted.
        for line in lines: line['memo']=re.sub(r'(?<![A-Za-z])\d{12,}', '[编号省略]',str(line.get('memo') or ''))[:300]
        if not lines:continue
        vouchers.append({'bill':bill,'number':str(lines[0]['group'])+'-'+str(lines[0]['number']),
            'date':lines[0]['date'],'status':lines[0]['status'],'lines':lines,
            'receipt_references':sorted(set(re.findall(r'SKD\d+', ' '.join(r['memo'] for r in lines)))),
            'ufirst':any('U先' in r['memo'] or 'u先' in r['memo'] for r in lines)})
    payload={'vouchers':vouchers,'synced_at':ec._now(),'read_only':True,'rows':[]}
    save_source(period,shop,'kingdee_docs',hashlib.sha256(json.dumps(payload).encode()).hexdigest(),[],payload,operator)


@router.post('/kingdee-docs/refresh')
async def voucher_refresh(request:Request):
    user=require(request,True);body=await request.json();period=body.get('period');shop=body.get('shop')
    check_period(period);check_shop(shop);key=(period,shop)
    with _lock:
        if _sync.get(key,{}).get('running'):raise HTTPException(409,'正在只读查询')
        _sync[key]={'running':True,'error':''}
    def job():
        try:sync_vouchers(period,shop,user['name'])
        except Exception as exc:_sync[key]['error']='凭证查询失败：'+type(exc).__name__
        finally:_sync[key]['running']=False
    threading.Thread(target=job,daemon=True).start()
    return {'ok':True}


@router.get('/preview')
def preview(request:Request,period:str,shop:str):
    require(request);check_period(period);check_shop(shop)
    data=get_data(period,shop);uf=[r for r in data['rows'] if r['business_type']=='ufirst']
    refs=data['sources'].get('kingdee_docs',{}).get('vouchers',[])
    return {'ok':True,'read_only':True,'existing_vouchers':refs,'ufirst':model.metrics(uf,data['cash_available']),
        'notice':'已有凭证优先对照；U先收入与普通结算可共用一张凭证。此页仅预览，不创建金蝶单据。',
        'draft':{'status':'待确认模板与来源链','blocked':True,
            'checks':['确认账户与辅助核算','确认含税金额与税额','核对历史已入账，防止重复','确认收款单来源关系后才可下推']}}


@router.get('/history')
def history(request:Request,period:str,shop:str):
    require(request);check_period(period);check_shop(shop)
    with db._engine.connect() as cx:
        rows=cx.execute(select(db.ec_settle_runs).where(db.ec_settle_runs.c.period==period,db.ec_settle_runs.c.shop==shop).order_by(db.ec_settle_runs.c.id.desc())).fetchall()
    return {'ok':True,'rows':[{'id':r.id,'period':r.period,'status':r.status,'ts':r.ts,'stats':json.loads(r.stats or '{}')} for r in rows]}


@router.get('/inbox')
def inbox(request:Request,period:str,shop:str):
    require(request);check_period(period);check_shop(shop)
    configured=os.environ.get('EC_INBOX_ROOT')
    if not configured:return {'ok':True,'configured':False,'files':[],'message':'服务器未挂载公盘。当前可按店铺、数据类别上传文件；公盘取件须配置服务器可访问目录。'}
    root=Path(configured).resolve();folder=(root/period/shop).resolve()
    if not folder.is_relative_to(root):raise HTTPException(400,'目录超出配置范围')
    result=[]
    if folder.is_dir():
        for path in folder.rglob('*'):
            if len(result)>=200:break
            resolved=path.resolve()
            if resolved.is_relative_to(root) and resolved.is_file() and resolved.suffix.lower() in ('.xlsx','.xls','.csv','.zip'):
                result.append({'name':str(resolved.relative_to(folder)),'bytes':resolved.stat().st_size})
    return {'ok':True,'configured':True,'files':result,'message':'按期间/店铺读取配置目录，原文件不修改。'}
