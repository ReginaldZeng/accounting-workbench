# [Change Log]
# Date: 2026-09-11 | Author: Codex | Version: V2.561
# Description: 复用报表取件机的计划任务和取件码，把旺店通共享盘 XLSX 主动推给 BP 工作台。

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.ServicePointManager]::SecurityProtocol

$EcomHere = Split-Path -Parent $MyInvocation.MyCommand.Path
$EcomIni = Join-Path $EcomHere 'pull_reports.ini'
$EcomLog = Join-Path $EcomHere 'pickup_ecommerce.log'
$EcomStateFile = Join-Path $EcomHere 'pickup_ecommerce_state.json'

function Write-EcommerceLog([string]$Message) {
    $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Write-Output $line
    try {
        $old = @()
        if (Test-Path -LiteralPath $EcomLog) {
            $old = @(Get-Content -LiteralPath $EcomLog -Encoding UTF8 -EA SilentlyContinue | Select-Object -Last 999)
        }
        ($old + $line) | Out-File -LiteralPath $EcomLog -Encoding utf8
    } catch {}
}

function Read-EcommerceConfig {
    if (-not (Test-Path -LiteralPath $EcomIni)) { return $null }
    $cfg = @{ timeout = 45 }
    foreach ($ln in Get-Content -LiteralPath $EcomIni -Encoding UTF8) {
        $text = $ln.Trim()
        if (-not $text -or $text.StartsWith(';') -or $text.StartsWith('#') -or $text.StartsWith('[')) { continue }
        $i = $text.IndexOf('=')
        if ($i -lt 1) { continue }
        $cfg[$text.Substring(0, $i).Trim()] = $text.Substring($i + 1).Trim()
    }
    if (-not $cfg.ecommerce_inventory_dir -and -not $cfg.ecommerce_shipments_dir) { return $null }
    $cfg.server = ($cfg.server -as [string]).TrimEnd('/')
    if (-not $cfg.server -or -not $cfg.pull_token) {
        throw 'pull_reports.ini 缺少 server 或 pull_token'
    }
    $cfg.bp_server = ($cfg.bp_server -as [string]).TrimEnd('/')
    if (-not $cfg.bp_server) { $cfg.bp_server = 'https://finance.starfieldsz.com' }
    return $cfg
}

function Invoke-EcommerceJson($cfg, [string]$Path, $Body) {
    $params = @{
        Uri = $cfg.bp_server + '/bp/api/wdt-agent/shared' + $Path
        Method = 'POST'
        Headers = @{ 'X-Pull-Token' = $cfg.pull_token }
        ContentType = 'application/json;charset=utf-8'
        Body = [Text.Encoding]::UTF8.GetBytes(($Body | ConvertTo-Json -Depth 5 -Compress))
        UseBasicParsing = $true
        TimeoutSec = [int]$cfg.timeout
    }
    $response = Invoke-WebRequest @params
    return [Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray()) | ConvertFrom-Json
}

function Send-EcommerceFile($cfg, $Target, $File, $Job) {
    $headers = @{
        'X-Pull-Token' = $cfg.pull_token
        'X-File-Name' = [uri]::EscapeDataString($File.Name)
    }
    if ($Job) {
        $headers['X-WDT-Lease'] = $Job.lease
        $path = '/jobs/{0}/complete' -f $Job.id
    } else {
        $path = '/automatic?kind=' + [uri]::EscapeDataString($Target.kind)
    }
    $response = Invoke-WebRequest -Uri ($cfg.bp_server + '/bp/api/wdt-agent/shared' + $path) `
        -Method POST -Headers $headers -InFile $File.FullName `
        -ContentType 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' `
        -UseBasicParsing -TimeoutSec ([int]$cfg.timeout)
    return [Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray()) | ConvertFrom-Json
}

function Fail-EcommerceJob($cfg, $Job, [string]$Reason) {
    if (-not $Job) { return }
    try {
        Invoke-EcommerceJson $cfg ('/jobs/{0}/fail' -f $Job.id) @{ lease = $Job.lease; reason = $Reason } | Out-Null
    } catch {}
}

function Read-EcommerceState {
    $state = @{}
    if (Test-Path -LiteralPath $EcomStateFile) {
        try {
            (Get-Content -LiteralPath $EcomStateFile -Raw -Encoding UTF8 | ConvertFrom-Json).PSObject.Properties |
                ForEach-Object { $state[$_.Name] = $_.Value }
        } catch {}
    }
    return $state
}

function Invoke-EcommercePickup {
    $cfg = Read-EcommerceConfig
    if (-not $cfg) { return }

    $targets = @(
        @{ kind = 'pickup_inventory'; label = '库存'; dir = $cfg.ecommerce_inventory_dir },
        @{ kind = 'pickup_shipments'; label = '销售出库明细'; dir = $cfg.ecommerce_shipments_dir }
    )
    $missing = @($targets | Where-Object { -not $_.dir -or -not (Test-Path -LiteralPath $_.dir) })
    $ready = $missing.Count -eq 0
    try {
        $poll = Invoke-EcommerceJson $cfg '/poll' @{
            host = $env:COMPUTERNAME
            ready = $ready
            reason = if ($ready) { 'ready' } else { 'pickup_unavailable' }
        }
    } catch {
        Write-EcommerceLog ('[X] 旺店通取件接口不可用：' + $_.Exception.Message)
        return
    }
    if (-not $ready) {
        Write-EcommerceLog ('[X] 旺店通共享盘目录不可访问：' + (($missing | ForEach-Object { $_.dir }) -join '；'))
        return
    }

    $job = $poll.job
    if ($job) {
        $targets = @($targets | Where-Object { $_.kind -eq $job.kind })
        if (-not $targets.Count) {
            Fail-EcommerceJob $cfg $job 'worker_error'
            return
        }
        Write-EcommerceLog ('收到手工扫描请求：' + $targets[0].label)
    }

    $state = Read-EcommerceState
    foreach ($target in $targets) {
        $file = Get-ChildItem -LiteralPath $target.dir -Recurse -File -Filter '*.xlsx' -EA SilentlyContinue |
            Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
        if (-not $file) {
            if ($job) { Fail-EcommerceJob $cfg $job 'pickup_file_missing' }
            continue
        }
        # 刚写入 30 秒内先不碰；下一分钟再取，避免读到“另存为”尚未写完的半份 Excel。
        if (((Get-Date).ToUniversalTime() - $file.LastWriteTimeUtc).TotalSeconds -lt 30) {
            if ($job) { Fail-EcommerceJob $cfg $job 'pickup_file_missing' }
            continue
        }
        $signature = '{0}|{1}|{2}' -f $file.FullName.ToLowerInvariant(), $file.Length, $file.LastWriteTimeUtc.Ticks
        if (-not $job -and $state[$target.kind] -eq $signature) { continue }
        try {
            $result = Send-EcommerceFile $cfg $target $file $job
            $state[$target.kind] = $signature
            Write-EcommerceLog ('[OK] {0}：{1}（{2} 行）' -f $target.label, $file.Name, $result.rows)
        } catch {
            if ($job) { Fail-EcommerceJob $cfg $job 'upload_failed' }
            Write-EcommerceLog ('[X] {0}上传失败：{1}' -f $target.label, $_.Exception.Message)
        }
    }
    try { $state | ConvertTo-Json -Depth 3 | Out-File -LiteralPath $EcomStateFile -Encoding utf8 } catch {}
}

try { Invoke-EcommercePickup } catch { Write-EcommerceLog ('[X] 旺店通取件异常：' + $_.Exception.Message) }
