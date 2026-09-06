# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-09-06 | Author: Claude / Reginald Zeng | Version: V2.489
# Description: 【日志中心】只读接口。两条数据源：
#              ① 运维请求日志——来自 ops.py 的埋点（内存实时态 + ops_log.db 历史）；
#              ② 业务操作留痕——来自 db.audit_log（登录/改权限/封存/建账号…35 类敏感动作）。
#              全部**只读、无副作用**。权限闸沿用「进入系统设置」(enter_settings)——与"日志放在系统设置内、
#              仅主管理员"的定位一致；此处逐点挂 _require_perm，不改 app.py 的登录门语义。
#              app.py 只 include_router(router)，不感知本文件内部（与其它 router 一致）。

import datetime as _dt

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

import ops
from core import JSONResponse, _require_perm, db

router = APIRouter()

_GATE = "enter_settings"   # 与「系统设置」同闸：默认仅主管理员，可由主管理员在账号管理授权


def _deny():
    return JSONResponse({"ok": False, "msg": "无「进入系统设置」权限，日志中心仅主管理员（或获授权者）可看"},
                        status_code=403)


# ══════════════════ 运维请求日志（移植 BP 运维监控） ══════════════════
@router.get("/api/ops/live")
def get_live(request: Request):
    """实时探针：当前正在处理的请求 + 最近完成 + 谁在线。**只反映本进程**（多 worker 时各进程内存独立）。
    页面每几秒轮询这个口；/api/ops 本身不记日志，不会自噪声。"""
    if not _require_perm(request, _GATE):
        return _deny()
    return ops.live()


@router.get("/api/ops/stats")
def get_stats(request: Request, days: int = Query(7, ge=1, le=90), slowLimit: int = Query(15, ge=1, le=50)):
    """历史聚合：并发曲线 / 活跃用户 / 板块使用 / 慢接口榜 / 错误列表 / 容量建议。"""
    if not _require_perm(request, _GATE):
        return _deny()
    return ops.stats(days=days, slow_limit=slowLimit)


@router.get("/api/ops/logs")
def get_logs(
    request: Request,
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(300, ge=1, le=2000),
    user: str = Query(None),
    board: str = Query(None),
    onlyErrors: bool = Query(False),
):
    """原始请求日志（倒序）。可按用户 / 板块 / 只看错误过滤。"""
    if not _require_perm(request, _GATE):
        return _deny()
    return {"rows": ops.recent_logs(limit=limit, user=user, board=board,
                                    only_errors=onlyErrors, days=days)}


@router.get("/api/ops/user-sessions")
def get_user_sessions(
    request: Request,
    user: str = Query(..., description="登录用户名；未登录传 (未透传身份)、系统自检传 系统·自检"),
    days: int = Query(1, ge=1, le=30),
    gapMin: int = Query(30, ge=5, le=240),
):
    """某用户的操作会话/轨迹：按 gapMin 分钟静默切会话，最近会话在前。"""
    if not _require_perm(request, _GATE):
        return _deny()
    return ops.user_sessions(user, days=days, gap_min=gapMin)


@router.get("/api/ops/logs.csv", response_class=PlainTextResponse)
def get_logs_csv(request: Request, days: int = Query(7, ge=1, le=90), limit: int = Query(2000, ge=1, le=2000)):
    """请求日志导出 CSV（Excel 直接打开）。带 UTF-8 BOM，否则 Excel 打开中文是乱码。"""
    if not _require_perm(request, _GATE):
        return _deny()
    rows = ops.recent_logs(limit=limit, days=days)
    head = "时间,用户,方法,路径,板块,状态码,耗时ms,在飞数,进程,来源IP,错误"
    out = [head]

    def esc(v):
        s = "" if v is None else str(v)
        return '"%s"' % s.replace('"', '""') if ("," in s or '"' in s or "\n" in s) else s

    for r in rows:
        out.append(",".join(esc(x) for x in [
            _dt.datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M:%S"),
            r["user"] or "", r["method"], r["path"], r["board"], r["status"],
            r["ms"], r["inflight"], r["pid"], r["ip"] or "", r["err"] or "",
        ]))
    return PlainTextResponse(
        "﻿" + "\n".join(out),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="workbench_ops_logs.csv"'},
    )


# ══════════════════ 业务操作留痕（db.audit_log） ══════════════════
@router.get("/api/ops/audit")
def get_audit(
    request: Request,
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(500, ge=1, le=5000),
    operator: str = Query(None),
    action: str = Query(None),
):
    """业务操作留痕（倒序）：谁、何时、对谁、做了什么、备注。可按操作人 / 动作 / 天数过滤。"""
    if not _require_perm(request, _GATE):
        return _deny()
    return {"rows": db.query_audit(operator=operator, action=action, days=days, limit=limit),
            "meta": db.audit_meta(days=max(days, 90))}


@router.get("/api/ops/audit.csv", response_class=PlainTextResponse)
def get_audit_csv(request: Request, days: int = Query(30, ge=1, le=365), limit: int = Query(5000, ge=1, le=5000)):
    """操作留痕导出 CSV。带 UTF-8 BOM。"""
    if not _require_perm(request, _GATE):
        return _deny()
    rows = db.query_audit(days=days, limit=limit)
    head = "时间,操作人,动作,对象,备注"
    out = [head]

    def esc(v):
        s = "" if v is None else str(v)
        return '"%s"' % s.replace('"', '""') if ("," in s or '"' in s or "\n" in s) else s

    for r in rows:
        out.append(",".join(esc(x) for x in [
            r.get("ts", ""), r.get("operator", ""), r.get("action", ""),
            r.get("target", ""), r.get("detail", ""),
        ]))
    return PlainTextResponse(
        "﻿" + "\n".join(out),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="workbench_audit.csv"'},
    )
