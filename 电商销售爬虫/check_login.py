# -*- coding: utf-8 -*-
"""检查各平台当前登录态。用法: python check_login.py [平台名...]"""
import sys, os
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from core import config, notify
from core.browser import BrowserSession
from platforms import get_crawler

c = config.load_config()
logger = notify.get_logger(c["logs_dir"], "check")
targets = [a for a in sys.argv[1:] if not a.startswith("-")] or c["platforms"]

for name in targets:
    prof = os.path.join(c["browser_profiles_dir"], name)
    try:
        with BrowserSession(prof, c["chrome_path"], headless=True) as page:
            cr = get_crawler(name, page, logger)
            ok = cr.is_logged_in()
            pwd = page.locator('input[name="password"]').count()
            print(f"[{name}] 登录态={'已登录 ✅' if ok else '未登录 ❌'}  当前URL={page.url}  密码框={pwd}")
    except NotImplementedError as e:
        print(f"[{name}] 未接入: {e}")
    except Exception as e:
        print(f"[{name}] 检测出错: {e}")
