# [Change Log] Date: 2026-09-12 | Author: Codex | Version: V2.564
# Description: Pure menu layout numbering and sibling insertion; function identities stay stable.
import re

PREFIXES = {'report': 'A1', 'gl': 'B1', 'ap': 'C1', 'cost': 'D1', 'ar': 'E1', 'misc': 'F1', 'common': 'S1'}


def section_codes(sections):
    return {s['key']: PREFIXES.get(s['key'], 'X' + str(i + 1)) for i, s in enumerate(sections)}


def numbered_modules(modules, sections):
    codes = section_codes(sections)
    result = []
    for sec in sections:
        roots = sorted((m for m in modules if m['sec'] == sec['key'] and not m.get('parent')),
                       key=lambda m: (m.get('order', 500), m['key']))
        for i, root in enumerate(roots, 1):
            layout = f"{codes[sec['key']]}-{i:02d}"
            result.append({**root, 'layout': layout})
            children = sorted((m for m in modules if m.get('parent') == root['key']),
                              key=lambda m: (m.get('order', 500), m['key']))
            for j, child in enumerate(children, 1):
                result.append({**child, 'sec': root['sec'], 'layout': f'{layout}-{j:03d}'})
    return result


def layout_patch(modules, sections, key, layout):
    """Return all changes for an insertion, validating before any persistence."""
    match = re.fullmatch(r'([A-Z][1-9][0-9]*)-([0-9]{2})(?:-([0-9]{3}))?', str(layout).strip().upper())
    if not match:
        raise ValueError('布局序号格式：B1-01（二级）或 B1-01-001（三级）')
    prefix, root_pos, child_pos = match.groups()
    sec = next((k for k, v in section_codes(sections).items() if v == prefix), None)
    if not sec:
        raise ValueError('布局序号中的板块不存在')
    item = next(m for m in modules if m['key'] == key)
    if item.get('always') or sec == 'common':
        raise ValueError('平台固定入口不可调整布局')
    parent = None
    if child_pos is not None:
        parent_mod = next((m for m in modules if m.get('layout') == f'{prefix}-{root_pos}'), None)
        if not parent_mod or parent_mod['key'] == key:
            raise ValueError('请选择已有的二级父菜单，且不能挂在自己下面')
        if any(m.get('parent') == key for m in modules):
            raise ValueError('该菜单有子项，不能移成三级菜单')
        parent = parent_mod['key']
    position = int(child_pos if child_pos is not None else root_pos)
    siblings = sorted((m for m in modules if m['key'] != key and m['sec'] == sec and m.get('parent') == parent),
                      key=lambda m: (m.get('order', 500), m['key']))
    if not 1 <= position <= len(siblings) + 1:
        raise ValueError(f'同级位置可填 1 至 {len(siblings) + 1}；插入时后续菜单自动顺移')
    siblings.insert(position - 1, item)
    return {m['key']: {'order': (i + 1) * 10, **({'sec': sec, 'parent': parent} if m['key'] == key else {})}
            for i, m in enumerate(siblings)}
