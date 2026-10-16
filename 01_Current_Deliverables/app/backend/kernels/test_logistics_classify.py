# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-16 | Author: Claude Opus 5.5 | Version: V2.986
# Description: 物流账单复核·费用归类判定单测。用例照 2026-09 真单(跨越/丰源/极鲜达)的金蝶字段造，不连金蝶。
import unittest

from kernels import logistics_classify as lc

SHORT = {"深圳市星期零食品科技有限公司": "深圳星期零", "深圳市星期九食品科技有限公司": "深圳星期九",
         "孝感市星期九食品科技有限公司": "孝感星期九"}
short = lambda x: SHORT.get(x, x)
CFG = lc.merge_cfg(None)
XG = "孝感市星期九食品科技有限公司"


def run(doc, kd, groups, accr=None):
    return lc.classify([doc], kd, groups, accr or [], CFG, short)


def accr(subj, acct, dept, fee, biz_code, amt):
    return {"subject": subj, "acct": acct, "dept": dept, "fee": fee, "biz_code": biz_code, "amt": amt}


class Classify(unittest.TestCase):
    def test_rd_sample_labelled_as_sample(self):
        """跨越 QTCK011740：领料部门是研发中心，物流部标「样品单-小料」，计提也跟着记在小料 → 标注不对、提示计提没有研发这一列。"""
        r = run({"key": "QTCK011740", "docs": ["QTCK011740"], "subject": "深圳星期零", "annot": "样品单-小料", "amount": 111.34},
                {"QTCK011740": {"form": "STK_MisDelivery", "head": {"部门": "永续研发中心", "库存组织": XG}, "codes": ["M1"]}},
                {"M1": "原料"}, [accr("深圳星期零", "6601.01", "永续物流中心", "出库运费", "CPFL009", 676.4)])
        d = r["docs"][0]
        self.assertEqual(d["v"], lc.V_BILL)
        self.assertEqual(d["sys"], "深圳星期零 · 研发费用")
        self.assertIn("费用类型", d["diff"])
        self.assertIn("「深圳星期零 · 研发费用」没有", d["hint"])
        self.assertTrue(d["acc_miss"])
        self.assertIn("判不出谁承担", d["subj_note"])

    def test_sale_subject_from_sales_org(self):
        """丰源 XSCKD250287：销售组织是深圳星期零，物流部标孝感星期九 → 标注不对(主体)。"""
        r = run({"key": "XSCKD250287", "docs": ["XSCKD250287"], "subject": "孝感星期九", "annot": "销售单-零售其他", "amount": 630.72},
                {"XSCKD250287": {"form": "SAL_OUTSTOCK", "head": {"销售组织": "深圳市星期零食品科技有限公司", "库存组织": XG}, "codes": ["M2"]}},
                {"M2": "零售其他"}, [accr("深圳星期零", "6601.01", "永续物流中心", "出库运费", "CPFL011", 1359.32)])
        d = r["docs"][0]
        self.assertEqual(d["v"], lc.V_BILL)
        self.assertEqual(d["diff"], ["主体"])
        self.assertEqual(d["sys"], "深圳星期零 · 零售")
        self.assertFalse(d["acc_miss"])

    def test_oem_to_jxd_stock_is_outbound_by_default(self):
        """极鲜达 FBDC077153 和盛园仓→武汉极鲜达仓(鲜食山姆)：默认规则按 9 月计提的做法记深圳星期零出库(山姆鲜食)；
        物流部标「销售单-山姆零售」→ 业务线不对，但计提已经在山姆鲜食。"""
        r = run({"key": "FBDC077153", "docs": ["FBDC077153"], "subject": "深圳星期零", "annot": "销售单-山姆零售", "amount": 5800},
                {"FBDC077153": {"form": "STK_TransferOut", "head": {"调入组织": XG}, "party": "和盛园仓→武汉极鲜达仓", "codes": ["M3", "M4"]}},
                {"M3": "鲜食山姆", "M4": "鲜食山姆"}, [accr("深圳星期零", "6601.01", "永续物流中心", "出库运费", "CPFL010", 68030.5)])
        d = r["docs"][0]
        self.assertEqual(d["sys"], "深圳星期零 · 山姆鲜食")
        self.assertEqual(d["v"], lc.V_BILL)
        self.assertEqual(d["diff"], ["业务线"])
        self.assertFalse(d["acc_miss"])

    def test_transfer_to_ecom_stock(self):
        """丰源 FBDR075947 孝感成品仓→迅鸽星期零仓：默认记孝感工厂入库；标注「调拨单-电商」按同一规则落同一列 → 一致；
        计提没有这一列 → 计提记错。"""
        doc = {"key": "FBDR075947", "docs": ["FBDR075947"], "subject": "孝感星期九", "annot": "调拨单-电商", "amount": 1300}
        kd = {"FBDR075947": {"form": "STK_TransferIn", "head": {"调入组织": XG}, "party": "孝感成品仓→迅鸽星期零仓", "codes": ["M5"]}}
        ok = run(doc, kd, {"M5": "零售其他"}, [accr("孝感星期九", "5101.01", "仓储物流部", "入库运费", "", 3000)])["docs"][0]
        self.assertEqual(ok["v"], lc.V_OK)
        self.assertEqual(ok["sys"], "孝感星期九 · 孝感工厂入库")
        other = [accr("孝感星期九", "5101.01", "茶饮小料部", "入库运费", "", 4700)]
        miss = run(doc, kd, {"M5": "零售其他"}, other)["docs"][0]
        self.assertEqual(miss["v"], lc.V_ACCR)

    def test_no_accrual_fetched_does_not_flag(self):
        """一条计提都没取到(金蝶没连上/还没计提)：不能整家判「计提记错」。"""
        doc = {"key": "FBDR075947", "docs": ["FBDR075947"], "subject": "孝感星期九", "annot": "调拨单-电商", "amount": 1300}
        kd = {"FBDR075947": {"form": "STK_TransferIn", "head": {"调入组织": XG}, "party": "孝感成品仓→迅鸽星期零仓", "codes": ["M5"]}}
        r = run(doc, kd, {"M5": "零售其他"}, [])
        self.assertEqual(r["docs"][0]["v"], lc.V_OK)
        self.assertFalse(r["accr_ok"])
        self.assertIn("没取到", r["accr_note"])

    def test_oem_transfer_label_without_bizline(self):
        """极鲜达 FBDR075913 孝感成品仓→山东新和盛仓：代工厂入库；标注「代工厂调拨」没写业务线，按金蝶补 → 一致。"""
        r = run({"key": "FBDR075913", "docs": ["FBDR075913"], "subject": "孝感星期九", "annot": "代工厂调拨", "amount": 1270},
                {"FBDR075913": {"form": "STK_TransferIn", "head": {"调入组织": XG}, "party": "孝感成品仓→山东新和盛仓", "codes": ["M6"]}},
                {"M6": "植物肉"}, [accr("孝感星期九", "6401.01", "仓储物流部", "入库运费", "CPFL007", 2873.12)])
        d = r["docs"][0]
        self.assertEqual(d["v"], lc.V_OK)
        self.assertIn("没写业务线", d["txt"])

    def test_raw_material_cannot_tell(self):
        """原料采购入库：看不出是小料还是工厂用的 → 判不了，认标注。"""
        r = run({"key": "CGRK183533", "docs": ["CGRK183533"], "subject": "孝感星期九", "annot": "采购入库-植物肉", "amount": 116.78},
                {"CGRK183533": {"form": "STK_InStock", "head": {"需求组织": XG}, "codes": ["M7"]}}, {"M7": "原料"})
        d = r["docs"][0]
        self.assertEqual(d["v"], lc.V_UNK)
        self.assertIn("原料", d["unk"])
        self.assertIn("孝感工厂入库", d["txt"])

    def test_packaging_only_and_mixed(self):
        self.assertEqual(lc.biz_of_groups(["包材"], CFG)[0], "")
        self.assertIn("几个业务线", lc.biz_of_groups(["小料", "零售山姆"], CFG)[2])
        self.assertEqual(lc.biz_of_groups(["小料", "包材"], CFG)[0], "小料")
        self.assertEqual(lc.biz_of_groups(["小料", "原料"], CFG)[0], "小料")
        self.assertEqual(lc.biz_of_groups(["原料"], CFG)[0], "")

    def test_no_doc_and_unlabelled(self):
        r = run({"key": "外仓!4", "docs": [], "subject": "孝感星期九", "annot": "（未标）", "amount": 900}, {}, {})
        self.assertEqual(r["docs"][0]["v"], lc.V_UNK)

    def test_crosscheck_table(self):
        """对照表：判不了的按标注放；系统判和计提差 ≥1 元、且和标注不一样的标 hot。"""
        r = lc.classify(
            [{"key": "A", "docs": ["A"], "subject": "孝感星期九", "annot": "销售单-零售其他", "amount": 630.72}],
            {"A": {"form": "SAL_OUTSTOCK", "head": {"销售组织": "深圳市星期零食品科技有限公司"}, "codes": ["M2"]}},
            {"M2": "零售其他"}, [accr("深圳星期零", "6601.01", "永续物流中心", "出库运费", "CPFL011", 630.72)], CFG, short)
        t = {x["name"]: x for x in r["tab"]}
        self.assertEqual(t["深圳星期零 · 零售"]["accr"], 630.72)
        self.assertEqual(t["深圳星期零 · 零售"]["by_sys"], 630.72)
        self.assertFalse(t["深圳星期零 · 零售"]["hot"])
        self.assertEqual(t["孝感星期九 · 零售出库"]["by_bill"], 630.72)
        self.assertEqual(r["accr_total"], 630.72)
        self.assertEqual(r["counts"][lc.V_BILL], 1)

    def test_outside_warehouse_storage_is_inbound(self):
        """恒茂(工厂外仓) 8 月：标注「仓储费-小料」，按 10-12 口径算入库运费记小料入库；8 月计提记成 6601 货物仓储费 → 计提记错。
        原料采购入库在恒茂默认算小料，不再判不了。"""
        acc = [accr("孝感星期九", "6601.01", "仓储物流部", "货物仓储费", "", 78733.53)]
        kd = {"FBDR075332": {"form": "STK_TransferIn", "head": {"调入组织": XG}, "party": "孝感原料仓→恒茂外仓", "codes": ["M8"]},
              "CGRK180497": {"form": "STK_InStock", "head": {"需求组织": XG}, "codes": ["M7"]}}
        r = lc.classify([{"key": "FBDR075332", "docs": ["FBDR075332"], "subject": "孝感星期九", "annot": "仓储费-小料", "amount": 802.78},
                         {"key": "CGRK180497", "docs": ["CGRK180497"], "subject": "孝感星期九", "annot": "仓储费-小料", "amount": 238.62}],
                        kd, {"M8": "小料", "M7": "原料"}, acc, CFG, short, "恒茂")
        a, b = r["docs"]
        self.assertEqual(a["v"], lc.V_ACCR)
        self.assertEqual(a["sys"], "孝感星期九 · 小料入库")
        self.assertEqual(b["v"], lc.V_ACCR)
        self.assertIn("默认算 小料", " ".join(b["how"]))
        # 不是外仓承运商：仓储费照常记 6601 货物仓储费
        bc = lc.bill_class("孝感星期九", "仓储费-小料")
        self.assertEqual(bc["a"]["acc"], "6601")

    def test_merge_cfg_keeps_defaults(self):
        c = lc.merge_cfg({"stock_rules": [], "bogus": 1})
        self.assertEqual(c["stock_rules"], [])
        self.assertIn("小料", c["group_biz"])
        self.assertNotIn("bogus", c)


if __name__ == "__main__":
    unittest.main()
