# -*- coding: utf-8 -*-
"""
金蝶云星空 - 导出 GL_VOUCHER(总账凭证) 的全部字段标识清单

用途：download_gl_bank.py 里"制单/审核/过账/出纳/核算维度.银行账号"等字段的
      确切标识各账套不同。跑一次本脚本，把 GL_VOUCHER 的字段 Key + 名称 全部列出，
      发回给对接人，即可一次性把字段配准，不用来回猜。

复用同目录 conf.ini。产出两份文件到桌面：
   - gl_voucher_字段清单.txt   （可读：字段标识 <Tab> 字段名）
   - gl_voucher_metadata.json  （原始返回，兜底用）
"""

import os, sys, json, configparser, requests

CONF_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conf.ini")
CONF_NODE = "config"
LOGIN_SVC = "Kingdee.BOS.WebApi.ServicesStub.AuthService.LoginByAppSecret.common.kdsvc"
META_SVC  = "Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.QueryBusinessInfo.common.kdsvc"
FORM_ID   = "GL_VOUCHER"
OUT_DIR   = os.path.join(os.path.expanduser("~"), "Desktop")


def load_conf():
    cfg = configparser.ConfigParser(); cfg.read(CONF_PATH, encoding="utf-8"); c = cfg[CONF_NODE]
    return {
        "acct_id": c.get("X-KDApi-AcctID","").strip(), "username": c.get("X-KDApi-UserName","").strip(),
        "app_id": c.get("X-KDApi-AppID","").strip(), "app_secret": c.get("X-KDApi-AppSec","").strip(),
        "server_url": c.get("X-KDApi-ServerUrl","").strip().rstrip("/"),
        "lcid": int(c.get("X-KDApi-LCID","2052") or "2052"),
    }

def post(session, conf, svc, parameters):
    url = f"{conf['server_url']}/{svc}"
    resp = session.post(url, data=json.dumps({"parameters": parameters}, ensure_ascii=False).encode("utf-8"),
                        headers={"Content-Type": "application/json;charset=utf-8"}, timeout=120)
    resp.raise_for_status(); return resp

def login(session, conf):
    r = post(session, conf, LOGIN_SVC, [conf["acct_id"], conf["username"], conf["app_id"], conf["app_secret"], conf["lcid"]])
    res = r.json()
    if not (isinstance(res, dict) and res.get("LoginResultType") == 1):
        sys.exit(f"登录失败：{res}")
    print("  登录成功")

def walk_fields(obj, found):
    """递归找出所有 {Key/字段标识, Name/字段名} 组合。"""
    if isinstance(obj, dict):
        key = obj.get("Key") or obj.get("key") or obj.get("FieldName")
        name = obj.get("Name") or obj.get("name") or obj.get("FieldCaption")
        if isinstance(name, dict):
            name = name.get("zh-CN") or name.get("zh_CN") or next(iter(name.values()), "")
        if key and isinstance(key, str):
            found[key] = name or ""
        for v in obj.values():
            walk_fields(v, found)
    elif isinstance(obj, list):
        for v in obj:
            walk_fields(v, found)


def main():
    conf = load_conf(); s = requests.Session()
    print("登录金蝶云星空 ...")
    login(s, conf)
    print(f"查询 {FORM_ID} 元数据 ...")
    r = post(s, conf, META_SVC, [{"FormId": FORM_ID}])
    try:
        data = r.json()
    except ValueError:
        sys.exit(f"返回非 JSON：\n{r.text[:500]}")

    os.makedirs(OUT_DIR, exist_ok=True)
    raw_path = os.path.join(OUT_DIR, "gl_voucher_metadata.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    found = {}
    walk_fields(data, found)
    txt_path = os.path.join(OUT_DIR, "gl_voucher_字段清单.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("字段标识\t字段名\n")
        for k in sorted(found):
            f.write(f"{k}\t{found[k]}\n")

    print(f"完成！共 {len(found)} 个字段标识。")
    print(f"  可读清单: {txt_path}")
    print(f"  原始返回: {raw_path}")
    print("请把这两个文件（尤其 txt）发给对接人，用于配准 制单/审核/过账/出纳/核算维度 等字段。")


if __name__ == "__main__":
    main()
