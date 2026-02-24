import pathlib
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


# Ensure `app.*` imports work when running tests from repo root.
BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

TEST_DEPS_AVAILABLE = True
MISSING_DEPENDENCY = ""

try:
    from app.models.audio_model import AudioDownloadResult
    from app.models.transcriber_model import TranscriptResult, TranscriptSegment
    from app.services.note import NoteGenerator
except ModuleNotFoundError as exc:
    TEST_DEPS_AVAILABLE = False
    MISSING_DEPENDENCY = str(exc)

if not TEST_DEPS_AVAILABLE:
    AudioDownloadResult = object
    TranscriptResult = object
    TranscriptSegment = object
    NoteGenerator = object


class DummyGPT:
    def __init__(self, markdown: str, model: str = "dummy-model"):
        self.model = model
        self._markdown = markdown
        self.last_source = None

    def summarize(self, source):
        self.last_source = source
        return self._markdown


@unittest.skipUnless(TEST_DEPS_AVAILABLE, f"missing dependency: {MISSING_DEPENDENCY}")
class FormalTranscriptChunkingTests(unittest.TestCase):
    @staticmethod
    def _build_audio_meta() -> AudioDownloadResult:
        return AudioDownloadResult(
            file_path="",
            title="demo title",
            duration=120.0,
            cover_url=None,
            platform="bilibili",
            video_id="vid",
            raw_info={"tags": ["tag1", "tag2"]},
            video_path=None,
        )

    @staticmethod
    def _build_transcript() -> TranscriptResult:
        segments = [
            TranscriptSegment(start=0.0, end=1.0, text="第一段"),
            TranscriptSegment(start=1.0, end=2.0, text="第二段"),
        ]
        return TranscriptResult(language="zh", full_text="第一段 第二段", segments=segments, raw={})

    def test_summarize_switches_to_chunked_when_formal_tokens_exceed_output_budget(self):
        generator = NoteGenerator()
        generator._update_status = Mock()
        gpt = DummyGPT(markdown="## AI 总结\n\n测试总结")

        def fake_estimate_tokens(text):
            if text == "FORMAL_SEGMENT_TEXT":
                return 5000
            if "FORMAL_SEGMENT_TEXT" in str(text):
                return 1000
            return 100

        with tempfile.TemporaryDirectory() as tmp_dir:
            markdown_file = pathlib.Path(tmp_dir) / "result.md"
            with patch("app.services.note.resolve_context_limit", return_value=32000), \
                    patch("app.services.note.is_overflow", return_value=False), \
                    patch("app.services.note.estimate_tokens", side_effect=fake_estimate_tokens), \
                    patch.object(generator, "_build_segment_text_for_budget", return_value="FORMAL_SEGMENT_TEXT"), \
                    patch.object(
                        generator,
                        "_generate_formal_transcript_chunked",
                        return_value=("分块生成文稿", 2, "concat"),
                    ) as chunked_mock:
                markdown = generator._summarize_text(
                    audio_meta=self._build_audio_meta(),
                    transcript=self._build_transcript(),
                    gpt=gpt,
                    markdown_cache_file=markdown_file,
                    link=False,
                    screenshot=False,
                    formats=["summary", "formal_transcript"],
                    style=None,
                    extras=None,
                    formal_transcript_style="auto",
                    video_img_urls=[],
                    task_id="task-demo",
                )

        self.assertTrue(chunked_mock.called)
        self.assertEqual(chunked_mock.call_args.kwargs["output_budget"], 4000)
        self.assertIsNotNone(gpt.last_source)
        self.assertNotIn("formal_transcript", gpt.last_source._format)
        self.assertIn("## 正式文稿", markdown)
        self.assertTrue(
            any(
                "formal_tokens_exceed_output_budget" in (
                    call.kwargs.get("diagnostics", {}).get("chunk_reason", "")
                )
                for call in generator._update_status.call_args_list
            )
        )

    def test_generate_chunked_uses_concat_when_generated_parts_exceed_budget(self):
        generator = NoteGenerator()
        generator._update_status = Mock()
        transcript = self._build_transcript()
        chunks = [[transcript.segments[0]], [transcript.segments[1]]]

        with patch.object(generator, "_chunk_transcript_segments", return_value=chunks), \
                patch.object(generator, "_build_segment_text_for_budget", return_value="chunk text"), \
                patch.object(generator, "_chat_text_with_retry", side_effect=["part one", "part two"]), \
                patch("app.services.note.estimate_tokens", side_effect=lambda text: 3000 if "part" in str(text) else 50), \
                patch.object(generator, "_merge_formal_transcript_parts") as merge_mock:
            formal_text, chunk_count, merge_strategy = generator._generate_formal_transcript_chunked(
                gpt=object(),
                transcript=transcript,
                title="demo",
                task_id="task-demo",
                context_limit=32000,
                formal_transcript_style="auto",
                output_budget=4000,
            )

        self.assertEqual(chunk_count, 2)
        self.assertEqual(merge_strategy, "concat")
        self.assertEqual(formal_text, "part one\n\npart two")
        merge_mock.assert_not_called()
        self.assertTrue(
            any(
                call.kwargs.get("diagnostics", {}).get("merge_strategy") == "concat"
                for call in generator._update_status.call_args_list
            )
        )

    def test_generate_chunked_uses_llm_merge_when_generated_parts_fit_budget(self):
        generator = NoteGenerator()
        generator._update_status = Mock()
        transcript = self._build_transcript()
        chunks = [[transcript.segments[0]], [transcript.segments[1]]]

        with patch.object(generator, "_chunk_transcript_segments", return_value=chunks), \
                patch.object(generator, "_build_segment_text_for_budget", return_value="chunk text"), \
                patch.object(generator, "_chat_text_with_retry", side_effect=["part one", "part two"]), \
                patch("app.services.note.estimate_tokens", side_effect=lambda text: 300 if "part" in str(text) else 50), \
                patch.object(generator, "_merge_formal_transcript_parts", return_value="merged output") as merge_mock:
            formal_text, chunk_count, merge_strategy = generator._generate_formal_transcript_chunked(
                gpt=object(),
                transcript=transcript,
                title="demo",
                task_id="task-demo",
                context_limit=32000,
                formal_transcript_style="auto",
                output_budget=4000,
            )

        self.assertEqual(chunk_count, 2)
        self.assertEqual(merge_strategy, "llm_merge")
        self.assertEqual(formal_text, "merged output")
        merge_mock.assert_called_once()
        self.assertTrue(
            any(
                call.kwargs.get("diagnostics", {}).get("merge_strategy") == "llm_merge"
                for call in generator._update_status.call_args_list
            )
        )


if __name__ == "__main__":
    unittest.main()
