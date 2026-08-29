from __future__ import annotations

import html as html_lib
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0 Safari/537.36 LiveRoomMonitor/1.0"
)


class PlatformError(RuntimeError):
    """Raised when a platform cannot provide a trustworthy room snapshot."""


@dataclass(frozen=True)
class RoomReference:
    platform: str
    room_key: str
    room_url: str
    anchor_key: str = ""
    profile_url: str = ""


@dataclass(frozen=True)
class RoomSnapshot:
    status: str
    anchor_name: str = ""
    title: str = ""
    cover_url: str = ""
    room_id: str = ""
    anchor_key: str = ""
    profile_url: str = ""


def http_get(url: str, *, timeout: int = 15) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise PlatformError(f"网络请求失败：{exc}") from exc


def parse_room_reference(
    raw: str,
    platform: str,
    profile_url: str = "",
) -> RoomReference:
    platform = (platform or "").strip().lower()
    value = (raw or "").strip()
    if platform not in {"bilibili", "huya", "douyin"}:
        raise ValueError("请选择 Bilibili、虎牙或抖音")
    if not value:
        raise ValueError("请输入直播间链接或房间 ID")

    if platform == "bilibili":
        return _parse_bilibili_reference(value)
    if platform == "douyin":
        return _parse_douyin_reference(value, profile_url)
    return _parse_huya_reference(value)


def _parse_bilibili_reference(value: str) -> RoomReference:
    if value.isdigit():
        room_key = value
    else:
        parsed = urlparse(value if "://" in value else f"https://{value}")
        host = parsed.netloc.lower().split(":", 1)[0]
        if host not in {"live.bilibili.com", "www.live.bilibili.com"}:
            raise ValueError("Bilibili 请填写 live.bilibili.com 的直播间链接或数字房间 ID")
        room_key = next((part for part in parsed.path.split("/") if part), "")
        if not room_key.isdigit():
            raise ValueError("没有识别到 Bilibili 数字房间 ID")

    return RoomReference(
        platform="bilibili",
        room_key=room_key,
        room_url=f"https://live.bilibili.com/{room_key}",
    )


def _parse_huya_reference(value: str) -> RoomReference:
    if "/" not in value and "://" not in value and " " not in value:
        room_key = value.strip()
    else:
        parsed = urlparse(value if "://" in value else f"https://{value}")
        host = parsed.netloc.lower().split(":", 1)[0]
        if host not in {"huya.com", "www.huya.com", "m.huya.com"}:
            raise ValueError("虎牙请填写 huya.com 的直播间链接或房间 ID")
        room_key = next((part for part in parsed.path.split("/") if part), "")

    if not room_key or room_key in {"l", "search", "download", ""}:
        raise ValueError("没有识别到虎牙直播间 ID")
    if any(char.isspace() for char in room_key):
        raise ValueError("虎牙房间 ID 不能包含空格")

    return RoomReference(
        platform="huya",
        room_key=room_key,
        room_url=f"https://www.huya.com/{room_key}",
    )


def _parse_douyin_profile_url(value: str) -> str:
    profile = (value or "").strip()
    if not profile:
        return ""
    parsed = urlparse(profile if "://" in profile else f"https://{profile}")
    host = parsed.netloc.lower().split(":", 1)[0]
    parts = [part for part in parsed.path.split("/") if part]
    if host not in {"douyin.com", "www.douyin.com", "m.douyin.com"}:
        raise ValueError("抖音主播主页请填写 www.douyin.com/user/ 的链接")
    if len(parts) < 2 or parts[0].lower() != "user":
        raise ValueError("没有识别到抖音主播主页 ID")
    return f"https://www.douyin.com/user/{parts[1]}"


def _parse_douyin_reference(value: str, profile_url: str) -> RoomReference:
    if value.isdigit():
        room_key = value
        anchor_key = ""
    else:
        parsed = urlparse(value if "://" in value else f"https://{value}")
        host = parsed.netloc.lower().split(":", 1)[0]
        if host not in {"live.douyin.com", "www.live.douyin.com"}:
            raise ValueError("抖音检测需要填写 live.douyin.com 的直播间链接")
        room_key = next((part for part in parsed.path.split("/") if part), "")
        if not room_key or not room_key.isdigit():
            raise ValueError("没有识别到抖音直播间 ID")
        query = parse_qs(parsed.query)
        anchor_key = (query.get("anchor_id") or [""])[0]
        if anchor_key and not anchor_key.isdigit():
            anchor_key = ""

    normalized_profile = _parse_douyin_profile_url(profile_url)
    room_url = f"https://live.douyin.com/{room_key}"
    if anchor_key:
        room_url = f"{room_url}?anchor_id={anchor_key}"
    return RoomReference(
        platform="douyin",
        room_key=room_key,
        room_url=room_url,
        anchor_key=anchor_key,
        profile_url=normalized_profile,
    )


class BilibiliAdapter:
    endpoint = "https://api.live.bilibili.com/room/v1/Room/get_info"

    def fetch(self, reference: RoomReference) -> RoomSnapshot:
        if reference.platform != "bilibili":
            raise PlatformError("Bilibili 适配器收到的不是 Bilibili 房间")

        payload = json.loads(http_get(f"{self.endpoint}?room_id={reference.room_key}"))
        if payload.get("code") != 0:
            message = payload.get("message") or payload.get("msg") or "接口返回异常"
            raise PlatformError(f"Bilibili 检查失败：{message}")

        data = payload.get("data") or {}
        if not data:
            raise PlatformError("Bilibili 没有返回直播间数据")
        if data.get("live_status") is None:
            raise PlatformError("Bilibili 返回数据缺少直播状态")

        raw_status = int(data.get("live_status") or 0)
        status = {0: "offline", 1: "live", 2: "replay"}.get(raw_status, "unknown")
        if status == "unknown":
            raise PlatformError(f"Bilibili 返回了未知直播状态：{raw_status}")

        return RoomSnapshot(
            status=status,
            title=str(data.get("title") or ""),
            cover_url=str(data.get("user_cover") or data.get("keyframe") or ""),
            room_id=str(data.get("room_id") or reference.room_key),
        )


def _extract_json_assignment(html: str, variable_name: str) -> dict[str, Any]:
    marker = re.search(rf"\b{re.escape(variable_name)}\s*=", html)
    if not marker:
        return {}
    start = html.find("{", marker.end())
    if start == -1:
        return {}
    try:
        value, _ = json.JSONDecoder().raw_decode(html[start:])
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


class HuyaAdapter:
    def fetch(self, reference: RoomReference) -> RoomSnapshot:
        if reference.platform != "huya":
            raise PlatformError("虎牙适配器收到的不是虎牙房间")

        html = http_get(reference.room_url)
        room_data = _extract_json_assignment(html, "TT_ROOM_DATA")
        profile_info = _extract_json_assignment(html, "TT_PROFILE_INFO")
        raw_is_on = room_data.get("isOn")
        is_on = raw_is_on is True or str(raw_is_on).lower() in {"1", "true"}
        if not is_on:
            is_on = bool(re.search(r'class="[^"]*liveStatus-on', html))

        anchor_name = str(
            profile_info.get("nick")
            or room_data.get("nick")
            or room_data.get("gameHostName")
            or ""
        )
        room_id = str(
            room_data.get("profileRoom")
            or profile_info.get("profileRoom")
            or room_data.get("id")
            or ""
        )
        if not anchor_name and not room_id:
            raise PlatformError("虎牙直播间不存在或页面无法识别")

        return RoomSnapshot(
            status="live" if is_on else "offline",
            anchor_name=anchor_name,
            title=str(room_data.get("roomName") or room_data.get("introduction") or ""),
            cover_url=str(room_data.get("screenshot") or room_data.get("previewUrl") or ""),
            room_id=room_id or reference.room_key,
        )


def _normalise_douyin_html(source: str) -> str:
    return (
        html_lib.unescape(source)
        .replace('\\"', '"')
        .replace("\\/", "/")
    )


def _extract_douyin_scalar(source: str, key: str) -> str:
    match = re.search(
        rf'"{re.escape(key)}"\s*:\s*"([^"]*)"',
        source,
    )
    return match.group(1) if match else ""


class DouyinAdapter:
    def fetch(self, reference: RoomReference) -> RoomSnapshot:
        if reference.platform != "douyin":
            raise PlatformError("抖音适配器收到的不是抖音房间")

        source = _normalise_douyin_html(http_get(reference.room_url))
        web_room_id = _extract_douyin_scalar(source, "web_rid")
        status_match = re.search(r'"is_live_end"\s*:\s*(\d+)', source)
        if not web_room_id or not status_match:
            raise PlatformError("抖音页面未返回可识别的直播状态")

        raw_is_live_end = int(status_match.group(1))
        if raw_is_live_end not in {0, 1}:
            raise PlatformError(f"抖音返回了未知直播状态：{raw_is_live_end}")

        anchor_match = re.search(
            r'"anchor"\s*:\s*\{\s*'
            r'"id_str"\s*:\s*"([^"]*)".*?'
            r'"sec_uid"\s*:\s*"([^"]*)".*?'
            r'"nickname"\s*:\s*"([^"]*)"',
            source,
            re.DOTALL,
        )
        anchor_key = reference.anchor_key
        profile_url = reference.profile_url
        anchor_name = ""
        if anchor_match:
            anchor_key = anchor_match.group(1) or anchor_key
            sec_uid = anchor_match.group(2)
            anchor_name = anchor_match.group(3)
            if sec_uid and not profile_url:
                profile_url = f"https://www.douyin.com/user/{sec_uid}"

        room_id_match = re.search(
            r'"roomInfo"\s*:\s*\{.*?"roomId"\s*:\s*"(\d+)"',
            source,
            re.DOTALL,
        )
        title_matches = re.findall(
            r'"logOptions"\s*:\s*\{.*?"title"\s*:\s*"([^"]*)"',
            source,
            re.DOTALL,
        )
        cover_url = _extract_douyin_scalar(source, "poster")
        return RoomSnapshot(
            status="live" if raw_is_live_end == 0 else "offline",
            anchor_name=anchor_name,
            title=title_matches[-1] if title_matches else "",
            cover_url=cover_url,
            room_id=room_id_match.group(1) if room_id_match else web_room_id,
            anchor_key=anchor_key,
            profile_url=profile_url,
        )


ADAPTERS = {
    "bilibili": BilibiliAdapter(),
    "huya": HuyaAdapter(),
    "douyin": DouyinAdapter(),
}


def fetch_room(reference: RoomReference) -> RoomSnapshot:
    try:
        adapter = ADAPTERS[reference.platform]
    except KeyError as exc:
        raise PlatformError(f"不支持的平台：{reference.platform}") from exc
    return adapter.fetch(reference)
