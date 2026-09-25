# [Change Log] Date: 2026-09-25 | Author: Claude / c | Version: V2.621（发票管家·申请人自助登记发票后补）
# Description: 业务同事（付款单/报销单的申请人）自己登记发票后补，不用工作台账号：
#   /api/inv/s/*（不走工作台登录，app._auth_gate 放行，本文件每个接口自己认人）。
#   认人两条路：① 在钉钉里打开 → 钉钉免登（dd.config 鉴权后 requestAuthCode）；
#             ② 电脑浏览器 → 输入钉钉上的姓名 → 系统通过钉钉给本人发 6 位验证码 → 输入即登录（能收到＝本人）。
#   登录后：列出「我发起的、允许后补的审批单」→ 选一张 → 填预计到票等 → 进发票后补池（与财务代填同一张单、同一个池子）；
#   「我的后补单」看到票进度。只能登记自己发起的单子（后端按审批单申请人的钉钉 userid 核）。
#   验证码/会话只存 sha256（inv_self 表）；发码限流：同一人 60 秒一次、一天 10 次；同一 IP 10 分钟 8 次。
import hashlib
import os
import secrets
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from routers import invoice as inv
from routers import invoice_books as books

router = APIRouter()

S = inv.S
E = inv.E
err = inv.err
idt = inv.idt

SESSION_H = 12            # 登录后 12 小时有效
CODE_MIN = 5              # 验证码 5 分钟有效
CODE_TRIES = 5            # 一个验证码最多试 5 次
RESEND_S = 60             # 同一人 60 秒内只发一次
DAY_MAX = 10              # 同一人一天最多发 10 次
IP_WINDOW_S = 600
IP_MAX = 8                # 同一 IP 10 分钟最多发 8 次（防拿名字乱刷别人的钉钉）
HEADER = "X-Inv-Self"
SELF_PATH = "/#/invself"

_IP_HITS = {}
_IP_LOCK = threading.Lock()


def _h(s):
    return hashlib.sha256(str(s).encode("utf-8")).hexdigest()


def _at(minutes=0, hours=0, seconds=0):
    return (datetime.now() + timedelta(minutes=minutes, hours=hours, seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")


def _ip(request):
    return (request.headers.get("x-real-ip") or (request.client.host if request.client else "") or "")[:64]


def _ip_ok(ip):
    now = time.time()
    with _IP_LOCK:
        hits = [t for t in _IP_HITS.get(ip, []) if now - t < IP_WINDOW_S]
        if len(hits) >= IP_MAX:
            _IP_HITS[ip] = hits
            return False
        hits.append(now)
        _IP_HITS[ip] = hits
        if len(_IP_HITS) > 5000:
            for k in [k for k, v in _IP_HITS.items() if not v or now - v[-1] > IP_WINDOW_S]:
                _IP_HITS.pop(k, None)
    return True


def self_from_request(request):
    """请求头里的自助登录令牌 → 会话行（有效才给）或 None。出任何错都当没登录。"""
    try:
        tok = request.headers.get(HEADER) or ""
        if not tok or len(tok) > 100:
            return None
        r = S.self_by_hash(E(), "session", _h(tok))
        if not r or r.get("used") or (r.get("expires_at") or "") < inv.now_s():
            return None
        return r
    except Exception:
        return None


def self_ops_user(request):
    """运维埋点：'<钉钉姓名>·自助'；认不出 → ''。"""
    r = self_from_request(request)
    return ((r.get("dt_name") or "") + "·自助") if r else ""


def _need(request):
    r = self_from_request(request)
    if not r:
        return None, err("登录已过期或没登录：请重新用钉钉验证", 401)
    ls = r.get("last_seen") or ""
    if not ls or ls < _at(seconds=-60):
        S.self_update(E(), r["id"], last_seen=inv.now_s())
    return r, None


def _actor(me):
    """登记/上传时记在谁名下：钉钉姓名（没有工作台账号）。"""
    return {"name": (me.get("dt_name") or "申请人")[:40]}


def _new_session(uid, name, dept, via, ip):
    tok = secrets.token_urlsafe(32)
    now = inv.now_s()
    S.self_insert(E(), kind="session", token_hash=_h(tok), dt_userid=uid, dt_name=(name or "")[:50],
                  dept=(dept or "")[:200], via=via, ip=ip, created_at=now, expires_at=_at(hours=SESSION_H), last_seen=now)
    return tok


def _dept_of(uid):
    r = idt.roster()
    p = next((x for x in (r.get("rows") or []) if x.get("userid") == uid), None) if r.get("ok") else None
    return (p or {}).get("dept") or ""


def _later_templates():
    return [t["name"] for t in (inv.get_settings().get("templates") or []) if t.get("allowLater")]


# ───────────────────────── 认人 ─────────────────────────

@router.get("/api/inv/s/hello")
async def s_hello(request: Request):
    st = inv.get_settings()
    me = self_from_request(request)
    return {"ok": True, "corpId": st.get("corpId") or "", "dingtalk": idt.configured(),
            "me": {"name": me.get("dt_name") or "", "dept": me.get("dept") or "", "via": me.get("via") or ""} if me else None,
            "templates": _later_templates()}


@router.get("/api/inv/s/jsconfig")
async def s_jsconfig(request: Request):
    """钉钉里打开时的 dd.config 参数（免登前先鉴权）。只给本站页面签名。"""
    url = inv._s(request.query_params.get("url"), 500)
    try:
        parts = urlsplit(url)
    except ValueError:
        parts = None
    ok_hosts = {(request.url.hostname or "").lower()}
    portal = (inv.get_settings().get("portalUrl") or "").strip()
    if portal:
        ok_hosts.add((urlsplit(portal).hostname or "").lower())
    if not parts or parts.scheme not in ("http", "https") or (parts.hostname or "").lower() not in ok_hosts:
        return err("只能给本站页面做钉钉鉴权", 400)
    return await run_in_threadpool(idt.jsapi_config, url, inv.get_settings().get("corpId") or "")


@router.post("/api/inv/s/login/dd")
async def s_login_dd(request: Request):
    """钉钉里打开：免登码 → 认出是谁 → 发会话令牌。"""
    body = await inv.body_json(request)
    code = inv._s(body.get("code"), 200)
    if not code:
        return err("没拿到钉钉免登码：请用钉钉打开，或改用验证码登录", 400)

    def run():
        who = idt.userinfo_by_code(code)
        if not who.get("ok"):
            return None, err("钉钉没认出你是谁（%s）：可以改用验证码登录" % (who.get("msg") or "原因不明"), 403)
        uid, name = who["userid"], who.get("name") or ""
        dept = _dept_of(uid)
        tok = _new_session(uid, name, dept, "dingtalk", _ip(request))
        inv.log({"name": name}, "申请人登录（钉钉免登）", detail={"dept": dept})
        return {"ok": True, "token": tok, "me": {"name": name, "dept": dept, "via": "dingtalk"}}, None
    out, bad = await run_in_threadpool(run)
    return bad if bad else out


@router.post("/api/inv/s/login/send")
async def s_login_send(request: Request):
    """电脑浏览器：输入钉钉上的姓名 → 通过钉钉给本人发 6 位验证码。重名 → 先回候选（部门、岗位）让他选 pick。"""
    body = await inv.body_json(request)
    name = inv._s(body.get("name"), 40).replace(" ", "")
    if not name:
        return err("写一下你在钉钉上的姓名", 400)
    pick = body.get("pick")
    ip = _ip(request)

    def run():
        r = idt.roster()
        if not r.get("ok"):
            return None, err("拉不到钉钉通讯录：%s" % (r.get("msg") or "原因不明"), 503)
        hits = [p for p in (r.get("rows") or []) if (p.get("name") or "").replace(" ", "") == name and p.get("userid")]
        if not hits:
            return None, err("钉钉通讯录里没找到「%s」：请写钉钉上的全名" % name, 404)
        if len(hits) > 1:
            try:
                idx = int(pick)
            except (TypeError, ValueError):
                idx = -1
            if not (0 <= idx < len(hits)):
                return {"ok": True, "need": "pick",
                        "choices": [{"i": i, "dept": p.get("dept") or "", "title": p.get("title") or ""} for i, p in enumerate(hits)]}, None
            p = hits[idx]
        else:
            p = hits[0]
        if not _ip_ok(ip):
            return None, err("发得太频繁了：请 10 分钟后再试", 429)
        e = E()
        uid = p["userid"]
        n1, last = S.self_recent_codes(e, uid, _at(seconds=-RESEND_S))
        if n1:
            return None, err("刚给你发过验证码：请看钉钉消息，%d 秒后才能再发" % RESEND_S, 429)
        nd, _ = S.self_recent_codes(e, uid, _at(hours=-24))
        if nd >= DAY_MAX:
            return None, err("今天发验证码次数太多了：请明天再试，或在钉钉里打开这个网址（不用验证码）", 429)
        try:
            S.self_purge(e, inv.now_s())
        except Exception:
            pass
        code = "%06d" % secrets.randbelow(1000000)
        ticket = secrets.token_urlsafe(24)
        rid = S.self_insert(e, kind="code", token_hash=_h(ticket), code_hash=_h(ticket + ":" + code), dt_userid=uid,
                            dt_name=(p.get("name") or "")[:50], dept=(p.get("dept") or "")[:200], tries=0, used=0,
                            ip=ip, created_at=inv.now_s(), expires_at=_at(minutes=CODE_MIN))
        sent = inv.notify_dt([uid], "【核算工作台·发票管家】你的登录验证码：%s（%d 分钟内有效）。用于登记发票后补；不是你本人操作请忽略。"
                             % (code, CODE_MIN))
        dry = sent.get("msg") == "dry-run" and os.environ.get("INV_DRY_SEND")
        if not sent.get("sent") and not dry:
            S.self_update(e, rid, used=1)
            return None, err("验证码没发出去（%s）：请稍后再试，或在钉钉里打开这个网址" % (sent.get("msg") or "原因不明"), 502)
        who = "%s%s" % (p.get("name") or "", "（%s）" % p["dept"].split("-")[-1] if p.get("dept") else "")
        out = {"ok": True, "ticket": ticket, "to": who, "expiresIn": CODE_MIN * 60,
               "msg": "验证码已发到「%s」的钉钉，请在钉钉消息里查看" % who}
        if dry:
            out["devCode"] = code         # 只在测试/本机联调（INV_DRY_SEND）时回给页面，正式环境不会有
        return out, None
    out, bad = await run_in_threadpool(run)
    return bad if bad else out


@router.post("/api/inv/s/login/verify")
async def s_login_verify(request: Request):
    body = await inv.body_json(request)
    ticket = inv._s(body.get("ticket"), 100)
    code = "".join(ch for ch in inv._s(body.get("code"), 20) if ch.isdigit())
    if not ticket or len(code) != 6:
        return err("请输入钉钉消息里的 6 位验证码", 400)

    def run():
        e = E()
        r = S.self_by_hash(e, "code", _h(ticket))
        if not r or r.get("used") or (r.get("expires_at") or "") < inv.now_s():
            return None, err("验证码已过期或已用过：请重新获取", 400)
        if int(r.get("tries") or 0) >= CODE_TRIES:
            S.self_update(e, r["id"], used=1)
            return None, err("验证码输错次数太多：请重新获取", 400)
        if _h(ticket + ":" + code) != r.get("code_hash"):
            S.self_update(e, r["id"], tries=int(r.get("tries") or 0) + 1)
            return None, err("验证码不对：请核对钉钉消息里的 6 位数字", 400)
        S.self_update(e, r["id"], used=1)
        tok = _new_session(r["dt_userid"], r.get("dt_name"), r.get("dept"), "code", _ip(request))
        inv.log({"name": r.get("dt_name") or ""}, "申请人登录（钉钉验证码）", detail={"dept": r.get("dept") or ""})
        return {"ok": True, "token": tok, "me": {"name": r.get("dt_name") or "", "dept": r.get("dept") or "", "via": "code"}}, None
    out, bad = await run_in_threadpool(run)
    return bad if bad else out


@router.post("/api/inv/s/logout")
async def s_logout(request: Request):
    r = self_from_request(request)
    if r:
        S.self_update(E(), r["id"], used=1)
    return {"ok": True}


# ───────────────────────── 登记 / 查看 ─────────────────────────

@router.get("/api/inv/s/payments")
async def s_payments(request: Request):
    """我近 60 天发起的、允许登记后补的审批单；标上已登记的后补单、票夹里是否已有发票。"""
    me, bad = _need(request)
    if bad:
        return bad

    def run():
        names = _later_templates()
        r = idt.list_user_payments(me["dt_userid"], names, days=60, limit=40)
        e = E()
        rows = []
        for x in r.get("rows") or []:
            ex = S.later_open_by_inst(e, x["procInstId"])
            f = S.folder_by_inst(e, x["procInstId"])
            has_inv = bool(f) and any(i.get("kind") == "invoice" and i.get("review") != "void" and i.get("status") != "removed"
                                      for i in S.folder_items(e, f["id"]))
            rows.append(dict(x, laterId=ex["id"] if ex else None, hasInvoice=has_inv))
        return {"ok": bool(r.get("ok")), "rows": rows, "msg": r.get("msg") or "", "templates": names}
    return await run_in_threadpool(run)


@router.get("/api/inv/s/receivers")
async def s_receivers(request: Request):
    me, bad = _need(request)
    if bad:
        return bad
    st = inv.get_settings()
    return {"ok": True, "rows": [{"account": p["account"], "name": p.get("dtName") or p["account"]}
                                  for p in (st.get("people") or []) if p.get("receiver") and p.get("account")]}


@router.post("/api/inv/s/later")
async def s_later_create(request: Request):
    """登记发票后补（与财务代填同一套校验）；只能登记自己发起的单子。"""
    me, bad = _need(request)
    if bad:
        return bad
    body = await inv.body_json(request)
    out, bad = await run_in_threadpool(books._later_create_sync, _actor(me), body, "self", me["dt_userid"])
    return bad if bad else out


def _mine(me, lid):
    l = S.later_get(E(), lid)
    if not l or me["dt_userid"] not in ((l.get("applicant_uid") or ""), (l.get("filed_uid") or "")):
        return None, err("后补单不存在，或不是你的", 404)
    return l, None


@router.get("/api/inv/s/laters")
async def s_laters(request: Request):
    me, bad = _need(request)
    if bad:
        return bad

    def run():
        return {"ok": True, "rows": [books._lv(l) for l in S.laters_of_person(E(), me["dt_userid"], limit=50)]}
    return await run_in_threadpool(run)


@router.post("/api/inv/s/later/{lid}/docs")
async def s_later_docs(lid: int, request: Request):
    """给自己的后补单补传资料（合同、对账单等，只留存不识别）。"""
    me, bad = _need(request)
    if bad:
        return bad
    l, bad = _mine(me, lid)
    if bad:
        return bad
    g = books._later_writable(l)
    if g:
        return g
    files, fields, results, bad = await inv.read_upload_files(request)
    if bad:
        return bad
    if not files and not results:
        return err("没收到文件", 400)
    out, docs = await run_in_threadpool(books.store_later_docs, l, lid, files, _actor(me)["name"])
    results.extend(out)
    inv.audit(_actor(me), "申请人上传后补资料", "后补单#%d" % lid, "、".join(n for n, _ in files)[:400])
    return {"ok": True, "results": results, "docs": docs}


# ───────────────────────── 给财务：入口网址和二维码 ─────────────────────────

def self_url(request):
    return inv._site(request, inv.get_settings()) + SELF_PATH


@router.get("/api/inv/s/link")
async def s_link(request: Request):
    """后补池页「业务同事自助登记」：网址＋二维码（发到群里、贴在墙上都行）。要后补池页面权限。"""
    u, bad = inv.need(request, inv.ENTER_LATER)
    if bad:
        return bad
    return {"ok": True, "url": self_url(request), "qr": "/api/inv/s/qr.png"}


@router.get("/api/inv/s/qr.png")
async def s_qr(request: Request):
    png = await run_in_threadpool(inv._qr_png, self_url(request))
    if not png:
        return err("服务器缺少二维码组件", 503)
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})
