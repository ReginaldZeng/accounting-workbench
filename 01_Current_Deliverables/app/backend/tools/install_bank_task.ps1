# [Change Log]
# Date: 2026-09-06 | Author: Claude / c | Version: V2.493
# ⚠ 本文件必须存为 **UTF-8 with BOM**（同 pull_reports.ps1，理由见那边注释）。
# Description: 把银行流水【上行】取件机注册成【每小时】跑一次的 Windows 计划任务。
#              由 注册银行流水取件.bat 拉起（纯 ASCII 外壳，中文放本文件）。
#              与报表取件机的「财务报表取件机」任务完全独立、互不干扰：
#              任务名不同、脚本不同(bank_pull.ps1)、启动器不同(bank_pull_hidden.vbs)、周期不同(每小时 vs 每分钟)。

$ErrorActionPreference = 'Stop'
$HERE = Split-Path -Parent $MyInvocation.MyCommand.Path
$TASK = '银行流水取件机'
$PS1 = Join-Path $HERE 'bank_pull.ps1'
$VBS = Join-Path $HERE 'bank_pull_hidden.vbs'
$INI = Join-Path $HERE 'bank_pull.ini'

function Line { Write-Host ('=' * 52) }
Line; Write-Host "  注册「$TASK」定时任务（每小时一次）"; Write-Host "  目录：$HERE"; Line; Write-Host ''

# ── 前置检查 ──────────────────────────────────────────
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Host '[X] 需要管理员权限才能注册计划任务。' -ForegroundColor Red
    Write-Host '    请右键「注册银行流水取件.bat」→ 以管理员身份运行。'
    exit 1
}
foreach ($f in @($PS1, $VBS)) {
    if (-not (Test-Path -LiteralPath $f)) {
        Write-Host "[X] 缺文件：$f" -ForegroundColor Red
        Write-Host '    请确认 bank_pull.ps1 和 bank_pull_hidden.vbs 都在本目录。'
        exit 1
    }
}
if (-not (Test-Path -LiteralPath $INI)) {
    Write-Host '[X] 缺 bank_pull.ini。' -ForegroundColor Red
    Write-Host '    请先把 bank_pull.ini.example 复制改名为 bank_pull.ini，填好 server / pull_token / src_root。'
    exit 1
}

# ── 先试跑：配置不对就别注册，免得注册完悄悄天天失败 ──
Write-Host '[1/2] 先试跑一次，验证配置...'; Write-Host ''
& powershell -NoProfile -ExecutionPolicy Bypass -File $PS1
$rc = $LASTEXITCODE
Write-Host ''
if ($rc -ne 0) {
    Write-Host "[X] 试跑失败（退出码 $rc）。请照上面的提示改好 bank_pull.ini，再重新运行本脚本。" -ForegroundColor Red
    exit 1
}
Write-Host '[OK] 试跑通过。' -ForegroundColor Green; Write-Host ''

# ── 注册 ──────────────────────────────────────────────
#   Execute = wscript.exe bank_pull_hidden.vbs  ——【窗口不弹出来】（同报表取件机的理由）。
#       必须跑在当前登录账号会话（共享盘只在这个会话里存在，SYSTEM 看不见）。
#   RepetitionInterval 1 小时 ——银行流水是月度数据，每小时扫一次足够（报表是每分钟，别混）。
#   MultipleInstances IgnoreNew ——上一轮没跑完就不启动下一轮（一次可能推几十个文件，跨轮才叠得起来）。
#   ExecutionTimeLimit 20 分钟 ——一次可能推整月几十个文件（财资回单 PDF 十几 MB），给足；卡死则强杀下轮重来。
Write-Host "[2/2] 注册计划任务（每 1 小时一次 · 无窗口 · 以账号 $env:USERNAME 运行）..."
$act = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('"{0}"' -f $VBS) -WorkingDirectory $HERE
$trg = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Hours 1)
$set = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
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
Write-Host '  · 每小时自动扫共享盘的月度流水，传完就推给工作台自动解析'
Write-Host '  · 【不弹窗口】——桌面上看不到任何东西才是正常的'
Write-Host "  · 要看它跑没跑，看日志：$HERE\bank_pull.log"
Write-Host '  · 工作台「数据接入」页上「共享盘取件机」卡片能看到最近扫描时间与呼吸灯'
Write-Host '  · 想让它立刻扫（如出纳刚更新了共享盘）：在该页点「↻ 立即扫描共享盘」，下一轮即扫'
Write-Host ''
Write-Host "  注意：这台电脑要保持账号 $env:USERNAME 登录（可锁屏，别注销）。"
Write-Host "  要停掉：任务计划程序 → 找到「$TASK」→ 禁用或删除。"
Write-Host '  （报表取件机的「财务报表取件机」任务不受影响，两个各跑各的。）'
Line
