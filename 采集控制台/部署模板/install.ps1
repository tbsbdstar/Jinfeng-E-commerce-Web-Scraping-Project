#requires -version 5
# 多平台电商经营数据自动采集与统计分析系统 V1.1 —— 一键安装
# 由 安装.bat 调用。做四件事：查 Python → 装依赖 → 注册定时任务 → 建桌面快捷方式。
# 全程只读/新增，不删除任何既有数据。

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$console = Join-Path $root "采集控制台"
$cq = Join-Path $root "常开页面爬取工具"

function Step($n, $t) { Write-Host "`n[$n] $t" -ForegroundColor Cyan }
function OK($t) { Write-Host "    √ $t" -ForegroundColor Green }
function Warn($t) { Write-Host "    ! $t" -ForegroundColor Yellow }
function Bad($t) { Write-Host "    x $t" -ForegroundColor Red }

Write-Host "════════════════════════════════════════════════════════"
Write-Host " 多平台电商经营数据自动采集与统计分析系统 V1.1  安装程序"
Write-Host "════════════════════════════════════════════════════════"
Write-Host " 安装位置：$root"

# ---------------------------------------------------------------- 1 Python
Step 1 "查找 Python 3.12"
$cands = @(
  "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
  "$env:ProgramFiles\Python312\python.exe",
  "C:\Python312\python.exe"
)
$py = $cands | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $py) {
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) { $py = $cmd.Source }
}
if (-not $py) {
  Bad "没找到 Python。请先安装 Python 3.12（勾选 Add to PATH），再重新运行本脚本。"
  Write-Host "      下载：https://www.python.org/downloads/"
  Read-Host "`n按回车退出"; exit 1
}
$ver = & $py -c "import sys;print('.'.join(map(str,sys.version_info[:2])))"
OK "$py  (版本 $ver)"
if ($ver -ne "3.12") { Warn "开发环境为 3.12，当前 $ver，一般也能跑，遇到问题请换 3.12。" }

# ---------------------------------------------------------------- 2 依赖
Step 2 "安装依赖（playwright / openpyxl / requests / winsdk）"
& $py -m pip install --upgrade pip 2>&1 | Out-Null
foreach ($pkg in @("playwright", "openpyxl", "requests", "winsdk")) {
  Write-Host "    正在装 $pkg ..." -NoNewline
  & $py -m pip install $pkg 2>&1 | Out-Null
  if ($LASTEXITCODE -eq 0) { Write-Host " 完成" -ForegroundColor Green }
  else { Write-Host " 失败" -ForegroundColor Yellow; Warn "$pkg 安装失败，可稍后手动 pip install $pkg" }
}
Write-Host "    检测系统 Chrome ..." -NoNewline
$chrome = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
            "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe") |
          Where-Object { Test-Path $_ } | Select-Object -First 1
if ($chrome) {
  Write-Host " 已安装" -ForegroundColor Green
} else {
  Write-Host " 未安装" -ForegroundColor Yellow
  Warn "未检测到系统 Chrome，改用 Playwright 内置浏览器（体积较大，需联网下载）"
  & $py -m playwright install chromium 2>&1 | Out-Null
}

# ---------------------------------------------------------------- 3 定时任务
Step 3 "注册每日定时采集任务"
$tasks = @(
  @{ Name = "电商爬虫_常开模式";   Script = Join-Path $cq "常开抓取.py";        Time = "10:00" },
  @{ Name = "聚水潭爬虫_常开模式"; Script = Join-Path $cq "常开抓取-聚水潭.py"; Time = "10:15" }
)
foreach ($t in $tasks) {
  if (-not (Test-Path $t.Script)) { Warn "缺少 $($t.Script)，跳过 $($t.Name)"; continue }
  $exists = Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue
  if ($exists) { Warn "$($t.Name) 已存在，保留原设置（如需改时间请在控制台『定时』页改）"; continue }
  $tr = '"' + $py + '" "' + $t.Script + '" --auto'
  schtasks /create /tn $t.Name /tr $tr /sc daily /st $t.Time /f 2>&1 | Out-Null
  if ($LASTEXITCODE -eq 0) {
    # 错过时间自动补跑 + 用电池也执行
    $set = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries `
             -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)
    try { Set-ScheduledTask -TaskName $t.Name -Settings $set | Out-Null } catch {}
    OK "$($t.Name)  每天 $($t.Time)（错过自动补跑）"
  } else { Warn "$($t.Name) 注册失败，可稍后在控制台『定时』页手工建" }
}

# ---------------------------------------------------------------- 4 快捷方式
Step 4 "创建桌面快捷方式"
$bat = Join-Path $console "启动.bat"
if (Test-Path $bat) {
  $lnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "多平台电商经营数据自动采集与统计分析系统.lnk"
  $ws = New-Object -ComObject WScript.Shell
  $s = $ws.CreateShortcut($lnk)
  $s.TargetPath = $bat
  $s.WorkingDirectory = $console
  $s.Description = "多平台电商经营数据自动采集与统计分析系统 V1.1"
  $s.Save()
  OK "桌面已创建快捷方式"
} else { Bad "找不到 $bat" }

# ---------------------------------------------------------------- 5 自检
Step 5 "定位三个采集工程"
& $py (Join-Path $console "paths.py")

Write-Host "`n════════════════════════════════════════════════════════"
Write-Host " 安装完成。接下来还需要两步（只需做一次）：" -ForegroundColor Cyan
Write-Host "   1. 双击桌面快捷方式打开系统 → 「浏览器 / 登录」页 →"
Write-Host "      点『打开登录窗口』，在弹出的浏览器里逐个平台登录（共 5 个）"
Write-Host "   2. 独立无头方式要用的话，再点『配置账号密码』填各平台账号"
Write-Host "      （出于安全，部署包不含账号密码）"
Write-Host "════════════════════════════════════════════════════════"
Read-Host "`n按回车退出"
