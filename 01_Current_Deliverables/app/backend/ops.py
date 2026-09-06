# -*- coding: utf-8 -*-
"""
[Change Log]
Date: 2026-09-06 / Author: Claude / Version: V2.489
Description: 运维观测埋点（并发 / 日志 / 慢接口 / 错误 / 谁在用哪块）。移植自 BP 工作台 app/ops.py，
  按核算工作台的"会话 Cookie 身份"与"板块路由前缀"改造。本文件**自包含**，不 import 任何业务模块
  （db/core 都不碰），身份由外层中间件塞进 scope，故搬到别的基线只需拷两个文件 + 在 app.py 加几行。

【为什么要有它】
  核算工作台此前没有任何请求日志：谁在用、几个人同时在用、哪个接口把进程占住了，全靠猜。
  出问题只能靠推理定位（单进程 uvicorn + CPU 重活占住 GIL），因为没有"卡死那一刻正在跑什么"的现场。
  本模块补的就是这个现场——也顺带回答业务侧最想知道的"谁在用哪个板块"。

【设计要点（都是有代价才这么定的，照搬 BP 的实战教训）】
  1) **纯 ASGI 中间件**，不用 BaseHTTPMiddleware —— 后者每个请求多套一层任务/队列，
     还会把流式响应（报表/CSV 下载）缓冲住；这里只要拿到 status 和耗时，裸 ASGI 更轻也更不容易出事。
  2) **请求线程不碰磁盘**：请求结束只往内存 Queue 塞一条 dict，由后台 writer 线程批量落库。
  3) **独立库 ops_log.db**，不写业务库：日志行数会远超业务表，混在一起既撑大业务库备份，
     又给业务库徒增写锁竞争。开 WAL + busy_timeout，多 worker（多进程）各自写同一个文件也安全。
  4) **记 pid**：将来开多 worker 时，实时探针只看得见自己进程的在飞请求（内存态各进程独立），
     历史聚合是全进程的（都写同一个库）。
  5) **路径归一化**：/api/bom/entry/059207 → /api/bom/entry/{id}，否则慢接口榜被 ID 打散没法看。

【身份口径（与 BP 的差别）】
  BP 走 Nginx 反代、在头里塞 X-BP-User；核算工作台有自己的登录态（会话 Cookie）。
  故身份不在本文件解析——由 app._auth_gate 解析会话后塞进 `request.state.ops_user`（= scope["state"]），
  本中间件在请求**结束**时（内层已跑完）从 scope 里读回来。这样本文件零业务依赖，也不重复查库。
  未透传身份（未登录/静态资源/登录接口本身）→ user 为空，按来源 IP 再分"系统·自检 / (未透传身份)"。

【口径说明（看图时别理解错）】
  - `inflight` = 该请求**开始那一刻**服务端正在处理的请求数（含它自己）。
    "每小时峰值并发" = 该小时内所有请求 inflight 的最大值——是真实观测到的并发峰值，不是估算。
  - `ms` = 中间件测到的服务端处理耗时，**不含**客户端网络时间，也不含 Nginx 排队时间。
  - 只统计到达本服务的请求；被 Nginx 挡掉的、纯静态资源的，这里不一定看得全。
"""
import os
import re
import queue
import sqlite3
import threading
import time
from datetime import datetime

# ── 开关与参数（全部可用环境变量覆盖，出问题能一键关掉）──────────────────────────
OPS_ENABLED = os.getenv("WB_OPS_ENABLED", "1") != "0"
OPS_RETAIN_DAYS = int(os.getenv("WB_OPS_RETAIN_DAYS", "30"))     # 超期日志自动清理
OPS_RECENT_MAX = 200                                             # 内存里保留的"最近请求"条数
_QUEUE_MAX = 20000                                               # 队列上限；满了丢弃并计数（宁可丢日志，不能拖垮业务）

# 默认不记录的路径：ops 自己（页面几秒轮询一次，记下来就是自噪声）+ 健康检查（监控在刷）
_EXCLUDE_DEFAULT = "/api/ops,/api/health"
EXCLUDE_PREFIXES = tuple(
    p.strip() for p in os.getenv("WB_OPS_EXCLUDE", _EXCLUDE_DEFAULT).split(",") if p.strip()
)

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
OPS_DB_PATH = os.getenv("WB_OPS_DB", os.path.join(_BACKEND_DIR, "ops_log.db"))

# scope["state"] 里放身份的键名——与 app._auth_gate 约定一致（改这里要一起改 app.py）
STATE_USER_KEY = "ops_user"

# ── 板块归属：按路径前缀映射到业务板块/模块名（"谁用了哪个板块"靠它）───────────────
# 覆盖核算工作台当前全部 router 前缀；未命中→"其他"。改菜单不用回来改这里（只影响归类展示）。
_BOARD_MAP = {
    "/api/reconcile": "银行对账",
    "/api/bank-import": "银行对账",
    "/api/balance-adjust": "银行对账",
    "/api/channel-adjust": "银行对账",
    "/api/operators": "银行对账",
    "/api/wealth-recon": "理财对账",
    "/api/fund-dashboard": "资金看板",
    "/api/account-ledger": "账户台账",
    "/api/fxrate": "汇率录入",
    "/api/subject-balance": "科目余额",
    "/api/report-dashboard": "报表仪表盘",
    "/api/report": "报表",
    "/api/rptexport": "报表导出",
    "/api/logistics-accrual": "物流计提",
    "/api/logistics-recon": "物流对账",
    "/api/cost-ledger": "存货台账",
    "/api/bomcost": "BOM报价审核",
    "/api/bom": "BOM报价审核",
    "/api/tempatt": "临时工考勤",
    "/api/ec": "电商对账",
    "/api/archive": "凭证归档",
    "/api/period-statuses": "月结·期间",
    "/api/period": "月结·期间",
    "/api/orgs": "基础数据",
    "/api/data-sources": "基础数据",
    "/api/kingdee": "基础数据",
    "/api/config": "系统设置",
    "/api/nav-sections": "账号·权限",
    "/api/nav-modules": "账号·权限",
    "/api/users": "账号·权限",
    "/api/perms": "账号·权限",
    "/api/portal": "账号·权限",
    "/api/change-pwd": "账号·权限",
    "/api/bp-perm-drift": "账号·权限",
    "/api/bp-authz": "账号·权限",
    "/api/llm-hub": "模型配置",
    "/api/llm": "模型配置",
    "/api/me": "身份·登录",
    "/api/login": "身份·登录",
    "/api/logout": "身份·登录",
    "/api/ops": "运维日志",
    "/api/health": "健康检查",
}
# 长前缀优先：/api/bomcost 必须先于 /api/bom 命中、/api/report-dashboard 先于 /api/report
_BOARD_PREFIXES = sorted(_BOARD_MAP.items(), key=lambda kv: -len(kv[0]))

_WRITE_METHODS = ("POST", "PUT", "DELETE", "PATCH")

# ── 在线用户分类（对齐 BP）────────────────────────────────────────────────────────
#   无身份的请求里，来自本机回环的是服务器自己的活儿（定时跑批、本机直连打开页面…），不是人 → 标「系统·自检」；
#   其余无身份才叫「(未透传身份)」。两者都不计在线人数。
LABEL_SYSTEM = "系统·自检"
LABEL_ANON = "(未透传身份)"
_LOCAL_IPS = ("127.0.0.1", "::1", "localhost")

# ── 错误口径：**权限拒绝(401/403)不是故障**，与真故障分开统计 ──────────────────────
# 登录门/权限闸拒绝无权账号是**设计行为**，不是服务出错。把它记进"错误"有两个实际危害：
#   ① 错误列表被刷屏并卡在 error_limit 上限——真出的 500 会被挤出列表，运维页反而看不见；
#   ② 并发图把整点标红、板块表"错误"列虚高——看图的人以为服务在报错，白查一轮。
# ⚠ 4xx 里**只摘 401/403**：400/404/422 留在故障里——那些通常是前端打错接口/参数，是真该看见的 bug。
_FAULT_SQL = "((status>=400 AND status NOT IN (401,403)) OR err IS NOT NULL)"
_DENIED_SQL = "(status IN (401,403) AND err IS NULL)"

# ── 进程内实时状态 ───────────────────────────────────────────────────────────────
_lock = threading.Lock()
_inflight = {}          # seq -> 请求现场 dict（正在处理中的）
_recent = []            # 最近完成的请求（内存环形，页面"最近请求"用，不查库）
_seq = 0
_peak_inflight = 0      # 本进程启动以来的峰值
_peak_at = None
_dropped = 0            # 队列满被丢弃的日志条数
_started_at = time.time()

_q = queue.Queue(maxsize=_QUEUE_MAX)
_writer_started = False


# ── 路径归一化 ───────────────────────────────────────────────────────────────────
_SAFE_SEG = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]*$")


def normalize_path(path):
    """把路径里的 ID / 期间等可变段替换成 {id}，控制慢接口榜的基数。

    规则：纯数字段、或含 ≥4 位数字的段 → {id}；其余保留。
    例：/api/bom/entry/059207 → /api/bom/entry/{id}
        /api/reconcile/sync    → 原样（无数字，是真实端点名）
    """
    out = []
    for seg in path.split("/"):
        if not seg:
            out.append(seg)
            continue
        digits = sum(c.isdigit() for c in seg)
        if seg.isdigit() or digits >= 4:
            out.append("{id}")
        elif _SAFE_SEG.match(seg):
            out.append(seg)
        else:
            out.append("{x}")        # 中文/编码过的段也收敛掉，别撑爆维度
        # 注意：不做小写化——路径大小写敏感，改了就对不上真实接口
    return "/".join(out) or "/"


def board_of(path):
    for prefix, name in _BOARD_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return name
    return "其他"


def _excluded(path):
    return any(path == p or path.startswith(p) for p in EXCLUDE_PREFIXES)


# ── SQLite：建表 / 连接 ──────────────────────────────────────────────────────────
_DDL = """
CREATE TABLE IF NOT EXISTS req_log (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  ts       REAL    NOT NULL,   -- epoch 秒（UTC 无关，展示时按本机时区格式化）
  day      TEXT    NOT NULL,   -- 本地日期 YYYY-MM-DD
  hour     INTEGER NOT NULL,   -- 本地小时 0-23
  user     TEXT,               -- 登录用户名，未登录为 NULL
  method   TEXT    NOT NULL,
  path     TEXT    NOT NULL,   -- 已归一化
  board    TEXT    NOT NULL,
  status   INTEGER NOT NULL,
  ms       INTEGER NOT NULL,   -- 服务端处理耗时
  inflight INTEGER NOT NULL,   -- 该请求开始时的在飞请求数（含自己）
  pid      INTEGER NOT NULL,
  ip       TEXT,
  err      TEXT                -- 异常摘要，正常为 NULL
);
CREATE INDEX IF NOT EXISTS ix_req_ts   ON req_log(ts);
CREATE INDEX IF NOT EXISTS ix_req_day  ON req_log(day, hour);
CREATE INDEX IF NOT EXISTS ix_req_stat ON req_log(status);
"""


def _connect():
    conn = sqlite3.connect(OPS_DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")      # 多进程/读写并发，读不挡写
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db():
    conn = _connect()
    try:
        conn.executescript(_DDL)
        conn.commit()
    finally:
        conn.close()


# ── 后台 writer 线程：批量落库 + 过期清理 ────────────────────────────────────────
def _writer_loop():
    conn = _connect()
    try:
        conn.executescript(_DDL)
        conn.commit()
    except Exception as e:
        print("[ops] 建表失败，埋点降级为只在内存：%s" % e)
    last_purge = 0.0
    while True:
        batch = []
        try:
            batch.append(_q.get(timeout=2.0))       # 阻塞等第一条，避免空转
        except queue.Empty:
            pass
        while len(batch) < 500:                      # 顺手把队列里攒的一起写
            try:
                batch.append(_q.get_nowait())
            except queue.Empty:
                break
        if batch:
            try:
                conn.executemany(
                    "INSERT INTO req_log(ts,day,hour,user,method,path,board,status,ms,inflight,pid,ip,err)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(r["ts"], r["day"], r["hour"], r["user"], r["method"], r["path"], r["board"],
                      r["status"], r["ms"], r["inflight"], r["pid"], r["ip"], r["err"]) for r in batch],
                )
                conn.commit()
            except Exception as e:                   # 落库失败不能影响业务，打一行就算了
                print("[ops] 日志落库失败(本批 %d 条已丢弃)：%s" % (len(batch), e))
                try:
                    conn.rollback()
                except Exception:
                    pass
        now = time.time()
        if now - last_purge > 3600:                  # 每小时清一次过期
            last_purge = now
            try:
                conn.execute("DELETE FROM req_log WHERE ts < ?", (now - OPS_RETAIN_DAYS * 86400,))
                conn.commit()
            except Exception:
                pass


def start_writer():
    global _writer_started
    if _writer_started or not OPS_ENABLED:
        return
    _writer_started = True
    threading.Thread(target=_writer_loop, name="ops-log-writer", daemon=True).start()


# ── 采样：进入 / 离开 ────────────────────────────────────────────────────────────
def _enter(rec):
    global _seq, _peak_inflight, _peak_at
    with _lock:
        _seq += 1
        key = _seq
        _inflight[key] = rec
        n = len(_inflight)
        rec["inflight"] = n
        if n > _peak_inflight:
            _peak_inflight = n
            _peak_at = rec["ts"]
    return key


def _leave(key, status, ms, err, user):
    global _dropped
    with _lock:
        rec = _inflight.pop(key, None)
        if rec is None:
            return
        rec = dict(rec)
        # 身份在请求**结束**时才补：内层 auth_gate 已把登录名塞进 scope["state"]，这里读回来（见文件头身份口径）
        rec.update(status=status, ms=int(ms), err=err, user=user)
        _recent.append(rec)
        if len(_recent) > OPS_RECENT_MAX:
            del _recent[: len(_recent) - OPS_RECENT_MAX]
    try:
        _q.put_nowait(rec)
    except queue.Full:
        with _lock:
            _dropped += 1


# ── 中间件本体 ───────────────────────────────────────────────────────────────────
def _header(scope, name):
    """从 ASGI scope 取头（bytes，小写名）。"""
    for k, v in scope.get("headers") or ():
        if k == name:
            try:
                return v.decode("latin-1")
            except Exception:
                return None
    return None


class OpsMiddleware:
    """记录每个 HTTP 请求。异常安全：埋点自身出任何问题都不能影响业务响应。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not OPS_ENABLED:
            return await self.app(scope, receive, send)
        path = scope.get("path", "") or ""
        if _excluded(path):
            return await self.app(scope, receive, send)

        try:
            client = scope.get("client") or ()
            rec = {
                "ts": time.time(),
                "user": None,                       # 结束时从 scope["state"] 补（内层 auth_gate 塞的）
                "method": scope.get("method", "?"),
                "raw_path": path,
                "path": normalize_path(path),
                "board": board_of(path),
                "pid": os.getpid(),
                "ip": _header(scope, b"x-forwarded-for") or (client[0] if client else None),
                "inflight": 1,
            }
            dt = datetime.fromtimestamp(rec["ts"])
            rec["day"], rec["hour"] = dt.strftime("%Y-%m-%d"), dt.hour
            key = _enter(rec)
        except Exception:
            return await self.app(scope, receive, send)   # 埋点坏了就当没埋点

        t0 = time.perf_counter()
        holder = {"status": 0}

        async def _send(message):
            if message.get("type") == "http.response.start":
                holder["status"] = message.get("status", 0)
            await send(message)

        err = None
        try:
            await self.app(scope, receive, _send)
        except Exception as e:
            err = ("%s: %s" % (type(e).__name__, e))[:300]
            holder["status"] = holder["status"] or 500
            raise
        finally:
            try:
                # 内层已跑完，身份此刻可读（登录门只对非白名单 /api 塞身份，其余保持匿名，见文件头口径）
                st = scope.get("state") or {}
                user = st.get(STATE_USER_KEY)
                _leave(key, holder["status"] or 499, (time.perf_counter() - t0) * 1000.0, err, user)
            except Exception:
                pass
            # status 0→499：响应还没开始就断了，一般是客户端主动取消（关页面/超时重试）


# ══════════════════════════════════════════════════════════════════════════════
#  查询侧：实时探针 + 历史聚合
# ══════════════════════════════════════════════════════════════════════════════
def live():
    """实时探针：当前在飞请求 + 最近完成的请求。**只反映本进程**（内存态）。"""
    now = time.time()
    with _lock:
        flying = [
            {
                "user": r.get("user"), "method": r["method"], "path": r["raw_path"],
                "board": r["board"], "ip": r.get("ip"),
                "elapsedMs": int((now - r["ts"]) * 1000), "startedAt": r["ts"],
            }
            for r in _inflight.values()
        ]
        recent = [
            {
                "ts": r["ts"], "user": r.get("user"), "method": r["method"], "path": r["raw_path"],
                "board": r["board"], "status": r.get("status"), "ms": r.get("ms"),
                "inflight": r.get("inflight"), "err": r.get("err"),
            }
            for r in _recent[-60:]
        ]
        peak, peak_at, dropped = _peak_inflight, _peak_at, _dropped
    flying.sort(key=lambda x: -x["elapsedMs"])
    recent.reverse()
    result = {
        "now": now, "pid": os.getpid(), "enabled": OPS_ENABLED,
        "uptimeSec": int(now - _started_at),
        "inflight": len(flying), "flying": flying,
        "peakSinceStart": peak, "peakAt": peak_at,
        "recent": recent, "dropped": dropped,
        "retainDays": OPS_RETAIN_DAYS, "dbPath": OPS_DB_PATH,
    }
    # "当前有谁在登录"＝最近有动作（查库跨进程汇总，多 worker 也全）。DB 出任何问题都不能拖垮实时探针→兜 None。
    try:
        result["online"] = online_users(5)
    except Exception:
        result["online"] = None
    return result


def _p95(conn, since, total):
    """P95 = 从慢到快数第 ceil(5%) 条。⚠ 用 ceil 不是 int：样本少时 int 会退化成 offset=0，
    把「最大值」当成 P95 报出去，看图的人会以为普遍很慢。"""
    if not total:
        return 0
    import math
    offset = max(0, math.ceil(total * 0.05) - 1)
    row = conn.execute(
        "SELECT ms FROM req_log WHERE ts>=? ORDER BY ms DESC LIMIT 1 OFFSET ?", (since, offset)
    ).fetchone()
    return int(row[0]) if row else 0


def _capacity_advice(peak, slow_5s, slow_max_ms, overlap_hours, busiest_path):
    """把观测数据翻成一句人话建议。**是启发式，不是定论**——依据一并给出，方便自己判断。

    判据（关心的是"要不要开多 worker / 单进程扛不扛得住"）：
      - 单进程 uvicorn 同一时刻只有一个线程在跑 Python 字节码（GIL）。
        async 接口里的 CPU 重活会把**所有人**堵住。
      - 所以真正的风险信号不是"请求多"，而是"**慢请求**与别人重叠"。
    """
    reasons = []
    level = "ok"
    if peak <= 1:
        reasons.append("观测期内没有出现过两个请求同时在处理（峰值并发 %d）" % peak)
    else:
        reasons.append("峰值并发 %d（%d 个小时段出现过 ≥2 并发）" % (peak, overlap_hours))
    if slow_5s:
        level = "warn"
        reasons.append("有 %d 次请求耗时 >5 秒，最慢 %.1f 秒%s" % (
            slow_5s, slow_max_ms / 1000.0, ("（%s）" % busiest_path) if busiest_path else ""))
    if peak >= 5:
        level = "warn"

    if level == "ok" and peak <= 2:
        text = "单进程够用。峰值并发很低，且没有长时间占住进程的请求。"
    elif slow_5s and peak >= 2:
        text = ("建议开 2–4 个 worker（或把重活挪进独立子进程）。已经出现「慢请求 + 并发」同时发生的情况，"
                "单进程下慢请求会把其他人一起堵住。")
    elif slow_5s:
        text = ("暂时不急着加 worker，但要盯住重活接口：目前是错峰用的，一旦两个人同时用就会互相卡。"
                "更优先的做法是把 >5 秒的接口挪到独立子进程，而不是单纯加 worker。")
    else:
        text = "建议开 %d 个 worker。并发峰值已经上来了，单进程排队会体感变慢。" % max(2, (peak + 1) // 2)
    return {"level": level, "advice": text, "reasons": reasons}


def stats(days=7, slow_limit=15, error_limit=100):
    """历史聚合。days=观测窗口天数；跨进程（所有 worker 都写同一个库）。"""
    since = time.time() - days * 86400
    conn = _connect()
    try:
        conn.executescript(_DDL)
        cur = conn.cursor()

        row = cur.execute(
            "SELECT COUNT(*), COUNT(DISTINCT COALESCE(user,'')), AVG(ms), MAX(ms),"
            f" SUM(CASE WHEN {_FAULT_SQL} THEN 1 ELSE 0 END),"
            " MAX(inflight), MIN(ts), MAX(ts),"
            " SUM(CASE WHEN ms>=5000 THEN 1 ELSE 0 END),"
            " SUM(CASE WHEN ms>=1000 THEN 1 ELSE 0 END),"
            f" SUM(CASE WHEN {_DENIED_SQL} THEN 1 ELSE 0 END)"
            " FROM req_log WHERE ts>=?", (since,)
        ).fetchone()
        total = row[0] or 0
        summary = {
            "days": days, "total": total,
            "users": row[1] or 0,
            "avgMs": round(row[2] or 0, 1), "maxMs": int(row[3] or 0),
            "p95Ms": _p95(conn, since, total),
            "errors": int(row[4] or 0),
            "peakConcurrency": int(row[5] or 0),
            "firstTs": row[6], "lastTs": row[7],
            "slow5s": int(row[8] or 0), "slow1s": int(row[9] or 0),
            "denied": int(row[10] or 0),          # 权限拒绝（401/403），与 errors 分开
        }

        # 并发口径澄清：`peak`=请求并发（前端一次开页会并行扇出多个请求，故此数≈请求扇出宽度，
        #   **不是同时在线人数**）；`users`=该小时去重的**具名用户数**（COUNT(DISTINCT user) 在 SQLite 会
        #   忽略 NULL，故未登录的直连不计入）——这才是"该小时几个人在用"的诚实指标。
        by_hour = [
            {"day": d, "hour": h, "count": c, "peak": p, "avgMs": round(a or 0, 1),
             "errors": e or 0, "denied": dn or 0, "users": us or 0}
            for d, h, c, p, a, e, dn, us in cur.execute(
                "SELECT day, hour, COUNT(*), MAX(inflight), AVG(ms),"
                f" SUM(CASE WHEN {_FAULT_SQL} THEN 1 ELSE 0 END),"
                f" SUM(CASE WHEN {_DENIED_SQL} THEN 1 ELSE 0 END),"
                " COUNT(DISTINCT user)"
                " FROM req_log WHERE ts>=? GROUP BY day, hour ORDER BY day, hour", (since,))
        ]
        summary["peakHourUsers"] = max((h["users"] for h in by_hour), default=0)

        # 每个用户用了哪些板块（"谁在用哪块"）
        user_boards = {}
        for u, b, c in cur.execute(
            "SELECT COALESCE(user,'(未透传身份)'), board, COUNT(*) FROM req_log WHERE ts>=?"
            " GROUP BY 1,2", (since,)
        ):
            user_boards.setdefault(u, []).append({"board": b, "count": c})
        for v in user_boards.values():
            v.sort(key=lambda x: -x["count"])

        by_user = [
            {"user": u, "count": c, "writes": w or 0, "lastTs": last, "days": dcnt,
             "boards": user_boards.get(u, [])}
            for u, c, w, last, dcnt in cur.execute(
                "SELECT COALESCE(user,'(未透传身份)'), COUNT(*),"
                " SUM(CASE WHEN method IN ('POST','PUT','DELETE','PATCH') THEN 1 ELSE 0 END),"
                " MAX(ts), COUNT(DISTINCT day)"
                " FROM req_log WHERE ts>=? GROUP BY 1 ORDER BY 2 DESC", (since,))
        ]

        by_board = [
            {"board": b, "count": c, "users": u, "avgMs": round(a or 0, 1), "maxMs": int(m or 0),
             "errors": e or 0, "denied": dn or 0}
            for b, c, u, a, m, e, dn in cur.execute(
                "SELECT board, COUNT(*), COUNT(DISTINCT COALESCE(user,'')), AVG(ms), MAX(ms),"
                f" SUM(CASE WHEN {_FAULT_SQL} THEN 1 ELSE 0 END),"
                f" SUM(CASE WHEN {_DENIED_SQL} THEN 1 ELSE 0 END)"
                " FROM req_log WHERE ts>=? GROUP BY board ORDER BY 2 DESC", (since,))
        ]

        # 慢接口榜：按**总占用时间**排（真正吃掉进程的是它，不是单次最慢那个偶发值）
        slow = [
            {"key": f"{m} {p}", "method": m, "path": p, "count": c,
             "avgMs": round(a or 0, 1), "maxMs": int(mx or 0), "totalMs": int(s or 0)}
            for m, p, c, a, mx, s in cur.execute(
                "SELECT method, path, COUNT(*), AVG(ms), MAX(ms), SUM(ms)"
                " FROM req_log WHERE ts>=? GROUP BY method, path"
                " ORDER BY SUM(ms) DESC LIMIT ?", (since, slow_limit))
        ]

        def _err_rows(cond):
            return [
                {"ts": t, "user": u, "method": m, "path": p, "status": st, "ms": ms, "err": er, "ip": ip}
                for t, u, m, p, st, ms, er, ip in cur.execute(
                    "SELECT ts, user, method, path, status, ms, err, ip FROM req_log"
                    f" WHERE ts>=? AND {cond}"
                    " ORDER BY ts DESC LIMIT ?", (since, error_limit))
            ]

        # 两张表分开。errors=真故障（前端「错误」tab，正常应为空）；
        #   denied=权限拒绝（前端「权限拒绝」tab，用来核对"谁被挡在哪个板块外"——是排权限的线索，不是故障）。
        errors = _err_rows(_FAULT_SQL)
        denied = _err_rows(_DENIED_SQL)

        by_pid = [
            {"pid": p, "count": c, "peak": pk, "lastTs": last}
            for p, c, pk, last in cur.execute(
                "SELECT pid, COUNT(*), MAX(inflight), MAX(ts) FROM req_log WHERE ts>=?"
                " GROUP BY pid ORDER BY 2 DESC", (since,))
        ]

        overlap_hours = sum(1 for h in by_hour if h["peak"] >= 2)
        busiest = slow[0]["key"] if slow else None
        capacity = _capacity_advice(summary["peakConcurrency"], summary["slow5s"],
                                    summary["maxMs"], overlap_hours, busiest)
        capacity["workers"] = len(by_pid)
        return {
            "summary": summary, "capacity": capacity, "byHour": by_hour, "byUser": by_user,
            "byBoard": by_board, "slow": slow, "errors": errors, "denied": denied, "byPid": by_pid,
            "retainDays": OPS_RETAIN_DAYS,
        }
    finally:
        conn.close()


# ── 当前在线用户 ─────────────────────────────────────────────────────────────────
def online_users(window_min=5):
    """当前在线：最近 window_min 分钟内有请求的用户（去重）。

    【为什么这么定】身份只在每个请求里（会话解析后塞进 scope），没有独立的心跳。
      所以"当前有谁在登录"只能等价成"最近谁在操作"——一个用户 window_min 分钟内没有任何请求，就当他离开了。
      窗口取 5 分钟：工作台是"看一眼/点几下"的交互节奏，太短会把正在看报表的人误判成离线。
    【口径】count 只数**真人**。无身份的匿名请求按来源 IP 再分两类：
      本机回环＝系统·自检（跑批/本机直连），其余＝(未透传身份)；两类都不计入 count，但各自单列并在表里显名。
    跨进程：读 DB，多 worker 部署时也能汇总到全部用户。
    """
    since = time.time() - window_min * 60
    conn = _connect()
    try:
        conn.executescript(_DDL)
        agg = {}
        for user, ip, board, path, method, ts in conn.execute(
            "SELECT user, ip, board, path, method, ts FROM req_log WHERE ts>=? ORDER BY ts DESC", (since,)
        ):
            key = user if user else (LABEL_SYSTEM if (ip in _LOCAL_IPS) else LABEL_ANON)
            a = agg.get(key)
            if a is None:
                a = {"user": key, "count": 0, "writes": 0, "_boards": set(), "lastTs": ts,
                     "lastBoard": board, "lastPath": path, "lastIp": ip, "lastMethod": method}
                agg[key] = a
            a["count"] += 1
            if method in _WRITE_METHODS:
                a["writes"] += 1
            a["_boards"].add(board)
        rows = []
        for a in agg.values():
            a["boards"] = len(a.pop("_boards"))
            rows.append(a)
        rows.sort(key=lambda x: -(x["lastTs"] or 0))
        SYN = (LABEL_SYSTEM, LABEL_ANON)
        return {
            "windowMin": window_min, "now": time.time(),
            "count": sum(1 for r in rows if r["user"] not in SYN),   # 只数真人
            "system": sum(1 for r in rows if r["user"] == LABEL_SYSTEM),
            "anon": sum(1 for r in rows if r["user"] == LABEL_ANON),
            "users": rows,
        }
    finally:
        conn.close()


# ── 单用户操作会话/轨迹 ──────────────────────────────────────────────────────────
def user_sessions(user, days=1, gap_min=30, max_rows=1500):
    """某用户的操作轨迹，按时间切成"会话"：相邻两条请求间隔 > gap_min 分钟就断成新会话。

    用途：「谁做了什么」——点一个用户，看他今天分几段来过、每段动了哪些板块、有没有写操作/报错。
    gap_min=30：用"静默多久算一次结束"来划分会话；半小时是"离开工位"的经验阈值。
    """
    since = time.time() - days * 86400
    if user == LABEL_SYSTEM:
        ph = ",".join("?" * len(_LOCAL_IPS))
        where = "ts>=? AND user IS NULL AND ip IN (" + ph + ")"
        args = [since, *_LOCAL_IPS]
    elif user == LABEL_ANON:
        where = "ts>=? AND user IS NULL"
        args = [since]
    else:
        where = "ts>=? AND user=?"
        args = [since, user]
    conn = _connect()
    try:
        conn.executescript(_DDL)
        rows = conn.execute(
            "SELECT ts, method, path, board, status, ms, ip, err FROM req_log"
            " WHERE " + where + " ORDER BY ts ASC LIMIT ?", args + [max_rows]
        ).fetchall()
        gap = gap_min * 60
        sessions = []
        cur = None
        for ts, method, path, board, status, ms, ip, err in rows:
            item = {"ts": ts, "method": method, "path": path, "board": board,
                    "status": status, "ms": ms, "ip": ip, "err": err}
            fault = (status is not None and status >= 400 and status not in (401, 403)) or err
            if cur is None or ts - cur["end"] > gap:
                cur = {"start": ts, "end": ts, "count": 0, "writes": 0, "errors": 0,
                       "ip": ip, "_boards": {}, "items": []}
                sessions.append(cur)
            cur["end"] = ts
            cur["count"] += 1
            cur["_boards"][board] = cur["_boards"].get(board, 0) + 1
            if method in _WRITE_METHODS:
                cur["writes"] += 1
            if fault:
                cur["errors"] += 1
            cur["items"].append(item)
        for s in sessions:
            s["boards"] = sorted(
                ({"board": b, "count": c} for b, c in s.pop("_boards").items()),
                key=lambda x: -x["count"])
        sessions.reverse()   # 最近的会话排在前
        return {
            "user": user, "days": days, "gapMin": gap_min,
            "sessionCount": len(sessions), "reqTotal": len(rows),
            "truncated": len(rows) >= max_rows, "sessions": sessions,
        }
    finally:
        conn.close()


def recent_logs(limit=300, user=None, board=None, only_errors=False, days=7):
    """原始日志翻页（"日志"诉求本体）。默认最近 7 天，按时间倒序。"""
    since = time.time() - days * 86400
    where, args = ["ts>=?"], [since]
    if user:
        where.append("COALESCE(user,'(未透传身份)')=?")
        args.append(user)
    if board:
        where.append("board=?")
        args.append(board)
    if only_errors:
        where.append("(status>=400 OR err IS NOT NULL)")
    args.append(min(int(limit), 2000))
    conn = _connect()
    try:
        conn.executescript(_DDL)
        rows = conn.execute(
            "SELECT ts,user,method,path,board,status,ms,inflight,pid,ip,err FROM req_log"
            " WHERE " + " AND ".join(where) + " ORDER BY ts DESC LIMIT ?", args
        ).fetchall()
        return [
            {"ts": r[0], "user": r[1], "method": r[2], "path": r[3], "board": r[4], "status": r[5],
             "ms": r[6], "inflight": r[7], "pid": r[8], "ip": r[9], "err": r[10]}
            for r in rows
        ]
    finally:
        conn.close()


# ── V2.492 验收台账用：按板块的近期调用量 + 每板块逐账号明细 ──────────────────────
def board_usage(days=7, detail=True):
    """{board: {count, accounts, byUser:[{user,count,lastTs}]}}——验收台账「近期调用」列与展开明细。
    只统计具名用户的写/读请求；未透传身份归「(未透传身份)」。detail=False 时省掉 byUser（省内存）。"""
    since = time.time() - days * 86400
    conn = _connect()
    try:
        conn.executescript(_DDL)
        agg = {}
        for board, user, ts in conn.execute(
            "SELECT board, user, ts FROM req_log WHERE ts>=?", (since,)
        ):
            b = agg.get(board)
            if b is None:
                b = {"count": 0, "_users": {}}
                agg[board] = b
            b["count"] += 1
            key = user or LABEL_ANON
            u = b["_users"].get(key)
            if u is None:
                b["_users"][key] = {"user": key, "count": 1, "lastTs": ts}
            else:
                u["count"] += 1
                if ts > u["lastTs"]:
                    u["lastTs"] = ts
        out = {}
        for board, b in agg.items():
            users = sorted(b["_users"].values(), key=lambda x: -x["count"])
            row = {"count": b["count"], "accounts": len(users)}
            if detail:
                row["byUser"] = users
            out[board] = row
        return out
    finally:
        conn.close()
