# [Change Log]
# Date: 2026-09-11 | Author: Codex | Version: V2.561
# Description: 复用报表取件机的计划任务和取件码，把旺店通共享盘 XLSX 主动推给 BP 工作台。
# Date: 2026-09-14 | Author: Claude | Version: V2.576
# Description: 增 ecommerce_recon_dir：扫公盘「4.1 电商对账」原生树，按原始相对路径推给核算工作台
#   /api/ec/workbench/pickup/*（同一 pull_token）；期间/店铺由服务器按 年月 文件夹 + 别名解析，取件机零店铺配置。

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
    if (-not $cfg.ecommerce_inventory_dir -and -not $cfg.ecommerce_shipments_dir -and -not $cfg.ecommerce_recon_dir) { return $null }
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

# ── 公盘「电商对账」上行（V2.576）：扫 4.1 电商对账 原生树，按原始相对路径推给核算工作台 ──
# 取件机保持"傻"：只搬文件、连原始路径一起送；期间(年月文件夹)与店铺(别名)全由服务器解析。
# 推送目标是核算工作台 $cfg.server（与银行/报表同一把 pull_token），不是 BP。未配 ecommerce_recon_dir 静默跳过。
function Push-EcommerceReconFile($cfg, [string]$RootFull, $File) {
    $rel = $File.FullName.Substring($RootFull.Length).TrimStart('\', '/')
    $bytes = [IO.File]::ReadAllBytes($File.FullName)
    $h = @{ 'X-Pull-Token' = $cfg.pull_token; 'X-Ec-Relpath' = [uri]::EscapeDataString($rel) }
    $r = Invoke-WebRequest -Uri ($cfg.server + '/api/ec/workbench/pickup/push') -Method POST -Headers $h `
        -ContentType 'application/octet-stream' -Body $bytes -UseBasicParsing -TimeoutSec ([int]$cfg.timeout)
    return [Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray()) | ConvertFrom-Json
}

function Invoke-EcommerceReconPickup {
    $cfg = Read-EcommerceConfig
    if (-not $cfg -or -not $cfg.ecommerce_recon_dir) { return }
    if (-not (Test-Path -LiteralPath $cfg.ecommerce_recon_dir)) {
        Write-EcommerceLog ('[X] 电商对账目录不可访问：' + $cfg.ecommerce_recon_dir)
        return
    }
    $rootFull = (Resolve-Path -LiteralPath $cfg.ecommerce_recon_dir).Path
    $stateFile = Join-Path $EcomHere 'pickup_ecommerce_recon_state.json'
    $state = @{}
    if (Test-Path -LiteralPath $stateFile) {
        try {
            (Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json).PSObject.Properties |
                ForEach-Object { $state[$_.Name] = $_.Value }
        } catch {}
    }
    $scanned = 0; $pushed = 0; $ingested = 0; $dup = 0; $unresolved = 0
    $shops = New-Object System.Collections.Generic.HashSet[string]
    $errs = @()
    $files = Get-ChildItem -LiteralPath $cfg.ecommerce_recon_dir -Recurse -File -EA SilentlyContinue |
        Where-Object { @('.xlsx', '.xls', '.zip') -contains $_.Extension.ToLowerInvariant() }
    foreach ($f in $files) {
        $scanned++
        # 刚写入 30 秒内先不碰；下一分钟再取，避免读到"另存为"尚未写完的半份文件。
        if (((Get-Date).ToUniversalTime() - $f.LastWriteTimeUtc).TotalSeconds -lt 30) { continue }
        $rel = $f.FullName.Substring($rootFull.Length).TrimStart('\', '/')
        $sig = '{0}|{1}' -f $f.Length, $f.LastWriteTimeUtc.Ticks
        if ($state[$rel] -eq $sig) { continue }
        try {
            $res = Push-EcommerceReconFile $cfg $rootFull $f
            $pushed++
            if ($res.ok) {
                $state[$rel] = $sig
                if ($res.duplicate) {
                    $dup++
                } else {
                    $ingested++
                    if ($res.shop_name) { [void]$shops.Add([string]$res.shop_name) }
                }
                Write-EcommerceLog ('[OK] {0} → {1} / {2}（{3} 行{4}）' -f $rel, $res.shop_name, $res.period, $res.rows, $(if ($res.duplicate) { '·已存在' } else { '' }))
            } else {
                $unresolved++
                Write-EcommerceLog ('[!] 未入库 {0}：{1}' -f $rel, $res.msg)
            }
        } catch {
            $errs += ($rel + '：' + $_.Exception.Message)
            Write-EcommerceLog ('[X] 推送失败 {0}：{1}' -f $rel, $_.Exception.Message)
        }
    }
    try { $state | ConvertTo-Json -Depth 3 | Out-File -LiteralPath $stateFile -Encoding utf8 } catch {}
    # 回执：写心跳（门户监控页显示"在跑·最近扫描"）+ 本轮成绩；发不出去不影响文件已推。
    try {
        $body = @{ host = $env:COMPUTERNAME; root = $cfg.ecommerce_recon_dir; scanned = $scanned;
            pushed = $pushed; ingested = $ingested; duplicate = $dup; unresolved = $unresolved;
            shops = @($shops); errors = $errs }
        Invoke-WebRequest -Uri ($cfg.server + '/api/ec/workbench/pickup/report') -Method POST `
            -Headers @{ 'X-Pull-Token' = $cfg.pull_token } -ContentType 'application/json;charset=utf-8' `
            -Body ([Text.Encoding]::UTF8.GetBytes(($body | ConvertTo-Json -Depth 4 -Compress))) `
            -UseBasicParsing -TimeoutSec ([int]$cfg.timeout) | Out-Null
    } catch {
        Write-EcommerceLog ('[!] 电商对账回执没发出去（不影响文件已推）：' + $_.Exception.Message)
    }
}

try { Invoke-EcommercePickup } catch { Write-EcommerceLog ('[X] 旺店通取件异常：' + $_.Exception.Message) }
try { Invoke-EcommerceReconPickup } catch { Write-EcommerceLog ('[X] 电商对账取件异常：' + $_.Exception.Message) }
