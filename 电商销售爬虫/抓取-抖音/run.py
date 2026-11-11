# -*- coding: utf-8 -*-
"""抖音 执行器。双击同目录「运行.bat」使用。"""
import sys, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core import runner
PLATFORMS = ["抖音"]
TITLE = "抖音"
if __name__ == "__main__":
    if "--auto" in sys.argv:
        runner.run_auto(PLATFORMS)
    else:
        runner.menu(PLATFORMS, TITLE, os.path.abspath(__file__))
