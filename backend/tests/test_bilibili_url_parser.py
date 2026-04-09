import pathlib
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

TMP_ROOT = BACKEND_DIR.parent / ".tmp-test"
TMP_ROOT.mkdir(parents=True, exist_ok=True)

if "yt_dlp" not in sys.modules:
    yt_dlp_stub = types.ModuleType("yt_dlp")
    yt_dlp_stub.YoutubeDL = object
    sys.modules["yt_dlp"] = yt_dlp_stub

from app.downloaders.bilibili_downloader import BilibiliDownloader
from app.enmus.exception import NoteErrorEnum
from app.exceptions.note import NoteError
from app.utils.url_parser import (
    BilibiliUrlKind,
    extract_video_id,
    normalize_bilibili_url,
    parse_bilibili_url,
)


class _FakeYoutubeDL:
    def __init__(self, opts, capture_store):
        self.opts = opts
        self.capture_store = capture_store

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def extract_info(self, video_url, download=False):
        self.capture_store["video_url"] = video_url
        self.capture_store["download"] = download
        return {
            "id": "BV1xx411c7mD_p4",
            "title": "demo",
            "duration": 12,
            "thumbnail": "https://example.com/cover.jpg",
            "ext": "m4a",
        }


class BilibiliUrlParserTests(unittest.TestCase):
    def test_video_url_preserves_page_parameter(self):
        normalized = normalize_bilibili_url("https://www.bilibili.com/video/BV1xx411c7mD?p=4&utm_source=test")
        self.assertEqual(normalized.kind, BilibiliUrlKind.VIDEO)
        self.assertEqual(normalized.normalized_url, "https://www.bilibili.com/video/BV1xx411c7mD?p=4")
        self.assertEqual(extract_video_id(normalized.normalized_url, "bilibili"), "BV1xx411c7mD_p4")

    def test_short_link_resolves_to_video(self):
        with patch("app.utils.url_parser.resolve_bilibili_short_url", return_value="https://www.bilibili.com/video/BV1xx411c7mD?p=2"):
            normalized = normalize_bilibili_url("https://b23.tv/demo")
        self.assertEqual(normalized.kind, BilibiliUrlKind.SHORT)
        self.assertTrue(normalized.was_short_link)
        self.assertEqual(normalized.normalized_url, "https://www.bilibili.com/video/BV1xx411c7mD?p=2")

    def test_series_list_homepage_requires_specific_episode(self):
        parsed = parse_bilibili_url("https://space.bilibili.com/123/lists/456?type=series")
        self.assertEqual(parsed.kind, BilibiliUrlKind.LIST_SERIES)
        self.assertTrue(parsed.needs_episode_selection)

        with self.assertRaises(NoteError) as context:
            normalize_bilibili_url("https://space.bilibili.com/123/lists/456?type=series")
        self.assertEqual(context.exception.code, NoteErrorEnum.BILIBILI_COLLECTION_NEEDS_EPISODE.code)

    def test_series_list_with_bvid_normalizes_to_video(self):
        normalized = normalize_bilibili_url(
            "https://space.bilibili.com/123/lists/456?type=series&bvid=BV1xx411c7mD&p=3"
        )
        self.assertEqual(normalized.kind, BilibiliUrlKind.LIST_SERIES)
        self.assertEqual(normalized.normalized_url, "https://www.bilibili.com/video/BV1xx411c7mD?p=3")

    def test_season_list_with_business_id_normalizes_to_av_video(self):
        normalized = normalize_bilibili_url(
            "https://space.bilibili.com/123/lists/456?type=season&business_id=987654321"
        )
        self.assertEqual(normalized.kind, BilibiliUrlKind.LIST_SEASON)
        self.assertEqual(normalized.normalized_url, "https://www.bilibili.com/video/av987654321")

    def test_series_detail_homepage_requires_specific_episode(self):
        parsed = parse_bilibili_url("https://space.bilibili.com/123/channel/seriesdetail?sid=456&ctype=0")
        self.assertEqual(parsed.kind, BilibiliUrlKind.SERIES_DETAIL)
        self.assertTrue(parsed.needs_episode_selection)

    def test_collection_detail_with_bvid_normalizes_to_video(self):
        normalized = normalize_bilibili_url(
            "https://space.bilibili.com/123/channel/collectiondetail?sid=456&bvid=BV1xx411c7mD"
        )
        self.assertEqual(normalized.kind, BilibiliUrlKind.COLLECTION_DETAIL)
        self.assertEqual(normalized.normalized_url, "https://www.bilibili.com/video/BV1xx411c7mD")


class BilibiliDownloaderNormalizationTests(unittest.TestCase):
    def test_download_normalizes_before_calling_ytdlp(self):
        downloader = BilibiliDownloader()
        capture_store = {}

        tmp_dir = tempfile.mkdtemp(dir=TMP_ROOT)
        try:
            fake_factory = lambda opts: _FakeYoutubeDL(opts, capture_store)
            with patch.object(downloader, "_apply_cookie_file", return_value=None), \
                    patch("app.downloaders.bilibili_downloader.yt_dlp.YoutubeDL", side_effect=fake_factory):
                downloader.download(
                    "https://space.bilibili.com/123/lists/456?type=series&bvid=BV1xx411c7mD&p=4",
                    output_dir=tmp_dir,
                )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        self.assertEqual(capture_store["video_url"], "https://www.bilibili.com/video/BV1xx411c7mD?p=4")
        self.assertTrue(capture_store["download"])

    def test_download_subtitles_normalizes_before_calling_bbdown(self):
        downloader = BilibiliDownloader()

        tmp_dir = tempfile.mkdtemp(dir=TMP_ROOT)
        try:
            cookie_path = pathlib.Path(tmp_dir) / "cookies.txt"
            with patch.object(downloader.cookie_service, "get_cookie_status", return_value={}), \
                    patch.object(downloader.cookie_service, "resolve_cookie_file_path", return_value=cookie_path), \
                    patch.object(
                        downloader.bbdown_client,
                        "run_subtitle_download",
                        return_value={
                            "success": False,
                            "reason_code": "SUBTITLE_NOT_AVAILABLE",
                            "message": "no subtitle",
                            "diagnostics": {},
                            "subtitle_files": [],
                        },
                    ) as run_mock:
                downloader.download_subtitles(
                    "https://space.bilibili.com/123/channel/collectiondetail?sid=456&bvid=BV1xx411c7mD",
                    output_dir=tmp_dir,
                )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        self.assertEqual(run_mock.call_args.kwargs["video_url"], "https://www.bilibili.com/video/BV1xx411c7mD")


if __name__ == "__main__":
    unittest.main()
