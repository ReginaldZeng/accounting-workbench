# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-08-05 | Author: Claude / c | Version: V2.172
# Description: 财务核算工作台 · 共享内核（从 app.py 拆出）。
#              放「各工具线都要用」的东西：路径常量 · 全局配置 CFG · 会计期间 · 缓存 ·
#              期间封存拦截 · 金蝶取数定格 · 登录/权限判定。
#
#              依赖方向单向：app.py → routers/* → core.py。core.py 不 import 任何 router，
#              也不 import app，因此永远不会出现循环引用。
#
#              为什么拆：app.py 原为 5006 行单文件，所有工具线的路由挤在一起，最近 60 次提交
#              被改 18 次——多条需求并行开发时它是头号合并冲突源。拆分后各工具线各改各的
#              routers/<线>.py，本文件只在「平台级」改动时才动。
#
#              注意：本模块的 CFG 等可变全局全部「原地改」(CFG.update)，全库无 global 重绑定，
#              因此 `from core import CFG` 拿到的始终是同一个对象。新增可变全局请守住这条，
#              否则各 router 会拿到过期副本。
import os
import re
import json
import datetime

from fastapi.responses import JSONResponse

from kernels import account_ledger as al
import sample_data as S
import db

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "sample_data")
LEDGER_PATH = os.path.join(DATA_DIR, "ledger.json")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
AUTH_LEDGER_PATH = os.path.join(DATA_DIR, "ledger_authoritative.json")

OVERRIDES_PATH = os.path.join(DATA_DIR, "account_overrides.json")   # 本地手工覆盖(失效)，不写金蝶
CLAIMS_PATH = os.path.join(DATA_DIR, "claims.json")                 # 逐笔差异·认领/处理状态(本地持久化)

DIST = os.path.join(BASE, "static")

os.makedirs(DATA_DIR, exist_ok=True)
_DEFAULT_CFG = {"source": os.environ.get("KD_SOURCE", "sample"), "year": 2026, "period": 6,
                "bank_import_dir": os.environ.get("KD_BANK_DIR", "")}   # 银行流水导入目录(逐笔稽核用)


def load_cfg():
    if os.path.exists(CONFIG_PATH):
        try:
            return {**_DEFAULT_CFG, **json.load(open(CONFIG_PATH, encoding="utf-8"))}
        except Exception:
            pass
    return dict(_DEFAULT_CFG)


def save_cfg(cfg):
    json.dump(cfg, open(CONFIG_PATH, "w", encoding="utf-8"), ensure_ascii=False)


CFG = load_cfg()
if CFG.get("source") == "sample" and (CFG.get("year"), CFG.get("period")) != (2026, 6):
    CFG["year"], CFG["period"] = 2026, 6      # 样例固定演示 6 月：纠正历史 config 里 sample+5月 的错位
    save_cfg(CFG)


def _period_str():
    return f"{CFG['year']}-{CFG['period']:02d}"


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


# ---------------- 缓存（进入秒开：GET 读缓存，仅 /sync 才重算/取数）----------------
_FUND_CACHE: dict = {}
_RECON_CACHE: dict = {}
_DS_CACHE: dict = {}
_BADJ_CACHE: dict = {}
_CH_CACHE: dict = {}
_SBAL_CACHE: dict = {}
_WR_CACHE: dict = {}    # 理财对账（含OCR，重，只 /sync 触发）


def _cache_key():
    return (CFG["source"], CFG["year"], CFG["period"])


def _cache_get(cache, producer, force=False):
    key = _cache_key()
    if not force and key in cache:
        d = dict(cache[key]); d["cached"] = True
        return d
    d = producer()
    cache[key] = d
    _SYNC_AT[key] = _now()          # producer 真跑了 = 这一刻从金蝶取回了数据
    out = dict(d); out["cached"] = False
    return out


def _cache_clear():
    _FUND_CACHE.clear(); _RECON_CACHE.clear(); _DS_CACHE.clear(); _BADJ_CACHE.clear(); _CH_CACHE.clear()
    _SBAL_CACHE.clear(); _WR_CACHE.clear()


# ---------------- 月结批次 / 期间封存 ----------------
# 封存 = 本月对账做完、把结果拍照存死。之后该期只读：不取金蝶、不能改认领/未达原因、不能撤销计提凭证。
# 没有"开启新一期"的动作——上一期封存后，下一期天然是进行中（与金蝶期末结账同理）。
_SYNC_AT: dict = {}     # (源,年,期) → 该期数据最近一次真从金蝶取回的时点；封存时记进期间表，底稿可追溯到那一版数据


def _closed_info(year=None, period=None):
    return db.get_period(CFG["source"], year or CFG["year"], period or CFG["period"])


def _period_data_status(year=None, period=None):
    """本期数据状态一句话（期间选择器旁的胶囊）：已封存 / 数据已上传 / 数据未上传 / 样例数据。"""
    y, p = int(year or CFG["year"]), int(period or CFG["period"])
    if db.is_closed(CFG["source"], y, p):
        return "已封存"
    if CFG["source"] == "sample":
        return "样例数据"
    has_bank = db.period_input_meta(CFG["source"], y, p, "bank") is not None
    has_kd = any(db.period_input_meta(CFG["source"], y, p, "kd:" + ck)
                 for ck in ("gl_voucher:1002", "gl_balance"))
    return "数据已上传" if (has_bank or has_kd) else "数据未上传"


def _is_closed(year=None, period=None):
    return db.is_closed(CFG["source"], year or CFG["year"], period or CFG["period"])


def _closed_block(year=None, period=None):
    """已封存期间的写入/取数一律拦下。返回 JSONResponse=拦截；None=放行。"""
    y, p = int(year or CFG["year"]), int(period or CFG["period"])
    if not db.is_closed(CFG["source"], y, p):
        return None
    i = db.get_period(CFG["source"], y, p)
    return JSONResponse({"ok": False, "closed": True,
                         "msg": "%d年%d期已封存（%s 于 %s 封存），本期只读。如需改动，请先到「月结看板」解封。"
                                % (y, p, i.get("封存人", "") or "?", i.get("封存时间", "") or "?")},
                        status_code=409)


# ---------------- 按期间存的输入数据：银行流水上传一次 / 金蝶取数一次，之后进页面直接读 ----------------
# 语义变化（V2.68）：页面进入不再自动取数/解析。有权限的人【刷新金蝶数据】/【上传流水】才更新本期数据；
# 没取过数就明说"本期未取数"（KdNotFetched），不再拿别的月或样例凑，也不再每次进来重跑。
SAMPLE_YM = (2026, 6)          # 样例=演示数据，固定 2026 年 6 月（选样例源时期间锁到这里）


class KdNotFetched(Exception):
    """本期还没从金蝶取过这类数（需先点「刷新金蝶数据」）。各页捕获后给友好提示，不 500。"""
    def __init__(self, call_key):
        self.call_key = call_key
        super().__init__(call_key)


def _kd_get(call_key):
    """读本期已定格的金蝶数据；没取过 → 抛 KdNotFetched。只有 refresh 端点会真取数并落库。"""
    rec = db.get_period_input(CFG["source"], CFG["year"], CFG["period"], "kd:" + call_key)
    if rec is None:
        raise KdNotFetched(call_key)
    return rec["payload"].get("rows", [])


def _kd_fetch_store(call_key, real_fetch, operator=""):
    """真去金蝶取一次并落库（仅 refresh 用）。返回笔数。"""
    rows = real_fetch()
    db.set_period_input(CFG["source"], CFG["year"], CFG["period"], "kd:" + call_key,
                        {"rows": rows}, {"call": call_key, "笔数": len(rows)}, operator)
    return len(rows)


def _kd_sync_info():
    """本期金蝶数据 谁在何时刷的（各 kd 项里最新那次）。没取过 → 空串。
    V2.185(原july线V2.176) 需求方定：凡刷新都要看得到"XXX 于 何时 刷新"，点了没反馈会以为没效果。"""
    best = {"at": "", "by": ""}
    for ck in ("gl_voucher:1002", "gl_balance", "gl_voucher:1012", "gl_subjects"):
        m = db.period_input_meta(CFG["source"], CFG["year"], CFG["period"], "kd:" + ck)
        if m and m["updated_at"] > best["at"]:
            best = {"at": m["updated_at"], "by": m["updated_by"] or ""}
    return best


def _kd_synced_at():
    """本期金蝶数据取自何时（取各 kd 项里最新那个时点）。没取过返回空。"""
    return _kd_sync_info()["at"]


def _period_bank():
    """本期已上传并解析好的银行流水 → (rows, manifest, meta_dict)；本期没上传过 → (None, [], None)。"""
    rec = db.get_period_input(CFG["source"], CFG["year"], CFG["period"], "bank")
    if rec is None:
        return None, [], None
    p = rec["payload"]
    return p.get("rows", []), p.get("manifest", []), {"updated_by": rec["updated_by"],
                                                      "updated_at": rec["updated_at"], **(rec.get("meta") or {})}


def _seed_ledger():
    if not os.path.exists(LEDGER_PATH):
        recs, _ = al.sync_ledger([], al.kd_accounts_from_cn(S.sample_kd_accounts_may()), "2026-05")
        al.save_ledger(LEDGER_PATH, recs)


_seed_ledger()


def _migrate_json_to_db():
    """一次性平滑迁移：旧版 JSON(账户覆盖/认领) 若存在且 DB 为空，导入 DB。升级不丢本地数据。"""
    try:
        if os.path.exists(OVERRIDES_PATH) and not db.load_overrides():
            ov = json.load(open(OVERRIDES_PATH, encoding="utf-8"))
            if ov:
                db.save_overrides(ov)
        if os.path.exists(CLAIMS_PATH) and not db.load_claims():
            for k, v in (json.load(open(CLAIMS_PATH, encoding="utf-8")) or {}).items():
                db.set_claim(k, v.get("状态"), v.get("操作人"), v.get("时间"), v.get("备注", ""))
    except Exception:
        pass


_migrate_json_to_db()
db.seed_admin()
db.seed_portal_tools()
db.seed_orgs()      # 主体档案：把物流计提原硬编码的三条 主体→账簿代码 幂等迁入
db._backfill_missing_perms()   # 给存量账号补齐新加的权限码(bp:board:*)，为 Nginx 透传做准备


def sid_name(request):
    """会话 cookie 名按端口隔离（V2.196）。浏览器 cookie 不分端口——本机多实例并行
    （8000 主服务 / 8082 工作树…）时同名 sid 互相顶掉：在 A 端口登录、切 B 端口标签再回来，
    A 的会话已被 B 覆盖 → 401（2026-08-06 实测：bills-parse 连续 401 而维表 GET 正常即此因）。
    非默认端口 → sid_{port}；默认端口(80/443/反代无端口) → 沿用 sid（服务器部署零变化）。"""
    p = request.url.port
    return f"sid_{p}" if p and p not in (80, 443) else "sid"


def _current_user(request):
    # 先读本端口专名；兜底读旧名 sid（平滑过渡：token 是 64 位随机串，别家实例的 sid 在本库查无即 None，无碰撞风险）
    return db.session_user(request.cookies.get(sid_name(request)) or request.cookies.get("sid"))


def _user_public(u):
    """对前端暴露的用户信息：含细粒度权限(管理员=全能力) + 账号管理分级范围。
    V2.149：子管理员的 perms 把其管辖工作台的全部功能码并成 True（除 enter_settings）——
    与 db.user_can 同口径，前端侧栏/按钮不用另写判定就全部跟上。"""
    perms = {k: True for k in db.caps()} if u.get("role") == "admin" else db.parse_perms(u.get("perms"))
    ws = db.managed_workspaces(u)
    if u.get("role") != "admin" and ws:
        perms = {**perms, **{c: True for c in db.sub_admin_caps(u)}}
    return {"name": u["name"], "role": u["role"], "grp": u["grp"], "perms": perms,
            "is_super": db.is_super(u),                        # 主管理员
            "can_admin": db.can_admin_accounts(u),             # 能进账号管理(主管理员或子管理员)
            "managed_ws": ws,                                  # 可管的工作台 key 列表
            "managed_ws_label": [db.WS_LABEL[w] for w in ws],
            "must_change_pwd": bool(u.get("must_change_pwd"))}  # V2.330 首登强制改密（App 据此弹改密页）


def _require_perm(request, cap):
    """校验当前登录用户是否有某能力；有→返回 user，无→None（调用方回 403）。"""
    u = _current_user(request)
    return u if db.user_can(u, cap) else None


_OPEN_API = {"/api/login", "/api/logout", "/api/me", "/api/health"}

# ── 机器取件通道（V2.241）──────────────────────────────────
# 报表导出的文件要落到办公室内网 NAS，而服务器在公网、够不着内网。
# 解法是反过来：内网一台常开电脑**主动出来取**（连接由内到外，办公室不用开任何入口）。
# 那台电脑没有登录会话，只揣一个**取件令牌**，所以下面这几个接口要放它过登录门——
# 但**不是无条件放行**：令牌必须已配置且完全一致，否则照旧走登录门。
# 令牌只能下载已导出的报表，登录不了工作台、动不了别的任何东西（爆炸半径就这么大）。
_PULL_PATHS = {"/api/rptexport/files", "/api/rptexport/download",
               "/api/rptexport/sync-report", "/api/rptexport/pending"}


def pull_token():
    """取件令牌：conf.ini [rptexport] pull_token。**未配置＝空串＝该通道整个关闭**，不是默认放行。

    ⚠ 令牌必须是**纯 ASCII**：它走 HTTP 请求头传，请求头不能承载非 ASCII 字符。
      放中文进去会静默失效——表现成"令牌明明一模一样却一直 401"，极难查（V2.241 联调实际踩过）。
      故此处把非 ASCII 的令牌当作**未配置**处理，宁可整条通道关掉，也不留一个时灵时不灵的闸。"""
    try:
        import configparser
        import kingdee_client as _kc
        c = configparser.ConfigParser()
        c.read(_kc.conf_path(), encoding="utf-8")
        tok = (c.get("rptexport", "pull_token", fallback="") or "").strip()
        return tok if tok.isascii() else ""
    except Exception:
        return ""


def dev_users_info():
    """能进「开发中」模块的账号名单：conf.ini `[nav] dev_users`（逗号/分号/顿号隔开）。
    返回 {"names": [...], "note": "人话说明服务器到底读到了什么", "path": conf.ini 实际路径}。

    **留空＝回落到"所有主管理员"**，不是"谁都能进"——空名单绝不放行任何非管理员。

    ⚠ 为什么放 conf.ini 而不是数据库/前端：这份名单的用途是**把范围收得比主管理员还窄**。
      一旦它能在页面上改，任何一个主管理员都能顺手把自己加回去——那这个限制就等于没有。
      放 conf.ini＝只有进得了服务器的人能改，正好和"指定开发者"这个语义对齐。
      （同一条边界：密钥/口令在 conf.ini，非机密设置才进 DB，见 [smtp] / [notify]。）

    ⚠ 为什么要回 note 而不是只回名单：这个闸**读不到配置时是放宽的**（回落全体主管理员）。
      放宽本身合理（免得配错就谁都进不去），但**如果不说明原因，现场表现是"我明明配了却不生效"，
      而页面上看不出任何线索**——V2.242 联调时实际卡在这里。故把"服务器读到了什么"直接摆到页面上。"""
    import configparser
    import kingdee_client as _kc
    p = _kc.conf_path()
    if not p:
        return {"names": [], "note": "服务器上找不到 conf.ini —— 名单未生效，当前回落「全体主管理员可进」", "path": ""}
    try:
        c = configparser.ConfigParser()
        c.read(p, encoding="utf-8")
    except Exception as e:
        return {"names": [], "note": "conf.ini 读不出来（%s）—— 名单未生效，当前回落「全体主管理员可进」"
                                     % str(e)[:80], "path": p}
    if not c.has_section("nav"):
        return {"names": [], "note": "conf.ini 里没有 [nav] 段 —— 当前回落「全体主管理员可进」", "path": p}
    raw = (c.get("nav", "dev_users", fallback="") or "").strip()
    names = sorted({x.strip() for x in re.split(r"[,;，；、]", raw) if x.strip()})
    if not names:
        return {"names": [], "note": "[nav] dev_users 是空的 —— 当前回落「全体主管理员可进」", "path": p}
    return {"names": names, "note": "只有这 %d 个账号进得去" % len(names), "path": p}


def dev_users():
    return set(dev_users_info()["names"])


def can_enter_dev(u):
    """这个人能不能进「开发中」的模块。"""
    if not u:
        return False
    names = dev_users()
    return (u.get("name") in names) if names else bool(db.is_super(u))


def pull_token_ok(request):
    """请求是否持有正确的取件令牌。空令牌恒 False——防止"没配 = 空 == 空 = 放行"这种经典失效。"""
    tok = pull_token()
    return bool(tok) and request.headers.get("X-Pull-Token", "") == tok

# 金蝶凭证状态码→中文。物流计提与汇率录入都要显示它，故留 core。
_KD_STATUS_CN = {"Z": "暂存", "A": "草稿", "B": "已提交", "C": "已审核", "D": "重新审核"}


# ---------------- 本实例「是谁」：版本 · 分支 · 提交（V2.176） ----------------
# 并行开发后每台机器可能同时跑好几条线，界面必须能自证身份，否则「改了却看不到」
# 根本查不动（实战教训：业务方盯着 main 找另一条分支的功能找了一下午）。
# 取数优先级：git（开发机——工作树里 git -C 任何子目录都好使）
#          → version_stamp.json（服务器部署包没有 .git，打包时可写入同名戳记）
#          → 全空（老部署包，界面只显示端口）。
def _git(*args):
    import subprocess
    try:
        out = subprocess.run(["git", "-C", BASE, *args], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=3)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _ver_key(v):
    import re
    m = re.match(r"[Vv](\d+)\.(\d+)", (v or "").strip())
    return (int(m.group(1)), int(m.group(2))) if m else (-1, -1)


def _compute_version_info():
    import re
    info = {"ver": "", "branch": "", "commit": "", "dirty": False}
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if branch:
        info["branch"] = branch
        info["commit"] = _git("rev-parse", "--short", "HEAD")
        info["dirty"] = bool(_git("status", "--porcelain"))
        # 版本号 = max(台账片段最大号, 最近提交标题里的 V 号)——片段是正编制，提交标题是兜底
        # 扫最近 30 条提交标题（不止 HEAD）：合并到 main 后 HEAD 常是无 V 号的 merge 提交，
        # 只看 HEAD 会把版本号漏回台账旧片段号（实翻车：页脚卡 V2.422）
        cands = []
        for line in _git("log", "-30", "--format=%s").split("\n"):
            m = re.search(r"[Vv]\d+\.\d+", line)
            if m:
                cands.append(m.group(0))
        frag_dir = os.path.join(BASE, "..", "..", "..", "00_Change_Log")
        if os.path.isdir(frag_dir):
            for fn in os.listdir(frag_dir):
                m = re.match(r"(V\d+\.\d+)_", fn)
                if m:
                    cands.append(m.group(1))
        if cands:
            info["ver"] = max(cands, key=_ver_key)
        return info
    stamp = os.path.join(BASE, "version_stamp.json")   # 服务器部署包：打包时写入
    if os.path.exists(stamp):
        try:
            d = json.load(open(stamp, encoding="utf-8"))
            info.update({k: d.get(k, info[k]) for k in ("ver", "branch", "commit")})
        except Exception:
            pass
    return info


VERSION_INFO = _compute_version_info()
