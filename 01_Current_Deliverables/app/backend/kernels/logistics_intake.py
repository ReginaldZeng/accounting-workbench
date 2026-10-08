# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-09-26 | Author: Claude Opus 4.8 | Version: V2.632
# V2.761：读老格式 .xls(xlrd)；列名 * 结尾按前缀认；wt_scale 账单重量换千克(链盟接入)
# V2.766：part_col 分项名取列值；wt_once_col 同一运单重量只算一次(顺丰冷运取数说明修复)
# V2.765：src_from_sheets 份名取表名(多文件各算一份)；amount_dp 金额小数位(天鹰接入)
# V2.764：表名 {m} 月份占位；row_re 行过滤；doc_blank 单号「无」当空；collapse 整表并一行(易风达接入)
# V2.762：doc_re 单号格式过滤(跨越/中通账单底下带透视小计，单号列会读到「总计」)；dedupe_col 跨 sheet 按运单号去重
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
    """按列名（或别名列表）在表头找列号，取首个命中。认名不认序号。
    名字以 * 结尾按前缀认(链盟「蜜雪卸货费/0.45元/箱(含6%税,单独开票)」这类长表头，V2.761)。"""
    names = name_or_list if isinstance(name_or_list, list) else [name_or_list]
    for n in names:
        for i, h in enumerate(hdr):
            if h == n or (isinstance(n, str) and n.endswith("*") and len(n) > 1 and h.startswith(n[:-1])):
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


def merged_rows(data):
    """xlsx 里跨行的合并单元格 → {表名: {列号(0 起): {行号(1 起): 合并块首行的行号}}}，只记首行以下的那几行。
    V2.864(用户看天鹰 8 月不符的单「是不是有一些合并单元格啊」)：物流部把同一张单的两三行在「金蝶单号」列合并成一格，
    下面几行读出来是空的；这几行自己又写了序号、日期，原来按「日期序号齐全却没单号＝漏填」甩成无单据，那张单的重量就少了一半。
    合并单元格是物流部明写的「这几行同一张单」，比按空不空去猜可靠。直接读 xlsx 里的 mergeCells(账单是只读方式打开的，拿不到合并信息；
    恒茂入库页带一百多万行空格式，也不能整本正常打开)，按块读、不把整张表放进内存。老格式 .xls、读不出来的一律当没有合并。"""
    import zipfile
    from io import BytesIO
    from html import unescape
    out = {}
    try:
        zf = zipfile.ZipFile(BytesIO(bytes(data)) if isinstance(data, (bytes, bytearray)) else data)
        attr = lambda tag, k: (re.search(r'(?:^|\s)%s="([^"]*)"' % k, tag) or [None, ""])[1]      # 不用 XML 解析器(账单是外来文件)，两个小清单用正则抠
        rels = {attr(t, "Id"): attr(t, "Target") for t in re.findall(r"<Relationship\b[^>]*>", zf.read("xl/_rels/workbook.xml.rels")[:2000000].decode("utf-8", "ignore"))}
        names = set(zf.namelist())
        pat = re.compile(rb'mergeCell\s+ref="([A-Z]+)(\d+):([A-Z]+)(\d+)"')
        for tag in re.findall(r"<(?:\w+:)?sheet\b[^>]*>", zf.read("xl/workbook.xml")[:2000000].decode("utf-8", "ignore")):
            tgt = rels.get(attr(tag, r"\w+:id"), "")
            path = tgt.lstrip("/") if tgt.startswith("/") else "xl/" + tgt
            if path not in names:
                continue
            found, tail = set(), b""
            with zf.open(path) as fh:
                while True:
                    chunk = fh.read(1 << 20)
                    if not chunk:
                        break
                    buf = tail + chunk
                    if b"mergeCell" in buf:
                        found.update(m.groups() for m in pat.finditer(buf))
                    tail = buf[-200:]
                    if len(found) > 20000:
                        break
            cols = {}
            for c1, r1, c2, r2 in found:
                r1, r2 = int(r1), int(r2)
                if r2 <= r1 or r2 - r1 > 2000:
                    continue
                for ci in range(_col_idx(c1), _col_idx(c2) + 1):
                    d = cols.setdefault(ci, {})
                    for rr in range(r1 + 1, r2 + 1):
                        d[rr] = r1
            if cols:
                out[unescape(attr(tag, "name"))] = cols
    except Exception:
        return {}
    return out


def _col_idx(letters):
    n = 0
    for ch in letters.decode() if isinstance(letters, bytes) else letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def parse_detail_sheet(sp, ws, period, carrier, box_prices=None, merged=None):
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
    c_fee = find_col(hdr, sp["fee_col"]) if sp.get("fee_col") else None     # 物流部填的费用类型(规范第4列，V2.735)
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
    c_dk = find_col(hdr, sp["dedupe_col"]) if sp.get("dedupe_col") else None   # 跨 sheet 去重键(运单号)
    # row_re={"col": 列名, "re": 正则}：只收该列对得上的行(易风达运输页只收「序号」是数字的行，表底合计/开票信息/透视小计都不收，V2.764)
    # part_col：分项名取这一列的值(顺丰一个运单拆 运费/保费/签回单 几行，按「服务」列记分项)；
    # wt_once_col：同一运单(该列值相同)的几行重量、件数只算一次，并单时才不会把计费重量加三遍(V2.766)
    c_part = find_col(hdr, sp["part_col"]) if sp.get("part_col") else None
    c_once = find_col(hdr, sp["wt_once_col"]) if sp.get("wt_once_col") else None
    seen_once = set()
    rre = sp.get("row_re")
    c_rre = find_col(hdr, rre["col"]) if rre else None
    blank_docs = set(sp.get("doc_blank") or [])        # 单号列写「无」这类字的当没单号(走 doc_default)
    marker = sp.get("summary_marker")
    out = []
    for ri, r in enumerate(rows[hr + 1:], start=hr + 2):
        if not any(x is not None for x in r):
            continue
        if marker and any(marker in _s(x) for x in r[:int(sp.get("summary_cols", 1))]):
            continue                      # 合计/汇总行：默认只看首格；summary_cols=2 连第二格也看(链盟「汇总」在 B 列)
        if c_rtype is not None and _s(r[c_rtype] if c_rtype < len(r) else "") not in sp["row_type"]["in"]:
            continue                      # 只取指定类型的行(合计行、别的类型跳过)
        if rre and (c_rre is None or c_rre >= len(r) or not re.match(rre["re"], _s(r[c_rre]))):
            continue
        doc = _s(r[c_doc]) if c_doc is not None and c_doc < len(r) else _s(sp.get("doc", ""))
        if doc in blank_docs:
            doc = ""
        loose = ""                        # 单号列里写的不是单号、但这行要收(doc_re_else=default)：原文留作备注
        if doc and sp.get("doc_re") and doc != _s(sp.get("doc_default", "")) and not re.match(sp["doc_re"], doc):
            if sp.get("doc_re_else") != "default":
                continue                  # 单号列里不像单号的(账单底下的透视小计「总计」「孝感市…公司」)不收(V2.762)
            # V2.820(诚煜)：单号列里写的是说明(「延迟扣款，订单255083234」)，是一笔真的扣款——按无单据收，不沿用上一行的单号
            loose, doc = doc, _s(sp.get("doc_default", ""))
        if c_doc is not None and not doc and not loose and merged and merged.get(c_doc, {}).get(ri):
            # 单号这一格是合并单元格的下半截：就是上面那张单(V2.864)，不看这行写没写日期序号
            top = merged[c_doc][ri]
            v = _s(rows[top - 1][c_doc]) if top - 1 < len(rows) and c_doc < len(rows[top - 1]) else ""
            if v and v not in blank_docs and (not sp.get("doc_re") or re.match(sp["doc_re"], v)):
                doc = v
        if c_doc is not None and not doc and sp.get("doc_ffill") and last_doc:
            # 单号只写在首行、下面几行沿用(恒茂入库：一张调拨单拆几个批次)。
            # doc_ffill_if_blank=[列名…]：这些列里有空的才算续行(天鹰：续行不写日期/序号；日期序号齐全却没单号的是漏填，不能并到上一单，V2.765)
            cond = [find_col(hdr, x) for x in (sp.get("doc_ffill_if_blank") or [])]
            if not cond or any(ci is not None and (ci >= len(r) or not _s(r[ci])) for ci in cond):
                doc = last_doc
        if c_doc is not None and not doc:
            doc = _s(sp.get("doc_default", ""))
            if not doc:
                continue
        if c_doc is not None and _s(r[c_doc] if c_doc < len(r) else "") and not loose:
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
            "fee": _s(r[c_fee]) if c_fee is not None and c_fee < len(r) else "",
            "qty": _f(r[c_qty]) if c_qty is not None and c_qty < len(r) else None,
            "unit": sp.get("qty_unit", ""),
            # amount_dp：金额留几位小数(默认 2)。天鹰逐行是 吨×18.5 的长小数、供应商只在合计处取整，逐行先取整合计会差几分
            "amount": round(base, int(sp.get("amount_dp", 2))) if base is not None else None,
            "base_amount": round(_f(r[c_amts[0]]) or 0, 2) if c_amts else None,
            "carrier_sub": _s(r[c_cs]) if c_cs is not None and c_cs < len(r) else "",
            "prov": _s(r[c_prov]) if c_prov is not None and c_prov < len(r) else "",
            # wt_scale：账单重量单位换千克(链盟「重量/吨」配 1000)，核量统一比金蝶 kg
            "charge_wt": (round(_f(r[c_wt]) * float(sp.get("wt_scale", 1)), 3) if _f(r[c_wt]) is not None else None)
                         if c_wt is not None and c_wt < len(r) else None,
            "src_sheet": ws.title, "src_row": ri,
        }
        if loose:
            row["note"] = loose           # 单号列里写的说明原样留在备注里(复核台看得到这笔为什么没单据)
        if c_dk is not None and c_dk < len(r) and _s(r[c_dk]):
            row["_dk"] = _s(r[c_dk])
        if c_part is not None and c_part < len(r) and _s(r[c_part]) and base:
            sub[_s(r[c_part])] = round(base, 2)
        if c_once is not None and c_once < len(r) and _s(r[c_once]):
            if _s(r[c_once]) in seen_once:
                row["charge_wt"] = row["qty"] = None
            seen_once.add(_s(r[c_once]))
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
                if round(abs(tv - std), 2) > 0.01:      # 先取整再比，免得 0.01 被浮点噪声判成「超 1 分」
                    sub2["核价差"] = round(tv - std, 2)
                row["sub_fees"] = json.dumps(sub2, ensure_ascii=False)
        if sub:
            row["sub_fees"] = json.dumps({**sub, **(json.loads(row["sub_fees"]) if row.get("sub_fees") else {})}, ensure_ascii=False)
        out.append(row)
    if sp.get("collapse") and out:
        out = [_collapse(out)]
    return _merge_doc(out) if sp.get("merge_doc") else out


def _collapse(rows):
    """整张表并成一行(金额/数量/分项相加)：易风达仓储费一天一行，计提只有一笔，逐日没有单据可核(V2.764)。"""
    m = dict(rows[0])
    for f in ("amount", "base_amount", "qty"):
        vs = [r[f] for r in rows if r.get(f) is not None]
        m[f] = round(sum(vs), 2) if vs else None
    sub = {}
    for r in rows:
        for k, v in (json.loads(r["sub_fees"]) if r.get("sub_fees") else {}).items():
            if isinstance(v, (int, float)):
                sub[k] = round(sub.get(k, 0) + v, 2)
    if sub:
        m["sub_fees"] = json.dumps(sub, ensure_ascii=False)
    m["charge_wt"] = None
    return m


def _merge_doc(rows):
    """同一单号的几行并成一行(金额/数量/重量/分项相加，留首行出处)。链盟一张出库单分两车送同一仓，
    第二车只写卸货费、单号空(配 doc_ffill 沿用上一行)，并起来才能和金蝶整单重量比(V2.761)。无单据行不并。"""
    out, idx = [], {}
    for r in rows:
        k = r.get("doc_no")
        if not k or k == "无单据" or k not in idx:
            if k and k != "无单据":
                idx[k] = len(out)
            out.append(dict(r))
            continue
        m = out[idx[k]]
        for f in ("amount", "base_amount", "qty", "charge_wt"):
            if r.get(f) is not None:
                m[f] = round((m.get(f) or 0) + r[f], 6)      # 只去浮点噪声；金额小数位由各行 amount_dp 定
        if r.get("sub_fees"):
            a, b = json.loads(m.get("sub_fees") or "{}"), json.loads(r["sub_fees"])
            for kk, v in b.items():
                a[kk] = round(a.get(kk, 0) + v, 2) if isinstance(v, (int, float)) and isinstance(a.get(kk, 0), (int, float)) else a.get(kk, v)
            m["sub_fees"] = json.dumps(a, ensure_ascii=False)
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


class _XlsSheet:
    """老格式 .xls(xlrd) 套成 openpyxl 只读表的样子：title + iter_rows(values_only)。"""
    def __init__(self, sh):
        self.title = sh.name
        self._sh = sh

    def iter_rows(self, min_row=1, max_row=None, values_only=True):
        n = self._sh.nrows if max_row is None else min(max_row, self._sh.nrows)
        for i in range(max(0, min_row - 1), n):
            yield tuple(None if v == "" else v for v in self._sh.row_values(i))


class _XlsBook:
    def __init__(self, data):
        import xlrd
        bk = xlrd.open_workbook(file_contents=data) if isinstance(data, (bytes, bytearray)) else xlrd.open_workbook(data)
        self.worksheets = [_XlsSheet(sh) for sh in bk.sheets()]


def open_book(data):
    """xlsx 走 openpyxl；老格式 .xls(OLE 文件头 D0CF11E0，如链盟)走 xlrd(V2.761)。"""
    import openpyxl
    from io import BytesIO
    head = bytes(data[:4]) if isinstance(data, (bytes, bytearray)) else open(data, "rb").read(4)
    if head == bytes.fromhex("d0cf11e0"):
        return _XlsBook(data)
    src = BytesIO(data) if isinstance(data, (bytes, bytearray)) else data
    return openpyxl.load_workbook(src, read_only=True, data_only=True)


def parse_bill(spec, data):
    """按取数说明解析整本账单 xlsx/xls → {'detail':[...], 'accrual':[...], 'skipped':[表名], 'period':...}。
    data=bytes 或路径。period 从 spec 传入或调用方补。"""
    wb = open_book(data)
    mg = merged_rows(data)
    carrier = spec.get("carrier", "")
    period = spec.get("period", "")
    detail, accrual, skipped = [], [], []
    boxp = box_prices_from(wb, spec) if any(c.get("box_col") for c in spec.get("sheets", [])) else {}
    per_row = []     # (sheet spec, 该表 detail 行)：per_row_fees 要等汇总页单价
    # 表名里的 {m} 换成账期月份(易风达一本表留着历月的页：只认「{m}月运输」「{m}月仓储费」，V2.764)
    mon = str(int(period[5:7])) if len(period) >= 7 and period[5:7].isdigit() else ""
    for ws in wb.worksheets:
        sp = None
        for cand in spec.get("sheets", []):
            if match_sheet(cand["name"].replace("{m}", mon) if mon else cand["name"], ws.title):
                sp = cand
                break
        if sp is None or sp.get("role") == "ignore":
            skipped.append(ws.title)
            continue
        if sp["role"] == "detail":
            got = parse_detail_sheet(sp, ws, period, carrier, boxp, mg.get(ws.title))
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
    # 跨 sheet 去重(spec 表配 dedupe_col)：跨越「深圳星期零」页是全量、「孝感星期九」页又抄了孝感那单，按运单号只留第一次(V2.762)
    seen, kept = set(), []
    for r in detail:
        k = r.pop("_dk", None)
        if k and k in seen:
            continue
        if k:
            seen.add(k)
        kept.append(r)
    detail = kept
    owner = bill_owner(wb, spec)
    if spec.get("src_from_sheets") and not owner:
        # 一家一月分几个文件、各管一类(天鹰：蜜雪装货/小料卸货/分步调拨…)：份名=取到数的表名(去掉「(2)」这类副本号)，各份互不覆盖(V2.765)
        names = list(dict.fromkeys(re.sub(r"[\s\(（]+\d*[\)）]*\s*$", "", r.get("src_sheet") or "").strip() for r in detail + accrual))
        owner = "+".join(n for n in names if n)
    # 货主→产品线(spec.owner_bizline，如 kikiherb→Kiki Herb)：同一套标注下的另一个货主，产品线跟货主走
    biz = next((v for k, v in (spec.get("owner_bizline") or {}).items() if k.lower() in owner.lower()), "")
    for r in detail + accrual:
        r["bill_src"] = owner
        if biz and not r.get("bizline"):
            r["bizline"] = biz
    return {"detail": detail, "accrual": accrual, "skipped": skipped, "carrier": carrier, "period": period,
            "bill_src": owner}
