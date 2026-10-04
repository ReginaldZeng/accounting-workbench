# [Change Log] 2026-10-03 | Codex | V-draft | Accounting worksheet comparison, read-only
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


def compare(e, product, detail):
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
    actual = defaultdict(lambda:{'qty':0.,'amount':0.,'name':'','bills':set(),'valid':True})
    for m in (detail or {}).get('movements',[]):
        k=(str(m.get('code') or '').strip(),unit(m.get('unit')))
        a=actual[k];q=numeric(m.get('net_qty'));v=numeric(m.get('net_amount'))
        a['valid'] &= q is not None and v is not None
        a['qty'] += q or 0.;a['amount'] += v or 0.;a['name']=m.get('name');a['bills'].add(m.get('bill',''))
    for k in dict.fromkeys([*std,*actual]):
        ms=std.get(k,[]);m=ms[0] if ms else {};a=actual.get(k)
        code,u=k;sq=numeric(m.get('qtyPerKg'));sc=numeric(m.get('costExcl'))
        aq=a['qty']/qty if a and a['valid'] else None;ac=a['amount']/qty if a and a['valid'] else None
        sp=sc/sq if sc is not None and sq is not None and sq>0 else None
        ap=ac/aq if ac is not None and aq is not None and aq>0 else None
        status='匹配'
        if not detail: status='尚未读取实际材料'
        elif not ms: status='单位不一致，待换算' if any(x[0]==code for x in std) else '实际新增用料'
        elif len(ms)>1: status='底稿同编码同单位多行，需确认'
        elif not m.get('matCode'): status='底稿缺物料编码'
        elif not a: status='本期未领用' if not any(x[0]==code for x in actual) else '单位不一致，待换算'
        elif not a['valid']: status='实际数量或金额缺失'
        elif sp is None or ap is None: status='零/负数量或价格缺失，待核查'
        valid=status=='匹配'
        out['rows'].append(dict(code=m.get('matCode') or code,name=m.get('matName') or (a or {}).get('name'),unit=u,
            section=m.get('seg') if ms else '实际新增',status=status,standard_qty=sq,actual_qty=aq,standard_price=sp,actual_price=ap,
            quote_price=numeric(m.get('priceIncl')),tax_rate=numeric(m.get('taxRate')),standard_cost=sc,actual_cost=ac,
            qty_difference=aq-sq if valid else None,cost_difference=ac-sc if valid else None,
            quantity_effect=(aq-sq)*sp if valid else None,price_effect=aq*(ap-sp) if valid else None,
            bills=sorted((a or {}).get('bills',[]))))
    out['standard_material_cost']=sum(numeric(m.get('costExcl')) or 0. for m in e.get('materials') or [])
    out['actual_material_input_per_kg']=sum(a['amount'] for a in actual.values())/qty if detail else None
    complete=(detail or {}).get('controls',{}).get('complete_material')
    out['completed_material_per_kg']=complete/qty if complete is not None else None
    out['material_timing_bridge']=(complete-sum(a['amount'] for a in actual.values()))/qty if complete is not None else None
    out['fees_incl']={k:e.get('fee_'+k) for k in ('mfg','load','adm')}
    out['standard_full_incl']=e.get('full_cost_incl')
    out['actual_costs']={k:product.get(k)/qty if numeric(product.get(k)) is not None else None for k in
                         ('labor','indirect','water','power','gas','oil','depreciation','rent','other','wip','total')}
    out['issues']=[e['provenance_note']] if e.get('provenance_note') else []
    if e.get('status')!='已定稿': out['issues'].append('所选底稿尚未定稿，仅作为本次比较参考；不改变原审核状态。')
    out['issues'].append('实际用量为本期净领补退料 ÷ 本期完工公斤，含在产和跨期影响，不直接判定超耗。实际价格为出库计价，采购价另见采购参考页。')
    out['issues'].append('逐料比较使用底稿存储的不含税成本；含税报价费用与金蝶费用分类尚未建立对应，不计算全成本差额。')
    return out
