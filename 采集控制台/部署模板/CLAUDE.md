# 给 Claude 的部署指引

你正在一台**新电脑**上部署「多平台电商经营数据自动采集与统计分析系统 V1.1」。
这是一套已经在另一台机器上稳定运行的工具，现在移交给本机用户接手。
**你的任务只有"装好、跑通、验收"三件事，不包括改进它。**

先读这三份（同目录）：`部署说明.md`（完整版）、`交接清单.md`（一页纸）、`打包清单.txt`（这一包含什么）。

---

## 〇 硬规则（先看这条）

1. **不要修改任何 `.py` 代码**。看着"可以优化"的地方（重试等待、判空逻辑、口径计算、固定的 sleep）
   基本都是踩坑调出来的，改了会静默出错数据。部署阶段只允许改 `config.json` 里的配置项。
2. **不要代替用户登录、不要代填账号密码**。5 个平台的登录必须真人操作（淘宝有滑块、聚水潭是
   账号密码），你只负责把登录窗口打开、告诉用户登哪几个、登完帮他验证。
3. **不要调系统时钟**。这套系统靠 Windows 任务计划程序每天触发，时钟一旦被往前调过，
   任务的"上次运行时间"会被记成未来值，之后每天都会被判定为"今天已跑过"而静默跳过
   （原机器因此连续漏跑两天）。真发生了，见第五节的修法。
4. **不要动 `wecom.webhook`**，除非用户明确要换群。它是往企业微信群发消息的地址，原样保留才能
   继续推到原来那个群。
5. 中文 Windows 控制台下跑脚本一律加 `-X utf8`（`python -X utf8 xxx.py`），否则输出乱码。

---

## 一 环境前提

| 项目 | 要求 | 检查命令 |
|---|---|---|
| Python | 3.12（3.11/3.13 一般也能跑） | `python --version` |
| Chrome | 系统版 Google Chrome | `Test-Path "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"` |
| 解压位置 | 路径可含中文和空格，**不能有 `&` `%` `^`** | — |
| 目录结构 | 四个工程**必须平级** | 见下 |

```
数据采集系统\
├── 安装.bat  install.ps1  部署说明.md  交接清单.md  打包清单.txt  CLAUDE.md
├── 采集控制台\            ← 可视化主程序
├── 常开页面爬取工具\        ← 定时入口（两个定时任务调的就是这里的脚本）
├── 电商销售爬虫\           ← 淘宝 / 抖音 / 拼多多 / 小红书
└── 聚水潭爬虫\            ← 聚水潭 ERP 实发 / 实退
```

Python 没装的话，让用户自己装（`winget install Python.Python.3.12` 或官网下载，**务必勾
Add Python to PATH**），安装器的 UAC 弹窗要他点。你不要静默装。

---

## 二 部署步骤（按顺序，每步都验证）

### 1. 跑安装脚本

```
.\安装.bat
```

它做五件事：查 Python → 装依赖（playwright / openpyxl / requests / winsdk）→ 注册两个每日
定时任务 → 建桌面快捷方式 → 自检三个采集工程的定位。

**验证**：

```powershell
python -c "import playwright, openpyxl, requests; print('deps ok')"
python -X utf8 "采集控制台\paths.py"        # 应打印三个工程路径 + 全部就位
```

### 2. 确认定时任务

```powershell
foreach ($n in @("电商爬虫_常开模式","聚水潭爬虫_常开模式")) {
  $i = Get-ScheduledTaskInfo -TaskName $n
  "{0,-18} Last={1} Next={2}" -f $n,$i.LastRunTime,$i.NextRunTime
}
```

- 期望：`Last=1999/11/30`（从未运行）、`Next` 是明天或今天的 10:00 / 10:15。
- **`Last` 若是一个未来日期，任务每天都会被跳过**——按第五节重建。

### 3. 让用户登录 5 个平台（唯一的人工步骤）

双击桌面快捷方式打开控制台 → 「浏览器 / 登录」页 → 点『打开登录窗口（5 个平台标签）』，
让用户在弹出的 Chrome 里逐个登录：**淘宝、抖音、拼多多、小红书、聚水潭**。

- 登录完**那个 Chrome 窗口不能关**，它就是"常开浏览器"，每天采集靠复用它的登录状态（CDP 端口 9333）。
- 登录完回控制台点『检测全部』，五项全绿才算好。
- 可选：小红书 / 抖音 / 拼多多 支持自动填表登录，用户想配的话在控制台点『配置账号密码』。
  **淘宝有滑块、聚水潭报表站不代填，任何方式下都只能真人登录。**

### 4. 试跑一次验收

控制台点「采昨天」，或命令行（先聚水潭后电商，**不要并行**，它们共用同一个 Chrome）：

```powershell
python -X utf8 "常开页面爬取工具\常开抓取-聚水潭.py" --auto
python -X utf8 "常开页面爬取工具\常开抓取.py" --auto
```

耗时参考：聚水潭 24 组约 1.5 分钟、电商 4 平台约 2.5 分钟。

**⚠ 这一步会真的往企业微信群发消息**，动手前让用户在群里说一句"测试"。

**验收四项**：

```powershell
# ① 主数据是否多了昨天那一天
Get-Content "聚水潭爬虫\outputs\jst_master.csv" -Tail 3
Get-Content "电商销售爬虫\outputs\daily_master.csv" -Tail 3
# ② 日志有没有 ERROR
Get-ChildItem "聚水潭爬虫\logs","电商销售爬虫\logs" | Sort-Object LastWriteTime -Desc | Select -First 2
# ③ 周表 / 月表是否生成（outputs 目录）
# ④ 群里收到两条消息：聚水潭三行文案 + 周表附件；电商月度汇总表
```

聚水潭那条群消息必须是**三行**：实发 / 实退 / **净发货**。

---

## 三 数据口径速查（别改，看懂就行）

- **实发、实退**：都是从聚水潭报表直接读的原始量，非负。
- **净发货（总计）= 实发 − 实退**。某产品当周退货多于发货时**显示负数是正常的**，用户和业务方
  已确认要如实显示，不要"修正"成 0 或绝对值。
- 周表『实发数量』sheet 底部三行固定顺序：行30 实发数量 → 行31 实退数量 → 行32 净发货（总计）。
- 聚水潭实退的时间口径是**进仓时间**（只算实际退回入库的），不是登记时间。

---

## 四 日常运行方式（装完告诉用户）

- 每天 **10:00** 电商、**10:15** 聚水潭 自动采前一天数据 → 出表 → 推企业微信群，无需人工。
- 每周一会把上周整周复核一遍再出周报（约 7 分钟）。
- 前提：**电脑开机、常开 Chrome 不关**。关机错过的，开机后任务会自动补跑一次。
- 补数据、看缺口、查日志、改定时，都在控制台里操作。

---

## 五 故障定位（按现象查）

| 现象 | 原因与处理 |
|---|---|
| 到点没跑、没日志、群里没消息 | 查 `Get-ScheduledTaskInfo`。**`LastRunTime` 是未来日期**＝系统时钟被调过留下的幽灵记录，任务每天都被判"已跑过"。**改时间或禁用再启用都没用，必须删掉重建**（见下方脚本）。重建后 Last 变 `1999/11/30`，Next 恢复正常。 |
| 双击快捷方式没反应 | 改跑 `采集控制台\诊断启动.bat`，错误会留在黑窗口里 |
| 提示"找不到采集工程" | 四个目录没平级；调整目录，或在 `采集控制台\paths.json` 填绝对路径 |
| CDP 握手失败 / "常开浏览器未运行" | 常开 Chrome 挂了。控制台「浏览器 / 登录」页点『打开 / 重启浏览器』，或 `python -X utf8 "采集控制台\worker.py" --restart-browser`。重启后登录态通常还在，不用重登 |
| 某平台"已失效" | 真掉登录了，让用户重登该平台 |
| 某平台"页面未就绪" | 不是掉登录，是加载慢，稍后点『检测全部』重测 |
| 聚水潭某组抓取失败并报错 | 设计如此：宁可报错也不写 0（历史上"假 0"污染过数据）。重跑那一天即可，不要把它改成写 0 |
| 控制台输出乱码 | 加 `-X utf8` |

重建定时任务（把 `$n` 和 `-At` 换成对应的任务）：

```powershell
$n = "电商爬虫_常开模式"     # 或 聚水潭爬虫_常开模式
$old = Get-ScheduledTask -TaskName $n
$exe = $old.Actions[0].Execute; $arg = $old.Actions[0].Arguments
Unregister-ScheduledTask -TaskName $n -Confirm:$false
$a = New-ScheduledTaskAction -Execute $exe -Argument $arg
$t = New-ScheduledTaskTrigger -Daily -At "10:00"     # 聚水潭用 10:15
$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries `
       -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$p = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
       -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $n -Action $a -Trigger $t -Settings $s -Principal $p
```

---

## 六 这一包里没有什么

登录档案 `browser_profiles\`（需重新登录一次）、`credentials.json` 明文账号密码（用户自己在控制台填）、
运行日志、`_dev\` 探路脚本。历史主数据是否随包见 `打包清单.txt`；没带的话，从原机器拷这两个文件即可，
其余报表都能由它们重新生成：

- `电商销售爬虫\outputs\daily_master.csv`
- `聚水潭爬虫\outputs\jst_master.csv`
