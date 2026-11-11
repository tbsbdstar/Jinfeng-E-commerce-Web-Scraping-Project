# -*- coding: utf-8 -*-
"""淘宝 执行器。双击同目录「运行.bat」使用。"""
import sys, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core import runner
PLATFORMS = ["淘宝"]
TITLE = "淘宝"
if __name__ == "__main__":
    if "--auto" in sys.argv:
        runner.run_auto(PLATFORMS)
    else:
        runner.menu(PLATFORMS, TITLE, os.path.abspath(__file__))
