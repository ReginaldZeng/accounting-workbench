# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-07-02 | Author: Claude / c | Version: V1.13
# Description: 列出金蝶 GL_BALANCE(科目余额) 的全部字段标识清单，用于定位正确的"期末余额/本期发生"字段。
#              产出到桌面 gl_balance_字段清单.txt。只读，复用同目录 conf.ini。
import os, sys, json, configparser, requests
CONF_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conf.ini")
LOGIN_SVC = "Kingdee.BOS.WebApi.ServicesStub.AuthService.LoginByAppSecret.common.kdsvc"
META_SVC  = "Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.QueryBusinessInfo.common.kdsvc"
FORM_ID   = "GL_BALANCE"
OUT_DIR   = os.path.join(os.path.expanduser("~"), "Desktop")

def load_conf():
    cfg = configparser.ConfigParser(); cfg.read(CONF_PATH, encoding="utf-8"); c = cfg["config"]
    return {"acct_id": c.get("X-KDApi-AcctID","").strip(), "username": c.get("X-KDApi-UserName","").strip(),
            "app_id": c.get("X-KDApi-AppID","").strip(), "app_secret": c.get("X-KDApi-AppSec","").strip(),
            "server_url": c.get("X-KDApi-ServerUrl","").strip().rstrip("/"),
            "lcid": int(c.get("X-KDApi-LCID","2052") or "2052")}

def post(s, conf, svc, params):
    url = f"{conf['server_url']}/{svc}"
    r = s.post(url, data=json.dumps({"parameters": params}, ensure_ascii=False).encode("utf-8"),
               headers={"Content-Type": "application/json;charset=utf-8"}, timeout=120)
    r.raise_for_status(); return r

def login(s, conf):
    res = post(s, conf, LOGIN_SVC, [conf["acct_id"], conf["username"], conf["app_id"], conf["app_secret"], conf["lcid"]]).json()
    if not (isinstance(res, dict) and res.get("LoginResultType") == 1): sys.exit(f"登录失败：{res}")
    print("  登录成功")

def walk(obj, found):
    if isinstance(obj, dict):
        k = obj.get("Key") or obj.get("FieldName"); n = obj.get("Name")
        if isinstance(n, list) and n: n = n[0].get("Value","") if isinstance(n[0], dict) else n
        elif isinstance(n, dict): n = n.get("zh-CN") or next(iter(n.values()), "")
        if k and isinstance(k, str): found[k] = n if isinstance(n, str) else ""
        for v in obj.values(): walk(v, found)
    elif isinstance(obj, list):
        for v in obj: walk(v, found)

def main():
    conf = load_conf(); s = requests.Session()
    print("登录金蝶云星空 ..."); login(s, conf)
    print(f"查询 {FORM_ID} 元数据 ...")
    data = post(s, conf, META_SVC, [{"FormId": FORM_ID}]).json()
    found = {}; walk(data, found)
    os.makedirs(OUT_DIR, exist_ok=True)
    p = os.path.join(OUT_DIR, "gl_balance_字段清单.txt")
    HOT = ["余额","balance","期末","期初","本期","借","贷","debit","credit","发生","累计","ytd","end","begin"]
    with open(p, "w", encoding="utf-8") as f:
        f.write("=== 余额/发生 相关字段(重点看这里) ===\n")
        for k in sorted(found):
            if any(h in (k+str(found[k])).lower() for h in HOT):
                f.write(f"{k}\t{found[k]}\n")
        f.write("\n=== 全部字段 ===\n")
        for k in sorted(found):
            f.write(f"{k}\t{found[k]}\n")
    print(f"完成！共 {len(found)} 个字段。清单：{p}")
    print("请把 gl_balance_字段清单.txt 发给我。")

if __name__ == "__main__":
    main()
