# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-09-26 | Author: Claude Opus 4.8 | Version: V2.632
# Description: 【物流账单复核】通用解析器——按「取数说明」(intake_spec) 认列，不写死序号（同一家导出月间列会漂移，写死必错）。
#   一张 sheet 按 spec 的角色解析：detail=对账逐单(带单号)、accrual=计提口径(月结按费用项)、ignore=价目表跳过。
#   出中间表行(dict)。表名按前缀/正则匹配月度变动（如「*发货明细」）。落库/核价核量在 router 串起来。
import re
import json


def _s(v):
    return "" if v is None else str(v).strip()


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def match_sheet(spec_name, real_name):
    """spec 里的 name 支持精确、前缀 * 通配、和 | 分隔的正则关键词。"""
    if spec_name == real_name:
        return True
    if "*" in spec_name or "|" in spec_name:
        pat = spec_name.replace("*", ".*")
        for part in pat.split("|"):
            if re.search(part, real_name):
                return True
    return False


def find_col(hdr, name_or_list):
    """按列名（或别名列表）在表头找列号，取首个命中。认名不认序号。"""
    names = name_or_list if isinstance(name_or_list, list) else [name_or_list]
    for n in names:
        for i, h in enumerate(hdr):
            if h == n:
                return i
    return None


def find_col_nth(hdr, spec_col):
    """列名可写成 [名, 第几次出现]：表头同名列(如恒茂「类型」出现两三次)取指定那一个。"""
    if isinstance(spec_col, list) and len(spec_col) == 2 and isinstance(spec_col[1], int):
        name, n = spec_col
        hits = [i for i, h in enumerate(hdr) if h == name]
        return hits[n - 1] if len(hits) >= n else None
    return find_col(hdr, spec_col)


def split_nos(no):
    """一格多单号 A+B / A/B / A，B → [A,B]。"""
    parts = re.split(r"[+/,，、;；\s]+", no or "")
    return [p for p in parts if p]


def _subject(spec_sub, row, hdr):
    if not spec_sub:
        return ""
    if "fixed" in spec_sub:
        return spec_sub["fixed"]
    if "column" in spec_sub:
        ci = find_col(hdr, spec_sub["column"])
        return _s(row[ci]) if ci is not None and ci < len(row) else ""
    return ""


def _box_price(v, prices):
    """箱型 → 单价：'7号超硬' / '原箱+3号五层'(几种相加) / '快递袋25*35'；取不到返回 None。"""
    tot, hit = 0.0, False
    for part in re.split(r"[+＋]", _s(v)):
        part = part.strip()
        if not part:
            continue
        pr = prices.get(part)
        if pr is None:
            pr = next((x for k, x in prices.items() if k.startswith(part) or part.startswith(k)), None)
        if pr is None:
            return None
        tot += pr
        hit = True
    return round(tot, 2) if hit else None


def box_prices_from(wb, spec):
    """从月结清单「物料费」段读箱子单价：{名称/备注简称: 单价}。备注列正好是发货明细「箱型」的叫法(7号超硬/1号五层)。"""
    acc = [c["name"] for c in spec.get("sheets", []) if c.get("role") == "accrual"]
    out = {}
    for ws in wb.worksheets:
        if not any(match_sheet(nm, ws.title) for nm in acc):
            continue
        in_mat = False
        for raw in ws.iter_rows(values_only=True):
            cells = [_s(c) for c in raw]
            k0 = next((k for k, c in enumerate(cells) if c), None)
            if k0 is None:
                continue
            if not _is_idx(raw[k0]) and any("金额" in c for c in cells):
                in_mat = cells[k0] == "物料费"
                continue
            if not in_mat or not _is_idx(raw[k0]):
                continue
            txt = [c for c in cells[k0 + 1:] if c and _f(c) is None and c not in ("物料中心", "运营中心", "后勤中心")]
            nums = [_f(c) for c in raw[k0 + 1:] if _f(c) is not None]
            if not txt or len(nums) < 2:
                continue
            price = nums[1] if len(nums) >= 3 else (nums[1] if nums[0] else nums[-1])
            for key in txt[:2]:        # 名称 + 备注简称
                out.setdefault(key, price)
    return out


def parse_detail_sheet(sp, ws, period, carrier, box_prices=None):
    """detail 角色 sheet → 逐单据行。按 spec 的 doc_col/amount_cols/qty_col/wt_col/prov_col/carrier_sub_col 认列。
    spec.fee_parts={分项名: [列名…]} 时按分项求和记 sub_fees；spec.box_col 时按箱型查汇总页物料单价加「箱子」分项(迅鸽 V2.720)。"""
    # 逐行读、连续 300 行空就停(恒茂入库页带 104 万行空格式，整表 list 会拖死)
    rows, blank = [], 0
    for r in ws.iter_rows(values_only=True):
        rows.append(r)
        blank = 0 if any(x not in (None, "") for x in r) else blank + 1
        if blank >= 300:
            break
    hr = int(sp.get("header_row", 1)) - 1
    if hr >= len(rows):
        return []
    hdr = [_s(x) for x in rows[hr]]
    c_doc = find_col(hdr, sp["doc_col"]) if sp.get("doc_col") else None
    c_annot = find_col_nth(hdr, sp["annot_col"]) if sp.get("annot_col") else None
    c_rtype = find_col_nth(hdr, sp["row_type"]["col"]) if sp.get("row_type") else None
    calc = sp.get("calc")
    c_calc_t = find_col(hdr, calc["target"]) if calc else None
    c_calc_f = [find_col(hdr, x) for x in calc.get("factors", [])] if calc else []
    last_doc = ""
    c_amts = [find_col(hdr, n) for n in sp["amount_cols"]] if sp.get("amount_cols") else \
             ([find_col(hdr, sp["amount_col"])] if sp.get("amount_col") else [])
    c_amts = [c for c in c_amts if c is not None]
    c_qty = find_col(hdr, sp["qty_col"]) if sp.get("qty_col") else None
    c_wt = find_col(hdr, sp["wt_col"]) if sp.get("wt_col") else None
    c_prov = find_col(hdr, sp["prov_col"]) if sp.get("prov_col") else None
    c_cs = find_col(hdr, sp["carrier_sub_col"]) if sp.get("carrier_sub_col") else None
    parts = {nm: [c for c in (find_col(hdr, x) for x in cols) if c is not None] for nm, cols in (sp.get("fee_parts") or {}).items()}
    c_box = find_col(hdr, sp["box_col"]) if sp.get("box_col") else None
    marker = sp.get("summary_marker")
    out = []
    for ri, r in enumerate(rows[hr + 1:], start=hr + 2):
        if not any(x is not None for x in r):
            continue
        if marker and _s(r[0]) and marker in _s(r[0]):
            continue
        if c_rtype is not None and _s(r[c_rtype] if c_rtype < len(r) else "") not in sp["row_type"]["in"]:
            continue                      # 只取指定类型的行(合计行、别的类型跳过)
        doc = _s(r[c_doc]) if c_doc is not None and c_doc < len(r) else _s(sp.get("doc", ""))
        if c_doc is not None and not doc and sp.get("doc_ffill") and last_doc:
            doc = last_doc                # 单号只写在首行、下面几行沿用(恒茂入库：一张调拨单拆几个批次)
        if c_doc is not None and not doc:
            doc = _s(sp.get("doc_default", ""))
            if not doc:
                continue
        if c_doc is not None and _s(r[c_doc] if c_doc < len(r) else ""):
            last_doc = doc
        base = sum((_f(r[c]) or 0) for c in c_amts) if c_amts else None
        sub = {}
        if parts:
            for nm, cs in parts.items():
                v = round(sum((_f(r[c]) or 0) for c in cs if c < len(r)), 2)
                if v:
                    sub[nm] = v
            if c_box is not None and c_box < len(r) and _s(r[c_box]):
                bp = _box_price(r[c_box], box_prices or {})
                sub["箱子"] = bp if bp is not None else 0.0
                sub["箱型"] = _s(r[c_box]) + ("" if bp is not None else "(单价未识别)")   # 文字，不参与求和
            base = sum(v for v in sub.values() if isinstance(v, (int, float)))
        row = {
            "period": period, "carrier": carrier, "grain": "detail",
            "subject": _subject(sp.get("subject"), r, hdr),
            "doc_no": "+".join(split_nos(doc)) if doc and doc != "无单据" else doc,
            "annot": _annot_of(sp, r, c_annot), "fee_item": sp.get("fee_item", ""),
            "qty": _f(r[c_qty]) if c_qty is not None and c_qty < len(r) else None,
            "unit": sp.get("qty_unit", ""),
            "amount": round(base, 2) if base is not None else None,
            "base_amount": round(_f(r[c_amts[0]]) or 0, 2) if c_amts else None,
            "carrier_sub": _s(r[c_cs]) if c_cs is not None and c_cs < len(r) else "",
            "prov": _s(r[c_prov]) if c_prov is not None and c_prov < len(r) else "",
            "charge_wt": _f(r[c_wt]) if c_wt is not None and c_wt < len(r) else None,
            "src_sheet": ws.title, "src_row": ri,
        }
        if calc and c_calc_t is not None and c_calc_t < len(r) and _f(r[c_calc_t]) is not None:
            # 按账单公式核价：标准=各因子相乘×rate(四舍五入到分)，与账单金额差超 1 分记「核价差」
            fs = [_f(r[c]) if c is not None and c < len(r) else None for c in c_calc_f]
            if all(x is not None for x in fs):
                std = 1.0
                for x in fs:
                    std *= x
                std = round(std * float(calc.get("rate", 1)), 2)
                tv = round(_f(r[c_calc_t]), 2)
                sub2 = json.loads(row.get("sub_fees") or "{}") if row.get("sub_fees") else {}
                sub2[calc["target"]] = tv
                sub2["标准"] = std
                sub2["公式"] = calc.get("label", "")
                if abs(tv - std) > 0.01:
                    sub2["核价差"] = round(tv - std, 2)
                row["sub_fees"] = json.dumps(sub2, ensure_ascii=False)
        if sub:
            row["sub_fees"] = json.dumps({**sub, **(json.loads(row["sub_fees"]) if row.get("sub_fees") else {})}, ensure_ascii=False)
        out.append(row)
    return out


def _annot_of(sp, r, c_annot):
    """标注：spec.annot_col 取该列(可配 annot_map 改写、annot_fmt 套模板如 仓储费-{})，否则用固定 annot。"""
    if c_annot is None or c_annot >= len(r) or not _s(r[c_annot]):
        return sp.get("annot", "")
    v = _s(r[c_annot])
    v = (sp.get("annot_map") or {}).get(v, v)
    return sp.get("annot_fmt", "{}").format(v)


def _is_idx(v):
    """月结清单明细行首格是序号(1、2、3…)。"""
    f = _f(v)
    return f is not None and float(f).is_integer() and 0 < f < 1000


def parse_accrual_sheet(sp, ws, period, carrier):
    """accrual 角色 sheet（月结清单，半结构）→ 按费用项一行。
    ① fee_map 名单内的费用：行内费用取本行金额；段头费用(物料费/第三方快递费等)取本段「合计」，段内明细不再另取。
    ② 名单外、带序号的费用行(如 卸货、B2C续件、B2B基础操作费、冲红·7月纸箱差异)：spec 配了 default_annot 就按它逐行收进来，
       金额为 0 的跳过——以前只认名单，名单外整行丢掉不提示(V2.715 修，迅鸽 8 月漏读 1,548+329.25)。没配 default_annot 保持旧行为。"""
    rows = list(ws.iter_rows(values_only=True))
    n = len(rows)
    fee_map = sp.get("fee_map", {})
    dflt = sp.get("default_annot")
    subj = sp.get("subject", {}).get("fixed", "")
    out = []
    in_total_sec = False      # 当前在名单内「段头费用」段里(段内明细不另取)
    for ri in range(n):
        raw = rows[ri]
        cells = [_s(c) for c in raw]
        label = next((c for c in cells if c in fee_map), None)
        if label:
            k0 = next((k for k, c in enumerate(cells) if c), 0)
            nums = [_f(c) for c in (raw[k0 + 1:] if _is_idx(raw[k0]) else raw) if _f(c) is not None]   # 序号不算数量
            if len(nums) >= 2:
                # 行内费用（名字与金额同一行）：金额=末值，数量=其余最大值
                amt, qty = nums[-1], max(nums[:-1], default=None)
            else:
                # 段头费用（名字在段头，金额在本段「合计」行）：往下找到合计取数，遇到下一段费用名即止
                in_total_sec = True
                amt = qty = None
                for j in range(ri + 1, min(ri + 40, n)):
                    jc = [_s(c) for c in rows[j]]
                    if any(c in fee_map for c in jc):
                        break
                    if jc and any("合计" in c for c in jc[:2]):
                        jn = [_f(c) for c in rows[j] if _f(c) is not None]
                        if jn:
                            amt, qty = jn[-1], (max(jn[:-1], default=None) if len(jn) > 1 else None)
                        break
            out.append({"period": period, "carrier": carrier, "grain": "accrual", "subject": subj,
                        "doc_no": "", "annot": fee_map[label], "fee_item": label,
                        "qty": qty, "unit": "", "amount": amt, "src_sheet": ws.title, "src_row": ri + 1})
            continue
        # 表格常从 B 列起(A 列空)：按每行第一个非空格判断段头/序号
        k0 = next((k for k, c in enumerate(cells) if c), None)
        if k0 is None:
            continue
        if not _is_idx(raw[k0]) and any("金额" in c for c in cells):
            in_total_sec = False      # 名单外的段头(服务费/冲红/仓储费…)：新段开始
            continue
        if dflt and not in_total_sec and _is_idx(raw[k0]):
            name = next((c for c in cells[k0 + 1:] if c and _f(c) is None), "")
            nums = [_f(c) for c in raw[k0 + 1:] if _f(c) is not None]
            if not name or not nums or not nums[-1]:
                continue
            out.append({"period": period, "carrier": carrier, "grain": "accrual", "subject": subj,
                        "doc_no": "", "annot": dflt, "fee_item": name,
                        "qty": (max(nums[:-1]) if len(nums) > 1 else None), "unit": "", "amount": nums[-1],
                        "src_sheet": ws.title, "src_row": ri + 1})
    return out


def bill_owner(wb, spec):
    """账单货主(一家承运商一个月可能有几份账单，如迅鸽 starfield / kikiherb 两个货主)：
    取月结清单「结算时间」下一行的单格文字(星期零-starfield / 星期零kikiherb)，取不到用「客户名称」。"""
    acc = [c["name"] for c in spec.get("sheets", []) if c.get("role") == "accrual"]
    for ws in wb.worksheets:
        if not any(match_sheet(nm, ws.title) for nm in acc):
            continue
        rows = [[_s(c) for c in r] for r in ws.iter_rows(min_row=1, max_row=15, values_only=True)]
        cust = ""
        for i, r in enumerate(rows):
            vals = [c for c in r if c]
            if vals and vals[0].startswith("客户名称") and len(vals) > 1:
                cust = vals[1]
            if vals and vals[0].startswith("结算时间"):
                for r2 in rows[i + 1:i + 8]:          # 中间隔着到期时间/收款员/空行
                    v2 = [c for c in r2 if c]
                    if len(v2) == 1 and _f(v2[0]) is None:
                        return v2[0]
        if cust:
            return cust
    return ""


def parse_bill(spec, data):
    """按取数说明解析整本账单 xlsx → {'detail':[...], 'accrual':[...], 'skipped':[表名], 'period':...}。
    data=bytes 或路径。period 从 spec 传入或调用方补。"""
    import openpyxl
    from io import BytesIO
    src = BytesIO(data) if isinstance(data, (bytes, bytearray)) else data
    wb = openpyxl.load_workbook(src, read_only=True, data_only=True)
    carrier = spec.get("carrier", "")
    period = spec.get("period", "")
    detail, accrual, skipped = [], [], []
    boxp = box_prices_from(wb, spec) if any(c.get("box_col") for c in spec.get("sheets", [])) else {}
    per_row = []     # (sheet spec, 该表 detail 行)：per_row_fees 要等汇总页单价
    for ws in wb.worksheets:
        sp = None
        for cand in spec.get("sheets", []):
            if match_sheet(cand["name"], ws.title):
                sp = cand
                break
        if sp is None or sp.get("role") == "ignore":
            skipped.append(ws.title)
            continue
        if sp["role"] == "detail":
            got = parse_detail_sheet(sp, ws, period, carrier, boxp)
            detail += got
            if sp.get("per_row_fees"):
                per_row.append((sp, got))
        elif sp["role"] == "accrual":
            accrual += parse_accrual_sheet(sp, ws, period, carrier)
    # 逐单固定费(spec.per_row_fees={分项名: 汇总页费用项})：单价=汇总页该项 金额÷数量，每行(一单)挂一份。
    # 如迅鸽退件表每张退货单挂「退货服务费」2元(=222÷111)，V2.721
    if per_row:
        unit = {}
        for a in accrual:
            if a.get("qty") and a.get("amount"):
                unit.setdefault(a["fee_item"], round(a["amount"] / a["qty"], 4))
        for sp, rows in per_row:
            for r in rows:
                try:
                    sub = json.loads(r.get("sub_fees") or "{}") or {}
                except Exception:
                    sub = {}
                for nm, src in sp["per_row_fees"].items():
                    p = unit.get(src)
                    if p:
                        sub[nm] = p
                if sub:
                    r["sub_fees"] = json.dumps(sub, ensure_ascii=False)
                    r["amount"] = round(sum(v for v in sub.values() if isinstance(v, (int, float))), 2)
    owner = bill_owner(wb, spec)
    # 货主→产品线(spec.owner_bizline，如 kikiherb→Kiki Herb)：同一套标注下的另一个货主，产品线跟货主走
    biz = next((v for k, v in (spec.get("owner_bizline") or {}).items() if k.lower() in owner.lower()), "")
    for r in detail + accrual:
        r["bill_src"] = owner
        if biz and not r.get("bizline"):
            r["bizline"] = biz
    return {"detail": detail, "accrual": accrual, "skipped": skipped, "carrier": carrier, "period": period,
            "bill_src": owner}
