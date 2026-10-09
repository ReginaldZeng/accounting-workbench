# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-09 | Author: Claude Opus 5.5 | Version: V2.884（钉钉免登）
# Description: 从钉钉工作台点开「星衡智策」时自动认人登录，不用再输账号密码。
#   · GET  /api/dd/hello     登录页问一句：钉钉配了没有、企业编号、应用编号（页面拿去向钉钉要免登码）。
#   · POST /api/login/dd     免登码 → 钉钉说出是谁 → 这个钉钉身份认过哪个账号 → 发登录会话（和密码登录同一种会话）。
#       没认过账号：不猜、不按姓名自动对——回一张 10 分钟有效的「认人凭条」，页面照常显示登录框；
#       本人输一次账号密码登录成功（见 app.api_login → bind_after_login），就把这个钉钉身份记到账号上，下次直接进。
#   为什么不按姓名自动对：账号名是手工建的，重名、改名、外部协作账号都对不准；认错一次就是拿别人的权限进系统。
#   为什么不认发票管家设置里那份钉钉名单：那份名单有「发票管家设置」权限就能改，拿它当登录凭据等于
#       谁能改名单谁就能登任何人的账号。这里只认「本人拿密码证明过」的那一次。
#   凭条只存在内存里（存 sha256、用一次就作废、重启即失效），不落库；免登码换身份有按 IP 的限流。
import hashlib
import secrets
import threading
import time

from fastapi import APIRouter, Request, Response
from starlette.concurrency import run_in_threadpool

from core import JSONResponse, _user_public, db, sid_name
from routers import invoice as inv

router = APIRouter()
idt = inv.idt

TICKET_S = 600           # 认人凭条 10 分钟有效
WINDOW_S = 600
MAX_HITS = 30             # 同一 IP 10 分钟最多换 30 次身份（每次都要去问钉钉）

_LOCK = threading.Lock()
_TICKETS = {}             # sha256(凭条) → (钉钉 userid, 钉钉姓名, 过期时刻)
_HITS = {}                # IP → [时刻]


def _h(s):
    return hashlib.sha256(str(s).encode("utf-8")).hexdigest()


def _ip(request):
    return (request.headers.get("x-real-ip") or (request.client.host if request.client else "") or "")[:64]


def _throttle_ok(ip):
    now = time.time()
    with _LOCK:
        hits = [t for t in _HITS.get(ip, []) if now - t < WINDOW_S]
        ok = len(hits) < MAX_HITS
        if ok:
            hits.append(now)
        _HITS[ip] = hits
        if len(_HITS) > 5000:
            for k in [k for k, v in _HITS.items() if not v or now - v[-1] > WINDOW_S]:
                _HITS.pop(k, None)
    return ok


def _new_ticket(uid, name):
    tok = secrets.token_urlsafe(24)
    now = time.time()
    with _LOCK:
        for k in [k for k, v in _TICKETS.items() if v[2] < now]:
            _TICKETS.pop(k, None)
        _TICKETS[_h(tok)] = (uid, name, now + TICKET_S)
    return tok


def _take_ticket(tok):
    """凭条 → (钉钉 userid, 钉钉姓名)，取一次就作废；不存在/过期 → None。"""
    tok = str(tok or "")
    if not tok or len(tok) > 100:
        return None
    with _LOCK:
        v = _TICKETS.pop(_h(tok), None)
    if not v or v[2] < time.time():
        return None
    return v[0], v[1]


def bind_after_login(user, ticket):
    """密码登录成功后调：带着有效凭条 → 把那个钉钉身份记到这个账号上。
    返回 {bound, msg}；没带凭条 → None（普通登录，什么都不做）。永不抛异常——认不上不能把已经成功的登录打回去。"""
    if not ticket:
        return None
    try:
        got = _take_ticket(ticket)
        if not got:
            return {"bound": False, "msg": "钉钉认人已过期（超过 10 分钟），这次没记下；下次从钉钉打开再登录一次即可"}
        uid, dt_name = got
        ok, msg = db.bind_user_dt(user["name"], uid, dt_name)
        if ok:
            db.audit(user["name"], "钉钉免登-认下钉钉身份", user["name"],
                     "钉钉姓名：%s%s" % (dt_name or "未知", "" if dt_name == user["name"] else "（和账号名不一样）"))
        return {"bound": ok, "msg": msg}
    except Exception as ex:
        print("[钉钉免登] 记钉钉身份失败：%s" % ex)
        return {"bound": False, "msg": "钉钉身份这次没记下，下次从钉钉打开再登录一次即可"}


@router.get("/api/dd/hello")
def dd_hello():
    conf = idt._load_conf() if idt.configured() else None
    return {"ok": True, "dingtalk": bool(conf),
            "corpId": (inv.get_settings().get("corpId") or "") if conf else "",
            "clientId": (conf or {}).get("appkey") or ""}


@router.post("/api/login/dd")
async def login_dd(request: Request, response: Response):
    try:
        body = await request.json()
    except Exception:
        body = {}
    code = str((body or {}).get("code") or "").strip()[:200] if isinstance(body, dict) else ""
    if not code:
        return JSONResponse({"ok": False, "code": "dd_fail", "msg": "没拿到钉钉免登码"}, status_code=400)
    if not _throttle_ok(_ip(request)):
        return JSONResponse({"ok": False, "code": "dd_fail", "msg": "试得太频繁了，过几分钟再来，或直接用账号密码登录"},
                            status_code=429)
    who = await run_in_threadpool(idt.userinfo_by_code, code)
    if not who.get("ok"):
        return JSONResponse({"ok": False, "code": "dd_fail",
                             "msg": "钉钉没认出你是谁（%s）" % (who.get("msg") or "原因不明")}, status_code=403)
    uid, dt_name = who["userid"], who.get("name") or ""
    u = db.user_by_dt(uid)
    if u and not u.get("active"):
        return JSONResponse({"ok": False, "code": "disabled",
                             "msg": "你的账号（%s）已被禁用，请联系管理员" % u["name"]}, status_code=403)
    if u:
        tok = db.create_session(u["name"])
        response.set_cookie(sid_name(request), tok, httponly=True, max_age=7 * 24 * 3600, samesite="lax")
        db.audit(u["name"], "登录", "", "钉钉免登")
        return {"ok": True, "user": _user_public(u)}
    # 还没认过账号：给凭条，让本人用密码登录一次来认
    return {"ok": False, "code": "unbound", "ddName": dt_name, "ticket": _new_ticket(uid, dt_name)}
