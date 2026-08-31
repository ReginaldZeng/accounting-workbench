# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-08-05 | Author: Claude / c | Version: V2.172
# Description: 【汇率录入】路由（V2.172 从 app.py 拆出）。
#              本文件是「汇率录入」这条工具线在后端的唯一落点：改这条线的接口只动本文件，
#              不再碰 app.py —— 这样多条需求并行开发时互不冲突。
#              共享的配置/期间/权限判定见 core.py；算法在 kernels/fx_rate.py。
#              app.py 只负责 include_router(router)，不感知本文件内部。

from fastapi import APIRouter
from fastapi import Request
from kernels import fx_rate as fx
import kingdee_client as kc
import mailer
import notifier
import threading
import time

from core import (
    JSONResponse, _KD_STATUS_CN, _require_perm, datetime, db, os,
)

router = APIRouter()


# ==================== 汇率录入（V2.159）：人行中间价 → 建汇率规则 → 金蝶 BD_Rate ====================
# 内核 kernels/fx_rate.py（抓取/规则/交叉/闸门/复核，P1）+ kingdee_client 通用写入（P2）。
# 本层只做编排：抓取生成 → 回读去重 → 跑闸门 → 写入(save+submit，只提交不审核) → 留痕。


def _fxrate_prev_beg(year, month):
    """上月最后一天（生效=失效=当天）——回读上月月末条作偏离闸门参考起点。"""
    py, pm = (year - 1, 12) if month == 1 else (year, month - 1)
    return fx.month_last_day(py, pm).isoformat()


def _fxrate_prev_rates(existing_rows, year, month):
    """从金蝶已有记录里取「上月月末·对某币中间价」作偏离闸门参考；没有则空。"""
    last = _fxrate_prev_beg(year, month)
    prev = {}
    for e in existing_rows:
        if str(e.get("生效", ""))[:10] == last and str(e.get("失效", ""))[:10] == last:
            prev[(e.get("原币码"), e.get("目标币码"))] = e.get("汇率")
    return prev


def _fxrate_row_out(r):
    """把内核行转成 JSON 安全的字典（Decimal → str）。"""
    return {k: (str(v) if k == "rate" else v) for k, v in r.items()}


def _fxrate_write(year, month, org, res, existing, operator, s, conf):
    """手工/自动共用的写入循环：逐条 save+submit（只提交不审核），已存在/本工具已录跳过（不覆盖）。
    operator 记入留痕（自动跑批传「系统自动」）。返回逐条结果 list。"""
    # 防重【以金蝶为准】(find_existing)：不预载台账来拦重写——否则金蝶记录被删后台账残留会误把该期全跳过、再也写不进(V2.167 修)。
    # posted 只在本次循环内防同期重复键（写一条即登记），跨次幂等由 find_existing 对金蝶实查保证。
    posted = {}
    results = []
    for r in res["rows"]:
        pair = f"{r['from_name']}→{r['to_name']}"
        item = {"pair": pair, "rate": str(r["rate"]), "beg": r["beg_date"], "end": r["end_date"],
                "kind": r["kind"], "basis": r.get("basis", ""), "source_date": r.get("source_date", "")}
        key = db._fx_key(org, r["from_code"], r["to_code"], r["beg_date"], r["end_date"])
        dup = fx.find_existing(existing, r, org)
        if dup:
            who = dup.get("使用组织") or dup.get("创建组织") or ""
            item.update(status="skipped", msg=f"金蝶已存在（{who}）同币对·同生效区间，按不覆盖跳过（汇率全集团共享，不分组织）")
        elif key in posted:
            item.update(status="skipped", msg="本次已写入同期同币对，跳过防重")
        else:
            try:
                saved = kc.save_bill("BD_Rate", fx.build_rate_model(r, org), s, conf)
                kc.submit_bill("BD_Rate", saved["id"], s, conf)     # 只提交，不审核
                db.log_fx_post(year, month, org, pair, r["from_code"], r["to_code"], r["rate"],
                               r["beg_date"], r["end_date"], r["kind"], saved["id"], operator)
                item.update(status="posted", kd_id=saved["id"])
                posted[key] = {"kd_id": saved["id"]}
            except kc.KingdeeError as e:
                item.update(status="failed", msg=f"金蝶写入/提交失败：{e}")
        results.append(item)
    return results


@router.get("/api/fxrate/orgs")
def fxrate_orgs(request: Request):
    """组织可选下拉·受控清单（默认 101）。"""
    return {"ok": True, "orgs": fx.FX_ORGS, "default": fx.DEFAULT_ORG}


@router.get("/api/fxrate/status")
def fxrate_status(request: Request, year: int = 2026, org: str = ""):
    """录入状态看板：**以金蝶为准**（不管谁录的），逐月「有没有 + 待审核/已审核 + 工具还是人工」。
    - 有没有 / 审核态：读金蝶 BD_Rate + FDocumentStatus（B已提交=待审核、C已审核），历史/人工录的也照实显示。
    - 工具 vs 人工：读金蝶备注标记 FX_MARK 判定——**跟着账套走、换后端服务器不丢**，不依赖本地留痕。
    - 留痕仅用于撤销定位：有本机留痕的工具记录才给从这里撤；人工的、别机录的只显示不代删。
    每月按「生效日所属月」归格（当月 = 3 条区间条(生效M/1) + 5 条月末条(生效M月末) = 满 8 条）。"""
    org = org or fx.DEFAULT_ORG
    try:
        s, conf = kc.login()
        # 全组织：汇率是全集团共享的一条时间线（101/107 接力、不重叠），看板按全部组织显示才是真相
        kd = kc.fetch_bd_rate(use_org=None, beg_from=f"{int(year)}-01-01", s=s, conf=conf)
    except kc.KingdeeError as e:
        return {"ok": False, "msg": f"连接金蝶失败（状态看板以金蝶为准，需读金蝶实况）：{e}"}
    logmap = {str(l["kd_id"]): l["id"] for l in db.fx_posts_year(int(year), org) if l.get("kd_id")}
    by_m = {}
    for r in kd:
        beg = str(r.get("生效") or "")[:10]
        if not beg.startswith(str(int(year))):
            continue
        by_m.setdefault(int(beg[5:7]), []).append(r)
    months = []
    for m in range(1, 13):
        rows = by_m.get(m, [])
        if not rows:
            months.append({"month": m, "present": 0, "state": "未录入", "source": "", "records": []})
            continue
        recs, aud, tool = [], 0, 0
        for r in rows:
            st = r.get("状态")
            desc = r.get("描述") or ""
            is_aud = (st == "C")
            is_tool = fx.is_tool_mark(desc)
            aud += 1 if is_aud else 0
            tool += 1 if is_tool else 0
            fid = str(r.get("FRATEID"))
            log_id = logmap.get(fid)
            recs.append({"pair": f"{r.get('原币')}→{r.get('目标币')}", "rate": str(r.get("汇率")),
                         "beg": str(r.get("生效"))[:10], "end": str(r.get("失效"))[:10],
                         "kd_status": _KD_STATUS_CN.get(st, st or "?"), "audited": is_aud,
                         "source": "工具" if is_tool else "人工", "kd_id": fid, "log_id": log_id,
                         "org": r.get("创建组织") or r.get("使用组织") or "",   # 创建组织（汇率全集团共享，标出谁建的）
                         "desc": desc.replace(fx.FX_MARK, "").strip(),   # 去掉标记前缀，只留算式/出处给人看
                         "revocable": bool(is_tool and not is_aud and log_id)})
        n = len(rows)
        state = "已审核" if aud == n else ("待审核" if aud == 0 else "部分审核")
        source = "工具" if tool == n else ("人工" if tool == 0 else "工具+人工")
        months.append({"month": m, "present": n, "audited": aud, "pending": n - aud,
                       "tool": tool, "manual": n - tool, "state": state, "source": source, "records": recs})
    return {"ok": True, "year": year, "org": org, "months": months}


@router.post("/api/fxrate/preview")
def fxrate_preview(body: dict, request: Request):
    """抓人行 → 生成 8 条 → 跑四道闸门 → 回读金蝶标「已存在」（只读，不写）。body:{year,month,org}"""
    try:
        year = int(body.get("year") or 0)
        month = int(body.get("month") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "msg": "结账年月格式不对"}
    if not (1 <= month <= 12):
        return {"ok": False, "msg": "请选择结账月份（1-12）"}
    org = str(body.get("org") or fx.DEFAULT_ORG)
    if org not in {o["code"] for o in fx.FX_ORGS}:
        return {"ok": False, "msg": f"组织 {org} 不在受控清单内"}
    try:
        res = fx.generate_rows(year, month, org)
    except fx.FxError as e:
        return {"ok": False, "msg": f"取人行汇率失败：{e}"}
    except Exception as e:
        return {"ok": False, "msg": f"抓取/解析人行公告出错：{e}"}
    try:
        s, conf = kc.login()
        existing = kc.fetch_bd_rate(use_org=None, beg_from=_fxrate_prev_beg(year, month), s=s, conf=conf)  # 全组织：金蝶汇率不重叠约束跨组织
    except kc.KingdeeError as e:
        return {"ok": False, "msg": f"连接金蝶失败（预览需回读已存在的汇率去重）：{e}"}
    gates = fx.run_gates(res, prev_rates=_fxrate_prev_rates(existing, year, month))
    rows = []
    for r in res["rows"]:
        out = _fxrate_row_out(r)
        out["exists"] = bool(fx.find_existing(existing, r, org))
        rows.append(out)
    n_new = sum(1 for r in rows if not r["exists"])
    return {"ok": True, "year": year, "month": month, "org": org,
            "org_name": next((o["name"] for o in fx.FX_ORGS if o["code"] == org), org),
            "rows": rows, "gates": gates, "warnings": res["warnings"],
            "month_end_ann": res["month_end_ann"], "next_range_ann": res["next_range_ann"],
            "n_new": n_new, "n_exist": len(rows) - n_new}


@router.post("/api/fxrate/post")
def fxrate_post(body: dict, request: Request):
    """确认后写入金蝶：服务端重新抓取生成（不信客户端汇率值，只抄不算的完整性）→ save+submit（只提交不审核）
    → 已存在跳过（不覆盖，铁律5）→ 留痕。手工模式：block 类闸门拦死；偏离(hold)由人点写=人工确认，放行。
    body:{year,month,org}"""
    u = _require_perm(request, "fxrate_post")
    if not u:
        return JSONResponse({"ok": False, "msg": "无「汇率录入·写入金蝶」权限（写全账套基础资料的敏感操作，请联系管理员开通）"}, status_code=403)
    try:
        year = int(body.get("year") or 0)
        month = int(body.get("month") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "msg": "结账年月格式不对"}
    if not (1 <= month <= 12):
        return {"ok": False, "msg": "请选择结账月份（1-12）"}
    org = str(body.get("org") or fx.DEFAULT_ORG)
    if org not in {o["code"] for o in fx.FX_ORGS}:
        return {"ok": False, "msg": f"组织 {org} 不在受控清单内"}
    try:
        res = fx.generate_rows(year, month, org)
    except Exception as e:
        return {"ok": False, "msg": f"取人行汇率失败，未写入任何数据：{e}"}
    try:
        s, conf = kc.login()
        existing = kc.fetch_bd_rate(use_org=None, beg_from=_fxrate_prev_beg(year, month), s=s, conf=conf)  # 全组织：金蝶汇率不重叠约束跨组织
    except kc.KingdeeError as e:
        return {"ok": False, "msg": f"连接金蝶失败，未写入任何数据：{e}"}
    gates = fx.run_gates(res, prev_rates=_fxrate_prev_rates(existing, year, month))
    blocking = [g for g in gates["gates"] if g["status"] == "block"]
    if blocking:
        return {"ok": False, "gates": gates,
                "msg": "机器闸门未通过，未写入任何数据：" + "；".join(f"{g['name']}（{g['detail']}）" for g in blocking)}
    results = _fxrate_write(year, month, org, res, existing, u["name"], s, conf)
    n_ok = sum(1 for r in results if r["status"] == "posted")
    n_skip = sum(1 for r in results if r["status"] == "skipped")
    n_fail = len(results) - n_ok - n_skip
    db.audit(u["name"], "汇率录入-写入金蝶", f"{year}年{month}月·组织{org}",
             f"应建{len(results)}：写入{n_ok} 跳过{n_skip} 失败{n_fail}")
    notify_res = None
    if n_ok:      # 真写入了才通知；全跳过(已存在)不打扰
        rep = {"year": year, "month": month, "org": org, "org_name": _fxrate_org_name(org),
               "status": "written" if n_fail == 0 else "partial",
               "msg": f"写入 {n_ok} 条" + (f"，跳过 {n_skip} 条" if n_skip else "") + (f"，失败 {n_fail} 条" if n_fail else "")
                      + "（手动录入）",
               "results": results, "gates": gates["gates"],
               "source": {"month_end": res.get("month_end_ann") or {}, "next_range": res.get("next_range_ann") or {}}}
        notify_res = _fxrate_send_report_notify(rep)
    return {"ok": True, "year": year, "month": month, "org": org,
            "写入": n_ok, "跳过": n_skip, "失败": n_fail, "gates": gates, "results": results, "notify": notify_res}


@router.get("/api/fxrate/posted")
def fxrate_posted(request: Request, year: int = 0, month: int = 0, org: str = ""):
    """列出本工具某结账年月×组织录入金蝶的汇率（台账 + 金蝶实时状态），供撤销。"""
    if not _require_perm(request, "fxrate_post"):
        return JSONResponse({"ok": False, "msg": "无「汇率录入·写入金蝶」权限"}, status_code=403)
    if not (1 <= int(month or 0) <= 12):
        return {"ok": False, "msg": "请指定结账月份（1-12）"}
    org = org or fx.DEFAULT_ORG
    logs = db.list_fx_posts(int(year), int(month), org)
    if not logs:
        return {"ok": True, "year": year, "month": month, "org": org, "items": []}
    try:
        s, conf = kc.login()
        ids = [str(l["kd_id"]) for l in logs if l.get("kd_id")]
        stmap = {}
        if ids:
            rows = kc._query(s, conf, "BD_Rate", [("FRATEID", "id"), ("FDocumentStatus", "st")],
                             "FRATEID in (%s)" % ",".join(ids), "")
            stmap = {str(r["id"]): r.get("st") for r in rows}
    except kc.KingdeeError as e:
        return {"ok": False, "msg": f"连接金蝶失败：{e}"}
    items = []
    for l in logs:
        st = stmap.get(str(l["kd_id"]))
        exists = st is not None
        items.append({"id": l["id"], "pair": l["pair"], "rate": l["rate"],
                      "beg": l["beg_date"], "end": l["end_date"],
                      "录入人": l["operator"], "录入时间": l["ts"],
                      "金蝶状态": ("已删除" if not exists else _KD_STATUS_CN.get(st, st or "草稿")),
                      # 未审核(C 以外)都可从这里撤：草稿直删、提交态(B)先撤销再删；已审核(C)须去金蝶反审核
                      "可撤销": (not exists) or st != "C"})
    return {"ok": True, "year": year, "month": month, "org": org, "items": items}


@router.post("/api/fxrate/unpost")
def fxrate_unpost(body: dict, request: Request):
    """撤销：删本工具录入金蝶的汇率（草稿直删；提交态先 CancelAssign 撤销再删；已审核不删）。body:{ids:[台账id]}"""
    u = _require_perm(request, "fxrate_post")
    if not u:
        return JSONResponse({"ok": False, "msg": "无「汇率录入·写入金蝶」权限"}, status_code=403)
    ids = body.get("ids") or []
    if not ids:
        return {"ok": False, "msg": "没有勾选要撤销的记录"}
    try:
        s, conf = kc.login()
    except kc.KingdeeError as e:
        return {"ok": False, "msg": f"连接金蝶失败，未撤销任何记录：{e}"}
    results = []
    for lid in ids:
        lg = db.get_fx_post(lid)
        if not lg:
            results.append({"id": lid, "status": "skipped", "msg": "台账无此记录（可能已撤销）"})
            continue
        item = {"id": lid, "pair": lg["pair"]}
        kid = lg["kd_id"]
        try:
            rows = kc._query(s, conf, "BD_Rate", [("FRATEID", "id"), ("FDocumentStatus", "st")], f"FRATEID={kid}", "")
        except kc.KingdeeError as e:
            item.update(status="failed", msg=f"查状态失败：{e}")
            results.append(item)
            continue
        if not rows:
            db.delete_fx_post_log(lid)
            item.update(status="cleared", msg="金蝶里已不存在，已清理台账")
        elif (rows[0].get("st") or "") == "C":
            item.update(status="blocked", msg="金蝶已审核，请先去金蝶反审核再撤销")
        else:
            try:
                if (rows[0].get("st") or "") == "B":
                    kc.unsubmit_bill("BD_Rate", kid, s, conf)     # 提交态先撤销
                kc.delete_bill("BD_Rate", kid, s, conf)
                db.delete_fx_post_log(lid)
                item.update(status="deleted", msg="已撤销并删除")
            except kc.KingdeeError as e:
                item.update(status="failed", msg=f"撤销/删除失败：{e}")
        results.append(item)
    n_del = sum(1 for r in results if r["status"] in ("deleted", "cleared"))
    n_block = sum(1 for r in results if r["status"] == "blocked")
    db.audit(u["name"], "汇率录入-撤销", f"{len(ids)}条", f"撤销{n_del} 拦下{n_block}")
    return {"ok": True, "撤销": n_del, "拦下": n_block, "失败": len(results) - n_del - n_block, "results": results}


@router.post("/api/fxrate/history")
def fxrate_history(body: dict, request: Request):
    """历史复核：金蝶已建汇率对回人行，标偏差 + 已知豁免（只读）。body:{org, from_date}。
    注：按月抓人行公布并缓存，区间越宽越慢。"""
    org = str(body.get("org") or fx.DEFAULT_ORG)
    from_date = str(body.get("from_date") or "2025-10-01")
    try:
        s, conf = kc.login()
        kd = kc.fetch_bd_rate(use_org=org, beg_from=from_date, s=s, conf=conf)
    except kc.KingdeeError as e:
        return {"ok": False, "msg": f"连接金蝶失败：{e}"}
    cache = {}

    def _pack(y, m):
        if (y, m) not in cache:
            cache[(y, m)] = fx.month_rates(y, m)
        return cache[(y, m)]

    def lookup(r):
        beg = str(r.get("生效") or "")[:10]
        if len(beg) < 7:
            return None
        return fx.pboc_value_for(r, _pack(int(beg[:4]), int(beg[5:7])))

    try:
        out = fx.compare_history(kd, lookup)
    except Exception as e:
        return {"ok": False, "msg": f"复核比对出错：{e}"}
    items = [{"org": r.get("使用组织"), "pair": f"{r.get('原币')}→{r.get('目标币')}",
              "beg": str(r.get("生效"))[:10], "acct": str(r.get("汇率")),
              "pboc": r.get("pboc"), "diff": r.get("diff"), "verdict": r.get("verdict")} for r in out]
    counts = {}
    for i in items:
        counts[i["verdict"]] = counts.get(i["verdict"], 0) + 1
    return {"ok": True, "org": org, "from_date": from_date, "total": len(items), "counts": counts, "items": items}


# ==================== 汇率录入 · P4 自动跑批 + 邮件（V2.160）====================
# 定时＝次月第一个工作日下午 14:00（D15）。实现＝每日 14:00 幂等检查：目标=上一自然月的结账，
# 若金蝶该月未齐、人行次月数据已出、四道闸门全绿→自动写入并提交（不审核）→ 邮件告知；
# 自动比手工严：block 或 hold 任一都不写、发告警邮件（手工模式 hold 可由人点确认放行）。
# 幂等：已齐的月再跑被"不覆盖"跳过；缺次月数据（周末/未到公布日）静默等待、不报警。
# 开关：仅 环境变量 FX_AUTORUN=1 才起定时线程——生产开、本地/开发默认关，杜绝多实例重复自动写。
FX_AUTORUN_ORGS = [o.strip() for o in os.environ.get("FX_AUTORUN_ORGS", "101").split(",") if o.strip()]
FX_AUTORUN_HOUR = int(os.environ.get("FX_AUTORUN_HOUR", "14") or "14")


def _fxrate_org_name(org):
    return next((o["name"] for o in fx.FX_ORGS if o["code"] == org), org)


def _fxrate_autobuild(year, month, org, dry_run=False):
    """无人值守跑「一个月×一个组织」的结账：抓→生成→闸门→(全绿则)写+提交（不审核）。
    status: waiting(缺次月数据,等) / done(已齐,免写) / held(闸门告警,挂起) / written / partial / error / would_write(预演)。"""
    rep = {"year": year, "month": month, "org": org, "org_name": _fxrate_org_name(org),
           "status": "", "msg": "", "results": [], "gates": None}
    try:
        res = fx.generate_rows(year, month, org)
    except fx.FxError as e:
        rep["status"] = "waiting"; rep["msg"] = str(e); return rep       # 次月未公布=还没到点
    except Exception as e:
        rep["status"] = "error"; rep["msg"] = f"抓取/生成出错：{e}"; return rep
    rep["source"] = {"month_end": res.get("month_end_ann") or {},
                     "next_range": res.get("next_range_ann") or {}}   # 人行公告出处（供通知给链接）
    try:
        s, conf = kc.login()
        existing = kc.fetch_bd_rate(use_org=None, beg_from=_fxrate_prev_beg(year, month), s=s, conf=conf)  # 全组织：金蝶汇率不重叠约束跨组织
    except kc.KingdeeError as e:
        rep["status"] = "error"; rep["msg"] = f"连接金蝶失败：{e}"; return rep
    gates = fx.run_gates(res, prev_rates=_fxrate_prev_rates(existing, year, month))
    rep["gates"] = gates["gates"]
    to_write = [r for r in res["rows"] if not fx.find_existing(existing, r, org)]
    if not to_write:
        rep["status"] = "done"; rep["msg"] = "本月汇率金蝶已齐（不覆盖），无需写入"; return rep
    bad = [g for g in gates["gates"] if g["status"] in ("block", "hold")]
    if bad:
        rep["status"] = "held"
        rep["msg"] = "机器闸门告警，已挂起未写：" + "；".join(f"{g['name']}（{g['detail']}）" for g in bad)
        return rep
    if dry_run:
        rep["status"] = "would_write"; rep["msg"] = f"预演：将写入 {len(to_write)} 条"
        rep["results"] = [{"pair": f"{r['from_name']}→{r['to_name']}", "rate": str(r["rate"]),
                           "beg": r["beg_date"], "end": r["end_date"], "status": "would_write",
                           "kind": r["kind"], "basis": r.get("basis", ""),
                           "source_date": r.get("source_date", "")} for r in to_write]
        return rep
    results = _fxrate_write(year, month, org, res, existing, "系统自动", s, conf)
    rep["results"] = results
    n_ok = sum(1 for r in results if r["status"] == "posted")
    n_fail = sum(1 for r in results if r["status"] == "failed")
    rep["status"] = "written" if n_fail == 0 else "partial"
    rep["msg"] = f"写入 {n_ok} 条" + (f"，失败 {n_fail} 条" if n_fail else "")
    db.audit("系统自动", "汇率录入-自动写入金蝶", f"{year}年{month}月·组织{org}", rep["msg"])
    return rep


_FX_MAIL_TAG = {"written": "已录入", "partial": "部分成功", "held": "挂起待处理", "error": "出错",
                "would_write": "预演"}


def _fxrate_src_links(rep):
    """人行中间价公告出处：[(标签, 公布日, url)]，按 url 去重（月末公布日 / 次月首个公布日两份）。"""
    s = rep.get("source") or {}
    out, seen = [], set()
    for key, label in (("month_end", "月末条"), ("next_range", "次月区间条")):
        a = s.get(key) or {}
        u = a.get("url")
        if u and u not in seen:
            seen.add(u)
            out.append((label, a.get("date") or "", u))
    return out


def _fxrate_mail_for(rep):
    tag = _FX_MAIL_TAG.get(rep["status"], rep["status"])
    # 主题带发送日期：避免阿里企业邮把「相同主题」邮件折叠/判重（挂起/出错可能连日发，需各自可见）
    subj = f"【汇率录入·{tag}】{rep['year']}年{rep['month']}月 {rep['org_name']}（{rep['org']}）· {datetime.date.today().isoformat()}"
    _stcn = {"posted": "已写入", "skipped": "跳过", "failed": "失败", "would_write": "将写入"}
    TD = "padding:5px 9px;border:1px solid #e5e7eb"
    TH = "padding:5px 9px;border:1px solid #e5e7eb;background:#f4f7fb;text-align:left"

    def _grp(r):
        return r.get("kind") or ("month_end" if (not r.get("end") or r.get("end") == r.get("beg")) else "next_range")

    def _mrow(r):
        st = _stcn.get(r.get("status"), r.get("status", ""))
        res = st + (f"｜{r['msg']}" if r.get("msg") else "")   # 不显示金蝶内码（审核人用不上）
        return (f"<tr><td style='{TD}'>{r.get('pair','')}</td>"
                f"<td style='{TD};text-align:right'>{r.get('rate','')}</td>"
                f"<td style='{TD};color:#555'>{r.get('basis','')}</td>"
                f"<td style='{TD}'>{res}</td></tr>")

    def _section(title, items):
        if not items:
            return ""
        head = (f"<tr><th style='{TH}'>币对</th><th style='{TH};text-align:right'>汇率</th>"
                f"<th style='{TH}'>算式（怎么算来的）</th><th style='{TH}'>结果</th></tr>")
        return (f"<p style='margin:14px 0 4px'><b>{title}</b></p>"
                f"<table style='border-collapse:collapse;width:100%;font-size:13px'>"
                + head + "".join(_mrow(r) for r in items) + "</table>")

    results = rep.get("results", [])
    me = [r for r in results if _grp(r) == "month_end"]
    nr = [r for r in results if _grp(r) == "next_range"]
    me_html = _section(f"【月末条】生效/失效 {me[0].get('beg', '')}", me) if me else ""
    nr_html = _section(f"【次月区间条】生效 {nr[0].get('beg', '')} 失效 {nr[0].get('end', '')}（供次月记账）", nr) if nr else ""

    gate_html = ""
    if rep["status"] == "held" and rep.get("gates"):
        gate_html = "<p><b>闸门告警：</b></p><ul>" + "".join(
            f"<li>{g['name']}：{g['detail']}</li>" for g in rep["gates"] if g["status"] in ("block", "hold")) + "</ul>"
    links = _fxrate_src_links(rep)
    src_html = ""
    if links:
        src_html = "<p><b>人行中间价公告出处（可点开核对）：</b></p><ul>" + "".join(
            f"<li>{label}（{d} 公布）：<a href='{u}'>{u}</a></li>" for label, d, u in links) + "</ul>"
    body = f"""<div style="font-family:'Microsoft YaHei',sans-serif;font-size:13px;color:#222">
<h3>汇率录入 · {tag}</h3>
<p>{rep['year']} 年 {rep['month']} 月 · {rep['org_name']}（{rep['org']}）：{rep['msg']}</p>
{gate_html}
{me_html}
{nr_html}
{src_html}
<p style="color:#a35a00">提示：本工具只写入并<b>提交</b>，<b>审核（生效）请到金蝶完成</b>；算式与人行出处已同时写入每条汇率在金蝶的「描述」。</p>
</div>"""
    return subj, body


def _fxrate_notify_text(rep):
    """钉钉工作通知用的纯文本摘要（对应邮件 HTML）。"""
    lines = [f"【汇率录入·{_FX_MAIL_TAG.get(rep['status'], rep['status'])}】"
             f"{rep['year']}年{rep['month']}月 · {rep['org_name']}（{rep['org']}）：{rep['msg']}"]
    for g in (rep.get("gates") or []):
        if g["status"] in ("block", "hold"):
            lines.append(f"⚠ {g['name']}：{g['detail']}")
    _stcn = {"posted": "已写入", "skipped": "跳过", "failed": "失败", "would_write": "将写入"}
    results = rep.get("results", [])
    _me = [r for r in results if not r.get("end") or r.get("end") == r.get("beg")]     # 月末条：生效=失效
    _nr = [r for r in results if r.get("end") and r.get("end") != r.get("beg")]        # 次月区间条

    def _line(r):
        st = _stcn.get(r.get("status"), r.get("status", ""))
        tail = "" if st in ("已写入", "") else f"（{st}）"
        return f"· {r.get('pair', '')} {r.get('rate', '')}{tail}"

    if _me:
        lines.append(f"【月末条】生效/失效 {_me[0].get('beg', '')}")
        lines += [_line(r) for r in _me]
    if _nr:
        lines.append(f"【次月区间条】生效 {_nr[0].get('beg', '')} 失效 {_nr[0].get('end', '')}（供次月记账）")
        lines += [_line(r) for r in _nr]
    links = _fxrate_src_links(rep)
    if links:
        lines.append("人行中间价公告出处（可点开核对）：")
        for label, d, u in links:
            lines.append(f"· {label}（{d}公布）{u}")
    if rep["status"] in ("written", "partial"):
        lines.append("请到金蝶完成审核（生效）。算式与人行出处已写入每条汇率的「描述」。")
    return "\n".join(lines)


# ---------------- 通知设置：前端可改收发件人（口令后端校验）；密钥恒留 conf.ini ----------------
def _aslist(v):
    """收件人入参：数组或分隔字符串（;/，/,/换行）→ 去空去重列表。"""
    if v is None:
        return []
    if isinstance(v, str):
        v = v.replace("；", ";").replace("，", ";").replace(",", ";").replace("\n", ";").split(";")
    return list(dict.fromkeys([str(x).strip() for x in v if str(x).strip()]))


def _fxrate_notify_passcode():
    """改收发件人的口令——写在后端 conf.ini [notify] passcode（机密、不下发前端）。未配置=空串。"""
    try:
        import configparser
        c = configparser.ConfigParser()
        c.read(kc.conf_path(), encoding="utf-8")
        return (c.get("notify", "passcode", fallback="") or "").strip()
    except Exception:
        return ""


def _fxrate_effective_confs():
    """通知实际用的收件人+渠道开关：存过 fx_notify_cfg 则以 DB 为准（可清空），否则回退 conf.ini。密钥恒取 conf.ini。"""
    dt = notifier.load_dingtalk_conf()
    sm = mailer.load_smtp_conf()
    ch = {"dingtalk": True, "email": True}
    try:
        cfg = db.get_setting("fx_notify_cfg", None)
    except Exception:
        cfg = None
    if cfg:
        ch["dingtalk"] = bool(cfg.get("dingtalk_on", True))
        ch["email"] = bool(cfg.get("email_on", True))
        if dt:
            dt = {**dt, "mobiles": list(cfg.get("dt_mobiles") or []), "userids": list(cfg.get("dt_userids") or [])}
        if sm:
            sm = {**sm, "to": list(cfg.get("mail_to") or []), "cc": list(cfg.get("mail_cc") or []),
                  "bcc": list(cfg.get("mail_bcc") or [])}
    return dt, sm, ch


def _fxrate_send_report_notify(rep):
    """按一份跑批结果 rep 发通知（钉钉机器人 + 邮件），收件人/开关用 DB 覆盖后的值。"""
    subj, html = _fxrate_mail_for(rep)
    dt, sm, ch = _fxrate_effective_confs()
    return notifier.notify(subj, _fxrate_notify_text(rep), html, dt_conf=dt, smtp_conf=sm, channels=ch)


def _fxrate_autorun_once(dry_run=False):
    """跑一次自动批：对配置组织跑「上一自然月」结账；written/partial/held/error 才发邮件（waiting/done 不打扰）。"""
    today = datetime.date.today()
    ty, tm = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    reports = []
    for org in FX_AUTORUN_ORGS:
        rep = _fxrate_autobuild(ty, tm, org, dry_run=dry_run)
        if (not dry_run) and rep["status"] in ("written", "partial", "held", "error"):
            rep["notify"] = _fxrate_send_report_notify(rep)
        reports.append(rep)
    return reports


def _fxrate_is_local():
    """本地/开发（SQLite）判定——用于生产保护：本地即使开了开关也不真触发，免得误写生产金蝶。"""
    try:
        return str(db.DB_URL).startswith("sqlite")
    except Exception:
        return False


def _fxrate_autorun_enabled():
    """是否启用定时自动跑批。env FX_AUTORUN=1 强制启用(任何库)；否则看页面开关(DB)且仅生产(非本地SQLite)生效。"""
    if os.environ.get("FX_AUTORUN") == "1":
        return True
    if _fxrate_is_local():
        return False                      # 本地测试机：开关只存不生效
    try:
        return bool(db.get_setting("fxrate_autorun", False))
    except Exception:
        return False


def _fxrate_scheduler():
    """每日 FX_AUTORUN_HOUR:00 触发一次自动批。幂等：已建的跳过、缺数静默等待。单次异常不弄垮线程。
    是否真正跑批在【触发时】判定（_fxrate_autorun_enabled），故页面开关即时生效、无需重启。"""
    while True:
        now = datetime.datetime.now()
        nxt = now.replace(hour=FX_AUTORUN_HOUR, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += datetime.timedelta(days=1)
        time.sleep(max(30, (nxt - now).total_seconds()))
        try:
            if _fxrate_autorun_enabled():
                _fxrate_autorun_once()
        except Exception:
            pass


@router.get("/api/fxrate/autorun-config")
def fxrate_autorun_config(request: Request):
    """自动跑批配置/状态：是否启用(FX_AUTORUN)、时点、组织、下次检查时点。供前端「自动」模式显示。"""
    now = datetime.datetime.now()
    nxt = now.replace(hour=FX_AUTORUN_HOUR, minute=0, second=0, microsecond=0)
    if nxt <= now:
        nxt += datetime.timedelta(days=1)
    try:
        setting = bool(db.get_setting("fxrate_autorun", False))
    except Exception:
        setting = False
    return {"ok": True,
            "enabled": _fxrate_autorun_enabled(),           # 本实例是否真会触发
            "setting": setting,                             # 页面开关的存储值
            "forced": os.environ.get("FX_AUTORUN") == "1",  # env 强制
            "local": _fxrate_is_local(),                    # 本地测试机（开关只存不生效）
            "hour": FX_AUTORUN_HOUR, "orgs": FX_AUTORUN_ORGS,
            "next_check": nxt.strftime("%Y-%m-%d %H:%M"),
            "note": "口径：次月第一个工作日下午自动建上月结账。实现＝每日该时点检查，人行次月一公布、闸门全绿即自动写入并提交；缺数则静默等待到次日再看。"}


@router.post("/api/fxrate/autorun-toggle")
def fxrate_autorun_toggle(body: dict, request: Request):
    """页面开关：开/关定时自动跑批（存 DB，触发时判定、即时生效；本地只存不生效）。需 fxrate_post 权限。"""
    u = _require_perm(request, "fxrate_post")
    if not u:
        return JSONResponse({"ok": False, "msg": "无「汇率录入·写入金蝶」权限"}, status_code=403)
    on = bool(body.get("on"))
    db.set_setting("fxrate_autorun", on, u["name"])
    db.audit(u["name"], "汇率录入-定时自动跑批开关", "开启" if on else "关闭")
    return {"ok": True, "setting": on, "enabled": _fxrate_autorun_enabled(), "local": _fxrate_is_local()}


@router.post("/api/fxrate/autorun-now")
def fxrate_autorun_now(body: dict, request: Request):
    """手动触发自动跑批（测试/补跑用）。body: {dry:1 只预演不写, year, month, org 可指定单月}。需 fxrate_post 权限。"""
    u = _require_perm(request, "fxrate_post")
    if not u:
        return JSONResponse({"ok": False, "msg": "无「汇率录入·写入金蝶」权限"}, status_code=403)
    dry = bool(body.get("dry"))
    if body.get("month"):
        org = str(body.get("org") or fx.DEFAULT_ORG)
        rep = _fxrate_autobuild(int(body.get("year") or 2026), int(body["month"]), org, dry_run=dry)
        if (not dry) and rep["status"] in ("written", "partial", "held", "error"):
            rep["notify"] = _fxrate_send_report_notify(rep)
        return {"ok": True, "dry_run": dry, "reports": [rep]}
    return {"ok": True, "dry_run": dry, "reports": _fxrate_autorun_once(dry_run=dry)}


@router.get("/api/fxrate/notify-config")
def fxrate_notify_config(request: Request):
    """通知设置（读）：当前收件人 + 渠道开关 + 密钥是否已配。**密钥值不下发前端**。需 fxrate_post。"""
    if not _require_perm(request, "fxrate_post"):
        return JSONResponse({"ok": False, "msg": "无「汇率录入·写入金蝶」权限"}, status_code=403)
    dt, sm, ch = _fxrate_effective_confs()
    return {"ok": True,
            "dt_mobiles": (dt or {}).get("mobiles", []), "dt_userids": (dt or {}).get("userids", []),
            "mail_to": (sm or {}).get("to", []), "mail_cc": (sm or {}).get("cc", []),
            "mail_bcc": (sm or {}).get("bcc", []),
            "dingtalk_on": ch["dingtalk"], "email_on": ch["email"],
            "dingtalk_configured": notifier.dingtalk_configured(),
            "smtp_configured": mailer.configured(),
            "passcode_set": bool(_fxrate_notify_passcode()),
            "saved": bool(db.get_setting("fx_notify_cfg", None))}


@router.post("/api/fxrate/notify-config")
def fxrate_notify_config_save(body: dict, request: Request):
    """通知设置（存）：改收发件人/渠道开关，**必须带正确口令**（后端 conf.ini [notify] passcode 校验）。需 fxrate_post。"""
    u = _require_perm(request, "fxrate_post")
    if not u:
        return JSONResponse({"ok": False, "msg": "无「汇率录入·写入金蝶」权限"}, status_code=403)
    pc = _fxrate_notify_passcode()
    if not pc:
        return {"ok": False, "msg": "后端未设置通知口令（conf.ini [notify] passcode 为空），暂不能从页面改收发件人，请联系管理员配置。"}
    if str(body.get("passcode") or "").strip() != pc:
        db.audit(u["name"], "汇率录入-通知设置", "口令错误，未保存")
        return JSONResponse({"ok": False, "msg": "口令错误，未保存"}, status_code=403)
    cfg = {"dt_mobiles": _aslist(body.get("dt_mobiles")), "dt_userids": _aslist(body.get("dt_userids")),
           "mail_to": _aslist(body.get("mail_to")), "mail_cc": _aslist(body.get("mail_cc")),
           "mail_bcc": _aslist(body.get("mail_bcc")),
           "dingtalk_on": bool(body.get("dingtalk_on", True)), "email_on": bool(body.get("email_on", True))}
    db.set_setting("fx_notify_cfg", cfg, u["name"])
    db.audit(u["name"], "汇率录入-通知设置", "更新收发件人/开关",
             f"钉钉{len(cfg['dt_mobiles'])+len(cfg['dt_userids'])}人/邮件 收{len(cfg['mail_to'])} 抄{len(cfg['mail_cc'])} 密{len(cfg['mail_bcc'])}")
    return {"ok": True, "msg": "已保存", "cfg": cfg}


@router.post("/api/fxrate/notify-test")
def fxrate_notify_test(body: dict, request: Request):
    """发一条测试通知到【当前设置】的收件人（钉钉+邮件，各按开关）。需 fxrate_post。"""
    u = _require_perm(request, "fxrate_post")
    if not u:
        return JSONResponse({"ok": False, "msg": "无「汇率录入·写入金蝶」权限"}, status_code=403)
    dt, sm, ch = _fxrate_effective_confs()
    subj = "【汇率录入·通道测试】" + datetime.datetime.now().strftime(" %m-%d %H:%M:%S")   # 时间戳→每次唯一，防邮件折叠
    text = f"这是一条通道测试，由 {u['name']} 从「通知设置」页发起。收到即表示通道可用，可忽略。"
    html = f"<div style='font-family:Microsoft YaHei,sans-serif;font-size:13px;color:#222'>{text}</div>"
    res = notifier.notify(subj, text, html, dt_conf=dt, smtp_conf=sm, channels=ch)
    db.audit(u["name"], "汇率录入-通知测试", "发送测试通知")
    return {"ok": True, "result": res}


# 定时线程常驻启动；是否真正跑批由 _fxrate_autorun_enabled() 在【触发时】判定
# （页面开关即时生效 / env FX_AUTORUN=1 强制 / 本地 SQLite 不触发）。跨组织判重使误触发也安全（幂等）。
threading.Thread(target=_fxrate_scheduler, daemon=True, name="fxrate-scheduler").start()
