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
    if platform == "淘宝":
        login_step = ("1. 登录：双击「淘宝Chrome.bat」，在打开的 Chrome 里过滑块登录一次"
                      "（登录态会持久保存，之后定时会自动开这个 Chrome，一般不用再管）。")
    else:
        login_step = ("1. 登录：双击「登录.bat」，按菜单选「" + platform + "」，"
                      "在弹出的浏览器里完成登录（如弹验证码/短信，在该窗口内完成）。")
    content = f"""⚠️ 需要重新登录：{platform}
========================================

发生时间：{now}
原因：{reason}
影响：{platform} {date_str} 的数据未抓取；其他平台不受影响。

【怎么处理】
{login_step}
2. 补抓这一天：双击「抓取-{platform}」文件夹里的「运行.bat」，选「2) 抓指定某天」，输入 {date_str}。
   （也可用「抓取-全部平台」的运行.bat，同样选指定某天。）
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


# ---------- 组合：一次性发出「平台失败」的通知（标记文件 + 桌面弹窗 + 企业微信群） ----------
def notify_platform_failed(output_dir: str, logger, platform: str, date_str: str, reason: str):
    logger.error(f"[{platform}] {reason}，已生成标记文件 + 发送桌面通知 + 推企业微信群。")
    write_marker(output_dir, platform, date_str, reason)
    send_toast(f"⚠️ 电商爬虫：{platform}需要处理", f"{reason}。其他平台已正常抓取，请重新登录后补跑。")
    # 推企业微信群，叫人工处理（失败不影响主流程）
    try:
        from core import config, wecom
        c = config.load_config()
        wecom.push(c.get("wecom"), logger=logger, text=(
            f"⚠️【电商抓取失败·需人工处理】\n"
            f"· 平台：{platform}\n· 日期：{date_str}\n· 原因：{reason}\n\n"
            "请到常开 Chrome 对应标签页重新登录，然后用抓取工具选 2 重抓该天即可。"))
    except Exception as e:
        logger.warning(f"失败提醒推送失败：{e}")
