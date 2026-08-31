# -*- coding: utf-8 -*-
# 理财对账内核单测（合成数据，不依赖 OCR/金蝶环境）。运行：python -m kernels.test_wealth_recon
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kernels import wealth_recon as wr

_n = 0
def ok(cond, msg):
    global _n; _n += 1
    assert cond, "FAIL: " + msg
    print("  ok:", msg)


def _vou(code, dim, deb=0, cre=0, exp="", vno="记1"):
    return {"科目编码": code, "FDetailID.FF100002.FNumber": dim, "FDEBIT": deb, "FCREDIT": cre,
            "FEXPLANATION": exp, "FVOUCHERGROUPID.FName": "记", "FVOUCHERGROUPNO": vno.replace("记", "")}


def _stmt(name, code, txns, entity="孝感九", issuer="招商银行"):
    return {"产品名称": name, "产品代码": code, "主体": entity, "出单机构": issuer,
            "账号": "7129***0308", "期初市值": None, "期末市值": None, "交易明细": txns}


def t_match_dim():
    dims = ["PY200001浦银理财天添鑫中短债93号", "ZGN2560081宁银理财宁欣日日薪固定收益类日开理财81", "基金账号791377"]
    ok(wr.match_dim("浦银理财天添鑫中短债93号（招享）", "", dims) == dims[0], "品牌+号数匹配浦银")
    ok(wr.match_dim("宁银理财宁欣日日薪81号", "ZGN2560081E", dims) == dims[1], "产品代码前缀匹配宁银")
    ok(wr.match_dim("完全不相干的产品", "", dims) == "", "无匹配返回空")


def t_bank_interest_excluded():
    """6603 财务费用带银行账号维度(普通利息)不得被当成理财产品。"""
    vou = [_vou("1101.01", "PY200001浦银理财天添鑫中短债93号", cre=35000000, exp="理财赎回")]
    inc = [_vou("6603.02", "花旗银行1037204028（USD）", deb=-1200, exp="美元账户存款利息"),  # 银行利息，非理财
           _vou("6603.02", "招商银行755953100010001", deb=-500, exp="活期利息")]
    agg = wr.kd_wealth_by_product(vou, inc)
    ok(set(agg.keys()) == {"PY200001浦银理财天添鑫中短债93号"}, "银行账号利息不进理财维度")
    ok(agg["PY200001浦银理财天添鑫中短债93号"]["投资收益"] == 0.0, "无理财收益误并")


def t_income_by_summary():
    """收益腿靠摘要产品名归到理财产品(即便凭证维度是银行账号)。"""
    vou = [_vou("1012", "PY200001浦银理财天添鑫中短债93号", cre=35000000, exp="理财赎回-浦银理财-天添鑫93号")]
    inc = [_vou("6603.02", "招商银行712900412410308", deb=-160943.15, exp="理财赎回-浦银理财-天添鑫中短债93号")]
    agg = wr.kd_wealth_by_product(vou, inc)
    ok(abs(agg["PY200001浦银理财天添鑫中短债93号"]["投资收益"] - 160943.15) < 0.01, "利息收入按摘要归到浦银")


def t_reconcile_states():
    dims_vou = [
        _vou("1012", "PY200001浦银理财天添鑫中短债93号", cre=35000000, exp="理财赎回-浦银"),
        _vou("1101.01", "ZGN2560081宁银理财宁欣日日薪", cre=95000000, exp="理财赎回-宁银"),
        _vou("1101.02", "基金账号791377", cre=53310.12, exp="基金赎回"),
        _vou("1101.01", "基金账号791377", cre=1084265.42, exp="基金赎回"),
    ]
    inc = [_vou("6603.02", "招商712900412410308", deb=-160943.15, exp="理财赎回-浦银理财-天添鑫中短债93号")]
    agg = wr.kd_wealth_by_product(dims_vou, inc)
    stmt = [
        _stmt("浦银理财天添鑫中短债93号（招享）", "", [{"类型": "理财赎回", "确认金额": 35160943.15, "日期": "2026-06-23"}]),
        _stmt("宁银理财宁欣日日薪81号", "ZGN2560081E", [{"类型": "理财赎回", "确认金额": 94165131.94, "日期": "2026-06-23"}]),
        _stmt("某未做账理财", "XX999999", [{"类型": "理财赎回", "确认金额": 5000000, "日期": "2026-06-10"}]),
    ]
    res = wr.reconcile_wealth(stmt, agg)
    by = {r["产品名称"][:4]: r for r in res["rows"]}
    ok(by["浦银理财"]["状态"] == "已勾稽", "浦银 本金+收益 对平→已勾稽")
    ok(abs(by["浦银理财"]["差额_含收益"]) < 0.01, "浦银差额含收益≈0")
    ok(by["宁银理财"]["状态"] == "有差异", "宁银 差834868→有差异")
    ok(by["某未做账"]["状态"].startswith("金蝶未记"), "对账单有、金蝶无腿→金蝶未记")
    fund = next(r for r in res["rows"] if "791377" in r["金蝶维度"])
    ok(fund["状态"].startswith("对账单缺"), "金蝶有腿、对账单缺→对账单缺")
    ok(res["已勾稽"] == 1 and res["有差异"] == 1, "汇总计数对")


for fn in (t_match_dim, t_bank_interest_excluded, t_income_by_summary, t_reconcile_states):
    print(fn.__name__)
    fn()
print(f"\n全部通过 · {_n} 断言")
