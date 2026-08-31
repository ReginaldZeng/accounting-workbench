# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-07-04 | Author: Claude / c | Version: V2.0(阶段1)
# Description: 数据层（多人服务器版基础）。SQLAlchemy，一套代码两种库：
#              本地开发/测试 = SQLite（默认，零配置）；服务器 = MySQL（DB_URL 环境变量指过去）。
#              存共享状态：认领(claims) / 账户覆盖(overrides) / 审计留痕(audit_log)。
#              列名用 ASCII（MySQL 稳），对外仍返回中文键的 dict（app 层不用改口径）。
#              DB_URL 例：
#                本地  sqlite:///.../sample_data/workbench.db  （默认）
#                服务器 mysql+pymysql://fw_app:PWD@127.0.0.1:3306/finance_workbench?charset=utf8mb4
import os
import re
import gzip
import json
import hashlib
import secrets
import datetime

from sqlalchemy import (create_engine, MetaData, Table, Column, String, Text, Integer, Float,
                        LargeBinary, UniqueConstraint, select, insert, update, delete)
from sqlalchemy.dialects.mysql import LONGTEXT   # v 字段大月留档可达数百KB，MySQL 的 Text 仅 64KB

BASE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT = "sqlite:///" + os.path.join(BASE, "sample_data", "workbench.db").replace("\\", "/")
DB_URL = os.environ.get("DB_URL", _DEFAULT)
_engine = create_engine(DB_URL, future=True, pool_pre_ping=True)
_md = MetaData()

claims = Table(
    "claims", _md,
    Column("item_key", String(64), primary_key=True),
    Column("status", String(20)),
    Column("operator", String(50)),
    Column("ts", String(20)),
    Column("note", Text),
)
overrides = Table(
    "account_overrides", _md,
    Column("acct", String(64), primary_key=True),
    Column("data", Text),                      # JSON: {失效, 稽核方案}
)
audit_log = Table(
    "audit_log", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", String(20)),
    Column("operator", String(50)),
    Column("action", String(40)),
    Column("target", String(160)),
    Column("detail", Text),
)
users = Table(
    "users", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(50), unique=True),     # 登录名=姓名
    Column("grp", String(20)),                    # 核算组 / BP组 / 法务 / 外部协作 / 管理
    Column("post", String(40)),                   # 岗位（识别标签，如 出纳/总账会计/财务BP，不影响权限）
    Column("role", String(20)),                   # admin / normal
    Column("pwd", String(200)),                   # pbkdf2: salt$hash
    Column("active", Integer),                    # 1 启用 / 0 禁用
    Column("created_at", String(20)),
    Column("perms", Text),                        # JSON 细粒度权限 {能力:bool}（NULL=全开，向后兼容）
    Column("must_change_pwd", Integer),           # V2.330 首登强制改密：新建/重置=1，本人改密后=0（老库 _ensure_user_columns 补列）
)
sessions = Table(
    "sessions", _md,
    Column("token", String(64), primary_key=True),
    Column("name", String(50)),
    Column("created_at", String(20)),
)
tax_rates = Table(                  # 物流计提·税率维表（供应商×费用类型→税率，不含税口径：税额=未税×税率）
    "logistics_tax_rates", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("supplier", String(120)),   # 供应商全名（与计提表/金蝶档案一致）
    Column("fee_type", String(60)),    # 费用类型 = 计提表费用归属列名（空字符串 = 该供应商默认税率）
    Column("rate", String(20)),        # 税率，存字符串避免浮点误差（如 "0.09"）
    Column("updated_by", String(50)),
    Column("updated_at", String(20)),
)
post_log = Table(                   # 物流计提·录入台账（草稿在金蝶列表查询里不可见，防重录靠这本账）
    "logistics_post_log", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("year", Integer),
    Column("period", Integer),
    Column("zhaiyao", String(300)),    # 凭证摘要（同年同期同摘要 = 同一张，天然幂等键）
    Column("billno", String(40)),      # 金蝶单据编号
    Column("kd_id", String(40)),       # 金蝶内码（View 回查草稿是否还在用）
    Column("vno", String(40)),         # 凭证字号（记-N，保存即分配）
    Column("operator", String(50)),
    Column("ts", String(20)),
)
fx_post_log = Table(                # 汇率录入·写金蝶台账（防重录 + 撤销；BD_Rate 草稿/提交态 ExecuteBillQuery 可见，但仍留本账为准）
    "fx_post_log", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("year", Integer),           # 结账年
    Column("month", Integer),          # 结账月
    Column("org", String(20)),         # 组织编码（101/107…）
    Column("pair", String(60)),        # 币对展示（美元→人民币）
    Column("from_code", String(20)),
    Column("to_code", String(20)),
    Column("rate", String(20)),
    Column("beg_date", String(20)),
    Column("end_date", String(20)),
    Column("kind", String(20)),        # month_end / next_range
    Column("kd_id", String(40)),       # 金蝶内码 FRATEID
    Column("operator", String(50)),
    Column("ts", String(20)),
)
balance_notes = Table(              # 余额调节·未达账项原因（会计填，供领导核查；按 期间×账号 一条）
    "balance_notes", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("year", Integer),
    Column("period", Integer),
    Column("acct", String(64)),        # 账号数字键
    Column("note", Text),              # 未达原因说明
    Column("operator", String(50)),    # 填写人（登录名，服务端认）
    Column("ts", String(20)),
)
portal_tools = Table(               # 门户工具卡片（门户管理 CMS 数据源）
    "portal_tools", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lane", String(20)),      # accounting / bp / legal
    Column("name", String(80)),
    Column("status", String(10)),    # V2.329 四档（键沿用不迁移）：ok=已上线(闪烁绿灯) / par=人工并行 / beta=开发中 / soon=敬请期待
    Column("icon", String(8)),
    Column("descr", Text),           # 概述（避开 SQL 保留字 desc）
    Column("gen", Text),             # 通用技能 JSON 数组
    Column("ai", Text),              # AI 技能 JSON 数组
    Column("sort", Integer),
    Column("mods", Text),            # V2.331 自动联动：核算导航模块 key 列表(JSON)。非空=状态由模块进度推导（app._portal_autolink）
)
periods = Table(                    # 月结批次：一个 (数据源,年,期) = 一期。封存 = 这个月封账，之后只读。
    "periods", _md,                 # 没有"开启"动作——上一期封存后，下一期天然就是进行中（同金蝶）。
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source", String(20)),
    Column("year", Integer),
    Column("period", Integer),
    Column("status", String(10)),        # open=进行中 / closed=已封存
    Column("closed_by", String(50)),
    Column("closed_at", String(20)),
    Column("note", Text),                # 封存说明（看板未全绿而强制封存时必填理由）
    Column("kd_synced_at", String(20)),  # 封存时的金蝶取数时点：底稿能追溯到"当时那一版数据"
    UniqueConstraint("source", "year", "period", name="uq_period"),
)
period_snapshots = Table(           # 封存快照：把该期结果整体拍照存死，之后重开直接读它、不再碰金蝶
    "period_snapshots", _md,        # → 底稿可复现：审计问"你当时凭什么这么调节"，调得出当时那张表
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source", String(20)),
    Column("year", Integer),
    Column("period", Integer),
    Column("kind", String(30)),      # reconcile=逐笔稽核 / balance_adjust=余额调节+钩稽 / checklist=看板
    Column("payload", LargeBinary(2 ** 32 - 1)),   # gzip(JSON)；MySQL→LONGBLOB（TEXT只有64K，装不下600+笔）
    Column("ts", String(20)),
    UniqueConstraint("source", "year", "period", "kind", name="uq_snap"),
)
period_inputs = Table(              # 按期间存的【输入数据】：上传一次的银行流水 / 取数一次的金蝶数据。
    "period_inputs", _md,           # 重启不丢、全员共享；只有上传/刷新才更新，页面进入直接读、不再重解析重取数。
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source", String(20)),
    Column("year", Integer),
    Column("period", Integer),
    Column("kind", String(40)),      # bank=银行流水 / kd:gl_voucher:1002 / kd:gl_balance / kd:gl_subjects …
    Column("payload", LargeBinary(2 ** 32 - 1)),   # gzip(JSON)：{rows:[...], manifest:[...], ...}
    Column("meta", Text),            # JSON：来源描述/笔数/上传人 等，摘要展示用（不含大数据）
    Column("updated_by", String(50)),
    Column("updated_at", String(20)),
    UniqueConstraint("source", "year", "period", "kind", name="uq_pinput"),
)
app_settings = Table(               # 通用应用设置（键值对，存 JSON）：导航模块上线开关等全员生效的全局配置
    "app_settings", _md,
    Column("k", String(60), primary_key=True),
    # v 存 JSON；临时工「按期留档」的结论快照压缩后大月可达数百KB——MySQL 的 Text 仅 64KB 会拒收/截断，
    # 导致全量月（如六月 300+ 人）留档静默失败（七月 40 人塞得下才成功）。MySQL 用 LONGTEXT，SQLite 无上限不受影响。
    Column("v", Text().with_variant(LONGTEXT(), "mysql")),
    Column("updated_by", String(50)),
    Column("updated_at", String(20)),
)
orgs = Table(                       # 主体档案（平台级基础数据）：一处维护，凭证归档取 code、物流计提取 book_code
    "orgs", _md,                    # 原先主体口径散在三处：台账自由文本 / logistics_accrual.BOOK_CODE 硬编码 / 无
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("full_name", String(200)),   # 账套全称（工商全名 / 金蝶账簿名）
    Column("short_name", String(60)),   # 简称，唯一，界面到处显示的那个
    Column("code", String(8)),          # 简码 [A-Z0-9]{2,4}，唯一，册号首段。一经生成过册号即锁死（见 org_code_locked）
    Column("book_code", String(20)),    # 金蝶账簿代码，唯一。物流计提写金蝶取 FACCOUNTBOOKID
    Column("aliases", Text),            # 别名，逗号分隔。把台账里「星期零/深零」等写法归到本行
    Column("color", String(9)),         # 标签纸颜色 #RRGGBB。只用于界面预览与"该买哪种纸"，打印不出底色（纸本身有色）
    Column("active", String(4)),        # "1"=启用 / "0"=停用（停用后不可新登记凭证册，历史照常查）
    Column("note", Text),
    Column("updated_by", String(50)),
    Column("updated_at", String(20)),
    UniqueConstraint("short_name", name="uq_org_short"),
)
# ===== 凭证归档工具（第 5 条工具线，挂「其它小工具」）=====
arch_locations = Table(             # 存放位置树（邻接表，任意深度）：库房/柜/层/箱/外仓/临时点/外借/销毁批次
    "arch_locations", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("parent_id", Integer),       # 父节点 id，根为 NULL
    Column("name", String(120)),
    Column("ntype", String(20)),        # 节点类型；"箱"是「已装箱」显示态的判据；"销毁批次"为终态
    Column("terminal", String(4)),      # "1"=终态（册子进去不可再转出，如销毁批次）
    Column("created_by", String(50)),
    Column("created_at", String(20)),
)
arch_volumes = Table(               # 凭证册：身份（终身不变，印标签）+ 当前位置（唯一会变的字段）
    "archive_volumes", _md,             # 表名与 org_code_locked() 的探测一致
    Column("vol_no", String(32), primary_key=True),   # 册号 SZL2026-03-02，系统生成
    Column("org", String(60)),          # 主体=orgs.short_name
    Column("year", Integer),
    Column("month", Integer),
    Column("vtype", String(20)),        # 凭证类型，默认「记账凭证」
    Column("seq", Integer),             # 册序（该主体该期间第几册）
    Column("no_from", Integer),         # 凭证号起
    Column("no_to", Integer),           # 凭证号止
    Column("sheets", Integer),          # 册内张数（= no_to - no_from + 1，登记时算）
    Column("status", String(10)),       # 存储状态：在库/借出中/待销毁/已销毁（「已装箱」是派生态，不存）
    Column("loc_id", Integer),          # 当前位置 → arch_locations.id
    Column("borrow_by", String(60)),    # 借出人（status=借出中 时有值）
    Column("due_date", String(20)),     # 应还日期
    Column("loc_before", Integer),      # 借出前位置（归还时自动回位）
    Column("keep_until", Integer),      # 保存到期年份
    Column("note", Text),
    Column("created_by", String(50)),
    Column("created_at", String(20)),
    Column("updated_by", String(50)),
    Column("updated_at", String(20)),
)
arch_transfers = Table(             # 转移单（一次批量转移 = 一单 + N 明细）
    "arch_transfers", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("transfer_no", String(32)),
    Column("reason", String(20)),       # 办公室搬迁/装箱/送外仓/调回/销毁/借出/归还
    Column("to_id", Integer),           # 目标位置
    Column("cnt", Integer),
    Column("approve_no", String(40)),   # 销毁审批单号（销毁时必填）
    Column("operator", String(50)),
    Column("ts", String(20)),
)
arch_transfer_items = Table(
    "arch_transfer_items", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("transfer_id", Integer),
    Column("vol_no", String(32)),
    Column("from_loc", Integer),
    Column("to_loc", Integer),
)
fee_map = Table(                    # 物流计提·费用归属映射维表（13类闭环；四级取数：费用归属×主体×业务线 精确→半精确→默认）
    "logistics_fee_map", _md,       # V2.195 映射外置：MAP 硬编码退役为本表种子。值存【名称】，金蝶编码仍走内核编码表。
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("fee", String(40)),         # 费用归属（13类）
    Column("subject", String(40)),     # 主体（空串=不限）
    Column("bizline", String(40)),     # 业务线（空串=不限）
    Column("account", String(80)),     # 借方科目 如 "6601 销售费用"（空=承默认/人工）
    Column("dept", String(80)),        # 部门名 如 "永续物流中心"（DEPT_CODE 的键）
    Column("item", String(80)),        # 费用项目短名 如 "出库运费"（ITEM_CODE 的键）
    Column("sword", String(40)),       # 摘要用语 如 "出库运费"
    Column("manual", Integer),         # 1=人工核对类（设备调拨/其它：科目部门逐笔人工定）
    Column("updated_by", String(50)),
    Column("updated_at", String(20)),
)
bizline_dim = Table(                # 物流计提·业务线维表（产品分类/产品项目挂接；TO C 规则=cpxm 非空即挂）
    "logistics_bizline", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(40), unique=True),
    Column("cpfl", String(20)),        # 金蝶产品分类编码（空=该业务线不挂）
    Column("cpxm", String(20)),        # 金蝶产品项目编码（CPXM017=山姆TO C / CPXM022=kikiherb；空=不挂）
    Column("updated_by", String(50)),
    Column("updated_at", String(20)),
)
logi_suppliers = Table(             # 物流·供应商列表（V2.198 基础数据页；简称=账单文件名认商键，全名=金蝶档案/税率键）
    "logistics_suppliers", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("short", String(60), unique=True),   # 简称（唯一查找键，同名两法人须拆不同简称）
    Column("full", String(120)),                # 全名（金蝶 BD_Supplier 口径；税率维表 supplier 键）
    Column("kd_code", String(60)),              # 金蝶供应商编码（可空，录入时以档案实查为准）
    Column("channel", String(10)),              # 渠道：线下/线上（进摘要）
    Column("note", String(200)),
    Column("updated_by", String(50)),
    Column("updated_at", String(20)),
)
bill_uploads = Table(               # 物流计提·账单上传批次留痕（V2.221：月份胶囊"真有用"——谁/何时/传了哪几份+现场可恢复）
    "logistics_bill_uploads", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("year", Integer),
    Column("month", Integer),
    Column("operator", String(50)),
    Column("ts", String(20)),
    Column("stats", Text),             # JSON：{文件数,票数,含税合计,明细行数,待人工行,待人工金额}
    Column("per_file", Text),          # JSON：[{文件,物流商,状态,票数,金额}]
    Column("rows_json", Text),         # JSON：活表行全量（恢复现场用，不用重传文件）
    Column("tickets_json", Text),      # JSON：票级明细（对账线数据源存档）
    Column("status", String(20)),      # V2.225：''=归集中 / '已提交'=物流部提交待核算组检查录入
    Column("submitted_by", String(50)),
    Column("submitted_at", String(20)),
)
supplier_docs = Table(              # 物流计提·供应商月度生命周期档案（V2.229：初始账单→计提→核对定稿→发票→付款）
    "logistics_supplier_docs", _md,    # 一行=年×月×付款主体×供应商简称（V2.232 分主体：发票抬头/付款都按主体走）。
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("year", Integer),
    Column("month", Integer),
    Column("short", String(50)),       # 供应商简称（与 logistics_suppliers.short 对齐）
    Column("subject", String(30)),     # 付款主体（深圳星期零/孝感星期九/深圳星期九；未补维度的行="待补"）
    Column("bill_file", Text),         # 初始账单：文件名（多份"；"连接）。重传=新版本→定稿作废须重新确认
    Column("bill_ts", String(20)),
    Column("bill_by", String(50)),
    Column("bill_amount", Float),      # 初始账单含税合计（解析口径）
    Column("bill_tickets", Integer),
    Column("verified", Integer),       # 1=核对定稿。无异议:定稿金额=账单金额；有异议:手登正确金额(计提行不动,差异次月冲)
    Column("verified_ts", String(20)),
    Column("verified_by", String(50)),
    Column("verified_amount", Float),
    Column("verify_note", Text),       # 定稿说明（异议原因/沟通结果——金额有出入时必看）
    Column("invoice_no", String(200)), # 发票登记（票号可多张"；"连接；金额=合计，自动与定稿金额比差异）
    Column("invoice_amount", Float),
    Column("invoice_date", String(20)),
    Column("invoice_ts", String(20)),
    Column("invoice_by", String(50)),
    Column("pay_requested", Integer),  # 1=已发起付款提醒
    Column("pay_ts", String(20)),
)
invoice_files = Table(              # 物流计提·结算凭证附件（V2.233 发票；V2.235 扩"结算账单"=正确版/盖章版账单）
    "logistics_invoice_files", _md,    # 按 主体×供应商×月 挂；结算账单只存档不解析——计提行永远不被它顶掉。
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("year", Integer),
    Column("month", Integer),
    Column("short", String(50)),
    Column("subject", String(30)),
    Column("kind", String(20)),        # 发票 / 结算账单
    Column("filename", String(200)),
    Column("content", LargeBinary),
    Column("ts", String(20)),
    Column("uploaded_by", String(50)),
)
notify_recipients = Table(          # 分场景通知收件人（V2.230：账单上传④通知设置页维护，前端可改）
    "notify_recipients", _md,          # 场景收件人为空 → 回落 conf.ini 公共名单；钉钉应用凭证仍只在 conf.ini。
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("scene", String(50), unique=True),   # 新供应商建档 / 提交核算组 / 付款提醒
    Column("mobiles", Text),           # 钉钉收件手机号，逗号/分号分隔
    Column("emails", Text),            # 邮件收件人，逗号/分号分隔
    Column("updated_by", String(50)),
    Column("updated_at", String(20)),
)
type_map = Table(                   # 物流计提·账单类型标注翻译表（物流部在账单上写的"类型"→费用归属×业务线×描述）
    "logistics_type_map", _md,      # 精确键优先；查不到走内核规则兜底；再不行进待人工。
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("pattern", String(80), unique=True),   # 标注原文（精确匹配，去首尾空格）
    Column("fee", String(40)),
    Column("bizline", String(40)),     # 空串=业务线"—"
    Column("descr", String(80)),       # 业务描述（进摘要，如 样品/山姆送仓/研发中试）
    Column("updated_by", String(50)),
    Column("updated_at", String(20)),
)
# ---------------- 电商对账·收款核销（V2.250，条目⑤一期）----------------
# 全部为新增表、可整体 drop 回滚；不触碰 post_log（防重屏障留待二期写金蝶时泛化）。
# 金额列存 Float：引擎产出前已按分位取整（确认书⑤ D11），不存在浮点尾差进库。
ec_shop_map = Table(                # 店铺对照：金蝶客户名 ↔ 旺店通店铺名（确认书① D8，3 处已知不一致）
    "ec_shop_map", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("kd_name", String(120), unique=True),   # 金蝶客户名
    Column("wdt_name", String(120)),               # 旺店通店铺名
    Column("mgmt_name", String(60)),               # 管理名称（V2.277 需求方定）：显示用简称，**只做显示层**——
    Column("platform", String(20)),                # 数据键仍是 wdt_name（跑批/上传/登记锚定不变，改名不动历史）
    Column("alipay_acct", String(40)),             # 支付宝账号(2088…)——银行对账流水包里自动认文件用（V2.255）
    Column("updated_by", String(50)),
    Column("updated_at", String(20)),
)
ec_post_log = Table(                # 电商·凭证录入台账（V2.255 建、V2.257 用）：草稿在金蝶列表查询不可见，
    "ec_post_log", _md,             # 防重录靠这本账。**独立建表、不泛化物流 post_log**（立项分析红线三的回避路径）。
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", Integer, index=True),
    Column("period", String(10)),
    Column("shop", String(120)),
    Column("kind", String(20)),                    # 扣款项 / 收款核销
    Column("kd_id", String(30)),                   # 金蝶凭证内码（Save 返回，delete_vouchers 可清）
    Column("amount", Float),
    Column("operator", String(50)),
    Column("ts", String(20)),
)
ec_fee_map = Table(                 # 费目→科目映射（确认书⑤ D9：受控配置，新费目码报警停下问人）
    "ec_fee_map", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("code", String(30), unique=True),       # 费目码（业务描述竖线前缀，如 0030003）
    Column("label", String(120)),                  # 费目名（软件服务费-类目软件服务费…）
    Column("account", String(40)),                 # 记账科目名（费用/应收账款…，凭证预览显示用）
    Column("kd_code", String(30)),                 # 金蝶科目编码（一键录入凭证用；空=不可录，V2.255）
    Column("updated_by", String(50)),
    Column("updated_at", String(20)),
)
ec_settle_runs = Table(             # 跑批留痕：一次上传+核销=一条
    "ec_settle_runs", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("shop", String(120)),                   # 店铺（旺店通名）
    Column("period", String(10)),                  # 结算期 YYYY-MM
    Column("status", String(20)),                  # running / done / error
    Column("stats", Text),                         # JSON：KPI 与分桶汇总
    Column("filenames", Text),                     # 上传文件名（人眼核对数据来源）
    Column("operator", String(50)),
    Column("ts", String(20)),
)
ec_settle_orders = Table(           # 逐单核销结果（收款核销页主表数据）
    "ec_settle_orders", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", Integer, index=True),
    Column("order_no", String(64), index=True),    # 平台原始单号
    Column("shop", String(120)),
    Column("channel", String(10)),                 # 支付宝 / 聚合
    Column("arrive_time", String(20)),             # 入账时间
    Column("serial_no", String(40)),               # 支付宝流水号（聚合渠道=聚合交易号）
    Column("plat_amt", Float),                     # 平台到账（0010001−0020001）
    Column("ar_no", String(200)),                  # 应收单号（可多张，逗号分隔）
    Column("ar_amt", Float),                       # 应收金额（蓝红合计，期间闸内）
    Column("rk_amt", Float),                       # 退款不退货调节
    Column("diff", Float),                         # 差异 = 平台 − 应收 + 退款不退货
    Column("bucket", String(20)),                  # ok/ufirst/crossed/real（真差异）
    Column("note", Text),                          # 机器初判说明（串单配对对象等）
)
ec_settle_fees = Table(             # 费目归集结果（凭证预览数据，两资金账户区）
    "ec_settle_fees", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", Integer, index=True),
    Column("zone", String(10)),                    # 支付宝 / 聚合
    Column("code", String(30)),
    Column("label", String(120)),
    Column("income", Float),
    Column("outgo", Float),
    Column("account", String(40)),                 # 按 ec_fee_map 映射；无映射=待定（报警）
)
ec_excluded = Table(                # 剔除留痕（确认书⑤ 5.3：剔了必须留痕，每期单列）
    "ec_excluded", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", Integer, index=True),
    Column("kind", String(40)),                    # 收钱码收款/内部划转/充值划转（万相台等）
    Column("cnt", Integer),
    Column("amount", Float),
    Column("detail", Text),                        # 明细行 JSON（V2.273 起全量：时间/金额/流水号/对方/描述）
)
ec_excl_notes = Table(              # 剔除留痕·定性登记（V2.274 需求方定："识别出来的做个记录"）——
    "ec_excl_notes", _md,           # 对得上活动/推广=正常、对不上=违规。按流水号记，跨跑批留存（重跑不丢）；
    Column("id", Integer, primary_key=True, autoincrement=True),   # 一笔一条，改判即覆盖，留最后判定人/时间。
    Column("period", String(10), index=True),
    Column("shop", String(120)),
    Column("serial", String(40), index=True),      # 支付宝流水号（聚合渠道=聚合交易号）——剔除明细行的锚
    Column("kind", String(60)),                    # 剔除类别（登记时抄档，防跑批记录被清后失联）
    Column("flow_ts", String(20)),                 # 流水时间（同上，留档用）
    Column("amount", Float),
    Column("verdict", String(10)),                 # 正常（对上活动/推广）/ 违规
    Column("note", String(200)),                   # 对上的活动/推广名，或违规去向与追款说明
    Column("operator", String(50)),
    Column("ts", String(20)),
)
_md.create_all(_engine)

# 细粒度权限能力清单 —— 代码持有的**静态**注册表：加一条动作权限只改这里，账号页按 ws/group 自动渲染。
# ⚠ 别直接读这个常量做判权/存盘——那会漏掉菜单树派生的准入点（kind="nav"，见下方 cap_meta()）。
#   读写权限一律走 cap_meta() / caps()；只有「加严」「BP码表对账」这类**仅针对代码持有码**的地方才用本常量。
# 字段：key 权限码 | label 中文名 | ws 所属工作台(accounting/bp/legal) | kind 类型
#       (enter=准入总闸 / manage=任命子管理员 / nav=菜单准入点(自动生成) / 省略=普通功能权限) | group 组内小类(工具线/板块族)
#       sensitive=敏感(代码定死的地板，默认不给、高亮) | plan=规划中(未上线，灰显不可勾)
# BP 板块码用 bp:board:<navTree叶子key>，即 BP 透传 X-BP-Perms 的合同码，两边必须一致。
#
# V2.53 账号页三栏化（先答「能不能进」、再答「进去能干什么」）新增两个**纯展示**字段：
#   tier = "nav"(菜单准入点) / "act"(进去能干的动作)。**独立于 kind**——kind 驱动 default_perms 的默认给不给，
#          动它会连带改变 BP 存量账号的默认板块，故另开一个字段只管账号页怎么摆。
#   mod  = 这个动作属于哪个**菜单 key**（对应 app.NAV_MODULES）。没有 mod ＝ 不限菜单、全台通用。
#   ⚠ mod 不是可有可无的注释：账号页第③栏「只显示他进得去的菜单的动作」全靠它。以前这里只有 group
#     (一个和菜单树毫无关联的自由字符串)，导致「上传科目余额表」挂在**银行对账**下，而科目余额早已搬去报表模块。
CAP_META_STATIC = [
    # ── 核算工作台 ──
    {"key": "enter_accounting", "label": "进入核算工作台", "ws": "accounting", "kind": "enter"},
    {"key": "manage_accounting", "label": "管理核算工作台账号", "ws": "accounting", "kind": "manage", "sensitive": True},
    {"key": "bank_upload", "label": "上传资金流水", "ws": "accounting", "group": "银行对账", "tier": "act", "mod": "reconcile"},
    {"key": "kingdee_refresh", "label": "从金蝶更新", "ws": "accounting", "group": "银行对账", "tier": "act", "mod": "reconcile"},
    {"key": "claim", "label": "认领/处理差异", "ws": "accounting", "group": "银行对账", "tier": "act", "mod": "reconcile"},
    {"key": "ledger_override", "label": "账户台账改动", "ws": "accounting", "group": "银行对账", "tier": "act", "mod": "ledger"},
    # 归位（V2.53）：科目余额这次搬去了「报表模块 › 财务会计」，它的上传动作跟着走，不再挂在银行对账下
    # 再归位（V2.240）：sbal 这个菜单被撤下（报表板块重排），mod 改指 rptdash「报表仪表盘」——
    #   ⚠ 不能留 mod:"sbal"：菜单树里查不到 sbal → _caps_for_ui 算不出 gate → 这条动作会掉进
    #     账号页第③栏的「不受菜单门控」堆里【恒显示】，变成一条谁都看得见、却对不上任何菜单的幽灵权限。
    #   指向 rptdash 而不是删除：手工上传金蝶报表做人眼核对是业务方要保的能力，报表仪表盘正是它的新家；
    #   rptdash 现为「敬请期待」、无人持有 enter:rptdash，故这条动作眼下对所有人都不渲染，等页面建好即接上。
    {"key": "subject_upload", "label": "上传科目余额表", "ws": "accounting", "group": "银行对账", "tier": "act", "mod": "rptdash"},
    # 归位（V2.53）：封存是「月结看板」这个菜单的动作，不是银行对账的
    {"key": "close_period", "label": "月结看板·封存本期", "ws": "accounting", "group": "银行对账", "sensitive": True,
     "tier": "act", "mod": "periodclose"},
    {"key": "logistics_upload", "label": "上传物流计提表·维护税率", "ws": "accounting", "group": "物流计提", "tier": "act", "mod": "logistics"},
    {"key": "logistics_post", "label": "物流计提·一键录入金蝶", "ws": "accounting", "group": "物流计提", "sensitive": True,
     "tier": "act", "mod": "logistics"},
    # 成本台账（V2.128 重分）：按【动作】分点，同银行对账惯例。
    # 查看＝进页面/看勾稽透视异常/导出台账（导出不单设点，同银行对账——屏幕上都看得见的数，导成 Excel 不多泄什么）。
    # **取数/上传单独一个点**：V2.122 数据改为按主体+期间落库、全员共享后，取数会【覆盖全员可见的数据】，
    # 再跟"查看"捆在一起，等于谁能看谁就能把成本会计刚核好的数冲掉。两个通道并列等价(V2.117)，故合一个点。
    {"key": "cost_ledger", "label": "存货台账·查看与导出", "ws": "accounting", "group": "存货台账",
     "tier": "act", "mod": "costledger"},
    # 取数/上传标【敏感】＝默认不给、须显式授予（业务方定）。原因：非敏感点会被 _backfill_missing_perms
    # 在启动时按 default_perms 自动补给全核算组存量账号——那样这个点等于没拆，谁都能覆盖全员数据。
    # 与银行对账的 kingdee_refresh/bank_upload(非敏感) 不一致是有意为之：那两个是各人自己的对账口径，
    # 成本台账则是【一份全员共享的月结底稿】，被谁随手一点覆盖掉，成本会计核了半天的活就没了。
    {"key": "cost_ledger_fetch", "label": "存货台账·取数/上传（会覆盖全员数据）", "ws": "accounting",
     "group": "存货台账", "sensitive": True, "tier": "act", "mod": "costledger"},
    {"key": "cost_ledger_wh", "label": "存货台账·维护基础资料", "ws": "accounting", "group": "存货台账",
     "tier": "act", "mod": "clwh"},
    {"key": "cost_ledger_close", "label": "存货台账·封存本期", "ws": "accounting", "group": "存货台账", "sensitive": True,
     "tier": "act", "mod": "costledger"},
    # 报表导出（V2.241）。导出本身只读金蝶、不写金蝶，故非敏感；
    # **改落地路径标敏感**——那是"往服务器上哪儿写文件"，填错能盖掉别的东西，
    # 且非敏感点会被 default_perms 自动发给每个核算组账号（等于人人可改路径）。
    {"key": "rpt_export", "label": "报表导出·一键导出", "ws": "accounting", "group": "报表导出",
     "tier": "act", "mod": "rptexport"},
    {"key": "rpt_export_cfg", "label": "报表导出·改落地路径与通知", "ws": "accounting", "group": "报表导出",
     "sensitive": True, "tier": "act", "mod": "rptexport"},
    # 删除单独一个点、且标敏感：它是**破坏性且不可撤销**的（还能连带删共享盘上的），
    # 与"能导出"完全两码事——能导的人不该顺带就能删。敏感＝默认不给，须显式授予。
    {"key": "rpt_export_del", "label": "报表导出·删除已导出文件", "ws": "accounting", "group": "报表导出",
     "sensitive": True, "tier": "act", "mod": "rptexport"},
    # 临时工考勤（V2.318）：全程只读——上传两张表在内存里算完即走，不写金蝶、不落库，故不设敏感位。
    {"key": "tempatt_review", "label": "临时工考勤·上传核对与导出", "ws": "accounting",
     "group": "临时工考勤", "tier": "act", "mod": "tempattrev"},
    # 单价表是【全员共享、按月落库】的一份基础数据，改了会改变所有人此后算出来的钱，
    # 故与「上传核对」分开一个点（同成本台账「维护仓库类型」的先例），默认不跟着核对权限一起给。
    {"key": "tempatt_rates", "label": "临时工考勤·维护单价表（按月）", "ws": "accounting",
     "group": "临时工考勤", "tier": "act", "mod": "tempattrev"},
    {"key": "tempatt_board", "label": "临时工看板·上传结构表查看", "ws": "accounting",
     "group": "临时工考勤", "tier": "act", "mod": "tempattboard"},
    {"key": "archive_edit", "label": "凭证归档·登记/转移/借出/销毁申请", "ws": "accounting", "group": "凭证归档",
     "tier": "act", "mod": "archive"},
    {"key": "archive_destroy", "label": "凭证归档·销毁执行（财务经理）", "ws": "accounting", "group": "凭证归档",
     "sensitive": True, "tier": "act", "mod": "archive"},
    # 汇率录入（V2.159）：写金蝶 BD_Rate 是全账套基础资料，比录一张凭证影响面大 → 标【敏感】默认不给、
    # 须显式授予、不进岗位模板、不被 default_perms 自动补发（同 logistics_post / cost_ledger_fetch 惯例）。
    {"key": "fxrate_post", "label": "汇率录入·写入金蝶（含提交、撤销）", "ws": "accounting", "group": "汇率录入",
     "sensitive": True, "tier": "act", "mod": "fxrate"},
    # 电商对账（V2.250，条目⑤一期）：两个动作点一律标【敏感】（沿 cost_ledger_fetch 先例——
    # 上传/跑批会覆盖全员共享的核销结果，基础资料是全线取数口径；非敏感会被 _backfill_missing_perms
    # 自动补给全核算组存量账号，立项分析 §6.2 的 V2.126 陷阱）。查看/导出不设点（屏幕上看得见的数）。
    {"key": "ec_settle_upload", "label": "电商对账·上传结算流水/跑批（会覆盖全员数据）", "ws": "accounting",
     "group": "电商对账", "sensitive": True, "tier": "act", "mod": "ecomsettle"},
    {"key": "ec_base_edit", "label": "电商对账·维护基础资料（店铺对照/费目科目映射）", "ws": "accounting",
     "group": "电商对账", "sensitive": True, "tier": "act", "mod": "ecombase"},
    # 写金蝶一律敏感（logistics_post/fxrate_post 先例；立项分析 §6.1）。只建草稿、提交审核人在金蝶做。
    {"key": "ec_post", "label": "电商对账·一键录入结算凭证（写金蝶·草稿）", "ws": "accounting",
     "group": "电商对账", "sensitive": True, "tier": "act", "mod": "ecomsettle"},
    # 它就是「系统设置」这个菜单的准入点（菜单声明 cap=enter_settings 复用它），故 tier=nav 不是 act。
    # mod 指回 settings 菜单：账号页第②栏据此把它排到「通用」板块里去，而不是因为它在本常量里排得早就窜到最前面。
    {"key": "enter_settings", "label": "进入系统设置", "ws": "accounting", "group": "通用", "sensitive": True,
     "tier": "nav", "mod": "settings"},
    # ── BP工作台（板块级；board 码即 BP 透传合同码）──
    {"key": "enter_bp", "label": "进入BP工作台", "ws": "bp", "kind": "enter"},
    {"key": "manage_bp", "label": "管理BP工作台账号", "ws": "bp", "kind": "manage", "sensitive": True},
    # BP 的板块码就是它的「能进哪些菜单」＝ tier=nav（**只改展示分栏，不动 kind**——
    # 给它们标 kind="nav" 会让 default_perms 不再默认给，等于悄悄改掉 BP 存量账号的板块，不能碰）
    {"key": "bp:board:dashboard", "label": "报表工作台", "ws": "bp", "group": "经营管理报表", "tier": "nav"},
    {"key": "bp:board:reportCenter", "label": "报表中心", "ws": "bp", "group": "经营管理报表", "plan": True, "tier": "nav"},
    {"key": "bp:board:pricingEcom", "label": "电商定价测算", "ws": "bp", "group": "定价测算", "tier": "nav"},
    {"key": "bp:board:pricingToc", "label": "TOC定价测算", "ws": "bp", "group": "定价测算", "plan": True, "tier": "nav"},
    {"key": "bp:board:pricingTob", "label": "TOB定价测算", "ws": "bp", "group": "定价测算", "plan": True, "tier": "nav"},
    {"key": "bp:board:pricingOverseas", "label": "海外定价测算", "ws": "bp", "group": "定价测算", "plan": True, "tier": "nav"},
    {"key": "bp:board:budgetCockpit", "label": "驾驶舱看板", "ws": "bp", "group": "销售预算", "tier": "nav"},
    {"key": "bp:board:budgetPrep", "label": "销售预算编制", "ws": "bp", "group": "销售预算", "tier": "nav"},
    {"key": "bp:board:budgetRolling", "label": "滚动预算", "ws": "bp", "group": "销售预算", "plan": True, "tier": "nav"},
    # V2.296 补登记（BP V2.183 就加了，核算这边一直没登记 → 除主管理员外没人能被授予，静默锁人）。
    # 「智能分析中心」在 BP 是**一级导航组**（与经营管理报表同级），不是挂在报表下的二级，故自成一个 group。
    # ⚠ 此码只管入口可见性；问数能问到哪些数由 BP 的 llm_query.ask 按 dashboard/budgetCockpit 另判——有入口 ≠ 什么都能问。
    {"key": "bp:board:askData", "label": "AI 问数", "ws": "bp", "group": "智能分析中心", "tier": "nav"},
    {"key": "bp:board:masterData", "label": "基础数据", "ws": "bp", "group": "通用与设置", "tier": "nav"},
    {"key": "bp:board:appSettings", "label": "基础设置", "ws": "bp", "group": "通用与设置", "sensitive": True, "tier": "nav"},
    {"key": "kingdee:fetch", "label": "读取金蝶数据", "ws": "bp", "group": "通用与设置", "sensitive": True, "tier": "act"},
    # ── BP 功能点 / 高危点（与 BP auth.py::PERMISSIONS 对齐；BP 是码表真相源，见 /api/bp-perm-drift 对账）──
    # ⚠ 这些码在 BP 侧是**敏感点**：BP 的 has_perm 对敏感点「'*' 不覆盖」，必须逐个显式透传（见 bp_perm_codes）。
    # BP 的功能点＝ tier=act。mod 指向 BP 板块码（＝它的菜单）；bp:edit / kingdee:fetch 是全台通用、不挂具体板块
    {"key": "bp:edit", "label": "编辑权（无=只读浏览）", "ws": "bp", "group": "通用与设置", "sensitive": True, "tier": "act"},
    {"key": "master:write", "label": "主数据写（整表覆盖/新增/删除）", "ws": "bp", "group": "通用与设置", "sensitive": True,
     "tier": "act", "mod": "bp:board:masterData"},
    {"key": "master:reseed", "label": "重灌主数据种子（冲掉线上清洗）", "ws": "bp", "group": "通用与设置", "sensitive": True,
     "tier": "act", "mod": "bp:board:masterData"},
    {"key": "period:close", "label": "关账 / 反关账", "ws": "bp", "group": "经营管理报表", "sensitive": True,
     "tier": "act", "mod": "bp:board:dashboard"},
    {"key": "perf:setTarget", "label": "设为业绩目标（改所有人看的目标）", "ws": "bp", "group": "销售预算", "sensitive": True,
     "tier": "act", "mod": "bp:board:budgetCockpit"},
    {"key": "budget:unlock", "label": "解锁已锁定的预算版本", "ws": "bp", "group": "销售预算", "sensitive": True,
     "tier": "act", "mod": "bp:board:budgetPrep"},
    # ── V2.296 补登记三个功能点（BP 侧 V2.146/V2.178 就有，这边漏登记 → 一直勾不到、静默不可用）──
    # ⚠ 这里的 sensitive 与 BP 的 SENSITIVE_PERMS 是**两件事**，别按 BP 抄：
    #   · BP_SENSITIVE_CODES（本文件下方）＝ BP 的 SENSITIVE_PERMS 镜像，管的是「'*' 不覆盖、主管理员须逐个显式透传」，
    #     漂了由 bp_registry_drift().superSensitiveGap 报出来 → **必须与 BP 逐字一致，这三个都不能加进去**。
    #   · CAP_META 的 sensitive ＝ 核算侧「默认给不给」（default_perms / _backfill_missing_perms 跳过、账号页高亮）。
    #     它不参与透传，故在这里标敏感**不会**让主管理员的 '*' 失效、不会造成「显示有权、点了 403」。
    # llm:call 标敏感是核算侧的**主动收紧**（业务方定，2026-08-16）：它按 token 真金白银花钱，
    #   非敏感会被 _backfill_missing_perms 按 default_perms 自动补给**全部 BP 组存量账号**（同 cost_ledger_fetch 先例）。
    #   BP 自己的注释也写着「门户接头做好后，只给需要 AI 功能的角色发这个码即可收紧」——接头已做好，就是现在。
    #   反向也更好收：先发再想收，得逐人取消勾选；默认不给则随时能加。
    {"key": "llm:call", "label": "调用大模型（AI 分析 / 问数 / 推荐归属）", "ws": "bp", "group": "智能分析中心",
     "sensitive": True, "tier": "act", "mod": "bp:board:askData"},
    # perf:trace 不标敏感（照 BP 原话：「常规 feature 点，'*' 覆盖；仅看板不下钻的角色不授」）——
    #   只是把看板上已经看得见的汇总数下钻到来源行，不写不发，同核算侧「导出不单设点」的惯例。
    {"key": "perf:trace", "label": "驾驶舱达成明细溯源（下钻到客户/物料/单号）", "ws": "bp", "group": "销售预算",
     "tier": "act", "mod": "bp:board:budgetCockpit"},
    # weekly:send 标敏感＝对外发邮件/群消息，同核算侧一切外发/写外部系统的动作（logistics_post / fxrate_post / ec_post）。
    #   注意 BP 侧 V2.150 已把它移出 SENSITIVE_PERMS，故**不进 BP_SENSITIVE_CODES**；BP 那边另有口令
    #   BP_WEEKLY_SEND_CODE 作真闸，这个码只管按钮显隐 → 双闸。
    # V2.323 加 parent：发送从属于「周报查看」——BP 侧 weekly 路由整体挂 weekly:view，
    # 没有查看权的发送权是死码；账号页据此收进层级、_cascade_revoke 据此级联收回。
    {"key": "weekly:send", "label": "发送业绩周报（邮件/群）", "ws": "bp", "group": "销售预算", "sensitive": True,
     "tier": "act", "mod": "bp:board:budgetCockpit", "parent": "weekly:view"},
    # ── V2.321 登记 BP 驾驶舱 V2.282 的 3 个视图功能点。
    #    **全标 sensitive**＝核算侧"默认不给、_backfill_missing_perms 不自动补发、须逐人勾选"
    #    （V2.296 已核实：CAP 的 sensitive 与 BP 的 SENSITIVE_PERMS 是两件事，标它不影响主管理员 '*'）。
    #    不标的话按 V2.296 表格的行为，非敏感 ws=bp 码会自动补给全部 BP 组存量账号——
    #    与业务方拍板（项目视图/周报"只有开通的才有"）正好相反。
    #    ⚠ 三个功能点在 BP 侧是**非敏感**（'*' 覆盖=管理员默认有）→ **都不进 BP_SENSITIVE_CODES**，
    #      drift 的 superSensitiveGap 不该报它们。
    {"key": "perf:projectView", "label": "驾驶舱·项目视图（收入分析）", "ws": "bp", "group": "销售预算",
     "sensitive": True, "tier": "act", "mod": "bp:board:budgetCockpit"},
    {"key": "weekly:view", "label": "驾驶舱·查看/生成业绩周报（发送另需 weekly:send）", "ws": "bp", "group": "销售预算",
     "sensitive": True, "tier": "act", "mod": "bp:board:budgetCockpit"},
    # V2.323 加 parent：导出从属于「项目视图」（没有视图权的导出权无意义），层级化+级联同上。
    {"key": "perf:export", "label": "驾驶舱·项目视图导出 Excel（接口未上线，先控按钮）", "ws": "bp", "group": "销售预算",
     "sensitive": True, "tier": "act", "mod": "bp:board:budgetCockpit", "parent": "perf:projectView"},
    # ── 数据域（BP V2.282）：勾了 = 该账号在驾驶舱**只看**勾中团队；一个不勾 = 全视野。
    #    V2.322 起**不再静态登记**（V2.321 曾手抄 8 行，业务方：「新增团队又要勾选一次」）——
    #    改由 bp_scope_cap_meta() 从 BP registry（layer=scope，源头=BP 基础数据「销售团队」）动态拉取，
    #    见下方 cap_meta()。账号页在「驾驶舱看板」堆里渲染成「数据范围」控件，不再摊平成一排勾选框。
    # ── 法务工作台（工具待建，仅准入/任命）──
    {"key": "enter_legal", "label": "进入法务工作台", "ws": "legal", "kind": "enter"},
    {"key": "manage_legal", "label": "管理法务工作台账号", "ws": "legal", "kind": "manage", "sensitive": True},
    # ── 平台级（门户自身的页面，不挂任何工作台）── V2.324
    # 业务方定（2026-08-20）：「模型配置」默认只有主管理员有，其他人要用去权限配置里开。
    # ws="platform" 是**有意不在 WS_LABEL 里**的：managed_workspaces 永远不含它 →
    #   子管理员既不「恒有」它（sub_admin_caps 只并管辖工作台的码）、也授不出它（assignable_caps 同源）；
    #   default_perms / _legacy_perms 的 own_ws / accounting 判定同样碰不到它 →
    #   「默认只有主管理员有、只有主管理员能授」全部由 ws 归属自然成立，不用像 enter_settings 那样点名特例。
    # sensitive 是双保险：日后真把 platform 升成正式工作台，敏感位仍挡住 _backfill_missing_perms 自动补发。
    # tier=nav（它是"看不看得见门户这一页"，不是危险动作）；无 mod——门户页不在核算菜单树里，
    #   _caps_for_ui 对无 mod 的 nav 点原样透传、排第②栏末尾，账号页在「平台」区块渲染。
    {"key": "model_config", "label": "模型配置（大模型接入/密钥/网关）", "ws": "platform", "group": "门户",
     "sensitive": True, "tier": "nav"},
]
# ---------------- 菜单准入点（kind="nav"）：跟着菜单树自动生成 ----------------
# 为什么要动态：菜单树可在系统设置里自建（一级增二级、二级增三级），总不能每加一个菜单就改一次代码。
# 故准入点（能不能进这个菜单）由菜单树派生，动作点（进去能干什么）仍由上面的静态注册表持有。
# db 不能反向 import app（循环依赖），故由 app 启动时注册 provider；provider 自己负责缓存（菜单树变更时失效）。
_nav_caps_provider = None


def set_nav_caps_provider(fn):
    """app 启动时注册：fn() -> [{key,label,ws,group,kind:"nav"}...]，即当前菜单树派生的准入点。"""
    global _nav_caps_provider
    _nav_caps_provider = fn


def nav_cap_meta():
    """菜单准入点清单。provider 未注册或出错时返回空——宁可少给权限，不能让整站 500。"""
    if not _nav_caps_provider:
        return []
    try:
        return list(_nav_caps_provider() or [])
    except Exception:
        return []


# ---------------- BP 数据域码（perf:team:*）：从 BP registry 动态拉取（V2.322） ----------------
_BP_SCOPE_MEM = {"at": None, "caps": None}      # 进程内 60s 新鲜度缓存
_BP_SCOPE_SETTING = "bp_scope_caps_cache"       # 设置表持久缓存：重启/BP 不可达时沿用上次成功结果
BP_SCOPE_PREFIX = "perf:team:"


def bp_scope_cap_meta():
    """BP 数据域码 → 动态 CAP 条目（V2.322，业务方「新增团队又要勾选一次」）。
    真相源＝BP `/api/perms/registry` 里 layer="scope" 的码（源头是 BP 基础数据「销售团队」）：
    团队增删改名全自动跟进，本侧不再手抄登记（V2.321 的 8 行静态登记就此废除）。
    条目一律 sensitive=True（默认不给、不进 default_perms/backfill，V2.296 口径）。

    可用性三层（⚠ parse_perms/set_user_perms 按 caps() 归一化，动态码缺席会把已授团队码剥掉，
    故这里**宁可旧、不可空**；另有 parse/set 的前缀保底兜第四层）：
      ① 进程内 60s 缓存——账号页反复渲染不打爆 BP；
      ② 拉取成功 → 同步落设置表（持久）；
      ③ 拉取失败 → 用设置表里上次成功的结果；从没成功过才返回空（宁可暂时勾不到，不能猜）。"""
    now = datetime.datetime.now()
    mem = _BP_SCOPE_MEM
    if mem["caps"] is not None and mem["at"] is not None and (now - mem["at"]).total_seconds() < 60:
        return list(mem["caps"])
    rows = None
    try:
        import urllib.request
        with urllib.request.urlopen(BP_API_BASE + "/api/perms/registry", timeout=1.5) as r:
            reg = json.loads(r.read().decode("utf-8"))
        rows = [{"key": c["code"], "label": c.get("label") or c["code"], "ws": "bp",
                 "group": "销售预算", "sensitive": True, "tier": "act",
                 "mod": "bp:board:budgetCockpit", "scope": True}
                for c in reg.get("codes", [])
                if c.get("layer") == "scope" and str(c.get("code", "")).startswith(BP_SCOPE_PREFIX)]
        set_setting(_BP_SCOPE_SETTING, rows)
    except Exception:
        rows = None
    if rows is None:
        rows = get_setting(_BP_SCOPE_SETTING, []) or []
        for m in rows:      # 兜底缓存也补齐字段（防设置表里存过残缺值）
            m.setdefault("sensitive", True); m.setdefault("tier", "act")
            m.setdefault("mod", "bp:board:budgetCockpit"); m.setdefault("scope", True)
    mem["at"], mem["caps"] = now, list(rows)
    return list(rows)


def cap_meta():
    """完整权限点注册表 ＝ 代码持有的静态码 + 菜单树派生的准入点 + BP 数据域动态码（V2.322）。"""
    return list(CAP_META_STATIC) + nav_cap_meta() + bp_scope_cap_meta()


def caps():
    """全部权限码。**动态**——菜单树一变就变，故所有读/写/补齐都必须调它，不能缓存成模块常量，
    否则新建菜单的准入点会在 parse_perms/set_user_perms 处被静默丢弃（改造前的老坑）。"""
    return [m["key"] for m in cap_meta()]


# 以下两组只认静态注册表：菜单准入点既不是工作台准入总闸，也永远不敏感
_ENTER_CAPS = tuple(m["key"] for m in CAP_META_STATIC if m.get("kind") == "enter")
_GRP_ENTER = {"核算组": "enter_accounting", "BP组": "enter_bp", "法务": "enter_legal"}
# 敏感能力＝代码定死的地板（sensitive 标记）：新账号默认不给，须显式授予；主管理员只能加严不能降级
_SENSITIVE_CAPS = tuple(m["key"] for m in CAP_META_STATIC if m.get("sensitive"))
# 默认不给的类别：工作台准入、任命权、菜单准入点。
# **"nav" 必须在这里**——否则 default_perms/_legacy_perms 会把菜单准入点当"普通非敏感功能点"自动补给
# 存量账号与新账号，业务方定的「存量全部不给」当场失效（见确认书 D5）。
_NO_DEFAULT_KINDS = ("enter", "manage", "nav")

# ---------------- 分级管理员：工作台 ↔ 组 / 可授权限 / 管理能力 映射 ----------------
WS_LABEL = {"accounting": "核算工作台", "bp": "BP工作台", "legal": "法务工作台"}
GRP_OF_WS = {"accounting": "核算组", "bp": "BP组", "legal": "法务"}
MANAGE_CAP = {"accounting": "manage_accounting", "bp": "manage_bp", "legal": "manage_legal"}
# 每个工作台可授的「功能权限点」＝该 ws 下、非 manage 的所有码（含准入与菜单准入点，不含 manage_* 任命权）
# **函数不是常量**：菜单准入点随菜单树变，算死了新建菜单就授不出去。


def ws_caps(ws):
    return [m["key"] for m in cap_meta() if m.get("ws") == ws and m.get("kind") != "manage"]


def _pd(user):
    """取用户 perms 为 dict——兼容 get_user(原始JSON串) 与 list_users(已解析dict)。"""
    p = user.get("perms") if user else None
    return p if isinstance(p, dict) else parse_perms(p)


def is_super(user):
    """主管理员＝ role=admin，全权。"""
    return bool(user and user.get("role") == "admin")


def managed_workspaces(user):
    """该用户可管理的工作台列表：主管理员=全部；否则=有对应 manage_* 能力的那些（可多个，如总监）。"""
    if is_super(user):
        return list(WS_LABEL.keys())
    if not user:
        return []
    p = _pd(user)
    return [w for w in WS_LABEL if p.get(MANAGE_CAP[w])]


def can_admin_accounts(user):
    """能否进「账号管理」：主管理员，或任一工作台子管理员。"""
    return is_super(user) or len(managed_workspaces(user)) > 0


def assignable_caps(user):
    """该管理员可授予/收回的权限点集合：主管理员=全部；子管理员=其管辖工作台功能权限并集（不含 manage_*）。"""
    if is_super(user):
        return set(caps())
    out = set()                       # 不叫 caps：会遮住模块级的 caps() 函数
    for w in managed_workspaces(user):
        out.update(ws_caps(w))
    return out


def manageable_grps(user):
    """子管理员可管的组集合；主管理员返回 None（不限）。"""
    if is_super(user):
        return None
    return {GRP_OF_WS[w] for w in managed_workspaces(user)}


def _has_any_manage(perms_dict):
    return any(perms_dict.get(MANAGE_CAP[w]) for w in WS_LABEL)


def can_manage_user(admin, target):
    """admin 能否管理 target 这个账号：主管理员可管所有；子管理员只能管【自己管辖组内的普通用户】，
    碰不了主管理员、也碰不了别的子管理员。"""
    if is_super(admin):
        return True
    if not managed_workspaces(admin) or not target:
        return False
    if target.get("role") == "admin":
        return False
    if target.get("grp") not in (manageable_grps(admin) or set()):
        return False
    if _has_any_manage(_pd(target)):   # 目标是子管理员 → 只有主管理员能管
        return False
    return True


def _ensure_user_columns():
    """老库缺列时补上（SQLite/MySQL 均适用）：perms(V2.13前无)、post(岗位)、must_change_pwd(V2.330)。"""
    from sqlalchemy import inspect, text
    try:
        cols = [c["name"] for c in inspect(_engine).get_columns("users")]
        with _engine.begin() as c:
            if "perms" not in cols:
                c.execute(text("ALTER TABLE users ADD COLUMN perms TEXT"))
            if "post" not in cols:
                c.execute(text("ALTER TABLE users ADD COLUMN post VARCHAR(40)"))
            # V2.330 首次登录强制改密：新建/重置密码时置 1，本人改密后清 0。
            # 存量账号默认 0（不强制）——要强制某个老账号，管理员给他重置一次密码即可。
            if "must_change_pwd" not in cols:
                c.execute(text("ALTER TABLE users ADD COLUMN must_change_pwd INTEGER DEFAULT 0"))
    except Exception:
        pass


_ensure_user_columns()


def _ensure_portal_columns():
    """老库 portal_tools 缺列时补上（V2.331 加 mods：自动联动的模块映射）。"""
    from sqlalchemy import inspect, text
    try:
        cols = [c["name"] for c in inspect(_engine).get_columns("portal_tools")]
        with _engine.begin() as c:
            if "mods" not in cols:
                c.execute(text("ALTER TABLE portal_tools ADD COLUMN mods TEXT"))
    except Exception:
        pass


_ensure_portal_columns()


def _ensure_app_settings_wide():
    """把既有 app_settings.v 加宽到 LONGTEXT（create_all 不改既有列）。
    临时工「按期留档」的结论快照压缩后大月可达数百KB，MySQL 的 Text 仅 64KB 会拒收/截断→大月留档静默失败。
    仅 MySQL 需要 ALTER；SQLite 的 TEXT 本就无长度上限，跳过。已是 LONGTEXT 则不重复执行。"""
    from sqlalchemy import inspect, text
    try:
        if _engine.dialect.name != "mysql":
            return
        col = next((c for c in inspect(_engine).get_columns("app_settings") if c["name"] == "v"), None)
        if col and "LONGTEXT" not in str(col["type"]).upper():
            with _engine.begin() as c:
                c.execute(text("ALTER TABLE app_settings MODIFY v LONGTEXT"))
    except Exception:
        pass


_ensure_app_settings_wide()


def default_perms(grp=None):
    """新账号默认权限：只默认开【本人所属工作台】的非敏感功能权限 + 本组准入；
    其余工作台的板块/准入、以及所有敏感能力，一律默认不给（外部协作等无对应工作台→全不给，管理员逐项授予）。"""
    own_ws = next((ws for ws, g in GRP_OF_WS.items() if g == grp), None)
    esc = escalated_caps()                             # 主管理员单向加严的码，也按敏感默认不给
    p = {k: False for k in caps()}
    for m in cap_meta():
        if m.get("kind") in _NO_DEFAULT_KINDS or m.get("sensitive") or m["key"] in esc:
            continue                                   # 准入/任命/菜单准入点/敏感(含加严) 默认不给
        if own_ws and m.get("ws") == own_ws:
            p[m["key"]] = True                         # 只开本工作台的非敏感功能权限
    if grp in _GRP_ENTER:
        p[_GRP_ENTER[grp]] = True                      # 本组对应的准入
    return p


def _legacy_perms():
    """老账号(无 perms，V2.13 前) 兼容：只给核算工作台的非敏感功能权限(含进核算)，
    跨工作台准入/板块 与 所有敏感能力一律不给——避免老账号意外能进 BP/法务或写金蝶。
    **菜单准入点(kind="nav")也一律不给**：它非敏感、ws=accounting，不显式排除的话这里会把全部菜单
    白送给 perms=NULL 的老账号，业务方定的「存量全部不给」当场失效（确认书 D5）。"""
    return {m["key"]: (m.get("ws") == "accounting" and not m.get("sensitive")
                       and m.get("kind") not in ("manage", "nav"))
            for m in cap_meta()}


def parse_perms(raw):
    """DB 里的 perms(JSON 或 NULL) → 规范化 dict。NULL/坏数据→老账号兼容(见 _legacy_perms)；
    正常 JSON→缺的键(如新加的板块码)默认 False。"""
    if not raw:
        return _legacy_perms()
    try:
        d = json.loads(raw)
    except Exception:
        return _legacy_perms()
    out = {k: bool(d.get(k, False)) for k in caps()}
    # V2.322 前缀保底：数据域码（perf:team:*）是动态 caps，BP 不可达/缓存为空时会从 caps() 缺席——
    # 已授的团队码不能被归一化剥掉（剥掉=该账号静默从"只看本团队"放开成全视野，方向性错误）。
    for k, v in d.items():
        if k.startswith(BP_SCOPE_PREFIX) and k not in out:
            out[k] = bool(v)
    return out


def sub_admin_caps(user):
    """子管理员的默认全权范围（V2.149，业务方定）：其管辖工作台的全部功能码，**除 enter_settings**。
    动态算、不发点——任命即全权、撤任命即收权，新加的权限点自动跟上（不吃 _backfill 那类补发坑）。
    背景矛盾：子管理员能给别人授出管辖工作台的全部点（assignable_caps），自己却要逐点被授——
    冯辉 10/57 却能授出 57 个，说不通。"""
    out = set()
    for w in managed_workspaces(user):
        out.update(ws_caps(w))
    out.discard("enter_settings")          # 业务方点名的唯一例外：系统设置仍须显式授
    return out


def user_can(user, cap):
    """当前用户是否有某能力：主管理员恒有；子管理员在其管辖工作台内恒有（除 enter_settings，V2.149）；
    其余看 perms。子管理员是**恒有**而非"默认可覆盖"——账号页保存会把未勾的存成 false，
    若尊重显式 false，历史保存过一次就把默认压没了；要收权的正路是撤销任命。"""
    if not user:
        return False
    if user.get("role") == "admin":
        return True
    # 顺手修 V2.128 记档的坑：原来直接 parse_perms(user["perms"])，list_users 传来的 dict 会
    # json.loads(dict) 抛异常 → 落 _legacy_perms（非敏感全给）。_pd 兼容 str/dict 两种形态。
    p = _pd(user)
    if _has_any_manage(p) and cap in sub_admin_caps(user):
        return True
    return p.get(cap, False)


_ESCALATED_KEY = "sensitive_escalated"


def escalated_caps():
    """主管理员单向加严的权限码集合（存 app_settings，全员生效）。只认【当前存在、非代码定死、非准入/任命】的码。"""
    lst = get_setting(_ESCALATED_KEY, [])
    if not isinstance(lst, list):
        return set()
    # 只认代码持有的码：菜单准入点(kind="nav")不可加严——它是"能不能看见这个菜单"，不是危险动作
    valid = {m["key"] for m in CAP_META_STATIC
             if m.get("kind") not in ("enter", "manage") and not m.get("sensitive")}
    return {k for k in lst if k in valid}


def is_sensitive(cap):
    """有效敏感 = 代码定死地板 ∪ 主管理员加严 ∪ BP 数据域动态码（V2.322：动态码不在静态表里，
    按前缀判——即便 BP 不可达、码在 cap_meta 里暂缺，敏感语义也不丢）。"""
    return cap in _SENSITIVE_CAPS or cap.startswith(BP_SCOPE_PREFIX) or cap in escalated_caps()


def set_cap_escalated(cap, on, operator=""):
    """主管理员把某常规权限单向升为敏感(on=True)/降回常规(on=False)。
    不能碰代码定死项(已是敏感)或准入/任命——只能更严、不能把地板降级。返回是否成功。"""
    meta = next((m for m in CAP_META_STATIC if m["key"] == cap), None)
    if not meta or meta.get("kind") in ("enter", "manage") or meta.get("sensitive"):
        return False
    cur = escalated_caps()
    cur = (cur | {cap}) if on else (cur - {cap})
    set_setting(_ESCALATED_KEY, sorted(cur), operator)
    return True


# BP 侧的敏感码（BP auth.py::SENSITIVE_PERMS）——'*' 不覆盖，主管理员须显式透传。
# 真相源是 BP 的 GET /api/perms/registry；此处是**透传用的静态副本**，漂了由 bp_registry_drift() 报出来。
BP_SENSITIVE_CODES = ["bp:edit", "master:write", "master:reseed",
                      "period:close", "perf:setTarget", "budget:unlock"]

BP_API_BASE = os.getenv("BP_API_BASE", "http://127.0.0.1:8010")


def bp_registry_drift():
    """拉 BP 的权限码表与本地 CAP_META 对账（V2.106）。

    为什么要有：BP 在 auth.py::PERMISSIONS 注册码、这里在 CAP_META 登记，**两边手抄**，
    漂了不会报错，只会「某功能对某些人静默不可用」（此处勾不到该码 → 除主管理员外无人能被授予）。
    → 账号管理页拉本函数结果，不一致就提示。

    BP 不可达/超时 → 返回 None（**静默降级**，绝不拖垮账号管理页）。
    """
    import urllib.request
    try:
        with urllib.request.urlopen(BP_API_BASE + "/api/perms/registry", timeout=1.5) as r:
            reg = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    # V2.322：layer="scope" 的数据域码不进对账——它们由 bp_scope_cap_meta() 动态同步，
    # 永不手抄登记，比进来只会恒报 missing 的假漂移。
    bp = {c["code"]: c for c in reg.get("codes", []) if c.get("layer") != "scope"}
    # 只比对「会透传给 BP 的码」：enter_bp/manage_bp 是中台侧概念、本就不外发，不算漂移
    mine = {m["key"] for m in CAP_META_STATIC
            if m.get("ws") == "bp" and m.get("kind") not in ("enter", "manage")}
    missing = [bp[c] for c in sorted(set(bp) - mine)]          # BP 有、这里没登记 → 勾不到
    extra = sorted(mine - set(bp))                              # 这里有、BP 不认 → 死码
    # 主管理员透传串里漏掉的敏感码（会导致主管理员失权）
    sens_gap = sorted(set(reg.get("sensitive") or []) - set(BP_SENSITIVE_CODES))
    return {"bpVersion": reg.get("version"), "missing": missing, "extra": extra,
            "superSensitiveGap": sens_gap,
            "ok": not missing and not extra and not sens_gap}


def bp_perm_codes(user):
    """透传给 BP 的权限码（供 Nginx 注入 X-BP-Perms）：主管理员=['*']；否则=其拥有的 BP 板块码+kingdee:fetch。
    无 enter_bp（未准入）→ 返回 []，BP 端也进不来。enter_bp/manage_bp 不外发（属中台侧概念）。"""
    if is_super(user):
        # ⚠ BP 侧「敏感码 '*' 不覆盖」（BP auth.py::has_perm）——只发 "*" 的话主管理员会**当场失去**
        # bp:edit / master:write / period:close / perf:setTarget 等，表现为「基础数据存不了、关不了账」。
        # 故主管理员必须把 BP 的敏感码逐个显式带上。BP 新增敏感码时这里要同步（/api/bp-perm-drift 会报出来）。
        return ["*"] + BP_SENSITIVE_CODES
    if "bp" in managed_workspaces(user):
        # V2.149：BP 子管理员＝BP 工作台内全权，透传与主管理员同款（'*' + 敏感码逐个显式）。
        # BP 侧敏感码 '*' 不覆盖（auth.py::has_perm），不带上就会"管 BP 的人存不了基础数据"。
        return ["*"] + BP_SENSITIVE_CODES
    p = _pd(user)
    if not p.get("enter_bp"):
        return []
    out = [m["key"] for m in CAP_META_STATIC
           if m.get("ws") == "bp" and m.get("kind") not in ("enter", "manage") and p.get(m["key"])]
    # V2.322 数据域码按**前缀直通**：授了就透传，不依赖 registry 在线/动态 meta 在场——
    # 透传断了=受限账号静默放开成全视野，方向性错误；meta 只管账号页可勾性，不管透传。
    out += [k for k, v in p.items()
            if v and k.startswith(BP_SCOPE_PREFIX) and k not in out]
    return out


def _backfill_missing_perms():
    """给存量账号补齐【新加的权限码】(如 bp:board:*)：缺失的键按 default_perms(其分组) 填、已有键不动——
    避免 Nginx 接头透传真权限后，存量 BP 账号因 perms JSON 缺键默认 False 而丢掉本该有的板块。幂等。"""
    with _engine.begin() as c:
        rows = c.execute(select(users.c.name, users.c.grp, users.c.perms)).mappings().all()
        for r in rows:
            if not r["perms"]:                     # NULL → 走 _legacy_perms，不动
                continue
            try:
                cur = json.loads(r["perms"])
            except Exception:
                continue
            dflt = default_perms(r["grp"])
            missing = {k: dflt.get(k, False) for k in caps() if k not in cur}
            if missing:
                c.execute(update(users).where(users.c.name == r["name"])
                          .values(perms=json.dumps({**cur, **missing}, ensure_ascii=False)))


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


# ----------------------------- 认领 -----------------------------
def load_claims() -> dict:
    with _engine.connect() as c:
        rows = c.execute(select(claims)).mappings().all()
    return {r["item_key"]: {"状态": r["status"], "操作人": r["operator"],
                            "时间": r["ts"], "备注": r["note"] or ""} for r in rows}


def set_claim(key, status, operator, ts, note=""):
    with _engine.begin() as c:
        exists = c.execute(select(claims.c.item_key).where(claims.c.item_key == key)).first()
        vals = dict(status=status, operator=operator, ts=ts, note=note)
        if exists:
            c.execute(update(claims).where(claims.c.item_key == key).values(**vals))
        else:
            c.execute(insert(claims).values(item_key=key, **vals))


def del_claim(key):
    with _engine.begin() as c:
        c.execute(delete(claims).where(claims.c.item_key == key))


# ----------------------------- 账户覆盖 -----------------------------
def load_overrides() -> dict:
    with _engine.connect() as c:
        rows = c.execute(select(overrides)).mappings().all()
    out = {}
    for r in rows:
        try:
            out[r["acct"]] = json.loads(r["data"] or "{}")
        except Exception:
            out[r["acct"]] = {}
    return out


def save_overrides(ov: dict):
    with _engine.begin() as c:
        c.execute(delete(overrides))
        for acct, d in (ov or {}).items():
            c.execute(insert(overrides).values(acct=str(acct), data=json.dumps(d, ensure_ascii=False)))


# ----------------------------- 审计留痕 -----------------------------
def audit(operator, action, target="", detail=""):
    with _engine.begin() as c:
        c.execute(insert(audit_log).values(ts=_now(), operator=str(operator or ""),
                                            action=str(action), target=str(target)[:160],
                                            detail=str(detail)))


def recent_audit(limit=200):
    with _engine.connect() as c:
        rows = c.execute(select(audit_log).order_by(audit_log.c.id.desc()).limit(limit)).mappings().all()
    return [dict(r) for r in rows]


def backend_info():
    return {"db": DB_URL.split("://")[0], "url_tail": DB_URL.split("@")[-1] if "@" in DB_URL else "(sqlite本地文件)"}


# ----------------------------- 账号 / 登录（阶段2） -----------------------------
def _hash_pwd(pwd, salt=None):
    salt = salt or secrets.token_hex(8)
    h = hashlib.pbkdf2_hmac("sha256", str(pwd).encode("utf-8"), bytes.fromhex(salt), 100000).hex()
    return f"{salt}${h}"


def _check_pwd(pwd, stored):
    try:
        salt, h = str(stored).split("$", 1)
        return secrets.compare_digest(_hash_pwd(pwd, salt).split("$", 1)[1], h)
    except Exception:
        return False


def get_user(name):
    with _engine.connect() as c:
        r = c.execute(select(users).where(users.c.name == name)).mappings().first()
    return dict(r) if r else None


def list_users():
    with _engine.connect() as c:
        rows = c.execute(select(users.c.id, users.c.name, users.c.grp, users.c.post, users.c.role,
                                users.c.active, users.c.created_at, users.c.perms).order_by(users.c.id)).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["post"] = d.get("post") or ""
        d["perms"] = parse_perms(d.pop("perms", None))     # 规范化成 dict 返给前端
        out.append(d)
    return out


def create_user(name, pwd, grp="核算组", role="normal", perms=None, post=""):
    p = {k: bool((perms or default_perms(grp)).get(k, False)) for k in caps()}
    with _engine.begin() as c:
        # must_change_pwd=1（V2.330）：初始密码是管理员设的，本人首次登录必须改掉
        c.execute(insert(users).values(name=name, grp=grp, post=(post or ""), role=role,
                                        pwd=_hash_pwd(pwd), active=1, created_at=_now(),
                                        must_change_pwd=1,
                                        perms=json.dumps(p, ensure_ascii=False)))


def set_user_post(name, post):
    with _engine.begin() as c:
        c.execute(update(users).where(users.c.name == name).values(post=(post or "")))


def set_user_perms(name, perms):
    p = {k: bool((perms or {}).get(k, False)) for k in caps()}
    # V2.322 前缀保底（与 parse_perms 对称）：动态数据域码缺席时不丢传入的团队码授权
    for k, v in (perms or {}).items():
        if k.startswith(BP_SCOPE_PREFIX) and k not in p:
            p[k] = bool(v)
    with _engine.begin() as c:
        c.execute(update(users).where(users.c.name == name).values(perms=json.dumps(p, ensure_ascii=False)))


def verify_login(name, pwd):
    u = get_user(name)
    if not u or not u.get("active"):
        return None
    return u if _check_pwd(pwd, u.get("pwd")) else None


def set_user_active(name, active):
    with _engine.begin() as c:
        c.execute(update(users).where(users.c.name == name).values(active=1 if active else 0))


def reset_pwd(name, pwd):
    """管理员重置密码：重置后的密码管理员知道 → 同样强制该账号下次登录改密（V2.330）。"""
    with _engine.begin() as c:
        c.execute(update(users).where(users.c.name == name).values(pwd=_hash_pwd(pwd), must_change_pwd=1))


def change_own_pwd(name, old_pwd, new_pwd):
    """本人改密（V2.330，/api/change-pwd）：验旧密→写新密→清 must_change_pwd。
    规则：新密码 ≥8 位、不得与原密码或姓名相同。密码值不落任何日志。"""
    u = get_user(name)
    if not u or not u.get("active"):
        return False, "账号不可用"
    if not _check_pwd(old_pwd, u.get("pwd")):
        return False, "原密码不正确"
    new_pwd = str(new_pwd or "")
    if len(new_pwd) < 8:
        return False, "新密码至少 8 位"
    if new_pwd == str(old_pwd or "") or new_pwd == str(name):
        return False, "新密码不能与原密码或姓名相同"
    with _engine.begin() as c:
        c.execute(update(users).where(users.c.name == name).values(pwd=_hash_pwd(new_pwd), must_change_pwd=0))
    return True, "OK"


def delete_user(name):
    with _engine.begin() as c:
        c.execute(delete(users).where(users.c.name == name))


def seed_admin():
    """首启无任何账号时，种一个管理员(管理员/admin888)引导——登录后自行改密+建团队账号。"""
    if not list_users():
        create_user("管理员", "admin888", grp="管理", role="admin")


# 会话（DB 存 token，重启不掉线；登出即失效）
def create_session(name):
    tok = secrets.token_hex(24)
    with _engine.begin() as c:
        c.execute(insert(sessions).values(token=tok, name=name, created_at=_now()))
    return tok


def session_user(token):
    if not token:
        return None
    with _engine.connect() as c:
        r = c.execute(select(sessions.c.name).where(sessions.c.token == token)).first()
    return get_user(r[0]) if r else None


def delete_session(token):
    with _engine.begin() as c:
        c.execute(delete(sessions).where(sessions.c.token == token))


def delete_user_sessions(name):
    with _engine.begin() as c:
        c.execute(delete(sessions).where(sessions.c.name == name))


# ----------------------------- 物流计提·税率维表 -----------------------------
def list_tax_rates():
    with _engine.connect() as c:
        rows = c.execute(select(tax_rates).order_by(tax_rates.c.supplier, tax_rates.c.fee_type)).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["rate"] = float(d.get("rate") or 0)
        except Exception:
            d["rate"] = 0.0
        out.append(d)
    return out


def save_tax_rate(supplier, fee_type, rate, operator):
    """同 (供应商, 费用类型) 存在则更新，否则新增。rate 传小数（0.09）。"""
    supplier = str(supplier or "").strip()
    fee_type = str(fee_type or "").strip()
    vals = dict(rate=repr(float(rate)), updated_by=str(operator or ""), updated_at=_now())
    with _engine.begin() as c:
        r = c.execute(select(tax_rates.c.id).where(
            tax_rates.c.supplier == supplier, tax_rates.c.fee_type == fee_type)).first()
        if r:
            c.execute(update(tax_rates).where(tax_rates.c.id == r[0]).values(**vals))
            return r[0]
        res = c.execute(insert(tax_rates).values(supplier=supplier, fee_type=fee_type, **vals))
        return res.inserted_primary_key[0]


def delete_tax_rate(rid):
    with _engine.begin() as c:
        c.execute(delete(tax_rates).where(tax_rates.c.id == int(rid)))


def tax_rate_lookup():
    """{(供应商, 费用类型): 税率}。费用类型空串 = 该供应商默认税率（查不到具体费用类型时兜底）。"""
    return {(r["supplier"], r["fee_type"]): r["rate"] for r in list_tax_rates()}


# ----------------------------- 物流计提·映射维表（V2.195 映射外置） -----------------------------
def _list_table(tbl, *order_cols):
    with _engine.connect() as c:
        return [dict(r) for r in c.execute(select(tbl).order_by(*order_cols)).mappings().all()]


def list_fee_map():
    return _list_table(fee_map, fee_map.c.fee, fee_map.c.subject, fee_map.c.bizline)


def save_fee_map(row, operator):
    """键=(fee, subject, bizline)，存在则更新。row: fee/subject/bizline/account/dept/item/sword/manual"""
    key = {k: str(row.get(k, "") or "").strip() for k in ("fee", "subject", "bizline")}
    if not key["fee"]:
        raise ValueError("费用归属不能为空")
    vals = {k: str(row.get(k, "") or "").strip() for k in ("account", "dept", "item", "sword")}
    vals["manual"] = 1 if row.get("manual") else 0
    vals.update(updated_by=str(operator or ""), updated_at=_now())
    with _engine.begin() as c:
        r = c.execute(select(fee_map.c.id).where(
            fee_map.c.fee == key["fee"], fee_map.c.subject == key["subject"],
            fee_map.c.bizline == key["bizline"])).first()
        if r:
            c.execute(update(fee_map).where(fee_map.c.id == r[0]).values(**vals))
            return r[0]
        return c.execute(insert(fee_map).values(**key, **vals)).inserted_primary_key[0]


def delete_fee_map(rid):
    with _engine.begin() as c:
        c.execute(delete(fee_map).where(fee_map.c.id == int(rid)))


def fee_map_lookup():
    """{(fee, subject, bizline): row}。取数按 (f,s,b)→(f,s,'')→(f,'',b)→(f,'','') 四级精确优先。"""
    return {(r["fee"], r["subject"], r["bizline"]): r for r in list_fee_map()}


def resolve_fee_map(lk, fee, subject, bizline):
    """四级取数。返回 (row或None, 命中层级说明)。"""
    for key, tier in (((fee, subject, bizline), "精确"), ((fee, subject, ""), "主体例外"),
                      ((fee, "", bizline), "业务线例外"), ((fee, "", ""), "默认")):
        r = lk.get(key)
        if r:
            return r, tier
    return None, "未配置"


def list_bizlines():
    return _list_table(bizline_dim, bizline_dim.c.id)


def save_bizline(row, operator):
    name = str(row.get("name", "") or "").strip()
    if not name:
        raise ValueError("业务线名不能为空")
    vals = dict(cpfl=str(row.get("cpfl", "") or "").strip(), cpxm=str(row.get("cpxm", "") or "").strip(),
                updated_by=str(operator or ""), updated_at=_now())
    with _engine.begin() as c:
        r = c.execute(select(bizline_dim.c.id).where(bizline_dim.c.name == name)).first()
        if r:
            c.execute(update(bizline_dim).where(bizline_dim.c.id == r[0]).values(**vals))
            return r[0]
        return c.execute(insert(bizline_dim).values(name=name, **vals)).inserted_primary_key[0]


def delete_bizline(rid):
    with _engine.begin() as c:
        c.execute(delete(bizline_dim).where(bizline_dim.c.id == int(rid)))


def bizline_lookup():
    """{业务线名: (产品分类编码, 产品项目编码)}"""
    return {r["name"]: (r["cpfl"] or "", r["cpxm"] or "") for r in list_bizlines()}


def _ensure_bill_upload_status_cols():
    """V2.221 建的表无 status 列时补上（create_all 不改既有表——主体档案 color 列同款兜底）。"""
    from sqlalchemy import text as _text
    with _engine.begin() as c:
        cols = [r[1] for r in c.execute(_text("PRAGMA table_info(logistics_bill_uploads)")).fetchall()] \
            if DB_URL.startswith("sqlite") else \
            [r[0] for r in c.execute(_text(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='logistics_bill_uploads'")).fetchall()]
        for col, ddl in (("status", "VARCHAR(20)"), ("submitted_by", "VARCHAR(50)"), ("submitted_at", "VARCHAR(20)")):
            if col not in cols:
                c.execute(_text(f"ALTER TABLE logistics_bill_uploads ADD COLUMN {col} {ddl}"))


_ensure_bill_upload_status_cols()


def _ensure_ec_shop_map_cols():
    """V2.250 建的 ec_shop_map 无 alipay_acct 列时补上（create_all 不改既有表，同上款兜底）。"""
    from sqlalchemy import text as _text
    with _engine.begin() as c:
        cols = [r[1] for r in c.execute(_text("PRAGMA table_info(ec_shop_map)")).fetchall()] \
            if DB_URL.startswith("sqlite") else \
            [r[0] for r in c.execute(_text(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='ec_shop_map'")).fetchall()]
        if "alipay_acct" not in cols:
            c.execute(_text("ALTER TABLE ec_shop_map ADD COLUMN alipay_acct VARCHAR(40)"))
        if "mgmt_name" not in cols:
            c.execute(_text("ALTER TABLE ec_shop_map ADD COLUMN mgmt_name VARCHAR(60)"))
        cols2 = [r[1] for r in c.execute(_text("PRAGMA table_info(ec_fee_map)")).fetchall()] \
            if DB_URL.startswith("sqlite") else \
            [r[0] for r in c.execute(_text(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='ec_fee_map'")).fetchall()]
        if "kd_code" not in cols2:
            c.execute(_text("ALTER TABLE ec_fee_map ADD COLUMN kd_code VARCHAR(30)"))


_ensure_ec_shop_map_cols()


def _ensure_supplier_docs_subject_col():
    """V2.229 建的档案表无 subject 列时补上（V2.232 分主体）。旧的"整商一行"档案没有主体归属，
    留着会永远匹配不上——直接清掉（至多损失当天的定稿/发票登记，重点亮即可）。"""
    from sqlalchemy import text as _text
    with _engine.begin() as c:
        cols = [r[1] for r in c.execute(_text("PRAGMA table_info(logistics_supplier_docs)")).fetchall()] \
            if DB_URL.startswith("sqlite") else \
            [r[0] for r in c.execute(_text(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='logistics_supplier_docs'")).fetchall()]
        if cols and "subject" not in cols:
            c.execute(_text("ALTER TABLE logistics_supplier_docs ADD COLUMN subject VARCHAR(30)"))
            c.execute(_text("DELETE FROM logistics_supplier_docs WHERE subject IS NULL OR subject = ''"))


_ensure_supplier_docs_subject_col()


def _ensure_v235_cols():
    """V2.235：档案表补 verify_note（异议定稿说明）；附件表补 kind（发票/结算账单，存量默认发票）。"""
    from sqlalchemy import text as _text
    with _engine.begin() as c:
        def _cols(t):
            return [r[1] for r in c.execute(_text(f"PRAGMA table_info({t})")).fetchall()] \
                if DB_URL.startswith("sqlite") else \
                [r[0] for r in c.execute(_text(
                    f"SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='{t}'")).fetchall()]
        sc = _cols("logistics_supplier_docs")
        if sc and "verify_note" not in sc:
            c.execute(_text("ALTER TABLE logistics_supplier_docs ADD COLUMN verify_note TEXT"))
        fc = _cols("logistics_invoice_files")
        if fc and "kind" not in fc:
            c.execute(_text("ALTER TABLE logistics_invoice_files ADD COLUMN kind VARCHAR(20)"))
            c.execute(_text("UPDATE logistics_invoice_files SET kind='发票' WHERE kind IS NULL OR kind=''"))


_ensure_v235_cols()


def submit_bill_upload(uid, operator):
    """物流部「提交给核算组」（V2.225）：批次标记已提交。返回批次概要（通知文案用）；无此批次→None。"""
    with _engine.begin() as c:
        r = c.execute(select(bill_uploads).where(bill_uploads.c.id == int(uid))).mappings().first()
        if not r:
            return None
        c.execute(update(bill_uploads).where(bill_uploads.c.id == int(uid))
                  .values(status="已提交", submitted_by=str(operator or ""), submitted_at=_now()))
    return {"id": r["id"], "year": r["year"], "month": r["month"], "operator": r["operator"], "ts": r["ts"],
            "stats": json.loads(r["stats"] or "{}"), "per_file": json.loads(r["per_file"] or "[]")}


def save_bill_upload(year, month, operator, stats, per_file, rows, tickets):
    """账单解析成功即留痕一批（同月多批并存，最新在前；活表行/票级全量入库供恢复现场）。"""
    with _engine.begin() as c:
        r = c.execute(insert(bill_uploads).values(
            year=int(year), month=int(month), operator=str(operator or ""), ts=_now(),
            stats=json.dumps(stats or {}, ensure_ascii=False),
            per_file=json.dumps(per_file or [], ensure_ascii=False),
            rows_json=json.dumps(rows or [], ensure_ascii=False),
            tickets_json=json.dumps(tickets or [], ensure_ascii=False)))
        return r.inserted_primary_key[0]


def list_bill_uploads(year, month):
    """某月上传批次（轻列表，不含大字段），最新在前。"""
    with _engine.connect() as c:
        rows = c.execute(select(bill_uploads.c.id, bill_uploads.c.operator, bill_uploads.c.ts,
                                bill_uploads.c.stats, bill_uploads.c.per_file,
                                bill_uploads.c.status, bill_uploads.c.submitted_by, bill_uploads.c.submitted_at)
                         .where(bill_uploads.c.year == int(year), bill_uploads.c.month == int(month))
                         .order_by(bill_uploads.c.id.desc())).mappings().all()
    out = []
    for r in rows:
        out.append({"id": r["id"], "operator": r["operator"], "ts": r["ts"],
                    "stats": json.loads(r["stats"] or "{}"), "per_file": json.loads(r["per_file"] or "[]"),
                    "status": r.get("status") or "", "submitted_by": r.get("submitted_by") or "",
                    "submitted_at": r.get("submitted_at") or ""})
    return out


def get_bill_upload(uid):
    """载入一批（含活表行全量）。"""
    with _engine.connect() as c:
        r = c.execute(select(bill_uploads).where(bill_uploads.c.id == int(uid))).mappings().first()
    if not r:
        return None
    return {"id": r["id"], "year": r["year"], "month": r["month"], "operator": r["operator"], "ts": r["ts"],
            "stats": json.loads(r["stats"] or "{}"), "per_file": json.loads(r["per_file"] or "[]"),
            "rows": json.loads(r["rows_json"] or "[]"), "tickets": json.loads(r["tickets_json"] or "[]")}


def update_bill_upload_rows(uid, rows):
    """活表改动写回批次（V2.223 自动保存）：改一行即全量回写 rows_json，并按行重算 stats 的待人工口径——
    下次「载入这批」拿到的就是改过的现场，不再回退到解析原始值。"""
    rows = rows or []
    pend = [r for r in rows if not r.get("费用归属")]
    with _engine.begin() as c:
        cur = c.execute(select(bill_uploads.c.stats).where(bill_uploads.c.id == int(uid))).first()
        if not cur:
            return False
        st = json.loads(cur[0] or "{}")
        st["明细行数"] = len(rows)
        st["待人工行"] = len(pend)
        st["待人工金额"] = round(sum(float(r.get("含税") or 0) for r in pend), 2)
        c.execute(update(bill_uploads).where(bill_uploads.c.id == int(uid))
                  .values(rows_json=json.dumps(rows, ensure_ascii=False),
                          stats=json.dumps(st, ensure_ascii=False)))
    return True


def bill_upload_periods(year):
    """{期间: 批次数}——月份胶囊「已上传」态用。"""
    from sqlalchemy import func
    with _engine.connect() as c:
        rows = c.execute(select(bill_uploads.c.month, func.count(bill_uploads.c.id))
                         .where(bill_uploads.c.year == int(year)).group_by(bill_uploads.c.month)).all()
    return {int(m): int(n) for m, n in rows}


def _split_recipients(v):
    """收件人串 → 列表（逗号/分号/中文标点都认）。"""
    return [x.strip() for x in str(v or "").replace("；", ";").replace("，", ";").replace(",", ";").split(";") if x.strip()]


def list_notify_recipients():
    with _engine.connect() as c:
        rows = c.execute(select(notify_recipients)).mappings().all()
    return [dict(r) for r in rows]


def notify_recipients_map():
    """{场景: {mobiles:[...], emails:[...]}}——notifier 按场景解析收件人用。"""
    return {r["scene"]: {"mobiles": _split_recipients(r["mobiles"]), "emails": _split_recipients(r["emails"])}
            for r in list_notify_recipients()}


def save_notify_recipients(scene, mobiles, emails, operator):
    scene = str(scene or "").strip()
    if not scene:
        raise ValueError("缺场景")
    vals = dict(mobiles=str(mobiles or "").strip(), emails=str(emails or "").strip(),
                updated_by=str(operator or ""), updated_at=_now())
    with _engine.begin() as c:
        r = c.execute(select(notify_recipients.c.id).where(notify_recipients.c.scene == scene)).first()
        if r:
            c.execute(update(notify_recipients).where(notify_recipients.c.id == r[0]).values(**vals))
        else:
            c.execute(insert(notify_recipients).values(scene=scene, **vals))


def get_supplier_docs(year, month):
    """{简称: {付款主体: 档案行dict}}——供应商月度生命周期（V2.232 分主体：发票抬头/付款按主体走）。"""
    with _engine.connect() as c:
        rows = c.execute(select(supplier_docs).where(
            supplier_docs.c.year == int(year), supplier_docs.c.month == int(month))).mappings().all()
    out = {}
    for r in rows:
        out.setdefault(r["short"], {})[r["subject"] or "待补"] = dict(r)
    return out


def upsert_supplier_bills(year, month, short, groups, files, operator, cleanup=True):
    """解析上传自动落"初始账单"档（一商多主体，V2.232）。groups={主体:{票数,金额}}；同一份账单文件挂到该商全部主体行。
    重传=新版本：写到的主体行定稿作废（须重新确认），发票登记保留；cleanup=True 时本版没有的主体行、
    又没登过发票的 → 清掉（存量回填走 cleanup=False，只补缺不动别的行）。"""
    now = _now()
    subs = {str(s or "待补") for s in groups}
    with _engine.begin() as c:
        olds = c.execute(select(supplier_docs).where(
            supplier_docs.c.year == int(year), supplier_docs.c.month == int(month),
            supplier_docs.c.short == str(short))).mappings().all()
        by_sub = {r["subject"] or "待补": r for r in olds}
        for sub, g in groups.items():
            sub = str(sub or "待补")
            vals = dict(bill_file=str(files or ""), bill_ts=now, bill_by=str(operator or ""),
                        bill_amount=round(float(g.get("金额") or 0), 2), bill_tickets=int(g.get("票数") or 0),
                        verified=0, verified_ts="", verified_by="", verified_amount=None)
            old = by_sub.get(sub)
            if old:
                c.execute(update(supplier_docs).where(supplier_docs.c.id == old["id"]).values(**vals))
            else:
                c.execute(insert(supplier_docs).values(year=int(year), month=int(month), short=str(short),
                                                       subject=sub, pay_requested=0, **vals))
        if cleanup:
            for sub, old in by_sub.items():   # 新版本没有的主体行：没登发票就清（避免僵尸行占矩阵）
                if sub not in subs and not (old["invoice_no"] or "").strip():
                    c.execute(delete(supplier_docs).where(supplier_docs.c.id == old["id"]))


def set_supplier_doc(year, month, short, subject, action, operator, invoice_no="", invoice_amount=None,
                     invoice_date="", final_amount=None, verify_note=""):
    """档案状态推进（主体×供应商粒度）：verify定稿（final_amount 有值=按异议手登正确金额，计提行不动、差异次月冲）/
    unverify撤定稿 / invoice登记发票 / clear_invoice清发票 / pay标记已发起付款 / unpay撤销付款标记。返回 (ok, msg)。"""
    subject = str(subject or "待补")
    with _engine.begin() as c:
        r = c.execute(select(supplier_docs).where(
            supplier_docs.c.year == int(year), supplier_docs.c.month == int(month),
            supplier_docs.c.short == str(short), supplier_docs.c.subject == subject)).mappings().first()
        if action == "verify":
            if not r or not r["bill_file"]:
                return False, "这家（该主体）还没有初始账单，不能定稿"
            amt = r["bill_amount"]
            if final_amount is not None and str(final_amount) != "":
                try:
                    amt = round(float(final_amount), 2)
                except (TypeError, ValueError):
                    return False, "定稿金额要填数字"
            vals = dict(verified=1, verified_ts=_now(), verified_by=str(operator or ""),
                        verified_amount=amt, verify_note=str(verify_note or ""))
        elif action == "unverify":
            vals = dict(verified=0, verified_ts="", verified_by="", verified_amount=None, verify_note="")
        elif action == "invoice":
            no = str(invoice_no or "").strip()
            if not no:
                return False, "票号不能为空"
            try:
                amt = round(float(invoice_amount), 2)
            except (TypeError, ValueError):
                return False, "发票金额要填数字"
            vals = dict(invoice_no=no, invoice_amount=amt, invoice_date=str(invoice_date or ""),
                        invoice_ts=_now(), invoice_by=str(operator or ""))
        elif action == "clear_invoice":
            vals = dict(invoice_no="", invoice_amount=None, invoice_date="", invoice_ts="", invoice_by="")
        elif action == "pay":
            vals = dict(pay_requested=1, pay_ts=_now())
        elif action == "unpay":
            vals = dict(pay_requested=0, pay_ts="")
        else:
            return False, f"不认识的动作：{action}"
        if r:
            c.execute(update(supplier_docs).where(supplier_docs.c.id == r["id"]).values(**vals))
        else:
            c.execute(insert(supplier_docs).values(year=int(year), month=int(month), short=str(short),
                                                   subject=subject, verified=0, pay_requested=0, **vals))
    return True, ""


def list_invoice_files(year, month):
    """{(简称, 主体): [{id, kind, filename, ts, uploaded_by}]}——结算凭证附件清单（不含内容）。"""
    with _engine.connect() as c:
        rows = c.execute(select(invoice_files.c.id, invoice_files.c.short, invoice_files.c.subject,
                                invoice_files.c.kind, invoice_files.c.filename, invoice_files.c.ts,
                                invoice_files.c.uploaded_by)
                         .where(invoice_files.c.year == int(year), invoice_files.c.month == int(month))
                         .order_by(invoice_files.c.id)).mappings().all()
    out = {}
    for r in rows:
        out.setdefault((r["short"], r["subject"] or "待补"), []).append(
            {"id": r["id"], "kind": r["kind"] or "发票", "filename": r["filename"],
             "ts": r["ts"], "uploaded_by": r["uploaded_by"]})
    return out


def save_invoice_file(year, month, short, subject, filename, content, operator, kind="发票"):
    with _engine.begin() as c:
        r = c.execute(insert(invoice_files).values(
            year=int(year), month=int(month), short=str(short), subject=str(subject or "待补"),
            kind=str(kind or "发票"), filename=str(filename or "发票"), content=content,
            ts=_now(), uploaded_by=str(operator or "")))
        return r.inserted_primary_key[0]


def get_invoice_file(fid):
    with _engine.connect() as c:
        r = c.execute(select(invoice_files).where(invoice_files.c.id == int(fid))).mappings().first()
    return dict(r) if r else None


def delete_invoice_file(fid):
    with _engine.begin() as c:
        c.execute(delete(invoice_files).where(invoice_files.c.id == int(fid)))


def _latest_bill_upload_with_rows(year, month):
    """本月最新"有明细"的批次全量（空批次=解析失败留痕，跳过）。返回 (批次dict, status) 或 (None, '')。"""
    for u in list_bill_uploads(year, month):
        if (u["stats"] or {}).get("明细行数") or (u["stats"] or {}).get("票数"):
            return get_bill_upload(u["id"]), (u.get("status") or "")
    return None, ""


def merge_bill_upload(year, month, operator, rows, per_file, tickets):
    """按供应商合并进本月工作批次（V2.229 生命周期）：本次解析的商覆盖旧行，别家行原样保留——
    账单到几家传几家、晚到的补传，互不影响。最新批次未提交→原地更新；已提交/不存在→带存量另起新批次
    （提交快照不动，核算组载入永远拿最新）。返回 (uid, 合并后 {rows,per_file,stats,tickets})。"""
    rows, per_file, tickets = rows or [], per_file or [], tickets or []
    shorts = {r.get("物流商") for r in rows} | {p.get("物流商") for p in per_file if p.get("物流商")}
    shorts -= {None, ""}
    new_files = {p.get("文件") for p in per_file}
    base, base_status = _latest_bill_upload_with_rows(year, month)
    if base:
        rows = [r for r in base["rows"] if r.get("物流商") not in shorts] + rows
        per_file = [p for p in base["per_file"]
                    if p.get("物流商") not in shorts and p.get("文件") not in new_files] + per_file
        tickets = [t for t in base["tickets"] if t.get("物流商") not in shorts] + tickets
    pend = [r for r in rows if not r.get("费用归属")]
    stats = {"文件数": len(per_file), "票数": sum(int(p.get("票数") or 0) for p in per_file),
             "含税合计": round(sum(float(r.get("含税") or 0) for r in rows), 2),
             "明细行数": len(rows), "待人工行": len(pend),
             "待人工金额": round(sum(float(r.get("含税") or 0) for r in pend), 2)}
    if base and base_status != "已提交":
        with _engine.begin() as c:
            c.execute(update(bill_uploads).where(bill_uploads.c.id == base["id"]).values(
                operator=str(operator or ""), ts=_now(),
                stats=json.dumps(stats, ensure_ascii=False),
                per_file=json.dumps(per_file, ensure_ascii=False),
                rows_json=json.dumps(rows, ensure_ascii=False),
                tickets_json=json.dumps(tickets, ensure_ascii=False)))
        uid = base["id"]
    else:
        uid = save_bill_upload(year, month, operator, stats, per_file, rows, tickets)
    return uid, {"rows": rows, "per_file": per_file, "stats": stats, "tickets": tickets}


def logistics_posted_periods(year):
    """{期间: 已录凭证张数}——物流计提月份状态胶囊用（V2.219）。"""
    from sqlalchemy import func
    with _engine.connect() as c:
        rows = c.execute(select(post_log.c.period, func.count(post_log.c.id))
                         .where(post_log.c.year == int(year)).group_by(post_log.c.period)).all()
    return {int(p): int(n) for p, n in rows}


def list_logi_suppliers():
    return _list_table(logi_suppliers, logi_suppliers.c.channel, logi_suppliers.c.short)


def save_logi_supplier(row, operator):
    short = str(row.get("short", "") or "").strip()
    if not short:
        raise ValueError("简称不能为空（简称是唯一查找键）")
    vals = dict(full=str(row.get("full", "") or "").strip(), kd_code=str(row.get("kd_code", "") or "").strip(),
                channel=str(row.get("channel", "") or "线下").strip(), note=str(row.get("note", "") or "").strip(),
                updated_by=str(operator or ""), updated_at=_now())
    with _engine.begin() as c:
        r = c.execute(select(logi_suppliers.c.id).where(logi_suppliers.c.short == short)).first()
        if r:
            c.execute(update(logi_suppliers).where(logi_suppliers.c.id == r[0]).values(**vals))
            return r[0]
        return c.execute(insert(logi_suppliers).values(short=short, **vals)).inserted_primary_key[0]


def delete_logi_supplier(rid):
    with _engine.begin() as c:
        c.execute(delete(logi_suppliers).where(logi_suppliers.c.id == int(rid)))


def list_type_map():
    return _list_table(type_map, type_map.c.pattern)


def save_type_map(row, operator):
    pattern = str(row.get("pattern", "") or "").strip()
    if not pattern:
        raise ValueError("标注原文不能为空")
    vals = dict(fee=str(row.get("fee", "") or "").strip(), bizline=str(row.get("bizline", "") or "").strip(),
                descr=str(row.get("descr", "") or "").strip(),
                updated_by=str(operator or ""), updated_at=_now())
    with _engine.begin() as c:
        r = c.execute(select(type_map.c.id).where(type_map.c.pattern == pattern)).first()
        if r:
            c.execute(update(type_map).where(type_map.c.id == r[0]).values(**vals))
            return r[0]
        return c.execute(insert(type_map).values(pattern=pattern, **vals)).inserted_primary_key[0]


def delete_type_map(rid):
    with _engine.begin() as c:
        c.execute(delete(type_map).where(type_map.c.id == int(rid)))


def type_map_lookup():
    """{标注原文: (fee, bizline, descr)}"""
    return {r["pattern"]: (r["fee"], r["bizline"] or "", r["descr"] or "") for r in list_type_map()}


# 种子（表空才播；账证/批注实证，见重构方案 v2.1）
_FEE_SEED = [
    # (fee, subject, bizline, account, dept, item, sword, manual)
    ("销售出库费用", "", "", "6601 销售费用", "永续物流中心", "出库运费", "出库运费", 0),
    ("销售出库费用", "深圳星期九", "", "6601 销售费用", "永续供应中心", "出库运费", "出库运费", 0),   # A期实证例外
    ("成品入库费用", "", "", "6401 主营业务成本", "仓储物流部", "入库运费", "成品入库运费", 0),
    ("原料入库费用", "", "", "5101 制造费用", "仓储物流部", "入库运费", "原料入库运费", 0),
    ("原料入库费用", "", "小料", "5101 制造费用", "茶饮小料部", "入库运费", "原料入库运费", 0),      # 业务方批注确认例外
    ("成品仓储费用", "", "", "6601 销售费用", "永续物流中心", "货物仓储费", "成品仓储费", 0),
    ("成品仓储费用", "孝感星期九", "小料", "6601 销售费用", "仓储物流部", "货物仓储费", "成品仓储费", 0),  # 账证:孝感线下外仓(易风达)
    ("原料仓储费用", "", "", "5101 制造费用", "仓储物流部", "货物仓储费", "原料仓储费", 0),
    ("成品调拨费用", "", "", "6601 销售费用", "永续物流中心", "出库运费", "调拨运费", 0),
    ("原料调拨费用", "", "", "5101 制造费用", "仓储物流部", "入库运费", "调拨运费", 0),
    ("出库装卸费用", "", "", "6601 销售费用", "永续物流中心", "出库运费", "装卸费", 0),
    ("成品入库装卸费用", "", "", "6401 主营业务成本", "仓储物流部", "入库运费", "装卸费", 0),
    ("原料入库装卸费用", "", "", "5101 制造费用", "仓储物流部", "入库运费", "装卸费", 0),
    ("研发设备采购", "", "", "6604 研发费用", "永续研发中心", "研发外购", "研发运费", 0),
    ("设备调拨费用", "", "", "", "", "搬运费", "设备调拨运费", 1),        # 🖐科目部门逐笔人工(业务方批注)
    ("其它", "", "", "", "", "", "费用", 1),                              # 🖐科目待财务定
]
_BIZ_SEED = [  # (name, cpfl, cpxm)  cpxm 非空=该业务线费用行挂产品项目(账证:鲜食入库也挂)
    ("植物肉", "CPFL007", ""), ("鲜食", "CPFL010", "CPXM017"), ("零售", "CPFL011", ""),
    ("小料", "CPFL009", ""), ("豆蛋制品", "CPFL008", ""), ("电商", "CPFL002", ""),
    ("山姆零售", "CPFL011", "CPXM017"), ("kikiherb", "CPFL013", "CPXM022"),   # 账证实证 2026-06 顺鸽
    ("海外", "", ""), ("—", "", ""),
]
_TYPE_SEED = [  # (pattern, fee, bizline, descr)  2026-07 账单包 24 distinct 标注
    ("销售单-植物肉", "销售出库费用", "植物肉", ""), ("销售单-豆蛋制品", "销售出库费用", "豆蛋制品", ""),
    ("销售单-小料", "销售出库费用", "小料", ""), ("销售单-零售其他", "销售出库费用", "零售", ""),
    ("零售其他销售单", "销售出库费用", "零售", ""), ("植物肉销售单", "销售出库费用", "植物肉", ""),
    ("山姆零售销售", "销售出库费用", "山姆零售", ""),
    ("样品-小料", "销售出库费用", "小料", "样品"), ("样品-植物肉", "销售出库费用", "植物肉", "样品"),
    ("样品-豆蛋制品", "销售出库费用", "豆蛋制品", "样品"), ("样品-零售", "销售出库费用", "零售", "样品"),
    ("小料样品", "销售出库费用", "小料", "样品"), ("植物肉样品", "销售出库费用", "植物肉", "样品"),
    ("豆蛋制品样品", "销售出库费用", "豆蛋制品", "样品"),
    ("原料入库", "原料入库费用", "", ""), ("原料入库-小料", "原料入库费用", "小料", ""),
    ("原料入库-植物肉", "原料入库费用", "植物肉", ""),
    ("成品调拨-植物肉", "成品调拨费用", "植物肉", ""),
    ("研发中试", "研发设备采购", "", "研发中试"), ("研发费用", "研发设备采购", "", "研发费用"),
    ("山姆送仓", "销售出库费用", "山姆零售", "山姆送仓"),
    ("仓储费·玉湖仓", "成品仓储费用", "小料", "仓储费·玉湖仓"),   # 易风达仓存小料成品,账证挂CPFL009
    ("仓储费·山绿仓", "成品仓储费用", "小料", "仓储费·山绿仓"),
]

# 标准标注规范（V2.237 业务方定）：物流部填单号 VLOOKUP 出单据类型后，从这套受控清单里选标注写进账单。
# 命名法＝费用类型-业务线(-产品线)；仅设备调拨/研发两类允许自由后缀（-需求部门 / -项目名，规则兜底认前缀）。
# 表非空也会补插缺的行（_ensure_type_map_std）——基础数据·标注翻译表 是唯一事实源，核算组可继续增改。
_TYPE_STD = [  # (pattern, fee, bizline, descr)
    # 销售出库单-业务线
    ("销售出库单-植物肉", "销售出库费用", "植物肉", ""), ("销售出库单-鲜食", "销售出库费用", "鲜食", ""),
    ("销售出库单-零售", "销售出库费用", "零售", ""), ("销售出库单-小料", "销售出库费用", "小料", ""),
    ("销售出库单-豆蛋制品", "销售出库费用", "豆蛋制品", ""), ("销售出库单-电商", "销售出库费用", "电商", ""),
    ("销售出库单-零售-山姆", "销售出库费用", "山姆零售", ""), ("销售出库单-山姆", "销售出库费用", "山姆零售", ""),
    ("销售出库单-kikiherb", "销售出库费用", "kikiherb", ""), ("销售出库单-海外", "销售出库费用", "海外", ""),
    # 其它出库单-业务线（计提不分性质，费用仍走销售出库；摘要标"其它出库"）
    ("其它出库单-植物肉", "销售出库费用", "植物肉", "其它出库"), ("其它出库单-鲜食", "销售出库费用", "鲜食", "其它出库"),
    ("其它出库单-零售", "销售出库费用", "零售", "其它出库"), ("其它出库单-小料", "销售出库费用", "小料", "其它出库"),
    ("其它出库单-豆蛋制品", "销售出库费用", "豆蛋制品", "其它出库"), ("其它出库单-电商", "销售出库费用", "电商", "其它出库"),
    # 成品入库单-业务线
    ("成品入库单-植物肉", "成品入库费用", "植物肉", ""), ("成品入库单-鲜食", "成品入库费用", "鲜食", ""),
    ("成品入库单-零售", "成品入库费用", "零售", ""), ("成品入库单-小料", "成品入库费用", "小料", ""),
    ("成品入库单-豆蛋制品", "成品入库费用", "豆蛋制品", ""), ("成品入库单-零售-山姆", "成品入库费用", "山姆零售", ""),
    # 原料入库单（小料→茶饮小料部/非小料→仓储物流部 由映射维表自动）
    ("原料入库单-小料", "原料入库费用", "小料", ""), ("原料入库单-植物肉", "原料入库费用", "植物肉", ""),
    ("原料入库单-豆蛋制品", "原料入库费用", "豆蛋制品", ""), ("原料入库单", "原料入库费用", "", ""),
    # 仓储
    ("成品仓储-植物肉", "成品仓储费用", "植物肉", ""), ("成品仓储-小料", "成品仓储费用", "小料", ""),
    ("成品仓储-零售", "成品仓储费用", "零售", ""), ("成品仓储-豆蛋制品", "成品仓储费用", "豆蛋制品", ""),
    ("原料仓储", "原料仓储费用", "", ""), ("原料仓储-小料", "原料仓储费用", "小料", ""),
    # 调拨
    ("成品调拨单-植物肉", "成品调拨费用", "植物肉", ""), ("成品调拨单-鲜食", "成品调拨费用", "鲜食", ""),
    ("成品调拨单-零售", "成品调拨费用", "零售", ""), ("成品调拨单-小料", "成品调拨费用", "小料", ""),
    ("成品调拨单-豆蛋制品", "成品调拨费用", "豆蛋制品", ""),
    ("原料调拨单", "原料调拨费用", "", ""), ("原料调拨单-小料", "原料调拨费用", "小料", ""),
    # 装卸
    ("出库装卸", "出库装卸费用", "", ""), ("成品入库装卸", "成品入库装卸费用", "", ""),
    ("原料入库装卸", "原料入库装卸费用", "", ""),
    # 设备/研发/其它（设备调拨须在标注后接-需求部门，如"设备调拨-永续研发中心"——规则认前缀；科目部门逐笔人工）
    ("设备调拨", "设备调拨费用", "", ""), ("研发设备采购", "研发设备采购", "", ""),
    ("其它", "其它", "", ""),
]


def _ensure_type_map_std():
    """标准标注补插（V2.237）：表已有数据也把缺的标准行补上——已存在的（含核算组改过的）绝不覆盖。"""
    with _engine.begin() as c:
        have = {r[0] for r in c.execute(select(type_map.c.pattern)).fetchall()}
        for p, f, b, d in _TYPE_STD:
            if p not in have:
                c.execute(insert(type_map).values(pattern=p, fee=f, bizline=b, descr=d,
                                                  updated_by="种子(标注规范)", updated_at=_now()))


# 供应商种子（v1.8 供应商列表 22 家；极鲜达/跨越全名按账证/账单修正——v1.8 转录自截图有误）
_SUP_SEED = [  # (简称, 全名, 渠道)
    ("顺丰冷运", "上海顺丰冷运供应链有限公司", "线下"), ("顺丰速运", "深圳顺丰速运有限公司", "线下"),
    ("跨越物流", "深圳市跨越速运有限公司", "线下"), ("易风达", "武汉易风达冷链物流有限公司", "线下"),
    ("朴朴", "朴朴", "线下"), ("诚煜物流", "武汉诚煜冷藏物流有限公司", "线下"),
    ("金刚物流", "大连金刚冷藏运输有限公司", "线下"), ("链盟", "东莞市链盟供应链有限公司", "线下"),
    ("路凯（13%）", "路凯包装设备租赁（上海）有限公司", "线下"), ("中通快运", "孝感市顺捷物流有限公司", "线下"),
    ("极鲜达", "湖北极鲜达供应链有限责任公司", "线下"), ("丰源", "湖北丰源物流供应链管理有限公司", "线下"),
    ("山姆VMI", "沃尔玛（中国）投资有限公司", "线下"), ("天鹰物流", "湖北天鹰物流有限公司", "线下"),
    ("比翼电商仓", "厦门比翼信息科技有限公司", "线上"), ("顺鸽电商仓", "武汉顺鸽科技有限公司", "线上"),
    ("嵘盛", "广州市嵘盛冷链物流有限公司", "线下"), ("大润发", "大润发", "线下"),
    ("兆驰", "北京兆驰信捷物流有限公司", "线下"), ("中嫄", "上海中嫄物流有限公司", "线下"),
    ("国际货运", "国际货运", "线下"), ("货拉拉", "货拉拉", "线下"),
]

# 税率种子（v1.8 供应商列表税率矩阵口径：键=全名×费用归属；空费用归属=默认档；特例=仓储 6%）
_TAX_SEED = [
    ("湖北丰源物流供应链管理有限公司", "", 0.09),
    ("武汉易风达冷链物流有限公司", "", 0.09),
    ("武汉易风达冷链物流有限公司", "成品仓储费用", 0.06),
    ("武汉易风达冷链物流有限公司", "原料仓储费用", 0.06),
    ("湖北极鲜达供应链有限责任公司", "", 0.09),
    ("湖北极鲜达供应链有限责任公司", "成品仓储费用", 0.06),
    ("湖北极鲜达供应链有限责任公司", "原料仓储费用", 0.06),
    ("深圳市跨越速运有限公司", "", 0.06),
    ("东莞市链盟供应链有限公司", "", 0.09),
    ("上海顺丰冷运供应链有限公司", "", 0.09),
    ("深圳顺丰速运有限公司", "", 0.06),
    ("厦门比翼信息科技有限公司", "", 0.06),
    ("武汉顺鸽科技有限公司", "", 0.06),
    ("湖北天鹰物流有限公司", "", 0.06),
    ("武汉诚煜冷藏物流有限公司", "", 0.09),
]


def seed_logistics_maps():
    """映射维表+税率维表+供应商列表为空时播种（服务器重启幂等）。"""
    with _engine.begin() as c:
        if not c.execute(select(logi_suppliers.c.id).limit(1)).first():
            for short, full, chan in _SUP_SEED:
                c.execute(insert(logi_suppliers).values(short=short, full=full, kd_code="", channel=chan,
                                                        note="", updated_by="种子(v1.8供列)", updated_at=_now()))
        if not c.execute(select(tax_rates.c.id).limit(1)).first():
            for sup, ft, rate in _TAX_SEED:
                c.execute(insert(tax_rates).values(supplier=sup, fee_type=ft, rate=repr(rate),
                                                   updated_by="种子(v1.8矩阵)", updated_at=_now()))
        if not c.execute(select(fee_map.c.id).limit(1)).first():
            for f, s, b, acc, dept, item, sw, manual in _FEE_SEED:
                c.execute(insert(fee_map).values(fee=f, subject=s, bizline=b, account=acc, dept=dept,
                                                 item=item, sword=sw, manual=manual,
                                                 updated_by="种子(账证实证)", updated_at=_now()))
        if not c.execute(select(bizline_dim.c.id).limit(1)).first():
            for n, cpfl, cpxm in _BIZ_SEED:
                c.execute(insert(bizline_dim).values(name=n, cpfl=cpfl, cpxm=cpxm,
                                                     updated_by="种子(账证实证)", updated_at=_now()))
        if not c.execute(select(type_map.c.id).limit(1)).first():
            for p, f, b, d in _TYPE_SEED:
                c.execute(insert(type_map).values(pattern=p, fee=f, bizline=b, descr=d,
                                                  updated_by="种子(7月账单)", updated_at=_now()))


seed_logistics_maps()
_ensure_type_map_std()


# ----------------------------- 主体档案（平台级基础数据） -----------------------------
# 一张表收敛三处口径：凭证归档取 code 作册号首段、物流计提取 book_code 作金蝶 FACCOUNTBOOKID。
# 关键约束：short_name / code / book_code 各自全表唯一；aliases 不得与他人的 short_name/aliases 相撞
#          （撞了就会把两家公司认成一家）。code 一经生成过册号即锁死——册号已印在标签上贴到书脊。
CODE_RE = re.compile(r"^[A-Z0-9]{2,4}$")
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
ORG_SEED = [("深圳星期零", "101"), ("深圳星期九", "105"), ("孝感星期九", "107")]   # 孝感=107 系 V2.29 实证

# 标签纸色板：市售不干胶常见浅色，均可清晰印黑字。颜色只做界面预览与"该买哪种纸"的指引，
# 打印时不出底色——纸本身就是彩色的，再喷一层底既费墨又把黑字压灰。
LABEL_PALETTE = [
    ("白", "#FFFFFF"), ("黄", "#FFF3B0"), ("粉", "#FFD6E0"), ("蓝", "#CFE6FB"),
    ("绿", "#D2F0D8"), ("橙", "#FFDCC0"), ("紫", "#E4D9F7"), ("灰", "#E4E6E9"),
]
_MIN_CONTRAST = 7.0        # 黑字 vs 底色的 WCAG 对比度下限（AAA）——低于它，一米外看不清册号


def _luminance(hex_color):
    """WCAG 相对亮度。"""
    def ch(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def black_text_contrast(hex_color):
    """黑字压在该底色上的对比度（越大越清晰）。"""
    return (_luminance(hex_color) + 0.05) / 0.05


def _ensure_org_color_column():
    """V2.74 建的 orgs 表无 color 列时补上（create_all 不改既有表）。"""
    from sqlalchemy import inspect, text
    try:
        cols = [c["name"] for c in inspect(_engine).get_columns("orgs")]
        if "color" not in cols:
            with _engine.begin() as c:
                c.execute(text("ALTER TABLE orgs ADD COLUMN color VARCHAR(9)"))
    except Exception:
        pass


_ensure_org_color_column()


def _split_aliases(raw):
    """别名 → 去重去空的列表。串（中英文逗号、顿号都认）与列表都收，
    因为 list_orgs() 返回的是列表，前端原样回传时不能再被当成字符串切。"""
    if not raw:
        return []
    items = raw if isinstance(raw, (list, tuple)) else None
    if items is None:
        raw = str(raw)
        for sep in ("，", "、", ";", "；"):
            raw = raw.replace(sep, ",")
        items = raw.split(",")
    out, seen = [], set()
    for a in items:
        a = str(a).strip()
        if a and a not in seen:
            seen.add(a)
            out.append(a)
    return out


def _org_row(r):
    d = dict(r)
    d["aliases"] = _split_aliases(d.get("aliases"))
    d["active"] = str(d.get("active") or "1") == "1"
    d["color"] = (d.get("color") or "").upper()
    return d


def list_orgs():
    with _engine.connect() as c:
        rows = c.execute(select(orgs).order_by(orgs.c.book_code, orgs.c.short_name)).mappings().all()
    return [_org_row(r) for r in rows]


def org_code_locked(short_name):
    """该主体是否已有凭证册在册 —— 有则简码锁死。凭证归档表尚未建时一律返回 False。"""
    try:
        with _engine.connect() as c:
            r = c.exec_driver_sql(
                "SELECT 1 FROM archive_volumes WHERE org = ? LIMIT 1"
                if DB_URL.startswith("sqlite") else
                "SELECT 1 FROM archive_volumes WHERE org = %s LIMIT 1",
                (str(short_name),)).first()
        return bool(r)
    except Exception:
        return False          # 表还不存在（凭证归档未上线）= 不可能有册子 = 不锁


def save_org(data, operator):
    """新增/更新一条主体档案。校验不过抛 ValueError(人话)，由路由转成提示。"""
    oid = data.get("id")
    full_name = str(data.get("full_name") or "").strip()
    short_name = str(data.get("short_name") or "").strip()
    code = str(data.get("code") or "").strip().upper()
    book_code = str(data.get("book_code") or "").strip()
    aliases = _split_aliases(data.get("aliases"))
    color = str(data.get("color") or "").strip().upper()
    active = "1" if data.get("active", True) else "0"
    note = str(data.get("note") or "").strip()

    if not short_name:
        raise ValueError("简称不能为空 —— 界面上到处显示的就是它")
    if code and not CODE_RE.match(code):
        raise ValueError("简码要 2–4 位大写字母或数字（如 SZL），它会印在凭证册标签上")
    if color:
        if not HEX_RE.match(color):
            raise ValueError("颜色要填 #RRGGBB 这样的六位十六进制（如 #FFF3B0）")
        ct = black_text_contrast(color)
        if ct < _MIN_CONTRAST:
            raise ValueError("这个颜色太深，印上去黑字看不清（黑字对比度 %.1f:1，需 ≥ %.0f:1）——"
                             "标签纸请选浅色" % (ct, _MIN_CONTRAST))

    others = [o for o in list_orgs() if o["id"] != oid]
    if any(o["short_name"] == short_name for o in others):
        raise ValueError("简称「%s」已被占用" % short_name)
    if code and any(o["code"] == code for o in others):
        raise ValueError("简码「%s」已被别的主体用了 —— 简码必须唯一，否则册号会撞号" % code)
    if book_code and any(o["book_code"] == book_code for o in others):
        raise ValueError("金蝶账簿代码「%s」已被别的主体用了" % book_code)
    dup = next((o for o in others if color and o["color"] == color and o["active"]), None)
    if dup:
        raise ValueError("颜色已被主体「%s」用了 —— 两家用同一种标签纸就分不出来了" % dup["short_name"])
    # 别名不得撞上他人的简称或别名，否则归一化会把两家公司认成一家
    taken = {}
    for o in others:
        taken[o["short_name"]] = o["short_name"]
        for a in o["aliases"]:
            taken[a] = o["short_name"]
    for a in aliases:
        if a in taken:
            raise ValueError("别名「%s」已归属主体「%s」，不能重复" % (a, taken[a]))
        if a == short_name:
            raise ValueError("别名不必再写一遍自己的简称")

    if oid:                                    # 更新：简码锁死校验
        cur = next((o for o in list_orgs() if o["id"] == oid), None)
        if cur and cur["code"] and cur["code"] != code and org_code_locked(cur["short_name"]):
            raise ValueError("主体「%s」已有凭证册在册，简码已锁定不可改 —— 册号已经印在标签上贴到书脊了"
                             % cur["short_name"])

    vals = dict(full_name=full_name, short_name=short_name, code=code, book_code=book_code,
                aliases=",".join(aliases), color=color, active=active, note=note,
                updated_by=str(operator or ""), updated_at=_now())
    with _engine.begin() as c:
        if oid:
            c.execute(update(orgs).where(orgs.c.id == int(oid)).values(**vals))
            return int(oid)
        return c.execute(insert(orgs).values(**vals)).inserted_primary_key[0]


def delete_org(oid):
    o = next((x for x in list_orgs() if x["id"] == int(oid)), None)
    if o and org_code_locked(o["short_name"]):
        raise ValueError("主体「%s」已有凭证册在册，不能删除" % o["short_name"])
    with _engine.begin() as c:
        c.execute(delete(orgs).where(orgs.c.id == int(oid)))


def book_code_lookup():
    """{主体写法: 金蝶账簿代码}。简称与每个别名都指向同一个 book_code —— 台账写「星期零」也能取到 101。
    停用主体不入表。表为空/缺主体时调用方应【拒绝录入并报错】，绝不回退硬编码（两份真相比一份缺失更危险）。"""
    out = {}
    for o in list_orgs():
        if not o["active"] or not o["book_code"]:
            continue
        out[o["short_name"]] = o["book_code"]
        for a in o["aliases"]:
            out[a] = o["book_code"]
    return out


def org_by_alias(name):
    """按简称或别名找主体行；找不到返回 None。"""
    name = str(name or "").strip()
    for o in list_orgs():
        if name == o["short_name"] or name in o["aliases"]:
            return o
    return None


# ----------------------------- 凭证归档：位置树 -----------------------------
def _loc_path(locs_by_id, lid):
    """从根到该节点的完整路径字符串，如 档案室B › 3号柜 › 第2层。"""
    parts, seen = [], set()
    while lid and lid not in seen:
        seen.add(lid)
        n = locs_by_id.get(lid)
        if not n:
            break
        parts.append(n["name"])
        lid = n["parent_id"]
    return " › ".join(reversed(parts))


def list_locations():
    with _engine.connect() as c:
        rows = [dict(r) for r in c.execute(select(arch_locations)).mappings().all()]
    by_id = {r["id"]: r for r in rows}
    # 每个位置节点上现挂多少本册子（含子节点递归）
    with _engine.connect() as c:
        cnt = c.execute(select(arch_volumes.c.loc_id).where(
            arch_volumes.c.status != "已销毁")).mappings().all()
    direct = {}
    for r in cnt:
        direct[r["loc_id"]] = direct.get(r["loc_id"], 0) + 1
    for r in rows:
        r["path"] = _loc_path(by_id, r["id"])
        r["terminal"] = str(r.get("terminal") or "0") == "1"
        r["count_direct"] = direct.get(r["id"], 0)
    return sorted(rows, key=lambda r: r["path"])


def save_location(data, operator):
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("位置名称不能为空")
    ntype = str(data.get("ntype") or "").strip() or "其它"
    parent_id = data.get("parent_id") or None
    terminal = "1" if data.get("terminal") else "0"
    lid = data.get("id")
    vals = dict(name=name, ntype=ntype, parent_id=parent_id, terminal=terminal)
    with _engine.begin() as c:
        if lid:
            c.execute(update(arch_locations).where(arch_locations.c.id == int(lid)).values(**vals))
            return int(lid)
        vals.update(created_by=str(operator or ""), created_at=_now())
        return c.execute(insert(arch_locations).values(**vals)).inserted_primary_key[0]


def _loc_node(lid):
    if not lid:
        return None
    with _engine.connect() as c:
        r = c.execute(select(arch_locations).where(arch_locations.c.id == int(lid))).mappings().first()
    return dict(r) if r else None


def ensure_location_path(path, operator):
    """把「档案室B › 3号柜 › 第2层」这样的路径逐级建/找出来，返回最末节点 id。
    分隔符 › / > 都认。节点类型按层级顺序猜（库房/柜/层/箱），认不出就留空。批量导入建位置树用。"""
    if not path or not str(path).strip():
        return None
    raw = str(path)
    for sep in ("›", ">", "»", "/"):
        raw = raw.replace(sep, "\n")
    parts = [p.strip() for p in raw.split("\n") if p.strip()]
    if not parts:
        return None
    guess = ["库房", "柜", "层", "箱"]
    parent = None
    for i, name in enumerate(parts):
        with _engine.begin() as c:
            q = select(arch_locations.c.id).where(arch_locations.c.name == name)
            q = q.where(arch_locations.c.parent_id == parent) if parent else q.where(arch_locations.c.parent_id.is_(None))
            hit = c.execute(q).first()
            if hit:
                parent = hit[0]
            else:
                parent = c.execute(insert(arch_locations).values(
                    name=name, ntype=guess[i] if i < len(guess) else "其它",
                    parent_id=parent, terminal="0",
                    created_by=str(operator or ""), created_at=_now())).inserted_primary_key[0]
    return parent


# ----------------------------- 凭证归档：凭证册 -----------------------------
def _vol_row(r, locs_by_id):
    from kernels import archive as _arch
    d = dict(r)
    node = locs_by_id.get(d.get("loc_id"))
    d["loc_path"] = _loc_path(locs_by_id, d.get("loc_id")) if node else ""
    d["loc_ntype"] = node.get("ntype") if node else ""
    d["display_status"] = _arch.display_status(d.get("status"), node.get("ntype") if node else None)
    return d


def list_volumes(org=None, year=None, status=None):
    q = select(arch_volumes)
    if org:
        q = q.where(arch_volumes.c.org == org)
    if year:
        q = q.where(arch_volumes.c.year == int(year))
    if status:
        q = q.where(arch_volumes.c.status == status)
    with _engine.connect() as c:
        rows = c.execute(q.order_by(arch_volumes.c.org, arch_volumes.c.year,
                                    arch_volumes.c.month, arch_volumes.c.seq)).mappings().all()
        locs = {r["id"]: dict(r) for r in c.execute(select(arch_locations)).mappings().all()}
    return [_vol_row(r, locs) for r in rows]


def _period_ranges(org, year, month, exclude_vol=None):
    """该主体该期间已登记册子的 [(起,止)...] 与已用册序，供号段校验/册序推算。"""
    with _engine.connect() as c:
        rows = c.execute(select(arch_volumes.c.vol_no, arch_volumes.c.no_from,
                                arch_volumes.c.no_to, arch_volumes.c.seq).where(
            arch_volumes.c.org == org, arch_volumes.c.year == int(year),
            arch_volumes.c.month == int(month))).mappings().all()
    ranges = [(r["no_from"], r["no_to"]) for r in rows if r["vol_no"] != exclude_vol]
    seqs = [r["seq"] for r in rows if r["vol_no"] != exclude_vol]
    return ranges, seqs


def register_volume(data, operator):
    """登记一本新册。自动生成册号、算册序/张数/到期，登记前做号段校验。"""
    from kernels import archive as _arch
    org = str(data.get("org") or "").strip()
    o = next((x for x in list_orgs() if x["short_name"] == org), None)
    if not o:
        raise ValueError("主体「%s」不在档案里" % org)
    if not o["active"]:
        raise ValueError("主体「%s」已停用，不能登记新册" % org)
    if not o["code"]:
        raise ValueError("主体「%s」还没有简码，先去「基础数据 › 主体档案」补上" % org)
    year, month = int(data.get("year")), int(data.get("month"))
    try:
        no_from, no_to = int(data.get("no_from")), int(data.get("no_to"))
    except (TypeError, ValueError):
        raise ValueError("凭证号起止都要填数字")

    ranges, seqs = _period_ranges(org, year, month)
    ok, probs = _arch.check_range(ranges, no_from, no_to)
    if not ok:
        raise ValueError("；".join(probs))

    seq = _arch.next_seq(seqs)
    vol_no = _arch.make_vol_no(o["code"], year, month, seq)
    loc_id = data.get("loc_id") or None
    vals = dict(vol_no=vol_no, org=org, year=year, month=month,
                vtype=str(data.get("vtype") or _arch.VTYPE_DEFAULT), seq=seq,
                no_from=no_from, no_to=no_to, sheets=no_to - no_from + 1,
                status="在库", loc_id=loc_id, keep_until=_arch.keep_until(year),
                note=str(data.get("note") or ""),
                created_by=str(operator or ""), created_at=_now(),
                updated_by=str(operator or ""), updated_at=_now())
    with _engine.begin() as c:
        c.execute(insert(arch_volumes).values(**vals))
    if loc_id:
        _log_transfer("登记入库", loc_id, [(vol_no, None, loc_id)], operator)
    return vol_no


def import_volumes(rows, operator):
    """Excel 批量导入（期初存量）。rows = [{主体,年,月,凭证号起,凭证号止,存放位置,备注}...]。
    逐行校验号段（同一批内先到先占、也和库里已有比），自动生成册号、建位置树。
    返回 {ok, 成功[], 失败[{行, 原因}], 建位置数}。整批不做事务——成功的先落库，失败的报出来让人改后重传。"""
    from kernels import archive as _arch
    orgs_by_name = {}
    for o in list_orgs():
        orgs_by_name[o["short_name"]] = o
        for a in o["aliases"]:
            orgs_by_name[a] = o
    # 每行成功即提交，故库里的 _period_ranges 天然累积本批前面的行——号段校验与册序直接读库即可，
    # 不必再维护批内缓存（失败行不提交、不占号，自然正确）。
    ok_list, err_list, loc_before = [], [], len(list_locations())
    for idx, row in enumerate(rows, start=1):
        try:
            name = str(row.get("主体") or "").strip()
            o = orgs_by_name.get(name)
            if not o:
                raise ValueError("主体「%s」不在档案里（简称或别名都没匹配上）" % name)
            if not o["active"]:
                raise ValueError("主体「%s」已停用" % name)
            if not o["code"]:
                raise ValueError("主体「%s」还没有简码，先去主体档案补" % name)
            year, month = int(row.get("年")), int(row.get("月"))
            no_from, no_to = int(row.get("凭证号起")), int(row.get("凭证号止"))
            db_ranges, db_seqs = _period_ranges(o["short_name"], year, month)
            good, probs = _arch.check_range(db_ranges, no_from, no_to)
            if not good:
                raise ValueError("；".join(probs))
            seq = _arch.next_seq(db_seqs)   # 库里该期间已有册数的下一个（含本批已提交的行）
            vol_no = _arch.make_vol_no(o["code"], year, month, seq)
            loc_id = ensure_location_path(row.get("存放位置"), operator)
            with _engine.begin() as c:
                c.execute(insert(arch_volumes).values(
                    vol_no=vol_no, org=o["short_name"], year=year, month=month,
                    vtype=str(row.get("凭证类型") or _arch.VTYPE_DEFAULT), seq=seq,
                    no_from=no_from, no_to=no_to, sheets=no_to - no_from + 1,
                    status="在库", loc_id=loc_id, keep_until=_arch.keep_until(year),
                    note=str(row.get("备注") or ""), created_by=str(operator or ""), created_at=_now(),
                    updated_by=str(operator or ""), updated_at=_now()))
            if loc_id:
                _log_transfer("初次登记入库（批量导入）", loc_id, [(vol_no, None, loc_id)], operator)
            ok_list.append({"行": idx, "册号": vol_no, "位置": _loc_path(
                {l["id"]: l for l in list_locations()}, loc_id) if loc_id else ""})
        except (ValueError, TypeError, KeyError) as e:
            err_list.append({"行": idx, "原因": str(e) or "字段缺失或格式错"})
    return {"ok": True, "成功": ok_list, "失败": err_list,
            "建位置数": len(list_locations()) - loc_before}


def find_volume(org, year, month, no):
    """按主体+年月+凭证号定位册子。返回命中的册子行（含位置），或 None。"""
    with _engine.connect() as c:
        rows = c.execute(select(arch_volumes).where(
            arch_volumes.c.org == org, arch_volumes.c.year == int(year),
            arch_volumes.c.month == int(month),
            arch_volumes.c.no_from <= int(no), arch_volumes.c.no_to >= int(no))).mappings().all()
        locs = {r["id"]: dict(r) for r in c.execute(select(arch_locations)).mappings().all()}
    return [_vol_row(r, locs) for r in rows]


def range_checkup(org, year, month, kingdee_max):
    """号段体检：对金蝶该期间总张数找缺口。"""
    from kernels import archive as _arch
    ranges, _ = _period_ranges(org, year, month)
    return _arch.range_gaps(ranges, kingdee_max)


# ----------------------------- 凭证归档：转移 -----------------------------
def _log_transfer(reason, to_id, items, operator, approve_no=""):
    """写转移单 + 明细。items = [(册号, 源位置, 目标位置)...]。"""
    tno = "TR-%s-%s" % (_now()[:10].replace("-", ""), secrets.token_hex(2))
    with _engine.begin() as c:
        tid = c.execute(insert(arch_transfers).values(
            transfer_no=tno, reason=reason, to_id=to_id, cnt=len(items),
            approve_no=approve_no, operator=str(operator or ""), ts=_now())).inserted_primary_key[0]
        for vol, frm, to in items:
            c.execute(insert(arch_transfer_items).values(
                transfer_id=tid, vol_no=vol, from_loc=frm, to_loc=to))
    return tno


def transfer_volumes(vol_nos, expected_locs, to_id, reason, operator):
    """批量转移。expected_locs={册号:勾选时位置id}，提交前校验现况是否一致（乐观锁）。
    返回 (ok, 冲突册号[], 转移单号 or '')。有冲突则整单拒绝、一本不动。"""
    from kernels import archive as _arch
    node = _loc_node(to_id)
    if not node:
        raise ValueError("目标位置不存在")
    if not _arch.can_transfer_into(node["ntype"], node.get("terminal")):
        raise ValueError("「%s」是终态位置，不能用普通转移放进去" % node["name"])
    with _engine.connect() as c:
        rows = c.execute(select(arch_volumes.c.vol_no, arch_volumes.c.loc_id,
                                arch_volumes.c.status).where(
            arch_volumes.c.vol_no.in_(list(vol_nos)))).mappings().all()
    actual = {r["vol_no"]: r["loc_id"] for r in rows}
    frozen = [r["vol_no"] for r in rows if r["status"] in ("已销毁", "待销毁")]
    if frozen:
        return False, frozen, ""     # 待销毁/已销毁不参与普通转移
    conflicts = _arch.transfer_conflicts(expected_locs, actual)
    if conflicts:
        return False, conflicts, ""
    items = [(v, actual.get(v), to_id) for v in vol_nos]
    with _engine.begin() as c:
        for v in vol_nos:
            c.execute(update(arch_volumes).where(arch_volumes.c.vol_no == v).values(
                loc_id=to_id, updated_by=str(operator or ""), updated_at=_now()))
    tno = _log_transfer(reason, to_id, items, operator)
    return True, [], tno


def _ensure_borrow_node(borrower):
    """「外借 › 借出人」节点，没有就建。借出人作为「外借」根下的子节点。"""
    borrower = str(borrower or "").strip() or "未具名"
    with _engine.begin() as c:
        root = c.execute(select(arch_locations.c.id).where(
            arch_locations.c.ntype == "外借", arch_locations.c.parent_id.is_(None))).first()
        rid = root[0] if root else c.execute(insert(arch_locations).values(
            name="外借", ntype="外借", parent_id=None, terminal="0",
            created_by="system", created_at=_now())).inserted_primary_key[0]
        sub = c.execute(select(arch_locations.c.id).where(
            arch_locations.c.parent_id == rid, arch_locations.c.name == borrower)).first()
        if sub:
            return sub[0]
        return c.execute(insert(arch_locations).values(
            name=borrower, ntype="外借", parent_id=rid, terminal="0",
            created_by="system", created_at=_now())).inserted_primary_key[0]


def borrow_volumes(vol_nos, borrower, due_date, operator):
    """借出：状态→借出中，位置→外借节点，记下借出前位置（归还时自动回位）。只借在库/已装箱的。"""
    with _engine.connect() as c:
        rows = c.execute(select(arch_volumes.c.vol_no, arch_volumes.c.loc_id,
                                arch_volumes.c.status).where(
            arch_volumes.c.vol_no.in_(list(vol_nos)))).mappings().all()
    bad = [r["vol_no"] for r in rows if r["status"] != "在库"]
    if bad:
        raise ValueError("这些册子不是「在库」状态，不能借出：%s" % "、".join(bad))
    node = _ensure_borrow_node(borrower)
    items = [(r["vol_no"], r["loc_id"], node) for r in rows]
    with _engine.begin() as c:
        for r in rows:
            c.execute(update(arch_volumes).where(arch_volumes.c.vol_no == r["vol_no"]).values(
                status="借出中", loc_before=r["loc_id"], loc_id=node,
                borrow_by=str(borrower or ""), due_date=str(due_date or ""),
                updated_by=str(operator or ""), updated_at=_now()))
    return _log_transfer("借出", node, items, operator)


def return_volumes(vol_nos, operator):
    """归还：状态→在库，位置自动回到借出前的位置。"""
    with _engine.connect() as c:
        rows = c.execute(select(arch_volumes.c.vol_no, arch_volumes.c.loc_id,
                                arch_volumes.c.loc_before, arch_volumes.c.status).where(
            arch_volumes.c.vol_no.in_(list(vol_nos)))).mappings().all()
    bad = [r["vol_no"] for r in rows if r["status"] != "借出中"]
    if bad:
        raise ValueError("这些册子不是「借出中」，无法归还：%s" % "、".join(bad))
    items = [(r["vol_no"], r["loc_id"], r["loc_before"]) for r in rows]
    with _engine.begin() as c:
        for r in rows:
            c.execute(update(arch_volumes).where(arch_volumes.c.vol_no == r["vol_no"]).values(
                status="在库", loc_id=r["loc_before"], loc_before=None,
                borrow_by="", due_date="", updated_by=str(operator or ""), updated_at=_now()))
    return _log_transfer("归还", None, items, operator)


def destroy_apply(vol_nos, approve_no, batch_name, operator):
    """销毁·申请（总账会计）：建/取销毁批次节点，册子转「待销毁」（可撤回）。需审批单号。"""
    approve_no = str(approve_no or "").strip()
    if not approve_no:
        raise ValueError("销毁必须填审批单号")
    with _engine.connect() as c:
        rows = c.execute(select(arch_volumes.c.vol_no, arch_volumes.c.loc_id,
                                arch_volumes.c.status).where(
            arch_volumes.c.vol_no.in_(list(vol_nos)))).mappings().all()
    bad = [r["vol_no"] for r in rows if r["status"] not in ("在库", "借出中")]
    if bad:
        raise ValueError("这些册子当前状态不能申请销毁：%s" % "、".join(bad))
    with _engine.begin() as c:
        node = c.execute(insert(arch_locations).values(
            name=str(batch_name or ("销毁批次 " + approve_no)), ntype="销毁批次", parent_id=None,
            terminal="1", created_by=str(operator or ""), created_at=_now())).inserted_primary_key[0]
        for r in rows:
            c.execute(update(arch_volumes).where(arch_volumes.c.vol_no == r["vol_no"]).values(
                status="待销毁", loc_before=r["loc_id"], loc_id=node,
                note=("销毁审批单号 " + approve_no), updated_by=str(operator or ""), updated_at=_now()))
    items = [(r["vol_no"], r["loc_id"], node) for r in rows]
    return _log_transfer("销毁申请", node, items, operator, approve_no=approve_no)


def destroy_cancel(vol_nos, operator):
    """撤回销毁申请（总账会计）：待销毁→在库，位置回到申请前。"""
    with _engine.connect() as c:
        rows = c.execute(select(arch_volumes.c.vol_no, arch_volumes.c.loc_id,
                                arch_volumes.c.loc_before, arch_volumes.c.status).where(
            arch_volumes.c.vol_no.in_(list(vol_nos)))).mappings().all()
    bad = [r["vol_no"] for r in rows if r["status"] != "待销毁"]
    if bad:
        raise ValueError("只有「待销毁」能撤回：%s" % "、".join(bad))
    items = [(r["vol_no"], r["loc_id"], r["loc_before"]) for r in rows]
    with _engine.begin() as c:
        for r in rows:
            c.execute(update(arch_volumes).where(arch_volumes.c.vol_no == r["vol_no"]).values(
                status="在库", loc_id=r["loc_before"], loc_before=None,
                updated_by=str(operator or ""), updated_at=_now()))
    return _log_transfer("撤回销毁", None, items, operator)


def destroy_execute(vol_nos, operator):
    """销毁·执行（财务经理）：待销毁→已销毁（终态，只读留痕，记录永不删）。"""
    with _engine.connect() as c:
        rows = c.execute(select(arch_volumes.c.vol_no, arch_volumes.c.loc_id,
                                arch_volumes.c.status).where(
            arch_volumes.c.vol_no.in_(list(vol_nos)))).mappings().all()
    bad = [r["vol_no"] for r in rows if r["status"] != "待销毁"]
    if bad:
        raise ValueError("只有「待销毁」能执行销毁：%s" % "、".join(bad))
    items = [(r["vol_no"], r["loc_id"], r["loc_id"]) for r in rows]
    with _engine.begin() as c:
        for r in rows:
            c.execute(update(arch_volumes).where(arch_volumes.c.vol_no == r["vol_no"]).values(
                status="已销毁", updated_by=str(operator or ""), updated_at=_now()))
    return _log_transfer("销毁执行", None, items, operator)


def volume_trail(vol_no):
    """一本册子的完整转移轨迹（最新在前）。"""
    with _engine.connect() as c:
        rows = c.execute(select(arch_transfer_items, arch_transfers).join(
            arch_transfers, arch_transfer_items.c.transfer_id == arch_transfers.c.id).where(
            arch_transfer_items.c.vol_no == vol_no).order_by(arch_transfers.c.ts.desc())).mappings().all()
        locs = {r["id"]: dict(r) for r in c.execute(select(arch_locations)).mappings().all()}
    out = []
    for r in rows:
        out.append({"transfer_no": r["transfer_no"], "reason": r["reason"], "ts": r["ts"],
                    "operator": r["operator"], "to_path": _loc_path(locs, r["to_loc"])})
    return out


def seed_orgs():
    """幂等种子：把物流计提原先硬编码的三条 主体→账簿代码 写进档案表。
    只在该简称尚不存在时插入；全称/简码/别名留空，待超管在「基础数据 › 主体档案」补齐。"""
    have = {o["short_name"] for o in list_orgs()}
    todo = [(s, b) for s, b in ORG_SEED if s not in have]
    if not todo:
        return 0
    with _engine.begin() as c:
        for s, b in todo:
            c.execute(insert(orgs).values(full_name="", short_name=s, code="", book_code=b,
                                          aliases="", color="", active="1",
                                          note="由物流计提原硬编码迁入，待补全称与简码",
                                          updated_by="system", updated_at=_now()))
    return len(todo)


# ----------------------------- 物流计提·录入台账 -----------------------------
def log_logistics_post(year, period, zhaiyao, billno, kd_id, vno, operator):
    with _engine.begin() as c:
        c.execute(insert(post_log).values(year=int(year), period=int(period), zhaiyao=str(zhaiyao),
                                          billno=str(billno), kd_id=str(kd_id), vno=str(vno),
                                          operator=str(operator or ""), ts=_now()))


def logistics_posted(year, period):
    """{摘要: {billno, kd_id, vno, operator, ts, id}}，同摘要取最新一条。"""
    with _engine.connect() as c:
        rows = c.execute(select(post_log).where(post_log.c.year == int(year),
                                                post_log.c.period == int(period))
                         .order_by(post_log.c.id)).mappings().all()
    return {r["zhaiyao"]: dict(r) for r in rows}


def list_logistics_posts(year, period):
    """某期间本工具录入金蝶的全部凭证台账（列表，按 id 升序），供「撤销/删除草稿」页展示。"""
    with _engine.connect() as c:
        rows = c.execute(select(post_log).where(post_log.c.year == int(year),
                                                post_log.c.period == int(period))
                         .order_by(post_log.c.id)).mappings().all()
    return [dict(r) for r in rows]


def get_logistics_post(log_id):
    with _engine.connect() as c:
        r = c.execute(select(post_log).where(post_log.c.id == int(log_id))).mappings().first()
    return dict(r) if r else None


def delete_logistics_post_log(log_id):
    """草稿已在金蝶被删时清掉对应台账行，放行重录。"""
    with _engine.begin() as c:
        c.execute(delete(post_log).where(post_log.c.id == int(log_id)))


# ----------------------------- 汇率录入·写金蝶台账 -----------------------------
def _fx_key(org, from_code, to_code, beg, end):
    """本工具内幂等键：组织×币对×生效区间（同金蝶不覆盖口径）。"""
    return f"{org}|{from_code}|{to_code}|{beg}|{end}"


def log_fx_post(year, month, org, pair, from_code, to_code, rate, beg, end, kind, kd_id, operator):
    with _engine.begin() as c:
        c.execute(insert(fx_post_log).values(
            year=int(year), month=int(month), org=str(org), pair=str(pair),
            from_code=str(from_code), to_code=str(to_code), rate=str(rate),
            beg_date=str(beg), end_date=str(end), kind=str(kind or ""),
            kd_id=str(kd_id), operator=str(operator or ""), ts=_now()))


def fx_posted(year, month, org):
    """{幂等键: {..台账行..}}，某结账年月×组织本工具已录入的，同键取最新一条。"""
    with _engine.connect() as c:
        rows = c.execute(select(fx_post_log).where(
            fx_post_log.c.year == int(year), fx_post_log.c.month == int(month),
            fx_post_log.c.org == str(org)).order_by(fx_post_log.c.id)).mappings().all()
    return {_fx_key(r["org"], r["from_code"], r["to_code"], r["beg_date"], r["end_date"]): dict(r)
            for r in rows}


def list_fx_posts(year, month, org):
    """某结账年月×组织本工具录入金蝶的全部汇率台账（按 id 升序），供撤销页展示。"""
    with _engine.connect() as c:
        rows = c.execute(select(fx_post_log).where(
            fx_post_log.c.year == int(year), fx_post_log.c.month == int(month),
            fx_post_log.c.org == str(org)).order_by(fx_post_log.c.id)).mappings().all()
    return [dict(r) for r in rows]


def fx_posts_year(year, org):
    """某年×组织本工具录入金蝶的全部汇率台账（供状态看板按月汇总）。"""
    with _engine.connect() as c:
        rows = c.execute(select(fx_post_log).where(
            fx_post_log.c.year == int(year), fx_post_log.c.org == str(org)
        ).order_by(fx_post_log.c.id)).mappings().all()
    return [dict(r) for r in rows]


def get_fx_post(log_id):
    with _engine.connect() as c:
        r = c.execute(select(fx_post_log).where(fx_post_log.c.id == int(log_id))).mappings().first()
    return dict(r) if r else None


def delete_fx_post_log(log_id):
    """金蝶里对应记录已被删/撤时清掉台账行，放行重录。"""
    with _engine.begin() as c:
        c.execute(delete(fx_post_log).where(fx_post_log.c.id == int(log_id)))


# ----------------------------- 余额调节·未达原因 -----------------------------
def list_balance_notes(year, period):
    """{账号: {note, operator, ts}}，某期间已填的未达原因。"""
    with _engine.connect() as c:
        rows = c.execute(select(balance_notes).where(
            balance_notes.c.year == int(year), balance_notes.c.period == int(period))).mappings().all()
    return {r["acct"]: {"note": r["note"] or "", "operator": r["operator"] or "", "ts": r["ts"] or ""} for r in rows}


def set_balance_note(year, period, acct, note, operator):
    """填/改某账户某期间的未达原因（同 期间×账号 upsert；note 传空串=清空）。"""
    acct = str(acct or "").strip()
    vals = dict(note=str(note or "").strip(), operator=str(operator or ""), ts=_now())
    with _engine.begin() as c:
        r = c.execute(select(balance_notes.c.id).where(
            balance_notes.c.year == int(year), balance_notes.c.period == int(period),
            balance_notes.c.acct == acct)).first()
        if r:
            c.execute(update(balance_notes).where(balance_notes.c.id == r[0]).values(**vals))
        else:
            c.execute(insert(balance_notes).values(year=int(year), period=int(period), acct=acct, **vals))


# ----------------------------- 门户工具卡片（门户管理 CMS） -----------------------------
def _pt_row(r):
    d = dict(r)
    for k in ("gen", "ai", "mods"):
        try:
            d[k] = json.loads(d.get(k) or "[]")
        except Exception:
            d[k] = []
    d["desc"] = d.pop("descr", "") or ""      # 对外仍用 desc 键
    return d


def list_portal_tools():
    with _engine.connect() as c:
        rows = c.execute(select(portal_tools).order_by(portal_tools.c.lane, portal_tools.c.sort, portal_tools.c.id)).mappings().all()
    return [_pt_row(r) for r in rows]


def save_portal_tool(d):
    """有 id → 更新；无 id → 新增。返回 id。"""
    vals = dict(
        lane=str(d.get("lane") or "accounting"),
        name=str(d.get("name") or "").strip(),
        status=str(d.get("status") or "beta"),
        icon=str(d.get("icon") or "▤")[:8],
        descr=str(d.get("desc") or ""),
        gen=json.dumps([str(x) for x in (d.get("gen") or []) if str(x).strip()], ensure_ascii=False),
        ai=json.dumps([str(x) for x in (d.get("ai") or []) if str(x).strip()], ensure_ascii=False),
        sort=int(d.get("sort") or 0),
        mods=json.dumps([str(x) for x in (d.get("mods") or []) if str(x).strip()], ensure_ascii=False),
    )
    with _engine.begin() as c:
        tid = d.get("id")
        if tid:
            c.execute(update(portal_tools).where(portal_tools.c.id == int(tid)).values(**vals))
            return int(tid)
        r = c.execute(insert(portal_tools).values(**vals))
        return r.inserted_primary_key[0]


def delete_portal_tool(tid):
    with _engine.begin() as c:
        c.execute(delete(portal_tools).where(portal_tools.c.id == int(tid)))


# 出厂工具集（V2.328 按两台已开发工具重写；V2.329 状态改四档并按业务方口径重新归档）：
# seed_portal_tools 首启灌种用它；「门户管理 › 同步出厂工具集」按钮（reset_portal_tools_defaults）也用它——
# 按**名称**匹配：同名卡整体覆盖、缺失补插、手工新增的其他卡保留不删。改完这份清单要提醒管理员去点一次同步。
# 状态四档（业务方定，2026-08-20，和工具在工作台里的真实进度挂钩）：
#   ok=已上线（组员自助在用/全自动跑，门户上闪烁绿灯）→ 仅 银行稽核/销售预算/汇率录入
#   par=人工并行（功能已上线但仍与人工流程双轨核对中）→ 大多数已开发工具落这档
#   beta=开发中 / soon=敬请期待
# AI 标签口径：已上线的 AI 能力裸写；未上线的带「（待开发）」（沿 销售预算·异动归因 的先例，不虚标）。
# 第 9 字段 mods（V2.331 自动联动）：核算导航模块 key 列表——非空的卡状态不再由本清单/手工决定，
# 而是由这些模块在「系统设置 › 导航模块上线管理」里的六档进度推导（木桶取最低，见 app._portal_autolink）。
# BP 泳道没有模块状态体系 → mods 留空 = 维持手动（BP 联动记 backlog）。
PORTAL_TOOL_DEFAULTS = [
    # ── 核算组 ──
    ("accounting", "银行-金蝶稽核", "ok", "▤", "银行流水 × 金蝶序时账逐笔勾稽，差异分级、认领、闭环；余额调节与资金看板。",
     ["逐笔稽核", "余额调节", "账户台账", "资金看板"], ["AI差异归因（待开发）"], 0, ["reconcile", "ledger", "fundboard"]),
    ("accounting", "月结与结账", "par", "▦", "月结节点看板与期间封存：封存后本期只读，先工作台封存、再金蝶结账。",
     ["节点看板", "期间封存", "结账清单"], ["AI结账检查（待开发）"], 1, ["periodclose"]),
    ("accounting", "电商平台对账", "par", "◫", "支付宝/微信/抖音结算流水上传跑批、与金蝶核销；剔除留痕定性登记，一键结算凭证草稿。",
     ["结算跑批", "核销勾稽", "剔除留痕", "凭证草稿"], ["AI差异定位（待开发）"], 2, ["ecomsettle", "ecombase"]),
    ("accounting", "存货成本台账", "par", "▥", "金蝶取数/上传双通道，按主体期间落库全员共享；勾稽透视、仓库维度、期间封存。",
     ["取数上传", "勾稽透视", "仓库维度"], [], 3, ["clexport", "cldash", "clwh"]),
    ("accounting", "物流费用计提与对账", "par", "▨", "物流计提表上传与税率维护、一键录入金蝶；物流账单对账核对。",
     ["计提录入", "税率维护", "物流对账"], [], 4, ["logistics", "logibase", "logiupload", "logisticspay", "logisticscost"]),
    ("accounting", "汇率录入", "ok", "◈", "每月自动取数写入金蝶汇率（含提交/撤销），挂起与出错自动邮件/钉钉告警。",
     ["自动取数", "写入金蝶", "异常告警"], [], 5, ["fxrate"]),
    ("accounting", "报表导出", "par", "▧", "金蝶报表一键导出落地共享盘，导出路径与通知可配置，已导出文件可管理。",
     ["一键导出", "路径配置", "通知推送"], [], 6, ["rptexport"]),
    ("accounting", "临时工考勤", "par", "▩", "打卡表 × 结构表两表比对、四档判定；应付薪资核对与结构看板，按期留档。",
     ["两表比对", "应付薪资", "考勤看板"], [], 7, ["tempattrev", "tempattboard"]),
    ("accounting", "凭证归档", "par", "▣", "纸质凭证归档登记、转移、借出与销毁申请，销毁执行留痕。",
     ["归档登记", "借出转移", "销毁留痕"], [], 8, ["archive"]),
    # ── BP 组 ──
    ("bp", "管报编制 & 经营分析", "par", "◐", "序时账解析、逐笔费用归属、管报↔财报稽核；利润拆解、同环比与经营看板。",
     ["费用归属", "管报稽核", "利润拆解", "经营看板"], ["AI经营分析"], 0, []),
    ("bp", "销售预算编制&追踪", "ok", "◎", "一键更新驾驶舱替换原有 BI；业绩达成、预算编制、滚动预算与业绩周报。",
     ["业绩达成", "预算编制", "滚动预算", "业绩周报"], ["AI异动归因（待开发）"], 1, []),
    ("bp", "定价测算", "par", "◆", "电商/达人渠道自助定价测算：全档位阶梯并排、反算建议价，钉钉审批出毛利测算单。",
     ["档位阶梯", "反算建议价", "钉钉审批"], [], 2, []),
    ("bp", "AI 问数 · 智能分析中心", "beta", "✦", "用自然语言问经营数据，口径对齐驾驶舱与报表，问答全程审计留痕。",
     ["口径对齐", "审计留痕"], ["AI问数"], 3, []),
    ("bp", "电商专项管报", "soon", "◇", "电商业务专项管理报表，渠道 / 店铺 / 品类维度分析。",
     ["渠道分析", "店铺维度", "品类洞察"], ["AI电商洞察"], 4, []),
]


def seed_portal_tools():
    """首启无卡片时，种入出厂工具集（此后由「门户管理」维护）。"""
    with _engine.connect() as c:
        if c.execute(select(portal_tools.c.id).limit(1)).first():
            return
    for lane, name, status, icon, descr, gen, ai, sort, mods in PORTAL_TOOL_DEFAULTS:
        save_portal_tool({"lane": lane, "name": name, "status": status, "icon": icon,
                          "desc": descr, "gen": gen, "ai": ai, "sort": sort, "mods": mods})


def backfill_portal_mods():
    """老库回填 portal_tools.mods —— 门户状态自动联动的**入口条件**。

    ⚠ 这是 V2.331 漏掉的一步，后果是「联动」一次都没生效过：
      · `_ensure_portal_columns()` 给老库补了 mods 这一列，但**只补列、不补值**；
      · `seed_portal_tools()` 只在**表为空**时灌种，而卡片在 V2.331 之前就已经存在。
    于是所有老卡的 mods 恒为空 → `app._portal_autolink` 每张都判「无法推导」→ 回落手工档。
    表现：管理员在「系统设置 › 导航模块上线管理」把模块改成什么，门户上纹丝不动，
    永远显示建门户时定的那一档（2026-08-23 使用者实测：「我在里面设置的是在建，
    外面怎么变成了人工并行」——当时 14 张卡 mods 全空）。

    只填**当前为空**的那些：管理员手工挂过映射的卡一律不碰。按名称匹配出厂清单，
    与「门户管理 › 同步出厂工具集」同一份数据源，但**只写 mods 这一个字段**——
    那颗按钮会把状态/概述/标签/图标/排序整体覆盖回出厂，回填不该有那种副作用。
    """
    try:
        rows = list_portal_tools()
    except Exception:
        return {"filled": []}
    want = {t[1]: t[8] for t in PORTAL_TOOL_DEFAULTS if t[8]}
    filled = []
    for t in rows:
        if t.get("mods"):                       # 已有映射（出厂灌的或管理员挂的）——不碰
            continue
        mods = want.get(str(t.get("name") or ""))
        if not mods:
            continue
        try:
            with _engine.begin() as c:
                c.execute(update(portal_tools).where(portal_tools.c.id == int(t["id"]))
                          .values(mods=json.dumps(mods, ensure_ascii=False)))
            filled.append(t["name"])
        except Exception:
            pass
    return {"filled": filled}


backfill_portal_mods()


def reset_portal_tools_defaults():
    """把工具卡片同步为出厂工具集（V2.328，「门户管理」按钮触发、仅管理员）：
    按名称匹配——同名卡整体覆盖（状态/概述/标签/图标/泳道/排序），缺失的补插；
    **不删任何卡**：清单之外的手工卡原样保留（防清掉管理员自己建的）。返回增/改/留名单供弹窗与审计。"""
    existing = {str(t.get("name") or ""): t for t in list_portal_tools()}
    default_names = {t[1] for t in PORTAL_TOOL_DEFAULTS}
    added, updated = [], []
    for lane, name, status, icon, descr, gen, ai, sort, mods in PORTAL_TOOL_DEFAULTS:
        row = {"lane": lane, "name": name, "status": status, "icon": icon,
               "desc": descr, "gen": gen, "ai": ai, "sort": sort, "mods": mods}
        if name in existing:
            row["id"] = existing[name]["id"]
            updated.append(name)
        else:
            added.append(name)
        save_portal_tool(row)
    kept = [n for n in existing if n not in default_names]
    return {"added": added, "updated": updated, "kept": kept}


# ==================== 月结批次 / 期间封存 ====================
# 封存 = 这个月的对账做完了，把结果拍照存死。之后这一期只读：不再取金蝶、不能改认领/未达原因、
# 不能撤销计提凭证。要改必须先解封（主管权限 + 留痕）。顺序：工作台封存 → 金蝶期末结账。
def _period_where(t, source, year, period):
    return (t.c.source == source) & (t.c.year == int(year)) & (t.c.period == int(period))


def get_period(source, year, period):
    """该期状态。从未封存过 → 进行中（不建行，省得每切一次期间就写库）。"""
    with _engine.connect() as c:
        r = c.execute(select(periods).where(_period_where(periods, source, year, period))).mappings().first()
    if not r or r["status"] != "closed":
        return {"status": "open", "已封存": False}
    return {"status": "closed", "已封存": True,
            "封存人": r["closed_by"] or "", "封存时间": r["closed_at"] or "",
            "封存说明": r["note"] or "", "金蝶取数时点": r["kd_synced_at"] or ""}


def is_closed(source, year, period):
    return get_period(source, year, period)["status"] == "closed"


def close_period(source, year, period, operator, note="", kd_synced_at=""):
    vals = dict(status="closed", closed_by=operator, closed_at=_now(),
                note=note or "", kd_synced_at=kd_synced_at or "")
    with _engine.begin() as c:
        w = _period_where(periods, source, year, period)
        if c.execute(select(periods.c.id).where(w)).first():
            c.execute(update(periods).where(w).values(**vals))
        else:
            c.execute(insert(periods).values(source=source, year=int(year), period=int(period), **vals))
    return get_period(source, year, period)


def reopen_period(source, year, period, operator, reason):
    """解封：状态转回进行中。快照保留不删——解封重封会覆盖，但解封前那一版留着可查。"""
    with _engine.begin() as c:
        w = _period_where(periods, source, year, period)
        c.execute(update(periods).where(w).values(
            status="open", closed_by="", closed_at="",
            note="%s 于 %s 解封：%s" % (operator, _now(), reason or "")))
    return get_period(source, year, period)


def list_closed_periods(source=None, limit=24):
    with _engine.connect() as c:
        q = select(periods).where(periods.c.status == "closed")
        if source:
            q = q.where(periods.c.source == source)
        rows = c.execute(q.order_by(periods.c.year.desc(), periods.c.period.desc()).limit(limit)).mappings().all()
    return [{"期间": "%d-%02d" % (r["year"], r["period"]), "year": r["year"], "period": r["period"],
             "数据源": r["source"], "封存人": r["closed_by"] or "", "封存时间": r["closed_at"] or "",
             "封存说明": r["note"] or "", "金蝶取数时点": r["kd_synced_at"] or ""} for r in rows]


def save_snapshot(source, year, period, kind, obj):
    """结果拍照：gzip(JSON) 落库。同期同 kind 覆盖（解封重封时更新）。"""
    blob = gzip.compress(json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"))
    with _engine.begin() as c:
        w = _period_where(period_snapshots, source, year, period) & (period_snapshots.c.kind == kind)
        if c.execute(select(period_snapshots.c.id).where(w)).first():
            c.execute(update(period_snapshots).where(w).values(payload=blob, ts=_now()))
        else:
            c.execute(insert(period_snapshots).values(source=source, year=int(year), period=int(period),
                                                      kind=kind, payload=blob, ts=_now()))
    return len(blob)


def load_snapshot(source, year, period, kind):
    with _engine.connect() as c:
        r = c.execute(select(period_snapshots.c.payload).where(
            _period_where(period_snapshots, source, year, period)
            & (period_snapshots.c.kind == kind))).first()
    if not r or not r[0]:
        return None
    try:
        return json.loads(gzip.decompress(r[0]).decode("utf-8"))
    except Exception:
        return None                       # 快照坏了就当没有，退回实时算（不让整页 500）


# ---------------- 按期间存的输入数据（银行流水上传一次 / 金蝶取数一次）----------------
def set_period_input(source, year, period, kind, payload, meta=None, operator=""):
    """存一份本期输入数据（gzip JSON）。同期同 kind 覆盖。"""
    blob = gzip.compress(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
    mj = json.dumps(meta or {}, ensure_ascii=False, default=str)
    with _engine.begin() as c:
        w = _period_where(period_inputs, source, year, period) & (period_inputs.c.kind == kind)
        if c.execute(select(period_inputs.c.id).where(w)).first():
            c.execute(update(period_inputs).where(w).values(payload=blob, meta=mj, updated_by=operator, updated_at=_now()))
        else:
            c.execute(insert(period_inputs).values(source=source, year=int(year), period=int(period),
                                                   kind=kind, payload=blob, meta=mj, updated_by=operator, updated_at=_now()))
    return len(blob)


def get_period_input(source, year, period, kind):
    """读本期该 kind 的输入数据 → {payload, meta, updated_by, updated_at}；没存过 → None。"""
    with _engine.connect() as c:
        r = c.execute(select(period_inputs.c.payload, period_inputs.c.meta,
                             period_inputs.c.updated_by, period_inputs.c.updated_at)
                      .where(_period_where(period_inputs, source, year, period)
                             & (period_inputs.c.kind == kind))).first()
    if not r or not r[0]:
        return None
    try:
        payload = json.loads(gzip.decompress(r[0]).decode("utf-8"))
    except Exception:
        return None
    try:
        meta = json.loads(r[1]) if r[1] else {}
    except Exception:
        meta = {}
    return {"payload": payload, "meta": meta, "updated_by": r[2] or "", "updated_at": r[3] or ""}


def list_period_inputs(source, kind):
    """该源该 kind 存过数据的期间清单（新→旧）。不解 payload。V2.119/V2.122：成本台账用它找"最近有数据的一期"。"""
    with _engine.connect() as c:
        rows = c.execute(select(period_inputs.c.year, period_inputs.c.period,
                                period_inputs.c.updated_by, period_inputs.c.updated_at)
                         .where((period_inputs.c.source == source) & (period_inputs.c.kind == kind))
                         .order_by(period_inputs.c.year.desc(), period_inputs.c.period.desc())).all()
    return [{"year": r[0], "period": r[1], "updated_by": r[2] or "", "updated_at": r[3] or ""} for r in rows]


def period_input_meta(source, year, period, kind):
    """只取摘要（meta + 更新人/时点），不解大 payload——列状态用，省带宽。"""
    with _engine.connect() as c:
        r = c.execute(select(period_inputs.c.meta, period_inputs.c.updated_by, period_inputs.c.updated_at)
                      .where(_period_where(period_inputs, source, year, period)
                             & (period_inputs.c.kind == kind))).first()
    if not r:
        return None
    try:
        meta = json.loads(r[0]) if r[0] else {}
    except Exception:
        meta = {}
    return {"meta": meta, "updated_by": r[1] or "", "updated_at": r[2] or ""}


def clear_period_inputs(source, year, period, prefix=None):
    """清本期输入数据（prefix 如 'kd:' 只清金蝶取数那批；None 清全部）。"""
    with _engine.begin() as c:
        w = _period_where(period_inputs, source, year, period)
        if prefix:
            w = w & period_inputs.c.kind.like(prefix + "%")
        return c.execute(delete(period_inputs).where(w)).rowcount


# ---------------- 通用应用设置（键值对，全员生效） ----------------
def get_setting(k, default=None):
    with _engine.connect() as c:
        r = c.execute(select(app_settings.c.v).where(app_settings.c.k == k)).first()
    if not r or r[0] is None:
        return default
    try:
        return json.loads(r[0])
    except Exception:
        return default          # 坏值当没设过，回退默认，不让整页 500


def list_settings(prefix=""):
    """按前缀列出设置键（不取值）。给「一个 key 一条记录」的按月配置用，例如
    临时工考勤的单价表 tempatt_rates_2026-07——页面据此列出已维护过的月份。"""
    with _engine.connect() as c:
        rows = c.execute(select(app_settings.c.k)).fetchall()
    return sorted(r[0] for r in rows if not prefix or str(r[0]).startswith(prefix))


def set_setting(k, obj, operator=""):
    v = json.dumps(obj, ensure_ascii=False)
    with _engine.begin() as c:
        if c.execute(select(app_settings.c.k).where(app_settings.c.k == k)).first():
            c.execute(update(app_settings).where(app_settings.c.k == k)
                      .values(v=v, updated_by=operator, updated_at=_now()))
        else:
            c.execute(insert(app_settings).values(k=k, v=v, updated_by=operator, updated_at=_now()))


def audit_exists(action, target):
    """审计日志里有没有这么一条（月结看板判断「底稿已导出」用）。"""
    with _engine.connect() as c:
        return bool(c.execute(select(audit_log.c.id).where(
            (audit_log.c.action == action) & (audit_log.c.target == target)).limit(1)).first())
