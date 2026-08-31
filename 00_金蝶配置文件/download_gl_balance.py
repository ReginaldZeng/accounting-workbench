# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-07-02 | Author: Claude / c | Version: V1.7
# Description: 科目范围由 1002/1012 扩到 资金看板四类：库存现金1001 + 银行存款1002
#              + 其它货币资金1012 + 交易性金融资产1101（见 ACCT_PREFIXES）。只读，不改金蝶。
"""
金蝶云星空 科目余额(GL_BALANCE) -> Excel
拉 资金看板四类科目（库存现金1001* / 银行存款1002* / 其它货币资金1012* / 交易性金融资产1101*）
指定年/期的 期初/本期借贷/期末余额，并带上"银行账号"核算维度，得到每个账户的期末余额
(资金看板的余额来源)。复用同目录 conf.ini。只读，不改金蝶。

首跑若"核算维度"两列报错(GL_BALANCE 不按银行账号存余额)：
  把带 [核算维度] 标记的两行注释掉，即可得到科目级余额；
  再由序时账的本期借贷 + 此期初，按账户算出期末(我来处理)。
"""
import os, sys, json, configparser, requests
from openpyxl import Workbook

# ===== 可配置 =====
FORM_ID = "GL_BALANCE"
YEAR = 2026
PERIOD = 6
ACCT_PREFIXES = ["1001", "1002", "1012", "1101"]   # 库存现金 + 银行存款 + 其它货币资金 + 交易性金融资产
OUTPUT_PATH = r"D:\银行金蝶稽核\金蝶科目余额表.xlsx"

FIELDS = [
    ("FACCOUNTBOOKID.FName", "账簿"),
    ("FYear",                "年"),
    ("FPeriod",              "期"),
    ("FAccountID.FNumber",   "科目编码"),
    ("FAccountID.FName",     "科目名称"),
    ("FCurrencyID.FName",    "币别"),
    ("FBeginBalanceFor",     "期初原币"),
    ("FDebitFor",            "本期借方原币"),
    ("FCreditFor",           "本期贷方原币"),
    ("FEndBalanceFor",       "期末原币"),
    ("FEndBalance",          "期末本位币"),
    ("FDetailID.FF100002.FNumber", "核算维度.银行账号.编码"),   # [核算维度] 报错就注释此行
    ("FDetailID.FF100002.FName",   "核算维度.银行账号.名称"),   # [核算维度] 报错就注释此行
]

_pref = " or ".join(f"FAccountID.FNumber like '{p}%'" for p in ACCT_PREFIXES)
FILTER_STRING = f"({_pref}) and FYear={YEAR} and FPeriod={PERIOD}"
ORDER_STRING = "FAccountID.FNumber"
PAGE_SIZE = 2000

CONF_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conf.ini")
CONF_NODE = "config"
LOGIN_SVC = "Kingdee.BOS.WebApi.ServicesStub.AuthService.LoginByAppSecret.common.kdsvc"
QUERY_SVC = "Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.ExecuteBillQuery.common.kdsvc"


def load_conf():
    cfg = configparser.ConfigParser(); cfg.read(CONF_PATH, encoding="utf-8"); c = cfg[CONF_NODE]
    conf = {"acct_id": c.get("X-KDApi-AcctID","").strip(), "username": c.get("X-KDApi-UserName","").strip(),
            "app_id": c.get("X-KDApi-AppID","").strip(), "app_secret": c.get("X-KDApi-AppSec","").strip(),
            "server_url": c.get("X-KDApi-ServerUrl","").strip().rstrip("/"),
            "lcid": int(c.get("X-KDApi-LCID","2052") or "2052")}
    miss = [k for k in ("acct_id","username","app_id","app_secret","server_url") if not conf[k]]
    if miss: sys.exit(f"conf.ini 缺项：{', '.join(miss)}")
    return conf

def post(s, conf, svc, params):
    url = f"{conf['server_url']}/{svc}"
    r = s.post(url, data=json.dumps({"parameters": params}, ensure_ascii=False).encode("utf-8"),
               headers={"Content-Type": "application/json;charset=utf-8"}, timeout=120)
    r.raise_for_status(); return r

def login(s, conf):
    res = post(s, conf, LOGIN_SVC, [conf["acct_id"], conf["username"], conf["app_id"], conf["app_secret"], conf["lcid"]]).json()
    if not (isinstance(res, dict) and res.get("LoginResultType") == 1):
        sys.exit(f"登录失败：{res}")
    print(f"  登录成功（用户：{conf['username']}）")

def fetch_all(s, conf):
    keys = ",".join(f for f,_ in FIELDS); rows=[]; start=0
    while True:
        q = {"FormId": FORM_ID, "FieldKeys": keys, "FilterString": FILTER_STRING,
             "OrderString": ORDER_STRING, "TopRowCount": 0, "StartRow": start, "Limit": PAGE_SIZE}
        data = post(s, conf, QUERY_SVC, [json.dumps(q, ensure_ascii=False)]).json()
        if isinstance(data, dict): sys.exit(f"查询报错：{json.dumps(data,ensure_ascii=False)[:600]}")
        if data and isinstance(data[0], list) and data[0] and isinstance(data[0][0], dict):
            try: msg = "；".join(e.get("Message","") for e in data[0][0]["Result"]["ResponseStatus"]["Errors"])
            except Exception: msg = json.dumps(data[0][0], ensure_ascii=False)[:300]
            sys.exit(f"查询报错：{msg}")
        rows += data; print(f"  已获取 {len(rows)} 行 (本页 {len(data)})")
        if len(data) < PAGE_SIZE: break
        start += PAGE_SIZE
    return rows

def write_excel(rows):
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    wb = Workbook(); ws = wb.active; ws.title = "科目余额表"
    ws.append([cn for _,cn in FIELDS])
    for r in rows: ws.append(list(r))
    try: wb.save(OUTPUT_PATH)
    except PermissionError: sys.exit(f"保存失败：文件可能正被打开：{OUTPUT_PATH}")
    return OUTPUT_PATH

def main():
    conf = load_conf(); s = requests.Session()
    print("登录金蝶云星空 ...")
    login(s, conf)
    print(f"查询科目余额（{FORM_ID}，科目 {'/'.join(ACCT_PREFIXES)}，{YEAR}年第{PERIOD}期）...")
    print(f"  过滤: {FILTER_STRING}")
    rows = fetch_all(s, conf)
    if not rows:
        print("没有数据。检查 年/期/科目前缀/权限。"); return
    path = write_excel(rows)
    print(f"完成！共 {len(rows)} 行，已保存到：\n  {path}")


if __name__ == "__main__":
    main()
