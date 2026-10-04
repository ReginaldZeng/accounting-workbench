# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-04 | Author: Claude Opus 5.5 | Version: V2.790（首页待办区）
# Description: 首页待办区 · 待办表与「记一笔 / 撤回 / 办结」入口（《首页待办区 需求确认书 v1.0》D5/D8/D9/D11）。
#   各工具线在「做完了、轮到别人接手」的那一刻调 open_item() 记一笔；首页只读这张表，不现查金蝶。
#   · 待办记的是【环节】不是人：挂给谁每次读的时候按「待办处理人」设置现算——换了处理人，没办完的自动跟到新人名下。
#   · 某环节没配处理人 → 挂到主管理员名下并写明「这个环节还没指定处理人」，不是不生成。
#   · 只有三种去向：办结(done) / 源头撤回(withdrawn) / 主管理员作废(void，留痕)。不能手工新建、不能删。
#   · 本模块不碰金蝶、不 import 任何 router；回读金蝶状态的核对逻辑在 routers/todo.py。
#   · 新表 todo_item 自带 MetaData，导入时 create_all（只建不改既有表）。
#   ⚠ 调用方铁律：记待办失败只留痕、不拦原有动作——用 safe_open / safe_withdraw，别让待办没记上害得汇率写不进金蝶。
import json
from datetime import datetime, timedelta

from sqlalchemy import (Column, Integer, MetaData, String, Table, Text, UniqueConstraint,
                        insert, select, update)

import db

_md = MetaData()
todo_item = Table(
    "todo_item", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("scene", String(40)),             # 环节 key（见 SCENES）
    Column("ref", String(160)),              # 环节内的业务键（汇率=年-月:组织；付款做账=审批实例号…）
    Column("title", String(200)),            # 事项
    Column("sub", Text),                     # 第二行说明（几条、已审几条）
    Column("warn", Text),                    # 要如实提醒的情形（还有草稿没提交 / 金蝶里找不到了）
    Column("state", String(200)),            # 现在的状态
    Column("holder", String(10)),            # scene=挂给环节处理人 / origin=挂回发起人（提交没成功、金蝶里被删）
    Column("origin", String(50)),            # 谁交过来的（人名；机器跑的写「系统 · xxx」）
    Column("origin_bot", Integer),           # 1=定时任务自动跑的
    Column("status", String(12)),            # open / done / withdrawn / void
    Column("created_ts", String(20)),
    Column("updated_ts", String(20)),
    Column("done_ts", String(20)),
    Column("done_by", String(50)),
    Column("done_how", String(200)),
    Column("checked_ts", String(20)),        # 上次去金蝶核对的时间
    Column("check_msg", String(200)),        # 上次核对结果（没连上金蝶 / 还没审完）
    Column("payload", Text),                 # 环节自用 JSON
    UniqueConstraint("scene", "ref", name="uq_todo_scene_ref"),
)
_md.create_all(db._engine)

_FMT = "%Y-%m-%d %H:%M:%S"
ASSIGN_KEY = "todo_assignees"                # {scene: [账号名]}
LATE_WORKDAYS = 3                            # 挂了超过 3 个工作日「等了多久」变色（Q4）
RECENT_DAYS = 7                              # 「最近办结」看近 7 天

# 环节注册表。kind: rev=待审核 / do=待处理；where: kd=在金蝶办 / wb=在本工作台办；nav=点「看明细」跳哪个菜单；
# kd=True 的环节要回读金蝶状态才能销；manual=True 允许处理人手动点办结（第一批只有计提更正）。
SCENES = {
    "fx_audit": {"label": "汇率待审", "role": "汇率审核人", "kind": "rev", "where": "kd", "place": "汇率体系",
                 "nav": "fxrate", "kd": True, "manual": False},
    "logi_accrual_audit": {"label": "物流计提凭证待审", "role": "物流计提凭证审核人", "kind": "rev", "where": "kd",
                           "place": "总账凭证", "nav": "logistics", "kd": True, "manual": False},
    "logi_voucher_audit": {"label": "付款做账凭证待审", "role": "付款凭证审核人", "kind": "rev", "where": "kd",
                           "place": "总账凭证", "nav": "logisticsvoucher", "kd": True, "manual": False},
    "logi_fix": {"label": "计提更正待办", "role": "计提更正处理人", "kind": "do", "where": "wb", "place": "付款做账",
                 "nav": "logisticsvoucher", "kd": False, "manual": True},
}
SCENE_ORDER = ["fx_audit", "logi_accrual_audit", "logi_voucher_audit", "logi_fix"]


def _now():
    return datetime.now().strftime(_FMT)


def _row(r):
    d = dict(r)
    try:
        d["payload"] = json.loads(d.get("payload") or "{}")
    except Exception:
        d["payload"] = {}
    return d


# ---------------- 记一笔 / 撤回 / 办结 ----------------
def open_item(scene, ref, title, sub="", origin="", bot=False, payload=None, state="", warn="", holder="scene"):
    """记一笔待办（同环节同业务键只有一条）。已有且没办完 → 更新内容、不动「等了多久」的起点；
    已办结/已撤回的又来了（撤了重录、办结后又登记了新的）→ 重新挂上，从现在起算。返回 id。
    sub / warn / state / holder 传 None＝已有且没办完时保持原样（那几样是上次核对金蝶写的，别被重记一笔冲掉）。"""
    if scene not in SCENES:
        raise ValueError("不认识的待办环节：%s" % scene)
    ref, now = str(ref)[:160], _now()
    base = {"title": str(title)[:200], "origin": str(origin or "")[:50], "origin_bot": 1 if bot else 0, "updated_ts": now,
            "payload": json.dumps(payload or {}, ensure_ascii=False)}
    shown = {"sub": sub, "warn": warn, "state": None if state is None else str(state)[:200],
             "holder": None if holder is None else (holder if holder in ("scene", "origin") else "scene")}
    with db._engine.begin() as c:
        ex = c.execute(select(todo_item.c.id, todo_item.c.status)
                       .where((todo_item.c.scene == scene) & (todo_item.c.ref == ref))).first()
        if ex and ex[1] == "open":
            c.execute(update(todo_item).where(todo_item.c.id == ex[0])
                      .values(**base, **{k: v for k, v in shown.items() if v is not None}))
            return ex[0]
        fresh = dict(base, status="open", created_ts=now, done_ts="", done_by="", done_how="", checked_ts="", check_msg="",
                     sub=str(sub or ""), warn=str(warn or ""), state=str(state or "")[:200], holder=shown["holder"] or "scene")
        if ex:
            c.execute(update(todo_item).where(todo_item.c.id == ex[0]).values(**fresh))
            return ex[0]
        return c.execute(insert(todo_item).values(scene=scene, ref=ref, **fresh)).inserted_primary_key[0]


def finish(item_id, by="", how="", status="done"):
    """办结 / 撤回 / 作废。只动还开着的；返回是否真改了。"""
    with db._engine.begin() as c:
        n = c.execute(update(todo_item).where((todo_item.c.id == int(item_id)) & (todo_item.c.status == "open"))
                      .values(status=status, done_ts=_now(), done_by=str(by or "")[:50], done_how=str(how or "")[:200],
                              updated_ts=_now())).rowcount
    return bool(n)


def withdraw(scene, ref, why="源头已撤销，待办自动撤回"):
    it = get(scene, ref)
    return bool(it and it["status"] == "open" and finish(it["id"], "", why, "withdrawn"))


def patch(item_id, **vals):
    """核对后更新显示内容（sub/warn/state/holder/checked_ts/check_msg/payload）。"""
    if "payload" in vals and not isinstance(vals["payload"], str):
        vals["payload"] = json.dumps(vals["payload"] or {}, ensure_ascii=False)
    vals["updated_ts"] = _now()
    with db._engine.begin() as c:
        c.execute(update(todo_item).where(todo_item.c.id == int(item_id)).values(**vals))


def safe_open(*a, **k):
    """给各工具线用：记待办出任何错都只留痕、不往外抛（不拦原有动作）。"""
    try:
        return open_item(*a, **k)
    except Exception as e:
        try:
            db.audit("系统", "待办-记一笔失败", "%s:%s" % (a[0] if a else "", a[1] if len(a) > 1 else ""), str(e)[:200])
        except Exception:
            pass
        return None


def safe_withdraw(scene, ref, why="源头已撤销，待办自动撤回"):
    try:
        return withdraw(scene, ref, why)
    except Exception:
        return False


# ---------------- 读 ----------------
def get(scene, ref):
    with db._engine.connect() as c:
        r = c.execute(select(todo_item).where((todo_item.c.scene == scene) & (todo_item.c.ref == str(ref)))).mappings().first()
    return _row(r) if r else None


def get_by_id(item_id):
    with db._engine.connect() as c:
        r = c.execute(select(todo_item).where(todo_item.c.id == int(item_id))).mappings().first()
    return _row(r) if r else None


def list_open(scene=None):
    q = select(todo_item).where(todo_item.c.status == "open")
    if scene:
        q = q.where(todo_item.c.scene == scene)
    with db._engine.connect() as c:
        return [_row(r) for r in c.execute(q.order_by(todo_item.c.created_ts)).mappings().all()]


def list_recent(days=RECENT_DAYS):
    since = (datetime.now() - timedelta(days=days)).strftime(_FMT)
    with db._engine.connect() as c:
        rows = c.execute(select(todo_item).where((todo_item.c.status != "open") & (todo_item.c.done_ts >= since))
                         .order_by(todo_item.c.done_ts.desc())).mappings().all()
    return [_row(r) for r in rows]


# ---------------- 处理人（D4 按环节固定配人 / D8 记环节不记人）----------------
def _active_names():
    return {u["name"]: u for u in db.list_users() if u.get("active", 1)}


def _supers(users=None):
    users = users if users is not None else _active_names()
    return [n for n, u in users.items() if u.get("role") == "admin"]


def assignees(scene, users=None):
    """→ (处理人名单, 是否配置过)。没配或配的人都停用了 → 回落主管理员，configured=False。"""
    users = users if users is not None else _active_names()
    picked = [n for n in ((db.get_setting(ASSIGN_KEY, None) or {}).get(scene) or []) if n in users]
    return (picked, True) if picked else (_supers(users), False)


def holders(it, users=None):
    """这条现在挂在谁名下 → (名单, 说明)。说明非空＝要在行上如实写出来的情形。"""
    users = users if users is not None else _active_names()
    if it.get("holder") == "origin" and not it.get("origin_bot") and it.get("origin") in users:
        return [it["origin"]], ""
    names, configured = assignees(it["scene"], users)
    return names, ("" if configured else "这个环节还没指定处理人，先挂在主管理员名下")


def set_assignees(mapping, operator):
    """存「待办处理人」设置；返回 [(环节, 旧名单, 新名单)] 供留痕。"""
    users = _active_names()
    old = db.get_setting(ASSIGN_KEY, None) or {}
    new, changes = dict(old), []
    for scene in SCENES:
        if scene not in mapping:
            continue
        names = []
        for n in mapping.get(scene) or []:
            n = str(n).strip()
            if n in users and n not in names:
                names.append(n)
        if names != (old.get(scene) or []):
            changes.append((scene, old.get(scene) or [], names))
        new[scene] = names
    db.set_setting(ASSIGN_KEY, new, operator)
    return changes


# ---------------- 首页用：我的待办 ----------------
def _workdays_between(a, b):
    """a 到 b 之间过了几个工作日（周一到周五，不含起点当天；不认法定节假日）。"""
    n, d = 0, a.date()
    while d < b.date():
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def _age(ts, now):
    try:
        t = datetime.strptime(ts, _FMT)
    except Exception:
        return "", False
    mins = max(0, int((now - t).total_seconds() // 60))
    text = "刚刚" if mins < 2 else "%d 分钟" % mins if mins < 60 else "%d 小时" % (mins // 60) if mins < 1440 else "%d 天" % (mins // 1440)
    return text, _workdays_between(t, now) > LATE_WORKDAYS


def _view(it, users, now):
    sc = SCENES[it["scene"]]
    names, note = holders(it, users)
    if it["status"] != "open":
        note = ""                                       # 办完了的不用再提「还没指定处理人」
    age, late = _age(it.get("created_ts") or "", now)
    back = it.get("holder") == "origin"                 # 挂回发起人的：要他自己去办（提交 / 处理金蝶里被删的），不是等审
    return {"id": it["id"], "scene": it["scene"], "sceneLabel": sc["label"], "kind": "do" if back else sc["kind"],
            "where": sc["where"], "place": sc["place"], "nav": sc["nav"], "kd": sc["kd"], "manual": sc["manual"],
            "title": it.get("title") or "", "sub": it.get("sub") or "", "warn": it.get("warn") or "", "note": note,
            "state": it.get("state") or "", "origin": it.get("origin") or "", "bot": bool(it.get("origin_bot")),
            "holders": names, "age": age, "late": late and it["status"] == "open", "status": it["status"],
            "checkedAt": (it.get("checked_ts") or "")[11:16], "checkMsg": it.get("check_msg") or "",
            "doneBy": it.get("done_by") or "", "doneAt": (it.get("done_ts") or "")[5:16], "doneHow": it.get("done_how") or ""}


def for_user(user):
    """→ {mine: 挂在我名下的, sent: 我交出去的（没办完的 + 近 7 天办完的）, done: 近 7 天办结的（与我有关；主管理员看全部）}"""
    name, users, now = user["name"], _active_names(), datetime.now()
    is_super = db.is_super(user)
    opens = [_view(it, users, now) for it in list_open()]
    recent = [_view(it, users, now) for it in list_recent()]
    mine = [v for v in opens if name in v["holders"]]
    sent_open = [v for v in opens if v["origin"] == name and not v["bot"] and name not in v["holders"]]
    sent_done = [v for v in recent if v["origin"] == name and not v["bot"] and v["status"] == "done"]
    done = [v for v in recent if is_super or v["origin"] == name or v["doneBy"] == name or name in v["holders"]]
    return {"mine": mine, "sent": sent_open + sent_done, "done": done,
            "counts": {"mine": len(mine), "late": sum(1 for v in mine if v["late"]), "waiting": len(sent_open)}}
