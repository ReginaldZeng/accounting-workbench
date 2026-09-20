# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-07-04 | Author: Claude / c | Version: V2.4
# Description: 科目余额表内核（账面核对）。
#   - build_rows_kingdee: GL_BALANCE 期初(按 科目+维度 去重) + 序时账本期借贷 → 还原实时科目余额表。
#     金蝶 GL_BALANCE 接口的"期末/本期发生"在凭证未过账时停在期初/返回0，不能直接用；
#     须「期初＋本期序时账」还原（与金蝶科目余额表报表同公式，V1.7 已逐户验证一致）。
#   - build_rows_sample: 样例行直读（样例数据自带期初/借/贷/期末）。
#   - parse_report_xlsx: 解析"金蝶界面导出的科目余额表 Excel"——容错认列：
#     单金额列格式(期初原币/本期借方原币/…)与 借/贷 两行表头格式都认。
#   - compare: 工具数 vs 上传数，按科目逐项核对(期初/本期借方/本期贷方/期末)，供人眼核对。
import re

MONEY_PREFIXES = ("1001", "1002", "1012", "1101")
CAT = {"1001": "库存现金", "1002": "银行存款", "1012": "其它货币资金", "1101": "交易性金融资产"}
ITEMS = ("期初", "本期借方", "本期贷方", "期末")

# ── 物流相关科目（V2.588 建，V2.590 按真金蝶收窄口径）──
# 物流计提工具（logistics_accrual.py）入账落到的科目就是"物流相关"的权威定义：
#   · 暂估进项税 = 精确明细科目 2221.01.07（挂供应商）  · 其他应付款—供应商往来 = 2241.02（挂供应商）
#   · 费用 = 6601/6604/6401/5101，但物流部分靠【费用项目维度】区分（出库运费/入库运费/货物仓储费/搬运费/研发外购）
# ⚠ 不能用 2221/2241 整段前缀——那会把整棵应交税费/其他应付款树都捞进来（真机 554 行即此坑，V2.590 修）。
LOGI_EXACT = ("2221.01.07", "2241.02")               # 精确明细科目
LOGI_FEE_PREFIXES = ("5101", "6401", "6601", "6604")  # 费用科目：物流部分靠费用项目维度区分
LOGI_FEE_ITEMS = ("运费", "仓储", "搬运", "研发外购")   # 物流费用项目关键字（费用科目维度里匹配）
LOGI_CAT_KEYWORDS = ("物流", "运输", "运费", "仓储", "搬运")   # 2241.02 费用归属为物流类的关键字
# 上传解析（按科目汇总、拿不到维度）用的前缀集：精确到 2221.01.07/2241.02，费用段仍按科目前缀
LOGI_PREFIXES = ("2221.01.07", "2241.02", "5101", "6401", "6601", "6604")
LOGI_CAT = [
    ("5101", "制造费用"), ("6401", "主营业务成本"), ("6601", "销售费用"),
    ("6604", "研发费用"), ("2241", "其他应付款"), ("2221", "应交税费"),
]


def cat_of_logi(code):
    for p, name in LOGI_CAT:
        if str(code).startswith(p):
            return name
    return ""


def _last_seg(dim_name):
    return str(dim_name or "").rsplit("/", 1)[-1]     # 维度名最后一段＝费用归属/费用项目（避免匹配到供应商名）


def is_logi(code, dim_name=""):
    """某(科目,维度)是否物流相关：
    2221.01.07 暂估进项税＝物流计提专用，全收；2241.02 其他应付款＝全供应商共用，只留费用归属为物流类；
    6601/6604/6401/5101 费用＝只留费用项目为物流类。"""
    code = str(code or "")
    if code.startswith("2221.01.07"):
        return True
    if code.startswith("2241.02"):
        return any(k in _last_seg(dim_name) for k in LOGI_CAT_KEYWORDS)
    if code.startswith(LOGI_FEE_PREFIXES):
        return any(k in str(dim_name or "") for k in LOGI_FEE_ITEMS)
    return False


def to_f(v):
    if v is None or v == "":
        return 0.0
    try:
        return float(str(v).replace(",", "").replace("￥", "").replace("¥", "").strip() or 0)
    except Exception:
        return 0.0


def cat_of(code):
    for p in MONEY_PREFIXES:
        if str(code).startswith(p):
            return CAT[p]
    return ""


def build_rows_kingdee(bal_rows, vou_rows):
    """真金蝶：期初(去重) + 序时账借贷 → 每 (科目, 账户维度) 一行。返回 list[dict]。"""
    opens, seen, names, meta = {}, set(), {}, {}
    for r in bal_rows:
        code = str(r.get("科目编码") or "")
        if not code.startswith(MONEY_PREFIXES):
            continue
        dim = str(r.get("核算维度.银行账号.编码") or "").strip()
        key = (code, dim)
        if key in seen:
            continue                      # GL_BALANCE 会返回重复行，须按(科目,维度)去重（V1.7 教训）
        seen.add(key)
        opens[key] = to_f(r.get("期初原币"))
        names.setdefault(code, r.get("科目名称") or "")
        meta[key] = {"账户": (r.get("核算维度.银行账号.名称") or dim or "—"),
                     "币别": (r.get("币别") or "CNY")}
    moves = {}
    for r in vou_rows:
        code = str(r.get("科目编码") or "")
        if not code.startswith(MONEY_PREFIXES):
            continue
        dim = str(r.get("FDetailID.FF100002.FNumber") or "").strip()
        key = (code, dim)
        m = moves.setdefault(key, [0.0, 0.0])
        m[0] += to_f(r.get("FDEBIT"))
        m[1] += to_f(r.get("FCREDIT"))
        names.setdefault(code, r.get("科目名称") or "")
    rows = []
    for key in sorted(set(opens) | set(moves)):
        code, dim = key
        op = opens.get(key, 0.0)
        d, c = moves.get(key, [0.0, 0.0])
        mt = meta.get(key) or {}
        rows.append({"科目编码": code, "科目名称": names.get(code, ""), "科目大类": cat_of(code),
                     "账户": mt.get("账户") or dim or "—", "币别": mt.get("币别") or "CNY",
                     "期初": round(op, 2), "本期借方": round(d, 2), "本期贷方": round(c, 2),
                     "期末": round(op + d - c, 2)})
    return rows


def build_rows_sample(bal_rows):
    """样例模式：样例余额行直读（自带期初/借/贷/期末）。"""
    rows = []
    for r in bal_rows:
        code = str(r.get("科目编码") or "")
        if not code.startswith(MONEY_PREFIXES):
            continue
        rows.append({"科目编码": code, "科目名称": r.get("科目名称") or "", "科目大类": cat_of(code),
                     "账户": (r.get("核算维度.银行账号.名称") or "—"), "币别": r.get("币别") or "CNY",
                     "期初": round(to_f(r.get("期初原币")), 2),
                     "本期借方": round(to_f(r.get("本期借方原币")), 2),
                     "本期贷方": round(to_f(r.get("本期贷方原币")), 2),
                     "期末": round(to_f(r.get("期末原币")), 2)})
    rows.sort(key=lambda x: (x["科目编码"], x["账户"]))
    return rows


# ---------------- 上传的金蝶科目余额表 Excel：容错解析 ----------------
def _labels(grid, h):
    """表头行(可能两行：期初余额 跨列 + 下行 借方/贷方) → 每列合成标签。返回 (labels, data_start)。"""
    top = list(grid[h])
    nxt = list(grid[h + 1]) if h + 1 < len(grid) else []
    ff, last = [], ""
    for v in top:                          # 顶行向右填充（合并单元格只有左上有值）
        s = str(v).strip() if v not in (None, "") else ""
        last = s if s else last
        ff.append(last)
    two_row = any(str(v).strip() in ("借方", "贷方", "借", "贷") for v in nxt if v not in (None, ""))
    labels = []
    for j, base in enumerate(ff):
        sub = str(nxt[j]).strip() if (two_row and j < len(nxt) and nxt[j] not in (None, "")) else ""
        labels.append(base + sub)
    return labels, (h + 2 if two_row else h + 1)


def _pick(labels, must, exclude=(), prefer=()):
    """按关键词挑列：含 must 全部、不含 exclude 任何；多命中优先含 prefer 的。返回列号或 None。"""
    hits = [j for j, lb in enumerate(labels)
            if lb and all(m in lb for m in must) and not any(x in lb for x in exclude)]
    if not hits:
        return None
    for p in prefer:
        for j in hits:
            if p in labels[j]:
                return j
    return hits[0]


def parse_report_xlsx(path, prefixes=MONEY_PREFIXES):
    """解析金蝶导出的科目余额表 xlsx → (per_code dict, err)。
    per_code = {科目编码: {"科目名称":…, "期初":…, "本期借方":…, "本期贷方":…, "期末":…}}。
    prefixes：只收以这些前缀开头的科目（默认四类资金科目；物流线传 LOGI_PREFIXES）。"""
    prefixes = tuple(prefixes)
    try:
        from openpyxl import load_workbook
        wb = load_workbook(path, data_only=True, read_only=True)
    except Exception as e:
        return None, f"打不开这个文件（请确认是 .xlsx 格式的 Excel；老 .xls 请在 Excel 里另存为 .xlsx）：{e}"
    for ws in wb.worksheets:
        grid = [list(row) for row in ws.iter_rows(values_only=True)]
        hdr = next((i for i, row in enumerate(grid[:20])
                    if any(v and "科目编码" in str(v) for v in row)), None)
        if hdr is None:
            continue
        labels, start = _labels(grid, hdr)
        c_code = _pick(labels, ["科目编码"])
        c_name = _pick(labels, ["科目名称"]) or _pick(labels, ["科目全名"])
        # 期初：单列(期初原币/期初余额) 或 借贷两列
        c_qc = _pick(labels, ["期初"], exclude=["借", "贷", "本位"], prefer=["原币"])
        c_qcj = _pick(labels, ["期初", "借"]); c_qcd = _pick(labels, ["期初", "贷"])
        # 本期发生：借/贷 各一列（"本期借方原币"或"本期发生额借方"）
        c_jf = _pick(labels, ["借"], exclude=["期初", "期末", "本年", "累计"], prefer=["本期", "原币"])
        c_df = _pick(labels, ["贷"], exclude=["期初", "期末", "本年", "累计"], prefer=["本期", "原币"])
        # 期末：单列 或 借贷两列
        c_qm = _pick(labels, ["期末"], exclude=["借", "贷", "本位"], prefer=["原币"])
        c_qmj = _pick(labels, ["期末", "借"]); c_qmd = _pick(labels, ["期末", "贷"])
        if c_code is None or (c_qc is None and c_qcj is None):
            continue
        per = {}
        for row in grid[start:]:
            code = str(row[c_code]).strip() if (c_code < len(row) and row[c_code] not in (None, "")) else ""
            if code.endswith(".0"):
                code = code[:-2]           # Excel 里科目编码若是数值格式会带 .0
            if not re.match(r"^\d{4}", code) or not code.startswith(prefixes):
                continue
            g = lambda j: to_f(row[j]) if (j is not None and j < len(row)) else 0.0
            qc = g(c_qc) if c_qc is not None else g(c_qcj) - g(c_qcd)      # 资产科目：期初=借-贷
            qm = g(c_qm) if c_qm is not None else (g(c_qmj) - g(c_qmd) if c_qmj is not None else None)
            d = per.setdefault(code, {"科目名称": "", "期初": 0.0, "本期借方": 0.0, "本期贷方": 0.0, "期末": 0.0, "_qm": qm is not None})
            if c_name is not None and c_name < len(row) and row[c_name] and not d["科目名称"]:
                d["科目名称"] = str(row[c_name]).strip()
            d["期初"] += qc
            d["本期借方"] += g(c_jf)
            d["本期贷方"] += g(c_df)
            d["期末"] += (qm or 0.0)
        if per:
            for d in per.values():
                if not d.pop("_qm"):       # 表里没有期末列 → 按 期初+借-贷 补算
                    d["期末"] = d["期初"] + d["本期借方"] - d["本期贷方"]
                for k in ITEMS:
                    d[k] = round(d[k], 2)
            return per, None
    return None, "没在表里认出「科目编码」及金额列。请上传金蝶导出的《科目余额表》Excel（表头须含 科目编码、期初、借方、贷方、期末）。"


def compare(tool_rows, uploaded, cat_fn=cat_of):
    """工具数(逐维度行) vs 上传数(按科目) → 按科目逐项核对。返回 {"rows":…, "科目数":…, "一致数":…}。
    cat_fn：科目→科目大类的函数（默认四类资金；物流线传 cat_of_logi）。"""
    tool = {}
    for r in tool_rows:
        d = tool.setdefault(r["科目编码"], {"科目名称": r.get("科目名称", ""),
                                            "期初": 0.0, "本期借方": 0.0, "本期贷方": 0.0, "期末": 0.0})
        for k in ITEMS:
            d[k] += r.get(k) or 0.0
    out = []
    for code in sorted(set(tool) | set(uploaded)):
        t, u = tool.get(code), uploaded.get(code)
        row = {"科目编码": code,
               "科目名称": (t or u or {}).get("科目名称", ""), "科目大类": cat_fn(code)}
        if t and u:
            ok = True
            for k in ITEMS:
                tv, uv = round(t[k], 2), round(u[k], 2)
                row[k + "_工具"], row[k + "_报表"] = tv, uv
                row[k + "_差"] = round(tv - uv, 2)
                ok = ok and abs(tv - uv) < 0.005
            row["结果"] = "一致" if ok else "有出入"
        else:
            for k in ITEMS:
                row[k + "_工具"] = round(t[k], 2) if t else None
                row[k + "_报表"] = round(u[k], 2) if u else None
                row[k + "_差"] = None
            row["结果"] = "报表里没有此科目" if t else "工具里没有此科目"
        out.append(row)
    n_ok = sum(1 for r in out if r["结果"] == "一致")
    return {"rows": out, "科目数": len(out), "一致数": n_ok, "全部一致": n_ok == len(out) and len(out) > 0}


# ---------------- 全科目/物流：金蝶报表口径取数 + 规整 + 质检 ----------------
def normalize_full_rows(rows12, prefixes=LOGI_PREFIXES, cat_fn=cat_of_logi):
    """把 kingdee_client.fetch_subject_balance_full 的 12 列行（金蝶报表口径）规整成行 dict，只留 prefixes 科目。
    12 列 = [编码, 名称, 维度编码, 维度名称, 期初借, 期初贷, 本期借, 本期贷, 本年借, 本年贷, 期末借, 期末贷]。
    余额取【借−贷】的有符号口径：资产/费用为正、负债/权益为负，勾稽恒等式 期末=期初+借−贷 恒成立。"""
    prefixes = tuple(prefixes)
    out = []
    for r in rows12:
        code = str((r[0] if len(r) > 0 else "") or "").strip()
        name = str((r[1] if len(r) > 1 else "") or "").strip()
        if not code or name == "合计" or not code.startswith(prefixes):
            continue
        g = lambda i: to_f(r[i]) if i < len(r) else 0.0
        dimc = str((r[2] if len(r) > 2 else "") or "").strip()
        dim = str((r[3] if len(r) > 3 else "") or dimc or "").strip()
        out.append({"科目编码": code, "科目名称": name, "科目大类": cat_fn(code),
                    "维度编码": dimc, "账户": dim or "—", "币别": "CNY",
                    "期初": round(g(4) - g(5), 2), "本期借方": round(g(6), 2),
                    "本期贷方": round(g(7), 2), "期末": round(g(10) - g(11), 2)})
    out.sort(key=lambda x: (x["科目编码"], x["账户"]))
    return out


def normalize_logi_rows(rows12, cat_fn=cat_of_logi):
    """金蝶《科目余额表》12列 → 物流精确口径行 dict：
    只留 2221.01.07/2241.02 的供应商明细 + 6601/6604/6401/5101 的物流费用项目明细；
    丢弃各级科目汇总(空维度)行——避免整棵费用/税费树与父级汇总污染（真机 554 行即此坑）。"""
    out = []
    for r in rows12:
        code = str((r[0] if len(r) > 0 else "") or "").strip()
        name = str((r[1] if len(r) > 1 else "") or "").strip()
        if not code or name == "合计":
            continue
        dimc = str((r[2] if len(r) > 2 else "") or "").strip()
        dimn = str((r[3] if len(r) > 3 else "") or "").strip()
        if not dimc:                       # 空维度=科目级汇总行，丢弃（前端按科目分组自算小计）
            continue
        if not is_logi(code, dimn):
            continue
        g = lambda i: to_f(r[i]) if i < len(r) else 0.0
        out.append({"科目编码": code, "科目名称": name, "科目大类": cat_fn(code),
                    "维度编码": dimc, "账户": dimn or dimc, "币别": "CNY",
                    "期初": round(g(4) - g(5), 2), "本期借方": round(g(6), 2),
                    "本期贷方": round(g(7), 2), "期末": round(g(10) - g(11), 2)})
    out.sort(key=lambda x: (x["科目编码"], x["账户"]))
    return out


def build_rows_logi_sample(sample_rows, cat_fn=cat_of_logi):
    """样例模式：样例行（自带 期初/借/贷/期末）→ 规整（补科目大类、四舍五入、按科目段过滤）。"""
    prefixes = tuple(LOGI_PREFIXES)
    rows = []
    for r in sample_rows:
        code = str(r.get("科目编码") or "")
        if not code.startswith(prefixes):
            continue
        rows.append({"科目编码": code, "科目名称": r.get("科目名称") or "", "科目大类": cat_fn(code),
                     "维度编码": r.get("维度编码") or "", "账户": r.get("账户") or r.get("维度") or "—", "币别": r.get("币别") or "CNY",
                     "期初": round(to_f(r.get("期初")), 2), "本期借方": round(to_f(r.get("本期借方")), 2),
                     "本期贷方": round(to_f(r.get("本期贷方")), 2), "期末": round(to_f(r.get("期末")), 2)})
    rows.sort(key=lambda x: (x["科目编码"], x["账户"]))
    return rows


def qc_rows(rows):
    """质检勾稽：逐行核 期末 ?= 期初 + 本期借方 − 本期贷方。返回 {rows(全部,带_勾稽/_勾稽差), 行数, 通过数, 全部通过, 异常}。"""
    out, bad = [], []
    for r in rows:
        should = round((r.get("期初") or 0) + (r.get("本期借方") or 0) - (r.get("本期贷方") or 0), 2)
        diff = round((r.get("期末") or 0) - should, 2)
        ok = abs(diff) < 0.005
        rr = dict(r); rr["_应为期末"] = should; rr["_勾稽差"] = diff; rr["_勾稽"] = ok
        out.append(rr)
        if not ok:
            bad.append({"科目编码": r.get("科目编码"), "科目名称": r.get("科目名称"), "账户": r.get("账户"),
                        "期末": r.get("期末"), "应为": should, "差": diff})
    n_ok = sum(1 for r in out if r["_勾稽"])
    return {"rows": out, "行数": len(out), "通过数": n_ok,
            "全部通过": n_ok == len(out) and len(out) > 0, "异常": bad}


_SUP_SUFFIXES = ("供应链管理有限公司", "供应链有限责任公司", "供应链有限公司", "物流有限公司",
                 "有限责任公司", "股份有限公司", "有限公司", "供应链", "集团")


def supplier_core(name):
    """供应商核心名：取 '/' 前一段（去掉费用归属后缀），再去掉公司后缀，用于在摘要里模糊匹配。
    如 '上海顺丰冷运供应链有限公司/物流运输服务' → '上海顺丰冷运'；'顺丰速运有限公司' → '顺丰速运'。"""
    s = str(name or "").split("/")[0].strip()
    for suf in _SUP_SUFFIXES:
        if s.endswith(suf) and len(s) > len(suf):
            return s[:-len(suf)]
    return s


def _dim_match(dim, codes):
    """余额表维度编码 dim 是否命中序时账某行的结构化核算维度值集合 codes。
    dim 可能是单值(物流运输服务016)或复合(物流运输服务016/供应商009)；要求 dim 的每一段都在 codes 里。"""
    dim = str(dim or "").strip()
    if not dim:
        return False
    cs = set(str(c).strip() for c in codes if str(c).strip())
    if not cs:
        return False
    segs = set(x for x in re.split(r"[/;,、\s]+", dim) if x) or {dim}
    return segs <= cs


def build_voucher_lines(voucher_rows, code, dim, dim_name=""):
    """下钻反查：从序时账逐笔挑出某 (科目, 维度) 的凭证行 → 规整。样例 dict 与金蝶 fetch_gl_voucher_subjects 都吃。
    维度归属（V2.597 根治）：序时账把有效核算维度槽(dim0/dim1…)一并带出，按【结构化维度精确匹配】(dim 各段都在该行维度值里)；
    退化顺序：结构化维度→(旧)FF100002 编码→供应商核心名在摘要里(dim_name)。返回 {lines,借合计,贷合计,笔数}。"""
    code, dim = str(code or ""), str(dim or "")
    core = supplier_core(dim_name)

    def gv(r, *keys):
        for k in keys:
            if k in r and r[k] not in (None, ""):
                return r[k]
        return ""

    lines, td, tc = [], 0.0, 0.0
    for r in voucher_rows:
        rc = str(gv(r, "科目编码") or "")
        if rc != code:
            continue
        rd = str(gv(r, "维度编码", "FDetailID.FF100002.FNumber") or "").strip()
        flex = [str(v).strip() for k, v in r.items()
                if isinstance(k, str) and k.startswith("dim") and str(v).strip()]
        codes = ([rd] if rd else []) + flex     # 该行所有结构化核算维度值（旧FF100002 + 新探到的槽）
        memo = str(gv(r, "摘要", "FEXPLANATION") or "")
        if not dim and not core:        # 全科目模式：该科目本期全部凭证（对账找差用，dim/dim_name 都不传）
            pass
        elif codes:                     # 有结构化核算维度 → 精确匹配（根治：dim 各段都在该行维度值里）
            if not _dim_match(dim, codes):
                continue
        elif core:                      # 无任何结构化维度 → 供应商核心名在摘要里兜底
            if core not in memo:
                continue
        else:
            continue
        d, c = to_f(gv(r, "借", "FDEBIT")), to_f(gv(r, "贷", "FCREDIT"))
        grp = str(gv(r, "凭证字", "FVOUCHERGROUPID.FName") or "")
        no = gv(r, "凭证号", "FVOUCHERGROUPNO")
        lines.append({"日期": str(gv(r, "日期", "FDATE") or "")[:10],
                      "凭证": (str(grp) + "-" + str(no)) if (grp or no not in ("", None)) else "",
                      "摘要": str(gv(r, "摘要", "FEXPLANATION") or ""),
                      "借": round(d, 2), "贷": round(c, 2),
                      "制单人": str(gv(r, "制单人", "FCREATORID.FName") or "")})
        td += d
        tc += c
    return {"lines": lines, "借合计": round(td, 2), "贷合计": round(tc, 2), "笔数": len(lines)}
