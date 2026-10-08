"""Preparation is evidence availability, not a financial reconciliation verdict."""
KINDS = {'order':'平台订单', 'item':'商品与子订单', 'wdt':'旺店通销售出库',
         'alipay':'支付宝流水', 'fund':'聚合账户流水', 'refund':'退款售后明细', 'kingdee':'金蝶应收单'}
TARGET = '星期零STARFIELD 天猫官旗店'
KINDS['price_protection']='天猫价保赔付（补充）'
# V2.811 抖音：一份订单维度动账明细（对金额、拆费用）+ 一份带余额的账户流水（对余额）
DOUYIN_KINDS = {'dy_settle':'抖音动账明细（订单维度）', 'dy_ledger':'抖音账户流水（带余额）', 'dy_orders':'旺店通订单明细（认合单）',
                'dy_platform':'抖音平台订单明细（抖店订单导出）'}   # V2.816 加第三类；平台订单明细是第四类（下单时的订单金额、状态、售后）
# 每类资料是干什么用的，写在清单上（会计的话）
PURPOSE = {'dy_settle':'对金额、拆平台扣费：每笔结算到账多少、扣了什么', 'dy_ledger':'对账户余额：账户里每一笔进出和当时余额',
           'dy_orders':'认合单发货：几个平台订单合成一张单发的，金蝶应收只记其中一个订单号',
           'dy_platform':'核蓝字金额、看没结算的单在平台上是等结算、售后中还是已关闭；不传也能对账和下推'}
KINDS.update(DOUYIN_KINDS)
# 旺店通退换单：只用来说明"为什么金蝶没红字"，不传不影响对账和下推，所以不算进齐套（不在 DOUYIN_KINDS 里）
DOUYIN_OPTIONAL = {'dy_returns':'旺店通退换单（退款不退货等）'}
PURPOSE['dy_returns'] = '说明差异原因：哪些单在旺店通登记了退款不退货——这类没有退货入库，金蝶不会自动出红字，要手工补；不传不影响对账和下推'
KINDS.update(DOUYIN_OPTIONAL)


def requirements(settings, shop, platform=''):
    # Only the already-agreed pilot has defaults; other shops must choose their materials.
    # 抖音店默认就是那两份动账明细，不用逐店去配。
    default = ([k for k in KINDS if k!='price_protection' and k not in DOUYIN_KINDS and k not in DOUYIN_OPTIONAL] if shop == TARGET
               else list(DOUYIN_KINDS) if platform == '抖音' else [])
    # 抖音店固定这四类：基础资料页没有抖音资料的勾选项，那一行一旦被动过保存，存下来的就不含抖音资料，清单会整个消失
    if platform == '抖音': return list(DOUYIN_KINDS)
    value = settings.get(shop, default)
    return [kind for kind in KINDS if kind in value and kind not in DOUYIN_OPTIONAL] if isinstance(value, list) else []


def validate(settings):
    if not isinstance(settings, dict):
        raise ValueError('资料准备配置必须按店铺设置')
    for shop, kinds in settings.items():
        if not isinstance(shop, str) or not isinstance(kinds, list) or any(k not in KINDS for k in kinds):
            raise ValueError('资料准备配置含不支持的资料类型')
        if len(kinds) != len(set(kinds)):
            raise ValueError('同一店铺资料类型不能重复')
    return settings


def progress(cards):
    all_cards=cards
    cards=[c for c in cards if not c.get('optional')]
    ready = sum(bool(c.get('available')) and c.get('state') == 'ready' for c in cards)
    required = len(cards)
    return {'ready':ready, 'required':required, 'files':sum(c.get('file_count', 0) for c in all_cards),
            'readiness':'ready' if required and ready == required else 'linked',
            'configured':bool(required)}
