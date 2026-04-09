import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from app.enmus.exception import NoteErrorEnum
from app.exceptions.note import NoteError


BILIBILI_VIDEO_HOSTS = {"www.bilibili.com", "bilibili.com", "m.bilibili.com"}
BILIBILI_SPACE_HOSTS = {"space.bilibili.com"}
BILIBILI_SHORT_HOSTS = {"b23.tv", "www.b23.tv"}


class BilibiliUrlKind(str, Enum):
    VIDEO = "video"
    LIST = "list"
    LIST_SERIES = "list_series"
    LIST_SEASON = "list_season"
    SERIES_DETAIL = "series_detail"
    COLLECTION_DETAIL = "collection_detail"
    SHORT = "short"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class NormalizedBilibiliUrl:
    original_url: str
    resolved_url: str
    normalized_url: str
    kind: BilibiliUrlKind
    video_id: Optional[str] = None
    page: Optional[int] = None
    needs_episode_selection: bool = False
    is_collection: bool = False
    was_short_link: bool = False


def _normalize_host(url: str) -> str:
    return urlparse(url).netloc.split("@")[-1].split(":")[0].lower()


def _normalize_bilibili_video_id(raw_value: str) -> Optional[str]:
    value = (raw_value or "").strip()
    if not value:
        return None
    if re.fullmatch(r"(?i)BV[0-9A-Za-z]{10}", value):
        return f"BV{value[2:]}"
    if re.fullmatch(r"(?i)av\d+", value):
        digits = re.sub(r"(?i)^av", "", value)
        return f"av{digits}"
    return None


def _extract_bilibili_video_id_from_text(text: str) -> Optional[str]:
    match = re.search(r"(?i)\b(BV[0-9A-Za-z]{10}|av\d+)\b", text or "")
    if not match:
        return None
    return _normalize_bilibili_video_id(match.group(1))


def _extract_positive_int(query: str, key: str) -> Optional[int]:
    values = parse_qs(query).get(key) or []
    if not values:
        return None
    try:
        number = int(values[0])
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _build_bilibili_video_url(video_id: str, page: Optional[int] = None) -> str:
    base_url = f"https://www.bilibili.com/video/{video_id}"
    params = {}
    if page and page > 0:
        params["p"] = str(page)
    return f"{base_url}?{urlencode(params)}" if params else base_url


def _classify_bilibili_collection_kind(url: str) -> BilibiliUrlKind:
    parsed = urlparse(url)
    host = _normalize_host(url)
    if host not in BILIBILI_SPACE_HOSTS:
        return BilibiliUrlKind.UNKNOWN

    path = parsed.path.rstrip("/")
    if re.search(r"/lists/\d+$", path):
        list_type = (parse_qs(parsed.query).get("type") or [""])[0].strip().lower()
        if list_type == "series":
            return BilibiliUrlKind.LIST_SERIES
        if list_type == "season":
            return BilibiliUrlKind.LIST_SEASON
        return BilibiliUrlKind.LIST
    if path.endswith("/channel/seriesdetail"):
        return BilibiliUrlKind.SERIES_DETAIL
    if path.endswith("/channel/collectiondetail"):
        return BilibiliUrlKind.COLLECTION_DETAIL
    return BilibiliUrlKind.UNKNOWN


def _extract_collection_video_id(url: str) -> Optional[str]:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    for key in ("bvid", "BVID"):
        for value in query.get(key) or []:
            normalized = _normalize_bilibili_video_id(value)
            if normalized:
                return normalized

    for key in ("avid", "aid", "business_id"):
        for value in query.get(key) or []:
            digits = re.sub(r"\D+", "", value or "")
            if digits:
                return f"av{digits}"

    return _extract_bilibili_video_id_from_text(url)


def is_supported_bilibili_url(url: str) -> bool:
    normalized = parse_bilibili_url(url)
    return normalized.kind != BilibiliUrlKind.UNKNOWN


def parse_bilibili_url(url: str) -> NormalizedBilibiliUrl:
    original_url = (url or "").strip()
    if not original_url:
        return NormalizedBilibiliUrl(
            original_url="",
            resolved_url="",
            normalized_url="",
            kind=BilibiliUrlKind.UNKNOWN,
        )

    resolved_url = original_url
    was_short_link = False
    host = _normalize_host(original_url)

    if host in BILIBILI_SHORT_HOSTS:
        was_short_link = True
        resolved_url = resolve_bilibili_short_url(original_url) or original_url
        host = _normalize_host(resolved_url)

    parsed = urlparse(resolved_url)
    page = _extract_positive_int(parsed.query, "p")

    collection_kind = _classify_bilibili_collection_kind(resolved_url)
    if collection_kind != BilibiliUrlKind.UNKNOWN:
        video_id = _extract_collection_video_id(resolved_url)
        normalized_url = _build_bilibili_video_url(video_id, page) if video_id else resolved_url
        return NormalizedBilibiliUrl(
            original_url=original_url,
            resolved_url=resolved_url,
            normalized_url=normalized_url,
            kind=collection_kind,
            video_id=video_id,
            page=page,
            needs_episode_selection=video_id is None,
            is_collection=True,
            was_short_link=was_short_link,
        )

    video_id = _extract_bilibili_video_id_from_text(resolved_url)
    if host in BILIBILI_VIDEO_HOSTS and (parsed.path.lower().startswith("/video/") or video_id):
        normalized_url = _build_bilibili_video_url(video_id, page) if video_id else resolved_url
        return NormalizedBilibiliUrl(
            original_url=original_url,
            resolved_url=resolved_url,
            normalized_url=normalized_url,
            kind=BilibiliUrlKind.VIDEO if not was_short_link else BilibiliUrlKind.SHORT,
            video_id=video_id,
            page=page,
            needs_episode_selection=False,
            is_collection=False,
            was_short_link=was_short_link,
        )

    if was_short_link:
        return NormalizedBilibiliUrl(
            original_url=original_url,
            resolved_url=resolved_url,
            normalized_url=resolved_url,
            kind=BilibiliUrlKind.SHORT,
            video_id=video_id,
            page=page,
            needs_episode_selection=False,
            is_collection=False,
            was_short_link=True,
        )

    return NormalizedBilibiliUrl(
        original_url=original_url,
        resolved_url=resolved_url,
        normalized_url=resolved_url,
        kind=BilibiliUrlKind.UNKNOWN,
        video_id=None,
        page=page,
        needs_episode_selection=False,
        is_collection=False,
        was_short_link=False,
    )


def normalize_bilibili_url(url: str) -> NormalizedBilibiliUrl:
    normalized = parse_bilibili_url(url)
    if normalized.kind == BilibiliUrlKind.UNKNOWN:
        raise NoteError(
            code=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.code,
            message=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.message,
        )
    if normalized.needs_episode_selection:
        raise NoteError(
            code=NoteErrorEnum.BILIBILI_COLLECTION_NEEDS_EPISODE.code,
            message=NoteErrorEnum.BILIBILI_COLLECTION_NEEDS_EPISODE.message,
        )
    return normalized


def extract_video_id(url: str, platform: str) -> Optional[str]:
    """
    从视频链接中提取视频 ID

    :param url: 视频链接
    :param platform: 平台名（bilibili / youtube / douyin）
    :return: 提取到的视频 ID 或 None
    """
    if platform == "bilibili":
        normalized = parse_bilibili_url(url)
        if not normalized.video_id:
            return None
        if normalized.page and normalized.page > 0:
            return f"{normalized.video_id}_p{normalized.page}"
        return normalized.video_id

    if platform == "youtube":
        match = re.search(r"(?:v=|youtu\.be/)([0-9A-Za-z_-]{11})", url)
        return match.group(1) if match else None

    if platform == "douyin":
        match = re.search(r"/video/(\d+)", url)
        return match.group(1) if match else None

    return None


def resolve_bilibili_short_url(short_url: str) -> Optional[str]:
    """
    解析哔哩哔哩短链接以获取真实视频链接

    :param short_url: Bilibili短链接（如"https://b23.tv/xxxxxx"）
    :return: 真实的视频链接或None
    """
    for method in ("head", "get"):
        try:
            request_fn = getattr(requests, method)
            response = request_fn(short_url, allow_redirects=True, timeout=10, stream=(method == "get"))
            resolved_url = response.url
            response.close()
            if resolved_url:
                return resolved_url
        except requests.RequestException:
            continue
    return None
