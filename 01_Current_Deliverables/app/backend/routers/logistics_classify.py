# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-25 | Author: Kimi | Version: V2.1007
# Description: 物流账单复核·费用归类接口。V2.1007 与逐单/逐笔共享主体回填和单头缓存；计提故障显式报错。
#   原说明：只读金蝶，单据头(销售组织/领料部门/需求组织/调入组织)＋物料档案分组；
#   计提取 logistics_review._accr_entries(本月逐笔计提)。判定逻辑在 kernels/logistics_classify.py。
#   GET  /api/logistics-review/classify        一家一个月：每张单的归类结论 + 和计提对照表(30 分钟缓存，fresh=1 重取)
#   GET  /api/logistics-review/classify-rules  对照表设置(物料分组→业务线、看不出业务线的分组、调拨仓库规则)
#   POST /api/logistics-review/classify-rules  保存设置(存 settings 键 logi_cls_rules)；空 body.cfg = 恢复默认
#   GET  /api/logistics-review/dups            本家本月怀疑重复收费的单(本月+上月所有承运商的运输费里撞单号)
#   POST /api/logistics-review/dup-ok          标「不是重复」/撤销(存 settings 键 logi_dup_ok)
import time

from fastapi import APIRouter, Request
from sqlalchemy import select

from core import JSONResponse, db
import kingdee_client as kc
from kernels import logistics_classify as lc
from routers import logistics_review as LR

router = APIRouter()
_CFG_KEY = "logi_cls_rules"
_TTL = 1800

# 每种单据要的单据头字段；字段 Key 按单据写死(大小写各单据不同)，一个字段一次查，查不到就跳过，不影响别的
_HEAD_FIELDS = {
    "SAL_OUTSTOCK": [("FSaleOrgId.FName", "销售组织"), ("FStockOrgId.FName", "库存组织")],
    "SAL_RETURNSTOCK": [("FSaleOrgId.FName", "销售组织"), ("FStockOrgId.FName", "库存组织")],
    "STK_MisDelivery": [("FDeptId.FName", "部门"), ("FStockOrgId.FName", "库存组织")],
    "STK_InStock": [("FDemandOrgId.FName", "需求组织"), ("FStockOrgId.FName", "库存组织")],
    "PUR_MRB": [("FStockOrgId.FName", "库存组织")],
    "STK_TransferIn": [("FStockOrgId.FName", "调入组织")],
    "STK_TransferOut": [("FStockInOrgID.FName", "调入组织"), ("FStockOrgID.FName", "调出组织")],
}


def _cfg():
    return lc.merge_cfg(db.get_setting(_CFG_KEY, None))


def _fetch_heads(s, conf, by_form):
    """{form: {单号}} → {单号: {字段中文名: 值}}。只读。"""
    out = {}
    for form, docs in by_form.items():
        docs = sorted(docs)
        for fk, cn in _HEAD_FIELDS.get(form, []):
            for i in range(0, len(docs), 150):
                flt = "FBillNo in (%s)" % ",".join("'%s'" % d.replace("'", "") for d in docs[i:i + 150])
                try:
                    rr = kc._query(s, conf, form, [("FBillNo", "单号"), (fk, cn)], flt)
                except Exception:
                    break
                for r in rr:
                    no, v = str(r.get("单号") or ""), r.get(cn)
                    if no and v not in (None, ""):
                        out.setdefault(no, {}).setdefault(cn, str(v))
    return out


def _bill_docs(carrier, period, fresh=False):
    """中间表逐单明细 → 一张单一条(几行账单合并金额)。没有金蝶单号的行(仓储费、搬运费…)第②步本来就不列，这里也不判。"""
    BL = LR.BL
    with db._engine.connect() as c:
        rows = [dict(r) for r in c.execute(select(BL).where(
            (BL.c.carrier == carrier) & (BL.c.period == period) & (BL.c.grain == "detail"))).mappings().all()]
    LR._backfill_subjects(rows, carrier, fresh=fresh)
    by = {}
    for r in rows:
        d0 = (r.get("doc_no") or "").split("+")[0].strip()
        if not d0 or d0 == "无单据":
            continue
        x = by.setdefault(d0, {"key": d0, "docs": [d0], "subject": LR._eff_subject(r), "annot": "", "amount": 0.0, "manual": ""})
        x["amount"] += float(r.get("amount") or 0)
        if not x["annot"] and (r.get("annot") or "").strip():
            x["annot"] = r["annot"].strip()
        if r.get("fee_ovr") and not x["manual"]:
            x["manual"] = str(r["fee_ovr"])
    return list(by.values())


def _build(carrier, period, fresh=False):
    docs = _bill_docs(carrier, period, fresh=fresh)
    if len(docs) > LR._FULL_DOCS:
        return {"ok": False, "msg": "%s 本月 %d 张单，超过 %d 张的先不整家判归类（要拉的金蝶太多）" % (carrier, len(docs), LR._FULL_DOCS)}
    by_form = {}
    for d in docs:
        for n in d["docs"]:
            pre = "".join(ch for ch in n if ch.isalpha())
            by_form.setdefault((LR._FORM_BY_PREFIX.get(pre) or ["SAL_OUTSTOCK"])[0], set()).add(n)
    kd, groups, accr = {}, {}, []
    if by_form:
        s, conf = kc.login()
        mats = LR._fetch_doc_materials(s, conf, by_form, fresh=fresh)
        real = {}
        for n, ms in mats.items():
            real.setdefault(ms[0].get("_form"), set()).add(n)
        heads = LR._fetch_doc_heads(s, conf, real, fresh=fresh)
        codes = set()
        for n, ms in mats.items():
            cs = [str(m.get("编码") or "") for m in ms if m.get("编码")]
            codes.update(cs)
            kd[n] = {"form": ms[0].get("_form"), "head": heads.get(n, {}), "party": ms[0].get("往来") or "", "codes": cs}
        groups = {k: v.get("grp", "") for k, v in (kc.fetch_material_attrs(codes, s, conf) or {}).items()}
    accr = (LR._accr_entries(carrier, period, LR._carrier_full(carrier)) or {}).get("entries") or []
    orgs = {o.get("full_name"): o.get("short_name") for o in (db.list_orgs() or []) if o.get("full_name")}
    res = lc.classify(docs, kd, groups, accr, _cfg(), lambda x: orgs.get(x, x), carrier)
    man = {d["key"]: d["manual"] for d in docs if d["manual"]}
    for x in res["docs"]:
        if x["key"] in man:
            x["manual"] = man[x["key"]]     # 人工定过费用类型的(V2.788 特批)，页面上标出来，结论照常给
    res.update(ok=True, carrier=carrier, period=period, n_docs=len(docs), kd_missing=sorted(set(n for d in docs for n in d["docs"]) - set(kd)),
               built_at=time.strftime("%Y-%m-%d %H:%M"))
    return res


@router.get("/api/logistics-review/classify")
def classify(request: Request, carrier: str = "", period: str = "", fresh: int = 0):
    if not LR._perm(request):
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    if not carrier or not period:
        return JSONResponse({"ok": False, "msg": "要带承运商和账期"}, status_code=400)
    k = ("cls", carrier, period)
    cc = LR._ACCR_CACHE.get(k)
    if cc and not fresh and time.time() - cc[2] < (_TTL if cc[0].get("accr_ok") else 300):   # 计提没取到的只缓存 5 分钟
        return cc[0]
    try:
        res = _build(carrier, period, fresh=bool(fresh))
    except Exception as e:
        return JSONResponse({"ok": False, "msg": "取金蝶失败：%s" % str(e)[:200]}, status_code=502)
    if res.get("ok"):
        LR._ACCR_CACHE[k] = (res, None, time.time())
    return res


@router.get("/api/logistics-review/classify-rules")
def classify_rules(request: Request):
    if not LR._perm(request):
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    saved = db.get_setting(_CFG_KEY, None)
    return {"ok": True, "cfg": _cfg(), "default": lc.DEFAULT_CFG, "customized": bool(saved)}


def _check_cfg(c):
    """→ 出错说明；没错返回空串。"""
    if not isinstance(c.get("group_biz"), dict) or not all(isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip()
                                                         for k, v in c["group_biz"].items()):
        return "「物料分组→业务线」每一行都要填分组和业务线"
    for f in ("skip_groups", "unknown_groups"):
        if not isinstance(c.get(f), list) or not all(isinstance(x, str) for x in c[f]):
            return "分组清单格式不对"
    if not isinstance(c.get("outside_wh"), dict) or not all(isinstance(v, dict) for v in c["outside_wh"].values()):
        return "工厂外仓承运商格式不对"
    for ca, v in c["outside_wh"].items():
        if v.get("raw_biz") and v["raw_biz"] not in set(c["group_biz"].values()):
            return "工厂外仓「%s」的原料默认业务线「%s」不在业务线清单里" % (ca, v["raw_biz"])
    if not isinstance(c.get("stock_rules"), list):
        return "仓库规则格式不对"
    for i, r in enumerate(c["stock_rules"], 1):
        if not isinstance(r, dict) or not str(r.get("match") or "").strip():
            return "第 %d 条仓库规则没填仓库名" % i
        if r.get("side") not in ("in", "out", "any"):
            return "第 %d 条仓库规则的「看哪一头」只能是 调入仓 / 调出仓 / 两头" % i
        if r.get("fee") not in ("入库运费", "出库运费"):
            return "第 %d 条仓库规则的费用类型只能是 入库运费 / 出库运费" % i
        if r.get("col") and r["col"] not in lc.IN_COLS:
            return "第 %d 条仓库规则的计提列「%s」不认识，只能是：%s" % (i, r["col"], "、".join(lc.IN_COLS))
        if r.get("subj") and r["subj"] not in lc.OUT_DEPT:
            return "第 %d 条仓库规则的主体「%s」不认识" % (i, r["subj"])
    return ""


@router.post("/api/logistics-review/classify-rules")
async def classify_rules_save(request: Request):
    u = LR._perm(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    b = await request.json()
    cfg = b.get("cfg")
    if cfg:
        cfg = lc.merge_cfg(cfg)
        err = _check_cfg(cfg)
        if err:
            return JSONResponse({"ok": False, "msg": err}, status_code=400)
        db.set_setting(_CFG_KEY, cfg, LR._uname(u))
        db.audit(LR._uname(u), "物流复核-归类规则", "", "分组 %d 条 / 仓库规则 %d 条" % (len(cfg["group_biz"]), len(cfg["stock_rules"])))
    else:
        db.set_setting(_CFG_KEY, None, LR._uname(u))
        db.audit(LR._uname(u), "物流复核-归类规则", "", "恢复默认")
    for k in [k for k in list(LR._ACCR_CACHE) if isinstance(k, tuple) and k and k[0] == "cls"]:
        LR._ACCR_CACHE.pop(k, None)       # 各家各月的归类结果按新规则重算
    return {"ok": True, "cfg": _cfg()}


# ---------------- 怀疑重复收费
_DUP_OK_KEY = "logi_dup_ok"


def _prev_period(p):
    y, m = int(p[:4]), int(p[5:7])
    return "%04d-%02d" % (y - 1, 12) if m == 1 else "%04d-%02d" % (y, m - 1)


@router.get("/api/logistics-review/dups")
def dups(request: Request, carrier: str = "", period: str = ""):
    if not LR._perm(request):
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    if not carrier or len(period) < 7:
        return JSONResponse({"ok": False, "msg": "要带承运商和账期"}, status_code=400)
    BL = LR.BL
    with db._engine.connect() as c:
        rows = [dict(r) for r in c.execute(select(BL.c.carrier, BL.c.period, BL.c.doc_no, BL.c.fee_item, BL.c.amount).where(
            (BL.c.grain == "detail") & (BL.c.period.in_((period, _prev_period(period)))))).mappings().all()]
    mine = [r for r in rows if not (r["carrier"] == carrier and r["period"] != period)]   # 本家只看本月
    found = lc.find_dups(carrier, mine)
    oks = db.get_setting(_DUP_OK_KEY, None) or {}
    for d, x in found.items():
        x["ok"] = oks.get("%s|%s|%s" % (carrier, period, d))
    return {"ok": True, "carrier": carrier, "period": period, "docs": found,
            "rule": "同一张单本月或上月有别家也收了运输费、或本家同一张单有两行金额一样的运输费；装卸、仓储、冷藏、保费、提货送货这些不算运输费"}


@router.post("/api/logistics-review/dup-ok")
async def dup_ok(request: Request):
    u = LR._perm(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    b = await request.json()
    carrier, period, doc = (b.get("carrier") or "").strip(), (b.get("period") or "").strip(), (b.get("doc") or "").strip()
    if not (carrier and period and doc):
        return JSONResponse({"ok": False, "msg": "缺承运商/账期/单号"}, status_code=400)
    lk = LR._locked(carrier, period)
    if lk:
        return lk
    allm = dict(db.get_setting(_DUP_OK_KEY, None) or {})
    k = "%s|%s|%s" % (carrier, period, doc)
    if b.get("ok"):
        allm[k] = {"by": LR._uname(u), "at": time.strftime("%Y-%m-%d %H:%M"), "reason": str(b.get("reason") or "")[:200]}
    else:
        allm.pop(k, None)
    db.set_setting(_DUP_OK_KEY, allm, LR._uname(u))
    db.audit(LR._uname(u), "物流复核-重复收费", "%s %s" % (carrier, period), "%s %s" % (doc, "不是重复" if b.get("ok") else "撤销"))
    return {"ok": True, "item": allm.get(k)}
