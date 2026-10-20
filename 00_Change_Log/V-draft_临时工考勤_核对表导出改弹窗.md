[Change Log]
Date: 2026-10-16
Author: Codex
Version: V-draft
Description: 核对表导出面板改为原生模态弹窗，保留范围与数量预览。

---
version: V-draft
date: 2026-10-16
line: 临时工考勤
author: Codex
status: Synced-local
revision: 01
---

点击工具栏导出按钮打开dialog模态框，原页面位置与筛选保持不变。保留全量/服务方/人员范围、搜索及预览，添加关闭和取消，支持Esc，导出期间禁止关闭；成功触发下载后关闭弹窗，失败在弹窗内显示错误。使用原生showModal约束焦点，关闭后返回触发按钮。仅前端布局交互变更，原导出接口不变。既有修订夹开发，构建后同步本地；V-draft待合并定号，未部署远端。
