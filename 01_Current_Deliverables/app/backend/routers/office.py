# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-04 | Author: Claude Opus 5.5 | Version: V2.796（数字员工办公室）
# Description: 数字员工办公室 · 接口（《数字员工办公室 需求确认书 v1.0》）。
#   · GET  /api/office/roster        值班表（登录即可看：只有件数和状态，没有业务明细）。
#   · GET  /api/office/runs?desk=    某个工位最近的干活记录（登录；出错原文只给主管理员 / 有系统设置权限的人）。
#   · GET  /api/office/screen        大屏用的值班表——**不用登录**，只认请求头 X-Office-Token 里的大屏口令。
#       为什么不让闲置电脑登一个账号挂着：登录只管 7 天，到期就黑屏；更要紧的是一台没人看着、一直登着账号的电脑，
#       谁走过去都能用那个账号。大屏口令只能读这一张脱敏的值班表，别的什么都干不了，丢了也只是让人看到件数。
#   · GET/POST /api/office/screen-token   主管理员：看 / 生成 / 换一个 / 停用大屏口令（换了旧链接立刻失效，留痕）。
#   口令没生成过＝空＝大屏通道整个关着，不是默认放行（同取件令牌的规矩）。
import os
import secrets

from fastapi import APIRouter, Request

from core import JSONResponse, _current_user, db
import worker_store as ws

router = APIRouter()

SCREEN_PATH = "/api/office/screen"
_TOKEN_KEY = "office_screen_token"          # {token, by, at}


def _token():
    return str((db.get_setting(_TOKEN_KEY, None) or {}).get("token") or "")


def screen_token_ok(request):
    """请求是否带着正确的大屏口令。空口令恒 False——防「没生成 = 空 == 空 = 放行」。"""
    tok = _token()
    return bool(tok) and secrets.compare_digest(request.headers.get("X-Office-Token", ""), tok)


_STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


def _page_stamp():
    """大屏页面和图片最后改动的时间戳。大屏每次取数带回去，变了就自己重新加载——上线后不用再去那台电脑上手动刷新。
    看文件改动时间而不是版本号：只改了页面没改后端的上线不会重启后端，版本号是启动时算的，会漏。"""
    try:
        m = os.path.getmtime(os.path.join(_STATIC, "office-screen.html"))
        art = os.path.join(_STATIC, "office-art")
        for f in os.listdir(art):
            m = max(m, os.path.getmtime(os.path.join(art, f)))
        return str(int(m))
    except Exception:
        return ""


@router.get("/api/office/roster")
def office_roster(request: Request):
    u = _current_user(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "未登录"}, status_code=401)
    return {"ok": True, "canManage": db.is_super(u), **ws.roster()}


@router.get(SCREEN_PATH)
def office_screen(request: Request):
    if not screen_token_ok(request):
        return JSONResponse({"ok": False, "msg": "大屏链接已失效，请主管理员在数字员工办公室里重新生成"}, status_code=401)
    return {"ok": True, "canManage": False, "page": _page_stamp(), **ws.roster()}


@router.get("/api/office/runs")
def office_runs(request: Request, desk: str = ""):
    u = _current_user(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "未登录"}, status_code=401)
    d = ws.runs(desk, show_error=db.user_can(u, "enter_settings"))
    if d is None:
        return JSONResponse({"ok": False, "msg": "没有这个工位"}, status_code=404)
    return {"ok": True, **d}


@router.get("/api/office/screen-token")
def office_token_get(request: Request):
    u = _current_user(request)
    if not db.is_super(u):
        return JSONResponse({"ok": False, "msg": "仅主管理员"}, status_code=403)
    t = db.get_setting(_TOKEN_KEY, None) or {}
    return {"ok": True, "token": t.get("token") or "", "by": t.get("by") or "", "at": t.get("at") or ""}


@router.post("/api/office/screen-token")
async def office_token_set(request: Request):
    """body {action: 'new' | 'off'}。new＝生成（已有的作废）；off＝停用大屏链接。"""
    u = _current_user(request)
    if not db.is_super(u):
        return JSONResponse({"ok": False, "msg": "仅主管理员"}, status_code=403)
    b = await request.json()
    act = str(b.get("action") or "")
    if act == "new":
        t = {"token": secrets.token_urlsafe(24), "by": u["name"], "at": ws._now()[:16]}
    elif act == "off":
        t = {"token": "", "by": u["name"], "at": ws._now()[:16]}
    else:
        return JSONResponse({"ok": False, "msg": "不认识的操作"}, status_code=400)
    db.set_setting(_TOKEN_KEY, t, u["name"])
    db.audit(u["name"], "数字员工办公室-大屏链接", "", "重新生成（旧链接作废）" if act == "new" else "停用")
    return {"ok": True, "token": t["token"], "by": t["by"], "at": t["at"]}


ws.purge()
