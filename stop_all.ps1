# [Change Log]
# Date: 2026-08-05 | Author: Claude / c | Version: V2.175
# Description: 停掉「一键启动全部」拉起的所有后台服务（按各工作树的固定端口找，逐个停进程）。
#              本文件存为 UTF-8 with BOM。
$ErrorActionPreference = 'SilentlyContinue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Get-PortFor([string]$path) {
    $p = $path.TrimEnd('\')
    if ($p -match '\\\.claude\\worktrees\\([^\\]+)$') {
        $n = $Matches[1]; $s = 0
        foreach ($c in $n.ToCharArray()) { $s += [int]$c }
        return 8001 + ($s % 98)
    }
    return 8000
}

$commonDir = (& git -C $here rev-parse --git-common-dir) 2>$null
if (-not $commonDir) { Write-Host "[X] 这里不是 git 仓库。" -ForegroundColor Red; exit 1 }
if (-not [System.IO.Path]::IsPathRooted($commonDir)) { $commonDir = Join-Path $here $commonDir }
$mainRoot = Split-Path -Parent (Resolve-Path -LiteralPath $commonDir).Path

$candidates = @($mainRoot)
$wtDir = Join-Path $mainRoot '.claude\worktrees'
if (Test-Path -LiteralPath $wtDir) {
    foreach ($d in (Get-ChildItem -LiteralPath $wtDir -Directory)) { $candidates += $d.FullName }
}

$stopped = 0
foreach ($p in $candidates) {
    if (-not (Test-Path -LiteralPath (Join-Path $p '01_Current_Deliverables\app\backend\app.py'))) { continue }
    $port = Get-PortFor $p
    $name = if ($p -eq $mainRoot) { 'main (主库)' } else { Split-Path -Leaf $p }
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($pid_ in ($conns | Select-Object -ExpandProperty OwningProcess -Unique)) {
        # uvicorn --reload 是父子两个进程，连子树一起杀干净，别留半死的监听
        & taskkill /F /T /PID $pid_ *> $null
        $stopped++
        Write-Host "  已停 $name（端口 $port，PID $pid_）"
    }
}

if ($stopped -eq 0) { Write-Host '没有在跑的，无需停止。' -ForegroundColor Green }
else { Write-Host ''; Write-Host "共停掉 $stopped 个进程。" -ForegroundColor Green }
