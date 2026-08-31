# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-08-17 | Author: Claude / c | Version: V2.301（V2.302 加网关管理代理）
# Description: 【平台通用】门户「模型配置」的后端。
#              P0.5（V2.301）：①聚合各工作台 GET /api/llm/health（模型接入状态，key 打码）；
#              ②服务端代理 BP 的 GET /api/llm/usage（用量/额度/豁免名单，管理员可见）。
#              P1（V2.302）：代理 AI 网关（gateway.py，独立进程 8020）的管理面——
#              凭证 列表/签发/吊销/轮换 + 集中用量。GW_ADMIN_TOKEN 只存核算服务器 .env/环境，
#              浏览器永远拿不到它：前端带核算登录态调本代理，本代理换令牌调网关。
#              网关未起/未配令牌 → gateway.available=false，前端保持 P0.5 只读形态（静默降级）。
#
#              ⚠ 安全边界：本文件不出现任何厂商 key。health 返回的 keyHint 本就是打码后的；
#              网关凭证列表接口只回打码 token，完整凭证只在 签发/轮换 响应里出现一次。
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from fastapi import APIRouter, Request

from core import JSONResponse, _current_user, db

router = APIRouter()

# 工作台注册表（P0.5 写死在这——就 3 行，值得上配置文件的那天就是该上 P1 网关的那天）。
# base 为空 = 该工作台还没有 /api/llm 模块（未接入大模型）。
# 核算侧接入路径：拷 BP 的 app/llm*.py 获得同款接口后，把 base 填成自己（设计说明 §5-bis）。
WORKBENCHES = [
    {"key": "bp", "name": "财务BP工作台", "vendor": "DeepSeek · BP账号",
     "base": db.BP_API_BASE, "home": "/bp/"},
    {"key": "hesuan", "name": "财务核算工作台", "vendor": "DeepSeek · 核算账号（待开）",
     "base": "", "home": "/"},
    {"key": "worker", "name": "数字员工办公室", "vendor": "—",
     "base": "", "home": ""},
]

_TIMEOUT = 2.5          # 单台探测超时（秒）：门户首页胶囊也走这条，不能拖慢首屏
_CACHE_TTL = 60         # 状态缓存（秒）：胶囊每次进门户都请求，别把 health 打成心跳
_cache: dict = {"ts": 0.0, "data": None}
_lock = threading.Lock()

# ── AI 网关（V2.302）：独立进程 8020，管理令牌只在服务端 ──
GW_BASE = os.getenv("GW_BASE", "http://127.0.0.1:8020").rstrip("/")


def _gw_admin_token():
    """每次现读（而非 import 时定死）：管理员在 .env 补了令牌后重启网关即可，核算后端不用跟着重启。"""
    tok = os.getenv("GW_ADMIN_TOKEN", "").strip()
    if not tok:
        # 与网关同目录共用一个 .env：核算后端与网关在同一台机器（设计说明 §7.2 独立进程、同机部署）
        import pathlib
        p = pathlib.Path(__file__).resolve().parent.parent / ".env"
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("GW_ADMIN_TOKEN="):
                    tok = line.split("=", 1)[1].strip()
                    break
    return tok


def _gw_call(method, path, body=None, timeout=6):
    """调网关管理面。抛 RuntimeError(人话) —— 调用方转 502/降级。"""
    tok = _gw_admin_token()
    if not tok:
        raise RuntimeError("网关管理令牌未配置（backend/.env 的 GW_ADMIN_TOKEN）")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(GW_BASE + path, data=data, method=method,
                                 headers={"Authorization": f"Bearer {tok}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8")).get("detail", "")
        except Exception:
            detail = ""
        raise RuntimeError(f"网关返回 {e.code}：{detail}")
    except Exception as e:
        raise RuntimeError(f"网关不可达（{type(e).__name__}）——确认 8020 进程已起（start_gateway.bat）")


def _fetch_json(url, headers=None, timeout=_TIMEOUT):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _probe(wb):
    """探测单个工作台的 /api/llm/health。不可达≠报错——如实报 reachable=False。"""
    out = {"key": wb["key"], "name": wb["name"], "vendor": wb["vendor"], "home": wb["home"],
           "integrated": bool(wb["base"]), "reachable": False, "configured": False,
           "provider": "", "model": "", "keyHint": "", "providers": [], "latencyMs": None,
           "error": ""}
    if not wb["base"]:
        return out
    t0 = time.time()
    try:
        h = _fetch_json(wb["base"] + "/api/llm/health")
    except Exception as e:
        out["error"] = type(e).__name__       # 不透传异常详情（可能含内网 URL）
        return out
    base_url = str(h.get("baseUrl") or "")
    out.update({
        "reachable": True,
        "configured": bool(h.get("configured")),
        "provider": h.get("provider", ""),
        "model": h.get("model", ""),
        "keyHint": h.get("keyHint", ""),      # BP 侧已打码，原样透传
        "providers": h.get("providers", []),  # 多供应商清单（含各自 configured/keyHint）
        "latencyMs": round((time.time() - t0) * 1000),
        # V2.305：从工作台自报的 baseUrl 识别真实接线——指向网关=经网关，否则=直连厂商。
        # 这是事实态不是配置态：工作台切 .env 的瞬间，这里跟着变。
        "viaGateway": base_url.startswith(GW_BASE),
    })
    return out


def _gw_probe():
    """网关在线状态（公开 health，无敏感信息）。不在线不报错——P1 未部署时页面保持 P0.5 形态。"""
    try:
        h = _fetch_json(GW_BASE + "/health", timeout=_TIMEOUT)
        return {"available": True, "base": GW_BASE, "providers": h.get("providers", []),
                "activeCredentials": h.get("activeCredentials", 0),
                "adminEnabled": bool(h.get("adminEnabled")),
                "siteDailyTokens": h.get("siteDailyTokens", 0)}
    except Exception:
        return {"available": False, "base": GW_BASE}


def _status(fresh=False):
    now = time.time()
    with _lock:
        if not fresh and _cache["data"] and now - _cache["ts"] < _CACHE_TTL:
            return _cache["data"]
    rows = [_probe(wb) for wb in WORKBENCHES]
    data = {
        "ok": True,
        "workbenches": rows,
        # 门户首页胶囊的口径：任意一台 已接入+可达+配了 key ⇒ AI 就绪
        "aiReady": any(r["configured"] and r["reachable"] for r in rows),
        "readyCount": sum(1 for r in rows if r["configured"] and r["reachable"]),
        "integratedCount": sum(1 for r in rows if r["integrated"]),
        "gateway": _gw_probe(),      # V2.302：网关在线 → 模型配置页点亮凭证管理
        "ts": now,
    }
    with _lock:
        _cache.update(ts=now, data=data)
    return data


@router.get("/api/llm-hub/status")
def llm_hub_status(request: Request, fresh: int = 0):
    """模型接入状态聚合（登录即可看——只有打码 keyHint 与模型名，无敏感数据）。
    门户首页「AI 模型」胶囊与模型配置页①②区共用。fresh=1 绕过缓存（自检按钮用）。"""
    return _status(fresh=bool(fresh))


@router.get("/api/llm-hub/usage")
def llm_hub_usage(request: Request, days: int = 7):
    """用量/额度/豁免聚合（V2.324 起：需平台级权限点 model_config——主管理员恒有，其他人由账号管理显式授予）。
    P0.5 只有 BP 一台有数——服务端代理 BP 的 /api/llm/usage。
    鉴权语义：核算本来就是给 BP 签发 X-BP-User/X-BP-Perms 的权威（api_bp_authz），
    这里服务端注入同款头不是绕权限，而是行使同一职权。"""
    u = _current_user(request)
    if not u or not db.user_can(u, "model_config"):
        return JSONResponse({"ok": False, "msg": "用量看板需「模型配置」权限"}, status_code=403)
    days = max(1, min(180, int(days or 7)))
    out = []
    for wb in WORKBENCHES:
        if not wb["base"]:
            continue
        try:
            usage = _fetch_json(
                f"{wb['base']}/api/llm/usage?days={days}",
                headers={
                    "X-BP-User": urllib.parse.quote(str(u["name"]), safe=""),
                    "X-BP-Perms": "bp:board:appSettings",
                })
            out.append({"key": wb["key"], "name": wb["name"], "ok": True, "usage": usage})
        except Exception as e:
            out.append({"key": wb["key"], "name": wb["name"], "ok": False,
                        "error": type(e).__name__})
    return {"ok": True, "days": days, "workbenches": out}


@router.post("/api/llm-hub/key")
def set_workbench_key(body: dict, request: Request):
    """更换/写入某工作台的厂商 key（V2.303，模型配置页「更换密钥」一次性写入框）。
    只做转发不做存储：key 直达该工作台的 POST /api/llm/key（写它自己的 .env，立即生效）。
    纪律：key 不落核算任何日志/audit——audit 只记"谁给哪台换了哪家的钥"，不记值。"""
    u = _current_user(request)
    if not u or not db.user_can(u, "model_config"):
        return JSONResponse({"ok": False, "msg": "需「模型配置」权限"}, status_code=403)
    wb_key = str(body.get("workbench", ""))
    provider = str(body.get("provider", ""))
    api_key = str(body.get("apiKey", ""))
    wb = next((w for w in WORKBENCHES if w["key"] == wb_key and w["base"]), None)
    if not wb:
        return JSONResponse({"ok": False, "msg": "该工作台未接入，无法写入"}, status_code=422)
    data = json.dumps({"provider": provider, "apiKey": api_key}).encode("utf-8")
    req = urllib.request.Request(
        wb["base"] + "/api/llm/key", data=data, method="POST",
        headers={"Content-Type": "application/json",
                 "X-BP-User": urllib.parse.quote(str(u["name"]), safe=""),
                 "X-BP-Perms": "bp:board:appSettings"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            out = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8")).get("detail", "")
        except Exception:
            detail = ""
        return JSONResponse({"ok": False, "msg": f"{wb['name']}拒绝：{detail or e.code}"},
                            status_code=502)
    except Exception as e:
        return JSONResponse({"ok": False, "msg": f"{wb['name']}不可达（{type(e).__name__}）"},
                            status_code=502)
    with _lock:
        _cache.update(ts=0.0)        # 失效状态缓存：下一次 status 立刻反映新 keyHint
    db.audit(u["name"], "更换模型密钥", wb["name"], f"provider={provider}")
    return out


@router.post("/api/llm-hub/provider")
def add_workbench_provider(body: dict, request: Request):
    """添加模型接入（V2.306）：选工作台 → 厂商（预置或自定义）→ 密钥/模型。
    转发到工作台 POST /api/llm/provider（写它的 .env 热生效）；key 不落核算任何日志。"""
    u = _current_user(request)
    if not u or not db.user_can(u, "model_config"):
        return JSONResponse({"ok": False, "msg": "需「模型配置」权限"}, status_code=403)
    wb = next((w for w in WORKBENCHES if w["key"] == str(body.get("workbench", "")) and w["base"]), None)
    if not wb:
        return JSONResponse({"ok": False, "msg": "该工作台未接入 llm 模块——先拷 BP 的 app/llm*.py"},
                            status_code=422)
    payload = {k: body.get(k) for k in ("provider", "baseUrl", "apiKey", "model")}
    req = urllib.request.Request(
        wb["base"] + "/api/llm/provider", data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json",
                 "X-BP-User": urllib.parse.quote(str(u["name"]), safe=""),
                 "X-BP-Perms": "bp:board:appSettings"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            out = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8")).get("detail", "")
        except Exception:
            detail = ""
        return JSONResponse({"ok": False, "msg": f"{wb['name']}拒绝：{detail or e.code}"}, status_code=502)
    except Exception as e:
        return JSONResponse({"ok": False, "msg": f"{wb['name']}不可达（{type(e).__name__}）"}, status_code=502)
    with _lock:
        _cache.update(ts=0.0)
    db.audit(u["name"], "添加模型接入", wb["name"], f"provider={payload.get('provider')}")
    return out


@router.post("/api/llm-hub/model")
def set_workbench_model(body: dict, request: Request):
    """切某工作台某供应商的默认模型（V2.304，模型配置页卡片直接改）。转发到工作台
    POST /api/llm/model（写它的 .env + 热生效）。"""
    u = _current_user(request)
    if not u or not db.user_can(u, "model_config"):
        return JSONResponse({"ok": False, "msg": "需「模型配置」权限"}, status_code=403)
    wb = next((w for w in WORKBENCHES if w["key"] == str(body.get("workbench", "")) and w["base"]), None)
    if not wb:
        return JSONResponse({"ok": False, "msg": "该工作台未接入"}, status_code=422)
    payload = {"provider": body.get("provider"), "model": body.get("model")}
    req = urllib.request.Request(
        wb["base"] + "/api/llm/model", data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json",
                 "X-BP-User": urllib.parse.quote(str(u["name"]), safe=""),
                 "X-BP-Perms": "bp:board:appSettings"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            out = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8")).get("detail", "")
        except Exception:
            detail = ""
        return JSONResponse({"ok": False, "msg": f"{wb['name']}拒绝：{detail or e.code}"}, status_code=502)
    except Exception as e:
        return JSONResponse({"ok": False, "msg": f"{wb['name']}不可达（{type(e).__name__}）"}, status_code=502)
    with _lock:
        _cache.update(ts=0.0)
    db.audit(u["name"], "切换默认模型", wb["name"], f"{payload['provider']}→{payload['model']}")
    return out


@router.post("/api/llm-hub/policy")
def set_workbench_policy(body: dict, request: Request):
    """人员策略集中改（V2.303，②-b 的 P1）：转发到工作台的 POST /api/llm/policy（写它的 .env+热载）。
    全站硬闸不在此列（工作台侧有意不开放）。"""
    u = _current_user(request)
    if not u or not db.user_can(u, "model_config"):
        return JSONResponse({"ok": False, "msg": "需「模型配置」权限"}, status_code=403)
    wb = next((w for w in WORKBENCHES if w["key"] == str(body.get("workbench", "")) and w["base"]), None)
    if not wb:
        return JSONResponse({"ok": False, "msg": "该工作台未接入"}, status_code=422)
    payload = {k: body.get(k) for k in ("dailyCalls", "dailyTokens", "exempt") if k in body}
    req = urllib.request.Request(
        wb["base"] + "/api/llm/policy", data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json",
                 "X-BP-User": urllib.parse.quote(str(u["name"]), safe=""),
                 "X-BP-Perms": "bp:board:appSettings"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            out = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8")).get("detail", "")
        except Exception:
            detail = ""
        return JSONResponse({"ok": False, "msg": f"{wb['name']}拒绝：{detail or e.code}"}, status_code=502)
    except Exception as e:
        return JSONResponse({"ok": False, "msg": f"{wb['name']}不可达（{type(e).__name__}）"}, status_code=502)
    db.audit(u["name"], "改AI人员策略", wb["name"],
             f"calls={body.get('dailyCalls')} tokens={body.get('dailyTokens')} exempt={len(body.get('exempt') or [])}人")
    return out


@router.get("/api/llm-hub/audit")
def llm_hub_audit(request: Request, days: int = 7, limit: int = 100, onlyErrors: int = 0):
    """调用审计聚合（V2.303，④ 的 P1）：拉各已接入工作台的 /api/llm/ask-audit 合并按时间倒序。
    覆盖问数/AI 分析/通用对话全部调用（BP V2.229 起三类都落审计账）。管理员可见。"""
    u = _current_user(request)
    if not u or not db.user_can(u, "model_config"):
        return JSONResponse({"ok": False, "msg": "需「模型配置」权限"}, status_code=403)
    days, limit = max(1, min(180, days)), max(1, min(200, limit))
    rows, errs = [], []
    for wb in WORKBENCHES:
        if not wb["base"]:
            continue
        req = urllib.request.Request(
            f"{wb['base']}/api/llm/ask-audit?days={days}&limit={limit}"
            + ("&onlyErrors=true" if onlyErrors else ""),
            headers={"X-BP-User": urllib.parse.quote(str(u["name"]), safe=""),
                     "X-BP-Perms": "bp:board:appSettings"})
        try:
            with urllib.request.urlopen(req, timeout=6) as r:
                d = json.loads(r.read().decode("utf-8"))
            for row in d.get("rows", []):
                # 答案截 300 字：审计主角是"谁问了什么+查询计划"，全文回 payload 太肥
                if isinstance(row.get("answer"), str):
                    row["answer"] = row["answer"][:300]
                rows.append({**row, "workbench": wb["name"]})
        except Exception as e:
            errs.append({"workbench": wb["name"], "error": type(e).__name__})
    rows.sort(key=lambda r: r.get("ts") or 0, reverse=True)
    return {"ok": True, "days": days, "rows": rows[:limit], "unreachable": errs}


# ── AI 网关管理代理（V2.302；V2.324 起改门 model_config 权限点）────────────────────
def _require_admin(request: Request):
    u = _current_user(request)
    if not u or not db.user_can(u, "model_config"):
        raise_403 = JSONResponse({"ok": False, "msg": "需「模型配置」权限"}, status_code=403)
        return None, raise_403
    return u, None


@router.get("/api/llm-hub/gateway/credentials")
def gw_credentials(request: Request):
    u, err = _require_admin(request)
    if err:
        return err
    try:
        return _gw_call("GET", "/admin/credentials")
    except RuntimeError as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=502)


@router.post("/api/llm-hub/gateway/credentials")
def gw_credential_create(body: dict, request: Request):
    """签发凭证。响应含完整 token（仅此一次）——审计落核算 audit 表，但**不落 token 本身**。"""
    u, err = _require_admin(request)
    if err:
        return err
    try:
        r = _gw_call("POST", "/admin/credentials", body=body)
    except RuntimeError as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=502)
    db.audit(u["name"], "签发AI网关凭证", str(body.get("workbench", "")),
             f"provider={body.get('provider')}")
    return r


@router.post("/api/llm-hub/gateway/credentials/{cid}/revoke")
def gw_credential_revoke(cid: int, request: Request):
    u, err = _require_admin(request)
    if err:
        return err
    try:
        r = _gw_call("POST", f"/admin/credentials/{cid}/revoke")
    except RuntimeError as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=502)
    db.audit(u["name"], "吊销AI网关凭证", str(cid), "")
    return r


@router.post("/api/llm-hub/gateway/credentials/{cid}/rotate")
def gw_credential_rotate(cid: int, request: Request):
    u, err = _require_admin(request)
    if err:
        return err
    try:
        r = _gw_call("POST", f"/admin/credentials/{cid}/rotate")
    except RuntimeError as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=502)
    db.audit(u["name"], "轮换AI网关凭证", str(cid), "")
    return r


@router.get("/api/llm-hub/gateway/usage")
def gw_usage(request: Request, days: int = 7):
    u, err = _require_admin(request)
    if err:
        return err
    try:
        return _gw_call("GET", f"/admin/usage?days={max(1, min(180, days))}")
    except RuntimeError as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=502)
