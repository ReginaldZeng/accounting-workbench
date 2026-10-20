**Comparison Artifacts**

- Source visual truth: `C:\Users\94899\AppData\Local\Temp\codex-clipboard-42089d6a-5a47-486e-b9df-44751aad5492.png`
- Source pixels: 1050 × 216; cropped desktop header reference; density not declared.
- Rendered implementation: `http://localhost:8000/`, captured from the in-app Browser in this task (tab 1; the browser tool exposes the capture in-task rather than as a filesystem path).
- Implementation pixels / viewport: 1265 × 710 CSS px, device scale factor 1.
- State: pending-review BOM detail, source-update menu and more-actions menu checked separately.
- Normalization: compared the common header/action region; the source is a narrow crop while the implementation capture includes the surrounding application shell, so browser shell and below-header content were excluded from judgment.

**Findings**

- No actionable P0/P1/P2 mismatch remains in the requested region.
- The title starts directly with the product name, followed by CP code, material type, review state, and the product switcher.
- The pending-review action order is `更新源表` → `修改价税费` → `审核通过` → `···`; both menus contain the requested items.
- Typography, spacing, existing color tokens, and control styling stay aligned with the original workbench rather than introducing a new visual system.
- There are no new image assets in this region; existing application icons and assets are unchanged.
- Copy matches the requested labels. Permission-gated actions remain permission-gated.

**Open Questions**

- The sample database currently contains no visible approved standard-cost row, so the approved-state toolbar was verified from render logic rather than an on-screen sample record.

**Full-view Comparison Evidence**

- In-app Browser capture at 1265 × 710 confirms the original workbench shell and detail content remain intact while only the header/action region changes.

**Focused Region Comparison Evidence**

- Header capture confirms product name, spaced CP code, type badge, `复核中`, and `切换产品` in one identity row.
- Open `更新源表` capture confirms `更新研发 BOM 表` and `更新采购核算表`.
- Open `···` capture confirms `修改物料名称`, `查看变更记录`, `申请作废`, and `删除记录`.

**Comparison History**

- Iteration 1 finding [P2]: CP code sat too close to the product name, and the source-update menu opened leftward across the content boundary.
- Fix: added an 8 px title/CP gap and left-aligned the source-update dropdown to its trigger.
- Post-fix evidence: refreshed in-app Browser captures show readable title separation and the dropdown opening beneath the trigger inside the content area.

**Implementation Checklist**

- [x] Preserve the original page structure and detail tables.
- [x] Replace the title prefix with product identity and review state.
- [x] Add same-group product switching in `CP码丨产品` format.
- [x] Implement the pending-review action order and menus.
- [x] Implement approved-state `撤销审核` and `导出核算表` actions.
- [x] Keep source updates and rename operations wired to backend APIs and audit trails.
- [x] Build production assets and visually inspect the requested interaction states.

**Follow-up Polish**

- None required for this scoped change.

final result: passed

## 待统一修改（用户走查登记 · 2026-09-11）

本轮仅登记，暂不修改页面：

1. 删除标题下方整行“来源：钉钉审批……程序解析……版本数”。
2. 原料区表头仅保留“1 原料明细”；删除公式口径、类型提示、核价提示、明细数量及上一版比较等附加文字。
3. “更新源表”按钮需要重新做视觉样式，使其与顶部主操作区更协调、更精致。
4. 产品标题行的产品名、CP码、物料类别、审核状态和切换产品控件间距过密，需要重新拉开层级与间距。
5. 包材区与原料区统一：表头仅保留“2 包材明细”，删除核价提示、明细数量和上一版比较等附加文字。
6. 修改税率状态下，“发票类型”列发生遮挡；需要检查编辑控件宽度、列宽及横向布局，确保表头和单元格均完整可见。
