from pydantic import AnyUrl, BaseModel, field_validator
import re

from app.utils.url_parser import is_supported_bilibili_url

SUPPORTED_PLATFORMS = {
    "youtube": r"(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[\w\-]+",
    "douyin": "douyin",
    "kuaishou": "kuaishou"
}


def is_supported_video_url(url: str) -> bool:
    if is_supported_bilibili_url(url):
        return True

    for _, pattern in SUPPORTED_PLATFORMS.items():
        if pattern in ["douyin", "kuaishou"]:
            if pattern in url:
                return True
        else:
            if re.match(pattern, url):
                return True
    return False


class VideoRequest(BaseModel):
    url: AnyUrl
    platform: str

    @field_validator("url")
    def validate_video_url(cls, v):
        if not is_supported_video_url(str(v)):
            raise ValueError("暂不支持该视频平台或链接格式无效")
        return v
