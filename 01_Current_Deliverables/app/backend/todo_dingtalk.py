# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-11 | Author: Claude Opus 5.5 | Version: V2.911（首页待办同步到钉钉待办）
# Description: 用户 2026-10-11 贴钉钉「待办 › 待我处理」的截图（里面有「来自: NPI」的一条）：「我们的待办有没有办法这样子呢」。
#   首页待办区的每一条，同步写到处理人的钉钉待办里（显示「来自: 星衡智策」、右边「去查看」跳回工作台对应页面）；
#   工作台这边办结了（金蝶里审了自动销 / 手动办结），钉钉那条跟着标成已完成；源头撤回、作废的，钉钉那条删掉。
#   · 做成「对账」而不是「每个动作各推一次」：sync() 拿「现在还开着的待办」和「上次写到钉钉的」比，缺的补、变了的改、多的收。
#     记一笔 / 办结 / 换处理人之后踢一脚(kick)，另外跟着每 20 分钟的核对再对一遍——哪一步漏了、钉钉一时不通，下一轮自己补上。
#   · 钉钉待办列表里只显示标题那一行（实测：说明文字不显示），所以主体、金额这些拼进标题。
#   · 钉钉里没有「同意 / 拒绝」：凭证要在金蝶审，钉钉里只能「去查看」。人在钉钉里手动点了完成，工作台这边不认（以金蝶为准）。
#   · 默认关。打开前在设置页能看到「会给谁写几条、谁发不到」。关掉时把已经写过去的都收回来。
#   · 要钉钉应用开「待办应用中待办写权限」(Todo.Todo.Write)；按人写待办用 unionId（由 dt_userid 换，换到的记下来不重复查）。
#   · 本模块出任何错都不往外抛：钉钉待办没写上，不能拦记账、不能拦待办本身。
import json
import re
import threading
import time

import db
import todo_store as ts

CFG_KEY = "todo_dt_cfg"        # {on, url, by, at}
MAP_KEY = "todo_dt_map"        # {待办 id: {task, owner, names, title, at}}——上次写到钉钉的样子
UNION_KEY = "todo_dt_union"    # {dt_userid: unionId}
API = "https://api.dingtalk.com/v1.0/todo/users/%s/tasks"
_LOCK = threading.Lock()
_KICK = {"pending": False}
STATE = {"lastRun": "", "lastResult": None, "lastError": ""}
_VOUCHER_TITLE = re.compile(r"^付款做账 · (记-\S+)　(.*)$")     # todo_scenes.voucher_touch 写的事项：凭证号＋全角空格＋收款方 账期


_ENDS_YM = re.compile(r" \d{4}-\d{2}$")


def cfg():
    c = db.get_setting(CFG_KEY, None) or {}
    return {"on": bool(c.get("on")), "url": str(c.get("url") or "").strip().rstrip("/"), "by": c.get("by") or "", "at": c.get("at") or ""}


def set_cfg(on, url, by):
    old = cfg()
    new = {"on": bool(on), "url": str(url if url is not None else old["url"]).strip().rstrip("/"), "by": by, "at": ts._now()}
    db.set_setting(CFG_KEY, new, by)
    return old, cfg()


# 钉钉那一行开头两段（用户 2026-10-13 定）：方括号里写「在哪办的什么事」，紧跟着写「是什么业务来的」。
# 值＝(在金蝶里审的是什么；不在金蝶办的填 None, 不在金蝶办的方括号里写什么, 什么业务来的)
_DT_HEAD = {"fx_audit": ("金蝶汇率", "", "汇率录入"),
            "logi_accrual_audit": ("金蝶凭证", "", "物流计提"),
            "logi_voucher_audit": ("金蝶凭证", "", "物流付款做账"),
            "logi_fix": (None, "计提更正", "物流对账复核")}


def _lead(it):
    """方括号里那几个字：金蝶凭证审核 / 金蝶汇率审核；提交没成功挂回做账人的是「提交」，金蝶里找不到的是「核实」。"""
    thing, own, _ = _DT_HEAD[it["scene"]]
    if not thing:
        return own
    if "找不到" in str(it.get("state") or ""):
        return thing + "核实"
    return thing + ("提交" if it.get("holder") == "origin" else "审核")


def _what(it):
    """业务后面那几段：哪个主体哪张、哪家、多少——按环节各拼各的。认不出的老格式原样用「事项｜说明」。"""
    p, title, sub = it.get("payload") or {}, str(it.get("title") or ""), str(it.get("sub") or "")
    nums = sub.replace(" · ", "，")
    try:
        if it["scene"] == "fx_audit":
            from kernels.fx_rate import FX_ORGS
            org = next((o["name"] for o in FX_ORGS if o["code"] == str(p["org"])), "组织 %s" % p["org"])
            return ["%s 年 %s 月" % (p["year"], p["month"]), org, nums]
        if it["scene"] == "logi_accrual_audit":
            return ["%s 年 %s 月" % (p["year"], p["period"]), nums]
        if it["scene"] == "logi_voucher_audit":
            m = _VOUCHER_TITLE.match(title)
            bits = sub.split(" · ")
            amt = next((b[3:] for b in bits if b.startswith("借贷 ")), "")
            who = [b for b in bits if not b.startswith(("借贷 ", "共 "))]
            ym = str(p.get("ym") or "")                                   # 凭证是哪个月的（凭证号每月从 1 排）
            month = ["%s 年 %d 月" % (ym[:4], int(ym[5:7]))] if len(ym) == 7 else []
            paid = m.group(2).strip()                                     # 收款方 + 账期：账期后面标「账单」，别和凭证月份混
            return [" ".join(who + month + [m.group(1)]), paid + (" 账单" if _ENDS_YM.search(paid) else ""), ("金额 " + amt) if amt else ""]
        if it["scene"] == "logi_fix":
            return ["%s %s" % (p["carrier"], p["period"]), nums]
    except Exception:
        pass
    return [title, sub]


def title_of(it):
    """钉钉待办列表只显示这一行，所以一行里要说全：【在哪办的什么事】什么业务来的｜哪个主体哪张｜哪家｜多少。
    例：【金蝶凭证审核】物流付款做账｜深圳星期九 2026 年 10 月 记-1｜湖北极鲜达供应链有限责任公司 2026-08 账单｜金额 6,093.62"""
    sc, warn = ts.SCENES[it["scene"]], str(it.get("warn") or "")
    parts = [_DT_HEAD[it["scene"]][2]] + [x for x in _what(it) if x]
    if warn:                                         # 有要如实提醒的情形：它本身就写了该怎么办
        parts.append("⚠ " + warn)
    elif not sc["kd"]:                               # 在金蝶办的方括号里已经写了；不在金蝶办的要说去哪办
        parts.append("在工作台「%s」处理" % sc["place"])
    return ("【%s】%s" % (_lead(it), "｜".join(parts)))[:180]


def desc_of(it):
    sc = ts.SCENES[it["scene"]]
    how = "在金蝶「%s」里审核后，这条会自动完成（最长隔 20 分钟）。" % sc["place"] if sc["kd"] else "在工作台「%s」里处理，办结后这条自动完成。" % sc["place"]
    return "\n".join(x for x in [sc["label"], str(it.get("state") or ""), ("交过来的：%s" % it["origin"]) if it.get("origin") else "", how] if x)[:900]


def link_of(it, base):
    return "%s/#/%s" % (base, ts.SCENES[it["scene"]]["nav"])


def source_of(it):
    return "xhzc-todo-%s-%s" % (it["id"], "".join(ch for ch in str(it.get("created_ts") or "") if ch.isdigit()))


def people(names):
    """账号名 → {名: unionId}；没认过钉钉身份或换不到的不在里面。"""
    from sqlalchemy import select
    import notifier
    with db._engine.connect() as c:
        rows = c.execute(select(db.users.c.name, db.users.c.dt_userid).where(db.users.c.name.in_(list(names) or [""]))).all()
    uid = {r[0]: r[1] for r in rows if r[1]}
    cache = dict(db.get_setting(UNION_KEY, None) or {})
    need = [u for u in uid.values() if u not in cache]
    if need:
        import requests
        conf = notifier.load_dingtalk_conf()
        tok = notifier._dt_token(conf)
        for u in need:
            r = requests.post("https://oapi.dingtalk.com/topapi/v2/user/get", params={"access_token": tok}, json={"userid": u}, timeout=20).json()
            un = (r.get("result") or {}).get("unionid")
            if un:
                cache[u] = un
        db.set_setting(UNION_KEY, cache, "系统")
    return {n: cache[u] for n, u in uid.items() if u in cache}


def preview():
    """打开之前给人看：现在没办完的待办会写给谁几条、谁发不到。只读本地库，不调钉钉。"""
    users = ts._active_names()
    per, miss = {}, set()
    for it in ts.list_open():
        names, _ = ts.holders(it, users)
        for n in names:
            if users.get(n, {}).get("dt_bound"):
                per[n] = per.get(n, 0) + 1
            else:
                miss.add(n)
    return {"per": [{"name": n, "n": k} for n, k in sorted(per.items(), key=lambda t: -t[1])], "miss": sorted(miss),
            "synced": len(db.get_setting(MAP_KEY, None) or {}), "lastRun": STATE["lastRun"], "lastError": STATE["lastError"]}


class _Api:
    def __init__(self):
        import notifier
        import requests
        self.rq = requests
        conf = notifier.load_dingtalk_conf()
        if not conf:
            raise RuntimeError("未配置钉钉（conf.ini [dingtalk]）")
        self.h = {"x-acs-dingtalk-access-token": notifier._dt_v2_token(conf), "Content-Type": "application/json"}

    def _do(self, method, owner, task="", body=None):
        url = (API % owner) + (("/" + task) if task else "")
        r = self.rq.request(method, url, params={"operatorId": owner}, headers=self.h, timeout=20,
                            data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None)
        try:
            d = r.json()
        except Exception:
            d = {}
        if r.status_code != 200:
            raise RuntimeError("钉钉待办接口 %s：%s" % (r.status_code, str(d.get("message") or r.text)[:200]))
        return d

    def create(self, owner, unions, it, base):
        url = link_of(it, base)
        d = self._do("POST", owner, body={"sourceId": source_of(it), "subject": title_of(it), "description": desc_of(it),
                                          "executorIds": unions, "participantIds": [], "detailUrl": {"appUrl": url, "pcUrl": url},
                                          "isOnlyShowExecutor": True, "priority": 20, "notifyConfigs": {"dingNotify": "0"}})
        if not d.get("id"):
            raise RuntimeError("钉钉待办没返回编号：%s" % str(d)[:160])
        return d["id"]

    def update(self, owner, task, it):
        self._do("PUT", owner, task, {"subject": title_of(it), "description": desc_of(it)})

    def done(self, owner, task):
        self._do("PUT", owner, task, {"done": True})

    def remove(self, owner, task):
        self._do("DELETE", owner, task)


def sync(api=None):
    """对一遍账。→ {on, created, updated, closed, removed, failed, miss}。不抛错。"""
    out = {"on": False, "created": 0, "updated": 0, "closed": 0, "removed": 0, "failed": 0, "miss": [], "error": ""}
    with _LOCK:
        try:
            c = cfg()
            out["on"] = c["on"]
            mp = dict(db.get_setting(MAP_KEY, None) or {})
            if not c["on"] and not mp:
                return out
            opens = {str(it["id"]): it for it in ts.list_open()} if c["on"] else {}       # 关掉＝一条都不该留在钉钉
            if c["on"] and not c["url"]:
                raise RuntimeError("还没填门户网址（钉钉里点「去查看」打开的地址）")
            users = ts._active_names()
            hold = {k: ts.holders(it, users)[0] for k, it in opens.items()}
            who = people({n for ns in hold.values() for n in ns}) if opens else {}
            api = api or _Api()
            miss, err = set(), ""
            for k, it in opens.items():
                names = [n for n in hold[k] if n in who]
                miss.update(n for n in hold[k] if n not in who)
                cur, title = mp.get(k), title_of(it)
                try:
                    if cur and cur.get("names") != names:                    # 换了处理人：旧的收回，按新名单重写
                        api.remove(cur["owner"], cur["task"])
                        mp.pop(k)
                        cur = None
                        out["removed"] += 1
                    if not names:
                        continue
                    if not cur:
                        owner = who[names[0]]
                        mp[k] = {"task": api.create(owner, [who[n] for n in names], it, c["url"]), "owner": owner, "names": names,
                                 "title": title, "at": ts._now()}
                        out["created"] += 1
                    elif cur.get("title") != title:
                        api.update(cur["owner"], cur["task"], it)
                        cur["title"] = title
                        out["updated"] += 1
                except Exception as e:
                    out["failed"] += 1
                    err = str(e)[:300]
            for k in [k for k in mp if k not in opens]:
                cur = mp[k]
                try:
                    it = ts.get_by_id(k) if c["on"] else None
                    if it and it["status"] == "done":
                        api.done(cur["owner"], cur["task"])
                        out["closed"] += 1
                    else:                                                    # 撤回、作废、同步关掉了：钉钉那条收回
                        api.remove(cur["owner"], cur["task"])
                        out["removed"] += 1
                    mp.pop(k)
                except Exception as e:
                    out["failed"] += 1
                    err = str(e)[:300]
            db.set_setting(MAP_KEY, mp, "系统")
            out["miss"], out["error"] = sorted(miss), err
        except Exception as e:
            out["error"] = str(e)[:300]
        STATE.update(lastRun=ts._now(), lastResult=out, lastError=out["error"])
        if out["created"] or out["closed"] or out["removed"] or out["error"]:
            try:
                db.audit("系统", "待办-同步钉钉待办", "对账", "新写 %d 条、改 %d 条、标完成 %d 条、收回 %d 条、没成 %d 条%s" % (
                    out["created"], out["updated"], out["closed"], out["removed"], out["failed"], ("；" + out["error"]) if out["error"] else ""))
            except Exception:
                pass
        return out


def kick(delay=3):
    """有待办记了一笔 / 办结了 / 换了处理人：过几秒在后台对一遍。连着来几下只跑一次。没打开同步就什么都不做。"""
    try:
        if _KICK["pending"] or not (cfg()["on"] or db.get_setting(MAP_KEY, None)):
            return False
        _KICK["pending"] = True

        def run():
            try:
                time.sleep(delay)
            finally:
                _KICK["pending"] = False
            sync()
        threading.Thread(target=run, name="todo-dt-sync", daemon=True).start()
        return True
    except Exception:
        return False
