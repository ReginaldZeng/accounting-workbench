# [Change Log]
# Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家）
# Description: 发票管家·核心路由（B1）——权限闸（页面准入点 AND 动作点）、设置、文件落盘与取图、进票流水线
#   （解析→查重→校验→可否抵扣建议）、后台线程（拉审批附件＋照片识别）、扫审批单、收票工作台、手机配对
#   （一次性令牌，登录门例外 /api/inv/m/）、发票审核。台账/税局对账/抵扣/新销方/期初/后补池/催票在
#   routers/invoice_books.py（B2），共用本模块导出的助手（each 函数文档写明入参出参）。
#   对外消息只走 notify_dt()：设了环境变量 INV_DRY_SEND 就只记不发；测试设 INV_WORKER_OFF 不起后台线程。
# 审查修复（同日）：已提交/已审票夹不许工作台/手机再放票、已审票合并只挂附件不改票面；查重按"谁先拿到号码"（dup_at）、
#   收据也查重、改票种/改机读字段亮给审核；自审含"票是自己登记的"；审核只过页面上看到的票（itemIds，409）；
#   没待审票的票夹不再卡队列；配对码一次性（绑定换发会话令牌）＋钉钉身份必核；拉附件中断有次数上限＋坏附件跳过；
#   登记失败不留挡路文件；多页 PDF 每页有预览、识别只解析一次；阻塞的钉钉/大批量写库放线程池；金额拒 NaN/超界。
"""发票管家（Invoice Butler）核心路由。接口契约见 docs/20260923_发票管家/发票管家_技术方案 §5。"""
import os
import re
import json
import math
import copy
import time
import hashlib
import mimetypes
import secrets
import shutil
import threading
import traceback
from datetime import datetime, timedelta
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import Response, FileResponse
from sqlalchemy import select, or_
from starlette.concurrency import run_in_threadpool

from core import JSONResponse, _current_user, db
from kernels import invoice_parse as ip
from kernels import invoice_dingtalk as idt
from kernels import invoice_store as S

router = APIRouter()

# ───────────────────────── 权限点 ─────────────────────────
# 非敏感动作点（收票/后补收票/抵扣）每次重启会被补给全体核算组账号（db._backfill_missing_perms），
# 所以动作点单独拦不住人：每个写接口都要「页面准入点 AND 动作点」一起判；读接口判页面准入点。
ENTER_DESK = "enter:invdesk"
ENTER_LATER = "enter:invlater"
ENTER_AUDIT = "enter:invaudit"
ENTER_LEDGER = "enter:invledger"
ENTERS = (ENTER_DESK, ENTER_LATER, ENTER_AUDIT, ENTER_LEDGER)
CAP_INTAKE = "inv_intake"
CAP_RECEIVE = "inv_receive"
CAP_AUDIT = "inv_audit"
CAP_DEDUCT = "inv_deduct"
CAP_UNBIND = "inv_unbind"
CAP_OPENING = "inv_opening"
CAP_CONFIG = "inv_config"
GATE_LABEL = {ENTER_DESK: "收票工作台", ENTER_LATER: "发票后补池", ENTER_AUDIT: "发票审核", ENTER_LEDGER: "发票台账"}
CAP_LABEL = {CAP_INTAKE: "收票（建票夹/登记/提交）", CAP_RECEIVE: "后补收票", CAP_AUDIT: "发票审核（通过/退回）",
             CAP_DEDUCT: "税局对账/抵扣勾选", CAP_UNBIND: "作废/解绑", CAP_OPENING: "期初导入", CAP_CONFIG: "发票管家设置"}

# ───────────────────────── 目录 / 上限 ─────────────────────────
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_DIR = os.path.join(_BASE, "inv_uploads")    # 原件是凭证（财会〔2020〕6号），落磁盘不进库；库里只存相对路径
MB = 1024 * 1024
MAX_FILE = 25 * MB                                  # 单个文件上限
MAX_TOTAL = 200 * MB                                # 一次上传总量上限（压缩包解开后的总量也按这个封顶：一次请求/一次拉附件共用一份）
MAX_FILES = 200                                     # 一次上传最多几个文件（表单解析器默认 1000，收紧）
MIN_FREE_MB = int(os.environ.get("INV_MIN_FREE_MB") or 2048)   # 服务器磁盘剩余低于这个（MB）就先不落文件，免得撑满整机
SETTINGS_KEY = "inv_config"
PAIR_BIND_MIN = 10                                  # 配对码 10 分钟内要扫
PAIR_SESSION_H = 12                                 # 绑定后手机可用 12 小时
STATUS_CN = {"collecting": "收票中", "submitted": "已提交待审", "approved": "已审核", "returned": "已退回"}

# 票面标量字段：前端 camelCase ↔ 库列
FIELD_COLS = (("code", "code"), ("number", "number"), ("date", "issue_date"), ("buyerName", "buyer_name"),
              ("buyerTaxId", "buyer_tax_id"), ("sellerName", "seller_name"), ("sellerTaxId", "seller_tax_id"),
              ("amount", "amount"), ("tax", "tax"), ("total", "total"), ("taxRate", "tax_rate"),
              ("category", "category"), ("checkCode", "check_code"), ("remark", "remark"))
F2C = dict(FIELD_COLS)
FIELD_LABEL = {"code": "发票代码", "number": "发票号码", "date": "开票日期", "buyerName": "购买方名称",
               "buyerTaxId": "购买方税号", "sellerName": "销售方名称", "sellerTaxId": "销售方税号", "amount": "金额",
               "tax": "税额", "total": "价税合计", "taxRate": "税率", "category": "项目类别", "checkCode": "校验码",
               "remark": "备注", "invType": "票种", "typeLabel": "票种名称", "kind": "票据类别", "lines": "明细"}
MONEY_FIELDS = ("amount", "tax", "total")
TRUSTED_SRC = ("qr", "pdf", "ofd", "xml", "manual", "taxpack", "taxlist")   # 这些来源的字段不许被识别结果覆盖
MACHINE_SRC = ("qr", "pdf", "ofd", "xml", "taxpack", "taxlist")   # 机器从二维码/电子原件/税局文件直接读到的（人手改了要亮给审核看）
KEY_FIELDS = ("number", "code", "total", "amount", "tax")         # 改了会动查重或金额的字段：人手改二维码/原件读到的值按"错"级提示
INV_TYPES = ("special", "normal", "travel", "toll", "train", "flight", "vehicle", "quota", "taxi", "tollpaper", "general",
             "other")
KINDS = ("invoice", "receipt", "other")
PAPER_ORIGINS = ("camera", "phone", "scanner")     # 这几种进票＝纸质件就在手上
USER_ORIGINS = ("upload", "camera", "phone", "scanner")   # 人手送进来的：审批单打印件可当"扫审批单"
OPEN_STATUSES = ("collecting", "returned")          # 收票工作台/手机只能往这两种状态的票夹里放票
# 重算时要保留的标记（其余 flags 每次按字段重算）：原始码串、识别提示、已审票后到文件不一致、同票文件不一致、人手改机读字段、改票种
STICKY_FLAGS = ("_qrRaw", "_warnings", "_region", "_splitDone", "_audOk", "_doubt", "postApprovalMismatch", "fileMismatch",
                "manualOverride", "kindChanged")
SYSTEM_USER = "系统"                                 # 后台线程拉下来的附件记在"系统"名下（不算打开票夹那个人登记的）
PAIR_ACTIVE_S = 120                                 # 手机 120 秒内来过＝正在用（电脑端据此快轮询）
_DUP_LOCK = threading.RLock()                       # 查重"查＋插"必须原子（两个人同时扫同一张票只能一个算正主）；可重入：合并路径里还要重算


# ───────────────────────── 通用小工具 ─────────────────────────

def E():
    """当前数据库引擎（每次现取，测试会换临时库）。"""
    return db._engine


def err(msg, status=400, **extra):
    """失败响应 {ok:false, msg, ...extra}。"""
    body = {"ok": False, "msg": msg}
    body.update(extra)
    return JSONResponse(body, status_code=status)


async def body_json(request):
    """POST 体 → dict；空体/坏 JSON → {}（前端 jp(url) 不带体时也不报错）。"""
    try:
        b = await request.json()
    except Exception:
        return {}
    return b if isinstance(b, dict) else {}


def now_s():
    return S.now_s()


def _after(minutes=0, hours=0):
    return (datetime.now() + timedelta(minutes=minutes, hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


def _secs_until(ts):
    try:
        return max(0, int((datetime.strptime(str(ts), "%Y-%m-%d %H:%M:%S") - datetime.now()).total_seconds()))
    except (TypeError, ValueError):
        return 0


def _s(v, n):
    return ("" if v is None else str(v)).strip()[:n]


def _b(v):
    return v is True or str(v).strip().lower() in ("1", "true", "yes", "on")


def _int(v, lo, hi, dflt):
    try:
        x = int(float(v))
    except (TypeError, ValueError):
        return dflt
    return min(hi, max(lo, x))


def _int_or_none(v):
    try:
        x = int(str(v).strip())
        return x if x > 0 else None
    except (TypeError, ValueError):
        return None


def _empty(v):
    return v is None or (isinstance(v, str) and v.strip() == "")


def _same(a, b):
    if _empty(a) and _empty(b):
        return True
    if isinstance(a, (int, float)) or isinstance(b, (int, float)):
        try:
            return abs(float(a) - float(b)) < 0.005
        except (TypeError, ValueError):
            return False
    return str(a) == str(b)


def _money_str(v):
    try:
        return "%.2f" % float(v)
    except (TypeError, ValueError):
        return "—"


def _money_val(v):
    """票面金额 → 两位小数 float；空、认不出、NaN/无穷大、超出 ±1e16（库里 Numeric(18,2) 装不下）→ None。"""
    if _empty(v) or isinstance(v, bool):
        return None
    try:
        x = float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x) or abs(x) >= 1e16:
        return None
    return round(x, 2)


def _now_us():
    # 带微秒的时间（查重先来后到用：同一秒里也分得出先后）
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")


def _clean_name(name):
    n = str(name or "").replace("\\", "/").split("/")[-1]
    n = re.sub(r"[\x00-\x1f\x7f]", "", n).strip()
    return n[:200] or "未命名"


def _uname(user):
    return user.get("name") if isinstance(user, dict) else str(user or "")


def _norm_date(v):
    """'2026-09-01' / '2026/9/1' / '20260901' → 'YYYY-MM-DD'；空 → None；乱的抛 ValueError。"""
    s = _s(v, 20)
    if not s:
        return None
    m = re.fullmatch(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", s) or re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s)
    if not m:
        raise ValueError(s)
    d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return d.strftime("%Y-%m-%d")


# ───────────────────────── 权限闸 ─────────────────────────

def _gates(enter):
    if enter is None:
        return ENTERS
    if isinstance(enter, str):
        return (enter,)
    return tuple(enter)


def can(u, enter=None, act=None):
    """u 能否：进得了 enter（单个准入点/元组任一/None＝任一 inv 页面）且（给了 act 时）有这个动作点。
    主管理员恒真、子管理员在管辖工作台内恒真——都由 db.user_can 决定，这里不另开口子。"""
    if not u:
        return False
    if not any(db.user_can(u, g) for g in _gates(enter)):
        return False
    return (not act) or bool(db.user_can(u, act))


def need(request, enter=None, act=None):
    """路由闸 → (user, None) 或 (None, JSONResponse)：未登录 401；进不了页面/没有动作点 403。
    enter=None 表示发票管家任一页面都行（跨页的读接口用）。"""
    u = _current_user(request)
    if not u:
        return None, err("未登录", 401)
    gates = _gates(enter)
    if not any(db.user_can(u, g) for g in gates):
        return None, err("无「%s」权限：请管理员在账号设置里开通这个页面" % "／".join(GATE_LABEL.get(g, g) for g in gates), 403)
    if act and not db.user_can(u, act):
        return None, err("无「%s」权限：请管理员在账号设置里开通" % CAP_LABEL.get(act, act), 403)
    return u, None


def need_any(request, pairs):
    """几条路子任一满足即放行：pairs=[(enter, act), ...]（如花名册：设置权限 或 后补收票权限）。"""
    u = _current_user(request)
    if not u:
        return None, err("未登录", 401)
    for enter, act in pairs:
        if can(u, enter, act):
            return u, None
    names = "」或「".join(CAP_LABEL.get(a, a) if a else "／".join(GATE_LABEL.get(g, g) for g in _gates(e))
                        for e, a in pairs)
    return None, err("无权限：需要「%s」" % names, 403)


# ───────────────────────── 设置（app_settings: inv_config） ─────────────────────────
# people 默认空：技术方案里的"张三"只是示意格式，不能当真人名单种进去。
DEFAULT_SETTINGS = {
    "people": [],
    "company": [{"name": "深圳市星期零食品科技有限公司", "taxId": "91440300MA5EHQAR7X"},
                {"name": "孝感市星期九食品科技有限公司", "taxId": "91420900MA4F00NK81"}],
    "templates": [{"name": "付款申请（公对公）", "amountFields": ["实际付款总额", "付款总额"], "allowLater": True},
                  {"name": "费用报销", "amountFields": ["报销总额", "合计金额", "报销金额", "实际报销金额"], "allowLater": True}],
    "remind": {"enabled": True, "beforeDays": 3, "everyDays": 7, "hour": 10},
    "blockNoInvoice": False,
    # 空＝用电脑上正在用的地址（手机扫配对码、钉钉消息里的链接都跟着走）。正式域名 finance.starfieldsz.com
    # 上线时还没备案、在腾讯云被拦（跳"网站未备案"页），不能写死；备案开通后在设置里填上即可
    "portalUrl": "",
    "corpId": "",
}


def _copy(o):
    return json.loads(json.dumps(o, ensure_ascii=False))


def normalize_settings(raw, base=None, strict=True):
    """设置入参（可只给部分键）→ 完整规范化设置；缺的键取 base（默认 DEFAULT_SETTINGS）。
    strict=True：格式不对抛 ValueError(中文)；strict=False（读库时）坏的键回退 base，不让整页 500。"""
    base = _copy(base if isinstance(base, dict) else DEFAULT_SETTINGS)
    for k, v in DEFAULT_SETTINGS.items():
        base.setdefault(k, _copy(v))
    raw = raw if isinstance(raw, dict) else {}
    out = dict(base)

    def bad(msg):
        if strict:
            raise ValueError(msg)

    if "people" in raw:
        rows = raw.get("people")
        if not isinstance(rows, list):
            bad("财务人员名单格式不对")
        else:
            people, seen = [], set()
            for i, p in enumerate(rows):
                if not isinstance(p, dict):
                    continue
                acc = _s(p.get("account"), 50)
                if not acc:
                    bad("财务人员名单第 %d 行没填工作台账号" % (i + 1))
                    continue
                if acc in seen:
                    continue
                seen.add(acc)
                people.append({"account": acc, "dtUserid": _s(p.get("dtUserid"), 64), "dtName": _s(p.get("dtName"), 50),
                               "receiver": _b(p.get("receiver"))})
            out["people"] = people
    if "company" in raw:
        rows = raw.get("company")
        if not isinstance(rows, list):
            bad("本公司抬头清单格式不对")
        else:
            comp = []
            for i, c in enumerate(rows):
                if not isinstance(c, dict):
                    continue
                nm = _s(c.get("name"), 120)
                if not nm:
                    bad("本公司抬头第 %d 行没填公司名称" % (i + 1))
                    continue
                comp.append({"name": nm, "taxId": _s(c.get("taxId"), 32).upper()})
            out["company"] = comp
    if "templates" in raw:
        rows = raw.get("templates")
        if not isinstance(rows, list):
            bad("审批模板清单格式不对")
        else:
            tpls, seen = [], set()
            for i, t in enumerate(rows):
                if not isinstance(t, dict):
                    continue
                nm = _s(t.get("name"), 80)
                if not nm:
                    bad("审批模板第 %d 行没填模板名称" % (i + 1))
                    continue
                if nm in seen:
                    continue
                seen.add(nm)
                af = t.get("amountFields")
                if isinstance(af, str):
                    af = re.split(r"[,，、;；\s]+", af)
                af = [_s(x, 40) for x in (af or []) if _s(x, 40)][:10]
                tpls.append({"name": nm, "amountFields": af, "allowLater": _b(t.get("allowLater"))})
            out["templates"] = tpls
    if "remind" in raw:
        r = raw.get("remind")
        if not isinstance(r, dict):
            bad("催票规则格式不对")
        else:
            cur = base.get("remind") or {}
            out["remind"] = {"enabled": _b(r.get("enabled", cur.get("enabled", True))),
                             "beforeDays": _int(r.get("beforeDays", cur.get("beforeDays")), 0, 30, 3),
                             "everyDays": _int(r.get("everyDays", cur.get("everyDays")), 1, 60, 7),
                             "hour": _int(r.get("hour", cur.get("hour")), 0, 23, 10)}
    if "blockNoInvoice" in raw:
        out["blockNoInvoice"] = _b(raw.get("blockNoInvoice"))
    if "portalUrl" in raw:
        u = _s(raw.get("portalUrl"), 200).rstrip("/")
        if u and not re.match(r"^https?://[^\s/]+", u):
            bad("门户网址要以 http:// 或 https:// 开头，例如 http://111.229.72.116；留空＝用电脑上正在用的地址")
        else:
            out["portalUrl"] = u
    if "corpId" in raw:
        out["corpId"] = _s(raw.get("corpId"), 64)
    return out


def get_settings():
    """当前设置（库里没存或存坏了的键用默认补齐）。"""
    raw = db.get_setting(SETTINGS_KEY, None)
    return normalize_settings(raw if isinstance(raw, dict) else {}, strict=False)


def save_settings(new, operator):
    """合并保存（只改给了的键）→ 规范化后的完整设置；格式不对抛 ValueError(中文)。"""
    merged = normalize_settings(new, base=get_settings(), strict=True)
    db.set_setting(SETTINGS_KEY, merged, operator)
    return merged


def _learn_corp(corp):
    """钉钉 corpId 自动学（不新增配置项）；手机页"钉钉免登"要用它。
    只在还没设过时学一次：已有值绝不自动改（扫到一条伪造链接不能把全员的免登搞坏），要改走设置页（发票管家设置权限）。
    调用方负责只把"从钉钉服务器回来的"corpId 交进来（不是扫码原文里带的）。"""
    corp = _s(corp, 64)
    if not corp or not re.fullmatch(r"[A-Za-z0-9_\-]{4,64}", corp):
        return
    try:
        st = get_settings()
        if not st.get("corpId"):
            st["corpId"] = corp
            db.set_setting(SETTINGS_KEY, st, SYSTEM_USER)
    except Exception:
        pass


def person_of(account, settings=None):
    """设置里某工作台账号的那一行 {account,dtUserid,dtName,receiver} 或 None。"""
    st = settings or get_settings()
    return next((p for p in st.get("people") or [] if p.get("account") == account), None)


def dt_uid_of(account, settings=None):
    """工作台账号 → 设置里绑定的钉钉 userid（没绑返回 ''）。"""
    p = person_of(account, settings)
    return (p or {}).get("dtUserid") or ""


def template_cfg(name, settings=None):
    """审批模板配置 {name, amountFields, allowLater} 或 None。"""
    st = settings or get_settings()
    return next((t for t in st.get("templates") or [] if t.get("name") == name), None) if name else None


# ───────────────────────── 留痕 / 通知 ─────────────────────────

def audit(user, action, target="", detail=""):
    """全站业务留痕（日志中心可查）：动作名自动加「发票管家-」前缀并截到 40 字（audit_log.action 宽 40）。"""
    if not isinstance(detail, str):
        detail = json.dumps(detail, ensure_ascii=False, default=str)
    try:
        db.audit(_uname(user), ("发票管家-" + str(action or ""))[:40], str(target or "")[:160], detail[:4000])
    except Exception as ex:   # 留痕失败不能把已经做成的业务动作打回去（库都写不进去时前面也早报错了）
        print("[发票管家] 写全站留痕失败：%s" % ex)


def log(user, action, folder_id=None, item_id=None, later_id=None, detail=None):
    """发票管家自己的逐项留痕（inv_log，票夹详情里看）。"""
    try:
        S.log_add(E(), _uname(user), action, folder_id=folder_id, item_id=item_id, later_id=later_id, detail=detail)
    except Exception as ex:
        print("[发票管家] 写票夹留痕失败：%s" % ex)


def notify_dt(userids, text):
    """发钉钉的唯一出口：设了环境变量 INV_DRY_SEND → 不发，回 {'sent': False, 'msg': 'dry-run'}；
    否则 invoice_dingtalk.send_text（未配置钉钉时它自己一个请求都不发）。永不抛异常。"""
    uids = [str(x).strip() for x in (userids or []) if str(x or "").strip()]
    if not uids:
        return {"sent": False, "msg": "对方没在设置里绑定钉钉，没发通知"}
    if os.environ.get("INV_DRY_SEND"):
        return {"sent": False, "msg": "dry-run"}
    try:
        return idt.send_text(uids, text)
    except Exception as ex:
        return {"sent": False, "msg": "发钉钉失败：%s" % ex}


def portal_link(path):
    """站内深链（钉钉消息里用）：设置里的门户网址 + path（如 '/#/invdesk?folder=3'）；
    没填门户网址 → 用最近一次有人从外面打开工作台时的地址（_site 记下的）。"""
    base = (get_settings().get("portalUrl") or _LAST_SITE[0] or "").rstrip("/")
    return (base + path) if base else path


# ───────────────────────── 文件落盘 / 取图 ─────────────────────────
# 路径 <YYYY-MM>/<票夹id>/<文件id>_o.<ext>（原件）、_p<N>.jpg（第 N 页预览）、_t<N>.jpg（第 N 页缩略）
_REL_RE = re.compile(r"^\d{4}-\d{2}/\d{1,10}/\d{1,10}_[opt]\d{0,3}\.[a-z0-9]{1,8}$")
_INLINE_MIME = ("application/pdf", "image/jpeg", "image/png")   # 其它类型一律附件下载（防同源 HTML/SVG 脚本）
_EXT_MIME = {"pdf": "application/pdf", "ofd": "application/ofd", "xml": "application/xml", "jpg": "image/jpeg",
             "png": "image/png", "zip": "application/zip",
             "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xls": "application/vnd.ms-excel"}


def abs_path(rel):
    """库里的相对路径 → 绝对路径；格式不对或越出 inv_uploads → None（防路径穿越）。"""
    if not rel or not isinstance(rel, str):
        return None
    rel = rel.replace("\\", "/")
    if not _REL_RE.match(rel):
        return None
    root = os.path.realpath(UPLOAD_DIR)
    p = os.path.realpath(os.path.join(root, *rel.split("/")))
    try:
        if os.path.commonpath([root, p]) != root:
            return None
    except ValueError:
        return None
    return p


def _write(rel, data):
    p = os.path.join(UPLOAD_DIR, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".part"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, p)     # 先写 .part 再改名：进程被杀也不会留下半截文件


def _variant_rel(f, v, page=0):
    base = f.get("orig_path") if v == "o" else (f.get("preview_path") if v == "p" else f.get("thumb_path"))
    if not base:
        return None
    if v in ("p", "t") and page:
        pages = int(f.get("pages") or 1)
        if 0 < page < pages:
            base = re.sub(r"_([pt])0\.jpg$", lambda m: "_%s%d.jpg" % (m.group(1), page), base)
    return base


def read_file_bytes(f, v="o", page=0):
    """文件行 → 字节（v=o 原件 / p 预览 / t 缩略，page 从 0 起）；找不到返回 None。"""
    p = abs_path(_variant_rel(f or {}, v, page))
    if not p or not os.path.isfile(p):
        return None
    with open(p, "rb") as fh:
        return fh.read()


class StoreRefused(ValueError):
    """不落文件（磁盘快满了）：消息是给人看的中文。进票流水线接住它回 action=error，不留半截行。"""


def _disk_free_mb(path):
    p = os.path.abspath(path)
    while p and not os.path.isdir(p):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    try:
        return shutil.disk_usage(p).free // MB
    except Exception:
        return None


def _check_disk(nbytes=0):
    free = _disk_free_mb(UPLOAD_DIR)
    if free is not None and free - nbytes // MB < MIN_FREE_MB:
        raise StoreRefused("服务器磁盘剩余空间不足（剩 %dMB，低于 %dMB 警戒线），先不收文件：请联系管理员清理磁盘" % (free, MIN_FREE_MB))


def store_file(folder_id, name, data, origin, user, role="original", mime=None, ext=None, previews=None,
               thumbs=None, orig_bytes=None, width=None, height=None, rotation=0, dt_file_id=None, item_id=None):
    """落一份文件 → file 行 dict。先插行拿 id，再写 <YYYY-MM>/<票夹>/<id>_{o,pN,tN}，最后回填相对路径。
    data＝收到的原始字节（算 sha256 查同文件用）；orig_bytes＝实际存的原件（照片超 2MB 压过的版本），不给就存 data。
    previews/thumbs＝按页的 JPEG 字节列表；thumbs 不给就从预览现生成。写盘失败：行标 failed 并抛出（不挡以后重传/重拉）。
    folder_id 可为 None（不挂票夹的资料，如后补单附件）：库里存 NULL，目录段用 0。
    磁盘剩余低于 MIN_FREE_MB → 抛 StoreRefused（ValueError 子类，中文消息），一行都不插。"""
    e = E()
    raw = data if orig_bytes is None else orig_bytes
    previews = [p for p in (previews or []) if p]
    _check_disk(len(raw) + sum(len(p) for p in previews))
    fid = S.file_insert(e, folder_id=folder_id, item_id=item_id, role=role, origin=origin, name=_clean_name(name),
                        mime=mime or "application/octet-stream", ext=ext or "bin", size=len(raw),
                        sha256=hashlib.sha256(data).hexdigest(), pages=max(1, len(previews)), width=width,
                        height=height, rotation=int(rotation or 0), dt_file_id=dt_file_id, created_by=_uname(user))
    base = "%s/%d/%d" % (datetime.now().strftime("%Y-%m"), int(folder_id or 0), int(fid))
    try:
        orig_rel = base + "_o." + (ext or "bin")
        _write(orig_rel, raw)
        prev_rel = thumb_rel = None
        for i, jp in enumerate(previews):
            _write(base + "_p%d.jpg" % i, jp)
            th = (thumbs[i] if thumbs and i < len(thumbs) else None)
            if not th:
                try:
                    th = ip.thumb_from_jpeg(jp)
                except Exception:
                    th = None
            if th:
                _write(base + "_t%d.jpg" % i, th)
            if i == 0:
                prev_rel = base + "_p0.jpg"
                thumb_rel = (base + "_t0.jpg") if th else None
        S.file_update(e, fid, orig_path=orig_rel, preview_path=prev_rel, thumb_path=thumb_rel)
    except Exception:
        S.file_update(e, fid, status="failed")
        raise
    return S.file_get(e, fid)


def _fail_orphan_file(f):
    """登记半路出错：这份文件还没挂上任何票 → 标 failed（同一份文件重传/钉钉附件重拉时能重新登记，不会被"已放进来过"挡住）。"""
    if not f:
        return
    try:
        cur = S.file_get(E(), f["id"])
        if cur and not cur.get("item_id"):
            S.file_update(E(), f["id"], status="failed")
    except Exception:
        pass


def file_response(f, v="o", page=0, inline=False):
    """文件行 → 响应：t/p 为 JPEG 内联；o 为原件，默认附件下载（RFC5987 中文文件名），inline=True 且是 PDF/图片时内联。"""
    if v not in ("t", "p", "o"):
        return err("参数 v 只能是 t（缩略图）/ p（预览图）/ o（原件）", 400)
    rel = _variant_rel(f, v, page)
    p = abs_path(rel)
    if (not p or not os.path.isfile(p)) and v in ("t", "p") and page:
        p = abs_path(_variant_rel(f, v, 0))
    if not p or not os.path.isfile(p):
        return err("文件不在了（没有预览，或已被清理）", 404)
    if v in ("t", "p"):
        return FileResponse(p, media_type="image/jpeg", headers={"X-Content-Type-Options": "nosniff"})
    mime = f.get("mime") or "application/octet-stream"
    fn = f.get("name") or ("原件." + (f.get("ext") or "bin"))
    disp = "inline" if (inline and mime in _INLINE_MIME) else "attachment"
    headers = {"Content-Disposition": "%s; filename*=UTF-8''%s" % (disp, quote(fn)),
               "X-Content-Type-Options": "nosniff"}
    return FileResponse(p, media_type=mime, headers=headers)


async def read_upload_files(request):
    """multipart → (files[(文件名, 字节)], fields{字段: 值}, errors[结果 dict], bad 响应|None)。
    单个文件超 MAX_FILE：记进 errors（action=error），其余照收；整包超 MAX_TOTAL、或一个能收的都没有且全是超限 → bad=413。
    没带长度（分块传输）→ 411：不先把不知多大的内容整个落到临时盘上；一次超过 MAX_FILES 个文件 → 400。"""
    mf, mt = MAX_FILE, MAX_TOTAL
    raw_cl = request.headers.get("content-length")
    if raw_cl is None:
        return [], {}, [], err("上传请求缺少长度信息，请刷新页面后重新上传", 411)
    try:
        cl = int(raw_cl or 0)
    except ValueError:
        cl = 0
    if cl > mt + MB:
        return [], {}, [], err("这一批文件太大（超过 %dMB），请分几次上传" % (mt // MB), 413)
    try:
        form = await request.form(max_files=MAX_FILES, max_fields=50)
    except Exception as ex:
        if "Too many" in str(ex):
            return [], {}, [], err("一次最多传 %d 个文件，请分几次上传" % MAX_FILES, 400)
        return [], {}, [], err("上传的内容读不出来：%s" % ex, 400)
    files, fields, errors, total = [], {}, [], 0
    try:
        for key, val in form.multi_items():
            if hasattr(val, "read"):
                fname = _clean_name(getattr(val, "filename", "") or key)
                data = await val.read(mf + 1)
                if len(data) > mf:
                    errors.append({"name": fname, "action": "error",
                                   "msg": "「%s」超过 %dMB，传不上来：PDF 请压缩，照片用手机默认画质" % (fname, mf // MB)})
                    continue
                if not data:
                    errors.append({"name": fname, "action": "error", "msg": "「%s」是空文件" % fname})
                    continue
                total += len(data)
                if total > mt:
                    return [], fields, [], err("这一批文件太大（超过 %dMB），请分几次上传" % (mt // MB), 413)
                files.append((fname, data))
            else:
                fields[key] = str(val)
    finally:
        try:
            await form.close()
        except Exception:
            pass
    if not files and errors and all("超过" in x["msg"] for x in errors):
        return [], fields, errors, err(errors[0]["msg"], 413, results=errors)
    return files, fields, errors, None


def _ext_mime(ftype, name, img_ext=None):
    ext = (os.path.splitext(name or "")[1] or "").lower().lstrip(".")
    if ftype in ("pdf", "ofd", "xml"):
        ext = ftype
    elif ftype == "image":
        ext = img_ext or "jpg"
    elif not re.fullmatch(r"[a-z0-9]{1,8}", ext or ""):
        ext = "bin"
    mime = _EXT_MIME.get(ext) or mimetypes.guess_type("x." + ext)[0] or "application/octet-stream"
    return ext, mime


# ───────────────────────── 视图（camelCase，技术方案 §5.1） ─────────────────────────

def _file_index(e, folder_ids):
    """几个票夹的全部文件 → {'by_id': {id: 行}, 'paper': {item_id: 最新一份有效纸质件}}（批量出视图时省查询）。"""
    ids = [i for i in set(folder_ids or []) if i is not None]
    by_id, paper = {}, {}
    if ids:
        with e.connect() as cx:
            rows = [S._row(r) for r in cx.execute(select(S.FILE).where(S.FILE.c.folder_id.in_(ids)).order_by(S.FILE.c.id))]
        for r in rows:
            by_id[r["id"]] = r
            if r.get("role") == "paper" and r.get("item_id") and (r.get("status") or "active") == "active":
                paper[r["item_id"]] = r
    return {"by_id": by_id, "paper": paper}


def file_view(f, mobile=False, page=0):
    """文件行 → {id,name,mime,ext,size,pages,rotation,role,origin,thumb,preview:[url],orig}；手机端只给缩略图。"""
    if not f:
        return None
    fid = int(f["id"])
    base = ("/api/inv/m/file/%d" if mobile else "/api/inv/file/%d") % fid
    pages = int(f.get("pages") or 1)
    pg = page if (page and 0 < int(page) < pages) else 0
    thumb = (base + "?v=t" + ("&page=%d" % pg if pg else "")) if f.get("thumb_path") else None
    prev = [base + "?v=p&page=%d" % i for i in range(pages)] if (f.get("preview_path") and not mobile) else []
    return {"id": fid, "name": f.get("name") or "", "mime": f.get("mime") or "", "ext": f.get("ext") or "",
            "size": f.get("size") or 0, "pages": pages, "rotation": int(f.get("rotation") or 0),
            "role": f.get("role") or "", "origin": f.get("origin") or "", "thumb": thumb, "preview": prev,
            "orig": None if mobile else base + "?v=o"}


def item_view(it, idx=None, mobile=False):
    """票据行 → 前端 Item。idx＝_file_index(...) 的结果（批量时传，省查询）；不传就按本票夹现查。"""
    if not it:
        return None
    if idx is None:
        idx = _file_index(E(), [it.get("folder_id")])
    fl = it.get("flags_json") if isinstance(it.get("flags_json"), dict) else {}
    f = idx["by_id"].get(it.get("file_id")) if it.get("file_id") else None
    pf = idx["paper"].get(it.get("id"))
    ded = it.get("deductible")
    v = {"id": it["id"], "folderId": it.get("folder_id"), "kind": it.get("kind") or "other", "origin": it.get("origin") or "",
         "invType": it.get("inv_type") or "", "typeLabel": it.get("type_label") or "", "qrType": it.get("qr_type") or "",
         "lines": it.get("lines_json") or [], "fieldSrc": it.get("field_src_json") or {},
         "pending": it.get("pending_json") or [], "procStatus": it.get("proc_status") or "done",
         "procError": it.get("proc_error") or "", "paper": bool(it.get("paper")),
         "flags": {k: x for k, x in fl.items() if not str(k).startswith("_")},
         "warnings": list(fl.get("_warnings") or []), "region": fl.get("_region"),
         "auditOk": fl.get("_audOk"), "doubt": fl.get("_doubt"),
         "deductSuggest": it.get("deduct_suggest") or "", "deductReason": it.get("deduct_reason") or "",
         "deductible": None if ded is None else bool(ded), "deductStatus": it.get("deduct_status") or "",
         "verify": it.get("verify") or "", "verifyAt": it.get("verify_at") or "", "verifyNote": it.get("verify_note") or "",
         "split": bool(it.get("split")), "alloc": it.get("alloc"), "laterId": it.get("later_id"),
         "review": it.get("review") or "draft", "reviewBy": it.get("review_by") or "", "reviewAt": it.get("review_at") or "",
         "reviewNote": it.get("review_note") or "", "selfReview": bool(it.get("self_review")),
         "voidBy": it.get("void_by") or "", "voidAt": it.get("void_at") or "", "voidNote": it.get("void_note") or "",
         "status": it.get("status") or "active", "page": int(it.get("page") or 0), "dupKey": it.get("dup_key") or "",
         "createdBy": it.get("created_by") or "", "createdAt": it.get("created_at") or "", "updatedAt": it.get("updated_at") or "",
         "file": file_view(f, mobile, int(it.get("page") or 0)), "paperFile": file_view(pf, mobile)}
    for k, col in FIELD_COLS:
        v[k] = it.get(col)
    return v


def item_views(items, mobile=False):
    """多张票一起出视图（一次查全部文件）。"""
    items = [i for i in (items or []) if i]
    idx = _file_index(E(), [i.get("folder_id") for i in items])
    return [item_view(i, idx, mobile) for i in items]


def _share(it):
    # 这张票算进本单的金额：拆分票按分摊额，否则价税合计（收据没有合计时用金额）
    if it.get("split") and it.get("alloc") is not None:
        return float(it["alloc"])
    if it.get("total") is not None:
        return float(it["total"])
    return float(it.get("amount") or 0)


def folder_stats(folder, items):
    """票夹统计：invoices/others/dup/processing/unchecked/sumTotal/diff(票合计−单据金额)/paperMissing/pendingReview。"""
    act = [i for i in items if i.get("status") != "removed" and i.get("review") != "void"]
    inv = [i for i in act if i.get("kind") == "invoice"]
    s = round(sum(_share(i) for i in act if i.get("kind") in ("invoice", "receipt")), 2)
    amt = (folder or {}).get("amount")
    return {"invoices": len(inv), "receipts": len([i for i in act if i.get("kind") == "receipt"]),
            "others": len([i for i in act if i.get("kind") not in ("invoice", "receipt")]),
            "dup": len([i for i in act if (i.get("flags_json") or {}).get("dup")]),
            "processing": len([i for i in act if i.get("proc_status") in ("pending", "running")]),
            "unchecked": len([i for i in act if i.get("pending_json")]),
            "sumTotal": s, "diff": (round(s - float(amt), 2) if amt is not None else None),
            "paperMissing": len([i for i in inv if i.get("origin") in ("attachment", "photo_field") and not i.get("paper")]),
            "pendingReview": len([i for i in act if i.get("review") == "pending"])}


LATER_ST_CN = {"open": "待收", "partial": "部分到票", "done": "已收齐", "closed": "已关闭"}


def folder_laters(e, folder):
    """这张单关联的发票后补单（申请人付款时登记的；按审批实例或票夹找），新的在前。"""
    if not folder:
        return []
    rows = S.laters_by_inst(e, folder.get("inst_id")) if folder.get("inst_id") else []
    ids = {r["id"] for r in rows}
    rows += [r for r in S._all(e, select(S.LATER).where(S.LATER.c.folder_id == folder["id"])) if r["id"] not in ids]
    return sorted(rows, key=lambda r: -r["id"])


def later_left(l):
    """后补单还没到的金额（与后补池同口径：预计补票（没填按付款金额）− 已登记到票 − 已收未登记）；已收齐/已关闭算 0。"""
    if (l.get("status") or "open") not in ("open", "partial"):
        return 0.0
    base = l.get("expect_amount") if l.get("expect_amount") is not None else l.get("pay_amount")
    if base is None:
        return 0.0
    return round(max(0.0, float(base) - float(l.get("received_amount") or 0) - float(l.get("unregistered_amount") or 0)), 2)


def later_row_view(l):
    """审核页「发票后补单」列表的一行。"""
    exp = l.get("expect_amount") if l.get("expect_amount") is not None else l.get("pay_amount")
    return {"id": l["id"], "filedBy": l.get("filed_by") or "", "filedAt": l.get("created_at") or "",
            "expectAmount": _money_val(exp), "receivedAmount": _money_val(l.get("received_amount")) or 0.0,
            "unregisteredAmount": _money_val(l.get("unregistered_amount")) or 0.0, "left": later_left(l),
            "expectDate": l.get("expect_date") or "", "receiverName": l.get("receiver_name") or l.get("receiver") or "",
            "status": l.get("status") or "open", "statusText": LATER_ST_CN.get(l.get("status") or "open", l.get("status") or ""),
            "remindCount": int(l.get("remind_count") or 0), "lastRemindAt": l.get("last_remind_at") or ""}


def folder_gap(folder, items, laters):
    """金额核对：差额＝付款金额 −（专票 ＋ 普票 ＋ 发票后补单还没到的）。正数＝票比付款少，负数＝票比付款多。
    专票＝增值税专用发票（含电子专票）；普票＝其余发票和收据。拆分票按本单分摊额。付款金额没有（手工票夹没填）→ gap=None。"""
    act = [i for i in items if i.get("status") != "removed" and i.get("review") != "void"
           and i.get("kind") in ("invoice", "receipt")]
    sp = [i for i in act if i.get("kind") == "invoice" and i.get("inv_type") == "special"]
    nm = [i for i in act if not (i.get("kind") == "invoice" and i.get("inv_type") == "special")]
    s_sp = round(sum(_share(i) for i in sp), 2)
    s_nm = round(sum(_share(i) for i in nm), 2)
    left = round(sum(later_left(l) for l in (laters or [])), 2)
    pay = (folder or {}).get("amount")
    gap = None if pay is None else round(float(pay) - s_sp - s_nm - left, 2)
    return {"pay": _money_val(pay), "special": s_sp, "specialN": len(sp), "normal": s_nm, "normalN": len(nm),
            "later": left, "gap": gap}


def gap_msg(g):
    v = g["gap"]
    return "有差额 ¥%s（票比付款%s）" % (_money_str(abs(v)), "少" if v > 0 else "多")


def gap_off(g):
    return bool(g) and g.get("gap") is not None and abs(g["gap"]) > 0.005


_UNSET = object()


def later_brief(folder, e=None, later=_UNSET):
    """这张单上未收齐的后补单摘要 {id,status,expectDate,expectAmount,receivedAmount,receiverName} 或 None。
    later：调用方已批量查好的后补单行（S.later_open_for_folders），给了就不再查库。"""
    e = e or E()
    if not folder:
        return None
    if later is not _UNSET:
        l = later
    else:
        l = S.later_open_by_inst(e, folder.get("inst_id")) if folder.get("inst_id") else None
        if not l:
            with e.connect() as cx:
                l = S._row(cx.execute(select(S.LATER).where(S.LATER.c.folder_id == folder["id"],
                                                             S.LATER.c.status.in_(("open", "partial")))
                                      .order_by(S.LATER.c.id.desc())).first())
    if not l:
        return None
    return {"id": l["id"], "status": l.get("status"), "expectDate": l.get("expect_date") or "",
            "expectAmount": l.get("expect_amount"), "receivedAmount": l.get("received_amount"),
            "receiverName": l.get("receiver_name") or l.get("receiver") or ""}


def folder_view(f, items=None, mobile=False, settings=None, with_form=False, later=_UNSET):
    """票夹行 → 前端 Folder（含 stats 与后补摘要 later）。items 不给就现查本票夹有效票；later 见 later_brief。"""
    if not f:
        return None
    e = E()
    if items is None:
        items = S.folder_items(e, f["id"])
    cfg = template_cfg(f.get("template"), settings)
    v = {"id": f["id"], "instId": f.get("inst_id") or "", "businessId": f.get("business_id") or "",
         "template": f.get("template") or "", "title": f.get("title") or "", "applicant": f.get("applicant") or "",
         "dept": f.get("dept") or "", "company": f.get("company") or "", "amount": f.get("amount"),
         "payee": {"name": f.get("payee_name") or "", "bank": f.get("payee_bank") or "", "account": f.get("payee_account") or ""},
         "reason": f.get("reason") or "", "erpNo": f.get("erp_no") or "",
         "approvalStatus": f.get("approval_status") or "", "approvalResult": f.get("approval_result") or "",
         "attachStatus": f.get("attach_status") or "none", "attachMsg": f.get("attach_msg") or "",
         "status": f.get("status") or "collecting", "source": f.get("source") or "",
         "createdBy": f.get("created_by") or "", "createdAt": f.get("created_at") or "",
         "updatedAt": f.get("updated_at") or "", "openedBy": f.get("opened_by") or "", "openedAt": f.get("opened_at") or "",
         "submittedBy": f.get("submitted_by") or "", "submittedAt": f.get("submitted_at") or "",
         "reviewedBy": f.get("reviewed_by") or "", "reviewedAt": f.get("reviewed_at") or "",
         "reviewNote": f.get("review_note") or "", "selfReview": bool(f.get("self_review")),
         "allowLater": bool(cfg and cfg.get("allowLater")),
         "later": later_brief(f, e, later), "stats": folder_stats(f, items)}
    # 黄牌「无票、未登记后补」只给允许后补的付款类模板（报销不亮：纸票常后放、餐补本就没票）；附件还在拉时先不亮（票可能马上就到）
    bills = [i for i in items if i.get("status") != "removed" and i.get("review") != "void"
             and i.get("kind") in ("invoice", "receipt")]
    v["laterMissing"] = bool(v["allowLater"] and not is_reimb(f.get("template")) and v["later"] is None and not bills
                             and (f.get("attach_status") or "none") not in ("pending", "running"))
    if with_form and not mobile:
        v["form"] = f.get("form_json") or []
    return v


def folder_views(folders, mobile=False, settings=None):
    """多个票夹一起出视图（票、后补单各一次查询取全）。"""
    folders = [f for f in (folders or []) if f]
    e = E()
    by = S.items_of_folders(e, [f["id"] for f in folders])
    lm = S.later_open_for_folders(e, folders)
    st = settings or get_settings()
    return [folder_view(f, by.get(f["id"], []), mobile, st, later=lm.get(f["id"])) for f in folders]


def later_view(l, items=None, today=None):
    """后补单行 → 前端 Later（§5.1）。items＝这张后补单登记的票（给了才算 sellerMismatch）。"""
    if not l:
        return None
    today = today or datetime.now().strftime("%Y-%m-%d")
    days = l.get("days_left")
    if days is None and l.get("expect_date"):
        try:
            days = (datetime.strptime(l["expect_date"][:10], "%Y-%m-%d") - datetime.strptime(today, "%Y-%m-%d")).days
        except ValueError:
            days = None
    active = l.get("status") in ("open", "partial")
    exp = l.get("expect_amount")
    rem = None
    if exp is not None:
        rem = round(max(0.0, float(exp) - float(l.get("received_amount") or 0) - float(l.get("unregistered_amount") or 0)), 2)
    mism = any((i.get("flags_json") or {}).get("sellerMismatch") for i in (items or [])
               if i.get("status") != "removed" and i.get("review") != "void")
    return {"id": l["id"], "folderId": l.get("folder_id"), "instId": l.get("inst_id") or "",
            "businessId": l.get("business_id") or "", "template": l.get("template") or "",
            "applicant": l.get("applicant") or "", "dept": l.get("dept") or "", "company": l.get("company") or "",
            "payee": {"name": l.get("payee_name") or "", "bank": l.get("payee_bank") or "", "account": l.get("payee_account") or ""},
            "payAmount": l.get("pay_amount"), "reason": l.get("reason") or "", "erpNo": l.get("erp_no") or "",
            "invKind": l.get("inv_kind") or "", "taxRate": l.get("tax_rate") or "", "expectDate": l.get("expect_date") or "",
            "expectAmount": exp, "receivedAmount": l.get("received_amount") or 0.0,
            "unregisteredAmount": l.get("unregistered_amount") or 0.0, "remaining": rem,
            "receiver": l.get("receiver") or "", "receiverName": l.get("receiver_name") or l.get("receiver") or "",
            "filedBy": l.get("filed_by") or "", "filedVia": l.get("filed_via") or "", "status": l.get("status") or "open",
            "overdue": bool(l.get("overdue")) if "overdue" in l else bool(active and days is not None and days < 0),
            "daysLeft": days, "lastRemindAt": l.get("last_remind_at") or "", "remindCount": int(l.get("remind_count") or 0),
            "note": l.get("note") or "", "sellerMismatch": bool(mism),
            "closedBy": l.get("closed_by") or "", "closedAt": l.get("closed_at") or "", "closeNote": l.get("close_note") or "",
            "createdAt": l.get("created_at") or "", "updatedAt": l.get("updated_at") or ""}


def log_view(r):
    """留痕行 → {id, ts, user, action, detail, itemId, laterId, folderId}（台账/审核页按 itemId 筛单张票的历史）。"""
    return {"id": r["id"], "ts": r.get("ts") or "", "user": r.get("user") or "", "action": r.get("action") or "",
            "detail": r.get("detail"), "itemId": r.get("item_id"), "laterId": r.get("later_id"), "folderId": r.get("folder_id")}


def attach_views(results, mobile=False):
    """进票结果补上视图：有 itemId 的加 item，action=folder 的加 folder。原地改并返回。票一次查全（大压缩包几百个结果也只查一次）。"""
    e = E()
    ids = [r["itemId"] for r in results if r.get("itemId")]
    rows = S.items_get(e, ids)
    idx = _file_index(e, [r.get("folder_id") for r in rows.values() if r])
    for r in results:
        if r.get("itemId") and rows.get(r["itemId"]):
            r["item"] = item_view(rows[r["itemId"]], idx, mobile)
        if r.get("action") == "folder" and r.get("folderId"):
            r["folder"] = folder_view(S.folder_get(e, r["folderId"]), mobile=mobile)
    return results


# ───────────────────────── 查重 / 校验 ─────────────────────────

def _doc_of_item(it):
    """库里的票 → 识别内核的 Doc 结构（给 validate/deduct_suggest/第二拨识别用）。"""
    d = ip.new_doc(int(it.get("page") or 0))
    for f, col in FIELD_COLS:
        d[f] = it.get(col)
    fl = it.get("flags_json") if isinstance(it.get("flags_json"), dict) else {}
    d.update(isInvoice=(it.get("kind") == "invoice"), kind=it.get("kind") or "other", invType=it.get("inv_type") or None,
             typeLabel=it.get("type_label") or None, qrType=it.get("qr_type") or None, lines=list(it.get("lines_json") or []),
             fieldSrc=_copy(it.get("field_src_json") or {}), pending=list(it.get("pending_json") or []),
             warnings=list(fl.get("_warnings") or []))
    d["qrRaw"] = fl.get("_qrRaw") or _qr_raw_of(it)
    return d


def _qr_raw_of(it):
    # 没存原始码串的老票：用字段拼回一条等价的发票码（只给第二拨识别定方向、定位用）
    qt, num = it.get("qr_type"), it.get("number")
    if not qt or not num or not it.get("issue_date"):
        return None
    money = it.get("total") if len(str(num)) == 20 else it.get("amount")
    if money is None:
        return None
    s = "01,%s,%s,%s,%.2f,%s,%s," % (qt, it.get("code") or "", num, float(money), str(it["issue_date"]).replace("-", ""),
                                     it.get("check_code") or "")
    return s if ip.parse_invoice_qr(s) else None


def is_reimb(tpl):
    """报销类模板（费用报销等）：收款方是报销人自己、纸票常后放、餐补本就没票。
    V2.621 起报销也能登记发票后补（allowLater），但下面这些付款单专属的判断仍不适用于报销。"""
    return "报销" in (tpl or "")


def _payee_for_check(folder, st):
    # 只有付款单才比"销方＝收款方"：费用报销的收款方是报销人自己，比了全是假红（报销允许后补也一样不比）
    name = (folder or {}).get("payee_name")
    if not name:
        return None
    tpl = (folder or {}).get("template") or ""
    if is_reimb(tpl):
        return None
    cfg = template_cfg(tpl, st)
    return name if ("公对公" in tpl or (cfg and cfg.get("allowLater"))) else None


def validate_flags(doc, folder, st=None):
    """票面校验 → flags（抬头 vs 票夹公司/本公司清单、公对公销方 vs 收款方、金额自洽、码面一致）。非发票 → {}。"""
    if (doc or {}).get("kind") != "invoice":
        return {}
    st = st or get_settings()
    return ip.validate(doc, companies=st.get("company") or None, expected_buyer=(folder or {}).get("company") or None,
                       payee=_payee_for_check(folder, st))


def suggest_deduct(doc, flags):
    """可否抵扣建议 (yes|no|'', 原因)：内核按票种/类别给；抬头不对一律 no（内核不看抬头）。"""
    s, why = ip.deduct_suggest(doc)
    if s and (flags.get("buyerMismatch") or flags.get("buyerNotCompany")):
        return "no", "发票抬头不是本公司（或不是这张单的公司），不能抵扣"
    return s or "", why or ""


class _DupGuard:
    """_DUP_LOCK 的包装（可重入）：记本线程嵌套深度。锁里要重算的后补单先攒着，最外层出锁后再算——
    后补重算要拿后补池的锁、还可能发钉钉，不能在查重锁里做（锁顺序固定：查重锁在外、后补锁在内，且不在查重锁里等网络）。"""

    def __enter__(self):
        _DUP_LOCK.acquire()
        _TL.depth = getattr(_TL, "depth", 0) + 1
        return self

    def __exit__(self, *exc):
        _TL.depth -= 1
        ids = None
        if _TL.depth == 0 and getattr(_TL, "later_q", None):
            ids, _TL.later_q = set(_TL.later_q), set()
        _DUP_LOCK.release()
        if ids:
            _later_sync(ids)
        return False


_TL = threading.local()
_dup_guard = _DupGuard()


def _later_sync(lids):
    """挂着后补单的票变了（移除/改金额/改票种/重复标记变化/识别完成）→ 重算那几张后补单的已到金额和状态。
    在查重锁里调用时先攒着、出锁再算（见 _DupGuard）。后补单逻辑在 routers/invoice_books（延迟导入，避免循环导入）；
    它收齐时会发"已签收"钉钉，所以本函数只能在线程池/后台线程里跑，不能直接在 async 路由里调。"""
    ids = {int(x) for x in (lids or []) if x}
    if not ids:
        return
    if getattr(_TL, "depth", 0) > 0:
        q = getattr(_TL, "later_q", None)
        if q is None:
            q = _TL.later_q = set()
        q.update(ids)
        return
    try:
        from routers import invoice_books as B
    except Exception as ex:
        print("[发票管家] 后补单重算没做（模块导入失败）：%s" % ex)
        return
    for lid in sorted(ids):
        try:
            B._later_recalc(lid, SYSTEM_USER)
        except Exception as ex:
            print("[发票管家] 后补单#%s 重算失败：%s" % (lid, ex))


def _dup_rank(g):
    # 谁先拿到这个号码谁排前：按拿到查重键的时间（微秒），同时或老数据没记时间再按 id
    return (g.get("dup_at") or "", g["id"])


def _split_ok(rows):
    """这几张同号票都是拆分票、且分摊合计不超票面 → True（合法拆分，不算重复）。"""
    if not rows or not all(g.get("split") for g in rows):
        return False
    cap = max([float(g["total"]) for g in rows if g.get("total") is not None] or [0])
    used = sum((float(g["alloc"]) if g.get("alloc") is not None else float(g.get("total") or 0)) for g in rows)
    return cap > 0 and used <= cap + 0.005


def dup_of(e, key, item_id=None, me=None):
    """查重结论 → flags.dup dict 或 None。
    先拿到号码的算正主：只和"比我先拿到这个查重键"的票比（dup_at 早的，同时再比 id）——照片先登记、号码后来
    才识别出/补填的票，和已经挂在别处（甚至已审）的同号票比时是它自己算重复，不会反过来把人家挤成重复。
    me：这张票（查重键刚变时库里还是旧键，调用方把带新键/新 dup_at 的行传进来）；新登记（item_id=None）→ 和全组比。
    拆分放行：我和比我先到的同号票全是拆分票、且分摊合计不超票面 → 不算重复（只标超额的那张、或不拆分的后来者）。
    期初（票总管）命中也算重复。"""
    if not key:
        return None
    group = S.items_by_dup(e, key)
    if me is None and item_id is not None:
        me = next((g for g in group if g["id"] == item_id), None)
    mid = me["id"] if me else item_id
    if me is None or me.get("dup_key") != key:
        others = [g for g in group if g["id"] != mid]
    else:
        others = [g for g in group if g["id"] != mid and _dup_rank(g) < _dup_rank(me)]
    if others and me and _split_ok(others + [me]):
        others = []
    others.sort(key=_dup_rank)
    if others:
        o = others[0]
        f = o.get("folder") or {}
        return {"kind": "item", "itemId": o["id"], "folderId": o.get("folder_id"), "businessId": f.get("business_id") or "",
                "title": f.get("title") or "", "applicant": f.get("applicant") or "", "at": o.get("created_at") or "",
                "source": o.get("origin") or ""}
    op = S.opening_match(e, key)
    if op:
        return {"kind": "opening", "at": op.get("imported_at") or "", "source": op.get("source") or "期初导入",
                "ref": op.get("ref") or ""}
    return None


def recheck_dup_group(key, skip=None):
    """同一查重键的一组票重判"重复"标记（有票被移除/作废/改号/拆分后调）。只改 flags.dup。
    重复标记变了的票若挂着后补单，顺手重算那张后补单（重复票不算已到）。→ 这些后补单号的集合。
    会重算后补单（可能发钉钉），async 路由里请用 run_in_threadpool 调。"""
    if not key:
        return set()
    e = E()
    lids = set()
    with _dup_guard:
        for g in S.items_by_dup(e, key):
            if g["id"] == skip:
                continue
            fl = dict(g.get("flags_json") or {})
            d = dup_of(e, key, item_id=g["id"], me=g)
            if json.dumps(fl.get("dup"), sort_keys=True, default=str) == json.dumps(d, sort_keys=True, default=str):
                continue
            if d:
                fl["dup"] = d
            else:
                fl.pop("dup", None)
            S.item_update(e, g["id"], flags_json=fl)
            if g.get("later_id"):
                lids.add(g["later_id"])
        _later_sync(lids)          # 在锁里只是攒着，出锁再算
    return lids


def item_dup_key(code, number, issue_date, total):
    """查重键：有号码就算（发票、收据、其它都算——把发票改成收据也躲不过查重）；凑不齐 → ''。"""
    return ip.dup_key(code, number, issue_date, total) or ""


def _seller_name_known(e, it):
    """同税号的销方名称在别的票上已由人核过（或从电子原件读到），且和这张一致 → True。
    只认人核过/原件来的：识别出来又被系统放过的不算，免得识别错一次就一路放行。"""
    tid = (it.get("seller_tax_id") or "").strip()
    nm = ip.normalize_name(it.get("seller_name") or "")
    if not tid or not nm:
        return False
    with e.connect() as cx:
        rows = [S._row(r) for r in cx.execute(select(S.ITEM).where(
            S.ITEM.c.seller_tax_id == tid, S.ITEM.c.id != it["id"],
            or_(S.ITEM.c.status.is_(None), S.ITEM.c.status != "removed")).order_by(S.ITEM.c.id.desc()).limit(50))]
    for r in rows:
        src = (r.get("field_src_json") or {}).get("sellerName") or {}
        if src.get("src") in TRUSTED_SRC and "sellerName" not in (r.get("pending_json") or []) \
                and ip.normalize_name(r.get("seller_name") or "") == nm:
            return True
    return False


def auto_checked(e, it, st):
    """识别来的「待核」字段里系统能自己核的 → {字段: 依据}：抬头/购方税号在公司清单里、销方税号校验位正确、
    金额＋税额＝二维码（或原件）的价税合计、税额÷金额＝税率、销方名称与人核过的同税号票一致。项目类别不自动核（决定能否抵扣）。"""
    pend = list(it.get("pending_json") or [])
    if not pend or it.get("kind") != "invoice":
        return {}
    fs = it.get("field_src_json") or {}
    out = {}
    comps = st.get("company") or []
    names = {ip.normalize_name(c.get("name") or "") for c in comps if c.get("name")}
    tids = {(c.get("taxId") or "").upper() for c in comps if c.get("taxId")}
    if "buyerName" in pend and it.get("buyer_name") and ip.normalize_name(it["buyer_name"]) in names:
        out["buyerName"] = "与公司清单一致"
    if "buyerTaxId" in pend and (it.get("buyer_tax_id") or "").upper() in tids:
        out["buyerTaxId"] = "与公司清单一致"
    stid = (it.get("seller_tax_id") or "").upper()
    if "sellerTaxId" in pend and len(stid) == 18 and ip.uscc_ok(stid):
        out["sellerTaxId"] = "信用代码校验位正确"
    a, t, tt = it.get("amount"), it.get("tax"), it.get("total")
    tsrc = (fs.get("total") or {}).get("src")
    if a is not None and t is not None and tt is not None and "total" not in pend and tsrc in TRUSTED_SRC \
            and abs(float(a) + float(t) - float(tt)) <= 0.011:
        why = "金额＋税额＝%s价税合计" % ("二维码" if tsrc == "qr" else "票面")
        for k in ("amount", "tax"):
            if k in pend:
                out[k] = why
    rate = str(it.get("tax_rate") or "").strip()
    m = re.fullmatch(r"(\d{1,2}(?:\.\d+)?)%", rate)
    if "taxRate" in pend and m and a and t is not None and float(a) > 0 \
            and abs(float(a) * float(m.group(1)) / 100 - float(t)) <= max(0.02, float(a) * 0.0005):
        out["taxRate"] = "税额÷金额＝%s" % rate
    if "sellerName" in pend and _seller_name_known(e, it):
        out["sellerName"] = "与人核过的同税号票一致"
    return out


def refresh_item(item_id, settings=None, user=SYSTEM_USER):
    """按票当前字段重算：查重键、重复标记、校验 flags、可否抵扣建议、销方档案；挂着后补单的顺手重算后补单。返回最新行。
    号码刚拿到（查重键变了）而同票夹里已有同号的票 → 两张并成一张（和"同票夹再进同一张票＝合并"一致），返回并入的那张
    （行里带 _merged_from＝被并掉的这张的 id）。会重算后补单（可能发钉钉）：async 路由里请用 run_in_threadpool 调。"""
    e = E()
    it = S.item_get(e, item_id)
    if not it:
        return None
    folder = S.folder_get(e, it.get("folder_id")) or {}
    st = settings or get_settings()
    d = _doc_of_item(it)
    old = it.get("flags_json") if isinstance(it.get("flags_json"), dict) else {}
    flags = {k: v for k, v in old.items() if k in STICKY_FLAGS}
    key = item_dup_key(it.get("code"), it.get("number"), it.get("issue_date"), it.get("total"))
    old_key = it.get("dup_key") or ""
    changed = key != old_key
    dup_at = (_now_us() if key else None) if changed else it.get("dup_at")
    live = it.get("status") != "removed" and it.get("review") != "void"
    with _dup_guard:
        if changed and key and live and it.get("review") not in ("approved", "void"):
            twin = next((g for g in S.items_by_dup(e, key)
                         if g["id"] != it["id"] and g.get("folder_id") == it.get("folder_id")), None)
            if twin:
                return _merge_twin(e, it, twin, old_key, user)
        me = dict(it, dup_key=key, dup_at=dup_at)
        dup = dup_of(e, key, item_id=it["id"], me=me) if (key and live) else None
        if dup:
            flags["dup"] = dup
        flags.update(validate_flags(d, folder, st))
        sug, why = suggest_deduct(d, flags)
        upd = dict(dup_key=key, dup_at=dup_at, flags_json=flags, deduct_suggest=sug, deduct_reason=why)
        auto = auto_checked(e, it, st) if live and it.get("review") not in ("approved", "void") else {}
        if auto:
            fs = _copy(it.get("field_src_json") or {})
            for k, why_k in auto.items():
                meta = dict(fs.get(k)) if isinstance(fs.get(k), dict) else {"src": "ocr", "page": int(it.get("page") or 0), "box": None}
                meta["sys"] = why_k
                fs[k] = meta
            upd.update(field_src_json=fs, pending_json=[p for p in (it.get("pending_json") or []) if p not in auto])
        S.item_update(e, it["id"], **upd)
        if changed:
            # 号码变了：老组少了一张要重判；新组里先到的票不受这张后来者影响（它们排在前面），重判只为稳妥
            for k in (old_key, key):
                recheck_dup_group(k, skip=it["id"])
        if it.get("later_id"):
            _later_sync([it["later_id"]])
    if it.get("kind") == "invoice" and it.get("seller_tax_id"):
        try:
            S.seller_seen(e, it["seller_tax_id"], it.get("seller_name"), datetime.now().strftime("%Y-%m-%d"), it["id"])
        except Exception:
            pass
    return S.item_get(e, item_id)


def _merge_twin(e, it, twin, old_key, user):
    """it 刚认出号码（识别/补填/税局文件），同票夹里已有同号的 twin → it 并进 twin（文件挂过去、缺的字段补上），it 标移除。
    典型：先拍的照片二维码没读出来、又扫了码登记了一张，照片识别完才知道是同一张——不该标成"重复票"卡住提交。"""
    f = S.file_get(e, it["file_id"]) if it.get("file_id") else None
    d = _doc_of_item(it)
    S.item_update(e, it["id"], status="removed", proc_status="done", proc_error="")
    if it.get("later_id") and not twin.get("later_id"):
        S.item_update(e, twin["id"], later_id=it["later_id"])
    log(user, "并入同票夹同一张票", it.get("folder_id"), it["id"], it.get("later_id"),
        {"into": twin["id"], "number": it.get("number") or ""})
    _merge_same(e, S.item_get(e, twin["id"]), f, d, it.get("origin") or "", user, bool(it.get("paper")), "",
                {"mergedFrom": it["id"]})
    if old_key:
        recheck_dup_group(old_key, skip=it["id"])
    _later_sync([it.get("later_id")])
    out = S.item_get(e, twin["id"])
    if out is not None:
        out["_merged_from"] = it["id"]
    return out


# ───────────────────────── 进票流水线 ─────────────────────────

def _res(name, action, msg, **kw):
    r = {"name": name, "action": action, "msg": msg}
    r.update(kw)
    return r


def _no_folder(name):
    return _res(name, "noFolder", "还没打开票夹：先扫审批单右上角的二维码（或新建手工票夹），再放发票")


def _is_paper(origin, ftype):
    return origin in PAPER_ORIGINS or (origin == "upload" and ftype == "image")


def _doc_cols(d):
    cols = {col: d.get(f) for f, col in FIELD_COLS}
    for k in ("amount", "tax", "total"):
        if cols[k] is not None:
            cols[k] = _money_val(cols[k])     # NaN/无穷大/超大数一律当没读到（库里存不下，也不是真票面）
    cols.update(inv_type=d.get("invType") or "", type_label=d.get("typeLabel") or "", qr_type=d.get("qrType") or "",
                lines_json=d.get("lines") or [], field_src_json=d.get("fieldSrc") or {}, pending_json=d.get("pending") or [])
    return cols


def _item_msg(d, kind):
    if kind != "invoice":
        return ""
    bits = []
    if d.get("number"):
        bits.append("号码 %s" % d["number"])
    if d.get("total") is not None:
        bits.append("¥%s" % _money_str(d["total"]))
    elif d.get("amount") is not None:
        bits.append("金额 ¥%s" % _money_str(d["amount"]))
    return "，".join(bits)


def _dup_msg(dup):
    if dup.get("kind") == "opening":
        return "重复票！期初（%s）里已有这张票，不能再挂" % (dup.get("source") or "期初导入")
    where = dup.get("title") or (("审批单 " + dup["businessId"]) if dup.get("businessId") else "别的票夹")
    who = ("（申请人 %s）" % dup["applicant"]) if dup.get("applicant") else ""
    return "重复票！已于 %s 挂在「%s」%s上，不能再挂；请移除这张" % ((dup.get("at") or "")[:10], where, who)


def _register_doc(folder, f, d, origin, user, later_id=None, paper=False, review=None, log_extra=None, name=""):
    """一张解析好的票（Doc）登记进票夹 → 结果 dict。同票夹同号＝合并（纸质件到件），别处有＝标重复。
    查重键：有号码就算（收据/其它也算）。"""
    e = E()
    st = get_settings()
    kind = d.get("kind") if d.get("kind") in KINDS else "other"
    key = item_dup_key(d.get("code"), d.get("number"), d.get("date"), _money_val(d.get("total"))) or None
    with _dup_guard:
        if key:
            mine = [x for x in S.items_by_dup(e, key) if x.get("folder_id") == folder["id"]]
            if mine:
                return _merge_same(e, mine[0], f, d, origin, user, paper, name, log_extra)
        flags = {}
        dup = dup_of(e, key) if key else None
        if dup:
            flags["dup"] = dup
        flags.update(validate_flags(d, folder, st))
        if d.get("qrRaw"):
            flags["_qrRaw"] = d["qrRaw"]
        if d.get("warnings"):
            flags["_warnings"] = list(d["warnings"])
        if d.get("region"):
            flags["_region"] = d["region"]     # 一张照片拍了几张票：这张在图上的那一块
        sug, why = suggest_deduct(d, flags)
        rv = review or ("pending" if (later_id or folder.get("status") in ("submitted", "approved")) else "draft")
        pending_ocr = bool(d.get("needOcr") and f)
        iid = S.item_insert(e, folder_id=folder["id"], file_id=(f or {}).get("id"), page=int(d.get("page") or 0),
                            kind=kind, origin=origin, dup_key=key or "", dup_at=_now_us() if key else None,
                            proc_status="pending" if pending_ocr else "done", paper=1 if paper else 0,
                            flags_json=flags, deduct_suggest=sug, deduct_reason=why, later_id=later_id, review=rv,
                            created_by=_uname(user), **_doc_cols(d))
    if f and not f.get("item_id"):
        S.file_update(e, f["id"], item_id=iid)
    if kind == "invoice" and d.get("sellerTaxId"):
        try:
            S.seller_seen(e, d["sellerTaxId"], d.get("sellerName"), datetime.now().strftime("%Y-%m-%d"), iid)
        except Exception:
            pass
    if dup:
        action, msg = "dup", _dup_msg(dup)
    elif kind == "other":
        action = "other"
        msg = ("已留存，正在识别是不是发票…" if pending_ocr else
               "已留存（不是发票：%s）" % (d.get("typeLabel") or name or "附件"))
    else:
        action = "item"
        head = "已登记收据" if kind == "receipt" else "已登记"
        tail = "；票面其它内容正在识别…" if pending_ocr else ""
        m = _item_msg(d, kind)
        msg = head + ("：" + m if m else "") + tail
    det = {"name": name, "origin": origin, "kind": kind, "number": d.get("number") or "", "action": action}
    det.update(log_extra or {})
    log(user, "登记重复票" if dup else "登记票据", folder["id"], iid, later_id, det)
    return _res(name, action, msg, itemId=iid, folderId=folder["id"])


def _field_diffs(ex, d):
    """新来的票面值和库里这张票对不上的字段 → {字段: [库里值, 新值]}（两边都有值且不同才算；金额按两位小数比）。"""
    out = {}
    for fld, col in FIELD_COLS:
        nv = d.get(fld)
        if fld in MONEY_FIELDS:
            nv = _money_val(nv)
        cur = ex.get(col)
        if _empty(nv) or _empty(cur):
            continue
        if not _same(cur, nv):
            out[fld] = ["" if cur is None else str(cur), str(nv)]
    return out


def _merge_frozen(e, ex, f, d, origin, user, paper, name, log_extra):
    """已审/已作废的票，或票夹已提交/已审（只许确认纸质件）：票面字段、主文件一概不动。
    新文件只挂成附件（纸质件到了就标纸质件已到）；值对不上 → 标 postApprovalMismatch（已审票）或 fileMismatch 并留痕，交审核看。"""
    rv = ex.get("review") or "draft"
    diffs = _field_diffs(ex, d)
    upd = {}
    if f:
        S.file_update(e, f["id"], item_id=ex["id"], role="paper" if paper else "attachment")
    if paper and not ex.get("paper"):
        upd["paper"] = 1
    flag = "postApprovalMismatch" if rv in ("approved", "void") else "fileMismatch"
    if diffs:
        fl = dict(ex.get("flags_json") or {})
        fl[flag] = sorted(set(fl.get(flag) or []) | set(diffs))
        upd["flags_json"] = fl
    if upd:
        S.item_update(e, ex["id"], **upd)
    num = ex.get("number") or ""
    det = {"name": name, "origin": origin, "number": num, "locked": True, "review": rv}
    if diffs:
        det["diff"] = diffs
    det.update(log_extra or {})
    if diffs:
        labels = "、".join(FIELD_LABEL.get(k, k) for k in diffs)
        msg = ("这张票已审核（或票夹已提交），票面没改；新%s和票面的%s对不上，已标出来请审核核对"
               % ("扫到的二维码" if not f else "文件", labels))
        log(user, "已审票来了不一致的文件" if flag == "postApprovalMismatch" else "锁定票来了不一致的文件",
            ex["folder_id"], ex["id"], ex.get("later_id"), det)
    else:
        msg = ("纸质件已到：这张票票夹里已经有了（号码 %s），票面没动" % num) if paper else \
            ("这张票票夹里已经有了（号码 %s），文件已挂上，票面没动" % num)
        log(user, "纸质件到件" if paper else "挂上同一张票的文件", ex["folder_id"], ex["id"], ex.get("later_id"), det)
    return _res(name, "confirm", msg, itemId=ex["id"], folderId=ex["folder_id"])


def _merge_same(e, ex, f, d, origin, user, paper, name, log_extra, frozen=False):
    """同票夹再进同一张票＝不新建：补缺字段、挂上新文件；纸质证据到了就标"纸质件已到"。
    电子原件后到而原来只有照片/二维码 → 原件升为主文件（照片降为纸质件），票面字段以原件为准（人工改过的除外）。
    已审/已作废的票、或 frozen=True（票夹已提交/已审，只许确认纸质件）→ _merge_frozen：字段和主文件一概不动。
    待审的票：库里值来自二维码/原件/人工（TRUSTED_SRC）而新值不同 → 不覆盖，放进"待核"并标 fileMismatch；
    识别来的值被新值改了也留在"待核"里，让审核人再看一眼。"""
    if frozen or (ex.get("review") or "draft") in ("approved", "void"):
        return _merge_frozen(e, ex, f, d, origin, user, paper, name, log_extra)
    guard = ex.get("review") == "pending"
    ex_file = S.file_get(e, ex["file_id"]) if ex.get("file_id") else None
    new_is_img = bool(f) and (f.get("mime") or "").startswith("image/")
    promote = bool(f) and (ex_file is None or ((ex_file.get("mime") or "").startswith("image/") and not new_is_img))
    fs = _copy(ex.get("field_src_json") or {})
    pend = list(ex.get("pending_json") or [])
    upd = {}
    mism = {}
    new_pend = set(d.get("pending") or [])
    for fld, col in FIELD_COLS:
        nv = d.get(fld)
        if fld in MONEY_FIELDS:
            nv = _money_val(nv)
        if _empty(nv):
            continue
        cur = ex.get(col)
        src = (fs.get(fld) or {}).get("src")
        differs = not _empty(cur) and not _same(cur, nv)
        if guard and differs and src in TRUSTED_SRC:
            # 待审的票：二维码/原件/人工核过的值不让后来的文件改掉，放"待核"交审核人对
            mism[fld] = [str(cur), str(nv)]
            if fld not in pend:
                pend.append(fld)
            continue
        if src == "manual" and not _empty(cur):
            continue
        ocr_only = fld in new_pend
        if _empty(cur) or (not ocr_only and (src == "ocr" or fld in pend or promote)):
            upd[col] = nv
            nsrc = dict((d.get("fieldSrc") or {}).get(fld) or {"src": "qr", "page": 0, "box": None})
            if not promote:
                nsrc["box"] = None    # 值来自另一份文件，框画不到主图上
            fs[fld] = nsrc
            if ocr_only or (guard and differs):    # 待审的票被改了值：留在"待核"里让审核人再看一眼
                if fld not in pend:
                    pend.append(fld)
            elif fld in pend:
                pend.remove(fld)
    if promote:
        newsrc = d.get("fieldSrc") or {}
        for fld, meta in fs.items():   # 主图换了：新文件没给出处的字段，旧框对不上了
            if isinstance(meta, dict) and meta.get("box") and fld not in newsrc:
                meta["box"] = None
        upd["file_id"] = f["id"]
        upd["page"] = int(d.get("page") or 0)
        if ex_file:
            S.file_update(e, ex_file["id"], role="paper")
        S.file_update(e, f["id"], item_id=ex["id"])
        if d.get("needOcr") and new_is_img:
            upd.update(proc_status="pending", proc_tries=0, proc_error="")
    elif f:
        S.file_update(e, f["id"], item_id=ex["id"], role="paper" if paper else "attachment")
    if paper:
        upd["paper"] = 1
    if not ex.get("lines_json") and d.get("lines"):
        upd["lines_json"] = d["lines"]
    if (not ex.get("inv_type") or ex.get("inv_type") == "other") and d.get("invType"):
        upd["inv_type"] = d["invType"]
    if not ex.get("type_label") and d.get("typeLabel"):
        upd["type_label"] = d["typeLabel"]
    if not ex.get("qr_type") and d.get("qrType"):
        upd["qr_type"] = d["qrType"]
    fl = dict(ex.get("flags_json") or {})
    if d.get("qrRaw") and not fl.get("_qrRaw"):
        fl["_qrRaw"] = d["qrRaw"]
        upd["flags_json"] = fl
    if mism:
        fl["fileMismatch"] = sorted(set(fl.get("fileMismatch") or []) | set(mism))
        upd["flags_json"] = fl
    upd["field_src_json"] = fs
    upd["pending_json"] = pend
    S.item_update(e, ex["id"], **upd)
    it = refresh_item(ex["id"], user=user)
    num = (it or ex).get("number") or ""
    if paper:
        msg = "纸质件已到：这张票票夹里已经有了（号码 %s），不算重复" % num
    else:
        msg = "这张票票夹里已经有了（号码 %s），已合并，不重复登记" % num
    if mism:
        msg += "；%s和票面对不上，已标「待核」请审核核对" % "、".join(FIELD_LABEL.get(k, k) for k in mism)
    det = {"name": name, "origin": origin, "number": num, "promote": promote}
    if mism:
        det["diff"] = mism
    det.update(log_extra or {})
    log(user, "纸质件到件" if paper else "合并同一张票", ex["folder_id"], ex["id"], ex.get("later_id"), det)
    return _res(name, "confirm", msg, itemId=ex["id"], folderId=ex["folder_id"])


def _switch_to_approval(link, user, name):
    r = open_approval_folder(link, _uname(user))
    if not r.get("ok"):
        return _res(name, "error", r.get("msg") or "审批单打不开")
    fo = r["folder"]
    S.desk_set(E(), _uname(user), fo["id"])
    return _res(name, "folder", "这是审批单：已打开票夹「%s」" % (fo.get("title") or fo.get("business_id") or fo["id"]),
                folderId=fo["id"])


def folder_locked(folder, origin, later_id=None):
    """收票工作台/手机/扫码枪（人手送进来的）往已提交或已审核的票夹里放票 → True（不许：票夹一提交就只有审核人能动）。
    后补池收票（带 later_id）、后台拉审批附件（origin=attachment/photo_field）不受此限。"""
    return bool(folder) and later_id is None and origin in USER_ORIGINS \
        and (folder.get("status") or "collecting") not in OPEN_STATUSES


def _locked_res(name, folder):
    st = STATUS_CN.get(folder.get("status"), folder.get("status") or "")
    return _res(name, "error", "这个票夹%s，不能再往里放票：要补票请让审核人先退回；先付款后补票的请到「发票后补池」收票" % st,
                folderId=folder["id"])


def ingest_bytes(folder_id, name, data, origin, user, later_id=None, role="original", dt_file_id=None,
                 review=None, allow_switch=None, log_extra=None, _depth=0, budget=None):
    """进票流水线（同步、CPU 重活，路由里用 run_in_threadpool 调）→ [结果 dict]。
    结果 {name, action, msg, itemId?, folderId?}：action ∈ item/confirm/dup/same/other/folder/error/noFolder
    （要给前端的 item/folder 视图用 attach_views(results) 补）。
    folder_id 可为 None：只认审批单打印件/审批码照片（→ 打开那张单的票夹，action=folder），其它回 noFolder。
    origin：upload/camera/phone/scanner/attachment/photo_field/later/taxpack；user＝工作台登录名。
    later_id：后补池收票时给（票直接进审核 pending）；review 可强制初始审核状态；
    allow_switch：审批单打印件是否当"扫审批单"切换票夹（默认人手送进来的才切，钉钉附件里的审批单 PDF 只留存）。
    票夹已提交/已审核时，人手送进来的文件一律不收（folder_locked），审批单打印件照样能切换票夹。
    budget：压缩包解开总量的额度 {"left": 字节}，一次上传请求/一次拉附件共用一份（调用方建，不给就本次单算 MAX_TOTAL）——
    防几个 KB 的压缩炸弹一包一包地撑满磁盘。磁盘快满（StoreRefused）→ 回 action=error，不抛。"""
    try:
        return _ingest_bytes(folder_id, name, data, origin, user, later_id, role, dt_file_id, review, allow_switch,
                             log_extra, _depth, budget if budget is not None else {"left": MAX_TOTAL})
    except StoreRefused as ex:
        return [_res(_clean_name(name), "error", str(ex))]


def _ingest_bytes(folder_id, name, data, origin, user, later_id, role, dt_file_id, review, allow_switch, log_extra,
                  _depth, budget):
    name = _clean_name(name)
    if not data:
        return [_res(name, "error", "文件是空的")]
    if len(data) > MAX_FILE:
        return [_res(name, "error", "「%s」超过 %dMB，请压缩后再传" % (name, MAX_FILE // MB))]
    e = E()
    if allow_switch is None:
        allow_switch = origin in USER_ORIGINS
    folder = S.folder_get(e, folder_id) if folder_id else None
    if folder:
        same = S.file_by_sha(e, folder["id"], hashlib.sha256(data).hexdigest())
        if same:
            return [_res(name, "same", "这份文件刚才已经放进来过了，没有重复登记", itemId=same.get("item_id"),
                         folderId=folder["id"])]
    locked = folder if folder_locked(folder, origin, later_id) else None
    if locked:
        folder = None          # 当成"没打开票夹"走：只认审批单打印件，其余回"票夹已锁"
    try:
        ftype = ip.sniff_type(name, data)
    except Exception:
        ftype = "other"
    kw = dict(later_id=later_id, role=role, dt_file_id=dt_file_id, review=review, log_extra=log_extra)
    if ftype == "zip":
        if _depth >= 2:
            return [_res(name, "error", "压缩包套得太深，请解开后再传")]
        if budget["left"] <= 0:
            return [_res(name, "error", "这一批压缩包解开后太大（超过 %dMB），这个包没处理：请分几次上传" % (MAX_TOTAL // MB))]
        try:
            files = ip.unpack_zip(data, max_total=budget["left"])
        except Exception as ex:
            return [_res(name, "error", "压缩包打不开：%s" % ex)]
        budget["left"] -= sum(len(b) for _n, b in files)
        out = [_res(name, "error", w) for w in (getattr(files, "warnings", None) or [])]
        if not files:
            out.append(_res(name, "error", "压缩包里没有能用的文件"))
            return out
        fid = folder_id
        for n, b in files:
            rs = ingest_bytes(fid, n, b, origin, user, allow_switch=allow_switch, _depth=_depth + 1, budget=budget, **kw)
            for r in rs:
                if r.get("action") == "folder" and r.get("folderId"):
                    fid = r["folderId"]
            out.extend(rs)
        return out
    if ftype == "pdf":
        return _ingest_pdf(folder, name, data, origin, user, allow_switch, locked=locked, **kw)
    if ftype == "image":
        return _ingest_image(folder, name, data, origin, user, allow_switch, locked=locked, **kw)
    if not folder:
        return [_locked_res(name, locked) if locked else _no_folder(name)]
    if ftype in ("ofd", "xml"):
        return _ingest_ofd_xml(folder, name, data, ftype, origin, user, **kw)
    return _ingest_plain(folder, name, data, ftype, origin, user, **kw)


def _register_all(folder, f, docs, origin, user, later_id, paper, review, log_extra, name):
    """一份文件里的几张票逐张登记；半路出错且文件还没挂上任何票 → 文件标 failed 再抛（重传/重拉能重新登记）。"""
    out = []
    try:
        for d in docs:
            out.append(_register_doc(folder, f, d, origin, user, later_id, paper, review, log_extra, name))
            f = S.file_get(E(), f["id"])
    except Exception:
        _fail_orphan_file(f)
        raise
    return out


def _pdf_preview_pages(docs):
    # 有票的页都要有预览（合并 PDF 第 6 张以后的票不能显示成第 1 页）；至少前 5 页，最多 30 页（与识别页数上限一致）
    need = max([int(d.get("page") or 0) for d in docs] + [0]) + 1
    return min(max(need, 5), 30)


def _ingest_pdf(folder, name, data, origin, user, allow_switch, later_id=None, role="original", dt_file_id=None,
                review=None, log_extra=None, locked=None):
    docs = ip.extract_pdf(data)
    if len(docs) == 1 and docs[0].get("kind") == "other" and docs[0].get("approvalLink") and (allow_switch or not folder):
        return [_switch_to_approval(docs[0]["approvalLink"], user, name)]
    if not folder:
        return [_locked_res(name, locked) if locked else _no_folder(name)]
    try:
        pages = ip.render_pdf(data, max_pages=_pdf_preview_pages(docs))
    except Exception:
        pages = []
    f = store_file(folder["id"], name, data, origin, user, role=role, mime="application/pdf", ext="pdf",
                   previews=[p["jpeg"] for p in pages], width=(pages[0]["w"] if pages else None),
                   height=(pages[0]["h"] if pages else None), dt_file_id=dt_file_id)
    return _register_all(folder, f, docs, origin, user, later_id, _is_paper(origin, "pdf"), review, log_extra, name)


def _ingest_image(folder, name, data, origin, user, allow_switch, later_id=None, role="original", dt_file_id=None,
                  review=None, log_extra=None, locked=None):
    try:
        v = ip.make_image_variants(data)
    except ValueError as ex:
        return [_res(name, "error", str(ex))]
    except Exception as ex:
        return [_res(name, "error", "图片打不开：%s" % ex)]
    d = ip.extract_image_fast(data)
    if d.get("approvalLink") and not d.get("isInvoice") and (allow_switch or not folder):
        return [_switch_to_approval(d["approvalLink"], user, name)]
    if not folder:
        return [_locked_res(name, locked) if locked else _no_folder(name)]
    rot = 0
    if d.get("isInvoice"):
        try:
            rot = int(ip.guess_rotation(data) or 0)   # 照片横着/倒着拍：按票面二维码方向摆正显示
        except Exception:
            rot = 0
    ext, mime = _ext_mime("image", name, v.get("orig_ext"))
    f = store_file(folder["id"], name, data, origin, user, role=role, mime=mime, ext=ext, previews=[v["preview"]],
                   thumbs=[v["thumb"]], orig_bytes=v["orig"], width=v.get("w"), height=v.get("h"), rotation=rot,
                   dt_file_id=dt_file_id)
    return _register_all(folder, f, [d], origin, user, later_id, _is_paper(origin, "image"), review, log_extra, name)


def _ingest_ofd_xml(folder, name, data, ftype, origin, user, later_id=None, role="original", dt_file_id=None,
                    review=None, log_extra=None):
    try:
        docs = ip.extract_ofd(data) if ftype == "ofd" else ip.extract_xml(data)
    except Exception as ex:
        docs = []
        err_msg = str(ex)
    else:
        err_msg = ""
    if not docs:
        d = ip.new_doc(0)
        d["warnings"].append(err_msg or "电子发票文件里没读到票面")
        docs = [d]
    previews = []
    for d in docs:
        try:
            jp = ip.render_virtual(d) if d.get("isInvoice") else b""
        except Exception:
            jp = b""
        previews.append(jp)
    if not all(previews):
        previews = []    # 一张没画出来就都不出预览：页码和票得一一对应
    ext, mime = _ext_mime(ftype, name)
    f = store_file(folder["id"], name, data, origin, user, role=role, mime=mime, ext=ext, previews=previews,
                   dt_file_id=dt_file_id)
    for i, d in enumerate(docs):
        # 虚拟图片一票一页：内核给的 fieldSrc 都是第 0 页，按票序改成第 i 页
        d["page"] = i if previews else 0
        for meta in (d.get("fieldSrc") or {}).values():
            if isinstance(meta, dict):
                meta["page"] = d["page"]
    return _register_all(folder, f, docs, origin, user, later_id, False, review, log_extra, name)


def _ingest_plain(folder, name, data, ftype, origin, user, later_id=None, role="original", dt_file_id=None,
                  review=None, log_extra=None):
    ext, mime = _ext_mime(ftype, name)
    f = store_file(folder["id"], name, data, origin, user, role="doc" if role == "original" else role, mime=mime,
                   ext=ext, dt_file_id=dt_file_id)
    return _register_all(folder, f, [ip.new_doc(0)], origin, user, later_id, False, review, log_extra, name)


def ingest_qr(folder_id, code_text, user, origin="scanner", later_id=None, review=None, log_extra=None):
    """扫码枪/手机扫到的发票二维码登记进票夹（没有图片，字段来自二维码，fieldSrc.src=qr）→ 结果 dict。
    扫到实物＝纸质件在手：同票夹已有这张票 → 标"纸质件已到"（action=confirm），不新建。
    票夹已提交/已审核（folder_locked）：只许给票夹里已有的这张票确认"纸质件已到"（票面一概不动），不许新登记。"""
    q = ip.parse_invoice_qr(code_text)
    if not q:
        return _res("扫码", "unknown", "这不是发票二维码")
    for k in ("total", "amount"):
        if q.get(k) is not None and _money_val(q[k]) is None:
            return _res("扫码", "unknown", "二维码里的金额读不出来，不像正常的发票二维码：请上传电子发票原件")
    folder = S.folder_get(E(), folder_id) if folder_id else None
    if not folder:
        return _no_folder("扫码")
    d = ip.new_doc(0)
    it, label = ip.QR_TYPES.get(q["qrType"], ("other", None))
    d.update(isInvoice=True, kind="invoice", invType=it, typeLabel=label, qrType=q["qrType"], code=q["code"],
             number=q["number"], date=q["date"], total=_money_val(q["total"]), amount=_money_val(q["amount"]),
             checkCode=q["checkCode"], qrRaw=q["raw"])
    for f in ("code", "number", "date", "total", "amount", "checkCode"):
        if d.get(f) is not None:
            d["fieldSrc"][f] = {"src": "qr", "page": 0, "box": None}
    if folder_locked(folder, origin, later_id):
        e = E()
        key = item_dup_key(d.get("code"), d.get("number"), d.get("date"), d.get("total"))
        with _dup_guard:
            mine = [x for x in S.items_by_dup(e, key) if x.get("folder_id") == folder["id"]] if key else []
            if mine:
                return _merge_same(e, mine[0], None, d, origin, user, True, "扫码", log_extra, frozen=True)
        return _locked_res("扫码", folder)
    r = _register_doc(folder, None, d, origin, user, later_id, True, review, log_extra, "扫码")
    if r.get("action") == "item":
        r["msg"] += "；明细等电子原件或税局文件包补全"
    return r


# ───────────────────────── 审批单 ─────────────────────────

def resolve_approval(code=None, inst_id=None):
    """审批码（钉钉短链接/含 procInstId 的链接）或审批编号，或直接给实例号 → 取单并规范化。
    → {ok, msg, norm(invoice_dingtalk.normalize_instance 结果), inst, procInstId}。
    钉钉没配置时 ok=False，msg 为内核原话（含「未配置钉钉（conf.ini [dingtalk]）」）。
    只接设置里配了的审批模板（别的审批单——调薪、人事等——不许拉进来建票夹、拉附件）。
    corpId 只从钉钉服务器回来的跳转里学（扫到的原文里自带的 corpid 不认），且只在还没设过时学。"""
    st = get_settings()
    tpls = st.get("templates") or []
    learn = ""
    if inst_id:
        iid = str(inst_id)
        g = idt.get_instance(iid)
        if not g.get("ok"):
            return {"ok": False, "msg": g.get("msg") or "取审批单失败"}
        inst = g["inst"]
    else:
        c = ip.classify_code(code or "")
        if c["kind"] == "approval_link":
            r = idt.resolve_link(c["value"])
            if not r.get("ok"):
                return {"ok": False, "msg": r.get("msg") or "审批单链接解析失败"}
            if r.get("corpId") and _corp_from_server(c["value"]):
                learn = r["corpId"]
            iid = r["procInstId"]
            g = idt.get_instance(iid)
            if not g.get("ok"):
                return {"ok": False, "msg": g.get("msg") or "取审批单失败"}
            inst = g["inst"]
        elif c["kind"] == "business_id":
            known = S.folder_by_business(E(), c["value"])
            if known and known.get("inst_id"):
                iid = known["inst_id"]
                g = idt.get_instance(iid)
                if not g.get("ok"):
                    return {"ok": False, "msg": g.get("msg") or "取审批单失败"}
                inst = g["inst"]
            else:
                fr = idt.find_by_business_id(c["value"], [t["name"] for t in tpls])
                if not fr.get("ok"):
                    return {"ok": False, "msg": fr.get("msg") or "按审批编号没找到单子"}
                iid, inst = fr["procInstId"], fr["inst"]
        else:
            return {"ok": False, "msg": "这不是审批单的二维码或审批编号"}
    n = idt.normalize_instance(inst, iid, tpls)
    if not n.get("instId"):
        n["instId"] = iid
    if n.get("template") not in [t.get("name") for t in tpls]:
        return {"ok": False, "msg": "这张审批单（%s）不是发票管家接的审批模板，不能建票夹：如确需接入，请管理员在设置 › 审批模板里加上"
                % (n.get("template") or n.get("title") or "模板认不出")}
    if learn:
        _learn_corp(learn)     # 取单成功后才学（扫一条打不开的假链接学不进去）
    return {"ok": True, "msg": "", "norm": n, "inst": inst, "procInstId": iid}


def _corp_from_server(link):
    """扫到的审批码本身不带 procInstId（钉钉 aflow 短链接），实例号和 corpid 是去钉钉服务器问回来的 → True。
    原文里就带 procInstId= 的链接（谁都能手搓一条）→ False：这种链接里的 corpid 不学。"""
    try:
        sp = urlsplit(str(link or "").strip())
        host_ok = sp.scheme in ("http", "https") and idt._is_dt_host(sp.hostname)
        self_carried = bool(idt._pick_ids(link)[0])
    except Exception:
        return False
    return bool(host_ok and not self_carried)


def open_approval_folder(code=None, user="", refresh=False, source="scan", inst_id=None):
    """扫到的审批码/编号（或实例号）→ 建或刷新票夹；新建或 refresh=True 时排队拉附件并唤醒后台线程。
    → {ok, msg, folder(行), created}；失败 {ok:False, msg}。不改当前票夹（调用方自己 desk_set）。"""
    r = resolve_approval(code, inst_id=inst_id)
    if not r.get("ok"):
        return r
    n = r["norm"]
    e = E()
    existed = S.folder_by_inst(e, n["instId"])
    fid = S.folder_upsert_from_approval(e, n, user, source=source)
    created = existed is None
    if created or refresh:
        if n.get("hasAttachments"):
            S.folder_update(e, fid, attach_status="pending", attach_msg="正在拉取审批单附件…")
            wake()
        else:
            S.folder_update(e, fid, attach_status="done", attach_msg="审批单上没有附件")
    folder = S.folder_get(e, fid)
    act = "扫审批单建票夹" if created else ("刷新审批单" if refresh else "打开票夹")
    log(user, act, fid, detail={"businessId": n.get("businessId"), "template": n.get("template"),
                                "attachments": len(n.get("attachments") or []), "photos": len(n.get("photos") or [])})
    if created or refresh:
        audit(user, act, folder.get("title") or n.get("businessId"), "票夹#%d 审批编号 %s" % (fid, n.get("businessId") or ""))
    title = folder.get("title") or n.get("businessId") or ("票夹#%d" % fid)
    msg = ("已打开票夹「%s」" % title) + ("，正在拉取附件…" if (created or refresh) and n.get("hasAttachments") else "")
    return {"ok": True, "msg": msg, "folder": folder, "created": created}


# ───────────────────────── 后台线程：拉附件 + 识别 ─────────────────────────
_WAKE = threading.Event()
_WORKER_LOCK = threading.Lock()
_WORKER_STATE = {"started": False, "lastError": "", "lastRun": ""}


def wake():
    """有新活（附件待拉/照片待识别）时叫醒后台线程，不用等 3 秒轮询。"""
    _WAKE.set()


def worker_step(max_items=3):
    """后台线程跑一轮：先拉附件、再识别。测试可直接调（不用等线程）。→ 这轮做了几件事。"""
    e = E()
    done = 0
    for f in S.folders_pending_attach(e, limit=2):
        if S.folder_claim_attach(e, f["id"]):
            done += 1
            try:
                _pull_attachments(f["id"])
            except Exception as ex:
                _WORKER_STATE["lastError"] = traceback.format_exc()[-2000:]
                S.folder_update(e, f["id"], attach_status="failed", attach_msg=("拉附件出错：%s" % ex)[:400])
    for it in S.items_pending_proc(e, limit=max_items):
        if S.item_claim(e, it["id"]):
            done += 1
            try:
                _process_item(it["id"])
            except Exception as ex:
                _WORKER_STATE["lastError"] = traceback.format_exc()[-2000:]
                S.item_update(e, it["id"], proc_status="failed", proc_error=("识别出错：%s" % ex)[:400])
    _WORKER_STATE["lastRun"] = now_s()
    return done


_POISON_MSG = "上次处理它时服务中断（可能文件过大或损坏），已跳过：请到钉钉下载核对后手工拖进来"


def _pull_one(e, fid, dtid, label, name, origin, fetch, fails, budget=None):
    """拉一个审批附件/图片并进票。拉之前落一行"正在拉"占位（按钉钉文件号），处理完删掉；
    进程在处理它时被杀（超大/损坏文件）→ 占位留下，下次拉到它直接跳过并报原因，其余附件照拉。"""
    ex = S.file_by_dt(e, fid, dtid)
    if ex:
        if ex.get("status") in ("pulling", "poison"):
            if ex.get("status") == "pulling":
                S.file_update(e, ex["id"], status="poison")
            fails.append("%s：%s" % (label, _POISON_MSG))
        return
    dl = fetch()
    if not dl.get("ok"):
        fails.append("%s：%s" % (label, dl.get("msg") or "下载失败"))
        return
    mark = S.file_insert(e, folder_id=fid, role="marker", origin=origin, name=_clean_name(name), mime="", ext="",
                         size=len(dl["bytes"]), status="pulling", dt_file_id=dtid, created_by=SYSTEM_USER)
    try:
        rs = ingest_bytes(fid, name, dl["bytes"], origin, SYSTEM_USER, dt_file_id=dtid, budget=budget)
    finally:
        try:
            S.file_delete(e, mark)
        except Exception:
            pass
    for r in rs:
        if r.get("action") == "error":
            fails.append("%s：%s" % (label if origin == "photo_field" else (r.get("name") or label), r.get("msg")))


def _pull_attachments(fid):
    """拉一张审批单的附件与「图片」栏 → 逐个进票（已拉过的按钉钉文件号跳过，人工移除的也不再拉回）。
    拉下来的票记在"系统"名下（不是打开票夹的那个人登记的——审核时不算他自审）。拉完领取次数清零。"""
    e = E()
    f = S.folder_get(e, fid)
    if not f or not f.get("inst_id"):
        if f:
            S.folder_update(e, fid, attach_status="done", attach_msg="", attach_tries=0)
        return
    g = idt.get_instance(f["inst_id"])
    if not g.get("ok"):
        S.folder_update(e, fid, attach_status="failed", attach_msg=(g.get("msg") or "取审批单失败")[:400], attach_tries=0)
        log(SYSTEM_USER, "拉取附件失败", fid, detail={"msg": g.get("msg")})
        return
    n = idt.normalize_instance(g["inst"], f["inst_id"], get_settings().get("templates") or [])
    atts, photos = n.get("attachments") or [], n.get("photos") or []
    fails = []
    budget = {"left": MAX_TOTAL}          # 这张单所有附件里的压缩包解开总量共用一份额度
    for a in atts:
        dtid = str(a.get("fileId") or "")[:64]
        if not dtid:
            continue
        nm = a.get("fileName") or "附件"
        _pull_one(e, fid, dtid, nm, nm, "attachment",
                  lambda a=a: idt.download_attachment(f["inst_id"], a, g["inst"]), fails, budget)
    for i, url in enumerate(photos):
        dtid = "ph:" + hashlib.sha1(str(url).encode("utf-8")).hexdigest()
        _pull_one(e, fid, dtid, "图片%d" % (i + 1), "审批图片%d.jpg" % (i + 1), "photo_field",
                  lambda url=url: idt.download_photo(url), fails, budget)
    items = S.folder_items(e, fid)
    n_inv = len([x for x in items if x.get("kind") == "invoice" and x.get("origin") in ("attachment", "photo_field")])
    msg = "附件 %d 个" % len(atts) + ("，图片 %d 张" % len(photos) if photos else "") + "，发票 %d 张" % n_inv
    if fails:
        msg += "；%d 个没拉下来（%s），可点「刷新」重试或手工拖进来" % (len(fails), "；".join(fails))
    S.folder_update(e, fid, attach_status="failed" if fails else "done", attach_msg=msg[:400], attach_tries=0)
    log(SYSTEM_USER, "拉取附件", fid, detail={"msg": msg, "fails": fails[:20]})
    audit(SYSTEM_USER, "拉取附件", f.get("title") or ("票夹#%d" % fid), msg)


# 同一份多页 PDF/OFD 的几张票只解析、渲染一次（后台单线程逐张识别，缓存按文件 id＋内容哈希，5 分钟或换了别的文件就丢）
_DOC_CACHE = {}
_DOC_CACHE_LOCK = threading.Lock()
_DOC_CACHE_MAX = 2
_DOC_CACHE_TTL = 300


def _doc_cache(f):
    now = time.time()
    key = ((f or {}).get("id"), (f or {}).get("sha256"))
    with _DOC_CACHE_LOCK:
        for k in [k for k, v in _DOC_CACHE.items() if now - v["at"] > _DOC_CACHE_TTL]:
            _DOC_CACHE.pop(k, None)
        ent = _DOC_CACHE.get(key)
        if ent is None:
            while len(_DOC_CACHE) >= _DOC_CACHE_MAX:
                _DOC_CACHE.pop(min(_DOC_CACHE, key=lambda k: _DOC_CACHE[k]["at"]), None)
            ent = _DOC_CACHE[key] = {"at": now, "docs": None, "pages": None}
        ent["at"] = now
        return ent


def _redo_pdf(data, it, f=None):
    """PDF 里一张票的第二拨识别：整份只解析一次、页面只渲染一次（缓存），然后只对这张票那一页做 OCR。"""
    ent = _doc_cache(f) if f else {"docs": None, "pages": None}
    if ent["docs"] is None:
        ent["docs"] = ip.extract_pdf(data)
    docs = ent["docs"]
    page = int(it.get("page") or 0)
    cand = [d for d in docs if int(d.get("page") or 0) == page] or docs
    match = next((d for d in cand if it.get("number") and d.get("number") == it.get("number")), None) or cand[0]
    match = copy.deepcopy(match)       # 缓存里的 Doc 给下一张票还要用，不能被识别改掉
    if not match.get("needOcr"):
        return match
    pages = ent["pages"]
    if pages is None or len(pages) <= page:
        need = max([int(d.get("page") or 0) for d in docs] + [page]) + 1
        pages = ip.render_pdf(data, max_pages=min(need, 30), long_side=1800)
        ent["pages"] = pages
    if len(pages) <= page:
        raise ValueError("PDF 第 %d 页渲染不出来" % (page + 1))
    qd = match if match.get("isInvoice") else (None if page == 0 else ip.new_doc(page))
    return ip.extract_image_ocr(pages[page]["jpeg"], qr_doc=qd)


def _process_item(iid):
    """第二拨：扫描页/照片做离线识别（PDF/OFD/XML 重新直读），结果并进票里：
    二维码/原件/人工来的字段不覆盖；只从识别来的字段进 pending（待人核）；然后重算查重和校验。"""
    e = E()
    it = S.item_get(e, iid)
    if not it or it.get("status") == "removed":
        return
    if int(it.get("proc_tries") or 0) > 3:
        S.item_update(e, iid, proc_status="failed", proc_error="识别多次中断，请手工补字段，或点「重新识别」")
        return
    f = S.file_get(e, it["file_id"]) if it.get("file_id") else None
    if not f:
        S.item_update(e, iid, proc_status="failed", proc_error="没有图片或原件，没法识别，请手工补字段")
        return
    data = read_file_bytes(f, "o")
    if data is None:
        S.item_update(e, iid, proc_status="failed", proc_error="原件文件找不到了，请重新上传")
        return
    mime = f.get("mime") or ""
    try:
        if mime == "application/pdf":
            doc = _redo_pdf(data, it, f)
        elif mime in ("application/ofd", "application/xml"):
            ent = _doc_cache(f)
            if ent["docs"] is None:
                ent["docs"] = ip.extract_ofd(data) if mime == "application/ofd" else ip.extract_xml(data)
            docs = ent["docs"]
            pg = int(it.get("page") or 0)
            doc = copy.deepcopy(docs[pg] if 0 <= pg < len(docs) else (docs[0] if docs else ip.new_doc(0)))
            for meta in (doc.get("fieldSrc") or {}).values():
                if isinstance(meta, dict):
                    meta["page"] = pg
        elif mime.startswith("image/"):
            qd = _doc_of_item(it) if (it.get("qr_type") or (it.get("flags_json") or {}).get("_qrRaw")) else None
            doc = ip.extract_image_ocr(data, qr_doc=qd)
        else:
            S.item_update(e, iid, proc_status="done", proc_error="")
            return
    except ip.OCRUnavailable as ex:
        S.item_update(e, iid, proc_status="failed", proc_error=str(ex)[:400])
        log("系统", "识别失败", it["folder_id"], iid, detail={"msg": str(ex)})
        return
    except Exception as ex:
        tries = int(it.get("proc_tries") or 0)
        if tries < 3:
            S.item_update(e, iid, proc_status="pending", proc_error=("识别出错，稍后自动重试：%s" % ex)[:400])
        else:
            S.item_update(e, iid, proc_status="failed", proc_error=("识别出错：%s；请手工补字段" % ex)[:400])
            log("系统", "识别失败", it["folder_id"], iid, detail={"msg": str(ex)})
        return
    cur = S.item_get(e, iid)
    if not cur or cur.get("status") == "removed" or cur.get("file_id") != it.get("file_id"):
        if cur:
            S.item_update(e, iid, proc_status="done", proc_error="")   # 识别期间换了主文件/被移除：这次结果作废
        return
    split_before = bool((cur.get("flags_json") or {}).get("_splitDone"))
    _merge_recognized(e, cur, doc)
    it2 = refresh_item(iid)
    log("系统", "识别完成", it2["folder_id"], iid, it2.get("later_id"),
        {"kind": it2.get("kind"), "number": it2.get("number") or "", "pending": it2.get("pending_json") or []})
    if doc.get("extraDocs") and not split_before:
        _register_split(cur, doc["extraDocs"])


def _register_split(cur, docs):
    """一张照片里拍了几张老式票（出租车、过路费…）：识别拆出的第 2 张起各登记一行，挂同一个文件；
    来源、登记人、后补单、审核状态跟第一张走，查重照常（同票夹同号合并、别处有标重复）。"""
    e = E()
    folder = S.folder_get(e, cur.get("folder_id"))
    f = S.file_get(e, cur["file_id"]) if cur.get("file_id") else None
    if not folder or not f:
        return
    user = cur.get("created_by") or SYSTEM_USER
    review = cur.get("review") if cur.get("review") == "pending" else None
    made = []
    for d in docs:
        try:
            r = _register_doc(folder, f, d, cur.get("origin") or "upload", user, later_id=cur.get("later_id"),
                              paper=bool(cur.get("paper")), review=review, log_extra={"splitFrom": cur["id"]},
                              name=f.get("name") or "")
        except Exception as ex:
            log(SYSTEM_USER, "拆票登记失败", cur["folder_id"], cur["id"], detail={"msg": str(ex)[:300]})
            continue
        if r.get("itemId") and r.get("action") in ("item", "dup"):
            made.append(r["itemId"])
            refresh_item(r["itemId"])
    log(SYSTEM_USER, "一张照片拆成几张票", cur["folder_id"], cur["id"], cur.get("later_id"),
        {"total": len(docs) + 1, "newItems": made})


def _merge_recognized(e, cur, doc):
    fs = _copy(cur.get("field_src_json") or {})
    pend = list(cur.get("pending_json") or [])
    new_pend = set(doc.get("pending") or [])
    upd = {}
    for fld, col in FIELD_COLS:
        nv = doc.get(fld)
        if fld in MONEY_FIELDS:
            nv = _money_val(nv)          # 识别出的 NaN/超大数当没识别到
        if _empty(nv):
            continue
        cv = cur.get(col)
        src = (fs.get(fld) or {}).get("src")
        if not _empty(cv) and src in TRUSTED_SRC:
            continue
        if not _empty(cv) and src not in (None, "", "ocr"):
            continue
        nsrc = dict((doc.get("fieldSrc") or {}).get(fld) or {"src": "ocr", "page": int(cur.get("page") or 0), "box": None})
        upd[col] = nv
        fs[fld] = nsrc
        if nsrc.get("src") == "ocr" or fld in new_pend:
            if fld not in pend:
                pend.append(fld)
        elif fld in pend:
            pend.remove(fld)
    if not cur.get("lines_json") and doc.get("lines"):
        upd["lines_json"] = doc["lines"]
    was_other = cur.get("kind") == "other"
    if was_other and doc.get("kind") in ("invoice", "receipt"):
        upd["kind"] = doc["kind"]
    if doc.get("invType") and (not cur.get("inv_type") or was_other or cur.get("inv_type") == "other"
                               or (doc["invType"] == "travel" and cur.get("inv_type") == "normal")):
        upd["inv_type"] = doc["invType"]
    if doc.get("typeLabel") and (not cur.get("type_label") or was_other):
        upd["type_label"] = doc["typeLabel"]
    if doc.get("qrType") and not cur.get("qr_type"):
        upd["qr_type"] = doc["qrType"]
    fl = dict(cur.get("flags_json") or {})
    if doc.get("qrRaw") and not fl.get("_qrRaw"):
        fl["_qrRaw"] = doc["qrRaw"]
    ws = list(fl.get("_warnings") or [])
    for w in doc.get("warnings") or []:
        if w not in ws:
            ws.append(w)
    if ws:
        fl["_warnings"] = ws[:20]
    if doc.get("region"):
        fl["_region"] = doc["region"]
    if doc.get("extraDocs"):
        fl["_splitDone"] = True           # 拆出来的其它票只登记一次（点「重新识别」不再拆第二遍）
    upd.update(field_src_json=fs, pending_json=pend, flags_json=fl, proc_status="done", proc_error="")
    S.item_update(e, cur["id"], **upd)


def _worker_loop():
    try:
        r = S.reset_running(E())     # 上次进程中断留下的 running 退回 pending，启动即续跑（拉附件被中断太多次的不再续跑）
        if r.get("items") or r.get("folders") or r.get("gaveUp"):
            print("[发票管家] 续跑中断的任务：识别 %d 张、拉附件 %d 个票夹；多次中断不再自动拉的票夹 %d 个"
                  % (r.get("items", 0), r.get("folders", 0), r.get("gaveUp", 0)))
    except Exception as ex:
        print("[发票管家] 启动续跑失败：%s" % ex)
    while True:
        try:
            n = worker_step()
        except Exception:
            n = 0
            _WORKER_STATE["lastError"] = traceback.format_exc()[-2000:]
        if not n:
            _WAKE.wait(3.0)
            _WAKE.clear()


def start_worker():
    """起后台线程（一个进程只起一次）；环境变量 INV_WORKER_OFF 设了就不起（测试用 worker_step 手动推）。"""
    if os.environ.get("INV_WORKER_OFF"):
        return False
    with _WORKER_LOCK:
        if _WORKER_STATE["started"]:
            return False
        threading.Thread(target=_worker_loop, name="inv-worker", daemon=True).start()
        _WORKER_STATE["started"] = True
    return True


# ═══════════════════════════ 接口：配置 / 设置 ═══════════════════════════

@router.get("/api/inv/config")
async def inv_config(request: Request):
    u, bad = need(request)
    if bad:
        return bad
    st = get_settings()

    def c(g, a=None):
        return can(u, g, a)
    return {"ok": True,
            "can": {"desk": c(ENTER_DESK), "later": c(ENTER_LATER), "audit": c(ENTER_AUDIT), "ledger": c(ENTER_LEDGER),
                    "intake": c(ENTER_DESK, CAP_INTAKE), "receive": c(ENTER_LATER, CAP_RECEIVE),
                    "auditAct": c(ENTER_AUDIT, CAP_AUDIT), "deduct": c(ENTER_LEDGER, CAP_DEDUCT),
                    "unbind": c(ENTER_LEDGER, CAP_UNBIND), "opening": c(ENTER_LEDGER, CAP_OPENING),
                    "config": c(None, CAP_CONFIG)},
            "me": {"name": u["name"], "isSuper": db.is_super(u)},
            "receivers": [{"account": p["account"], "dtName": p.get("dtName") or ""} for p in st["people"] if p.get("receiver")],
            "settings": {"company": st["company"], "templates": [t["name"] for t in st["templates"]],
                         "laterTemplates": [t["name"] for t in st["templates"] if t.get("allowLater")],
                         "remind": st["remind"], "blockNoInvoice": st["blockNoInvoice"]},
            "dingtalk": bool(idt.configured())}


@router.get("/api/inv/settings")
async def inv_settings_get(request: Request):
    u, bad = need(request, None, CAP_CONFIG)
    if bad:
        return bad
    return {"ok": True, "settings": get_settings()}


@router.post("/api/inv/settings")
async def inv_settings_save(request: Request):
    u, bad = need(request, None, CAP_CONFIG)
    if bad:
        return bad
    body = await body_json(request)
    new = body.get("settings") if isinstance(body.get("settings"), dict) else None
    if new is None:
        return err("没收到设置内容", 400)
    try:
        st = save_settings(new, u["name"])
    except ValueError as ex:
        return err(str(ex), 400)
    log(u, "改设置", detail={"keys": sorted(new.keys())})
    audit(u, "改设置", "发票管家设置", "改了：" + "、".join(sorted(new.keys())))
    return {"ok": True, "settings": st}


@router.get("/api/inv/roster")
async def inv_roster(request: Request):
    u, bad = need_any(request, [(None, CAP_CONFIG), (ENTER_LATER, CAP_RECEIVE)])
    if bad:
        return bad
    fresh = _b(request.query_params.get("fresh"))
    r = await run_in_threadpool(idt.roster, fresh)
    if not r.get("ok"):
        return err(r.get("msg") or "拉不到钉钉通讯录", 400)
    return {"ok": True, "rows": r.get("rows") or []}


@router.get("/api/inv/accounts")
async def inv_accounts(request: Request):
    u, bad = need(request, None, CAP_CONFIG)
    if bad:
        return bad
    rows = [{"name": x["name"], "grp": x.get("grp") or "", "post": x.get("post") or ""}
            for x in db.list_users() if x.get("active")]
    return {"ok": True, "rows": rows}


# ═══════════════════════════ 接口：收票工作台 ═══════════════════════════

def _secs_since(ts):
    try:
        return max(0, int((datetime.now() - datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S")).total_seconds()))
    except (TypeError, ValueError):
        return None


def _pair_state(user):
    """电脑端看到的手机配对状态：{id, bound, dtName, device, expiresAt, expiresIn, sessionExpires, lastSeen, active}。
    active＝手机 120 秒内来过（电脑端据此决定要不要快轮询；只配上了没在用就慢轮询）。"""
    p = S.pair_active_for_user(E(), user, now_s())
    if not p:
        return None
    bound = bool(p.get("bound_at"))
    exp = p.get("session_expires") if bound else p.get("bind_deadline")
    ago = _secs_since(p.get("last_seen")) if p.get("last_seen") else None
    return {"id": p["id"], "bound": bound, "dtName": p.get("dt_name") or "", "device": p.get("device") or "",
            "expiresAt": exp or "", "expiresIn": _secs_until(exp), "sessionExpires": p.get("session_expires") or "",
            "lastSeen": p.get("last_seen") or "", "active": bool(bound and ago is not None and ago <= PAIR_ACTIVE_S)}


def _desk_state(user):
    e = E()
    st = get_settings()
    fid = S.desk_get(e, user)
    folder = S.folder_get(e, fid) if fid else None
    items = S.folder_items(e, folder["id"]) if folder else []
    recent = S.folders_recent(e, user, limit=10)
    fv = folder_view(folder, items, settings=st) if folder else None
    if fv is not None:
        fv["gap"] = folder_gap(folder, items, folder_laters(e, folder))     # 底部金额核对（付款−专票−普票−后补）
    return {"ok": True, "folder": fv, "items": item_views(items),
            "recent": folder_views(recent, settings=st), "pair": _pair_state(user)}


@router.get("/api/inv/desk")
async def desk_get(request: Request):
    u, bad = need(request, ENTER_DESK)
    if bad:
        return bad
    return await run_in_threadpool(_desk_state, u["name"])     # 1.5 秒一轮询：查库放线程池，不占住事件循环


def _scan_approval_sync(user, value, mobile):
    r = open_approval_folder(value, user)
    if not r.get("ok"):
        return None, r.get("msg") or "审批单打不开"
    e = E()
    f = r["folder"]
    S.desk_set(e, user, f["id"])
    items = S.folder_items(e, f["id"])
    return {"ok": True, "action": "folder", "msg": r["msg"], "folder": folder_view(f, items, mobile),
            "items": item_views(items, mobile)}, None


def _scan_invoice_sync(user, value, origin, mobile, log_extra):
    e = E()
    fid = S.desk_get(e, user)
    folder = S.folder_get(e, fid) if fid else None
    if not folder:
        return {"ok": True, "action": "noFolder", "folder": None,
                "msg": "还没打开票夹：先扫审批单右上角的二维码（或新建手工票夹），再扫发票"}
    r = ingest_qr(folder["id"], value, user, origin, None, None, log_extra)
    attach_views([r], mobile)
    audit(user, "扫码登记", folder.get("title") or ("票夹#%d" % folder["id"]), r.get("msg"))
    return {"ok": True, "action": r["action"], "msg": r["msg"], "item": r.get("item"),
            "folder": folder_view(S.folder_get(e, folder["id"]), mobile=mobile)}


async def _do_scan(user, code, origin, mobile=False, log_extra=None):
    """扫码枪/手机扫到的一串字 → 分辨是审批单还是发票并处理（电脑端与手机端共用；取单/登记/出视图都在线程池里做）。"""
    code = str(code or "").strip()
    if not code:
        return err("没收到扫码内容", 400)
    c = ip.classify_code(code)
    if c["kind"] in ("approval_link", "business_id"):
        out, msg = await run_in_threadpool(_scan_approval_sync, user, c["value"], mobile)
        return out if out else err(msg, 400)
    if c["kind"] == "invoice_qr":
        return await run_in_threadpool(_scan_invoice_sync, user, c["value"], origin, mobile, log_extra)
    if c["kind"] == "url":
        return {"ok": True, "action": "unknown", "folder": None, "msg": "这个链接不是钉钉审批单的二维码，认不出来"}
    return {"ok": True, "action": "unknown", "folder": None,
            "msg": "没认出这个码：请扫审批单右上角的二维码，或发票左上角的二维码"}


@router.post("/api/inv/desk/scan")
async def desk_scan(request: Request):
    u, bad = need(request, ENTER_DESK, CAP_INTAKE)
    if bad:
        return bad
    body = await body_json(request)
    return await _do_scan(u["name"], body.get("code"), "scanner")


@router.post("/api/inv/desk/open")
async def desk_open(request: Request):
    # 打开/收起票夹只是"看哪个"：有收票工作台页面就行（只能看的人也要能点开票夹看）；往里放票、改票仍要收票权限
    u, bad = need(request, ENTER_DESK)
    if bad:
        return bad
    body = await body_json(request)
    fid = _int_or_none(body.get("folderId"))

    def run():
        e = E()
        f = S.folder_get(e, fid) if fid else None
        if not f:
            return None
        S.desk_set(e, u["name"], f["id"])
        items = S.folder_items(e, f["id"])
        return {"ok": True, "folder": folder_view(f, items), "items": item_views(items)}
    out = await run_in_threadpool(run)
    return out if out else err("票夹不存在", 404)


@router.post("/api/inv/desk/close")
async def desk_close(request: Request):
    u, bad = need(request, ENTER_DESK)
    if bad:
        return bad
    S.desk_set(E(), u["name"], None)
    return {"ok": True}


def _upload_summary(results):
    cnt = {}
    for r in results:
        cnt[r.get("action")] = cnt.get(r.get("action"), 0) + 1
    names = {"item": "登记", "confirm": "纸质件到件/合并", "dup": "重复", "same": "同文件", "other": "非发票留存",
             "folder": "打开票夹", "error": "失败", "noFolder": "没打开票夹"}
    return "，".join("%s %d" % (names.get(k, k), v) for k, v in cnt.items())


async def _ingest_uploads(user, fid, files, origin, log_extra=None):
    results = []
    budget = {"left": MAX_TOTAL}          # 这一次上传里所有压缩包解开的总量共用一份额度
    for name, data in files:
        rs = await run_in_threadpool(ingest_bytes, fid, name, data, origin, user, None, "original", None, None,
                                     None, log_extra, 0, budget)
        for r in rs:
            if r.get("action") == "folder" and r.get("folderId"):
                fid = r["folderId"]    # 一批里夹着审批单打印件：后面的票进它的票夹
        results.extend(rs)
    wake()
    return fid, results


@router.post("/api/inv/desk/upload")
async def desk_upload(request: Request):
    u, bad = need(request, ENTER_DESK, CAP_INTAKE)
    if bad:
        return bad
    files, fields, results, bad = await read_upload_files(request)
    if bad:
        return bad
    if not files and not results:
        return err("没收到文件", 400)
    origin = fields.get("origin") if fields.get("origin") in ("camera", "upload") else "upload"
    e = E()
    fid = _int_or_none(fields.get("folderId"))
    if fid and not S.folder_get(e, fid):
        return err("票夹不存在", 404)
    fid = fid or S.desk_get(e, u["name"])
    fid, rs = await _ingest_uploads(u["name"], fid, files, origin)
    results.extend(rs)

    def views():
        attach_views(results)
        folder = S.folder_get(e, fid) if fid else None
        if files:
            audit(u, "上传票据", (folder or {}).get("title") or ("票夹#%s" % fid if fid else "未开票夹"),
                  "%d 个文件：%s" % (len(files), _upload_summary(results)))
        return {"ok": True, "results": results, "folder": folder_view(folder) if folder else None}
    return await run_in_threadpool(views)


@router.post("/api/inv/folder/manual")
async def folder_manual(request: Request):
    u, bad = need(request, ENTER_DESK, CAP_INTAKE)
    if bad:
        return bad
    body = await body_json(request)
    title = _s(body.get("title"), 200) or "手工票夹"
    try:
        amount = S.money(body.get("amount"))
    except ValueError:
        return err("单据金额格式不对：%s" % _s(body.get("amount"), 40), 400)
    st = get_settings()
    company = _s(body.get("company"), 120) or (st["company"][0]["name"] if len(st["company"]) == 1 else "")
    e = E()
    fid = S.folder_create_manual(e, title, amount, company, u["name"])
    S.desk_set(e, u["name"], fid)
    log(u, "建手工票夹", fid, detail={"title": title, "amount": _money_str(amount) if amount is not None else ""})
    audit(u, "建手工票夹", title, "票夹#%d" % fid)
    return {"ok": True, "folder": folder_view(S.folder_get(e, fid), [])}


@router.post("/api/inv/folder/{fid}/refresh")
async def folder_refresh(fid: int, request: Request):
    u, bad = need(request, ENTER_DESK, CAP_INTAKE)
    if bad:
        return bad
    e = E()
    f = S.folder_get(e, fid)
    if not f:
        return err("票夹不存在", 404)
    if not f.get("inst_id"):
        return err("手工票夹没有钉钉审批单，不用刷新", 400)
    r = await run_in_threadpool(open_approval_folder, None, u["name"], True, f.get("source") or "scan", f["inst_id"])
    if not r.get("ok"):
        return err(r.get("msg") or "刷新失败", 400)
    return {"ok": True, "msg": r["msg"], "folder": folder_view(S.folder_get(e, fid))}


def approved_elsewhere(e, it):
    """同号的票已在别的票夹审核通过（且不是合法拆分）→ 那张票的行，否则 None。
    提交/审核前的硬闸：哪怕重复标记因故没亮（号码后补、识别晚到……），同一张票也不能在两张单上都审核通过。"""
    key = it.get("dup_key")
    if not key or it.get("status") == "removed" or it.get("review") == "void":
        return None
    others = [g for g in S.items_by_dup(e, key)
              if g["id"] != it["id"] and g.get("folder_id") != it.get("folder_id") and g.get("review") == "approved"]
    if not others or _split_ok(others + [it]):
        return None
    return sorted(others, key=_dup_rank)[0]


def _elsewhere_blocker(e, rows, tail):
    hits = [(i, approved_elsewhere(e, i)) for i in rows]
    hits = [(i, o) for i, o in hits if o]
    if not hits:
        return None
    i, o = hits[0]
    where = (o.get("folder") or {}).get("title") or ("票夹#%s" % o.get("folder_id"))
    return {"code": "dup", "itemIds": [x["id"] for x, _ in hits],
            "msg": "有 %d 张票已在别的单上审核通过（如 %s 已挂在「%s」），%s" % (
                len(hits), i.get("number") or ("#%d" % i["id"]), where, tail)}


def submit_check(folder, items, settings=None):
    """提交前检查 → (blockers, warnings)，每条 {code, msg, itemIds?}。
    拦：有重复票（含已在别的单审核通过的同号票）、还有票在识别、附件还在下载、非后补模板的空票夹、
    （设置了拦截时）没票也没登记后补。"""
    st = settings or get_settings()
    act = [i for i in items if i.get("status") != "removed" and i.get("review") != "void"]
    blockers, warnings = [], []
    dups = [i for i in act if (i.get("flags_json") or {}).get("dup")]
    if dups:
        blockers.append({"code": "dup", "itemIds": [i["id"] for i in dups],
                         "msg": "有 %d 张重复票（%s），移除后才能提交" % (len(dups), "、".join((i.get("number") or "#%d" % i["id"]) for i in dups[:5]))})
    else:
        b = _elsewhere_blocker(E(), [i for i in act if i.get("review") != "approved"], "移除后才能提交")
        if b:
            blockers.append(b)
    busy = [i for i in act if i.get("proc_status") in ("pending", "running")]
    if busy:
        blockers.append({"code": "processing", "itemIds": [i["id"] for i in busy],
                         "msg": "还有 %d 张票在识别中，等识别完再提交" % len(busy)})
    if folder.get("attach_status") in ("pending", "running"):
        blockers.append({"code": "attach", "msg": "审批单附件还在下载，稍等再提交"})
    elif folder.get("attach_status") == "failed":
        warnings.append({"code": "attachFailed", "msg": "有附件没拉下来：%s" % (folder.get("attach_msg") or "")})
    bills = [i for i in act if i.get("kind") in ("invoice", "receipt")]
    cfg = template_cfg(folder.get("template"), st)
    if cfg and cfg.get("allowLater") and not is_reimb(folder.get("template")):
        if not bills and not later_brief(folder):
            m = "付款单没附发票，也没登记发票后补：请先到「发票后补池」登记"
            (blockers if st.get("blockNoInvoice") else warnings).append({"code": "noInvoice", "msg": m})
    elif not act and not later_brief(folder):
        # 报销单登记了发票后补（票还没到）可以先空着提交，审核时看后补单
        blockers.append({"code": "empty", "msg": "票夹是空的，先放票再提交"})
    failed = [i for i in act if i.get("proc_status") == "failed"]
    if failed:
        warnings.append({"code": "procFailed", "itemIds": [i["id"] for i in failed],
                         "msg": "%d 张票没识别出来，审核时要对着图补字段" % len(failed)})
    if bills and folder.get("amount") is not None:
        g = folder_gap(folder, act, folder_laters(E(), folder))
        if gap_off(g):
            warnings.append({"code": "gap", "gap": g,
                             "msg": "%s：付款金额 −（专票＋普票＋发票后补单）不等于 0，审核时会重点关注" % gap_msg(g)})
    return blockers, warnings


def _submit_sync(u, fid):
    """提交票夹 → (响应 dict, None) 或 (None, JSONResponse)。
    没有一张要送审的票时不许挂成"已提交"（否则审核队列里永远卡着一张没法通过也没法退回的单）：
    票都已审核通过 → 票夹直接记已审核；只登记了后补 → 告诉他票到了会直接进审核；否则让他先放票。"""
    e = E()
    f = S.folder_get(e, fid)
    if not f:
        return None, err("票夹不存在", 404)
    if f.get("status") not in OPEN_STATUSES:
        return None, err("这个票夹%s，不用再提交" % STATUS_CN.get(f.get("status"), f.get("status") or ""), 400)
    items = S.folder_items(e, fid)
    blockers, warnings = submit_check(f, items)
    if blockers:
        return None, err("还不能提交：" + blockers[0]["msg"], 400, blockers=blockers, warnings=warnings)
    ts = now_s()
    todo = [it for it in items if it.get("review") in ("draft", "returned")]
    waiting = [it for it in items if it.get("review") == "pending"]
    if not todo and not waiting:
        bills = [i for i in items if i.get("kind") in ("invoice", "receipt") and i.get("review") != "void"]
        if bills and all(i.get("review") == "approved" for i in bills):
            S.folder_update(e, fid, status="approved", submitted_by=u["name"], submitted_at=ts)
            log(u, "提交（票已全部审核）", fid, detail={"items": len(bills)})
            audit(u, "提交票夹", f.get("title") or ("票夹#%d" % fid), "票已全部审核通过，票夹直接记为已审核")
            f = S.folder_get(e, fid)
            return {"ok": True, "msg": "这张单的票都已审核通过，票夹直接记为已审核", "folder": folder_view(f, items),
                    "items": item_views(items), "warnings": warnings}, None
        if later_brief(f):
            return None, err("这张单还没有要审核的票：已登记发票后补，票到了会直接进审核，不用提交", 400,
                             blockers=[{"code": "nothing", "msg": "没有要审核的票"}], warnings=warnings)
        return None, err("票夹里没有要审核的票，先放票再提交", 400,
                         blockers=[{"code": "nothing", "msg": "没有要审核的票"}], warnings=warnings)
    for it in todo:
        fl = dict(it.get("flags_json") or {})
        stale = [k for k in ("_audOk", "_doubt") if k in fl]
        for k in stale:
            fl.pop(k)          # 退回后改好再提交：上一轮的「已核/疑问」作废，审核人重新看
        S.item_update(e, it["id"], review="pending", **({"flags_json": fl} if stale else {}))
    n = len(todo)
    S.folder_update(e, fid, status="submitted", submitted_by=u["name"], submitted_at=ts)
    log(u, "提交审核", fid, detail={"items": n, "warnings": [w["msg"] for w in warnings]})
    audit(u, "提交票夹", f.get("title") or ("票夹#%d" % fid), "%d 张票送审%s" % (n, ("；提示：" + "；".join(w["msg"] for w in warnings)) if warnings else ""))
    f = S.folder_get(e, fid)
    items = S.folder_items(e, fid)
    return {"ok": True, "msg": "已提交，等会计审核", "folder": folder_view(f, items), "items": item_views(items),
            "warnings": warnings}, None


@router.post("/api/inv/folder/{fid}/submit")
async def folder_submit(fid: int, request: Request):
    u, bad = need(request, ENTER_DESK, CAP_INTAKE)
    if bad:
        return bad
    out, bad = await run_in_threadpool(_submit_sync, u, fid)
    return bad if bad else out


def folder_settle(fid, user=SYSTEM_USER):
    """已提交的票夹里待审的票没了（被移除/作废）→ 自动调整票夹状态，免得卡在审核队列里：
    还有已审核的发票/收据 → 已审核；否则退回"收票中"让收票人接着收。→ 新状态或 None（没动）。
    （作废在 invoice_books.item_void 里：作废后也请调它。）"""
    e = E()
    f = S.folder_get(e, fid)
    if not f or f.get("status") != "submitted":
        return None
    items = S.folder_items(e, fid)
    if any(i.get("review") == "pending" for i in items):
        return None
    approved = [i for i in items if i.get("review") == "approved" and i.get("kind") in ("invoice", "receipt")]
    ns = "approved" if approved else "collecting"
    S.folder_update(e, fid, status=ns)
    log(user, "票夹状态自动调整", fid, detail={"from": "submitted", "to": ns, "reason": "待审的票都没了"})
    return ns


@router.get("/api/inv/folder/{fid}")
async def folder_detail(fid: int, request: Request):
    u, bad = need(request)
    if bad:
        return bad

    def run():
        e = E()
        f = S.folder_get(e, fid)
        if not f:
            return None
        items = S.folder_items(e, fid)
        fv = folder_view(f, items, with_form=True)
        # 审核页从链接直接打开票夹时也要看到异常清单（不依赖它是否在当前队列页里）
        fv["anomalies"], fv["clean"] = folder_anomalies(f, items, "pending")
        laters = folder_laters(e, f)
        fv["laters"] = [later_row_view(l) for l in laters]
        fv["gap"] = folder_gap(f, items, laters)
        # 留痕：票夹级的＋这夹里每张票（含已移除的）自己的，按 itemId/laterId 可筛单张票的历史
        ids = [i["id"] for i in S.folder_items(e, fid, include_removed=True)]
        logs = S.logs_of(e, folder_id=fid, item_ids=ids, limit=300)
        return {"ok": True, "folder": fv, "items": item_views(items), "logs": [log_view(r) for r in logs]}
    out = await run_in_threadpool(run)
    return out if out else err("票夹不存在", 404)


# ───────────────────────── 单张票 ─────────────────────────

def item_guard(u, it, folder):
    """这个人能不能改/移除这张票 → None 放行，或 JSONResponse。
    草稿/退回的票：收票人（后补登记的票，后补收票人也行）；待审的票：审核人（未提交票夹里的后补票，后补收票人也行）；
    已通过/已作废的票：谁都不能在这里改。"""
    if not it:
        return err("票不存在", 404)
    if it.get("status") == "removed":
        return err("这张票已经移除了", 400)
    rv = it.get("review") or "draft"
    if rv in ("approved", "void"):
        return err("这张票已审核通过（或已作废），不能再改；确需更正请在发票台账作废后重新登记", 400)
    if rv in ("draft", "returned"):
        if can(u, ENTER_DESK, CAP_INTAKE) or (it.get("later_id") and can(u, ENTER_LATER, CAP_RECEIVE)):
            return None
        return err("无「收票」权限：请管理员开通收票工作台的收票权限", 403)
    if can(u, ENTER_AUDIT, CAP_AUDIT):
        return None
    if (folder or {}).get("status") not in ("submitted", "approved") and it.get("later_id") and can(u, ENTER_LATER, CAP_RECEIVE):
        return None
    return err("这张票已提交审核，只有审核人能改（需要「发票审核」权限）", 403)


def _clean_lines(v):
    if not isinstance(v, list):
        raise ValueError("明细格式不对")
    out = []
    for l in v[:300]:
        if not isinstance(l, dict):
            continue
        row = {k: (_s(l.get(k), 200) or None) for k in ("name", "category", "spec", "unit", "rate")}
        for k in ("qty", "price"):
            try:
                row[k] = None if _empty(l.get(k)) else float(str(l.get(k)).replace(",", ""))
                if row[k] is not None and (not math.isfinite(row[k]) or abs(row[k]) >= 1e16):
                    raise ValueError
            except ValueError:
                raise ValueError("明细第 %d 行的%s不是正常数字" % (len(out) + 1, "数量" if k == "qty" else "单价"))
        for k in ("amount", "tax"):
            dv = S.money(l.get(k))
            row[k] = None if dv is None else float(dv)
        out.append(row)
    return out


@router.post("/api/inv/item/{iid}/update")
async def item_update(iid: int, request: Request):
    u, bad = need(request)
    if bad:
        return bad
    body = await body_json(request)
    out, bad = await run_in_threadpool(_item_update_sync, u, iid, body)
    return bad if bad else out


def _item_update_sync(u, iid, body):
    """改票面字段/核对待核字段 → (响应, None) 或 (None, 错误响应)。
    · 号码来自发票二维码/电子原件（MACHINE_SRC）的票不许改成收据/其它（改票种躲查重）；别的发票改成收据/其它 → 标 kindChanged；
    · 人手把二维码/原件/税局文件读到的值改成别的 → 标 manualOverride（号码/金额类按"错"级给审核看），原值留痕；
    · 点"核对"（值没改）＝人核过：来源记为 manual，以后识别/税局文件不再覆盖它。"""
    e = E()
    it = S.item_get(e, iid)
    folder = S.folder_get(e, (it or {}).get("folder_id")) if it else None
    g = item_guard(u, it, folder)
    if g:
        return None, g
    fields = body.get("fields") or {}
    confirm = body.get("confirm")
    if not isinstance(fields, dict):
        return None, err("字段格式不对", 400)
    extra_cols = {"invType": "inv_type", "typeLabel": "type_label", "kind": "kind", "lines": "lines_json"}
    fs = _copy(it.get("field_src_json") or {})
    pend = list(it.get("pending_json") or [])
    upd, changes, confirmed = {}, {}, []
    overrides = {}
    for k, v in fields.items():
        col = F2C.get(k) or extra_cols.get(k)
        if not col:
            continue   # 前端多带的展示字段不认、不报错
        try:
            if k in MONEY_FIELDS:
                dv = S.money(v)
                nv = None if dv is None else float(dv)
            elif k == "date":
                nv = _norm_date(v)
            elif k == "kind":
                nv = _s(v, 12)
                if nv not in KINDS:
                    raise ValueError(v)
            elif k == "invType":
                nv = _s(v, 16)
                if nv and nv not in INV_TYPES:
                    raise ValueError(v)
            elif k == "lines":
                nv = _clean_lines(v)
            else:
                width = S.ITEM.c[col].type.length or 200
                nv = _s(v, width) or None
        except ValueError as ex:
            if k in MONEY_FIELDS:
                return None, err("「%s」金额格式不对：%s" % (FIELD_LABEL[k], _s(v, 40)), 400)
            if k == "date":
                return None, err("开票日期格式不对（要 2026-09-01 这样）：%s" % _s(v, 40), 400)
            return None, err("「%s」填得不对：%s" % (FIELD_LABEL.get(k, k), ex), 400)
        cur = it.get(col)
        if k == "kind" and cur == "invoice" and nv != "invoice":
            nsrc = (fs.get("number") or {}).get("src") if isinstance(fs.get("number"), dict) else None
            if nsrc in MACHINE_SRC and it.get("number"):
                return None, err("这张票的号码是从发票二维码/电子原件读出来的，它就是发票，不能改成收据或其它；"
                                 "确实不是这张单的票请移除", 400)
        if k in F2C and _same(cur, nv):
            if k in pend:           # 值没改但点了保存＝核过了
                pend.remove(k)
                confirmed.append(k)
            continue
        if k == "lines" and json.dumps(cur or [], sort_keys=True) == json.dumps(nv, sort_keys=True):
            continue
        if k not in F2C and k != "lines" and _same(cur, nv):
            continue
        upd[col] = nv
        changes[k] = [cur, nv]
        if k in F2C:
            old = fs.get(k) if isinstance(fs.get(k), dict) else {}
            if old.get("src") in MACHINE_SRC and not _empty(cur):
                overrides[k] = {"from": "" if cur is None else str(cur), "to": "" if nv is None else str(nv),
                                "src": old.get("src"), "by": u["name"], "at": now_s()}
            fs[k] = {"src": "manual", "page": old.get("page", int(it.get("page") or 0)), "box": old.get("box")}
            if k in pend:
                pend.remove(k)
    if confirm == "all":
        confirmed += pend
        pend = []
    elif isinstance(confirm, list):
        cc = [c for c in confirm if c in pend]
        confirmed += cc
        pend = [p for p in pend if p not in cc]
    if not upd and not confirmed:
        return {"ok": True, "item": item_view(it)}, None
    for k in confirmed:
        # 人核过的值＝人工确认：以后识别结果、税局文件包都不再覆盖它
        if k in F2C:
            meta = dict(fs.get(k)) if isinstance(fs.get(k), dict) else {"page": int(it.get("page") or 0), "box": None}
            meta.update(src="manual", confirmed=True)
            fs[k] = meta
    fl = dict(it.get("flags_json") or {})
    if overrides:
        mo = dict(fl.get("manualOverride") or {})
        for k, x in overrides.items():
            first = dict(mo.get(k) or x)         # 记第一次的机读原值：再改几次也看得到最初读到的是什么
            first.update(to=x["to"], by=x["by"], at=x["at"])
            mo[k] = first
        fl["manualOverride"] = mo
    kind_off = "kind" in changes and changes["kind"][0] == "invoice"     # 发票被人手改成收据/其它：亮"错"给审核
    if kind_off:
        fl["kindChanged"] = {"from": changes["kind"][0] or "", "to": changes["kind"][1] or "", "by": u["name"],
                             "at": now_s()}
    if overrides or kind_off:
        upd["flags_json"] = fl
    upd["field_src_json"] = fs
    upd["pending_json"] = pend
    S.item_update(e, iid, **upd)
    it2 = refresh_item(iid, user=u["name"])
    det = {"changes": {k: [str(a) if a is not None else "", str(b) if b is not None else ""] for k, (a, b) in changes.items()
                       if k != "lines"}, "confirmed": confirmed}
    if "lines" in changes:
        det["lines"] = "改了明细"
    if overrides:
        det["overrides"] = {k: {"from": x["from"], "src": x["src"]} for k, x in overrides.items()}
    act = "改票面字段" if changes else "核对字段"
    log(u, act, it["folder_id"], iid, it.get("later_id"), det)
    audit(u, act, "票#%d %s" % (iid, (it2 or it).get("number") or ""), det)
    out = {"ok": True, "item": item_view(it2)}
    if it2 and it2.get("_merged_from") == iid:
        out["mergedInto"] = it2["id"]
        out["msg"] = "同票夹里已经有这张票（号码 %s），两张已并成一张" % (it2.get("number") or "")
    return out, None


def _item_remove_sync(u, iid, note):
    e = E()
    it = S.item_get(e, iid)
    folder = S.folder_get(e, (it or {}).get("folder_id")) if it else None
    g = item_guard(u, it, folder)
    if g:
        return None, g
    S.item_update(e, iid, status="removed")
    # 这张票独占的文件一起标移除：同一份文件以后再放进来还能重新登记（查同文件只认有效文件）
    with e.connect() as cx:
        shared = cx.execute(select(S.ITEM.c.id).where(
            S.ITEM.c.file_id == it.get("file_id"), S.ITEM.c.id != iid,
            or_(S.ITEM.c.status.is_(None), S.ITEM.c.status != "removed"))).first() if it.get("file_id") else None
        linked = [S._row(r) for r in cx.execute(select(S.FILE).where(S.FILE.c.item_id == iid))]
    fids = {x["id"] for x in linked if x["id"] != it.get("file_id")}
    if it.get("file_id") and not shared:
        fids.add(it["file_id"])
    for x in fids:
        S.file_update(e, x, status="removed")
    if it.get("dup_key"):
        recheck_dup_group(it["dup_key"])
    _later_sync([it.get("later_id")])             # 挂着后补单的票被移除：已到金额、收齐状态要跟着变
    settled = folder_settle(it["folder_id"], u["name"])
    log(u, "移除票据", it["folder_id"], iid, it.get("later_id"), {"number": it.get("number") or "", "note": note})
    audit(u, "移除票据", "票#%d %s" % (iid, it.get("number") or ""), note or "")
    out = {"ok": True, "item": item_view(S.item_get(e, iid)), "folder": folder_view(S.folder_get(e, it["folder_id"]))}
    if settled:
        out["msg"] = "已移除；票夹里没有待审的票了，票夹已改为「%s」" % STATUS_CN.get(settled, settled)
    return out, None


@router.post("/api/inv/item/{iid}/remove")
async def item_remove(iid: int, request: Request):
    u, bad = need(request)
    if bad:
        return bad
    body = await body_json(request)
    out, bad = await run_in_threadpool(_item_remove_sync, u, iid, _s(body.get("note"), 200))
    return bad if bad else out


def _item_split_sync(u, iid, body):
    e = E()
    it = S.item_get(e, iid)
    folder = S.folder_get(e, (it or {}).get("folder_id")) if it else None
    g = item_guard(u, it, folder)
    if g:
        return None, g
    if it.get("kind") != "invoice":
        return None, err("只有发票能拆分", 400)
    split = _b(body.get("split"))
    alloc = None
    if split:
        try:
            alloc = S.money(body.get("alloc"))
        except ValueError:
            return None, err("分摊金额格式不对：%s" % _s(body.get("alloc"), 40), 400)
        if alloc is None or alloc <= 0:
            return None, err("拆分要填这张单分摊的金额", 400)
        if it.get("total") is not None and float(alloc) > float(it["total"]) + 0.005:
            return None, err("分摊金额不能超过票面价税合计 ¥%s" % _money_str(it["total"]), 400)
    S.item_update(e, iid, split=1 if split else 0, alloc=alloc)
    refresh_item(iid, user=u["name"])
    if it.get("dup_key"):
        recheck_dup_group(it["dup_key"], skip=iid)
    it2 = S.item_get(e, iid)
    det = {"split": split, "alloc": _money_str(alloc) if alloc is not None else ""}
    log(u, "拆分票" if split else "取消拆分", it["folder_id"], iid, it.get("later_id"), det)
    audit(u, "拆分票" if split else "取消拆分", "票#%d %s" % (iid, it.get("number") or ""), det)
    return {"ok": True, "item": item_view(it2)}, None


@router.post("/api/inv/item/{iid}/split")
async def item_split(iid: int, request: Request):
    u, bad = need(request)
    if bad:
        return bad
    body = await body_json(request)
    out, bad = await run_in_threadpool(_item_split_sync, u, iid, body)
    return bad if bad else out


@router.post("/api/inv/item/{iid}/rotate")
async def item_rotate(iid: int, request: Request):
    u, bad = need(request)
    if bad:
        return bad
    e = E()
    it = S.item_get(e, iid)
    if not it:
        return err("票不存在", 404)
    if not (can(u, ENTER_DESK, CAP_INTAKE) or can(u, ENTER_AUDIT, CAP_AUDIT) or can(u, ENTER_LATER, CAP_RECEIVE)):
        return err("无权限：需要收票、审核或后补收票权限", 403)
    if not it.get("file_id"):
        return err("这张票没有图片", 400)
    body = await body_json(request)
    try:
        rot = int(float(body.get("rotation") or 0)) % 360
    except (TypeError, ValueError):
        return err("旋转角度只能是 0/90/180/270", 400)
    if rot % 90:
        return err("旋转角度只能是 0/90/180/270", 400)
    S.file_update(e, it["file_id"], rotation=rot)
    log(u, "旋转图片", it["folder_id"], iid, it.get("later_id"), {"rotation": rot})
    audit(u, "旋转图片", "票#%d" % iid, "%d°" % rot)
    return {"ok": True, "item": item_view(S.item_get(e, iid))}


@router.post("/api/inv/item/{iid}/reprocess")
async def item_reprocess(iid: int, request: Request):
    u, bad = need(request)
    if bad:
        return bad
    e = E()
    it = S.item_get(e, iid)
    folder = S.folder_get(e, (it or {}).get("folder_id")) if it else None
    g = item_guard(u, it, folder)
    if g:
        return g
    if not it.get("file_id"):
        return err("只有二维码、没有图片的票不用重新识别", 400)
    S.item_update(e, iid, proc_status="pending", proc_tries=0, proc_error="")
    wake()
    log(u, "重新识别", it["folder_id"], iid, it.get("later_id"))
    audit(u, "重新识别", "票#%d %s" % (iid, it.get("number") or ""))
    return {"ok": True, "item": item_view(S.item_get(e, iid))}


@router.get("/api/inv/file/{file_id}")
async def file_get(file_id: int, request: Request):
    u, bad = need(request)
    if bad:
        return bad
    f = S.file_get(E(), file_id)
    if not f:
        return err("文件不存在", 404)
    v = request.query_params.get("v") or "o"
    page = _int(request.query_params.get("page"), 0, 999, 0)
    return file_response(f, v, page, inline=_b(request.query_params.get("inline")))


# ═══════════════════════════ 接口：手机配对 ═══════════════════════════
_PAIR_URLS = {}              # pair_id → (url, 过期 epoch, user)：令牌明文只在内存留到绑定期限，库里只存 sha256
_PAIR_LOCK = threading.Lock()


def _hash_tok(tok):
    return hashlib.sha256(tok.encode("ascii")).hexdigest()


_LAST_SITE = [""]


def _site(request, st):
    """配对码/消息链接用的站点地址：设置里填了门户网址就用它；没填 → 电脑上正在用的地址（手机和电脑走同一个入口）。
    直接用 IP 访问时一律给 http：服务器证书只签了域名，https://IP 在手机上会报证书错误打不开。
    手机页拍照走系统相机（文件选择），http 也能用；只有电脑上的高拍仪预览要 https。"""
    portal = (st.get("portalUrl") or "").rstrip("/")
    host = (request.url.hostname or "").lower()
    if portal and host not in ("localhost", "127.0.0.1", "::1"):
        return portal
    scheme = request.url.scheme
    if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", host):
        scheme = "http"
        netloc = host if request.url.port in (None, 80, 443) else "%s:%s" % (host, request.url.port)
    else:
        netloc = request.url.netloc
    site = "%s://%s" % (scheme, netloc)
    if host not in ("localhost", "127.0.0.1", "::1", "testserver"):
        _LAST_SITE[0] = site
    return site


def _qr_png(text, scale=8, quiet=4):
    try:
        import cv2
    except Exception:
        return None
    enc = cv2.QRCodeEncoder.create() if hasattr(cv2.QRCodeEncoder, "create") else cv2.QRCodeEncoder()
    q = enc.encode(text)
    q = cv2.resize(q, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    b = quiet * scale
    q = cv2.copyMakeBorder(q, b, b, b, b, cv2.BORDER_CONSTANT, value=255)
    ok, buf = cv2.imencode(".png", q)
    return buf.tobytes() if ok else None


@router.post("/api/inv/pair")
async def pair_create(request: Request):
    u, bad = need(request, ENTER_DESK, CAP_INTAKE)
    if bad:
        return bad
    tok = secrets.token_urlsafe(32)
    deadline = _after(minutes=PAIR_BIND_MIN)
    pid = S.pair_create(E(), u["name"], _hash_tok(tok), deadline)     # 同时吊销此人旧配对
    url = _site(request, get_settings()) + "/#/invpair?t=" + tok
    now = time.time()
    with _PAIR_LOCK:
        for k in [k for k, v in _PAIR_URLS.items() if v[1] < now or v[2] == u["name"]]:
            _PAIR_URLS.pop(k, None)
        _PAIR_URLS[pid] = (url, now + PAIR_BIND_MIN * 60, u["name"])
    hint = ""        # 手机页拍照走系统相机，http 也能用，不再提示
    log(u, "发起手机配对", detail={"pairId": pid})
    audit(u, "发起手机配对", "配对#%d" % pid, "10 分钟内有效")
    return {"ok": True, "pair": {"id": pid, "expiresAt": deadline, "expiresIn": PAIR_BIND_MIN * 60}, "url": url,
            "qr": "/api/inv/pair/%d/qr.png" % pid, "httpsHint": hint}


@router.get("/api/inv/pair/{pid}/qr.png")
async def pair_qr(pid: int, request: Request):
    u, bad = need(request, ENTER_DESK, CAP_INTAKE)
    if bad:
        return bad
    p = S.pair_get(E(), pid)
    if not p or p.get("user") != u["name"]:
        return err("配对码不存在", 404)
    with _PAIR_LOCK:
        ent = _PAIR_URLS.get(pid)
    if not ent or ent[1] < time.time() or p.get("revoked") or p.get("bound_at"):
        return err("配对码已失效（过期、已被扫过或已断开），请重新生成", 404)
    png = await run_in_threadpool(_qr_png, ent[0])
    if not png:
        return err("服务器缺少二维码组件（opencv），生成不了配对码", 500)
    return Response(content=png, media_type="image/png")


@router.post("/api/inv/pair/revoke")
async def pair_revoke(request: Request):
    u, bad = need(request, ENTER_DESK, CAP_INTAKE)
    if bad:
        return bad
    n = S.pair_revoke_user(E(), u["name"])
    with _PAIR_LOCK:
        for k in [k for k, v in _PAIR_URLS.items() if v[2] == u["name"]]:
            _PAIR_URLS.pop(k, None)
    log(u, "断开手机配对", detail={"revoked": n})
    audit(u, "断开手机配对", u["name"], "吊销 %d 个" % n)
    return {"ok": True, "revoked": n}


def _req_token(request):
    tok = request.headers.get("X-Inv-Pair", "") or ""
    if not tok or len(tok) > 200 or not tok.isascii():
        return ""
    return tok


def pair_from_request(request):
    """请求头 X-Inv-Pair → 有效的配对行或 None（空/非 ASCII/查无/已吊销/过期都算无效）。
    未绑定：令牌＝配对码，看 10 分钟绑定期限；已绑定：令牌＝绑定时新发的会话令牌，看 12 小时会话期限。
    配对码一绑定就作废（库里换成会话令牌的哈希），拿配对码再来一律无效。"""
    tok = _req_token(request)
    if not tok:
        return None
    p = S.pair_by_hash(E(), _hash_tok(tok))
    if not p or p.get("revoked"):
        return None
    now = now_s()
    if p.get("bound_at"):
        return p if (p.get("session_expires") or "") >= now else None
    return p if (p.get("bind_deadline") or "") >= now else None


def _used_qr_pair(request):
    """拿已经用过的配对码来绑定 → 那条配对（m/bind 据此回 409"用过了"，而不是笼统的"失效"）。"""
    tok = _req_token(request)
    return S.pair_by_qr_hash(E(), _hash_tok(tok)) if tok else None


def pair_token_ok(request):
    """登录门例外用（app._auth_gate）：/api/inv/m/* 带有效配对令牌 → True。出任何错都当无效。
    拿用过的配对码来 /api/inv/m/bind 也放进去（路由回 409 说清楚"配对码已经用过了"）。
    顺手把配对的电脑账号记到 request.state.inv_pair_user（运维埋点按"<账号>·手机"归因）。"""
    try:
        p = pair_from_request(request)
        if p is None:
            path = getattr(getattr(request, "url", None), "path", "") or ""
            if path == "/api/inv/m/bind":
                p = _used_qr_pair(request)
        if p is None:
            return False
        try:
            request.state.inv_pair_user = p.get("user") or ""
        except Exception:
            pass
        return True
    except Exception:
        return False


def pair_ops_user(request):
    """运维埋点用的手机端身份：'<电脑账号>·手机'；认不出 → ''。"""
    try:
        u = getattr(request.state, "inv_pair_user", "") or ""
    except Exception:
        u = ""
    return (u + "·手机") if u else ""


def revoke_pairs_for(name):
    """吊销某账号的全部手机配对（管理员重置密码/禁用/删除账号时调，app.py）→ 吊销条数。出错不抛。"""
    try:
        n = S.pair_revoke_user(E(), name)
    except Exception as ex:
        print("[发票管家] 吊销手机配对失败（%s）：%s" % (name, ex))
        return 0
    with _PAIR_LOCK:
        for k in [k for k, v in _PAIR_URLS.items() if v[2] == name]:
            _PAIR_URLS.pop(k, None)
    if n:
        log(SYSTEM_USER, "吊销手机配对", detail={"user": name, "revoked": n, "reason": "账号被重置密码/禁用/删除"})
    return n


def _phone(request, need_bound=True):
    """手机端每次调用都重核：令牌有效、电脑账号还在且启用、没被重置密码待改、仍有收票工作台＋收票权限 → (pair, user, None)。"""
    try:
        p = pair_from_request(request)
    except Exception:
        p = None
    if not p:
        return None, None, err("手机配对已失效（过期或已断开）：请在电脑上重新点「手机当相机」生成配对码", 401)
    u = db.get_user(p["user"])
    if not u or not u.get("active"):
        return None, None, err("电脑上的账号「%s」不可用了，手机配对已失效" % p["user"], 403)
    if u.get("must_change_pwd"):
        return None, None, err("账号「%s」的密码已被重置，手机配对已失效：请先在电脑上登录改密码，再重新配对" % p["user"], 403)
    if not (db.user_can(u, ENTER_DESK) and db.user_can(u, CAP_INTAKE)):
        return None, None, err("账号「%s」已没有收票工作台的收票权限，手机配对已失效" % p["user"], 403)
    if need_bound and not p.get("bound_at"):
        return None, None, err("手机还没完成配对：请重新扫电脑上的配对码", 403)
    ls = p.get("last_seen") or ""
    now = now_s()
    ago = _secs_since(ls) if ls else None
    if ago is None or ago >= 30:     # 30 秒内不重复写库（手机端 2 秒一轮询；电脑端按 120 秒内来过判"正在用"）
        S.pair_update(E(), p["id"], last_seen=now)
    return p, u, None


def _phone_extra(p):
    return {"via": "手机", "dtName": p.get("dt_name") or "", "pairId": p["id"]}


@router.get("/api/inv/m/hello")
async def m_hello(request: Request):
    p, u, bad = _phone(request, need_bound=False)
    if bad:
        return bad
    bound = bool(p.get("bound_at"))
    return {"ok": True, "corpId": get_settings().get("corpId") or "", "bound": bound, "user": p["user"],
            "dtName": p.get("dt_name") or "", "expiresAt": (p.get("session_expires") if bound else p.get("bind_deadline")) or ""}


_QR_USED_MSG = "这个配对码已经用过了，请在电脑上重新点「手机当相机」"
_PAIR_BIND_LOCK = threading.Lock()      # 同一个配对码两台手机同时来绑：只能一台成功


@router.post("/api/inv/m/bind")
async def m_bind(request: Request):
    """手机扫配对码后绑定 → {ok, user, dtName, msg, session, expiresAt, identified}。
    配对码只能用一次：绑定成功当场作废（库里换成新发的会话令牌 session 的哈希），手机之后每次调用都带 session；
    再拿配对码来绑 → 409。身份：钉钉已配置且设置里给这个电脑账号绑了钉钉 → 必须用手机钉钉扫（带免登码）且是同一个人；
    账号没绑钉钉 → 放行但提示"没认出身份"。"""
    if not pair_from_request(request) and _used_qr_pair(request):
        return err(_QR_USED_MSG, 409)
    p, u, bad = _phone(request, need_bound=False)
    if bad:
        return bad
    if p.get("bound_at"):
        return err(_QR_USED_MSG, 409)
    body = await body_json(request)
    code, device = _s(body.get("code"), 200), _s(body.get("device"), 200)
    who = {"ok": False, "userid": "", "name": "", "msg": "没拿到钉钉免登码"}
    if code:
        who = await run_in_threadpool(idt.userinfo_by_code, code)
    bound_uid = dt_uid_of(p["user"])
    # V2.628 用户定「信任电脑登录身份」：扫链接二维码打开的页面不是注册微应用，钉钉免登多半调不起来（拿不到 code）。
    # 认不出钉钉身份也放行——配对是从已登录的电脑发起的，登记人＝电脑上登录的人，页面标注"未通过钉钉核对"。
    # 只有钉钉明确认出是"另一个人"才拦（防在别人电脑上用自己手机配对）。
    if who.get("ok") and bound_uid and who.get("userid") != bound_uid:
        log(p["user"], "手机配对被拒", detail={"dtName": who.get("name") or "", "reason": "钉钉身份与电脑账号不一致"})
        audit(p["user"], "手机配对被拒", "配对#%d" % p["id"], "手机钉钉：%s" % (who.get("name") or ""))
        return err("手机上的人和电脑上登录的人不是同一个，请用本人手机扫码", 403)
    exp = _after(hours=PAIR_SESSION_H)
    sess = secrets.token_urlsafe(32)
    qr_hash = _hash_tok(_req_token(request))
    with _PAIR_BIND_LOCK:
        cur = S.pair_get(E(), p["id"])
        if not cur or cur.get("bound_at") or cur.get("revoked") or cur.get("token_hash") != qr_hash:
            return err(_QR_USED_MSG, 409)
        S.pair_update(E(), p["id"], token_hash=_hash_tok(sess), qr_hash=qr_hash, bound_at=now_s(), session_expires=exp,
                      dt_userid=who.get("userid") or "", dt_name=who.get("name") or "", device=device, last_seen=now_s())
    with _PAIR_LOCK:
        _PAIR_URLS.pop(p["id"], None)     # 配对码只能扫一次
    if who.get("ok"):
        msg = "已配对：%s（钉钉：%s）" % (p["user"], who.get("name") or who.get("userid"))
    else:
        msg = "没通过钉钉核对身份，按电脑上登录的「%s」登记（扫码收票照常用）" % p["user"]
    det = {"dtName": who.get("name") or "", "dtIdentified": bool(who.get("ok")), "device": device}
    log(p["user"], "手机配对", detail=det)
    audit(p["user"], "手机配对", "配对#%d" % p["id"], det)
    return {"ok": True, "user": p["user"], "dtName": who.get("name") or "", "msg": msg, "session": sess,
            "expiresAt": exp, "identified": bool(who.get("ok"))}


@router.get("/api/inv/m/jsconfig")
async def m_jsconfig(request: Request):
    """手机页调钉钉「扫一扫」前的 dd.config 参数（JSAPI 鉴权）。query：url＝手机页当前地址（不含 #）。
    只给本站地址签名（请求的 Host 或设置里的站点地址），免得拿我们的应用给别人的网页签权限。
    还没绑定（刚扫配对码）也给：钉钉里要先 dd.config 鉴权、再 requestAuthCode 才认得出是谁（V2.621 修手机认不出人）。"""
    p, u, bad = _phone(request, need_bound=False)
    if bad:
        return bad
    url = _s(request.query_params.get("url"), 500)
    try:
        parts = urlsplit(url)
    except ValueError:
        parts = None
    ok_hosts = {(request.url.hostname or "").lower()}
    portal = (get_settings().get("portalUrl") or "").strip()
    if portal:
        ok_hosts.add((urlsplit(portal).hostname or "").lower())
    if not parts or parts.scheme not in ("http", "https") or (parts.hostname or "").lower() not in ok_hosts:
        return err("只能给本站页面做钉钉鉴权", 400)
    r = await run_in_threadpool(idt.jsapi_config, url, get_settings().get("corpId") or "")
    if not r.get("ok"):
        log(p["user"], "钉钉扫码鉴权失败", detail={"msg": r.get("msg") or "", "via": "手机"})
    return r


def _m_state_sync(p):
    e = E()
    fid = S.desk_get(e, p["user"])
    folder = S.folder_get(e, fid) if fid else None
    items = S.folder_items(e, folder["id"]) if folder else []
    recent = sorted(items, key=lambda x: x["id"], reverse=True)[:12]
    return {"ok": True, "user": p["user"], "dtName": p.get("dt_name") or "",
            "folder": folder_view(folder, items, mobile=True) if folder else None, "items": item_views(recent, mobile=True)}


@router.get("/api/inv/m/state")
async def m_state(request: Request):
    p, u, bad = _phone(request)
    if bad:
        return bad
    return await run_in_threadpool(_m_state_sync, p)     # 手机 2 秒一轮询：查库放线程池


@router.post("/api/inv/m/scan")
async def m_scan(request: Request):
    p, u, bad = _phone(request)
    if bad:
        return bad
    body = await body_json(request)
    return await _do_scan(p["user"], body.get("code"), "phone", mobile=True, log_extra=_phone_extra(p))


@router.post("/api/inv/m/upload")
async def m_upload(request: Request):
    p, u, bad = _phone(request)
    if bad:
        return bad
    files, fields, results, bad = await read_upload_files(request)
    if bad:
        return bad
    if not files and not results:
        return err("没收到照片", 400)
    e = E()
    user = p["user"]
    fid = S.desk_get(e, user)
    extra = _phone_extra(p)
    if (fields.get("purpose") or "invoice") == "scan":
        # 「扫审批单」拍的照片：只找审批码，不把它当票留存
        for name, data in files:
            d = await run_in_threadpool(ip.extract_image_fast, data)
            if d.get("approvalLink"):
                r = await run_in_threadpool(_switch_to_approval, d["approvalLink"], user, name)
                if r.get("folderId"):
                    fid = r["folderId"]
                results.append(r)
            elif d.get("isInvoice"):
                results.append(_res(name, "unknown", "这是发票不是审批单：请点「拍发票」再拍一次"))
            else:
                results.append(_res(name, "unknown", "照片里没读到审批单二维码：请对准审批单右上角的二维码，拍近一点"))
    else:
        fid, rs = await _ingest_uploads(user, fid, files, "phone", extra)
        results.extend(rs)

    def views():
        attach_views(results, mobile=True)
        folder = S.folder_get(e, fid) if fid else None
        if files:
            audit(user, "手机上传", (folder or {}).get("title") or "未开票夹",
                  "%d 张（钉钉：%s）：%s" % (len(files), p.get("dt_name") or "未识别", _upload_summary(results)))
        return {"ok": True, "results": results, "folder": folder_view(folder, mobile=True) if folder else None}
    return await run_in_threadpool(views)


@router.get("/api/inv/m/file/{file_id}")
async def m_file(file_id: int, request: Request):
    p, u, bad = _phone(request)
    if bad:
        return bad
    e = E()
    f = S.file_get(e, file_id)
    fo = S.folder_get(e, f.get("folder_id")) if f else None
    if not f or not fo or p["user"] not in (fo.get("created_by"), fo.get("opened_by"), f.get("created_by")):
        return err("文件不存在", 404)    # 不是自己票夹里的文件：不认也不说"无权"，免得被拿来探测
    v = request.query_params.get("v") or "t"
    if v not in ("t", "p"):
        return err("手机上只能看缩略图", 400)
    return file_response(f, v, _int(request.query_params.get("page"), 0, 999, 0))


# ═══════════════════════════ 接口：发票审核 ═══════════════════════════

def folder_anomalies(folder, items, tab="pending", later=_UNSET):
    """审核队列异常标注 → ([{code,label,level(err|warn|info)}], clean)。clean＝一条都没有，才允许批量通过。
    tab=pending 只看待审的票；金额差只在票夹已提交/已审时比（后补陆续到票的不比总额）。
    已审票后来收到对不上的文件（postMismatch）不分页签都亮：票已过审、只能在这里被看到。"""
    act = [i for i in items if i.get("status") != "removed" and i.get("review") != "void"]
    scope = [i for i in act if i.get("review") == "pending"] if tab == "pending" else act
    inv = [i for i in scope if i.get("kind") == "invoice"]
    out = []

    def add(code, label, level, rows=None):
        a = {"code": code, "label": label, "level": level}
        if rows:
            a["itemIds"] = [r["id"] for r in rows]
        out.append(a)

    def fl(i):
        return i.get("flags_json") if isinstance(i.get("flags_json"), dict) else {}
    rows = [i for i in scope if fl(i).get("dup")]
    if rows:
        add("dup", "重复票 %d 张" % len(rows), "err", rows)
    rows = [i for i in scope if i.get("proc_status") in ("pending", "running")]
    if rows:
        add("processing", "还有 %d 张在识别中" % len(rows), "warn", rows)
    rows = [i for i in scope if i.get("proc_status") == "failed"]
    if rows:
        add("procFailed", "%d 张没识别出来，要对着图补字段" % len(rows), "warn", rows)
    rows = [i for i in scope if i.get("pending_json")]
    if rows:
        add("unchecked", "%d 张有待核字段" % len(rows), "warn", rows)
    rows = [i for i in inv if fl(i).get("buyerMismatch") or fl(i).get("buyerNotCompany")]
    if rows:
        add("buyer", "%d 张抬头不对（不是这张单的公司/不是本公司）" % len(rows), "err", rows)
    rows = [i for i in inv if fl(i).get("sellerMismatch")]
    if rows:
        add("seller", "%d 张销方和付款单收款方不一致" % len(rows), "warn", rows)
    rows = [i for i in inv if fl(i).get("qrMismatch")]
    if rows:
        add("qr", "%d 张票面和二维码对不上" % len(rows), "warn", rows)
    rows = [i for i in inv if fl(i).get("sumMismatch")]
    if rows:
        add("sum", "%d 张金额＋税额≠价税合计" % len(rows), "warn", rows)
    rows = [i for i in inv if i.get("verify") == "red"]
    if rows:
        add("verifyRed", "%d 张税局显示已作废/红冲" % len(rows), "err", rows)
    rows = [i for i in scope if fl(i).get("kindChanged")]
    if rows:
        add("kindChanged", "%d 张被人手改了票据类别（如发票改成收据），请核对" % len(rows), "err", rows)
    rows = [i for i in scope if fl(i).get("manualOverride")]
    if rows:
        key_hit = any(set(fl(i).get("manualOverride") or {}) & set(KEY_FIELDS) for i in rows)
        add("override", "%d 张有人手改了二维码/电子原件上读到的字段（原值见留痕），请核对" % len(rows),
            "err" if key_hit else "warn", rows)
    rows = [i for i in scope if fl(i).get("fileMismatch")]
    if rows:
        add("fileMismatch", "%d 张后来收到的同号文件/二维码和票面对不上，请核对" % len(rows), "err", rows)
    rows = [i for i in act if fl(i).get("postApprovalMismatch")]
    if rows:
        add("postMismatch", "%d 张已审核的票后来收到对不上的文件，请核对是否要作废重登" % len(rows), "err", rows)
    rows = [i for i in inv if i.get("origin") in ("attachment", "photo_field") and not i.get("paper")]
    if rows:
        add("paper", "%d 张纸质件还没到" % len(rows), "warn", rows)
    rows = [i for i in scope if i.get("kind") == "receipt"]
    if rows:
        add("receipt", "含 %d 张收据" % len(rows), "info", rows)
    rows = [i for i in scope if i.get("kind") not in ("invoice", "receipt")]
    if rows:
        add("other", "含 %d 份非发票附件" % len(rows), "info", rows)
    rows = [i for i in inv if i.get("split")]
    if rows:
        add("split", "%d 张是拆分票" % len(rows), "info", rows)
    if folder.get("status") in ("submitted", "approved"):
        if not [i for i in act if i.get("kind") in ("invoice", "receipt")]:
            lb = later_brief(folder, None, later)
            add("noInvoice", "这张单没有发票" + ("，已登记后补" if lb else "，也没登记后补"), "info" if lb else "warn")
        elif folder.get("amount") is not None:
            g = folder_gap(folder, act, folder_laters(E(), folder))
            if gap_off(g):
                add("amountDiff", gap_msg(g), "warn")
    return out, not out


def _self_review(u, folder, pend):
    """审核人是不是在审自己的东西：票夹是他提交的，或者待审的票里有他亲手登记的（包括他在别人提交的票夹里
    后补扫进去的票）。后台拉的审批附件记在"系统"名下，不算打开票夹那个人登记的。"""
    if folder.get("status") == "submitted" and folder.get("submitted_by") == u["name"]:
        return True
    return any(i.get("created_by") == u["name"] for i in pend)


def _self_review_msg(u, folder, what="审核"):
    if folder.get("status") == "submitted" and folder.get("submitted_by") == u["name"]:
        return "提交人不能审核自己提交的票夹，请换一位会计%s" % what
    return "这个票夹里有你自己登记的票，不能自己审，请换一位会计%s" % what


def approve_folder(u, folder, decisions=None, note="", batch=False, item_ids=None, gap_note=""):
    """审核通过票夹里待审的票 → (ok, http状态, msg, extra)。逐张通过与批量通过共用。
    item_ids：审核人页面上看到的待审票 id（逐张通过时必给）——只通过这些；审核期间票夹里又进了新的待审票 →
    409 {newItems}，让他刷新后再审（没看过的票不能被一起通过）。批量通过不给（只过"干净"的票夹）。
    重复票/识别中的票、同号票已在别的单审核通过的，拦下（extra.blockers）；提交人或登记人不能审自己（主管理员放行并标自审）；
    可否抵扣：decisions {"<itemId>": {"deductible": bool}}，没给的按系统建议（yes→可抵、no→不可、空→不判）。
    票夹已提交却一张待审的票都没有（后补单没票就关了、票被作废……）→ 允许直接把票夹记为已审核，不再卡在队列里。"""
    e = E()
    items = S.folder_items(e, folder["id"])
    pend = [i for i in items if i.get("review") == "pending"]
    if item_ids is not None:
        seen = {int(x) for x in item_ids if _int_or_none(x)}
        new = [i["id"] for i in pend if i["id"] not in seen]
        if new:
            return False, 409, "审核期间这个票夹又进了新票，请刷新后再审", {"newItems": new}
        pend = [i for i in pend if i["id"] in seen]
    if not pend:
        if folder.get("status") == "submitted":
            return _approve_empty_folder(u, folder, note, batch)
        return False, 400, "这个票夹没有待审核的票", {}
    self_rev = _self_review(u, folder, pend)
    if self_rev and not db.is_super(u):
        return False, 403, _self_review_msg(u, folder), {}
    doubts = [i for i in pend if (i.get("flags_json") or {}).get("_doubt")]
    if doubts:
        m = "有 %d 张记了疑问：请提交退回，或先清掉疑问" % len(doubts)
        return False, 400, m, {"blockers": [{"code": "doubt", "itemIds": [i["id"] for i in doubts], "msg": m}]}
    g = folder_gap(folder, items, folder_laters(e, folder))
    if gap_off(g):
        # 付款金额 −（专票＋普票＋后补）≠ 0：逐张审要写差额说明（批量通过本来就只过没异常的票夹）
        if not gap_note:
            return False, 400, "%s：要写差额说明才能通过" % gap_msg(g), {"code": "gapNote", "gap": g}
        note = ("差额说明（%s）：%s" % (_money_str(g["gap"]), gap_note)) + (("；" + note) if note else "")
    blockers = []
    dups = [i for i in pend if (i.get("flags_json") or {}).get("dup")]
    if dups:
        blockers.append({"code": "dup", "itemIds": [i["id"] for i in dups],
                         "msg": "有 %d 张重复票，先退回让提交人移除" % len(dups)})
    else:
        b = _elsewhere_blocker(e, pend, "先退回让提交人移除")
        if b:
            blockers.append(b)
    busy = [i for i in pend if i.get("proc_status") in ("pending", "running")]
    if busy:
        blockers.append({"code": "processing", "itemIds": [i["id"] for i in busy], "msg": "还有 %d 张在识别中" % len(busy)})
    if blockers:
        return False, 400, "还不能通过：" + blockers[0]["msg"], {"blockers": blockers}
    decisions = decisions if isinstance(decisions, dict) else {}
    ts = now_s()
    note = _s(note, 500)
    for it in pend:
        dec = decisions.get(str(it["id"])) or {}
        ded = dec.get("deductible") if isinstance(dec, dict) else None
        if it.get("kind") != "invoice":
            dv = None
        elif ded is None:
            dv = {"yes": 1, "no": 0}.get(it.get("deduct_suggest") or "")
        else:
            dv = 1 if _b(ded) else 0
        S.item_update(e, it["id"], review="approved", review_by=u["name"], review_at=ts, review_note=note,
                      self_review=1 if self_rev else 0, deductible=dv)
    fu = {"reviewed_by": u["name"], "reviewed_at": ts, "review_note": note, "self_review": 1 if self_rev else 0}
    if folder.get("status") == "submitted":
        fu["status"] = "approved"
    S.folder_update(e, folder["id"], **fu)
    act = "审核·自审" if self_rev else ("批量审核通过" if batch else "审核通过")
    det = {"items": len(pend), "note": note, "selfReview": self_rev}
    log(u, act, folder["id"], detail=det)
    audit(u, act, folder.get("title") or ("票夹#%d" % folder["id"]), det)
    return True, 200, "已通过 %d 张" % len(pend), {"selfReview": self_rev}


def _approve_empty_folder(u, folder, note, batch):
    """票夹已提交、却一张待审的票都没有（后补单没票就关了、票被作废……）→ 直接把票夹记为已审核，不再卡在审核队列里。"""
    self_rev = folder.get("submitted_by") == u["name"]
    if self_rev and not db.is_super(u):
        return False, 403, "提交人不能审核自己提交的票夹，请换一位会计审核", {}
    ts = now_s()
    note = _s(note, 500)
    S.folder_update(E(), folder["id"], status="approved", reviewed_by=u["name"], reviewed_at=ts, review_note=note,
                    self_review=1 if self_rev else 0)
    det = {"items": 0, "note": note, "selfReview": self_rev, "batch": bool(batch)}
    log(u, "审核通过（没有待审的票）", folder["id"], detail=det)
    audit(u, "审核通过（没有待审的票）", folder.get("title") or ("票夹#%d" % folder["id"]), det)
    return True, 200, "这张单没有待审核的票，票夹已记为已审核", {"selfReview": self_rev}


def _audit_queue_sync(u, tab, q, page, size):
    e = E()
    total, rows = S.audit_queue(e, tab=tab, q=q, page=page, size=size)
    by = S.items_of_folders(e, [r["id"] for r in rows])
    lm = S.later_open_for_folders(e, rows)
    st = get_settings()
    out = []
    for r in rows:
        items = by.get(r["id"], [])
        v = folder_view(r, items, settings=st, later=lm.get(r["id"]))
        anomalies, clean = folder_anomalies(r, items, tab, later=lm.get(r["id"]))
        c = r.get("counts") or {}
        v.update(pendingItems=c.get("pending_items", 0), anomalies=anomalies, clean=clean,
                 counts={"pendingItems": c.get("pending_items", 0), "invoices": c.get("invoices", 0),
                         "dup": c.get("dup", 0), "unchecked": c.get("unchecked", 0), "others": c.get("others", 0)},
                 mine=_self_review(u, r, [i for i in items if i.get("review") == "pending"]))
        out.append(v)
    return {"ok": True, "total": total, "rows": out}


@router.get("/api/inv/audit/queue")
async def audit_queue(request: Request):
    u, bad = need(request, ENTER_AUDIT)
    if bad:
        return bad
    qp = request.query_params
    tab = qp.get("tab") if qp.get("tab") in ("pending", "returned", "done") else "pending"
    return await run_in_threadpool(_audit_queue_sync, u, tab, qp.get("q"), qp.get("page") or 1, qp.get("size") or 30)


@router.post("/api/inv/audit/approve")
async def audit_approve(request: Request):
    """逐张审核通过。body：{folderId, itemIds:[页面上看到的待审票 id], decisions?, note?}。
    itemIds 必给（老页面没带 → 400 让刷新）；审核期间又进了新的待审票 → 409 {newItems}。"""
    u, bad = need(request, ENTER_AUDIT, CAP_AUDIT)
    if bad:
        return bad
    body = await body_json(request)
    ids = body.get("itemIds")
    if not isinstance(ids, list):
        return err("请刷新页面后再审", 400)

    def run():
        e = E()
        f = S.folder_get(e, _int_or_none(body.get("folderId")))
        if not f:
            return err("票夹不存在", 404)
        ok, status, msg, extra = approve_folder(u, f, body.get("decisions"), _s(body.get("note"), 300), item_ids=ids,
                                                gap_note=_s(body.get("gapNote"), 300))
        if not ok:
            return err(msg, status, **extra)
        f = S.folder_get(E(), f["id"])
        return {"ok": True, "msg": msg, "folder": folder_view(f), "selfReview": extra.get("selfReview", False)}
    return await run_in_threadpool(run)


def _audit_batch_sync(u, ids, note):
    done, skipped = [], []
    e = E()
    for raw in ids[:200]:
        fid = _int_or_none(raw)
        f = S.folder_get(e, fid) if fid else None
        if not f:
            skipped.append({"id": raw, "reason": "票夹不存在"})
            continue
        anomalies, clean = folder_anomalies(f, S.folder_items(e, fid), "pending")
        if not clean:
            skipped.append({"id": fid, "reason": "有异常（%s），要逐张审核" % "；".join(a["label"] for a in anomalies[:3])})
            continue
        ok, status, msg, extra = approve_folder(u, f, None, note, batch=True)
        if ok:
            done.append(fid)
        else:
            skipped.append({"id": fid, "reason": msg})
    return {"ok": True, "done": done, "skipped": skipped}


@router.post("/api/inv/audit/batch")
async def audit_batch(request: Request):
    u, bad = need(request, ENTER_AUDIT, CAP_AUDIT)
    if bad:
        return bad
    body = await body_json(request)
    ids = body.get("folderIds") if isinstance(body.get("folderIds"), list) else []
    # 最多 200 个票夹、每个几次写库：整段放线程池，不让别的工具的请求干等
    return await run_in_threadpool(_audit_batch_sync, u, ids, _s(body.get("note"), 500))


def _audit_mark_sync(u, iid, mark, text):
    """审核弹窗里单张票：ok＝这张核对无误（待核字段一并确认、记已核）；doubt＝记疑问（整单提交时退回）；clear＝清掉标记。"""
    e = E()
    it = S.item_get(e, iid)
    folder = S.folder_get(e, (it or {}).get("folder_id")) if it else None
    g = item_guard(u, it, folder)
    if g:
        return None, g
    if it.get("review") != "pending":
        return None, err("这张票不在待审核状态", 400)
    if mark == "ok" and it.get("pending_json"):
        _, bad = _item_update_sync(u, iid, {"confirm": "all"})
        if bad:
            return None, bad
        it = S.item_get(e, iid)
    fl = dict(it.get("flags_json") or {})
    fl.pop("_audOk", None)
    fl.pop("_doubt", None)
    if mark == "ok":
        fl["_audOk"] = {"by": u["name"], "at": now_s()}
    elif mark == "doubt":
        fl["_doubt"] = {"text": text, "by": u["name"], "at": now_s()}
    S.item_update(e, iid, flags_json=fl)
    act = {"ok": "审核·本张核对无误", "doubt": "审核·记疑问", "clear": "审核·清掉标记"}[mark]
    log(u, act, it["folder_id"], iid, it.get("later_id"), {"text": text} if text else {})
    return {"ok": True, "item": item_view(S.item_get(e, iid))}, None


@router.post("/api/inv/audit/item/{iid}/mark")
async def audit_item_mark(iid: int, request: Request):
    """body：{mark: ok|doubt|clear, text?}。疑问必须写内容。"""
    u, bad = need(request, ENTER_AUDIT, CAP_AUDIT)
    if bad:
        return bad
    body = await body_json(request)
    mark = body.get("mark")
    text = _s(body.get("text"), 200)
    if mark not in ("ok", "doubt", "clear"):
        return err("mark 只能是 ok / doubt / clear", 400)
    if mark == "doubt" and not text:
        return err("写一下疑问是什么，退回时提交人才知道改哪", 400)
    out, bad = await run_in_threadpool(_audit_mark_sync, u, iid, mark, text)
    return bad if bad else out


def _audit_return_sync(u, fid, note):
    """退回 → (响应, None) 或 (None, 错误响应)。已提交但没有待审票的票夹也能退回（票夹退回收票人接着收）。
    钉钉通知在这里（线程池里）发，不占住事件循环。"""
    e = E()
    f = S.folder_get(e, fid)
    if not f:
        return None, err("票夹不存在", 404)
    items = S.folder_items(e, f["id"])
    pend = [i for i in items if i.get("review") == "pending"]
    if not pend and f.get("status") != "submitted":
        return None, err("这个票夹没有待审核的票，不用退回", 400)
    self_rev = _self_review(u, f, pend)
    if self_rev and not db.is_super(u):
        return None, err(_self_review_msg(u, f, "处理"), 403)
    ts = now_s()
    for it in pend:
        S.item_update(e, it["id"], review="returned", review_by=u["name"], review_at=ts, review_note=note)
    S.folder_update(e, f["id"], status="returned", reviewed_by=u["name"], reviewed_at=ts, review_note=note,
                    self_review=1 if self_rev else 0)
    target = f.get("submitted_by") or next((i.get("created_by") for i in pend
                                            if i.get("created_by") and i.get("created_by") != SYSTEM_USER), "")
    title = f.get("title") or f.get("business_id") or ("票夹#%d" % f["id"])
    text = "【核算工作台·发票管家】你提交的票夹「%s」被退回：%s。请到收票工作台处理 → %s" % (
        title, note, portal_link("/#/invdesk?folder=%d" % f["id"]))
    sent = notify_dt([dt_uid_of(target)] if target else [], text)
    act = "退回·自审" if self_rev else "审核退回"
    det = {"items": len(pend), "note": note, "notify": target, "sent": bool(sent.get("sent")), "sendMsg": sent.get("msg")}
    log(u, act, f["id"], detail=det)
    audit(u, act, title, det)
    return {"ok": True, "folder": folder_view(S.folder_get(e, f["id"])), "notify": sent}, None


@router.post("/api/inv/audit/return")
async def audit_return(request: Request):
    u, bad = need(request, ENTER_AUDIT, CAP_AUDIT)
    if bad:
        return bad
    body = await body_json(request)
    note = _s(body.get("note"), 500)
    fid = _int_or_none(body.get("folderId"))
    if not fid:
        return err("票夹不存在", 404)
    if not note:
        return err("退回要写原因，提交人才知道改什么", 400)
    out, bad = await run_in_threadpool(_audit_return_sync, u, fid, note)
    return bad if bad else out


# 模块导入即起后台线程（单进程 uvicorn；测试设 INV_WORKER_OFF）
start_worker()
