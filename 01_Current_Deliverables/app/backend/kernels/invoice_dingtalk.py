# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家）
# Description: 【发票管家】钉钉内核——扫审批单取单、按审批编号找单、规范化表单、拉附件/图片栏、H5 免登换身份、
#              发提醒、通讯录花名册。只读取数（发消息只在 send_text 一处，且未配置时绝不发）。
#              复用 dingtalk_bom（附件递归扫描、下载链接、发起人离职时的钉盘代下载）与 notifier（配置、发消息、花名册）；
#              不 import db / core——路由层负责落库与权限。
#              对外一律不抛：失败回 {ok:False, msg:人话}，错误串先抹掉 appkey/appsecret/access_token 再外露。
#              实证（2026-09-23/24）：
#                · aflow 短链接免登录 GET 回 ~326 字节 HTML，内含 qrTargetUrl（带 corpid= 与 procInstId=），再 location.href 跳走；
#                · 审批编号 21 位，前 12 位＝创建时刻 YYYYMMDDHHMM（北京时间），可把 listids 窗口缩到分钟级；
#                · 标题＝「<申请人>提交的<模板名>」；表单值可能是字符串 "null"；明细/银行信息是 TableField 的 JSON 串。
#              审查修复（同日）：按审批编号找单一次最多逐张取 200 张、整天兜底最多 60 秒，到顶明说"没找到、请扫二维码"，
#                取过的实例编号缓存 1 小时（重扫接着查）；扫码原文里的 corpid 只认钉钉域名的链接。

import datetime
import html as _html
import json
import re
import threading
import time
from urllib.parse import unquote, urlsplit

try:
    import requests
except Exception:                        # 干净环境没装 requests：钉钉通道整体关掉，不拖垮导入
    requests = None

try:
    import notifier                      # conf.ini [dingtalk] 读取、发消息、花名册
except Exception:
    notifier = None

try:
    from kernels import dingtalk_bom as _bom
except Exception:
    try:
        import dingtalk_bom as _bom      # 以 kernels 目录为 sys.path 跑时的兜底
    except Exception:
        _bom = None

OAPI = "https://oapi.dingtalk.com/"
VAPI = "https://api.dingtalk.com"
CN_TZ = datetime.timezone(datetime.timedelta(hours=8))   # 审批编号里的时刻是北京时间，不看服务器本地时区

ATTACH_CAP = 30 * 1024 * 1024            # 单个附件上限 30MB
PHOTO_CAP = 20 * 1024 * 1024             # 单张图片上限 20MB
PHOTO_HOSTS = ("dingtalk.com", "alicdn.com", "aliyuncs.com", "dingtalkapps.com")
DEFAULT_AMOUNT_FIELDS = ["实际付款总额", "付款总额", "报销总额", "合计金额", "报销金额", "实际报销金额"]
_TOKEN_BAD = (40001, 40014, 42001, 88, 40089)   # token 过期/失效 → 清缓存重取一次

_LOCK = threading.Lock()
_TOKENS = {}                             # (kind, appkey) → (token, 过期时刻)
_PC_CACHE = {}                           # 模板名 → (process_code, 过期时刻)
_ROSTER = {"ts": 0.0, "rows": None}
ROSTER_TTL = 30 * 60
# 按审批编号找单：整天兜底要逐张取单比编号（钉钉 listids 不回编号），单子多的日子一次能取几百上千张。
# 一次查找最多逐张取 FIND_MAX_GETS 张、整天兜底最多花 FIND_BUDGET_S 秒；取过的「实例→编号」记 1 小时，
# 重扫同一天时不重复取、接着往下查。
FIND_MAX_GETS = 200
FIND_BUDGET_S = 60
DAY_LIST_MAX_PAGES = 30                  # 整天兜底每个模板最多翻 30 页（600 张）
_BID_TTL = 3600
_BID_CACHE = {}                          # 实例 ID → (审批编号, 过期时刻)
_BID_CACHE_MAX = 20000


# ───────────────────────── 基础：配置 / 抹密钥 / token ─────────────────────────

def _load_conf():
    return notifier.load_dingtalk_conf() if notifier else None


def configured():
    """conf.ini [dingtalk] 配齐且装了 requests → True。"""
    try:
        return bool(requests and _load_conf())
    except Exception:
        return False


_NOT_CONF = "未配置钉钉（conf.ini [dingtalk]），无法从钉钉取单"


def _clean(msg, conf=None):
    """外露前抹掉 appkey/appsecret（复用 dingtalk_bom._scrub）+ access_token（oapi 把它放在 URL query 里，网络异常串会带出来）。"""
    ak = sk = None
    if conf:
        ak, sk = conf.get("appkey"), conf.get("appsecret")
    s = _bom._scrub(msg, ak, sk) if _bom else str(msg or "")
    s = re.sub(r"(access_token|accessToken)=[^&\s\"']+", r"\1=***", s)
    with _LOCK:
        toks = [v[0] for v in _TOKENS.values() if v and v[0]]
    for t in toks:
        s = s.replace(str(t), "***")
    return s[:300]


def _token(conf, kind="old", force=False):
    """带缓存的 token（dingtalk_bom._token 每次都请求 gettoken，高频扫单扛不住）。提前 5 分钟换新。
    kind=old → oapi（审批查询/下载/免登）；kind=v2 → api.dingtalk.com v1.0（新版下载接口）。"""
    key = (kind, conf.get("appkey"))
    now = time.time()
    with _LOCK:
        hit = _TOKENS.get(key)
        if hit and not force and hit[1] > now + 300:
            return hit[0]
    if kind == "old":
        r = requests.get(OAPI + "gettoken", params={"appkey": conf["appkey"], "appsecret": conf["appsecret"]},
                         timeout=20).json()
        if r.get("errcode") != 0:
            raise RuntimeError("钉钉 gettoken 失败：%s" % (r.get("errmsg") or r.get("errcode")))
        tok, ttl = r["access_token"], int(r.get("expires_in") or 7200)
    else:
        r = requests.post(VAPI + "/v1.0/oauth2/accessToken",
                          json={"appKey": conf["appkey"], "appSecret": conf["appsecret"]}, timeout=20).json()
        tok = r.get("accessToken")
        if not tok:
            raise RuntimeError("钉钉 v1.0 accessToken 失败：%s" % (r.get("message") or r.get("code") or "无返回"))
        ttl = int(r.get("expireIn") or 7200)
    with _LOCK:
        _TOKENS[key] = (tok, now + ttl)
    return tok


def _drop_token(conf, kind="old"):
    with _LOCK:
        _TOKENS.pop((kind, conf.get("appkey")), None)


def _oapi(conf, path, body, timeout=20):
    """调老版 topapi；token 失效时清缓存重试一次。返回解析后的 JSON dict。"""
    for attempt in (0, 1):
        tok = _token(conf, "old", force=bool(attempt))
        r = requests.post(OAPI + path, params={"access_token": tok}, json=body, timeout=timeout).json()
        if attempt == 0 and r.get("errcode") in _TOKEN_BAD:
            _drop_token(conf, "old")
            continue
        return r
    return r


def _errtext(r):
    return "%s %s" % (r.get("errcode", ""), r.get("errmsg") or r.get("message") or "")


# ───────────────────────── 扫审批单：短链接 → procInstId ─────────────────────────

_RX_INST = re.compile(r"procInstId=([A-Za-z0-9_\-]+)", re.I)
_RX_CORP = re.compile(r"corpid=([A-Za-z0-9_\-]+)", re.I)


def _pick_ids(text):
    """从 URL / HTML 里抠 procInstId 与 corpid（先 HTML 反转义、再 URL 解码两轮，防 &amp; 与 %3D 包裹）。"""
    s = _html.unescape(str(text or ""))
    s = unquote(unquote(s))
    m1, m2 = _RX_INST.search(s), _RX_CORP.search(s)
    return (m1.group(1) if m1 else ""), (m2.group(1) if m2 else "")


def _is_dt_host(host):
    host = (host or "").lower()
    return host == "dingtalk.com" or host.endswith(".dingtalk.com")


def _on_dt_host(u):
    try:
        sp = urlsplit(u)
    except Exception:
        return False
    return sp.scheme in ("http", "https") and _is_dt_host(sp.hostname)


def resolve_link(url):
    """审批单二维码/短链接 → {ok, procInstId, corpId, msg}。
    已带 procInstId= 的完整链接直接抠；aflow 短链接免登录 GET 一次（15 秒超时，不跟随跳转——跳去登录页会丢参数）。
    corpId 只认钉钉自家域名的链接或钉钉返回的内容：外站随手拼个 ?corpid= 的链接不能带出企业 ID
    （路由会把它记成全局设置，手机免登靠它）。"""
    u = str(url or "").strip()
    if not u:
        return {"ok": False, "procInstId": "", "corpId": "", "msg": "没有收到审批单链接"}
    iid, corp = _pick_ids(u)
    if iid:
        return {"ok": True, "procInstId": iid, "corpId": corp if _on_dt_host(u) else "", "msg": ""}
    try:
        sp = urlsplit(u)
    except Exception:
        sp = None
    if not sp or sp.scheme not in ("http", "https") or not _is_dt_host(sp.hostname):
        return {"ok": False, "procInstId": "", "corpId": "", "msg": "这不是钉钉审批单的链接"}
    if not requests:
        return {"ok": False, "procInstId": "", "corpId": "", "msg": "服务器缺 requests 组件，无法解析审批单链接"}
    try:
        if sp.scheme == "http":
            u = "https://" + u[len("http://"):]
        resp = requests.get(u, timeout=15, allow_redirects=False,
                            headers={"User-Agent": "Mozilla/5.0 (Linux; Android 12) DingTalk"})
        loc = (resp.headers or {}).get("Location") or (resp.headers or {}).get("location") or ""
        iid, corp = _pick_ids(loc)
        if not iid:
            body = resp.text if len(resp.content or b"") <= 512 * 1024 else resp.text[:512 * 1024]
            iid, corp2 = _pick_ids(body)
            corp = corp or corp2
        if iid:
            return {"ok": True, "procInstId": iid, "corpId": corp, "msg": ""}
        return {"ok": False, "procInstId": "", "corpId": corp,
                "msg": "链接打开了，但里面没有审批单号（可能二维码已失效，或不是审批单二维码）"}
    except Exception as e:
        return {"ok": False, "procInstId": "", "corpId": "", "msg": "打开审批单链接失败：%s" % _clean(e)}


# ───────────────────────── 取单 ─────────────────────────

def get_instance(proc_inst_id):
    """按实例 ID 取审批单（topapi/processinstance/get）→ {ok, inst, msg}。"""
    iid = str(proc_inst_id or "").strip()
    if not iid:
        return {"ok": False, "inst": None, "msg": "缺审批实例号"}
    conf = _load_conf() if requests else None
    if not conf:
        return {"ok": False, "inst": None, "msg": _NOT_CONF}
    try:
        r = _oapi(conf, "topapi/processinstance/get", {"process_instance_id": iid})
        if r.get("errcode") != 0:
            return {"ok": False, "inst": None, "msg": "钉钉没给这张审批单：%s" % _clean(_errtext(r), conf)}
        inst = r.get("process_instance") or r.get("result")
        if not isinstance(inst, dict):
            return {"ok": False, "inst": None, "msg": "钉钉返回的审批单是空的"}
        return {"ok": True, "inst": inst, "msg": ""}
    except Exception as e:
        return {"ok": False, "inst": None, "msg": "从钉钉取审批单失败：%s" % _clean(e, conf)}


def _process_code(conf, name):
    """模板名 → process_code（get_by_name；未知名字 820004 → None）。缓存 12 小时。已是 PROC- 开头的直接用。"""
    nm = str(name or "").strip()
    if not nm:
        return None, "模板名为空"
    if nm.upper().startswith("PROC-"):
        return nm, ""
    now = time.time()
    with _LOCK:
        hit = _PC_CACHE.get(nm)
        if hit and hit[1] > now:
            return hit[0], ""
    r = _oapi(conf, "topapi/process/get_by_name", {"name": nm})
    if r.get("errcode") != 0:
        if r.get("errcode") == 820004:
            return None, "钉钉里没有叫「%s」的审批模板" % nm
        return None, "查模板「%s」失败：%s" % (nm, _clean(_errtext(r), conf))
    pc = r.get("process_code") or r.get("result")
    if isinstance(pc, dict):
        pc = pc.get("process_code")
    if not pc:
        return None, "钉钉里没有叫「%s」的审批模板" % nm
    with _LOCK:
        _PC_CACHE[nm] = (pc, now + 12 * 3600)
    return pc, ""


def _list_ids(conf, pc, start_ms, end_ms, userids=None, max_pages=100, deadline=None):
    """listids 翻页（next_cursor）。返回 (ids, 错误串)。deadline（time.monotonic 时刻）到了就不再翻页。"""
    out, cursor, err = [], 0, ""
    for _ in range(max_pages):
        if deadline is not None and time.monotonic() > deadline:
            break
        body = {"process_code": pc, "start_time": int(start_ms), "end_time": int(end_ms), "size": 20, "cursor": cursor}
        if userids:
            body["userid_list"] = ",".join(userids)
        r = _oapi(conf, "topapi/processinstance/listids", body)
        if r.get("errcode") != 0:
            err = _clean(_errtext(r), conf)
            break
        res = r.get("result") or {}
        out += [x for x in (res.get("list") or []) if x not in out]
        nc = res.get("next_cursor")
        if not nc:
            break
        cursor = nc
    return out, err


def _ms(dt):
    return int(dt.timestamp() * 1000)


def business_id_windows(business_id):
    """审批编号 → 查找窗口 [(start_ms, end_ms, 说明)]：先分钟窗口（前 12 位 −2 分钟 ～ +3 分钟），再整天兜底。
    20 位老编号、或前 12 位不是合法时刻 → 只有整天窗口。编号不合法 → []。纯函数，供单测。"""
    s = str(business_id or "").strip()
    if not re.fullmatch(r"\d{20,21}", s):
        return []
    try:
        day = datetime.datetime.strptime(s[:8], "%Y%m%d").replace(tzinfo=CN_TZ)
    except ValueError:
        return []
    wins = []
    try:
        minute = datetime.datetime.strptime(s[:12], "%Y%m%d%H%M").replace(tzinfo=CN_TZ)
        wins.append((_ms(minute - datetime.timedelta(minutes=2)), _ms(minute + datetime.timedelta(minutes=3)), "minute"))
    except ValueError:
        pass
    wins.append((_ms(day), _ms(day + datetime.timedelta(days=1)) - 1, "day"))
    return wins


def _bid_get(iid):
    now = time.time()
    with _LOCK:
        hit = _BID_CACHE.get(iid)
        if hit and hit[1] > now:
            return hit[0]
    return None


def _bid_put(iid, bid):
    now = time.time()
    with _LOCK:
        if len(_BID_CACHE) >= _BID_CACHE_MAX:
            for k in [k for k, v in _BID_CACHE.items() if v[1] <= now]:
                _BID_CACHE.pop(k, None)
            if len(_BID_CACHE) >= _BID_CACHE_MAX:
                _BID_CACHE.clear()
        _BID_CACHE[iid] = (bid, now + _BID_TTL)


def find_by_business_id(business_id, template_names):
    """按审批编号在指定模板里找单 → {ok, procInstId, inst, template, msg}。
    分钟窗口逐模板 listids → 逐个 get 比 business_id；分钟窗口没有再整天兜底（已看过的实例不重复取）。
    一次最多逐张取 FIND_MAX_GETS 张、整天兜底最多 FIND_BUDGET_S 秒，到顶就停下回"没找到"并请扫二维码；
    取过的实例编号缓存 1 小时（重扫不重复取，接着往下查）。"""
    bid = re.sub(r"\s+", "", str(business_id or ""))
    fail = {"ok": False, "procInstId": "", "inst": None, "template": "", "msg": ""}
    wins = business_id_windows(bid)
    if not wins:
        return dict(fail, msg="审批编号不对：应为 20～21 位数字，前 8 位是日期")
    names = [str(n).strip() for n in (template_names or []) if str(n or "").strip()]
    if not names:
        return dict(fail, msg="还没设置要查的审批模板（发票管家设置 › 审批模板）")
    conf = _load_conf() if requests else None
    if not conf:
        return dict(fail, msg=_NOT_CONF)
    try:
        pcs, notes = [], []
        for nm in names:
            pc, why = _process_code(conf, nm)
            if pc:
                pcs.append((nm, pc))
            elif why:
                notes.append(why)
        if not pcs:
            return dict(fail, msg="；".join(notes) or "审批模板都没找到")
        seen = set()
        gets, capped, more = 0, "", False        # capped：many＝逐张取到上限；slow＝整天兜底超时
        deadline = time.monotonic() + FIND_BUDGET_S
        for st, et, kind in wins:
            day = kind == "day"
            for nm, pc in pcs:
                if capped:
                    break
                if day and time.monotonic() > deadline:
                    capped = "slow"
                    break
                ids, err = _list_ids(conf, pc, st, et, max_pages=DAY_LIST_MAX_PAGES if day else 100,
                                     deadline=deadline if day else None)
                if day and len(ids) >= DAY_LIST_MAX_PAGES * 20:
                    more = True                   # 当天这个模板的单子没翻完
                if err:
                    notes.append("列「%s」的审批单失败：%s" % (nm, err))
                for iid in ids:
                    if iid in seen:
                        continue
                    seen.add(iid)
                    known = _bid_get(iid)
                    if known is not None and known != bid:
                        continue                  # 之前取过、编号对不上：不再取
                    if gets >= FIND_MAX_GETS:
                        capped = "many"
                        break
                    if day and time.monotonic() > deadline:
                        capped = "slow"
                        break
                    gets += 1
                    r = _oapi(conf, "topapi/processinstance/get", {"process_instance_id": iid})
                    inst = (r.get("process_instance") or r.get("result")) if r.get("errcode") == 0 else None
                    if isinstance(inst, dict):
                        got = str(inst.get("business_id") or "")
                        _bid_put(iid, got)
                        if got == bid:
                            return {"ok": True, "procInstId": iid, "inst": inst, "template": nm, "msg": ""}
            if capped:
                break
        if not capped and wins[-1][2] == "day" and time.monotonic() > deadline:
            capped = "slow"                      # 翻页翻到一半超时：列出来的查完了，但当天没翻完
        if not capped and more:
            capped = "many"
        names_txt = "、".join(n for n, _ in pcs)
        if capped:
            why = "当天的审批单太多" if capped == "many" else "钉钉这会儿响应慢"
            msg = ("「%s」%s，逐张查了 %d 张还没找到审批编号 %s 的单子，先停下了；"
                   "请直接扫审批单上的二维码（或核对编号后再试一次，会接着往下查）" % (names_txt, why, gets, bid))
        else:
            msg = "在「%s」里没找到审批编号 %s 的单子（核对编号，或确认模板已加进设置）" % (names_txt, bid)
        if notes:
            msg += "；" + "；".join(notes)
        return dict(fail, msg=msg)
    except Exception as e:
        return dict(fail, msg="按审批编号找单失败：%s" % _clean(e, conf))


# ───────────────────────── 规范化表单 ─────────────────────────

def _nul(v):
    """钉钉空值有 None / "" / "null" / "[]" 几种写法，统一成 None。"""
    if v is None:
        return None
    if isinstance(v, str):
        t = v.strip()
        if t in ("", "null", "None", "[]", "{}", "undefined"):
            return None
        return t
    return v


def _json(v):
    v = _nul(v)
    if isinstance(v, str) and v[:1] in ("[", "{"):
        try:
            return json.loads(v)
        except Exception:
            return None
    return v


def _money(v):
    """金额串 → float；带千分位/¥/元也认；认不出 → None。"""
    v = _nul(v)
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[,，¥￥元\s]", "", str(v))
    try:
        return float(s)
    except ValueError:
        return None


def _table_rows(value):
    """TableField 值 → [[{label, value, type}]]。兼容 rowValue 包裹与直接列表两种形态。"""
    data = _json(value)
    rows = []
    if not isinstance(data, list):
        return rows
    for row in data:
        cells = row.get("rowValue") if isinstance(row, dict) else row
        if not isinstance(cells, list):
            continue
        out = []
        for c in cells:
            if isinstance(c, dict):
                out.append({"label": str(c.get("label") or c.get("name") or ""),
                            "value": c.get("value"),
                            "type": str(c.get("componentType") or c.get("component_type") or "")})
        rows.append(out)
    return rows


def _cell_text(v):
    v = _nul(v)
    if v is None:
        return ""
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    return str(v).strip()


def _classify_bank_label(label):
    """银行信息格子归类：account > branch > payee > bank（「银行账户名」要归户名，「银行所属支行」要归支行）。"""
    lb = label or ""
    if any(k in lb for k in ("方式", "类型", "省", "市", "地区", "城市")):   # 「收款方式」「收款银行所在省市」不是户名/银行
        return ""
    if "账号" in lb or "卡号" in lb:
        return "account"
    if "支行" in lb:
        return "branch"
    if any(k in lb for k in ("收款方名称", "银行账户名", "账户名", "收款人", "户名", "收款单位", "收款方")):
        return "payee"
    if any(k in lb for k in ("所属银行", "开户行", "开户银行", "收款银行")):
        return "bank"
    return ""


_ROW_MONEY_PRI = ("实际报销金额", "报销金额", "实际付款金额", "付款金额", "金额")


def _row_money(cells):
    """明细行取一个金额格（优先「报销金额」，避免同一行「金额」「报销金额」重复相加）。"""
    cands = [c for c in cells if ("金额" in c["label"] and "大写" not in c["label"]) or c["type"] == "MoneyField"]
    if not cands:
        return None
    for key in _ROW_MONEY_PRI:
        for c in cands:
            if key in c["label"]:
                m = _money(c["value"])
                if m is not None:
                    return m
    for c in cands:
        m = _money(c["value"])
        if m is not None:
            return m
    return None


def _tpl_cfg(templates_cfg, name):
    if isinstance(templates_cfg, dict):
        c = templates_cfg.get(name)
        return c if isinstance(c, dict) else {}
    for c in templates_cfg or []:
        if isinstance(c, dict) and c.get("name") == name:
            return c
    return {}


def _tpl_names(templates_cfg):
    if isinstance(templates_cfg, dict):
        return [str(k) for k in templates_cfg.keys()]
    return [str(c.get("name")) for c in (templates_cfg or []) if isinstance(c, dict) and c.get("name")]


def _split_title(title, templates_cfg):
    """「<申请人>提交的<模板名>」拆开；设置里有的模板名优先按后缀认（申请人名字里万一带「提交的」也不拆错）。"""
    t = str(title or "").strip()
    for nm in sorted(_tpl_names(templates_cfg), key=len, reverse=True):
        if nm and t.endswith(nm) and t[:-len(nm)].endswith("提交的"):
            return t[:-len(nm) - 3], nm
    if "提交的" in t:
        a, b = t.split("提交的", 1)
        return a.strip(), b.strip()
    return "", t


def _photo_urls(value):
    data = _json(value)
    out = []
    for x in (data if isinstance(data, list) else [data] if data else []):
        u = x.get("url") if isinstance(x, dict) else x
        if isinstance(u, str) and u.strip().lower().startswith(("http://", "https://")):
            out.append(u.strip())
    return out


def _relate_titles(value):
    data = _json(value)
    out = []
    for x in (data if isinstance(data, list) else [data] if data else []):
        t = (x.get("title") or x.get("name") or x.get("businessId")) if isinstance(x, dict) else x
        if t:
            out.append(str(t))
    return out


def _attach_names(value):
    data = _json(value)
    out = []
    for x in (data if isinstance(data, list) else []):
        if isinstance(x, dict):
            n = x.get("fileName") or x.get("file_name")
            if n:
                out.append(str(n))
    return out


def _form_value_text(ftype, value):
    """表单快照里的值：给人看的字符串，截 500 字。"""
    if ftype == "TableField":
        rows = _table_rows(value)
        s = "\n".join("；".join("%s：%s" % (c["label"], _cell_text(c["value"])) for c in r if _cell_text(c["value"]))
                      for r in rows)
    elif ftype == "DDAttachment":
        s = "、".join(_attach_names(value))
    elif ftype == "DDPhotoField":
        n = len(_photo_urls(value))
        s = ("%d 张图片" % n) if n else ""
    elif ftype == "RelateField":
        s = "、".join(_relate_titles(value))
    else:
        s = _cell_text(value)
    return s[:500]


_REASON_NAMES = ("付款事由", "报销事由", "申请事由", "事由", "付款用途", "用途", "费用说明")
_COMPANY_NAMES = ("公司主体", "付款主体", "付款公司", "费用承担公司", "所属公司", "报销公司")


def normalize_instance(inst, proc_inst_id, templates_cfg=None):
    """钉钉实例 → 票夹字段（键名见模块说明/技术方案 §3 inv_folder）。纯函数、不联网、不抛（坏结构按空处理）。"""
    inst = inst if isinstance(inst, dict) else {}
    applicant, template = _split_title(inst.get("title"), templates_cfg)
    comps = [c for c in (inst.get("form_component_values") or []) if isinstance(c, dict)]
    by_name, form, photos, relate = {}, [], [], []
    tables = []
    for c in comps:
        name = str(c.get("name") or c.get("id") or "").strip()
        ftype = str(c.get("component_type") or "")
        val = c.get("value")
        if ftype == "TextNote":           # 说明文字控件，不是填的内容
            continue
        if name and name not in by_name:
            by_name[name] = (ftype, val)
        if ftype == "DDPhotoField":
            photos += [u for u in _photo_urls(val) if u not in photos]
        elif ftype == "RelateField":
            relate += _relate_titles(val)
        elif ftype == "TableField":
            tables.append((name, _table_rows(val)))
        form.append({"name": name, "type": ftype, "value": _form_value_text(ftype, val)})

    def top(names, contains=False):
        for n in names:
            if n in by_name and _nul(by_name[n][1]) is not None:
                return _cell_text(by_name[n][1])
        if contains:
            for n in names:
                for k, (_t, v) in by_name.items():
                    if n in k and _nul(v) is not None:
                        return _cell_text(v)
        return ""

    # 金额：模板配置的顶层字段按序取第一个非空 → 明细表逐行求和 → 第一个顶层金额控件
    amount = None
    fields = _tpl_cfg(templates_cfg, template).get("amountFields") or DEFAULT_AMOUNT_FIELDS
    for n in fields:
        if n in by_name:
            m = _money(by_name[n][1])
            if m is not None:
                amount = m
                break
    if amount is None:
        total, hit = 0.0, False
        for _n, rows in tables:
            for r in rows:
                m = _row_money(r)
                if m is not None:
                    total += m
                    hit = True
        if hit:
            amount = round(total, 2)
    if amount is None:
        for _k, (t, v) in by_name.items():
            if t in ("MoneyField", "CalculateField"):
                m = _money(v)
                if m is not None:
                    amount = m
                    break

    # 收款方：顶层字段 + 各明细表里的格子一起归类，取第一个有户名或账号的行
    payee = {"payee": "", "bank": "", "branch": "", "account": ""}
    for k, (t, v) in by_name.items():
        if t == "TableField":
            continue
        kind = _classify_bank_label(k)
        if kind and not payee[kind]:
            payee[kind] = _cell_text(v)
    for _n, rows in tables:
        for r in rows:
            got = {"payee": "", "bank": "", "branch": "", "account": ""}
            for c in r:
                kind = _classify_bank_label(c["label"])
                if kind and not got[kind]:
                    got[kind] = _cell_text(c["value"])
            if got["payee"] or got["account"]:
                for kk in got:
                    if not payee[kk]:
                        payee[kk] = got[kk]
                break
    bank, branch = payee["bank"], payee["branch"]
    if branch and bank and bank not in branch:
        bank = bank + branch
    elif branch:
        bank = branch

    reason = top(_REASON_NAMES)
    if not reason:                        # 报销单常没有事由栏 → 用明细里的「费用明细/说明」拼
        bits = []
        for _n, rows in tables:
            for r in rows:
                for c in r:
                    if any(k in c["label"] for k in ("费用明细", "事由", "说明", "用途")) and _cell_text(c["value"]):
                        bits.append(_cell_text(c["value"]))
        reason = "；".join(bits)[:500]

    erp = ""
    for k, (_t, v) in by_name.items():
        if ("ERP" in k.upper() or "订单编号" in k) and _nul(v) is not None:
            erp = _cell_text(v)
            break

    atts = []
    if _bom:
        try:
            atts = _bom.collect_attachments(inst)
        except Exception:
            atts = []
    iid = str(proc_inst_id or inst.get("process_instance_id") or "")
    return {
        "instId": iid,
        "businessId": str(inst.get("business_id") or ""),
        "template": template,
        "title": str(inst.get("title") or ""),
        "applicant": applicant,
        "applicantUid": str(inst.get("originator_userid") or ""),
        "dept": str(inst.get("originator_dept_name") or ""),
        "company": top(_COMPANY_NAMES, contains=True),
        "amount": amount,
        "payeeName": payee["payee"],
        "payeeBank": bank,
        "payeeAccount": payee["account"],
        "reason": reason[:2000],
        "erpNo": erp,
        "approvalStatus": str(inst.get("status") or ""),
        "approvalResult": str(inst.get("result") or ""),
        "createTime": str(inst.get("create_time") or ""),
        "relate": relate,
        "attachments": atts,
        "photos": photos,
        "form": form,
        "hasAttachments": bool(atts or photos),
    }


# ───────────────────────── 下载 ─────────────────────────

def _read_capped(resp, cap):
    """流式读、超上限即停。返回 (bytes|None, 原因)。"""
    try:
        n = int((resp.headers or {}).get("Content-Length") or 0)
    except Exception:
        n = 0
    if n and n > cap:
        return None, "文件太大（%.1fMB，上限 %dMB）" % (n / 1048576.0, cap // 1048576)
    buf = bytearray()
    it = resp.iter_content(65536) if hasattr(resp, "iter_content") else [resp.content]
    for chunk in it:
        if not chunk:
            continue
        buf += chunk
        if len(buf) > cap:
            return None, "文件太大（超过 %dMB）" % (cap // 1048576)
    return bytes(buf), ""


_USER_GONE = ("用户不存在", "找不到该用户", "userNotExist")


def download_attachment(proc_inst_id, att, inst=None):
    """下载审批单附件 → {ok, bytes, via, msg}。先新版/老版审批下载接口；发起人账号已不存在时走钉盘代下载（同 fetch_approval）。"""
    fail = {"ok": False, "bytes": None, "via": ""}
    att = att or {}
    fid = str(att.get("fileId") or "")
    if not fid:
        return dict(fail, msg="附件缺 fileId")
    try:
        if att.get("fileSize") and int(att.get("fileSize")) > ATTACH_CAP:
            return dict(fail, msg="附件太大（超过 %dMB），请手工下载后拖进来" % (ATTACH_CAP // 1048576))
    except (TypeError, ValueError):
        pass
    conf = _load_conf() if requests else None
    if not conf or not _bom:
        return dict(fail, msg=_NOT_CONF)
    try:
        tok_old = _token(conf, "old")
        tok_v2 = _token(conf, "v2")
        iid = str(proc_inst_id)
        url, via = _bom.download_url(tok_v2, tok_old, iid, fid)
        headers = {}
        if not url and via and any(k in via for k in _USER_GONE):
            url2, headers2, via2 = _bom.storage_download(tok_v2, tok_old, iid, fid, att.get("spaceId"), inst or {})
            if url2:
                url, via, headers = url2, via2, headers2 or {}
            else:
                return dict(fail, msg="发起人钉钉账号已不存在，附件拿不到（%s）；可手工下载后拖进来" % _clean(via2 or "备用通道失败", conf))
        if not url:
            return dict(fail, msg="拿不到附件下载链接%s" % (("（%s）" % _clean(via, conf)) if via else ""))
        resp = requests.get(url, headers=headers or None, timeout=120, stream=True)
        if getattr(resp, "status_code", 200) != 200:
            return dict(fail, msg="下载附件失败：HTTP %s" % resp.status_code)
        data, why = _read_capped(resp, ATTACH_CAP)
        if data is None:
            return dict(fail, msg=why)
        return {"ok": True, "bytes": data, "via": via or "", "msg": ""}
    except Exception as e:
        return dict(fail, msg="下载附件失败：%s" % _clean(e, conf))


def _photo_host_ok(host):
    host = (host or "").lower()
    return any(host == d or host.endswith("." + d) for d in PHOTO_HOSTS)


def download_photo(url):
    """下载审批单「图片」栏的一张图 → {ok, bytes, msg}。只认钉钉/阿里云域名（防被拿来访问内网），手动跟随跳转且每跳都校验域名。"""
    fail = {"ok": False, "bytes": None}
    u = str(url or "").strip()
    if not requests:
        return dict(fail, msg="服务器缺 requests 组件，无法下载图片")
    try:
        for _hop in range(4):
            sp = urlsplit(u)
            if sp.scheme == "http" and _photo_host_ok(sp.hostname):
                u = "https://" + u[len("http://"):]      # 钉钉图片 http/https 同址，统一走 https
                sp = urlsplit(u)
            if sp.scheme != "https" or not _photo_host_ok(sp.hostname):
                return dict(fail, msg="图片地址不是钉钉的，不下载")
            resp = requests.get(u, timeout=60, stream=True, allow_redirects=False)
            code = getattr(resp, "status_code", 200)
            if code in (301, 302, 303, 307, 308):
                nxt = (resp.headers or {}).get("Location") or ""
                if not nxt:
                    return dict(fail, msg="图片地址跳转异常")
                if nxt.startswith("/"):
                    nxt = "%s://%s%s" % (sp.scheme, sp.netloc, nxt)
                u = nxt
                continue
            if code != 200:
                return dict(fail, msg="下载图片失败：HTTP %s" % code)
            data, why = _read_capped(resp, PHOTO_CAP)
            if data is None:
                return dict(fail, msg=why)
            return {"ok": True, "bytes": data, "msg": ""}
        return dict(fail, msg="图片地址跳转太多次")
    except Exception as e:
        return dict(fail, msg="下载图片失败：%s" % _clean(e))


# ───────────────────────── 身份 / 通知 / 花名册 ─────────────────────────

def userinfo_by_code(code):
    """H5 免登 authCode → {ok, userid, name, msg}（topapi/v2/user/getuserinfo）。"""
    c = str(code or "").strip()
    if not c:
        return {"ok": False, "userid": "", "name": "", "msg": "没拿到钉钉免登码"}
    conf = _load_conf() if requests else None
    if not conf:
        return {"ok": False, "userid": "", "name": "", "msg": _NOT_CONF}
    try:
        r = _oapi(conf, "topapi/v2/user/getuserinfo", {"code": c})
        if r.get("errcode") != 0:
            return {"ok": False, "userid": "", "name": "",
                    "msg": "钉钉没认出是谁（%s）" % _clean(_errtext(r), conf)}
        res = r.get("result") or {}
        uid = str(res.get("userid") or "")
        if not uid:
            return {"ok": False, "userid": "", "name": "", "msg": "钉钉没返回用户身份"}
        return {"ok": True, "userid": uid, "name": str(res.get("name") or ""), "msg": ""}
    except Exception as e:
        return {"ok": False, "userid": "", "name": "", "msg": "钉钉免登失败：%s" % _clean(e, conf)}


_TICKET = {}                             # appkey → (jsapi_ticket, 过期时刻)


def _jsapi_ticket(conf, force=False):
    """H5 页面 JSAPI 鉴权用的 jsapi_ticket（有效 2 小时，提前 5 分钟换新；token 失效时换 token 重取一次）。"""
    key = conf.get("appkey")
    now = time.time()
    with _LOCK:
        hit = _TICKET.get(key)
        if hit and not force and hit[1] > now + 300:
            return hit[0]
    r = {}
    for attempt in (0, 1):
        tok = _token(conf, "old", force=bool(attempt))
        r = requests.get(OAPI + "get_jsapi_ticket", params={"access_token": tok}, timeout=20).json()
        if attempt == 0 and r.get("errcode") in _TOKEN_BAD:
            _drop_token(conf, "old")
            continue
        break
    if r.get("errcode") != 0 or not r.get("ticket"):
        raise RuntimeError("钉钉 get_jsapi_ticket 失败：%s" % _errtext(r))
    t, ttl = r["ticket"], int(r.get("expires_in") or 7200)
    with _LOCK:
        _TICKET[key] = (t, now + ttl)
    return t


def jsapi_sign(ticket, nonce, timestamp, url):
    """钉钉 JSAPI 签名：sha1("jsapi_ticket=…&noncestr=…&timestamp=…&url=…")，url 去掉 # 及后面、先解码。"""
    import hashlib
    u = unquote(str(url or "").split("#", 1)[0])
    plain = "jsapi_ticket=%s&noncestr=%s&timestamp=%s&url=%s" % (ticket, nonce, timestamp, u)
    return hashlib.sha1(plain.encode("utf-8")).hexdigest()


def jsapi_config(url, corp_id):
    """手机页调钉钉扫码等 JSAPI 前的 dd.config 参数 → {ok, agentId, corpId, timeStamp, nonceStr, signature, msg}。
    未配置钉钉/取票失败回 ok False＋人话（手机页就退回拍照读码）。"""
    conf = _load_conf() if requests else None
    if not conf:
        return {"ok": False, "msg": _NOT_CONF}
    if not corp_id:
        return {"ok": False, "msg": "还不知道公司的钉钉企业编号：先用拍照扫一次审批单，系统会自动记下"}
    try:
        import secrets
        ticket = _jsapi_ticket(conf)
        ts = str(int(time.time() * 1000))
        nonce = secrets.token_hex(8)
        return {"ok": True, "agentId": str(conf["agentid"]), "corpId": corp_id, "timeStamp": ts, "nonceStr": nonce,
                "signature": jsapi_sign(ticket, nonce, ts, url), "msg": ""}
    except Exception as e:
        return {"ok": False, "msg": "钉钉扫码鉴权失败：%s" % _clean(e, conf)}


def send_text(userids, text):
    """按 userid 发钉钉文字消息（notifier.send_dingtalk_to：机器人单聊，失败回退工作通知）→ {sent, msg}。
    未配置钉钉时直接回 sent False，一个请求都不发。"""
    uids = [str(u).strip() for u in (userids or []) if str(u or "").strip()]
    uids = list(dict.fromkeys(uids))
    if not uids:
        return {"sent": False, "msg": "没有收件人"}
    conf = _load_conf() if (requests and notifier) else None
    if not conf:
        return {"sent": False, "msg": "未配置钉钉（conf.ini [dingtalk]），未发送"}
    try:
        r = notifier.send_dingtalk_to(uids, str(text or ""), conf=conf) or {}
        return {"sent": bool(r.get("sent")), "msg": _clean(r.get("msg") or ("已发送" if r.get("sent") else "发送失败"), conf),
                "via": r.get("via") or ""}
    except Exception as e:
        return {"sent": False, "msg": "发钉钉失败：%s" % _clean(e, conf)}


def roster(fresh=False):
    """全公司花名册 [{userid,name,title,dept}]（notifier.dt_roster，30 分钟缓存；fresh=True 强刷）→ {ok, rows, msg}。"""
    now = time.time()
    with _LOCK:
        if not fresh and _ROSTER["rows"] is not None and now - _ROSTER["ts"] < ROSTER_TTL:
            return {"ok": True, "rows": list(_ROSTER["rows"]), "msg": ""}
    conf = _load_conf() if (requests and notifier) else None
    if not conf:
        return {"ok": False, "rows": [], "msg": "未配置钉钉（conf.ini [dingtalk]），拉不到通讯录"}
    try:
        r = notifier.dt_roster(conf) or {}
        if not r.get("ok"):
            return {"ok": False, "rows": [], "msg": _clean(r.get("msg") or "拉通讯录失败", conf)}
        rows = [{"userid": p.get("userid") or "", "name": p.get("name") or "", "title": p.get("title") or "",
                 "dept": p.get("dept") or ""} for p in (r.get("people") or [])]
        with _LOCK:
            _ROSTER["rows"], _ROSTER["ts"] = rows, now
        return {"ok": True, "rows": list(rows), "msg": ""}
    except Exception as e:
        return {"ok": False, "rows": [], "msg": "拉通讯录失败：%s" % _clean(e, conf)}


def list_user_payments(userid, template_names, days=60, limit=60):
    """某人近 N 天在指定模板里发起的审批单（申请人自助登记发票后补「选一张我发起的单子」用）→
    {ok, rows:[{procInstId, businessId, title, template, createTime, amount, payeeName, hasAttachments,
    approvalStatus, approvalResult}], msg, truncated}。
    listids 按发起人过滤（userid_list），窗口钉钉限 120 天；单据详情 6 路并发取（请款多的人不至于等太久）。"""
    uid = str(userid or "").strip()
    if not uid:
        return {"ok": False, "rows": [], "msg": "缺钉钉用户身份"}
    names = [str(n).strip() for n in (template_names or []) if str(n or "").strip()]
    if not names:
        return {"ok": False, "rows": [], "msg": "还没设置审批模板"}
    conf = _load_conf() if requests else None
    if not conf:
        return {"ok": False, "rows": [], "msg": _NOT_CONF}
    try:
        days = max(1, min(int(days or 60), 120))
        end = datetime.datetime.now(CN_TZ)
        st, et = _ms(end - datetime.timedelta(days=days)), _ms(end)
        ids, notes, seen = [], [], set()
        for nm in names:
            pc, why = _process_code(conf, nm)
            if not pc:
                notes.append(why)
                continue
            got, err = _list_ids(conf, pc, st, et, userids=[uid], max_pages=20)
            if err:
                notes.append(err)
            for iid in got:
                if iid not in seen:
                    seen.add(iid)
                    ids.append(iid)
        truncated = len(ids) > limit
        ids = ids[:limit]

        def one(iid):
            r = _oapi(conf, "topapi/processinstance/get", {"process_instance_id": iid})
            inst = (r.get("process_instance") or r.get("result")) if r.get("errcode") == 0 else None
            if not isinstance(inst, dict):
                return None
            n = normalize_instance(inst, iid, [{"name": x} for x in names])
            return {"procInstId": iid, "businessId": n["businessId"], "title": n["title"], "template": n.get("template") or "",
                    "createTime": n["createTime"], "amount": n["amount"], "payeeName": n["payeeName"],
                    "hasAttachments": n["hasAttachments"], "approvalStatus": n.get("approvalStatus") or "",
                    "approvalResult": n.get("approvalResult") or ""}
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as ex:
            rows = [x for x in ex.map(one, ids) if x]
        rows.sort(key=lambda x: x["createTime"], reverse=True)
        if truncated:
            notes.append("单子太多，只列了最近 %d 张" % limit)
        return {"ok": True, "rows": rows, "msg": "；".join(x for x in notes if x), "truncated": truncated}
    except Exception as e:
        return {"ok": False, "rows": [], "msg": "列审批单失败：%s" % _clean(e, conf)}


def _reset_caches():
    """单测用：清空 token / 模板 / 花名册缓存。"""
    with _LOCK:
        _TOKENS.clear()
        _TICKET.clear()
        _PC_CACHE.clear()
        _BID_CACHE.clear()
        _ROSTER["ts"], _ROSTER["rows"] = 0.0, None
