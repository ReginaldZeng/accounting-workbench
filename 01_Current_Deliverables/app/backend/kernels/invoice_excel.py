# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家）
# Description: 发票管家·表格内核（纯函数，不碰 db / core / 网络）。
#   读：read_table 通用读表（xlsx/xlsm/xls/csv，按同义词在前 15 行里找表头，容忍标题行、合并单元格、
#       两层表头、空行、合计行、文本数字、各种日期写法、发票号码被存成浮点丢精度）；
#       parse_taxlist 电子税务局「全量发票查询 › 取得发票」清单；parse_opening 票总管历史导出（期初查重底子）；
#       mark_deduct_file 在「抵扣类勾选」未勾选清单上逐行写 是/否，原文件其余部分原样保留。
#   写：export_ledger 发票台账（只写模式，大表不爆内存）、export_later 欠票清单（按供应商汇总＋明细）、
#       export_sellers 新销方核查清单。
#   ⚠ 三份导入件（票总管、税局两份）一期都还没见到实物——列名全靠同义词自动认，拿到样本后只需补同义词表。
#   ⚠ openpyxl / xlrd 一律函数内懒加载：缺库只让对应功能报友好话，不拖垮 import。
from __future__ import annotations

import csv
import datetime
import io
import math
import re
import unicodedata
import warnings as _pywarnings
from decimal import Decimal, InvalidOperation

try:                                    # 作为 kernels 包导入（路由里 from kernels import invoice_excel）
    from ._common import parse_number, to_date as _to_date
except ImportError:                     # 在 kernels 目录下直接跑
    from _common import parse_number, to_date as _to_date


# ============================================================
# 一、同义词表（路由拿去展示「识别到的列」；拿到真实样本后只改这里）
# ============================================================
# 每个字段的同义词按「越靠前越优先」排：同一个字段有两列都能对上时，取排前面的那个名字。
# 先整名精确对，再按「表头里含这个词」模糊对（模糊只用 ≥3 个字的词，免得「日期」「金额」误伤）。

FIELD_LABELS = {
    "numberSd": "数电票号码", "code": "发票代码", "number": "发票号码", "date": "开票日期",
    "sellerTaxId": "销方税号", "sellerName": "销方名称", "buyerTaxId": "购方税号", "buyerName": "购方名称",
    "amount": "金额（不含税）", "tax": "税额", "total": "价税合计", "status": "发票状态",
    "invType": "票种", "checkState": "勾选/用途状态", "positive": "是否正数发票", "risk": "风险等级",
    "source": "发票来源", "tick": "是否勾选", "deductTax": "有效抵扣税额",
    "refDoc": "报销单", "refPerson": "报销人/录入人",
}

_NUMBER_SD = ["数电票号码", "数电发票号码", "数电号码", "全电发票号码", "全电票号码"]
_SELLER_TAX = ["销方识别号", "销方纳税人识别号", "销售方纳税人识别号", "销售方识别号", "销方税号", "销售方税号",
               "销售方统一社会信用代码/纳税人识别号", "销方统一社会信用代码/纳税人识别号",
               "销售方统一社会信用代码", "销方统一社会信用代码"]
_SELLER_NAME = ["销方名称", "销售方名称", "销售方纳税人名称", "销方纳税人名称", "开票方名称", "销售方", "销方", "开票方"]
_BUYER_TAX = ["购方识别号", "购方纳税人识别号", "购买方纳税人识别号", "购买方识别号", "购方税号", "购买方税号",
              "购买方统一社会信用代码/纳税人识别号", "购方统一社会信用代码/纳税人识别号",
              "购买方统一社会信用代码", "购方统一社会信用代码"]
_BUYER_NAME = ["购方名称", "购买方名称", "购买方纳税人名称", "购方纳税人名称", "受票方名称", "购买方", "购方", "受票方"]

TAXLIST_SYNONYMS = {
    "numberSd": _NUMBER_SD,
    "code": ["发票代码"],
    "number": ["发票号码", "号码"],
    "date": ["开票日期", "开票时间", "发票日期", "日期"],
    "sellerTaxId": _SELLER_TAX,
    "sellerName": _SELLER_NAME,
    "buyerTaxId": _BUYER_TAX,
    "buyerName": _BUYER_NAME,
    "amount": ["金额", "不含税金额", "合计金额", "金额合计"],
    "tax": ["税额", "合计税额", "税额合计"],
    "total": ["价税合计", "价税合计金额", "含税金额", "票面金额"],
    "status": ["发票状态", "状态"],
    "invType": ["发票票种", "票种", "发票类型", "发票种类"],
    "checkState": ["勾选状态", "用途确认状态", "用途状态", "抵扣状态", "认证状态", "是否勾选", "勾选标志"],
    "positive": ["是否正数发票", "正数发票"],
    "risk": ["发票风险等级", "风险等级"],
    "source": ["发票来源"],
}

OPENING_SYNONYMS = {
    "numberSd": _NUMBER_SD,
    "code": ["发票代码"],
    "number": ["发票号码", "号码"],
    "date": ["开票日期", "发票日期", "开票时间", "日期"],
    # 票总管的"金额"多半是票面含税数（未见实物），所以放最后兜底：有价税合计先用价税合计
    "total": ["价税合计", "价税合计金额", "含税金额", "票面金额", "发票金额", "开票金额", "金额合计", "合计金额", "金额"],
    "sellerName": _SELLER_NAME + ["销货单位", "供应商名称", "供应商"],
    "buyerName": _BUYER_NAME + ["发票抬头", "抬头"],
    "refDoc": ["报销单名称", "报销单号", "报销单编号", "单据编号", "单据号", "单据名称", "关联单据", "审批编号", "单据"],
    "refPerson": ["报销人", "录入人", "提交人", "经办人", "上传人", "创建人"],
}

# 抵扣类勾选导出里「是否勾选」那一列（写 是/否 的地方）
TICK_SYNONYMS = ["是否勾选", "勾选标志", "勾选状态", "是否勾选抵扣", "勾选"]

DEDUCT_SYNONYMS = {
    "numberSd": _NUMBER_SD,
    "code": ["发票代码"],
    "number": ["发票号码", "号码"],
    "date": ["开票日期", "开票时间", "发票日期", "日期"],
    "sellerTaxId": _SELLER_TAX,
    "sellerName": _SELLER_NAME,
    "deductTax": ["有效抵扣税额", "本次有效抵扣税额", "有效税额", "可抵扣税额", "抵扣税额"],
    "amount": ["金额", "不含税金额", "合计金额", "金额合计"],
    "tax": ["税额", "合计税额", "税额合计"],
    "total": ["价税合计", "价税合计金额", "含税金额", "票面金额"],
    "status": ["发票状态", "状态"],
    "invType": ["发票票种", "票种", "发票类型", "发票种类"],
    "risk": ["发票风险等级", "风险等级"],
    "source": ["发票来源"],
    "tick": TICK_SYNONYMS,
}

# 模糊对（表头含同义词）时的排除词：防"原发票号码/红字发票号码"抢号码列、"有效抵扣税额"抢税额列、
# "勾选日期"抢开票日期列、"销售方地址电话"抢销方名称列
_EXCLUDE = {
    "numberSd": ("原", "蓝字", "红字", "对应", "关联"),
    "number": ("原", "蓝字", "红字", "对应", "关联"),
    "code": ("原", "蓝字", "红字", "对应", "关联"),
    "sellerName": ("识别号", "税号", "代码", "地址", "电话", "银行", "账号", "开户"),
    "buyerName": ("识别号", "税号", "代码", "地址", "电话", "银行", "账号", "开户"),
    "amount": ("税", "抵扣"),
    "tax": ("抵扣", "有效", "率"),
    "date": ("勾选", "确认", "认证", "入账", "导入", "录入", "报销", "提交"),
    "checkState": ("时间", "日期", "人"),
    "tick": ("时间", "日期", "人", "批次", "用途"),
    "refDoc": ("日期", "时间", "金额"),
    "refPerson": ("日期", "时间", "部门"),
}


def _all_synonyms():
    """通用读表不知道是哪种表时，用三张表的并集找表头。"""
    out = {}
    for tbl in (TAXLIST_SYNONYMS, OPENING_SYNONYMS, DEDUCT_SYNONYMS):
        for f, syns in tbl.items():
            lst = out.setdefault(f, [])
            for s in syns:
                if s not in lst:
                    lst.append(s)
    return out


ALL_SYNONYMS = _all_synonyms()


def mapping_lines(mapping):
    """{字段: 表头} → ["发票号码 ← 数电票号码", …]，给页面「识别到的列」直接显示。"""
    return ["%s ← %s" % (FIELD_LABELS.get(f, f), h) for f, h in (mapping or {}).items()]


# ============================================================
# 二、格子清洗
# ============================================================

_WS_RE = re.compile(r"\s+")
_PAREN_RE = re.compile(r"[\(\[【][^\)\]】]*[\)\]】]")
_SCI_RE = re.compile(r"^[+-]?\d+(\.\d+)?[eE][+]?\d+$")
_ILLEGAL_RE = re.compile(r"[\000-\010]|[\013-\014]|[\016-\037]")   # 与 openpyxl ILLEGAL_CHARACTERS_RE 同口径


def _norm_h(s):
    """表头归一：全角转半角、去所有空白（含换行）、去 * 必填星号、去结尾冒号。"""
    if s is None:
        return ""
    t = unicodedata.normalize("NFKC", str(s))
    t = _WS_RE.sub("", t).replace("*", "").rstrip(":")
    return t


def _norm_h2(s):
    """再去掉括号里的单位注记：「价税合计(元)」→「价税合计」。"""
    return _PAREN_RE.sub("", s)


def _cell(v):
    """读出来的格子 → JSON 可存的值（raw_json 要落库）。日期统一成文本，其余原样。"""
    if v is None:
        return None
    if isinstance(v, datetime.datetime):
        if v.hour == 0 and v.minute == 0 and v.second == 0:
            return v.strftime("%Y-%m-%d")
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, datetime.date):
        return v.isoformat()
    if isinstance(v, datetime.time):
        return v.isoformat()
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, str):
        v = v.strip()
        return v if v else None
    return v


def _blank(v):
    return v is None or (isinstance(v, str) and not v.strip())


def _row_blank(row):
    return all(_blank(v) for v in row)


_TOTAL_WORDS = {"合计", "总计", "小计", "本页合计", "总合计", "合计金额", "本月合计", "累计"}
_TOTAL_PREFIX_RE = re.compile(r"^(合计|总计|小计)[:：(（]")


def _is_total_row(row):
    """前 3 个非空格子里出现「合计/总计/小计」就是合计行——合计行不当数据、也绝不改写。"""
    seen = 0
    for v in row:
        if _blank(v):
            continue
        seen += 1
        if isinstance(v, str):
            t = _WS_RE.sub("", v)
            if t in _TOTAL_WORDS or _TOTAL_PREFIX_RE.match(t):
                return True
        if seen >= 3:
            break
    return False


def _norm_no(v, kind="number"):
    """发票号码/代码 → (文本, 是否丢了精度, 是否数字格式存的)。

    文本格子原样保留（只去空白、去 ="…" 包装和前导撇号）；数字格子转成整数文本。
    Excel 只存 15 位有效数字——20 位的数电票号码一旦被存成数字，尾数就已经没了，只能报警让人重导。
    """
    if v is None or isinstance(v, bool):
        return "", False, False
    if isinstance(v, (int, float, Decimal)):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return "", False, True
        try:
            d = Decimal(repr(v)) if isinstance(v, float) else Decimal(v)
            s = format(d.quantize(Decimal(1)) if d == d.to_integral_value() else d, "f")
        except (InvalidOperation, ValueError):
            return str(v), False, True
        s = s.lstrip("+")
        lost = len(s.lstrip("-")) > 15
        s = _pad_no(s, kind)
        return s, lost, True
    s = unicodedata.normalize("NFKC", str(v)).strip()
    if s.startswith('="') and s.endswith('"'):
        s = s[2:-1]
    s = _WS_RE.sub("", s.lstrip("'`"))
    if re.fullmatch(r"\d+\.0+", s):          # pandas/CSV 转存留下的 "12345678.0"
        s = s.split(".")[0]
    if _SCI_RE.match(s):                     # 文本里就是 "2.6422E+19"：导出前已经丢了精度
        try:
            s = format(Decimal(s).quantize(Decimal(1)), "f")
        except (InvalidOperation, ValueError):
            pass
        return s, True, True
    return s, False, False


def _pad_no(s, kind):
    """数字格式存的号码/代码会丢前导零：老版发票号码固定 8 位；代码 10 或 12 位（北京等地区以 0 开头）。"""
    if not s.isdigit():
        return s
    if kind == "number" and len(s) < 8:
        return s.zfill(8)
    if kind == "code" and len(s) in (9, 11):
        return s.zfill(len(s) + 1)
    return s


def _date_str(v, w, row_no, label="开票日期"):
    """各种日期写法 → 'YYYY-MM-DD'；读不懂留空并记一条警告。"""
    if _blank(v):
        return ""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        f = float(v)
        if f.is_integer() and 19000101 <= f <= 21001231:     # 20260923 被存成了数字
            v = str(int(f))
    tmp = []
    d = _to_date(v, tmp)
    if d is None:
        w.add("date", row_no, "%s读不懂（如「%s」），已留空" % (label, str(v)[:20]))
        return ""
    return d.isoformat()


def _num(v, w, row_no, label):
    """金额 → float；空格子 → None（不是 0：缺金额和金额为 0 是两回事）；读不懂记警告。"""
    x, note = parse_number(v)
    if x is None:
        if note != "空":
            w.add("num:" + label, row_no, "%s读不懂（如「%s」），已留空" % (label, str(v)[:20]))
        return None
    return round(x, 2)


def _txt(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


class _Warns:
    """同类警告合并成一条："第 5、8、12 行等共 30 行：……"——几千行的清单不能刷几千条警告。"""

    def __init__(self):
        self._order = []
        self._data = {}
        self.plain = []

    def add(self, key, row_no, msg):
        if key not in self._data:
            self._data[key] = [msg, []]
            self._order.append(key)
        if row_no is not None:
            self._data[key][1].append(row_no)

    def note(self, msg):
        if msg not in self.plain:
            self.plain.append(msg)

    def lines(self):
        out = list(self.plain)
        for k in self._order:
            msg, rows = self._data[k]
            if not rows:
                out.append(msg)
                continue
            head = "、".join(str(r) for r in rows[:5])
            more = "等共 %d 行" % len(rows) if len(rows) > 5 else ""
            out.append("第 %s 行%s：%s" % (head, more, msg))
        return out


# ============================================================
# 三、读文件（xlsx/xlsm → openpyxl 只读；xls → xlrd；csv → 标准库）
# ============================================================

_HEAD_SCAN = 15


class _BadFile(Exception):
    """读不了的文件；消息是给财务看的白话。"""


def _sniff(data, filename):
    fn = (filename or "").lower()
    if data[:4] == b"PK\x03\x04":
        return "xlsx"
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "xls"
    head = data[:512].lstrip().lower()
    if head.startswith(b"<"):
        raise _BadFile("这个文件其实是网页格式（有的系统导出的 .xls 是这样），请用 Excel 打开后「另存为 .xlsx」再上传")
    if fn.endswith((".xlsx", ".xlsm", ".xls")):
        raise _BadFile("文件打不开：看着不像 Excel 文件，可能已损坏或只下载了一半，请重新导出")
    return "csv"


class _Reader:
    """统一三种格式：sheet_names() / head(name, n) / rows(name)。行都是 list，格子已过 _cell。"""

    def __init__(self, data, filename):
        self.fmt = _sniff(data, filename)
        self._data = data
        self._wb = None
        if self.fmt == "xlsx":
            try:
                from openpyxl import load_workbook
            except ImportError:
                raise _BadFile("服务器缺 Excel 读取组件（openpyxl），请管理员安装后重试")
            try:
                with _pywarnings.catch_warnings():
                    _pywarnings.simplefilter("ignore")
                    self._wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            except Exception as e:
                raise _BadFile("Excel 打不开（%s）：文件可能加了密码或已损坏，请重新导出" % type(e).__name__)
            self._names = [ws.title for ws in self._wb.worksheets
                           if getattr(ws, "sheet_state", "visible") == "visible"] or list(self._wb.sheetnames)
        elif self.fmt == "xls":
            try:
                import xlrd
            except ImportError:
                raise _BadFile("服务器缺 .xls 读取组件（xlrd），请管理员安装，或把文件另存为 .xlsx 再上传")
            try:
                self._wb = xlrd.open_workbook(file_contents=data)
            except Exception as e:
                raise _BadFile("老版 .xls 打不开（%s）：文件可能加了密码或已损坏，请另存为 .xlsx 再上传" % type(e).__name__)
            self._xlrd = xlrd
            self._names = [s.name for s in self._wb.sheets() if getattr(s, "visibility", 0) == 0] \
                or self._wb.sheet_names()
        else:
            self._csv_rows = _read_csv(data)
            self._names = ["CSV"]

    def sheet_names(self):
        return list(self._names)

    def rows(self, name, limit=None):
        n = 0
        for row in self._iter(name):
            yield row
            n += 1
            if limit is not None and n >= limit:
                return

    def head(self, name, n=_HEAD_SCAN):
        return list(self.rows(name, n))

    def _iter(self, name):
        if self.fmt == "xlsx":
            ws = self._wb[name]
            try:
                ws.reset_dimensions()       # 有的导出工具把表范围写成 A1，不重置只读得到第一行
            except Exception:
                pass
            for r in ws.iter_rows(values_only=True):
                yield [_cell(v) for v in r]
        elif self.fmt == "xls":
            sh = self._wb.sheet_by_name(name)
            xlrd = self._xlrd
            for r in range(sh.nrows):
                out = []
                for c in range(sh.ncols):
                    cell = sh.cell(r, c)
                    t, v = cell.ctype, cell.value
                    if t in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK, xlrd.XL_CELL_ERROR):
                        out.append(None)
                    elif t == xlrd.XL_CELL_DATE:
                        try:
                            out.append(_cell(xlrd.xldate_as_datetime(v, self._wb.datemode)))
                        except Exception:
                            out.append(v)
                    elif t == xlrd.XL_CELL_BOOLEAN:
                        out.append(bool(v))
                    else:
                        out.append(_cell(v))
                yield out
        else:
            for r in self._csv_rows:
                yield [_cell(v) for v in r]

    def close(self):
        if self.fmt == "xlsx" and self._wb is not None:
            try:
                self._wb.close()
            except Exception:
                pass


def _read_csv(data):
    """CSV：先按 UTF-8（带不带 BOM 都行）解，失败按 GB18030（GBK 的超集）解；分隔符自动认 , 制表符 ;。"""
    text = None
    for enc in ("utf-8-sig", "gb18030"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise _BadFile("文件编码认不出来，请用 Excel 打开后另存为 .xlsx 再上传")
    sample = text[:4096]
    delim = ","
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=",\t;").delimiter
    except csv.Error:
        if sample.count("\t") > sample.count(","):
            delim = "\t"
    return list(csv.reader(io.StringIO(text), delimiter=delim))


# ============================================================
# 四、找表头
# ============================================================

def _map_headers(headers, synonyms):
    """表头列表 → {字段: 列号}。先整名精确对（按同义词优先级），再按「含」模糊对。一列只归一个字段。"""
    norm = [_norm_h(h) for h in headers]
    norm2 = [_norm_h2(h) for h in norm]
    used, out = set(), {}
    for f, syns in synonyms.items():
        for syn in syns:
            hit = None
            for i, (a, b) in enumerate(zip(norm, norm2)):
                if i not in used and a and (a == syn or b == syn):
                    hit = i
                    break
            if hit is not None:
                out[f] = hit
                used.add(hit)
                break
    for f, syns in synonyms.items():
        if f in out:
            continue
        excl = _EXCLUDE.get(f, ())
        best = None
        for syn in syns:
            if len(syn) < 3:
                continue
            for i, a in enumerate(norm):
                if i in used or not a or syn not in a:
                    continue
                if any(x in a and x not in syn for x in excl):
                    continue
                if best is None or len(a) < len(norm[best]):
                    best = i
            if best is not None:
                break
        if best is not None:
            out[f] = best
            used.add(best)
    return out


def _looks_like_header_row(row):
    """第二层表头应该全是文字：有数字、日期、长数字串的行是数据，不是表头。"""
    seen = False
    for v in row:
        if _blank(v):
            continue
        seen = True
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return False
        s = str(v).strip()
        x, _note = parse_number(s)
        if x is not None:
            return False
        if re.search(r"\d{4}[-/年.]\d{1,2}", s) or re.search(r"\d{6,}", s):
            return False
    return seen


def _combine(parent, child):
    """两层表头合成一层：上层合并格（只在首列有字）向右顺延到下层有字的列。"""
    n = max(len(parent), len(child))
    parent = list(parent) + [None] * (n - len(parent))
    child = list(child) + [None] * (n - len(child))
    out, carry = [], None
    for p, c in zip(parent, child):
        p = None if _blank(p) else str(p).strip()
        c = None if _blank(c) else str(c).strip()
        if p is not None:
            carry = p
        if c is None:
            # 下层没字＝上层是竖着合并的单列表头（如「序号」），不往右顺延
            out.append(p or "")
            carry = None
        elif p is not None:
            out.append(p + c)
        elif carry is not None:
            out.append(carry + c)
        else:
            out.append(c)
    return out


def _find_header(head_rows, synonyms):
    """前 15 行里挑同义词命中最多的一行（同分取靠上的）；两层表头合起来命中更多就用合起来的。
    返回 (命中数, 表头行下标0起, 表头占几行, 表头文字列表, {字段: 列号})。"""
    best = (0, -1, 1, [], {})
    for r, row in enumerate(head_rows):
        if _row_blank(row):
            continue
        hdr = ["" if _blank(v) else str(v).strip() for v in row]
        mp = _map_headers(hdr, synonyms)
        cand = (len(mp), r, 1, hdr, mp)
        if r + 1 < len(head_rows) and _looks_like_header_row(head_rows[r + 1]):
            hdr2 = _combine(row, head_rows[r + 1])
            mp2 = _map_headers(hdr2, synonyms)
            nxt = _map_headers(["" if _blank(v) else str(v).strip() for v in head_rows[r + 1]], synonyms)
            # 合起来要比「上一行单独」和「下一行单独」都强才算两层表头——
            # 否则合并标题格（如「票总管 发票导出」）会被当成上层表头，拼到每个列名前面
            if len(mp2) > max(len(mp), len(nxt)):
                cand = (len(mp2), r, 2, hdr2, mp2)
        if cand[0] > best[0]:
            best = cand
    return best


def _scan(data, filename, synonyms=None, sheet=None, min_hits=2):
    """读表主流程。返回 dict：ok/msg/fmt/sheet/header_row(Excel 行号,1起)/headers/rows/row_nos/col_map/warnings。
    rows 已去掉空行和合计行；row_nos 与 rows 一一对应，是原表里的行号（改写勾选列要用）。"""
    syn = synonyms or ALL_SYNONYMS
    res = {"ok": False, "msg": "", "fmt": "", "sheet": "", "header_row": 0, "header_rows": 0,
           "headers": [], "rows": [], "row_nos": [], "col_map": {}, "warnings": [], "skipped_total": []}
    if not data:
        res["msg"] = "文件是空的"
        return res
    try:
        rd = _Reader(data, filename)
    except _BadFile as e:
        res["msg"] = str(e)
        return res
    try:
        res["fmt"] = rd.fmt
        names = rd.sheet_names()
        if sheet:
            if sheet not in names:
                res["msg"] = "找不到工作表「%s」" % sheet
                return res
            names = [sheet]
        pick = None
        for nm in names:
            hits, r, span, hdr, mp = _find_header(rd.head(nm), syn)
            if pick is None or hits > pick[1]:
                pick = (nm, hits, r, span, hdr, mp)
        if pick is None:
            res["msg"] = "文件里没有可读的工作表"
            return res
        nm, hits, r, span, hdr, mp = pick
        res["sheet"] = nm
        if hits < min_hits:
            # 一个标准列名都没认出来：通用读表退而求其次，拿第一行有 ≥2 格文字的当表头
            r = -1
            for i, row in enumerate(rd.head(nm)):
                if sum(1 for v in row if isinstance(v, str) and v.strip()) >= 2:
                    r = i
                    break
            if r < 0:
                res["msg"] = "没找到表头：前 %d 行里没有认得的列名（如 发票号码、开票日期、价税合计）" % _HEAD_SCAN
                return res
            span = 1
            hdr = ["" if _blank(v) else str(v).strip() for v in rd.head(nm)[r]]
            mp = _map_headers(hdr, syn)
            res["warnings"].append("没认出标准列名，按第 %d 行当表头" % (r + 1))
        # 表头去掉末尾空列
        while hdr and not hdr[-1]:
            hdr.pop()
        ncol = len(hdr)
        res.update(header_row=r + span, header_rows=span, headers=hdr, col_map=mp)
        start = r + span
        for i, row in enumerate(rd.rows(nm)):
            if i < start:
                continue
            row = list(row[:ncol]) + [None] * max(0, ncol - len(row))
            if _row_blank(row):
                continue
            if _is_total_row(row):
                res["skipped_total"].append(i + 1)
                continue
            res["rows"].append(row)
            res["row_nos"].append(i + 1)
        if res["skipped_total"]:
            res["warnings"].append("已跳过合计行（第 %s 行）" % "、".join(str(x) for x in res["skipped_total"][:5]))
        res["ok"] = True
        return res
    except _BadFile as e:
        res["msg"] = str(e)
        return res
    except Exception as e:                    # 奇形怪状的导出件：报白话，不抛 500
        res["msg"] = "表格读取出错（%s: %s），请把文件另存为 .xlsx 后重试" % (type(e).__name__, str(e)[:80])
        return res
    finally:
        rd.close()


def read_table(data, filename, synonyms=None, sheet=None):
    """通用读表。→ {"headers", "rows", "header_row"(Excel 行号,1起), "sheet", "warnings",
    另附 "ok", "msg", "row_nos"(每行在原表的行号), "col_map"({字段: 列号})}。
    synonyms 不传＝用三张表同义词的并集找表头；多个工作表时挑命中最多的那张。"""
    s = _scan(data, filename, synonyms, sheet, min_hits=2)
    return {"ok": s["ok"], "msg": s["msg"], "headers": s["headers"], "rows": s["rows"],
            "header_row": s["header_row"], "sheet": s["sheet"], "warnings": s["warnings"],
            "row_nos": s["row_nos"], "col_map": s["col_map"]}


# ============================================================
# 五、解析：税局清单 / 票总管期初
# ============================================================

def _raw(headers, row):
    """原行 → {表头: 值}，重名表头加「#2」；落 raw_json 用。"""
    out = {}
    for i, h in enumerate(headers):
        k = h or ("第%d列" % (i + 1))
        if k in out:
            j = 2
            while "%s#%d" % (k, j) in out:
                j += 1
            k = "%s#%d" % (k, j)
        v = row[i] if i < len(row) else None
        out[k] = v
    return out


def _getter(row, col_map):
    def g(f):
        i = col_map.get(f)
        return row[i] if i is not None and i < len(row) else None
    return g


def _pick_number(g, w, row_no):
    """号码取法：两列都有时优先 20 位数电票号码；老版票用 发票号码 ＋ 发票代码。
    → (号码, 代码, 号码是否丢精度)"""
    sd, sd_lost, _a = _norm_no(g("numberSd"), "sd")
    no, no_lost, _b = _norm_no(g("number"), "number")
    code, code_lost, _c = _norm_no(g("code"), "code")
    if len(sd) == 20 and sd.isdigit():
        number, lost = sd, sd_lost
    elif len(no) == 20 and no.isdigit():
        number, lost = no, no_lost
    elif no:
        number, lost = no, no_lost
    else:
        number, lost = sd, sd_lost
    if lost:
        w.add("lost", row_no, "发票号码是数字格式存的、超过 15 位，Excel 已把尾数丢了（如 %s），"
                              "这几张票比对不准——请重新导出，或把号码列设成「文本」后再导" % number)
    if code_lost:
        w.add("codelost", row_no, "发票代码是数字格式存的、超过 15 位，尾数可能已丢")
    return number, code, lost


def _dup_numbers(rows, w):
    seen, dup = set(), 0
    for r in rows:
        k = (r.get("code") or "", r.get("number") or "")
        if not k[1]:
            continue
        if k in seen:
            dup += 1
        seen.add(k)
    if dup:
        w.note("文件里有 %d 行发票号码与前面重复（已照常读入，请留意是否导重了）" % dup)


def _result_base(s):
    headers = s["headers"]
    mapping = {f: headers[i] for f, i in s["col_map"].items() if i < len(headers)}
    return {"ok": False, "rows": [], "mapping": mapping, "headers": headers,
            "headerRow": s["header_row"], "sheet": s["sheet"], "warnings": list(s["warnings"]), "msg": s["msg"]}


def parse_taxlist(data, filename):
    """电子税务局「税务数字账户 › 发票查询统计 › 全量发票查询 › 取得发票」导出 → 清单行。
    → {"ok", "rows": [{number, code, date, sellerTaxId, sellerName, buyerTaxId, buyerName, amount, tax, total,
       status, invType, checkState, raw, positive, risk, source, rowNo, numberLost}], "mapping", "warnings", "msg",
       另附 "headers", "headerRow", "sheet"}。金额读不到为 None（不是 0）。"""
    s = _scan(data, filename, TAXLIST_SYNONYMS, min_hits=2)
    out = _result_base(s)
    if not s["ok"]:
        return out
    cm = s["col_map"]
    if "number" not in cm and "numberSd" not in cm:
        out["msg"] = "没认出「发票号码」列。识别到的列：%s" % ("、".join(mapping_lines(out["mapping"])) or "无")
        return out
    w = _Warns()
    rows, skipped = [], 0
    for row, rn in zip(s["rows"], s["row_nos"]):
        g = _getter(row, cm)
        number, code, lost = _pick_number(g, w, rn)
        amount, tax, total = _num(g("amount"), w, rn, "金额"), _num(g("tax"), w, rn, "税额"), _num(g("total"), w, rn, "价税合计")
        if not number:
            if amount is not None or total is not None:
                w.add("nonum", rn, "没有发票号码，已跳过")
            skipped += 1
            continue
        pos = _txt(g("positive"))
        rows.append({
            "number": number, "code": code, "date": _date_str(g("date"), w, rn),
            "sellerTaxId": _txt(g("sellerTaxId")).upper(), "sellerName": _txt(g("sellerName")),
            "buyerTaxId": _txt(g("buyerTaxId")).upper(), "buyerName": _txt(g("buyerName")),
            "amount": amount, "tax": tax, "total": total,
            "status": _txt(g("status")), "invType": _txt(g("invType")), "checkState": _txt(g("checkState")),
            "positive": (False if pos in ("否", "N", "负数") else True if pos in ("是", "Y", "正数") else None),
            "risk": _txt(g("risk")), "source": _txt(g("source")),
            "raw": _raw(s["headers"], row), "rowNo": rn, "numberLost": lost,
        })
    _dup_numbers(rows, w)
    if "total" not in cm and "amount" not in cm:
        w.note("没认出金额列（价税合计/金额），只能按号码比对")
    out["rows"] = rows
    out["warnings"] += w.lines()
    out["ok"] = True
    out["msg"] = "读到 %d 张票" % len(rows) + ("，跳过 %d 行没号码的" % skipped if skipped else "")
    return out


def parse_opening(data, filename):
    """票总管导出的历史发票清单（期初查重底子）→ 行。列名未见实物，全靠同义词。
    → {"ok", "rows": [{number, code, date, total, sellerName, buyerName, ref, raw, rowNo, numberLost}],
       "mapping", "warnings", "msg", "headers", "headerRow", "sheet"}"""
    s = _scan(data, filename, OPENING_SYNONYMS, min_hits=2)
    out = _result_base(s)
    if not s["ok"]:
        return out
    cm = s["col_map"]
    if "number" not in cm and "numberSd" not in cm:
        out["msg"] = "没认出「发票号码」列。识别到的列：%s" % ("、".join(mapping_lines(out["mapping"])) or "无")
        return out
    w = _Warns()
    rows, skipped = [], 0
    for row, rn in zip(s["rows"], s["row_nos"]):
        g = _getter(row, cm)
        number, code, lost = _pick_number(g, w, rn)
        total = _num(g("total"), w, rn, "金额")
        if not number:
            if total is not None:
                w.add("nonum", rn, "没有发票号码，已跳过（收据等不参与查重）")
            skipped += 1
            continue
        ref_parts = []
        doc, who = _txt(g("refDoc")), _txt(g("refPerson"))
        if doc:
            ref_parts.append(doc)
        if who:
            ref_parts.append(who)
        rows.append({
            "number": number, "code": code, "date": _date_str(g("date"), w, rn),
            "total": total, "sellerName": _txt(g("sellerName")), "buyerName": _txt(g("buyerName")),
            "ref": " / ".join(ref_parts)[:200],
            "raw": _raw(s["headers"], row), "rowNo": rn, "numberLost": lost,
        })
    _dup_numbers(rows, w)
    out["rows"] = rows
    out["warnings"] += w.lines()
    out["ok"] = True
    out["msg"] = "读到 %d 张票" % len(rows) + ("，跳过 %d 行没号码的" % skipped if skipped else "")
    return out


# ============================================================
# 六、抵扣勾选：在税局导出的未勾选清单上写 是/否
# ============================================================

_YES = {"是", "yes", "y", "true", "1", "勾选"}
_NO = {"否", "no", "n", "false", "0", "不勾选"}


def _norm_decision(d):
    if d is True:
        return "是"
    if d is False:
        return "否"
    if d is None:
        return None
    t = str(d).strip().lower()
    if t in _YES:
        return "是"
    if t in _NO:
        return "否"
    return None


def _find_tick(headers, col_map):
    """勾选列：先按同义词认；再退到「表头含 勾选、但不是 勾选时间/勾选人 之类」的列。"""
    if "tick" in col_map:
        return col_map["tick"]
    for i, h in enumerate(headers):
        a = _norm_h(h)
        if "勾选" in a and not any(x in a for x in _EXCLUDE["tick"]):
            return i
    return None


def mark_deduct_file(data, filename, decide):
    """电子税务局「抵扣类勾选」导出的未勾选清单 → 逐行写 是/否，交回去「清单导入勾选」。

    decide(row) -> (决定 "是"|"否"|None, 原因)；row 同 parse_taxlist 的行，另有 tick（原值）、deductTax、rowNo。
    None＝这张不表态，格子原样不动。只改勾选列的数据格——表头、合计行、其它列、样式、合并格一律不碰。
    .xls/.csv 进来只能另存成新的 .xlsx（格式保不住），会警告。
    → {"ok", "bytes", "summary": {rows, yes, no, unknown}, "preview": [{rowNo, number, decision, reason}](前 200),
       "mapping", "warnings", "msg", "tickColumn", "sheet", "headerRow"}"""
    s = _scan(data, filename, DEDUCT_SYNONYMS, min_hits=2)
    headers = s["headers"]
    out = {"ok": False, "bytes": b"", "summary": {"rows": 0, "yes": 0, "no": 0, "unknown": 0}, "preview": [],
           "mapping": {f: headers[i] for f, i in s["col_map"].items() if i < len(headers)},
           "warnings": list(s["warnings"]), "msg": s["msg"], "tickColumn": "", "sheet": s["sheet"],
           "headerRow": s["header_row"]}
    if not s["ok"]:
        return out
    cm = s["col_map"]
    if "number" not in cm and "numberSd" not in cm:
        out["msg"] = "没认出「发票号码」列，没法逐张判断。识别到的列：%s" % ("、".join(mapping_lines(out["mapping"])) or "无")
        return out
    try:
        from openpyxl import Workbook, load_workbook
    except ImportError:
        out["msg"] = "服务器缺 Excel 组件（openpyxl），请管理员安装后重试"
        return out

    w = _Warns()
    tick = _find_tick(headers, cm)
    appended = tick is None
    if appended:
        tick = len(headers)
        w.note("原表里没找到「是否勾选」列，已在最后加了一列「是否勾选」——导入税局前请核对税局模板要不要这一列")
    else:
        out["mapping"]["tick"] = headers[tick]
        odd = sorted({_txt(r[tick]) for r in s["rows"] if tick < len(r)} - {"", "是", "否"})
        if odd:
            w.note("「%s」列原来填的是「%s」，现按「是/否」写入；如税局导入报错请告诉我们" % (headers[tick], "、".join(odd[:3])))
    out["tickColumn"] = "是否勾选" if appended else headers[tick]

    # 逐行问 decide
    decisions = []                       # (Excel 行号, 是/否/None)
    summ = out["summary"]
    for row, rn in zip(s["rows"], s["row_nos"]):
        g = _getter(row, cm)
        number, code, lost = _pick_number(g, w, rn)
        if not number:
            continue                     # 没号码的行（备注、说明）不是票，不问也不改
        rd = {
            "number": number, "code": code, "date": _date_str(g("date"), w, rn),
            "sellerTaxId": _txt(g("sellerTaxId")).upper(), "sellerName": _txt(g("sellerName")),
            "amount": _num(g("amount"), w, rn, "金额"), "tax": _num(g("tax"), w, rn, "税额"),
            "total": _num(g("total"), w, rn, "价税合计"), "deductTax": _num(g("deductTax"), w, rn, "有效抵扣税额"),
            "status": _txt(g("status")), "invType": _txt(g("invType")), "risk": _txt(g("risk")),
            "source": _txt(g("source")), "tick": "" if appended else _txt(row[tick] if tick < len(row) else None),
            "raw": _raw(headers, row), "rowNo": rn, "numberLost": lost,
        }
        try:
            res = decide(rd)
        except Exception as e:
            res = (None, "判断出错：%s" % str(e)[:60])
            w.add("decide", rn, "这几行判断时出错，已原样不动")
        if isinstance(res, (tuple, list)):
            dec, reason = (res[0] if res else None), (res[1] if len(res) > 1 else "")
        else:
            dec, reason = res, ""
        dec = _norm_decision(dec)
        summ["rows"] += 1
        if dec == "是":
            summ["yes"] += 1
        elif dec == "否":
            summ["no"] += 1
        else:
            summ["unknown"] += 1
        decisions.append((rn, dec))
        if len(out["preview"]) < 200:
            out["preview"].append({"rowNo": rn, "number": number, "decision": dec or "", "reason": reason or ""})

    col = tick + 1                       # openpyxl 列号从 1 起
    buf = io.BytesIO()
    try:
        if s["fmt"] == "xlsx":
            # 整本正常模式打开（不是只读、不取缓存值）：公式、样式、合并格、数据有效性都跟着原样存回去
            with _pywarnings.catch_warnings():
                _pywarnings.simplefilter("ignore")
                wb = load_workbook(io.BytesIO(data))
            ws = wb[s["sheet"]]
            if appended:
                _append_tick_header(ws, s["header_row"], col)
            for rn, dec in decisions:
                if dec is not None:
                    ws.cell(row=rn, column=col).value = dec
            wb.save(buf)
        else:
            # 老 .xls / csv：openpyxl 写不回原格式，只能照值另存一份 .xlsx
            w.note("原文件是%s，已另存为 .xlsx（原来的格式、颜色保不住）；导入税局前请确认税局收 .xlsx"
                   % ("老版 .xls" if s["fmt"] == "xls" else " CSV"))
            rd = _Reader(data, filename)
            try:
                wb = Workbook()
                ws = wb.active
                ws.title = _safe_title(s["sheet"])
                for i, row in enumerate(rd.rows(s["sheet"])):
                    for j, v in enumerate(row):
                        if not _blank(v):
                            c = ws.cell(row=i + 1, column=j + 1, value=_xl_val(v))
                            if isinstance(v, str) and v.startswith("="):
                                c.data_type = "s"
            finally:
                rd.close()
            if appended:
                ws.cell(row=s["header_row"], column=col).value = "是否勾选"
            for rn, dec in decisions:
                if dec is not None:
                    ws.cell(row=rn, column=col).value = dec
            wb.save(buf)
    except Exception as e:
        out["msg"] = "写回文件出错（%s: %s）" % (type(e).__name__, str(e)[:80])
        out["warnings"] += w.lines()
        return out

    out["bytes"] = buf.getvalue()
    out["warnings"] += w.lines()
    out["ok"] = True
    out["msg"] = "共 %d 张：标「是」%d 张、标「否」%d 张、没表态 %d 张（原样不动）" % (
        summ["rows"], summ["yes"], summ["no"], summ["unknown"])
    return out


def _append_tick_header(ws, header_row, col):
    """在表头行末尾加「是否勾选」，样式照抄左边一格表头。"""
    from copy import copy
    c = ws.cell(row=header_row, column=col)
    c.value = "是否勾选"
    if col > 1:
        left = ws.cell(row=header_row, column=col - 1)
        if left.has_style:
            c.font = copy(left.font)
            c.fill = copy(left.fill)
            c.border = copy(left.border)
            c.alignment = copy(left.alignment)


# ============================================================
# 七、导出（只写模式：几万行也不在内存里攒单元格对象）
# ============================================================

MONEY_FMT = "#,##0.00"

INV_TYPE_LABELS = {
    "special": "增值税专用发票", "normal": "增值税普通发票", "travel": "旅客运输服务", "toll": "通行费发票",
    "train": "铁路电子客票", "flight": "航空电子客票行程单", "vehicle": "机动车销售统一发票",
    "quota": "定额发票", "taxi": "出租车票", "tollpaper": "过路（过桥）费发票", "general": "通用机打发票", "other": "其他",
}
KIND_LABELS = {"invoice": "发票", "receipt": "收据", "other": "非发票附件"}
VERIFY_LABELS = {"green": "已验真", "red": "作废/红冲", "yellow": "税局清单里没有（待核）",
                 "gray": "清单覆盖不到", "": "未对账"}
DEDUCT_STATUS_LABELS = {"": "", "marked": "已标注勾选", "checked": "已勾选"}
ORIGIN_LABELS = {
    "attachment": "审批附件", "photo_field": "审批图片栏", "camera": "电脑摄像头", "phone": "手机",
    "upload": "上传", "taxpack": "税局文件包", "later": "后补收票", "scanner": "扫码枪",
}
INV_KIND_LABELS = {"special": "专票", "normal": "普票", "receipt": "收据"}
LATER_STATUS_LABELS = {"open": "未到票", "partial": "部分到票", "done": "已收齐", "closed": "已关闭"}
FILED_VIA_LABELS = {"proxy": "财务代填", "self": "本人填报"}

# 台账列：(表头, 取值函数, 类型 money/text/general, 列宽)
LEDGER_COLUMNS = [
    "审批编号", "单据类型", "申请人", "部门", "收款方/事由", "票种", "发票代码", "发票号码", "开票日期",
    "销方名称", "销方税号", "购方名称", "购方税号", "金额", "税额", "价税合计", "税率", "项目类别",
    "验真", "可否抵扣", "抵扣状态", "登记人", "登记时间", "审核人", "审核时间", "来源",
]


def _safe_title(t):
    t = re.sub(r"[\[\]:*?/\\]", "_", str(t or "Sheet"))[:31]
    return t or "Sheet"


def _xl_val(v):
    """写 Excel 前清洗：去掉 openpyxl 不收的控制字符。"""
    if isinstance(v, str):
        return _ILLEGAL_RE.sub("", v)
    return v


def _money(v):
    if v is None or v == "":
        return None
    x, _n = parse_number(v)
    return None if x is None else round(x, 2)


def _flat(v):
    """事由等 JSON 字段 → 一行文字。"""
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return "；".join(x for x in (_flat(i) for i in v) if x)
    if isinstance(v, dict):
        if "value" in v and ("name" in v or "label" in v):
            return "%s：%s" % (v.get("name") or v.get("label"), _flat(v.get("value")))
        return "；".join("%s：%s" % (k, _flat(x)) for k, x in v.items() if not _blank(x))
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


class _SheetWriter:
    """只写模式的一张表：表头加粗底色、冻结首行、自动筛选、金额千分位、号码按文本、末尾合计行。"""

    def __init__(self, wb, title, columns, money=(), text=(), widths=None):
        from openpyxl.cell import WriteOnlyCell
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
        self._C = WriteOnlyCell
        self.ws = wb.create_sheet(_safe_title(title))
        self.columns = list(columns)
        self.money = {self.columns.index(c) for c in money if c in self.columns}
        self.text = {self.columns.index(c) for c in text if c in self.columns}
        self.sums = {i: 0.0 for i in self.money}
        self.n = 0
        self._bold = Font(bold=True)
        self._fill = PatternFill("solid", fgColor="DDEBF7")
        self._center = Alignment(horizontal="center", vertical="center", wrap_text=True)
        widths = widths or {}
        for i, c in enumerate(self.columns):
            wdt = widths.get(c) or (14 if i in self.money else max(10, min(40, len(c) * 2 + 4)))
            self.ws.column_dimensions[get_column_letter(i + 1)].width = wdt
        self.ws.freeze_panes = "A2"
        self._letter = get_column_letter
        hdr = []
        for c in self.columns:
            cell = WriteOnlyCell(self.ws, value=c)
            cell.font, cell.fill, cell.alignment = self._bold, self._fill, self._center
            hdr.append(cell)
        self.ws.append(hdr)

    def _cell(self, i, v, bold=False):
        if i in self.money:
            v = _money(v)
            c = self._C(self.ws, value=v)
            c.number_format = MONEY_FMT
        else:
            if i in self.text and v is not None and not isinstance(v, str):
                v = _txt(v)
            v = _xl_val(v)
            c = self._C(self.ws, value=v)
            if isinstance(v, str) and v.startswith("="):
                c.data_type = "s"            # 防公式注入：用户填的 "=…" 一律当文字
            if i in self.text:
                c.number_format = "@"
        if bold:
            c.font = self._bold
        return c

    def add(self, values):
        cells = []
        for i, v in enumerate(values):
            c = self._cell(i, v)
            if i in self.money and c.value is not None:
                self.sums[i] += c.value
            cells.append(c)
        self.ws.append(cells)
        self.n += 1

    def finish(self, total_label="合计"):
        """末尾合计行（数值，不写公式——导出件常被别的程序只读取值，公式读出来是空）。"""
        if self.money:
            vals = [None] * len(self.columns)
            vals[0] = "%s（%d 行）" % (total_label, self.n)
            for i, x in self.sums.items():
                vals[i] = round(x, 2)
            self.ws.append([self._cell(i, v, bold=True) for i, v in enumerate(vals)])
        if self.n:
            self.ws.auto_filter.ref = "A1:%s%d" % (self._letter(len(self.columns)), self.n + 1)


def _new_wb():
    from openpyxl import Workbook
    return Workbook(write_only=True)


def _save(wb):
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _yesno_deductible(v):
    if v is True or v == 1:
        return "可抵扣"
    if v is False or v == 0:
        return "不可抵扣"
    return ""


def _payee_text(folder):
    p = folder.get("payee")
    if isinstance(p, dict):
        p = p.get("name")
    return _flat(p) or _flat(folder.get("title"))


def export_ledger(rows, title="发票台账"):
    """发票台账导出。rows＝接口里的 Item（camelCase）＋ row["folder"]={businessId,title,applicant,template,dept,payee}。
    → xlsx 字节。金额千分位两位小数、号码按文本、冻结表头、末尾合计行。"""
    wb = _new_wb()
    sw = _SheetWriter(wb, title, LEDGER_COLUMNS, money=("金额", "税额", "价税合计"),
                      text=("审批编号", "发票代码", "发票号码", "销方税号", "购方税号"),
                      widths={"审批编号": 22, "收款方/事由": 30, "发票号码": 24, "发票代码": 14, "开票日期": 12,
                              "销方名称": 32, "购方名称": 32, "销方税号": 22, "购方税号": 22, "项目类别": 20,
                              "登记时间": 19, "审核时间": 19, "票种": 16, "验真": 12})
    for r in rows or []:
        f = r.get("folder") or {}
        kind = r.get("kind") or "invoice"
        if kind != "invoice":
            typ = KIND_LABELS.get(kind, kind)
        else:
            typ = r.get("typeLabel") or INV_TYPE_LABELS.get(r.get("invType") or "", r.get("invType") or "")
        sw.add([
            f.get("businessId"), f.get("template"), f.get("applicant"), f.get("dept"), _payee_text(f),
            typ, r.get("code"), r.get("number"), r.get("date"),
            r.get("sellerName"), r.get("sellerTaxId"), r.get("buyerName"), r.get("buyerTaxId"),
            r.get("amount"), r.get("tax"), r.get("total"), r.get("taxRate"), r.get("category"),
            VERIFY_LABELS.get(r.get("verify") or "", r.get("verify")),
            _yesno_deductible(r.get("deductible")),
            DEDUCT_STATUS_LABELS.get(r.get("deductStatus") or "", r.get("deductStatus")),
            r.get("createdBy"), r.get("createdAt"), r.get("reviewBy"), r.get("reviewAt"),
            ORIGIN_LABELS.get(r.get("origin") or "", r.get("origin")),
        ])
    sw.finish()
    return _save(wb)


LATER_SUMMARY_COLUMNS = ["收款方（供应商）", "笔数", "付款金额合计", "已到票合计", "未到票合计", "最早预计到票日", "超期笔数"]
LATER_DETAIL_COLUMNS = [
    "后补单号", "票夹ID", "审批实例ID", "审批编号", "单据类型", "申请人", "部门", "公司主体",
    "收款方", "收款银行", "收款账号", "付款金额", "事由", "ERP单号", "发票类型", "税率",
    "预计到票日", "预计到票金额", "已到票金额", "已收未登记金额", "未到票金额", "状态", "是否超期", "剩余天数",
    "接收人", "登记人", "登记方式", "最近催票", "催票次数", "销方与收款方不一致", "备注", "创建时间", "更新时间",
]


def _later_arrived(r):
    """已到票＝已登记号码的 ＋ 已收到但号码没登记的（标黄那部分），两者都是票已到手。"""
    return (_money(r.get("receivedAmount")) or 0.0) + (_money(r.get("unregisteredAmount")) or 0.0)


def _later_remaining(r):
    """未到票：接口给了 remaining 就用；没给按 预计到票金额(没有就用付款金额) − 已到票 算，最低 0。"""
    rem = _money(r.get("remaining"))
    if rem is not None:
        return rem
    base = _money(r.get("expectAmount"))
    if base is None:
        base = _money(r.get("payAmount")) or 0.0
    return round(max(base - _later_arrived(r), 0.0), 2)


def export_later(rows):
    """欠票清单导出：「按供应商汇总」（按未到票金额从大到小）＋「明细」（后补单全部字段）。→ xlsx 字节。"""
    rows = list(rows or [])
    groups = {}
    for r in rows:
        p = r.get("payee")
        name = (p.get("name") if isinstance(p, dict) else p) or "（没填收款方）"
        g = groups.setdefault(name, {"n": 0, "pay": 0.0, "arr": 0.0, "rem": 0.0, "dates": [], "open_dates": [], "over": 0})
        g["n"] += 1
        g["pay"] += _money(r.get("payAmount")) or 0.0
        g["arr"] += _later_arrived(r)
        rem = _later_remaining(r)
        g["rem"] += rem
        d = _txt(r.get("expectDate"))
        if d:
            g["dates"].append(d)
            if rem > 0:
                g["open_dates"].append(d)
        if r.get("overdue"):
            g["over"] += 1

    wb = _new_wb()
    sw = _SheetWriter(wb, "按供应商汇总", LATER_SUMMARY_COLUMNS, money=("付款金额合计", "已到票合计", "未到票合计"),
                      widths={"收款方（供应商）": 36, "笔数": 8, "最早预计到票日": 16, "超期笔数": 10})
    tot_n = tot_over = 0
    for name, g in sorted(groups.items(), key=lambda kv: (-kv[1]["rem"], kv[0])):
        earliest = min(g["open_dates"] or g["dates"]) if (g["open_dates"] or g["dates"]) else ""
        sw.add([name, g["n"], g["pay"], g["arr"], g["rem"], earliest, g["over"]])
        tot_n += g["n"]
        tot_over += g["over"]
    # 合计行把笔数、超期笔数也带上（_SheetWriter 只合计金额列，这里手工补）
    if sw.money:
        vals = [None] * len(sw.columns)
        vals[0] = "合计（%d 家）" % len(groups)
        vals[1], vals[6] = tot_n, tot_over
        for i, x in sw.sums.items():
            vals[i] = round(x, 2)
        sw.ws.append([sw._cell(i, v, bold=True) for i, v in enumerate(vals)])
        if sw.n:
            sw.ws.auto_filter.ref = "A1:G%d" % (sw.n + 1)

    dw = _SheetWriter(wb, "明细", LATER_DETAIL_COLUMNS,
                      money=("付款金额", "预计到票金额", "已到票金额", "已收未登记金额", "未到票金额"),
                      text=("审批编号", "审批实例ID", "收款账号", "ERP单号"),
                      widths={"审批编号": 22, "审批实例ID": 24, "收款方": 32, "收款银行": 28, "收款账号": 24,
                              "事由": 36, "备注": 30, "创建时间": 19, "更新时间": 19, "最近催票": 19})
    for r in rows:
        p = r.get("payee") if isinstance(r.get("payee"), dict) else {"name": r.get("payee")}
        dw.add([
            r.get("id"), r.get("folderId"), r.get("instId"), r.get("businessId"), r.get("template"),
            r.get("applicant"), r.get("dept"), r.get("company"),
            p.get("name"), p.get("bank"), p.get("account"), r.get("payAmount"), _flat(r.get("reason")),
            r.get("erpNo"), INV_KIND_LABELS.get(r.get("invKind") or "", r.get("invKind")), r.get("taxRate"),
            r.get("expectDate"), r.get("expectAmount"), r.get("receivedAmount"), r.get("unregisteredAmount"),
            _later_remaining(r), LATER_STATUS_LABELS.get(r.get("status") or "", r.get("status")),
            "超期" if r.get("overdue") else "", r.get("daysLeft"),
            r.get("receiverName") or r.get("receiver"), r.get("filedBy"),
            FILED_VIA_LABELS.get(r.get("filedVia") or "", r.get("filedVia")),
            r.get("lastRemindAt"), r.get("remindCount"),
            _flat(r.get("sellerMismatch")) if r.get("sellerMismatch") else "",
            r.get("note"), r.get("createdAt"), r.get("updatedAt"),
        ])
    dw.finish()
    return _save(wb)


SELLER_COLUMNS = ["销方税号", "销方名称", "首次出现", "票数", "价税合计", "查询日期", "查询渠道", "查询结果", "备注", "查询人"]


def export_sellers(rows):
    """新销方核查清单导出（每月第一次出现的销方，专人去信用中国/省税务局公布栏查）。→ xlsx 字节。"""
    wb = _new_wb()
    sw = _SheetWriter(wb, "新销方核查", SELLER_COLUMNS, money=("价税合计",), text=("销方税号",),
                      widths={"销方税号": 22, "销方名称": 36, "首次出现": 12, "票数": 8, "查询渠道": 18,
                              "备注": 36, "查询日期": 12})
    for r in rows or []:
        sw.add([
            r.get("taxId"), r.get("name"), r.get("firstSeen"), r.get("items"), r.get("total"),
            r.get("checkDate"), r.get("checkChannel"), r.get("checkResult") or "未查", r.get("checkNote"),
            r.get("checkedBy"),
        ])
    sw.finish()
    return _save(wb)
