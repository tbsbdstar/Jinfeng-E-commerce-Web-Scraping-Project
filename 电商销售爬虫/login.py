# -*- coding: utf-8 -*-
"""
登录入口：为指定平台建立/刷新登录态（脚本自动填账号密码；验证码由人在弹出的窗口过一次）。
用法：
    python login.py            # 全部平台
    python login.py 抖音        # 只登录抖音
"""
import sys, os, json

from core import config, notify
from core.browser import BrowserSession
from platforms import get_crawler


def load_credentials(root: str) -> dict:
    p = os.path.join(root, "credentials.json")
    if not os.path.exists(p):
        print("未找到 credentials.json —— 请先双击『配置账号.bat』填写账号密码。")
        sys.exit(1)
    with open(p, encoding="utf-8-sig") as f:
        return json.load(f)


# ASCII 别名 → 平台名（登录.bat 菜单用别名传参，避免中文参数在 cmd 里的编码问题）
ALIASES = {"xhs": "小红书", "dy": "抖音", "pdd": "拼多多", "tb": "淘宝",
           "taobao": "淘宝", "douyin": "抖音", "pinduoduo": "拼多多", "xiaohongshu": "小红书"}


def main():
    c = config.load_config()
    creds = load_credentials(c["root"])
    logger = notify.get_logger(c["logs_dir"], "login")
    raw = [a for a in sys.argv[1:] if not a.startswith("-")]
    targets = [ALIASES.get(a.lower(), a) for a in raw] or c["platforms"]

    for name in targets:
        if name == "淘宝":
            logger.warning("[淘宝] 登录不走这里：请双击『淘宝Chrome.bat』在真人浏览器过滑块登录。")
            continue
        cred = creds.get(name, {}) or {}
        acc, pwd = cred.get("account", ""), cred.get("password", "")
        prof = os.path.join(c["browser_profiles_dir"], name)
        logger.info(f"[{name}] 打开浏览器，检查登录态...")
        try:
            with BrowserSession(prof, c["chrome_path"], headless=False) as page:
                crawler = get_crawler(name, page, logger)
                if crawler.is_logged_in():
                    logger.info(f"[{name}] 已是登录态，无需登录 ✅")
                    continue
                if not pwd:
                    logger.warning(f"[{name}] 未填密码，跳过（请运行 配置账号.bat）")
                    continue
                logger.info(f"[{name}] 自动填账号密码登录（若弹验证码，请在弹出的窗口里完成）...")
                crawler.do_login(acc, pwd)
                if crawler.wait_login(timeout=1800):
                    logger.info(f"[{name}] 登录成功，登录态已保存 ✅")
                else:
                    logger.error(f"[{name}] 登录未完成（超时或验证码未过），请重试。")
        except NotImplementedError as e:
            logger.warning(f"[{name}] 暂未接入：{e}")
        except Exception as e:
            logger.error(f"[{name}] 登录过程出错：{e}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
