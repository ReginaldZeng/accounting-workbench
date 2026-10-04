# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-04 | Author: Claude Opus 5.5 | Version: V2.790（首页待办区）
# Description: 首页待办区 · 接口与后台核对线程（《首页待办区 需求确认书 v1.0》）。
#   · GET  /api/todo/mine    我的待办（待我处理 / 我发起的 / 最近办结）。只读本地待办表，不碰金蝶——首页可以直接调（铁律 4）。
#   · POST /api/todo/check   「我已办，立即核对」：马上去金蝶读状态，审了当场销（D3 的滞后出口）。只能核与自己有关的。
#   · POST /api/todo/close   处理人手动办结——只有计提更正这一环允许（Q2：走打印更正单的，系统没法知道专人改的哪张凭证）。
#   · POST /api/todo/void    主管理员作废（必须写原因，留痕）。
#   · GET/PUT /api/todo/config  系统设置 ›「待办处理人」：每个环节配一个或多个工作台账号（D4），改动留痕。
#   · 后台线程每 20 分钟把所有没办结的核一遍；本机测试库（sqlite）不起，免得开发机去查真金蝶。
import threading
import time

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from core import JSONResponse, _current_user, db
import todo_store as ts
import todo_scenes as sc

router = APIRouter()

EVERY_MIN = 20
_LOCK = threading.Lock()                 # 定时核对和「立即核对」别同时跑
_STATE = {"started": False, "lastRun": "", "lastResult": None, "lastError": ""}


def _is_local():
    try:
        return str(db.DB_URL).startswith("sqlite")
    except Exception:
        return False


def _related(u, it):
    """这条待办和这个人有没有关系：挂在他名下 / 他发起的 / 主管理员。"""
    names, _ = ts.holders(it)
    return db.is_super(u) or u["name"] in names or (it.get("origin") == u["name"] and not it.get("origin_bot"))


def _checks(ids, by):
    with _LOCK:
        return sc.run_checks(ids, by)


@router.get("/api/todo/mine")
def todo_mine(request: Request):
    u = _current_user(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "未登录"}, status_code=401)
    return {"ok": True, "everyMin": EVERY_MIN, "lastRun": _STATE["lastRun"][11:16], **ts.for_user(u)}


@router.post("/api/todo/check")
async def todo_check(request: Request):
    """立即核对。body {id}＝核这一条；不带 id＝把和我有关的、没办结的都核一遍。"""
    u = _current_user(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "未登录"}, status_code=401)
    b = await request.json()
    if b.get("id"):
        it = ts.get_by_id(b["id"])
        if not it or not _related(u, it):
            return JSONResponse({"ok": False, "msg": "没有这条待办，或它不在你名下"}, status_code=404)
        ids = [it["id"]] if it["status"] == "open" else []
    else:
        ids = [it["id"] for it in ts.list_open() if _related(u, it)]
    res = await run_in_threadpool(_checks, ids, u["name"]) if ids else {"checked": 0, "done": 0, "withdrawn": 0, "failed": 0, "kd_error": ""}
    it = ts.get_by_id(b["id"]) if b.get("id") else None
    if it and it["status"] == "done":
        msg = "金蝶里已审核，这条已销账"
    elif it and it["status"] == "withdrawn":
        msg = "这条的源头已撤销，待办已撤回"
    elif res.get("kd_error"):
        msg = "没连上金蝶，这次没核对成：%s" % res["kd_error"]
    elif res.get("failed"):
        msg = (it or {}).get("check_msg") or "有 %d 条这次没核对成" % res["failed"]
    elif it:
        msg = "核对过了：%s" % (it.get("state") or "还没办完")
    else:
        msg = "核对了 %d 条，销账 %d 条" % (res["checked"], res["done"])
    return {"ok": True, "msg": msg, "result": res, **ts.for_user(u)}


@router.post("/api/todo/close")
async def todo_close(request: Request):
    u = _current_user(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "未登录"}, status_code=401)
    b = await request.json()
    it = ts.get_by_id(b.get("id") or 0)
    if not it or it["status"] != "open":
        return JSONResponse({"ok": False, "msg": "没有这条待办，或它已经办结"}, status_code=404)
    if not ts.SCENES[it["scene"]]["manual"]:
        return JSONResponse({"ok": False, "msg": "这一类在金蝶里审了会自动销账，不用手动点；等不及可以点「立即核对」"}, status_code=400)
    names, _ = ts.holders(it)
    if u["name"] not in names and not db.is_super(u):
        return JSONResponse({"ok": False, "msg": "这条不在你名下"}, status_code=403)
    vno = str(b.get("vno") or "").strip()
    if not vno:
        return JSONResponse({"ok": False, "msg": "请填金蝶里改好的那张凭证号（如 记-312），方便以后回查"}, status_code=400)
    sc.fix_manual_close(it, u["name"], vno)
    db.audit(u["name"], "待办-手动办结", it["title"], "已在金蝶改好：%s" % vno)
    return {"ok": True, **ts.for_user(u)}


@router.post("/api/todo/void")
async def todo_void(request: Request):
    u = _current_user(request)
    if not db.is_super(u):
        return JSONResponse({"ok": False, "msg": "仅主管理员"}, status_code=403)
    b = await request.json()
    it = ts.get_by_id(b.get("id") or 0)
    reason = str(b.get("reason") or "").strip()[:150]
    if not it or it["status"] != "open":
        return JSONResponse({"ok": False, "msg": "没有这条待办，或它已经办结"}, status_code=404)
    if not reason:
        return JSONResponse({"ok": False, "msg": "作废要写原因（会留痕）"}, status_code=400)
    ts.finish(it["id"], u["name"], "主管理员作废：%s" % reason, "void")
    db.audit(u["name"], "待办-作废", it["title"], reason)
    return {"ok": True, **ts.for_user(u)}


# ---------------- 系统设置 ›「待办处理人」 ----------------
def _settings_user(request):
    u = _current_user(request)
    return u if db.user_can(u, "enter_settings") else None


@router.get("/api/todo/config")
def todo_config(request: Request):
    if not _settings_user(request):
        return JSONResponse({"ok": False, "msg": "仅主管理员"}, status_code=403)
    users = ts._active_names()
    opens = ts.list_open()
    scenes = []
    for k in ts.SCENE_ORDER:
        m = ts.SCENES[k]
        names, configured = ts.assignees(k, users)
        scenes.append({"key": k, "label": m["label"], "role": m["role"], "where": m["where"], "place": m["place"],
                       "names": names if configured else [], "fallback": [] if configured else names,
                       "open": sum(1 for it in opens if it["scene"] == k)})
    return {"ok": True, "scenes": scenes, "local": _is_local(), "everyMin": EVERY_MIN, "thread": dict(_STATE),
            "users": [{"name": n, "post": u.get("post") or "", "grp": u.get("grp") or "", "admin": u.get("role") == "admin"}
                      for n, u in users.items()]}


@router.put("/api/todo/config")
async def todo_config_save(request: Request):
    u = _settings_user(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "仅主管理员"}, status_code=403)
    b = await request.json()
    changes = ts.set_assignees(b.get("assignees") or {}, u["name"])
    for scene, old, new in changes:
        db.audit(u["name"], "待办处理人-变更", ts.SCENES[scene]["role"],
                 "%s → %s" % ("、".join(old) or "未指定", "、".join(new) or "未指定"))
    return {"ok": True, "changed": len(changes)}


# ---------------- 后台核对线程 ----------------
def _loop():
    time.sleep(120)                      # 开机先让别的线程起来
    while True:
        try:
            r = _checks(None, "定时")
            _STATE.update(lastRun=ts._now(), lastResult=r, lastError=r.get("kd_error") or "")
            import worker_store      # 数字员工办公室·待办核对员：每圈报到；真销了账 / 没连上金蝶才记一笔（只写条数）
            worker_store.beat("todo_check")
            if r.get("kd_error"):
                worker_store.record("todo_check", ok=False, summary="没连上金蝶，这一轮没核对成", error=r["kd_error"])
            elif r.get("done") or r.get("withdrawn"):
                worker_store.record("todo_check", n=r.get("done") or 0,
                                    summary="已核对 %d 条，销账 %d 条" % (r.get("checked") or 0, r.get("done") or 0))
        except Exception as e:
            _STATE["lastError"] = str(e)[:300]      # 绝不让线程死掉：记下来，下一轮再试
        time.sleep(EVERY_MIN * 60)


def start_timer():
    """起核对线程（一个进程一次）；sqlite 库（本机/测试）不起，免得开发机去查真金蝶。"""
    if _is_local() or _STATE["started"]:
        return False
    _STATE["started"] = True
    threading.Thread(target=_loop, name="todo-check", daemon=True).start()
    return True


start_timer()
