#!/usr/bin/env bash
# 核算工作台 · 服务器一键部署（git 拉取即部署，前端构建物已在 git、服务器无需 node）
# 用法：在服务器上 `bash deploy.sh`；或 cron 每 1–2 分钟自动跑一次 = 自动部署（推 main 后自动上线）。
#
# 架构（与 BP 一致）：
#   ┌ git 克隆目录（源，含 .git，只拉不改）      = $ACC_REPO
#   └ 运行目录（扁平，跑生产的 backend，无 .git） = $ACC_RUN_DIR
#   部署 = 克隆里 git pull → rsync 源码到运行目录（不带 --delete，护住运行期数据）→ 写版本戳 → 重启。
#   运行期数据（conf.ini/.env/*.db/*_uploads/ 等）只在运行目录、git 里没有，rsync 明确 --exclude 保护。
#   ⚠ 若你就是「直接从克隆里跑」（无独立运行目录），把 ACC_RUN_DIR 设成克隆的 backend 目录即可，
#      脚本会自动跳过 rsync（源=目标时不复制）。
set -euo pipefail

# ── 按你服务器实际改这三行（或用同名环境变量传入）──
REPO="${ACC_REPO:-/www/wwwroot/accounting-workbench}"                     # git 克隆目录
RUN_DIR="${ACC_RUN_DIR:-/www/wwwroot/finance_workbench/backend}"          # 跑生产的 backend 目录
RESTART_CMD="${ACC_RESTART:-}"                                            # 重启命令，如 "systemctl restart acc-workbench"；留空=不自动重启（需手动重启宝塔 Python 项目）

SRC="$REPO/01_Current_Deliverables/app/backend"

cd "$REPO"
BEFORE="$(git rev-parse HEAD)"
git fetch --quiet origin main
AFTER_REMOTE="$(git rev-parse origin/main)"

if [ "$BEFORE" = "$AFTER_REMOTE" ]; then
  echo "[$(date '+%F %T')] 无新提交（$BEFORE），跳过。"
  exit 0
fi

echo "[$(date '+%F %T')] 发现新版本：$BEFORE → $AFTER_REMOTE，开始部署。"
git pull --ff-only origin main
echo "  当前提交： $(git log -1 --oneline)"

# ── rsync 源码 → 运行目录（不带 --delete：绝不删运行目录里的运行期数据）──
#    额外 --exclude 双保险：即便某运行期文件与 git 里的样例同名，也绝不被覆盖。
#    ⚠ sample_data/*.json 一并排除——真台账配置(如 cost_ledger_config.json)与 git 样例同名，
#      不排除会被样例覆盖，等于清空生产配置（实为高危，必须保留此行）。
if [ "$SRC" != "$RUN_DIR" ]; then
  echo "  同步源码： $SRC/ → $RUN_DIR/（不带 --delete）"
  rsync -a \
    --exclude='conf.ini' \
    --exclude='.env' \
    --exclude='gateway.db' \
    --exclude='*_uploads/' \
    --exclude='sample_data/*.db' \
    --exclude='sample_data/*.json' \
    --exclude='version_stamp.json' \
    --exclude='__pycache__/' \
    "$SRC/" "$RUN_DIR/"
else
  echo "  运行目录即克隆目录，跳过 rsync（直接从克隆里跑）。"
fi

# ── 版本戳：写进【运行目录】（它无 .git，页脚/health 全靠这个文件）──
#    扫最近 30 条提交标题取最新 V 号——HEAD 若是无 V 号的 merge 提交也不受影响（这是 V2.422 卡壳的根因）。
VER="$(git log --oneline -30 | grep -oE 'V[0-9]+\.[0-9]+' | head -1 || true)"
if [ -n "$VER" ]; then
  printf '{"ver":"%s","commit":"%s","branch":"main"}\n' \
    "$VER" "$(git rev-parse --short HEAD)" \
    > "$RUN_DIR/version_stamp.json" || true
  echo "  版本戳 → $VER（写入 $RUN_DIR/version_stamp.json）"
fi

# ── 重启后端 ──
if [ -n "$RESTART_CMD" ]; then
  echo "  重启后端： $RESTART_CMD"
  eval "$RESTART_CMD"
else
  echo "  未配 ACC_RESTART：请手动重启（宝塔 Python 项目→重启 / systemctl restart <服务名>）。"
  echo "  ——rsync 只换文件不重启进程，不重启= 代码没生效。cron 自动部署务必配 ACC_RESTART。"
fi
echo "[$(date '+%F %T')] 部署完成。对版： 看页脚版本号，应显 $VER。"
