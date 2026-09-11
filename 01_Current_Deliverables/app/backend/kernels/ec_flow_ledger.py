"""Searchable statement evidence. No Kingdee calls; never silently discard unknown columns.

Business buckets and risk flags are independent from order reconciliation buckets.
No-serial rows are retained even when their content matches another file.
"""
import hashlib
import io
import json
import re
import zipfile
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET
import openpyxl
from kernels import ec_settle as es
from kernels import ec_tmall_import as tm

ALIASES = {
    'ts': ('入账时间', '记账时间', '发生时间'),
    'serial': ('支付宝流水号', '账务流水号', '流水号'),
    'txn': ('支付宝交易号', '交易号'),
    'mch_no': ('商户订单号',), 'order_no': ('业务基础订单号', '淘宝订单编号'),
    'btype': ('账务类型', '业务类型'), 'chan': ('支付渠道',),
    'income': ('收入（+元）', '收入(+元)', '收入金额（元）'),
    'outgo': ('支出（-元）', '支出(-元)', '支出金额（元）'),
    'balance': ('账户余额（元）', '余额（元）', '账户余额', '余额'),
    'goods': ('商品名称',), 'peer': ('对方名称',),
    'desc': ('业务描述',), 'remark': ('备注', '摘要'),
}
BUCKETS = {'receipt':'交易收款', 'refund':'交易退款', 'fee':'平台费用', 'adjustment':'补贴 / 调整',
    'ufirst_fee':'U先专属费用', 'qr':'收钱码收款', 'transfer':'内部划转候选',
    'recharge':'充值 / 划转候选', 'other':'其他已知费目', 'unknown':'待识别流水'}
RULE_FIELDS = {'remark':'备注 / 摘要', 'desc':'业务描述', 'btype':'账务类型', 'goods':'商品名称'}
RULE_DIRECTIONS = {'outgo':'支出', 'income':'收入', '':'不限'}
DEFAULT_CLASS_RULES = [{
    'id':'cat_coin_fee', 'name':'猫猫币平台垫付扣款', 'enabled':True,
    'account_kind':'alipay', 'field':'remark',
    'keywords':['猫猫币抵扣项目平台垫付资金', '扣款'],
    'direction':'outgo', 'bucket':'fee', 'label':'猫猫币抵扣费用',
}]


def string(value):
    return '' if value is None else str(value).strip().lstrip("'")


def decimal(value, nullable=False):
    s = string(value).replace(',', '')
    if s in ('', '--', '—', '-'):
        return None if nullable else Decimal('0')
    try:
        number = Decimal(s)
        if not number.is_finite(): raise ValueError()
        return number
    except (InvalidOperation, ValueError):
        raise ValueError('流水金额无法识别；该文件未导入')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def merchant_id(filename):
    # Export prefix is the 16-digit Alipay PID; '#账号' may be a different statement account identifier.
    match=re.match(r'^(2088\d{12})-', str(filename).replace('\\','/').rsplit('/',1)[-1])
    return match.group(1) if match else ''


def normalize_rules(rows):
    if not isinstance(rows, list) or len(rows)>50: raise ValueError('流水分类规则最多 50 条')
    result=[];seen=set()
    for source in rows:
        if not isinstance(source, dict): raise ValueError('流水分类规则格式错误')
        rid=string(source.get('id'))[:40]
        name=string(source.get('name'))[:80]
        field=string(source.get('field'))
        direction=string(source.get('direction'))
        account_kind=string(source.get('account_kind'))
        bucket=string(source.get('bucket'))
        keywords=source.get('keywords', [])
        if isinstance(keywords, str): keywords=[v.strip() for v in keywords.split('|')]
        keywords=[string(v)[:120] for v in keywords if string(v)] if isinstance(keywords,list) else []
        if not rid or rid in seen or not re.fullmatch(r'[A-Za-z0-9_-]+',rid): raise ValueError('流水分类规则编号须唯一且仅含字母、数字、横线或下划线')
        if not name or field not in RULE_FIELDS or direction not in RULE_DIRECTIONS or account_kind not in ('','alipay','fund'):
            raise ValueError('流水分类规则名称、账户、字段或方向不完整')
        if bucket not in BUCKETS or bucket=='unknown' or not keywords or len(keywords)>6:
            raise ValueError('流水分类规则须填写 1–6 个关键词并选择明确分桶')
        seen.add(rid);result.append({'id':rid,'name':name,'enabled':bool(source.get('enabled',True)),
            'account_kind':account_kind,'field':field,'keywords':keywords,'direction':direction,
            'bucket':bucket,'label':string(source.get('label'))[:80] or BUCKETS[bucket]})
    return result


def rule_matches(row, rule, account_kind=''):
    if not rule.get('enabled') or row.get('bucket')!='unknown': return False
    if rule.get('account_kind') and rule['account_kind']!=account_kind: return False
    if rule.get('direction')=='outgo' and not decimal(row.get('outgo')): return False
    if rule.get('direction')=='income' and not decimal(row.get('income')): return False
    text=string(row.get(rule['field'])).casefold()
    return all(word.casefold() in text for word in rule['keywords'])


def apply_rule(row, rule):
    result=dict(row);result['bucket']=rule['bucket'];result['reason']='流水分类规则：'+rule['name']
    result['rule_id']=rule['id'];result['rule_label']=rule['label']
    result['flags']=[f for f in result.get('flags',[]) if f!='流水待识别']
    return result


def apply_configured_rules(row, rules, account_kind=''):
    matches=[rule for rule in rules if rule_matches(row,rule,account_kind)]
    if len(matches)==1:return apply_rule(row,matches[0])
    if len(matches)>1:
        result=dict(row);result['flags']=list(result.get('flags',[]))
        if '分类规则冲突' not in result['flags']:result['flags'].append('分类规则冲突')
        return result
    return row


def supplement(row):
    """Read-time evidence only, including historical imports. Never change raw fields or accounting."""
    remark=string(row.get('remark')); merchant=string(row.get('mch_no'))
    evidence=[]
    # Only explicit order-bearing templates; arbitrary long IDs may be transactions or accounts.
    for match in re.finditer(r'猫猫币抵扣项目平台垫付资金\s*[（(]\s*([0-9]{15,24})\s*[）)]\s*扣款',remark):
        evidence.append({'order_no':match.group(1),'source':'备注','text':match.group(0)})
    hit=re.fullmatch(r'T200P([0-9]{15,24})',merchant)
    if hit:evidence.append({'order_no':hit.group(1),'source':'商户订单号','text':merchant})
    candidates=sorted({e['order_no'] for e in evidence})
    original=string(row.get('order_no'))
    original=original if original!='0' else ''
    conflict=len(set(candidates+([original] if original else [])))>1
    label='猫猫币抵扣项目平台垫付资金扣款' if any(e['source']=='备注' for e in evidence) and not string(row.get('desc')) else ''
    derived={'business_label':label,'business_source':'备注' if label else '',
        'order_candidate':candidates[0] if len(candidates)==1 and not conflict else '',
        'order_sources':list(dict.fromkeys(e['source'] for e in evidence)),
        'status':'conflict' if conflict else 'corroborates' if original and candidates else 'candidate' if candidates else 'none',
        'evidence':evidence,'notice':'补充识别仅供查找，未确认订单归属及费用性质，不参与自动核销。'}
    return dict(row,supplement=derived)


def _cells_xml(data):
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('不支持包含外部实体的流水 XML')
    sheet = ''; number = 0
    for event, elem in ET.iterparse(io.BytesIO(data), events=('start','end')):
        tag = elem.tag.rsplit('}',1)[-1]
        if event == 'start' and tag == 'Worksheet':
            sheet = next((v for k,v in elem.attrib.items() if k.rsplit('}',1)[-1]=='Name'), '')
            number = 0
        if event != 'end' or tag != 'Row': continue
        number += 1; cells=[]
        for cell in elem:
            if cell.tag.rsplit('}',1)[-1]!='Cell': continue
            index = next((int(v) for k,v in cell.attrib.items() if k.rsplit('}',1)[-1]=='Index'), len(cells)+1)
            if index > 256: raise ValueError('流水列数超过 256 列')
            while len(cells)<index-1: cells.append('')
            cells.append(next((c.text or '' for c in cell if c.tag.rsplit('}',1)[-1]=='Data'), ''))
        elem.clear()
        yield sheet, number, cells


def _cells(data):
    tm._preflight_alipay_file(data)
    if data[:2] == b'PK':
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names=archive.namelist()
                if '[Content_Types].xml' not in names:
                    candidates=[n for n in names if n.lower().endswith(('.xls','.xml')) and not n.endswith('/')]
                    if len(candidates)!=1: raise ValueError('流水压缩包须包含一个账务明细文件')
                    yield from _cells_xml(archive.read(candidates[0])); return
        except UnicodeDecodeError:
            # Existing bounded parser handles exports whose Chinese ZIP name has a wrong UTF-8 flag.
            import tempfile, os
            from kernels.bank_import import _read_broken_zip
            with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as temp:
                temp.write(data); path=temp.name
            try: inner=_read_broken_zip(path)
            finally: os.unlink(path)
            yield from _cells_xml(inner); return
        book=openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            for sheet in book:
                for number,row in enumerate(sheet.iter_rows(values_only=True),1):
                    if len(row)>256: raise ValueError('流水列数超过 256 列')
                    yield sheet.title, number, row
        finally: book.close()
    else:
        yield from _cells_xml(data)


def classify(row, fee_map):
    code, label = es._code_of(row['desc']); flags=[]
    bucket='unknown'; reason='业务描述不含可识别费目码'
    if code == '0010001': bucket,reason='receipt','交易收款费目 0010001'
    elif code in ('0020001','0020002'): bucket,reason='refund','交易退款费目 '+code
    elif code in ('008000200003','008002800014','008002800015'):
        bucket,reason='transfer','保证金划转费目；单独核对，不作为平台费用'
        flags.append('保证金划转性质待确认')
    elif code == '0240004T':bucket,reason='adjustment','百亿补贴激励前返，补贴单列'
    elif code in es.UFIRST_FEE_CODES: bucket,reason='ufirst_fee','U先专属费目信号；不等于整笔订单都是U先'
    elif code in fee_map:
        category=fee_map[code].get('category','')
        bucket='fee' if category=='费用' or code.startswith(('003','006','017')) else 'other'
        reason='已知费目 '+code
    elif not code and row['btype']=='在线支付' and row['goods']==es.DEFAULT_RULES['qr_goods']:
        bucket,reason='qr','沿用收钱码规则，单独留痕；不删除流水'
    elif not code and row['btype']=='转账' and row['income']>=Decimal(str(es.DEFAULT_RULES['inner_min'])):
        bucket,reason='transfer','沿用大额转账规则，仅候选，需核对对方账户'
        flags.append('划转性质待确认')
    elif not code and label:
        bucket,reason='recharge','沿用无费目有描述规则，仅候选，不自动作为费用或收入'
        flags.append('充值划转性质待确认')
    if code and code not in fee_map: flags.append('未知费目')
    if code in fee_map and (fee_map[code].get('account') or '待定')=='待定': flags.append('会计科目待映射')
    if bucket=='unknown': flags.append('流水待识别')
    if not row['serial']: flags.append('缺少流水号：不自动跨文件去重')
    if not row['order_no'] or row['order_no']=='0':
        if bucket in ('receipt','refund','ufirst_fee'): flags.append('订单关联号缺失')
    if row['income'] and row['outgo']: flags.append('同笔同时有收入和支出')
    if row['income']<0 or row['outgo']<0: flags.append('负数收支：核对冲正口径')
    if row['chan']=='聚合结算渠道': flags.append('聚合渠道映射：不得与聚合账户重复计款')
    return dict(code=code, label=label, bucket=bucket, reason=reason, flags=flags)


def parse(data, filename, fee_map):
    """Return full header-labelled statement fields, exact decimals and source coordinates."""
    headers=None; current=None; account=''; rows=[]; matched=False
    for sheet,number,cells in _cells(data):
        if current != sheet: headers=None;current=sheet
        texts=[string(c) for c in cells]
        for cell in texts:
            if cell.startswith('#账号'):
                hit=re.search(r'\[(\d+)\]',cell)
                if hit: account=hit.group(1)
        if any(h in texts for h in ALIASES['ts']) and '账务类型' in texts:
            if len([h for h in texts if h])!=len(set(h for h in texts if h)):
                raise ValueError('流水表头重名，无法无损解析')
            headers=texts; matched=True
            for key in ('income','outgo','desc'):
                if not any(h in headers for h in ALIASES[key]): raise ValueError('流水缺少必要列：'+ALIASES[key][0])
            continue
        if headers is None: continue
        raw={h:string(cells[i]) if i<len(cells) else '' for i,h in enumerate(headers) if h}
        for key in ('serial','txn','mch_no','order_no'):
            for name in ALIASES[key]:
                if name in headers:
                    index=headers.index(name); value=cells[index] if index<len(cells) else None
                    if isinstance(value,(int,float)) and len(string(value))>=15:
                        raise ValueError('流水长编号以数值格式保存，精度可能丢失；请按文本重新导出')
        row={key:next((raw[h] for h in names if h in raw),'') for key,names in ALIASES.items()}
        if not row['ts'] or row['ts'].startswith(('#','合计')): continue
        date=tm._date_text(row['ts'])
        if not re.match(r'^\d{4}-\d{2}-\d{2}',date):
            if any(row[k] for k in ('serial','income','outgo')): raise ValueError('流水时间无法识别，未跳过该记录')
            continue
        row.update(ts=date, income=decimal(row['income']),outgo=decimal(row['outgo']),balance=decimal(row['balance'],True))
        row.update(classify(row,fee_map),raw=raw,source={'file':filename,'sheet':sheet,'row':number},account=account,account_pid=merchant_id(filename))
        # The source row number is not business identity, and may change across exports.
        semantic={k:v for k,v in raw.items() if k not in ('序号','行号')}
        row['fingerprint']=fingerprint(semantic)
        rows.append(row)
        if len(rows)>500000:raise ValueError('单次流水最多 500,000 行')
    if not matched or not rows:raise ValueError('未识别到支付宝账务流水明细')
    accounts={r['account'] for r in rows if r['account']}
    if len(accounts)>1:raise ValueError('文件内有多个账户，请分账户导入')
    return rows


def parse_fund(data, filename, fee_map):
    from kernels.ec_workbench import sheet_rows, choose
    result=[]
    for number, source in enumerate(sheet_rows(data),2):
        if not source.get('入账时间'):continue
        if not all(k in source for k in ('支付流水号','淘宝订单编号','入账类型','收入金额（元）','支出金额')):
            raise ValueError('聚合流水缺少必要表头')
        for key in ('支付流水号','淘宝订单编号'):
            value=source.get(key)
            if isinstance(value,(int,float)) and len(string(value))>=15:
                raise ValueError('聚合流水长编号须按文本导出，避免精度丢失')
        row={key:'' for key in ALIASES}
        row.update(ts=tm._date_text(source['入账时间']),serial=string(source.get('支付流水号')),
            order_no=string(source.get('淘宝订单编号')),btype=string(source.get('入账类型')),
            desc=string(source.get('业务描述')),chan='聚合账户',income=decimal(source.get('收入金额（元）')),
            outgo=decimal(source.get('支出金额')),balance=decimal(choose(source,'账户余额（元）','账户余额','余额（元）','余额'),True))
        if not row['ts']:raise ValueError('聚合流水入账时间无法识别')
        raw={str(k):string(v) for k,v in source.items()}
        row.update(classify(row,fee_map),raw=raw,source={'file':filename,'sheet':'首个工作表','row':number},account='')
        if row['btype']=='交易收款':row.update(bucket='receipt',reason='聚合账户入账类型：交易收款')
        elif '退款' in row['btype']:row.update(bucket='refund',reason='聚合账户退款入账类型')
        row['fingerprint']=fingerprint({k:v for k,v in raw.items() if k not in ('序号','行号')})
        result.append(row)
    if not result:raise ValueError('未读取到聚合账户流水')
    return result
