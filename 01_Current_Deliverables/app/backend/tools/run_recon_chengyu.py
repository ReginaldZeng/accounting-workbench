# -*- coding: utf-8 -*-
"""诚煜物流账单 · 二期付款对账端到端跑分器（按回填单号直查金蝶，只读）。
用法：python tools/run_recon_chengyu.py [账单.xls]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kernels import logistics_recon as lr
import kingdee_client as kc

BILL = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\94899\Desktop\5月诚煜账单.xls"


def main():
    print("解析账单：", BILL)
    bill = lr.parse_chengyu_bill(BILL)
    if bill["warnings"]:
        print("  ⚠", bill["warnings"])
    nos = sorted({r["backfill_no"] for r in bill["rows"] if not r["pending"] and r["backfill_no"]})
    print("  明细 %d 行；待直查单号 %d 个；勾稽 合计=%s 明细求和=%s"
          % (len(bill["rows"]), len(nos), bill["summary"].get("合计"), bill["summary"].get("明细求和")))

    print("直查金蝶（7 类单据，只读）…")
    docs = kc.fetch_docs_by_nos(nos)
    from collections import Counter
    byform = Counter(d["form_name"] for d in docs)
    print("  金蝶取回分录 %d 行，按单据：%s" % (len(docs), dict(byform)))

    res = lr.reconcile_by_backfill(bill, docs)
    print("\n== 比对结果（对账组级）==")
    for k, v in res["stats"].items():
        print("   %s: %s" % (k, v))
    print("   勾稽：", res["tieout"])

    def show(state, label, n=12):
        rs = [r for r in res["rows"] if r["state"] == state]
        if not rs:
            return
        print("\n%s（%d）：" % (label, len(rs)))
        for r in rs[:n]:
            print("   行%s 单号=%s 目的地=%s 账单量=%s 金蝶量=%s [%s] 金额=%s 缺号=%s"
                  % (r.get("lines"), r["nos"], r.get("dest"), r.get("bill_qty"),
                     r.get("kd_qty"), r.get("kd_forms"), r.get("billed"), r.get("missing")))

    show("单号查无", "单号查无")
    show("部分查无", "部分查无")
    show(lr.ST_QTY, "数量不符")
    show("待核·金蝶无数量", "待核·金蝶无数量")
    show(lr.ST_OK, "核对一致抽样")


if __name__ == "__main__":
    main()
