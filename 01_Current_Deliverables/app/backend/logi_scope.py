# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-06 | Author: Claude Opus 5.5 | Version: V2.846
# Description: 【物流·哪些供应商算物流】用户问「你是怎么知道哪些是物流的」「不应该写死吧，应该读金蝶的供应商列表之类的」。
#   原来四处都认编码前缀「物流运输服务」(钉钉请款单认收款方、金蝶付款单、支付凭证、复核台总表的计提)。
#   现在改成读金蝶供应商档案(BD_Supplier)的「供应商分组」：哪几个分组算物流存在设置里、页面上勾，名单从金蝶现读(缓存 12 小时)。
#   第一次没有设置时，按「已有请款单的供应商 + 工作台物流供应商档案里的金蝶编码」现在在金蝶哪个分组来定(不写死分组名)。
#   不走物流计提的排除规则(办公室快递月结、湖北顺丰速运各部门快递费)也挪进同一份设置，页面上维护。
#   金蝶读不到时用上一次存下的名单，不会因为金蝶一时不通就把物流商全认丢。
import time

import db
import kingdee_client as kc

KEY = "logi_scope"                # {"groups": [金蝶供应商分组编码], "excl_kw": [[关键词, 原因]], "excl_payee": [[收款方关键词, 原因]], "by", "at"}
CACHE_KEY = "logi_scope_cache"    # 上一次从金蝶读到的：{"at", "sups": [[码, 名, 组码]](只存范围内的), "all_groups": [[组码, 组名, 家数]]}
TTL = 12 * 3600
_MEM = {"ts": 0.0, "sups": None, "groups": None, "err": ""}     # sups: {码: (名, 组码)} 全量；groups: {组码: (组名, 家数)}
_DATA = {"t": 0.0, "val": None}       # data() 的结果记 60 秒：is_logi 会在循环里逐行调，不能每次都读库

# 排除规则的初始值(用户 2026-10-03 定的两条)，只在第一次建设置时写进去，之后以页面维护的为准
_SEED_EXCL_KW = [["办公室", "办公室快递月结"]]
_SEED_EXCL_PAYEE = [["湖北顺丰速运", "各部门快递费"]]


def _now():
    return time.strftime("%Y-%m-%d %H:%M")


def _read_kd():
    """金蝶供应商档案全量 → ({码: (名, 组码)}, {组码: (组名, 家数)})。一家供应商分配给几个组织会有几行，按编码去重。只读。"""
    s, conf = kc.login()
    rows = kc._query(s, conf, "BD_Supplier", [("FNumber", "码"), ("FName", "名"), ("FGroup.FNumber", "组码"), ("FGroup.FName", "组名")], "FNumber<>''")
    sups, gname = {}, {}
    for r in rows:
        c, n, g = str(r.get("码") or "").strip(), str(r.get("名") or "").strip(), str(r.get("组码") or "").strip()
        if not c or c in sups:
            continue
        sups[c] = (n, g)
        if g:
            gname[g] = str(r.get("组名") or "").strip()
    cnt = {}
    for _n, g in sups.values():
        if g:
            cnt[g] = cnt.get(g, 0) + 1
    return sups, {g: (gname.get(g, ""), cnt[g]) for g in cnt}


def _load(force=False):
    """内存里的金蝶名单(12 小时)。读金蝶失败：内存里有旧的继续用；都没有就留空，由 data() 用库里存的兜底。"""
    if not force and _MEM["sups"] is not None and time.time() - _MEM["ts"] < TTL:
        return
    try:
        sups, groups = _read_kd()
        if sups:
            _MEM.update(ts=time.time(), sups=sups, groups=groups, err="")
            return
        _MEM["err"] = "金蝶没返回供应商"
    except Exception as e:
        _MEM["err"] = str(e)[:160]


def _init_scope():
    """第一次：看已有请款单的供应商、工作台物流供应商档案里的金蝶编码，现在在金蝶哪些分组——那些分组就是物流范围。"""
    codes = set()
    try:
        from kernels import logistics_review_store as store
        from sqlalchemy import select
        with db._engine.connect() as c:
            codes |= {str(x[0] or "").strip() for x in c.execute(select(store.payreq.c.sup_code).distinct()).all()}
    except Exception:
        pass
    try:
        codes |= {str(s.get("kd_code") or "").strip() for s in (db.list_logi_suppliers() or [])}
    except Exception:
        pass
    sups = _MEM["sups"] or {}
    groups = sorted({sups[c][1] for c in codes if c in sups and sups[c][1]})
    if not groups:
        return None
    sc = {"groups": groups, "excl_kw": _SEED_EXCL_KW, "excl_payee": _SEED_EXCL_PAYEE, "by": "系统初始化（按已有物流请款单的供应商所在的金蝶分组）", "at": _now()}
    db.set_setting(KEY, sc, "系统")
    return sc


def get_scope():
    sc = db.get_setting(KEY, None)
    if not sc or not sc.get("groups"):
        _load()
        sc = _init_scope() or sc or {"groups": [], "excl_kw": _SEED_EXCL_KW, "excl_payee": _SEED_EXCL_PAYEE}
    return sc


def data(force=False):
    """→ {groups, codes:set, name2code, code2name, at, stale, err}。codes＝范围内分组的全部金蝶供应商编码。"""
    if not force and _DATA["val"] is not None and time.time() - _DATA["t"] < 60:
        return _DATA["val"]
    _load(force)
    sc = get_scope()
    gs = set(sc.get("groups") or [])
    if _MEM["sups"] is not None:
        inscope = [(c, n, g) for c, (n, g) in _MEM["sups"].items() if g in gs]
        at, stale = time.strftime("%Y-%m-%d %H:%M", time.localtime(_MEM["ts"])), False
        old = db.get_setting(CACHE_KEY, None) or {}
        new_s = sorted([list(x) for x in inscope])
        if old.get("sups") != new_s:          # 名单变了才落库(下次金蝶读不到时兜底用)
            db.set_setting(CACHE_KEY, {"at": at, "sups": new_s, "all_groups": sorted([g, v[0], v[1]] for g, v in (_MEM["groups"] or {}).items())}, "系统")
    else:
        old = db.get_setting(CACHE_KEY, None) or {}
        inscope = [tuple(x) for x in old.get("sups") or [] if len(x) == 3 and x[2] in gs]
        at, stale = old.get("at") or "", True
    n2c, c2n = {}, {}
    for c, n, _g in sorted(inscope):
        c2n[c] = n
        if n:
            n2c.setdefault(n, c)
    val = {"groups": sorted(gs), "codes": set(c2n), "name2code": n2c, "code2name": c2n, "at": at, "stale": stale, "err": _MEM["err"],
           "ts": _MEM["ts"]}
    _DATA.update(t=time.time(), val=val)
    return val


def is_logi(code):
    return bool(code) and str(code).strip() in data()["codes"]


def in_filters(field, size=100):
    """金蝶查询用的过滤片段：[\"字段 in ('码1','码2',…)\"]，一段最多 size 个编码；范围里没有供应商返回 []。"""
    codes = sorted(data()["codes"])
    return ["%s in (%s)" % (field, ",".join("'%s'" % c.replace("'", "''") for c in codes[i:i + size])) for i in range(0, len(codes), size)]


def excl_rules():
    sc = get_scope()
    return ([tuple(x) for x in sc.get("excl_kw") or [] if len(x) == 2 and x[0]], [tuple(x) for x in sc.get("excl_payee") or [] if len(x) == 2 and x[0]])


def view(force=False):
    """页面用：现在的范围 + 金蝶全部供应商分组(家数、勾没勾) + 范围内供应商名单。"""
    d = data(force)
    sc = get_scope()
    if _MEM["groups"] is not None:
        allg = sorted(([g, v[0], v[1]] for g, v in _MEM["groups"].items()), key=lambda x: x[0])
    else:
        allg = (db.get_setting(CACHE_KEY, None) or {}).get("all_groups") or []
    on = set(sc.get("groups") or [])
    return {"ok": True, "groups": [{"code": g, "name": n, "n": k, "on": g in on} for g, n, k in allg],
            "n": len(d["codes"]), "sups": [{"code": c, "name": d["code2name"][c]} for c in sorted(d["codes"])],
            "excl_kw": sc.get("excl_kw") or [], "excl_payee": sc.get("excl_payee") or [], "by": sc.get("by") or "", "at": sc.get("at") or "",
            "kd_at": d["at"], "stale": d["stale"], "err": d["err"]}


def save(groups, excl_kw, excl_payee, user):
    _load()
    known = set((_MEM["groups"] or {}).keys()) or {x[0] for x in (db.get_setting(CACHE_KEY, None) or {}).get("all_groups") or []}
    gs = sorted({str(g).strip() for g in groups or [] if str(g).strip()})
    bad = [g for g in gs if known and g not in known]
    if bad:
        raise ValueError("金蝶里没有这些供应商分组：%s" % "、".join(bad))
    if not gs:
        raise ValueError("至少要勾一个供应商分组，不然一家物流商都认不到")

    def clean(xs):
        return [[str(a).strip()[:30], str(b).strip()[:30] or "不走物流计提"] for a, b in (xs or []) if str(a).strip()]
    sc = {"groups": gs, "excl_kw": clean(excl_kw), "excl_payee": clean(excl_payee), "by": user, "at": _now()}
    db.set_setting(KEY, sc, user)
    _DATA.update(t=0.0, val=None)
    return sc
