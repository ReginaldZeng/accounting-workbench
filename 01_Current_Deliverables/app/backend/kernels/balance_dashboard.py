# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-07-02
# Author: Claude / c
# Version: V2.238（V2.111 修双重计数；V2.112 期末=期初+本期序时账净还原；
#          V2.238 三段六列(期初/本期变动/期末 × 原币/本位币) + 补「只在序时账里出现的本期新开维度」——
#          此前新开理财维度余额表无行、整笔在看板消失，1.1亿被漏计成"7月资金大降"）
# Description: 资金看板 汇总内核。读金蝶科目余额(GL_BALANCE)四类科目
#              (库存现金1001/银行存款1002/其它货币资金1012/交易性金融资产1101)，
#              按 主体/科目大类/账户 聚合期末余额；生效判定；可选合并"金蝶动账时间"(序时账)
#              与"流水更新时间"(银行明细)。确定性、可单元测试、不含 LLM。
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime
import re

# 科目大类前缀 → 名称（资金看板口径）
SUBJECT_MAP = [
    ("1001", "库存现金"),
    ("1002", "银行存款"),
    ("1012", "其它货币资金"),
    ("1101", "交易性金融资产"),
]


def subject_cat(code: str):
    """科目编码 -> (大类前缀, 大类名)；不属四类返回 (None, None)。"""
    s = str(code or "")
    for pref, name in SUBJECT_MAP:
        if s.startswith(pref):
            return pref, name
    return None, None


def norm_acct(text) -> str:
    """从核算维度文本抽最长数字串作账号键。"""
    if text is None:
        return ""
    runs = re.findall(r"\d+", str(text))
    return max(runs, key=len) if runs else ""


def to_float(x) -> float:
    if x in (None, "", "-", "—"):
        return 0.0
    if isinstance(x, (int, float)):
        return float(x)
    try:
        return float(str(x).replace(",", "").replace("¥", "").replace("￥", "").strip())
    except ValueError:
        return 0.0


def to_date(x):
    if x in (None, ""):
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


@dataclass
class BalRec:
    entity: str            # 主体（账簿）
    subject_code: str
    subject_name: str
    cat_code: str
    cat_name: str
    currency: str
    end_for: float         # 期末原币
    end_base: float        # 期末本位币
    debit_for: float
    credit_for: float
    acct: str              # 归一化账号
    acct_raw: str
    year: int | None
    period: int | None
    begin_for: float = 0.0   # 期初原币（V2.238 看板要"期初/本期变动/期末"三段）
    begin_base: float = 0.0  # 期初本位币
    move_for: float = 0.0    # 本期变动原币（＝本期序时账净额）
    move_base: float = 0.0   # 本期变动本位币
    vou_only: bool = False   # 本期新开维度：余额表尚无此行，整段来自序时账（V2.238）


def _acct_no(r):
    return str(_get(r, "核算维度.银行账号.编码", "核算维度.银行账号.名称",
                    "FDetailID.FF100002.FNumber", "FDetailID.FF100002.FName") or "")


def _cur(r):
    return str(_get(r, "币别", "FCurrencyID.FName") or "").strip()


def dedup_gl_balance(rows: list[dict]) -> list[dict]:
    """金蝶 GL_BALANCE 层级去重（按 账簿+科目 分组）：
    ① 若该科目下有"核算维度(银行账号)"明细行 → 只留核算维度非空行(丢科目/组织合计)；
       否则(如库存现金无账号维度) → 留核算维度为空的余额行。
    ② 再丢掉"币别为空"的本位币汇总行、只留币别非空的分币别明细(避免同一余额被重复计)；
       若无币别非空行则回退保留。
    对样例数据(本就是账号+币别明细、无合计行)无影响。"""
    groups: dict = {}
    for r in rows:
        code = str(_get(r, "科目编码", "FAccountID.FNumber") or "")
        book = str(_get(r, "账簿", "主体", "FACCOUNTBOOKID.FName") or "")
        groups.setdefault((book, code), []).append(r)
    out = []
    for rs in groups.values():
        has_acct = any(_acct_no(r).strip() for r in rs)
        cand = [r for r in rs if bool(_acct_no(r).strip()) == has_acct]
        with_cur = [r for r in cand if _cur(r)]
        out.extend(with_cur if with_cur else cand)
    return out


def _vou_net_for(r) -> float:
    """一行序时账的净发生·原币口径（借+/贷-）。有 FAMOUNTFOR(原币)用原币（境外簿 FDEBIT/FCREDIT 是美元本位币，
    与账户原币不同），无则退本位币；汇兑重估行原币=0 天然不计。同 reconcile.kd_delta_for，内联避免跨内核依赖。"""
    debit = to_float(_get(r, "FDEBIT", "借方", "借方金额"))
    credit = to_float(_get(r, "FCREDIT", "贷方", "贷方金额"))
    if any(k in r for k in ("FAMOUNTFOR", "原币金额")):
        amt = abs(to_float(_get(r, "FAMOUNTFOR", "原币金额")))
        return amt if debit > 0 else (-amt if credit > 0 else 0.0)
    return debit - credit


def _voucher_moves(vou_rows: list[dict]) -> dict:
    """四类科目序时账 → {(科目编码, 维度编码): [原币净, 本位币净, 原币发生额绝对值合计]}。
    维度编码＝FF100002.FNumber（＝GL_BALANCE 的核算维度.银行账号.编码），跨主体唯一，可据此对回。"""
    prefixes = tuple(p for p, _ in SUBJECT_MAP)
    move: dict = {}
    for r in vou_rows or []:
        code = str(_get(r, "科目编码", "FAccountID.FNumber") or "")
        if not code.startswith(prefixes):
            continue
        dim = str(_get(r, "FDetailID.FF100002.FNumber", "核算维度.银行账号.编码") or "").strip()
        d = to_float(_get(r, "FDEBIT", "借方"))
        c = to_float(_get(r, "FCREDIT", "贷方"))
        m = move.setdefault((code, dim), [0.0, 0.0, 0.0])
        m[0] += _vou_net_for(r)          # 原币净
        m[1] += d - c                    # 本位币净
        m[2] += abs(_vou_net_for(r))     # 原币发生额（判生效用）
    return move


def _vou_key(r):
    """凭证身份键：账簿+凭证字+号+日期。旧定格数据无账簿列时退化为 字+号+日期。"""
    return (str(_get(r, "账簿", "FACCOUNTBOOKID.FName") or ""),
            str(_get(r, "FVOUCHERGROUPID.FName", "凭证字") or ""),
            str(_get(r, "FVOUCHERGROUPNO", "凭证号") or ""),
            str(_get(r, "FDATE", "日期") or "")[:10])


def _voucher_meta(vou_rows: list[dict], entity_by_dim: dict | None = None) -> dict:
    """{(科目编码, 维度编码): {主体, 币别, 科目名}}——给"只在凭证里出现的新维度"补身份（V2.238）。
    主体优先取序时账「账簿」列；**旧定格数据无此列时**，从同一张凭证的其它分录反查——
    那些分录的维度通常已在余额表里(如买理财的 1002 付款腿)，据此定位主体；
    仅当同张凭证反查出的主体唯一时才采用，有歧义则留空（不猜）。"""
    prefixes = tuple(p for p, _ in SUBJECT_MAP)
    # ① 同凭证反查表：凭证键 → 该凭证涉及的主体集合（来自余额表已知维度）
    vou_entity: dict = {}
    if entity_by_dim:
        for r in vou_rows or []:
            dim = str(_get(r, "FDetailID.FF100002.FNumber", "核算维度.银行账号.编码") or "").strip()
            ents = entity_by_dim.get(dim)
            if ents:
                vou_entity.setdefault(_vou_key(r), set()).update(ents)
    meta: dict = {}
    for r in vou_rows or []:
        code = str(_get(r, "科目编码", "FAccountID.FNumber") or "")
        if not code.startswith(prefixes):
            continue
        dim = str(_get(r, "FDetailID.FF100002.FNumber", "核算维度.银行账号.编码") or "").strip()
        key = (code, dim)
        if key in meta and meta[key].get("entity"):
            continue
        ent = str(_get(r, "账簿", "FACCOUNTBOOKID.FName") or "")
        if not ent:                                   # 旧数据兜底：同凭证唯一主体才采用
            cand = vou_entity.get(_vou_key(r)) or set()
            if len(cand) == 1:
                ent = next(iter(cand))
        meta[key] = {
            "entity": ent,
            "currency": str(_get(r, "FCURRENCYID.FName", "币别") or "CNY"),
            "subject_name": str(_get(r, "科目名称", "FAccountID.FName") or ""),
        }
    return meta


def load_balance(rows: list[dict], vou_rows: list[dict] | None = None) -> list[BalRec]:
    """GL_BALANCE 行(中文列名或原始字段Key均可) -> BalRec，仅保留四类科目。先做金蝶层级去重。
    ★ 未过账期间金蝶 GL_BALANCE 的「期末/本期发生」停在期初/返回 0，不能直接读；给了序时账(vou_rows)时，
    按 期末＝期初＋本期序时账净 还原（原币、本位币各自还原，与科目余额表/余额调节同口径，逐户验证一致）。
    不给序时账(如样例)时退回读 GL_BALANCE 的期末字段（样例自带真实期末）。"""
    rows = dedup_gl_balance(rows)
    move = _voucher_moves(vou_rows) if vou_rows is not None else None
    out = []
    seen_keys: set = set()          # 余额表已覆盖的 (科目,维度)——余下的是本期新开维度
    for r in rows:
        code = str(_get(r, "科目编码", "FAccountID.FNumber") or "")
        cat_code, cat_name = subject_cat(code)
        if cat_code is None:
            continue
        acct_raw = str(_get(r, "核算维度.银行账号.编码", "核算维度.银行账号.名称",
                            "FDetailID.FF100002.FNumber", "FDetailID.FF100002.FName") or "")
        begin_for = to_float(_get(r, "期初原币", "FBeginBalanceFor"))
        begin_base = to_float(_get(r, "期初本位币", "FBeginBalance", "期初原币", "FBeginBalanceFor"))
        if move is not None:
            # 还原：期末 = 期初 + 本期序时账净
            seen_keys.add((code, acct_raw.strip()))
            mv = move.get((code, acct_raw.strip()), (0.0, 0.0, 0.0))
            move_for, move_base = mv[0], mv[1]
            end_for = round(begin_for + move_for, 2)
            end_base = round(begin_base + move_base, 2)
            debit_for, credit_for = mv[2], 0.0        # 供生效判定（本期有发生额即视为活跃）
        else:
            end_for = to_float(_get(r, "期末原币", "FEndBalanceFor"))
            end_base = to_float(_get(r, "期末本位币", "FEndBalance", "期末原币", "FEndBalanceFor"))
            debit_for = to_float(_get(r, "本期借方原币", "FDebitFor"))
            credit_for = to_float(_get(r, "本期贷方原币", "FCreditFor"))
            move_for = round(end_for - begin_for, 2)   # 样例/已过账：本期变动＝期末−期初
            move_base = round(end_base - begin_base, 2)
        out.append(BalRec(
            begin_for=begin_for, begin_base=begin_base, move_for=round(move_for, 2), move_base=round(move_base, 2),
            entity=str(_get(r, "账簿", "主体", "FACCOUNTBOOKID.FName") or ""),
            subject_code=code,
            subject_name=str(_get(r, "科目名称", "FAccountID.FName") or cat_name),
            cat_code=cat_code, cat_name=cat_name,
            currency=str(_get(r, "币别", "FCurrencyID.FName") or "CNY"),
            end_for=end_for, end_base=end_base,
            debit_for=debit_for, credit_for=credit_for,
            acct=norm_acct(acct_raw), acct_raw=acct_raw,
            year=int(to_float(_get(r, "年", "FYear")) or 0) or None,
            period=int(to_float(_get(r, "期", "FPeriod")) or 0) or None,
        ))
    # ★ V2.238：补「只在序时账里出现的新维度」——本期新开的理财产品/账户，金蝶科目余额表
    #   尚无该维度行（期初为0、未过账），旧版只遍历余额表 → 这笔钱在看板上凭空消失。
    #   实证：2026-07 孝感九买入 华夏理财8000万(记-37)+兴银理财3000万(记-38)，维度为当月新开，
    #   看板集团总资金因此少计 1.1 亿（显示 8744 万，实为 1.97 亿），被误读成"7月资金大降"。
    if move is not None:
        ent_by_dim: dict = {}          # 余额表已知的 维度→主体，供旧数据反查主体（V2.238）
        for r in rows:
            dm = str(_get(r, "核算维度.银行账号.编码", "核算维度.银行账号.名称",
                          "FDetailID.FF100002.FNumber", "FDetailID.FF100002.FName") or "").strip()
            en = str(_get(r, "账簿", "主体", "FACCOUNTBOOKID.FName") or "")
            if dm and en:
                ent_by_dim.setdefault(dm, set()).add(en)
        meta = _voucher_meta(vou_rows, ent_by_dim)
        yr = next((int(to_float(_get(r, "年", "FYear")) or 0) or None for r in rows), None)
        pd = next((int(to_float(_get(r, "期", "FPeriod")) or 0) or None for r in rows), None)
        for (code, dim), mv in move.items():
            if (code, dim) in seen_keys:
                continue
            if abs(mv[0]) < 0.005 and abs(mv[1]) < 0.005:
                continue                       # 本期净额为0的新维度（买入又赎回轧平）不列
            cat_code, cat_name = subject_cat(code)
            if cat_code is None:
                continue
            m = meta.get((code, dim), {})
            out.append(BalRec(
                entity=m.get("entity", "") or "（主体待刷新）", subject_code=code,
                subject_name=m.get("subject_name") or cat_name,
                cat_code=cat_code, cat_name=cat_name,
                currency=m.get("currency") or "CNY",
                begin_for=0.0, begin_base=0.0,
                move_for=round(mv[0], 2), move_base=round(mv[1], 2),
                end_for=round(mv[0], 2), end_base=round(mv[1], 2),
                debit_for=mv[2], credit_for=0.0,
                acct=norm_acct(dim), acct_raw=dim, year=yr, period=pd, vou_only=True))
    return _collapse_subaccounts(out)


def _collapse_subaccounts(recs: list[BalRec]) -> list[BalRec]:
    """处理"科目有子科目"的情形（如 1101 交易性金融资产 下挂 1101.01 成本 / 1101.02 公允价值变动）：
    ① 丢父级汇总行——若同一主体下存在以"本科目编码+."开头的子科目，则本行余额已被子科目覆盖，
       保留会造成【父＋子双重计数】(金蝶科目余额表对有子科目的科目会同时返回父级汇总与子级明细)；
    ② 合并同一(主体·核算维度·币别)的多个子科目行——把同一理财产品的 成本+公允价值变动 相加成一行，
       余额＝成本＋公允价值变动(即该笔理财的公允市值)。1001/1002/1012 无点号子科目，不受影响。"""
    codes_by_ent: dict = {}
    for r in recs:
        codes_by_ent.setdefault(r.entity, set()).add(r.subject_code)
    def _has_child(r):
        return any(c != r.subject_code and c.startswith(r.subject_code + ".")
                   for c in codes_by_ent.get(r.entity, ()))
    leaves = [r for r in recs if not _has_child(r)]
    merged: dict = {}
    out: list[BalRec] = []
    for r in leaves:
        if not r.acct_raw:                     # 无核算维度(如库存现金)：不合并，原样保留
            out.append(r); continue
        key = (r.entity, r.acct_raw, r.currency)
        if key in merged:
            m = merged[key]
            m.end_for = round(m.end_for + r.end_for, 2)
            m.end_base = round(m.end_base + r.end_base, 2)
            m.begin_for = round(m.begin_for + r.begin_for, 2)      # V2.238 三段同步合并
            m.begin_base = round(m.begin_base + r.begin_base, 2)
            m.move_for = round(m.move_for + r.move_for, 2)
            m.move_base = round(m.move_base + r.move_base, 2)
            m.debit_for = round(m.debit_for + r.debit_for, 2)
            m.credit_for = round(m.credit_for + r.credit_for, 2)
        else:
            merged[key] = r
            out.append(r)
    return out


def last_dates(rows: list[dict], acct_keys, date_keys) -> dict:
    """通用：按账号取最大日期。用于金蝶动账时间(序时账)/流水更新时间(银行明细)。
    acct_keys/date_keys 为候选列名元组。返回 {归一化账号: 'YYYY-MM-DD'}。"""
    out = {}
    for r in rows:
        acct = norm_acct(_get(r, *acct_keys))
        d = to_date(_get(r, *date_keys))
        if not acct or d is None:
            continue
        if acct not in out or d > out[acct]:
            out[acct] = d
    return {k: v.isoformat() for k, v in out.items()}


def is_active(b: BalRec, ledger: dict | None) -> bool:
    """生效判定：优先账户台账(ledger[acct]=True/False)；否则启发式——
    期末余额≠0 或 本期有借贷发生 视为生效，否则疑似停用/销户。"""
    if ledger and b.acct in ledger:
        return bool(ledger[b.acct])
    return abs(round(b.end_base, 2)) > 0 or (b.debit_for + b.credit_for) > 0


def build_dashboard(bal: list[BalRec], kd_last: dict | None = None,
                    flow_last: dict | None = None, ledger: dict | None = None) -> dict:
    kd_last = kd_last or {}
    flow_last = flow_last or {}
    accounts = []
    for b in bal:
        active = is_active(b, ledger)
        flow_t = flow_last.get(b.acct, "")
        kd_t = kd_last.get(b.acct, "")
        lag = bool(active and flow_t and kd_t and kd_t < flow_t)  # 金蝶动账滞后于流水
        # 账号显示：银行/其它货币→抽出的数字账号；交易性金融资产(1101,按理财产品/项目名记，
        # 如"63672948信益嘉321号6单元")→直接显示完整核算维度名称，别把名字丢成一串数字。
        # 1101 交易性金融资产 / 1012 其它货币资金 的核算维度是【产品名/渠道名】（如"24135013B华夏理财悦慧…"、
        # "天猫1058952426@…"），抽成纯数字就看不出是什么了 → 原样显示；1001/1002 仍显示数字账号（V2.238）
        acct_show = (b.acct_raw if (b.cat_code in ("1101", "1012") and b.acct_raw) else (b.acct or b.acct_raw))
        accounts.append({
            "主体": b.entity, "账号": acct_show,
            "科目大类": b.cat_name, "科目编码": b.subject_code,
            "生效": ("生效" if active else "已销户"), "_active": active,
            "币种": b.currency,
            # V2.238 三段六列（需求方定：期初/本期变动/期末，各分原币与本位币）
            "期初余额(原币)": round(b.begin_for, 2),
            "期初余额(本位币)": round(b.begin_base, 2),
            "本期变动(原币)": round(b.move_for, 2),
            "本期变动(本位币)": round(b.move_base, 2),
            "期末余额(原币)": round(b.end_for, 2),
            "期末余额(本位币)": round(b.end_base, 2),
            "本期新开": bool(b.vou_only),      # 余额表尚无此维度，整段来自序时账
            "流水更新时间": flow_t, "金蝶动账时间": kd_t, "金蝶动账滞后": lag,
        })
    active_rows = [a for a in accounts if a["_active"]]
    total = round(sum(a["期末余额(本位币)"] for a in active_rows), 2)
    total_begin = round(sum(a["期初余额(本位币)"] for a in active_rows), 2)
    total_move = round(sum(a["本期变动(本位币)"] for a in active_rows), 2)
    by_cat = _group_sum(active_rows, "科目大类")
    by_ent = _group_sum(active_rows, "主体")
    guardrail = {
        "账户数": len(accounts),
        "生效账户": len(active_rows),
        "已销户": len(accounts) - len(active_rows),
        "集团合计=Σ科目大类": abs(total - round(sum(by_cat.values()), 2)) < 0.01,
        "集团合计=Σ主体": abs(total - round(sum(by_ent.values()), 2)) < 0.01,
        "金蝶动账滞后账户": sum(1 for a in accounts if a["金蝶动账滞后"]),
        "期初+本期变动=期末": abs(total - round(total_begin + total_move, 2)) < 0.01,   # V2.238 三段自校验
        "本期新开维度账户": sum(1 for a in accounts if a.get("本期新开")),
    }
    return {
        "集团合计": total,
        "集团期初": total_begin,
        "集团本期变动": total_move,
        "科目大类": [{"科目大类": name, "合计": round(by_cat.get(name, 0.0), 2)}
                     for _, name in SUBJECT_MAP],
        "主体": [{"主体": k, "合计": round(v, 2)} for k, v in
                 sorted(by_ent.items(), key=lambda kv: -kv[1])],
        "accounts": accounts,
        "guardrail": guardrail,
    }


def _group_sum(rows, key) -> dict:
    out = {}
    for r in rows:
        out[r[key]] = out.get(r[key], 0.0) + r["期末余额(本位币)"]
    return out
