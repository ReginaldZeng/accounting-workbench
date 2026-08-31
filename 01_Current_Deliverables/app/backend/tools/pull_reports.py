# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-08-07 | Author: Claude / c | Version: V2.241
# Description: 报表取件机——跑在【公司内网一台常开电脑】上，定时把云端工作台导好的报表
#              拉回来、放进共享盘。
#
# 为什么要这么绕：云服务器在腾讯云公网，NAS 在办公室内网（10.10.8.x），服务器够不着 NAS。
# 让服务器进内网需要 VPN／在防火墙开入口；改成【内网主动出去取】，连接方向由内到外，
# 出方向本来就通，**办公室网络一个入口都不用开**，运维零审批。
#
# 只用 Python 标准库（urllib/json/shutil）——那台电脑不用装任何第三方包。
#
# 用法：
#   1. 把本文件和 pull_reports.ini 放到那台电脑的同一个目录（如 D:\报表取件\）
#   2. 编辑 pull_reports.ini 填服务器地址、取件码、共享盘目标目录
#   3. 先手工跑一次验证：python pull_reports.py
#   4. Windows 任务计划程序建个任务，每 1 分钟跑一次（详见 README 段）
import configparser
import datetime
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
INI = os.path.join(BASE, "pull_reports.ini")
LOG = os.path.join(BASE, "pull_reports.log")


def log(msg):
    line = "%s  %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        # 日志只留最近 2000 行——这脚本一天跑 1440 次，不封顶迟早把盘写满
        old = []
        if os.path.exists(LOG):
            with open(LOG, encoding="utf-8") as f:
                old = f.read().splitlines()[-1999:]
        with open(LOG, "w", encoding="utf-8") as f:
            f.write("\n".join(old + [line]) + "\n")
    except OSError:
        pass


def load_cfg():
    if not os.path.exists(INI):
        log("[X] 缺配置文件：%s（照 pull_reports.ini.example 建一个）" % INI)
        sys.exit(2)
    c = configparser.ConfigParser()
    c.read(INI, encoding="utf-8")
    s = c["pull"]
    cfg = {"base": s.get("server", "").strip().rstrip("/"),
           "token": s.get("pull_token", "").strip(),
           "dest": s.get("dest_dir", "").strip(),
           # 别往大了调：任务每分钟一轮，单次卡过 60 秒就跨轮了
           "timeout": int(s.get("timeout", "45") or 45)}
    miss = [k for k in ("base", "token", "dest") if not cfg[k]]
    if miss:
        log("[X] pull_reports.ini 缺项：%s" % "、".join(miss))
        sys.exit(2)
    return cfg


def api(cfg, path, query=None, data=None, binary=False):
    url = cfg["base"] + path + (("?" + urllib.parse.urlencode(query)) if query else "")
    body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, method="POST" if data is not None else "GET")
    req.add_header("X-Pull-Token", cfg["token"])
    if body:
        req.add_header("Content-Type", "application/json;charset=utf-8")
    with urllib.request.urlopen(req, timeout=cfg["timeout"]) as r:
        return r.read() if binary else json.loads(r.read().decode("utf-8"))


def main():
    cfg = load_cfg()
    if not os.path.isdir(cfg["dest"]):
        log("[X] 目标目录不存在：%s —— 共享盘没挂上？先在资源管理器里打开确认。" % cfg["dest"])
        return 1
    try:
        r = api(cfg, "/api/rptexport/files")
    except urllib.error.HTTPError as e:
        log("[X] 服务器拒绝（HTTP %s）：取件码不对，或服务器没配 pull_token。" % e.code)
        return 1
    except Exception as e:
        log("[X] 连不上服务器 %s：%s" % (cfg["base"], e))
        return 1
    if not r.get("ok"):
        log("[X] 服务器：%s" % r.get("msg"))
        return 1

    # 有人在页面点了「立即同步」→ 这轮即便文件没变也照样回报一次，让页面上的时间戳立刻更新，
    # 点的人马上看到"动了"。不然他点完看不到任何反应，只会以为坏了、再点五次。
    todel = []
    try:
        pend = api(cfg, "/api/rptexport/pending")
        forced, todel = bool(pend.get("pending")), list(pend.get("delete") or [])
    except Exception:
        forced = False
    if forced:
        log("收到「立即同步」请求，本轮强制回报")

    # 已取记录：记「上次取的时候服务器上那个文件是多大、什么时候改的」。
    # ⚠ 不能只比文件大小：同一张报表重导后改几个数字，字节数很可能一模一样，
    #   只比大小会把更新整个漏掉——表现成"同步一切正常、共享盘上却是旧数据"，最难发现。
    # 也不能拿共享盘上文件的 mtime 比：复制过去后它变成"复制时间"，每轮都会判成新的。
    state_p = os.path.join(BASE, "pull_state.json")
    try:
        state = json.load(open(state_p, encoding="utf-8"))
    except Exception:
        state = {}

    copied, skipped, errors, retry = [], 0, [], []
    for f in r.get("files", []):
        rel = f.get("rel") or f["name"]                  # 老服务器只给 name，兼容
        dst = os.path.join(cfg["dest"], *rel.split("/"))
        stamp = "%s|%s" % (f["size"], f.get("mtime", ""))
        if os.path.exists(dst) and state.get(rel) == stamp:
            skipped += 1
            continue
        try:
            blob = api(cfg, "/api/rptexport/download", {"name": rel}, binary=True)
            if len(blob) != f["size"]:
                # 不是下载坏了——是服务器上这个文件正在被重导覆盖：/files 报的是取清单那一刻的大小。
                # 不写盘也不记成失败（记失败会在页面刷一片红字吓人）；state 没更新，下轮自然重取。
                retry.append(rel)
                continue
            sub = os.path.dirname(dst)                   # 按月建子目录，与服务器结构一致
            os.makedirs(sub, exist_ok=True)
            # 先落临时文件再原子改名——别让同事在共享盘上看到写了一半的 Excel
            fd, tmp = tempfile.mkstemp(dir=sub, suffix=".part")
            with os.fdopen(fd, "wb") as w:
                w.write(blob)
            shutil.move(tmp, dst)
            state[rel] = stamp
            copied.append(rel)
        except Exception as e:
            errors.append("%s: %s" % (rel, str(e)[:120]))
    # 执行删除指令：**只删服务器明确点名的那几个**，不是"服务器没有的都删"。
    # 镜像式删除的经典翻车：落地路径配错/目录读不到 → 以为服务器空了 → 把共享盘清光。
    # 另加两道保险：文件名必须符合导出格式；解析出的绝对路径必须仍在 dest 内。
    deleted = []
    name_re = re.compile(r"^\d{4}年\d{2}期_.{1,120}_财务报表\.xlsx$")
    root = os.path.realpath(cfg["dest"])
    for rel in todel:
        if not name_re.match(os.path.basename(rel)):
            errors.append("拒绝删除(文件名不合格式)：%s" % rel)
            continue
        full = os.path.realpath(os.path.join(cfg["dest"], *rel.split("/")))
        if os.path.commonpath([full, root]) != root:
            errors.append("拒绝删除(越出目标目录)：%s" % rel)
            continue
        if not os.path.exists(full):
            deleted.append(rel)               # 本来就没有＝指令已达成
            continue
        try:
            os.remove(full)
            state.pop(rel, None)              # 从已取记录划掉：将来它再出现要能重新下载
            deleted.append(rel)
            log("   [删] " + rel)
        except OSError as e:
            errors.append("删除失败 %s: %s" % (rel, e))

    try:
        json.dump(state, open(state_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except OSError:
        pass

    if copied or errors or deleted or retry or forced:
        log("下载 %d 个、跳过 %d 个、删除 %d 个、待重取 %d 个、失败 %d 个 → %s"
            % (len(copied), skipped, len(deleted), len(retry), len(errors), cfg["dest"]))
        for n in retry:
            log("   [等] %s（服务器端正在更新，下轮重取）" % n)
        for n in copied:
            log("   ✓ " + n)
        for e in errors:
            log("   ✗ " + e)
    # 扫一遍共享盘，找出**最新的那个文件**。
    # 页面上人真正问的是"共享盘最新的文件是什么时候的"，而**服务器看不到共享盘**，
    # 只能由这台电脑报上去。文件不多（一个月 8 个），每轮扫一次的开销可以忽略。
    # 按期分桶：页面是按期间看的，站在"2026年7期"那一屏，人要的是 7 期的数，
    # 给全部期间合计只会被读错。期间**从文件名认**（2026年07期_…），不从目录认——
    # 早期导的散件平铺在根目录上，按目录认会把它们漏掉。
    newest, newest_at, total = "", "", 0
    months = {}
    mkey = re.compile(r"^(\d{4})年(\d{2})期_")

    def _mk(name):
        m = mkey.match(os.path.basename(name))
        return ("%s年%s月" % (m.group(1), m.group(2))) if m else ""

    def _bucket(k):
        return months.setdefault(k, {"n": 0, "copied": 0, "skipped": 0, "newest": "", "newest_at": ""})

    for r in copied:
        k = _mk(r)
        if k:
            _bucket(k)["copied"] += 1
    try:
        best = None
        for root, _dirs, files in os.walk(cfg["dest"]):
            for n in files:
                if not name_re.match(n):
                    continue
                total += 1
                fp = os.path.join(root, n)
                try:
                    mt = os.path.getmtime(fp)
                except OSError:
                    continue
                rel = os.path.relpath(fp, cfg["dest"]).replace("\\", "/")
                ts = datetime.datetime.fromtimestamp(mt).strftime("%Y-%m-%d %H:%M:%S")
                k = _mk(n)
                if k:
                    b = _bucket(k)
                    b["n"] += 1
                    if ts > b["newest_at"]:
                        b["newest_at"], b["newest"] = ts, rel
                if best is None or mt > best[0]:
                    best = (mt, rel)
        # 跳过数按期反推：该期共有几个 − 本轮新搬几个
        for b in months.values():
            b["skipped"] = max(0, b["n"] - b["copied"])
        if best:
            newest = best[1]
            newest_at = datetime.datetime.fromtimestamp(best[0]).strftime("%Y-%m-%d %H:%M:%S")
    except OSError as e:
        errors.append("扫描共享盘失败：%s" % str(e)[:120])

    # 回执：让工作台页面能显示「取件机在跑 · 共享盘最新文件是 X」。
    # 这台电脑要是关机了，页面上时间就一直停在旧值——问题当场看得见。
    try:
        api(cfg, "/api/rptexport/sync-report",
            data={"host": socket.gethostname(), "dest": cfg["dest"],
                  "copied": copied, "skipped": skipped, "deleted": deleted, "retry": retry, "errors": errors,
                  "newest": newest, "newest_at": newest_at, "total": total, "months": months})
    except Exception as e:
        log("[!] 回执没发出去（不影响文件已落盘）：%s" % str(e)[:120])
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
