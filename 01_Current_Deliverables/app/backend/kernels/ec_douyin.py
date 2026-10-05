# V2.811: 抖音月结——两份动账明细的解析、与金蝶应收逐单对、收款单草稿。纯计算：不连金蝶、不碰库。
import calendar
import csv
import html
import io
import math
import re
import zipfile
from collections import defaultdict

KINDS = {'dy_settle': '抖音动账明细（订单维度）', 'dy_ledger': '抖音账户流水（带余额）', 'dy_orders': '旺店通订单明细（认合单）'}
SETTLE = '货款结算入账'
# 结算时从货款里直接扣掉的费用列（文件里是负数）
FEE_COLUMNS = ['平台服务费', '佣金', '服务商佣金', '渠道分成', '招商服务费', '站外推广费', '其他分成']
SUBSIDY_COLUMNS = ['实际平台补贴_运费', '实际平台补贴', '其他平台补贴', '以旧换新抵扣', '政府补贴平台垫资', '实际达人补贴', '实际抖音支付补贴', '实际抖音月付营销补贴', '银行补贴']
FORMAT = 3                                  # 解析出的字段变了就加一：让同一份文件重新上传时能覆盖旧快照
_SETTLE_HEAD = {'动帐流水号', '动账方向', '动账金额', '动账场景', '订单号', '订单实付应结', '订单退款', '平台服务费'}
_LEDGER_HEAD = {'动账流水号', '账户方向', '动账金额(元)', '动账场景', '账户余额(元)'}
_INSURANCE_HEAD = {'保险单号', '动账流水号', '金额(元)'}
_ORDERS_HEAD = {'订单编号', '店铺', '子单原始单号', '订单状态', '应收金额', '分摊后总价'}
# 结算后把钱退给买家 / 退回补贴：冲的是应收；其余结算后场景（分账、退分账）是费用的返还
REFUND_SCENES = {'退款-结算后退款-退用户', '退款-订单退款触发-退补贴'}
CATEGORIES = {'ok': '钱已到账·金额一致，可核销', 'mismatch': '钱已到账·金额对不上', 'pair': '结算前已退款·红字蓝字互冲即平',
              'overdue': '发货超过 {days} 天还没结算', 'transit': '在途·月底前还没结算', 'no_order': '应收单上没有平台订单号'}
BUCKETS = {'ok': '金额一致', 'refunded': '先结算后又退款（两边实际一致）', 'mismatch': '金额对不上', 'written': '应收已核销过', 'missing': '金蝶里没有这单的应收'}


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
            'refund': round(_num(r.get('订单退款')), 2), 'fees': fees, 'memo': _text(r.get('备注'))[:60],
            # V2.816 订单全链路要用：下单时间、商品、买家实付、各项补贴合计（实付 + 补贴 = 结算前的订单金额；运费不随货款结算，不计入）
            'ot': _text(r.get('下单时间')), 'goods': _text(r.get('商品名称'))[:60],
            'paid': round(_num(r.get('订单实付应结')), 2),
            'subsidy': round(math.fsum(_num(r.get(c)) for c in SUBSIDY_COLUMNS), 2)}


def _ledger_row(r):
    return {'id': _text(r.get('动账流水号')), 't': _text(r.get('动账时间')), 'amt': round(_num(r.get('动账金额(元)')), 2),
            'scene': _text(r.get('动账场景')), 'order': _text(r.get('关联订单号')), 'bal': round(_num(r.get('账户余额(元)')), 2),
            'memo': _text(r.get('备注'))[:60]}


def _platform_order(value):
    """旺店通的「子单原始单号」→ 平台订单号：去掉补发单的 A 后缀和拆单的 -1。不是平台订单号（手工单等）返回空串。"""
    text = _text(value)
    for _ in range(2):
        if text[-1:] in ('A', 'a'): text = text[:-1]
        head, _, tail = text.rpartition('-')
        if head and tail.isdigit() and len(tail) <= 2: text = head
    return text if text.isdigit() and len(text) >= 15 else ''


_CELL = re.compile(rb'<c r="([A-Z]+)\d+"([^>]*?)(?:/>|>(?:<is><t[^>]*>([^<]*)</t></is>|<v>([^<]*)</v>)?</c>)')
_SHARED = re.compile(rb'<si>(.*?)</si>', re.S)
_TEXT = re.compile(rb'<t[^>]*>([^<]*)</t>')


def _xlsx_rows(blob, must=b''):
    """流式读 xlsx 第一张表：按 </row> 切块，每行用正则取单元格，返回 {列序号: 文本}。
    旺店通一个月的订单明细解开有 200 多 MB，用 openpyxl 逐格读要 7 分钟，这样读约 15 秒。
    旺店通原样导出的是内联字符串；用 Excel 另存过的是共享字符串，两种都认。
    must 只对内联字符串的文件有用：给了就只要含这串字节的行（表头行总是给）。"""
    z = zipfile.ZipFile(io.BytesIO(blob))
    shared = []
    if 'xl/sharedStrings.xml' in z.namelist():
        shared = [html.unescape(b''.join(_TEXT.findall(m.group(1))).decode('utf-8')) for m in _SHARED.finditer(z.read('xl/sharedStrings.xml'))]
    if shared: must = b''
    sheet = sorted(n for n in z.namelist() if n.startswith('xl/worksheets/') and n.endswith('.xml'))[0]
    buf, first = b'', True
    with z.open(sheet) as f:
        while True:
            chunk = f.read(4 << 20)
            if not chunk: break
            buf += chunk; parts = buf.split(b'</row>'); buf = parts.pop()
            for part in parts:
                if first and b'&#' in part: must = b''                # 有的导出把汉字写成 &#数字; 实体，按字节筛行会全筛掉
                if not first and must and must not in part: continue
                first = False
                row = {}
                for m in _CELL.finditer(part):
                    n = 0
                    for ch in m.group(1): n = n * 26 + (ch - 64)
                    if b't="s"' in m.group(2): row[n - 1] = shared[int(m.group(4))] if m.group(4) else ''
                    else: row[n - 1] = html.unescape((m.group(3) or m.group(4) or b'').decode('utf-8'))
                yield row


def _orders(blob, filename, shop):
    """旺店通订单明细（xlsx）。只取核对要用的列，收件人、电话、地址等一概不留。
    一个「旺店通订单 × 平台订单」汇成一条：金蝶应收是照旺店通订单开的，一张里可能合了几个平台订单。"""
    it = _xlsx_rows(blob, html.escape(shop).encode('utf-8') if shop else b'')
    first = next(it, None) or {}
    at = {_text(name): i for i, name in first.items()}
    if not _ORDERS_HEAD <= set(at): raise ValueError('「%s」表头不是旺店通订单明细' % filename)
    cell = lambda row, k: row.get(at[k]) if k in at else None
    when = lambda row, k: _text(cell(row, k))[:19] if _text(cell(row, k))[:2] == '20' else ''
    agg = {}
    for i, row in enumerate(it):
        if shop and _text(cell(row, '店铺')) != shop: continue
        jy, order = _text(cell(row, '订单编号')), _platform_order(cell(row, '子单原始单号'))
        t = when(row, '交易时间') or when(row, '付款时间')
        if not jy or not order or not t: continue
        r = agg.get(jy + '|' + order)
        if r is None:
            r = agg[jy + '|' + order] = {'id': jy + '|' + order, 'jy': jy, 'order': order, 't': t, 'n': i, 'status': _text(cell(row, '订单状态')),
                                         'refund': _text(cell(row, '订单退款状态')), 'recv': round(_num(cell(row, '应收金额')), 2),
                                         'paid': round(_num(cell(row, '买家实付')), 2), 'ship': when(row, '发货时间'), 'amount': 0.0, 'qty': 0.0, 'goods': []}
        r['amount'] = round(r['amount'] + _num(cell(row, '分摊后总价')), 2); r['qty'] += _num(cell(row, '实发数量'))
        goods = _text(cell(row, '货品名称'))[:30]
        if goods and goods not in r['goods'] and len(r['goods']) < 4: r['goods'].append(goods)
    if not agg:
        raise ValueError(('「%s」里没有「%s」的订单，请确认导出时包含了这家店' % (filename, shop)) if shop else '「%s」里没有带平台订单号的订单' % filename)
    return list(agg.values())


def parse(blob, filename, shop=''):
    """只看表头认资料类型，不看文件名。返回 [{name, kind, rows|skip}]；认不出抛 ValueError。
    shop 只对旺店通订单明细有用：那份文件是全店铺的，只取这家店的行。"""
    if zipfile.is_zipfile(io.BytesIO(blob)) and '[Content_Types].xml' in zipfile.ZipFile(io.BytesIO(blob)).namelist():
        return [{'name': filename, 'kind': 'dy_orders', 'rows': _orders(blob, filename, shop)}]
    out = []
    for name, rows, head in _tables(blob, filename):
        if _SETTLE_HEAD <= head: kind, make = 'dy_settle', _settle_row
        elif _LEDGER_HEAD <= head: kind, make = 'dy_ledger', _ledger_row
        elif _INSURANCE_HEAD <= head:
            out.append({'name': name, 'kind': '', 'skip': '保费明细已含在账户流水里，不单独接入'}); continue
        else: raise ValueError('「%s」表头不是抖音动账明细、带余额的账户流水，也不是旺店通订单明细' % name)
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


def order_groups(orders):
    """旺店通会把同一买家的几个平台订单合成一张单发货，金蝶应收照合并后的单开、只记其中一个平台订单号。
    这里把"同一张旺店通订单里的平台订单"连成一组，对账按组对。返回 (取组函数, 订单→旺店通记录)。"""
    parent, info = {}, defaultdict(list)
    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    by_jy = defaultdict(list)
    for r in orders or []:
        info[r['order']].append(r)
        if r['status'] != '已取消': by_jy[r['jy']].append(r['order'])
    for members in by_jy.values():
        for o in members[1:]: parent[find(o)] = find(members[0])
    members = defaultdict(set)
    for o in list(parent): members[find(o)].add(o)
    return (lambda o: members[find(o)] if o in parent else {o}), info


def _why(open_net, expected, opened, refund_at, after, tolerance):
    """金额对不上的订单，属于哪一种。只描述两边的事实，不替人下结论。"""
    red = any(b['open'] < 0 for b in opened); diff = round(open_net - expected, 2)
    if abs(diff) <= 1: return '尾差 %.2f' % diff
    if red and abs(open_net) <= tolerance and abs(refund_at) < 0.005: return '金蝶红字蓝字已全冲掉，抖音没退款、全额结了 %.2f' % expected
    if red and abs(open_net) <= tolerance: return '金蝶红字蓝字已全冲掉，抖音只退了 %.2f、仍结了 %.2f' % (abs(refund_at), expected)
    if not red and abs(refund_at) >= 0.005: return '抖音结算时退了 %.2f，金蝶没开红字' % abs(refund_at)
    if abs(after) >= 0.005: return '抖音结算后又有退款／调整 %.2f，和金蝶红字对不上' % after
    if red: return '金蝶红字金额和抖音退的不一样，差 %.2f' % diff
    return '金蝶应收和抖音结算金额不一样，差 %.2f' % diff


def reconcile(period, settle, ledger, bills, orders=None, overdue_days=10, tolerance=0.01):
    """period 月结：settle / ledger 为截至该期末的流水（可含更早月份），bills 为该店金蝶应收（全部状态），
    orders 为旺店通订单明细（可没有：没有就认不出合单，合单的订单会落到"对不上"）。"""
    end = period_end(period); late = _days_before(end, overdue_days)
    settle = [r for r in settle if r['t'][:7] <= period]
    ledger, breaks = chain([r for r in ledger if r['t'][:7] <= period])
    in_period = [r for r in settle if r['t'][:7] == period]
    group, _ = order_groups(orders)
    gid = lambda o: min(group(o))
    # 每组订单：本期结算应冲额、截至期末累计应冲额，以及依据（到账 / 扣费 / 结算时间 / 结算时退款）
    now, total, settled_orders = defaultdict(float), defaultdict(float), set()
    proof = {True: defaultdict(lambda: [0.0, 0.0, '', 0.0]), False: defaultdict(lambda: [0.0, 0.0, '', 0.0])}
    after = defaultdict(float); refunded = defaultdict(float)
    for r in settle:
        if not r['order']: continue
        g = gid(r['order'])
        if r['scene'] != SETTLE:
            after[g] += r['amt']
            if r['scene'] in REFUND_SCENES: refunded[g] += r['amt']
            continue
        total[g] += _gross(r); settled_orders.add(r['order'])
        this = r['t'][:7] == period
        if this: now[g] += _gross(r)
        for scope in ((True, False) if this else (False,)):
            p = proof[scope][g]; p[0] += r['amt']; p[1] += math.fsum(r['fees'].values()); p[2] = max(p[2], r['t']); p[3] += r['refund']
    # 金蝶应收：只看业务日期在期末及以前的
    bills = [b for b in bills if b['date'] <= end]
    by_group = defaultdict(list)
    for b in bills:
        if b['order']: by_group[gid(b['order'])].append(b)
    rows, cats = [], {k: {'count': 0, 'orders': 0, 'amount': 0.0} for k in CATEGORIES}
    seen, verdict = set(), {}
    for b in bills:
        if b['ws'] == 'C': continue
        g = gid(b['order']) if b['order'] else ''
        opened = [x for x in by_group[g] if x['ws'] != 'C'] if g else [b]
        open_net = round(math.fsum(x['open'] for x in opened), 2)
        members = group(b['order']) if b['order'] else set()
        expected, cash, fee, settled_at, reason = None, None, None, '', ''
        if not b['order']: cat = 'no_order'
        elif g in total:
            scope = g in now
            expected = round(now[g] if scope else total[g], 2)
            p = proof[scope][g]; cash, fee, settled_at = round(p[0], 2), round(p[1], 2), p[2]
            if abs(open_net - expected) <= tolerance: cat = 'ok'
            elif refunded[g] and abs(open_net - (expected + refunded[g])) <= tolerance:
                cat, reason = 'pair', '抖音先结算、后来又把钱退回去了，两边实际都是 %.2f' % open_net
            elif len(members) > 1 and any(o not in settled_orders for o in members):
                cat, reason = 'transit', '合单发货的 %d 个订单里还有没结算的' % len(members)
            else: cat, reason = 'mismatch', _why(open_net, expected, opened, p[3], after[g], tolerance)
        elif len(opened) > 1 and abs(open_net) <= tolerance and any(x['open'] < 0 for x in opened): cat = 'pair'
        else: cat = 'overdue' if min(x['date'] for x in opened) <= late else 'transit'
        verdict[g or b['no']] = cat
        rows.append(dict(no=b['no'], date=b['date'], order=b['order'], amount=b['amount'], written=b['written'], open=b['open'],
                         ws=b['ws'], ds=b['ds'], cat=cat, order_open=open_net, flow=expected, cash=cash, fee=fee, settled_at=settled_at,
                         diff=None if expected is None else round(open_net - expected, 2), reason=reason, merged=len(members) if len(members) > 1 else 0))
        c = cats[cat]; c['count'] += 1; c['amount'] += b['open']
        if (cat, g or b['no']) not in seen: seen.add((cat, g or b['no'])); c['orders'] += 1
    for c in cats.values(): c['amount'] = round(c['amount'], 2)
    rows.sort(key=lambda r: (r['date'], r['no']), reverse=True)
    # 本期结算的订单，流水这边应冲多少、金蝶那边挂着多少——把两边的差逐桶摆出来（各桶流水之和 = 收款明细合计）
    buckets = {k: {'orders': 0, 'flow': 0.0, 'ar': 0.0} for k in BUCKETS}
    missing = []
    per_order = defaultdict(float)
    for r in in_period:
        if r['scene'] == SETTLE and r['order']: per_order[r['order']] += _gross(r)
    for g, flow in now.items():
        own = by_group.get(g, []); opened = [x for x in own if x['ws'] != 'C']
        ar = round(math.fsum(x['open'] for x in opened), 2)
        key = ('missing' if not own else 'written' if not opened else 'ok' if abs(ar - flow) <= tolerance
               else 'refunded' if verdict.get(g) == 'pair' else 'mismatch')
        members = [o for o in group(g) if o in per_order]
        b = buckets[key]; b['orders'] += len(members); b['flow'] += flow; b['ar'] += ar
        if key in ('missing', 'written'): missing += [{'order': o, 'flow': round(per_order[o], 2), 'bucket': key} for o in members]
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
    deductions = [{'name': k, 'amount': round(v, 2), 'kind': 'fee' if k in FEE_COLUMNS else 'other'}
                  for k, v in sorted(lines.items(), key=lambda x: -abs(x[1])) if round(v, 2)]
    draft = {'net': net, 'deductions': deductions, 'deduction_total': round(math.fsum(d['amount'] for d in deductions), 2),
             'from_ledger': bool(ledger_now)}
    draft['total'] = round(net + draft['deduction_total'], 2)
    draft['fee_total'] = round(math.fsum(d['amount'] for d in deductions if d['kind'] == 'fee'), 2)
    draft['other_total'] = round(math.fsum(d['amount'] for d in deductions if d['kind'] == 'other'), 2)
    draft['settle_cash'] = round(net + draft['other_total'], 2)          # 货款结算实际进账户的钱（平台已扣完费用）
    draft['settled_gross'] = round(math.fsum(now.values()), 2)          # 本期结算订单应冲额合计，应等于 total
    # 余额：期初 / 期末 / 链是否连续
    balance = None
    if ledger_now:
        balance = {'open': round(ledger_now[0]['bal'] - ledger_now[0]['amt'], 2), 'close': ledger_now[-1]['bal'],
                   'net': net, 'breaks': breaks, 'rows': len(ledger_now), 'first': ledger_now[0]['t'], 'last': ledger_now[-1]['t']}
    merged = {g for g in set(now) | set(by_group) if g and len(group(g)) > 1}
    return {'period': period, 'end': end, 'overdue_days': overdue_days, 'bills': rows, 'categories': cats, 'buckets': buckets,
            'missing': missing, 'draft': draft, 'balance': balance,
            'coverage': {'settle_rows': len(in_period), 'settled_orders': len(per_order), 'ledger_rows': len(ledger_now),
                         'settle_first': in_period[0]['t'] if in_period else '', 'settle_last': in_period[-1]['t'] if in_period else '',
                         'open_bills': len(rows), 'order_rows': len(orders or []), 'merged_groups': len(merged)}}


def order_detail(period, settle, ledger, bills, order, orders=None):
    """一个订单的全部依据：抖音这边每一笔动账（到账、各项扣费、当时余额），金蝶那边每一张应收单。
    合单发货的，把同组的平台订单一起列出来——金蝶应收是按合并后的那张单开的。"""
    end = period_end(period)
    group, info = order_groups(orders)
    members = sorted(group(order))
    balance = {r['id']: r['bal'] for r in ledger}
    flows = []
    for r in settle:
        if r['order'] not in members or r['t'][:7] > period: continue
        settled = r['scene'] == SETTLE
        flows.append({'id': r['id'], 't': r['t'], 'order': r['order'], 'scene': r['scene'], 'btype': r.get('btype', ''), 'amt': r['amt'], 'fees': r['fees'],
                      'fee': round(math.fsum(r['fees'].values()), 2), 'refund': r['refund'], 'gross': round(_gross(r), 2) if settled else None,
                      'bal': balance.get(r['id']), 'memo': r['memo'], 'in_period': r['t'][:7] == period})
    own = sorted((b for b in bills if b['order'] in members and b['date'] <= end), key=lambda b: (b['date'], b['no']))
    opened = [b for b in own if b['ws'] != 'C']
    settled = [f for f in flows if f['gross'] is not None]
    raw = [r for r in settle if r['order'] in members and r['t'][:7] <= period and r['scene'] == SETTLE]
    known = bool(raw) and all('paid' in r for r in raw)                 # 旧版解析存的快照没有实付 / 补贴，要重新上传才有
    total = lambda rows, key: round(math.fsum(r[key] for r in rows), 2)
    wdt = {}
    for o in members:
        for r in info.get(o, []):
            w = wdt.setdefault(r['jy'], {'jy': r['jy'], 'status': r['status'], 'refund': r['refund'], 'recv': r['recv'], 'paid': r['paid'], 'ship': r['ship'], 'orders': [], 'goods': []})
            w['orders'].append(o); w['goods'] += [x for x in r['goods'] if x not in w['goods']]
    return {'order': order, 'members': members, 'wdt': sorted(wdt.values(), key=lambda w: w['jy']), 'flows': flows, 'bills': own, 'settled': bool(settled),
            'unsettled': [o for o in members if o not in {f['order'] for f in settled}] if len(members) > 1 else [],
            'flow_total': total([f for f in settled if f['in_period']] or settled, 'gross'),
            'cash_total': total(settled, 'amt'), 'fee_total': total(settled, 'fee'), 'refund_total': abs(total(settled, 'refund')),
            'after_total': total([f for f in flows if f['gross'] is None], 'amt'),
            'paid_total': total(raw, 'paid') if known else None, 'subsidy_total': total(raw, 'subsidy') if known else None,
            'ordered_at': min((r['ot'] for r in raw if r.get('ot')), default=''), 'goods': list(dict.fromkeys(r['goods'] for r in raw if r.get('goods'))),
            'settled_at': max((f['t'] for f in settled), default=''), 'btype': next((f['btype'] for f in settled if f['btype']), ''),
            'ar_total': total(own, 'amount'), 'written_total': total(own, 'written'), 'open_total': total(opened, 'open')}


def category_label(cat, days):
    return CATEGORIES[cat].format(days=days)


def select(result, cat='', q=''):
    q = _text(q).lower()
    hold = cat == 'hold'                                                # 可核销、但系统不下推的（订单带红字等）
    return [b for b in result['bills'] if (not cat or (b.get('hold') if hold else b['cat'] == cat)) and (not q or q in b['no'].lower() or q in b['order'])]


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
    ws = wb.create_sheet('本月账户进出汇总')
    ws.append(['%s · %s 账户进出汇总（用来和账户余额核对，不是收款单）' % (shop, period)])
    ws.append([])
    head(ws, ['项目', '金额', '说明'])
    ws.append(['货款结算到账', d['settle_cash'], '平台已扣完费用后进账户的钱'])
    for x in d['deductions']:
        if x['kind'] == 'other': ws.append(['　' + x['name'], -x['amount'], '不挂在订单上的账户进出，不在任何一张下推的收款单里'])
    ws.append(['本月账户净变动', d['net'], '应等于账户期末余额 − 期初余额'])
    ws.append([])
    head(ws, ['结算时平台直接扣掉的费用（不经过账户）', '金额'])
    for x in d['deductions']:
        if x['kind'] == 'fee': ws.append([x['name'], x['amount']])
    ws.append(['合计', d['fee_total']])
    ws.append([])
    head(ws, ['本月结算的订单', '订单数', '流水应冲应收', '金蝶挂着的应收', '两边差'])
    for k, label in BUCKETS.items():
        b = result['buckets'][k]; ws.append([label, b['orders'], b['flow'], b['ar'], round(b['flow'] - b['ar'], 2)])
    if result['balance']:
        bal = result['balance']; ws.append([])
        head(ws, ['账户余额', '流水', '金蝶账面'])
        ws.append(['期初', bal['open'], (book or {}).get('open')]); ws.append(['期末', bal['close'], (book or {}).get('close')])
        ws.append(['本月净变动', bal['net'], '']); ws.append(['余额链断点', bal['breaks'], ''])
    COLUMNS = ['应收单号', '业务日期', '平台订单号', '应收金额', '已核销', '未核销', '该订单(组)未核销合计', '抖音结算时间', '到账金额', '结算时扣费', '流水应冲额(到账+扣费)', '差额', '合单订单数', '情况说明']
    line = lambda b: [b['no'], b['date'], b['order'], b['amount'], b['written'], b['open'], b['order_open'], b['settled_at'], b['cash'], b['fee'], b['flow'], b['diff'], b['merged'] or '', b['reason']]
    todo = [b for b in result['bills'] if b['cat'] in ('mismatch', 'overdue', 'no_order')]
    if todo:
        ws = wb.create_sheet('交人工处理')
        ws.append(['系统判断不了、要人看的应收单：金额对不上的写明了两边各是什么情况；该结没结的要查买家是否一直没确认或已退款。'])
        head(ws, ['分类'] + COLUMNS)
        for b in sorted(todo, key=lambda b: (b['cat'], b['reason'][:8], b['date'])): ws.append([category_label(b['cat'], days)] + line(b))
    hold = [b for b in result['bills'] if b.get('hold')]
    if hold:
        ws = wb.create_sheet('可核销但系统不推')
        ws.append(['金额对得上，但系统不替你下推的应收单（订单带红字的要先红蓝对冲）。'])
        head(ws, ['原因'] + COLUMNS)
        for b in sorted(hold, key=lambda b: (b['hold'], b['order'], b['no'])): ws.append([PUSH_SKIP[b['hold']]] + line(b))
    titles = {'ok': '可核销(下推源单)', 'mismatch': '金额对不上', 'pair': '红蓝互冲', 'overdue': '该结没结', 'transit': '在途', 'no_order': '无订单号'}
    for cat, title in titles.items():
        rows = select(result, cat)
        if not rows: continue
        ws = wb.create_sheet(title)
        ws.append([category_label(cat, days)])
        head(ws, COLUMNS)
        for b in rows: ws.append(line(b))
    if result['missing']:
        ws = wb.create_sheet('流水有·应收对不上号')
        head(ws, ['平台订单号', '流水应冲额', '情况'])
        for m in result['missing']: ws.append([m['order'], m['flow'], BUCKETS[m['bucket']]])
    buf = io.BytesIO(); wb.save(buf)
    return buf.getvalue()


# ---------------- 下推收款单（V2.821）：只挑最干净的应收，分批备成金蝶暂存收款单 ----------------
PUSH_SKIP = {'red': '订单里有红字应收（要先红蓝对冲，留给人）', 'partial': '已经核销过一部分', 'unaudited': '应收单还没审核',
             'earlier': '钱是更早月份结算的（那个月的到账已经入过账，不能再算一次）', 'cents': '两边差几分钱'}


def push_lines(period, groups):
    """一张收款单的收款明细：到账一行 + 一种扣款一行（金额按分累加，不走浮点）。"""
    fees = defaultdict(int)
    for g in groups:
        for k, v in g['fees'].items(): fees[k] += v
    lines = [{'kind': 'cash', 'name': '到账', 'amount': sum(g['cash'] for g in groups) / 100, 'memo': ''}]
    return lines + [{'kind': 'fee', 'name': k, 'amount': v / 100, 'memo': memo(period, k, v / 100)} for k, v in sorted(fees.items(), key=lambda x: -x[1]) if v]


def push_split(period, groups, receipts):
    """金蝶一次下推可能自己拆成几张收款单（实测 16,070 张应收被拆成 2 张）。receipts 是每张收款单里的应收单号；
    给每张各算一份收款明细。同一个订单的几张应收被拆到不同收款单里就算不了，返回 None。"""
    where = {}
    for i, nos in enumerate(receipts):
        for no in nos: where[no] = i
    parts = [[] for _ in receipts]
    for g in groups:
        homes = {where.get(no) for no in g['bills']}
        if len(homes) != 1 or None in homes: return None
        parts[homes.pop()].append(g)
    if sum(len(g['bills']) for p in parts for g in p) != sum(len(nos) for nos in receipts): return None
    return [{'lines': push_lines(period, p), 'total': sum(g['open'] for g in p) / 100, 'count': sum(len(g['bills']) for g in p)} for p in parts]


def push_plan(period, settle, orders, result, taken=(), size=500):
    """从"可核销"里挑能直接下推的，排出下一批。只推整张蓝字应收、本次收款＝应收全额：
    订单（组）里不能有红字、不能核销过、必须已审核、两边分毫不差。
    返回 {eligible, skipped, pushed, batch}；batch 里是这一批的应收单号和收款明细（到账一行 + 一种扣款一行），批内两边相等。"""
    taken = set(taken)
    group, _ = order_groups(orders)
    gid = lambda o: min(group(o))
    by_group = defaultdict(list)
    for b in result['bills']:
        if b['cat'] == 'ok' and b['order']: by_group[gid(b['order'])].append(b)
    flows = defaultdict(list)
    for r in settle:
        if r['order'] and r['scene'] == SETTLE and r['t'][:7] == period: flows[gid(r['order'])].append(r)      # 只认本期结算的：每批的到账行是本期进账户的钱
    cents = lambda v: int(round(v * 100))
    eligible, skipped, held = [], {k: {'count': 0, 'amount': 0.0} for k in PUSH_SKIP}, {}
    for g, rows in by_group.items():
        why = ('red' if any(b['open'] < 0 for b in rows) else 'partial' if any(b['ws'] != 'A' or b['written'] for b in rows)
               else 'unaudited' if any(b['ds'] != 'C' for b in rows) else '')
        own = flows.get(g, [])
        if not why and not own: why = 'earlier'
        cash =sum(cents(r['amt']) for r in own); fee = sum(cents(v) for r in own for v in r['fees'].values())
        if not why and cash + fee != sum(cents(b['open']) for b in rows): why = 'cents'
        if why:
            skipped[why]['count'] += len(rows); skipped[why]['amount'] += math.fsum(b['open'] for b in rows)
            held.update({b['no']: why for b in rows}); continue
        eligible.append({'gid': g, 'bills': sorted(rows, key=lambda b: b['no']), 'flows': own, 'date': min(b['date'] for b in rows)})
    for v in skipped.values(): v['amount'] = round(v['amount'], 2)
    eligible.sort(key=lambda e: (e['date'], e['bills'][0]['no']))
    total = lambda es: round(math.fsum(b['open'] for e in es for b in e['bills']), 2)
    count = lambda es: sum(len(e['bills']) for e in es)
    left = [e for e in eligible if not any(b['no'] in taken for b in e['bills'])]
    batch, n = [], 0
    for e in left:
        if batch and n + len(e['bills']) > size: break
        batch.append(e); n += len(e['bills'])
    groups = []
    for e in batch:
        fees = defaultdict(int)
        for r in e['flows']:
            for k, v in r['fees'].items(): fees[k] += cents(v)
        groups.append({'bills': [b['no'] for b in e['bills']], 'open': sum(cents(b['open']) for b in e['bills']),
                       'cash': sum(cents(r['amt']) for r in e['flows']), 'fees': dict(fees)})
    lines = push_lines(period, groups)
    nos = [b['no'] for e in batch for b in e['bills']]
    return {'eligible': {'count': count(eligible), 'amount': total(eligible)}, 'skipped': skipped, 'held': held,
            'pushed': {'count': count(eligible) - count(left), 'amount': round(total(eligible) - total(left), 2)},
            'left': {'count': count(left), 'amount': total(left)},
            'batch': {'bills': nos, 'groups': groups, 'count': len(nos), 'total': total(batch), 'lines': lines, 'line_total': round(math.fsum(l['amount'] for l in lines), 2),
                      'first': nos[0] if nos else '', 'last': nos[-1] if nos else '',
                      'from': batch[0]['date'] if batch else '', 'to': max((e['date'] for e in batch), default='')}}
