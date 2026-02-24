import io
import logging
import mimetypes
import os
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from app.utils.markdown_images import MarkdownImage, rewrite_markdown_image_paths
from app.utils.task_assets import (
    get_task_assets_dir,
    get_task_dir,
    safe_join_under,
    sanitize_filename,
)

logger = logging.getLogger(__name__)


@dataclass
class BundleBuildResult:
    zip_bytes: bytes
    zip_filename: str
    markdown_filename: str
    warnings: list[str] = field(default_factory=list)
    rewritten_markdown: str = ""


class MarkdownBundleExporter:
    def __init__(self, task_id: str, title: str, markdown: str):
        self.task_id = task_id
        self.title = title
        self.markdown = markdown or ""
        self._assets: dict[str, bytes] = {}
        self._warnings: list[str] = []
        self._source_to_asset_name: dict[str, str] = {}

    def build(self) -> BundleBuildResult:
        rewritten = rewrite_markdown_image_paths(self.markdown, self._map_image_path)

        safe_title = sanitize_filename(self.title, default="note")
        markdown_filename = f"{safe_title}.md"
        zip_filename = f"{safe_title}.zip"

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(markdown_filename, rewritten)
            # Keep a stable layout even when there is no image.
            zf.writestr("assets/", "")
            for name, content in self._assets.items():
                zf.writestr(f"assets/{name}", content)

        return BundleBuildResult(
            zip_bytes=zip_buffer.getvalue(),
            zip_filename=zip_filename,
            markdown_filename=markdown_filename,
            warnings=self._warnings,
            rewritten_markdown=rewritten,
        )

    def _map_image_path(self, image: MarkdownImage) -> str | None:
        raw_path = (image.path or "").strip()
        if not raw_path:
            return None

        lower_path = raw_path.lower()
        if lower_path.startswith("data:"):
            # Data URI is already self-contained.
            return None

        source_key = raw_path
        if source_key in self._source_to_asset_name:
            return f"./assets/{self._source_to_asset_name[source_key]}"

        local_path = self._resolve_local_path(raw_path)
        if local_path:
            try:
                content = local_path.read_bytes()
            except OSError as exc:
                warning = f"读取图片失败，保留原引用：{raw_path} ({exc})"
                logger.warning(warning)
                self._warnings.append(warning)
                return None

            asset_name = self._allocate_asset_name(local_path.name)
            self._assets[asset_name] = content
            self._source_to_asset_name[source_key] = asset_name
            return f"./assets/{asset_name}"

        if lower_path.startswith("http://") or lower_path.startswith("https://"):
            content, filename = self._download_external_image(raw_path)
            if content is None:
                return None
            asset_name = self._allocate_asset_name(filename)
            self._assets[asset_name] = content
            self._source_to_asset_name[source_key] = asset_name
            return f"./assets/{asset_name}"

        warning = f"未找到本地图片，保留原引用：{raw_path}"
        logger.warning(warning)
        self._warnings.append(warning)
        return None

    def _resolve_local_path(self, raw_path: str) -> Path | None:
        task_assets_dir = get_task_assets_dir(self.task_id, create=False)
        task_dir = get_task_dir(self.task_id, create=False)
        backend_root = Path(__file__).resolve().parents[2]

        if raw_path.startswith("./assets/"):
            relative = raw_path[len("./assets/"):]
            return self._safe_existing_path(task_assets_dir, relative)
        if raw_path.startswith("assets/"):
            relative = raw_path[len("assets/"):]
            return self._safe_existing_path(task_assets_dir, relative)
        if raw_path.startswith("/static/"):
            candidate = (backend_root / raw_path.lstrip("/")).resolve()
            return candidate if candidate.exists() and candidate.is_file() else None
        if raw_path.startswith("/"):
            candidate = (backend_root / raw_path.lstrip("/")).resolve()
            return candidate if candidate.exists() and candidate.is_file() else None
        return self._safe_existing_path(task_dir, raw_path)

    @staticmethod
    def _safe_existing_path(base_dir: Path, relative_path: str) -> Path | None:
        try:
            candidate = safe_join_under(base_dir, relative_path)
        except ValueError:
            return None
        if candidate.exists() and candidate.is_file():
            return candidate
        return None

    def _download_external_image(self, url: str) -> tuple[bytes | None, str]:
        parsed = urllib.parse.urlparse(url)
        guessed_name = os.path.basename(parsed.path) or "image"
        guessed_name = sanitize_filename(guessed_name, default="image")
        if "." not in guessed_name:
            guessed_name = f"{guessed_name}.jpg"

        request = urllib.request.Request(
            url=url,
            headers={"User-Agent": "Mozilla/5.0 (BiliNote Exporter)"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as resp:
                content = resp.read()
                content_type = resp.headers.get("Content-Type", "")
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            warning = f"下载外链图片失败，保留原引用：{url} ({exc})"
            logger.warning(warning)
            self._warnings.append(warning)
            return None, guessed_name

        if guessed_name.endswith(".jpg") and content_type:
            ext = mimetypes.guess_extension(content_type.split(";")[0].strip())
            if ext and ext not in (".jpe",):
                guessed_name = f"{Path(guessed_name).stem}{ext}"

        return content, guessed_name

    def _allocate_asset_name(self, original_name: str) -> str:
        safe_name = sanitize_filename(original_name, default="image")
        stem = Path(safe_name).stem or "image"
        suffix = Path(safe_name).suffix or ".jpg"

        name = f"{stem}{suffix}"
        counter = 1
        while name in self._assets:
            name = f"{stem}_{counter}{suffix}"
            counter += 1
        return name

