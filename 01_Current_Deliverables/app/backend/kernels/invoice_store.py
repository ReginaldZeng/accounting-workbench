# [Change Log]
# Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家）
# Description: 发票管家数据层——11 张 inv_ 表（自带 MetaData，db.py 用 to_metadata 挂入）＋ 增删改查。
#   只用 SQLAlchemy Core，所有函数第一个参数是 engine；不许 import db / core（db.py 在 import 期间会导入本模块）。
#   约定：JSON 列一律"进出都是 Python 对象"（写入时 json.dumps，读出时 _row 自动 loads 并给默认值）；
#   金额列 Numeric(18,2)，读出转 float（保留 2 位）；时间 String(20) '%Y-%m-%d %H:%M:%S'；日期 String(10)。
# 审查修复（同日）：加列 inv_folder.attach_tries、inv_item.dup_at、inv_pair.qr_hash 及索引；money() 拒 NaN/无穷/超界，
#   JSON 列不落 NaN；文件状态加 failed/pulling/poison；台账「含作废」review=withvoid、拆分票合计按分摊额。
"""发票管家（Invoice Butler）持久层：票夹/文件/票据/留痕/后补池/手机配对/期初/税局清单/批次/销方。"""
import json
import math
import re
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from sqlalchemy import (MetaData, Table, Column, String, Integer, Text, Numeric, Index, UniqueConstraint,
                        select, insert, update, func, or_, and_, not_, case)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.exc import IntegrityError

md = MetaData()


def JT():
    # JSON/长文本：MySQL 的 TEXT 只有 64KB（V2.412 app_settings 静默丢档的教训），一律 LONGTEXT
    return Text().with_variant(LONGTEXT(), "mysql")


def MONEY():
    return Numeric(18, 2)


# ───────────────────────── 表定义（技术方案 §3） ─────────────────────────
# 索引长度：MariaDB utf8mb4 每字符 4 字节、单索引上限 3072 字节 → 所有索引列都是单列或短串，远低于上限。

FOLDER = Table(
    "inv_folder", md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("inst_id", String(80)),                    # 钉钉审批实例 ID；手工票夹为 NULL（唯一约束允许多个 NULL）
    Column("business_id", String(32)),                # 审批编号
    Column("template", String(80)), Column("title", String(200)),
    Column("applicant", String(50)), Column("applicant_uid", String(64)),
    Column("dept", String(200)), Column("company", String(120)),
    Column("amount", MONEY()),
    Column("payee_name", String(200)), Column("payee_bank", String(200)), Column("payee_account", String(64)),
    Column("reason", JT()),                           # 事由：纯文本（长），不是 JSON
    Column("erp_no", String(120)),
    Column("approval_status", String(20)), Column("approval_result", String(20)),
    Column("form_json", JT()),                        # 规范化表单快照 [{name,type,value}]
    Column("attach_status", String(16), default="none"),   # none/pending/running/done/failed
    Column("attach_msg", String(400)),
    # 拉附件被领取的次数（拉完清零）：进程在拉的时候被杀（超大/损坏附件），重启续跑到第 ATTACH_MAX_TRIES 次就标失败，不再死循环
    Column("attach_tries", Integer, default=0),
    Column("status", String(16), default="collecting"),    # collecting/submitted/approved/returned
    Column("source", String(16)),                     # scan/manual/later
    Column("created_by", String(50)), Column("created_at", String(20)), Column("updated_at", String(20)),
    # 方案外补充：最近打开人/时间——"我最近的票夹"要算上别人建、我打开过的
    Column("opened_by", String(50)), Column("opened_at", String(20)),
    Column("submitted_by", String(50)), Column("submitted_at", String(20)),
    Column("reviewed_by", String(50)), Column("reviewed_at", String(20)),
    Column("review_note", String(500)), Column("self_review", Integer, default=0),
    UniqueConstraint("inst_id", name="uq_inv_folder_inst"),
    Index("ix_inv_folder_biz", "business_id"),
    Index("ix_inv_folder_status", "status"),
    Index("ix_inv_folder_created", "created_by"),      # 收票工作台"我最近的票夹"按建/开人查
    Index("ix_inv_folder_opened", "opened_by"),
)

FILE = Table(
    "inv_file", md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("folder_id", Integer), Column("item_id", Integer),
    Column("role", String(16)),                       # original/paper/attachment/doc
    Column("origin", String(16)),                     # attachment/photo_field/camera/phone/upload/taxpack/later/scanner
    Column("name", String(255)), Column("mime", String(80)), Column("ext", String(10)),
    Column("size", Integer), Column("sha256", String(64)),
    # 相对 backend/inv_uploads/ 的路径，不存绝对路径（换机器/换目录不失效）
    Column("orig_path", String(400)), Column("preview_path", String(400)), Column("thumb_path", String(400)),
    Column("pages", Integer, default=1), Column("width", Integer), Column("height", Integer),
    Column("rotation", Integer, default=0),
    Column("dt_file_id", String(64)),
    # active/removed（人工移除）/failed（落盘或登记失败，可重传）/pulling（正在拉的钉钉附件占位）/poison（拉它时进程中断过，跳过）
    Column("status", String(12), default="active"),
    Column("created_by", String(50)), Column("created_at", String(20)),
    Index("ix_inv_file_folder", "folder_id"),
    Index("ix_inv_file_item", "item_id"),
    Index("ix_inv_file_sha", "sha256"),
)

ITEM = Table(
    "inv_item", md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("folder_id", Integer), Column("file_id", Integer), Column("page", Integer, default=0),
    Column("kind", String(12)),                       # invoice/receipt/other
    Column("origin", String(16)),
    Column("inv_type", String(16)), Column("type_label", String(60)), Column("qr_type", String(4)),
    Column("code", String(20)), Column("number", String(24)),
    Column("dup_key", String(64)),
    # 拿到这个查重键的时间（微秒）：谁先拿到号码谁是正主——不按行 id（照片先登记、号码后补的票不能反过来把已审的票挤成重复）
    Column("dup_at", String(26)),
    Column("issue_date", String(10)),
    Column("buyer_name", String(200)), Column("buyer_tax_id", String(32)),
    Column("seller_name", String(200)), Column("seller_tax_id", String(32)),
    Column("amount", MONEY()), Column("tax", MONEY()), Column("total", MONEY()),
    Column("tax_rate", String(40)), Column("category", String(80)),
    Column("lines_json", JT()), Column("check_code", String(32)), Column("remark", String(500)),
    Column("field_src_json", JT()), Column("pending_json", JT()),
    Column("proc_status", String(12), default="done"),   # done/pending/running/failed
    Column("proc_error", String(400)), Column("proc_tries", Integer, default=0),
    Column("paper", Integer, default=0),
    Column("flags_json", JT()),
    Column("deduct_suggest", String(12), default=""), Column("deduct_reason", String(200)),
    Column("deductible", Integer),                    # 会计判定：1 可抵/0 不可抵/NULL 未判
    Column("deduct_status", String(12), default=""),  # ''/marked/checked
    Column("verify", String(8), default=""),          # ''/green/red/yellow/gray
    Column("verify_at", String(20)), Column("verify_note", String(200)),
    Column("split", Integer, default=0), Column("alloc", MONEY()),
    Column("later_id", Integer),
    Column("review", String(12), default="draft"),    # draft/pending/approved/returned/void
    Column("review_by", String(50)), Column("review_at", String(20)),
    Column("review_note", String(500)), Column("self_review", Integer, default=0),
    Column("void_by", String(50)), Column("void_at", String(20)), Column("void_note", String(500)),
    # 方案外补充：票夹里"移除"是软删，查重/台账都不认 removed 的票，但行和图片留着可追
    Column("status", String(12), default="active"),  # active/removed
    Column("created_by", String(50)), Column("created_at", String(20)), Column("updated_at", String(20)),
    Index("ix_inv_item_folder", "folder_id"),
    Index("ix_inv_item_dup", "dup_key"),
    Index("ix_inv_item_seller", "seller_tax_id"),
    Index("ix_inv_item_proc", "proc_status"),
    Index("ix_inv_item_later", "later_id"),
    Index("ix_inv_item_review", "review"),
    Index("ix_inv_item_date", "issue_date"),
)

LOG = Table(
    "inv_log", md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("folder_id", Integer), Column("item_id", Integer), Column("later_id", Integer),
    Column("ts", String(20)), Column("user", String(50)), Column("action", String(40)),
    Column("detail", JT()),
    Index("ix_inv_log_folder", "folder_id"),
    Index("ix_inv_log_item", "item_id"),
    Index("ix_inv_log_later", "later_id"),
)

LATER = Table(
    "inv_later", md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("folder_id", Integer),
    Column("inst_id", String(80)), Column("business_id", String(32)), Column("template", String(80)),
    Column("applicant", String(50)), Column("applicant_uid", String(64)),
    Column("dept", String(200)), Column("company", String(120)),
    Column("payee_name", String(200)), Column("payee_bank", String(200)), Column("payee_account", String(64)),
    Column("pay_amount", MONEY()), Column("reason", JT()), Column("erp_no", String(120)),
    Column("inv_kind", String(12)),                   # special/normal/receipt
    Column("tax_rate", String(20)),
    Column("expect_date", String(10)), Column("expect_amount", MONEY()),
    Column("received_amount", MONEY(), default=0), Column("unregistered_amount", MONEY(), default=0),
    Column("receiver", String(50)), Column("receiver_uid", String(64)), Column("receiver_name", String(50)),
    Column("filed_by", String(50)), Column("filed_uid", String(64)), Column("filed_via", String(12)),
    Column("note", String(500)),
    Column("status", String(12), default="open"),    # open/partial/done/closed
    Column("closed_by", String(50)), Column("closed_at", String(20)), Column("close_note", String(500)),
    Column("last_remind_at", String(20)), Column("remind_count", Integer, default=0),
    Column("created_at", String(20)), Column("updated_at", String(20)),
    Index("ix_inv_later_folder", "folder_id"),
    Index("ix_inv_later_inst", "inst_id"),
    Index("ix_inv_later_status", "status"),
)

PAIR = Table(
    "inv_pair", md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # 只存 sha256，令牌明文不落库。未绑定时＝配对码令牌；绑定成功即换成新发的会话令牌（配对码当场作废）
    Column("token_hash", String(64), nullable=False),
    Column("user", String(50)), Column("created_at", String(20)),
    Column("bind_deadline", String(20)), Column("bound_at", String(20)), Column("session_expires", String(20)),
    Column("dt_userid", String(64)), Column("dt_name", String(50)), Column("device", String(200)),
    Column("revoked", Integer, default=0), Column("last_seen", String(20)),
    Column("qr_hash", String(64)),                    # 用过的配对码令牌 sha256：再拿它来绑定 → 明确告诉"已经用过了"
    UniqueConstraint("token_hash", name="uq_inv_pair_token"),
    Index("ix_inv_pair_user", "user"),
    Index("ix_inv_pair_qr", "qr_hash"),
)

DESK = Table(
    "inv_desk", md,
    Column("user", String(50), primary_key=True),
    Column("folder_id", Integer), Column("updated_at", String(20)),
)

OPENING = Table(
    "inv_opening", md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("dup_key", String(64)), Column("code", String(20)), Column("number", String(24)),
    Column("issue_date", String(10)), Column("total", MONEY()),
    Column("seller_name", String(200)), Column("buyer_name", String(200)),
    Column("ref", String(200)), Column("source", String(40)), Column("batch_id", Integer),
    Column("imported_by", String(50)), Column("imported_at", String(20)),
    Index("ix_inv_opening_dup", "dup_key"),
    Index("ix_inv_opening_batch", "batch_id"),
)

TAXLIST = Table(
    "inv_taxlist", md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("batch_id", Integer),
    Column("number", String(24)), Column("code", String(20)), Column("dup_key", String(64)),
    Column("issue_date", String(10)),
    Column("seller_tax_id", String(32)), Column("seller_name", String(200)),
    Column("buyer_tax_id", String(32)), Column("buyer_name", String(200)),
    Column("amount", MONEY()), Column("tax", MONEY()), Column("total", MONEY()),
    Column("status", String(40)), Column("inv_type", String(60)), Column("check_state", String(40)),
    Column("raw_json", JT()),
    Column("imported_by", String(50)), Column("imported_at", String(20)),
    Index("ix_inv_taxlist_batch", "batch_id"),
    Index("ix_inv_taxlist_dup", "dup_key"),
)

BATCH = Table(
    "inv_batch", md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("kind", String(16)),                       # taxlist/opening/deduct/taxpack
    Column("name", String(255)), Column("rows", Integer),
    Column("summary_json", JT()),
    Column("created_by", String(50)), Column("created_at", String(20)),
    Index("ix_inv_batch_kind", "kind"),
)

SELLER = Table(
    "inv_seller", md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tax_id", String(32), nullable=False), Column("name", String(200)),
    Column("first_seen", String(10)), Column("first_item_id", Integer),
    Column("check_date", String(10)), Column("check_channel", String(60)),
    Column("check_result", String(20), default="未查"),   # 未查/无记录/命中
    Column("check_note", String(500)), Column("checked_by", String(50)),
    Column("updated_at", String(20)),
    UniqueConstraint("tax_id", name="uq_inv_seller_tax"),
)

TABLES = tuple(md.tables.values())

# JSON 列（写入 dumps / 读出 loads）及读出时的默认值；reason 是长文本不在此列
JSON_DEFAULTS = {"form_json": list, "lines_json": list, "field_src_json": dict, "pending_json": list,
                 "flags_json": dict, "detail": lambda: None, "raw_json": dict, "summary_json": dict}
TEXT_COLS = {"reason"}
_ACTIVE_LATER = ("open", "partial")
_UNCHECKED = ("", "未查")
MONEY_LIMIT = Decimal("1e16")          # Numeric(18,2) 装得下的上限（不含）；超了 MariaDB 严格模式直接报错
ATTACH_MAX_TRIES = 3                   # 拉附件被中断这么多次就标失败，不再续跑
LEDGER_REVIEWS = ("approved", "withvoid", "void", "pending", "draft", "returned", "all")


# ───────────────────────── 通用工具 ─────────────────────────

def now_s():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_json(name, raw):
    fallback = JSON_DEFAULTS.get(name, lambda: None)
    if raw is None or raw == "":
        return fallback()
    try:
        v = json.loads(raw)
    except (TypeError, ValueError):
        return raw  # 老数据/手工改库留下的非 JSON 串：原样给出，不吞掉
    return fallback() if v is None else v


def _row(r):
    """行 → 普通 dict（列名 snake_case）：JSON 列转 Python 对象，Decimal 转 float(2 位)。"""
    if r is None:
        return None
    m = r._mapping if hasattr(r, "_mapping") else r
    out = {}
    for k, v in m.items():
        if k in JSON_DEFAULTS:
            v = _load_json(k, v)
        elif isinstance(v, Decimal):
            v = round(float(v), 2)
        out[k] = v
    return out


def money(v):
    """金额入参 → Decimal(2 位，四舍五入)；空 → None；乱码/NaN/无穷大/超出 ±1e16 → ValueError。"""
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip().replace(",", "").replace("，", "").replace("¥", "").replace("￥", "")
        if v == "":
            return None
    if isinstance(v, bool):
        raise ValueError("金额格式不对：%r" % (v,))
    try:
        d = Decimal(str(v))
        if not d.is_finite():
            raise ValueError
        d = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValueError("金额格式不对：%r" % (v,))
    if not d.is_finite() or abs(d) >= MONEY_LIMIT:
        raise ValueError("金额超出范围：%r" % (v,))
    return d


def _no_nan(v):
    # JSON 里的 NaN/Infinity：前端 JSON.parse 认不了、Starlette 回包直接 500 —— 一律换成 null
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, dict):
        return {k: _no_nan(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_no_nan(x) for x in v]
    return v


def _dumps(v):
    try:
        return json.dumps(v, ensure_ascii=False, default=str, allow_nan=False)
    except ValueError:
        return json.dumps(_no_nan(v), ensure_ascii=False, default=str, allow_nan=False)


def _f(v):
    # 汇总结果（可能是 Decimal/float/None）→ float
    return round(float(v), 2) if v is not None else 0.0


def _prep(table, cols):
    """按列类型整理入参：JSON 列 dumps、金额转 Decimal、字符串按列宽截断（MySQL 严格模式超长会报错）。"""
    out = {}
    for k, v in cols.items():
        col = table.c.get(k)
        if col is None:
            raise KeyError("%s 没有列 %s" % (table.name, k))
        t = col.type
        if k in JSON_DEFAULTS:
            v = None if v is None else _dumps(v)
        elif k in TEXT_COLS:
            if v is not None and not isinstance(v, str):
                v = _dumps(v)
        elif isinstance(t, Numeric):
            v = money(v)
        elif isinstance(t, String):
            if v is not None and not isinstance(v, str):
                v = str(v)
            if v is not None and t.length and len(v) > t.length:
                v = v[:t.length]
        elif isinstance(t, Integer) and isinstance(v, bool):
            v = int(v)
        out[k] = v
    return out


def _insert(e, table, cols):
    with e.begin() as cx:
        return cx.execute(insert(table).values(**_prep(table, cols))).inserted_primary_key[0]


def _get(e, table, id_):
    if id_ is None:
        return None
    with e.connect() as cx:
        return _row(cx.execute(select(table).where(table.c.id == id_)).first())


def _update(e, table, id_, cols, touch=True):
    cols = dict(cols)
    if touch and "updated_at" in table.c and "updated_at" not in cols:
        cols["updated_at"] = now_s()
    if not cols:
        return False
    with e.begin() as cx:
        return cx.execute(update(table).where(table.c.id == id_).values(**_prep(table, cols))).rowcount == 1


def _all(e, stmt):
    with e.connect() as cx:
        return [_row(r) for r in cx.execute(stmt)]


def _page(page, size, cap=200):
    try:
        size = int(size or 50)
    except (TypeError, ValueError):
        size = 50
    try:
        page = int(page or 1)
    except (TypeError, ValueError):
        page = 1
    size = min(cap, max(1, size))
    page = max(1, page)
    return page, size, (page - 1) * size


def _like(col, q):
    return col.contains(q, autoescape=True)


def _item_active(t=ITEM):
    # 未被移除的票（status 为空按有效处理，兼容手工补的行）
    return or_(t.c.status.is_(None), t.c.status != "removed")


def _not_void(t=ITEM):
    return func.coalesce(t.c.review, "") != "void"


def dup_key_of(number, code=""):
    """查重键（与识别内核同一口径，此处独立实现以免反向依赖）：
    数电票 20 位号码 → 'N:'+号码；老版票 代码+号码 → 'C:'+代码+':'+号码；其它（收据/缺号）→ ''。"""
    n = re.sub(r"\s+", "", str(number or ""))
    c = re.sub(r"\s+", "", str(code or ""))
    if re.fullmatch(r"\d{20}", n):
        return "N:" + n
    if c and n:
        return "C:" + c + ":" + n
    return ""


# ───────────────────────── 票夹 inv_folder ─────────────────────────

# normalize_instance 的键 → 列名
_APPROVAL_MAP = (("instId", "inst_id"), ("businessId", "business_id"), ("template", "template"), ("title", "title"),
                 ("applicant", "applicant"), ("applicantUid", "applicant_uid"), ("dept", "dept"),
                 ("company", "company"), ("amount", "amount"), ("payeeName", "payee_name"),
                 ("payeeBank", "payee_bank"), ("payeeAccount", "payee_account"), ("reason", "reason"),
                 ("erpNo", "erp_no"), ("approvalStatus", "approval_status"), ("approvalResult", "approval_result"),
                 ("form", "form_json"))


def folder_get(e, id_):
    return _get(e, FOLDER, id_)


def folder_by_inst(e, inst_id):
    if not inst_id:
        return None
    with e.connect() as cx:
        return _row(cx.execute(select(FOLDER).where(FOLDER.c.inst_id == inst_id)).first())


def folder_by_business(e, business_id):
    if not business_id:
        return None
    with e.connect() as cx:
        return _row(cx.execute(select(FOLDER).where(FOLDER.c.business_id == str(business_id))
                               .order_by(FOLDER.c.id.desc())).first())


def folder_upsert_from_approval(e, f, user, source="scan"):
    """按审批实例 upsert 票夹；已存在只刷新审批字段（f 里给了的键），绝不动 status/审核字段。返回 id。"""
    inst_id = (f or {}).get("instId")
    if not inst_id:
        raise ValueError("审批单缺少实例 ID")
    vals = {col: f[k] for k, col in _APPROVAL_MAP if k in f}
    vals["inst_id"] = inst_id
    ts = now_s()

    def _refresh():
        with e.begin() as cx:
            fid = cx.execute(select(FOLDER.c.id).where(FOLDER.c.inst_id == inst_id)).scalar()
            if fid is None:
                return None
            cx.execute(update(FOLDER).where(FOLDER.c.id == fid).values(**_prep(FOLDER, dict(vals, updated_at=ts))))
            return fid

    fid = _refresh()
    if fid is not None:
        return fid
    try:
        return _insert(e, FOLDER, dict(vals, status="collecting", source=source, attach_status="none",
                                       created_by=user, created_at=ts, updated_at=ts))
    except IntegrityError:
        # 两个人同时扫同一张单：后到的撞唯一约束，转为刷新
        return _refresh()


def folder_create_manual(e, title, amount, company, user):
    ts = now_s()
    return _insert(e, FOLDER, dict(inst_id=None, title=title or "手工票夹", amount=amount, company=company or "",
                                   status="collecting", source="manual", attach_status="none",
                                   created_by=user, created_at=ts, updated_at=ts))


def folder_update(e, id_, **cols):
    return _update(e, FOLDER, id_, cols)


def folders_recent(e, user, limit=10):
    """我建的或我最近打开的票夹；还在收票/被退回的排前面，其次按最近打开/创建时间。"""
    if not user:
        return []
    order_open = case((FOLDER.c.status.in_(("collecting", "returned")), 0), else_=1)
    stmt = (select(FOLDER).where(or_(FOLDER.c.created_by == user, FOLDER.c.opened_by == user))
            .order_by(order_open, func.coalesce(FOLDER.c.opened_at, FOLDER.c.created_at).desc(), FOLDER.c.id.desc())
            .limit(max(1, int(limit or 10))))
    return _all(e, stmt)


def folder_items(e, folder_id, include_removed=False):
    stmt = select(ITEM).where(ITEM.c.folder_id == folder_id)
    if not include_removed:
        stmt = stmt.where(_item_active())
    return _all(e, stmt.order_by(ITEM.c.id))


def items_of_folders(e, folder_ids, include_removed=False):
    """多个票夹的票一次取回 → {folder_id: [item...]}（审核队列/列表统计用）。"""
    ids = [i for i in (folder_ids or []) if i is not None]
    out = {i: [] for i in ids}
    if not ids:
        return out
    stmt = select(ITEM).where(ITEM.c.folder_id.in_(ids))
    if not include_removed:
        stmt = stmt.where(_item_active())
    for r in _all(e, stmt.order_by(ITEM.c.id)):
        out.setdefault(r["folder_id"], []).append(r)
    return out


# ───────────────────────── 文件 inv_file ─────────────────────────

def file_insert(e, **cols):
    cols.setdefault("created_at", now_s())
    cols.setdefault("status", "active")
    return _insert(e, FILE, cols)


def file_get(e, id_):
    return _get(e, FILE, id_)


def file_update(e, id_, **cols):
    return _update(e, FILE, id_, cols, touch=False)


def file_delete(e, id_):
    """硬删一行（只给"正在拉"占位行用：拉完就删，不是用户数据）。"""
    with e.begin() as cx:
        return cx.execute(FILE.delete().where(FILE.c.id == id_)).rowcount == 1


def _file_live(t=FILE):
    # 有效文件：status 空或 active（removed/failed/pulling/poison 都不算）
    return or_(t.c.status.is_(None), t.c.status == "active")


def files_of_folder(e, folder_id, include_removed=False):
    stmt = select(FILE).where(FILE.c.folder_id == folder_id)
    if not include_removed:
        stmt = stmt.where(_file_live())
    return _all(e, stmt.order_by(FILE.c.id))


def file_by_sha(e, folder_id, sha256):
    """同票夹里内容相同、已登记成功的有效文件（移除过的不算——移除后重新放进来要能再进；
    落盘/登记半路失败留下的行也不算——没挂上票，重传要能重新登记）。"""
    if not sha256:
        return None
    with e.connect() as cx:
        return _row(cx.execute(select(FILE).where(FILE.c.folder_id == folder_id, FILE.c.sha256 == sha256,
                                                  _file_live(), FILE.c.item_id.isnot(None))
                               .order_by(FILE.c.id)).first())


def file_by_dt(e, folder_id, dt_file_id):
    """同票夹里已拉过的钉钉附件：含人工移除的（刷新审批单时不能把移除的附件又拉回来）、
    正在拉/拉它时进程中断过的占位（pulling/poison，调用方据此跳过并报原因）；
    不含落盘/登记失败的（failed）和没挂上票的半截行（进程在登记前中断）——那些下次刷新要能重拉。"""
    if not dt_file_id:
        return None
    ok = or_(FILE.c.status.in_(("removed", "pulling", "poison")), and_(_file_live(), FILE.c.item_id.isnot(None)))
    with e.connect() as cx:
        return _row(cx.execute(select(FILE).where(FILE.c.folder_id == folder_id, FILE.c.dt_file_id == dt_file_id, ok)
                               .order_by(FILE.c.id)).first())


# ───────────────────────── 票据 inv_item ─────────────────────────

def item_insert(e, **cols):
    ts = now_s()
    cols.setdefault("created_at", ts)
    cols.setdefault("updated_at", ts)
    cols.setdefault("status", "active")
    cols.setdefault("review", "draft")
    return _insert(e, ITEM, cols)


def item_get(e, id_):
    return _get(e, ITEM, id_)


def items_get(e, ids):
    """按 id 一次取多张票 → {id: 行}（进票结果批量出视图用，免得一张一查）。"""
    ids = sorted({int(i) for i in (ids or []) if i is not None})
    out = {}
    for i in range(0, len(ids), 500):
        for r in _all(e, select(ITEM).where(ITEM.c.id.in_(ids[i:i + 500]))):
            out[r["id"]] = r
    return out


def item_update(e, id_, **cols):
    return _update(e, ITEM, id_, cols)


def items_by_dup(e, dup_key, exclude_id=None):
    """查重：同查重键的有效票（未作废、未移除），附所在票夹摘要 row['folder']={id,business_id,title,applicant,template,status}。"""
    if not dup_key:
        return []
    stmt = (select(ITEM, FOLDER.c.id.label("f__id"), FOLDER.c.business_id.label("f__business_id"),
                   FOLDER.c.title.label("f__title"), FOLDER.c.applicant.label("f__applicant"),
                   FOLDER.c.template.label("f__template"), FOLDER.c.status.label("f__status"))
            .select_from(ITEM.outerjoin(FOLDER, FOLDER.c.id == ITEM.c.folder_id))
            .where(ITEM.c.dup_key == dup_key, _item_active(), _not_void()))
    if exclude_id is not None:
        stmt = stmt.where(ITEM.c.id != exclude_id)
    return [_split_prefixed(r, "f__", "folder") for r in _all(e, stmt.order_by(ITEM.c.id))]


def _split_prefixed(row, prefix, key):
    sub = {k[len(prefix):]: row.pop(k) for k in list(row) if k.startswith(prefix)}
    row[key] = sub if sub.get("id") is not None else None
    return row


def items_pending_proc(e, limit=5):
    stmt = (select(ITEM).where(ITEM.c.proc_status == "pending", _item_active())
            .order_by(ITEM.c.id).limit(max(1, int(limit or 5))))
    return _all(e, stmt)


def item_claim(e, id_):
    """后台线程领取识别任务：pending → running 且重试次数 +1；被别人领走返回 False。"""
    with e.begin() as cx:
        return cx.execute(update(ITEM).where(ITEM.c.id == id_, ITEM.c.proc_status == "pending")
                          .values(proc_status="running", proc_tries=func.coalesce(ITEM.c.proc_tries, 0) + 1,
                                  updated_at=now_s())).rowcount == 1


def folder_claim_attach(e, id_):
    """后台线程领取拉附件任务：attach_status pending → running，领取次数 attach_tries +1；被别人领走返回 False。"""
    with e.begin() as cx:
        return cx.execute(update(FOLDER).where(FOLDER.c.id == id_, FOLDER.c.attach_status == "pending")
                          .values(attach_status="running", attach_tries=func.coalesce(FOLDER.c.attach_tries, 0) + 1,
                                  updated_at=now_s())).rowcount == 1


def folders_pending_attach(e, limit=5):
    stmt = select(FOLDER).where(FOLDER.c.attach_status == "pending").order_by(FOLDER.c.id).limit(max(1, int(limit or 5)))
    return _all(e, stmt)


ATTACH_GIVEUP_MSG = ("拉审批单附件时服务中断了 %d 次（可能某个附件过大或损坏），已停止自动拉取："
                     "请到钉钉下载附件核对后手工拖进来，或点「重新取审批」再试一次" % ATTACH_MAX_TRIES)


def reset_running(e):
    """启动时续跑：上次进程中断留下的 running 退回 pending。拉附件已被中断 ATTACH_MAX_TRIES 次的票夹不再续跑，
    标 failed 并写明原因（防一个会拖垮进程的附件让服务反复重启）。返回 {'items': n, 'folders': n, 'gaveUp': n}。"""
    with e.begin() as cx:
        n1 = cx.execute(update(ITEM).where(ITEM.c.proc_status == "running").values(proc_status="pending")).rowcount
        tries = func.coalesce(FOLDER.c.attach_tries, 0)
        n3 = cx.execute(update(FOLDER).where(FOLDER.c.attach_status == "running", tries >= ATTACH_MAX_TRIES)
                        .values(attach_status="failed", attach_msg=ATTACH_GIVEUP_MSG)).rowcount
        n2 = cx.execute(update(FOLDER).where(FOLDER.c.attach_status == "running")
                        .values(attach_status="pending")).rowcount
    return {"items": n1, "folders": n2, "gaveUp": n3}


def items_of_later(e, later_id, include_removed=False):
    stmt = select(ITEM).where(ITEM.c.later_id == later_id)
    if not include_removed:
        stmt = stmt.where(_item_active())
    return _all(e, stmt.order_by(ITEM.c.id))


# ───────────────────────── 期初 inv_opening ─────────────────────────

def opening_match(e, dup_key):
    if not dup_key:
        return None
    with e.connect() as cx:
        return _row(cx.execute(select(OPENING).where(OPENING.c.dup_key == dup_key).order_by(OPENING.c.id)).first())


def opening_insert_many(e, rows, batch_id, source, user):
    """期初底子批量入库：查重键已在库里或本批重复的跳过；缺查重键时按号码/代码现算，算不出的跳过。返回实际入库条数。"""
    ts = now_s()
    prepared, seen = [], set()
    for r in rows or []:
        key = r.get("dup_key") or dup_key_of(r.get("number"), r.get("code"))
        if not key or key in seen:
            continue
        seen.add(key)
        prepared.append(_prep(OPENING, dict(dup_key=key, code=r.get("code") or "", number=r.get("number") or "",
                                            issue_date=r.get("issue_date") or "", total=r.get("total"),
                                            seller_name=r.get("seller_name") or "", buyer_name=r.get("buyer_name") or "",
                                            ref=r.get("ref") or "", source=source or "", batch_id=batch_id,
                                            imported_by=user, imported_at=ts)))
    if not prepared:
        return 0
    inserted = 0
    with e.begin() as cx:
        for i in range(0, len(prepared), 500):
            chunk = prepared[i:i + 500]
            have = set(cx.execute(select(OPENING.c.dup_key)
                                  .where(OPENING.c.dup_key.in_([c["dup_key"] for c in chunk]))).scalars())
            fresh = [c for c in chunk if c["dup_key"] not in have]
            if fresh:
                cx.execute(insert(OPENING), fresh)
                inserted += len(fresh)
    return inserted


def opening_stats(e):
    """{'total': n, 'batches': [{batch_id, name, source, rows, imported_by, imported_at}]}（新批次在前）"""
    with e.connect() as cx:
        total = cx.execute(select(func.count()).select_from(OPENING)).scalar_one()
        grouped = cx.execute(select(OPENING.c.batch_id, func.count().label("rows"),
                                    func.max(OPENING.c.source).label("source"),
                                    func.max(OPENING.c.imported_by).label("imported_by"),
                                    func.max(OPENING.c.imported_at).label("imported_at"))
                             .group_by(OPENING.c.batch_id)
                             .order_by(func.max(OPENING.c.imported_at).desc())).mappings().all()
        ids = [g["batch_id"] for g in grouped if g["batch_id"] is not None]
        names = dict(cx.execute(select(BATCH.c.id, BATCH.c.name).where(BATCH.c.id.in_(ids))).all()) if ids else {}
    return {"total": total, "batches": [dict(g, name=names.get(g["batch_id"], "")) for g in grouped]}


# ───────────────────────── 留痕 inv_log ─────────────────────────

def log_add(e, user, action, folder_id=None, item_id=None, later_id=None, detail=None):
    return _insert(e, LOG, dict(folder_id=folder_id, item_id=item_id, later_id=later_id, ts=now_s(),
                                user=user or "", action=(action or "")[:40], detail=detail))


def logs_of(e, folder_id=None, later_id=None, item_id=None, limit=200, item_ids=None):
    """按票夹/后补单/票据取留痕（给了几个条件就"任一命中"），最新的在前。
    item_ids：再并上这些票自己的留痕（票夹详情要看到逐张票的动作，哪怕那条留痕没记票夹号）。"""
    conds = []
    if folder_id is not None:
        conds.append(LOG.c.folder_id == folder_id)
    if later_id is not None:
        conds.append(LOG.c.later_id == later_id)
    if item_id is not None:
        conds.append(LOG.c.item_id == item_id)
    ids = sorted({int(i) for i in (item_ids or []) if i is not None})
    if ids:
        conds.append(LOG.c.item_id.in_(ids[:1000]))
    stmt = select(LOG)
    if conds:
        stmt = stmt.where(or_(*conds))
    return _all(e, stmt.order_by(LOG.c.id.desc()).limit(max(1, int(limit or 200))))


# ───────────────────────── 当前票夹 inv_desk ─────────────────────────

def desk_get(e, user):
    if not user:
        return None
    with e.connect() as cx:
        return cx.execute(select(DESK.c.folder_id).where(DESK.c.user == user)).scalar()


def desk_set(e, user, folder_id):
    """设/清当前票夹；设的时候顺手记票夹"最近打开人"（供 folders_recent）。"""
    ts = now_s()
    with e.begin() as cx:
        if cx.execute(select(DESK.c.user).where(DESK.c.user == user)).first():
            cx.execute(update(DESK).where(DESK.c.user == user).values(folder_id=folder_id, updated_at=ts))
        else:
            cx.execute(insert(DESK).values(user=user, folder_id=folder_id, updated_at=ts))
        if folder_id is not None:
            cx.execute(update(FOLDER).where(FOLDER.c.id == folder_id).values(opened_by=user, opened_at=ts))


# ───────────────────────── 手机配对 inv_pair ─────────────────────────

def pair_create(e, user, token_hash, bind_deadline):
    """新建配对，同时吊销此人所有旧配对（一人同一时间只认一台手机）。返回 id。"""
    ts = now_s()
    with e.begin() as cx:
        cx.execute(update(PAIR).where(PAIR.c.user == user, func.coalesce(PAIR.c.revoked, 0) == 0).values(revoked=1))
        return cx.execute(insert(PAIR).values(**_prep(PAIR, dict(
            token_hash=token_hash, user=user, created_at=ts, bind_deadline=bind_deadline, revoked=0))))\
            .inserted_primary_key[0]


def pair_get(e, id_):
    return _get(e, PAIR, id_)


def pair_by_hash(e, token_hash):
    if not token_hash:
        return None
    with e.connect() as cx:
        return _row(cx.execute(select(PAIR).where(PAIR.c.token_hash == token_hash)).first())


def pair_by_qr_hash(e, qr_hash):
    """按"已用过的配对码"哈希找配对（绑定后配对码作废，再拿来绑定要能说清楚是"用过了"）。"""
    if not qr_hash:
        return None
    with e.connect() as cx:
        return _row(cx.execute(select(PAIR).where(PAIR.c.qr_hash == qr_hash).order_by(PAIR.c.id.desc())).first())


def pair_update(e, id_, **cols):
    return _update(e, PAIR, id_, cols, touch=False)


def pair_revoke_user(e, user):
    with e.begin() as cx:
        return cx.execute(update(PAIR).where(PAIR.c.user == user, func.coalesce(PAIR.c.revoked, 0) == 0)
                          .values(revoked=1)).rowcount


def pair_active_for_user(e, user, now):
    """此人最新一条仍有效的配对：未吊销，且（未绑定→未过绑定期限；已绑定→会话未过期）。now 为 now_s() 格式串。"""
    valid = and_(func.coalesce(PAIR.c.revoked, 0) == 0,
                 or_(and_(PAIR.c.bound_at.is_(None), PAIR.c.bind_deadline >= now),
                     and_(PAIR.c.bound_at.isnot(None), PAIR.c.session_expires >= now)))
    with e.connect() as cx:
        return _row(cx.execute(select(PAIR).where(PAIR.c.user == user, valid).order_by(PAIR.c.id.desc())).first())


# ───────────────────────── 发票后补池 inv_later ─────────────────────────

def later_insert(e, **cols):
    ts = now_s()
    cols.setdefault("status", "open")
    cols.setdefault("received_amount", 0)
    cols.setdefault("unregistered_amount", 0)
    cols.setdefault("remind_count", 0)
    cols.setdefault("created_at", ts)
    cols.setdefault("updated_at", ts)
    return _insert(e, LATER, cols)


def later_get(e, id_):
    return _get(e, LATER, id_)


def later_update(e, id_, **cols):
    return _update(e, LATER, id_, cols)


def _days_between(a, b):
    """b - a 的天数；任一不是合法日期 → None。"""
    try:
        return (date.fromisoformat(str(b)[:10]) - date.fromisoformat(str(a)[:10])).days
    except (TypeError, ValueError):
        return None


def _later_decorate(row, today):
    d = _days_between(today, row.get("expect_date"))
    row["days_left"] = d
    row["overdue"] = bool(d is not None and d < 0 and row.get("status") in _ACTIVE_LATER)
    return row


def later_list(e, scope_user=None, status=None, q=None, page=1, size=50, today=None):
    """后补池列表 → (total, rows)。scope_user：接收人或代填人是他；status：open/partial/done/closed，
    'overdue'＝未收齐且预计日早于今天，None/'all'＝全部；q 搜收款方/审批编号/申请人/事由/ERP 单号。
    每行附 days_left（预计日−今天）与 overdue。未收齐的排前、按预计日由近到远。"""
    today = today or date.today().isoformat()
    conds = []
    if scope_user:
        conds.append(or_(LATER.c.receiver == scope_user, LATER.c.filed_by == scope_user))
    if status == "overdue":
        conds += [LATER.c.status.in_(_ACTIVE_LATER), LATER.c.expect_date.isnot(None),
                  LATER.c.expect_date != "", LATER.c.expect_date < today]
    elif status and status != "all":
        conds.append(LATER.c.status == status)
    q = (q or "").strip()
    if q:
        conds.append(or_(_like(LATER.c.payee_name, q), _like(LATER.c.business_id, q), _like(LATER.c.applicant, q),
                         _like(LATER.c.reason, q), _like(LATER.c.erp_no, q)))
    page, size, off = _page(page, size, cap=500)
    active_first = case((LATER.c.status.in_(_ACTIVE_LATER), 0), else_=1)
    no_date_last = case((func.coalesce(LATER.c.expect_date, "") == "", 1), else_=0)
    with e.connect() as cx:
        total = cx.execute(select(func.count()).select_from(LATER).where(*conds)).scalar_one()
        rows = [_later_decorate(_row(r), today) for r in cx.execute(
            select(LATER).where(*conds).order_by(active_first, no_date_last, LATER.c.expect_date, LATER.c.id.desc())
            .offset(off).limit(size))]
    return total, rows


def later_open_by_inst(e, inst_id):
    """这张审批单上未收齐的后补单（最新一条）。"""
    if not inst_id:
        return None
    with e.connect() as cx:
        return _row(cx.execute(select(LATER).where(LATER.c.inst_id == inst_id, LATER.c.status.in_(_ACTIVE_LATER))
                               .order_by(LATER.c.id.desc())).first())


def later_open_for_folders(e, folders):
    """一批票夹各自"未收齐的后补单"（与逐个 later_open_by_inst → 按 folder_id 兜底同一口径）→ {folder_id: 行或 None}。
    一条查询取完（收票工作台轮询/审核队列列表用，免得每个票夹查一两次）。"""
    folders = [f for f in (folders or []) if f and f.get("id") is not None]
    out = {f["id"]: None for f in folders}
    if not folders:
        return out
    insts = sorted({f["inst_id"] for f in folders if f.get("inst_id")})
    fids = [f["id"] for f in folders]
    conds = [LATER.c.folder_id.in_(fids)]
    if insts:
        conds.append(LATER.c.inst_id.in_(insts))
    rows = _all(e, select(LATER).where(LATER.c.status.in_(_ACTIVE_LATER), or_(*conds)).order_by(LATER.c.id.desc()))
    for f in folders:
        hit = next((r for r in rows if f.get("inst_id") and r.get("inst_id") == f["inst_id"]), None)
        out[f["id"]] = hit or next((r for r in rows if r.get("folder_id") == f["id"]), None)
    return out


def laters_by_inst(e, inst_id):
    if not inst_id:
        return []
    return _all(e, select(LATER).where(LATER.c.inst_id == inst_id).order_by(LATER.c.id.desc()))


def laters_due_for_remind(e, today, before_days, every_days):
    """今天该催的后补单（未收齐的）：
    ① 快到期：0 ≤ 预计日−今天 ≤ before_days，且进入这个窗口后还没提醒过（窗口内只提醒一次，对应确认书 Q9"预计日前 3 天提醒一次"）；
    ② 已超期：从没提醒过，或距上次提醒 ≥ every_days 天。
    每行附 days_left、remind_reason（'soon'/'overdue'）。"""
    before_days = int(before_days if before_days is not None else 3)
    every_days = max(1, int(every_days or 7))
    rows = _all(e, select(LATER).where(LATER.c.status.in_(_ACTIVE_LATER)).order_by(LATER.c.id))
    out = []
    for r in rows:
        left = _days_between(today, r.get("expect_date"))
        if left is None:
            continue
        last = (r.get("last_remind_at") or "")[:10]
        since = _days_between(last, today) if last else None
        if left < 0:
            if since is None or since >= every_days:
                out.append(dict(r, days_left=left, remind_reason="overdue"))
        elif before_days >= 0 and left <= before_days:
            # 窗口起点＝预计日−before_days；起点当天及以后提醒过（含手动"催一下"）→ 本窗口不再提醒
            start = (date.fromisoformat(r["expect_date"][:10]) - timedelta(days=before_days)).isoformat()
            if not last or last < start:
                out.append(dict(r, days_left=left, remind_reason="soon"))
    return out


# ───────────────────────── 台账 / 审核队列 ─────────────────────────

_LEDGER_FOLDER_COLS = ("id", "business_id", "title", "applicant", "template", "dept", "payee_name")


def _split_share(t=ITEM):
    """拆分票算进合计的份额：拆分且填了分摊额 → 分摊额；否则价税合计（一张票拆给几张单，合计里只算各自那份）。"""
    return case((and_(func.coalesce(t.c.split, 0) == 1, t.c.alloc.isnot(None)), t.c.alloc), else_=t.c.total)


def _split_scaled(col, t=ITEM):
    # 拆分票的金额/税额按 分摊额÷价税合计 同比例折算（合计为 0 或没填时按原值）
    return case((and_(func.coalesce(t.c.split, 0) == 1, t.c.alloc.isnot(None), t.c.total.isnot(None), t.c.total != 0),
                 col * t.c.alloc / t.c.total), else_=col)


def ledger_query(e, filters, page=1, size=50):
    """发票台账 → (total, sums{amount,tax,total}, rows)。
    filters 键：q（号码/销方/申请人/审批编号/票夹标题）、date_from/date_to（开票日期）、inv_type、verify（''或'unset'＝未验）、
    deduct（yes/no/unset/marked/checked）、seller（销方名称或税号片段）、kind、
    review（默认 approved；'withvoid'＝已审核＋已作废（页面「含作废」）；'all'＝不限，仅内部用）。
    也认 camelCase 别名 from/to/invType。移除的票永不出现；合计不含已作废（除非就是筛作废）；
    拆分票合计只算本单分摊额（金额/税额同比例折算），不按票面重复累加。
    rows 每行附 folder={id,business_id,title,applicant,template,dept,payee_name}；按开票日期倒序、id 倒序。"""
    conds, review = _ledger_conds(filters)
    src = ITEM.outerjoin(FOLDER, FOLDER.c.id == ITEM.c.folder_id)
    sum_conds = list(conds) if review == "void" else conds + [_not_void()]
    page, size, off = _page(page, size, cap=500)
    cols = [FOLDER.c[c].label("f__" + c) for c in _LEDGER_FOLDER_COLS]
    with e.connect() as cx:
        total = cx.execute(select(func.count()).select_from(src).where(*conds)).scalar_one()
        s_amount, s_tax, s_total = cx.execute(select(func.sum(_split_scaled(ITEM.c.amount)),
                                                     func.sum(_split_scaled(ITEM.c.tax)), func.sum(_split_share()))
                                              .select_from(src).where(*sum_conds)).one()
        rows = [_split_prefixed(_row(r), "f__", "folder") for r in cx.execute(
            select(ITEM, *cols).select_from(src).where(*conds)
            .order_by(ITEM.c.issue_date.desc(), ITEM.c.id.desc()).offset(off).limit(size))]
    return total, {"amount": _f(s_amount), "tax": _f(s_tax), "total": _f(s_total)}, rows


def ledger_iter(e, filters, cap=100000, chunk=500):
    """台账导出用：筛选、排序同 ledger_query，逐批流式吐行（生成器）——不取 lines/fieldSrc/flags/pending 这几列 JSON
    大字段、不算总数和合计，最多 cap 行；每行附 folder={...} 与 share（拆分票＝分摊额，否则价税合计，导出合计按它加）。
    导出时边读边写 Excel，不用先把几万行连同 JSON 全装进内存（单进程只有 ~2G 可用）。"""
    conds, _review = _ledger_conds(filters)
    src = ITEM.outerjoin(FOLDER, FOLDER.c.id == ITEM.c.folder_id)
    cols = [c for c in ITEM.c if c.name not in JSON_DEFAULTS] + \
        [FOLDER.c[c].label("f__" + c) for c in _LEDGER_FOLDER_COLS]
    stmt = (select(*cols).select_from(src).where(*conds)
            .order_by(ITEM.c.issue_date.desc(), ITEM.c.id.desc()).limit(max(1, int(cap or 1))))
    with e.connect() as cx:
        res = cx.execution_options(stream_results=True, yield_per=chunk).execute(stmt)
        for part in res.partitions(chunk):
            for r in part:
                row = _split_prefixed(_row(r), "f__", "folder")
                row["share"] = row.get("alloc") if (row.get("split") and row.get("alloc") is not None) else row.get("total")
                yield row


def _ledger_conds(filters):
    """台账筛选条件 → (conds, review)。ledger_query / ledger_iter 共用。"""
    f = dict(filters or {})
    g = lambda *ks: next((str(f[k]).strip() for k in ks if f.get(k) not in (None, "")), "")
    conds = [_item_active()]
    review = g("review") or "approved"
    if review == "withvoid":
        conds.append(ITEM.c.review.in_(("approved", "void")))
    elif review != "all":
        conds.append(ITEM.c.review == review)
    q = g("q")
    if q:
        conds.append(or_(_like(ITEM.c.number, q), _like(ITEM.c.code, q), _like(ITEM.c.seller_name, q),
                         _like(ITEM.c.seller_tax_id, q), _like(FOLDER.c.applicant, q),
                         _like(FOLDER.c.business_id, q), _like(FOLDER.c.title, q)))
    d1, d2 = g("date_from", "from", "dateFrom"), g("date_to", "to", "dateTo")
    if d1:
        conds.append(ITEM.c.issue_date >= d1)
    if d2:
        conds.append(ITEM.c.issue_date <= d2)
    it = g("inv_type", "invType")
    if it:
        conds.append(ITEM.c.inv_type == it)
    ver = g("verify")  # 空串＝不筛；'unset'/'none'＝筛"还没验"
    if ver in ("unset", "none"):
        conds.append(func.coalesce(ITEM.c.verify, "") == "")
    elif ver:
        conds.append(ITEM.c.verify == ver)
    dd = g("deduct")
    if dd == "yes":
        conds.append(ITEM.c.deductible == 1)
    elif dd == "no":
        conds.append(ITEM.c.deductible == 0)
    elif dd == "unset":
        conds.append(ITEM.c.deductible.is_(None))
    elif dd == "marked":
        conds.append(func.coalesce(ITEM.c.deduct_status, "").in_(("marked", "checked")))
    elif dd == "checked":
        conds.append(ITEM.c.deduct_status == "checked")
    s = g("seller")
    if s:
        conds.append(or_(_like(ITEM.c.seller_name, s), _like(ITEM.c.seller_tax_id, s)))
    k = g("kind")
    if k == "all":
        pass
    elif k:
        conds.append(ITEM.c.kind == k)
    else:
        # 台账默认只列发票和收据：对账单、支付截图等附件只在票夹里看，不进台账
        conds.append(ITEM.c.kind.in_(("invoice", "receipt")))
    return conds, review


def _item_exists(*extra):
    it = ITEM.alias("it")
    return select(it.c.id).where(it.c.folder_id == FOLDER.c.id,
                                 or_(it.c.status.is_(None), it.c.status != "removed"), *[x(it) for x in extra]).exists()


def _counts(items):
    c = {"pending_items": 0, "invoices": 0, "dup": 0, "unchecked": 0, "others": 0}
    for it in items:
        if (it.get("review") or "") == "void":
            continue
        if it.get("review") == "pending":
            c["pending_items"] += 1
        if it.get("kind") == "invoice":
            c["invoices"] += 1
        else:
            c["others"] += 1
        if isinstance(it.get("flags_json"), dict) and it["flags_json"].get("dup"):
            c["dup"] += 1
        if it.get("pending_json"):
            c["unchecked"] += 1
    return c


def audit_queue(e, tab="pending", q=None, page=1, size=30):
    """审核队列 → (total, rows)。
    pending＝有待审票（review=pending）或票夹已提交；returned＝票夹被退回；done＝票全部通过/作废且至少一张通过。
    每行＝票夹 dict ＋ counts{pending_items, invoices, dup, unchecked, others}（统计不含作废票）。
    排序：pending 先到先审（提交时间正序）；returned/done 最近的在前。"""
    if tab == "returned":
        conds = [FOLDER.c.status == "returned"]
        order = [func.coalesce(FOLDER.c.reviewed_at, FOLDER.c.updated_at).desc(), FOLDER.c.id.desc()]
    elif tab == "done":
        conds = [not_(_item_exists(lambda t: func.coalesce(t.c.review, "").not_in(("approved", "void")))),
                 _item_exists(lambda t: t.c.review == "approved")]
        order = [func.coalesce(FOLDER.c.reviewed_at, FOLDER.c.updated_at).desc(), FOLDER.c.id.desc()]
    else:
        conds = [or_(FOLDER.c.status == "submitted", _item_exists(lambda t: t.c.review == "pending"))]
        order = [func.coalesce(FOLDER.c.submitted_at, FOLDER.c.updated_at), FOLDER.c.id]
    q = (q or "").strip()
    if q:
        conds.append(or_(_like(FOLDER.c.business_id, q), _like(FOLDER.c.title, q), _like(FOLDER.c.applicant, q),
                         _like(FOLDER.c.payee_name, q), _like(FOLDER.c.company, q),
                         _item_exists(lambda t: _like(t.c.number, q))))
    page, size, off = _page(page, size, cap=200)
    with e.connect() as cx:
        total = cx.execute(select(func.count()).select_from(FOLDER).where(*conds)).scalar_one()
        rows = [_row(r) for r in cx.execute(select(FOLDER).where(*conds).order_by(*order).offset(off).limit(size))]
    by = items_of_folders(e, [r["id"] for r in rows])
    for r in rows:
        r["counts"] = _counts(by.get(r["id"], []))
    return total, rows


# ───────────────────────── 批次 / 税局清单 ─────────────────────────

def batch_insert(e, kind, name, rows, summary, user):
    return _insert(e, BATCH, dict(kind=kind, name=name or "", rows=int(rows or 0), summary_json=summary,
                                  created_by=user, created_at=now_s()))


def batch_get(e, id_):
    return _get(e, BATCH, id_)


def batch_update(e, id_, **cols):
    return _update(e, BATCH, id_, cols, touch=False)


def batches_list(e, kind=None, limit=20):
    stmt = select(BATCH)
    if kind:
        stmt = stmt.where(BATCH.c.kind == kind)
    return _all(e, stmt.order_by(BATCH.c.id.desc()).limit(max(1, int(limit or 20))))


_TAXLIST_KEYS = ("number", "code", "issue_date", "seller_tax_id", "seller_name", "buyer_tax_id", "buyer_name",
                 "amount", "tax", "total", "status", "inv_type", "check_state")


def taxlist_insert_many(e, batch_id, rows, user):
    """税局清单行入库（行 dict 用列名键，原行放 raw 键 → raw_json）；查重键缺省时按号码/代码现算。返回条数。"""
    ts = now_s()
    prepared = []
    for r in rows or []:
        v = {k: r.get(k) for k in _TAXLIST_KEYS}  # 键要齐：批量插入每行列集必须一致
        v["dup_key"] = r.get("dup_key") or dup_key_of(r.get("number"), r.get("code"))
        v.update(batch_id=batch_id, raw_json=r.get("raw"), imported_by=user, imported_at=ts)
        prepared.append(_prep(TAXLIST, v))
    if not prepared:
        return 0
    with e.begin() as cx:
        for i in range(0, len(prepared), 500):
            cx.execute(insert(TAXLIST), prepared[i:i + 500])
    return len(prepared)


def taxlist_rows(e, batch_id):
    return _all(e, select(TAXLIST).where(TAXLIST.c.batch_id == batch_id).order_by(TAXLIST.c.id))


def taxlist_by_dup(e, batch_id, dup_key):
    """某批清单里同查重键的行（list；同号可能出现多行，如正常＋作废两条状态）。"""
    if not dup_key:
        return []
    return _all(e, select(TAXLIST).where(TAXLIST.c.batch_id == batch_id, TAXLIST.c.dup_key == dup_key)
                .order_by(TAXLIST.c.id))


# ───────────────────────── 销方档案 inv_seller ─────────────────────────

def seller_seen(e, tax_id, name, date_s, item_id):
    """登记见到的销方：新税号就建档（返回 True）；老税号若这次日期更早则改首见日期/首见票据（返回 False）。"""
    tax_id = (tax_id or "").strip()
    if not tax_id:
        return False
    ts = now_s()
    try:
        _insert(e, SELLER, dict(tax_id=tax_id, name=name or "", first_seen=date_s or "", first_item_id=item_id,
                                check_result="未查", updated_at=ts))
        return True
    except IntegrityError:
        pass
    with e.begin() as cx:
        cur = cx.execute(select(SELLER).where(SELLER.c.tax_id == tax_id)).mappings().first()
        if not cur:
            return False
        vals = {}
        if date_s and (not cur["first_seen"] or date_s < cur["first_seen"]):
            vals.update(first_seen=date_s, first_item_id=item_id)
        if name and not cur["name"]:
            vals["name"] = name
        if vals:
            vals["updated_at"] = ts
            cx.execute(update(SELLER).where(SELLER.c.tax_id == tax_id).values(**_prep(SELLER, vals)))
    return False


def sellers_query(e, month=None, status=None):
    """销方档案列表。month='YYYY-MM' 按首见月份筛；status：unchecked(未查)/checked(已查)/hit(命中)，或直接给结果中文。
    每行附 items（已审核发票张数）与 total（价税合计；拆分票只算各单分摊额，不重复累加票面）。首见日期倒序。"""
    agg = (select(ITEM.c.seller_tax_id.label("tid"), func.count().label("n_items"), func.sum(_split_share()).label("n_total"))
           .where(ITEM.c.kind == "invoice", ITEM.c.review == "approved", _item_active())
           .group_by(ITEM.c.seller_tax_id).subquery())
    stmt = select(SELLER, agg.c.n_items, agg.c.n_total).select_from(SELLER.outerjoin(agg, agg.c.tid == SELLER.c.tax_id))
    if month:
        stmt = stmt.where(SELLER.c.first_seen.like(str(month)[:7] + "%"))
    res = func.coalesce(SELLER.c.check_result, "")
    if status == "unchecked":
        stmt = stmt.where(res.in_(_UNCHECKED))
    elif status == "checked":
        stmt = stmt.where(res.not_in(_UNCHECKED))
    elif status == "hit":
        stmt = stmt.where(res == "命中")
    elif status:
        stmt = stmt.where(res == status)
    out = _all(e, stmt.order_by(SELLER.c.first_seen.desc(), SELLER.c.id.desc()))
    for r in out:
        r["items"] = int(r.pop("n_items") or 0)
        r["total"] = _f(r.pop("n_total"))
    return out


def seller_get(e, tax_id):
    with e.connect() as cx:
        return _row(cx.execute(select(SELLER).where(SELLER.c.tax_id == tax_id)).first())


def seller_check(e, tax_id, date_s, channel, result, note, user):
    """填回人工核查结果；档案里没有这个税号就先建档。返回更新后的档案行。"""
    tax_id = (tax_id or "").strip()
    if not tax_id:
        raise ValueError("缺少销方税号")
    if not seller_get(e, tax_id):
        seller_seen(e, tax_id, "", "", None)
    vals = dict(check_date=date_s or "", check_channel=channel or "", check_result=result or "未查",
                check_note=note or "", checked_by=user or "", updated_at=now_s())
    with e.begin() as cx:
        cx.execute(update(SELLER).where(SELLER.c.tax_id == tax_id).values(**_prep(SELLER, vals)))
    return seller_get(e, tax_id)
