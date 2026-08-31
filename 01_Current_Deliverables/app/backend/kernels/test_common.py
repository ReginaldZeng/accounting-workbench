# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-08-01 | Author: Claude / c | Version: V2.158
# Description: 统一取数工具箱单元测试。逐条验证业务方 2026-08-01 拍板的 6 条口径，
#   并把《取数规则_跨工具对照表 v1.0》里点名的每一处差异都做成用例——
#   老实现读错的那些格子，这里必须读对。纯确定性、无外部依赖。
#   跑法：在 kernels 目录下 `python test_common.py`
import datetime
import math
from decimal import Decimal

import _common as C

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  [OK] {name}")
    else:
        _fail += 1
        print(f"  [XX] {name}")


def eq(name, got, want):
    check(f"{name}  →  {got!r}", got == want)


# ============================================================
print("\n【口径①】空格子：金额列当 0，数量/单价列当「不算数」")
# ============================================================
for blank in (None, "", "   ", "-", "—", "--", "/", "N/A"):
    check(f"金额 {blank!r} → 0.0", C.to_amount(blank) == 0.0)
    check(f"数量 {blank!r} → 不算数", C.to_qty(blank) is None)

# 空格子是合法的「没有值」，不该刷警告——否则一张有 200 个空格的表会刷屏
w = []
C.to_amount("", warn=w)
C.to_amount(None, warn=w)
C.to_qty("—", warn=w)
eq("空格子不发警告，警告条数", len(w), 0)

# 真 0 和空格子在金额上同值，但数量列必须分得开
check("数量 0 是真 0，不是不算数", C.to_qty(0) == 0.0)
check("数量 '0' 是真 0", C.to_qty("0") == 0.0)


# ============================================================
print("\n【口径②】千分位 / 币种符号 / 空格 全认；读不懂落警告")
# ============================================================
eq("逗号千分位", C.to_amount("1,234.56"), 1234.56)
eq("空格千分位", C.to_amount("1 234.56"), 1234.56)
eq("不换行空格千分位", C.to_amount("1 1234.56".replace("1 1", "1 ")), 1234.56)
eq("人民币符号(前)", C.to_amount("¥100"), 100.0)
eq("人民币符号(全角)", C.to_amount("￥100"), 100.0)
eq("美元符号", C.to_amount("$1,000.50"), 1000.5)
eq("符号在负号后", C.to_amount("-¥100"), -100.0)          # 老实现全部读成 0
eq("负号在符号后", C.to_amount("¥-100"), -100.0)
eq("带「元」字", C.to_amount("1,234.56元"), 1234.56)
eq("全角数字", C.to_amount("１２３４"), 1234.0)
eq("全角逗号", C.to_amount("1，234.56"), 1234.56)
eq("正号", C.to_amount("+3.39"), 3.39)
eq("科学计数", C.to_amount("1.5e3"), 1500.0)

# —— 这两条是对照表里「成本台账/物流对账 静默变 0」的正解 ——
eq("成本台账踩过的坑：文本格式千分位", C.to_amount("1,234.56"), 1234.56)
eq("物流对账踩过的坑：文本格式千分位", C.to_amount("2,000"), 2000.0)

# 读不懂 → 金额落 0 但**必须**留警告，不能静默
w = []
eq("读不懂的文字 → 0", C.to_amount("待定", warn=w, where="第12行·运费"), 0.0)
check("读不懂要发警告", len(w) == 1)
check("警告要说清是哪一格", "第12行·运费" in w[0])
check("警告要带原文", "待定" in w[0])
check("警告要说处理方式", "0" in w[0])

w = []
check("数量读不懂 → 不算数", C.to_qty("见附件", warn=w, where="第3行·数量") is None)
check("数量读不懂要发警告", len(w) == 1 and "跳过" in w[0])


# ============================================================
print("\n【口径③】括号负数 (100) = -100")
# ============================================================
eq("半角括号", C.to_amount("(100)"), -100.0)
eq("全角括号", C.to_amount("（100）"), -100.0)
eq("括号+千分位", C.to_amount("(1,234.56)"), -1234.56)
eq("括号+币种符号", C.to_amount("(¥1,234.56)"), -1234.56)
eq("括号+小数", C.to_amount("(0.01)"), -0.01)
eq("数量列也认括号负数", C.to_qty("(5)"), -5.0)
# 别把无关括号当负数
w = []
eq("括号里不是数 → 0 且告警", C.to_amount("(见附件)", warn=w), 0.0)
check("括号里不是数要发警告", len(w) == 1)


# ============================================================
print("\n【口径④】账号取键：先去空格，再取最长且≥4 位")
# ============================================================
eq("整串卡号", C.norm_acct("工行6228480012345678"), "6228480012345678")
eq("带空格卡号取全（老实现取成 6228）",
   C.norm_acct("工商银行 6228 4800 1234 5678"), "6228480012345678")
eq("全角空格卡号", C.norm_acct("工行　6228　4800　1234　5678"), "6228480012345678")
eq("短数字不当账号（≥4 位）", C.norm_acct("工行 123"), "")
eq("尾号四位算账号", C.norm_acct("尾号8888"), "8888")
eq("年份与账号并存取账号", C.norm_acct("2026年6月 工行123456789"), "123456789")
eq("完全没数字", C.norm_acct("现金"), "")
eq("None", C.norm_acct(None), "")
eq("全角数字账号", C.norm_acct("工行６２２８４８００１２３４５６７８"), "6228480012345678")
# 并列最长取第一个——与老实现一致，这条不改
eq("并列最长取第一个", C.norm_acct("甲1111 乙2222".replace(" ", "／")), "1111")


# ============================================================
print("\n【口径⑤】日期全认；认不出留空 + 警告（取消原文照抄）")
# ============================================================
D = datetime.date(2026, 6, 30)
eq("date 对象", C.to_date(D), D)
eq("datetime 对象", C.to_date(datetime.datetime(2026, 6, 30, 9, 12)), D)
eq("横杠", C.to_date("2026-06-30"), D)
eq("斜杠", C.to_date("2026/06/30"), D)
eq("斜杠+单位数月日（银行流水导入原来不认）", C.to_date("2026/6/30"), D)
eq("横杠+单位数月日", C.to_date("2026-6-30"), D)
eq("点分隔", C.to_date("2026.06.30"), D)
eq("纯数字", C.to_date("20260630"), D)
eq("中文", C.to_date("2026年6月30日"), D)
eq("中文（全角数字）", C.to_date("２０２６年６月３０日"), D)
eq("带时间", C.to_date("2026-06-30 09:12:00"), D)
eq("带时间(T)", C.to_date("2026-06-30T09:12:00"), D)

# Excel 序列号：老实现里只有物流对账认，其余三条线拿到就判无效
serial = (D - C.EPOCH).days
eq("Excel 序列号(数字)", C.to_date(serial), D)
eq("Excel 序列号(浮点)", C.to_date(float(serial) + 0.5), D)
eq("Excel 序列号(文本)", C.to_date(str(serial)), D)

# 空 → 留空且不告警
w = []
for blank in (None, "", "  ", "-", "—"):
    check(f"日期 {blank!r} → 留空", C.to_date(blank, warn=w) is None)
eq("日期空格子不发警告", len(w), 0)

# 认不出 → 留空 + 告警，**绝不原文照抄**
w = []
got = C.to_date("见附件", warn=w, where="第7行·交易日期")
check("认不出的日期 → 留空（不是原文）", got is None)
check("认不出要发警告", len(w) == 1 and "第7行·交易日期" in w[0])
check("警告要带原文", "见附件" in w[0])

w = []
check("不存在的日期 2026-02-30 → 留空", C.to_date("2026年2月30日", warn=w) is None)
check("不存在的日期要发警告", len(w) == 1)

# 年份边界：老实现只认 20xx，历史补录会踩
eq("1998 年（老实现不认）", C.to_date("1998-12-31"), datetime.date(1998, 12, 31))
eq("2101 年", C.to_date("2101-01-01"), datetime.date(2101, 1, 1))
# 但「2026」这种裸年份不能被当成 Excel 序列号
w = []
check("裸年份 2026 不当序列号", C.to_date(2026, warn=w) is None)
check("裸年份要发警告", len(w) == 1)


# ============================================================
print("\n【NaN 防护】老实现 reconcile/balance_dashboard 漏掉的一道")
# ============================================================
nan = float("nan")
got = C.to_amount(nan)
check("NaN 金额 → 0.0（老实现会透传 NaN 毒化整个合计）", got == 0.0)
check("NaN 数量 → 不算数", C.to_qty(nan) is None)
check("NaN 文本 → 空串（不是 'nan' 四个字母）", C.to_text(nan) == "")
check("无穷大 → 0.0", C.to_amount(float("inf")) == 0.0)
check("字符串 'nan' → 0.0", C.to_amount("nan") == 0.0)
check("字符串 'inf' → 0.0", C.to_amount("inf") == 0.0)
check("NaN 日期 → 留空", C.to_date(nan) is None)
# 合计不会被毒化
rows = [100.0, nan, 200.0, "", "1,234.56"]
total = sum(C.to_amount(r) for r in rows)
check(f"含 NaN 的合计仍是有限数 {total}", math.isfinite(total) and abs(total - 1534.56) < 1e-9)

# 布尔值不能被当 1/0 悄悄加进合计
w = []
eq("True 不当 1", C.to_amount(True, warn=w), 0.0)
check("布尔值要发警告", len(w) == 1)


# ============================================================
print("\n【文本】to_text")
# ============================================================
eq("None", C.to_text(None), "")
eq("两端空白", C.to_text("  甲公司  "), "甲公司")
eq("数字", C.to_text(123), "123")


# ============================================================
print("\n【口径⑥】判平容差：收到一处 + 可配")
# ============================================================
C.reset_tolerances()
eq("默认勾稽容差", C.tol("amount_abs"), 0.01)
eq("默认配对绝对容差", C.tol("match_amount_abs"), 1.0)
eq("默认配对相对容差", C.tol("match_amount_rel"), 0.02)
eq("默认重量容差", C.tol("weight_abs"), 0.001)
eq("默认汇率偏离", C.tol("fx_deviation_rel"), 0.03)

check("差 1 分算平", C.close_enough(100.00, 100.01))
check("差 2 分不算平", not C.close_enough(100.00, 100.02))
check("相对容差：差 2% 内算平", C.close_enough(1000.0, 1015.0, rel_tol=0.02))
check("相对容差：差 2% 外不算平", not C.close_enough(1000.0, 1025.0, rel_tol=0.02))
check("绝对与相对取大者", C.close_enough(10.0, 10.5, abs_tol=1.0, rel_tol=0.001))
check("容差比对也走统一取数", C.close_enough("1,000.00", 1000.0))
check("空格子与 0 判平", C.close_enough("", 0))

# 设置页改容差
C.configure_tolerances({"amount_abs": 0.5})
eq("改后生效", C.tol("amount_abs"), 0.5)
check("改后差 3 角算平", C.close_enough(100.0, 100.3))
# 填错不能把全台判平搞乱
C.configure_tolerances({"amount_abs": "手滑", "不存在的项": 1, "match_amount_abs": -5})
eq("填错的值不生效", C.tol("amount_abs"), 0.5)
eq("负容差不生效", C.tol("match_amount_abs"), 1.0)
C.reset_tolerances()
eq("恢复默认", C.tol("amount_abs"), 0.01)
try:
    C.tol("拼错的键")
    check("未知容差项要报错", False)
except KeyError:
    check("未知容差项要报错（不能默默返回 0）", True)


# ============================================================
print("\n【回归】对照表里点名的 5 处差异，新工具箱统一后的结果")
# ============================================================
# ①空格子：金额一律 0（笔数完整），数量一律不算数
eq("空金额", C.to_amount(""), 0.0)
check("空单价", C.to_qty("") is None)
# ②千分位：全台都认
eq("千分位", C.to_amount("1,234.56"), 1234.56)
# ③括号负数：全台都认
eq("括号负数", C.to_amount("(100)"), -100.0)
# ④账号：一把尺子
eq("账号", C.norm_acct("工行 6228 4800 1234 5678"), "6228480012345678")
# ⑤日期：Excel 序列号全台都认，认不出留空
eq("Excel 序列号", C.to_date(46203), datetime.date(2026, 6, 30))
check("认不出留空", C.to_date("见附件") is None)


# ============================================================
print("\n【类型】Decimal 进出（汇率录入用 Decimal，接口处要能互通）")
# ============================================================
eq("Decimal 金额", C.to_amount(Decimal("1234.56")), 1234.56)
check("Decimal NaN → 0", C.to_amount(Decimal("NaN")) == 0.0)
eq("Decimal 日期序列", C.to_date(Decimal(46203)), datetime.date(2026, 6, 30))


if __name__ == "__main__":
    print(f"\n{'=' * 46}")
    print(f"  通过 {_pass} 项，失败 {_fail} 项")
    print(f"{'=' * 46}")
    raise SystemExit(1 if _fail else 0)
