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
_ACC_CACHE = {}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _perm(request):
    return _require_perm(request, "logistics_upload")


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
    if not all(i["paper"] for i in invs) and r["inst_id"] not in ovr:
        return "paper"
    return "ready"


@router.get("/api/logistics-voucher/list")
def vlist(request: Request, since: str = "2026-09-01"):
    if not _perm(request):
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    ovr = db.get_setting(_OVR_KEY, None) or {}
    with db._engine.connect() as c:
        reqs = [dict(r) for r in c.execute(select(PR).where(PR.c.create_time >= since)).mappings().all()]
    out = []
    for r in reqs:
        if r.get("excluded") or r.get("dt_status") == "TERMINATED" or r.get("dt_result") == "refuse":
            continue
        folder, invs = _invoices(r["inst_id"])
        pi = _paid_info(r) or {}
        out.append({"inst": r["inst_id"], "bid": r.get("business_id"), "carrier": r.get("carrier"), "code": r.get("sup_code"),
                    "payee": r.get("payee"), "subject": r.get("subject"), "amount": r.get("amount"), "period": r.get("period") or "",
                    "applicant": r.get("applicant"), "created": r.get("create_time"), "paid": pi.get("date") or "",
                    "paid_voucher": pi.get("voucher") or "", "folder": folder["id"] if folder else None,
                    "n_inv": len(invs), "inv_total": round(sum(i["gross"] for i in invs), 2),
                    "n_paper": sum(1 for i in invs if i["paper"]), "paper_ovr": ovr.get(r["inst_id"]),
                    "booked": sorted({str(v) for i in invs for v in (i["vouchers"] or [])})[:5],
                    "status": _status(r, folder, invs, ovr)})
    out.sort(key=lambda x: (x["created"] or ""), reverse=True)
    return {"ok": True, "rows": out}


# ---------- 金蝶：计提凭证 / 付款单 ----------
_F = [("FVOUCHERGROUPNO", "号"), ("FEXPLANATION", "摘要"), ("FAccountID.FNumber", "科目"), ("FAccountID.FName", "科目名"),
      ("FDEBIT", "借"), ("FCREDIT", "贷"), ("FDetailID.FFLEX4.FNumber", "供应商码"), ("FDetailID.FFLEX4.FName", "供应商"),
      ("FDetailID.FFLEX5.FNumber", "部门码"), ("FDetailID.FFLEX5.FName", "部门"), ("FDetailID.FFLEX9.FNumber", "费用码"),
      ("FDetailID.FFLEX9.FName", "费用"), ("FDetailID.FF100010.FNumber", "分类码"), ("FDetailID.FF100010.FDataValue", "分类"),
      ("FDetailID.FF100006.FNumber", "项目码"), ("FDetailID.FF100006.FDataValue", "项目")]


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
                "biz": _s(r["分类"]), "proj_code": _s(r["项目码"]), "proj": _s(r["项目"])})
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


def _preview_data(inst):
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
           "paid": bool(pi) and not pi.get("voucher")}
    lines = LV.build(ctx, vouchers, inv_in, pl, fixes) if pl["status"] == "ok" else []
    dr, cr = LV.balance(lines)
    msgs = list(notes) + list(pl["msgs"])
    if pi.get("voucher"):
        msgs.append("这笔付款已经在金蝶 %s 记过支付凭证，本张不再出支付分录" % pi["voucher"])
    if not pi:
        msgs.append("还没付款（金蝶没有付款单）：先出红冲/更正/核销预览，付款后才加支付分录")
    if pi.get("bill_id") and not bank:
        msgs.append("金蝶付款单没取到我方银行账号，银行存款那行的账号待补")
    st = _status(r, folder, invs, ovr)
    kind, ktext = _kind(vouchers, notes, pl)
    return {"ok": True, "kind": kind, "kind_text": ktext, "req": {"inst": inst, "bid": r.get("business_id"), "carrier": r.get("carrier"), "payee": r.get("payee"),
                                "code": r.get("sup_code"), "subject": r.get("subject"), "subject_full": r.get("subject_full"),
                                "amount": r.get("amount"), "period": r.get("period"), "applicant": r.get("applicant"),
                                "paid": pay_date if pi else "", "bank": bank, "folder": folder["id"] if folder else None,
                                "paper_ovr": ovr.get(inst), "status": st},
            "invoices": invs,
            "accruals": [{"vno": v["vno"], "expl": v["expl"], "gross": v["gross"], "net": v["net"], "tax": v["tax"], "rate": v["rate"],
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
                out[inst] = {"kind": d.get("kind"), "text": d.get("kind_text")} if code == 200 else {"kind": "err", "text": d.get("msg")}
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
