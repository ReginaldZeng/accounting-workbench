# -*- coding: utf-8 -*-
"""
金蝶云星空 银行存款(1002)序时账 -> Excel 下载脚本

照搬同目录 download_po.py 的成熟写法（LoginByAppSecret 登录 + ExecuteBillQuery 分页），
只把目标单据换成【总账凭证 GL_VOUCHER】，并按 科目=1002* + 期间 过滤，
取回银行存款的逐笔分录（序时账），供"银行—金蝶稽核"三栏对账 / 逐笔勾稽使用。

授权信息复用同目录 conf.ini（与采购订单、应收单脚本共用）。

用法：
    1) 已装依赖（requests、openpyxl）
    2) 改下面【可配置区】的 DATE_FROM / DATE_TO（会计期间）
    3) python download_gl_bank.py
    4) 首次若报某字段"未找到"，按你账套的 GL_VOUCHER 字段标识调整 FIELDS 即可
"""

import os
import sys
import json
import configparser

import requests
from openpyxl import Workbook


# ============================ 可配置区（按需修改） ============================

# 总账凭证单据标识，固定
FORM_ID = "GL_VOUCHER"

# —— 会计期间（按需改）——
DATE_FROM = "2026-06-01"
DATE_TO   = "2026-06-30"

# 银行存款科目编码前缀（你们若不是 1002，改这里）
ACCT_PREFIXES = ["1001", "1002", "1012", "1101"]   # 资金看板四类科目

# 过滤条件（金蝶 FilterString 语法）
#   默认：科目以 1002 开头 且 凭证日期在期间内。
#   可选叠加（视账套需要，取消注释并按实际字段调整）：
#     - 只取已过账：      " and FPOSTED='1'"
#     - 指定核算组织/账簿： " and FACCOUNTBOOKID.FNumber='xxx'"   ← TODO 按账套确认字段
_pref = " or ".join(f"FACCOUNTID.FNumber like '{p}%'" for p in ACCT_PREFIXES)
FILTER_STRING = (
    f"({_pref}) "
    f"and FDATE>='{DATE_FROM}' and FDATE<='{DATE_TO}'"
)

# 排序
ORDER_STRING = "FDATE, FBILLNO"

# 要导出的列：(金蝶字段标识, 中文表头)。含明细字段时按分录行展开（每条分录一行）。
# 注意：GL_VOUCHER 的分录字段标识各账套/版本可能略有差异，
#       首跑若报"字段未找到"，用金蝶【表单设计器】或元数据接口核对后替换即可（TODO）。
FIELDS = [
    # —— 单据头（凭证级）——
    ("FAccountBookID.FName",  "账簿"),
    ("FDate",                 "日期"),
    ("FYEAR",                 "会计年度"),
    ("FPERIOD",               "期间"),
    ("FVOUCHERGROUPID.FName", "凭证字"),      # 凭证字(记/银/现…)
    ("FVOUCHERGROUPNO",       "凭证号"),       # 凭证号(清单确认=FVOUCHERGROUPNO)
    ("FCreatorId.FName",      "制单"),
    ("FCHECKERID.FName",      "审核"),
    ("FPOSTERID.FName",       "过账"),
    ("FCASHIERID.FName",      "出纳"),
    # —— 分录（科目级）——
    ("FACCOUNTID.FNumber",    "科目编码"),
    ("FACCOUNTID.FName",      "科目名称"),
    ("FACCOUNTID.FFullName",  "科目全名"),
    ("FCURRENCYID.FName",     "币别"),
    ("FEXCHANGERATE",         "汇率"),
    ("FAMOUNTFOR",            "原币金额"),
    ("FDEBIT",                "借方金额"),      # 本位币借方
    ("FCREDIT",               "贷方金额"),      # 本位币贷方
    ("FEXPLANATION",          "摘要"),
    # —— 核算维度：银行账号（探针确认：正确写法 = FDetailID.FF100002.*）——
    ("FDetailID.FF100002.FNumber", "核算维度.银行账号.编码"),   # 值形如 宁波行一般户73110122000157061
    ("FDetailID.FF100002.FName",   "核算维度.银行账号.名称"),
]

# 每页条数（金蝶单次上限一般 2000）。首次试跑可临时改小，例如 10
PAGE_SIZE = 2000

# 输出文件
OUTPUT_PATH = r"D:\银行金蝶稽核\金蝶银行存款序时账.xlsx"

# conf.ini 路径与配置节名（与采购订单脚本共用）
CONF_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conf.ini")
CONF_NODE = "config"

# WebAPI 服务路径（一般不用改）
LOGIN_SVC = "Kingdee.BOS.WebApi.ServicesStub.AuthService.LoginByAppSecret.common.kdsvc"
QUERY_SVC = "Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.ExecuteBillQuery.common.kdsvc"

# ==========================================================================


def load_conf():
    if not os.path.exists(CONF_PATH):
        sys.exit(f"找不到配置文件：{CONF_PATH}")
    cfg = configparser.ConfigParser()
    cfg.read(CONF_PATH, encoding="utf-8")
    c = cfg[CONF_NODE]
    conf = {
        "acct_id":    c.get("X-KDApi-AcctID", "").strip(),
        "username":   c.get("X-KDApi-UserName", "").strip(),
        "app_id":     c.get("X-KDApi-AppID", "").strip(),
        "app_secret": c.get("X-KDApi-AppSec", "").strip(),
        "server_url": c.get("X-KDApi-ServerUrl", "").strip().rstrip("/"),
        "lcid":       int(c.get("X-KDApi-LCID", "2052") or "2052"),
    }
    missing = [k for k in ("acct_id", "username", "app_id", "app_secret", "server_url") if not conf[k]]
    if missing:
        sys.exit(f"conf.ini 还有没填的项：{', '.join(missing)}")
    return conf


def post(session, conf, svc, parameters):
    url = f"{conf['server_url']}/{svc}"
    body = json.dumps({"parameters": parameters}, ensure_ascii=False)
    resp = session.post(
        url,
        data=body.encode("utf-8"),
        headers={"Content-Type": "application/json;charset=utf-8"},
        timeout=120,
    )
    resp.raise_for_status()
    return resp


def login(session, conf):
    params = [conf["acct_id"], conf["username"], conf["app_id"], conf["app_secret"], conf["lcid"]]
    resp = post(session, conf, LOGIN_SVC, params)
    try:
        result = resp.json()
    except ValueError:
        sys.exit(f"登录返回非 JSON，可能是地址错误：\n{resp.text[:500]}")
    if isinstance(result, dict) and result.get("LoginResultType") == 1:
        print(f"  登录成功（用户：{conf['username']}）")
        return
    sys.exit(f"登录失败：{result}")


def fetch_all(session, conf):
    field_keys = ",".join(f for f, _ in FIELDS)
    all_rows = []
    start_row = 0
    while True:
        query = {
            "FormId": FORM_ID,
            "FieldKeys": field_keys,
            "FilterString": FILTER_STRING,
            "OrderString": ORDER_STRING,
            "TopRowCount": 0,
            "StartRow": start_row,
            "Limit": PAGE_SIZE,
        }
        resp = post(session, conf, QUERY_SVC, [json.dumps(query, ensure_ascii=False)])
        try:
            data = resp.json()
        except ValueError:
            sys.exit(f"查询返回非 JSON：\n{resp.text[:500]}")
        if isinstance(data, dict):
            sys.exit(f"查询接口报错：{json.dumps(data, ensure_ascii=False)[:800]}")
        if not isinstance(data, list):
            sys.exit(f"查询返回格式异常：{data!r}")
        if data:
            first = data[0]
            cell = first[0] if isinstance(first, list) and first else first
            if isinstance(cell, dict):
                try:
                    errs = cell["Result"]["ResponseStatus"]["Errors"]
                    msg = "；".join(e.get("Message", "") for e in errs)
                except Exception:
                    msg = json.dumps(cell, ensure_ascii=False)
                sys.exit(f"查询接口报错：{msg}")
        all_rows.extend(data)
        print(f"  已获取 {len(all_rows)} 行 (本页 {len(data)})")
        if len(data) < PAGE_SIZE:
            break
        start_row += PAGE_SIZE
    return all_rows


def write_excel(rows):
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "银行存款序时账"
    ws.append([cn for _, cn in FIELDS])
    for row in rows:
        ws.append(list(row))
    try:
        wb.save(OUTPUT_PATH)
    except PermissionError:
        sys.exit(f"保存失败：文件可能正被 Excel 打开，请关闭后重试：\n  {OUTPUT_PATH}")
    return OUTPUT_PATH


def main():
    conf = load_conf()
    session = requests.Session()

    print("登录金蝶云星空 ...")
    login(session, conf)

    print(f"查询银行存款序时账（{FORM_ID}，科目 {'/'.join(ACCT_PREFIXES)}，{DATE_FROM}~{DATE_TO}）...")
    print(f"  过滤条件: {FILTER_STRING}")
    rows = fetch_all(session, conf)

    if not rows:
        print("没有符合条件的数据。请检查 科目前缀 / 期间 / 权限。")
        return

    path = write_excel(rows)
    print(f"完成！共 {len(rows)} 行，已保存到：\n  {path}")


if __name__ == "__main__":
    main()
