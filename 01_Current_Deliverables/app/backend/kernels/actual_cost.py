# [Change Log] 2026-10-02 | Codex | V-draft | Product actual-cost calculation
# [Change Log] 2026-10-04 | Claude / c | V2.789 | 委外产品进全成本表（单列「委外」、新增成本项「委外加工费」，不参与厂内分摊）；
#              没归类的费用项目、没配系数的产品分组一次报全（带名单），供页面直接补。
import math
import re
from collections import defaultdict

CENTRES = ("植物肉车间", "小料车间")
COST_FIELDS = ("material", "packaging", "labor", "indirect", "water", "power", "gas", "depreciation", "rent", "other", "subcontract", "wip")
# 委外（V2.789，用户定）：有成本的委外工单进全成本表，车间记「委外」；成本＝直接材料＋委外加工费。
# 不在厂内生产，所以不分摊厂内水电/折旧/租金/共享领用，也不计入共享分摊产量。金额为零的委外工单照旧不列。
OUTSOURCED = "委外"
OUTSOURCED_ITEMS = {'直接材料':'material', '间接材料':'material', '委外加工费':'subcontract'}
# 页面上允许自己归类的去向；水电/租金/氮气三类要和凭证口径逐项对上，只能改规则文件。
EDITABLE_EXPENSE_TARGETS = ('other', 'indirect', 'gas', 'gold_depreciation')


class MissingConfig(ValueError):
    """缺配置：带上缺的名单（kind='expense' 费用项目 / 'group' 产品分组），页面据此让人直接补。"""
    def __init__(self, kind, names, message):
        super().__init__(message); self.kind, self.names = kind, list(names)


def is_outsourced(wo, bill_type):
    return str(wo or '').strip().upper().startswith('SUB') or '委外' in str(bill_type or '')


def number(value):
    """金蝶金额可含千分位；空金额只在报表非金额层级调用方显式补零。"""
    if value is None or isinstance(value, bool): raise ValueError('缺少有效金额')
    try: result = float(str(value).replace(',', ''))
    except (TypeError, ValueError): raise ValueError('金额不是有效数字') from None
    if not math.isfinite(result): raise ValueError('金额不是有限数字')
    return result


def reconcile(a, b, label, tolerance=.00002):
    if abs(number(a)-number(b)) > tolerance: raise ValueError(f'{label}不平：{a} / {b}')


def trial_workorders(sources):
    """按工单类型或试产中心识别；不以物料名称猜测，包含试产中心的普通生产单。"""
    orders=[]
    for index,row in enumerate(sources['cost'],1):
        if not row[3] or (row[0]!='试产中心' and not any(t in str(row[4]) for t in ('中试','试产'))): continue
        if is_outsourced(row[3],row[4]): continue
        cc,code,name,wo,bill_type=[str(v or '').strip() for v in row[:5]]
        orders.append(dict(key='|'.join((cc,code,wo)),cc=cc,code=code,name=name,wo=wo,bill_type=bill_type,
                           source_row=index,qty=0 if row[8] in ('',None) else number(row[8]),
                           gold_total=0 if row[9] in ('',None) else number(row[9])))
    if len({o['key'] for o in orders})!=len(orders): raise ValueError('试产工单键重复，需核对来源层级')
    return orders


def monthly_input_issues(rules, supplement):
    issues=[]
    decisions=supplement.get('trial_shared_decisions',{})
    if not isinstance(decisions,dict) or any(not isinstance(k,str) or not 1<=len(k)<=400 or v not in ('pending','include','exclude') for k,v in decisions.items()):
        issues.append('试产工单共享确认值无效')
    if rules.get('shared_basis')=='supplement':
        try:
            if not 0<=number(supplement.get('shared_tea_ratio'))<=1: raise ValueError()
        except ValueError: issues.append('请补充本期共享领用小料比例（0%至100%）')
    elif rules.get('shared_basis')=='completed_quantity':
        weights=rules.get('shared_group_weights') or {}
        if not weights: issues.append('请配置产品分组的共享产量权重')
        for group,weight in weights.items():
            try:
                if not isinstance(weight,dict) or not 0<=number(weight.get('tea'))<=number(weight.get('total'))<=1: raise ValueError()
            except ValueError: issues.append(f'请配置{group}的共享权重：0≤小料系数≤总量系数≤1')
        centres=rules.get('shared_centre_weights')
        if centres is not None:
            if not isinstance(centres,dict) or not centres: issues.append('请配置共享产量的车间范围')
            else:
                for cc,weight in centres.items():
                    try:
                        if number(weight) not in (0,1): raise ValueError()
                    except ValueError: issues.append(f'请明确{cc}是否计入共享产量')
    if rules.get('basis')=='reference':
        pools=supplement.get('reference_pools') or {}
        for cc in CENTRES:
            for field,label in [('water','水费'),('power','电费'),('depreciation','折旧摊销'),('rent','租金')]:
                try: number(pools.get(cc,{}).get(field))
                except ValueError: issues.append(f'请补充本期{cc}{label}金额')
    return issues


def prepare(sources, rules, supplement):
    """仅源数据与显式规则进入计算；参考原表结果不在此接口中。"""
    period, org = sources['period'], sources['org']
    if not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])', period): raise ValueError('期间无效')
    if rules['org'] != org or supplement['org'] != org or supplement['period'] != period:
        raise ValueError('规则/补充依据与来源的组织或期间不一致')
    if not rules['effective_from'] <= period <= (rules.get('effective_to') or '2099-12'):
        raise ValueError('规则不在生效期间内')
    if rules['basis'] not in ('reference','ledger'): raise ValueError('需明确费用金额口径')
    if rules['wip_policy'] not in ('reverse_addback','exclude'): raise ValueError('需明确在产品调整方向')
    if rules['unallocated_wip'] not in ('separate','block'): raise ValueError('需明确无产出调整处理')
    if rules['shared_basis'] not in ('supplement','completed_quantity'): raise ValueError('需明确共享分摊依据')
    if rules.get('close_gate') not in ('ledger','both','manual'): raise ValueError('需明确出表结账节点')
    missing=monthly_input_issues(rules,supplement)
    if missing: raise ValueError('；'.join(missing))
    material_fields = {'直接材料':'material', '间接材料':'material','直接人工':'labor'}
    grouped, items = defaultdict(lambda:defaultdict(float)), defaultdict(lambda:defaultdict(float))
    expenses, names = defaultdict(lambda:defaultdict(float)), {}
    key = None; item = ''; outsourced = False; skipped = False; unmapped = []
    for row in sources['cost']:
        if not isinstance(row,list) or len(row) != 10: raise ValueError('成本报表结构变化')
        # 此报表零完工金额/数量返回空字符串；保留该已验证的来源语义。
        amount = 0 if row[9] in ('',None) else number(row[9])
        if row[3]:
            cc,code,name,wo,bt = [str(x or '').strip() for x in row[:5]]
            item=''; outsourced=is_outsourced(wo,bt)
            skipped = outsourced and amount == 0     # 金额为零的委外工单不列（8 月即此情形，保持原结果）
            if skipped: key = None; continue
            if outsourced: cc = OUTSOURCED
            key = (cc,code)
            if not cc or not code: raise ValueError('成本工单缺少成本中心或产品编码')
            names[key] = name; grouped[key]['qty'] += 0 if row[8] in ('',None) else number(row[8]); grouped[key]['gold_total'] += amount
        elif skipped:
            if amount != 0: raise ValueError('零成本委外工单下出现非零明细，需核对成本报表')
        elif outsourced:
            if row[5]:
                item = str(row[5]); items[key][item] += amount
                if item in OUTSOURCED_ITEMS: grouped[key][OUTSOURCED_ITEMS[item]] += amount
                elif amount: raise ValueError(f'委外工单出现未配置成本项目：{item}')
        else:
            if key is None: raise ValueError('成本明细缺少父工单')
            if row[5]:
                item = str(row[5]); items[key][item] += amount
                if item in material_fields: grouped[key][material_fields[item]] += amount
                elif item != '制造费用': raise ValueError(f'未配置成本项目：{item}')
            elif row[6] and item == '制造费用':
                name = str(row[6]); amt = amount; expenses[key][name] += amt
                if name not in rules['expense_map']:
                    if name not in unmapped: unmapped.append(name)
                    continue
                grouped[key][rules['expense_map'][name]] += amt
                if name == rules['oil_expense']: grouped[key]['oil'] += amt
    if unmapped: raise MissingConfig('expense',unmapped,'未配置费用项目：'+'、'.join(unmapped))
    products = []
    for key,p in grouped.items():
        attrs = sources['materials'].get(key[1])
        if not attrs or attrs['unit'] != '千克': raise ValueError(f'{key}缺少千克基本单位档案')
        reconcile(sum(items[key].values()),p['gold_total'],f'{key}成本项目合计')
        reconcile(sum(expenses[key].values()),items[key]['制造费用'],f'{key}制造费用明细')
        products.append(dict(cc=key[0],code=key[1],name=names[key],spec=attrs['spec'],group=attrs['group'],
            **{k:p[k] for k in ('qty','gold_total','material','labor','indirect','gas','oil','other',
                               'gold_utilities','gold_depreciation','gold_rent','subcontract')},packaging=0))
    if not products: raise ValueError('本期无产品成本数据')
    qty = {cc:sum(p['qty'] for p in products if p['cc']==cc) for cc in CENTRES}
    shared, selected, excluded_outbound = 0, [], []
    for row in sources['outbound']:
        if str(row['org']) != org or str(row['date'])[:7] != period: raise ValueError('其他出库组织或日期不一致')
        if row['category_name'] not in rules['shared_categories']: continue
        if row['status'] != 'C' or row['cancel'] != 'A':
            excluded_outbound.append({'bill':row['bill'],'entry_id':row['entry_id'],'reason':'未审核或已作废'})
            continue
        if row['direction'] != 'GENERAL': raise ValueError('存在非普通方向其他出库，需确认冲减方式')
        selected.append(row)
        if row['dept'] != rules['tea_department']: shared += number(row['amount'])
    if excluded_outbound: raise ValueError('本期分摊类别存在未审核或作废出库，需核对取数范围')
    trials=trial_workorders(sources)
    decisions=supplement.get('trial_shared_decisions',{})
    if set(decisions)-{o['key'] for o in trials}: raise ValueError('试产确认包含本期来源不存在的工单，请重新核对')
    for order in trials:
        order['decision']=decisions.get(order['key'],'pending')
        order['baseline_weight']=number((rules.get('shared_centre_weights') or {}).get(order['cc'],1))
        order['effective_weight']={'include':1,'exclude':0}.get(order['decision'],order['baseline_weight'])
    shared_quantity=None
    if rules['shared_basis'] == 'supplement':
        ratio = number(supplement['shared_tea_ratio'])
    else:
        weights = rules['shared_group_weights']
        centres=rules.get('shared_centre_weights')
        house=[p for p in products if p['cc']!=OUTSOURCED]   # 委外不在厂内生产，不进共享分摊产量
        if centres is not None and any(p['cc'] not in centres for p in house):
            raise ValueError('新增成本中心缺少共享产量范围配置')
        missing_groups=sorted({p['group'] for p in house if p['group'] not in weights})
        if missing_groups: raise MissingConfig('group',missing_groups,'新增产品分组缺少共享分摊权重：'+'、'.join(missing_groups))
        included=[p for p in house if centres is None or number(centres[p['cc']])==1]
        numerator = sum(p['qty']*number(weights[p['group']]['tea']) for p in included)
        denominator = sum(p['qty']*number(weights[p['group']]['total']) for p in included)
        for order in trials:
            weight=weights[sources['materials'][order['code']]['group']]
            adjustment=order['qty']*(order['effective_weight']-order['baseline_weight'])
            numerator+=adjustment*number(weight['tea'])
            denominator+=adjustment*number(weight['total'])
        if denominator <= 0: raise ValueError('共享分摊没有有效产量')
        ratio = numerator/denominator
        shared_quantity=dict(tea=numerator,total=denominator)
    ledger = defaultdict(float); wip=defaultdict(float); wip_detail=[]; utility_rows=0
    for row in sources['ledger']:
        if str(row['org']) != org or f"{int(row['year']):04d}-{int(row['period']):02d}" != period:
            raise ValueError('凭证期间或组织不一致')
        if row['status'] != 'C': raise ValueError('存在未审核成本凭证')
        debit = number(row['debit']); credit = number(row['credit'])
        if str(row['account']).startswith('5001'):
            m = re.fullmatch(r'期末在产品成本调整产品编码([A-Za-z0-9]+)',row['note'])
            if not m or row['dept'] not in rules['wip_departments'] or credit:
                raise ValueError('在产品调整的产品、部门或借贷方向需要确认')
            cc=rules['wip_departments'][row['dept']];code=m[1]
            wip[cc+'|'+code] += -debit if rules['wip_policy']=='reverse_addback' else 0
            wip_detail.append(dict(cc=cc,code=code,voucher=row['voucher'],entry_id=row['entry_id'],ledger=debit))
        elif str(row['account']).startswith('5101') and debit:
            name=row['expense'];dept=row['dept']
            ledger[name] += debit;ledger[(dept,name)] += debit
            if name=='水电费':
                tokens=[x for x in ('水费','电费') if x in row['note']]
                if len(tokens)!=1: raise ValueError('水电凭证摘要分类不明确')
                utility_rows+=1;ledger[tokens[0]]+=debit
                if '光伏电费' in row['note']: ledger[(dept,'光伏电费')]+=debit
    if sum(p['gold_utilities'] for p in products) and not utility_rows: raise ValueError('未取得水电拆分凭证')
    reconcile(ledger['水费']+ledger['电费'],sum(p['gold_utilities'] for p in products),'水电凭证与产品成本',.01)
    pools={}
    rent_ratio=number(rules['rent_plant_ratio'])
    if not 0<=rent_ratio<=1: raise ValueError('厂房租金比例无效')
    if rules['basis']=='reference':
        pools=supplement['reference_pools']
        for cc in CENTRES:
            for f in ('water','power','depreciation','rent'): number(pools[cc][f])
    else:
        if sum(qty.values())<=0: raise ValueError('无有效完工数量')
        solar_depts=rules['solar_departments']
        solar={cc:ledger[(solar_depts[cc],'光伏电费')] for cc in CENTRES}
        for i,cc in enumerate(CENTRES):
            pools[cc]=dict(water=ledger['水费']*qty[cc]/sum(qty.values()),
                power=(ledger['电费']-sum(solar.values()))*qty[cc]/sum(qty.values())+solar[cc],
                depreciation=sum(p['gold_depreciation'] for p in products if p['cc']==cc),
                rent=(ledger['办公场所租赁费']+ledger['物业管理费'])*(rent_ratio if i==0 else 1-rent_ratio)
                    +ledger[(rules['dorm_departments'][cc],'员工宿舍')])
        reconcile(sum(x['rent'] for x in pools.values()),sum(p['gold_rent'] for p in products),'租金费用归属',.01)
    inputs=dict(period=period,org=org,sources={'period':period,'fetched_at':sources['fetched_at']},
                rules={'period':period,'shared_tea_ratio':ratio},products=products,pools=pools,
                shared_amount=shared,nitrogen=ledger['车间燃料费'],wip=dict(wip))
    result=calculate(inputs)
    allocated_keys={p['cc']+'|'+p['code'] for p in products if p['qty']}
    unallocated=[{'key':k,'amount':v} for k,v in wip.items() if k not in allocated_keys and v]
    issues=[]
    pending_trials=[o for o in trials if o['qty'] and o['decision']=='pending']
    if rules['shared_basis']=='completed_quantity' and pending_trials:
        issues.append(f'{len(pending_trials)}张有产量试产工单的共享分摊范围待确认；当前仅按车间范围试算')
    if rules['close_gate']=='both': issues.append('存货核算结账接口尚未验证，当前仅可试算')
    if rules['close_gate']=='manual':
        confirmation=rules.get('close_confirmation') or {}
        if confirmation.get('org')!=org or confirmation.get('period')!=period or not confirmation.get('confirmed_by'):
            issues.append('待在工作台确认已结账后正式出表')
    if not rules.get('confirmed'): issues.append('分摊规则尚未确认')
    if rules['shared_basis']=='supplement' and not supplement.get('confirmed'): issues.append('本期共享分摊依据尚未确认')
    if rules['basis']=='reference' and not supplement.get('confirmed'): issues.append('本期台账费用依据尚未确认')
    if unallocated and rules['unallocated_wip']=='block': issues.append('有无产出在产品调整待处理')
    if rules['close_gate']!='manual' and sources.get('book_current_period','')<=period: issues.append('金蝶账簿尚未进入下一期间')
    gold_total=sum(p['gold_total'] for p in products);total=sum(p['total'] for p in result)
    wip_total=sum(p['wip'] for p in result)
    source_difference=sum(sum(x.values()) for x in pools.values())-sum(p['gold_utilities']+p['gold_depreciation']+p['gold_rent'] for p in products)
    reconcile(total,gold_total+wip_total+source_difference,'全成本与金蝶成本桥接')
    if rules['basis']=='ledger': reconcile(source_difference,0,'金蝶金额口径不得增减费用',.01)
    return dict(period=period,org=org,inputs=inputs,products=result,rule_version=rules['version'],
        ready=not issues,issues=issues,unallocated_wip=unallocated,wip_detail=wip_detail,trial_orders=trials,
        controls=dict(gold_total=gold_total,total=total,wip_allocated=wip_total,source_difference=source_difference,
                      selected_outbound=len(selected),shared_amount=shared,shared_ratio=ratio,shared_quantity=shared_quantity,
                      outsourced_total=sum(p['total'] for p in result if p['cc']==OUTSOURCED)))

def calculate(inputs):
    """No original product results enter the calculation. Inputs must identify their period."""
    period = inputs['period']
    if any(inputs[x]['period'] != period for x in ('rules', 'sources')):
        raise ValueError('期间不一致，不能沿用其他月份数据')
    rules, pools = inputs['rules'], inputs['pools']
    if not 0 <= rules['shared_tea_ratio'] <= 1:
        raise ValueError('共享分摊比例必须在0和1之间')
    result, qty, keys = [], defaultdict(float), set()
    for p in inputs['products']:
        key = (p['cc'], p['code'])
        if key in keys:
            raise ValueError(f'重复产品键: {key}')
        keys.add(key)
        for field in ('qty','material','packaging','labor','indirect','gas','oil','other',
                      'gold_utilities','gold_depreciation','gold_rent','subcontract'):
            if not isinstance(p[field], (int,float)) or not math.isfinite(p[field]):
                raise ValueError(f'{key}缺少有效数值: {field}')
        if p['cc'] not in CENTRES and any(p[k] for k in ('gold_utilities','gold_depreciation','gold_rent')):
            raise ValueError(f'{key}出现新费用归属，需要新增分摊规则')
        if p['qty'] < 0:
            raise ValueError(f'负完工数量需单独处理: {key}')
        if p['cc'] == OUTSOURCED and any(p[k] for k in ('labor','indirect','gas','oil','other')):
            raise ValueError(f'{key}委外产品出现厂内人工或制造费用，需明确口径')
        if p['cc'] != OUTSOURCED and p['subcontract']:
            raise ValueError(f'{key}自制产品出现委外加工费，需明确口径')
        if p['qty'] == 0 and any(p[k] for k in ('material','packaging','labor','indirect','gas','subcontract')):
            raise ValueError(f'{key}有直接费用但无完工数量，需核对成本报表')
        qty[p['cc']] += p['qty']
    transfer = inputs['shared_amount'] * rules['shared_tea_ratio']
    centre_other = {cc: sum(p['other'] - p['oil'] for p in inputs['products'] if p['cc'] == cc)
                    + (-transfer if cc == CENTRES[0] else transfer) for cc in CENTRES}
    for cc in CENTRES:
        if qty[cc] == 0 and (centre_other[cc] or any(pools[cc].values()) or
                            (cc == CENTRES[0] and inputs['nitrogen'])):
            raise ValueError(f'{cc}有费用但无产量，需明确费用去向')
    for p in inputs['products']:
        r = dict(p)
        share = p['qty'] / qty[p['cc']] if qty[p['cc']] else 0
        r['material'] = p['material'] + p['oil']
        for field in ('water', 'power', 'depreciation', 'rent'):
            r[field] = pools[p['cc']][field] * share if p['cc'] in CENTRES else 0
        r['gas'] = p['gas'] + (inputs['nitrogen'] * share if p['cc'] == CENTRES[0] else 0)
        r['other'] = centre_other[p['cc']] * share if p['cc'] in CENTRES else p['other'] - p['oil']
        r['wip'] = inputs['wip'].get(p['cc'] + '|' + p['code'], 0) if p['qty'] else 0
        r['total'] = sum(r[k] for k in COST_FIELDS)
        r['unit'] = r['total'] / r['qty'] if r['qty'] else None
        result.append(r)
    return result
