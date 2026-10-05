# V2.810: 电商工作台·金蝶未核销清单。金蝶只做查询（ExecuteBillQuery），不新增、不下推、不审核。
import gzip
import json
import os
import tempfile
import threading
import time
from urllib.parse import quote
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import Response
import kingdee_client as kc
from core import db, _require_perm
from routers import ec
from kernels import ec_open_items as model

router = APIRouter(prefix='/api/ec/open-items')
ORG = '深圳市星期零食品科技有限公司'      # 与 kc.fetch_ec_receivables 默认口径一致：电商＝本组织 + 销售部门永续媒介中心
DEPT = '永续媒介中心'
META_KEY = 'ec_open_items_meta'
_lock = threading.Lock()
_job = {'running': False, 'error': ''}
_cache = {'key': None, 'snapshot': None}


def require(request, write=False):
    user = _require_perm(request, 'ec_settle_upload' if write else 'enter:ecomsettle')
    if not user: raise HTTPException(403, '无电商工作台权限')
    return user


def _path():
    return os.path.join(ec.EC_UPLOAD_DIR, '金蝶未核销清单.json.gz')


def _meta():
    meta = db.get_setting(META_KEY, None)
    return meta if meta and os.path.isfile(_path()) else None      # 文件被清了就别谎报有数据


def _snapshot():
    """读已保存的清单（按文件 mtime/size 缓存，同一份只解析一次）。没同步过返回 None。"""
    try: st = os.stat(_path())
    except OSError: return None
    key = (st.st_mtime_ns, st.st_size)
    with _lock:
        if _cache['key'] == key: return _cache['snapshot']
    with gzip.open(_path(), 'rt', encoding='utf-8') as stream:
        snapshot = json.load(stream)
    with _lock:
        _cache.update(key=key, snapshot=snapshot)
    return snapshot


def _sync(operator):
    started = time.time()
    s, conf = kc.login()
    open_only = "FWRITTENOFFSTATUS<>'C' and FCancelStatus='A'"
    ar = kc._query(s, conf, 'AR_receivable', model.AR_FIELDS,
                   "FSETTLEORGID.FName='%s' and FSALEDEPTID.FName='%s' and %s" % (ORG, DEPT, open_only), order='FBillNo ASC')
    rec = kc._query(s, conf, 'AR_RECEIVEBILL', model.REC_FIELDS,
                    "FPAYORGID.FName='%s' and FSALEDEPTID.FName='%s' and %s" % (ORG, DEPT, open_only), order='FBillNo ASC')
    snapshot = model.build(ar, rec)
    os.makedirs(os.path.dirname(_path()), exist_ok=True)
    temp = None
    try:                                                            # 先写临时文件再整体替换：读的人永远拿到完整的旧版或新版
        with tempfile.NamedTemporaryFile(dir=os.path.dirname(_path()), prefix='.open-items-', suffix='.tmp', delete=False) as f:
            temp = f.name
            with gzip.open(f, 'wt', encoding='utf-8') as stream:
                json.dump(snapshot, stream, ensure_ascii=False, separators=(',', ':'))
        os.replace(temp, _path())
    finally:
        if temp and os.path.exists(temp): os.remove(temp)
    meta = {'ts': ec._now(), 'operator': operator, 'ar': len(snapshot['ar']), 'rec': len(snapshot['rec']),
            'seconds': round(time.time() - started, 1), 'org': ORG, 'dept': DEPT}
    db.set_setting(META_KEY, meta, operator=operator)
    db.audit(operator, 'ec_open_items_refresh', detail='应收 %d 张 / 收款 %d 张' % (meta['ar'], meta['rec']))


@router.post('/refresh')
def refresh(request: Request):
    """只读同步金蝶 → 落盘。后台线程跑（几万张约十几秒），页面轮询 summary 看进度。"""
    user = require(request, write=True)
    with _lock:
        if _job['running']: raise HTTPException(409, '正在同步中，请稍候')
        _job.update(running=True, error='')

    def job():
        try: _sync(user['name'])
        except Exception as e: _job['error'] = '金蝶同步失败（%s），请检查金蝶连接后重试' % type(e).__name__
        finally: _job['running'] = False
    threading.Thread(target=job, daemon=True).start()
    return {'ok': True}


@router.get('/summary')
def summary(request: Request):
    require(request)
    snapshot, meta = _snapshot(), _meta()
    labels = {'piles': model.PILES, 'pile_help': model.PILE_HELP, 'hints': model.HINTS, 'ws': model.WS, 'ds': model.DS}
    return {'ok': True, 'meta': meta, 'refreshing': _job['running'], 'error': _job['error'], 'labels': labels,
            'summary': model.summary(snapshot) if snapshot and meta else None}


def _selected(side, pile, shop, month, hint, q):
    snapshot = _snapshot()
    if not snapshot or not _meta(): raise HTTPException(404, '还没有同步过金蝶未核销清单')
    if side not in model.SIDES: raise HTTPException(400, '单据类型不对')
    return snapshot, model.select(snapshot, side, pile, shop, month, hint, q)


@router.get('/bills')
def bills(request: Request, side: str = 'ar', pile: str = '', shop: str = '', month: str = '', hint: str = '', q: str = '', page: int = 1):
    require(request)
    _, rows = _selected(side, pile, shop, month, hint, q)
    out = model.page(rows, page)
    out['rows'] = [dict(b, hint_text=model.hint_text(b)) for b in out['rows']]
    return dict(out, ok=True)


@router.get('/export')
def export(request: Request, side: str = 'ar', pile: str = '', shop: str = '', month: str = '', hint: str = '', q: str = ''):
    user = require(request)
    snapshot, rows = _selected(side, pile, shop, month, hint, q)
    meta = _meta()
    db.audit(user['name'], 'ec_open_items_export', detail='%s %d 张' % (model.SIDES[side], len(rows)))
    name = '金蝶未核销清单_%s_%s.xlsx' % (model.SIDES[side], str(meta['ts'])[:10].replace('-', ''))
    return Response(model.export(snapshot, rows, side, meta),
                    media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': "attachment; filename*=UTF-8''%s" % quote(name)})
