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


def list_ids(tok, pc, start_ms, end_ms, errs=None):
    """errs 传个 list 进来 → 钉钉报错时把原话记进去（V2.872：以前静默 break，「接口报错」和「确实没有」分不清）。"""
    cursor, out = 0, []
    while True:
        r = _oapi(tok, "topapi/processinstance/listids",
                  {"process_code": pc, "start_time": start_ms, "end_time": end_ms, "size": 20, "cursor": cursor})
        if r.get("errcode") != 0:
            if errs is not None:
                errs.append("%s（%s）" % (r.get("errmsg") or "未知错误", r.get("errcode")))
            break
        res = r.get("result") or {}
        out += res.get("list") or []
        nc = res.get("next_cursor")
        if not nc:
            break
        cursor = nc
    return out


def get_inst(tok, iid, errs=None):
    r = _oapi(tok, "topapi/processinstance/get", {"process_instance_id": iid})
    if r.get("errcode") != 0:
        if errs is not None:
            errs.append("%s（%s）" % (r.get("errmsg") or "未知错误", r.get("errcode")))
        return None
    return r.get("process_instance") or r.get("result")


# ---- 审批编号 ↔ 实例号（V2.872）----
# 钉钉**没有**「按审批编号直接取单」的接口：只能按发起日期列出当天的单再逐个比对编号，而「按日期列单」新老两个接口都只给查
# **近 365 天内发起**的（2026-10-08 实测：往回 365 天可查、366 天起老接口回 400003「时间戳无效」、新接口回 invalidEndTime）。
# 所以：①找到过一次就把「编号→实例号」记下来，以后直接按实例号取（不怕单子变老，也省掉逐个比对）；
#       ②一年以前的老单，让人贴钉钉里这张审批单的链接（链接里带实例号），按实例号取。
LIST_MAX_DAYS = 365
_IID_MEM = {}
_IID_STORE = [None, None]               # [getter(business_id)->iid, setter(business_id, iid)]，路由层注入持久化；不注入只在进程内记
_REF_KEYS = ("procInstId", "procInsId", "processInstanceId", "process_instance_id", "proc_inst_id", "instanceId", "instance_id")


def set_iid_store(getter, setter):
    _IID_STORE[0], _IID_STORE[1] = getter, setter


def known_iid(business_id):
    bid = str(business_id or "")
    if not bid:
        return ""
    if bid in _IID_MEM:
        return _IID_MEM[bid]
    try:
        v = _IID_STORE[0](bid) if _IID_STORE[0] else ""
    except Exception:
        v = ""
    if v:
        _IID_MEM[bid] = str(v)
    return str(v or "")


def remember_iid(business_id, iid):
    bid, iid = str(business_id or ""), str(iid or "")
    if not bid or not iid or _IID_MEM.get(bid) == iid:
        return
    _IID_MEM[bid] = iid
    try:
        if _IID_STORE[1]:
            _IID_STORE[1](bid, iid)
    except Exception:
        pass


def parse_instance_ref(text):
    """人贴进来的东西里认出实例号：钉钉审批单链接（带 procInstId= 之类）或直接一串实例号。纯数字（审批编号）→ ""。"""
    s = str(text or "").strip()
    if not s or s.isdigit():
        return ""
    from urllib.parse import unquote
    u = unquote(unquote(s))
    for k in _REF_KEYS:
        m = re.search(r"(?:^|[?&#/;])%s=([A-Za-z0-9_\-]{8,96})" % re.escape(k), u)
        if m:
            return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_\-]{16,96}", s) and re.search(r"[A-Za-z]", s):
        return s                         # 直接贴了实例号
    return ""


def age_days(business_id):
    """审批编号前 8 位＝发起日期 → 距今多少天；认不出 → None。"""
    try:
        t = time.mktime(time.strptime(str(business_id)[:8], "%Y%m%d"))
    except Exception:
        return None
    return int((time.time() - t) // 86400)


def too_old_msg(business_id):
    d = str(business_id)[:8]
    return ("这张单是 %s-%s-%s 发起的，距今 %s 天。钉钉只允许按编号查近 %d 天内发起的审批单，所以按编号取不到（不是单号填错）。"
            "两条路：①在钉钉里打开这张审批单 → 复制它的链接 → 贴到这个输入框再点立项（链接里带着这张单的内部号，不受一年限制）；"
            "②或在钉钉下载附件，用下面的「上传采购核算表」立项（单号照填）。"
            % (d[:4], d[4:6], d[6:8], age_days(business_id), LIST_MAX_DAYS))


def _locate(tok, business_id, process_code=None, start=None, end=None):
    """按审批编号定位实例。→ (iid, inst, why, n)；why：""=找到 / "too_old" / "api:<钉钉原话>" / "not_found"；n=当天该模板的单数。"""
    bid = str(business_id or "")
    iid = known_iid(bid)
    if iid:
        inst = get_inst(tok, iid)
        if inst and str(inst.get("business_id") or "") == bid:
            return iid, inst, "", 0
    st, et = _day_window(bid, start, end)
    floor = int(time.time() * 1000) - LIST_MAX_DAYS * 86400000 + 60000     # 钉钉能查到的最早时刻（留 1 分钟余量）
    if et < floor:
        return None, None, "too_old", 0
    st = max(st, floor)                  # 恰好卡在第 365 天的单：把起点收到允许范围内，别整窗被拒
    pc = find_process_code(tok, process_code)
    errs = []
    ids = list_ids(tok, pc, st, et, errs)
    for i in ids:
        inst = get_inst(tok, i)
        if inst and str(inst.get("business_id") or "") == bid:
            remember_iid(bid, i)
            return i, inst, "", len(ids)
    if errs:
        return None, None, "api:" + errs[0], len(ids)
    return None, None, "not_found", len(ids)


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
    评论区附件另带 byUserId / at（哪位评论人、何时传的）——供上层判断「没取到的是不是重传前的旧件」。
    实测（2026-10-06，350508/251965/027725/020869）：评论区附件走常规下载接口**已能取到**（早先一律回 400020 无访问权限，
    2026-09-06 应用开通 Storage.DownloadInfo.Read 之后的某个时点起可取，确切原因未查明）；仍回 400020 的多为评论人删除/撤回后重传的旧件。"""
    form_bag, cmt_bag = [], []
    walk_attachments(inst.get("form_component_values"), form_bag)
    walk_attachments(inst.get("operation_records"), cmt_bag)
    meta = {}
    for r in (inst.get("operation_records") or []):
        for a in (r.get("attachments") or []):
            fid = str(a.get("file_id") or a.get("fileId") or "")
            if fid:
                meta[fid] = {"byUserId": r.get("userid") or "", "at": str(r.get("date") or "")}
    out, seen = [], set()
    for src, bag in (("dingtalk_form", form_bag), ("dingtalk_comment", cmt_bag)):
        for a in bag:
            fid = str(a.get("fileId"))
            if fid in seen:
                continue
            seen.add(fid)
            out.append({"fileId": fid, "fileName": a.get("fileName"), "spaceId": a.get("spaceId"),
                        "fileSize": a.get("fileSize"), "source": src, "label": a.get("_label") or "",
                        **(meta.get(fid, {}) if src == "dingtalk_comment" else {})})
    return out


def download_url(tok_v2, tok_old, iid, file_id):
    """先新版 workflow 接口（现有应用已具备权限），失败回退老版 TOP。返回 (url, via)；失败 → (None, 原因)。
    ⚠ 实证 2026-09-06（202607011742000186641）：**发起人钉钉账号已不存在**（离职/注销，v2/user/get 60121）时，
      两个接口都回「用户不存在 / 找不到该用户」——钉钉按发起人身份放附件，人没了这两个接口就拿不到。
      V2.469 起由 storage_download() 走钉盘代下载兜底（应用已开 Storage.DownloadInfo.Read，2026-09-06 实证 8 附件全通）。
      原因要带回去让页面讲清楚，别只说「拿不到下载链接」。
    ⚠ 实证 2026-10-01（202609111542000383471）：**审批人在评论里补的附件**不带 withCommentAttatchment 一律回 noPermission，
      带上就通；表单附件带上同样能下（钉钉参数名本身拼错，照抄）。"""
    reasons = []
    try:
        j = requests.post(VAPI + "/v1.0/workflow/processInstances/spaces/files/urls/download",
                          headers={"x-acs-dingtalk-access-token": tok_v2},
                          json={"processInstanceId": iid, "fileId": str(file_id), "withCommentAttatchment": True}, timeout=30).json()
        res = j.get("result") or j
        for k in ("fileUrl", "downloadUri", "resourceUrl", "url"):
            if isinstance(res, dict) and res.get(k):
                return res[k], "v1.0"
        if isinstance(j, dict) and (j.get("code") or j.get("message")):
            reasons.append("%s %s" % (j.get("code") or "", j.get("message") or ""))
    except Exception as e:
        reasons.append(str(e)[:80])
    r = _oapi(tok_old, "topapi/processinstance/file/url/get",
              {"request": {"process_instance_id": iid, "file_id": str(file_id)}})
    if r.get("errcode") == 0 and isinstance(r.get("result"), dict):
        for k in ("download_uri", "downloadUri", "url"):
            if r["result"].get(k):
                return r["result"][k], "top"
    if r.get("errmsg"):
        reasons.append("%s %s" % (r.get("errcode", ""), r.get("errmsg")))
    return None, "；".join(x.strip() for x in reasons if x.strip()) or None


_STORAGE_SCOPE = "Storage.DownloadInfo.Read"


def _live_users(tok_old, inst, limit=4):
    """实例的审批/抄送/操作人里**账号仍存在**的 (userid, unionid, name)——发起人没了时，借他们的身份去钉盘要文件。"""
    out, seen = [], set()
    for o in inst.get("operation_records") or []:
        uid = o.get("userid")
        if not uid or uid in seen:
            continue
        seen.add(uid)
        r = (_oapi(tok_old, "topapi/v2/user/get", {"userid": uid}).get("result") or {})
        if r.get("unionid"):
            out.append((uid, r["unionid"], r.get("name") or uid))
        if len(out) >= limit:
            break
    return out


def storage_download(tok_v2, tok_old, iid, file_id, space_id, inst):
    """备用通道（V2.469，实证 186641 发起人已离职）：**审批附件下载接口按发起人放行，发起人账号没了就一律「用户不存在」**。
    绕法＝①`processinstance/cspace/preview` 把该附件授权给一个仍在职的审批人（实测回 errcode 0）
         ②以其 unionId 调钉盘 `/v1.0/storage/spaces/{spaceId}/dentries/{fileId}/downloadInfos/query` 拿带签名的下载地址
    ②需要应用开通权限 **Storage.DownloadInfo.Read**（实测回 403 指名此权限；开通后本函数即通）。
    返回 (url, headers, via) 或 (None, None, 原因)。"""
    if not space_id:
        return None, None, "附件无 spaceId"
    reason = ""
    for uid, union, name in _live_users(tok_old, inst):
        try:
            _oapi(tok_old, "topapi/processinstance/cspace/preview",
                  {"request": {"process_instance_id": iid, "file_id": str(file_id), "userid": uid}})
            resp = requests.post(VAPI + "/v1.0/storage/spaces/%s/dentries/%s/downloadInfos/query?unionId=%s" % (space_id, file_id, union),
                                 headers={"x-acs-dingtalk-access-token": tok_v2},
                                 json={"withInternalResourceUrl": False}, timeout=30)
            j = resp.json() if resp.content else {}
            if resp.status_code == 200:
                sig = j.get("headerSignatureInfo") or j
                urls = sig.get("resourceUrls") or j.get("resourceUrls") or []
                if urls:
                    return urls[0], (sig.get("headers") or {}), "storage(代下载·%s)" % name
                reason = "钉盘回应无下载地址"
            else:
                reason = "%s %s" % (j.get("code") or resp.status_code, (j.get("message") or "")[:120])
                if "Permission" in str(j.get("code") or "") or _STORAGE_SCOPE in str(j.get("message") or ""):
                    return None, None, "需开通应用权限 %s（钉钉开发者后台 › 应用 › 权限管理），开通后重新立项即自动代下载" % _STORAGE_SCOPE
        except Exception as e:
            reason = str(e)[:100]
    return None, None, reason or "无可用的在职审批人身份"


_TASK_OPEN = ("NEW", "RUNNING", "PAUSED")


def list_running_at_nodes(node_ids, since, process_code=None, until=None):
    """在途单里**当前停在指定节点**的（V2.504 自动立项用）：按模板列 since..until 窗口内的实例，逐个看 status=RUNNING
    且 tasks 里有 activity_id∈node_ids 且 task_status 未结（NEW/RUNNING/PAUSED）。
    返回 [{businessId, instanceId, title, nodeId, taskUserId, taskCreateTime, createTime}]；未配置/异常 → []（不抛）。
    只读；窗口由调用方给（上线日起），不扫历史。"""
    if not configured():
        return []
    try:
        ak, sk = _conf()
        tok = _token(ak, sk)
        pc = find_process_code(tok, process_code)
        st, et = _day_window("00000000", since, until or time.strftime("%Y-%m-%d"))
        out = []
        for iid in list_ids(tok, pc, st, et):
            inst = get_inst(tok, iid)
            if not inst or (inst.get("status") or "").upper() != "RUNNING":
                continue
            opens = [t for t in (inst.get("tasks") or []) if t.get("activity_id") in node_ids and (t.get("task_status") or "").upper() in _TASK_OPEN]
            if opens:      # 或签节点有多个在办人（实证 23b2_ee20：志鹏 + 冯辉）→ 全部带回，提醒都发
                t = opens[0]
                out.append({"businessId": str(inst.get("business_id") or ""), "instanceId": iid, "title": inst.get("title") or "",
                            "nodeId": t.get("activity_id"), "taskUserId": t.get("userid") or "",
                            "taskUserIds": [x.get("userid") for x in opens if x.get("userid")],
                            "taskCreateTime": t.get("create_time") or "", "createTime": inst.get("create_time") or ""})
        return out
    except Exception:
        return []


def _day_window(business_id, start=None, end=None):
    day = str(business_id)[:8]
    d0 = "%s-%s-%s" % (day[:4], day[4:6], day[6:8])
    start, end = start or d0, end or d0
    st = int(time.mktime(time.strptime(start, "%Y-%m-%d")) * 1000)
    et = int(time.mktime(time.strptime(end, "%Y-%m-%d")) * 1000) + 86399999
    return st, et


def fetch_approval(business_id, process_code=None, start=None, end=None, download=True, instance_id=None):
    """按审批编号抓实例 + 下载附件字节。永不抛：出错回 {ok:False, msg}。
    instance_id 给了（人贴的审批单链接里认出来的）→ 直接按实例号取，business_id 以钉钉回的为准（V2.872，一年以前的老单走这条）。
    返回 {ok, instanceId, title, businessId, status, attachments:[{fileName,fileId,source,fileSize,bytes?}], msg}。"""
    if not configured():
        return {"ok": False, "msg": "未配置钉钉应用或缺 requests——请在服务器 conf.ini [dingtalk] 配 appkey/appsecret 后再取数。"}
    ak = sk = None
    try:
        ak, sk = _conf()
        tok = _token(ak, sk)
        if instance_id:
            errs = []
            iid, inst = str(instance_id), get_inst(tok, str(instance_id), errs)
            if not inst:
                return {"ok": False, "msg": "按贴进来的链接没取到这张审批单（钉钉回：%s）。请确认复制的是这张审批单自己的链接；"
                                            "仍不行就在钉钉下载附件，用下面的「上传采购核算表」立项。" % (errs[0] if errs else "空")}
            business_id = str(inst.get("business_id") or business_id or "")
            if not business_id:
                return {"ok": False, "msg": "按链接取到了审批单，但钉钉没回审批编号，没法立项。"}
            remember_iid(business_id, iid)
        else:
            iid, inst, why, n = _locate(tok, business_id, process_code, start, end)
            if why == "too_old" or (why.startswith("api:") and "时间戳" in why and (age_days(business_id) or 0) >= LIST_MAX_DAYS):
                return {"ok": False, "tooOld": True, "msg": too_old_msg(business_id)}
            if why.startswith("api:"):
                return {"ok": False, "msg": "钉钉没让查这一天的审批单（钉钉回：%s）——是接口报错，不是确认没有这张单。稍后再试；急用可下载附件走下面的上传。" % why[4:]}
            if why:
                return {"ok": False, "instanceCount": n,
                        "msg": "钉钉里这一天「BOM表报价」模板共 %d 张单，没有编号 %s 的。请核对单号有没有输错位；"
                               "如果这张单不是用「BOM表报价（研发使用）」模板发起的，也会找不到——那就贴这张审批单的链接，或用下面的上传。" % (n, business_id)}
        atts = collect_attachments(inst)
        if download:
            tok_v2 = _v2_token(ak, sk)
            storage_hint = ""
            for a in atts:
                url, via = download_url(tok_v2, tok, iid, a["fileId"])
                headers = {}
                if not url and via and any(k in via for k in ("用户不存在", "找不到该用户", "userNotExist")):
                    # 发起人账号没了 → 备用通道：授权在职审批人 + 钉盘代下载（需 Storage.DownloadInfo.Read）
                    # ⚠ 评论区附件回的 400020 不走这里（cspace/preview 对它也回 400020）；2026-10-06 起评论区附件常规接口多数已可取，
                    #    仍 400020 的多是评论人删除/撤回后重传的旧件，见 collect_attachments 说明
                    url2, headers2, via2 = storage_download(tok_v2, tok, iid, a["fileId"], a.get("spaceId"), inst)
                    if url2:
                        url, via, headers = url2, via2, headers2 or {}
                    else:
                        storage_hint = via2 or ""
                        via = "%s；备用通道：%s" % (via, via2 or "失败")
                if url:
                    try:
                        a["bytes"] = requests.get(url, headers=headers or None, timeout=120).content
                        a["via"] = via
                    except Exception as e:
                        a["error"] = "下载失败：%s" % _scrub(e, ak, sk)
                else:
                    a["error"] = "拿不到下载链接" + ("（%s）" % via if via else "")
        # 发起人账号已不存在（离职/注销）且备用通道也没拿到 → 上层据此给人话提示（含要开的权限名）
        originator_gone = False
        errs = [a.get("error") or "" for a in atts]
        if atts and all(("用户不存在" in e or "找不到该用户" in e or "userNotExist" in e) for e in errs):
            originator_gone = True
        return {"ok": True, "instanceId": iid, "title": inst.get("title"),
                "businessId": str(business_id), "status": inst.get("status"),
                "originatorGone": originator_gone, "originatorUserId": inst.get("originator_userid"),
                "storageHint": (storage_hint if download else ""),
                "attachments": atts, "instance": inst}
    except Exception as e:
        return {"ok": False, "msg": "钉钉取数失败：%s" % _scrub(e, ak, sk)}   # 抹掉可能带的 appkey/appsecret（审查 H7）


def _find_inst(tok, business_id, process_code=None):
    """按审批编号定位实例（记过实例号的直接取；否则当日窗口 + business_id 精确匹配）。→ (iid, inst) / (None, None)"""
    iid, inst, _why, _n = _locate(tok, business_id, process_code)
    return iid, inst


def final_state_from_inst(inst, node_ids, iid=""):
    """纯函数（可离线测）：从实例 dict 判「财务经理/BP 节点」最近一次动作。
    两种信号取**较晚者**：①该节点已完成任务 task_result AGREE/REFUSE；②**回退**＝REDIRECT_* 操作记录之后、在**非该节点**（更早的节点，如成本核算）
    出现了新任务（27725 实证 2026-09-04：REDIRECT_PROCESS → 财务经理任务 CANCELED、成本核算节点新任务；同节点转交不算回退）。
    → {ok, instanceId, instStatus, instResult, taskId, result(AGREE/REFUSE/REDIRECTED/""), userid, finishTime, remark, openAtNode}"""
    tasks = inst.get("tasks") or []
    ops = inst.get("operation_records") or []
    node_ids = set(node_ids or [])
    done = [t for t in tasks if t.get("activity_id") in node_ids and (t.get("task_status") or "").upper() == "COMPLETED"
            and (t.get("task_result") or "").upper() in ("AGREE", "REFUSE")]
    done.sort(key=lambda t: str(t.get("finish_time") or ""))
    t = done[-1] if done else None
    best = None
    if t:
        uid = t.get("userid") or ""
        recs = [r for r in ops if (r.get("userid") or "") == uid and str(r.get("operation_type") or "").upper().startswith("EXECUTE_TASK")
                and str(r.get("date") or "")[:16] <= str(t.get("finish_time") or "")[:16]]
        recs.sort(key=lambda r: str(r.get("date") or ""))
        best = {"taskId": str(t.get("taskid") or t.get("task_id") or ""), "result": (t.get("task_result") or "").upper(), "userid": uid,
                "finishTime": str(t.get("finish_time") or ""), "remark": str(recs[-1].get("remark") or "") if recs else "", "at": str(t.get("finish_time") or "")}
    # 回退：流程正停在本节点时有人做了 REDIRECT_*（不看是谁——27725 实证回退人不是节点任务的持有人），之后在别的（更早）节点出现新任务
    for r in sorted(ops, key=lambda r: str(r.get("date") or "")):
        if not str(r.get("operation_type") or "").upper().startswith("REDIRECT"):
            continue
        d = str(r.get("date") or "")[:16]
        at_node = any(x.get("activity_id") in node_ids and str(x.get("create_time") or "")[:16] <= d
                      and ((x.get("task_status") or "").upper() != "COMPLETED" or str(x.get("finish_time") or "")[:16] >= d) for x in tasks)
        if not at_node:
            continue                     # 回退发生时流程不在本节点（更早节点的人退的）→ 不算本节点的动作
        back = [x for x in tasks if x.get("activity_id") not in node_ids and str(x.get("create_time") or "")[:16] >= d]
        if not back:
            continue                     # 只在本节点内转交/加签 → 不是回退
        cand = {"taskId": "redirect:%s:%s" % (d, r.get("userid") or ""), "result": "REDIRECTED", "userid": r.get("userid") or "",
                "finishTime": str(r.get("date") or ""), "remark": str(r.get("remark") or ""), "at": d}
        if best is None or cand["at"] >= best["at"][:16]:
            best = cand
    out = {"ok": True, "instanceId": iid, "instStatus": (inst.get("status") or "").upper(), "instResult": (inst.get("result") or "").lower(),
           "taskId": "", "result": "", "userid": "", "finishTime": "", "remark": "",
           "openAtNode": any(x.get("activity_id") in node_ids and (x.get("task_status") or "").upper() in _TASK_OPEN for x in tasks)}
    if best:
        out.update({k: best[k] for k in ("taskId", "result", "userid", "finishTime", "remark")})
    return out


def final_node_state(business_id, node_ids, process_code=None):
    """V2.584/586 OA 终审同步：某单在指定节点最近一次动作（同意/拒绝/回退）。只读、不抛。见 final_state_from_inst。"""
    if not configured():
        return {"ok": False, "msg": "未配置钉钉"}
    try:
        ak, sk = _conf()
        tok = _token(ak, sk)
        iid, inst = _find_inst(tok, business_id, process_code)
        if not inst:
            return {"ok": False, "msg": "未找到实例"}
        return final_state_from_inst(inst, node_ids, iid)
    except Exception as e:
        return {"ok": False, "msg": str(e)[:200]}


_UNAME = {}


def user_name(userid):
    """钉钉 userid → 姓名（评论区附件显示「谁传的」用）。进程内缓存；取不到回空串，不抛。"""
    uid = str(userid or "")
    if not uid:
        return ""
    if uid in _UNAME:
        return _UNAME[uid]
    nm = ""
    try:
        ak, sk = _conf()
        r = _oapi(_token(ak, sk), "topapi/v2/user/get", {"userid": uid})
        nm = ((r.get("result") or {}).get("name") or "") if r.get("errcode") == 0 else ""
    except Exception:
        nm = ""
    _UNAME[uid] = nm
    return nm
