import os
import platform
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Request
from pydantic import BaseModel

from app.security.config_guard import validate_sensitive_config_request
from app.utils.response import ResponseWrapper as R
from app.utils.logger import get_logger
from app.utils.path_helper import get_model_dir

from app.services.bbdown_client import BBDownClient
from app.services.bilibili_cookie_service import BilibiliCookieService
from app.services.bbdown_login_service import BBDownLoginService
from app.services.cookie_manager import CookieConfigManager
from app.services.transcriber_config_manager import TranscriberConfigManager
from ffmpeg_helper import ensure_ffmpeg_or_raise

logger = get_logger(__name__)

router = APIRouter()
cookie_manager = CookieConfigManager()
bilibili_cookie_service = BilibiliCookieService(cookie_manager=cookie_manager)
bbdown_client = BBDownClient()
bbdown_login_service = BBDownLoginService(
    bbdown_client=bbdown_client,
    cookie_service=bilibili_cookie_service,
)
transcriber_config_manager = TranscriberConfigManager()


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


class TranscriberConfigRequest(BaseModel):
    transcriber_type: str
    whisper_model_size: Optional[str] = None


AVAILABLE_TRANSCRIBER_TYPES = [
    {"value": "fast-whisper", "label": "Faster Whisper（本地）"},
    {"value": "bcut", "label": "必剪（在线）"},
    {"value": "kuaishou", "label": "快手（在线）"},
    {"value": "groq", "label": "Groq（在线）"},
    {"value": "mlx-whisper", "label": "MLX Whisper（仅macOS）"},
]

WHISPER_MODEL_SIZES = ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]


@router.get("/transcriber_config")
def get_transcriber_config():
    from app.transcriber.transcriber_provider import MLX_WHISPER_AVAILABLE

    config = transcriber_config_manager.get_config()
    return R.success(data={
        **config,
        "available_types": AVAILABLE_TRANSCRIBER_TYPES,
        "whisper_model_sizes": WHISPER_MODEL_SIZES,
        "mlx_whisper_available": MLX_WHISPER_AVAILABLE,
    })


@router.post("/transcriber_config")
def update_transcriber_config(data: TranscriberConfigRequest):
    config = transcriber_config_manager.update_config(
        transcriber_type=data.transcriber_type,
        whisper_model_size=data.whisper_model_size,
    )
    return R.success(data=config)


# ---- Whisper 模型下载状态 & 下载触发 ----

# 用于跟踪正在进行的下载任务
_downloading: dict[str, str] = {}  # model_size -> status ("downloading" | "done" | "failed")


def _check_whisper_model_exists(model_size: str, subdir: str = "whisper") -> bool:
    """检查指定 whisper 模型是否已下载到本地。"""
    model_dir = get_model_dir(subdir)
    model_path = os.path.join(model_dir, f"whisper-{model_size}")
    return Path(model_path).exists()


@router.get("/transcriber_models_status")
def get_transcriber_models_status():
    """返回所有 whisper 模型的下载状态。"""
    statuses = []
    for size in WHISPER_MODEL_SIZES:
        downloaded = _check_whisper_model_exists(size, "whisper")
        download_status = _downloading.get(size)
        statuses.append({
            "model_size": size,
            "downloaded": downloaded,
            "downloading": download_status == "downloading",
        })

    # 也检查 mlx-whisper（仅 macOS）
    mlx_available = platform.system() == "Darwin"
    mlx_statuses = []
    if mlx_available:
        for size in WHISPER_MODEL_SIZES:
            mlx_key = f"mlx-{size}"
            model_dir = get_model_dir("mlx-whisper")
            model_path = os.path.join(model_dir, f"mlx-community/whisper-{size}")
            downloaded = Path(model_path).exists()
            mlx_statuses.append({
                "model_size": size,
                "downloaded": downloaded,
                "downloading": _downloading.get(mlx_key) == "downloading",
            })

    return R.success(data={
        "whisper": statuses,
        "mlx_whisper": mlx_statuses,
        "mlx_available": mlx_available,
    })


class ModelDownloadRequest(BaseModel):
    model_size: str
    transcriber_type: str = "fast-whisper"  # "fast-whisper" 或 "mlx-whisper"


def _do_download_whisper(model_size: str):
    """后台下载 faster-whisper 模型。"""
    from app.transcriber.whisper import MODEL_MAP
    from modelscope import snapshot_download

    try:
        _downloading[model_size] = "downloading"
        model_dir = get_model_dir("whisper")
        model_path = os.path.join(model_dir, f"whisper-{model_size}")
        if Path(model_path).exists():
            _downloading[model_size] = "done"
            return
        repo_id = MODEL_MAP.get(model_size)
        if not repo_id:
            _downloading[model_size] = "failed"
            return
        logger.info(f"开始下载 whisper 模型: {model_size}")
        snapshot_download(repo_id, local_dir=model_path)
        logger.info(f"whisper 模型下载完成: {model_size}")
        _downloading[model_size] = "done"
    except Exception as e:
        logger.error(f"whisper 模型下载失败: {model_size}, {e}")
        _downloading[model_size] = "failed"


def _do_download_mlx_whisper(model_size: str):
    """后台下载 mlx-whisper 模型。"""
    key = f"mlx-{model_size}"
    try:
        _downloading[key] = "downloading"
        from huggingface_hub import snapshot_download as hf_download

        model_dir = get_model_dir("mlx-whisper")
        model_name = f"mlx-community/whisper-{model_size}"
        model_path = os.path.join(model_dir, model_name)
        if Path(model_path).exists():
            _downloading[key] = "done"
            return
        logger.info(f"开始下载 mlx-whisper 模型: {model_size}")
        hf_download(model_name, local_dir=model_path, local_dir_use_symlinks=False)
        logger.info(f"mlx-whisper 模型下载完成: {model_size}")
        _downloading[key] = "done"
    except Exception as e:
        logger.error(f"mlx-whisper 模型下载失败: {model_size}, {e}")
        _downloading[key] = "failed"


@router.post("/transcriber_download")
def download_transcriber_model(data: ModelDownloadRequest, background_tasks: BackgroundTasks):
    """触发后台下载指定的 whisper 模型。"""
    if data.model_size not in WHISPER_MODEL_SIZES:
        return R.error(msg=f"不支持的模型大小: {data.model_size}")

    if data.transcriber_type == "mlx-whisper":
        if platform.system() != "Darwin":
            return R.error(msg="MLX Whisper 仅支持 macOS")
        key = f"mlx-{data.model_size}"
        if _downloading.get(key) == "downloading":
            return R.success(msg="模型正在下载中")
        background_tasks.add_task(_do_download_mlx_whisper, data.model_size)
    else:
        if _downloading.get(data.model_size) == "downloading":
            return R.success(msg="模型正在下载中")
        background_tasks.add_task(_do_download_whisper, data.model_size)

    return R.success(msg="模型下载已开始")


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


@router.get("/deploy_status")
async def deploy_status():
    """返回部署监控所需的所有状态信息"""
    try:
        import torch
        cuda_available = torch.cuda.is_available()
        cuda_info = {
            "available": cuda_available,
            "version": torch.version.cuda if cuda_available else None,
            "gpu_name": torch.cuda.get_device_name(0) if cuda_available else None,
        }
    except Exception:
        cuda_info = {
            "available": False,
            "version": None,
            "gpu_name": None,
        }

    # Whisper 模型状态（从配置文件读取，与前端设置同步）
    transcriber_cfg = transcriber_config_manager.get_config()
    model_size = transcriber_cfg["whisper_model_size"]
    transcriber_type = transcriber_cfg["transcriber_type"]

    # FFmpeg 状态
    try:
        ensure_ffmpeg_or_raise()
        ffmpeg_ok = True
    except Exception:
        ffmpeg_ok = False

    return R.success(data={
        "backend": {"status": "running", "port": int(os.getenv("BACKEND_PORT", 8483))},
        "cuda": cuda_info,
        "whisper": {"model_size": model_size, "transcriber_type": transcriber_type},
        "ffmpeg": {"available": ffmpeg_ok},
    })
