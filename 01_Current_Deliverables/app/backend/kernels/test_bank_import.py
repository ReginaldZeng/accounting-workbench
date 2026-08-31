# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-08-03 | Author: Claude / c | Version: V2.167
# Description: 银行流水导入·识别层单元测试。重点回归"改名兜底"：文件名对不上时按内容认
#   （财资平台 xlsx 表头 / 中行 HISQRY 块头），建行 csv 不被误抢、乱文件仍跳过、原名快路不读文件。
#   夹具全部合成落临时目录，不依赖真实流水。
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kernels import bank_import as bi

from openpyxl import Workbook


# ---- 合成夹具 ----
def make_treasury_xlsx(path):
    """财资平台导出的最小复刻：账户头两行 + 明细表头 + 两笔明细（结构对齐真实导出）。"""
    wb = Workbook()
    ws = wb.active
    ws.append(["账户明细", "账号：", "73110122000157061"])
    ws.append(["", "户名：", "测试食品科技有限公司"])
    ws.append(["", "交易时间", "本方户名", "本方账号", "收入", "支出", "账户余额",
               "对方户名", "对方账号", "币种", "交易备注", "交易类型"])
    ws.append(["", "2026-07-01 16:37:03", "测试食品科技有限公司", "73110122000157061",
               "", "2,550.00", "365,442.33", "微笑办公用品商行", "44306643601801001783",
               "人民币", "办公用品", "转账"])
    ws.append(["", "2026-07-01 19:12:01", "测试食品科技有限公司", "73110122000157061",
               "31,200.00", "", "396,642.33", "农业科技（上海）有限公司", "50131000927752036",
               "人民币", "货款", "网银转账"])
    wb.save(path)


def make_hisqry_csv(path):
    """中行 HISQRY 最小复刻（V2.199 真实形态）：块头 + 明细表头(付款人/收款人名称) + 来账/往账各一笔。
    含「付款人开户行名称」陷阱列——银行名也带"公司"，修复前会被误当对方户名（需求方实查案例）。"""
    lines = [
        "查询账号,1234567890123",
        "总笔数,2",
        "借方发生总额,50.00",
        "贷方发生总额,267.10",
        "交易类型[ Transaction Type ],业务类型,付款人开户行名称,付款人账号,付款人名称[ Payer's Name ],"
        "收款人账号,收款人名称[ Payee's Name ],交易日期,交易金额,交易后余额",
        "来账,小额普通,中信银行股份有限公司东莞分行,744800018260,东莞市绿邦实业有限公司,"
        "1234567890123,深圳市星期零食品科技有限公司,20260703,+267.10,\"2,719,648.72\"",
        "往账,跨行汇款,宁波银行股份有限公司深圳分行,1234567890123,深圳市星期零食品科技有限公司,"
        "50131000927752,某某贸易有限公司,20260705,-50.00,\"2,719,598.72\"",
    ]
    with open(path, "w", encoding="gbk", newline="") as f:
        f.write("\n".join(lines))


def break_declared_dimension(path):
    """复刻银行生成器的坑：把 sheet1.xml 的 <dimension> 错报成 A1:A1。
    openpyxl read_only 模式会信这个声明、整表读成空——嗅探必须 reset_dimensions 才见内容。"""
    import re
    import zipfile
    tmp = path + ".tmp"
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.namelist():
            data = zin.read(item)
            if item == "xl/worksheets/sheet1.xml":
                data = re.sub(rb'<dimension ref="[^"]*"/>', b'<dimension ref="A1:A1"/>', data)
            zout.writestr(item, data)
    os.replace(tmp, path)


def make_ccb_csv(path):
    """建行 host-to-host 明细最小复刻（gb18030，表头含 借方发生额/贷方发生额/账号）。"""
    lines = [
        "账号,账户名称,币种,记账日期,摘要,借方发生额（支取）,贷方发生额（收入）,余额,对方户名",
        "62001234567,测试食品科技有限公司,人民币,2026-07-01,货款,0.00,100.00,100.00,某某贸易有限公司",
    ]
    with open(path, "w", encoding="gb18030", newline="") as f:
        f.write("\n".join(lines))


class TestRenameFallback(unittest.TestCase):
    """V2.167：同事随手改名后，内容还在就必须认得出（此前整包被静默标"跳过"）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    # ---- 快路：原名靠文件名认，不必读文件（路径不存在也能分类）----
    def test_original_names_fast_path(self):
        self.assertEqual(bi._classify("宁波银行+招商银行20260731.xlsx"), "treasury")
        self.assertEqual(bi._classify("HISQRY_202607.csv"), "hisqry")
        self.assertEqual(bi._classify("2088123456.xls"), "alipay")
        self.assertEqual(bi._classify("1576978681_2026.csv"), "wechat")
        self.assertEqual(bi._classify("DL20260701.csv"), "douyin")

    # ---- 兜底：改名的财资平台 xlsx 按表头认回 ----
    def test_renamed_treasury_recognized(self):
        p = os.path.join(self.dir, "宁波+招商.xlsx")     # 真实案例的改名方式
        make_treasury_xlsx(p)
        self.assertTrue(bi.is_treasury_xlsx(p))
        self.assertEqual(bi._classify(p), "treasury")
        rows = bi.parse_treasury(p)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["账号"], "73110122000157061")
        self.assertEqual(rows[0]["支出"], 2550.00)
        self.assertEqual(rows[1]["收入"], 31200.00)

    # ---- 兜底：改名 + 尺寸声明错报 A1:A1（7月真实案例的完整形态）也要认得出 ----
    def test_renamed_treasury_with_bad_dimension(self):
        p = os.path.join(self.dir, "宁波+招商.xlsx")
        make_treasury_xlsx(p)
        break_declared_dimension(p)
        self.assertTrue(bi.is_treasury_xlsx(p))
        self.assertEqual(bi._classify(p), "treasury")
        self.assertEqual(len(bi.parse_treasury(p)), 2)   # 解析器(完整模式)本就不受影响，一并锁住

    # ---- 兜底：改名的中行 HISQRY csv 按块头认回 ----
    def test_renamed_hisqry_recognized(self):
        p = os.path.join(self.dir, "中行7月流水.csv")
        make_hisqry_csv(p)
        self.assertTrue(bi.is_hisqry_csv(p))
        self.assertEqual(bi._classify(p), "hisqry")
        rows, blocks = bi.parse_hisqry(p)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["收入"], 267.10)
        self.assertEqual(rows[1]["支出"], 50.00)
        self.assertEqual(blocks[0]["总笔数"], 2)
        # V2.199：来账对方=付款人名称（不是含"公司"的开户行名），往账对方=收款人名称（不是本方）
        self.assertEqual(rows[0]["对方户名"], "东莞市绿邦实业有限公司")
        self.assertEqual(rows[1]["对方户名"], "某某贸易有限公司")

    def test_hisqry_fallback_excludes_bank(self):
        # 无表头老格式走启发式兜底：银行/分行名让位给真正的交易对手
        self.assertEqual(bi._counterparty(
            ["来账", "汇款", "中信银行股份有限公司东莞分行", "东莞市绿邦实业有限公司"], "123"),
            "东莞市绿邦实业有限公司")

    # ---- 防误抢：建行 csv 表头是「账号」不带"查询"，仍走建行通道 ----
    def test_ccb_not_stolen_by_hisqry_sniff(self):
        p = os.path.join(self.dir, "随手改名的建行流水.csv")
        make_ccb_csv(p)
        self.assertFalse(bi.is_hisqry_csv(p))
        self.assertEqual(bi._classify(p), "skip")   # skip 后由 load_bank_dir 的建行内容检查接住
        self.assertTrue(bi.is_ccb_csv(p))

    # ---- 乱文件：内容对不上的照旧跳过，不能瞎认 ----
    def test_junk_still_skipped(self):
        px = os.path.join(self.dir, "理财持仓.xlsx")
        wb = Workbook()
        wb.active.append(["产品名称", "净值", "份额"])
        wb.save(px)
        pc = os.path.join(self.dir, "备注.csv")
        with open(pc, "w", encoding="utf-8") as f:
            f.write("a,b,c\n1,2,3")
        self.assertEqual(bi._classify(px), "skip")
        self.assertEqual(bi._classify(pc), "skip")

    # ---- 端到端：混合目录一次扫，改名件全并入、乱文件标跳过 ----
    def test_load_bank_dir_end_to_end(self):
        make_treasury_xlsx(os.path.join(self.dir, "宁波+招商.xlsx"))
        make_hisqry_csv(os.path.join(self.dir, "中行7月流水.csv"))
        make_ccb_csv(os.path.join(self.dir, "建行明细.csv"))
        with open(os.path.join(self.dir, "说明.txt"), "w", encoding="utf-8") as f:
            f.write("与流水无关的备注")
        rows, manifest = bi.load_bank_dir(self.dir)
        types = {m["文件"]: m["类型"] for m in manifest}
        self.assertEqual(types["宁波+招商.xlsx"], "财资平台·宁波+招商")
        self.assertEqual(types["中行7月流水.csv"], "中行HISQRY")
        self.assertEqual(types["建行明细.csv"], "建设银行·明细CSV")
        self.assertEqual(types["说明.txt"], "跳过")
        banks = {r["银行"] for r in rows}
        self.assertIn("中国银行", banks)
        self.assertIn("建设银行", banks)
        self.assertEqual(len(rows), 5)   # 财资2 + 中行2 + 建行1


if __name__ == "__main__":
    unittest.main(verbosity=2)
