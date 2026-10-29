[Change Log]
Date: 2026-10-29
Author: Kimi
Version: V-draft
Description: 【物流对账】顺丰冷运无金蝶单费用保留，过滤合计与零金额占位行。

---

version: V-draft
date: 2026-10-29
line: 物流对账
author: Kimi
status: In Review
revision: 01
---

**【物流对账】冷运无金蝶单费用保留**。**起因**：SF7002327142385 研发到付260元填写钉钉审批号且研发物料无法入库，被 `doc_re` 过滤，导致系统账单比原始费用行和计提少260元。**改法**：解析器新增按取数说明启用的 `skip_zero`，非标准单号可用 `doc_re_else=default` 保留为无单据，同时小计/合计行跳过、零金额占位行不导入，避免汇总重复。冷运配置将同步使用这些选项。**验证**：真实冷运原件26个父费用行、5,544.40元，sheet_report 为 `ok`；解析、科目选项与 readiness 回归共45项通过。**边界**：无金蝶单只保留金额与原始备注，不去金蝶查物料或判数量一致。
