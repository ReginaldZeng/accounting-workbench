import io
import unittest
import zipfile
from kernels import ec_douyin as m

SETTLE_HEAD = '动账时间,动帐流水号,动账方向,动账金额,动账账户,动账场景,计费类型,子订单号,订单号,订单实付应结,运费实付,实际平台补贴,订单退款,平台服务费,佣金,招商服务费,站外推广费,备注'
LEDGER_HEAD = '动账流水号,关联订单号,关联子订单号,动账时间,账户方向,动账金额(元),动账场景,账户余额(元),备注'


def settle_csv(*lines):
    return ('﻿' + SETTLE_HEAD + '\n' + '\n'.join(lines) + '\n').encode('utf-8')


def ledger_csv(*lines):
    return ('﻿' + LEDGER_HEAD + '\n' + '\n'.join(lines) + '\n').encode('utf-8')


def bill(no, date, amount, order, written=0, ws='A', ds='C'):
    return dict(no=no, date=date, amount=amount, written=written, open=round(amount - written, 2), ws=ws, ds=ds, order=order)


O1, O2, O3, O4, O5, O6 = ('69301627455895%05d' % i for i in range(1, 7))
SETTLE = settle_csv(
    f"2026-09-10 08:00:00,'S1,入账,48.50,聚合账户,货款结算入账,巨量千川,'{O1},'{O1},40,0,10,0,-1.50,0,0,0,订单结算",
    f"2026-09-10 08:00:00,'S2,入账,26.00,聚合账户,货款结算入账,精选联盟,'{O2},'{O2},30,0,0,0,-1.00,-2.00,-0.50,-0.50,订单结算",
    f"2026-09-11 09:00:00,'S3,入账,19.00,聚合账户,货款结算入账,小店自卖,'{O3},'{O3},20,0,0,0,-1.00,0,0,0,订单结算",
    f"2026-09-12 09:00:00,'S6,入账,9.00,聚合账户,货款结算入账,小店自卖,'{O6},'{O6},10,0,0,0,-1.00,0,0,0,订单结算",
    "2026-09-12 10:00:00,'X1,出账,5.00,聚合账户,退换货运费险,,,,0,0,0,0,0,0,0,0,保费扣除",
    f"2026-08-20 10:00:00,'S0,入账,9.50,聚合账户,货款结算入账,小店自卖,'{O5},'{O5},10,0,0,0,-0.50,0,0,0,订单结算")
LEDGER = ledger_csv(
    f"S0,`{O5},`{O5},2026-08-20 10:00:00,收入,9.50,货款结算入账,109.50,商家货款入账",
    f"S2,`{O2},`{O2},2026-09-10 08:00:00,收入,26.00,货款结算入账,184.00,商家货款入账",      # 同一秒两笔，文件里顺序是反的
    f"S1,`{O1},`{O1},2026-09-10 08:00:00,收入,48.50,货款结算入账,158.00,商家货款入账",
    f"S3,`{O3},`{O3},2026-09-11 09:00:00,收入,19.00,货款结算入账,203.00,商家货款入账",
    f"S6,`{O6},`{O6},2026-09-12 09:00:00,收入,9.00,货款结算入账,212.00,商家货款入账",
    "X1,,,2026-09-12 10:00:00,支出,-5.00,退换货运费险,207.00,保费扣除")
BILLS = [bill('AR1', '2026-09-05', 50, O1), bill('AR2', '2026-09-05', 30, O2), bill('AR3', '2026-09-06', 25, O3),
         bill('AR4', '2026-09-08', 60, O4), bill('AR4R', '2026-09-09', -60, O4),
         bill('AR5', '2026-08-18', 10, O5, written=10, ws='C'),
         bill('AR7', '2026-09-15', 70, '6930162745589500007'), bill('AR8', '2026-09-28', 80, '6930162745589500008'),
         bill('AR9', '2026-09-29', 5, ''), bill('ARX', '2026-10-02', 99, '6930162745589500009')]


class DouyinTests(unittest.TestCase):
    def rows(self):
        s = m.parse(SETTLE, '明细.csv')[0]; l = m.parse(LEDGER, '流水.csv')[0]
        self.assertEqual((s['kind'], l['kind']), ('dy_settle', 'dy_ledger'))
        return s['rows'], l['rows']

    def test_parse_recognises_by_header_inside_zip_and_skips_insurance(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr('a.csv', LEDGER); z.writestr('b.csv', '﻿保险单号,动账流水号,关联子订单号,摘要描述,动账时间,金额(元)\n1,X1,,退换货运费险,2026-09-12 10:00:00,5\n'.encode())
        parts = m.parse(buf.getvalue(), '包.zip')
        self.assertEqual([p['kind'] for p in parts], ['dy_ledger', '']); self.assertIn('不单独接入', parts[1]['skip'])
        with self.assertRaises(ValueError): m.parse('a,b\n1,2\n'.encode(), 'x.csv')
        s, _ = self.rows()
        self.assertEqual((s[1]['id'], s[1]['order'], s[1]['amt'], s[1]['fees']), ('S2', O2, 26.0, {'平台服务费': 1.0, '佣金': 2.0, '招商服务费': 0.5, '站外推广费': 0.5}))
        self.assertEqual(s[4]['amt'], -5.0)

    def test_merge_dedupes_and_months_split(self):
        s, _ = self.rows()
        self.assertEqual(len(m.merge(s, s)), 6)
        self.assertEqual({k: len(v) for k, v in m.by_month(s).items()}, {'2026-09': 5, '2026-08': 1})

    def test_chain_orders_same_second_rows_by_balance(self):
        _, l = self.rows()
        ordered, breaks = m.chain(m.merge([], l))
        self.assertEqual(([r['id'] for r in ordered], breaks), (['S0', 'S1', 'S2', 'S3', 'S6', 'X1'], 0))
        self.assertEqual(m.chain([dict(l[0]), dict(l[3])])[1], 1)

    def test_reconcile_september(self):
        s, l = self.rows()
        r = m.reconcile('2026-09', m.merge([], s), m.merge([], l), BILLS)
        cat = {b['no']: b['cat'] for b in r['bills']}
        self.assertEqual(cat, {'AR1': 'ok', 'AR2': 'ok', 'AR3': 'mismatch', 'AR4': 'pair', 'AR4R': 'pair', 'AR7': 'overdue', 'AR8': 'transit', 'AR9': 'no_order'})
        self.assertEqual(r['categories']['ok'], {'count': 2, 'orders': 2, 'amount': 80.0})
        self.assertEqual(next(b for b in r['bills'] if b['no'] == 'AR3')['diff'], 5.0)
        d = r['draft']
        self.assertEqual((d['net'], d['deduction_total'], d['total'], d['settled_gross'], d['from_ledger']), (97.5, 12.5, 110.0, 110.0, True))
        self.assertEqual({x['name']: x['amount'] for x in d['deductions']}, {'退换货运费险': 5.0, '平台服务费': 4.5, '佣金': 2.0, '招商服务费': 0.5, '站外推广费': 0.5})
        b = r['buckets']
        self.assertEqual((b['ok'], b['mismatch'], b['missing']), ({'orders': 2, 'flow': 80.0, 'ar': 80.0}, {'orders': 1, 'flow': 20.0, 'ar': 25.0}, {'orders': 1, 'flow': 10.0, 'ar': 0.0}))
        self.assertEqual(round(sum(x['flow'] for x in b.values()), 2), d['total'])            # 四个桶把收款明细合计分完
        self.assertEqual(r['missing'], [{'order': O6, 'flow': 10.0, 'bucket': 'missing'}])
        self.assertEqual({k: r['balance'][k] for k in ('open', 'close', 'net', 'breaks', 'rows')}, {'open': 109.5, 'close': 207.0, 'net': 97.5, 'breaks': 0, 'rows': 5})
        self.assertIn('2026年09月结算单扣款项 佣金2.00元', m.memo('2026-09', '佣金', 2.0))

    def test_august_already_written_off_and_no_ledger_fallback(self):
        s, _ = self.rows()
        r = m.reconcile('2026-08', m.merge([], s), [], BILLS)
        self.assertEqual(r['buckets']['written'], {'orders': 1, 'flow': 10.0, 'ar': 0.0})
        self.assertEqual((r['draft']['net'], r['draft']['from_ledger'], r['balance'], r['bills']), (9.5, False, None, []))

    def test_page_select_export(self):
        s, l = self.rows()
        r = m.reconcile('2026-09', m.merge([], s), m.merge([], l), BILLS)
        self.assertEqual([b['no'] for b in m.select(r, 'pair')], ['AR4R', 'AR4']); self.assertEqual(len(m.select(r, q=O1[-6:])), 1)
        self.assertEqual(m.page(m.select(r, 'ok'))['amount'], 80.0)
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(m.export(r, '抖音店', {'account': '抖音177', 'open': 109.5, 'close': 109.5})))
        self.assertEqual(wb.sheetnames, ['收款单草稿', '可核销(下推源单)', '金额对不上', '红蓝互冲', '该结没结', '在途', '无订单号', '流水有·应收对不上号'])
        self.assertEqual([c.value for c in wb['收款单草稿'][4]], [1, '支付宝', '抖音177', 97.5, '本月账户净变动（到账）'])
        self.assertEqual(wb['可核销(下推源单)'].max_row, 4)


if __name__ == '__main__':
    unittest.main()
