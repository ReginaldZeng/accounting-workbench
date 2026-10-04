# [Change Log] 2026-10-02 | Codex | V-draft | Full-cost period API; Kingdee reads only
# [Change Log] 2026-10-04 | Claude / c | V2.789 | 建立本期配置可沿用任意一个有规则的月份（原先只能沿用更早的）；
#              /state 返回有规则的月份清单与可归类去向；/inputs 可保存费用项目归类（水电/租金/氮气三类固定项不可改）。
# [Change Log] 2026-10-04 | Claude / c | V2.791 | /product 带上直接材料拆分（没有底稿也能看）和各物料的差异原因备注；新增 /material-note 保存备注。
# [Change Log] 2026-10-04 | Claude / c | V2.792 | 底稿对比按人工设定的单位换算、替代料合并；新增 /material-map 维护这两类设定（按账簿存，跨期间、跨产品通用）。
import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from core import db, _require_perm
import actual_cost_service as service
from actual_cost_export import build_workbook
from kernels.actual_cost import number, CENTRES, monthly_input_issues, EDITABLE_EXPENSE_TARGETS
from actual_cost_trace import product_rows, fetch_product_trace
import actual_cost_standard as standard

router = APIRouter(prefix='/api/actual-cost')
INPUT = service.INPUT


def visible_standards(request, code):
    # Same source and draft/final access rules as BOM entry reads; no self-heal or approval writes.
    from core import _current_user, CFG
    user = _current_user(request)
    if not user: raise HTTPException(401,'请先登录')
    draft = db.user_can(user,'enter:bomdraft')
    approved = db.user_can(user,'bom:view_sheet') or db.user_can(user,'enter:bomstd')
    if not (draft or approved): raise HTTPException(403,'没有核算底稿查阅权限')
    return [e for e in db.bom_list_entries(CFG.get('source','sample'),include_superseded=True)
            if str(e.get('erp_code'))==code and e.get('status') not in ('已作废','作废','已被替换')
            and (approved if e.get('status') in ('初审','已审核','已定稿') else draft)]


@router.get('/standards')
def standard_candidates(request:Request,org:str,code:str=Query(max_length=80)):
    authorized(request,org)
    return {'rows':[standard.metadata(e) for e in visible_standards(request,code)]}


@router.get('/standard-comparison')
def standard_comparison(request:Request,org:str,run_id:str,entry_id:int,cc:str=Query(max_length=200),code:str=Query(max_length=80),
                        year:int=Query(ge=2000,le=2100),period:int=Query(ge=1,le=12)):
    trace=trace_product(request,org,run_id,cc,code,year,period)
    entry=next((e for e in visible_standards(request,code) if e['id']==entry_id),None)
    if not entry: raise HTTPException(404,'未找到有权查看的对应产品底稿')
    result=standard.compare(entry,trace['product'],trace['detail'],material_map(org))
    result['mapping']=material_map(org)
    return result


def map_key(org):
    return 'actual_cost_material_map:'+org


def material_map(org):
    """人工设定的匹配关系，按账簿存（物料的单位换算、替代关系是物料本身的属性，不随月份和产品变）。"""
    saved=db.get_setting(map_key(org),None)
    saved=saved if isinstance(saved,dict) else {}
    return dict(units=saved.get('units') or {},aliases=saved.get('aliases') or {})


@router.post('/material-map')
def save_material_map(body:dict,request:Request,org:str):
    """设定或撤销：单位换算（kind=unit：code、from、to、factor；factor 留空＝撤销）／替代料（kind=alias：code、to；to 留空＝撤销）。"""
    user=authorized(request,org,'cost_ledger_wh')
    kind,code=body.get('kind'),body.get('code')
    ok=lambda v:isinstance(v,str) and 1<=len(v.strip())<=80 and v==v.strip()
    if kind not in ('unit','alias') or not ok(code): raise HTTPException(400,'设定无效：需要物料编码和类型')
    stamp=dict(by=user['name'],at=datetime.now(timezone.utc).isoformat())
    with service._RUN_LOCK:
        mapping=material_map(org)
        if kind=='unit':
            factor=body.get('factor')
            if factor in (None,''):
                mapping['units'].pop(code,None);summary=f'撤销 {code} 的单位换算'
            else:
                try:value=number(factor)
                except ValueError as exc:raise HTTPException(400,'换算系数不是有效数字') from exc
                if not 0<value<=1e6 or not ok(body.get('from')) or not ok(body.get('to')) or body['from']==body['to']:
                    raise HTTPException(400,'换算设定无效：系数须大于 0，且两个单位不能相同')
                mapping['units'][code]=dict(stamp,**{'from':body['from'],'to':body['to'],'factor':value})
                summary=f"{code}：1 {body['from']} ＝ {value} {body['to']}"
        else:
            target=body.get('to')
            if target in (None,''):
                mapping['aliases'].pop(code,None);summary=f'撤销 {code} 的替代料设定'
            else:
                if not ok(target) or target==code: raise HTTPException(400,'替代料设定无效：目标编码不能为空，也不能是它自己')
                if target in mapping['aliases']: raise HTTPException(400,'目标物料本身已被设成别的料的替代料，不能再接替代料')
                if any(v.get('to')==code for v in mapping['aliases'].values()): raise HTTPException(400,'这颗料已经是别的替代料的目标，不能再把它设成替代料')
                mapping['aliases'][code]=dict(stamp,to=target);summary=f'{code} 视同 {target}（替代料）'
        if len(mapping['units'])+len(mapping['aliases'])>5000: raise HTTPException(400,'设定条数过多')
        db.set_setting(map_key(org),mapping,operator=user['name'])
    db.audit(user['name'],'全成本·物料匹配设定',org,summary)
    return dict(ok=True,mapping=mapping)


def trace_snapshot(org,year,period,run_id):
    latest=(db.get_period_input('actual_cost:'+org,year,period,service.LATEST) or {}).get('payload') or {}
    if latest.get('run_id')!=run_id: raise HTTPException(409,'本期快照已变化，请重新打开明细')
    record=db.get_period_input('actual_cost:'+org,year,period,run_id)
    if not record:raise HTTPException(404,'未找到本期取数快照')
    snapshot=record['payload']
    if snapshot['sources']['org']!=org or snapshot['sources']['period']!=f'{year:04d}-{period:02d}':raise HTTPException(409,'快照期间不符')
    return snapshot


@router.get('/sources')
def trace_sources(request:Request,org:str,run_id:str,kind:Literal['cost','outbound','ledger','materials']='cost',
                  year:int=Query(ge=2000,le=2100),period:int=Query(ge=1,le=12),offset:int=Query(default=0,ge=0),limit:int=Query(default=100,ge=1,le=300),q:str=Query(default='',max_length=200)):
    authorized(request,org)
    snapshot=trace_snapshot(org,year,period,run_id);source=snapshot['sources'];rows=source[kind]
    if kind=='cost':
        fields=['原始行号','成本中心原值','产品编码原值','产品名称原值','工单原值','单据类型','成本项目','费用项目','本期投入金额','完工数量','完工金额','归属车间','归属产品','归属工单']
        found=[];cc=code=wo=''
        for i,r in enumerate(rows,1):
            if r[3]:cc,code,wo=r[0],r[1],r[3]
            found.append(dict(zip(fields,[i,*r,cc,code,wo])))
        rows=found
    elif kind=='materials':rows=list(rows.values())
    fields=list(dict.fromkeys(k for r in rows for k in r))
    filtered=[r for r in rows if q.lower() in ' '.join(str(v or '') for v in r.values()).lower()] if q else rows
    return dict(org=org,period=source['period'],fetched_at=source['fetched_at'],kind=kind,fields=fields,rows=filtered[offset:offset+limit],total=len(filtered),source_total=len(rows))


@router.get('/product')
def trace_product(request:Request,org:str,run_id:str,cc:str=Query(max_length=200),code:str=Query(max_length=80),
                  year:int=Query(ge=2000,le=2100),period:int=Query(ge=1,le=12)):
    authorized(request,org);snapshot=trace_snapshot(org,year,period,run_id)
    try:product,rows=product_rows(snapshot,cc,code)
    except ValueError as exc:raise HTTPException(404,str(exc)) from exc
    identity={'run_id':run_id,'cc':cc,'code':code};key='ac:t:'+service.fingerprint(identity,{}, {})[:32]
    cached=(db.get_period_input('actual_cost:'+org,year,period,key) or {}).get('payload')
    if cached and cached.get('identity')!=identity:raise HTTPException(409,'产品追溯键冲突')
    detail=(cached or {}).get('detail')
    return dict(product=product,cost_rows=rows,detail=detail,source_time=snapshot['sources']['fetched_at'],
                breakdown=standard.breakdown(product,detail),notes=material_notes(org,year,period,cc,code))


def note_key(cc,code):
    # 差异原因跟「期间 + 产品」走，不跟某一次试算走：重新取数试算后备注还在。
    return 'ac:n:'+service.fingerprint({'cc':cc,'code':code},{}, {})[:32]


def material_notes(org,year,period,cc,code):
    saved=(db.get_period_input('actual_cost:'+org,year,period,note_key(cc,code)) or {}).get('payload') or {}
    return saved.get('notes') or {} if saved.get('identity')=={'cc':cc,'code':code} else {}


@router.post('/material-note')
def save_material_note(body:dict,request:Request,org:str,run_id:str,cc:str=Query(max_length=200),code:str=Query(max_length=80),
                       year:int=Query(ge=2000,le=2100),period:int=Query(ge=1,le=12)):
    """逐料差异原因（人写的说明，如「26 年采购单价降低」）。只是备注，不参与任何计算。"""
    user=authorized(request,org,'cost_ledger_wh');snapshot=trace_snapshot(org,year,period,run_id)
    try:product_rows(snapshot,cc,code)
    except ValueError as exc:raise HTTPException(404,str(exc)) from exc
    material,text=body.get('material'),body.get('text')
    if not isinstance(material,str) or not 1<=len(material)<=120 or not isinstance(text,str) or len(text)>500:
        raise HTTPException(400,'备注无效：物料标识必填，说明不超过500字')
    with service._RUN_LOCK:
        notes=material_notes(org,year,period,cc,code)
        if text.strip():
            notes[material]=dict(text=text.strip(),by=user['name'],at=datetime.now(timezone.utc).isoformat())
        else: notes.pop(material,None)
        if len(notes)>500: raise HTTPException(400,'备注条数过多')
        db.set_period_input('actual_cost:'+org,year,period,note_key(cc,code),dict(identity={'cc':cc,'code':code},notes=notes),operator=user['name'])
    db.audit(user['name'],'全成本·差异原因',f'{org}/{year}-{period:02d}',f'{cc}/{code}/{material}：{text.strip()[:80] or "（清空）"}')
    return dict(ok=True,notes=notes)


@router.post('/product-refresh')
def refresh_product(request:Request,org:str,run_id:str,cc:str=Query(max_length=200),code:str=Query(max_length=80),
                    year:int=Query(ge=2000,le=2100),period:int=Query(ge=1,le=12)):
    user=authorized(request,org,'cost_ledger_fetch');snapshot=trace_snapshot(org,year,period,run_id)
    try:detail=fetch_product_trace(snapshot,cc,code)
    except (ValueError,service.kingdee.KingdeeError) as exc:
        raise HTTPException(409,str(exc) if isinstance(exc,ValueError) else '金蝶明细读取失败，本次未更新；请检查连接或字段') from exc
    trace_snapshot(org,year,period,run_id)  # 取数期间换月/换版本不能挂到另一份快照。
    identity={'run_id':run_id,'cc':cc,'code':code};digest=service.fingerprint(identity,detail,{})
    archive='ac:d:'+digest[:32];index='ac:t:'+service.fingerprint(identity,{}, {})[:32]
    old=(db.get_period_input('actual_cost:'+org,year,period,archive) or {}).get('payload')
    if old and old.get('digest')!=digest:raise HTTPException(409,'明细快照键冲突')
    payload=dict(identity=identity,digest=digest,detail=detail)
    db.set_period_input('actual_cost:'+org,year,period,archive,payload,operator=user['name'])
    db.set_period_input('actual_cost:'+org,year,period,index,payload,operator=user['name'])
    db.audit(user['name'],'全成本·产品追溯取数',f'{org}/{year}-{period:02d}',f'{cc}/{code}；{archive}')
    return dict(ok=True,detail=detail)


@router.post('/confirm-close')
def confirm_close(body:dict, request:Request, org:str, year:int=Query(ge=2000,le=2100), period:int=Query(ge=1,le=12)):
    user=authorized(request,org,'cost_ledger_close')
    authorized(request,org,'cost_ledger_fetch')
    if body.get('confirmed_closed') is not True: raise HTTPException(400,'请明确确认本期已结账，可以出全成本表')
    with service._RUN_LOCK:
        data=inputs(year,period,org)
        if not data: raise HTTPException(409,'请先建立本期配置')
        rules,supplement=data['rules'],data['supplement']
        if body.get('rule_version')!=rules['version']: raise HTTPException(409,'本期依据版本已变化，请重新查看后确认')
        issues=monthly_input_issues(rules,supplement)
        if not rules.get('confirmed'): issues.append('请先核对并保存本期分摊规则')
        if rules['shared_basis']=='supplement' and not supplement.get('confirmed'): issues.append('请先核对本期共享分摊依据')
        if issues: raise HTTPException(409,'；'.join(issues))
        confirmation=dict(id=str(uuid4()),org=org,period=f'{year:04d}-{period:02d}',
            confirmed_by=user['name'],confirmed_at=datetime.now(timezone.utc).isoformat(),
            input_digest=service.fingerprint({},rules,supplement))
        db.set_period_input('actual_cost:'+org,year,period,service.CLOSE,confirmation,operator=user['name'])
        db.audit(user['name'],'全成本·确认已结账',f'{org}/{year}-{period:02d}',confirmation['id'])
        outcome=service.generate(year,period,org,rules,supplement,db,operator=user['name'],close_confirmation=confirmation)
        if outcome['status']=='ready':
            try: service.export_result(year,period,org,outcome['run_id'],db,formal=True)
            except (ValueError,service.kingdee.KingdeeError) as exc:
                outcome.update(status='failed',issues=[str(exc) if isinstance(exc,ValueError) else '取数状态变化，请重新确认出表'])
                db.set_period_input('actual_cost:'+org,year,period,service.LATEST,outcome,operator=user['name'])
                raise HTTPException(409,outcome['issues'][0]) from exc
        outcome=(db.get_period_input('actual_cost:'+org,year,period,service.LATEST) or {})['payload']
    return {'ok':True,'latest':outcome}


@router.post('/revoke-close')
def revoke_close(request:Request, org:str, year:int=Query(ge=2000,le=2100), period:int=Query(ge=1,le=12)):
    user=authorized(request,org,'cost_ledger_close')
    with service._RUN_LOCK:
        namespace='actual_cost:'+org
        previous=(db.get_period_input(namespace,year,period,service.LATEST) or {}).get('payload') or {}
        db.set_period_input(namespace,year,period,service.CLOSE,{},operator=user['name'])
        db.set_period_input(namespace,year,period,service.LATEST,dict(org=org,period=f'{year:04d}-{period:02d}',
            status='reopened',run_id=previous.get('run_id'),issues=['工作台已撤销结账确认，请重新确认后出表']),operator=user['name'])
    db.audit(user['name'],'全成本·撤销结账确认',f'{org}/{year}-{period:02d}','保留历史快照，停止正式导出')
    return {'ok':True}


@router.get('/export')
def export(request:Request, org:str, run_id:str, mode:Literal['trial','formal']='trial',
           year:int=Query(ge=2000,le=2100), period:int=Query(ge=1,le=12)):
    user=authorized(request,org)
    try: data=service.export_result(year,period,org,run_id,db,formal=mode=='formal')
    except (ValueError,service.kingdee.KingdeeError) as exc:
        raise HTTPException(409,str(exc) if isinstance(exc,ValueError) else '无法核实金蝶结账状态，暂不导出') from exc
    label='正式' if mode=='formal' else '试算-待确认'
    filename=f'全成本表_{org}_{year}-{period:02d}_{label}.xlsx'
    db.audit(user['name'],'全成本·导出',f'{org}/{year}-{period:02d}',f'{label}；{run_id}')
    return Response(data,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition':"attachment; filename=actual_cost.xlsx; filename*=UTF-8''"+quote(filename),
                             'Cache-Control':'no-store'})


# 菜单「成本模块 › 全成本溯源」(fullcost) 的准入点，由 app.py NAV_MODULES 自动生成，存量账号默认不给。
# 侧栏状态只挡菜单不挡接口，所以这里每个接口都再判一次准入点 + 动作点（同发票管家的做法）。
NAV_CAP = 'enter:fullcost'


def authorized(request, org, permission='cost_ledger'):
    user = _require_perm(request, 'cost_ledger')
    if not user or not _require_perm(request, permission) or not _require_perm(request, NAV_CAP):
        raise HTTPException(403, '没有对应的全成本查看或操作权限')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,8}',org) or not any(
        o.get('active') and str(o.get('book_code'))==org for o in db.list_orgs()):
        raise HTTPException(400, '主体档案中没有该账簿')
    return user


def inputs(year, period, org):
    saved = db.get_period_input('actual_cost:'+org,year,period,INPUT)
    # 历史核对种子严格绑定主体及月份，不沿用到新月份。
    path = Path(__file__).resolve().parents[1]/f'actual_cost_{org}_{year}_{period:02d}.json'
    data=copy.deepcopy(saved['payload']) if saved else json.loads(path.read_text(encoding='utf8')) if path.is_file() else None
    if data and (data['rules'].get('basis')!='ledger' or data['rules'].get('close_gate')!='manual'):
        data['rules'].update(basis='ledger',close_gate='manual',confirmed=False,version='manual-ledger-'+data['rules']['version'])
        data['rules'].pop('close_confirmation',None)
        data['supplement']['confirmed']=False
    return data


def rule_periods(org):
    """有分摊规则可沿用的月份（新→旧）：工作台保存过的，加随代码带的规则种子。"""
    found={f"{r['year']:04d}-{int(r['period']):02d}" for r in db.list_period_inputs('actual_cost:'+org,INPUT)}
    for path in Path(__file__).resolve().parents[1].glob(f'actual_cost_{org}_20??_??.json'):
        m=re.fullmatch(rf'actual_cost_{re.escape(org)}_(20\d{{2}})_(0[1-9]|1[0-2])\.json',path.name)
        if m: found.add(f'{m[1]}-{m[2]}')
    return sorted(found,reverse=True)


@router.post('/initialize')
def initialize(body:dict, request:Request, org:str, year:int=Query(ge=2000,le=2100), period:int=Query(ge=1,le=12)):
    user=authorized(request,org,'cost_ledger_wh')
    target=f'{year:04d}-{period:02d}'
    source=body.get('source_period')
    # V2.789：补做早月份时没有更早的规则可沿用，放开为「任意一个别的、有规则的月份」。沿用的只是规则结构，
    # 本期金额、比例、试产确认和「已核对」状态一概不带过来（见下）。
    if not isinstance(source,str) or not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])',source) or source==target:
        raise HTTPException(400,'请选择本期以外、已有规则的月份')
    with service._RUN_LOCK:
        if inputs(year,period,org) is not None: raise HTTPException(409,'本期已有配置，不能覆盖')
        y,p=map(int,source.split('-')); previous=inputs(y,p,org)
        if not previous: raise HTTPException(409,'所选月份没有分摊规则')
        rules=copy.deepcopy(previous['rules'])
        if rules.get('org')!=org: raise HTTPException(409,'来源规则账簿不一致')
        if not rules.get('shared_group_weights'):
            last=(db.get_period_input('actual_cost:'+org,y,p,service.LATEST) or {}).get('payload') or {}
            prior=db.get_period_input('actual_cost:'+org,y,p,last['run_id']) if last.get('run_id') else None
            groups={row['group'] for row in ((prior or {}).get('payload') or {}).get('result',{}).get('products',[])}
            rules['shared_group_weights']={group:{'tea':None,'total':None} for group in sorted(groups)}
        stamp=datetime.now(timezone.utc).isoformat()
        rules.update(effective_from=target,effective_to=target,confirmed=False,version='workbench-'+stamp)
        supplement=dict(org=org,period=target,confirmed=False,shared_tea_ratio=None,
                        reference_pools={cc:{k:None for k in ('water','power','depreciation','rent')} for cc in CENTRES},
                        note='',provenance={'rules':f'沿用{source}规则结构，本期尚未确认；本期比例和金额须重新填写'})
        data=dict(rules=rules,supplement=supplement,updated_by=user['name'],updated_at=stamp,
                  inherited_from={'period':source,'version':previous['rules']['version']})
        db.set_period_input('actual_cost:'+org,year,period,INPUT,data,operator=user['name'])
    db.audit(user['name'],'全成本·建立本期配置',f'{org}/{target}',f'规则来源{source}；金额与确认状态不沿用')
    return {'ok':True}


@router.get('/state')
def state(request:Request, org:str, year:int=Query(ge=2000,le=2100), period:int=Query(ge=1,le=12)):
    authorized(request,org)
    with service._RUN_LOCK:
        namespace='actual_cost:'+org
        latest=(db.get_period_input(namespace,year,period,service.LATEST) or {}).get('payload') or {'status':'not_generated','issues':[]}
        latest=copy.deepcopy(latest)
        stored=db.get_period_input(namespace,year,period,latest['run_id']) if latest.get('run_id') else None
        snapshot=(stored or {}).get('payload')
        if latest['status']=='ready':
            try: service.current_result(year,period,org,db)
            except (ValueError,service.kingdee.KingdeeError) as exc:
                latest.update(status='unverified',issues=[str(exc) if isinstance(exc,ValueError) else '暂时无法核实金蝶结账状态'])
        data=inputs(year,period,org)
        if snapshot and data:
            prior_rules={k:v for k,v in snapshot['rules'].items() if k!='close_confirmation'}
            if latest['status'] in ('ready','needs_confirmation') and (prior_rules!=data['rules'] or snapshot['supplement']!=data['supplement']):
                latest.update(status='needs_inputs',issues=['本期口径或依据已更新，请重新取数校验'])
        if data is None: latest['issues']=list(latest.get('issues',[]))+['本期未配置分摊规则与补充依据']
        else: latest['issues']=list(dict.fromkeys(list(latest.get('issues',[]))+monthly_input_issues(data['rules'],data['supplement'])))
        result=(snapshot or {}).get('result')
        return dict(ok=True,org=org,period=f'{year:04d}-{period:02d}',latest=latest,inputs=data,
            rule_periods=rule_periods(org),expense_targets=list(EDITABLE_EXPENSE_TARGETS),
            result=result,source_time=(snapshot or {}).get('sources',{}).get('fetched_at'),
            historical=bool(snapshot and latest['status'] not in ('ready','needs_confirmation')),
            trigger='manual',close_confirmation=(db.get_period_input(namespace,year,period,service.CLOSE) or {}).get('payload'))


@router.post('/generate')
def generate(request:Request, org:str, year:int=Query(ge=2000,le=2100), period:int=Query(ge=1,le=12)):
    user=authorized(request,org,'cost_ledger_fetch')
    with service._RUN_LOCK:
        data=inputs(year,period,org) or {}
        db.set_period_input('actual_cost:'+org,year,period,service.CLOSE,{},operator=user['name'])
        outcome=service.generate(year,period,org,data.get('rules'),data.get('supplement'),db,operator=user['name'])
    db.audit(user['name'],'全成本·取数试算',f'{org}/{year}-{period:02d}',outcome['status'])
    return {'ok':True,'latest':outcome}


@router.post('/inputs')
def save_inputs(body:dict, request:Request, org:str, year:int=Query(ge=2000,le=2100), period:int=Query(ge=1,le=12)):
    user=authorized(request,org,'cost_ledger_wh')
    if not isinstance(body.get('confirmed',False),bool): raise HTTPException(400,'确认标志无效')
    if body.get('confirmed'): authorized(request,org,'cost_ledger_close')
    with service._RUN_LOCK:
        data=inputs(year,period,org)
        if data is None: raise HTTPException(409,'本期尚未建立基础分摊规则，不能沿用其他月份金额')
        rules,supplement=data['rules'],data['supplement']
        choices={'basis':('ledger',),'close_gate':('manual',),
                 'wip_policy':('reverse_addback','exclude'),'unallocated_wip':('separate','block'),
                 'shared_basis':('supplement','completed_quantity')}
        try:
            for field,allowed in choices.items():
                if field in body:
                    if body[field] not in allowed: raise ValueError('无效口径：'+field)
                    rules[field]=body[field]
            if 'shared_tea_ratio' in body:
                ratio=number(body['shared_tea_ratio'])
                if not 0<=ratio<=1: raise ValueError('小料共享比例应在0%至100%之间')
                supplement['shared_tea_ratio']=ratio
            if 'reference_pools' in body and rules['basis']=='reference':
                pool=body['reference_pools']
                if not isinstance(pool,dict) or set(pool)!=set(CENTRES): raise ValueError('费用池车间不完整')
                for cc in CENTRES:
                    if not isinstance(pool[cc],dict) or set(pool[cc])!={'water','power','depreciation','rent'}:
                        raise ValueError('费用池项目不完整')
                supplement['reference_pools']={cc:{k:number(v) for k,v in pool[cc].items()} for cc in CENTRES}
            if 'rent_plant_ratio' in body:
                ratio=number(body['rent_plant_ratio'])
                if not 0<=ratio<=1: raise ValueError('厂房租金植物肉比例应在0%至100%之间')
                rules['rent_plant_ratio']=ratio
            if 'shared_group_weights' in body:
                weights=body['shared_group_weights']
                if not isinstance(weights,dict) or not weights or len(weights)>500: raise ValueError('产品分组权重无效')
                parsed={}
                for group,weight in weights.items():
                    if not isinstance(group,str) or not 1<=len(group)<=200 or not isinstance(weight,dict): raise ValueError('产品分组权重无效')
                    tea,total=number(weight.get('tea')),number(weight.get('total'))
                    if not 0<=tea<=total<=1: raise ValueError('共享产量权重须满足0≤小料系数≤总量系数≤1')
                    parsed[group]={'tea':tea,'total':total}
                rules['shared_group_weights']=parsed
            if 'shared_centre_weights' in body:
                centres=body['shared_centre_weights']
                if not isinstance(centres,dict) or not centres or len(centres)>100: raise ValueError('共享产量车间范围无效')
                if any(not isinstance(cc,str) or not 1<=len(cc)<=200 or number(v) not in (0,1) for cc,v in centres.items()):
                    raise ValueError('请明确各车间是否计入共享产量')
                rules['shared_centre_weights']={cc:number(v) for cc,v in centres.items()}
            if 'expense_map' in body:
                mapping=body['expense_map'];current=rules['expense_map']
                if not isinstance(mapping,dict) or not mapping or len(mapping)>500: raise ValueError('费用项目归类无效')
                for name,target_field in mapping.items():
                    if not isinstance(name,str) or not 1<=len(name.strip())<=100 or name!=name.strip(): raise ValueError('费用项目名称无效')
                    if current.get(name)==target_field: continue
                    # 水电、租金、氮气要和凭证口径逐项对上，改了会对不平，页面不开放；只允许归到可自行归类的去向
                    if name in current and current[name] not in EDITABLE_EXPENSE_TARGETS: raise ValueError(f'「{name}」是固定归类，不能在页面修改')
                    if target_field not in EDITABLE_EXPENSE_TARGETS: raise ValueError(f'「{name}」的归类去向无效')
                fixed={k for k,v in current.items() if v not in EDITABLE_EXPENSE_TARGETS}
                if fixed-set(mapping): raise ValueError('固定归类的费用项目不能删除')
                rules['expense_map']=dict(mapping)
            if 'trial_shared_decisions' in body:
                decisions=body['trial_shared_decisions']
                if not isinstance(decisions,dict) or len(decisions)>10000: raise ValueError('试产工单确认列表无效')
                supplement['trial_shared_decisions']=decisions
            missing=monthly_input_issues(rules,supplement)
            if missing: raise ValueError('；'.join(missing))
            note=body.get('note','')
            if not isinstance(note,str) or not 5<=len(note.strip())<=1000: raise ValueError('请填写5至1000字的依据说明')
        except ValueError as exc: raise HTTPException(400,str(exc)) from exc
        stamp=datetime.now(timezone.utc).isoformat()
        rules.update(confirmed=body.get('confirmed',False),version='workbench-'+stamp)
        supplement.update(confirmed=body.get('confirmed',False),note=note.strip())
        data.update(updated_by=user['name'],updated_at=stamp)
        namespace='actual_cost:'+org
        # 先撤销当前可发布状态；即使后续保存失败，也不能显示旧版为新版成功。
        old=(db.get_period_input(namespace,year,period,service.LATEST) or {}).get('payload') or {}
        db.set_period_input(namespace,year,period,service.LATEST,
            dict(org=org,period=f'{year:04d}-{period:02d}',status='needs_inputs',run_id=old.get('run_id'),
                 issues=['本期依据已修改，请重新取数校验']),operator=user['name'])
        db.set_period_input(namespace,year,period,INPUT,data,operator=user['name'])
        db.set_period_input(namespace,year,period,service.CLOSE,{},operator=user['name'])
    db.audit(user['name'],'全成本·保存依据',f'{org}/{year}-{period:02d}',f"确认={rules['confirmed']}；{note.strip()}")
    return {'ok':True}
