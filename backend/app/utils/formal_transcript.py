import re


def sanitize_formal_transcript_body(formal_text: str) -> str:
    """
    Format-only post-processing for the '## 正式文稿' body.

    Important: do NOT rewrite meaning; only remove time markers and normalize speaker labels.
    """
    text = (formal_text or "").strip()
    if not text:
        return ""

    # Remove time marker placeholders used by other modules.
    text = re.sub(r"\*Content-\[\d{1,2}:\d{2}(?::\d{2})?\]", "", text)
    text = re.sub(r"\*Screenshot-\[\d{1,2}:\d{2}(?::\d{2})?\]", "", text)
    text = re.sub(r"\[\d{1,2}:\d{2}(?::\d{2})?\]", "", text)

    # Remove leading "mm:ss - " that may leak from transcript segment formatting.
    text = re.sub(r"(?m)^\s*\d{1,2}:\d{2}(?::\d{2})?\s*-\s*", "", text)

    speaker_hint = re.compile(r"(主持|嘉宾|讲述者|旁白|采访|老师|学生|观众|提问|回答|问|答)")

    def _normalize_wrapped_label(match: re.Match) -> str:
        label = (match.group(1) or "").strip()
        if speaker_hint.search(label) or re.search(r"\d", label):
            return f"{label}："
        # Keep as-is for stage directions like "[笑]" or unknown labels to avoid damaging meaning.
        return match.group(0)

    # Normalize common speaker label notations at the start of a line:
    # "【主持人】你好" -> "主持人：你好"
    # Only convert when the label looks speaker-like and the line continues with content.
    text = re.sub(
        r"(?m)^\s*[【\[]\s*([^\]】]{1,20})\s*[】\]]\s*(?=\S)",
        _normalize_wrapped_label,
        text,
    )
    text = re.sub(
        r"(?m)^\s*（\s*([^）]{1,20})\s*）\s*(?=\S)",
        _normalize_wrapped_label,
        text,
    )

    # Normalize colon spacing and unify to fullwidth colon for speaker-like labels only.
    text = re.sub(
        r"(?m)^(\s*(?:主持人|嘉宾|讲述者|旁白|采访者|采访|老师|学生|观众|提问者|回答者|问|答)\s*\d{0,2})\s*[:：]\s*",
        lambda m: f"{m.group(1).replace(' ', '')}：",
        text,
    )

    # Normalize "讲述者 1：" / "嘉宾 2：" style labels.
    text = re.sub(r"(?m)^(\s*(?:讲述者|嘉宾|观众|主持人))\s+(\d+)\s*：", r"\1\2：", text)

    # Clean up blank lines and trailing spaces created by removals.
    text = re.sub(r"(?m)[ \t]+$", "", text)
    text = re.sub(r"(?m)^\s+$", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

