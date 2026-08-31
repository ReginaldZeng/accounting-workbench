[Change Log]
Date: 2026-07-02
Author: Claude / c
Description: 本地应用（这一个文件夹）是"活的应用"，逐版演进；此处记应用内版本，与总台账 00_Change_Log.md 对应。

# 本地应用 · 版本记录

## v1.10（2026-07-02）接金蝶真数据 + 设置页
- 新增 `backend/kingdee_client.py`：金蝶云星空 WebAPI 客户端（登录/分页查询），直接返回 dict（列名对齐内核）；三类取数 `fetch_gl_balance` / `fetch_bank_accounts`(CN_BANKACNT) / `fetch_gl_voucher`；网络异常统一包成 KingdeeError，接口优雅报错不崩。
- `backend/app.py`：**数据源可切换 样例/金蝶**（`/api/config` 读写，持久化 config.json）；`/api/kingdee/test` 测连接；`/api/health` 返回源与 conf 路径。资金看板、账户台账在"金蝶"模式下走真实拉数→同一批内核。
- 前端：新增**设置页**（数据源单选 / 会计期间 / 测试连接 / conf 路径显示）；左侧底部**数据源徽标**（样例/金蝶）；资金看板与账户台账加**金蝶失败红色 banner**；切换数据源各页自动刷新。
- 发现：金蝶是**云星空公有云**（xingql.ik3cloud.com），非内网——能上网即可接真数据（防火墙放行即可）。
- 图标：`@tabler/icons-webfont` npm 包不含字体二进制，无法离线打包；因运行机可上网（要连金蝶云），暂回退 CDN 图标。**真离线图标**（改用 SVG 图标组件/内联）列为后续小项。
- 逐笔稽核仍用样例（银行流水那半边需上传归一化明细，留 v1.11）。

## 用法变化
- 首页启动同前（`启动.bat`）。想接真数据：设置页选"金蝶真数据"→保存；把授权 `conf.ini` 放到 `backend/`（或设环境变量 `KD_CONF_PATH`）。用"测试连接"确认，再回资金看板点"一键接入金蝶"。

## v1.9（2026-07-02）首版
- FastAPI + React 三视图（资金看板/逐笔稽核/账户台账），样例数据经真内核产出，本机 localhost:8000 可访问。


## 2026-07-03 启动.bat 修复
- 现象: 双击后浏览器打开但页面打不开(服务器没就绪/依赖没装上)。
- 修复: ①python/py 自动择一并校验; ②依赖只在缺失时安装(不再每次重装、避免离线卡住), 装不上给明确提示; ③浏览器延迟约6秒再开(等 uvicorn 起来, 解决空白页); ④出错窗口保留(pause)并提示端口占用可能。全英文提示避免老cmd中文乱码。
- 页面 {"detail":"Not Found"} 修复: StaticFiles(html=True) 无 SPA 兜底, 前端子路径/刷新(如 /account-ledger)会 404。app.py 改为 显式 "/" 返回 index.html + catch-all `/{full_path:path}` 兜底(真实静态文件直给, 其余非API的GET回index.html), /api/* 仍优先。沙箱验证 /、子路径、/assets、/api 全部 200。**需关掉旧黑窗、重跑启动.bat 才生效。**
- 端口占用修复(error 10048 / 旧服务器残留导致仍显示 Not Found): 启动.bat 启动前先 netstat 查 8000 端口的 LISTENING 进程并 taskkill 清掉, 稍等释放后再起新服务。这样不必手动找旧黑窗, 每次双击都能起到最新代码。
