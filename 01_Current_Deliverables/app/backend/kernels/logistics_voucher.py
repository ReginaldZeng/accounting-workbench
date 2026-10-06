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
# V2.832 发票后补(用户 2026-10-05「这个是发票后补，所以可能收到发票之后，还要做一个账」「按照第一种做」)：
#   ① 付款时还没票(ctx.tax_later)：付款凭证只出支付两行，不出核销，暂估进项税先挂着；
#   ② 发票到了：later_lines() 单独出一张「暂估转待认证」——借 2221.01.06(摘要＝发票号+核销M/N#+原摘要) / 贷 2221.01.07(挂供应商)，
#      写法照金蝶里已有的(孝感 9月记-97 易嘉达、深圳星期零 7月记-257 顺新晖)。税额尾差调到费用行(照孝感 6月记-251)。
#   直接做账的费用凭证(direct，摘要「××提起支付…」)：核销、支付的摘要都用「核销M/N#＋原摘要」(照禾享 5月记-360)。
# V2.818 金额有差(用户 2026-10-05「金额有差的，按照红冲处理，或者特殊的按照部分核销处理（凭证说明）」)：
#   amts={凭证号: {gross: 应为含税, part: 是否部分核销, memo: 说明}}——mode=amt 整笔红冲、按应为金额重新计提(更正)再核销；
#   mode=part 不红冲，只核销应为金额那一部分，剩下的留在账上，核销摘要写明计提多少、本次核销多少、余多少和说明。
#   复核台登记的金额更正(to_amt_tax)原来 plan 不认(仍拿原计提合计去比，永远判人工)，现在一并按更正后的金额比。
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
            "lines": lines, "exp_lines": exp, "tax_line": taxl[0] if taxl else None, "ap_line": apl[0] if apl else None,
            # 做这张的时候就有票、税直接挂了待认证的(禾享 5月记-155)：没有暂估要转，付款时只做支付
            "tax06": r2(sum(l["dr"] for l in lines if str(l.get("acct", "")).startswith("2221.01.06") and l.get("dr")))}


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
def eff_gross(v, fx, amt=None):
    """这张计提「应为」的含税金额：人工/系统给了应为金额的用它；复核台登记了金额更正的按更正算；否则就是原计提。"""
    if amt and amt.get("gross") is not None:
        return r2(amt["gross"])
    g = v["gross"]
    for f in fx or []:
        if f.get("to_amt_tax") not in (None, ""):
            try:
                g = r2(g - float((f.get("snap") or {}).get("amt") or 0) + float(str(f["to_amt_tax"]).replace(",", "")))
            except (TypeError, ValueError):
                pass
    return r2(g)


def plan(vouchers, invoices, fixes=None, amts=None):
    """vouchers：acc_voucher(...) 列表；invoices：[{number, rate, gross, tax}]；fixes：{vno: [复核台计提更正]}；
    amts：{vno: {gross, part, memo}} 应为金额(金额有差时)。
    → {status: ok/manual, msgs:[], per:{vno: {mode: hx/rate/fix/tail/move/amt/part, new_rate, why, gross}}, tails:{vno: d}}"""
    fixes, amts = fixes or {}, amts or {}
    msgs, per = [], {}
    eff = {v["vno"]: eff_gross(v, None if v.get("from") else fixes.get(v["vno"]), amts.get(v["vno"])) for v in vouchers}
    inv_g, inv_t = {}, {}
    for i in invoices:
        r = rate_of(i.get("rate"))
        if r is None:
            msgs.append("发票 %s 没识别出税率" % i.get("number"))
            return {"status": "manual", "msgs": msgs, "per": {}, "tails": {}}
        inv_g[r] = r2(inv_g.get(r, 0) + float(i.get("gross") or 0))
        inv_t[r] = r2(inv_t.get(r, 0) + float(i.get("tax") or 0))
    G_inv, G_acc = r2(sum(inv_g.values())), r2(sum(eff.values()))
    for v in vouchers:                       # 部分核销只能比原计提少
        a = amts.get(v["vno"]) or {}
        if a.get("part") and eff[v["vno"]] - v["gross"] > 0.004:
            msgs.append("记-%s 要部分核销 %.2f，比计提 %.2f 还多：部分核销只能核销计提的一部分，多出来的请改用红冲更正，要人工处理" % (v["vno"], eff[v["vno"]], v["gross"]))
            return {"status": "manual", "msgs": msgs, "per": {}, "tails": {}}
    if abs(G_inv - G_acc) >= 0.005:
        msgs.append("发票含税合计 %.2f ≠ 计提含税合计 %.2f（差 %.2f），要人工处理" % (G_inv, G_acc, G_inv - G_acc))
        return {"status": "manual", "msgs": msgs, "per": {}, "tails": {}}
    cur = {}
    for v in vouchers:
        f = fixes.get(v["vno"]) if not v.get("from") else None
        a = amts.get(v["vno"]) or {}
        chg = abs(eff[v["vno"]] - v["gross"]) >= 0.005 and a.get("gross") is not None      # 给了应为金额、且和原计提不一样
        if v.get("from"):
            per[v["vno"]] = {"mode": "move", "new_rate": v["rate"],
                             "why": "计提记在了「%s」的账上，补提到本主体" % v["from"].get("short", "")}
            cur[v["vno"]] = v["rate"]
        elif chg and a.get("part"):
            per[v["vno"]] = {"mode": "part", "new_rate": v["rate"], "memo": str(a.get("memo") or "").strip(),
                             "why": "部分核销：计提 %.2f，本次核销 %.2f，余 %.2f 留在账上" % (v["gross"], eff[v["vno"]], v["gross"] - eff[v["vno"]])}
            cur[v["vno"]] = v["rate"]
        elif chg:
            per[v["vno"]] = {"mode": "amt", "new_rate": v["rate"], "memo": str(a.get("memo") or "").strip(),
                             "why": "金额有差：计提 %.2f，应为 %.2f（%+.2f）" % (v["gross"], eff[v["vno"]], eff[v["vno"]] - v["gross"])}
            cur[v["vno"]] = v["rate"]
        elif f:
            nr = next((rate_of(x.get("to_rate")) for x in f if rate_of(x.get("to_rate")) is not None), None)
            per[v["vno"]] = {"mode": "fix", "new_rate": nr if nr is not None else v["rate"], "why": "复核台登记了计提更正"}
            cur[v["vno"]] = per[v["vno"]]["new_rate"]
        else:
            per[v["vno"]] = {"mode": "hx", "new_rate": v["rate"], "why": ""}
            cur[v["vno"]] = v["rate"]
        per[v["vno"]]["gross"] = eff[v["vno"]]

    def acc_g():
        g = {}
        for v in vouchers:
            g[cur[v["vno"]]] = r2(g.get(cur[v["vno"]], 0) + eff[v["vno"]])
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
            cand = [v for v in vouchers if per[v["vno"]]["mode"] in ("hx", "move", "amt") and need.get(cur[v["vno"]], 0) < -0.004]
            hit = _subset(cand, need[r])
            if hit:
                for v in hit:
                    why = "计提按 %s，发票开的是 %s" % (pct(v["rate"]), pct(r))
                    if per[v["vno"]]["mode"] == "amt":       # 金额、税率都不对：一次红冲，按应为金额和发票税率更正
                        per[v["vno"]] = dict(per[v["vno"]], new_rate=r, why=per[v["vno"]]["why"] + "；" + why)
                    elif per[v["vno"]]["mode"] == "move":    # 主体更正的同时税率也不对：补提时直接按发票税率
                        per[v["vno"]] = {"mode": "move", "new_rate": r, "why": per[v["vno"]]["why"] + "；" + why, "gross": eff[v["vno"]]}
                    else:
                        per[v["vno"]] = {"mode": "rate", "new_rate": r, "why": why, "gross": eff[v["vno"]]}
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
        at = r2(sum(v["tax"] if _keep_tax(v, per[v["vno"]]) else split_gross(eff[v["vno"]], r)[1] for v in vs))
        d = r2(inv_t[r] - at)
        if abs(d) < 0.005:
            continue
        if abs(d) > TAIL_MAX:
            msgs.append("%s 这组含税一致，但发票税额 %.2f 和计提 %.2f 差 %.2f，要人工看" % (pct(r), inv_t[r], at, d))
            return {"status": "manual", "msgs": msgs, "per": per, "tails": {}}
        hx = [v for v in vs if per[v["vno"]]["mode"] == "hx"]
        redone = [v for v in vs if per[v["vno"]]["mode"] in ("amt", "rate", "fix", "move")]
        # 尾差挂在哪张：这组里已经要红冲更正的优先(反正要重做，带上这几分)，免得为一两分钱再多红冲一张好好的计提
        tgt = max(redone or hx or vs, key=lambda v: v["gross"])
        tails[tgt["vno"]] = d
        if per[tgt["vno"]]["mode"] == "hx":       # 尾差也整笔红冲 + 更正：税额 +d、不含税 −d，含税不变
            per[tgt["vno"]] = {"mode": "tail", "new_rate": per[tgt["vno"]]["new_rate"], "why": "发票税额与计提差 %.2f" % d, "gross": eff[tgt["vno"]]}
        msgs.append("%s 尾差 %.2f：记-%s 整笔红冲后更正（税额 %+.2f）" % (pct(r), d, tgt["vno"], d))
    return {"status": "ok", "msgs": msgs, "per": per, "tails": tails}


def pct(r):
    return ("%g%%" % round(r * 100, 2)) if r is not None else "?"


def _keep_tax(v, p):
    """这张计提的税额沿用原值(核销、尾差、主体更正且税率没变)，还是按新税率重算(改税率/复核台更正)。"""
    if abs(float(p.get("gross", v["gross"])) - v["gross"]) >= 0.005:      # 含税金额变了(金额更正/部分核销/复核台改金额)：税额必须重算
        return False
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


def split_part(f, v, line_net, base_net):
    """计提更正填了「只改其中一部分(含税)」→ 这一部分占这条费用行的不含税额；没填 / 填得不对(≤0、不小于这一行) → 0(整笔改)。"""
    try:
        x = float(str(f.get("split_amt") or "").replace(",", ""))
    except ValueError:
        return 0.0
    line_gross = float(v["gross"]) * line_net / (base_net or 1.0)          # 这一行对应的含税额(一张计提几行费用时按不含税额占比)
    if x <= 0.004 or x >= line_gross - 0.004 or line_gross <= 0:
        return 0.0
    return line_net * x / line_gross


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
    redo = [v for v in vouchers if pl["per"].get(v["vno"], {}).get("mode") in ("rate", "fix", "tail", "amt")]
    moved = [v for v in vouchers if pl["per"].get(v["vno"], {}).get("mode") == "move"]     # 主体更正：本账簿没有原凭证，不红冲，只补提
    parts = [v for v in vouchers if pl["per"].get(v["vno"], {}).get("mode") == "part"]     # 部分核销：不红冲不更正，只核销一部分
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
        gross = p["gross"] if p.get("gross") is not None else eff_gross(v, fx)      # 应为金额(人工/系统给的，或复核台登记的金额更正)
        d_tail = pl["tails"].get(v["vno"], 0)
        if _keep_tax(v, p):                      # 尾差 / 主体更正(税率没变)：含税不变，税额沿用原计提、按发票口径 +d
            tax = r2(v["tax"] + d_tail)
            net = r2(gross - tax)
        else:                                    # 改税率/更正：按新税率重算；尾差正好落在这张上的也带上(原来漏了，核销段会差这几分)
            net, tax = split_gross(gross, p["new_rate"])
            tax, net = r2(tax + d_tail), r2(net - d_tail)
        exps = [dict(l) for l in v["exp_lines"]]
        ws = [float(l["dr"] or 0) for l in v["exp_lines"]]          # 各费用行按原记账的不含税额分摊新的不含税额
        base = sum(ws) or 1.0
        for f in fx:
            sn = f.get("snap") or {}
            for i, l in enumerate(exps):
                if l.get("acct") == sn.get("acct") and (l.get("fee_code") or "") == (sn.get("fee_code") or "") and \
                        (l.get("dept_code") or "") == (sn.get("dept_code") or "") and (l.get("biz_code") or "") == (sn.get("biz_code") or ""):
                    part = split_part(f, v, ws[i], base)
                    if part:                     # 只改其中一部分(V2.855)：这一行拆成两行——拆出去的按「应改为」记，剩下的维度不动
                        exps[i:i + 1] = [l, _apply_fix(l, f)]
                        ws[i:i + 1] = [ws[i] - part, part]
                    else:
                        exps[i] = _apply_fix(l, f)
                    break
        acc = 0.0
        for i, l in enumerate(exps):
            amt = r2(net - acc) if i == len(exps) - 1 else r2(net * ws[i] / base)
            acc = r2(acc + amt)
            out.append(_ln("更正", e, l["acct"], l["acct_name"], dr=amt, src=l, keep=EXP_DIMS))
        tl = v["tax_line"] or v["ap_line"] or {}
        if tax:                                  # 更正到 0%(普票不抵扣)不出 0 金额的税行
            out.append(_ln("更正", e, "2221.01.07", "暂估进项税", dr=tax, src=tl, keep=("sup_code", "sup_name")))
        out.append(_ln("更正", e, "2241.02", "供应商往来", cr=gross, src=v["ap_line"], keep=SUP_DIMS))
        v["_new"] = {"gross": gross, "tax": tax}
    # 核销：每张票一行待认证 + 每张计提一行贷暂估(更正过的用新税额、引用本凭证号)
    for v in parts:                          # 部分核销：只转出本次核销那部分对应的暂估税
        p = pl["per"][v["vno"]]
        v["_new"] = {"gross": p["gross"], "tax": r2(split_gross(p["gross"], p["new_rate"])[1] + pl["tails"].get(v["vno"], 0))}
    refs = "、".join(dict.fromkeys((self_ref if v in renew else ref_of(v, py) + ("（部分）" if v in parts else ""))
                                  for v in sorted(vouchers, key=lambda x: (x["year"], x["month"], x["vno"]))))      # 几张都更正进本凭证的，本凭证号只写一次
    pre, items, mc = merged_desc(vouchers, sup)
    hx_desc = "核销%s%s%s" % (refs, pre, items)
    all_direct = bool(vouchers) and all(v.get("direct") for v in vouchers)
    if all_direct:                           # 直接做账的费用凭证：摘要不是「计提…」，核销/支付都写「核销M/N#＋原摘要」
        hx_desc = "核销%s%s" % (refs, "、".join(dict.fromkeys(v["expl"] for v in vouchers)))
    for v in parts:                          # 凭证说明：计提多少、这次核销多少、余多少 + 人写的原因
        p = pl["per"][v["vno"]]
        hx_desc += "（%s部分核销：计提%.2f，本次核销%.2f，余%.2f未核销%s）" % (
            ref_of(v, py), v["gross"], p["gross"], v["gross"] - p["gross"], ("；" + p["memo"]) if p.get("memo") else "")
    for i in invoices:
        if i.get("deduct") is False or ctx.get("tax_later"):   # 普票等不能抵扣：不出待认证行(调用方已按 0 税率、0 税额参与核对)
            continue
        out.append(_ln("核销", "%s%s" % (i["number"], hx_desc), "2221.01.06", "待认证进项税额", dr=float(i.get("tax") or 0)))
    for v in sorted(vouchers, key=lambda x: (x["year"], x["month"], x["vno"])):
        t = v["_new"]["tax"] if (v in renew or v in parts) else v["tax"]
        if t and not ctx.get("tax_later"):       # 发票后补：这张不转暂估税，等发票到了另做
            out.append(_ln("核销", hx_desc, "2221.01.07", "暂估进项税", cr=t, src=v["tax_line"] or v["ap_line"], keep=("sup_code", "sup_name")))
    # 支付
    if ctx.get("paid"):
        pe = "%s提起支付%s%s%s" % (ctx.get("applicant") or "", sup, mc, items)
        if all_direct:
            pe = hx_desc
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


def later_lines(vouchers, invoices, pay_year, supplier):
    """发票后补：发票到了以后单独做的「暂估转待认证」凭证 → (分录, 税额尾差)。
    借 2221.01.06 每张票一行(摘要以发票号开头，发票管家据此标已做账) / 贷 2221.01.07 每张计提(费用凭证)一行(挂供应商)；
    发票税额合计 − 暂估税合计 的尾差调到金额最大那张的费用行(借方负数＝费用减少)。金额对不对得上、尾差大不大由调用方把关。"""
    vs = sorted(vouchers, key=lambda x: (x["year"], x["month"], x["vno"]))
    refs = "、".join(dict.fromkeys(ref_of(v, pay_year) for v in vs))
    if vs and all(v.get("direct") for v in vs):
        desc = "核销%s%s" % (refs, "、".join(dict.fromkeys(v["expl"] for v in vs)))
    else:
        pre, items, _ = merged_desc(vs, supplier)
        desc = "核销%s%s%s" % (refs, pre, items)
    out, it, at = [], 0.0, 0.0
    for i in invoices:
        if i.get("deduct") is False:
            continue
        out.append(_ln("核销", "%s%s" % (i["number"], desc), "2221.01.06", "待认证进项税额", dr=float(i.get("tax") or 0)))
        it += float(i.get("tax") or 0)
    for v in vs:
        if v["tax"]:
            out.append(_ln("核销", desc, "2221.01.07", "暂估进项税", cr=v["tax"], src=v["tax_line"] or v["ap_line"], keep=("sup_code", "sup_name")))
            at += v["tax"]
    d = r2(it - at)
    if abs(d) >= 0.005 and vs:
        big = max(vs, key=lambda v: v["gross"])
        e = (big.get("exp_lines") or [{}])[0]
        if e.get("acct"):
            out.append(_ln("核销", desc, e["acct"], e.get("acct_name", ""), dr=-d, src=e, keep=EXP_DIMS))
    return out, d


def balance(lines):
    dr = r2(sum(l["dr"] for l in lines))
    cr = r2(sum(l["cr"] for l in lines))
    return dr, cr
