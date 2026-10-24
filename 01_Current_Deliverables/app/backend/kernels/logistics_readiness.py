# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-24 | Author: Kimi | Version: V2.1004
# Description: 物流账单复核四步就绪判定纯函数。只消费各步现有结果，不查库、不连金蝶；GET readiness 与登记复核共用。
import re


def normalize_name(value):
    return re.sub(r"[\s()（）]+", "", str(value or ""))


def _step(label, count=0, blockers=None, error=False):
    blockers = list(blockers or [])
    return {"status": "error" if error else ("pending" if blockers else "ok"),
            "label": label, "count": int(count), "blockers": blockers}


def _failed(data):
    return str((data or {}).get("error") or (data or {}).get("msg") or "") if isinstance(data, dict) and (
        data.get("error") or data.get("ok") is False) else ""


def _intake_step(data):
    err = _failed(data)
    if err:
        return _step("数据接入取数失败", blockers=[err], error=True)
    data = data or {}
    blockers = []
    if not data.get("has_spec"):
        blockers.append("还没配取数说明")
    if not data.get("bills"):
        blockers.append("还没有解析好的账单")
    if data.get("verdict") != "ok":
        verdict = data.get("verdict") or "未核对"
        labels = {"nobill": "还没账单", "noraw": "没存原件", "nospec": "没有取数说明", "unsplit": "有金额未拆到费用项",
                  "gap": "与账单合计不一致", "maybe": "可能漏读", "nocheck": "没有合计可比", "err": "解析失败"}
        blockers.append("账单原件核对未通过：%s" % labels.get(verdict, verdict))
    if data.get("diff") is None or abs(float(data.get("diff") or 0)) >= 0.005:
        blockers.append("账单与请款合计未对平")
    return _step("数据接入已就绪", len(data.get("bills") or []), blockers)


def _normal_lines_step(data):
    err = _failed(data)
    if err:
        return _step("逐笔计提取数失败", blockers=[err], error=True)
    data = data or {}
    rows = data.get("rows") or []
    blockers = []
    if data.get("n_unexplained"):
        blockers.append("还有 %d 笔差异或部门问题没有解释/更正" % int(data["n_unexplained"]))
    totals = [r for r in rows if r.get("kind") == "gtotal"]
    if not totals:
        blockers.append("没有可比的主体汇总")
    for row in totals:
        mark = row.get("mark") or {}
        subject = row.get("subject") or "未标主体"
        if mark.get("status") != "ok":
            blockers.append("%s 还未标记本主体通过" % subject)
        if abs(float(row.get("diff") or 0)) >= 0.005 and mark.get("dv") not in ("fix", "keep"):
            blockers.append("%s 的非零差异还没确认 fix/keep" % subject)
    for row in rows:
        warn = row.get("dept_warn")
        if not warn or warn.get("fixed") or str(row.get("note") or "").strip():
            continue
        if (row.get("fix") or {}).get("to_dept"):
            continue
        blockers.append("%s %s 部门问题未解释或登记更正" % (row.get("subject") or "", row.get("vno") or row.get("key") or ""))
    return _step("逐笔计提已就绪", sum(r.get("kind") != "gtotal" for r in rows), list(dict.fromkeys(blockers)))


def _register_lines_step(data, note):
    err = _failed(data)
    if err:
        return _step("登记制汇总取数失败", blockers=[err], error=True)
    cells = (data or {}).get("cells") or []
    blockers = []
    if not cells:
        blockers.append("没有费用凭证/计提与请款可比")
    for cell in cells:
        subject = cell.get("subject") or "未标主体"
        if not cell.get("has_req") or not cell.get("has_accr"):
            blockers.append("%s 缺费用凭证/计提或请款，暂时无数可比" % subject)
    if any(abs(float(c.get("diff") or 0)) >= 0.005 for c in cells) and not str(note or "").strip():
        blockers.append("计提/费用凭证与请款有差异，登记时必须填写 note 说明")
    return _step("登记制汇总已就绪", len(cells), blockers)


def _price_passed(doc):
    if doc.get("ky"):
        return doc["ky"].get("verdict") == "一致"
    trip = doc.get("trip") or {}
    if trip:
        return (trip.get("price") is not None and trip.get("std") is not None
                and abs(float(trip.get("run") or 0) - float(trip.get("std") or 0)) < 0.005
                and not trip.get("ot"))
    fees = doc.get("sub_fees") or {}
    return "标准" in fees and "核价差" not in fees


def _docs_step(data):
    err = _failed(data)
    if err:
        return _step("逐单核价核量取数失败", blockers=[err], error=True)
    data = data or {}
    docs = data.get("docs") or []
    classes = data.get("classify") or {}
    dups = data.get("dups") or {}
    blockers = []
    for doc in docs:
        if doc.get("confirmed"):
            continue
        no = doc.get("doc_no") or "未标单号"
        if doc.get("state") not in ("ok", "info"):
            blockers.append("%s 数量未系统通过，需人工确认" % no)
        if not _price_passed(doc):
            blockers.append("%s 价格无依据或未系统通过，需人工确认" % no)
        if doc.get("fee_chk") not in ("ok", "na"):
            blockers.append("%s 费用类型未通过，需人工确认" % no)
        cls = classes.get(no) or {}
        if cls.get("v") != "归类一致":
            blockers.append("%s 归类未通过（%s），需人工确认" % (no, cls.get("v") or "未取到结论"))
        if no in dups and not (dups[no] or {}).get("ok"):
            blockers.append("%s 疑似重复收费未处理，需人工确认" % no)
    return _step("逐单核价核量已就绪", len(docs), blockers)


def _sign_step(data, carry):
    err = _failed(data)
    if err:
        return _step("发票审核取数失败", blockers=[err], error=True)
    blocks = (data or {}).get("blocks") or []
    blockers = [] if blocks else ["还没有钉钉请款单"]
    for block in blocks:
        name = block.get("bid") or block.get("inst") or "未标请款单"
        if block.get("err"):
            blockers.append("%s：%s" % (name, block["err"]))
            continue
        invoices = block.get("invoices") or []
        if not invoices:
            if not block.get("later"):
                blockers.append("%s 无发票且未确认后补" % name)
            continue
        total = round(sum(float(i.get("gross") or 0) for i in invoices), 2)
        if abs(total - float(block.get("amount") or 0)) >= 0.005:
            blockers.append("%s 请款金额与发票合计不等" % name)
        for inv in invoices:
            number = inv.get("number") or "无号码"
            if inv.get("review") != "approved":
                blockers.append("%s 发票 %s 尚未审核通过" % (name, number))
            if normalize_name(inv.get("seller")) != normalize_name(block.get("payee")):
                blockers.append("%s 发票 %s 销方与请款收款方不一致" % (name, number))
            if normalize_name(inv.get("buyer")) != normalize_name(block.get("subject_full")):
                blockers.append("%s 发票 %s 购方与请款主体不一致" % (name, number))
    blockers.extend("历史待扣回未处理：%s" % x for x in (carry or []))
    return _step("发票审核已就绪", len(blocks), blockers)


def evaluate(carrier, period, mode="normal", intake=None, lines=None, docs=None, sign=None,
             note="", carry=None, signed=None):
    register = mode == "register"
    steps = {
        "intake": _step("登记制免核") if register else _intake_step(intake),
        "docs": _step("登记制免核") if register else _docs_step(docs),
        "lines": _register_lines_step(lines, note) if register else _normal_lines_step(lines),
        "sign": _sign_step(sign, carry),
    }
    blockers = list(dict.fromkeys(x for step in steps.values() for x in step["blockers"]))
    required = ("lines", "sign") if register else ("intake", "lines", "docs", "sign")
    return {"ok": True, "carrier": carrier, "period": period, "mode": "register" if register else "normal",
            "steps": steps, "can_sign": not signed and all(steps[k]["status"] == "ok" for k in required),
            "blockers": blockers, "signed": signed}
