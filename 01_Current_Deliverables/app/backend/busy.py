# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-10 | Author: Claude Opus 5.5 | Version: V2.908
# Description: 【平台通用】占线登记——正在写金蝶的时候挂个牌子，自动部署重启后端之前先看牌子，没人在写才重启。
#   起因(2026-10-10 17:42)：物流付款做账点「保存到金蝶」，付款单刚审核完、凭证还没补，正好赶上自动部署重启，请求被掐断，
#   留下「付款单已审核、凭证没补、系统没记」的半截状态。那十分钟里部署了四次，重启从不看有没有请求正在写金蝶。
#   三样东西：
#     hold(what)     一整段多步操作(审核付款单→等凭证→补分录→提交→落记录)。系统准备重启时，新的不让开始(抛 Draining)，已经开始的照常做完。
#     writing(what)  单次写请求(保存/提交/审核/删除/下推)发出去到回来这一段。kingdee_client._post 自动挂，不拦——多步操作的中间一步不能被拦掉。
#     wrote()        刚向金蝶发过一次写请求。之后 LINGER_S 秒内仍算占线：没有显式 hold 的那些多步写入(汇率、计提、电商下推…)，
#                    两次写请求之间的空档也护得住。
#   部署脚本(/root/deploy_acc.sh，仓库里留底 deploy_acc.sh)的用法：touch 准备重启标记 → 轮询 /api/health 的 busy，等到不占线 → 重启 → 删标记。
#   标记是个文件(后端目录下 .deploy_draining)：脚本和后端在同一台机器，用文件不用鉴权；脚本中途死了，标记 DRAIN_TTL_S 后自己失效。
#   只在单进程里有效(生产是单进程 uvicorn)。
import os
import threading
import time
from contextlib import contextmanager

LINGER_S = 20            # 发完一次写请求后，再算这么久占线
STALE_S = 15 * 60        # 牌子挂了这么久还没摘＝多半是漏摘，不再挡部署
DRAIN_TTL_S = 10 * 60    # 「准备重启」标记最多管这么久
DRAIN_MSG = "系统正在更新，大约一分钟后再点。这张还没有开始写金蝶，不会做到一半。"

_lock = threading.Lock()
_holds = {}
_seq = [0]
_last_write = [0.0]
_tls = threading.local()


class Draining(RuntimeError):
    """系统准备重启：新的写金蝶操作先不开始。"""


def drain_path():
    return os.getenv("WB_DRAIN_FILE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".deploy_draining")


def draining():
    try:
        return time.time() - os.path.getmtime(drain_path()) < DRAIN_TTL_S
    except OSError:
        return False


def _enter(what):
    with _lock:
        _seq[0] += 1
        _holds[_seq[0]] = {"what": str(what), "since": time.time()}
        return _seq[0]


def _leave(k):
    with _lock:
        _holds.pop(k, None)


@contextmanager
def hold(what):
    """一段不能被重启打断的多步操作。先挂牌子再看标记：部署脚本是先放标记再看牌子，两边这样错开，不会出现「都以为对方没动」。
    同一个线程里套着用(保存到金蝶里面再建红冲凭证)，里面那层不再看标记——已经开始的要做完。"""
    depth = getattr(_tls, "depth", 0)
    k = _enter(what)
    try:
        if not depth and draining():
            raise Draining(DRAIN_MSG)
        _tls.depth = depth + 1
        try:
            yield
        finally:
            _tls.depth = depth
    finally:
        _leave(k)


@contextmanager
def writing(what):
    """单次写请求进行中：只登记，不拦。"""
    k = _enter(what)
    try:
        yield
    finally:
        _leave(k)
        wrote()


def wrote():
    _last_write[0] = time.time()


def snapshot():
    """现在占不占线 → {busy, what:[说明], draining}。what 只放操作名和秒数，不放金额、单号(/api/health 不用登录就能看)。"""
    now = time.time()
    with _lock:
        hs = [(h["what"], now - h["since"]) for h in _holds.values()]
    live = [(w, a) for w, a in hs if a < STALE_S]
    ago = (now - _last_write[0]) if _last_write[0] else None
    linger = ago is not None and ago < LINGER_S
    what = ["%s（已 %d 秒）" % (w, a) for w, a in live]
    if linger and not live:
        what.append("%d 秒前刚写过金蝶" % ago)
    return {"busy": bool(live) or linger, "what": what, "draining": draining()}
