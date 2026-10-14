# [Change Log] Date: 2026-10-14 | Author: Claude Opus 5.5 | Version: V2.960（发票管家·对纸质票）
# Description: 用户 2026-10-14 定：月末准备认证时有一堆票夹，要确定手上的纸质票和选中的票夹是不是一样的。
#   选中几个票夹开一次「点验」，把纸质票连续扫一遍（电脑扫码枪、手机都行，可以同时扫）：扫到的那一行记下来（页面上变绿），
#   没扫到的不动；同一张又扫了＝重复；不在选中票夹里的 / 系统里没有的只提醒，不登记也不挪。手机上按结果念「确认／重复／不在这里／没有这张」。
#   「扫到」按这一次点验单独记（inv_check / inv_check_hit）——不能只看纸质件已到：审核时就记了已到，月末再对会一上来全是到了。
#   扫到的票顺手把纸质件记为已到（原来没记的才记）。一个人同时只有一次点验在对（手机扫的才知道记到哪一次）。
#   入口：收票工作台、发票审核都能开（有其中一个页面就行）；只有开这次点验的人能往里扫。
import re

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy import select, and_
from starlette.concurrency import run_in_threadpool

from kernels import invoice_parse as ip
from kernels import invoice_store as S
from kernels import invoice_excel as X
from routers import invoice as inv

router = APIRouter()
BILL_KINDS = ("invoice", "receipt")
MAX_FOLDERS = 300
SAY = {"ok": "确认", "dup": "重复", "other": "不在这里", "none": "没有这张", "unknown": "不是发票码"}
TICKLIST_KIND = "ticklist"


def _list_of(ck):
    """这次点验对着的认证清单（没有 → None）。"""
    if not ck.get("list_id"):
        return None
    b = S.batch_get(inv.E(), ck["list_id"])
    return b if b and b.get("kind") == TICKLIST_KIND else None


def _list_items(b):
    """认证清单里的票（没作废、没移除的）→ [行]，按凭证、号码排：纸质票按凭证顺序码放。"""
    sm = b.get("summary_json") if isinstance(b.get("summary_json"), dict) else {}
    rows = [i for i in S.items_get(inv.E(), [int(x) for x in sm.get("itemIds") or []]).values()
            if i.get("kind") in BILL_KINDS and i.get("status") != "removed" and i.get("review") != "void"]
    return sorted(rows, key=lambda i: (inv.book_month_of(i) or "9999", _vno(i), i.get("number") or ""))


def _vno(it):
    """排序用的凭证号（数字在前）。"""
    fl = it.get("flags_json") if isinstance(it.get("flags_json"), dict) else {}
    vs = (fl.get("_bookkeeping") or {}).get("vouchers") or []
    m = re.search(r"(\d+)$", str((vs[0] if vs else {}).get("number") or ""))
    return int(m.group(1)) if m else 10 ** 9


def _month_say(ym):
    """'2026-09' → 「9 月的」（给人念的：这张票是哪个月做账的）。"""
    return "%d 月的" % int(ym[5:7]) if re.fullmatch(r"\d{4}-\d{2}", ym or "") else ""
VIA_CN = {"scanner": "扫码枪", "phone": "手机", "manual": "手工勾"}
EXPORT_COLUMNS = ["票夹", "收款方", "审批编号", "归谁审", "票种", "发票代码", "发票号码", "开票日期", "销方名称", "价税合计",
                  "这次点验", "扫到的时间", "谁扫的"]


def _type_label(r):
    if r.get("kind") != "invoice":
        return X.KIND_LABELS.get(r.get("kind"), r.get("kind") or "")
    return r.get("type_label") or X.INV_TYPE_LABELS.get(r.get("inv_type") or "", r.get("inv_type") or "")


def _folders(ids):
    e = inv.E()
    out = []
    for i in ids:
        f = S.folder_get(e, i)
        if f:
            out.append(f)
    return out


def _items(folder_ids):
    """选中票夹里的发票/收据（没作废、没移除）→ {folder_id: [行]}。"""
    by = S.items_of_folders(inv.E(), folder_ids) if folder_ids else {}
    return {fid: [i for i in rows if i.get("kind") in BILL_KINDS and i.get("review") != "void"] for fid, rows in by.items()}


def _find(code, number):
    """按号码在全部票里找有效的发票/收据；老版票号码只有 8 位，带代码的再比代码。"""
    I = S.ITEM.c
    with inv.E().connect() as c:
        rows = [S._row(r) for r in c.execute(select(S.ITEM).where(and_(
            I.number == number, I.kind.in_(BILL_KINDS), S._item_active(), S._not_void())))]
    if code:
        rows = [r for r in rows if not r.get("code") or r.get("code") == code] or rows
    return rows


def _item_view(it, hit=None):
    v = {"id": it["id"], "folderId": it.get("folder_id"), "kind": it.get("kind"), "typeLabel": _type_label(it),
         "code": it.get("code") or "", "number": it.get("number") or "", "date": it.get("issue_date") or "",
         "sellerName": it.get("seller_name") or "", "total": inv._money_val(it.get("total")), "paper": bool(it.get("paper")),
         "review": it.get("review") or "draft", "hasQr": it.get("kind") == "invoice" and bool(it.get("number")), "hit": None,
         "voucher": "、".join(inv.voucher_labels_of(it)), "bookMonth": inv.book_month_of(it), "noBook": inv.no_book(it)}
    if hit:
        v["hit"] = {"at": hit.get("ts") or "", "by": hit.get("user") or "", "via": hit.get("via") or "",
                    "viaLabel": VIA_CN.get(hit.get("via") or "", hit.get("via") or "")}
    return v


def view(ck):
    """一次点验的全貌 → 页面要的：选中的票夹（各扫到几张）、每张票扫没扫到、扫到的不相干的票、合计。"""
    hits = S.check_hits(inv.E(), ck["id"])
    hit_of = {h["item_id"]: h for h in hits if h.get("kind") == "hit" and h.get("item_id")}
    fv, iv = [], []
    lst = _list_of(ck)
    if lst:
        # 对着一份认证清单：不按票夹分组，整份清单一组，按凭证顺序排（每行写着凭证）
        rows = _list_items(lst)
        got = sum(1 for i in rows if i["id"] in hit_of)
        fv.append({"id": 0, "title": lst.get("name") or "认证清单", "payee": "", "businessId": "", "amount": None, "source": "",
                   "company": "", "ownerName": "", "status": "", "total": len(rows), "hit": got, "isList": True})
        iv = [dict(_item_view(i, hit_of.get(i["id"])), folderId=0, realFolderId=i.get("folder_id")) for i in rows]
    else:
        folders = _folders(ck["folder_ids"])
        by = _items([f["id"] for f in folders])
        for f in folders:
            rows = by.get(f["id"], [])
            got = sum(1 for i in rows if i["id"] in hit_of)
            fv.append({"id": f["id"], "title": f.get("title") or "", "payee": f.get("payee_name") or "", "businessId": f.get("business_id") or "",
                       "amount": f.get("amount"), "source": f.get("source") or "", "company": f.get("company") or "",
                       "ownerName": f.get("owner_name") or "", "status": f.get("status") or "", "total": len(rows), "hit": got})
            iv += [_item_view(i, hit_of.get(i["id"])) for i in rows]
    strangers = [{"kind": h["kind"], "at": h.get("ts") or "", "by": h.get("user") or "", "via": h.get("via") or "",
                  **(h.get("detail") if isinstance(h.get("detail"), dict) else {})} for h in hits if h.get("kind") in ("other", "none")]
    n = sum(1 for i in iv if i["hit"])
    return {"ok": True, "check": {"id": ck["id"], "by": ck.get("created_by") or "", "at": ck.get("created_at") or "",
                                  "closedAt": ck.get("closed_at") or "", "entry": ck.get("entry") or "", "open": not ck.get("closed_at"),
                                  "listId": (lst or {}).get("id"), "listName": (lst or {}).get("name") or ""},
            "folders": fv, "items": iv, "strangers": strangers[-50:],
            "stats": {"total": len(iv), "hit": n, "left": len(iv) - n, "strangers": len(strangers)}}


def start_list(u, list_id, entry):
    """对着一份认证清单开一次点验。"""
    b = S.batch_get(inv.E(), list_id) if list_id else None
    if not b or b.get("kind") != TICKLIST_KIND:
        return None, inv.err("没有这份认证清单", 404)
    cid = S.check_create(inv.E(), u["name"], [], entry if entry in ("desk", "audit", "ledger") else "", list_id=b["id"])
    inv.audit(u, "对纸质票·开始", "点验#%d" % cid, "认证清单「%s」%d 张" % (b.get("name") or "", int(b.get("rows") or 0)))
    return view(S.check_get(inv.E(), cid)), None


def start(u, folder_ids, entry):
    ids = []
    for x in folder_ids if isinstance(folder_ids, list) else []:
        i = inv._int_or_none(x)
        if i and i not in ids:
            ids.append(i)
    if not ids:
        return None, inv.err("先勾上要对的票夹", 400)
    if len(ids) > MAX_FOLDERS:
        return None, inv.err("一次最多对 %d 个票夹，分几次来" % MAX_FOLDERS, 400)
    have = [f["id"] for f in _folders(ids)]
    if not have:
        return None, inv.err("勾的票夹都找不到了，请刷新后再选", 404)
    cid = S.check_create(inv.E(), u["name"], have, entry if entry in ("desk", "audit") else "")
    inv.audit(u, "对纸质票·开始", "点验#%d" % cid, "票夹 %d 个" % len(have))
    return view(S.check_get(inv.E(), cid)), None


def _mine(u, cid):
    """这次点验是不是这个人开的、还开着 → (行, None) 或 (None, 错误响应)。"""
    ck = S.check_get(inv.E(), cid) if cid else None
    if not ck or ck.get("created_by") != inv._uname(u):
        return None, inv.err("没有这次点验（或不是你开的）", 404)
    return ck, None


def scan(u, ck, text, via="scanner"):
    """扫到一个码 → {ok, result: ok|dup|other|none|unknown, say, msg, item?, stats}。只认发票二维码。"""
    e = inv.E()
    if ck.get("closed_at"):
        return {"ok": False, "result": "closed", "say": "", "msg": "这次点验已经收了：要接着对，重新勾票夹开一次"}
    name = inv._uname(u)
    c = ip.classify_code(text or "")
    q = (ip.parse_invoice_qr(c["value"]) or ip.parse_invoice_qr(text or "")) if c["kind"] == "invoice_qr" else None
    if not q or not q.get("number"):
        msg = "这是审批单的码：对纸质票请扫发票左上角的二维码" if c["kind"] in ("approval_link", "business_id") else "这不是发票二维码"
        return {"ok": True, "result": "unknown", "say": SAY["unknown"], "msg": msg}
    number, total = q["number"], inv._money_val(q.get("total"))
    found = _find(q.get("code") or "", number)
    hit_ids = {h["item_id"] for h in S.check_hits(e, ck["id"]) if h.get("kind") == "hit"}
    lst = _list_of(ck)
    if lst:
        in_list = {i["id"] for i in _list_items(lst)}
        inside = [i for i in found if i["id"] in in_list]
    else:
        inside = [i for i in found if i.get("folder_id") in ck["folder_ids"]]
    if inside:
        it = next((i for i in inside if i["id"] not in hit_ids), inside[0])
        new = S.check_hit_add(e, ck["id"], it["id"], name, via)
        if new and not it.get("paper"):
            S.item_update(e, it["id"], paper=1)               # 扫到实物＝纸质件在手
            inv.log(u, "纸质件到件（对纸质票）", it.get("folder_id"), it["id"], it.get("later_id"), {"number": number, "via": VIA_CN.get(via, via)})
            it = dict(it, paper=1)
        h = next((x for x in S.check_hits(e, ck["id"]) if x.get("item_id") == it["id"] and x.get("kind") == "hit"), None)
        out = {"ok": True, "result": "ok" if new else "dup", "item": _item_view(it, h)}
        out["msg"] = ("对上了：%s" if new else "这张扫过了：%s") % number
    elif found:
        it = found[0]
        f = S.folder_get(e, it.get("folder_id")) or {}
        det = {"number": number, "total": inv._money_val(it.get("total")), "sellerName": it.get("seller_name") or "",
               "folderId": f.get("id"), "folderTitle": f.get("title") or "", "payee": f.get("payee_name") or "",
               "ownerName": f.get("owner_name") or ""}
        if lst:
            # 对着认证清单：不在这份清单里的，说清它是哪个月做账的——整理纸质票时好分到那个月那一摞
            bm = inv.book_month_of(it)
            det.update(bookMonth=bm, voucher="、".join(inv.voucher_labels_of(it)), noBook=inv.no_book(it))
            S.check_hit_add(e, ck["id"], None, name, via, kind="other", detail=det)
            if det["noBook"]:
                say, why = "普票", "是普票，不入账、不认证"
            elif bm:
                say, why = _month_say(bm), "是 %s 做账的（%s）" % (bm, det["voucher"])
            elif it.get("review") != "approved":
                say, why = "还没审核", "还没审核通过，没到认证这一步"
            else:
                say, why = "还没做账", "还没做账（系统在金蝶没找到它的凭证）"
            out = {"ok": True, "result": "other", "stranger": dict(det, kind="other"), "say": say,
                   "msg": "不在这份清单里：%s 这张%s。没有登记，也没有挪" % (number, why)}
        else:
            S.check_hit_add(e, ck["id"], None, name, via, kind="other", detail=det)
            where = "「%s」" % (det["payee"] or det["folderTitle"] or ("票夹 #%s" % det["folderId"]))
            out = {"ok": True, "result": "other", "stranger": dict(det, kind="other"),
                   "msg": "不在选中的票夹里：%s 这张在%s里%s。没有登记，也没有挪" % (
                       number, where, ("（归%s审）" % det["ownerName"]) if det["ownerName"] else "")}
    else:
        det = {"number": number, "total": total}
        S.check_hit_add(e, ck["id"], None, name, via, kind="none", detail=det)
        out = {"ok": True, "result": "none", "stranger": dict(det, kind="none"),
               "msg": "系统里没有这张票：%s%s。要先收票（放进对应的付款单，或新建票夹）。没有登记" % (
                   number, (" · %s" % inv._money_str(total)) if total is not None else "")}
    out.setdefault("say", SAY[out["result"]])
    hits = [h for h in S.check_hits(e, ck["id"])]
    if lst:
        live = {i["id"] for i in _list_items(lst)}
        tot = len(live)
    else:
        by = _items(ck["folder_ids"])
        tot = sum(len(v) for v in by.values())
        live = {i["id"] for v in by.values() for i in v}
    n = len({h["item_id"] for h in hits if h.get("kind") == "hit" and h.get("item_id") in live})
    out["stats"] = {"total": tot, "hit": n, "left": tot - n}
    return out


def mark(u, ck, item_id, on):
    """没有二维码的票（收据、定额票、出租车票）：人看过以后手工勾上 / 取消。"""
    e = inv.E()
    if ck.get("closed_at"):
        return None, inv.err("这次点验已经收了", 400)
    it = S.item_get(e, item_id) if item_id else None
    lst = _list_of(ck)
    inside = bool(it) and ((it["id"] in {i["id"] for i in _list_items(lst)}) if lst else (it.get("folder_id") in ck["folder_ids"]))
    if not it or not inside or it.get("kind") not in BILL_KINDS or it.get("status") == "removed":
        return None, inv.err("这张票不在这份清单里" if lst else "这张票不在选中的票夹里", 400)
    if on:
        if S.check_hit_add(e, ck["id"], it["id"], inv._uname(u), "manual") and not it.get("paper"):
            S.item_update(e, it["id"], paper=1)
            inv.log(u, "纸质件到件（对纸质票）", it.get("folder_id"), it["id"], it.get("later_id"), {"number": it.get("number"), "via": "手工勾"})
    else:
        S.check_hit_remove(e, ck["id"], it["id"])             # 只撤这一次点验的勾，纸质件已到不往回改
    h = next((x for x in S.check_hits(e, ck["id"]) if x.get("item_id") == it["id"] and x.get("kind") == "hit"), None)
    return {"ok": True, "item": _item_view(S.item_get(e, it["id"]), h)}, None


def export(ck, only):
    v = view(ck)
    fo = {f["id"]: f for f in v["folders"]}
    rows = [i for i in v["items"] if only != "missing" or not i["hit"]]
    wb = X._new_wb()
    sw = X._SheetWriter(wb, "对纸质票" + ("·没扫到的" if only == "missing" else ""), EXPORT_COLUMNS, money=("价税合计",),
                        text=("审批编号", "发票代码", "发票号码"),
                        widths={"票夹": 34, "收款方": 28, "审批编号": 22, "发票号码": 24, "销方名称": 32, "扫到的时间": 19})
    for i in rows:
        f = fo.get(i["folderId"]) or {}
        h = i["hit"] or {}
        sw.add([f.get("title") or "", f.get("payee") or "", f.get("businessId") or "", f.get("ownerName") or "", i["typeLabel"], i["code"],
                i["number"], i["date"], i["sellerName"], i["total"], "扫到了" if i["hit"] else "没扫到", h.get("at") or "",
                ("%s（%s）" % (h.get("by") or "", h.get("viaLabel") or "")) if h else ""])
    sw.finish()
    return X._save(wb), len(rows)


# ───────────────────────── 接口（电脑） ─────────────────────────
GATES = (inv.ENTER_DESK, inv.ENTER_AUDIT, inv.ENTER_LEDGER)


@router.post("/api/inv/check/start")
async def check_start(request: Request):
    """body：{folderIds:[...], entry: desk|audit}。开一次点验（这个人原来没收的那次顺手收掉）。"""
    u, bad = inv.need(request, GATES)
    if bad:
        return bad
    body = await inv.body_json(request)
    if body.get("listId"):           # 对着一份认证清单
        out, bad = await run_in_threadpool(start_list, u, inv._int_or_none(body.get("listId")), body.get("entry"))
    else:
        out, bad = await run_in_threadpool(start, u, body.get("folderIds"), body.get("entry"))
    return bad if bad else out


@router.get("/api/inv/check/current")
async def check_current(request: Request):
    """我正在对的那一次（没有 → check=null）。电脑页面每两三秒取一次：手机扫到的那一行就跟着变绿。"""
    u, bad = inv.need(request, GATES)
    if bad:
        return bad

    def run():
        ck = S.check_current(inv.E(), u["name"])
        return view(ck) if ck else {"ok": True, "check": None}
    return await run_in_threadpool(run)


@router.get("/api/inv/check/{cid}")
async def check_get(cid: int, request: Request):
    u, bad = inv.need(request, GATES)
    if bad:
        return bad
    ck, bad = _mine(u, cid)
    if bad:
        return bad
    return await run_in_threadpool(view, ck)


@router.post("/api/inv/check/{cid}/scan")
async def check_scan(cid: int, request: Request):
    """body：{code}。电脑扫码枪扫到的一串字。"""
    u, bad = inv.need(request, GATES)
    if bad:
        return bad
    ck, bad = _mine(u, cid)
    if bad:
        return bad
    body = await inv.body_json(request)
    return await run_in_threadpool(scan, u, ck, str(body.get("code") or ""), "scanner")


@router.post("/api/inv/check/{cid}/mark")
async def check_mark(cid: int, request: Request):
    """body：{itemId, on}。没有二维码的票手工勾 / 取消。"""
    u, bad = inv.need(request, GATES)
    if bad:
        return bad
    ck, bad = _mine(u, cid)
    if bad:
        return bad
    body = await inv.body_json(request)
    out, bad = await run_in_threadpool(mark, u, ck, inv._int_or_none(body.get("itemId")), inv._b(body.get("on", True)))
    return bad if bad else out


@router.post("/api/inv/check/{cid}/close")
async def check_close(cid: int, request: Request):
    u, bad = inv.need(request, GATES)
    if bad:
        return bad
    ck, bad = _mine(u, cid)
    if bad:
        return bad

    def run():
        S.check_close(inv.E(), ck["id"])
        v = view(S.check_get(inv.E(), ck["id"]))
        st = v["stats"]
        inv.audit(u, "对纸质票·收", "点验#%d" % ck["id"], "票夹 %d 个；%d 张扫到 %d 张，没扫到 %d 张；不相干的票 %d 张" % (
            len(v["folders"]), st["total"], st["hit"], st["left"], st["strangers"]))
        return v
    return await run_in_threadpool(run)


@router.get("/api/inv/check/{cid}/export")
async def check_export(cid: int, request: Request):
    """?only=missing 只导没扫到的（默认）；only=all 全部。"""
    u, bad = inv.need(request, GATES)
    if bad:
        return bad
    ck, bad = _mine(u, cid)
    if bad:
        return bad
    only = "all" if request.query_params.get("only") == "all" else "missing"
    data, _n = await run_in_threadpool(export, ck, only)
    name = "对纸质票_%s%s.xlsx" % (re.sub(r"\D", "", ck.get("created_at") or "")[:12], "_没扫到的" if only == "missing" else "")
    from urllib.parse import quote
    return Response(content=data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename*=UTF-8''%s" % quote(name)})


# ───────────────────────── 接口（手机：用收票工作台的手机配对） ─────────────────────────

@router.get("/api/inv/m/check")
async def m_check(request: Request):
    """手机上看：电脑那边现在有没有在对纸质票、扫到几张了。"""
    p, u, bad = inv._phone(request)
    if bad:
        return bad

    def run():
        ck = S.check_current(inv.E(), u["name"])
        if not ck:
            return {"ok": True, "check": None}
        v = view(ck)
        return {"ok": True, "check": v["check"], "stats": v["stats"], "folders": len(v["folders"])}
    return await run_in_threadpool(run)


@router.post("/api/inv/m/check/scan")
async def m_check_scan(request: Request):
    """body：{code}。手机连续扫：记到电脑上这个账号正在对的那一次；回 say 给手机念（确认／重复／不在这里／没有这张）。"""
    p, u, bad = inv._phone(request)
    if bad:
        return bad
    body = await inv.body_json(request)

    def run():
        ck = S.check_current(inv.E(), u["name"])
        if not ck:
            return {"ok": False, "result": "closed", "say": "", "msg": "电脑上还没开始对纸质票：先在电脑上勾好票夹，点「对纸质票」"}
        return scan(u, ck, str(body.get("code") or ""), "phone")
    return await run_in_threadpool(run)
