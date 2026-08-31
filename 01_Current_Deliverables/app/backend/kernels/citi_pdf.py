# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-07-06 | Author: Claude / c | Version: V2.26
# Description: 花旗银行 PDF 对账单解析（补 task① 缺口：花旗美元/港币户流水此前无电子明细、走PDF）。
#              两种版式：
#                A) 亚洲账户对账单（中文列 余额/贷记/借记/说明/日期，多账户分页，含港币/美元/人民币户）
#                   —— 按词坐标分列判方向（借记列=支、贷记列=收），排除页顶「账单摘要」的期初/期末金额。
#                B) 银行对帐单 - US（英文定宽，DEBITS/CREDITS 分列，自带 TOTALS/CLOSING）。
#              逐户余额自校验（期初 + Σ贷 − Σ借 = 期末），**只并入校验通过的账户**，失败账户不并入并在
#              manifest 标 FAIL，透明可审计、不塞脏数据。金额为原币（与金蝶花旗户原币口径一致，可逐笔对上）。
#              统一输出行 schema 对齐 bank_import._row / reconcile.bank_to_recs：
#                账号 / 户名 / 交易日期 / 摘要 / 对方户名 / 收入 / 支出 / 余额 / 来源文件 / 银行 / 币种
from __future__ import annotations
import re

try:
    import fitz   # pymupdf（旧名）
except ImportError:
    try:
        import pymupdf as fitz   # 新版 PyMuPDF 只暴露 pymupdf 名
    except ImportError:   # 未装时优雅降级：is_citi_pdf 恒 False、parse 抛错（不崩、只跳过）
        fitz = None

_AMT = re.compile(r"-?[\d,]+\.\d{2}")


def _n(s):
    return float(str(s).replace(",", ""))


def _iso(d):
    m = re.match(r"(\d{2})/(\d{2})/(\d{2,4})", str(d or ""))
    if not m:
        return ""
    mm, dd, yy = m.groups()
    if len(yy) == 2:
        yy = "20" + yy
    return f"{yy}-{mm}-{dd}"


def is_citi_pdf(path):
    """内容嗅探（文件名多为 Unsaved-*，不能靠文件名）。读前两页文本认花旗版式。"""
    if fitz is None or not str(path).lower().endswith(".pdf"):
        return False
    try:
        doc = fitz.open(path)
        t = doc[0].get_text()
        if doc.page_count > 1:
            t += doc[1].get_text()
        doc.close()
    except Exception:
        return False
    return ("亚洲账户对账单" in t) or ("银行对帐单" in t and "ACCOUNT NAME" in t) or ("CITIBANK" in t)


def _parse_asia(doc):
    """亚洲对账单：多账户分页。按词坐标分列（借记列<360 / 贷记列<460 / 余额列≥460）。"""
    accts, cur = [], None
    def _close():
        if cur:
            accts.append(cur)
    for pi in range(doc.page_count):
        text = doc[pi].get_text()
        m_acc = re.search(r"账号\s*\n(?:[A-Z]*\s*\n)*\s*(\d{6,})", text)
        m_open = re.search(r"期初余额\s*\n\s*([A-Z]{3})\s+([\d,]+\.\d{2})", text)
        m_close = re.search(r"期末余额\s*\n\s*([A-Z]{3})\s+([\d,]+\.\d{2})", text)
        if m_open:                       # 新账户摘要页 → 收尾上一户、起新户
            _close()
            cur = {"acct": m_acc.group(1) if m_acc else "", "ccy": m_open.group(1),
                   "open": _n(m_open.group(2)), "close": _n(m_close.group(2)) if m_close else None,
                   "txns": []}
        # 词坐标：列头「借记」的 y 为表格起点，只取其下方行（排除页顶「账单摘要」期初/期末金额）
        words = doc[pi].get_text("words")
        hdr_y = min([w[1] for w in words if w[4] == "借记"], default=1e9)
        rows = {}
        for x0, y0, x1, y1, ww, *_ in words:
            if y0 <= hdr_y:
                continue
            rows.setdefault(round(y0 / 3) * 3, []).append(((x0 + x1) / 2, ww))
        for y in sorted(rows):
            deb = cred = date = None
            desc = []
            for xc, ww in sorted(rows[y]):
                if _AMT.fullmatch(ww):
                    if xc < 360:
                        deb = _n(ww)
                    elif xc < 460:
                        cred = _n(ww)
                    # ≥460 为余额列，忽略
                elif re.fullmatch(r"\d{2}/\d{2}/\d{4}", ww):
                    date = ww
                elif xc < 300 and ww not in ("期末余额", "余额结转", "余额", "贷记", "借记", "说明", "日期"):
                    desc.append(ww)
            if cur and (deb or cred):
                cur["txns"].append({"date": date, "desc": " ".join(desc), "收": cred or 0.0, "支": deb or 0.0})
    _close()
    return accts


def _parse_us(doc):
    """US 报告：英文定宽。一行 = 日期 + 批次号 + 描述 + (DEBITS 或 CREDITS) + LEDGER 余额。"""
    text = "\n".join(doc[p].get_text() for p in range(doc.page_count))
    acc = re.search(r"ACCOUNT\s+(\d{6,})", text)
    op = re.search(r"OPENING LEDGER BALANCE\s+([\d,]+\.\d{2})", text)
    cl = re.search(r"CLOSING LEDGER AS OF[^\n]*?([\d,]+\.\d{2})", text)
    cur = {"acct": acc.group(1) if acc else "", "ccy": "USD",
           "open": _n(op.group(1)) if op else 0.0, "close": _n(cl.group(1)) if cl else None, "txns": []}
    for m in re.finditer(r"(\d{2}/\d{2}/\d{2})\s+\d+\s+([A-Z][A-Z /]+?)\s{2,}([\d,]+\.\d{2})\s+([\d,]+\.\d{2})", text):
        date, desc, a1 = m.group(1), m.group(2).strip(), _n(m.group(3))
        isdeb = any(k in desc for k in ("DR TRANSFER", "DEBIT", "BILLING", "FEE", "CHARGE"))
        cur["txns"].append({"date": date, "desc": desc, "收": 0.0 if isdeb else a1, "支": a1 if isdeb else 0.0})
    return [cur]


def parse_citi_pdf(path):
    """→ (rows, report)。rows=标准银行行（仅自校验通过账户）；report=每户自校验信息（供 manifest 透明展示）。"""
    if fitz is None:
        raise RuntimeError("需要 pymupdf(fitz) 才能解析花旗 PDF；requirements.txt 已声明，请 pip install")
    doc = fitz.open(path)
    head = doc[0].get_text()
    accts = _parse_asia(doc) if "亚洲账户对账单" in head else _parse_us(doc)
    doc.close()
    rows, report = [], []
    for a in accts:
        sc = round(sum(t["收"] for t in a["txns"]), 2)
        sd = round(sum(t["支"] for t in a["txns"]), 2)
        comp = round(a["open"] + sc - sd, 2)
        ok = (a["close"] is not None) and abs(comp - a["close"]) < 0.01
        report.append({"账号": a["acct"], "币种": a["ccy"], "笔数": len(a["txns"]),
                       "期初": a["open"], "期末": a["close"], "算得期末": comp, "自校验": "OK" if ok else "FAIL"})
        if not ok or not a["txns"]:
            continue                    # 校验不过（或空）不并入，避免脏数据
        # 期末余额记在按日期最晚的一笔上，供余额调节取「银行对账单期末余额」
        last = max(range(len(a["txns"])), key=lambda i: _iso(a["txns"][i]["date"]) or "")
        for i, t in enumerate(a["txns"]):
            rows.append({"账号": a["acct"], "户名": "", "交易日期": _iso(t["date"]),
                         "摘要": t["desc"], "对方户名": "",
                         "收入": t["收"], "支出": t["支"],
                         "余额": (a["close"] if i == last else None),
                         "来源文件": path, "银行": "花旗银行", "币种": a["ccy"]})
    return rows, report
