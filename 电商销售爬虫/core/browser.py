# -*- coding: utf-8 -*-
"""
Playwright 持久化浏览器会话。
- 复用系统 Chrome（executable_path），找不到则回退到 Playwright 内置 Chromium(channel)。
- 每个平台一个独立档案目录(user_data_dir)，登录态存在里面，下次免登录。
- 独立档案与用户日常 Chrome 隔离，不冲突。
用法：
    with BrowserSession(profile_dir, chrome_path, headless=False) as page:
        page.goto(...)
"""
from __future__ import annotations
import os
from playwright.sync_api import sync_playwright


class BrowserSession:
    def __init__(self, profile_dir: str, chrome_path: str | None = None, headless: bool = False):
        self.profile_dir = profile_dir
        self.chrome_path = chrome_path
        self.headless = headless
        self._pw = None
        self._ctx = None
        self.context = None

    def __enter__(self):
        os.makedirs(self.profile_dir, exist_ok=True)
        self._pw = sync_playwright().start()
        kwargs = dict(
            user_data_dir=self.profile_dir,
            headless=self.headless,
            no_viewport=True,
            ignore_default_args=["--enable-automation"],   # 去掉“受自动化控制”标志
            args=[
                "--disable-blink-features=AutomationControlled",  # 降低被识别为自动化
                "--start-maximized",
            ],
        )
        if self.chrome_path and os.path.exists(self.chrome_path):
            kwargs["executable_path"] = self.chrome_path   # 用系统 Chrome
        else:
            kwargs["channel"] = "chrome"
        self._ctx = self._pw.chromium.launch_persistent_context(**kwargs)
        # 反检测：抹掉自动化特征，让风控把它当普通真人浏览器（滑块/验证码更易通过）
        self._ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "window.chrome=window.chrome||{runtime:{}};"
            "Object.defineProperty(navigator,'languages',{get:()=>['zh-CN','zh']});"
            "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});"
        )
        self.context = self._ctx
        page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        return page

    def __exit__(self, *exc):
        try:
            if self._ctx:
                self._ctx.close()
        finally:
            if self._pw:
                self._pw.stop()
