# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-09-06 | Author: Claude / c | Version: V2.486
# Description: 银行流水【上行】取件机——跑在【公司内网一台常开电脑】上，定时扫共享盘的月度流水目录，
#              把散件【推】给云端工作台的 /api/bank-pull/*，服务器收齐后自动解析定格（财资归并/逐笔查重
#              /重复待确认弹窗一并继承）。方向与报表取件相反：报表取件把云端文件拉回共享盘，本脚本把
#              共享盘文件推上云端。连接方向仍是【内网主动出去】，办公室防火墙一个入口都不用开。
#
# 与报表取件机（pull_reports.py）互不干扰：读各自 ini、走各自接口、各自的状态文件。
# 铁律落地：
#   ① 只推、绝不删共享盘任何文件（上行本就不该动源）。
#   ② 传完判据：一个月目录里的文件【连续 settle_minutes 分钟大小/数量不再变】才认定"齐了"，
#      再向服务器 commit——否则解析到出纳还没传完的半个月数据，静默少账。
#   ③ 增量：记每个文件的 大小+mtime，没变的不重推（省带宽）。
#   ④ 服务器侧不覆盖人工上传（见 /api/bank-pull/commit）；解析后保留人工确认闸，不自动确认。
"""银行流水上行取件机

用法（内网常开电脑，装了 Python）：
    复制 bank_pull.ini.example → bank_pull.ini，填 server / pull_token / src_root
    手工验证：  python bank_pull.py
    定时：      每小时跑一次（用「注册定时任务.bat」同款方式，或系统计划任务）
"""
import os
import sys
import io
import json
import time
import re
import configparser
import urllib.request
import urllib.parse
import urllib.error
import datetime

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
INI = os.path.join(HERE, "bank_pull.ini")
LOGF = os.path.join(HERE, "bank_pull.log")
STATEF = os.path.join(HERE, "bank_pull_state.json")

# 共享盘月份目录名 → 期间 YYYY-MM。出纳按「5月流水 / 6月流水 / 2026年08月」等命名，尽量宽松认。
_MONTH_PATTERNS = [
    (re.compile(r"^(\d{4})年.*?(\d{1,2})月"), lambda m: (int(m.group(1)), int(m.group(2)))),
    (re.compile(r"^(\d{1,2})月"), lambda m: (None, int(m.group(1)))),          # 只有"6月流水" → 年取父目录
    (re.compile(r"(\d{4})[-_]?(\d{2})"), lambda m: (int(m.group(1)), int(m.group(2)))),
]


def log(msg):
    line = "%s  %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        old = []
        if os.path.exists(LOGF):
            with open(LOGF, encoding="utf-8", errors="replace") as f:
                old = f.read().splitlines()[-1999:]
        with open(LOGF, "w", encoding="utf-8") as f:
            f.write("\n".join(old + [line]))
    except Exception:
        pass


def read_ini():
    if not os.path.exists(INI):
        log("[X] 缺配置文件：%s（把 bank_pull.ini.example 复制改名为 bank_pull.ini）" % INI)
        sys.exit(2)
    c = configparser.ConfigParser()
    # 剔除控制字符（含 \0）：ini 若被存成 UTF-16/"Unicode"，值里夹空字节，塞进请求头会报错。宽松读，容忍任意编码。
    raw = open(INI, "rb").read().decode("utf-8", errors="replace")
    raw = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", raw)
    c.read_string(raw)
    s = c["pull"] if c.has_section("pull") else {}
    cfg = {
        "server": (s.get("server", "") or "").strip().rstrip("/"),
        "token": (s.get("pull_token", "") or "").strip(),
        "src_root": (s.get("src_root", "") or "").strip(),
        "year": (s.get("year", "") or "").strip(),
        "settle_minutes": int(s.get("settle_minutes", "10") or "10"),
        "timeout": int(s.get("timeout", "60") or "60"),
    }
    miss = [k for k in ("server", "token", "src_root") if not cfg[k]]
    if miss:
        log("[X] bank_pull.ini 缺项：" + "、".join(miss))
        sys.exit(2)
    if not cfg["token"].isascii():
        log("[X] pull_token 含非 ASCII（中文/空格）——请求头传不了，会一直 403。改成纯英文数字。")
        sys.exit(2)
    return cfg


def api(cfg, path, data=None, headers=None, binary=False):
    url = cfg["server"] + path
    h = {"X-Pull-Token": cfg["token"]}
    if headers:
        h.update(headers)
    body = None
    if data is not None and not binary:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        h["Content-Type"] = "application/json;charset=utf-8"
    elif binary:
        body = data
        h["Content-Type"] = "application/octet-stream"
    req = urllib.request.Request(url, data=body, headers=h, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=cfg["timeout"]) as r:
        raw = r.read()
    return json.loads(raw.decode("utf-8")) if not binary else raw


def month_key(dirname, default_year):
    for pat, fn in _MONTH_PATTERNS:
        m = pat.search(dirname)
        if m:
            y, mo = fn(m)
            if y is None:
                y = default_year
            if y and 1 <= mo <= 12:
                return "%04d-%02d" % (int(y), mo)
    return None


def scan_dir_signature(d):
    """目录当前签名：{相对路径: (size, mtime)}。用于判"齐没齐"和增量。"""
    sig = {}
    for root, _dirs, files in os.walk(d):
        for fn in files:
            if fn.endswith(".part"):
                continue
            p = os.path.join(root, fn)
            try:
                st = os.stat(p)
                rel = os.path.relpath(p, d).replace("\\", "/")
                sig[rel] = (st.st_size, int(st.st_mtime))
            except OSError:
                continue
    return sig


def main():
    cfg = read_ini()
    default_year = None
    if re.match(r"^\d{4}$", cfg["year"]):
        default_year = int(cfg["year"])
    if not os.path.isdir(cfg["src_root"]):
        log("[X] 源目录不存在：%s —— 共享盘没连上？先在资源管理器里打开确认。" % cfg["src_root"])
        sys.exit(1)

    # 有人在页面点了「立即扫描」→ 忽略 settle 窗口、本轮直接推（人主动要的，别让他等 10 分钟）
    forced = False
    try:
        pend = api(cfg, "/api/bank-pull/pending")
        forced = bool(pend.get("pending"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            log("[X] 服务器拒绝（HTTP %s）：取件码不对，或服务器没配 pull_token。" % e.code)
            sys.exit(1)
        log("[X] 连服务器失败：%s" % e)
        sys.exit(1)
    except Exception as e:
        log("[X] 连服务器失败：%s" % e)
        sys.exit(1)
    if forced:
        log("收到「立即扫描」请求，本轮忽略稳定期直接推")

    state = {}
    if os.path.exists(STATEF):
        try:
            with open(STATEF, encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {}

    # 枚举月份目录
    months = {}
    for name in os.listdir(cfg["src_root"]):
        d = os.path.join(cfg["src_root"], name)
        if not os.path.isdir(d):
            continue
        key = month_key(name, default_year)
        if key:
            months[key] = d

    if not months:
        log("源目录下没有可识别的月份文件夹（如「6月流水」「2026年08月」）：%s" % cfg["src_root"])
        _report(cfg, {"scanned": 0, "pushed": 0, "committed": [], "waiting": [], "note": "无月份目录"})
        return

    pushed_total, committed, waiting = 0, [], []
    for key, d in sorted(months.items()):
        sig = scan_dir_signature(d)
        if not sig:
            continue
        st = state.setdefault(key, {})
        prev_sig = st.get("sig")
        prev_seen = st.get("seen_at")
        cur = {k: list(v) for k, v in sig.items()}
        now = time.time()

        # 稳定期判定：签名与上轮完全一致，且已保持 settle_minutes 分钟 → 认定"齐了"
        stable = (prev_sig == cur)
        if not stable:
            st["sig"] = cur
            st["seen_at"] = now
            st["committed"] = False
            waiting.append("%s（内容仍在变，重新计时）" % key)
            continue
        settled = forced or (prev_seen and (now - prev_seen) >= cfg["settle_minutes"] * 60)
        if not settled and not st.get("committed"):
            left = int(cfg["settle_minutes"] * 60 - (now - (prev_seen or now)))
            waiting.append("%s（稳定中，约 %d 秒后可推）" % (key, max(0, left)))
            continue
        if st.get("committed") and not forced:
            continue   # 这个月已经推过且没再变，跳过

        # 推该月所有文件（增量：与已推签名一致的跳过）
        done_sig = st.get("pushed_sig", {})
        n_push = 0
        for rel, meta in sig.items():
            if done_sig.get(rel) == list(meta):
                continue
            p = os.path.join(d, rel.replace("/", os.sep))
            try:
                with open(p, "rb") as f:
                    blob = f.read()
                api(cfg, "/api/bank-pull/push", data=blob, binary=True,
                    headers={"X-Bank-Period": key,
                             "X-Bank-Relname": urllib.parse.quote(rel)})
                done_sig[rel] = list(meta)
                n_push += 1
            except Exception as e:
                log("   [X] 推送失败 %s/%s：%s" % (key, rel, e))
        st["pushed_sig"] = done_sig
        pushed_total += n_push

        # 收齐 → commit（服务器按当前所选期间落库；非当前期会返回 need_switch_period，不算错）
        try:
            r = api(cfg, "/api/bank-pull/commit", data={"period": key, "host": os.environ.get("COMPUTERNAME", "?")})
            if r.get("ok"):
                st["committed"] = True
                if r.get("skipped"):
                    committed.append("%s（服务器已有人工数据，未覆盖）" % key)
                else:
                    committed.append("%s（并入%s笔%s）" % (key, r.get("并入笔数", "?"),
                                     "·待确认重复" if r.get("need_dup_confirm") else ""))
            elif r.get("need_switch_period"):
                waiting.append("%s（文件已推达，待服务器切到该期再解析）" % key)
            else:
                waiting.append("%s（%s）" % (key, r.get("msg", "commit未成功")))
        except Exception as e:
            log("   [X] commit 失败 %s：%s" % (key, e))

    try:
        with open(STATEF, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
    except Exception:
        pass

    if pushed_total or committed or waiting or forced:
        log("扫描 %d 个月目录 · 本轮推送 %d 个文件 · 提交 %s · 等待 %s"
            % (len(months), pushed_total, "，".join(committed) or "无", "，".join(waiting) or "无"))
    _report(cfg, {"scanned": len(months), "pushed": pushed_total,
                  "committed": committed, "waiting": waiting,
                  "host": os.environ.get("COMPUTERNAME", "?"), "src_root": cfg["src_root"]})


def _report(cfg, payload):
    try:
        api(cfg, "/api/bank-pull/report", data=payload)
    except Exception as e:
        log("[!] 回执没发出去（不影响已推文件）：%s" % e)


if __name__ == "__main__":
    main()
