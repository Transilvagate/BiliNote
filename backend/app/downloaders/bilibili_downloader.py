import os
import json
import logging
import re
from abc import ABC
from typing import Any, Dict, Union, Optional, List, Tuple
from pathlib import Path

import yt_dlp

from app.downloaders.base import Downloader, DownloadQuality
from app.models.notes_model import AudioDownloadResult
from app.models.transcriber_model import TranscriptResult, TranscriptSegment, SubtitleFetchResult
from app.services.bbdown_client import BBDownClient
from app.services.bilibili_cookie_service import BilibiliCookieService
from app.utils.path_helper import get_data_dir
from app.utils.url_parser import extract_video_id

logger = logging.getLogger(__name__)


class BilibiliDownloader(Downloader, ABC):
    def __init__(self):
        super().__init__()
        self.cookie_service = BilibiliCookieService()
        self.bbdown_client = BBDownClient()
        self.subtitle_provider = (os.getenv("BILIBILI_SUBTITLE_PROVIDER", "bbdown") or "bbdown").strip().lower()

    def _resolve_output_dir(self, output_dir: Optional[str]) -> str:
        if output_dir is None:
            output_dir = get_data_dir()
        if not output_dir:
            output_dir = self.cache_data
        os.makedirs(output_dir, exist_ok=True)
        return output_dir

    def _apply_cookie_file(
        self,
        ydl_opts: Dict[str, Any],
        diagnostics: Optional[Dict[str, Any]] = None,
    ) -> None:
        migration_info = self.cookie_service.migrate_legacy_cookie_if_needed()
        cookies_path = self.cookie_service.resolve_cookie_file_path()
        if diagnostics is not None:
            diagnostics["cookie_migration"] = migration_info
            diagnostics["cookies_path"] = str(cookies_path)
            diagnostics["cookie_source"] = migration_info.get("source") or "missing"
            diagnostics["cookie_exists"] = bool(cookies_path.exists())
        if cookies_path.exists():
            ydl_opts["cookiefile"] = str(cookies_path)

    @staticmethod
    def _build_audio_result_from_info(
        info: Dict[str, Any],
        output_dir: str,
        ext: str = "mp3",
    ) -> AudioDownloadResult:
        video_id = info.get("id")
        title = info.get("title")
        duration = info.get("duration", 0)
        cover_url = info.get("thumbnail")
        file_path = os.path.join(output_dir, f"{video_id}.{ext}") if video_id else ""
        return AudioDownloadResult(
            file_path=file_path,
            title=title,
            duration=duration,
            cover_url=cover_url,
            platform="bilibili",
            video_id=video_id,
            raw_info=info,
            video_path=None,
        )

    def download(
        self,
        video_url: str,
        output_dir: Union[str, None] = None,
        quality: DownloadQuality = "fast",
        need_video: Optional[bool] = False
    ) -> AudioDownloadResult:
        output_dir = self._resolve_output_dir(output_dir)

        output_path = os.path.join(output_dir, "%(id)s.%(ext)s")

        ydl_opts = {
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "outtmpl": output_path,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "64",
                }
            ],
            "noplaylist": True,
            "quiet": False,
        }
        self._apply_cookie_file(ydl_opts)

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
        return self._build_audio_result_from_info(info, output_dir=output_dir, ext="mp3")

    def get_media_info(self, video_url: str, output_dir: str = None) -> Optional[AudioDownloadResult]:
        output_dir = self._resolve_output_dir(output_dir)
        ydl_opts: Dict[str, Any] = {
            "skip_download": True,
            "noplaylist": True,
            "quiet": True,
        }
        self._apply_cookie_file(ydl_opts)
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
            return self._build_audio_result_from_info(info, output_dir=output_dir, ext="mp3")
        except Exception as exc:
            logger.warning(f"获取 B站媒体元信息失败: {exc}")
            return None

    def download_video(
        self,
        video_url: str,
        output_dir: Union[str, None] = None,
    ) -> str:
        """
        下载视频，返回视频文件路径
        """

        output_dir = self._resolve_output_dir(output_dir)
        video_id = extract_video_id(video_url, "bilibili")
        video_path = os.path.join(output_dir, f"{video_id}.mp4")
        if os.path.exists(video_path):
            return video_path

        output_path = os.path.join(output_dir, "%(id)s.%(ext)s")

        ydl_opts = {
            "format": "bv*[ext=mp4]/bestvideo+bestaudio/best",
            "outtmpl": output_path,
            "noplaylist": True,
            "quiet": False,
            "merge_output_format": "mp4",
        }
        self._apply_cookie_file(ydl_opts)

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            video_id = info.get("id")
            video_path = os.path.join(output_dir, f"{video_id}.mp4")

        if not os.path.exists(video_path):
            raise FileNotFoundError(f"视频文件未找到: {video_path}")

        return video_path

    def delete_video(self, video_path: str) -> str:
        """
        删除视频文件
        """
        if os.path.exists(video_path):
            os.remove(video_path)
            return f"视频文件已删除: {video_path}"
        else:
            return f"视频文件未找到: {video_path}"

    def download_subtitles(self, video_url: str, output_dir: str = None,
                           langs: List[str] = None) -> SubtitleFetchResult:
        """
        使用 BBDown 获取 B站字幕

        :param video_url: 视频链接
        :param output_dir: 输出路径
        :param langs: 优先语言列表
        :return: SubtitleFetchResult
        """
        output_dir = self._resolve_output_dir(output_dir)
        if langs is None:
            langs = ["zh-Hans", "zh", "zh-CN", "ai-zh", "en", "en-US"]

        if self.subtitle_provider not in {"bbdown", "auto"}:
            return SubtitleFetchResult(
                outcome="error",
                reason_code="BILIBILI_SUBTITLE_PROVIDER_UNSUPPORTED",
                message=f"B站字幕提供方配置不受支持: {self.subtitle_provider}",
                diagnostics={"provider": self.subtitle_provider},
            )
        return self._download_subtitles_with_bbdown(video_url=video_url, output_dir=output_dir, langs=langs)

    def _download_subtitles_with_bbdown(
        self,
        video_url: str,
        output_dir: str,
        langs: List[str],
    ) -> SubtitleFetchResult:
        diagnostics: Dict[str, Any] = {
            "platform": "bilibili",
            "provider": "bbdown",
            "output_dir_hint": output_dir,
        }
        bbdown_work_dir = self.bbdown_client.resolve_work_dir()
        cookie_status = self.cookie_service.get_cookie_status()
        cookies_file = self.cookie_service.resolve_cookie_file_path()
        diagnostics["cookies_path"] = str(cookies_file)
        diagnostics["cookie_status"] = cookie_status
        diagnostics["bbdown_bin"] = self.bbdown_client.bin_path
        diagnostics["bbdown_work_dir"] = str(bbdown_work_dir)

        run_result = self.bbdown_client.run_subtitle_download(
            video_url=video_url,
            output_dir=bbdown_work_dir,
            cookie_file=cookies_file if cookies_file.exists() else None,
        )
        diagnostics["bbdown_result"] = run_result.get("diagnostics", {})

        if not run_result.get("success"):
            return SubtitleFetchResult(
                outcome="error" if run_result.get("reason_code") != "SUBTITLE_NOT_AVAILABLE" else "unavailable",
                reason_code=str(run_result.get("reason_code") or "SUBTITLE_FETCH_EXCEPTION"),
                message=str(run_result.get("message") or "字幕抓取失败"),
                diagnostics=diagnostics,
            )

        subtitle_files = [str(path) for path in (run_result.get("subtitle_files") or []) if path]
        if not subtitle_files:
            return SubtitleFetchResult(
                outcome="unavailable",
                reason_code="SUBTITLE_NOT_AVAILABLE",
                message="BBDown 未返回可用字幕文件",
                diagnostics=diagnostics,
            )

        subtitle_file = self._select_subtitle_file(subtitle_files, langs)
        if not subtitle_file:
            return SubtitleFetchResult(
                outcome="error",
                reason_code="SUBTITLE_FILE_NOT_FOUND",
                message="无法定位可解析字幕文件",
                diagnostics={**diagnostics, "subtitle_files": subtitle_files},
            )

        ext = Path(subtitle_file).suffix.lower().lstrip(".")
        lang = self._guess_lang_from_filename(subtitle_file, langs)
        transcript = self._parse_subtitle_file(subtitle_file=subtitle_file, language=lang, ext=ext)
        if not transcript:
            return SubtitleFetchResult(
                outcome="error",
                reason_code="SUBTITLE_PARSE_FAILED",
                message="字幕文件解析失败",
                diagnostics={**diagnostics, "subtitle_file": subtitle_file},
            )

        transcript.raw = {
            "source": "bilibili_subtitle_bbdown",
            "format": ext,
            "language": lang,
            "file": subtitle_file,
        }
        return SubtitleFetchResult(
            transcript=transcript,
            outcome="success",
            message="已使用 BBDown 字幕",
            diagnostics={**diagnostics, "subtitle_file": subtitle_file},
        )

    @staticmethod
    def _select_subtitle_file(file_paths: List[str], langs: List[str]) -> Optional[str]:
        if not file_paths:
            return None
        ext_priority = {"srt": 0, "vtt": 1, "json3": 2, "json": 3, "ass": 4, "ssa": 5}
        lang_tokens = [lang.lower() for lang in (langs or [])]

        def sort_key(path_str: str) -> Tuple[int, int, str]:
            path = Path(path_str)
            ext = path.suffix.lower().lstrip(".")
            name = path.name.lower()
            lang_rank = 1
            for token in lang_tokens:
                if token and token in name:
                    lang_rank = 0
                    break
            return (lang_rank, ext_priority.get(ext, 9), path.name)

        return sorted(file_paths, key=sort_key)[0]

    @staticmethod
    def _guess_lang_from_filename(file_path: str, langs: List[str]) -> str:
        filename = Path(file_path).name.lower()
        for lang in langs:
            if lang and lang.lower() in filename:
                return lang
        return "zh"

    def _pick_subtitle(self, subtitles: Dict[str, Any], langs: List[str]) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        if not subtitles:
            return None, None

        for lang in langs:
            if lang in subtitles:
                normalized = self._normalize_sub_info(subtitles[lang])
                if normalized:
                    return lang, normalized

        for lang, info_item in subtitles.items():
            if lang == "danmaku":
                continue
            normalized = self._normalize_sub_info(info_item)
            if normalized:
                return lang, normalized

        return None, None

    @staticmethod
    def _normalize_sub_info(info_item: Any) -> Optional[Dict[str, Any]]:
        if isinstance(info_item, dict):
            return info_item
        if isinstance(info_item, list):
            for item in info_item:
                if isinstance(item, dict):
                    return item
        return None

    def _parse_subtitle_from_info(
        self,
        sub_info: Dict[str, Any],
        language: str,
        output_dir: str,
        video_id: str,
    ) -> Optional[TranscriptResult]:
        inline_data = sub_info.get("data")
        if isinstance(inline_data, str) and inline_data.strip():
            return self._parse_text_subtitle(inline_data, sub_info.get("ext", "srt"), language)
        if isinstance(inline_data, dict):
            return self._parse_json_subtitle_data(inline_data, language, sub_info.get("ext", "json"))

        subtitle_file = self._resolve_subtitle_file(sub_info, output_dir, video_id, language)
        if not subtitle_file:
            return None

        ext = Path(subtitle_file).suffix.lower().lstrip(".") or (sub_info.get("ext") or "srt")
        return self._parse_subtitle_file(subtitle_file, language, ext)

    def _resolve_subtitle_file(
        self,
        sub_info: Dict[str, Any],
        output_dir: str,
        video_id: str,
        language: str,
    ) -> Optional[str]:
        filepath = sub_info.get("filepath")
        if filepath and os.path.exists(filepath):
            return filepath

        ext = sub_info.get("ext", "srt")
        candidates = [
            os.path.join(output_dir, f"{video_id}.{language}.{ext}"),
            os.path.join(output_dir, f"{video_id}.{language}.json3"),
            os.path.join(output_dir, f"{video_id}.{language}.srt"),
            os.path.join(output_dir, f"{video_id}.{language}.vtt"),
            os.path.join(output_dir, f"{video_id}.{language}.json"),
        ]
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate

        logger.info(f"字幕文件不存在（video_id={video_id}, language={language}）")
        return None

    def _parse_subtitle_file(self, subtitle_file: str, language: str, ext: str) -> Optional[TranscriptResult]:
        try:
            with open(subtitle_file, "r", encoding="utf-8") as f:
                content = f.read()
            return self._parse_text_subtitle(content, ext, language)
        except Exception as e:
            logger.warning(f"读取字幕文件失败: {e}")
            return None

    def _parse_text_subtitle(self, content: str, ext: str, language: str) -> Optional[TranscriptResult]:
        ext = (ext or "").lower()
        if ext == "json3":
            try:
                return self._parse_json3_subtitle_data(json.loads(content), language)
            except Exception as e:
                logger.warning(f"解析 json3 字幕失败: {e}")
                return None
        if ext == "json":
            try:
                return self._parse_json_subtitle_data(json.loads(content), language, ext)
            except Exception as e:
                logger.warning(f"解析 json 字幕失败: {e}")
                return None
        if ext == "vtt":
            return self._parse_vtt_content(content, language)
        if ext in {"ass", "ssa"}:
            return self._parse_ass_content(content, language)
        return self._parse_srt_content(content, language)

    def _parse_ass_content(self, ass_content: str, language: str) -> Optional[TranscriptResult]:
        try:
            segments: List[TranscriptSegment] = []
            dialogue_pattern = re.compile(r"^Dialogue:\s*\d+,(.*)$")
            for raw_line in ass_content.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                match = dialogue_pattern.match(line)
                if not match:
                    continue
                payload = match.group(1)
                parts = payload.split(",", 9)
                if len(parts) < 10:
                    continue
                start_time = parts[0].strip()
                end_time = parts[1].strip()
                text = parts[9].strip()
                text = text.replace("\\N", " ").replace("\\n", " ")
                text = re.sub(r"\{[^{}]*\}", "", text).strip()
                if not text:
                    continue
                segments.append(
                    TranscriptSegment(
                        start=self._ass_time_to_seconds(start_time),
                        end=self._ass_time_to_seconds(end_time),
                        text=text,
                    )
                )
            if not segments:
                return None
            full_text = " ".join(seg.text for seg in segments)
            return TranscriptResult(
                language=language,
                full_text=full_text,
                segments=segments,
                raw={"source": "bilibili_subtitle", "format": "ass"},
            )
        except Exception as exc:
            logger.warning(f"解析 ASS 字幕失败: {exc}")
            return None

    @staticmethod
    def _ass_time_to_seconds(value: str) -> float:
        # ASS 时间格式: H:MM:SS.cc
        hh, mm, ss = value.split(":")
        return float(hh) * 3600 + float(mm) * 60 + float(ss)

    def _parse_srt_content(self, srt_content: str, language: str) -> Optional[TranscriptResult]:
        """
        解析 SRT 格式字幕内容

        :param srt_content: SRT 字幕文本内容
        :param language: 语言代码
        :return: TranscriptResult
        """
        try:
            segments = []
            # SRT 格式: 序号\n时间戳\n文本\n\n
            pattern = r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})\n(.*?)(?=\n\n|\n\d+\n|$)'
            matches = re.findall(pattern, srt_content, re.DOTALL)

            for match in matches:
                idx, start_time, end_time, text = match
                text = text.strip()
                if not text:
                    continue

                # 转换时间格式 00:00:00,000 -> 秒
                def time_to_seconds(t):
                    parts = t.replace(',', '.').split(':')
                    return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])

                segments.append(TranscriptSegment(
                    start=time_to_seconds(start_time),
                    end=time_to_seconds(end_time),
                    text=text
                ))

            if not segments:
                return None

            full_text = ' '.join(seg.text for seg in segments)
            logger.info(f"成功解析B站SRT字幕，共 {len(segments)} 段")
            return TranscriptResult(
                language=language,
                full_text=full_text,
                segments=segments,
                raw={'source': 'bilibili_subtitle', 'format': 'srt'}
            )

        except Exception as e:
            logger.warning(f"解析SRT字幕失败: {e}")
            return None

    def _parse_json3_subtitle(self, subtitle_file: str, language: str) -> Optional[TranscriptResult]:
        """
        解析 json3 格式字幕文件

        :param subtitle_file: 字幕文件路径
        :param language: 语言代码
        :return: TranscriptResult
        """
        try:
            with open(subtitle_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            segments = []
            events = data.get('events', [])

            for event in events:
                # json3 格式中时间单位是毫秒
                start_ms = event.get('tStartMs', 0)
                duration_ms = event.get('dDurationMs', 0)

                # 提取文本
                segs = event.get('segs', [])
                text = ''.join(seg.get('utf8', '') for seg in segs).strip()

                if text:  # 只添加非空文本
                    segments.append(TranscriptSegment(
                        start=start_ms / 1000.0,
                        end=(start_ms + duration_ms) / 1000.0,
                        text=text
                    ))

            if not segments:
                return None

            full_text = ' '.join(seg.text for seg in segments)

            logger.info(f"成功解析B站字幕，共 {len(segments)} 段")
            return TranscriptResult(
                language=language,
                full_text=full_text,
                segments=segments,
                raw={'source': 'bilibili_subtitle', 'file': subtitle_file}
            )

        except Exception as e:
            logger.warning(f"解析字幕文件失败: {e}")
            return None

    def _parse_json3_subtitle_data(self, data: Dict[str, Any], language: str) -> Optional[TranscriptResult]:
        try:
            segments = []
            events = data.get('events', [])
            for event in events:
                start_ms = event.get('tStartMs', 0)
                duration_ms = event.get('dDurationMs', 0)
                segs = event.get('segs', [])
                text = ''.join(seg.get('utf8', '') for seg in segs).strip()
                if text:
                    segments.append(
                        TranscriptSegment(
                            start=start_ms / 1000.0,
                            end=(start_ms + duration_ms) / 1000.0,
                            text=text,
                        )
                    )
            if not segments:
                return None
            full_text = ' '.join(seg.text for seg in segments)
            return TranscriptResult(language=language, full_text=full_text, segments=segments)
        except Exception as e:
            logger.warning(f"解析 json3 字幕数据失败: {e}")
            return None

    def _parse_json_subtitle_data(self, data: Any, language: str, ext: str) -> Optional[TranscriptResult]:
        try:
            segments: List[TranscriptSegment] = []
            if isinstance(data, dict) and isinstance(data.get("body"), list):
                for item in data["body"]:
                    text = (item.get("content") or "").strip()
                    start = float(item.get("from", 0))
                    end = float(item.get("to", start))
                    if text:
                        segments.append(TranscriptSegment(start=start, end=end, text=text))
            elif isinstance(data, dict) and isinstance(data.get("events"), list):
                return self._parse_json3_subtitle_data(data, language)
            elif isinstance(data, list):
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    text = (item.get("text") or item.get("content") or "").strip()
                    start = float(item.get("start", item.get("from", 0)))
                    end = float(item.get("end", item.get("to", start)))
                    if text:
                        segments.append(TranscriptSegment(start=start, end=end, text=text))

            if not segments:
                return None
            full_text = " ".join(seg.text for seg in segments)
            return TranscriptResult(
                language=language,
                full_text=full_text,
                segments=segments,
                raw={"source": "bilibili_subtitle", "format": ext},
            )
        except Exception as e:
            logger.warning(f"解析 json 字幕失败: {e}")
            return None

    def _parse_vtt_content(self, vtt_content: str, language: str) -> Optional[TranscriptResult]:
        try:
            segments = []
            pattern = (
                r"(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})\n"
                r"(.*?)(?=\n\n|\n\d{2}:\d{2}:\d{2}\.\d{3}\s*-->|$)"
            )
            matches = re.findall(pattern, vtt_content, re.DOTALL)
            for start_time, end_time, text in matches:
                clean_text = re.sub(r"<[^>]+>", "", text).strip()
                if not clean_text:
                    continue

                def time_to_seconds(value: str) -> float:
                    hh, mm, ss = value.split(":")
                    return float(hh) * 3600 + float(mm) * 60 + float(ss)

                segments.append(
                    TranscriptSegment(
                        start=time_to_seconds(start_time),
                        end=time_to_seconds(end_time),
                        text=clean_text,
                    )
                )

            if not segments:
                return None
            full_text = " ".join(seg.text for seg in segments)
            return TranscriptResult(
                language=language,
                full_text=full_text,
                segments=segments,
                raw={"source": "bilibili_subtitle", "format": "vtt"},
            )
        except Exception as e:
            logger.warning(f"解析 VTT 字幕失败: {e}")
            return None
