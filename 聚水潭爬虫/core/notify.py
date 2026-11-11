# -*- coding: utf-8 -*-
"""
通知模块：桌面弹窗(Toast) + 标记文件 + 日志。三者分工见 docs/逻辑规则.md §7。
"""
from __future__ import annotations
import os
import base64
import logging
import subprocess
import datetime as _dt

# ---------- 1) 桌面弹窗 (Windows Toast) ----------
_TOAST_PS = r'''
[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
[void][Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime]
[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime]
$xml = @"
<toast><visual><binding template="ToastGeneric">
<text>__TITLE__</text><text>__BODY__</text>
</binding></visual></toast>
"@
$doc = New-Object Windows.Data.Xml.Dom.XmlDocument
$doc.LoadXml($xml)
$toast = New-Object Windows.UI.Notifications.ToastNotification $doc
$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
'''


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def send_toast(title: str, body: str) -> bool:
    """右下角滑出通知，并留在通知中心。失败不抛异常（通知不该阻断主流程）。"""
    try:
        script = (_TOAST_PS
                  .replace("__TITLE__", _xml_escape(title))
                  .replace("__BODY__", _xml_escape(body)))
        # 用 EncodedCommand 传递，彻底避开中文编码问题
        enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                        "-EncodedCommand", enc],
                       timeout=20, capture_output=True)
        return True
    except Exception:
        return False


# ---------- 2) 标记文件 ----------
def _marker_path(output_dir: str, platform: str) -> str:
    return os.path.join(output_dir, f"⚠需要重新登录_{platform}.txt")


def write_marker(output_dir: str, platform: str, date_str: str, reason: str):
    os.makedirs(output_dir, exist_ok=True)
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    content = f"""⚠️ 需要重新登录：{platform}
========================================

发生时间：{now}
原因：{reason}
影响：{platform} {date_str} 的数据未抓取；其他平台不受影响。

【怎么处理】
1. 双击运行「登录.bat」→ 选择「{platform}」→ 完成一次验证码登录。
2. 登录成功后运行：抓取 --平台 {platform} --date {date_str}
3. 补跑成功后本文件会自动消失。
"""
    with open(_marker_path(output_dir, platform), "w", encoding="utf-8") as f:
        f.write(content)


def clear_marker(output_dir: str, platform: str):
    """补跑成功后调用，删除该平台的标记文件。"""
    p = _marker_path(output_dir, platform)
    if os.path.exists(p):
        os.remove(p)


# ---------- 3) 日志 ----------
def get_logger(logs_dir: str, name: str = "crawler") -> logging.Logger:
    os.makedirs(logs_dir, exist_ok=True)
    day = _dt.date.today().strftime("%Y%m%d")
    logfile = os.path.join(logs_dir, f"抓取_{day}.log")
    logger = logging.getLogger(name)
    if logger.handlers:            # 避免重复添加
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(logfile, encoding="utf-8"); fh.setFormatter(fmt)
    ch = logging.StreamHandler(); ch.setFormatter(fmt)
    logger.addHandler(fh); logger.addHandler(ch)
    return logger


# ---------- 组合：一次性发出「平台失败」的三重通知 ----------
def notify_platform_failed(output_dir: str, logger, platform: str, date_str: str, reason: str):
    logger.error(f"[{platform}] {reason}，已生成标记文件 + 发送桌面通知。")
    write_marker(output_dir, platform, date_str, reason)
    send_toast(f"⚠️ 电商爬虫：{platform}需要处理", f"{reason}。其他平台已正常抓取，请重新登录后补跑。")
