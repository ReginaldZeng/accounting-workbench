# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-02 | Author: Claude Opus 5.5 | Version: V2.749
# Description: 【物流·付款做账】纯函数（不连金蝶/库，便于单测）：一张物流请款单 → 合成一张凭证
#   红冲 → 更正 → 核销(暂估转待认证) → 支付（用户 2026-10-01 定顺序与写法）。
#   ① plan  计提凭证 vs 发票：按税率分组比含税；对得上的整张「核销」；税率开错的(计提 6% 发票 9%)整张「红冲+更正」到发票税率；
#           复核台登记过计提更正的也整张红冲+更正；同税率含税一致、税差 ≤0.05 的记尾差(照孝感 8月记-221 的 0.01 写法)；
#           含税合计都对不上的 → 需人工。
#   ② build 拼分录：红冲=原分录全额取负；更正=按新税率/更正值重做(税挂 2221.01.07 暂估)；核销=每张票一行 2221.01.06 待认证
#           (摘要以发票号开头，发票管家据此自动标「已做账」)+每张计提一行贷 2221.01.07；支付=借 2241.02 / 贷 1002。
#   摘要：红冲/更正「红冲8/565#计提…」(跨年写 2026-6/424#)；核销「{发票号}核销8/552#、9/□#计提{供应商}8月线下A、B」
#        (更正过的那笔引用本张凭证号，保存前用 □ 占位)；支付「{申请人}提起支付{供应商}8月线下A、B」。
# V2.798 主体更正(mode=move，用户 2026-10-05「这得出两张了，一张给星期零做账，一张给星期九做账」)：计提记到了别的主体账上
#   (凭证带 from={short, full}，exp_lines 已由调用方换成本主体的科目/费用项目/部门)——本张凭证里不红冲(原凭证不在本账簿)，
#   「更正」段直接在本主体补提，再核销、支付；原主体那边的红冲分录用 red_lines() 另出，给那边的人做账。
import re

STD_RATES = (0.0, 0.01, 0.03, 0.05, 0.06, 0.09, 0.13)
TAIL_MAX = 0.05            # 同税率含税一致时，发票税额与计提税额的尾差上限(超过算拆分不一致→人工)


def r2(x):
    return round(float(x or 0) + 1e-9, 2) if float(x or 0) >= 0 else -round(-float(x or 0) + 1e-9, 2)


def rate_of(v):
    """'6%' / '0.06' / 6 / 0.06 → 0.06；认不出 None。"""
    if v is None or v == "":
        return None
    s = str(v).strip().replace("％", "%")
    try:
        f = float(s.rstrip("%"))
    except ValueError:
        return None
    if s.endswith("%") or f > 1:
        f = f / 100.0
    return round(f, 4)


def snap_rate(x):
    """计提实际税率(税/不含税)→最近的标准税率。"""
    return min(STD_RATES, key=lambda r: abs(r - x)) if x is not None else None


def split_gross(gross, rate):
    net = r2(gross / (1 + rate))
    return net, r2(gross - net)


# ---------- 计提凭证 ----------
def acc_voucher(vno, lines, year, month):
    """一张计提凭证的分录 → {vno, year, month, expl, gross, tax, net, rate, exp_lines, tax_line, ap_line}。
    lines：[{acct, acct_name, dr, cr, expl, sup_code, sup_name, dept_code, dept, fee_code, fee, biz_code, biz, proj_code, proj}]"""
    exp = [l for l in lines if str(l.get("acct", ""))[:1] in ("5", "6") and l.get("dr")]
    taxl = [l for l in lines if str(l.get("acct", "")).startswith("2221.01.07") and l.get("dr")]
    apl = [l for l in lines if str(l.get("acct", "")).startswith("2241") and l.get("cr")]
    net = r2(sum(l["dr"] for l in exp))
    tax = r2(sum(l["dr"] for l in taxl))
    gross = r2(sum(l["cr"] for l in apl))
    expl = next((l.get("expl") for l in apl if l.get("expl")), None) or (lines[0].get("expl") if lines else "")
    return {"vno": str(vno), "year": int(year), "month": int(month), "expl": str(expl or "").strip(),
            "gross": gross, "tax": tax, "net": net, "rate": snap_rate(tax / net) if net else 0.0,
            "lines": lines, "exp_lines": exp, "tax_line": taxl[0] if taxl else None, "ap_line": apl[0] if apl else None}


def ref_of(v, pay_year):
    """引用原计提凭证：同年 8/565#，跨年 2026-6/424#。"""
    return ("%d/%s#" % (v["month"], v["vno"])) if v["year"] == pay_year else ("%d-%d/%s#" % (v["year"], v["month"], v["vno"]))


def desc_parts(expl, supplier):
    """「计提武汉易风达冷链物流有限公司8月线下孝感工厂入库运费」→ (前缀「计提{供应商}8月线下」, 费用项「孝感工厂入库运费」, 「8月线下」)。"""
    s = re.sub(r"^\s*(补计提|计提)", "", str(expl or "")).strip()
    if supplier and s.startswith(supplier):
        s = s[len(supplier):]
    mo = re.match(r"^((?:\d{4}年)?\d{1,2}月)(线上|线下)?(.*)$", s)
    if mo:
        mc = mo.group(1) + (mo.group(2) or "")
        return "计提%s%s" % (supplier, mc), mo.group(3).strip(), mc
    return "计提%s" % supplier, s, ""


def merged_desc(vouchers, supplier):
    """多张计提合成一句：计提{供应商}8月线下A、B、C（月份/线上下取第一张，费用项去重按凭证号排）。"""
    pre, items, mc = None, [], ""
    for v in sorted(vouchers, key=lambda x: (x["year"], x["month"], int(re.sub(r"\D", "", x["vno"]) or 0))):
        p, it, m = desc_parts(v["expl"], supplier)
        pre = pre or p
        mc = mc or m
        if it and it not in items:
            items.append(it)
    return (pre or ("计提%s" % supplier)), "、".join(items), mc


# ---------- ① 比对：哪张核销、哪张红冲更正 ----------
def plan(vouchers, invoices, fixes=None):
    """vouchers：acc_voucher(...) 列表；invoices：[{number, rate, gross, tax}]；fixes：{vno: [复核台计提更正]}。
    → {status: ok/manual, msgs:[], per:{vno: {mode: hx/rate/fix, new_rate, why}}, tails:{vno: d}}"""
    fixes = fixes or {}
    msgs, per = [], {}
    inv_g, inv_t = {}, {}
    for i in invoices:
        r = rate_of(i.get("rate"))
        if r is None:
            msgs.append("发票 %s 没识别出税率" % i.get("number"))
            return {"status": "manual", "msgs": msgs, "per": {}, "tails": {}}
        inv_g[r] = r2(inv_g.get(r, 0) + float(i.get("gross") or 0))
        inv_t[r] = r2(inv_t.get(r, 0) + float(i.get("tax") or 0))
    G_inv, G_acc = r2(sum(inv_g.values())), r2(sum(v["gross"] for v in vouchers))
    if abs(G_inv - G_acc) >= 0.005:
        msgs.append("发票含税合计 %.2f ≠ 计提含税合计 %.2f（差 %.2f），要人工处理" % (G_inv, G_acc, G_inv - G_acc))
        return {"status": "manual", "msgs": msgs, "per": {}, "tails": {}}
    cur = {}
    for v in vouchers:
        f = fixes.get(v["vno"]) if not v.get("from") else None
        if v.get("from"):
            per[v["vno"]] = {"mode": "move", "new_rate": v["rate"],
                             "why": "计提记在了「%s」的账上，补提到本主体" % v["from"].get("short", "")}
            cur[v["vno"]] = v["rate"]
        elif f:
            nr = next((rate_of(x.get("to_rate")) for x in f if rate_of(x.get("to_rate")) is not None), None)
            per[v["vno"]] = {"mode": "fix", "new_rate": nr if nr is not None else v["rate"], "why": "复核台登记了计提更正"}
            cur[v["vno"]] = per[v["vno"]]["new_rate"]
        else:
            per[v["vno"]] = {"mode": "hx", "new_rate": v["rate"], "why": ""}
            cur[v["vno"]] = v["rate"]

    def acc_g():
        g = {}
        for v in vouchers:
            g[cur[v["vno"]]] = r2(g.get(cur[v["vno"]], 0) + v["gross"])
        return g
    # 税率组含税对不上：找哪几张计提挪到发票的税率能补平(计提税率开错/发票开了别的税率)
    for _ in range(3):
        ag = acc_g()
        rates = set(ag) | set(inv_g)
        need = {r: r2(inv_g.get(r, 0) - ag.get(r, 0)) for r in rates}
        short = [r for r in rates if need[r] > 0.004]
        if not short:
            break
        moved = False
        for r in short:
            cand = [v for v in vouchers if per[v["vno"]]["mode"] in ("hx", "move") and need.get(cur[v["vno"]], 0) < -0.004]
            hit = _subset(cand, need[r])
            if hit:
                for v in hit:
                    why = "计提按 %s，发票开的是 %s" % (pct(v["rate"]), pct(r))
                    if per[v["vno"]]["mode"] == "move":      # 主体更正的同时税率也不对：补提时直接按发票税率
                        per[v["vno"]] = {"mode": "move", "new_rate": r, "why": per[v["vno"]]["why"] + "；" + why}
                    else:
                        per[v["vno"]] = {"mode": "rate", "new_rate": r, "why": why}
                    cur[v["vno"]] = r
                moved = True
                break
        if not moved:
            break
    ag = acc_g()
    bad = [r for r in set(ag) | set(inv_g) if abs(ag.get(r, 0) - inv_g.get(r, 0)) >= 0.005]
    if bad:
        msgs.append("按税率分组对不上：" + "；".join("%s 发票 %.2f / 计提 %.2f" % (pct(r), inv_g.get(r, 0), ag.get(r, 0)) for r in sorted(bad)))
        return {"status": "manual", "msgs": msgs, "per": per, "tails": {}}
    # 同税率含税一致，看税额：更正过的按新税率重算；差 ≤ TAIL_MAX 记尾差(挂该税率最大一张核销的计提)，超过→人工
    tails = {}
    for r in inv_g:
        vs = [v for v in vouchers if cur[v["vno"]] == r]
        at = r2(sum(v["tax"] if _keep_tax(v, per[v["vno"]]) else split_gross(v["gross"], r)[1] for v in vs))
        d = r2(inv_t[r] - at)
        if abs(d) < 0.005:
            continue
        if abs(d) > TAIL_MAX:
            msgs.append("%s 这组含税一致，但发票税额 %.2f 和计提 %.2f 差 %.2f，要人工看" % (pct(r), inv_t[r], at, d))
            return {"status": "manual", "msgs": msgs, "per": per, "tails": {}}
        hx = [v for v in vs if per[v["vno"]]["mode"] == "hx"]
        tgt = max(hx or vs, key=lambda v: v["gross"])
        tails[tgt["vno"]] = d
        if per[tgt["vno"]]["mode"] == "hx":       # 尾差也整笔红冲 + 更正：税额 +d、不含税 −d，含税不变
            per[tgt["vno"]] = {"mode": "tail", "new_rate": per[tgt["vno"]]["new_rate"], "why": "发票税额与计提差 %.2f" % d}
        msgs.append("%s 尾差 %.2f：记-%s 整笔红冲后更正（税额 %+.2f）" % (pct(r), d, tgt["vno"], d))
    return {"status": "ok", "msgs": msgs, "per": per, "tails": tails}


def pct(r):
    return ("%g%%" % round(r * 100, 2)) if r is not None else "?"


def _keep_tax(v, p):
    """这张计提的税额沿用原值(核销、尾差、主体更正且税率没变)，还是按新税率重算(改税率/复核台更正)。"""
    return p.get("mode") in ("hx", "tail") or (p.get("mode") == "move" and p.get("new_rate") == v["rate"])


def fix_expl(v, p, pay_year):
    """「更正」段的摘要：更正8/565#计提…；主体更正带上原主体——更正深圳星期零8/390#计提…(8/390# 是那边账簿的凭证)。"""
    src = v["from"].get("short", "") if (p.get("mode") == "move" and v.get("from")) else ""
    return "更正%s%s%s" % (src, ref_of(v, pay_year), v["expl"])


def red_lines(v, pay_year):
    """一张计提整笔红冲的分录(原分录全额取负)。本主体红冲更正用；主体更正时拿去给原主体的人做账。"""
    e = "红冲%s%s" % (ref_of(v, pay_year), v["expl"])
    out = []
    for l in v["lines"]:
        if l.get("dr"):
            keep = EXP_DIMS if str(l["acct"])[:1] in ("5", "6") else SUP_DIMS
            out.append(_ln("红冲", e, l["acct"], l["acct_name"], dr=-l["dr"], src=l, keep=keep))
        elif l.get("cr"):
            out.append(_ln("红冲", e, l["acct"], l["acct_name"], cr=-l["cr"], src=l, keep=SUP_DIMS))
    return out


def _subset(cands, target):
    """凭证子集含税合计 = target（≤12 张穷举，先小集合）。"""
    from itertools import combinations
    cands = cands[:12]
    for k in range(1, len(cands) + 1):
        for comb in combinations(cands, k):
            if abs(sum(v["gross"] for v in comb) - target) < 0.005:
                return list(comb)
    return None


# ---------- ② 拼分录 ----------
def _ln(block, expl, acct, acct_name, dr=0.0, cr=0.0, src=None, keep=()):
    dims = {k: (src or {}).get(k) for k in keep if (src or {}).get(k)}
    return {"block": block, "expl": expl, "acct": acct, "acct_name": acct_name, "dr": r2(dr), "cr": r2(cr), "dims": dims}


EXP_DIMS = ("dept_code", "dept", "fee_code", "fee", "biz_code", "biz", "proj_code", "proj")
SUP_DIMS = ("sup_code", "sup_name", "sup_grp")


def _split_code(v):
    """复核台「应改为」存「编码 名称」→ (编码, 名称)。"""
    s = str(v or "").strip()
    if not s:
        return "", ""
    p = s.split(" ", 1)
    return (p[0], p[1] if len(p) > 1 else "")


def _apply_fix(l, f):
    """把一条计提更正的「应改为」套到费用分录上(只改填了的)。"""
    l = dict(l)
    for fk, ck, nk in (("to_acct", "acct", "acct_name"), ("to_fee", "fee_code", "fee"), ("to_dept", "dept_code", "dept"),
                       ("to_biz", "biz_code", "biz"), ("to_proj", "proj_code", "proj")):
        if f.get(fk):
            c, n = _split_code(f[fk])
            l[ck], l[nk] = c, n or l.get(nk)
    return l


def build(ctx, vouchers, invoices, pl, fixes=None):
    """ctx：{supplier, applicant, pay_year, pay_month, pay_amount, bank, paid(bool)}。→ 分录列表(含 block)。"""
    fixes = fixes or {}
    sup = ctx["supplier"]
    py = ctx["pay_year"]
    out = []
    redo = [v for v in vouchers if pl["per"].get(v["vno"], {}).get("mode") in ("rate", "fix", "tail")]
    moved = [v for v in vouchers if pl["per"].get(v["vno"], {}).get("mode") == "move"]     # 主体更正：本账簿没有原凭证，不红冲，只补提
    renew = redo + moved
    # 本张凭证号：写金蝶时付款单自动凭证的号已知(ctx.self_vno)，直接填；预览时用 □ 占位
    self_ref = "%d/%s#" % (ctx["pay_month"], ctx.get("self_vno") or "□")
    # 红冲：原分录全额取负
    for v in redo:
        out.extend(red_lines(v, py))
    # 更正：按新税率/更正值重做，税挂暂估（主体更正＝在本主体补提）
    for v in renew:
        p = pl["per"][v["vno"]]
        e = fix_expl(v, p, py)
        fx = fixes.get(v["vno"]) or []
        gross = v["gross"]
        for f in fx:
            if f.get("to_amt_tax") not in (None, ""):
                try:
                    gross = r2(gross - float(f["snap"].get("amt") or 0) + float(str(f["to_amt_tax"]).replace(",", "")))
                except (TypeError, ValueError, KeyError):
                    pass
        d_tail = pl["tails"].get(v["vno"], 0)
        if _keep_tax(v, p):                      # 尾差 / 主体更正(税率没变)：含税不变，税额沿用原计提、按发票口径 +d
            tax = r2(v["tax"] + d_tail)
            net = r2(gross - tax)
        else:                                    # 改税率/更正：按新税率重算；尾差正好落在这张上的也带上(原来漏了，核销段会差这几分)
            net, tax = split_gross(gross, p["new_rate"])
            tax, net = r2(tax + d_tail), r2(net - d_tail)
        exps = [dict(l) for l in v["exp_lines"]]
        for f in fx:
            sn = f.get("snap") or {}
            for i, l in enumerate(exps):
                if l.get("acct") == sn.get("acct") and (l.get("fee_code") or "") == (sn.get("fee_code") or "") and \
                        (l.get("dept_code") or "") == (sn.get("dept_code") or "") and (l.get("biz_code") or "") == (sn.get("biz_code") or ""):
                    exps[i] = _apply_fix(l, f)
                    break
        base = sum(l["dr"] for l in v["exp_lines"]) or 1.0
        acc = 0.0
        for i, l in enumerate(exps):
            amt = r2(net - acc) if i == len(exps) - 1 else r2(net * v["exp_lines"][i]["dr"] / base)
            acc = r2(acc + amt)
            out.append(_ln("更正", e, l["acct"], l["acct_name"], dr=amt, src=l, keep=EXP_DIMS))
        tl = v["tax_line"] or v["ap_line"] or {}
        if tax:                                  # 更正到 0%(普票不抵扣)不出 0 金额的税行
            out.append(_ln("更正", e, "2221.01.07", "暂估进项税", dr=tax, src=tl, keep=("sup_code", "sup_name")))
        out.append(_ln("更正", e, "2241.02", "供应商往来", cr=gross, src=v["ap_line"], keep=SUP_DIMS))
        v["_new"] = {"gross": gross, "tax": tax}
    # 核销：每张票一行待认证 + 每张计提一行贷暂估(更正过的用新税额、引用本凭证号)
    refs = "、".join(self_ref if v in renew else ref_of(v, py) for v in sorted(vouchers, key=lambda x: (x["year"], x["month"], x["vno"])))
    pre, items, mc = merged_desc(vouchers, sup)
    hx_desc = "核销%s%s%s" % (refs, pre, items)
    for i in invoices:
        if i.get("deduct") is False:            # 普票等不能抵扣：不出待认证行(调用方已按 0 税率、0 税额参与核对)
            continue
        out.append(_ln("核销", "%s%s" % (i["number"], hx_desc), "2221.01.06", "待认证进项税额", dr=float(i.get("tax") or 0)))
    for v in sorted(vouchers, key=lambda x: (x["year"], x["month"], x["vno"])):
        t = v["_new"]["tax"] if v in renew else v["tax"]
        if t:
            out.append(_ln("核销", hx_desc, "2221.01.07", "暂估进项税", cr=t, src=v["tax_line"] or v["ap_line"], keep=("sup_code", "sup_name")))
    # 支付
    if ctx.get("paid"):
        pe = "%s提起支付%s%s%s" % (ctx.get("applicant") or "", sup, mc, items)
        plain = [i["number"] for i in invoices if i.get("deduct") is False and i.get("number")]
        if plain:                                # 普票号码写进支付摘要，发票管家按摘要里的号码认「已做账」
            pe += "（普票%s）" % "、".join(plain)
        ap = (vouchers[0]["ap_line"] if vouchers else None) or {}
        out.append(_ln("支付", pe, "2241.02", "供应商往来", dr=ctx["pay_amount"], src=ap, keep=SUP_DIMS))
        out.append({"block": "支付", "expl": pe, "acct": "1002", "acct_name": "银行存款", "dr": 0.0, "cr": r2(ctx["pay_amount"]),
                    "dims": {"bank": ctx.get("bank") or ""}})
    for v in vouchers:
        v.pop("_new", None)
    return out


def balance(lines):
    dr = r2(sum(l["dr"] for l in lines))
    cr = r2(sum(l["cr"] for l in lines))
    return dr, cr
