import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch


# Ensure `app.*` imports work when running tests from repo root.
BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

TEST_DEPS_AVAILABLE = True
MISSING_DEPENDENCY = ""

try:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
except ModuleNotFoundError as exc:
    TEST_DEPS_AVAILABLE = False
    MISSING_DEPENDENCY = str(exc)

if TEST_DEPS_AVAILABLE:
    try:
        from app.db.engine import Base
        from app.db.models.models import Model
        from app.db.models.providers import Provider
        from app.downloaders.bilibili_downloader import BilibiliDownloader
        from app.enmus.task_status_enums import TaskStatus
        from app.models.audio_model import AudioDownloadResult
        from app.models.transcriber_model import SubtitleFetchResult, TranscriptResult, TranscriptSegment
        from app.services.note import (
            BILIBILI_CHINESE_SUBTITLE_LANGS,
            BILIBILI_ENGLISH_SUBTITLE_LANGS,
            NoteGenerator,
        )
        from app.services.provider import ProviderService
    except ModuleNotFoundError as exc:
        TEST_DEPS_AVAILABLE = False
        MISSING_DEPENDENCY = str(exc)

if not TEST_DEPS_AVAILABLE:
    # Keep type names available for static analyzers and avoid NameError in skipped tests.
    BilibiliDownloader = object
    TaskStatus = object
    AudioDownloadResult = object
    SubtitleFetchResult = object
    TranscriptResult = object
    TranscriptSegment = object
    NoteGenerator = object
    ProviderService = object
    Base = object
    Model = object
    Provider = object


def _build_transcript(language: str, text: str) -> TranscriptResult:
    return TranscriptResult(
        language=language,
        full_text=text,
        segments=[TranscriptSegment(start=0.0, end=1.0, text=text)],
        raw={"source": "test_subtitle", "language": language},
    )


class FakeBilibiliDownloader:
    def __init__(
        self,
        duration_seconds,
        chinese_result: SubtitleFetchResult,
        english_result: SubtitleFetchResult,
    ):
        self.duration_seconds = duration_seconds
        self.chinese_result = chinese_result
        self.english_result = english_result
        self.calls = []

    def download_subtitles(self, video_url: str, output_dir=None, langs=None):
        normalized_langs = list(langs or [])
        self.calls.append(normalized_langs)
        if normalized_langs and normalized_langs[0].startswith("zh"):
            return self.chinese_result
        if normalized_langs and normalized_langs[0].startswith("en"):
            return self.english_result
        return SubtitleFetchResult(outcome="unavailable", reason_code="UNEXPECTED_LANGS")

    def get_media_info(self, video_url: str, output_dir=None):
        if self.duration_seconds is None:
            return None
        return AudioDownloadResult(
            file_path="",
            title="demo",
            duration=self.duration_seconds,
            cover_url=None,
            platform="bilibili",
            video_id="vid",
            raw_info={},
            video_path=None,
        )


@unittest.skipUnless(TEST_DEPS_AVAILABLE, f"missing dependency: {MISSING_DEPENDENCY}")
class BilibiliSubtitleSelectionTests(unittest.TestCase):
    def test_select_subtitle_file_prefers_chinese_by_lang_priority(self):
        paths = [
            "/tmp/video.en.srt",
            "/tmp/video.ai-zh.srt",
            "/tmp/video.zh-Hans.vtt",
        ]
        langs = ["zh-Hans", "zh-CN", "zh-TW", "zh", "ai-zh", "en", "en-US"]
        selected = BilibiliDownloader._select_subtitle_file(paths, langs)
        self.assertEqual(selected, "/tmp/video.zh-Hans.vtt")

    def test_select_subtitle_file_avoids_fuzzy_lang_match(self):
        paths = [
            "/tmp/video.zhen.srt",
            "/tmp/video.en.srt",
            "/tmp/video.zh.srt",
        ]
        selected = BilibiliDownloader._select_subtitle_file(paths, ["zh", "en"])
        self.assertEqual(selected, "/tmp/video.zh.srt")


@unittest.skipUnless(TEST_DEPS_AVAILABLE, f"missing dependency: {MISSING_DEPENDENCY}")
class BilibiliFallbackStrategyTests(unittest.TestCase):
    def setUp(self):
        self.generator = NoteGenerator()
        self.unavailable = SubtitleFetchResult(
            outcome="unavailable",
            reason_code="SUBTITLE_NOT_AVAILABLE",
            message="无可用字幕",
        )
        self.english_success = SubtitleFetchResult(
            transcript=_build_transcript("en", "hello"),
            outcome="success",
            message="english subtitle",
            diagnostics={"subtitle_file": "video.en.srt"},
        )

    def test_short_video_uses_english_when_chinese_unavailable(self):
        downloader = FakeBilibiliDownloader(
            duration_seconds=120,
            chinese_result=self.unavailable,
            english_result=self.english_success,
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_file = pathlib.Path(tmp_dir) / "case_transcript.json"
            result = self.generator._get_transcript(
                downloader=downloader,
                video_url="https://www.bilibili.com/video/demo",
                audio_file=None,
                transcript_cache_file=cache_file,
                status_phase=TaskStatus.TRANSCRIBING,
                task_id=None,
                platform="bilibili",
                force_refresh=True,
                allow_asr_fallback=False,
            )
        self.assertIsNotNone(result)
        self.assertEqual(result.language, "en")
        self.assertEqual(downloader.calls[0], BILIBILI_CHINESE_SUBTITLE_LANGS)
        self.assertEqual(downloader.calls[1], BILIBILI_ENGLISH_SUBTITLE_LANGS)

    def test_long_video_skips_english_fallback_and_returns_none(self):
        downloader = FakeBilibiliDownloader(
            duration_seconds=1200,
            chinese_result=self.unavailable,
            english_result=self.english_success,
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_file = pathlib.Path(tmp_dir) / "case_transcript.json"
            result = self.generator._get_transcript(
                downloader=downloader,
                video_url="https://www.bilibili.com/video/demo",
                audio_file=None,
                transcript_cache_file=cache_file,
                status_phase=TaskStatus.TRANSCRIBING,
                task_id=None,
                platform="bilibili",
                force_refresh=True,
                allow_asr_fallback=False,
            )
        self.assertIsNone(result)
        self.assertEqual(len(downloader.calls), 1)
        self.assertEqual(downloader.calls[0], BILIBILI_CHINESE_SUBTITLE_LANGS)

    def test_unknown_duration_defaults_to_asr_path(self):
        downloader = FakeBilibiliDownloader(
            duration_seconds=None,
            chinese_result=self.unavailable,
            english_result=self.english_success,
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_file = pathlib.Path(tmp_dir) / "case_transcript.json"
            result = self.generator._get_transcript(
                downloader=downloader,
                video_url="https://www.bilibili.com/video/demo",
                audio_file=None,
                transcript_cache_file=cache_file,
                status_phase=TaskStatus.TRANSCRIBING,
                task_id=None,
                platform="bilibili",
                force_refresh=True,
                allow_asr_fallback=False,
            )
        self.assertIsNone(result)
        self.assertEqual(len(downloader.calls), 1)
        self.assertEqual(downloader.calls[0], BILIBILI_CHINESE_SUBTITLE_LANGS)


@unittest.skipUnless(TEST_DEPS_AVAILABLE, f"missing dependency: {MISSING_DEPENDENCY}")
class ProviderCascadeDeleteTests(unittest.TestCase):
    def test_delete_provider_with_models_cascades(self):
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        Base.metadata.create_all(bind=engine)

        session = TestSessionLocal()
        session.add(
            Provider(
                id="provider-test",
                name="provider-test",
                logo="custom",
                type="custom",
                api_key="key",
                base_url="https://example.com/v1",
                enabled=1,
            )
        )
        session.add(Model(provider_id="provider-test", model_name="m1"))
        session.add(Model(provider_id="provider-test", model_name="m2"))
        session.commit()
        session.close()

        with patch("app.services.provider.SessionLocal", TestSessionLocal):
            result = ProviderService.delete_provider_with_models("provider-test")

        self.assertTrue(result["deleted"])
        self.assertEqual(result["deleted_models"], 2)

        verify_session = TestSessionLocal()
        try:
            provider_exists = verify_session.query(Provider).filter_by(id="provider-test").first()
            model_count = verify_session.query(Model).filter_by(provider_id="provider-test").count()
        finally:
            verify_session.close()

        self.assertIsNone(provider_exists)
        self.assertEqual(model_count, 0)


if __name__ == "__main__":
    unittest.main()
