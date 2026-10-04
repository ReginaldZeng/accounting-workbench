# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-05 | Author: Claude Opus 5.5 | Version: V2.797（数字员工办公室·接入 BP 定时任务）
# Description: 数字员工办公室 · 替 BP 工作台的两个工位报到、记账。
#   BP 工作台有自己的一套定时调度（它的 app/scheduler.py）：驾驶舱值守（早 / 下午）、业绩快报（日 / 周 / 月 / 季）。
#   这些活在 BP 的进程里干，核算这边看不见。本模块每分钟去 BP 读一次它「系统设置 › 定时任务」页用的那个只读接口
#   （GET /api/ops/schedule：任务清单 + 下次几点 + 上次跑的结果 + 调度线程在不在），然后：
#     · 调度在转 → 替工位报到，把每条任务的「下次几点」写进排队表；
#     · 某条任务有了新的一次运行结果 → 记一笔干活记录（只写任务名和推了几个群，不带群名、不带业务数字）；
#     · 调度没开 / 这类任务全停用 → 报成「没上岗」并写明原因；
#     · BP 连不上 → 不报到。连续十分钟没人替它报到，值班表上就显示「停了」。
#   两个工位：bp_sentinel 驾驶舱值守员（kind=sentinel 的任务）、bp_flash 业绩快报员（kind=flash 的任务）。
#   **不改 BP 的任何代码**，只读、不触发、不改它的任务时间。
#   怎么过 BP 的门：BP 没有登录，靠请求头 X-BP-User / X-BP-Perms 认身份（平时由 Nginx 替登录用户注入，权限码本来就是核算签发的）；
#   它只信本机回环来的请求。这里从本机回环去读，带一个服务身份和读这一个接口所需的那一个权限码。
#   第一次连上 BP 时只记下「它已经跑到哪了」，不把以前的运行补记成刚干完。
#   全部吞异常：读不到绝不影响核算自己的活。
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime

import db
import worker_store

EVERY_S = 60
SEEN_KEY = "office_bp_seen"            # {任务 id: "计划时点|结果"}——上次看到它跑到哪了
SERVICE_USER = "数字员工办公室"
NEED_PERM = "bp:board:appSettings"     # BP 的「系统设置」板块码：/api/ops/* 整个路由挂在它下面
DESK_OF = {"sentinel": "bp_sentinel", "flash": "bp_flash"}
_FLASH_CN = {"day": "日报", "week": "周报", "month": "月报", "quarter": "季报"}
_DOW = "一二三四五六日"
_STATE = {"lastAt": "", "lastError": "", "ok": False}


def fetch(timeout=4):
    """读 BP 的定时任务清单。→ dict；读不到抛异常。"""
    req = urllib.request.Request(db.BP_API_BASE.rstrip("/") + "/api/ops/schedule", headers={
        "X-BP-User": urllib.parse.quote(SERVICE_USER, safe=""), "X-BP-Perms": NEED_PERM})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _cadence(job):
    """把 BP 任务的排期说成人话：工作日 09:00 / 每天 14:00 / 每周一 09:10 / 每月 1 日 09:30 / 1、4、7、10 月 1 日 09:30。"""
    w, t = job.get("when") or {}, str(job.get("time") or "")
    if w.get("type") == "monthly":
        months = w.get("months") or []
        head = "每月" if len(months) >= 12 or not months else "、".join(str(m) for m in months) + " 月"
        return "%s %s 日 %s" % (head, w.get("dom") or 1, t)
    dows = sorted(w.get("dows") or [])
    if dows == [1, 2, 3, 4, 5, 6, 7]:
        return "每天 " + t
    if dows == [1, 2, 3, 4, 5]:
        return "工作日 " + t
    return "每周" + "、".join(_DOW[d - 1] for d in dows if 1 <= d <= 7) + " " + t


def _job_name(job):
    if job.get("kind") == "flash":
        return "业绩快报 · " + _FLASH_CN.get(job.get("flashKind"), "推送")
    return str(job.get("name") or "驾驶舱值守")[:40]


def _summary(job, run):
    """一次运行 → (件数, 成没成, 给大屏看的一句话, 出错原文)。一句话里不带群名和业务数字。"""
    st, note = str(run.get("status") or ""), str(run.get("note") or "")
    name = _job_name(job)
    if job.get("kind") == "flash":
        m = re.search(r"(\d+)/(\d+) 个目标已推", note)
        if m:
            sent, total = int(m.group(1)), int(m.group(2))
            return sent, st != "failed", "%s已推 %d/%d 个群" % (name, sent, total), (note if st == "failed" else "")
        if "没有订阅" in note:
            return 0, True, "%s到点了，没有订阅的群，没推" % name, ""
        return 0, st != "failed", name + ("已推" if st != "failed" else "没推成"), (note if st == "failed" else "")
    return 1, st != "failed", name + ("已开跑" if st != "failed" else "没起成"), (note if st == "failed" else "")


def sync(data, seen):
    """把 BP 的一份任务清单落到办公室。seen＝上次看到的进度（None＝第一次连上：只记进度、不补记）。→ 新的 seen。"""
    sch = data.get("scheduler") or {}
    jobs, runs = data.get("jobs") or [], data.get("runs") or {}
    first = seen is None
    new_seen = dict(seen or {})
    for kind, desk in DESK_OF.items():
        mine = [j for j in jobs if j.get("kind") == kind]
        on = [j for j in mine if j.get("enabled")]
        if not sch.get("envOn"):
            worker_store.beat(desk, off="BP 工作台的定时调度没开")
            worker_store.set_queue(desk, [])
            continue
        if not sch.get("running"):
            continue                                        # 调度线程没在转：不替它报到（十分钟后显示「停了」）
        if not on:
            worker_store.beat(desk, off="这类定时任务在 BP 里都停用了")
            worker_store.set_queue(desk, [])
            continue
        worker_store.beat(desk)
        worker_store.set_queue(desk, [{"job": j["id"], "ts": str(j["nextDue"]) + ":00", "name": _job_name(j), "cadence": _cadence(j)}
                                      for j in on if j.get("nextDue")])
        for j in mine:
            run = runs.get(j.get("id")) or {}
            st = str(run.get("status") or "")
            if not run.get("lastKey") or st == "running":   # 没跑过 / 还在推：等它出结果
                continue
            mark = "%s|%s" % (run["lastKey"], st)
            if new_seen.get(j["id"]) == mark:
                continue
            new_seen[j["id"]] = mark
            if first:
                continue
            n, ok, text, err = _summary(j, run)
            worker_store.record(desk, n=n, ok=ok, summary=text, error=err, trigger="手动" if run.get("manual") else "定时")
    return new_seen


def tick():
    try:
        data = fetch()
    except Exception as e:
        _STATE.update(ok=False, lastError=str(e)[:200], lastAt=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        return False
    seen = db.get_setting(SEEN_KEY, None)
    new_seen = sync(data, seen if isinstance(seen, dict) else None)
    if new_seen != seen:
        db.set_setting(SEEN_KEY, new_seen, "系统")
    _STATE.update(ok=True, lastError="", lastAt=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return True


def _loop():
    time.sleep(45)                      # 开机先让别的线程起来
    while True:
        try:
            tick()
        except Exception as e:          # 绝不让线程死掉
            _STATE.update(ok=False, lastError=str(e)[:200])
        time.sleep(EVERY_S)


def start():
    threading.Thread(target=_loop, daemon=True, name="office-bp-bridge").start()
