import hmac
import os
from ipaddress import ip_address
from typing import Tuple

from fastapi import Request

ADMIN_TOKEN_HEADER = "X-BiliNote-Admin-Token"
LOCAL_ONLY_ENV = "COOKIE_SECURITY_LOCAL_ONLY"
ADMIN_TOKEN_ENV = "CONFIG_ADMIN_TOKEN"


def _to_bool(value: str, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _extract_host_from_header(host_header: str) -> str:
    if not host_header:
        return ""
    candidate = host_header.split(",")[0].strip().lower()
    if not candidate:
        return ""
    if candidate.startswith("[") and "]" in candidate:
        return candidate[1:candidate.index("]")]
    if candidate.count(":") == 1:
        return candidate.split(":", 1)[0]
    return candidate


def _is_loopback_ip(value: str) -> bool:
    if not value:
        return False
    candidate = value.split(",")[0].strip()
    try:
        return ip_address(candidate).is_loopback
    except Exception:
        return False


def is_local_request(request: Request) -> bool:
    host = _extract_host_from_header(request.headers.get("host", ""))
    if host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
        return True

    forwarded_host = _extract_host_from_header(request.headers.get("x-forwarded-host", ""))
    if forwarded_host in {"localhost", "127.0.0.1", "::1"}:
        return True

    real_ip = request.headers.get("x-real-ip", "")
    if _is_loopback_ip(real_ip):
        return True

    client_host = request.client.host if request.client else ""
    if _is_loopback_ip(client_host):
        return True

    return False


def validate_sensitive_config_request(request: Request) -> Tuple[bool, int, str]:
    local_only_enabled = _to_bool(os.getenv(LOCAL_ONLY_ENV), default=True)
    if local_only_enabled and not is_local_request(request):
        return False, 403, "当前仅允许本机访问 Cookie 敏感配置接口"

    expected_admin_token = (os.getenv(ADMIN_TOKEN_ENV) or "").strip()
    if expected_admin_token:
        request_token = (request.headers.get(ADMIN_TOKEN_HEADER) or "").strip()
        if not request_token or not hmac.compare_digest(request_token, expected_admin_token):
            return False, 401, "管理员令牌无效或缺失"

    return True, 200, "ok"

