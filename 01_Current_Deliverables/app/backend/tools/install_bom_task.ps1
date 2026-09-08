# [Change Log]
# Date: 2026-09-08 | Author: Claude / c | Version: V2.525
# ⚠ 本文件必须存为 **UTF-8 with BOM**（同 pull_reports.ps1，理由见那边注释）。
# Description: 把 BOM 采购核算表小取件机注册成【每 2 分钟】跑一次的 Windows 计划任务。
#              由 注册BOM取件.bat 拉起（纯 ASCII 外壳，中文放本文件）。与报表/银行流水取件机任务互不干扰。

$ErrorActionPreference = 'Stop'
$HERE = Split-Path -Parent $MyInvocation.MyCommand.Path
$TASK = 'BOM采购核算表取件机'
$PS1 = Join-Path $HERE 'bom_pull.ps1'
$VBS = Join-Path $HERE 'bom_pull_hidden.vbs'
$INI = Join-Path $HERE 'bom_pull.ini'

function Line { Write-Host ('=' * 52) }
Line; Write-Host "  注册「$TASK」定时任务（每 2 分钟一次）"; Write-Host "  目录：$HERE"; Line; Write-Host ''

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Host '[X] 需要管理员权限才能注册计划任务。' -ForegroundColor Red
    Write-Host '    请右键「注册BOM取件.bat」→ 以管理员身份运行。'
    exit 1
}
foreach ($f in @($PS1, $VBS)) {
    if (-not (Test-Path -LiteralPath $f)) { Write-Host "[X] 缺文件：$f" -ForegroundColor Red; exit 1 }
}
if (-not (Test-Path -LiteralPath $INI)) {
    Write-Host '[X] 缺 bom_pull.ini。请先把 bom_pull.ini.example 复制改名为 bom_pull.ini，填好 server / pull_token / dest_dir。' -ForegroundColor Red
    exit 1
}

Write-Host '[1/2] 先试跑一次，验证配置...'; Write-Host ''
& powershell -NoProfile -ExecutionPolicy Bypass -File $PS1
$rc = $LASTEXITCODE
Write-Host ''
if ($rc -ne 0) {
    Write-Host "[X] 试跑失败（退出码 $rc）。请照上面的提示改好 bom_pull.ini，再重新运行本脚本。" -ForegroundColor Red
    exit 1
}
Write-Host '[OK] 试跑通过。' -ForegroundColor Green; Write-Host ''

Write-Host "[2/2] 注册计划任务（每 2 分钟一次 · 无窗口 · 以账号 $env:USERNAME 运行）..."
$act = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('"{0}"' -f $VBS) -WorkingDirectory $HERE
$trg = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 2)
$set = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$prn = New-ScheduledTaskPrincipal -UserId ('{0}\{1}' -f $env:USERDOMAIN, $env:USERNAME) `
    -LogonType Interactive -RunLevel Limited
try {
    Register-ScheduledTask -TaskName $TASK -Action $act -Trigger $trg -Settings $set -Principal $prn -Force | Out-Null
} catch {
    Write-Host ''; Write-Host ('[X] 注册失败：' + $_.Exception.Message) -ForegroundColor Red; exit 1
}

Write-Host ''
Line
Write-Host '  [OK] 装好了。' -ForegroundColor Green
Write-Host ''
Write-Host '  · 成本会计在工作台初审通过后，两分钟内两份采购核算表就出现在 dest_dir（默认桌面\BOM报价审核\年\月）'
Write-Host '  · 【不弹窗口】——桌面上看不到任何东西才是正常的'
Write-Host "  · 要看它跑没跑，看日志：$HERE\bom_pull.log"
Write-Host ''
Write-Host "  注意：这台电脑要保持账号 $env:USERNAME 登录（可锁屏，别注销）。关机期间不取，开机后自动补上。"
Write-Host "  要停掉：任务计划程序 → 找到「$TASK」→ 禁用或删除。"
Line
