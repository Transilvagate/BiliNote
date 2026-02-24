import os
import re
from pathlib import Path

from app.utils.path_helper import get_app_dir

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_INVALID_TASK_ID_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def sanitize_filename(name: str, default: str = "note", max_length: int = 120) -> str:
    """
    Convert arbitrary text to a filesystem-safe filename.
    """
    cleaned = _INVALID_FILENAME_CHARS.sub("_", (name or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        cleaned = default
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip(" ._")
        if not cleaned:
            cleaned = default
    return cleaned


def normalize_task_id(task_id: str, default: str = "unknown_task") -> str:
    """
    Keep task id stable but safe to use as a folder name.
    """
    cleaned = _INVALID_TASK_ID_CHARS.sub("_", (task_id or "").strip())
    return cleaned or default


def get_notes_root(create: bool = True) -> Path:
    notes_root = Path(get_app_dir("notes")).resolve()
    if create:
        notes_root.mkdir(parents=True, exist_ok=True)
    return notes_root


def get_task_dir(task_id: str, create: bool = True) -> Path:
    task_dir = get_notes_root(create=create) / normalize_task_id(task_id)
    if create:
        task_dir.mkdir(parents=True, exist_ok=True)
    return task_dir


def get_task_assets_dir(task_id: str, create: bool = True) -> Path:
    assets_dir = get_task_dir(task_id, create=create) / "assets"
    if create:
        assets_dir.mkdir(parents=True, exist_ok=True)
    return assets_dir


def get_task_note_path(task_id: str, create: bool = True) -> Path:
    task_dir = get_task_dir(task_id, create=create)
    return task_dir / "note.md"


def safe_join_under(base_dir: Path, relative_path: str) -> Path:
    """
    Resolve a relative path under a base directory and prevent directory traversal.
    """
    base_dir = base_dir.resolve()
    candidate = (base_dir / relative_path).resolve()
    if os.path.commonpath([str(base_dir), str(candidate)]) != str(base_dir):
        raise ValueError("Path escapes base directory")
    return candidate
