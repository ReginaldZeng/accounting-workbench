# -*- coding: utf-8 -*-
"""
探针：找出云星空里可用的"科目余额"查询对象(FormId)。

逐个候选 FormId 调 QueryBusinessInfo：
  - 若返回元数据 -> 该对象存在，打印字段数 + 余额相关字段(余额/Balance/期末/借/贷/期间/科目)
  - 若报错 -> 打印报错(说明该账套没有这个对象)
输出到控制台，并写到桌面 gl_balance_probe.txt。把结果发给对接人即可定下取数脚本。
只读，不改金蝶。复用同目录 conf.ini。
"""
import os, sys, json, configparser, requests

CONF_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conf.ini")
CONF_NODE = "config"
LOGIN_SVC = "Kingdee.BOS.WebApi.ServicesStub.AuthService.LoginByAppSecret.common.kdsvc"
META_SVC  = "Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.QueryBusinessInfo.common.kdsvc"
OUT = os.path.join(os.path.expanduser("~"), "Desktop", "gl_balance_probe.txt")

# 候选"科目余额/账表"单据标识
CANDIDATES = [
    "GL_BALANCE", "GL_ACCBALANCE", "GL_ACCOUNTBALANCE",
    "GL_RPT_BALANCE", "GL_RPT_BALANCELIST", "GL_RPT_AccountBalance",
    "GL_RPT_SubsidiaryLedger", "GL_INITBALANCE", "AcctBalance",
]
# 余额相关关键词(用于从字段里挑重点)
HOT = ["余额", "Balance", "期末", "期初", "借", "贷", "Debit", "Credit", "期间", "Period", "科目", "Account", "年度", "Year", "币", "Curr"]


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

def walk_fields(obj, found):
    if isinstance(obj, dict):
        key = obj.get("Key") or obj.get("FieldName")
        name = obj.get("Name")
        if isinstance(name, list) and name:
            name = name[0].get("Value","") if isinstance(name[0], dict) else name
        elif isinstance(name, dict):
            name = name.get("zh-CN") or next(iter(name.values()), "")
        if key and isinstance(key, str):
            found[key] = name if isinstance(name, str) else ""
        for v in obj.values(): walk_fields(v, found)
    elif isinstance(obj, list):
        for v in obj: walk_fields(v, found)


def main():
    conf = load_conf(); s = requests.Session()
    print("登录金蝶云星空 ...")
    login(s, conf)
    lines = []
    for fid in CANDIDATES:
        try:
            data = post(s, conf, META_SVC, [{"FormId": fid}]).json()
        except Exception as e:
            print(f"[{fid}] 请求异常: {e}\n"); lines.append(f"[{fid}] 请求异常: {e}"); continue
        found = {}
        walk_fields(data, found)
        if found:
            hot = {k: v for k, v in found.items() if any(h.lower() in (k+str(v)).lower() for h in HOT)}
            print(f"[{fid}] ✅ 存在，共 {len(found)} 字段。余额相关字段：")
            lines.append(f"\n[{fid}] 存在，共 {len(found)} 字段。余额相关：")
            for k in sorted(hot):
                print(f"    {k}\t{hot[k]}")
                lines.append(f"    {k}\t{hot[k]}")
        else:
            msg = json.dumps(data, ensure_ascii=False)[:150]
            print(f"[{fid}] ✗ 不存在/无字段：{msg}")
            lines.append(f"[{fid}] 不存在/报错：{msg}")
        print()
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"结果已写到：{OUT}\n请把带 ✅ 的对象及其字段发给对接人，用于确定科目余额取数脚本。")


if __name__ == "__main__":
    main()
