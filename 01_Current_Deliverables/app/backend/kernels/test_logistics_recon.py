# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-07-08 | Author: Claude / c | Version: V2.44
# Description: 物流对账内核单元测试（合成数据，不连金蝶）。覆盖：零担/加急/快运分档与最低收费/
#   山姆板折与武汉京东特例/首衡仓储、×1.1 折算、内部单剔除、别名确认与未确认、9 态归因、
#   入向与补送人工项、三道勾稽。真数据端到端另见 tools/run_recon_jxd.py。
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kernels import logistics_recon as lr

CFG = lr.load_config(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "sample_data", "logistics_recon_极鲜达.json"))
D = datetime.date


def ltl_row(**kw):
    base = {"sheet": "零担快运", "line": 3, "origin": "孝感", "biz": "零担", "waybill": "8005",
            "cust": "东莞呈丰", "dest": "东莞", "ship_date": D(2026, 6, 3), "svc": "送货",
            "pieces": 50, "weight": 500.0, "unit_price": 0.62, "freight": 310.0,
            "delivery": 300.0, "pickup": 0, "unload": 0, "upstairs": 0, "other": 0,
            "total": 610.0, "note": "", "inbound": False}
    base.update(kw)
    return base


def doc(no="XSCKD1", date=D(2026, 6, 3), cust="东莞呈丰科技有限公司", kg=500.0,
        bags=1000.0, addr="广东省东莞市xx路", mats=("低脂低GI豆腐面-山姆",), internal=False):
    return {"no": no, "form": "SAL_OUTSTOCK", "date": date, "cust": cust, "kg": kg,
            "bags": bags, "addr": addr, "mats": set(mats), "internal": internal}


class TestPricing(unittest.TestCase):
    def test_ltl_standard(self):
        p = lr.price_ltl(ltl_row(), CFG)
        self.assertAlmostEqual(p["trunk"], 310.0)
        self.assertEqual(p["delivery"], 300)

    def test_ltl_urgent_uplift(self):
        p = lr.price_ltl(ltl_row(biz="加急零担", dest="北京", weight=1848.0, svc="特殊送仓"), CFG)
        self.assertAlmostEqual(p["rate"], 0.77)
        self.assertAlmostEqual(p["trunk"], 1848 * 0.77)
        self.assertEqual(p["delivery"], 430)

    def test_ltl_unknown_city(self):
        self.assertIsNone(lr.price_ltl(ltl_row(dest="拉萨"), CFG))

    def test_express_tier_and_min(self):
        p = lr.price_express(ltl_row(biz="快运", dest="深圳", weight=220.0, svc="特殊送仓"), CFG)
        self.assertAlmostEqual(p["trunk"], 220 * 1.7)   # 200-500 档二级地市
        self.assertEqual(p["delivery"], 100)
        p2 = lr.price_express(ltl_row(biz="快运", dest="深圳", weight=20.0, svc="送货"), CFG)
        self.assertAlmostEqual(p2["trunk"], 165)         # 低于最低收费
        self.assertTrue(p2["min_applied"])

    def test_sam_pallet(self):
        row = {"sheet": "山姆", "dc": "成都FDC", "pieces": 48, "pallets": 1.0, "weight": 650.0,
               "trunk": 468.0, "delivery": 300.0, "note": ""}
        p = lr.price_sam(row, CFG)
        self.assertAlmostEqual(p["rate"], 0.72)
        self.assertAlmostEqual(p["trunk"], 650 * 0.72)
        self.assertAlmostEqual(p["exp_weight"], 650.0)

    def test_sam_wuhan_and_jd(self):
        wh = lr.price_sam({"sheet": "山姆", "dc": "武汉FDC", "pieces": 64, "pallets": 1.5,
                           "weight": 975.0, "trunk": 0, "delivery": 400, "note": ""}, CFG)
        self.assertEqual(wh["trunk"], 0)
        self.assertEqual(wh["delivery"], 400)
        jd = lr.price_sam({"sheet": "山姆", "dc": "北京京东", "pieces": 48, "pallets": None,
                           "weight": 624.0, "trunk": 480.48, "delivery": 300, "note": ""}, CFG)
        self.assertAlmostEqual(jd["exp_weight"], 624.0)  # 48×13
        self.assertAlmostEqual(jd["trunk"], 624 * 0.77)


class TestMatch(unittest.TestCase):
    def test_internal_excluded(self):
        docs = [doc(no="A", cust="深圳市星期零食品科技有限公司", internal=True), doc(no="B")]
        m = lr.match_row(ltl_row(), docs, CFG)
        self.assertEqual(m["match"]["no"], "B")

    def test_signals_route_weight_alias(self):
        docs = [doc(no="B"), doc(no="C", cust="别家公司", kg=999.0, addr="上海市")]
        m = lr.match_row(ltl_row(), docs, CFG)
        self.assertEqual(m["match"]["no"], "B")
        self.assertIn("线路", m["method"])
        self.assertIn("重量", m["method"])

    def test_factor_conversion_match(self):
        # 鱼你：账单计重 = ERP×1.1
        docs = [doc(no="Y", cust="杭州熠陪你供应链管理有限公司", kg=1680.0, addr="陕西省西安市xx")]
        row = ltl_row(cust="西安鱼你", dest="西安", weight=1848.0)
        m = lr.match_row(row, docs, CFG)
        self.assertEqual(m["match"]["no"], "Y")

    def test_unconfirmed_alias_only_candidates(self):
        # 未确认别名 + 无线路无重量 → 只给候选不锁定（宁标不猜）
        docs = [doc(no="X", cust="顺新晖(东莞)供应链管理有限公司", kg=999.0, addr="江苏南通")]
        row = ltl_row(cust="上海夏晖", dest="上海", weight=1400.0)
        m = lr.match_row(row, docs, CFG)
        self.assertIsNone(m["match"])

    def test_sam_match_by_cases(self):
        docs = [doc(no="S1", cust=CFG["sam"]["customer"], kg=522.24, bags=384, addr="四川省成都市"),
                doc(no="S2", cust=CFG["sam"]["customer"], kg=783.36, bags=576, addr="四川省成都市")]
        row = {"sheet": "山姆", "line": 3, "dc": "成都FDC", "ship_date": D(2026, 6, 3),
               "pieces": 48, "pallets": 1.0, "weight": 650.0, "inbound": False}
        m = lr.match_sam_global([row], docs, CFG)[3]
        self.assertEqual(m["match"]["no"], "S1")   # 384袋/8=48箱

    def test_sam_ordered_alignment_beats_greedy(self):
        # 桶内混入窗口外多余单据：账单 6-15/6-18 两行，单据 6-13/6-15/7-02 三张
        # 贪心就近会把 6-15 行抢到 6-15 单；保序对齐应给 6-13、6-15（与人工回填一致场景）
        C = CFG["sam"]["customer"]
        docs = [doc(no="A", cust=C, kg=435.2, bags=320, addr="沈阳市xx", date=D(2026, 6, 13)),
                doc(no="B", cust=C, kg=435.2, bags=320, addr="沈阳市xx", date=D(2026, 6, 15)),
                doc(no="X", cust=C, kg=435.2, bags=320, addr="沈阳市xx", date=D(2026, 7, 2))]
        rows = [{"sheet": "山姆", "line": 1, "dc": "沈阳FDC", "ship_date": D(2026, 6, 15),
                 "pieces": 40, "pallets": 1.0, "weight": 650.0, "inbound": False},
                {"sheet": "山姆", "line": 2, "dc": "沈阳FDC", "ship_date": D(2026, 6, 18),
                 "pieces": 40, "pallets": 1.0, "weight": 650.0, "inbound": False}]
        m = lr.match_sam_global(rows, docs, CFG)
        self.assertEqual(m[1]["match"]["no"], "A")
        self.assertEqual(m[2]["match"]["no"], "B")

    def test_ltl_group_match(self):
        # 对账组：账单一行 500kg = 同客户两张单 300+200
        docs = [doc(no="G1", kg=300.0), doc(no="G2", kg=200.0, date=D(2026, 6, 4))]
        row = ltl_row(weight=500.0, freight=310.0)
        m = lr.match_row(row, docs, CFG)
        self.assertEqual({d["no"] for d in m.get("group", [])}, {"G1", "G2"})
        c = lr.classify_row(row, m, CFG)
        self.assertEqual(c["state"], lr.ST_OK)
        self.assertEqual(c["match_no"], "G1+G2")


class TestClassify(unittest.TestCase):
    def _one(self, row, docs):
        m = lr.match_row(row, docs, CFG)
        return lr.classify_row(row, m, CFG)

    def test_ok(self):
        c = self._one(ltl_row(), [doc()])
        self.assertEqual(c["state"], lr.ST_OK)

    def test_price_overcharge_xian(self):
        # 西安 0.67 vs 合同 0.57 → 多收·单价 184.8
        row = ltl_row(cust="西安鱼你", dest="西安", weight=1848.0, unit_price=0.67,
                      freight=1848 * 0.67, svc="特殊送仓", delivery=430.0)
        docs = [doc(no="Y", cust="杭州熠陪你供应链管理有限公司", kg=1680.0, addr="陕西省西安市")]
        c = self._one(row, docs)
        self.assertEqual(c["state"], lr.ST_PRICE)
        self.assertAlmostEqual(c["diff"], 184.8, places=2)

    def test_price_undercharge_flagged(self):
        row = ltl_row(dest="天津", biz="零担", weight=250.0, freight=250 * 0.7, svc="自提", delivery=0)
        c = self._one(row, [doc(kg=250.0, addr="天津市xx")])
        self.assertEqual(c["state"], lr.ST_PRICE)
        self.assertTrue(c["diff"] < 0)
        self.assertIn("少收", c["reason"])

    def test_surcharge(self):
        row = ltl_row(svc="特殊送仓", delivery=600.0, note="专车送仓")
        c = self._one(row, [doc(addr="广东省东莞市")])
        self.assertEqual(c["state"], lr.ST_SURCH)
        self.assertAlmostEqual(c["diff"], 170.0)   # 600-430

    def test_weight_mismatch(self):
        row = ltl_row(weight=500.0, freight=310.0)
        c = self._one(row, [doc(kg=460.0)])        # ERP 460 vs 账单 500
        self.assertEqual(c["state"], lr.ST_WEIGHT)

    def test_no_rule(self):
        c = self._one(ltl_row(dest="昌邑", freight=1174.08), [])
        self.assertEqual(c["state"], lr.ST_NO_RULE)

    def test_manual_inbound_and_makeup(self):
        c1 = self._one(ltl_row(origin="山东新和盛", dest="孝感", inbound=True), [])
        self.assertEqual(c1["state"], lr.ST_MANUAL)
        c2 = self._one(ltl_row(ship_date=None, svc="补送"), [])
        self.assertEqual(c2["state"], lr.ST_MANUAL)

    def test_price_ok_but_unmatched_goes_manual(self):
        c = self._one(ltl_row(), [])
        self.assertEqual(c["state"], lr.ST_MANUAL)

    def test_warehouse_shuttle_overcharge(self):
        row = {"sheet": "首衡外仓", "line": 5, "date": D(2026, 6, 5), "item": "速冻米麻薯",
               "in_tons": None, "in_price": None, "out_tons": 0.99, "out_price": 25.0,
               "handling": 24.75, "storage_fee": 24.75, "shuttle_type": "4.2米冷藏车",
               "shuttle_n": 1, "shuttle_price": 260.0, "shuttle_fee": 260.0, "inbound": False}
        c = self._one(row, [])
        self.assertEqual(c["state"], lr.ST_SURCH)
        self.assertAlmostEqual(c["diff"], 60.0)    # 260-200


class TestGuards(unittest.TestCase):
    def test_reconcile_counts_and_amounts(self):
        bill = {"summary": {"零担快运": 610.0}, "rows": [ltl_row()]}
        docs = [{"单号": "XSCKD1", "日期": "2026-06-03", "客户": "东莞呈丰科技有限公司",
                 "物料": "低脂低GI豆腐面-山姆", "数量": 1000.0, "kg": 500.0,
                 "收货地址": "广东省东莞市xx路", "form": "SAL_OUTSTOCK"}]
        res = lr.reconcile(bill, docs, CFG)
        self.assertTrue(res["guards"]["笔数勾稽"])
        self.assertTrue(res["guards"]["金额勾稽"])
        self.assertEqual(res["stats"]["各态"][lr.ST_OK], 1)
        self.assertEqual(res["rows"][0]["match_no"], "XSCKD1")


class TestTianying(unittest.TestCase):
    """天鹰解析（V2.150）：多文件·多sheet、两种列布局、吨数×18.5 计费、单号向下填充。合成数据。"""

    @staticmethod
    def _wb(sheets):
        """sheets=[(sheetname, header_list, [row_list,...])] → xlsx bytes。第1行留标题占位。"""
        import openpyxl
        from io import BytesIO
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        for name, header, rows in sheets:
            ws = wb.create_sheet(name)
            ws.append(["记录表标题"])                 # R1 标题占位
            ws.append(header)                        # R2 表头
            for r in rows:
                ws.append(r)
        bio = BytesIO()
        wb.save(bio)
        return bio.getvalue()

    def test_two_layouts_and_filldown_and_tieout(self):
        # 装货布局：序号|产品名称|物料编码|规格|收货仓|物流车信息|数量(箱)|吨数(T)|装货日期|金蝶单据编号|含税金额|备注
        load_hdr = ["序号", "产品名称", "物料编码", "规格", "收货仓", "物流车信息", "数量(箱）",
                    "吨数(T)", "装货日期", "金蝶单据编号", "含税金额（18.5元/吨）", "备注"]
        load_rows = [
            [1, "豆腐A", "305000001", "200g", "广州", "鄂W806Q5", 54, 0.17, "2026-06-01", "XSCKD207315", 3.145, ""],
            [2, "豆腐B", "305000001", "200g", "", "", 25, 0.25, "", "", 4.625, ""],   # 续行：无单号→继承 XSCKD207315
            ["合计", "", "", "", "", "", "", 0.42, "", "", 7.77, ""],                  # 合计行
        ]
        # 卸货布局：序号|物料编码|产品名称|规格型号|数量(KG)|吨数(T)|卸货日期|物流信息|金蝶入库单号|含税金额|备注
        unload_hdr = ["序号", "物料编码", "产品名称", "规格型号", "数量（KG）", "吨数(T)", "卸货日期",
                      "物流信息", "金蝶入库单号", "含税金额（18.5元/吨）", "备注"]
        unload_rows = [
            [1, "104000006", "可得然胶", "25KG", 1075, 1.075, "2026-06-09", "鄂aba7716", "CGRK171950", 19.8875, ""],
            [2, "108010153", "苏北大豆", "48.2kg", 48.2, 0.0482, "2026-06-10", "", "", 0.8917, ""],   # 续行→继承 CGRK171950
            [3, "108010007", "冰鸡蛋白", "10kg", 100, 0.1, "2026-06-11", "", "CGRK171951", None, ""],  # 有单号无金额→no_amount(仍参与核量)
            ["合计", "", "", "", "", 1.2232, "", "", "6%含税金额", 20.7792, ""],
        ]
        # 独立 sheet：首行即无单号（无从继承）→ 真·待人工
        noorphan_hdr = list(unload_hdr)
        noorphan_rows = [[1, "999", "无单号货", "x", 50, 0.05, "2026-06-12", "", "", 0.925, ""],
                         ["合计", "", "", "", "", 0.05, "", "", "6%含税金额", 0.925, ""]]
        f1 = self._wb([("植物肉装货明细", load_hdr, load_rows)])
        f2 = self._wb([("植物肉卸货明细", unload_hdr, unload_rows),
                       ("无单号明细", noorphan_hdr, noorphan_rows), ("Sheet1", [], [])])
        bill = lr.parse_bill("天鹰物流", [f1, f2])

        self.assertEqual(bill["warnings"], [])
        rows = bill["rows"]
        self.assertEqual(len(rows), 6)                                  # 2+3+1 明细行（合计不计）
        # 勾稽：明细求和 == 合计（无单号明细该 sheet 无合计行，不影响；断言两文件合计对齐明细）
        self.assertAlmostEqual(bill["summary"]["明细求和"], bill["summary"]["合计"], places=2)
        # 单号向下填充
        by = {(r["sheet"], r["seq"]): r for r in rows}
        self.assertEqual(by[("天鹰-植物肉装货明细", 2)]["backfill_no"], "XSCKD207315")
        self.assertTrue(by[("天鹰-植物肉装货明细", 2)]["inherited"])
        self.assertEqual(by[("天鹰-植物肉卸货明细", 2)]["backfill_no"], "CGRK171950")
        # 核价自洽：吨数×18.5==金额
        for r in rows:
            if r["tons"] and r["total"] is not None:
                self.assertAlmostEqual(r["tons"] * 18.5, r["total"], places=2)
        # weight 存 KG
        self.assertAlmostEqual(by[("天鹰-植物肉装货明细", 1)]["weight"], 170.0, places=1)
        # V2.151：有单号无金额 → no_amount=True 但 pending=False（仍参与核量分组）
        r3 = by[("天鹰-植物肉卸货明细", 3)]
        self.assertTrue(r3["no_amount"])
        self.assertFalse(r3["pending"])
        # 真·待人工＝无单号可继承的那 1 行
        orphan = by[("天鹰-无单号明细", 1)]
        self.assertEqual(orphan["backfill_no"], "")
        self.assertTrue(orphan["pending"])
        self.assertEqual(sum(1 for r in rows if r["pending"]), 1)

    def test_duplicate_sheet_skipped(self):
        """整表重复必须跳过：物流部门留「一份带图一份不带图」，全读会把同批货算两遍。"""
        hdr = ["序号", "产品名称", "物料编码", "规格", "收货仓", "物流车信息", "数量(箱）",
               "吨数(T)", "装货日期", "金蝶单据编号", "含税金额（18.5元/吨）", "备注"]
        rows = [[1, "糯米制品", "300600096", "500g", "诸暨", "皖D56626", 2650, 26.5, "2026-06-10", "XSCKD208316", 490.25, ""]]
        f = self._wb([("小料装货明细", hdr, rows), ("小料装货明细 (2)", hdr, rows)])
        bill = lr.parse_bill("天鹰物流", [f])
        self.assertEqual(len(bill["rows"]), 1)                     # 不是 2
        self.assertAlmostEqual(bill["rows"][0]["weight"], 26500.0)  # 不是 53000
        self.assertAlmostEqual(bill["summary"]["明细求和"], 490.25)
        self.assertTrue(any("内容完全相同" in n for n in bill["notices"]))

    def test_same_file_twice_deduped(self):
        """多文件上传误把同一文件选两次，也不能翻倍。"""
        hdr = ["序号", "物料编码", "产品名称", "规格型号", "数量（KG）", "吨数(T)", "卸货日期",
               "物流信息", "金蝶入库单号", "含税金额（18.5元/吨）", "备注"]
        rows = [[1, "104000006", "可得然胶", "25KG", 1075, 1.075, "2026-06-09", "鄂a1", "CGRK171950", 19.8875, ""]]
        f = self._wb([("植物肉卸货明细", hdr, rows)])
        one = lr.parse_bill("天鹰物流", [f])
        two = lr.parse_bill("天鹰物流", [f, f])
        self.assertEqual(len(two["rows"]), len(one["rows"]))
        self.assertAlmostEqual(two["summary"]["明细求和"], one["summary"]["明细求和"])
        self.assertTrue(any("内容完全相同" in n for n in two["notices"]))

    def test_detail_row_without_seq_kept(self):
        """有单号有金额但没填序号的行是真明细，不得丢——旧规则靠数字序号会静默漏计费用。
        同时：不写"合计"二字、只有数值没有货物标识的行，要认成合计行。"""
        hdr = ["序号", "产品名称", "物料编码", "规格", "收货仓", "物流车信息", "数量(箱）",
               "吨数(T)", "装货日期", "金蝶单据编号", "含税金额（18.5元/吨）", "备注"]
        rows = [
            ["", "糯米制品", "300600096", "500g", "武汉", "鄂A1", 1300, 13, "2026-06-05", "XSCKD209569", 240.5, "系统单据为6月份"],
            [1, "木薯糖水", "300600097", "200g", "昆山", "鄂A2", 237, 1.32, "2026-06-10", "XSCKD208236", 24.42, ""],
            ["", "", "", "", "", "", "", 14.32, "", "", 264.92, ""],      # 无货物标识的合计行（不写"合计"）
        ]
        bill = lr.parse_bill("天鹰物流", [self._wb([("小料装货明细", hdr, rows)])])
        self.assertEqual(len(bill["rows"]), 2)                     # 两条明细都在
        noseq = [r for r in bill["rows"] if r["backfill_no"] == "XSCKD209569"][0]
        self.assertIsNone(noseq["seq"])                            # 序号确实没填
        self.assertAlmostEqual(noseq["total"], 240.5)              # 但费用照算
        self.assertAlmostEqual(bill["summary"]["明细求和"], 264.92)
        self.assertAlmostEqual(bill["summary"]["合计"], 264.92)     # 无"合计"字样的行被认成合计行
        self.assertEqual(bill["notices"], [])                      # 吨数也自洽，无提示

    def test_tons_not_selfconsistent_notice(self):
        """账单合计行吨数漏加某行（金额却算了）＝承运商表内部矛盾，要提示不吞掉。"""
        hdr = ["序号", "产品名称", "物料编码", "规格", "收货仓", "物流车信息", "数量(箱）",
               "吨数(T)", "装货日期", "金蝶单据编号", "含税金额（18.5元/吨）", "备注"]
        rows = [
            [1, "甲", "1001", "x", "武汉", "鄂A1", 100, 13, "2026-06-05", "XSCKD1", 240.5, ""],
            [2, "乙", "1002", "x", "昆山", "鄂A2", 100, 1.32, "2026-06-10", "XSCKD2", 24.42, ""],
            ["合计", "", "", "", "", "", "", 1.32, "", "", 264.92, ""],   # 吨数漏加甲的 13 吨
        ]
        bill = lr.parse_bill("天鹰物流", [self._wb([("小料装货明细", hdr, rows)])])
        self.assertTrue(any("吨数不自洽" in n for n in bill["notices"]))

    def test_multi_flag_and_single_parser_guard(self):
        self.assertTrue(lr.PARSERS["天鹰物流"].get("multi"))
        self.assertFalse(lr.PARSERS["跨越物流"].get("multi"))
        # 单文件方案传多文件 → 报错
        with self.assertRaises(ValueError):
            lr.parse_bill("跨越物流", [b"x", b"y"])

    def test_template_changed_warns(self):
        # 缺"金蝶"列 → 找不到表头 → sheets_seen=0 → 明确报警（被 parse_bill 包成"疑似模板被改"）
        bad_hdr = ["序号", "产品名称", "吨数(T)", "含税金额（18.5元/吨）"]
        f = self._wb([("植物肉装货明细", bad_hdr, [[1, "A", 0.1, 1.85]])])
        with self.assertRaises(ValueError):
            lr.parse_bill("天鹰物流", [f])


class TestMaterialRecon(unittest.TestCase):
    """物料级核量（V2.152）：单号×物料编码 比 kg，金蝶为准，超容差落需人工复核。"""

    @staticmethod
    def _bill(rows):
        amt = sum(r.get("total") or 0 for r in rows)
        return {"summary": {"合计": round(amt, 2), "明细求和": round(amt, 2)}, "rows": rows}

    @staticmethod
    def _row(line, no, code, kg, total, product="货", pending=False):
        return {"line": line, "backfill_no": no, "mat_code": code, "weight": kg, "total": total,
                "product": product, "pending": pending}

    @staticmethod
    def _doc(no, code, kg, qty, unit="千克", name="货", form="采购入库单"):
        return {"单号": no, "物料编码": code, "kg": kg, "数量": qty, "基本单位": unit,
                "物料": name, "form_name": form}

    def test_states(self):
        bill = self._bill([
            self._row(3, "CGRK1", "1001", 1000.0, 18.5, "大豆"),          # 一致
            self._row(4, "FBDC1", "2002", 1075.0, 19.9, "肉糜"),          # 多报 25kg → 需人工复核
            self._row(5, "CGRK2", "3003", 600.0, 11.1, "印刷内袋"),        # 基本单位 Pcs → 无法核
            self._row(6, "CGRK1", "9999", 50.0, 0.9, "不在单里的货"),       # 金蝶无此物料
            self._row(7, "NOPE1", "1001", 80.0, 1.5, "查无单号货"),        # 单号查无
            self._row(8, "", "1001", 20.0, 0.4, "无单号", pending=True),   # 待人工
        ])
        docs = [
            self._doc("CGRK1", "1001", 1000.0, 1000.0, "千克", "大豆"),
            self._doc("FBDC1", "2002", 1050.0, 210.0, "千克", "肉糜", "分步式调出单"),
            self._doc("CGRK2", "3003", 96000.0, 96000.0, "Pcs", "印刷内袋"),
        ]
        res = lr.reconcile_by_material(bill, docs)
        by = {(r["单号"], r["物料编码"]): r for r in res["rows"]}

        self.assertEqual(by[("CGRK1", "1001")]["state"], lr.ST_MAT_OK)
        rev = by[("FBDC1", "2002")]
        self.assertEqual(rev["state"], lr.ST_MAT_REVIEW)
        self.assertAlmostEqual(rev["差异kg"], 25.0)
        self.assertEqual(rev["方向"], "账单多报")
        self.assertEqual(by[("CGRK2", "3003")]["state"], lr.ST_MAT_UNIT)
        self.assertIsNone(by[("CGRK2", "3003")]["差异kg"])            # 非kg计量不判差异
        self.assertEqual(by[("CGRK1", "9999")]["state"], lr.ST_MAT_NOMAT)
        self.assertEqual(by[("NOPE1", "1001")]["state"], lr.ST_MAT_NONO)
        self.assertEqual(by[("", "1001")]["state"], lr.ST_MAT_MANUAL)

        # 可核口径只含 一致+需复核（非kg计量/查无 不计入，避免总量假象）
        self.assertAlmostEqual(res["tieout"]["可核账单kg"], 1000.0 + 1075.0)
        self.assertAlmostEqual(res["tieout"]["可核金蝶kg"], 1000.0 + 1050.0)
        self.assertAlmostEqual(res["tieout"]["可核差异kg"], 25.0)

    def test_zero_tolerance_splits_by_direction(self):
        """容差 0，但按方向分态：多报→需人工复核（要追）；少报→我方有利（只标记，不强提醒）。"""
        # 0.17吨=170kg vs 实际172.8kg，差 -2.8kg → 少报＝我方有利
        bill = self._bill([self._row(3, "X1", "1001", 170.0, 3.145)])
        docs = [self._doc("X1", "1001", 172.8, 864.0, "千克")]
        r = lr.reconcile_by_material(bill, docs)["rows"][0]
        self.assertEqual(r["state"], lr.ST_MAT_UNDER)
        self.assertAlmostEqual(r["差异kg"], -2.8)
        self.assertEqual(r["方向"], "账单少报")
        # 多报同样抓
        bill2 = self._bill([self._row(3, "X1", "1001", 1200.0, 22.2)])
        docs2 = [self._doc("X1", "1001", 1000.0, 1000.0, "千克")]
        r2 = lr.reconcile_by_material(bill2, docs2)["rows"][0]
        self.assertEqual(r2["state"], lr.ST_MAT_REVIEW)
        self.assertEqual(r2["方向"], "账单多报")
        # 分毫不差才算一致
        bill3 = self._bill([self._row(3, "X1", "1001", 1000.0, 18.5)])
        docs3 = [self._doc("X1", "1001", 1000.0, 1000.0, "千克")]
        self.assertEqual(lr.reconcile_by_material(bill3, docs3)["rows"][0]["state"], lr.ST_MAT_OK)

    def test_under_billing_not_counted_as_review(self):
        """少报不得混进「需人工复核」——复核清单只放多报，否则真要追的被淹。"""
        bill = self._bill([self._row(3, "A", "1", 90.0, 1.665, "少报货"),
                           self._row(4, "B", "2", 98.0, 1.813, "少报货2"),
                           self._row(5, "C", "3", 1075.0, 19.9, "多报货")])
        docs = [self._doc("A", "1", 98.0, 98.0, "千克"),
                self._doc("B", "2", 109.44, 109.44, "千克"),
                self._doc("C", "3", 1050.0, 210.0, "千克")]
        res = lr.reconcile_by_material(bill, docs)
        self.assertEqual(res["stats"].get(lr.ST_MAT_REVIEW), 1)     # 只有多报那条
        self.assertEqual(res["stats"].get(lr.ST_MAT_UNDER), 2)
        # 少报仍进"可核"汇总（是真实比对过的重量，不能漏出总量口径）
        self.assertAlmostEqual(res["tieout"]["可核账单kg"], 90.0 + 98.0 + 1075.0)
        self.assertAlmostEqual(res["tieout"]["可核金蝶kg"], 98.0 + 109.44 + 1050.0)

    def test_float_noise_not_flagged(self):
        """克级取整避浮点噪声：0.0482吨×1000 在浮点下非整 48.2，不该报成差异。"""
        bill = self._bill([self._row(3, "X1", "1001", 0.0482 * 1000, 0.8917)])
        docs = [self._doc("X1", "1001", 48.2, 48.2, "千克")]
        r = lr.reconcile_by_material(bill, docs)["rows"][0]
        self.assertEqual(r["state"], lr.ST_MAT_OK)
        self.assertEqual(r["差异kg"], 0.0)

    def test_material_code_normalised(self):
        # Excel 数值单元格读成 305000001.0，须能与金蝶字符串编码对上
        bill = self._bill([self._row(3, "X1", "305000001.0", 100.0, 1.85)])
        docs = [self._doc("X1", "305000001", 100.0, 100.0, "千克")]
        self.assertEqual(lr.reconcile_by_material(bill, docs)["rows"][0]["state"], lr.ST_MAT_OK)

    def test_same_no_multiple_materials_each_compared(self):
        # 一个单号多物料：天鹰只拉子集，各物料各自比对，未拉的物料不算差异
        bill = self._bill([self._row(3, "Q1", "1001", 30.0, 0.555, "酥排"),
                           self._row(4, "Q1", "1002", 220.0, 4.07, "火腿片")])
        docs = [self._doc("Q1", "1001", 30.0, 30.0, "千克", "酥排", "其他出库单"),
                self._doc("Q1", "1002", 220.0, 220.0, "千克", "火腿片", "其他出库单"),
                self._doc("Q1", "7777", 500.0, 500.0, "千克", "天鹰没拉的货", "其他出库单")]
        res = lr.reconcile_by_material(bill, docs)
        self.assertEqual(len(res["rows"]), 2)                      # 只出账单有的两行；未拉的 7777 不算差异
        self.assertTrue(all(r["state"] == lr.ST_MAT_OK for r in res["rows"]))
        self.assertEqual(res["stats"].get(lr.ST_MAT_OK), 2)

    def test_same_no_one_material_off_only_that_one_flags(self):
        """一单多物料时，只有对不上的那个物料落需人工复核，不牵连同单其它物料。"""
        bill = self._bill([self._row(3, "Q1", "1001", 30.0, 0.555, "酥排"),
                           self._row(4, "Q1", "1002", 220.0, 4.07, "火腿片")])
        docs = [self._doc("Q1", "1001", 30.0, 30.0, "千克", "酥排", "其他出库单"),
                self._doc("Q1", "1002", 217.5, 217.5, "千克", "火腿片", "其他出库单")]
        by = {r["物料编码"]: r for r in lr.reconcile_by_material(bill, docs)["rows"]}
        self.assertEqual(by["1001"]["state"], lr.ST_MAT_OK)
        self.assertEqual(by["1002"]["state"], lr.ST_MAT_REVIEW)
        self.assertAlmostEqual(by["1002"]["差异kg"], 2.5)
        self.assertEqual(by["1002"]["方向"], "账单多报")


if __name__ == "__main__":
    unittest.main(verbosity=2)
