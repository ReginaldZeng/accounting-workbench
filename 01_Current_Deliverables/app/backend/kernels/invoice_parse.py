# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家）
# Description: 发票管家·票面解析内核（纯函数：不碰数据库、不联网、不 import core/db/FastAPI）。
#   原则「能读码的不认图，能读原件的不认图」：
#     ① 识别码分类（扫码枪嘀一声读到的是审批链接/审批编号/发票二维码）、发票二维码解析、查重键；
#     ② 电子原件直读：PDF 文字层（按位置解析，不按文字顺序）＋页面二维码；OFD（zip 里的
#        TextObject 带坐标）；数电 XML（按标签名尽量认）；压缩包逐层解开（GBK 文件名）；
#     ③ 照片两拨：第一拨只读二维码（号码/日期/金额当场出）；第二拨离线 OCR（rapidocr）按位置
#        解析，二维码读到的字段不被覆盖，OCR 独有的字段一律进 pending（待人核）；
#     ④ 预览图/缩略图、OFD/XML 的"虚拟图片"、可否抵扣建议、抬头/销方/金额校验。
#   重依赖（fitz/cv2/numpy/PIL/rapidocr）一律函数内懒加载：缺了只降级，不影响本模块被 import。
#   坐标约定：fieldSrc.box 为相对预览图（PDF=整页渲染；照片=按 EXIF 摆正后的图）的 0..1 坐标。
# 审查修复（同日）：防"小文件吃大内存"——图片解码前按文件头宽高把关（>4000 万像素：JPEG 缩档解、其他拒收）、
#   PDF 各渲染路径单页位图 ≤2500 万像素、OFD 条目 ≤10MB/整包 ≤50MB/压缩比 ≤100、压缩包总量按压缩比封顶＋条目数/
#   目录大小/提示条数封顶、XML 先按实际编码（含 UTF-16/32）解码再拒 DTD＋≤10MB/≤20 万元素；二维码金额与 OFD 坐标
#   拒收 NaN/无穷；新增 render_pdf_page（只渲染一页）。
import io
import os
import re
import codecs
import math
import time
import zipfile
import threading
import posixpath
import datetime
import unicodedata
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------------------
# 0. Doc 规范结构
# ---------------------------------------------------------------------------
DOC_KEYS = ("isInvoice", "kind", "invType", "typeLabel", "qrType", "code", "number", "date",
            "buyerName", "buyerTaxId", "sellerName", "sellerTaxId", "amount", "tax", "total",
            "taxRate", "category", "lines", "checkCode", "remark", "fieldSrc", "pending", "page",
            "approvalLink", "qrRaw", "warnings", "needOcr")

# OCR 独有时要进 pending 的字段（标量；明细 lines 不单列，审核时整体看图核）
PENDABLE = ("code", "number", "date", "buyerName", "buyerTaxId", "sellerName", "sellerTaxId",
            "amount", "tax", "total", "taxRate", "category", "checkCode", "remark")

FIELD_LABELS = {"code": "发票代码", "number": "发票号码", "date": "开票日期", "total": "价税合计",
                "amount": "金额", "tax": "税额", "checkCode": "校验码", "buyerName": "购买方名称",
                "buyerTaxId": "购买方税号", "sellerName": "销售方名称", "sellerTaxId": "销售方税号"}


def new_doc(page=0):
    """一张票（或一份非发票附件）的规范结构；所有键都在，不知道的留 None/[]/{}。"""
    return {"isInvoice": False, "kind": "other", "invType": None, "typeLabel": None, "qrType": None,
            "code": None, "number": None, "date": None, "buyerName": None, "buyerTaxId": None,
            "sellerName": None, "sellerTaxId": None, "amount": None, "tax": None, "total": None,
            "taxRate": None, "category": None, "lines": [], "checkCode": None, "remark": None,
            "fieldSrc": {}, "pending": [], "page": page, "approvalLink": None, "qrRaw": None,
            "warnings": [], "needOcr": False}


# ---------------------------------------------------------------------------
# 1. 文本小工具
# ---------------------------------------------------------------------------
_ZW = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)


def _nfkc(s):
    # 真实票面里有"电⼦发票"这类康熙部首/兼容区码位（U+2F26），先 NFKC 归一再比
    if s is None:
        return ""
    return unicodedata.normalize("NFKC", str(s)).translate(_ZW)


def _compact(s):
    return re.sub(r"\s+", "", _nfkc(s))


def _r2(v):
    return None if v is None else round(float(v) + 0.0, 2)


def _fix_num_text(s):
    # OCR 常见：¥ 认成 $、小数点两边多空格（"20. 00"）、百分号跑到前面（"%6"）
    t = _nfkc(s).replace("$", "¥").replace("￥", "¥")
    t = re.sub(r"(?<=\d)\s*\.\s*(?=\d)", ".", t)
    t = re.sub(r"^%(\d{1,2})$", r"\1%", t.strip())
    return t


_MONEY_TOKEN = re.compile(r"^-?¥?-?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?$|^-?¥?-?\d+(?:\.\d{1,2})?$")


def _parse_money(s, need_dec=True):
    """'¥1288.60' / '¥ 800.00' / '-¥12.00' / '2,345.67' → float；单价那种多位小数不认。"""
    t = _fix_num_text(s).replace(" ", "")
    t = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", t)
    m = re.search(r"(?<![\d.])(-?)¥?(-?)(\d+)(\.\d{1,2})?(?![\d.])", t)
    if not m:
        return None
    if need_dec and not m.group(4) and "¥" not in t:
        return None
    v = float(m.group(3) + (m.group(4) or ""))
    if m.group(1) or m.group(2):
        v = -v
    return round(v, 2)


def _is_money_tok(c):
    t = _fix_num_text(c).replace(" ", "")
    if not _MONEY_TOKEN.match(t):
        return False
    return "." in t or "¥" in t


def _parse_num(s):
    t = _fix_num_text(s).replace(" ", "").replace(",", "")
    if re.fullmatch(r"-?\d+(?:\.\d+)?", t):
        try:
            return float(t)
        except ValueError:
            return None
    return None


def _valid_date(y, m, d):
    try:
        return datetime.date(int(y), int(m), int(d)).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _parse_date(s):
    """'2026年09月01日' / '2026-09-01' / '20260901' / '2026-09-01 10:11:12' → '2026-09-01'。"""
    t = _compact(s)
    for pat in (r"(20\d{2})年(\d{1,2})月(\d{1,2})日?", r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})",
                r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)"):
        m = re.search(pat, t)
        if m:
            v = _valid_date(*m.groups())
            if v:
                return v
    return None


# 统一社会信用代码（GB 32100-2015）：18 位，不含 I/O/S/V/Z
_USCC_RE = re.compile(r"^[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}$")
_USCC_CH = "0123456789ABCDEFGHJKLMNPQRTUWXY"
_USCC_W = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)
_TAXID_ANY = re.compile(r"(?<![0-9A-Z])([0-9A-Z]{15,20})(?![0-9A-Z])")


def uscc_ok(s):
    """统一社会信用代码校验位是否正确（只对 18 位新代码有意义）。"""
    s = (s or "").upper()
    if not _USCC_RE.match(s):
        return False
    total = sum(_USCC_CH.index(c) * w for c, w in zip(s[:17], _USCC_W))
    return _USCC_CH[(31 - total % 31) % 31] == s[17]


def _fix_taxid(v, ocr=False):
    v = (v or "").upper()
    if ocr and len(v) == 18 and not uscc_ok(v):
        # OCR 把 0/1/2/5 认成 O/I/Z/S（新代码里根本没有这些字母）——换回来且校验位对上才用
        alt = v.translate(str.maketrans("OIZS", "0125"))
        if uscc_ok(alt):
            return alt
    return v


def _taxid_in(s, ocr=False):
    t = _compact(s).upper()
    for m in _TAXID_ANY.finditer(t):
        v = m.group(1)
        if not any(ch.isdigit() for ch in v):
            continue
        if len(v) == 20 and v.isdigit():
            continue  # 20 位纯数字多半是数电发票号码
        return _fix_taxid(v, ocr)
    return None


_CN_DIG = {"零": 0, "〇": 0, "壹": 1, "贰": 2, "叁": 3, "肆": 4, "伍": 5, "陆": 6, "柒": 7, "捌": 8, "玖": 9}
_CN_UNIT = {"拾": 10, "佰": 100, "仟": 1000}
_CN_BIG = {"万": 10000, "亿": 100000000}


def cn_upper_to_float(s):
    """价税合计（大写）→ 数字：'壹仟贰佰捌拾捌圆陆角整' → 1288.6；认不出返回 None。"""
    t = "".join(ch for ch in _compact(s).replace("元", "圆")
                if ch in _CN_DIG or ch in _CN_UNIT or ch in _CN_BIG or ch in "圆角分整正负")
    if not t or not any(ch in _CN_DIG or ch in _CN_UNIT or ch in _CN_BIG for ch in t):
        return None
    neg = t.startswith("负")
    t = t.lstrip("负")
    ip, fp = (t.split("圆", 1) + [""])[:2] if "圆" in t else (t, "")
    total = section = num = 0
    for ch in ip:
        if ch in _CN_DIG:
            num = _CN_DIG[ch]
        elif ch in _CN_UNIT:
            if ch == "拾" and num == 0:
                num = 1
            section += num * _CN_UNIT[ch]
            num = 0
        elif ch in _CN_BIG:
            total += (section + num) * _CN_BIG[ch]
            section = num = 0
    total += section + num
    jiao = fen = 0
    num = None
    for ch in fp:
        if ch in _CN_DIG:
            num = _CN_DIG[ch]
        elif ch == "角":
            jiao, num = (num or 0), None
        elif ch == "分":
            fen, num = (num or 0), None
    v = round(total + jiao / 10.0 + fen / 100.0, 2)
    return -v if neg else v


def _same_val(field, a, b):
    if a is None or b is None:
        return True
    if field in ("amount", "tax", "total"):
        try:
            return abs(float(a) - float(b)) < 0.005
        except (TypeError, ValueError):
            return False
    if field in ("number", "code", "checkCode"):
        return re.sub(r"\D", "", str(a)) == re.sub(r"\D", "", str(b))
    return str(a) == str(b)


# ---------------------------------------------------------------------------
# 2. 识别码分类 / 发票二维码 / 查重键 / 名称归一
# ---------------------------------------------------------------------------
def classify_code(text):
    """扫码枪读到的一串字 → {"kind": approval_link|business_id|invoice_qr|url|unknown, "value"}。
    扫码枪在中文输入法下会打出全角逗号/冒号，NFKC 一并归一。"""
    s = _nfkc(text).strip()
    if not s:
        return {"kind": "unknown", "value": ""}
    if re.search(r"aflow\.dingtalk\.com", s, re.I) or "procInstId=" in s or "procinstid=" in s.lower():
        return {"kind": "approval_link", "value": re.sub(r"\s+", "", s)}
    digits = re.sub(r"\s+", "", s)
    if re.fullmatch(r"\d{20,21}", digits):
        y = int(digits[:4])
        if 2015 <= y <= 2099 and _valid_date(digits[:4], digits[4:6], digits[6:8]):
            return {"kind": "business_id", "value": digits}
    if re.match(r"^0?1\s*,", s) and s.lstrip().startswith("01"):
        return {"kind": "invoice_qr", "value": s}
    if re.match(r"^(https?://|www\.)", s, re.I):
        return {"kind": "url", "value": re.sub(r"\s+", "", s)}
    return {"kind": "unknown", "value": s}


# 票种代码 → (invType, 票种名)。01–15 是老版增值税票；31/32/51/61 已见实物或官方口径；
# 81–88 据公开资料推断（数电纸质票/机动车/二手车），待拿实物校准。
QR_TYPES = {
    "01": ("special", "增值税专用发票"),
    "02": ("special", "货物运输业增值税专用发票"),
    "03": ("vehicle", "机动车销售统一发票"),
    "04": ("normal", "增值税普通发票"),
    "08": ("special", "增值税电子专用发票"),
    "10": ("normal", "增值税电子普通发票"),
    "11": ("normal", "增值税普通发票（卷式）"),
    "14": ("toll", "增值税电子普通发票（通行费）"),
    "15": ("vehicle", "二手车销售统一发票"),
    "31": ("special", "电子发票（增值税专用发票）"),
    "32": ("normal", "电子发票（普通发票）"),
    "51": ("train", "电子发票（铁路电子客票）"),
    "61": ("flight", "电子发票（航空运输电子客票行程单）"),
    "81": ("special", "电子发票（增值税专用发票）"),
    "82": ("normal", "电子发票（普通发票）"),
    "83": ("vehicle", "电子发票（机动车销售统一发票）"),
    "84": ("vehicle", "电子发票（二手车销售统一发票）"),
    "85": ("special", "纸质发票（增值税专用发票）"),
    "86": ("normal", "纸质发票（普通发票）"),
    "87": ("vehicle", "纸质发票（机动车销售统一发票）"),
    "88": ("vehicle", "纸质发票（二手车销售统一发票）"),
}


def parse_invoice_qr(text):
    """发票二维码 → dict；不是发票码返回 None。
    格式：01,票种,发票代码,发票号码,金额,开票日期,校验码,CRC
      · 数电（号码 20 位，代码为空）：金额＝价税合计 → total；
      · 老版增值税票（代码 10/12 位＋号码 8 位）：金额＝不含税金额 → amount。
    实物里见过：字段带空格（"01,32, ,…, ,XXXX"）、日期带横杠、CRC 为空。"""
    s = _nfkc(text).strip()
    if not s.startswith("01,"):
        return None
    parts = [re.sub(r"\s+", "", p) for p in s.split(",")]
    if len(parts) < 6:
        return None
    qt, code, number, amt, dt = parts[1], parts[2], parts[3], parts[4], parts[5]
    check = parts[6] if len(parts) > 6 else ""
    if not re.fullmatch(r"\d{2}", qt):
        return None
    if not re.fullmatch(r"\d{8}|\d{20}", number):
        return None
    if code and not re.fullmatch(r"\d{10}|\d{12}", code):
        return None
    # 只认普通小数：float() 会吃 nan/inf/1e17，落库后整个票夹打不开（JSON 不收 NaN、金额列放不下）
    if not re.fullmatch(r"-?\d{1,13}(?:\.\d{1,6})?", amt):
        return None
    v = round(float(amt), 2)
    date = _parse_date(dt)
    if not date:
        return None
    digital = len(number) == 20
    return {"qrType": qt, "code": code or None, "number": number, "date": date,
            "total": v if digital else None, "amount": None if digital else v,
            "checkCode": check or None, "raw": s}


def dup_key(code, number, date=None, total=None):
    """查重键：数电 20 位号码 → N:号码；老票 代码+号码 → C:代码:号码；
    只有号码没有代码（定额/出租车等 OCR 票）→ W:号码:日期:金额（弱键）；凑不齐返回 None。"""
    num = re.sub(r"\D", "", _nfkc(number or ""))
    cd = re.sub(r"\D", "", _nfkc(code or ""))
    if len(num) == 20:
        return "N:" + num
    if cd and num:
        return "C:" + cd + ":" + num
    if num and date and total is not None:
        try:
            return "W:" + num + ":" + str(date) + ":" + ("%.2f" % float(total))
        except (TypeError, ValueError):
            return None
    return None


_BRACKETS = str.maketrans({"【": "(", "】": ")", "[": "(", "]": ")", "〔": "(", "〕": ")",
                           "（": "(", "）": ")", "{": "(", "}": ")", "〖": "(", "〗": ")"})


def normalize_name(s):
    """比公司名用：NFKC、去所有空白、全半角括号/方头括号统一成 ()、去尾部标点、英文大写。"""
    t = re.sub(r"\s+", "", _nfkc(s)).translate(_BRACKETS)
    t = t.rstrip(".,;:!?、。，；：！？·-_~")
    return t.upper()


# ---------------------------------------------------------------------------
# 3. 文件类型嗅探 / 压缩包
# ---------------------------------------------------------------------------
_EXT_TYPES = {"pdf": "pdf", "ofd": "ofd", "xml": "xml", "zip": "zip", "xlsx": "excel", "xls": "excel",
              "xlsm": "excel", "et": "excel", "jpg": "image", "jpeg": "image", "png": "image",
              "bmp": "image", "webp": "image", "gif": "image", "tif": "image", "tiff": "image",
              "heic": "image", "heif": "image"}
_HEIF_BRANDS = (b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1", b"avif")


def _is_heif(data):
    return len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in _HEIF_BRANDS


# ---- 防"小文件吃大内存/大磁盘"（压缩炸弹、伪造成几十万个条目的包）：整站只有一个 uvicorn 进程，
#      一次 OOM 所有财务工具一起挂；钉钉附件是后台线程自动拉的，坏文件还会让它反复重来。
_MB = 1024 * 1024
_ZIP_MAX_CD = 4 * _MB          # zip 中央目录上限（≈几万个条目）；伪造几十万个条目的包光列目录就要几百 MB
_ZIP_MAX_RATIO = 100           # 单个条目/整包：解开后最多是压缩前的 100 倍（正常发票 PDF/OFD/图片约 1～10 倍）
_ZIP_RATIO_MIN = 1 * _MB       # 解开后不到 1MB 的小条目不看压缩比（小文本压得狠也无害）
_ZIP_TOTAL_FLOOR = 25 * _MB    # 整包解开总量至少允许 25MB（小包按压缩比算出来的额度太小时兜底）
_ZIP_MAX_WARN = 30             # 提示最多列 30 条，其余合成一条（几十万个坏条目别刷出几十万条提示）


class _ZipTooBig(ValueError):
    """压缩包目录大得离谱（条目太多），不打开。"""


def _open_zip(data):
    """打开 zip 前先看中央目录多大，超限抛 _ZipTooBig（中文）；不是 zip 照常抛 zipfile 的异常。"""
    bio = io.BytesIO(data)
    try:
        rec = zipfile._EndRecData(bio)   # 只读包尾的目录记录（含 zip64），不建条目对象
        cd = int(rec[getattr(zipfile, "_ECD_SIZE", 5)]) if rec else 0
    except Exception:
        cd = 0
    if cd > _ZIP_MAX_CD:
        raise _ZipTooBig("压缩包里的文件多得不正常（目录超过 %dMB），没有展开" % (_ZIP_MAX_CD // _MB))
    return zipfile.ZipFile(bio)


def sniff_type(name, data):
    """先看文件头魔数，再看扩展名。OFD 本质是 zip，里面有 OFD.xml。"""
    head = bytes(data[:2048]) if data else b""
    ext = os.path.splitext(name or "")[1].lower().lstrip(".")
    if b"%PDF-" in head[:1024]:
        return "pdf"
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06"):
        try:
            with _open_zip(data) as z:
                names = [n.replace("\\", "/").strip("/").lower() for n in z.namelist()]
        except _ZipTooBig:
            return "zip"          # 交给解压那一步报"文件太多"，别按扩展名当成 PDF/图片去解析
        except Exception:
            names = None
        if names is not None:
            if "ofd.xml" in names:
                return "ofd"
            if "xl/workbook.xml" in names:
                return "excel"
            if "word/document.xml" in names or "ppt/presentation.xml" in names:
                return "other"
            return "zip"
    if head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "excel" if ext in ("xls", "et", "xlsx") else "other"
    if (head[:3] == b"\xff\xd8\xff" or head[:8] == b"\x89PNG\r\n\x1a\n" or head[:6] in (b"GIF87a", b"GIF89a")
            or (head[:4] == b"RIFF" and head[8:12] == b"WEBP") or head[:4] in (b"II*\x00", b"MM\x00*")
            or _is_heif(head)):
        return "image"
    if head[:2] == b"BM" and len(head) > 18 and int.from_bytes(head[14:18], "little") in (12, 40, 52, 56, 108, 124):
        return "image"
    body = head.lstrip(b"\xef\xbb\xbf").lstrip()
    if body[:5].lower() == b"<?xml" or (body[:1] == b"<" and ext == "xml"):
        return "xml"
    return _EXT_TYPES.get(ext, "other")


class ZipFiles(list):
    """unpack_zip 的返回值：本身就是 [(name, bytes), ...]；另带 .warnings（中文提示列表）。"""

    def __init__(self, items=(), warnings=None):
        list.__init__(self, items)
        self.warnings = list(warnings or [])


def _zip_name(info):
    # 标志位 bit11 没置位时 Python 按 cp437 解的名字；国内压缩软件多半是 GBK 字节 → 还原重解
    n = info.filename
    if info.flag_bits & 0x800:
        return n
    try:
        raw = n.encode("cp437")
    except UnicodeEncodeError:
        return n
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return n


def _mb_text(n):
    return ("%d" % (n // _MB)) if n >= _MB else ("%.2f" % (n / float(_MB)))


def unpack_zip_ex(data, max_depth=2, max_total=200 * 1024 * 1024, max_file=25 * 1024 * 1024, max_count=500):
    """解开压缩包（含套娃）→ (files, warnings)。files=[(相对路径名, bytes)]，套娃里的文件名
    带上外层包名前缀（"外层.zip/里面/发票.pdf"），分隔符统一 "/"。跳过 __MACOSX、.DS_Store、目录；
    超 25MB 的单个文件、超 max_depth 层的套娃、加密文件都跳过并记一条中文提示。
    防压缩炸弹：解开总量封顶 min(max_total, max(25MB, 压缩包大小×100))（套娃各层合计、先按条目自报的
    解压大小预判）；单个条目解开后超过 1MB 且超过压缩前 100 倍的跳过；目录大得离谱的包不打开；
    连同跳过的条目最多看 max_count×4 个，提示最多列 30 条。"""
    files, warnings = [], []
    limit = min(int(max_total), max(_ZIP_TOTAL_FLOOR, len(data or b"") * _ZIP_MAX_RATIO))
    state = {"total": 0, "stop": False, "seen": 0, "hidden": 0}

    def warn(msg):
        if len(warnings) < _ZIP_MAX_WARN:
            warnings.append(msg)
        else:
            state["hidden"] += 1

    def walk(blob, prefix, depth):
        try:
            zf = _open_zip(blob)
        except _ZipTooBig as e:
            warn(("%s：%s" % (e, prefix.rstrip("/"))) if prefix else str(e))
            return
        except Exception:
            warn(("压缩包损坏，打不开：" + prefix.rstrip("/")) if prefix else "压缩包损坏，打不开")
            return
        with zf:
            for info in zf.infolist():
                if state["stop"]:
                    return
                name = _zip_name(info).replace("\\", "/")
                parts = [p for p in name.split("/") if p]
                if not parts or name.endswith("/") or info.is_dir():
                    continue
                state["seen"] += 1
                if state["seen"] > max_count * 4:
                    warn("压缩包里文件超过 %d 个，后面的文件没有处理" % max_count)
                    state["stop"] = True
                    return
                base = parts[-1]
                if "__MACOSX" in parts or base in (".DS_Store", "Thumbs.db", "desktop.ini") or base.startswith("._"):
                    continue
                full = prefix + "/".join(parts)
                if info.flag_bits & 0x1:
                    warn("文件加了密码，跳过：" + full)
                    continue
                if info.file_size > max_file:
                    warn("单个文件超过 %dMB，跳过：%s" % (max_file // _MB, full))
                    continue
                # 自报的压缩大小不可能比整个包还大（防伪造出一个很大的压缩大小来骗过比例检查）
                packed = max(min(int(info.compress_size), len(blob)), 1)
                if info.file_size > _ZIP_RATIO_MIN and info.file_size > _ZIP_MAX_RATIO * packed:
                    warn("文件压缩得异常小（解开后是压缩前的 %d 倍以上，疑似恶意压缩包），跳过：%s" % (_ZIP_MAX_RATIO, full))
                    continue
                if state["total"] + info.file_size > limit:
                    warn("压缩包解开后超过 %sMB，后面的文件没有处理" % _mb_text(limit))
                    state["stop"] = True
                    return
                if len(files) >= max_count:
                    warn("压缩包里文件超过 %d 个，后面的文件没有处理" % max_count)
                    state["stop"] = True
                    return
                try:
                    with zf.open(info) as fp:
                        b = fp.read(max_file + 1)   # zipfile 不会吐出超过自报大小的内容，上面按自报大小预判是可靠的
                except Exception:
                    warn("解压失败，跳过：" + full)
                    continue
                if len(b) > max_file:
                    warn("单个文件超过 %dMB，跳过：%s" % (max_file // _MB, full))
                    continue
                state["total"] += len(b)
                if sniff_type(base, b) == "zip":
                    if depth < max_depth:
                        walk(b, full + "/", depth + 1)
                    else:
                        warn("压缩包套了超过 %d 层，里层没有展开：%s" % (max_depth, full))
                    continue
                files.append((full, b))

    walk(data, "", 1)
    if state["hidden"]:
        warnings.append("……另有 %d 条同类提示没有列出" % state["hidden"])
    return files, warnings


def unpack_zip(data, max_depth=2, max_total=200 * 1024 * 1024, max_file=25 * 1024 * 1024):
    """同 unpack_zip_ex，但返回 ZipFiles（list 子类）：遍历得 (name, bytes)，提示在 .warnings。"""
    files, warns = unpack_zip_ex(data, max_depth=max_depth, max_total=max_total, max_file=max_file)
    return ZipFiles(files, warns)


# ---------------------------------------------------------------------------
# 4. 图片：预览/缩略、按 EXIF 摆正
# ---------------------------------------------------------------------------
_BAD_IMG = "不支持的图片格式（请用 JPG/PNG）"
_IMG_FORMATS = ("JPEG", "PNG", "BMP", "WEBP", "GIF", "TIFF", "MPO")
# 解码前按文件头里的宽高把关（不改 Pillow 的全局上限：同进程还有别的工具在用 Pillow）。
# 4000 万像素≈A4 600dpi 扫描件；更大的 JPEG 按 DCT 缩档解码（1/2～1/8，手机 48MP/200MP 照片照收），
# 其他格式（PNG/TIFF/BMP…）只能整张解码，超了就拒收。
_IMG_MAX_PIXELS = 40000000
_IMG_DRAFT_SIDE = 4000
_BIG_IMG = "图片像素太大（超过4000万像素），请缩小或改存成 JPG 后再传"


def _pil():
    try:
        from PIL import Image, ImageOps
    except Exception:
        raise ValueError("服务器缺少图像组件（Pillow），处理不了图片")
    return Image, ImageOps


def _resample(Image, name):
    r = getattr(Image, "Resampling", Image)
    return getattr(r, name)


def _to_rgb(im):
    Image, _ = _pil()
    if im.mode in ("RGBA", "LA", "P", "PA"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        return bg
    if im.mode != "RGB":
        return im.convert("RGB")
    return im


def _open_image(data, draft_side=None):
    """打开图片并按 EXIF 摆正；HEIC 等不支持的格式、像素大得离谱的图抛 ValueError（中文）。
    Image.open 只读文件头：宽高在真正解码之前就检查，超限的不解码（小文件伪造成上亿像素会把进程撑爆）。"""
    Image, ImageOps = _pil()
    if not data or _is_heif(bytes(data[:16])):
        raise ValueError(_BAD_IMG)
    bomb = getattr(Image, "DecompressionBombError", None)
    try:
        try:
            im = Image.open(io.BytesIO(data), formats=_IMG_FORMATS)   # 只试这几种格式的解析器
        except TypeError:                                              # Pillow < 7.2 没有 formats 参数
            im = Image.open(io.BytesIO(data))
        fmt = im.format
    except Exception as e:
        if bomb is not None and isinstance(e, bomb):
            raise ValueError(_BIG_IMG)
        raise ValueError(_BAD_IMG)
    if fmt not in _IMG_FORMATS:
        raise ValueError(_BAD_IMG)
    w, h = im.size
    if fmt in ("JPEG", "MPO"):
        f = 0
        if draft_side:
            f = max(w, h) / float(draft_side)
            if f < 2:
                f = 0
        if w * h > _IMG_MAX_PIXELS:
            if im.info.get("progressive") or im.info.get("progression"):
                # 渐进式 JPEG 缩档解码也要按原尺寸开系数缓冲区，大图照样吃内存
                raise ValueError(_BIG_IMG)
            f = max(f, 2.0, max(w, h) / float(_IMG_DRAFT_SIDE))
        if f:
            try:  # JPEG 按 DCT 比例解码，12MP 照片解码快几倍（draft 只会给 ≥ 请求尺寸的档位）
                im.draft("RGB", (max(1, int(w / f)), max(1, int(h / f))))
            except Exception:
                pass
            w, h = im.size
    if w * h > _IMG_MAX_PIXELS:
        raise ValueError(_BIG_IMG)
    try:
        im = ImageOps.exif_transpose(im)
    except Exception:
        try:
            im.load()
        except Exception:
            raise ValueError("图片损坏，打不开")
    return im, fmt


def _shrink(im, long_side):
    Image, _ = _pil()
    w, h = im.size
    if max(w, h) <= long_side:
        return im
    f = long_side / float(max(w, h))
    size = (max(1, int(round(w * f))), max(1, int(round(h * f))))
    try:
        return im.resize(size, _resample(Image, "LANCZOS"), reducing_gap=3.0)
    except TypeError:
        return im.resize(size, _resample(Image, "LANCZOS"))


def _jpeg(im, long_side, quality):
    im = _shrink(_to_rgb(im), long_side)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return buf.getvalue(), im.size


def make_image_variants(data):
    """原图/预览/缩略三份：按 EXIF 摆正；原图 ≤2MB 且是 JPG/PNG 原样保留，否则转 JPEG（长边 2600，q88）；
    预览 JPEG 长边 1600 q82；缩略 JPEG 长边 320 q75；w/h 为预览图尺寸。HEIC 等 → ValueError。
    大 JPEG 按长边 ≥2600 的档位缩档解码（最大只用到 2600，没必要整张解开，省几百 MB 内存）。"""
    im, fmt = _open_image(data, draft_side=2600)
    if len(data) <= 2 * 1024 * 1024 and fmt in ("JPEG", "PNG", "MPO"):
        orig, orig_ext = bytes(data), ("png" if fmt == "PNG" else "jpg")
    else:
        orig, _ = _jpeg(im, 2600, 88)
        orig_ext = "jpg"
    preview, (w, h) = _jpeg(im, 1600, 82)
    thumb, _ = _jpeg(im, 320, 75)
    return {"orig": orig, "orig_ext": orig_ext, "preview": preview, "thumb": thumb, "w": w, "h": h}


def thumb_from_jpeg(jpeg_bytes):
    """PDF 渲染出的预览图 → 320px 缩略图（JPEG q75）。"""
    im, _ = _open_image(jpeg_bytes)
    return _jpeg(im, 320, 75)[0]


def _load_gray(data, max_side=2000):
    """→ (numpy 灰度图, 宽, 高)；已按 EXIF 摆正并缩到长边 ≤ max_side。"""
    cvn = _cv2()
    if not cvn:
        raise ValueError("服务器缺少图像组件（opencv/numpy）")
    _, np = cvn
    im, _ = _open_image(data, draft_side=max_side)
    im = _shrink(_to_rgb(im), max_side).convert("L")
    arr = np.asarray(im, dtype=np.uint8)
    return arr, arr.shape[1], arr.shape[0]


# ---------------------------------------------------------------------------
# 5. 二维码
# ---------------------------------------------------------------------------
_CV = None
_TLS = threading.local()


def _cv2():
    """(cv2, numpy) 或 None（缺库时二维码功能整体降级为"读不到"）。"""
    global _CV
    if _CV is None:
        try:
            import cv2
            import numpy as np
            try:  # 发票码里的 ECI 段会让 OpenCV 每张刷一行 WARN，日志太吵
                cv2.setLogLevel(2)
            except Exception:
                try:
                    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
                except Exception:
                    pass
            _CV = (cv2, np)
        except Exception:
            _CV = False
    return _CV or None


def _detectors():
    # 检测器对象不保证线程安全：每线程一套
    ds = getattr(_TLS, "dets", None)
    if ds is None:
        cv2, _ = _cv2()
        ds = []
        if hasattr(cv2, "QRCodeDetectorAruco"):
            try:
                ds.append(cv2.QRCodeDetectorAruco())  # 实测对票面小码更稳（经典检测器 150dpi 全军覆没）
            except Exception:
                pass
        ds.append(cv2.QRCodeDetector())
        _TLS.dets = ds
    return ds


def _qr_try(gray, want_cands=False):
    """一次检测：→ ([(text, pts4x2)], [未解出的候选 pts])。"""
    cv2, np = _cv2()
    found, cands = [], []
    for det in _detectors():
        try:
            if hasattr(det, "detectAndDecodeMulti"):
                ok, texts, pts, _ = det.detectAndDecodeMulti(gray)
                texts = list(texts or [])
            else:
                t, pts, _ = det.detectAndDecode(gray)
                texts = [t]
                pts = None if pts is None else pts.reshape(1, 4, 2)
        except Exception:
            continue
        if pts is None:
            continue
        pts = np.asarray(pts, dtype=np.float32).reshape(-1, 4, 2)
        for i in range(pts.shape[0]):
            t = texts[i] if i < len(texts) else ""
            if t:
                found.append((t, pts[i]))
            elif want_cands:
                cands.append(pts[i])
        if found:
            break
    return found, cands


def _pts_box(p):
    return [float(p[:, 0].min()), float(p[:, 1].min()), float(p[:, 0].max()), float(p[:, 1].max())]


def _qr_scan(gray, budget=0.65, stop_on_invoice=True):
    """多尺度＋候选区放大＋二值化＋三个旋转方向，找出图里所有二维码。
    → [(text, box[x0,y0,x1,y1] 按 gray 像素, pts)]。总耗时尽量控制在 budget 秒内。"""
    cv2, np = _cv2()
    t0 = time.time()
    H, W = gray.shape[:2]
    L = float(max(H, W))
    res, seen, cands = [], set(), []

    def add(hits, back):
        for t, p in hits:
            if t in seen:
                continue
            seen.add(t)
            q = back(p)
            res.append((t, _pts_box(q), q))

    def key_found():
        return any(r[0].startswith("01,") or "aflow.dingtalk.com" in r[0] for r in res)

    def done():
        # stop_on_invoice：读到发票码或钉钉审批码就收手；
        # 否则（要全部码，比如 A4 上拼了两张票）各个尺度都在时限内过一遍
        return bool(res) and stop_on_invoice and key_found()

    def covered(box):
        cx, cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
        return any(r[1][0] <= cx <= r[1][2] and r[1][1] <= cy <= r[1][3] for r in res)

    if L >= 1400:
        scales = [1600.0 / L if L > 1700 else 1.0, 1.0, 0.5]
    elif L >= 700:
        scales = [1.0, 1.5, 0.6]
    else:
        scales = [2.0, 1.0, 1.5]
    tried = set()
    for s in scales:
        s = round(s, 3)
        if s in tried or L * s > 2600:
            continue
        tried.add(s)
        img = gray if s == 1.0 else cv2.resize(gray, None, fx=s, fy=s,
                                                  interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
        hits, cc = _qr_try(img, want_cands=True)
        add(hits, lambda p, s=s: p / s)
        cands.extend([c / s for c in cc])
        if done() or time.time() - t0 > budget:
            break
    # 检测到了但没解出：把那块抠出来放大再解（已解出的、重复的候选跳过）
    tried_c = []
    for c in cands:
        if time.time() - t0 > budget * 1.3 or len(tried_c) >= 6:
            break
        x0, y0, x1, y1 = _pts_box(c)
        if covered([x0, y0, x1, y1]) or any(abs(x0 - a) < 0.3 * (x1 - x0) and abs(y0 - b) < 0.3 * (y1 - y0) for a, b in tried_c):
            continue
        tried_c.append((x0, y0))
        m = 0.25 * max(x1 - x0, y1 - y0) + 8
        X0, Y0 = int(max(0, x0 - m)), int(max(0, y0 - m))
        X1, Y1 = int(min(W, x1 + m)), int(min(H, y1 + m))
        if X1 - X0 < 8 or Y1 - Y0 < 8:
            continue
        crop = gray[Y0:Y1, X0:X1]
        f = 420.0 / max(crop.shape)
        crop = cv2.resize(crop, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
        crop = cv2.copyMakeBorder(crop, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)
        hits, _ = _qr_try(crop)
        add(hits, lambda p, f=f, X0=X0, Y0=Y0: (p - 40) / f + np.array([X0, Y0], dtype=np.float32))
        if done():
            return res
    if res and (not stop_on_invoice or key_found()):
        return res
    s0 = min(1.0, 1600.0 / L) if L > 1700 else (1.0 if L >= 700 else 2.0)
    base = gray if s0 == 1.0 else cv2.resize(gray, None, fx=s0, fy=s0,
                                              interpolation=cv2.INTER_AREA if s0 < 1 else cv2.INTER_CUBIC)
    # 二值化（反光/阴影照片）
    if time.time() - t0 < budget:
        try:
            bw = cv2.adaptiveThreshold(base, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10)
            hits, _ = _qr_try(bw)
            add(hits, lambda p: p / s0)
            if done():
                return res
        except Exception:
            pass
    # 三个旋转方向（极少数情况下检测器对方向敏感）
    bh, bw_ = base.shape[:2]
    for k in (1, 2, 3):
        if time.time() - t0 > budget:
            break
        rimg = np.ascontiguousarray(np.rot90(base, k))
        hits, _ = _qr_try(rimg)

        def back(p, k=k):
            q = p.copy()
            x, y = q[:, 0].copy(), q[:, 1].copy()
            if k == 1:      # rot90 逆时针：原(x,y) → (y, W-1-x)
                q[:, 0], q[:, 1] = bw_ - 1 - y, x
            elif k == 2:
                q[:, 0], q[:, 1] = bw_ - 1 - x, bh - 1 - y
            else:
                q[:, 0], q[:, 1] = y, bh - 1 - x
            return q / s0
        add(hits, back)
        if done():
            return res
    return res


def decode_qr_image(data):
    """图片字节 → 图里所有二维码内容（去重，按找到顺序）。缺 cv2 或图片打不开返回 []。"""
    if not _cv2():
        return []
    try:
        gray, _, _ = _load_gray(data, 2000)
    except Exception:
        return []
    return [t for t, _, _ in _qr_scan(gray, stop_on_invoice=False)]


def _qr_layout(data):
    """→ (rot, [(text, 归一化 box)])：rot=要顺时针转几度才摆正；box 按（EXIF 摆正后的）原图。"""
    if not _cv2():
        return 0, []
    try:
        gray, W, H = _load_gray(data, 1600)
    except Exception:
        return 0, []
    hits = _qr_scan(gray, budget=0.4)
    rot = 0
    for t, _, p in hits:
        if parse_invoice_qr(t) or len(hits) == 1:
            dx, dy = float(p[1][0] - p[0][0]), float(p[1][1] - p[0][1])
            k = int(round(math.degrees(math.atan2(dy, dx)) / 90.0)) % 4   # 需逆时针转 k*90
            rot = (-90 * k) % 360
            break
    return rot, _reading_order([(t, _nb(bx, W, H)) for t, bx, _ in hits])


def guess_rotation(data):
    """按照片里发票二维码的朝向，估计要顺时针转多少度才摆正（0/90/180/270；找不到码返回 0）。
    二维码四个角点按码自身的"左上→右上→右下→左下"给出，所以上边的方向就是票面的方向。"""
    return _qr_layout(data)[0]


# ---------------------------------------------------------------------------
# 6. 按位置解析票面（PDF 文字层 / OCR 框 / OFD TextObject 共用）
# ---------------------------------------------------------------------------
class _T(object):
    __slots__ = ("t", "c", "x0", "y0", "x1", "y1")

    def __init__(self, t, x0, y0, x1, y1):
        self.t = t
        self.c = re.sub(r"\s+", "", t)
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1

    @property
    def h(self):
        return max(self.y1 - self.y0, 1e-6)

    @property
    def w(self):
        return max(self.x1 - self.x0, 1e-6)

    @property
    def cx(self):
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self):
        return (self.y0 + self.y1) / 2.0

    @property
    def box(self):
        return [self.x0, self.y0, self.x1, self.y1]


def _union(boxes):
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _yov(a, b):
    ov = min(a.y1, b.y1) - max(a.y0, b.y0)
    return ov / max(min(a.h, b.h), 1e-6)


def _right_of(lab, toks, x_max=None):
    tol = max(0.6 * lab.h, 2.0)
    out = [t for t in toks if t is not lab and (t.x0 >= lab.x1 - tol or t.cx > lab.x1 + tol)
           and _yov(lab, t) >= 0.4 and (x_max is None or t.x0 < x_max)]
    out.sort(key=lambda t: t.x0)
    return out


def _text_w(s):
    # 估字宽：汉字/全角≈1 个字号，数字字母≈0.55（按比例切"标签+值"合在一块的框）
    return sum(1.0 if ord(ch) > 0x2E80 else 0.55 for ch in s)


def _sub_box(tok, start_frac):
    return [tok.x0 + (tok.x1 - tok.x0) * start_frac, tok.y0, tok.x1, tok.y1]


def _label_value(lab, pat, toks, x_max=None, accept=None, join=False):
    """标签取值：同一个 token 里标签后面有字就用它（OCR 常把"名称：xxx"认成一块）；
    否则取同一行右侧最近的 token（数电 PDF 标签和值是分开的文字对象）。→ (text, box)"""
    m = re.search(pat, lab.c)
    rest = lab.c[m.end():] if m else ""
    rest = rest.lstrip(":;：)）").strip()
    if rest and (accept is None or accept(rest)):
        whole = _text_w(lab.c)
        return rest, _sub_box(lab, (whole - _text_w(rest)) / max(whole, 1e-6))
    right = _right_of(lab, toks, x_max)
    for i, t in enumerate(right):
        if accept is not None and not accept(t.c):
            if _looks_label(t.c):
                break
            continue
        if not join:
            return t.t.strip(), t.box
        parts, boxes, last = [t.t.strip()], [t.box], t
        for u in right[i + 1:]:
            if u.x0 - last.x1 > 1.5 * last.h or _looks_label(u.c):
                break
            parts.append(u.t.strip())
            boxes.append(u.box)
            last = u
        return "".join(parts), _union(boxes)
    return None, None


_LABEL_WORDS = ("名称", "纳税人识别号", "信用代码", "识别号", "地址", "电话", "开户行", "账号", "发票号码",
                "开票日期", "发票代码", "校验码", "密码区", "备注", "开票人", "收款人", "复核", "价税合计", "合计")


def _looks_label(c):
    return any(w in c for w in _LABEL_WORDS) and (":" in c or len(c) <= 8)


def _vlabel(toks, words):
    """找竖排的"购买方""销售方"：可能一个 token 就是整词（OCR），也可能是一个字一个 token 竖着叠。"""
    for word in words:
        for t in toks:
            if word in t.c and len(t.c) <= len(word) + 3 and "开户" not in t.c:
                return t
        for f in [t for t in toks if t.c == word[0]]:
            cur, boxes, ok = f, [f.box], True
            for ch in word[1:]:
                nxt = [t for t in toks if t.c == ch and t.y0 >= cur.y0 + 0.3 * cur.h
                       and t.y0 - cur.y1 < 1.6 * cur.h and abs(t.cx - cur.cx) < max(cur.w, cur.h)]
                if not nxt:
                    ok = False
                    break
                cur = min(nxt, key=lambda t: t.y0)
                boxes.append(cur.box)
            if ok:
                b = _union(boxes)
                return _T(word, b[0], b[1], b[2], b[3])
    return None


# 先找五个字的"购买方信息"（竖排标签的全高＝整个区块的高度），找不到再退到三个字
_BUY_WORDS = ("购买方信息", "购买方", "购货单位")
_SELL_WORDS = ("销售方信息", "销售方", "销货单位")
_COMPANY_HINT = re.compile(r"公司|有限|集团|中心|银行|医院|学校|大学|研究院|事务所|合作社|商行|经营部|店|厂|局|个人|超市|酒店")


def _party(region, toks, ocr):
    """一个区块（购买方或销售方）里取名称和税号。→ (name, name_box, taxid, taxid_box)"""
    x0, y0, x1, y1 = region
    rt = [t for t in toks if x0 <= t.cx <= x1 and y0 <= t.cy <= y1]
    name = nbox = tid = tbox = None
    for lab in sorted([t for t in rt if re.match(r"^名称", t.c)], key=lambda t: (t.y0, t.x0)):
        v, b = _label_value(lab, r"^名称", rt, x_max=x1, join=True,
                            accept=lambda s: bool(re.search(r"[一-鿿A-Za-z]", s)) and not _looks_label(s))
        if v:
            name, nbox = v, b
            break
    for lab in [t for t in rt if re.search(r"纳税人识别号|信用代码|识别号|税号", t.c)]:
        v, b = _label_value(lab, r"(纳税人识别号|信用代码|识别号|税号)[^:]*:?", rt, x_max=x1,
                            accept=lambda s: _taxid_in(s, ocr) is not None)
        if v:
            tid, tbox = _taxid_in(v, ocr), b
            break
    if not tid:
        for t in sorted(rt, key=lambda t: t.y0):
            if re.search(r"[一-鿿]", t.c) and not re.search(r"识别号|信用代码|税号", t.c):
                continue
            v = _taxid_in(t.c, ocr)
            if v and len(re.sub(r"[^0-9A-Z]", "", t.c.upper())) <= 22:
                tid, tbox = v, t.box
                break
    if not name:
        cands = [t for t in rt if re.search(r"[一-鿿]{2,}", t.c) and not _looks_label(t.c)
                 and not re.search(r"识别号|信用代码|地址|电话|开户|账号|信息$", t.c) and len(t.c) >= 2
                 and t.c not in ("购", "买", "方", "信", "息", "销", "售")]
        hint = [t for t in cands if _COMPANY_HINT.search(t.c)]
        pick = sorted(hint or cands, key=lambda t: (t.y0, t.x0))
        if pick:
            name, nbox = pick[0].t.strip(), pick[0].box
    if name:
        name = re.sub(r"^名称[:：]?", "", name).strip(" :：")
    return name or None, nbox, tid, tbox


def _party_regions(toks, W, H, table_top):
    """定位购买方/销售方区块：先找竖排标签（数电左右并排；老版上下排且右边是密码区），
    找不到再按两个"名称："标签，最后退到页面中线。"""
    B, S = _vlabel(toks, _BUY_WORDS), _vlabel(toks, _SELL_WORDS)
    if B and S:
        pad = max(B.w, S.w, 4.0)
        if S.x0 > B.x1 and min(B.y1, S.y1) - max(B.y0, S.y0) > 0:
            y0, y1 = min(B.y0, S.y0) - pad, max(B.y1, S.y1) + pad
            if table_top and y1 < table_top < y1 + 4 * pad:
                y1 = table_top - 0.5
            return (B.x0 - pad, y0, S.x0 - 0.5, y1), (S.x0 - 0.5, y0, W, y1)
        if S.y0 > B.y1 - 1:
            pw = _vlabel(toks, ("密码区",))
            right = pw.x0 - 1 if pw and pw.x0 > B.x1 else W * 0.62
            rk = _vlabel(toks, ("备注",))
            right_s = rk.x0 - 1 if rk and rk.x0 > S.x1 and abs(rk.cy - S.cy) < S.h else W * 0.62
            return ((B.x0 - pad, B.y0 - pad, right, B.y1 + pad),
                    (S.x0 - pad, S.y0 - pad, right_s, S.y1 + pad))
    labs = sorted([t for t in toks if re.match(r"^名称", t.c)], key=lambda t: (t.y0, t.x0))
    if len(labs) >= 2:
        a, b = labs[0], labs[1]
        if _yov(a, b) > 0.3 or abs(a.cy - b.cy) < 2 * a.h:
            a, b = sorted((a, b), key=lambda t: t.x0)
            split = b.x0 - 0.5 * b.h
            y0 = min(a.y0, b.y0) - 1.5 * a.h
            y1 = table_top if table_top and table_top > y0 else max(a.y1, b.y1) + 5 * a.h
            return (0, y0, split, y1), (split, y0, W, y1)
        return ((0, a.y0 - a.h, W * 0.62, a.y1 + 4 * a.h), (0, b.y0 - b.h, W * 0.62, b.y1 + 4 * b.h))
    top = H * 0.12
    bot = table_top if table_top else H * 0.45
    return (0, top, W / 2.0, bot), (W / 2.0, top, W, bot)


_HDR_KEYS = [("项目名称", "name"), ("货物或应税劳务、服务名称", "name"), ("货物或应税劳务名称", "name"),
             ("货物或应税劳务", "name"), ("规格型号", "spec"), ("单位", "unit"), ("数量", "qty"),
             ("单价", "price"), ("金额", "amount"), ("税率/征收率", "rate"), ("税率征收率", "rate"),
             ("征收率", "rate"), ("税率", "rate"), ("税额", "tax")]


def _table_header(toks):
    """明细表头：→ (表头 token 列表, {列: (x0, cx)}) 或 (None, {})。"""
    anchor = None
    for t in toks:
        if "项目名称" in t.c or "货物或应税劳务" in t.c or t.c in ("名称", "项目"):
            row = [u for u in toks if _yov(t, u) >= 0.5]
            if any("金额" in u.c or u.c in ("金", "额") for u in row):
                anchor = t
                break
    if not anchor:
        return None, {}
    row = sorted([u for u in toks if _yov(anchor, u) >= 0.5], key=lambda u: u.x0)
    cols = {}
    used = set()
    for i in range(len(row)):
        if i in used:
            continue
        for n in (1, 2, 3):
            if i + n > len(row):
                break
            seg = row[i:i + n]
            s = "".join(u.c for u in seg)
            key = None
            for k, col in _HDR_KEYS:
                if s == k or (n == 1 and k in s and len(s) <= len(k) + 2):
                    key = col
                    break
            if key and key not in cols:
                x0 = min(u.x0 for u in seg)
                x1 = max(u.x1 for u in seg)
                cols[key] = (x0, (x0 + x1) / 2.0, x1)
                used.update(range(i, i + n))
                break
    return row, cols


def _find_heji(toks, W, lo_y=None):
    """'合计'行锚点（排除"价税合计"）：'合计'/'合 计' 一个 token，或'合''计'两个单字同行。"""
    for t in toks:
        if t.c == "合计" or (t.c.startswith("合计") and len(t.c) <= 4):
            if lo_y is None or t.cy > lo_y:
                return t
    for a in toks:
        if a.c == "合" and (lo_y is None or a.cy > lo_y):
            for b in toks:
                if b.c == "计" and b.x0 > a.x1 and b.x0 - a.x1 < W * 0.25 and _yov(a, b) > 0.4:
                    return _T("合计", a.x0, min(a.y0, b.y0), b.x1, max(a.y1, b.y1))
    return None


def _norm_rate(c):
    c = _fix_num_text(c).replace(" ", "")
    if "免税" in c:
        return "免税"
    if "不征税" in c:
        return "不征税"
    if re.fullmatch(r"\*{2,}", c):
        return "***"
    m = re.fullmatch(r"(\d{1,2}(?:\.\d+)?)%", c)
    if m:
        return m.group(1) + "%"
    return None


_SPECIAL_LABELS = ("旅客运输服务", "通行费", "不动产经营租赁服务", "不动产租赁服务", "建筑服务", "货物运输服务",
                   "农产品收购", "成品油", "机动车", "二手车", "稀土", "卷烟", "自产农产品销售", "差额征税",
                   "代收车船税", "不动产销售", "拖拉机和联合收割机", "光伏")


def _type_from_title(c):
    if "铁路电子客票" in c:
        return "train"
    if "航空运输电子客票行程单" in c or ("航空" in c and "行程单" in c):
        return "flight"
    if "二手车" in c or "机动车" in c:
        return "vehicle"
    if "通行费" in c:
        return "toll"
    # 老式票名字里也带"专用发票"（XX市出租汽车专用发票、过路（过桥）费专用发票），不是增值税专票，先认出来
    if "出租" in c or "TAXI" in c.upper():
        return "taxi"
    if "过路" in c or "过桥" in c:
        return "tollpaper"
    if "定额" in c:
        return "quota"
    if "专用发票" in c:
        # 专票标题一定带"增值税"（电子发票（增值税专用发票）/XX增值税专用发票）；只剩"专用发票"几个字的
        # （OCR 没认全、或别的行业专用票）不当专票：交给上层按"票种没认全"处理，不会误判成可抵扣
        return "special" if "增值税" in c else None
    if "普通发票" in c:
        return "normal"
    if "机打" in c:
        return "general"
    return None


def _canon_title(c):
    t = c.replace("(", "（").replace(")", "）")
    return t[:40]


def _skew_angle(tokens):
    """OCR 文字行的中位倾角（弧度）；样本太少、角度太小（<0.4°）或离谱（>20°）返回 0。"""
    angs = []
    for t in tokens or []:
        p = t.get("poly")
        if not p or len(p) < 4:
            continue
        try:
            (ax, ay), (bx, by), (dx, dy) = p[0][:2], p[1][:2], p[3][:2]
        except (TypeError, ValueError):
            continue
        w = math.hypot(bx - ax, by - ay)
        h = math.hypot(dx - ax, dy - ay)
        if w < 20 or w < 2.5 * h:
            continue
        angs.append(math.atan2(by - ay, bx - ax))
    if len(angs) < 4:
        return 0.0
    angs.sort()
    a = angs[len(angs) // 2]
    if abs(a) < math.radians(0.4) or abs(a) > math.radians(20):
        return 0.0
    return a


def _nb(box, W, H):
    if not box:
        return None
    x0, y0, x1, y1 = box
    f = lambda v, d: round(min(1.0, max(0.0, v / float(d))), 4)
    return [f(x0, W), f(y0, H), f(x1, W), f(y1, H)]


def fields_from_tokens(tokens, width, height, src, page=0):
    """按位置把一页的文字块解析成票面字段（PDF 文字层、OCR 框、OFD 文字对象共用）。
    tokens=[{"text", "box": [x0,y0,x1,y1]（与 width/height 同一坐标系，y 向下）}]。
    → 完整 Doc 结构（所有键都在），fieldSrc[字段]={"src", "page", "box"(0..1)}；
       kind 按票面判断：有"发票"标题且读到号码 → invoice；像收据 → receipt；否则 other。"""
    W, H = float(width or 1), float(height or 1)
    ocr = (src == "ocr")
    # 照片拍斜了（透视/手抖）：同一行字的 y 在整页宽度上会漂出一个字高，按行配对就散了。
    # OCR 框带四点多边形时取文字行的中位倾角，先把坐标转正再解析，出框时再转回去。
    ang = _skew_angle(tokens) if ocr else 0.0
    ca, sa = math.cos(-ang), math.sin(-ang)
    ox, oy = W / 2.0, H / 2.0

    def fwd(x, y):
        dx, dy = x - ox, y - oy
        return ox + dx * ca - dy * sa, oy + dx * sa + dy * ca

    def nbox(box):
        if not box:
            return None
        if ang:
            c2, s2 = math.cos(ang), math.sin(ang)
            pts = []
            for x, y in ((box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])):
                dx, dy = x - ox, y - oy
                pts.append((ox + dx * c2 - dy * s2, oy + dx * s2 + dy * c2))
            box = [min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)]
        return _nb(box, W, H)

    toks = []
    for tk in tokens or []:
        txt = _nfkc(tk.get("text", "")).strip()
        if not txt:
            continue
        b = [float(v) for v in (tk.get("box") or [0, 0, 0, 0])[:4]]
        if ang:
            poly = tk.get("poly") or [(b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3])]
            pts = [fwd(float(p[0]), float(p[1])) for p in poly]
            b = [min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)]
        x0, x1 = min(b[0], b[2]), max(b[0], b[2])
        y0, y1 = min(b[1], b[3]), max(b[1], b[3])
        if ocr:
            txt = _fix_num_text(txt)
        toks.append(_T(txt, x0, y0, x1, y1))
    d = new_doc(page)
    fs = d["fieldSrc"]

    def put(field, value, box):
        if value is None or value == "":
            return
        d[field] = value
        nb = nbox(box)
        if nb:
            fs[field] = {"src": src, "page": page, "box": nb}

    if not toks:
        return d

    # —— 标题与票种 ——
    title = None
    tcands = [t for t in toks if t.cy < H * 0.3 and ("发票" in t.c or "客票" in t.c or "行程单" in t.c)
              and not re.search(r"发票(号码|代码)", t.c) and "监制" not in t.c]
    if tcands:
        title = max(tcands, key=lambda t: (t.h, -abs(t.cx - W / 2)))
        # 有的平台把标题拆成"电子发票(""普通发票"")"三个文字对象：同一行、字号相近、挨着的拼回去
        row = sorted([t for t in toks if _yov(title, t) >= 0.6 and t.h >= 0.7 * title.h and t.h <= 1.4 * title.h],
                     key=lambda t: t.x0)
        if len(row) > 1:
            i = row.index(title)
            lo = hi = i
            while lo > 0 and row[lo].x0 - row[lo - 1].x1 < 1.2 * title.h:
                lo -= 1
            while hi < len(row) - 1 and row[hi + 1].x0 - row[hi].x1 < 1.2 * title.h:
                hi += 1
            if hi > lo:
                seg = row[lo:hi + 1]
                b = _union([t.box for t in seg])
                title = _T("".join(t.t.strip() for t in seg), b[0], b[1], b[2], b[3])
    special = None
    for t in toks:
        if t.cy < H * 0.3 and t.c in _SPECIAL_LABELS:
            special = t
            break
    inv_type = _type_from_title(title.c) if title else None
    if title and not inv_type and "发票" in title.c:
        inv_type = "normal"   # 标题认不全（OCR）：先按普票算（不会误判成可抵扣），审核时核
        d["warnings"].append("票种没认全，先按普通发票处理，请核对")
    if special is not None and special.c == "旅客运输服务" and inv_type == "normal":
        inv_type = "travel"
    if special is not None and special.c == "通行费" and inv_type == "normal":
        inv_type = "toll"
    if title:
        put("typeLabel", special.c if special is not None else _canon_title(title.c),
            special.box if special is not None else title.box)
    d["invType"] = inv_type

    # —— 抬头区：号码、代码、日期、校验码 ——
    def by_label(pat, accept, zone_y=0.4):
        for lab in [t for t in toks if re.search(pat, t.c) and t.cy < H * zone_y]:
            v, b = _label_value(lab, pat, toks, accept=accept)
            if v:
                return v, b
        return None, None

    num, numbox = by_label(r"发票号码|发票号|票号码", lambda s: re.fullmatch(r"(No\.?)?\d{8,20}", s.replace(" ", "")) is not None)
    if num:
        num = re.sub(r"\D", "", num)
    else:
        big = [t for t in toks if re.fullmatch(r"\d{20}", t.c) and t.cy < H * 0.35]
        if big:
            t = max(big, key=lambda t: t.cx)
            num, numbox = t.c, t.box
    put("number", num, numbox)
    code, cbox = by_label(r"发票代码", lambda s: re.fullmatch(r"\d{10}|\d{12}", s.replace(" ", "")) is not None)
    put("code", re.sub(r"\D", "", code) if code else None, cbox)
    dt, dbox = by_label(r"开票日期|开票时间", lambda s: _parse_date(s) is not None)
    if not dt:
        for t in toks:
            if t.cy < H * 0.35 and "年" in t.c and _parse_date(t.c):
                dt, dbox = t.c, t.box
                break
    put("date", _parse_date(dt) if dt else None, dbox)
    ck, kbox = by_label(r"校验码", lambda s: re.fullmatch(r"[\d ]{5,30}", s) is not None, zone_y=0.5)
    put("checkCode", re.sub(r"\D", "", ck) if ck else None, kbox)

    # —— 明细表头、合计、价税合计 ——
    hdr_row, cols = _table_header(toks)
    table_top = min(u.y0 for u in hdr_row) if hdr_row else None
    table_bot = max(u.y1 for u in hdr_row) if hdr_row else None
    xs_lab = next((t for t in toks if "小写" in t.c), None)
    total = tbox = None
    if xs_lab is not None:
        v, b = _label_value(xs_lab, r"小写\)?", toks, accept=lambda s: _is_money_tok(s))
        if v is not None:
            total, tbox = _parse_money(v, need_dec=False), b
    if total is None:
        jl = next((t for t in toks if "价税合计" in t.c), None)
        if jl is not None:
            ms = [t for t in toks if _yov(jl, t) >= 0.4 and t.x0 > jl.x1 and _is_money_tok(t.c)]
            if ms:
                t = max(ms, key=lambda t: t.x1)
                total, tbox = _parse_money(t.c, need_dec=False), t.box
    if total is None:
        for lab_pat in (r"票价", r"合计金额", r"金额合计"):
            v, b = by_label(lab_pat, lambda s: _is_money_tok(s), zone_y=1.0)
            if v:
                total, tbox = _parse_money(v, need_dec=False), b
                break
    upper = None
    dx_lab = next((t for t in toks if "大写" in t.c), None)
    if dx_lab is not None:
        m = re.search(r"大写\)?", dx_lab.c)
        rest = dx_lab.c[m.end():] if m else ""
        upper = cn_upper_to_float(rest) if rest else None
        if upper is None:
            for t in _right_of(dx_lab, toks):
                upper = cn_upper_to_float(t.c)
                if upper is not None:
                    break
    heji = _find_heji(toks, W, lo_y=table_bot)
    jrow_y = None
    if xs_lab is not None:
        jrow_y = xs_lab.y0
    amount = tax = abox = xbox = None
    money_row = []
    if heji is not None:
        money_row = [t for t in toks if _yov(heji, t) >= 0.4 and t.x0 > heji.x1 and _is_money_tok(t.c)]
    elif table_bot is not None:
        # OCR 常漏掉稀疏的"合  计"两个字：以价税合计上方最近一个带 ¥ 的金额定行，
        # 同一行的金额都算（税额那格的 ¥ 也常被漏认）
        yen = [t for t in toks if t.cy > table_bot and (jrow_y is None or t.cy < jrow_y - 0.3 * t.h)
               and "¥" in _fix_num_text(t.c) and _is_money_tok(t.c)]
        if yen:
            last = max(yen, key=lambda t: t.cy)
            money_row = [t for t in toks if _yov(last, t) >= 0.4 and _is_money_tok(t.c)]
    if money_row:
        money_row.sort(key=lambda t: t.x0)
        if "amount" in cols and "tax" in cols and len(money_row) <= 2:
            for t in money_row:
                col = "amount" if abs(t.cx - cols["amount"][1]) <= abs(t.cx - cols["tax"][1]) else "tax"
                if col == "amount" and amount is None:
                    amount, abox = _parse_money(t.c, need_dec=False), t.box
                elif col == "tax" and tax is None:
                    tax, xbox = _parse_money(t.c, need_dec=False), t.box
        else:
            amount, abox = _parse_money(money_row[0].c, need_dec=False), money_row[0].box
            if len(money_row) >= 2:
                tax, xbox = _parse_money(money_row[-1].c, need_dec=False), money_row[-1].box

    # —— 明细行 ——
    lines = []
    if hdr_row:
        bottom = heji.y0 if heji is not None else (jrow_y if jrow_y else H)
        if money_row:
            bottom = min(bottom, min(t.y0 for t in money_row))
        for t in toks:
            if "出行人" in t.c and table_bot < t.cy < bottom:
                bottom = min(bottom, t.y0)
        other_cols = [v[0] for k, v in cols.items() if k != "name"]
        name_right = (cols["spec"][0] if "spec" in cols else (min(other_cols) if other_cols else W * 0.3)) - 1
        body = [t for t in toks if t.cy > table_bot and t.cy < bottom - 0.2 and t not in hdr_row]
        body.sort(key=lambda t: t.cy)
        rows = []
        for t in body:
            for r in rows:
                if _yov(r[0], t) >= 0.5:
                    r.append(t)
                    break
            else:
                rows.append([t])
        num_cols = [(k, v[1]) for k, v in cols.items() if k != "name"]
        cur = None
        for r in rows:
            r.sort(key=lambda t: t.x0)
            nm = [t for t in r if t.cx < name_right]
            vals = {}
            for t in r:
                if t in nm or not num_cols:
                    continue
                k = min(num_cols, key=lambda kv: abs(kv[1] - t.cx))[0]
                vals[k] = (vals[k] + t.c) if k in vals and k in ("spec", "unit") else vals.get(k, t.c)
            name_txt = "".join(t.t.strip() for t in nm)
            amt = _parse_money(vals["amount"], need_dec=False) if "amount" in vals else None
            starts = name_txt.startswith("*") or (amt is not None and (cur is None or cur["amount"] is not None))
            if starts:
                cur = {"name": name_txt, "category": None, "spec": vals.get("spec"), "unit": vals.get("unit"),
                       "qty": None, "price": None, "amount": None, "rate": None, "tax": None, "_box": _union([t.box for t in nm])}
                lines.append(cur)
            elif cur is not None:
                cur["name"] += name_txt
                for k in ("spec", "unit"):
                    if vals.get(k):
                        cur[k] = (cur[k] or "") + vals[k]
            else:
                continue
            if amt is not None and cur["amount"] is None:
                cur["amount"] = amt
            if "qty" in vals and cur["qty"] is None:
                cur["qty"] = _parse_num(vals["qty"])
            if "price" in vals and cur["price"] is None:
                cur["price"] = _parse_num(vals["price"])
            if "rate" in vals and cur["rate"] is None:
                cur["rate"] = _norm_rate(vals["rate"]) or vals["rate"]
            if "tax" in vals and cur["tax"] is None:
                tv = vals["tax"]
                cur["tax"] = 0.0 if re.fullmatch(r"\*+", tv) else _parse_money(tv, need_dec=False)
        for ln in lines:
            m = re.match(r"^\*([^*]+)\*(.*)$", ln["name"])
            if m:
                ln["category"], ln["name"] = m.group(1), m.group(2)
            if ln["rate"] in ("免税", "不征税", "***") and ln["tax"] is None:
                ln["tax"] = 0.0
    lines = [l for l in lines if l["name"] or l["amount"] is not None]
    rates = []
    for ln in lines:
        r = ln["rate"]
        if r and r != "***" and r not in rates:
            rates.append(r)
    if not rates and "rate" in cols:
        for t in toks:
            r = _norm_rate(t.c)
            if r and r != "***" and abs(t.cx - cols["rate"][1]) < 40 and r not in rates:
                rates.append(r)
    if lines:
        first = lines[0]
        box = first.get("_box")
        put("category", first["category"], box)
        rb = None
        for t in toks:
            if rates and _norm_rate(t.c) == rates[0] and table_bot is not None and t.cy > table_bot:
                rb = t.box
                break
        put("taxRate", ",".join(rates) if rates else None, rb)
    elif rates:
        d["taxRate"] = ",".join(rates)
    for ln in lines:
        ln.pop("_box", None)
        for k in ("amount", "tax"):
            ln[k] = _r2(ln[k])
    d["lines"] = lines

    # —— 金额兜底与自洽 ——
    if amount is None and lines and all(l["amount"] is not None for l in lines):
        amount = round(sum(l["amount"] for l in lines), 2)
    if tax is None and lines and all(l["tax"] is not None for l in lines):
        tax = round(sum(l["tax"] for l in lines), 2)
    if tax is None and d.get("taxRate") and all(r in ("免税", "不征税") for r in d["taxRate"].split(",")):
        tax = 0.0
    if ocr and upper is not None and total is not None and abs(upper - total) > 0.005:
        if amount is not None and tax is not None and abs(amount + tax - upper) < 0.011:
            d["warnings"].append("价税合计小写没认准，已按大写金额改正")
            total = upper
    if total is None and upper is not None:
        total = upper
        tbox = tbox or (dx_lab.box if dx_lab is not None else None)
    if tax is None and amount is not None and total is not None and total - amount >= -0.005:
        tax = round(total - amount, 2)
        xbox = None
    if total is None and amount is not None and tax is not None:
        total = round(amount + tax, 2)
        d["warnings"].append("没读到价税合计，按金额+税额推算")
    put("amount", _r2(amount), abox)
    if tax is not None:
        d["tax"] = _r2(tax)
        if xbox:
            fs["tax"] = {"src": src, "page": page, "box": nbox(xbox)}
    put("total", _r2(total), tbox)
    if upper is not None and d["total"] is not None and abs(upper - d["total"]) > 0.005:
        d["warnings"].append("价税合计大小写不一致：大写 %.2f，小写 %.2f" % (upper, d["total"]))

    # —— 买卖双方 ——
    breg, sreg = _party_regions(toks, W, H, table_top)
    bn, bnb, bt, btb = _party(breg, toks, ocr)
    sn, snb, st, stb = _party(sreg, toks, ocr)
    put("buyerName", bn, bnb)
    put("buyerTaxId", bt, btb)
    put("sellerName", sn, snb)
    put("sellerTaxId", st, stb)

    # —— 备注 ——
    rk = _vlabel(toks, ("备注",))
    if rk is None:
        rk = next((t for t in toks if t.c.startswith("备注")), None)
    if rk is not None:
        kp = next((t for t in toks if re.match(r"^(开票人|收款人|复核)", t.c) and t.cy > rk.y0), None)
        lo = (xs_lab.y1 if xs_lab is not None and xs_lab.y1 < rk.y1 else rk.y0 - 3 * rk.w)
        hi = kp.y0 if kp is not None else rk.y1 + 3 * rk.w
        rtoks = [t for t in toks if t.x0 >= rk.x1 - 1 and lo <= t.cy <= hi and t is not rk
                 and not re.match(r"^(开票人|收款人|复核|销售方|价税合计)", t.c) and t.c not in ("备", "注")]
        if rk.c.startswith("备注") and len(rk.c) > 2:
            rest = rk.t.split("注", 1)[-1].lstrip(":： ")
            if rest:
                rtoks.insert(0, _T(rest, rk.x0, rk.y0, rk.x1, rk.y1))
        if rtoks:
            rtoks.sort(key=lambda t: (round(t.cy / max(t.h, 1)), t.x0))
            text = " ".join(re.sub(r"\s{2,}", " ", t.t.strip()) for t in rtoks).strip()
            put("remark", text[:500], _union([t.box for t in rtoks]))

    # —— 是不是发票 ——
    alltext = "".join(t.c for t in toks)
    if title is not None and (d["number"] or d["code"]):
        d["kind"], d["isInvoice"] = "invoice", True
        if not d["invType"]:
            d["invType"] = "other"
    elif "收据" in alltext and "发票" not in (title.c if title else ""):
        d["kind"] = "receipt"
    else:
        # 不是发票（行程单、审批单、对账单……）：按发票版式硬套出来的字段没意义，清掉；
        # typeLabel 留着当"这份文件的标题"给界面显示（如"某某出行-行程单"）
        keep = {"typeLabel": d["typeLabel"], "page": page, "warnings": [],
                "fieldSrc": {k: v for k, v in fs.items() if k == "typeLabel"}}
        d = new_doc(page)
        d.update(keep)
    return d


def _qr_regions(boxes, W, H):
    """一页上有几张发票（A4 上下拼两张、扫描件拼四张…）：发票二维码都在各自票面的左上角，
    所以每个码往右到同一行下一个码、往下到下面一个码，就是它那张票的范围。→ [(x0,y0,x1,y1)]"""
    out = []
    for i, (x0, y0, x1, y1) in enumerate(boxes):
        qw, qh = x1 - x0, y1 - y0
        rx0, ry0, rx1, ry1 = max(0.0, x0 - 0.6 * qw), max(0.0, y0 - 0.6 * qh), float(W), float(H)
        for j, (a0, b0, a1, b1) in enumerate(boxes):
            if j == i:
                continue
            if abs(b0 - y0) < 3 * qh:
                if a0 > x1:
                    rx1 = min(rx1, a0 - 0.6 * (a1 - a0))
            elif b0 > y1 and a0 < x1 + 3 * qw and a1 > x0 - 3 * qw:
                ry1 = min(ry1, b0 - 0.6 * (b1 - b0))
        out.append((rx0, ry0, rx1, ry1))
    return out


def _parse_regions(tokens, W, H, src, page, qrs):
    """按二维码把一页切成几张票分别解析。qrs=[(二维码内容, 像素 box)] → [(二维码内容, Doc)]；
    Doc 的 fieldSrc.box 仍是相对整页的 0..1。"""
    out = []
    for (text, _), (rx0, ry0, rx1, ry1) in zip(qrs, _qr_regions([b for _, b in qrs], W, H)):
        sub = []
        for tk in tokens:
            b = tk["box"]
            cx, cy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
            if rx0 <= cx < rx1 and ry0 <= cy < ry1:
                nt = dict(tk)
                nt["box"] = [b[0] - rx0, b[1] - ry0, b[2] - rx0, b[3] - ry0]
                if tk.get("poly"):
                    nt["poly"] = [[p[0] - rx0, p[1] - ry0] for p in tk["poly"]]
                sub.append(nt)
        rw, rh = rx1 - rx0, ry1 - ry0
        d = fields_from_tokens(sub, rw, rh, src, page)
        for v in d["fieldSrc"].values():
            bx = v.get("box")
            if bx:
                v["box"] = _nb([rx0 + bx[0] * rw, ry0 + bx[1] * rh, rx0 + bx[2] * rw, ry0 + bx[3] * rh], W, H)
        out.append((text, d))
    return out


# ---------------------------------------------------------------------------
# 6b. 没有二维码的老式票（出租车票、过路过桥费票、定额票…）：一张照片拼了几张也能拆开
#   每张老式票都印着"发票代码（12 位）＋发票号码（8 位）"——以它为锚点：锚点按左右分列、
#   同列上下按下一张票的标题切开，每块单独取字段。字段一律"待核"。
# ---------------------------------------------------------------------------
OLD_TICKET_TYPES = ("taxi", "tollpaper", "quota", "general")
_OLD_CODE = re.compile(r"^(?:发票代码[:：]?)?([01]\d{11})$")
_OLD_CODE_LAB = re.compile(r"发票代码[:：]?(\d{10}|\d{12})(?!\d)")
_OLD_NUM = re.compile(r"^(?:No\.?)?(\d{8})$", re.I)
_OLD_NUM_LAB = re.compile(r"发票号码[:：]?(\d{8})(?!\d)")
_OLD_TITLE = re.compile(r"出租|过路|过桥|通行费|定额|税务局|TAXI|INVOICE|专用发票|普通发票|统一发票|机打发票$", re.I)
_OLD_TITLE_NOT = re.compile(r"发票代码|发票号码|发票联|专用章|手写无效|发票系|查询|监制")
_KV_LABELS = (  # (字段, 标签正则)：出租车/过路费票面上"标签：值"成列排
    ("paid", r"实收金额|^实收"),
    ("extra", r"另收.{0,4}费"),
    ("fare", r"^金额"),
)  # 只认中文标签：出租车票每个中文标签下面还有一行英文（Fare/Date…），掺进来会把上下错位估歪


def _old_anchor_toks(toks):
    """[_T] → 锚点 [(code_T|None, code, num_T, num)]：发票代码+号码配对；只有带"发票号码"字样的号码可以单独成锚。"""
    codes, nums = [], []
    for t in toks:
        c = t.c
        m = _OLD_CODE_LAB.search(c) or _OLD_CODE.match(c)
        if m and not _parse_date(m.group(1)):
            codes.append((t, m.group(1)))
        m = _OLD_NUM_LAB.search(c)
        if m:
            nums.append((t, m.group(1), True))
            continue
        m = _OLD_NUM.match(c)
        if m and not _parse_date(m.group(1)):
            nums.append((t, m.group(1), False))
    out, used = [], set()
    for ct, code in codes:
        best = None
        for i, (nt, num, lab) in enumerate(nums):
            if i in used or nt is ct:
                continue
            below = 0 < nt.cy - ct.cy < 3.2 * ct.h and (min(ct.x1, nt.x1) - max(ct.x0, nt.x0) > -ct.h
                                                          or abs(nt.x0 - ct.x0) < 2 * ct.h)
            right = abs(nt.cy - ct.cy) < 0.6 * ct.h and 0 <= nt.x0 - ct.x1 < 6 * ct.h
            if below or right:
                dist = abs(nt.cy - ct.cy) + abs(nt.x0 - ct.x0) * 0.2
                if best is None or dist < best[0]:
                    best = (dist, i)
        if best is not None:
            used.add(best[1])
            nt, num, _ = nums[best[1]]
            out.append((ct, code, nt, num))
    for i, (nt, num, lab) in enumerate(nums):
        if i not in used and lab:
            out.append((None, None, nt, num))
    seen, uniq = set(), []
    for a in out:
        if a[3] not in seen:
            seen.add(a[3])
            uniq.append(a)
    return uniq


def _abox(a):
    ct, _, nt, _ = a
    return _union([t.box for t in (ct, nt) if t is not None])


def _min_cover_cut(toks, lo, hi, axis):
    """lo..hi 之间找文字最稀的一刀（axis=0 竖切看 x，1 横切看 y）；找不到空档就取中点。"""
    if hi - lo < 4:
        return (lo + hi) / 2.0
    step = max(1.0, (hi - lo) / 80.0)
    best, runs, x = None, [], lo
    while x <= hi:
        n = sum(1 for t in toks if (t.x0 if axis == 0 else t.y0) <= x <= (t.x1 if axis == 0 else t.y1))
        if best is None or n < best:
            best, runs = n, [[x, x]]
        elif n == best:
            if runs and x - runs[-1][1] <= step * 1.01:
                runs[-1][1] = x
            else:
                runs.append([x, x])
        x += step
    r = max(runs, key=lambda r: r[1] - r[0])
    return (r[0] + r[1]) / 2.0


def old_ticket_regions(toks, W, H):
    """一张照片里的老式票 → [(锚点, (x0,y0,x1,y1) 像素)]；锚点少于 2 个返回 []（不用拆）。"""
    anchors = _old_anchor_toks(toks)
    if len(anchors) < 2:
        return []
    cols = []                                   # 按锚点左右重叠分列
    for a in sorted(anchors, key=lambda a: _abox(a)[0]):
        b = _abox(a)
        for c in cols:
            if min(c["x1"], b[2]) - max(c["x0"], b[0]) > -0.2 * (b[3] - b[1]):
                c["a"].append(a)
                c["x0"], c["x1"] = min(c["x0"], b[0]), max(c["x1"], b[2])
                break
        else:
            cols.append({"a": [a], "x0": b[0], "x1": b[2]})
    cols.sort(key=lambda c: c["x0"])
    xcuts = [0.0]
    for c1, c2 in zip(cols, cols[1:]):
        xcuts.append(_min_cover_cut(toks, c1["x1"], c2["x0"], 0) if c2["x0"] > c1["x1"] else (c1["x1"] + c2["x0"]) / 2.0)
    xcuts.append(float(W))
    out = []
    for i, c in enumerate(cols):
        x0, x1 = xcuts[i], xcuts[i + 1]
        inside = [t for t in toks if x0 <= t.cx < x1]
        seq = sorted(c["a"], key=lambda a: _abox(a)[1])
        ycuts = [0.0]
        for a, b in zip(seq, seq[1:]):
            lo, hi = _abox(a)[3], _abox(b)[1]
            titles = [t for t in inside if lo < t.cy < hi and _OLD_TITLE.search(t.c) and not _OLD_TITLE_NOT.search(t.c)]
            if titles:
                ycuts.append(min(t.y0 for t in titles) - 0.3 * min(t.h for t in titles))
            else:
                ycuts.append(_min_cover_cut(inside, lo, hi, 1))
        ycuts.append(float(H))
        for j, a in enumerate(seq):
            out.append((a, (x0, ycuts[j], x1, ycuts[j + 1])))
    return out


def _kv_pairs(toks, label_pat):
    """标签列 ↔ 值列对齐（热敏小票的值常整体比标签高/低半行）：先估整体上下错位，再按最近配。
    → [(标签 _T, 值 _T)]，值只取标签右边的金额样 token。"""
    labs = [t for t in toks if re.search(label_pat, t.c) and len(t.c) <= 12]
    vals = [t for t in toks if re.fullmatch(r"¥?\d{1,6}(?:\.\d{1,2})?元?", t.c) and not re.search(label_pat, t.c)]
    if not labs or not vals:
        return []
    hs = sorted(t.h for t in labs)
    lh = hs[len(hs) // 2]
    allabs = [t for t in toks if any(re.search(p, t.c) for _, p in _KV_LABELS) or re.search(
        r"^(日期|时间|单价|里程|等候|状态|车号|证号|上车|下车|卡号|卡余额|车型)", t.c)]

    # 估错位用所有"值样"的字（带数字、不是标签），不只金额：时间、里程、车号都在同一列
    anyvals = [t for t in toks if re.search(r"\d", t.c) and t not in allabs]

    def near(l, d):
        cs = [v for v in anyvals if v.x0 > l.x1 - 0.5 * lh and v.x0 - l.x1 < 12 * lh]
        return min((abs(v.cy - (l.cy + d)) for v in cs), default=None)
    best = (None, 0.0)
    d = -1.2 * lh
    while d <= 1.2 * lh:
        cost = 0.0
        for l in allabs:
            n = near(l, d)
            cost += min(n, lh) if n is not None else 0
        if best[0] is None or cost < best[0] - 1e-6:
            best = (cost, d)
        d += max(1.0, lh / 10.0)
    off = best[1]
    out = []
    for l in labs:
        cs = [v for v in vals if v.x0 > l.x1 - 0.5 * lh and v.x0 - l.x1 < 12 * lh and abs(v.cy - (l.cy + off)) < 0.8 * lh]
        if cs:
            out.append((l, min(cs, key=lambda v: abs(v.cy - (l.cy + off)))))
    return out


def old_ticket_fields(toks, W, H, src="ocr", page=0, anchor=None):
    """老式票（一张的范围内的 tokens，坐标与 W/H 同系）→ Doc：票种、代码、号码、日期、金额、销方。
    金额：有"实收金额"用实收；否则"金额"＋"另收×××费"（深圳出租车另收燃油附加费）。"""
    d = new_doc(page)
    fs = d["fieldSrc"]

    def put(field, value, box):
        if value in (None, ""):
            return
        d[field] = value
        if box:
            fs[field] = {"src": src, "page": page, "box": _nb(box, W, H)}
        if field not in d["pending"]:
            d["pending"].append(field)
    text = "".join(t.c for t in toks)
    it = _type_from_title(text)
    if it not in OLD_TICKET_TYPES:
        # 标题常被章盖住、认不全：按票面内容补认（出租车票有上下车/里程，过路费票有出入口站）
        if re.search(r"上车|下车|里程|等候|车号", text):
            it = "taxi"
        elif re.search(r"入口|出口|收费站|公路发展|车型", text):
            it = "tollpaper"
        elif "定额" in text:
            it = "quota"
        elif "机打" in text:
            it = "general"
    titles = [t for t in toks if _OLD_TITLE.search(t.c) and not _OLD_TITLE_NOT.search(t.c)]
    title = min(titles, key=lambda t: t.y0) if titles else None
    label = {"taxi": "出租汽车发票", "tollpaper": "过路（过桥）费发票", "quota": "定额发票", "general": "通用机打发票"}.get(it)
    if title is not None and it == "taxi" and "出租汽车" in title.c and "发票" in title.c:
        label = _canon_title(title.c)
    d["invType"] = it or "general"
    d["typeLabel"] = label or "通用机打发票"
    if title is not None:
        fs["typeLabel"] = {"src": src, "page": page, "box": _nb(title.box, W, H)}
    if anchor is None:
        an = _old_anchor_toks(toks)
        anchor = an[0] if an else None
    if anchor is not None:
        ct, code, nt, num = anchor
        put("code", code, ct.box if ct is not None else None)
        put("number", num, nt.box)
    if not d["code"]:
        for t in toks:
            m = _OLD_CODE_LAB.search(t.c) or _OLD_CODE.match(t.c)
            if m:
                put("code", m.group(1), t.box)
                break
    for t in sorted(toks, key=lambda t: t.cy):
        v = _parse_date(t.c)
        if v:
            put("date", v, t.box)
            break
    pairs = {k: _kv_pairs(toks, p) for k, p in _KV_LABELS}
    paid = next(((v, _parse_money(v.c, need_dec=False)) for _, v in pairs["paid"]), None)
    fare = next(((v, _parse_money(v.c, need_dec=False)) for _, v in pairs["fare"]), None)
    extra = [(v, _parse_money(v.c, need_dec=False)) for _, v in pairs["extra"]]
    extra = [(v, m) for v, m in extra if m and m > 0]
    if paid and paid[1] and (fare is None or paid[1] >= fare[1] - 0.005):
        put("total", _r2(paid[1]), paid[0].box)
    elif fare and fare[1]:
        tot = fare[1] + sum(m for _, m in extra)
        put("total", _r2(tot), _union([fare[0].box] + [v.box for v, _ in extra]))
        if extra:
            d["warnings"].append("金额按票面金额 %.2f 加另收费用 %s 合计，请核对"
                                 % (fare[1], "、".join("%.2f" % m for _, m in extra)))
    for t in toks:
        m = re.search(r"(?:单位名称[:：]?)?(.{4,40}(?:公司|集团))", t.c)
        if m and "税务" not in t.c and "监制" not in t.c:
            put("sellerName", m.group(1).lstrip(":："), t.box)
            break
    for t in toks:
        m = _TAXID_ANY.search(t.c.upper())
        if m and len(m.group(1)) == 18 and uscc_ok(m.group(1)):
            put("sellerTaxId", m.group(1), t.box)
            break
    if d["number"] or d["code"]:
        d["kind"], d["isInvoice"] = "invoice", True
    return d


def _supplement_old(f, tokens, W, H, page=0):
    """单张老式票（没二维码）：票面认得出是出租车/过路费/定额/机打票 → 改用老式票取法（fields_from_tokens
    按增值税票版式硬套的购销方、金额不可信）。按票面认成增值税票（专票/普票…）的原样返回。"""
    toks = _toks_of(tokens)
    if not _old_anchor_toks(toks):
        return f
    o = old_ticket_fields(toks, W, H, "ocr", page)
    if not o["isInvoice"] or o["invType"] not in OLD_TICKET_TYPES:
        return f
    if f.get("invType") not in (None, "other", "normal", "general") + OLD_TICKET_TYPES:
        return f
    # 认定是老式票：按增值税票版式硬套出来的购销方、金额税额都不可信，以老式票取法为准；
    # 老式取法没取到的号码/代码/日期才用版式解析的
    for k in ("code", "number", "date"):
        if o.get(k) in (None, "") and f.get(k) not in (None, ""):
            o[k] = f[k]
            if f["fieldSrc"].get(k):
                o["fieldSrc"][k] = f["fieldSrc"][k]
            if k not in o["pending"]:
                o["pending"].append(k)
    o["warnings"] += [w for w in f["warnings"] if w not in o["warnings"] and not w.startswith("票种没认全")]
    return o


def _toks_of(tokens):
    out = []
    for tk in tokens or []:
        txt = _fix_num_text(_nfkc(tk.get("text", "")).strip())
        if txt:
            b = [float(v) for v in (tk.get("box") or [0, 0, 0, 0])[:4]]
            out.append(_T(txt, min(b[0], b[2]), min(b[1], b[3]), max(b[0], b[2]), max(b[1], b[3])))
    return out


def split_old_tickets(tokens, W, H, page=0):
    """整张照片的 OCR tokens → 拆出的老式票 [Doc]（每张带 region＝它那块在整图上的 0..1 框）；
    不到两张返回 []。"""
    toks = _toks_of(tokens)
    regs = old_ticket_regions(toks, W, H)
    out = []
    for a, (x0, y0, x1, y1) in regs:
        sub = [t for t in toks if x0 <= t.cx < x1 and y0 <= t.cy < y1]
        d = old_ticket_fields(sub, W, H, "ocr", page, anchor=a)
        d["region"] = _nb([x0, y0, x1, y1], W, H)
        d["warnings"].append("这张照片里拍了 %d 张票，已按票自动拆开；请对着图逐张核对" % len(regs))
        out.append(d)
    return out


def _distinct_invoice_qrs(qrs):
    """[(text, box)] → 按发票号码去重后的发票码（保持顺序）。"""
    seen, out = set(), []
    for t, b in qrs:
        q = parse_invoice_qr(t)
        if q and q["number"] not in seen:
            seen.add(q["number"])
            out.append((t, b))
    return out


# ---------------------------------------------------------------------------
# 7. 发票二维码 → Doc；二维码并进已解析的 Doc
# ---------------------------------------------------------------------------
def _doc_from_qr(text, box=None, page=0):
    q = parse_invoice_qr(text)
    d = new_doc(page)
    if not q:
        return d
    it, label = QR_TYPES.get(q["qrType"], ("other", None))
    d.update(isInvoice=True, kind="invoice", invType=it, typeLabel=label, qrType=q["qrType"], code=q["code"],
             number=q["number"], date=q["date"], total=q["total"], amount=q["amount"],
             checkCode=q["checkCode"], qrRaw=q["raw"], needOcr=True)
    for f in ("code", "number", "date", "total", "amount", "checkCode"):
        if d[f] is not None:
            d["fieldSrc"][f] = {"src": "qr", "page": page, "box": box}
    return d


def _merge_qr(d, text, box, page):
    """PDF/OFD：原件读到的字段为准；原件缺的用二维码补（src=qr）；两边不一致记一条提示。"""
    q = parse_invoice_qr(text)
    if not q:
        return d
    d["qrRaw"], d["qrType"] = q["raw"], q["qrType"]
    for f in ("number", "date", "code", "checkCode", "total", "amount"):
        qv = q.get(f)
        if qv in (None, ""):
            continue
        if d.get(f) in (None, ""):
            d[f] = qv
            d["fieldSrc"][f] = {"src": "qr", "page": page, "box": box}
        elif not _same_val(f, d[f], qv):
            d["warnings"].append("票面%s与二维码不一致：票面 %s，二维码 %s" % (FIELD_LABELS.get(f, f), d[f], qv))
    it, label = QR_TYPES.get(q["qrType"], ("other", None))
    if not d.get("invType") or d["invType"] == "other":
        d["invType"] = it
    if not d.get("typeLabel"):
        d["typeLabel"] = label
    d["isInvoice"], d["kind"] = True, "invoice"
    return d


def _finish(d):
    """收尾：金额自洽检查、税率兜底。"""
    if d["kind"] == "invoice":
        a, t, s = d["amount"], d["tax"], d["total"]
        if a is not None and t is not None and s is not None and abs(a + t - s) > 0.011:
            msg = "金额+税额≠价税合计（%.2f+%.2f≠%.2f）" % (a, t, s)
            if msg not in d["warnings"]:
                d["warnings"].append(msg)
    return d


# ---------------------------------------------------------------------------
# 8. PDF
# ---------------------------------------------------------------------------
def _fitz():
    try:
        import fitz
        return fitz
    except Exception:
        return None


def _bad_doc(msg, page=0):
    d = new_doc(page)
    d["warnings"].append(msg)
    return d


# 页面渲染位图上限 2500 万像素（A4 200dpi 约 390 万）：页面尺寸是文件自己写的，几百字节的 PDF
# 就能声明一张 80 英寸见方的页，按固定倍数渲染会要几个 GB 内存
_RENDER_MAX_PIXELS = 25000000


def _safe_zoom(rect, z):
    """渲染倍数封顶：rect（整页或裁剪区，单位 pt）× z 之后不超过 _RENDER_MAX_PIXELS 像素。"""
    try:
        area = float(rect.width) * float(rect.height)
    except Exception:
        return z
    if not math.isfinite(area):
        return min(z, 1e-3)
    if area <= 0:
        return z
    return min(z, math.sqrt(_RENDER_MAX_PIXELS / area))


def _pdf_tokens(page):
    """页面文字层 → tokens（按页面显示坐标）。斜着的字（印章"全国统一发票监制章"）、竖排边注跳过。"""
    toks = []
    rot = page.rotation or 0
    mat = page.rotation_matrix if rot else None
    try:
        d = page.get_text("dict", flags=3)
    except TypeError:
        d = page.get_text("dict")
    for b in d.get("blocks", []):
        if b.get("type", 0) != 0:
            continue
        for ln in b.get("lines", []):
            dx, dy = ln.get("dir", (1, 0))
            if rot:
                a = math.radians(rot)
                dx, dy = dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)
            if dx < 0.99 or abs(dy) > 0.03:
                continue
            for sp in ln.get("spans", []):
                txt = sp.get("text", "")
                if not txt.strip():
                    continue
                bb = sp["bbox"]
                if mat is not None:
                    import fitz
                    r = fitz.Rect(bb) * mat
                    bb = (r.x0, r.y0, r.x1, r.y1)
                toks.append({"text": txt, "box": [bb[0], bb[1], bb[2], bb[3]], "score": 1.0})
    return toks


def _pix_gray(pix):
    cv2, np = _cv2()
    a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 1:
        return a[:, :, 0].copy()
    return cv2.cvtColor(np.ascontiguousarray(a[:, :, :3]), cv2.COLOR_RGB2GRAY)


def _pdf_page_qr(page, fitz, budget=0.35):
    """页面上的二维码 → [(text, 归一化 box)]。先按"像二维码的小方图"逐个抠出来放大解（快、准），
    解不出再整页 200dpi 渲染扫一遍（矢量画的码、扫描件）。"""
    if not _cv2():
        return []
    cv2, np = _cv2()
    R = page.rect
    out, seen = [], set()
    if not page.rotation:
        try:
            infos = page.get_image_info()
        except Exception:
            infos = []
        for inf in infos[:12]:
            x0, y0, x1, y1 = inf["bbox"]
            w, h = x1 - x0, y1 - y0
            if not (20 <= w <= 260 and 20 <= h <= 260 and 0.75 <= w / max(h, 1e-6) <= 1.33):
                continue
            m = 0.12 * max(w, h)
            clip = fitz.Rect(x0 - m, y0 - m, x1 + m, y1 + m) & R
            z = _safe_zoom(clip, min(8.0, max(2.0, 420.0 / max(w, h))))
            try:
                pix = page.get_pixmap(matrix=fitz.Matrix(z, z), clip=clip, alpha=False)
            except Exception:
                continue
            g = cv2.copyMakeBorder(_pix_gray(pix), 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)
            hits, _ = _qr_try(g)
            for t, p in hits:
                if t in seen:
                    continue
                seen.add(t)
                bx = _pts_box((p - 40) / z)
                bx = [bx[0] + clip.x0, bx[1] + clip.y0, bx[2] + clip.x0, bx[3] + clip.y0]
                out.append((t, _nb(bx, R.width, R.height)))
    if out:
        return _reading_order(out)
    z = _safe_zoom(R, 200.0 / 72.0)     # 超大页面降分辨率渲染（整页位图 ≤ 2500 万像素）
    try:
        pix = page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
    except Exception:
        return out
    g = _pix_gray(pix)
    for t, bx, _ in _qr_scan(g, budget=budget, stop_on_invoice=False):
        if t not in seen:
            seen.add(t)
            out.append((t, _nb([v / z for v in bx], R.width, R.height)))
    return _reading_order(out)


def _reading_order(qrs):
    """[(text, 归一化 box)] 按从上到下、同一行从左到右排（一页多张票时 Doc 顺序跟纸面一致）。"""
    return sorted(qrs, key=lambda q: ((int(q[1][1] * 12), q[1][0]) if q[1] else (99, 0)))


def extract_pdf(data, max_pages=30):
    """PDF → [Doc]：一张发票一个 Doc（一般一页一张）；整份没有发票 → 只回一个 kind=other 的 Doc
    （页面上有钉钉审批二维码时 approvalLink 有值＝这是审批单打印件）。
    有文字层：按位置解析（src=pdf）＋二维码核对/补缺；没文字层（扫描件）：只读二维码，needOcr=True，
    交后台对渲染页做 OCR（extract_image_ocr(render_pdf(...)[page]["jpeg"], qr_doc=doc)）。
    文件坏/加密：回 other Doc，warnings 里写原因，不抛异常。"""
    fitz = _fitz()
    if fitz is None:
        return [_bad_doc("服务器缺少 PDF 组件（PyMuPDF），没法读 PDF")]
    try:
        pdf = fitz.open(stream=bytes(data), filetype="pdf")
    except Exception:
        return [_bad_doc("PDF 打不开（文件损坏或不是 PDF）")]
    try:
        if pdf.needs_pass and not pdf.authenticate(""):
            return [_bad_doc("PDF 有密码，打不开")]
        docs, others = [], []
        n = pdf.page_count
        for i in range(min(n, max_pages)):
            page = pdf[i]
            R = page.rect
            toks = _pdf_tokens(page)
            has_text = sum(len(_compact(t["text"])) for t in toks) >= 20
            # 有文字层的页整页兜底扫码给少点时间（票面码几乎都是嵌入小图，第一步就读到了）
            qrs = _pdf_page_qr(page, fitz, budget=0.2 if has_text else 0.35)
            inv_qrs = [q for q in qrs if parse_invoice_qr(q[0])]
            aflow = next((q[0] for q in qrs if classify_code(q[0])["kind"] == "approval_link"), None)
            multi = _distinct_invoice_qrs(inv_qrs)
            if has_text and len(multi) >= 2 and all(b for _, b in multi):
                # 一页拼了几张电子票：按二维码切块分别解析，免得两张票的字段搅在一起
                qpx = [(t, [b[0] * R.width, b[1] * R.height, b[2] * R.width, b[3] * R.height]) for t, b in multi]
                for (t, d), (_, nb) in zip(_parse_regions(toks, R.width, R.height, "pdf", i, qpx), multi):
                    if d["kind"] != "invoice":
                        d = fields_from_tokens([], 1, 1, "pdf", page=i)
                        d["needOcr"] = True
                    _merge_qr(d, t, nb, i)
                    docs.append(_finish(d))
                continue
            if has_text:
                d = fields_from_tokens(toks, R.width, R.height, "pdf", page=i)
                if d["kind"] == "invoice" or inv_qrs:
                    if d["kind"] != "invoice":
                        d["needOcr"] = True   # 有码但文字层读不出票面（字体乱码等）
                    used = None
                    for q in inv_qrs:
                        pq = parse_invoice_qr(q[0])
                        if not d["number"] or _same_val("number", d["number"], pq["number"]):
                            used = q
                            break
                    if used is None and inv_qrs:
                        used = inv_qrs[0]
                    if used is not None:
                        _merge_qr(d, used[0], used[1], i)
                    docs.append(_finish(d))
                    for q in inv_qrs:
                        if q is not used and parse_invoice_qr(q[0])["number"] != d["number"]:
                            docs.append(_doc_from_qr(q[0], q[1], i))
                    continue
                d["approvalLink"] = aflow
                others.append(d)
            else:
                if inv_qrs:
                    for q in inv_qrs:
                        docs.append(_doc_from_qr(q[0], q[1], i))
                    continue
                o = new_doc(i)
                o["needOcr"] = True
                o["approvalLink"] = aflow
                if aflow:
                    o["needOcr"] = False
                others.append(o)
        if docs:
            if n > max_pages:
                docs[0]["warnings"].append("PDF 超过 %d 页，后面的页没有读" % max_pages)
            return docs
        o = others[0] if others else new_doc(0)
        link = next((x["approvalLink"] for x in others if x.get("approvalLink")), None)
        o["approvalLink"] = link
        o["needOcr"] = bool(any(x.get("needOcr") for x in others)) and not link
        o.update(kind="other", isInvoice=False)
        return [o]
    finally:
        pdf.close()


def _pix_jpeg(pix, quality=82):
    Image, _ = _pil()
    mode = "L" if pix.n == 1 else "RGB"
    im = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def _render_page(fitz, page, long_side):
    r = page.rect
    z = _safe_zoom(r, long_side / float(max(r.width, r.height, 1)))
    pix = page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
    return {"jpeg": _pix_jpeg(pix), "w": pix.width, "h": pix.height}


def render_pdf(data, max_pages=5, long_side=1600):
    """PDF 前几页渲染成预览 JPEG：[{"jpeg", "w", "h"}]（长边 long_side；单页位图封顶 2500 万像素）。"""
    fitz = _fitz()
    if fitz is None:
        return []
    pdf = fitz.open(stream=bytes(data), filetype="pdf")
    try:
        if pdf.needs_pass and not pdf.authenticate(""):
            return []
        return [_render_page(fitz, pdf[i], long_side) for i in range(min(pdf.page_count, max_pages))]
    finally:
        pdf.close()


def render_pdf_page(data, page, long_side=1800):
    """只渲染 PDF 的第 page 页（0 起）→ {"jpeg", "w", "h"}；页码超出/加密/打不开返回 None。
    给后台补识别用：只要一页时不必把前面的页都渲染一遍。"""
    fitz = _fitz()
    if fitz is None:
        return None
    try:
        pdf = fitz.open(stream=bytes(data), filetype="pdf")
    except Exception:
        return None
    try:
        if pdf.needs_pass and not pdf.authenticate(""):
            return None
        page = int(page or 0)
        if not 0 <= page < pdf.page_count:
            return None
        return _render_page(fitz, pdf[page], long_side)
    finally:
        pdf.close()


# ---------------------------------------------------------------------------
# 9. OFD / XML / 虚拟图片
# ---------------------------------------------------------------------------
def _local(tag):
    return tag.split("}", 1)[-1] if isinstance(tag, str) else ""


# 发票 XML/OFD 里的 XML 都只有几 KB～几百 KB：超 10MB、超 20 万个元素的不是正常票面，不解析
# （一个 "<a/>" 4 字节，解析成元素对象要一两百字节，10MB 就能撑出上 GB 的树）
_XML_MAX_BYTES = 10 * _MB
_XML_MAX_ELEMS = 200000
_XML_BOMS = ((b"\x00\x00\xfe\xff", "utf-32-be"), (b"\xff\xfe\x00\x00", "utf-32-le"),
             (b"\xfe\xff", "utf-16-be"), (b"\xff\xfe", "utf-16-le"), (b"\xef\xbb\xbf", "utf-8"))
_XML_NUL = ((b"\x00\x00\x00<", "utf-32-be"), (b"<\x00\x00\x00", "utf-32-le"),
            (b"\x00<\x00?", "utf-16-be"), (b"<\x00?\x00", "utf-16-le"))
_XML_DTD = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)", re.I)


def _xml_text(b):
    """XML 字节 → str：先认 BOM、再认 UTF-16/32 的零字节特征、再认声明里的编码（GBK 按 GB18030），
    都没有按 UTF-8。解码后再做安全检查，UTF-16/32、EBCDIC 等编码绕不过去。"""
    for bom, enc in _XML_BOMS:
        if b.startswith(bom):
            return b[len(bom):].decode(enc, "replace")
    for sig, enc in _XML_NUL:
        if b.startswith(sig):
            return b.decode(enc, "replace")
    m = re.search(rb"encoding\s*=\s*[\"']([A-Za-z0-9_.:\-]+)[\"']", b[:200])
    enc = m.group(1).decode("ascii").lower() if m else "utf-8"
    if enc in ("gbk", "gb2312", "gb_2312-80", "cp936"):
        enc = "gb18030"
    try:
        codecs.lookup(enc)
    except LookupError:
        enc = "utf-8"
    return b.decode(enc, "replace")


def _xml_root(b):
    """解析 XML → 根元素；不合格返回 None。
    安全：先按实际编码解码成 str，再拒收 DOCTYPE/实体定义（防实体膨胀；标准库本来就不解外部实体）；
    超 10MB 或超 20 万个元素的不解析（防用极小的标签撑爆内存）。
    编码：pyexpat 不认 GBK 这类多字节编码声明，所以一律自己解码、去掉声明再按 UTF-8 解析。"""
    if not b:
        return None
    b = bytes(b)
    if len(b) > _XML_MAX_BYTES:
        return None
    try:
        s = _xml_text(b)
    except Exception:
        return None
    if _XML_DTD.search(s):
        return None
    s = re.sub(r"^\s*<\?xml[^>]*\?>", "", s.lstrip("\ufeff \t\r\n"))
    if not s.lstrip().startswith("<"):
        return None
    pp = ET.XMLPullParser(events=("start",))
    root, n = None, 0
    try:
        for i in range(0, len(s), 1 << 16):
            pp.feed(s[i:i + (1 << 16)])
            for _ev, el in pp.read_events():
                if root is None:
                    root = el
                n += 1
                if n > _XML_MAX_ELEMS:
                    return None
        pp.close()
        for _ev, el in pp.read_events():
            if root is None:
                root = el
    except Exception:
        return None
    return root


def _floats(s):
    """坐标串 → [float]；有 nan/inf 的整串不要（坐标带 NaN 会跟着框进库，接口出 JSON 时整个票夹打不开）。"""
    try:
        v = [float(x) for x in re.split(r"[\s,]+", (s or "").strip()) if x]
    except ValueError:
        return []
    return v if all(math.isfinite(x) for x in v) else []


def _phys_box(el):
    b = _floats(el.text)[:4]
    return b if len(b) == 4 and b[2] > 0 and b[3] > 0 else None


def _ofd_objs(root):
    """Content.xml 里所有 TextObject → ([token], {ID: text})。Boundary="x y w h"（毫米，y 向下）。"""
    toks, by_id = [], {}
    if root is None:
        return toks, by_id
    for el in root.iter():
        if _local(el.tag) != "TextObject":
            continue
        txt = "".join((c.text or "") for c in el.iter() if _local(c.tag) == "TextCode")
        if not txt.strip():
            continue
        b = _floats(el.get("Boundary"))
        if len(b) < 4:
            continue
        x, y, w, h = b[:4]
        toks.append({"text": txt, "box": [x, y, x + w, y + h], "score": 1.0})
        if el.get("ID"):
            by_id[el.get("ID")] = txt
    return toks, by_id


# CustomTag（老版 OFD 电子发票，标签指向页面上文字对象的 ID）→ Doc 字段
_OFD_TAGS = {"InvoiceCode": "code", "InvoiceNo": "number", "IssueDate": "date", "InvoiceCheckCode": "checkCode",
             "BuyerName": "buyerName", "BuyerTaxID": "buyerTaxId", "SellerName": "sellerName",
             "SellerTaxID": "sellerTaxId", "TaxExclusiveTotalAmount": "amount", "TaxTotalAmount": "tax",
             "TaxInclusiveTotalAmount": "total", "Note": "remark"}


def _coerce(field, v):
    v = _nfkc(v).strip()
    if not v:
        return None
    if field in ("amount", "tax", "total"):
        return _parse_money(v, need_dec=False)
    if field == "date":
        return _parse_date(v)
    if field in ("number", "code", "checkCode"):
        return re.sub(r"\D", "", v) or None
    if field in ("buyerTaxId", "sellerTaxId"):
        return _taxid_in(v) or v.upper()
    return v


_OFD_ENTRY_MAX = 10 * _MB     # OFD 包里单个条目解开后上限
_OFD_TOTAL_MAX = 50 * _MB     # 一个 OFD 累计读取上限
_OFD_REFUSED = "OFD 文件里有过大或压缩得异常的内容，没有读（疑似异常文件，请核对原件）"


def extract_ofd(data):
    """OFD（zip）→ [Doc]。读 OFD.xml → Document.xml → 各页 Content.xml（连同模板页）的 TextObject，
    位置 token 交给同一个按位置解析器（src=ofd）；老版 OFD 的 CustomTag、包里附带的发票 XML、
    资源里的二维码图片都拿来补缺/核对。fieldSrc.box 对应 render_virtual 画出的虚拟图片。"""
    try:
        z = _open_zip(data)
    except _ZipTooBig:
        return [_bad_doc("OFD 文件里的内容多得不正常，没有读（疑似异常文件，请核对原件）")]
    except Exception:
        return [_bad_doc("OFD 文件打不开（文件损坏）")]
    with z:
        index = {}
        for n in z.namelist():
            index[n.replace("\\", "/").lstrip("/").lower()] = n
        budget = {"left": _OFD_TOTAL_MAX, "refused": 0}

        def read(p, cap=None):
            """读包里一个条目：单个 ≤10MB、整包累计 ≤50MB（模板页被引用多次也累计）、
            解开后超 1MB 的压缩比不超过 100 倍；超了不读（压缩炸弹：几百 KB 的 OFD 能解出几个 GB）。
            cap 是调用方自己的更小上限（比如图片 3MB），超了静默跳过。"""
            n = index.get((p or "").replace("\\", "/").lstrip("/").lower())
            if not n:
                return None
            try:
                info = z.getinfo(n)
            except KeyError:
                return None
            size = int(info.file_size)
            if cap is not None and size > cap:
                return None
            packed = max(min(int(info.compress_size), len(data)), 1)
            if size > _OFD_ENTRY_MAX or size > budget["left"] or (size > _ZIP_RATIO_MIN and size > _ZIP_MAX_RATIO * packed):
                budget["refused"] += 1
                return None
            try:
                with z.open(info) as fp:
                    b = fp.read(_OFD_ENTRY_MAX + 1)
            except Exception:
                return None
            if len(b) > _OFD_ENTRY_MAX:
                budget["refused"] += 1
                return None
            budget["left"] -= len(b)
            return b

        def join(base, loc):
            loc = (loc or "").strip().replace("\\", "/")
            if loc.startswith("/"):
                return loc.lstrip("/")
            return posixpath.normpath(posixpath.join(base, loc))

        def bad():
            return [_bad_doc(_OFD_REFUSED if budget["refused"] else "OFD 文件结构不对，读不出来")]

        root = _xml_root(read("OFD.xml"))
        if root is None:
            return bad()
        docroot = next((el.text for el in root.iter() if _local(el.tag) == "DocRoot" and el.text), None)
        if not docroot:
            return bad()
        docroot = join("", docroot)
        ddir = posixpath.dirname(docroot)
        dxml = _xml_root(read(docroot))
        if dxml is None:
            return bad()
        phys = [0.0, 0.0, 210.0, 140.0]
        for el in dxml.iter():
            if _local(el.tag) == "PhysicalBox" and _phys_box(el):
                phys = _phys_box(el)
                break
        tpls, pages = {}, []
        for el in dxml.iter():
            ln = _local(el.tag)
            if ln == "TemplatePage" and el.get("ID") and el.get("BaseLoc"):
                tpls[el.get("ID")] = el.get("BaseLoc")
            elif ln == "Page" and el.get("BaseLoc"):
                pages.append(el.get("BaseLoc"))
        all_ids = {}
        docs = []
        for pi, loc in enumerate(pages[:30]):
            ppath = join(ddir, loc)
            px = _xml_root(read(ppath))
            if px is None:
                continue
            area = phys
            for el in px.iter():
                if _local(el.tag) == "PhysicalBox" and _phys_box(el):
                    area = _phys_box(el)
                    break
            toks = []
            for el in px.iter():
                if _local(el.tag) == "Template" and el.get("TemplateID") in tpls:
                    tt, ids = _ofd_objs(_xml_root(read(join(ddir, tpls[el.get("TemplateID")]))))
                    toks += tt
                    all_ids.update(ids)
            pt, ids = _ofd_objs(px)
            toks += pt
            all_ids.update(ids)
            ox, oy = area[0], area[1]
            for t in toks:
                b = t["box"]
                t["box"] = [b[0] - ox, b[1] - oy, b[2] - ox, b[3] - oy]
            d = fields_from_tokens(toks, area[2], area[3], "ofd", page=pi)
            d["page"] = pi
            docs.append(d)
        if not docs:
            docs = [new_doc(0)]
        d = docs[0]
        # 老版 OFD：CustomTag 直接指向号码/金额等文字对象，比按位置猜更准
        for n in list(index.values()):
            if not n.lower().endswith(".xml") or "customtag" not in n.lower():
                continue
            cr = _xml_root(read(n))
            if cr is None:
                continue
            for el in cr.iter():
                f = _OFD_TAGS.get(_local(el.tag))
                if not f:
                    continue
                refs = [c.text.strip() for c in el.iter() if _local(c.tag) == "ObjectRef" and c.text]
                val = "".join(all_ids.get(r, "") for r in refs) if refs else (el.text or "")
                v = _coerce(f, val)
                if v is not None and d.get(f) in (None, ""):
                    d[f] = v
                    d["fieldSrc"][f] = {"src": "ofd", "page": 0, "box": None}
        # 包里带的发票 XML（数电 OFD 常附结构化数据）
        skip = ("ofd.xml", "document.xml", "content.xml", "publicres", "documentres", "annotation",
                "signature", "customtag", "attachments.xml", "pages.xml", "res.xml")
        for n in list(index.values()):
            ln = n.lower()
            if not ln.endswith(".xml") or any(s in posixpath.basename(ln) or s in ln for s in skip):
                continue
            xd = extract_xml(read(n) or b"", _virtual=False)[0]
            if xd.get("number"):
                for f in PENDABLE:
                    if xd.get(f) not in (None, "") and d.get(f) in (None, ""):
                        d[f] = xd[f]
                        d["fieldSrc"][f] = {"src": "ofd", "page": 0, "box": None}
                if not d["lines"] and xd["lines"]:
                    d["lines"] = xd["lines"]
                if not d.get("invType") or d["invType"] == "other":
                    d["invType"] = xd.get("invType")
                d["typeLabel"] = d.get("typeLabel") or xd.get("typeLabel")
                break
        # 资源里的二维码图片
        if _cv2():
            imgs = [n for n in index.values() if os.path.splitext(n)[1].lower() in (".png", ".jpg", ".jpeg", ".bmp", ".gif")]
            for n in imgs[:6]:
                b = read(n, cap=3 * _MB)
                if not b:
                    continue
                hit = next((t for t in decode_qr_image(b) if parse_invoice_qr(t)), None)
                if hit:
                    _merge_qr(d, hit, None, 0)
                    break
        out = []
        for x in docs:
            if x is d and (d["number"] or d["qrRaw"]):
                d["isInvoice"], d["kind"] = True, "invoice"
                if not d["invType"]:
                    d["invType"] = "other"
            if x["kind"] == "invoice":
                _virtual_src(x, "ofd")
                out.append(_finish(x))
        if out:
            if budget["refused"]:
                out[0]["warnings"].append(_OFD_REFUSED)
            return out
        o = docs[0]
        o["kind"], o["isInvoice"] = "other", False
        o["warnings"].append("OFD 里没读到发票")
        if budget["refused"]:
            o["warnings"].append(_OFD_REFUSED)
        return [o]


# 数电 XML 标签（据公开资料整理，未见实物：按"本地名精确匹配 → 含关键字匹配"两轮尽量认）
_XML_MAP = [
    ("number", ["EInvoiceNumber", "InvoiceNumber", "InvoiceNo", "FPHM", "发票号码"]),
    ("code", ["InvoiceCode", "FPDM", "发票代码"]),
    ("date", ["IssueTime", "IssueDate", "RequestTime", "InvoiceDate", "KPRQ", "开票日期"]),
    ("buyerName", ["BuyerName", "GMFMC", "购买方名称", "购方名称"]),
    ("buyerTaxId", ["BuyerIdNum", "BuyerTaxID", "BuyerTaxId", "BuyerTaxNo", "GMFNSRSBH", "购买方纳税人识别号", "购方税号"]),
    ("sellerName", ["SellerName", "XSFMC", "销售方名称", "销方名称"]),
    ("sellerTaxId", ["SellerIdNum", "SellerTaxID", "SellerTaxId", "SellerTaxNo", "XSFNSRSBH", "销售方纳税人识别号", "销方税号"]),
    ("amount", ["TotalAmWithoutTax", "TaxExclusiveTotalAmount", "TotalAmountWithoutTax", "HJJE", "合计金额"]),
    ("tax", ["TotalTaxAm", "TaxTotalAmount", "TotalTax", "HJSE", "合计税额"]),
    ("total", ["TotalTax-includedAmount", "TotalTaxIncludedAmount", "TotalAmWithTax", "TaxInclusiveTotalAmount",
               "TotalAmountWithTax", "JSHJ", "价税合计"]),
    ("checkCode", ["CheckCode", "InvoiceCheckCode", "JYM", "校验码"]),
    ("remark", ["Remark", "Note", "BZ", "备注"]),
]
_XML_ITEM = {"name": ["ItemName", "GoodsName", "XMMC", "项目名称"], "spec": ["SpecMod", "Specification", "GGXH", "规格型号"],
             "unit": ["MeaUnits", "Unit", "DW", "单位"], "qty": ["Quantity", "XMSL", "数量"],
             "price": ["UnPrice", "Price", "XMDJ", "单价"], "amount": ["Amount", "XMJE", "金额"],
             "rate": ["TaxRate", "SL", "税率"], "tax": ["ComTaxAm", "TaxAmount", "TaxAm", "SE", "税额"]}
_XML_ITEM_TAGS = ("IssuItemInformation", "IssuItem", "InvoiceItem", "GoodsInfo", "ItemInformation", "Item", "Detail", "明细")


def _xml_rate(v):
    v = _nfkc(v).strip()
    if not v:
        return None
    r = _norm_rate(v)
    if r:
        return r
    try:
        f = float(v)
    except ValueError:
        return v
    if f < 1:
        f *= 100
    return ("%g" % round(f, 2)) + "%"


def extract_xml(data, _virtual=True):
    """数电 XML → [Doc]（一份 XML 一张票）。没有真实样本：按标签本地名宽松匹配，
    假设①金额/税额/价税合计在 TotalAmWithoutTax/TotalTaxAm/TotalTax-includedAmount 这类标签里；
    ②明细在 IssuItemInformation（或 Item/Detail）下，税率可能是 0.06 或 6%；
    ③票种在 GeneralOrSpecialVAT/LabelName 或 InvoiceType 里；认不出票种按 other 并提示核对。"""
    raw = bytes(data or b"").lstrip(b"\xef\xbb\xbf")
    if len(raw) > _XML_MAX_BYTES:
        return [_bad_doc("XML 文件超过 %dMB，不像电子发票，没有读" % (_XML_MAX_BYTES // _MB))]
    root = _xml_root(raw)
    if root is None:
        return [_bad_doc("XML 文件读不出来")]
    els = [(_local(e.tag), (e.text or "").strip(), e) for e in root.iter()]
    d = new_doc(0)
    lower = {}
    for ln, tx, e in els:
        if tx:
            lower.setdefault(ln.lower(), tx)
    for f, names in _XML_MAP:
        for n in names:
            v = lower.get(n.lower())
            if v:
                cv = _coerce(f, v)
                if cv is not None:
                    d[f] = cv
                    break
    if d["amount"] is None:
        v = next((tx for ln, tx, e in els if tx and "withouttax" in ln.lower()), None)
        d["amount"] = _coerce("amount", v) if v else None
    if d["total"] is None:
        v = next((tx for ln, tx, e in els if tx and ("included" in ln.lower() or "withtax" in ln.lower())
                  and "chinese" not in ln.lower() and "without" not in ln.lower()), None)
        d["total"] = _coerce("total", v) if v else None
    if d["total"] is None and d["amount"] is not None and d["tax"] is not None:
        d["total"] = round(d["amount"] + d["tax"], 2)
    lines = []
    for ln, tx, e in els:
        if ln not in _XML_ITEM_TAGS:
            continue
        kids = {}
        for c in e.iter():
            if c is not e and (c.text or "").strip():
                kids.setdefault(_local(c.tag).lower(), c.text.strip())
        item = {}
        for k, names in _XML_ITEM.items():
            item[k] = next((kids[n.lower()] for n in names if n.lower() in kids), None)
        if not item["name"] and item["amount"] is None:
            continue
        nm = _nfkc(item["name"] or "")
        cat = None
        m = re.match(r"^\*([^*]+)\*(.*)$", nm)
        if m:
            cat, nm = m.group(1), m.group(2)
        lines.append({"name": nm, "category": cat, "spec": item["spec"], "unit": item["unit"],
                      "qty": _parse_num(item["qty"]) if item["qty"] else None,
                      "price": _parse_num(item["price"]) if item["price"] else None,
                      "amount": _parse_money(item["amount"], need_dec=False) if item["amount"] else None,
                      "rate": _xml_rate(item["rate"]) if item["rate"] else None,
                      "tax": _parse_money(item["tax"], need_dec=False) if item["tax"] else None})
    d["lines"] = lines
    rates = []
    for l in lines:
        if l["rate"] and l["rate"] not in rates:
            rates.append(l["rate"])
    d["taxRate"] = ",".join(rates) if rates else (_xml_rate(lower.get("taxrate")) if lower.get("taxrate") else None)
    d["category"] = lines[0]["category"] if lines else None
    label = next((tx for ln, tx, e in els if ln in ("LabelName", "InvoiceType", "InvoiceTypeName", "FPLX", "发票类型") and tx), "")
    alltxt = " ".join(tx for ln, tx, e in els if tx)
    it = _type_from_title(_compact(label)) if label else None
    if not it and label in ("普通发票", "02"):
        it = "normal"
    if not it and label in ("增值税专用发票", "专用发票", "01"):
        it = "special"
    if "旅客运输服务" in alltxt and it == "normal":
        it, d["typeLabel"] = "travel", "旅客运输服务"
    if d["number"]:
        d["isInvoice"], d["kind"] = True, "invoice"
        if not it:
            d["warnings"].append("XML 里没认出票种，请核对")
        d["invType"] = it or "other"
        if not d["typeLabel"]:
            d["typeLabel"] = {"special": "电子发票（增值税专用发票）", "normal": "电子发票（普通发票）"}.get(it) or (label or None)
        for f in PENDABLE:
            if d.get(f) not in (None, ""):
                d["fieldSrc"][f] = {"src": "xml", "page": 0, "box": None}
        if _virtual:
            _virtual_src(d, "xml")
        _finish(d)
    else:
        d["warnings"].append("XML 里没读到发票号码")
    return [d]


# 虚拟图片版式（单位 pt，页面 595×397，与数电票面比例一致）；fieldSrc.box 就指向这里
_VW, _VH = 595.0, 397.0
_VBOX = {
    "typeLabel": (150, 12, 445, 44), "number": (452, 24, 590, 38), "date": (452, 42, 590, 56),
    "code": (452, 60, 590, 74), "checkCode": (300, 60, 450, 74),
    "buyerName": (60, 86, 294, 102), "buyerTaxId": (150, 108, 294, 124),
    "sellerName": (345, 86, 578, 102), "sellerTaxId": (433, 108, 578, 124),
    "category": (20, 150, 200, 163), "taxRate": (440, 150, 492, 163),
    "amount": (360, 263, 440, 277), "tax": (495, 263, 578, 277), "total": (430, 283, 578, 298),
    "remark": (45, 304, 578, 338), "qr": (18, 10, 66, 58),
}


def _vb(key):
    b = _VBOX[key]
    return [round(b[0] / _VW, 4), round(b[1] / _VH, 4), round(b[2] / _VW, 4), round(b[3] / _VH, 4)]


def _virtual_src(d, src):
    for f in list(d["fieldSrc"].keys()) + [f for f in PENDABLE if d.get(f) not in (None, "")]:
        if f not in _VBOX:
            continue
        cur = d["fieldSrc"].get(f) or {"src": src, "page": 0}
        cur["box"] = _vb("qr") if cur.get("src") == "qr" else _vb(f)
        cur["page"] = 0
        d["fieldSrc"][f] = cur


def _fmt_money(v):
    return "" if v is None else ("%.2f" % v)


def render_virtual(doc, long_side=1600):
    """OFD/XML 没法直接渲染：按 Doc 字段画一张干净的"虚拟图片"（PyMuPDF 新建页＋中文字体 china-s），
    版式与 fieldSrc.box 对应。→ JPEG 字节；缺 PyMuPDF 返回 b""。"""
    fitz = _fitz()
    if fitz is None:
        return b""
    pdf = fitz.open()
    try:
        pg = pdf.new_page(width=_VW, height=_VH)
        ink, line = (0.55, 0.25, 0.15), (0.72, 0.45, 0.35)
        font = "china-s"

        def text(x, y, s, size=9.0, color=ink, maxw=None):
            s = _nfkc(s or "")
            if not s:
                return
            # 纯 ASCII（号码、税号、金额）用 helv：china-s 的数字是全角宽，20 位号码会挤出格子
            ascii_only = all(ord(ch) < 128 for ch in s)
            if maxw:
                est = (0.56 * len(s) if ascii_only else float(len(s))) * size
                if est > maxw:
                    size = max(5.0, size * maxw / est)
            pg.insert_text((x, y), s, fontname="helv" if ascii_only else font, fontsize=size, color=color)

        title = doc.get("typeLabel") or "电子发票"
        if doc.get("typeLabel") in _SPECIAL_LABELS:
            title = {"special": "电子发票（增值税专用发票）", "travel": "电子发票（普通发票）"}.get(doc.get("invType"), "电子发票（普通发票）")
            text(80, 56, doc["typeLabel"], 9)
        text(160, 36, title, 16, maxw=280)
        pg.draw_line((190, 42), (410, 42), color=line, width=0.8)
        text(400, 34, "发票号码：", 9)
        text(452, 34, doc.get("number") or "", 9)
        text(400, 52, "开票日期：", 9)
        text(452, 52, (doc.get("date") or "").replace("-", "年", 1).replace("-", "月", 1) + ("日" if doc.get("date") else ""), 9)
        if doc.get("code"):
            text(400, 70, "发票代码：", 9)
            text(452, 70, doc["code"], 9)
        if doc.get("checkCode"):
            text(250, 70, "校验码：", 8)
            text(300, 70, doc["checkCode"], 8, maxw=150)
        pg.draw_rect(fitz.Rect(15, 78, 580, 340), color=line, width=0.8)
        pg.draw_line((15, 130), (580, 130), color=line, width=0.6)
        pg.draw_line((297, 78), (297, 130), color=line, width=0.6)
        for i, ch in enumerate("购买方信息"):
            text(20, 90 + i * 9.5, ch, 8)
        for i, ch in enumerate("销售方信息"):
            text(302, 90 + i * 9.5, ch, 8)
        text(34, 98, "名称：", 9)
        text(60, 98, doc.get("buyerName") or "", 9, maxw=232)
        text(34, 120, "统一社会信用代码/纳税人识别号：", 7)
        text(150, 120, doc.get("buyerTaxId") or "", 9, maxw=142)
        text(318, 98, "名称：", 9)
        text(345, 98, doc.get("sellerName") or "", 9, maxw=232)
        text(318, 120, "统一社会信用代码/纳税人识别号：", 7)
        text(433, 120, doc.get("sellerTaxId") or "", 9, maxw=142)
        cols = [(20, "项目名称"), (205, "规格型号"), (255, "单位"), (285, "数量"), (320, "单价"),
                (380, "金额"), (445, "税率/征收率"), (520, "税额")]
        for x, s in cols:
            text(x, 143, s, 8)
        pg.draw_line((15, 147), (580, 147), color=line, width=0.4)
        lines = doc.get("lines") or []
        y = 160
        for ln in lines[:7]:
            nm = ("*%s*" % ln["category"] if ln.get("category") else "") + (ln.get("name") or "")
            text(20, y, nm, 8, maxw=182)
            text(205, y, ln.get("spec") or "", 7, maxw=48)
            text(255, y, ln.get("unit") or "", 8, maxw=28)
            text(285, y, "" if ln.get("qty") is None else ("%g" % ln["qty"]), 8, maxw=33)
            text(320, y, "" if ln.get("price") is None else ("%g" % ln["price"]), 8, maxw=58)
            text(372, y, _fmt_money(ln.get("amount")), 8, maxw=66)
            text(450, y, ln.get("rate") or "", 8, maxw=45)
            text(505, y, _fmt_money(ln.get("tax")), 8, maxw=72)
            y += 14
        if len(lines) > 7:
            text(20, y, "……共 %d 行明细，其余见原件" % len(lines), 8, color=(0.4, 0.4, 0.4))
        pg.draw_line((15, 258), (580, 258), color=line, width=0.4)
        text(60, 273, "合        计", 9)
        text(362, 273, ("¥" + _fmt_money(doc.get("amount"))) if doc.get("amount") is not None else "", 9)
        text(497, 273, ("¥" + _fmt_money(doc.get("tax"))) if doc.get("tax") is not None else "", 9)
        pg.draw_line((15, 280), (580, 280), color=line, width=0.6)
        text(40, 294, "价税合计（大写）", 9)
        text(390, 294, "（小写）", 9)
        text(432, 294, ("¥" + _fmt_money(doc.get("total"))) if doc.get("total") is not None else "", 10)
        pg.draw_line((15, 300), (580, 300), color=line, width=0.6)
        text(20, 316, "备", 9)
        text(20, 332, "注", 9)
        rm = doc.get("remark") or ""
        text(45, 316, rm[:60], 8, maxw=530)
        if len(rm) > 60:
            text(45, 330, rm[60:120], 8, maxw=530)
        text(20, 360, "示意图：按电子原件（OFD/XML）内容生成，仅供查看，以原件为准", 7.5, color=(0.45, 0.45, 0.45))
        if doc.get("qrRaw") and _cv2():
            try:
                cv2, np = _cv2()
                enc = cv2.QRCodeEncoder.create() if hasattr(cv2.QRCodeEncoder, "create") else cv2.QRCodeEncoder()
                q = enc.encode(doc["qrRaw"])
                q = cv2.resize(q, None, fx=6, fy=6, interpolation=cv2.INTER_NEAREST)
                ok, png = cv2.imencode(".png", q)
                if ok:
                    b = _VBOX["qr"]
                    pg.insert_image(fitz.Rect(b[0], b[1], b[2], b[3]), stream=png.tobytes())
            except Exception:
                pass
        z = _safe_zoom(pg.rect, long_side / _VW)
        pix = pg.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
        return _pix_jpeg(pix, 85)
    finally:
        pdf.close()


# ---------------------------------------------------------------------------
# 10. 照片：第一拨读码 / 第二拨 OCR
# ---------------------------------------------------------------------------
class OCRUnavailable(RuntimeError):
    """服务器没有离线识别组件（rapidocr_onnxruntime / numpy / Pillow）。"""


_OCR = None
_OCR_LOCK = threading.Lock()


def _ocr_engine():
    # 调用方已持有 _OCR_LOCK：RapidOCR 单例不保证线程安全，初始化和识别都串行
    global _OCR
    if _OCR is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except Exception:
            raise OCRUnavailable("服务器缺少离线识别组件（rapidocr_onnxruntime），没法识别图片文字")
        _OCR = RapidOCR()
    return _OCR


def ocr_tokens(image_bytes, long_side=1800, rotate=0):
    """图片 → (tokens, 宽, 高)：tokens=[{"text", "box":[x0,y0,x1,y1], "score"}]，坐标按
    （EXIF 摆正、缩到长边 ≤ long_side、再顺时针转 rotate 度后）的图片像素。缺组件抛 OCRUnavailable。"""
    try:
        import numpy as np
        Image, _ = _pil()
    except Exception:
        raise OCRUnavailable("服务器缺少图像组件（numpy/Pillow），没法识别图片文字")
    im, _ = _open_image(image_bytes, draft_side=long_side)
    im = _shrink(_to_rgb(im), long_side)
    if max(im.size) < 1000:
        f = 1400.0 / max(im.size)
        im = im.resize((int(im.size[0] * f), int(im.size[1] * f)), _resample(Image, "BICUBIC"))
    rot = int(rotate or 0) % 360
    if rot:
        tp = {90: "ROTATE_270", 180: "ROTATE_180", 270: "ROTATE_90"}[rot]
        im = im.transpose(getattr(getattr(Image, "Transpose", Image), tp))
    arr = np.ascontiguousarray(np.asarray(im)[:, :, ::-1])
    with _OCR_LOCK:
        eng = _ocr_engine()
        res, _ = eng(arr)
    toks = []
    for item in res or []:
        try:
            pts, txt, sc = item[0], item[1], item[2]
            xs = [float(p[0]) for p in pts]
            ys = [float(p[1]) for p in pts]
            toks.append({"text": str(txt), "box": [min(xs), min(ys), max(xs), max(ys)], "score": float(sc),
                         "poly": [[round(float(p[0]), 1), round(float(p[1]), 1)] for p in pts]})
        except Exception:
            continue
    return toks, im.size[0], im.size[1]


def extract_image_fast(data):
    """照片第一拨：只读二维码（毫秒级）。发票码 → 号码/日期/金额（fieldSrc.src=qr，box=二维码位置），
    needOcr=True（销方、税额等要第二拨补）；只有钉钉审批码 → approvalLink＋kind=other；
    什么码都没有 → kind=other、needOcr=True（可能是无码票，交第二拨判断）。图片打不开 → warnings。"""
    d = new_doc(0)
    if not _cv2():
        d["needOcr"] = True
        d["warnings"].append("服务器缺少图像组件（opencv），读不了二维码")
        return d
    try:
        gray, W, H = _load_gray(data, 2000)
    except ValueError as e:
        d["warnings"].append(str(e))
        return d
    hits = _qr_scan(gray, budget=0.65)
    inv = [h for h in hits if parse_invoice_qr(h[0])]
    if inv:
        t, bx, _ = inv[0]
        d = _doc_from_qr(t, _nb(bx, W, H), 0)
        others = set(parse_invoice_qr(h[0])["number"] for h in inv[1:]) - {d["number"]}
        if others:
            d["warnings"].append("照片里还有 %d 张发票的二维码，这里只登记了第一张，请分开拍" % len(others))
        d["needOcr"] = any(d.get(f) in (None, "") for f in PENDABLE if f not in ("number", "date", "total", "code", "checkCode", "remark"))
        return d
    link = next((h[0] for h in hits if classify_code(h[0])["kind"] == "approval_link"), None)
    if link:
        d["approvalLink"] = link
        return d
    d["needOcr"] = True
    return d


def _rot_box_fwd(b, rot):
    """原图（EXIF 摆正后）0..1 框 → 顺时针转 rot 度后的图上的 0..1 框。"""
    if not b or not rot:
        return b
    pts = []
    for x, y in ((b[0], b[1]), (b[2], b[3])):
        if rot == 90:
            pts.append((1 - y, x))
        elif rot == 180:
            pts.append((1 - x, 1 - y))
        else:
            pts.append((y, 1 - x))
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def _rot_box_back(b, rot):
    """OCR 在转正后的图上做的：把 0..1 框换回原图（EXIF 摆正后）坐标。rot=原图顺时针转了几度。"""
    if not b or not rot:
        return b
    u0, v0, u1, v1 = b
    pts = []
    for u, v in ((u0, v0), (u1, v1)):
        if rot == 90:
            pts.append((v, 1 - u))
        elif rot == 180:
            pts.append((1 - u, 1 - v))
        else:
            pts.append((1 - v, u))
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [round(min(xs), 4), round(min(ys), 4), round(max(xs), 4), round(max(ys), 4)]


def extract_image_ocr(data, qr_doc=None):
    """照片第二拨：离线 OCR → 按位置解析（src=ocr），再与第一拨（qr_doc）合并：
    二维码/原件来的字段不覆盖；只从 OCR 来的字段一律列进 pending（待人核）；
    OCR 读到的号码/日期/金额与二维码不一致 → warnings。照片是横着/倒着的，按二维码方向先转正再识别。
    qr_doc 可传 extract_image_fast 的结果，或扫描版 PDF 那一页的 Doc（data 则传 render_pdf 渲染的该页 JPEG，
    fieldSrc.page 沿用 qr_doc.page）；不传时图里有发票码就自己读码为准。一张图里拼了几张票时，只解析
    与 qr_doc 二维码对应的那一块。缺识别组件抛 OCRUnavailable（调用方把识别状态记为 failed）。"""
    page = (qr_doc or {}).get("page") or 0
    rot, qrs = _qr_layout(data) if (qr_doc is None or qr_doc.get("qrRaw")) else (0, [])
    multi = _distinct_invoice_qrs([q for q in qrs if q[1]])
    if qr_doc is None and multi:
        qr_doc = _doc_from_qr(multi[0][0], multi[0][1], 0)   # 没给第一拨结果：图里有发票码就以码为准
    toks, w, h = ocr_tokens(data, rotate=rot)
    f = None
    if len(multi) >= 2:
        # 一张图里拼了几张票：只解析二维码对得上的那一块
        mine = (parse_invoice_qr((qr_doc or {}).get("qrRaw") or "") or {}).get("number")
        qpx = []
        for t, b in multi:
            u = _rot_box_fwd(b, rot)
            qpx.append((t, [u[0] * w, u[1] * h, u[2] * w, u[3] * h]))
        for t, fd in _parse_regions(toks, w, h, "ocr", page, qpx):
            if parse_invoice_qr(t)["number"] == mine:
                f = fd
                break
    extra = []
    no_qr = not multi and not (qr_doc and qr_doc.get("isInvoice"))
    if f is None and no_qr:
        # 没有发票二维码：可能是一张照片拍了几张老式票（出租车、过路费…），按票拆开
        olds = split_old_tickets(toks, w, h, page)
        if olds:
            f, extra = olds[0], olds[1:]
    if f is None:
        f = fields_from_tokens(toks, w, h, "ocr", page=page)
        if no_qr:
            f = _supplement_old(f, toks, w, h, page)
    if rot:
        for x in [f] + extra:
            for k, v in x["fieldSrc"].items():
                v["box"] = _rot_box_back(v.get("box"), rot)
            if x.get("region"):
                x["region"] = _rot_box_back(x["region"], rot)
    if qr_doc and qr_doc.get("isInvoice"):
        d = dict(qr_doc)
        d["fieldSrc"] = dict(qr_doc.get("fieldSrc") or {})
        d["pending"] = list(qr_doc.get("pending") or [])
        d["warnings"] = list(qr_doc.get("warnings") or [])
        d["lines"] = list(qr_doc.get("lines") or [])
    else:
        d = new_doc((qr_doc or {}).get("page") or 0)
        if qr_doc:
            d["approvalLink"] = qr_doc.get("approvalLink")
            d["warnings"] = list(qr_doc.get("warnings") or [])
    page = d.get("page") or 0
    for k in PENDABLE:
        ov = f.get(k)
        bv = d.get(k)
        if bv not in (None, ""):
            if ov not in (None, "") and k in ("number", "date", "total", "amount", "code") and not _same_val(k, bv, ov):
                d["warnings"].append("识别出的%s与二维码不一致：识别 %s，二维码 %s（以二维码为准）" % (FIELD_LABELS.get(k, k), ov, bv))
            continue
        if ov in (None, ""):
            continue
        d[k] = ov
        src = dict(f["fieldSrc"].get(k) or {"src": "ocr", "box": None})
        src["page"] = page
        d["fieldSrc"][k] = src
        if k not in d["pending"]:
            d["pending"].append(k)
    if not d["lines"] and f["lines"]:
        d["lines"] = f["lines"]
    if f.get("typeLabel") in _SPECIAL_LABELS:
        d["typeLabel"] = f["typeLabel"]
        if f["typeLabel"] == "旅客运输服务" and d.get("invType") == "normal":
            d["invType"] = "travel"
    if not d.get("invType") and f.get("invType"):
        d["invType"] = f["invType"]
    if not d.get("typeLabel") and f.get("typeLabel"):
        d["typeLabel"] = f["typeLabel"]
    if not d["isInvoice"]:
        d["kind"], d["isInvoice"] = f["kind"], f["isInvoice"]
        if d["isInvoice"] and not d.get("invType"):
            d["invType"] = "other"
    if d["isInvoice"] and d.get("qrRaw"):
        q = parse_invoice_qr(d["qrRaw"])
        if q and q["total"] is not None and f.get("amount") is not None and f.get("tax") is not None \
                and abs(f["amount"] + f["tax"] - q["total"]) > 0.011:
            d["warnings"].append("识别出的金额+税额与二维码价税合计对不上，请核对金额和税额")
    for k in ("buyerTaxId", "sellerTaxId"):
        v = d.get(k)
        if v and k in d["pending"] and len(v) == 18 and not uscc_ok(v):
            d["warnings"].append("识别出的%s校验位不对，可能认错了字，请核对" % FIELD_LABELS[k])
    for wmsg in f["warnings"]:
        if d.get("qrType") and wmsg.startswith("票种没认全"):
            continue   # 票种以二维码为准，标题认没认全无所谓
        if wmsg not in d["warnings"]:
            d["warnings"].append(wmsg)
    d["needOcr"] = False
    if f.get("region"):
        d["region"] = f["region"]
    if extra:
        for x in extra:
            x["needOcr"] = False
            x["page"] = page
            for v in x["fieldSrc"].values():
                v["page"] = page
            _finish(x)
        d["extraDocs"] = extra
    return _finish(d)


# ---------------------------------------------------------------------------
# 11. 可否抵扣建议 / 校验
# ---------------------------------------------------------------------------
_NO_DEDUCT_CATS = ("餐饮服务", "居民日常服务", "娱乐服务", "贷款服务")


def deduct_suggest(doc):
    """系统建议（会计最终定）→ ("yes"|"no"|"", 原因)。非发票 → ("", "")。
    抬头不是本公司的情况由调用方结合 validate() 结果另判。"""
    if not doc or not doc.get("isInvoice"):
        return "", ""
    it = doc.get("invType") or ""
    label = doc.get("typeLabel") or ""
    rate = doc.get("taxRate") or ""
    tax = doc.get("tax")
    lines = doc.get("lines") or []
    cats = [((l.get("category") or ""), (l.get("name") or "")) for l in lines]
    if doc.get("category") and not cats:
        cats = [(doc["category"], "")]
    # 老式票先判：名字里也带"专用发票"，票面没有税额，一律只作报销凭证
    if it == "taxi":
        return "no", "出租车票（纸质）没有乘客身份信息，不能计算抵扣，只作报销凭证"
    if it == "tollpaper":
        return "no", "纸质过路（过桥）费发票不能抵扣（能抵的是通行费电子普通发票），只作报销凭证"
    if it == "quota":
        return "no", "定额发票，不能抵扣"
    if (tax is not None and abs(float(tax)) < 0.005) or (rate and all(r.strip() in ("免税", "不征税", "***", "0%") for r in rate.split(","))):
        return "no", "税额为 0（免税/不征税），没有进项税可抵"
    if it == "special":
        for c, _ in cats:
            for bad in _NO_DEDUCT_CATS:
                if bad in c:
                    return "no", "不得抵扣类别：" + bad
        return "yes", "增值税专用发票，可抵扣"
    if it == "vehicle":
        if "二手车" in label:
            return "no", "二手车销售统一发票，不能抵扣"
        return "yes", "机动车销售统一发票，可抵扣"
    if it == "train":
        return "yes", "铁路电子客票，可计算抵扣（限本单位员工出行）"
    if it == "flight":
        return "yes", "航空运输电子客票行程单，可计算抵扣（限本单位员工出行）"
    travel = it == "travel" or label == "旅客运输服务" or any(("运输服务" in c and "客运" in (c + n)) for c, n in cats)
    if travel:
        return "yes", "旅客运输服务，可计算抵扣（限本单位员工出行）"
    if it == "toll" or "通行费" in label or any("通行费" in (c + n) for c, n in cats):
        return "yes", "通行费电子发票，可抵扣"
    return "no", "普通发票，不能抵扣"


def validate(doc, companies=None, expected_buyer=None, payee=None):
    """票面校验 → flags（只含命中的项）：
    sellerMismatch{seller,payee}：销方≠付款单收款方；buyerMismatch{buyer,expected}：抬头≠票夹公司主体；
    buyerNotCompany：抬头不在本公司清单（名称或税号都对不上）；sumMismatch{amount,tax,total}；
    qrMismatch[字段]：票面与二维码不一致。名称比较一律 normalize_name。"""
    flags = {}
    if not doc or not doc.get("isInvoice"):
        return flags
    seller, buyer = doc.get("sellerName"), doc.get("buyerName")
    if payee and seller and normalize_name(seller) != normalize_name(payee):
        flags["sellerMismatch"] = {"seller": seller, "payee": payee}
    if expected_buyer and buyer and normalize_name(buyer) != normalize_name(expected_buyer):
        flags["buyerMismatch"] = {"buyer": buyer, "expected": expected_buyer}
    if companies and (buyer or doc.get("buyerTaxId")):
        nb = normalize_name(buyer or "")
        tid = (doc.get("buyerTaxId") or "").upper()
        hit = False
        for c in companies:
            if (c.get("name") and normalize_name(c["name"]) == nb) or (tid and (c.get("taxId") or "").upper() == tid):
                hit = True
                break
        if not hit:
            flags["buyerNotCompany"] = True
    a, t, s = doc.get("amount"), doc.get("tax"), doc.get("total")
    if a is not None and t is not None and s is not None and abs(float(a) + float(t) - float(s)) > 0.011:
        flags["sumMismatch"] = {"amount": a, "tax": t, "total": s}
    q = parse_invoice_qr(doc.get("qrRaw") or "")
    if q:
        bad = []
        for f in ("number", "date", "code", "total", "amount"):
            if q.get(f) not in (None, "") and doc.get(f) not in (None, "") and not _same_val(f, doc[f], q[f]):
                bad.append(f)
        if bad:
            flags["qrMismatch"] = bad
    return flags
