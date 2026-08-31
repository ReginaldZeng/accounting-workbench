# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-07-03 | Author: Claude / c | Version: V1.1
# Description: 对账引擎 v2 单元测试。覆盖七态、4 位小数比对、晚记当天分档、
#              账号对不上台账、组合候选(1:N)、护栏自校验。纯确定性、无外部依赖。
import reconcile as R

LEDGER = [
    {"账号": "1001", "类别": "银行账户", "主体": "甲公司", "开户行": "农业银行"},
    {"账号": "2002", "类别": "银行账户", "主体": "乙公司", "开户行": "招商银行"},
]
IDX = R.ledger_index(LEDGER)

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  [OK] {name}")
    else:
        _fail += 1
        print(f"  [XX] {name}")


def brow(acct, d, inn=0, out=0, cp="", memo=""):
    return {"账号": acct, "户名": "户", "交易日期": d, "收入": inn, "支出": out,
            "对方户名": cp, "摘要": memo}


def krow(acct, d, deb=0, cred=0, memo="", vno=""):
    return {"账号": acct, "FDATE": d, "FDEBIT": deb, "FCREDIT": cred,
            "FEXPLANATION": memo, "FVOUCHERGROUPID.FName": "记", "FVOUCHERGROUPNO": vno}


def run(banks, kds, **kw):
    b = R.bank_to_recs(banks, IDX)
    k = R.kd_to_recs(kds, IDX)
    return R.reconcile(b, k, **kw)


def find(results, **kw):
    for r in results:
        if all(r.get(k) == v for k, v in kw.items()):
            return r
    return None


# ---------------- 用例 ----------------
def t_matched_same_day():
    res, s = run([brow("1001", "2026-06-10", out=100.0, cp="供应商A", memo="货款")],
                 [krow("1001", "2026-06-10", cred=100.0, memo="货款")])
    r = res[0]
    check("已匹配·当天准时", r["状态"] == "已匹配" and r["日期差天"] == 0)
    check("已匹配·置信度高(同日+摘要重合)", r["置信度"] == "高")


def t_late_month():
    res, s = run([brow("1001", "2026-06-10", inn=200.0, memo="收款")],
                 [krow("1001", "2026-06-13", deb=200.0, memo="收款")])
    check("晚记·本月(晚3天未跨月)", res[0]["状态"] == "晚记·本月" and res[0]["日期差天"] == 3)


def t_late_cross():
    res, s = run([brow("1001", "2026-06-28", inn=300.0, memo="货款")],
                 [krow("1001", "2026-07-02", deb=300.0, memo="货款")])
    check("跨期晚记(6月发生7月入账)", res[0]["状态"] == "跨期晚记" and res[0]["日期差天"] == 4)


def t_amount_wrong():
    res, s = run([brow("1001", "2026-06-12", out=48156.23, cp="供应商A", memo="采购")],
                 [krow("1001", "2026-06-12", cred=48156.13, memo="采购")])
    r = res[0]
    check("做错·金额(差0.10)", r["状态"] == "做错·金额" and round(r["差额"], 2) == 0.10)


def t_four_decimal_no_false_alarm():
    # 银行 .23 == 金蝶 .2300 → 已匹配，绝不误报做错
    res, s = run([brow("1001", "2026-06-05", inn=48156.23, memo="利息")],
                 [krow("1001", "2026-06-05", deb=48156.2300, memo="利息")])
    check("4位小数正常值不误报(.23==.2300→已匹配)", res[0]["状态"] == "已匹配")


def t_four_decimal_real_diff():
    # 银行 .7500 vs 金蝶 .7478 → 真差 0.0022，落做错·金额并显 4 位差
    res, s = run([brow("1001", "2026-06-20", inn=182780.75, memo="利息收入")],
                 [krow("1001", "2026-06-20", deb=182780.7478, memo="利息收入")])
    r = res[0]
    check("4位小数真差捕获(.7500 vs .7478→做错·金额 差0.0022)",
          r["状态"] == "做错·金额" and abs(r["差额"] - 0.0022) < 1e-9)


def t_bank_leak():
    # 账户两侧都有数据(一对已匹配)，另有一笔银行行金蝶查无 → 疑似漏账（而非整户缺数据）
    res, s = run([brow("1001", "2026-06-08", out=100.0, memo="货款"),
                  brow("1001", "2026-06-09", out=86400.0, cp="某物流", memo="付款")],
                 [krow("1001", "2026-06-08", cred=100.0, memo="货款")])
    leak = find(res, 状态="疑似漏账")
    check("疑似漏账(共同账户内·银行有金蝶无)", leak is not None and leak["贷方金额"] == 86400.0)


def t_kd_only():
    # 账户两侧都有数据，另有一笔金蝶行银行查无 → 金蝶单边·疑似做错（而非整户缺数据）
    res, s = run([brow("1001", "2026-06-15", out=50.0, memo="费用")],
                 [krow("1001", "2026-06-15", cred=50.0, memo="费用"),
                  krow("1001", "2026-06-16", cred=25000.0, memo="预提费用")])
    only = find(res, 状态="金蝶单边·疑似做错")
    check("金蝶单边·疑似做错(共同账户内·金蝶有银行无)", only is not None and only["金蝶金额"] == 25000.0)


def t_account_no_kd():
    # 账户只在银行侧(金蝶该账户查无) → 账户缺金蝶数据，绝不叫漏账
    res, s = run([brow("1001", "2026-06-10", out=100.0, memo="货款")],
                 [krow("2002", "2026-06-10", cred=100.0, memo="货款")])
    r = find(res, 账号="1001")
    check("账户缺金蝶数据(银行独有账户不叫漏账)", r is not None and r["状态"] == "账户缺金蝶数据")
    check("覆盖预检·汇总计数(缺金蝶数据=1)", s["账户缺金蝶数据"] == 1 and s["疑似漏账"] == 0)


def t_account_no_bank():
    # 账户只在金蝶侧(银行没导对账单，如花旗美元户) → 账户缺银行流水，绝不叫单边
    res, s = run([brow("1001", "2026-06-10", out=100.0, memo="货款"),
                  brow("1001", "2026-06-11", out=200.0, memo="货款2")],
                 [krow("1001", "2026-06-10", cred=100.0, memo="货款"),
                  krow("1001", "2026-06-11", cred=200.0, memo="货款2"),
                  krow("2002", "2026-06-12", cred=888.0, memo="美元户服务费")])
    r = find(res, 账号="2002")
    check("账户缺银行流水(金蝶独有账户·如花旗美元户)", r is not None and r["状态"] == "账户缺银行流水")
    check("覆盖预检·汇总计数(缺银行流水=1)", s["账户缺银行流水"] == 1 and s["金蝶单边·疑似做错"] == 0)


def t_internal_unbooked():
    # 1001 一对已匹配 + 一笔"社保"银行支金蝶查无；2002 金蝶有同额"资金调拨"入账（另户）
    # → 1001 那笔社保判「内部往来·未做账」，附对方凭证；不叫外部漏账
    res, s = run([brow("1001", "2026-06-08", out=100.0, memo="货款"),
                  brow("1001", "2026-06-22", out=255182.46, memo="星期零社保"),
                  brow("2002", "2026-06-22", out=50.0, memo="费用")],
                 [krow("1001", "2026-06-08", cred=100.0, memo="货款"),
                  krow("2002", "2026-06-22", cred=50.0, memo="费用"),
                  krow("2002", "2026-06-20", cred=255182.46, memo="资金调拨转出深圳", vno="265")])
    r = find(res, 状态="内部往来·未做账")
    check("内部往来·未做账(跨账号同额+划转特征)",
          r is not None and r["贷方金额"] == 255182.46 and "265" in str(r["内部往来对应"]))
    check("内部往来不误判为外部漏账", find(res, 状态="疑似漏账") is None)


def t_kd_xfer_crossacct():
    # A) 金蝶把内部划转记在 1001，钱实际从 2002 走（银行 2002 有同额同向未匹配腿）
    #    → 内部划转·对应他账户，附实走银行账户；不再误报金蝶单边
    res, s = run([brow("1001", "2026-06-08", out=100.0, memo="货款"),
                  brow("2002", "2026-06-10", out=100.0, memo="货款2"),
                  brow("2002", "2026-06-23", out=700000.0, memo="往来款")],
                 [krow("1001", "2026-06-08", cred=100.0, memo="货款"),
                  krow("2002", "2026-06-10", cred=100.0, memo="货款2"),
                  krow("1001", "2026-06-30", cred=700000.0, memo="资金调拨转出深圳", vno="301")])
    r = find(res, 状态="内部划转·对应他账户")
    check("内部划转·对应他账户(金蝶挂本户·银行走他账户)",
          r is not None and r["贷方金额"] == 700000.0 and "2002" in str(r["内部往来对应"]))
    check("内部划转不再误报金蝶单边", find(res, 状态="金蝶单边·疑似做错") is None)
    check("汇总计入内部划转·对应他账户=1", s["内部划转·对应他账户"] == 1)

    # B) 划转两腿都进了金蝶、双方都缺银行流水（1001 支 ↔ 2002 收 同额反向）→ 双方都标内部划转
    res2, s2 = run([brow("1001", "2026-06-08", out=100.0, memo="货款"),
                    brow("2002", "2026-06-10", out=200.0, memo="货款2")],
                   [krow("1001", "2026-06-08", cred=100.0, memo="货款"),
                    krow("2002", "2026-06-10", cred=200.0, memo="货款2"),
                    krow("1001", "2026-06-24", cred=500000.0, memo="资金归集转出"),
                    krow("2002", "2026-06-24", deb=500000.0, memo="资金归集转入")])
    check("金蝶对开两腿均标内部划转(缺银行流水)",
          s2["内部划转·对应他账户"] == 2 and s2["金蝶单边·疑似做错"] == 0)

    # C) 负例：非划转特征的金蝶单边(如结息)不被扫进内部划转，仍留金蝶单边·疑似做错
    res3, s3 = run([brow("1001", "2026-06-15", out=50.0, memo="费用"),
                    brow("2002", "2026-06-16", out=21737.46, memo="其它")],
                   [krow("1001", "2026-06-15", cred=50.0, memo="费用"),
                    krow("2002", "2026-06-16", cred=21737.46, memo="费用2"),
                    krow("1001", "2026-06-21", deb=21737.46, memo="结息-孝感招行")])
    only = find(res3, 状态="金蝶单边·疑似做错")
    check("结息类金蝶单边(无划转特征)不误并入内部划转",
          only is not None and only["金蝶金额"] == 21737.46 and s3["内部划转·对应他账户"] == 0)


def t_unmapped_not_leak():
    # 账号 9999 不在台账 → 账号对不上台账，绝不叫漏账
    res, s = run([brow("9999", "2026-06-22", inn=12600.0, memo="掩码账号")], [])
    r = res[0]
    check("账号对不上台账(不叫漏账)", r["状态"] == "账号对不上台账" and r["账户已映射"] is False)


def t_combo_1_to_n():
    # 银行 1 笔 1000 = 金蝶 600 + 400（1:N，理财本息/合并缴税式）
    # V2.178 需求方定：两侧都已做账、合计分毫不差 → 单列「组合待确认」，不算错漏账；仍交人工、不自动核销
    kd1 = krow("1001", "2026-06-18", deb=600.0, memo="本金"); kd1["制单人"] = "叶丽珊"
    kd2 = krow("1001", "2026-06-19", deb=400.0, memo="收益"); kd2["制单人"] = "叶丽珊"
    res, s = run([brow("1001", "2026-06-18", inn=1000.0, memo="理财赎回到账")], [kd1, kd2])
    combos = [r for r in res if r["状态"] == "组合待确认"]
    check("组合候选1:N→两侧共3行单列组合待确认",
          len(combos) == 3 and all(r["组合候选"] for r in combos) and s["组合待确认"] == 3)
    check("组合行不再计入疑似漏账/金蝶单边",
          find(res, 状态="疑似漏账") is None and find(res, 状态="金蝶单边·疑似做错") is None
          and s["疑似漏账"] == 0 and s["金蝶单边·疑似做错"] == 0)
    check("组合仍不自动核销(不进已匹配)", s["已匹配"] == 0)
    # V2.179：整组等式随行——组内每一行都能看到 目标=成员1+成员2（含凭证），人在工作台直接判
    ci = combos[0]["组合明细"]
    check("组合明细整组等式随行", ci is not None and ci["目标"]["金额"] == 1000.0
          and sorted(m["金额"] for m in ci["成员"]) == [400.0, 600.0] and ci["合计"] == 1000.0
          and all(r["组合明细"] == ci for r in combos))
    check("组合成员腿带制单人(V2.195·点开详情逐张列)",
          all(m["制单人"] == "叶丽珊" for m in ci["成员"]))


def t_misbook_wrong_account():
    # V2.180：黄一飞式记错户——钱到 1001 户、凭证记在 3003 户（3003 整户没银行流水）；
    # 银行对方户名出现在他户凭证摘要=强证据 → 银行腿标「疑记错户·账在他户」并附凭证线索
    idx2 = R.ledger_index(LEDGER + [{"账号": "3003", "类别": "银行账户", "主体": "甲公司", "开户行": "招商银行"}])
    banks = [brow("1001", "2026-06-01", out=10.0, memo="货款A"),
             brow("1001", "2026-06-10", inn=3000.0, cp="黄一飞", memo="备用金返还")]
    kds = [krow("1001", "2026-06-01", cred=10.0, memo="货款A"),
           krow("3003", "2026-06-10", deb=3000.0, memo="核销9/33支付黄一飞申请日常办公备用金", vno="333")]
    res, s = R.reconcile(R.bank_to_recs(banks, idx2), R.kd_to_recs(kds, idx2))
    m = find(res, 状态="疑记错户·账在他户")
    check("对方户名命中他户凭证摘要→疑记错户+凭证线索",
          m is not None and "333" in m["记错户对应"] and "3003" in m["记错户对应"])
    check("金蝶腿保持整户缺银行流水且不再算漏账",
          find(res, 状态="账户缺银行流水") is not None and s["疑似漏账"] == 0)
    # V2.194：结构化明细——钱在哪里/账在哪里 两栏齐全，点开详情用
    mi = m["记错户明细"]
    check("记错户明细·钱在/账在两栏齐全",
          mi is not None and mi["钱在"]["账号"] == "1001" and mi["钱在"]["对方"] == "黄一飞"
          and mi["账在"]["账号"] == "3003" and "333" in mi["账在"]["凭证"]
          and mi["账在"]["金额"] == 3000.0)
    kds2 = [krow("1001", "2026-06-01", cred=10.0, memo="货款A"),
            krow("3003", "2026-06-10", deb=3000.0, memo="其他收款", vno="334")]
    res2, s2 = R.reconcile(R.bank_to_recs(banks, idx2), R.kd_to_recs(kds2, idx2))
    check("无户名证据→不猜(仍疑似漏账)", s2["疑似漏账"] == 1 and s2["疑记错户·账在他户"] == 0)
    # V2.200：金蝶腿在【两侧都有数据】的账户上（原会落金蝶单边）→ 双腿都标疑记错户、互指同一份明细
    banks3 = [brow("1001", "2026-06-01", out=10.0, memo="货款A"),
              brow("2002", "2026-06-01", inn=20.0, memo="货款B"),
              brow("1001", "2026-06-10", inn=3000.0, cp="黄一飞", memo="备用金返还")]
    kds3 = [krow("1001", "2026-06-01", cred=10.0, memo="货款A"),
            krow("2002", "2026-06-01", deb=20.0, memo="货款B"),
            krow("2002", "2026-06-10", deb=3000.0, memo="核销支付黄一飞备用金", vno="369")]
    res3, s3 = R.reconcile(R.bank_to_recs(banks3, IDX), R.kd_to_recs(kds3, IDX))
    mrows = [r for r in res3 if r["状态"] == "疑记错户·账在他户"]
    # V2.201 需求方指出双行=重复计数 → 两腿并一行：银行侧列钱、金蝶侧列凭证，计数=真实错误个数
    check("两腿并一行：疑记错户仅1行且带金蝶凭证", len(mrows) == 1
          and s3["金蝶单边·疑似做错"] == 0 and "369" in str(mrows[0]["金蝶凭证"]))
    check("并行后两侧金额与明细齐全", mrows[0]["借方金额"] == 3000.0 and mrows[0]["金蝶金额"] == 3000.0
          and mrows[0]["记错户明细"] and "369" in mrows[0]["记错户明细"]["账在"]["凭证"])


def t_guardrail():
    banks = [brow("1001", "2026-06-01", out=10.0), brow("1001", "2026-06-02", inn=20.0),
             brow("2002", "2026-06-03", out=30.0)]
    kds = [krow("1001", "2026-06-01", cred=10.0), krow("2002", "2026-06-03", cred=30.0)]
    res, s = run(banks, kds)
    g = s["guardrail"]
    check("护栏·银行笔数核对一致", g["银行笔数核对一致"] and g["银行已归类"] == 3)
    check("护栏·金蝶笔数核对一致", g["金蝶笔数核对一致"] and g["金蝶已归类"] == 2)
    check("护栏·合计正确", g["银行支合计"] == 40.0 and g["银行收合计"] == 20.0)
    # 每笔恰归一类：结果行数 = 已配对(2) + 剩余银行1(收20漏账) = 3? 精确核对总覆盖
    total_bank_rows = sum(1 for r in res if r["借方金额"] is not None or r["贷方金额"] is not None)
    check("每笔恰归一类(无重复/无遗漏)", total_bank_rows == len(res))


def t_backward_compat_no_ledger():
    # 无台账(index=None) → 全部透传为已映射，向后兼容
    b = R.bank_to_recs([brow("777", "2026-06-01", out=5.0)], None)
    k = R.kd_to_recs([krow("777", "2026-06-01", cred=5.0)], None)
    res, s = R.reconcile(b, k)
    check("无台账向后兼容(透传→可匹配)", res and res[0]["状态"] == "已匹配")


def t_xfer_counterparty_gate():
    # V2.171：对方户名是外部名称（个人/税务局）→ 不猜内部往来，回归疑似漏账；
    # 集团主体/空白照旧；不传 group_names 保持老行为（向后兼容）
    # 两账户各垫一对可配平流水，保证都进逐笔匹配（账户只在单侧会直接落「账户缺数据」，到不了侦测）
    banks = [brow("1001", "2026-06-01", out=10.0, memo="货款A"),
             brow("2002", "2026-06-01", inn=20.0, memo="货款B"),
             brow("1001", "2026-06-22", out=2800.0, cp="曹水英", memo="网银转账"),
             brow("1001", "2026-06-23", out=500.0, cp="乙公司", memo="往来"),
             brow("1001", "2026-06-24", out=66.0, cp="", memo="归集")]
    kds = [krow("1001", "2026-06-01", cred=10.0, memo="货款A"),
           krow("2002", "2026-06-01", deb=20.0, memo="货款B"),
           krow("2002", "2026-06-22", cred=2800.0, memo="往来款", vno="1"),
           krow("2002", "2026-06-23", cred=500.0, memo="内部往来", vno="2"),
           krow("2002", "2026-06-24", cred=66.0, memo="资金归集", vno="3")]
    b = R.bank_to_recs(banks, IDX)
    k = R.kd_to_recs(kds, IDX)
    res, _ = R.reconcile(b, k, group_names={"甲公司", "乙公司"})
    leak = find(res, 状态="疑似漏账")
    check("外部对方(个人)不猜内部往来→疑似漏账", leak is not None and leak["收(付)方名称"] == "曹水英")
    check("对方是集团主体→仍判内部往来",
          find(res, 状态="内部往来·未做账", **{"收(付)方名称": "乙公司"}) is not None)
    check("对方空白(归集常态)→仍判内部往来",
          find(res, 状态="内部往来·未做账", **{"收(付)方名称": ""}) is not None)
    res2, _ = run(banks, kds)   # 不传 group_names
    check("不传主体名单→老行为(外部名也标内部往来)",
          find(res2, 状态="内部往来·未做账", **{"收(付)方名称": "曹水英"}) is not None)
    # V2.172：两腿明细——同向（钱走本户、账记他户）
    xr = find(res, 状态="内部往来·未做账", **{"收(付)方名称": "乙公司"})
    xi = xr["内部往来明细"]
    check("两腿明细·同向(账号维度记错)标出", xi is not None and xi["同向"] is True
          and xi["对方账号"] == "2002" and "2" in str(xi["对方凭证"]))


def t_xfer_pair_detail_opposite():
    # V2.172：真划转（一收一支）两腿明细——本户收、对方户支已记
    banks = [brow("1001", "2026-06-01", out=10.0, memo="货款A"),
             brow("2002", "2026-06-01", inn=20.0, memo="货款B"),
             brow("1001", "2026-06-10", inn=300.0, cp="", memo="往来入账")]
    kd3 = krow("2002", "2026-06-10", cred=300.0, memo="往来款拨出", vno="77"); kd3["制单人"] = "吴一凡"
    kds = [krow("1001", "2026-06-01", cred=10.0, memo="货款A"),
           krow("2002", "2026-06-01", deb=20.0, memo="货款B"), kd3]
    res, _ = run(banks, kds)
    xr = find(res, 状态="内部往来·未做账")
    xi = xr and xr["内部往来明细"]
    check("两腿明细·反向(真划转)标出", xi is not None and xi["同向"] is False
          and xi["对方方向"] == "支" and "77" in str(xi["对方凭证"]))
    check("对方腿带日期/制单人/摘要(V2.196·点开详情用)",
          xi["对方日期"] == "2026-06-10" and xi["对方制单人"] == "吴一凡" and "往来款拨出" in xi["对方摘要"])


def t_maker_passthrough():
    # V2.169：金蝶行「制单人」透传到结果行——差异要能直接看到经手人
    kd = krow("1001", "2026-06-10", cred=100.0, memo="货款", vno="88")
    kd["制单人"] = "叶丽珊"
    res, _ = run([brow("1001", "2026-06-10", out=100.0, cp="供应商A", memo="货款")], [kd])
    check("金蝶行制单人透传到结果行", res[0]["制单人"] == "叶丽珊")
    res2, _ = run([brow("1001", "2026-06-10", out=5.0)], [krow("1001", "2026-06-10", cred=5.0)])
    check("旧定格数据无制单人列→空串不报错", res2[0]["制单人"] == "")
    res3, _ = run([brow("1001", "2026-06-10", out=5.0), brow("1001", "2026-06-12", out=7.0)],
                  [krow("1001", "2026-06-10", cred=5.0)])
    leak = find(res3, 状态="疑似漏账")
    check("疑似漏账(本无凭证)制单人留空", leak is not None and leak["制单人"] == "")


def t_xfer_both_unbooked():
    # V2.172：两户银行一收一支同额同窗、金蝶两边都查无 → 双双标「内部往来·未做账」+两腿均未做
    banks = [brow("1001", "2026-06-01", out=10.0, memo="货款A"),
             brow("2002", "2026-06-01", inn=20.0, memo="货款B"),
             brow("1001", "2026-06-10", inn=888.0, cp="乙公司", memo="资金调拨"),
             brow("2002", "2026-06-11", out=888.0, cp="", memo="划转出")]
    kds = [krow("1001", "2026-06-01", cred=10.0, memo="货款A"),
           krow("2002", "2026-06-01", deb=20.0, memo="货款B")]
    b, k = R.bank_to_recs(banks, IDX), R.kd_to_recs(kds, IDX)
    res, _ = R.reconcile(b, k, group_names={"甲公司", "乙公司"})
    xr = [r for r in res if r["状态"] == "内部往来·未做账"]
    check("两腿均未做·双双标出", len(xr) == 2
          and all(r["内部往来明细"] and r["内部往来明细"].get("两腿均未做") for r in xr))
    check("两腿互指对方账号", {x["内部往来明细"]["对方账号"] for x in xr} == {"1001", "2002"})
    # 防巧合：对方是外部名称（且无划转字眼）的同额反向 → 不配对，仍是疑似漏账
    banks2 = [brow("1001", "2026-06-01", out=10.0, memo="货款A"),
              brow("2002", "2026-06-01", inn=20.0, memo="货款B"),
              brow("1001", "2026-06-10", inn=777.0, cp="曹水英", memo="转账"),
              brow("2002", "2026-06-11", out=777.0, cp="", memo="付款")]
    res2, _ = R.reconcile(R.bank_to_recs(banks2, IDX), R.kd_to_recs(kds, IDX),
                          group_names={"甲公司", "乙公司"})
    check("外部对方→不猜两腿均未做(仍疑似漏账)",
          not [r for r in res2 if r["状态"] == "内部往来·未做账"]
          and len([r for r in res2 if r["状态"] == "疑似漏账"]) == 2)


if __name__ == "__main__":
    tests = [t_matched_same_day, t_late_month, t_late_cross, t_amount_wrong,
             t_four_decimal_no_false_alarm, t_four_decimal_real_diff,
             t_bank_leak, t_kd_only, t_account_no_kd, t_account_no_bank,
             t_internal_unbooked, t_kd_xfer_crossacct, t_unmapped_not_leak, t_combo_1_to_n,
             t_guardrail, t_backward_compat_no_ledger, t_maker_passthrough,
             t_xfer_counterparty_gate, t_xfer_pair_detail_opposite, t_xfer_both_unbooked,
             t_misbook_wrong_account]
    for t in tests:
        print(f"# {t.__name__}")
        t()
    print(f"\n==== 单测结果：{_pass} 通过 / {_fail} 失败 (共 {_pass + _fail}) ====")
    raise SystemExit(1 if _fail else 0)
