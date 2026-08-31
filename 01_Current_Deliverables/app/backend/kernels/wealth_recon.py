# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-07-04 | Author: Claude / c | Version: V2.6
# Description: 理财产品对账内核（产品维度聚合，不逐笔）。
#   理财在金蝶跨科目、拆多腿记账：本金(1101.01 或 1012理财腿) / 公允价值变动(1101.02) /
#   投资收益(6101) / 申购(1101.01 借)。一笔赎回=金蝶N条腿，逐笔对不齐，故按【理财产品维度】
#   两侧聚合勾稽：对账单赎回/申购额 ↔ 金蝶本金退回(+收益)。差额多为收益/公允价值时间差，
#   标出交核算组核（不硬凑对平）。纯月末公允价值变动确认单列，不算异常。
#   对账单侧数据来自 wealth_statement.py（OCR 解析真理财PDF）。
import re

MONEY_SUBJ = ("1101", "1012")
# 电商/第三方渠道维度（记在 1012 但不是理财，排除）
_NON_LICAI = ("支付宝", "微信", "天猫", "淘宝", "抖音", "小红书", "聚合结算",
              "zhifubo", "wuzw", "starfield", "@", "旗舰店", "货款")
# 理财类关键词（1012 腿判定是否理财）
_LICAI_KW = ("理财", "赎回", "核销", "购买", "申购", "基金", "信托", "净值", "持有")
_REDEEM_KW = ("赎回", "转出", "分红", "派息", "核销")
_SUBSCRIBE_KW = ("申购", "认购", "转投", "购买")


def _f(v):
    if v is None or v == "":
        return 0.0
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return 0.0


def _is_licai_dim(dim):
    d = str(dim or "")
    if not d:
        return False
    return not any(x in d.lower() for x in [n.lower() for n in _NON_LICAI])


def norm_key(s):
    """产品名/维度名 归一化成匹配键：去空格/标点/括号内容尾巴，保留 品牌+号数 主干。"""
    c = re.sub(r"[\s（）()·\-—,，。.、]", "", str(s or ""))
    c = re.sub(r"[A-Za-z]*\d{5,}[A-Za-z]?", "", c)   # 去掉产品代码/长数字串（ZGN2560081/PY200001）
    return c


def prod_code(s):
    """抽产品代码（ZGN2560081E / PY200001 之类 字母+数字）。"""
    m = re.search(r"[A-Z]{2,4}\d{5,}[A-Z]?", str(s or ""))
    return m.group(0) if m else ""


def _brand_no(s):
    """抽 品牌关键词 + 号数（浦银理财…93号 / 宁银理财…81号），做宽松匹配。"""
    c = re.sub(r"\s", "", str(s or ""))
    brand = ""
    for b in ("浦银理财", "宁银理财", "招银理财", "华鑫信托", "信益嘉", "中银理财",
              "工银理财", "农银理财", "建信理财", "交银理财", "兴银理财", "光大理财",
              "信银理财", "平安理财", "民生理财", "基金"):
        if b in c:
            brand = b
            break
    m = re.search(r"(\d{1,4})号", c)
    no = m.group(1) if m else ""
    return brand, no


def match_dim(stmt_name, stmt_code, kd_dims, stmt_acct=""):
    """把对账单产品匹配到金蝶维度。优先产品代码前缀，其次账号数字，再品牌+号数，再归一化子串。"""
    scode = prod_code(stmt_code) or prod_code(stmt_name)
    if scode:
        base = re.match(r"[A-Z]+\d+", scode)
        base = base.group(0) if base else scode
        for d in kd_dims:
            if base and base in re.sub(r"\s", "", d):
                return d
    acct = re.sub(r"\D", "", str(stmt_acct or ""))     # 账号数字（信托/基金对账单靠账号号匹配金蝶维度）
    if len(acct) >= 6:
        for d in kd_dims:
            if acct in re.sub(r"\s", "", d):
                return d
    sb, sn = _brand_no(stmt_name)
    if sb and sn:
        for d in kd_dims:
            db, dn = _brand_no(d)
            if db == sb and dn == sn:
                return d
    if sb:                                   # 只有品牌（号数缺）时退品牌匹配
        for d in kd_dims:
            if sb in re.sub(r"\s", "", d):
                return d
    sk = norm_key(stmt_name)
    if len(sk) >= 4:
        for d in kd_dims:
            dk = norm_key(d)
            if sk and (sk in dk or dk in sk):
                return d
    return ""


def kd_wealth_by_product(vou_subjects, vou_income):
    """金蝶按理财产品维度聚合。返回 {维度: {本金退回, 本金申购, 公允价值净, 现金到账, 投资收益, legs:[…]}}。
    vou_subjects: 1002/1012/1101.* 序时账；vou_income: 6101 投资收益序时账。"""
    agg = {}

    def slot(dim):
        return agg.setdefault(dim, {"本金退回": 0.0, "本金申购": 0.0, "公允价值净": 0.0,
                                    "现金到账": 0.0, "投资收益": 0.0, "纯估值确认": 0.0, "legs": []})

    for r in vou_subjects:
        code = str(r.get("科目编码") or "")
        dim = str(r.get("FDetailID.FF100002.FNumber") or "")
        if not dim or not _is_licai_dim(dim):
            continue
        deb, cre = _f(r.get("FDEBIT")), _f(r.get("FCREDIT"))
        exp = str(r.get("FEXPLANATION") or "")
        vno = f"{r.get('FVOUCHERGROUPID.FName', '')}{r.get('FVOUCHERGROUPNO', '')}"
        is_val = "公允价值变动" in exp        # 纯月末估值确认
        if code.startswith("1101.01") or code == "1101":
            s = slot(dim)
            s["本金退回"] += cre              # 贷=退本金(赎回)
            s["本金申购"] += deb              # 借=增持(申购)
            s["legs"].append({"科目": code, "借": deb, "贷": cre, "凭证": vno, "摘要": exp})
        elif code.startswith("1101.02"):
            s = slot(dim)
            s["公允价值净"] += (deb - cre)
            if is_val:
                s["纯估值确认"] += (deb - cre)
            s["legs"].append({"科目": code, "借": deb, "贷": cre, "凭证": vno, "摘要": exp})
        elif code.startswith("1012"):
            if not any(k in exp for k in _LICAI_KW):
                continue                     # 1012 非理财腿（电商渠道）跳过
            s = slot(dim)
            s["本金退回"] += cre              # 1012 理财腿贷=赎回核销出账
            s["本金申购"] += deb
            s["现金到账"] += cre
            s["legs"].append({"科目": code, "借": deb, "贷": cre, "凭证": vno, "摘要": exp})

    # 收益/损益腿（6101/6111/6603.02利息收入）：只归到【已建立的理财产品维度】，按摘要产品名匹配。
    # 不能凭原始维度新建产品——6603 财务费用带的是各银行账号维度(普通利息/手续费)，非理财，须排除。
    established = set(agg.keys())             # 来自 1101/1012 的真理财产品维度
    for r in vou_income:
        dim = str(r.get("FDetailID.FF100002.FNumber") or "")
        exp = str(r.get("FEXPLANATION") or "")
        cre, deb = _f(r.get("FCREDIT")), _f(r.get("FDEBIT"))
        code = str(r.get("科目编码") or "")
        # 靠摘要里的理财产品名归集；仅当摘要含理财线索时才认（否则是普通银行利息/费用）
        key = dim if dim in established else ""
        if not key and any(k in exp for k in ("理财", "赎回", "基金", "信托", "宁欣", "天添鑫")):
            key = _income_dim_by_text(exp, established)
        if key:
            slot(key)["投资收益"] += (cre - deb)
            slot(key)["legs"].append({"科目": code or "6xxx", "借": deb, "贷": cre,
                                      "凭证": f"{r.get('FVOUCHERGROUPID.FName', '')}{r.get('FVOUCHERGROUPNO', '')}",
                                      "摘要": exp})
    for d in agg.values():
        for k in ("本金退回", "本金申购", "公允价值净", "现金到账", "投资收益", "纯估值确认"):
            d[k] = round(d[k], 2)
    return agg


def _income_dim_by_text(exp, dims):
    """6101 摘要里的产品名 → 归集到已有金蝶理财维度。"""
    eb, en = _brand_no(exp)
    for d in dims:
        db, dn = _brand_no(d)
        if eb and db == eb and (not en or en == dn):
            return d
    ek = norm_key(exp)
    for d in dims:
        dk = norm_key(d)
        if dk and len(dk) >= 4 and dk in ek:
            return d
    return ""


def reconcile_wealth(stmt_records, kd_agg, tol=1.0):
    """对账单(OCR记录列表) × 金蝶理财聚合 → 产品级对账行。
    stmt_records: wealth_statement.parse 出的记录列表（每个含 交易明细）。"""
    # 汇总对账单侧：按 (匹配到的金蝶维度 或 产品名) 归集 赎回/申购
    kd_dims = list(kd_agg.keys())
    used = set()
    rows = []
    # 先按对账单产品聚合本期赎回/申购
    by_prod = {}
    for rec in stmt_records:
        name = rec.get("产品名称", "")
        code = rec.get("产品代码", "")
        dim = match_dim(name, code, kd_dims, rec.get("账号", ""))
        pkey = dim or ("对账单:" + name)
        p = by_prod.setdefault(pkey, {"主体": rec.get("主体", ""), "机构": rec.get("出单机构", ""),
                                      "产品名称": name, "产品代码": code, "账号": rec.get("账号", ""),
                                      "金蝶维度": dim, "赎回": 0.0, "申购": 0.0, "笔数": 0,
                                      "期初市值": rec.get("期初市值"), "期末市值": rec.get("期末市值"),
                                      "txns": []})
        for t in rec.get("交易明细", []):
            amt = t.get("确认金额") or 0
            typ = t.get("类型", "")
            if any(k in typ for k in _REDEEM_KW):
                p["赎回"] += amt
            elif any(k in typ for k in _SUBSCRIBE_KW):
                p["申购"] += amt
            p["笔数"] += 1
            p["txns"].append({"日期": t.get("日期"), "类型": typ, "确认金额": amt})
        if dim:
            used.add(dim)

    for pkey, p in by_prod.items():
        dim = p["金蝶维度"]
        kd = kd_agg.get(dim) if dim else None
        p["赎回"] = round(p["赎回"], 2)
        p["申购"] = round(p["申购"], 2)
        row = {**p}
        no_txn = (abs(p["赎回"]) < 0.01 and abs(p["申购"]) < 0.01)   # 持仓对账单，本期无申赎
        if kd:
            kd_redeem = kd["本金退回"]
            kd_income = kd["投资收益"]
            # 勾稽：对账单赎回额 ≈ 金蝶本金退回 + 投资收益（收益/公允价值补足）
            diff_principal = round(p["赎回"] - kd_redeem, 2)
            diff_with_income = round(p["赎回"] - kd_redeem - kd_income, 2)
            row.update({
                "金蝶本金退回": kd_redeem, "金蝶本金申购": kd["本金申购"],
                "金蝶投资收益": kd_income, "金蝶公允价值净": kd["公允价值净"],
                "金蝶纯估值确认": kd["纯估值确认"],
                "差额_对本金": diff_principal, "差额_含收益": diff_with_income,
                "legs": kd["legs"],
            })
            if no_txn:
                row["状态"] = "持仓·无交易"           # 对账单是持仓单、本期无申赎；金蝶多为月末公允价值估值
                row["差额_对本金"] = None; row["差额_含收益"] = None
            elif abs(diff_with_income) <= tol or abs(diff_principal) <= tol:
                row["状态"] = "已勾稽"
            else:
                row["状态"] = "有差异"
        else:
            row.update({"金蝶本金退回": None, "金蝶投资收益": None, "金蝶公允价值净": None,
                        "差额_对本金": None, "差额_含收益": None, "legs": []})
            row["状态"] = "持仓·无交易" if no_txn else "金蝶未记(对账单有赎回、金蝶无对应理财腿)"
        rows.append(row)

    # 金蝶有理财腿但对账单没覆盖的产品
    for dim, kd in kd_agg.items():
        if dim in used:
            continue
        if abs(kd["本金退回"]) < 0.01 and abs(kd["本金申购"]) < 0.01 and abs(kd["公允价值净"]) < 0.01:
            continue
        rows.append({
            "主体": "", "机构": "", "产品名称": dim, "产品代码": prod_code(dim), "账号": "",
            "金蝶维度": dim, "赎回": None, "申购": None, "笔数": 0, "txns": [],
            "期初市值": None, "期末市值": None,
            "金蝶本金退回": kd["本金退回"], "金蝶本金申购": kd["本金申购"],
            "金蝶投资收益": kd["投资收益"], "金蝶公允价值净": kd["公允价值净"],
            "金蝶纯估值确认": kd["纯估值确认"], "差额_对本金": None, "差额_含收益": None,
            "legs": kd["legs"],
            "状态": "对账单缺(金蝶有理财腿、无对账单覆盖)",
        })

    order = {"有差异": 0, "金蝶未记(对账单有赎回、金蝶无对应理财腿)": 1,
             "对账单缺(金蝶有理财腿、无对账单覆盖)": 2, "持仓·无交易": 3, "已勾稽": 4}
    rows.sort(key=lambda r: order.get(r["状态"], 9))
    return {
        "rows": rows,
        "对账单赎回合计": round(sum((r["赎回"] or 0) for r in rows), 2),
        "对账单申购合计": round(sum((r["申购"] or 0) for r in rows), 2),
        "金蝶本金退回合计": round(sum((r["金蝶本金退回"] or 0) for r in rows), 2),
        "金蝶投资收益合计": round(sum((r["金蝶投资收益"] or 0) for r in rows), 2),
        "已勾稽": sum(1 for r in rows if r["状态"] == "已勾稽"),
        "有差异": sum(1 for r in rows if r["状态"] == "有差异"),
        "产品数": len(rows),
    }
