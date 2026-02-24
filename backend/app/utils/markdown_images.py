import re
from dataclasses import dataclass
from typing import Callable

IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


@dataclass
class MarkdownImage:
    alt: str
    path: str
    suffix: str
    raw: str


def parse_image_target(target: str) -> tuple[str, str]:
    """
    Parse markdown image target and preserve optional suffix such as title.
    Example: "path/to/a.png \"title\"" -> ("path/to/a.png", " \"title\"")
    """
    cleaned = target.strip()
    if cleaned.startswith("<") and cleaned.endswith(">"):
        return cleaned[1:-1].strip(), ""

    match = re.match(r"^(\S+)(.*)$", cleaned)
    if not match:
        return cleaned, ""
    return match.group(1), match.group(2)


def iter_markdown_images(markdown: str):
    for match in IMAGE_PATTERN.finditer(markdown):
        alt = match.group(1)
        path, suffix = parse_image_target(match.group(2))
        yield MarkdownImage(alt=alt, path=path, suffix=suffix, raw=match.group(0))


def rewrite_markdown_image_paths(markdown: str, path_mapper: Callable[[MarkdownImage], str | None]) -> str:
    """
    Rewrite markdown image paths with a callback.
    Return original image markdown when callback returns None.
    """

    def repl(match):
        alt = match.group(1)
        path, suffix = parse_image_target(match.group(2))
        image = MarkdownImage(alt=alt, path=path, suffix=suffix, raw=match.group(0))
        new_path = path_mapper(image)
        if not new_path:
            return match.group(0)
        return f"![{alt}]({new_path}{suffix})"

    return IMAGE_PATTERN.sub(repl, markdown)
