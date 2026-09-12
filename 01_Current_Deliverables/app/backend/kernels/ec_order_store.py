"""Persistent order read model. Paging never parses sources or computes accounting results."""
import gzip
import hashlib
import json
import time
import uuid
from datetime import datetime
from sqlalchemy import Table, Column, MetaData, String, Integer, Text, LargeBinary, Index, select, insert, update, func, or_
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.exc import IntegrityError
from kernels import ec_workbench as model

md=MetaData()
STATES=Table('ec_order_result_states',md,
    Column('scope',String(64),primary_key=True),Column('period',String(7)),Column('shop',String(120)),
    Column('requested_version',String(64)),Column('active_build',String(32)),Column('status',String(20)),
    Column('owner',String(32)),Column('lease_until',Integer,default=0),Column('error',String(200)),
    Index('ix_ec_order_state_period','period','shop'))
BUILDS=Table('ec_order_result_builds',md,
    Column('id',String(32),primary_key=True),Column('scope',String(64),nullable=False),Column('input_version',String(64)),
    Column('built_at',String(20)),Column('row_count',Integer),Column('metrics',Text().with_variant(LONGTEXT(),'mysql')),
    Column('evidence',Text().with_variant(LONGTEXT(),'mysql')),Index('ix_ec_order_build_scope','scope','built_at'))
FLAGS=('manual','unshipped','refund_only','return_refund','unknown_refund','refund','fees','ar_missing')
ROWS=Table('ec_order_result_rows',md,
    Column('build_id',String(32),primary_key=True),Column('order_no',String(64),primary_key=True),
    Column('created_at',String(32)),Column('business',String(20)),Column('channel',String(20)),
    *[Column('f_'+flag,Integer,nullable=False) for flag in FLAGS],
    Column('search_text',Text().with_variant(LONGTEXT(),'mysql')),
    Column('listing',Text().with_variant(LONGTEXT(),'mysql')),Column('detail',LargeBinary(2**32-1)),
    Index('ix_ec_order_page','build_id','created_at','order_no'),
    Index('ix_ec_order_business','build_id','business','created_at'),
    Index('ix_ec_order_channel','build_id','channel','created_at'))
TABLES=tuple(md.tables.values())
BUSINESSES=('', 'normal','ufirst','mixed','review','unknown')
HIDDEN={'events','fee_events','items','shipments','refunds','order_key','id','run_id'}


def pack(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'),sort_keys=True)
def fingerprint(value):return hashlib.sha256(pack(value).encode()).hexdigest()
def scope(period,shop):return fingerprint([period,shop])


def state(engine,period,shop):
    with engine.connect() as cx:
        row=cx.execute(select(STATES).where(STATES.c.scope==scope(period,shop))).mappings().first()
        return dict(row) if row else None


def observe(engine,period,shop,version,force=False):
    key=scope(period,shop)
    try:
        with engine.begin() as cx:
            cx.execute(insert(STATES).values(scope=key,period=period,shop=shop,requested_version=version,
                active_build='',status='pending',owner='',lease_until=0,error=''))
        return
    except IntegrityError:pass
    with engine.begin() as cx:
        condition=STATES.c.scope==key
        if not force:condition=condition & (STATES.c.requested_version!=version)
        cx.execute(update(STATES).where(condition).values(requested_version=version,status='pending',error=''))


def claim(engine,period,shop):
    key=scope(period,shop);now=int(time.time());owner=uuid.uuid4().hex
    with engine.begin() as cx:
        changed=cx.execute(update(STATES).where(STATES.c.scope==key,STATES.c.lease_until<now,
            or_(STATES.c.status=='pending',STATES.c.status=='building')).values(owner=owner,lease_until=now+120,status='building',error=''))
        if changed.rowcount!=1:return None
        row=cx.execute(select(STATES).where(STATES.c.scope==key)).mappings().one()
        return dict(row)


def heartbeat(engine,job):
    with engine.begin() as cx:
        return cx.execute(update(STATES).where(STATES.c.scope==job['scope'],STATES.c.owner==job['owner'])
            .values(lease_until=int(time.time())+120)).rowcount==1


def fail(engine,job,error='',superseded=False):
    with engine.begin() as cx:
        current=cx.execute(select(STATES).where(STATES.c.scope==job['scope'])).mappings().first()
        if not current or current['owner']!=job['owner']:return
        pending=superseded or current['requested_version']!=job['requested_version']
        cx.execute(update(STATES).where(STATES.c.scope==job['scope'],STATES.c.owner==job['owner'])
            .values(status='pending' if pending else 'error',owner='',lease_until=0,error='' if pending else error[:200]))


class Superseded(Exception):pass


def publish(engine,job,data):
    """Atomically publish a complete generation; failed/superseded writes roll back."""
    build=uuid.uuid4().hex;rows=data['rows'];cash=data['cash_available']
    metrics={b:model.metrics([r for r in rows if not b or r['business_type']==b],cash) for b in BUSINESSES}
    evidence={k:data[k] for k in ('provenance','kingdee','rule','cash_available')}
    evidence.update(has_order='order' in data['sources'],source_rows={k:len(v.get('rows',[])) for k,v in data['sources'].items()})
    prepared=[]
    for r in rows:
        dest=r['destination'];channel='multiple' if '、' in dest else 'alipay' if '支付宝' in dest else 'fund' if '聚合账户' in dest else 'pending'
        flags={f:bool(r[f]) for f in ('manual','unshipped')}
        flags.update({f:r[f]>0 for f in ('refund_only','return_refund','unknown_refund','refund')})
        flags.update(fees=r['fees'] not in (None,0),ar_missing=bool(r['should_ar'] and not r['ar_documents']))
        listing={k:v for k,v in r.items() if k not in HIDDEN}
        prepared.append(dict(build_id=build,order_no=r['order_no'],created_at=r.get('created_at') or '',
            business=r['business_type'],channel=channel,**{'f_'+k:int(v) for k,v in flags.items()},
            search_text='\n'.join([r['order_no']]+[str(i.get(k) or '') for i in r['items'] for k in ('sku','name')]).casefold(),
            listing=pack(listing),detail=gzip.compress(pack(r).encode())))
    with engine.begin() as cx:
        cx.execute(insert(BUILDS).values(id=build,scope=job['scope'],input_version=job['requested_version'],
            built_at=datetime.now().isoformat(' ',timespec='seconds'),row_count=len(rows),metrics=pack(metrics),evidence=pack(evidence)))
        for i in range(0,len(prepared),200):cx.execute(insert(ROWS),prepared[i:i+200])
        changed=cx.execute(update(STATES).where(STATES.c.scope==job['scope'],STATES.c.owner==job['owner'],
            STATES.c.requested_version==job['requested_version']).values(active_build=build,status='ready',owner='',lease_until=0,error=''))
        if changed.rowcount!=1:raise Superseded()
    # ponytail: retain old generations so in-flight pagination stays consistent; add explicit retention when storage requires it.
    return build


def load_build(cx,current,version=''):
    if not current:return None
    selected=version or current.get('active_build')
    if not selected:return None
    value=cx.execute(select(BUILDS).where(BUILDS.c.id==selected,BUILDS.c.scope==current['scope'])).mappings().first()
    return dict(value) if value else None


def public_status(current,build):
    return {'status':current['status'] if current else 'missing','error':current.get('error','') if current else '',
        'build_id':build['id'] if build else '', 'built_at':build['built_at'] if build else None,
        'input_version':build['input_version'] if build else '', 'stored_rows':build['row_count'] if build else None,
        'stale':bool(build and current and (current['requested_version']!=build['input_version'] or current['active_build']!=build['id']))}


def page(engine,period,shop,q='',business='',channel='',flag='',page=1,size=30,version=''):
    current=state(engine,period,shop)
    with engine.connect() as cx:
        build=load_build(cx,current,version)
        status=public_status(current,build)
        if not build:
            if version:raise ValueError('核算结果版本不属于本店本期或不存在，请刷新')
            return dict(ok=True,rows=[],total=None,page=1,pages=None,result=status)
        conditions=[ROWS.c.build_id==build['id']]
        if business:conditions.append(ROWS.c.business==business)
        if channel:conditions.append(ROWS.c.channel.in_([channel,'multiple']) if channel in ('alipay','fund') else ROWS.c.channel==channel)
        if flag in FLAGS:conditions.append(ROWS.c['f_'+flag]==1)
        if q.strip():conditions.append(ROWS.c.search_text.contains(q.strip().casefold(),autoescape=True))
        total=cx.execute(select(func.count()).select_from(ROWS).where(*conditions)).scalar_one()
        size=min(100,max(10,size));pages=max(1,(total+size-1)//size);page=min(pages,max(1,page))
        selected=cx.execute(select(ROWS.c.listing).where(*conditions).order_by(ROWS.c.created_at.desc(),ROWS.c.order_no.desc())
            .offset((page-1)*size).limit(size)).scalars()
        return dict(ok=True,rows=[json.loads(r) for r in selected],total=total,page=page,pages=pages,result=status)


def detail(engine,period,shop,order_no,version=''):
    current=state(engine,period,shop)
    with engine.connect() as cx:
        build=load_build(cx,current,version)
        if not build:return None,public_status(current,None),{}
        packed=cx.execute(select(ROWS.c.detail).where(ROWS.c.build_id==build['id'],ROWS.c.order_no==order_no)).scalar()
        return json.loads(gzip.decompress(packed)) if packed else None,public_status(current,build),json.loads(build['evidence'])


def summary(engine,period,shop,business=''):
    current=state(engine,period,shop)
    with engine.connect() as cx:
        build=load_build(cx,current)
        return (json.loads(build['metrics']).get(business),json.loads(build['evidence']),public_status(current,build)) if build else (None,{},public_status(current,None))


def combine_metrics(values):
    if not values:return None
    keys=set(values[0])-{'refund_only_rate','return_refund_rate','unknown_refund_rate'}
    out={k:None if any(v.get(k) is None for v in values) else model.total(v[k] for v in values) for k in keys}
    for k in ('refund_only','return_refund','unknown_refund'):out[k+'_rate']=out[k]/out['gmv'] if out['gmv'] else None
    return out
