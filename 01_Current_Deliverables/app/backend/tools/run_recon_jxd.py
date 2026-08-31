# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-07-08 | Author: Claude / c | Version: V2.44
# Description: 物流对账端到端跑分器（极鲜达）。用法：
#   python tools/run_recon_jxd.py <账单.xlsx> [YYYY-MM] [--cache 出库单缓存.json] [--out 输出目录]
#   连真金蝶（只读）拉账期±1月四种出库单 → 内核对账 → 打印 DoD 跑分 →
#   导出《差异清单》《单号回填版账单》。--cache 存在则直接用缓存不打金蝶。
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kernels import logistics_recon as lr

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opts = {a.split("=")[0][2:]: (a.split("=", 1)[1] if "=" in a else True)
            for a in sys.argv[1:] if a.startswith("--")}
    bill_path = args[0]
    ym = args[1] if len(args) > 1 else "2026-06"
    y, m = int(ym[:4]), int(ym[5:7])
    d_from = (datetime.date(y, m, 1) - datetime.timedelta(days=4)).isoformat()
    d_to = (datetime.date(y + (m == 12), m % 12 + 1, 1) + datetime.timedelta(days=3)).isoformat()
    out_dir = opts.get("out", os.path.dirname(os.path.abspath(bill_path)))

    cfg = lr.load_config(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                      "sample_data", "logistics_recon_极鲜达.json"))
    bill = lr.parse_jxd_bill(bill_path)
    print(f"账单解析: {len(bill['rows'])} 行 | 汇总页: {bill['summary']}")

    cache = opts.get("cache")
    if cache and os.path.exists(cache):
        docs = json.load(open(cache, encoding="utf-8"))
        print(f"出库单: 用缓存 {cache}，{len(docs)} 分录")
    else:
        import kingdee_client as kc
        docs = kc.fetch_outbound_docs(d_from, d_to)
        print(f"出库单: 真金蝶取数 {d_from}~{d_to}，{len(docs)} 分录")
        if cache:
            json.dump(docs, open(cache, "w", encoding="utf-8"), ensure_ascii=False)

    res = lr.reconcile(bill, docs, cfg)
    print("\n=== 三道勾稽 ===")
    print(json.dumps(res["guards"], ensure_ascii=False, indent=1, default=str))
    print("\n=== 统计 ===")
    print(json.dumps(res["stats"], ensure_ascii=False, indent=1, default=str))

    # DoD-1 山姆回填一致率（对齐物流部门人工回填的 XSCKD 列）
    sam = [r for r in res["rows"] if r["sheet"] == "山姆"]
    ok = sum(1 for r in sam if r["match_no"] and r["match_no"] == r.get("backfill_no"))
    print(f"\nDoD-1 山姆回填: {ok}/{len(sam)} 与人工一致 "
          f"(引擎已匹配 {sum(1 for r in sam if r['match_no'])})")
    for r in sam:
        if r["match_no"] and r["match_no"] != r.get("backfill_no"):
            print(f"  不一致 行{r['line']}: 引擎={r['match_no']} 人工={r.get('backfill_no')}")

    # DoD-2 零担快运自动匹配率（剔除天然人工项：入向/补送/无日期）
    lk = [r for r in res["rows"] if r["sheet"] == "零担快运"]
    eligible = [r for r in lk if not r.get("inbound") and r.get("ship_date")]
    matched = [r for r in eligible if r["match_no"]]
    print(f"DoD-2 零担快运自动匹配: {len(matched)}/{len(eligible)} "
          f"= {len(matched)/len(eligible)*100:.0f}%（全 sheet {len(lk)} 行，含天然人工 {len(lk)-len(eligible)} 行）")
    for r in eligible:
        if not r["match_no"]:
            print(f"  未匹配 行{r['line']} {r.get('cust')} {r.get('dest')} {r.get('weight')} "
                  f"[{r['method']}] 候选:{r['cands'][:2]}")

    # DoD-3 已知疑似差异捕获（手工原型 712 元）
    print("\nDoD-3 差异捕获（多收·单价 / 多收·附加费）:")
    for r in res["rows"]:
        if r["state"] in (lr.ST_PRICE, lr.ST_SURCH):
            print(f"  行{r['line']}({r['sheet']}) {r.get('cust') or r.get('dc') or r.get('item')} "
                  f"{r['state']} 差额 {r['diff']:+.2f} | {r['reason']}")

    p1 = lr.export_diff_excel(res, os.path.join(out_dir, f"极鲜达_差异清单_{ym}.xlsx"), period=ym)
    p2 = lr.export_backfill_excel(res, os.path.join(out_dir, f"极鲜达_单号回填版账单_{ym}.xlsx"), period=ym)
    print(f"\n导出: {p1}\n      {p2}")

if __name__ == "__main__":
    main()
