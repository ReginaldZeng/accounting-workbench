[Change Log]
Date: 2026-07-03
Author: Claude / c
Version: V1.0
Description: 00_金蝶配置文件 目录说明。金蝶云星空授权配置、取数脚本、历史取数输出的集中存放区。

# 00_金蝶配置文件 · 说明

金蝶云星空（公有云 xingql.ik3cloud.com）相关的**配置、取数脚本、取数输出**集中放这里。

## 内容

| 项 | 说明 |
|---|---|
| `conf.ini` | **金蝶 API 授权**（AcctID / AppSec / ServerUrl / LCID / OrgNum 等）。⚠️ 含密钥，勿外传、勿提交到公开仓库。 |
| `download_*.py` + `下载*.bat` / `列出*.bat` / `测试*.bat` | 取数脚本（科目余额 / 银行存款序时账 / 理财序时账 / 银行账号台账 / 采购订单等）与一键运行 bat。均**只读**取金蝶。 |
| `取数输出参考/` | 历史取数输出数据（权威账户台账、科目余额表、理财赎回等），供参考核对。 |

## 应用如何读取 conf.ini

- 运行中的应用（`01_Current_Deliverables/app/`）读取的是 **`app/backend/conf.ini`**（本目录 conf.ini 的一份副本，纯 ASCII 路径，稳定可靠）。
- **本目录是金蝶配置与脚本的主区/归档**。
- **改金蝶授权**：更新本目录 `conf.ini` 后，复制一份覆盖 `01_Current_Deliverables/app/backend/conf.ini`（两处保持一致）。
- 备选：设环境变量 `KD_CONF_PATH` 指向本目录 conf.ini，应用会优先用它。
- （V1.1 重构时将统一为单一来源，应用直接读本目录，免去双份。）

## 数据源切换

应用「设置」页可在 **样例 / 金蝶** 间切换。当前默认数据源为**金蝶**（`app/backend/sample_data/config.json`）。金蝶模式需本机能上外网。
