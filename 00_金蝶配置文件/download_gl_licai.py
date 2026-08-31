# -*- coding: utf-8 -*-
"""
金蝶云星空 理财序时账 -> Excel 下载脚本
    科目 = 交易性金融资产(1101) + 其它货币资金(1012)

用途：核对"理财申购/赎回"在金蝶是否做账、金额是否正确。
理财赎回一般不记银行存款(1002)，而是记 1101(交易性金融资产) 或 1012(其它货币资金)，
所以单独把这两个科目的六月逐笔分录导出来，配合《理财对账单/交易凭证》解析结果逐笔勾稽。

完全照搬同目录 download_gl_bank.py 的成熟写法(LoginByAppSecret 登录 + ExecuteBillQuery 分页)，
仅改：科目前缀、输出文件名、页签标题。授权信息复用同目录 conf.ini。

用法：
    1) 已装依赖(requests、openpyxl)
    2) 需要的话改下面 DATE_FROM / DATE_TO
    3) 双击『下载理财序时账.bat』或运行 python download_gl_licai.py
    4) 把生成的 金蝶理财序时账_1101_1012.xlsx 发回即可逐笔核对

注意：1101 的核算维度未必是"银行账号(FF100002)"，该列可能为空——
      这不影响，靠『科目全名 / 摘要 / 金额 / 日期』即可识别到具体理财产品。
"""

import os
import sys
import json
import configparser

import requests
from openpyxl import Workbook


# ============================ 可配置区（按需修改） ============================

FORM_ID = "GL_VOUCHER"

# —— 会计期间（按需改）——
DATE_FROM = "2026-06-01"
DATE_TO   = "2026-06-30"

# 理财相关科目前缀：交易性金融资产 1101 + 其它货币资金 1012
ACCT_PREFIXES = ["1101", "1012"]

_pref = " or ".join(f"FACCOUNTID.FNumber like '{p}%'" for p in ACCT_PREFIXES)
FILTER_STRING = (
    f"({_pref}) "
    f"and FDATE>='{DATE_FROM}' and FDATE<='{DATE_TO}'"
)

ORDER_STRING = "FDATE, FBILLNO"

FIELDS = [
    ("FAccountBookID.FName",  "账簿"),
    ("FDate",                 "日期"),
    ("FYEAR",                 "会计年度"),
    ("FPERIOD",               "期间"),
    ("FVOUCHERGROUPID.FName", "凭证字"),
    ("FVOUCHERGROUPNO",       "凭证号"),
    ("FCreatorId.FName",      "制单"),
    ("FCHECKERID.FName",      "审核"),
    ("FPOSTERID.FName",       "过账"),
    ("FCASHIERID.FName",      "出纳"),
    ("FACCOUNTID.FNumber",    "科目编码"),
    ("FACCOUNTID.FName",      "科目名称"),
    ("FACCOUNTID.FFullName",  "科目全名"),      # 理财产品常体现在科目全名/末级科目
    ("FCURRENCYID.FName",     "币别"),
    ("FEXCHANGERATE",         "汇率"),
    ("FAMOUNTFOR",            "原币金额"),
    ("FDEBIT",                "借方金额"),
    ("FCREDIT",               "贷方金额"),
    ("FEXPLANATION",          "摘要"),
    # 银行账号核算维度(1101 可能为空，保留以便与银行侧对齐)
    ("FDetailID.FF100002.FNumber", "核算维度.银行账号.编码"),
    ("FDetailID.FF100002.FName",   "核算维度.银行账号.名称"),
]

PAGE_SIZE = 2000

# 输出文件（与 download_gl_bank.py 同目录约定，文件名区分开）
OUTPUT_PATH = r"D:\银行金蝶稽核\金蝶理财序时账_1101_1012.xlsx"

CONF_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conf.ini")
CONF_NODE = "config"

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
    ws.title = "理财序时账"
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

    print(f"查询理财序时账（{FORM_ID}，科目 {'/'.join(ACCT_PREFIXES)}，{DATE_FROM}~{DATE_TO}）...")
    print(f"  过滤条件: {FILTER_STRING}")
    rows = fetch_all(session, conf)

    if not rows:
        print("没有符合条件的数据。请检查 科目前缀 / 期间 / 权限。")
        return

    path = write_excel(rows)
    print(f"完成！共 {len(rows)} 行，已保存到：\n  {path}")


if __name__ == "__main__":
    main()
