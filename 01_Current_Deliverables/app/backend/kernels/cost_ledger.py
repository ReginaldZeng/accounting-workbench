# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-07-09 | Author: Claude / c | Version: V2.57
# Description: 成本台账内核（A 期第一增量，按需求确认书 v1.0 §6）。
#   存货收发存汇总表（跨维度 / 按日期）解析 → 三道勾稽（①两表互勾 ②收发存自平 ③账实勾稽）
#   → 类别 / 仓库 / 仓库类型透视 → 异常扫描（负结存 / 挂账尾差 / 对照缺失 / 成本调整提示）
#   → 损益归集（货损→管理费用、资产处置→营业外支出，业务↔总账互核）。
#   铁律：对照表未配置的类别标「对照缺失」不硬归；成本调整负数行如实列示不算异常。
#   金蝶侧只读：科目余额走 kingdee_client.fetch_gl_balance（账户级，按科目去重）；
#   物料级收发存明细为报表类、API 取不到 → 走手工上传（parse_cross_report）。
import json
import os

# ---- 异常状态（需求确认书 v1.0 §6.2）----
ST_NEG = "负结存"
ST_TAILDIFF = "挂账尾差"
ST_NOMAP = "对照缺失"
ST_COSTADJ = "成本调整提示"
ST_OK = "正常"
ANOMALY_STATES = [ST_NEG, ST_TAILDIFF, ST_NOMAP, ST_COSTADJ, ST_OK]

# 跨维度报表列名 → 内部键。按表头文字匹配（容忍列漂移），非写死列号。
_CROSS_COLS = {
    "物料编码": "code", "物料名称": "name", "存货类别": "cat", "物料分组": "grp",
    "规格型号": "spec", "库存状态": "status", "批号": "batch", "仓库": "wh",
    "单位": "unit", "计量单位": "unit", "基本单位": "unit",     # V2.139：底稿这列叫法不一，都认
    "期初数量": "oq", "期初金额": "oa", "收入数量": "iq", "收入金额": "ia",
    "发出数量": "dq", "发出金额": "da", "结存数量": "eq", "结存金额": "ea",
    # V2.138：四段单价。表里没这几列也不报错（按 idx 存在与否取），老底稿照样能传。
    "期初单价": "op", "收入单价": "ip", "发出单价": "dp", "结存单价": "ep",
}
_PRICE_KEYS = ("op", "ip", "dp", "ep")
# 按日期（物料级）报表：同名列，无仓库/批号维度
_BYDATE_KEYS = ("oq", "oa", "iq", "ia", "dq", "da", "eq", "ea")


def load_config(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _num(v, default=0.0):
    try:
        f = float(v)
        return default if f != f else f          # NaN → default
    except (TypeError, ValueError):
        return default


def _s(v):
    return "" if v is None else str(v).strip()


def _find_header(rows, must_have="物料编码"):
    """定位表头行索引（金蝶导出前两行是核算体系/会计期间元数据）。找不到返回 -1。"""
    for i, row in enumerate(rows):
        cells = [_s(c) for c in row]
        if must_have in cells:
            return i
    return -1


def parse_cross_report(rows):
    """解析存货收发存汇总表（跨维度）。rows=行元组列表（openpyxl values_only）。
    返回 [dict(code/name/cat/spec/wh/batch/oq/oa/iq/ia/dq/da/eq/ea)]，跳过元数据/表头/合计行。"""
    h = _find_header(rows)
    if h < 0:
        raise ValueError("未找到表头行（缺“物料编码”列）——请确认是收发存汇总表（跨维度）")
    header = [_s(c) for c in rows[h]]
    idx = {}
    for col_i, name in enumerate(header):
        if name in _CROSS_COLS:
            idx[_CROSS_COLS[name]] = col_i
    for req in ("code", "cat", "eq", "ea"):
        if req not in idx:
            raise ValueError(f"收发存报表缺必要列：{req}")
    out = []
    for row in rows[h + 1:]:
        code = _s(row[idx["code"]]) if idx["code"] < len(row) else ""
        name = _s(row[idx["name"]]) if "name" in idx and idx["name"] < len(row) else ""
        if not code and not name:
            continue
        if code == "合计" or name == "合计":
            continue
        rec = {"code": code, "name": name}
        for k in ("cat", "spec", "wh", "batch", "grp", "status", "unit"):
            rec[k] = _s(row[idx[k]]) if k in idx and idx[k] < len(row) else ""
        for k in ("oq", "oa", "iq", "ia", "dq", "da", "eq", "ea"):
            rec[k] = _num(row[idx[k]]) if k in idx and idx[k] < len(row) else 0.0
        # 单价空 → None（不是 0.0）：数量为 0 时本就没有单价，写 0 会被读成"单价真是零"。
        # 与 🅰 通道的 _rpt_price 同口径，两条通道出来的行结构一致。
        for k in _PRICE_KEYS:
            v = row[idx[k]] if k in idx and idx[k] < len(row) else None
            s = str(v).replace(",", "").strip() if v is not None else ""
            try:
                rec[k] = float(s) if s else None
            except ValueError:
                rec[k] = None
        out.append(rec)
    return out


def parse_bydate_report(rows):
    """解析存货收发存汇总表（按日期，物料级，无仓库/批号）。用于勾稽①两表互勾。"""
    return parse_cross_report(rows)      # 同结构解析，wh/batch 自然为空


# ---------------- 透视 ----------------
_CAT_KEYS = ("oq", "oa", "iq", "ia", "dq", "da", "eq", "ea")


def pivot_by_category(recs):
    """按存货类别汇总 期初/收入/发出/结存的【数量与金额】。返回 {类别: {oq,oa,iq,ia,dq,da,eq,ea}} + '合计'。

    V2.141 补数量四列（原来只有金额）——导出的「收发存汇总·按类别」要出数量。
    ⚠数量是跨单位相加的（本期 107 实测 22 种单位：千克/个/Pcs/米/张…），
    **合计只是件数总和、没有物理意义**；金额才是可加的。口径与业务方底稿一致，故照出，
    但页面/导出旁注已注明，别把它当重量看。"""
    agg = {}
    for r in recs:
        cat = r["cat"] or "（未分类）"
        a = agg.setdefault(cat, {k: 0.0 for k in _CAT_KEYS})
        for k in _CAT_KEYS:
            a[k] += r.get(k) or 0.0
    tot = {k: 0.0 for k in _CAT_KEYS}
    for a in agg.values():
        for k in tot:
            tot[k] += a[k]
    agg["合计"] = tot
    return agg


def subject_of_category(config):
    """存货类别 → 总账科目 的反查表。config['category_to_subject'] 是 {科目: [类别,...]}。
    查不到的类别不硬归（返回时给空）——同「对照缺失不硬归」的一贯口径。"""
    out = {}
    for subj, cats in (config.get("category_to_subject") or {}).items():
        for c in cats:
            out[c] = subj
    return out


def pivot_wh_category(recs, wh_attr=None):
    """仓库 × 存货类别 交叉透视（V2.118）。数量与金额都出。

    返回 {
      "cats":  [存货类别...],                     # 列顺序（按结存金额从大到小，'（未分类）'殿后）
      "rows":  [{wh, type, cells:{类别:{eq,ea}}, total:{eq,ea}}...],  # 按仓库类型分组、组内金额降序
      "types": [{type, whs:[仓库...], total:{eq,ea}}...],             # 仓库类型小计
      "cat_total": {类别:{eq,ea}},                # 列合计
      "total": {eq,ea},                          # 总计
    }
    仓库为空 → '（无仓库）'；类别为空 → '（未分类）'；仓库无属性 → '（属性缺失）'。
    """
    wh_attr = wh_attr or {}
    cell, wtot, ctot = {}, {}, {}
    tot = {"eq": 0.0, "ea": 0.0}
    for r in recs:
        wh = r["wh"] or "（无仓库）"
        cat = r["cat"] or "（未分类）"
        c = cell.setdefault(wh, {}).setdefault(cat, {"eq": 0.0, "ea": 0.0})
        w = wtot.setdefault(wh, {"eq": 0.0, "ea": 0.0})
        k = ctot.setdefault(cat, {"eq": 0.0, "ea": 0.0})
        for f in ("eq", "ea"):
            c[f] += r[f]; w[f] += r[f]; k[f] += r[f]; tot[f] += r[f]

    def rnd(d):
        return {"eq": round(d["eq"], 2), "ea": round(d["ea"], 2)}

    cats = sorted(ctot, key=lambda c: (c == "（未分类）", -abs(ctot[c]["ea"])))
    rows = []
    for wh, cs in cell.items():
        t = wh_attr.get(wh) or ("（无仓库）" if wh == "（无仓库）" else "（属性缺失）")
        rows.append({"wh": wh, "type": t,
                     "cells": {c: rnd(v) for c, v in cs.items()}, "total": rnd(wtot[wh])})
    # 按仓库类型分组、组内按结存金额降序；类型小计也按类别拆开（供组小计行逐列显示）
    tsum, tcell = {}, {}
    for r in recs:
        wh = r["wh"] or "（无仓库）"
        t = wh_attr.get(wh) or ("（无仓库）" if wh == "（无仓库）" else "（属性缺失）")
        a = tsum.setdefault(t, {"eq": 0.0, "ea": 0.0})
        c = tcell.setdefault(t, {}).setdefault(r["cat"] or "（未分类）", {"eq": 0.0, "ea": 0.0})
        for f in ("eq", "ea"):
            a[f] += r[f]; c[f] += r[f]
    torder = sorted(tsum, key=lambda t: -abs(tsum[t]["ea"]))
    rows.sort(key=lambda r: (torder.index(r["type"]), -abs(r["total"]["ea"])))
    types = [{"type": t, "whs": [r["wh"] for r in rows if r["type"] == t],
              "cells": {c: rnd(v) for c, v in tcell[t].items()}, "total": rnd(tsum[t])}
             for t in torder]
    return {"cats": cats, "rows": rows, "types": types,
            "cat_total": {c: rnd(v) for c, v in ctot.items()}, "total": rnd(tot)}


def pivot_by_warehouse(recs, wh_attr=None):
    """按仓库汇总【收发存全四段】：期初 oq/oa、收入 iq/ia、发出 dq/da、结存 eq/ea。
    wh_attr={仓库:类型} 时附仓库类型。

    V2.131 起补 oa/ia/da（原来只有结存 eq/ea）——导出的「收发存汇总·按仓库」要与
    「按类别」同格式（期初/收入/发出/结存），只有结存出不了那张表。
    原有键 eq/ea/type 保持不变，纯追加。"""
    wh_attr = wh_attr or {}
    agg = {}
    for r in recs:
        wh = r["wh"] or "（无仓库）"
        a = agg.setdefault(wh, {"oq": 0.0, "oa": 0.0, "iq": 0.0, "ia": 0.0,
                                "dq": 0.0, "da": 0.0, "eq": 0.0, "ea": 0.0,
                                "type": wh_attr.get(wh, "")})
        for f in ("oq", "oa", "iq", "ia", "dq", "da", "eq", "ea"):
            a[f] += r.get(f) or 0.0
    return agg


def share_of(part, total):
    """结存金额占比。总额为 0（或正负相抵为 0）时返回 None——
    除不动就老实说没有，别拿 0% 糊弄看的人。"""
    if not total:
        return None
    return part / total


def pivot_by_warehouse_type(recs, wh_attr):
    """按仓库类型汇总结存金额。未在维表中的仓库归入 '（属性缺失）'。"""
    agg = {}
    missing = set()
    for r in recs:
        wh = r["wh"] or "（无仓库）"
        t = wh_attr.get(wh)
        if t is None and wh != "（无仓库）":
            missing.add(wh)
            t = "（属性缺失）"
        agg[t or "（属性缺失）"] = agg.get(t or "（属性缺失）", 0.0) + r["ea"]
    return agg, sorted(missing)


# ---------------- 三道勾稽 ----------------
def tie_two_reports(cross, bydate, tol=0.01):
    """勾稽①两表互勾：跨维度 vs 按日期 四项金额合计。返回 {项:{cross,bydate,diff,pass}}。"""
    def sums(recs):
        s = {"oa": 0.0, "ia": 0.0, "da": 0.0, "ea": 0.0}
        for r in recs:
            for k in s:
                s[k] += r[k]
        return s
    a, b = sums(cross), sums(bydate)
    out = {}
    ok = True
    for k, label in [("oa", "期初金额"), ("ia", "收入金额"), ("da", "发出金额"), ("ea", "结存金额")]:
        diff = round(a[k] - b[k], 2)
        p = abs(diff) <= tol
        ok = ok and p
        out[label] = {"cross": round(a[k], 2), "bydate": round(b[k], 2), "diff": diff, "pass": p}
    out["pass"] = ok
    return out


def tie_receipt_balance(cross, tol=0.01):
    """勾稽②收发存自平：期初+收入-发出=结存（总额与逐类别）。返回 {类别:{diff,pass}, '合计':..., pass}。"""
    cat = pivot_by_category(cross)
    out = {}
    ok = True
    for name, a in cat.items():
        diff = round(a["oa"] + a["ia"] - a["da"] - a["ea"], 2)
        p = abs(diff) <= tol
        if name != "合计":
            ok = ok and p
        out[name] = {"oa": round(a["oa"], 2), "ia": round(a["ia"], 2),
                     "da": round(a["da"], 2), "ea": round(a["ea"], 2), "diff": diff, "pass": p}
    out["pass"] = ok
    return out


def tie_book_vs_actual(cross, gl_balance, config, tol=0.01):
    """勾稽③账实勾稽：收发存结存按类别 ↔ 总账科目余额。
    gl_balance={科目名:余额}；config['category_to_subject']={科目:[类别,...]}。
    返回 {科目:{book,actual,diff,pass}} + 单列科目 + 合计 + 对照缺失清单 + pass。"""
    cat = pivot_by_category(cross)
    cat_end = {k: v["ea"] for k, v in cat.items() if k != "合计"}
    mapping = config["category_to_subject"]
    mapped_cats = set()
    out = {"subjects": {}, "extra": {}, "unmapped": [], "pass": True}
    book_total = 0.0
    actual_total = 0.0
    for subj, cats in mapping.items():
        actual = sum(cat_end.get(c, 0.0) for c in cats)
        for c in cats:
            mapped_cats.add(c)
        book = _num(gl_balance.get(subj))
        diff = round(book - actual, 2)
        p = abs(diff) <= tol
        out["subjects"][subj] = {"book": round(book, 2), "actual": round(actual, 2),
                                 "cats": cats, "diff": diff, "pass": p}
        out["pass"] = out["pass"] and p
        book_total += book
        actual_total += actual
    # 单列科目（在途物资/委托加工物资等，收发存报表外）
    for subj in config.get("extra_subjects", []):
        bal = _num(gl_balance.get(subj))
        out["extra"][subj] = round(bal, 2)
        book_total += bal
    # 对照缺失：出现在收发存但未配到任何科目的类别
    for c in cat_end:
        if c not in mapped_cats:
            out["unmapped"].append({"cat": c, "ea": round(cat_end[c], 2)})
    out["book_total"] = round(book_total, 2)
    out["actual_total"] = round(actual_total, 2)      # = 收发存结存合计
    return out


# ---------------- 异常扫描（需求确认书 v1.0 §6.2）----------------
def scan_anomalies(cross, config, qty_tol=1e-6, amt_tol=1e-6):
    """扫描四类异常。返回 {'items':[...], 'counts':{态:数}, 'total_rows':n}。
    负结存：结存数量<0；挂账尾差：结存数量≈0 但结存金额≠0；对照缺失：类别不在对照表；
    成本调整提示：收入/发出金额为负（金蝶成本调整单痕迹，仅提示不算异常）。"""
    mapped = set()
    for cats in config["category_to_subject"].values():
        mapped.update(cats)
    items = []
    counts = {s: 0 for s in ANOMALY_STATES}
    for r in cross:
        st = ST_OK
        note = ""
        if r["eq"] < -qty_tol:
            st = ST_NEG
            note = "结存数量为负——出库先于入库或单据漏做，关账前清理"
        elif abs(r["eq"]) <= qty_tol and abs(r["ea"]) > amt_tol:
            st = ST_TAILDIFF
            note = "数量为0但金额不为0——疑调拨单价尾差未冲，建议调整单冲平"
        elif r["cat"] and r["cat"] not in mapped:
            st = ST_NOMAP
            note = f"存货类别「{r['cat']}」未配置科目对照——请补对照关系，不硬归"
        elif r["ia"] < -amt_tol or r["da"] < -amt_tol:
            st = ST_COSTADJ
            note = "收入/发出金额为负——金蝶成本调整单正常痕迹，如实列示不算异常"
        counts[st] += 1
        if st != ST_OK:
            items.append({"code": r["code"], "name": r["name"], "cat": r["cat"],
                          "wh": r["wh"], "batch": r["batch"], "eq": round(r["eq"], 4),
                          "ea": round(r["ea"], 4), "status": st, "note": note})
    # 挂账尾差配对提示：同金额正负镜像
    _pair_tail_diffs(items)
    prio = {ST_NEG: 0, ST_TAILDIFF: 1, ST_NOMAP: 2, ST_COSTADJ: 3}
    items.sort(key=lambda x: (prio.get(x["status"], 9), -abs(x["ea"])))
    return {"items": items, "counts": counts, "total_rows": len(cross)}


def _pair_tail_diffs(items):
    """给挂账尾差找正负镜像对手仓库，附到 note。"""
    tails = [it for it in items if it["status"] == ST_TAILDIFF]
    for a in tails:
        for b in tails:
            if a is b:
                continue
            if abs(a["ea"] + b["ea"]) <= 1e-6 and abs(a["ea"]) > 1e-6:
                a["mirror"] = {"wh": b["wh"], "ea": b["ea"]}
                break


# ---------------- 损益归集（需求确认书 v1.0 §6.3）----------------
def collect_pnl(loss_rows, disposal_rows, other_rows=None, excluded_rows=None, tol=0.01):
    """损益归集。loss_rows=[{cat,amount}] 货损→管理费用；disposal_rows=[{amount}] 处置→营业外支出；
    other_rows=[{cat,acct,acct_name,amount}] 第三档：既非货损也非处置的存货出损益（福利领用/捐赠…）。

    ⚠**第三档必须回到 result["pnl"] 里**（V2.312 修）：V2.307 把它从货损拆出来时只落进了
    `_raw.pnl_detail`，导出页读得到、**前端读不到**——`pnl` 里只有 loss/disposal 两档。
    于是 101 深圳星期零在第⑦步页面上是"货损 0、处置 0"，一片空白，
    业务方原话「福利领用还是不出来」。**同一份数据两条路各走各的，迟早对不上。**

    excluded_rows＝**口径外**（Owner 定案：6602 管理费用里非货损的不算成本台账口径）。
    不进任何合计，只报条数与金额——钱凭空少一块而没人说得清去哪了，比多列一行糟得多。

    返回 {loss:{by_cat,total}, disposal:{total}, other:{by_item,by_acct,total},
          excluded:{by_item,total,n}}。"""
    loss_by_cat = {}
    loss_total = 0.0
    for r in loss_rows:
        c = r.get("cat", "") or "（未分类）"
        amt = _num(r.get("amount"))
        loss_by_cat[c] = loss_by_cat.get(c, 0.0) + amt
        loss_total += amt
    disp_total = sum(_num(r.get("amount")) for r in disposal_rows)
    # 第三档按【费用项目】和【科目】各汇一份：业务方看这块时问的是"哪些进了营业外支出"，
    # 只按费用项目分会答不了——捐赠在 6711、产品领用福利在 6602，两者性质与去向都不同。
    oth_by_item, oth_by_acct, oth_total = {}, {}, 0.0
    for r in (other_rows or []):
        c = r.get("cat", "") or "（未分类）"
        a = ("%s %s" % (r.get("acct") or "", r.get("acct_name") or "")).strip() or "（未注明科目）"
        amt = _num(r.get("amount"))
        oth_by_item[c] = oth_by_item.get(c, 0.0) + amt
        oth_by_acct[a] = oth_by_acct.get(a, 0.0) + amt
        oth_total += amt
    exc_by_item, exc_total = {}, 0.0
    for r in (excluded_rows or []):
        c = r.get("cat", "") or "（未分类）"
        amt = _num(r.get("amount"))
        exc_by_item[c] = exc_by_item.get(c, 0.0) + amt
        exc_total += amt
    return {
        "loss": {"by_cat": {k: round(v, 2) for k, v in loss_by_cat.items()}, "total": round(loss_total, 2)},
        "disposal": {"total": round(disp_total, 2)},
        "other": {"by_item": {k: round(v, 2) for k, v in oth_by_item.items()},
                  "by_acct": {k: round(v, 2) for k, v in oth_by_acct.items()},
                  "total": round(oth_total, 2)},
        "excluded": {"by_item": {k: round(v, 2) for k, v in exc_by_item.items()},
                     "total": round(exc_total, 2), "n": len(excluded_rows or [])},
    }


# ---------------- 顶层编排 ----------------
def pivot_cost_center(cc_recs):
    """成本计算单 → 「车间 × 成本项目」交叉表（V2.257）。

    ⚠只吃 level=='item' 的行：树形报表里成本项目行的金额＝其下费用项目之和，
    两层一起求和会翻倍。费用项目另走 `pivot_cost_expense`。
    ⚠委外（工单前缀 SUB）**不并进车间**——它不走生产成本科目，硬并会让车间合计对不上总额。
    返回 {items(列), rows(每车间), outsourced(委外单列), item_total(列合计), total}。"""
    items, rows, out_row = [], {}, {}
    for r in cc_recs:
        if r.get("level") != "item" or not r.get("item"):
            continue
        it, amt = r["item"], _num(r.get("amt"))
        if it not in items:
            items.append(it)
        if r.get("outsourced"):
            out_row[it] = out_row.get(it, 0.0) + amt
        else:
            cc = r.get("cc") or "（无车间）"
            rows.setdefault(cc, {})[it] = rows.setdefault(cc, {}).get(it, 0.0) + amt
    items.sort(key=lambda i: -(sum(v.get(i, 0.0) for v in rows.values()) + out_row.get(i, 0.0)))
    body = [{"cc": cc, "cells": {k: round(v, 2) for k, v in cells.items()},
             "total": round(sum(cells.values()), 2)}
            for cc, cells in sorted(rows.items(), key=lambda kv: -sum(kv[1].values()))]
    item_total = {i: round(sum(v.get(i, 0.0) for v in rows.values()) + out_row.get(i, 0.0), 2)
                  for i in items}
    return {"items": items, "rows": body,
            "outsourced": {"cells": {k: round(v, 2) for k, v in out_row.items()},
                           "total": round(sum(out_row.values()), 2)},
            "item_total": item_total, "total": round(sum(item_total.values()), 2)}


def pivot_cost_expense(cc_recs, top=None):
    """成本计算单 → 费用项目构成（V2.257）。只吃 level=='exp' 的行，理由同上。"""
    agg = {}
    for r in cc_recs:
        if r.get("level") != "exp" or not r.get("exp"):
            continue
        agg[r["exp"]] = agg.get(r["exp"], 0.0) + _num(r.get("amt"))
    out = [{"exp": k, "amount": round(v, 2)} for k, v in sorted(agg.items(), key=lambda x: -x[1])]
    return out[:top] if top else out


def tie_cost(cc_recs, cost_gl, tol=0.5):
    """三道成本勾稽（V2.257）。等式全部由 107/2026-3 真数据实证得出，非推测：

      ①制造费用归集  总账 5101 借方 ＝ 成本计算单（制造费用 + 间接材料）
                     2,371,486.79 ＝ 2,275,015.57 + 96,471.23
      ②完工结转      总账 5001 贷方 ＝ 流水表「汇报入库」        13,688,275.73 ＝ 13,688,275.73
      ③投入归集      总账 5001 借方 ＝ 本期投入(剔委外) + 期末在产品成本调整
                     13,664,100.77 ＝ 13,671,990.45 + (-7,889.68)

    容差 0.5 元：树形报表各层独立四舍五入，①实测差 0.21，卡到 0.01 会误报。
    `cost_gl` 缺项则该道跳过（回 None），不伪装成通过。"""
    by_item = {}
    inp_ex_sub = 0.0
    for r in cc_recs:
        if r.get("level") != "item" or not r.get("item"):
            continue
        by_item[r["item"]] = by_item.get(r["item"], 0.0) + _num(r.get("amt"))
        if not r.get("outsourced"):
            inp_ex_sub += _num(r.get("amt"))
    g = cost_gl or {}
    out = {"by_item": {k: round(v, 2) for k, v in by_item.items()}}

    def one(label, book, biz, note):
        d = round(_num(book) - _num(biz), 2)
        return {"label": label, "book": round(_num(book), 2), "biz": round(_num(biz), 2),
                "diff": d, "pass": abs(d) <= tol, "note": note}

    mfg_biz = by_item.get("制造费用", 0.0) + by_item.get("间接材料", 0.0)
    out["mfg_collect"] = one("制造费用归集", g.get("mfg_debit"), mfg_biz,
                             "总账制造费用发生额 ＝ 成本计算单「制造费用＋间接材料」") \
        if g.get("mfg_debit") is not None else None
    out["wip_input"] = one("投入归集", g.get("wip_debit"),
                           inp_ex_sub + _num(g.get("wip_adjust")),
                           "总账生产成本借方 ＝ 本期投入（剔委外）＋ 期末在产品成本调整") \
        if g.get("wip_debit") is not None else None
    out["input_ex_sub"] = round(inp_ex_sub, 2)
    out["outsourced"] = round(sum(_num(r.get("amt")) for r in cc_recs
                                  if r.get("level") == "item" and r.get("outsourced")), 2)
    return out


# 贷记生产成本的事务类型。**这个清单是"发现一个补一个"的，不是一次想全的**——
# 每次都是某个月冒出一种新类型、等式当月才不平：
#   ·「生产退库」：2026-3 没有这类单据、等式碰巧成立；2026-5 有 6 笔 -875.89，
#     只算汇报入库就差这个数（8,859,531.01 vs 8,860,406.90）。
#   ·「生产入库」：3–6 月一笔没有，2026-7 冒出 32,917.85，业务方一眼看出"是不是漏了个表"。
#     🧪 补上后 3/4/5/6/7 五个月全部归零（3–6 月新旧口径完全一致，只有 7 月由 32,917.85 → 0）。
# ⚠**所以不平时先看事务类型清单里有没有没收录的新类型**，别急着怀疑总账或流水本身。
WIP_CREDIT_BTYPES = ("汇报入库", "生产退库", "生产入库")


def tie_cost_complete(cost_gl, btypes, tol=0.5):
    """勾稽②完工结转：总账 5001 贷方 ＝ 流水表【汇报入库 + 生产退库】收入金额。

    单列一个函数是因为业务侧数据来自**事务类型汇总**、不在成本计算单里。
    实证：2026-3 13,688,275.73 ＝ 13,688,275.73（无生产退库）；
          2026-5  8,859,531.01 ＝ 8,860,406.90 + (-875.89)。
    总账侧实测两个月的 5001 贷方 **100% 由汇报入库构成**（41 笔全是），故只需对业务侧补齐类型。"""
    if not cost_gl or cost_gl.get("wip_credit") is None or btypes is None:
        return None
    parts = {}
    for a in btypes:
        bt = a.get("bt") if isinstance(a, dict) else None
        if bt in WIP_CREDIT_BTYPES:
            parts[bt] = round(_num(a.get("ia")), 2)
    biz = sum(parts.values())
    d = round(_num(cost_gl.get("wip_credit")) - biz, 2)
    return {"label": "完工结转", "book": round(_num(cost_gl.get("wip_credit")), 2),
            "biz": round(biz, 2), "parts": parts, "diff": d, "pass": abs(d) <= tol,
            "note": "总账生产成本贷方 ＝ 流水表「汇报入库＋生产退库」"}


def build_cat_map(recs):
    """本期「物料编码 → (存货类别, 物料分组)」映射（V2.282）。封存/落库时一并存下，供下期比对。

    为什么要存它：金蝶的收发存报表按**当前**物料档案归集类别，改档案会**追溯改变历史月份的报表**；
    而总账凭证记的是当时的科目、不会追溯变。两者一个"活"一个"死"，只要有人改档案就会错开。
    2026-3 实测：18 个物料被重分类（纸箱/吸水垫→包材、品牌宣传品→广宣品、纤蔬脆→产成品），
    存货总额一分没动，但账实勾稽因此差 ±1.92，而这事**三个月后才被人拿旧底稿对出来**。
    存下映射，下期就能当月发现。"""
    out = {}
    for r in recs:
        c = (r.get("code") or "").strip()
        if c:
            out[c] = [r.get("cat") or "", r.get("grp") or ""]
    return out


def scan_category_drift(recs, prev_map, prev_label=""):
    """类别漂移：本期物料的存货类别/物料分组与上期不同者（V2.282）。

    只对**两期都出现过**的物料比——新物料没有"变过"可言，本期没有的也无从比。
    按本期结存金额降序，金额大的排前面（同样一次改名，动 10 万和动 1 分不是一回事）。
    ⚠**只报不判**：漂移本身多半是档案归正（纸箱本来就该是包材），不是错误；
    工具的职责是让它当月就被看见，而不是替业务方判定对错。"""
    if not prev_map:
        return {"items": [], "prev": prev_label, "n": 0, "amount": 0.0}
    cur, amt = {}, {}
    for r in recs:
        c = (r.get("code") or "").strip()
        if not c:
            continue
        cur[c] = [r.get("cat") or "", r.get("grp") or "", r.get("name") or ""]
        amt[c] = amt.get(c, 0.0) + _num(r.get("ea"))
    items = []
    for c, (cat, grp, name) in cur.items():
        old = prev_map.get(c)
        if not old:
            continue
        ocat, ogrp = (old + ["", ""])[:2]
        if cat == ocat and grp == ogrp:
            continue
        items.append({"code": c, "name": name,
                      "old_cat": ocat, "new_cat": cat, "cat_changed": cat != ocat,
                      "old_grp": ogrp, "new_grp": grp, "grp_changed": grp != ogrp,
                      "ea": round(amt.get(c, 0.0), 2)})
    items.sort(key=lambda x: -abs(x["ea"]))
    return {"items": items, "prev": prev_label, "n": len(items),
            "amount": round(sum(x["ea"] for x in items if x["cat_changed"]), 2)}


def build_cost_block(cc_recs, cost_gl, btypes):
    """成本计算单 → 可直接落库的【结论块】（V2.257）。

    **取数时算一次、落库存结论，不落那 16,379 行原始明细**——同 V2.141 事务类型的做法：
    原始行只用来算这三样，留着既撑大 payload 又诱使别处误用（那张表的层级语义很容易用错）。
    返回 {ties, pivot_cc, expenses, n}。"""
    if not cc_recs:
        return None
    ct = tie_cost(cc_recs, cost_gl)
    ct["complete"] = tie_cost_complete(cost_gl, btypes)
    done = [x for x in (ct.get("mfg_collect"), ct.get("complete"), ct.get("wip_input")) if x]
    ct["pass"] = all(x["pass"] for x in done) if done else None
    return {"ties": ct, "pivot_cc": pivot_cost_center(cc_recs),
            "expenses": pivot_cost_expense(cc_recs), "n": len(cc_recs)}


def build_cost_ledger(cross, config, gl_balance=None, bydate=None,
                      loss_rows=None, disposal_rows=None, other_rows=None, excluded_rows=None,
                      cost_block=None, prev_cat_map=None, prev_label=""):
    """一次算全：三道勾稽 + 透视 + 异常 + 损益 + 可信度结论。gl_balance/bydate 可缺（对应勾稽跳过）。"""
    wh_attr = config.get("warehouse_attr", {})
    result = {
        "pivot_category": {k: {kk: round(vv, 2) for kk, vv in v.items()}
                           for k, v in pivot_by_category(cross).items()},
        "pivot_warehouse": pivot_by_warehouse(cross, wh_attr),
        "pivot_wh_category": pivot_wh_category(cross, wh_attr),
        "anomalies": scan_anomalies(cross, config),
    }
    wt, wt_missing = pivot_by_warehouse_type(cross, wh_attr)
    result["pivot_wh_type"] = {"by_type": {k: round(v, 2) for k, v in wt.items()}, "missing_attr": wt_missing}
    ties = {"self_balance": tie_receipt_balance(cross)}
    if bydate is not None:
        ties["two_reports"] = tie_two_reports(cross, bydate)
    if gl_balance is not None:
        ties["book_vs_actual"] = tie_book_vs_actual(cross, gl_balance, config)
    result["ties"] = ties
    if loss_rows is not None or disposal_rows is not None or other_rows is not None:
        result["pnl"] = collect_pnl(loss_rows or [], disposal_rows or [], other_rows or [],
                                    excluded_rows or [])
    # 制造费用（V2.257）：三道成本勾稽 + 车间/费用项目透视。取不到就整块不出，
    # **不并进存货那三道的可信度**——两边是不同的账，一边不平不该把另一边也判为不可信。
    if cost_block:
        result["cost"] = cost_block
    # 类别漂移（V2.282）：与上期档案比，只报不判——不并进 credible，改档案不是"不可信"
    result["drift"] = scan_category_drift(cross, prev_cat_map, prev_label)
    # 可信度结论：所有已执行的勾稽全过才可信
    all_pass = all(t.get("pass", True) for t in ties.values())
    result["credible"] = all_pass
    return result
