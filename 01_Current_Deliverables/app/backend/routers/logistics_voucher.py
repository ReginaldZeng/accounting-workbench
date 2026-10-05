# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-02 | Author: Claude Opus 5.5 | Version: V2.749
# Description: 【应付板块 › 物流对账 › 付款做账】一张物流请款单 → 一张合成凭证预览（红冲 → 更正 → 核销 → 支付），**第一版只预览不写金蝶**。
#   数据：请款单(logistics_payreq，钉钉) / 发票(发票管家票夹，按审批实例号认，谁建的票夹都算) / 计提凭证(金蝶本期 2241 贷方挂该供应商、
#   摘要「计提」的那几张，整张分录带全部核算维度) / 计提更正(复核台登记的) / 付款(金蝶付款单：日期+我方银行账号)。
#   能不能做账(用户定)：已付款 + 发票齐(票合计＝请款金额) + 纸质件都到了；纸质件没到可手动放行(记谁放的)。
#   分工(用户同意 A)：物流的发票内容审核在这一页做(暂估 vs 发票逐张比)；发票管家只管识别/查重/纸质件/凭证同步。
#   凭证摘要待认证行以发票号开头 → 发票管家 V2.746 定时同步会自动把那张票标「已做账·凭证号」，这里不另回写。
import json
import time
from datetime import datetime

from fastapi import APIRouter, Request
from sqlalchemy import select, text

from core import JSONResponse, _require_perm, db
import kingdee_client as kc
import todo_scenes
from kernels import logistics_review_store as store
from kernels import logistics_voucher as LV

router = APIRouter()
PR = store.payreq
FX = store.review_line_fix
_OVR_KEY = "logi_voucher_paper_ok"          # {inst_id: {by, at}} 纸质件没到、手动放行做账
_POSTED_KEY = "logi_voucher_posted"         # {inst_id: {bill_no, vid, vno, book, at, by}} 已写入金蝶
_ACC_CACHE = {}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _perm(request):
    return _require_perm(request, "logistics_upload")


# 金蝶付款单单据状态：Z 暂存 / A 创建 / B 审核中 / C 已审核 / D 重新审核
_PAY_ST = {"Z": "暂存", "A": "创建", "B": "审核中", "C": "已审核", "D": "重新审核"}


def _paid_info(r):
    """kd_paid = '日期|状态|付款单FID'（付款单）或 '日期|记-N|gl:xxx'（已有支付凭证）。"""
    v = str(r.get("kd_paid") or "")
    if not v:
        return None
    p = v.split("|")
    return {"date": p[0], "voucher": p[1] if len(p) > 2 and str(p[2]).startswith("gl:") else "",
            "bill_id": p[2] if len(p) > 2 and not str(p[2]).startswith("gl:") else ""}


def _invoices(inst_id):
    """审批单对应的票夹(按实例号，钉钉自动接入/物流拉的/扫码建的都算) → (票夹, [发票])。"""
    with db._engine.connect() as c:
        f = c.execute(text("select id, status from inv_folder where inst_id=:i order by id desc limit 1"), {"i": inst_id}).mappings().first()
        if not f:
            return None, []
        rows = c.execute(text("select id, number, inv_type, type_label, total, amount, tax, tax_rate, paper, review, flags_json, "
                              "seller_name, buyer_name from inv_item where folder_id=:f and kind='invoice' and status='active' order by id"),
                         {"f": f["id"]}).mappings().all()
    out = []
    for x in rows:
        try:
            fl = json.loads(x["flags_json"]) if isinstance(x["flags_json"], str) else (x["flags_json"] or {})
        except Exception:
            fl = {}
        bk = fl.get("_bookkeeping") or {}
        # 能抵扣的只有专票、旅客运输票；普票/出租车票等不抵扣(用户 2026-10-02「普票不能抵扣，不做更正」)
        typ, short, tag = _inv_type_text(x["inv_type"], x["type_label"])
        out.append({"id": x["id"], "number": x["number"] or "", "type": typ, "type_short": short, "type_tag": tag,
                    "deduct": (x["inv_type"] or "") in ("special", "travel"),
                    "gross": float(x["total"] or 0), "net": float(x["amount"] or 0), "tax": float(x["tax"] or 0),
                    "rate": x["tax_rate"] or "", "paper": bool(x["paper"]), "review": x["review"] or "",
                    "booked": bk.get("status") == "booked", "vouchers": bk.get("vouchers") or [],
                    "seller": x["seller_name"] or "", "buyer": x["buyer_name"] or ""})
    return dict(f), out


def _inv_type_text(inv_type, label):
    """发票管家的 type_label 存的是票面左上角那行字：数电票带「特定业务」的(货物运输服务/建筑服务…)存的是特定业务名、不是票种，
    直接当类型显示就成了「货物运输服务」「电子发票（增值税专用发票）」混着出现(用户 2026-10-03「发票类型怎么奇奇怪怪」)。
    这里统一成：票种看 inv_type，特定业务单独给。→ (全称, 简称, 特定业务)"""
    from kernels.invoice_excel import INV_TYPE_LABELS
    from kernels.invoice_parse import _SPECIAL_LABELS
    it, lab = str(inv_type or ""), str(label or "").strip()
    base = INV_TYPE_LABELS.get(it, "")
    short = {"special": "专票", "normal": "普票"}.get(it) or base or lab or it
    tag = lab if (lab in _SPECIAL_LABELS and lab != base) else ""
    full = (base or lab or it) + ("（%s）" % tag if tag else "")
    return full, short, tag


def _status(r, folder, invs, ovr):
    pi = _paid_info(r)
    tot = round(sum(i["gross"] for i in invs), 2)
    if invs and all(i["booked"] for i in invs):
        return "booked"
    if not pi:
        return "unpaid"
    if not invs:
        return "noinv"
    if abs(tot - float(r.get("amount") or 0)) >= 0.005:
        return "invdiff"
    # 纸质件不卡做账(用户 2026-10-02 改：月末统一查验，同发票管家 V2.739/740)，列表只提示到了几张
    return "ready"


@router.get("/api/logistics-voucher/list")
def vlist(request: Request, since: str = "2026-09-01"):
    if not _perm(request):
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    ovr = db.get_setting(_OVR_KEY, None) or {}
    posted = db.get_setting(_POSTED_KEY, None) or {}
    with db._engine.connect() as c:
        reqs = [dict(r) for r in c.execute(select(PR).where(PR.c.create_time >= since)).mappings().all()]
    out = []
    # 物流商写金蝶全称、主体写简称，都带编码(用户 2026-10-02)
    try:
        from routers.logistics_payreq import _kd_suppliers
        c2n = _kd_suppliers().get("code2name") or {}
    except Exception:
        c2n = {}
    s2book = {o.get("short_name"): o.get("book_code") for o in (db.list_orgs() or []) if o.get("short_name")}
    for r in reqs:
        if r.get("excluded") or r.get("dt_status") == "TERMINATED" or r.get("dt_result") == "refuse":
            continue
        folder, invs = _invoices(r["inst_id"])
        pi = _paid_info(r) or {}
        out.append({"inst": r["inst_id"], "bid": r.get("business_id"), "carrier": r.get("carrier"), "code": r.get("sup_code"),
                    "sup_full": c2n.get(r.get("sup_code")) or r.get("payee") or r.get("carrier"), "book": s2book.get(r.get("subject")) or "",
                    "payee": r.get("payee"), "subject": r.get("subject"), "amount": r.get("amount"), "period": r.get("period") or "",
                    "applicant": r.get("applicant"), "created": r.get("create_time"), "paid": pi.get("date") or "",
                    "paid_voucher": pi.get("voucher") or "", "folder": folder["id"] if folder else None,
                    "pay_st": _PAY_ST.get(str(r.get("kd_paid") or "").split("|")[1] if "|" in str(r.get("kd_paid") or "") else "", ""),
                    "dt_status": r.get("dt_status"), "dt_result": r.get("dt_result"),
                    "n_inv": len(invs), "inv_total": round(sum(i["gross"] for i in invs), 2),
                    "n_paper": sum(1 for i in invs if i["paper"]), "paper_ovr": ovr.get(r["inst_id"]),
                    "booked": sorted({str(v) for i in invs for v in (i["vouchers"] or [])})[:5],
                    "posted": posted.get(r["inst_id"]),
                    "status": "booked" if r["inst_id"] in posted else _status(r, folder, invs, ovr)})
    out.sort(key=lambda x: (x["created"] or ""), reverse=True)
    return {"ok": True, "rows": out}


# ---------- 金蝶：计提凭证 / 付款单 ----------
_F = [("FVOUCHERGROUPNO", "号"), ("FEXPLANATION", "摘要"), ("FAccountID.FNumber", "科目"), ("FAccountID.FName", "科目名"),
      ("FDEBIT", "借"), ("FCREDIT", "贷"), ("FDetailID.FFLEX4.FNumber", "供应商码"), ("FDetailID.FFLEX4.FName", "供应商"),
      ("FDetailID.FFLEX5.FNumber", "部门码"), ("FDetailID.FFLEX5.FName", "部门"), ("FDetailID.FFLEX9.FNumber", "费用码"),
      ("FDetailID.FFLEX9.FName", "费用"), ("FDetailID.FF100010.FNumber", "分类码"), ("FDetailID.FF100010.FDataValue", "分类"),
      ("FDetailID.FF100006.FNumber", "项目码"), ("FDetailID.FF100006.FDataValue", "项目"),
      ("FDetailID.FF100005.FNumber", "供应商分组")]


def _s(v):
    return str(v or "").strip()


def _accruals(book, period, sup_code):
    """本期挂该供应商的计提凭证(整张分录) → ([acc_voucher], 提示)。10 分钟缓存。"""
    from routers.logistics_review import _accr_is_current
    k = (book, period, sup_code)
    hit = _ACC_CACHE.get(k)
    if hit and time.time() - hit[1] < 600:
        return hit[0]
    y, m = int(period[:4]), int(period[5:7])
    s, conf = kc.login()
    base = "FACCOUNTBOOKID.FName='%s' and FYear=%d and FPeriod=%d" % (book.replace("'", ""), y, m)
    heads = kc._query(s, conf, "GL_VOUCHER", _F, base + " and FAccountID.FNumber like '2241%%' and FCREDIT<>0 and "
                      "FDetailID.FFLEX4.FNumber='%s'" % sup_code.replace("'", ""))
    vnos = sorted({_s(h["号"]) for h in heads if "计提" in _s(h["摘要"]) and _accr_is_current(h["摘要"], period)}, key=lambda x: int(x) if x.isdigit() else 0)
    notes, out = [], []
    if vnos:
        rows = kc._query(s, conf, "GL_VOUCHER", _F, base + " and FVOUCHERGROUPNO in (%s)" % ",".join("'%s'" % v for v in vnos))
        by = {}
        for r in rows:
            by.setdefault(_s(r["号"]), []).append({
                "acct": _s(r["科目"]), "acct_name": _s(r["科目名"]), "dr": float(r["借"] or 0), "cr": float(r["贷"] or 0),
                "expl": _s(r["摘要"]), "sup_code": _s(r["供应商码"]), "sup_name": _s(r["供应商"]), "dept_code": _s(r["部门码"]),
                "dept": _s(r["部门"]), "fee_code": _s(r["费用码"]), "fee": _s(r["费用"]), "biz_code": _s(r["分类码"]),
                "biz": _s(r["分类"]), "proj_code": _s(r["项目码"]), "proj": _s(r["项目"]), "sup_grp": _s(r["供应商分组"])})
        for vno in vnos:
            ls = by.get(vno) or []
            sups = {l["sup_code"] for l in ls if l["acct"].startswith("2241") and l["sup_code"]}
            if len(sups) > 1:
                notes.append("记-%s 一张凭证里计提了 %d 家供应商，整张红冲会动到别家，要人工处理" % (vno, len(sups)))
                continue
            out.append(LV.acc_voucher(vno, ls, y, m))
    res = (out, notes)
    _ACC_CACHE[k] = (res, time.time())
    return res


_HERE_CACHE = {}


def _here_dims(book, year, month, fee_name, biz_code):
    """主体更正要在本主体补提：这类费用(按费用项目名，如「出库运费」)本主体平时计提挂哪个科目/费用项目编码/部门。
    各主体的部门编码不一样(深圳星期零 永续物流中心 0011401 / 深圳星期九 永续供应中心 0010401)，科目也可能不一样(孝感入库运费记 5101)，
    所以到本主体账簿里找同名费用项目的计提分录，取最常用的那组；优先同产品分类的、计提当月的，没有再看全年。找不到返回 None(人工)。"""
    if not fee_name:
        return None
    k = (book, year, month, fee_name, biz_code or "")
    hit = _HERE_CACHE.get(k)
    if hit and time.time() - hit[1] < 600:
        return hit[0]
    from collections import Counter
    s, conf = kc.login()
    res = None
    for per in ("FYear=%d and FPeriod=%d" % (year, month), "FYear=%d" % year):
        try:
            rows = kc._query(s, conf, "GL_VOUCHER", _F, "FACCOUNTBOOKID.FName='%s' and %s and FDEBIT>0 and FDetailID.FFLEX9.FName='%s'" % (
                book.replace("'", ""), per, str(fee_name).replace("'", "")))
        except Exception:
            rows = []
        rows = [r for r in rows if "计提" in _s(r["摘要"]) and "红冲" not in _s(r["摘要"]) and _s(r["部门码"]) and _s(r["科目"])[:1] in ("5", "6")]
        same = [r for r in rows if biz_code and _s(r["分类码"]) == biz_code]
        pool = same or rows
        if pool:
            top = Counter((_s(r["科目"]), _s(r["科目名"]), _s(r["部门码"]), _s(r["部门"]), _s(r["费用码"]), _s(r["费用"])) for r in pool).most_common(1)[0][0]
            res = dict(zip(("acct", "acct_name", "dept_code", "dept", "fee_code", "fee"), top))
            break
    _HERE_CACHE[k] = (res, time.time())
    return res


def _own_claim(book, period, sup_code):
    """另一个主体自己这个月对这家供应商的请款合计——它名下对得上这个数的计提是它自己的，不能当成「记错主体」拿过来。"""
    with db._engine.connect() as c:
        rs = c.execute(select(PR.c.amount, PR.c.excluded, PR.c.dt_status, PR.c.dt_result).where(
            (PR.c.subject_full == book) & (PR.c.period == period) & (PR.c.sup_code == sup_code))).all()
    return round(sum(float(a or 0) for a, ex, st, rs_ in rs if not ex and st != "TERMINATED" and rs_ != "refuse"), 2)


def _reversed_in(book, sup_code, v):
    """原主体账上这张计提红冲了没有：计提月及以后、2241 贷方＝负的含税额、挂这家供应商、摘要带「红冲」。→ {vno, year, month} / None。只读。"""
    try:
        s, conf = kc.login()
        rows = kc._query(s, conf, "GL_VOUCHER", [("FVOUCHERGROUPNO", "号"), ("FEXPLANATION", "摘要"), ("FYear", "年"), ("FPeriod", "期")],
                         "FACCOUNTBOOKID.FName='%s' and FAccountID.FNumber like '2241%%' and FCREDIT=%.2f and FDetailID.FFLEX4.FNumber='%s' "
                         "and FYear>=%d" % (book.replace("'", ""), -float(v["gross"]), sup_code.replace("'", ""), v["year"]))
    except Exception:
        return None
    for r in rows:
        y, m = int(r.get("年") or 0), int(r.get("期") or 0)
        if "红冲" in _s(r["摘要"]) and (y, m) >= (v["year"], v["month"]):
            return {"vno": _s(r["号"]), "year": y, "month": m}
    return None


def _bank_of(bill_id):
    """付款单 FID → (付款日, 我方银行账号)。"""
    if not str(bill_id or "").isdigit():
        return None, ""
    try:
        s, conf = kc.login()
        rows = kc._query(s, conf, "AP_PAYBILL", [("FDate", "日期"), ("FACCOUNTID.FNumber", "账号"), ("FBillNo", "单号")], "FID=%s" % int(bill_id))
    except Exception:
        return None, ""
    if not rows:
        return None, ""
    return str(rows[0].get("日期") or "")[:10], _s(rows[0].get("账号"))


def _fixes(carrier, period, subject):
    """复核台登记的计提更正 → {vno: [fix]}。"""
    with db._engine.connect() as c:
        rows = [dict(r) for r in c.execute(select(FX).where((FX.c.carrier == carrier) & (FX.c.period == period))).mappings().all()]
    out = {}
    for f in rows:
        try:
            snap = json.loads(f.get("snap_json") or "{}")
        except Exception:
            snap = {}
        if snap.get("subject") != subject:
            continue
        vno = str(snap.get("vno") or "").split("-")[-1]
        out.setdefault(vno, []).append({**{k: f.get(k) or "" for k in ("to_acct", "to_fee", "to_dept", "to_biz", "to_proj",
                                                                         "to_amt_tax", "to_rate", "to_amt", "memo")}, "snap": snap,
                                        "id": f.get("id")})     # id：写入金蝶时记下合进了哪几笔更正（首页待办区销账用）
    return out


@router.get("/api/logistics-voucher/preview")
def preview(request: Request, inst: str):
    if not _perm(request):
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    d, code = _preview_data(inst)
    return d if code == 200 else JSONResponse(d, status_code=code)


def _kind(vouchers, notes, pl):
    """一句话做账类型(列表用)：hx 一致·只核销 / tail 核销含尾差 / redo 需红冲更正 / subj 计提记错主体 / manual 金额不符 / noacc 没有计提。"""
    subj = next((n for n in notes if "主体记错" in n), "")
    if subj:
        if pl["status"] != "ok" and any(v.get("from") for v in vouchers):     # 想在本张里补提，但税率/金额还有别的问题
            return "subj", subj.split("：本张凭证里")[0] + "；另外" + ((pl["msgs"] or ["要人工处理"])[0])
        return "subj", subj
    if not vouchers:
        return "noacc", "金蝶本期没找到这家的计提凭证"
    if pl["status"] != "ok":
        return "manual", (pl["msgs"] or ["要人工处理"])[0]
    redo = [(k, p) for k, p in pl["per"].items() if p.get("mode") in ("rate", "fix")]
    tails = [(k, p) for k, p in pl["per"].items() if p.get("mode") == "tail"]
    if redo:
        return "redo", "需红冲更正 %d 张：%s" % (len(redo), "；".join("记-%s %s" % (k, p.get("why") or "") for k, p in redo))
    if tails:
        return "tail", "计提与发票一致，尾差 %s：%s 整笔红冲后更正" % ("、".join("%.2f" % d for d in pl["tails"].values()),
                                                     "、".join("记-%s" % k for k, _ in tails))
    return "hx", "计提与发票一致，只核销"


def _preview_data(inst, self_vno=None):
    with db._engine.connect() as c:
        r = c.execute(select(PR).where(PR.c.inst_id == inst)).mappings().first()
    if not r:
        return {"ok": False, "msg": "没有这张请款单"}, 404
    r = dict(r)
    if not r.get("period"):
        return {"ok": False, "msg": "这张请款单还没认出归哪个月（到账单核对总表认领）"}, 400
    folder, invs = _invoices(inst)
    ovr = db.get_setting(_OVR_KEY, None) or {}
    try:
        vouchers, notes = _accruals(r["subject_full"], r["period"], r["sup_code"])
        vouchers, notes = list(vouchers), list(notes)      # 缓存里的列表别被下面改到
    except Exception as e:
        return {"ok": False, "msg": "读金蝶计提凭证失败：%s" % str(e)[:160]}, 502
    # 同一家同月有几张请款单(或计提多记了一张)：计提比发票多时，挑出含税合计正好＝发票的那几张，其余不在这次请款里
    inv_tot = round(sum(i["gross"] for i in invs), 2)
    if vouchers and invs and sum(v["gross"] for v in vouchers) - inv_tot > 0.004:
        pick = LV._subset(vouchers, inv_tot)
        if pick:
            rest = [v for v in vouchers if v not in pick]
            notes.append("本期这家还有 %s 不在这次请款里（含税合计正好对上发票的是另外几张）" %
                         "、".join("记-%s %.2f" % (v["vno"], v["gross"]) for v in rest))
            vouchers = pick
    # 本账簿没有/对不上：去另外两个主体的账上找，计提可能记错了主体(实证 丰源 深圳星期九 918.93 记在深圳星期零 记-390)
    # V2.798(用户 2026-10-05「这得出两张了，一张给星期零做账，一张给星期九做账」)：不再只提示——那边的计提拿过来，在本张凭证里补提到本主体
    #   再核销、支付(mode=move)；原主体的红冲 V2.798 先不写，V2.799(用户 2026-10-05「也是系统做星期零」)改成系统在那边账簿新建红冲凭证并提交，见 _post_xred。
    xbook = []
    own_g = round(sum(v["gross"] for v in vouchers), 2)
    if invs and abs(own_g - inv_tot) > 0.004:
        need = round(inv_tot - own_g, 2) if own_g < inv_tot else inv_tot      # 本主体已有一部分计提的，只找缺的那部分
        for o in db.list_orgs() or []:
            ob = o.get("full_name")
            if not ob or ob == r["subject_full"]:
                continue
            try:
                ov, _ = _accruals(ob, r["period"], r["sup_code"])
            except Exception:
                continue
            ov = list(ov)
            claim = _own_claim(ob, r["period"], r["sup_code"]) if ov else 0
            used = (LV._subset(ov, claim) or []) if claim > 0.004 else []       # 那个主体自己请款要用的计提先留给它
            cand = [v for v in ov if v not in used]
            hit = LV._subset(cand, need) if cand else None
            if not hit:
                continue
            short = o.get("short_name") or ob
            where = "计提记在了「%s」的账上（%s），主体记错" % (short, "、".join("记-%s %.2f" % (v["vno"], v["gross"]) for v in hit))
            why, mv = "", []
            if own_g > inv_tot:
                why = "本主体自己的计提也对不上发票"
            elif {v["vno"] for v in hit} & {v["vno"] for v in vouchers}:
                why = "两边凭证号正好相同，系统分不开"
            for v in (hit if not why else []):
                exps = []
                for l in v["exp_lines"]:
                    h = _here_dims(r["subject_full"], v["year"], v["month"], l.get("fee"), l.get("biz_code"))
                    if not h:
                        why = "本主体账簿里没找到「%s」平时挂哪个部门" % (l.get("fee") or l.get("acct_name") or "这类费用")
                        break
                    exps.append(dict(l, **h))
                if why:
                    break
                mv.append(dict(v, exp_orig=v["exp_lines"], exp_lines=exps, **{"from": {"short": short, "full": ob}}))
            if why:
                notes.append("%s：%s，要先在那边红冲、在本主体重新计提，再做这张" % (where, why))
            else:
                vouchers = vouchers + mv
                notes.append("%s：本张凭证里补提到%s再核销；%s那边的红冲凭证系统一并建好并提交（不审核）" % (where, r.get("subject") or "本主体", short))
                xbook = [(ob, short, v) for v in mv]
            break
    fixes = _fixes(r["carrier"], r["period"], r["subject"])
    fixes = {k: v for k, v in fixes.items() if any(x["vno"] == k and not x.get("from") for x in vouchers)}
    # 不能抵扣的票按 0 税率、0 税额参与核对：计提本就全额进费用的直接核销；计提分了税的要红冲更正到 0%
    inv_in = [{"number": i["number"], "rate": i["rate"], "gross": i["gross"], "tax": i["tax"]} if i["deduct"] else
              {"number": i["number"], "rate": 0, "gross": i["gross"], "tax": 0.0, "deduct": False} for i in invs]
    nd = [i for i in invs if not i["deduct"]]
    if nd:
        notes.append("%s 不能抵扣（%s），按含税全额进费用核对、不出待认证行，号码写进支付摘要" %
                     ("、".join(i["number"] or "无号码" for i in nd), "、".join(dict.fromkeys(i["type"] for i in nd))))
    pl = LV.plan(vouchers, inv_in, fixes) if (vouchers and inv_in) else \
        {"status": "manual", "msgs": ["金蝶本期没找到这家的计提凭证" if not vouchers else "票夹里还没有发票"], "per": {}, "tails": {}}
    pi = _paid_info(r) or {}
    pay_date, bank = _bank_of(pi.get("bill_id")) if pi.get("bill_id") else (pi.get("date"), "")
    pay_date = pay_date or pi.get("date") or datetime.now().strftime("%Y-%m-%d")
    ctx = {"supplier": r.get("payee") or "", "applicant": r.get("applicant") or "", "pay_year": int(pay_date[:4]),
           "pay_month": int(pay_date[5:7]), "pay_amount": float(r.get("amount") or 0), "bank": bank,
           "paid": bool(pi) and not pi.get("voucher"), "self_vno": self_vno}
    lines = LV.build(ctx, vouchers, inv_in, pl, fixes) if pl["status"] == "ok" else []
    dr, cr = LV.balance(lines)
    msgs = list(notes) + list(pl["msgs"])
    if pi.get("voucher"):
        msgs.append("这笔付款已经在金蝶 %s 记过支付凭证，本张不再出支付分录" % pi["voucher"])
    if not pi:
        msgs.append("还没付款（金蝶没有付款单）：先出红冲/更正/核销预览，付款后才加支付分录")
    if pi.get("bill_id") and not bank:
        msgs.append("金蝶付款单没取到我方银行账号，银行存款那行的账号待补")
    st = "booked" if inst in (db.get_setting(_POSTED_KEY, None) or {}) else _status(r, folder, invs, ovr)
    kind, ktext = _kind(vouchers, notes, pl)
    # 计提调整单(V2.759，用户 2026-10-02「审核的时候就出来，打印后贴在钉钉单据后面」)：每张红冲更正的计提，原计提 vs 更正后
    adjust = []
    for v in (vouchers if lines else []):
        p = pl["per"].get(v["vno"]) or {}
        if p.get("mode") not in ("rate", "fix", "tail", "move"):
            continue
        e = LV.fix_expl(v, p, ctx["pay_year"])
        nl = [l for l in lines if l["block"] == "更正" and l["expl"] == e]
        exp_old = [{k: l.get(k) for k in ("acct", "acct_name", "dr") + LV.EXP_DIMS} for l in (v.get("exp_orig") or v["exp_lines"])]
        exp_new = [dict(l["dims"], acct=l["acct"], acct_name=l["acct_name"], dr=l["dr"]) for l in nl if str(l["acct"])[:1] in ("5", "6")]
        ng = sum(l["cr"] for l in nl if l["acct"] == "2241.02")
        nt = sum(l["dr"] for l in nl if l["acct"] == "2221.01.07")
        adjust.append({"ref": LV.ref_of(v, ctx["pay_year"]), "vno": v["vno"], "year": v["year"], "month": v["month"], "expl": v["expl"],
                       "mode": p["mode"], "why": p.get("why") or "", "from": (v.get("from") or {}).get("short") or "",
                       "from_full": (v.get("from") or {}).get("full") or "",
                       "old": {"gross": v["gross"], "net": v["net"], "tax": v["tax"], "rate": v["rate"], "exp": exp_old},
                       "new": {"gross": LV.r2(ng), "tax": LV.r2(nt), "net": LV.r2(ng - nt),
                               "rate": v["rate"] if p["mode"] == "tail" else p.get("new_rate"), "exp": exp_new}})
    # 主体更正：原主体那边要做的红冲分录 + 那边做了没有(读金蝶)
    xb = []
    xdone = ((db.get_setting(_POSTED_KEY, None) or {}).get(inst) or {}).get("xred") or {}
    for ob, short, v in (xbook if lines else []):
        rv = _reversed_in(ob, r["sup_code"], v)
        mine = xdone.get("%s|%s" % (ob, v["vno"]))             # 系统建的那张(保存即有记-号)；金蝶查询一时查不到也认它
        if mine and not rv:                                    # 金蝶查不到：确认那张还在(可能被人删了)，不在就当没做
            try:
                if not kc.view_voucher(mine.get("vid")).get("exists"):
                    mine = None
            except Exception:
                pass
        if mine and (not rv or rv.get("vno") == mine.get("vno")):
            rv = {"vno": mine.get("vno"), "year": mine.get("year"), "month": mine.get("month"), "sys": True, "vid": mine.get("vid"),
                  "by": mine.get("by"), "at": mine.get("at"), "submitted": mine.get("submitted")}
        xb.append({"book": ob, "short": short, "vno": v["vno"], "year": v["year"], "month": v["month"], "ref": LV.ref_of(v, ctx["pay_year"]),
                   "expl": v["expl"], "gross": v["gross"], "net": v["net"], "tax": v["tax"],
                   "lines": LV.red_lines(v, ctx["pay_year"]), "reversed": rv})
    return {"ok": True, "kind": kind, "kind_text": ktext, "xbook": xb, "req": {"inst": inst, "bid": r.get("business_id"), "carrier": r.get("carrier"), "payee": r.get("payee"),
                                "code": r.get("sup_code"), "subject": r.get("subject"), "subject_full": r.get("subject_full"),
                                "amount": r.get("amount"), "period": r.get("period"), "applicant": r.get("applicant"),
                                "paid": pay_date if pi else "", "bank": bank, "folder": folder["id"] if folder else None,
                                "paper_ovr": ovr.get(inst), "status": st, "posted": (db.get_setting(_POSTED_KEY, None) or {}).get(inst),
                                "bill_id": pi.get("bill_id") or ""},
            "invoices": invs,
            "accruals": [{"vno": v["vno"], "month": v["month"], "expl": v["expl"], "gross": v["gross"], "net": v["net"], "tax": v["tax"], "rate": v["rate"],
                          "from": (v.get("from") or {}).get("short") or "",
                          "fee": "、".join(dict.fromkeys(l.get("fee") or l.get("acct_name") or "" for l in v["exp_lines"])),
                          "biz": "、".join(dict.fromkeys(l.get("biz") for l in v["exp_lines"] if l.get("biz"))),
                          "tail": pl["tails"].get(v["vno"]),
                          **(pl["per"].get(v["vno"]) or {"mode": "", "new_rate": None, "why": ""})} for v in vouchers],
            "plan": {"status": pl["status"], "msgs": msgs, "tails": pl["tails"]},
            "adjust": adjust,
            "voucher": {"date": pay_date, "book": r.get("subject_full"), "lines": lines, "dr": dr, "cr": cr}}, 200

@router.post("/api/logistics-voucher/plans")
async def plans(request: Request):
    """列表的「做账类型」列：逐张算(要读金蝶计提，按账簿×月×供应商缓存 10 分钟)，前端列表出来后再来取。"""
    if not _perm(request):
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    b = await request.json()
    from starlette.concurrency import run_in_threadpool

    def run():
        out = {}
        for inst in (b.get("insts") or [])[:80]:
            try:
                d, code = _preview_data(str(inst))
                if code == 200:
                    out[inst] = {"kind": d.get("kind"), "text": d.get("kind_text"), "auto": (d.get("plan") or {}).get("status") == "ok",
                                 "xrev": [{"short": x["short"], "vno": x["vno"], "reversed": x["reversed"]} for x in d.get("xbook") or []],
                                 "acc": [{k: a.get(k) for k in ("vno", "month", "fee", "biz", "tail", "expl", "gross", "rate", "mode", "new_rate", "why", "from")}
                                         for a in d.get("accruals") or []]}
                else:
                    out[inst] = {"kind": "err", "text": d.get("msg"), "acc": []}
            except Exception as e:
                out[inst] = {"kind": "err", "text": str(e)[:120]}
        return out
    return {"ok": True, "plans": await run_in_threadpool(run)}


@router.post("/api/logistics-voucher/paper-override")
async def paper_override(request: Request):
    """纸质件没到、手动放行做账(用户定：可手动放行，记谁放的)；on=false 撤销。"""
    u = _perm(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    b = await request.json()
    inst = str(b.get("inst") or "")
    ovr = dict(db.get_setting(_OVR_KEY, None) or {})
    if b.get("on"):
        ovr[inst] = {"by": u["name"], "at": _now(), "note": str(b.get("note") or "")[:100]}
    else:
        ovr.pop(inst, None)
    db.set_setting(_OVR_KEY, ovr, u["name"])
    db.audit(u["name"], "物流付款做账-纸质件" + ("手动放行" if b.get("on") else "撤销放行"), inst, str(b.get("note") or ""))
    return {"ok": True}


# ---------- 写金蝶（V2.753，用户 2026-10-02 实测通过后定）----------
# 流程(顺丰速运 960 / 深圳星期零 记-260 实证)：付款单 暂存→Save(只传 FID，生成单号，字段不变)→Submit→Audit
#   （用户授权：物流付款单由系统提交审核，是确认书 D11「审核留给人」的例外，只限物流付款单）→ 金蝶约 5 秒自动生成付款凭证(状态 A)
#   → 读回这张凭证 → Save(IsDeleteEntry=False + NeedUpDateFields) 加红冲/更正/核销分录、支付两行改摘要。
#   金蝶允许改自动凭证；新加的行排在支付行后面(行号不随顺序变)，用户同意「支付在前」。补完分录提交凭证(V2.758)，凭证不审核，留给人在金蝶审。
_AUDIT_SVC = "Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.Audit.common.kdsvc"
_POST_LOCK = {}
_KD_BASE = {"FCURRENCYID": {"FNumber": "PRE001"}, "FEXCHANGERATETYPE": {"FNumber": "HLTX01_SYS"}, "FEXCHANGERATE": 1.0}
ST_CN = {"ready": "可做账", "paper": "纸质件未到", "invdiff": "发票≠请款", "noinv": "票不齐", "unpaid": "未付款", "booked": "已做账"}


def _kd_ok(res):
    st = (res or {}).get("Result", {}).get("ResponseStatus") or {}
    if st.get("IsSuccess"):
        return ""
    return "；".join(str(e.get("Message") or "") for e in (st.get("Errors") or [])) or json.dumps(st, ensure_ascii=False)[:200]


def _kd_dims(l):
    x, dd = l.get("dims") or {}, {}
    for k, f in (("sup_code", "FDETAILID__FFLEX4"), ("dept_code", "FDETAILID__FFLEX5"), ("fee_code", "FDETAILID__FFLEX9"),
                 ("biz_code", "FDETAILID__FF100010"), ("proj_code", "FDETAILID__FF100006"), ("sup_grp", "FDETAILID__FF100005")):
        if x.get(k):
            dd[f] = {"FNumber": x[k]}
    return dd


def _post(inst, user):
    from datetime import datetime as _dt, timedelta
    d, code = _preview_data(inst)
    if code != 200:
        return {"ok": False, "msg": d.get("msg")}
    req = d["req"]
    if req.get("posted"):
        return {"ok": False, "msg": "这张已经写过金蝶：记-%s" % req["posted"].get("vno")}
    if d["plan"]["status"] != "ok":
        return {"ok": False, "msg": "计提和发票对不上，不能写金蝶"}
    if req["status"] != "ready":
        return {"ok": False, "msg": "还不能做账（%s）" % (ST_CN.get(req["status"]) or req["status"])}
    if not str(req.get("bill_id") or "").isdigit():
        return {"ok": False, "msg": "没有金蝶付款单（或付款已经记过支付凭证），这一版只支持从付款单出凭证"}
    fid = int(req["bill_id"])
    s, conf = kc.login()
    F = [("FBillNo", "单号"), ("FDOCUMENTSTATUS", "状态"), ("FPAYTOTALAMOUNTFOR", "金额"), ("FCONTACTUNIT.FNumber", "码"), ("FDate", "日期")]
    pb = (kc._query(s, conf, "AP_PAYBILL", F, "FID=%d" % fid) or [None])[0]
    if not pb or pb["码"] != req["code"] or abs(float(pb["金额"] or 0) - float(req["amount"] or 0)) >= 0.01:
        return {"ok": False, "msg": "金蝶付款单和请款单对不上（供应商或金额），没动"}
    steps = []
    if pb["状态"] == "C":
        return {"ok": False, "msg": "付款单 %s 已经被人审核过，金蝶已出过凭证；这一版不去改别人生成的凭证，请人工处理" % pb["单号"]}
    if pb["状态"] == "Z" or not str(pb["单号"] or "").strip():
        e = _kd_ok(kc._post(s, conf, kc.SAVE_SVC, ["AP_PAYBILL", json.dumps({"IsDeleteEntry": False, "Model": {"FID": fid}})]).json())
        if e:
            return {"ok": False, "msg": "付款单保存失败：" + e}
        steps.append("付款单保存")
    pb = kc._query(s, conf, "AP_PAYBILL", F, "FID=%d" % fid)[0]
    if pb["状态"] in ("A", "D"):
        e = _kd_ok(kc._post(s, conf, kc.SUBMIT_SVC, ["AP_PAYBILL", json.dumps({"Ids": str(fid)})]).json())
        if e:
            return {"ok": False, "msg": "付款单提交失败：" + e, "steps": steps}
        steps.append("付款单提交")
    t0 = _dt.now() - timedelta(seconds=30)
    e = _kd_ok(kc._post(s, conf, _AUDIT_SVC, ["AP_PAYBILL", json.dumps({"Ids": str(fid)})]).json())
    if e:
        return {"ok": False, "msg": "付款单审核失败：" + e, "steps": steps}
    steps.append("付款单审核 %s" % pb["单号"])
    db.audit(user, "物流付款做账-系统审核付款单", pb["单号"], "%s %s %.2f" % (req["subject"], req["payee"], float(req["amount"] or 0)))
    # 找金蝶自动生成的付款凭证：同账簿、同付款日、贷 1002 = 付款金额、审核之后生成、状态 A
    book = req["subject_full"]
    flt = ("FACCOUNTBOOKID.FName='" + book + "' and FDate='" + str(pb["日期"])[:10] + "' and FAccountID.FNumber like '1002%' and FCREDIT="
           + ("%.2f" % float(pb["金额"])) + " and FCreateDate>='" + t0.strftime("%Y-%m-%d %H:%M:%S") + "'")
    vid = None
    for _ in range(12):
        time.sleep(2.5)
        rows = [r for r in kc._query(s, conf, "GL_VOUCHER", [("FVOUCHERID", "id"), ("FDOCUMENTSTATUS", "状态")], flt) if r["状态"] in ("A", "Z")]
        if rows:
            vid = rows[-1]["id"]
            break
    if not vid:
        return {"ok": False, "msg": "付款单已审核，但 30 秒内没等到金蝶生成付款凭证，请到金蝶看一下", "steps": steps}
    m = kc._post(s, conf, kc.VIEW_SVC, ["GL_VOUCHER", json.dumps({"Id": str(vid)})]).json()["Result"]["Result"]
    ents = [x for x in m.get("GL_VOUCHERENTRY") or [] if x.get("FACCOUNTID") and x.get("Id")]
    vno = str(m.get("VOUCHERGROUPNO") or "")
    if m.get("DocumentStatus") not in ("A", "Z") or len(ents) != 2:
        return {"ok": False, "msg": "金蝶付款凭证 记-%s 状态/分录不是预期(状态 %s，%d 行)，没动" % (vno, m.get("DocumentStatus"), len(ents)), "steps": steps}
    # 有了凭证号，核销摘要里引用本凭证的 □ 直接填上
    d2, _ = _preview_data(inst, self_vno=vno)
    lines = d2["voucher"]["lines"]
    add = [l for l in lines if l["block"] in ("红冲", "更正", "核销")]
    pay_expl = next(l["expl"] for l in lines if l["block"] == "支付")
    new = []
    for l in add:
        x = dict(_KD_BASE, FEXPLANATION=l["expl"], FACCOUNTID={"FNumber": l["acct"]}, FDEBIT=l["dr"], FCREDIT=l["cr"])
        dd = _kd_dims(l)
        if dd:
            x["FDetailID"] = dd
        new.append(x)
    body = {"IsDeleteEntry": False, "NeedUpDateFields": ["FEntity", "FEXPLANATION", "FACCOUNTID", "FDEBIT", "FCREDIT", "FDetailID",
                                                         "FCURRENCYID", "FEXCHANGERATETYPE", "FEXCHANGERATE"],
            "Model": {"FVOUCHERID": vid, "FEntity": new + [{"FEntryID": x["Id"], "FEXPLANATION": pay_expl} for x in ents]}}
    e = _kd_ok(kc._post(s, conf, kc.SAVE_SVC, ["GL_VOUCHER", json.dumps(body, ensure_ascii=False)]).json())
    if e:
        return {"ok": False, "msg": "付款单已审核、凭证 记-%s 已生成，但补分录失败：%s（凭证还是金蝶原样，可人工补）" % (vno, e), "steps": steps, "vno": vno}
    # 补完分录就提交凭证(用户 2026-10-02：公司凭证做完都提交，进审核人的待审列表；审核仍留给人)
    sub_err = _kd_ok(kc._post(s, conf, kc.SUBMIT_SVC, ["GL_VOUCHER", json.dumps({"Ids": str(vid)})]).json())
    m2 = kc._post(s, conf, kc.VIEW_SVC, ["GL_VOUCHER", json.dumps({"Id": str(vid)})]).json()["Result"]["Result"]
    rec = {"bill_no": pb["单号"], "vid": vid, "vno": vno, "book": book, "at": _now(), "by": user,
           "n_adjust": len(d2.get("adjust") or []),          # 有几笔计提更正(装订时要附更正单；扫码查凭证用)
           "dr": m2.get("DEBITTOTAL"), "cr": m2.get("FCREDITTOTAL"), "lines": len(new) + 2,
           "submitted": not sub_err, "submit_err": sub_err, "status": m2.get("DocumentStatus")}
    # 这张凭证合进了复核台登记的哪几笔计提更正（与 _preview_data 同口径：只算挂在这次用到的计提凭证上的）
    try:
        used = {a["vno"] for a in d2.get("accruals") or [] if not a.get("from")}      # 别的主体拿过来补提的(V2.798)不算：凭证号是那边账簿的
        rec["fix_ids"] = [x["id"] for k, v in _fixes(req.get("carrier"), req.get("period"), req.get("subject")).items()
                          if k in used for x in v if x.get("id")]
    except Exception:
        rec["fix_ids"] = []          # 凭证已经写进金蝶了，下面的落记录绝不能被这一步拦住
    posted = dict(db.get_setting(_POSTED_KEY, None) or {})
    posted[inst] = rec
    db.set_setting(_POSTED_KEY, posted, user)
    with db._engine.begin() as c:
        c.execute(text("update logistics_payreq set kd_paid=:v where inst_id=:i"), {"v": "%s|C|%s" % (str(pb["日期"])[:10], fid), "i": inst})
    db.audit(user, "物流付款做账-写入金蝶凭证", "记-%s" % vno, "%s 付款单 %s；补 %d 行；借 %s 贷 %s" % (book, pb["单号"], len(new), rec["dr"], rec["cr"]))
    steps.append("凭证 记-%s 补 %d 行、改支付摘要" % (vno, len(new)))
    steps.append("凭证已提交，等人审核" if not sub_err else "凭证提交失败：%s（凭证已保存，可在金蝶手动提交）" % sub_err)
    # 首页待办区：提交成功 → 给付款凭证审核人记一笔；没提交上 → 挂回做账人。合进凭证的计提更正顺带销账。失败只留痕，不拦。
    todo_scenes.voucher_touch(inst, rec, req, user)
    todo_scenes.fix_touch(req.get("carrier"), req.get("period"), user)
    if d2.get("xbook"):                        # 主体更正：到原主体账簿建红冲凭证(本主体这张已经写好了，那边建不成只提醒、可重试，不回滚)
        for x in d2["xbook"]:
            db.audit(user, "物流付款做账-主体更正", "记-%s" % vno, "%s 记-%s %.2f 补提到 %s" % (x["short"], x["vno"], x["gross"], req["subject"]))
        try:
            steps.extend(_post_xred(inst, user, d2, s, conf))
        except Exception as e:
            steps.append("⚠ 原主体的红冲凭证没建成：%s（本张已写好；可在预览里点「补做红冲」重试）" % str(e)[:160])
    return {"ok": True, "vno": vno, "bill_no": pb["单号"], "steps": steps, "dr": rec["dr"], "cr": rec["cr"]}


def _xred_date(book, pay_date, s, conf):
    """红冲凭证记哪天：和本主体那张付款凭证同一天；那边账簿这个月已经结账了，就记到它当前期间(今天在当前期间用今天，否则当期最后一天)。"""
    import calendar
    py, pm = int(pay_date[:4]), int(pay_date[5:7])
    try:
        bk = kc._query(s, conf, "BD_AccountBook", [("FCurrentYear", "年"), ("FCurrentPeriod", "期")], "FName='%s'" % book.replace("'", ""))
        cy, cm = int(bk[0]["年"]), int(bk[0]["期"])
    except Exception:
        return pay_date, py, pm
    if (py, pm) >= (cy, cm):
        return pay_date, py, pm
    today = datetime.now()
    if (today.year, today.month) == (cy, cm):
        return today.strftime("%Y-%m-%d"), cy, cm
    return "%04d-%02d-%02d" % (cy, cm, calendar.monthrange(cy, cm)[1]), cy, cm


def _post_xred(inst, user, d=None, s=None, conf=None):
    """主体更正(V2.799，用户 2026-10-05「也是系统做星期零」)：到原主体账簿新建一张红冲凭证(原计提分录全额取负)并提交，不审核。
    建凭证的写法沿用物流计提一键录入验证过的配方(账簿/凭证字/分录在前、FDate 在后)。防重复：那边已有红冲(金蝶查得到，或本系统建过且还在)就跳过。
    只在本主体那张付款凭证写入之后做；本主体那张不受这一步成败影响。→ [步骤文字]"""
    if d is None:
        d, code = _preview_data(inst)
        if code != 200:
            raise RuntimeError(d.get("msg") or "读不到这张请款单")
    posted = dict(db.get_setting(_POSTED_KEY, None) or {})
    rec = dict(posted.get(inst) or {})
    if not rec:
        raise RuntimeError("本主体这张付款凭证还没写金蝶，先保存到金蝶")
    if s is None:
        s, conf = kc.login()
    b2c = {o.get("full_name"): o.get("book_code") for o in (db.list_orgs() or [])}
    xred, steps = dict(rec.get("xred") or {}), []
    for x in d.get("xbook") or []:
        key = "%s|%s" % (x["book"], x["vno"])
        old = xred.get(key)
        if old and kc.view_voucher(old.get("vid"), s, conf).get("exists"):
            steps.append("%s 红冲凭证 记-%s 已建过，没重复建" % (x["short"], old.get("vno")))
            continue
        rv = x.get("reversed")
        if rv and not rv.get("sys"):
            steps.append("%s 记-%s 已经有人红冲过（%s 月 记-%s），没重复建" % (x["short"], x["vno"], rv.get("month"), rv.get("vno")))
            continue
        code = b2c.get(x["book"])
        if not code:
            raise RuntimeError("主体档案里没有「%s」的账簿编码" % x["short"])
        date, y, m = _xred_date(x["book"], d["voucher"]["date"], s, conf)
        ents = []
        for l in x["lines"]:
            e = dict(_KD_BASE, FEXPLANATION=l["expl"], FACCOUNTID={"FNumber": l["acct"]}, FDEBIT=l["dr"], FCREDIT=l["cr"])
            dd = _kd_dims(l)
            if dd:
                e["FDetailID"] = dd
            ents.append(e)
        if abs(sum(l["dr"] for l in x["lines"]) - sum(l["cr"] for l in x["lines"])) >= 0.005:
            raise RuntimeError("%s 记-%s 的红冲分录借贷不平，没建" % (x["short"], x["vno"]))
        model = {"FACCOUNTBOOKID": {"FNumber": code}, "FVOUCHERGROUPID": {"FNumber": "PRE001"}, "FEntity": ents,
                 "FDate": date, "FYear": y, "FPeriod": m}
        r = kc.save_voucher(model, s, conf)
        info = kc.view_voucher(r["id"], s, conf)
        sub_err = ""
        try:
            kc.submit_bill("GL_VOUCHER", r["id"], s, conf)
        except Exception as e:
            sub_err = str(e)[:160]
        xred[key] = {"vid": r["id"], "vno": info.get("vno") or "", "billno": r.get("billno"), "book": x["book"], "short": x["short"],
                     "src_vno": x["vno"], "year": y, "month": m, "date": date, "gross": x["gross"], "at": _now(), "by": user,
                     "submitted": not sub_err, "submit_err": sub_err}
        rec["xred"] = xred
        posted[inst] = rec
        db.set_setting(_POSTED_KEY, posted, user)              # 建一张存一张，后面的失败不丢前面的记录
        db.audit(user, "物流付款做账-原主体红冲凭证", "%s 记-%s" % (x["short"], xred[key]["vno"]),
                 "红冲 %s %.2f，日期 %s；%s" % (x["ref"], x["gross"], date, "已提交" if not sub_err else "提交失败：" + sub_err))
        steps.append("%s 红冲凭证 记-%s 已建（%s，冲 %s %.2f）%s" % (x["short"], xred[key]["vno"], date, x["ref"], x["gross"],
                                                        "、已提交，等人审核" if not sub_err else "，但提交失败：%s（可在金蝶手动提交）" % sub_err))
    return steps


# ---------- 扫付款单二维码查凭证（V2.801，用户 2026-10-05「扫描那个付款单二维码，就知道是什么凭证、哪个主体」）----------
# 纸质付款单右上角的二维码是钉钉审批单链接：解析出审批实例号 → 请款单 → 做账记录里的凭证号。只读，不建票夹、不拉附件。
# 不是物流请款单的，退一步看发票管家票夹里同步到的凭证号(那边定时从金蝶凭证摘要里认发票号)。
_SCAN_CACHE = {}


def _scan_lookup(code):
    from kernels import invoice_parse as ip, invoice_dingtalk as idt
    c = ip.classify_code(code or "")
    iid = ""
    if c["kind"] == "approval_link":
        iid = _SCAN_CACHE.get(c["value"]) or ""
        if not iid:
            rl = idt.resolve_link(c["value"])
            if not rl.get("ok"):
                return {"ok": False, "msg": rl.get("msg") or "这个二维码没解析出审批单"}
            iid = _SCAN_CACHE[c["value"]] = rl["procInstId"]
    elif c["kind"] == "business_id":
        with db._engine.connect() as cx:
            row = cx.execute(select(PR.c.inst_id).where(PR.c.business_id == c["value"])).first()
            if not row:
                row = cx.execute(text("select inst_id from inv_folder where business_id=:b order by id desc limit 1"), {"b": c["value"]}).first()
        iid = (row[0] if row else "") or ""
        if not iid:
            return {"ok": False, "msg": "系统里没有审批编号 %s 的单子（不是物流请款单，发票管家也没收过它的票）" % c["value"]}
    elif c["kind"] == "invoice_qr":
        return {"ok": False, "msg": "这是发票上的二维码：请扫付款单（审批单）右上角那个"}
    else:
        return {"ok": False, "msg": "没认出这个码：请扫付款单右上角的二维码，或输入 20 位审批编号"}
    with db._engine.connect() as cx:
        r = cx.execute(select(PR).where(PR.c.inst_id == iid)).mappings().first()
    f2s = {o.get("full_name"): o.get("short_name") for o in (db.list_orgs() or [])}
    folder, invs = _invoices(iid)
    synced = []                                   # 发票管家同步到的凭证：[{book, period, number}]
    for i in invs:
        for v in i.get("vouchers") or []:
            if isinstance(v, dict) and v.get("number"):
                k = {"subject": f2s.get(v.get("book")) or v.get("book") or "", "month": str(v.get("period") or "")[:7],
                     "vno": str(v["number"]).replace("记-", "").replace("记", ""), "what": "金蝶凭证", "src": "金蝶已有"}
                if k not in synced:
                    synced.append(k)
    if not r:
        if not folder:
            return {"ok": False, "msg": "系统里没有这张审批单：不是物流请款单，发票管家也没收过它的票"}
        return {"ok": True, "inst": iid, "kind": "发票管家票夹 #%s" % folder["id"], "subject": (synced[0]["subject"] if synced else ""),
                "payee": next((i["seller"] for i in invs if i.get("seller")), ""), "amount": round(sum(i["gross"] for i in invs), 2),
                "vouchers": synced, "state": "" if synced else "发票管家里这张单的发票还没被金蝶凭证引用（还没做账，或还没同步到）"}
    r = dict(r)
    pi = _paid_info(r) or {}
    posted = (db.get_setting(_POSTED_KEY, None) or {}).get(iid)
    vs = []
    if posted:
        vs.append({"subject": r.get("subject"), "month": str(pi.get("date") or posted.get("at") or "")[:7], "vno": posted.get("vno"),
                   "what": "付款凭证", "src": "系统写入", "bill_no": posted.get("bill_no")})
        for x in (posted.get("xred") or {}).values():
            vs.append({"subject": x.get("short"), "month": "%s-%02d" % (x.get("year"), int(x.get("month") or 0)), "vno": x.get("vno"),
                       "what": "红冲凭证（没有纸质付款单，只附计提更正单 ①）", "src": "系统写入"})
    else:
        if pi.get("voucher"):
            vs.append({"subject": r.get("subject"), "month": str(pi.get("date") or "")[:7], "vno": str(pi["voucher"]).replace("记-", ""),
                       "what": "支付凭证", "src": "金蝶已有"})
        vs.extend(v for v in synced if not any(v["vno"] == o["vno"] and v["subject"] == o["subject"] for o in vs))
    try:
        from routers.logistics_payreq import _kd_suppliers
        full = (_kd_suppliers().get("code2name") or {}).get(r.get("sup_code"))
    except Exception:
        full = None
    st = "booked" if posted else _status(r, folder, invs, {})
    return {"ok": True, "inst": iid, "kind": "物流请款单", "bid": r.get("business_id"), "subject": r.get("subject"),
            "payee": full or r.get("payee") or r.get("carrier"), "code": r.get("sup_code"), "amount": r.get("amount"), "period": r.get("period") or "",
            "applicant": r.get("applicant"), "paid": pi.get("date") or "", "vouchers": vs,
            "n_adjust": (posted or {}).get("n_adjust"), "has_xred": bool((posted or {}).get("xred")),
            "state": "" if vs else "这张还没做账（%s）" % (ST_CN.get(st) or st)}


@router.post("/api/logistics-voucher/scan")
async def scan_lookup(request: Request):
    """扫付款单右上角的二维码(或输审批编号) → 哪个主体、哪张凭证。只读。"""
    if not _perm(request):
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    b = await request.json()
    from starlette.concurrency import run_in_threadpool
    try:
        return await run_in_threadpool(_scan_lookup, str(b.get("code") or "")[:600])
    except Exception as e:
        return {"ok": False, "msg": "查询出错：%s" % str(e)[:160]}


@router.get("/api/logistics-voucher/dd-config")
async def scan_dd_config(request: Request, url: str = ""):
    """手机页在钉钉里调「扫一扫」前的 dd.config 签名(V2.803，用户「扫描，而不是拍照」)。只给本站页面签，沿用发票管家那套签名和企业编号。"""
    if not _perm(request):
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    from urllib.parse import urlsplit
    from kernels import invoice_dingtalk as idt
    try:
        parts = urlsplit(url[:500])
    except ValueError:
        parts = None
    if not parts or parts.scheme not in ("http", "https") or (parts.hostname or "").lower() != (request.url.hostname or "").lower():
        return {"ok": False, "msg": "只能给本站页面做钉钉鉴权"}
    from starlette.concurrency import run_in_threadpool
    try:
        from routers.invoice import get_settings as _inv_settings
        corp = (_inv_settings() or {}).get("corpId") or ""
        return await run_in_threadpool(idt.jsapi_config, url[:500], corp)
    except Exception as e:
        return {"ok": False, "msg": "钉钉鉴权出错：%s" % str(e)[:120]}


@router.post("/api/logistics-voucher/scan-photo")
async def scan_photo(request: Request):
    """手机拍付款单右上角的二维码(V2.802，用户「手机可以吗」)：照片传上来，服务器认码再查凭证。只读。
    站点现在是 http(域名没备案)，手机浏览器不给网页直接开摄像头扫码，所以走「拍一张照片上传」——认码用发票管家认发票二维码的那套。"""
    if not _perm(request):
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    ctype = request.headers.get("content-type", "")
    if "multipart/form-data" in ctype:
        form = await request.form()
        f = form.get("file")
        data = await f.read() if f is not None and hasattr(f, "read") else b""
    else:
        data = await request.body()
    if not data:
        return {"ok": False, "msg": "没收到照片"}
    if len(data) > 15 * 1024 * 1024:
        return {"ok": False, "msg": "照片太大了（超过 15M），请重拍"}
    from starlette.concurrency import run_in_threadpool

    def run():
        from kernels import invoice_parse as ip
        qrs = ip.decode_qr_image(data)
        kinds = [(q, ip.classify_code(q)["kind"]) for q in qrs]
        link = next((q for q, k in kinds if k == "approval_link"), None)
        if not link:
            if any(k == "invoice_qr" for _, k in kinds):
                return {"ok": False, "msg": "照片里是发票的二维码：请拍付款单（审批单）右上角那个"}
            return {"ok": False, "msg": "照片里没认出付款单的二维码：对准右上角那个码，拍近一点、别反光，再试一次"}
        return _scan_lookup(link)
    try:
        return await run_in_threadpool(run)
    except Exception as e:
        return {"ok": False, "msg": "识别出错：%s" % str(e)[:160]}


@router.post("/api/logistics-voucher/post-xred")
async def post_xred(request: Request):
    """补做原主体的红冲凭证(保存到金蝶时那一步没成的重试)。已有红冲就不重复建。"""
    u = _perm(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    b = await request.json()
    inst = str(b.get("inst") or "")
    import threading
    lk = _POST_LOCK.setdefault(inst, threading.Lock())
    if not lk.acquire(blocking=False):
        return JSONResponse({"ok": False, "msg": "这张正在写金蝶，稍等"}, status_code=409)
    try:
        from starlette.concurrency import run_in_threadpool
        steps = await run_in_threadpool(_post_xred, inst, u["name"])
        r = {"ok": True, "steps": steps}
    except Exception as e:
        r = {"ok": False, "msg": "红冲凭证没建成：%s" % str(e)[:200]}
    finally:
        lk.release()
    return r if r.get("ok") else JSONResponse(r, status_code=400)


@router.post("/api/logistics-voucher/post")
async def post_to_kingdee(request: Request):
    """保存到金蝶：审核付款单 → 金蝶自动出付款凭证 → 往里补红冲/更正/核销分录、改支付摘要 → 提交凭证。凭证不审核。"""
    u = _perm(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    b = await request.json()
    inst = str(b.get("inst") or "")
    import threading
    lk = _POST_LOCK.setdefault(inst, threading.Lock())
    if not lk.acquire(blocking=False):
        return JSONResponse({"ok": False, "msg": "这张正在写金蝶，稍等"}, status_code=409)
    try:
        from starlette.concurrency import run_in_threadpool
        r = await run_in_threadpool(_post, inst, u["name"])
    except Exception as e:
        r = {"ok": False, "msg": "写金蝶出错：%s" % str(e)[:200]}
    finally:
        lk.release()
    return r if r.get("ok") else JSONResponse(r, status_code=400)
