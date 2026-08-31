# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-08-01 | Author: Claude / c | Version: V2.160
# Description: 汇率录入·P4 邮件框架（工作台首个邮件能力，stdlib smtplib，无新依赖）。
#   配置走 backend/conf.ini 的 [smtp] 段（机密不进库不进前端，同金蝶授权惯例）；未配置则记日志、不发、不报错。
#   目前仅汇率工具自动跑批用；设计成通用 send_mail(subject, html, to)，后续物流计提/对账可复用。
# Date: 2026-08-03 | Author: Claude / c | Version: V2.164
# Description: 新增抄送(cc) + 密送(bcc)。cc 进邮件头(收件人可见)、bcc 不进头但计入实际投递名单(隐藏)。
#   三类地址均支持多人（; , ； ， 任意分隔）；envelope 投递名单 = to + cc + bcc，邮件头只写 To/Cc。
import smtplib
import ssl
import configparser
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr


def _split(v):
    return [x.strip() for x in (v or "").replace("；", ";").replace("，", ";").replace(",", ";").split(";") if x.strip()]


def load_smtp_conf():
    """读 conf.ini [smtp] 段；缺段/缺 host/from 则返回 None（视作未配置）。"""
    try:
        import kingdee_client as kc
        p = kc.conf_path()
    except Exception:
        p = ""
    if not p:
        return None
    cfg = configparser.ConfigParser()
    cfg.read(p, encoding="utf-8")
    if not cfg.has_section("smtp"):
        return None
    c = cfg["smtp"]
    conf = {
        "host": c.get("host", "").strip(),
        "port": int((c.get("port", "465") or "465").strip()),
        "user": c.get("user", "").strip(),
        "pwd": c.get("pwd", "").strip(),
        "from": (c.get("from", "") or c.get("user", "")).strip(),
        "from_name": (c.get("from_name", "") or "汇率录入工具").strip(),
        "tls": (c.get("tls", "ssl") or "ssl").strip().lower(),   # ssl / starttls / none
        "to": _split(c.get("to", "")),
        "cc": _split(c.get("cc", "")),
        "bcc": _split(c.get("bcc", "")),
    }
    if not conf["host"] or not conf["from"]:
        return None
    return conf


def configured():
    return load_smtp_conf() is not None


def send_mail(subject, html, to=None, conf=None):
    """发一封 HTML 邮件（支持抄送 cc / 密送 bcc）。未配置 SMTP → {'sent': False, ...}（不抛错，让自动跑批照常留痕）。
    to 缺省用 conf['to']；cc/bcc 取自 conf。返回 {'sent': bool, 'to':[...], 'cc':[...], 'bcc':[...], 'msg':...}。"""
    conf = conf or load_smtp_conf()
    if not conf:
        return {"sent": False, "msg": "未配置 SMTP（conf.ini [smtp]），邮件未发送（仅记录）"}
    to_list = to if to else conf.get("to", [])
    if isinstance(to_list, str):
        to_list = [to_list]
    cc_list = conf.get("cc", [])
    bcc_list = conf.get("bcc", [])
    envelope = list(dict.fromkeys(to_list + cc_list + bcc_list))   # 实际投递名单（去重），含密送
    if not envelope:
        return {"sent": False, "msg": "无收件人（conf.ini [smtp] to/cc/bcc 皆空）"}
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header(conf["from_name"], "utf-8")), conf["from"]))
    if to_list:
        msg["To"] = ";".join(to_list)
    if cc_list:
        msg["Cc"] = ";".join(cc_list)     # 抄送进头（可见）；bcc 不进头（隐藏）
    try:
        if conf["tls"] == "ssl":
            srv = smtplib.SMTP_SSL(conf["host"], conf["port"], timeout=30,
                                   context=ssl.create_default_context())
        else:
            srv = smtplib.SMTP(conf["host"], conf["port"], timeout=30)
            if conf["tls"] == "starttls":
                srv.starttls(context=ssl.create_default_context())
        if conf["user"]:
            srv.login(conf["user"], conf["pwd"])
        srv.sendmail(conf["from"], envelope, msg.as_string())
        srv.quit()
        return {"sent": True, "to": to_list, "cc": cc_list, "bcc": bcc_list}
    except Exception as e:
        return {"sent": False, "msg": f"SMTP 发送失败：{e}", "to": to_list, "cc": cc_list, "bcc": bcc_list}
