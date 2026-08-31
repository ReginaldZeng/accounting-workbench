# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-07-04 | Author: Claude / c | Version: V2.6
# Description: 理财对账单解析内核（移植自隔壁 V1.15 wealth_statement.py，验证成熟）。
#   面向银行代销/托管出具的"财富对账单/持仓对账单/交易凭证"(多为扫描图片PDF，无文字层)。
#   流程：PDF渲染为图片(PyMuPDF，免 poppler 系统依赖) → 离线中文OCR(rapidocr，多dpi取并集)
#        → 正则抽取归一化记录 → 生成"稽核可用"交易(赎回=资金流入/申购=流出)。
#   可直接传 blocks(OCR文本列表)做无OCR单测。OCR 依赖(pymupdf/rapidocr)缺失时给清晰报错。
import os
import re

MONEY2 = r'\d{1,3}(?:,\d{3})*\.\d{2}'          # 金额(两位小数); OCR会把相邻单元格粘连，
                                               # 靠"以.dd结尾"天然切分：findall即可拆开
NAV_RE = r'\d\.\d{3,4}'                        # 净值(3~4位小数)
DATE_RE = r'20\d{2}-\d{2}-\d{2}'
TXN_KEYS = ["理财赎回", "赎回", "理财申购", "申购", "分红", "派息",
            "份额确认", "转投", "转出", "转入", "认购"]
ISSUER_KEYS = ["招商银行", "宁波银行", "浦发银行", "中国银行", "工商银行", "农业银行",
               "建设银行", "交通银行", "平安银行", "民生银行", "兴业银行", "光大银行",
               "中信银行", "华夏银行", "邮储银行", "花旗银行"]
WEALTH_KEYS = ["财富对账单", "持仓", "理财", "份额", "净值", "确认份额", "持仓市值", "净值日期",
               "交易凭证", "交易名称", "理财产品赎回", "理财产品申购", "网上银行"]


def _num(s):
    if s is None:
        return None
    s = str(s).replace(",", "").replace(" ", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


# ---------- 识别 ----------
def is_wealth_statement(text):
    """传入PDF原生文本或OCR拼接文本，判定是否为理财对账单(命中>=3个特征词)。"""
    if not text:
        return False
    return sum(1 for k in WEALTH_KEYS if k in text) >= 3


class OCRUnavailable(RuntimeError):
    pass


# ---------- OCR（PyMuPDF 渲染 + rapidocr 识别）----------
def pdf_to_images(pdf_path, dpi=200, outdir=None, max_pages=None):
    """PyMuPDF 把 PDF 各页渲染成 PNG（免 poppler 系统依赖）。返回 PNG 路径列表。"""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise OCRUnavailable("未安装 PyMuPDF（渲染理财PDF用）。请 pip install pymupdf")
    import tempfile
    outdir = outdir or tempfile.mkdtemp(prefix="wealth_")
    doc = fitz.open(pdf_path)
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pages = []
    n = doc.page_count if not max_pages else min(max_pages, doc.page_count)
    for i in range(n):
        pix = doc.load_page(i).get_pixmap(matrix=mat)
        png = os.path.join(outdir, f"page-{dpi}-{i + 1:03d}.png")
        pix.save(png)
        pages.append(png)
    doc.close()
    return pages


_OCR = None


def _engine():
    global _OCR
    if _OCR is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError:
            raise OCRUnavailable("未安装 rapidocr_onnxruntime（离线中文OCR）。请 pip install rapidocr_onnxruntime")
        _OCR = RapidOCR()
    return _OCR


def ocr_image(png):
    res, _ = _engine()(png)
    return [t for _, t, _ in (res or [])]


def ocr_pdf(pdf_path, dpis=(200, 300), max_pages=None):
    """返回OCR文本块列表(去重并集)。扫描件小字文本在单一分辨率下召回不稳，
    故默认在多个dpi各跑一遍取并集，显著提升交易日期/类型等小字段召回。"""
    if isinstance(dpis, int):
        dpis = (dpis,)
    blocks, seen = [], set()
    for dpi in dpis:
        for png in pdf_to_images(pdf_path, dpi, max_pages=max_pages):
            for t in ocr_image(png):
                if t not in seen:
                    seen.add(t)
                    blocks.append(t)
    return blocks


def pdf_native_text(pdf_path):
    """尝试读原生文字层；扫描件返回空串（PyMuPDF）。"""
    try:
        import fitz
        doc = fitz.open(pdf_path)
        txt = "\n".join(doc.load_page(i).get_text() for i in range(doc.page_count))
        doc.close()
        return txt
    except Exception:
        return ""


# ---------- 字段抽取 ----------
def _pick_entity(blocks):
    """持仓主体(客户公司)——排除理财子/管理人/支行。"""
    for t in blocks:
        if "公司" in t and not any(k in t for k in ["理财有限", "管理有限", "支行", "分行", "管理责任"]):
            m = re.search(r'[一-龥A-Za-z（）()]{4,}?(?:有限责任公司|有限公司|公司)', t)
            if m and any(k in m.group(0) for k in ["食品", "科技", "贸易", "实业", "集团", "投资", "商贸", "供应链"]):
                return m.group(0)
    for t in blocks:
        m = re.search(r'[一-龥A-Za-z（）()]{4,}?(?:有限责任公司|有限公司)', t)
        if m and not any(k in m.group(0) for k in ["理财", "支行", "分行", "银行"]):
            return m.group(0)
    # 兼容OCR把末字"司"拆到别的块: "…食品科技有限公" -> 补"司"
    for t in blocks:
        m = re.search(r'[一-龥]{4,}(?:食品|科技|贸易|实业|集团|投资|商贸|供应链)[一-龥]*有限公', t)
        if m and "理财" not in m.group(0):
            return m.group(0) + "司"
    return ""


def _pick_manager(blocks):
    for t in blocks:
        m = re.search(r'[一-龥]{2,6}理财(?:有限责任公司|有限公司|股份有限公司)', t)
        if m:
            return m.group(0)
    return ""


def _pick_product(blocks):
    best = ""
    for t in blocks:
        c = re.sub(r'\s', '', t)
        if "理财" in c and ("号" in c or "招享" in c or "债" in c):
            m = re.search(r'[一-龥A-Za-z]{2,}理财[一-龥A-Za-z0-9]*?\d*号(?:（招享）|\(招享\))?', c)
            cand = m.group(0) if m else c
            if len(cand) > len(best):
                best = cand
    return best


def _tail_digits(x):
    m = re.search(r'\d+$', x)
    return len(m.group(0)) if m else 0


def _lead_digits(x):
    m = re.match(r'\d+', x)
    return len(m.group(0)) if m else 0


def _pick_account(blocks):
    masked, plain = [], []
    for t in blocks:
        c = t.replace(" ", "")
        if any(u in c.lower() for u in ("http", "clientno", "ebank", "aspx")):
            continue                                  # 跳过URL/水印中的长数字
        for m in re.findall(r'\d[\d*]{5,}\d', c):
            (masked if "*" in m else plain).append(m.strip("*"))
    pool = masked or plain                            # 优先掩码账号
    if pool:
        pool.sort(key=lambda x: (_tail_digits(x), _lead_digits(x)), reverse=True)  # 尾号最长, 再取前缀最完整
        return pool[0]
    return ""


def _pick_branch(blocks):
    for t in blocks:
        m = re.search(r'[一-龥]{2,8}(?:支行|分行)', t)
        if m:
            return re.sub(r'^.*?公司', '', m.group(0))   # 去掉粘连的"…公司"前缀
    return ""


def _pick_issuer(blocks, hint=""):
    hay = hint + " " + " ".join(blocks)
    for k in ISSUER_KEYS:
        if k in hay:
            return k
    return ""


def _pick_currency(blocks):
    hay = " ".join(blocks)
    for cur, kw in [("USD", "美元"), ("HKD", "港币"), ("EUR", "欧元")]:
        if kw in hay:
            return cur
    return "CNY"


def _pick_nav(blocks):
    # 净值常与净值日期被OCR粘连: "1.02572026-05-29" -> 取紧跟日期前的4位小数
    for t in blocks:
        c = t.replace(" ", "")
        m = re.search(r'(\d\.\d{4})(?=20\d{2}-\d{2})', c)
        if m:
            return float(m.group(1))
    for t in blocks:
        c = t.replace(" ", "")
        for m in re.finditer(r'(?<![\d,.])(\d\.\d{3,4})(?![\d])', c):
            v = float(m.group(1))
            if 0.3 < v < 5:
                return v
    return None


def _is_footnote(t):
    c = t.replace(" ", "")
    if re.match(r'^\d+\.', c):
        return True
    return any(k in c for k in ["仅供参考", "生成的交易", "委托份额", "对账单可能",
                                "请联系", "如分红", "强赎", "涉及变动", "查询条件", "被动类交易"])


def _confirm_amounts(blocks):
    """纯金额块(2~3个粘连数字), 首个为大额 -> (确认金额, 确认份额)。"""
    for t in blocks:
        c = t.replace(" ", "")
        if re.fullmatch(r'(?:' + MONEY2 + r'){2,3}', c):
            vals = [_num(x) for x in re.findall(MONEY2, c)]
            if vals and vals[0] and vals[0] > 1000:
                return vals[0], (vals[1] if len(vals) > 1 else None)
    return None, None


def _txn_rows(blocks):
    """交易明细：交易行必须(含交易关键词 + 带日期 + 非脚注)。确认金额/份额优先取纯金额块。"""
    conf_amt, conf_share = _confirm_amounts(blocks)
    rows, seen = [], set()
    for t in blocks:
        if _is_footnote(t):
            continue
        typ = next((k for k in TXN_KEYS if k in t), "")
        if not typ:
            continue
        c = t.replace(" ", "")
        d = re.search(DATE_RE, c)
        if not d:
            continue
        inline = [_num(x) for x in re.findall(MONEY2, c)]
        inline_big = [a for a in inline if a and a > 1]
        key = (d.group(0), typ)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "日期": d.group(0),
            "类型": typ,
            "确认金额": conf_amt if conf_amt else (inline_big[0] if inline_big else None),
            "确认份额": conf_share,
            "交易金额": inline[-1] if inline else None,
        })
    return rows


def _holdings(blocks, exclude=None):
    """持仓市值区间：返回(期初市值, 期末市值, 是否清仓)。最大市值为期初；出现0则判清仓。
    exclude: 赎回确认金额(通常含收益, 大于持仓市值), 排除后期初市值才准确。"""
    vals = []
    zero_hit = False
    for t in blocks:
        c = t.replace(" ", "")
        for m in re.findall(MONEY2, c):
            v = _num(m)
            if v is not None and v > 1 and v != exclude:
                vals.append(v)
            if v == 0.0 and ("持仓" in c or "合计" in c or re.search(DATE_RE, c)):
                zero_hit = True
    begin_mv = max(vals) if vals else None
    end_mv = 0.0 if zero_hit else None
    return begin_mv, end_mv, zero_hit


# ---------- 主入口 ----------
def parse(pdf_path=None, blocks=None, dpis=(200, 300), issuer_hint=""):
    """解析理财对账单 -> 归一化记录。pdf_path 走OCR；blocks 直接传OCR文本块(便于单测)。"""
    if blocks is None:
        if not pdf_path:
            raise ValueError("需提供 pdf_path 或 blocks")
        blocks = ocr_pdf(pdf_path, dpis=dpis)
    hint = issuer_hint or (os.path.basename(pdf_path) if pdf_path else "")
    if _is_voucher(blocks):
        return parse_voucher(blocks, hint)
    txns = _txn_rows(blocks)
    _conf_amt, _ = _confirm_amounts(blocks)
    begin_mv, end_mv, cleared = _holdings(blocks, exclude=_conf_amt)
    rec = {
        "单据类型": "理财对账单",
        "出单机构": _pick_issuer(blocks, hint),
        "主体": _pick_entity(blocks),
        "产品名称": _pick_product(blocks),
        "管理机构": _pick_manager(blocks),
        "账号": _pick_account(blocks),
        "开户机构": _pick_branch(blocks),
        "币种": _pick_currency(blocks),
        "净值": _pick_nav(blocks),
        "期初市值": begin_mv,
        "期末市值": end_mv,
        "是否清仓": cleared,
        "交易明细": txns,
        "warnings": [],
    }
    _validate(rec)
    return rec


def _validate(rec):
    w = rec["warnings"]
    if not rec["主体"]:
        w.append("未识别到持仓主体")
    if not rec["账号"]:
        w.append("未识别到账号")
    if not rec["交易明细"]:
        w.append("本期无交易明细")
    redeems = [t for t in rec["交易明细"] if "赎回" in t["类型"] and t.get("确认金额")]
    if rec.get("是否清仓") and rec.get("期初市值") and redeems:
        conf = sum(t["确认金额"] for t in redeems)
        base = rec["期初市值"]
        if base and abs(conf - base) / base > 0.05:
            w.append("清仓勾稽偏差>5%: 期初市值%.2f vs 赎回确认%.2f" % (base, conf))
    return rec


# ---------- 交易凭证式(宁波网银凭证等: 标签-值, 单笔) ----------
def _is_voucher(blocks):
    hay = "".join(b.replace(" ", "") for b in blocks)
    return ("交易凭证" in hay or "交易名称" in hay or "网上银行" in hay) and "持仓市值" not in hay


def _voucher_product(blocks):
    """产品名称常被OCR拆成多块; 拼接实质名称碎片, 排除交易名称/字段标签污染。"""
    LBL = ("赎回", "申购", "购买", "认购", "账号", "户名", "产品名称", "产品代码",
           "金额", "日期", "状态", "备注", "交易", "凭证", "币种")
    frags = []
    for b in blocks:
        c = b.replace(" ", "")
        if any(x in c for x in LBL):
            continue
        if ("理财" in c) or ("持有" in c) or ("收益" in c) or re.search(r'\d+号', c) or c.endswith("-E") or "天)" in c:
            frags.append(c)
    return "".join(frags)


def _voucher_product_code(blocks):
    for b in blocks:
        c = b.replace(" ", "")
        m = re.search(r'\b[A-Z]{2,4}\d{5,}[A-Z]?\b', c)
        if m:
            return m.group(0)
    return ""


def _voucher_txn(blocks):
    hay = [b.replace(" ", "") for b in blocks]
    joined = "".join(hay)
    m = re.search(r'理财产品?(赎回|申购|购买|认购)', joined) or re.search(r'(赎回|申购|购买|认购|分红|派息)', joined)
    if m and m.lastindex:
        raw = m.group(1)
        typ = ("理财" + raw) if raw in ("赎回", "申购") else raw
    else:
        typ = "理财赎回"
    dates = [re.search(DATE_RE, b).group(0) for b in hay if re.search(DATE_RE, b)]
    monies = [_num(x) for b in hay for x in re.findall(MONEY2, b)]
    amt = max(monies) if monies else None
    return [{"日期": dates[0] if dates else "", "类型": typ,
             "确认金额": amt, "确认份额": None, "交易金额": amt}]


def _voucher_account(blocks):
    # 账号常带"(人民币)"后缀; 优先"数字(人民币)"块, 否则退回通用
    for t in blocks:
        c = t.replace(" ", "")
        m = re.search(r'(\d{8,})[（(]', c)
        if m:
            return m.group(1)
    return _pick_account(blocks)


def _voucher_status(blocks):
    hay = "".join(b.replace(" ", "") for b in blocks)
    for st in ("交易成功", "交易失败", "处理中", "受理成功"):
        if st in hay:
            return st
    return ""


def parse_voucher(blocks, hint=""):
    txns = _voucher_txn(blocks)
    rec = {
        "单据类型": "理财交易凭证",
        "出单机构": _pick_issuer(blocks, hint),
        "主体": _pick_entity(blocks),
        "产品名称": _voucher_product(blocks),
        "产品代码": _voucher_product_code(blocks),
        "管理机构": _pick_manager(blocks),
        "账号": _voucher_account(blocks),
        "开户机构": _pick_branch(blocks),
        "币种": _pick_currency(blocks),
        "净值": _pick_nav(blocks),
        "期初市值": None,
        "期末市值": None,
        "是否清仓": None,
        "凭证状态": _voucher_status(blocks),
        "交易明细": txns,
        "warnings": [],
    }
    if not rec["主体"]:
        rec["warnings"].append("未识别到持仓主体")
    if not rec["账号"]:
        rec["warnings"].append("未识别到账号")
    if not txns or not txns[0]["确认金额"]:
        rec["warnings"].append("未识别到交易金额")
    return rec


def find_wealth_pdfs(root):
    """在流水目录里找理财对账单/交易凭证 PDF：文件名含 理财/赎回/财富/持仓/申购，
    或原生文字层命中理财特征词（扫描件无文字层→靠文件名）。返回 [路径]。"""
    import os as _os
    hits = []
    NAME_KW = ("理财", "赎回", "财富", "持仓", "申购", "净值", "基金", "信托")
    for dp, _dn, fns in _os.walk(root):
        for fn in fns:
            if not fn.lower().endswith(".pdf"):
                continue
            p = _os.path.join(dp, fn)
            if any(k in fn for k in NAME_KW):
                hits.append(p)
                continue
            try:
                if is_wealth_statement(pdf_native_text(p)):   # 有文字层的理财单
                    hits.append(p)
            except Exception:
                pass
    return sorted(set(hits))


def to_audit_records(rec):
    """理财对账单交易 -> 银行流水式记录。赎回/转出/分红=资金流入(收入)；申购/认购/转投=流出(支出)。"""
    INFLOW = ("赎回", "转出", "分红", "派息")
    out = []
    for t in rec["交易明细"]:
        amt = t.get("确认金额") or 0
        is_in = any(k in t["类型"] for k in INFLOW)
        out.append({
            "账号": rec["账号"],
            "户名": rec["主体"],
            "交易日期": t["日期"],
            "摘要": t["类型"],
            "对方户名": rec["管理机构"] or rec["出单机构"],
            "收入": amt if is_in else 0,
            "支出": 0 if is_in else amt,
            "来源单据": rec.get("单据类型", "理财对账单"),
            "产品": rec["产品名称"],
            "产品代码": rec.get("产品代码", ""),
        })
    return out
