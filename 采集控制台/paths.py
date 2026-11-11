# -*- coding: utf-8 -*-
"""三个采集工程的位置解析（供 console.py 与 worker.py 共用）。

为什么要有这个模块：控制台本身不抓数据，它调用同一套系统里另外三个工程的函数，
所以必须知道它们在哪。位置原先写死在代码里，换台电脑就失效；现在按三步找：

  1. **兄弟目录**（默认，也是部署包的形态）——与本目录同级的
     『电商销售爬虫』『聚水潭爬虫』『常开页面爬取工具』；
  2. **paths.json**——兄弟目录里找不到时读它，可填任意绝对路径；
  3. **历史默认值**——兼容原来那台开发机的 D:\\claude workspace1\\…。

每一步都不只看"目录在不在"，还要看里面有没有该工程的标志文件，
避免同名空目录把程序引到错的地方。
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
CONFIG = os.path.join(HERE, "paths.json")

# 工程键 → (默认目录名, 用于确认"这真是那个工程"的标志文件)
PROJECTS = {
    "ECOM": ("电商销售爬虫", os.path.join("core", "runner.py")),
    "JST": ("聚水潭爬虫", os.path.join("core", "jst_report.py")),
    "CQ": ("常开页面爬取工具", "常开抓取.py"),
}
LEGACY_ROOT = r"D:\claude workspace1"          # 原开发机位置，向后兼容


def _ok(key, path):
    """目录存在且含标志文件，才认为是该工程。"""
    if not path or not os.path.isdir(path):
        return False
    return os.path.isfile(os.path.join(path, PROJECTS[key][1]))


def resolve():
    """返回 ({键: 绝对路径}, {键: 来源说明})，找不到的键不出现在第一个字典里。"""
    found, how = {}, {}
    # 1) 兄弟目录
    for k, (name, _) in PROJECTS.items():
        p = os.path.join(PARENT, name)
        if _ok(k, p):
            found[k], how[k] = p, "同级目录"
    # 2) paths.json
    if len(found) < len(PROJECTS) and os.path.isfile(CONFIG):
        try:
            with open(CONFIG, encoding="utf-8-sig") as f:
                data = json.load(f)
        except Exception:
            data = {}
        for k in PROJECTS:
            if k in found:
                continue
            p = (data.get(k) or "").strip()
            if _ok(k, p):
                found[k], how[k] = os.path.abspath(p), "paths.json"
    # 3) 历史默认值
    for k, (name, _) in PROJECTS.items():
        if k in found:
            continue
        p = os.path.join(LEGACY_ROOT, name)
        if _ok(k, p):
            found[k], how[k] = p, "历史默认位置"
    return found, how


_found, _how = resolve()
ECOM = _found.get("ECOM", "")
JST = _found.get("JST", "")
CQ = _found.get("CQ", "")
MISSING = [PROJECTS[k][0] for k in PROJECTS if k not in _found]
SOURCE = dict(_how)


def describe():
    """一段可直接显示给用户的定位结果说明。"""
    rows = []
    for k, (name, _) in PROJECTS.items():
        p = _found.get(k)
        rows.append("  %-12s %s%s" % (name, p or "（未找到）",
                                      "    ← " + _how[k] if p else ""))
    return "\n".join(rows)


def hint():
    """缺工程时给出的处置提示。"""
    return (
        "缺少以下采集工程，控制台无法执行采集：\n\n    "
        + "、".join(MISSING)
        + "\n\n请任选一种方式解决：\n"
        "  1) 把这些工程目录放到与『采集控制台』同级的位置（推荐，部署包就是这样）；\n"
        "  2) 编辑本目录下的 paths.json，填写它们的实际路径。\n\n"
        "当前定位结果：\n" + describe()
    )


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print("采集控制台 · 工程位置定位结果")
    print(describe())
    print()
    print("全部就位" if not MISSING else hint())
