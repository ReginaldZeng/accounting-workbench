# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-08-09 | Author: Claude / c | Version: V2.248
# Description: 【报表仪表盘 / 子公司报表】路由。把 kernels/report_dashboard.py 算出的
#              {books, periods, rpt} 下发前端。本文件是这条工具线在后端的唯一落点。
#
#              取数重（2024-01 至今每期一次 GL_BALANCE 查询，8 主体全量），故：
#              · GET 读缓存秒开（首次触发计算）；· POST /refresh 强刷（同 app 的「刷新金蝶数据」语义）。
#              进入本页的准入闸走菜单自动生成的 enter:rptdash（与「报表仪表盘」菜单同生共灭）。
from fastapi import APIRouter, Request

from kernels import report_dashboard as rd
import kingdee_client as kc

from core import CFG, JSONResponse, _current_user, _require_perm, _period_str, db, datetime

router = APIRouter()


def _payload(force=False):
    """算并返回子公司报表全量。sample 源无真数据 → 明确告知，不臆造。"""
    base = {"source": CFG["source"], "period": _period_str(),
            "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
    if CFG["source"] != "kingdee":
        return {**base, "books": {}, "periods": [], "rpt": {},
                "note": "当前数据源为样例，子公司报表需金蝶直取。请在「数据接入」切到金蝶后再看。"}
    try:
        data = rd.compute(kc, int(CFG["year"]), int(CFG["period"]), force=force)
    except kc.KingdeeError as e:
        return {**base, "books": {}, "periods": [], "rpt": {}, "error": str(e)[:300]}
    return {**base, **data}


@router.get("/api/report/dashboard")
def report_dashboard():
    """子公司报表全量（读缓存；首次访问触发计算，之后秒开）。"""
    return _payload(force=False)


@router.post("/api/report/dashboard/refresh")
def report_dashboard_refresh(request: Request):
    """强刷：重连金蝶、按期重算。进得了本页即可刷（enter:rptdash）。"""
    if not _require_perm(request, "enter:rptdash"):
        return JSONResponse({"ok": False, "msg": "无「报表仪表盘」进入权限"}, status_code=403)
    if CFG["source"] != "kingdee":
        return {"ok": False, "msg": "当前数据源为样例，无需刷新。"}
    try:
        data = _payload(force=True)
        u = _current_user(request)
        db.audit((u or {}).get("name", "?"), "报表仪表盘", "刷新",
                 "期间=%s 主体%d" % (_period_str(), len(data.get("books") or {})))
        return {"ok": True, **data}
    except Exception as e:
        return {"ok": False, "msg": str(e)[:300]}
