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
BUCKETS = {'receipt':'交易收款', 'refund':'交易退款', 'fee':'平台费用',
    'ufirst_fee':'U先专属费用', 'qr':'收钱码收款', 'transfer':'内部划转候选',
    'recharge':'充值 / 划转候选', 'other':'其他已知费目', 'unknown':'待识别流水'}


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
    elif code == '0020001': bucket,reason='refund','交易退款费目 0020001'
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
        row.update(classify(row,fee_map),raw=raw,source={'file':filename,'sheet':sheet,'row':number},account=account)
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
