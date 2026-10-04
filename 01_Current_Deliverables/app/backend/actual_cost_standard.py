# [Change Log] 2026-10-03 | Codex | V-draft | Accounting worksheet comparison, read-only
# [Change Log] 2026-10-04 | Claude / c | V2.791 | 直接材料拆分：没有底稿也能按子项物料看每公斤单耗/单价/成本（breakdown）；
#              底稿对比行补用量/单价/整体三个差异率、成本占比、含税口径（按底稿自身的含税÷不含税倍率折算）与合计。
# [Change Log] 2026-10-04 | Claude / c | V2.792 | 人工设定的匹配关系：单位换算（1 个实际单位＝多少底稿单位）与替代料（实际编码并到底稿编码）。
#              系统不猜系数、不猜替代关系；设了才并，行上写明并了什么，随时可撤。
# [Change Log] 2026-10-04 | Claude / c | V2.793 | 加「生产 BOM」一层：各工单用料清单按本期完工量折成每公斤标准单耗，
#              把用量差异拆成「报价→BOM（配方/报价假设）」和「BOM→实际（生产执行）」两段；BOM 只有用量、没有价格。
from collections import defaultdict
import hashlib
import json
import math


def numeric(v):
    if v is None or isinstance(v, bool): return None
    try:
        n = float(v)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError): return None


def unit(v):
    s = str(v or '').strip().lower()
    return {'kg':'kg','千克':'kg','公斤':'kg','pcs':'pcs','个':'pcs'}.get(s, s)


def metadata(e):
    return {k:e.get(k) for k in ('id','cp_code','erp_code','product_name','calc_date','status','src_file','provenance_note')}


def rate(actual, standard):
    """差异率＝（实际−底稿）÷底稿；底稿为零或缺值不算。"""
    return (actual-standard)/standard if actual is not None and standard not in (None, 0) else None


def actual_materials(detail):
    """本期净领补退料按（物料编码，单位）汇总。"""
    actual = defaultdict(lambda:{'qty':0.,'amount':0.,'name':'','bills':set(),'valid':True})
    for m in (detail or {}).get('movements',[]):
        k=(str(m.get('code') or '').strip(),unit(m.get('unit')))
        a=actual[k];q=numeric(m.get('net_qty'));v=numeric(m.get('net_amount'))
        a['valid'] &= q is not None and v is not None
        a['qty'] += q or 0.;a['amount'] += v or 0.;a['name']=m.get('name');a['bills'].add(m.get('bill',''))
    return actual


def purchase_tax(detail):
    """各物料本期最近一张已审核应付单的税率（小数）。只用于把实际成本折成含税口径，没有记录就不折。"""
    found={}
    for p in sorted((detail or {}).get('prices',[]),key=lambda p:str(p.get('date') or '')):
        r=numeric(p.get('tax_rate'))
        if r is not None and 0<=r<100: found[str(p.get('code') or '').strip()]=r/100
    return found


def resolver(std, mapping):
    """人工设定的匹配怎么落到一颗料上：返回 resolve(编码, 单位) → (并到的编码, 单位, 数量系数, 用了哪几种设定)。
    替代料：这个编码在底稿里没有、而设定的目标编码在底稿里有，才并过去；
    单位换算：这个单位在底稿该编码下没有、而换算后的单位有，才换；
    并过去之后单位仍对不上的，一律不并（保持原样，照旧提示）。"""
    units=(mapping or {}).get('units') or {};aliases=(mapping or {}).get('aliases') or {}
    std_units=defaultdict(set)
    for code,u in std: std_units[code].add(u)
    def resolve(code,u):
        target,tu,factor,how=code,u,1.,[]
        alias=aliases.get(code) or {}
        if code not in std_units and alias.get('to') in std_units: target=alias['to'];how.append('替代料')
        if tu not in std_units.get(target,()):
            conv=units.get(code) or {};f=numeric(conv.get('factor'))
            if conv.get('from')==u and conv.get('to') in std_units.get(target,()) and f and f>0:
                tu,factor=conv['to'],f;how.append('单位换算')
        if how and tu not in std_units.get(target,()): return code,u,1.,[]
        return target,tu,factor,how
    return resolve


def bom_usage(detail, std=(), mapping=None):
    """生产 BOM 标准用量（未除完工量）：产品追溯里已按「工单用料清单 ÷ 用料单产品数量 × 本期完工量」逐工单折好，这里按物料汇总。
    同一张工单里主料和它的替代料是二选一（金蝶用料清单里两行各列足量，实测如此），设了替代料后每张工单取较大的一行，不相加。
    某张工单的用料清单不满足折算条件（固定用量/固定损耗、单位非千克、未审核、一单多版本）时该料整体不出数，不拿残缺的数去比。"""
    resolve=resolver(std,mapping);per={}
    for m in (detail or {}).get('materials',[]):
        if not m.get('bom'): continue                      # 这张工单的用料清单里没有这颗料
        code=str(m.get('code') or '').strip();target,tu,factor,_=resolve(code,unit(m.get('unit')));q=numeric(m.get('standard_qty'))
        slot=per.setdefault((m.get('wo',''),target,tu),{'qty':0.,'valid':True,'name':''})
        slot['valid']&=q is not None;slot['qty']=max(slot['qty'],(q or 0.)*factor)
        if code==target or not slot['name']: slot['name']=m.get('name')
    found={}
    for (wo,target,tu),slot in per.items():
        b=found.setdefault((target,tu),{'qty':0.,'name':slot['name'],'bills':set(),'valid':True})
        b['valid']&=slot['valid'];b['qty']+=slot['qty'];b['bills'].add(wo)
    return found


def bom_fields(b, qty, actual_qty, standard_qty=None, has_detail=True):
    """一行里的 BOM 三个数：每公斤标准单耗、报价→BOM（配方）差异率、BOM→实际（生产）差异率。"""
    bq=b['qty']/qty if b and b['valid'] else None
    note=None if bq is not None else '尚未读取实际材料' if not has_detail else '工单用料清单不满足折算条件' if b else '生产BOM里没有'
    return dict(bom_qty=bq,bom_note=note,bom_orders=len(b['bills']) if b else 0,
                design_rate=rate(bq,standard_qty),bom_rate=rate(actual_qty,bq))


def apply_mapping(actual, std, mapping):
    """按人工设定把实际领用并到底稿行（数量×系数相加，金额不变）。规则见 resolver。"""
    resolve=resolver(std,mapping);out={}
    for (code,u),a in actual.items():
        target,tu,factor,how=resolve(code,u)
        o=out.setdefault((target,tu),{'qty':0.,'amount':0.,'name':'','bills':set(),'valid':True,'merged':[]})
        o['valid']&=a['valid'];o['qty']+=a['qty']*factor;o['amount']+=a['amount'];o['bills']|=a['bills']
        if not how or not o['name']: o['name']=a['name']
        if how: o['merged'].append(dict(code=code,name=a['name'],unit=u,qty=a['qty'],amount=a['amount'],
                                        kind='、'.join(how),alias='替代料' in how,factor=factor if '单位换算' in how else None,to_unit=tu))
    return out


def bridge(out, detail, actual, qty):
    """材料投入 → 完工材料的衔接：差额是在产和跨期影响，单列，不摊到物料。"""
    total=sum(a['amount'] for a in actual.values())
    out['actual_material_input_per_kg']=total/qty if detail else None
    complete=(detail or {}).get('controls',{}).get('complete_material')
    out['completed_material_per_kg']=complete/qty if complete is not None else None
    out['material_timing_bridge']=(complete-total)/qty if complete is not None else None
    return total


def finish(out, total, qty):
    """占比、含税合计、整体差异率。"""
    for r in out['rows']:
        r['actual_share']=r['actual_cost']*qty/total if r['actual_cost'] is not None and total else None
    known=[r for r in out['rows'] if r['actual_cost'] is not None]
    out['actual_material_incl']=sum(r['actual_cost_incl'] for r in known if r['actual_cost_incl'] is not None) if known else None
    out['actual_incl_unknown']=sum(1 for r in known if r['actual_cost_incl'] is None)   # 折不成含税的行数（没有税率依据）
    return out


def breakdown(product, detail):
    """没有底稿也能看的直接材料拆分：本期净领用按子项物料汇总，折成每公斤完工产品的单耗、单价、成本。"""
    out={'rows':[],'product_qty':product.get('qty'),'actual_unit_cost':product.get('unit'),'ready':bool(detail)}
    qty=numeric(product.get('qty'))
    if qty is None or qty<=0:
        out.update(ready=False,issue='本期完工量不大于零，不能折成每公斤'); return out
    if not detail: return out
    actual=actual_materials(detail);tax=purchase_tax(detail);bom=bom_usage(detail)
    for k in dict.fromkeys([*actual,*bom]):
        code,u=k;a=actual.get(k);b=bom.get(k)
        aq=a['qty']/qty if a and a['valid'] else None;ac=a['amount']/qty if a and a['valid'] else None
        ap=ac/aq if ac is not None and aq is not None and aq>0 else None
        factor=1+tax[code] if code in tax else None
        if not a: a={'name':b['name'],'qty':None,'amount':None,'bills':set(),'valid':True};status='生产BOM有、本期未领用'
        else: status='实际用料' if a['valid'] else '实际数量或金额缺失'
        out['rows'].append(dict(code=code,name=a['name'],unit=u,section='实际用料',status=status,**bom_fields(b,qty,aq),
            standard_qty=None,actual_qty=aq,standard_price=None,actual_price=ap,quote_price=None,tax_rate=tax.get(code),
            standard_cost=None,actual_cost=ac,qty_difference=None,cost_difference=None,quantity_effect=None,price_effect=None,
            qty_rate=None,price_rate=None,cost_rate=None,standard_cost_incl=None,
            actual_price_incl=ap*factor if ap is not None and factor else None,
            actual_cost_incl=ac*factor if ac is not None and factor else None,
            net_qty=a['qty'],net_amount=a['amount'],bills=sorted(a['bills'])))
    out['rows'].sort(key=lambda r:-(r['actual_cost'] or 0))
    return finish(out,bridge(out,detail,actual,qty),qty)


def compare(e, product, detail, mapping=None):
    if str(e.get('erp_code')) != str(product['code']): raise ValueError('核算底稿的产品编码不一致')
    out = {'standard':metadata(e), 'fingerprint':hashlib.sha256(json.dumps(e,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest(),
           'rows':[], 'product_qty':product.get('qty'), 'actual_unit_cost':product.get('unit'), 'ready':bool(detail)}
    qty = numeric(product.get('qty'))
    if qty is None or qty <= 0:
        out.update(ready=False, issue='本期完工量不大于零，不能计算每公斤对比'); return out
    std = defaultdict(list)
    for i,m in enumerate(e.get('materials') or []):
        code = str(m.get('matCode') or '').strip()
        std[(code or '@missing-'+str(i), unit(m.get('unit')))].append(m)
    actual = apply_mapping(actual_materials(detail),std,mapping);tax = purchase_tax(detail)
    bom = bom_usage(detail,std,mapping)                     # 换了编码/单位的料，BOM 一侧按同样的设定并（替代料逐工单取大，不相加）
    for k in dict.fromkeys([*std,*actual,*bom]):
        ms=std.get(k,[]);m=ms[0] if ms else {};a=actual.get(k);b=bom.get(k)
        code,u=k;sq=numeric(m.get('qtyPerKg'));sc=numeric(m.get('costExcl'))
        aq=a['qty']/qty if a and a['valid'] else None;ac=a['amount']/qty if a and a['valid'] else None
        sp=sc/sq if sc is not None and sq is not None and sq>0 else None
        ap=ac/aq if ac is not None and aq is not None and aq>0 else None
        status='匹配'
        if not detail: status='尚未读取实际材料'
        elif not ms and not a: status='仅在生产BOM里，本期未领用'
        elif not ms: status='单位不一致，待换算' if any(x[0]==code for x in std) else '实际新增用料'
        elif len(ms)>1: status='底稿同编码同单位多行，需确认'
        elif not m.get('matCode'): status='底稿缺物料编码'
        elif not a: status='本期未领用' if not any(x[0]==code for x in actual) else '单位不一致，待换算'
        elif not a['valid']: status='实际数量或金额缺失'
        elif sp is None or ap is None: status='零/负数量或价格缺失，待核查'
        valid=status=='匹配'
        # 含税口径：底稿这一行自己的「含税价 ÷ 不含税计价」就是倍率（专票≈1+税率，普票＝1），实际成本按同一倍率折，两边才可比。
        # 没有底稿行的（实际新增）退而用本期应付单税率；都没有就不折，页面显示「—」。
        pi=numeric(m.get('priceIncl'))
        factor=pi/sp if pi is not None and sp else (1+tax[code] if not ms and code in tax else None)
        out['rows'].append(dict(code=m.get('matCode') or code,name=m.get('matName') or (a or b or {}).get('name'),unit=u,
            section=m.get('seg') if ms else '实际新增' if a else '仅生产BOM',status=status,
            **bom_fields(b,qty,aq if valid or not ms else None,sq if len(ms)==1 else None,bool(detail)),standard_qty=sq,actual_qty=aq,standard_price=sp,actual_price=ap,
            quote_price=pi,tax_rate=numeric(m.get('taxRate')) if ms else tax.get(code),standard_cost=sc,actual_cost=ac,
            qty_difference=aq-sq if valid else None,cost_difference=ac-sc if valid else None,
            quantity_effect=(aq-sq)*sp if valid else None,price_effect=aq*(ap-sp) if valid else None,
            qty_rate=rate(aq,sq) if valid else None,price_rate=rate(ap,sp) if valid else None,cost_rate=rate(ac,sc) if valid else None,
            standard_cost_incl=sq*pi if sq is not None and pi is not None else None,
            actual_price_incl=ap*factor if ap is not None and factor else None,
            actual_cost_incl=ac*factor if ac is not None and factor else None,
            net_qty=a['qty'] if a else None,net_amount=a['amount'] if a else None,
            merged=[dict(x,qty_per_kg=x['qty']/qty,cost_per_kg=x['amount']/qty) for x in (a or {}).get('merged',[])],
            bills=sorted((a or {}).get('bills',[]))))
    out['standard_material_cost']=sum(numeric(m.get('costExcl')) or 0. for m in e.get('materials') or [])
    out['standard_material_incl']=sum(r['standard_cost_incl'] or 0. for r in out['rows'])
    total=bridge(out,detail,actual,qty)
    out['material_cost_rate']=rate(out['actual_material_input_per_kg'],out['standard_material_cost'])
    out['fees_incl']={k:e.get('fee_'+k) for k in ('mfg','load','adm')}
    out['standard_full_incl']=e.get('full_cost_incl')
    out['actual_costs']={k:product.get(k)/qty if numeric(product.get(k)) is not None else None for k in
                         ('labor','indirect','water','power','gas','oil','depreciation','rent','other','wip','total')}
    out['issues']=[e['provenance_note']] if e.get('provenance_note') else []
    if e.get('status')!='已定稿': out['issues'].append('所选底稿尚未定稿，仅作为本次比较参考；不改变原审核状态。')
    out['issues'].append('实际用量为本期净领补退料 ÷ 本期完工公斤，含在产和跨期影响，不直接判定超耗。实际价格为出库计价，采购价另见采购参考页。')
    out['issues'].append('逐料比较使用底稿存储的不含税成本；含税报价费用与金蝶费用分类尚未建立对应，不计算全成本差额。')
    return finish(out,total,qty)
