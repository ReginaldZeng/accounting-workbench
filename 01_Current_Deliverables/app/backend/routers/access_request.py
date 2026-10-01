# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-01 | Author: Claude Opus 5.5 | Version: V2.731
# Description: 首页「申请开通」→ 钉钉推送（移植财务BP工作台 V2.553/V2.555，交接提示词 §2.3，口径已定）。
#   · 谁能申请：任何登录的人，只能申请**自己没有**的准入点（有了提示「刷新即可」）；同人同准入点 24 小时只推一次（防连点刷屏）。
#   · 推给谁：settings access_request_conf.userids（系统设置 → 常规设置「权限申请推送」里选人）；
#     没选 → 默认管理员 = conf.ini [dingtalk] to_userids + to_mobiles 换成的 userId（notifier._all_userids，
#     BP V2.555 踩过的「只配了手机号→接收人 0」在这里天然规避）。与风控值守/取件机停机告警同一个钉钉应用。
#   · 记录：settings access_requests（最近 300 条，含是否送达/失败原因），设置卡显示最近 10 条；/mine 供首页卡片显示「已申请 · 时间」。
#   · 本机（sqlite 库）一律打桩不真发：本机可能读到真实 conf.ini，会真的推出去（交接 §2.3 ⚠）。
from datetime import datetime, timedelta

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from core import JSONResponse, _current_user, db

router = APIRouter()

CONF_KEY = "access_request_conf"
LOG_KEY = "access_requests"
KEEP = 300
COOLDOWN = timedelta(hours=24)
_FMT = "%Y-%m-%d %H:%M:%S"


def _is_local():
    try:
        return str(db.DB_URL).startswith("sqlite")
    except Exception:
        return False


def _recipients():
    """→ (userids, 来源 configured/default, conf 或 None, 不可用原因)。"""
    import notifier
    conf = notifier.load_dingtalk_conf()
    if not conf:
        return [], "default", None, "未配置钉钉（conf.ini [dingtalk] appkey/appsecret/agentid）"
    picked = [u for u in ((db.get_setting(CONF_KEY, None) or {}).get("userids") or []) if u]
    if picked:
        return picked, "configured", conf, ""
    try:
        return notifier._all_userids(conf), "default", conf, ""
    except Exception as e:
        return [], "default", conf, "默认管理员取不到 userId：%s" % str(e)[:160]


def _cap_label(cap):
    for m in db.cap_meta():
        if m.get("key") == cap:
            return m.get("label") or cap
    return None


@router.post("/api/access-request")
async def submit(request: Request):
    u = _current_user(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "未登录"}, status_code=401)
    b = await request.json()
    cap = str(b.get("cap") or "").strip()
    cap_label = _cap_label(cap)
    if not cap_label:
        return JSONResponse({"ok": False, "msg": "不认识的权限点"}, status_code=400)
    if db.user_can(u, cap):
        return JSONResponse({"ok": False, "msg": "你已经有这个权限了，刷新页面即可使用"}, status_code=400)
    now = datetime.now()
    log = list(db.get_setting(LOG_KEY, None) or [])
    for r in reversed(log):
        if r.get("user") == u["name"] and r.get("cap") == cap and r.get("sent"):
            try:
                at = datetime.strptime(r["at"], _FMT)
            except Exception:
                continue
            if now - at < COOLDOWN:
                return JSONResponse({"ok": False, "msg": "%s 已经申请过了，管理员处理后刷新页面即可" % at.strftime("%m-%d %H:%M"),
                                     "at": r["at"]}, status_code=429)
            break
    uids, src, conf, why = await run_in_threadpool(_recipients)
    if _is_local() and not uids:          # 本机测试库没配钉钉：走打桩，好验证去重与「已申请」
        uids, src, conf = ["本机打桩"], "local", conf or {}
    if not conf and conf != {}:
        return JSONResponse({"ok": False, "msg": "钉钉未就绪：%s" % why}, status_code=503)
    if not uids:
        return JSONResponse({"ok": False, "msg": why or "还没有设置接收人：请主管理员在 系统设置 → 常规设置「权限申请推送」里选人"},
                            status_code=503)
    label = str(b.get("label") or cap_label).strip()[:30]
    pages = [str(p).strip()[:20] for p in (b.get("pages") or []) if str(p).strip()][:12]
    note = str(b.get("note") or "").strip()[:200]
    post = u.get("post") or ""
    text = "\n".join(x for x in [
        "【核算工作台 · 权限申请】",
        "%s%s 申请开通「%s」" % (u["name"], "（%s）" % post if post else "", label),
        "含页面：%s" % ("、".join(pages) if pages else label),
        "权限点：%s（%s）" % (cap_label, cap),
        ("用途：%s" % note) if note else "",
        "处理：门户 › 账号管理 → 找到 %s → 勾选「%s」，对方刷新核算工作台即可使用。" % (u["name"], cap_label),
        now.strftime("%Y-%m-%d %H:%M"),
    ] if x)
    if _is_local():                      # 本机打桩：不真发
        sent, err = True, "本机测试·未真发"
    else:
        import notifier
        try:
            r = await run_in_threadpool(notifier.send_dingtalk_to, uids, text, conf)
            sent, err = bool((r or {}).get("sent")), "" if (r or {}).get("sent") else str((r or {}).get("msg") or "发送失败")[:200]
        except Exception as e:
            sent, err = False, "%s: %s" % (type(e).__name__, str(e)[:200])
    log.append({"at": now.strftime(_FMT), "user": u["name"], "post": post, "cap": cap, "label": label, "pages": pages,
                "note": note, "to": len(uids), "toSource": src, "sent": sent, "error": err})
    db.set_setting(LOG_KEY, log[-KEEP:], u["name"])
    db.audit(u["name"], "申请开通权限", cap, "%s → %d 人%s" % (label, len(uids), "" if sent else "（失败：%s）" % err))
    if not sent:
        return JSONResponse({"ok": False, "msg": "推送失败：%s" % err}, status_code=502)
    return {"ok": True, "at": now.strftime(_FMT), "to": len(uids)}


@router.get("/api/access-request/mine")
def mine(request: Request):
    """当前用户 24 小时内已推送成功的申请 {cap: at}（首页卡片显示「已申请 · 时间」）。轻量，只读设置表，不碰业务数据。"""
    u = _current_user(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "未登录"}, status_code=401)
    since = datetime.now() - COOLDOWN
    out = {}
    for r in db.get_setting(LOG_KEY, None) or []:
        if r.get("user") != u["name"] or not r.get("sent"):
            continue
        try:
            if datetime.strptime(r["at"], _FMT) >= since:
                out[r["cap"]] = r["at"]
        except Exception:
            pass
    return {"ok": True, "requested": out}


def _settings_user(request):
    u = _current_user(request)
    return u if db.user_can(u, "enter_settings") else None


@router.get("/api/access-request/config")
async def get_config(request: Request):
    """接收人配置 + 生效人数自检（effective>0 才发得出去）+ 最近 30 条申请。"""
    if not _settings_user(request):
        return JSONResponse({"ok": False, "msg": "仅主管理员"}, status_code=403)
    uids, src, conf, why = await run_in_threadpool(_recipients)
    cfg = db.get_setting(CONF_KEY, None) or {}
    log = list(db.get_setting(LOG_KEY, None) or [])
    return {"ok": True, "userids": cfg.get("userids") or [], "names": cfg.get("names") or {}, "effective": len(uids),
            "source": src, "robotOk": bool(conf), "robotMsg": why, "local": _is_local(), "recent": list(reversed(log[-30:]))}


@router.put("/api/access-request/config")
async def put_config(request: Request):
    u = _settings_user(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "仅主管理员"}, status_code=403)
    b = await request.json()
    ids, names = [], {}
    for e in b.get("entries") or []:
        uid = str((e or {}).get("userid") or "").strip()
        if uid and uid not in ids:
            ids.append(uid)
            names[uid] = str((e or {}).get("name") or "")[:30]
    db.set_setting(CONF_KEY, {"userids": ids, "names": names, "updated_at": datetime.now().strftime(_FMT)}, u["name"])
    db.audit(u["name"], "权限申请推送·设接收人", "", "、".join(names.values()) or "默认管理员")
    return {"ok": True, "userids": ids}
