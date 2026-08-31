# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-08-07 | Author: Claude / c | Version: V2.241
# Description: 【报表导出】路由。一个主体一个文件、五个表页（三大报表+科目余额+序时账簿），
#              从金蝶直取、整形、落到指定目录。本文件是这条工具线在后端的唯一落点。
#
#              为什么必须走后台任务：单主体实测约 30 秒（序时账簿 2.4 万行是大头），
#              8 个主体≈4 分钟。挂在一个 HTTP 请求里必被网关/浏览器超时掐断，
#              而且掐断时前面几个主体已经落盘了，用户却只看到一个红叉——最坏的那种失败。
#              故：POST 起任务立即返回，前端轮询 GET 进度。
import os
import re
import threading
import time

import kingdee_client as kc
import mailer
import notifier
import report_export as rx
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from core import JSONResponse, _require_perm, db, datetime, pull_token, pull_token_ok

router = APIRouter()

_CFG_KEY = "rpt_export_cfg"          # {out_dir}
# 通知走既有的「分场景收件人」机制（V2.230 建的 notify_recipients 表）：
# 收件人存 DB、前端可改；SMTP 账号密码**只在 conf.ini**，不经前端、不进 DB。
_SCENE = "报表导出"
_JOB = {"running": False, "done": 0, "total": 0, "cur": "", "files": [],
        "errors": [], "started": "", "finished": "", "msg": "", "t0": 0.0, "t1": 0.0}
_LOCK = threading.Lock()


def _passcode():
    """改落地路径的口令——与通知设置同一把（conf.ini [notify] passcode），机密、不下发前端。
    没配＝页面锁改（同汇率线现有行为），不静默放行。"""
    try:
        import configparser
        c = configparser.ConfigParser()
        c.read(kc.conf_path(), encoding="utf-8")
        return (c.get("notify", "passcode", fallback="") or "").strip()
    except Exception:
        return ""


def _conf_out_dir():
    """conf.ini [rptexport] out_dir ＝ 兜底路径（业务方定：前端不填就走它）。"""
    try:
        import configparser
        c = configparser.ConfigParser()
        c.read(kc.conf_path(), encoding="utf-8")
        return (c.get("rptexport", "out_dir", fallback="") or "").strip()
    except Exception:
        return ""


def _out_dir():
    """实际落地目录：前端存过就用前端的，**没存或存了空串就回落 conf.ini**（业务方定）。"""
    cfg = db.get_setting(_CFG_KEY, None) or {}
    return (str(cfg.get("out_dir") or "").strip() or _conf_out_dir()).strip()


def _check_dir(path):
    """返回 (ok, 人话消息)。不自动造多层目录——一个手滑的路径不该在服务器上凭空长出一棵目录树；
    父目录必须已存在，末级不存在则创建（按年月建子目录是常规用法）。"""
    if not path:
        return False, "还没设落地路径。请在②通知设置里填，或让管理员在服务器 conf.ini 的 [rptexport] out_dir 配一个兜底路径。"
    # 跨平台先拦一道：Linux 服务器上填 Windows 路径（UNC \\NAS\... 或 D:\...）是最常见的错，
    # 而且**症状具有欺骗性**——Linux 的 os.path 不认 \ 分隔符，dirname() 返回空串，
    # 回落后会把完整路径当成"上级目录"报出来，提示自相矛盾（V2.241 部署当天实际踩到）。
    if os.sep == "/" and ("\\" in path or re.match(r"^[A-Za-z]:", path)):
        kind = "网络共享路径（UNC）" if path.startswith("\\\\") else "Windows 盘符路径"
        return False, ("这台服务器是 Linux，但填的是 %s：%s —— Linux 不认反斜杠。"
                       "若要落到网络共享，需先由运维把共享挂载到服务器的某个目录"
                       "（如 mount 到 /mnt/nas），这里再填那个挂载点（形如 /mnt/nas/月度报表）。"
                       "也可以先填服务器本地目录（如 /www/wwwroot/报表导出）先跑起来。" % (kind, path))
    parent = os.path.dirname(path.rstrip("\\/")) or path
    if not os.path.isdir(parent):
        return False, "上级目录不存在：%s —— 这是**服务器上**的路径（不是你自己电脑上的），请确认服务器能看到它。" % parent
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".rptexport_write_test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
    except PermissionError:
        return False, "服务器账号对该目录没有写权限：%s —— 请让管理员给运行本工具的账号开写权限。" % path
    except OSError as e:
        return False, "该目录不可写：%s（%s）" % (path, e)
    return True, ""


def _join_disp(base, sub):
    """拼一条给人看的路径，分隔符**跟着 base 自己的走**。
    服务器目录是 Linux 的 /、共享盘是 Windows 的 \\；用 os.path.join 会按跑代码那台机器来选，
    在 Windows 上调试就拼出 /www/...\\2026年06月 这种四不像。"""
    return base.rstrip("\\/") + ("\\" if "\\" in base else "/") + sub


def _share_lines(year, period, out_dir, share):
    """回执里那三行：落地目录 / 共享盘目录 / 同步时间。返回 [(标题, 值), ...]。

    收件人真正要的是「文件在哪、能不能去拿了」。所以路径一律给到**月份子目录那一层**——
    给个上级目录还得自己点进去找，而且一年 12 个月堆在一起。
    同步时间是取件机回报的真实时间，不是服务器猜的。"""
    mdir = rx.month_dir(year, period)
    rows = [("落地目录", _join_disp(out_dir, mdir))]
    if not pull_token():
        return rows                       # 没配取件通道＝压根没有共享盘这一环，不写空行
    s = (share or {}).get("sync") or {}
    dest = s.get("dest") or ""
    rows.append(("共享盘目录", _join_disp(dest, mdir) if dest
                 else "取件机还没回报过——请到工作台「报表导出」页查看"))
    rows.append(("同步时间", s.get("at") if (share or {}).get("complete")
                 else "⚠ 尚未确认送达。取件机可能没在跑，请到工作台「报表导出」页查看共享盘同步那一行"))
    return rows


def _mail_body(year, period, out_dir, files, errors, started, finished, share=None):
    """导出回执邮件。成败都发（业务方定）——"导好了"本身就是要告知的事。
    失败项放最前面：出问题时最该被一眼看到的是哪几个主体没导出来、为什么。"""
    ok = "全部成功" if not errors else "有 %d 个主体失败" % len(errors)
    lines = _share_lines(year, period, out_dir, share)
    rows = "".join(
        "<tr><td style='padding:4px 10px;border-bottom:1px solid #eee'>%s</td>"
        "<td style='padding:4px 10px;border-bottom:1px solid #eee'>%s</td>"
        "<td style='padding:4px 10px;border-bottom:1px solid #eee;text-align:right'>%s</td>"
        "<td style='padding:4px 10px;border-bottom:1px solid #eee;text-align:right'>%s</td></tr>"
        % (f["org"], f["name"], f["rows"].get("科目余额", ""), f["rows"].get("序时账簿", ""))
        for f in files)
    bad = "".join("<li><b>%s %s</b>：%s</li>" % (e["org"], e["name"], e["err"]) for e in errors)
    where = "".join(
        "<tr><td style='padding:3px 12px 3px 0;color:#888;white-space:nowrap;vertical-align:top'>%s</td>"
        "<td style='padding:3px 0'><code style='word-break:break-all'>%s</code></td></tr>" % kv
        for kv in lines)
    html = """<div style="font-family:'Microsoft YaHei',sans-serif;font-size:14px;color:#222">
<p><b>%d年%d期</b> 财务报表导出完成：<b>%s</b>（成功 %d 个主体）。</p>
%s
<table style="border-collapse:collapse;font-size:13px;margin:10px 0">%s</table>
<table style="border-collapse:collapse;font-size:13px">
<tr><th style="padding:4px 10px;text-align:left;border-bottom:2px solid #ddd">主体</th>
<th style="padding:4px 10px;text-align:left;border-bottom:2px solid #ddd">文件</th>
<th style="padding:4px 10px;text-align:right;border-bottom:2px solid #ddd">科目余额</th>
<th style="padding:4px 10px;text-align:right;border-bottom:2px solid #ddd">序时账簿</th></tr>%s</table>
<p style="color:#888;font-size:12px">每个文件五个表页：资产负债表 · 利润表 · 现金流量表 · 科目余额 · 序时账簿。
数据自金蝶直取（只读不写），境外主体按本位币（美元）出。<br>
开始 %s · 结束 %s · 由财务核算工作台「报表板块 › 报表导出」自动发出。</p></div>"""
    html = html % (year, period, ok, len(files),
                   ("<p style='color:#c0392b'><b>失败明细：</b></p><ul style='color:#c0392b'>%s</ul>" % bad) if bad else "",
                   where, rows, started, finished)
    text = "%d年%d期 财务报表导出完成：%s（成功 %d 个主体）。\n" % (year, period, ok, len(files))
    text += "\n".join("%s：%s" % kv for kv in lines)
    subject = "[报表导出] %d年%d期 %s（成功 %d 个）" % (year, period, ok, len(files))
    return subject, text, html


def _channels():
    """通知渠道开关，存 rpt_export_cfg。默认：邮件开、钉钉关（业务方最初只要邮件）。"""
    c = db.get_setting(_CFG_KEY, None) or {}
    return {"email": bool(c.get("email_on", True)), "dingtalk": bool(c.get("dingtalk_on", False))}


def _wait_share(files, after, timeout=150):
    """等取件机把这一批送到共享盘，再发通知。返回 {"sync": 回报内容, "complete": 送齐没有}。

    为什么要等：导出一跑完就发通知的话，通知里的「同步时间」只能是**上一轮**的旧值——
    比这次导出还早，等于告诉收件人"文件已经在共享盘了"，而其实还没送到。
    人照着通知去共享盘拿，拿到的是上个月的文件，这比不写这行还糟。

    判据不是"有新回报"而是"**这一批的文件名都在 copied 里**"：取件机可能在导出结束前
    就取过清单，那一轮的回报虽然新，却不含这批文件。
    重导必然改 mtime，所以这批一定会被重新下载、一定会出现在 copied 里。

    等待发生在任务已标记完成之后（见 _run），所以页面上的进度条不会跟着一直转。"""
    if not pull_token():
        return {"sync": None, "complete": False}      # 没配取件通道＝没有共享盘这一环
    want = {f["name"] for f in files}
    # 顺手催一轮，别干等到取件机自己的下一个整点
    db.set_setting(_WANT_KEY, {"at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                               "by": "导出完成自动同步"}, "system")
    deadline, last = time.time() + timeout, None
    while True:
        last = db.get_setting(_SYNC_KEY, None) or None
        if last and last.get("at", "") >= after:
            got = {os.path.basename(x) for x in (last.get("copied") or [])}
            if want <= got:
                return {"sync": last, "complete": True}
        if time.time() >= deadline:
            return {"sync": last, "complete": False}
        time.sleep(3)


def _send_notify(year, period, out_dir, files, errors, started, finished, share=None):
    """发导出回执。邮件 / 钉钉两条渠道各自可开关（页面②设置）。
    通知失败不能影响导出结果本身——文件已经落盘了，发不出去是另一回事，故整体兜住只记消息。"""
    try:
        ch = _channels()
        want = [k for k, v in ch.items() if v]
        if not want:
            return {"sent": False, "msg": "两条通知渠道都关着，未发送"}
        miss = []
        if ch["email"] and not mailer.configured():
            miss.append("SMTP（conf.ini [smtp]）")
        if ch["dingtalk"] and not notifier.dingtalk_configured():
            miss.append("钉钉（conf.ini [dingtalk]）")
        if len(miss) == len(want):
            return {"sent": False, "msg": "未配置 %s，跳过通知" % "、".join(miss)}
        subject, text, html = _mail_body(year, period, out_dir, files, errors, started, finished, share)
        r = notifier.notify(subject, text, html, channels=ch, scene=_SCENE)
        # 汇总成一句话：哪条通道发出去了、哪条没有。别让页面只显示其中一条的结果。
        sent = [k for k in ("email", "dingtalk") if (r.get(k) or {}).get("sent")]
        bad = ["%s(%s)" % ({"email": "邮件", "dingtalk": "钉钉"}[k], (r.get(k) or {}).get("msg", "")[:60])
               for k in want if k in r and not (r.get(k) or {}).get("sent")]
        return {"sent": bool(sent), "detail": r,
                "msg": ("已发：" + "、".join({"email": "邮件", "dingtalk": "钉钉"}[k] for k in sent) if sent else "")
                       + ("；未发出：" + "、".join(bad) if bad else "")}
    except Exception as e:
        return {"sent": False, "msg": "通知发送异常：%s" % str(e)[:200]}


def _run(year, period, orgs, out_dir, who):
    s = conf = None
    files = errors = None          # 非 None ＝ 跑完了、该发回执（中断/无报表都不发）
    started = finished = ""
    try:
        s, conf = kc.login()
        rpts = kc.fetch_fin_report_list(year, period, s, conf)
        if orgs:
            rpts = [r for r in rpts if r["org"] in set(orgs)]
        with _LOCK:
            _JOB["total"] = len(rpts)
        if not rpts:
            with _LOCK:
                _JOB["msg"] = "金蝶里没有 %d年%d期 的「财务报表」（编码 GB00001、上报状态在制）。请先在金蝶把该期报表生成出来。" % (year, period)
            return
        for r in rpts:
            with _LOCK:
                _JOB["cur"] = "%s %s" % (r["org"], r["org_name"])
            t1 = time.time()
            try:
                path, n = rx.export_one(year, period, r, out_dir, s, conf)
                with _LOCK:
                    _JOB["files"].append({"org": r["org"], "name": os.path.basename(path),
                                          "rows": n, "cur": r.get("cur"),
                                          "sec": round(time.time() - t1, 1)})
            except Exception as e:                       # 一个主体炸掉不连坐其余的
                with _LOCK:
                    _JOB["errors"].append({"org": r["org"], "name": r["org_name"],
                                           "err": str(e)[:300], "sec": round(time.time() - t1, 1)})
            with _LOCK:
                _JOB["done"] += 1
        with _LOCK:
            _JOB["msg"] = "完成：成功 %d 个、失败 %d 个 → %s" % (len(_JOB["files"]), len(_JOB["errors"]), out_dir)
            files, errors = list(_JOB["files"]), list(_JOB["errors"])
            started = _JOB["started"]
        db.audit(who, "报表导出", "%d年%d期" % (year, period),
                 "成功%d 失败%d → %s" % (len(files), len(errors), out_dir))
    except Exception as e:
        files = None
        with _LOCK:
            _JOB["msg"] = "导出中断：%s" % str(e)[:300]
    finally:
        with _LOCK:
            _JOB["running"] = False
            _JOB["cur"] = ""
            _JOB["t1"] = time.time()
            _JOB["finished"] = finished = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── 回执 ──────────────────────────────────────────────
    # **放在任务标记完成之后**：下面要等取件机把文件送到共享盘（最多 150 秒），
    # 放在 try 里会让页面上的进度条跟着一直转，明明文件早就导好了。
    if files is None:
        return
    share = _wait_share(files, finished)
    n = _send_notify(year, period, out_dir, files, errors, started, finished, share)
    with _LOCK:
        # 这期间可能已经有人起了新任务；只在还是本次的结果时才回写，别把回执盖到新任务上
        if _JOB["finished"] == finished:
            _JOB["notify"] = n
            if not n.get("sent"):
                _JOB["msg"] += "（通知未发出：%s）" % n.get("msg", "")


# ==================== 内网取件（V2.241）====================
# 云主机连不到办公室内网 NAS（NAS 是 10.10.8.x 私网，服务器在腾讯云公网），
# 于是把方向**反过来**：内网一台常开电脑定时来取，连接由内网发起、往外走。
# 好处是办公室网络**不用开任何入口**、不用 VPN、运维零审批——比给云主机开条内网口子干净得多。
# 取件脚本见 tools/pull_reports.py（跑在内网那台电脑上）。

def _pull_ok(request):
    """放行条件：带对令牌的机器，**或**页面上已登录且有导出权限的人。
    令牌校验走 core.pull_token_ok —— 与登录门中间件**同一把**，别在两处各写一遍：
    写两遍就迟早不一致，而且不一致的那一侧是安全闸。
    令牌刻意不用账号密码：脚本要长期无人值守地揣着凭据，揣一个只能下载报表的令牌，
    比揣一个能登录整个工作台的账号安全得多——泄漏的爆炸半径就是"别人也能下这些报表"。"""
    return pull_token_ok(request) or bool(_require_perm(request, "rpt_export"))


_NAME_RE = re.compile(r"^\d{4}年\d{2}期_[^\\/]{1,120}_财务报表\.xlsx$")
_MDIR_RE = re.compile(r"^\d{4}年\d{2}月$")          # 月份子目录，如 2026年07月


def _safe_path(rel):
    """把外部传进来的相对路径收敛成 out_dir 下的一个真实文件；任何越界一律返回 None。

    四道闸：① 只接受「月份目录/文件名」这一种形状（或裸文件名，兼容旧版取件机）；
    ② 两段各自按正则严格匹配导出器自己生成的格式；③ 各段取 basename 掐掉目录成分；
    ④ 最终真实路径必须仍在 out_dir 内（realpath 比对，防符号链接绕出去）。
    少任何一道，这就是个"给个 ../../etc/passwd 就能读服务器任意文件"的洞。"""
    rel = str(rel or "").replace("\\", "/")
    parts = [x for x in rel.split("/") if x not in ("", ".")]
    if len(parts) == 1:
        mdir, name = "", os.path.basename(parts[0])
    elif len(parts) == 2:
        mdir, name = os.path.basename(parts[0]), os.path.basename(parts[1])
        if not _MDIR_RE.match(mdir):
            return None
    else:
        return None
    if not _NAME_RE.match(name):
        return None
    base = _out_dir()
    if not base:
        return None
    p = os.path.realpath(os.path.join(base, mdir, name))
    root = os.path.realpath(base)
    if os.path.commonpath([p, root]) != root:
        return None
    return p if os.path.isfile(p) else None


@router.get("/api/rptexport/files")
def rptexport_files(request: Request, year: int = 0, period: int = 0):
    """列出落地目录里的导出文件（取件脚本据此决定下载哪些）。year/period 传了就只列该期。

    返回的 rel 是【月份目录/文件名】——取件机照这个相对路径在共享盘上原样建目录，
    两边结构一致，将来对账、整月归档都省事。
    mtime 一并下发：取件机靠它判断"服务器上这个文件重导过没有"，
    **不能只比大小**——同一张表改几个数字，文件大小很可能一模一样，只比大小会把更新漏掉，
    表现成"同步显示正常、共享盘上却是旧数据"，是最难发现的一类错。"""
    if not _pull_ok(request):
        return JSONResponse({"ok": False, "msg": "需要「报表导出」权限或正确的取件令牌"}, status_code=403)
    base = _out_dir()
    if not base or not os.path.isdir(base):
        return {"ok": False, "msg": "落地目录不可用：%s" % (base or "(未设置)"), "files": []}
    want_m = ("%d年%02d月" % (year, period)) if (year and period) else ""
    # 根目录散件（月份子目录是后来才有的，之前导的都平铺在根上）按【文件名】认期，
    # 不能按目录认——它压根不在月份目录里。
    # ⚠ 这里以前写的是 `and not want_m`：取件机不带参数来问，散件会被列出去、
    #   跟着同步到共享盘；而页面带 year/period 来问，散件被整个跳过 → 删除面板里看不见，
    #   于是"共享盘上有、页面上删不掉"，人只能手工去共享盘删（实际发生了）。
    #   凡是同步得走的文件，删除面板就必须看得见，两边口径不能不一样。
    want_pfx = ("%d年%02d期_" % (year, period)) if (year and period) else ""
    out = []
    for d in sorted(os.listdir(base)):
        dp = os.path.join(base, d)
        if os.path.isdir(dp):
            if not _MDIR_RE.match(d) or (want_m and d != want_m):
                continue
            names = [(d + "/" + n, os.path.join(dp, n)) for n in sorted(os.listdir(dp)) if _NAME_RE.match(n)]
        elif _NAME_RE.match(d) and (not want_pfx or d.startswith(want_pfx)):
            names = [(d, dp)]
        else:
            continue
        for rel, full in names:
            st = os.stat(full)
            out.append({"rel": rel, "name": os.path.basename(rel), "size": st.st_size,
                        "loose": "/" not in rel,          # 根目录散件，页面上标一下，免得人以为删漏了
                        "mtime": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")})
    return {"ok": True, "dir": base, "files": out}


@router.get("/api/rptexport/period-status")
def rptexport_period_status(request: Request, year: int = 0):
    """一年 12 个月各导出了几个主体——给期间选择器上的状态胶囊用。

    只数落地目录里的文件，**不连金蝶**：这里要回答的是"这个月我导过没有"，
    不是"金蝶出没出这个月的报表"。后者得翻 12 次金蝶接口，而人正卡在选期间那一步等着。"""
    if not _require_perm(request, "rpt_export"):
        return JSONResponse({"ok": False, "msg": "无「报表导出」权限"}, status_code=403)
    counts, base = {}, _out_dir()
    if year and base and os.path.isdir(base):
        root = [x for x in os.listdir(base) if _NAME_RE.match(x)]      # 根目录散件，按文件名认期
        for m in range(1, 13):
            dp = os.path.join(base, "%d年%02d月" % (year, m))
            n = len([x for x in os.listdir(dp) if _NAME_RE.match(x)]) if os.path.isdir(dp) else 0
            n += len([x for x in root if x.startswith("%d年%02d期_" % (year, m))])
            if n:
                counts[str(m)] = n
    return {"ok": True, "counts": counts,
            "statuses": {str(m): ("已导出" if counts.get(str(m)) else "未导出") for m in range(1, 13)}}


@router.get("/api/rptexport/download")
def rptexport_download(request: Request, name: str = ""):
    """下载一个导出文件。name 收【月份目录/文件名】或裸文件名，必须真在落地目录里。"""
    if not _pull_ok(request):
        return JSONResponse({"ok": False, "msg": "需要「报表导出」权限或正确的取件令牌"}, status_code=403)
    p = _safe_path(name)
    if not p:
        return JSONResponse({"ok": False, "msg": "文件不存在或文件名不合法"}, status_code=404)
    return FileResponse(p, filename=os.path.basename(p),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


_SYNC_KEY = "rpt_export_sync"     # 取件机回报的最近一次同步结果
_WANT_KEY = "rpt_export_sync_want"   # 「有人点了立即同步」的标记（取件机下次来问时消费掉）


@router.post("/api/rptexport/request-sync")
def rptexport_request_sync(request: Request):
    """点「立即同步」＝**留个话**，不是去戳那台电脑。

    服务器主动连内网取件机，仍然是被防火墙挡的方向（这一条绕不过去）。
    所以改成：这里记个标记，取件机下次来问时看到就马上干活、干完清掉。
    对点的人来说体感就是"点一下它就动了"，延迟＝取件机的轮询间隔。

    **触发者控制不了文件去哪儿**：目标目录写死在取件机自己的 ini 里，
    这个接口只能说"同步一下"，说不了"同步到我指定的地方"——故意如此。"""
    u = _require_perm(request, "rpt_export")
    if not u:
        return JSONResponse({"ok": False, "msg": "无「报表导出」权限"}, status_code=403)
    if not pull_token():
        return {"ok": False, "msg": "服务器没配取件码（conf.ini [rptexport] pull_token），取件通道未启用。"}
    db.set_setting(_WANT_KEY, {"at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                               "by": u["name"]}, u["name"])
    return {"ok": True, "msg": "已通知取件机，等它下一轮来取（通常 1 分钟内）。"}


_DEL_KEY = "rpt_export_del_queue"     # 待取件机执行的共享盘删除指令（相对路径清单）


@router.get("/api/rptexport/pending")
def rptexport_pending(request: Request):
    """取件机每轮先问这个：有没有人点过立即同步、有没有要删的文件。响应极小，可以问得勤。

    **删除是指令式、不是镜像式**：这里下发的是页面上被明确点名的那几个相对路径，
    而不是"服务器上没有的你都删掉"。镜像式的经典翻车是——落地路径配错/目录临时读不到时，
    取件机以为"服务器空了"，把共享盘上的报表全清光。指令式从根上没有这个失效模式。"""
    if not _pull_ok(request):
        return JSONResponse({"ok": False, "msg": "需要「报表导出」权限或正确的取件令牌"}, status_code=403)
    w = db.get_setting(_WANT_KEY, None)
    return {"ok": True, "pending": bool(w), "at": (w or {}).get("at", ""), "by": (w or {}).get("by", ""),
            "delete": [x["rel"] for x in (db.get_setting(_DEL_KEY, None) or [])]}


@router.post("/api/rptexport/delete")
def rptexport_delete(body: dict, request: Request):
    """删掉服务器上某期的导出文件；勾了 also_share 才连带下发共享盘删除指令。

    为什么要有这个：只有管理员进得去宝塔，普通会计删不了服务器上的文件——
    没有这个入口，"整份作废"这条路对使用者等于不存在。

    共享盘那半边**不自动做**：取件机平时只增不删，只有这里显式勾选、
    把具体文件名下发过去，它才动手（业务方定）。"""
    u = _require_perm(request, "rpt_export_del")
    if not u:
        return JSONResponse({"ok": False, "msg": "无「报表导出·删除已导出文件」权限（敏感点，默认不给）"},
                            status_code=403)
    rels = [str(x) for x in (body.get("rels") or [])]
    if not rels:
        return {"ok": False, "msg": "没选要删的文件"}
    also = bool(body.get("also_share"))
    gone, miss = [], []
    for rel in rels:
        p = _safe_path(rel)                       # 同一道路径闸：越界的一律取不到
        if not p:
            miss.append(rel)
            continue
        try:
            os.remove(p)
            gone.append(rel)
        except OSError as e:
            miss.append("%s（%s）" % (rel, e))
    # 顺手清掉空掉的月份目录，别留一堆空壳
    base = _out_dir()
    for d in list(os.listdir(base)) if base and os.path.isdir(base) else []:
        dp = os.path.join(base, d)
        if os.path.isdir(dp) and _MDIR_RE.match(d) and not os.listdir(dp):
            try:
                os.rmdir(dp)
            except OSError:
                pass
    if also and gone:
        q = db.get_setting(_DEL_KEY, None) or []
        have = {x["rel"] for x in q}
        q += [{"rel": r, "at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "by": u["name"]}
              for r in gone if r not in have]
        db.set_setting(_DEL_KEY, q, u["name"])
    db.audit(u["name"], "报表导出-删除", "删 %d 个" % len(gone),
             "%s；共享盘=%s" % ("、".join(gone)[:200], "一并删" if also else "不动"))
    msg = "已删服务器上 %d 个文件" % len(gone)
    msg += "，共享盘删除指令已下发（取件机下一轮执行，通常 1 分钟内）" if (also and gone) else "。共享盘上那份未动"
    if miss:
        msg += "；%d 个没删成：%s" % (len(miss), "、".join(miss)[:150])
    return {"ok": bool(gone), "msg": msg, "deleted": gone}


_ALIVE_SEC = 240        # 取件机每分钟一轮；4 分钟没回报就算"停了"（留 3 轮容错，别一抖就报警）


def _sync_view():
    """给页面看的取件机状态。页面只需要回答两个问题（业务方定）：
       ① 共享盘上最新的文件是什么时候的　② 它现在还在不在干活。

    「在不在干活」＝距上次回报多久，**这个差值必须在服务端算**：前端拿本机时钟去减服务器
    时间戳，两边差几秒就会出负数或跳变（V2.241 的进度耗时踩过同一个坑）。"""
    rec = db.get_setting(_SYNC_KEY, None)
    if not rec:
        return None
    out = dict(rec)
    try:
        t = datetime.datetime.strptime(rec.get("at", ""), "%Y-%m-%d %H:%M:%S")
        ago = max(0, int((datetime.datetime.now() - t).total_seconds()))
    except Exception:
        ago = None
    out["ago_sec"] = ago
    out["alive"] = (ago is not None and ago <= _ALIVE_SEC)
    return out


@router.post("/api/rptexport/sync-report")
def rptexport_sync_report(body: dict, request: Request):
    """取件机把文件放进共享盘后回报一声——**这也是内网往外发**，不需要任何入口。
    页面据此显示「已同步到共享盘 09:03 · 8 个文件」；那台电脑关机了就一直显示旧时间，
    问题当场看得见，而不是等同事说「共享盘里怎么没有」。"""
    if not _pull_ok(request):
        return JSONResponse({"ok": False, "msg": "需要「报表导出」权限或正确的取件令牌"}, status_code=403)
    rec = {"at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "host": str(body.get("host") or "")[:60],
           "dest": str(body.get("dest") or "")[:300],
           "copied": [str(x)[:160] for x in (body.get("copied") or [])][:50],
           "skipped": int(body.get("skipped") or 0),
           "deleted": [str(x)[:160] for x in (body.get("deleted") or [])][:50],
           # retry ≠ 失败：服务器端文件正在被重导覆盖，下轮自然重取。分开记，别混进 errors 吓人。
           "retry": [str(x)[:160] for x in (body.get("retry") or [])][:50],
           "errors": [str(x)[:200] for x in (body.get("errors") or [])][:20],
           # 共享盘上最新的那个文件（取件机扫目标目录得来）。业务方要的两个答案之一：
           # 「共享盘最新的文件是什么时候的」——服务器自己看不到共享盘，只能由取件机报。
           # 计数（本轮搬几个、跳过几个）是取件机的内部账，人真正问的是这个。
           "newest": str(body.get("newest") or "")[:200],
           "newest_at": str(body.get("newest_at") or "")[:30],
           "total": int(body.get("total") or 0),
           # 按期分桶：{"2026年07月": {n, copied, skipped, newest, newest_at}}。
           # 页面是按期间看的，站在某一期那一屏就该只显示那一期的数（业务方定）。
           #
           # ⚠ months_ok 必须单独记：老版取件机压根不发 months，这里会存成 {}，
           #   而 `{}` 在 JS 里是**真值**——前端拿 `!months` 判断就会把"旧取件机没报"
           #   误判成"这一期没有文件"，页面言之凿凿说共享盘上没有，其实是它不知道。
           #   （实际发生过：刚导完 8 个文件，页面却说"共享盘上还没有 4 期的文件"。）
           "months_ok": isinstance(body.get("months"), dict),
           "months": {str(k)[:20]: {"n": int((v or {}).get("n") or 0),
                                    "copied": int((v or {}).get("copied") or 0),
                                    "skipped": int((v or {}).get("skipped") or 0),
                                    "newest": str((v or {}).get("newest") or "")[:200],
                                    "newest_at": str((v or {}).get("newest_at") or "")[:30]}
                      for k, v in (body.get("months") or {}).items()
                      if isinstance(v, dict)}}
    db.set_setting(_SYNC_KEY, rec, "取件机")
    # 删除指令：**执行成功的才从队列里划掉**。没删成的留着下一轮重试——
    # 那台电脑当时可能没连上共享盘，不该把这条指令悄悄吞了。
    if rec["deleted"]:
        done = set(rec["deleted"])
        q = [x for x in (db.get_setting(_DEL_KEY, None) or []) if x["rel"] not in done]
        db.set_setting(_DEL_KEY, q or None, "取件机")
    # 消费掉「立即同步」标记——**只在取件机回报成功后清**，不是它一来问就清：
    # 中途失败要让标记留着，下一轮继续试，别让一次网络抖动把这次请求吞了。
    if not rec["errors"]:
        db.set_setting(_WANT_KEY, None, "取件机")
    return {"ok": True}


@router.get("/api/rptexport/orgs")
def rptexport_orgs(request: Request, year: int, period: int):
    """某期可导的主体清单（已按本位币去重：境外三家只出美元那套）。"""
    if not _require_perm(request, "rpt_export"):
        return JSONResponse({"ok": False, "msg": "无「报表导出」权限"}, status_code=403)
    try:
        return {"ok": True, "orgs": kc.fetch_fin_report_list(year, period)}
    except Exception as e:
        return {"ok": False, "msg": str(e)[:300], "orgs": []}


@router.get("/api/rptexport/config")
def rptexport_config(request: Request):
    """落地路径（读）。**口令值不下发前端**，只告诉它配没配。"""
    if not _require_perm(request, "rpt_export"):
        return JSONResponse({"ok": False, "msg": "无「报表导出」权限"}, status_code=403)
    cfg = db.get_setting(_CFG_KEY, None) or {}
    eff = _out_dir()
    ok, msg = _check_dir(eff) if eff else (False, "")
    row = db.notify_recipients_map().get(_SCENE) or {}
    sm = mailer.load_smtp_conf() or {}
    return {"ok": True, "out_dir": str(cfg.get("out_dir") or ""), "fallback": _conf_out_dir(),
            "effective": eff, "dir_ok": ok, "dir_msg": msg,
            "passcode_set": bool(_passcode()),
            "can_edit": bool(_require_perm(request, "rpt_export_cfg")),
            # 通知：收件人可改；SMTP 账号密码只在 conf.ini，这里只回"配没配"，绝不回显
            "emails": row.get("emails") or [],
            "mobiles": row.get("mobiles") or [],
            "smtp_configured": mailer.configured(),
            "smtp_from": sm.get("from", ""),
            "fallback_to": sm.get("to", []),
            # 钉钉：应用凭证只在 conf.ini（同 SMTP 密码的边界），这里只回"配没配"+公共名单
            "dingtalk_configured": notifier.dingtalk_configured(),
            "dt_fallback": (notifier.load_dingtalk_conf() or {}).get("mobiles", []),
            **_channels(),
            # 取件机（内网那台常开电脑）状态：令牌配没配 + 最近一次同步回执
            "pull_token_set": bool(pull_token()),
            "sync": _sync_view()}


@router.post("/api/rptexport/config")
def rptexport_config_save(body: dict, request: Request):
    """落地路径（存）：**须口令**（同通知设置那把）+ 须 rpt_export_cfg（敏感点）。
    留空＝清掉覆盖值、回落 conf.ini，这是业务方要的兜底行为，不是"没填错误"。"""
    u = _require_perm(request, "rpt_export_cfg")
    if not u:
        return JSONResponse({"ok": False, "msg": "无「报表导出·改落地路径与通知」权限"}, status_code=403)
    pc = _passcode()
    if not pc:
        return {"ok": False, "msg": "后端未设置口令（conf.ini [notify] passcode 为空），暂不能从页面改落地路径，请联系管理员配置。"}
    if str(body.get("passcode") or "").strip() != pc:
        db.audit(u["name"], "报表导出-落地路径", "口令错误，未保存")
        return JSONResponse({"ok": False, "msg": "口令错误，未保存"}, status_code=403)
    path = str(body.get("out_dir") or "").strip()
    if path:
        ok, msg = _check_dir(path)
        if not ok:
            return {"ok": False, "msg": msg}
    db.set_setting(_CFG_KEY, {"out_dir": path,
                              "email_on": bool(body.get("email_on", True)),
                              "dingtalk_on": bool(body.get("dingtalk_on", False))}, u["name"])
    emails, mobiles = _aslist(body.get("emails")), _aslist(body.get("mobiles"))
    db.save_notify_recipients(_SCENE, ";".join(mobiles), ";".join(emails), u["name"])
    db.audit(u["name"], "报表导出-设置", "更新",
             "路径=%s 邮件%d人 钉钉%d人 开关(邮件%s/钉钉%s)"
             % (path or "(清空，回落 conf.ini)", len(emails), len(mobiles),
                bool(body.get("email_on", True)), bool(body.get("dingtalk_on", False))))
    return {"ok": True, "msg": "已保存" + ("" if path else "（路径已清空，回落 conf.ini 兜底路径）"),
            "effective": _out_dir()}


def _aslist(v):
    """收件人：接受数组或"用分号/逗号/换行随便隔"的一串，去空去重、保序。"""
    if not isinstance(v, list):
        v = str(v or "").replace("；", ";").replace("，", ";").replace(",", ";").replace("\n", ";").split(";")
    return list(dict.fromkeys([str(x).strip() for x in v if str(x).strip()]))


@router.post("/api/rptexport/notify-test")
def rptexport_notify_test(body: dict, request: Request):
    """发一封测试邮件，用当前收件人。**不跑导出**——只验通道通不通。"""
    u = _require_perm(request, "rpt_export_cfg")
    if not u:
        return JSONResponse({"ok": False, "msg": "无「报表导出·改落地路径与通知」权限"}, status_code=403)
    ch = _channels()
    if not any(ch.values()):
        return {"ok": False, "msg": "两条通知渠道都关着——先打开邮件或钉钉，保存后再测。"}
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 带上取件机最近一次回报：测试信里那几行路径就是真实的，正好一并核对写没写对
    r = _send_notify(2026, 6, _out_dir() or "(未设置)",
                     [{"org": "TEST", "name": "这是一封测试邮件，未实际导出.xlsx",
                       "rows": {"科目余额": 0, "序时账簿": 0}}], [], now, now,
                     {"sync": db.get_setting(_SYNC_KEY, None), "complete": True})
    db.audit(u["name"], "报表导出-通知", "发送测试", str(r)[:200])
    return {"ok": bool(r.get("sent")), "msg": r.get("msg") or ("已发出" if r.get("sent") else "发送失败")}


@router.get("/api/rptexport/progress")
def rptexport_progress(request: Request):
    if not _require_perm(request, "rpt_export"):
        return JSONResponse({"ok": False, "msg": "无「报表导出」权限"}, status_code=403)
    with _LOCK:
        j = {k: (list(v) if isinstance(v, list) else v) for k, v in _JOB.items()}
        # 已用秒数在**服务端**算：前端拿本机时钟去减服务器时间戳，两边时钟差几秒就出负数/跳变
        if j["t0"]:
            j["elapsed"] = round((j["t1"] or time.time()) - j["t0"], 1)
        return {"ok": True, **j}


@router.post("/api/rptexport/run")
def rptexport_run(body: dict, request: Request):
    """一键导出：起后台任务立即返回，前端轮 /progress。orgs 为空＝该期全部主体。"""
    u = _require_perm(request, "rpt_export")
    if not u:
        return JSONResponse({"ok": False, "msg": "无「报表导出」权限"}, status_code=403)
    with _LOCK:
        if _JOB["running"]:
            return {"ok": False, "msg": "已有一个导出在跑（%s），等它跑完再来。" % (_JOB["cur"] or "准备中")}
    year, period = int(body.get("year") or 0), int(body.get("period") or 0)
    if not (2000 <= year <= 2100 and 1 <= period <= 12):
        return {"ok": False, "msg": "年/期不合法"}
    out_dir = _out_dir()
    ok, msg = _check_dir(out_dir)
    if not ok:
        return {"ok": False, "msg": msg}
    orgs = [str(x) for x in (body.get("orgs") or [])]
    with _LOCK:
        _JOB.update({"running": True, "done": 0, "total": 0, "cur": "准备中", "files": [], "errors": [],
                     "started": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "finished": "",
                     "msg": "", "t0": time.time(), "t1": 0.0})
    threading.Thread(target=_run, args=(year, period, orgs, out_dir, u["name"]), daemon=True).start()
    return {"ok": True, "msg": "已开始导出，进度见下方。"}
