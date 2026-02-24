import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.security.config_guard import validate_sensitive_config_request
from app.utils.response import ResponseWrapper as R

from app.services.bbdown_client import BBDownClient
from app.services.bilibili_cookie_service import BilibiliCookieService
from app.services.bbdown_login_service import BBDownLoginService
from app.services.cookie_manager import CookieConfigManager
from ffmpeg_helper import ensure_ffmpeg_or_raise

router = APIRouter()
cookie_manager = CookieConfigManager()
bilibili_cookie_service = BilibiliCookieService(cookie_manager=cookie_manager)
bbdown_client = BBDownClient()
bbdown_login_service = BBDownLoginService(
    bbdown_client=bbdown_client,
    cookie_service=bilibili_cookie_service,
)


class CookieUpdateRequest(BaseModel):
    platform: str
    cookie: str


class BBDownSubtitleTestRequest(BaseModel):
    video_url: str


class BBDownLoginCancelRequest(BaseModel):
    session_id: str


def _sanitize_exception_message(exc: Exception, fallback: str = "请稍后重试") -> str:
    sensitive_keys = ("SESSDATA", "bili_jct", "DedeUserID", "sid")
    message = (str(exc) or "").strip()
    if not message:
        return fallback
    if any(key.lower() in message.lower() for key in sensitive_keys):
        return fallback
    return message[:180]


def _guard_sensitive_request(request: Request):
    allowed, code, message = validate_sensitive_config_request(request)
    if allowed:
        return None
    return R.error(code=code, msg=message)


@router.get("/get_downloader_cookie/{platform}")
def get_cookie(platform: str, request: Request):
    platform_key = (platform or "").strip().lower()
    if platform_key == "bilibili":
        guard_error = _guard_sensitive_request(request)
        if guard_error is not None:
            return guard_error
        status = bilibili_cookie_service.get_cookie_status()
        return R.success(data={"platform": platform_key, **status})

    cookie = cookie_manager.get(platform_key)
    if not cookie:
        return R.success(msg='未找到Cookies')
    return R.success(
        data={"platform": platform_key, "cookie": cookie}
    )


@router.post("/update_downloader_cookie")
def update_cookie(data: CookieUpdateRequest, request: Request):
    platform = (data.platform or "").strip().lower()
    if platform == "bilibili":
        guard_error = _guard_sensitive_request(request)
        if guard_error is not None:
            return guard_error
        status = bilibili_cookie_service.get_cookie_status()
        return R.success(
            data={
                "platform": data.platform,
                "cookie_saved": status.get("exists", False),
                "cookies_file": status.get("cookies_file"),
                "updated_at": status.get("updated_at"),
                "source": status.get("source"),
                "message": "B站 Cookie 由 BBDown 维护，请在设置页使用网页扫码登录或使用命令行登录",
            }
        )

    cookie_manager.set(platform, data.cookie)
    return R.success(data={"platform": data.platform, "cookie_saved": True})


@router.post("/bilibili_qr_login/start")
def start_bilibili_qr_login(request: Request):
    return R.error(code=410, msg="B站网页扫码接口已下线，请使用 BBDown 命令行登录")


@router.get("/bilibili_qr_login/poll")
def poll_bilibili_qr_login(request: Request):
    return R.error(code=410, msg="B站网页扫码接口已下线，请使用 BBDown 命令行登录")


def _to_container_path(raw_path: str, default_relative: str) -> str:
    value = (raw_path or "").strip() or default_relative
    value = value.replace("\\", "/")
    if value.startswith("/"):
        return value
    return f"/app/{value}".replace("//", "/")


@router.get("/bilibili_bbdown/status")
def bilibili_bbdown_status(request: Request):
    guard_error = _guard_sensitive_request(request)
    if guard_error is not None:
        return guard_error

    version_info = bbdown_client.get_version()
    cookie_status = bilibili_cookie_service.get_cookie_status()
    cookies_file = bilibili_cookie_service.resolve_cookie_file_path()
    work_dir = bbdown_client.resolve_work_dir()

    work_dir_for_command = _to_container_path(
        os.getenv("BBDOWN_WORK_DIR", ""),
        "data/bbdown",
    )
    bin_for_command = os.path.basename(version_info.get("bin_path") or "BBDown")

    return R.success(
        data={
            "installed": bool(version_info.get("installed")),
            "version": version_info.get("version"),
            "bin_path": version_info.get("bin_path") or bbdown_client.bin_path,
            "work_dir": str(work_dir),
            "cookies_file": str(cookies_file),
            "cookie_exists": bool(cookie_status.get("exists")),
            "cookie_updated_at": cookie_status.get("updated_at"),
            "login_command": f"docker compose exec -it backend {bin_for_command} login",
            "test_command_template": (
                f"docker compose exec -it backend {bin_for_command} <B站URL> "
                f"--sub-only --skip-ai false --work-dir {work_dir_for_command}"
            ),
        }
    )


@router.post("/bilibili_bbdown/login/start")
def bilibili_bbdown_login_start(request: Request):
    guard_error = _guard_sensitive_request(request)
    if guard_error is not None:
        return guard_error
    try:
        return R.success(data=bbdown_login_service.start_login())
    except Exception as exc:
        safe_message = _sanitize_exception_message(exc, fallback="启动登录失败")
        return R.error(msg=f"启动 BBDown 登录失败: {safe_message}")


@router.get("/bilibili_bbdown/login/status")
def bilibili_bbdown_login_status(request: Request, session_id: Optional[str] = None):
    guard_error = _guard_sensitive_request(request)
    if guard_error is not None:
        return guard_error
    try:
        return R.success(data=bbdown_login_service.get_login_status(session_id=session_id))
    except Exception as exc:
        safe_message = _sanitize_exception_message(exc, fallback="获取登录状态失败")
        return R.error(msg=f"获取 BBDown 登录状态失败: {safe_message}")


@router.post("/bilibili_bbdown/login/cancel")
def bilibili_bbdown_login_cancel(data: BBDownLoginCancelRequest, request: Request):
    guard_error = _guard_sensitive_request(request)
    if guard_error is not None:
        return guard_error
    try:
        return R.success(data=bbdown_login_service.cancel_login(session_id=data.session_id))
    except Exception as exc:
        safe_message = _sanitize_exception_message(exc, fallback="取消登录失败")
        return R.error(msg=f"取消 BBDown 登录失败: {safe_message}")


@router.post("/bilibili_bbdown/test")
def bilibili_bbdown_test(data: BBDownSubtitleTestRequest, request: Request):
    guard_error = _guard_sensitive_request(request)
    if guard_error is not None:
        return guard_error

    video_url = (data.video_url or "").strip()
    if not video_url:
        return R.error(code=400, msg="请先输入待测试的 B站视频链接")

    cookie_file = bilibili_cookie_service.resolve_cookie_file_path()
    run_result = bbdown_client.run_subtitle_download(
        video_url=video_url,
        output_dir=bbdown_client.resolve_work_dir(),
        cookie_file=cookie_file if cookie_file.exists() else None,
    )
    subtitle_files = run_result.get("subtitle_files") or []
    diagnostics = run_result.get("diagnostics") or {}
    stdout_tail = diagnostics.get("stdout_tail") if isinstance(diagnostics, dict) else None
    stderr_tail = diagnostics.get("stderr_tail") if isinstance(diagnostics, dict) else None

    return R.success(
        data={
            "success": bool(run_result.get("success")),
            "reason_code": run_result.get("reason_code"),
            "message": run_result.get("message"),
            "subtitle_count": len(subtitle_files),
            "subtitle_files": [Path(item).name for item in subtitle_files[:5]],
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
        }
    )


@router.get("/bilibili_cookie/status")
def bilibili_cookie_status(request: Request):
    guard_error = _guard_sensitive_request(request)
    if guard_error is not None:
        return guard_error
    return R.success(data=bilibili_cookie_service.get_cookie_status())


@router.get("/bilibili_cookie/verify")
def verify_bilibili_cookie(request: Request):
    guard_error = _guard_sensitive_request(request)
    if guard_error is not None:
        return guard_error
    try:
        return R.success(data=bilibili_cookie_service.verify_cookie())
    except Exception as exc:
        safe_message = _sanitize_exception_message(exc, fallback="检测失败")
        return R.error(msg=f"B站 Cookie 检测失败: {safe_message}")


@router.delete("/bilibili_cookie")
def clear_bilibili_cookie(request: Request):
    guard_error = _guard_sensitive_request(request)
    if guard_error is not None:
        return guard_error
    try:
        return R.success(data=bilibili_cookie_service.clear_cookie())
    except Exception as exc:
        safe_message = _sanitize_exception_message(exc, fallback="删除失败")
        return R.error(msg=f"B站 Cookie 清除失败: {safe_message}")


@router.get("/sys_health")
async def sys_health():
    try:
        ensure_ffmpeg_or_raise()
        return R.success()
    except EnvironmentError:
        return R.error(msg="系统未安装 ffmpeg 请先进行安装")

@router.get("/sys_check")
async def sys_check():
    return R.success()
