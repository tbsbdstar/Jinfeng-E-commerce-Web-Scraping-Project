# -*- coding: utf-8 -*-
"""平台抓取基类 + 通用重试。各平台模块继承 PlatformCrawler。"""
from __future__ import annotations
import time


class LoginRequired(Exception):
    """登录态失效，需要人工登录。"""


class DataNotReady(Exception):
    """目标日数据在平台侧还没结算好（如抖音早高峰抓昨天）。
    此时不该回退老方法(会抓到残缺/错位值)，直接失败——宁可缺，也不存错。"""


class CrawlError(Exception):
    """抓取失败（页面异常/数据校验不过）。"""


def retry(fn, times=3, delays=(5, 15, 30), logger=None, what=""):
    """带退避的重试。fn 无参；抛异常则重试，全失败抛最后一次异常。"""
    last = None
    for i in range(times):
        try:
            return fn()
        except Exception as e:
            last = e
            d = delays[min(i, len(delays) - 1)]
            if logger:
                logger.warning(f"{what} 失败({i+1}/{times}): {e}" + (f"，{d}s后重试" if i < times - 1 else ""))
            if i < times - 1:
                time.sleep(d)
    raise last


class PlatformCrawler:
    name = ""
    login_url = ""

    # 取数方式：'api'=只用接口 | 'page'=只用老方法(页面/日历/OCR) | 'auto'=接口优先,失败回退老方法。
    # 由执行器按 config.json 的 crawl_method 注入(见 runner)。默认 auto。
    crawl_method = "auto"

    def __init__(self, page, logger):
        self.page = page
        self.log = logger

    # 子类必须实现
    def is_logged_in(self) -> bool:
        raise NotImplementedError

    def do_login(self, account: str, password: str):
        raise NotImplementedError

    def crawl(self, date) -> dict:
        """返回 {'sales': float, 'refund': float, 'orders': int}。
        按 crawl_method 分发到接口版(_crawl_api)或老方法(_crawl_page)；
        auto 模式接口失败(非登录态问题)自动回退老方法，保证接口哪天失效仍能取数。"""
        method = getattr(self, "crawl_method", "auto")
        if method == "page":
            return self._crawl_page(date)
        if method == "api":
            return self._crawl_api(date)
        # auto：接口优先
        try:
            return self._crawl_api(date)
        except (LoginRequired, DataNotReady):
            raise                       # 登录态问题/数据未就绪：回退老方法也没用(反而抓到残缺值)，直接抛
        except Exception as e:
            if self.log:
                self.log.warning(f"[{self.name}] 接口取数失败，回退老方法：{e}")
            return self._crawl_page(date)

    # 子类实现这两个：接口版 / 老方法版
    def _crawl_api(self, date) -> dict:
        raise NotImplementedError

    def _crawl_page(self, date) -> dict:
        raise NotImplementedError

    # ---------- 登录辅助 ----------
    def _type_human(self, selector: str, text: str, delay: int = 90):
        """模拟真人逐字敲键盘输入：先聚焦并清空(去掉浏览器/平台的自动填充)，再带间隔逐字敲。
        比 page.fill() 更像真人(有键盘事件)，可降低登录时触发验证码/滑块的概率。"""
        page = self.page
        try:
            el = page.locator(selector).first
            el.click(timeout=8000)          # 聚焦
            el.fill("")                     # 清掉自动填充的值，确保是"重新输入"
            page.wait_for_timeout(200)
        except Exception:
            pass
        # keyboard.type 向已聚焦的输入框逐字符发送真实按键事件（带 delay 间隔），而非一次性塞值
        page.keyboard.type(text, delay=delay)
        page.wait_for_timeout(300)

    # 登录成功后内页 URL 会含的特征（子类定义），用于可靠判断登录完成
    success_url_markers = ()

    # 点登录后被动等待登录成功：遍历所有标签页判断
    # - 若定义了 success_url_markers：任一标签 URL 含标志即成功
    # - 否则：任一标签离开登录页(不含 login/passport/sso)即成功
    def wait_login(self, timeout: int = 1800) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            try:
                for p in list(self.page.context.pages):
                    try:
                        u = (p.url or "").lower()
                    except Exception:
                        continue
                    if not u or not u.startswith("http"):
                        continue
                    if self.success_url_markers:
                        if any(m in u for m in self.success_url_markers):
                            return True
                    else:
                        if all(k not in u for k in ("login", "passport", "sso")):
                            return True
            except Exception:
                pass
            time.sleep(2)
        return False
