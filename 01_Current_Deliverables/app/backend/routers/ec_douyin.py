# V2.811: 电商工作台·抖音月结。两份动账明细入库（复用资料快照表）→ 与金蝶应收逐单对 → 收款单草稿。金蝶只读。
import datetime
import gzip
import hashlib
import json
import os
import tempfile
import threading
import time
from typing import List
from urllib.parse import quote
from fastapi import APIRouter, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import Response
from sqlalchemy import select, func
import kingdee_client as kc
from core import db
from routers import ec, ec_workbench as wbr
from kernels import ec_douyin as model, ec_open_items as open_items

router = APIRouter(prefix='/api/ec/douyin')
ORG = '深圳市星期零食品科技有限公司'
LOOKBACK = 3                               # 月结往前看几个月的流水与应收：上月发货本月结算是常态
META_KEY = 'ec_douyin_ar_meta'
TABLE = wbr.TABLE
_lock = threading.Lock()
_jobs = {}                                 # (period, shop) -> {'running', 'error'}
_cache = {}                                # (period, shop) -> (版本键, 对账结果)
require = wbr.require


def check(period, shop):
    wbr.check_period(period)
    selected = wbr.check_shop(shop)
    if selected['platform'] != '抖音': raise HTTPException(400, '这家店不是抖音店铺')
    return selected


def _periods(period):
    first = datetime.date(int(period[:4]), int(period[5:7]), 1)
    return [open_items.month_shift(first, -i)[:7] for i in range(LOOKBACK, -1, -1)]


def _latest(cx, shop, periods, kinds):
    latest = (select(func.max(TABLE.c.id)).where(TABLE.c.shop == shop, TABLE.c.kind.in_(kinds), TABLE.c.period.in_(periods))
              .group_by(TABLE.c.period, TABLE.c.kind))
    return cx.execute(select(TABLE.c.id, TABLE.c.period, TABLE.c.kind, TABLE.c.filenames, TABLE.c.payload).where(TABLE.c.id.in_(latest))).fetchall()


def _sources(period, shop):
    """截至本期的两份流水（含前几个月）。返回 (版本键, {kind: rows})。"""
    with db._engine.connect() as cx:
        records = _latest(cx, shop, _periods(period), list(model.KINDS))
    rows = {k: [] for k in model.KINDS}
    for r in sorted(records, key=lambda r: r.period):
        rows[r.kind] += json.loads(gzip.decompress(r.payload))['rows']
    return tuple(sorted(r.id for r in records)), rows


@router.post('/upload')
async def upload(request: Request, shop: str = Form(...), files: List[UploadFile] = File(...)):
    """上传抖音动账明细 / 账户流水（csv 或 zip）。只看表头认类型；文件里有几个月就按月各存一份，重复流水号只留一份。"""
    user = require(request, write=True)
    selected = wbr.check_shop(shop)
    if selected['platform'] != '抖音': raise HTTPException(400, '这家店不是抖音店铺')
    results = []
    for f in files[:10]:
        blob = await f.read()
        try: parts = model.parse(blob, f.filename or '未命名')
        except (ValueError, OSError, KeyError) as e:
            results.append({'name': f.filename, 'ok': False, 'error': str(e)[:120]}); continue
        for part in parts:
            if not part['kind']:
                results.append({'name': part['name'], 'ok': False, 'error': part['skip']}); continue
            months, added = model.by_month(part['rows']), 0
            with db._engine.connect() as cx:
                old = {r.period: r for r in _latest(cx, shop, list(months), [part['kind']])}
            for month, new in sorted(months.items()):
                before = json.loads(gzip.decompress(old[month].payload))['rows'] if month in old else []
                names = json.loads(old[month].filenames or '[]') if month in old else []
                merged = model.merge(before, new)
                added += len(merged) - len(before)
                digest = hashlib.sha256(json.dumps([[r['id'], r['amt']] for r in merged]).encode()).hexdigest()
                wbr.save_source(month, shop, part['kind'], digest, list(dict.fromkeys(names + [part['name']])),
                                {'rows': merged, 'status': 'ready'}, user['name'])
            results.append({'name': part['name'], 'ok': True, 'kind': part['kind'], 'label': model.KINDS[part['kind']],
                            'rows': len(part['rows']), 'duplicate': added == 0,
                            'warnings': ['%s %d 行' % (m, len(v)) for m, v in sorted(months.items())]})
    db.audit(user['name'], 'ec_douyin_upload', target=shop, detail='；'.join('%s:%s' % (r['name'], r.get('label') or r.get('error')) for r in results)[:300])
    with _lock: _cache.clear()
    return {'ok': True, 'results': results}


def _ar_path(period, shop):
    return os.path.join(ec.EC_UPLOAD_DIR, period, '抖音应收_%s.json.gz' % hashlib.sha256(shop.encode()).hexdigest()[:10])


def _ar_meta(period, shop):
    meta = (db.get_setting(META_KEY, {}) or {}).get('%s|%s' % (period, shop))
    return meta if meta and os.path.isfile(_ar_path(period, shop)) else None


def _book(s, conf, customer, period):
    """金蝶账面：收款账户照这家店最近一张收款单用的那个；取本期其他货币资金在该账户上的期初 / 借 / 贷 / 期末。"""
    rows, err = kc._query_raw(s, conf, 'AR_RECEIVEBILL', 'FBillNo,FDATE,FACCOUNTID.FNumber',
                              "FCONTACTUNIT.FName='%s' and FCancelStatus='A' and FDATE>='%s'" % (customer, open_items.month_shift(datetime.date(int(period[:4]), int(period[5:7]), 1), -12)), 0)
    if err: return {'error': '读收款单失败'}
    used = sorted((r for r in rows if r[2]), key=lambda r: str(r[1]), reverse=True)
    if not used: return {'error': '这家店近一年没有带收款账户的收款单，认不出账户'}
    account = used[0][2]
    for r in kc.fetch_gl_balance(int(period[:4]), int(period[5:7]), prefixes=('1012',), s=s, conf=conf):
        if str(r.get('核算维度.银行账号.编码') or '') == account and str(r.get('账簿') or '') == ORG:
            num = lambda k: round(float(r.get(k) or 0), 2)
            return {'account': account, 'from_receipt': used[0][0], 'open': num('期初原币'), 'debit': num('本期借方原币'),
                    'credit': num('本期贷方原币'), 'close': num('期末原币')}
    return {'account': account, 'from_receipt': used[0][0], 'error': '本期科目余额表里还没有这个账户'}


def _sync(period, shop, customer, operator):
    started = time.time()
    s, conf = kc.login()
    since = open_items.month_shift(datetime.date(int(period[:4]), int(period[5:7]), 1), -LOOKBACK)
    raw = kc._query(s, conf, 'AR_receivable', open_items.AR_FIELDS,
                    "FSETTLEORGID.FName='%s' and FCUSTOMERID.FName='%s' and FCancelStatus='A' and FDATE>='%s'" % (ORG, customer.replace("'", ''), since),
                    order='FBillNo ASC')
    bills = open_items._merge(raw, lambda r: dict(order=open_items.order_key(r.get('t6'), r.get('t4'))))
    try: book = _book(s, conf, customer.replace("'", ''), period)
    except Exception as e: book = {'error': '读金蝶账面余额失败（%s）' % type(e).__name__}
    path = _ar_path(period, shop); os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=os.path.dirname(path), prefix='.douyin-ar-', suffix='.tmp', delete=False) as f:
            temp = f.name
            with gzip.open(f, 'wt', encoding='utf-8') as stream:
                json.dump({'bills': bills, 'book': book}, stream, ensure_ascii=False, separators=(',', ':'))
        os.replace(temp, path)
    finally:
        if temp and os.path.exists(temp): os.remove(temp)
    meta = db.get_setting(META_KEY, {}) or {}
    meta['%s|%s' % (period, shop)] = {'ts': ec._now(), 'operator': operator, 'bills': len(bills), 'since': since,
                                      'customer': customer, 'seconds': round(time.time() - started, 1)}
    db.set_setting(META_KEY, meta, operator=operator)
    db.audit(operator, 'ec_douyin_ar_refresh', target='%s %s' % (period, shop), detail='应收 %d 张' % len(bills))


@router.post('/ar-refresh')
def ar_refresh(request: Request, period: str = Form(...), shop: str = Form(...)):
    """只读同步这家店的金蝶应收（全部核销状态）和账面余额。后台线程跑，页面轮询。"""
    user = require(request, write=True); selected = check(period, shop)
    key = (period, shop)
    with _lock:
        if _jobs.get(key, {}).get('running'): raise HTTPException(409, '正在同步中，请稍候')
        _jobs[key] = {'running': True, 'error': ''}

    def job():
        try: _sync(period, shop, selected['kd_name'], user['name'])
        except Exception as e: _jobs[key]['error'] = '金蝶同步失败（%s），请检查金蝶连接后重试' % type(e).__name__
        finally:
            _jobs[key]['running'] = False
            with _lock: _cache.pop(key, None)
    threading.Thread(target=job, daemon=True).start()
    return {'ok': True}


def _result(period, shop):
    """对账结果（按资料版本 + 应收同步时间缓存）。没同步过应收返回 (None, None, 资料行数)。"""
    version, rows = _sources(period, shop)
    counts = {k: sum(1 for r in v if r['t'][:7] == period) for k, v in rows.items()}
    meta = _ar_meta(period, shop)
    if not meta: return None, None, counts
    key = (period, shop); stamp = (version, meta['ts'])
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] == stamp: return hit[1], hit[2], counts
    with gzip.open(_ar_path(period, shop), 'rt', encoding='utf-8') as stream:
        saved = json.load(stream)
    result = model.reconcile(period, rows['dy_settle'], rows['dy_ledger'], saved['bills'])
    with _lock: _cache[key] = (stamp, result, saved.get('book') or {})
    return result, saved.get('book') or {}, counts


@router.get('/settle')
def settle(request: Request, period: str, shop: str):
    require(request); selected = check(period, shop)
    result, book, counts = _result(period, shop)
    job = _jobs.get((period, shop), {})
    out = {'ok': True, 'shop': selected, 'period': period, 'sources': counts, 'ar': _ar_meta(period, shop),
           'refreshing': bool(job.get('running')), 'error': job.get('error', ''), 'result': None}
    if result:
        days, draft = result['overdue_days'], result['draft']
        out['result'] = {
            'categories': [dict(key=k, label=model.category_label(k, days), **v) for k, v in result['categories'].items()],
            'buckets': [dict(key=k, label=model.BUCKETS[k], **v) for k, v in result['buckets'].items()],
            'draft': dict(draft, deductions=[dict(d, memo=model.memo(period, d['name'], d['amount'])) for d in draft['deductions']]),
            'balance': result['balance'], 'book': book, 'coverage': result['coverage'], 'end': result['end'], 'missing': len(result['missing'])}
    return out


def _need(period, shop):
    result, book, _ = _result(period, shop)
    if not result: raise HTTPException(404, '还没有同步这家店的金蝶应收')
    return result, book


@router.get('/bills')
def bills(request: Request, period: str, shop: str, cat: str = '', q: str = '', page: int = 1):
    require(request); check(period, shop)
    result, _ = _need(period, shop)
    if cat and cat not in model.CATEGORIES: raise HTTPException(400, '分类不对')
    return dict(model.page(model.select(result, cat, q), page), ok=True)


@router.get('/missing')
def missing(request: Request, period: str, shop: str):
    require(request); check(period, shop)
    result, _ = _need(period, shop)
    return {'ok': True, 'rows': [dict(m, label=model.BUCKETS[m['bucket']]) for m in result['missing'][:500]], 'total': len(result['missing'])}


@router.get('/export')
def export(request: Request, period: str, shop: str):
    user = require(request); selected = check(period, shop)
    result, book = _need(period, shop)
    db.audit(user['name'], 'ec_douyin_export', target='%s %s' % (period, shop))
    name = '抖音月结_%s_%s.xlsx' % (selected['name'], period)
    return Response(model.export(result, selected['name'], book),
                    media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': "attachment; filename*=UTF-8''%s" % quote(name)})
