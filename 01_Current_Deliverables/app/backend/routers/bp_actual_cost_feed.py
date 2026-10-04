# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-04 | Author: Claude Opus 5.5 | Version: V2.795
# Description: 【BP 工作台读产品全成本】内部只读口 GET /api/internal/bp-actual-cost?org=107&period=YYYY-MM
#   BP「STEP6 现金流」原来每月上传成本会计的《产品实际成本表》，现在改成读「产品全成本」算好的本期结果。
#   · 只读、不取金蝶、不重算：只把全成本工作台存在 period_inputs 里的本期状态和结果快照拿出来。
#     本文件**不依赖**全成本工作台的代码（kernels/actual_cost 等），只认它的存储约定：
#       source='actual_cost:<账簿>'；kind='actual_cost:latest' 是本期状态（status / run_id / issues / controls），
#       kind=<run_id> 是那次取数的快照（sources / rules / supplement / result），kind='actual_cost:close' 是结账确认。
#     全成本工作台还没上线、或本期没取过数时，返回 status='not_generated'、products 为空，不报错。
#   · 只有 ready（已确认结账出表）和 needs_confirmation（试算，待确认）两种状态才给产品行；其余状态只给原因。
#     formal=true 的条件：status=ready 且当前有效的结账确认就是这份快照出表时用的那一份（撤销 / 改依据后不算）。
#     这里不去金蝶核实账簿期间（那是页面「导出正式表」时做的），BP 拿到的是「核算认定的状态」。
#   · 每个产品一行 = 车间 × 物料：完工数量 + 各成本要素**总额**（元，不含税）。零产量零成本的成本对象照给，BP 自己滤。
#     委外产品（V2.789 起车间记「委外」，成本 = 直接材料 + 委外加工费 subcontract）照给，带 subcontract 一项；
#     BP 那边对委外产品不用全成本表（按销售成本倒推），收到后单列、不进它的全成本批次。老快照没有这一项，按 0 给。
#   鉴权同 /api/internal/bp-identity、bp-logistics-lines：X-Internal-Token + 回环来源（bom_quote.internal_token_ok），
#   登录门由 app.py 的 BP_INTERNAL_PREFIX 放行。
import hashlib
import json
import re

from fastapi import APIRouter, Request

from core import JSONResponse, db
from routers import bom_quote

router = APIRouter()
LATEST, CLOSE = "actual_cost:latest", "actual_cost:close"       # 与 actual_cost_service 同名常量
USABLE = ("ready", "needs_confirmation")
_TEXT = ("cc", "code", "name", "spec", "group")
_NUM = ("qty", "material", "packaging", "labor", "indirect", "water", "power", "gas",
        "depreciation", "rent", "other", "subcontract", "wip", "total")


def build_feed(org, period, latest, snapshot, close):
    """全成本工作台的本期状态 + 结果快照 → 给 BP 的结构。纯函数（不碰库）。"""
    latest = latest or {}
    status = latest.get("status") or "not_generated"
    out = {"ok": True, "org": org, "period": period, "status": status, "formal": False,
           "runId": latest.get("run_id") or "", "checkedAt": latest.get("checked_at") or "",
           "issues": [str(x) for x in (latest.get("issues") or []) if x],
           "controls": {}, "unallocatedWip": [], "products": [],
           "sourceTime": "", "ruleVersion": "", "confirmedBy": "", "confirmedAt": ""}
    if status in USABLE and snapshot:
        src, res, rules = snapshot.get("sources") or {}, snapshot.get("result") or {}, snapshot.get("rules") or {}
        if src.get("org") != org or src.get("period") != period:
            out.update(status="failed", issues=["本期快照的账簿或期间与请求不符，请到全成本工作台重新取数"])
        else:
            for p in res.get("products") or []:
                row = {k: (str(p.get(k) or "").strip()) for k in _TEXT}
                for k in _NUM:
                    v = p.get(k)
                    row[k] = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0
                out["products"].append(row)
            out["controls"] = res.get("controls") or latest.get("controls") or {}
            out["unallocatedWip"] = res.get("unallocated_wip") or []
            out["sourceTime"] = src.get("fetched_at") or ""
            out["ruleVersion"] = res.get("rule_version") or rules.get("version") or ""
            conf = rules.get("close_confirmation") or {}
            if status == "ready" and conf and close == conf and conf.get("confirmed_by"):
                out.update(formal=True, confirmedBy=conf.get("confirmed_by") or "",
                           confirmedAt=conf.get("confirmed_at") or "")
    # 指纹：BP 用它判断「同步之后核算这边又出了新结果 / 从试算变成了正式」
    out["stamp"] = hashlib.sha256(json.dumps(
        [out["status"], out["runId"], out["formal"], out["confirmedAt"], len(out["products"])],
        ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    return out


@router.get("/api/internal/bp-actual-cost")
def bp_actual_cost(request: Request, org: str = "", period: str = ""):
    if not bom_quote.internal_token_ok(request):
        return JSONResponse({"ok": False, "msg": "未授权：需 X-Internal-Token（回环调用）"}, status_code=401)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,8}", org or "") or not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", period or ""):
        return JSONResponse({"ok": False, "msg": "参数无效：org=账簿代码，period=YYYY-MM"}, status_code=400)
    year, mon, ns = int(period[:4]), int(period[5:7]), "actual_cost:" + org
    latest = (db.get_period_input(ns, year, mon, LATEST) or {}).get("payload") or {}
    snapshot = None
    if latest.get("status") in USABLE and latest.get("run_id"):
        snapshot = (db.get_period_input(ns, year, mon, latest["run_id"]) or {}).get("payload")
    close = (db.get_period_input(ns, year, mon, CLOSE) or {}).get("payload") or {}
    return build_feed(org, period, latest, snapshot, close)
