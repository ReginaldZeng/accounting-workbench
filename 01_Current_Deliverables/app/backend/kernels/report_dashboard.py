# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-08-09 | Author: Claude / c | Version: V2.248
# Description: 【报表仪表盘 / 子公司报表】内核。从金蝶 GL_BALANCE 直取全科目 → 去重 →
#   折成资产负债表 / 利润表 / KPI / 三道勾稽，按账簿(子公司)× 期间(2024-01 至今)一次算齐。
#
#   本文件的取数/去重/映射口径**原样搬自已验证的样机取数脚本 build_data.py**（2026-08 抢救）：
#   去重口径（见样机 probe3 实证）：
#     · 服务端 FDetailID=0 —— 丢掉核算维度明细行，只留科目合计行
#     · 本地 币别为空       —— GL_BALANCE 同一科目返回「综合行 + 各币别行」，金额重复
#     · 一级科目            —— 只取 4 位科目，避免父子重复
#   利润表口径：损益已按月结转、净额被抹平 → 收入类取累计贷方、成本费用类取累计借方（单边）。
#
#   为什么按期循环直取、不走 app 的 _kd_get 当期缓存：子公司报表要画跨期趋势（单月营收/净利、
#   同比），必须拿到 2024-01 至今每一期；app 的当期缓存只有一期。故本内核自建按期查询 + 结果缓存。
import threading

# BS：报表项目 → 一级科目码。基于活账套实有 51 个一级科目（样机实测）。
BS = [
    ("流动资产", [
        ("货币资金",       ["1001", "1002", "1012"]),
        ("交易性金融资产", ["1101"]),
        ("应收账款",       ["1122", "1231"]),
        ("预付款项",       ["1123"]),
        ("其他应收款",     ["1221", "1132"]),
        ("存货",           ["1401", "1402", "1403", "1405", "1408", "1411"]),
    ]),
    ("非流动资产", [
        ("长期股权投资",   ["1511"]),
        ("固定资产",       ["1601", "1602"]),
        ("固定资产清理",   ["1606"]),
        ("无形资产",       ["1701", "1702"]),
        ("长期待摊费用",   ["1801"]),
        ("待处理财产损溢", ["1901"]),
    ]),
    ("负债", [
        ("应付账款",       ["2202"]),
        ("预收款项",       ["2203"]),
        ("应付职工薪酬",   ["2211"]),
        ("应交税费",       ["2221"]),
        ("其他应付款",     ["2241"]),
        ("递延收益",       ["2401"]),
    ]),
    ("所有者权益", [
        ("实收资本",       ["4001"]),
        ("资本公积",       ["4002"]),
        ("未分配利润",     ["4104", "4103"]),
    ]),
]
# 利润表：(项目, [(科目, 取哪边)], 加减号)  side: 'C'=累计贷 'D'=累计借
PL = [
    ("营业收入",         [("6001", "C"), ("6051", "C")], +1),
    ("营业成本",         [("6401", "D"), ("6402", "D")], -1),
    ("税金及附加",       [("6403", "D")],                -1),
    ("销售费用",         [("6601", "D")],                -1),
    ("管理费用",         [("6602", "D")],                -1),
    ("研发费用",         [("6604", "D")],                -1),
    ("财务费用",         [("6603", "D")],                -1),
    ("其他收益",         [("6117", "C")],                +1),
    ("投资收益",         [("6111", "C")],                +1),
    ("公允价值变动收益", [("6101", "C")],                +1),
    ("资产处置收益",     [("6115", "C")],                +1),
    ("资产减值损失",     [("6701", "D")],                -1),
    ("营业外收入",       [("6301", "C")],                +1),
    ("营业外支出",       [("6711", "D")],                -1),
]
ASSET_ITEMS = {n for g, items in BS if "资产" in g for n, _ in items}
LIAB_ITEMS = {n for g, items in BS if g == "负债" for n, _ in items}
EQ_ITEMS = {n for g, items in BS if g == "所有者权益" for n, _ in items}

# GL_BALANCE 取数字段（含账簿码——账簿码才是稳定主键，账簿名会重名）
_FIELDS = [
    ("FACCOUNTBOOKID.FNumber", "账簿码"), ("FACCOUNTBOOKID.FName", "账簿"),
    ("FAccountID.FNumber", "科目编码"), ("FAccountID.FName", "科目名称"),
    ("FCurrencyID.FName", "币别"),
    ("FEndBalance", "期末"), ("FBeginBalance", "期初"),
    ("FDebit", "本期借"), ("FCredit", "本期贷"),
    ("FYtdDebit", "累计借"), ("FYtdCredit", "累计贷"),
]

# 结果缓存：单进程假设（同 app 里 _*_CACHE，uvicorn 单 worker）。键含 source+末期，换期自动失效。
_CACHE = {"key": None, "data": None}
_LOCK = threading.Lock()


def _f(v):
    try:
        return float(str(v or 0).replace(",", "") or 0)
    except Exception:
        return 0.0


def _periods(cur_year, cur_period):
    """2024-01 至当前期。当前期由 app 的期间设置传入，不写死（样机写死到 2026-07）。"""
    out = []
    for y in range(2024, cur_year + 1):
        last = cur_period if y == cur_year else 12
        for p in range(1, last + 1):
            out.append((y, p))
    return out


def _build(acc):
    """单账簿单期：科目余额字典 acc → {bs, pl, detail, pdetail, kpi, tie}。搬自 build_data.py.build()。"""
    g = lambda c, k: acc.get(c, {}).get(k, 0.0)

    bs, detail = [], {}
    for grp, items in BS:
        for name, codes in items:
            v = sum(g(c, "end") for c in codes)
            if grp in ("负债", "所有者权益"):
                v = -v                                    # 金蝶贷方余额记负 → 报表取正
            bs.append({"g": grp, "n": name, "v": round(v, 2)})
            detail[name] = [{"c": c, "n": acc.get(c, {}).get("name", ""),
                             "v": round(-g(c, "end") if grp in ("负债", "所有者权益") else g(c, "end"), 2)}
                            for c in codes if c in acc]

    pl, pdetail = [], {}
    for name, parts, sign in PL:
        v = sum(g(c, "yc" if side == "C" else "yd") for c, side in parts)
        pl.append({"n": name, "v": round(v, 2), "sign": sign})
        pdetail[name] = [{"c": c, "n": acc.get(c, {}).get("name", ""),
                          "v": round(g(c, "yc" if side == "C" else "yd"), 2)}
                         for c, side in parts if c in acc]
    m = {x["n"]: x["v"] for x in pl}
    revenue = m["营业收入"]
    other_gain = m["其他收益"] + m["投资收益"] + m["公允价值变动收益"] + m["资产处置收益"]
    op_profit = (m["营业收入"] - m["营业成本"] - m["税金及附加"] - m["销售费用"] - m["管理费用"]
                 - m["研发费用"] - m["财务费用"] + other_gain - m["资产减值损失"])
    net = op_profit + m["营业外收入"] - m["营业外支出"]
    gross = m["营业收入"] - m["营业成本"]

    bm = {x["n"]: x["v"] for x in bs}
    assets = sum(v for k, v in bm.items() if k in ASSET_ITEMS)
    liab = sum(v for k, v in bm.items() if k in LIAB_ITEMS)
    equity = sum(v for k, v in bm.items() if k in EQ_ITEMS)

    # 勾稽①：资产 = 负债 + 权益（权益已含 4103 本年利润）
    tie1 = round(assets - liab - equity, 2)
    # 勾稽②③：Σ收入类累计贷 = 4103 累计贷；Σ费用类累计借 = 4103 累计借
    rev_side = sum(g(c, "yc") for name, parts, sg in PL for c, side in parts if side == "C")
    exp_side = sum(g(c, "yd") for name, parts, sg in PL for c, side in parts if side == "D")
    tie2r = round(rev_side - g("4103", "yc"), 2)
    tie2e = round(exp_side - g("4103", "yd"), 2)
    posted = abs(sum(g(c, "d") + g(c, "c") for c in acc)) > 0.005

    return {"bs": bs, "pl": pl, "detail": detail, "pdetail": pdetail,
            "kpi": {"revenue": round(revenue, 2), "gross": round(gross, 2),
                    "op": round(op_profit, 2), "net": round(net, 2),
                    "assets": round(assets, 2), "liab": round(liab, 2), "equity": round(equity, 2),
                    "cash": round(bm.get("货币资金", 0), 2), "inv": round(bm.get("存货", 0), 2),
                    "ar": round(bm.get("应收账款", 0), 2),
                    "gm": round(gross / revenue * 100, 2) if revenue else None,
                    "dar": round(liab / assets * 100, 2) if assets else None},
            "tie": {"balance": tie1, "rev": tie2r, "exp": tie2e,
                    "p4103": round(g("4103", "yc") - g("4103", "yd"), 2), "posted": posted}}


def compute(kc, cur_year, cur_period, force=False):
    """按期循环查 GL_BALANCE → 返回 {books, periods, rpt}。结果缓存到末期，force 强刷。
    kc = kingdee_client 模块（由调用方传入，避免内核直接依赖 app 的导入路径）。"""
    key = ("kingdee", cur_year, cur_period)
    with _LOCK:
        if not force and _CACHE["key"] == key and _CACHE["data"] is not None:
            return _CACHE["data"]

    s, conf = kc.login()
    periods = _periods(cur_year, cur_period)
    books, data = {}, {}
    for (y, p) in periods:
        flt = f"FYear={y} and FPeriod={p} and FDetailID=0"
        rows = kc._query(s, conf, "GL_BALANCE", _FIELDS, flt, "FAccountID.FNumber")
        keep = [r for r in rows if not r["币别"] and "." not in str(r["科目编码"])]
        for r in keep:
            books[r["账簿码"]] = r["账簿"]
            acc = data.setdefault(f"{y}-{p:02d}", {}).setdefault(r["账簿码"], {})
            a = acc.setdefault(str(r["科目编码"]), {"name": r["科目名称"], "end": 0.0, "begin": 0.0,
                                                    "d": 0.0, "c": 0.0, "yd": 0.0, "yc": 0.0})
            a["end"] += _f(r["期末"]); a["begin"] += _f(r["期初"])
            a["d"] += _f(r["本期借"]); a["c"] += _f(r["本期贷"])
            a["yd"] += _f(r["累计借"]); a["yc"] += _f(r["累计贷"])

    per_strs = [f"{y}-{p:02d}" for (y, p) in periods]
    out = {"books": books, "periods": per_strs, "rpt": {}}
    for per in per_strs:
        out["rpt"][per] = {bk: _build(data.get(per, {}).get(bk, {})) for bk in books}

    with _LOCK:
        _CACHE["key"], _CACHE["data"] = key, out
    return out


def clear_cache():
    with _LOCK:
        _CACHE["key"], _CACHE["data"] = None, None
