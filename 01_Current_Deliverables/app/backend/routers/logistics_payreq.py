# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-01 | Author: Claude Opus 5.5 | Version: V2.730
# Description: 【物流复核·钉钉请款单接入】用户 2026-10-01 定：自动扫钉钉「付款申请（公对公）」**全模板**(不是谁的待办)，
#   收款方是金蝶「物流运输服务…」供应商的留下(其余直接丢、不入库)，落到总表 承运商×主体×月份 一格，显示钉钉节点进度：
#   已提交/待谁审批/待我审批/已通过·待付款/已付款(金蝶出现付款单＝已付款)。
#   上线后提交的单(auto=1)：发票自动进发票管家(建票夹拉附件)；表单里的账单 xlsx 自动进复核台(这家这月还没账单才导，
#   已有账单不覆盖——账单/发票都要可替换，出错不会重走流程，工作台为准)；审批人评论里补传的 = 审核留档。
#   上线前的老单只显示进度，可手工「拉进来」。每 20 分钟扫一轮(新提交 + 还在审批中的)，本机 sqlite 不跑。
#   纯函数在 kernels/logistics_payreq；表在 kernels/logistics_review_store(payreq / payreq_file)。
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, Response
from sqlalchemy import select, insert, update

from core import JSONResponse, _require_perm, db
import kingdee_client as kc
from kernels import logistics_review_store as store
from kernels import logistics_payreq as lpq
from kernels import invoice_dingtalk as idt

router = APIRouter()
PR, PF = store.payreq, store.payreq_file
BL = store.bill_lines

_SCAN_LOCK = threading.Lock()
_NONLOGI = set()                                  # 看过、收款方不是物流供应商的实例号(进程内记着，不再取)
_SUP = {"ts": 0.0, "name2code": {}, "code2name": {}}
_SET_LAST, _SET_SINCE = "logi_payreq_last", "logi_payreq_auto_since"
_SET_PAYWARN = "logi_payreq_paywarn"     # {inst_id: {…}} 金蝶有付款单、金额主体收款方都对得上，但往来单位编码选错了(V2.825)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _perm(request):
    return _require_perm(request, "logistics_upload")


def _is_local():
    try:
        return str(db.DB_URL).startswith("sqlite")
    except Exception:
        return False


# ---------- 主数据：金蝶物流供应商 / 主体简称 / 复核台承运商简称 ----------
def _kd_suppliers(force=False):
    """金蝶 BD_Supplier 编码「物流运输服务…」→ {name2code, code2name}，缓存 12 小时。"""
    if not force and _SUP["name2code"] and time.time() - _SUP["ts"] < 12 * 3600:
        return _SUP
    try:
        s, conf = kc.login()
        rows = kc._query(s, conf, "BD_Supplier", [("FNumber", "码"), ("FName", "名")], "FNumber like '物流运输服务%'")
    except Exception:
        rows = []
    if rows:
        n2c, c2n = {}, {}
        for r in rows:
            c, n = str(r.get("码") or "").strip(), str(r.get("名") or "").strip()
            if c and n:
                n2c.setdefault(n, c)
                c2n.setdefault(c, n)
        _SUP.update(ts=time.time(), name2code=n2c, code2name=c2n)
    return _SUP


def _subj_short(full):
    for o in db.list_orgs() or []:
        if o.get("full_name") == full:
            return o.get("short_name") or full
    return full


def carrier_short(full, code=""):
    """金蝶全称/编码 → 复核台承运商简称(物流供应商档案)；没建档就用全称(与总表 sup_short 同口径)。"""
    sup = db.list_logi_suppliers() or []
    for s in sup:
        if code and s.get("kd_code") == code:
            return s.get("short")
    for s in sup:
        if s.get("full") and s.get("full") == full:
            return s.get("short")
    for s in sup:
        sf = s.get("full") or ""
        if sf and full and (sf.startswith(full) or full.startswith(sf) or full in sf or sf in full):
            return s.get("short")
    return full


def _has_spec(carrier):
    with db._engine.connect() as c:
        return c.execute(select(store.intake_spec.c.id).where(store.intake_spec.c.carrier == carrier)).first() is not None


# ---------- 扫描 ----------
def _accr_maps(periods):
    """{period: {(主体简称, 供应商编码): 计提含税}}（借总表的 2241 缓存）。"""
    from routers import logistics_review as LR
    out = {}
    for p in periods:
        try:
            rows, _, _ = LR._ov_kd_rows(p)
            accr, _ = LR._ov_accr(rows, p)
            out[p] = {k: round(v, 2) for k, v in accr.items()}
        except Exception:
            out[p] = {}
    return out


def _paybills(since_date):
    """金蝶付款单(物流供应商) → [{code, org, amount, date, status}]。进金蝶＝已付款(用户 2026-10-01)。"""
    try:
        s, conf = kc.login()
        rows = kc._query(s, conf, "AP_PAYBILL",
                         [("FID", "id"), ("FCONTACTUNIT.FNumber", "码"), ("FPAYORGID.FName", "组织"), ("FPAYTOTALAMOUNTFOR", "金额"),
                          ("FDate", "日期"), ("FDOCUMENTSTATUS", "状态")],
                         "FCONTACTUNIT.FNumber like '物流运输服务%%' and FDate>='%s'" % since_date)
    except Exception:
        return None
    return [{"id": r.get("id"), "code": r.get("码"), "org": r.get("组织"), "amount": r.get("金额"), "date": str(r.get("日期") or "")[:10],
             "status": r.get("状态")} for r in rows]


def _pay_code_warn():
    """已通过、却配不上付款单的请款单：再按「收款方名称 + 付款主体 + 金额」去金蝶找付款单——找得到说明出纳把往来单位编码选错了
    (实证 路凯 2026-09-28 深圳星期零 1,002.09：同一家公司有 物流运输服务074 / 其他押金022 两个编码，付款单选成了押金那个；
     计提挂在 074 上，照这样审核付款凭证会冲到 022，074 的计提核销不掉)。
    结果存 _SET_PAYWARN，付款做账页据此把这张单亮出来并拦住(用户 2026-10-05「这个在做凭证的时候你得提醒出来」)。只读金蝶。"""
    with db._engine.connect() as c:
        allr = [dict(r) for r in c.execute(select(PR.c.inst_id, PR.c.payee, PR.c.subject_full, PR.c.sup_code, PR.c.amount, PR.c.create_time,
                                                  PR.c.kd_paid, PR.c.excluded, PR.c.dt_status, PR.c.dt_result)).mappings().all()]
    wait = [r for r in allr if r.get("dt_status") == "COMPLETED" and r.get("dt_result") == "agree" and not r.get("kd_paid")
            and not r.get("excluded") and r.get("payee") and r.get("amount") is not None]
    old = db.get_setting(_SET_PAYWARN, None) or {}
    warn = {}
    if wait:
        taken = {str(r["kd_paid"]).split("|")[-1] for r in allr if r.get("kd_paid")}
        cond = " or ".join("FCONTACTUNIT.FName='%s'" % str(n).replace("'", "") for n in sorted({r["payee"] for r in wait}))
        s, conf = kc.login()
        rows = kc._query(s, conf, "AP_PAYBILL",
                         [("FID", "id"), ("FBillNo", "单号"), ("FDate", "日期"), ("FDOCUMENTSTATUS", "状态"), ("FPAYORGID.FName", "组织"),
                          ("FCONTACTUNIT.FNumber", "码"), ("FCONTACTUNIT.FName", "供应商"), ("FPAYTOTALAMOUNTFOR", "金额"), ("FCREATORID.FName", "创建人")],
                         "(%s) and FDate>='%s'" % (cond, min(str(r.get("create_time") or "")[:10] for r in wait)))
        bills = {}
        for b in rows:
            bills.setdefault(str(b.get("id")), b)
        used = set()
        for r in sorted(wait, key=lambda x: str(x.get("create_time") or "")):
            for bid, b in bills.items():
                if bid in taken or bid in used or b.get("码") == r.get("sup_code"):
                    continue
                if b.get("供应商") != r["payee"] or b.get("组织") != r.get("subject_full"):
                    continue
                if abs(float(b.get("金额") or 0) - float(r["amount"])) >= 0.005 or str(b.get("日期") or "")[:10] < str(r.get("create_time") or "")[:10]:
                    continue
                used.add(bid)
                warn[r["inst_id"]] = {"bill_id": bid, "bill_no": str(b.get("单号") or "").strip(), "date": str(b.get("日期") or "")[:10],
                                      "status": b.get("状态"), "code": b.get("码"), "want": r.get("sup_code"), "name": b.get("供应商"),
                                      "creator": b.get("创建人") or "", "amount": float(b.get("金额") or 0)}
                break
    if warn != old:
        db.set_setting(_SET_PAYWARN, warn, "系统")
        for i, w in warn.items():
            if i not in old:
                db.audit("系统", "物流请款单-付款单编码不对", i, "%s %.2f：金蝶付款单往来单位是 %s，应为 %s（%s 建，%s）" % (
                    w["name"], w["amount"], w["code"], w["want"], w["creator"], w["date"]))
    return warn


def _paid_vouchers(since_date, want=None):
    """金蝶支付凭证(2241 借方、挂物流供应商的支付行) → 同 _paybills 结构。
    付款单 9 月下旬才开始自动进金蝶，之前付过的只能从支付凭证认(实证 孝感 8月记-221 易风达 12,091)。
    V2.823(实证 比翼 6 月账单：深圳星期零 8月记-262，请款 6,150.02，凭证里 2241 借 8,150.02 = 预付款核销 2,000 + 银行 6,150.02，
    摘要「陈慧娴厦门比翼…6月线上零售出库运费」没写「提起支付」——原来认不出，一直挂成「已通过没付款」)：
    ① 支付行不再硬要「提起支付」四个字：不是计提/红冲/更正/核销的 2241 借方就算；
    ② want={(供应商码, 账簿)}：这些还在等付款的，顺带取同一张凭证的银行贷方合计(alt)——有预付款/押金抵扣时请款金额等于银行实付、不等于 2241 借方。"""
    try:
        s, conf = kc.login()
        rows = kc._query(s, conf, "GL_VOUCHER",
                         [("FACCOUNTBOOKID.FName", "账簿"), ("FDate", "日期"), ("FVOUCHERGROUPID.FName", "字"), ("FVOUCHERGROUPNO", "号"),
                          ("FEXPLANATION", "摘要"), ("FDEBIT", "借"), ("FDetailID.FFLEX4.FNumber", "码")],
                         "FAccountID.FNumber like '2241%%' and FDEBIT>0 and FDetailID.FFLEX4.FNumber like '物流运输服务%%' and FDate>='%s'" % since_date)
    except Exception:
        return None
    out, meta = [], []
    for r in rows:
        z = str(r.get("摘要") or "")
        if "提起支付" not in z and any(k in z for k in ("计提", "红冲", "更正", "核销", "冲回", "冲销")):
            continue
        vno = "%s-%s" % (str(r.get("字") or "记").strip(), str(r.get("号") or "").strip())
        gid = hashlib.md5(("%s|%s|%s" % (r.get("账簿"), str(r.get("日期"))[:7], vno)).encode("utf-8")).hexdigest()[:10]
        out.append({"id": "gl:" + gid, "code": r.get("码"), "org": r.get("账簿"),
                    "amount": r.get("借"), "date": str(r.get("日期") or "")[:10], "status": vno})
        meta.append((r.get("账簿"), str(r.get("日期") or "")[:10], str(r.get("号") or "").strip()))
    n = 0
    for i, (book, date, no) in enumerate(meta):
        if not want or (out[i]["code"], out[i]["org"]) not in want or n >= 30:
            continue
        n += 1
        try:
            bk = kc._query(s, conf, "GL_VOUCHER", [("FCREDIT", "贷")],
                           "FACCOUNTBOOKID.FName='%s' and FDate='%s' and FVOUCHERGROUPNO='%s' and FAccountID.FNumber like '1002%%' and FCREDIT>0" % (
                               str(book).replace("'", ""), date, no.replace("'", "")))
        except Exception:
            continue
        alt = round(sum(float(x.get("贷") or 0) for x in bk), 2)
        if alt and abs(alt - float(out[i]["amount"] or 0)) >= 0.005:
            out[i]["alt"] = alt
    return out


def _fmt_ms(v):
    try:
        return datetime.fromtimestamp(int(v) / 1000, idt.CN_TZ).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(v or "")[:16]


def scan_once(trigger="定时", days=None):
    if not _SCAN_LOCK.acquire(blocking=False):
        return {"ok": False, "msg": "上一轮还在扫，稍后再试"}
    try:
        return _scan(trigger, days)
    finally:
        _SCAN_LOCK.release()


def _scan(trigger, days):
    t0 = time.time()
    conf = idt._load_conf()
    if not conf:
        return {"ok": False, "msg": "未配置钉钉（conf.ini [dingtalk]）"}
    pc, err = idt._process_code(conf, lpq.TEMPLATE)
    if not pc:
        return {"ok": False, "msg": err or "找不到审批模板"}
    last = db.get_setting(_SET_LAST, None) or {}
    since = db.get_setting(_SET_SINCE, None)
    if not since:                       # 起算日＝首次扫那天：之前的老单只显示进度，不自动建票夹/导账单
        since = datetime.now().strftime("%Y-%m-%d")
        db.set_setting(_SET_SINCE, since, "系统")
    nd = int(days or (3 if last.get("at") else 60))
    now = datetime.now(idt.CN_TZ)
    ids, lerr = idt._list_ids(conf, pc, idt._ms(now - timedelta(days=nd)), idt._ms(now), max_pages=300)
    with db._engine.connect() as c:
        known = {r["inst_id"]: dict(r) for r in c.execute(select(PR)).mappings().all()}
    todo = [i for i in ids if i not in _NONLOGI and (i not in known or known[i]["dt_status"] == "RUNNING")]
    todo += [i for i, r in known.items() if r["dt_status"] == "RUNNING" and i not in todo]
    sups = _kd_suppliers()
    acct2code = {r["payee_account"]: r["sup_code"] for r in known.values() if r.get("payee_account") and r.get("sup_code")}
    names = {p["userid"]: p["name"] for p in (idt.roster().get("rows") or []) if p.get("userid")}

    def get(i):
        try:
            r = idt._oapi(conf, "topapi/processinstance/get", {"process_instance_id": i})
            return i, (r.get("process_instance") if r.get("errcode") == 0 else None)
        except Exception:
            return i, None

    with ThreadPoolExecutor(8) as ex:
        got = list(ex.map(get, todo))
    # 并发 8 偶尔被钉钉限流回空(首扫 559 张空了 40 张，单张重取都正常)：空的逐张再取一次
    got = [(i, inst) if inst else get(i) for i, inst in got]
    n_new = n_upd = n_fail = 0
    new_ids = []
    for iid, inst in got:
        if not inst:
            n_fail += 1
            continue
        f = lpq.parse_form(inst)
        code = sups["name2code"].get(f["payee"]) or acct2code.get(f["payee_account"])
        if not code:
            _NONLOGI.add(iid)
            continue
        atts = idt._bom.collect_attachments(inst) if idt._bom else []
        files = [{"fileId": a.get("fileId"), "fileName": a.get("fileName"), "source": a.get("source"), "size": a.get("fileSize"),
                  "spaceId": a.get("spaceId"), "role": lpq.classify_file(a.get("fileName"), a.get("source"))} for a in atts]
        ct = str(inst.get("create_time") or "")[:16]
        row = {"business_id": str(inst.get("business_id") or ""), "title": str(inst.get("title") or "")[:200],
               "applicant": names.get(inst.get("originator_userid"), ""), "subject_full": f["subject_full"],
               "subject": _subj_short(f["subject_full"]), "payee": f["payee"], "payee_account": f["payee_account"],
               "amount": f["amount"], "reason": f["reason"][:2000], "sup_code": code,
               "carrier": carrier_short(sups["code2name"].get(code) or f["payee"], code),
               "dt_status": str(inst.get("status") or "").upper(), "dt_result": str(inst.get("result") or "").lower(),
               "cur_json": json.dumps(lpq.current_tasks(inst, names), ensure_ascii=False),
               "ops_json": json.dumps(lpq.ops_view(inst, names), ensure_ascii=False),
               "files_json": json.dumps(files, ensure_ascii=False), "create_time": ct,
               "finish_time": str(inst.get("finish_time") or "")[:16], "updated_at": _now()}
        with db._engine.begin() as c:
            if iid in known:
                c.execute(update(PR).where(PR.c.inst_id == iid).values(**row))
                n_upd += 1
            else:
                c.execute(insert(PR).values(inst_id=iid, auto=1 if ct[:10] >= since else 0, **row))
                n_new += 1
                if row["business_id"]:
                    new_ids.append(row["business_id"])
    # 不属于物流账单的(办公室快递月结等)：没判过的按规则判一次；人工改过的(''或原因)不再动
    with db._engine.connect() as c:
        allr = [dict(r) for r in c.execute(select(PR)).mappings().all()]
    for r in allr:
        if r.get("excluded") is None:
            texts = [r.get("reason")] + [x.get("fileName") for x in json.loads(r.get("files_json") or "[]")]
            why = lpq.auto_exclude(texts, r.get("payee"))
            with db._engine.begin() as c:
                c.execute(update(PR).where(PR.c.inst_id == r["inst_id"]).values(excluded=("自动：" + why) if why else ""))
            r["excluded"] = ("自动：" + why) if why else ""
    # 认账期：没认过的(手工认领的不动)——金额正好＝某月计提优先，其次事由/附件名写的月份
    pend = [r for r in allr if not r.get("period")]
    if pend:
        ps = sorted({p for r in pend for p in lpq.prev_periods(r.get("create_time"))})
        amap = _accr_maps(ps)
        for r in pend:
            texts = [r.get("reason")] + [x.get("fileName") for x in json.loads(r.get("files_json") or "[]")]
            p, src = lpq.infer_period(r.get("amount"), r.get("subject"), r.get("sup_code"), r.get("create_time"), amap, texts)
            if p:
                with db._engine.begin() as c:
                    c.execute(update(PR).where(PR.c.inst_id == r["inst_id"]).values(period=p, period_src=src))
                r["period"], r["period_src"] = p, src
    # 已付款：审批通过但还没配上金蝶付款单的，按 编码+组织+金额 配
    wait = [r for r in allr if r.get("dt_status") == "COMPLETED" and r.get("dt_result") == "agree" and not r.get("kd_paid")]
    n_paid = 0
    if wait:
        pbs = _paybills(min(str(r.get("create_time") or "")[:10] for r in wait))
        if pbs is not None:
            taken = {str(r["kd_paid"]).split("|")[-1] for r in allr if r.get("kd_paid")}
            hit = lpq.match_paybills(wait, pbs, taken)
            for iid, v in hit.items():
                with db._engine.begin() as c:
                    c.execute(update(PR).where(PR.c.inst_id == iid).values(kd_paid=v))
                n_paid += 1
                taken.add(v.split("|")[-1])
            wait = [r for r in wait if r["inst_id"] not in hit]
        if wait:      # 付款单配不上的，再看支付凭证
            gls = _paid_vouchers(min(str(r.get("create_time") or "")[:10] for r in wait), {(r.get("sup_code"), r.get("subject_full")) for r in wait})
            for iid, v in lpq.match_paybills(wait, gls or [], {str(r["kd_paid"]).split("|")[-1] for r in allr if r.get("kd_paid")}).items():
                with db._engine.begin() as c:
                    c.execute(update(PR).where(PR.c.inst_id == iid).values(kd_paid=v))
                n_paid += 1
    try:
        _pay_code_warn()
    except Exception:
        pass
    # 上线后提交的：自动建票夹拉发票 + 导账单
    n_auto = 0
    for r in allr:
        if r.get("auto") and not r.get("excluded") and r.get("dt_status") != "TERMINATED" and r.get("dt_result") != "refuse":
            if not r.get("folder_id") or not r.get("bill_state"):
                pull(r["inst_id"], "系统·钉钉")
                n_auto += 1
    out = {"at": _now(), "trigger": trigger, "days": nd, "listed": len(ids), "fetched": len(todo), "new": n_new,
           "updated": n_upd, "failed": n_fail, "paid": n_paid, "auto": n_auto, "err": lerr or "",
           "sec": round(time.time() - t0, 1)}
    db.set_setting(_SET_LAST, out, "系统")
    return {"ok": True, **out, "newIds": new_ids[:8]}       # newIds＝这一轮新接的钉钉审批编号（数字员工办公室的干活记录用；设置里不存）


# ---------- 拉进来：发票进发票管家 + 账单进复核台 ----------
def _download(iid, fmeta, inst=None):
    att = {"fileId": fmeta.get("fileId"), "fileName": fmeta.get("fileName"), "spaceId": fmeta.get("spaceId"),
           "fileSize": fmeta.get("size")}
    return idt.download_attachment(iid, att, inst or {})


def _store_file(iid, fmeta, data):
    sha = hashlib.sha256(data).hexdigest()
    with db._engine.begin() as c:
        hit = c.execute(select(PF.c.id).where((PF.c.inst_id == iid) & (PF.c.file_id == str(fmeta.get("fileId"))))).first()
        if not hit:
            c.execute(insert(PF).values(inst_id=iid, file_id=str(fmeta.get("fileId")), name=str(fmeta.get("fileName") or "")[:200],
                                        source=fmeta.get("source"), role=fmeta.get("role"), sha256=sha, size=len(data),
                                        data=data, created_at=_now()))
    return sha


def pull(iid, operator, force_bill=False):
    """一张请款单：①建/刷票夹(发票管家自己拉附件、识别发票) ②xlsx 原件存档(账单/审核留档) ③账单导复核台。
    ③只在这家这月还没账单时导；已有账单不覆盖(账单可替换在复核台做，工作台为准)。返回 {ok, msg}。"""
    with db._engine.connect() as c:
        r = c.execute(select(PR).where(PR.c.inst_id == iid)).mappings().first()
    if not r:
        return {"ok": False, "msg": "没有这张请款单"}
    r = dict(r)
    msgs, vals = [], {}
    if not r.get("folder_id"):
        try:
            from routers import invoice as INV
            g = INV.open_approval_folder(user=operator, source="logistics", inst_id=iid)
            if g.get("ok"):
                vals["folder_id"] = g["folder"]["id"]
                msgs.append("发票：已进发票管家票夹#%d" % g["folder"]["id"])
            else:
                msgs.append("发票：%s" % (g.get("msg") or "建票夹失败"))
        except Exception as e:
            msgs.append("发票：建票夹失败 %s" % str(e)[:80])
    files = json.loads(r.get("files_json") or "[]")
    xl = [f for f in files if f.get("role") in ("bill", "review") and str(f.get("fileName") or "").lower().endswith(lpq._SHEET_EXT)]
    shas = {}
    for f in xl:
        with db._engine.connect() as c:
            have = c.execute(select(PF.c.sha256).where((PF.c.inst_id == iid) & (PF.c.file_id == str(f.get("fileId"))))).first()
        if have:
            shas[f["fileId"]] = have[0]
            continue
        d = _download(iid, f)
        if d.get("ok"):
            shas[f["fileId"]] = _store_file(iid, f, d["bytes"])
        elif f.get("role") == "bill":
            msgs.append("账单：%s没拉下来（%s）" % (f.get("fileName"), d.get("msg")))
    bills = [f for f in xl if f.get("role") == "bill" and f.get("fileId") in shas]
    if not bills:
        vals["bill_state"], vals["bill_msg"] = "nofile", "请款单里没有账单 xlsx"
    elif not r.get("period"):
        vals["bill_state"], vals["bill_msg"] = None, "还没认出归哪个月，认领后再导"
    else:
        vals.update(_import(r, bills[0], shas[bills[0]["fileId"]], operator, force_bill, bills=bills))
    msgs.append("账单：" + (vals.get("bill_msg") or ""))
    vals["updated_at"] = _now()
    with db._engine.begin() as c:
        c.execute(update(PR).where(PR.c.inst_id == iid).values(**vals))
    return {"ok": True, "msg": "；".join(m for m in msgs if m)}


def _import_multi(r, bills, operator, force=False):
    """一张请款单带几个账单文件、每个文件一类(取数说明 multi_file，天鹰：蜜雪装货/小料卸货/分步调拨…)：
    逐个文件导，各算一份(份名=表名，import_bill 只替换同名那份)。复核台已有同名那份的不覆盖(force 才换)。V2.765"""
    from routers import logistics_review as LR
    from kernels import logistics_intake as intake
    carrier, period = r["carrier"], r["period"]
    spec = LR._load_spec(carrier) or {}
    spec["period"] = period
    done, kept, empty, fail, nrow = [], [], [], [], 0
    for b in bills:
        with db._engine.connect() as c:
            data = c.execute(select(PF.c.data).where((PF.c.inst_id == r["inst_id"]) & (PF.c.file_id == str(b["fileId"])))).scalar()
        try:
            pre = intake.parse_bill(spec, data)
        except Exception:
            fail.append(b.get("fileName"))
            continue
        src = (pre.get("bill_src") or "").strip()
        if not pre["detail"] and not pre["accrual"]:
            empty.append(b.get("fileName"))
            continue
        with db._engine.connect() as c:
            has = c.execute(select(BL.c.id).where((BL.c.carrier == carrier) & (BL.c.period == period) & (BL.c.bill_src == src)).limit(1)).first()
        if has and not force:
            kept.append(src or b.get("fileName"))
            continue
        res = LR.import_bill(carrier, period, data, operator, origin="钉钉请款单 %s「%s」" % (r.get("business_id"), b.get("fileName")))
        if not res.get("ok"):
            fail.append(b.get("fileName"))
            continue
        done.append(src or b.get("fileName"))
        nrow += res.get("detail", 0) + res.get("accrual", 0)
    if fail:
        return {"bill_state": "parsefail", "bill_msg": "账单已到但有 %d 个文件解析失败：%s" % (len(fail), "、".join(str(x) for x in fail))}
    if not done and not kept:
        return {"bill_state": "parsefail", "bill_msg": "账单文件里没认出费用明细表（%s）" % "、".join(str(x) for x in empty)}
    msg = "账单已就绪：%d 份（%s）%s" % (len(done) + len(kept), "、".join(done + kept),
                                    "，明细 %d 行" % nrow if nrow else "")
    if kept:
        msg += "；其中 %s 复核台已有，没覆盖（要以钉钉这份为准请点「用这份替换」）" % "、".join(kept)
    return {"bill_state": "imported" if done or r.get("bill_state") == "imported" else "exists", "bill_msg": msg}


def _import(r, bill, sha, operator, force=False, bills=None):
    carrier, period = r["carrier"], r["period"]
    if not _has_spec(carrier):
        return {"bill_state": "nospec", "bill_msg": "账单已到，%s 还没配取数说明，配好后点「导入账单」" % carrier}
    from routers import logistics_review as _LR
    if (_LR._load_spec(carrier) or {}).get("review_mode") == "register":
        return {"bill_state": "register", "bill_msg": "登记制：不导账单，只核对计提、请款、发票金额"}
    if (_LR._load_spec(carrier) or {}).get("multi_file"):
        return _import_multi(r, bills or [bill], operator, force)
    with db._engine.connect() as c:
        n = c.execute(select(BL.c.id).where((BL.c.carrier == carrier) & (BL.c.period == period)).limit(1)).first()
        # 同一份账单别的主体的请款单已导过(易风达：孝感 87,168 + 深圳 800 共一份账单)
        same = c.execute(select(PR.c.inst_id).select_from(PR.join(PF, PF.c.inst_id == PR.c.inst_id)).where(
            (PR.c.carrier == carrier) & (PR.c.period == period) & (PR.c.bill_state == "imported") & (PF.c.sha256 == sha))).first()
    if n and same:
        return {"bill_state": "imported", "bill_msg": "账单已就绪（同一份账单已由另一张请款单导入）"}
    if n and not force:
        return {"bill_state": "exists", "bill_msg": "复核台已有这家这月的账单，没覆盖；要以钉钉这份为准请点「用这份替换」"}
    with db._engine.connect() as c:
        data = c.execute(select(PF.c.data).where((PF.c.inst_id == r["inst_id"]) & (PF.c.file_id == str(bill["fileId"])))).scalar()
    from routers import logistics_review as LR
    res = LR.import_bill(carrier, period, data, operator, origin="钉钉请款单 %s「%s」" % (r.get("business_id"), bill.get("fileName")))
    if not res.get("ok"):
        return {"bill_state": "parsefail", "bill_msg": "账单已到但解析失败：%s" % res.get("msg")}
    return {"bill_state": "imported", "bill_msg": "账单已就绪：明细 %d 行 / 汇总 %d 行（%s）" % (
        res.get("detail", 0), res.get("accrual", 0), bill.get("fileName"))}


# ---------- 总表合并 ----------
def _me_uid(user):
    nm = (user or {}).get("name") or ""
    if not nm:
        return ""
    for p in idt.roster().get("rows") or []:
        if p.get("name") == nm:
            return p.get("userid") or ""
    return ""


def req_view(r, me):
    st = lpq.status_view({**r, "cur": json.loads(r.get("cur_json") or "[]")}, me)
    files = json.loads(r.get("files_json") or "[]")
    return {"inst": r["inst_id"], "bid": r.get("business_id"), "applicant": r.get("applicant"), "amount": r.get("amount"),
            "subject": r.get("subject"), "carrier": r.get("carrier"), "code": r.get("sup_code"), "payee": r.get("payee"),
            "period": r.get("period") or "", "period_src": r.get("period_src") or "", "reason": r.get("reason") or "",
            "created": r.get("create_time"), "finished": r.get("finish_time"), "st": st,
            "ops": json.loads(r.get("ops_json") or "[]"), "cur": json.loads(r.get("cur_json") or "[]"),
            "files": [{"id": f.get("fileId"), "name": f.get("fileName"), "role": f.get("role"), "src": f.get("source")} for f in files],
            "folder": r.get("folder_id"), "bill_state": r.get("bill_state") or "", "bill_msg": r.get("bill_msg") or "",
            "auto": bool(r.get("auto")), "paid": r.get("kd_paid") or "", "excluded": r.get("excluded") or ""}


def overview_merge(period, rows, user):
    """总表行(按供应商编码)挂上本月请款单 → 每个主体格 cells[sj].reqs；没计提但有请款的承运商补一行；返回 payreq 汇总。"""
    me = _me_uid(user)
    with db._engine.connect() as c:
        reqs = [dict(r) for r in c.execute(select(PR)).mappings().all()]
    excl = [req_view(r, me) for r in reqs if r.get("excluded") and r.get("period") == period]
    reqs = [r for r in reqs if not r.get("excluded")]      # 不属于物流账单的不进总表格子/待我审批/待认领
    mine_all = [req_view(r, me) for r in reqs if r.get("dt_status") == "RUNNING"]
    mine_all = [v for v in mine_all if v["st"]["key"] == "mine"]
    cur = [req_view(r, me) for r in reqs if r.get("period") == period]
    unassigned = [req_view(r, me) for r in reqs if not r.get("period") and r.get("dt_status") != "TERMINATED"]
    # V2.819(用户 2026-10-05「那诚煜的我怎么复核？其实这里应该接的是计提和请款一起？」)：账单是别的月份、但还没登记已复核的请款单也露出来
    #   ——总表按「本月有计提」列承运商，诚煜 9 月才请 6 月账单的款，8 期里既没计提也没请款，根本看不到。点一下切到它账单那个月去复核。
    other = []
    try:
        from routers.logistics_review import _signed
        done = set((db.get_setting("logi_voucher_posted", None) or {}).keys())
        for r in reqs:
            p = r.get("period")
            if not p or p == period or r.get("dt_status") == "TERMINATED" or r.get("dt_result") == "refuse":
                continue
            # V2.822：只留还有事要办的——已经做过账的(系统写的，或金蝶里早就记了支付凭证的)不列，原来往月付完的几十张全挤在这一行，找不到要找的
            if r["inst_id"] in done or "|gl:" in str(r.get("kd_paid") or ""):
                continue
            if _signed(r.get("carrier"), p):
                continue
            v = req_view(r, me)
            v["has_pay"] = bool(r.get("kd_paid"))            # 金蝶已有付款单(待做账)的排前面
            other.append(v)
        other.sort(key=lambda v: (not v["has_pay"], v["period"], v["carrier"] or ""))
    except Exception:
        other = []
    by_code = {x.get("code"): x for x in rows if x.get("code")}
    for v in cur:
        x = by_code.get(v["code"])
        if not x:
            x = {"carrier": v["payee"], "code": v["code"], "short": v["carrier"], "full": v["payee"], "has_spec": _has_spec(v["carrier"]),
                 "cells": {}, "total_accr": 0.0, "signed": None, "status": "noaccr", "progress": {}}
            rows.append(x)
            by_code[v["code"]] = x
        cell = x["cells"].setdefault(v["subject"], {"accr": 0, "paid": 0, "diff": 0})
        cell.setdefault("reqs", []).append(v)
    for x in rows:
        vs = [v for c in (x.get("cells") or {}).values() for v in c.get("reqs") or []]
        for c in (x.get("cells") or {}).values():
            if c.get("reqs"):
                c["reqs"].sort(key=lambda v: lpq.cell_rank(v["st"]["key"]))
        if vs:
            live = [v for v in vs if v["st"]["key"] != "void"]
            x["dt"] = {"n": len(live), "paid": sum(1 for v in live if v["st"]["key"] == "paid"),
                       "mine": sum(1 for v in live if v["st"]["key"] == "mine")}
            imp = [v for v in vs if v["bill_state"] == "imported"]
            if imp:
                x["bill_from"] = {"bid": imp[0]["bid"], "date": (imp[0]["created"] or "")[:10]}
            elif any(v["bill_state"] in ("nospec", "parsefail", "exists") for v in vs) and x.get("status") in ("nobill", "nospec", "noaccr"):
                x["status"] = "billarrived"
    last = db.get_setting(_SET_LAST, None) or {}
    return {"mine": mine_all, "unassigned": unassigned, "excluded": excl, "other": other, "last": last, "since": db.get_setting(_SET_SINCE, None)}


# ---------- 接口 ----------
@router.post("/api/logistics-review/payreq/scan")
async def payreq_scan(request: Request, days: int = 0):
    u = _perm(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(scan_once, "手动·" + u["name"], days or None)


@router.post("/api/logistics-review/payreq/pull")
async def payreq_pull(request: Request):
    """手工「拉进来」(上线前的老单) / 「用这份替换」(force=1)。"""
    u = _perm(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    b = await request.json()
    from starlette.concurrency import run_in_threadpool
    r = await run_in_threadpool(pull, str(b.get("inst") or ""), u["name"], bool(b.get("force")))
    if r.get("ok"):
        db.audit(u["name"], "物流复核-请款单拉进来", str(b.get("inst")), r.get("msg"))
    return r


@router.post("/api/logistics-review/payreq/assign")
async def payreq_assign(request: Request):
    """认领/改归属月份(认不出或认错时)。改了月份，账单状态清空，下次「拉进来」按新月份导。"""
    u = _perm(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    b = await request.json()
    iid, p = str(b.get("inst") or ""), str(b.get("period") or "")
    if p and not (len(p) == 7 and p[4] == "-" and p[:4].isdigit() and p[5:].isdigit()):
        return JSONResponse({"ok": False, "msg": "月份格式应为 2026-08"}, status_code=400)
    with db._engine.begin() as c:
        c.execute(update(PR).where(PR.c.inst_id == iid).values(period=p or None, period_src="manual" if p else None,
                                                               bill_state=None, bill_msg=None, updated_at=_now()))
    db.audit(u["name"], "物流复核-请款单认领月份", iid, p or "清空")
    return {"ok": True}


@router.post("/api/logistics-review/payreq/exclude")
async def payreq_exclude(request: Request):
    """标「不属于物流账单」/ 恢复。exclude=false 存 ''＝人工确认属于，自动规则不再改它。"""
    u = _perm(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    b = await request.json()
    iid = str(b.get("inst") or "")
    val = ("%s：%s" % (u["name"], str(b.get("reason") or "不属于物流账单").strip()[:30])) if b.get("exclude") else ""
    with db._engine.begin() as c:
        n = c.execute(update(PR).where(PR.c.inst_id == iid).values(excluded=val, updated_at=_now())).rowcount
    if not n:
        return JSONResponse({"ok": False, "msg": "没有这张请款单"}, status_code=404)
    db.audit(u["name"], "物流复核-请款单" + ("排除" if val else "恢复"), iid, val)
    return {"ok": True, "excluded": val}


@router.get("/api/logistics-review/payreq/file")
async def payreq_file(request: Request, inst: str, fid: str):
    """下载请款单附件：存过的 xlsx 直接给；其余现从钉钉拉。"""
    if not _perm(request):
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    from urllib.parse import quote
    from starlette.concurrency import run_in_threadpool
    with db._engine.connect() as c:
        hit = c.execute(select(PF.c.name, PF.c.data).where((PF.c.inst_id == inst) & (PF.c.file_id == fid))).first()
        r = c.execute(select(PR.c.files_json).where(PR.c.inst_id == inst)).first()
    if hit:
        name, data = hit[0], hit[1]
    else:
        meta = next((f for f in json.loads((r[0] if r else None) or "[]") if str(f.get("fileId")) == fid), None)
        if not meta:
            return JSONResponse({"ok": False, "msg": "没有这个附件"}, status_code=404)
        d = await run_in_threadpool(_download, inst, meta)
        if not d.get("ok"):
            return JSONResponse({"ok": False, "msg": d.get("msg")}, status_code=400)
        name, data = meta.get("fileName") or "附件", d["bytes"]
    return Response(content=data, media_type="application/octet-stream",
                    headers={"Content-Disposition": "attachment; filename*=UTF-8''%s" % quote(name)})


# ---------- 第③步·发票与暂估（V2.767）----------
# 用户 2026-10-01 定「发票也接入第三步，将暂估的和实际的发票放上来」：这家这月的钉钉请款单 → 发票管家票夹里的发票，
# 和金蝶计提凭证的暂估进项税按税率并排比。口径与付款做账同一套(logistics_voucher._preview_data)，这里只读、不出凭证。
_KIND_CN = {"hx": "一致·只核销", "tail": "尾差·红冲更正", "redo": "需红冲更正", "subj": "计提记错主体", "manual": "金额不符·人工", "noacc": "没有计提"}


def _inv_block(r, me):
    from routers import logistics_voucher as LVR
    from kernels import logistics_voucher as LV
    st = lpq.status_view({**r, "cur": json.loads(r.get("cur_json") or "[]")}, me)
    out = {"inst": r["inst_id"], "bid": r.get("business_id"), "subject": r.get("subject"), "amount": r.get("amount"),
           "applicant": r.get("applicant"), "st": st, "folder": r.get("folder_id"), "invoices": [], "rates": [], "msgs": []}
    try:
        d, code = LVR._preview_data(r["inst_id"])
    except Exception as e:
        out["err"] = "读取失败：%s" % str(e)[:120]
        return out
    if code != 200:
        out["err"] = d.get("msg") or "读取失败"
        return out
    invs, accs = d.get("invoices") or [], d.get("accruals") or []
    out.update(invoices=invs, folder=d["req"].get("folder") or out["folder"], kind=d.get("kind"), kind_cn=_KIND_CN.get(d.get("kind"), ""),
               kind_text=d.get("kind_text"), msgs=[m for m in (d.get("plan") or {}).get("msgs") or [] if m != d.get("kind_text")],
               paid=d["req"].get("paid"), posted=d["req"].get("posted"), pay_status=d["req"].get("status"),
               accruals=[{k: a.get(k) for k in ("vno", "month", "fee", "biz", "gross", "tax", "rate", "mode", "new_rate", "why")} for a in accs])
    # 按税率并排：计提(含税/暂估税) vs 发票(含税/税额)；不能抵扣的票算 0 税率
    g = {}
    for a in accs:
        x = g.setdefault(round(float(a.get("rate") or 0), 4), {"acc": 0.0, "acc_tax": 0.0, "inv": 0.0, "inv_tax": 0.0, "n_inv": 0})
        x["acc"] += float(a.get("gross") or 0)
        x["acc_tax"] += float(a.get("tax") or 0)
    for i in invs:
        rt = 0.0 if i.get("deduct") is False else LV.rate_of(i.get("rate"))
        if rt is None:
            continue
        x = g.setdefault(round(rt, 4), {"acc": 0.0, "acc_tax": 0.0, "inv": 0.0, "inv_tax": 0.0, "n_inv": 0})
        x["inv"] += float(i.get("gross") or 0)
        x["inv_tax"] += 0.0 if i.get("deduct") is False else float(i.get("tax") or 0)
        x["n_inv"] += 1
    out["rates"] = [{"rate": k, **{kk: (round(v, 2) if kk != "n_inv" else v) for kk, v in x.items()}} for k, x in sorted(g.items())]
    out["acc_total"] = round(sum(float(a.get("gross") or 0) for a in accs), 2)
    out["acc_tax"] = round(sum(float(a.get("tax") or 0) for a in accs), 2)
    out["inv_total"] = round(sum(float(i.get("gross") or 0) for i in invs), 2)
    out["inv_tax"] = round(sum(float(i.get("tax") or 0) for i in invs if i.get("deduct") is not False), 2)
    return out


@router.get("/api/logistics-review/invoices")
async def review_invoices(request: Request, carrier: str = "", period: str = ""):
    """第③步·发票与暂估：这家这月每张钉钉请款单(撤回/拒绝/已排除的不算) → 发票清单 + 按税率的计提暂估 vs 发票。只读。"""
    u = _perm(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    with db._engine.connect() as c:
        reqs = [dict(r) for r in c.execute(select(PR).where((PR.c.carrier == carrier) & (PR.c.period == period))).mappings().all()]
    reqs = [r for r in reqs if not r.get("excluded") and str(r.get("dt_status") or "").upper() != "TERMINATED"
            and str(r.get("dt_result") or "").lower() != "refuse"]
    reqs.sort(key=lambda r: (r.get("subject") or "", r.get("create_time") or ""))
    from starlette.concurrency import run_in_threadpool
    me = _me_uid(u)
    blocks = await run_in_threadpool(lambda: [_inv_block(r, me) for r in reqs])
    return {"ok": True, "carrier": carrier, "period": period, "blocks": blocks,
            "req_total": round(sum(float(b.get("amount") or 0) for b in blocks), 2),
            "inv_total": round(sum(b.get("inv_total") or 0 for b in blocks), 2),
            "inv_tax": round(sum(b.get("inv_tax") or 0 for b in blocks), 2),
            "acc_tax": round(sum(b.get("acc_tax") or 0 for b in blocks), 2),
            "n_inv": sum(len(b.get("invoices") or []) for b in blocks)}


def _scheduler():
    while True:
        time.sleep(20 * 60)
        try:
            import worker_store      # 数字员工办公室·物流请款单接收员：每圈报到；真接了新单 / 出了错才记一笔（只写张数和钉钉单号）
            if _is_local():
                worker_store.beat("payreq", off="本机测试库不自动跑")
                continue
            r = scan_once("定时") or {}
            worker_store.beat("payreq", next_in=20 * 60)
            try:                      # 付款做账员(V2.809)：刚配完金蝶付款单，接着看有没有够条件自动做账的(关/演练/真做 三档，在付款做账页设)
                from routers import logistics_voucher as _lv
                _lv.auto_round("定时")
            except Exception as e:
                worker_store.record("voucher_auto", ok=False, summary="自动做账这一轮出错", error=str(e))
            if r.get("new") or r.get("failed") or r.get("err"):
                bad = r.get("failed") or r.get("err")
                worker_store.record("payreq", n=r.get("new") or 0, ok=not bad, summary="已接入 %d 张请款单" % (r.get("new") or 0), refs=r.get("newIds"),
                                    error=("%s 张没读成；%s" % (r.get("failed") or 0, r.get("err") or "")) if bad else "")
        except Exception as e:
            try:
                import worker_store
                worker_store.record("payreq", ok=False, summary="扫钉钉请款单出错", error=str(e))
            except Exception:
                pass


threading.Thread(target=_scheduler, daemon=True, name="logi-payreq-scan").start()
