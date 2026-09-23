# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家）
# Description: 发票管家·表格内核单元测试。夹具全部在内存里用 openpyxl / 标准库现造（合成数据，不含任何真实
#   公司、税号、人名、金额、发票号码）：标题行在表头上面、合并标题格、两层表头、文本数字带千分位、
#   发票号码存成浮点丢精度、合计行、数电票与老版代码＋号码混排、GBK / UTF-8-BOM 两种 CSV、多工作表。
#   抵扣勾选做往返验证：打开结果文件，逐格比对——只有勾选列的数据格变了，样式、合并格、公式、列宽原样。
#   真实导出件回归：只有设了环境变量 INV_EXCEL_SAMPLES_DIR（指向本机样本目录）才跑，样本不入库。
#   跑法：cd backend && python -m unittest kernels.test_invoice_excel -v
import datetime
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kernels import invoice_excel as ie  # noqa: E402

from openpyxl import Workbook, load_workbook  # noqa: E402
from openpyxl.styles import Font, PatternFill  # noqa: E402

SD1 = "26440000000000000011"          # 合成的 20 位数电票号码
SD2 = "26440000000000000022"
SELLER_A = "测试销方甲有限公司"
SELLER_B = "测试销方乙有限公司"
TAX_A = "91000000TESTA0001X"
TAX_B = "91000000TESTB0002Y"
BUYER = "测试购方公司"
BUYER_TAX = "91000000TESTC0003Z"

TAXLIST_HEADER = ["序号", "发票代码", "发票号码", "数电票号码", "销方识别号", "销方名称", "购方识别号", "购方名称",
                  "开票日期", "金额", "税额", "价税合计", "发票来源", "发票票种", "发票状态", "是否正数发票",
                  "发票风险等级", "勾选状态"]


def _xlsx(wb):
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _taxlist_book():
    """标题行(合并) + 导出时间行 + 空行 + 表头(第 4 行) + 6 行数据(中间夹空行) + 合计行。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "发票基础信息"
    ws["A1"] = "全量发票查询 — 取得发票"
    ws.merge_cells("A1:R1")
    ws["A2"] = "导出时间：2026-09-24 10:00:00"
    ws.append([])                                 # 第 3 行空
    ws.append(TAXLIST_HEADER)                     # 第 4 行表头
    ws.append([1, None, None, SD1, TAX_A, SELLER_A, BUYER_TAX, BUYER,
               datetime.datetime(2026, 9, 23, 0, 0), 1000.0, 130.0, 1130.0, "电子发票服务平台",
               "数电票（增值税专用发票）", "正常", "是", "正常", "未勾选"])
    ws.append([2, "044031900111", "01234567", None, TAX_B, SELLER_B, BUYER_TAX, BUYER,
               "2026/9/3", "1,234.50", "160.49", "1,394.99", "增值税发票管理系统", "增值税普通发票", "正常", "是", "", ""])
    ws.append([3, None, None, 2.4442000001234567e+19, TAX_A, SELLER_A, BUYER_TAX, BUYER,
               "20260923", 100, 6, 106, "电子发票服务平台", "数电票（普通发票）", "已作废", "是", "", ""])
    ws.append([])                                 # 数据中间的空行
    ws.append([4, None, None, SD2, TAX_A, SELLER_A, BUYER_TAX, BUYER,
               "2026年09月01日", -50, -6.5, -56.5, "电子发票服务平台", "数电票（增值税专用发票）", "全额红冲", "否", "", ""])
    ws.append([5, 44031900111.0, 1234567.0, None, TAX_B, SELLER_B, BUYER_TAX, BUYER,
               20260902, 10, 0.6, 10.6, "", "增值税专用发票", "正常", "是", "", ""])
    ws.append([6, None, None, SD2.replace("22", "33"), TAX_B.lower(), SELLER_B, BUYER_TAX, BUYER,
               "看不懂的日期", "见附件", None, 88.0, "", "", "正常", "", "", ""])
    ws.append(["合计", None, None, None, None, None, None, None, None, 2294.5, 290.59, 2583.09])
    return wb


class TestNormalize(unittest.TestCase):
    def test_number_text_kept(self):
        self.assertEqual(ie._norm_no("01234567"), ("01234567", False, False))
        self.assertEqual(ie._norm_no(" 0123 4567 ")[0], "01234567")
        self.assertEqual(ie._norm_no('="01234567"')[0], "01234567")
        self.assertEqual(ie._norm_no("\t" + SD1)[0], SD1)
        self.assertEqual(ie._norm_no("12345678.0")[0], "12345678")

    def test_number_float_lost(self):
        s, lost, numeric = ie._norm_no(2.4442000001234567e+19, "sd")
        self.assertEqual(len(s), 20)                     # 尾数已不可信，只保证是 20 位整数文本
        self.assertTrue(s.startswith("2444200000123456"))
        self.assertTrue(lost)
        self.assertTrue(numeric)
        s, lost, _n = ie._norm_no("2.4442000001234567E+19")
        self.assertEqual(s, "24442000001234567000")
        self.assertTrue(lost)

    def test_number_float_pad(self):
        self.assertEqual(ie._norm_no(1234567.0, "number")[:2], ("01234567", False))
        self.assertEqual(ie._norm_no(11002000111.0, "code")[:2], ("011002000111", False))
        self.assertEqual(ie._norm_no(4403190011, "code")[0], "4403190011")

    def test_dates(self):
        w = ie._Warns()
        for v in ("2026-09-23", "2026/9/23", "20260923", "2026年09月23日", "2026-09-23 10:11:12",
                  20260923, 20260923.0, datetime.datetime(2026, 9, 23, 8, 0)):
            self.assertEqual(ie._date_str(ie._cell(v), w, 1), "2026-09-23", v)
        self.assertEqual(ie._date_str("不是日期", w, 7), "")
        self.assertTrue(any("第 7 行" in x for x in w.lines()))

    def test_total_row(self):
        self.assertTrue(ie._is_total_row(["合计", None, 1]))
        self.assertTrue(ie._is_total_row([None, "合 计", 1]))
        self.assertTrue(ie._is_total_row(["合计：", 1]))
        self.assertFalse(ie._is_total_row([1, "合计服务有限公司", 1]))

    def test_warns_merge(self):
        w = ie._Warns()
        for i in range(8):
            w.add("k", i + 1, "同一类问题")
        lines = w.lines()
        self.assertEqual(len(lines), 1)
        self.assertIn("等共 8 行", lines[0])


class TestReadTable(unittest.TestCase):
    def test_header_detect_with_title_rows(self):
        r = ie.read_table(_xlsx(_taxlist_book()), "取得发票.xlsx")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["header_row"], 4)
        self.assertEqual(r["sheet"], "发票基础信息")
        self.assertEqual(r["headers"][:4], ["序号", "发票代码", "发票号码", "数电票号码"])
        self.assertEqual(len(r["rows"]), 6)             # 空行、合计行都去掉了
        self.assertEqual(r["row_nos"][0], 5)
        self.assertNotIn(8, r["row_nos"])               # 第 8 行空；第 11 行是第 6 张票；合计在第 12 行
        self.assertEqual(r["row_nos"][-1], 11)
        self.assertTrue(any("合计行" in x for x in r["warnings"]))
        self.assertEqual(r["rows"][0][8], "2026-09-23")  # datetime → 文本

    def test_pick_best_sheet(self):
        wb = _taxlist_book()
        info = wb.create_sheet("说明", 0)                 # 放在第一个
        info["A1"] = "本文件由系统导出"
        info["A2"] = "请勿修改"
        det = wb.create_sheet("发票明细信息")
        det.append(["数电票号码", "货物或应税劳务名称", "金额", "税额"])
        det.append([SD1, "*测试*服务费", 1000, 130])
        r = ie.read_table(_xlsx(wb), "x.xlsx")
        self.assertEqual(r["sheet"], "发票基础信息")
        r2 = ie.read_table(_xlsx(wb), "x.xlsx", sheet="发票明细信息")
        self.assertEqual(r2["header_row"], 1)
        self.assertEqual(r2["rows"][0][0], SD1)

    def test_two_level_header(self):
        wb = Workbook()
        ws = wb.active
        ws["A1"] = "发票清单"
        ws.merge_cells("A1:F1")
        ws.append(["序号", "发票号码", "销售方", None, "价税合计(元)", "开票日期"])
        ws.merge_cells("C2:D2")
        ws.merge_cells("A2:A3")
        ws.append([None, None, "纳税人识别号", "名称", None, None])
        ws.append([1, SD1, TAX_A, SELLER_A, "1,130.00", "2026-09-23"])
        r = ie.parse_taxlist(_xlsx(wb), "x.xlsx")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["headerRow"], 3)
        self.assertEqual(r["mapping"]["sellerTaxId"], "销售方纳税人识别号")
        self.assertEqual(r["mapping"]["sellerName"], "销售方名称")
        self.assertEqual(r["mapping"]["total"], "价税合计(元)")
        row = r["rows"][0]
        self.assertEqual((row["number"], row["sellerTaxId"], row["sellerName"], row["total"]),
                         (SD1, TAX_A, SELLER_A, 1130.0))

    def test_generic_fallback_header(self):
        wb = Workbook()
        ws = wb.active
        ws.append(["甲列", "乙列", "丙列"])
        ws.append(["a", "b", "c"])
        r = ie.read_table(_xlsx(wb), "x.xlsx")
        self.assertTrue(r["ok"])
        self.assertEqual(r["header_row"], 1)
        self.assertEqual(r["rows"], [["a", "b", "c"]])
        self.assertTrue(r["warnings"])

    def test_bad_files(self):
        self.assertFalse(ie.read_table(b"", "a.xlsx")["ok"])
        r = ie.read_table(b"not an excel file at all", "a.xlsx")
        self.assertFalse(r["ok"])
        self.assertIn("不像 Excel", r["msg"])
        r = ie.read_table(b"<html><table><tr><td>1</td></tr></table></html>", "a.xls")
        self.assertFalse(r["ok"])
        self.assertIn("网页格式", r["msg"])
        r = ie.parse_taxlist(b"PK\x03\x04garbage", "a.xlsx")
        self.assertFalse(r["ok"])
        self.assertTrue(r["msg"])

    @unittest.skipUnless(os.environ.get("INV_EXCEL_SAMPLES_DIR"), "未设 INV_EXCEL_SAMPLES_DIR，跳过真实导出件回归")
    def test_real_samples(self):
        d = os.environ["INV_EXCEL_SAMPLES_DIR"]
        n = 0
        for fn in sorted(os.listdir(d)):
            if not fn.lower().endswith((".xlsx", ".xls", ".csv")):
                continue
            with open(os.path.join(d, fn), "rb") as f:
                data = f.read()
            r = ie.read_table(data, fn)
            self.assertTrue(r["ok"], (fn, r["msg"]))
            n += 1
        self.assertGreater(n, 0)


class TestParseTaxlist(unittest.TestCase):
    def setUp(self):
        self.r = ie.parse_taxlist(_xlsx(_taxlist_book()), "取得发票.xlsx")

    def test_mapping(self):
        m = self.r["mapping"]
        self.assertTrue(self.r["ok"], self.r)
        self.assertEqual(m["number"], "发票号码")
        self.assertEqual(m["numberSd"], "数电票号码")
        self.assertEqual(m["code"], "发票代码")
        self.assertEqual(m["sellerTaxId"], "销方识别号")
        self.assertEqual(m["amount"], "金额")
        self.assertEqual(m["total"], "价税合计")
        self.assertEqual(m["status"], "发票状态")
        self.assertEqual(m["invType"], "发票票种")
        self.assertEqual(m["checkState"], "勾选状态")
        self.assertIn("发票号码 ← 发票号码", ie.mapping_lines(m))

    def test_rows(self):
        rows = self.r["rows"]
        self.assertEqual(len(rows), 6)
        a = rows[0]
        self.assertEqual((a["number"], a["code"], a["date"], a["amount"], a["tax"], a["total"]),
                         (SD1, "", "2026-09-23", 1000.0, 130.0, 1130.0))
        self.assertEqual((a["sellerTaxId"], a["sellerName"], a["buyerName"], a["status"], a["checkState"]),
                         (TAX_A, SELLER_A, BUYER, "正常", "未勾选"))
        self.assertTrue(a["positive"])
        self.assertEqual(a["raw"]["数电票号码"], SD1)
        b = rows[1]                                       # 老版票：代码 + 8 位号码，文本数字带千分位
        self.assertEqual((b["code"], b["number"], b["date"], b["amount"], b["tax"], b["total"]),
                         ("044031900111", "01234567", "2026-09-03", 1234.5, 160.49, 1394.99))
        c = rows[2]                                       # 浮点丢精度
        self.assertTrue(c["numberLost"])
        self.assertEqual(len(c["number"]), 20)
        self.assertTrue(c["number"].startswith("244420000012345"))
        self.assertEqual(c["status"], "已作废")
        self.assertEqual(c["date"], "2026-09-23")
        d = rows[3]
        self.assertEqual((d["date"], d["total"], d["positive"], d["status"]), ("2026-09-01", -56.5, False, "全额红冲"))
        e = rows[4]                                       # 数字格式的老版代码/号码补回前导零
        self.assertEqual((e["code"], e["number"], e["date"]), ("044031900111", "01234567", "2026-09-02"))
        f = rows[5]
        self.assertEqual(f["sellerTaxId"], TAX_B)         # 税号统一大写
        self.assertIsNone(f["amount"])                    # 读不懂的金额留空，不当 0
        self.assertIsNone(f["tax"])
        self.assertEqual(f["date"], "")
        self.assertEqual(f["rowNo"], 11)

    def test_warnings(self):
        ws = "\n".join(self.r["warnings"])
        self.assertIn("尾数丢了", ws)
        self.assertIn("第 7 行", ws)
        self.assertIn("开票日期读不懂", ws)
        self.assertIn("金额读不懂", ws)
        self.assertIn("合计行", ws)
        self.assertIn("重复", ws)                         # 第 2、6 行代码+号码相同

    def test_no_number_column(self):
        wb = Workbook()
        ws = wb.active
        ws.append(["销方名称", "价税合计", "开票日期"])
        ws.append([SELLER_A, 10, "2026-09-01"])
        r = ie.parse_taxlist(_xlsx(wb), "x.xlsx")
        self.assertFalse(r["ok"])
        self.assertIn("发票号码", r["msg"])
        self.assertIn("价税合计", r["msg"])               # 把认到的列告诉人

    def test_prefers_20_digit_number(self):
        wb = Workbook()
        ws = wb.active
        ws.append(["发票代码", "发票号码", "数电票号码", "价税合计"])
        ws.append(["044031900111", "01234567", SD1, 10])      # 数电纸质票：两套号码都有
        ws.append([None, SD2, None, 20])                      # 20 位号码写在「发票号码」列
        r = ie.parse_taxlist(_xlsx(wb), "x.xlsx")
        self.assertEqual([x["number"] for x in r["rows"]], [SD1, SD2])

    def _csv_bytes(self, enc):
        lines = [
            "全量发票查询导出",
            "发票代码,发票号码,数电发票号码,销售方纳税人识别号,销售方名称,开票日期,金额,税额,价税合计,发票状态",
            ',,\t%s,%s,%s,2026-09-23,"1,000.00",130.00,"1,130.00",正常' % (SD1, TAX_A, SELLER_A),
            '044031900111,="01234567",,%s,%s,2026/9/3,100,6,106,已红冲' % (TAX_B, SELLER_B),
            "合计,,,,,,\"1,100.00\",136.00,\"1,236.00\",",
        ]
        return ("\r\n".join(lines) + "\r\n").encode(enc)

    def test_csv_utf8_sig_and_gbk(self):
        for enc in ("utf-8-sig", "gbk"):
            r = ie.parse_taxlist(self._csv_bytes(enc), "list.csv")
            self.assertTrue(r["ok"], (enc, r))
            self.assertEqual(r["headerRow"], 2, enc)
            self.assertEqual(r["mapping"]["numberSd"], "数电发票号码")
            rows = r["rows"]
            self.assertEqual(len(rows), 2, enc)
            self.assertEqual((rows[0]["number"], rows[0]["amount"], rows[0]["total"], rows[0]["sellerName"]),
                             (SD1, 1000.0, 1130.0, SELLER_A))
            self.assertEqual((rows[1]["code"], rows[1]["number"], rows[1]["date"], rows[1]["status"]),
                             ("044031900111", "01234567", "2026-09-03", "已红冲"))

    def test_tab_separated_csv(self):
        text = "发票号码\t开票日期\t价税合计\n%s\t2026-09-23\t1,130.00\n" % SD1
        r = ie.parse_taxlist(text.encode("utf-8"), "list.csv")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["rows"][0]["total"], 1130.0)

    def test_xls_branch_with_fake_xlrd(self):
        """本机没有 xlwt 造不出真 .xls：用假 xlrd 走一遍 .xls 分支（格子类型转换、日期、空格子）。"""
        import types
        from unittest import mock
        E, T, N, D, B = 0, 1, 2, 3, 4
        grid = [[(T, "发票清单"), (E, ""), (E, "")],
                [(T, "发票号码"), (T, "开票日期"), (T, "价税合计")],
                [(T, SD1), (D, 46288.0), (N, 1130.0)],
                [(N, 1234567.0), (T, "2026/9/3"), (T, "1,000.00")],
                [(T, "合计"), (E, ""), (N, 2130.0)]]

        class Cell:
            def __init__(self, t, v):
                self.ctype, self.value = t, v

        class Sheet:
            name, visibility, nrows, ncols = "Sheet1", 0, len(grid), 3

            def cell(self, r, c):
                return Cell(*grid[r][c])

        class Book:
            datemode = 0

            def sheets(self):
                return [Sheet()]

            def sheet_names(self):
                return ["Sheet1"]

            def sheet_by_name(self, n):
                return Sheet()

        fake = types.SimpleNamespace(
            open_workbook=lambda file_contents=None: Book(),
            XL_CELL_EMPTY=E, XL_CELL_TEXT=T, XL_CELL_NUMBER=N, XL_CELL_DATE=D, XL_CELL_BOOLEAN=B,
            XL_CELL_BLANK=6, XL_CELL_ERROR=5,
            xldate_as_datetime=lambda v, m: datetime.datetime(1899, 12, 30) + datetime.timedelta(days=v))
        data = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
        with mock.patch.dict(sys.modules, {"xlrd": fake}):
            r = ie.parse_taxlist(data, "list.xls")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["headerRow"], 2)
        self.assertEqual([(x["number"], x["date"], x["total"]) for x in r["rows"]],
                         [(SD1, "2026-09-23", 1130.0), ("01234567", "2026-09-03", 1000.0)])

    @unittest.skipUnless(__import__("importlib").util.find_spec("xlwt"), "本机没装 xlwt，跳过 .xls 读取用例")
    def test_xls(self):
        import xlwt
        book = xlwt.Workbook()
        sh = book.add_sheet("清单")
        sh.write(0, 0, "发票清单")
        for j, h in enumerate(["发票号码", "开票日期", "价税合计"]):
            sh.write(1, j, h)
        sh.write(2, 0, SD1)
        sh.write(2, 1, datetime.datetime(2026, 9, 23), xlwt.easyxf(num_format_str="yyyy-mm-dd"))
        sh.write(2, 2, 1130.0)
        buf = io.BytesIO()
        book.save(buf)
        r = ie.parse_taxlist(buf.getvalue(), "list.xls")
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["rows"][0]["number"], r["rows"][0]["date"], r["rows"][0]["total"]),
                         (SD1, "2026-09-23", 1130.0))


class TestParseOpening(unittest.TestCase):
    def test_opening(self):
        wb = Workbook()
        ws = wb.active
        ws.title = "发票列表"
        ws["A1"] = "票总管 发票导出"
        ws.merge_cells("A1:J1")
        ws.append(["序号", "报销单号", "报销人", "发票代码", "发票号码", "开票日期", "销售方名称", "购买方名称",
                   "金额", "价税合计"])
        ws.append([1, "BX-TEST-001", "测试员甲", None, SD1, "2026-08-01", SELLER_A, BUYER, 100, 106])
        ws.append([2, "BX-TEST-002", "测试员乙", "044031900111", "01234567", "2026/8/2", SELLER_B, BUYER,
                   "1,000.00", "1,130.00"])
        ws.append([3, "BX-TEST-003", "测试员乙", None, None, None, "某收据", None, 50, 50])
        ws.append(["合计", None, None, None, None, None, None, None, 1150, 1286])
        r = ie.parse_opening(_xlsx(wb), "票总管导出.xlsx")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["mapping"]["total"], "价税合计")     # 两列都有时不拿「金额」
        self.assertEqual(r["mapping"]["refDoc"], "报销单号")
        self.assertEqual(len(r["rows"]), 2)
        a, b = r["rows"]
        self.assertEqual((a["number"], a["date"], a["total"], a["sellerName"], a["buyerName"], a["ref"]),
                         (SD1, "2026-08-01", 106.0, SELLER_A, BUYER, "BX-TEST-001 / 测试员甲"))
        self.assertEqual((b["code"], b["number"], b["total"]), ("044031900111", "01234567", 1130.0))
        self.assertTrue(any("没有发票号码" in x for x in r["warnings"]))

    def test_opening_face_amount_only(self):
        wb = Workbook()
        ws = wb.active
        ws.append(["开票方", "发票号码", "票面金额", "录入人"])
        ws.append([SELLER_A, SD2, "2,000", "测试员丙"])
        r = ie.parse_opening(_xlsx(wb), "x.xlsx")
        self.assertTrue(r["ok"], r)
        row = r["rows"][0]
        self.assertEqual((row["number"], row["total"], row["sellerName"], row["ref"]), (SD2, 2000.0, SELLER_A, "测试员丙"))


DEDUCT_HEADER = ["序号", "数电票号码", "发票代码", "发票号码", "开票日期", "销售方纳税人识别号", "销售方纳税人名称",
                 "金额", "税额", "有效抵扣税额", "发票状态", "是否勾选", "勾选时间"]
HDR_FILL = "FFC6E0B4"
TICK_FILL = "FFFFF2CC"


def _deduct_book(with_tick=True):
    wb = Workbook()
    ws = wb.active
    ws.title = "抵扣类勾选"
    hdr = DEDUCT_HEADER if with_tick else [h for h in DEDUCT_HEADER if h != "是否勾选"]
    ws["A1"] = "抵扣类勾选 未勾选发票清单"
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(hdr))
    ws["A2"] = "税款所属期：202609"
    ws.append(hdr)                                   # 第 3 行表头
    for c in ws[3]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor=HDR_FILL)
    data = [
        [1, SD1, None, None, "2026-09-01", TAX_A, SELLER_A, 1000, 130, 130, "正常", "否", None],
        [2, None, "044031900111", "01234567", "2026-09-02", TAX_B, SELLER_B, 200, 26, 26, "正常", "否", None],
        [3, SD2, None, None, "2026-09-03", TAX_A, SELLER_A, 300, 18, 18, "正常", "否", None],
        [4, "26440000000000000044", None, None, "2026-09-04", TAX_B, SELLER_B, 50, 3, 3, "正常", "否", None],
    ]
    for row in data:
        if not with_tick:
            row = row[:11] + row[12:]
        ws.append(row)
    tick_col = hdr.index("是否勾选") + 1 if with_tick else None
    if tick_col:
        for r in range(4, 8):
            ws.cell(row=r, column=tick_col).fill = PatternFill("solid", fgColor=TICK_FILL)
    total = ["合计", None, None, None, None, None, None, "=SUM(H4:H7)", "=SUM(I4:I7)", 177, None]
    if with_tick:
        total += ["否", None]                       # 合计行勾选列故意放个值：绝不能被改
    ws.append(total)                                 # 第 8 行
    ws.column_dimensions["B"].width = 26
    ws.freeze_panes = "A4"
    return wb


def _decide(row):
    if row["number"] == SD1:
        return ("是", "已审核·可抵扣·已验真")
    if row["number"] == "01234567":
        return ("否", "台账里没有")
    if row["number"] == SD2:
        return True                                 # 裸布尔也认
    return (None, "待核")


class TestMarkDeduct(unittest.TestCase):
    def test_roundtrip_only_tick_changes(self):
        src = _xlsx(_deduct_book())
        seen = []

        def decide(row):
            seen.append(row)
            return _decide(row)

        r = ie.mark_deduct_file(src, "未勾选.xlsx", decide)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["summary"], {"rows": 4, "yes": 2, "no": 1, "unknown": 1})
        self.assertEqual(r["tickColumn"], "是否勾选")
        self.assertEqual(r["mapping"]["deductTax"], "有效抵扣税额")
        self.assertEqual(r["mapping"]["tax"], "税额")
        self.assertEqual([p["decision"] for p in r["preview"]], ["是", "否", "是", ""])
        self.assertEqual(r["preview"][0]["reason"], "已审核·可抵扣·已验真")
        self.assertEqual(seen[0]["tick"], "否")
        self.assertEqual(seen[0]["deductTax"], 130.0)
        self.assertEqual(seen[1]["code"], "044031900111")

        before = load_workbook(io.BytesIO(src))["抵扣类勾选"]
        after = load_workbook(io.BytesIO(r["bytes"]))["抵扣类勾选"]
        tick_col = DEDUCT_HEADER.index("是否勾选") + 1
        changed = []
        for row in range(1, max(before.max_row, after.max_row) + 1):
            for col in range(1, max(before.max_column, after.max_column) + 1):
                a, b = before.cell(row=row, column=col).value, after.cell(row=row, column=col).value
                if a != b:
                    changed.append((row, col, a, b))
        self.assertEqual(changed, [(4, tick_col, "否", "是"), (6, tick_col, "否", "是")])
        # 公式、合计行、样式、合并格、列宽、冻结都原样
        self.assertEqual(after["H8"].value, "=SUM(H4:H7)")
        self.assertEqual(after.cell(row=8, column=tick_col).value, "否")
        self.assertEqual(after.cell(row=4, column=tick_col).fill.fgColor.rgb, TICK_FILL)
        self.assertEqual(after["A3"].fill.fgColor.rgb, HDR_FILL)
        self.assertTrue(after["A3"].font.bold)
        self.assertIn("A1:M1", [str(m) for m in after.merged_cells.ranges])
        self.assertEqual(after.column_dimensions["B"].width, 26)
        self.assertEqual(after.freeze_panes, "A4")

    def test_no_tick_column_appends(self):
        src = _xlsx(_deduct_book(with_tick=False))
        r = ie.mark_deduct_file(src, "未勾选.xlsx", _decide)
        self.assertTrue(r["ok"], r)
        self.assertTrue(any("没找到「是否勾选」" in x for x in r["warnings"]))
        ws = load_workbook(io.BytesIO(r["bytes"]))["抵扣类勾选"]
        col = len(DEDUCT_HEADER)                       # 原 12 列 + 追加 1 列
        self.assertEqual(ws.cell(row=3, column=col).value, "是否勾选")
        self.assertEqual(ws.cell(row=3, column=col).fill.fgColor.rgb, HDR_FILL)
        self.assertEqual([ws.cell(row=x, column=col).value for x in range(4, 9)], ["是", "否", "是", None, None])
        self.assertEqual(ws.cell(row=3, column=col - 1).value, "勾选时间")   # 勾选时间不会被当勾选列

    def test_decide_error_is_unknown(self):
        def boom(row):
            raise ValueError("x")
        r = ie.mark_deduct_file(_xlsx(_deduct_book()), "a.xlsx", boom)
        self.assertTrue(r["ok"])
        self.assertEqual(r["summary"]["unknown"], 4)
        ws = load_workbook(io.BytesIO(r["bytes"]))["抵扣类勾选"]
        self.assertEqual(ws.cell(row=4, column=12).value, "否")

    def test_csv_input_becomes_xlsx(self):
        text = "发票号码,开票日期,税额,是否勾选\n%s,2026-09-01,130,否\n01234567,2026-09-02,26,否\n合计,,156,\n" % SD1
        r = ie.mark_deduct_file(text.encode("gbk"), "未勾选.csv", _decide)
        self.assertTrue(r["ok"], r)
        self.assertTrue(any(".xlsx" in x for x in r["warnings"]))
        ws = load_workbook(io.BytesIO(r["bytes"])).active
        self.assertEqual([ws.cell(row=x, column=4).value for x in range(1, 5)], ["是否勾选", "是", "否", None])
        self.assertEqual(ws["A2"].value, SD1)
        self.assertEqual(ws["A4"].value, "合计")

    def test_odd_tick_values_warn(self):
        wb = Workbook()
        ws = wb.active
        ws.append(["发票号码", "勾选状态", "勾选日期"])
        ws.append([SD1, "未勾选", None])
        r = ie.mark_deduct_file(_xlsx(wb), "a.xlsx", _decide)
        self.assertEqual(r["tickColumn"], "勾选状态")
        self.assertTrue(any("未勾选" in x for x in r["warnings"]))
        self.assertEqual(load_workbook(io.BytesIO(r["bytes"])).active["B2"].value, "是")


def _item(i, total, number=None, **kw):
    it = {
        "id": i, "kind": "invoice", "invType": "special", "typeLabel": "", "code": "", "number": number or SD1,
        "date": "2026-09-0%d" % (i % 9 + 1), "sellerName": SELLER_A, "sellerTaxId": TAX_A, "buyerName": BUYER,
        "buyerTaxId": BUYER_TAX, "amount": round(total / 1.13, 2), "tax": round(total - round(total / 1.13, 2), 2),
        "total": total, "taxRate": "13%", "category": "*测试*服务", "verify": "green", "deductible": True,
        "deductStatus": "", "createdBy": "测试员甲", "createdAt": "2026-09-24 10:00:00", "reviewBy": "测试员乙",
        "reviewAt": "2026-09-24 11:00:00", "origin": "scanner",
        "folder": {"businessId": "202609241000000%02d" % i, "title": "测试付款", "applicant": "测试员丙",
                   "template": "付款申请（公对公）", "dept": "测试部", "payee": {"name": SELLER_A, "bank": "", "account": ""}},
    }
    it.update(kw)
    return it


class TestExports(unittest.TestCase):
    def test_ledger(self):
        rows = [_item(1, 1130.0), _item(2, 56.5, number="01234567", code="044031900111", invType="normal"),
                _item(3, 20.0, kind="receipt", number="", sellerName="=1+1", verify="", deductible=None, origin="upload")]
        rows[2]["folder"]["payee"] = None
        data = ie.export_ledger(rows, title="发票台账")
        wb = load_workbook(io.BytesIO(data))
        ws = wb["发票台账"]
        self.assertEqual([c.value for c in ws[1]], ie.LEDGER_COLUMNS)
        self.assertEqual(ws.freeze_panes, "A2")
        col = {h: i + 1 for i, h in enumerate(ie.LEDGER_COLUMNS)}
        self.assertEqual(ws.cell(row=2, column=col["发票号码"]).value, SD1)
        self.assertEqual(ws.cell(row=2, column=col["发票号码"]).data_type, "s")
        self.assertEqual(ws.cell(row=3, column=col["发票代码"]).value, "044031900111")
        self.assertEqual(ws.cell(row=2, column=col["票种"]).value, "增值税专用发票")
        self.assertEqual(ws.cell(row=4, column=col["票种"]).value, "收据")
        self.assertEqual(ws.cell(row=2, column=col["验真"]).value, "已验真")
        self.assertEqual(ws.cell(row=2, column=col["可否抵扣"]).value, "可抵扣")
        self.assertEqual(ws.cell(row=2, column=col["来源"]).value, "扫码枪")
        self.assertEqual(ws.cell(row=2, column=col["收款方/事由"]).value, SELLER_A)
        self.assertEqual(ws.cell(row=4, column=col["收款方/事由"]).value, "测试付款")
        self.assertEqual(ws.cell(row=2, column=col["审批编号"]).value, "20260924100000001")
        self.assertEqual(ws.cell(row=4, column=col["销方名称"]).value, "=1+1")      # 不被当公式
        self.assertEqual(ws.cell(row=4, column=col["销方名称"]).data_type, "s")
        money = ws.cell(row=2, column=col["价税合计"])
        self.assertEqual(money.number_format, "#,##0.00")
        tot = ws.cell(row=5, column=col["价税合计"]).value
        self.assertAlmostEqual(tot, 1130.0 + 56.5 + 20.0, places=2)
        self.assertIn("合计", ws.cell(row=5, column=1).value)
        self.assertAlmostEqual(ws.cell(row=5, column=col["金额"]).value + ws.cell(row=5, column=col["税额"]).value,
                               tot, places=2)

    def test_ledger_large(self):
        rows = [_item(i, 100.0 + i) for i in range(3000)]
        data = ie.export_ledger(rows)
        ws = load_workbook(io.BytesIO(data), read_only=True)["发票台账"]
        last = None
        n = 0
        for r in ws.iter_rows(values_only=True):
            n += 1
            last = r
        self.assertEqual(n, 3002)
        self.assertAlmostEqual(last[ie.LEDGER_COLUMNS.index("价税合计")], sum(100.0 + i for i in range(3000)), places=2)

    def test_ledger_empty(self):
        wb = load_workbook(io.BytesIO(ie.export_ledger([])))
        self.assertEqual([c.value for c in wb.active[1]], ie.LEDGER_COLUMNS)

    def test_later(self):
        base = {"template": "付款申请（公对公）", "applicant": "测试员甲", "dept": "测试部", "company": BUYER,
                "invKind": "special", "taxRate": "13%", "status": "open", "reason": [{"name": "事由", "value": "测试预付"}],
                "filedVia": "proxy", "receiverName": "测试员乙"}
        rows = [
            dict(base, id=1, payee={"name": SELLER_A, "bank": "测试银行", "account": "6200000000000001"},
                 payAmount=1000, expectAmount=1000, receivedAmount=300, unregisteredAmount=0, remaining=700,
                 expectDate="2026-09-30", overdue=False),
            dict(base, id=2, payee={"name": SELLER_A, "bank": "", "account": ""}, payAmount=500, expectAmount=None,
                 receivedAmount=100, unregisteredAmount=50, expectDate="2026-09-10", overdue=True, status="partial"),
            dict(base, id=3, payee={"name": SELLER_B}, payAmount=2000, expectAmount=2000, receivedAmount=0,
                 unregisteredAmount=0, remaining=2000, expectDate="2026-10-15", overdue=False),
        ]
        wb = load_workbook(io.BytesIO(ie.export_later(rows)))
        self.assertEqual(wb.sheetnames, ["按供应商汇总", "明细"])
        s = wb["按供应商汇总"]
        self.assertEqual([c.value for c in s[1]], ie.LATER_SUMMARY_COLUMNS)
        vals = [[c.value for c in r] for r in s.iter_rows(min_row=2)]
        self.assertEqual(vals[0], [SELLER_B, 1, 2000, 0, 2000, "2026-10-15", 0])          # 未到票大的排前
        self.assertEqual(vals[1], [SELLER_A, 2, 1500, 450, 1050, "2026-09-10", 1])        # 350=500−100−50
        self.assertEqual(vals[2][1:], [3, 3500, 450, 3050, None, 1])
        self.assertIn("合计", vals[2][0])
        d = wb["明细"]
        self.assertEqual([c.value for c in d[1]], ie.LATER_DETAIL_COLUMNS)
        col = {h: i for i, h in enumerate(ie.LATER_DETAIL_COLUMNS)}
        r1 = [c.value for c in d[2]]
        self.assertEqual(r1[col["收款账号"]], "6200000000000001")
        self.assertEqual(r1[col["事由"]], "事由：测试预付")
        self.assertEqual(r1[col["发票类型"]], "专票")
        self.assertEqual(r1[col["状态"]], "未到票")
        self.assertEqual(r1[col["登记方式"]], "财务代填")
        self.assertEqual([c.value for c in d[3]][col["未到票金额"]], 350)
        self.assertEqual([c.value for c in d[3]][col["是否超期"]], "超期")

    def test_sellers(self):
        rows = [{"taxId": TAX_A, "name": SELLER_A, "firstSeen": "2026-09-01", "items": 3, "total": 1234.5,
                 "checkDate": "", "checkChannel": "", "checkResult": "", "checkNote": "", "checkedBy": ""},
                {"taxId": TAX_B, "name": SELLER_B, "firstSeen": "2026-09-05", "items": 1, "total": 100,
                 "checkDate": "2026-09-20", "checkChannel": "信用中国", "checkResult": "无记录", "checkNote": "",
                 "checkedBy": "测试员甲"}]
        ws = load_workbook(io.BytesIO(ie.export_sellers(rows))).active
        self.assertEqual([c.value for c in ws[1]], ie.SELLER_COLUMNS)
        self.assertEqual(ws["H2"].value, "未查")
        self.assertEqual(ws["H3"].value, "无记录")
        self.assertAlmostEqual(ws["E4"].value, 1334.5, places=2)


class TestLazyImports(unittest.TestCase):
    def test_module_has_no_heavy_top_level_imports(self):
        with open(ie.__file__, encoding="utf-8") as f:
            src = f.read()
        top = [ln for ln in src.splitlines() if ln.startswith(("import ", "from "))]
        for mod in ("openpyxl", "xlrd", "fitz", "cv2", "numpy", "PIL", "requests", "db", "core"):
            self.assertFalse(any(ln.split()[1].split(".")[0] == mod for ln in top), mod)


if __name__ == "__main__":
    unittest.main()
