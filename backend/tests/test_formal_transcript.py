import pathlib
import sys
import unittest


# Ensure `app.*` imports work when running tests from repo root.
BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


from app.gpt.prompt_builder import get_formal_transcript_format
from app.utils.formal_transcript import sanitize_formal_transcript_body


class FormalTranscriptPromptTests(unittest.TestCase):
    def test_formal_transcript_format_includes_style_block(self):
        text = get_formal_transcript_format(formal_transcript_style="multi_dialogue")
        self.assertIn("正式文稿", text)
        self.assertIn("多人对话", text)
        self.assertIn("禁止时间标记", text)


class FormalTranscriptSanitizerTests(unittest.TestCase):
    def test_sanitize_removes_time_markers_and_normalizes_labels(self):
        raw = """
*Content-[01:23]
00:12 - 你好
[00:34] 继续
主持人 : 你好
【嘉宾 1】 嗯嗯
http://example.com
""".strip()

        sanitized = sanitize_formal_transcript_body(raw)
        self.assertNotIn("*Content-[01:23]", sanitized)
        self.assertNotRegex(sanitized, r"\[\d{1,2}:\d{2}\]")
        self.assertNotRegex(sanitized, r"(?m)^\s*\d{1,2}:\d{2}\s*-\s*")
        self.assertIn("主持人：你好", sanitized)
        self.assertIn("嘉宾1：嗯嗯", sanitized)
        self.assertIn("http://example.com", sanitized)


if __name__ == "__main__":
    unittest.main()
