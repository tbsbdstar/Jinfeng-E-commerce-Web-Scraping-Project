# 电商销售爬虫 · 一键安装器
# 双击「一键安装.bat」运行。幂等：可重复运行，不会重复装。
param([switch]$Silent)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

function Say($m, $c = "White") { Write-Host $m -ForegroundColor $c }

Say "========================================" Cyan
Say "   电商多平台销售爬虫 · 一键安装" Cyan
Say "========================================" Cyan

# --- 1) Python ---
$py = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
if (Test-Path $py) {
    Say "[1/6] Python 已就绪" Green
} else {
    Say "[1/6] 未检测到 Python，正在用 winget 安装 Python 3.12 ..." Yellow
    winget install --id Python.Python.3.12 -e --scope user --silent `
        --accept-package-agreements --accept-source-agreements | Out-Null
    if (-not (Test-Path $py)) { Say "Python 安装失败，请检查网络后重试。" Red; if (-not $Silent){Read-Host "回车关闭"}; exit 1 }
    Say "      Python 安装完成" Green
}

# --- 2) 依赖 ---
Say "[2/6] 安装依赖 (playwright, openpyxl) ..." Yellow
& $py -m pip install --upgrade pip -q
& $py -m pip install -r (Join-Path $root "requirements.txt") -q
Say "      依赖就绪" Green

# --- 3) 浏览器内核：优先复用系统 Chrome ---
$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
if (Test-Path $chrome) {
    Say "[3/6] 检测到系统 Chrome，将直接复用（无需下载）" Green
} else {
    Say "[3/6] 未发现 Chrome，下载 Playwright 内置浏览器（约几百 MB）..." Yellow
    & $py -m playwright install chromium
    Say "      浏览器内核就绪" Green
}

# --- 4) 选择数据存储目录（图形对话框）---
$store = Join-Path $root "outputs"
if (-not $Silent) {
    Add-Type -AssemblyName System.Windows.Forms
    $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
    $dlg.Description = "请选择【数据存储目录】：汇总表和周报将保存在这里（可选默认，也可指向任意目录）"
    $dlg.SelectedPath = $store
    if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { $store = $dlg.SelectedPath }
}
Say "[4/6] 数据存储目录：$store" Green

# --- 5) 写入 config.json 的 output_dir（只改这一项，保留其余内容与中文）---
$cfgPath = Join-Path $root "config.json"
$raw = [IO.File]::ReadAllText($cfgPath, [Text.UTF8Encoding]::new($false))
$escaped = $store.Replace('\', '\\')
$raw = [Text.RegularExpressions.Regex]::Replace($raw, '("output_dir"\s*:\s*)"[^"]*"', ('${1}"' + $escaped + '"'))
[IO.File]::WriteAllText($cfgPath, $raw, [Text.UTF8Encoding]::new($false))
Say "[5/6] 配置已写入 config.json" Green

# --- 6) 建目录 ---
foreach ($d in @($store, (Join-Path $root "logs"), (Join-Path $root "browser_profiles"))) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}
Say "[6/6] 目录已就绪" Green

Say "========================================" Cyan
Say "  安装完成！" Green
Say "  下一步：双击『登录.bat』，把各平台登录一次。" White
Say "  之后每天会自动抓取；登录过期会弹窗提醒你。" White
Say "========================================" Cyan
if (-not $Silent) { Read-Host "按回车关闭" }
