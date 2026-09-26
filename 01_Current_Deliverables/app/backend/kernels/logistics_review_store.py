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
    Column("subject", String(30)),         # 费用主体简称
    Column("doc_no", String(80)),          # 金蝶单号（一行多单以 + 连接）
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

TABLES = [bill_lines, price_card, intake_spec]

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


def seed_pilot(engine):
    """迅鸽取数说明 pilot 种子（表为空才插；价格卡走「导入合同价目表」不硬编码）。幂等，服务器重启安全。"""
    import json
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with engine.begin() as c:
        if not c.execute(select(intake_spec.c.id).limit(1)).first():
            c.execute(insert(intake_spec).values(carrier="迅鸽",
                spec_json=json.dumps(_XUNGE_SPEC, ensure_ascii=False),
                updated_by="种子(迅鸽pilot)", updated_at=now))
