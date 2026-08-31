# -*- coding: utf-8 -*-
"""
探针：试出"核算维度-银行账号"在 ExecuteBillQuery 里能取到值的正确字段写法。

逐个候选写法各查 1 行 GL_VOUCHER(科目1002*)，打印每种写法：成功(附样例值) 或 报错。
把输出发给对接人，即可把 download_gl_bank.py 里核算维度那两行配准。
复用同目录 conf.ini。
"""
import os, sys, json, configparser, requests

CONF_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conf.ini")
CONF_NODE = "config"
LOGIN_SVC = "Kingdee.BOS.WebApi.ServicesStub.AuthService.LoginByAppSecret.common.kdsvc"
QUERY_SVC = "Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.ExecuteBillQuery.common.kdsvc"
FORM_ID   = "GL_VOUCHER"
FILTER    = "FACCOUNTID.FNumber like '1002%' and FDATE>='2026-06-01' and FDATE<='2026-06-30'"

# 候选写法（银行账号维度 FF100002 / PropertyName F100002 / 关联 CN_BANKACNT）
CANDIDATES = [
    "FDETAILID__FF100002",
    "FDETAILID__FF100002.FNumber",
    "FDETAILID__FF100002.FName",
    "FDETAILID__FF100002.FNUMBER",
    "FDETAILID__FF100002.FDATAVALUE",
    "FF100002",
    "FF100002.FNumber",
    "FF100002.FName",
    "FDetailID.FF100002",
    "FDetailID.FF100002.FNumber",
    "FDETAILID_FF100002",
    "F100002",
    "F100002.FNumber",
]


def load_conf():
    cfg = configparser.ConfigParser(); cfg.read(CONF_PATH, encoding="utf-8"); c = cfg[CONF_NODE]
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
    if not (isinstance(res, dict) and res.get("LoginResultType") == 1):
        sys.exit(f"登录失败：{res}")
    print("  登录成功\n")

def try_field(s, conf, key):
    # 同时取 科目编码 + 候选字段，便于看样例
    query = {"FormId": FORM_ID, "FieldKeys": f"FACCOUNTID.FNumber,{key}",
             "FilterString": FILTER, "OrderString": "", "TopRowCount": 0,
             "StartRow": 0, "Limit": 3}
    try:
        data = post(s, conf, QUERY_SVC, [json.dumps(query, ensure_ascii=False)]).json()
    except Exception as e:
        return f"请求异常: {e}"
    if isinstance(data, dict):
        return "错误: " + json.dumps(data, ensure_ascii=False)[:150]
    if isinstance(data, list):
        if data and isinstance(data[0], list):
            cell0 = data[0][0] if data[0] else None
            if isinstance(cell0, dict):  # 错误字典
                try:
                    msg = "；".join(e.get("Message","") for e in cell0["Result"]["ResponseStatus"]["Errors"])
                except Exception:
                    msg = json.dumps(cell0, ensure_ascii=False)[:150]
                return "错误: " + msg
            samples = [row[1] for row in data[:3]]
            return f"✅ 成功  样例值={samples}"
        return "✅ 成功(空数据)"
    return f"未知返回: {str(data)[:120]}"


def main():
    conf = load_conf(); s = requests.Session()
    print("登录金蝶云星空 ...")
    login(s, conf)
    print(f"逐个测试 {len(CANDIDATES)} 种写法（银行账号核算维度）：\n")
    for key in CANDIDATES:
        print(f"[{key}]")
        print("   ->", try_field(s, conf, key), "\n")
    print("把上面结果整段发给对接人；带 ✅ 且样例值像银行账号/账户名的那一行，就是正确写法。")


if __name__ == "__main__":
    main()
