# [Change Log]
# Date: 2026-08-07 | Author: Claude / c | Version: V2.241
# ⚠ 本文件必须存为 **UTF-8 with BOM**：Windows PowerShell 5.1 没有 BOM 就按 ANSI(GBK) 读，
#   中文注释和字符串会整片乱码，引号配对随之崩掉，报出一堆莫名其妙的语法错（实际踩过）。
#   编辑后另存时务必确认编码，别存成"UTF-8 无 BOM"。
# Description: 报表取件机 · PowerShell 版——跑在【公司内网一台常开电脑】上，定时把云端工作台
#              导好的报表拉回来、放进共享盘。
#
# 与 pull_reports.py 功能完全相同，读同一个 pull_reports.ini。选一个用即可：
#   · 这台电脑没装 Python → 用本文件（PowerShell 是 Windows 自带的，零安装）
#   · 已经装了 Python     → 两个都行
#
# 为什么要这么绕：云服务器在腾讯云公网，NAS 在办公室内网，服务器够不着 NAS。
# 让服务器进内网需要 VPN／在防火墙开入口；改成【内网主动出去取】，连接方向由内到外，
# 出方向本来就通，**办公室网络一个入口都不用开**。

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.ServicePointManager]::SecurityProtocol

$HERE = Split-Path -Parent $MyInvocation.MyCommand.Path
$INI = Join-Path $HERE 'pull_reports.ini'
$LOGF = Join-Path $HERE 'pull_reports.log'

function Write-Log([string]$msg) {
    $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Write-Output $line
    try {
        # 日志封顶 2000 行——这脚本一天跑 1440 次，不封顶迟早把盘写满
        $old = @()
        if (Test-Path -LiteralPath $LOGF) { $old = @(Get-Content -LiteralPath $LOGF -Encoding UTF8 -EA SilentlyContinue | Select-Object -Last 1999) }
        ($old + $line) | Out-File -LiteralPath $LOGF -Encoding utf8
    } catch {}
}

function Read-Ini {
    if (-not (Test-Path -LiteralPath $INI)) {
        Write-Log "[X] 缺配置文件：$INI（把 pull_reports.ini.example 复制一份改名为 pull_reports.ini）"
        exit 2
    }
    $cfg = @{ timeout = 45 }        # 别往大了调：任务每分钟一轮，单次卡过 60 秒就跨轮了
    foreach ($ln in Get-Content -LiteralPath $INI -Encoding UTF8) {
        $t = $ln.Trim()
        if (-not $t -or $t.StartsWith(';') -or $t.StartsWith('#') -or $t.StartsWith('[')) { continue }
        $i = $t.IndexOf('=')
        if ($i -lt 1) { continue }
        $cfg[$t.Substring(0, $i).Trim()] = $t.Substring($i + 1).Trim()
    }
    $cfg.server = ($cfg.server -as [string]).TrimEnd('/')
    $miss = @('server', 'pull_token', 'dest_dir') | Where-Object { -not $cfg[$_] }
    if ($miss) { Write-Log ('[X] pull_reports.ini 缺项：' + ($miss -join '、')); exit 2 }
    return $cfg
}

# ⚠ 必须自己按 UTF-8 解码，不能直接用 Invoke-RestMethod：
#   Windows PowerShell 5.1 在响应头没带 charset 时会按 ISO-8859-1 解，
#   中文文件名会变成乱码，然后"文件名不合法"被服务器拒掉——症状离病因很远，极难查。
function Invoke-Api($cfg, [string]$path, [string]$query, $body) {
    $url = $cfg.server + $path
    if ($query) { $url += '?' + $query }
    $p = @{ Uri = $url; Headers = @{ 'X-Pull-Token' = $cfg.pull_token }
            UseBasicParsing = $true; TimeoutSec = [int]$cfg.timeout }
    if ($null -ne $body) {
        $p.Method = 'POST'
        $p.ContentType = 'application/json;charset=utf-8'
        $p.Body = [Text.Encoding]::UTF8.GetBytes(($body | ConvertTo-Json -Depth 5 -Compress))
    }
    $r = Invoke-WebRequest @p
    return $r.RawContentStream.ToArray()
}

function Invoke-ApiJson($cfg, [string]$path, [string]$query, $body) {
    return [Text.Encoding]::UTF8.GetString((Invoke-Api $cfg $path $query $body)) | ConvertFrom-Json
}

# ───────────────────────── 主流程 ─────────────────────────
$cfg = Read-Ini
if (-not (Test-Path -LiteralPath $cfg.dest_dir)) {
    Write-Log ('[X] 目标目录不存在：{0} —— 共享盘没连上？先在资源管理器里打开确认。' -f $cfg.dest_dir)
    exit 1
}

try {
    $lst = Invoke-ApiJson $cfg '/api/rptexport/files'
} catch {
    $code = $_.Exception.Response.StatusCode.value__
    if ($code -eq 401 -or $code -eq 403) {
        Write-Log "[X] 服务器拒绝（HTTP $code）：取件码不对，或服务器 conf.ini 没配 pull_token。"
    } else {
        Write-Log ('[X] 连不上服务器 {0}：{1}' -f $cfg.server, $_.Exception.Message)
    }
    exit 1
}
if (-not $lst.ok) { Write-Log ('[X] 服务器：' + $lst.msg); exit 1 }

# 有人在页面点了「立即同步」→ 这轮即便文件没变也回报一次，让页面时间戳立刻更新，
# 点的人马上看到"动了"。不然他看不到任何反应，只会以为坏了、再点五次。
$forced = $false; $todel = @()
try {
    $pend = Invoke-ApiJson $cfg '/api/rptexport/pending'
    $forced = [bool]$pend.pending
    $todel = @($pend.delete)
} catch {}
if ($forced) { Write-Log '收到「立即同步」请求，本轮强制回报' }

# ── 已取记录 ────────────────────────────────────────────
# 记「上次取的时候，服务器上那个文件是多大、什么时候改的」。
# ⚠ 不能只比文件大小：同一张报表重导后改了几个数字，字节数很可能一模一样，
#   只比大小会把这次更新整个漏掉——表现成"同步一切正常、共享盘上却是旧数据"，
#   是最难被发现的一类错（V2.241 自查时抓到）。故改为比对【服务器端的 大小+修改时间】。
# 也不能拿共享盘上文件的 mtime 去比：复制过去后它变成"复制时间"，每轮都会判成新的。
# ⚠⚠ 路径变量绝不能叫 $STATE：**PowerShell 变量名不区分大小写**，`$STATE` 和 `$state`
#     是同一个变量。原来写成 `$STATE = 路径` 紧接着 `$state = @{}`，第二行就把路径覆盖没了，
#     后面 `Out-File -LiteralPath $STATE` 拿到的是那个哈希表，被 PowerShell 转成字符串——
#     于是记录被写进一个叫 `System.Collections.Hashtable` 的文件，`pull_state.json` 从来没出现过。
#     它居然还能用（读写用的是同一个错名字、工作目录又固定），属于**看着正常、其实全靠巧合**，
#     换个工作目录立刻失效。业务方是从文件夹里多出个怪文件才发现的。
$StateFile = Join-Path $HERE 'pull_state.json'
$state = @{}
if (Test-Path -LiteralPath $StateFile) {
    try {
        (Get-Content -LiteralPath $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json).PSObject.Properties |
            ForEach-Object { $state[$_.Name] = $_.Value }
    } catch {}
}
# 迁移：把上一版那个错名字的记录搬回来，顺手删掉，免得目录里一直躺个怪文件
$legacyState = Join-Path $HERE 'System.Collections.Hashtable'
if ((-not $state.Count) -and (Test-Path -LiteralPath $legacyState)) {
    try {
        (Get-Content -LiteralPath $legacyState -Raw -Encoding UTF8 | ConvertFrom-Json).PSObject.Properties |
            ForEach-Object { $state[$_.Name] = $_.Value }
        Write-Log ('已把旧版错名记录（System.Collections.Hashtable，{0} 条）迁回 pull_state.json' -f $state.Count)
    } catch {}
    try { Remove-Item -LiteralPath $legacyState -Force } catch {}
}

$copied = @(); $skipped = 0; $errors = @(); $retry = @()
foreach ($f in $lst.files) {
    $rel = if ($f.rel) { $f.rel } else { $f.name }        # 老服务器只给 name，兼容
    $dst = Join-Path $cfg.dest_dir ($rel -replace '/', '\')
    $stamp = '{0}|{1}' -f $f.size, $f.mtime
    $prev = $state[$rel]
    # 目标还在 且 服务器端这个文件自上次取件以来没变过 → 跳过
    if ((Test-Path -LiteralPath $dst) -and $prev -and ($prev -eq $stamp)) { $skipped++; continue }
    try {
        $blob = Invoke-Api $cfg '/api/rptexport/download' ('name=' + [uri]::EscapeDataString($rel))
        if ($blob.Length -ne $f.size) {
            # 不是下载坏了——是**服务器上这个文件正在被重导覆盖**：/files 报的是取清单那一刻的大小，
            # 等真去下载时文件已经换成新的了。导出重写文件、取件机每分钟扫一次，这一撞是常态。
            # 所以不写盘、也不记成失败（记成失败会在页面上刷一片红字吓人），
            # 本轮跳过即可：state 没更新，下一轮自然会重取到那时的完整版本。
            $retry += $rel; continue
        }
        # 按月建子目录（2026年07月），与服务器结构保持一致
        $sub = Split-Path -Parent $dst
        if ($sub -and -not (Test-Path -LiteralPath $sub)) { New-Item -ItemType Directory -Path $sub -Force | Out-Null }
        # 先落临时文件再改名——别让同事在共享盘上看到写了一半的 Excel
        $tmp = Join-Path $sub ('.' + [guid]::NewGuid().ToString('N') + '.part')
        [IO.File]::WriteAllBytes($tmp, $blob)
        Move-Item -LiteralPath $tmp -Destination $dst -Force
        $state[$rel] = $stamp
        $copied += $rel
    } catch {
        $errors += ('{0}: {1}' -f $rel, $_.Exception.Message)
    }
}
# ── 执行删除指令 ────────────────────────────────────────
# **只删服务器明确点名的那几个**，不是"服务器没有的都删"。
# 镜像式删除的经典翻车：落地路径配错/目录临时读不到 → 以为服务器空了 → 把共享盘清光。
# 指令式没有这个失效模式。另加两道保险：文件名必须符合导出格式；解析出的绝对路径必须仍在 dest_dir 内。
$deleted = @()
$NAME_RE = '^\d{4}年\d{2}期_.{1,120}_财务报表\.xlsx$'
$destFull = (Resolve-Path -LiteralPath $cfg.dest_dir).ProviderPath.TrimEnd('\')
foreach ($rel in $todel) {
    $leaf = Split-Path -Leaf $rel
    if ($leaf -notmatch $NAME_RE) { $errors += "拒绝删除(文件名不合格式)：$rel"; continue }
    $p = Join-Path $cfg.dest_dir ($rel -replace '/', '\')
    try { $full = [IO.Path]::GetFullPath($p) } catch { $errors += "拒绝删除(路径非法)：$rel"; continue }
    if (-not $full.StartsWith($destFull, [StringComparison]::OrdinalIgnoreCase)) {
        $errors += "拒绝删除(越出目标目录)：$rel"; continue
    }
    if (-not (Test-Path -LiteralPath $full)) { $deleted += $rel; continue }   # 本来就没有＝指令已达成
    try {
        Remove-Item -LiteralPath $full -Force
        $state.Remove($rel)          # 从已取记录里划掉：将来它再出现，要能重新下载
        $deleted += $rel
        Write-Log ('   [删] ' + $rel)
    } catch { $errors += ('删除失败 {0}: {1}' -f $rel, $_.Exception.Message) }
}

try { $state | ConvertTo-Json -Depth 3 | Out-File -LiteralPath $StateFile -Encoding utf8 } catch {}

if ($copied.Count -or $errors.Count -or $deleted.Count -or $retry.Count -or $forced) {
    Write-Log ('下载 {0} 个、跳过 {1} 个、删除 {2} 个、待重取 {3} 个、失败 {4} 个 → {5}' -f $copied.Count, $skipped, $deleted.Count, $retry.Count, $errors.Count, $cfg.dest_dir)
    foreach ($n in $retry) { Write-Log ('   [等] ' + $n + '（服务器端正在更新，下轮重取）') }
    foreach ($n in $copied) { Write-Log ('   [OK] ' + $n) }
    foreach ($e in $errors) { Write-Log ('   [X]  ' + $e) }
}

# ── 扫共享盘：整体 + **按期分桶** ──────────────────────
# 页面上人真正问的是「共享盘最新的文件是什么时候的」，而**服务器看不到共享盘**，
# 只能由这台电脑报上去。文件不多（一个月 8 个），每轮扫一次开销可忽略。
#
# 按期分桶是因为页面是**按期间看**的：站在"2026年7期"这一屏，人要的是 7 期的数，
# 给全部期间合计只会被读错（业务方：「选的是 5 期只有 8 个，怎么写 16」）。
# 期间**从文件名认**（2026年07期_…），不从目录认——早期导的散件平铺在根目录上，
# 按目录认会把它们漏掉。
$newest = ''; $newestAt = ''; $total = 0
$months = @{}
function Add-Month([string]$key, [string]$field) {
    if (-not $key) { return }
    if (-not $months.ContainsKey($key)) { $months[$key] = @{ n = 0; copied = 0; skipped = 0; newest = ''; newest_at = '' } }
    $months[$key][$field] = [int]$months[$key][$field] + 1
}
function Get-MonthKey([string]$name) {
    $m = [regex]::Match((Split-Path -Leaf $name), '^(\d{4})年(\d{2})期_')
    if ($m.Success) { return ('{0}年{1}月' -f $m.Groups[1].Value, $m.Groups[2].Value) }
    return ''
}
foreach ($r in $copied) { Add-Month (Get-MonthKey $r) 'copied' }
try {
    $all = @(Get-ChildItem -LiteralPath $cfg.dest_dir -Recurse -File -Filter '*.xlsx' -EA SilentlyContinue |
             Where-Object { $_.Name -match $NAME_RE })
    $total = $all.Count
    foreach ($f in $all) {
        $k = Get-MonthKey $f.Name
        if (-not $k) { continue }
        Add-Month $k 'n'
        $ts = $f.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')
        if ($ts -gt [string]$months[$k]['newest_at']) {
            $months[$k]['newest_at'] = $ts
            $months[$k]['newest'] = $f.FullName.Substring($destFull.Length).TrimStart('\') -replace '\\', '/'
        }
    }
    # 跳过数按期反推：该期共有几个 − 本轮新搬几个（跳过的就是剩下那些已在盘上的）
    foreach ($k in @($months.Keys)) {
        $months[$k]['skipped'] = [Math]::Max(0, [int]$months[$k]['n'] - [int]$months[$k]['copied'])
    }
    $top = $all | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($top) {
        $newest = $top.FullName.Substring($destFull.Length).TrimStart('\') -replace '\\', '/'
        $newestAt = $top.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')
    }
} catch { $errors += ('扫描共享盘失败：' + $_.Exception.Message) }

# 回执：让工作台页面显示「取件机在跑 · 共享盘最新文件是 X」。
# 这台电脑关机了，页面上时间就一直停在旧值——问题当场看得见。
try {
    Invoke-Api $cfg '/api/rptexport/sync-report' $null @{
        host = $env:COMPUTERNAME; dest = $cfg.dest_dir
        copied = $copied; skipped = $skipped; deleted = $deleted; retry = $retry; errors = $errors
        newest = $newest; newest_at = $newestAt; total = $total; months = $months } | Out-Null
} catch {
    Write-Log ('[!] 回执没发出去（不影响文件已落盘）：' + $_.Exception.Message)
}
if ($errors.Count) { exit 1 } else { exit 0 }
