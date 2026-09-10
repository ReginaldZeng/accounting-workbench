[Change Log]
Date: 2026-09-08
Author: Claude / c
Version: V2.525
Description: BOM 采购核算表取件机（装在成本会计电脑上，落桌面）安装说明。

# BOM 采购核算表取件机 · 安装说明（成本会计电脑）

## 这是干什么的

成本会计在工作台点完**初审**，工作台自动出两份采购核算表：`（财务版）CP码 产品名 YYYYMMDD HH：mm.xlsx`（全量）和
`（脱敏版）…`（遮型号/规格/供应商），都带上游复配料/半成品页。这个小程序每 2 分钟去工作台把新出的文件取回本机，
默认放在 **桌面\BOM报价审核\2026年\09月\**。什么都不用点，文件自己出现。终审不再覆盖。

它揣的是一把 **BOM 专用取件码**：只能取这些采购核算表，取不了报表，登录不了工作台。

## 三步装好

1. 在这台电脑建个目录，比如 `D:\BOM取件\`，把这五个文件放进去：`bom_pull.ps1`、`bom_pull_hidden.vbs`、`bom_pull.ini.example`、`install_bom_task.ps1`、`注册BOM取件.bat`。
2. 把 `bom_pull.ini.example` 复制改名为 `bom_pull.ini`，填三项：`server`（工作台网址）、`pull_token`（找管理员要 BOM 专用码）、`dest_dir`（默认桌面\BOM报价审核，可改）。
3. 右键 `注册BOM取件.bat` → 以管理员身份运行。它会先试跑一次验证配置，通过后注册成每 2 分钟一次的无窗口计划任务。

## 管理员侧

服务器 `conf.ini` 的 `[bom]` 段配 `pull_token = <24 位以上纯 ASCII 随机串>`，与成本会计电脑上 `bom_pull.ini` 的一致。
生成：`python -c "import secrets;print(secrets.token_urlsafe(24))"`。凭据只写这两处，不进代码不进文档。

## 常见情况

- 日志在 `bom_pull.log`；正常时每轮没新文件不写日志，有新文件才写一行。
- 「服务器拒绝（HTTP 403）」＝取件码不对或服务器没配。
- 电脑关机期间不取，开机后下一轮自动补齐，不会丢。
- 只增不删：本机文件夹里的文件自己整理，取件机不会删。
