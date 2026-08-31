# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-07-10 | Author: Claude / c | Version: V2.77
# Description: 凭证归档工具·内核。纯函数（不碰 DB），把「唯一会算错账」的四件事集中在这里，便于单测：
#              ①册号生成 ②号段校验（重叠/跳号）③号段体检（对金蝶找缺口）④「已装箱」显示态派生
#              ⑤批量转移的并发冲突判定。DB 读写在 db.py，本模块只做判断与计算。

VTYPE_DEFAULT = "记账凭证"
STORE_STATES = ("在库", "借出中", "待销毁", "已销毁")   # 存储态（DB 只存这四种）
BOX_NTYPE = "箱"                                        # 挂在「箱」节点下 → 显示「已装箱」
# 会计凭证法定保存期：默认 30 年（自会计年度终了起算），做成可配置，业务方核实后可改
DEFAULT_KEEP_YEARS = 30


def make_vol_no(code, year, month, seq):
    """册号 = 简码 + 年 + 两位月 + 两位册序，如 SZL2026-03-02。"""
    code = str(code or "").strip().upper()
    if not code:
        raise ValueError("该主体还没有简码，去「基础数据 › 主体档案」补上才能生成册号")
    return "%s%04d-%02d-%02d" % (code, int(year), int(month), int(seq))


def next_seq(existing_seqs):
    """该主体该期间已有册序 → 下一册序。空则从 1 起；不假设连续，取 max+1。"""
    return (max(existing_seqs) + 1) if existing_seqs else 1


def check_range(existing, no_from, no_to):
    """登记新册前的号段校验。existing = 该主体该期间已登记册子的 [(起,止), ...]（不含本册）。
    返回 (ok, problems[])。号段必须：起≤止、与已有互不重叠、与紧邻的上一本首尾相接不跳号。"""
    problems = []
    try:
        a, b = int(no_from), int(no_to)
    except (TypeError, ValueError):
        return False, ["凭证号起止都要填数字"]
    if a <= 0 or b <= 0:
        return False, ["凭证号要是正整数"]
    if a > b:
        return False, ["起号 %d 比止号 %d 还大，反了" % (a, b)]

    for (x, y) in existing:
        if a <= y and x <= b:                 # 区间相交
            problems.append("与已登记的第 %d–%d 号有重叠 —— 会导致一个凭证号查出两本" % (x, y))

    # 跳号：紧邻的上一本（止号 < 本册起号里最大的那个）应恰好 = a-1
    prior = [y for (x, y) in existing if y < a]
    if prior:
        top = max(prior)
        if top != a - 1:
            problems.append("上一本止于 %d 号，本册却从 %d 号起，中间第 %d–%d 号没人收 —— 疑似漏装订"
                            % (top, a, top + 1, a - 1))
    return (not problems), problems


def range_gaps(existing, kingdee_max):
    """号段体检：该主体该期间已登记册子覆盖 existing=[(起,止)...]，金蝶实际共 kingdee_max 张。
    返回未被任何册子覆盖的号段列表 [(缺起, 缺止), ...]——通常是漏装订或册子丢了。"""
    if not kingdee_max or kingdee_max <= 0:
        return []
    covered = [False] * (kingdee_max + 1)     # 下标 1..kingdee_max
    for (x, y) in existing:
        for n in range(max(1, int(x)), min(kingdee_max, int(y)) + 1):
            covered[n] = True
    gaps, start = [], None
    for n in range(1, kingdee_max + 1):
        if not covered[n] and start is None:
            start = n
        elif covered[n] and start is not None:
            gaps.append((start, n - 1)); start = None
    if start is not None:
        gaps.append((start, kingdee_max))
    return gaps


def display_status(stored_status, node_type):
    """显示态：存储态=在库 且 当前挂在「箱」节点下 → 「已装箱」；否则原样返回存储态。
    这样「已装箱」永远由位置派生，不作字段存储，状态与位置不可能自相矛盾。"""
    if stored_status == "在库" and node_type == BOX_NTYPE:
        return "已装箱"
    return stored_status


def keep_until(year, keep_years=DEFAULT_KEEP_YEARS):
    """保存到期年份 = 会计年度 + 保存年限。"""
    return int(year) + int(keep_years)


def transfer_conflicts(expected, actual):
    """批量转移的并发冲突判定（乐观锁）。
    expected = {册号: 勾选时看到的位置id}；actual = {册号: 现在的真实位置id}。
    返回冲突册号列表——它们在你勾选之后被别人挪走了。宁可拒绝重来，不可静默覆盖让台账骗人。"""
    bad = []
    for vol, exp in expected.items():
        act = actual.get(vol)
        if act != exp:
            bad.append(vol)
    return bad


def can_transfer_into(node_type, terminal):
    """能否把册子转入该位置。终态节点（销毁批次）只进不出，且只能由销毁流程写入，普通转移不许选。"""
    return not (str(terminal) == "1")
