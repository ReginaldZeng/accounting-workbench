# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-08-01 | Author: Claude / c | Version: V2.157
# Description: 汇率录入工具·P1 内核（纯函数，不 import 框架/db）。据《汇率录入工具·需求确认书 v1.1》
#              （2026-08-01 签署）实现：人行「人民币汇率中间价公告」抓取 + 建汇率规则引擎（D3–D10）
#              + 交叉汇率（带算式）+ 四道机器闸门（D17）+ 历史复核比对（D13）。
#
#   ★ 五条铁律落到代码：
#     1) 只抄不算：对人民币三条直接取公告值，仅四舍五入到 4 位，不做任何加工。
#     2) 交叉汇率是唯一例外且必须留算式：cross_rate() 返回 (值, 算式字符串)，用原始中间价相除。
#     3) 每条带公告出处：每行带 source_date + source_url。
#     4) 抓不到就停绝不猜：缺任一在用币种即缺数闸门拦下，绝不用上月顶替/插值/第三方。
#     5) 写入过机器闸门 + 不覆盖已有：run_gates() 四道闸门；去重在写入侧（P2）。
#
#   ★ 两个已实证的坑（拆解阶段踩过，勿回退）：
#     - 日期一律从「链接文字 / title 属性」解析（如「2026年7月31日…中间价公告」），
#       严禁从 URL 数字截取——2025-11 前的公告 URL 是纯流水号，截出来是错的静默错账（D12）。
#     - 舍入用 decimal.ROUND_HALF_UP（四舍五入），不能用 Python 内建 round()（银行家舍入）。

from __future__ import annotations

import re
import datetime
from decimal import Decimal, ROUND_HALF_UP

# ============================================================
# 常量
# ============================================================

# 人行「人民币汇率中间价公告」栏目
PBOC_LIST_BASE = "https://www.pbc.gov.cn/zhengcehuobisi/125207/125217/125925/"
# 第 1 页 = index.html；第 N 页（N≥2）= 17105-{N}.html （实测 2026-08-01）
_UA = {"User-Agent": "Mozilla/5.0 (compatible; FinanceWorkbench/FxRate)"}
_HTTP_TIMEOUT = 20

# 币别码（金蝶 BD_CURRENCY）。人行正文用「港元」，金蝶/贵司口径用「港币」。
RMB_CODE, RMB_NAME = "PRE001", "人民币"
CURRENCY = {
    # 人行正文名 -> (金蝶币别码, 金蝶显示名)
    "美元": ("PRE007", "美元"),
    "港元": ("PRE002", "港币"),
    "英镑": ("PRE006", "英镑"),
}
# 在用币种（对人民币三条）。顺序即建汇率顺序。
IN_USE = ["美元", "港元", "英镑"]
# 交叉汇率（月末条才建）：该币 -> 美元
CROSS = ["港元", "英镑"]

RATE_TYPE = "HLTX01_SYS"  # 固定汇率（D10）
DEFAULT_ORG = "101"       # 8 月起写 101（D9，v1.1 定案；组织可选、默认 101）
# 写入金蝶时盖进「描述」(FDescription)的来源标记 + 计算过程。作用有二：
#  ① 算式/出处盖进金蝶本身——审核的人在金蝶直接看到"怎么算的、取自人行哪天"，不用回工具（两条铁律落地）。
#  ② 靠标记判"工具录入 vs 人工录入"，由金蝶数据自证、跟着账套走、换后端服务器/换库都不丢（不依赖本地留痕）。
FX_MARK = "【汇率录入工具】"


def is_tool_mark(desc) -> bool:
    return bool(desc) and FX_MARK in str(desc)


def build_desc(row) -> str:
    """一行的金蝶「描述」＝标记 + 计算过程/出处（交叉汇率带算式、对人民币标照抄或进位）+ 人行取数日。"""
    basis = row.get("basis") or ""
    src = row.get("source_date") or ""
    return FX_MARK + basis + (f"｜人行{src}公布" if src else "")
# 组织可选下拉·受控清单（默认 101；v1.1 D9。后话：改从金蝶组织表实时拉，见确认书第二笔）
FX_ORGS = [
    {"code": "101", "name": "深圳星期零"},
    {"code": "107", "name": "孝感星期九"},
]
# 金蝶显示名 → 人行正文名（人行用「港元」，金蝶用「港币」）
DISP_TO_PBOC = {"美元": "美元", "港币": "港元", "英镑": "英镑"}

# 偏离闸门默认阈值（D17，可在设置页调）
DEFAULT_DEVIATION = Decimal("0.03")  # ±3%

FOUR = Decimal("0.0001")


class FxError(Exception):
    """内核可预期错误（缺数、解析失败等），供上层转成用户话术。"""


# ============================================================
# 小工具：舍入 / 日期
# ============================================================

def round4(x) -> Decimal:
    """四舍五入到 4 位小数（ROUND_HALF_UP，非银行家舍入）。"""
    return Decimal(str(x)).quantize(FOUR, rounding=ROUND_HALF_UP)


def _d(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def month_last_day(year: int, month: int) -> datetime.date:
    if month == 12:
        return datetime.date(year, 12, 31)
    return datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)


def next_month(year: int, month: int):
    return (year + 1, 1) if month == 12 else (year, month + 1)


def parse_cn_date(text: str):
    """从「2026年7月31日」解析出 date；找不到返回 None。"""
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if not m:
        return None
    return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


# ============================================================
# 抓取：列表页 + 详情页
# ============================================================

def _http_get(url: str) -> str:
    """默认抓取实现（可在测试里注入替身）。响应头未声明编码，手工按 utf-8 解码。"""
    import requests  # 延迟导入，保持模块顶层零框架依赖
    r = requests.get(url, headers=_UA, timeout=_HTTP_TIMEOUT)
    if r.status_code != 200:
        raise FxError(f"人行列表页 HTTP {r.status_code}：{url}")
    r.encoding = "utf-8"
    return r.text


def list_page_url(page: int) -> str:
    return PBOC_LIST_BASE + ("index.html" if page <= 1 else f"17105-{page}.html")


# 真正的公告条目：href 是 /125925/<15+位数字>/index.html，且文字/ title 带「…中间价公告」
_ANCHOR_RE = re.compile(
    r'<a[^>]*href="([^"]*?/125925/(\d{10,})/index\.html)"[^>]*?>([^<]*?中间价公告)',
    re.I,
)
_TITLE_ATTR_RE = re.compile(r'title="([^"]*?中间价公告)"', re.I)


def parse_list_html(html: str):
    """解析列表页 → [{date, url, title}]，按日期倒序。日期取自链接文字（铁律/D12）。"""
    out, seen = [], set()
    for m in _ANCHOR_RE.finditer(html):
        href, _num, text = m.group(1), m.group(2), m.group(3)
        # 若 <a> 段里另有更完整的 title=，用 title（有时文字被截断）
        seg = html[m.start():m.start() + 400]
        tm = _TITLE_ATTR_RE.search(seg)
        title = tm.group(1) if tm else text
        d = parse_cn_date(title) or parse_cn_date(text)
        if d is None:
            continue  # 没有日期的（导航/历史数据入口）跳过
        url = href if href.startswith("http") else "https://www.pbc.gov.cn" + href
        if url in seen:
            continue
        seen.add(url)
        out.append({"date": d, "url": url, "title": title})
    out.sort(key=lambda x: x["date"], reverse=True)
    return out


def list_announcements(page: int = 1, fetch=_http_get):
    return parse_list_html(fetch(list_page_url(page)))


# 正文取数句：「…2026年7月31日银行间外汇市场人民币汇率中间价为1美元对人民币6.7894元，…。」
_FWD_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([一-龥]{2,8}?)对人民币(\d+(?:\.\d+)?)元")
_REV_RE = re.compile(r"人民币1元对(\d+(?:\.\d+)?)\s*([一-龥]{2,8}?)[，,。]")


def parse_detail_html(html: str):
    """解析详情页 → {date, rates}. rates: {人行币名: {'raw': Decimal(1外币=?人民币), 'unit': N, 'quote': 原值}}。
    前 10 币种是「N 外币对人民币 Y 元」（日元 N=100）；后 15 是「人民币1元对 X 外币」（取倒数）。"""
    txt = re.sub(r"<[^>]+>", " ", html)
    txt = re.sub(r"\s+", " ", txt)
    k = txt.find("中间价为")
    if k < 0:
        raise FxError("详情页未找到「中间价为」取数句，页面可能改版")
    seg = txt[max(0, k - 60):k + 700]
    date = parse_cn_date(seg)
    rates = {}
    for unit, name, val in _FWD_RE.findall(seg):
        unit_d, val_d = _d(unit), _d(val)
        rates[name] = {"raw": val_d / unit_d, "unit": int(unit_d), "quote": val_d}
    for val, name in _REV_RE.findall(seg):
        val_d = _d(val)
        if val_d != 0 and name not in rates:
            rates[name] = {"raw": Decimal(1) / val_d, "unit": 1, "quote": val_d, "reciprocal": True}
    if not rates:
        raise FxError("详情页取数句解析出 0 个币种，页面可能改版")
    return {"date": date, "rates": rates}


def get_announcement(url: str, fetch=_http_get):
    d = parse_detail_html(fetch(url))
    d["url"] = url
    return d


# ============================================================
# 找公布日：当月最后一个公布日 / 次月第一个公布日
# ============================================================

def collect_announcements(fetch=_http_get, until_before: datetime.date = None, max_pages: int = 12):
    """从最新往回收集公告条目，直到出现早于 until_before 的日期（确保覆盖完整月份）或到 max_pages。"""
    acc = []
    for page in range(1, max_pages + 1):
        entries = list_announcements(page, fetch)
        if not entries:
            break
        acc.extend(entries)
        if until_before and any(e["date"] < until_before for e in entries):
            break
    # 去重按 url
    uniq, seen = [], set()
    for e in sorted(acc, key=lambda x: x["date"], reverse=True):
        if e["url"] not in seen:
            seen.add(e["url"]); uniq.append(e)
    return uniq


def last_publish_in_month(year: int, month: int, fetch=_http_get):
    """当月最后一个公布日的公告条目（date.year==year 且 date.month==month 的最大日期）。"""
    entries = collect_announcements(fetch, until_before=datetime.date(year, month, 1))
    cand = [e for e in entries if e["date"].year == year and e["date"].month == month]
    if not cand:
        raise FxError(f"未在人行公告中找到 {year}-{month:02d} 的任何公布日（缺数即停，绝不猜）")
    return max(cand, key=lambda e: e["date"])


def first_publish_in_month(year: int, month: int, fetch=_http_get):
    """次月第一个公布日的公告条目（date.year==year 且 date.month==month 的最小日期）。"""
    first_day = datetime.date(year, month, 1)
    entries = collect_announcements(fetch, until_before=first_day)
    cand = [e for e in entries if e["date"].year == year and e["date"].month == month]
    if not cand:
        raise FxError(f"未在人行公告中找到 {year}-{month:02d} 的任何公布日（缺数即停，绝不猜）")
    return min(cand, key=lambda e: e["date"])


# ============================================================
# 交叉汇率（唯一在“算”的地方，必须留算式）
# ============================================================

def cross_rate(x_raw: Decimal, usd_raw: Decimal):
    """交叉汇率＝该币中间价 ÷ 美元中间价，用原始中间价（非 4 位）相除，结果四舍五入 4 位。
    返回 (值Decimal4, 算式字符串)。"""
    precise = _d(x_raw) / _d(usd_raw)
    value = round4(precise)
    formula = f"{_trim(x_raw)} ÷ {_trim(usd_raw)} = {precise.quantize(Decimal('0.000001'), ROUND_HALF_UP)}"
    return value, formula


def _trim(x) -> str:
    """去掉 Decimal 尾部无意义的 0，用于算式展示（9.0145 而非 9.01450000）。"""
    s = format(_d(x), "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


# ============================================================
# 规则引擎：生成当月应建的 8 条
# ============================================================

def _row(kind, from_name, to_code, to_name, rate, beg, end, src_date, src_url, basis, is_cross=False):
    code, disp = CURRENCY[from_name]
    return {
        "kind": kind,
        "from_code": code, "from_name": disp,
        "to_code": to_code, "to_name": to_name,
        "rate": rate,
        "beg_date": beg.isoformat(), "end_date": end.isoformat(),
        "source_date": src_date.isoformat(), "source_url": src_url,
        "basis": basis, "is_cross": is_cross,
    }


def generate_rows(year: int, month: int, org: str = DEFAULT_ORG, fetch=_http_get):
    """生成某结账月应建的 8 条 BD_Rate 行（不写金蝶）。
    月末条 5 条：生效=失效=自然月最后一天，值取当月最后一个公布日。
    次月区间条 3 条：生效=次月1日，失效=次月最后一天减1天，值取次月第一个公布日。
    返回 {org, year, month, rows, month_end_ann, next_range_ann, warnings}。"""
    try:
        me_ann = last_publish_in_month(year, month, fetch)
    except FxError:
        raise FxError(f"人行还没有 {year}-{month:02d} 的公布数据——该结账月可能尚未结束或人行未公布，"
                      f"无法建月末条。请确认结账月份，或待人行公布后再取数。")
    me_detail = get_announcement(me_ann["url"], fetch)
    ny, nm = next_month(year, month)
    try:
        nr_ann = first_publish_in_month(ny, nm, fetch)
    except FxError:
        raise FxError(f"结 {month} 月还差最后一步：要用次月（{ny}-{nm:02d}）第一个公布日的汇率建「次月区间条」"
                      f"（供 {nm} 月记账），但人行 {ny}-{nm:02d} 还没公布。请等 {nm} 月首个工作日人行公布后"
                      f"（约上午 9:15）再来取数——这也正是自动跑批定在「次月第一个工作日下午」的原因。")
    nr_detail = get_announcement(nr_ann["url"], fetch)

    warnings = []
    rows = []

    # --- 月末条 ---
    me_effect = month_last_day(year, month)  # 生效=失效=自然月最后一天（哪怕休市，D4）
    if me_ann["date"] != me_effect:
        warnings.append(f"月末 {me_effect} 非公布日（休市），取当月最后一个公布日 {me_ann['date']} 的中间价（D4）")
    _emit_to_rmb(rows, "month_end", IN_USE, me_detail["rates"], me_effect, me_effect,
                 me_ann["date"], me_ann["url"], warnings)
    # 交叉汇率（仅月末条）
    usd = me_detail["rates"].get("美元")
    if usd is None:
        warnings.append("缺美元中间价，无法算交叉汇率")
    else:
        for name in CROSS:
            r = me_detail["rates"].get(name)
            if r is None:
                warnings.append(f"缺 {name} 中间价，跳过其交叉汇率")
                continue
            val, formula = cross_rate(r["raw"], usd["raw"])
            code, disp = CURRENCY[name]
            rows.append({
                "kind": "month_end",
                "from_code": code, "from_name": disp,
                "to_code": CURRENCY["美元"][0], "to_name": CURRENCY["美元"][1],
                "rate": val,
                "beg_date": me_effect.isoformat(), "end_date": me_effect.isoformat(),
                "source_date": me_ann["date"].isoformat(), "source_url": me_ann["url"],
                "basis": formula, "is_cross": True,
            })

    # --- 次月区间条（只对人民币，不建交叉，照搬现行做法） ---
    nr_beg = datetime.date(ny, nm, 1)
    nr_end = month_last_day(ny, nm) - datetime.timedelta(days=1)  # 次月最后一天减1天（D6）
    if nr_ann["date"] != nr_beg:
        warnings.append(f"次月首日 {nr_beg} 非公布日，取次月第一个公布日 {nr_ann['date']} 的中间价")
    _emit_to_rmb(rows, "next_range", IN_USE, nr_detail["rates"], nr_beg, nr_end,
                 nr_ann["date"], nr_ann["url"], warnings)

    return {
        "org": org, "year": year, "month": month,
        "rows": rows,
        "month_end_ann": {"date": me_ann["date"].isoformat(), "url": me_ann["url"],
                          "sentence_date": me_detail["date"].isoformat() if me_detail["date"] else None},
        "next_range_ann": {"date": nr_ann["date"].isoformat(), "url": nr_ann["url"],
                           "sentence_date": nr_detail["date"].isoformat() if nr_detail["date"] else None},
        "warnings": warnings,
    }


def _emit_to_rmb(rows, kind, names, rates, beg, end, src_date, src_url, warnings):
    for name in names:
        r = rates.get(name)
        if r is None:
            warnings.append(f"缺 {name} 中间价（{src_date}），缺数即停")
            continue
        raw = r["raw"]
        rate = round4(raw)
        # 只抄不算：若原值本就 ≤4 位则「照抄」，否则标出「原值 → 4 位」
        basis = "公告原文照抄" if _d(raw) == rate else f"{_trim(raw)} → 4 位"
        rows.append(_row(kind, name, RMB_CODE, RMB_NAME, rate, beg, end, src_date, src_url, basis))


# ============================================================
# 机器闸门（D17）：全绿才可自动写入
# ============================================================

def run_gates(result: dict, prev_rates: dict = None, deviation=DEFAULT_DEVIATION,
              expected_count: int = 8):
    """对 generate_rows() 的结果跑四道闸门 + 不覆盖占位。返回 {passed, gates:[{name,status,detail}]}。
    status: ok / block / hold / warn。任一 block/hold => passed=False。
    prev_rates: {("PRE007","PRE001"): Decimal, ...} 上月同币对参考值，用于偏离闸门；缺省则该闸门跳过。"""
    rows = result.get("rows", [])
    gates = []

    # 1) 缺数闸门（铁律4）
    miss = [w for w in result.get("warnings", []) if "缺" in w and "中间价" in w]
    gates.append({"name": "缺数", "status": "block" if miss else "ok",
                  "detail": "；".join(miss) if miss else "在用币种齐备"})

    # 2) 完整性闸门（条数 = 应建）
    n = len(rows)
    gates.append({"name": "完整性", "status": "ok" if n == expected_count else "block",
                  "detail": f"生成 {n} 条，应建 {expected_count} 条"})

    # 3) 公布日闸门（取数日必须落在预期月份内，D12 兜底）
    dbad = []
    me_month = (result.get("year"), result.get("month"))
    ny, nm = next_month(*me_month) if me_month[0] else (None, None)
    for r in rows:
        d = datetime.date.fromisoformat(r["source_date"])
        want = me_month if r["kind"] == "month_end" else (ny, nm)
        if (d.year, d.month) != want:
            dbad.append(f"{r['from_name']}→{r['to_name']} 取数日 {r['source_date']} 不在预期月 {want[0]}-{want[1]:02d}")
    gates.append({"name": "公布日", "status": "block" if dbad else "ok",
                  "detail": "；".join(dbad) if dbad else "取数日均在预期区间"})

    # 4) 偏离上月闸门（超阈值 => 挂起待人工确认）
    if prev_rates:
        dev = []
        for r in rows:
            key = (r["from_code"], r["to_code"])
            base = prev_rates.get(key)
            if base is None or _d(base) == 0:
                continue
            change = abs(_d(r["rate"]) - _d(base)) / _d(base)
            if change > _d(deviation):
                dev.append(f"{r['from_name']}→{r['to_name']} {r['rate']} 较上月 {base} 偏离 {change:.2%}")
        gates.append({"name": "偏离上月", "status": "hold" if dev else "ok",
                      "detail": "；".join(dev) if dev else f"均在 ±{_d(deviation):.0%} 内"})
    else:
        gates.append({"name": "偏离上月", "status": "warn", "detail": "无上月参考值，未校验"})

    passed = all(g["status"] not in ("block", "hold") for g in gates)
    return {"passed": passed, "gates": gates}


# ============================================================
# 历史复核（D13）：把金蝶已建的对回人行，只标不改
# ============================================================

# 两条已知历史错值（v1.1 Q2「忽略」）→ 复核页打「已知豁免」，不重复飘红
KNOWN_EXEMPT = {
    ("101", "英镑", "美元", "2026-01-31"),
    ("101", "港币", "美元", "2025-12-31"),
}


def month_rates(year: int, month: int, fetch=_http_get):
    """某月的 月末公布 与 月初公布 两份详情（历史复核比对用，按月缓存）。"""
    me = get_announcement(last_publish_in_month(year, month, fetch)["url"], fetch)
    mf = get_announcement(first_publish_in_month(year, month, fetch)["url"], fetch)
    return {"month_end": me, "month_first": mf}


def pboc_value_for(row, month_pack):
    """按一条金蝶记录求其人行应有值。row: {原币/from_name(显示名), 目标币码/to_code, 生效, 失效}。
    month_pack = month_rates() 结果。对人民币：月末条(生效==失效)取月末公布、区间条取月初公布；
    对美元(交叉)：只月末条、由该月月末公布相除。返回 Decimal4 或 None。"""
    disp = row.get("原币") or row.get("from_name")
    pboc_name = DISP_TO_PBOC.get(disp, disp)
    to_code = row.get("目标币码") or row.get("to_code")
    beg = str(row.get("生效") or row.get("beg_date") or "")[:10]
    end = str(row.get("失效") or row.get("end_date") or "")[:10]
    me = (month_pack or {}).get("month_end") or {}
    mf = (month_pack or {}).get("month_first") or {}
    src = me if beg == end else mf
    r = (src.get("rates") or {}).get(pboc_name)
    if r is None:
        return None
    if to_code == RMB_CODE:
        return round4(r["raw"])
    if to_code == CURRENCY["美元"][0]:                 # 交叉→美元，只月末
        usd = (me.get("rates") or {}).get("美元")
        if usd is None:
            return None
        val, _ = cross_rate(r["raw"], usd["raw"])
        return val
    return None


def compare_history(kd_rows, pboc_lookup):
    """kd_rows: 金蝶已建汇率 [{org,原币/from_name,目标币码/to_code,rate,生效/beg_date,失效/end_date}]。
    pboc_lookup(row) -> 人行换算值 Decimal 或 None。
    返回逐条判定 [{..., pboc, diff, verdict}]，verdict: 一致 / 偏差 / 已知豁免 / 无法核对。"""
    out = []
    for r in kd_rows:
        acct = _d(r["rate"] if r.get("rate") is not None else r.get("汇率"))
        try:
            pboc = pboc_lookup(r)
        except FxError:
            pboc = None
        fname = r.get("原币") or r.get("from_name")
        tname = r.get("目标币") or r.get("to_name")
        beg = str(r.get("生效") or r.get("beg_date") or "")[:10]
        key = (str(r.get("org") or r.get("使用组织") or ""), fname, tname, beg)
        if pboc is None:
            verdict, diff = "无法核对", None
        else:
            pboc = round4(pboc)
            diff = acct - pboc
            if diff == 0:
                verdict = "一致"
            elif key in KNOWN_EXEMPT:
                verdict = "已知豁免"
            else:
                verdict = "偏差"
        out.append({**r, "pboc": (str(pboc) if pboc is not None else None),
                    "diff": (str(diff) if diff is not None else None), "verdict": verdict})
    return out


# ============================================================
# 金蝶 BD_Rate 写入模型 + 去重（纯函数，不 import kingdee_client；由 app 层拼装）
# ============================================================

def build_rate_model(row: dict, org: str) -> dict:
    """把 generate_rows() 的一行拼成金蝶 BD_Rate 的 Save Model。
    组织字段放最前（org 受控基础资料，组织上下文先行）；FExchangeRate 用 4 位值。"""
    return {
        "FCreateOrgId": {"FNumber": org},
        "FUseOrgId": {"FNumber": org},
        "FRATETYPEID": {"FNumber": RATE_TYPE},
        "FCyForID": {"FNumber": row["from_code"]},
        "FCyToID": {"FNumber": row["to_code"]},
        "FExchangeRate": float(round4(row["rate"])),
        "FBegDate": row["beg_date"],
        "FEndDate": row["end_date"],
        "FDescription": build_desc(row),   # 描述＝标记+算式/出处（金蝶自证来源、审核可核；见 build_desc）
    }


def find_existing(existing_rows, row: dict, org: str = None):
    """铁律5/不覆盖。★金蝶实证（2026-08-01）：汇率不重叠约束是【跨组织】的——同「汇率类型+原币+目标币」下
    生效期间在【全集团】唯一，101/107 是一条首尾相接、不重叠的接力时间线，不是两套并行。
    故判重【忽略组织】：任何组织已有同币对同生效区间即视为已存在（返回该行），否则写入会被金蝶以
    "生效期间重叠"拒绝。existing_rows 建议用 kingdee_client.fetch_bd_rate(use_org=None) 取全组织。"""
    for e in existing_rows:
        if (e.get("原币码") == row["from_code"] and e.get("目标币码") == row["to_code"]
                and str(e.get("生效", ""))[:10] == row["beg_date"]
                and str(e.get("失效", ""))[:10] == row["end_date"]):
            return e
    return None
