# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-09-26 | Author: Claude Opus 4.8 | Version: V2.632
# Description: 【物流账单复核】核价核量引擎。复核=两轴：核价(收费标准，单价 vs 合同价格卡) + 核量(数量 vs 金蝶)，金额是导出。
#   核量口径按承运商计费方式定：快递比件数、干线整车比重量/吨、仓储比板天——别一律比 kg（迅鸽商品金蝶多按袋/个计量，比 kg 必错）。
#   给中间表 detail 行配 价格卡 + 金蝶数量 → 标准费/核价差/核价态 + 核量差/核量态 → 归一态。accrual 行按费用项汇总供计提。
#   实证：迅鸽 2026-08 逐单，核价 5706/5711 一致·0 多收；核量按件 5116/5458 一致、342 不符、260 金蝶查无（2026-09-26 验）。
from kernels import logistics_price as lp

# 归一态：pass 两轴过 / price 多收 / gap 价卡缺 / free 账单未收(我方有利) / qty 数量存疑
_TOL = 0.01


def price_check(row, card):
    """核价：账单金额 vs 按合同价格卡重算的标准费。返回 (标准费, 核价差, 核价态, 计价档)。不倒算。"""
    amount = row.get("amount")
    fee_item = row.get("fee_item")
    if fee_item == "快递费":
        # 核价只比基价（快递费本身）vs 合同档价；旺季/燃油/地区加收是价卡不含的附加费，另算不当多收。
        base = row.get("base_amount")
        if base is None:
            base = amount
        std, why = lp.std_freight(card, row.get("carrier_sub"), row.get("prov"), row.get("charge_wt"))
        if std is None:
            return None, None, "gap", why
        if (base or 0) == 0:
            return std, round(0 - std, 2), "free", why
        diff = round((base or 0) - std, 2)
        return std, diff, ("over" if diff > _TOL else "under" if diff < -_TOL else "ok"), why
    # 简单单价费用项（操作费/退货/仓储/装卸/包材）：标准=单价×数量
    up = lp.unit_price(card, fee_item) if fee_item else None
    qty = row.get("qty")
    if up is not None and qty is not None:
        std = round(up * qty, 2)
        diff = round((amount or 0) - std, 2)
        return std, diff, ("over" if diff > _TOL else "under" if diff < -_TOL else "ok"), "%s×%s" % (up, qty)
    return None, None, "na", "无价卡/待人工"


def qty_check(row, kd_qty):
    """核量：账单数量 vs 金蝶出库数量（口径由取数说明按承运商定，此处按行已带的 qty/单位）。
    返回 (金蝶数量, 核量差, 核量态)。kd_qty=该单号金蝶数量 或 None(查无)。"""
    qty = row.get("qty")
    if kd_qty is None:
        return None, None, "miss"
    if qty is None:
        return kd_qty, None, "na"
    diff = round(qty - kd_qty, 3)
    return kd_qty, diff, ("ok" if abs(diff) <= 0.001 else "qtydiff")


def verdict(price_state, qty_state):
    if price_state == "over":
        return "price"
    if price_state == "gap":
        return "gap"
    if price_state == "free":
        return "free"
    if qty_state in ("miss", "qtydiff"):
        return "qty"
    return "pass"


def review_details(rows, card, kd_qty_map):
    """给一批 detail 行做两轴复核，就地填 std_amount/price_diff/price_state/kd_qty/qty_diff/qty_state/verdict。返回同一 list。"""
    for r in rows:
        std, pdiff, pstate, tier = price_check(r, card)
        r["std_amount"], r["price_diff"], r["price_state"], r["tier"] = std, pdiff, pstate, tier
        kd = kd_qty_map.get((r.get("doc_no") or "").split("+")[0]) if kd_qty_map else None
        kq, qdiff, qstate = qty_check(r, kd)
        r["kd_qty"], r["qty_diff"], r["qty_state"] = kq, qdiff, qstate
        r["verdict"] = verdict(pstate, qstate)
    return rows


def summarize(rows):
    """费用项汇总（核价×核量）+ 归一态计数。rows=复核后的 detail 行。"""
    fees = {}
    for r in rows:
        fi = r.get("fee_item") or "其它"
        f = fees.setdefault(fi, {"fee_item": fi, "bill": 0.0, "std": 0.0, "std_known": False,
                                 "n": 0, "price_ok": 0, "over": 0, "gap": 0, "free": 0,
                                 "qty_ok": 0, "qtydiff": 0, "miss": 0})
        f["n"] += 1
        f["bill"] += r.get("amount") or 0
        if r.get("std_amount") is not None:
            f["std"] += r["std_amount"]
            f["std_known"] = True
        for k, st in (("price_ok", "ok"), ("over", "over"), ("gap", "gap"), ("free", "free")):
            if r.get("price_state") == st:
                f[k] += 1
        for k, st in (("qty_ok", "ok"), ("qtydiff", "qtydiff"), ("miss", "miss")):
            if r.get("qty_state") == st:
                f[k] += 1
    out = []
    for f in fees.values():
        f["bill"] = round(f["bill"], 2)
        f["std"] = round(f["std"], 2) if f["std_known"] else None
        f["diff"] = round(f["bill"] - f["std"], 2) if f["std"] is not None else None
        out.append(f)
    return out


def verdict_counts(rows):
    """归一态分组计数（给待处理队列）。"""
    c = {"pass": 0, "price": 0, "gap": 0, "free": 0, "qty": 0, "miss": 0, "qtydiff": 0}
    for r in rows:
        v = r.get("verdict")
        if v in c:
            c[v] += 1
        if r.get("qty_state") == "miss":
            c["miss"] += 1
        elif r.get("qty_state") == "qtydiff":
            c["qtydiff"] += 1
    return c
