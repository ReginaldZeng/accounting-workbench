# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-07-06 | Author: Claude / c | Version: V2.29
# Description: ①税率改为维表优先（供应商×费用类型，不含税口径；查不到退回计提表格内税率，都没有=缺税率拦录入）；
#   ②产品项目 TO C（CPXM017）按 1-6 月序时账实证规律挂：业务线=鲜食 或 费用归属带「山姆」的费用行（比说明书v0.2只写山姆系更宽，账上鲜食61行全挂）；
#   ③新增写金蝶编码表（账簿/部门/费用项目 FNumber，孝感账簿实证=107 非记忆中104）与 build_kd_model()——凭证→GL_VOUCHER Save 报文。
# Date: 2026-07-05 | Author: Claude / c | Version: V2.20
# Description: 物流计提内核（门户「月结与结账」通用技能）。
#   把物流计提表（老格式宽交叉表）解析成扁平记录 → 查映射表 → 生成计提凭证分录 → 自校验。
#   规则均由 2026-01~06 K3Cloud 序时账（GL_VOUCHER）实证反推，见
#   03_Source_Materials/需求确认书草稿归档/物流计提工具_说明书与一期方案_v0.1_20260705.md。
#   分录模板：借 费用科目[部门/费用项目/业务线] + 借 2221.01.07 暂估进项税[供应商]
#             贷 2241.02 其他应付款-供应商往来[供应商]（含税，挂供应商）。
#   税额一律走暂估进项税；一格一张凭证（主体×供应商×费用归属）；只生成正向计提，红冲/更正/核销不管。
import io
import math
import calendar

SUBJECTS = ["深圳星期零", "孝感星期九", "深圳星期九"]
SUBFIELDS = {"税率": "rate", "出库未税": "net", "未税": "net", "税额": "tax", "凭证号": "vno", "业务线": "biz"}
SKIP_HDR = {"费用归属", "名称", "承运商", "合计", "仓库名称", ""}

# 分录常量
ACC_TAX = "2221.01.07 应交税费—应交增值税—暂估进项税"
ACC_PAYABLE = "2241.02 其他应付款—供应商往来"

# 映射表 (主体, 费用归属列名) -> (借方科目, 部门, 费用项目, 业务线)  [1-6月序时账实证]
# 业务线 '?' = 计提表定不死、待物流部在"业务线"列填，可留空（B 且允许留空）
MAP = {
    ("深圳星期零", "植物肉"): ("6601 销售费用", "永续物流中心", "出库运费", "植物肉"),
    ("深圳星期零", "鲜食"): ("6601 销售费用", "永续物流中心", "出库运费", "鲜食"),
    ("深圳星期零", "鲜食仓储"): ("6601 销售费用", "永续物流中心", "货物仓储费", "鲜食"),
    ("深圳星期零", "鲜食山姆仓储"): ("6601 销售费用", "永续物流中心", "货物仓储费", "鲜食"),
    ("深圳星期零", "山姆零售"): ("6601 销售费用", "永续物流中心", "出库运费", "山姆零售"),
    ("深圳星期零", "豆蛋制品"): ("6601 销售费用", "永续物流中心", "出库运费", "豆蛋制品"),
    ("深圳星期零", "零售"): ("6601 销售费用", "永续物流中心", "出库运费", "零售"),
    ("深圳星期零", "小料"): ("6601 销售费用", "永续物流中心", "出库运费", "小料"),
    ("深圳星期零", "电商"): ("6601 销售费用", "永续物流中心", "出库运费", "电商"),
    ("深圳星期零", "研发费用"): ("6604 研发费用", "永续研发中心", "研发外购", ""),
    ("深圳星期零", "设备"): ("6604 研发费用", "永续研发中心", "搬运费", ""),
    ("深圳星期零", "设备转移"): ("6604 研发费用", "永续研发中心", "搬运费", ""),
    ("孝感星期九", "代工厂入库"): ("6401 主营业务成本", "仓储物流部", "入库运费", "?"),
    ("孝感星期九", "鲜食入库"): ("6401 主营业务成本", "仓储物流部", "入库运费", "?"),
    ("孝感星期九", "山姆鲜食入库"): ("6401 主营业务成本", "仓储物流部", "入库运费", "?"),
    ("孝感星期九", "零售入库"): ("6401 主营业务成本", "仓储物流部", "入库运费", "?"),
    ("孝感星期九", "零售山姆入库"): ("6401 主营业务成本", "仓储物流部", "入库运费", "?"),
    ("孝感星期九", "孝感工厂入库"): ("5101 制造费用", "仓储物流部", "入库运费", ""),
    ("孝感星期九", "植物肉入库"): ("5101 制造费用", "仓储物流部", "入库运费", ""),
    ("孝感星期九", "小料入库"): ("5101 制造费用", "茶饮小料部", "入库运费", "小料"),
    ("孝感星期九", "仓储"): ("6601 销售费用", "仓储物流部", "货物仓储费", "?"),
    ("孝感星期九", "研发中试"): ("6604 研发费用", "永续研发中心", "研发外购", ""),
    ("孝感星期九", "设备转移"): ("5101 制造费用", "仓储物流部", "搬运费", ""),
    ("深圳星期九", "小料出库"): ("6601 销售费用", "永续供应中心", "出库运费", "小料"),
    ("深圳星期九", "零售出库"): ("6601 销售费用", "永续供应中心", "出库运费", "零售"),   # 推断待确认
    ("深圳星期九", "仓储"): ("6601 销售费用", "永续供应中心", "货物仓储费", "?"),         # 推断待确认
    # 电商仓（线上）
    ("孝感星期九", "仓储费用"): ("6601 销售费用", "永续物流中心", "货物仓储费", "?"),
    ("深圳星期零", "线上零售"): ("6601 销售费用", "永续物流中心", "出库运费", "零售"),
}

# 业务线 -> 产品分类编码（写金蝶用；查询取 FF100010.FNumber）。
# V2.195 起单一事实源=维表 logistics_bizline（账单直采行自带「产品分类编码」字段直通），
# 本表退居兜底：旧宽表解析/回翻仍走这里。kikiherb=CPFL013 系 2026-06 序时账实证。
BIZLINE_CODE = {
    "植物肉": "CPFL007", "鲜食": "CPFL010", "山姆零售": "CPFL011",
    "零售": "CPFL011", "豆蛋制品": "CPFL008", "小料": "CPFL009", "电商": "CPFL002",
    "kikiherb": "CPFL013",
}

# ---------------- 写金蝶编码表（2026-07-06 由 1-6 月序时账 889 行实证批量反查，非手填） ----------------
BOOK_CODE = {"深圳星期零": "101", "深圳星期九": "105", "孝感星期九": "107"}   # 账簿（孝感实证=107）
DEPT_CODE = {                       # 部门 FDetailID.FFLEX5（三账簿编码一致，已核）
    "永续物流中心": "0011401", "仓储物流部": "0030301", "茶饮小料部": "0030902",
    "永续供应中心": "0010401", "永续研发中心": "0010506",
}
ITEM_CODE = {                       # 费用项目 FDetailID.FFLEX9（编码带中文为金蝶原样，不可截断）
    "出库运费": "FYXM008.002其他物流费用002", "入库运费": "FYXM002.002运费成本002",
    "货物仓储费": "FYXM008.002其他物流费用001", "研发外购": "FYXM005.002研发费用001",
    "搬运费": "FYXM005.003其他业务活动005",
}
TOC_CODE = "CPXM017"                # 产品项目 TO C（FDetailID.FF100006）
VOUCHER_GROUP = "PRE001"            # 凭证字「记」
CURRENCY = "PRE001"                 # 币别 人民币
RATE_TYPE = "HLTX01_SYS"            # 汇率类型 固定汇率（必填）
ACC_TAX_CODE = "2221.01.07"
ACC_PAYABLE_CODE = "2241.02"


def _toc_flag(item_name, bizline):
    """产品项目 TO C 挂载规律（1-6月账上实证）：业务线=鲜食 或 费用归属带「山姆」的费用行挂 CPXM017，其余不挂。"""
    return bizline == "鲜食" or "山姆" in (item_name or "")


def _num(v):
    if v is None or v == "":
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return None


def _s(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return str(v).strip()


def parse_accrual_df(df):
    """把物流计提表 DataFrame(无表头) 解析成扁平记录。返回 list[dict]，含月结(线下/线上)与非月结。"""
    rows, cols = df.shape

    def cell(r, c):
        if r < 0 or r >= rows or c < 0 or c >= cols:
            return None
        return df.iat[r, c]

    def sc(r, c):
        return _s(cell(r, c))

    # 定位分区
    sec = {}
    for r in range(rows):
        t = sc(r, 0)
        if t in ("线下费用计提", "电商仓", "非月结费用"):
            sec[t] = r
    order = sorted(sec.items(), key=lambda x: x[1])

    def subject_row(start):
        for r in range(start, min(start + 4, rows)):
            if [c for c in range(cols) if sc(r, c) in SUBJECTS]:
                return r
        return start + 1

    def build_blocks(sr, hr, line):
        scols = sorted((c, sc(sr, c)) for c in range(cols) if sc(sr, c) in SUBJECTS)

        def subj(col):
            cur = None
            for c, n in scols:
                if c <= col:
                    cur = n
            return cur

        blocks, cur = [], None
        for c in range(cols):
            t = sc(hr, c)
            if not t:
                continue
            if t in SUBFIELDS:
                if cur is not None and SUBFIELDS[t] not in cur:
                    cur[SUBFIELDS[t]] = c
            elif t in SKIP_HDR or t in SUBJECTS:
                cur = None
            else:
                cur = {"name": t, "gross": c, "subject": subj(c), "line": line}
                blocks.append(cur)
        return blocks

    records = []

    def parse_matrix(st, nxt, line):
        sr = subject_row(st + 1)
        hr = sr + 1
        blocks = build_blocks(sr, hr, line)
        for r in range(hr + 1, nxt):
            nm = sc(r, 0)
            if nm == "合计":
                break
            if nm == "":
                continue
            full = sc(r, 1)
            for b in blocks:
                g = _num(cell(r, b["gross"]))
                if g is None or abs(g) < 1e-9:
                    continue
                biz_in = sc(r, b["biz"]) if "biz" in b else ""
                records.append({
                    "结类": "月结", "区": "线上" if line == "线上" else "线下",
                    "主体": b["subject"], "物流商": nm, "公司全名": full,
                    "费用归属": b["name"], "含税": round(g, 2),
                    "税率": _num(cell(r, b["rate"])) if "rate" in b else None,
                    "源未税": _num(cell(r, b["net"])) if "net" in b else None,
                    "源税额": _num(cell(r, b["tax"])) if "tax" in b else None,
                    "业务线填报": biz_in,
                    "凭证号": sc(r, b["vno"]) if "vno" in b else "",
                })

    for i, (nm, st) in enumerate(order):
        nxt = order[i + 1][1] if i + 1 < len(order) else rows
        if nm == "线下费用计提":
            parse_matrix(st, nxt, "线下")
        elif nm == "电商仓":
            parse_matrix(st, nxt, "线上")
        elif nm == "非月结费用":
            for r in range(st + 2, nxt):
                if sc(r, 0) in ("", "合计"):
                    continue
                amt = _num(cell(r, 2))
                records.append({
                    "结类": "非月结", "区": "", "主体": sc(r, 22), "物流商": sc(r, 0),
                    "公司全名": sc(r, 1), "费用归属": sc(r, 7),
                    "含税": round(amt, 2) if amt else None, "税率": None,
                    "源未税": None, "源税额": None, "业务线填报": "", "凭证号": "",
                })
    return records


def _suffix(item):
    return "费用" if "仓储" in (item or "") else "运费"


def resolve_rate(rates, supplier, fee_type, sheet_rate):
    """税率取数口径：①维表(供应商×费用类型) ②维表(供应商默认，费用类型空串) ③计提表格内税率 ④缺。
    返回 (税率或None, 来源说明)。"""
    if rates:
        r = rates.get((supplier, fee_type))
        if r is not None:
            return float(r), "税率表"
        r = rates.get((supplier, ""))
        if r is not None:
            return float(r), "税率表(默认)"
    if sheet_rate is not None:
        return float(sheet_rate), "计提表"
    return None, "缺税率"


def build_vouchers(records, month, rates=None):
    """月结记录 -> 计提凭证 + 自校验。rates=税率维表 {(供应商,费用类型):税率}，None=只用表内税率。返回 dict。"""
    vouchers = []
    unmapped = []
    no_rate = []          # 缺税率的格子（不给录入）
    rate_diff = []        # 维表与计提表税率不一致的格子（提醒人工确认）
    bal_ok = net_ok = 0
    bad = []
    for rec in records:
        if rec["结类"] != "月结":
            continue
        key = (rec["主体"], rec["费用归属"])
        if key not in MAP:
            if key not in [(u["主体"], u["费用归属"]) for u in unmapped]:
                unmapped.append({"主体": rec["主体"], "费用归属": rec["费用归属"]})
            continue
        acc, dept, item, biz = MAP[key]
        # 业务线 B 且允许留空：优先物流部在计提表"业务线"列填报的值；否则用映射；'?' -> 空(待定)
        bizline = rec.get("业务线填报") or (biz if biz != "?" else "")
        rate, rate_src = resolve_rate(rates, rec["公司全名"], rec["费用归属"], rec["税率"])
        if rate is None:
            no_rate.append({"供应商": rec["公司全名"], "费用类型": rec["费用归属"], "主体": rec["主体"]})
            rate = 0.0
        elif rate_src.startswith("税率表") and rec["税率"] is not None and abs(rate - rec["税率"]) > 1e-9:
            rate_diff.append({"供应商": rec["公司全名"], "费用类型": rec["费用归属"],
                              "税率表": rate, "计提表": rec["税率"]})
        gross = rec["含税"]
        net = round(gross / (1 + rate), 2)
        tax = round(gross - net, 2)
        if abs(net + tax - gross) > 0.01:
            bad.append(rec)
        else:
            bal_ok += 1
        if rec["源未税"] is not None and abs(net - rec["源未税"]) <= 0.02:
            net_ok += 1
        zhaiyao = "计提%s%s月%s%s%s" % (rec["公司全名"], month, rec["区"], rec["费用归属"], _suffix(item))
        # 产品项目 TO C：鲜食/山姆系费用行挂 CPXM017（账上实证规律）
        toc = TOC_CODE if _toc_flag(rec["费用归属"], bizline) else ""
        biz_dim = ("/" + bizline) if bizline else ""
        toc_dim = "/TO C" if toc else ""
        vouchers.append({
            "主体": rec["主体"], "物流商": rec["物流商"], "公司全名": rec["公司全名"],
            "费用归属": rec["费用归属"], "业务线": bizline, "业务线编码": BIZLINE_CODE.get(bizline, ""),
            "产品项目": toc,
            "含税": gross, "税率": rate, "税率来源": rate_src, "未税": net, "税额": tax,
            "科目": acc, "部门": dept, "费用项目": item, "供应商": rec["公司全名"],
            "摘要": zhaiyao, "凭证号": rec["凭证号"],
            "可录入": rate_src != "缺税率",
            "分录": [
                {"方向": "借", "科目": acc, "维度": dept + "/" + item + biz_dim + toc_dim, "借方": net, "贷方": 0},
                {"方向": "借", "科目": ACC_TAX, "维度": rec["公司全名"], "借方": tax, "贷方": 0},
                {"方向": "贷", "科目": ACC_PAYABLE, "维度": rec["公司全名"], "借方": 0, "贷方": gross},
            ],
        })
    return {
        "vouchers": vouchers,
        "unmapped": unmapped,
        "no_rate": no_rate,
        "rate_diff": rate_diff,
        "summary": {
            "月结记录": sum(1 for r in records if r["结类"] == "月结"),
            "非月结记录": sum(1 for r in records if r["结类"] == "非月结"),
            "生成凭证": len(vouchers),
            "借贷平衡通过": bal_ok,
            "未税核对一致": net_ok,
            "异常": len(bad),
            "未覆盖映射": len(unmapped),
            "缺税率": len(no_rate),
            "含税合计": round(sum(v["含税"] for v in vouchers), 2),
            "未税合计": round(sum(v["未税"] for v in vouchers), 2),
            "税额合计": round(sum(v["税额"] for v in vouchers), 2),
        },
    }


def process_workbook(file_bytes, sheet, month, rates=None):
    """完整流水：上传的 xlsx bytes -> 解析 -> 生成凭证 + 校验。供 app 路由调用。"""
    import pandas as pd
    df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet, header=None)
    records = parse_accrual_df(df)
    result = build_vouchers(records, month, rates=rates)
    result["records"] = records
    result["non_monthly"] = [r for r in records if r["结类"] == "非月结"]
    return result


# ---------------- 写金蝶：凭证 -> GL_VOUCHER Save 报文 ----------------
def month_end(year, month):
    return "%04d-%02d-%02d" % (year, month, calendar.monthrange(year, month)[1])


def check_voucher_for_post(v, supplier_codes):
    """录入前逐张校验（服务端护栏，不信任前端回传）。返回问题清单，空=可录。
    supplier_codes: {供应商全名: (编码, 分组编码)}，来自金蝶 BD_Supplier。"""
    problems = []
    if v.get("税率来源") == "缺税率" or not v.get("可录入", True):
        problems.append("缺税率（请先在税率维护里补该供应商×费用类型的税率）")
    net, tax, gross = v.get("未税", 0), v.get("税额", 0), v.get("含税", 0)
    if abs(round(net + tax - gross, 2)) > 0.01:
        problems.append("未税+税额≠含税，金额勾稽不平")
    if v.get("主体") not in BOOK_CODE:
        problems.append("主体「%s」没有对应的金蝶账簿" % v.get("主体"))
    if v.get("部门") not in DEPT_CODE:
        problems.append("部门「%s」缺编码" % v.get("部门"))
    if v.get("费用项目") not in ITEM_CODE:
        problems.append("费用项目「%s」缺编码" % v.get("费用项目"))
    biz = v.get("业务线")
    if biz and biz not in ("—",) and not v.get("产品分类编码") and biz not in BIZLINE_CODE:
        problems.append("业务线「%s」缺产品分类编码" % biz)   # 行自带编码(账单直采/活表)则免查兜底表
    if v.get("公司全名") not in (supplier_codes or {}):
        problems.append("供应商「%s」金蝶未建档或未启用" % v.get("公司全名"))
    return problems


def build_kd_model(v, year, period, supplier_codes):
    """一张计提凭证 -> 金蝶 GL_VOUCHER Save 的 Model（2026-07-06 控制测试验证的配方）。
    核算维度=分录内 FDetailID 对象、内层键用完整 FDETAILID__FFLEXn。"""
    sup_code, sup_grp = supplier_codes[v["公司全名"]]
    acc_code = str(v["科目"]).split(" ")[0]   # "6601 销售费用" -> "6601"
    base = {"FCURRENCYID": {"FNumber": CURRENCY},
            "FEXCHANGERATETYPE": {"FNumber": RATE_TYPE}, "FEXCHANGERATE": 1.0,
            "FEXPLANATION": v["摘要"]}
    # 借·费用行：部门+费用项目(+产品分类)(+产品项目 TO C)，不挂供应商
    exp_dims = {"FDETAILID__FFLEX5": {"FNumber": DEPT_CODE[v["部门"]]},
                "FDETAILID__FFLEX9": {"FNumber": ITEM_CODE[v["费用项目"]]}}
    cpfl = v.get("产品分类编码") or (BIZLINE_CODE.get(v["业务线"]) if v.get("业务线") not in ("", "—", None) else "")
    if cpfl:
        exp_dims["FDETAILID__FF100010"] = {"FNumber": cpfl}   # 行自带编码优先(账单直采/活表),兜底查 BIZLINE_CODE
    if v.get("产品项目"):
        exp_dims["FDETAILID__FF100006"] = {"FNumber": v["产品项目"]}
    entries = [
        dict(base, FACCOUNTID={"FNumber": acc_code}, FDEBIT=v["未税"], FCREDIT=0, FDetailID=exp_dims),
    ]
    # 借·暂估进项税行：挂供应商（税额为 0 时省略该行，账上无 0 税行）
    if round(v["税额"], 2) != 0:
        entries.append(dict(base, FACCOUNTID={"FNumber": ACC_TAX_CODE}, FDEBIT=v["税额"], FCREDIT=0,
                            FDetailID={"FDETAILID__FFLEX4": {"FNumber": sup_code}}))
    # 贷·其他应付款行：挂供应商+供应商分组
    pay_dims = {"FDETAILID__FFLEX4": {"FNumber": sup_code}}
    if sup_grp:
        pay_dims["FDETAILID__FF100005"] = {"FNumber": sup_grp}
    entries.append(dict(base, FACCOUNTID={"FNumber": ACC_PAYABLE_CODE}, FDEBIT=0, FCREDIT=v["含税"],
                        FDetailID=pay_dims))
    # ⚠⚠ 两个坑（2026-07-07 实证，缺一即录错月份）：
    #  ① 字段名必须是 FDate（不是 FDATE 全大写）——大写金蝶不认；
    #  ② 金蝶 Save 对 JSON 字段【顺序】敏感：FDate 必须排在 FACCOUNTBOOKID/FEntity 之后！
    #     若 FDate 放在最前，金蝶会忽略它、把凭证钉到"当前会计期间"（如当前是6月就全录6月），
    #     放在账簿/分录后面才生效；会计期间由 FDate 自动推导。
    return {
        "FACCOUNTBOOKID": {"FNumber": BOOK_CODE[v["主体"]]},
        "FVOUCHERGROUPID": {"FNumber": VOUCHER_GROUP},
        "FEntity": entries,
        "FDate": month_end(year, period), "FYear": year, "FPeriod": period,
    }


def list_suppliers_in_table(records):
    """计提表里【月结】出现的供应商全名（去重，非空），供与金蝶 BD_Supplier 比对。
    非月结（货拉拉/国际货运，公司全名列实为经办人）不做账、不纳入供应商核对。"""
    seen = []
    for r in records:
        if r.get("结类") != "月结":
            continue
        full = (r.get("公司全名") or "").strip()
        if full and full not in seen:
            seen.append(full)
    return seen
