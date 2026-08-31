# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-08-17 | Author: Claude / c | Version: V2.302
# Description: 【平台通用】AI 网关 P1 MVP —— 独立进程独立端口（8020，勿与核算 8000 同进程：GIL 教训），
#              OpenAI 兼容透明转发 + 内部凭证 + 记账 + 额度。设计契约见
#              03_Source_Materials/模型配置中台_设计说明_20260816.md §3：
#
#                POST /ai/v1/chat/completions
#                Authorization: Bearer <内部凭证 tok_xxx>
#                X-AI-User: <URL编码用户名>        ← 人级记账（透传自各工作台）
#                body: 标准 OpenAI chat 格式（model 可省略→凭证默认；指定须在允许集内）
#
#              工作台切换成本 = 两行 .env（BP 实测：BP_LLM_BASE_URL=http://<网关>:8020/ai +
#              BP_LLM_API_KEY=tok_bp_xxx，BP llm.py 拼 {base}/v1/chat/completions 正好命中）。
#
#              选型注记：设计说明 §5-bis 首选开源网关（OneAPI/LiteLLM）。本 MVP 自建的理由：
#              ①转发/重试/记账/配额这四样 BP llm 模块已在生产验证过，边际自建量只有凭证表；
#              ②不引入 Go 二进制/Postgres 新运维面；③工作台侧切换是配置级——将来规模触发
#              信号（工作台≥4/月费用显著/吊销治理事件）要换 OneAPI，各台只改 BASE_URL，零代码。
#
#              安全边界（设计说明 §6，逐条落实）：
#              1. 厂商 key 只读本目录 .env（GW_*），任何日志/接口/报错一律打码；
#              2. 凭证可即时吊销（active=0 下一次调用即 401）；
#              3. 全站日 token 硬闸 GW_SITE_DAILY_TOKENS 对任何凭证生效；
#              4. 管理 API 须 Bearer GW_ADMIN_TOKEN（未配置则管理面整体关闭，转发面不受影响）；
#              5. gateway.db（凭证+流水）不进部署包白名单，与 workbench.db 同待遇。
#
#              运行：py -m uvicorn gateway:app --port 8020（项目根 start_gateway.bat）
from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime

import requests
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "gateway.db")

# ── .env 自装载（不依赖 python-dotenv；只认 KEY=VALUE 行，已存在的环境变量不覆盖）──
def _load_env():
    p = os.path.join(HERE, ".env")
    if not os.path.exists(p):
        return
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

_load_env()

# ── 供应商预置（对齐 BP llm.py PRESETS 口径；key 读 GW_{NAME}_API_KEY）──
PRESETS = {
    "deepseek": {"base": "https://api.deepseek.com", "model": "deepseek-chat"},
    "kimi": {"base": "https://api.moonshot.cn", "model": "kimi-k3"},   # V2.306 K3
    "glm": {"base": "https://open.bigmodel.cn/api/paas", "model": "glm-4-plus"},
    "dashscope": {"base": "https://dashscope.aliyuncs.com/compatible-mode", "model": "qwen-plus"},
}
TIMEOUT = float(os.getenv("GW_TIMEOUT", "90"))
SITE_DAILY_TOKENS = int(os.getenv("GW_SITE_DAILY_TOKENS", "0"))   # 0=不限
ADMIN_TOKEN = os.getenv("GW_ADMIN_TOKEN", "").strip()


def _mask(s):
    if not s:
        return ""
    return s[:4] + "***" + s[-4:] if len(s) > 12 else "***"


def vendor_conf(provider):
    p = (provider or "").strip().lower()
    preset = PRESETS.get(p)
    if not preset:
        return None
    key = os.getenv(f"GW_{p.upper()}_API_KEY", "").strip()
    base = os.getenv(f"GW_{p.upper()}_BASE_URL", "").strip() or preset["base"]
    return {"provider": p, "base": base.rstrip("/"), "key": key,
            "default_model": os.getenv(f"GW_{p.upper()}_MODEL", "").strip() or preset["model"]}


# ── 存储（WAL：转发面与管理面并发读写不互卡）──
_DDL = """
CREATE TABLE IF NOT EXISTS credentials(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  token TEXT UNIQUE NOT NULL,
  workbench TEXT NOT NULL,
  provider TEXT NOT NULL,
  models TEXT NOT NULL DEFAULT '[]',
  daily_calls INTEGER NOT NULL DEFAULT 0,
  daily_tokens INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  created_at REAL, revoked_at REAL
);
CREATE TABLE IF NOT EXISTS gw_usage(
  ts REAL, day TEXT, workbench TEXT, user TEXT,
  provider TEXT, model TEXT,
  prompt_tokens INTEGER, completion_tokens INTEGER, total_tokens INTEGER,
  ms INTEGER, ok INTEGER, err TEXT
);
CREATE INDEX IF NOT EXISTS idx_gwu_day ON gw_usage(day);
"""


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_DDL)     # 每次连接自愈建表（BP llm_quota 同款教训：只在启动建，TestClient/换库就 500）
    return conn


def _today():
    return datetime.now().strftime("%Y-%m-%d")


app = FastAPI(title="AI Gateway", version="P1-MVP")


# ── 转发面 ────────────────────────────────────────────────
def _auth_credential(authorization):
    tok = (authorization or "").removeprefix("Bearer ").strip()
    if not tok:
        raise HTTPException(401, "缺内部凭证（Authorization: Bearer tok_xxx）")
    conn = _connect()
    try:
        r = conn.execute("SELECT id,token,workbench,provider,models,daily_calls,daily_tokens,active"
                         " FROM credentials WHERE token=?", (tok,)).fetchone()
    finally:
        conn.close()
    if not r:
        raise HTTPException(401, "凭证不存在")
    if not r[7]:
        raise HTTPException(401, "凭证已吊销")
    return {"id": r[0], "token": r[1], "workbench": r[2], "provider": r[3],
            "models": json.loads(r[4] or "[]"), "daily_calls": r[5], "daily_tokens": r[6]}


def _quota_check(cred):
    """每凭证日额度 + 全站日 token 硬闸（对任何凭证生效，含额度=0 的"不限"凭证）。"""
    conn = _connect()
    try:
        day = _today()
        calls, tokens = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(total_tokens),0) FROM gw_usage WHERE day=? AND workbench=? AND ok=1",
            (day, cred["workbench"])).fetchone()
        site_tokens = conn.execute(
            "SELECT COALESCE(SUM(total_tokens),0) FROM gw_usage WHERE day=? AND ok=1", (day,)).fetchone()[0]
    finally:
        conn.close()
    if SITE_DAILY_TOKENS and site_tokens >= SITE_DAILY_TOKENS:
        raise HTTPException(429, f"全站今日 token 硬闸已到（{SITE_DAILY_TOKENS}），明日重置")
    if cred["daily_calls"] and calls >= cred["daily_calls"]:
        raise HTTPException(429, f"凭证今日调用额度已用完（{cred['daily_calls']} 次），明日重置")
    if cred["daily_tokens"] and tokens >= cred["daily_tokens"]:
        raise HTTPException(429, f"凭证今日 token 额度已用完（{cred['daily_tokens']}），明日重置")


def _record(cred, user, model, usage, ms, ok, err=""):
    try:
        conn = _connect()
        try:
            u = usage or {}
            conn.execute(
                "INSERT INTO gw_usage VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (time.time(), _today(), cred["workbench"], user or "", cred["provider"], model,
                 u.get("prompt_tokens", 0), u.get("completion_tokens", 0), u.get("total_tokens", 0),
                 ms, 1 if ok else 0, str(err)[:300]))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass   # 记账失败不拦调用（与 BP record 同则）


@app.post("/ai/v1/chat/completions")
@app.post("/v1/chat/completions")
async def chat_completions(request: Request,
                           authorization: str | None = Header(None),
                           x_ai_user: str | None = Header(None)):
    cred = _auth_credential(authorization)
    vc = vendor_conf(cred["provider"])
    if not vc:
        raise HTTPException(502, f"凭证绑定的供应商未预置：{cred['provider']}")
    if not vc["key"]:
        raise HTTPException(503, f"网关未配置 {cred['provider']} 的 key（GW_{cred['provider'].upper()}_API_KEY）")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "body 须为 JSON（OpenAI chat 格式）")
    if body.get("stream"):
        raise HTTPException(400, "网关 MVP 暂不支持 stream=true（BP/核算现有调用均为非流式）")
    # 人级记账：X-AI-User 头优先；没有则认 body 里的用户标识——OpenAI 标准叫 user，
    # DeepSeek 文档叫 user_id（BP llm.py 对 deepseek 塞的正是 user_id）。两个都认，
    # BP 两行 .env 切过来记账口径零改造。
    x_ai_user = x_ai_user or str(body.get("user") or body.get("user_id") or "")

    # model 治理：缺省→凭证允许集第一个→供应商默认；指定→须在允许集内（允许集为空=只放默认模型）
    allowed = cred["models"] or [vc["default_model"]]
    model = (body.get("model") or "").strip() or allowed[0]
    if model not in allowed:
        raise HTTPException(403, f"模型 {model} 不在该凭证允许集内：{allowed}")
    body["model"] = model

    _quota_check(cred)
    t0 = time.time()
    try:
        r = requests.post(
            f"{vc['base']}/v1/chat/completions",
            headers={"Authorization": f"Bearer {vc['key']}", "Content-Type": "application/json"},
            json=body, timeout=TIMEOUT)
    except requests.RequestException as e:
        _record(cred, x_ai_user, model, None, round((time.time() - t0) * 1000), False, type(e).__name__)
        raise HTTPException(502, f"供应商网络失败：{type(e).__name__}")   # 不带 URL/key
    ms = round((time.time() - t0) * 1000)
    if r.status_code != 200:
        # 供应商报错原样转发状态码；正文剥到只剩 message（防 key 回显——DeepSeek 不回显，防御性照做）
        try:
            msg = (r.json().get("error") or {}).get("message", "")[:300]
        except Exception:
            msg = r.text[:200]
        _record(cred, x_ai_user, model, None, ms, False, f"HTTP{r.status_code}")
        raise HTTPException(r.status_code if r.status_code in (400, 401, 402, 422, 429) else 502,
                            f"供应商返回 {r.status_code}：{msg}")
    data = r.json()
    _record(cred, x_ai_user, model, data.get("usage"), ms, True)
    return JSONResponse(data)


@app.get("/health")
def health():
    """自检（无敏感信息：key 只给打码提示）。核算 llm-hub 探测这里。"""
    provs = []
    for name in PRESETS:
        vc = vendor_conf(name)
        provs.append({"name": name, "configured": bool(vc["key"]), "keyHint": _mask(vc["key"]),
                      "model": vc["default_model"]})
    conn = _connect()
    try:
        n_active = conn.execute("SELECT COUNT(*) FROM credentials WHERE active=1").fetchone()[0]
    finally:
        conn.close()
    return {"ok": True, "service": "ai-gateway", "providers": provs,
            "activeCredentials": n_active, "adminEnabled": bool(ADMIN_TOKEN),
            "siteDailyTokens": SITE_DAILY_TOKENS}


# ── 管理面（Bearer GW_ADMIN_TOKEN；未配置=整体关闭）────────────
def _admin(authorization):
    if not ADMIN_TOKEN:
        raise HTTPException(403, "管理面未启用（网关 .env 未配 GW_ADMIN_TOKEN）")
    if (authorization or "").removeprefix("Bearer ").strip() != ADMIN_TOKEN:
        raise HTTPException(403, "管理令牌不对")


def _cred_row(r, reveal=False):
    return {"id": r[0], "token": r[1] if reveal else _mask(r[1]), "workbench": r[2],
            "provider": r[3], "models": json.loads(r[4] or "[]"),
            "dailyCalls": r[5], "dailyTokens": r[6], "active": bool(r[7]),
            "createdAt": r[8], "revokedAt": r[9]}


_SEL = "SELECT id,token,workbench,provider,models,daily_calls,daily_tokens,active,created_at,revoked_at FROM credentials"


@app.get("/admin/credentials")
def admin_credentials(authorization: str | None = Header(None)):
    _admin(authorization)
    conn = _connect()
    try:
        rows = conn.execute(_SEL + " ORDER BY id").fetchall()
        day = _today()
        used = {r[0]: {"calls": r[1], "tokens": r[2]} for r in conn.execute(
            "SELECT workbench, COUNT(*), COALESCE(SUM(total_tokens),0) FROM gw_usage"
            " WHERE day=? AND ok=1 GROUP BY workbench", (day,))}
    finally:
        conn.close()
    out = []
    for r in rows:
        c = _cred_row(r)
        c["todayUsed"] = used.get(c["workbench"], {"calls": 0, "tokens": 0})
        out.append(c)
    return {"ok": True, "credentials": out}


@app.post("/admin/credentials")
async def admin_create(request: Request, authorization: str | None = Header(None)):
    _admin(authorization)
    b = await request.json()
    wb = re.sub(r"[^a-z0-9_]", "", str(b.get("workbench", "")).lower())
    provider = str(b.get("provider", "deepseek")).lower()
    if not wb:
        raise HTTPException(422, "workbench 必填（小写字母/数字/下划线，如 bp / hesuan）")
    if provider not in PRESETS:
        raise HTTPException(422, f"provider 须为 {list(PRESETS)} 之一")
    models = [str(m).strip() for m in (b.get("models") or []) if str(m).strip()]
    tok = f"tok_{wb}_{secrets.token_hex(12)}"
    conn = _connect()
    try:
        conn.execute("INSERT INTO credentials(token,workbench,provider,models,daily_calls,daily_tokens,active,created_at)"
                     " VALUES(?,?,?,?,?,?,1,?)",
                     (tok, wb, provider, json.dumps(models),
                      int(b.get("dailyCalls") or 0), int(b.get("dailyTokens") or 0), time.time()))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(500, "token 撞库（重试即可）")
    finally:
        conn.close()
    # 完整 token 只在创建/轮换响应里出现一次——列表接口永远打码
    return {"ok": True, "token": tok, "workbench": wb, "provider": provider,
            "note": "完整凭证只显示这一次，请立即写入该工作台 .env"}


@app.post("/admin/credentials/{cid}/revoke")
def admin_revoke(cid: int, authorization: str | None = Header(None)):
    _admin(authorization)
    conn = _connect()
    try:
        n = conn.execute("UPDATE credentials SET active=0, revoked_at=? WHERE id=? AND active=1",
                         (time.time(), cid)).rowcount
        conn.commit()
    finally:
        conn.close()
    if not n:
        raise HTTPException(404, "凭证不存在或已吊销")
    return {"ok": True}


@app.post("/admin/credentials/{cid}/rotate")
def admin_rotate(cid: int, authorization: str | None = Header(None)):
    """轮换=旧 token 立即失效、原配置换新 token。响应含完整新 token（仅此一次）。"""
    _admin(authorization)
    conn = _connect()
    try:
        r = conn.execute(_SEL + " WHERE id=? AND active=1", (cid,)).fetchone()
        if not r:
            conn.close()
            raise HTTPException(404, "凭证不存在或已吊销")
        new_tok = f"tok_{r[2]}_{secrets.token_hex(12)}"
        conn.execute("UPDATE credentials SET token=?, created_at=? WHERE id=?", (new_tok, time.time(), cid))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "token": new_tok, "note": "旧凭证已失效；完整新凭证只显示这一次"}


@app.get("/admin/usage")
def admin_usage(days: int = 7, authorization: str | None = Header(None)):
    _admin(authorization)
    days = max(1, min(180, days))
    since = time.time() - days * 86400
    conn = _connect()
    try:
        by_wb = [{"workbench": r[0], "calls": r[1], "tokens": r[2], "failed": r[3], "avgMs": round(r[4] or 0)}
                 for r in conn.execute(
                     "SELECT workbench, COUNT(*), COALESCE(SUM(total_tokens),0), SUM(1-ok), AVG(ms)"
                     " FROM gw_usage WHERE ts>=? GROUP BY workbench ORDER BY 3 DESC", (since,))]
        by_user = [{"user": r[0], "workbench": r[1], "calls": r[2], "tokens": r[3]}
                   for r in conn.execute(
                       "SELECT user, workbench, COUNT(*), COALESCE(SUM(total_tokens),0)"
                       " FROM gw_usage WHERE ts>=? GROUP BY user, workbench ORDER BY 4 DESC", (since,))]
        by_day = [{"day": r[0], "calls": r[1], "tokens": r[2]}
                  for r in conn.execute(
                      "SELECT day, COUNT(*), COALESCE(SUM(total_tokens),0)"
                      " FROM gw_usage WHERE ts>=? GROUP BY day ORDER BY 1", (since,))]
        recent = [{"ts": r[0], "workbench": r[1], "user": r[2], "provider": r[3], "model": r[4],
                   "tokens": r[5], "ms": r[6], "ok": bool(r[7]), "err": r[8]}
                  for r in conn.execute(
                      "SELECT ts,workbench,user,provider,model,total_tokens,ms,ok,err"
                      " FROM gw_usage WHERE ts>=? ORDER BY ts DESC LIMIT 100", (since,))]
    finally:
        conn.close()
    return {"ok": True, "days": days, "byWorkbench": by_wb, "byUser": by_user,
            "byDay": by_day, "recent": recent, "siteDailyTokens": SITE_DAILY_TOKENS}
