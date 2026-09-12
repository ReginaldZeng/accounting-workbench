# V2.556: unified ecommerce workbench. Kingdee access is explicitly read-only.
import datetime
import gzip
import hashlib
import json
import os
import re
import threading
from pathlib import Path
from fastapi import APIRouter, Request, UploadFile, File, Form, HTTPException
from sqlalchemy import select, insert, func
from sqlalchemy.exc import IntegrityError
from core import db, _require_perm
from routers import ec
from kernels import ec_workbench as model
from kernels import ec_flow_ledger as ledger
from kernels import ec_preparation as preparation
from kernels import ec_documents as documents
from kernels import ec_order_store as order_store

router = APIRouter(prefix='/api/ec/workbench')
TABLE = db.ec_workbench_imports
_lock = threading.RLock()
_sync = {}
TARGET = ec.fulfillment.TARGET_SHOP
_result_wake=threading.Event()
_result_stop=threading.Event()
_result_thread=None


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
    _result_wake.set()
    return {'id':rid,'duplicate':False}


def load_sources(period, shop):
    with db._engine.connect() as cx:
        latest=select(func.max(TABLE.c.id)).where(TABLE.c.period==period,TABLE.c.shop==shop).group_by(TABLE.c.kind)
        records = cx.execute(select(TABLE).where(TABLE.c.id.in_(latest))).fetchall()
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
                event['rule_id']=value.get('rule_id','')
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


def compute_data(period, shop):
    """Background builder only. HTTP reads use ec_order_store, never this function."""
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
    rows = model.build_orders(sources,ar,rule.get('recognition','shipment'),db.get_setting('ec_fee_display_categories',{}) or {})
    conflicted=set(sources.get('alipay',{}).get('conflicted_keys',[])+sources.get('fund',{}).get('conflicted_keys',[]))
    for row in rows:
        if row['order_key'] in conflicted:
            row.update(fees=None,routine_fee=None,commission_fee=None,unclassified_fee=None,settlement_state='review',net_receipt=None,manual=True)
            row['issues'].append('流水内容或订单关联冲突：费用与净收待核对')
    cash_available = any(sources.get(k,{}).get('rows') for k in ('alipay','fund'))
    if not cash_available:
        for r in rows: r.update(fees=None,routine_fee=None,commission_fee=None,unclassified_fee=None,settlement_state='unknown')
    data = {'sources':sources,'provenance':provenance,'rows':rows,'kingdee':ar_state,'rule':rule,'cash_available':cash_available}
    return data


def mark_order_inputs_changed(account_ids):
    import uuid
    # Separate keys avoid losing another account's revision during concurrent imports.
    for aid in set(account_ids):
        db.set_setting('ec_order_account_revision:'+aid,uuid.uuid4().hex,operator='工作台结果更新')
    _result_wake.set()


def result_inputs():
    """Small metadata manifest; no XLSX parsing, statement payloads or Kingdee requests."""
    with db._engine.connect() as cx:
        latest=select(func.max(TABLE.c.id)).where(TABLE.c.kind!='kingdee_docs').group_by(TABLE.c.period,TABLE.c.shop,TABLE.c.kind)
        imports=[dict(r._mapping) for r in cx.execute(select(TABLE.c.id,TABLE.c.period,TABLE.c.shop,TABLE.c.kind,TABLE.c.digest).where(TABLE.c.id.in_(latest)))]
        legacy=[tuple(r) for r in cx.execute(select(db.ec_tmall_import_runs.c.period,db.ec_tmall_import_runs.c.kind,func.max(db.ec_tmall_import_runs.c.id)).group_by(db.ec_tmall_import_runs.c.period,db.ec_tmall_import_runs.c.kind))]
        wdt=[tuple(r) for r in cx.execute(select(db.ec_wdt_import_runs.c.period,func.max(db.ec_wdt_import_runs.c.id)).group_by(db.ec_wdt_import_runs.c.period))]
        accounts=[dict(r._mapping) for r in cx.execute(select(db.ec_flow_accounts))]
        cash=[tuple(r) for r in cx.execute(select(db.ec_flow_rows.c.account_id,db.ec_flow_rows.c.period,func.count(),func.max(db.ec_flow_rows.c.id)).group_by(db.ec_flow_rows.c.account_id,db.ec_flow_rows.c.period))]
        files=[tuple(r) for r in cx.execute(select(db.ec_flow_files.c.account_id,func.max(db.ec_flow_files.c.id)).group_by(db.ec_flow_files.c.account_id))]
        scopes={(r.period,r.shop) for r in cx.execute(select(order_store.STATES.c.period,order_store.STATES.c.shop))}
    scopes.update((r['period'],r['shop']) for r in imports)
    scopes.update((p,TARGET) for p,_,_ in legacy)
    return dict(imports=imports,legacy=legacy,wdt=wdt,accounts=accounts,cash=cash,files=files,scopes=scopes,
        shops={s['id']:s for s in shops()},rules=db.get_setting('ec_workbench_rules',{}) or {},
        recognition=db.get_setting('ec_income_recognition_rules',{}) or {},
        fee_categories=db.get_setting('ec_fee_display_categories',{}) or {},
        account_revisions={a['id']:db.get_setting('ec_order_account_revision:'+a['id'],None) for a in accounts},
        kd_meta=db.get_setting('ec_kd_cache_meta',{}) or {})


def input_version(period,shop,inputs=None):
    inputs=inputs or result_inputs()
    accounts=[a for a in inputs['accounts'] if shop in json.loads(a.get('shops') or '[]')]
    ids={a['id'] for a in accounts}
    try:
        stat=Path(ec._kd_cache_path(period)).stat();kd_file=(stat.st_mtime_ns,stat.st_size)
    except OSError:kd_file=None
    manifest={'model':'order-results-v2','fee_categories':inputs.get('fee_categories',{}),'shop':inputs['shops'].get(shop),
        'imports':[r for r in inputs['imports'] if r['period']==period and r['shop']==shop],
        'legacy':[r for r in inputs['legacy'] if r[0]==period] if shop==TARGET else [],
        'wdt':[r for r in inputs['wdt'] if r[0]==period] if shop==TARGET else [],
        'accounts':accounts,'cash':[r for r in inputs['cash'] if r[0] in ids and r[1]==period],
        'files':[r for r in inputs['files'] if r[0] in ids],
        'account_revisions':{a:inputs['account_revisions'].get(a) for a in sorted(ids)},
        'rule':inputs['rules'].get(period,{}).get(shop),'recognition':inputs['recognition'].get(shop,'shipment'),
        'kd_meta':inputs['kd_meta'].get(period),'kd_file':kd_file}
    # Stable ordering prevents database row order from looking like a source change.
    for k in ('imports','legacy','wdt','accounts','cash','files'):manifest[k]=sorted(manifest[k],key=order_store.pack)
    return order_store.fingerprint(manifest)


def build_result(period,shop):
    job=order_store.claim(db._engine,period,shop)
    if not job:return
    stop=threading.Event()
    def renew():
        while not stop.wait(30):
            try:
                if not order_store.heartbeat(db._engine,job):return
            except Exception:return  # CAS publication still prevents a superseded worker from publishing.
    thread=threading.Thread(target=renew,daemon=True);thread.start()
    try:
        data=compute_data(period,shop)
        current=input_version(period,shop)
        if current!=job['requested_version']:
            order_store.observe(db._engine,period,shop,current)
            raise order_store.Superseded()
        order_store.publish(db._engine,job,data)
    except order_store.Superseded:
        order_store.fail(db._engine,job,superseded=True)
    except Exception as exc:
        order_store.fail(db._engine,job,'结果生成失败：'+type(exc).__name__+'；旧版本保留，可重试')
    finally:stop.set();thread.join(timeout=1)


@router.on_event('startup')
def start_result_worker():
    global _result_thread
    if _result_thread and _result_thread.is_alive():return
    _result_stop.clear()
    def run():
        # ponytail: one build at a time per worker; DB leases prevent duplicate work across server processes.
        while not _result_stop.is_set():
            try:
                inputs=result_inputs()
                for period,shop in sorted(inputs['scopes'],reverse=True):
                    if _result_stop.is_set():break
                    if shop not in inputs['shops']:continue
                    version=input_version(period,shop,inputs)
                    order_store.observe(db._engine,period,shop,version)
                    build_result(period,shop)
            except Exception as exc:
                import logging
                logging.getLogger(__name__).error('Order result worker: %s',type(exc).__name__)
            _result_wake.wait(15);_result_wake.clear()
    _result_thread=threading.Thread(target=run,daemon=True);_result_thread.start()


@router.on_event('shutdown')
def stop_result_worker():
    _result_stop.set();_result_wake.set()


@router.post('/results/refresh')
async def refresh_result(request:Request):
    require(request,True);body=await request.json();period=body.get('period');shop=body.get('shop')
    check_period(period);check_shop(shop)
    order_store.observe(db._engine,period,shop,input_version(period,shop),force=True)
    _result_wake.set()
    return {'ok':True,'status':'pending'}


@router.get('/results/status')
def result_status(request:Request,period:str,shop:str):
    require(request);check_period(period);check_shop(shop)
    _,evidence,status=order_store.summary(db._engine,period,shop)
    return dict(ok=True,result=status,source_rows=evidence.get('source_rows',{}),provenance=evidence.get('provenance',{}))


def preparation_cards(period, shop):
    """Read saved source metadata; never build orders or decode account statements here."""
    kinds = preparation.requirements(db.get_setting('ec_preparation_rules', {}) or {}, shop)
    cards = {k:dict(kind=k,label=preparation.KINDS[k],available=False,state='missing',rows=None,
                    files=[],file_count=0,warnings=[],imported_at=None,accounts=[]) for k in kinds}
    with db._engine.connect() as cx:
        latest = select(func.max(TABLE.c.id)).where(TABLE.c.period==period,TABLE.c.shop==shop).group_by(TABLE.c.kind)
        for r in cx.execute(select(TABLE).where(TABLE.c.id.in_(latest))):
            if r.kind not in cards or r.kind in ('alipay','fund'):continue
            try:
                value=json.loads(gzip.decompress(r.payload)); names=json.loads(r.filenames or '[]')
                if not isinstance(value,dict) or not isinstance(names,list):raise ValueError('来源格式错误')
            except (OSError,ValueError,TypeError):
                cards[r.kind].update(state='warning',warnings=['已保存快照不可读，原始记录保留；请补充资料'])
                continue
            valid=isinstance(value.get('rows'),list) and bool(value['rows']) and value.get('status') in ('ready','warning')
            cards[r.kind].update(available=valid,state='ready' if valid else 'warning',rows=len(value.get('rows',[])),
                files=names,file_count=len(names),imported_at=r.ts,warnings=value.get('warnings',[]))
        # Original imports remain valid evidence, even before the new document index exists.
        if shop==TARGET:
            for kind, r in ec._tmall_latest_rows(period).items():
                if kind not in cards or cards[kind]['available'] or kind in ('alipay','fund'):continue
                summary=json.loads(r.summary or '{}')
                cards[kind].update(available=True,state='ready' if r.status=='ready' else 'warning',
                    rows=summary.get('rows'),legacy=True,file_count=1,imported_at=r.ts,
                    warnings=['原核算快照已保留'])
            if 'wdt' in cards and not cards['wdt']['available']:
                old=cx.execute(select(db.ec_wdt_import_runs).where(db.ec_wdt_import_runs.c.period==period).order_by(db.ec_wdt_import_runs.c.id.desc()).limit(1)).first()
                if old:cards['wdt'].update(available=True,state='warning',legacy=True,file_count=1,imported_at=old.ts,warnings=['原出库汇总已保留，逐单依据待核对'])
        # Historical documents and monthly imports are two provenance sources of the same material row.
        heads=documents.ec_document_heads; files=documents.ec_document_files
        for kind in kinds:
            if kind in ('alipay','fund','kingdee'):continue
            scope=(heads.c.shop==shop,heads.c.period==period,heads.c.kind==kind)
            n=cx.execute(select(func.count()).select_from(heads).where(*scope)).scalar_one()
            if not n:continue
            origins=list(cx.execute(select(files).where(files.c.id.in_(select(heads.c.file_id).where(*scope)))))
            conflicts=cx.execute(select(func.count()).select_from(heads).where(*scope,heads.c.conflict==1)).scalar_one()
            c=cards[kind]
            c.update(available=True,state='warning' if conflicts else 'ready',rows=max(n,c['rows'] or 0),
                files=list(dict.fromkeys(c['files']+[f.filename for f in origins])),
                imported_at=max([c['imported_at'] or '']+[f.ts for f in origins]))
            c['file_count']=len(c['files'])
            if conflicts:c['warnings'].append(f'{conflicts} 条单据存在版本冲突')
        accounts=[a for a in cx.execute(select(db.ec_flow_accounts)) if shop in json.loads(a.shops or '[]')]
        for kind in ('alipay','fund'):
            if kind not in cards:continue
            selected=[a for a in accounts if a.kind==kind]; ids=[a.id for a in selected]
            c=cards[kind]; c['accounts']=[{'id':a.id,'name':a.name} for a in selected]
            if not ids:
                c['warnings']=['请在基础资料登记并关联账户'];continue
            rows=db.ec_flow_rows; origins=db.ec_flow_origins; source_files=db.ec_flow_files
            totals=dict(cx.execute(select(rows.c.account_id,func.count()).where(rows.c.account_id.in_(ids),rows.c.period==period).group_by(rows.c.account_id)).all())
            imported=list(cx.execute(select(source_files).where(source_files.c.id.in_(select(origins.c.file_id).join(rows,rows.c.id==origins.c.flow_id).where(rows.c.account_id.in_(ids),rows.c.period==period)))))
            c.update(available=bool(totals),state='ready' if len(totals)==len(ids) else 'warning' if totals else 'missing',
                rows=sum(totals.values()),files=[f.filename for f in imported],file_count=len(imported),
                imported_at=max((f.ts for f in imported),default=None))
            if len(totals)<len(ids):c['warnings']=[f'{len(ids)-len(totals)} 个关联账户缺少本期流水']
    if 'kingdee' in cards:
        meta=ec._kd_cache_meta(period)
        if meta:
            try:
                cached=json.loads(Path(ec._kd_cache_path(period)).read_text(encoding='utf-8'))
                if not isinstance(cached,list) or any(not isinstance(r,dict) for r in cached):raise ValueError('缓存格式错误')
                selected=check_shop(shop)
                matched=[r for r in cached if any(str(r.get(f) or '')==selected['kd_name'] for f in ('客户','客户名称'))]
                scoped=not cached or any('客户' in r or '客户名称' in r for r in cached)
                cards['kingdee'].update(available=True,state='ready' if scoped else 'warning',rows=len(matched) if scoped else None,imported_at=meta.get('ts'),
                    warnings=['已读取本店应收缓存，匹配结果见收入确认'] if scoped else ['缓存缺少客户字段，店铺归属待核对'])
            except (OSError,ValueError):
                cards['kingdee']['warnings']=['金蝶缓存不可用，请重新只读同步']
    return list(cards.values())


@router.get('/shops')
def shops_view(request:Request,period:str=''):
    """Lightweight shop selector sourced only from controlled basic data."""
    require(request)
    if period:check_period(period)
    return {'ok':True,'shops':[dict(s,**preparation.progress(preparation_cards(period,s['id']))) if period else s for s in shops()]}


@router.get('/overview')
def overview(request:Request,period:str,business:str=''):
    require(request); check_period(period)
    result=[]; summaries=[]
    for shop in shops():
        metrics,evidence,status=order_store.summary(db._engine,period,shop['id'],business)
        cards=preparation_cards(period,shop['id']); ready=sum(c['available'] and c['state']=='ready' for c in cards)
        available=bool(evidence.get('has_order'))
        if available and metrics is not None:summaries.append(metrics)
        result.append(dict(shop,available=available,metrics=metrics if available else None,result=status,
            ready=ready,required=len(cards),files=sum(c['file_count'] for c in cards),
            readiness='ready' if cards and ready==len(cards) else 'linked'))
    return {'ok':True,'period':period,'shops':result,'totals':order_store.combine_metrics(summaries),
        'coverage':{'available':sum(s['available'] for s in result),'total':len(result)},
        'kingdee_read_only':True,'basis':'订单按创建月归属；GMV为该批订单支付成功金额，GSV扣除对应订单已成功退款。资金仅覆盖已导入流水期间。'}


@router.get('/sources')
def sources_view(request:Request,period:str,shop:str):
    require(request); check_period(period); selected=check_shop(shop)
    cards=preparation_cards(period,shop)
    return {'ok':True,'shop':selected,'sources':cards,'progress':preparation.progress(cards),
        'kingdee':{'refreshing':bool(ec._KD_REFRESH.get(period,{}).get('running'))},
        'collector_configured':bool(os.environ.get('EC_INBOX_ROOT')),
        'voucher_sync':_sync.get((period,shop),{}),'kingdee_read_only':True}


@router.get('/orders')
def orders_view(request:Request,period:str,shop:str,q:str='',business:str='',channel:str='',flag:str='',page:int=1,size:int=30,version:str='',start_date:str='',end_date:str='',shipment:str='',refund_state:str='',settlement:str=''):
    require(request); check_period(period); check_shop(shop)
    if len(q)>200 or len(version)>32:raise HTTPException(400,'查询条件过长')
    if shipment not in ('','shipped','unshipped','unknown') or refund_state not in ('','none','unknown','full_only','partial_only','full_return','partial_return','full_mixed','partial_mixed') or settlement not in ('','unknown','settled','partial','review'):
        raise HTTPException(400,'不支持的订单状态筛选')
    try:return order_store.page(db._engine,period,shop,q,business,channel,flag,page,size,version,start_date,end_date,shipment,refund_state,settlement)
    except ValueError as exc:raise HTTPException(409,str(exc))


@router.get('/order')
def order_view(request:Request,period:str,shop:str,order_no:str,version:str=''):
    require(request);check_period(period);check_shop(shop)
    row,status,evidence=order_store.detail(db._engine,period,shop,order_no,version)
    if not row: raise HTTPException(404,'本期本店未找到订单')
    return {'ok':True,'order':row,'result':status,'provenance':evidence.get('provenance',{}),
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
        aliases=(db.get_setting('ec_document_shop_aliases',{'星期零STARFIELD 天猫官旗店':['STARFIELD星期零旗舰店']}) or {}).get(shop,[])
        batches=[]
        for f,blob in zip(files,blobs):
            batch=await run_in_threadpool(documents.parse_documents,blob,Path((f.filename or '').replace('\\','/')).name[:180],shop,aliases)
            if {r['kind'] for r in batch['docs']}!={kind}:
                raise ValueError('文件实际列结构与所选资料行不一致，请在对应资料行导入；识别不使用文件名')
            batches.append(batch)
    except (ValueError,TypeError,KeyError) as exc:
        raise HTTPException(400,str(exc)[:200])
    digest=hashlib.sha256(''.join(sorted(hashlib.sha256(b).hexdigest() for b in blobs)).encode()).hexdigest()
    filenames=[Path(f.filename.replace('\\','/')).name[:180] for f in files]
    result=save_source(period,shop,kind,digest,filenames,parsed,user['name'])
    indexed=await run_in_threadpool(documents.store_documents,db._engine,shop,batches,user['name'])
    if indexed['affected']:
        await run_in_threadpool(documents.reconcile,db._engine,db.ec_flow_rows,db.ec_flow_accounts,indexed['affected'])
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
    _result_wake.set()
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
    metrics,_,status=order_store.summary(db._engine,period,shop,'ufirst')
    with db._engine.connect() as cx:
        packed=cx.execute(select(TABLE.c.payload).where(TABLE.c.period==period,TABLE.c.shop==shop,TABLE.c.kind=='kingdee_docs').order_by(TABLE.c.id.desc()).limit(1)).scalar()
    refs=json.loads(gzip.decompress(packed)).get('vouchers',[]) if packed else []
    return {'ok':True,'read_only':True,'existing_vouchers':refs,'ufirst':metrics,'result':status,
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
