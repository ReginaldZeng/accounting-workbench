# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-08-03 | Author: Claude / c | Version: V2.168
# Description: 新增 bank_of_row——流水行按【账号→官方开户行】归行(主数据/台账优先，标签兜底)。
#              财资平台导出无开户行列、行标签是"宁波/招商"混合值，纯按标签认字会把招商笔数全归宁波。
# Date: 2026-07-03
# Author: Claude / c
# Version: V1.16
# Description: 账户台账「做实」内核。权威源=金蝶出纳管理银行账号(CN_BANKACNT, 见 download_bank_accounts.py),
#              全量账户+官方开户行+启用/禁用状态。在 V1.8 同步内核基础上补三件:
#              ①富开户行别名 + 类别分类(银行账户/电商渠道/理财产品/现金);
#              ②build_ledger 产出用户约定9列台账(一账户一行);
#              ③权威匹配桥 match_account: 以"账号数字+主体"消歧, 消除"抓最长数字串"的短号假配。
#              确定性、可测、无 LLM。CN列名对齐 download_bank_accounts.py 输出。
from __future__ import annotations
import json
import os
import re

SUBJECT_MAP = [("1001", "库存现金"), ("1002", "银行存款"),
               ("1012", "其它货币资金"), ("1101", "交易性金融资产")]

# 富开户行别名(含简称/理财子/网点前缀归一)。宁波行/宁银→宁波银行 等
BANK_ALIAS = [
    (("宁波", "宁银"), "宁波银行"), (("招商", "招行", "招银"), "招商银行"),
    (("中国银行", "中行"), "中国银行"), (("花旗",), "花旗银行"),
    (("建设", "建行"), "建设银行"), (("工商", "工行"), "工商银行"),
    (("农业", "农行", "农银"), "农业银行"), (("交通", "交行", "交银"), "交通银行"),
    (("平安",), "平安银行"), (("民生",), "民生银行"), (("浦发", "浦银"), "浦发银行"),
    (("兴业", "兴银"), "兴业银行"), (("光大",), "光大银行"), (("中信",), "中信银行"),
    (("邮储", "中邮"), "邮储银行"), (("SVB", "硅谷"), "硅谷银行"), (("华夏",), "华夏银行"),
]
CHANNEL = ["支付宝", "微信", "淘宝", "天猫", "抖音", "京东", "小红书", "快手",
           "有赞", "拼多多", "唯品会"]
ACCT_TYPE_KW = ["资本金户", "基本户", "一般户", "专用户", "通知存款", "结构性存款", "NRA"]
LICAI_KW = ["理财", "碧乐活", "日日金", "日日薪", "鎏金", "现金管理", "增利", "天添",
            "添利", "基金", "Swap", "Structured", "Investment", "单元", "邮鸿", "周周"]


def norm_acct(text) -> str:
    """账号数字键：取最长数字串(≥4位)。仅作粗键，权威匹配请走 match_account。"""
    if text is None:
        return ""
    runs = re.findall(r"\d{4,}", str(text))
    return max(runs, key=len) if runs else ""


def cat_from_code(code: str) -> str:
    s = str(code or "")
    for pref, name in SUBJECT_MAP:
        if s.startswith(pref):
            return name
    return ""


def bank_of(name: str) -> str:
    s = str(name or "")
    for kws, full in BANK_ALIAS:
        if any(k in s for k in kws):
            return full
    m = re.search(r"[一-龥]{2,6}银行", s)
    return m.group(0) if m else ""


def acct_type(name: str) -> str:
    s = str(name or "")
    for t in ["资本金户", "基本户", "一般户", "专用户", "通知存款", "结构性存款"]:
        if t in s:
            return t
    return "NRA账户" if "NRA" in s else ""


def classify(name: str, cat_code: str = "") -> dict:
    """归类：返回 {类别, 开户行, 账户类型}。cat_code=科目前缀(可空)。"""
    nm = str(name or "")
    if cat_code == "1001":
        return {"类别": "现金", "开户行": "", "账户类型": "现金"}
    ch = next((c for c in CHANNEL if c in nm), "")
    if ch:
        return {"类别": "电商渠道", "开户行": ch, "账户类型": "结算账户"}
    if cat_code == "1002":
        return {"类别": "银行账户", "开户行": bank_of(nm), "账户类型": acct_type(nm) or "一般户"}
    if cat_code == "1101":
        return {"类别": "理财产品", "开户行": bank_of(nm), "账户类型": "理财"}
    # 无科目提示时按关键词
    if any(k in nm for k in ["一般户", "基本户", "资本金户", "专用户", "通知存款", "结构性存款", "NRA"]):
        return {"类别": "银行账户", "开户行": bank_of(nm), "账户类型": acct_type(nm)}
    if any(k in nm for k in LICAI_KW):
        return {"类别": "理财产品", "开户行": bank_of(nm), "账户类型": "理财"}
    if bank_of(nm):
        return {"类别": "银行账户", "开户行": bank_of(nm), "账户类型": "一般户"}
    return {"类别": "其它", "开户行": "", "账户类型": ""}


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


def _to_f(x):
    try:
        return float(str(x).replace(",", "")) if x not in (None, "") else 0.0
    except ValueError:
        return 0.0


def cur_of(name: str, fallback: str = "CNY") -> str:
    """币别字段空时从账户名/编码推断币种。港币户/美元户/USD 等。"""
    s = str(name or "")
    if any(k in s for k in ("港币", "HKD")):
        return "HKD"
    if any(k in s for k in ("美元", "美金", "USD")):
        return "USD"
    if any(k in s for k in ("欧元", "EUR")):
        return "EUR"
    if any(k in s for k in ("英镑", "GBP")):
        return "GBP"
    if any(k in s for k in ("日元", "JPY")):
        return "JPY"
    return fallback


def _active_from_forbid(forbid) -> bool:
    """禁用状态 -> 生效bool。A/可用/启用=生效; B/禁用/已销户=停用。"""
    if forbid is None or forbid == "":
        return True
    s = str(forbid).strip()
    return s not in ("B", "禁用", "已禁用", "已销户", "1", "True", "true", "停用", "作废", "C")


# --------------------- 数据源适配 ---------------------
def kd_accounts_from_cn(rows: list[dict]) -> list[dict]:
    """金蝶 CN_BANKACNT 导出(列名对齐 download_bank_accounts.py) -> 统一账号记录。"""
    out = []
    for r in rows:
        # 该账套: "银行账号"列(FNumber)实为账户编码(如"宁波行港币户73063025000008088"),
        # 内含开户行+类型+账号数字, 与GL核算维度编码同格式 -> 正好作账户全名与权威匹配键。
        acct_raw = _get(r, "银行账号", "账号", "账户全名", "FNumber", "FBANKACCOUNTNUMBER") or ""
        name = _get(r, "账户名称", "户名", "FName", "FACCOUNTNAME") or ""
        bank_cat = _get(r, "银行类别", "开户行", "开户银行", "FBANKID.FName", "FOPENBANKNAME") or ""
        subj = _get(r, "所属组织", "主体", "所属公司", "FUSEORGID.FName", "账簿") or name or ""
        forbid = _get(r, "禁用状态", "使用状态", "FFORBIDSTATUS", "FDocumentStatus")
        full = str(acct_raw)
        bank = bank_of(full) or bank_of(str(bank_cat)) or bank_of(str(name)) or str(bank_cat).strip()
        cls = classify(full + " " + str(bank_cat), "")
        cur = _get(r, "币别", "币种", "FCURRENCYID.FName")
        out.append({
            "账号": norm_acct(full), "账号原文": full,
            "开户行": bank, "开户行网点": str(bank_cat or "").strip(),
            "账户名称": full or str(name), "主体": str(subj),
            "类别": cls["类别"], "账户类型": cls["账户类型"],
            "科目大类": "银行存款", "币种": cur_of((str(cur or "") + " " + full)),
            "生效": _active_from_forbid(forbid),
        })
    return out


def kd_accounts_from_balance(rows: list[dict]) -> list[dict]:
    """GL_BALANCE 派生(兜底，无禁用状态，生效按余额/发生启发)。"""
    seen = {}
    for r in rows:
        code = str(_get(r, "科目编码", "FAccountID.FNumber") or "")
        cat = cat_from_code(code)
        if not cat:
            continue
        name = str(_get(r, "核算维度.银行账号.编码", "核算维度.银行账号.名称",
                        "FDetailID.FF100002.FNumber") or "")
        subj = str(_get(r, "账簿", "FACCOUNTBOOKID.FName") or "")
        acct = norm_acct(name)
        end = _to_f(_get(r, "期末本位币", "FEndBalance"))
        occ = _to_f(_get(r, "本期借方原币", "FDebitFor")) + _to_f(_get(r, "本期贷方原币", "FCreditFor"))
        cls = classify(name, code[:4])
        key = (subj, acct or name)
        rec = seen.setdefault(key, {
            "账号": acct, "账号原文": name, "开户行": cls["开户行"], "开户行网点": name,
            "账户名称": name, "主体": subj, "类别": cls["类别"], "账户类型": cls["账户类型"],
            "科目大类": cat, "币种": str(_get(r, "币别", "FCurrencyID.FName") or "CNY"),
            "生效": False,
        })
        if abs(round(end, 2)) > 0 or occ > 0:
            rec["生效"] = True
    return list(seen.values())


# --------------------- 建台账(9列) ---------------------
LEDGER_COLS = ["主体", "会计科目", "开户行", "类别", "账号", "币种", "账户全名", "状态", "第一笔动账日期"]


def build_ledger_rows(kd_accounts: list[dict], first_dates: dict | None = None) -> list[dict]:
    """统一账号记录 -> 用户约定9列台账(一账户一行, 按主体/科目/开户行排序)。
    first_dates: {账号: 'YYYY-MM-DD'} 本期第一笔动账日期(来自序时账, 可空)。"""
    first_dates = first_dates or {}
    rows = []
    seen = set()
    for a in kd_accounts:
        key = (a.get("主体", ""), a.get("账号原文") or a.get("账号", ""))
        if key in seen:
            continue
        seen.add(key)
        # 只有"银行账户"才有可匹配银行流水的账号; 理财/电商/现金的标识是产品名/邮箱/手机号,
        # 不是银行账号(如"JY100015交银理财…"的100015是产品号), 清空以免误配银行流水。
        acct = a.get("账号", "") if a.get("类别", "") == "银行账户" else ""
        rows.append({
            "主体": a.get("主体", ""),
            "会计科目": a.get("科目大类", "银行存款"),
            "开户行": a.get("开户行", ""),
            "类别": a.get("类别", ""),
            "账号": acct,
            "币种": a.get("币种", "CNY"),
            "账户全名": a.get("账户名称") or a.get("账号原文", ""),
            "状态": "生效" if a.get("生效", True) else "已销户",
            "第一笔动账日期": first_dates.get(acct, ""),
        })
    rows.sort(key=lambda x: (x["主体"], x["会计科目"], x["开户行"], x["账号"]))
    return rows


# --------------------- 权威匹配桥(消除短号假配) ---------------------
def build_match_index(ledger_rows: list[dict]) -> dict:
    """{账号数字: [台账行,...]}。同账号多主体时保留多条, 供 match_account 消歧。"""
    idx = {}
    for r in ledger_rows:
        a = r.get("账号", "")
        if a:
            idx.setdefault(a, []).append(r)
    return idx


def match_account(bank_acct, index: dict, subject_hint: str = "") -> dict | None:
    """银行流水账号 -> 唯一台账账户。
    规则: 先按完整数字键命中; 多命中时用主体消歧; 仍多义或短号无命中 -> 返回 None(交由上层标'待映射',
    绝不用最长数字串瞎配)。"""
    digits = norm_acct(bank_acct)
    if not digits:
        return None
    cands = index.get(digits)
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    if subject_hint:
        sub = [c for c in cands if subject_hint and subject_hint in c.get("主体", "")]
        if len(sub) == 1:
            return sub[0]
    return None                      # 多义未消歧 -> 不猜


# --------------------- 同步(保留 V1.8 语义) ---------------------
def sync_ledger(prev: list[dict], kd_accounts: list[dict], period: str):
    prev_by = {(r.get("主体", ""), r["账号"]): dict(r) for r in prev if r.get("账号")}
    kd_keys = set()
    changes = {"新增": [], "停用": [], "恢复": []}
    merged = {}
    for a in kd_accounts:
        key = (a.get("主体", ""), a.get("账号") or a.get("账号原文", ""))
        if not key[1]:
            continue
        kd_keys.add(key)
        active = bool(a.get("生效", True))
        if key in prev_by:
            rec = prev_by[key]
            was = bool(rec.get("_active", rec.get("状态") == "生效"))
            rec.update({"开户行": a.get("开户行") or rec.get("开户行", ""),
                        "主体": a.get("主体") or rec.get("主体", ""),
                        "类别": a.get("类别") or rec.get("类别", ""),
                        "账户名称": a.get("账户名称") or rec.get("账户名称", ""),
                        "科目大类": a.get("科目大类") or rec.get("科目大类", ""),
                        "币种": a.get("币种") or rec.get("币种", "CNY")})
            rec["_active"] = active
            rec["状态"] = "生效" if active else "已销户"
            rec["最近同步期间"] = period
            rec.pop("消失", None)
            if active and not was:
                changes["恢复"].append(key[1])
            if (not active) and was:
                changes["停用"].append(key[1])
        else:
            rec = {"账号": key[1], "账号原文": a.get("账号原文", key[1]),
                   "开户行": a.get("开户行", ""), "账户名称": a.get("账户名称", ""),
                   "主体": a.get("主体", ""), "类别": a.get("类别", ""),
                   "科目大类": a.get("科目大类", ""), "币种": a.get("币种", "CNY"),
                   "_active": active, "状态": "生效" if active else "已销户",
                   "首次出现期间": period, "最近同步期间": period, "来源": "金蝶同步"}
            changes["新增"].append(key[1])
        merged[key] = rec
    for key, rec in prev_by.items():
        if key not in kd_keys:
            was = bool(rec.get("_active", rec.get("状态") == "生效"))
            rec["_active"] = False
            rec["状态"] = "已销户"
            rec["消失"] = True
            if was:
                changes["停用"].append(key[1])
            merged[key] = rec
    out = list(merged.values())
    for r in out:
        r["本月新增"] = bool(r.get("首次出现期间") == period and r.get("_active"))
    report = {
        "期间": period, "账号数": len(out),
        "生效": sum(1 for r in out if r["_active"]),
        "已销户": sum(1 for r in out if not r["_active"]),
        "本月新增": sum(1 for r in out if r["本月新增"]),
        "本次新增": len(changes["新增"]), "本次停用": len(changes["停用"]),
        "本次恢复": len(changes["恢复"]), "changes": changes,
    }
    return out, report


# --------------------- 持久化 ---------------------
def load_ledger(path: str) -> list[dict]:
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_ledger(path: str, records: list[dict]):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def to_ledger_map(records: list[dict]) -> dict:
    return {r["账号"]: {"开户行": r.get("开户行", ""), "主体": r.get("主体", ""),
                        "类别": r.get("类别", ""),
                        "生效": bool(r.get("_active", r.get("状态") == "生效"))}
            for r in records if r.get("账号")}


def bank_of_row(acct, bank_label, master_map=None, ledger_map=None) -> str:
    """流水行归行（数据源覆盖用，V2.168）：账号先查【出纳管理主数据/账户台账】的官方开户行，
    查无再按行内"银行"标签认字。财资平台导出无开户行列、标签是"宁波/招商"混合值，
    纯按标签认会把招商的笔数全记到宁波头上。纯函数可单测。"""
    a = str(acct or "")
    info = (master_map or {}).get(norm_acct(a)) or {}
    if isinstance(info, str):
        info = {"开户行": info}      # 兼容旧格式（曾只存开户行字符串）
    lm = ledger_map or {}
    ob = info.get("开户行") or (lm.get(a) or lm.get(norm_acct(a)) or {}).get("开户行", "")
    return bank_of(ob) or bank_of(bank_label) or bank_of(a)


# 向后兼容别名(V1.8 名称)
parse_bank = bank_of
