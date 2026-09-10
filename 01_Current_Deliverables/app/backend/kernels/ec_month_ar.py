# [Change Log]
# Date: 2026-09-10 | Author: Codex | Version: V2.554
# Description: Exact platform-order linkage for Kingdee receivable lines, independent of settlement runs.
from decimal import Decimal


def order_key(value):
    # IDs must stay strings: converting a long platform ID to float loses precision.
    if value is None or isinstance(value, float):
        return ''
    value = str(value).strip().strip("'\"").strip()
    return '' if value in ('', '0', 'None', 'null') else value


def index_receivables(rows):
    grouped = {}
    skipped = 0
    for row in rows:
        amount = Decimal(str(row.get('价税合计') or 0))
        if not amount.is_finite():
            raise ValueError('金蝶应收金额不是有效数值')
        key = order_key(row.get('Text6'))
        if not key and amount < 0:
            key = order_key(row.get('Text4'))
        bill = str(row.get('单据编号') or '').strip()
        if not key or not bill:
            skipped += 1
            continue
        entry = grouped.setdefault(key, {})
        doc = entry.setdefault(bill, {'bill_no': bill, 'amount': Decimal(0),
                                     'has_red': False, 'date': str(row.get('日期') or '')[:10]})
        doc['amount'] += amount
        doc['has_red'] = doc['has_red'] or amount < 0
    result = {}
    for key, bills in grouped.items():
        docs = [dict(doc, amount=float(doc['amount'].quantize(Decimal('0.01'))))
                for _, doc in sorted(bills.items())]
        result[key] = {'bill_nos': [doc['bill_no'] for doc in docs], 'documents': docs,
                       'amount': float(sum((d['amount'] for d in bills.values()), Decimal(0)).quantize(Decimal('0.01')))}
    return result, skipped
