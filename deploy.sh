#!/usr/bin/env bash
# 核算工作台 · 服务器一键部署（git 拉取即部署，前端构建物已在 git、服务器无需 node）
# 用法：在服务器上 `bash deploy.sh`；或 cron 每 1–2 分钟自动跑一次 = 自动部署（推 main 后自动上线）。
# 前置：服务器已 git clone 本仓库、后端直接从克隆里跑（运行期数据 db/conf.ini/uploads/.env 都在克隆的 backend 目录里，已 gitignore，git pull 不碰）。
set -euo pipefail

# ── 按你服务器实际改这两行 ──
REPO="${ACC_REPO:-/www/wwwroot/accounting-workbench}"   # git 克隆目录
RESTART_CMD="${ACC_RESTART:-}"                           # 重启命令，如 "systemctl restart acc-workbench"；留空=不自动重启（--reload 会自己热更）

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

# 版本戳：写下最新 V 号，页脚/health 即使 HEAD 是 merge 提交也显对（可选，有 .git 时其实能自算）
VER="$(git log --oneline -20 | grep -oE 'V[0-9]+\.[0-9]+' | head -1 || true)"
if [ -n "$VER" ]; then
  printf '{"ver":"%s","commit":"%s","branch":"main"}\n' \
    "$VER" "$(git rev-parse --short HEAD)" \
    > "$REPO/01_Current_Deliverables/app/backend/version_stamp.json" || true
  echo "  版本戳 → $VER"
fi

# 前端构建物已随 git 更新（static/），无需 npm build。运行期数据 gitignore，未触碰。
if [ -n "$RESTART_CMD" ]; then
  echo "  重启后端： $RESTART_CMD"
  eval "$RESTART_CMD"
else
  echo "  未配 ACC_RESTART：若后端以 --reload 运行会自动热更；否则请手动重启（宝塔 Python 项目→重启 / systemctl restart <服务名>）。"
fi
echo "[$(date '+%F %T')] 部署完成。对版： cd $REPO && git log -1 ；或看页脚版本号。"
