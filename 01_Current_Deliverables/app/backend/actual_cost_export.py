# [Change Log] 2026-10-02 | Codex | V-draft | Period snapshot workbook with cached formulas
# [Change Log] 2026-10-04 | Claude / c | V2.789 | 加「委外加工费」列；委外产品（车间记「委外」）进公式归集链；主表列位改按字段名定位，不再写死列字母。
import io
import json
from datetime import datetime, timezone
try:
    import xlsxwriter
except ImportError:  # 服务器还没装这个库时，只让「导出」报明白话；不能因为它把整个后端的启动带崩
    xlsxwriter = None
from kernels.actual_cost import COST_FIELDS, CENTRES, OUTSOURCED, is_outsourced, number, reconcile, trial_workorders

FIELDS = [('cc','车间'),('code','物料编码'),('name','物料名称'),('spec','规格'),('qty','完工数量 kg'),
          ('material','直接材料'),('packaging','包材'),('labor','直接人工'),('indirect','间接人工'),
          ('water','水费'),('power','电费'),('gas','燃气及氮气'),('depreciation','折旧摊销'),
          ('rent','租金'),('other','其他制造费用'),('subcontract','委外加工费'),('wip','在产调整'),
          ('total','全成本合计'),('unit','单位成本 元/kg')]
IDX = {key:i for i,(key,_) in enumerate(FIELDS)}      # 主表字段 → 0 起列号
LETTER = {key:chr(65+i) for key,i in IDX.items()}     # 主表字段 → 列字母（不超过 Z）
TRIAL_COL = len(FIELDS)                               # 主表右侧三列试产信息的起始列（0 起）


def build_workbook(snapshot, latest, formal=False):
    """保存取数快照；Excel公式从原始字段逐层归集，与内核结果独立核对。"""
    if xlsxwriter is None:
        raise ValueError('服务器还没安装导出组件 XlsxWriter，暂时不能导出 Excel；请管理员安装后重启后端')
    sources, result = snapshot['sources'], snapshot['result']
    org, period = sources['org'], sources['period']
    if result['org'] != org or result['period'] != period: raise ValueError('导出快照期间不符')
    if formal and (not result['ready'] or latest['status'] != 'ready'): raise ValueError('试算不能标为正式结果')
    label = '正式' if formal else '试算-待确认'
    products = result['products']
    reconcile(sum(p['total'] for p in products),result['controls']['total'],'导出总额')
    stream = io.BytesIO()
    wb = xlsxwriter.Workbook(stream, {'in_memory':True,'strings_to_formulas':False,'strings_to_urls':False})
    wb.set_properties({'title':f'{period} 全成本表 · {label}','comments':latest['run_id']})
    base = {'font_name':'微软雅黑','font_size':10,'valign':'vcenter'}
    text = wb.add_format(base)
    money = wb.add_format({**base,'num_format':'#,##0.0000;[Red](#,##0.0000);"–"'})
    header = wb.add_format({**base,'bold':True,'font_color':'white','bg_color':'#243C5A','text_wrap':True})
    title = wb.add_format({**base,'font_size':15,'bold':True})
    caution = wb.add_format({**base,'font_color':'#9C5700','bg_color':'#FFF4D6','text_wrap':True})
    note = f'账簿 {org} | {period} | {label} | 金额：元 | 取数：{sources["fetched_at"]}'

    def sheet(name, headings, rows):
        ws = wb.add_worksheet(name)
        ws.hide_gridlines(2); ws.set_default_row(22); ws.set_column(0,len(headings)-1,22,text)
        ws.write(0,0,f'{name} · {label}',title); ws.write(1,0,note)
        ws.write(2,0,'公式关联版：原始数据→产品公式归集/费用分摊依据→产品全成本。修改本地文件不回写工作台；修改后须重新核对。')
        ws.write_row(4,0,headings,header); ws.set_row(4,34)
        for r,row in enumerate(rows,5):
            for c,value in enumerate(row):
                if isinstance(value,(dict,list)): value=json.dumps(value,ensure_ascii=False,allow_nan=False)
                if isinstance(value,str) and len(value)>32767: raise ValueError('导出文本超出Excel单元格限制')
                ws.write(r,c,value,money if isinstance(value,(int,float)) and not isinstance(value,bool) else text)
        ws.autofilter(4,0,max(4,4+len(rows)),len(headings)-1); ws.freeze_panes(5,2)
        ws.set_landscape(); ws.fit_to_pages(1,0); ws.repeat_rows(0,4)
        return ws

    main = sheet('产品全成本',[h for _,h in FIELDS],[[p.get(k) for k,_ in FIELDS] for p in products])
    main.set_column(2,2,48); main.set_column(3,3,30)
    for i,p in enumerate(products,5):
        r=i+1
        reconcile(sum(p.get(k,0) for k in COST_FIELDS),p['total'],f'{p["code"]}导出合计')
        main.write_formula(i,IDX['total'],f'=SUM({LETTER["material"]}{r}:{LETTER["wip"]}{r})',money,p['total'])
        value=p['total']/p['qty'] if p['qty'] else ''
        if p['qty']: reconcile(value,p['unit'],f'{p["code"]}导出单位成本')
        main.write_formula(i,IDX['unit'],f'=IF(E{r}=0,"",{LETTER["total"]}{r}/E{r})',money,value)
    total_row=len(products)+6
    main.write(total_row,0,'筛选后合计',header)
    for c,(key,_) in enumerate(FIELDS[4:IDX['total']+1],4):
        col=xlsxwriter.utility.xl_col_to_name(c)
        main.write_formula(total_row,c,f'=SUBTOTAL(109,{col}6:{col}{len(products)+5})',money,sum(p.get(key,0) for p in products))

    controls = result['controls']
    audit = sheet('出表校验',['项目','值'],[
        ['出表状态',label],['账簿',org],['期间',period],['快照编号',latest['run_id']],
        ['规则版本',result['rule_version']],['导出时间',datetime.now(timezone.utc).isoformat()],
        ['金蝶完工成本',controls['gold_total']],['在产调整',controls['wip_allocated']],
        ['来源差异',controls['source_difference']],['全成本合计',controls['total']],
        ['金额桥接差额',0],['产品对象数',len(products)],
        ['说明','源表分层金额不可整列求和；导出保留原始来源，成本仅按计算内核校验后的口径汇总。'],
        *[['待处理事项',issue] for issue in latest.get('issues',[])],
        *[[label,snapshot['rules'].get('close_confirmation',{}).get(key,'')] for key,label in
          [('confirmed_by','工作台结账确认人'),('confirmed_at','工作台结账确认时间'),('id','结账确认编号')]],
    ])
    audit.set_column(0,0,26); audit.set_column(1,1,100)
    audit.write_formula(15,1,'=ROUND(B12+B13+B14-B15,6)',money,round(controls['gold_total']+controls['wip_allocated']+controls['source_difference']-controls['total'],6))
    audit.write(16,1,len(products),wb.add_format({**base,'num_format':'0'}))
    if not formal: audit.write(5,1,label,caution)
    sheet('未分配在产',['产品对象','调整金额 元'],[[r['key'],r['amount']] for r in result['unallocated_wip']])
    configs=[['规则',key,value] for key,value in snapshot['rules'].items()]
    configs += [['本期补充依据',key,value] for key,value in snapshot['supplement'].items()]
    rules = sheet('规则及本期依据',['来源类别','字段','内容'],configs); rules.set_column(2,2,100)
    cost = sheet('金蝶成本原始层级',['原始行号','成本中心','物料编码','物料名称','工单','单据类型','成本项目','费用项目','投入金额','完工数量','完工金额'],
                 [[i+1,*row] for i,row in enumerate(sources['cost'])])
    cost.set_column(3,3,48)
    raw={}
    for name,key in [('金蝶其他出库','outbound'),('金蝶凭证','ledger')]:
        rows=sources[key]
        fields=list(dict.fromkeys(k for row in rows for k in row))
        raw[key]=(sheet(name,fields or ['无记录'],[[row.get(k) for k in fields] for row in rows]),fields)
    _link_formulas(wb,sheet,snapshot,main,audit,cost,raw,money,header)
    wb.close()
    return stream.getvalue()


def _link_formulas(wb, sheet, snapshot, main, audit, cost, raw, money, header):
    sources, rules, supplement, result = (snapshot[k] for k in ('sources','rules','supplement','result'))
    products = result['inputs']['products']
    count = len(products)
    col = xlsxwriter.utility.xl_col_to_name
    link = wb.add_format({'font_name':'微软雅黑','font_size':10,'font_color':'#008000',
                         'num_format':'#,##0.0000;[Red](#,##0.0000);"–"'})
    def rng(name, column, size):
        return f"'{name}'!${column}$6:${column}${max(6,size+5)}"
    def put(ws, row, column, formula, value):
        ws.write_formula(row-1,column-1,'='+formula,link,'' if value is None else value)
    def quote(value):
        return '"'+str(value).replace('"','""')+'"'
    def lookup(key, name, left, right, size):
        return f'INDEX({rng(name,right,size)},MATCH({key},{rng(name,left,size)},0))'

    params = sheet('分摊参数',['参数','值','','产品分组','小料系数','总量系数','','成本中心','计入共享产量','光伏归属部门','宿舍归属部门','','在产凭证部门','对应成本中心','','共享出库类别'],[])
    scalars=[('费用金额口径',rules['basis']),('共享分摊方式',rules['shared_basis']),
             ('手填共享比例',supplement.get('shared_tea_ratio')),('植物肉厂房租金比例',rules['rent_plant_ratio']),
             ('小料领用部门',rules['tea_department']),('油费用项目',rules['oil_expense']),('在产处理方式',rules['wip_policy'])]
    for r,values in enumerate(scalars,5): params.write_row(r,0,values)
    groups=rules.get('shared_group_weights') or {p['group']:{'tea':0,'total':0} for p in products}
    centres=rules.get('shared_centre_weights') or {p['cc']:1 for p in products if p['cc']!=OUTSOURCED}
    no_weight={'tea':0,'total':0}   # 委外不计入共享分摊产量：车间系数、分组系数一律按 0
    for r,(name,weight) in enumerate(groups.items(),5): params.write_row(r,3,[name,weight['tea'],weight['total']])
    for r,(name,weight) in enumerate(centres.items(),5):
        params.write_row(r,7,[name,weight,rules['solar_departments'].get(name,''),rules['dorm_departments'].get(name,'')])
    for r,values in enumerate(rules['wip_departments'].items(),5): params.write_row(r,12,values)
    for r,value in enumerate(rules['shared_categories'],5): params.write(r,15,value)
    params.autofilter(4,0,5+max(len(scalars),len(groups),len(centres),len(rules['wip_departments']),len(rules['shared_categories']))-1,15)
    params.write(3,0,'业务规则待确认；蓝色区域为规则值。每月重新取数导出会重建范围；本文件不自动识别新增行或新增产品。')
    params.set_column(1,1,26,wb.add_format({'font_color':'#0000FF','num_format':'0.00000000'}))
    params.set_column(4,5,14);params.set_column(8,8,16)
    mapping=sheet('费用映射',['金蝶费用项目','归集字段','全成本去向'],
                  [[k,v,{'other':'其他费用池（油另转直接材料）','gold_utilities':'水电费用池','gold_rent':'租金费用池','gold_depreciation':'折旧摊销费用池','indirect':'间接人工','gas':'燃气','nitrogen':'氮气费用池'}.get(v,v)] for k,v in rules['expense_map'].items()])
    mapping.set_column(2,2,42)
    materials=list(sources['materials'].values())
    sheet('金蝶物料档案',['物料编码','物料名称','规格','基本单位','产品分组'],
          [[m[k] for k in ('code','name','spec','unit','group')] for m in materials])
    trials=trial_workorders(sources)
    decisions=supplement.get('trial_shared_decisions',{})
    trial=sheet('试产工单确认',['原始行号','车间','物料编码','物料名称','工单号','单据类型','完工数量 kg','金蝶完工成本 元','车间默认系数','共享分摊确认','本地试算系数','小料分组系数','总量分组系数','分子产量调整','分母产量调整'],[])
    trial.write(2,0,'单独列示，不另加成本。待确认沿用车间范围仅供试算；计入/不计入只调整共享分摊产量。手填比例口径不受本页选择影响。')
    trial.write(3,0,'Excel选择只在本地重算，不回写工作台；正式出表须在工作台保存逐单选择并确认。零完工工单列示但不影响比例。')
    trial.set_column(3,3,40);trial.set_column(9,9,22,wb.add_format({'font_color':'#0000FF','bg_color':'#FFF4D6'}))
    trial_adjustments={}
    for i,o in enumerate(trials,6):
        source=o['source_row']+5
        decision=decisions.get(o['key'],'pending');baseline=centres[o['cc']]
        effective={'include':1,'exclude':0}.get(decision,baseline)
        group=sources['materials'][o['code']]['group'];weight=groups[group]
        vals=[o['source_row'],o['cc'],o['code'],o['name'],o['wo'],o['bill_type'],o['qty'],o['gold_total']]
        for c,(rawcol,value) in enumerate(zip(('A','B','C','D','E','F','S','R'),vals),1):
            put(trial,i,c,f"'金蝶成本原始层级'!{rawcol}{source}",value)
        put(trial,i,9,lookup(f'B{i}','分摊参数','H','I',len(centres)),baseline)
        trial.write(i-1,9,{'pending':'待确认','include':'计入','exclude':'不计入'}[decision])
        put(trial,i,11,f'IF(J{i}="计入",1,IF(J{i}="不计入",0,I{i}))',effective)
        glookup=lookup(f'C{i}','金蝶物料档案','A','E',len(materials))
        for c,right,key in [(12,'E','tea'),(13,'F','total')]:put(trial,i,c,lookup(glookup,'分摊参数','D',right,len(groups)),weight[key])
        delta=o['qty']*(effective-baseline)
        put(trial,i,14,f'G{i}*(K{i}-I{i})*L{i}',delta*weight['tea'])
        put(trial,i,15,f'G{i}*(K{i}-I{i})*M{i}',delta*weight['total'])
        adjustments=trial_adjustments.setdefault((o['cc'],o['code']),[0,0])
        adjustments[0]+=delta*weight['tea'];adjustments[1]+=delta*weight['total']
    trial.autofilter(4,0,max(4,len(trials)+4),14)
    if trials:trial.data_validation(5,9,len(trials)+4,9,{'validate':'list','source':['待确认','计入','不计入'],'ignore_blank':False,'error_type':'stop','error_message':'请选择待确认、计入或不计入'})
    tr=lambda c:rng('试产工单确认',c,len(trials))
    main.write_row(4,TRIAL_COL,['试产工单数','其中试产完工 kg','试产共享确认'],header)
    main.autofilter(4,0,count+4,TRIAL_COL+2);main.set_column(TRIAL_COL,TRIAL_COL+2,22)
    tn,tq=chr(65+TRIAL_COL),chr(65+TRIAL_COL+1)   # 试产工单数、试产完工 kg 两列的列字母
    for i,p in enumerate(products,6):
        related=[o for o in trials if o['cc']==p['cc'] and o['code']==p['code']]
        common=f'{tr("B")},A{i},{tr("C")},B{i}'
        put(main,i,TRIAL_COL+1,f'COUNTIFS({common})',len(related))
        put(main,i,TRIAL_COL+2,f'SUMIFS({tr("G")},{common})',sum(o['qty'] for o in related))
        pending=any(o['qty'] and decisions.get(o['key'],'pending')=='pending' for o in related)
        status='待确认' if pending else '已逐单选择' if any(o['qty'] for o in related) else '零产量' if related else '无试产'
        put(main,i,TRIAL_COL+3,f'IF({tn}{i}=0,"无试产",IF({tq}{i}=0,"零产量",IF(COUNTIFS({common},{tr("J")},"待确认",{tr("G")},">0")>0,"待确认","已逐单选择")))',status)

    # 金蝶字段原样保留；新增列只做公式归属，避免把三层金额重复加总。
    cost.write(3,0,'A:K保留本次金蝶接口取数字段及原始顺序（不是手工导出文件版式）；L:S为公式辅助列。仅工单汇总计数量，各成本层级分开归集。委外工单车间记「委外」，金额为零的委外工单不纳入。')
    cost.write_row(4,11,['公式车间','公式产品编码','公式成本项目','公式行层级','纳入全成本','归集字段','完工金额数值','完工数量数值'],header)
    cc=code=item='';included=0
    sub_test=lambda r: f'OR(LEFT(UPPER(E{r}),3)="SUB",ISNUMBER(SEARCH("委外",F{r})))'
    for i,row in enumerate(sources['cost'],6):
        if row[3]:
            outsourced=is_outsourced(row[3],row[4])
            cc,code=(OUTSOURCED if outsourced else str(row[0] or '').strip()),str(row[1] or '').strip(); item=''
            included=int(not (outsourced and (0 if row[9] in ('',None) else number(row[9]))==0))
        elif row[5]: item=str(row[5])
        level='工单汇总' if row[3] else '成本项目' if row[5] else '制造费用明细' if row[6] and item=='制造费用' else '明细'
        field=('gold_total' if level=='工单汇总' else
               {'直接材料':'material','间接材料':'material','直接人工':'labor','制造费用':'manufacturing_total','委外加工费':'subcontract'}.get(item,'') if level=='成本项目' else
               rules['expense_map'].get(str(row[6]),'') if level=='制造费用明细' else '')
        previous=lambda c: f'{c}{i-1}' if i>6 else '""'
        formulas=[(f'IF(E{i}<>"",IF({sub_test(i)},{quote(OUTSOURCED)},TRIM(B{i})),{previous("L")})',cc),
                  (f'IF(E{i}<>"",TRIM(C{i}),{previous("M")})',code),
                  (f'IF(E{i}<>"","",IF(G{i}<>"",G{i},{previous("N")}))',item),
                  (f'IF(E{i}<>"","工单汇总",IF(G{i}<>"","成本项目",IF(AND(H{i}<>"",N{i}="制造费用"),"制造费用明细","明细")))',level),
                  (f'IF(E{i}<>"",IF(AND({sub_test(i)},R{i}=0),0,1),{previous("P") or 0})',included),
                  (f'IF(O{i}="工单汇总","gold_total",IF(O{i}="成本项目",IF(OR(G{i}="直接材料",G{i}="间接材料"),"material",IF(G{i}="直接人工","labor",IF(G{i}="制造费用","manufacturing_total",IF(G{i}="委外加工费","subcontract","未映射")))),IF(O{i}="制造费用明细",{lookup(f"H{i}","费用映射","A","B",len(rules["expense_map"]))},"")))',field),
                  (f'IF(K{i}="",0,VALUE(SUBSTITUTE(K{i},",","")))',0 if row[9] in ('',None) else number(row[9])),
                  (f'IF(J{i}="",0,VALUE(SUBSTITUTE(J{i},",","")))',0 if row[8] in ('',None) else number(row[8]))]
        for c,(formula,value) in enumerate(formulas,12):put(cost,i,c,formula,value)
    cost.autofilter(4,0,len(sources['cost'])+4,18)
    cost.set_column(11,18,22)

    # 其他出库与凭证保留所有返回字段；筛选、分类与在产提取也用公式。
    ledger_sums={}; solar={}
    refs={}
    for kind in ('outbound','ledger'):
        ws,fields=raw[kind]; rows=sources[kind]; name=ws.name
        ref=lambda key,r: f'{col(fields.index(key))}{r}'
        start=len(fields)
        headings=['参与共享分摊','非小料共享金额'] if kind=='outbound' else ['水费金额','电费金额','光伏电费金额','费用借方金额','在产车间','在产产品编码','在产调整金额']
        ws.write_row(4,start,headings,header)
        ws.write(3,0,f'前{start}列为金蝶接口取数字段原值；右侧为公式辅助列。日期、金额、凭证号及分录ID保留便于追溯。')
        for i,row in enumerate(rows,6):
            if kind=='outbound':
                selected=row['category_name'] in rules['shared_categories'] and row['status']=='C' and row['cancel']=='A' and row['direction']=='GENERAL'
                formulas=[(f'IF(AND(COUNTIF({rng("分摊参数","P",len(rules["shared_categories"]))},{ref("category_name",i)})>0,{ref("status",i)}="C",{ref("cancel",i)}="A",{ref("direction",i)}="GENERAL"),1,0)',int(selected)),
                          (f'IF(AND({col(start)}{i}=1,{ref("dept",i)}<>\'分摊参数\'!$B$10),{ref("amount",i)},0)',number(row['amount']) if selected and row['dept']!=rules['tea_department'] else 0)]
            else:
                debit=number(row['debit']);is_wip=str(row['account']).startswith('5001'); valid=str(row['account']).startswith('5101') and row['status']=='C'
                condition=f'AND(LEFT({ref("account",i)},4)="5101",{ref("status",i)}="C")'
                expense,note=row['expense'],row['note']
                water=debit if valid and expense=='水电费' and '水费' in note else 0
                power=debit if valid and expense=='水电费' and '电费' in note else 0
                sun=debit if valid and expense=='水电费' and '光伏电费' in note else 0
                for key,v in [('water',water),('power',power),(expense,debit if valid else 0)]:ledger_sums[key]=ledger_sums.get(key,0)+v
                solar[row['dept']]=solar.get(row['dept'],0)+sun
                wcc=rules['wip_departments'].get(row['dept'],'') if is_wip else ''
                wcode=note.removeprefix('期末在产品成本调整产品编码') if is_wip else ''
                wamt=-debit if is_wip and rules['wip_policy']=='reverse_addback' else 0
                formulas=[]
                for token,v in [('水费',water),('电费',power),('光伏电费',sun)]:
                    formulas.append((f'IF(AND({condition},{ref("expense",i)}="水电费",ISNUMBER(SEARCH("{token}",{ref("note",i)}))),{ref("debit",i)},0)',v))
                formulas += [(f'IF({condition},{ref("debit",i)},0)',debit if valid else 0),
                             (f'IF(LEFT({ref("account",i)},4)="5001",{lookup(ref("dept",i),"分摊参数","M","N",len(rules["wip_departments"]))},"")',wcc),
                             (f'IF(LEFT({ref("account",i)},4)="5001",SUBSTITUTE({ref("note",i)},"期末在产品成本调整产品编码",""),"")',wcode),
                             (f'IF(AND(LEFT({ref("account",i)},4)="5001",\'分摊参数\'!$B$12="reverse_addback"),-{ref("debit",i)},0)',wamt)]
            for c,(formula,value) in enumerate(formulas,start+1):put(ws,i,c,formula,value)
        ws.autofilter(4,0,max(4,len(rows)+4),start+len(headings)-1)
        refs[kind]={k:rng(name,col(c),len(rows)) for c,k in enumerate(fields+headings)}

    agg_fields=['cc','code','name','spec','group','qty','gold_total','material','labor','indirect','gas','oil','other','gold_utilities','gold_depreciation','gold_rent','packaging']
    agg=sheet('产品公式归集',['车间','物料编码','物料名称','规格','产品分组','完工数量 kg','金蝶完工成本','材料原值','直接人工','间接人工','燃气','油转材料','其他费用原值','金蝶水电费','金蝶折旧摊销','金蝶租金','包材（未单独拆分）','本车间产量','车间内分摊比例','共享车间系数','小料分组系数','总量分组系数','共享小料产量','共享分母产量','在产调整','委外加工费'],[[p.get(k) for k in agg_fields]+[None]*9 for p in products])
    def ar(c):return rng('产品公式归集',c,count)
    def cr(c):return rng('金蝶成本原始层级',c,len(sources['cost']))
    lr=refs['ledger'];orr=refs['outbound']
    for i,p in enumerate(products,6):
        qty=sum(x['qty'] for x in products if x['cc']==p['cc']);share=p['qty']/qty if qty else 0
        common=f'{cr("L")},A{i},{cr("M")},B{i},{cr("P")},1'
        put(agg,i,4,lookup(f'B{i}','金蝶物料档案','A','C',len(materials)),p['spec'])
        put(agg,i,5,lookup(f'B{i}','金蝶物料档案','A','E',len(materials)),p['group'])
        put(agg,i,6,f'SUMIFS({cr("S")},{common},{cr("O")},"工单汇总")',p['qty'])
        for c,key in enumerate(agg_fields[6:16],7):
            formula=(f'SUMIFS({cr("R")},{common},{cr("O")},"制造费用明细",{rng("金蝶成本原始层级","H",len(sources["cost"]))},\'分摊参数\'!$B$11)' if key=='oil' else f'SUMIFS({cr("R")},{common},{cr("Q")},{quote(key)})')
            put(agg,i,c,formula,p[key])
        put(agg,i,17,'0',0)
        put(agg,i,18,f'SUMIF({ar("A")},A{i},{ar("F")})',qty)
        put(agg,i,19,f'IF(R{i}=0,0,F{i}/R{i})',share)
        house=p['cc']!=OUTSOURCED
        weight=groups[p['group']] if house else no_weight;cw=centres[p['cc']] if house else 0
        for c,key,left,right,n,v in [(20,f'A{i}','H','I',len(centres),cw),(21,f'E{i}','D','E',len(groups),weight['tea']),(22,f'E{i}','D','F',len(groups),weight['total'])]:
            put(agg,i,c,lookup(key,'分摊参数',left,right,n) if house else '0',v)
        adjustments=trial_adjustments.get((p['cc'],p['code']),[0,0])
        put(agg,i,23,f'F{i}*T{i}*U{i}+SUMIFS({tr("N")},{tr("B")},A{i},{tr("C")},B{i})',p['qty']*cw*weight['tea']+adjustments[0])
        put(agg,i,24,f'F{i}*T{i}*V{i}+SUMIFS({tr("O")},{tr("B")},A{i},{tr("C")},B{i})',p['qty']*cw*weight['total']+adjustments[1])
        wip=result['inputs']['wip'].get(p['cc']+'|'+p['code'],0) if p['qty'] else 0
        put(agg,i,25,f'IF(F{i}=0,0,SUMIFS({lr["在产调整金额"]},{lr["在产车间"]},A{i},{lr["在产产品编码"]},B{i}))',wip)
        put(agg,i,26,f'SUMIFS({cr("R")},{common},{cr("Q")},"subcontract")',p.get('subcontract',0))

    # 费用池每一项均有来源公式，分配只引用本页的费用池和车间产量。
    pool=sheet('费用分摊依据',['车间/范围','费用项目','金额 元/数量 kg/比例','计算口径'],[])
    pool.set_column(1,1,28);pool.set_column(2,2,24);pool.set_column(3,3,78)
    pr={};pv={}
    def poolrow(key,scope,label,formula,value,note):
        r=len(pr)+6;pr[key]=f"'费用分摊依据'!$C${r}";pv[key]=value
        pool.write(r-1,0,scope);pool.write(r-1,1,label);put(pool,r,3,formula,value);pool.write(r-1,3,note)
    poolrow('water','全厂','水费',f'SUM({lr["水费金额"]})',ledger_sums.get('water',0),'凭证水电费项目，摘要含水费')
    poolrow('power','全厂','电费',f'SUM({lr["电费金额"]})',ledger_sums.get('power',0),'凭证水电费项目，摘要含电费')
    for cc in CENTRES:
        dept=rules['solar_departments'][cc]
        poolrow(cc+'solar',cc,'光伏电费',f'SUMIF({lr["dept"]},{lookup(quote(cc),"分摊参数","H","J",len(centres))},{lr["光伏电费金额"]})',solar.get(dept,0),'归属部门见分摊参数')
    for key,expense in [('building','办公场所租赁费'),('property','物业管理费'),('nitrogen','车间燃料费')]:
        poolrow(key,'全厂',expense,f'SUMIF({lr["expense"]},{quote(expense)},{lr["费用借方金额"]})',ledger_sums.get(expense,0),'按金蝶凭证费用项目取入账金额')
    poolrow('shared','共享','非小料共享领用',f'SUM({orr["非小料共享金额"]})',result['inputs']['shared_amount'],'其他出库按类别、审核、作废、方向和领用部门筛选')
    for key,column in [('tea_qty','W'),('all_qty','X')]:
        value=sum(p['qty']*centres[p['cc']]*groups[p['group']]['tea' if key=='tea_qty' else 'total'] for p in products if p['cc']!=OUTSOURCED)+sum(v[0 if key=='tea_qty' else 1] for v in trial_adjustments.values())
        poolrow(key,'共享','小料分子 kg' if key=='tea_qty' else '分摊分母 kg',f'SUM({ar(column)})',value,'金蝶完工数量×车间范围×产品分组系数')
    poolrow('ratio','共享','转入小料比例',f'IF(\'分摊参数\'!$B$7="completed_quantity",{pr["tea_qty"]}/{pr["all_qty"]},\'分摊参数\'!$B$8)',result['controls']['shared_ratio'],'自动产量比例或明确的本期补充比例')
    poolrow('transfer','共享','转入小料金额',f'{pr["shared"]}*{pr["ratio"]}',pv['shared']*pv['ratio'],'植物肉其他费用池扣减，小料其他费用池增加；全厂总额不变')
    for cc in CENTRES:
        value=sum(p['qty'] for p in products if p['cc']==cc)
        poolrow(cc+'qty',cc,'车间完工数量',f'SUMIF({ar("A")},{quote(cc)},{ar("F")})',value,'本车间金蝶完工数量')
    for cc in CENTRES:
        proportion=f'{pr[cc+"qty"]}/({pr[CENTRES[0]+"qty"]}+{pr[CENTRES[1]+"qty"]})'
        reference=supplement.get('reference_pools',{}).get(cc,{})
        for field,label in [('water','水费'),('power','电费'),('depreciation','折旧摊销'),('rent','租金')]:
            if rules['basis']=='reference':
                # 历史复现分支：补充台账值明确放在参数区，不冒充金蝶取数。
                row=15+CENTRES.index(cc)*4+['water','power','depreciation','rent'].index(field)
                params.write(row-1,0,cc+label+'补充值');params.write(row-1,1,reference[field],money)
                formula=f"'分摊参数'!B{row}"
            elif field=='water':formula=f'{pr["water"]}*{proportion}'
            elif field=='power':formula=f'({pr["power"]}-{pr[CENTRES[0]+"solar"]}-{pr[CENTRES[1]+"solar"]})*{proportion}+{pr[cc+"solar"]}'
            elif field=='depreciation':formula=f'SUMIF({ar("A")},{quote(cc)},{ar("O")})'
            else:
                rent_ratio="'分摊参数'!$B$9" if cc==CENTRES[0] else "(1-'分摊参数'!$B$9)"
                formula=f'({pr["building"]}+{pr["property"]})*{rent_ratio}+SUMIFS({lr["费用借方金额"]},{lr["dept"]},{lookup(quote(cc),"分摊参数","H","K",len(centres))},{lr["expense"]},"员工宿舍")'
            poolrow(cc+field,cc,label+'分摊池',formula,result['inputs']['pools'][cc][field],'历史补充台账' if rules['basis']=='reference' else '金蝶入账口径；分摊至产品按车间内完工数量比例')
        direction='-' if cc==CENTRES[0] else '+'
        value=sum(p['other']-p['oil'] for p in products if p['cc']==cc)+(pv['transfer'] if direction=='+' else -pv['transfer'])
        poolrow(cc+'other',cc,'其他制造费用分摊池',f'SUMIF({ar("A")},{quote(cc)},{ar("M")})-SUMIF({ar("A")},{quote(cc)},{ar("L")}){direction}{pr["transfer"]}',value,'原制造其他费用减油，再作车间间共享领用转移')
    pool.autofilter(4,0,len(pr)+4,3)
    for i,p in enumerate(result['products'],6):
        a=lambda c:f"'产品公式归集'!{c}{i}"
        formula_by_key={'qty':a('F'),'material':f'{a("H")}+{a("L")}','packaging':a('Q'),'labor':a('I'),'indirect':a('J'),
                        'gas':f'{a("K")}+{pr["nitrogen"]}*{a("S")}' if p['cc']==CENTRES[0] else a('K'),
                        'other':f'{pr[p["cc"]+"other"]}*{a("S")}' if p['cc'] in CENTRES else f'{a("M")}-{a("L")}', 'wip':a('Y'),
                        'subcontract':a('Z')}
        for field in ('water','power','depreciation','rent'):
            formula_by_key[field]=f'{pr[p["cc"]+field]}*{a("S")}' if p['cc'] in CENTRES else '0'
        for c,(key,_) in enumerate(FIELDS[4:IDX['wip']+1],5):put(main,i,c,formula_by_key[key],p.get(key,0))
        main.write_comment(i-1,IDX['other'],'公式路径：其他出库/成本原始层级 → 产品公式归集及费用分摊依据 → 本格。共享领用只作车间间转移，不能重复加一次成本。')
    put(audit,12,2,f'SUM({ar("G")})',result['controls']['gold_total'])
    put(audit,13,2,f'SUM({ar("Y")})',result['controls']['wip_allocated'])
    pool_total='+'.join(pr[cc+f] for cc in CENTRES for f in ('water','power','depreciation','rent'))
    put(audit,14,2,f'{pool_total}-SUM({ar("N")})-SUM({ar("O")})-SUM({ar("P")})',result['controls']['source_difference'])
    put(audit,15,2,f'SUM({rng("产品全成本",LETTER["total"],count)})',result['controls']['total'])
    audit.write(17,1,'原始数据为本次金蝶接口取数字段原值；计算由Excel公式关联。包材未单拆，不代表未发生。跨月、新增行/产品应从工作台重新生成。')
    # 公式改动不改变已确认快照；单独提示源数据失配和本地编辑影响。
    extra=[('水电凭证与成本原表差额',f'{pr["water"]}+{pr["power"]}-SUM({ar("N")})',pv['water']+pv['power']-sum(p['gold_utilities'] for p in products)),
           ('费用来源差额（入账口径应为0）','B14',result['controls']['source_difference']),
           ('导出时全成本快照金额',None,result['controls']['total'])]
    start=max(audit.dim_rowmax+2,22)
    for offset,(label,formula,value) in enumerate(extra):
        audit.write(start+offset,0,label)
        if formula:put(audit,start+offset+1,2,formula,value)
        else:audit.write(start+offset,1,value,money)
    audit.write(start+3,0,'本地公式结果与导出快照差额')
    put(audit,start+4,2,f'B15-B{start+3}',0)
    audit.autofilter(4,0,start+3,1)
    unallocated=wb.get_worksheet_by_name('未分配在产')
    for i,row in enumerate(result['unallocated_wip'],6):
        key=f'A{i}'
        put(unallocated,i,2,f'SUMIFS({lr["在产调整金额"]},{lr["在产车间"]},LEFT({key},FIND("|",{key})-1),{lr["在产产品编码"]},MID({key},FIND("|",{key})+1,200))-SUMIFS({ar("Y")},{ar("A")},LEFT({key},FIND("|",{key})-1),{ar("B")},MID({key},FIND("|",{key})+1,200))',row['amount'])
