# -*- coding: utf-8 -*-
"""聚水潭 实发/实退 抓取入口。
    python run.py            # 命令行菜单
    python run.py --auto     # 定时任务：抓昨天并渲染周表
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import runner

if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    if "--auto" in sys.argv:
        runner.run_auto()
    else:
        runner.menu(os.path.abspath(__file__))
