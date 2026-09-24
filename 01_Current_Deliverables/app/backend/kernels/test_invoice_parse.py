# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家）
# Description: 发票管家·票面解析内核单测。全部用合成夹具（PyMuPDF 现画的数电票/老版票、cv2 现生成的
#   二维码、现拼的压缩包/OFD/XML），不碰真实发票。真实样本回归只在设置环境变量 INV_SAMPLES_DIR
#   （指向本机样本目录，样本不入库）时才跑。
#   审查修复（同日）：TestDecodeCaps——压缩炸弹/超大像素/超大页面/UTF-16 绕 DTD/NaN 金额与坐标的回归用例。
#   运行：cd backend && python -m unittest kernels.test_invoice_parse -v
import io
import os
import re
import json
import time
import zlib
import base64
import struct
import zipfile
import random
import unittest
from unittest.mock import patch

from kernels import invoice_parse as ip


def _has(mod):
    try:
        __import__(mod)
        return True
    except Exception:
        return False


HAS_FITZ = _has("fitz")
HAS_CV2 = _has("cv2") and _has("numpy")
HAS_PIL = _has("PIL")
HAS_OCR = _has("rapidocr_onnxruntime")
USCC_PAT = re.compile(r"^[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}$")


def _uscc(base17):
    """给 17 位前缀补上正确的校验位（合成税号，不对应任何真实主体）。"""
    for ch in "0123456789ABCDEFGHJKLMNPQRTUWXY":
        if ip.uscc_ok(base17 + ch):
            return base17 + ch
    raise ValueError(base17)


FAKE = {
    "number": "26440000000012345678", "date": "2026-09-01", "date_cn": "2026年09月01日",
    "buyer": "测试甲方科技有限公司", "buyer_tid": _uscc("91110000MA00ABCD1"),
    "seller": "示例乙方服务有限公司", "seller_tid": _uscc("91310000MA11EFGH2"),
    "amount": 1200.00, "tax": 72.00, "total": 1272.00, "upper": "壹仟贰佰柒拾贰圆整",
    "remark": "合同号HT-0001 测试备注",
}
FAKE_QR = "01,31,,%s,%.2f,%s,,ABCD" % (FAKE["number"], FAKE["total"], FAKE["date"].replace("-", ""))
# 第二张（A4 拼两张用）：号码、销方不同
FAKE2 = dict(FAKE, number="26440000000087654321", seller="样例丙方贸易有限公司", seller_tid=_uscc("91440300MA22JKLM3"),
             remark="第二张 测试")
FAKE2_QR = "01,31,,%s,%.2f,%s,,BCDE" % (FAKE2["number"], FAKE2["total"], FAKE2["date"].replace("-", ""))


def _qr_png(text, module=6):
    import cv2
    enc = cv2.QRCodeEncoder.create() if hasattr(cv2.QRCodeEncoder, "create") else cv2.QRCodeEncoder()
    q = enc.encode(text)
    q = cv2.resize(q, None, fx=module, fy=module, interpolation=cv2.INTER_NEAREST)
    q = cv2.copyMakeBorder(q, 2 * module, 2 * module, 2 * module, 2 * module, cv2.BORDER_CONSTANT, value=255)
    ok, png = cv2.imencode(".png", q)
    return png.tobytes()


def _page_items_digital(style="split", title="电子发票（增值税专用发票）", special=None, rate="6%",
                        tax1="60.00", tax2="12.00", tax_total="72.00", v=None):
    """数电票版式（595×397pt，坐标仿实物）→ [(x, 基线y, 文字, 字号)]。"""
    v = v or FAKE
    it = []

    def T(x, y, s, size=9.0):
        it.append((x, y, s, size))
    T(170, 40, title, 20)
    if special:
        T(94, 50, special, 9)
    if style == "split":
        T(438, 40, "发票号码：", 9.4)
        T(482, 40, v["number"], 9)
        T(438, 57, "开票日期：", 9.4)
        T(482, 57, v["date_cn"], 9)
    else:
        T(440, 37, "发票号码：" + v["number"], 9)
        T(440, 53, "开票日期：" + v["date_cn"], 9)
    for i, ch in enumerate("购买方信息"):
        T(17, 101 + i * 9.9, ch, 8.5)
    for i, ch in enumerate("销售方信息"):
        T(302, 101 + i * 9.9, ch, 8.5)
    if style == "split":
        T(33, 104, "名称：")
        T(57, 104, v["buyer"])
        T(318, 104, "名称：")
        T(341, 104, v["seller"])
        T(33, 134, "统一社会信用代码/纳税人识别号：", 8.5)
        T(154, 135, v["buyer_tid"], 11)
        T(318, 134, "统一社会信用代码/纳税人识别号：", 8.5)
        T(439, 135, v["seller_tid"], 11)
    else:
        T(32, 108, "名称：" + v["buyer"])
        T(317, 108, "名称：" + v["seller"])
        T(32, 133, "统一社会信用代码/纳税人识别号：" + v["buyer_tid"], 8.5)
        T(317, 133, "统一社会信用代码/纳税人识别号：" + v["seller_tid"], 8.5)
    for x, s in ((45, "项目名称"), (119, "规格型号"), (190, "单 位"), (264, "数 量"), (335, "单 价"),
                 (407, "金 额"), (447, "税率/征收率"), (551, "税 额")):
        T(x, 159, s)
    # 第一行明细故意折行（实物常见），第二行单独一行
    for x, s in ((13, "*信息技术服务*软件维护"), (125, "V1"), (198, "项"), (286, "1"), (340, "1000"),
                 (402, "1000.00"), (467, rate), (556, tax1)):
        T(x, 168, s)
    T(13, 181, "服务费")
    for x, s in ((13, "*现代服务*咨询费"), (198, "次"), (286, "2"), (345, "100"), (407, "200.00"),
                 (467, rate), (556, tax2)):
        T(x, 194, s)
    T(58, 269, "合")
    T(103, 269, "计")
    T(397, 270, "¥", 11)
    T(404, 269, "1200.00")
    if tax_total:
        T(549, 270, "¥", 11)
        T(556, 269, tax_total)
    T(48, 288, "价税合计（大写）")
    T(178, 287, v["upper"])
    T(407, 288, "（小写）")
    T(441, 288, "¥", 11)
    T(448, 287, "%.2f" % v["total"], 11)
    T(17, 318, "备")
    T(17, 335, "注")
    T(32, 304, v["remark"])
    T(55, 376, "开票人：")
    T(91, 376, "测试员")
    return it


_CJK_FONT = []


def _font():
    # PyMuPDF 自带的 Droid Sans Fallback：中文＋比例宽度的数字（内置 china-s 的数字是全角宽，
    # 20 位票号会画出页面），和真实票面的字宽接近
    import fitz
    if not _CJK_FONT:
        _CJK_FONT.append(fitz.Font("cjk"))
    return _CJK_FONT[0]


def _put(pg, items):
    import fitz
    tw = fitz.TextWriter(pg.rect)
    for x, y, s, fs in items:
        tw.append((x, y), s, font=_font(), fontsize=fs)   # 每次 append 是一个独立文字对象
    tw.write_text(pg)


def build_pdf(items, qr=FAKE_QR, size=(595, 397), qr_rect=(18, 17, 75, 74), shuffle=True, extra_pages=()):
    """把文字块画成 PDF（中文字体）；shuffle=True 打乱写入顺序，专门考"按位置不按顺序"。"""
    import fitz
    doc = fitz.open()
    pg = doc.new_page(width=size[0], height=size[1])
    items = list(items)
    if shuffle:
        random.Random(7).shuffle(items)
    _put(pg, items)
    pg.draw_rect(fitz.Rect(10, 85, 585, 360), color=(0.6, 0.35, 0.25), width=0.6)
    if qr:
        pg.insert_image(fitz.Rect(*qr_rect), stream=_qr_png(qr))
    for extra in extra_pages:
        p2 = doc.new_page(width=595, height=842)
        _put(p2, extra)
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out


def build_old_items():
    """老版增值税电子专用发票版式：购买方在上、销售方在下，右上是密码区（里面放了个像税号的陷阱串）。"""
    it = []

    def T(x, y, s, size=9.0):
        it.append((x, y, s, size))
    T(190, 38, "增值税电子专用发票", 18)
    T(420, 40, "发票代码：")
    T(470, 40, "044032000111")
    T(420, 54, "发票号码：")
    T(470, 54, "12345678")
    T(420, 68, "开票日期：")
    T(470, 68, "2020年01月01日")
    for i, ch in enumerate("购买方"):
        T(20, 110 + i * 12, ch)
    T(35, 112, "名称：")
    T(75, 112, FAKE["buyer"])
    T(35, 126, "纳税人识别号：")
    T(105, 126, FAKE["buyer_tid"])
    T(35, 140, "地址、电话：测试路1号 0755-00000000", 8)
    for i, ch in enumerate("密码区"):
        T(350, 110 + i * 12, ch)
    T(365, 112, "03*4<>/+-*<<9876543210ABCDEFGH>>", 8)
    T(365, 126, "+-<>*/0123<>*/45+-67", 8)
    for x, s in ((25, "货物或应税劳务、服务名称"), (170, "规格型号"), (225, "单位"), (260, "数量"),
                 (300, "单价"), (370, "金额"), (430, "税率"), (500, "税额")):
        T(x, 170, s)
    for x, s in ((25, "*餐饮服务*餐费"), (228, "次"), (265, "1"), (300, "100"), (370, "100.00"),
                 (432, "6%"), (500, "6.00")):
        T(x, 185, s)
    T(60, 240, "合　　计")
    T(362, 240, "¥100.00")
    T(495, 240, "¥6.00")
    T(30, 258, "价税合计（大写）")
    T(140, 258, "⊗壹佰零陆圆整")
    T(400, 258, "（小写）¥106.00")
    for i, ch in enumerate("销售方"):
        T(20, 282 + i * 12, ch)
    T(35, 284, "名称：")
    T(75, 284, FAKE["seller"])
    T(35, 298, "纳税人识别号：")
    T(105, 298, FAKE["seller_tid"])
    T(35, 312, "开户行及账号：测试银行 6222000000000000", 8)
    T(350, 288, "备")
    T(350, 305, "注")
    T(365, 290, "测试订单 A-01")
    T(40, 350, "收款人：")
    T(200, 350, "复核：")
    T(320, 350, "开票人：测试员")
    return it


OLD_QR = "01,08,044032000111,12345678,100.00,20200101,,1A2B"


def build_2up_pdf(scanned=False):
    """A4 上下拼两张数电票（报销时常见的打印/扫描方式）；scanned=True 时整页只是一张图。"""
    import fitz
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    for v, qr, dy in ((FAKE, FAKE_QR, 20), (FAKE2, FAKE2_QR, 440)):
        _put(pg, [(x, y + dy, s, fs) for x, y, s, fs in _page_items_digital("split", v=v)])
        pg.insert_image(fitz.Rect(18, 17 + dy, 75, 74 + dy), stream=_qr_png(qr))
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    if not scanned:
        return out
    jpg, _ = _render_jpeg(out, zoom=2.5)
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    pg.insert_image(pg.rect, stream=jpg)
    out = doc.tobytes()
    doc.close()
    return out


def _render_jpeg(pdf_bytes, zoom=3.0):
    import fitz
    d = fitz.open(stream=pdf_bytes, filetype="pdf")
    pix = d[0].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    from PIL import Image
    im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    d.close()
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return buf.getvalue(), im.size


def _degraded_photo(pdf_bytes, size=(4000, 3000), rot=0):
    """模拟手机/高拍仪照片：放到桌面底色上、透视变形＋倾斜、加噪声、JPEG q60。"""
    import cv2
    import numpy as np
    import fitz
    d = fitz.open(stream=pdf_bytes, filetype="pdf")
    pix = d[0].get_pixmap(matrix=fitz.Matrix(5.5, 5.5), alpha=False)
    d.close()
    inv = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)[:, :, ::-1].copy()
    W, H = size
    s = (W * 0.82) / inv.shape[1]
    inv = cv2.resize(inv, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    h, w = inv.shape[:2]
    ox, oy = int(W * 0.08), int((H - h) / 2)
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[ox + 60, oy + 20], [ox + w - 10, oy + 130], [ox + w + 30, oy + h + 50], [ox - 20, oy + h - 30]])
    M = cv2.getPerspectiveTransform(src, dst)
    canvas = np.full((H, W, 3), (95, 110, 125), np.uint8)
    warped = cv2.warpPerspective(inv, M, (W, H))
    mask = cv2.warpPerspective(np.full((h, w), 255, np.uint8), M, (W, H))
    canvas[mask > 0] = warped[mask > 0]
    rng = np.random.RandomState(3)
    canvas = (canvas.astype(np.int16) + rng.randint(-16, 17, canvas.shape)).clip(0, 255).astype(np.uint8)
    if rot:
        canvas = np.ascontiguousarray(np.rot90(canvas, {90: -1, 180: 2, 270: 1}[rot]))
    ok, jpg = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 60])
    return jpg.tobytes()


def _inside(box):
    return box and len(box) == 4 and all(0.0 <= v <= 1.0 for v in box) and box[0] < box[2] and box[1] < box[3]


# ---------------------------------------------------------------------------
class TestCodes(unittest.TestCase):
    def test_classify(self):
        c = ip.classify_code
        self.assertEqual(c("https://aflow.dingtalk.com/qr/AbCdEf1234")["kind"], "approval_link")
        self.assertEqual(c(" https：//aflow.dingtalk.com/qr/X9 \r\n")["kind"], "approval_link")
        self.assertEqual(c("https://x.example.com/p?procInstId=abc-123&corpid=d")["kind"], "approval_link")
        r = c(" 202609011200000123456 ")
        self.assertEqual(r, {"kind": "business_id", "value": "202609011200000123456"})
        self.assertEqual(c("20260901120000012345")["kind"], "business_id")   # 20 位
        self.assertEqual(c("202613011200000123456")["kind"], "unknown")      # 13 月
        self.assertEqual(c("26990000000000000001")["kind"], "unknown")       # 数电发票号码不是审批编号
        self.assertEqual(c("01,32,,26440000000000000001,10.00,20260901,,")["kind"], "invoice_qr")
        self.assertEqual(c("01，32，，26440000000000000001，10.00，20260901，，")["kind"], "invoice_qr")
        self.assertEqual(c("https://v.example.cn/p/abc")["kind"], "url")
        self.assertEqual(c("hello")["kind"], "unknown")
        self.assertEqual(c("")["kind"], "unknown")

    def test_parse_qr_digital(self):
        q = ip.parse_invoice_qr("01,31,,26440000000012345678,1288.60,20260901,,AB12")
        self.assertEqual((q["qrType"], q["number"], q["date"], q["total"], q["amount"], q["code"]),
                         ("31", "26440000000012345678", "2026-09-01", 1288.6, None, None))
        q = ip.parse_invoice_qr("01,32, ,26440000000012345679,66.60,20260902, ,9A9B")
        self.assertEqual((q["number"], q["total"], q["checkCode"]), ("26440000000012345679", 66.6, None))
        q = ip.parse_invoice_qr("01,32,,26440000000012345670,58.80,2026-09-03,,")
        self.assertEqual((q["date"], q["total"]), ("2026-09-03", 58.8))
        q = ip.parse_invoice_qr("01，32，，26440000000012345670，58.80，20260903，，")
        self.assertEqual(q["total"], 58.8)
        self.assertEqual(ip.parse_invoice_qr("01,32,,26440000000012345670,58.80,20260903,,")["raw"],
                         "01,32,,26440000000012345670,58.80,20260903,,")

    def test_parse_qr_old(self):
        q = ip.parse_invoice_qr("01,01,4400194130,01234567,1000.00,20200105,,A1B2")
        self.assertEqual((q["qrType"], q["code"], q["number"], q["amount"], q["total"], q["checkCode"]),
                         ("01", "4400194130", "01234567", 1000.0, None, None))
        q = ip.parse_invoice_qr("01,04,044001900111,76543210,88.50,20191231,12345678901234567890,C3D4")
        self.assertEqual((q["code"], q["amount"], q["checkCode"], q["date"]),
                         ("044001900111", 88.5, "12345678901234567890", "2019-12-31"))
        for t in ("10", "11", "14", "15"):
            q = ip.parse_invoice_qr("01,%s,044031900111,12345678,20.00,20210101,98765432109876543210,EE" % t)
            self.assertEqual((q["qrType"], q["amount"]), (t, 20.0))

    def test_parse_qr_rejects(self):
        for s in ("https://aflow.dingtalk.com/qr/x", "02,31,,26440000000012345678,1.00,20260901,,", "01,31",
                  "01,31,,2644000000001234567,1.00,20260901,,", "01,31,,26440000000012345678,abc,20260901,,",
                  "01,31,,26440000000012345678,1.00,20261399,,", "01,31,123,26440000000012345678,1.00,20260901,,", ""):
            self.assertIsNone(ip.parse_invoice_qr(s), s)

    def test_dup_key(self):
        self.assertEqual(ip.dup_key(None, "26440000000012345678"), "N:26440000000012345678")
        self.assertEqual(ip.dup_key("4400194130", "01234567"), "C:4400194130:01234567")
        self.assertEqual(ip.dup_key("", "01234567", "2020-01-01", 12.5), "W:01234567:2020-01-01:12.50")
        self.assertIsNone(ip.dup_key("", "01234567"))
        self.assertIsNone(ip.dup_key(None, None))

    def test_normalize_name(self):
        n = ip.normalize_name
        self.assertEqual(n(" 样例（深圳）商贸 有限公司。"), n("样例(深圳)商贸有限公司"))
        self.assertEqual(n("【测试】甲方　科技有限公司"), n("(测试)甲方科技有限公司"))
        self.assertEqual(n("电⼦发票"), "电子发票")      # 康熙部首"⼦"
        self.assertEqual(n("abc Co., Ltd."), "ABCCO.,LTD")
        self.assertNotEqual(n("测试甲方科技有限公司"), n("测试甲方食品科技有限公司"))

    def test_cn_upper(self):
        self.assertEqual(ip.cn_upper_to_float("壹仟贰佰捌拾捌圆陆角整"), 1288.6)
        self.assertEqual(ip.cn_upper_to_float("⊗贰佰圆零伍角"), 200.5)
        self.assertEqual(ip.cn_upper_to_float("贰佰叁拾肆圆伍角陆分"), 234.56)
        self.assertEqual(ip.cn_upper_to_float("拾圆整"), 10.0)
        self.assertEqual(ip.cn_upper_to_float("壹万零伍圆整"), 10005.0)
        self.assertIsNone(ip.cn_upper_to_float("(小写)"))

    def test_uscc(self):
        self.assertTrue(ip.uscc_ok(FAKE["buyer_tid"]))
        self.assertFalse(ip.uscc_ok(FAKE["buyer_tid"][:-1] + ("0" if FAKE["buyer_tid"][-1] != "0" else "1")))


class TestSniffZip(unittest.TestCase):
    def _zip(self, members, flag_utf8=True):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for n, b in members:
                z.writestr(n, b)
        return buf.getvalue()

    def test_sniff(self):
        s = ip.sniff_type
        self.assertEqual(s("a.bin", b"%PDF-1.7\n..."), "pdf")
        self.assertEqual(s("x.dat", b"\xff\xd8\xff\xe0" + b"\0" * 20), "image")
        self.assertEqual(s("x", b"\x89PNG\r\n\x1a\n" + b"\0" * 20), "image")
        self.assertEqual(s("x.heic", b"\0\0\0\x18ftypheic" + b"\0" * 20), "image")
        self.assertEqual(s("inv.ofd", self._zip([("OFD.xml", b"<ofd:OFD/>"), ("Doc_0/Document.xml", b"<a/>")])), "ofd")
        self.assertEqual(s("pack.zip", self._zip([("a.pdf", b"%PDF-1.4")])), "zip")
        self.assertEqual(s("list.xlsx", self._zip([("xl/workbook.xml", b"<x/>"), ("[Content_Types].xml", b"<x/>")])), "excel")
        self.assertEqual(s("old.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 40), "excel")
        self.assertEqual(s("e.xml", b"\xef\xbb\xbf<?xml version='1.0'?><EInvoice/>"), "xml")
        self.assertEqual(s("e.xml", b"  <EInvoice/>"), "xml")
        self.assertEqual(s("note.txt", b"hello"), "other")
        self.assertEqual(s("scan.JPG", b"garbage"), "image")     # 魔数认不出退到扩展名
        self.assertEqual(s("", b""), "other")

    def _raw_name_zip(self, entries):
        """写出"文件名是 GBK 字节、UTF-8 标志位没置"的压缩包（国内压缩软件的常见产物）：
        先用等长 ASCII 占位名写，再把两处文件名字节原地换成 GBK。"""
        buf = io.BytesIO()
        subs = []
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for i, (name, data) in enumerate(entries):
                raw = name.encode("gbk")
                ph = ("N%d_" % i).encode("ascii")
                ph = ph + b"x" * (len(raw) - len(ph))
                self.assertEqual(len(ph), len(raw))
                z.writestr(ph.decode("ascii"), data)
                subs.append((ph, raw))
        b = buf.getvalue()
        for ph, raw in subs:
            self.assertEqual(b.count(ph), 2)
            b = b.replace(ph, raw)
        return b

    def test_unpack_gbk_nested_and_junk(self):
        inner = self._raw_name_zip([("里层发票.pdf", b"%PDF-1.4 inner")])
        outer = self._raw_name_zip([("发票/甲.pdf", b"%PDF-1.4 a"), ("发票/里层.zip", inner)])
        buf = io.BytesIO(outer)
        with zipfile.ZipFile(buf, "a") as z:
            z.writestr("__MACOSX/发票/._甲.pdf", b"junk")
            z.writestr(".DS_Store", b"junk")
            z.writestr("空目录/", b"")
            z.writestr("ok.png", b"\x89PNG\r\n\x1a\n")
        files = ip.unpack_zip(buf.getvalue())
        names = [n for n, _ in files]
        self.assertIn("发票/甲.pdf", names)
        self.assertIn("发票/里层.zip/里层发票.pdf", names)
        self.assertIn("ok.png", names)
        self.assertEqual(len(names), 3, names)
        self.assertEqual(dict(files)["发票/甲.pdf"], b"%PDF-1.4 a")
        self.assertEqual(files.warnings, [])
        files2, warns = ip.unpack_zip_ex(buf.getvalue())
        self.assertEqual(sorted(n for n, _ in files2), sorted(names))

    def test_unpack_limits(self):
        lvl3 = self._zip([("deep.pdf", b"%PDF-1.4")])
        lvl2 = self._zip([("l3.zip", lvl3)])
        top = self._zip([("l2.zip", lvl2), ("big.pdf", b"%PDF" + b"x" * 5000), ("small.pdf", b"%PDF-1.4")])
        files = ip.unpack_zip(top, max_file=1000)
        names = [n for n, _ in files]
        self.assertEqual(names, ["small.pdf"])
        self.assertTrue(any("big.pdf" in w for w in files.warnings), files.warnings)
        self.assertTrue(any("l3.zip" in w and "层" in w for w in files.warnings), files.warnings)
        files = ip.unpack_zip(self._zip([("a.pdf", b"a" * 600), ("b.pdf", b"b" * 600)]), max_total=1000)
        self.assertEqual([n for n, _ in files], ["a.pdf"])
        self.assertTrue(any("超过" in w for w in files.warnings))
        files = ip.unpack_zip(b"PK\x03\x04broken")
        self.assertEqual(list(files), [])
        self.assertTrue(files.warnings)


@unittest.skipUnless(HAS_PIL, "Pillow 未安装")
class TestImageVariants(unittest.TestCase):
    def _img(self, size, fmt="PNG", exif_orient=None, noise=False):
        from PIL import Image
        if noise:
            im = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))
        else:
            im = Image.new("RGB", size, (200, 220, 240))
        buf = io.BytesIO()
        if exif_orient:
            ex = Image.Exif()
            ex[274] = exif_orient
            im.save(buf, fmt, exif=ex.tobytes())
        else:
            im.save(buf, fmt)
        return buf.getvalue()

    def _size(self, b):
        from PIL import Image
        return Image.open(io.BytesIO(b)).size

    def test_small_png_kept(self):
        data = self._img((800, 600))
        v = ip.make_image_variants(data)
        self.assertEqual(v["orig"], data)
        self.assertEqual(v["orig_ext"], "png")
        self.assertEqual((v["w"], v["h"]), (800, 600))
        self.assertEqual(self._size(v["preview"]), (800, 600))
        self.assertEqual(max(self._size(v["thumb"])), 320)
        self.assertTrue(v["preview"].startswith(b"\xff\xd8"))

    def test_exif_rotation(self):
        data = self._img((400, 300), "JPEG", exif_orient=6)
        v = ip.make_image_variants(data)
        self.assertEqual((v["w"], v["h"]), (300, 400))
        self.assertEqual(self._size(v["thumb"]), (240, 320))
        self.assertEqual(v["orig_ext"], "jpg")

    def test_big_recompressed(self):
        data = self._img((2400, 1500), "PNG", noise=True)
        self.assertGreater(len(data), 2 * 1024 * 1024)
        v = ip.make_image_variants(data)
        self.assertEqual(v["orig_ext"], "jpg")
        self.assertTrue(v["orig"].startswith(b"\xff\xd8"))
        self.assertEqual(self._size(v["orig"]), (2400, 1500))
        self.assertEqual((v["w"], v["h"]), (1600, 1000))
        big = self._img((3200, 2000), "PNG", noise=True)
        self.assertEqual(max(self._size(ip.make_image_variants(big)["orig"])), 2600)

    def test_rejects(self):
        with self.assertRaises(ValueError) as cm:
            ip.make_image_variants(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64)
        self.assertIn("JPG/PNG", str(cm.exception))
        with self.assertRaises(ValueError):
            ip.make_image_variants(b"not an image at all")

    def test_thumb_from_jpeg(self):
        data = self._img((1600, 1000), "JPEG")
        self.assertEqual(self._size(ip.thumb_from_jpeg(data)), (320, 200))


@unittest.skipUnless(HAS_FITZ and HAS_CV2 and HAS_PIL, "PyMuPDF/opencv/Pillow 未安装")
class TestPdf(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pdf = build_pdf(_page_items_digital("split"))

    def _check_fake(self, d, src="pdf"):
        self.assertEqual(d["kind"], "invoice")
        self.assertTrue(d["isInvoice"])
        self.assertEqual(d["invType"], "special")
        self.assertEqual(d["typeLabel"], "电子发票（增值税专用发票）")
        self.assertEqual(d["number"], FAKE["number"])
        self.assertEqual(d["date"], FAKE["date"])
        self.assertEqual(d["buyerName"], FAKE["buyer"])
        self.assertEqual(d["buyerTaxId"], FAKE["buyer_tid"])
        self.assertEqual(d["sellerName"], FAKE["seller"])
        self.assertEqual(d["sellerTaxId"], FAKE["seller_tid"])
        self.assertEqual((d["amount"], d["tax"], d["total"]), (FAKE["amount"], FAKE["tax"], FAKE["total"]))
        self.assertEqual(d["taxRate"], "6%")
        self.assertEqual(d["category"], "信息技术服务")
        self.assertEqual(len(d["lines"]), 2, d["lines"])
        l1, l2 = d["lines"]
        self.assertEqual((l1["category"], l1["name"], l1["spec"], l1["unit"], l1["qty"], l1["price"], l1["amount"], l1["rate"], l1["tax"]),
                         ("信息技术服务", "软件维护服务费", "V1", "项", 1.0, 1000.0, 1000.0, "6%", 60.0))
        self.assertEqual((l2["name"], l2["unit"], l2["qty"], l2["amount"], l2["tax"]), ("咨询费", "次", 2.0, 200.0, 12.0))
        self.assertEqual(d["remark"], FAKE["remark"])
        self.assertEqual(d["qrRaw"], FAKE_QR)
        self.assertEqual(d["qrType"], "31")
        self.assertEqual(d["warnings"], [])
        self.assertFalse(d["needOcr"])
        self.assertEqual(d["pending"], [])
        for k in ip.DOC_KEYS:
            self.assertIn(k, d)
        fs = d["fieldSrc"]
        for f in ("number", "date", "buyerName", "buyerTaxId", "sellerName", "sellerTaxId", "amount", "tax", "total"):
            self.assertIn(f, fs)
            self.assertEqual(fs[f]["src"], src)
            self.assertEqual(fs[f]["page"], 0)
            self.assertTrue(_inside(fs[f]["box"]), (f, fs[f]))
        # 按位置：购买方在左半边、销售方在右半边；号码在右上；合计在下半部
        self.assertLess(fs["buyerName"]["box"][2], 0.5)
        self.assertGreater(fs["sellerName"]["box"][0], 0.5)
        self.assertLess(fs["buyerTaxId"]["box"][2], 0.5)
        self.assertGreater(fs["sellerTaxId"]["box"][0], 0.5)
        self.assertGreater(fs["number"]["box"][0], 0.7)
        self.assertLess(fs["number"]["box"][3], 0.2)
        self.assertGreater(fs["total"]["box"][1], 0.6)
        # 票号框大致落在写入位置（482pt, 基线 40pt）
        self.assertAlmostEqual(fs["number"]["box"][0], 482 / 595.0, delta=0.01)

    def test_digital_split_shuffled(self):
        t = time.time()
        docs = ip.extract_pdf(self.pdf)
        el = time.time() - t
        print("\n  extract_pdf（合成数电票，1页）%.0f ms" % (el * 1000))
        self.assertEqual(len(docs), 1)
        self._check_fake(docs[0])
        self.assertEqual(ip.dup_key(docs[0]["code"], docs[0]["number"]), "N:" + FAKE["number"])
        self.assertLess(el, 1.0)

    def test_digital_merged_labels(self):
        d = ip.extract_pdf(build_pdf(_page_items_digital("merged")))[0]
        self._check_fake(d)

    def test_no_tax_and_travel(self):
        items = _page_items_digital("split", title="电子发票（普通发票）", special="旅客运输服务", rate="不征税",
                                    tax1="***", tax2="***", tax_total=None)
        qr = "01,32,,%s,1200.00,20260901,," % FAKE["number"]
        d = ip.extract_pdf(build_pdf(items, qr=qr))[0]
        self.assertEqual(d["invType"], "travel")
        self.assertEqual(d["typeLabel"], "旅客运输服务")
        self.assertEqual((d["amount"], d["tax"]), (1200.0, 0.0))
        self.assertEqual(d["taxRate"], "不征税")
        self.assertEqual([l["tax"] for l in d["lines"]], [0.0, 0.0])
        # 票面价税合计写的是 1272，二维码说 1200 → 提示不一致；金额+税额≠合计也提示
        self.assertTrue(any("二维码" in w for w in d["warnings"]), d["warnings"])
        self.assertEqual(ip.validate(d).get("qrMismatch"), ["total"])
        self.assertEqual(ip.deduct_suggest(d)[0], "no")

    def test_old_layout_stacked(self):
        d = ip.extract_pdf(build_pdf(build_old_items(), qr=OLD_QR, qr_rect=(20, 12, 70, 62)))[0]
        self.assertEqual(d["kind"], "invoice")
        self.assertEqual(d["invType"], "special")
        self.assertEqual((d["code"], d["number"], d["date"]), ("044032000111", "12345678", "2020-01-01"))
        self.assertEqual((d["buyerName"], d["buyerTaxId"]), (FAKE["buyer"], FAKE["buyer_tid"]))
        self.assertEqual((d["sellerName"], d["sellerTaxId"]), (FAKE["seller"], FAKE["seller_tid"]))
        self.assertEqual((d["amount"], d["tax"], d["total"]), (100.0, 6.0, 106.0))
        self.assertEqual((d["category"], d["taxRate"]), ("餐饮服务", "6%"))
        self.assertEqual(d["remark"], "测试订单 A-01")
        self.assertEqual(d["warnings"], [])
        self.assertEqual(ip.dup_key(d["code"], d["number"]), "C:044032000111:12345678")
        self.assertEqual(ip.deduct_suggest(d), ("no", "不得抵扣类别：餐饮服务"))

    def test_non_invoice_and_approval(self):
        trip = [(200, 60, "某某出行-行程单", 20), (40, 100, "申请日期：2026-09-01", 9), (40, 120, "行程 1 笔 合计 30.00 元", 9)]
        d = ip.extract_pdf(build_pdf(trip, qr="https://trip.example.com/p/abc", size=(595, 842), qr_rect=(480, 30, 560, 110)))
        self.assertEqual(len(d), 1)
        self.assertEqual((d[0]["kind"], d[0]["isInvoice"], d[0]["approvalLink"]), ("other", False, None))
        self.assertIsNone(d[0]["sellerName"])
        self.assertFalse(d[0]["needOcr"])
        link = "https://aflow.dingtalk.com/qr/TESTCODE01"
        appr = [(40, 60, "付款申请", 18), (40, 100, "审批编号 202609011200000123456", 9), (40, 120, "申请人 测试员", 9)]
        d = ip.extract_pdf(build_pdf(appr, qr=link, size=(596, 842), qr_rect=(470, 30, 560, 120)))[0]
        self.assertEqual((d["kind"], d["approvalLink"]), ("other", link))

    def test_multi_page_and_scanned(self):
        trip = [(200, 60, "某某出行-行程单", 20), (40, 100, "行程 1 笔", 9)]
        docs = ip.extract_pdf(build_pdf(_page_items_digital("split"), extra_pages=[trip]))
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["number"], FAKE["number"])
        # 扫描件：整页只有一张图，没有文字层 → 只读二维码、needOcr
        import fitz
        jpg, (w, h) = _render_jpeg(self.pdf, zoom=2.5)
        doc = fitz.open()
        pg = doc.new_page(width=595, height=397)
        pg.insert_image(pg.rect, stream=jpg)
        scanned = doc.tobytes()
        doc.close()
        d = ip.extract_pdf(scanned)
        self.assertEqual(len(d), 1)
        d = d[0]
        self.assertEqual((d["kind"], d["number"], d["date"], d["total"], d["needOcr"]),
                         ("invoice", FAKE["number"], FAKE["date"], FAKE["total"], True))
        self.assertEqual(d["fieldSrc"]["number"]["src"], "qr")
        self.assertTrue(_inside(d["fieldSrc"]["number"]["box"]))
        self.assertLess(d["fieldSrc"]["number"]["box"][0], 0.2)
        blank = fitz.open()
        blank.new_page()
        e = ip.extract_pdf(blank.tobytes())[0]
        self.assertEqual((e["kind"], e["needOcr"]), ("other", True))

    def test_two_invoices_on_one_page(self):
        docs = ip.extract_pdf(build_2up_pdf())
        self.assertEqual(len(docs), 2)
        a, b = docs
        for d, v in ((a, FAKE), (b, FAKE2)):
            self.assertEqual((d["number"], d["sellerName"], d["sellerTaxId"], d["buyerName"], d["total"], d["remark"]),
                             (v["number"], v["seller"], v["seller_tid"], v["buyer"], v["total"], v["remark"]))
            self.assertEqual(d["fieldSrc"]["number"]["src"], "pdf")
            self.assertEqual(len(d["lines"]), 2)
            self.assertEqual(d["warnings"], [])
        self.assertLess(a["fieldSrc"]["sellerName"]["box"][3], 0.5)
        self.assertGreater(b["fieldSrc"]["sellerName"]["box"][1], 0.5)
        docs = ip.extract_pdf(build_2up_pdf(scanned=True))
        self.assertEqual([d["number"] for d in docs], [FAKE["number"], FAKE2["number"]])
        self.assertTrue(all(d["needOcr"] for d in docs))

    def test_broken_pdf(self):
        d = ip.extract_pdf(b"%PDF-1.4 this is not really a pdf")
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["kind"], "other")
        self.assertTrue(d[0]["warnings"])

    def test_render_pdf(self):
        pages = ip.render_pdf(self.pdf, long_side=1600)
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["w"], 1600)
        self.assertAlmostEqual(pages[0]["h"], 1600 * 397 / 595.0, delta=1.0)
        self.assertTrue(pages[0]["jpeg"].startswith(b"\xff\xd8"))
        self.assertEqual(ip.decode_qr_image(pages[0]["jpeg"]), [FAKE_QR])
        self.assertEqual(max(TestImageVariants._size(None, ip.thumb_from_jpeg(pages[0]["jpeg"]))), 320)


@unittest.skipUnless(HAS_FITZ and HAS_CV2 and HAS_PIL, "PyMuPDF/opencv/Pillow 未安装")
class TestPhoto(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pdf = build_pdf(_page_items_digital("split"))

    def test_degraded_photo_qr(self):
        for rot in (0, 90, 180):
            photo = _degraded_photo(self.pdf, rot=rot)
            self.assertIn(FAKE_QR, ip.decode_qr_image(photo))
            t = time.time()
            d = ip.extract_image_fast(photo)
            el = time.time() - t
            print("\n  extract_image_fast（12MP 模拟照片，旋转 %d°）%.0f ms" % (rot, el * 1000))
            self.assertEqual((d["kind"], d["number"], d["date"], d["total"]), ("invoice", FAKE["number"], FAKE["date"], FAKE["total"]))
            self.assertTrue(d["needOcr"])
            self.assertEqual(d["fieldSrc"]["number"]["src"], "qr")
            self.assertTrue(_inside(d["fieldSrc"]["number"]["box"]))
            self.assertEqual(ip.guess_rotation(photo), {0: 0, 90: 270, 180: 180}[rot])
            self.assertLess(el, 2.0)

    def test_photo_without_invoice_qr(self):
        from PIL import Image
        import cv2
        import numpy as np
        blank = io.BytesIO()
        Image.new("RGB", (1200, 900), (240, 240, 240)).save(blank, "JPEG")
        d = ip.extract_image_fast(blank.getvalue())
        self.assertEqual((d["kind"], d["needOcr"], d["approvalLink"]), ("other", True, None))
        link = "https://aflow.dingtalk.com/qr/TESTCODE02"
        q = cv2.imdecode(np.frombuffer(_qr_png(link), np.uint8), cv2.IMREAD_GRAYSCALE)
        canvas = np.full((900, 1200), 235, np.uint8)
        canvas[300:300 + q.shape[0], 400:400 + q.shape[1]] = q
        ok, jpg = cv2.imencode(".jpg", canvas)
        d = ip.extract_image_fast(jpg.tobytes())
        self.assertEqual((d["kind"], d["approvalLink"], d["needOcr"]), ("other", link, False))
        d = ip.extract_image_fast(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64)
        self.assertEqual(d["kind"], "other")
        self.assertTrue(d["warnings"])


@unittest.skipUnless(HAS_FITZ and HAS_CV2 and HAS_PIL and HAS_OCR, "rapidocr_onnxruntime 未安装，跳过识别测试")
class TestOcr(unittest.TestCase):
    def test_ocr_clean_render(self):
        pdf = build_pdf(_page_items_digital("split"))
        jpg = ip.render_pdf(pdf, long_side=1800)[0]["jpeg"]
        fast = ip.extract_image_fast(jpg)
        t = time.time()
        d = ip.extract_image_ocr(jpg, qr_doc=fast)
        el = time.time() - t
        print("\n  extract_image_ocr（1800px）%.1f s" % el)
        self.assertEqual((d["number"], d["total"], d["date"]), (FAKE["number"], FAKE["total"], FAKE["date"]))
        self.assertEqual(d["fieldSrc"]["number"]["src"], "qr")
        self.assertEqual(d["sellerTaxId"], FAKE["seller_tid"])
        self.assertEqual(d["fieldSrc"]["sellerTaxId"]["src"], "ocr")
        self.assertTrue(_inside(d["fieldSrc"]["sellerTaxId"]["box"]))
        self.assertGreater(d["fieldSrc"]["sellerTaxId"]["box"][0], 0.5)
        self.assertIn("sellerTaxId", d["pending"])
        self.assertNotIn("number", d["pending"])
        self.assertFalse(d["needOcr"])
        self.assertTrue(d["isInvoice"])
        # 不给第一拨结果：图里有发票码就自己读码，码上的字段不进待核
        d2 = ip.extract_image_ocr(jpg)
        self.assertEqual((d2["number"], d2["sellerTaxId"], d2["qrRaw"]), (FAKE["number"], FAKE["seller_tid"], FAKE_QR))
        self.assertNotIn("number", d2["pending"])
        # 没有二维码的票：号码/金额全靠识别，全部进待核
        noqr = ip.render_pdf(build_pdf(_page_items_digital("split"), qr=None), long_side=1800)[0]["jpeg"]
        d3 = ip.extract_image_ocr(noqr)
        self.assertEqual((d3["number"], d3["total"], d3["sellerTaxId"], d3["kind"]),
                         (FAKE["number"], FAKE["total"], FAKE["seller_tid"], "invoice"))
        for f in ("number", "total", "date", "sellerTaxId", "buyerName"):
            self.assertIn(f, d3["pending"])
        self.assertIsNone(d3["qrRaw"])

    def test_ocr_two_up_scan(self):
        # A4 上下两张的扫描件：第二张的识别结果只取第二张那一块，不串到第一张的字段
        pdf = build_2up_pdf(scanned=True)
        docs = ip.extract_pdf(pdf)
        page = ip.render_pdf(pdf, long_side=1800)[0]["jpeg"]
        d = ip.extract_image_ocr(page, qr_doc=docs[1])
        self.assertEqual((d["number"], d["sellerName"], d["sellerTaxId"]), (FAKE2["number"], FAKE2["seller"], FAKE2["seller_tid"]))
        self.assertGreater(d["fieldSrc"]["sellerTaxId"]["box"][1], 0.5)
        d = ip.extract_image_ocr(page, qr_doc=docs[0])
        self.assertEqual((d["number"], d["sellerName"]), (FAKE["number"], FAKE["seller"]))
        self.assertLess(d["fieldSrc"]["sellerName"]["box"][3], 0.5)

    def test_ocr_tokens_shape(self):
        pdf = build_pdf(_page_items_digital("split"))
        jpg = ip.render_pdf(pdf, long_side=1200)[0]["jpeg"]
        toks, w, h = ip.ocr_tokens(jpg, long_side=1200)
        self.assertEqual((w, h), (1200, 801))
        self.assertTrue(toks)
        for t in toks:
            self.assertEqual(len(t["box"]), 4)
            self.assertTrue(0 <= t["box"][0] <= t["box"][2] <= w + 1)
            self.assertIsInstance(t["score"], float)


@unittest.skipUnless(HAS_FITZ and HAS_CV2 and HAS_PIL, "PyMuPDF/opencv/Pillow 未安装")
class TestOfdXml(unittest.TestCase):
    @staticmethod
    def build_ofd(with_qr=True, pad=b"", phys="0 0 210 140", number_box=None):
        """pad：塞进票面页 Content.xml 的填充（测压缩炸弹/超大条目）；phys：PhysicalBox；
        number_box：给发票号码那个文字对象换一个 Boundary 串（测 NaN 坐标）。"""
        k = 210.0 / 595.0     # pt → mm
        tpl, val = [], []
        labels = {"电子发票（增值税专用发票）", "发票号码：", "开票日期：", "名称：", "统一社会信用代码/纳税人识别号：",
                  "项目名称", "规格型号", "单 位", "数 量", "单 价", "金 额", "税率/征收率", "税 额", "合", "计",
                  "价税合计（大写）", "（小写）", "备", "注", "开票人："} | set("购买方信息销售方信息")
        for x, y, s, fs in _page_items_digital("split"):
            wid = sum(1.0 if ord(ch) > 0x2E80 else 0.55 for ch in s) * fs
            obj = (x * k, (y - fs * 0.88) * k, wid * k, fs * 1.1 * k, s)
            (tpl if s in labels else val).append(obj)

        def content(objs, start, template=None, pad=b""):
            parts = ['<?xml version="1.0" encoding="UTF-8"?>',
                     '<ofd:Page xmlns:ofd="http://www.ofdspec.org/2016">']
            if template:
                parts.append('<ofd:Template TemplateID="%s" ZOrder="Background"/>' % template)
            parts.append('<ofd:Content><ofd:Layer ID="%d">' % start)
            for i, (x, y, w, h, s) in enumerate(objs):
                bd = "%.2f %.2f %.2f %.2f" % (x, y, w, h)
                if number_box is not None and FAKE["number"] in s:
                    bd = number_box
                parts.append('<ofd:TextObject ID="%d" Boundary="%s" Font="3" Size="3.2">'
                             '<ofd:TextCode X="0" Y="%.2f">%s</ofd:TextCode></ofd:TextObject>'
                             % (start + 1 + i, bd, h * 0.8, s.replace("&", "&amp;").replace("<", "&lt;")))
            parts.append("</ofd:Layer></ofd:Content></ofd:Page>")
            return "\n".join(parts).encode("utf-8").replace(b"</ofd:Content>", pad + b"</ofd:Content>")
        ofd_xml = ('<?xml version="1.0" encoding="UTF-8"?><ofd:OFD xmlns:ofd="http://www.ofdspec.org/2016" '
                   'DocType="OFD" Version="1.1"><ofd:DocBody><ofd:DocInfo><ofd:DocID>t</ofd:DocID></ofd:DocInfo>'
                   '<ofd:DocRoot>Doc_0/Document.xml</ofd:DocRoot></ofd:DocBody></ofd:OFD>').encode("utf-8")
        doc_xml = ('<?xml version="1.0" encoding="UTF-8"?><ofd:Document xmlns:ofd="http://www.ofdspec.org/2016">'
                   '<ofd:CommonData><ofd:MaxUnitID>999</ofd:MaxUnitID><ofd:PageArea><ofd:PhysicalBox>' + phys +
                   '</ofd:PhysicalBox></ofd:PageArea><ofd:TemplatePage ID="2" BaseLoc="Tpls/Tpl_0/Content.xml"/>'
                   '</ofd:CommonData><ofd:Pages><ofd:Page ID="1" BaseLoc="Pages/Page_0/Content.xml"/></ofd:Pages>'
                   '</ofd:Document>').encode("utf-8")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("OFD.xml", ofd_xml)
            z.writestr("Doc_0/Document.xml", doc_xml)
            z.writestr("Doc_0/Tpls/Tpl_0/Content.xml", content(tpl, 100))
            z.writestr("Doc_0/Pages/Page_0/Content.xml", content(val, 500, template="2", pad=pad))
            if with_qr:
                z.writestr("Doc_0/Res/qr.png", _qr_png(FAKE_QR))
        return buf.getvalue()

    def test_ofd(self):
        data = self.build_ofd()
        self.assertEqual(ip.sniff_type("x.ofd", data), "ofd")
        docs = ip.extract_ofd(data)
        self.assertEqual(len(docs), 1)
        d = docs[0]
        for f, v in (("number", FAKE["number"]), ("date", FAKE["date"]), ("buyerName", FAKE["buyer"]),
                     ("buyerTaxId", FAKE["buyer_tid"]), ("sellerName", FAKE["seller"]),
                     ("sellerTaxId", FAKE["seller_tid"]), ("amount", FAKE["amount"]), ("tax", FAKE["tax"]),
                     ("total", FAKE["total"]), ("invType", "special"), ("taxRate", "6%"), ("qrRaw", FAKE_QR)):
            self.assertEqual(d[f], v, f)
        self.assertEqual(len(d["lines"]), 2)
        self.assertEqual(d["fieldSrc"]["number"]["src"], "ofd")
        self.assertTrue(all(_inside(v["box"]) for v in d["fieldSrc"].values()))
        jpg = ip.render_virtual(d)
        from PIL import Image
        im = Image.open(io.BytesIO(jpg))
        self.assertEqual(im.format, "JPEG")
        self.assertEqual(im.size[0], 1600)
        self.assertEqual(ip.decode_qr_image(jpg), [FAKE_QR])    # 虚拟图片上画回了二维码

    def test_xml(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<EInvoice xmlns="urn:test:einvoice">
  <Header><EIid>{n}</EIid><InherentLabel><GeneralOrSpecialVAT><LabelCode>01</LabelCode>
    <LabelName>增值税专用发票</LabelName></GeneralOrSpecialVAT></InherentLabel></Header>
  <EInvoiceData>
    <SellerInformation><SellerIdNum>{st}</SellerIdNum><SellerName>{s}</SellerName></SellerInformation>
    <BuyerInformation><BuyerIdNum>{bt}</BuyerIdNum><BuyerName>{b}</BuyerName></BuyerInformation>
    <BasicInformation><TotalAmWithoutTax>1200.00</TotalAmWithoutTax><TotalTaxAm>72.00</TotalTaxAm>
      <TotalTax-includedAmount>1272.00</TotalTax-includedAmount>
      <TotalTax-includedAmountInChinese>壹仟贰佰柒拾贰圆整</TotalTax-includedAmountInChinese>
      <RequestTime>2026-09-01 10:11:12</RequestTime></BasicInformation>
    <IssuItemInformation><ItemName>*信息技术服务*软件维护服务费</ItemName><SpecMod>V1</SpecMod><MeaUnits>项</MeaUnits>
      <Quantity>1</Quantity><UnPrice>1000</UnPrice><Amount>1000.00</Amount><TaxRate>0.06</TaxRate><ComTaxAm>60.00</ComTaxAm></IssuItemInformation>
    <IssuItemInformation><ItemName>*现代服务*咨询费</ItemName><Quantity>2</Quantity><UnPrice>100</UnPrice>
      <Amount>200.00</Amount><TaxRate>6%</TaxRate><ComTaxAm>12.00</ComTaxAm></IssuItemInformation>
  </EInvoiceData>
  <TaxSupervisionInfo><InvoiceNumber>{n}</InvoiceNumber><IssueTime>2026-09-01</IssueTime></TaxSupervisionInfo>
</EInvoice>""".format(n=FAKE["number"], s=FAKE["seller"], st=FAKE["seller_tid"], b=FAKE["buyer"], bt=FAKE["buyer_tid"])
        data = xml.encode("utf-8")
        self.assertEqual(ip.sniff_type("a.xml", data), "xml")
        d = ip.extract_xml(data)[0]
        for f, v in (("number", FAKE["number"]), ("date", FAKE["date"]), ("buyerName", FAKE["buyer"]),
                     ("buyerTaxId", FAKE["buyer_tid"]), ("sellerName", FAKE["seller"]),
                     ("sellerTaxId", FAKE["seller_tid"]), ("amount", 1200.0), ("tax", 72.0), ("total", 1272.0),
                     ("invType", "special"), ("taxRate", "6%"), ("category", "信息技术服务"), ("kind", "invoice")):
            self.assertEqual(d[f], v, f)
        self.assertEqual([l["name"] for l in d["lines"]], ["软件维护服务费", "咨询费"])
        self.assertEqual(d["fieldSrc"]["number"]["src"], "xml")
        self.assertTrue(_inside(d["fieldSrc"]["number"]["box"]))
        self.assertTrue(ip.render_virtual(d).startswith(b"\xff\xd8"))
        gbk = ('<?xml version="1.0" encoding="GBK"?><发票><发票号码>%s</发票号码><开票日期>2026年09月01日</开票日期>'
               '<销售方名称>%s</销售方名称><价税合计>1272.00</价税合计></发票>' % (FAKE["number"], FAKE["seller"])).encode("gbk")
        d = ip.extract_xml(gbk)[0]
        self.assertEqual((d["number"], d["sellerName"], d["total"], d["date"]), (FAKE["number"], FAKE["seller"], 1272.0, FAKE["date"]))
        bomb = b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "xx">]><a>&x;</a>'
        d = ip.extract_xml(bomb)[0]
        self.assertEqual(d["kind"], "other")
        self.assertTrue(d["warnings"])


def _png_gray(w, h, rows=True):
    """手写一张 w×h 的 8 位灰度 PNG（全黑，压缩后很小）；rows=False 只写文件头不写像素（测"文件头先把关"）。"""
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    body = b""
    if rows:
        co = zlib.compressobj(9)
        row = b"\x00" * (w + 1)
        body = b"".join(co.compress(row) for _ in range(h)) + co.flush()
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", body) + chunk(b"IEND", b""))


class _Patch(object):
    """临时改内核里的上限常量（测试结束还原）。"""

    def __init__(self, **kw):
        self.kw = kw
        self.old = {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = getattr(ip, k)
            setattr(ip, k, v)

    def __exit__(self, *a):
        for k, v in self.old.items():
            setattr(ip, k, v)


class TestDecodeCaps(unittest.TestCase):
    """防"小文件吃大内存"：压缩炸弹、伪造超大像素/超大页面、UTF-16 绕过 DTD 检查、NaN 金额/坐标。
    每条都是修之前能复现、修之后被挡住的场景（合成数据）。"""

    def _zip(self, members, method=zipfile.ZIP_DEFLATED):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", method) as z:
            for n, b in members:
                z.writestr(n, b)
        return buf.getvalue()

    # ---- 二维码金额 ----
    def test_qr_amount_nan_inf_rejected(self):
        for amt in ("nan", "NaN", "inf", "-inf", "1e17", "1E5", "12345678901234", "1,000.00", "0x10"):
            self.assertIsNone(ip.parse_invoice_qr("01,32,,26440000000012345678,%s,20260901,," % amt), amt)
        q = ip.parse_invoice_qr("01,32,,26440000000012345678,-12.50,20260901,,")
        self.assertEqual(q["total"], -12.5)
        self.assertEqual(ip.parse_invoice_qr("01,04,044001900111,76543210,88,20191231,,")["amount"], 88.0)

    # ---- 压缩包 ----
    def test_zip_entry_ratio_bomb_skipped(self):
        data = self._zip([("bomb.pdf", b"\x00" * (5 * 1024 * 1024)), ("ok.pdf", b"%PDF-1.4 ok")])
        self.assertLess(len(data), 64 * 1024)
        files = ip.unpack_zip(data)
        self.assertEqual([n for n, _ in files], ["ok.pdf"])
        self.assertTrue(any("bomb.pdf" in w and "压缩" in w for w in files.warnings), files.warnings)

    def test_zip_total_ratio_cap(self):
        # 每个条目都不到 1MB（逐个看不出问题），但整包解开是压缩前的几百倍：总量按 max(25MB, 包大小×100) 封顶
        members = [("p%02d.pdf" % i, (b"%%PDF-%02d" % i) + b"\x00" * (900 * 1024)) for i in range(40)]
        data = self._zip(members)
        self.assertLess(len(data) * ip._ZIP_MAX_RATIO, ip._ZIP_TOTAL_FLOOR)
        files = ip.unpack_zip(data)
        total = sum(len(b) for _, b in files)
        self.assertLessEqual(total, ip._ZIP_TOTAL_FLOOR)
        self.assertLess(len(files), 40)
        self.assertTrue(any("超过" in w for w in files.warnings), files.warnings)
        # 套娃：外层很小、里层照样按外层大小算总额度
        outer = self._zip([("inner.zip", data)], method=zipfile.ZIP_STORED)
        files = ip.unpack_zip(outer)
        self.assertLessEqual(sum(len(b) for _, b in files), ip._ZIP_TOTAL_FLOOR)
        self.assertTrue(any("超过" in w for w in files.warnings), files.warnings)

    def test_zip_entry_count_and_warning_caps(self):
        junk = [("._junk%04d" % i, b"j") for i in range(60)] + [("real.pdf", b"%PDF-1.4")]
        files, warns = ip.unpack_zip_ex(self._zip(junk), max_count=10)     # 连同跳过的最多看 40 个
        self.assertEqual(files, [])
        self.assertTrue(any("超过 10 个" in w for w in warns), warns)
        big = [("f%03d.pdf" % i, b"%PDF" + b"x" * 200) for i in range(100)]
        files, warns = ip.unpack_zip_ex(self._zip(big), max_file=100)
        self.assertEqual(files, [])
        self.assertEqual(len(warns), ip._ZIP_MAX_WARN + 1)
        self.assertIn("另有 70 条", warns[-1])

    def test_zip_huge_directory_not_opened(self):
        data = self._zip([("f%03d.pdf" % i, b"%PDF-1.4") for i in range(100)])
        with _Patch(_ZIP_MAX_CD=1000):
            self.assertEqual(ip.sniff_type("a.ofd", data), "zip")
            files = ip.unpack_zip(data)
            self.assertEqual(list(files), [])
            self.assertTrue(any("多得不正常" in w for w in files.warnings), files.warnings)
            d = ip.extract_ofd(data)[0]
            self.assertEqual(d["kind"], "other")
            self.assertTrue(any("多得不正常" in w for w in d["warnings"]), d["warnings"])
        self.assertEqual(len(ip.unpack_zip(data)), 100)

    # ---- 图片 ----
    @unittest.skipUnless(HAS_PIL, "Pillow 未安装")
    def test_image_over_pixel_cap_rejected_before_decode(self):
        png = _png_gray(8000, 6000)          # 4800 万像素：低于 Pillow 默认告警线，修之前会整张解码
        self.assertLess(len(png), 256 * 1024)
        with self.assertRaises(ValueError) as cm:
            ip.make_image_variants(png)
        self.assertIn("像素", str(cm.exception))
        self.assertEqual(ip.decode_qr_image(png), [])
        if HAS_CV2:
            d = ip.extract_image_fast(png)
            self.assertEqual(d["kind"], "other")
            self.assertTrue(any("像素" in w for w in d["warnings"]), d["warnings"])
        # 只有文件头、声明 4 亿像素：直接说"像素太大"，不是"格式不支持"
        with self.assertRaises(ValueError) as cm:
            ip.make_image_variants(_png_gray(20000, 20000, rows=False))
        self.assertIn("像素", str(cm.exception))
        # 上限以内照常处理
        v = ip.make_image_variants(_png_gray(3000, 2000))
        self.assertEqual((v["w"], v["h"]), (1600, 1067))

    @unittest.skipUnless(HAS_PIL, "Pillow 未安装")
    def test_big_jpeg_draft_decoded_progressive_rejected(self):
        from PIL import Image
        base = Image.new("L", (9000, 5000), 200)
        buf = io.BytesIO()
        base.save(buf, "JPEG", quality=30)
        jpg = buf.getvalue()
        im, fmt = ip._open_image(jpg)            # 4500 万像素的 JPEG：按 DCT 缩档解码，不拒收
        self.assertEqual(fmt, "JPEG")
        self.assertLessEqual(im.size[0] * im.size[1], ip._IMG_MAX_PIXELS)
        self.assertEqual(ip.make_image_variants(jpg)["w"], 1600)
        buf = io.BytesIO()
        base.save(buf, "JPEG", quality=30, progressive=True)
        with self.assertRaises(ValueError) as cm:  # 渐进式 JPEG 缩档也要按原尺寸开缓冲区
            ip.make_image_variants(buf.getvalue())
        self.assertIn("像素", str(cm.exception))

    # ---- PDF 页面渲染 ----
    @unittest.skipUnless(HAS_FITZ and HAS_CV2 and HAS_PIL, "PyMuPDF/opencv/Pillow 未安装")
    def test_pdf_huge_page_render_capped(self):
        import fitz
        doc = fitz.open()
        doc.new_page(width=4000, height=4000)    # 55 英寸见方的空白页：按 200dpi 渲染要 1.2 亿像素
        doc.new_page(width=595, height=842)
        data = doc.tobytes()
        doc.close()
        orig = fitz.Page.get_pixmap
        asked = []

        def spy(page, *a, **kw):
            m = kw.get("matrix") or (a[0] if a else fitz.Identity)
            r = fitz.Rect(kw.get("clip") or page.rect) * m
            px = abs(r.width * r.height)
            asked.append(px)
            if px > 30e6:
                raise RuntimeError("spy: 拒绝渲染 %d 像素" % px)
            return orig(page, *a, **kw)

        fitz.Page.get_pixmap = spy
        try:
            docs = ip.extract_pdf(data)
            pages = ip.render_pdf(data, max_pages=5, long_side=12000)
            one = ip.render_pdf_page(data, 0, long_side=12000)
            virt = ip.render_virtual(dict(ip.new_doc(), number=FAKE["number"]), long_side=20000)
        finally:
            fitz.Page.get_pixmap = orig
        self.assertTrue(asked)
        self.assertLessEqual(max(asked), ip._RENDER_MAX_PIXELS * 1.01, asked)
        self.assertEqual(docs[0]["kind"], "other")
        self.assertEqual(len(pages), 2)
        self.assertLessEqual(pages[0]["w"] * pages[0]["h"], ip._RENDER_MAX_PIXELS * 1.01)
        self.assertEqual((one["w"], one["h"]), (pages[0]["w"], pages[0]["h"]))
        self.assertTrue(virt.startswith(b"\xff\xd8"))
        # A4 正常页不受影响：整页兜底仍是 200dpi
        z = ip._safe_zoom(fitz.Rect(0, 0, 595, 842), 200.0 / 72.0)
        self.assertAlmostEqual(z, 200.0 / 72.0)

    @unittest.skipUnless(HAS_FITZ and HAS_CV2 and HAS_PIL, "PyMuPDF/opencv/Pillow 未安装")
    def test_render_pdf_page(self):
        pdf = build_pdf(_page_items_digital("split"), extra_pages=[[(40, 60, "第二页 说明", 12)]])
        pages = ip.render_pdf(pdf, max_pages=5, long_side=1800)
        self.assertEqual(len(pages), 2)
        p1 = ip.render_pdf_page(pdf, 1, long_side=1800)
        self.assertEqual((p1["w"], p1["h"], p1["jpeg"]), (pages[1]["w"], pages[1]["h"], pages[1]["jpeg"]))
        self.assertIsNone(ip.render_pdf_page(pdf, 2))
        self.assertIsNone(ip.render_pdf_page(pdf, -1))
        self.assertIsNone(ip.render_pdf_page(b"%PDF-1.4 broken", 0))

    # ---- OFD ----
    @unittest.skipUnless(HAS_FITZ and HAS_CV2 and HAS_PIL, "PyMuPDF/opencv/Pillow 未安装")
    def test_ofd_bomb_entry_not_read(self):
        ok = ip.extract_ofd(TestOfdXml.build_ofd(with_qr=False))[0]
        self.assertEqual(ok["number"], FAKE["number"])
        # ① 解开 2MB、压缩比上千倍的页面内容（压缩包本身几 KB）
        data = TestOfdXml.build_ofd(with_qr=False, pad=b" " * (2 * 1024 * 1024))
        self.assertLess(len(data), 64 * 1024)
        d = ip.extract_ofd(data)[0]
        self.assertEqual(d["kind"], "other")
        self.assertIsNone(d["number"])
        self.assertTrue(any("异常" in w for w in d["warnings"]), d["warnings"])
        # ② 压缩比正常但单个条目超过 10MB
        noise = base64.b64encode(os.urandom(8 * 1024 * 1024))
        d = ip.extract_ofd(TestOfdXml.build_ofd(with_qr=False, pad=b"<!--" + noise + b"-->"))[0]
        self.assertIsNone(d["number"])
        self.assertTrue(any("异常" in w for w in d["warnings"]), d["warnings"])
        # ③ 整包累计读取额度用完（模板页被反复引用之类）：后面的不读
        with _Patch(_OFD_TOTAL_MAX=4000):
            d = ip.extract_ofd(TestOfdXml.build_ofd(with_qr=False))[0]
        self.assertIsNone(d["number"])
        self.assertTrue(any("异常" in w for w in d["warnings"]), d["warnings"])

    @unittest.skipUnless(HAS_FITZ and HAS_CV2 and HAS_PIL, "PyMuPDF/opencv/Pillow 未安装")
    def test_ofd_nan_coordinates(self):
        self.assertEqual(ip._floats("nan 1 2 3"), [])
        self.assertEqual(ip._floats("1 2 inf 4"), [])
        self.assertEqual(ip._floats("1, 2.5 3"), [1.0, 2.5, 3.0])
        # 页面尺寸写成 NaN/0：退回默认 A5 尺寸照常解析，结果里没有 NaN（接口出 JSON 时不收 NaN）
        for phys in ("0 0 nan nan", "0 0 0 0", "0 0 210"):
            docs = ip.extract_ofd(TestOfdXml.build_ofd(with_qr=False, phys=phys))
            self.assertEqual(docs[0]["number"], FAKE["number"], phys)
            json.dumps(docs, allow_nan=False)
        docs = ip.extract_ofd(TestOfdXml.build_ofd(with_qr=False, number_box="nan nan nan nan"))
        json.dumps(docs, allow_nan=False)

    # ---- XML ----
    def _xml(self, number=FAKE["number"], dtd=False):
        head = '<?xml version="1.0" encoding="%s"?>'
        body = "<EInvoice><EInvoiceNumber>%s</EInvoiceNumber><TotalTax-includedAmount>10.00</TotalTax-includedAmount></EInvoice>"
        if dtd:
            return head, '<!DOCTYPE EInvoice [<!ENTITY n "%s">]>' % number, body % "&n;"
        return head, "", body % number

    def test_xml_dtd_rejected_in_any_encoding(self):
        head, dtd, body = self._xml(dtd=True)
        cases = [
            ("utf-16", b"", (head % "UTF-16" + dtd + body).encode("utf-16")),            # 带 BOM
            ("utf-16-le", b"", (head % "UTF-16" + dtd + body).encode("utf-16-le")),      # 不带 BOM
            ("utf-16-be", b"", (head % "UTF-16" + dtd + body).encode("utf-16-be")),
            ("utf-32", b"", (head % "UTF-32" + dtd + body).encode("utf-32")),
            ("utf-7", b"", (head % "UTF-7").encode("ascii") + (dtd + body).encode("utf-7")),
            ("utf-8", b"", (head % "UTF-8" + dtd + body).encode("utf-8")),
        ]
        for name, _, data in cases:
            d = ip.extract_xml(data)[0]
            self.assertEqual(d["kind"], "other", name)
            self.assertIsNone(d["number"], name)
            self.assertIsNone(ip._xml_root(data), name)
        # 同样的编码、不带 DTD 的正常票照常能读
        head, _, body = self._xml()
        for enc in ("utf-16", "utf-16-le", "utf-32", "utf-8"):
            data = (head % ("UTF-16" if "16" in enc else "UTF-32" if "32" in enc else "UTF-8") + body).encode(enc)
            d = ip.extract_xml(data)[0]
            self.assertEqual((d["kind"], d["number"], d["total"]), ("invoice", FAKE["number"], 10.0), enc)

    def test_xml_size_and_element_caps(self):
        head, _, body = self._xml()
        data = (head % "UTF-8" + body).encode("utf-8")
        self.assertEqual(ip.extract_xml(data)[0]["number"], FAKE["number"])
        many = (head % "UTF-8" + "<r>" + "<a/>" * 5000 + "</r>").encode("utf-8")
        self.assertIsNotNone(ip._xml_root(many))
        with _Patch(_XML_MAX_ELEMS=1000):
            self.assertIsNone(ip._xml_root(many))
            self.assertEqual(ip.extract_xml(data)[0]["number"], FAKE["number"])
        with _Patch(_XML_MAX_BYTES=100):
            d = ip.extract_xml(data)[0]
            self.assertEqual(d["kind"], "other")
            self.assertTrue(any("超过" in w for w in d["warnings"]), d["warnings"])
        self.assertIsNone(ip._xml_root(b"<a><b></a>"))
        self.assertIsNone(ip._xml_root(b"not xml at all"))


class TestDeductValidate(unittest.TestCase):
    def doc(self, **kw):
        d = ip.new_doc()
        d.update(isInvoice=True, kind="invoice", invType="special", tax=6.0, taxRate="6%",
                 lines=[{"name": "服务费", "category": "现代服务", "spec": None, "unit": None, "qty": None,
                         "price": None, "amount": 100.0, "rate": "6%", "tax": 6.0}])
        d.update(kw)
        return d

    def test_deduct(self):
        s = ip.deduct_suggest
        self.assertEqual(s(self.doc())[0], "yes")
        for cat in ("餐饮服务", "居民日常服务", "娱乐服务", "贷款服务"):
            v, r = s(self.doc(lines=[{"name": "x", "category": cat, "amount": 1, "tax": 0.06}]))
            self.assertEqual((v, r), ("no", "不得抵扣类别：" + cat))
        self.assertEqual(s(self.doc(invType="normal"))[0], "no")
        self.assertEqual(s(self.doc(invType="travel", taxRate="3%"))[0], "yes")
        self.assertEqual(s(self.doc(invType="normal", typeLabel="旅客运输服务"))[0], "yes")
        self.assertEqual(s(self.doc(invType="normal", lines=[{"name": "客运服务费", "category": "交通运输服务", "tax": 3.0}]))[0], "yes")
        self.assertEqual(s(self.doc(invType="train", tax=None, taxRate=None, lines=[]))[0], "yes")
        self.assertEqual(s(self.doc(invType="flight", tax=None, taxRate=None, lines=[]))[0], "yes")
        self.assertEqual(s(self.doc(invType="toll"))[0], "yes")
        self.assertEqual(s(self.doc(tax=0.0, taxRate="免税"))[0], "no")
        self.assertEqual(s(self.doc(invType="normal", tax=0.0, taxRate="不征税"))[0], "no")
        self.assertEqual(s(self.doc(taxRate="***", tax=None))[0], "no")
        self.assertEqual(s(self.doc(invType="quota", tax=None, taxRate=None, lines=[]))[0], "no")
        self.assertEqual(s(ip.new_doc()), ("", ""))

    def test_validate(self):
        comps = [{"name": "测试甲方科技有限公司", "taxId": FAKE["buyer_tid"]}, {"name": "测试丙方有限公司", "taxId": ""}]
        d = self.doc(buyerName="测试甲方科技有限公司", buyerTaxId=FAKE["buyer_tid"], sellerName="示例乙方服务有限公司",
                     amount=100.0, tax=6.0, total=106.0)
        self.assertEqual(ip.validate(d, comps, "测试甲方科技 有限公司", "示例乙方服务有限公司。"), {})
        f = ip.validate(d, comps, "测试丙方有限公司", "别的收款方")
        self.assertEqual(f["sellerMismatch"], {"seller": "示例乙方服务有限公司", "payee": "别的收款方"})
        self.assertEqual(f["buyerMismatch"], {"buyer": "测试甲方科技有限公司", "expected": "测试丙方有限公司"})
        self.assertNotIn("buyerNotCompany", f)
        # 抬头少写一个字但税号对：不算"非本公司"，但与票夹主体不一致
        typo = dict(d, buyerName="测试甲方科有限公司")
        f = ip.validate(typo, comps, "测试甲方科技有限公司")
        self.assertIn("buyerMismatch", f)
        self.assertNotIn("buyerNotCompany", f)
        f = ip.validate(dict(d, buyerName="个人", buyerTaxId=None), comps)
        self.assertTrue(f["buyerNotCompany"])
        f = ip.validate(dict(d, total=107.0))
        self.assertEqual(f["sumMismatch"], {"amount": 100.0, "tax": 6.0, "total": 107.0})
        q = "01,31,,%s,106.00,20260901,," % FAKE["number"]
        good = dict(d, number=FAKE["number"], date="2026-09-01", qrRaw=q)
        self.assertNotIn("qrMismatch", ip.validate(good))
        self.assertEqual(ip.validate(dict(good, number=FAKE["number"][:-1] + "9", total=107.0, tax=7.0))["qrMismatch"], ["number", "total"])
        self.assertEqual(ip.validate(ip.new_doc(), comps, "x", "y"), {})


# ---------------------------------------------------------------------------
# 真实样本回归：只在本机设置了 INV_SAMPLES_DIR 时跑（样本含真实票面，绝不入库）
# ---------------------------------------------------------------------------
SAMPLES = os.environ.get("INV_SAMPLES_DIR")


def _read(p):
    with open(p, "rb") as fh:
        return fh.read()


def _tk(text, x0, y0, x1, y1):
    return {"text": text, "box": [x0, y0, x1, y1], "score": 0.9}


# 合成的"一张照片拍了四张老式票"：左一张北京式出租车票（代码/号码不带标签）、中间上下两张过路费票
# （第二张的代码被章盖住没认出）、右一张深圳式出租车票（值整体比标签高半行、另收附加费）。号码全是编的。
OLD_PHOTO = [
    _tk("北京市出租汽车专用发票", 60, 450, 330, 475),
    _tk("111000000001", 40, 580, 300, 605), _tk("12345678", 40, 612, 220, 637),
    _tk("日期", 90, 785, 140, 805), _tk("2026-01-08", 170, 780, 300, 800),
    _tk("单价", 90, 850, 140, 870), _tk("3.45", 260, 842, 300, 862),
    _tk("里程", 90, 882, 140, 902), _tk("31.6", 262, 876, 300, 896),
    _tk("金额", 95, 985, 140, 1005), _tk("¥100.40", 220, 978, 335, 998),
    _tk("燃油附加费", 95, 1015, 190, 1035), _tk("¥0.00", 225, 1010, 300, 1030),
    _tk("北京市税务局过路（过桥）费专用发票", 460, 335, 900, 360), _tk("北京市某某公路发展集团有限公司", 488, 366, 850, 390),
    _tk("入口站北京站", 507, 461, 680, 485), _tk("金额：", 512, 547, 568, 567), _tk("5", 600, 545, 615, 567),
    _tk("发票代码：111000000002", 488, 658, 760, 680), _tk("发票号码：87654321", 490, 693, 700, 715),
    _tk("北京市税务局", 489, 812, 640, 835), _tk("费专用发票", 780, 810, 900, 835),
    _tk("入口站：", 458, 936, 530, 956), _tk("机场南线进京站：", 545, 930, 700, 950), _tk("金额：", 455, 996, 516, 1016), _tk("10", 570, 1000, 590, 1020),
    _tk("2026-01-08", 535, 1040, 660, 1060), _tk("发票号码：87654322", 458, 1119, 700, 1140),
    _tk("深圳市出租汽车专用发票", 995, 383, 1230, 410),
    _tk("发票代码144000000003", 997, 489, 1230, 510), _tk("发票号码11223344", 994, 517, 1200, 540),
    _tk("单位名称：深圳某某出租汽车有限公司", 995, 584, 1270, 600),
    _tk("车号：", 1020, 702, 1075, 722), _tk("BAH0000", 1125, 687, 1200, 707),
    _tk("日期：", 1024, 758, 1075, 778), _tk("2026年01月11日", 1072, 743, 1220, 763),
    _tk("单价：", 1023, 834, 1075, 854), _tk("4.32元", 1150, 819, 1210, 839),
    _tk("里程：", 1024, 862, 1075, 882), _tk("21.81KM", 1124, 847, 1210, 867),
    _tk("等候：", 1024, 889, 1075, 909), _tk("00:04:06", 1112, 874, 1210, 894),
    _tk("金额：", 1026, 914, 1075, 934), _tk("¥96.00元", 1127, 899, 1225, 919),
    _tk("另收附加费：", 1029, 945, 1124, 965), _tk("¥8.50元", 1127, 930, 1225, 950),
    _tk("另收电召费", 1030, 971, 1116, 991),
]


class TestOldTickets(unittest.TestCase):
    def test_type_from_title_not_special(self):
        t = ip._type_from_title
        self.assertEqual(t("深圳市出租汽车专用发票"), "taxi")
        self.assertEqual(t("BEIJINGTAXISPECIALINVOICE"), "taxi")
        self.assertEqual(t("北京市税务局过路（过桥）费专用发票"), "tollpaper")
        self.assertEqual(t("电子发票（增值税专用发票）"), "special")
        self.assertEqual(t("广东增值税专用发票"), "special")
        self.assertIsNone(t("专用发票"))                      # 没认全的"专用发票"不当专票
        self.assertEqual(t("增值税电子普通发票（通行费）"), "toll")

    def test_deduct_old_types(self):
        for it in ("taxi", "tollpaper", "quota"):
            d = ip.new_doc()
            d.update(isInvoice=True, kind="invoice", invType=it, typeLabel="XX专用发票", tax=None)
            self.assertEqual(ip.deduct_suggest(d)[0], "no", it)
        d = ip.new_doc()
        d.update(isInvoice=True, kind="invoice", invType="toll", tax=3.0)
        self.assertEqual(ip.deduct_suggest(d)[0], "yes")      # 通行费电子发票照样可抵

    def test_title_only_special_fields_from_tokens(self):
        # 只有"XX出租汽车专用发票"标题的票，按版式解析也不能判成专票
        toks = [_tk("深圳市出租汽车专用发票", 300, 20, 700, 50), _tk("发票代码：144000000003", 100, 120, 500, 140),
                _tk("发票号码：11223344", 100, 150, 400, 170)]
        d = ip.fields_from_tokens(toks, 1000, 1000, "ocr")
        self.assertEqual(d["invType"], "taxi")
        self.assertEqual(ip.deduct_suggest(d)[0], "no")

    def test_split_four_tickets(self):
        docs = ip.split_old_tickets(OLD_PHOTO, 1300, 1700)
        got = [(d["invType"], d["code"], d["number"], d["date"], d["total"]) for d in docs]
        self.assertEqual(got, [
            ("taxi", "111000000001", "12345678", "2026-01-08", 100.4),
            ("tollpaper", "111000000002", "87654321", None, 5.0),
            ("tollpaper", None, "87654322", "2026-01-08", 10.0),        # 标题被盖掉一半也按出入口站认成过路费
            ("taxi", "144000000003", "11223344", "2026-01-11", 104.5),  # 金额 96 + 另收附加费 8.5
        ])
        self.assertEqual(docs[3]["sellerName"], "深圳某某出租汽车有限公司")
        self.assertTrue(any("另收费用" in w for w in docs[3]["warnings"]))
        for d in docs:
            self.assertTrue(d["isInvoice"])
            self.assertEqual(ip.deduct_suggest(d)[0], "no")
            self.assertIn("number", d["pending"])            # 识别来的一律待核
            self.assertTrue(any("已按票自动拆开" in w for w in d["warnings"]))
            x0, y0, x1, y1 = d["region"]
            self.assertTrue(0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1)
            # 每张票自己的号码框落在自己那块里
            b = d["fieldSrc"]["number"]["box"]
            self.assertTrue(x0 <= (b[0] + b[2]) / 2 <= x1 and y0 <= (b[1] + b[3]) / 2 <= y1)
        # 四块互不重叠
        for i in range(4):
            for j in range(i + 1, 4):
                a, c = docs[i]["region"], docs[j]["region"]
                self.assertFalse(min(a[2], c[2]) - max(a[0], c[0]) > 1e-6 and min(a[3], c[3]) - max(a[1], c[1]) > 1e-6)

    def test_single_or_none(self):
        one = [t for t in OLD_PHOTO if t["box"][0] >= 990]
        self.assertEqual(ip.split_old_tickets(one, 1300, 1700), [])          # 只有一张不拆
        f = ip.fields_from_tokens(one, 1300, 1700, "ocr")
        d = ip._supplement_old(f, one, 1300, 1700)
        self.assertEqual((d["invType"], d["number"], d["total"]), ("taxi", "11223344", 104.5))
        self.assertIsNone(d["buyerName"])                   # 版式硬套的购买方丢掉
        # 增值税票不受影响：号码/代码同样能配成锚点，但票种是专票就原样返回
        vat = [_tk("广东增值税专用发票", 300, 20, 700, 50), _tk("发票代码：144000000009", 700, 60, 950, 80),
               _tk("发票号码：99887766", 700, 90, 950, 110)]
        f = ip.fields_from_tokens(vat, 1000, 600, "ocr")
        self.assertEqual(ip._supplement_old(dict(f), vat, 1000, 600)["invType"], "special")
        # 没有锚点（没代码号码）的照片：不拆、不改
        self.assertEqual(ip.split_old_tickets([_tk("某某超市小票", 10, 10, 200, 30)], 500, 500), [])

    def test_extract_image_ocr_returns_extra_docs(self):
        with patch.object(ip, "ocr_tokens", return_value=(OLD_PHOTO, 1300, 1700)),                 patch.object(ip, "_qr_layout", return_value=(0, [])):
            d = ip.extract_image_ocr(b"x")
        self.assertEqual(d["number"], "12345678")
        self.assertEqual([x["number"] for x in d["extraDocs"]], ["87654321", "87654322", "11223344"])
        self.assertTrue(d["region"] and all(x["region"] for x in d["extraDocs"]))
        self.assertFalse(d["needOcr"])


@unittest.skipUnless(SAMPLES and os.path.isdir(SAMPLES or "") and HAS_FITZ and HAS_CV2,
                     "未设置 INV_SAMPLES_DIR，跳过真实样本回归")
class TestRealSamples(unittest.TestCase):
    def _pdfs(self):
        out = []
        for f in sorted(os.listdir(SAMPLES)):
            p = os.path.join(SAMPLES, f)
            data = _read(p)
            ty = ip.sniff_type(f, data)
            if ty == "pdf":
                out.append((f, data))
            elif ty == "zip":
                for n, b in ip.unpack_zip(data):
                    if ip.sniff_type(n, b) == "pdf":
                        out.append((f + "!" + os.path.basename(n)[:12], b))
        return out

    def test_pdfs(self):
        import fitz
        import unicodedata
        keys, n_inv, n_other, fails = {}, 0, 0, []
        for name, data in self._pdfs():
            pdf = fitz.open(stream=data, filetype="pdf")
            text = unicodedata.normalize("NFKC", "".join(p.get_text() for p in pdf))
            pdf.close()
            expect_invoice = "发票号码" in text
            t = time.time()
            docs = ip.extract_pdf(data)
            el = (time.time() - t) / max(1, len(docs))
            for d in docs:
                q = ip.parse_invoice_qr(d.get("qrRaw") or "")
                line = "  %-28s %4.0fms %-7s %-8s" % (name[:28], el * 1000, d["kind"], d["invType"] or "")
                try:
                    if expect_invoice:
                        self.assertEqual(d["kind"], "invoice")
                        self.assertIsNotNone(q, "没读到二维码")
                        self.assertEqual(d["number"], q["number"])
                        self.assertEqual(d["fieldSrc"]["number"]["src"], "pdf")
                        self.assertEqual(d["total"], q["total"])
                        self.assertEqual(d["fieldSrc"]["total"]["src"], "pdf")
                        self.assertEqual(d["date"], q["date"])
                        self.assertLessEqual(abs(d["amount"] + d["tax"] - d["total"]), 0.01)
                        self.assertRegex(d["buyerTaxId"] or "", USCC_PAT)
                        self.assertRegex(d["sellerTaxId"] or "", USCC_PAT)
                        self.assertTrue(d["buyerName"] and d["sellerName"])
                        self.assertTrue(d["category"])
                        self.assertEqual(d["warnings"], [])
                        self.assertLess(el, 0.4 + 0.6)
                        n_inv += 1
                        keys.setdefault(ip.dup_key(d["code"], d["number"]), []).append(name)
                        line += " 号码✓ 合计✓ 税号✓"
                    else:
                        self.assertEqual(d["kind"], "other")
                        self.assertFalse(d["isInvoice"])
                        n_other += 1
                        line += " 非发票" + (" 审批单" if d["approvalLink"] else "") + (" 待识别" if d["needOcr"] else "")
                except AssertionError as e:
                    fails.append("%s: %s" % (name, e))
                    line += " ✗ " + str(e)[:80]
                print(line)
        dups = [v for v in keys.values() if len(v) > 1]
        print("  共 %d 张发票、%d 份非发票；查重键 %d 个，其中 %d 组是同一张票重复上传：%s"
              % (n_inv, n_other, len(keys), len(dups), "；".join("、".join(v) for v in dups)))
        self.assertFalse(fails, "\n".join(fails))
        self.assertGreater(n_inv, 0)

    def test_images_fast(self):
        for f in sorted(os.listdir(SAMPLES)):
            data = _read(os.path.join(SAMPLES, f))
            if ip.sniff_type(f, data) != "image":
                continue
            t = time.time()
            d = ip.extract_image_fast(data)
            el = time.time() - t
            print("  %-28s %4.0fms %-7s needOcr=%s" % (f[:28], el * 1000, d["kind"], d["needOcr"]))
            self.assertIn(d["kind"], ("invoice", "other"))
            if d["kind"] == "invoice":
                self.assertTrue(d["number"])
            self.assertLess(el, 1.5)


if __name__ == "__main__":
    unittest.main()
