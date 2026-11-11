# -*- coding: utf-8 -*-
"""
企业微信群机器人推送：文字日报/周报 + Excel 月表文件。

配置在 config.json 的 wecom 块：
  "wecom": {"enabled": true, "webhook": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxx",
            "send_text": true, "send_file": true}

发文件需先把文件上传到企业微信拿临时 media_id(有效期3天,≤20MB)再发。
所有失败都只记日志、不抛异常（推送不该阻断抓取主流程）。
"""
from __future__ import annotations
import os, re, json
import urllib.request

SEND_TMPL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}"
UPLOAD_TMPL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/upload_media?key={key}&type=file"


def _key_of(webhook: str) -> str:
    m = re.search(r"[?&]key=([^&]+)", webhook or "")
    return m.group(1) if m else ""


def _post_json(url: str, payload: dict, logger=None) -> dict:
    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read().decode("utf-8"))
        if res.get("errcode") not in (0, None):
            logger and logger.warning(f"[企业微信] 返回异常：{res}")
        return res
    except Exception as e:
        logger and logger.warning(f"[企业微信] 请求失败：{e}")
        return {"errcode": -1, "error": str(e)}


def _upload_file(key: str, file_path: str, logger=None) -> str:
    """上传文件拿 media_id。用 multipart/form-data(requests 优先，退回 urllib 手写)。"""
    url = UPLOAD_TMPL.format(key=key)
    fname = os.path.basename(file_path)
    try:
        import requests
        with open(file_path, "rb") as f:
            files = {"media": (fname, f, "application/octet-stream")}
            r = requests.post(url, files=files, timeout=60)
        res = r.json()
    except Exception:
        # 退回：手写 multipart
        try:
            res = _upload_urllib(url, file_path, fname)
        except Exception as e:
            logger and logger.warning(f"[企业微信] 文件上传失败：{e}")
            return ""
    if res.get("errcode") not in (0, None):
        logger and logger.warning(f"[企业微信] 上传返回异常：{res}")
    return res.get("media_id", "")


def _upload_urllib(url, file_path, fname):
    boundary = "----EcomWeComBoundary7MA4YWxkTrZu0gW"
    with open(file_path, "rb") as f:
        content = f.read()
    body = b""
    body += f"--{boundary}\r\n".encode()
    body += (f'Content-Disposition: form-data; name="media"; filename="{fname}"\r\n').encode("utf-8")
    body += b"Content-Type: application/octet-stream\r\n\r\n"
    body += content + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def push(cfg: dict, text: str = None, file_path: str = None, logger=None):
    """按配置推送文字 + 文件到群。cfg = 已解析的 wecom 配置块。"""
    if not cfg or not cfg.get("enabled"):
        return
    webhook = (cfg.get("webhook") or "").strip()
    key = _key_of(webhook)
    if not key:
        logger and logger.warning("[企业微信] 未配置有效 webhook，跳过推送")
        return
    send_url = SEND_TMPL.format(key=key)
    # 1) 文字
    if text and cfg.get("send_text", True):
        _post_json(send_url, {"msgtype": "text", "text": {"content": text[:2000]}}, logger)
    # 2) 文件
    if file_path and cfg.get("send_file", True) and os.path.exists(file_path):
        media_id = _upload_file(key, file_path, logger)
        if media_id:
            _post_json(send_url, {"msgtype": "file", "file": {"media_id": media_id}}, logger)
            logger and logger.info(f"[企业微信] 已推送文件：{os.path.basename(file_path)}")


def test(cfg: dict, logger=None):
    """自测：发一条文字确认能收到。"""
    push(cfg, text="【电商销售爬虫】企业微信对接测试：能收到这条就说明配置成功 ✅", logger=logger)
