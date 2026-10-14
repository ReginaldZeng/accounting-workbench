# [Change Log] Date: 2026-10-14 | Author: Claude / c | Version: V2.953（后补票转待认证·物流单也由后补单这边做）
# Description: Owner 2026-10-14：「因为这个是后补，我们的付款凭证已经做完了，所以不可能在付款那条线转待认证」——物流请款单的后补票，转待认证也由发票后补单这边触发（审核通过、后补单收齐马上做，不等付款做账 20 分钟一轮）。开关＝真做时接管；分录和保存仍调付款做账那条线的 _post_later（同一把锁、同一份做账记录，那边记了 later3 就不会重复建），不另写一套算法。开关不是真做时不接管，维持那边自己做。
# [Change Log] Date: 2026-10-14 | Author: Claude / c | Version: V2.951（核销暂估·物流请款单说清楚谁来做）
# Description: 用户上线当晚看到物流请款单的后补票写着「这里不重复做」，以为系统不做了。改成把「付款做账」那条线现在的打算原样亮出来：付款凭证做没做、转待认证税额多少、对不对得上、两行分录、是审核通过后自动做还是要去那边手动点（_logistics_plan，只读）。
# [Change Log] Date: 2026-10-14 | Author: Claude / c | Version: V2.950（发票管家·后补票自动转待认证）
# Description: Owner 2026-10-13/14 定：后补单的发票到了、审核通过以后，那张「暂估转待认证」凭证系统在后台自己做
#   （「我只会审核发票，以及确认收到」），审核时把它要核销的暂估亮出来、对不上或找不准的由审核人在这里选。
#   · 找原凭证（只读金蝶）：同一个主体的账簿里，供应商往来（2241）贷方＝付款金额、挂的供应商＝收款方的费用凭证；
#     其中借方有暂估进项税（2221.01.07）的就是要核销的那张。找到唯一一张自动用；多张要人选；找不到不做。
#   · 分录：借 2221.01.06 待认证进项税额（每张票一行，摘要＝发票号＋核销M/N#＋原摘要）／贷 2221.01.07 暂估进项税（挂供应商）
#     ——用物流付款做账那条线已经在线上跑过的同一个内核（kernels.logistics_voucher.later_lines）和同一套保存写法
#     （新建并提交、不审核）。
#   · 这一版只自动做「发票税额＝账上暂估」的；对不上的（税率不同、金额不同）先不自动做，写明差多少、提醒人工处理。
#   · 三档开关（设置里）：关／演练／真做，默认「演练」＝只算不写金蝶，把打算怎么做记下来给人看（演练也不发钉钉）。
#   · 不翻旧账：开「真做」之前审核通过的后补票，系统不自动补做（可能早有人在金蝶手工处理过）；要做在审核弹窗里点「现在做」。
#   · 物流请款单（logistics_payreq 里的）不归这里：那条线自己会做（routers/logistics_voucher._post_later）。
#   状态存 app_settings: inv_later3（{后补单 id: {...}}），不新增表。写金蝶挂 busy.hold（部署重启会等它做完）。
import threading
import time
import traceback
from datetime import datetime, timedelta

from fastapi import APIRouter, Request
from sqlalchemy import select, text
from starlette.concurrency import run_in_threadpool

from core import db
from kernels import invoice_parse as ip
from kernels import invoice_store as S
from routers import invoice as inv

router = APIRouter()

CFG_KEY = "inv_later_voucher"        # {mode, by, at}
STATE_KEY = "inv_later3"             # {后补单 id: {status, msg, picks, vid, vno, ...}}
MODES = ("off", "dry", "on")
MODE_CN = {"off": "关", "dry": "演练", "on": "真做"}
ACCT_WAIT, ACCT_EST = "2221.01.06", "2221.01.07"
LOOK_BACK_DAYS = 100                 # 原凭证从付款单提交日往前找多少天（费用凭证一般和付款单同月或早一个月）
CAND_MAX = 8
RETRY_HOURS = 6                      # 没做成的隔多久再试一次
SKIP_WORDS = ("红冲", "更正", "核销", "冲回", "冲销")
_LOCK = threading.Lock()
_Q, _Q_LOCK = set(), threading.Lock()
_CACHE = {}                          # (账簿, 收款方, 金额, 起始日) → (候选凭证, 时间)


def _lvr():
    """物流付款做账模块（延迟导入：它依赖多，测试里按需打桩）。"""
    from routers import logistics_voucher as LVR
    return LVR


def cfg():
    c = db.get_setting(CFG_KEY, None) or {}
    return {"mode": c.get("mode") if c.get("mode") in MODES else "dry", "by": c.get("by") or "", "at": c.get("at") or ""}


def _state():
    d = db.get_setting(STATE_KEY, None)
    return dict(d) if isinstance(d, dict) else {}


def state_of(lid):
    return dict(_state().get(str(lid)) or {})


def _put(lid, **kw):
    with _LOCK:
        st = _state()
        cur = dict(st.get(str(lid)) or {})
        cur.update(kw)
        st[str(lid)] = cur
        db.set_setting(STATE_KEY, st, inv.SYSTEM_USER)
    return cur


def _is_logistics(inst_id):
    """这张付款单是不是物流请款单（付款做账那条线管的）。"""
    if not inst_id:
        return False
    try:
        with db._engine.connect() as c:
            return bool(c.execute(text("select 1 from logistics_payreq where inst_id=:i"), {"i": inst_id}).first())
    except Exception:
        return False


def _book(l, folder):
    """后补单的公司主体 → (金蝶账簿名, 账簿编码)。主体档案里按全称找；找不到编码回空。"""
    name = str((l or {}).get("company") or (folder or {}).get("company") or "").strip()
    for o in db.list_orgs() or []:
        if name and name == str(o.get("full_name") or "").strip():
            return name, str(o.get("book_code") or "")
    return name, ""


def _same_name(a, b):
    x, y = ip.normalize_name(a), ip.normalize_name(b)
    return bool(x and y) and (x == y or x in y or y in x)


def _bills(lid):
    return [i for i in S.items_of_later(inv.E(), lid) if i.get("kind") in ("invoice", "receipt") and i.get("review") != "void"
            and not (i.get("flags_json") or {}).get("dup")]


def _inv_in(bills):
    """后补单上的票 → 内核要的发票清单。能抵扣的＝专票、旅客运输票，且审核时没勾成不可抵；其余按 0 税额。"""
    out = []
    for i in bills:
        if i.get("kind") != "invoice":
            continue
        ded = (i.get("inv_type") or "") in ("special", "travel") and i.get("deductible") != 0
        out.append({"id": i["id"], "number": str(i.get("number") or ""), "rate": i.get("tax_rate") or "",
                    "gross": float(inv._share(i)), "tax": round(float(i.get("tax") or 0), 2) if ded else 0.0, "deduct": ded})
    return out


def _vkey(v):
    return "%d-%02d-%s" % (int(v["year"]), int(v["month"]), v["vno"])


def _find(book, payee, amount, since):
    """金蝶里这张付款单当时的费用凭证候选（只读）：账簿 book、since 以后、供应商往来贷方＝amount、挂的供应商＝payee。
    → [内核的 acc_voucher（整张分录）]，都按「直接做账的费用凭证」处理（摘要原样引用）。10 分钟缓存。"""
    k = (book, payee, round(float(amount), 2), since)
    hit = _CACHE.get(k)
    if hit and time.time() - hit[1] < 600:
        return hit[0]
    LVR = _lvr()
    kc, s_ = LVR.kc, LVR._s
    s, conf = LVR._kd()
    bk = book.replace("'", "")
    heads = kc._query(s, conf, "GL_VOUCHER", list(LVR._F) + [("FYear", "年"), ("FPeriod", "期")],
                      "FACCOUNTBOOKID.FName='%s' and FDate>='%s' and FAccountID.FNumber like '2241%%' and FCREDIT=%.2f" % (bk, since, float(amount)))
    keys = []
    for h in heads:
        if any(w in s_(h["摘要"]) for w in SKIP_WORDS) or not _same_name(h["供应商"], payee):
            continue
        key = (int(h["年"]), int(h["期"]), s_(h["号"]))
        if key not in keys:
            keys.append(key)
    out = []
    for y, m, vno in keys[:CAND_MAX]:
        rows = kc._query(s, conf, "GL_VOUCHER", LVR._F, "FACCOUNTBOOKID.FName='%s' and FYear=%d and FPeriod=%d and FVOUCHERGROUPNO='%s'" % (
            bk, y, m, vno.replace("'", "")))
        ls = [{"acct": s_(r["科目"]), "acct_name": s_(r["科目名"]), "dr": float(r["借"] or 0), "cr": float(r["贷"] or 0),
               "expl": s_(r["摘要"]), "sup_code": s_(r["供应商码"]), "sup_name": s_(r["供应商"]), "dept_code": s_(r["部门码"]),
               "dept": s_(r["部门"]), "fee_code": s_(r["费用码"]), "fee": s_(r["费用"]), "biz_code": s_(r["分类码"]),
               "biz": s_(r["分类"]), "proj_code": s_(r["项目码"]), "proj": s_(r["项目"]), "sup_grp": s_(r["供应商分组"])} for r in rows]
        v = LVR.LV.acc_voucher(vno, ls, y, m)
        v["direct"] = True
        out.append(v)
    _CACHE[k] = (out, time.time())
    return out


def _cand_view(v, picked):
    return {"key": _vkey(v), "year": v["year"], "month": v["month"], "vno": v["vno"], "expl": v.get("expl") or "",
            "gross": v.get("gross") or 0.0, "net": v.get("net") or 0.0, "tax": v.get("tax") or 0.0, "tax06": v.get("tax06") or 0.0,
            "picked": _vkey(v) in picked}


def plan(lid):
    """这张后补单的「暂估转待认证」打算怎么做（只读金蝶，不写）→ {ok, kind, why, book, cands, src, lines, taxInv, taxEst}。
    kind：exact 对得上可以做 / none 不用做（原凭证没有暂估）/ diff 税额对不上 / multi 多张要人选 / nosrc 没找到原凭证 /
    logistics 归物流线 / nobook 主体没配账簿 / noinv 还没发票 / split 有拆分票 / error 读金蝶出错。"""
    e = inv.E()
    l = S.later_get(e, lid)
    out = {"ok": False, "kind": "", "why": "", "book": "", "cands": [], "src": [], "lines": [], "taxInv": 0.0, "taxEst": 0.0}
    if not l:
        return dict(out, kind="gone", why="后补单不存在")
    if _is_logistics(l.get("inst_id")):
        return dict(out, **_logistics_plan(l.get("inst_id")))
    folder = S.folder_get(e, l.get("folder_id")) or {}
    book, code = _book(l, folder)
    out["book"] = book
    if not code:
        return dict(out, kind="nobook", why="主体档案里没有「%s」的账簿编码，系统不知道记到哪个账簿" % (book or "（付款单上没写公司主体）"))
    bills = _bills(lid)
    invs = _inv_in(bills)
    if not invs:
        return dict(out, kind="noinv", why="后补单上还没有发票")
    if any(i.get("split") for i in bills):
        return dict(out, kind="split", why="后补单上有拆分到几张单的发票，各该转多少系统定不了，要人工处理")
    pay = l.get("pay_amount") if l.get("pay_amount") is not None else folder.get("amount")
    if pay is None:
        return dict(out, kind="nosrc", why="付款单上读不出付款金额，没法到金蝶找原凭证")
    bid = str(l.get("business_id") or folder.get("business_id") or "")      # 钉钉审批编号开头八位就是提交日
    base = ("%s-%s-%s" % (bid[:4], bid[4:6], bid[6:8])) if bid[:8].isdigit() else \
        str(folder.get("created_at") or l.get("created_at") or inv.now_s())[:10]
    try:
        since = (datetime.strptime(base, "%Y-%m-%d") - timedelta(days=LOOK_BACK_DAYS)).strftime("%Y-%m-%d")
    except ValueError:
        since = (datetime.now() - timedelta(days=LOOK_BACK_DAYS * 2)).strftime("%Y-%m-%d")
    payee = l.get("payee_name") or folder.get("payee_name") or ""
    try:
        cands = _find(book, payee, float(pay), since)
    except Exception as ex:
        return dict(out, kind="error", why="读金蝶出错：%s" % str(ex)[:160])
    picks = [str(x) for x in (state_of(lid).get("picks") or [])]
    it = round(sum(i["tax"] for i in invs), 2)
    out.update(cands=[_cand_view(v, picks) for v in cands], taxInv=it)
    where = "%s 账簿里 %s 以后、供应商往来贷方 %s、挂「%s」的费用凭证" % (book, since, inv._money_str(pay), payee)
    if picks:
        src = [v for v in cands if _vkey(v) in picks]
        if len(src) != len(picks):
            return dict(out, kind="nosrc", why="之前选定的原凭证在金蝶里找不到了（可能被改过），请重新选")
    else:
        good = [v for v in cands if v.get("tax")]
        if not cands:
            return dict(out, kind="nosrc", why="金蝶里没找到这张付款单当时的费用凭证（找的是：%s）" % where)
        if not good:
            how = "税当时就挂在了待认证" if all(v.get("tax06") for v in cands) else "当时全额进了费用"
            return dict(out, ok=True, kind="none", why="原凭证 %s 上没有暂估进项税（%s），不用转" % ("、".join(_ref(v) for v in cands), how),
                        src=[_vkey(v) for v in cands])
        if len(good) > 1:
            return dict(out, kind="multi", why="金蝶里有 %d 张都对得上（%s），请选是哪一张" % (len(good), "、".join(_ref(v) for v in good)))
        src = good
    at = round(sum(float(v.get("tax") or 0) for v in src), 2)
    out.update(src=[_vkey(v) for v in src], taxEst=at)
    if not at:
        return dict(out, ok=True, kind="none", why="选定的原凭证 %s 上没有暂估进项税，不用转" % "、".join(_ref(v) for v in src))
    if abs(it - at) >= 0.005:
        return dict(out, kind="diff", why="发票税额 %s ≠ 账上暂估进项税 %s（差 %s）：这一版系统只自动做对得上的，这张请人工处理（红冲更正后再转待认证）" % (
            inv._money_str(it), inv._money_str(at), inv._money_str(abs(round(it - at, 2)))))
    LV = _lvr().LV
    lines, d = LV.later_lines(src, invs, datetime.now().year, payee)
    dr, cr = LV.balance(lines)
    if not lines or abs(dr - cr) >= 0.005 or abs(d) >= 0.005:
        return dict(out, kind="diff", why="按发票和原凭证拼出来的分录借贷不平（借 %s／贷 %s），要人工处理" % (inv._money_str(dr), inv._money_str(cr)))
    return dict(out, ok=True, kind="exact", lines=lines,
                why="发票税额和账上暂估进项税都是 %s，对得上：借待认证进项税额／贷暂估进项税（核销 %s）" % (inv._money_str(it), "、".join(_ref(v) for v in src)))


def _logistics_plan(inst):
    """物流请款单的后补票：转待认证归「付款做账」那条线做（routers/logistics_voucher，开关也是那边的）。
    把那条线现在的打算原样亮出来（只读，要读金蝶）→ {kind:'logistics', ok, why, lines?, taxInv?, taxEst?}。
    ok＝审核通过后它会自己做（或已经做了／不用做）；False＝它现在不会自动做，要人去付款做账页处理。"""
    mine = cfg()["mode"] == "on"          # 开关是真做：这张也由发票后补单这边触发（分录按付款做账那条线的记录和算法算）
    head = "这张是物流请款单，转待认证由发票后补单这边做、分录按「付款做账」那条线的记录算：" if mine else \
        "这张是物流请款单，转待认证由「付款做账」那条线做："
    out = {"kind": "logistics", "ok": False, "why": "", "by": "later" if mine else "logistics"}
    try:
        LVR = _lvr()
        c = LVR._auto_cfg()
        auto = c.get("mode") == "on" and "later" in (c.get("kinds") or [])
        rec = (db.get_setting(LVR._POSTED_KEY, None) or {}).get(inst)
        rec = rec if isinstance(rec, dict) else {}
        if not rec.get("vno"):
            return dict(out, why=head + "那边这张单的付款凭证还没做。付款凭证做了、这张票审核通过以后才会转待认证")
        if not rec.get("tax_later"):
            return dict(out, ok=True, why=head + "付款凭证 记-%s 当时不是按「发票后补」做的，账上没有挂着的暂估进项税，不用转" % rec.get("vno"))
        l3d = rec.get("later3") or {}
        if l3d.get("vno"):
            return dict(out, ok=True, why=head + "已经建好了，记-%s（%s）%s" % (
                l3d["vno"], l3d.get("date") or "", "" if l3d.get("submitted", True) else "，但没提交上，请到金蝶手动提交"))
        d, code = LVR._preview_data(inst)
        if code != 200:
            return dict(out, why=head + "现在读不出它的打算（%s），过一会儿再打开看" % str(d.get("msg") or "读取失败")[:120])
        l3 = d.get("later3") or {}
        if not (l3.get("lines") and l3.get("ok")) or l3.get("gross_diff"):
            why = l3.get("msg") or ("发票和付款金额不一样" if l3.get("gross_diff") else "发票税额和挂着的暂估进项税对不上")
            return dict(out, why=head + "但现在不会自动做——%s。请到「付款做账」页处理" % why)
        tax = round(float(l3.get("tax") if l3.get("tax") is not None else l3.get("dr") or 0), 2)
        how = ("发票和账上对不上，会先整笔红冲更正 %d 张，再转待认证 %s" % (l3["redo"], inv._money_str(tax))) if l3.get("redo") else \
            ("发票和账上对得上，转待认证税额 %s" % inv._money_str(tax))
        if mine:
            return dict(out, ok=True, why=head + "你审核通过、后补单收齐后，这边马上建凭证并提交、不审核。" + how,
                        lines=l3["lines"], taxInv=tax, taxEst=tax)
        when = "你审核通过后，那边下一轮（每 20 分钟一轮）自动建凭证并提交、不审核" if auto else \
            "不过那边的自动做账现在没开到「真做」（或没把发票后补放进自动范围），审核通过后要到「付款做账」页手动点"
        return dict(out, ok=auto, why=head + when + "。" + how, lines=l3["lines"], taxInv=tax, taxEst=tax)
    except Exception as ex:
        return dict(out, why=head + "现在读不出它的打算（%s），过一会儿再打开看" % str(ex)[:120])


def _run_logistics(l, lid, bills, p, who, mode):
    """物流请款单的后补票：开关＝真做时由发票后补单这边触发转待认证（Owner 2026-10-14：付款凭证做完，那条线的事就结束了）。
    分录、保存用付款做账那条线的 _post_later——同一把锁、同一份做账记录（那边记了 later3 就不会重复建），不另写一套算法。
    开关不是真做：不接管，那条线自己的自动做账照旧。"""
    inst = l.get("inst_id")
    if mode != "on":
        _put(lid, status="skip", kind="logistics", msg=p["why"], at=inv.now_s())
        return {"ok": True, "status": "skip", "msg": p["why"]}
    import busy
    LVR = _lvr()

    def rec_():
        r = (db.get_setting(LVR._POSTED_KEY, None) or {}).get(inst)
        return r if isinstance(r, dict) else {}
    rec = rec_()
    if not rec.get("vno"):                                  # 付款凭证还没做：等着，下一轮再看
        return {"ok": False, "status": "wait", "msg": p["why"]}
    if not rec.get("tax_later"):
        _put(lid, status="none", kind="logistics", msg=p["why"], at=inv.now_s())
        return {"ok": True, "status": "none", "msg": p["why"]}
    if not (rec.get("later3") or {}).get("vno"):
        if not p["ok"]:
            _put(lid, status="manual", kind="logistics", msg=p["why"], at=inv.now_s())
            _tell(l, lid, p["why"])
            return {"ok": False, "status": "manual", "msg": p["why"]}
        lk = LVR._POST_LOCK.setdefault(inst, threading.Lock())
        if not lk.acquire(blocking=False):
            return {"ok": False, "status": "wait", "msg": "这张单正在做账，稍后再看"}
        try:
            LVR._held("发票后补·暂估转待认证凭证", LVR._post_later, inst, who)
        except busy.Draining as ex:
            return {"ok": False, "status": "wait", "msg": str(ex)}
        except Exception as ex:
            why = "建暂估转待认证凭证没成：%s。可以到「付款做账」页看这张单，或在这里点「现在做」重试" % str(ex)[:200]
            _put(lid, status="manual", kind="logistics", msg=why, at=inv.now_s())
            _tell(l, lid, why)
            return {"ok": False, "status": "manual", "msg": why}
        finally:
            lk.release()
        rec = rec_()
    l3 = rec.get("later3") or {}
    vno, date, book = str(l3.get("vno") or ""), str(l3.get("date") or ""), str(l3.get("book") or "")
    if not vno:
        why = "凭证建了但没读到凭证号，请到「付款做账」页看这张单"
        _put(lid, status="manual", kind="logistics", msg=why, at=inv.now_s())
        return {"ok": False, "status": "manual", "msg": why}
    try:
        y, m = int(date[:4]), int(date[5:7])
    except ValueError:
        y, m = datetime.now().year, datetime.now().month
    sub_ok = l3.get("submitted", True) is not False
    msg = "已新建暂估转待认证凭证 %s %d年%d月 记-%s（%s），%s" % (
        book, y, m, vno, date, "已提交、等人在金蝶审核" if sub_ok else "但提交失败：%s（凭证已保存，请到金蝶手动提交）" % (l3.get("submit_err") or ""))
    st = _put(lid, status="done", kind="logistics", msg=msg, vid=l3.get("vid"), vno=vno, book=book, date=date, vyear=y, vperiod=m,
              tax=l3.get("tax"), submitted=sub_ok, at=inv.now_s(), by=l3.get("by") or who)
    one = {"book": book, "period": "%04d-%02d" % (y, m), "number": "记-%s" % vno}
    for i in bills:
        if i.get("kind") != "invoice":
            continue
        fl = dict((S.item_get(inv.E(), i["id"]) or {}).get("flags_json") or {})
        old = fl.get("_bookkeeping") or {}
        vs = [v for v in (old.get("vouchers") or []) if v != one] if old.get("status") == "booked" else []
        fl["_bookkeeping"] = {"status": "booked", "vouchers": vs + [one], "source": "auto", "by": "系统自动转待认证", "at": inv.now_s()}
        S.item_update(inv.E(), i["id"], flags_json=fl)
        inv.log(who, "暂估转待认证凭证", l.get("folder_id"), i["id"], lid, {"voucher": "记-%s" % vno, "period": one["period"], "book": book, "via": "物流付款做账的算法"})
    try:
        import todo_scenes
        todo_scenes.later_voucher_touch(lid, st, l, who)
    except Exception as ex:
        print("[发票管家] 转待认证凭证的首页待办没记上：%s" % ex)
    return {"ok": True, "status": "done", "msg": msg, "vno": vno}


def _ref(v):
    if isinstance(v, str):
        y, m, no = v.split("-", 2)
        return "%d年%d月 记-%s" % (int(y), int(m), no)
    return "%d年%d月 记-%s" % (int(v["year"]), int(v["month"]), v["vno"])


def _already(bills):
    """金蝶里是不是已经有人把这几张票做了（凭证摘要里带发票号）→ 全都有 → [凭证…]，否则 []。只读。"""
    from routers import invoice_vouchers as iv
    todo = [i for i in bills if i.get("kind") == "invoice" and (i.get("inv_type") or "") in ("special", "travel") and i.get("deductible") != 0]
    nums = [str(i.get("number") or "") for i in todo if i.get("number")]
    if not todo or len(nums) != len(todo):
        return []
    rows = iv.fetch_rows(nums)
    per = [iv.match_vouchers(i, rows) for i in todo]
    if not all(per):
        return []
    iv._sync_items(todo, rows)
    out = []
    for vs in per:
        out += [v for v in vs if v not in out]
    return out


def _tell(l, lid, why):
    """没自动做成：给审核这张票的人发一条钉钉（同一个原因只发一次）。"""
    st = state_of(lid)
    if st.get("told") == why:
        return
    who = next((i.get("review_by") for i in _bills(lid) if i.get("review_by")), "") or l.get("receiver") or ""
    uid = inv.dt_uid_of(who) if who else ""
    msg = "【核算工作台·发票管家】后补单 #%s（%s，%s）的发票审核通过了，但「暂估转待认证」凭证系统没自动做：%s。请到金蝶手工处理 → %s" % (
        lid, l.get("payee_name") or "", inv._money_str(l.get("pay_amount")), why, inv.portal_link("/#/invlater?id=%s" % lid))
    sent = inv.notify_dt([uid] if uid else [], msg)
    _put(lid, told=why, toldTo=who, toldSent=bool(sent.get("sent")))


def run_one(lid, user=None, manual=False):
    """一张后补单：发票都审核通过、后补单收齐 → 做「暂估转待认证」。→ {ok, status, msg}。只在线程里调（读写金蝶、可能发钉钉）。
    status：wait 还不到时候 / skip 不归这里或开关关着 / none 不用做 / dry 演练（没写金蝶）/ done 做了 / manual 要人工 /
    old 开真做之前审核的（不自动补做）。manual=True＝人点的「现在做」：不受「不翻旧账」限制。"""
    lid = int(lid)
    mode = cfg()["mode"]
    who = inv._uname(user) if user else "系统自动"
    if mode == "off":
        return {"ok": False, "status": "skip", "msg": "「后补票自动转待认证」是关着的"}
    e = inv.E()
    l = S.later_get(e, lid)
    if not l:
        return {"ok": False, "status": "skip", "msg": "后补单不存在"}
    bills = _bills(lid)
    if (l.get("status") or "") != "done" or not bills or any(i.get("review") != "approved" for i in bills):
        return {"ok": False, "status": "wait", "msg": "要等后补单收齐、上面的票都审核通过"}
    st = state_of(lid)
    if st.get("status") in ("done", "none") or (st.get("status") == "skip" and (st.get("kind") != "logistics" or mode != "on")):
        return {"ok": True, "status": st["status"], "msg": st.get("msg") or ""}
    if st.get("status") == "posting":
        why = "上次建这张凭证做到一半中断了：请到金蝶看有没有摘要带发票号 %s 的凭证，有就不用再做，没有再手工做" % (
            "、".join(str(i.get("number") or "") for i in bills if i.get("number"))[:80])
        _put(lid, status="manual", kind="half", msg=why, at=inv.now_s())
        _tell(l, lid, why)
        return {"ok": False, "status": "manual", "msg": why}
    if mode == "on" and not manual and max(str(i.get("review_at") or "") for i in bills) < str(cfg()["at"] or ""):
        why = "这张后补单的票是开「真做」之前审核通过的，系统不自动补做（怕和已经手工做过的重复）；要做请在审核弹窗「核销暂估」里点「现在做」"
        _put(lid, status="old", kind="old", msg=why, at=inv.now_s())
        return {"ok": False, "status": "old", "msg": why}
    p = plan(lid)
    if p["kind"] == "logistics":
        return _run_logistics(l, lid, bills, p, who, mode)
    if p["kind"] == "none":
        _put(lid, status="none", kind="none", msg=p["why"], src=p["src"], at=inv.now_s())
        return {"ok": True, "status": "none", "msg": p["why"]}
    if p["kind"] == "error":                        # 金蝶一时读不到：不记成要人工，下一轮再试
        return {"ok": False, "status": "wait", "msg": p["why"]}
    try:
        had = _already(bills)
    except Exception as ex:
        return {"ok": False, "status": "wait", "msg": "读金蝶出错：%s" % str(ex)[:160]}
    if had:
        msg = "金蝶里已经有人做了：%s（摘要里带这几张发票的号），系统不再做" % "、".join("%s %s" % (v.get("period"), v.get("number")) for v in had)[:200]
        _put(lid, status="done", kind="byhand", msg=msg, vouchers=had, at=inv.now_s(), by="人工")
        return {"ok": True, "status": "done", "msg": msg}
    if not p["ok"]:
        _put(lid, status="manual", kind=p["kind"], msg=p["why"], src=p["src"], taxInv=p["taxInv"], taxEst=p["taxEst"], at=inv.now_s())
        if mode == "on":                            # 演练不打扰人：原因只记在后补票上
            _tell(l, lid, p["why"])
        return {"ok": False, "status": "manual", "msg": p["why"]}
    brief = ["%s %s %s" % ("借" if x["dr"] else "贷", x["acct"], inv._money_str(x["dr"] or x["cr"])) for x in p["lines"]]
    if mode == "dry":
        msg = "演练（没写金蝶）：%s。会新建一张凭证：%s" % (p["why"], "；".join(brief))
        _put(lid, status="dry", kind="exact", msg=msg, src=p["src"], taxInv=p["taxInv"], taxEst=p["taxEst"], at=inv.now_s())
        return {"ok": True, "status": "dry", "msg": msg}
    return _post(l, lid, bills, p, who)


def _post(l, lid, bills, p, who):
    """真做：新建凭证 → 读回凭证号 → 提交（不审核）→ 记到票上、首页待办。保存前先落「正在做」，中断了下次不重复建。"""
    import busy
    LVR = _lvr()
    kc = LVR.kc
    folder = S.folder_get(inv.E(), l.get("folder_id")) or {}
    book, code = _book(l, folder)
    _put(lid, status="posting", at=inv.now_s(), by=who, src=p["src"])
    try:
        with busy.hold("发票后补·暂估转待认证凭证"):
            s, conf = kc.login()
            date, y, m = LVR._xred_date(book, datetime.now().strftime("%Y-%m-%d"), s, conf)
            ents = []
            for x in p["lines"]:
                en = dict(LVR._KD_BASE, FEXPLANATION=x["expl"], FACCOUNTID={"FNumber": x["acct"]}, FDEBIT=x["dr"], FCREDIT=x["cr"])
                dd = LVR._kd_dims(x)
                if dd:
                    en["FDetailID"] = dd
                ents.append(en)
            r = kc.save_voucher({"FACCOUNTBOOKID": {"FNumber": code}, "FVOUCHERGROUPID": {"FNumber": "PRE001"}, "FEntity": ents,
                                 "FDate": date, "FYear": y, "FPeriod": m}, s, conf)
            info = kc.view_voucher(r["id"], s, conf)
            sub_err = ""
            try:
                kc.submit_bill("GL_VOUCHER", r["id"], s, conf)
            except Exception as ex:
                sub_err = str(ex)[:160]
    except busy.Draining as ex:                     # 系统正准备重启：还没开始写，退回去下一轮再做
        _put(lid, status="", at=inv.now_s())
        return {"ok": False, "status": "wait", "msg": str(ex)}
    except Exception as ex:
        why = "建凭证时出错：%s。请到金蝶看有没有建出半张（摘要带发票号），没有的话可以在后补单上点「现在做」重试" % str(ex)[:160]
        _put(lid, status="manual", kind="error", msg=why, at=inv.now_s(), trace=traceback.format_exc()[-600:])
        _tell(l, lid, why)
        return {"ok": False, "status": "manual", "msg": why}
    vno = str(info.get("vno") or "")
    msg = "已新建暂估转待认证凭证 %s %d年%d月 记-%s（%s），%s" % (
        book, y, m, vno, date, "已提交、等人在金蝶审核" if not sub_err else "但提交失败：%s（凭证已保存，请到金蝶手动提交）" % sub_err)
    rec = _put(lid, status="done", kind="exact", msg=msg, vid=r["id"], vno=vno, billno=r.get("billno") or "", book=book, date=date,
               vyear=y, vperiod=m, tax=p["taxEst"], submitted=not sub_err, submit_err=sub_err, at=inv.now_s(), by=who)
    bk = {"status": "booked", "vouchers": [{"book": book, "period": "%04d-%02d" % (y, m), "number": "记-%s" % vno}],
          "source": "auto", "by": "系统自动转待认证", "at": inv.now_s()}
    for i in bills:
        if i.get("kind") != "invoice":
            continue
        fl = dict((S.item_get(inv.E(), i["id"]) or {}).get("flags_json") or {})
        old = fl.get("_bookkeeping") or {}
        vs = [v for v in (old.get("vouchers") or []) if v not in bk["vouchers"]] if old.get("status") == "booked" else []
        fl["_bookkeeping"] = dict(bk, vouchers=vs + bk["vouchers"])
        S.item_update(inv.E(), i["id"], flags_json=fl)
        inv.log(who, "暂估转待认证凭证", l.get("folder_id"), i["id"], lid, {"voucher": "记-%s" % vno, "period": "%04d-%02d" % (y, m), "book": book})
    inv.audit(who, "发票后补-暂估转待认证凭证", "记-%s" % vno, "后补单#%s %s；%s" % (lid, l.get("payee_name") or "", msg))
    try:
        import todo_scenes
        todo_scenes.later_voucher_touch(lid, rec, l, who)
    except Exception as ex:
        print("[发票管家] 转待认证凭证的首页待办没记上：%s" % ex)
    return {"ok": True, "status": "done", "msg": msg, "vno": vno}


# ───────────────────────── 排队 / 定时兜底 ─────────────────────────

def kick(lids):
    """后补票审核通过了：排队让后台线程去做（发票管家的后台线程每 3 秒看一次）。"""
    with _Q_LOCK:
        _Q.update(int(x) for x in (lids or []) if x)
    inv.wake()


def drain(n=2):
    """后台线程取几张来做 → 做了几张。"""
    with _Q_LOCK:
        ids = sorted(_Q)[:n]
        _Q.difference_update(ids)
    for lid in ids:
        try:
            run_one(lid)
        except Exception:
            print("[发票管家] 后补单#%s 转待认证出错：%s" % (lid, traceback.format_exc()[-800:]))
    return len(ids)


def run_due(limit=10):
    """定时兜底：已收齐、票都审核通过、还没做完的后补单再过一遍（排队漏了的、开关刚从演练切到真做的、上次金蝶读不到的）。
    要人工的隔 RETRY_HOURS 小时再看一次（人可能已经在金蝶处理了）。→ 这轮看了几张。"""
    mode = cfg()["mode"]
    if mode == "off":
        return 0
    st = _state()
    rows = S._all(inv.E(), select(S.LATER).where(S.LATER.c.status == "done").order_by(S.LATER.c.id.desc()).limit(200))
    edge = (datetime.now() - timedelta(hours=RETRY_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    n = 0
    for l in rows:
        cur = st.get(str(l["id"])) or {}
        s_ = cur.get("status") or ""
        if s_ in ("done", "none", "skip", "old") or (s_ == "dry" and mode != "on") or (s_ == "manual" and str(cur.get("at") or "") > edge):
            continue
        bills = _bills(l["id"])
        if not bills or any(i.get("review") != "approved" for i in bills):
            continue                                 # 票还没审完：不算这一轮的名额（免得一直排在前面把后面的挤掉）
        if s_ == "manual":
            _put(l["id"], status="")                 # 到点了重新算一遍
        n += 1
        try:
            run_one(l["id"])
        except Exception:
            print("[发票管家] 后补单#%s 转待认证出错：%s" % (l["id"], traceback.format_exc()[-800:]))
        if n >= limit:
            break
    return n


# ───────────────────────── 接口 ─────────────────────────

def _view(lid):
    l = S.later_get(inv.E(), lid)
    if not l:
        return None
    p = plan(lid)
    bills = _bills(lid)
    ready = (l.get("status") or "") == "done" and bool(bills) and all(i.get("review") == "approved" for i in bills)
    lines = [{"dc": "借" if x["dr"] else "贷", "acct": x["acct"], "acctName": x.get("acct_name") or "", "amount": x["dr"] or x["cr"],
              "expl": x["expl"], "supplier": (x.get("dims") or {}).get("sup_name") or ""} for x in p.get("lines") or []]
    st = state_of(lid)
    return {"ok": True, "laterId": lid, "mode": cfg()["mode"], "modeText": MODE_CN[cfg()["mode"]], "ready": ready,
            "plan": {k: p.get(k) for k in ("ok", "kind", "why", "book", "cands", "src", "taxInv", "taxEst", "by")}, "lines": lines,
            "state": {k: st.get(k) for k in ("status", "kind", "msg", "vno", "vyear", "vperiod", "book", "date", "at", "by", "submitted", "picks")}}


@router.get("/api/inv/later/{lid}/voucher-plan")
async def later_voucher_plan(lid: int, request: Request):
    """审核弹窗「核销暂估」：这张后补单的票要核销金蝶里哪张凭证的暂估、对不对得上、系统打算怎么做（要读金蝶，10 分钟缓存）。"""
    u, bad = inv.need(request)
    if bad:
        return bad
    if not (inv.can(u, inv.ENTER_AUDIT) or inv.can(u, inv.ENTER_LATER)):
        return inv.err("无权限：要有发票审核或后补池的权限", 403)
    v = await run_in_threadpool(_view, lid)
    return v or inv.err("后补单不存在", 404)


@router.post("/api/inv/later/{lid}/voucher-source")
async def later_voucher_source(lid: int, request: Request):
    """审核人选定「核销哪张原凭证的暂估」（picks＝候选的 key 列表；空＝交回系统自己找）。已经建过凭证的不能再改。"""
    u, bad = inv.need(request, inv.ENTER_AUDIT, inv.CAP_AUDIT)
    if bad:
        return bad
    body = await inv.body_json(request)
    picks = [str(x)[:40] for x in (body.get("picks") or []) if x][:CAND_MAX]

    def run():
        if not S.later_get(inv.E(), lid):
            return None
        if state_of(lid).get("status") in ("done", "posting"):
            return "done"
        _put(lid, picks=picks, pickedBy=u["name"], pickedAt=inv.now_s(), status="", msg="", told="")
        inv.log(u, "选定核销的暂估凭证", (S.later_get(inv.E(), lid) or {}).get("folder_id"), None, lid, {"picks": picks})
        return _view(lid)
    v = await run_in_threadpool(run)
    if v is None:
        return inv.err("后补单不存在", 404)
    if v == "done":
        return inv.err("这张后补单的转待认证凭证已经建过了，不能再改核销哪张", 400)
    return v


@router.post("/api/inv/later/{lid}/voucher-run")
async def later_voucher_run(lid: int, request: Request):
    """后补单上「现在做」：不等后台，马上按当前开关做一次（演练＝只算不写；真做＝写金蝶）。要人工的也可以点它重试。"""
    u, bad = inv.need(request, inv.ENTER_AUDIT, inv.CAP_AUDIT)
    if bad:
        return bad

    def run():
        if state_of(lid).get("status") in ("manual", "dry", "old"):
            _put(lid, status="", told="")
        r = run_one(lid, u, manual=True)
        return dict(r, view=_view(lid))
    r = await run_in_threadpool(run)
    if r.get("view") is None:
        return inv.err("后补单不存在", 404)
    return {"ok": True, "status": r.get("status"), "msg": r.get("msg") or "", **{k: r["view"][k] for k in ("mode", "modeText", "ready", "plan", "lines", "state")}}


@router.get("/api/inv/later-voucher/config")
async def later_voucher_config(request: Request):
    u, bad = inv.need(request, None, inv.CAP_CONFIG)
    if bad:
        return bad
    st = _state()
    cnt = {}
    for v in st.values():
        cnt[v.get("status") or ""] = cnt.get(v.get("status") or "", 0) + 1
    return {"ok": True, **cfg(), "modeText": MODE_CN[cfg()["mode"]], "count": {k: cnt.get(k, 0) for k in ("done", "dry", "manual", "none")}}


@router.post("/api/inv/later-voucher/config")
async def later_voucher_config_set(request: Request):
    """改开关。切到「真做」只有主管理员能点（系统会往金蝶新建凭证）。"""
    u, bad = inv.need(request, None, inv.CAP_CONFIG)
    if bad:
        return bad
    body = await inv.body_json(request)
    mode = body.get("mode")
    if mode not in MODES:
        return inv.err("开关只能是：关 / 演练 / 真做", 400)
    old = cfg()
    if mode == "on" and old["mode"] != "on" and not db.is_super(u):
        return inv.err("切到「真做」系统会往金蝶新建凭证，只有主管理员能开", 403)
    db.set_setting(CFG_KEY, {"mode": mode, "by": u["name"], "at": inv.now_s()}, u["name"])
    inv.audit(u, "发票后补-自动转待认证开关", "%s → %s" % (MODE_CN[old["mode"]], MODE_CN[mode]), "")
    return {"ok": True, **cfg(), "modeText": MODE_CN[mode]}
