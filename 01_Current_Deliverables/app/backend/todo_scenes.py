# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-04 | Author: Claude Opus 5.5 | Version: V2.790（首页待办区）
# Description: 首页待办区 · 第一批四个环节的「记一笔」与「核对」（《首页待办区 需求确认书 v1.0》第四、七节）。
#   · xxx_touch()：各工具线在交接点调一行。按源头台账现算——台账里还有就挂上（或更新），台账空了就撤回，
#     所以「录入」和「撤销」两头调的是同一个函数。全部吞异常、只留痕，不拦原有动作。
#   · check(it, s, conf)：核对一条待办现在该是什么样。在金蝶里审的（汇率 / 物流计提凭证 / 付款做账凭证）回读金蝶单据状态，
#     全部「已审核」才销（D3 销账看事实不看点击）；计提更正不用金蝶，看有没有合进已写入金蝶的付款凭证，或处理人确认已改好（Q2）。
#   · 金蝶「读不到」和「确实没有」是两回事：连不上/报错一律往外抛（由调用方记「这次没核对成」），绝不当成已审，也不当成已删。
#   · 金蝶审核人这一版不读（字段没实测，Q5），办结只记「金蝶里已审核」。
#   依赖：db / kingdee_client / todo_store / kernels.logistics_review_store。不 import 任何 router。
import json

from sqlalchemy import select

import db
import kingdee_client as kc
import todo_store as ts
from kernels import logistics_review_store as lr_store

FIXT = lr_store.review_line_fix
VOUCHER_POSTED_KEY = "logi_voucher_posted"     # 与 routers/logistics_voucher._POSTED_KEY 同一个设置键
BOT_FX = "系统 · 汇率定时录入"


def _swallow(fn):
    def wrap(*a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:
            try:
                db.audit("系统", "待办-记一笔失败", fn.__name__, str(e)[:200])
            except Exception:
                pass
            return None
    wrap.__name__ = fn.__name__
    return wrap


# ============ ① 汇率待审：一个月 × 一个组织 一条 ============
def _fx_ref(year, month, org):
    return "%d-%02d:%s" % (int(year), int(month), org)


@_swallow
def fx_touch(year, month, org, origin="", bot=False, posted=True):
    """汇率写入金蝶并提交后调（posted=True）；在工具里撤销后也调（posted=False：全撤光了才撤回待办，
    只撤了一部分的不动，条数等下一轮核对刷新——撤销不是新的交接，不该凭空挂出一条）。"""
    ref, logs = _fx_ref(year, month, org), db.list_fx_posts(int(year), int(month), str(org))
    if not logs:
        return ts.withdraw("fx_audit", ref, "汇率已在工具里撤销，待办自动撤回")
    if not posted:
        return None
    ex = ts.get("fx_audit", ref)
    keep = bool(ex and ex["status"] == "open")
    return ts.open_item("fx_audit", ref, "汇率录入 · %d 年 %d 月（组织 %s）" % (int(year), int(month), org),
                        sub=None if keep else "%d 条汇率" % len(logs), state=None if keep else "已提交，等审核",
                        warn=None if keep else "", holder=None if keep else "scene",
                        origin=origin or BOT_FX, bot=bot,
                        payload={"year": int(year), "month": int(month), "org": str(org)})


def _check_fx(it, s, conf):
    p = it["payload"]
    logs = db.list_fx_posts(p["year"], p["month"], p["org"])
    if not logs:
        return {"status": "withdrawn", "how": "汇率已在工具里撤销，待办自动撤回"}
    ids = [str(l["kd_id"]) for l in logs if l.get("kd_id")]
    rows = kc._query(s, conf, "BD_Rate", [("FRATEID", "id"), ("FDocumentStatus", "st")],
                     "FRATEID in (%s)" % ",".join(ids), "") if ids else []
    st = {str(r["id"]): r.get("st") for r in rows}
    n, found = len(ids), [i for i in ids if i in st]
    aud = sum(1 for i in found if st[i] == "C")
    if n and aud == n:
        return {"status": "done", "how": "金蝶里 %d 条汇率已全部审核，自动销账" % n}
    gone = n - len(found)
    sub = "%d 条汇率 · 金蝶里已审 %d 条" % (n, aud)
    if not found:
        return {"status": "open", "holder": "origin", "sub": sub, "state": "金蝶里已找不到",
                "warn": "金蝶里已找不到这批汇率（可能被人删了）。要重录，先在汇率录入页把这批撤销"}
    return {"status": "open", "holder": "scene", "sub": sub, "state": "已提交，等审核",
            "warn": ("其中 %d 条金蝶里已找不到" % gone) if gone else ""}


# ============ ② 物流计提凭证待审：一个落账月份 一条 ============
def _accrual_ref(year, period):
    return "%d-%02d" % (int(year), int(period))


@_swallow
def accrual_touch(year, period, origin="", posted=True):
    """「一键录入金蝶」成功后调（posted=True）；撤销录入后也调（posted=False，同汇率：全撤光才撤回）。
    录进去的是草稿，要有人在金蝶提交后审核人才审得了（Q1）。"""
    ref, logs = _accrual_ref(year, period), db.list_logistics_posts(int(year), int(period))
    if not logs:
        return ts.withdraw("logi_accrual_audit", ref, "录入已全部撤销，待办自动撤回")
    if not posted:
        return None
    ex = ts.get("logi_accrual_audit", ref)
    keep = bool(ex and ex["status"] == "open")
    payload = dict(ex["payload"]) if keep else {}
    payload.update(year=int(year), period=int(period))
    return ts.open_item("logi_accrual_audit", ref, "物流计提凭证 · %d 年 %d 月" % (int(year), int(period)),
                        sub=None if keep else "%d 张" % len(logs),
                        state=None if keep else "录进金蝶的是草稿，等提交",
                        warn=None if keep else "这 %d 张还是草稿、没提交，现在审不了" % len(logs),
                        holder=None if keep else "scene",
                        origin=origin or (ex["origin"] if keep else ""), payload=payload)


def _voucher_status(vid, s, conf):
    """按内码读一张凭证的单据状态。→ 状态码；金蝶明确答复没有这张 → None。连不上/报错往外抛（不当成没有）。"""
    res = kc._post(s, conf, kc.VIEW_SVC, ["GL_VOUCHER", json.dumps({"Id": str(vid)})]).json()
    result = res.get("Result") or {}
    status = result.get("ResponseStatus") or {}
    if status and not status.get("IsSuccess", True):
        return None
    m = result.get("Result") or {}
    if not isinstance(m, dict) or not m.get("Id"):
        return None
    return str(m.get("DocumentStatus") or "")


def _check_accrual(it, s, conf):
    p = dict(it["payload"])
    logs = db.list_logistics_posts(p["year"], p["period"])
    if not logs:
        return {"status": "withdrawn", "how": "录入已全部撤销，待办自动撤回"}
    audited = set(p.get("audited") or [])        # 已审核的记下来，下一轮不再逐张去问金蝶（一期：办结后不追反审核）
    cnt = {"C": 0, "B": 0, "draft": 0, "gone": 0}
    for l in logs:
        kid = str(l["kd_id"])
        if kid in audited:
            cnt["C"] += 1
            continue
        st = _voucher_status(kid, s, conf)
        if st == "C":
            audited.add(kid)
            cnt["C"] += 1
        elif st == "B":
            cnt["B"] += 1
        elif st is None:
            cnt["gone"] += 1
        else:
            cnt["draft"] += 1
    n = len(logs)
    if cnt["C"] == n:
        return {"status": "done", "how": "金蝶里 %d 张凭证已全部审核，自动销账" % n}
    p["audited"] = sorted(audited & {str(l["kd_id"]) for l in logs})
    sub = "%d 张 · 金蝶里已审 %d 张" % (n, cnt["C"])
    if cnt["gone"] and cnt["gone"] + cnt["C"] == n:
        return {"status": "open", "holder": "origin", "sub": sub, "payload": p, "state": "金蝶里已找不到",
                "warn": "有 %d 张金蝶里已找不到（可能被人删了）。到物流计提「已录入凭证」里清掉后可重录" % cnt["gone"]}
    warn = []
    if cnt["draft"]:
        warn.append("其中 %d 张还是草稿、没提交，现在审不了" % cnt["draft"])
    if cnt["gone"]:
        warn.append("%d 张金蝶里已找不到" % cnt["gone"])
    state = ("已提交 %d 张，等审核" % cnt["B"]) if cnt["B"] else "都还没提交，等录入人在金蝶提交"
    return {"status": "open", "holder": "scene", "sub": sub, "warn": "；".join(warn), "state": state, "payload": p}


# ============ ③ 付款做账凭证待审：一张凭证 一条 ============
@_swallow
def voucher_touch(inst, rec, req, origin=""):
    """付款凭证补完分录写入金蝶后调。提交成功 → 挂审核人；提交没成功 → 挂回做账人去金蝶手动提交。"""
    ok = bool(rec.get("submitted"))
    period = str(req.get("period") or "")
    title = "付款做账 · 记-%s　%s%s" % (rec.get("vno") or "?", req.get("payee") or req.get("carrier") or "",
                                   (" %s" % period) if period else "")
    try:
        amt = "借贷 %s" % format(float(rec.get("dr") or 0), ",.2f")
    except (TypeError, ValueError):
        amt = ""
    sub = " · ".join(x for x in [req.get("subject") or "", "共 %s 行" % rec.get("lines") if rec.get("lines") else "", amt] if x)
    return ts.open_item("logi_voucher_audit", str(inst), title, sub=sub, origin=origin,
                        holder="scene" if ok else "origin",
                        state="已提交，等审核" if ok else "保存了，没提交上",
                        warn="" if ok else "请到金蝶手动提交，提交后才会转给审核人",
                        payload={"inst": str(inst), "vid": rec.get("vid"), "vno": rec.get("vno")})


def _check_voucher(it, s, conf):
    rec = (db.get_setting(VOUCHER_POSTED_KEY, None) or {}).get(it["ref"])
    if not rec:
        return {"status": "withdrawn", "how": "这张的做账记录已不在，待办自动撤回"}
    st = _voucher_status(rec.get("vid"), s, conf)
    if st == "C":
        return {"status": "done", "how": "金蝶里凭证 记-%s 已审核，自动销账" % (rec.get("vno") or "")}
    if st is None:
        return {"status": "open", "holder": "origin", "state": "金蝶里已找不到",
                "warn": "金蝶里已找不到这张凭证 记-%s（可能被人删了），请做账人到金蝶看一下" % (rec.get("vno") or "")}
    if st == "B":
        return {"status": "open", "holder": "scene", "state": "已提交，等审核", "warn": ""}
    return {"status": "open", "holder": "origin", "state": "保存了，没提交上",
            "warn": "请到金蝶手动提交，提交后才会转给审核人"}


# ============ ④ 计提更正待办：一家承运商 × 一个账期 一条 ============
def _fix_ref(carrier, period):
    return "%s|%s" % (carrier, period)


def _fix_ids(carrier, period):
    with db._engine.connect() as c:
        return [r[0] for r in c.execute(select(FIXT.c.id).where((FIXT.c.carrier == carrier) & (FIXT.c.period == period)))]


def _fix_left(ids, payload):
    """还没办的更正：没合进已写入金蝶的付款凭证，处理人也没确认「已在金蝶改好」的。"""
    in_voucher = set()
    for rec in (db.get_setting(VOUCHER_POSTED_KEY, None) or {}).values():
        in_voucher |= {int(x) for x in (rec.get("fix_ids") or [])}
    manual = {int(x) for x in (payload.get("manual_ids") or [])}
    return [i for i in ids if i not in in_voucher and i not in manual]


@_swallow
def fix_touch(carrier, period, origin=""):
    """复核台登记 / 撤掉一笔「应改为」后调；付款凭证写入金蝶后也调（看这家这个月的更正是不是都合进去了）。"""
    ref, ids = _fix_ref(carrier, period), _fix_ids(carrier, period)
    if not ids:
        return ts.withdraw("logi_fix", ref, "更正已全部撤掉，待办自动撤回")
    ex = ts.get("logi_fix", ref)
    keep = bool(ex and ex["status"] == "open")
    payload = dict(ex["payload"]) if ex else {}
    payload.update(carrier=carrier, period=period)
    left = _fix_left(ids, payload)
    if not left:
        if keep:
            ts.finish(ex["id"], origin, _fix_how(payload, ids))
        return None
    return ts.open_item("logi_fix", ref, "计提更正 · %s %s" % (carrier, period),
                        sub="%d 笔要改 · 还剩 %d 笔没办" % (len(ids), len(left)), state="还没合进付款凭证", warn="",
                        holder="scene", origin=origin or (ex["origin"] if keep else ""), payload=payload)


def _fix_how(payload, ids):
    if {int(x) for x in (payload.get("manual_ids") or [])} & set(ids):     # 现有的更正里有处理人手动确认过的
        return "处理人确认已在金蝶改好（%s）" % (payload.get("manual_vno") or "没填凭证号")
    return "已合进付款凭证并写入金蝶，自动销账"


def _check_fix(it, s=None, conf=None):
    p = it["payload"]
    ids = _fix_ids(p.get("carrier"), p.get("period"))
    if not ids:
        return {"status": "withdrawn", "how": "更正已全部撤掉，待办自动撤回"}
    left = _fix_left(ids, p)
    if not left:
        return {"status": "done", "how": _fix_how(p, ids)}
    return {"status": "open", "holder": "scene", "sub": "%d 笔要改 · 还剩 %d 笔没办" % (len(ids), len(left)),
            "state": "还没合进付款凭证", "warn": ""}


def fix_manual_close(it, by, vno):
    """处理人点「已在金蝶改好」（Q2：走打印更正单那条路的，系统没法知道专人改的是哪张凭证，只能人确认）。"""
    p = dict(it["payload"])
    ids = _fix_ids(p.get("carrier"), p.get("period"))
    p["manual_ids"] = sorted({int(x) for x in (p.get("manual_ids") or [])} | set(_fix_left(ids, p)))
    p["manual_vno"], p["manual_by"] = str(vno or "").strip()[:60], by
    ts.patch(it["id"], payload=p)
    return ts.finish(it["id"], by, "处理人确认已在金蝶改好（%s）" % (p["manual_vno"] or "没填凭证号"))


CHECKERS = {"fx_audit": _check_fx, "logi_accrual_audit": _check_accrual,
            "logi_voucher_audit": _check_voucher, "logi_fix": _check_fix}


# ============ 核对一轮 ============
def run_checks(ids=None, by=""):
    """把没办结的待办核一遍（ids=None 全部）。→ {checked, done, withdrawn, failed, kd_error}。
    金蝶连不上：在金蝶办的那几条只记「这次没核对成」，状态一律不动——绝不因为读不到就当成已审。"""
    items = [it for it in ts.list_open() if ids is None or it["id"] in ids]
    out = {"checked": 0, "done": 0, "withdrawn": 0, "failed": 0, "kd_error": ""}
    s = conf = None
    if any(ts.SCENES[it["scene"]]["kd"] for it in items):
        try:
            s, conf = kc.login()
        except Exception as e:
            out["kd_error"] = str(e)[:160]
    for it in items:
        now = ts._now()
        if ts.SCENES[it["scene"]]["kd"] and out["kd_error"]:
            ts.patch(it["id"], checked_ts=now, check_msg="没连上金蝶，这次没核对成")
            out["failed"] += 1
            continue
        try:
            r = CHECKERS[it["scene"]](it, s, conf)
        except Exception as e:
            ts.patch(it["id"], checked_ts=now, check_msg=("核对出错，这次没核对成：%s" % str(e))[:200])
            out["failed"] += 1
            continue
        out["checked"] += 1
        if r["status"] in ("done", "withdrawn"):
            if "payload" in r:
                ts.patch(it["id"], payload=r["payload"])
            ts.finish(it["id"], "", r.get("how") or "", r["status"])
            out[r["status"]] += 1
            continue
        vals = {k: r[k] for k in ("sub", "warn", "state", "holder", "payload") if k in r}
        ts.patch(it["id"], checked_ts=now, check_msg="", **vals)
    return out
