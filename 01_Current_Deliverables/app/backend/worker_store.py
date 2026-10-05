# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-04 | Author: Claude Opus 5.5 | Version: V2.796（数字员工办公室）
# Description: 数字员工办公室 · 工位注册表 + 干活记录 + 心跳（《数字员工办公室 需求确认书 v1.0》）。
#   一个工位＝一件自带触发器、无人值守的重复劳动（沿《AI 合同预审》确认书 D2：按活分不按人分，不用真人姓名头像）。
#   · beat(desk)：后台线程每转一圈报一次到——「还活着」。超过该工位的容忍时长没报到＝停了（线程悄悄死了也看得出来）。
#     没启用/没配置的传 off=原因 → 值班表上显示「没上岗」并写明为什么，不和「停了」混为一谈。
#   · record(desk, n, ok, summary, error)：真干了活（或出了错）才记一笔。空转不记，免得刷屏。
#     同一个错连着出只留一条、刷新时间——二十分钟一轮的线程连不上钉钉，不该一天记七十条。
#   · summary 只许写件数，不写供应商/客户/金额——值班表要放在闲置电脑上常开，路过的人都看得见。error 原文只给管理员看。
#   · refs＝这一笔干的是哪几张单（钉钉审批编号这类单号，最多留 6 个）。业务方 2026-10-04 定：大屏「刚干完的活」要看得见单号。
#     单号本身不带供应商、客户、金额；除了单号别的不许往里塞。
#   · 不记工时（业务方 2026-10-04 定：暂时不用）。
#   · beat(desk, next_in=秒)：报到时顺带说「下一轮还有多久」→ 值班表带出 nextTs，大屏据此排「定时任务排队」。
#     单独放一张表 worker_next：新表 create_all 会自己建，不用去改已经上线的 worker_beat。
#     一个工位可以有好几条定时任务（BP 的业绩快报员有日报 / 周报 / 月报 / 季报四条）→ set_queue(desk, [...]) 一次写一组。
#   几张新表自带 MetaData，导入时 create_all（只建不改既有表）。beat/record 全部吞异常：记不上绝不拦工位本身的活。
#   不 import 任何 router；取件机这类「状态在别处」的工位由 app.py 通过 PROVIDERS 注册进来。
from datetime import datetime, timedelta

from sqlalchemy import Column, Integer, MetaData, String, Table, Text, delete, func, insert, inspect, select, text, update

import db

_md = MetaData()
worker_run = Table(
    "worker_run", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("desk", String(40)),
    Column("ts", String(20)),
    Column("ok", Integer),                   # 1 干成了 / 0 出错或被拦下
    Column("n", Integer),                    # 这一轮干完几件（本月件数按它加）
    Column("summary", String(200)),          # 只写件数，脱敏（值班表、大屏都显示）
    Column("error", Text),                   # 出错原文（只给管理员看）
    Column("trigger", String(20)),
    Column("refs", String(400)),             # 这一笔干的是哪几张单：单号用 | 隔开，最多 6 个（大屏「刚干完的活」显示）
)
worker_beat = Table(
    "worker_beat", _md,
    Column("desk", String(40), primary_key=True),
    Column("ts", String(20)),                # 上次报到
    Column("off", String(200)),              # 非空＝没上岗的原因（开关没开 / 没配置 / 本机测试库）
)
worker_next = Table(
    "worker_next", _md,
    Column("desk", String(40), primary_key=True),
    Column("job", String(40), primary_key=True),   # 这个工位的哪条任务；只有一条的工位填 ""
    Column("ts", String(20)),                # 下一轮预计什么时候跑（任务自己报的）
    Column("name", String(60)),              # 任务叫什么（空＝就用工位名）
    Column("cadence", String(40)),           # 多久一轮（空＝用工位注册表里的）
)
_md.create_all(db._engine)
try:          # refs 是后加的列：表已经建过的库（本机测试库）补上这一列；新库 create_all 时已经带了
    if "refs" not in {c["name"] for c in inspect(db._engine).get_columns("worker_run")}:
        with db._engine.begin() as _c:
            _c.execute(text("ALTER TABLE worker_run ADD COLUMN refs VARCHAR(400)"))
except Exception:
    pass

_FMT = "%Y-%m-%d %H:%M:%S"

# 工位注册表（顺序即值班表顺序）。stale_min＝多久没报到算「停了」（按该线程转一圈的时长留三倍余量）；
# todo_scene＝它交出去的活落在首页待办区的哪个环节（有的话，值班表上显示「还压着几件没人接」）。
# zone＝坐在办公室哪一组（大屏按组分区摆工位）。
# sched＝按钟点跑的定时任务（每天几点干一次）。没有这个标记的是「巡检」——每隔二三十分钟去钉钉 / 金蝶看一眼有没有新活，
#   有才干、没有就空转；大屏的「定时任务排队」只排 sched 的，巡检的不算定时任务（业务方 2026-10-04 指出）。
DESKS = [
    {"key": "fx", "zone": "总账组", "name": "汇率录入员", "what": "抓人行中间价 → 建汇率 → 过闸门 → 写金蝶并提交", "cadence": "每天 14:00",
     "stale_min": 50 * 60, "handoff": "汇率审核人", "todo_scene": "fx_audit", "unit": "条", "sched": True},
    {"key": "inv_intake", "zone": "发票组", "name": "发票接收员", "what": "扫钉钉审批单 → 建票夹 → 拉附件识别 → 票齐自动送审", "cadence": "每 20 分钟",
     "stale_min": 30, "handoff": "发票审核人", "unit": "张单", "ref": "钉钉单号"},
    {"key": "inv_remind", "zone": "发票组", "name": "催票员", "what": "后补单到期 → 钉钉催申请人交票", "cadence": "每天一轮",
     "stale_min": 40, "handoff": "申请人", "unit": "张", "sched": True},
    {"key": "inv_voucher", "zone": "发票组", "name": "发票凭证同步员", "what": "回查金蝶 → 把发票标成「已做账·凭证号」", "cadence": "每 20 分钟",
     "stale_min": 70, "handoff": "不用交接", "unit": "张"},
    {"key": "bom_intake", "zone": "成本组", "name": "BOM 立项员", "what": "钉钉单走到成本核算节点 → 自动立项进待办", "cadence": "定时扫描",
     "stale_min": 180, "handoff": "成本会计初审", "unit": "单", "ref": "钉钉单号"},
    {"key": "bom_final", "zone": "成本组", "name": "BOM 终审同步员", "what": "财务经理在 OA 点同意 / 退回 → 工作台同步终审", "cadence": "定时扫描",
     "stale_min": 180, "handoff": "不用交接", "unit": "单", "ref": "钉钉单号"},
    {"key": "payreq", "zone": "应付组", "name": "物流请款单接收员", "what": "扫钉钉公对公付款申请 → 认承运商和账期 → 发票进票夹", "cadence": "每 20 分钟",
     "stale_min": 70, "handoff": "物流复核人", "unit": "张单", "ref": "钉钉单号"},
    {"key": "voucher_auto", "zone": "应付组", "name": "付款做账员", "what": "金蝶有付款单、票齐、计提对平 → 审核付款单 → 补凭证分录并提交", "cadence": "每 20 分钟",
     "stale_min": 70, "handoff": "付款凭证审核人", "todo_scene": "logi_voucher_audit", "unit": "张", "ref": "钉钉单号"},
    {"key": "todo_check", "zone": "总账组", "name": "待办核对员", "what": "去金蝶看审了没 → 审了就把首页待办销掉", "cadence": "每 20 分钟",
     "stale_min": 70, "handoff": "不用交接", "unit": "条"},
    # 下面两个的活在 BP 工作台里干（它自己的定时调度），核算这边每分钟去看一眼它的任务清单和运行记录，替它报到、记账
    #（office_bp_bridge.py）。BP 连不上 → 没人替它报到 → 十分钟后值班表上显示「停了」。
    {"key": "bp_sentinel", "zone": "经营分析组", "name": "驾驶舱值守员", "what": "从金蝶刷新驾驶舱 → 体检基础资料待办 → 有待办就钉钉推给负责人",
     "cadence": "工作日 09:00、每天 14:00", "stale_min": 10, "handoff": "财务 BP", "unit": "次", "sched": True},
    {"key": "bp_flash", "zone": "经营分析组", "name": "业绩快报员", "what": "按日 / 周 / 月 / 季把业绩快报推到钉钉群",
     "cadence": "日报 / 周报 / 月报 / 季报", "stale_min": 10, "handoff": "各业务群", "unit": "个群", "sched": True},
    {"key": "bp_ads", "zone": "经营分析组", "name": "投放数据员", "what": "每天拉千川数据 → 重建投放 ROI 快报 → 推给订阅的群和人；白天每半小时看一眼千川余额和爆量",
     "cadence": "每天 08:00 起", "stale_min": 10, "handoff": "订阅的群和人", "unit": "次", "sched": True},
    {"key": "contract", "zone": "法务组", "name": "合同预审员", "what": "钉钉合同审批发起 → 预审 → 意见贴回评论", "cadence": "审批事件",
     "stale_min": None, "handoff": "法务、财务", "unit": "份", "static_off": "还没上岗：等法务签字"},
]
_BY_KEY = {d["key"]: d for d in DESKS}
PROVIDERS = []          # 外部工位（取件机）：app.py 注册 callable → [{key,name,what,cadence,handoff,status,statusText,lastAt,lastText}]


def _now():
    return datetime.now().strftime(_FMT)


# ---------------- 报到 / 记一笔（全部吞异常）----------------
def beat(desk, off="", next_in=None):
    """报到。next_in＝离下一轮还有几秒（任务自己最清楚）；不传就不动上次报的。"""
    try:
        vals = {"ts": _now(), "off": str(off or "")[:200]}
        with db._engine.begin() as c:
            if c.execute(select(worker_beat.c.desk).where(worker_beat.c.desk == desk)).first():
                c.execute(update(worker_beat).where(worker_beat.c.desk == desk).values(**vals))
            else:
                c.execute(insert(worker_beat).values(desk=desk, **vals))
    except Exception:
        pass
    if next_in is None:
        return
    try:
        nxt = (datetime.now() + timedelta(seconds=max(0, float(next_in)))).strftime(_FMT)
    except Exception:
        return
    set_queue(desk, [{"job": "", "ts": nxt}])


def set_queue(desk, items):
    """把这个工位接下来要跑的任务整组换掉。items＝[{job, ts(年-月-日 时:分:秒), name, cadence, patrol}]；传空列表＝清空。
    patrol=True＝这条是巡检型的（隔一阵看一眼，不是几点干什么）：排队表里不算定时任务。存的时候在任务名前加个 ~ 记住它，
    不为这一个标记去改已经上线的表。"""
    try:
        rows, seen = [], set()
        for it in items or []:
            job, ts = (("~" if it.get("patrol") else "") + str(it.get("job") or ""))[:40], str(it.get("ts") or "")[:19]
            if not ts or job in seen:
                continue
            datetime.strptime(ts, _FMT)                       # 格式不对的不收
            seen.add(job)
            rows.append({"desk": desk, "job": job, "ts": ts, "name": str(it.get("name") or "")[:60], "cadence": str(it.get("cadence") or "")[:40]})
        with db._engine.begin() as c:
            c.execute(delete(worker_next).where(worker_next.c.desk == desk))
            if rows:
                c.execute(insert(worker_next), rows)
    except Exception:
        pass


def _refs_s(refs):
    """单号列表 → 存库的字符串：去空去重、每个最多 40 字、最多留 6 个。"""
    out = []
    for r in (refs or []):
        r = str(r or "").strip().replace("|", "")[:40]
        if r and r not in out:
            out.append(r)
    return "|".join(out[:6])


def _refs_l(s):
    return [x for x in str(s or "").split("|") if x]


def record(desk, n=0, ok=True, summary="", error="", trigger="定时", refs=None):
    """记一笔干活记录。同一个工位连着出同一个错 → 不新增，只把上一条的时间刷到现在。
    refs＝这一笔干的是哪几张单的单号（列表，只放单号）。"""
    try:
        err = str(error or "")[:2000]
        refs_s = _refs_s(refs) if not isinstance(refs, str) else _refs_s([refs])
        with db._engine.begin() as c:
            if not ok:
                last = c.execute(select(worker_run.c.id, worker_run.c.ok, worker_run.c.error).where(worker_run.c.desk == desk)
                                 .order_by(worker_run.c.id.desc()).limit(1)).first()
                if last and not last[1] and (last[2] or "") == err:
                    c.execute(update(worker_run).where(worker_run.c.id == last[0]).values(ts=_now()))
                    return
            c.execute(insert(worker_run).values(desk=desk, ts=_now(), ok=1 if ok else 0, n=int(n or 0),
                                                summary=str(summary or "")[:200], error=err, trigger=str(trigger or "")[:20], refs=refs_s))
    except Exception:
        pass


# ---------------- 值班表 ----------------
def _ago_text(mins):
    if mins < 60:
        return "%d 分钟" % max(1, mins)
    if mins < 48 * 60:
        return "%d 小时" % (mins // 60)
    return "%d 天" % (mins // 1440)


def _ago_min(ts, now):
    try:
        return max(0, int((now - datetime.strptime(ts, _FMT)).total_seconds() // 60))
    except Exception:
        return None


def _short(ts, now):
    """给人看的时间：今天 15:20 / 10-07 11:42。"""
    try:
        t = datetime.strptime(ts, _FMT)
    except Exception:
        return ""
    return ("今天 " + t.strftime("%H:%M")) if t.date() == now.date() else t.strftime("%m-%d %H:%M")


def roster():
    """→ {desks: [...], kpi: {...}, since, asOf}。全是件数和状态，不含任何业务明细——可以直接上大屏。"""
    now = datetime.now()
    month0 = now.strftime("%Y-%m-01 00:00:00")
    with db._engine.connect() as c:
        beats = {r["desk"]: dict(r) for r in c.execute(select(worker_beat)).mappings().all()}
        try:
            nexts = {}
            for r in c.execute(select(worker_next).order_by(worker_next.c.ts)).mappings().all():
                nexts.setdefault(r["desk"], []).append(dict(r))
        except Exception:
            nexts = {}
        month = {r[0]: (int(r[1] or 0), int(r[2] or 0)) for r in c.execute(
            select(worker_run.c.desk, func.sum(worker_run.c.n), func.count()).where(worker_run.c.ts >= month0)
            .group_by(worker_run.c.desk)).all()}
        last, bad_job = {}, {}
        for d in DESKS:
            rs = c.execute(select(worker_run).where(worker_run.c.desk == d["key"]).order_by(worker_run.c.id.desc()).limit(40)).mappings().all()
            if rs:
                last[d["key"]] = dict(rs[0])
                # 一个工位管好几条任务的（trigger 里用 # 带了任务名，如「定时#adroi-daily」）：每条任务各看自己最近一次，
                # 有一条最近一次没干成，这个工位就算「有出错」——不能因为后面另一条任务干成了就把前一条的错盖掉。
                seen_job = set()
                for r in rs:
                    job = str(r["trigger"] or "").partition("#")[2]
                    if job and job not in seen_job:
                        seen_job.add(job)
                        if not r["ok"]:
                            bad_job[d["key"]] = True
        since = c.execute(select(func.min(worker_run.c.ts))).scalar() or ""
        today_n = int(c.execute(select(func.sum(worker_run.c.n)).where(worker_run.c.ts >= now.strftime("%Y-%m-%d 00:00:00"))).scalar() or 0)
        today_runs = int(c.execute(select(func.count()).select_from(worker_run).where(worker_run.c.ts >= now.strftime("%Y-%m-%d 00:00:00"))).scalar() or 0)
        recent = c.execute(select(worker_run).order_by(worker_run.c.ts.desc(), worker_run.c.id.desc()).limit(14)).mappings().all()
    waiting = _waiting_by_scene()
    desks, queue = [], []
    for d in DESKS:
        b, lr = beats.get(d["key"]), last.get(d["key"])
        if d.get("static_off"):
            status, text = "off", d["static_off"]
        elif not b:
            status, text = "off", "还没上岗：后台任务还没报到过"
        elif b.get("off"):
            status, text = "off", "没上岗：" + b["off"]
        else:
            try:
                idle = int((now - datetime.strptime(b["ts"], _FMT)).total_seconds() // 60)
            except Exception:
                idle = 0
            if d["stale_min"] and idle > d["stale_min"]:
                status, text = "down", "%s没动静" % _ago_text(idle)
            elif (lr and not lr["ok"]) or bad_job.get(d["key"]):
                status, text = "err", "上一轮出错了"
            else:
                status, text = "ok", "正常"
        n_month, runs_month = month.get(d["key"], (0, 0))
        w = waiting.get(d.get("todo_scene") or "", None)
        desks.append({"key": d["key"], "name": d["name"], "zone": d["zone"], "kind": "desk",
                      "what": d["what"], "cadence": d["cadence"], "handoff": d["handoff"],
                      "unit": d["unit"], "status": status, "statusText": text,
                      "lastAt": _short(lr["ts"], now) if lr else "", "lastText": (lr["summary"] if lr else "") or ("出错了" if lr and not lr["ok"] else ""),
                      "lastAgoMin": _ago_min(lr["ts"], now) if lr else None,      # 上次干活是几分钟前（大屏：刚干完的在打字，其余的歇着）
                      # 大屏拿这两个原始时间戳比「两次刷新之间有没有变」：干活记录变了＝来了新活（弹派工单）；只有报到变了＝巡了一圈没新活
                      "lastTs": lr["ts"] if lr else "", "beatTs": (b or {}).get("ts") or "",
                      # 下一轮预计什么时候跑（任务自己报的；没上岗、停了的不排队）→ 大屏「定时任务排队」
                      "nextTs": (nexts.get(d["key"]) or [{}])[0].get("ts", "") if status in ("ok", "err") else "", "sched": bool(d.get("sched")),
                      "lastOk": bool(lr["ok"]) if lr else True, "monthN": n_month, "monthRuns": runs_month,
                      "waiting": (w or {}).get("n"), "waitingLate": (w or {}).get("late", 0), "detail": True})
        if status in ("ok", "err"):          # 定时任务排队：在岗的才排；一个工位可以有好几条
            for r in nexts.get(d["key"]) or []:
                queue.append({"key": d["key"], "desk": d["name"], "name": r.get("name") or d["name"], "cadence": r.get("cadence") or d["cadence"],
                              "nextTs": r["ts"], "sched": bool(d.get("sched")) and not str(r.get("job") or "").startswith("~")})
    queue.sort(key=lambda q: q["nextTs"])
    for p in PROVIDERS:
        try:
            for x in p() or []:
                desks.append({"unit": "", "monthN": None, "monthRuns": None, "waiting": None, "waitingLate": 0,
                              "lastOk": True, "detail": False, "zone": "取件间", "kind": "machine", **x})
        except Exception:
            pass
    on = [x for x in desks if x["status"] in ("ok", "err")]
    # 刚干完的活（大屏右栏用）：工位名 + 脱敏的一句话 + 单号 + 成没成，不带出错原文
    feed = [{"at": _short(r["ts"], now), "desk": _BY_KEY[r["desk"]]["name"] if r["desk"] in _BY_KEY else r["desk"],
             "key": r["desk"], "text": r["summary"] or ("出错了" if not r["ok"] else ""), "ok": bool(r["ok"]),
             "n": r["n"] or 0, "refs": _refs_l(r.get("refs")), "refLabel": (_BY_KEY.get(r["desk"]) or {}).get("ref") or "单号",
             "agoMin": _ago_min(r["ts"], now)} for r in recent]
    return {"desks": desks, "feed": feed, "queue": queue, "asOf": now.strftime("%H:%M"), "nowTs": now.strftime(_FMT), "since": since[:10],
            "kpi": {"on": len(on), "total": len(desks), "todayN": today_n, "down": sum(1 for x in desks if x["status"] == "down"),
                    "off": sum(1 for x in desks if x["status"] == "off"),
                    "monthN": sum(x["monthN"] or 0 for x in desks),
                    # 干活次数（一笔干活记录算一次）：各工位的件数单位不一样（张、条、次、个群），加在一起没意义，大屏汇总用次数
                    "todayRuns": today_runs, "monthRuns": sum(x["monthRuns"] or 0 for x in desks),
                    "waiting": sum(x["waiting"] or 0 for x in desks),
                    "err": sum(1 for x in desks if x["status"] == "err")}}


def _waiting_by_scene():
    """各环节首页待办里还没办完的条数（工位交出去、还压着没人接的）。待办模块不在也不报错。"""
    try:
        import todo_store as ts
        now, out = datetime.now(), {}
        for it in ts.list_open():
            o = out.setdefault(it["scene"], {"n": 0, "late": 0})
            o["n"] += 1
            _, late = ts._age(it.get("created_ts") or "", now)
            o["late"] += 1 if late else 0
        return out
    except Exception:
        return {}


def runs(desk, limit=60, show_error=False):
    """某个工位最近的干活记录。error 原文只在 show_error 时给（管理员）；否则只说「出错了」。"""
    if desk not in _BY_KEY:
        return None
    now = datetime.now()
    with db._engine.connect() as c:
        rows = c.execute(select(worker_run).where(worker_run.c.desk == desk).order_by(worker_run.c.id.desc()).limit(int(limit))).mappings().all()
        b = c.execute(select(worker_beat).where(worker_beat.c.desk == desk)).mappings().first()
    return {"desk": {k: _BY_KEY[desk][k] for k in ("key", "name", "what", "cadence", "handoff", "unit")},
            "beatAt": _short(b["ts"], now) if b else "", "off": (b or {}).get("off") or "",
            "runs": [{"at": _short(r["ts"], now), "ok": bool(r["ok"]), "n": r["n"] or 0, "summary": r["summary"] or "",
                      "error": (r["error"] or "") if show_error else ("" if r["ok"] else "出错原因只有管理员能看"),
                      "trigger": str(r["trigger"] or "").partition("#")[0], "refs": _refs_l(r.get("refs"))} for r in rows]}


def purge(keep_days=400):
    """干活记录留 400 天（够看一年同比），再早的清掉。由 office 路由启动时顺手调一次。"""
    try:
        cut = (datetime.now() - timedelta(days=keep_days)).strftime(_FMT)
        with db._engine.begin() as c:
            c.execute(worker_run.delete().where(worker_run.c.ts < cut))
    except Exception:
        pass
