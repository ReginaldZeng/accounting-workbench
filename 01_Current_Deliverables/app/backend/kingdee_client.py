# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-08-01 | Author: Claude / c | Version: V2.158
# Description: 汇率录入P2：新增通用单据写入 save_bill()/submit_bill()/delete_bill()（不写死 formid，
#              可写 BD_Rate 等任意单据）+ 新增 SUBMIT_SVC。汇率工具走"只提交、不审核"——故只实现
#              Save+Submit，刻意不实现 Audit（审核=生效动作留给人在金蝶原生审核流做，见确认书 v1.1 D11）。
#              BD_Rate 回读去重复用现成 _query(...,"BD_Rate",...)。原 GL_VOUCHER 专用写入函数不动。
# Date: 2026-07-08 | Author: Claude / c | Version: V2.44
# Description: 物流对账A期：新增 fetch_outbound_docs()——只读取 销售出库/其他出库/分步式调出/调入
#              四种单据（单号/日期/客户/物料/数量/基本单位数量kg/收货地址），供 logistics_recon 内核
#              五信号匹配用。字段按 2026-07-08 实单探查落定；其他出库单无基本单位数量列则留空。
# Date: 2026-07-06 | Author: Claude / c | Version: V2.29
# Description: 物流计提一键录入：新增 save_voucher()/delete_vouchers()（GL_VOUCHER 建/删草稿，
#              配方=2026-07-06 控制测试实证）与 fetch_voucher_numbers()（按单据编号回查记-字号）；
#              fetch_suppliers() 加「分组」列（FGroup.FNumber，应付行核算维度用）。
#              写入仅限物流计提路由显式调用，其余取数仍全部只读。
# Date: 2026-07-02 | Author: Claude / c | Version: V1.10
# Description: 金蝶云星空 WebAPI 客户端(后端用)。复用 download_* 脚本的登录/查询逻辑，
#              但直接返回 list[dict](中文列名，对齐各内核 loader)，不写 Excel。
#              conf.ini 查找顺序：环境变量 KD_CONF_PATH → backend/conf.ini → 金蝶配置文件/conf.ini。
import os
import re
import json
import base64
import zlib
import configparser
import requests

LOGIN_SVC = "Kingdee.BOS.WebApi.ServicesStub.AuthService.LoginByAppSecret.common.kdsvc"
QUERY_SVC = "Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.ExecuteBillQuery.common.kdsvc"
META_SVC = "Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.QueryBusinessInfo.common.kdsvc"
SAVE_SVC = "Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.Save.common.kdsvc"
DELETE_SVC = "Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.Delete.common.kdsvc"
VIEW_SVC = "Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.View.common.kdsvc"
SUBMIT_SVC = "Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.Submit.common.kdsvc"
CANCELASSIGN_SVC = "Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.CancelAssign.common.kdsvc"
PAGE_SIZE = 2000
BASE = os.path.dirname(os.path.abspath(__file__))

_CONF_CANDIDATES = [
    os.environ.get("KD_CONF_PATH", ""),
    os.path.join(BASE, "conf.ini"),
    os.path.join(BASE, "..", "..", "20260702_V1.0_初始归档", "金蝶配置文件", "conf.ini"),
]


class KingdeeError(RuntimeError):
    pass


def conf_path():
    for p in _CONF_CANDIDATES:
        if p and os.path.exists(p):
            return os.path.abspath(p)
    return ""


def load_conf():
    p = conf_path()
    if not p:
        raise KingdeeError("找不到 conf.ini（把金蝶授权 conf.ini 放到 backend/ 或设 KD_CONF_PATH）")
    cfg = configparser.ConfigParser()
    cfg.read(p, encoding="utf-8")
    c = cfg["config"]
    conf = {"acct_id": c.get("X-KDApi-AcctID", "").strip(), "username": c.get("X-KDApi-UserName", "").strip(),
            "app_id": c.get("X-KDApi-AppID", "").strip(), "app_secret": c.get("X-KDApi-AppSec", "").strip(),
            "server_url": c.get("X-KDApi-ServerUrl", "").strip().rstrip("/"),
            "lcid": int(c.get("X-KDApi-LCID", "2052") or "2052")}
    miss = [k for k in ("acct_id", "username", "app_id", "app_secret", "server_url") if not conf[k]]
    if miss:
        raise KingdeeError(f"conf.ini 缺项：{', '.join(miss)}")
    return conf


def _post(s, conf, svc, params):
    url = f"{conf['server_url']}/{svc}"
    try:
        r = s.post(url, data=json.dumps({"parameters": params}, ensure_ascii=False).encode("utf-8"),
                   headers={"Content-Type": "application/json;charset=utf-8"}, timeout=120)
        r.raise_for_status()
    except requests.RequestException as e:
        raise KingdeeError(f"连接金蝶失败（检查网络/防火墙/ServerUrl）：{e}")
    return r


def login(s=None, conf=None):
    conf = conf or load_conf()
    s = s or requests.Session()
    res = _post(s, conf, LOGIN_SVC, [conf["acct_id"], conf["username"], conf["app_id"],
                                     conf["app_secret"], conf["lcid"]]).json()
    if not (isinstance(res, dict) and res.get("LoginResultType") == 1):
        raise KingdeeError(f"登录失败：{res}")
    return s, conf


def _query(s, conf, form_id, fields, filter_str, order=""):
    keys = ",".join(f for f, _ in fields)
    rows, start = [], 0
    while True:
        q = {"FormId": form_id, "FieldKeys": keys, "FilterString": filter_str,
             "OrderString": order, "TopRowCount": 0, "StartRow": start, "Limit": PAGE_SIZE}
        data = _post(s, conf, QUERY_SVC, [json.dumps(q, ensure_ascii=False)]).json()
        if isinstance(data, dict):
            raise KingdeeError(f"查询报错：{json.dumps(data, ensure_ascii=False)[:400]}")
        if data and isinstance(data[0], list) and data[0] and isinstance(data[0][0], dict):
            raise KingdeeError(f"查询报错(字段Key可能不符本账套)：{json.dumps(data[0][0], ensure_ascii=False)[:300]}")
        rows += data
        if len(data) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    cn = [c for _, c in fields]
    return [dict(zip(cn, r)) for r in rows]


# ---------------- 三类取数（列名对齐各内核 loader / sample_data） ----------------
GL_BALANCE_FIELDS = [
    ("FACCOUNTBOOKID.FName", "账簿"), ("FYear", "年"), ("FPeriod", "期"),
    ("FAccountID.FNumber", "科目编码"), ("FAccountID.FName", "科目名称"), ("FCurrencyID.FName", "币别"),
    ("FBeginBalanceFor", "期初原币"), ("FDebitFor", "本期借方原币"), ("FCreditFor", "本期贷方原币"),
    ("FEndBalanceFor", "期末原币"), ("FEndBalance", "期末本位币"),
    ("FBeginBalance", "期初本位币"),   # 折人民币用：期初本位币+本期序时账本位币净发生 还原实时本位币账面(V2.35)
    ("FDetailID.FF100002.FNumber", "核算维度.银行账号.编码"),
    ("FDetailID.FF100002.FName", "核算维度.银行账号.名称"),
]
# CN_BANKACNT 逻辑列：(表头, 中文名关键词, 需要.FName, 候选字段Key)。移植自 download_bank_accounts.py。
# 不再写死 FBankAccountNumber（该字段Key本账套不符）——改由元数据按中文名自动认列 + 候选兜底 + 错列自愈。
_BANK_LOGICAL_COLS = [
    ("银行账号", ["银行账号", "账号"], False, ["FBANKACCOUNTNUMBER", "FACCTNUMBER", "FNumber"]),
    ("账户名称", ["账户名称", "户名", "名称"], False, ["FACCOUNTNAME", "FBankAccountName", "FName"]),
    ("开户行", ["开户行", "开户银行", "开户网点"], False, ["FOPENBANKNAME", "FTEXTBANKDETAIL"]),
    ("银行类别", ["银行类别", "所属银行", "银行"], True, ["FBANKID", "FBANKTYPEREFID", "FBANKGROUPID"]),
    ("所属组织", ["所属组织", "使用组织", "核算组织", "所属公司", "公司"], True, ["FUSEORGID", "FUseOrgId", "FCreateOrgId"]),
    ("币别", ["币别", "币种"], True, ["FCURRENCYID"]),
    ("禁用状态", ["禁用", "使用状态", "单据状态"], False, ["FFORBIDSTATUS", "FDOCUMENTSTATUS"]),
]
_ORG_FILTER_FIELD = "FUseOrgId"          # 银行账号"使用组织"，组织隔离过滤用
_ORG_FORMS = ["ORG_Organizations", "BOS_Organizations"]
_MANUAL_ORG_NAMES = [                     # 组织清单查不到时按主体名过滤兜底
    "深圳市星期零食品科技有限公司", "孝感市星期九食品科技有限公司",
    "深圳市星期八食品科技有限公司", "深圳市星期九食品科技有限公司",
    "深圳市星期十食品科技有限公司", "Sinkio Limited",
    "Starfield Food and Science", "Starfield Plant-Based,Inc",
]
GL_VOUCHER_FIELDS = [
    ("FDetailID.FF100002.FNumber", "FDetailID.FF100002.FNumber"), ("FDATE", "FDATE"),
    ("FDEBIT", "FDEBIT"), ("FCREDIT", "FCREDIT"), ("FEXPLANATION", "FEXPLANATION"),
    ("FVOUCHERGROUPID.FName", "FVOUCHERGROUPID.FName"), ("FVOUCHERGROUPNO", "FVOUCHERGROUPNO"),
    # 币别口径(V2.32)：FDEBIT/FCREDIT 是账簿本位币(境外簿本位币=美元)，银行流水是账户原币——
    # 港币户/境外簿人民币户两边币种不同。补 原币金额/币别/汇率，逐笔稽核与余额调节按原币对账。
    ("FAMOUNTFOR", "FAMOUNTFOR"), ("FCURRENCYID.FName", "FCURRENCYID.FName"),
    ("FEXCHANGERATE", "FEXCHANGERATE"),
    # 制单人(V2.169)：差异行(做错金额/晚记)直接亮出经手人，免回金蝶翻凭证。
    # 2026-08-03 活账套只读实测：7月1002序时账 244 笔全部带出姓名。
    ("FCREATORID.FName", "制单人"),
]


# ── _STOCKORG：**核算组织 ≠ 库存组织，取数只按核算组织过滤** ──────────────────
# 三张存货报表（跨维度/按日期/事务类型流水）原先都带 `FSTOCKORGID = 核算组织代码`，
# 想当然地以为"这个主体的货就在这个主体的仓库里"。**不成立**：核算组织的存货完全可以
# 存放在别的库存组织的仓库中（101 深圳星期零的货放在孝感成品仓、昆山吉波、迅鸽星期零仓…）。
# 加了这个条件＝把跨组织存放的存货整批滤掉。
# 🧪 V2.300 实测 101/2026-7：带过滤 4 行、收入 0.00；去掉后 164 行、
#    期初 9,446.08／收入 2,115,194.74／发出 2,117,269.41／结存 7,371.41，**与业务方底稿四项全中**。
#    而 107/2026-3、5、7 三个月**带不带这个条件结果完全一样**（行数与四项金额逐项相同）——
#    107 的货恰好都在自己组织的仓库里，所以这个 bug 藏了两个月没露头。
# ⚠**危险之处不在于它报错，而在于它不报错**：101 照样跑完三道勾稽、账实还"全过"
#    （因为期初/结存恰好只有本组织仓库那几行），只有流量整段是空的——
#    看着像"这家公司没什么业务"，实则是被过滤掉了。
# 核算范围由 `FACCTGORGID`（核算组织）+ 核算体系/会计政策 圈定，已经够了。
# ── _ROWS_NULL：金蝶报表**无数据时返回 `Rows: null`，不是 `[]`** ────────────────
# 于是 `r.get("Rows", [])` 的默认值不生效（键在、值是 None），拿到的是 None，
# 下一句 `raw += batch` 当场 TypeError。而 V2.286 起代码错误**不再降级成提示**（那条是对的，
# 它抓出过被误删的函数），于是这个 None 直接变成 500 —— 整条一键取数报废。
# 🧪 V2.299 实翻车：深圳星期零 101 没有生产、成本计算单为空 → 2026-7 期一键取数 500，
#    跨维度/按日期/科目余额三张明明都取回来了，也一起陪葬。
# **主体没有某类业务是常态，不是错误**：全站统一写 `r.get("Rows") or []`。
def fetch_gl_balance(year, period, prefixes=("1001", "1002", "1012", "1101"), book=None, s=None, conf=None):
    """科目余额表。book＝账簿代码（如 "107"），给了就在金蝶端按账簿过滤。

    V2.253：新增 book 参数。GL_BALANCE 上 **`FACCOUNTBOOKID.FNumber` 就是账簿代码**
    （2026-08-10 实证：107→孝感市星期九、101→深圳市星期零…，与主体档案 `book_code` 同一套编码），
    故成本台账不必再靠【账簿全称】认账簿。⚠该字段第一次试时报 500，看清楚报的是
    「分页取数需要排序条件或者单据有设置主键」——**不是字段不存在**；本函数一直带
    OrderString 故无此问题。别再照着 500 去猜字段名（同 V2.130 教训）。
    不传 book 时行为与旧版完全一致（银行/资金线沿用）。"""
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    pref = " or ".join(f"FAccountID.FNumber like '{p}%'" for p in prefixes)
    flt = f"({pref}) and FYear={year} and FPeriod={period}"
    if book:
        flt += f" and FACCOUNTBOOKID.FNumber='{book}'"
    return _query(s, conf, "GL_BALANCE", GL_BALANCE_FIELDS, flt, "FAccountID.FNumber")


# ---------------- CN_BANKACNT 自愈取数（移植自 download_bank_accounts.py）----------------
def _walk_fields(obj, found):
    if isinstance(obj, dict):
        key = obj.get("Key") or obj.get("key") or obj.get("FieldName")
        name = obj.get("Name") or obj.get("name") or obj.get("FieldCaption")
        if isinstance(name, dict):
            name = name.get("zh-CN") or name.get("zh_CN") or next(iter(name.values()), "")
        if key and isinstance(key, str):
            found.setdefault(key, name or "")
        for v in obj.values():
            _walk_fields(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _walk_fields(v, found)


def _field_catalog(s, conf, form_id):
    """QueryBusinessInfo 拉字段元数据 → {字段Key: 中文名}。失败返回空(用候选字段名)。"""
    try:
        data = _post(s, conf, META_SVC, [{"FormId": form_id}]).json()
    except Exception:
        return {}
    found = {}
    _walk_fields(data, found)
    return found


def _resolve_bank_fields(catalog):
    """按中文名认列 + 候选兜底。返回 [(字段Key, 表头)]，保证含"银行账号"列。"""
    chosen, used = [], set()
    for header, kws, need_fname, cands in _BANK_LOGICAL_COLS:
        key = None
        for k, nm in catalog.items():
            if "." in k:
                continue
            if any(w in str(nm) for w in kws) and k not in used:
                key = k
                break
        if key is None:
            for c in cands:
                if not catalog or c in catalog:
                    key = c
                    break
            if key is None and cands:
                key = cands[0]
        if not key:
            continue
        used.add(key)
        qkey = key + ".FName" if (need_fname and not key.endswith(".FName")) else key
        chosen.append((qkey, header))
    if not any(h == "银行账号" for _, h in chosen):
        chosen.insert(0, ("FNumber", "银行账号"))
    return chosen


def _bad_field(errmsg):
    m = re.search(r"[Ff]ield\s+['\"]?([A-Za-z0-9_.]+)['\"]?", errmsg)
    if m:
        return m.group(1)
    m = re.search(r"字段['\"]?([A-Za-z0-9_.]+)['\"]?", errmsg)
    return m.group(1) if m else None


def _query_raw(s, conf, form_id, keys, filt, start):
    """单页查询，返回 (rows|None, errmsg|None)。"""
    q = {"FormId": form_id, "FieldKeys": keys, "FilterString": filt,
         "OrderString": "", "TopRowCount": 0, "StartRow": start, "Limit": PAGE_SIZE}
    data = _post(s, conf, QUERY_SVC, [json.dumps(q, ensure_ascii=False)]).json()
    if isinstance(data, dict):
        return None, json.dumps(data, ensure_ascii=False)[:600]
    if data and isinstance(data[0], list) and data[0] and isinstance(data[0][0], dict):
        try:
            msg = "；".join(e.get("Message", "") for e in data[0][0]["Result"]["ResponseStatus"]["Errors"])
        except Exception:
            msg = json.dumps(data[0][0], ensure_ascii=False)[:300]
        return None, msg
    return data, None


def _fetch_resilient(s, conf, form_id, fields, filt=""):
    """取数；遇"字段不存在"自动丢该列重试；分页。返回 (rows, used_fields)。"""
    cur = list(fields)
    for _ in range(len(fields) + 2):
        keys = ",".join(k for k, _ in cur)
        rows, err = _query_raw(s, conf, form_id, keys, filt, 0)
        if err is None:
            all_rows = list(rows)
            start = len(rows)
            while len(rows) == PAGE_SIZE:
                rows, err2 = _query_raw(s, conf, form_id, keys, filt, start)
                if err2 or not rows:
                    break
                all_rows += rows
                start += len(rows)
            return all_rows, cur
        bad = _bad_field(err)
        dropped = False
        if bad:
            for k, h in list(cur):
                if (k == bad or k.split(".")[0] == bad.split(".")[0]) and h != "银行账号":
                    cur = [(kk, hh) for kk, hh in cur if kk != k]
                    dropped = True
                    break
        if not dropped:
            raise KingdeeError(f"查询 {form_id} 报错(无法自愈)：{err[:200]}")
    return [], cur


def _unwrap_row(r, ncol):
    while isinstance(r, (list, tuple)) and len(r) == 1 and isinstance(r[0], (list, tuple)) and len(r[0]) >= ncol:
        r = r[0]
    return r


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return ",".join(_cell(x) for x in v)
    if isinstance(v, dict):
        return v.get("FName") or v.get("Name") or ""
    return v


def _get_orgs(s, conf):
    """核算组织清单 [(FNumber, FName)]；查不到用手工主体名兜底。"""
    for form in _ORG_FORMS:
        try:
            data, err = _query_raw(s, conf, form, "FNumber,FName", "", 0)
            if err is None and data:
                orgs = []
                for r in data:
                    rr = _unwrap_row(r, 2)
                    if isinstance(rr, list) and len(rr) >= 2:
                        orgs.append((str(rr[0]), str(rr[1])))
                if orgs:
                    return orgs
        except Exception:
            continue
    return [("", n) for n in _MANUAL_ORG_NAMES]


def _rows_to_dicts(rows, used):
    uheaders = [h for _, h in used]
    out = []
    for r in rows:
        rr = _unwrap_row(r, len(used))
        cells = [_cell(c) for c in rr] if isinstance(rr, (list, tuple)) else [_cell(rr)]
        out.append(dict(zip(uheaders, cells)))
    return out


def fetch_bank_accounts(s=None, conf=None):
    """出纳·银行账号(CN_BANKACNT) 全量。自愈式认列(不写死字段Key) + 按组织遍历去重合并
    (组织隔离基础资料默认只回登录用户主职组织，故逐组织带 FUseOrgId 各查一次)。
    返回 list[dict]（表头对齐 account_ledger.kd_accounts_from_cn：银行账号/账户名称/开户行/银行类别/所属组织/币别/禁用状态）。"""
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    catalog = _field_catalog(s, conf, "CN_BANKACNT")
    fields = _resolve_bank_fields(catalog)
    orgs = _get_orgs(s, conf)
    merged, seen = [], set()
    for num, name in orgs:
        filt = f"{_ORG_FILTER_FIELD}.FNumber='{num}'" if num else f"{_ORG_FILTER_FIELD}.FName='{name}'"
        try:
            rows, used = _fetch_resilient(s, conf, "CN_BANKACNT", fields, filt)
        except KingdeeError:
            continue
        for d in _rows_to_dicts(rows, used):
            acct = d.get("银行账号", "")
            if acct and acct not in seen:
                seen.add(acct)
                merged.append(d)
    if not merged:                       # 逐组织一个没取到 → 退回无过滤(至少当前登录组织)
        rows, used = _fetch_resilient(s, conf, "CN_BANKACNT", fields, "")
        for d in _rows_to_dicts(rows, used):
            acct = d.get("银行账号", "")
            if acct and acct not in seen:
                seen.add(acct)
                merged.append(d)
    return merged


def fetch_gl_voucher(year, period, prefix="1002", s=None, conf=None):
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    flt = f"FAccountID.FNumber like '{prefix}%' and FYear={year} and FPeriod={period}"
    return _query(s, conf, "GL_VOUCHER", GL_VOUCHER_FIELDS, flt, "FDATE")


# 科目余额表视图用：序时账带科目编码/名称（区分 1002.01 等明细科目），一次 OR 过滤拉四类资金科目。
GL_VOUCHER_SUBJ_FIELDS = GL_VOUCHER_FIELDS + [
    ("FAccountID.FNumber", "科目编码"), ("FAccountID.FName", "科目名称"),
    ("FACCOUNTBOOKID.FName", "账簿")]   # V2.238：本期新开维度只在序时账里，建记录要认主体


def fetch_gl_voucher_subjects(year, period, prefixes=("1001", "1002", "1012", "1101"), s=None, conf=None):
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    pref = " or ".join(f"FAccountID.FNumber like '{p}%'" for p in prefixes)
    flt = f"({pref}) and FYear={year} and FPeriod={period}"
    return _query(s, conf, "GL_VOUCHER", GL_VOUCHER_SUBJ_FIELDS, flt, "FDATE")


def fetch_voucher_count(year, period, book_code, s=None, conf=None):
    """凭证归档号段体检用：该账簿该期间「记」字凭证的最大号 = 当月凭证张数。
    取 GL_VOUCHER 的 FVOUCHERGROUPNO（凭证字内序号），按账簿+期间过滤，取最大值。只读。"""
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    flt = f"FACCOUNTBOOKID.FNumber='{book_code}' and FYear={year} and FPeriod={period}"
    rows = _query(s, conf, "GL_VOUCHER",
                  [("FVOUCHERGROUPNO", "号"), ("FVOUCHERGROUPID.FName", "字")],
                  flt, "FVOUCHERGROUPNO")
    nums = []
    for r in rows:
        try:
            nums.append(int(r.get("号") or 0))
        except (TypeError, ValueError):
            pass
    return max(nums) if nums else 0


def fetch_gl_voucher_income(year, period, prefixes=("6101", "6111", "6603"), s=None, conf=None):
    """理财收益/损益科目序时账（理财对账用：补赎回收益腿）。
    6101 公允价值变动损益 / 6111 投资收益 / 6603 财务费用（本账套理财利息收入冲减 6603.02）。"""
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    pref = " or ".join(f"FAccountID.FNumber like '{p}%'" for p in prefixes)
    flt = f"({pref}) and FYear={year} and FPeriod={period}"
    return _query(s, conf, "GL_VOUCHER", GL_VOUCHER_SUBJ_FIELDS, flt, "FDATE")


SUPPLIER_FIELDS = [
    ("FNumber", "供应商编码"), ("FName", "供应商名称"), ("FDocumentStatus", "单据状态"),
    ("FForbidStatus", "禁用状态"), ("FGroup.FNumber", "分组"),
]


def fetch_suppliers(s=None, conf=None):
    """金蝶供应商基础资料（BD_Supplier）全量。供物流计提「供应商核对闸门」比对 + 录入取编码用。只读。
    返回 list[dict]：供应商编码 / 供应商名称 / 单据状态 / 禁用状态 / 分组。"""
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    # 只取已审核(C)且未禁用(A)的正式供应商
    flt = "FDocumentStatus='C' and FForbidStatus='A'"
    return _query(s, conf, "BD_Supplier", SUPPLIER_FIELDS, flt, "FNumber")


def supplier_code_map(suppliers):
    """供应商全名 -> (编码, 分组编码)。同名多档案时优先物流供应商分组（供应商009，1-6月计提凭证
    应付行实证全挂此分组），其次编码带「物流运输服务」的档案——避免误挂到押金等其它档案。"""
    m = {}
    for r in suppliers:
        name = str(r.get("供应商名称") or "").strip()
        code = str(r.get("供应商编码") or "").strip()
        grp = str(r.get("分组") or "").strip()
        if not name or not code:
            continue
        prio = 0 if grp == "供应商009" else (1 if "物流运输服务" in code else 2)
        old = m.get(name)
        if old is None or prio < old[2]:
            m[name] = (code, grp, prio)
    return {k: (v[0], v[1]) for k, v in m.items()}


# ---------------- 写入（仅物流计提一键录入使用；建草稿态，提交/审核始终人在金蝶做） ----------------
def _save_result(res):
    """解析 Save/Delete 返回。成功返回 Result dict，失败抛 KingdeeError（拼出可读错误）。"""
    try:
        result = res["Result"]
        status = result["ResponseStatus"]
        if status.get("IsSuccess"):
            return result
        errs = status.get("Errors") or []
        msg = "；".join(str(e.get("Message", "")) for e in errs) or json.dumps(status, ensure_ascii=False)[:300]
    except (KeyError, TypeError):
        msg = json.dumps(res, ensure_ascii=False)[:300]
    raise KingdeeError(msg)


def save_voucher(model, s=None, conf=None):
    """GL_VOUCHER 建一张凭证草稿。model 由 kernels.logistics_accrual.build_kd_model 生成。
    成功返回 {"id": 内码, "billno": 单据编号}；失败抛 KingdeeError（金蝶不落任何数据）。"""
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    res = _post(s, conf, SAVE_SVC, ["GL_VOUCHER", json.dumps({"Model": model}, ensure_ascii=False)]).json()
    result = _save_result(res)
    return {"id": result.get("Id"), "billno": str(result.get("Number") or "")}


def delete_vouchers(ids, s=None, conf=None):
    """按内码删凭证（清理测试草稿用）。ids 可为单个或列表。"""
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    if not isinstance(ids, (list, tuple)):
        ids = [ids]
    res = _post(s, conf, DELETE_SVC, ["GL_VOUCHER", json.dumps({"Ids": ",".join(str(i) for i in ids)})]).json()
    _save_result(res)
    return True


def view_voucher(vid, s=None, conf=None):
    """View 按内码读一张凭证（草稿态 ExecuteBillQuery 查不到，View 查得到——2026-07-06 实证：
    保存即分配记-字号 VOUCHERGROUPNO，状态 A=创建）。
    返回 {"exists": bool, "billno": 单据编号, "vno": 凭证字号, "status": 单据状态}。"""
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    try:
        res = _post(s, conf, VIEW_SVC, ["GL_VOUCHER", json.dumps({"Id": str(vid)})]).json()
        result = res.get("Result", {})
        status = result.get("ResponseStatus") or {}
        if status and not status.get("IsSuccess", True):
            return {"exists": False, "billno": "", "vno": "", "status": ""}
        m = result.get("Result") or {}
        if not isinstance(m, dict) or not m.get("Id"):
            return {"exists": False, "billno": "", "vno": "", "status": ""}
        return {"exists": True, "billno": str(m.get("BillNo") or ""),
                "vno": str(m.get("VOUCHERGROUPNO") or ""), "status": str(m.get("DocumentStatus") or "")}
    except (KingdeeError, ValueError):
        return {"exists": False, "billno": "", "vno": "", "status": ""}


# ---------------- 通用单据写入（V2.158，不写死 formid；汇率工具写 BD_Rate 用） ----------------
def save_bill(form_id, model, s=None, conf=None):
    """通用 Save：建一条单据草稿。form_id 如 "BD_Rate"；model 为该单据的 Model dict。
    成功返回 {"id": 内码, "billno": 编号}；失败抛 KingdeeError（金蝶不落任何数据）。"""
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    res = _post(s, conf, SAVE_SVC, [form_id, json.dumps({"Model": model}, ensure_ascii=False)]).json()
    result = _save_result(res)
    return {"id": result.get("Id"), "billno": str(result.get("Number") or "")}


def submit_bill(form_id, ids, s=None, conf=None):
    """通用 Submit：把已保存的单据提交（仅提交，不审核）。ids 可为单个或列表。
    审核（生效）刻意不实现——留给人在金蝶原生审核流做（确认书 v1.1 D11）。"""
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    if not isinstance(ids, (list, tuple)):
        ids = [ids]
    res = _post(s, conf, SUBMIT_SVC, [form_id, json.dumps({"Ids": ",".join(str(i) for i in ids)})]).json()
    return _save_result(res)


def unsubmit_bill(form_id, ids, s=None, conf=None):
    """撤销提交（CancelAssign）：把已提交(状态B)未审核的单据退回暂存态。
    2026-08-01 实测：金蝶「单据状态为暂存、创建或重新审核的数据才允许删除」，故提交态要删须先撤销。
    供"撤销本期录入"及清理用；已审核(状态C)的须人在金蝶反审核，本工具不做。"""
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    if not isinstance(ids, (list, tuple)):
        ids = [ids]
    res = _post(s, conf, CANCELASSIGN_SVC, [form_id, json.dumps({"Ids": ",".join(str(i) for i in ids)})]).json()
    return _save_result(res)


def delete_bill(form_id, ids, s=None, conf=None):
    """通用 Delete：按内码删单据（清理测试/撤销未审核记录用）。ids 可为单个或列表。
    注意：提交态(B)须先 unsubmit_bill 撤销、已审核(C)须人反审核，否则金蝶拒删。"""
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    if not isinstance(ids, (list, tuple)):
        ids = [ids]
    res = _post(s, conf, DELETE_SVC, [form_id, json.dumps({"Ids": ",".join(str(i) for i in ids)})]).json()
    _save_result(res)
    return True


# 汇率单据（BD_Rate）只读回查字段——去重（同组织×同币对×同生效区间不覆盖）与历史复核用
BD_RATE_FIELDS = [
    ("FRATEID", "FRATEID"),   # BD_Rate 内码字段名是 FRATEID（非 FID，2026-08-01 实测），删除/回查用
    ("FRATETYPEID.FNumber", "汇率类型"),
    ("FCyForID.FNumber", "原币码"), ("FCyForID.FName", "原币"),
    ("FCyToID.FNumber", "目标币码"), ("FCyToID.FName", "目标币"),
    ("FExchangeRate", "汇率"),
    ("FBegDate", "生效"), ("FEndDate", "失效"),
    ("FCreateOrgId.FNumber", "创建组织"), ("FUseOrgId.FNumber", "使用组织"),
    ("FDocumentStatus", "状态"),   # Z暂存/A创建/B已提交(待审核)/C已审核/D重新审核——状态看板判待审核vs已审核
    ("FDescription", "描述"),      # 工具写入时盖「标记+算式/出处」于此：判工具/人工来源 + 审核可核（见 fx_rate.build_desc）
]


def fetch_bd_rate(use_org=None, beg_from=None, s=None, conf=None):
    """只读回查 BD_Rate。use_org 按使用组织编码过滤；beg_from 只取生效日≥该值（'YYYY-MM-DD'）。"""
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    conds = []
    if use_org:
        conds.append(f"FUseOrgId.FNumber = '{use_org}'")
    if beg_from:
        conds.append(f"FBegDate >= '{beg_from}'")
    flt = " and ".join(conds) if conds else ""
    return _query(s, conf, "BD_Rate", BD_RATE_FIELDS, flt, "FBegDate")


# ---------------- 物流对账取数（V2.44，只读） ----------------
# 四种单据的字段按 2026-07-08 实单探查：销售出库单含 收货地址/基本单位数量(=千克)；
# 其他出库/分步式调拨无统一 kg 字段，取到什么给什么，缺列自动降级不报错。
_OUTBOUND_FORMS = [
    ("SAL_OUTSTOCK", "销售出库单",
     [("FBillNo", "单号"), ("FDate", "日期"), ("FCustomerID.FName", "客户"),
      ("FMaterialID.FName", "物料"), ("FRealQty", "数量"), ("FBaseUnitQty", "kg"),
      ("FReceiveAddress", "收货地址"), ("FStockOrgId.FName", "库存组织"),
      ("FMaterialID.FNumber", "物料编码"), ("FBaseUnitId.FName", "基本单位")]),
    ("STK_MisDelivery", "其他出库单",
     [("FBillNo", "单号"), ("FDate", "日期"), ("FDeptId.FName", "客户"),
      ("FMaterialID.FName", "物料"), ("FQty", "数量"), ("FBaseQty", "kg"), ("FStockOrgId.FName", "库存组织"),
      ("FMaterialID.FNumber", "物料编码"), ("FBaseUnitId.FName", "基本单位")]),
    ("STK_TransferOut", "分步式调出单",
     [("FBillNo", "单号"), ("FDate", "日期"), ("FMaterialId.FName", "物料"),
      ("FQty", "数量"), ("FBaseQty", "kg"), ("FStockOrgId.FName", "客户"),
      ("FMaterialID.FNumber", "物料编码"), ("FBaseUnitId.FName", "基本单位")]),   # FSettleOrgId 本账套报错、去掉；调拨往来＝库存组织
    ("STK_TransferIn", "分步式调入单",
     [("FBillNo", "单号"), ("FDate", "日期"), ("FMaterialId.FName", "物料"),
      ("FQty", "数量"), ("FBaseQty", "kg"), ("FStockOrgId.FName", "客户"),
      ("FMaterialID.FNumber", "物料编码"), ("FBaseUnitId.FName", "基本单位")]),   # FSettleOrgId 本账套报错、去掉；调拨往来＝库存组织
]


def fetch_outbound_docs(date_from, date_to, forms=None, s=None, conf=None):
    """物流对账用：四种出库/调拨单分录级取数（只读）。

    date_from/date_to: 'YYYY-MM-DD'。forms: 限定单据类型（FormId 列表），默认全部四种。
    返回 list[dict]，每行含 form/单号/日期/客户/物料/数量/kg/收货地址（缺列为 None）。
    单个字段本账套不认时逐字段降级重试（复用银行账号取数的自愈思路，简化为丢列）。
    """
    if s is None or conf is None:
        s, conf = login()
    filt = f"FDate>='{date_from}' and FDate<='{date_to}'"
    out = []
    for form_id, form_name, fields in _OUTBOUND_FORMS:
        if forms and form_id not in forms:
            continue
        cols = list(fields)
        while True:
            try:
                rows = _query(s, conf, form_id, cols, filt)
                break
            except KingdeeError as e:
                # 丢掉报错字段重试；只剩单号/日期还失败就放弃该单据类型
                if len(cols) <= 2:
                    rows = []
                    break
                cols = cols[:-1]
        for r in rows:
            for k in ("kg", "收货地址", "客户", "物料", "数量", "物料编码", "基本单位"):
                r.setdefault(k, None)
            r["form"] = form_id
            r["form_name"] = form_name
            out.append(r)
    return out


# ---------------- BOM报价审核·价格校验：按物料编码查金蝶实际采购价（V-draft，只读） ----------------
# 用途：成本会计核研发填的含税采购价 vs 金蝶实际。应付单为主（最接近实际结算含税单价），
#       采购订单/入库单兜底（供应商报价+型号规格）。字段Key 按通用规则给一版、逐字段自愈降级——
#       确切 FormId/字段名要在服务器按本账套实测一轮（同平台其它金蝶取数惯例）。
# 字段Key 按本账套元数据实测（2026-09-02）：应付单 含税单价=FPRICE_P、数量=FQTY_P、税率=FTaxRate。
# 采购订单 含税单价字段本账套物料行未探明（标准 FTaxPrice 返回空）——暂以应付单为准（业务方定「应付单为主」），
# 采购订单先不并入以免出空价行；日后探明其字段再加。
_PRICE_FORMS = [
    ("AP_Payable", "应付单",
     [("FBillNo", "单号"), ("FDate", "日期"), ("FSupplierId.FName", "供应商"),
      ("FMaterialId.FNumber", "物料编码"), ("FMaterialId.FName", "物料"),
      ("FMaterialId.FSpecification", "规格"),
      ("FQTY_P", "数量"), ("FPRICE_P", "含税单价"), ("FTaxRate", "税率")]),
]


def fetch_material_prices(code, months=12, forms=None, limit=30, s=None, conf=None):
    """按物料编码查金蝶近 months 个月已审核单据的含税单价（应付单为主）。只读。
    返回 list[dict]：form_name/单号/日期/供应商/物料编码/物料/规格/型号/数量/含税单价/单价/税率（缺列为 None），按日期倒序。
    编码字段Key、单据类型本账套不认时逐字段/逐单据降级，不抛垮页面。"""
    import datetime as _dt
    if s is None or conf is None:
        s, conf = login()
    code = str(code or "").strip().replace("'", "''")
    if not code:
        return []
    cutoff = (_dt.date.today() - _dt.timedelta(days=int(months) * 31)).strftime("%Y-%m-%d")
    out = []
    for form_id, form_name, fields in _PRICE_FORMS:
        if forms and form_id not in forms:
            continue
        cols = list(fields)
        filt = "FMaterialId.FNumber = '%s' and FDocumentStatus = 'C' and FDate >= '%s'" % (code, cutoff)
        rows = None
        while cols:
            try:
                rows = _query(s, conf, form_id, cols, filt, order="FDate desc")
                break
            except KingdeeError:
                # 先丢价格/规格等非关键列；只剩「物料编码/日期」两列还失败 → 该单据类型放弃
                if len(cols) <= 2:
                    rows = []
                    break
                cols = cols[:-1]
        for r in (rows or []):
            for k in ("供应商", "物料", "规格", "型号", "数量", "含税单价", "单价", "税率"):
                r.setdefault(k, None)
            p = r.get("含税单价")
            if p is None or (isinstance(p, (int, float)) and p <= 0):
                continue                         # 跳过核销/调整/负数等零价行，只留有意义的实采
            r["form_name"] = form_name
            out.append(r)
    out.sort(key=lambda r: str(r.get("日期") or ""), reverse=True)
    return out[:limit]


# ---- 物料档案按「研发编码」反查物料编码（BOM报价审核 · 补物料编码提示，2026-09-05）----
# 本账套元数据实测：BD_MATERIAL 自定义字段 **F_ora_Text1 中文名「研发编码」**，值即研发 CP 码（如 300600092 → CP05113401-1（SN2））。
# 全账套 9630 个物料中 892 个带研发编码（产成品 827 / 自制半成品 64 / 委外半成品 1；前缀 CP/SZF/SZB/SZY/SHB/SHF/SHY）。
# ⚠ **同一研发编码可挂多个物料编码**（97 例，如 SZF004027 → 200000174 与 200000184、CP04119903 → 300200025 与 T00000207 临时码并存）
#   → 这里只回候选，**绝不自动写**，由成本会计确认（业务方 2026-09-05 定「检测到就提示成本会计确认」）。
# 同一物料按使用组织(101/105/107)出多行 → 按物料编码去重。标准字段 FOldNumber/FMnemonicCode/FDescription 实测皆空，不是 CP 码所在。
MATERIAL_RD_CODE_FIELD = "F_ora_Text1"
_MAT_LOOKUP_FIELDS = [("FNumber", "erpCode"), ("FName", "name"), ("FSpecification", "spec"),
                      (MATERIAL_RD_CODE_FIELD, "rdCode"), ("FCategoryID.FName", "category"),
                      ("FForbidStatus", "forbid"), ("FDocumentStatus", "doc"), ("FUseOrgId.FNumber", "org")]


def normalize_rd_code(cp):
    """研发编码比对口径：去空白、全角括号→半角、大写。"""
    return re.sub(r"\s+", "", str(cp or "")).replace("（", "(").replace("）", ")").upper()


def fetch_materials_by_rd_code(cp, s=None, conf=None):
    """按研发编码(CP 码)查金蝶物料档案 → 候选 [{erpCode,name,spec,rdCode,category,forbidden,orgs,exact}]。只读。
    精确匹配优先（原样 + 全/半角括号两种写法）；一个都没有再按**去括号前缀**近似
    （台账 CP04108204(SN5) 金蝶只有 CP04108204 / (SN2) / (SN3) → 列出来让人判）。"""
    if s is None or conf is None:
        s, conf = login()
    raw = str(cp or "").strip()
    if not raw:
        return []
    esc = lambda x: x.replace("'", "''")
    variants = {raw, raw.replace("（", "(").replace("）", ")"), raw.replace("(", "（").replace(")", "）")}
    filt = " or ".join("%s = '%s'" % (MATERIAL_RD_CODE_FIELD, esc(v)) for v in sorted(variants))
    rows = _query(s, conf, "BD_MATERIAL", _MAT_LOOKUP_FIELDS, filt, "FNumber")
    exact = True
    if not rows:
        base = re.sub(r"[（(].*$", "", raw).strip()
        if len(base) >= 6:                       # 太短的前缀不近似（避免 CP 打头全命中）
            rows = _query(s, conf, "BD_MATERIAL", _MAT_LOOKUP_FIELDS, "%s like '%s%%'" % (MATERIAL_RD_CODE_FIELD, esc(base)), "FNumber")
            exact = False
    by = {}
    for r in rows:
        code = str(r.get("erpCode") or "").strip()
        if not code:
            continue
        o = by.setdefault(code, {"erpCode": code, "name": str(r.get("name") or "").strip(), "spec": str(r.get("spec") or "").strip(),
                                 "rdCode": str(r.get("rdCode") or "").strip(), "category": str(r.get("category") or "").strip(),
                                 "forbidden": str(r.get("forbid") or "") == "B", "orgs": [], "exact": exact})
        org = str(r.get("org") or "").strip()
        if org and org not in o["orgs"]:
            o["orgs"].append(org)
    out = list(by.values())
    # 精确命中的排前；临时码(T 开头)排后，正式编码优先给成本会计看
    out.sort(key=lambda o: (not o["exact"], o["erpCode"].upper().startswith("T"), o["forbidden"], o["erpCode"]))
    return out


# 二期付款对账：按回填单号直查的 7 类单据（前 4 类同上，新增 3 类 2026-07-14 实单探查确认）。
# 字段Key 大小写各单据不同（库存基本数量：FBaseunitQty/FBaseUnitQty/FBASEUNITQTY），逐单据写死+缺列降级。
_RETURN_FORMS = [
    ("SAL_RETURNSTOCK", "销售退货单",
     [("FBillNo", "单号"), ("FDate", "日期"), ("FRetcustId.FName", "客户"),
      ("FMaterialId.FName", "物料"), ("FRealQty", "数量"), ("FBaseunitQty", "kg"),
      ("FStockOrgId.FName", "库存组织"),
      ("FMaterialID.FNumber", "物料编码"), ("FBaseUnitId.FName", "基本单位")]),   # 数量=实退(反向)
    ("STK_InStock", "采购入库单",
     [("FBillNo", "单号"), ("FDate", "日期"), ("FSupplierId.FName", "供应商"),
      ("FMaterialId.FName", "物料"), ("FRealQty", "数量"), ("FBaseUnitQty", "kg"),
      ("FStockOrgId.FName", "库存组织"),
      ("FMaterialID.FNumber", "物料编码"), ("FBaseUnitId.FName", "基本单位")]),   # 往来=供应商；FDate=入库日期
    ("PUR_MRB", "采购退料单",
     [("FBillNo", "单号"), ("FDate", "日期"), ("FSupplierID.FName", "供应商"),
      ("FMATERIALID.FName", "物料"), ("FRMREALQTY", "数量"), ("FBASEUNITQTY", "kg"),
      ("FStockOrgId.FName", "库存组织"),
      ("FMATERIALID.FNumber", "物料编码"), ("FBASEUNITID.FName", "基本单位")]),   # 数量=实退(反向)；字段全大写
]
_DOC_FORMS_BY_NO = _OUTBOUND_FORMS + _RETURN_FORMS               # 7 类


def _billno_in(nos):
    """安全拼 FBillNo in ('a','b')；去单引号防注入，空表返回恒假。"""
    vals = [str(n).replace("'", "") for n in nos if str(n).strip()]
    if not vals:
        return "1=0"
    return "FBillNo in (" + ",".join(f"'{v}'" for v in vals) + ")"


def fetch_docs_by_nos(bill_nos, forms=None, s=None, conf=None, batch=50):
    """二期付款对账：按回填单号直查金蝶 7 类单据（只读，分录级）。

    bill_nos: 单号列表。forms: 限定 FormId（默认 7 类全查）。返回 list[dict]，
    每行含 form/form_name/单号/日期/客户或供应商/物料/数量/kg/库存组织（缺列 None）。
    单号唯一属于某一单据类型，各 FormId 只返回命中它的行；查不到即该单号在金蝶不存在。
    """
    if s is None or conf is None:
        s, conf = login()
    nos = [n for n in dict.fromkeys(str(x).strip() for x in bill_nos) if n]
    out = []
    for form_id, form_name, fields in _DOC_FORMS_BY_NO:
        if forms and form_id not in forms:
            continue
        cols = list(fields)                      # 先按首批把可用列定死，避免各批列不一
        settled = False
        for i in range(0, len(nos), batch):
            filt = _billno_in(nos[i:i + batch])
            while True:
                try:
                    rows = _query(s, conf, form_id, cols, filt)
                    break
                except KingdeeError:
                    if len(cols) <= 2:
                        rows = []
                        break
                    cols = cols[:-1]
            settled = True
            for r in rows:
                # 物料编码/基本单位是 V2.152 为物料级核量新加的尾列；某单据不认时会被降级丢掉，
                # 故一律 setdefault，下游按"缺则降级为不可比"处理，不 KeyError。
                for k in ("数量", "kg", "客户", "供应商", "物料", "库存组织", "物料编码", "基本单位"):
                    r.setdefault(k, None)
                r["form"] = form_id
                r["form_name"] = form_name
                out.append(r)
        _ = settled
    return out


# ---------------- 存货收发存汇总表(跨维度) 报表取数（成本台账 API 通道，V2.61 / V2.115 / V2.116）----------------
# 实测：报表取数走 GetSysReportData（非 ExecuteBillQuery）。
# 报表无「存货类别」字段，靠物料档案(名称+规格→FCategoryID)关联。
#
# V2.116 关键订正（2026-07-16 实测）——仓库维度一直可取，此前是看错字段：
#   · 仓库字段＝`FStockId`（返回的是仓库【名称】）；`FStockName` 是哑字段、恒返回 null。
#   · `FCOMBOTotalType="3"` → 2771 行 = 2770 明细 + 1 行总计（总计行各列为空）。
#   · 每个(物料×规格×批号×库存状态)恰好落在唯一仓库 → 加仓库维度行数不变，可直接按仓库汇总。
#   · 2026-5 期实测：按仓库汇总 44/44 个仓库与成本会计底稿逐仓一致（孝感茶饮成品仓 1 分钱四舍五入尾差）。
#   · 真·「存货收发存明细表(跨维度)」`HS_InOutStockDetailRpt` 走本接口会报"此接口暂时只支持简单账表"，
#     但**无需**它——本汇总表已含全部维度。`HS_NoDimInOutStockDetailRpt` 是单据流水级、结存列为
#     按物料累计的滚动结存(不按仓库/批号重置)，不可用于按仓库聚合。
GETRPT_SVC = "Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.GetSysReportData.common.kdsvc"
RPT_INOUT_BYDATE = "HS_INOUTSTOCKSUMMARYBYDATERPT"
RPT_INOUT_CROSSDIM = "HS_INOUTSTOCKSUMMARYRPT"      # 跨维度汇总表（含仓库）
RPT_INOUT_FLOW = "HS_NoDimInOutStockDetailRpt"      # 流水级（单据级）——事务类型 FBusinessType 只在这张上
# 孝感星期九固定口径（实测落定）：核算体系/会计政策/币别
_CL_ACCTSYS = "KJHSTX01_SYS"
_CL_POLICY = "KJZC01_SYS"
_CL_CURRENCY = "PRE001"


def _rpt_num(v):
    try:
        f = float(str(v).replace(",", ""))
        # 收到 4 位小数：金蝶金额精度就是 4 位，不收的话 float 的二进制尾巴会被原样写进 Excel，
        # 和金蝶原生导出逐格比对时满屏假差异（V2.241 实测）。
        return 0.0 if f != f else round(f, 4)
    except (TypeError, ValueError):
        return 0.0


def _rpt_price(v):
    """单价专用：空 → None（不是 0.0）。

    数量/金额可以理直气壮地当 0（没动就是没动），单价不行——
    金蝶在【数量为 0】时把单价留空，若跟着 `_rpt_num` 返回 0.0，
    页面上就会显示"单价 0.00"，读的人会当成"这货单价真是零"。
    None 让上层显示「—」＝没有这个数，与"除不动就老实说没有"一贯口径一致。"""
    s = str(v).replace(",", "").strip()
    if not s:
        return None
    try:
        f = float(s)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def fetch_material_categories(s=None, conf=None):
    """物料档案 → {(名称,规格): (存货类别, 物料编码)} + {名称: (…)}(兜底)。
    存货类别=FCategoryID.FName；物料编码=FNumber。

    ⛔**存货台账已不再用它**（V2.292 起）。此处保留只为别的取数线可能要读物料档案。

    ⚠这条 docstring 原先写着"收发存跨维度报表本身给不出物料编码"，**是错的**：
    V2.130 试过 `FMaterialId`（接受但恒空）、`FMaterialNumber`/`FNumber`/`FMaterialCode`（被拒），
    就此收工——**唯独没试 `FMaterialBaseId`**，而那个恰好是对的
    （2026-08-13 实测返回 1002000008 等真实编码；存货类别同理是 `FMaterType`）。
    于是编码只能靠 (名称,规格) 反查档案，同名同规格一撞就挂到别人名下：
    🧪 107/2026-3 与业务方底稿逐物料比，**109 个物料的结存金额两两对调**。
    合计不受影响（三道勾稽一直全过），所以错了 160 多个版本没人发现。

    **教训**：几个候选名都被拒，只说明"这几个不对"，不等于"这张表没有这个字段"。
    把"我没找到"写成"它没有"，下一个人就不会再找了。"""
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    by_ns, by_n = {}, {}
    start = 0
    while True:
        q = {"FormId": "BD_MATERIAL", "FieldKeys": "FName,FSpecification,FCategoryID.FName,FNumber",
             "FilterString": "", "OrderString": "", "TopRowCount": 0, "StartRow": start, "Limit": PAGE_SIZE}
        data = _post(s, conf, QUERY_SVC, [json.dumps(q, ensure_ascii=False)]).json()
        if isinstance(data, dict):
            raise KingdeeError(f"物料档案取数报错：{json.dumps(data, ensure_ascii=False)[:200]}")
        if not data:
            break
        for r in data:
            rr = _unwrap_row(r, 4)
            nm, spec, cat, code = _cell(rr[0]), _cell(rr[1]), _cell(rr[2]), _cell(rr[3])
            by_ns[(nm, spec)] = (cat, code)
            by_n[nm] = (cat, code)
        if len(data) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return by_ns, by_n


def fetch_business_type_summary(year, period, org="107", billnos=None, wip_btypes=None, s=None, conf=None):
    """事务类型汇总（V2.141）：流水级报表按 FBusinessType 聚合 → [{bt, n, iq, ia, dq, da}]。

    数据源＝RPT_INOUT_FLOW（流水级，5 期实测 13,625 行、21 种事务类型）——
    事务类型字段 FBusinessType **只在这张表上**（跨维度汇总表没有；
    真·明细表 HS_InOutStockDetailRpt 接口不开放："此接口暂时只支持简单账表"）。

    ⚠只聚合【收入/发出发生额】，**不碰它的"结存"列**——那是按物料累计的滚动结存、
    不按仓库/批号重置（V2.87 精炼椰子油调拨实证 175→157.5 跨仓续算），拿来算结存必错。
    「期初结存/期末结存/合计」行是报表结构行、非业务事务，这里剔除——
    发生额自证：剔后 收入合计 24,222,127.26 / 发出合计 25,909,708.27，与跨维度汇总表分毫不差。

    在服务端聚合完只回 21 行、不落 13,625 行原始流水——落库的是结论不是底表。

    **V2.285：`billnos` 给了就顺路把这些单据的物料级明细也带回来**（返回 `(汇总, 明细)` 二元组）。
    用于损益归集出**物料级**货损/盘盈亏清单——业务方底稿那页要的是物料编码/名称/规格/单位/
    数量/单价/仓库，而科目余额下钻只到凭证级。**不额外打接口**：这张流水表本来就在取。
    🧪 2026-3 实证：6 张单据全部命中，发出−收入＝**18,881.52**，与底稿货损页合计分毫不差。"""
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    import calendar
    last = calendar.monthrange(int(year), int(period))[1]
    begin = f"{year}-{int(period):02d}-01"
    end = f"{year}-{int(period):02d}-{last:02d}"
    model = {
        "FACCTGSYSTEMID": {"FNumber": _CL_ACCTSYS}, "FACCTGORGID": {"FNumber": org},
        "FACCTPOLICYID": {"FNumber": _CL_POLICY}, "FBEGINDATE": begin, "FENDDATE": end,
        "FYear": str(year), "FPeriod": str(period), "FENDYEAR": str(year), "FEndPeriod": str(period),
        # ⛔**不要加 `FSTOCKORGID`（库存组织）过滤**（V2.300 血的教训，见 _STOCKORG 注）
        "FSTOCKSTATUSID": [{"FNumber": ""}], "FMATERTYPEID": {"FNUMBER": ""},
        "FDimType": "", "FCHXEXPENSE": "false", "FCHXTotal": "false", "FCHXNOINOUT": "false",
        "FCHXNOCOSTALLOT": "false", "FIsDisplayPeriod": "false", "FCHXNOSTOCKADJ": "false",
        "FCOMBOTotalType": "3", "FCOMBOSTATUS": "", "FPeriodStartDate": begin, "FCURRENCYID": {"FNumber": _CL_CURRENCY},
    }
    want = {str(x).strip() for x in (billnos or []) if str(x).strip()}
    wip_types = set(wip_btypes or ())
    wip = []
    # 要物料级明细时才多取那几列——不要就保持原样，别让常规取数白白变宽
    fields = ("FBusinessType,FReceiveQty,FReceiveAmount,FSendQty,FSendAmount" if not (want or wip_types) else
              "FBusinessType,FReceiveQty,FReceiveAmount,FSendQty,FSendAmount,"
              "FBILLNO,FMATERIALID,FMATERIALNAME,FMODEL,FMATERTYPENAME,FMaterialGroup,"
              "FUNITNAME,FSTOCKNAME,FLOTNO,FRECEIVEPrice,FSENDPrice,FVOUCHER,FBILLDATE")
    rows, start = [], 0
    while True:
        para = {"FieldKeys": fields, "SchemeId": "", "StartRow": start, "Limit": 5000,
                "IsVerifyBaseDataField": "true", "FilterString": [], "Model": model}
        res = _post(s, conf, GETRPT_SVC, [RPT_INOUT_FLOW, json.dumps(para, ensure_ascii=False)]).json()
        r = res.get("Result", {}) if isinstance(res, dict) else {}
        if not r.get("IsSuccess"):
            raise KingdeeError(f"流水级报表取数失败：{json.dumps(res, ensure_ascii=False)[:250]}")
        batch = r.get("Rows") or []      # 无数据时 Rows 是 null 不是 []，见 _ROWS_NULL 注
        rows += batch
        if len(batch) < 5000:
            break
        start += 5000
    SKIP = {"期初结存", "期末结存", "合计", ""}       # 报表结构行，非业务事务
    agg, hits = {}, []
    for row in rows:
        bt = _cell(row[0])
        if bt in SKIP:
            continue
        if wip_types and bt in wip_types:
            # 勾稽②「完工结转」的业务侧底表（V2.310）：只收这几种事务类型的行。
            # 🧪 7 月 225 行 / 全表 11,984 行＝1.9%——**十几万行的流水不落库，但这 1.9% 值得留**，
            # 否则勾稽②的业务侧是个孤零零的数、无从追。用的是同一次取数，不多打一次接口。
            wip.append({"billno": _cell(row[5]), "btype": bt, "date": str(_cell(row[17]) or "")[:10],
                        "code": _cell(row[6]), "name": _cell(row[7]), "spec": _cell(row[8]),
                        "cat": _cell(row[9]), "grp": _cell(row[10]), "unit": _cell(row[11]),
                        "wh": _cell(row[12]), "batch": _cell(row[13]),
                        "qty": _rpt_num(row[1]), "price": _rpt_price(row[14]),
                        "amount": _rpt_num(row[2]), "voucher": _cell(row[16])})
        if want and _cell(row[5]) in want:
            # 金额口径＝**发出 − 收入**，与总账借方同向：盘盈是收入侧，在货损页上应显示为负
            # （底稿也是这么写的：QTRK001129 发出数量 -49、金额 -0.49）
            # 列序＝上面 fields 的顺序，共 18 列（0–17）。⚠改 fields 必须同步改这里的下标：
            # 0 事务类型 1 收入数量 2 收入金额 3 发出数量 4 发出金额 5 单据编号 6 物料编码
            # 7 物料名称 8 规格 9 存货类别 10 物料分组 11 基本单位 12 仓库 13 批号
            # 14 收入单价 15 发出单价 16 凭证字号 17 业务日期
            hits.append({"billno": _cell(row[5]), "btype": bt, "date": str(_cell(row[17]) or "")[:10],
                         "code": _cell(row[6]), "name": _cell(row[7]), "spec": _cell(row[8]),
                         "cat": _cell(row[9]), "grp": _cell(row[10]), "unit": _cell(row[11]),
                         "wh": _cell(row[12]), "batch": _cell(row[13]),
                         "qty": round(_rpt_num(row[3]) - _rpt_num(row[1]), 4),
                         "price": _rpt_price(row[15]) if _rpt_num(row[3]) else _rpt_price(row[14]),
                         "amount": round(_rpt_num(row[4]) - _rpt_num(row[2]), 2),
                         "voucher": _cell(row[16])})
        a = agg.setdefault(bt, {"bt": bt, "n": 0, "iq": 0.0, "ia": 0.0, "dq": 0.0, "da": 0.0})
        a["n"] += 1
        a["iq"] += _rpt_num(row[1]); a["ia"] += _rpt_num(row[2])
        a["dq"] += _rpt_num(row[3]); a["da"] += _rpt_num(row[4])
    out = sorted(agg.values(), key=lambda a: -(abs(a["ia"]) + abs(a["da"])))
    for a in out:
        for k in ("iq", "ia", "dq", "da"):
            a[k] = round(a[k], 4)
    if want or wip_types:
        hits.sort(key=lambda x: -abs(x["amount"]))
        wip.sort(key=lambda x: -abs(x["amount"]))
        return out, hits, wip          # ⚠三元组：调用方按 (汇总, 单据回查明细, 完工入库明细) 解
    return out


def fetch_inventory_period_totals(year, p_from, p_to, org="107", s=None, conf=None):
    """多期收发存合计（供存货看板）→ [{period, iq,ia,dq,da,eq,ea, n}]，按会计期间聚合。

    ⚠**V2.280 起改为逐期各取一次，不再用区间一次取**。原实现走 `FIsDisplayPeriod="true"`
    一次取整段（V2.254），当时只验了区间末期 P5 与单期一致就上线了——**中间月份是错的**：

        区间 P3–P3 / P3–P4 / P3–P5   3 月结存 11,914,577.88  ✓ 与单期一致
        区间 P1–P3 / P1–P5 / P1–P12  3 月结存 11,915,738.06  ✗ 多 1,160.18（多 1 行）
        区间 P2–P3                   3 月结存 11,914,591.48  ✗ 多 13.60

    **只要区间起点早于该月，那个月就被带进前期残留，且起点越早带得越多**。
    实测暴露方式：业务方拿 3 月底稿与工具对，底稿 11,914,577.88（＝单期取，分毫不差），
    而看板显示 11,915,738.06——同一个月两个数。

    代价是 N 次往返换 N 期（5 期约 15 秒，原先约 5 秒）。**这个代价必须付**：
    看板与台账导出显示的必须是同一个数，否则整个工具的可信度就没了。
    区间取数的坑留在这里当反例，别再"省一次调用"。"""
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    out = []
    for p in range(int(p_from), int(p_to) + 1):
        rows = fetch_inventory_summary(year, p, org=org, s=s, conf=conf)["rows"]
        if not rows:
            continue
        a = {"period": "%d.%02d" % (int(year), p), "n": len(rows),
             "iq": 0.0, "ia": 0.0, "dq": 0.0, "da": 0.0, "eq": 0.0, "ea": 0.0, "cats": {}}
        for r in rows:
            for k in ("iq", "ia", "dq", "da", "eq", "ea"):
                a[k] += r.get(k) or 0.0
            c = a["cats"].setdefault(r.get("cat") or "（未分类）", {"ea": 0.0, "ia": 0.0, "da": 0.0})
            c["ea"] += r.get("ea") or 0.0
            c["ia"] += r.get("ia") or 0.0
            c["da"] += r.get("da") or 0.0
        for k in ("iq", "ia", "dq", "da", "eq", "ea"):
            a[k] = round(a[k], 2)
        a["cats"] = {c: {k: round(v, 2) for k, v in d.items()} for c, d in a["cats"].items()}
        out.append(a)
    return out


def fetch_inventory_bydate(year, period, org="107", s=None, conf=None):
    """存货收发存汇总表【按日期】取数（V2.255）→ [{oa,ia,da,ea}]，供勾稽①两表互勾。

    起因：`RPT_INOUT_BYDATE` 这个 formid 从 V2.53 起就定义在这里，但**一直没有取数函数**——
    勾稽①的第二张表只能从🅱上传的工作簿里拆，于是"两表互勾必须上传"被当成了技术限制。
    实际不是：业务方 2026-08-10 提供的官方接口文档表明这张表同样走 GetSysReportData。

    ⚠Model 与跨维度表**不同**：本表**有** `FBEGINDATE`/`FENDDATE`/`FPeriodStartDate`（跨维度表没有，
    以前给跨维度表发这三个纯属多余、靠金蝶忽略才没炸）；`FCOMBOTotalType`(汇总依据) 与
    `FDimType`(显示维度) 两者皆为必填。

    只取勾稽①用得上的四个金额列——这张表在本工具里**唯一用途就是互相验证合计**，
    多取列既慢又会诱使别处误用它当明细（明细以跨维度表为准，那张才带仓库维度）。
    剔小计/总计行仍用【库存状态为空】判据（V2.115）。"""
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    import calendar
    last = calendar.monthrange(int(year), int(period))[1]
    begin = f"{year}-{int(period):02d}-01"
    end = f"{year}-{int(period):02d}-{last:02d}"
    model = {
        "FACCTGSYSTEMID": {"FNumber": _CL_ACCTSYS}, "FACCTGORGID": {"FNumber": org},
        "FACCTPOLICYID": {"FNumber": _CL_POLICY},
        "FBEGINDATE": begin, "FENDDATE": end, "FPeriodStartDate": begin,
        "FYear": str(year), "FPeriod": str(period), "FENDYEAR": str(year), "FEndPeriod": str(period),
        "FMATERIALID": {"FNumber": ""}, "FENDMATERIALID": {"FNumber": ""},
        "FEXPENID": {"FNumber": ""}, "FENDEXPENID": {"FNumber": ""},
        "FACCTGRANGEID": {"FNumber": ""}, "FOwnerID": {"FNumber": ""},
        # ⛔**不要加 `FSTOCKORGID`（库存组织）过滤**（V2.300 血的教训，见 _STOCKORG 注）
        "FSTOCKSTATUSID": [{"FNumber": ""}],
        "FSTOCKId": {"FNumber": ""}, "FENDSTOCKID": {"FNumber": ""},
        "FMATERTYPEID": {"FNUMBER": ""},
        "FCOMBOTotalType": "3", "FDimType": "",
        "FCHXEXPENSE": "false", "FCHXTotal": "false", "FCHXNOINOUT": "false",
        "FCHXNOCOSTALLOT": "false", "FIsDisplayPeriod": "false", "FCHXNOSTOCKADJ": "false",
        "FCOMBOSTATUS": "", "FCURRENCYID": {"FNumber": _CL_CURRENCY},
    }
    # V2.278：由 4 个金额列扩到全列——导出要出「按日期（金蝶原样）」这张原始页，
    # 不再只是勾稽①的一个加数。tie_two_reports 只用 oa/ia/da/ea，扩列不影响它。
    fields = ("FStockStatusName,FMATERIALBASEID,FMATERIALNAME,FMODEL,FMATERTYPE,FMATERIALGROUP,"
              "FLOTNO,FUNITNAME,FINITQty,FINITPrice,FINITAMOUNT,FRECEIVEQty,FRECEIVEPrice,"
              "FRECEIVEAmount,FSENDQty,FSENDPrice,FSENDAmount,FENDQty,FENDPrice,FENDAmount")
    rows, start = [], 0
    while True:
        para = {"FieldKeys": fields, "SchemeId": "", "StartRow": start, "Limit": 5000,
                "IsVerifyBaseDataField": "true", "FilterString": [], "Model": model}
        res = _post(s, conf, GETRPT_SVC, [RPT_INOUT_BYDATE, json.dumps(para, ensure_ascii=False)]).json()
        r = res.get("Result", {}) if isinstance(res, dict) else {}
        if not r.get("IsSuccess"):
            raise KingdeeError(f"按日期收发存表取数失败：{json.dumps(res, ensure_ascii=False)[:250]}")
        batch = r.get("Rows") or []      # 无数据时 Rows 是 null 不是 []，见 _ROWS_NULL 注
        rows += batch
        if len(batch) < 5000:
            break
        start += 5000
    out = []
    for row in rows:
        if _cell(row[0]) in ("", None):       # 库存状态为空＝小计/总计行
            continue
        out.append({"status": _cell(row[0]), "code": _cell(row[1]), "name": _cell(row[2]),
                    "spec": _cell(row[3]), "cat": _cell(row[4]), "grp": _cell(row[5]),
                    "batch": _cell(row[6]), "unit": _cell(row[7]),
                    "oq": _rpt_num(row[8]), "op": _rpt_price(row[9]), "oa": _rpt_num(row[10]),
                    "iq": _rpt_num(row[11]), "ip": _rpt_price(row[12]), "ia": _rpt_num(row[13]),
                    "dq": _rpt_num(row[14]), "dp": _rpt_price(row[15]), "da": _rpt_num(row[16]),
                    "eq": _rpt_num(row[17]), "ep": _rpt_price(row[18]), "ea": _rpt_num(row[19])})
    return out


# ⚠两种写法都要认：存货那边写「单据号QTRK001129…」，资产处置那边写「单据编号PRODIS00000079 的卡片处置」。
# 只认「单据号」会把 6711 全漏掉——实测 2026-5 期漏掉 5 笔共 8,912.37（正是底稿的营业外支出数）。
RPT_COST_CALC = "CB_CostCalBill"          # 成本计算单（产品成本核算）


def fetch_cost_calc(year, period, org="107", s=None, conf=None):
    """成本计算单（V2.257）→ 规范化明细行，供制造费用三道勾稽与「车间×成本项目」透视。

    ⚠**`FSHOWWAY` 必填但官方文档没给值域**，实测（107/2026-3）：
      ''/'2'/'3'/'4' → 394 行工单级汇总，**成本项目全空**（只看这个会以为报表不给成本项目）；
      '0'            → 展开但成本项目仍空；
      **'1'          → 完整树形展开**：工单 → 成本项目 → 子项费用项目，16,379 行。
    本函数固定用 '1'。3 月按成本项目汇总＝制造费用 2,275,015.57 / 直接人工 1,625,191.99，
    与业务方底稿「制造费用（3月）」页分毫不差。

    ⚠**树形结构两条坑**：
      ①车间/产品/工单只在父行出现，子行是空的 → 必须**向下填充**，否则车间维度全丢；
      ②成本项目行的金额＝其下费用项目之和 → **两层不能一起求和**，会翻倍。
        故每行标 `level`：'item'＝成本项目层，'exp'＝费用项目层，各取各的。

    ⚠委外订单（工单前缀 SUB）**不走生产成本科目**，`outsourced=True` 单独标出——
    3 月 494,998.06，硬并进车间会让车间合计对不上总额。"""
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    import calendar
    last = calendar.monthrange(int(year), int(period))[1]
    model = {
        "FACCTGSYSTEMID": {"FNumber": _CL_ACCTSYS}, "FACCTGORGID": {"FNumber": org},
        "FACCTPOLICYID": {"FNumber": _CL_POLICY}, "FCurrencyID": {"FNumber": _CL_CURRENCY},
        "FYear": str(year), "FEndYear": str(year), "FPeriod": str(period), "FEndPeriod": str(period),
        "FSTARTDATE": f"{year}-{int(period):02d}-01", "FENDDATE": f"{year}-{int(period):02d}-{last:02d}",
        "FSTARTCOSTCID": {"FNumber": ""}, "FENDCOSTCID": {"FNumber": ""},
        "FSTARTBILLNO": "", "FENDBILLNO": "", "FFPROORDERTYPE": "", "FBILLTYPE": "",
        "FMULBILLTYPE": "", "FOUTSRCMULCOMBOX": "",
        "FSTARTMATERIALID": {"FNumber": ""}, "FENDMATERIALID": {"FNumber": ""},
        "FENDPROORDERID": "", "FSTARTPROORDERID": "",
        "FSTARTEXPENSEID": {"FNumber": ""}, "FENDEXPENSEID": {"FNumber": ""},
        "FSTARTCOSTITEMID": {"FNUMBER": ""}, "FENDCOSTITEMID": {"FNUMBER": ""},
        "FISSHOWDETAIL": "true", "FISSHOWMATERIA": "true", "FSHOWWAY": "1",
        "FShowIndirectExpenseDetial": "true", "FSumGist": "",
        "FStartOrderBillNo": "", "FEndOrderBillNo": "",
        "FNOINCOMPNOSHOW": "false", "FSumPeriod": "false",
        "FISMERGECOSTITEM": "false", "FISPROCESSEXPDETAIL": "true",
    }
    fields = ("FCOSTCENTERNAME,FPRODUCTID_FNUMBER,FPRODUCTID_FNAME,FPRODUCTNO,FBILLTYPENAME,"
              "FCOSTITEMID_FNAME,FEXPENSEITEMFIELD_FNAME,FCurrInputAmount,FCompleteQty,FCompleteAmount")
    raw, start = [], 0
    while True:
        para = {"FieldKeys": fields, "SchemeId": "", "StartRow": start, "Limit": 10000,
                "IsVerifyBaseDataField": "true", "FilterString": [], "Model": model}
        res = _post(s, conf, GETRPT_SVC, [RPT_COST_CALC, json.dumps(para, ensure_ascii=False)]).json()
        r = res.get("Result", {}) if isinstance(res, dict) else {}
        if not r.get("IsSuccess"):
            raise KingdeeError(f"成本计算单取数失败：{json.dumps(res, ensure_ascii=False)[:250]}")
        batch = r.get("Rows") or []      # 无数据时 Rows 是 null 不是 []，见 _ROWS_NULL 注
        raw += batch
        if len(batch) < 10000:      # 单页上限 10000，3 月 16,379 行必须分页
            break
        start += 10000
    cc = pno = pnm = wo = bt = ""
    out = []
    for row in raw:
        cc = _cell(row[0]) or cc
        if _cell(row[1]):
            pno, pnm = _cell(row[1]), _cell(row[2])
        wo = _cell(row[3]) or wo
        bt = _cell(row[4]) or bt
        item, exp = _cell(row[5]), _cell(row[6])
        if not item and not exp:
            continue                # 纯层级行（只有工单），本身不带金额
        out.append({"cc": cc, "prod_no": pno, "prod_name": pnm, "wo": wo, "billtype": bt,
                    "level": "item" if item else "exp", "item": item, "exp": exp,
                    "amt": _rpt_num(row[7]), "cqty": _rpt_num(row[8]), "camt": _rpt_num(row[9]),
                    "outsourced": wo.upper().startswith("SUB")})
    return out


# 5001 借方按摘要归类用。生产成本的借方来源就这几类（107/2026-3 实证 1,323 笔全覆盖）：
# 制造费用归集 2,371,486.79 ／ 人工计提 1,625,721.88 ／ 生产领料 SOUT 9,530,885.54 ／
# 生产退料 SCTL -8,047.65 ／ 期末在产品成本调整 -7,889.68 ／ 其他 151,943.89。
_WIP_ADJ_KEY = "在产品成本调整"


def fetch_cost_gl(year, period, org="107", mfg="5101", wip="5001", s=None, conf=None):
    """制造费用/生产成本两个科目的本期发生额（V2.257），供三道成本勾稽。

    返回 {mfg_debit, wip_debit, wip_credit, wip_adjust}。
    `wip_adjust`＝5001 借方里「期末在产品成本调整」那几笔——**它进总账但不算本期投入**，
    是勾稽③两端差额的全部来源（107/2026-3 实证 -7,889.68，补上后两端分毫不差）。
    ⚠只能看本期发生额：成本类科目期末结转后余额为 0。"""
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    fields = [("FDEBIT", "借"), ("FCREDIT", "贷"), ("FEXPLANATION", "摘要")]

    def pull(acct):
        flt = (f"FACCOUNTID.FNumber like '{acct}%' and FYear={year} and FPeriod={period}"
               f" and FACCOUNTBOOKID.FNumber='{org}'")
        return _query(s, conf, "GL_VOUCHER", fields, flt, "FDEBIT")

    m = pull(mfg)
    w = pull(wip)
    return {
        "mfg_debit": round(sum(_num_or_zero(r.get("借")) for r in m), 2),
        "wip_debit": round(sum(_num_or_zero(r.get("借")) for r in w), 2),
        "wip_credit": round(sum(_num_or_zero(r.get("贷")) for r in w), 2),
        "wip_adjust": round(sum(_num_or_zero(r.get("借")) for r in w
                                if _WIP_ADJ_KEY in str(r.get("摘要") or "")), 2),
    }


# ── 损益归集的分类维度（V2.308 起用【费用项目】，不再靠摘要猜）────────────
# 6602/6711 的凭证分录挂着核算维度：`FDETAILID.FFLEX9` ＝ **费用项目**、`FFLEX5` ＝ 部门。
# 🧪 实测取值：产品货损／包材货损／原辅料货损／产品盘盈亏／原辅料盘盈亏／包材盘盈亏
#            ／处置固定资产净损益／产品领用福利／捐赠／快递费／项目认证服务费…
# 这正是成本会计底稿《货损明细-管理费》按「类别」分的那套口径。
# ⚠「盘盈亏」和「货损」**同归一档，这是 Owner 定的口径，不是遗漏**（2026-08-18 拍板）。
#   业务方一度提出「管理费用只列货损，盘盈亏应该在盘盈亏」，实测后改判：
#   🧪 他们自己 7 月的《成本台账-孝感九7月》「货损明细-管理费」第 2 行就是
#     `QTRK001180 原辅料盘盈亏 −4.00`，且算进了该页合计 324,529.81 ——
#     因为那笔在金蝶挂的是【费用项目=原辅料盘盈亏、科目=6602 管理费用】，
#     做表的人照**总账落的位置**放，不是照类别放。他们那张「盘盈亏」页绑的是 6711，
#     107 的 6711 当月 0 条分录，页也就是空的。
#   定案＝**照总账摆**：6602 的进管理费用块、6711 的进营业外块。
#   好处是永远和科目余额表对得上；代价是"盘盈亏"这个词会出现在管理费用块里——**这是有意的**。
#   👉 别"顺手修正"把盘盈亏从这里拆走，那会让本块与 6602 科目余额对不上。
_PNL_LOSS_ITEM = ("货损", "盘盈亏")        # 费用项目含这些 → 货损
_PNL_DISP_ITEM = ("处置",)                 # → 资产处置
# 摘要黑名单**降级为兜底**：只在该分录没挂费用项目时才用（见 fetch_pnl_details）
_PNL_NOT_LOSS = ("福利领用", "捐赠", "赠送", "领用")
_PNL_BILL_RE = re.compile(r"单据(?:编)?号\s*([A-Za-z0-9]+)")


# ⚠数量取 **FBaseQty（基本单位）**，不是 FQty（库存单位）：
#   🧪 T00000145 FQty=360「袋」／FBaseQty=10.8「千克」，业务方底稿写的是 10.8——
#   拿 FQty 填进去会和底稿差 33 倍，而金额都是 0、看不出来。
_BILL_LINE_FIELDS = ("FBillNo,FNote,FMaterialId.FNumber,FMaterialId.FName,"
                    "FMaterialId.FSpecification,FBaseQty,FBaseUnitID.FName,FAmount,"
                    "FStockID.FName,FLot.FNumber")


def fetch_bill_notes(billnos, s=None, conf=None):
    """单据号 → (备注, 分录行)。一次查询两用，**不为分录行多打一趟接口**。

    **备注**＝业务方底稿「备注」列填的钉钉审批单号（如 QTCK011302 → 202606041716000281241），
    此前一直靠人工从金蝶界面抄。住在【其他出库单/其他入库单的单据头 `FNote`】上，
    不在凭证、也不在收发存流水里——所以只能按单据号回查单据本身。
    分录级 `FEntryNote` 实测全空，不取。盘亏毁损单(PKSH)/资产处置(PRODIS) 不是这两种单据，
    查不到就留空，不猜。

    **分录行**（V2.314）＝用来补流水表**不吐的零金额行**。
    🧪 QTCK011302：出库单 **123** 个分录行，收发存流水表只有 **122** 行，差的是
      `T00000145 口袋蛋白脆（果木烟熏培根风味）-出口版` 360 袋 / **金额 0.00**
      ——整张单唯一的零金额行。业务方底稿照单据做故有它，工具回查走流水故没有，
      于是两边行数 124 vs 123 对不上（金额零影响）。业务方要求补齐。
    ⚠**只用来补流水没返回的行**，不能拿它替换流水行：金额口径以流水表为准
      （流水是存货账的出口，单据金额可能未结转成本）。

    返回 {"notes": {单号: 备注}, "lines": {单号: [{code,name,spec,qty,amount,wh,batch}, …]}}。"""
    if not billnos:
        return {"notes": {}, "lines": {}}
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    notes, lines = {}, {}
    want = sorted({str(b).strip() for b in billnos if str(b or "").strip()})
    for fid in ("STK_MisDelivery", "STK_MISCELLANEOUS"):
        for i in range(0, len(want), 100):          # 单据号拼进 SQL，分批免得过长
            chunk = want[i:i + 100]
            flt = "FBillNo in (%s)" % ",".join("'%s'" % b.replace("'", "") for b in chunk)
            q = {"FormId": fid, "FieldKeys": _BILL_LINE_FIELDS, "FilterString": flt,
                 "OrderString": "", "TopRowCount": 0, "StartRow": 0, "Limit": 5000}
            try:
                data = _post(s, conf, QUERY_SVC, [json.dumps(q, ensure_ascii=False)]).json()
            except Exception:
                continue
            if isinstance(data, dict) or (data and not isinstance(data[0], list)):
                continue
            for r in data:
                rr = _unwrap_row(r, 10)
                bn, nt = _cell(rr[0]), (_cell(rr[1]) or "").strip()
                if not bn:
                    continue
                if nt and bn not in notes:
                    notes[bn] = nt
                code = _cell(rr[2])
                if code:
                    lines.setdefault(bn, []).append(
                        {"code": code, "name": _cell(rr[3]) or "", "spec": _cell(rr[4]) or "",
                         "qty": _num_or_zero(rr[5]), "unit": _cell(rr[6]) or "",
                         "amount": round(_num_or_zero(rr[7]), 2),
                         "wh": _cell(rr[8]) or "", "batch": _cell(rr[9]) or ""})
    return {"notes": notes, "lines": lines}


def fetch_material_attrs(codes, s=None, conf=None):
    """物料编码 → {cat: 存货类别, grp: 物料分组}。**只给「出库单补齐行」用**（V2.314）。

    那些行零金额、零结存，**收发存跨维度表里根本没有它们**，拿不到类别/分组：
    🧪 T00000145 不在 2,487 行跨维度表里，于是补齐行的「类别」按兜底规则落成"原辅料货损"，
      而业务方底稿写的是「产品货损／植物肉」——**归错档比缺一行更糟**（会串到分类小计）。
    档案里两个字段正好对得上：`FCategoryID.FName`＝产成品→产品货损，`FMaterialGroup.FName`＝植物肉。

    ⚠BD_MATERIAL 同一编码会返回多行（多组织各一份），取第一行即可——实测各行这两个字段一致。
    通常只有一两个码要查，不会成为取数负担。"""
    if not codes:
        return {}
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    out, want = {}, sorted({str(c).strip() for c in codes if str(c or "").strip()})
    for i in range(0, len(want), 100):
        chunk = want[i:i + 100]
        flt = "FNumber in (%s)" % ",".join("'%s'" % c.replace("'", "") for c in chunk)
        q = {"FormId": "BD_MATERIAL", "FieldKeys": "FNumber,FCategoryID.FName,FMaterialGroup.FName",
             "FilterString": flt, "OrderString": "", "TopRowCount": 0, "StartRow": 0, "Limit": 5000}
        try:
            data = _post(s, conf, QUERY_SVC, [json.dumps(q, ensure_ascii=False)]).json()
        except Exception:
            continue
        if isinstance(data, dict) or (data and not isinstance(data[0], list)):
            continue
        for r in data:
            rr = _unwrap_row(r, 3)
            code = _cell(rr[0])
            if code and code not in out:
                out[code] = {"cat": _cell(rr[1]) or "", "grp": _cell(rr[2]) or ""}
    return out


def fetch_pnl_details(year, period, org="107", loss_acct="6602", disp_acct="6711", s=None, conf=None):
    """货损/盘盈亏明细（V2.256）——**照成本会计的做法，从科目余额表往下钻**。

    业务方原话：「6、7 是通过科目余额表里面的关联查询找到对应的明细的」。即：
    科目余额表 → 明细账 → 凭证分录。本函数就走这条：按【科目 + 期间 + 账簿】取 GL_VOUCHER 分录。

    **识别规则在摘要里，不需要人挑**（2026-03/107 实证）：
      · 「单据号PKSH000363的盘亏毁损单」        17,287.71
      · 「单据号QTRK001129盘盈入库的其他入库单」    -0.49
      6602 管理费用当期 18 条分录里，带「单据号」的 6 条合计 **18,881.52**，
      与底稿「货损明细-管理费」页分毫不差；其余 12 条是工伤补助/差旅/招待/快递/折旧/结转损益，
      与存货无关。所以判据＝**摘要含「单据号」**，不靠人工判断。

    ⚠只取【本期发生额】：损益类科目期末已结转、余额恒为 0，看余额什么都看不见。
    返回 {"loss": [...], "disposal": [...]}，每条 {billno, doctype, amount, voucher, date, note}。"""
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    fields = [("FACCOUNTID.FNumber", "科目"), ("FACCOUNTID.FName", "科目名"), ("FDATE", "日期"),
              ("FDEBIT", "借方"), ("FCREDIT", "贷方"), ("FEXPLANATION", "摘要"),
              ("FVOUCHERGROUPID.FName", "字"), ("FVOUCHERGROUPNO", "号"),
              # 核算维度：FFLEX9＝费用项目（分类靠它），FFLEX5＝部门（留着备查）
              ("FDETAILID.FFLEX9.FName", "费用项目"), ("FDETAILID.FFLEX5.FName", "部门")]

    def pull(acct):
        if not acct:
            return []
        flt = (f"FACCOUNTID.FNumber like '{acct}%' and FYear={year} and FPeriod={period}"
               f" and FACCOUNTBOOKID.FNumber='{org}'")
        out = []
        for r in _query(s, conf, "GL_VOUCHER", fields, flt, "FDATE"):
            note = _cell(r.get("摘要")) or ""
            m = _PNL_BILL_RE.search(note)
            if not m:                       # 没有单据号＝普通费用（差旅/折旧/结转…），与存货无关
                continue
            billno = m.group(1)
            # 单据号后面那截就是单据类型。先去空白再去「的」——资产那边写成「PRODIS…　的卡片处置」，
            # 直接 lstrip("的") 会被中间那个空格挡住，留下「 的卡片处置」。
            tail = note[m.end():].strip().lstrip("的").strip()
            out.append({"billno": billno, "doctype": tail or "（未注明单据类型）",
                        "item": _cell(r.get("费用项目")) or "", "dept": _cell(r.get("部门")) or "",
                        "amount": round(_num_or_zero(r.get("借方")) - _num_or_zero(r.get("贷方")), 2),
                        "voucher": f"{_cell(r.get('字')) or ''}{_cell(r.get('号')) or ''}",
                        "date": str(_cell(r.get("日期")) or "")[:10], "note": note,
                        "acct": _cell(r.get("科目")), "acct_name": _cell(r.get("科目名"))})
        return out

    # V2.307：**「摘要含单据号」只说明这笔与存货出入库有关，不等于货损/处置**。
    # 该判据是拿 107 验的——107 全年 6602 命中的全是「报废出库/盘亏出库/盘亏毁损/盘盈入库」，
    # 6711 全是「卡片处置」（PRODIS 固定资产），所以规则看着很准。
    # 🧪 101 深圳星期零 2026-7 一比就露馅：
    #   6602 唯一命中「单据号QTCK011431**福利领用**…广宣品的其他出库单」198.11 —— 职工福利，不是货损；
    #   6711 命中「单据号QTCK011351**捐赠**…植物肉的其他出库单」2,053.54 —— 捐赠，不是资产处置。
    #   业务方一句「星期零科目余额表应该没有货损啊」点破。
    # 故按摘要里的**业务性质词**再分一层：福利领用/捐赠/赠送/领用 → 归入 `other`，
    # **不进货损与处置合计，但也不丢**——单列出来，让人看见"这个月还有这些存货流向了损益"。
    # ⚠只用黑名单不用白名单：107 有一条「单据号QTRK001137的其他入库单」1,595.09 没有性质词，
    #   而业务方底稿把它算进货损（3 月 18,881.52 的组成部分）。白名单会把它误杀。
    def split(rows, default):
        """按【费用项目】分三类；没挂费用项目的才退回摘要黑名单兜底。"""
        keep, other = [], []
        for r in rows:
            item = r.get("item") or ""
            if item:
                if any(w in item for w in _PNL_LOSS_ITEM):
                    kind = "loss"
                elif any(w in item for w in _PNL_DISP_ITEM):
                    kind = "disp"
                else:
                    kind = "other"
            else:
                kind = "other" if any(w in (r.get("note") or "") for w in _PNL_NOT_LOSS) else default
            (keep if kind == default else other).append(r)
        return keep, other

    loss, o1 = split(pull(loss_acct), "loss")
    disp, o2 = split(pull(disp_acct), "disp")
    # 第三档**只收营业外支出那一侧**（V2.314，Owner 定案：「6602 福利，不算」）。
    #   🧪 101 深圳星期零：捐赠挂 6711 营业外支出、产品领用福利挂 6602 管理费用，
    #     去向与性质都不同；成本台账的损益归集口径只覆盖前者。
    #   🧪 107 孝感九当月 6602 里一条福利领用都没有（14 条全是货损/盘盈亏），
    #     所以这条口径**对孝感九零影响，只影响星期零**——业务方底稿也确实没算。
    # ⚠6602 那侧的非货损分录**不并进 other，但也不静悄悄扔掉**：单独放 `excluded`，
    #   页面与导出各留一行「口径外·未计入」。钱凭空少一块而没人说得清去哪了，
    #   比多列一行糟得多——业务方就是这么发现 V2.311 那个整页蒸发的 bug 的。
    return {"loss": loss, "disposal": disp, "other": o2, "excluded": o1}


def _num_or_zero(v):
    try:
        return float(str(v).replace(",", "")) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def fetch_account_books(s=None, conf=None):
    """账簿档案（`BD_AccountBook`）→ [{code,name}]，107 账套实测 8 条。

    V2.253：账簿代码就是 `GL_BALANCE.FACCOUNTBOOKID.FNumber`，也与主体档案 `book_code` 同一套。
    只在"按账簿代码取余额取到空"时调用，用于把"这主体本就没存货科目"与"代码填错了"分开说。
    ⚠业务对象标识是 `BD_AccountBook`——`AccountBook`/`GL_AccountBook`/`BD_AccountBookInfo`
    金蝶均返回「业务对象不存在」，别再试那几个。"""
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    q = {"FormId": "BD_AccountBook", "FieldKeys": "FNumber,FName", "FilterString": "",
         "OrderString": "FNumber", "TopRowCount": 0, "StartRow": 0, "Limit": PAGE_SIZE}
    data = _post(s, conf, QUERY_SVC, [json.dumps(q, ensure_ascii=False)]).json()
    if isinstance(data, dict):
        raise KingdeeError(f"账簿档案取数报错：{json.dumps(data, ensure_ascii=False)[:200]}")
    out = []
    for r in data:
        rr = _unwrap_row(r, 2)
        if isinstance(rr, list) and len(rr) >= 2 and not isinstance(rr[0], dict):
            out.append({"code": _cell(rr[0]), "name": _cell(rr[1])})
    return out


def fetch_warehouses(org="107", s=None, conf=None):
    """仓库档案（BD_STOCK）→ [{code,name,forbid}]，限该核算组织【使用】的仓库。
    V2.119：供「仓库类型」维护页列全量仓库——107 实测 143 个，涵盖收发存报表出现的全部仓库，
    故新仓库可在有业务之前先配好类型，不必等仓库透视报「属性缺失」再补。
    forbid: 'A'=启用 / 'B'=禁用（禁用仓仍列出但默认折叠，历史数据可能仍挂在其下）。"""
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    out, start = [], 0
    while True:
        q = {"FormId": "BD_STOCK", "FieldKeys": "FNumber,FName,FForbidStatus",
             "FilterString": f"FUseOrgId.FNumber='{org}'", "OrderString": "",
             "TopRowCount": 0, "StartRow": start, "Limit": PAGE_SIZE}
        data = _post(s, conf, QUERY_SVC, [json.dumps(q, ensure_ascii=False)]).json()
        if isinstance(data, dict):
            raise KingdeeError(f"仓库档案取数报错：{json.dumps(data, ensure_ascii=False)[:200]}")
        if not data:
            break
        for r in data:
            rr = _unwrap_row(r, 3)
            out.append({"code": _cell(rr[0]), "name": _cell(rr[1]), "forbid": _cell(rr[2])})
        if len(data) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return out


# ⛔ 试过、不通、别再试：金蝶另有一张《存货收发存**明细**表（跨维度）》，
#    其接口**不对外开放**——调用报「此接口暂时只支持简单账表」。
#    但**不需要它**：下面这张汇总表（跨维度）已含全部维度（物料×规格×仓库×批号×库存状态），
#    第⑤步的收发存明细就是从它出的。
#    （原本这段写在前端第①步的使用说明里占着用户的屏，V2.316 挪回代码——
#     它是给开发看的死胡同留痕，不是会计要读的东西。）
def fetch_inventory_summary(year, period, org="107", s=None, conf=None):
    """存货收发存汇总表(跨维度) 取数 + 关联存货类别。
    返回 cost_ledger 内核可用的行 [{code,name,cat,spec,wh,batch,oq,oa,iq,ia,dq,da,eq,ea}]。
    剔小计行判据＝【库存状态为空】；wh 取自 FStockId（V2.116 起真正带仓库维度）。

    V2.115 订正：原判据「无批号=小计行」会误杀"有库存状态、无批号"的真实明细行——
    2026 年 5 期实测误杀 2 行负结存(酱香南昌拌粉 -13.9348 / 爆辣新疆炒米粉 -13.7305)，
    合计 -27.67，即长期挂在库存商品科目上的那笔账实差。改判据后 5 期实测
    2770 行 / 结存 5,181,959.69 / 10,958,051.26，与成本会计底稿分毫不差。"""
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    import calendar
    last = calendar.monthrange(int(year), int(period))[1]
    begin = f"{year}-{int(period):02d}-01"
    end = f"{year}-{int(period):02d}-{last:02d}"
    model = {
        "FACCTGSYSTEMID": {"FNumber": _CL_ACCTSYS}, "FACCTGORGID": {"FNumber": org},
        "FACCTPOLICYID": {"FNumber": _CL_POLICY}, "FBEGINDATE": begin, "FENDDATE": end,
        "FYear": str(year), "FPeriod": str(period), "FENDYEAR": str(year), "FEndPeriod": str(period),
        # ⛔**不要加 `FSTOCKORGID`（库存组织）过滤**（V2.300 血的教训，见 _STOCKORG 注）
        "FSTOCKSTATUSID": [{"FNumber": ""}], "FMATERTYPEID": {"FNUMBER": ""},
        "FDimType": "", "FCHXEXPENSE": "false", "FCHXTotal": "false", "FCHXNOINOUT": "false",
        "FCHXNOCOSTALLOT": "false", "FIsDisplayPeriod": "false", "FCHXNOSTOCKADJ": "false",
        # TotalType=3：每物料小计行收敛成全表 1 行总计（该行库存状态为空，下方判据会剔掉）
        "FCOMBOTotalType": "3", "FCOMBOSTATUS": "", "FPeriodStartDate": begin, "FCURRENCYID": {"FNumber": _CL_CURRENCY},
    }
    # V2.138 起补齐业务方底稿的 17 列：加【物料分组】+【四段单价】。
    # 单价一律【取金蝶的、不自己算 金额÷数量】——实测三条硬理由（107/2026-5，2,770 行）：
    #   ① 数量=0 金额≠0 的「挂账尾差」7 行 → 自己算要除零，而金蝶照样给价（加权平均结转价）；
    #   ② 数量极小（0.0001）的 2 行 → 拿【四舍五入后】的金额去除，误差放大到 3.22 / 2.66 元；
    #   ③「紫糯米」有收入量 42、收入额却为空 → 自己算出 0，等于凭空造一个"单价 0"。
    # 且金蝶单价是加权平均结转价、非简单相除，自己算出来的数成本会计拿去跟金蝶核会对不上。
    # 这 5 个字段挤在【同一次报表请求】里，不多打接口、不增加取数时间。
    # V2.139 加【单位】＝ `FUnitName`——它就是数量所用的那个单位，且实测 **99.7% 等于物料档案的基本单位**
    # （2,770 行比对：2,763 一致 / 7 不一致，如 急停开关 报表"个"vs档案"套"、一水柠檬酸 报表"袋"vs档案"包"）。
    # **不去档案取 FBaseUnitId.FName 贴上来**：那 7 行的数量是按【报表单位】计的，贴档案基本单位＝把
    # "5 个"标成"5 套"，标错比不标更糟。报表单位才是这个数字的真实单位。
    # V2.287 补三列（库存状态/核算范围编码/名称）：业务方底稿的「库存」页是**金蝶原表原样 23 列**，
    # 导出要能出一张同样原样的底稿页。库存状态本来就在取（剔小计行的判据），只是没往外给——
    # 不给出来，看的人无法验证工具剔行剔得对不对。
    # V2.292 修 Q13【物料编码/存货类别错配】——**报表本来就给这两列，此前没要**。
    # 原做法：报表只取名称+规格，再拿 (名称,规格) 去物料档案 `BD_MATERIAL` 反查编码与类别。
    # 同名同规格的物料一撞，整行就挂到别人名下：🧪 107/2026-3 与业务方底稿逐物料比，
    # **109 个物料的结存金额两两对调**（如 300100403 的 63,711.85 被记到 T00000047 名下）。
    # 合计不受影响（所以三道勾稽一直全过、没人发现），但**逐行看会串**——
    # 而"逐行能追"正是台账的用处。
    # 2026-08-13 实测跨维度报表接受 `FMaterialBaseId`(编码) 与 `FMaterType`(存货类别)，
    # 与「按日期」报表一直在用的 FMATERIALBASEID/FMATERTYPE 是同一对字段，只是这张表的
    # 命名风格是驼峰。**改成报表直给，物料档案那次 join 整个删掉**——
    # 不是"换个更好的匹配键"，是**根本不需要匹配**：报表和编码同源，没有撞名的余地。
    fields = ("FMaterialBaseId,FMaterialName,FMaterType,FModel,FStockId,FLotNo,FStockStatusName,"
              "FMaterialGroup,FUnitName,"
              "FInitQty,FInitAmount,FInitPrice,FReceiveQty,FReceiveAmount,FReceivePrice,"
              "FSendQty,FSendAmount,FSendPrice,FEndQty,FEndAmount,FEndPrice,"
              "FACCTGRANGEID,FACCTGRANGENAME")
    rows, start = [], 0
    while True:
        para = {"FieldKeys": fields, "SchemeId": "", "StartRow": start, "Limit": 5000,
                "IsVerifyBaseDataField": "true", "FilterString": [], "Model": model}
        res = _post(s, conf, GETRPT_SVC, [RPT_INOUT_CROSSDIM, json.dumps(para, ensure_ascii=False)]).json()
        r = res.get("Result", {}) if isinstance(res, dict) else {}
        if not r.get("IsSuccess"):
            raise KingdeeError(f"收发存报表取数失败：{json.dumps(res, ensure_ascii=False)[:250]}")
        batch = r.get("Rows") or []      # 无数据时 Rows 是 null 不是 []，见 _ROWS_NULL 注
        rows += batch
        if len(batch) < 5000:
            break
        start += 5000
    # 列序：0编码 1名称 2类别 3规格 4仓库 5批号 6库存状态 7物料分组 8单位
    #       9..20 期初/收入/发出/结存 各(数量,金额,单价)  21核算范围编码 22核算范围名称
    out, nomap = [], 0
    for row in rows:
        code, name, cat, spec = _cell(row[0]), _cell(row[1]), _cell(row[2]), _cell(row[3])
        wh, lot, status = _cell(row[4]), _cell(row[5]), _cell(row[6])   # FStockId 返回的是仓库名称
        grp, unit = _cell(row[7]), _cell(row[8])
        if not status:                   # 库存状态为空=小计/合计行，剔除（明细行必带库存状态，批号则可能为空）
            continue
        if not cat:                      # 报表直给，理论上不会空；空了如实计数、不硬归（口径不变）
            nomap += 1
        out.append({"code": code, "name": name, "cat": cat, "grp": grp, "spec": spec,
                    "unit": unit, "wh": wh, "batch": lot,
                    "oq": _rpt_num(row[9]), "oa": _rpt_num(row[10]), "op": _rpt_price(row[11]),
                    "iq": _rpt_num(row[12]), "ia": _rpt_num(row[13]), "ip": _rpt_price(row[14]),
                    "dq": _rpt_num(row[15]), "da": _rpt_num(row[16]), "dp": _rpt_price(row[17]),
                    "eq": _rpt_num(row[18]), "ea": _rpt_num(row[19]), "ep": _rpt_price(row[20]),
                    "status": status,
                    "rng_code": _cell(row[21]), "rng_name": _cell(row[22])})
    return {"rows": out, "nomap": nomap}


# ================== 报表导出（V2.241）===========
# 三大报表**不是**可查询的报表对象——GetSysReportData 那条路查无此对象（GL_RPT_BalanceSheet 等
# 40+ 个候选命名全报"业务对象不存在"）。正确路径是把「财务报表」当**普通单据**查：
#
#   KDS_Report 单据查询(编码=GB00001 + 年 + 期 + 上报状态=在制)  → 每张报表的 FRptId（GUID 主键）
#        ↓ View(FRptId)
#   KDS_Sheet[] 三个表页（资产负债表/利润表/现金流量表）
#        ↓ RptContent 字段：base64 → zlib 解压
#   FarPoint Spread XML：每格都带 <Data>算好的值</Data> + <Formula>Acct(...)</Formula> + <Tag>报表项目码</Tag>
#
# 2026-08-07 实证：101 主体 2026/6 期解出的三张表，与金蝶 UI 原生导出的 xlsx **逐格全等**
# （资产负债表 188 格 / 利润表 93 格 / 现金流量表 333 格，不一致 0）。
FIN_RPT_NUMBER = "GB00001"          # 「财务报表」这张报表的编码（另有 DX00001 抵消表、HZFB0002 部门报表，不取）
FIN_RPT_SHEETS = ("资产负债表", "利润表", "现金流量表")
_RPT_STATUS_DRAFT = "A"             # 上报状态：A=在制（业务方指定的过滤口径）


def _spread_cells(blob):
    """RptContent(base64+zlib) → {(row, col): 值}。行列均 0 基（Spread 口径），写 Excel 时各 +1。
    只取 <Data>（算好的值）；<Formula> 是金蝶的 Acct() 取数式、Excel 不认，不带出去。"""
    xml = zlib.decompress(base64.b64decode(blob)).decode("utf-8", errors="replace")
    out = {}
    for m in re.finditer(r'<Cell\s+row="(\d+)"\s+column="(\d+)"[^>]*>(.*?)</Cell>', xml, re.S):
        dm = re.search(r'<Data type="System\.(\w+)">(.*?)</Data>', m.group(3), re.S)
        if not dm:
            continue
        t, v = dm.group(1), dm.group(2)
        if t in ("Double", "Decimal", "Single"):
            # 统一收到 4 位小数＝金蝶金额精度。合计格（流动资产合计/营业利润/净利润…）在 Spread XML 里
            # 存的是**完整浮点**（74260573.37810001），照源串位数舍等于把二进制尾巴留下来，
            # 写进 Excel 与金蝶原生导出逐格比对时满屏假差异——数值无差别，但看着像算错了。
            out[(int(m.group(1)), int(m.group(2)))] = round(float(v), 4)
        elif t in ("Int32", "Int64"):
            out[(int(m.group(1)), int(m.group(2)))] = int(v)
        else:
            out[(int(m.group(1)), int(m.group(2)))] = _html_unescape(v)
    return out


def _html_unescape(s):
    for a, b in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'"), ("&amp;", "&")):
        s = s.replace(a, b)
    return s


def fetch_fin_report_list(year, period, s=None, conf=None):
    """某年某期的「财务报表」清单 → [{rid, org, org_name, cur}]，**已按本位币去重**。

    为什么要去重：境外三家（103 Sinkio / 104 Starfield Food / 109 Starfield Plant-Based）在 1–6 期
    各有【人民币】和【美元】两套报表（美元=本位币，人民币=折算版）；7 期起只出本位币那一套。
    业务方定：**只出本位币**。判据不写死主体号——按该主体账簿的本位币认，认不出时退化为
    "该期该主体只有一张就用它"，两张时取与多数期一致的那张（见 _base_currency_of）。"""
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    q = {"FormId": "KDS_Report",
         "FieldKeys": "FRptId,FOrgId.FNumber,FOrgId.FName,FCurrencyID.FName,FPeriod,FYear",
         "FilterString": ("FNumber='%s' and FYear=%d and FPeriod=%d and FReportStatus='%s'"
                          % (FIN_RPT_NUMBER, int(year), int(period), _RPT_STATUS_DRAFT)),
         "OrderString": "", "TopRowCount": 0, "StartRow": 0, "Limit": PAGE_SIZE}
    data = _post(s, conf, QUERY_SVC, [json.dumps(q, ensure_ascii=False)]).json()
    if isinstance(data, dict) or (data and isinstance(data[0], list) and data[0] and isinstance(data[0][0], dict)):
        raise KingdeeError("财务报表清单取数失败：%s" % json.dumps(data, ensure_ascii=False)[:300])
    # 单据查询会因「上报信息」单据体有多行而把同一张报表返回多次 → 按 FRptId 去重
    uniq = {}
    for r in data:
        rr = _unwrap_row(r, 6)
        uniq[_cell(rr[0])] = {"rid": _cell(rr[0]), "org": _cell(rr[1]),
                              "org_name": _cell(rr[2]), "cur": _cell(rr[3])}
    base = _base_currency_of(s, conf)
    by_org = {}
    for it in uniq.values():
        by_org.setdefault(it["org"], []).append(it)
    out = []
    for org, lst in by_org.items():
        if len(lst) == 1:
            out.append(lst[0])
            continue
        want = base.get(org)
        hit = [x for x in lst if x["cur"] == want] if want else []
        out.append(hit[0] if hit else lst[0])
    return sorted(out, key=lambda x: x["org"])


def _base_currency_of(s, conf):
    """账簿 → 记账本位币名。取自账簿档案 BD_AccountBook.FCURRENCYID（**不是** AC_AccountBook，
    那个对象在本账套不存在；字段也不是 FBaseCurrencyID）。查不到回空 dict（调用方自行退化）。
    2026-08-07 实测：101/102/105/107/108=人民币，103/104/109=美元。"""
    try:
        q = {"FormId": "BD_AccountBook", "FieldKeys": "FNumber,FCurrencyID.FName",
             "FilterString": "", "OrderString": "FNumber", "TopRowCount": 0, "StartRow": 0, "Limit": 500}
        d = _post(s, conf, QUERY_SVC, [json.dumps(q, ensure_ascii=False)]).json()
        if isinstance(d, dict) or (d and isinstance(d[0], list) and d[0] and isinstance(d[0][0], dict)):
            return {}
        return {_cell(_unwrap_row(r, 2)[0]): _cell(_unwrap_row(r, 2)[1]) for r in d}
    except Exception:
        return {}


def fetch_fin_report_sheets(rid, s=None, conf=None):
    """View 一张财务报表 → {表页名: {(行,列): 值}}。表页顺序按业务习惯（资产负债表→利润表→现金流量表），
    不按金蝶的 Index（它把现金流量表排在 Index=2、利润表 Index=1，导出来顺序会拧巴）。"""
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    res = _post(s, conf, VIEW_SVC, ["KDS_Report",
                                    json.dumps({"CreateOrgId": 0, "Number": "", "Id": rid})]).json()
    r = (res or {}).get("Result", {})
    st = r.get("ResponseStatus") or {}
    if st and not st.get("IsSuccess"):
        raise KingdeeError("财务报表 View 失败：%s" % json.dumps(st.get("Errors"), ensure_ascii=False)[:250])
    out = {}
    for row in (r.get("Result") or {}).get("KDS_Sheet") or []:
        blob = row.get("RptContent")
        if blob:
            out[row.get("SheetName")] = _spread_cells(blob)
    return out


# 核算维度＝弹性域，每个科目挂的维度类型不同，得把 19 个域全查出来再拼。
# 表项：(域Key, 中文名, 编码字段, 名称字段)。三个字段名都是 2026-08-07 逐域实测锁定的，别照抄别处：
#   · 查询写法是 FDetailID.<域>.<字段>，**不是**元数据目录里那个 FDETAILID__<域> 双下划线名（查询不认）。
#   · 员工域 FFLEX7 的编码字段是 FStaffNumber，不是 FNumber。
#   · 6 个自定义辅助资料域（办公地点/产品项目/品牌项目/营销渠道/市场活动类型/产品分类）没有 FName，
#     名称字段是 FDataValue。
# ⚠ 任一域的字段名写错，整条查询报的是「元数据中标识为 FDetailID 的字段不存在」——报父字段、不报是哪个域，
#   一次查 19 个域时根本反推不出来是谁写错。故此处逐域实测锁定，改动前请照 _probe 方式先单域验证。
FLEX_DIMS = [
    ("FF100002", "银行账号", "FNumber", "FName"),
    ("FF100003", "办公地点", "FNumber", "FDataValue"),
    ("FF100004", "其他往来单位", "FNumber", "FName"),
    ("FF100005", "供应商分组", "FNumber", "FName"),
    ("FF100006", "产品项目（TO C）", "FNumber", "FDataValue"),
    ("FF100007", "品牌项目(TO B)", "FNumber", "FDataValue"),
    ("FF100008", "营销渠道", "FNumber", "FDataValue"),
    ("FF100009", "市场活动类型", "FNumber", "FDataValue"),
    ("FF100010", "产品分类", "FNumber", "FDataValue"),
    ("FFLEX4", "供应商", "FNumber", "FName"),
    ("FFLEX5", "部门", "FNumber", "FName"),
    ("FFLEX6", "客户", "FNumber", "FName"),
    ("FFLEX7", "员工", "FStaffNumber", "FName"),
    ("FFLEX8", "物料", "FNumber", "FName"),
    ("FFLEX9", "费用项目", "FNumber", "FName"),
    ("FFLEX10", "资产类别", "FNumber", "FName"),
    ("FFLEX11", "组织机构", "FNumber", "FName"),
    ("FFLEX12", "物料分组", "FNumber", "FName"),
    ("FFLEX13", "客户分组", "FNumber", "FName"),
]


def _flex_keys(prefix="FDetailID"):
    ks = []
    for f, _, numfld, namefld in FLEX_DIMS:
        ks += ["%s.%s.%s" % (prefix, f, numfld), "%s.%s.%s" % (prefix, f, namefld)]
    return ks


def fetch_account_dim_order(s=None, conf=None):
    """科目编码 → 该科目配置的核算维度顺序 [域Key, …]。

    **为什么非要它不可**：金蝶把核算维度拼成一串时，顺序是**每个科目自己配的维度顺序**，不是什么全局固定顺序。
    按 FLEX_DIMS 的固定顺序拼，1123 会拼成「供应商分组/供应商」，而金蝶是「供应商/供应商分组」——
    值全对、串反了。实测：科目余额 24 行、序时账簿 6949 行因此对不上。
    维度顺序取自科目档案 BD_Account 的 FFlEXITEMPROPERTYID（单据体，按录入序返回）。
    例：1122 应收账款＝客户→员工→费用项目；1123 预付账款＝供应商→供应商分组→品牌项目(TO B)。"""
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    by_label = {label: key for key, label, _, _ in FLEX_DIMS}
    q = {"FormId": "BD_Account", "FieldKeys": "FNumber,FFlEXITEMPROPERTYID.FName",
         "FilterString": "", "OrderString": "FNumber", "TopRowCount": 0, "StartRow": 0, "Limit": PAGE_SIZE}
    out, allc, start = {}, set(), 0
    while True:
        q["StartRow"] = start
        d = _post(s, conf, QUERY_SVC, [json.dumps(q, ensure_ascii=False)]).json()
        if isinstance(d, dict) or (d and isinstance(d[0], list) and d[0] and isinstance(d[0][0], dict)):
            return {}, set()                # 拿不到就回空，调用方退化为 FLEX_DIMS 固定顺序
        for r in d:
            rr = _unwrap_row(r, 2)
            code = _cell(rr[0])
            allc.add(code)
            k = by_label.get(_cell(rr[1]))
            if k:
                out.setdefault(code, []).append(k)
        if len(d) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    # 末级科目集合：科目表里没有以「本编码.」开头的下级。**必须用完整科目表算**——
    # 拿过滤后的余额行去推，会把"下级恰好被剔零剔掉"的科目误判成末级，合计就错（实测差 10,347.15）。
    leaf = {c for c in allc if not any(o != c and o.startswith(c + ".") for o in allc)}
    return out, leaf


def _flex_pairs(vals, order=None):
    """19 个域的扁平取数结果 → [(编码, 名称), …]，只留有值的域。
    order＝该科目的维度顺序（域Key 列表）；给了就按它排，没给就按 FLEX_DIMS 的顺序。"""
    got = []
    for i, (key, _, _, _) in enumerate(FLEX_DIMS):
        c, n = _cell(vals[2 * i]), _cell(vals[2 * i + 1])
        if c or n:
            got.append((key, c, n))
    if order:
        ix = {k: i for i, k in enumerate(order)}
        got.sort(key=lambda x: ix.get(x[0], 99))     # 科目没配到的域排在最后，不丢
    return [(c, n) for _, c, n in got]



def _flex_inline(vals, order=None):
    """序时账簿口径：**单列**，每个维度写成 `编码/名称`，维度之间用 ; 隔开。
    实测自参考文件：`0011301/永续质量中心;FYXM005.001日常服务费用017/员工培训费`。
    ⚠ 这里的 `/` 是"编码与名称之间"，维度之间用 `;`——两种含义容易记反。"""
    return ";".join("/".join(x for x in (c, n) if x) for c, n in _flex_pairs(vals, order))



SUBJECT_BAL_COLS = ["科目编码", "科目名称", "核算维度编码", "核算维度名称",
                    "期初余额借方", "期初余额贷方", "本期发生借方", "本期发生贷方",
                    "本年累计借方", "本年累计贷方", "期末余额借方", "期末余额贷方"]


# ---- 科目余额表：走金蝶自己的报表接口（V2.248 起）----------------------------
# 【为什么换】原来是从 GL_BALANCE 明细自己加总，靠十二条**试**出来的口径（剔零判据、
#   维度顺序、拼音序、翻页排序键…）。业务方担心"自己加总会不会出错"——担心得对：
#   那十二条是对着 101/2026-6 一个样本试出来的，换主体换期间未必成立（过拟合）。
#   2026-08-09 拿到金蝶官方字段说明后改走 GL_RPT_AccountBalance，**数由金蝶自己的引擎出**，
#   十二条口径连同它们的风险一起退役。
#
# 【切换前的回归】8 个主体 × 2026年6期，新旧逐格比对 **27,000 格**：
#   行数、行序、明细行 100% 一致；仅 16 格不同，**全部在合计行**——
#   而那正是新版更对的地方（旧版是我们自己加总的，新版是金蝶印在报表上的那个数，
#   即确认书 Q1 里"推不出来"的 303,852,643.8146）。
#
# 【两个致命细节，改这段的人务必看】
#   ① FACCTBOOKID 要 {"FNumber": "101"} 对象，而 FCURRENCY 要**数字内码**（人民币=1、美元=7）。
#      给 FCURRENCY 传 "PRE001" 报「输入字符串的格式不正确」、传对象报「不能转换」，
#      **而留空不报错、只是整张表取回来全是 null**——最容易被误判成"列名写错了"。
#      内码**按账簿取**、别按币别名称查：本账套有两个都叫「人民币」的币别（见 _book_currency_id）。
#   ② 那四个「包括…」开关的组合决定行数，差一个就对不上金蝶导出：
#      全关 95 行 ／ 只开余额为零 447 ／ 开三个(零+本期无+本年无)+级别6 = **1111 ✓** ／ 四个全开 4764。
SBAL_RPT = "GL_RPT_AccountBalance"
SBAL_RPT_COLS = ["FBALANCEID", "FBALANCENAME", "FDETAILNUMBER", "FDETAILNAME",
                 "FBEGINDEBIT", "FBEGINCREDIT", "FDEBIT", "FCREDIT",
                 "FYTDDEBIT", "FYTDCREDIT", "FENDDEBIT", "FENDCREDIT"]
_BOOK_CY = {}


def _book_currency_id(book, s=None, conf=None):
    """账簿 → 它的本位币【内部数字 ID】。报表接口的 FCURRENCY 只吃数字。

    ⚠ **不能拿币别名称去换 ID**：本账套里有**两个都叫「人民币」的币别**（内码 1 和 478156）。
      按名字查会拿到后建的那个 478156，报表**不报错、只返回 0 行**——
      切换时实际踩到：美元主体正常、人民币主体全空，症状离病因很远。
      改成直接问账簿要 `BD_AccountBook.FCURRENCYID`，一个账簿只有一个本位币，不存在歧义。"""
    global _BOOK_CY
    if not _BOOK_CY:
        q = {"FormId": "BD_AccountBook", "FieldKeys": "FNumber,FCURRENCYID", "FilterString": "",
             "OrderString": "FNumber", "TopRowCount": 0, "StartRow": 0, "Limit": 500}
        try:
            d = _post(s, conf, QUERY_SVC, [json.dumps(q, ensure_ascii=False)]).json()
            if isinstance(d, list):
                for r in d:
                    rr = _unwrap_row(r, 2)
                    _BOOK_CY[_cell(rr[0])] = int(rr[1])
        except Exception:
            _BOOK_CY = {}
    cy = _BOOK_CY.get(str(book))
    if not cy:
        raise KingdeeError("取不到账簿 %s 的本位币内码（BD_AccountBook.FCURRENCYID）" % book)
    return cy


def fetch_subject_balance_full(year, period, book, cur=None, s=None, conf=None):
    """整本科目余额表 → 与金蝶导出同款 12 列（科目编码/名称、维度编码/名称、8 个金额列），
    末行是金蝶自己算的合计。**只取本位币那一档**（cur 不传则按账簿本位币）。

    返回结构与 V2.241 的旧实现完全相同，调用方（report_export）无需改动。"""
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    if not cur:
        cur = _base_currency_of(s, conf).get(book) or "人民币"
    model = {
        "FACCTBOOKID": {"FNumber": str(book)},          # 账簿：对象写法
        "FCURRENCY": _book_currency_id(book, s, conf),  # 币别：数字内码，按【账簿】取（见上方细节①）
        "FSTARTYEAR": str(int(year)), "FSTARTPERIOD": str(int(period)),
        "FENDYEAR": str(int(year)), "FENDPERIOD": str(int(period)),
        "FBALANCELEVEL": "6",                           # 科目级别取到末级
        "FSTARTBALANCE": {"FNumber": ""}, "FENDBALANCE": {"FNumber": ""},   # 科目【编码】区间，不是金额
        "FSHOWDETAIL": "true",                          # 显示核算维度明细
        "FBALANCEZERO": "true",                         # ┐ 这三个开、FNOBUSINESS 关，
        "FPERIODNOBALANCE": "true",                     # ├ 才与金蝶导出行数一致（见上方细节②）
        "FYEARNOBALANCE": "true",                       # ┘
        "FNOBUSINESS": "false",
        "FFORBIDBALANCE": "false", "FNOTPOSTVOUCHER": "false", "FDEBITORCREDIT": "false",
        "FSHOWFULLNAME": "false", "FDETAILSHOWACCT": "false", "FSHOWDETAILONLY": "false",
        "FEXCLUDEADJUSTVCH": "false", "FFLEXDEBITORCREDIT": "false", "FSHOWFLEXBYCOL": "false",
    }
    para = {"FieldKeys": ",".join(SBAL_RPT_COLS), "SchemeId": "", "StartRow": 0, "Limit": 10000,
            "IsVerifyBaseDataField": "true", "FilterString": [], "Model": model}
    res = _post(s, conf, GETRPT_SVC, [SBAL_RPT, json.dumps(para, ensure_ascii=False)]).json()
    r = res.get("Result", {}) if isinstance(res, dict) else {}
    if r.get("RowCount") is None:
        raise KingdeeError("科目余额表取数失败：%s" % json.dumps(res, ensure_ascii=False)[:300])

    rows, code, name = [], "", ""
    for raw in (r.get("Rows") or []):
        c, n = _cell(raw[0]), _cell(raw[1])
        amt = [_rpt_num(x) or None for x in raw[4:12]]
        if n == "合计":                     # 金蝶自己算的合计，原样搬（不再由我们加总）
            rows.append([None, "合计", None, None] + amt)
            continue
        if c:                               # 科目行：记下编码/名称，供其下的维度行沿用
            code, name = c, n
        # 金蝶只在科目行给编码/名称，维度行留空；而导出格式是**每行都带**（与参考文件一致），故向下填充
        rows.append([code, name, _cell(raw[2]) or "", _cell(raw[3]) or ""] + amt)
    return rows


JOURNAL_COLS = ["账簿", "日期", "期间", "凭证字", "凭证号", "摘要", "科目编码", "科目名称",
                "核算维度", "借方金额", "贷方金额", "制单", "审核", "来源系统"]


def _kd_date(v):
    """'2026-06-02T00:00:00' → '2026/6/2'（金蝶导出的序时簿日期就是这个**文本**格式，非日期值）。"""
    d = str(v or "")[:10].split("-")
    return "%s/%d/%d" % (d[0], int(d[1]), int(d[2])) if len(d) == 3 and d[0] else str(v or "")


def fetch_journal_full(year, period, book, s=None, conf=None):
    """整本序时账簿（全科目）→ JOURNAL_COLS 那 14 列。单主体单月实测约 2.4 万行，故按 PAGE_SIZE 翻页。"""
    s, conf = login(s, conf) if s is None else (s, conf or load_conf())
    head = ["FAccountBookID.FName", "FDate", "FPERIOD", "FVOUCHERGROUPID.FName", "FVOUCHERGROUPNO",
            "FEXPLANATION", "FACCOUNTID.FNumber", "FACCOUNTID.FName"]
    # 来源系统取 .FName 直接拿中文（"总账"/"出纳管理"…）；裸 FSystemID 回的是代码、FNumber 回小写码，
    # 都还得自己维护一张映射表，且漏一个就出错——让金蝶给中文最省事。
    tail = ["FDEBIT", "FCREDIT", "FCreatorId.FName", "FCHECKERID.FName", "FSystemID.FName"]
    fields = head + _flex_keys() + tail
    # 排序按【凭证号 + 分录行号】。
    #   · 凭证号：金蝶序时簿就是这个口径，**不是按日期**——凭证号是录入顺序、与日期不同调
    #     （实测参考文件首行是 6/2 的记1，而 6/1 的记33 排在后面；按日期排会整表错位）。
    #   · 分录行号（`FEntity_FEntrySeq`）：**必须补上，因为凭证号不唯一**。
    #     2.4 万行要翻页，而非唯一排序键翻页会**静默丢行**——科目余额表上实测踩过
    #     （只按科目号排，6601 少 101 行、不报错）。这里今天没丢，但那是**运气不是保证**：
    #     服务端的并列次序未定义，数据量或执行计划一变就可能变。
    #     实测加这个键**不改变任何输出**（101 的 626 张凭证 + 107 的 749 张，顺序差异 0），
    #     所以这是一次零风险的加固：把"碰巧对"变成"结构上必然对"。
    # 分录行号怎么取到的：ExecuteBillQuery 的「单据体Key_字段」写法（`FEntity_FEntrySeq`）。
    # 另有 `FEntity_FEntryId`＝分录内码（创建序），实测两者次序一致，取行号更贴近金蝶的显示口径。
    q = {"FormId": "GL_VOUCHER", "FieldKeys": ",".join(fields),
         "FilterString": "FYear=%d and FPERIOD=%d and FAccountBookID.FNumber='%s'" % (int(year), int(period), book),
         "OrderString": "FVOUCHERGROUPNO,FEntity_FEntrySeq", "TopRowCount": 0, "StartRow": 0, "Limit": PAGE_SIZE}
    dim_order = fetch_account_dim_order(s, conf)[0]
    rows, start = [], 0
    n = len(fields)
    nf = 2 * len(FLEX_DIMS)
    while True:
        q["StartRow"] = start
        d = _post(s, conf, QUERY_SVC, [json.dumps(q, ensure_ascii=False)]).json()
        if isinstance(d, dict) or (d and isinstance(d[0], list) and d[0] and isinstance(d[0][0], dict)):
            raise KingdeeError("序时账簿取数失败：%s" % json.dumps(d, ensure_ascii=False)[:300])
        for r in d:
            rr = _unwrap_row(r, n)
            h = [_cell(x) for x in rr[:8]]
            t = rr[8 + nf:]
            rows.append([h[0], _kd_date(h[1]), h[2], h[3], h[4], h[5], h[6], h[7],
                         _flex_inline(rr[8:8 + nf], dim_order.get(h[6])),
                         _rpt_num(t[0]) or None, _rpt_num(t[1]) or None,
                         _cell(t[2]), _cell(t[3]), _cell(t[4])])
        if len(d) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return rows
# ---------------- 电商对账·收款核销取数（V2.250，只读，纯新增不动既有函数） ----------------
# 电商应收整段拉取：结算组织+销售部门是唯一电商筛选器（确认书① §4.1，2026-08-11 实证——
# 不加部门会混入 B2B，312 万 vs 9.6 万差 32 倍）。整段拉取+本地索引替代逐单千次 in 查询
# （确认书⑤ D5：7 月 4 万单逐批查实测超时，整段 16,443 行一次到位）。
# 红字应收单三个旺店通字段整体左移一位（蓝字 Text6=平台原始单号；红字 Text4=平台原始单号、
# Text6 空，源单=RK销售退货单）——故三个 Text 一起取，配对逻辑在 kernels/ec_settle.py。
EC_AR_FIELDS = [
    ("FBillNo", "单据编号"), ("FDATE", "日期"), ("FCUSTOMERID.FName", "客户"),
    ("FMATERIALID.FNumber", "物料编码"), ("FMATERIALID.FName", "物料名称"),
    ("FPriceQty", "数量"), ("FALLAMOUNTFOR_D", "价税合计"),
    ("F_ora_Text3", "Text3"), ("F_ora_Text4", "Text4"), ("F_ora_Text6", "Text6"),
]


def fetch_ec_receivables(date_from, date_to, org="深圳市星期零食品科技有限公司",
                         dept="永续媒介中心", s=None, conf=None):
    """电商应收单整段拉取（只读）。date_to = 结算期末（期间闸：下期红字属下期，实证 AR00301771）。"""
    if s is None or conf is None:
        s, conf = login()
    org = str(org).replace("'", "")
    dept = str(dept).replace("'", "")
    filt = (f"FDATE>='{date_from}' and FDATE<='{date_to}'"
            f" and FSETTLEORGID.FName='{org}' and FSALEDEPTID.FName='{dept}'")
    return _query(s, conf, "AR_receivable", EC_AR_FIELDS, filt)


def test_connection():
    """仅登录，验证 conf.ini 与网络。返回 (ok, msg)。"""
    try:
        login()
        return True, f"连接成功（conf: {conf_path()}）"
    except Exception as e:
        return False, str(e)
