# [Change Log]
# Date: 2026-09-08 | Author: Claude / c | Version: V2.525
# ⚠ 本文件必须存为 **UTF-8 with BOM**：Windows PowerShell 5.1 没 BOM 就按 GBK 读，中文全乱、引号配对崩。
# Description: BOM 采购核算表【下行】小取件机——装在成本会计电脑上，把工作台初审通过后自动出的
#   「（财务版）/（脱敏版）CP码 产品名 审核日期.xlsx」按 年\月 目录取到本机（默认桌面\BOM报价审核）。
#   只取 BOM：揣的是 conf.ini [bom] pull_token 这把专用码，取不了报表、登录不了工作台。
#   与报表取件机 pull_reports.ps1 同款路子：只比「服务器端 大小+修改时间」判变；本机文件只增不删。

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.ServicePointManager]::SecurityProtocol

$HERE = Split-Path -Parent $MyInvocation.MyCommand.Path
$INI = Join-Path $HERE 'bom_pull.ini'
$LOGF = Join-Path $HERE 'bom_pull.log'
$StateFile = Join-Path $HERE 'bom_pull_state.json'

function Write-Log([string]$msg) {
    $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Write-Output $line
    try {
        $old = @()
        if (Test-Path -LiteralPath $LOGF) { $old = @(Get-Content -LiteralPath $LOGF -Encoding UTF8 -EA SilentlyContinue | Select-Object -Last 1999) }
        ($old + $line) | Out-File -LiteralPath $LOGF -Encoding utf8
    } catch {}
}

function Read-Ini {
    if (-not (Test-Path -LiteralPath $INI)) {
        Write-Log "[X] 缺配置文件：$INI（把 bom_pull.ini.example 复制改名为 bom_pull.ini）"; exit 2
    }
    $cfg = @{ timeout = 45 }
    foreach ($ln0 in Get-Content -LiteralPath $INI -Encoding UTF8) {
        $ln = [regex]::Replace([string]$ln0, '[\x00-\x1F\x7F]', '')
        $t = $ln.Trim()
        if (-not $t -or $t.StartsWith(';') -or $t.StartsWith('#') -or $t.StartsWith('[')) { continue }
        $i = $t.IndexOf('=')
        if ($i -lt 1) { continue }
        $cfg[$t.Substring(0, $i).Trim()] = $t.Substring($i + 1).Trim()
    }
    $cfg.server = ($cfg.server -as [string]).TrimEnd('/')
    # dest_dir 支持 %USERPROFILE% 这类环境变量（默认桌面）
    $cfg.dest_dir = [Environment]::ExpandEnvironmentVariables(($cfg.dest_dir -as [string]))
    $miss = @('server', 'pull_token', 'dest_dir') | Where-Object { -not $cfg[$_] }
    if ($miss) { Write-Log ('[X] bom_pull.ini 缺项：' + ($miss -join '、')); exit 2 }
    $cfg.timeout = [int]$cfg.timeout
    return $cfg
}

function Invoke-Api($cfg, [string]$path, [string]$query) {
    $url = $cfg.server + $path
    if ($query) { $url += '?' + $query }
    $r = Invoke-WebRequest -Uri $url -Headers @{ 'X-Pull-Token' = $cfg.pull_token } -UseBasicParsing -TimeoutSec $cfg.timeout
    return $r.RawContentStream.ToArray()
}
function Invoke-ApiJson($cfg, [string]$path) {
    return [Text.Encoding]::UTF8.GetString((Invoke-Api $cfg $path '')) | ConvertFrom-Json
}

$cfg = Read-Ini
# 目标目录：本机路径不存在就建（桌面下建个 BOM报价审核）；父目录都不存在才报错
if (-not (Test-Path -LiteralPath $cfg.dest_dir)) {
    $parent = Split-Path -Parent $cfg.dest_dir
    if ($parent -and (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $cfg.dest_dir -Force | Out-Null }
    else { Write-Log ('[X] 目标目录不存在且父目录也没有：{0}' -f $cfg.dest_dir); exit 2 }
}

try {
    $lst = Invoke-ApiJson $cfg '/api/bom/outbox/files'
} catch {
    $code = $null; try { $code = [int]$_.Exception.Response.StatusCode } catch {}
    if ($code -eq 403 -or $code -eq 401) { Write-Log "[X] 服务器拒绝（HTTP $code）：取件码不对，或服务器 conf.ini [bom] 没配 pull_token。" }
    else { Write-Log ('[X] 连不上服务器 {0}：{1}' -f $cfg.server, $_.Exception.Message) }
    exit 1
}
if (-not $lst.ok) { Write-Log ('[X] 服务器回：' + $lst.msg); exit 1 }

$state = @{}
if (Test-Path -LiteralPath $StateFile) {
    try { (Get-Content -LiteralPath $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $state[$_.Name] = $_.Value } } catch {}
}

$copied = @(); $skipped = 0; $errors = @(); $retry = @()
foreach ($f in $lst.files) {
    $rel = $f.rel
    $dst = Join-Path $cfg.dest_dir ($rel -replace '/', '\')
    $stamp = '{0}|{1}' -f $f.size, $f.mtime
    if ((Test-Path -LiteralPath $dst) -and $state[$rel] -and ($state[$rel] -eq $stamp)) { $skipped++; continue }
    try {
        $blob = Invoke-Api $cfg '/api/bom/outbox/download' ('name=' + [uri]::EscapeDataString($rel))
        if ($blob.Length -ne $f.size) { $retry += $rel; continue }      # 服务器正在重写这个文件，下轮再取
        $sub = Split-Path -Parent $dst
        if ($sub -and -not (Test-Path -LiteralPath $sub)) { New-Item -ItemType Directory -Path $sub -Force | Out-Null }
        $tmp = Join-Path $sub ('.' + [guid]::NewGuid().ToString('N') + '.part')
        [IO.File]::WriteAllBytes($tmp, $blob)
        Move-Item -LiteralPath $tmp -Destination $dst -Force
        $state[$rel] = $stamp
        $copied += $rel
    } catch {
        $errors += ('{0}: {1}' -f $rel, $_.Exception.Message)
    }
}
try { $state | ConvertTo-Json -Depth 3 | Out-File -LiteralPath $StateFile -Encoding utf8 } catch {}

if ($copied.Count -or $errors.Count -or $retry.Count) {
    Write-Log ('BOM 采购核算表：下载 {0} 个、跳过 {1} 个、待重取 {2} 个、失败 {3} 个 → {4}' -f $copied.Count, $skipped, $retry.Count, $errors.Count, $cfg.dest_dir)
    foreach ($n in $copied) { Write-Log ('   [OK] ' + $n) }
    foreach ($n in $retry) { Write-Log ('   [等] ' + $n) }
    foreach ($e in $errors) { Write-Log ('   [X]  ' + $e) }
}
if ($errors.Count) { exit 1 } else { exit 0 }
