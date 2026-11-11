# 聚水潭 实发/实退 周数据爬虫 · 一键安装器
# 双击「一键安装.bat」运行。幂等：可重复运行。
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
function Say($m, $c = "White") { Write-Host $m -ForegroundColor $c }

Say "========================================" Cyan
Say "   聚水潭 实发/实退 周数据爬虫 · 一键安装" Cyan
Say "========================================" Cyan

$py = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
if (Test-Path $py) {
    Say "[1/4] Python 已就绪" Green
} else {
    Say "[1/4] 未检测到 Python，正在用 winget 安装 Python 3.12 ..." Yellow
    winget install --id Python.Python.3.12 -e --scope user --silent `
        --accept-package-agreements --accept-source-agreements | Out-Null
    if (-not (Test-Path $py)) { Say "Python 安装失败，请检查网络后重试。" Red; Read-Host "回车关闭"; exit 1 }
    Say "      Python 安装完成" Green
}

Say "[2/4] 安装依赖 (playwright, openpyxl) ..." Yellow
& $py -m pip install --upgrade pip -q
& $py -m pip install -r (Join-Path $root "requirements.txt") -q
Say "      依赖就绪" Green

$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
if (Test-Path $chrome) {
    Say "[3/4] 检测到系统 Chrome，直接复用（无需下载）" Green
} else {
    Say "[3/4] 未发现 Chrome，下载 Playwright 内置浏览器 ..." Yellow
    & $py -m playwright install chromium
    Say "      浏览器内核就绪" Green
}

Say "[4/4] 安装完成！" Green
Say "" 
Say "下一步：" Cyan
Say "  1) 双击『登录.bat』，在弹出的浏览器里登录聚水潭（只需首次/登录过期时）" White
Say "  2) 双击『运行.bat』，选 1 抓昨天，或选 4 设为每天定时自动抓" White
Read-Host "回车关闭"
