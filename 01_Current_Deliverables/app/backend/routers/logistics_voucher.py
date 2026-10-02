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
        out.append({"id": x["id"], "number": x["number"] or "", "type": x["type_label"] or x["inv_type"] or "",
                    "gross": float(x["total"] or 0), "net": float(x["amount"] or 0), "tax": float(x["tax"] or 0),
                    "rate": x["tax_rate"] or "", "paper": bool(x["paper"]), "review": x["review"] or "",
                    "booked": bk.get("status") == "booked", "vouchers": bk.get("vouchers") or [],
                    "seller": x["seller_name"] or "", "buyer": x["buyer_name"] or ""})
    return dict(f), out


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
                                                                         "to_amt_tax", "to_rate", "to_amt", "memo")}, "snap": snap})
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
        return "subj", subj
    if not vouchers:
        return "noacc", "金蝶本期没找到这家的计提凭证"
    if pl["status"] != "ok":
        return "manual", (pl["msgs"] or ["要人工处理"])[0]
    redo = [(k, p) for k, p in pl["per"].items() if p.get("mode") in ("rate", "fix")]
    if redo:
        return "redo", "需红冲更正 %d 张：%s" % (len(redo), "；".join("记-%s %s" % (k, p.get("why") or "") for k, p in redo))
    if pl["tails"]:
        return "tail", "计提与发票一致，核销（含尾差 %s）" % "、".join("%.2f" % d for d in pl["tails"].values())
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
    if invs and abs(sum(v["gross"] for v in vouchers) - inv_tot) > 0.004:
        for o in db.list_orgs() or []:
            ob = o.get("full_name")
            if not ob or ob == r["subject_full"]:
                continue
            try:
                ov, _ = _accruals(ob, r["period"], r["sup_code"])
            except Exception:
                continue
            hit = LV._subset(ov, inv_tot) if ov else None
            if hit:
                notes.append("计提记在了「%s」的账上（%s），主体记错：要先在那边红冲、在本主体重新计提，再做这张" %
                             (o.get("short_name") or ob, "、".join("记-%s %.2f" % (v["vno"], v["gross"]) for v in hit)))
                break
    fixes = _fixes(r["carrier"], r["period"], r["subject"])
    fixes = {k: v for k, v in fixes.items() if any(x["vno"] == k for x in vouchers)}
    inv_in = [{"number": i["number"], "rate": i["rate"], "gross": i["gross"], "tax": i["tax"]} for i in invs]
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
    return {"ok": True, "kind": kind, "kind_text": ktext, "req": {"inst": inst, "bid": r.get("business_id"), "carrier": r.get("carrier"), "payee": r.get("payee"),
                                "code": r.get("sup_code"), "subject": r.get("subject"), "subject_full": r.get("subject_full"),
                                "amount": r.get("amount"), "period": r.get("period"), "applicant": r.get("applicant"),
                                "paid": pay_date if pi else "", "bank": bank, "folder": folder["id"] if folder else None,
                                "paper_ovr": ovr.get(inst), "status": st, "posted": (db.get_setting(_POSTED_KEY, None) or {}).get(inst),
                                "bill_id": pi.get("bill_id") or ""},
            "invoices": invs,
            "accruals": [{"vno": v["vno"], "month": v["month"], "expl": v["expl"], "gross": v["gross"], "net": v["net"], "tax": v["tax"], "rate": v["rate"],
                          "fee": "、".join(dict.fromkeys(l.get("fee") or l.get("acct_name") or "" for l in v["exp_lines"])),
                          "biz": "、".join(dict.fromkeys(l.get("biz") for l in v["exp_lines"] if l.get("biz"))),
                          "tail": pl["tails"].get(v["vno"]),
                          **(pl["per"].get(v["vno"]) or {"mode": "", "new_rate": None, "why": ""})} for v in vouchers],
            "plan": {"status": pl["status"], "msgs": msgs, "tails": pl["tails"]},
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
                    out[inst] = {"kind": d.get("kind"), "text": d.get("kind_text"),
                                 "acc": [{k: a.get(k) for k in ("vno", "month", "fee", "biz", "tail", "expl", "gross", "rate", "mode", "new_rate", "why")}
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
#   金蝶允许改自动凭证；新加的行排在支付行后面(行号不随顺序变)，用户同意「支付在前」。凭证本身不审核，留给人在金蝶审。
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
    m2 = kc._post(s, conf, kc.VIEW_SVC, ["GL_VOUCHER", json.dumps({"Id": str(vid)})]).json()["Result"]["Result"]
    rec = {"bill_no": pb["单号"], "vid": vid, "vno": vno, "book": book, "at": _now(), "by": user,
           "dr": m2.get("DEBITTOTAL"), "cr": m2.get("FCREDITTOTAL"), "lines": len(new) + 2}
    posted = dict(db.get_setting(_POSTED_KEY, None) or {})
    posted[inst] = rec
    db.set_setting(_POSTED_KEY, posted, user)
    with db._engine.begin() as c:
        c.execute(text("update logistics_payreq set kd_paid=:v where inst_id=:i"), {"v": "%s|C|%s" % (str(pb["日期"])[:10], fid), "i": inst})
    db.audit(user, "物流付款做账-写入金蝶凭证", "记-%s" % vno, "%s 付款单 %s；补 %d 行；借 %s 贷 %s" % (book, pb["单号"], len(new), rec["dr"], rec["cr"]))
    steps.append("凭证 记-%s 补 %d 行、改支付摘要" % (vno, len(new)))
    return {"ok": True, "vno": vno, "bill_no": pb["单号"], "steps": steps, "dr": rec["dr"], "cr": rec["cr"]}


@router.post("/api/logistics-voucher/post")
async def post_to_kingdee(request: Request):
    """保存到金蝶：审核付款单 → 金蝶自动出付款凭证 → 往里补红冲/更正/核销分录、改支付摘要。凭证不审核。"""
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
