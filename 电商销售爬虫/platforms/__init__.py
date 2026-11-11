# -*- coding: utf-8 -*-
"""平台工厂：按名字返回对应抓取器实例。未接入的平台会给出明确提示。"""


def get_crawler(name: str, page, logger):
    if name == "抖音":
        from .douyin import DouyinCrawler
        return DouyinCrawler(page, logger)
    if name == "拼多多":
        from .pinduoduo import PinduoduoCrawler
        return PinduoduoCrawler(page, logger)
    if name == "淘宝":
        from .taobao import TaobaoCrawler
        return TaobaoCrawler(page, logger)
    if name == "小红书":
        from .xiaohongshu import XiaohongshuCrawler
        return XiaohongshuCrawler(page, logger)
    raise ValueError(f"未知平台: {name}")
