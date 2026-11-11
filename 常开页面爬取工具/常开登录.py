# -*- coding: utf-8 -*-
"""常开浏览器 · 登录工具（有头）。
启动一个带调试端口(9333)的常开 Chrome，确保打开 4 个平台的数据页标签，供人工登录并保持不关。
- 独立 profile、独立端口，跟现有电商爬虫的无头流程/淘宝9222 互不影响。
- 登录一次后保持窗口开着；页面自身的心跳会帮着维持登录态，减少过期。
双击 常开登录.bat 运行。
"""
import sys
import os
import time
import socket
import subprocess

ECOM = r"D:\claude workspace1\电商销售爬虫"
sys.path.insert(0, ECOM)
from core import config   # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PORT = 9333
PROFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser_profiles", "常开Chrome")
# 平台 -> (域名特征, 数据页URL)
TABS = {
    "淘宝":   ("taobao.com",      "https://sycm.taobao.com/portal/home.htm"),
    "抖音":   ("jinritemai.com",  "https://compass.jinritemai.com/shop"),
    "拼多多": ("pinduoduo.com",   "https://mms.pinduoduo.com/sycm/stores_data/operation"),
    "小红书": ("xiaohongshu.com", "https://ark.xiaohongshu.com/app-datacenter/overview"),
    # 聚水潭(实发/实退)：登录 erp321 主站即可，抓取时会自己开报表页
    "聚水潭": ("erp321.com",      "https://www.erp321.com/epaas"),
}


def port_open() -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        s.connect(("127.0.0.1", PORT))
        return True
    except Exception:
        return False
    finally:
        s.close()


def ensure_tabs():
    """连上常开 Chrome，确保 4 个平台各有一个标签页（缺的补开）。"""
    with sync_playwright() as pw:
        b = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        ctx = b.contexts[0]
        for name, (marker, url) in TABS.items():
            has = any(marker in (pg.url or "") for pg in ctx.pages)
            if not has:
                pg = ctx.new_page()
                try:
                    pg.goto(url, timeout=40000, wait_until="domcontentloaded")
                except Exception:
                    pass
        # 关掉多余的 about:blank / chrome://intro 空白页
        for pg in list(ctx.pages):
            u = (pg.url or "")
            if (u.startswith("about:") or u.startswith("chrome://")) and len(ctx.pages) > len(TABS):
                try:
                    pg.close()
                except Exception:
                    pass


def main():
    c = config.load_config()
    chrome = c.get("chrome_path")
    if not chrome or not os.path.exists(chrome):
        print("❌ 未找到 Chrome，可执行文件路径无效：", chrome)
        return
    if not port_open():
        os.makedirs(PROFILE, exist_ok=True)
        args = [chrome,
                f"--remote-debugging-port={PORT}",
                f"--user-data-dir={PROFILE}",
                "--no-first-run", "--no-default-browser-check",
                "--disable-blink-features=AutomationControlled",
                TABS["淘宝"][1]]
        subprocess.Popen(args, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        # 等调试端口就绪
        for _ in range(30):
            if port_open():
                break
            time.sleep(1)
        time.sleep(3)
    # 确保 4 个平台标签都开着
    try:
        ensure_tabs()
    except Exception as e:
        print("（提示：打开标签时出了点小问题，可手动在 Chrome 里打开各平台页）", e)

    print(f"✅ 常开 Chrome 已就绪（端口 {PORT}），{len(TABS)} 个平台标签页：" + " / ".join(TABS))
    print("👉 请在每个标签页里登录（淘宝过滑块；聚水潭账号 你的聚水潭账号），"
          "登录完【保持这个 Chrome 窗口不要关】。")
    print("   之后 常开抓取.bat / 常开抓取-聚水潭.bat 会连它抓数据。")
    print("   登录超时了，再双击本工具即可（会自动补开缺的标签）。")


if __name__ == "__main__":
    main()
