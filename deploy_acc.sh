#!/usr/bin/env bash
# accounting-workbench 自动部署（cron 每 5 分钟；也可手动 bash /root/deploy_acc.sh）
# 本文件是服务器 /root/deploy_acc.sh 的留底：服务器上跑的是 /root 下那一份，改了这里要手动装上去——
#   install -m 755 /www/wwwroot/accounting-workbench/deploy_acc.sh /root/deploy_acc.sh.new && mv /root/deploy_acc.sh.new /root/deploy_acc.sh
#   （先落成 .new 再 mv：cron 正在跑的那一趟读的还是旧文件，不会读到半截。）
#
# fetch 看有没有新提交 → 有才 merge；和「上次部署到哪个提交」比，有差才 rsync。
# 前端 static 由后端 StaticFiles 读盘即时生效不重启；只有后端核心 .py（排除 static/tools/templates）改动才走宝塔重启。
# rsync 无 --delete：运行期数据(真实 sample_data/bank_uploads/conf.ini/.env/gateway.db 等)分毫不动。
#
# V2.908 重启前先看有没有人正在写金蝶（2026-10-10 17:42 一张付款做账被重启掐在半截）：
#   要重启时：放「准备重启」标记(后端见了就不再开始新的写金蝶操作) → 轮询 /api/health 的 busy，等正在写的做完 → rsync → 重启 → 撤标记。
#   等 WAIT_MAX 秒还在写：这一轮不部署(代码不落盘、不重启)，撤标记，下一轮(5 分钟后)再来。
#   「上次部署到哪个提交」记在 $DEPLOYED：没部署成的那次 HEAD 已经前进了，靠它下一轮才知道还欠着。
#   后端还是老版本(health 里没有 busy)或没响应：照旧直接重启，不等。
export HOME=/root
export PATH=/usr/local/bin:/usr/bin:/bin
set -euo pipefail

# 下面几项都可以用同名环境变量换掉(只为了能在临时目录里演练这个脚本；线上不设，走默认值)
REPO="${ACC_REPO:-/www/wwwroot/accounting-workbench}"
BACKEND_SRC="$REPO/01_Current_Deliverables/app/backend/"
BACKEND_DST="${ACC_RUN_DIR:-/www/wwwroot/finance_workbench/backend/}"
LOG="${ACC_LOG:-/root/acc_deploy.log}"
DEPLOYED="${ACC_DEPLOYED:-/root/.acc_deployed_commit}"
HEALTH="${ACC_HEALTH:-http://127.0.0.1:8000/api/health}"
RESTART_CMD="${ACC_RESTART:-/usr/bin/btpython /www/server/panel/script/restart_project.py python 财务核算工作台}"
WAIT_MAX="${ACC_WAIT_MAX:-180}"
DRAIN="${BACKEND_DST%/}/.deploy_draining"
TS() { date "+%F %T"; }

cd "$REPO"
git fetch -q origin main 2>/dev/null || { echo "$(TS) [ERR] git fetch 失败" >>"$LOG"; exit 1; }
HEAD_NOW=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)
# 「上次部署到哪个提交」：第一次跑(还没有这个文件)或文件坏了，就当现在克隆里的这个提交是已部署的，先记下来再往下走——
# 不先记的话，头一回就碰上「等不到空闲、这轮不部署」，下一轮克隆已经是最新、会以为没有更新。
OLD=$(cat "$DEPLOYED" 2>/dev/null || true)
if [ -z "$OLD" ] || ! git cat-file -e "$OLD^{commit}" 2>/dev/null; then OLD="$HEAD_NOW"; echo "$OLD" > "$DEPLOYED"; fi
if [ "$OLD" = "$REMOTE" ]; then echo "$(TS) 无更新（$OLD）"; exit 0; fi

# 等正在写金蝶的做完。返回 0＝可以重启；1＝等到头了还在写。
wait_idle() {
  local t=0 h
  touch "$DRAIN"
  sleep 1
  while :; do
    h=$(curl -s --max-time 5 "$HEALTH" || true)
    # 不占线 / 老版本没有这个字段 / 后端没响应：都不用等
    if ! echo "$h" | grep -qE '"busy": *true'; then return 0; fi
    if [ "$t" -ge "$WAIT_MAX" ]; then return 1; fi
    if [ "$t" -eq 0 ]; then echo "  有人正在写金蝶，等它做完再重启：$(echo "$h" | grep -o '"busy_what":\[[^]]*\]' || true)"; fi
    sleep 3; t=$((t + 3))
    touch "$DRAIN"
  done
}

{
  echo "===== $(TS) 检测到新版本，开始部署 ====="
  if [ "$HEAD_NOW" != "$REMOTE" ]; then git merge --ff-only origin/main; fi
  echo "  $OLD -> $REMOTE"
  # 后端核心代码(.py，排除 static/tools/templates)有改动才重启
  RESTART=0
  if git diff --name-only "$OLD" "$REMOTE" -- "01_Current_Deliverables/app/backend/" \
       | grep -vE "app/backend/(static|tools|templates)/" | grep -qE "\.py$"; then RESTART=1; fi
  if [ "$RESTART" = 1 ]; then
    trap 'rm -f "$DRAIN"' EXIT
    if ! wait_idle; then
      rm -f "$DRAIN"
      echo "  ⏸ 等了 ${WAIT_MAX} 秒还有人在写金蝶，这一轮不部署（代码没落盘、没重启），下一轮再来"
      exit 0
    fi
  fi
  echo "  rsync 后端(含 static，无 --delete，保留运行期数据)"
  rsync -a --exclude='conf.ini' --exclude='.env' --exclude='*.db' \
    --exclude='*_uploads/' --exclude='sample_data/*.json' \
    --exclude='version_stamp.json' --exclude='__pycache__/' \
    "$BACKEND_SRC" "$BACKEND_DST"
  # 版本戳：version_stamp.json 是 gitignore 的、git 部署不带它，这里从 git 现算现写到部署目录，
  # 保证左下角版本号跟代码同步（否则会赖着上次手工 zip 部署留下的旧戳，代码新、版本号老）。失败不阻断部署。
  COMMIT=$(git rev-parse --short HEAD)
  python3 - "$BACKEND_DST/version_stamp.json" "$COMMIT" "$REPO" <<'PYEOF' || echo "  [warn] 版本戳写入失败，不影响部署"
import sys, os, re, json, datetime
dst, commit, repo = sys.argv[1], sys.argv[2], sys.argv[3]
def key(v):
    m = re.match(r"[Vv](\d+)\.(\d+)", v or "")
    return (int(m.group(1)), int(m.group(2))) if m else (-1, -1)
cands = []
d = os.path.join(repo, "00_Change_Log")
if os.path.isdir(d):
    for fn in os.listdir(d):
        m = re.match(r"(V\d+\.\d+)_", fn)
        if m:
            cands.append(m.group(1))
ver = max(cands, key=key) if cands else ""
json.dump({"ver": ver, "branch": "main", "commit": commit,
           "packed_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "dirty": False},
          open(dst, "w"), ensure_ascii=False, indent=1)
print("  版本戳 -> %s %s" % (ver, commit))
PYEOF
  if [ "$RESTART" = 1 ]; then
    echo "  后端核心 .py 有改动 -> 宝塔重启 财务核算工作台"
    $RESTART_CMD
    rm -f "$DRAIN"
    sleep 4
    echo "  health: $(curl -s --max-time 5 "$HEALTH" || echo 无响应!)"
  else
    echo "  仅前端/工具/模板改动 -> 不重启（前端 StaticFiles 读盘即时生效）"
  fi
  echo "$REMOTE" > "$DEPLOYED"
  echo "  ✅ 完成：$(git log -1 --oneline)"
} 2>&1 | tee -a "$LOG"
