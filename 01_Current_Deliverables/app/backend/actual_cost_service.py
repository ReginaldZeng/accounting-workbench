# [Change Log] 2026-10-02 | Codex | V-draft | Period-bound full-cost generation and preserved snapshots
# [Change Log] 2026-10-04 | Claude / c | V2.789 | 试算因缺配置失败时，把缺的费用项目/产品分组名单写进状态（missing），页面直接让人补。
import hashlib
import json
import threading
import re
import base64
from datetime import datetime, timezone
from kernels.actual_cost import prepare, monthly_input_issues, MissingConfig
import kingdee_client as kingdee

# ponytail: 单服务进程内串行生成；多worker部署时改为数据库按期间的任务锁。
_RUN_LOCK = threading.RLock()
LATEST = 'actual_cost:latest'
INPUT = 'actual_cost:inputs'
CLOSE = 'actual_cost:close'


def fingerprint(sources, rules, supplement):
    stable_sources = {k:v for k,v in sources.items() if k not in ('fetched_at','book_current_period')}
    return hashlib.sha256(json.dumps([stable_sources,rules,supplement],ensure_ascii=False,
                                     sort_keys=True,allow_nan=False,separators=(',',':')).encode('utf8')).hexdigest()


def generate(year, period, org, rules, supplement, store, client=kingdee, operator='system',close_confirmation=None):
    """结账确认和人工试算共用；store复用db.period_inputs。"""
    year,period,org=int(year),int(period),str(org)
    if not 2000<=year<=2100 or not 1<=period<=12: raise ValueError('无效期间')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,8}',org): raise ValueError('账簿代码不符合期间存储长度要求')
    target=f'{year:04d}-{period:02d}';source='actual_cost:'+org
    def save_latest(payload):
        store.set_period_input(source,year,period,LATEST,payload,operator=operator)
        return payload
    with _RUN_LOCK:
        previous=store.get_period_input(source,year,period,LATEST)
        previous=(previous or {}).get('payload') or {}
        common=dict(org=org,period=target,checked_at=datetime.now(timezone.utc).isoformat())
        try:
            observed=client.fetch_accounting_period(org)
        except kingdee.KingdeeError:
            return save_latest(dict(common,status='failed',run_id=previous.get('run_id'),
                                    issues=['无法核实金蝶账簿期间，本次结果不可发布']))
        common['observed_period']=observed
        if observed<=target and (rules or {}).get('close_gate')!='manual':
            return save_latest(dict(common,status='reopened' if previous.get('run_id') else 'waiting_close',
                                    run_id=previous.get('run_id'),issues=['金蝶账簿本期尚未结账或已反结账']))
        if rules is None or supplement is None:
            return save_latest(dict(common,status='needs_inputs',run_id=previous.get('run_id'),
                                    issues=['缺少本期规则或补充依据']))
        missing=monthly_input_issues(rules,supplement)
        if missing:
            return save_latest(dict(common,status='needs_inputs',run_id=previous.get('run_id'),issues=missing))
        if rules.get('close_gate')=='manual':
            rules=dict(rules)
            rules.pop('close_confirmation',None)
            if close_confirmation:
                active=(store.get_period_input(source,year,period,CLOSE) or {}).get('payload')
                if active!=close_confirmation or active.get('org')!=org or active.get('period')!=target or active.get('input_digest')!=fingerprint({},rules,supplement):
                    return save_latest(dict(common,status='needs_inputs',run_id=previous.get('run_id'),issues=['结账确认已失效']))
                rules['close_confirmation']=close_confirmation
        save_latest(dict(common,status='running',run_id=previous.get('run_id'),issues=['正在重新取数校验']))
        try:
            sources=client.fetch_actual_cost_sources(year,period,org)
            if sources['period']!=target or sources['org']!=org: raise ValueError('返回快照期间或组织不符')
            result=prepare(sources,rules,supplement)
            if client.fetch_accounting_period(org)!=sources['book_current_period']:
                raise ValueError('生成期间账簿期间变化，停止发布')
            digest=fingerprint(sources,rules,supplement)
        except (KeyError, ValueError, kingdee.KingdeeError) as exc:
            # 保留上一版本快照，同时使当前下载状态失效；失败不得伪装为旧版本成功。
            return save_latest(dict(common,status='failed',run_id=previous.get('run_id'),
                                    issues=[str(exc) if isinstance(exc,ValueError) else
                                            '金蝶取数失败，请检查连接或源数据' if isinstance(exc,kingdee.KingdeeError) else
                                            '源数据或规则字段缺失，请核对本期输入'],
                                    **({'missing':{'kind':exc.kind,'names':exc.names}} if isinstance(exc,MissingConfig) else {})))
        except Exception:
            save_latest(dict(common,status='failed',run_id=previous.get('run_id'),issues=['生成程序异常，请检查服务日志']))
            raise  # 程序错误不得伪装为取数问题；旧快照仍留存，但失去当前可发布状态。
        # period_inputs.kind上限40；保留完整摘要校验，禁止短键碰撞复用错误快照。
        run_id='ac:r:'+digest[:32]
        existing=store.get_period_input(source,year,period,run_id)
        if existing:
            saved=existing['payload']
            if fingerprint(saved['sources'],saved['rules'],saved['supplement'])!=digest:
                return save_latest(dict(common,status='failed',run_id=previous.get('run_id'),issues=['快照键冲突，停止发布']))
        if existing is None:
            store.set_period_input(source,year,period,run_id,
                dict(digest=digest,sources=sources,rules=rules,supplement=supplement,result=result),operator=operator)
        return save_latest(dict(common,status='ready' if result['ready'] else 'needs_confirmation',
            run_id=run_id,reused=existing is not None,issues=result['issues'],controls=result['controls'],
            unallocated_wip=result['unallocated_wip']))


def current_result(year,period,org,store,client=kingdee):
    """下载/正式展示前再次核对期间，反结账后只能查看历史快照。"""
    with _RUN_LOCK:
        source='actual_cost:'+str(org)
        item=store.get_period_input(source,year,period,LATEST)
        latest=(item or {}).get('payload') or {}
        if latest.get('status')!='ready': raise ValueError('本期没有通过校验的正式结果')
        snapshot=store.get_period_input(source,year,period,latest['run_id'])
        if not snapshot: raise ValueError('结果快照缺失，需要重新生成')
        gate=snapshot['payload']['rules'].get('close_gate')
        observed=client.fetch_accounting_period(str(org))
        if gate=='manual':
            confirmation=snapshot['payload']['rules'].get('close_confirmation')
            active=(store.get_period_input(source,year,period,CLOSE) or {}).get('payload')
            if not active or active!=confirmation: raise ValueError('结账确认已撤销或发生变化，请重新确认出表')
            data=(store.get_period_input(source,year,period,INPUT) or {}).get('payload') or {}
            if not data or active.get('input_digest')!=fingerprint({},data['rules'],data['supplement']):
                raise ValueError('本期分摊依据已变化，请重新确认出表')
            if observed<snapshot['payload']['sources']['book_current_period']: raise ValueError('金蝶账簿期间回退，请重新确认结账')
        elif observed<=f'{int(year):04d}-{int(period):02d}':
            raise ValueError('金蝶已反结账，请重新生成；历史快照仍保留')
        elif gate!='ledger':
            raise ValueError('存货核算结账接口尚未验证，当前仅可试算')
        return snapshot['payload']


def export_result(year,period,org,run_id,store,formal=False,client=kingdee):
    """结账确认和页面下载共用：核实期间、校验版本、缓存正式Excel。"""
    from actual_cost_export import build_workbook
    with _RUN_LOCK:
        source='actual_cost:'+org
        latest=(store.get_period_input(source,year,period,LATEST) or {}).get('payload') or {}
        if latest.get('run_id')!=run_id or latest.get('status') not in ('ready','needs_confirmation'):
            raise ValueError('结果已失效或版本已变化，请重新取数后再导出')
        saved=store.get_period_input(source,year,period,run_id)
        if not saved: raise ValueError('结果快照缺失，请重新生成')
        if formal and (saved['payload']['rules'].get('close_gate')!='manual' or saved['payload']['rules'].get('basis')!='ledger'):
            raise ValueError('正式表须采用金蝶入账金额，并在工作台确认已结账')
        observed=client.fetch_accounting_period(org)
        if saved['payload']['rules'].get('close_gate')!='manual' and observed<=f'{year:04d}-{period:02d}':
            raise ValueError('本期未结账或已反结账，不能导出当前结果')
        snapshot=current_result(year,period,org,store,client) if formal else saved['payload']
        digest=fingerprint(snapshot['sources'],snapshot['rules'],snapshot['supplement'])
        key='ac:x:'+digest[:32]
        cache=store.get_period_input(source,year,period,key) if formal else None
        try:
            if cache:
                if cache['payload'].get('digest')!=digest: raise ValueError('导出快照键冲突，停止发布')
                data=base64.b64decode(cache['payload']['xlsx'],validate=True)
            else: data=build_workbook(snapshot,latest,formal=formal)
        except Exception:
            latest.update(status='failed',export_ready=False,issues=['Excel生成或读取失败，请重新生成'])
            store.set_period_input(source,year,period,LATEST,latest,operator='全成本生成')
            raise
        if client.fetch_accounting_period(org)!=observed: raise ValueError('导出期间结账状态变化，请重新核实')
        if formal:
            if not cache:
                store.set_period_input(source,year,period,key,dict(digest=digest,run_id=run_id,
                    xlsx=base64.b64encode(data).decode('ascii'),created_at=datetime.now(timezone.utc).isoformat()),operator='全成本生成')
            latest.update(export_ready=True,export_run_id=run_id)
            store.set_period_input(source,year,period,LATEST,latest,operator='全成本生成')
        return data
