# -*- coding: utf-8 -*-
"""
登录聚水潭并把登录态保存到本地档案（下次抓取免登录）。
弹出浏览器窗口后，请在窗口里手动登录（账号密码/若需改密码或短信验证，按提示完成），
登录进去后回到这个黑色命令行窗口按一下回车。脚本不会替你输入账号密码。
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import config
from core.browser import BrowserSession

LOGIN_URL = "https://www.erp321.com/epaas"
CHECK_URL = "https://bi.erp321.com/app/daas/report/subject/adsorder/default.aspx?_c=jst-epaas&epaas=true"
ACCOUNT = "你的聚水潭账号"


def _report_ok(ctx) -> bool:
    """用一个独立标签页打开报表，检测是否已登录（不打扰主窗口的手动登录）。"""
    p = ctx.new_page()
    try:
        p.goto(CHECK_URL, wait_until="domcontentloaded", timeout=40000)
        end = time.time() + 12
        while time.time() < end:
            for fr in p.frames:
                if "adsorder/filter.aspx" in (fr.url or ""):
                    try:
                        if fr.evaluate("()=>!!document.getElementById('sku_id')"):
                            return True
                    except Exception:
                        pass
            if any(k in (p.url or "").lower() for k in ("login", "passport", "sso")):
                return False
            time.sleep(1)
        return False
    finally:
        try: p.close()
        except Exception: pass


def main():
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    c = config.load_config()
    profile = os.path.join(c["browser_profiles_dir"], "聚水潭")
    print("打开浏览器，检查登录态……")
    with BrowserSession(profile, c["chrome_path"], headless=False) as page:
        if _report_ok(page.context):
            print("✅ 已是登录态，无需重新登录。")
            time.sleep(2); return
        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass
        print("\n>>> 请在弹出的浏览器窗口里手动登录聚水潭：")
        print(f">>>   账号：{ACCOUNT}")
        print(">>>   密码你自己填；若提示『改密码』或『短信验证』，按提示走完。")
        for _ in range(10):
            try:
                input("\n>>> 登录进去后，回到本窗口按【回车】继续……")
            except EOFError:
                time.sleep(30)
            if _report_ok(page.context):
                print("✅ 登录成功，登录态已保存到本地档案。以后抓取免登录。")
                time.sleep(2); return
            print("❌ 还没检测到登录成功，请确认已登录进聚水潭首页后再按回车。")
        print("多次未成功，请重试运行本脚本。")


if __name__ == "__main__":
    main()
