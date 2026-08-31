# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-08-11 | Author: Claude / c | Version: V2.250
# Description: 【电商对账】路由（条目⑤一期：收款核销+基础资料）。
#              本文件是「电商对账」线在后端的唯一落点；算法在 kernels/ec_settle.py，
#              金蝶取数走 kingdee_client.fetch_ec_receivables（只读）。
#              跑批（95k 行流水+金蝶整段拉取，约 2-4 分钟）走后台线程+轮询，
#              不阻塞 uvicorn 单 worker（同汇率线 threading 惯例）。
import json
import os
import re
import threading
import datetime

from fastapi import APIRouter, Request, File, UploadFile, Form
from sqlalchemy import select, insert, delete

import kingdee_client as kc
from kernels import ec_settle as es
from core import JSONResponse, _require_perm, db

router = APIRouter()

_now = lambda: datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EC_UPLOAD_DIR = os.path.join(_BASE, "ec_uploads")          # 手工文件按期落盘：ec_uploads/{YYYY-MM}/
BANK_UPLOAD_DIR = os.path.join(_BASE, "bank_uploads")      # 银行对账流水包解压目录（app.py UPLOAD_DIR 同路径）


# ==================== 数据源识别（V2.255：文件导入页的灯表） ====================
def _alipay_files(period):
    """扫银行对账本期流水包解压目录里的支付宝 2088* 原始文件。
    出纳每月上传的流水包里就有全量逐笔（银行对账只取渠道汇总，我们逐行喂核销引擎）。
    目录名={source}_{year}_{period}（source 通常「银行」；扫所有 source 前缀兜底）。"""
    y, m = period[:4], str(int(period[5:7]))
    out = []
    if not os.path.isdir(BANK_UPLOAD_DIR):
        return out
    for d in os.listdir(BANK_UPLOAD_DIR):
        if not d.endswith("_%s_%02d" % (y, int(m))):
            continue
        ex = os.path.join(BANK_UPLOAD_DIR, d, "extracted")
        if not os.path.isdir(ex):
            continue
        for root, _dirs, files in os.walk(ex):
            for fn in files:
                if "2088" in fn and (fn.endswith(".xls") or fn.endswith(".xls.zip") or fn.endswith(".xlsx")):
                    m2 = re.search(r"(2088\d{12,})", fn)
                    out.append({"file": os.path.join(root, fn), "name": fn,
                                "acct": m2.group(1) if m2 else ""})
    # 同查询段 .xls 与 .xls.zip 并存时取一个（同 bank_import.parse_channels 的去重思路）
    seen = {}
    for f in sorted(out, key=lambda x: x["name"]):
        key = re.sub(r"\.(xls|xlsx)(\.zip)?$", "", f["name"])
        seen.setdefault(key, f)
    return list(seen.values())


def _shop_dir(period, shop):
    safe = re.sub(r"[^\w一-鿿（）()-]", "_", shop)[:60]
    return os.path.join(EC_UPLOAD_DIR, period, safe)


def _manual_path(period, kind, shop):
    return os.path.join(_shop_dir(period, shop), kind + ".xlsx")


_WDT_KINDS = ("销售出库", "销售退货", "退款不退货")     # ③旺店通数据（发货核对×2 + 收款核销×1）
_PLAT_KINDS = ("平台订单", "平台退款", "平台保证金", "平台价保")   # ④平台数据（订单必、退款/保证金推荐、价保可选）


def _shop_files(period, shop):
    """该店本期已识别落盘的手工文件：{类型: 原始文件名}。类型名.xlsx 旁存 .name 记原始名。"""
    d = _shop_dir(period, shop)
    out = {}
    for kind in _WDT_KINDS + _PLAT_KINDS:
        p = os.path.join(d, kind + ".xlsx")
        if os.path.isfile(p):
            namef = p + ".name"
            out[kind] = open(namef, encoding="utf-8").read() if os.path.isfile(namef) else kind + ".xlsx"
    return out


# ---- 金蝶应收·半自动（V2.257）：手动刷新→缓存落盘（谁/何时刷的可追溯），跑批优先用缓存 ----
def _kd_cache_path(period):
    return os.path.join(EC_UPLOAD_DIR, period, "金蝶应收缓存.json")


def _kd_cache_meta(period):
    meta = (db.get_setting("ec_kd_cache_meta", {}) or {}).get(period)
    if meta and not os.path.isfile(_kd_cache_path(period)):
        return None                                    # 文件被清了就别谎报有缓存
    return meta


_KD_REFRESH = {}                                       # period -> {"running","error"}


@router.post("/api/ec/settle/kd-refresh")
def ec_kd_refresh(request: Request, period: str = Form(...)):
    """手动刷新金蝶应收（只读）→ 缓存落盘。后台线程跑（16k+ 行约 1 分钟），sources 轮询可见。"""
    u = _require_perm(request, "ec_settle_upload")
    if not u:
        return JSONResponse({"error": "需要「上传结算流水/跑批」权限"}, status_code=403)
    if not re.match(r"^\d{4}-\d{2}$", period):
        return JSONResponse({"error": "期间格式 YYYY-MM"}, status_code=400)
    if _KD_REFRESH.get(period, {}).get("running"):
        return JSONResponse({"error": "该期正在刷新中"}, status_code=409)
    _KD_REFRESH[period] = {"running": True, "error": ""}

    def job():
        try:
            import calendar
            y, m = int(period[:4]), int(period[5:7])
            end = "%04d-%02d-%02d" % (y, m, calendar.monthrange(y, m)[1])
            fy, fm = (y - 1, m + 6) if m <= 6 else (y, m - 6)
            rows = kc.fetch_ec_receivables("%04d-%02d-01" % (fy, fm), end)
            p = _kd_cache_path(period)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False)
            meta = db.get_setting("ec_kd_cache_meta", {}) or {}
            meta[period] = {"rows": len(rows), "ts": _now(), "operator": u["name"]}
            db.set_setting("ec_kd_cache_meta", meta, operator=u["name"])
            db.audit(u["name"], "ec_kd_refresh", target=period, detail="%d 行" % len(rows))
        except Exception as e:
            _KD_REFRESH[period]["error"] = str(e)[:200]
        finally:
            _KD_REFRESH[period]["running"] = False

    threading.Thread(target=job, daemon=True).start()
    return {"ok": True}


@router.get("/api/ec/settle/sources")
def ec_settle_sources(request: Request, period: str):
    """文件导入页灯表：金蝶(半自动·可刷新) / 支付宝(自动·来自银行对账流水包) / 手工文件(退款不退货 按店)。"""
    if not _require_perm(request, "enter:ecomsettle"):
        return JSONResponse({"error": "无权限"}, status_code=403)
    if not re.match(r"^\d{4}-\d{2}$", period or ""):
        return JSONResponse({"error": "期间格式 YYYY-MM"}, status_code=400)
    shops = [r for r in _rows(db.ec_shop_map) if (r.get("alipay_acct") or "").strip()]
    files = _alipay_files(period)
    by_acct = {}
    for f in files:
        by_acct.setdefault(f["acct"], []).append(f)
    kd = _kd_cache_meta(period)
    rows = []
    for s in shops:
        acct = s["alipay_acct"].strip()
        ali = by_acct.get(acct, [])
        got = _shop_files(period, s["wdt_name"])
        wdt = {k: {"ok": k in got, "name": got.get(k, "")} for k in _WDT_KINDS}
        plat = {k: {"ok": k in got, "name": got.get(k, "")} for k in _PLAT_KINDS}
        # 四类就绪：①账户流水 ②金蝶 ③旺店通(三张全) ④平台(订单导出为必，退款/价保为辅)。
        # 123齐=可核销(常亮)；四类全齐=呼吸灯。
        c1, c2 = bool(ali), bool(kd)
        c3 = all(wdt[k]["ok"] for k in _WDT_KINDS)
        c4 = plat["平台订单"]["ok"]
        rows.append({"shop": s["wdt_name"], "acct": acct,
                     "alipay": {"ok": c1, "files": [a["name"] for a in ali]},
                     "wdt": wdt, "platform": plat,
                     "missing": (0 if c1 else 1) + (0 if c2 else 1) + (0 if c3 else 1) + (0 if c4 else 1),
                     "ready_settle": c1 and c2 and c3, "ready_all": c1 and c2 and c3 and c4})
    orphan = [f["name"] for f in files if f["acct"] not in {s["alipay_acct"].strip() for s in shops}]
    kd_state = _KD_REFRESH.get(period, {})
    # 账户流水包是谁/何时传的（银行对账上传时的审计记录，target=YYYY-MM；查不到就只报有无文件）
    pkg = None
    try:
        with db._engine.begin() as cx:
            row = cx.execute(select(db.audit_log).where(db.audit_log.c.action == "上传银行流水")
                             .where(db.audit_log.c.target == period)
                             .order_by(db.audit_log.c.id.desc()).limit(1)).first()
        if row:
            pkg = {"operator": row.operator, "ts": row.ts}
    except Exception:
        pkg = None
    return {"period": period, "rows": rows, "orphan_files": orphan, "flow_pkg": pkg,
            "kd": {"meta": kd, "refreshing": bool(kd_state.get("running")),
                   "error": kd_state.get("error", "")},
            "hint_no_shop": (not shops) and "店铺对照表还没配「支付宝账号」——基础资料里给店铺填 2088 账号后，这里才能自动认文件" or ""}


@router.post("/api/ec/settle/upload-files")
async def ec_upload_files(request: Request, period: str = Form(...), shop: str = Form(...)):
    """多文件拖入 → 按表头自动识别归类落盘（旺店通三表+平台订单）。认不出的单独报，不猜不静默。"""
    u = _require_perm(request, "ec_settle_upload")
    if not u:
        return JSONResponse({"error": "需要「上传结算流水/跑批」权限"}, status_code=403)
    if not re.match(r"^\d{4}-\d{2}$", period):
        return JSONResponse({"error": "期间格式 YYYY-MM"}, status_code=400)
    form = await request.form()
    ups = [v for v in form.getlist("files") if hasattr(v, "filename")]
    if not ups:
        return JSONResponse({"error": "没收到文件"}, status_code=400)
    recognized, unknown = [], []
    for f in ups:
        data = await f.read()
        kind = es.sniff_type(data)
        if not kind:
            unknown.append(f.filename)
            continue
        p = _manual_path(period, kind, shop)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as w:
            w.write(data)
        with open(p + ".name", "w", encoding="utf-8") as w:
            w.write(f.filename or "")
        recognized.append({"file": f.filename, "kind": kind})
    db.audit(u["name"], "ec_upload_files", target="%s %s" % (period, shop),
             detail="识别%d 未识别%d" % (len(recognized), len(unknown)))
    # 平台订单/销售出库落盘后 → 后台重建事件索引（时间链的下单/付款/发货，51k 行约半分钟，不卡上传）
    if any(r["kind"] in ("平台订单", "销售出库") for r in recognized):
        try:
            os.remove(_event_index_path(period, shop))
        except OSError:
            pass
        threading.Thread(target=_build_event_index, args=(period, shop), daemon=True).start()
    return {"ok": True, "recognized": recognized, "unknown": unknown}


@router.post("/api/ec/settle/manual-upload")
async def ec_manual_upload(request: Request, period: str = Form(...), kind: str = Form("退款不退货"),
                           shop: str = Form(...), file: UploadFile = File(...)):
    u = _require_perm(request, "ec_settle_upload")
    if not u:
        return JSONResponse({"error": "需要「上传结算流水/跑批」权限"}, status_code=403)
    if not re.match(r"^\d{4}-\d{2}$", period):
        return JSONResponse({"error": "期间格式 YYYY-MM"}, status_code=400)
    data = await file.read()
    p = _manual_path(period, kind, shop)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    db.audit(u["name"], "ec_manual_upload", target="%s %s %s" % (period, kind, shop), detail=file.filename)
    return {"ok": True}


@router.post("/api/ec/settle/run-auto")
def ec_settle_run_auto(request: Request, shop: str = Form(...), period: str = Form(...)):
    """自动跑批：支付宝流水=银行对账流水包（按店铺配置的 2088 账号认文件），退款不退货=手工文件夹。"""
    u = _require_perm(request, "ec_settle_upload")
    if not u:
        return JSONResponse({"error": "需要「上传结算流水/跑批」权限"}, status_code=403)
    srow = next((r for r in _rows(db.ec_shop_map) if r["wdt_name"] == shop and (r.get("alipay_acct") or "").strip()), None)
    if not srow:
        return JSONResponse({"error": "店铺对照表里没给「%s」配支付宝账号（基础资料）" % shop}, status_code=400)
    files = [f for f in _alipay_files(period) if f["acct"] == srow["alipay_acct"].strip()]
    if not files:
        return JSONResponse({"error": "本期银行对账流水包里没找到该店铺的支付宝文件（账号 %s）——请确认出纳已上传本期流水包" % srow["alipay_acct"]}, status_code=400)
    if not _RUN_LOCK.acquire(blocking=False):
        return JSONResponse({"error": "已有跑批在进行，请等它结束"}, status_code=409)
    rk_path = _manual_path(period, "退款不退货", shop)
    refund_bytes = open(rk_path, "rb").read() if os.path.isfile(rk_path) else None
    fnames = "；".join(f["name"] for f in files) + (("；" + os.path.basename(rk_path)) if refund_bytes else "")
    with db._engine.begin() as cx:
        rid = cx.execute(insert(db.ec_settle_runs).values(
            shop=shop, period=period, status="running", stats="{}",
            filenames="[自动]" + fnames, operator=u["name"], ts=_now())).inserted_primary_key[0]
    _RUNS[rid] = {"step": "读取银行对账流水包中的支付宝文件…", "error": ""}
    threading.Thread(target=_run_job_auto, daemon=True,
                     args=(rid, shop, period, [f["file"] for f in files], refund_bytes, u["name"])).start()
    return {"ok": True, "run_id": rid}


def _run_job_auto(rid, shop, period, file_paths, refund_bytes, operator):
    try:
        flow_rows = []
        for p in file_paths:
            _RUNS[rid]["step"] = "解析 %s …" % os.path.basename(p)
            flow_rows += es.parse_flow_any(open(p, "rb").read())
        _run_core(rid, shop, period, flow_rows, refund_bytes, operator)
    except Exception as e:
        _fail_run(rid, e)
    finally:
        _RUN_LOCK.release()

# 跑批进度（进程内，单 worker 假设与 _NAV_CACHE 一致）：run_id -> {"step","error"}
_RUNS = {}
_RUN_LOCK = threading.Lock()          # 同时只允许一个跑批——覆盖全员数据的动作不并发


# ==================== 基础资料 ====================
def _rows(table):
    with db._engine.begin() as cx:
        return [dict(r._mapping) for r in cx.execute(select(table))]


@router.get("/api/ec/basicdata")
def ec_basicdata(request: Request):
    if not _require_perm(request, "enter:ecombase") and not _require_perm(request, "enter:ecomsettle"):
        return JSONResponse({"error": "无权限"}, status_code=403)
    seeded = _seed_if_empty()
    return {"shop_map": _rows(db.ec_shop_map), "fee_map": _rows(db.ec_fee_map),
            "rules": db.get_setting("ec_settle_rules", es.DEFAULT_RULES),
            "voucher_cfg": db.get_setting("ec_voucher_cfg", {}) or {}, "seeded": seeded}


def _seed_if_empty():
    """首启播种：费目映射 30 条 + 店铺对照 6 条（两月实证提取）。只在空表时种，绝不覆盖人工维护。"""
    seeded = False
    with db._engine.begin() as cx:
        if not cx.execute(select(db.ec_fee_map).limit(1)).first():
            for code, label, account in es.FEE_MAP_SEED:
                cx.execute(insert(db.ec_fee_map).values(
                    code=code, label=label, account=account, updated_by="种子", updated_at=_now()))
            seeded = True
        if not cx.execute(select(db.ec_shop_map).limit(1)).first():
            for kd, wdt, plat in es.SHOP_MAP_SEED:
                cx.execute(insert(db.ec_shop_map).values(
                    kd_name=kd, wdt_name=wdt, platform=plat, updated_by="种子", updated_at=_now()))
            seeded = True
    return seeded


@router.post("/api/ec/basicdata")
async def ec_basicdata_save(request: Request):
    u = _require_perm(request, "ec_base_edit")
    if not u:
        return JSONResponse({"error": "需要「维护基础资料」权限"}, status_code=403)
    body = await request.json()
    with db._engine.begin() as cx:
        for name, table, cols in (("shop_map", db.ec_shop_map, ("kd_name", "wdt_name", "mgmt_name", "platform", "alipay_acct")),
                                  ("fee_map", db.ec_fee_map, ("code", "label", "account", "kd_code"))):
            rows = body.get(name)
            if rows is None:
                continue
            clean = []
            seen = set()
            for r in rows:
                key0 = str(r.get(cols[0]) or "").strip()
                if not key0 or key0 in seen:
                    continue
                seen.add(key0)
                clean.append({c: str(r.get(c) or "").strip()[:120] for c in cols})
            cx.execute(delete(table))                 # 整表覆盖（受控小表，几十行）
            for r in clean:
                cx.execute(insert(table).values(**r, updated_by=u["name"], updated_at=_now()))
    if body.get("rules") is not None:
        ru = {k: float(v) if k != "qr_goods" else str(v)
              for k, v in dict(body["rules"]).items() if k in es.DEFAULT_RULES}
        db.set_setting("ec_settle_rules", ru, operator=u["name"])
    if body.get("voucher_cfg") is not None:          # 凭证配置（账簿/凭证字/币别/两侧科目编码，V2.257）
        vc = {k: str(v or "").strip()[:40] for k, v in dict(body["voucher_cfg"]).items()
              if k in ("book_code", "voucher_group", "currency", "rate_type", "cash_acct", "ar_acct")}
        db.set_setting("ec_voucher_cfg", vc, operator=u["name"])
    db.audit(u["name"], "ec_basicdata_save", detail="电商对账基础资料整表保存")
    return {"ok": True}


# ==================== 跑批（上传 → 后台线程 → 轮询） ====================
@router.post("/api/ec/settle/run")
async def ec_settle_run(request: Request, shop: str = Form(""), period: str = Form(""),
                        ar_from: str = Form(""),
                        flow: UploadFile = File(...), refunds: UploadFile = File(None)):
    u = _require_perm(request, "ec_settle_upload")
    if not u:
        return JSONResponse({"error": "需要「上传结算流水/跑批」权限"}, status_code=403)
    if not (period and len(period) == 7):
        return JSONResponse({"error": "结算期格式应为 YYYY-MM"}, status_code=400)
    if not _RUN_LOCK.acquire(blocking=False):
        return JSONResponse({"error": "已有跑批在进行，请等它结束"}, status_code=409)
    flow_bytes = await flow.read()
    refund_bytes = await refunds.read() if refunds is not None else None
    fnames = flow.filename + (("；" + refunds.filename) if refunds is not None else "")
    with db._engine.begin() as cx:
        rid = cx.execute(insert(db.ec_settle_runs).values(
            shop=shop, period=period, status="running", stats="{}",
            filenames=fnames, operator=u["name"], ts=_now())).inserted_primary_key[0]
    _RUNS[rid] = {"step": "已接收文件，解析流水中…", "error": ""}
    threading.Thread(target=_run_job, daemon=True,
                     args=(rid, shop, period, ar_from, flow_bytes, refund_bytes, u["name"])).start()
    return {"ok": True, "run_id": rid}


def _run_job(rid, shop, period, ar_from, flow_bytes, refund_bytes, operator):
    try:
        _RUNS[rid]["step"] = "解析流水中…"
        flow_rows = es.parse_flow_any(flow_bytes)
        _run_core(rid, shop, period, flow_rows, refund_bytes, operator, ar_from=ar_from)
    except Exception as e:
        _fail_run(rid, e)
    finally:
        _RUN_LOCK.release()


def _fail_run(rid, e):
    """失败要说失败，不静默（铁律）——库里记、页面显、钉钉/邮件也提醒。"""
    _RUNS[rid]["error"] = str(e)[:300]
    with db._engine.begin() as cx:
        cx.execute(db.ec_settle_runs.update().where(db.ec_settle_runs.c.id == rid)
                   .values(status="error", stats=json.dumps({"error": str(e)[:300]}, ensure_ascii=False)))
        run = cx.execute(select(db.ec_settle_runs).where(db.ec_settle_runs.c.id == rid)).first()
    _ec_notify("电商核销失败", "【电商对账】%s %s 跑批失败" % (run.shop, run.period),
               "失败原因：%s\n操作人：%s（去 收款核销›文件导入 检查数据后重跑）" % (str(e)[:200], run.operator),
               run.operator)


def _run_core(rid, shop, period, flow_rows, refund_bytes, operator, ar_from=""):
    """手工/自动共用的跑批核心：退款解析 → 金蝶整段拉取 → 引擎 → 写库。"""
    st = _RUNS[rid]
    st["step"] = "流水 %d 行已解析，解析退款不退货…" % len(flow_rows)
    refund_map, n_rk = es.parse_refunds(refund_bytes) if refund_bytes else ({}, 0)

    # 金蝶应收：优先用「刷新」缓存（半自动——谁/何时刷的可追溯）；没刷过就实时拉。
    meta = _kd_cache_meta(period)
    if meta and not ar_from:
        st["step"] = "读取金蝶应收缓存（%s %s 刷新，%d 行）…" % (meta["operator"], meta["ts"], meta["rows"])
        with open(_kd_cache_path(period), encoding="utf-8") as f:
            ar_rows = json.load(f)
        ar_src = "缓存·%s %s" % (meta["operator"], meta["ts"])
    else:
        st["step"] = "从金蝶整段拉取电商应收（只读）…"
        # 期间闸：应收只认结算期末以前（确认书⑤ 5.2）。起点默认往前 6 个月覆盖跨月发货。
        y, m = int(period[:4]), int(period[5:7])
        import calendar
        end = "%04d-%02d-%02d" % (y, m, calendar.monthrange(y, m)[1])
        if not ar_from:
            fy, fm = (y - 1, m + 6) if m <= 6 else (y, m - 6)
            ar_from = "%04d-%02d-01" % (fy, fm)
        ar_rows = kc.fetch_ec_receivables(ar_from, end)
        ar_src = "实时拉取"
    st["step"] = "应收 %d 行已到，逐单核销中…" % len(ar_rows)

    fee_map = {r["code"]: r for r in _rows(db.ec_fee_map)}
    rules = db.get_setting("ec_settle_rules", None) or {}
    # ④平台保证金明细（已上传即用）：结算后客服退款走保证金池，不并入则永远假差异
    deposit_map, n_dep = {}, 0
    dep_path = _manual_path(period, "平台保证金", shop)
    if os.path.isfile(dep_path):
        st["step"] = "解析保证金流水明细…"
        deposit_map, n_dep = es.parse_deposit(open(dep_path, "rb").read())
    res = es.run(flow_rows, refund_map, es.index_receivables(ar_rows),
                 fee_map, rules, shop=shop, deposit_map=deposit_map)
    res["stats"]["deposit_rows"] = n_dep
    res["stats"]["refund_rows"] = n_rk
    res["stats"]["flow_rows"] = len(flow_rows)
    res["stats"]["ar_rows"] = len(ar_rows)
    res["stats"]["ar_src"] = ar_src

    st["step"] = "写库中…"
    with db._engine.begin() as cx:
        # 同店同期重跑=覆盖旧结果（保留 runs 行当历史，orders/fees/excluded 只留最新）
        old = [r[0] for r in cx.execute(
            select(db.ec_settle_runs.c.id).where(db.ec_settle_runs.c.shop == shop)
            .where(db.ec_settle_runs.c.period == period)
            .where(db.ec_settle_runs.c.id != rid))]
        for t in (db.ec_settle_orders, db.ec_settle_fees, db.ec_excluded):
            if old:
                cx.execute(delete(t).where(t.c.run_id.in_(old)))
        for o in res["orders"]:
            cx.execute(insert(db.ec_settle_orders).values(run_id=rid, **o))
        for f in res["fees"]:
            cx.execute(insert(db.ec_settle_fees).values(run_id=rid, **f))
        for e in res["excluded"]:
            cx.execute(insert(db.ec_excluded).values(run_id=rid, **e))
        cx.execute(db.ec_settle_runs.update().where(db.ec_settle_runs.c.id == rid)
                   .values(status="done", stats=json.dumps(res["stats"], ensure_ascii=False)))
    db.audit(operator, "ec_settle_run", target="%s %s" % (shop, period),
             detail="流水%d行 应收%d行 订单%d" % (len(flow_rows), len(ar_rows), res["stats"]["orders"]))
    st["step"] = "完成"
    # 跑批完成 → 钉钉/邮件摘要（照工作台惯例；通道未配置时 notifier 自行回执不发）
    bk = res["stats"]["buckets"]
    g = lambda k: (bk.get(k) or {}).get("cnt", 0)
    _ec_notify("电商核销完成",
               "【电商对账】%s %s 核销完成" % (shop, period),
               "结算订单 %s 单（流水 %s 行 × 金蝶应收 %s 行）\n"
               "可核销 %s · 真差异 %s · 串单嫌疑 %s · U先 %s · 跨期调节 %s\n"
               "平台收入 %.2f · 应收命中 %.2f · 退款调节 %.2f%s\n"
               "操作人：%s（结果见 应收模块›电商对账›收款核销）"
               % (res["stats"]["orders"], len(flow_rows), len(ar_rows),
                  g("ok"), g("real"), g("crossed"), g("ufirst"), g("carry"),
                  res["stats"]["plat_amt"], res["stats"]["ar_amt"], res["stats"]["rk_amt"],
                  ("\n⚠ %d 个费目码未映射科目，请去基础资料补齐" % res["stats"]["unmapped_codes"])
                  if res["stats"].get("unmapped_codes") else "",
                  operator), operator)


# ==================== 事件索引（V2.269：时间链业务全链——下单/付款/发货） ====================
def _event_index_path(period, shop):
    return os.path.join(_shop_dir(period, shop), "_事件索引.json")


def _build_event_index(period, shop):
    """从④平台订单 + ③销售出库建 {单号: {created,paid,shipped,ck,jy}}，落盘缓存。"""
    idx = {}
    p = _manual_path(period, "平台订单", shop)
    if os.path.isfile(p):
        for o, ev in es.parse_platform_events(open(p, "rb").read()).items():
            idx.setdefault(o, {}).update(ev)
    p = _manual_path(period, "销售出库", shop)
    if os.path.isfile(p):
        for o, ev in es.parse_ship_events(open(p, "rb").read()).items():
            idx.setdefault(o, {}).update(ev)
    out = _event_index_path(period, shop)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False)
    return idx


def _event_index(period, shop):
    p = _event_index_path(period, shop)
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return _build_event_index(period, shop)


def _event_index_if_any(period, shop):
    """有索引或有④/③文件才读——别为没数据的月份凭空建目录（V2.271 跨月回看用）。"""
    if (os.path.isfile(_event_index_path(period, shop))
            or os.path.isfile(_manual_path(period, "平台订单", shop))
            or os.path.isfile(_manual_path(period, "销售出库", shop))):
        return _event_index(period, shop)
    return {}


# ==================== 操作详情抽屉（V2.265：逐单核销点行看档案——把人工五路排查机器化） ====================
@router.get("/api/ec/settle/order-detail")
def ec_order_detail(request: Request, run_id: int, order_no: str):
    """一张订单的完整档案：核销行 + 金蝶蓝红逐行（勾稽字段透出）+ 红字源单RK反查退货链 +
    同发货JY下的伙伴蓝字（串单侦探自动化——2026-08-11 对 5123182287333018342 的人工解析路径固化）。"""
    if not _require_perm(request, "enter:ecomsettle"):
        return JSONResponse({"error": "无权限"}, status_code=403)
    order_no = str(order_no).strip().replace("'", "")[:64]
    with db._engine.begin() as cx:
        row = cx.execute(select(db.ec_settle_orders)
                         .where(db.ec_settle_orders.c.run_id == run_id)
                         .where(db.ec_settle_orders.c.order_no == order_no)).first()
        run = cx.execute(select(db.ec_settle_runs).where(db.ec_settle_runs.c.id == run_id)).first()
    if not row or not run:
        return JSONResponse({"error": "订单不在该次跑批里"}, status_code=404)
    import calendar
    y, m = int(run.period[:4]), int(run.period[5:7])
    end = "%04d-%02d-%02d" % (y, m, calendar.monthrange(y, m)[1])
    s, conf = kc.login()
    F = [("FBillNo", "bill"), ("FDate", "date"), ("FMATERIALID.FNumber", "mat"),
         ("FMATERIALID.FName", "mat_name"), ("FPriceQty", "qty"), ("FALLAMOUNTFOR_D", "amt"),
         ("F_ora_Text3", "t3"), ("F_ora_Text4", "t4"), ("F_ora_Text6", "t6"), ("FSourceBillNo", "src")]
    ar = kc._query(s, conf, "AR_receivable", F,
                   "(F_ora_Text6 like '%%%s%%' or F_ora_Text4='%s') and FDATE<='%s'" % (order_no, order_no, end))
    # 红字源单 RK → 销售退货单反查（退换单号/原始单号/JY）
    returns = []
    jys = set()
    seen_rk = set()
    for r in ar:
        src = str(r.get("src") or "")
        if float(r.get("amt") or 0) < 0 and str(r.get("t3") or "").startswith("JY"):
            jys.add(str(r["t3"]))
        if src.startswith("RK") and src not in seen_rk:
            seen_rk.add(src)
            F2 = [("FBillNo", "bill"), ("FDate", "date"), ("FMaterialId.FNumber", "mat"),
                  ("FMaterialId.FName", "mat_name"), ("FRealQty", "qty"),
                  ("F_ora_Text", "orig"), ("F_ora_Text1", "jy"), ("F_ora_Text2", "tk")]
            for x in kc._query(s, conf, "SAL_RETURNSTOCK", F2, "FBillNo='%s'" % src.replace("'", "")):
                returns.append(x)
                if x.get("jy"):
                    jys.add(str(x["jy"]))
    # 同发货 JY 下的伙伴蓝字（蓝字 T4=JY）——串单时它挂在别的订单名下，这里自动带出
    partners = []
    have = {(r["bill"], r["mat"], r["amt"]) for r in ar}
    for jy in sorted(jys):
        for r in kc._query(s, conf, "AR_receivable", F,
                           "F_ora_Text4='%s' and FDATE<='%s'" % (jy.replace("'", ""), end)):
            if (r["bill"], r["mat"], r["amt"]) not in have:
                partners.append(r)
    # 业务事件（下单/付款/发货）——④平台订单+③销售出库的事件索引（上传后后台预建，此处秒读）
    biz = dict(_event_index(run.period, run.shop).get(order_no) or {})
    # V2.271 跨月回看：上月下单/发货、本月确认收货才结算是常态（可核销桶里月初到账的大都是上月单）——
    # 本期索引没有的键回看上月（该月④/③传了才读；本期已有的值优先）
    y_, m_ = int(run.period[:4]), int(run.period[5:7])
    prev = "%04d-12" % (y_ - 1) if m_ == 1 else "%04d-%02d" % (y_, m_ - 1)
    if not biz.get("created") or not biz.get("shipped"):
        pb = _event_index_if_any(prev, run.shop).get(order_no) or {}
        biz = {**pb, **{k: v for k, v in biz.items() if v}}
    # V2.270 节点缺失要说实话：区分「文件没传」和「文件里没这张单」两种可证原因；
    # V2.271 再补可行动的一句：蓝字应收在上月且上月④/③未传 → 提示期间切到上月补传
    biz_gap = {}
    ar_min = min((str(r.get("date"))[:7] for r in ar if float(r.get("amt") or 0) > 0), default="")
    if not biz.get("created"):
        biz_gap["order"] = ("not_in_file" if os.path.isfile(_manual_path(run.period, "平台订单", run.shop))
                            else "no_file")
        if ar_min and ar_min < run.period and not os.path.isfile(_manual_path(prev, "平台订单", run.shop)):
            biz_gap["order_prev"] = prev
    if not biz.get("shipped"):
        biz_gap["ship"] = ("not_in_file" if os.path.isfile(_manual_path(run.period, "销售出库", run.shop))
                           else "no_file")
        if ar_min and ar_min < run.period and not os.path.isfile(_manual_path(prev, "销售出库", run.shop)):
            biz_gap["ship_prev"] = prev
    return {"order": dict(row._mapping), "period": run.period,
            "ar_rows": ar, "returns": returns, "partners": partners, "biz": biz, "biz_gap": biz_gap}


# ==================== 通知（V2.264：照物流线 V2.230/231 惯例——分场景收件人+改动须口令） ====================
_EC_NOTIFY_SCENES = [
    ("电商核销完成", "跑批完成后的结果摘要（订单数/分桶计数/费目待映射提醒）"),
    ("电商核销失败", "跑批失败提醒（含失败原因，便于当天补救）"),
]


def _notify_passcode():
    """改收件人的口令——conf.ini [notify] passcode（与汇率/物流线同一把；机密不下发前端）。未配置=空串。"""
    import configparser
    p = kc.conf_path()
    if not p:
        return ""
    c = configparser.ConfigParser()
    c.read(p, encoding="utf-8")
    return (c.get("notify", "passcode", fallback="") or "").strip()


def _ec_notify(scene, subject, text, operator=""):
    """跑批钩子：发场景通知。通知失败绝不影响跑批结果（只记审计）。
    ⚠通知铁律：本函数只调 notifier.notify——通道未配置时其自行回执不发，不在代码里造真实收件人。"""
    import notifier
    try:
        res = notifier.notify(subject, text, scene=scene)
    except Exception as e:
        res = {"error": str(e)}
    try:
        db.audit(operator or "系统", "电商对账-通知", scene, str(res)[:200])
    except Exception:
        pass


# ==================== 与已入账凭证核对（V2.278：账已做时，凭证预览变复核——找记-号、逐费目对） ====================
@router.get("/api/ec/settle/voucher-check")
def ec_voucher_check(request: Request, run_id: int):
    """只读查 GL_VOUCHER：按 期间+账簿+摘要(金蝶客户名+SKD收款单号) 找本店结算凭证，
    与本次跑批做三层核对——净到账(借1012) / 贷应收(1122) / 费用逐项（金额精确配对）。
    2026-08-13 实证：春艳按店一店一张、收款单下推（SKD 单号在摘要里），费用行=费目收支相抵净额、两资金账户合并。"""
    if not _require_perm(request, "enter:ecomsettle"):
        return JSONResponse({"error": "无权限"}, status_code=403)
    with db._engine.begin() as cx:
        run = cx.execute(select(db.ec_settle_runs).where(db.ec_settle_runs.c.id == run_id)).first()
        fees = [dict(r._mapping) for r in cx.execute(
            select(db.ec_settle_fees).where(db.ec_settle_fees.c.run_id == run_id)).fetchall()]
    if not run or run.status != "done":
        return JSONResponse({"error": "跑批不存在或未完成"}, status_code=400)
    stats = json.loads(run.stats or "{}")
    y, m = int(run.period[:4]), int(run.period[5:7])
    srow = next((r for r in _rows(db.ec_shop_map) if r["wdt_name"] == run.shop), None)
    kd_name = ((srow or {}).get("kd_name") or run.shop).replace("'", "")
    book = str((db.get_setting("ec_voucher_cfg", {}) or {}).get("book_code") or "101").replace("'", "")
    s, conf = kc.login()
    F = [("FBillNo", "no"), ("FDATE", "date"), ("FVOUCHERGROUPNO", "gno"),
         ("FVOUCHERGROUPID.FName", "grp"), ("FEXPLANATION", "memo"),
         ("FACCOUNTID.FNumber", "acct"), ("FACCOUNTID.FName", "acct_name"),
         ("FDEBIT", "dr"), ("FCREDIT", "cr"), ("FCREATORID.FName", "maker"),
         # 审核状态（需求方定）：FDOCUMENTSTATUS C=已审核；审核人/时间=FCHECKERID/FAUDITDATE
         # （2026-08-13 实测本账套字段名；FPOSTED/FAUDITORID 不存在，别回头再猜）
         ("FDOCUMENTSTATUS", "st"), ("FCHECKERID.FName", "checker"), ("FAUDITDATE", "audit_dt")]
    rows = kc._query(s, conf, "GL_VOUCHER", F,
                     "FYEAR=%d and FPERIOD=%d and FACCOUNTBOOKID.FNumber='%s' and "
                     "FEXPLANATION like '%%%s%%' and FEXPLANATION like '%%SKD%%'" % (y, m, book, kd_name),
                     "FBillNo")
    if not rows:
        return {"found": False,
                "msg": "%s年%s期 %s簿里没找到摘要含「%s」+SKD收款单号的凭证——本期可能还没做账，或客户名/账簿与基础资料不一致。" % (y, m, book, kd_name)}
    by = {}
    for r in rows:
        by.setdefault(r["no"], []).append(r)
    vouchers, related = [], []                         # 结算凭证（动了1012/1122）/ 摘要相关的调整类凭证
    their_fees = []                                    # (凭证号, 金额)：客户往来贷方=她的费用净额行
    t_recv = t_ar = 0.0
    for no, rs in sorted(by.items()):
        skd = re.search(r"SKD\d+", str(rs[0]["memo"]))
        dr1012 = round(sum(float(r["dr"] or 0) for r in rs if str(r["acct"]).startswith("1012")), 2)
        cr1122 = round(sum(float(r["cr"] or 0) for r in rs if str(r["acct"]).startswith("1122")), 2)
        st = str(rs[0].get("st") or "")
        v = {"bill_no": no, "gno": rs[0]["gno"], "grp": rs[0]["grp"],
             "date": str(rs[0]["date"])[:10], "maker": rs[0]["maker"],
             "skd": skd.group(0) if skd else "", "lines": len(rs),
             "status": {"Z": "暂存", "A": "已创建", "B": "审核中", "C": "已审核", "D": "重新审核"}.get(st, st or "未知"),
             "audited": st == "C", "checker": rs[0].get("checker") or "",
             "audit_dt": str(rs[0].get("audit_dt") or "")[:10],
             "dr_1012": dr1012, "cr_1122": cr1122, "memo": str(rs[0]["memo"])[:60]}
        if not dr1012 and not cr1122:                  # 只是摘要里提到了 SKD 的调整凭证——列出但不进核对口径
            related.append(v)
            continue
        t_recv += dr1012
        t_ar += cr1122
        their_fees += [(no, round(float(r["cr"]), 2)) for r in rs
                       if str(r["acct"]).startswith("2241") and float(r["cr"] or 0) > 0]
        vouchers.append(v)
    if not vouchers:
        return {"found": False, "related": related,
                "msg": "找到 %d 张摘要相关凭证，但没有动 1012/1122 的结算凭证——本期结算可能还没入账。" % len(related)}
    # 我们侧：费目净额（两资金账户按费目码合并；收支相抵）——只取科目=费用类；净到账=全部费目收支轧差
    ours = {}
    net_recv = 0.0
    for f in fees:
        net_recv += float(f["income"] or 0) - float(f["outgo"] or 0)
        if "费用" not in str(f["account"] or "") and str(f["account"] or "") != "待定":
            continue
        o = ours.setdefault(f["code"], {"code": f["code"], "label": f["label"], "net": 0.0})
        o["net"] = round(o["net"] + float(f["outgo"] or 0) - float(f["income"] or 0), 2)
    our_fees = [o for o in ours.values() if abs(o["net"]) > 0.005]
    # 金额精确配对（各用一次）；剩余两侧并列——她的收款单可能把几个费目并作一行，合计对上即口径差异
    pool = list(their_fees)
    matched, un_ours = [], []
    for o in sorted(our_fees, key=lambda x: -abs(x["net"])):
        hit = next((t for t in pool if abs(t[1] - o["net"]) < 0.005), None)
        if hit:
            pool.remove(hit)
            matched.append({**o, "voucher": hit[0]})
        else:
            un_ours.append(o)
    return {"found": True, "vouchers": vouchers, "related": related,
            "totals": {"recv_theirs": round(t_recv, 2), "recv_ours": round(net_recv, 2),
                       "ar_theirs": round(t_ar, 2), "ar_ours": stats.get("ar_amt"),
                       "fee_theirs": round(sum(a for _, a in their_fees), 2),
                       "fee_ours": round(sum(o["net"] for o in our_fees), 2)},
            "matched": matched, "un_ours": un_ours,
            "un_theirs": [{"voucher": no, "amount": a} for no, a in pool]}


# ==================== 剔除留痕·定性登记（V2.274：识别出来的要有下文——活动/违规逐笔记档） ====================
@router.get("/api/ec/settle/excl-notes")
def ec_excl_notes_get(request: Request, period: str, shop: str):
    if not _require_perm(request, "enter:ecomsettle"):
        return JSONResponse({"error": "无权限"}, status_code=403)
    with db._engine.begin() as cx:
        rows = cx.execute(select(db.ec_excl_notes)
                          .where(db.ec_excl_notes.c.period == period)
                          .where(db.ec_excl_notes.c.shop == shop)).fetchall()
    return {"notes": {r.serial: {"verdict": r.verdict, "note": r.note or "",
                                 "operator": r.operator or "", "ts": r.ts or ""} for r in rows}}


@router.post("/api/ec/settle/excl-note")
async def ec_excl_note_save(request: Request):
    u = _require_perm(request, "ec_settle_upload")
    if not u:
        return JSONResponse({"error": "需要「上传结算流水/跑批」权限"}, status_code=403)
    body = await request.json()
    period = str(body.get("period") or "").strip()[:10]
    shop = str(body.get("shop") or "").strip()[:120]
    serial = str(body.get("serial") or "").strip()[:40]
    if not (period and shop and serial):
        return JSONResponse({"error": "缺期间/店铺/流水号"}, status_code=400)
    verdict = str(body.get("verdict") or "").strip()[:10]
    if verdict and verdict not in ("正常", "违规"):
        return JSONResponse({"error": "判定只能是 正常 / 违规（空=撤销登记）"}, status_code=400)
    with db._engine.begin() as cx:
        cx.execute(delete(db.ec_excl_notes)
                   .where(db.ec_excl_notes.c.period == period)
                   .where(db.ec_excl_notes.c.shop == shop)
                   .where(db.ec_excl_notes.c.serial == serial))
        if verdict:                                    # 空判定=撤销登记（删记录，审计留一笔）
            cx.execute(insert(db.ec_excl_notes).values(
                period=period, shop=shop, serial=serial,
                kind=str(body.get("kind") or "")[:60], flow_ts=str(body.get("flow_ts") or "")[:20],
                amount=float(body.get("amount") or 0),
                verdict=verdict, note=str(body.get("note") or "").strip()[:200],
                operator=u["name"], ts=_now()))
    db.audit(u["name"], "ec_excl_note", target="%s %s %s" % (period, shop, serial),
             detail=("%s %s" % (verdict, str(body.get("note") or "")))[:80] if verdict else "撤销登记")
    return {"ok": True}


@router.post("/api/ec/settle/excl-notes-batch")
async def ec_excl_notes_batch(request: Request):
    """批量定性（V2.275 需求方定：活动一场就是一串流水，勾选后一次登记同一判定+说明）。
    verdict 空=批量撤销。单事务写入，审计合记一条。"""
    u = _require_perm(request, "ec_settle_upload")
    if not u:
        return JSONResponse({"error": "需要「上传结算流水/跑批」权限"}, status_code=403)
    body = await request.json()
    period = str(body.get("period") or "").strip()[:10]
    shop = str(body.get("shop") or "").strip()[:120]
    verdict = str(body.get("verdict") or "").strip()[:10]
    note = str(body.get("note") or "").strip()[:200]
    items = [it for it in (body.get("items") or []) if str(it.get("serial") or "").strip()][:500]
    if not (period and shop and items):
        return JSONResponse({"error": "缺期间/店铺/勾选明细"}, status_code=400)
    if verdict and verdict not in ("正常", "违规"):
        return JSONResponse({"error": "判定只能是 正常 / 违规（空=撤销登记）"}, status_code=400)
    with db._engine.begin() as cx:
        for it in items:
            serial = str(it.get("serial")).strip()[:40]
            cx.execute(delete(db.ec_excl_notes)
                       .where(db.ec_excl_notes.c.period == period)
                       .where(db.ec_excl_notes.c.shop == shop)
                       .where(db.ec_excl_notes.c.serial == serial))
            if verdict:
                cx.execute(insert(db.ec_excl_notes).values(
                    period=period, shop=shop, serial=serial,
                    kind=str(it.get("kind") or "")[:60], flow_ts=str(it.get("flow_ts") or "")[:20],
                    amount=float(it.get("amount") or 0),
                    verdict=verdict, note=note, operator=u["name"], ts=_now()))
    db.audit(u["name"], "ec_excl_note_batch", target="%s %s" % (period, shop),
             detail=("批量%s %d 笔 %s" % (verdict or "撤销登记", len(items), note))[:80])
    return {"ok": True, "n": len(items)}


@router.get("/api/ec/settle/notify-recipients")
def ec_notify_recipients(request: Request):
    u = _require_perm(request, "ec_settle_upload")
    if not u:
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    import notifier
    import mailer
    saved = {r["scene"]: r for r in db.list_notify_recipients()}
    scenes = [{"scene": s, "desc": d,
               "mobiles": (saved.get(s) or {}).get("mobiles") or "",
               "emails": (saved.get(s) or {}).get("emails") or "",
               "updated_by": (saved.get(s) or {}).get("updated_by") or "",
               "updated_at": (saved.get(s) or {}).get("updated_at") or ""} for s, d in _EC_NOTIFY_SCENES]
    dt = notifier.load_dingtalk_conf()
    sm = mailer.load_smtp_conf()
    fallback = {"mobiles": (dt or {}).get("mobiles") or [], "emails": (sm or {}).get("to") or [],
                "dingtalk_ready": bool(dt), "email_ready": bool(sm)}
    return {"ok": True, "scenes": scenes, "fallback": fallback,
            "passcode_set": bool(_notify_passcode())}


@router.post("/api/ec/settle/notify-recipients")
async def ec_notify_recipients_save(request: Request):
    u = _require_perm(request, "ec_settle_upload")
    if not u:
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    body = await request.json()
    scene = str(body.get("scene") or "").strip()
    if scene not in {s for s, _d in _EC_NOTIFY_SCENES}:
        return {"ok": False, "msg": "不认识的场景：%s" % scene}
    pc = _notify_passcode()
    if not pc:
        return {"ok": False, "msg": "后端未设置通知口令（conf.ini [notify] passcode 为空），暂不能从页面改收件人，请联系管理员配置。"}
    if str(body.get("passcode") or "").strip() != pc:
        db.audit(u["name"], "电商对账-通知收件人", scene, "口令错误，未保存")
        return JSONResponse({"ok": False, "msg": "口令错误，未保存"}, status_code=403)
    db.save_notify_recipients(scene, body.get("mobiles") or "", body.get("emails") or "", u["name"])
    db.audit(u["name"], "电商对账-通知收件人", scene,
             "钉钉[%s] 邮件[%s]" % (body.get("mobiles") or "（空→公共名单）", body.get("emails") or "（空→公共名单）"))
    return {"ok": True}


@router.post("/api/ec/settle/notify-test")
async def ec_notify_test(request: Request):
    """按当前配置真发一条测试——通道未配置/禁用时只回执不发（通知铁律）。"""
    u = _require_perm(request, "ec_settle_upload")
    if not u:
        return JSONResponse({"ok": False, "msg": "无权限"}, status_code=403)
    body = await request.json()
    scene = str(body.get("scene") or "").strip()
    import notifier
    try:
        res = notifier.notify("【测试】财务核算工作台·%s通知连通测试" % scene,
                              "%s 于 %s 在「收款核销 › 通知设置」发送测试。收到本条即该场景收件人配置正确。" % (u["name"], _now()),
                              scene=scene)
    except Exception as e:
        res = {"error": str(e)}
    db.audit(u["name"], "电商对账-通知测试", scene, str(res)[:200])
    return {"ok": True, "notify": res}


# ==================== 一键录入结算凭证（V2.257：写金蝶=草稿 only，配置驱动） ====================
@router.post("/api/ec/settle/post-voucher")
async def ec_post_voucher(request: Request):
    """两张结算凭证（扣款项/收款核销）→ 金蝶草稿。提交/审核人在金蝶做；防重靠 ec_post_log
    （独立台账，不碰物流 post_log——立项分析红线三的回避路径）。"""
    u = _require_perm(request, "ec_post")
    if not u:
        return JSONResponse({"error": "需要「一键录入结算凭证」权限（敏感，须显式授予）"}, status_code=403)
    body = await request.json()
    rid = int(body.get("run_id") or 0)
    with db._engine.begin() as cx:
        run = cx.execute(select(db.ec_settle_runs).where(db.ec_settle_runs.c.id == rid)).first()
        fees = [dict(r._mapping) for r in cx.execute(
            select(db.ec_settle_fees).where(db.ec_settle_fees.c.run_id == rid))]
        posted = [dict(r._mapping) for r in cx.execute(
            select(db.ec_post_log).where(db.ec_post_log.c.period == (run.period if run else ""))
            .where(db.ec_post_log.c.shop == (run.shop if run else "")))]
    if not run or run.status != "done":
        return JSONResponse({"error": "跑批不存在或未完成"}, status_code=400)
    if posted:
        return JSONResponse({"error": "本期本店已录过（凭证内码 %s）——防重复记账；确需重录请先在金蝶删草稿并联系管理员清台账"
                             % "、".join(p["kd_id"] for p in posted)}, status_code=409)
    # 费目→科目编码（fee_map.kd_code）+ 凭证配置（settings）
    fm = {r["code"]: r for r in _rows(db.ec_fee_map)}
    for f in fees:
        f["kd_code"] = (fm.get(f["code"]) or {}).get("kd_code") or ""
    cfg = db.get_setting("ec_voucher_cfg", {}) or {}
    try:
        vouchers = es.build_settle_vouchers(fees, json.loads(run.stats or "{}"), cfg, run.period)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    results = []
    for v in vouchers:
        res = kc.save_voucher(v["model"])            # 只建草稿；含 FDate 顺序坑的报文由内核保证
        if not res.get("ok"):
            return JSONResponse({"error": "「%s」凭证保存失败：%s（已录 %s）"
                                 % (v["kind"], res.get("msg"), "、".join(r["kind"] for r in results) or "无")},
                                status_code=502)
        with db._engine.begin() as cx:
            cx.execute(insert(db.ec_post_log).values(
                run_id=rid, period=run.period, shop=run.shop, kind=v["kind"],
                kd_id=str(res.get("id") or ""), amount=v["amount"], operator=u["name"], ts=_now()))
        results.append({"kind": v["kind"], "kd_id": str(res.get("id") or ""), "amount": v["amount"]})
    db.audit(u["name"], "ec_post_voucher", target="%s %s" % (run.shop, run.period),
             detail=json.dumps(results, ensure_ascii=False))
    return {"ok": True, "vouchers": results}


@router.get("/api/ec/settle/post-status")
def ec_post_status(request: Request, run_id: int):
    if not _require_perm(request, "enter:ecomsettle"):
        return JSONResponse({"error": "无权限"}, status_code=403)
    with db._engine.begin() as cx:
        run = cx.execute(select(db.ec_settle_runs).where(db.ec_settle_runs.c.id == run_id)).first()
        posted = [dict(r._mapping) for r in cx.execute(
            select(db.ec_post_log).where(db.ec_post_log.c.period == (run.period if run else ""))
            .where(db.ec_post_log.c.shop == (run.shop if run else "")))]
    cfg = db.get_setting("ec_voucher_cfg", {}) or {}
    fm = _rows(db.ec_fee_map)
    nocode = sorted({r["code"] for r in fm if not (r.get("kd_code") or "").strip()
                     and r["code"] not in ("0010001", "0020001")})
    missing = [k for k in ("book_code", "voucher_group", "currency", "cash_acct", "ar_acct")
               if not str(cfg.get(k) or "").strip()]
    return {"posted": posted, "cfg_missing": missing, "codes_missing": nocode}


@router.get("/api/ec/settle/progress")
def ec_settle_progress(request: Request, run_id: int):
    if not _require_perm(request, "enter:ecomsettle"):
        return JSONResponse({"error": "无权限"}, status_code=403)
    with db._engine.begin() as cx:
        row = cx.execute(select(db.ec_settle_runs).where(db.ec_settle_runs.c.id == run_id)).first()
    if not row:
        return JSONResponse({"error": "跑批不存在"}, status_code=404)
    mem = _RUNS.get(run_id, {})
    return {"status": row.status, "step": mem.get("step", ""), "error": mem.get("error", ""),
            "stats": json.loads(row.stats or "{}")}


# ==================== 结果 ====================
@router.get("/api/ec/settle/runs")
def ec_settle_runs_list(request: Request):
    if not _require_perm(request, "enter:ecomsettle"):
        return JSONResponse({"error": "无权限"}, status_code=403)
    with db._engine.begin() as cx:
        rows = [dict(r._mapping) for r in cx.execute(
            select(db.ec_settle_runs).order_by(db.ec_settle_runs.c.id.desc()).limit(24))]
    for r in rows:
        r["stats"] = json.loads(r.pop("stats") or "{}")
    return {"runs": rows}


@router.get("/api/ec/settle/result")
def ec_settle_result(request: Request, run_id: int, bucket: str = "", page: int = 1, size: int = 50,
                     shop: str = "", order_no: str = "", ar_no: str = "", serial_no: str = ""):
    """结果查询。筛选（V2.267 需求方定）：店铺 / 平台订单号 / 应收单号 / 流水号（后三者模糊匹配）。"""
    if not _require_perm(request, "enter:ecomsettle"):
        return JSONResponse({"error": "无权限"}, status_code=403)
    t = db.ec_settle_orders
    conds = [t.c.run_id == run_id]
    if bucket:
        conds.append(t.c.bucket == bucket)
    if shop.strip():
        conds.append(t.c.shop == shop.strip())
    for col, val in ((t.c.order_no, order_no), (t.c.ar_no, ar_no), (t.c.serial_no, serial_no)):
        v = str(val or "").strip().replace("%", "").replace("_", "")
        if v:
            conds.append(col.like("%" + v + "%"))
    q = select(t)
    cq = select(t.c.id)
    for c in conds:
        q = q.where(c)
        cq = cq.where(c)
    page, size = max(1, page), min(200, max(10, size))
    with db._engine.begin() as cx:
        total = len(cx.execute(cq).fetchall())
        orders = [dict(r._mapping) for r in cx.execute(
            q.order_by(t.c.diff.desc(), t.c.id).offset((page - 1) * size).limit(size))]
        fees = [dict(r._mapping) for r in cx.execute(
            select(db.ec_settle_fees).where(db.ec_settle_fees.c.run_id == run_id))]
        excluded = [dict(r._mapping) for r in cx.execute(
            select(db.ec_excluded).where(db.ec_excluded.c.run_id == run_id))]
    for e in excluded:
        e["detail"] = json.loads(e.pop("detail") or "[]")
    return {"orders": orders, "total": total, "page": page, "size": size,
            "fees": fees, "excluded": excluded}
