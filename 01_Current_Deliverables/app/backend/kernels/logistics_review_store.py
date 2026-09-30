# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-09-26 | Author: Claude Opus 4.8 | Version: V2.632
# Description: 【物流账单复核】三张新表（自带 MetaData，由 db.py to_metadata 挂进来，create_all 只建不改）：
#   ① logistics_bill_lines  中间表——一行=一条账单明细，双粒度：grain='detail'(对账/逐单,带单号) / 'accrual'(计提,按费用项汇总)。
#      通用解析落库于此；核价核量把标准费/差/归一态填回；计提读 accrual 行聚合。
#   ② logistics_price_card   价格卡——承运商×费用项×计价单位×单价(或阶梯 tier_json)×生效期×合同位置×确认状态。
#      核价的地基。价来自合同，不拿账单倒算。快递费按省×重量档存 tier_json，一条=一家快递公司整表。
#   ③ logistics_intake_spec  取数说明——一家承运商一条 spec_json：哪些 sheet 算费用、每张表列名/汇总/单据/数量列/费用项翻译。
#      通用解析器按它认列（按列名不按序号），加一家配一张，不改代码。
# 复核链路：上传→通用解析(按 intake_spec)→bill_lines(detail+accrual)→核价(price_card)+核量(金蝶数量)→归一态→计提行(复用 logistics_bills)。
from sqlalchemy import Table, Column, Integer, String, Float, Text, MetaData, insert, select

_md = MetaData()

bill_lines = Table(
    "logistics_bill_lines", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("batch_id", String(40)),        # 上传批次（一次上传一家一个月）
    Column("period", String(7)),           # 'YYYY-MM'
    Column("carrier", String(60)),         # 承运商简称
    Column("grain", String(10)),           # 'detail' 对账粒度 / 'accrual' 计提粒度
    Column("review_mode", String(10)),     # 复核模式：'audit' 核价核量（有账单/合同价）/ 'register' 登记免核（议价/报销：货拉拉等）
    Column("subject", String(30)),         # 费用主体简称
    Column("doc_no", String(300)),         # 金蝶单号（一行多单以 + 连接；物流部一格写7单/27单区间，80字不够，加宽到300）
    Column("annot", String(60)),           # 费用标注（规范词表）
    Column("fee", String(40)),             # 翻译后费用归属
    Column("bizline", String(40)),         # 业务线
    Column("fee_item", String(40)),        # 承运商费用项原名（快递费/操作费/仓储费…）
    Column("qty", Float),                  # 账单数量
    Column("unit", String(12)),            # 数量单位
    Column("amount", Float),               # 含税金额（元）
    Column("carrier_sub", String(40)),     # 快递公司/子类（快递核价用：圆通/中通…）
    Column("prov", String(20)),            # 目的省（快递核价用）
    Column("charge_wt", Float),            # 计费重量（快递核价定档用）
    Column("sub_fees", Text),              # JSON：账单各费用列分项
    Column("kd_qty", Float),               # 金蝶出库数量（核量填回）
    Column("kd_kg", Float),                # 金蝶货物净重kg（辅助）
    Column("std_amount", Float),           # 标准费（核价填回：价卡单价×数量/重量档）
    Column("price_diff", Float),           # 核价差（账单−标准）
    Column("price_state", String(12)),     # 核价态：ok/over/under/free/gap
    Column("qty_diff", Float),             # 核量差（账单数量−金蝶数量）
    Column("qty_state", String(12)),       # 核量态：ok/qtydiff/miss/na
    Column("verdict", String(12)),         # 归一态：pass/price/qty/gap/free
    Column("src_sheet", String(60)),       # 来源工作表
    Column("src_row", Integer),            # 原表行号
    Column("note", Text),
    Column("subj_ovr", String(30)),        # 复核台人工改归类：主体覆盖(非空时生效，不动原 subject)
    Column("fee_ovr", String(40)),         # 复核台人工改归类：费用类型覆盖(非空时生效，不动原 fee_item/单号推断)
    Column("created_at", String(20)),
)

price_card = Table(
    "logistics_price_card", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("carrier", String(60)),         # 承运商简称
    Column("fee_item", String(60)),        # 费用项：快递费/操作费B2C/退货入库/仓储租金/货物装卸/包材·xxx
    Column("sub", String(40)),             # 子类：快递公司(圆通/中通…) 或 规格；空=不分
    Column("unit", String(16)),            # 计价单位：元/单、元/托/天、元/立方…
    Column("price", Float),                # 单价（简单单价；快递阶梯为空，看 tier_json）
    Column("tier_json", Text),             # 快递阶梯 JSON：{省:[<0.5,0.5-1,1-2,2-3,首重,续重]} / 顺丰{省:[1kg,2kg,3kg,续重]}
    Column("first_kg", Float),             # 首重kg（快递：圆通3/中通韵达邮政1）
    Column("effective_from", String(10)),  # 生效日期
    Column("source", String(80)),          # 合同/报价位置
    Column("status", String(12)),          # 确认状态：已签署/待确认
    Column("updated_by", String(50)),
    Column("updated_at", String(20)),
)

intake_spec = Table(
    "logistics_intake_spec", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("carrier", String(60), unique=True),   # 承运商简称
    Column("spec_json", Text),                     # 取数说明 JSON（sheet 清单）
    Column("updated_by", String(50)),
    Column("updated_at", String(20)),
)

# 复核登记：整月一家一次，登记后锁当月（改归类/备注要先撤销）。snap_json=登记时结论快照(计提/账单/差异合计)
review_sign = Table(
    "logistics_review_sign", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("carrier", String(60)),
    Column("period", String(7)),
    Column("status", String(12)),          # signed
    Column("reviewer", String(50)),
    Column("signed_at", String(20)),
    Column("note", Text),
    Column("snap_json", Text),
)

# 逐笔计提复核的备注＝差异解释。分录没有稳定ID，line_key=凭证号+科目+主体+费用项目+产品线+产品类型+部门
review_line_note = Table(
    "logistics_review_line_note", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("carrier", String(60)),
    Column("period", String(7)),
    Column("line_key", String(200)),
    Column("note", Text),
    Column("updated_by", String(50)),
    Column("updated_at", String(20)),
)

# 供应商复核要点：一家一段（顺丰按重量、丰源按件数箱…），挂在逐笔复核页顶部
review_carrier_pts = Table(
    "logistics_review_carrier_pts", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("carrier", String(60), unique=True),
    Column("points", Text),
    Column("updated_by", String(50)),
    Column("updated_at", String(20)),
)

# 计提更正：复核时发现金蝶计提记错维度(费用项目/科目/部门/产品线)，不在系统里改账，只登记"应改为什么"，
# 导出《计提更正单》打印交专人去金蝶改。snap_json=登记时原分录快照(主体/凭证号/科目/费用项目/部门/产品线/金额)，
# 金蝶改好后原行键会变，更正单仍按快照打印。to_* 存「编码 名称」(如 0030301 仓储物流部)，改账按编码找。
review_line_fix = Table(
    "logistics_review_line_fix", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("carrier", String(60)),
    Column("period", String(7)),
    Column("line_key", String(200)),
    Column("snap_json", Text),
    Column("to_acct", String(120)),
    Column("to_fee", String(120)),
    Column("to_dept", String(120)),
    Column("to_biz", String(120)),        # 产品分类
    Column("to_proj", String(120)),       # 产品项目
    Column("adj_period", String(7)),      # 调账月份(YYYY-MM)：=归属月份改原凭证，晚于则在该月做调整凭证
    Column("to_amt", String(30)),         # 应改为金额(不含税，空=不变；部分调走/金额记错时填)
    Column("memo", Text),
    Column("updated_by", String(50)),
    Column("updated_at", String(20)),
)

TABLES = [bill_lines, price_card, intake_spec, review_sign, review_line_note, review_carrier_pts, review_line_fix]

# 迅鸽取数说明（pilot 种子）：一家一条，sheet 清单。角色 accrual=计提口径(月结) / detail=对账口径(逐单) / ignore=价目表。
_XUNGE_SPEC = {
    "carrier": "迅鸽",
    "sheets": [
        {"name": "帐单-深圳星期零", "role": "accrual", "header_row": 14, "subject": {"fixed": "深圳星期零"},
         "amount_col": "金额", "qty_col": "服务数量(个)", "fee_item_col": "服务费", "summary_marker": "合计",
         "fee_map": {"第三方快递费": "销售出库单-电商", "B2C基础操作费": "销售出库单-电商",
                     "退货服务费": "其它出库单-电商", "物料费": "销售出库单-电商"}},
        {"name": "账单-孝感星期九", "role": "accrual", "header_row": 14, "subject": {"fixed": "孝感星期九"},
         "amount_col": "金额", "qty_col": "数量(㎡)", "fee_item_col": "仓储费", "summary_marker": "合计",
         "fee_map": {"货品仓储费": "成品仓储-电商"}},
        {"name": "*发货明细", "role": "detail", "header_row": 1, "subject": {"fixed": "深圳星期零"},
         "doc_col": ["金蝶单号", "金蝶单据编号"], "amount_cols": ["金额", "旺季加收", "燃油附加", "地区加收"],
         "qty_col": "数量", "qty_unit": "件", "wt_col": "快递重量", "prov_col": "省", "carrier_sub_col": "快递公司",
         "fee_item": "快递费", "annot": "销售出库单-电商", "kd_qty_axis": "qty"},
        {"name": "搬运费", "role": "detail", "header_row": 2, "subject": {"column": "客户"},
         "doc_col": "金蝶单号", "amount_col": "合计费用", "qty_col": "合计体积", "qty_unit": "方",
         "fee_item": "卸货费", "annot": "出库装卸", "summary_marker": "合计"},
        {"name": "退件表", "role": "detail", "header_row": 1, "subject": {"column": "公司"},
         "doc_col": "金蝶单号", "qty_col": "入库数量", "qty_unit": "件",
         "fee_item": "退货", "annot": "其它出库单-电商", "kd_qty_axis": "qty"},
        {"name": "存储费", "role": "detail", "header_row": 2, "subject": {"fixed": "深圳星期零"},
         "amount_col": "仓储费", "qty_col": "结存板位数", "qty_unit": "板",
         "fee_item": "仓储费", "annot": "成品仓储-电商", "doc": "无单据"},
        {"name": "*服务费|快递费（*|快运费|包装材料|基础服务费|增值服务费", "role": "ignore"},
    ],
}

# 登记制承运商种子：议价/报销制（货拉拉、顺丰速运零星），不核价核量，只登记单据运费 + 轻核单号真实。
_REGISTER_CARRIERS = [
    {"carrier": "货拉拉", "review_mode": "register",
     "note": "同城/整车议价、员工报销制；无合同价目表、不按件数重量计费。登记 单据号(FBDR/CGRK)+费用+主体+需求部门，轻核单号在金蝶存在。"},
]


def seed_pilot(engine):
    """迅鸽取数说明 pilot 种子 + 登记制承运商种子（表为空才插；价格卡走「导入合同价目表」）。幂等。"""
    import json
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with engine.begin() as c:
        if not c.execute(select(intake_spec.c.id).limit(1)).first():
            c.execute(insert(intake_spec).values(carrier="迅鸽",
                spec_json=json.dumps(_XUNGE_SPEC, ensure_ascii=False),
                updated_by="种子(迅鸽pilot)", updated_at=now))
        have = {r[0] for r in c.execute(select(intake_spec.c.carrier)).all()}
        for rc in _REGISTER_CARRIERS:
            if rc["carrier"] not in have:
                c.execute(insert(intake_spec).values(carrier=rc["carrier"],
                    spec_json=json.dumps(rc, ensure_ascii=False),
                    updated_by="种子(登记制)", updated_at=now))


def migrate_cols(engine):
    """给已建的 logistics_bill_lines 补新列（create_all 只建不改）。MySQL/SQLite 兼容，缺列才补。"""
    from sqlalchemy import text
    drv = engine.url.drivername
    need = [("review_mode", "VARCHAR(10)"), ("subj_ovr", "VARCHAR(30)"), ("fee_ovr", "VARCHAR(40)")]
    with engine.begin() as c:
        if "mysql" in drv:
            have = {r[0] for r in c.execute(text("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                                                 "WHERE TABLE_NAME='logistics_bill_lines'")).fetchall()}
        else:
            have = {r[1] for r in c.execute(text("PRAGMA table_info(logistics_bill_lines)")).fetchall()}
        for col, typ in need:
            if col not in have:
                c.execute(text("ALTER TABLE logistics_bill_lines ADD COLUMN %s %s" % (col, typ)))
        # doc_no 加宽到 300：物流部一格写多个金蝶单号(换行/区间)，按"+"连起来 80 字不够(7单=83字、27单区间=296字)。
        # MySQL 用 MODIFY；SQLite 不校验 VARCHAR 长度，不用改。
        if "mysql" in drv:
            ln = c.execute(text("SELECT CHARACTER_MAXIMUM_LENGTH FROM INFORMATION_SCHEMA.COLUMNS "
                                "WHERE TABLE_NAME='logistics_bill_lines' AND COLUMN_NAME='doc_no'")).scalar()
            if ln is not None and int(ln) < 300:
                c.execute(text("ALTER TABLE logistics_bill_lines MODIFY doc_no VARCHAR(300)"))
        # 计提更正表(V2.697建)：补产品项目列；应改为改存「编码 名称」，MySQL 加宽到 120
        if "mysql" in drv:
            fx = {r[0]: r[1] for r in c.execute(text("SELECT COLUMN_NAME, CHARACTER_MAXIMUM_LENGTH FROM INFORMATION_SCHEMA.COLUMNS "
                                                     "WHERE TABLE_NAME='logistics_review_line_fix'")).fetchall()}
        else:
            fx = {r[1]: None for r in c.execute(text("PRAGMA table_info(logistics_review_line_fix)")).fetchall()}
        if fx:
            if "to_proj" not in fx:
                c.execute(text("ALTER TABLE logistics_review_line_fix ADD COLUMN to_proj VARCHAR(120)"))
            if "adj_period" not in fx:
                c.execute(text("ALTER TABLE logistics_review_line_fix ADD COLUMN adj_period VARCHAR(7)"))
            if "to_amt" not in fx:
                c.execute(text("ALTER TABLE logistics_review_line_fix ADD COLUMN to_amt VARCHAR(30)"))
            if "mysql" in drv:
                for col in ("to_acct", "to_fee", "to_dept", "to_biz"):
                    if fx.get(col) is not None and int(fx[col]) < 120:
                        c.execute(text("ALTER TABLE logistics_review_line_fix MODIFY %s VARCHAR(120)" % col))
