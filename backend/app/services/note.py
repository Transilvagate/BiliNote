import json
import logging
import os
import re
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union, Any

from pydantic import HttpUrl
from dotenv import load_dotenv

from app.downloaders.base import Downloader
from app.db.video_task_dao import delete_task_by_video, insert_video_task
from app.enmus.exception import NoteErrorEnum, ProviderErrorEnum
from app.enmus.task_status_enums import TaskStatus
from app.enmus.note_enums import DownloadQuality
from app.exceptions.note import NoteError
from app.exceptions.provider import ProviderError
from app.gpt.base import GPT
from app.gpt.context_budget import estimate_tokens, is_overflow, resolve_context_limit
from app.gpt.gpt_factory import GPTFactory
from app.gpt.prompt import (
    FORMAL_TRANSCRIPT_STYLE_AUTO,
    FORMAL_TRANSCRIPT_STYLE_LEAD_PLUS_SUPPORT,
    FORMAL_TRANSCRIPT_STYLE_MULTI_DIALOGUE,
    FORMAL_TRANSCRIPT_STYLE_SINGLE_NARRATION,
)
from app.models.gpt_model import GPTSource
from app.models.model_config import ModelConfig
from app.models.notes_model import AudioDownloadResult, NoteResult
from app.models.transcriber_model import TranscriptResult, TranscriptSegment, SubtitleFetchResult
from app.services.constant import SUPPORT_PLATFORM_MAP
from app.services.provider import ProviderService
from app.transcriber.base import Transcriber
from app.transcriber.transcriber_provider import get_transcriber, _transcribers
from app.utils.note_helper import replace_content_markers
from app.utils.task_assets import get_task_assets_dir, get_task_note_path
from app.utils.formal_transcript import sanitize_formal_transcript_body
from app.utils.video_helper import generate_screenshot
from app.utils.video_reader import VideoReader

# ------------------ 环境变量与全局配置 ------------------

# 从 .env 文件中加载环境变量
load_dotenv()

# 后端 API 地址与端口（若有需要可以在代码其他部分使用 BACKEND_BASE_URL）
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost")
BACKEND_PORT = os.getenv("BACKEND_PORT", "8483")
BACKEND_BASE_URL = f"{API_BASE_URL}:{BACKEND_PORT}"

# 输出目录（用于缓存音频、转写、Markdown 文件，以及存储截图）
NOTE_OUTPUT_DIR = Path(os.getenv("NOTE_OUTPUT_DIR", "note_results"))
NOTE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 日志配置
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

STATUS_STEP_ORDER = [
    TaskStatus.PENDING.value,
    TaskStatus.PARSING.value,
    TaskStatus.DOWNLOADING.value,
    TaskStatus.TRANSCRIBING.value,
    TaskStatus.SUMMARIZING.value,
    TaskStatus.FORMATTING.value,
    TaskStatus.SAVING.value,
    TaskStatus.SUCCESS.value,
]
STATUS_EVENTS_LIMIT = 30
FORMAL_TRANSCRIPT_OUTPUT_RESERVE = int(os.getenv("FORMAL_TRANSCRIPT_OUTPUT_RESERVE", "4000"))
FORMAL_TRANSCRIPT_CHUNK_TARGET = int(os.getenv("FORMAL_TRANSCRIPT_CHUNK_TARGET", "6000"))
FORMAL_TRANSCRIPT_MERGE_TARGET = int(os.getenv("FORMAL_TRANSCRIPT_MERGE_TARGET", "8000"))
ASR_LOCK = threading.Lock()

BILIBILI_CHINESE_SUBTITLE_LANGS = ["zh-Hans", "zh-CN", "zh-TW", "zh", "ai-zh"]
BILIBILI_ENGLISH_SUBTITLE_LANGS = ["en", "en-US", "en-GB"]
BILIBILI_SHORT_VIDEO_THRESHOLD_SECONDS = 600


class NoteGenerator:
    """
    NoteGenerator 用于执行视频/音频下载、转写、GPT 生成笔记、插入截图/链接、
    以及将任务信息写入状态文件与数据库等功能。
    """

    def __init__(self):
        self.model_size: str = "base"
        self.device: Optional[str] = None
        self.transcriber_type: str = os.getenv("TRANSCRIBER_TYPE", "fast-whisper")
        self.transcriber: Optional[Transcriber] = None
        self.video_path: Optional[Path] = None
        self.video_img_urls=[]
        logger.info("NoteGenerator 初始化完成（转写器懒加载）")


    # ---------------- 公有方法 ----------------

    def generate(
        self,
        video_url: Union[str, HttpUrl],
        platform: str,
        quality: DownloadQuality = DownloadQuality.medium,
        task_id: Optional[str] = None,
        model_name: Optional[str] = None,
        provider_id: Optional[str] = None,
        link: bool = False,
        screenshot: bool = False,
        _format: Optional[List[str]] = None,
        style: Optional[str] = None,
        extras: Optional[str] = None,
        formal_transcript_style: str = "auto",
        output_path: Optional[str] = None,
        video_understanding: bool = False,
        video_interval: int = 0,
        grid_size: Optional[List[int]] = None,
        force_refresh_transcript: bool = False,
    ) -> NoteResult | None:
        """
        主流程：按步骤依次下载、转写、GPT 总结、截图/链接处理、存库、返回 NoteResult。

        :param video_url: 视频或音频链接
        :param platform: 平台名称，对应 SUPPORT_PLATFORM_MAP 中的键
        :param quality: 下载音频的质量枚举
        :param task_id: 用于标识本次任务的唯一 ID，亦用于状态文件和缓存文件命名
        :param model_name: GPT 模型名称
        :param provider_id: 模型供应商 ID
        :param link: 是否在笔记中插入视频片段链接
        :param screenshot: 是否在笔记中替换 Screenshot 标记为图片
        :param _format: 包含 'link' 或 'screenshot' 等字符串的列表，决定后续处理
        :param style: GPT 生成笔记的风格
        :param extras: 额外参数，传递给 GPT
        :param output_path: 下载输出目录（可选）
        :param video_understanding: 是否需要视频拼图理解（生成缩略图）
        :param video_interval: 视频帧截取间隔（秒），仅在 video_understanding 为 True 时生效
        :param grid_size: 生成缩略图时的网格大小，如 [3, 3]
        :return: NoteResult 对象，包含 markdown 文本、转写结果和音频元信息
        """
        if grid_size is None:
            grid_size = []

        try:
            logger.info(f"开始生成笔记 (task_id={task_id})")
            self.video_path = None
            self.video_img_urls = []
            self._update_status(
                task_id,
                TaskStatus.PARSING,
                message="解析链接",
                detail="正在解析链接并初始化任务",
                source="system",
            )

            # 获取下载器与 GPT 实例

            downloader = self._get_downloader(platform)
            gpt = self._get_gpt(model_name, provider_id)
            self._update_status(
                task_id,
                TaskStatus.PARSING,
                message="解析链接",
                detail=f"平台识别为 {platform}，已准备下载器与模型",
                source="system",
            )

            # 缓存文件路径
            audio_cache_file = NOTE_OUTPUT_DIR / f"{task_id}_audio.json"
            transcript_cache_file = NOTE_OUTPUT_DIR / f"{task_id}_transcript.json"
            markdown_cache_file = NOTE_OUTPUT_DIR / f"{task_id}_markdown.md"
            # 1. 先尝试平台字幕（命中则跳过音频下载）
            transcript = self._get_transcript(
                downloader=downloader,
                video_url=video_url,
                audio_file=None,
                transcript_cache_file=transcript_cache_file,
                status_phase=TaskStatus.TRANSCRIBING,
                task_id=task_id,
                platform=platform,
                force_refresh=force_refresh_transcript,
                allow_asr_fallback=False,
            )

            # 2. 根据字幕命中情况决定是否下载音频
            if transcript is None:
                audio_meta = self._download_media(
                    downloader=downloader,
                    video_url=video_url,
                    quality=quality,
                    audio_cache_file=audio_cache_file,
                    status_phase=TaskStatus.DOWNLOADING,
                    platform=platform,
                    output_path=output_path,
                    screenshot=screenshot,
                    video_understanding=video_understanding,
                    video_interval=video_interval,
                    grid_size=grid_size,
                    download_audio=True,
                )
                transcript = self._transcribe_audio(
                    audio_file=audio_meta.file_path,
                    transcript_cache_file=transcript_cache_file,
                    status_phase=TaskStatus.TRANSCRIBING,
                    force_refresh=force_refresh_transcript,
                )
            else:
                audio_meta = self._download_media(
                    downloader=downloader,
                    video_url=video_url,
                    quality=quality,
                    audio_cache_file=audio_cache_file,
                    status_phase=TaskStatus.DOWNLOADING,
                    platform=platform,
                    output_path=output_path,
                    screenshot=screenshot,
                    video_understanding=video_understanding,
                    video_interval=video_interval,
                    grid_size=grid_size,
                    download_audio=False,
                )

            # 3. GPT 生成
            markdown = self._summarize_text(
                audio_meta=audio_meta,
                transcript=transcript,
                gpt=gpt,
                markdown_cache_file=markdown_cache_file,
                link=link,
                screenshot=screenshot,
                formats=_format or [],
                style=style,
                extras=extras,
                formal_transcript_style=formal_transcript_style,
                video_img_urls=self.video_img_urls,
                task_id=task_id,
            )

            # 4. 截图 & 链接替换
            if _format:
                self._update_status(
                    task_id,
                    TaskStatus.FORMATTING,
                    message="格式化内容",
                    detail="正在处理截图与跳转链接",
                    source="system",
                )
                markdown = self._post_process_markdown(
                    markdown=markdown,
                    video_path=self.video_path,
                    formats=_format,
                    audio_meta=audio_meta,
                    platform=platform,
                    task_id=task_id,
                )

            # 5. 同步写入每任务目录中的 note.md
            self._update_status(
                task_id,
                TaskStatus.SAVING,
                message="保存结果",
                detail="正在写入 Markdown 文件",
                source="system",
            )
            self._save_task_markdown(task_id=task_id, markdown=markdown)

            # 6. 保存记录到数据库
            self._update_status(
                task_id,
                TaskStatus.SAVING,
                message="保存结果",
                detail="正在保存任务元数据",
                source="system",
            )
            self._save_metadata(video_id=audio_meta.video_id, platform=platform, task_id=task_id)

            # 7. 完成
            self._update_status(
                task_id,
                TaskStatus.SUCCESS,
                message="任务完成",
                detail="笔记已生成完成",
                source="system",
            )
            logger.info(f"笔记生成成功 (task_id={task_id})")
            return NoteResult(markdown=markdown, transcript=transcript, audio_meta=audio_meta)

        except Exception as exc:
            logger.error(f"生成笔记流程异常 (task_id={task_id})：{exc}", exc_info=True)
            self._handle_exception(task_id, exc)
            return None

    @staticmethod
    def delete_note(video_id: str, platform: str) -> int:
        """
        删除数据库中对应 video_id 与 platform 的任务记录

        :param video_id: 视频 ID
        :param platform: 平台标识
        :return: 删除的记录数
        """
        logger.info(f"删除笔记记录 (video_id={video_id}, platform={platform})")
        return delete_task_by_video(video_id, platform)

    # ---------------- 私有方法 ----------------

    def _init_transcriber(self) -> Transcriber:
        """
        根据环境变量 TRANSCRIBER_TYPE 动态获取并实例化转写器
        """
        supported_types = {item.value for item in _transcribers}
        if self.transcriber_type not in supported_types:
            logger.error(f"未找到支持的转写器：{self.transcriber_type}")
            raise Exception(f"不支持的转写器：{self.transcriber_type}")

        logger.info(f"使用转写器：{self.transcriber_type}")
        return get_transcriber(transcriber_type=self.transcriber_type)

    def _ensure_transcriber(self) -> Transcriber:
        if self.transcriber is None:
            self.transcriber = self._init_transcriber()
        return self.transcriber

    def _get_gpt(self, model_name: Optional[str], provider_id: Optional[str]) -> GPT:
        """
        根据 provider_id 获取对应的 GPT 实例
        :param model_name: GPT 模型名称
        :param provider_id: 供应商 ID
        :return: GPT 实例
        """
        provider = ProviderService.get_provider_by_id(provider_id)
        if not provider:
            logger.error(f"[get_gpt] 未找到模型供应商: provider_id={provider_id}")
            raise ProviderError(code=ProviderErrorEnum.NOT_FOUND,message=ProviderErrorEnum.NOT_FOUND.message)
        logger.info(f"创建 GPT 实例 {provider_id}")
        config = ModelConfig(
            api_key=provider["api_key"],
            base_url=provider["base_url"],
            model_name=model_name,
            provider=provider["type"],
            name=provider["name"],
        )
        return GPTFactory().from_config(config)

    def _get_downloader(self, platform: str) -> Downloader:
        """
        根据平台名称获取对应的下载器实例

        :param platform: 平台标识，需在 SUPPORT_PLATFORM_MAP 中
        :return: 对应的 Downloader 子类实例
        """
        downloader_cls = SUPPORT_PLATFORM_MAP.get(platform)
        logger.debug(f"实例化下载器 -  {platform}")
        if not downloader_cls:
            logger.error(f"不支持的平台：{platform}")
            raise NoteError(code=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.code,
                            message=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.message)
        try:
            instance: Downloader = downloader_cls()
        except Exception as e:
            logger.error(f"实例化下载器失败：{e}")
            raise

        logger.info(f"使用下载器：{downloader_cls.__name__}")
        return instance

    @staticmethod
    def _status_to_key(status: Union[str, TaskStatus]) -> str:
        return status.value if isinstance(status, TaskStatus) else status

    @staticmethod
    def _parse_iso_time(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        normalized = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None

    def _build_step_info(self, status_key: str) -> Dict[str, Any]:
        index = STATUS_STEP_ORDER.index(status_key) + 1 if status_key in STATUS_STEP_ORDER else 0
        label = TaskStatus.description(TaskStatus(status_key)) if status_key in TaskStatus._value2member_map_ else "未知状态"
        return {
            "key": status_key,
            "label": label,
            "index": index,
            "total": len(STATUS_STEP_ORDER),
        }

    def _status_file_path(self, task_id: str) -> Path:
        NOTE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        return NOTE_OUTPUT_DIR / f"{task_id}.status.json"

    def _read_status(self, task_id: Optional[str]) -> Dict[str, Any]:
        if not task_id:
            return {}
        status_file = self._status_file_path(task_id)
        if not status_file.exists():
            return {}
        try:
            return json.loads(status_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(f"读取状态文件失败 (task_id={task_id})：{exc}")
            return {}

    def _write_status(self, task_id: str, payload: Dict[str, Any]) -> None:
        status_file = self._status_file_path(task_id)
        try:
            temp_file = status_file.with_suffix(".tmp")
            with temp_file.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            temp_file.replace(status_file)
        except Exception as exc:
            logger.error(f"写入状态文件失败 (task_id={task_id})：{exc}")

    def _update_status(
        self,
        task_id: Optional[str],
        status: Union[str, TaskStatus],
        message: Optional[str] = None,
        detail: Optional[str] = None,
        source: str = "system",
        diagnostics: Optional[Dict[str, Any]] = None,
        error: Optional[Dict[str, Any]] = None,
    ):
        """
        创建或更新 {task_id}.status.json，记录当前任务状态与进度细节。
        """
        if not task_id:
            return

        status_key = self._status_to_key(status)
        now = datetime.now(timezone.utc)
        now_str = now.isoformat()

        existing = self._read_status(task_id)
        started_at = existing.get("started_at") or now_str
        started_time = self._parse_iso_time(started_at) or now
        elapsed_ms = max(int((now - started_time).total_seconds() * 1000), 0)

        if message:
            status_message = message
        elif status_key in TaskStatus._value2member_map_:
            status_message = TaskStatus.description(TaskStatus(status_key))
        else:
            status_message = status_key
        events = list(existing.get("events") or [])
        event = {
            "at": now_str,
            "status": status_key,
            "message": status_message,
            "detail": detail or "",
            "source": source,
        }
        if diagnostics:
            event["diagnostics"] = diagnostics
        events.append(event)

        payload = {
            "status": status_key,
            "message": status_message,
            "detail": detail or existing.get("detail", ""),
            "source": source,
            "step": self._build_step_info(status_key),
            "started_at": started_at,
            "updated_at": now_str,
            "elapsed_ms": elapsed_ms,
            "events": events[-STATUS_EVENTS_LIMIT:],
            "diagnostics": diagnostics if diagnostics is not None else existing.get("diagnostics", {}),
            "task_id": task_id,
        }
        if error is not None:
            payload["error"] = error

        self._write_status(task_id, payload)

    def _handle_exception(
        self,
        task_id: Optional[str],
        exc: Exception,
        reason_code: str = "INTERNAL_ERROR",
        diagnostics: Optional[Dict[str, Any]] = None,
    ):
        logger.error(f"任务异常 (task_id={task_id})", exc_info=True)
        error_message = getattr(exc, "detail", str(exc))
        if isinstance(error_message, dict):
            try:
                error_message = json.dumps(error_message, ensure_ascii=False)
            except Exception:
                error_message = str(error_message)
        merged_diagnostics = {"exception_type": exc.__class__.__name__}
        if diagnostics:
            merged_diagnostics.update(diagnostics)
        self._update_status(
            task_id=task_id,
            status=TaskStatus.FAILED,
            message="任务失败",
            detail=str(error_message),
            source="system",
            diagnostics=merged_diagnostics,
            error={"reason_code": reason_code, "retryable": True},
        )

    def _download_media(
        self,
        downloader: Downloader,
        video_url: Union[str, HttpUrl],
        quality: DownloadQuality,
        audio_cache_file: Path,
        status_phase: TaskStatus,
        platform: str,
        output_path: Optional[str],
        screenshot: bool,
        video_understanding: bool,
        video_interval: int,
        grid_size: List[int],
        download_audio: bool = True,
    ) -> AudioDownloadResult | None:
        """
        1. 根据需要下载视频（用于截图/多模态理解）。
        2. 读取音频缓存；若无缓存则按配置选择仅取元信息或下载音频。
        3. 返回 AudioDownloadResult。

        :param downloader: Downloader 实例
        :param video_url: 视频/音频链接
        :param quality: 音频下载质量
        :param audio_cache_file: 本地缓存 JSON 文件路径
        :param status_phase: 对应的状态枚举，如 TaskStatus.DOWNLOADING
        :param platform: 平台标识
        :param output_path: 下载输出目录（可为 None）
        :param screenshot: 是否需要在笔记中插入截图
        :param video_understanding: 是否需要生成缩略图
        :param video_interval: 视频截帧间隔
        :param grid_size: 缩略图网格尺寸
        :param download_audio: 是否强制下载音频（False 时优先只取元信息）
        :return: AudioDownloadResult 对象
        """
        task_id = audio_cache_file.stem.split("_")[0]
        self._update_status(
            task_id,
            status_phase,
            message="下载资源",
            detail="正在准备下载音频/视频",
            source="download",
        )

        # 判断是否需要下载视频
        need_video = screenshot or video_understanding
        if need_video:
            try:
                self._update_status(
                    task_id,
                    status_phase,
                    message="下载资源",
                    detail="正在下载视频用于截图/多模态理解",
                    source="download",
                )
                logger.info("开始下载视频")
                video_path_str = downloader.download_video(video_url)
                self.video_path = Path(video_path_str)
                logger.info(f"视频下载完成：{self.video_path}")

                # 若指定了 grid_size，则生成缩略图
                if grid_size:
                    self.video_img_urls=VideoReader(
                        video_path=str(self.video_path),
                        grid_size=tuple(grid_size),
                        frame_interval=video_interval,
                        unit_width=1280,
                        unit_height=720,
                        save_quality=90,
                    ).run()
                    self._update_status(
                        task_id,
                        status_phase,
                        message="下载资源",
                        detail=f"视频下载完成，已生成 {len(self.video_img_urls)} 组拼图",
                        source="download",
                    )
                else:
                    logger.info("未指定 grid_size，跳过缩略图生成")
                    self._update_status(
                        task_id,
                        status_phase,
                        message="下载资源",
                        detail="视频下载完成，未启用拼图生成",
                        source="download",
                    )
            except Exception as exc:
                logger.error(f"视频下载失败：{exc}")

                self._handle_exception(task_id, exc, reason_code="VIDEO_DOWNLOAD_FAILED")
                raise
        # 已有缓存，尝试加载
        if audio_cache_file.exists():
            logger.info(f"检测到音频缓存 ({audio_cache_file})，直接读取")
            try:
                data = json.loads(audio_cache_file.read_text(encoding="utf-8"))
                self._update_status(
                    task_id,
                    status_phase,
                    message="下载资源",
                    detail="命中音频缓存，跳过下载",
                    source="cache",
                )
                return AudioDownloadResult(**data)
            except Exception as e:
                logger.warning(f"读取音频缓存失败，将重新下载：{e}")

        if not download_audio:
            try:
                self._update_status(
                    task_id,
                    status_phase,
                    message="下载资源",
                    detail="字幕已命中，跳过音频下载，正在提取媒体元信息",
                    source="download",
                )
                media_info = downloader.get_media_info(video_url=video_url, output_dir=output_path)
                if media_info:
                    audio_cache_file.write_text(
                        json.dumps(asdict(media_info), ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    self._update_status(
                        task_id,
                        status_phase,
                        message="下载资源",
                        detail="已提取媒体元信息，未下载音频",
                        source="download",
                    )
                    return media_info
                self._update_status(
                    task_id,
                    status_phase,
                    message="下载资源",
                    detail="媒体元信息提取失败，降级为音频下载",
                    source="download",
                )
            except Exception as exc:
                logger.warning(f"媒体元信息提取失败，降级下载音频: {exc}")
                self._update_status(
                    task_id,
                    status_phase,
                    message="下载资源",
                    detail=f"媒体元信息提取失败，降级为音频下载：{exc}",
                    source="download",
                )
        # 下载音频
        try:
            self._update_status(
                task_id,
                status_phase,
                message="下载资源",
                detail="正在下载音频",
                source="download",
            )
            logger.info("开始下载音频")
            audio = downloader.download(
                video_url=video_url,
                quality=quality,
                output_dir=output_path,
                need_video=need_video,
            )
            # 缓存 audio 元信息到本地 JSON
            audio_cache_file.write_text(json.dumps(asdict(audio), ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"音频下载并缓存成功 ({audio_cache_file})")
            self._update_status(
                task_id,
                status_phase,
                message="下载资源",
                detail="音频下载完成",
                source="download",
            )
            return audio
        except Exception as exc:
            logger.error(f"音频下载失败：{exc}")
            self._handle_exception(task_id, exc, reason_code="AUDIO_DOWNLOAD_FAILED")
            raise

    @staticmethod
    def _normalize_subtitle_result(subtitle_result: Any) -> SubtitleFetchResult:
        if isinstance(subtitle_result, SubtitleFetchResult):
            return subtitle_result
        if isinstance(subtitle_result, TranscriptResult):
            return SubtitleFetchResult(
                transcript=subtitle_result,
                outcome="success",
                message="使用平台字幕",
            )
        if subtitle_result is None:
            return SubtitleFetchResult(
                outcome="unavailable",
                reason_code="SUBTITLE_NOT_AVAILABLE",
                message="平台无可用字幕",
            )
        return SubtitleFetchResult(
            outcome="error",
            reason_code="SUBTITLE_FETCH_EXCEPTION",
            message="字幕抓取返回结果异常",
            diagnostics={"raw_type": subtitle_result.__class__.__name__},
        )

    @staticmethod
    def _build_subtitle_diagnostics(
        subtitle_result: Optional[SubtitleFetchResult],
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        diagnostics: Dict[str, Any] = {}
        if subtitle_result:
            diagnostics.update(subtitle_result.diagnostics or {})
            if subtitle_result.outcome:
                diagnostics.setdefault("subtitle_outcome", subtitle_result.outcome)
            if subtitle_result.reason_code:
                diagnostics.setdefault("subtitle_reason_code", subtitle_result.reason_code)
            if subtitle_result.message:
                diagnostics.setdefault("subtitle_message", subtitle_result.message)

            transcript = subtitle_result.transcript
            if transcript:
                raw = transcript.raw if isinstance(transcript.raw, dict) else {}
                diagnostics.setdefault("subtitle_source", raw.get("source") or diagnostics.get("provider"))
                diagnostics.setdefault("subtitle_file", raw.get("file") or diagnostics.get("subtitle_file"))
                diagnostics.setdefault("subtitle_language", transcript.language or raw.get("language"))
                diagnostics.setdefault("subtitle_segment_count", len(transcript.segments or []))

        if extra:
            diagnostics.update(extra)
        return diagnostics

    @staticmethod
    def _resolve_media_duration_seconds(
        downloader: Downloader,
        video_url: str,
    ) -> Optional[float]:
        try:
            media_info = downloader.get_media_info(video_url=video_url, output_dir=None)
        except Exception:
            return None
        if not media_info:
            return None
        duration = getattr(media_info, "duration", None)
        try:
            return float(duration) if duration is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _bilibili_fallback_subtitle_langs(duration_seconds: Optional[float]) -> List[str]:
        if duration_seconds is None:
            return []
        if duration_seconds < BILIBILI_SHORT_VIDEO_THRESHOLD_SECONDS:
            return list(BILIBILI_ENGLISH_SUBTITLE_LANGS)
        return []


    def _get_transcript(
        self,
        downloader: Downloader,
        video_url: str,
        audio_file: Optional[str],
        transcript_cache_file: Path,
        status_phase: TaskStatus,
        task_id: Optional[str] = None,
        platform: Optional[str] = None,
        force_refresh: bool = False,
        allow_asr_fallback: bool = True,
    ) -> TranscriptResult | None:
        """
        优先获取平台字幕，没有则 fallback 到音频转写

        :param downloader: 下载器实例
        :param video_url: 视频链接
        :param audio_file: 音频文件路径（用于 fallback 转写）
        :param transcript_cache_file: 缓存文件路径
        :param status_phase: 状态枚举
        :param task_id: 任务 ID
        :param platform: 平台标识
        :param allow_asr_fallback: 是否允许在字幕失败后回退 ASR
        :return: TranscriptResult 对象
        """
        self._update_status(
            task_id,
            status_phase,
            message="获取字幕/转写",
            detail="准备获取转写文本",
            source="system",
        )

        # 已有缓存，直接返回
        if transcript_cache_file.exists() and not force_refresh:
            logger.info(f"检测到转写缓存 ({transcript_cache_file})，尝试读取")
            try:
                data = json.loads(transcript_cache_file.read_text(encoding="utf-8"))
                segments = [TranscriptSegment(**seg) for seg in data.get("segments", [])]
                self._update_status(
                    task_id,
                    status_phase,
                    message="获取字幕/转写",
                    detail="命中转写缓存，跳过字幕与ASR流程",
                    source="cache",
                )
                return TranscriptResult(language=data.get("language"), full_text=data["full_text"], segments=segments)
            except Exception as e:
                logger.warning(f"加载转写缓存失败，将重新获取：{e}")

        # 1. 先尝试获取平台字幕
        logger.info("尝试获取平台字幕...")
        normalized_platform = (platform or "").strip().lower()
        subtitle_attempts: List[Tuple[str, Optional[List[str]]]] = [("默认字幕", None)]
        policy_diagnostics: Dict[str, Any] = {"platform": normalized_platform or "unknown"}
        if normalized_platform == "bilibili":
            duration_seconds = self._resolve_media_duration_seconds(downloader, video_url)
            fallback_langs = self._bilibili_fallback_subtitle_langs(duration_seconds)
            subtitle_attempts = [("中文字幕", list(BILIBILI_CHINESE_SUBTITLE_LANGS))]
            if fallback_langs:
                subtitle_attempts.append(("英文字幕", fallback_langs))
            policy_diagnostics.update(
                {
                    "duration_seconds": duration_seconds,
                    "short_video_threshold_seconds": BILIBILI_SHORT_VIDEO_THRESHOLD_SECONDS,
                    "english_fallback_enabled": bool(fallback_langs),
                    "fallback_rule": "short_video_en_else_asr",
                }
            )

        last_subtitle_result: Optional[SubtitleFetchResult] = None
        for attempt_index, (attempt_label, langs) in enumerate(subtitle_attempts, start=1):
            self._update_status(
                task_id,
                status_phase,
                message="获取字幕/转写",
                detail=f"正在尝试平台字幕（{attempt_label}）",
                source="subtitle",
                diagnostics=self._build_subtitle_diagnostics(
                    last_subtitle_result,
                    extra={
                        **policy_diagnostics,
                        "subtitle_attempt_label": attempt_label,
                        "subtitle_attempt_index": attempt_index,
                        "subtitle_attempt_total": len(subtitle_attempts),
                        "requested_langs": langs or [],
                    },
                ),
            )
            try:
                subtitle_result = self._normalize_subtitle_result(
                    downloader.download_subtitles(video_url, langs=langs)
                )
            except Exception as e:
                logger.warning(f"获取平台字幕失败 ({attempt_label}): {e}")
                diagnostics = {
                    **policy_diagnostics,
                    "reason_code": "SUBTITLE_FETCH_EXCEPTION",
                    "exception": str(e),
                    "subtitle_attempt_label": attempt_label,
                    "subtitle_attempt_index": attempt_index,
                    "subtitle_attempt_total": len(subtitle_attempts),
                    "requested_langs": langs or [],
                }
                has_next_attempt = attempt_index < len(subtitle_attempts)
                next_action = "继续尝试其他字幕策略" if has_next_attempt else (
                    "准备下载音频并转写" if not allow_asr_fallback else "回退到音频转写"
                )
                self._update_status(
                    task_id,
                    status_phase,
                    message="获取字幕/转写",
                    detail=f"字幕抓取异常（{attempt_label}），{next_action}：{e}",
                    source="subtitle",
                    diagnostics=diagnostics,
                )
                continue

            transcript = subtitle_result.transcript if subtitle_result else None
            diagnostics = self._build_subtitle_diagnostics(
                subtitle_result,
                extra={
                    **policy_diagnostics,
                    "subtitle_attempt_label": attempt_label,
                    "subtitle_attempt_index": attempt_index,
                    "subtitle_attempt_total": len(subtitle_attempts),
                    "requested_langs": langs or [],
                },
            )
            if transcript and transcript.segments:
                logger.info(f"成功获取平台字幕，共 {len(transcript.segments)} 段")
                # 缓存结果
                transcript_cache_file.write_text(
                    json.dumps(asdict(transcript), ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )
                self._update_status(
                    task_id,
                    status_phase,
                    message="获取字幕/转写",
                    detail=f"平台字幕获取成功（{attempt_label}），共 {len(transcript.segments)} 段",
                    source="subtitle",
                    diagnostics=diagnostics,
                )
                return transcript

            last_subtitle_result = subtitle_result
            subtitle_message = (subtitle_result.message if subtitle_result else None) or "平台无可用字幕"
            has_next_attempt = attempt_index < len(subtitle_attempts)
            next_action = "继续尝试其他字幕策略" if has_next_attempt else (
                "准备下载音频并转写" if not allow_asr_fallback else "回退到音频转写"
            )
            logger.info(f"{subtitle_message}（{attempt_label}），{next_action}")
            self._update_status(
                task_id,
                status_phase,
                message="获取字幕/转写",
                detail=f"{subtitle_message}（{attempt_label}），{next_action}",
                source="subtitle",
                diagnostics=diagnostics,
            )

        if not allow_asr_fallback:
            return None
        if not audio_file:
            raise ValueError("字幕抓取失败且缺少音频文件，无法执行 ASR 回退")

        # 2. Fallback 到音频转写
        return self._transcribe_audio(
            audio_file=audio_file,
            transcript_cache_file=transcript_cache_file,
            status_phase=status_phase,
            force_refresh=force_refresh,
        )

    def _transcribe_audio(
        self,
        audio_file: str,
        transcript_cache_file: Path,
        status_phase: TaskStatus,
        force_refresh: bool = False,
    ) -> TranscriptResult | None:
        """
        1. 检查转写缓存；若存在则尝试加载，否则调用转写器生成并缓存。
        2. 返回 TranscriptResult 对象

        :param audio_file: 音频文件本地路径
        :param transcript_cache_file: 转写结果缓存路径
        :param status_phase: 对应的状态枚举，如 TaskStatus.TRANSCRIBING
        :return: TranscriptResult 对象
        """
        task_id = transcript_cache_file.stem.split("_")[0]
        self._update_status(
            task_id,
            status_phase,
            message="获取字幕/转写",
            detail="正在准备音频转写",
            source="asr",
        )

        # 已有缓存，尝试加载
        if transcript_cache_file.exists() and not force_refresh:
            logger.info(f"检测到转写缓存 ({transcript_cache_file})，尝试读取")
            try:
                data = json.loads(transcript_cache_file.read_text(encoding="utf-8"))
                segments = [TranscriptSegment(**seg) for seg in data.get("segments", [])]
                self._update_status(
                    task_id,
                    status_phase,
                    message="获取字幕/转写",
                    detail="命中转写缓存，跳过 ASR",
                    source="cache",
                )
                return TranscriptResult(language=data["language"], full_text=data["full_text"], segments=segments)
            except Exception as e:
                logger.warning(f"加载转写缓存失败，将重新转写：{e}")

        # 调用转写器
        try:
            self._update_status(
                task_id,
                status_phase,
                message="获取字幕/转写",
                detail=f"等待转写器资源（{self.transcriber_type}）",
                source="asr",
            )
            with ASR_LOCK:
                self._update_status(
                    task_id,
                    status_phase,
                    message="获取字幕/转写",
                    detail=f"正在执行 {self.transcriber_type} 音频转写",
                    source="asr",
                )
                logger.info("开始转写音频")
                transcriber = self._ensure_transcriber()
                transcript = transcriber.transcript(file_path=audio_file)
            transcript_cache_file.write_text(json.dumps(asdict(transcript), ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"转写并缓存成功 ({transcript_cache_file})")
            self._update_status(
                task_id,
                status_phase,
                message="获取字幕/转写",
                detail=f"ASR 转写完成，共 {len(transcript.segments)} 段",
                source="asr",
            )
            return transcript
        except Exception as exc:
            logger.error(f"音频转写失败：{exc}")
            self._handle_exception(task_id, exc, reason_code="ASR_TRANSCRIBE_FAILED")
            raise

    def _summarize_text(
        self,
        audio_meta: AudioDownloadResult,
        transcript: TranscriptResult,
        gpt: GPT,
        markdown_cache_file: Path,
        link: bool,
        screenshot: bool,
        formats: List[str],
        style: Optional[str],
        extras: Optional[str],
        formal_transcript_style: str,
        video_img_urls: List[str],
        task_id: Optional[str],
    ) -> str | None:
        """
        调用 GPT 对转写结果进行总结，生成 Markdown 文本并缓存。

        :param audio_meta: AudioDownloadResult 元信息
        :param transcript: TranscriptResult 转写结果
        :param gpt: GPT 实例
        :param markdown_cache_file: Markdown 缓存路径
        :param link: 是否在笔记中插入链接
        :param screenshot: 是否在笔记中生成截图占位
        :param formats: 包含 'link' 或 'screenshot' 的列表
        :param style: GPT 输出风格
        :param extras: GPT 额外参数
        :return: 生成的 Markdown 字符串
        """
        model_name = getattr(gpt, "model", "") or ""
        segment_text_for_budget = self._build_segment_text_for_budget(transcript.segments)
        estimated_tokens = estimate_tokens(
            f"{audio_meta.title}\n{audio_meta.raw_info.get('tags', '')}\n{segment_text_for_budget}\n{extras or ''}"
        )
        context_limit = resolve_context_limit(model_name)
        output_budget = max(min(FORMAL_TRANSCRIPT_OUTPUT_RESERVE, context_limit // 2), 1200)

        effective_formats = list(formats or [])
        include_formal_transcript = "formal_transcript" in effective_formats
        formal_tokens = estimate_tokens(segment_text_for_budget) if include_formal_transcript else 0
        overflow = include_formal_transcript and is_overflow(
            estimated_input=estimated_tokens,
            context_limit=context_limit,
            reserve_output=FORMAL_TRANSCRIPT_OUTPUT_RESERVE,
        )
        chunk_reasons: List[str] = []
        if include_formal_transcript:
            if overflow:
                chunk_reasons.append("input_overflow")
            if formal_tokens > output_budget:
                chunk_reasons.append("formal_tokens_exceed_output_budget")
            if estimated_tokens + formal_tokens > context_limit:
                chunk_reasons.append("combined_input_formal_exceed_context")

        need_chunked_formal = include_formal_transcript and bool(chunk_reasons)
        chunk_reason = ",".join(chunk_reasons) if chunk_reasons else "none"

        self._update_status(
            task_id,
            TaskStatus.SUMMARIZING,
            message="总结内容",
            detail="正在调用大模型生成笔记",
            source="gpt",
            diagnostics={
                "formal_transcript_mode": "chunked" if need_chunked_formal else ("single" if include_formal_transcript else "disabled"),
                "estimated_tokens": estimated_tokens,
                "context_limit": context_limit,
                "formal_tokens": formal_tokens,
                "output_budget": output_budget,
                "chunk_reason": chunk_reason,
            },
        )

        if need_chunked_formal:
            effective_formats = [item for item in effective_formats if item != "formal_transcript"]
            self._update_status(
                task_id,
                TaskStatus.SUMMARIZING,
                message="总结内容",
                detail="检测到正式文稿可能超长，切换为分块生成",
                source="gpt",
                diagnostics={
                    "formal_transcript_mode": "chunked",
                    "estimated_tokens": estimated_tokens,
                    "context_limit": context_limit,
                    "formal_tokens": formal_tokens,
                    "output_budget": output_budget,
                    "chunk_reason": chunk_reason,
                },
            )

        source = GPTSource(
            title=audio_meta.title,
            segment=transcript.segments,
            tags=audio_meta.raw_info.get("tags", []),
            screenshot=screenshot,
            video_img_urls=video_img_urls,
            link=link,
            _format=effective_formats,
            style=style,
            extras=extras,
            formal_transcript_style=formal_transcript_style,
        )

        try:
            markdown = gpt.summarize(source)

            formal_section_detected = False
            chunk_count = 0
            merge_strategy: Optional[str] = None
            if include_formal_transcript and need_chunked_formal:
                formal_text, chunk_count, merge_strategy = self._generate_formal_transcript_chunked(
                    gpt=gpt,
                    transcript=transcript,
                    title=audio_meta.title,
                    task_id=task_id,
                    context_limit=context_limit,
                    formal_transcript_style=formal_transcript_style,
                    output_budget=output_budget,
                )
                markdown = self._append_formal_transcript_at_end(markdown=markdown, formal_text=formal_text)
                formal_section_detected = True
            elif include_formal_transcript:
                markdown, formal_section_detected = self._ensure_formal_transcript_at_end(markdown)

            markdown_cache_file.write_text(markdown, encoding="utf-8")
            logger.info(f"GPT 总结并缓存成功 ({markdown_cache_file})")

            diagnostics = {
                "formal_transcript_mode": "chunked" if need_chunked_formal else ("single" if include_formal_transcript else "disabled"),
                "estimated_tokens": estimated_tokens,
                "context_limit": context_limit,
                "formal_tokens": formal_tokens,
                "output_budget": output_budget,
                "chunk_reason": chunk_reason,
                "formal_section_detected": formal_section_detected,
            }
            if chunk_count:
                diagnostics["chunk_count"] = chunk_count
            if merge_strategy:
                diagnostics["merge_strategy"] = merge_strategy

            self._update_status(
                task_id,
                TaskStatus.SUMMARIZING,
                message="总结内容",
                detail="大模型总结完成",
                source="gpt",
                diagnostics=diagnostics,
            )
            return markdown
        except Exception as exc:
            logger.error(f"GPT 总结失败：{exc}")
            self._handle_exception(task_id, exc, reason_code="GPT_SUMMARY_FAILED")
            raise

    @staticmethod
    def _format_mmss(seconds: float) -> str:
        total_seconds = max(int(seconds), 0)
        minutes = total_seconds // 60
        remaining_seconds = total_seconds % 60
        return f"{minutes:02d}:{remaining_seconds:02d}"

    def _build_segment_text_for_budget(self, segments: List[TranscriptSegment]) -> str:
        lines = []
        for seg in segments:
            text = (seg.text or "").strip()
            if not text:
                continue
            lines.append(f"{self._format_mmss(seg.start)} - {text}")
        return "\n".join(lines)

    def _chunk_transcript_segments(
        self,
        segments: List[TranscriptSegment],
        target_tokens: int,
    ) -> List[List[TranscriptSegment]]:
        safe_target = max(target_tokens, 1000)
        chunks: List[List[TranscriptSegment]] = []
        current_chunk: List[TranscriptSegment] = []
        current_tokens = 0

        for seg in segments:
            seg_text = (seg.text or "").strip()
            if not seg_text:
                continue
            seg_line = f"{self._format_mmss(seg.start)} - {seg_text}"
            seg_tokens = estimate_tokens(seg_line) + 20
            if current_chunk and current_tokens + seg_tokens > safe_target:
                chunks.append(current_chunk)
                current_chunk = []
                current_tokens = 0
            current_chunk.append(seg)
            current_tokens += seg_tokens

        if current_chunk:
            chunks.append(current_chunk)

        return chunks or [segments]

    def _build_formal_chunk_prompt(
        self,
        title: str,
        chunk_text: str,
        chunk_index: int,
        chunk_count: int,
        formal_transcript_style: str,
    ) -> str:
        style_key = (formal_transcript_style or "auto").strip().lower()
        style_map = {
            "auto": FORMAL_TRANSCRIPT_STYLE_AUTO,
            "single_narration": FORMAL_TRANSCRIPT_STYLE_SINGLE_NARRATION,
            "lead_plus_support": FORMAL_TRANSCRIPT_STYLE_LEAD_PLUS_SUPPORT,
            "multi_dialogue": FORMAL_TRANSCRIPT_STYLE_MULTI_DIALOGUE,
        }
        style_block = style_map.get(style_key, FORMAL_TRANSCRIPT_STYLE_AUTO)
        return f"""
你是专业中文编辑。请对下面的口语化转写生成“正式文稿”的一部分（将被拼接到 `## 正式文稿` 章节中）。

视频标题：{title}
当前片段：第 {chunk_index}/{chunk_count} 块

总体要求（高优先级）：
1. 不是总结；按原字幕逻辑改写为可读文稿，尽量保留主要内容、顺序与细节（事实/术语/数字/结论/例子/因果链路）。
2. 中度润色：修正病句与重复赘词、清理口头禅/语气词；不得杜撰、不得新增原文没有的信息；不要改写成提纲或摘要。
3. 多讲述者：优先使用可识别角色名（主持人/嘉宾/旁白/采访者等），否则使用“讲述者1/2/3...”兜底；不确定是否换人时保守延续上一讲述者；抢话/被打断按时间顺序线性还原，必要时用极短提示（如“（打断）”“（接话）”）。
4. 非语音信息：删除无意义噪声词；仅保留影响理解的舞台信息（如[笑]/[音乐]/[停顿]等）。
5. 禁止时间标记：不要输出 `*Content-[mm:ss]`、`*Screenshot-[mm:ss]`、`[mm:ss]`、以及 `mm:ss -` 开头的行等任何时间戳形式。
6. 输出形态：只输出正文段落（可带”角色名：/讲述者N：”作为段首），不要标题、列表、表格、代码块。
7. 长度校准：本块输出字数应与输入转写文本**大体相当**（允许 ±20% 偏差）；禁止以”精炼”为由大量删减原文内容；输入越长，输出也应越长。
8. 情感保留：保留讲述者的情绪基调与表达强度（激动/幽默/感慨/严肃等）；不得将情绪化表达改写成平淡叙述；在合理范围内保留感叹句、反问句、强调句等。

样式约束：
{style_block}

转写内容：
---
{chunk_text}
---
""".strip()

    def _build_formal_merge_prompt(
        self,
        title: str,
        part_texts: List[str],
        round_index: int,
        group_index: int,
        group_count: int,
        formal_transcript_style: str,
    ) -> str:
        style_key = (formal_transcript_style or "auto").strip().lower()
        style_map = {
            "auto": FORMAL_TRANSCRIPT_STYLE_AUTO,
            "single_narration": FORMAL_TRANSCRIPT_STYLE_SINGLE_NARRATION,
            "lead_plus_support": FORMAL_TRANSCRIPT_STYLE_LEAD_PLUS_SUPPORT,
            "multi_dialogue": FORMAL_TRANSCRIPT_STYLE_MULTI_DIALOGUE,
        }
        style_block = style_map.get(style_key, FORMAL_TRANSCRIPT_STYLE_AUTO)
        merged_source = "\n\n".join(
            [f"[片段{i}]\n{text}" for i, text in enumerate(part_texts, start=1)]
        )
        return f"""
你是专业中文编辑。请将以下多段“正式文稿片段”进行拼接润色，生成统一连贯的正式文稿正文（将放入 `## 正式文稿` 章节中）。

视频标题：{title}
合并轮次：第 {round_index} 轮
当前分组：第 {group_index}/{group_count} 组

要求（高优先级）：
1. 不遗漏事实、数字、术语和结论，不新增观点与结论。
2. 保持原始内容顺序与信息密度，仅做去重、断句与句间衔接；禁止生成总结式、提纲式输出。
3. 讲述者标签需全局一致：优先角色名；无法判断则用“讲述者1/2/3...”兜底；如不同片段对同一人标签不一致，合并时尽量归一；不确定时不要强行合并或改写；不要在后文改变同一人的编号。
4. 非语音信息：删除无意义噪声词；仅保留影响理解的舞台信息（如[笑]/[音乐]/[停顿]等）。
5. 禁止时间标记：删除/避免任何 `*Content-[mm:ss]`、`*Screenshot-[mm:ss]`、`[mm:ss]`、以及 `mm:ss -` 开头的行等形式。
6. 只输出合并后的正文段落，不要标题、列表、表格、代码块。
7. 长度校准：合并后的总字数**不得显著少于**各片段字数之和（允许去重带来的小幅减少，但禁止大量压缩）；禁止以"精炼"或"整合"为由删减大量原文内容。
8. 情感保留：保持各片段的情绪基调与表达强度，不要在合并润色中将情感化表达改写成平淡叙述。

样式约束：
{style_block}

待合并内容：
---
{merged_source}
---
""".strip()

    def _group_text_by_token_budget(self, texts: List[str], token_budget: int) -> List[List[str]]:
        safe_budget = max(token_budget, 1200)
        groups: List[List[str]] = []
        current_group: List[str] = []
        current_tokens = 0

        for text in texts:
            text_tokens = estimate_tokens(text) + 80
            if current_group and current_tokens + text_tokens > safe_budget:
                groups.append(current_group)
                current_group = []
                current_tokens = 0

            current_group.append(text)
            current_tokens += text_tokens

        if current_group:
            groups.append(current_group)

        # Prevent infinite loop when every group contains exactly one part.
        if len(groups) == len(texts) and len(texts) > 1:
            regrouped: List[List[str]] = []
            for idx in range(0, len(texts), 2):
                regrouped.append(texts[idx:idx + 2])
            return regrouped

        return groups

    def _chat_text_with_retry(
        self,
        gpt: GPT,
        prompt: str,
        max_attempts: int = 2,
    ) -> str:
        last_exception: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            try:
                content = gpt.chat_text(prompt, temperature=0.2)
                content = (content or "").strip()
                if not content:
                    raise RuntimeError("模型返回空文本")
                return content
            except Exception as exc:
                last_exception = exc
                logger.warning(f"模型调用失败 (attempt={attempt}/{max_attempts}): {exc}")

        raise RuntimeError("模型调用多次失败") from last_exception

    def _merge_formal_transcript_parts(
        self,
        gpt: GPT,
        title: str,
        parts: List[str],
        merge_target: int,
        formal_transcript_style: str,
    ) -> str:
        if not parts:
            return ""

        round_index = 1
        current_parts = [part.strip() for part in parts if part and part.strip()]
        while len(current_parts) > 1:
            groups = self._group_text_by_token_budget(current_parts, token_budget=merge_target)
            merged_parts: List[str] = []
            total_groups = len(groups)
            for group_index, group in enumerate(groups, start=1):
                if len(group) == 1:
                    merged_parts.append(group[0])
                    continue
                prompt = self._build_formal_merge_prompt(
                    title=title,
                    part_texts=group,
                    round_index=round_index,
                    group_index=group_index,
                    group_count=total_groups,
                    formal_transcript_style=formal_transcript_style,
                )
                merged = self._chat_text_with_retry(gpt=gpt, prompt=prompt, max_attempts=2)
                merged_parts.append(merged)
            current_parts = merged_parts
            round_index += 1

        return current_parts[0]

    def _generate_formal_transcript_chunked(
        self,
        gpt: GPT,
        transcript: TranscriptResult,
        title: str,
        task_id: Optional[str],
        context_limit: int,
        formal_transcript_style: str,
        output_budget: int,
    ) -> Tuple[str, int, str]:
        # Keep chunk/merge requests within a conservative output budget to reduce truncation risk.
        chunk_target = min(FORMAL_TRANSCRIPT_CHUNK_TARGET, output_budget)
        merge_target = min(FORMAL_TRANSCRIPT_MERGE_TARGET, output_budget)
        chunks = self._chunk_transcript_segments(transcript.segments, target_tokens=chunk_target)
        chunk_count = len(chunks)
        generated_parts: List[str] = []

        for chunk_index, chunk_segments in enumerate(chunks, start=1):
            self._update_status(
                task_id,
                TaskStatus.SUMMARIZING,
                message="总结内容",
                detail=f"正式文稿分块生成 {chunk_index}/{chunk_count}",
                source="gpt",
                diagnostics={
                    "formal_transcript_mode": "chunked",
                    "chunk_count": chunk_count,
                    "current_chunk": chunk_index,
                    "output_budget": output_budget,
                },
            )
            chunk_text = self._build_segment_text_for_budget(chunk_segments)
            prompt = self._build_formal_chunk_prompt(
                title=title,
                chunk_text=chunk_text,
                chunk_index=chunk_index,
                chunk_count=chunk_count,
                formal_transcript_style=formal_transcript_style,
            )
            try:
                chunk_content = self._chat_text_with_retry(gpt=gpt, prompt=prompt, max_attempts=2)
            except Exception as exc:
                raise RuntimeError(f"正式文稿分块生成失败 (chunk={chunk_index}/{chunk_count}): {exc}") from exc
            generated_parts.append(chunk_content)

        generated_total_tokens = sum(estimate_tokens(part) for part in generated_parts if part)
        if generated_total_tokens > output_budget:
            merge_strategy = "concat"
            self._update_status(
                task_id,
                TaskStatus.SUMMARIZING,
                message="总结内容",
                detail="正式文稿分块拼接中（跳过合并润色以避免截断）",
                source="gpt",
                diagnostics={
                    "formal_transcript_mode": "chunked",
                    "chunk_count": chunk_count,
                    "current_chunk": chunk_count,
                    "output_budget": output_budget,
                    "generated_part_tokens": generated_total_tokens,
                    "merge_strategy": merge_strategy,
                },
            )
            formal_text = "\n\n".join(
                part.strip() for part in generated_parts if part and part.strip()
            )
        else:
            merge_strategy = "llm_merge"
            self._update_status(
                task_id,
                TaskStatus.SUMMARIZING,
                message="总结内容",
                detail="正式文稿合并润色中",
                source="gpt",
                diagnostics={
                    "formal_transcript_mode": "chunked",
                    "chunk_count": chunk_count,
                    "current_chunk": chunk_count,
                    "output_budget": output_budget,
                    "generated_part_tokens": generated_total_tokens,
                    "merge_strategy": merge_strategy,
                },
            )
            formal_text = self._merge_formal_transcript_parts(
                gpt=gpt,
                title=title,
                parts=generated_parts,
                merge_target=merge_target,
                formal_transcript_style=formal_transcript_style,
            )

        return formal_text, chunk_count, merge_strategy

    @staticmethod
    def _extract_formal_transcript_section(markdown: str) -> Tuple[str, Optional[str]]:
        heading_pattern = re.compile(r"^##\s*正式文稿\s*$", re.MULTILINE)
        match = heading_pattern.search(markdown or "")
        if not match:
            return (markdown or "").strip(), None

        before = (markdown[:match.start()] or "").strip()
        after_heading = markdown[match.end():]
        next_h2 = re.search(r"^##\s+.+$", after_heading, re.MULTILINE)
        if next_h2:
            formal_section = (after_heading[:next_h2.start()] or "").strip()
            trailing_content = (after_heading[next_h2.start():] or "").strip()
        else:
            formal_section = (after_heading or "").strip()
            trailing_content = ""

        main_parts = [part for part in (before, trailing_content) if part]
        main_markdown = "\n\n".join(main_parts).strip()
        return main_markdown, formal_section

    @staticmethod
    def _normalize_formal_transcript_body(formal_text: str) -> str:
        text = (formal_text or "").strip()
        if not text:
            return ""

        # Remove optional fenced markdown wrapper.
        if text.startswith("```") and text.endswith("```"):
            lines = text.splitlines()
            if len(lines) >= 2:
                text = "\n".join(lines[1:-1]).strip()

        text = re.sub(r"^\s*##\s*正式文稿\s*\n?", "", text, count=1)
        text = NoteGenerator._sanitize_formal_transcript_body(text)
        return text.strip()

    @staticmethod
    def _sanitize_formal_transcript_body(formal_text: str) -> str:
        return sanitize_formal_transcript_body(formal_text)

    def _append_formal_transcript_at_end(self, markdown: str, formal_text: str) -> str:
        main_markdown, extracted_formal = self._extract_formal_transcript_section(markdown)
        body = self._normalize_formal_transcript_body(formal_text or extracted_formal or "")
        if not body:
            return (main_markdown or markdown or "").strip()

        if main_markdown:
            return f"{main_markdown}\n\n## 正式文稿\n\n{body}".strip()
        return f"## 正式文稿\n\n{body}".strip()

    def _ensure_formal_transcript_at_end(self, markdown: str) -> Tuple[str, bool]:
        main_markdown, extracted_formal = self._extract_formal_transcript_section(markdown)
        if not extracted_formal:
            return markdown, False
        normalized = self._append_formal_transcript_at_end(main_markdown, extracted_formal)
        return normalized, True

    def _post_process_markdown(
        self,
        markdown: str,
        video_path: Optional[Path],
        formats: List[str],
        audio_meta: AudioDownloadResult,
        platform: str,
        task_id: Optional[str] = None,
    ) -> str:
        """
        对生成的 Markdown 做后期处理：插入截图和/或插入链接。

        :param markdown: 原始 Markdown 字符串
        :param video_path: 本地视频路径（可为 None）
        :param formats: 包含 'link' 或 'screenshot' 的列表
        :param audio_meta: AudioDownloadResult 元信息，用于链接替换
        :param platform: 平台标识，用于链接替换
        :return: 处理后的 Markdown 字符串
        """
        if "screenshot" in formats and video_path and task_id:
            self._update_status(
                task_id,
                TaskStatus.FORMATTING,
                message="格式化内容",
                detail="正在插入截图",
                source="system",
            )
            try:
                markdown = self._insert_screenshots(markdown, video_path, task_id=task_id)
            except Exception as exc:
                logger.warning("截图插入失败，跳过该步骤")
                self._update_status(
                    task_id,
                    TaskStatus.FORMATTING,
                    message="格式化内容",
                    detail=f"截图插入失败，已跳过：{exc}",
                    source="system",
                )

        if "link" in formats:
            self._update_status(
                task_id,
                TaskStatus.FORMATTING,
                message="格式化内容",
                detail="正在替换原片跳转链接",
                source="system",
            )
            try:
                markdown = replace_content_markers(markdown, video_id=audio_meta.video_id, platform=platform)
            except Exception as e:
                logger.warning(f"链接插入失败，跳过该步骤：{e}")
                self._update_status(
                    task_id,
                    TaskStatus.FORMATTING,
                    message="格式化内容",
                    detail=f"链接插入失败，已跳过：{e}",
                    source="system",
                )

        return markdown

    def _insert_screenshots(self, markdown: str, video_path: Path, task_id: str) -> str:
        """
        扫描 Markdown 文本中所有 Screenshot 标记，并替换为实际生成的截图链接。

        :param markdown: 含有 *Screenshot-mm:ss 或 Screenshot-[mm:ss] 标记的 Markdown 文本
        :param video_path: 本地视频文件路径
        :param task_id: 任务 ID，用于定位任务附件目录
        :return: 替换后的 Markdown 字符串
        """
        output_dir = get_task_assets_dir(task_id)
        matches: List[Tuple[str, int]] = self._extract_screenshot_timestamps(markdown)
        for idx, (marker, ts) in enumerate(matches):
            try:
                img_path = generate_screenshot(str(video_path), str(output_dir), ts, idx)
                filename = Path(img_path).name
                # 使用任务内相对路径，支持导出后离线阅读。
                img_url = f"./assets/{filename}"
                markdown = markdown.replace(marker, f"![]({img_url})", 1)
            except Exception as exc:
                logger.error(f"生成截图失败 (timestamp={ts})：{exc}")
                # 保留原始 marker，不中断整篇笔记生成。
                continue
        return markdown

    def _save_task_markdown(self, task_id: Optional[str], markdown: Optional[str]) -> None:
        if not task_id or markdown is None:
            return
        try:
            note_path = get_task_note_path(task_id)
            note_path.write_text(markdown, encoding="utf-8")
        except Exception as exc:
            logger.warning(f"写入任务 note.md 失败 (task_id={task_id})：{exc}")

    @staticmethod
    def _extract_screenshot_timestamps(markdown: str) -> List[Tuple[str, int]]:
        """
        从 Markdown 文本中提取所有 '*Screenshot-mm:ss' 或 'Screenshot-[mm:ss]' 标记，
        返回 [(原始标记文本, 时间戳秒数), ...] 列表。

        :param markdown: 原始 Markdown 文本
        :return: 标记与对应时间戳秒数的列表
        """
        pattern = r"(?:\*Screenshot-(\d{2}):(\d{2})|Screenshot-\[(\d{2}):(\d{2})\])"
        results: List[Tuple[str, int]] = []
        for match in re.finditer(pattern, markdown):
            mm = match.group(1) or match.group(3)
            ss = match.group(2) or match.group(4)
            total_seconds = int(mm) * 60 + int(ss)
            results.append((match.group(0), total_seconds))
        return results

    def _save_metadata(self, video_id: str, platform: str, task_id: str) -> None:
        """
        将生成的笔记任务记录插入数据库

        :param video_id: 视频 ID
        :param platform: 平台标识
        :param task_id: 任务 ID
        """
        try:
            insert_video_task(video_id=video_id, platform=platform, task_id=task_id)
            logger.info(f"已保存任务记录到数据库 (video_id={video_id}, platform={platform}, task_id={task_id})")
        except Exception as e:
            logger.error(f"保存任务记录失败：{e}")
