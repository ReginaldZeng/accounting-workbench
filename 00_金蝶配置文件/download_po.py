# -*- coding: utf-8 -*-
"""
金蝶云星空 采购订单(PUR_PurchaseOrder) 列表 -> Excel 下载脚本

直接用 requests 调金蝶 WebAPI：
  - 登录：AuthService.LoginByAppSecret（第三方应用 AppId/AppSecret 免密登录）
  - 查询：DynamicFormService.ExecuteBillQuery（单据查询，分页取全）
授权信息复用同目录 conf.ini。

用法：
    1) 已装依赖（requests、openpyxl）
    2) 按需修改下面【可配置区】的 FILTER_STRING / FIELDS
    3) 双击 下载采购订单.bat（或 python download_po.py）
"""

import os
import sys
import json
import datetime
import configparser

import requests
from openpyxl import Workbook


# ============================ 可配置区（按需修改） ============================

# 采购订单单据标识，固定
FORM_ID = "PUR_PurchaseOrder"

# 过滤条件（金蝶 FilterString 语法）。默认：单据状态 = 已审核(C)。
# 其它状态：Z=暂存, A=创建, B=审核中, C=已审核, D=重新审核
# 可叠加日期：  "FDOCUMENTSTATUS='C' and FDate>='2026-01-01' and FDate<='2026-12-31'"
FILTER_STRING = "FDOCUMENTSTATUS='C'"

# 排序
ORDER_STRING = "FDate desc, FBillNo desc"

# 要导出的列：(金蝶字段标识, 中文表头)。顺序即 Excel 列顺序。
# 含明细字段时，ExecuteBillQuery 会自动按明细行展开（每条明细一行）。
# 若运行报某字段“未找到”，按你账套的 PUR_PurchaseOrder 字段标识调整这里即可。
FIELDS = [
    # —— 单据头 ——
    ("FBillNo",               "单据编号"),
    ("FDate",                 "采购日期"),
    ("FDocumentStatus",       "单据状态"),
    ("FBillTypeID.FName",     "单据类型"),
    ("FPurchaseOrgId.FName",  "采购组织"),
    ("FSupplierId.FNumber",   "供应商编码"),
    ("FSupplierId.FName",     "供应商名称"),
    ("FSettleCurrId.FName",   "结算币别"),
    # —— 物料明细行 ——
    ("FMaterialId.FNumber",   "物料编码"),
    ("FMaterialId.FName",     "物料名称"),
    ("FQty",                  "采购数量"),
    ("FTaxPrice",             "含税单价"),
    ("FAllAmount",            "价税合计"),
]

# 每页条数（金蝶单次上限一般 2000）。首次试跑可临时改小，例如 10
PAGE_SIZE = 2000

# 输出文件：固定路径、固定文件名。每天跑会自动覆盖同名旧文件。
OUTPUT_PATH = r"D:\采购原料成本核对\采购订单.xlsx"

# conf.ini 路径与配置节名（与应收单脚本共用同一个 conf.ini）
CONF_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conf.ini")
CONF_NODE = "config"

# WebAPI 服务路径（一般不用改）
LOGIN_SVC = "Kingdee.BOS.WebApi.ServicesStub.AuthService.LoginByAppSecret.common.kdsvc"
QUERY_SVC = "Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.ExecuteBillQuery.common.kdsvc"

# ==========================================================================


def load_conf():
    """读取 conf.ini，返回配置字典。"""
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
    """通用 WebAPI 调用：POST {"parameters": [...]}。"""
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
    """第三方应用免密登录。成功后 session 自带 sessionid cookie。"""
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
    """分页拉取全部数据，返回二维列表（不含表头）。"""
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
        # 错误可能是 [{...}] 或 [[{...}]]，取第一格看是不是错误字典
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
    """把数据写到 Excel（同名文件直接覆盖）。"""
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)  # 文件夹不存在则创建
    wb = Workbook()
    ws = wb.active
    ws.title = "采购订单"
    ws.append([cn for _, cn in FIELDS])          # 表头
    for row in rows:
        ws.append(list(row))                      # 数据
    try:
        wb.save(OUTPUT_PATH)                       # 覆盖写入
    except PermissionError:
        sys.exit(f"保存失败：文件可能正被 Excel 打开，请关闭后重试：\n  {OUTPUT_PATH}")
    return OUTPUT_PATH


def main():
    conf = load_conf()
    session = requests.Session()

    print("登录金蝶云星空 ...")
    login(session, conf)

    print(f"查询采购订单（{FORM_ID}）...")
    print(f"  过滤条件: {FILTER_STRING}")
    rows = fetch_all(session, conf)

    if not rows:
        print("没有符合条件的数据。请检查过滤条件或权限。")
        return

    path = write_excel(rows)
    print(f"完成！共 {len(rows)} 行，已保存到：\n  {path}")


if __name__ == "__main__":
    main()
