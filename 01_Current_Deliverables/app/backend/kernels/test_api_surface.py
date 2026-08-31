# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-08-13 | Author: Claude / c | Version: V2.286
# Description: 取数接口面自检——存货台账路由依赖 kingdee_client 的哪些函数，逐个断言存在且签名没变。
#   起因：V2.280 用字符串切片替换函数体时，把夹在两个锚点之间的 5 个函数一起删掉了
#   （按日期表/成本计算单/成本科目/损益明细）。而路由处一律 `except Exception`，
#   把 AttributeError 显示成"某某取数失败"的一行黄字，看着像金蝶那边的问题——
#   V2.280–284 的包都带着这个残缺发出去过，直到业务方问"这个明细还是导不出来吗"才暴露。
#   本测试是那次事故的护栏：**删函数会立刻红**，不必等真去调金蝶。
import inspect
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kingdee_client as kc


class TestKingdeeApiSurface(unittest.TestCase):
    # 函数名 → 必须具备的参数名（只查关键的，不锁死全部签名，留改动余地）
    REQUIRED = {
        "fetch_inventory_summary": ("year", "period", "org"),
        "fetch_inventory_bydate": ("year", "period", "org"),          # 勾稽①两表互勾
        "fetch_inventory_period_totals": ("year", "p_from", "p_to", "org"),   # 存货看板
        "fetch_business_type_summary": ("year", "period", "org", "billnos"),  # 事务类型 + 物料级明细
        "fetch_pnl_details": ("year", "period", "org"),               # 第⑦步损益归集
        "fetch_cost_calc": ("year", "period", "org"),                 # 第⑧步制造费用
        "fetch_cost_gl": ("year", "period", "org"),                   # 成本三道勾稽的"账"
        "fetch_gl_balance": ("year", "period", "book"),               # 勾稽③账实
        "fetch_material_categories": (),
        "fetch_warehouses": ("org",),
        "fetch_account_books": (),
    }

    def test_all_present(self):
        missing = [n for n in self.REQUIRED if not hasattr(kc, n)]
        self.assertEqual(missing, [], f"kingdee_client 缺函数：{missing}——多半是编辑时被整段删掉了")

    def test_signatures(self):
        for name, params in self.REQUIRED.items():
            fn = getattr(kc, name, None)
            self.assertTrue(callable(fn), f"{name} 不可调用")
            got = set(inspect.signature(fn).parameters)
            for p in params:
                self.assertIn(p, got, f"{name} 少了参数 {p}")

    def test_report_ids(self):
        """报表 formid 是实测锁定的，改错一个整条线就取不到数。"""
        self.assertEqual(kc.RPT_INOUT_CROSSDIM, "HS_INOUTSTOCKSUMMARYRPT")
        self.assertEqual(kc.RPT_INOUT_BYDATE, "HS_INOUTSTOCKSUMMARYBYDATERPT")
        self.assertEqual(kc.RPT_INOUT_FLOW, "HS_NoDimInOutStockDetailRpt")
        self.assertEqual(kc.RPT_COST_CALC, "CB_CostCalBill")

    def test_period_totals_is_per_period(self):
        """V2.280：多期必须逐期各取一次——区间取法会把前期残留带进中间月份。"""
        src = inspect.getsource(kc.fetch_inventory_period_totals)
        self.assertIn("fetch_inventory_summary", src,
                      "存货看板的多期取数必须逐期调 fetch_inventory_summary，不能用区间一次取")


if __name__ == "__main__":
    unittest.main()


class TestCostLedgerClassifiers(unittest.TestCase):
    """两张"发现一个补一个"的清单，别被人顺手删短了（V2.307 护栏）。

    它们的共同点：**漏一项不会报错，只会让某个月的数悄悄不平/归错类**，
    而且往往要等业务方拿手工底稿来比才发现。"""

    def test_wip_credit_btypes(self):
        """贷记生产成本的事务类型。少「生产入库」→ 2026-7 勾稽②差 32,917.85。"""
        from kernels.cost_ledger import WIP_CREDIT_BTYPES
        for bt in ("汇报入库", "生产退库", "生产入库"):
            self.assertIn(bt, WIP_CREDIT_BTYPES, "%s 被删了会导致某些月份完工结转不平" % bt)

    def test_pnl_classify_by_expense_item(self):
        """损益归集按【费用项目】分类（V2.308）。业务方：「不应该按照摘要吧，肯定有核算维度」——
        对，`FDETAILID.FFLEX9` 就是费用项目。摘要黑名单降级为"没挂费用项目时"的兜底。"""
        for w in ("货损", "盘盈亏"):
            self.assertIn(w, kc._PNL_LOSS_ITEM)
        self.assertIn("处置", kc._PNL_DISP_ITEM)
        for w in ("福利领用", "捐赠"):
            self.assertIn(w, kc._PNL_NOT_LOSS)

    def test_pnl_fetch_requests_the_dimension(self):
        """取数必须把费用项目那一列要回来——不要，分类就退化回摘要。"""
        import inspect
        src = inspect.getsource(kc.fetch_pnl_details)
        self.assertIn("FDETAILID.FFLEX9.FName", src)

    def test_pnl_bill_regex_matches_real_notes(self):
        """单据号正则得认得住真实摘要的两种写法（有「的」和没「的」）。"""
        for note, want in [
            ("单据号QTCK011302报废出库无包材的其他出库单", "QTCK011302"),
            ("单据号PKSH000363的盘亏毁损单", "PKSH000363"),
            ("单据编号PRODIS00000079 的卡片处置", "PRODIS00000079"),
        ]:
            m = kc._PNL_BILL_RE.search(note)
            self.assertIsNotNone(m, note)
            self.assertEqual(m.group(1), want)


class TestPnlThirdBucket(unittest.TestCase):
    """第三档「其他存货出库」必须回到 result["pnl"] 里，不能只活在 _raw。

    事故（V2.312）：V2.307 把福利领用/捐赠从货损拆成第三档，只落进 `_raw.pnl_detail`；
    导出页直接读 _raw 所以有，**前端只认 res["pnl"]** 所以没有。
    🧪 101 深圳星期零 6 月货损 0／处置 0／其他 26,003.58 —— 第⑦步页面一片空白，
    业务方原话「福利领用还是不出来」。同一份数据两条路各走各的，迟早对不上。"""

    def test_collect_pnl_returns_other(self):
        import kernels.cost_ledger as clg
        r = clg.collect_pnl(
            [], [],
            [{"cat": "产品领用福利", "acct": "6602", "acct_name": "管理费用", "amount": 3422.02},
             {"cat": "捐赠", "acct": "6711", "acct_name": "营业外支出", "amount": 17.57},
             {"cat": "捐赠", "acct": "6711", "acct_name": "营业外支出", "amount": 12514.05}])
        self.assertIn("other", r, "第三档没进 pnl —— 前端就是这么丢的")
        self.assertEqual(r["other"]["total"], 15953.64)
        self.assertEqual(r["other"]["by_item"], {"产品领用福利": 3422.02, "捐赠": 12531.62})
        # 按科目也要分：业务方看这块时问的是"哪些进了营业外支出"
        self.assertEqual(r["other"]["by_acct"],
                         {"6602 管理费用": 3422.02, "6711 营业外支出": 12531.62})
        # 第三档**不得**混进货损/处置合计
        self.assertEqual(r["loss"]["total"], 0.0)
        self.assertEqual(r["disposal"]["total"], 0.0)


class TestBillOnlyRowMerge(unittest.TestCase):
    """出库单有、流水表没有的行要补进明细页，且**只补不替**。

    🧪 QTCK011302：出库单 123 个分录行、收发存流水 122 行，差的是
    T00000145 口袋蛋白脆-出口版 10.8 千克 / 金额 0.00（整张单唯一零金额行）。
    业务方底稿照单据做故有它，工具回查走流水故没有 —— 124 行 vs 123 行。"""

    def test_merge_adds_only_missing(self):
        from routers.cost_ledger import _merge_bill_only_rows
        flow = [{"billno": "B1", "code": "M1", "wh": "W", "amount": 100.0,
                 "date": "2026-07-01", "voucher": "记570"}]
        lines = {"B1": [{"code": "M1", "name": "甲", "spec": "", "qty": 9, "unit": "kg",
                         "amount": 999.0, "wh": "W", "batch": ""},          # 流水已有 → 不能动
                        {"code": "M2", "name": "乙", "spec": "", "qty": 10.8, "unit": "千克",
                         "amount": 0.0, "wh": "W", "batch": ""}]}           # 流水没有 → 补
        out = _merge_bill_only_rows(flow, lines)
        self.assertEqual(len(out), 2, "应只补 1 行")
        kept = [r for r in out if r["code"] == "M1"][0]
        self.assertEqual(kept["amount"], 100.0,
                         "流水已有的行被单据金额覆盖了——金额口径必须以流水为准")
        added = [r for r in out if r["code"] == "M2"][0]
        self.assertEqual(added["amount"], 0.0)
        self.assertEqual(added["src"], "bill")
        self.assertIn("流水无此行", added["btype"], "补齐行要在事务类型列写明来源")
        # 元数据照同单已有的流水行填，不猜
        self.assertEqual(added["date"], "2026-07-01")
        self.assertEqual(added["voucher"], "记570")

    def test_no_bill_lines_is_noop(self):
        from routers.cost_ledger import _merge_bill_only_rows
        flow = [{"billno": "B1", "code": "M1", "wh": "W", "amount": 1.0}]
        self.assertIs(_merge_bill_only_rows(flow, {}), flow)


class TestRouterWiring(unittest.TestCase):
    """路由必须挂在**公开处理函数**上，不能挂到私有辅助函数头上。

    事故（V2.315）：V2.314 把 `_merge_bill_only_rows` 插进了
    `@router.get("/api/cost-ledger/export")` 和 `def cost_ledger_export` **中间**，
    于是装饰器修饰了那个辅助函数，FastAPI 把它的两个形参当成必填查询参数：
      「点击下载台账」→ 422 {"loc":["query","flow_rows"],"msg":"Field required"}
    Python 语法完全合法、导入不报错、126 项测试全绿 —— **只有真点一下才会发现**。
    这条护栏就是那一下。"""

    def test_no_route_bound_to_private_function(self):
        from routers.cost_ledger import router
        bad = [(r.path, r.name) for r in router.routes if r.name.startswith("_")]
        self.assertEqual(bad, [], "路由挂到了私有辅助函数上：%s" % bad)

    def test_export_route_present_and_clean(self):
        from routers.cost_ledger import router
        hit = [r for r in router.routes if r.path == "/api/cost-ledger/export"]
        self.assertEqual(len(hit), 1, "导出路由不见了或重复了")
        self.assertEqual(hit[0].name, "cost_ledger_export")
        # 查询参数只该有这三个；多出来的必然是装饰器串位（辅助函数的形参被当成了 query）
        names = {f.name for f in hit[0].dependant.query_params}
        self.assertEqual(names, {"year", "period", "org"},
                         "导出接口的查询参数变了：%s" % sorted(names))
