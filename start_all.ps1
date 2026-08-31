# [Change Log]
# Date: 2026-08-05 | Author: Claude / c | Version: V2.175
# Description: 一键启动全部工作树——V2.175 重做：只开【一个】窗口，服务全部后台隐藏跑，
#              【默认一个浏览器都不弹】。上一版去调每个工作树自己的 启动.bat，
#              而其它工作树还是旧版启动器（写死 8000、互相 taskkill、各弹一个浏览器），
#              结果 N 个黑框 + N 个一样的 localhost:8000 标签页。现在不碰任何 bat，
#              本脚本直接按端口后台拉起 uvicorn，日志落文件，停止用 停止全部.bat。
#              本文件存为 UTF-8 with BOM —— Windows PowerShell 5.1 没有 BOM 会按 ANSI 读，中文全乱。
param(
    [string]$Open = '',      # 只打开这一条的页面（按名称/分支关键字匹配，如 -Open july）；不传就谁也不开
    [switch]$ListOnly        # 只列表，不启动任何东西
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Get-PortFor([string]$path) {
    # 与 启动.bat 完全同一套规则：主库 8000；worktree 按名字算出 8001..8098 的固定端口
    $p = $path.TrimEnd('\')
    if ($p -match '\\\.claude\\worktrees\\([^\\]+)$') {
        $n = $Matches[1]
        $s = 0
        foreach ($c in $n.ToCharArray()) { $s += [int]$c }
        return 8001 + ($s % 98)
    }
    return 8000
}

function Test-Listening([int]$port) {
    try {
        $c = New-Object Net.Sockets.TcpClient
        $c.Connect('127.0.0.1', $port)
        $c.Close()
        return $true
    } catch { return $false }
}

# ── 找到主库根目录（本脚本可能位于任一工作树里）──
$commonDir = (& git -C $here rev-parse --git-common-dir) 2>$null
if (-not $commonDir) {
    Write-Host "[X] 这里不是 git 仓库，无法枚举工作树。" -ForegroundColor Red
    exit 1
}
if (-not [System.IO.Path]::IsPathRooted($commonDir)) {
    $commonDir = Join-Path $here $commonDir
}
$mainRoot = Split-Path -Parent (Resolve-Path -LiteralPath $commonDir).Path

# ── 候选：主库 + .claude/worktrees/* ──
$candidates = @($mainRoot)
$wtDir = Join-Path $mainRoot '.claude\worktrees'
if (Test-Path -LiteralPath $wtDir) {
    foreach ($d in (Get-ChildItem -LiteralPath $wtDir -Directory | Sort-Object Name)) {
        $candidates += $d.FullName
    }
}

$logDir = Join-Path $env:TEMP 'fw_logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$rows = @()
foreach ($p in $candidates) {
    $backendDir = Join-Path $p '01_Current_Deliverables\app\backend'
    if (-not (Test-Path -LiteralPath (Join-Path $backendDir 'app.py'))) { continue }
    $port = Get-PortFor $p
    $name = if ($p -eq $mainRoot) { 'main (主库)' } else { Split-Path -Leaf $p }
    # 分支名必须显示：光看端口分不清哪个窗口是哪条线，最容易「改了却看不到」
    $branch = (& git -C $p rev-parse --abbrev-ref HEAD) 2>$null
    if (-not $branch) { $branch = '?' }
    # 产物是否比前端源码旧——旧了就是在看上一版界面
    $idx = Join-Path $backendDir 'static\index.html'
    $build = '未构建'
    if (Test-Path -LiteralPath $idx) {
        $bt = (Get-Item -LiteralPath $idx).LastWriteTime
        $srcDir = Join-Path $p '01_Current_Deliverables\app\frontend\src'
        $newest = Get-ChildItem -LiteralPath $srcDir -Recurse -File -EA SilentlyContinue |
                  Sort-Object LastWriteTime -Descending | Select-Object -First 1
        $build = if ($newest -and $newest.LastWriteTime -gt $bt) { '过期' } else { '最新' }
    }
    $rows += [pscustomobject]@{
        名称 = $name; 分支 = $branch; 端口 = $port; 地址 = "http://localhost:$port/"
        前端 = $build; 在跑 = (Test-Listening $port); 路径 = $p; 后端 = $backendDir
    }
}

if ($rows.Count -eq 0) {
    Write-Host "[X] 没找到任何可启动的工作树。" -ForegroundColor Red
    exit 1
}

# ── 端口撞车自检 ──
$dupe = $rows | Group-Object 端口 | Where-Object { $_.Count -gt 1 }
foreach ($g in $dupe) {
    $who = ($g.Group | ForEach-Object { $_.名称 }) -join '、'
    Write-Host "[!] 端口 $($g.Name) 撞车：$who —— 改个工作树名字即可错开。" -ForegroundColor Yellow
}

# ── 启动缺的（后台隐藏窗口，日志落文件；绝不弹浏览器）──
if (-not $ListOnly) {
    foreach ($r in $rows) {
        if ($r.在跑) { continue }
        # 前端过期且装过依赖 → 顺手重建（1 秒左右）；没装过依赖就先照旧跑老界面，只提醒
        if ($r.前端 -ne '最新') {
            $fe = Join-Path $r.路径 '01_Current_Deliverables\app\frontend'
            if (Test-Path -LiteralPath (Join-Path $fe 'node_modules')) {
                Write-Host "  重建前端 $($r.名称) ..." -ForegroundColor DarkGray
                Push-Location -LiteralPath $fe
                & cmd /c "npm run build" *> (Join-Path $logDir "build_$($r.端口).log")
                Pop-Location
                if ($LASTEXITCODE -eq 0) { $r.前端 = '最新' }
            } else {
                Write-Host "  [!] $($r.名称) 前端$($r.前端)且未装依赖，先跑现有界面（要最新请进它的 app\ 跑 build_frontend.bat）" -ForegroundColor Yellow
            }
        }
        Write-Host "  启动 $($r.名称) -> $($r.端口)（后台，无窗口）"
        Start-Process -FilePath python -ArgumentList '-m','uvicorn','app:app','--port',"$($r.端口)",'--reload' `
            -WorkingDirectory $r.后端 -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $logDir "$($r.端口)_out.log") `
            -RedirectStandardError  (Join-Path $logDir "$($r.端口)_err.log") | Out-Null
    }
    # 等就绪（最多 ~15 秒），刷新「在跑」
    for ($i = 0; $i -lt 50; $i++) {
        $pending = @($rows | Where-Object { -not $_.在跑 })
        if ($pending.Count -eq 0) { break }
        Start-Sleep -Milliseconds 300
        foreach ($r in $pending) { $r.在跑 = Test-Listening $r.端口 }
    }
}

Write-Host ''
Write-Host '财务核算工作台 · 全部工作树（服务在后台，本窗口关了它们也接着跑）' -ForegroundColor Cyan
$rows | Format-Table 名称, 分支, 端口, 地址, 前端, 在跑 -AutoSize
Write-Host "要看哪条，按住 Ctrl 点上面的地址，或： 一键启动全部.bat -Open 关键字（只开那一条）" -ForegroundColor DarkGray
Write-Host "全部停掉： 停止全部.bat      日志： $logDir" -ForegroundColor DarkGray

# ── -Open：只开匹配的那一条 ──
if ($Open) {
    $hit = @($rows | Where-Object { $_.名称 -like "*$Open*" -or $_.分支 -like "*$Open*" })
    if ($hit.Count -eq 0) {
        Write-Host "[!] 没有名称/分支含「$Open」的工作树。" -ForegroundColor Yellow
    } else {
        if ($hit.Count -gt 1) {
            Write-Host "[!] 「$Open」匹配到 $($hit.Count) 条，只开第一条：$($hit[0].名称)" -ForegroundColor Yellow
        }
        Start-Process $hit[0].地址
    }
}
