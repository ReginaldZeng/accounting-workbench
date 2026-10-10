# [Change Log]
# Date: 2026-09-06 | Author: Claude / c | Version: V2.487
# ⚠ 本文件必须存为 **UTF-8 with BOM**：Windows PowerShell 5.1 没 BOM 就按 GBK 读，中文全乱、引号配对崩。
# Description: 银行流水【上行】取件机 · PowerShell 版——与 bank_pull.py 功能完全相同，读同一个 bank_pull.ini。
#   取件机没装 Python 时用本文件（PowerShell 是 Windows 自带的，零安装），跟报表取件机 pull_reports.ps1 同款路子。
#   跑在【公司内网一台常开电脑】上：扫共享盘月度流水目录 → 把散件推给云端工作台 → 服务器收齐后自动解析定格
#   （财资归并/逐笔查重/重复待确认弹窗一并继承）。连接方向内网主动出去，办公室零入口。
#   三条铁律（服务器侧强制）：只推绝不删共享盘、不覆盖人工上传、保留人工确认闸。
# [Change Log]
# Date: 2026-10-10 | Author: Claude Opus 5.5 | Version: V2.891
# Description: 「立即扫描」真做到约 1 分钟内开始——计划任务改成【每分钟】拉起本脚本，但绝大多数时候只做一件极轻的事：
#   问服务器一句「有没有人点立即扫描」。没人点、也没到整点那一轮 → 立刻退出，不碰共享盘、不回报、不写任何文件。
#   真去扫共享盘只在三种时候：① 有人点了立即扫描 ② 每小时整点那一轮（和以前一样）③ 上一轮有月份还在等文件稳定。
#   另修：以前出纳刚改完文件就点立即扫描，本轮只会记一句「内容仍在变」然后跳过，要到下一轮才推——
#   现在有人点了就隔 5 秒再看一眼，文件没在变就当轮直接推。
#   手工验证/想马上整盘扫一次：加 -Full。

param([switch]$Full)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.ServicePointManager]::SecurityProtocol

$HERE = Split-Path -Parent $MyInvocation.MyCommand.Path
$INI = Join-Path $HERE 'bank_pull.ini'
$LOGF = Join-Path $HERE 'bank_pull.log'
$STATEF = Join-Path $HERE 'bank_pull_state.json'
$ERRF = Join-Path $HERE 'bank_pull_lasterr.txt'

function Write-Log([string]$msg) {
    $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Write-Output $line
    try {
        $old = @()
        if (Test-Path -LiteralPath $LOGF) { $old = @(Get-Content -LiteralPath $LOGF -Encoding UTF8 -EA SilentlyContinue | Select-Object -Last 1999) }
        ($old + $line) | Out-File -LiteralPath $LOGF -Encoding utf8
    } catch {}
}

# 每分钟跑一次以后，「连不上服务器」「共享盘没连上」这类错会一分钟一条，一天半就把 2000 行日志冲光。
# 这类错同一个钟点、同一种只往日志里记一条（屏幕上照常每次都显示，手工跑时看得见）。$kind 用纯英文短词区分是哪种错。
function Write-LogHourly([string]$kind, [string]$msg) {
    $tag = (Get-Date -Format 'yyyyMMddHH') + '|' + $kind
    $last = ''
    try { if (Test-Path -LiteralPath $ERRF) { $last = ([string](Get-Content -LiteralPath $ERRF -Raw -EA SilentlyContinue)).Trim() } } catch {}
    if ($last -eq $tag) { Write-Output $msg; return }
    Write-Log $msg
    try { $tag | Out-File -LiteralPath $ERRF -Encoding ascii } catch {}
}

function Read-Ini {
    if (-not (Test-Path -LiteralPath $INI)) {
        Write-Log "[X] 缺配置文件：$INI（把 bank_pull.ini.example 复制改名为 bank_pull.ini）"; exit 2
    }
    $cfg = @{ year = ''; settle_minutes = 10; timeout = 60 }
    foreach ($ln0 in Get-Content -LiteralPath $INI -Encoding UTF8) {
        # 剔除所有控制字符（含 \0）：ini 若被存成 UTF-16/"Unicode"，值里会夹空字节，
        # 塞进 HTTP 请求头会报「指定的值含有无效的控制字符」——这里一律清掉，容忍任意编码。
        $ln = [regex]::Replace([string]$ln0, '[\x00-\x1F\x7F]', '')
        $t = $ln.Trim()
        if (-not $t -or $t.StartsWith(';') -or $t.StartsWith('#') -or $t.StartsWith('[')) { continue }
        $i = $t.IndexOf('=')
        if ($i -lt 1) { continue }
        $cfg[$t.Substring(0, $i).Trim()] = $t.Substring($i + 1).Trim()
    }
    $cfg.server = ($cfg.server -as [string]).TrimEnd('/')
    $miss = @('server', 'pull_token', 'src_root') | Where-Object { -not $cfg[$_] }
    if ($miss) { Write-Log ('[X] bank_pull.ini 缺项：' + ($miss -join '、')); exit 2 }
    $cfg.settle_minutes = [int]$cfg.settle_minutes
    $cfg.timeout = [int]$cfg.timeout
    return $cfg
}

# 发 GET/JSON-POST，返回解析后的对象。UTF-8 自解码（PS5.1 响应无 charset 时会乱码）。
function Invoke-ApiJson($cfg, [string]$path, $body) {
    $url = $cfg.server + $path
    $p = @{ Uri = $url; Headers = @{ 'X-Pull-Token' = $cfg.pull_token }; UseBasicParsing = $true; TimeoutSec = $cfg.timeout }
    if ($null -ne $body) {
        $p.Method = 'POST'; $p.ContentType = 'application/json;charset=utf-8'
        $p.Body = [Text.Encoding]::UTF8.GetBytes(($body | ConvertTo-Json -Depth 6 -Compress))
    }
    $r = Invoke-WebRequest @p
    return [Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray()) | ConvertFrom-Json
}

# 推一个文件（二进制体 + 期间/相对名走请求头）。相对名 URL 编码（含中文，请求头传不了非 ASCII）。
function Push-File($cfg, [string]$period, [string]$rel, [byte[]]$bytes) {
    $url = $cfg.server + '/api/bank-pull/push'
    $h = @{ 'X-Pull-Token' = $cfg.pull_token; 'X-Bank-Period' = $period; 'X-Bank-Relname' = [uri]::EscapeDataString($rel) }
    $r = Invoke-WebRequest -Uri $url -Method POST -Headers $h -ContentType 'application/octet-stream' -Body $bytes -UseBasicParsing -TimeoutSec $cfg.timeout
    return [Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray()) | ConvertFrom-Json
}

function Get-MonthKey([string]$name, $defaultYear) {
    $m = [regex]::Match($name, '^(\d{4})年.*?(\d{1,2})月')
    if ($m.Success) { return ('{0:D4}-{1:D2}' -f [int]$m.Groups[1].Value, [int]$m.Groups[2].Value) }
    $m = [regex]::Match($name, '^(\d{1,2})月')
    if ($m.Success -and $defaultYear) { return ('{0}-{1:D2}' -f $defaultYear, [int]$m.Groups[1].Value) }
    $m = [regex]::Match($name, '(\d{4})[-_]?(\d{2})')
    if ($m.Success) { return ('{0}-{1}' -f $m.Groups[1].Value, $m.Groups[2].Value) }
    return ''
}

# 目录签名：{相对路径 = "size|mtime"}，判"齐没齐"和增量都靠它。
function Get-DirSig([string]$dir) {
    $sig = @{}
    Get-ChildItem -LiteralPath $dir -Recurse -File -EA SilentlyContinue | ForEach-Object {
        if ($_.Name.EndsWith('.part')) { return }
        $rel = $_.FullName.Substring($dir.Length).TrimStart('\') -replace '\\', '/'
        $sig[$rel] = ('{0}|{1}' -f $_.Length, [int][double]::Parse((Get-Date $_.LastWriteTimeUtc -UFormat %s)))
    }
    return $sig
}

function Test-SameSig($a, $b) {
    if (-not $a -or -not $b -or $a.Count -ne $b.Count) { return $false }
    foreach ($k in $b.Keys) { if ($a[$k] -ne $b[$k]) { return $false } }
    return $true
}

# 回报统一带上：脚本版本 v=2（服务器据此知道这台是「每分钟来问」的新版）、这轮是不是有人点的、认领的是哪条留话。
# 服务器只清编号对得上的那条留话——扫到一半又有人点的那条不会被这次回报顺手清掉。
function Send-Report($cfg, $payload) {
    $payload['v'] = 2
    $payload['forced'] = [bool]$script:forced
    $payload['want_id'] = [string]$script:wantId
    try { [void](Invoke-ApiJson $cfg '/api/bank-pull/report' $payload) }
    catch { Write-Log ('[!] 回执没发出去（不影响已推文件）：' + $_.Exception.Message) }
}

# ───────────────────────── 主流程 ─────────────────────────
$cfg = Read-Ini
$defaultYear = if ($cfg.year -match '^\d{4}$') { [int]$cfg.year } else { $null }

# ① 每分钟先问一句：页面上有没有人点「立即扫描」（响应极小）
$forced = $false; $wantId = ''
try {
    $pend = Invoke-ApiJson $cfg '/api/bank-pull/pending' $null
    $forced = [bool]$pend.pending
    $wantId = [string]$pend.id
} catch {
    $code = $_.Exception.Response.StatusCode.value__
    if ($code -eq 401 -or $code -eq 403) { Write-LogHourly 'token' "[X] 服务器拒绝（HTTP $code）：取件码不对，或服务器没配 pull_token。" }
    else { Write-LogHourly 'server' ('[X] 连不上服务器 {0}：{1}' -f $cfg.server, $_.Exception.Message) }
    exit 1
}

# 读状态（PSCustomObject → hashtable）
$state = @{}
if (Test-Path -LiteralPath $STATEF) {
    try {
        (Get-Content -LiteralPath $STATEF -Raw -Encoding UTF8 | ConvertFrom-Json).PSObject.Properties | ForEach-Object {
            $mk = $_.Name; $v = $_.Value; $h = @{}
            foreach ($pp in $v.PSObject.Properties) {
                if ($pp.Name -in @('sig', 'pushed_sig')) {
                    $inner = @{}; foreach ($x in $pp.Value.PSObject.Properties) { $inner[$x.Name] = $x.Value }
                    $h[$pp.Name] = $inner
                } else { $h[$pp.Name] = $pp.Value }
            }
            $state[$mk] = $h
        }
    } catch { $state = @{} }
}

# ② 这一分钟要不要真去扫共享盘：有人点了 / 到了整点那一轮（本钟点还没扫过）/ 上轮有月份在等文件稳定 / 手工加了 -Full。
#    都不是就立刻退出——不碰共享盘、不回报、不写文件，所以每分钟跑一次也不费事。
if (-not $state.ContainsKey('_meta')) { $state['_meta'] = @{} }
$meta = $state['_meta']
$hourKey = Get-Date -Format 'yyyyMMddHH'
$due = ([string]$meta['last_hour'] -ne $hourKey) -or [bool]$meta['recheck']
if (-not ($forced -or $due -or $Full)) {
    Write-Output '没人点「立即扫描」，也没到整点那一轮，这次不扫。（要马上整盘扫一次：加 -Full）'
    exit 0
}
if ($forced) { Write-Log '收到「立即扫描」请求，本轮不等稳定期、直接推' }

if (-not (Test-Path -LiteralPath $cfg.src_root)) {
    Write-LogHourly 'share' ('[X] 源目录不存在：{0} —— 共享盘没连上？先在资源管理器里打开确认。' -f $cfg.src_root)
    # 有人在页面上等着：把「共享盘连不上」回报回去，别让他干等。整点那轮照旧不回报（不回报久了页面才会亮红灯、发停机告警）。
    if ($forced) { Send-Report $cfg @{ scanned = 0; pushed = 0; committed = @(); waiting = @(); host = $env:COMPUTERNAME; error = ('取件机连不上共享盘：' + $cfg.src_root) } }
    exit 1
}

# 枚举月份目录
$months = @{}
Get-ChildItem -LiteralPath $cfg.src_root -Directory -EA SilentlyContinue | ForEach-Object {
    $k = Get-MonthKey $_.Name $defaultYear
    if ($k) { $months[$k] = $_.FullName }
}
if ($months.Count -eq 0) {
    Write-Log ('源目录下没有可识别的月份文件夹（如「6月流水」「2026年08月」）：' + $cfg.src_root)
    Send-Report $cfg @{ scanned = 0; pushed = 0; committed = @(); waiting = @(); host = $env:COMPUTERNAME; note = '无月份目录'; error = ('共享盘目录下没有认得出月份的文件夹：' + $cfg.src_root) }
    # 记下「这个钟点扫过了」，否则下一分钟又当成该扫、每分钟回报一次
    $meta['last_hour'] = $hourKey; $meta['recheck'] = $false
    try { $state | ConvertTo-Json -Depth 6 | Out-File -LiteralPath $STATEF -Encoding utf8 } catch {}
    exit 0
}

$pushedTotal = 0; $committed = @(); $waiting = @()
$recheck = $false   # 有月份还在等文件稳定 → 下一分钟接着看，稳了就推（不必再等一整个钟点）
foreach ($key in ($months.Keys | Sort-Object)) {
    $d = $months[$key]
    $sig = Get-DirSig $d
    if ($sig.Count -eq 0) { continue }
    if (-not $state.ContainsKey($key)) { $state[$key] = @{} }
    $st = $state[$key]
    $now = [int][double]::Parse((Get-Date -UFormat %s))

    # 稳定期：签名与上轮完全一致，且保持 settle_minutes 分钟 → 认定"齐了"
    if (-not (Test-SameSig $st['sig'] $sig)) {
        $st['sig'] = $sig; $st['seen_at'] = $now; $st['committed'] = $false
        $go = $false
        if ($forced) {
            # 人点了「立即扫描」＝他说出纳改完了，不让他等 10 分钟稳定期；但隔 5 秒再看一眼，
            # 文件还在变（正在往里拷）就不推半截的，照常走稳定期，稳了自动推。
            Start-Sleep -Seconds 5
            $sig2 = Get-DirSig $d
            if (Test-SameSig $sig $sig2) { $go = $true }
            else { $sig = $sig2; $st['sig'] = $sig2; $st['seen_at'] = [int][double]::Parse((Get-Date -UFormat %s)) }
        }
        if (-not $go) {
            $recheck = $true
            if ($forced) { $waiting += ('{0}（文件正在写入，没推半截的；写完后约 {1} 分钟内自动推，或写完再点一次）' -f $key, $cfg.settle_minutes) }
            else { $waiting += ('{0}（文件有变化，等 {1} 分钟不再变就推）' -f $key, $cfg.settle_minutes) }
            continue
        }
    } else {
        $settled = $forced -or ($st['seen_at'] -and ($now - [int]$st['seen_at']) -ge $cfg.settle_minutes * 60)
        if (-not $settled -and -not $st['committed']) {
            $left = [int]($cfg.settle_minutes * 60 - ($now - [int]$st['seen_at']))
            $recheck = $true
            $waiting += ('{0}（稳定中，约 {1} 秒后可推）' -f $key, [Math]::Max(0, $left)); continue
        }
        if ($st['committed'] -and -not $forced) { continue }
    }

    # 推该月文件（增量：与已推签名一致的跳过）
    if (-not $st['pushed_sig']) { $st['pushed_sig'] = @{} }
    $doneSig = $st['pushed_sig']
    $nPush = 0
    foreach ($rel in $sig.Keys) {
        if ($doneSig[$rel] -eq $sig[$rel]) { continue }
        $p = Join-Path $d ($rel -replace '/', '\')
        try {
            $bytes = [IO.File]::ReadAllBytes($p)
            [void](Push-File $cfg $key $rel $bytes)
            $doneSig[$rel] = $sig[$rel]; $nPush++
        } catch { Write-Log ('   [X] 推送失败 {0}/{1}：{2}' -f $key, $rel, $_.Exception.Message) }
    }
    $st['pushed_sig'] = $doneSig; $pushedTotal += $nPush

    # 收齐 → commit
    try {
        $r = Invoke-ApiJson $cfg '/api/bank-pull/commit' @{ period = $key; host = $env:COMPUTERNAME }
        if ($r.ok) {
            $st['committed'] = $true
            if ($r.skipped) { $committed += ('{0}（服务器已有人工数据，未覆盖）' -f $key) }
            else { $committed += ('{0}（并入{1}笔{2}）' -f $key, $r.'并入笔数', $(if ($r.need_dup_confirm) { '·待确认重复' } else { '' })) }
        } elseif ($r.need_switch_period) {
            $waiting += ('{0}（文件已推达，待服务器切到该期再解析）' -f $key)
        } else {
            $waiting += ('{0}（{1}）' -f $key, $r.msg)
        }
    } catch { Write-Log ('   [X] commit 失败 {0}：{1}' -f $key, $_.Exception.Message) }
}

$meta['last_hour'] = $hourKey; $meta['recheck'] = $recheck
try { $state | ConvertTo-Json -Depth 6 | Out-File -LiteralPath $STATEF -Encoding utf8 } catch {}

if ($pushedTotal -or $committed.Count -or $waiting.Count -or $forced) {
    Write-Log ('扫描 {0} 个月目录 · 本轮推送 {1} 个文件 · 提交 {2} · 等待 {3}' -f `
        $months.Count, $pushedTotal, $(if ($committed.Count) { $committed -join '，' } else { '无' }), $(if ($waiting.Count) { $waiting -join '，' } else { '无' }))
}
Send-Report $cfg @{ scanned = $months.Count; pushed = $pushedTotal; committed = $committed; waiting = $waiting; host = $env:COMPUTERNAME; src_root = $cfg.src_root }
exit 0
