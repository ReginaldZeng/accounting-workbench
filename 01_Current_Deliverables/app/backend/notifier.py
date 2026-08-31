# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-08-01 | Author: Claude / c | Version: V2.160
# Description: 汇率录入·统一通知（邮件 + 钉钉工作通知）。业务方定：保留邮件与消息两种，不做待办。
# Date: 2026-08-03 | Author: Claude / c | Version: V2.162
# Description: 钉钉改「机器人单聊」为主、工作通知为回退。业务方为应用开了「机器人配置」，机器人消息
#   （api.dingtalk.com v1.0 /robot/oToMessages/batchSend，robotCode=AppKey，msgKey=sampleText）比工作通知
#   更像会话消息；三项权限（gettoken/手机号查userid/工作通知）2026-08-03 已实测通，机器人 O2O 亦实测通。
#   send_dingtalk() 先发机器人，失败再回退工作通知(asyncsend_v2)；两条 token（老 oapi 供 getbymobile / 新 v1.0 供机器人）分开缓存。
#   任一渠道未配置/失败都不抛错、只返回结果，绝不弄垮自动跑批。配置走 conf.ini [dingtalk]（机密，gitignore）。
import time
import json
import configparser
import mailer

_TOK = {"v": None, "exp": 0.0}       # oapi.dingtalk.com access_token（工作通知 + getbymobile）
_TOK2 = {"v": None, "exp": 0.0}      # api.dingtalk.com v1.0 accessToken（机器人单聊）


def load_dingtalk_conf():
    """读 conf.ini [dingtalk]；缺段/缺 appkey/appsecret/agentid → None（视作未配置）。"""
    try:
        import kingdee_client as kc
        p = kc.conf_path()
    except Exception:
        p = ""
    if not p:
        return None
    cfg = configparser.ConfigParser()
    cfg.read(p, encoding="utf-8")
    if not cfg.has_section("dingtalk"):
        return None
    c = cfg["dingtalk"]

    def _split(v):
        return [x.strip() for x in (v or "").replace("；", ";").replace("，", ";").replace(",", ";").split(";") if x.strip()]

    conf = {"appkey": c.get("appkey", "").strip(), "appsecret": c.get("appsecret", "").strip(),
            "agentid": c.get("agentid", "").strip(),
            "mobiles": _split(c.get("to_mobiles", "")), "userids": _split(c.get("to_userids", ""))}
    if not (conf["appkey"] and conf["appsecret"] and conf["agentid"]):
        return None
    return conf


def dingtalk_configured():
    return load_dingtalk_conf() is not None


def _dt_token(conf):
    """老版 token（oapi.dingtalk.com）——工作通知 asyncsend_v2 + 按手机号查 userid 用。"""
    import requests
    now = time.time()
    if _TOK["v"] and _TOK["exp"] > now + 60:
        return _TOK["v"]
    r = requests.get("https://oapi.dingtalk.com/gettoken",
                     params={"appkey": conf["appkey"], "appsecret": conf["appsecret"]}, timeout=20).json()
    if r.get("errcode") != 0:
        raise RuntimeError(f"gettoken 失败：{r.get('errmsg')}")
    _TOK["v"] = r["access_token"]
    _TOK["exp"] = now + int(r.get("expires_in", 7200) or 7200)
    return _TOK["v"]


def _dt_v2_token(conf):
    """新版 token（api.dingtalk.com v1.0）——机器人单聊 batchSend 用。"""
    import requests
    now = time.time()
    if _TOK2["v"] and _TOK2["exp"] > now + 60:
        return _TOK2["v"]
    r = requests.post("https://api.dingtalk.com/v1.0/oauth2/accessToken",
                      json={"appKey": conf["appkey"], "appSecret": conf["appsecret"]}, timeout=20).json()
    tok = r.get("accessToken")
    if not tok:
        raise RuntimeError(f"v1.0 accessToken 失败：{r}")
    _TOK2["v"] = tok
    _TOK2["exp"] = now + int(r.get("expireIn", 7200) or 7200)
    return tok


def _dt_resolve_userids(conf, tok):
    """收件人 userid：优先直接配的 userids；再把 to_mobiles 逐个查成 userid（需应用开通该权限）。"""
    import requests
    uids = list(conf["userids"])
    for m in conf["mobiles"]:
        r = requests.post("https://oapi.dingtalk.com/topapi/v2/user/getbymobile",
                          params={"access_token": tok}, json={"mobile": m}, timeout=20).json()
        uid = (r.get("result") or {}).get("userid") if r.get("errcode") == 0 else None
        if uid:
            uids.append(uid)
        else:
            raise RuntimeError(f"按手机号查 userid 失败（{m}）：{r.get('errmsg')}"
                               f"（需应用开通「根据手机号获取成员信息」权限，或改在 [dingtalk] to_userids 直接配 userid）")
    return list(dict.fromkeys(uids))


def _all_userids(conf):
    """汇总收件 userid：有手机号则用老 token 查（顺带并入直配 userids）；否则直接用直配 userids。"""
    if conf["mobiles"]:
        return _dt_resolve_userids(conf, _dt_token(conf))
    return list(dict.fromkeys(conf["userids"]))


def send_dingtalk_robot(text, conf=None):
    """发钉钉【机器人单聊】消息（O2O batchSend）。未配置/失败 → {'sent': False, 'msg': ...}，不抛错。"""
    conf = conf or load_dingtalk_conf()
    if not conf:
        return {"sent": False, "msg": "未配置钉钉（conf.ini [dingtalk]），未发送"}
    try:
        import requests
        uids = _all_userids(conf)
        if not uids:
            return {"sent": False, "msg": "无收件人（[dingtalk] to_mobiles/to_userids 皆空）"}
        tok = _dt_v2_token(conf)
        body = {"robotCode": conf["appkey"], "userIds": uids, "msgKey": "sampleText",
                "msgParam": json.dumps({"content": text}, ensure_ascii=False)}
        r = requests.post("https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend",
                          headers={"x-acs-dingtalk-access-token": tok, "Content-Type": "application/json"},
                          data=json.dumps(body, ensure_ascii=False).encode("utf-8"), timeout=20).json()
        if r.get("processQueryKey"):
            return {"sent": True, "via": "robot", "to": uids,
                    "invalid": r.get("invalidStaffIdList") or []}
        return {"sent": False, "msg": f"机器人发送失败：{r.get('message') or r}"}
    except Exception as e:
        return {"sent": False, "msg": f"机器人发送异常：{e}"}


def send_dingtalk_worknotice(text, conf=None):
    """发钉钉【工作通知】(asyncsend_v2)——机器人失败时的回退。未配置/失败 → {'sent': False, ...}，不抛错。"""
    conf = conf or load_dingtalk_conf()
    if not conf:
        return {"sent": False, "msg": "未配置钉钉（conf.ini [dingtalk]），未发送"}
    try:
        import requests
        tok = _dt_token(conf)
        uids = _dt_resolve_userids(conf, tok)
        if not uids:
            return {"sent": False, "msg": "无收件人（[dingtalk] to_mobiles/to_userids 皆空）"}
        body = {"agent_id": int(conf["agentid"]), "userid_list": ",".join(uids),
                "msg": {"msgtype": "text", "text": {"content": text}}}
        r = requests.post("https://oapi.dingtalk.com/topapi/message/corpconversation/asyncsend_v2",
                          params={"access_token": tok}, json=body, timeout=20).json()
        if r.get("errcode") == 0:
            return {"sent": True, "via": "worknotice", "task_id": r.get("task_id"), "to": uids}
        return {"sent": False, "msg": f"钉钉工作通知失败：{r.get('errmsg')}"
                                      f"（可能应用未开通「工作通知」权限）"}
    except Exception as e:
        return {"sent": False, "msg": f"钉钉工作通知异常：{e}"}


def send_dingtalk(text, conf=None):
    """发钉钉：先机器人单聊；失败再回退工作通知。返回最终结果（含 via 标明走了哪条）。"""
    conf = conf or load_dingtalk_conf()
    if not conf:
        return {"sent": False, "msg": "未配置钉钉（conf.ini [dingtalk]），未发送"}
    r = send_dingtalk_robot(text, conf)
    if r.get("sent"):
        return r
    r2 = send_dingtalk_worknotice(text, conf)
    if r2.get("sent"):
        r2["fallback_from"] = r.get("msg")
        return r2
    return {"sent": False, "msg": f"机器人与工作通知均失败：机器人[{r.get('msg')}]；工作通知[{r2.get('msg')}]"}


def notify(subject, text, html=None, dt_conf=None, smtp_conf=None, channels=None, scene=None):
    """统一通知：钉钉(机器人优先) + 邮件(HTML)。
    dt_conf/smtp_conf 显式传入则用之（app 层可注入 DB 覆盖后的收件人），否则各自读 conf.ini。
    scene（V2.230）：分场景收件人——DB notify_recipients 里该场景配了手机号/邮箱就用之（逐渠道覆盖），
    没配的渠道回落 conf.ini 公共名单；凭证(appkey/secret/smtp账号)永远只在 conf.ini。
    channels={'dingtalk':bool,'email':bool} 可临时关某渠道。各渠道未配置/关闭/失败都不抛错。返回 {渠道: 结果}。"""
    out = {}
    ch = channels or {}
    dt = dt_conf if dt_conf is not None else load_dingtalk_conf()
    sm = smtp_conf if smtp_conf is not None else mailer.load_smtp_conf()
    if scene and (dt_conf is None or smtp_conf is None):
        try:
            import db as _db
            row = _db.notify_recipients_map().get(str(scene)) or {}
            if dt and dt_conf is None and row.get("mobiles"):
                dt = {**dt, "mobiles": row["mobiles"], "userids": []}
            if sm and smtp_conf is None and row.get("emails"):
                sm = {**sm, "to": row["emails"], "cc": [], "bcc": []}
        except Exception:
            pass   # 场景解析失败照走公共名单，绝不因此弄垮通知
    if dt and ch.get("dingtalk", True):
        out["dingtalk"] = send_dingtalk(f"{subject}\n{text}", dt)
    if sm and ch.get("email", True):
        out["email"] = mailer.send_mail(subject, html or text.replace("\n", "<br>"), conf=sm)
    if not out:
        out["none"] = {"sent": False, "msg": "未配置/未启用任何通知渠道（钉钉/邮件），仅记录"}
    return out
