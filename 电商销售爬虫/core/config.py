# -*- coding: utf-8 -*-
"""
配置与路径解析。核心原则：路径自动定位、可移植。
- 程序自己找到项目根目录（本文件的上一级）。
- 除「数据存储目录 output_dir」可配外，其余目录都相对项目根自动定位。
- output_dir 支持相对(相对项目根)或绝对路径；换电脑/换盘符都不会错。
"""
from __future__ import annotations
import os
import json

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 系统 Chrome 常见位置（找得到就复用，找不到用 Playwright 自带 Chromium）
_CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]


def project_root() -> str:
    return _ROOT


def find_chrome() -> str | None:
    for p in _CHROME_CANDIDATES:
        if p and os.path.exists(p):
            return p
    return None


def load_config() -> dict:
    """读取 config.json 并把所有路径解析成绝对路径；缺省值自动补齐。"""
    cfg_path = os.path.join(_ROOT, "config.json")
    cfg = {}
    if os.path.exists(cfg_path):
        with open(cfg_path, "r", encoding="utf-8-sig") as f:
            cfg = json.load(f)

    # 数据存储目录：唯一可配项。留空/相对 -> 相对项目根；绝对 -> 原样
    out = (cfg.get("output_dir") or "outputs").strip()
    if not os.path.isabs(out):
        out = os.path.join(_ROOT, out)

    resolved = {
        "root": _ROOT,
        "output_dir": out,
        "master_file": os.path.join(out, "daily_master.csv"),
        "logs_dir": os.path.join(_ROOT, "logs"),
        "browser_profiles_dir": os.path.join(_ROOT, "browser_profiles"),
        "platforms": cfg.get("platforms", ["淘宝", "抖音", "拼多多", "小红书"]),
        "week_start": cfg.get("week_start", "monday"),
        "daily_run_time": cfg.get("daily_run_time", "09:00"),
        "chrome_path": cfg.get("chrome_path") or find_chrome(),
        "notify": cfg.get("notify", {"toast": True, "email": False}),
        "email": cfg.get("email", {}),
        # 取数方式：auto=接口优先失败回退老方法(默认) | api=只接口 | page=只老方法(页面/日历/OCR)
        "crawl_method": (cfg.get("crawl_method") or "auto").strip().lower(),
        # 企业微信群机器人推送（每天抓完把日报/周报+月表推到群）
        "wecom": cfg.get("wecom", {"enabled": False, "webhook": "", "send_text": True, "send_file": True}),
    }
    # 确保关键目录存在（首次运行自建）
    for d in (resolved["output_dir"], resolved["logs_dir"], resolved["browser_profiles_dir"]):
        os.makedirs(d, exist_ok=True)
    return resolved
