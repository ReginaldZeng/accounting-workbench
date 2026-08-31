# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-08-05 | Author: Claude / c | Version: V2.172
# Description: 【物流对账】路由（V2.172 从 app.py 拆出）。
#              本文件是「物流对账」这条工具线在后端的唯一落点：改这条线的接口只动本文件，
#              不再碰 app.py —— 这样多条需求并行开发时互不冲突。
#              共享的配置/期间/权限判定见 core.py；算法在 kernels/logistics_recon.py。
#              app.py 只负责 include_router(router)，不感知本文件内部。

from fastapi import APIRouter
from fastapi import Request
from kernels import logistics_recon as lrc
import kingdee_client as kc
import re

from core import (
    JSONResponse, _require_perm, db,
)

router = APIRouter()


# ==================== 物流对账 · 付款对账（二期：按回填单号直查金蝶，只读）====================
def _recon_backfill_nos(bill):
    """从解析结果收集去重后的回填单号（拆一行多单号）。"""
    return sorted({n for r in bill["rows"] if not r["pending"] for n in lrc._split_nos(r["backfill_no"])})


@router.get("/api/logistics-recon/parsers")
def logistics_recon_parsers(request: Request):
    """已做解析方案的承运商（可上传）+ 已建税率但未做方案的承运商（灰显）。显式选承运商，不自动识别。"""
    supported = lrc.list_parsers()
    done = {p["carrier"] for p in supported}
    others = []
    try:
        for r in (db.list_tax_rates() or []):
            nm = r.get("供应商") or r.get("supplier") or r.get("carrier")
            if nm and nm not in done and nm not in others:
                others.append(nm)
    except Exception:
        others = []
    return {"ok": True, "supported": supported, "others": sorted(others)}


@router.get("/api/logistics-recon/carriers")
def logistics_recon_carriers(request: Request, year: int = 0, period: int = 0):
    """承运商列表（从金蝶本期物流计提凭证派生）+ 是否已做解析方案 + 本期计提数。已做方案的可上传对账。"""
    if not _require_perm(request, "logistics_upload"):
        return JSONResponse({"ok": False, "msg": "无「物流对账」权限，请联系管理员"}, status_code=403)
    accr = {}
    if year and period:
        try:
            accr = lrc.accrued_by_carrier(kc.fetch_gl_voucher(int(year), int(period), prefix="2241"))
        except Exception:
            accr = {}

    def scheme_of(carrier):
        return next(((k, v) for k, v in lrc.PARSERS.items() if v.get("match") and v["match"] in carrier), None)

    out, matched = [], set()
    for carrier, amt in accr.items():
        p = scheme_of(carrier)
        if p:
            matched.add(p[0])
        out.append({"carrier": carrier, "parser": p[0] if p else None,
                    "scheme": f"{p[1]['name']} {p[1]['version']}" if p else "",
                    "docs": p[1].get("docs", "") if p else "", "multi": bool(p[1].get("multi")) if p else False,
                    "计提数": round(amt, 2)})
    for k, v in lrc.PARSERS.items():                       # 已做方案但本期无计提也要列出（可上传）
        if k not in matched:
            out.append({"carrier": k, "parser": k, "scheme": f"{v['name']} {v['version']}",
                        "docs": v.get("docs", ""), "multi": bool(v.get("multi")), "计提数": 0.0})
    out.sort(key=lambda x: (x["parser"] is None, -x["计提数"]))
    return {"ok": True, "year": year, "period": period,
            "计提数取自": "金蝶2241供应商往来·本期物流计提", "carriers": out}


async def _recon_read_files(request: Request):
    """读账单文件：multipart(字段 files，可多文件·天鹰) 或 原始 body(单文件·诚煜/跨越)。
    返回 bytes 列表；空则 []。两种都收，向后兼容旧单文件前端。"""
    ctype = request.headers.get("content-type", "")
    if "multipart/form-data" in ctype:
        form = await request.form()
        out = []
        for uf in form.getlist("files"):
            b = await uf.read()
            if b:
                out.append(b)
        return out
    data = await request.body()
    return [bytes(data)] if data else []


@router.post("/api/logistics-recon/parse")
async def logistics_recon_parse(request: Request, carrier: str = ""):
    """上传物流账单(单/多文件) → 按所选承运商解析方案解析 + 回填单号统计（不连金蝶，快）。"""
    if not _require_perm(request, "logistics_upload"):
        return JSONResponse({"ok": False, "msg": "无「物流对账」权限，请联系管理员"}, status_code=403)
    if not carrier:
        return {"ok": False, "msg": "请先选择承运商"}
    files = await _recon_read_files(request)
    if not files:
        return {"ok": False, "msg": "空文件（请选择物流账单 .xls/.xlsx）"}
    try:
        bill = lrc.parse_bill(carrier, files)
    except Exception as e:
        return {"ok": False, "msg": str(e)}
    rows = bill["rows"]
    nos = _recon_backfill_nos(bill)
    pref = {}
    for n in nos:
        m = re.match(r"^[A-Za-z]+", n)
        k = m.group(0) if m else "其它"
        pref[k] = pref.get(k, 0) + 1
    scheme = lrc.PARSERS.get(carrier, {})
    return {"ok": True, "carrier": carrier, "scheme": f"{scheme.get('name', '')} {scheme.get('version', '')}".strip(),
            "文件数": len(files), "summary": bill["summary"], "明细行": len(rows),
            "待人工": sum(1 for r in rows if r["pending"]),
            "notices": bill.get("notices") or [],       # 跳过的重复表 / 账单不自洽——必须让人看见
            "回填单号": len(nos), "单号前缀": pref}


@router.post("/api/logistics-recon/reconcile")
async def logistics_recon_reconcile(request: Request, carrier: str = ""):
    """上传账单 → 按所选承运商解析 + 按单号直查金蝶(只读) + 对账组核量 + 单位费用报表。慢查询，点按钮才跑。"""
    u = _require_perm(request, "logistics_upload")
    if not u:
        return JSONResponse({"ok": False, "msg": "无「物流对账」权限，请联系管理员"}, status_code=403)
    if not carrier:
        return {"ok": False, "msg": "请先选择承运商"}
    files = await _recon_read_files(request)
    if not files:
        return {"ok": False, "msg": "空文件"}
    try:
        bill = lrc.parse_bill(carrier, files)
        nos = _recon_backfill_nos(bill)
        docs = kc.fetch_docs_by_nos(nos)
    except kc.KingdeeError as e:
        return {"ok": False, "msg": f"金蝶取数失败：{e}"}
    except Exception as e:
        return {"ok": False, "msg": str(e)}
    scheme = lrc.PARSERS.get(carrier, {})
    recon = lrc.reconcile_by_backfill(bill, docs, qty_check=scheme.get("qty_check", True))
    report = lrc.unit_cost_report(bill, docs, carrier)
    # 物料级核量（天鹰等按重量计费的承运商）：单号×物料编码 比 kg，金蝶为准，超容差落「需人工复核」。
    # 按单号汇总整单会大面积误报（一单多物料只拉子集 / 基本单位非千克），故单开一份。
    matrecon = lrc.reconcile_by_material(bill, docs) if scheme.get("mat_check") else None
    try:
        db.audit(u["name"], "物流对账-付款对账", carrier,
                 f"{len(nos)}单号 命中{recon['stats'].get('一致', 0)+recon['stats'].get('数量不符', 0)}组")
    except Exception:
        pass
    return {"ok": True, "carrier": carrier, "scheme": f"{scheme.get('name', '')} {scheme.get('version', '')}".strip(),
            "qty_check": bool(scheme.get("qty_check", True)),
            "mat_check": bool(scheme.get("mat_check")),
            "notices": bill.get("notices") or [],       # 跳过的重复表 / 账单不自洽——必须让人看见
            "summary": bill["summary"], "kd_docs": len(docs),
            "stats": recon["stats"], "tieout": recon["tieout"], "rows": recon["rows"], "report": report,
            "matrecon": matrecon}
