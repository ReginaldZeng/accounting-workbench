# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-16 | Author: Claude Opus 5.5 | Version: V2.986
# Description: 物流账单复核·费用归类判定（第③轴，和量、价并列）。每张单按金蝶单据推「应该记成凭证上的哪一行」，
#   再和物流部标注、本月计提比。比的是会计口径：主体＋科目＋部门＋费用项目＋产品分类——凭证会不会落到同一行。
#   纯计算，不连金蝶；取数在 routers/logistics_classify.py。
#   口径来源：V2.735「物流部填 vs 系统判」(只到费用类型)＋计提映射表(kernels.logistics_accrual.MAP)＋2026-10-16 样机定版。
#   两条调拨规则先按 2026-09 计提的实际做法当默认值(用户 2026-10-16 定)，页面上可改：
#     ① 调到「极鲜达仓」(代工厂→极鲜达仓给山姆送货)：按销售出库运费记深圳星期零；
#     ② 调到迅鸽电商仓：记孝感星期九「孝感工厂入库」(5101 仓储物流部)。
import collections
import copy
import re

from kernels import logistics_accrual as _la

V_OK, V_BILL, V_ACCR, V_UNK = "归类一致", "标注不对", "计提记错", "判不了"
SEV = [V_BILL, V_ACCR, V_UNK, V_OK]

# 出库运费：主体 → 部门(计提映射表实证：深圳星期零挂永续物流中心、深圳星期九挂永续供应中心、孝感星期九挂仓储物流部)
OUT_DEPT = {"深圳星期零": "永续物流中心", "深圳星期九": "永续供应中心", "孝感星期九": "仓储物流部"}
# 孝感入库的几列：列名 → (科目, 部门, 产品分类要不要按业务线挂)
IN_COLS = {
    "小料入库": ("5101", "茶饮小料部", False),
    "孝感工厂入库": ("5101", "仓储物流部", False),
    "代工厂入库": ("6401", "仓储物流部", True),
    "山姆鲜食入库": ("6401", "仓储物流部", True),
    "零售入库": ("6401", "仓储物流部", True),
}
ACC_NAME = {"6601": "销售费用", "6604": "研发费用", "5101": "制造费用", "6401": "主营业务成本"}

DEFAULT_CFG = {
    # 物料分组(金蝶物料档案 FMaterialGroup) → 业务线
    "group_biz": {"小料": "小料", "植物肉": "植物肉", "零售其他": "零售", "零售山姆": "山姆零售", "鲜食山姆": "鲜食",
                  "豆蛋制品": "豆蛋制品", "花草茶": "kikiherb"},
    # 不算业务线的分组(包材、广宣品跟着货走，不决定业务线)
    "skip_groups": ["包材", "广宣品"],
    # 看不出业务线的分组：只有它们的单判不了
    "unknown_groups": ["原料"],
    # 工厂外仓承运商：仓储费、装卸费都算入库运费(用户 2026-10-12 定：工厂外仓不进销售费用)；raw_biz＝原料默认算哪条业务线(空＝照样判不了)
    "outside_wh": {"恒茂": {"raw_biz": "小料", "note": "恒茂外仓存小料原辅料"}, "易风达": {"raw_biz": "", "note": "易风达仓储费按入库费用"}},
    # 调拨单按仓库认：从上往下第一条命中的算。side＝in 看调入仓 / out 看调出仓 / any 两头都看；match 是仓库名关键字(| 分隔)
    "stock_rules": [
        {"match": "极鲜达仓", "side": "in", "fee": "出库运费", "subj": "深圳星期零", "col": "",
         "note": "调到极鲜达仓给山姆送货：按销售出库运费记深圳星期零（2026-09 计提的做法）"},
        {"match": "迅鸽", "side": "in", "fee": "入库运费", "subj": "", "col": "孝感工厂入库",
         "note": "调到迅鸽电商仓：记孝感工厂入库（2026-09 计提的做法）"},
        {"match": "和盛园|新和盛", "side": "any", "fee": "入库运费", "subj": "", "col": "代工厂入库",
         "note": "一头是代工厂仓：记代工厂入库"},
    ],
}


def merge_cfg(saved):
    """页面存的设置盖在默认值上(没存的项用默认)。"""
    c = copy.deepcopy(DEFAULT_CFG)
    for k, v in (saved or {}).items():
        if k in c and isinstance(v, type(c[k])):
            c[k] = v
    return c


# 产品分类编码 → 业务线名(同一个码对几个业务线的取通用名：CPFL011 叫「零售」不叫「山姆零售」)
BIZ_BY_CODE = {}
for _b, _c in _la.BIZLINE_CODE.items():
    if _b != "山姆零售":
        BIZ_BY_CODE.setdefault(_c, _b)


def biz_code(biz):
    return _la.BIZLINE_CODE.get(biz, "")


def acct(subj, fee, biz, col=None):
    """(主体, 费用类型, 业务线[, 指定计提列]) → 会计口径 {subj,col,acc,dept,item,pc}；判不了返回 None。"""
    if not subj or not fee:
        return None
    if fee == "研发外购":
        return dict(subj=subj, col="研发费用", acc="6604", dept="永续研发中心", item="研发外购", pc="")
    if fee == "出库运费":
        dept = OUT_DEPT.get(subj)
        if not dept or not biz or biz == "原料":
            return None
        if subj == "深圳星期零":
            name = "山姆鲜食" if biz == "鲜食" else biz
        else:
            name = biz + "出库"
        return dict(subj=subj, col=name, acc="6601", dept=dept, item="出库运费", pc=biz_code(biz))
    if fee == "仓储费":
        dept = OUT_DEPT.get(subj)
        if not dept:
            return None
        return dict(subj=subj, col="货物仓储费" + ("（%s）" % biz if biz else ""), acc="6601", dept=dept, item="货物仓储费", pc=biz_code(biz))
    if fee == "入库运费":
        if not col:
            col = {"小料": "小料入库", "植物肉": "孝感工厂入库", "鲜食": "山姆鲜食入库",
                   "零售": "零售入库", "山姆零售": "零售入库", "电商": "零售入库"}.get(biz)
        if not col or col not in IN_COLS:
            return None
        acc, dept, by_biz = IN_COLS[col]
        if by_biz and not biz:
            return None
        return dict(subj=subj, col=col + ("（%s）" % biz if by_biz and col != "山姆鲜食入库" else ""), acc=acc, dept=dept,
                    item="入库运费", pc=biz_code(biz) if by_biz else "")
    return None


def akey(a):
    return (a["subj"], a["acc"], a["dept"], a["item"], a["pc"]) if a else None


def aname(a):
    return "%s · %s" % (a["subj"], a["col"]) if a else ""


def adesc(a):
    if not a:
        return ""
    return "%s %s %s · %s · %s%s" % (a["subj"], a["acc"], ACC_NAME.get(a["acc"], ""), a["dept"], a["item"],
                                       (" · " + a["pc"]) if a["pc"] else "")


# ---------------- 物流部标注 → 归类
def _norm_annot(annot):
    a = str(annot or "").strip()
    return "" if a in ("（未标）", "(未标)", "未标", "-", "—", "无") else a


def bill_class(subj, annot, rule=None, wh=None):
    """物流部的费用标注(销售单-小料 / 采购入库-植物肉 / 研发费用-TOB …)＋主体 → 归类。
    rule：这张单命中的仓库规则——标注的费用类型和规则一致时，计提列按规则走(标注只说「调拨-电商」，记哪一列由规则定)。"""
    a = _norm_annot(annot)
    if "研发" in a:
        fee = "研发外购"
    elif "仓储" in a:
        fee = "仓储费"
    elif any(k in a for k in ("销售单", "样品单", "福利", "退货", "销售出库")):
        fee = "出库运费"
    elif any(k in a for k in ("调拨", "入库")):
        fee = "入库运费"
    else:
        fee = ""
    biz = ""
    for kw, b in (("山姆零售", "山姆零售"), ("山姆鲜食", "鲜食"), ("鲜食", "鲜食"), ("植物肉", "植物肉"), ("豆蛋", "豆蛋制品"),
                  ("小料", "小料"), ("kikiherb", "kikiherb"), ("电商", "电商"), ("零售", "零售"), ("原料", "原料")):
        if kw in a:
            biz = b
            break
    if wh is not None and (fee == "仓储费" or ("装卸" in a and not fee)):
        fee = "入库运费"                     # 工厂外仓：仓储、装卸按入库运费
    if wh is not None and biz == "原料" and wh.get("raw_biz"):
        biz = wh["raw_biz"]
    col = "代工厂入库" if (fee == "入库运费" and "代工厂" in a) else None
    if rule and fee and fee == rule.get("fee") and rule.get("col"):
        col = rule["col"]
    return dict(raw=a, fee=fee, biz=biz, subj=subj, col=col, a=acct(subj, fee, biz, col))


# ---------------- 金蝶 → 系统判
def match_stock_rule(party, rules):
    """调拨单的「源仓→目的仓」配仓库规则，返回第一条命中的(没有返回 None)。"""
    p = str(party or "")
    if "→" not in p:
        return None
    src, dst = p.split("→", 1)
    for r in rules or []:
        kws = [k for k in str(r.get("match") or "").split("|") if k.strip()]
        side = r.get("side") or "any"
        where = dst if side == "in" else src if side == "out" else src + "→" + dst
        if kws and any(k.strip() in where for k in kws):
            return r
    return None


def biz_of_groups(groups, cfg):
    """一张单各物料的分组 → (业务线, 说明, 判不了原因)。"""
    gs = [str(g or "").strip() for g in groups]
    real = [g for g in gs if g and g not in cfg["skip_groups"]]
    unk = [g for g in real if g in cfg["unknown_groups"] or g not in cfg["group_biz"]]
    bizs = sorted({cfg["group_biz"][g] for g in real if g in cfg["group_biz"]})
    cnt = collections.Counter(real)
    if len(bizs) == 1:      # 原料这类看不出用途的跟着同单认得出的那条业务线走，不一票否决
        extra = ("（%s 跟着走）" % "、".join(sorted(set(unk)))) if unk else ""
        return bizs[0], "物料分组「%s」→ 业务线 %s%s" % ("、".join(cnt), bizs[0], extra), ""
    if len(bizs) > 1:
        return "", "一张单 %d 个业务线（%s）" % (len(bizs), "、".join(bizs)), "一张单几个业务线（%s），系统不替你分" % "、".join(bizs)
    if unk:
        u = "、".join(sorted(set(unk)))
        return "", "物料分组「%s」看不出业务线" % u, "物料分组是「%s」，看不出是哪条业务线用的" % u
    shown = "、".join(sorted(set(g for g in gs if g))) or "没取到"
    return "", "物料分组只有 %s" % shown, "物料只有 %s，看不出是哪条业务线的" % shown


def sys_class(form, head, groups, party, bill_subj, cfg, short=lambda x: x, wh=None):
    """金蝶单据(单据类型 form、单据头 head、各物料分组 groups、调拨的「源仓→目的仓」party) → 系统判的归类。
    short：组织全称→简称。返回 dict(fee, biz, subj, a, how[], unk, subj_note, rule)。"""
    head = head or {}
    biz, bhow, bunk = biz_of_groups(groups, cfg)
    if not biz and wh and wh.get("raw_biz") and form in ("STK_InStock", "PUR_MRB", "STK_TransferIn", "STK_TransferOut")             and all(str(g or "") in cfg["unknown_groups"] + cfg["skip_groups"] + [""] for g in groups):
        biz, bunk = wh["raw_biz"], ""
        bhow += "；工厂外仓（%s）原料默认算 %s" % (wh.get("note") or "", biz)
    how, subj_note, rule = [], "", None
    if form == "SAL_OUTSTOCK":
        fee = "出库运费"
        subj = short(head.get("销售组织") or head.get("库存组织") or "")
        how.append("销售出库单 → 出库运费；销售组织 %s → 主体 %s" % (head.get("销售组织") or "?", subj))
    elif form == "SAL_RETURNSTOCK":
        fee = "出库运费"
        subj = short(head.get("销售组织") or "") or bill_subj
        how.append("销售退货单 → 按出库运费；主体 %s" % subj)
    elif form == "STK_MisDelivery":
        dept = head.get("部门") or ""
        fee = "研发外购" if "研发" in dept else "出库运费"
        subj = bill_subj
        subj_note = "其他出库单都开在库存组织下、部门每个公司各有一份，金蝶判不出谁承担；主体认物流部填的「%s」" % (bill_subj or "空")
        how.append("其他出库单，领料部门 %s → %s" % (dept or "没取到", fee))
    elif form in ("STK_InStock", "PUR_MRB"):
        fee = "入库运费"
        subj = short(head.get("需求组织") or head.get("库存组织") or "")
        how.append("%s → 入库运费；主体 %s" % ("采购入库单" if form == "STK_InStock" else "采购退料单", subj))
    elif form in ("STK_TransferIn", "STK_TransferOut"):
        fee = "入库运费"
        subj = short(head.get("调入组织") or head.get("库存组织") or "")
        rule = match_stock_rule(party, cfg["stock_rules"])
        how.append("分步式调拨单（%s）→ 入库运费；调入组织 → 主体 %s" % (party or "仓库没取到", subj))
        if rule:
            fee = rule.get("fee") or fee
            subj = rule.get("subj") or subj
            how.append("仓库规则：" + (rule.get("note") or rule.get("match")))
    else:
        return dict(unk="这种单据（%s）还没有规则" % (form or "没取到"), how=[bhow], a=None, fee="", biz="", subj="", subj_note="", rule=None)
    how.append(bhow)
    col = rule.get("col") if rule else None
    a = acct(subj, fee, biz, col)
    unk = ""
    if not a:
        if fee == "出库运费" and not subj and form == "STK_MisDelivery":
            unk = "账单没写主体，其他出库单金蝶也判不出是谁承担"
        elif fee == "出库运费" and not OUT_DEPT.get(subj):
            unk = "主体「%s」判不出来" % (subj or "空")
        else:
            unk = bunk or "没有对应的计提列"
    return dict(fee=fee, biz=biz, subj=subj, a=a, how=how, unk=unk, subj_note=subj_note, rule=rule)


def compare(bc, sc):
    """→ (结论, 一句话, 差在哪[])。只比 标注 vs 系统；计提那一环在 crosscheck 里补。"""
    if sc.get("unk") or not sc.get("a"):
        return V_UNK, "%s，认物流部标注「%s」" % (sc.get("unk") or "判不了", aname(bc.get("a")) or bc.get("raw") or "空"), []
    note = []
    if not bc.get("biz") and sc.get("biz") and bc.get("fee"):
        b2 = acct(bc["subj"], bc["fee"], sc["biz"], bc.get("col"))
        if b2:
            bc["a"] = b2
            note = ["标注没写业务线"]
    if not bc.get("a"):
        if not bc.get("raw"):
            return V_OK, "物流部没标，按系统判", ["没标"]
        return V_BILL, "物流部标注「%s」认不出（应记 %s）" % (bc["raw"], aname(sc["a"])), ["标注"]
    if akey(bc["a"]) == akey(sc["a"]):
        return V_OK, ("物流部标注没写业务线，按金蝶 %s" % sc["biz"]) if note else "", note
    A, B, diff = sc["a"], bc["a"], []
    if A["subj"] != B["subj"]:
        diff.append("主体")
    if A["acc"] != B["acc"] or A["item"] != B["item"]:
        diff.append("费用类型")
    elif A["dept"] != B["dept"] and A["subj"] == B["subj"]:     # 主体不同部门自然不同，不另列
        diff.append("部门")
    if A["pc"] != B["pc"]:
        diff.append("业务线")
    return V_BILL, "应记 %s，标注 %s" % (aname(A), aname(B)), diff


# ---------------- 和本月计提对照
def accr_key(e):
    """计提分录(routers.logistics_review._accr_entries 的一条) → 会计口径 key。"""
    return (str(e.get("subject") or "").strip(), str(e.get("acct") or "")[:4], str(e.get("dept") or "").strip(),
            str(e.get("fee") or "").strip(), str(e.get("biz_code") or "").strip())


def key_name(k):
    subj, acc, dept, item, pc = k
    if acc == "6604":
        col = "研发费用"
    elif item == "入库运费":
        if acc == "6401":       # 代工厂/山姆鲜食/零售入库都是 6401 仓储物流部，凭证上只靠产品分类分
            biz = BIZ_BY_CODE.get(pc, pc)
            col = "6401 入库（%s）" % (biz or "无产品分类")
        else:
            col = next((c for c, (a, d, _by) in IN_COLS.items() if a == acc and d == dept), dept + " 入库运费")
    elif item == "出库运费":
        biz = BIZ_BY_CODE.get(pc, pc) or "无产品分类"
        col = ("山姆鲜食" if biz == "鲜食" else biz) if subj == "深圳星期零" else biz + "出库"
    else:
        col = "%s %s" % (item, dept)
    return "%s · %s" % (subj, col)


def crosscheck(docs, accr_entries):
    """docs：[{key, amount, bc, sc, v, txt, diff}](已 compare 过)；accr_entries：本月计提分录。
    补每张单的计提那一环(系统判的那一列，本月计提有没有)，并出「和计提对照」表：
    每个计提列 计提金额 / 账单按物流部标注 / 账单按系统判。判不了的单按标注放。"""
    tab = collections.OrderedDict()

    def b(k, name="", desc=""):
        if k not in tab:
            tab[k] = dict(name=name or (key_name(k) if k and k[0] != "?" else "物流部没标 / 标注认不出"), desc=desc,
                          accr=0.0, by_bill=0.0, by_sys=0.0, n_bill=0, n_sys=0)
        return tab[k]

    for e in accr_entries or []:
        if str(e.get("subject") or "") == "—":
            continue
        x = b(accr_key(e))
        x["accr"] = round(x["accr"] + float(e.get("amt") or 0), 2)
    for d in docs:
        bc, sc = d["bc"], d["sc"]
        bk = akey(bc.get("a")) or ("?",)
        x = b(bk, aname(bc.get("a")), adesc(bc.get("a")))
        x["by_bill"] = round(x["by_bill"] + d["amount"], 2)
        x["n_bill"] += 1
        eff = sc.get("a") if d["v"] != V_UNK else bc.get("a")
        ek = akey(eff) or ("?",)
        y = b(ek, aname(eff), adesc(eff))
        y["by_sys"] = round(y["by_sys"] + d["amount"], 2)
        y["n_sys"] += 1
    has_accr = any(x["accr"] for x in tab.values())      # 一条计提都没取到(还没计提 / 金蝶没连上)：计提这一环不判，免得整家误报「计提记错」
    for d in docs:
        sk = akey(d["sc"].get("a")) if d["v"] != V_UNK else None
        d["acc_miss"] = has_accr and bool(sk) and not (tab.get(sk) and tab[sk]["accr"])
        d["hint"] = ""
        if d["v"] == V_OK and d["acc_miss"]:
            d["v"], d["txt"] = V_ACCR, "应记 %s，本月计提没有这一列" % aname(d["sc"]["a"])
        if d["v"] == V_BILL and has_accr:
            ab, asys = tab.get(akey(d["bc"].get("a")) or ("?",)), tab.get(sk)
            d["hint"] = "本月计提「%s」%s；「%s」%s" % (
                aname(d["bc"].get("a")) or "—", ("有 %.2f" % ab["accr"]) if ab and ab["accr"] else "没有",
                aname(d["sc"]["a"]), ("有 %.2f" % asys["accr"]) if asys and asys["accr"] else "没有")
    rows = []
    for k, x in tab.items():
        if not x["desc"] and k and k[0] != "?":
            x["desc"] = "%s %s %s · %s · %s%s" % (k[0], k[1], ACC_NAME.get(k[1], ""), k[2], k[3], (" · " + k[4]) if k[4] else "")
        x["d_bill"] = round(x["by_bill"] - x["accr"], 2)
        x["d_sys"] = round(x["by_sys"] - x["accr"], 2)
        x["hot"] = abs(x["d_sys"]) >= 1 and (abs(x["by_bill"] - x["by_sys"]) >= 1 or not x["accr"] or not x["by_sys"])
        rows.append(x)
    return rows


def classify(docs_in, kd, groups_of, accr_entries, cfg, short=lambda x: x, carrier=""):
    """一整家承运商一个月：
    docs_in：[{key, docs:[单号], subject, annot, amount}]（一张单一条，几行账单已合并）；
    kd：{单号: {form, head, party, codes:[物料编码]}}；groups_of：{物料编码: 物料分组}；
    → {docs:[{key, v, txt, diff, sys, sys_d, bill, bill_d, how, subj_note, unk, acc_miss, hint}], tab, counts, amounts}"""
    wh = (cfg.get("outside_wh") or {}).get(carrier)
    work = []
    for d in docs_in:
        nos = [n for n in d.get("docs") or [] if n]
        if not nos:
            sc = dict(unk="没有金蝶单号", how=[], a=None, fee="", biz="", subj="", subj_note="", rule=None)
            bc = bill_class(d.get("subject"), d.get("annot"), None, wh)
        else:
            scs = []
            for n in nos:
                o = kd.get(n)
                if not o:
                    scs.append(dict(unk="金蝶查不到 %s" % n, how=[], a=None, fee="", biz="", subj="", subj_note="", rule=None))
                    continue
                scs.append(sys_class(o.get("form"), o.get("head"), [groups_of.get(c, "") for c in o.get("codes") or []],
                                     o.get("party"), d.get("subject"), cfg, short, wh))
            sc = scs[0]
            if any(akey(s.get("a")) != akey(sc.get("a")) or s.get("unk") for s in scs[1:]):
                sc = dict(unk="几张单归类不一样", how=[h for s in scs for h in s["how"]], a=None, fee="", biz="", subj="", subj_note="", rule=None)
            bc = bill_class(d.get("subject"), d.get("annot"), sc.get("rule"), wh)
        v, txt, diff = compare(bc, sc)
        work.append(dict(key=d["key"], amount=float(d.get("amount") or 0), bc=bc, sc=sc, v=v, txt=txt, diff=diff))
    tab = crosscheck(work, accr_entries)
    out = []
    for w in work:
        sc, bc = w["sc"], w["bc"]
        out.append(dict(key=w["key"], v=w["v"], txt=w["txt"], diff=w["diff"], sys=aname(sc.get("a")), sys_d=adesc(sc.get("a")),
                        bill=aname(bc.get("a")), bill_d=adesc(bc.get("a")), how=[h for h in sc.get("how") or [] if h],
                        subj_note=sc.get("subj_note") or "", unk=sc.get("unk") or "", acc_miss=w["acc_miss"], hint=w["hint"],
                        amount=round(w["amount"], 2)))
    counts = {v: sum(1 for x in out if x["v"] == v) for v in SEV}
    amounts = {v: round(sum(x["amount"] for x in out if x["v"] == v), 2) for v in SEV}
    has_accr = any(x["accr"] for x in tab)
    return dict(docs=out, tab=tab, counts=counts, amounts=amounts, accr_ok=has_accr,
                accr_note="" if has_accr else "本月计提一条也没取到（可能还没计提，或者金蝶没连上），计提这一环先不判",
                accr_total=round(sum(float(e.get("amt") or 0) for e in accr_entries or [] if str(e.get("subject") or "") != "—"), 2))
