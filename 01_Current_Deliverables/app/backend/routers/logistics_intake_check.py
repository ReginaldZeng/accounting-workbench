# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-17 | Author: Claude Opus 5.5 | Version: V2.989
# Description: 物流账单复核台·第①步「数据接入」接口(用户 2026-10-17 定复核台四步：①数据接入 ②逐笔计提 ③逐单核价核量 ④发票审核)。
#   GET  /api/logistics-review/intake           一家一个月：账单原件从哪来、解析记录、每页读了哪些列·钱和账单合计对不对得上、请款单合计
#   GET  /api/logistics-review/intake-overview  一个月各家：请款单、原件、读出来的、和请款单差、钱怎么取、读全没有
#   POST /api/logistics-review/reparse          用存的原件按现在的取数说明再解析一遍(人工改的归类、备注保留；已登记复核的月份不许)
#   检查逻辑在 kernels/logistics_intake.sheet_report(只读、不落库)。报告按 原件sha×取数说明 缓存，原件或取数说明一变就重算。
import hashlib
import json
import threading

from fastapi import APIRouter, Request
from sqlalchemy import select, update, delete, insert, text

from core import JSONResponse, db
from kernels import logistics_intake as intake
from kernels import logistics_review_store as store
from routers import logistics_review as LR

router = APIRouter()
BL, BR, SP, PR = LR.BL, LR.BR, LR.SP, store.payreq
_REP = {}                   # {(承运商, 账期, 份名, 原件sha, 取数说明sha): 报告}
_REP_LOCK = threading.Lock()


def _spec_sha(spec):
    s = dict(spec or {})
    s.pop("period", None)      # 账期是请求级塞进来的，不属于取数说明内容
    return hashlib.sha256(json.dumps(s, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _how(spec):
    """取数说明里钱是怎么取的 → (说法, 有没有合计兜底)。"""
    det = [s for s in (spec or {}).get("sheets") or [] if s.get("role") == "detail"]
    if not det:
        acc = [s for s in (spec or {}).get("sheets") or [] if s.get("role") == "accrual"]
        return ("只有汇总页（按费用项）" if acc else "没有逐单明细页"), False
    kinds = []
    for s in det:
        k = "拆项相加" if s.get("fee_parts") else ("几列相加" if len(s.get("amount_cols") or []) > 1
                                              else "直接读账单的金额列" if (s.get("amount_col") or s.get("amount_cols")) else "没写算钱的列")
        if k not in kinds:
            kinds.append(k)
    tot = any(s.get("total_col") for s in det)
    return "、".join(kinds) + ("＋合计兜底" if tot else ""), tot


def _raws(carrier, period, with_data=False):
    cols = [BR.c.id, BR.c.bill_src, BR.c.name, BR.c.origin, BR.c.sha256, BR.c.size, BR.c.created_by, BR.c.created_at]
    if with_data:
        cols.append(BR.c.data)
    with db._engine.connect() as c:
        return [dict(r) for r in c.execute(select(*cols).where((BR.c.carrier == carrier) & (BR.c.period == period)).order_by(BR.c.id)).mappings().all()]


def _report_db_get(raw_id, sha):
    try:
        with db._engine.connect() as c:
            row = c.execute(select(store.intake_reports.c.report_json).where(
                (store.intake_reports.c.raw_id == raw_id) & (store.intake_reports.c.spec_sha == sha))).first()
        return json.loads(row[0]) if row else None
    except Exception:
        return None


def _report_db_put(raw_id, sha, rep):
    """同一原件只留一份报告（按 raw_id 覆盖），取数说明变了 sha 也变、覆盖成新口径。"""
    try:
        from datetime import datetime
        with db._engine.begin() as c:
            c.execute(delete(store.intake_reports).where(store.intake_reports.c.raw_id == raw_id))
            c.execute(insert(store.intake_reports).values(raw_id=raw_id, spec_sha=sha,
                        report_json=json.dumps(rep, ensure_ascii=False), updated_at=datetime.now().strftime("%Y-%m-%d %H:%M")))
    except Exception:
        pass


def warm_report(carrier, period, src, spec, data):
    """导入/重解析成功后顺手把核对报告算好落库（V2.991）：第①步打开直接读库，不用现算。
    src 对不上（还没存进 bill_raw）就静默跳过，首次打开页面时兜底算。"""
    try:
        with db._engine.connect() as c:
            rid = c.execute(select(BR.c.id).where((BR.c.carrier == carrier) & (BR.c.period == period) &
                        ((BR.c.bill_src == src) if src else ((BR.c.bill_src.is_(None)) | (BR.c.bill_src == "")))
                        ).order_by(BR.c.id.desc())).scalar()
        if not rid:
            return
        try:
            rep = intake.sheet_report(dict(spec), bytes(data), period)
        except Exception as e:
            rep = {"err": "按现在的取数说明解析不了：%s" % str(e)[:120], "sheets": [], "verdict": "err"}
        _report_db_put(rid, _spec_sha(spec), rep)
        with _REP_LOCK:                     # 内存缓存也暖上
            if len(_REP) > 300:
                _REP.clear()
            _REP[(carrier, period, src or "", "", _spec_sha(spec))] = rep
    except Exception:
        pass


def _report(carrier, period, raw, spec):
    sha = _spec_sha(spec)
    k = (carrier, period, raw.get("bill_src") or "", raw.get("sha256") or "", sha)
    with _REP_LOCK:
        hit = _REP.get(k)
    if hit is not None:
        return hit
    rid = raw.get("id")
    rep = _report_db_get(rid, sha) if rid else None
    if rep is None:
        try:
            rep = intake.sheet_report(dict(spec), bytes(raw["data"]), period)
        except Exception as e:
            rep = {"err": "按现在的取数说明解析不了：%s" % str(e)[:120], "sheets": [], "verdict": "err"}
        if rid:
            _report_db_put(rid, sha, rep)
    with _REP_LOCK:
        if len(_REP) > 300:
            _REP.clear()
        _REP[k] = rep
    return rep


def _payreqs(carrier, period):
    """这家这月算数的钉钉请款单(撤回/拒绝/排除的不算)。"""
    with db._engine.connect() as c:
        rows = [dict(r) for r in c.execute(select(PR.c.inst_id, PR.c.business_id, PR.c.subject, PR.c.amount, PR.c.applicant, PR.c.create_time,
                                                  PR.c.files_json, PR.c.bill_state, PR.c.bill_msg, PR.c.excluded, PR.c.dt_status, PR.c.dt_result).where(
            (PR.c.carrier == carrier) & (PR.c.period == period))).mappings().all()]
    return [r for r in rows if not r.get("excluded") and str(r.get("dt_status") or "").upper() != "TERMINATED"
            and str(r.get("dt_result") or "").lower() != "refuse"]


def _history(carrier, period):
    try:
        with db._engine.connect() as c:
            rs = c.execute(text("select ts, operator, action, detail from audit_log where target=:t and action in "
                                "('物流复核-解析账单','物流复核-删除账单','物流复核-用原件重新解析') order by id desc limit 20"),
                           {"t": "%s %s" % (carrier, period)}).all()
        return [{"at": str(r[0] or "")[:16], "by": r[1] or "", "action": r[2] or "", "detail": r[3] or ""} for r in rs]
    except Exception:
        return []


def intake_view(carrier, period):
    spec = LR._load_spec(carrier) or {}
    how, tot = _how(spec)
    raws = _raws(carrier, period, with_data=bool(spec))
    files = []
    for r in raws:
        rep = _report(carrier, period, r, spec) if spec and r.get("data") else None
        files.append({"id": r["id"], "src": r.get("bill_src") or "", "name": r.get("name") or "", "origin": r.get("origin") or "",
                      "size": r.get("size"), "by": r.get("created_by") or "", "at": str(r.get("created_at") or "")[:16], "report": rep})
    bills = LR._bill_sources(carrier, period)
    reqs = _payreqs(carrier, period)
    req_files = []
    for q in reqs:
        for f in json.loads(q.get("files_json") or "[]"):
            req_files.append({"inst": q["inst_id"], "bid": q.get("business_id"), "subject": q.get("subject"), "name": f.get("fileName") or "",
                              "role": f.get("role") or "", "size": f.get("size"), "applicant": q.get("applicant") or "", "at": q.get("create_time") or ""})
    bill_total = round(sum(b["amount"] for b in bills), 2)
    req_total = round(sum(float(q.get("amount") or 0) for q in reqs), 2)
    vs = [f["report"]["verdict"] for f in files if f.get("report")]
    rank = {"err": 5, "gap": 4, "maybe": 3, "unsplit": 2, "nocheck": 1, "ok": 0}
    sg = LR._signed(carrier, period)
    return {"ok": True, "carrier": carrier, "period": period, "has_spec": bool(spec), "how": how, "total_col": tot,
            "register": spec.get("review_mode") == "register", "note": spec.get("note") or "",
            "files": files, "bills": bills, "bill_total": bill_total,
            "payreqs": [{"inst": q["inst_id"], "bid": q.get("business_id"), "subject": q.get("subject"), "amount": q.get("amount"),
                         "applicant": q.get("applicant"), "at": q.get("create_time"), "bill_state": q.get("bill_state"), "bill_msg": q.get("bill_msg")} for q in reqs],
            "req_files": req_files, "req_total": req_total, "diff": round(bill_total - req_total, 2) if reqs and bills else None,
            "verdict": max(vs, key=lambda v: rank.get(v, 0)) if vs else ("nobill" if not bills else "noraw"),
            "history": _history(carrier, period),
            "locked": ("本月已登记复核（%s %s）" % (sg.get("reviewer") or "", sg.get("signed_at") or "")) if sg else ""}


@router.get("/api/logistics-review/intake")
async def review_intake(request: Request, carrier: str = "", period: str = ""):
    if not LR._perm(request):
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    if not carrier or not period:
        return JSONResponse({"ok": False, "msg": "缺承运商/账期"}, status_code=400)
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(intake_view, carrier, period)


def overview(period):
    with db._engine.connect() as c:
        specs = {a: json.loads(b or "{}") for a, b in c.execute(select(SP.c.carrier, SP.c.spec_json)).all()}
        pr_carriers = {r[0] for r in c.execute(select(PR.c.carrier).where(PR.c.period == period)).all() if r[0]}
        raw_carriers = {r[0] for r in c.execute(select(BR.c.carrier).where(BR.c.period == period)).all() if r[0]}
    rows = []
    for ca in sorted(set(specs) | pr_carriers | raw_carriers):
        spec = specs.get(ca) or {}
        how, tot = _how(spec) if spec else ("没有取数说明", False)
        reqs = _payreqs(ca, period)
        bills = LR._bill_sources(ca, period)
        raws = _raws(ca, period, with_data=bool(spec))
        if not spec and not reqs and not raws and not bills:
            continue          # 没取数说明、这个月也什么都没有的(撤回的请款单留下的名字)，不列
        reps = [_report(ca, period, r, spec) for r in raws if spec and r.get("data")]
        bill_total = round(sum(b["amount"] for b in bills), 2) if bills else None
        req_total = round(sum(float(q.get("amount") or 0) for q in reqs), 2) if reqs else None
        vs = [r.get("verdict") for r in reps]
        rank = {"err": 5, "gap": 4, "maybe": 3, "unsplit": 2, "nocheck": 1, "ok": 0}
        if not spec:
            st = "nospec"
        elif spec.get("review_mode") == "register":
            st = "register"
        elif not bills and not raws:
            st = "wait" if not reqs else "nobill"
        elif vs:
            st = max(vs, key=lambda v: rank.get(v, 0))
        else:
            st = "noraw"
        rows.append({"carrier": ca, "how": how, "total_col": tot, "multi": len([s for s in spec.get("sheets") or [] if s.get("role") == "detail"]),
                     "n_req": len(reqs), "req_total": req_total, "n_raw": len(raws), "bill_total": bill_total,
                     "diff": round(bill_total - req_total, 2) if bill_total is not None and req_total is not None else None,
                     "status": st, "risky": bool(spec) and not tot and any(
                         s.get("fee_parts") or len(s.get("amount_cols") or []) > 1 for s in spec.get("sheets") or [] if s.get("role") == "detail")})
    return {"ok": True, "period": period, "rows": rows}


@router.get("/api/logistics-review/intake-overview")
async def review_intake_overview(request: Request, period: str = ""):
    if not LR._perm(request):
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    if not period:
        return JSONResponse({"ok": False, "msg": "缺账期"}, status_code=400)
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(overview, period)


def reparse(carrier, period, src, operator):
    """用存的原件按现在的取数说明再解析一遍。人工改的归类 import_bill 本来就按单号带过去；备注这里按单号写回。"""
    lk = LR._locked(carrier, period)
    if lk:
        return lk
    raws = [r for r in _raws(carrier, period, with_data=True) if (r.get("bill_src") or "") == (src or "")]
    if not raws:
        return JSONResponse({"ok": False, "msg": "这一份账单没存原件（V2.843 之前导入的没存），请重新上传"}, status_code=404)
    raw = raws[-1]
    with db._engine.connect() as c:
        old = [dict(r) for r in c.execute(select(BL.c.doc_no, BL.c.note, BL.c.amount, BL.c.grain).where(LR._src_scope(carrier, period, src))).mappings().all()]
    before = round(sum(float(r["amount"] or 0) for r in old if r["grain"] == "detail"), 2)
    notes = {}
    for r in old:
        if r.get("doc_no") and (r.get("note") or "").strip():
            notes.setdefault(r["doc_no"], r["note"])
    res = LR.import_bill(carrier, period, bytes(raw["data"]), operator, origin="用存的原件重新解析", fname=raw.get("name") or "")
    if not res.get("ok"):
        return JSONResponse(res, status_code=400)
    if notes:
        with db._engine.begin() as c:
            for dno, nt in notes.items():
                c.execute(update(BL).where(LR._src_scope(carrier, period, (res.get("bill_src") or "").strip()) & (BL.c.doc_no == dno)).values(note=nt))
    with db._engine.connect() as c:
        after = round(sum(float(r[0] or 0) for r in c.execute(select(BL.c.amount).where(
            LR._src_scope(carrier, period, (res.get("bill_src") or "").strip()) & (BL.c.grain == "detail"))).all()), 2)
    db.audit(operator, "物流复核-用原件重新解析", "%s %s" % (carrier, period),
             "「%s」%s：明细 %d 行，合计 %.2f → %.2f；备注写回 %d 张" % (src or "（未标份名）", raw.get("name") or "", res.get("detail") or 0, before, after, len(notes)))
    LR._bust(carrier, period)
    return {"ok": True, "before": before, "after": after, "detail": res.get("detail"), "notes": len(notes)}


@router.post("/api/logistics-review/reparse")
async def review_reparse(request: Request):
    u = LR._perm(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    b = await request.json()
    carrier, period, src = (b.get("carrier") or "").strip(), (b.get("period") or "").strip(), (b.get("src") or "").strip()
    if not carrier or not period:
        return JSONResponse({"ok": False, "msg": "缺承运商/账期"}, status_code=400)
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(reparse, carrier, period, src, LR._uname(u))
