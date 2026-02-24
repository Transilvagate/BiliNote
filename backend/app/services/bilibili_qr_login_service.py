import logging
from typing import Dict, Optional

import httpx

from app.services.bilibili_cookie_service import BilibiliCookieService

logger = logging.getLogger(__name__)


class BilibiliQrLoginService:
    GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
    POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )
    }

    STATUS_WAITING_SCAN = "WAITING_SCAN"
    STATUS_SCANNED = "SCANNED"
    STATUS_CONFIRMED = "CONFIRMED"
    STATUS_EXPIRED = "EXPIRED"
    STATUS_FAILED = "FAILED"
    SENSITIVE_KEYS = ("SESSDATA", "bili_jct", "DedeUserID", "sid")

    def __init__(self, cookie_service: Optional[BilibiliCookieService] = None):
        self.cookie_service = cookie_service or BilibiliCookieService()

    @staticmethod
    def _request_json(
        url: str,
        method: str = "GET",
        params: Optional[Dict[str, str]] = None,
    ) -> tuple[Dict, httpx.Cookies]:
        with httpx.Client(timeout=12.0, headers=BilibiliQrLoginService.DEFAULT_HEADERS) as client:
            if method == "POST":
                response = client.post(url, params=params)
            else:
                response = client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
            return payload, response.cookies

    @staticmethod
    def _extract_cookie_from_response_cookies(response_cookies: httpx.Cookies) -> Dict[str, str]:
        cookie_map: Dict[str, str] = {}
        try:
            for key, value in response_cookies.items():
                if not key:
                    continue
                cookie_map[key] = value
        except Exception:
            return {}
        return cookie_map

    @classmethod
    def _sanitize_message(cls, message: Optional[str], fallback: str) -> str:
        text = (message or "").strip()
        if not text:
            return fallback
        lower_text = text.lower()
        if any(key.lower() in lower_text for key in cls.SENSITIVE_KEYS):
            return fallback
        return text[:180]

    def start_login(self) -> Dict:
        payload, _ = self._request_json(self.GENERATE_URL)
        if payload.get("code") != 0:
            raise ValueError(self._sanitize_message(payload.get("message"), "B站二维码生成失败"))

        data = payload.get("data") or {}
        login_url = data.get("url")
        qrcode_key = data.get("qrcode_key")
        if not login_url or not qrcode_key:
            raise ValueError("B站二维码返回字段不完整")

        return {
            "status": self.STATUS_WAITING_SCAN,
            "login_url": login_url,
            "qrcode_key": qrcode_key,
            "expires_in": 180,
        }

    def poll_login(self, qrcode_key: str) -> Dict:
        if not qrcode_key:
            raise ValueError("qrcode_key 不能为空")

        payload, response_cookies = self._request_json(
            self.POLL_URL,
            method="GET",
            params={"qrcode_key": qrcode_key},
        )
        if payload.get("code") != 0:
            raise ValueError(self._sanitize_message(payload.get("message"), "B站扫码状态查询失败"))

        data = payload.get("data") or {}
        bili_code = int(data.get("code", -1))
        message = self._sanitize_message(data.get("message"), "")

        if bili_code == 86101:
            return {
                "status": self.STATUS_WAITING_SCAN,
                "message": message or "等待扫码",
                "bili_code": bili_code,
                "cookie_saved": False,
            }
        if bili_code == 86090:
            return {
                "status": self.STATUS_SCANNED,
                "message": message or "已扫码，等待确认",
                "bili_code": bili_code,
                "cookie_saved": False,
            }
        if bili_code == 86038:
            return {
                "status": self.STATUS_EXPIRED,
                "message": message or "二维码已过期",
                "bili_code": bili_code,
                "cookie_saved": False,
            }
        if bili_code != 0:
            return {
                "status": self.STATUS_FAILED,
                "message": message or "扫码状态未知",
                "bili_code": bili_code,
                "cookie_saved": False,
            }

        # Login confirmed.
        login_url = data.get("url") or ""
        cookie_from_url = self.cookie_service.parse_cookie_from_url(login_url)
        cookie_from_resp = self._extract_cookie_from_response_cookies(response_cookies)
        try:
            saved = self.cookie_service.persist_bilibili_cookie(
                cookie_map=self.cookie_service.merge_cookie_map(cookie_from_resp, cookie_from_url)
            )
        except Exception as exc:
            safe_error = self._sanitize_message(str(exc), "保存失败")
            logger.error(f"B站扫码成功但保存 Cookie 失败: {safe_error}")
            return {
                "status": self.STATUS_FAILED,
                "message": f"登录成功但保存 Cookie 失败: {safe_error}",
                "bili_code": bili_code,
                "cookie_saved": False,
            }

        return {
            "status": self.STATUS_CONFIRMED,
            "message": "扫码登录成功，Cookie 已保存",
            "bili_code": bili_code,
            "cookie_saved": True,
            "cookies_file": saved.get("cookies_file"),
            "updated_at": saved.get("updated_at"),
        }
