# -*- coding: utf-8 -*-
"""小红书独立执行器。双击同目录「运行.bat」使用。"""
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)   # 把项目根加入路径，以便 import 共享的 core / platforms

from core import runner

PLATFORMS = ["小红书"]
TITLE = "小红书"

if __name__ == "__main__":
    if "--auto" in sys.argv:
        runner.run_auto(PLATFORMS)          # 定时任务入口：抓昨天
    else:
        runner.menu(PLATFORMS, TITLE, os.path.abspath(__file__))
