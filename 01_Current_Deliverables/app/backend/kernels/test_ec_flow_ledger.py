import io
import unittest
from decimal import Decimal
import openpyxl
from kernels import ec_flow_ledger as ledger


class FlowLedgerTests(unittest.TestCase):
    def test_supplement_preserves_originals_and_amounts(self):
        row={'order_no':'','desc':'','remark':'猫猫币抵扣项目平台垫付资金（331639328010038983）扣款',
             'mch_no':'T200P331639328010038983','bucket':'unknown','outgo':Decimal('3.95'),'raw':{'业务描述':''}}
        result=ledger.supplement(row)
        self.assertEqual({k:result[k] for k in row},row)
        self.assertNotIn('supplement',row)
        self.assertEqual(result['supplement']['order_candidate'],'331639328010038983')
        self.assertEqual(result['supplement']['order_sources'],['备注','商户订单号'])
        self.assertEqual(result['supplement']['status'],'candidate')

    def test_supplement_conflicts_and_structured_priority(self):
        row={'order_no':'331639328010038983','desc':'known','mch_no':'T200P331639328010038984'}
        result=ledger.supplement(row)
        self.assertEqual(result['order_no'],row['order_no'])
        self.assertEqual(result['supplement']['status'],'conflict')
        self.assertEqual(result['supplement']['order_candidate'],'')
        self.assertEqual(result['supplement']['business_label'],'')
        row['mch_no']='T200P'+row['order_no']
        self.assertEqual(ledger.supplement(row)['supplement']['status'],'corroborates')

    def test_supplement_no_arbitrary_numbers_or_multiple_matches(self):
        result=ledger.supplement({'remark':'付款账号(331639328010038983)扣款','mch_no':'X331639328010038983'})
        self.assertEqual(result['supplement']['status'],'none')
        remark='猫猫币抵扣项目平台垫付资金(331639328010038983)扣款；猫猫币抵扣项目平台垫付资金(331639328010038984)扣款'
        self.assertEqual(ledger.supplement({'remark':remark})['supplement']['status'],'conflict')

    def workbook(self, rows, headers=None):
        book=openpyxl.Workbook();sheet=book.active
        sheet.append(headers or ['入账时间','账务类型','收入（+元）','支出（-元）','业务描述','支付宝流水号','业务基础订单号','余额（元）','新增业务字段'])
        for row in rows:sheet.append(row)
        buffer=io.BytesIO();book.save(buffer);book.close();return buffer.getvalue()

    def test_preserves_unknown_field_and_long_ids(self):
        rows=ledger.parse(self.workbook([['2026-08-01 12:00:00','收费',0,1.81,'0030003|技术费','12345678901234567890','5127484536168046206',14,'完整保留']]),'a.xlsx',{})
        self.assertEqual(rows[0]['raw']['新增业务字段'],'完整保留')
        self.assertEqual(rows[0]['serial'],'12345678901234567890')
        self.assertEqual(rows[0]['outgo'],Decimal('1.81'))
        self.assertIn('未知费目',rows[0]['flags'])

    def test_no_order_reconciliation_buckets(self):
        row=ledger.parse(self.workbook([['2026-08-01','收款',14,0,'0010001|交易收款','s','o',14,'']]),'a.xlsx',{})[0]
        self.assertEqual(row['bucket'],'receipt')
        self.assertNotIn(row['bucket'],('ok','crossed','carry','real'))

    def test_missing_balance_not_zero(self):
        row=ledger.parse(self.workbook([['2026-08-01','收款',14,0,'0010001|交易收款','','o',None,'']]),'a.xlsx',{})[0]
        self.assertIsNone(row['balance'])
        self.assertTrue(any('缺少流水号' in f for f in row['flags']))

    def test_invalid_money_rejects(self):
        with self.assertRaises(ValueError):ledger.parse(self.workbook([['2026-08-01','收款','bad',0,'','','','','']]),'a.xlsx',{})

    def test_transfers_are_candidates_not_confirmed(self):
        headers=['入账时间','账务类型','收入（+元）','支出（-元）','业务描述']
        row=ledger.parse(self.workbook([['2026-08-01','转账',5000,0,'']],headers),'a.xlsx',{})[0]
        self.assertEqual(row['bucket'],'transfer');self.assertIn('划转性质待确认',row['flags'])

    def test_xml_sparse_columns(self):
        xml=b'''<?xml version="1.0"?><Workbook xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"><Worksheet ss:Name="flow"><Table>
        <Row><Cell><Data>\xe5\x85\xa5\xe8\xb4\xa6\xe6\x97\xb6\xe9\x97\xb4</Data></Cell><Cell><Data>\xe8\xb4\xa6\xe5\x8a\xa1\xe7\xb1\xbb\xe5\x9e\x8b</Data></Cell></Row>
        <Row><Cell><Data>2026-08-01</Data></Cell><Cell ss:Index="3"><Data>kept</Data></Cell></Row>
        </Table></Worksheet></Workbook>'''
        cells=list(ledger._cells_xml(xml));self.assertEqual(cells[1][2],['2026-08-01','','kept'])

    def test_entity_rejected(self):
        with self.assertRaises(ValueError):list(ledger._cells_xml(b'<!DOCTYPE x><x/>'))

    def test_refund_transfer_and_subsidy_not_platform_fees(self):
        for code,expected in [('0020002','refund'),('008002800014','transfer'),('0240004T','adjustment')]:
            row=ledger.parse(self.workbook([['2026-08-01','其它',0,10,code+'|测试费目','s','o',14,'']]),'a.xlsx',{code:{'category':'费用','account':'test'}})[0]
            self.assertEqual(row['bucket'],expected)

    def test_numeric_long_id_rejected(self):
        with self.assertRaises(ValueError):
            ledger.parse(self.workbook([['2026-08-01','收款',14,0,'0010001|交易收款',1234567890123456789,'o',14,'']]),'a.xlsx',{})

    def test_fund_retains_raw_and_does_not_invent_balance(self):
        headers=['入账时间','支付流水号','淘宝订单编号','入账类型','收入金额（元）','支出金额','业务描述','附加字段']
        row=ledger.parse_fund(self.workbook([['2026-08-01','s','5127484536168046206','交易收款',14,0,'交易收款','保留']],headers),'fund.xlsx',{})[0]
        self.assertEqual(row['bucket'],'receipt')
        self.assertEqual(row['raw']['附加字段'],'保留')
        self.assertIsNone(row['balance'])


if __name__=='__main__':unittest.main()
