import os
import json
import logging
import re
from abc import ABC
from pathlib import Path
from typing import Any, Dict, Union, Optional, List, Tuple

import yt_dlp

from app.downloaders.base import Downloader, DownloadQuality
from app.downloaders.youtube_subtitle import YouTubeSubtitleFetcher
from app.models.notes_model import AudioDownloadResult
from app.models.transcriber_model import TranscriptResult, TranscriptSegment, SubtitleFetchResult
from app.utils.path_helper import get_data_dir
from app.utils.url_parser import extract_video_id

logger = logging.getLogger(__name__)


class YoutubeDownloader(Downloader, ABC):
    def __init__(self):

        super().__init__()

    def download(
        self,
        video_url: str,
        output_dir: Union[str, None] = None,
        quality: DownloadQuality = "fast",
        need_video: Optional[bool] = False,
        skip_download: bool = False,
    ) -> AudioDownloadResult:
        if output_dir is None:
            output_dir = get_data_dir()
        if not output_dir:
            output_dir = self.cache_data
        os.makedirs(output_dir, exist_ok=True)

        output_path = os.path.join(output_dir, "%(id)s.%(ext)s")

        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'outtmpl': output_path,
            'noplaylist': True,
            'quiet': False,
        }

        if skip_download:
            ydl_opts['skip_download'] = True

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=not skip_download)
            video_id = info.get("id")
            ext = info.get("ext", "m4a")
            audio_path = os.path.join(output_dir, f"{video_id}.{ext}")

        return AudioDownloadResult(
            file_path=audio_path,
            title=info.get("title"),
            duration=info.get("duration", 0),
            cover_url=info.get("thumbnail"),
            platform="youtube",
            video_id=video_id,
            raw_info={"tags": info.get("tags")},  # 全部返回会报错
            video_path=None,  # 音频下载不包含视频路径
        )

    def get_media_info(self, video_url: str, output_dir: str = None) -> Optional[AudioDownloadResult]:
        if output_dir is None:
            output_dir = get_data_dir()
        if not output_dir:
            output_dir = self.cache_data
        os.makedirs(output_dir, exist_ok=True)

        ydl_opts = {
            "skip_download": True,
            "noplaylist": True,
            "quiet": True,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
            video_id = info.get("id")
            return AudioDownloadResult(
                file_path=os.path.join(output_dir, f"{video_id}.m4a") if video_id else "",
                title=info.get("title"),
                duration=info.get("duration", 0),
                cover_url=info.get("thumbnail"),
                platform="youtube",
                video_id=video_id,
                raw_info={"tags": info.get("tags")},
                video_path=None,
            )
        except Exception as exc:
            logger.warning(f"获取 YouTube 媒体元信息失败: {exc}")
            return None

    def download_video(
        self,
        video_url: str,
        output_dir: Union[str, None] = None,
    ) -> str:
        """
        下载视频，返回视频文件路径
        """
        if output_dir is None:
            output_dir = get_data_dir()
        video_id = extract_video_id(video_url, "youtube")
        video_path = os.path.join(output_dir, f"{video_id}.mp4")
        if os.path.exists(video_path):
            return video_path
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "%(id)s.%(ext)s")

        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]',
            'outtmpl': output_path,
            'noplaylist': True,
            'quiet': False,
            'merge_output_format': 'mp4',  # 确保合并成 mp4
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            video_id = info.get("id")
            video_path = os.path.join(output_dir, f"{video_id}.mp4")

        if not os.path.exists(video_path):
            raise FileNotFoundError(f"视频文件未找到: {video_path}")

        return video_path

    def download_subtitles(self, video_url: str, output_dir: str = None,
                           langs: List[str] = None) -> SubtitleFetchResult:
        """
        通过 YouTube InnerTube API 直接获取字幕（优先人工字幕，其次自动生成）。
        比 yt_dlp 方式更轻量，无需写临时文件到磁盘。

        :param video_url: 视频链接
        :param output_dir: 未使用（保留接口兼容）
        :param langs: 优先语言列表
        :return: SubtitleFetchResult
        """
        if langs is None:
            langs = ['zh-Hans', 'zh', 'zh-CN', 'zh-TW', 'en', 'en-US', 'ja']

        video_id = extract_video_id(video_url, "youtube")
        if output_dir is None:
            output_dir = get_data_dir()
        if not output_dir:
            output_dir = self.cache_data
        os.makedirs(output_dir, exist_ok=True)

        fetcher = YouTubeSubtitleFetcher()
        try:
            fetch_result = fetcher.fetch_subtitles(video_id, langs)
            if fetch_result and fetch_result.transcript and fetch_result.transcript.segments:
                return fetch_result
        except Exception as exc:
            logger.warning(f"YouTube transcript-api 获取字幕失败，将回退 yt-dlp: {exc}")

        ydl_opts = {
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': langs,
            'subtitlesformat': 'json3/vtt/srt/json/best',
            'skip_download': True,
            'outtmpl': os.path.join(output_dir, f'{video_id}.%(ext)s'),
            'quiet': True,
        }
        diagnostics: Dict[str, Any] = {
            "platform": "youtube",
            "source_checked": [],
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
                real_video_id = info.get("id") or video_id
                diagnostics["video_id"] = real_video_id

                subtitle_sources = [
                    ("requested_subtitles", info.get("requested_subtitles") or {}),
                    ("subtitles", info.get("subtitles") or {}),
                    ("automatic_captions", info.get("automatic_captions") or {}),
                ]

                for source_name, subtitles in subtitle_sources:
                    diagnostics["source_checked"].append(source_name)
                    lang, sub_info = self._pick_subtitle(subtitles, langs)
                    if not lang or not sub_info:
                        continue
                    diagnostics["selected_source"] = source_name
                    diagnostics["selected_lang"] = lang
                    diagnostics["selected_ext"] = sub_info.get("ext")

                    transcript = self._parse_subtitle_from_info(
                        sub_info=sub_info,
                        language=lang,
                        output_dir=output_dir,
                        video_id=real_video_id,
                    )
                    if transcript:
                        transcript.raw = {
                            "source": "youtube_subtitle",
                            "subtitle_source": source_name,
                            "language": lang,
                            "format": sub_info.get("ext"),
                        }
                        return SubtitleFetchResult(
                            transcript=transcript,
                            outcome="success",
                            message=f"已使用 {source_name} 字幕",
                            diagnostics=diagnostics,
                        )

                return SubtitleFetchResult(
                    outcome="unavailable",
                    reason_code="SUBTITLE_NOT_AVAILABLE",
                    message="平台未提供可用字幕",
                    diagnostics=diagnostics,
                )

        except Exception as e:
            logger.warning(f"获取YouTube字幕失败: {e}")
            diagnostics["exception"] = str(e)
            return SubtitleFetchResult(
                outcome="error",
                reason_code="SUBTITLE_FETCH_EXCEPTION",
                message=f"字幕抓取异常: {e}",
                diagnostics=diagnostics,
            )

    def _pick_subtitle(self, subtitles: Dict[str, Any], langs: List[str]) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        if not subtitles:
            return None, None

        for lang in langs:
            if lang in subtitles:
                normalized = self._normalize_sub_info(subtitles[lang])
                if normalized:
                    return lang, normalized

        for lang, info_item in subtitles.items():
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
        ext = sub_info.get("ext", "json3")
        if isinstance(inline_data, str) and inline_data.strip():
            return self._parse_text_subtitle(inline_data, ext, language)
        if isinstance(inline_data, dict):
            return self._parse_json_subtitle_data(inline_data, language, ext)

        subtitle_file = self._resolve_subtitle_file(sub_info, output_dir, video_id, language)
        if not subtitle_file:
            return None

        file_ext = Path(subtitle_file).suffix.lower().lstrip(".") or ext
        return self._parse_subtitle_file(subtitle_file, language, file_ext)

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

        ext = sub_info.get("ext", "json3")
        candidates = [
            os.path.join(output_dir, f"{video_id}.{language}.{ext}"),
            os.path.join(output_dir, f"{video_id}.{language}.json3"),
            os.path.join(output_dir, f"{video_id}.{language}.vtt"),
            os.path.join(output_dir, f"{video_id}.{language}.srt"),
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
        return self._parse_srt_content(content, language)

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

            logger.info(f"成功解析YouTube字幕，共 {len(segments)} 段")
            return TranscriptResult(
                language=language,
                full_text=full_text,
                segments=segments,
                raw={'source': 'youtube_subtitle', 'file': subtitle_file}
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
            if isinstance(data, dict) and isinstance(data.get("events"), list):
                return self._parse_json3_subtitle_data(data, language)
            if isinstance(data, dict) and isinstance(data.get("body"), list):
                for item in data["body"]:
                    text = (item.get("content") or "").strip()
                    start = float(item.get("from", 0))
                    end = float(item.get("to", start))
                    if text:
                        segments.append(TranscriptSegment(start=start, end=end, text=text))
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
                raw={"source": "youtube_subtitle", "format": ext},
            )
        except Exception as e:
            logger.warning(f"解析 json 字幕失败: {e}")
            return None

    def _parse_srt_content(self, srt_content: str, language: str) -> Optional[TranscriptResult]:
        try:
            segments = []
            pattern = r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})\n(.*?)(?=\n\n|\n\d+\n|$)'
            matches = re.findall(pattern, srt_content, re.DOTALL)
            for _, start_time, end_time, text in matches:
                text = text.strip()
                if not text:
                    continue

                def time_to_seconds(value: str) -> float:
                    hh, mm, ss = value.replace(",", ".").split(":")
                    return float(hh) * 3600 + float(mm) * 60 + float(ss)

                segments.append(
                    TranscriptSegment(
                        start=time_to_seconds(start_time),
                        end=time_to_seconds(end_time),
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
                raw={"source": "youtube_subtitle", "format": "srt"},
            )
        except Exception as e:
            logger.warning(f"解析 SRT 字幕失败: {e}")
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
                raw={"source": "youtube_subtitle", "format": "vtt"},
            )
        except Exception as e:
            logger.warning(f"解析 VTT 字幕失败: {e}")
            return None
