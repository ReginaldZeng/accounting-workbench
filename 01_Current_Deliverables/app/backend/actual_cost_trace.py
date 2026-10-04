# [Change Log] 2026-10-03 | Codex | V-draft | Read-only product, order and material trace
# [Change Log] 2026-10-04 | Claude / c | V2.789 | 委外产品（车间「委外」）下钻：取委外领料/补料/退料单与委外用料清单，口径同自制。
import calendar
import re
from collections import defaultdict
from datetime import datetime, timezone

import kingdee_client as kd
from kernels.actual_cost import number, OUTSOURCED, is_outsourced

# 逐料单据来源：自制走生产领料/补料/退料 + 生产用料清单；委外走对应的委外单据。字段名经真账套只读实测。
FORMS = {
    False: dict(moves=[('PRD_PickMtrl','生产领料','FBaseActualQty',1),('PRD_FeedMtrl','生产补料','FBaseActualQty',1),('PRD_ReturnMtrl','生产退料','FBaseQty',-1)],
                wo='FMoBillNo', org='FPrdOrgId.FNumber', bom='PRD_PPBOM', bom_wo='FMOBillNO', bom_entry='FMOEntryID'),
    True: dict(moves=[('SUB_PickMtrl','委外领料','FBaseActualQty',1),('SUB_FEEDMTRL','委外补料','FBaseActualQty',1),('SUB_RETURNMTRL','委外退料','FBaseQty',-1)],
               wo='FSubReqBillNo', org='FSubOrgId.FNumber', bom='SUB_PPBOM', bom_wo='FSubReqBillNO', bom_entry='FSubReqEntryId'),
}


def product_rows(snapshot, cc, code):
    product=next((p for p in snapshot['result']['products'] if p['cc']==cc and p['code']==code),None)
    if product is None: raise ValueError('本期快照中没有该车间产品')
    rows=[];active=False;wo=item=''
    for i,r in enumerate(snapshot['sources']['cost'],1):
        if r[3]:
            out=is_outsourced(r[3],r[4])
            # 委外工单在金蝶里没有成本中心，全成本表记「委外」；金额为零的委外工单不列（与内核一致）
            active=str(r[1]).strip()==code and ((cc==OUTSOURCED and r[9] not in ('',None) and number(r[9])!=0) if out else str(r[0]).strip()==cc)
            wo=str(r[3]);item=''
        if not active:continue
        if r[5]:item=r[5]
        level='工单汇总' if r[3] else '成本项目' if r[5] else '费用明细' if r[6] else '材料子层（编码待补）'
        rows.append(dict(source_row=i,wo=wo,level=level,item=item,expense=r[6],
                         input_amount=0 if r[7] in ('',None) else number(r[7]),
                         qty=None if r[8] in ('',None) else number(r[8]),
                         amount=0 if r[9] in ('',None) else number(r[9])))
    return product,rows


def fetch_product_trace(snapshot,cc,code,client=kd):
    """成本快照不修改；补取工单用料清单、本期领补退料及同组织本期应付价格。"""
    product,cost_rows=product_rows(snapshot,cc,code)
    org,period=snapshot['sources']['org'],snapshot['sources']['period']
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,8}',org) or not re.fullmatch(r'20\d{2}-\d{2}',period): raise ValueError('组织或期间无效')
    orders=sorted({r['wo'] for r in cost_rows if r['level']=='工单汇总'})
    if any(not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',wo) for wo in orders): raise ValueError('工单号无效')
    quoted=','.join("'"+wo+"'" for wo in orders)
    year,month=map(int,period.split('-'));end=f'{period}-{calendar.monthrange(year,month)[1]:02d}T23:59:59'
    dates=f"FDate>='{period}-01' and FDate<='{end}'"
    s,c=client.login();movements=[];notes=[]
    outsourced=cc==OUTSOURCED;f=FORMS[outsourced]
    for form,label,qty_key,sign in f['moves']:
        fields=[('FBillNo','bill'),('FEntity_FEntryID','entry_id'),('FDate','date'),(f['wo'],'wo'),
                ('FMaterialId.FNumber','code'),('FMaterialId.FName','name'),('FBaseUnitId.FName','unit'),
                (qty_key,'qty'),('FAmount','amount'),('FDocumentStatus','status'),('FCancelStatus','cancel'),(f['org'],'org')]
        rows=client._query(s,c,form,fields,f"{f['org']}='{org}' and {f['wo']} in ({quoted}) and {dates}",'FBillNo,FEntity_FEntryID')
        for row in rows:
            if str(row['org'])!=org or row['wo'] not in orders or str(row['date'])[:7]!=period: raise ValueError('领补退料返回范围不符')
            if row['status']!='C' or row['cancel']!='A':raise ValueError('本期存在未审核或作废领补退料，需核对后重取')
            movements.append(dict(row,form=form,kind=label,net_qty=sign*number(row['qty']),net_amount=sign*number(row['amount'])))
    if len({(r['form'],r['entry_id']) for r in movements})!=len(movements): raise ValueError('领补退料分录重复')
    fields=[('FBillNo','bill'),('FEntity_FEntryID','entry_id'),(f['bom_wo'],'wo'),(f['bom_entry'],'mo_entry'),('FMaterialID.FNumber','product'),
            ('FBOMID.FNumber','bom'),('FBaseQty','product_qty'),('FBaseUnitID.FName','product_unit'),
            ('FMaterialID2.FNumber','code'),('FMaterialID2.FName','name'),('FBaseUnitID1.FName','unit'),
            ('FBaseStdQty','standard_qty'),('FBaseNeedQty','need_qty'),('FBasePickedQty','picked'),('FBaseRepickedQty','repicked'),
            ('FBaseGoodReturnQty','returned'),('FBaseConsumeQty','consumed'),('FBaseWipQty','wip'),
            ('FDosageType','dosage_type'),('FBaseFixScrapQTY','fixed_scrap'),('FDocumentStatus','status'),('FModifyDate','modified_at'),(f['org'],'org')]
    bom=client._query(s,c,f['bom'],fields,f"{f['org']}='{org}' and {f['bom_wo']} in ({quoted})",f"{f['bom_wo']},FEntity_FEntryID")
    for r in bom:
        if str(r['org'])!=org or r['wo'] not in orders or str(r['product'])!=code: raise ValueError('工单用料清单返回范围不符')
    codes=sorted({r['code'] for r in movements+bom})
    if any(not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',x) for x in codes):raise ValueError('子料编码无效')
    prices=[]
    if codes:
        fields=[('FBillNo','bill'),('FEntityDetail_FEntryID','entry_id'),('FDate','date'),('FSupplierId.FName','supplier'),
                ('FMaterialId.FNumber','code'),('FMaterialId.FName','name'),('FPRICEUNITID.FName','unit'),('FPriceQty','qty'),
                ('FPrice','price'),('FTaxPrice','tax_price'),('FEntryTaxRate','tax_rate'),('FCURRENCYID.FName','currency'),
                ('FSETTLEORGID.FNumber','org'),('FDocumentStatus','status')]
        material_filter=','.join("'"+x+"'" for x in codes)
        prices=client._query(s,c,'AP_Payable',fields,f"FSETTLEORGID.FNumber='{org}' and FMaterialId.FNumber in ({material_filter}) and FDocumentStatus='C' and {dates}",'FDate desc,FBillNo')
        for r in prices:
            if str(r['org'])!=org or str(r['date'])[:7]!=period or r['code'] not in codes:raise ValueError('采购价格返回范围不符')
        if len({r['entry_id'] for r in prices})!=len(prices):raise ValueError('应付物料分录重复，不能作价格对照')
    detail=dict(org=org,period=period,cc=cc,code=code,fetched_at=datetime.now(timezone.utc).isoformat(),
                movements=movements,bom=bom,prices=prices,notes=notes)
    detail.update(compare_materials(cost_rows,detail))
    if outsourced:
        detail['notes']=['委外产品：逐料取自委外领料、委外补料、委外退料单和委外用料清单；委外加工费见成本计算单，不在逐料里。',
                         '委外产品不在厂内生产，不分摊厂内人工、水电、折旧、租金和共享领用。']+detail['notes']
    return detail


def compare_materials(cost_rows,detail):
    grouped={};standards=defaultdict(list);completed=defaultdict(float)
    mo_entries=defaultdict(set)
    for row in cost_rows:
        if row['level']=='工单汇总':completed[row['wo']]+=row['qty'] or 0
    for r in detail['bom']:
        standards[(r['wo'],r['code'],r['unit'])].append(r)
        mo_entries[r['wo']].add((r['bill'],r['mo_entry']))
    for r in detail['movements']:
        key=(r['wo'],r['code'],r['unit'])
        item=grouped.setdefault(key,dict(wo=r['wo'],code=r['code'],name=r['name'],unit=r['unit'],qty=0,amount=0))
        item['qty']+=r['net_qty'];item['amount']+=r['net_amount']
    for key,rows in standards.items():
        grouped.setdefault(key,dict(wo=key[0],code=key[1],name=rows[0]['name'],unit=key[2],qty=0,amount=0))
    for key,r in grouped.items():
        r['unit_cost']=r['amount']/r['qty'] if r['qty'] else None
        standard=standards.get(key,[]);r['standard_qty']=r['variance_qty']=r['variance_rate']=None
        r['bom']=','.join(sorted({x['bom'] for x in standard}));r['comparison_note']='缺少对应工单用料清单'
        if standard:
            valid=len(mo_entries[r['wo']])==1 and all(x['status']=='C' and x['product_unit']=='千克' and number(x['product_qty'])>0 and str(x['dosage_type'])=='2' and number(x['fixed_scrap'] or 0)==0 for x in standard)
            if valid:
                r['standard_qty']=sum(number(x['standard_qty'])/number(x['product_qty'])*completed[r['wo']] for x in standard)
                r['variance_qty']=r['qty']-r['standard_qty']
                r['variance_rate']=r['variance_qty']/r['standard_qty'] if r['standard_qty'] else None
                r['comparison_note']='本期净领用 vs 工单标准量按本期完工kg等比例折算；在产/跨期会影响差异，不能直接认定超耗'
            else:r['comparison_note']='固定用量/损耗、单位或审核条件不满足，暂不强行折算'
    input_material=sum(r['input_amount'] for r in cost_rows if r['level']=='成本项目' and r['item'] in ('直接材料','间接材料'))
    complete_material=sum(r['amount'] for r in cost_rows if r['level']=='成本项目' and r['item'] in ('直接材料','间接材料'))
    movement_amount=sum(r['net_amount'] for r in detail['movements'])
    return dict(materials=list(grouped.values()),controls=dict(input_material=input_material,complete_material=complete_material,
                movement_amount=movement_amount,input_difference=movement_amount-input_material),notes=[
                '净领用=本期生产领料+生产补料−生产退料；金额是库存出库成本，单位成本=净领用金额÷基本单位净数量。',
                '采购价格来自同结算组织、本期已审核应付单物料分录；列出税率、币别、计价单位，仅供参考，不代表领用库存对应采购批次。',
                '用料清单保留工单指定BOM版本及取数时修改日期，可能存在后续变更；已消耗/在制数为金蝶单据原值，不替代领退料实绩。',
                '配方比较暂为用量差异，尚未引入标准价格或把差异判定为实际损失。'])
