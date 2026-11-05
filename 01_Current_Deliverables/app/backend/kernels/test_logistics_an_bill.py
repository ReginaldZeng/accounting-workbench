import unittest
from io import BytesIO

from openpyxl import Workbook

from kernels import logistics_an_bill as an


def make_book():
    wb = Workbook()
    ws = wb.active
    ws.title = "1.深圳星期零"
    headers = [None] * 34
    for col, name in {10: "运费", 11: "送货/入仓费", 12: "提货费", 13: "卸货费", 14: "上楼费", 15: "其他", 16: "总运费"}.items():
        headers[col] = name
    for offset, name in enumerate(an.HEADERS[:7] + [None, None, "备注"]):
        headers[24 + offset] = name
    ws.append([None] * 34)
    ws.append(headers)
    ws.append([None] * 24 + ["深圳市星期零食品科技有限公司", "XSCKD219348", "出库运费", "按重量", 4000, "kg", 3030, 2600, 430, None])
    for col, value in {10: 2600, 11: 430}.items():
        ws.cell(ws.max_row, col + 1, value)

    ws = wb.create_sheet("4.山姆")
    headers = [None] * 33
    for col, name in {9: "干线", 10: "提货费", 11: "送货费", 12: "交仓卸货费", 13: "暂存费", 14: "合计费用", 15: "备注"}.items():
        headers[col] = name
    for offset, name in enumerate(an.HEADERS[:7] + [None, None, "备注"]):
        headers[23 + offset] = name
    ws.append([None] * 33)
    ws.append(headers)
    ws.append([None] * 23 + ["深圳市星期零食品科技有限公司", "FBDC077150", "出库运费", "按车", 1, "车", 5800, 5800, 0, None])
    for col, value in {9: 5800, 11: 0, 12: 0, 13: 0}.items():
        ws.cell(ws.max_row, col + 1, value)

    ws = wb.create_sheet("5.外仓")
    top_headers = [None] * 28
    for col, name in {2: "短驳运费", 10: "装卸费"}.items():
        top_headers[col] = name
    for offset, name in enumerate(an.HEADERS[:7] + [None, None, "备注"]):
        top_headers[18 + offset] = name
    headers = [None] * 28
    for col, name in {2: "车型", 6: "运费", 12: "合计"}.items():
        headers[col] = name
    ws.append([None] * 27)
    ws.append(top_headers)
    ws.append(headers)
    ws.append([None] * 18 + ["孝感市星期九食品科技有限公司", "FBDR075739", "入库运费", "按天", 1, "天", 900, 900, 0, None])
    ws.cell(ws.max_row, 3, "4.2米冷藏")
    ws.cell(ws.max_row, 7, 900)
    ws.cell(ws.max_row, 13, 0)
    return wb


class ANBillTests(unittest.TestCase):
    def test_three_sheet_mapping_and_output(self):
        wb = make_book()
        bio = BytesIO()
        wb.save(bio)
        rows = an.convert_bytes(bio.getvalue())
        self.assertEqual(len(rows), 3)
        self.assertEqual(round(sum(r["amount"] for r in rows), 2), 9730.00)
        self.assertEqual(rows[0]["freight"], 2600)
        self.assertEqual(rows[0]["delivery"], 430)
        self.assertEqual(rows[1]["freight"], 5800)
        self.assertEqual(rows[1]["delivery"], 0)
        self.assertEqual(rows[1]["unload"], 0)
        self.assertEqual(rows[2]["freight"], 900)
        self.assertEqual(rows[2]["unload"], 0)
        data = an.write_workbook(rows, "极鲜达", "2026-09")
        reread = an.convert_bytes(data)
        self.assertEqual(len(reread), 3)
        self.assertEqual(round(sum(r["amount"] for r in reread), 2), 9730.00)

    def test_bad_total_rejected(self):
        wb = make_book()
        ws = wb["1.深圳星期零"]
        ws.cell(3, 31, 999)
        bio = BytesIO()
        wb.save(bio)
        with self.assertRaises(ValueError):
            an.convert_bytes(bio.getvalue())


if __name__ == "__main__":
    unittest.main()
