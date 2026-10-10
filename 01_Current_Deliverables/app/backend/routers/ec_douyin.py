# V2.811: 电商工作台·抖音月结。两份动账明细入库（复用资料快照表）→ 与金蝶应收逐单对 → 收款单草稿。金蝶只读。
import csv
import datetime
import gzip
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
import zipfile
import zlib
from typing import List
from urllib.parse import quote
from fastapi import APIRouter, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import Response
from sqlalchemy import select, func
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
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
_cache = {}                                # (period, shop) -> (版本键, 对账结果, 账面, 四类资料行, 应收)
_building = {}                             # (period, shop) -> 锁：同时进来的请求等第一个算完，别各算一遍
_bill_cache = {}                           # (period, shop, 应收单号…) -> (时刻, 分录)：同一张单反复打开不重复查金蝶
CACHE_KEEP = 3                             # 对账结果最多留几个「期间 × 店」：每份连四类资料行一起有几十 MB
MAX_UPLOAD = 60 << 20                      # 单个上传文件的上限
_upload_lock = threading.Lock()            # 上传放到线程池里跑：读旧快照 → 合并 → 存 这一段要排队，免得两个人互相盖掉
log = logging.getLogger(__name__)
require = wbr.require


def check(period, shop):
    wbr.check_period(period)
    selected = wbr.check_shop(shop)
    if selected['platform'] != '抖音': raise HTTPException(400, '这家店不是抖音店铺')
    return selected


def _periods(period):
    first = datetime.date(int(period[:4]), int(period[5:7]), 1)
    return [open_items.month_shift(first, -i)[:7] for i in range(LOOKBACK, -1, -1)]


def _latest(cx, shop, periods, kinds, payload=True):
    latest = (select(func.max(TABLE.c.id)).where(TABLE.c.shop == shop, TABLE.c.kind.in_(kinds), TABLE.c.period.in_(periods))
              .group_by(TABLE.c.period, TABLE.c.kind))
    last = TABLE.c.payload if payload else TABLE.c.summary             # 不要内容时只取摘要（里面有行数），不把几 MB 的快照搬出来
    return cx.execute(select(TABLE.c.id, TABLE.c.period, TABLE.c.kind, TABLE.c.filenames, last).where(TABLE.c.id.in_(latest))).fetchall()


def _version(period, shop):
    """现用的是哪几份快照、本期各类资料多少行——只查编号和摘要，不解压内容。返回 (版本键, {kind: 本期行数})。"""
    with db._engine.connect() as cx:
        heads = _latest(cx, shop, _periods(period), list(model.KINDS), payload=False)
    counts = {k: 0 for k in model.KINDS}
    for r in heads:
        if r.period == period:
            try: counts[r.kind] = int(json.loads(r.summary or '{}').get('rows') or 0)
            except (ValueError, TypeError): pass
    return tuple(sorted(r.id for r in heads)), counts


def _sources(period, shop):
    """截至本期的四类资料（含前几个月），整份解开。返回 (版本键, {kind: rows})。慢，只在缓存对不上时才调。"""
    with db._engine.connect() as cx:
        records = _latest(cx, shop, _periods(period), list(model.KINDS))
    rows = {k: [] for k in model.KINDS}
    for r in sorted(records, key=lambda r: r.period):
        rows[r.kind] += json.loads(gzip.decompress(r.payload))['rows']
    return tuple(sorted(r.id for r in records)), rows


@router.post('/upload')
async def upload(request: Request, shop: str = Form(...), files: List[UploadFile] = File(...)):
    """上传抖音动账明细 / 账户流水 / 抖店订单导出（csv 或 zip）/ 旺店通订单明细（xlsx）。只看表头认类型；文件里有几个月就按月各存一份，重复的只留一份。"""
    user = require(request, write=True)
    selected = wbr.check_shop(shop)
    if selected['platform'] != '抖音': raise HTTPException(400, '这家店不是抖音店铺')
    results = []
    try:
        for f in files[:10]:
            name = f.filename or '未命名'
            blob = await f.read(MAX_UPLOAD + 1)
            if len(blob) > MAX_UPLOAD:
                results.append({'name': name, 'ok': False, 'error': '超过 60 MB，没有入库'}); continue
            # 解析、合并、压缩、入库要好几秒：放到线程池里，别让整个工作台这几秒不响应
            results += await run_in_threadpool(_ingest_safely, shop, name, blob, user['name'])
    finally:                                   # 中途出错也留痕、也清缓存：排在前面的文件可能已经入库了
        if results:
            db.audit(user['name'], 'ec_douyin_upload', target=shop, detail='；'.join('%s:%s' % (r['name'], r.get('label') or r.get('error')) for r in results)[:300])
        with _lock: _cache.clear()
    return {'ok': True, 'results': results}


def _body(rows):
    """比较两份快照内容是否一样：不看 n（文件内行序）、x（哪次导出），也不看行的先后（同一秒的几行，两次导出里先后可能不同）。"""
    return sorted(({k: v for k, v in r.items() if k not in ('n', 'x')} for r in rows), key=lambda r: r['id'])


def _ingest(shop, name, blob, operator, out):
    """一个文件：认类型 → 按月和库里现用的那份合并 → 和现用的不一样才存。结果逐条记进 out（zip 里有几个 csv 就有几条）。"""
    for part in model.parse(blob, name, shop):
        if not part['kind']:
            out.append({'name': part['name'], 'ok': False, 'error': part['skip']}); continue
        kind, rows = part['kind'], part['rows']
        if kind == 'dy_platform':              # 订单状态是哪次导出时的：只认文件名里的导出时刻；文件改过名认不出就不记，新旧改按订单状态有没有倒退来判断
            x = model.export_time(part['name'])
            rows = [dict(r, x=x) for r in rows]
        months, fresh, skipped = model.by_month(rows), False, []
        # 先把月份都看一遍再动手存：有一个不对就整份不收，免得存了一半却说没入库
        if any(not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', k) for k in months):
            raise ValueError('「%s」有日期被改过格式（像是用 Excel 打开后另存的），请传平台导出的原始文件' % part['name'])
        with db._engine.connect() as cx:
            old = {r.period: r for r in _latest(cx, shop, list(months), [kind])}
        for month, new in sorted(months.items()):
            wbr.check_period(month)
            before = json.loads(gzip.decompress(old[month].payload))['rows'] if month in old else []
            names = json.loads(old[month].filenames or '[]') if month in old else []
            merged = model.merge(before, new, kind if kind in ('dy_platform', 'dy_orders') else '', skipped)
            # 判重按内容比：和现用的那份逐行一样才算重复。只比几个字段的话，补了新字段的重传、只改了备注的导出都会被当成重复；
            # 指纹里带上现用那份的编号，保证不一样就一定存得进去（表上有唯一约束，会和历史上任何一版比）
            if month in old and _body(merged) == _body(before): continue
            digest = hashlib.sha256(json.dumps([model.FORMAT, old[month].id if month in old else 0, _body(merged)], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            saved = wbr.save_source(month, shop, kind, digest, list(dict.fromkeys(names + [part['name']])),
                                    {'rows': merged, 'status': 'ready', 'extra': dict(model.snapshot_note(kind, merged), format=model.FORMAT)}, operator)
            fresh = fresh or not saved['duplicate']
        warnings = ['%s %d 行' % (m, len(v)) for m, v in sorted(months.items())]
        if skipped: warnings.append('这份导出比系统里的旧，%d 行没有覆盖' % len(skipped))
        out.append({'name': part['name'], 'ok': True, 'kind': kind, 'label': model.KINDS[kind], 'rows': len(rows), 'duplicate': not fresh, 'warnings': warnings})


def _ingest_safely(shop, name, blob, operator):
    """每个文件各出各的结果，一个读不了不连累同批其它文件；读不了的说人话，原始报错只进服务器日志。"""
    out = []
    fail = lambda why: out + [{'name': name, 'ok': False, 'error': why}]
    try:
        with _upload_lock: _ingest(shop, name, blob, operator, out)
    except (zipfile.BadZipFile, zlib.error): return fail('压缩包损坏，请重新下载后再传')
    except csv.Error: return fail('文件内容格式不对，不像平台导出的原始文件')
    except HTTPException as e: return fail(str(e.detail)[:120])
    except (ValueError, OSError, KeyError) as e: return fail(str(e)[:120])
    except SQLAlchemyError:
        log.exception('抖音资料入库失败：%s', name)
        return fail('系统保存时出错，文件没有入库（不是文件的问题）；请稍后重传，还不行请找管理员')
    except Exception:
        log.exception('抖音资料上传读不了：%s', name)
        return fail('这个文件读不了，请确认是平台导出的原始文件')
    return out


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
    raw = kc._query(s, conf, 'AR_receivable', open_items.AR_FIELDS + [('F_ora_Text3', 't3')],
                    "FSETTLEORGID.FName='%s' and FCUSTOMERID.FName='%s' and FCancelStatus='A' and FDATE>='%s'" % (ORG, customer.replace("'", ''), since),
                    order='FBillNo ASC')
    # 蓝字应收：Text3=出库单号、Text4=交易单号；红字应收：Text3=交易单号、Text4=原订单号
    ship = lambda r: next((str(r.get(k) or '').strip() for k in ('t3', 't4') if str(r.get(k) or '').strip().startswith('CK')), '')
    bills = open_items._merge(raw, lambda r: dict(order=open_items.order_key(r.get('t6'), r.get('t4')), ship=ship(r)))
    try: book = _book(s, conf, customer.replace("'", ''), period)
    except Exception as e:
        log.warning('抖音月结读金蝶账面余额失败 %s %s：%s', period, shop, type(e).__name__)
        book = {'error': '金蝶账面余额这次没读到，重新同步一次再看'}
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
        except Exception as e:
            _jobs[key]['error'] = '金蝶这会儿没连上，应收没有同步成；过一会儿再点一次同步'
            log.exception('抖音应收同步失败 %s %s', period, shop)
            try: db.audit(user['name'], 'ec_douyin_ar_refresh_failed', target='%s %s' % (period, shop), detail=type(e).__name__)
            except Exception: pass
        finally:
            _jobs[key]['running'] = False
            with _lock: _cache.pop(key, None)
    threading.Thread(target=job, daemon=True).start()
    return {'ok': True}


def _result(period, shop):
    """对账结果（按资料版本 + 应收同步时间缓存）。没同步过应收返回 (None, None, 资料行数)。
    先只查现用的是哪几份快照；和缓存对得上就直接用，对不上才把四类资料整份解开重算。"""
    version, counts = _version(period, shop)
    meta = _ar_meta(period, shop)
    if not meta: return None, None, counts
    key = (period, shop); stamp = (version, meta['ts'])
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] == stamp: return hit[1], hit[2], counts
        gate = _building.setdefault(key, threading.Lock())
    with gate:                                  # 页面一打开是几个请求同时到：等第一个算完直接用
        with _lock:
            hit = _cache.get(key)
            if hit and hit[0] == stamp: return hit[1], hit[2], counts
        loaded, rows = _sources(period, shop)
        with gzip.open(_ar_path(period, shop), 'rt', encoding='utf-8') as stream:
            saved = json.load(stream)
        result = model.reconcile(period, rows['dy_settle'], rows['dy_ledger'], saved['bills'], rows['dy_orders'])
        first = model.push_plan(period, rows['dy_settle'], rows['dy_orders'], result)      # 不看推没推过：只为标出"可核销但系统不推"的
        for b in result['bills']: b['hold'] = first['held'].get(b['no'], '')
        # 还没结算的蓝字应收：平台订单现在什么情况（已关闭 / 售后中 / 等结算），有平台订单明细才有。
        # 红字不说（那句话说的是钱来不来，套在红字上分不清指哪笔）；合单的说同组里还没结算的那几个订单
        index = model.platform_index(rows['dy_platform'])
        for b in result['bills']:
            waiting = b['cat'] in ('overdue', 'transit') and b['open'] > 0
            b['pstate'] = model.platform_states(index, b.get('pending') or [b['order']], result['end'], b.get('merged') or 0) if waiting else ''
        model.returns_notes(result['bills'], model.returns_index(rows['dy_returns'], period), rows['dy_orders'])
        result['held'] = [dict(key=k, label=model.PUSH_SKIP[k], **v) for k, v in first['skipped'].items() if v['count']]
        result['pushable'] = first['eligible']
        with _lock:
            _cache.pop(key, None)
            _cache[key] = ((loaded, meta['ts']), result, saved.get('book') or {}, rows, saved['bills'])
            while len(_cache) > CACHE_KEEP: _cache.pop(next(iter(_cache)))            # 最早放进去的先丢
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
            'balance': result['balance'], 'book': book, 'coverage': result['coverage'], 'end': result['end'], 'missing': len(result['missing']),
            'held': result['held'], 'pushable': result['pushable'], 'overdue_days': days}
    return out


def _need(period, shop):
    result, book, _ = _result(period, shop)
    if not result: raise HTTPException(404, '还没有同步这家店的金蝶应收')
    return result, book


@router.get('/bills')
def bills(request: Request, period: str, shop: str, cat: str = '', q: str = '', page: int = 1):
    require(request); check(period, shop)
    result, _ = _need(period, shop)
    if cat and cat != 'hold' and cat not in model.CATEGORIES: raise HTTPException(400, '分类不对')
    return dict(model.page(model.select(result, cat, q), page), ok=True)


@router.get('/order')
def order(request: Request, period: str, shop: str, order: str):
    """一个订单的依据：抖音每一笔动账 + 金蝶每一张应收单。"""
    require(request); check(period, shop)
    _need(period, shop)
    with _lock: hit = _cache.get((period, shop))
    if not hit: raise HTTPException(404, '对账结果已更新，请刷新后重试')
    detail = model.order_detail(period, hit[3]['dy_settle'], hit[3]['dy_ledger'], hit[4], order.strip(), hit[3]['dy_orders'], hit[3]['dy_platform'])
    mine = [b for b in hit[1]['bills'] if b['order'] in detail['members']]
    cats = {b['cat'] for b in mine}; detail['reason'] = next((b['reason'] for b in mine if b['reason']), '')
    detail['categories'] = [model.category_label(c, hit[1]['overdue_days']) for c in model.CATEGORIES if c in cats]
    detail['hold'] = next((model.PUSH_SKIP[b['hold']] for b in mine if b.get('hold')), '')      # 金额对得上、但系统不下推的原因
    detail['rnote'] = next((b['rnote'] for b in mine if b.get('rnote')), '')
    detail['returns'] = [r for r in hit[3]['dy_returns'] if r['order'] in detail['members'] and r['t'][:7] <= period]
    return dict(detail, ok=True, period=period)


@router.get('/order-bills')
def order_bills(request: Request, period: str, shop: str, nos: str):
    """订单抽屉里应收单的分录明细（物料、数量、单价、税、来源单据）：现查金蝶，只读。只认这家店对账结果里有的应收单号。"""
    require(request); check(period, shop)
    _need(period, shop)
    with _lock: hit = _cache.get((period, shop))
    if not hit: raise HTTPException(404, '对账结果已更新，请刷新后重试')
    known = {b['no'] for b in hit[4]}
    wanted = [n for n in dict.fromkeys(x.strip() for x in nos.split(',')) if n in known][:60]
    if not wanted: return {'ok': True, 'bills': {}}
    key, now = (period, shop, hit[0][1], tuple(wanted)), time.time()
    with _lock:
        kept = _bill_cache.get(key)
        if kept and now - kept[0] < 300: return {'ok': True, 'bills': kept[1]}
    try:
        s, conf = kc.login()
        rows, err = kc._query_raw(s, conf, 'AR_receivable', ','.join(model.BILL_LINE_KEYS), 'FBillNo in (%s)' % ','.join("'%s'" % n for n in wanted), 0)
    except Exception as e:
        rows, err = None, str(e) or type(e).__name__
    if err:                                     # 金蝶的报错原文带服务器地址、程序字样：只进日志，页面上说人话
        log.warning('抖音订单抽屉取金蝶应收分录失败 %s %s：%s', period, shop, str(err)[:500])
        return {'ok': True, 'bills': {}, 'error': '金蝶这会儿没连上，物料、数量这些明细暂时看不了；上面的对账不受影响，稍后重开这张单再试'}
    found = model.bill_lines(rows)
    with _lock:
        for k in [k for k, v in _bill_cache.items() if now - v[0] >= 300]: _bill_cache.pop(k, None)
        _bill_cache[key] = (now, found)
    return {'ok': True, 'bills': found}


@router.get('/flows')
def flows(request: Request, period: str, shop: str, scene: str, q: str = '', page: int = 1):
    """「账户进出汇总」里某一项的逐笔明细：运费险按保单逐单（来自账户流水压缩包里的保费支出），其余按账户流水逐笔。只读已上传的资料。"""
    require(request); check(period, shop)
    _need(period, shop)
    with _lock: hit = _cache.get((period, shop))
    if not hit: raise HTTPException(404, '对账结果已更新，请刷新后重试')
    rows = hit[3]
    end = model.period_end(period)                                                # 和订单抽屉取数的范围一致：到本期末为止有应收或有动账的才点得开
    known = {b['order'] for b in hit[4] if b['order'] and b['date'] <= end} | {r['order'] for r in rows['dy_settle'] if r['order'] and r['t'][:7] <= period}
    sub2main = {r['id']: r['order'] for r in rows['dy_platform']}
    kind, found = model.flow_rows(period, rows['dy_ledger'], rows['dy_settle'], rows['dy_insure'], scene.strip(), known, sub2main)
    q = q.strip()
    if q: found = [r for r in found if q in r['id'] or q in (r.get('order') or '') or q in (r.get('flow') or '')]
    size = 50; pages = max(1, -(-len(found) // size)); page = min(max(1, page), pages)
    return {'ok': True, 'kind': kind, 'rows': found[(page - 1) * size:page * size], 'total': len(found), 'page': page, 'pages': pages,
            'amount': round(sum(r['amt'] for r in found), 2), 'insure_missing': scene.strip() == model.INSURE_SCENE and kind != 'insure'}


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


# ---------------- 下推收款单到金蝶「暂存」（V2.821）----------------
# 这是电商这条线第一处写金蝶的地方（用户 2026-10-05 定：干净的单系统批量备好，有分歧的交给会计）。
# 只做到「暂存」：不保存、不提交、不审核——暂存单没有单号、不占应收的关联额度，删掉后业务上不留东西
# （金蝶上机操作日志会留下"生成收款单 / 删除"的记录，用户知情并接受）；
# 会计在金蝶里打开看过，自己保存→提交→审核，审核那一刻金蝶才核销。
# 实测（2026-10-05，正式账套）：下推规则与手工下推同一条，100 张 1.7 秒；默认收款明细是一张应收一行且结算方式为空，
# 用「暂存」接口改成 到账一行 + 一种扣款一行。
_PUSH_SVC = 'Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.Push.common.kdsvc'
_DRAFT_SVC = 'Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.Draft.common.kdsvc'
_PUSH_RULE, _SETTLE_CASH, _SETTLE_INNER, _PURPOSE = 'AR_recableToRecBill', 'JSFS32_SYS', 'JSFS41_SYS', 'SFKYT01_SYS'
_API_USER = '系统操作员'
PUSH_KEY = 'ec_douyin_push'
PUSH_MODES = {'off': '关', 'dry': '演练', 'on': '真做'}
PUSH_MAX = 10000                              # 每批上限＝金蝶一张收款单最多挂的源单行数：一次下推正好一张收款单（实测超过会被金蝶拆单）
_PUSH_TIMEOUT = 1800                          # 大批量下推金蝶要算很久：单独放宽到 30 分钟（kc._post 只等 2 分钟）
_push_lock = threading.Lock()
_push_jobs = {}                               # (period, shop) -> {'running', 'stage', 'error', 'done', 'started'}
_DS = {'Z': '暂存（等会计保存）', 'A': '已保存', 'B': '审核中', 'C': '已审核', 'D': '重新审核'}


def _push_conf():
    c = db.get_setting(PUSH_KEY, {}) or {}
    size = c.get('size') if isinstance(c.get('size'), int) and 1 <= c['size'] <= PUSH_MAX else 500
    return {'mode': c.get('mode') if c.get('mode') in PUSH_MODES else 'dry', 'size': size,
            'base': str(c.get('base') or 'FYXM001.001产品销售002'),        # 扣款行上的费用项目：照 8 月抖音收款单填「电商」
            'by': str(c.get('by') or ''), 'at': str(c.get('at') or '')}    # 最后一次是谁、什么时候改的档位 / 每批张数


def _push_log_path(period, shop):
    return os.path.join(ec.EC_UPLOAD_DIR, period, '抖音下推记录_%s.json' % hashlib.sha256(shop.encode()).hexdigest()[:10])


def _push_log(period, shop):
    try:
        with open(_push_log_path(period, shop), encoding='utf-8') as f: return json.load(f)
    except (OSError, ValueError): return []


def _push_log_save(period, shop, log):
    path = _push_log_path(period, shop); os.makedirs(os.path.dirname(path), exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=os.path.dirname(path), prefix='.push-', suffix='.tmp', delete=False) as f:
        json.dump(log, f, ensure_ascii=False); temp = f.name
    os.replace(temp, path)


def _kd_err(res):
    st = (res.get('Result') or {}).get('ResponseStatus') or {}
    if st.get('IsSuccess'): return ''
    return '；'.join(str(e.get('Message') or '') for e in st.get('Errors') or [])[:300] or '金蝶没有返回成功'


def _post_long(s, conf, svc, params):
    """同 kc._post，只是等得久。"""
    kc.count_call(svc)      # 这里绕开了 kc._post(要等更久)，调用次数自己记一笔(V2.875)
    r = s.post('%s/%s' % (conf['server_url'], svc), data=json.dumps({'parameters': params}, ensure_ascii=False).encode('utf-8'),
               headers={'Content-Type': 'application/json;charset=utf-8'}, timeout=_PUSH_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _receipts(s, conf, fids):
    """这几张收款单在金蝶里现在什么样。查不到＝已被删掉。"""
    out = {}
    for i in range(0, len(fids), 50):
        rows, err = kc._query_raw(s, conf, 'AR_RECEIVEBILL', 'FID,FBillNo,FDOCUMENTSTATUS,FWRITTENOFFSTATUS,FCreatorId.FName',
                                  'FID in (%s)' % ','.join(str(int(f)) for f in fids[i:i + 50]), 0)
        if err: raise kc.KingdeeError(err)
        for r in rows: out[int(r[0])] = {'no': str(r[1] or '').strip(), 'ds': r[2], 'ws': r[3], 'creator': r[4]}
    return out


def _drafts(s, conf, customer):
    """接口账号给这家店建的、还在暂存且没有单号的收款单（内码 → 创建时间）。用来找下推中途断掉留下的"没登记"的暂存单。"""
    rows, err = kc._query_raw(s, conf, 'AR_RECEIVEBILL', 'FID,FBillNo,FCreateDate',
                              "FCreatorId.FName='%s' and FDOCUMENTSTATUS='Z' and FCONTACTUNIT.FName='%s'" % (_API_USER, customer.replace("'", '')), 0)
    if err: raise kc.KingdeeError(err)
    return {int(r[0]): str(r[2])[:19].replace('T', ' ') for r in rows if not str(r[1] or '').strip()}


def _push_state(period, shop, customer='', session=None):
    """下推记录 + 每批在金蝶里的现状；还占着应收的批（金蝶里还在的）里的单号不再推。"""
    log = _push_log(period, shop)
    alive = [b for b in log if not b.get('deleted')]
    live, problem, orphans = {}, '', []
    try:
        s, conf = session or kc.login()
        if alive: live = _receipts(s, conf, [b['fid'] for b in alive])
        if customer:
            known = {int(b['fid']) for b in alive}
            orphans = [{'fid': f, 'at': at} for f, at in sorted(_drafts(s, conf, customer).items()) if f not in known]
    except Exception as e: problem = '读金蝶收款单状态失败（%s），先按记录显示' % type(e).__name__
    taken = set()
    for b in log:
        now = live.get(int(b['fid'])) if not problem else None
        gone = bool(b.get('deleted')) or (not problem and now is None)
        b['state'] = ('已撤回' if b.get('deleted') else '金蝶里已被删掉' if gone else '状态未知' if problem
                      else _DS.get(now['ds'], now['ds']) + ('·已核销' if now['ws'] == 'C' else ''))
        b['number'] = (now or {}).get('no', '') if not gone else ''
        b['can_undo'] = bool(now) and now['ds'] == 'Z' and not now['no']
        if not gone: taken |= set(b['bills'])
    return log, taken, problem, orphans


def _push_plan(period, shop, customer='', session=None):
    result, book = _need(period, shop)
    with _lock: hit = _cache.get((period, shop))
    if not hit: raise HTTPException(404, '对账结果已更新，请刷新后重试')
    conf_ = _push_conf()
    log, taken, problem, orphans = _push_state(period, shop, customer, session)
    plan = model.push_plan(period, hit[3]['dy_settle'], hit[3]['dy_orders'], result, taken, conf_['size'])
    return plan, log, problem, conf_, book, result, orphans


@router.get('/push/plan')
def push_plan(request: Request, period: str, shop: str):
    user = require(request); selected = check(period, shop)
    job = _push_jobs.get((period, shop), {})
    plan, log, problem, conf_, book, result, orphans = _push_plan(period, shop, '' if job.get('running') else selected['kd_name'])
    batch = dict(plan['batch']); batch.pop('bills'); batch.pop('groups')
    fee_names = set(model.FEE_COLUMNS)
    other = [d for d in result['draft']['deductions'] if d['name'] not in fee_names]
    return {'ok': True, 'conf': dict(conf_, mode_label=PUSH_MODES[conf_['mode']], max=PUSH_MAX), 'account': book.get('account', ''), 'can_admin': True,                       # 老前端用的字段：现在能不能改设置看的是上传 / 跑批权限，由页面按 canEdit 判断
            'eligible': plan['eligible'], 'pushed': plan['pushed'], 'left': plan['left'], 'batch': batch,
            'skipped': [dict(key=k, label=model.PUSH_SKIP[k], **v) for k, v in plan['skipped'].items() if v['count']],
            'other': {'lines': other, 'total': round(sum(d['amount'] for d in other), 2)},
            'batches': [{k: b.get(k) for k in ('fid', 'at', 'by', 'count', 'total', 'first', 'last', 'state', 'number', 'can_undo', 'seconds')} for b in reversed(log)],
            'orphans': orphans, 'problem': problem,
            'job': {'running': bool(job.get('running')), 'stage': job.get('stage', ''), 'error': job.get('error', ''), 'done': job.get('done'), 'batch': job.get('batch', 1),
                    'seconds': round(time.time() - job['started']) if job.get('running') else 0}}


@router.get('/push/export')
def push_list(request: Request, period: str, shop: str, fid: str = ''):
    """推了什么，下载成 Excel：每张应收一行（源单编号、本次收款金额、平台订单号）+ 每批的收款明细。fid 给了（可以逗号隔开给几个）只出那几批，不给出全部没撤回的。"""
    user = require(request); selected = check(period, shop)
    _need(period, shop)
    with _lock: hit = _cache.get((period, shop))
    if not hit: raise HTTPException(404, '对账结果已更新，请刷新后重试')
    want = {int(x) for x in fid.split(',') if x.strip().isdigit()}
    batches = [b for b in _push_log(period, shop) if not b.get('deleted') and (not want or int(b['fid']) in want)]
    if not batches: raise HTTPException(404, '没有这一批，或者已经撤回了')
    db.audit(user['name'], 'ec_douyin_push_export', target='%s %s' % (period, shop), detail='%d 批 %d 张' % (len(batches), sum(len(b['bills']) for b in batches)))
    name = '抖音已下推清单_%s_%s%s.xlsx' % (selected['name'], period, ('_%s' % batches[0]['at'][:16].replace(':', '').replace(' ', '_')) if len(want) == 1 else '')
    return Response(model.push_export(selected['name'], period, batches, hit[4]),
                    media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': "attachment; filename*=UTF-8''%s" % quote(name)})


@router.post('/push/mode')
def push_mode(request: Request, mode: str = Form(''), size: int = Form(0)):
    """改档位 / 每批张数。有电商对账上传 / 跑批权限的人都能改（能点下推的人就能改）；每次改动留痕：谁、什么时候、从什么改成什么。"""
    user = require(request, write=True)
    c = _push_conf(); before = '%s · 每批 %d 张' % (PUSH_MODES[c['mode']], c['size'])
    if mode:
        if mode not in PUSH_MODES: raise HTTPException(400, '档位不对')
        c['mode'] = mode
    if size:
        if not 1 <= size <= PUSH_MAX: raise HTTPException(400, '每批 1 到 %d 张' % PUSH_MAX)
        c['size'] = size
    after = '%s · 每批 %d 张' % (PUSH_MODES[c['mode']], c['size'])
    if after != before: c.update(by=user['name'], at=ec._now())
    db.set_setting(PUSH_KEY, c, operator=user['name'])
    db.audit(user['name'], 'ec_douyin_push_mode', detail='%s → %s' % (before, after))
    return {'ok': True, 'conf': c}


def _push_do(period, shop, selected, user, job):
    """真做一批（在后台线程里）：推前逐张复核 → 下推 → 改收款明细 → 记录。任何一步不对就停；
    下推那一步如果断了（超时等），去金蝶找有没有已经建出来的暂存单，有就删掉，不留不明不白的东西。"""
    session = kc.login(); s, conf = session
    plan, log, problem, conf_, book, _, _ = _push_plan(period, shop, '', session)
    if problem: raise ValueError(problem)
    batch = plan['batch']; nos = batch['bills']
    if not nos: raise ValueError('没有可以下推的应收了')
    if not book.get('account'): raise ValueError('认不出这家店的收款账户，请先重新同步金蝶应收')
    if batch['line_total'] != batch['total']: raise ValueError('这一批两边不平（应收 %.2f / 收款明细 %.2f），没动' % (batch['total'], batch['line_total']))
    job['stage'] = '到金蝶逐张复核 %d 张应收的状态' % len(nos)
    live = []
    for i in range(0, len(nos), 200):
        rows, err = kc._query_raw(s, conf, 'AR_receivable', 'FBillNo,FWRITTENOFFSTATUS,FDOCUMENTSTATUS,FCancelStatus,FPAYAMOUNTFOR,FWRITTENOFFAMOUNTFOR_P,FRELATEHADPAYAMOUNT',
                                  'FBillNo in (%s)' % ','.join("'%s'" % n for n in nos[i:i + 200]), 0)
        if err: raise ValueError('复核应收状态失败')
        live += rows
    changed = [r[0] for r in live if not (r[1] == 'A' and r[2] == 'C' and r[3] == 'A' and not float(r[5] or 0) and not float(r[6] or 0))]
    cents = lambda v: int(round(float(v or 0) * 100))
    if len({r[0] for r in live}) != len(nos) or changed or sum(cents(r[4]) for r in live) != cents(batch['total']):
        raise ValueError('这一批里有 %d 张应收的状态或金额在金蝶里变了（可能有人刚核销过），没动。请先重新同步金蝶应收。' % max(len(changed), 1))
    before = set(_drafts(s, conf, selected['kd_name']))
    started = time.time(); job['stage'] = '金蝶正在下推 %d 张应收（大批量要等几分钟）' % len(nos)
    try:
        res = _post_long(s, conf, _PUSH_SVC, ['AR_receivable', json.dumps({'Ids': '', 'Numbers': nos, 'EntryIds': '', 'RuleId': _PUSH_RULE, 'TargetBillTypeId': '', 'TargetOrgId': 0,
                         'TargetFormId': 'AR_RECEIVEBILL', 'IsEnableDefaultRule': 'false', 'IsDraftWhenSaveFail': 'true', 'CustomParams': {}}, ensure_ascii=False)])
    except Exception as e:
        # 请求断了不等于金蝶没做：等一等再找新冒出来的暂存单，找到就删
        job['stage'] = '下推请求中断，正在检查金蝶里有没有留下暂存单'
        removed, failed = [], []
        for _ in range(6):
            time.sleep(20)
            try:
                s, conf = kc.login()
                for fid in set(_drafts(s, conf, selected['kd_name'])) - before - set(removed):
                    try: kc.delete_bill('AR_RECEIVEBILL', fid, s, conf); removed.append(fid)
                    except Exception: failed.append(fid)
            except Exception: pass
        raise ValueError('下推请求中断（%s，等了 %d 秒）。%s' % (type(e).__name__, time.time() - started,
                         ('金蝶里留下的暂存单已删掉（内码 %s）。' % '、'.join(map(str, removed))) if removed and not failed else
                         ('金蝶里有暂存单没删掉（内码 %s），请在页面“没登记的暂存单”里处理。' % '、'.join(map(str, set(failed)))) if failed else
                         '两分钟内没发现金蝶留下暂存单；稍后刷新本页，如出现“没登记的暂存单”请删掉。'))
    err = _kd_err(res); made = ((res.get('Result') or {}).get('ResponseStatus') or {}).get('SuccessEntitys') or []
    fids = [int(m['Id']) for m in made]

    def drop_all(why):
        left = []
        try: s2, conf2 = kc.login()
        except Exception: s2 = conf2 = None
        for f in fids:
            try: kc.delete_bill('AR_RECEIVEBILL', f, s2, conf2)
            except Exception: left.append(f)
        return ValueError('%s。%s' % (why, '刚建的暂存单已删掉' if not left else '有暂存单没删掉（内码 %s），请在页面“没登记的暂存单”里处理' % '、'.join(map(str, left))))
    if err or not made: raise drop_all('金蝶下推没成功：%s' % (err or '没有生成收款单'))
    pushed_in = round(time.time() - started, 1)
    # 金蝶可能把一次下推拆成几张收款单：读回每张里实际挂了哪些应收，各算各的收款明细
    job['stage'] = '下推完成（%s 秒，金蝶生成了 %d 张收款单），正在读回每张挂了哪些应收' % (pushed_in, len(fids))
    try: inside = [[r['no'] for r in kc._query(s, conf, 'AR_RECEIVEBILL', [('FSRCBILLNO', 'no')], 'FID=%d' % f, order='FSRCBILLNO ASC')] for f in fids]
    except Exception as e: raise drop_all('读回收款单的源单失败（%s）' % type(e).__name__)
    parts = model.push_split(period, batch['groups'], inside)
    if not parts or sorted(n for nos_ in inside for n in nos_) != sorted(nos):
        raise drop_all('金蝶生成的 %d 张收款单里挂的应收和这一批对不上（或同一订单的应收被拆到了两张单里）' % len(fids))
    job['stage'] = '正在把 %d 张收款单的收款明细改成 到账行 + 扣款行' % len(fids)
    t2 = time.time()
    for i, (m, part) in enumerate(zip(made, parts)):
        entries = (m.get('EntryIds') or {}).get('FRECEIVEBILLENTRY') or []
        lines = []
        for line, eid in zip(part['lines'], entries + [None] * len(part['lines'])):
            x = {'FPURPOSEID': {'FNumber': _PURPOSE}, 'FRECTOTALAMOUNTFOR': line['amount'], 'FRECAMOUNTFOR_E': line['amount'], 'FCOMMENT': line['memo']}
            if line['kind'] == 'cash': x.update(FSETTLETYPEID={'FNumber': _SETTLE_CASH}, FACCOUNTID={'FNumber': book['account']})
            else: x.update(FSETTLETYPEID={'FNumber': _SETTLE_INNER}, F_ora_Base={'FNumber': conf_['base']})
            if eid: x['FEntryID'] = eid
            lines.append(x)
        try:
            err = _kd_err(_post_long(s, conf, _DRAFT_SVC, ['AR_RECEIVEBILL', json.dumps({'IsDeleteEntry': 'true', 'Model': {'FID': fids[i], 'FDATE': model.period_end(period),     # 表头备注不写（业务方 2026-10-10 定）：是哪批、谁推的，看工作台的下推记录和清单
                                                                                                                          'FRECEIVEBILLENTRY': lines}}, ensure_ascii=False)]))
        except Exception as e: err = '请求中断（%s）' % type(e).__name__
        if err: raise drop_all('收款明细没改成功：%s' % err)
    log = _push_log(period, shop)
    seconds = round(time.time() - started, 1)
    for f, nos_, part in zip(fids, inside, parts):
        log.append({'fid': f, 'at': ec._now(), 'by': user['name'], 'count': part['count'], 'total': part['total'], 'first': min(nos_), 'last': max(nos_),
                    'lines': part['lines'], 'bills': nos_, 'seconds': seconds, 'push_seconds': pushed_in, 'edit_seconds': round(time.time() - t2, 1)})
    _push_log_save(period, shop, log)
    db.audit(user['name'], 'ec_douyin_push', target='%s %s' % (period, shop),
             detail='下推 %d 张应收 %.2f → 金蝶暂存收款单 %d 张（内码 %s，%s 秒）' % (len(nos), batch['total'], len(fids), '、'.join(map(str, fids)), seconds))
    return {'fids': fids, 'receipts': [{'fid': f, 'count': p['count'], 'total': p['total']} for f, p in zip(fids, parts)],
            'count': len(nos), 'total': batch['total'], 'seconds': seconds, 'push_seconds': pushed_in}


@router.post('/push/run')
def push_run(request: Request, period: str = Form(...), shop: str = Form(...), times: int = Form(1)):
    """下推到金蝶暂存。演练档不碰金蝶。真做档在后台线程里跑（大批量要几分钟），页面轮询 /push/plan 看进度。
    times＝连着推几批：1 只推下一批；0 一批接一批推到没有为止（每批一张收款单）。中途哪一批出错就停在那里，前面推成的保留。"""
    user = require(request, write=True); selected = check(period, shop)
    if _push_conf()['mode'] != 'on': raise HTTPException(400, '现在是「%s」档，不会写金蝶。要真做请管理员把档位改成「真做」。' % PUSH_MODES[_push_conf()['mode']])
    _need(period, shop)
    if not _push_lock.acquire(blocking=False): raise HTTPException(409, '正在下推中，请稍候')
    if times < 0 or times > 100: raise HTTPException(400, '连推批数不对')
    job = _push_jobs[(period, shop)] = {'running': True, 'stage': '准备中', 'error': '', 'done': None, 'started': time.time(), 'batch': 1}

    def work():
        done = {'receipts': [], 'count': 0, 'total': 0.0, 'seconds': 0.0, 'push_seconds': 0.0, 'batches': 0}
        try:
            while True:
                job['batch'] = done['batches'] + 1
                try: one = _push_do(period, shop, selected, user, job)
                except ValueError as e:
                    if done['batches'] and str(e) == '没有可以下推的应收了': break          # 连着推：推完了，正常结束
                    raise
                done['receipts'] += one['receipts']; done['batches'] += 1; done['count'] += one['count']
                for k in ('total', 'seconds', 'push_seconds'): done[k] = round(done[k] + one[k], 2)
                job['done'] = dict(done)
                if times and done['batches'] >= times: break
        except ValueError as e: job['error'] = ('第 %d 批没推成：' % (done['batches'] + 1) if done['batches'] else '') + str(e)
        except Exception as e: job['error'] = '下推出错（%s）：%s' % (type(e).__name__, str(e)[:200])
        finally:
            job['running'] = False; _push_lock.release()
    threading.Thread(target=work, daemon=True).start()
    return {'ok': True, 'started': True}


@router.post('/push/undo')
def push_undo(request: Request, period: str = Form(...), shop: str = Form(...), fid: int = Form(...)):
    """撤回一批：只删还在「暂存」、没有单号、且是接口账号建的那张；会计保存过的不动。
    也用来删"没登记的暂存单"（下推中途断掉留下的）：同样的三个条件，外加必须是这家店的。"""
    user = require(request, write=True); selected = check(period, shop)
    log = _push_log(period, shop)
    entry = next((b for b in log if int(b['fid']) == fid and not b.get('deleted')), None)
    try:
        s, conf = kc.login()
        if not entry and fid not in _drafts(s, conf, selected['kd_name']): raise HTTPException(404, '下推记录里没有这一批，金蝶里也没有这张暂存单')
        now = _receipts(s, conf, [fid]).get(fid)
        if now and (now['ds'] != 'Z' or now['no'] or now['creator'] != _API_USER):
            raise HTTPException(409, '这张收款单已经不是暂存草稿了（%s %s），系统不动它，请在金蝶里处理' % (now['no'], _DS.get(now['ds'], now['ds'])))
        if now: kc.delete_bill('AR_RECEIVEBILL', fid, s, conf)
    except HTTPException: raise
    except kc.KingdeeError as e: raise HTTPException(409, '金蝶没让删：%s' % str(e)[:200])
    except Exception as e: raise HTTPException(502, '连不上金蝶（%s）' % type(e).__name__)
    if entry:
        entry.update(deleted=True, deleted_at=ec._now(), deleted_by=user['name'])
        _push_log_save(period, shop, log)
    db.audit(user['name'], 'ec_douyin_push_undo', target='%s %s' % (period, shop), detail='删除暂存收款单 内码 %d（%s）' % (fid, ('%d 张应收' % entry['count']) if entry else '没登记的'))
    return {'ok': True}
