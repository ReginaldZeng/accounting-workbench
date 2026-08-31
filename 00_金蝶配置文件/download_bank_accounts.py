# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-07-03 | Author: Claude / c | Version: V1.16
# Description: 金蝶 出纳管理-银行账号(CN_BANKACNT) -> Excel。全量银行账号(含未动账/已销户),
#              带开户行+启用/禁用状态,作为《账户台账》权威来源。
#              **按组织遍历取数**：组织隔离的基础资料经WebAPI默认只回登录用户当前组织(主职组织)的数据,
#              故先查核算组织清单, 再对每个组织带 FUseOrgId 过滤各查一次, 按账户去重合并。
#              自愈式：先QueryBusinessInfo拉字段清单+按中文名自动认列;某字段名不符自动丢列重试。
#              复用同目录 conf.ini。只读。
import os
import sys
import json
import re
import configparser

import requests
from openpyxl import Workbook

FORM_ID = "CN_BANKACNT"
import datetime as _dt
_TS = _dt.datetime.now().strftime("%H%M%S")
OUTPUT_PATH = r"D:\银行金蝶稽核\金蝶银行账号台账_%s.xlsx" % _TS
FIELDS_TXT = r"D:\银行金蝶稽核\cn_bankacnt_字段清单.txt"

# 组织隔离过滤字段(银行账号的使用组织)。若账套不同可改。
ORG_FILTER_FIELD = "FUseOrgId"
# 核算组织清单来源(依次尝试)。拿不到时用 MANUAL_ORGS。
ORG_FORMS = ["ORG_Organizations", "BOS_Organizations", "BD_Empinfo"]
# 兜底: 手工指定组织(编码优先, 没有就写名称)。自动查到组织时此项忽略。
MANUAL_ORG_NUMBERS = []    # 如 ["100","101",...]
MANUAL_ORG_NAMES = [       # 已知的8~9个主体(自动查不到组织清单时用名称过滤)
    "深圳市星期零食品科技有限公司", "孝感市星期九食品科技有限公司",
    "深圳市星期八食品科技有限公司", "深圳市星期九食品科技有限公司",
    "深圳市星期十食品科技有限公司", "Sinkio Limited",
    "Starfield Food and Science", "Starfield Plant-Based,Inc",
]

LOGICAL_COLS = [
    ("银行账号",  ["银行账号", "账号"],            False, ["FBANKACCOUNTNUMBER", "FACCTNUMBER", "FNumber"]),
    ("账户名称",  ["账户名称", "户名", "名称"],      False, ["FACCOUNTNAME", "FBankAccountName", "FName"]),
    ("开户行",    ["开户行", "开户银行", "开户网点"], False, ["FOPENBANKNAME", "FTEXTBANKDETAIL"]),
    ("银行类别",  ["银行类别", "所属银行", "银行"],   True,  ["FBANKID", "FBANKTYPEREFID", "FBANKGROUPID"]),
    ("所属组织",  ["所属组织", "使用组织", "核算组织", "所属公司", "公司"], True, ["FUSEORGID", "FUseOrgId", "FCreateOrgId"]),
    ("币别",      ["币别", "币种"],                True,  ["FCURRENCYID"]),
    ("禁用状态",  ["禁用", "使用状态", "单据状态"],   False, ["FFORBIDSTATUS", "FDOCUMENTSTATUS"]),
]

PAGE_SIZE = 2000
CONF_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conf.ini")
CONF_NODE = "config"
LOGIN_SVC = "Kingdee.BOS.WebApi.ServicesStub.AuthService.LoginByAppSecret.common.kdsvc"
QUERY_SVC = "Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.ExecuteBillQuery.common.kdsvc"
META_SVC  = "Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.QueryBusinessInfo.common.kdsvc"


def load_conf():
    if not os.path.exists(CONF_PATH):
        sys.exit(f"找不到配置文件：{CONF_PATH}")
    cfg = configparser.ConfigParser()
    cfg.read(CONF_PATH, encoding="utf-8")
    c = cfg[CONF_NODE]
    conf = {"acct_id": c.get("X-KDApi-AcctID", "").strip(), "username": c.get("X-KDApi-UserName", "").strip(),
            "app_id": c.get("X-KDApi-AppID", "").strip(), "app_secret": c.get("X-KDApi-AppSec", "").strip(),
            "server_url": c.get("X-KDApi-ServerUrl", "").strip().rstrip("/"),
            "lcid": int(c.get("X-KDApi-LCID", "2052") or "2052")}
    miss = [k for k in ("acct_id", "username", "app_id", "app_secret", "server_url") if not conf[k]]
    if miss:
        sys.exit(f"conf.ini 缺项：{', '.join(miss)}")
    return conf


def post(s, conf, svc, params):
    url = f"{conf['server_url']}/{svc}"
    r = s.post(url, data=json.dumps({"parameters": params}, ensure_ascii=False).encode("utf-8"),
               headers={"Content-Type": "application/json;charset=utf-8"}, timeout=120)
    r.raise_for_status()
    return r


def login(s, conf):
    res = post(s, conf, LOGIN_SVC, [conf["acct_id"], conf["username"], conf["app_id"],
                                    conf["app_secret"], conf["lcid"]]).json()
    if not (isinstance(res, dict) and res.get("LoginResultType") == 1):
        sys.exit(f"登录失败：{res}")
    print(f"  登录成功（用户：{conf['username']}）")


def walk_fields(obj, found):
    if isinstance(obj, dict):
        key = obj.get("Key") or obj.get("key") or obj.get("FieldName")
        name = obj.get("Name") or obj.get("name") or obj.get("FieldCaption")
        if isinstance(name, dict):
            name = name.get("zh-CN") or name.get("zh_CN") or next(iter(name.values()), "")
        if key and isinstance(key, str):
            found.setdefault(key, name or "")
        for v in obj.values():
            walk_fields(v, found)
    elif isinstance(obj, list):
        for v in obj:
            walk_fields(v, found)


def get_field_catalog(s, conf):
    try:
        data = post(s, conf, META_SVC, [{"FormId": FORM_ID}]).json()
    except Exception as e:
        print(f"  [提示] 拉字段元数据失败({e})，用候选字段名。")
        return {}
    found = {}
    walk_fields(data, found)
    if found:
        try:
            os.makedirs(os.path.dirname(FIELDS_TXT), exist_ok=True)
            with open(FIELDS_TXT, "w", encoding="utf-8") as f:
                f.write("字段标识\t字段名\n")
                for k in sorted(found):
                    f.write(f"{k}\t{found[k]}\n")
            print(f"  字段清单已存: {FIELDS_TXT} (共 {len(found)} 个)")
        except Exception:
            pass
    return found


def resolve_fields(catalog):
    chosen, used = [], set()
    for header, kws, need_fname, cands in LOGICAL_COLS:
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


def query_page(s, conf, form_id, keys, filt, start):
    q = {"FormId": form_id, "FieldKeys": keys, "FilterString": filt,
         "OrderString": "", "TopRowCount": 0, "StartRow": start, "Limit": PAGE_SIZE}
    data = post(s, conf, QUERY_SVC, [json.dumps(q, ensure_ascii=False)]).json()
    if isinstance(data, dict):
        return None, json.dumps(data, ensure_ascii=False)[:600]
    if data and isinstance(data[0], list) and data[0] and isinstance(data[0][0], dict):
        try:
            msg = "；".join(e.get("Message", "") for e in data[0][0]["Result"]["ResponseStatus"]["Errors"])
        except Exception:
            msg = json.dumps(data[0][0], ensure_ascii=False)[:300]
        return None, msg
    return data, None


def fetch_resilient(s, conf, fields, filt=""):
    """带过滤取数; 遇字段不存在自动丢列重试; 分页。返回(rows, used_fields)。"""
    cur = list(fields)
    for _ in range(len(fields) + 2):
        keys = ",".join(k for k, _ in cur)
        rows, err = query_page(s, conf, FORM_ID, keys, filt, 0)
        if err is None:
            all_rows = list(rows)
            start = len(rows)
            while len(rows) == PAGE_SIZE:
                rows, err2 = query_page(s, conf, FORM_ID, keys, filt, start)
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
                    print(f"    [自愈] 字段 {k} 不符，跳过。")
                    dropped = True
                    break
        if not dropped:
            print(f"    [警告] 查询报错(无法自愈): {err[:150]}")
            return [], cur
    return [], cur


def get_orgs(s, conf):
    """查核算组织清单 [(FNumber, FName)]; 依次尝试候选FormId, 都失败用手工兜底。"""
    for form in ORG_FORMS:
        try:
            data, err = query_page(s, conf, form, "FNumber,FName", "", 0)
            if err is None and data:
                orgs = []
                for r in data:
                    rr = r[0] if (isinstance(r, list) and len(r) == 1 and isinstance(r[0], list)) else r
                    if isinstance(rr, list) and len(rr) >= 2:
                        orgs.append((str(rr[0]), str(rr[1])))
                if orgs:
                    print(f"  组织清单来自 {form}: {len(orgs)} 个")
                    return orgs
        except Exception:
            continue
    if MANUAL_ORG_NUMBERS:
        print(f"  用手工组织编码 {len(MANUAL_ORG_NUMBERS)} 个")
        return [(n, "") for n in MANUAL_ORG_NUMBERS]
    print(f"  用手工组织名称 {len(MANUAL_ORG_NAMES)} 个")
    return [("", n) for n in MANUAL_ORG_NAMES]


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return ",".join(_cell(x) for x in v)
    if isinstance(v, dict):
        return v.get("FName") or v.get("Name") or json.dumps(v, ensure_ascii=False)
    return v


def _unwrap_row(r, ncol):
    while isinstance(r, (list, tuple)) and len(r) == 1 and isinstance(r[0], (list, tuple)) and len(r[0]) >= ncol:
        r = r[0]
    return r


def write_excel(rows, fields):
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "银行账号台账"
    ws.append([h for _, h in fields])
    ncol = len(fields)
    for r in rows:
        r = _unwrap_row(r, ncol)
        row = ([_cell(c) for c in r] + [""] * ncol)[:ncol]
        ws.append(row)
    try:
        wb.save(OUTPUT_PATH)
    except PermissionError:
        sys.exit(f"保存失败：文件可能正被 Excel 打开：{OUTPUT_PATH}")
    return OUTPUT_PATH


def main():
    conf = load_conf()
    s = requests.Session()
    print("登录金蝶云星空 ...")
    login(s, conf)
    print(f"读取 {FORM_ID} 字段清单 ...")
    catalog = get_field_catalog(s, conf)
    fields = resolve_fields(catalog)
    print("  取数字段：" + " | ".join(f"{h}<{k}>" for k, h in fields))

    print("查核算组织清单 ...")
    orgs = get_orgs(s, conf)

    # 逐组织取数 + 按账户去重合并
    merged, used = [], fields
    seen = set()
    acct_idx = 0  # 银行账号列位置(第一列)
    for num, name in orgs:
        if num:
            filt = f"{ORG_FILTER_FIELD}.FNumber='{num}'"
        else:
            filt = f"{ORG_FILTER_FIELD}.FName='{name}'"
        rows, used = fetch_resilient(s, conf, fields, filt)
        added = 0
        for r in rows:
            rr = _unwrap_row(r, len(used))
            key = _cell(rr[acct_idx]) if isinstance(rr, (list, tuple)) and rr else _cell(rr)
            if key and key not in seen:
                seen.add(key)
                merged.append(rr)
                added += 1
        print(f"  组织[{name or num}] 取到 {len(rows)} 行, 新增 {added}")

    # 若逐组织一个都没取到, 退回不带过滤查一次(至少拿当前组织)
    if not merged:
        print("逐组织未取到, 退回无过滤查询(当前登录组织)...")
        rows, used = fetch_resilient(s, conf, fields, "")
        for r in rows:
            rr = _unwrap_row(r, len(used))
            key = _cell(rr[acct_idx]) if isinstance(rr, (list, tuple)) and rr else _cell(rr)
            if key and key not in seen:
                seen.add(key)
                merged.append(rr)

    if not merged:
        print("没有数据。检查权限/组织/FormId。")
        return
    path = write_excel(merged, used)
    print(f"完成！合并去重后共 {len(merged)} 个银行账号，已保存到：\n  {path}")
    print("把这个 xlsx 发回, 即可据此建《账户台账》权威版(全量账户+开户行+启用状态)。")


class _Tee:
    def __init__(self, *streams):
        self.streams = streams
    def write(self, d):
        for st in self.streams:
            try:
                st.write(d); st.flush()
            except Exception:
                pass
    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:
                pass


if __name__ == "__main__":
    import traceback
    _logdir = r"D:\银行金蝶稽核"
    try:
        os.makedirs(_logdir, exist_ok=True)
    except Exception:
        _logdir = os.path.dirname(os.path.abspath(__file__))
    _logpath = os.path.join(_logdir, "取数日志.txt")
    try:
        _lf = open(_logpath, "w", encoding="utf-8")
        sys.stdout = _Tee(sys.__stdout__, _lf)
        sys.stderr = _Tee(sys.__stderr__, _lf)
    except Exception:
        _lf = None
    try:
        main()
    except SystemExit as e:
        if e.code not in (0, None):
            print(f"[脚本主动退出] {e}")
    except Exception:
        print("[发生异常]\n" + traceback.format_exc())
    finally:
        print("\n============================================")
        print(f"运行日志已保存到:\n  {_logpath}")
        print("请把这个【取数日志.txt】发我, 我据此定位。")
        print("============================================")
        if _lf:
            try:
                _lf.flush(); _lf.close()
            except Exception:
                pass
    try:
        input("按回车键关闭窗口...")
    except Exception:
        pass
