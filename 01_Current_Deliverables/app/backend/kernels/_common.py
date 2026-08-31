# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-08-01 | Author: Claude / c | Version: V2.158
# Description: 全台统一取数工具箱（第一步：只建不换，老内核一行不动）。
#   依《取数规则_跨工具对照表_v1.0_20260801》业务方 2026-08-01 拍板的 6 条口径：
#     ① 空格子：金额列当 0，数量/单价列当「不算数」(None)
#     ② 千分位 / 币种符号 / 空格 全认；读不懂的不静默——落一条警告
#     ③ 括号负数 (100) 认成 -100
#     ④ 账号取键：先去空格，再取最长且≥4 位的数字串
#     ⑤ 日期：横杠/斜杠/点/纯数字/中文/Excel 序列号 全认，单位数月日也认；
#        认不出留空 + 警告（取消银行流水导入原来的「原文照抄」）
#     ⑥ 判平容差收到一处，且可被 app.py 从基础设置覆盖
#
#   ⚠ 本模块只依赖标准库，不 import 任何其它内核、不 import db——
#     内核层必须保持纯函数、可单测、无副作用。容差的「可配」靠 configure_tolerances()
#     由 app.py 启动时注入，而不是让内核反向去读数据库。
from __future__ import annotations

import datetime
import math
import re
import unicodedata
from decimal import Decimal

# Excel 序列日期基准。1899-12-30 而非 12-31：Excel 沿用了 Lotus 1-2-3 把 1900 年
# 当闰年的历史 bug，减一天正好抵消（与 logistics_recon.py:15 保持一致）。
EPOCH = datetime.date(1899, 12, 30)

# Excel 序列号的合理区间：20000=1954-10-03，80000=2119-01-27。
# 下界必须挡住「2026」这种被误当序列号的年份（2026 号序列＝1905 年，明显不合理）。
_SERIAL_MIN, _SERIAL_MAX = 20000, 80000

# 会计里表示「无 / 零」的横杠写法，不算读不懂，不发警告
_NIL_MARKS = {"", "-", "--", "—", "——", "–", "/", "N/A", "n/a", "NA"}

# 币种符号：数字列里出现就洗掉，**不限位置**——"-¥100" 这种符号夹在负号后面的写法，
# 只 strip 两端是去不掉的。币种符号不会合法地出现在数字中间，整串删掉是安全的。
# 「元」只在结尾出现时才去（避免误伤以"元"开头的编码）。
_CURRENCY_RE = re.compile(r"[¥￥$＄€£₤]")

# 各种空白：半角/全角/不换行空格/窄空格/制表符
_SPACE_RE = re.compile(r"[\s   　]+")


def _halfwidth(s: str) -> str:
    """全角 → 半角。金蝶/银行导出里全角数字、全角逗号、全角括号都出现过。
    NFKC 一次搞定（１→1、，→,、（→(、．→.），且不动中文。"""
    return unicodedata.normalize("NFKC", s)


# ============================================================
# 一、金额 / 数量
# ============================================================

def parse_number(x):
    """把一个格子解析成数。返回 (值 or None, 说明)。

    说明取值：
      ""      解析成功
      "空"    空白 / 会计横杠 → 合法的「没有值」，**不算错，不发警告**
      其它    读不懂的原因，调用方据此落警告

    认：1234.56 / '1,234.56' / '¥100' / '100元' / '1 234.56' / '(100)' / '１２３'
    不认：'待定' / '见附件' / 日期 —— 一律返回 (None, 原因)
    """
    if x is None:
        return None, "空"

    # bool 是 int 的子类，先挡掉——openpyxl 读 TRUE/FALSE 单元格会给 bool，
    # 静默当成 1/0 是错的（"是否含税"列被当金额加进合计过就麻烦了）
    if isinstance(x, bool):
        return None, "布尔值"

    if isinstance(x, Decimal):
        if x.is_nan():
            return None, "非数值(NaN)"
        return float(x), ""

    if isinstance(x, (int, float)):
        f = float(x)
        if math.isnan(f):
            return None, "非数值(NaN)"          # pandas 空单元格 → NaN
        if math.isinf(f):
            return None, "非数值(无穷)"
        return f, ""

    s = _halfwidth(str(x)).strip()
    if s in _NIL_MARKS:
        return None, "空"

    # 括号负数：会计通用写法，(100) = -100。境外系统/审计底稿/花旗对账单常见。
    neg_paren = False
    if len(s) >= 3 and s[0] == "(" and s[-1] == ")":
        neg_paren = True
        s = s[1:-1].strip()

    s = _CURRENCY_RE.sub("", s).strip()            # 币种符号（不限位置）
    if s.endswith("元"):
        s = s[:-1].strip()
    s = _SPACE_RE.sub("", s)                       # 空格千分位
    s = s.replace(",", "")                         # 逗号千分位

    if s in _NIL_MARKS:
        return None, "空"

    try:
        f = float(s)
    except ValueError:
        return None, "读不懂「%s」" % (str(x).strip()[:20],)
    if math.isnan(f) or math.isinf(f):
        # float("nan") / float("inf") 是合法调用——必须显式挡，否则 NaN 混进合计，
        # 整个合计会变成 NaN（老实现 reconcile.to_float / balance_dashboard.to_float 就漏了这道）
        return None, "非数值"
    return (-f if neg_paren else f), ""


def to_amount(x, warn=None, where="") -> float:
    """【金额列】用这个。空格子 → 0.0（参与合计）；读不懂 → 0.0 且落警告。

    warn: 传一个 list 进来收警告；不传就不收（老调用方零改动）。
    where: 出问题时告诉人是哪一格，如 "第 12 行·运费"。
    """
    v, note = parse_number(x)
    if v is not None:
        return v
    if note != "空":
        _warn(warn, where, note, "已按 0 计入")
    return 0.0


def to_qty(x, warn=None, where=""):
    """【数量 / 单价 / 单位成本列】用这个。空格子 → None（这一行不算数）；
    读不懂 → None 且落警告。

    为什么和金额分开：单价为 0 通常是「没填」而不是「真免费」，当 0 会把单位成本算歪。
    """
    v, note = parse_number(x)
    if v is None and note != "空":
        _warn(warn, where, note, "已按「不算数」跳过")
    return v


def _warn(warn, where, note, action):
    if warn is None:
        return
    warn.append("%s%s，%s" % ((where + "：") if where else "", note, action))


# ============================================================
# 二、文本
# ============================================================

def to_text(x) -> str:
    """格子 → 干净字符串。None / NaN → ""。
    NaN 这道是必须的：pandas 读出来的空单元格 str() 出来是 "nan" 四个字母，
    直接当户名/摘要存下去就成了脏数据（老实现只有 logistics_accrual 挡了这道）。"""
    if x is None:
        return ""
    if isinstance(x, float) and math.isnan(x):
        return ""
    return str(x).strip()


# ============================================================
# 三、账号取键
# ============================================================

_ACCT_RE = re.compile(r"\d{4,}")


def norm_acct(text) -> str:
    """从核算维度文本里抽账号数字键。规则（业务方 2026-08-01 定）：
    **先去掉所有空格，再取最长的、且至少 4 位的数字串**；取不到返回 ""。

    去空格是为了把 '工行 6228 4800 1234 5678' 取成完整卡号——
    老实现不去空格，会取成 '6228'（并列最长取第一段），跟《账户台账》里存的
    完整卡号对不上，那一户直接落进「未映射」。

    ≥4 位是为了别把「2026」这种年份序号、「1」这种排序号误当账号。
    并列最长时取第一个（与老实现一致，不改这一条）。
    """
    if text is None:
        return ""
    s = _SPACE_RE.sub("", _halfwidth(str(text)))
    runs = _ACCT_RE.findall(s)
    return max(runs, key=len) if runs else ""


# ============================================================
# 四、日期
# ============================================================

_DATE_FMTS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d")
_CN_DATE_RE = re.compile(r"^(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?$")


def to_date(x, warn=None, where=""):
    """格子 → datetime.date，认不出返回 None（**不再原文照抄**）。

    认：date / datetime / Excel 序列号(数字或文本) / '2026-06-30' / '2026/6/30' /
        '2026.06.30' / '20260630' / '2026年6月30日' / 带时间的 '2026-06-30 09:12:00'
    空格子 → None，不发警告；读不懂 → None + 警告。
    """
    if x is None:
        return None
    if isinstance(x, bool):
        _warn(warn, where, "日期是布尔值", "已留空")
        return None
    if isinstance(x, datetime.datetime):
        return x.date()
    if isinstance(x, datetime.date):
        return x

    # 数值：Excel 序列号。老实现里只有物流对账认这个，其余三条线拿到裸序列号直接判无效。
    if isinstance(x, (int, float, Decimal)):
        f = float(x)
        if math.isnan(f) or math.isinf(f):
            return None
        return _from_serial(f, warn, where, x)

    s = _halfwidth(str(x)).strip()
    if s in _NIL_MARKS:
        return None

    # 带时间的取日期部分：'2026-06-30 09:12:00' / '2026-06-30T09:12:00'
    s = re.split(r"[ T]", s, maxsplit=1)[0].strip()

    m = _CN_DATE_RE.match(_halfwidth(str(x)).strip())
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)), warn, where, x)

    for fmt in _DATE_FMTS:
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue

    # 文本格式的 Excel 序列号（某些导出会把日期列整列存成文本）
    if s.isdigit() and _SERIAL_MIN <= int(s) <= _SERIAL_MAX:
        return _from_serial(float(s), warn, where, x)

    _warn(warn, where, "日期读不懂「%s」" % (str(x).strip()[:20],), "已留空")
    return None


def _from_serial(f, warn, where, raw):
    if not (_SERIAL_MIN <= f <= _SERIAL_MAX):
        _warn(warn, where, "日期读不懂「%s」" % (str(raw).strip()[:20],), "已留空")
        return None
    return EPOCH + datetime.timedelta(days=int(f))


def _safe_date(y, mo, d, warn, where, raw):
    try:
        return datetime.date(y, mo, d)
    except ValueError:
        _warn(warn, where, "日期不存在「%s」" % (str(raw).strip()[:20],), "已留空")
        return None


# ============================================================
# 五、判平容差
# ============================================================
# 容差是**业务口径**不是技术参数，所以：① 收到一处 ② 允许 app.py 从基础设置覆盖。
# 默认值＝各线现行值原样搬过来，换线时行为不变（换完再由业务方统一调整）。
_TOL_DEFAULTS = {
    "amount_abs":       0.01,   # 勾稽判平：差 1 分以内算平（资金看板/物流对账现行）
    "amount_rel":       0.0,
    "match_amount_abs": 1.0,    # 银行对账「做错·金额」配对：绝对 1 元（reconcile 现行）
    "match_amount_rel": 0.02,   # 同上，相对 2%，与绝对取大者
    "weight_abs":       0.001,  # 重量勾稽：0.001 吨（物流对账现行）
    "fx_deviation_rel": 0.03,   # 汇率偏离闸门：±3%（汇率录入现行）
}
TOLERANCES = dict(_TOL_DEFAULTS)


def configure_tolerances(overrides) -> dict:
    """app.py 启动时 / 基础设置保存后调用，把业务方在设置页配的容差灌进来。
    只认已知键、只认正数，其余原样保留——设置页填错不该把全台判平搞乱。
    返回实际生效的容差表。"""
    for k, v in (overrides or {}).items():
        if k not in _TOL_DEFAULTS:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f >= 0 and not math.isnan(f) and not math.isinf(f):
            TOLERANCES[k] = f
    return dict(TOLERANCES)


def reset_tolerances() -> None:
    """恢复默认（单测用，也给设置页的「恢复默认」按钮用）。"""
    TOLERANCES.clear()
    TOLERANCES.update(_TOL_DEFAULTS)


def tol(name: str) -> float:
    """取一条容差。键写错要当场报错，不能默默返回 0——0 容差＝什么都判不平。"""
    if name not in _TOL_DEFAULTS:
        raise KeyError("未知容差项：%s（可选：%s）" % (name, "/".join(sorted(_TOL_DEFAULTS))))
    return TOLERANCES[name]


def close_enough(a, b, abs_tol=None, rel_tol=0.0) -> bool:
    """|a-b| 在容差内算相等（**含边界**：容差 0.01 时，差 1 分算平）。
    绝对与相对取大者（与 reconcile 现行口径一致）。abs_tol 不传就用 amount_abs。

    ⚠ 那个 round(..., 9) 不是装饰：100.01 - 100.00 在浮点里等于 0.010000000000005，
      比 1 分**大**——不修的话"差 1 分算平"会被判成不平。抹掉小数点后 9 位以下的
      浮点噪声，真实差异（最小 1 分＝1e-2）远在其上，不会被误抹。

    ⚠ 口径微调：现行各线写的是「差 < 1 分」（差正好 1 分算不平），这里统一成
      「差 ≤ 1 分」（算平）——「容差 0.01」的自然读法。换线时会让「正好差 1 分」
      的行从「需人工复核」变成「平」，换第一条线时要拿实际数据确认业务方认可。
    """
    av, bv = to_amount(a), to_amount(b)
    at = tol("amount_abs") if abs_tol is None else float(abs_tol)
    limit = max(at, max(abs(av), abs(bv)) * float(rel_tol or 0.0))
    return round(abs(av - bv) - limit, 9) <= 0
