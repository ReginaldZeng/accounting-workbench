[Change Log]
Date: 2026-10-29
Author: Kimi
Version: V-draft
Description: 【物流对账】计提更正科目下拉补充6604研发费用。

---

version: V-draft
date: 2026-10-29
line: 物流对账
author: Kimi
status: In Review
revision: 01
---

**【物流对账】计提更正科目补6604**。**起因**：计提更正弹窗的“应改为”科目只有5101/5301/6401/6402/6601等，遗漏实际使用的6604研发费用。**改法**：在独立分支 `fix/logistics-dim-options-6604` 将6604加入 `dim-options` 的 BD_Account 查询清单；前端继续按金蝶编码和名称动态显示，无需改前端。新增回归测试验证查询过滤条件及返回选项。**验证**：专项测试1项通过，物流 readiness 回归26项通过，共27项；`git diff --check` 通过。**缓存**：接口清单缓存12小时，部署时需清理 `_DIMOPT_CACHE` 或重启后端。
