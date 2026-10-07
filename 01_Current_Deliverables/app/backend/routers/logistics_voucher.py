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
import re
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
_FEE_KEY = "logi_voucher_fee"               # {inst_id: {vid, vno, year, month, date, book, by, at, …}} 审核时由系统生成的费用凭证(V2.835)
_LATER_KEY = "logi_voucher_later"           # {inst_id: {by, at, note}} 人工确认「发票后补，先做付款凭证」(V2.832)
_PICK_KEY = "logi_voucher_pick"             # {inst_id: {picks: [{year, month, vno}], by, at}} 人工选定这张请款单核销哪几张计提(V2.817)
_ACC_CACHE = {}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _perm(request):
    return _require_perm(request, "logistics_upload")


def _perm_scan(request):
    """扫码查凭证(只读)：能做账的可以用；装订的同事只勾「扫码查凭证」也可以用(V2.848)。"""
    return _require_perm(request, "logistics_upload") or _require_perm(request, "voucher_scan") or _require_perm(request, "enter:vbind")


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
                              "seller_name, buyer_name from inv_item where folder_id=:f and kind='invoice' and status='active' "
                              "and coalesce(review,'')<>'void' order by id"),      # 作废的票不算(V2.812：原来只排除了「移除」的，作废的还被算进发票合计)
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


_PAY_ST_CN = {"Z": "暂存", "A": "创建", "B": "审核中", "C": "已审核", "D": "重新审核"}


def _paywarn(inst):
    """这张请款单在金蝶有一张「金额、主体、收款方都对得上、但往来单位编码选错」的付款单 → {…, text} / None。扫描时写下的(logistics_payreq._pay_code_warn)。"""
    w = (db.get_setting("logi_payreq_paywarn", None) or {}).get(inst)
    if not w:
        return None
    return dict(w, text="金蝶有一张付款单（%s · %s · %s 建）金额、付款主体、收款方名称都和这张请款单对得上，但往来单位选的是「%s」，应为「%s」。"
                        "请出纳把往来单位改成「%s」再做账——照现在这样审核，付款凭证会冲到「%s」上，「%s」的计提核销不掉。" % (
                            w.get("date"), _PAY_ST_CN.get(w.get("status"), w.get("status") or ""), w.get("creator") or "—",
                            w.get("code"), w.get("want"), w.get("want"), w.get("code"), w.get("want")))


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
    laters = db.get_setting(_LATER_KEY, None) or {}
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
        # 付款单往来单位编码选错的：不算「未付款」藏起来，单列一类亮出来(做不了账，要出纳先改付款单)
        pr_ = posted.get(r["inst_id"]) or {}
        if pr_.get("tax_later"):                      # 发票后补：付款凭证做了，第三笔(暂估转待认证)做了没有
            out[-1]["later_state"] = "done" if pr_.get("later3") else ("todo" if invs else "wait")
            out[-1]["later3_vno"] = (pr_.get("later3") or {}).get("vno") or ""
            if out[-1]["later_state"] == "todo":
                out[-1]["status"] = "latertax"
        elif out[-1]["status"] == "noinv" and pi.get("bill_id") and r["inst_id"] in laters and _later_slip(r["inst_id"]):
            out[-1].update(status="ready", later=True)   # 人确认过发票后补：没票也列进可做账(只做支付)
        pw = _paywarn(r["inst_id"]) if out[-1]["status"] == "unpaid" else None
        if pw:
            out[-1].update(status="paycode", paywarn=pw)
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


_LATER_ST_CN = {"open": "待收", "partial": "部分到票", "done": "已收齐"}


def _later_slip(inst):
    """发票管家里这张请款单的后补单(没关闭的最新一张) → {id, status, status_cn, expect_date, expect_amount, received_amount, inv_kind, filed_by} / None。
    V2.833(用户 2026-10-06「要在发票后补单登记了才行」)：没票先做付款凭证，以登记了后补单为前提——只是漏传发票的不放行。只读。"""
    try:
        with db._engine.connect() as c:
            r = c.execute(text("select id, status, expect_date, expect_amount, received_amount, inv_kind, tax_rate, filed_by, applicant, note "
                               "from inv_later where inst_id=:i and coalesce(status,'open')<>'closed' order by id desc limit 1"), {"i": inst}).mappings().first()
    except Exception:
        return None
    if not r:
        return None
    r = dict(r)
    for k in ("expect_amount", "received_amount"):
        r[k] = float(r[k]) if r.get(k) is not None else None
    r["status_cn"] = _LATER_ST_CN.get(r.get("status") or "open", r.get("status") or "")
    return r


def _is_direct(z, credit):
    """不走计提、直接做的费用凭证的应付行：贷 2241 挂供应商，摘要是「××提起支付…」而不是「计提…」
    (实证 禾享 新加坡样品运费：深圳星期零 5月记-155 陈梓华、9月记-196 曾禹锡——借 6601 快递费 + 进项税 / 贷 2241.02；付款时另一张凭证「核销5/155#…」借 2241 贷银行)。
    V2.831(用户 2026-10-05「禾享不属于计提，我们应该是直接做账了」)：这种凭证和计提凭证一样可以被请款单选定核销。"""
    z = _s(z)
    return "提起支付" in z and "计提" not in z and float(credit or 0) > 0 and not any(k in z for k in ("红冲", "更正", "核销", "冲回", "冲销"))


def _accruals(book, period, sup_code):
    """本期挂该供应商的计提凭证(整张分录；直接做账的费用凭证也算，标 direct) → ([acc_voucher], 提示)。10 分钟缓存。"""
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
    direct = {_s(h["号"]) for h in heads if _is_direct(h["摘要"], h["贷"])}
    vnos = sorted({_s(h["号"]) for h in heads if "计提" in _s(h["摘要"]) and _accr_is_current(h["摘要"], period)} | direct, key=lambda x: int(x) if x.isdigit() else 0)
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
            if vno in direct:
                out[-1]["direct"] = True
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


def _siblings(r):
    """同一主体、同一家、同一账期的其他请款单(撤回/拒绝/已排除的不算) → [{inst, amount, picks}]，金额大的在前。picks＝人工给它选定的计提。只读。"""
    with db._engine.connect() as c:
        rs = c.execute(select(PR.c.inst_id, PR.c.amount, PR.c.excluded, PR.c.dt_status, PR.c.dt_result).where(
            (PR.c.subject_full == r["subject_full"]) & (PR.c.period == r["period"]) & (PR.c.sup_code == r["sup_code"]) &
            (PR.c.inst_id != r["inst_id"]))).all()
    picks = db.get_setting(_PICK_KEY, None) or {}
    out = [{"inst": i, "amount": round(float(a or 0), 2), "picks": (picks.get(i) or {}).get("picks") or []}
           for i, a, ex, st, rs_ in rs if not ex and str(st or "").upper() != "TERMINATED" and str(rs_ or "").lower() != "refuse"]
    return sorted(out, key=lambda x: -x["amount"])


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


_FIX_DIMS = (("to_acct", "科目", "acct", "acct_name"), ("to_fee", "费用项目", "fee_code", "fee"), ("to_dept", "部门", "dept_code", "dept"),
             ("to_biz", "产品分类", "biz_code", "biz"), ("to_proj", "产品项目", "proj_code", "proj"))


def _fix_changed(fx):
    """一条计提更正里真正变了的维度 → ["产品分类 CPFL002 电商", …]。登记时把没变的也选上了(和原记账一样)的不算。"""
    sn = fx.get("snap") or {}
    out = []
    for k, lb, ck, nk in _FIX_DIMS:
        v = str(fx.get(k) or "").strip()
        if v and v.split(" ", 1)[0] != str(sn.get(ck) or "").strip():
            out.append("%s %s" % (lb, v))
    return out


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
                                                                         "to_amt_tax", "to_rate", "to_amt", "memo", "split_amt")}, "snap": snap,
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
    if pl.get("pay_only") == "later":
        return "later", "发票后补：这张付款凭证只做支付（借应付 / 贷银行），暂估进项税先挂着，发票到了再单独做「暂估转待认证」"
    if pl.get("pay_only") == "tax06":
        return "hx", "费用凭证做的时候税已经挂到待认证，付款只做支付"
    if pl["status"] != "ok":
        return "manual", (pl["msgs"] or ["要人工处理"])[0]
    parts = [(k, p) for k, p in pl["per"].items() if p.get("mode") == "part"]
    if parts:
        return "part", "部分核销 %d 张：%s" % (len(parts), "；".join("记-%s %s" % (k, p.get("why") or "") for k, p in parts))
    redo = [(k, p) for k, p in pl["per"].items() if p.get("mode") in ("rate", "fix", "amt")]
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
    # V2.817(用户 2026-10-05「最好不写死代码，我们可以选择去核销哪些计提」)：人工选定了核销哪几张计提的，就用选定的——
    #   可以跨月(诚煜 6 月账单 = 6 月记-518 + 7 月记-522)，也可以从同月几张里只挑一张(迅鸽同月按项目拆成几张请款单)。
    #   选定以后不再自动挑子集、不再去别的主体账上找。
    picked = (db.get_setting(_PICK_KEY, None) or {}).get(inst)
    if picked and picked.get("picks"):
        try:
            vouchers = _picked_vouchers(r["subject_full"], r["sup_code"], picked["picks"])
        except Exception as e:
            return {"ok": False, "msg": "读人工选定的计提凭证失败：%s" % str(e)[:160]}, 502
        notes = ["人工选定核销 %s（%s %s）" % ("、".join("%d/%s#" % (v["month"], v["vno"]) for v in vouchers) or "（选的计提在金蝶里找不到了）",
                                     picked.get("by") or "", picked.get("at") or "")]
    # 同一家同月有几张请款单(或计提多记了一张)：计提比发票多时，挑出含税合计正好＝发票的那几张，其余不在这次请款里
    inv_tot = round(sum(i["gross"] for i in invs), 2)
    if not picked and vouchers and invs and sum(v["gross"] for v in vouchers) - inv_tot > 0.004:
        pick = LV._subset(vouchers, inv_tot)
        if pick:
            rest = [v for v in vouchers if v not in pick]
            notes.append("本期这家还有 %s 不在这次请款里（含税合计正好对上发票的是另外几张）" %
                         "、".join("记-%s %.2f" % (v["vno"], v["gross"]) for v in rest))
            vouchers = pick
    # V2.844(用户看迅鸽 8 月「发票 3,151.50 ≠ 计提合计 3,919.50，差 -768.00」)：同月按项目拆成几张请款单、这张的金额和计提又有差的，
    #   上面挑不出「正好＝发票」的子集，就把这家这月的计提全算到这张头上，差额虚大(真差只有 24.00：记-561 3,175.50 对发票 3,151.50；
    #   另外 744.00 的 记-562 是同月 kikiherb 那张请款单的)。先把兄弟请款单的计提让出去——人工选定的按选定，没选的按「金额正好对得上」——剩下的才是这张的。
    if not picked and invs and len(vouchers) > 1 and abs(sum(v["gross"] for v in vouchers) - inv_tot) > 0.004:
        rest, gave = list(vouchers), []
        for sib in _siblings(r):
            sp = [str(x.get("vno")) for x in sib["picks"]]
            pk = [v for v in rest if v["vno"] in sp] if sp else (LV._subset(rest, sib["amount"]) or [])
            if pk and len(pk) < len(rest):
                gave += pk
                rest = [v for v in rest if v not in pk]
        if gave and rest:
            notes.append("本期这家的 %s 是同月另一张请款单的（金额正好对上那张，或已选定给那张），不算在这次请款里" %
                         "、".join("记-%s %.2f" % (v["vno"], v["gross"]) for v in gave))
            vouchers = rest
    # 本账簿没有/对不上：去另外两个主体的账上找，计提可能记错了主体(实证 丰源 深圳星期九 918.93 记在深圳星期零 记-390)
    # V2.798(用户 2026-10-05「这得出两张了，一张给星期零做账，一张给星期九做账」)：不再只提示——那边的计提拿过来，在本张凭证里补提到本主体
    #   再核销、支付(mode=move)；原主体的红冲 V2.798 先不写，V2.799(用户 2026-10-05「也是系统做星期零」)改成系统在那边账簿新建红冲凭证并提交，见 _post_xred。
    xbook = []
    own_g = round(sum(v["gross"] for v in vouchers), 2)
    if not picked and invs and abs(own_g - inv_tot) > 0.004:
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
    # V2.818 金额有差(用户 2026-10-05「金额有差的，按照红冲处理，或者特殊的按照部分核销处理（凭证说明）」)：
    #   每张计提「应为」多少——人在「选择核销哪些计提」里填了的用填的(可选部分核销+说明)；没填、且只有一张计提的，默认按发票金额红冲更正；
    #   几张计提对一张发票的，系统定不了各该多少，仍判人工并提示去填。
    amts = {}
    if picked:
        for p in picked.get("picks") or []:
            if p.get("to_gross") not in (None, ""):
                amts[str(p["vno"])] = {"gross": float(p["to_gross"]), "part": bool(picked.get("part")), "memo": picked.get("memo") or ""}
    if not amts and inv_in and len(vouchers) == 1 and not vouchers[0].get("from"):
        v0 = vouchers[0]
        g0 = LV.eff_gross(v0, fixes.get(v0["vno"]))
        if abs(g0 - inv_tot) > 0.004 and abs(g0 - v0["gross"]) < 0.005:
            # 先看是不是跨月核销：别的月份有没用过的计提、合起来金额正好等于发票(诚煜 6 月账单 = 6/518# + 7/522#)——有的话不擅自红冲，让人去确认
            sug = []
            if not picked:
                try:
                    sug = [c for c in _accrual_candidates(r, invs)[0] if c["suggest"]]
                except Exception:
                    sug = []
            if len(sug) > 1:
                notes.append("这家别的月份有计提、合起来正好等于发票（%s），多半是跨月核销：点「选择核销哪些计提」确认后再做" %
                             "、".join("%d/%s# %.2f" % (c["month"], c["vno"], c["gross"]) for c in sug))
            else:
                amts[v0["vno"]] = {"gross": inv_tot}
                notes.append("计提 %.2f 和发票 %.2f 差 %+.2f：按红冲处理——原计提整笔红冲，按发票金额重新计提后核销" % (v0["gross"], inv_tot, inv_tot - v0["gross"]))
    pl = LV.plan(vouchers, inv_in, fixes, amts) if (vouchers and inv_in) else \
        {"status": "manual", "msgs": ["金蝶本期没找到这家的计提凭证" if not vouchers else "票夹里还没有发票"], "per": {}, "tails": {}}
    # V2.832 付款只做支付(不出核销)的两种情况：
    #   tax06＝费用凭证做的时候就有票、税已经挂待认证了(禾享 5月记-155)，没有暂估要转；
    #   later＝发票后补：人确认过「先做付款凭证」(或这张已经这样写过金蝶)，暂估税等发票到了另做一张转待认证。
    posted_rec = (db.get_setting(_POSTED_KEY, None) or {}).get(inst) or {}
    later = (db.get_setting(_LATER_KEY, None) or {}).get(inst)
    slip = _later_slip(inst)                 # 发票管家的后补单：没登记就不许「没票先付」
    if later and not slip and not posted_rec.get("tax_later"):
        later = None
        notes.append("之前确认过发票后补，但发票管家里已经没有这张单的后补单了（被关闭或删除），不能没票先付")
    g_acc = round(sum(v["gross"] for v in vouchers), 2)
    pay_only = ""
    if vouchers and not any(v.get("from") for v in vouchers) and not amts:
        if all(not v["tax"] for v in vouchers) and any(v.get("tax06") for v in vouchers):
            pay_only = "tax06"
        elif posted_rec.get("tax_later") or (later and not invs):
            pay_only = "later"
    if pay_only:
        if abs(g_acc - float(r.get("amount") or 0)) >= 0.005:
            pl = {"status": "manual", "per": {}, "tails": {},
                  "msgs": ["%s含税合计 %.2f ≠ 请款金额 %.2f，要人工处理" % ("费用凭证" if all(v.get("direct") for v in vouchers) else "计提", g_acc, float(r.get("amount") or 0))]}
            pay_only = ""
        else:
            pl = {"status": "ok", "msgs": [], "tails": {}, "pay_only": pay_only,
                  "per": {v["vno"]: {"mode": "hx", "new_rate": v["rate"], "why": "", "gross": v["gross"]} for v in vouchers}}
    pi = _paid_info(r) or {}
    pay_date, bank = _bank_of(pi.get("bill_id")) if pi.get("bill_id") else (pi.get("date"), "")
    pay_date = pay_date or pi.get("date") or datetime.now().strftime("%Y-%m-%d")
    ctx = {"supplier": r.get("payee") or "", "applicant": r.get("applicant") or "", "pay_year": int(pay_date[:4]),
           "pay_month": int(pay_date[5:7]), "pay_amount": float(r.get("amount") or 0), "bank": bank,
           "paid": bool(pi) and not pi.get("voucher"), "self_vno": self_vno, "tax_later": bool(pay_only)}
    lines = LV.build(ctx, vouchers, [] if pay_only else inv_in, pl, fixes) if pl["status"] == "ok" else []
    dr, cr = LV.balance(lines)
    msgs = list(notes) + list(pl["msgs"])
    if pl["status"] != "ok" and len(vouchers) > 1 and any("≠ 计提含税合计" in m for m in pl["msgs"]):
        msgs.append("几张计提各该改成多少，系统定不了：点「选择核销哪些计提」，给每张填「本次按多少」（默认红冲更正；特殊的可选部分核销并写说明）")
    if pi.get("voucher"):
        msgs.append("这笔付款已经在金蝶 %s 记过支付凭证，本张不再出支付分录" % pi["voucher"])
    if not pi:
        msgs.append("还没付款（金蝶没有付款单）：先出红冲/更正/核销预览，付款后才加支付分录")
    if pi.get("bill_id") and not bank:
        msgs.append("金蝶付款单没取到我方银行账号，银行存款那行的账号待补")
    st = "booked" if inst in (db.get_setting(_POSTED_KEY, None) or {}) else _status(r, folder, invs, ovr)
    if st == "noinv" and pay_only and pi.get("bill_id"):
        st = "ready"                 # 没票也能做：只做支付
    # 发票后补的第三笔：这张已经只做了支付，现在发票到了 → 暂估转待认证(单独一张凭证)
    later3 = posted_rec.get("later3")
    if posted_rec.get("tax_later") and not later3 and invs and vouchers:
        if abs(inv_tot - g_acc) >= 0.005:
            later3 = {"ok": False, "lines": [], "msg": "发票含税合计 %.2f ≠ 费用凭证/计提含税合计 %.2f：金额有差，不能直接转，要人工处理（红冲更正）" % (inv_tot, g_acc)}
        else:
            l3, d3 = LV.later_lines(vouchers, inv_in, datetime.now().year, r.get("payee") or "")
            b3 = LV.balance(l3)
            later3 = {"ok": abs(d3) <= LV.TAIL_MAX + 1e-9 and abs(b3[0] - b3[1]) < 0.005, "lines": l3, "dr": b3[0], "cr": b3[1], "tail": d3,
                      "msg": "" if abs(d3) <= LV.TAIL_MAX + 1e-9 else "发票税额和暂估进项税差 %.2f，超过尾差上限，要人工处理" % d3}
    paywarn = _paywarn(inst) if st == "unpaid" else None
    if paywarn:
        st = "paycode"
        msgs.insert(0, paywarn["text"])
    kind, ktext = _kind(vouchers, notes, pl)
    # 计提调整单(V2.759，用户 2026-10-02「审核的时候就出来，打印后贴在钉钉单据后面」)：每张红冲更正的计提，原计提 vs 更正后
    adjust = []
    for v in (vouchers if lines else []):
        p = pl["per"].get(v["vno"]) or {}
        if p.get("mode") not in ("rate", "fix", "tail", "move", "amt"):
            continue
        e = LV.fix_expl(v, p, ctx["pay_year"])
        nl = [l for l in lines if l["block"] == "更正" and l["expl"] == e]
        exp_old = [{k: l.get(k) for k in ("acct", "acct_name", "dr") + LV.EXP_DIMS} for l in (v.get("exp_orig") or v["exp_lines"])]
        exp_new = [dict(l["dims"], acct=l["acct"], acct_name=l["acct_name"], dr=l["dr"]) for l in nl if str(l["acct"])[:1] in ("5", "6")]
        ng = sum(l["cr"] for l in nl if l["acct"] == "2241.02")
        nt = sum(l["dr"] for l in nl if l["acct"] == "2221.01.07")
        adjust.append({"ref": LV.ref_of(v, ctx["pay_year"]), "vno": v["vno"], "year": v["year"], "month": v["month"], "expl": v["expl"],
                       "mode": p["mode"], "why": (p.get("why") or "") + (("；" + p["memo"]) if p.get("memo") else "") + "".join(
                           "；其中 %s（含税）改为 %s，其余不动%s" % (
                               "{:,.2f}".format(float(x["split_amt"])), " · ".join(_fix_changed(x)) or "（见复核台登记）",
                               ("（%s）" % x["memo"]) if x.get("memo") else "")
                           for x in (fixes.get(v["vno"]) or []) if x.get("split_amt")),
                       "from": (v.get("from") or {}).get("short") or "",
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
    return {"ok": True, "kind": kind, "kind_text": ktext, "xbook": xb, "picked": picked or None, "req": {"inst": inst, "bid": r.get("business_id"), "carrier": r.get("carrier"), "payee": r.get("payee"),
                                "code": r.get("sup_code"), "subject": r.get("subject"), "subject_full": r.get("subject_full"),
                                "amount": r.get("amount"), "period": r.get("period"), "applicant": r.get("applicant"),
                                "paid": pay_date if pi else "", "bank": bank, "folder": folder["id"] if folder else None,
                                "paper_ovr": ovr.get(inst), "status": st, "posted": (db.get_setting(_POSTED_KEY, None) or {}).get(inst),
                                "bill_id": pi.get("bill_id") or "", "paywarn": paywarn, "later": later, "pay_only": pay_only, "later_slip": slip},
            "later3": later3,
            "invoices": invs,
            "accruals": [{"vno": v["vno"], "month": v["month"], "expl": v["expl"], "gross": v["gross"], "net": v["net"], "tax": v["tax"], "rate": v["rate"],
                          "from": (v.get("from") or {}).get("short") or "", "direct": bool(v.get("direct")),
                          "gross_new": (pl["per"].get(v["vno"]) or {}).get("gross"),
                          "fee": "、".join(dict.fromkeys(l.get("fee") or l.get("acct_name") or "" for l in v["exp_lines"])),
                          "biz": "、".join(dict.fromkeys(l.get("biz") for l in v["exp_lines"] if l.get("biz"))),
                          "tail": pl["tails"].get(v["vno"]),
                          **(pl["per"].get(v["vno"]) or {"mode": "", "new_rate": None, "why": ""})} for v in vouchers],
            "plan": {"status": pl["status"], "msgs": msgs, "tails": pl["tails"], "pay_only": pl.get("pay_only") or ""},
            "adjust": adjust,
            "voucher": {"date": pay_date, "book": r.get("subject_full"), "lines": lines, "dr": dr, "cr": cr}}, 200

# ---------- 人工选择核销哪些计提（V2.817）----------
def _picked_vouchers(book, sup_code, picks):
    """人工选定的 [{year, month, vno}] → 计提凭证列表(按月去金蝶取，10 分钟缓存)。金蝶里找不到的那张跳过。"""
    out = []
    for ym in sorted({(int(p["year"]), int(p["month"])) for p in picks}):
        vs, _ = _accruals(book, "%04d-%02d" % ym, sup_code)
        want = {str(p["vno"]) for p in picks if (int(p["year"]), int(p["month"])) == ym}
        out.extend(v for v in vs if v["vno"] in want)
    return out


def _accrual_candidates(r, invs):
    """这张请款单可以选哪些计提：本主体账簿、这家供应商、账单月往前 2 个月 到 付款月(没付款到当月) 的全部计提凭证。
    每张标出：是不是已经被别的凭证核销/红冲过(看这家 2241 分录摘要里的「核销6/518#」「红冲5/498#」)、是不是被别的请款单选走了。只读金蝶。"""
    y, m = int(r["period"][:4]), int(r["period"][5:7])
    pi = _paid_info(r) or {}
    end = str(pi.get("date") or datetime.now().strftime("%Y-%m-%d"))
    ey, em = int(end[:4]), int(end[5:7])
    months, cy, cm = [], y, m
    for _ in range(2):                                  # 往前两个月(跨年照退)
        cm -= 1
        if cm < 1:
            cy, cm = cy - 1, 12
    while (cy, cm) <= (ey, em) and len(months) < 14:
        months.append((cy, cm))
        cm += 1
        if cm > 12:
            cy, cm = cy + 1, 1
    book, sup = r["subject_full"], r["sup_code"]
    # 这家在本账簿的全部 2241 分录：一来知道哪几个月有计提(没有的月份不去取整张凭证)，二来认哪些计提已经被核销/红冲
    s, conf = kc.login()
    used, has = {}, set()
    for yy in sorted({a for a, _ in months}):
        rows = kc._query(s, conf, "GL_VOUCHER", [("FVOUCHERGROUPNO", "号"), ("FPeriod", "期"), ("FEXPLANATION", "摘要"), ("FCREDIT", "贷"), ("FDEBIT", "借")],
                         "FACCOUNTBOOKID.FName='%s' and FYear=%d and FAccountID.FNumber like '2241%%' and FDetailID.FFLEX4.FNumber='%s'" % (
                             book.replace("'", ""), yy, sup.replace("'", "")))
        for x in rows:
            z, pp = _s(x["摘要"]), int(x["期"] or 0)
            if ("计提" in z and float(x["贷"] or 0) and not any(k in z for k in ("红冲", "更正", "核销"))) or _is_direct(z, x["贷"]):
                has.add((yy, pp))
            if z.startswith("核销") or z.startswith("红冲") or "核销" in z[:30]:
                head = z.split("计提")[0]
                for mo in re.finditer(r"(?:(\d{4})-)?(\d{1,2})/(\d+)#", head):
                    used.setdefault((int(mo.group(1) or yy), int(mo.group(2)), mo.group(3)), "%d 月 记-%s" % (pp, _s(x["号"])))
    others = {}
    for i2, pk in (db.get_setting(_PICK_KEY, None) or {}).items():
        if i2 != r["inst_id"]:
            for p in pk.get("picks") or []:
                others[(int(p["year"]), int(p["month"]), str(p["vno"]))] = i2
    mine = {(int(p["year"]), int(p["month"]), str(p["vno"])): p.get("to_gross") for p in ((db.get_setting(_PICK_KEY, None) or {}).get(r["inst_id"]) or {}).get("picks") or []}
    cands = []
    for (yy, mm) in months:
        if (yy, mm) not in has:
            continue
        vs, _ = _accruals(book, "%04d-%02d" % (yy, mm), sup)
        for v in vs:
            k = (yy, mm, v["vno"])
            cands.append({"year": yy, "month": mm, "vno": v["vno"], "expl": v["expl"], "gross": v["gross"], "tax": v["tax"], "net": v["net"], "rate": v["rate"],
                          "fee": "、".join(dict.fromkeys(l.get("fee") or l.get("acct_name") or "" for l in v["exp_lines"])),
                          "biz": "、".join(dict.fromkeys(l.get("biz") for l in v["exp_lines"] if l.get("biz"))),
                          "used": used.get(k) or "", "other": bool(others.get(k)), "picked": k in mine, "to_gross": mine.get(k), "bill_month": (yy, mm) == (y, m),
                          "direct": bool(v.get("direct"))})
    # 建议：没被用过的里面，含税合计正好＝发票合计的一组(优先带上账单月的)
    inv_tot = round(sum(i["gross"] for i in invs), 2)
    free = sorted([c for c in cands if not c["used"] and not c["other"]], key=lambda c: (not c["bill_month"], c["year"], c["month"]))
    sug = LV._subset(free, inv_tot) if (free and inv_tot) else None
    for c in cands:
        c["suggest"] = bool(sug and c in sug)
    return cands, inv_tot


@router.get("/api/logistics-voucher/accrual-candidates")
def accrual_candidates(request: Request, inst: str):
    """预览里「选择核销哪些计提」的候选清单。只读。"""
    if not _perm(request):
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    with db._engine.connect() as c:
        r = c.execute(select(PR).where(PR.c.inst_id == inst)).mappings().first()
    if not r or not r.get("period"):
        return JSONResponse({"ok": False, "msg": "没有这张请款单，或还没认出账单月"}, status_code=400)
    r = dict(r)
    _, invs = _invoices(inst)
    try:
        cands, inv_tot = _accrual_candidates(r, invs)
    except Exception as e:
        return JSONResponse({"ok": False, "msg": "读金蝶计提失败：%s" % str(e)[:160]}, status_code=502)
    pk = (db.get_setting(_PICK_KEY, None) or {}).get(inst)
    return {"ok": True, "inv_total": inv_tot, "period": r["period"], "picked": pk or None, "cands": cands}


@router.post("/api/logistics-voucher/pick")
async def pick_accruals(request: Request):
    """人工选定这张请款单核销哪几张计提；picks 传空＝恢复系统自动认。已写金蝶的不能再改。留痕。"""
    u = _perm(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    b = await request.json()
    inst = str(b.get("inst") or "")
    if inst in (db.get_setting(_POSTED_KEY, None) or {}):
        return JSONResponse({"ok": False, "msg": "这张已经写过金蝶，不能再改核销哪些计提"}, status_code=400)
    picks = []
    for p in b.get("picks") or []:
        try:
            one = {"year": int(p["year"]), "month": int(p["month"]), "vno": str(p["vno"])}
            if p.get("to_gross") not in (None, ""):
                one["to_gross"] = round(float(p["to_gross"]), 2)
                if one["to_gross"] < 0:
                    raise ValueError
            picks.append(one)
        except (KeyError, TypeError, ValueError):
            return JSONResponse({"ok": False, "msg": "选的计提格式不对（金额要填数字）"}, status_code=400)
    part, memo = bool(b.get("part")), str(b.get("memo") or "").strip()[:80]
    if part and not any("to_gross" in p for p in picks):
        return JSONResponse({"ok": False, "msg": "部分核销要给至少一张计提填「本次按多少」"}, status_code=400)
    if part and not memo:
        return JSONResponse({"ok": False, "msg": "部分核销要写说明（为什么只核销一部分，会写进凭证摘要）"}, status_code=400)
    vnos = [p["vno"] for p in picks]
    if len(set(vnos)) != len(vnos):
        return JSONResponse({"ok": False, "msg": "选的计提里有两张凭证号相同（不同月份），系统暂时分不开，这张请人工做"}, status_code=400)
    allp = dict(db.get_setting(_PICK_KEY, None) or {})
    for i2, pk in allp.items():
        if i2 != inst and any((p["year"], p["month"], p["vno"]) == (int(q["year"]), int(q["month"]), str(q["vno"])) for p in picks for q in pk.get("picks") or []):
            return JSONResponse({"ok": False, "msg": "其中有计提已经被另一张请款单选走了，先到那张里取消"}, status_code=400)
    if picks:
        allp[inst] = {"picks": picks, "part": part, "memo": memo, "by": u["name"], "at": _now()}
    else:
        allp.pop(inst, None)
    db.set_setting(_PICK_KEY, allp, u["name"])
    db.audit(u["name"], "物流付款做账-选定核销的计提", inst, ("、".join(
        "%d-%d/%s#%s" % (p["year"], p["month"], p["vno"], ("→%.2f" % p["to_gross"]) if "to_gross" in p else "") for p in picks)
        + ("；部分核销：" + memo if part else ("；" + memo if memo else ""))) if picks else "恢复系统自动认")
    return {"ok": True}


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
                                 "acc": [{k: a.get(k) for k in ("vno", "month", "fee", "biz", "tail", "expl", "gross", "rate", "mode", "new_rate", "why", "from", "gross_new")}
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
ST_CN = {"ready": "可做账", "paper": "纸质件未到", "invdiff": "发票≠请款", "noinv": "票不齐", "unpaid": "未付款", "booked": "已做账",
         "paycode": "付款单往来单位编码不对", "latertax": "发票到了·待转待认证"}


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
           "tax_later": d2["plan"].get("pay_only") == "later",     # 发票后补：只做了支付，暂估税还没转(发票到了要补第三笔)
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


# ---------- 自动做账（V2.809，用户 2026-10-05「系统自动看看有没有付款单，有的话，自动执行做账」「红冲更正后，也可以做」）----------
# 跟在「物流请款单接收员」每 20 分钟那一轮后面：已付款、票齐的单逐张算一遍，够条件的自动走「保存到金蝶」。
# 三档：off 关 / dry 演练(只算「如果开着会做哪几张」，不碰金蝶，默认) / on 真做。真做＝没有人点按钮，系统自己审核付款单、写凭证并提交。
# 自动范围(可配)：一致·只核销、尾差·红冲更正、需红冲更正。计提记错主体(要在别的账簿新建红冲凭证)、金额不符、没有计提 一律留给人。
import threading as _th
_AUTO_KEY = "logi_voucher_auto"             # {mode, kinds, cap, max_round, by, at}
_AUTO_LAST_KEY = "logi_voucher_auto_last"   # 上一轮：{at, mode, trigger, items:[…], done:[…]}
_AUTO_LOCK = _th.Lock()
AUTO_KINDS = ("hx", "tail", "redo")
KIND_CN = {"hx": "一致·只核销", "tail": "尾差·红冲更正", "redo": "需红冲更正", "subj": "计提记错主体", "manual": "金额不符", "noacc": "没有计提",
           "part": "部分核销", "later": "发票后补·先付款"}
AUTO_USER = "系统自动"


def _auto_cfg():
    c = dict(db.get_setting(_AUTO_KEY, None) or {})
    kinds = c.get("kinds")
    try:
        cap = max(0.0, float(c.get("cap") or 0))
    except (TypeError, ValueError):
        cap = 0.0
    try:
        mx = min(50, max(1, int(c.get("max_round") or 10)))
    except (TypeError, ValueError):
        mx = 10
    return {"mode": c.get("mode") if c.get("mode") in ("off", "dry", "on") else "dry",
            "kinds": [k for k in (kinds if isinstance(kinds, list) else AUTO_KINDS) if k in AUTO_KINDS],
            "cap": cap, "max_round": mx, "by": c.get("by") or "", "at": c.get("at") or ""}


def _auto_ok(d, cfg):
    """这一张够不够条件自动做 → (bool, 不做的原因)。条件比人点的时候严：任何一条提示里带「要人工」都不做。"""
    req, k = d["req"], d.get("kind")
    if d["plan"]["status"] != "ok":
        return False, "计提和发票对不上：%s" % ((d["plan"]["msgs"] or ["要人工处理"])[0])
    if k == "subj" or d.get("xbook"):
        return False, "计提记错主体：要在别的账簿建红冲凭证，留给人单张做"
    if k not in AUTO_KINDS:
        return False, "%s，要人工" % (KIND_CN.get(k) or k or "做账类型认不出")
    if k not in cfg["kinds"]:
        return False, "「%s」没放进自动范围" % KIND_CN[k]
    if any(a.get("mode") == "amt" for a in d.get("accruals") or []) and not d.get("picked"):
        return False, "计提和发票金额有差（要红冲、按发票金额更正），金额更正留给人点；人确认过应为金额的才自动做"
    if not str(req.get("bill_id") or "").isdigit():
        return False, "没有金蝶付款单（或这笔付款已经记过支付凭证）"
    if not req.get("bank"):
        return False, "金蝶付款单上没有我方银行账号"
    v = d["voucher"]
    if not v.get("lines") or abs(float(v.get("dr") or 0) - float(v.get("cr") or 0)) >= 0.005:
        return False, "凭证借贷不平"
    if cfg["cap"] and float(req.get("amount") or 0) > cfg["cap"] + 0.004:
        return False, "金额 %.2f 超过单笔上限 %.2f" % (float(req.get("amount") or 0), cfg["cap"])
    man = next((m for m in d["plan"]["msgs"] if "要人工" in m), "")
    if man:
        return False, man
    return True, ""


def auto_round(trigger="定时"):
    """跑一轮。off 直接返回；dry 只算；on 真做(每轮最多 max_round 张)。结果存 _AUTO_LAST_KEY 给页面看，并向数字员工办公室报到。"""
    try:
        import worker_store
    except Exception:
        worker_store = None
    cfg = _auto_cfg()
    if cfg["mode"] == "off":
        if worker_store:
            worker_store.beat("voucher_auto", off="自动做账没打开（付款做账页可以开）")
        return {"ok": True, "mode": "off"}
    if not _AUTO_LOCK.acquire(blocking=False):
        return {"ok": False, "msg": "上一轮还在跑"}
    try:
        posted = db.get_setting(_POSTED_KEY, None) or {}
        with db._engine.connect() as c:
            reqs = [dict(r) for r in c.execute(select(PR).where(PR.c.create_time >= "2026-09-01")).mappings().all()]
        items = []
        for r in reqs:
            inst = r["inst_id"]
            if r.get("excluded") or r.get("dt_status") == "TERMINATED" or r.get("dt_result") == "refuse" or inst in posted or not r.get("period"):
                continue
            folder, invs = _invoices(inst)
            if _status(r, folder, invs, {}) != "ready":          # 还没付款 / 票不齐 / 发票≠请款：本来就做不了，不列
                continue
            it = {"inst": inst, "bid": r.get("business_id"), "payee": r.get("payee") or r.get("carrier"), "subject": r.get("subject"),
                  "amount": r.get("amount"), "period": r.get("period"), "kind": "", "kind_cn": "", "ok": False, "why": ""}
            try:
                d, code = _preview_data(inst)
                if code != 200:
                    it["why"] = d.get("msg") or "读取失败"
                else:
                    it["kind"], it["kind_cn"] = d.get("kind") or "", KIND_CN.get(d.get("kind")) or ""
                    it["ok"], it["why"] = _auto_ok(d, cfg)
            except Exception as e:
                it["why"] = "读取出错：%s" % str(e)[:120]
            items.append(it)
        done = []
        if cfg["mode"] == "on":
            for it in [x for x in items if x["ok"]][:cfg["max_round"]]:
                lk = _POST_LOCK.setdefault(it["inst"], _th.Lock())
                if not lk.acquire(blocking=False):
                    continue
                try:
                    res = _post(it["inst"], AUTO_USER)
                except Exception as e:
                    res = {"ok": False, "msg": "写金蝶出错：%s" % str(e)[:200]}
                finally:
                    lk.release()
                it["done"] = bool(res.get("ok"))
                it["vno"] = res.get("vno") or ""
                it["msg"] = " → ".join(res.get("steps") or []) if res.get("ok") else (res.get("msg") or "")
                done.append({"inst": it["inst"], "bid": it["bid"], "ok": it["done"], "vno": it["vno"], "msg": it["msg"]})
        last = {"at": _now(), "mode": cfg["mode"], "trigger": trigger, "items": items, "done": done}
        db.set_setting(_AUTO_LAST_KEY, last, AUTO_USER)
        n_ok, n_bad = sum(1 for x in done if x["ok"]), sum(1 for x in done if not x["ok"])
        if done:
            db.audit(AUTO_USER, "物流付款做账-自动做账", trigger, "做成 %d 张（%s）；失败 %d 张" % (
                n_ok, "、".join("记-%s" % x["vno"] for x in done if x["ok"]), n_bad))
        if worker_store:
            worker_store.beat("voucher_auto", next_in=20 * 60)
            if done:
                worker_store.record("voucher_auto", n=n_ok, ok=not n_bad, summary="自动做账 %d 张" % n_ok, trigger=trigger,
                                    refs=[x["bid"] for x in done if x["ok"]],
                                    error=("%d 张没做成：%s" % (n_bad, "；".join(x["msg"] for x in done if not x["ok"])[:300])) if n_bad else "")
        return {"ok": True, **last}
    finally:
        _AUTO_LOCK.release()


@router.get("/api/logistics-voucher/auto")
def auto_get(request: Request):
    if not _perm(request):
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    return {"ok": True, "cfg": _auto_cfg(), "last": db.get_setting(_AUTO_LAST_KEY, None), "kinds": [{"k": k, "n": KIND_CN[k]} for k in AUTO_KINDS]}


@router.post("/api/logistics-voucher/auto")
async def auto_set(request: Request):
    """改自动做账的档位/范围/限额。打开「真做」(on) 只有管理员能点；改动留痕。"""
    u = _perm(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    b = await request.json()
    old = _auto_cfg()
    new = dict(old)
    if b.get("mode") in ("off", "dry", "on"):
        new["mode"] = b["mode"]
    if isinstance(b.get("kinds"), list):
        new["kinds"] = [k for k in b["kinds"] if k in AUTO_KINDS]
    if b.get("cap") is not None:
        new["cap"] = b.get("cap")
    if b.get("max_round") is not None:
        new["max_round"] = b.get("max_round")
    if new["mode"] == "on" and old["mode"] != "on" and u.get("role") != "admin":
        return JSONResponse({"ok": False, "msg": "打开自动做账要管理员来点（它会让系统自己审核付款单、写凭证）"}, status_code=403)
    new["by"], new["at"] = u["name"], _now()
    db.set_setting(_AUTO_KEY, new, u["name"])
    new = _auto_cfg()
    db.audit(u["name"], "物流付款做账-自动做账设置", {"off": "关", "dry": "演练", "on": "真做"}[new["mode"]],
             "范围 %s；单笔上限 %s；每轮最多 %d 张" % ("、".join(KIND_CN[k] for k in new["kinds"]) or "（空）",
                                            ("%.2f" % new["cap"]) if new["cap"] else "不限", new["max_round"]))
    return {"ok": True, "cfg": new}


@router.post("/api/logistics-voucher/auto/run")
async def auto_run(request: Request):
    """现在就跑一轮(按当前档位：演练只算、真做才写)。"""
    u = _perm(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    from starlette.concurrency import run_in_threadpool
    try:
        return await run_in_threadpool(auto_round, "%s 手动跑" % u["name"])
    except Exception as e:
        return JSONResponse({"ok": False, "msg": "这一轮出错：%s" % str(e)[:200]}, status_code=400)


# ---------- 扫付款单二维码查凭证（V2.801，用户 2026-10-05「扫描那个付款单二维码，就知道是什么凭证、哪个主体」）----------
# 纸质付款单右上角的二维码是钉钉审批单链接：解析出审批实例号 → 请款单 → 做账记录里的凭证号。只读，不建票夹、不拉附件。
# 不是物流请款单的，退一步看发票管家票夹里同步到的凭证号(那边定时从金蝶凭证摘要里认发票号)。
_SCAN_CACHE = {}
_VPEOPLE = {}


def _voucher_people(book, month, vno):
    """金蝶里这张凭证的制单人、审核人、状态(V2.804，用户「加一个制单人」)。只读，缓存 5 分钟；查不到返回 {}。"""
    k = (book, month, str(vno))
    hit = _VPEOPLE.get(k)
    if hit and time.time() - hit[1] < 300:
        return hit[0]
    out = {}
    try:
        y, m = int(str(month)[:4]), int(str(month)[5:7])
        s, conf = kc.login()
        rows = kc._query(s, conf, "GL_VOUCHER", [("FCREATORID.FName", "制单"), ("FCHECKERID.FName", "审核"), ("FDOCUMENTSTATUS", "状态")],
                         "FACCOUNTBOOKID.FName='%s' and FYear=%d and FPeriod=%d and FVOUCHERGROUPNO='%s'" % (
                             str(book).replace("'", ""), y, m, str(vno).replace("'", "")))
        if rows:
            out = {"maker": _s(rows[0].get("制单")), "checker": _s(rows[0].get("审核")), "status": _s(rows[0].get("状态"))}
    except Exception:
        out = {}
    _VPEOPLE[k] = (out, time.time())
    return out


def _with_people(vs, makers=None):
    """给每张凭证挂上制单人/审核人。制单人照金蝶的写(系统写入的在金蝶里就是「系统操作员」，和打印出来的凭证一致)；
    实际在工作台点「保存到金蝶」的那个人另给一项 operator(经办人，makers={凭证号: 人})。
    V2.804 曾把经办人当制单人显示，用户问「这两张是我制单的吗，不是系统操作员吗」→ V2.807 改回照金蝶。"""
    s2f = {o.get("short_name"): o.get("full_name") for o in (db.list_orgs() or [])}
    for v in vs:
        p = _voucher_people(s2f.get(v.get("subject")) or v.get("subject") or "", v.get("month") or "", v.get("vno"))
        who = (makers or {}).get(str(v.get("vno")))
        who = who.split("(")[0].split("（")[0].strip() if who else who      # 早期实测那张记的是「曾禹锡(实测)」，只显示人名
        v["maker"] = p.get("maker") or ""
        v["operator"] = who or ""
        v["checker"] = p.get("checker") or ""
        v["audited"] = (p.get("status") == "C") if p else None
    return vs


def _scan_lookup(code):
    """扫码 → 哪个主体、哪张凭证。V2.854：计提更正单上印的是系统自己的二维码「审批编号#页别」(0 普通 / 1 主体更正①原主体红冲 / 2 ②本主体补提)，
    按审批编号查，再按页别把该订的那张凭证排在最前面——原主体的红冲凭证没有纸质付款单、没有钉钉二维码，靠这个让装订的人知道。"""
    import unicodedata
    m = re.fullmatch(r"(\d{20,21})#([012])", re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(code or ""))))
    if not m:
        return _scan_lookup0(code)
    r = _scan_lookup0(m.group(1))
    if not r.get("ok"):
        return r
    sheet = int(m.group(2))
    r["sheet"] = sheet
    if sheet == 1:
        red = [v for v in r.get("vouchers") or [] if str(v.get("what") or "").startswith("红冲凭证")]
        if red:
            r["vouchers"] = red + [v for v in r["vouchers"] if v not in red]
            r["has_xred"] = False            # 这一页就是 ①，不再提示「后面订着两张」
        else:
            r["vouchers"] = []
            r["state"] = "更正单上这一项对应的红冲凭证还没建"
            r["sheet_note"] = "原主体的红冲凭证要等这张请款单的付款凭证保存到金蝶时，系统一并建好；建好再扫。"
    return r


def _scan_lookup0(code):
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
                "vouchers": _with_people(synced), "state": "" if synced else "发票管家里这张单的发票还没被金蝶凭证引用（还没做账，或还没同步到）"}
    r = dict(r)
    pi = _paid_info(r) or {}
    posted = (db.get_setting(_POSTED_KEY, None) or {}).get(iid)
    vs = []
    if posted:
        vs.append({"subject": r.get("subject"), "month": str(pi.get("date") or posted.get("at") or "")[:7], "vno": posted.get("vno"),
                   "what": "付款凭证", "src": "系统写入", "bill_no": posted.get("bill_no")})
        for x in (posted.get("xred") or {}).values():
            vs.append({"subject": x.get("short"), "month": "%s-%02d" % (x.get("year"), int(x.get("month") or 0)), "vno": x.get("vno"),
                       "what": "红冲凭证（没有纸质付款单）", "src": "系统写入"})
        if (posted.get("later3") or {}).get("vno"):
            x = posted["later3"]
            vs.append({"subject": r.get("subject"), "month": str(x.get("date") or "")[:7], "vno": x.get("vno"),
                       "what": "暂估转待认证（发票后补，附发票）", "src": "系统写入"})
    else:
        if pi.get("voucher"):
            vs.append({"subject": r.get("subject"), "month": str(pi.get("date") or "")[:7], "vno": str(pi["voucher"]).replace("记-", ""),
                       "what": "支付凭证", "src": "金蝶已有"})
        vs.extend(v for v in synced if not any(v["vno"] == o["vno"] and v["subject"] == o["subject"] for o in vs))
    makers = {}
    if posted:
        makers[str(posted.get("vno"))] = posted.get("by") or ""
        for x in (posted.get("xred") or {}).values():
            makers[str(x.get("vno"))] = x.get("by") or ""
    _with_people(vs, makers)
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


# ---------- 凭证装订（V2.850：其它模块 › 凭证装订，先做物流试运行）----------
# 用户 2026-10-06 梳理流程：钉钉审核 → 出纳付款 → 纸质付款单到实习生手里；付款凭证工作台做、财务经理审核。
#   定：不要贴条，实习生扫码看到凭证号手写标上；没审核的也显示(提醒一句)；扫码不混在付款做账页里。
#   计提更正单：由装订的人打——扫到那张单时当场打(电脑)，或回到电脑在「计提更正单」页签把扫过、还没打的一次打出来。
#   「还没扫到的」只能按「扫没扫过」算，系统不知道人有没有真写上去。
BS = store.bind_scan


def _bind_marks():
    with db._engine.connect() as c:
        return {r["inst_id"]: dict(r) for r in c.execute(select(BS)).mappings().all()}


def _bind_touch(inst, user, vnos):
    """记一笔「这张纸质单扫过了」→ 这次之前的状态 {by, at, n} / None(第一次)。"""
    from sqlalchemy import insert, update
    now = _now()
    with db._engine.begin() as c:
        r = c.execute(select(BS).where(BS.c.inst_id == inst)).mappings().first()
        if r:
            c.execute(update(BS).where(BS.c.id == r["id"]).values(last_by=user, last_at=now, n=int(r["n"] or 0) + 1, vnos=vnos[:200]))
            return {"by": r["first_by"], "at": r["first_at"], "n": int(r["n"] or 0)}
        c.execute(insert(BS).values(inst_id=inst, vnos=vnos[:200], first_by=user, first_at=now, last_by=user, last_at=now, n=1))
    return None


def _bind_printed(inst, user):
    from sqlalchemy import insert, update
    now = _now()
    with db._engine.begin() as c:
        r = c.execute(select(BS).where(BS.c.inst_id == inst)).mappings().first()
        if r:
            c.execute(update(BS).where(BS.c.id == r["id"]).values(adj_by=user, adj_at=now))
        else:
            c.execute(insert(BS).values(inst_id=inst, vnos="", n=0, adj_by=user, adj_at=now))


def _adj_n(inst, posted_rec):
    """这张做账时有没有红冲更正/主体更正(要不要附计提更正单) → 张数。做账记录里记了就用记的；早先做的三张没记，现算一次。"""
    if not posted_rec:
        return 0
    if posted_rec.get("n_adjust") is not None:
        return int(posted_rec.get("n_adjust") or 0)
    try:
        d, code = _preview_data(inst)
        return len(d.get("adjust") or []) if code == 200 else 0
    except Exception:
        return 0


def _scan_done(r, user):
    """扫码结果出来以后：补上要不要附更正单、以前扫没扫过，并记一笔。"""
    if not (r and r.get("ok") and r.get("inst")):
        return r
    posted = (db.get_setting(_POSTED_KEY, None) or {}).get(r["inst"])
    if posted:
        r["n_adjust"] = _adj_n(r["inst"], posted)
    if r.get("vouchers") and r.get("sheet") is None:        # 扫的是更正单就不算「纸质付款单扫过了」
        prev = _bind_touch(r["inst"], user, "；".join("%s 记-%s" % (v.get("subject") or "", v.get("vno")) for v in r["vouchers"]))
        r["scanned_before"] = prev
        mk = _bind_marks().get(r["inst"]) or {}
        r["adj_printed"] = {"by": mk.get("adj_by"), "at": mk.get("adj_at")} if mk.get("adj_at") else None
    return r


def _bind_items(month=""):
    """已有凭证号的物流请款单 → 装订条目 + 扫过/打过的记录。month 空＝最近一个凭证月份。只读(审核人要读金蝶，5 分钟缓存)。"""
    posted = db.get_setting(_POSTED_KEY, None) or {}
    with db._engine.connect() as c:
        rows = [dict(r) for r in c.execute(select(PR)).mappings().all()]
    try:
        from routers.logistics_payreq import _kd_suppliers
        c2n = _kd_suppliers().get("code2name") or {}
    except Exception:
        c2n = {}
    f2s = {o.get("full_name"): o.get("short_name") for o in (db.list_orgs() or [])}
    out = []
    for r in rows:
        if r.get("excluded"):
            continue
        iid, p, pi = r["inst_id"], posted.get(r["inst_id"]), (_paid_info(r) or {})
        base = {"inst": iid, "payee": c2n.get(r.get("sup_code")) or r.get("payee") or r.get("carrier"), "bid": r.get("business_id"), "period": r.get("period") or ""}
        if p:
            out.append(dict(base, key=iid, subject=r.get("subject"), month=str(pi.get("date") or p.get("at") or "")[:7], vno=str(p.get("vno")), amount=r.get("amount"),
                            what="付款凭证", paper=True, by=p.get("by") or "", _p=p))
            for x in (p.get("xred") or {}).values():
                out.append(dict(base, key="%s|x%s" % (iid, x.get("src_vno")), subject=x.get("short"), month="%s-%02d" % (x.get("year"), int(x.get("month") or 0)),
                                vno=str(x.get("vno")), amount=-float(x.get("gross") or 0), what="红冲凭证（没有纸质付款单）", paper=False, by=x.get("by") or "", adj=1))
            x = p.get("later3") or {}
            if x.get("vno"):
                out.append(dict(base, key=iid + "|later", subject=r.get("subject"), month=str(x.get("date") or "")[:7], vno=str(x.get("vno")), amount=x.get("tax") or 0,
                                what="暂估转待认证（没有纸质付款单，附发票）", paper=False, by=x.get("by") or ""))
        else:
            vs = []
            if pi.get("voucher"):
                vs.append((r.get("subject"), str(pi.get("date") or "")[:7], str(pi["voucher"]).replace("记-", "")))
            try:
                for i in _invoices(iid)[1]:
                    for v in i.get("vouchers") or []:
                        if isinstance(v, dict) and v.get("number"):
                            k = (f2s.get(v.get("book")) or v.get("book") or "", str(v.get("period") or "")[:7], str(v["number"]).replace("记-", "").replace("记", ""))
                            if k not in vs and not any(k[2] == o[2] and k[0] == o[0] for o in vs):
                                vs.append(k)
            except Exception:
                pass
            for j, (sj, mo, vno) in enumerate(vs):
                out.append(dict(base, key=iid if j == 0 else "%s|k%d" % (iid, j), subject=sj, month=mo, vno=vno, amount=r.get("amount"),
                                what="金蝶已有的凭证（不是本系统写的）", paper=j == 0, by=""))
    months = sorted({x["month"] for x in out if x["month"]}, reverse=True)
    month = month if month in months else (months[0] if months else "")
    out = [x for x in out if x["month"] == month]
    for x in out:
        p = x.pop("_p", None)
        if p is not None:
            x["adj"] = _adj_n(x["inst"], p)
            x["has_xred"] = bool(p.get("xred"))
        x.setdefault("adj", 0)
    _with_people(out, {x["vno"]: x["by"] for x in out if x.get("by")})
    marks = _bind_marks()
    for x in out:
        m = marks.get(x["inst"]) or {}
        x["scanned"] = {"by": m.get("first_by"), "at": m.get("first_at"), "n": m.get("n")} if (x["paper"] and m.get("first_at")) else None
        x["printed"] = {"by": m.get("adj_by"), "at": m.get("adj_at")} if (x["adj"] and m.get("adj_at")) else None
    so = {"深圳星期零": 0, "深圳星期九": 1, "孝感星期九": 2}
    out.sort(key=lambda x: (so.get(x["subject"], 9), x["subject"] or "", int(x["vno"]) if str(x["vno"]).isdigit() else 0))
    return {"ok": True, "months": months, "month": month, "items": out}


@router.get("/api/logistics-voucher/bind-list")
async def bind_list(request: Request, month: str = ""):
    """凭证装订：这个凭证月份已有凭证号的物流请款单——扫过没有、要不要附更正单、更正单打过没有。只读。"""
    if not _perm_scan(request):
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    from starlette.concurrency import run_in_threadpool
    try:
        return await run_in_threadpool(_bind_items, month)
    except Exception as e:
        return JSONResponse({"ok": False, "msg": "读取失败：%s" % str(e)[:160]}, status_code=502)


@router.post("/api/logistics-voucher/bind-adjust")
async def bind_adjust(request: Request):
    """凭证装订：取一张请款单的计提更正单内容(同付款做账的凭证预览，只读)，并记下谁、什么时候打的。"""
    u = _perm_scan(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    b = await request.json()
    inst = str(b.get("inst") or "")
    from starlette.concurrency import run_in_threadpool
    try:
        d, code = await run_in_threadpool(_preview_data, inst)
    except Exception as e:
        return JSONResponse({"ok": False, "msg": "读取失败：%s" % str(e)[:160]}, status_code=502)
    if code != 200:
        return JSONResponse(d, status_code=code)
    if (d.get("adjust") or []) and not b.get("peek"):
        _bind_printed(inst, u["name"])
    return d


@router.post("/api/logistics-voucher/scan")
async def scan_lookup(request: Request):
    """扫付款单右上角的二维码(或输审批编号) → 哪个主体、哪张凭证。只读。"""
    u = _perm_scan(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    b = await request.json()
    from starlette.concurrency import run_in_threadpool
    try:
        return await run_in_threadpool(lambda: _scan_done(_scan_lookup(str(b.get("code") or "")[:600]), u["name"]))
    except Exception as e:
        return {"ok": False, "msg": "查询出错：%s" % str(e)[:160]}


@router.get("/api/logistics-voucher/dd-config")
async def scan_dd_config(request: Request, url: str = ""):
    """手机页在钉钉里调「扫一扫」前的 dd.config 签名(V2.803，用户「扫描，而不是拍照」)。只给本站页面签，沿用发票管家那套签名和企业编号。"""
    if not _perm_scan(request):
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
    _u = _perm_scan(request)
    if not _u:
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    _uname = _u["name"]
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
        link = next((q for q, k in kinds if k == "approval_link"), None) or next((q for q in qrs if re.fullmatch(r"\s*\d{20,21}#[012]\s*", str(q))), None)
        if not link:
            if any(k == "invoice_qr" for _, k in kinds):
                return {"ok": False, "msg": "照片里是发票的二维码：请拍付款单（审批单）右上角那个"}
            return {"ok": False, "msg": "照片里没认出付款单的二维码：对准右上角那个码，拍近一点、别反光，再试一次"}
        return _scan_done(_scan_lookup(link), _uname)
    try:
        return await run_in_threadpool(run)
    except Exception as e:
        return {"ok": False, "msg": "识别出错：%s" % str(e)[:160]}


# ---------- 审核时生成费用凭证（V2.835：不计提、直接做账的那一笔）----------
# 用户 2026-10-06：「这种还是没法自动，因为维度需要人检查下」「连带审核一起吧，审核完检查之后生成凭证」。
# 做法：复核台登记制的承运商审核时，系统把这家上一次费用凭证的科目/部门/费用项目/产品分类带出来，人核对(可改)、打勾确认后才生成；
#   借 费用(不含税) + 借 进项税(有票＝待认证，每张票一行、摘要以发票号开头；没票＝暂估) / 贷 2241.02 供应商。提交不审核。
#   生成后自动把这张凭证选定给这张请款单(付款做账、复核台总表就都认得)。不进自动做账。
def _last_direct(book, sup_code):
    """这家在本主体账簿最近一张直接做账的费用凭证(今年没有看去年) → acc_voucher / None。只读。"""
    s, conf = kc.login()
    for y in (datetime.now().year, datetime.now().year - 1):
        rows = kc._query(s, conf, "GL_VOUCHER", [("FVOUCHERGROUPNO", "号"), ("FPeriod", "期"), ("FEXPLANATION", "摘要"), ("FCREDIT", "贷")],
                         "FACCOUNTBOOKID.FName='%s' and FYear=%d and FAccountID.FNumber like '2241%%' and FCREDIT>0 and FDetailID.FFLEX4.FNumber='%s'" % (
                             book.replace("'", ""), y, sup_code.replace("'", "")))
        ds = sorted({(int(r["期"] or 0), _s(r["号"])) for r in rows if _is_direct(r["摘要"], r["贷"])}, key=lambda k: (k[0], int(k[1]) if k[1].isdigit() else 0))
        if ds:
            per, vno = ds[-1]
            vs, _ = _accruals(book, "%04d-%02d" % (y, per), sup_code)
            return next((v for v in vs if v["vno"] == vno), None)
    return None


def _fee_draft(inst):
    with db._engine.connect() as c:
        r = c.execute(select(PR).where(PR.c.inst_id == inst)).mappings().first()
    if not r:
        return {"ok": False, "msg": "没有这张请款单"}
    r = dict(r)
    folder, invs = _invoices(inst)
    book, sup = r["subject_full"], r["sup_code"]
    last = _last_direct(book, sup)
    exist = []
    if r.get("period"):
        try:
            exist = [c for c in _accrual_candidates(r, invs)[0] if c.get("direct") and not c["used"] and not c["other"]]
        except Exception:
            exist = []
    e0 = ((last or {}).get("exp_lines") or [{}])[0]
    s, conf = kc.login()
    date, y, m = _xred_date(book, datetime.now().strftime("%Y-%m-%d"), s, conf)
    ded = [i for i in invs if i.get("deduct")]
    rec = (db.get_setting(_FEE_KEY, None) or {}).get(inst)
    return {"ok": True, "fee": rec, "picked": (db.get_setting(_PICK_KEY, None) or {}).get(inst),
            "req": {"inst": inst, "bid": r.get("business_id"), "subject": r.get("subject"), "subject_full": book, "payee": r.get("payee"), "code": sup,
                    "amount": float(r.get("amount") or 0), "period": r.get("period") or "", "applicant": r.get("applicant") or "", "reason": r.get("reason") or ""},
            "invoices": [{k: i.get(k) for k in ("number", "type_short", "gross", "tax", "rate", "deduct")} for i in invs],
            "inv_total": round(sum(i["gross"] for i in invs), 2), "inv_tax": round(sum(i["tax"] for i in ded), 2),
            "tax_mode": "inv" if ded else ("none" if invs else "est"),      # 有能抵扣的票＝待认证；有票但都不能抵扣＝不出税行；没票＝暂估
            "last": ({"vno": last["vno"], "year": last["year"], "month": last["month"], "expl": last["expl"], "gross": last["gross"], "rate": last["rate"]} if last else None),
            "defaults": {"acct": " ".join(x for x in (e0.get("acct"), e0.get("acct_name")) if x), "dept": " ".join(x for x in (e0.get("dept_code"), e0.get("dept")) if x),
                         "fee": " ".join(x for x in (e0.get("fee_code"), e0.get("fee")) if x), "biz": " ".join(x for x in (e0.get("biz_code"), e0.get("biz")) if x),
                         "proj": " ".join(x for x in (e0.get("proj_code"), e0.get("proj")) if x),
                         "rate": (last or {}).get("rate"), "expl": (last or {}).get("expl") or "%s提起支付%s" % (r.get("applicant") or "", r.get("payee") or ""),
                         "sup_grp": ((last or {}).get("ap_line") or {}).get("sup_grp") or ""},
            "date": date, "exist": [{k: c[k] for k in ("year", "month", "vno", "gross", "expl")} for c in exist],
            # V2.837(用户「关联发票后补单」)：没票做账以发票管家登记了后补单为前提(同 V2.833 没票先付款)，页面把后补单号/预计到票日亮出来
            "later_slip": _later_slip(inst)}


@router.get("/api/logistics-voucher/fee-draft")
def fee_draft(request: Request, inst: str):
    """审核时的费用凭证草稿：请款单、发票、这家上一次费用凭证的维度(默认值)、金蝶里有没有还没用过的费用凭证(防重复)。只读。"""
    if not _perm(request):
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    try:
        d = _fee_draft(inst)
    except Exception as e:
        return JSONResponse({"ok": False, "msg": "读金蝶失败：%s" % str(e)[:160]}, status_code=502)
    return d if d.get("ok") else JSONResponse(d, status_code=400)


def _fee_post(inst, b, user):
    d = _fee_draft(inst)
    if not d.get("ok"):
        raise RuntimeError(d.get("msg"))
    if not b.get("checked"):
        raise RuntimeError("要先核对科目和维度并打勾确认")
    s, conf = kc.login()
    allf = dict(db.get_setting(_FEE_KEY, None) or {})
    old = allf.get(inst)
    if old and kc.view_voucher(old.get("vid"), s, conf).get("exists"):
        raise RuntimeError("这张请款单的费用凭证 记-%s 已经生成过了" % old.get("vno"))
    if d["exist"] and not b.get("force"):
        raise RuntimeError("金蝶里已经有这家还没用过的费用凭证（%s），多半已经做过了；确实要再做一张请勾「仍然新建」" %
                           "、".join("%d/%s# %.2f" % (x["month"], x["vno"], x["gross"]) for x in d["exist"]))
    req = d["req"]
    gross = round(req["amount"], 2)
    if gross <= 0:
        raise RuntimeError("请款金额不对")
    dims = {}
    for k, need in (("acct", True), ("dept", True), ("fee", True), ("biz", False), ("proj", False)):
        code, name = LV._split_code(b.get(k))
        if need and not code:
            raise RuntimeError("%s没填" % {"acct": "科目", "dept": "部门", "fee": "费用项目"}[k])
        dims[k] = (code, name)
    expl = str(b.get("expl") or "").strip()
    if not expl:
        raise RuntimeError("摘要没填")
    if "计提" in expl:
        raise RuntimeError("这是直接做账的费用凭证，摘要不要写「计提」（系统靠摘要分辨计提和直接做账）")
    # 税：有能抵扣的票→待认证(每张票一行)；没票→暂估(按填的税率)；有票但不能抵扣→全额进费用
    _, invs = _invoices(inst)
    slip = None if invs else _later_slip(inst)
    if not invs and not slip:
        raise RuntimeError("票夹里还没有发票，发票管家里也没有登记后补单：没票做账要先登记发票后补单（申请人自助登记，或财务在后补池登记）")
    ded = [i for i in invs if i.get("deduct")]
    tax_lines, tax = [], 0.0
    if invs:
        if abs(sum(i["gross"] for i in invs) - gross) >= 0.005:
            raise RuntimeError("票夹里发票含税合计 %.2f ≠ 请款金额 %.2f，先把发票弄对" % (sum(i["gross"] for i in invs), gross))
        for i in ded:
            if float(i.get("tax") or 0):
                tax_lines.append(("2221.01.06", "%s%s" % (i["number"], expl), round(float(i["tax"]), 2)))
        tax = round(sum(t for _, _, t in tax_lines), 2)
    else:
        try:
            rate = float(str(b.get("rate") if b.get("rate") not in (None, "") else 0))
        except ValueError:
            raise RuntimeError("税率要填数字")
        rate = rate / 100.0                 # 页面按百分数填(1 = 1%)
        if not 0 <= rate <= 0.13:
            raise RuntimeError("税率不对（0～13%）")
        tax = LV.split_gross(gross, rate)[1] if rate else 0.0
        if tax:
            tax_lines.append(("2221.01.07", expl, tax))
    net = round(gross - tax, 2)
    book = req["subject_full"]
    bcode = {o.get("full_name"): o.get("book_code") for o in (db.list_orgs() or [])}.get(book)
    if not bcode:
        raise RuntimeError("主体档案里没有「%s」的账簿编码" % book)
    exp_dims = {"FDETAILID__FFLEX5": {"FNumber": dims["dept"][0]}, "FDETAILID__FFLEX9": {"FNumber": dims["fee"][0]}}
    if dims["biz"][0]:
        exp_dims["FDETAILID__FF100010"] = {"FNumber": dims["biz"][0]}
    if dims["proj"][0]:
        exp_dims["FDETAILID__FF100006"] = {"FNumber": dims["proj"][0]}
    ents = [dict(_KD_BASE, FEXPLANATION=expl, FACCOUNTID={"FNumber": dims["acct"][0]}, FDEBIT=net, FCREDIT=0, FDetailID=exp_dims)]
    for acct, e2, t in tax_lines:
        ents.append(dict(_KD_BASE, FEXPLANATION=e2, FACCOUNTID={"FNumber": acct}, FDEBIT=t, FCREDIT=0))
    ap = {"FDETAILID__FFLEX4": {"FNumber": req["code"]}}
    if d["defaults"].get("sup_grp"):
        ap["FDETAILID__FF100005"] = {"FNumber": d["defaults"]["sup_grp"]}
    ents.append(dict(_KD_BASE, FEXPLANATION=expl, FACCOUNTID={"FNumber": "2241.02"}, FDEBIT=0, FCREDIT=gross, FDetailID=ap))
    date, y, m = _xred_date(book, datetime.now().strftime("%Y-%m-%d"), s, conf)
    r = kc.save_voucher({"FACCOUNTBOOKID": {"FNumber": bcode}, "FVOUCHERGROUPID": {"FNumber": "PRE001"}, "FEntity": ents,
                         "FDate": date, "FYear": y, "FPeriod": m}, s, conf)
    info = kc.view_voucher(r["id"], s, conf)
    vno = info.get("vno") or ""
    sub_err = ""
    try:
        kc.submit_bill("GL_VOUCHER", r["id"], s, conf)
    except Exception as e:
        sub_err = str(e)[:160]
    rec = {"vid": r["id"], "vno": vno, "year": y, "month": m, "date": date, "book": book, "by": user, "at": _now(), "gross": gross, "net": net, "tax": tax,
           "tax_acct": tax_lines[0][0] if tax_lines else "", "expl": expl, "dims": {k: " ".join(x for x in v if x) for k, v in dims.items()},
           "submitted": not sub_err, "submit_err": sub_err, "later_id": (slip or {}).get("id")}
    allf[inst] = rec
    db.set_setting(_FEE_KEY, allf, user)
    _ACC_CACHE.clear()                      # 新凭证要马上能被候选/预览读到
    steps = ["费用凭证 记-%s 已建（%s，%s）：借 %s %.2f%s / 贷 2241.02 %.2f%s" % (
        vno, book[:7], date, dims["acct"][0], net, (" + 进项税 %.2f（%s）" % (tax, "待认证" if tax_lines and tax_lines[0][0].endswith("06") else "暂估")) if tax else "",
        gross, "、已提交，等人审核" if not sub_err else "，但提交失败：%s（可在金蝶手动提交）" % sub_err)]
    # 自动选定给这张请款单(没人选过才写)
    picks = dict(db.get_setting(_PICK_KEY, None) or {})
    if vno and not (picks.get(inst) or {}).get("picks"):
        picks[inst] = {"picks": [{"year": y, "month": m, "vno": vno}], "part": False, "memo": "", "by": user, "at": _now(), "auto": "fee"}
        db.set_setting(_PICK_KEY, picks, user)
        steps.append("已把这张凭证选定给这张请款单（付款时核销它）")
    db.audit(user, "物流付款做账-审核生成费用凭证", "记-%s" % vno, "%s %s %.2f；%s；%s%s" % (
        req["subject"], req["payee"], gross, " / ".join(rec["dims"][k] for k in ("acct", "dept", "fee", "biz", "proj") if rec["dims"].get(k)),
        "已提交" if not sub_err else "提交失败：" + sub_err, "；没票，关联发票后补单 #%s" % slip["id"] if slip else ""))
    return {"ok": True, "vno": vno, "steps": steps, "fee": rec}


@router.post("/api/logistics-voucher/fee-post")
async def fee_post(request: Request):
    """审核通过、人核对过维度后生成费用凭证并提交(不审核)。"""
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
        r = await run_in_threadpool(_fee_post, inst, b, u["name"])
    except Exception as e:
        r = {"ok": False, "msg": "费用凭证没生成：%s" % str(e)[:220]}
    finally:
        lk.release()
    return r if r.get("ok") else JSONResponse(r, status_code=400)


@router.post("/api/logistics-voucher/later")
async def set_later(request: Request):
    """人工确认「发票后补，先做付款凭证」(on=false 撤销)。只对已有付款单、票夹里还没有发票、还没写金蝶的单有意义；原因必填，留痕。"""
    u = _perm(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    b = await request.json()
    inst, note = str(b.get("inst") or ""), str(b.get("note") or "").strip()[:100]
    if inst in (db.get_setting(_POSTED_KEY, None) or {}):
        return JSONResponse({"ok": False, "msg": "这张已经写过金蝶，不能再改"}, status_code=400)
    allp = dict(db.get_setting(_LATER_KEY, None) or {})
    if b.get("on"):
        slip = _later_slip(inst)
        if not slip:
            return JSONResponse({"ok": False, "msg": "发票管家里这张请款单还没有登记后补单：先让申请人（或财务）在发票管家登记发票后补，才能没票先做付款凭证"}, status_code=400)
        note = note or "后补单 #%s%s" % (slip["id"], ("，预计 %s 到" % slip["expect_date"]) if slip.get("expect_date") else "")
        allp[inst] = {"by": u["name"], "at": _now(), "note": note, "slip": slip["id"]}
    else:
        allp.pop(inst, None)
    db.set_setting(_LATER_KEY, allp, u["name"])
    db.audit(u["name"], "物流付款做账-发票后补" + ("先付款" if b.get("on") else "撤销"), inst, note)
    return {"ok": True}


def _post_later(inst, user):
    """发票后补的第三笔：新建「暂估转待认证」凭证并提交(不审核)。借 2221.01.06 每张票一行(摘要＝发票号+核销M/N#+原摘要) / 贷 2221.01.07。
    只在这张的付款凭证已经写过金蝶(当时没票、只做了支付)、现在发票到了的时候做；做过了不重复。→ 步骤文字"""
    d, code = _preview_data(inst)
    if code != 200:
        raise RuntimeError(d.get("msg") or "读不到这张请款单")
    posted = dict(db.get_setting(_POSTED_KEY, None) or {})
    rec = dict(posted.get(inst) or {})
    if not rec.get("tax_later"):
        raise RuntimeError("这张不是「发票后补、只做了支付」的单")
    old = rec.get("later3")
    s, conf = kc.login()
    if old and kc.view_voucher(old.get("vid"), s, conf).get("exists"):
        raise RuntimeError("暂估转待认证凭证 记-%s 已经建过了" % old.get("vno"))
    l3 = d.get("later3") or {}
    if not l3.get("lines"):
        raise RuntimeError(l3.get("msg") or "票夹里还没有发票")
    if not l3.get("ok"):
        raise RuntimeError(l3.get("msg") or "借贷不平")
    book = d["req"]["subject_full"]
    code_ = {o.get("full_name"): o.get("book_code") for o in (db.list_orgs() or [])}.get(book)
    if not code_:
        raise RuntimeError("主体档案里没有「%s」的账簿编码" % book)
    date, y, m = _xred_date(book, datetime.now().strftime("%Y-%m-%d"), s, conf)
    ents = []
    for l in l3["lines"]:
        e = dict(_KD_BASE, FEXPLANATION=l["expl"], FACCOUNTID={"FNumber": l["acct"]}, FDEBIT=l["dr"], FCREDIT=l["cr"])
        dd = _kd_dims(l)
        if dd:
            e["FDetailID"] = dd
        ents.append(e)
    r = kc.save_voucher({"FACCOUNTBOOKID": {"FNumber": code_}, "FVOUCHERGROUPID": {"FNumber": "PRE001"}, "FEntity": ents,
                         "FDate": date, "FYear": y, "FPeriod": m}, s, conf)
    info = kc.view_voucher(r["id"], s, conf)
    sub_err = ""
    try:
        kc.submit_bill("GL_VOUCHER", r["id"], s, conf)
    except Exception as e:
        sub_err = str(e)[:160]
    rec["later3"] = {"vid": r["id"], "vno": info.get("vno") or "", "billno": r.get("billno"), "book": book, "date": date, "at": _now(), "by": user,
                     "tax": l3.get("dr"), "tail": l3.get("tail"), "submitted": not sub_err, "submit_err": sub_err}
    posted[inst] = rec
    db.set_setting(_POSTED_KEY, posted, user)
    db.audit(user, "物流付款做账-暂估转待认证凭证", "记-%s" % rec["later3"]["vno"], "%s 发票后补：%s，日期 %s；%s" % (
        d["req"]["payee"], "、".join(i["number"] for i in d["invoices"] if i.get("number")), date, "已提交" if not sub_err else "提交失败：" + sub_err))
    return ["暂估转待认证凭证 记-%s 已建（%s，税额 %.2f）%s" % (rec["later3"]["vno"], date, float(l3.get("dr") or 0),
                                                "、已提交，等人审核" if not sub_err else "，但提交失败：%s（可在金蝶手动提交）" % sub_err)]


@router.post("/api/logistics-voucher/post-later")
async def post_later(request: Request):
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
        r = {"ok": True, "steps": await run_in_threadpool(_post_later, inst, u["name"])}
    except Exception as e:
        r = {"ok": False, "msg": "暂估转待认证凭证没建成：%s" % str(e)[:200]}
    finally:
        lk.release()
    return r if r.get("ok") else JSONResponse(r, status_code=400)


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
