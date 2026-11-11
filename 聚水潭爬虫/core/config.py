# -*- coding: utf-8 -*-
"""配置与路径解析。路径自动定位、可移植（不写死盘符）。"""
from __future__ import annotations
import os
import json

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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


def _abs(path: str, default_rel: str) -> str:
    path = (path or default_rel).strip()
    return path if os.path.isabs(path) else os.path.join(_ROOT, path)


def load_config() -> dict:
    cfg_path = os.path.join(_ROOT, "config.json")
    cfg = {}
    if os.path.exists(cfg_path):
        with open(cfg_path, "r", encoding="utf-8-sig") as f:
            cfg = json.load(f)

    out = _abs(cfg.get("output_dir"), "outputs")
    resolved = {
        "root": _ROOT,
        "output_dir": out,
        "master_file": os.path.join(out, "jst_master.csv"),
        "logs_dir": os.path.join(_ROOT, "logs"),
        "browser_profiles_dir": os.path.join(_ROOT, "browser_profiles"),
        "channels": cfg.get("channels", ["抖音", "拼多多", "淘宝", "小红书"]),
        "channel_shop_suffix": cfg.get("channel_shop_suffix", "劲风车生活"),
        "week_start": cfg.get("week_start", "monday"),
        "daily_run_time": cfg.get("daily_run_time", "09:00"),
        "template_file": _abs(cfg.get("template_file"), "每周各渠道实发_实退数据_模板.xlsx"),
        "chrome_path": cfg.get("chrome_path") or find_chrome(),
        "headless": cfg.get("headless", True),   # 抓取默认后台无头(不弹窗)；登录始终有头
        "notify": cfg.get("notify", {"toast": True, "email": False}),
        "wecom": cfg.get("wecom", {"enabled": False, "webhook": "", "send_text": True, "send_file": True}),
        "email": cfg.get("email", {}),
    }
    for d in (resolved["output_dir"], resolved["logs_dir"], resolved["browser_profiles_dir"]):
        os.makedirs(d, exist_ok=True)
    return resolved
