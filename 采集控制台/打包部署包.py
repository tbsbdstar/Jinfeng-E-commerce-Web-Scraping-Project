# -*- coding: utf-8 -*-
"""把整套系统打成可移交的部署包（zip）。

包含四个工程的代码、模板、文档与安装脚本；**不含**登录档案、账号密码、
历史数据与日志——换台电脑要重新登录一次，账号密码在控制台里重填。

用法：
    python 打包部署包.py --带数据    # 换机接手时用：额外带两个主数据 CSV，历史不断档
    python 打包部署包.py            # 输出到工作区上一级
    python 打包部署包.py --out D:\\某处
"""
import argparse
import datetime as dt
import fnmatch
import os
import shutil
import sys
import zipfile

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NAME = "多平台电商经营数据自动采集与统计分析系统"
VER = "V1.1"

# --带数据 时额外纳入的文件（相对工作区根，用 / 分隔）。只带"主数据"，其余报表都能由它重新生成。
DATA_FILES = [
    "电商销售爬虫/outputs/daily_master.csv",
    "聚水潭爬虫/outputs/jst_master.csv",
]

PROJECTS = ["采集控制台", "常开页面爬取工具", "电商销售爬虫", "聚水潭爬虫"]

# 整个目录排除（体积或隐私）
SKIP_DIRS = {
    "__pycache__", "browser_profiles", "logs", "outputs", "_dev",
    ".git", ".idea", ".vscode", "node_modules",
}
# 按文件名模式排除
SKIP_FILES = [
    "*.bak", "*.pyc", "*.log", "*.tmp",
    "*_备份_*", "*备份*.csv", "*备份*.xlsx",
    "credentials.json",              # ★ 明文账号密码，绝不打包
    "settings.json",                 # 本机界面偏好
    "paths.json",                     # 各机器自行生成
    "⚠需要重新登录_*.txt",
    "淘宝API_demo.py",
    "test_*.py", "probe_*.py",
]
# 这些即使在 SKIP_DIRS 里也要带上（模板/样例）
KEEP_ANYWAY = ["credentials.example.json"]


def skipped(path, is_dir):
    base = os.path.basename(path)
    if base in KEEP_ANYWAY:
        return False
    if is_dir:
        return base in SKIP_DIRS
    return any(fnmatch.fnmatch(base, pat) for pat in SKIP_FILES)


def collect(stage):
    """把四个工程复制到 stage，返回 (文件数, 字节数, 被排除清单)。"""
    n = size = 0
    excluded = []
    for proj in PROJECTS:
        src = os.path.join(ROOT, proj)
        if not os.path.isdir(src):
            print("  ⚠ 缺工程，跳过：", proj)
            continue
        for dirpath, dirnames, filenames in os.walk(src):
            pruned = [d for d in dirnames if skipped(os.path.join(dirpath, d), True)]
            for d in pruned:
                excluded.append(os.path.relpath(os.path.join(dirpath, d), ROOT) + os.sep)
                dirnames.remove(d)
            for fn in filenames:
                s = os.path.join(dirpath, fn)
                rel = os.path.relpath(s, ROOT)
                if skipped(s, False):
                    excluded.append(rel)
                    continue
                d = os.path.join(stage, rel)
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.copy2(s, d)
                n += 1
                size += os.path.getsize(s)
    return n, size, excluded


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.dirname(ROOT) or ROOT)
    ap.add_argument("--带数据", dest="with_data", action="store_true",
                    help="额外打包两个主数据 CSV（换机接手、历史不断档时用）")
    a = ap.parse_args()

    stamp = dt.date.today().strftime("%Y%m%d")
    pkg = "%s%s_部署包_%s" % (NAME, VER, stamp)
    stage = os.path.join(a.out, pkg)
    if os.path.isdir(stage):
        shutil.rmtree(stage)
    os.makedirs(stage)

    print("正在收集四个工程……")
    n, size, excluded = collect(stage)
    print("  纳入 %d 个文件，%.1f MB" % (n, size / 1048576))

    # 历史主数据（可选）
    data_note = []
    if a.with_data:
        for rel in DATA_FILES:
            src = os.path.join(ROOT, os.path.normpath(rel))
            if not os.path.isfile(src):
                print("  ⚠ 缺主数据，跳过：", rel)
                continue
            dst = os.path.join(stage, os.path.normpath(rel))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            rows = max(0, sum(1 for _ in open(src, encoding="utf-8-sig")) - 1)
            data_note.append("%s（%d 行）" % (rel, rows))
            print("  + 历史主数据 %s（%d 行）" % (rel, rows))

    # 安装脚本与说明由同目录的模板生成（保证部署包自带）
    for fn in ("安装.bat", "install.ps1", "部署说明.md", "交接清单.md", "CLAUDE.md"):
        s = os.path.join(HERE, "部署模板", fn)
        if os.path.isfile(s):
            shutil.copy2(s, os.path.join(stage, fn))
            print("  + 根目录附带 %s" % fn)

    # 每次构建的实况清单（模板说明是通用的，这里写这一包的真实情况）
    with open(os.path.join(stage, "打包清单.txt"), "w", encoding="utf-8") as f:
        pr = lambda *x: print(*x, file=f)
        pr("%s %s" % (NAME, VER))
        pr("构建时间：%s" % dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
        pr("构建机器：%s" % os.environ.get("COMPUTERNAME", ""))
        pr("")
        pr("【本包含有】")
        pr("  · %s 四个工程的全部代码、Excel 模板、文档、安装脚本" % "、".join(PROJECTS))
        pr("  · 企业微信推送地址（两个 config.json 里，推的还是原来那个群）")
        if data_note:
            pr("  · 历史主数据（换机后接着用，报表可由它整体重生成）：")
            for x in data_note:
                pr("      - %s" % x)
        else:
            pr("  · 不含历史数据：新机器从零积累，要接历史见《部署说明.md》第五节")
        pr("")
        pr("【本包不含】")
        pr("  · 登录档案 browser_profiles —— 到新机器要重新登录一次（5 个平台）")
        pr("  · credentials.json 明文账号密码 —— 需在控制台里重填")
        pr("  · 运行日志 logs、_dev 探路脚本、各类 *_备份_* 文件")

    zip_path = os.path.join(a.out, pkg + ".zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)
    print("正在压缩……")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for dirpath, _, filenames in os.walk(stage):
            for fn in filenames:
                p = os.path.join(dirpath, fn)
                z.write(p, os.path.join(pkg, os.path.relpath(p, stage)))
    shutil.rmtree(stage)

    mb = os.path.getsize(zip_path) / 1048576
    print("\n✔ 部署包：%s（%.2f MB）" % (zip_path, mb))

    kinds = {}
    for e in excluded:
        k = ("登录档案" if "browser_profiles" in e else
             "历史数据" if "outputs" in e else
             "运行日志" if "logs" in e else
             "账号密码" if "credentials.json" in e else
             "缓存/临时" if "__pycache__" in e or e.endswith((".pyc", ".bak")) else
             "探路脚本" if "_dev" in e else "其它")
        kinds[k] = kinds.get(k, 0) + 1
    print("\n已排除（不进部署包）：")
    for k, c in sorted(kinds.items(), key=lambda x: -x[1]):
        print("  %-10s %d 项" % (k, c))


if __name__ == "__main__":
    main()
