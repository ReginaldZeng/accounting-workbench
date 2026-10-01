# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-01 | Author: Claude Opus 5.5 | Version: V2.730
# Description: 【物流复核·钉钉请款单】纯函数（不连钉钉/金蝶/库，便于单测）：
#   ① parse_form     审批单表单 → 主体/收款方/账号/金额/事由
#   ② classify_file  附件分角色：评论区=审核留档(review)；表单 xlsx=账单(bill)；表单里带「账单/盖章/对账/盘点」的 pdf=账单盖章件(stamp)；其余=发票(invoice，归发票管家)
#   ③ infer_period   归属账期：金额正好=某月该主体该供应商计提 → 那个月；否则事由/附件名写的月份；都没有 → 待认领
#   ④ status_view    钉钉节点 + 金蝶付款单 → 总表上一枚进度标(未提交/待谁审批/待我审批/已通过·待付款/已付款/已撤回)
#   ⑤ match_paybills 金蝶付款单(进金蝶＝已付款，用户 2026-10-01)按 供应商编码+付款组织+金额 一对一配给请款单
import json
import re
from datetime import datetime

TEMPLATE = "付款申请（公对公）"

_OP_TYPE = {"START_PROCESS_INSTANCE": "发起", "EXECUTE_TASK_NORMAL": "审批", "EXECUTE_TASK_AUTO": "自动通过",
            "REDIRECT_TASK": "转交", "ADD_REMARK": "评论", "TERMINATE_PROCESS_INSTANCE": "撤销",
            "REDIRECT_PROCESS": "退回"}
_OP_RES = {"AGREE": "同意", "REFUSE": "拒绝", "REDIRECTED": "转交", "NONE": ""}


def _money(v):
    try:
        return round(float(str(v).replace(",", "").strip()), 2)
    except (TypeError, ValueError):
        return None


def parse_form(inst):
    """审批单 → {subject_full, payee, payee_account, amount, reason}。字段按控件名认(公司主体/付款总额/实际付款总额/付款事由/银行信息表)。"""
    out = {"subject_full": "", "payee": "", "payee_account": "", "amount": None, "reason": ""}
    act = None
    for c in (inst or {}).get("form_component_values") or []:
        n, v = str(c.get("name") or ""), c.get("value")
        sv = "" if v in (None, "null") else str(v)
        if n == "公司主体":
            out["subject_full"] = sv.strip()
        elif n == "付款总额":
            out["amount"] = _money(sv)
        elif n == "实际付款总额":
            act = _money(sv)
        elif n == "付款事由":
            out["reason"] = sv.strip()
        elif n == "银行信息" and sv:
            try:
                rows = json.loads(sv)
            except ValueError:
                rows = []
            for row in rows if isinstance(rows, list) else []:
                for x in (row or {}).get("rowValue") or []:
                    lb, xv = str(x.get("label") or ""), str(x.get("value") or "").strip()
                    if "收款方名称" in lb and xv and not out["payee"]:
                        out["payee"] = xv
                    elif lb.startswith("银行账号") and xv and not out["payee_account"]:
                        out["payee_account"] = re.sub(r"\s+", "", xv)
    if act:                      # 有调整/押金时以实际付款总额为准
        out["amount"] = act
    return out


_STAMP_KW = ("账单", "盖章", "对账", "盘点")
_SHEET_EXT = (".xlsx", ".xls", ".xlsm")


def classify_file(name, source):
    """附件 → 角色。source=dingtalk_comment(审批人评论里补传) 一律算审核留档。"""
    nm = str(name or "").lower()
    if source == "dingtalk_comment":
        return "review"
    if nm.endswith(_SHEET_EXT):
        return "bill"
    if nm.endswith(".pdf") and any(k in str(name) for k in _STAMP_KW):
        return "stamp"
    return "invoice"


def _ym(dt_str):
    try:
        d = datetime.strptime(str(dt_str)[:10], "%Y-%m-%d")
        return d.year, d.month
    except ValueError:
        return None


def prev_periods(create_time, n=4):
    """提交月起往前 n 个账期(含当月)：请款一般在次月，先看上月。返回 ['2026-08','2026-09',...] 上月优先。"""
    ym = _ym(create_time)
    if not ym:
        return []
    y, m = ym
    out = []
    for k in range(n):
        mm, yy = m - k, y
        while mm <= 0:
            mm += 12
            yy -= 1
        out.append("%04d-%02d" % (yy, mm))
    return out[1:] + out[:1]     # 上月、上上月…，当月放最后


def period_from_text(texts, create_time):
    """事由/附件名里写的月份 → 账期。「2026年8月」优先；只写「8月」按提交日推年份(月份大于提交月＝去年)。"""
    ym = _ym(create_time)
    for t in texts:
        mo = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月", str(t or ""))
        if mo and 1 <= int(mo.group(2)) <= 12:
            return "%s-%02d" % (mo.group(1), int(mo.group(2)))
    if not ym:
        return ""
    for t in texts:
        mo = re.search(r"(?<!\d)(\d{1,2})\s*月", str(t or ""))
        if mo and 1 <= int(mo.group(1)) <= 12:
            m = int(mo.group(1))
            y = ym[0] - 1 if m > ym[1] else ym[0]
            return "%04d-%02d" % (y, m)
    return ""


def infer_period(amount, subject, sup_code, create_time, accr_by_period, texts):
    """→ (period, src)。accr_by_period = {period: {(subject, sup_code): 计提含税}}。"""
    if amount:
        for p in prev_periods(create_time):
            a = (accr_by_period.get(p) or {}).get((subject, sup_code))
            if a and abs(a - amount) < 0.005:
                return p, "amount"
    p = period_from_text(texts, create_time)
    return (p, "text") if p else ("", "")


def ops_view(inst, names):
    """节点记录(去掉抄送) → [{name,type,result,date,remark}]。names = {userid: 姓名}。"""
    out = []
    for o in (inst or {}).get("operation_records") or []:
        t = o.get("operation_type") or ""
        if t == "PROCESS_CC":
            continue
        out.append({"name": names.get(o.get("userid"), o.get("userid") or ""), "type": _OP_TYPE.get(t, t),
                    "result": _OP_RES.get(o.get("operation_result") or "", o.get("operation_result") or ""),
                    "date": str(o.get("date") or "")[:16], "remark": str(o.get("remark") or "")[:300]})
    return out


def current_tasks(inst, names):
    """当前在办(未结的任务) → [{userid,name}]。"""
    out, seen = [], set()
    for t in (inst or {}).get("tasks") or []:
        if str(t.get("task_status") or "").upper() in ("NEW", "RUNNING") and t.get("userid") and t["userid"] not in seen:
            seen.add(t["userid"])
            out.append({"userid": t["userid"], "name": names.get(t["userid"], t["userid"])})
    return out


def status_view(req, me_uid=""):
    """一张请款单的进度标 → {key, label, date}。key: mine/run/agreed/paid/void。"""
    st, res = str(req.get("dt_status") or "").upper(), str(req.get("dt_result") or "").lower()
    if st == "TERMINATED":
        return {"key": "void", "label": "已撤回", "date": req.get("finish_time") or ""}
    if res == "refuse":
        return {"key": "void", "label": "已拒绝", "date": req.get("finish_time") or ""}
    if st == "RUNNING":
        cur = req.get("cur") or []
        if me_uid and any(x.get("userid") == me_uid for x in cur):
            return {"key": "mine", "label": "待我审批", "date": ""}
        who = "、".join(x.get("name") or "" for x in cur[:2]) + ("等" if len(cur) > 2 else "")
        return {"key": "run", "label": ("待%s审批" % who) if who else "审批中", "date": ""}
    if req.get("kd_paid"):
        return {"key": "paid", "label": "已付款", "date": str(req["kd_paid"]).split("|")[0]}
    if st == "COMPLETED":
        return {"key": "agreed", "label": "已通过·待付款", "date": req.get("finish_time") or ""}
    return {"key": "run", "label": "已提交", "date": ""}


# 一格多张请款单时取"最靠后"的那张代表这一格：待我审批最醒目
_RANK = {"mine": 0, "run": 1, "agreed": 2, "paid": 3, "void": 9}


def cell_rank(key):
    return _RANK.get(key, 5)


def match_paybills(reqs, paybills, taken=()):
    """金蝶付款单一对一配请款单：同供应商编码+同付款组织(全称)+同金额，付款单日期不早于请款提交日；早提交的先配。
    reqs: [{inst_id, sup_code, subject_full, amount, create_time}]；paybills: [{id, code, org, amount, date, status}]；
    taken = 已配给别的请款单的付款单 id。→ {inst_id: 'YYYY-MM-DD|状态|付款单id'}。"""
    used, out = {i for i, b in enumerate(paybills) if str(b.get("id")) in {str(t) for t in taken}}, {}
    for r in sorted(reqs, key=lambda x: str(x.get("create_time") or "")):
        for i, b in enumerate(paybills):
            if i in used:
                continue
            if b.get("code") != r.get("sup_code") or b.get("org") != r.get("subject_full"):
                continue
            if r.get("amount") is None or abs(float(b.get("amount") or 0) - float(r["amount"])) >= 0.005:
                continue
            if str(b.get("date") or "")[:10] < str(r.get("create_time") or "")[:10]:
                continue
            used.add(i)
            out[r["inst_id"]] = "%s|%s|%s" % (str(b.get("date") or "")[:10], b.get("status") or "", b.get("id") or "")
            break
    return out
