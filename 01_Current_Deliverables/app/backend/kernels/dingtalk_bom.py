# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-09-02 | Author: Claude / c | Version: V-draft(BOM报价审核)
# Description: 【BOM报价审核】钉钉抓取内核——按审批编号(business_id)定位实例并下载其附件字节。
#              只读，不动审批状态。凭据复用 notifier.load_dingtalk_conf()（conf.ini [dingtalk]，机密不落库/不落文档）。
#              移植自交接夹 tools/dt_fetch_attach.py，实测链路见交接文档 §3：
#                编号前 8 位=创建日期 → topapi/processinstance/listids（当天窗口）→ 逐个 get 匹配 business_id
#                → 附件 fileId 递归扫 form_component_values（含明细控件内嵌 JSON 串）+ 评论区 operation_records
#                → v1.0 workflow 下载接口（现有应用已具备该权限）拿 fileUrl → GET 字节。
#              没配 conf.ini / 缺 requests → configured()=False，各接口返回 {ok:False, msg:友好话}，绝不抛垮。

import io
import json
import re
import time

try:
    import requests
except Exception:                       # 干净环境没装 requests：整条抓取通道关掉，不拖垮别的工具线
    requests = None

try:
    import notifier                      # 复用已有的钉钉配置读取（conf.ini [dingtalk]）
except Exception:
    notifier = None

OAPI = "https://oapi.dingtalk.com/"
VAPI = "https://api.dingtalk.com"
# BOM表报价（研发使用）模板 processCode（交接文档 §3.1 实测）。抓不到 get_by_name 时兜底用它。
DEFAULT_PROCESS_CODE = "PROC-057F450E-7DFC-445E-A95E-089ACF77D63E"
_NAME_HINTS = ["BOM表报价（研发使用）", "BOM表报价", "销售报价支持需求", "销售报价支持", "定价审批"]


def configured():
    """conf.ini [dingtalk] 配齐 appkey/appsecret 且装了 requests → True。"""
    return bool(requests and notifier and notifier.load_dingtalk_conf())


def _conf():
    c = notifier.load_dingtalk_conf() if notifier else None
    if not c:
        raise RuntimeError("未配置钉钉应用（conf.ini [dingtalk] 缺 appkey/appsecret）")
    return c["appkey"], c["appsecret"]


def _scrub(msg, ak=None, sk=None):
    """从任何将回给前端的错误串里抹掉 appkey/appsecret（审查 H7：gettoken 把密钥放 URL query，
    网络异常串会带上完整 URL『/gettoken?appkey=...&appsecret=...』，绝不能让它越过服务端边界）。"""
    s = str(msg or "")
    for v in (sk, ak):
        if v:
            s = s.replace(str(v), "***")
    # 兜底：即便未拿到 ak/sk，也把 query 里的 appkey/appsecret 值统一打码
    s = re.sub(r"(appsecret|appkey)=[^&\s\"']+", r"\1=***", s, flags=re.I)
    return s


def _token(ak, sk):
    """老版 token（oapi）——审批实例查询/下载走它。密钥走 query（钉钉 gettoken 仅此形式），异常须经 _scrub 再外露。"""
    r = requests.get(OAPI + "gettoken", params={"appkey": ak, "appsecret": sk}, timeout=20).json()
    if r.get("errcode") != 0:
        raise RuntimeError("gettoken 失败：%s" % (r.get("errmsg") or r))   # 用解析后的 JSON，不带密钥
    return r["access_token"]


def _v2_token(ak, sk):
    """新版 token（api.dingtalk.com v1.0）——新版 workflow 下载接口走它。"""
    r = requests.post(VAPI + "/v1.0/oauth2/accessToken",
                      json={"appKey": ak, "appSecret": sk}, timeout=20).json()
    tok = r.get("accessToken")
    if not tok:
        raise RuntimeError("v1.0 accessToken 失败：%s" % r)
    return tok


def _oapi(tok, path, body):
    return requests.post(OAPI + path, params={"access_token": tok}, json=body, timeout=40).json()


def find_process_code(tok, name_hint=None):
    if name_hint and str(name_hint).startswith("PROC-"):
        return name_hint
    names = ([name_hint] if name_hint else []) + _NAME_HINTS
    seen = set()
    for nm in [n for n in names if n and not (n in seen or seen.add(n))]:
        r = _oapi(tok, "topapi/process/get_by_name", {"name": nm})
        if r.get("errcode") == 0:
            pc = r.get("process_code") or r.get("result")
            if isinstance(pc, dict):
                pc = pc.get("process_code")
            if pc:
                return pc
    return DEFAULT_PROCESS_CODE          # 名字查不到 → 用实测的默认 processCode 兜底


def list_ids(tok, pc, start_ms, end_ms):
    cursor, out = 0, []
    while True:
        r = _oapi(tok, "topapi/processinstance/listids",
                  {"process_code": pc, "start_time": start_ms, "end_time": end_ms, "size": 20, "cursor": cursor})
        if r.get("errcode") != 0:
            break
        res = r.get("result") or {}
        out += res.get("list") or []
        nc = res.get("next_cursor")
        if not nc:
            break
        cursor = nc
    return out


def get_inst(tok, iid):
    r = _oapi(tok, "topapi/processinstance/get", {"process_instance_id": iid})
    if r.get("errcode") != 0:
        return None
    return r.get("process_instance") or r.get("result")


def walk_attachments(obj, bag, label=None):
    """递归扫任意结构里的附件对象（含明细控件内嵌 JSON 串）。
    ⚠ 两种命名并存：**表单附件**用驼峰 fileId/fileName/spaceId；**评论区附件**（operation_records
    的 ADD_REMARK.attachments）用下划线 file_id/file_name/file_size。两者都要认，否则评论区补传的漏掉。
    label＝就近的表单控件 name（如「成本核算表（商务输出）/（商品版本）」），随递归下探更新，
    附件命中时一并记下——供上层判「来源方」（采购商务版/成本会计商品版/研发BOM）。"""
    if isinstance(obj, dict):
        cur = obj.get("name") or label     # 控件层带 name → 成为其内层附件的标注
        fid = obj.get("fileId") or obj.get("file_id")
        fname = obj.get("fileName") or obj.get("file_name")
        if fid and fname:
            a = dict(obj)
            a["fileId"] = fid              # 规范化成驼峰，下游 download/collect 统一
            a["fileName"] = fname
            a["fileSize"] = obj.get("fileSize") or obj.get("file_size")
            a.setdefault("_label", label)   # 附件自身不含 name，用上一层控件的 name
            bag.append(a)
        for k, v in obj.items():
            walk_attachments(v, bag, cur if k == "value" else (obj.get("name") or label))
    elif isinstance(obj, list):
        for v in obj:
            walk_attachments(v, bag, label)
    elif isinstance(obj, str) and any(t in obj for t in ("fileId", "fileName", "file_id", "file_name")):
        try:
            walk_attachments(json.loads(obj), bag, label)
        except Exception:
            pass


def collect_attachments(inst):
    """表单附件 + 评论区附件（operation_records）。返回去重后的列表，标 source。
    评论区结构在、理论可扫（交接文档 §3.4 尚无真实案例，遇到第一单实测）。"""
    form_bag, cmt_bag = [], []
    walk_attachments(inst.get("form_component_values"), form_bag)
    walk_attachments(inst.get("operation_records"), cmt_bag)
    out, seen = [], set()
    for src, bag in (("dingtalk_form", form_bag), ("dingtalk_comment", cmt_bag)):
        for a in bag:
            fid = str(a.get("fileId"))
            if fid in seen:
                continue
            seen.add(fid)
            out.append({"fileId": fid, "fileName": a.get("fileName"), "spaceId": a.get("spaceId"),
                        "fileSize": a.get("fileSize"), "source": src, "label": a.get("_label") or ""})
    return out


def download_url(tok_v2, tok_old, iid, file_id):
    """先新版 workflow 接口（现有应用已具备权限），失败回退老版 TOP。返回 (url, via)。"""
    try:
        j = requests.post(VAPI + "/v1.0/workflow/processInstances/spaces/files/urls/download",
                          headers={"x-acs-dingtalk-access-token": tok_v2},
                          json={"processInstanceId": iid, "fileId": str(file_id)}, timeout=30).json()
        res = j.get("result") or j
        for k in ("fileUrl", "downloadUri", "resourceUrl", "url"):
            if isinstance(res, dict) and res.get(k):
                return res[k], "v1.0"
    except Exception:
        pass
    r = _oapi(tok_old, "topapi/processinstance/file/url/get",
              {"request": {"process_instance_id": iid, "file_id": str(file_id)}})
    if r.get("errcode") == 0 and isinstance(r.get("result"), dict):
        for k in ("download_uri", "downloadUri", "url"):
            if r["result"].get(k):
                return r["result"][k], "top"
    return None, None


def _day_window(business_id, start=None, end=None):
    day = str(business_id)[:8]
    d0 = "%s-%s-%s" % (day[:4], day[4:6], day[6:8])
    start, end = start or d0, end or d0
    st = int(time.mktime(time.strptime(start, "%Y-%m-%d")) * 1000)
    et = int(time.mktime(time.strptime(end, "%Y-%m-%d")) * 1000) + 86399999
    return st, et


def fetch_approval(business_id, process_code=None, start=None, end=None, download=True):
    """按审批编号抓实例 + 下载附件字节。永不抛：出错回 {ok:False, msg}。
    返回 {ok, instanceId, title, businessId, status, attachments:[{fileName,fileId,source,fileSize,bytes?}], msg}。"""
    if not configured():
        return {"ok": False, "msg": "未配置钉钉应用或缺 requests——请在服务器 conf.ini [dingtalk] 配 appkey/appsecret 后再取数。"}
    ak = sk = None
    try:
        ak, sk = _conf()
        tok = _token(ak, sk)
        st, et = _day_window(business_id, start, end)
        pc = find_process_code(tok, process_code)
        ids = list_ids(tok, pc, st, et)
        target = None
        for iid in ids:
            inst = get_inst(tok, iid)
            if inst and str(inst.get("business_id") or "") == str(business_id):
                target = (iid, inst)
                break
        if not target:
            return {"ok": False, "msg": "当日该模板未找到编号 %s 的实例（模板或日期窗口可能不对）。" % business_id,
                    "instanceCount": len(ids)}
        iid, inst = target
        atts = collect_attachments(inst)
        if download:
            tok_v2 = _v2_token(ak, sk)
            for a in atts:
                url, via = download_url(tok_v2, tok, iid, a["fileId"])
                if url:
                    try:
                        a["bytes"] = requests.get(url, timeout=120).content
                        a["via"] = via
                    except Exception as e:
                        a["error"] = "下载失败：%s" % _scrub(e, ak, sk)
                else:
                    a["error"] = "拿不到下载链接"
        return {"ok": True, "instanceId": iid, "title": inst.get("title"),
                "businessId": str(business_id), "status": inst.get("status"),
                "attachments": atts, "instance": inst}
    except Exception as e:
        return {"ok": False, "msg": "钉钉取数失败：%s" % _scrub(e, ak, sk)}   # 抹掉可能带的 appkey/appsecret（审查 H7）
