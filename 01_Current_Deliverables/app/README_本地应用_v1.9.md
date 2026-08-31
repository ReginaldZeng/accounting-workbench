[Change Log]
Date: 2026-07-02
Author: Claude / c
Version: V1.9
Description: 财务核算工作台 本地应用（FastAPI + React）。三视图整合、样例数据经内核产出、可本机访问。

# 财务核算工作台 · 本地应用 v1.9

从"原型文件"变成**真正能本机访问的应用**：浏览器打开 `http://localhost:8000` 就能用。
Web 方案落地首版：FastAPI 后端（复用既有 Python 内核 + 金蝶脚本占位） + React 前端（已构建）。

## 怎么运行（只需 Python）
双击 **`启动.bat`**（或在 `backend/` 目录执行）：
```bash
pip install -r requirements.txt
python -m uvicorn app:app --port 8000
```
浏览器开 `http://localhost:8000`。**运行只需 Python**——前端已构建成静态文件由后端托管，无需 Node。

## 三个视图（左侧导航）
- **资金看板**（首页）：集团合计 + 四类科目(1001/1002/1012/1101) + 各主体账户余额；一键接入金蝶(占位)；主体/科目筛选、含已销户、流水更新/金蝶动账双时间。
- **银行-金蝶稽核 · 逐笔稽核**：四态(漏账/金额不符/金蝶单边/已匹配) + 借贷分列 + 凭证/差额。
- **基础数据 · 账户台账**：从金蝶自动同步、本月新增标红 *New、两行筛选；点「从金蝶同步」演示新增2户+停用1户。

## 后端 API（样例数据经内核产出）
- `GET /api/fund-dashboard`、`POST /api/fund-dashboard/sync`
- `GET /api/account-ledger`、`POST /api/account-ledger/sync`
- `GET /api/reconcile`
- `GET /api/health`

数据当前来自 `backend/sample_data.py`，跑的是**真内核**(balance_dashboard / reconcile / account_ledger)，只是喂样例。

## 接金蝶真数据（到内网机上）
1. 把 `金蝶配置文件/conf.ini` 放到能连金蝶的机器；
2. 在 `backend/app.py` 里把三个 `*/sync` 占位改为调 `download_gl_balance.py` / `download_bank_accounts.py` / `download_gl_bank.py` 拉数，再喂给同一批内核函数（入口已留）；
3. 界面无需改动。

## 目录
```
backend/   app.py(FastAPI 平台域：登录/账号/权限/导航/期间/银行对账 + include_router + SPA 兜底)
           core.py(共享内核：路径常量·CFG·会计期间·缓存·封存拦截·金蝶取数定格·权限判定)
           routers/(★一条工具线一个模块 —— 改某条线只动这里，见下)
           sample_data.py · requirements.txt
           kernels/(各工具线的纯算法内核，有单测)
           static/(React 构建产物，后端托管)——★不入库，见下
frontend/  React 源码(Vite)——vite.config.js 已把 build.outDir 指向 ../backend/static
build_frontend.bat  一键构建前端（新增，V2.171）
启动.bat
```

## ★ 本机同时跑多条并行分支（V2.173，2026-08-05 起）

**平时测试只要开 1 个 bat**——项目根的 `启动.bat`。后端自带前端产物，不用另起前端窗口。

多条需求并行时，每个工作树自动分到自己的端口，互不抢占：

| checkout | 端口 | 说明 |
|---|---|---|
| 主库 | 8000 | 不变，原来的书签照用 |
| `.claude\worktrees\<名字>` | 8001~8098 | 由名字算出，同一工作树每次都是同一个 |

- **一次全起**：项目根 `一键启动全部.bat` —— **只开一个窗口**，服务全部后台隐藏跑，
  **默认不弹任何浏览器**，只打印「名称/分支/端口/地址/前端新旧/在跑」一览表，要看哪条自己点。
  `-Open 关键字` 只打开匹配的那一条页面；`-ListOnly` 只看表不启动。
  全部停掉：项目根 `停止全部.bat`（连 `--reload` 的子进程一起清干净）。日志在 `%TEMP%\fw_logs\`。
- **改前端要热更新**才需要第二个窗口：`app/dev_frontend.bat`（dev 页端口 ＝ 后端端口 + 1000，
  自动把 `/api` 代理到**本工作树**的后端，不会串到别的分支）。

> 这不影响服务器部署。线上是宝塔 Python 管理器跑 `uvicorn app:app --host 0.0.0.0 --port 8000` + Nginx 反代，
> 根本不经过 `启动.bat`；端口规则也只在路径位于 `.claude\worktrees\` 下时才触发。线上仍是单 IP 单地址。

## ★ 后端按工具线分模块（V2.172，2026-08-05 起）

原来 `app.py` 一个文件 5006 行装下全部工具线，是并行开发的头号冲突源（最近 60 次提交被改 18 次）。
现在拆成：

| 改什么 | 动哪个文件 |
|--------|-----------|
| 物流计提的接口 | `routers/logistics_accrual.py` |
| 物流对账的接口 | `routers/logistics_recon.py` |
| 成本台账的接口 | `routers/cost_ledger.py` |
| 汇率录入的接口（含自动跑批调度） | `routers/fxrate.py` |
| 凭证归档的接口 | `routers/archive.py` |
| 算法/口径（不涉及接口） | `kernels/<对应内核>.py` |
| 会计期间、封存、权限判定等**所有线共用**的东西 | `core.py` |
| 登录/账号/导航/期间/银行对账，以及**新增一条工具线** | `app.py` |

依赖方向单向 `app.py → routers/* → core.py`，core 不 import 任何 router，不会成环。
新增工具线：写 `routers/<新线>.py`（内含 `router = APIRouter()`），然后在 `app.py` 的
include_router 清单里加一行——**必须加在 SPA 兜底路由之前**，兜底会吃掉所有非 /api 路径。

## ★ 前端构建产物不入库（V2.171，2026-08-05 起）

`backend/static/` 是**构建产物，已 gitignore、不进 git**。

**为什么**：Vite 按内容 hash 给包命名（`assets/index-<hash>.js`），每条并行开发分支构建出的文件名都不一样
→ 合并时 `static/index.html` 每次必冲突，还会残留一堆旧 hash 的死文件。源码在 `frontend/` 里、随时能重打，
所以按项目既有原则（同 `04_部署包/`）出库。

**对你的影响**：

| 场景 | 要做什么 |
|------|---------|
| 双击 `启动.bat` | **不用管**。检测到 `static/` 缺失会自动调 `build_frontend.bat` 重建（首次需 Node.js + 几分钟） |
| 新克隆 / 换机器 | 同上，第一次启动会自动构建；没装 Node 会提示装 |
| 改了 `frontend/src/` 下的东西 | 双击 `build_frontend.bat`，或 `cd frontend && npm run build` |
| **打部署包（后端 zip）** | **必须先构建前端**，否则 zip 里没有前端，服务器上只有 `/api/*` 能用 |

## 二次开发前端（需 Node.js LTS）
```bash
cd frontend
npm install
npm run dev        # 开发热更新(自动代理 /api 到 8000)
npm run build      # 直接产出到 ../backend/static/（无需手工拷贝）
```
或直接双击 `build_frontend.bat`（会自动 npm install + build + 校验产物）。

## 说明
- 只读：不写金蝶、不碰收付。
- 内核是纯 Python、有单测；前端换 React 未动内核，符合"内核不变、外壳可换"。
