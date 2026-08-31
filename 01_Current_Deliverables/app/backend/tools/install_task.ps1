# [Change Log]
# Date: 2026-08-07 | Author: Claude / c | Version: V2.241
# ⚠ 本文件必须存为 **UTF-8 with BOM**（同 pull_reports.ps1，理由见那边注释）。
# Description: 把报表取件机注册成每分钟跑一次的 Windows 计划任务。
#              由 注册定时任务.bat 拉起——那个 bat 是纯 ASCII 的三行外壳，
#              中文一律放在本文件里：cmd.exe 按系统 ANSI 编码读 .bat，
#              UTF-8 的中文 bat 会整片乱码、每行都被当成命令执行（实际踩过）。

$ErrorActionPreference = 'Stop'
$HERE = Split-Path -Parent $MyInvocation.MyCommand.Path
$TASK = '财务报表取件机'
$PS1 = Join-Path $HERE 'pull_reports.ps1'
$VBS = Join-Path $HERE 'pull_hidden.vbs'
$INI = Join-Path $HERE 'pull_reports.ini'

function Line { Write-Host ('=' * 52) }
Line; Write-Host "  注册「$TASK」定时任务"; Write-Host "  目录：$HERE"; Line; Write-Host ''

# ── 先收拾旧版留下的残局 ──────────────────────────────
# V2.241 的任务直接跑 powershell.exe，每分钟在桌面弹一个黑框。Windows 控制台默认开着
# 「快速编辑」：往框里误点一下就选中文本、**进程当场冻住**，要按 Esc 才继续。
# 于是冻住的窗口一分钟叠一个，同步也悄悄停了（那台电脑上实际发生了）。
# 这里把它们收掉——只杀命令行里带 pull_reports.ps1 的，不碰别的 PowerShell。
$stale = @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -EA SilentlyContinue |
    Where-Object { $_.CommandLine -like '*pull_reports.ps1*' -and $_.ProcessId -ne $PID })
if ($stale.Count) {
    Write-Host ("[清理] 发现 {0} 个卡住的旧取件窗口，正在关闭..." -f $stale.Count) -ForegroundColor Yellow
    $stale | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }
    Write-Host ''
}

# ── 前置检查 ──────────────────────────────────────────
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Host '[X] 需要管理员权限才能注册计划任务。' -ForegroundColor Red
    Write-Host '    请右键「注册定时任务.bat」→ 以管理员身份运行。'
    exit 1
}
foreach ($f in @($PS1, $VBS)) {
    if (-not (Test-Path -LiteralPath $f)) {
        Write-Host "[X] 缺文件：$f" -ForegroundColor Red
        Write-Host '    解压的可能是旧包。请重新解压最新的「报表取件机」压缩包（选覆盖）。'
        exit 1
    }
}
if (-not (Test-Path -LiteralPath $INI)) {
    Write-Host '[X] 缺 pull_reports.ini。' -ForegroundColor Red
    Write-Host '    请先把 pull_reports.ini.example 复制一份改名为 pull_reports.ini，并填好取件码。'
    exit 1
}

# ── 先试跑：配置不对就别注册，免得注册完悄悄天天失败 ──
Write-Host '[1/2] 先试跑一次，验证配置...'; Write-Host ''
& powershell -NoProfile -ExecutionPolicy Bypass -File $PS1
$rc = $LASTEXITCODE
Write-Host ''
if ($rc -ne 0) {
    Write-Host "[X] 试跑失败（退出码 $rc）。请照上面的提示改好配置，再重新运行本脚本。" -ForegroundColor Red
    exit 1
}
Write-Host '[OK] 试跑通过。' -ForegroundColor Green; Write-Host ''

# ── 注册 ──────────────────────────────────────────────
# 改用 Register-ScheduledTask（不再用 schtasks）：schtasks 只能建任务，
# 下面这三条设置它给不了，而这次翻车恰恰翻在这三条上。
#
#   Execute = wscript.exe pull_hidden.vbs   ——【窗口不再弹出来】。
#       必须跑在当前登录账号的会话里（共享盘只在这个会话里存在，SYSTEM 看不见），
#       而会话里起的东西默认带窗口。V2.241 直接起 powershell.exe，于是每分钟弹一个黑框，
#       误点一下就被"快速编辑"冻住不动。没有窗口，就没有这个失效模式。
#   MultipleInstances IgnoreNew ——上一轮还没跑完，这一轮就不启动，不会越叠越多。
#   ExecutionTimeLimit 5 分钟   ——万一真卡住（共享盘掉线时 SMB 能吊很久），
#       5 分钟后由系统强杀，下一分钟重来；不设它默认能挂 3 天。
Write-Host "[2/2] 注册计划任务（每 1 分钟一次 · 无窗口 · 以账号 $env:USERNAME 运行）..."
$act = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('"{0}"' -f $VBS) -WorkingDirectory $HERE
# 起点取整分的过去时刻 + StartWhenAvailable：重启后不用等，下一分钟就自动接上
$trg = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 1)
$set = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$prn = New-ScheduledTaskPrincipal -UserId ('{0}\{1}' -f $env:USERDOMAIN, $env:USERNAME) `
    -LogonType Interactive -RunLevel Limited
try {
    Register-ScheduledTask -TaskName $TASK -Action $act -Trigger $trg -Settings $set -Principal $prn -Force | Out-Null
} catch {
    Write-Host ''
    Write-Host ('[X] 注册失败：' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}

Write-Host ''
Line
Write-Host '  [OK] 装好了。' -ForegroundColor Green
Write-Host ''
Write-Host '  · 每分钟自动把报表取回并放进共享盘'
Write-Host '  · 【不会再弹窗口】——桌面上看不到任何东西才是正常的'
Write-Host "  · 要看它跑没跑，看日志：$HERE\pull_reports.log"
Write-Host '  · 工作台「报表导出」页上能看到同步时间，每分钟刷新'
Write-Host ''
Write-Host "  注意：这台电脑要保持账号 $env:USERNAME 登录（可锁屏，别注销）。"
Write-Host "  要停掉：任务计划程序 → 找到「$TASK」→ 禁用或删除。"
Line
