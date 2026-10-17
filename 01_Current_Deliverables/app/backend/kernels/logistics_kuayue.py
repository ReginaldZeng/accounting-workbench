# -*- coding: utf-8 -*-
# [Change Log] Date:2026-10-17 Author:Kimi Version:V2.990
# 跨越速运「官网计价」核价核量内核（照 2026-10-17 定稿样机，用户 10-17「开发吧」）：
#   价三样：①公式重算＝原价（首重＋分段续重；带「运费最低收费X（四舍五入取整）」的先四舍五入取整再和最低收费取大）
#           ②原价 × 折扣% ＝ 折后（分位四舍五入）
#           ③折后 ＋ 附加费（夜间取货费/保费/退仓费/资源调配费）＝ 应付
#           三样全对＝价通过；官网原价本身合不合理不评判（没有价目表，不伪造依据）。
#   量：系数＝账单计费重量 ÷ 金蝶净重，落在 [lo, hi] 内算量对；账单计费＝起重 且 金蝶净重×lo 不到起重的，
#       判「按最低计费」直接算量对（不到起重按起重收费是跨越明示规则，旧口径把这类单全误报多报）；
#       超上限只把超出部分折钱（按该单最重一档续重单价×折扣估）判多报；低于下限判少报（我方有利只标记）；
#       金蝶无数 / Pcs 计量判「量核不了」；备注带「体积」的体积重计费判「量核不了·体积重」（→只核了价）；
#       备注带「退回」的退回运输判「量核不了·退回」——金蝶里查到的是原正向出库单，口径不可比，人工核。
#   常量：系数区间默认 (0.95, 1.20) 为建议值（待业务确认；正式口径按类型各配一套），承运商档案配了毛重比范围就用配的。
import math
import re

KY_SUBKEY = "跨越计价"          # 账单行 sub_fees 里存原始计价字段的键（JSON 字符串，非数字、不进费用归类）
KY_DEFAULT_LO, KY_DEFAULT_HI = 0.95, 1.20
KY_DEFAULT_DISC = 70.0           # 账单缺折扣列时的默认值（%）；跨越 2026-09 全月 70

_MINFEE_RE = re.compile(r",运费最低收费([\d.]+)（四舍五入取整）")
_TIER_RE = re.compile(r"^([\d.]+)\*([\d.]+)$")


def _r0(x):
    """JS Math.round 语义（半进位），账单「四舍五入取整」按这个来，不用 Python 银行家舍入。"""
    return float(math.floor(x + 0.5))


def _r2(x):
    """分位四舍五入（半进位），账单折后/应付都是这么算的。"""
    return _r0((x + 1e-9) * 100) / 100.0


def parse_freight_formula(s):
    """解析跨越运费公式文本 → {'calc','base','minfee','rounded','first','tiers'} / 解析不了返回 None。
    '10.0+19.5*3.5' → 首重10 + 19.5kg×3.5；'20.0+9.0*12.0,运费最低收费128.0（四舍五入取整）' → 先求和再四舍五入取整、
    和最低收费取大。tiers＝各续重段 [(kg, rate)]，按出现顺序；rate_top＝最后一档单价（超重估金额用）。"""
    s = str(s or "").strip()
    if not s:
        return None
    expr, minfee, rounded = s, None, False
    m = _MINFEE_RE.search(s)
    if m:
        minfee = float(m.group(1))
        expr = s[:m.start()]
        rounded = True
    tiers, first, base = [], 0.0, 0.0
    for i, t in enumerate(expr.split("+")):
        t = t.strip()
        mm = _TIER_RE.match(t)
        if mm:
            kg, rate = float(mm.group(1)), float(mm.group(2))
            tiers.append((kg, rate))
            base += kg * rate
        else:
            try:
                v = float(t)
            except ValueError:
                return None
            if i == 0:
                first = v
            base += v
    calc = _r0(base) if rounded else _r2(base)
    if minfee is not None:
        calc = max(calc, minfee)
    return {"calc": calc, "base": round(base, 4), "minfee": minfee, "rounded": rounded,
            "first": first, "tiers": tiers, "rate_top": tiers[-1][1] if tiers else None}


def price_check(d):
    """价三样核对。d＝账单行原始计价字段 {'formula','orig','disc','net','sur','pay'}。
    返回 {ok_f, ok_d, ok_p, ok_all, orig_calc, net_calc, pay_calc, rate_top, diff_amt, bads[]}。"""
    f = parse_freight_formula(d.get("formula"))
    orig = _num(d.get("orig"))
    disc = _num(d.get("disc"))
    net = _num(d.get("net"))
    sur = _num(d.get("sur")) or 0.0
    pay = _num(d.get("pay"))
    if disc is not None and 0 < disc <= 1:          # 折扣写成 0.7 的比例而不是 70 的百分数
        disc = disc * 100
    if disc is None:
        disc = KY_DEFAULT_DISC
    ok_f = ok_d = ok_p = None
    orig_calc = net_calc = pay_calc = None
    if f is not None and orig is not None:
        orig_calc = f["calc"]
        ok_f = abs(f["calc"] - orig) <= 0.005
    if orig is not None:
        net_calc = _r2(orig * disc / 100)
        if net is not None:
            ok_d = abs(net_calc - net) <= 0.005
    if net is not None or orig is not None:
        pay_calc = _r2((net if net is not None else net_calc) + sur)
        if pay is not None:
            ok_p = abs(pay_calc - pay) <= 0.005
    ok_all = bool(ok_f and ok_d and ok_p)
    diff_amt = 0.0
    if pay is not None and orig is not None:
        diff_amt = _r2(pay - _r2(f["calc"] * disc / 100) - sur) if f else _r2(pay - _r2(orig * disc / 100) - sur)
    bads = []
    if ok_f is False:
        bads.append("公式重算 %s ≠ 原价 %s" % (_fm(orig_calc), _fm(orig)))
    if ok_d is False:
        bads.append("折扣应 %s，账单 %s" % (_fm(net_calc), _fm(net)))
    if ok_p is False:
        bads.append("应付应 %s，账单 %s" % (_fm(pay_calc), _fm(pay)))
    return {"ok_f": ok_f, "ok_d": ok_d, "ok_p": ok_p, "ok_all": ok_all,
            "orig_calc": orig_calc, "net_calc": net_calc, "pay_calc": pay_calc,
            "minfee": f["minfee"] if f else None, "rounded": f["rounded"] if f else None,
            "rate_top": f["rate_top"] if f else None, "diff_amt": diff_amt, "bads": bads}


def qty_check(d, kd_sum, lo=None, hi=None, kd_miss=False):
    """量判定（起重优先 → 系数区间 → 降级）。kd_sum＝金蝶净重合计(kg，非千克计量折不了=0/None)。
    返回 {'label','top','sub','diff_kg','diff_amt','conv'}；label ∈ 量对/按最低计费/多报/少报/量核不了/量核不了·体积重/量核不了·退回。"""
    lo = float(lo if lo is not None else KY_DEFAULT_LO)
    hi = float(hi if hi is not None else KY_DEFAULT_HI)
    bw = _num(d.get("bw"))                    # 计费重量（跨越按它收费，拿它核）
    minwt = _num(d.get("minwt"))              # 起重＝最低计费重量
    aw = _num(d.get("aw"))                    # 实际重量
    note = str(d.get("note") or "")
    kd = _num(kd_sum) or 0.0
    rate_top = None
    f = parse_freight_formula(d.get("formula"))
    if f:
        rate_top = f["rate_top"]
    if "退回" in note:
        return {"label": "量核不了·退回", "top": "退回运输", "sub": "账单备注「%s」：这是退回/反向运输，金蝶里同号的是原正向出库单，口径不可比，人工核" % note[:60],
                "diff_kg": None, "diff_amt": 0.0, "conv": None}
    if "体积" in note:
        return {"label": "量核不了·体积重", "top": "体积重计费",
                "sub": "实际 %s kg · 计费 %s kg（%s），ERP 无体积数据，仅提示" % (_fg(aw), _fg(bw), note[:60]),
                "diff_kg": None, "diff_amt": 0.0, "conv": None}
    if kd_miss:
        return {"label": "量核不了", "top": "金蝶查无此单", "sub": "金蝶里没找到这张单的出库单（单号填错 / 未审核 / 拆单后缀）",
                "diff_kg": None, "diff_amt": 0.0, "conv": None}
    if kd <= 0:
        # 账单计费正好＝起重：这是跨越明示的最低收费（账单也写「最低收费」），金蝶折不折得出千克不影响
        # 「按起重收费」这个判断——按最低计费放行。金蝶真有重量且净重×下限≥起重的，conv_lo 顶上来、落不到这条。
        if bw is not None and minwt is not None and abs(bw - minwt) < 1e-9:
            return {"label": "按最低计费", "top": "按最低计费 %g kg" % minwt,
                    "sub": "实际 %s kg · 金蝶按 Pcs/升计量折不了千克；账单计费正好＝起重 %g，按最低收费 ✓" % (_fg(aw), minwt),
                    "diff_kg": 0.0, "diff_amt": 0.0, "conv": None}
        return {"label": "量核不了", "top": "金蝶无重量",
                "sub": (note[:60] + "；" if note else "") + "金蝶侧无数（Pcs 计量 / 没填单位换算），折不了千克",
                "diff_kg": None, "diff_amt": 0.0, "conv": None}
    conv = round(bw / kd, 3) if bw else None
    conv_lo, conv_hi = kd * lo, kd * hi
    if bw is not None and minwt is not None and abs(bw - minwt) < 1e-9 and conv_lo < minwt - 1e-9:
        return {"label": "按最低计费", "top": "按最低计费 %g kg" % minwt,
                "sub": "实际 %s kg · 金蝶 %s kg，不到起重 %g，按最低收费 ✓" % (_fg(aw), _fg(kd), minwt),
                "diff_kg": 0.0, "diff_amt": 0.0, "conv": conv}
    if bw is not None and bw > conv_hi + 0.005:
        diff_kg = round(bw - max(conv_hi, minwt or 0) + 1e-9, 3)
        diff_amt = _r2(diff_kg * rate_top * (_num(d.get("disc")) or KY_DEFAULT_DISC) / 100) if rate_top else 0.0
        return {"label": "多报", "top": "账单 %s kg · 应收 %s kg" % (_fg(bw), _fg(round(max(conv_hi, minwt or 0), 3))),
                "sub": "系数 %s 超上限 %s，多报 %s kg" % (("%.3f" % conv) if conv else "—", hi, _fg(diff_kg)),
                "diff_kg": diff_kg, "diff_amt": diff_amt, "conv": conv}
    if bw is not None and bw < conv_lo - 0.005:
        diff_kg = round(conv_lo - bw + 1e-9, 3)
        return {"label": "少报", "top": "账单 %s kg · 下限 %s kg" % (_fg(bw), _fg(round(conv_lo, 3))),
                "sub": "系数 %s 低于下限 %s，少报 %s kg（我方有利，只标记）" % (("%.3f" % conv) if conv else "—", lo, _fg(diff_kg)),
                "diff_kg": -diff_kg, "diff_amt": 0.0, "conv": conv}
    return {"label": "量对", "top": "账单 %s kg · 金蝶 %s kg" % (_fg(bw), _fg(round(kd, 3))),
            "sub": "系数 %s 在 %s–%s 内" % (("%.3f" % conv) if conv else "—", lo, hi),
            "diff_kg": 0.0, "diff_amt": 0.0, "conv": conv}


def verdict(price, qty):
    """价优先于量（价错了，量的比较就没意义了）。返回 {'verdict','why','diff'}。
    六态：多收/少收/量核不了/只核了价/一致（+价待确认不在跨越出现——价有账单公式做依据）。"""
    if not price["ok_all"]:
        v = "多收" if price["diff_amt"] > 0 else "少收"
        return {"verdict": v, "why": price["bads"][0] if price["bads"] else "账单计价有误",
                "diff": price["diff_amt"]}
    lb = qty["label"]
    if lb == "多报":
        return {"verdict": "多收", "why": "账单多报 %s kg ≈ 多收 %s 元（%s）" % (_fg(qty["diff_kg"]), _fm(qty["diff_amt"]), qty["sub"]),
                "diff": qty["diff_amt"]}
    if lb == "少报":
        return {"verdict": "少收", "why": "账单少报 %s kg（我方有利，只标记）" % _fg(-qty["diff_kg"]), "diff": 0.0}
    if lb == "量核不了":
        return {"verdict": "量核不了", "why": qty["sub"], "diff": None}
    if lb == "量核不了·体积重":
        return {"verdict": "只核了价", "why": qty["sub"], "diff": None}
    if lb == "量核不了·退回":
        return {"verdict": "量核不了", "why": qty["sub"], "diff": None}
    if lb == "按最低计费":
        return {"verdict": "一致", "why": "按最低收费 ✓ · 量价都对上", "diff": 0.0}
    return {"verdict": "一致", "why": "量价都对上", "diff": 0.0}


def _num(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _fm(v):
    return ("{:,.0f}" if v is not None and abs(v - round(v)) < 0.005 else "{:,.2f}").format(v if v is not None else 0)


def _fg(v):
    return ("{:,.3f}".format(v)).rstrip("0").rstrip(".") if v is not None else "—"
