# [Change Log] Date: 2026-10-19 | Author: Codex | Version: V2.994 | 总账凭证批量审核与选择部分反审
"""总账凭证审核。独立留痕、保守处理超时，绝不自动重试写请求。"""
import hashlib
import json
import uuid
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import MetaData, Table, Column, String, Text, Integer, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.mysql import LONGTEXT
import db
import busy
import kingdee_client as kd

md = MetaData()
large_text = Text().with_variant(LONGTEXT(), 'mysql')
owners = Table('vr_ownership', md,
    Column('voucher', String(40), primary_key=True),
    Column('fingerprint', String(64), nullable=False, default=''),
    Column('lock', String(40), nullable=False, default=''),
    Column('operator', String(100), nullable=False, default=''))
jobs = Table('vr_jobs', md, Column('id', String(40), primary_key=True),
    Column('operator', String(100), nullable=False), Column('payload', Text, nullable=False),
    Column('result', large_text, nullable=False), Column('status', String(24), nullable=False),
    Column('created', String(40), nullable=False))
cache = Table('vr_counts', md, Column('key', String(100), primary_key=True), Column('n', Integer))
summaries = Table('vr_summaries', md, Column('key', String(100), primary_key=True),
    Column('status', String(24)), Column('result', large_text))
md.create_all(db._engine)
def recover_interrupted():
    """仅在 FastAPI 启动时执行；禁止重放未完成的金蝶写请求。"""
    with db._engine.begin() as conn:
        conn.execute(summaries.update().where(summaries.c.status=='running').values(status='failed',result=json.dumps({'error':'计算被重启中断，请刷新后联系管理员清理该汇总缓存'})))
        unfinished=conn.execute(select(jobs).where(jobs.c.status.in_(['queued','running']))).mappings().all()
        for old in unfinished:
            done=json.loads(old['result']); completed={str(r['id']) for r in done}
            for item in json.loads(old['payload'])['items']:
                if str(item['id']) not in completed:
                    done.append({'id':str(item['id']),'status':'uncertain','message':'任务被重启中断，需核实金蝶状态；系统不会自动重放'})
            conn.execute(jobs.update().where(jobs.c.id==old['id']).values(status='done',result=json.dumps(done,ensure_ascii=False)))


pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='voucher-review')
summary_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='voucher-summary')


class Rejected(RuntimeError):
    """金蝶明确返回失败，和网络超时/未知回包区别处理。"""

FIELDS = {
 'id':'FVOUCHERID', 'book':'FAccountBookID.FNumber', 'subject':'FAccountBookID.FName',
 'date':'FDate','group':'FVOUCHERGROUPID.FName','number':'FVOUCHERGROUPNO',
 'creator':'FCreatorId.FName','checker':'FCHECKERID.FName','checker_id':'FCHECKERID',
 'status':'FDocumentStatus','debit':'FDEBITTOTAL','credit':'FCREDITTOTAL',
 'modified':'FModifyDate','audited':'FAuditDate','invalid':'FInvalid',
 'poster':'FPOSTERID','posted':'FPostDate','year':'FYEAR','period':'FPERIOD'}
ENTRY = {'seq':'FEntity_FEntrySeq','summary':'FEXPLANATION','account':'FACCOUNTID.FNumber',
 'account_name':'FACCOUNTID.FName','currency':'FCURRENCYID.FName','rate':'FEXCHANGERATE',
 'original':'FAMOUNTFOR','debit':'FDEBIT','credit':'FCREDIT'}


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


class Client:
    def __init__(self):
        self.s, self.conf = kd.login()

    def query(self, fields, where, order='FVOUCHERID', start=0, limit=1000):
        q = {'FormId':'GL_VOUCHER','FieldKeys':','.join(fields), 'FilterString':where,
             'OrderString':order,'TopRowCount':0,'StartRow':start,'Limit':limit}
        rows = kd._post(self.s, self.conf, kd.QUERY_SVC, [json.dumps(q, ensure_ascii=False)]).json()
        if not isinstance(rows, list) or (rows and (not isinstance(rows[0], list) or isinstance(rows[0][0], dict))):
            raise RuntimeError('金蝶查询失败：' + str(rows)[:350])
        return rows

    def headers(self, where):
        result, cursor = {}, 0
        while True:
            rows = self.query(FIELDS.values(), f'({where}) and FVOUCHERID>{cursor}', limit=1000)
            if not rows: break
            for values in rows:
                row = dict(zip(FIELDS, values)); row['id'] = str(int(row['id']))
                result[row['id']] = row
            nxt = max(int(r[0]) for r in rows)
            if nxt <= cursor: raise RuntimeError('金蝶分页游标未推进')
            cursor = nxt
            if len(rows) < 1000: break
        return list(result.values())

    def header(self, vid):
        rows = self.headers('FVOUCHERID=' + str(int(vid)))
        if len(rows) != 1: raise RuntimeError('凭证不存在或金蝶账号不可见')
        return rows[0]

    def count(self, row):
        key = row['id'] + ':' + fingerprint(row)
        with db._engine.connect() as c:
            old = c.execute(select(cache.c.n).where(cache.c.key == key)).scalar_one_or_none()
        if old is not None: return old
        where = 'FVOUCHERID=' + row['id']
        last = self.query(['FEntity_FEntrySeq'], where, 'FEntity_FEntrySeq DESC', limit=1)
        hi = int(last[0][0]) if last else 0
        lo = 0
        if hi and self.query(['FEntity_FEntrySeq'], where, 'FEntity_FEntrySeq', hi-1, 1):
            lo = hi
        # offset n-1 存在，才证明行数至少 n；有删行间隙时二分求真实行数。
        while lo < hi:
            mid = (lo + hi + 1)//2
            exists = self.query(['FEntity_FEntrySeq'], where, 'FEntity_FEntrySeq', mid-1, 1)
            if exists: lo = mid
            else: hi = mid-1
        try:
            with db._engine.begin() as c: c.execute(cache.insert().values(key=key, n=lo))
        except IntegrityError: pass
        return lo

    def mutate(self, action, vid):
        svc = 'Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.' + action + '.common.kdsvc'
        response = kd._post(self.s, self.conf, svc, ['GL_VOUCHER', json.dumps({'Ids':str(int(vid))})]).json()
        status = response.get('Result', {}).get('ResponseStatus', {}) if isinstance(response, dict) else {}
        if status.get('IsSuccess') is False:
            try: kd._save_result(response)
            except kd.KingdeeError as e: raise Rejected(str(e)) from e
        return kd._save_result(response)


def fingerprint(row):
    # 包含当前审核周期、修改时间、记账状态和金额，外部重审后旧授权自然失效。
    return hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def ownership(row):
    with db._engine.connect() as c:
        own = c.execute(select(owners).where(owners.c.voucher == row['id'])).mappings().first()
    return bool(own and not own['lock'] and own['fingerprint'] == fingerprint(row) and row['status']=='C')


def claim(vid, token, operator):
    try:
        with db._engine.begin() as c:
            c.execute(owners.insert().values(voucher=vid, fingerprint='', lock='', operator=''))
    except IntegrityError: pass
    with db._engine.begin() as c:
        r = c.execute(update(owners).where(owners.c.voucher==vid, owners.c.lock=='').values(lock=token, operator=operator))
        if r.rowcount != 1: raise RuntimeError('凭证已有任务或待核实的操作，禁止重复执行')
        return c.execute(select(owners.c.fingerprint).where(owners.c.voucher==vid)).scalar_one()


def save_job(jid, rows, status):
    with db._engine.begin() as c:
        c.execute(jobs.update().where(jobs.c.id==jid).values(result=json.dumps(rows, ensure_ascii=False),status=status))


def run_job(jid, operator, payload):
    results = []
    client = None
    try:
        with busy.hold('总账凭证批量' + payload['action']):
            client = Client()
            for item in payload['items']:
                vid = str(int(item['id'])); locked = False; sent = False
                out = {'id':vid,'status':'failed','message':''}
                try:
                    own = claim(vid,jid,operator); locked=True
                    before = client.header(vid)
                    out['before']=before
                    if fingerprint(before) != item['fingerprint']: raise RuntimeError('凭证已变化，请刷新后重选')
                    if str(before['invalid']) not in ('0','False','false','', 'None'): raise RuntimeError('作废凭证不可操作')
                    if str(before['poster']) not in ('-1','0','', 'None') or before['posted']: raise RuntimeError('已记账凭证不可操作，请在金蝶处理')
                    if payload['action']=='unaudit':
                        if before['status']!='C' or own!=fingerprint(before): raise RuntimeError('本次审核不属于工作台，不能反审')
                        sent=True
                        out['stage']='UnAudit'
                        client.mutate('UnAudit',vid)
                        after=client.header(vid)
                        if after['status'] not in ('A','B','D','Z'): raise RuntimeError('反审后状态未确认')
                        newfp=''
                    else:
                        if before['status'] not in ('A','B'): raise RuntimeError('仅创建/审核中的凭证可审核')
                        if before['status']=='A':
                            sent=True
                            out['stage']='Submit'
                            client.mutate('Submit',vid)
                            submitted=client.header(vid)
                            out['after_submit']=submitted
                            if submitted['status']!='B': raise RuntimeError('提交后状态不是审核中，已停止')
                        sent=True
                        out['stage']='Audit'
                        client.mutate('Audit',vid)
                        after=client.header(vid)
                        if after['status']!='C' or not after['audited'] or str(after['checker_id']) in ('0','','None'):
                            raise RuntimeError('审核结果未确认')
                        newfp=fingerprint(after)
                    # 成功记录与释放锁在同一事务。只有已确认成功的当前审核周期拥有反审权。
                    with db._engine.begin() as c:
                        c.execute(owners.update().where(owners.c.voucher==vid,owners.c.lock==jid).values(fingerprint=newfp,lock=''))
                    locked=False
                    out.update(status='success', message='已审核' if payload['action']=='audit' else '已反审', before=before, after=after)
                except Exception as exc:
                    if isinstance(exc, Rejected): sent=False
                    out['message']=str(exc)[:700]
                    if sent:
                        # 写请求已发出后的任何异常都保守冻结，绝不根据 C 状态补认所有权。
                        out.update(status='uncertain',message='需在金蝶核实，禁止自动重试：'+out['message'])
                    elif locked:
                        with db._engine.begin() as c:
                            c.execute(owners.update().where(owners.c.voucher==vid,owners.c.lock==jid).values(lock=''))
                results.append(out)
                save_job(jid,results,'running')
                db.audit(operator,'voucher_'+payload['action'],vid,json.dumps(out,ensure_ascii=False))
    except Exception as exc:
        completed={r['id'] for r in results}
        results.extend({'id':str(i['id']),'status':'failed','message':str(exc)[:700]} for i in payload['items'] if str(i['id']) not in completed)
    save_job(jid,results,'done')


def enqueue(operator, payload):
    jid=payload['request_id']
    if str(uuid.UUID(jid)) != jid: raise ValueError("请求标识格式无效")
    if payload['action'] not in ('audit','unaudit'): raise ValueError('操作无效')
    items=payload['items']
    if not 1<=len(items)<=100 or len({str(int(x['id'])) for x in items})!=len(items): raise ValueError('请选择 1—100 张不同凭证')
    canonical=json.dumps(payload,sort_keys=True,ensure_ascii=False)
    try:
        with db._engine.begin() as c:
            c.execute(jobs.insert().values(id=jid,operator=operator,payload=canonical,result='[]',status='queued',created=datetime.now().isoformat()))
    except IntegrityError:
        with db._engine.connect() as c: old=c.execute(select(jobs).where(jobs.c.id==jid)).mappings().one()
        if old['operator']!=operator or old['payload']!=canonical: raise ValueError('请求标识已被使用')
        return jid
    pool.submit(run_job,jid,operator,payload)
    return jid


def aggregate(key, row):
    from decimal import Decimal
    try:
        c=Client(); accounts=set(); currencies=set(); rates=set(); amounts={}; cursor=0
        while True:
            data=c.query(['FEntity_FEntrySeq','FACCOUNTID.FNumber','FCURRENCYID.FName','FEXCHANGERATE','FAMOUNTFOR','FDEBIT','FCREDIT'],
                f"FVOUCHERID={row['id']} and FEntity_FEntrySeq>{cursor}", 'FEntity_FEntrySeq',limit=10000)
            if not data: break
            for seq,account,currency,rate,amount,debit,credit in data:
                accounts.add(str(account)); currencies.add(str(currency)); rates.add(str(rate))
                for side,value in (('借',debit),('贷',credit)):
                    if Decimal(str(value or 0)) != 0:
                        k=(str(currency),side)
                        amounts[k]=amounts.get(k,Decimal(0))+Decimal(str(amount or 0))
            cursor=max(int(r[0]) for r in data)
            if len(data)<10000: break
        if fingerprint(c.header(row['id']))!=fingerprint(row): raise RuntimeError('凭证计算中发生变化，请刷新')
        result={'accounts':', '.join(sorted(accounts)), 'currencies':', '.join(sorted(currencies)),
            'rates':', '.join(sorted(rates)), 'original':' / '.join(' '.join(k)+' '+format(v,',.2f') for k,v in sorted(amounts.items()))}
        status='done'
    except Exception as e: result={'error':str(e)[:300]}; status='failed'
    with db._engine.begin() as conn:
        conn.execute(summaries.update().where(summaries.c.key==key).values(status=status,result=json.dumps(result,ensure_ascii=False)))
