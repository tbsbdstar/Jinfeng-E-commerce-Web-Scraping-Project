# -*- coding: utf-8 -*-
"""Windows 自带 OCR 封装（winsdk），把图片识别成文本。用于拼多多字体反爬。"""
from __future__ import annotations
import os
import asyncio
from winsdk.windows.media.ocr import OcrEngine
from winsdk.windows.globalization import Language
from winsdk.windows.graphics.imaging import BitmapDecoder
from winsdk.windows.storage import StorageFile, FileAccessMode


async def _recognize(path: str, lang: str):
    f = await StorageFile.get_file_from_path_async(os.path.abspath(path))
    stream = await f.open_async(FileAccessMode.READ)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    engine = OcrEngine.try_create_from_language(Language(lang))
    if engine is None:
        engine = OcrEngine.try_create_from_user_profile_languages()
    return await engine.recognize_async(bitmap)


def _sync(coro):
    """在独立线程的新事件循环里跑协程，避免与 Playwright 的事件循环冲突。"""
    import threading
    box = {}

    def run():
        loop = asyncio.new_event_loop()
        try:
            box["r"] = loop.run_until_complete(coro)
        finally:
            loop.close()

    t = threading.Thread(target=run)
    t.start()
    t.join()
    return box["r"]


def ocr_text(path: str, lang: str = "zh-Hans-CN") -> str:
    """返回整段识别文本。"""
    res = _sync(_recognize(path, lang))
    return res.text or ""


def ocr_lines(path: str, lang: str = "zh-Hans-CN"):
    """返回逐行文本列表（保留视觉行结构，便于按行解析）。"""
    res = _sync(_recognize(path, lang))
    return [line.text for line in res.lines]
