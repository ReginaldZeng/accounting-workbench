# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-08-06 | Author: Claude / c | Version: V2.201
# Description: 疑记错户两腿并一行（需求方指出双行=重复计数）——银行侧列钱、金蝶侧列凭证/制单人，
#              计数=真实错误个数；整户缺流水的金蝶腿口径不变。内部往来仍两行（两户各补一腿=两个动作）。
# Date: 2026-08-06 | Author: Claude / c | Version: V2.200
# Description: 疑记错户双腿互指——金蝶腿(两侧有数据的账户上)不再留在"金蝶单边·疑似做错"，与银行腿双双
#              标疑记错户、共享两栏明细；整户缺流水的金蝶腿仍保持账户级状态。
# Date: 2026-08-06 | Author: Claude / c | Version: V2.196
# Description: 内部往来明细补对方腿 日期/制单人/摘要——内部往来行点开详情：收支两腿对照、未做账腿高亮。
# Date: 2026-08-06 | Author: Claude / c | Version: V2.195
# Description: 组合成员腿补「制单人」、摘要放宽到40字——组合待确认行点开详情逐张列 日期/金额/凭证/制单人/摘要。
# Date: 2026-08-06 | Author: Claude / c | Version: V2.194
# Description: 疑记错户行输出「记错户明细」结构化两栏（钱在哪里=银行实走腿 / 账在哪里=金蝶挂错的户+凭证+制单人），
#   前端点开详情对照显示并给更正指引。
# Date: 2026-08-03 | Author: Claude / c | Version: V2.180
# Description: ①组合行输出「组合明细」整组等式（目标+全部成员的凭证/金额/摘要），组内每行可见完整等式；
#   ②新状态「疑记错户·账在他户」（misbook）：同额同向异户日近 + 银行对方户名命中他户凭证摘要（强证据）
#   → 银行腿标出并附对方凭证线索；金蝶腿保持原状态。案例：黄一飞备用金返还3000到宁波户、记-333记在招商户。
# Date: 2026-08-03 | Author: Claude / c | Version: V2.178
# Description: 新状态「组合待确认」（combo_pending）——组合候选(1:N/N:1 合计4位分毫不差)两侧都已做账，
#   不再归疑似漏账/金蝶单边（那是"有问题"的追人清单，混入会自相矛盾+淹掉真问题）；仍交人工确认不自动核销。
# Date: 2026-08-03 | Author: Claude / c | Version: V2.172
# Description: ①内部往来行输出「内部往来明细」结构化字段（对方账号/主体/开户行/方向/凭证/同向），
#   前端据此把两条腿各标 已做账/未做账：真划转=收方户/支方户，同向=钱走哪个户/账记哪个户（疑记错户）。
#   ②新增 _flag_bank_pair「两腿均未做账」侦测：两户银行一收一支同额同窗、金蝶两边查无 → 双双标出
#   （此前显示为两条互不相认的疑似漏账）；对方户名闸+划转字眼防金额巧合。
# Date: 2026-08-03 | Author: Claude / c | Version: V2.171
# Description: 「内部往来·未做账」加对方户名闸（reconcile 新参 group_names）——对方是外部名称
#   （个人/税务局/供应商）不猜内部往来，金额巧合误报回归疑似漏账；对方空白/集团主体保持原判
#   （资金归集、账号维度记错场景不受影响，方向不设限）。不传 group_names 行为不变（向后兼容）。
# Date: 2026-08-03 | Author: Claude / c | Version: V2.169
# Description: 金蝶行透传「制单人」（Rec.maker，来自取数新列 FCREATORID.FName），
#   结果行输出加 制单人 列——做错金额/晚记等差异直接亮出经手人。旧定格数据无此列时为空。
# Date: 2026-07-06 | Author: Claude / c | Version: V2.32
# Description: 币别口径：金蝶侧对账金额改用原币(FAMOUNTFOR)——FDEBIT/FCREDIT 是账簿本位币(境外簿=美元)，
#   银行流水是账户原币，港币户/境外簿人民币户此前对不上。新增状态 fx_adjust「汇兑损益·账面调整」
#   (原币=0本位币有数的期末重估行，本无银行流水，单列不参与配对)；行输出加 币别/本位币金额/汇率；
#   kd_delta_for() 供余额调节等按原币汇总。无原币字段(样例数据)自动退回本位币，口径不变。
# Date: 2026-07-03
# Author: Claude / c
# Version: V1.1
# Description: 银行—金蝶逐笔稽核 确定性内核 v2（按《需求确认书 v1.3》§6 重写）。
#              九态：已匹配 / 晚记·本月 / 跨期晚记 / 做错·金额 / 疑似漏账 / 金蝶单边·疑似做错 /
#              账户缺银行流水 / 账户缺金蝶数据 / 账号对不上台账。
#              金额比对到 4 位小数（金蝶设置有时产生 4 位，属正常，废止旧「精度异常」）。
#              晚记以「当天」为准（金蝶记账日 = 银行日 = 准时；晚 ≥1 天 = 晚记，跨会计月 = 跨期晚记）。
#              每笔配对带置信度（高/中/低）；对未匹配两侧做「组合候选」侦测（1:N / N:1，只标不自动核销）。
#              纯确定性、可单元测试、不含任何 LLM 调用。
"""
逐笔稽核内核 v2 / reconcile

对齐口径（银行存款 1002 科目视角）：
  银行「收入」= 金蝶「借方」(存款增加) → 标准方向「收」
  银行「支出」= 金蝶「贷方」(存款减少) → 标准方向「支」

七态（每笔银行/金蝶行恰好归入一类）：
  matched       已匹配        同账户+同方向+金额(4位)相等，金蝶日 = 银行日（当天·准时）
  late_month    晚记·本月     配对上，金蝶日晚于银行日 ≥1 天，未跨会计月
  late_cross    跨期晚记      配对上，金蝶日跨了会计月才入账
  amount_wrong  做错·金额     配对上但金额不等（比对到 4 位小数，显示差额）
  bank_leak     疑似漏账      共同账户内，银行有、金蝶整取数窗口查无（账户已对上台账）—— 优先核
  kd_only       金蝶单边·疑似做错 共同账户内，金蝶有、银行无 —— 以银行为准，很大概率金蝶做错（重复/错账户/错记），待核/更正
  no_bank_acct  账户缺银行流水 整个账户金蝶有数据、银行没导对账单（如花旗美元户）—— 待补数据源
  no_kd_acct    账户缺金蝶数据 整个账户银行有流水、金蝶该账户查无 —— 待补取数范围/确认未记账
  unmapped      账号对不上台账 账号无法在台账唯一命中（掩码/短号/查无/多义）—— 单列不定性

护栏：Σ收/Σ支/笔数自校验；每笔恰归一类、不重复消费。
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime
from itertools import combinations
import re


def amt_key(x) -> int:
    """金额到 4 位小数的整数键（万分之一），避免浮点误差。"""
    return int(round(to_float(x) * 10000))


# ----------------------------- 数据结构 -----------------------------
@dataclass
class Rec:
    """统一后的一笔（银行或金蝶）。amount 恒为正，direction ∈ {'收','支'}。"""
    side: str
    acct: str
    acct_raw: str
    holder: str
    d: "date | None"
    direction: str
    amount: float          # 正数，保留 4 位小数
    memo: str = ""
    counterparty: str = ""
    voucher: str = ""
    subject: str = ""
    bank_name: str = ""
    mapped: bool = True
    idx: int = -1
    matched: bool = False
    combo: str = ""        # 组合候选标记（说明文本）
    xfer: bool = False     # 内部往来·未做账 标记（跨账号识别的集团划转未记账腿）
    xfer_ref: str = ""     # 对应的另一侧金蝶凭证/账户（供核算组补做账定位）
    currency: str = ""     # 币别（金蝶行 FCURRENCYID；银行行由账号回填）
    amount_base: float = 0.0   # 金蝶行的本位币金额（外币户与 amount(原币) 不同；银行行恒 0）
    rate: float = 0.0      # 金蝶行汇率（原币→本位币）
    fx: bool = False       # 汇兑损益·账面调整（原币=0 本位币有数，期末重估，无银行流水对应）
    maker: str = ""        # 金蝶行制单人（V2.169，差异行亮出经手人；银行行恒空）
    xfer_info: dict = None  # 内部往来对方腿结构化信息（V2.172：账号/主体/方向/凭证/同向，供前端列两腿）
    combo_info: dict = None  # 组合整组等式（V2.179：目标+全部成员的凭证/金额，人在工作台一眼判组合）
    misbook: bool = False   # 疑记错户·账在他户（V2.180：钱到本户、凭证记在别的户；银行腿标）
    misbook_ref: str = ""   # 对应的他户凭证线索（账号尾号+凭证号+摘要片段）
    misbook_info: dict = None  # 记错户结构化明细（V2.194：钱在哪里/账在哪里 两栏对照，点开详情用）


# ----------------------------- 工具 -----------------------------
def norm_acct(text) -> str:
    if text is None:
        return ""
    runs = re.findall(r"\d+", str(text))
    return max(runs, key=len) if runs else ""


def ledger_index(ledger_rows) -> dict:
    """《账户台账》-> {账号数字: [银行账户行]}。仅收录 类别=银行账户 且有账号 的行。"""
    idx: dict = {}
    for r in (ledger_rows or []):
        a = str(r.get("账号", "") or "")
        if a and r.get("类别", "银行账户") == "银行账户":
            idx.setdefault(a, []).append(r)
    return idx


def resolve_acct(raw, index, subject_hint: str = ""):
    """账号 -> (规范账号, 台账行or None, 是否已映射)。无台账透传；唯一命中→映射；
    多主体主体消歧；短号/掩码/台账没有/多义→未映射(不瞎配)。"""
    digits = norm_acct(raw)
    if not index:
        return digits, None, True
    if not digits:
        return digits, None, False
    cands = index.get(digits)
    if not cands:
        return digits, None, False
    if len(cands) == 1:
        return digits, cands[0], True
    if subject_hint:
        sub = [c for c in cands if str(c.get("主体", "")) and
               (subject_hint in str(c.get("主体", "")) or str(c.get("主体", "")) in subject_hint)]
        if len(sub) == 1:
            return digits, sub[0], True
    return digits, None, False


def to_float(x) -> float:
    if x is None or x == "":
        return 0.0
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).replace(",", "").replace("¥", "").replace("￥", "").strip()
    if s in ("", "-", "—"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def to_date(x):
    if x is None or x == "":
        return None
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    s = str(x).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _get(r: dict, *keys):
    for k in keys:
        if k in r and r[k] not in (None, ""):
            return r[k]
    low = {str(k).strip().lower(): v for k, v in r.items()}
    for k in keys:
        v = low.get(str(k).strip().lower())
        if v not in (None, ""):
            return v
    return None


# ----------------------------- 归一化 -----------------------------
def bank_to_recs(rows, index=None):
    out = []
    for i, r in enumerate(rows):
        inflow = to_float(_get(r, "收入", "收入金额", "贷方", "贷方金额"))
        outflow = to_float(_get(r, "支出", "支出金额", "借方", "借方金额"))
        if inflow > 0 and outflow > 0:
            net = inflow - outflow
            direction, amount = ("收", net) if net >= 0 else ("支", -net)
        elif inflow > 0:
            direction, amount = "收", inflow
        elif outflow > 0:
            direction, amount = "支", outflow
        else:
            continue
        acct_raw = str(_get(r, "账号", "银行账号", "账户") or "")
        holder = str(_get(r, "户名", "账户名称", "主体") or "")
        canon, lrow, mapped = resolve_acct(acct_raw, index, subject_hint=holder)
        out.append(Rec(
            side="bank", acct=canon, acct_raw=acct_raw, holder=holder,
            d=to_date(_get(r, "交易日期", "日期", "记账日期")),
            direction=direction, amount=round(abs(amount), 4),
            memo=str(_get(r, "摘要", "摘要说明") or ""),
            counterparty=str(_get(r, "对方户名", "对方", "对方名称") or ""),
            subject=(lrow.get("主体", "") if lrow else ""),
            bank_name=(lrow.get("开户行", "") if lrow else ""),
            mapped=mapped, idx=i,
        ))
    return out


def kd_delta_for(r) -> float:
    """一行金蝶序时账的净发生（原币口径，借+/贷-），供余额调节等汇总用。
    有 FAMOUNTFOR(原币金额) 用原币——外币户凭证 FDEBIT/FCREDIT 是账簿本位币(境外簿=美元)，
    与银行流水(账户原币)不同币；无原币字段(样例数据/老口径)退回本位币。汇兑重估行原币=0 天然不计。"""
    debit = to_float(_get(r, "FDEBIT", "借方", "借方金额"))
    credit = to_float(_get(r, "FCREDIT", "贷方", "贷方金额"))
    if any(k in r for k in ("FAMOUNTFOR", "原币金额")):
        amount_for = abs(to_float(_get(r, "FAMOUNTFOR", "原币金额")))
        return amount_for if debit > 0 else (-amount_for if credit > 0 else 0.0)
    return debit - credit


def kd_to_recs(rows, index=None):
    out = []
    for i, r in enumerate(rows):
        debit = to_float(_get(r, "FDEBIT", "借方", "借方金额", "借方原币"))
        credit = to_float(_get(r, "FCREDIT", "贷方", "贷方金额", "贷方原币"))
        if debit > 0:
            direction, base = "收", debit
        elif credit > 0:
            direction, base = "支", credit
        else:
            continue
        # 币别口径：银行流水是账户原币，FDEBIT/FCREDIT 是账簿本位币(境外簿本位币=美元)——
        # 港币户/境外簿人民币户两边不同币。有 FAMOUNTFOR(原币金额)按原币对账；
        # 原币=0 而本位币有数 = 期末汇兑损益重估(账面调整，本无银行流水)，单列不参与配对。
        has_for = any(k in r for k in ("FAMOUNTFOR", "原币金额"))
        amount_for = abs(to_float(_get(r, "FAMOUNTFOR", "原币金额"))) if has_for else 0.0
        currency = str(_get(r, "FCURRENCYID.FName", "币别") or "")
        rate = to_float(_get(r, "FEXCHANGERATE", "汇率"))
        fx = has_for and amt_key(amount_for) == 0 and amt_key(base) != 0
        amount = amount_for if (has_for and not fx) else base
        acct_raw = str(_get(r, "核算维度.银行账号.编码", "核算维度.银行账号.名称",
                            "FDetailID.FF100002.FNumber", "FDetailID.FF100002.FName",
                            "核算维度·银行账号", "银行账号", "账号", "账户") or "")
        vg = str(_get(r, "FVOUCHERGROUPID.FName", "凭证字", "凭证字号") or "").strip()
        vno = str(_get(r, "FVOUCHERGROUPNO", "凭证号", "凭证编号") or "").strip()
        voucher = (f"{vg}-{vno}" if vg and vno else (vg or vno))
        holder = str(_get(r, "户名", "账户名称", "账簿", "FACCOUNTBOOKID.FName") or "")
        canon, lrow, mapped = resolve_acct(acct_raw, index, subject_hint=holder)
        out.append(Rec(
            side="kd", acct=canon, acct_raw=acct_raw, holder=holder,
            d=to_date(_get(r, "FDATE", "日期", "记账日期")),
            direction=direction, amount=round(abs(amount), 4),
            memo=str(_get(r, "FEXPLANATION", "摘要") or ""),
            counterparty="",
            subject=(lrow.get("主体", "") if lrow else ""),
            bank_name=(lrow.get("开户行", "") if lrow else ""),
            mapped=mapped, voucher=voucher, idx=i,
            currency=currency, amount_base=round(base, 4), rate=rate, fx=fx,
            maker=str(_get(r, "制单人", "FCREATORID.FName") or ""),
        ))
    return out


# ----------------------------- 匹配辅助 -----------------------------
def _day_diff(a, b) -> int:
    if a is None or b is None:
        return 10 ** 6
    return abs((a - b).days)


def _lag(bank_d, kd_d) -> int:
    """金蝶日 − 银行日（天）。正=金蝶晚记，0=当天准时，负=金蝶提前。"""
    if bank_d is None or kd_d is None:
        return 0
    return (kd_d - bank_d).days


def _same_month(a, b) -> bool:
    return bool(a and b and a.year == b.year and a.month == b.month)


def _norm_text(s) -> str:
    return re.sub(r"\s+", "", str(s or ""))


def _overlap(a, b) -> bool:
    """对方/摘要是否有实质重合：共享 4+ 位数字串，或共享 2 字中文片段。"""
    a, b = _norm_text(a), _norm_text(b)
    if not a or not b:
        return False
    for d in re.findall(r"\d{4,}", a):
        if d in b:
            return True
    for seg in re.findall(r"[一-鿿]{2,}", a):
        for i in range(len(seg) - 1):
            if seg[i:i + 2] in b:
                return True
    return False


def _confidence(b: Rec, k: Rec, lag: int, exact: bool) -> str:
    ov = _overlap((b.counterparty or "") + (b.memo or ""), k.memo or "")
    near = abs(lag) <= 3
    if exact and ov and near:
        return "高"
    if (exact and near) or (ov and abs(lag) <= 7):
        return "中"
    return "低"


# ----------------------------- 主流程 -----------------------------
def reconcile(bank, kd, pair_window_days: int = 60,
              amount_tol_ratio: float = 0.02, amount_tol_abs: float = 1.0,
              group_names=None):
    """执行逐笔匹配。返回 (results, summary)。
    pair_window_days: 精确配对可跨的最大天数（宽窗，抓跨月晚记，不因日期远误判漏账）。
    amount_tol_*: 「做错·金额」配对的接近阈值（相对/绝对取大者）。
    group_names: 集团主体名称集合（V2.171）——给「内部往来」侦测做对方户名闸；
                 不传(None)保持旧行为（样例模式/旧调用向后兼容）。"""
    results = []

    # ①.0 汇兑损益·账面调整：原币=0 而本位币有数（期末汇率重估）——账面调整本无银行流水，
    #     单列不参与配对（进配对会永远配不上、被误判金蝶单边·疑似做错）。
    for r in kd:
        if r.fx:
            r.matched = True
            results.append(_row("fx_adjust", None, r))

    # ① 账号对不上台账：未映射的银行/金蝶行单列，不参与漏账/单边定性
    for r in bank:
        if not r.mapped:
            r.matched = True
            results.append(_row("unmapped", r, None))
    for r in kd:
        if not r.mapped and not r.matched:      # 汇兑重估行已在 ①.0 单列，不重复归类
            r.matched = True
            results.append(_row("unmapped", None, r))

    # ①.5 账户覆盖预检：某账户只有一侧有数据 → 单列「缺银行流水／缺金蝶数据」，
    #     不放进 疑似漏账／金蝶单边（那是共同账户内的逐行结论；整户缺数据混进来会淹没真异常）。
    bank_accts = {r.acct for r in bank if r.mapped}
    kd_accts = {r.acct for r in kd if r.mapped}
    for r in bank:
        if r.mapped and not r.matched and r.acct not in kd_accts:
            r.matched = True
            results.append(_row("no_kd_acct", r, None))      # 银行有此账户、金蝶查无 → 账户缺金蝶数据
    kd_nobank = []                                           # 缺银行流水户的金蝶行——供「疑记错户」侦测比对（V2.180）
    for r in kd:
        if r.mapped and not r.matched and r.acct not in bank_accts:
            r.matched = True
            r._nobank = True                                 # V2.200：标记账户级缺流水，疑记错户互指时跳过
            kd_nobank.append(r)
            results.append(_row("no_bank_acct", None, r))    # 金蝶有此账户、银行没导对账单 → 账户缺银行流水

    # ② 起：仅对两侧都有数据的账户做逐笔匹配
    accts = sorted(bank_accts & kd_accts)
    all_left_b, all_left_k = [], []          # 跨账号内部往来侦测用，全局收集剩余
    for acct in accts:
        b = [r for r in bank if r.mapped and r.acct == acct and not r.matched]
        k = [r for r in kd if r.mapped and r.acct == acct and not r.matched]

        # ② 精确配对：同方向 + 金额(4位)相等，日期就近且 ≤ pair_window_days
        bucket = {}
        for r in k:
            bucket.setdefault((r.direction, amt_key(r.amount)), []).append(r)
        for br in sorted(b, key=lambda r: (r.d or date.max, r.amount)):
            cands = [x for x in bucket.get((br.direction, amt_key(br.amount)), []) if not x.matched]
            if not cands:
                continue
            best = min(cands, key=lambda x: _day_diff(br.d, x.d))
            if _day_diff(br.d, best.d) > pair_window_days:
                continue
            br.matched = best.matched = True
            lag = _lag(br.d, best.d)
            conf = _confidence(br, best, lag, exact=True)
            if len(cands) > 1 and conf == "高":
                conf = "中"                       # 同额多候选 → 降置信
            if lag <= 0:
                status = "matched"                # 当天(或金蝶提前) → 准时
            elif _same_month(br.d, best.d):
                status = "late_month"             # 晚 ≥1 天，本月内
            else:
                status = "late_cross"             # 跨会计月
            results.append(_row(status, br, best, confidence=conf, lag=lag))

        # ③ 做错·金额：同方向、日期就近(≤3天)、金额接近但不等
        for br in sorted([r for r in b if not r.matched], key=lambda r: (r.d or date.max, r.amount)):
            cands = [x for x in k if (not x.matched and x.direction == br.direction and _day_diff(br.d, x.d) <= 3)]
            if not cands:
                continue
            best = min(cands, key=lambda x: (abs(x.amount - br.amount), _day_diff(br.d, x.d)))
            diff = abs(best.amount - br.amount)
            tol = max(amount_tol_abs, max(br.amount, best.amount) * amount_tol_ratio)
            if 0 < diff <= tol:
                br.matched = best.matched = True
                lag = _lag(br.d, best.d)
                results.append(_row("amount_wrong", br, best,
                                    confidence=_confidence(br, best, lag, exact=False), lag=lag))

        # ④ 剩余：同账户组合候选（1:N）先标；跨账号内部往来留到全局 pass
        leftover_b = [r for r in b if not r.matched]
        leftover_k = [r for r in k if not r.matched]
        _flag_combo(leftover_b, leftover_k)
        all_left_b.extend(leftover_b)
        all_left_k.extend(leftover_k)

    # ⑤ 跨账号内部划转甄别：
    #   银行侧 → 银行有资金动、金蝶该户未记，同额在别处以划转特征入账 → 内部往来·未做账
    #   金蝶侧 → 金蝶单边里含划转特征、对手腿在别账户（银行流水/金蝶对开）→ 内部划转·对应他账户（非漏导）
    _flag_internal(all_left_b, kd, group_names=group_names)
    _flag_bank_pair(all_left_b, group_names=group_names)   # 两腿均未做账（金蝶有据的优先，故排其后）
    _flag_misbook(all_left_b, all_left_k + kd_nobank)      # 疑记错户：对方户名命中他户凭证摘要（V2.180）
    _flag_kd_crossacct(all_left_k, all_left_b)
    # V2.178（需求方定）：组合候选（合计4位分毫不差的 1:N / N:1）两侧都已有账，单列「组合待确认」——
    # 不再喊成疑似漏账/金蝶单边（引擎自己都算出对上了还报错漏，自相矛盾）；仍交人工确认、不自动核销。
    for br in all_left_b:
        br.matched = True
        st = ("xfer_unbooked" if br.xfer else "misbook" if br.misbook
              else "combo_pending" if br.combo else "bank_leak")
        results.append(_row(st, br, getattr(br, "misbook_pair", None)))   # V2.201 记错户两腿并一行
    for kr in all_left_k:
        kr.matched = True
        if kr.misbook:
            continue                                 # V2.201：已并入疑记错户成对行，不再单独出行
        st = "kd_xfer" if kr.xfer else ("combo_pending" if kr.combo else "kd_only")
        results.append(_row(st, None, kr))

    # 币别回填：银行流水行本身不带币别，按账号取该户金蝶行的币别（一户一币）
    acct_cur = {}
    for r in kd:
        if r.currency and r.acct and r.acct not in acct_cur:
            acct_cur[r.acct] = r.currency
    for row in results:
        if not row.get("币别"):
            row["币别"] = acct_cur.get(row["账号"], "")

    # 排序 = 按账号分组、组内按银行流水日期升序（无银行日期的金蝶单边用其日期字段兜底沉到组内靠后），
    # 同账号同日再按状态优先级稳定排。全局序号(前端 _seq)随此顺序 → 每账户流水连续不断号、由早到晚。
    results.sort(key=lambda x: (x["账号"], x["日期"] or "9999-99-99", _ORDER[x["status"]]))
    return results, summarize(bank, kd, results)


# ----------------------------- 组合候选（1:N / N:1） -----------------------------
def _flag_combo(bank_left, kd_left, window: int = 5, max_parts: int = 12):
    """在未匹配两侧侦测「一笔=多笔之和」，只在 Rec.combo 打标记，不消费、不核销。
    典型：理财赎回 银行 1 笔 = 金蝶「本金+收益」多张（1:N）。
    V2.179：整组等式（目标+全部成员的凭证/金额/摘要）存进 combo_info——
    组内每一行都带完整等式，人在工作台直接判组合，不必回金蝶凑数。"""
    def _leg(r):
        return {"日期": (r.d.isoformat() if r.d else ""), "金额": round(r.amount, 4),
                "凭证": r.voucher or "", "制单人": r.maker or "", "摘要": (r.memo or "")[:40]}
    def detect(targets, parts, kind, tside, pside):
        for t in targets:
            if t.combo:
                continue
            pool = [p for p in parts if p.direction == t.direction and not p.combo
                    and _day_diff(t.d, p.d) <= window]
            if not (2 <= len(pool) <= max_parts):
                continue
            hit = None
            for n in (2, 3):
                for combo in combinations(pool, n):
                    if amt_key(sum(p.amount for p in combo)) == amt_key(t.amount):
                        hit = combo
                        break
                if hit:
                    break
            if hit:
                info = {"类型": kind, "目标侧": tside, "成员侧": pside,
                        "目标": _leg(t), "成员": [_leg(p) for p in hit],
                        "合计": round(sum(p.amount for p in hit), 4)}
                t.combo = kind
                t.combo_info = info
                for p in hit:
                    p.combo = kind
                    p.combo_info = info
    detect(bank_left, kd_left, "1:N 银行1笔=金蝶多张", "银行", "金蝶")
    detect(kd_left, bank_left, "N:1 多笔银行=金蝶1张", "金蝶", "银行")


# ----------------------------- 内部往来·未做账（跨账号） -----------------------------
_XFER = re.compile(r"转入|转出|调拨|归集|内部往来|往来款|往来|划转|资金归集|集团内")


def _flag_internal(bank_left, kd_all, window: int = 10, group_names=None):
    """跨账号「内部往来·未做账」侦测。
    银行某户有资金动、金蝶该户查无，但**同额(4位)在别的账户以划转特征入了账** →
    判为集团内部划转的未做账腿：性质是内部往来（非外部漏账），但金蝶这头确实没做账、仍需补记。
    只打标 + 附对应凭证，不自动核销。
    V2.171 对方户名闸：流水对方户名非空且不在集团主体名单（个人/税务局/供应商等外部名称）
    → 不猜内部往来，留在疑似漏账如实交人工（7月实测：曹水英2800/税款6909两笔金额巧合误报）。
    对方为空白不设限——资金归集/账号维度记错的真场景常无对方户名。方向亦不设限（同向=维度记错腿）。"""
    xfers = [k for k in kd_all if _XFER.search(str(k.memo or ""))]
    for br in bank_left:
        if br.combo:                       # 已是同账户 1:N 组合候选，不重复判
            continue
        cp = str(br.counterparty or "").strip()
        if group_names and cp and not any(g and ((g in cp) or (cp in g)) for g in group_names):
            continue                       # 对方是外部名称 → 不猜
        for k in xfers:
            if k.acct and br.acct and k.acct == br.acct:
                continue                   # 同账户不算内部往来
            if amt_key(k.amount) == amt_key(br.amount) and _day_diff(br.d, k.d) <= window:
                br.xfer = True
                ref = k.voucher or ""
                if k.acct:
                    ref = (ref + " " if ref else "") + "对方账户…" + str(k.acct)[-6:]
                br.xfer_ref = ref or "金蝶另户已记划转"
                br.xfer_info = {"对方账号": k.acct, "对方主体": k.subject, "对方开户行": k.bank_name,
                                "对方方向": k.direction, "对方凭证": k.voucher,
                                "对方日期": (k.d.isoformat() if k.d else ""),
                                "对方制单人": k.maker or "", "对方摘要": (k.memo or "")[:40],
                                "同向": k.direction == br.direction}   # 同向=钱走本户账记他户（疑账号维度记错）
                break


def _flag_bank_pair(bank_left, group_names=None, window: int = 10):
    """「两腿均未做账」的内部划转侦测（V2.172，需求方点名）：两个账户的银行流水一收一支、
    同额(4位)、日期差≤window，金蝶两边都查无 → 双双标「内部往来·未做账」，明细注明两边账都没做。
    防金额巧合三道闸：①每腿对方户名须为空或集团主体（外部名称不猜）；
    ②两腿对方都空白时，还须任一腿摘要带划转字眼；③一腿只配一次。"""
    def cp_ok(r):
        cp = str(r.counterparty or "").strip()
        if not cp:
            return True, False
        if group_names and any(g and ((g in cp) or (cp in g)) for g in group_names):
            return True, True
        return False, False
    for i, a in enumerate(bank_left):
        if a.xfer or a.combo:
            continue
        ok_a, strong_a = cp_ok(a)
        if not ok_a:
            continue
        for b2 in bank_left[i + 1:]:
            if b2.xfer or b2.combo or not b2.acct or not a.acct or b2.acct == a.acct:
                continue
            if b2.direction == a.direction or amt_key(a.amount) != amt_key(b2.amount):
                continue
            if _day_diff(a.d, b2.d) > window:
                continue
            ok_b, strong_b = cp_ok(b2)
            if not ok_b:
                continue
            if not (strong_a or strong_b or _XFER.search(str(a.memo or "")) or _XFER.search(str(b2.memo or ""))):
                continue               # 双空白又无划转字眼 → 不猜
            for x, y in ((a, b2), (b2, a)):
                x.xfer = True
                x.xfer_ref = "对方账户…" + str(y.acct)[-6:] + " 银行亦动·两边均未做账"
                x.xfer_info = {"对方账号": y.acct, "对方主体": y.subject or y.holder,
                               "对方开户行": y.bank_name, "对方方向": y.direction,
                               "对方凭证": "", "对方日期": (y.d.isoformat() if y.d else ""),
                               "对方制单人": "", "对方摘要": (y.memo or "")[:40],
                               "对方是银行流水": True, "两腿均未做": True, "同向": False}
            break


def _flag_misbook(bank_left, kd_candidates, window: int = 3):
    """「疑记错户·账在他户」侦测（V2.180，需求方案例：黄一飞备用金返还）——
    钱到了本户、凭证却记在另一个户的账号维度上：同额(4位)同方向、日期差≤window、异户，
    且【银行对方户名(≥2字)出现在金蝶摘要里】才认——人名/单位名对上是强证据，防金额巧合。
    只标银行腿并给出对方凭证线索（人工核后去改凭证的账号维度）；
    金蝶腿保持原状态（多为整户缺银行流水，账户级事实不动）。"""
    for br in bank_left:
        if br.xfer or br.combo or br.misbook:
            continue
        cp = str(br.counterparty or "").strip()
        if len(cp) < 2:
            continue
        for k in kd_candidates:
            if getattr(k, "misbook", False):
                continue                       # 一张凭证只认领一次
            if not k.acct or k.acct == br.acct or k.direction != br.direction:
                continue
            if amt_key(k.amount) != amt_key(br.amount) or _day_diff(br.d, k.d) > window:
                continue
            if cp not in str(k.memo or ""):
                continue
            br.misbook = True
            br.misbook_ref = ("账已记在 …" + str(k.acct)[-6:]
                              + (f" {k.voucher}" if k.voucher else "")
                              + (f"（{str(k.memo)[:18]}）" if k.memo else ""))
            # V2.200：金蝶腿若在两侧都有数据的账户上（原会落"金蝶单边·疑似做错"），也标疑记错户并互指——
            # 凭证内容没错、只是户挂错，留在"疑似做错"会冤枉制单人（需求方 650,000 记-369 实例）。
            # 整户缺银行流水的金蝶腿仍保持账户级状态不动。
            if not getattr(k, "_nobank", False):
                k.misbook = True                    # V2.201：并入银行腿成一行（一个错=一行），不再单独出行
                br.misbook_pair = k
            _mi_share = not getattr(k, "_nobank", False)
            br.misbook_info = {   # V2.194：点开详情——钱在哪里 / 账在哪里
                "钱在": {"账号": br.acct, "开户行": br.bank_name, "主体": br.subject,
                         "日期": (br.d.isoformat() if br.d else ""), "方向": br.direction,
                         "金额": round(br.amount, 4), "对方": br.counterparty,
                         "摘要": (br.memo or "")[:30]},
                "账在": {"账号": k.acct, "开户行": k.bank_name, "主体": k.subject,
                         "日期": (k.d.isoformat() if k.d else ""), "方向": k.direction,
                         "金额": round(k.amount, 4), "凭证": k.voucher, "制单人": k.maker,
                         "摘要": (k.memo or "")[:30]},
            }
            if _mi_share:
                k.misbook_info = br.misbook_info
            break


def _flag_kd_crossacct(kd_left, bank_left, window: int = 31):
    """金蝶单边再甄别：摘要含划转特征的金蝶单边，若其对手腿能在别账户找到 →
    不是"银行流水漏导"，而是集团内部划转，只是账号维度落在本户、对手腿在他账户。
      A) 同额、同方向出现在【别账户的未匹配银行流水】 → 钱实际走他账户，本户金蝶挂账（账号维度不一致）
      B) 同额、反方向出现在【别账户、同含划转特征的金蝶单边】 → 划转两腿都入了金蝶、双方都缺银行流水
    只改标 + 附对应账户，不自动核销、不改金额口径（仍按企业已记银行未记进余额调节）。"""
    for kr in kd_left:
        if kr.combo or not _XFER.search(str(kr.memo or "")):
            continue
        # A) 银行对手腿：同方向、别账户、同额、窗口内
        hit = next((b for b in bank_left
                    if b.acct and b.acct != kr.acct and b.direction == kr.direction
                    and amt_key(b.amount) == amt_key(kr.amount) and _day_diff(kr.d, b.d) <= window), None)
        if hit:
            kr.xfer = True
            kr.xfer_ref = "银行流水实走账户…" + str(hit.acct)[-6:] + (f"（{hit.bank_name}）" if hit.bank_name else "")
            kr.xfer_info = {"对方账号": hit.acct, "对方主体": hit.subject or hit.holder,
                            "对方开户行": hit.bank_name, "对方方向": hit.direction,
                            "对方凭证": "", "对方日期": (hit.d.isoformat() if hit.d else ""),
                            "对方制单人": "", "对方摘要": (hit.memo or "")[:40],
                            "对方是银行流水": True, "同向": True}
            continue
        # B) 金蝶对开腿：反方向、别账户、同额、同含划转特征
        opp = "支" if kr.direction == "收" else "收"
        k2 = next((x for x in kd_left
                   if x is not kr and x.acct and x.acct != kr.acct and x.direction == opp
                   and amt_key(x.amount) == amt_key(kr.amount) and _day_diff(kr.d, x.d) <= window
                   and _XFER.search(str(x.memo or ""))), None)
        if k2:
            kr.xfer = True
            kr.xfer_ref = "与账户…" + str(k2.acct)[-6:] + " 金蝶对开（两腿均缺银行流水）"
            kr.xfer_info = {"对方账号": k2.acct, "对方主体": k2.subject, "对方开户行": k2.bank_name,
                            "对方方向": k2.direction, "对方凭证": k2.voucher,
                            "对方日期": (k2.d.isoformat() if k2.d else ""),
                            "对方制单人": k2.maker or "", "对方摘要": (k2.memo or "")[:40], "同向": False}


# ----------------------------- 出行 -----------------------------
_ORDER = {"bank_leak": 0, "xfer_unbooked": 1, "amount_wrong": 2, "misbook": 3, "late_cross": 4,
          "late_month": 5, "combo_pending": 6, "kd_only": 7, "kd_xfer": 8, "fx_adjust": 9,
          "no_bank_acct": 10, "no_kd_acct": 11, "unmapped": 12, "matched": 13}
_CN = {"bank_leak": "疑似漏账", "xfer_unbooked": "内部往来·未做账", "amount_wrong": "做错·金额",
       "misbook": "疑记错户·账在他户",
       "late_cross": "跨期晚记", "late_month": "晚记·本月", "combo_pending": "组合待确认",
       "kd_only": "金蝶单边·疑似做错",
       "kd_xfer": "内部划转·对应他账户", "fx_adjust": "汇兑损益·账面调整",
       "no_bank_acct": "账户缺银行流水", "no_kd_acct": "账户缺金蝶数据",
       "unmapped": "账号对不上台账", "matched": "已匹配"}


def _row(status, b, k, confidence="", lag=None):
    ref = b or k
    direction = ref.direction
    amt = ref.amount
    diff = round(b.amount - k.amount, 4) if (status == "amount_wrong" and b and k) else None
    combo = (b.combo if b else "") or (k.combo if k else "")
    xref = (b.xfer_ref if b else "") or (k.xfer_ref if k else "")
    late_txt = ""
    if status in ("late_month", "late_cross") and lag is not None:
        late_txt = f"晚 {lag} 天" + ("· 跨期" if status == "late_cross" else "· 本月")
    return {
        "status": status,
        "状态": _CN[status],
        "置信度": confidence,
        "日期": (b.d.isoformat() if b and b.d else (k.d.isoformat() if k and k.d else "")),
        "金蝶日期": (k.d.isoformat() if k and k.d else ""),
        "日期差天": lag,
        "晚记": late_txt,
        "账号": ref.acct,
        "开户行": (ref.bank_name or ""),
        "主体": (ref.subject or (b.holder if b else (k.holder if k else ""))),
        "账户已映射": bool(ref.mapped),
        "户名": (b.holder if b else k.holder),
        "收(付)方名称": (b.counterparty if b else ""),
        "方向": direction,
        "借方金额": (round(amt, 4) if direction == "收" else None),
        "贷方金额": (round(amt, 4) if direction == "支" else None),
        "金蝶金额": (round(k.amount, 4) if k else None),
        "币别": (k.currency if k and k.currency else ""),
        # 外币户金蝶行的本位币金额（≠原币时给出，供与金蝶界面核对；汇兑重估行金额即本位币不重复给）
        "本位币金额": (round(k.amount_base, 4) if k and not k.fx and amt_key(k.amount_base) != amt_key(k.amount) else None),
        "汇率": (k.rate if k and k.rate and k.rate != 1 else None),
        "差额": diff,
        "摘要": (b.memo if b else "") or (k.memo if k else ""),
        "金蝶凭证": (k.voucher if k else ""),
        "制单人": (k.maker if k else ""),
        "组合候选": bool(combo),
        "组合候选说明": combo,
        "组合明细": ((b.combo_info if b else None) or (k.combo_info if k else None)),
        "内部往来对应": xref,
        "内部往来明细": ((b.xfer_info if b else None) or (k.xfer_info if k else None)),
        "记错户对应": ((b.misbook_ref if b else "") or (k.misbook_ref if k else "")),
        "记错户明细": ((b.misbook_info if b else None) or (k.misbook_info if k else None)),
    }


# ----------------------------- 护栏 -----------------------------
def _sum(recs, d):
    return round(sum(r.amount for r in recs if r.direction == d), 4)


def summarize(bank, kd, results) -> dict:
    counts = {s: 0 for s in _CN}
    for r in results:
        counts[r["status"]] += 1
    bank_used = sum(1 for r in bank if r.matched)
    kd_used = sum(1 for r in kd if r.matched)
    guardrail = {
        "银行笔数": len(bank),
        "金蝶笔数": len(kd),
        "银行已归类": bank_used,
        "金蝶已归类": kd_used,
        "银行收合计": _sum(bank, "收"),
        "银行支合计": _sum(bank, "支"),
        "金蝶收合计(借)": _sum(kd, "收"),
        "金蝶支合计(贷)": _sum(kd, "支"),
        "银行笔数核对一致": bank_used == len(bank),
        "金蝶笔数核对一致": kd_used == len(kd),
    }
    return {
        "已匹配": counts["matched"],
        "晚记·本月": counts["late_month"],
        "跨期晚记": counts["late_cross"],
        "做错·金额": counts["amount_wrong"],
        "疑似漏账": counts["bank_leak"],
        "内部往来·未做账": counts["xfer_unbooked"],
        "疑记错户·账在他户": counts["misbook"],
        "组合待确认": counts["combo_pending"],
        "金蝶单边·疑似做错": counts["kd_only"],
        "内部划转·对应他账户": counts["kd_xfer"],
        "汇兑损益·账面调整": counts["fx_adjust"],
        "账户缺银行流水": counts["no_bank_acct"],
        "账户缺金蝶数据": counts["no_kd_acct"],
        "账号对不上台账": counts["unmapped"],
        "组合候选": sum(1 for r in results if r.get("组合候选")),
        "guardrail": guardrail,
    }
