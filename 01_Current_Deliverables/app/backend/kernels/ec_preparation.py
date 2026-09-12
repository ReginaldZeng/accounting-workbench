"""Preparation is evidence availability, not a financial reconciliation verdict."""
KINDS = {'order':'平台订单', 'item':'商品与子订单', 'wdt':'旺店通销售出库',
         'alipay':'支付宝流水', 'fund':'聚合账户流水', 'refund':'退款售后明细', 'kingdee':'金蝶应收单'}
TARGET = '星期零STARFIELD 天猫官旗店'


def requirements(settings, shop):
    # Only the already-agreed pilot has defaults; other shops must choose their materials.
    value = settings.get(shop, list(KINDS) if shop == TARGET else [])
    return [kind for kind in KINDS if kind in value] if isinstance(value, list) else []


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
    ready = sum(bool(c.get('available')) and c.get('state') == 'ready' for c in cards)
    required = len(cards)
    return {'ready':ready, 'required':required, 'files':sum(c.get('file_count', 0) for c in cards),
            'readiness':'ready' if required and ready == required else 'linked',
            'configured':bool(required)}
