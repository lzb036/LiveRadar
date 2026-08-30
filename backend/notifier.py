from __future__ import annotations

import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


class NotificationError(RuntimeError):
    """Raised when a notification provider rejects a message."""


def mask_secret(value: str) -> str:
    value = value or ""
    if len(value) <= 8:
        return "已设置" if value else ""
    return f"{value[:4]}{'*' * min(12, len(value) - 8)}{value[-4:]}"


def send_notification(settings: dict[str, str], title: str, message: str) -> None:
    provider = settings.get("notify_provider", "none")
    if provider == "serverchan":
        _send_serverchan(settings.get("serverchan_sendkey", ""), title, message)
        return
    if provider == "wecom":
        _send_wecom(settings.get("wecom_webhook", ""), title, message)
        return
    if provider == "wxpusher":
        _send_wxpusher(settings.get("wxpusher_spt", ""), title, message)
        return
    if provider == "none":
        raise NotificationError("尚未选择微信通知方式")
    raise NotificationError(f"不支持的通知方式：{provider}")


def _send_serverchan(sendkey: str, title: str, message: str) -> None:
    if not sendkey:
        raise NotificationError("请先填写 Server酱 SendKey")
    endpoint = f"https://sctapi.ftqq.com/{quote(sendkey, safe='')}.send"
    body = urlencode({"title": title, "desp": message}).encode("utf-8")
    request = Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "LiveRoomMonitor/1.0",
        },
    )
    payload = _request_json(request)
    if payload.get("code") != 0:
        raise NotificationError(str(payload.get("message") or "Server酱发送失败"))


def _send_wecom(webhook: str, title: str, message: str) -> None:
    if not webhook:
        raise NotificationError("请先填写企业微信机器人 Webhook")
    payload = json.dumps(
        {
            "msgtype": "text",
            "text": {
                "content": "\n".join(
                    part for part in (title, message) if part
                )
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        webhook,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "LiveRoomMonitor/1.0",
        },
    )
    response = _request_json(request)
    if response.get("errcode", 0) != 0:
        raise NotificationError(str(response.get("errmsg") or "企业微信发送失败"))


def _send_wxpusher(
    raw_spts: str,
    title: str,
    message: str,
) -> None:
    spts = parse_wxpusher_spts(raw_spts)
    if not spts:
        raise NotificationError("请先填写 WxPusher SPT")
    if any(not spt.startswith("SPT_") for spt in spts):
        raise NotificationError("WxPusher SPT 格式不正确")
    if len(spts) > 10:
        raise NotificationError("WxPusher SPT 最多支持 10 个")

    content = "\n".join(part for part in (title, message) if part)
    payload_data: dict[str, Any] = {
        "content": content,
        "summary": title[:100],
        "contentType": 1,
    }
    if len(spts) == 1:
        payload_data["spt"] = spts[0]
    else:
        payload_data["sptList"] = spts
    payload = json.dumps(
        payload_data,
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        "https://wxpusher.zjiecode.com/api/send/message/simple-push",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "LiveRoomMonitor/1.0",
        },
    )
    response = _request_json(request)
    if response.get("code") != 1000:
        raise NotificationError(
            str(response.get("msg") or response.get("message") or "WxPusher 发送失败")
        )


def parse_wxpusher_spts(raw_spts: str) -> list[str]:
    return [
        spt
        for spt in re.split(r"[,，\s]+", raw_spts or "")
        if spt
    ]


def _request_json(request: Request) -> dict[str, Any]:
    try:
        with urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise NotificationError(f"通知请求失败：{exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise NotificationError("通知服务返回了无法解析的结果") from exc
    if not isinstance(payload, dict):
        raise NotificationError("通知服务返回格式异常")
    return payload
