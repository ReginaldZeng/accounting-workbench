# V2.811: 抖音月结——两份动账明细的解析、与金蝶应收逐单对、收款单草稿。纯计算：不连金蝶、不碰库。
import calendar
import csv
import io
import math
import zipfile
from collections import defaultdict

KINDS = {'dy_settle': '抖音动账明细（订单维度）', 'dy_ledger': '抖音账户流水（带余额）'}
SETTLE = '货款结算入账'
# 结算时从货款里直接扣掉的费用列（文件里是负数）
FEE_COLUMNS = ['平台服务费', '佣金', '服务商佣金', '渠道分成', '招商服务费', '站外推广费', '其他分成']
_SETTLE_HEAD = {'动帐流水号', '动账方向', '动账金额', '动账场景', '订单号', '订单实付应结', '订单退款', '平台服务费'}
_LEDGER_HEAD = {'动账流水号', '账户方向', '动账金额(元)', '动账场景', '账户余额(元)'}
_INSURANCE_HEAD = {'保险单号', '动账流水号', '金额(元)'}
CATEGORIES = {'ok': '钱已到账·金额一致，可核销', 'mismatch': '钱已到账·金额对不上', 'pair': '结算前已退款·红字蓝字互冲即平',
              'overdue': '发货超过 {days} 天还没结算', 'transit': '在途·月底前还没结算', 'no_order': '应收单上没有平台订单号'}
BUCKETS = {'ok': '金额一致', 'mismatch': '金额对不上', 'written': '应收已核销过', 'missing': '金蝶里没有这单的应收'}


def _text(value):
    return str(value or '').strip().lstrip("'`").strip()


def _num(value):
    try: return float(str(value or 0).replace(',', '') or 0)
    except (TypeError, ValueError): return 0.0


def _decode(blob):
    for encoding in ('utf-8-sig', 'gb18030'):
        try: return blob.decode(encoding)
        except UnicodeDecodeError: continue
    raise ValueError('文件编码无法识别')


def _tables(blob, filename):
    """上传的可能是 csv，也可能是装着多个 csv 的 zip。逐个产出 (文件名, 行字典列表, 表头)。"""
    if zipfile.is_zipfile(io.BytesIO(blob)):
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            for info in z.infolist():
                if info.is_dir() or not info.filename.lower().endswith('.csv'): continue
                try: name = info.filename.encode('cp437').decode('gbk')
                except (UnicodeEncodeError, UnicodeDecodeError): name = info.filename
                yield from _tables(z.read(info), name.rsplit('/', 1)[-1])
        return
    reader = csv.DictReader(io.StringIO(_decode(blob), newline=''))
    yield filename, list(reader), set(reader.fieldnames or [])


def _settle_row(r):
    sign = 1 if _text(r.get('动账方向')) == '入账' else -1
    fees = {c: round(-_num(r.get(c)), 2) for c in FEE_COLUMNS if _num(r.get(c))}
    return {'id': _text(r.get('动帐流水号')), 't': _text(r.get('动账时间')), 'amt': round(sign * _num(r.get('动账金额')), 2),
            'scene': _text(r.get('动账场景')), 'btype': _text(r.get('计费类型')), 'order': _text(r.get('订单号')),
            'refund': round(_num(r.get('订单退款')), 2), 'fees': fees, 'memo': _text(r.get('备注'))[:60]}


def _ledger_row(r):
    return {'id': _text(r.get('动账流水号')), 't': _text(r.get('动账时间')), 'amt': round(_num(r.get('动账金额(元)')), 2),
            'scene': _text(r.get('动账场景')), 'order': _text(r.get('关联订单号')), 'bal': round(_num(r.get('账户余额(元)')), 2),
            'memo': _text(r.get('备注'))[:60]}


def parse(blob, filename):
    """只看表头认资料类型，不看文件名。返回 [{name, kind, rows|skip}]；认不出抛 ValueError。"""
    out = []
    for name, rows, head in _tables(blob, filename):
        if _SETTLE_HEAD <= head: kind, make = 'dy_settle', _settle_row
        elif _LEDGER_HEAD <= head: kind, make = 'dy_ledger', _ledger_row
        elif _INSURANCE_HEAD <= head:
            out.append({'name': name, 'kind': '', 'skip': '保费明细已含在账户流水里，不单独接入'}); continue
        else: raise ValueError('「%s」表头不是抖音动账明细，也不是带余额的账户流水' % name)
        parsed = [dict(x, n=i) for i, x in enumerate(map(make, rows)) if x['id'] and len(x['t']) >= 7]      # n=文件内行序
        if not parsed: raise ValueError('「%s」里没有可用的流水行' % name)
        out.append({'name': name, 'kind': kind, 'rows': parsed})
    if not out: raise ValueError('压缩包里没有 csv 文件')
    return out


def by_month(rows):
    months = defaultdict(list)
    for r in rows: months[r['t'][:7]].append(r)
    return dict(months)


def merge(old, new):
    """同一流水号只留一份（新文件覆盖旧的），按时间排好。"""
    rows = {r['id']: r for r in old}
    rows.update({r['id']: r for r in new})
    return sorted(rows.values(), key=lambda r: (r['t'], r.get('n', 0), r['id']))


def chain(rows):
    """账户流水按余额链排好：同一秒内常有多笔，时间排不出先后，就按"上一笔余额 + 本笔金额 = 本笔余额"接龙。
    返回 (排好的行, 接不上的断点数)。"""
    out, breaks, prev, i = [], 0, None, 0
    while i < len(rows):
        j = i
        while j < len(rows) and rows[j]['t'] == rows[i]['t']: j += 1
        group = rows[i:j]; i = j
        if prev is None and len(group) > 1:                              # 开头：找没有前一笔的那行当起点
            ends = [round(r['bal'], 2) for r in group]
            start = next((r for r in group if round(r['bal'] - r['amt'], 2) not in ends), group[0])
            prev = round(start['bal'] - start['amt'], 2)
        while group:
            pick = next((r for r in group if prev is None or abs(r['bal'] - r['amt'] - prev) < 0.006), None)
            if pick is None: pick = group[0]; breaks += 1
            group.remove(pick); out.append(pick); prev = pick['bal']
    return out, breaks


def period_end(period):
    y, m = int(period[:4]), int(period[5:7])
    return '%s-%02d' % (period, calendar.monthrange(y, m)[1])


def _days_before(day, days):
    import datetime
    d = datetime.date(int(day[:4]), int(day[5:7]), int(day[8:10])) - datetime.timedelta(days=days)
    return d.isoformat()


def _gross(row):
    """这笔结算对应该冲掉多少应收 = 到账 + 结算时扣的费用（即 实付 + 各项补贴 − 结算时退款）。"""
    return row['amt'] + math.fsum(row['fees'].values())


def reconcile(period, settle, ledger, bills, overdue_days=10, tolerance=0.01):
    """period 月结：settle / ledger 为截至该期末的流水（可含更早月份），bills 为该店金蝶应收（全部状态）。"""
    end = period_end(period); late = _days_before(end, overdue_days)
    settle = [r for r in settle if r['t'][:7] <= period]
    ledger, breaks = chain([r for r in ledger if r['t'][:7] <= period])
    in_period = [r for r in settle if r['t'][:7] == period]
    # 每个订单：本期结算应冲额、截至期末累计应冲额
    now, total = defaultdict(float), defaultdict(float)
    proof = {True: defaultdict(lambda: [0.0, 0.0, '']), False: defaultdict(lambda: [0.0, 0.0, ''])}      # 依据：到账 / 扣费 / 结算时间（True=本期，False=累计）
    for r in settle:
        if r['scene'] != SETTLE or not r['order']: continue
        total[r['order']] += _gross(r)
        this = r['t'][:7] == period
        if this: now[r['order']] += _gross(r)
        for scope in ((True, False) if this else (False,)):
            p = proof[scope][r['order']]; p[0] += r['amt']; p[1] += math.fsum(r['fees'].values()); p[2] = max(p[2], r['t'])
    # 金蝶应收：只看业务日期在期末及以前的
    bills = [b for b in bills if b['date'] <= end]
    by_order = defaultdict(list)
    for b in bills:
        if b['order']: by_order[b['order']].append(b)
    rows, cats = [], {k: {'count': 0, 'orders': 0, 'amount': 0.0} for k in CATEGORIES}
    seen = set()
    for b in bills:
        if b['ws'] == 'C': continue
        group = [x for x in by_order[b['order']] if x['ws'] != 'C'] if b['order'] else [b]
        open_net = round(math.fsum(x['open'] for x in group), 2)
        expected, cash, fee, settled_at = None, None, None, ''
        if not b['order']: cat = 'no_order'
        elif b['order'] in total:
            expected = round(now[b['order']] if b['order'] in now else total[b['order']], 2)
            p = proof[b['order'] in now][b['order']]; cash, fee, settled_at = round(p[0], 2), round(p[1], 2), p[2]
            cat = 'ok' if abs(open_net - expected) <= tolerance else 'mismatch'
        elif len(group) > 1 and abs(open_net) <= tolerance and any(x['open'] < 0 for x in group): cat = 'pair'
        else: cat = 'overdue' if min(x['date'] for x in group) <= late else 'transit'
        rows.append(dict(no=b['no'], date=b['date'], order=b['order'], amount=b['amount'], written=b['written'], open=b['open'],
                         ws=b['ws'], ds=b['ds'], cat=cat, order_open=open_net, flow=expected, cash=cash, fee=fee, settled_at=settled_at,
                         diff=None if expected is None else round(open_net - expected, 2)))
        c = cats[cat]; c['count'] += 1; c['amount'] += b['open']
        if (cat, b['order'] or b['no']) not in seen: seen.add((cat, b['order'] or b['no'])); c['orders'] += 1
    for c in cats.values(): c['amount'] = round(c['amount'], 2)
    rows.sort(key=lambda r: (r['date'], r['no']), reverse=True)
    # 本期结算的订单，流水这边应冲多少、金蝶那边挂着多少——把两边的差逐桶摆出来
    buckets = {k: {'orders': 0, 'flow': 0.0, 'ar': 0.0} for k in BUCKETS}
    missing = []
    for order, flow in now.items():
        group = by_order.get(order, []); opened = [x for x in group if x['ws'] != 'C']
        ar = round(math.fsum(x['open'] for x in opened), 2)
        key = 'missing' if not group else 'written' if not opened else 'ok' if abs(ar - flow) <= tolerance else 'mismatch'
        b = buckets[key]; b['orders'] += 1; b['flow'] += flow; b['ar'] += ar
        if key in ('missing', 'written'): missing.append({'order': order, 'flow': round(flow, 2), 'bucket': key})
    for b in buckets.values(): b['flow'] = round(b['flow'], 2); b['ar'] = round(b['ar'], 2)
    missing.sort(key=lambda r: -abs(r['flow']))
    # 收款单草稿：到账行 + 一种扣款一行。到账行优先用带余额的账户流水，没有则退回订单维度明细
    ledger_now = [r for r in ledger if r['t'][:7] == period]
    source = ledger_now or in_period
    net = round(math.fsum(r['amt'] for r in source), 2)
    lines = defaultdict(float)
    for r in in_period:
        if r['scene'] == SETTLE:
            for name, value in r['fees'].items(): lines[name] += value
    for r in source:
        if r['scene'] != SETTLE: lines[r['scene'] or '未注明场景'] -= r['amt']
    deductions = [{'name': k, 'amount': round(v, 2)} for k, v in sorted(lines.items(), key=lambda x: -abs(x[1])) if round(v, 2)]
    draft = {'net': net, 'deductions': deductions, 'deduction_total': round(math.fsum(d['amount'] for d in deductions), 2),
             'from_ledger': bool(ledger_now)}
    draft['total'] = round(net + draft['deduction_total'], 2)
    draft['settled_gross'] = round(math.fsum(now.values()), 2)          # 本期结算订单应冲额合计，应等于 total
    # 余额：期初 / 期末 / 链是否连续
    balance = None
    if ledger_now:
        balance = {'open': round(ledger_now[0]['bal'] - ledger_now[0]['amt'], 2), 'close': ledger_now[-1]['bal'],
                   'net': net, 'breaks': breaks, 'rows': len(ledger_now), 'first': ledger_now[0]['t'], 'last': ledger_now[-1]['t']}
    return {'period': period, 'end': end, 'overdue_days': overdue_days, 'bills': rows, 'categories': cats, 'buckets': buckets,
            'missing': missing, 'draft': draft, 'balance': balance,
            'coverage': {'settle_rows': len(in_period), 'settled_orders': len(now), 'ledger_rows': len(ledger_now),
                         'settle_first': in_period[0]['t'] if in_period else '', 'settle_last': in_period[-1]['t'] if in_period else '',
                         'open_bills': len(rows)}}


def order_detail(period, settle, ledger, bills, order):
    """一个订单的全部依据：抖音这边每一笔动账（到账、各项扣费、当时余额），金蝶那边每一张应收单。"""
    end = period_end(period)
    balance = {r['id']: r['bal'] for r in ledger}
    flows = []
    for r in settle:
        if r['order'] != order or r['t'][:7] > period: continue
        settled = r['scene'] == SETTLE
        flows.append({'id': r['id'], 't': r['t'], 'scene': r['scene'], 'btype': r.get('btype', ''), 'amt': r['amt'], 'fees': r['fees'],
                      'fee': round(math.fsum(r['fees'].values()), 2), 'refund': r['refund'], 'gross': round(_gross(r), 2) if settled else None,
                      'bal': balance.get(r['id']), 'memo': r['memo'], 'in_period': r['t'][:7] == period})
    own = sorted((b for b in bills if b['order'] == order and b['date'] <= end), key=lambda b: (b['date'], b['no']))
    opened = [b for b in own if b['ws'] != 'C']
    return {'order': order, 'flows': flows, 'bills': own,
            'flow_total': round(math.fsum(f['gross'] for f in flows if f['gross'] is not None and f['in_period']), 2),
            'cash_total': round(math.fsum(f['amt'] for f in flows if f['gross'] is not None and f['in_period']), 2),
            'fee_total': round(math.fsum(f['fee'] for f in flows if f['gross'] is not None and f['in_period']), 2),
            'open_total': round(math.fsum(b['open'] for b in opened), 2)}


def category_label(cat, days):
    return CATEGORIES[cat].format(days=days)


def select(result, cat='', q=''):
    q = _text(q).lower()
    return [b for b in result['bills'] if (not cat or b['cat'] == cat) and (not q or q in b['no'].lower() or q in b['order'])]


def page(rows, number=1, size=50):
    pages = max(1, -(-len(rows) // size)); number = min(max(1, number), pages)
    return {'rows': rows[(number - 1) * size:number * size], 'total': len(rows), 'page': number, 'pages': pages,
            'amount': round(math.fsum(b['open'] for b in rows), 2)}


def memo(period, name, amount):
    """扣款行摘要，照现行收款单的写法。"""
    return '%s年%s月结算单扣款项 %s%.2f元' % (period[:4], period[5:7], name, amount)


def export(result, shop, book=None):
    """导出 Excel：收款单草稿 + 各类应收清单（可核销的那页就是下推用的源单）。"""
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font
    wb = Workbook(write_only=True); bold = Font(bold=True)
    def head(ws, values):
        cells = []
        for v in values:
            c = WriteOnlyCell(ws, value=v); c.font = bold; cells.append(c)
        ws.append(cells)
    days, d, period = result['overdue_days'], result['draft'], result['period']
    ws = wb.create_sheet('收款单草稿')
    ws.append(['%s · %s 收款单草稿（只供照着做，系统不写金蝶）' % (shop, period)])
    ws.append([])
    head(ws, ['行', '结算方式', '收款账户', '金额', '摘要'])
    ws.append([1, '支付宝', (book or {}).get('account', ''), d['net'], '本月账户净变动（到账）'])
    for i, x in enumerate(d['deductions'], 2): ws.append([i, '内部转销', '', x['amount'], memo(period, x['name'], x['amount'])])
    ws.append(['', '', '收款明细合计', d['total'], '应等于源单应收合计'])
    ws.append([])
    head(ws, ['本月结算的订单', '订单数', '流水应冲应收', '金蝶挂着的应收', '两边差'])
    for k, label in BUCKETS.items():
        b = result['buckets'][k]; ws.append([label, b['orders'], b['flow'], b['ar'], round(b['flow'] - b['ar'], 2)])
    if result['balance']:
        bal = result['balance']; ws.append([])
        head(ws, ['账户余额', '流水', '金蝶账面'])
        ws.append(['期初', bal['open'], (book or {}).get('open')]); ws.append(['期末', bal['close'], (book or {}).get('close')])
        ws.append(['本月净变动', bal['net'], '']); ws.append(['余额链断点', bal['breaks'], ''])
    titles = {'ok': '可核销(下推源单)', 'mismatch': '金额对不上', 'pair': '红蓝互冲', 'overdue': '该结没结', 'transit': '在途', 'no_order': '无订单号'}
    for cat, title in titles.items():
        rows = select(result, cat)
        if not rows: continue
        ws = wb.create_sheet(title)
        ws.append([category_label(cat, days)])
        head(ws, ['应收单号', '业务日期', '平台订单号', '应收金额', '已核销', '未核销', '该订单未核销合计', '抖音结算时间', '到账金额', '结算时扣费', '流水应冲额(到账+扣费)', '差额'])
        for b in rows: ws.append([b['no'], b['date'], b['order'], b['amount'], b['written'], b['open'], b['order_open'], b['settled_at'], b['cash'], b['fee'], b['flow'], b['diff']])
    if result['missing']:
        ws = wb.create_sheet('流水有·应收对不上号')
        head(ws, ['平台订单号', '流水应冲额', '情况'])
        for m in result['missing']: ws.append([m['order'], m['flow'], BUCKETS[m['bucket']]])
    buf = io.BytesIO(); wb.save(buf)
    return buf.getvalue()
