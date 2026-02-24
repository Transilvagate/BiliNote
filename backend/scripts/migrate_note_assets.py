#!/usr/bin/env python
import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.utils.markdown_images import MarkdownImage, rewrite_markdown_image_paths
from app.utils.task_assets import (
    get_task_assets_dir,
    get_task_note_path,
    sanitize_filename,
)


def _resolve_note_output_dir() -> Path:
    configured = os.getenv("NOTE_OUTPUT_DIR", "note_results")
    configured_path = Path(configured)
    if configured_path.is_absolute():
        return configured_path
    return (PROJECT_ROOT / configured_path).resolve()


def _allocate_name(name: str, used_names: set[str]) -> str:
    safe_name = sanitize_filename(name, default="image")
    stem = Path(safe_name).stem or "image"
    suffix = Path(safe_name).suffix or ".jpg"
    candidate = f"{stem}{suffix}"
    idx = 1
    while candidate in used_names:
        candidate = f"{stem}_{idx}{suffix}"
        idx += 1
    used_names.add(candidate)
    return candidate


def migrate_markdown(
    task_id: str,
    markdown: str,
    apply_changes: bool,
    copied_sources: set[Path],
) -> tuple[str, int, list[str]]:
    warnings: list[str] = []
    assets_dir = get_task_assets_dir(task_id, create=apply_changes)
    used_names = set()
    source_to_target: dict[Path, str] = {}
    copied_count = 0

    backend_root = PROJECT_ROOT

    def mapper(image: MarkdownImage) -> str | None:
        nonlocal copied_count
        src = (image.path or "").strip()
        if not src:
            return None

        if src.startswith("./assets/"):
            return src
        if src.startswith("assets/"):
            return f"./{src}"

        if not src.startswith("/static/"):
            return None

        source_file = (backend_root / src.lstrip("/")).resolve()
        if not source_file.exists() or not source_file.is_file():
            warnings.append(f"图片不存在，保留原引用：{src}")
            return None

        if source_file in source_to_target:
            target_name = source_to_target[source_file]
            return f"./assets/{target_name}"

        target_name = _allocate_name(source_file.name, used_names)
        source_to_target[source_file] = target_name
        if apply_changes:
            assets_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, assets_dir / target_name)
            copied_sources.add(source_file)
        copied_count += 1
        return f"./assets/{target_name}"

    updated = rewrite_markdown_image_paths(markdown, mapper)
    return updated, copied_count, warnings


def _task_files(note_output_dir: Path, target_task_id: str | None):
    for file_path in sorted(note_output_dir.glob("*.json")):
        name = file_path.name
        if name.endswith(".status.json"):
            continue
        if name.endswith("_audio.json") or name.endswith("_transcript.json"):
            continue
        task_id = file_path.stem
        if target_task_id and task_id != target_task_id:
            continue
        yield task_id, file_path


def run_migration(apply_changes: bool, target_task_id: str | None, report_path: Path | None):
    note_output_dir = _resolve_note_output_dir()
    copied_sources: set[Path] = set()

    report = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "mode": "apply" if apply_changes else "dry-run",
        "note_output_dir": str(note_output_dir),
        "task_filter": target_task_id,
        "tasks_total": 0,
        "tasks_changed": 0,
        "assets_copied": 0,
        "old_files_removed": 0,
        "warnings": [],
        "tasks": [],
    }

    for task_id, file_path in _task_files(note_output_dir, target_task_id):
        report["tasks_total"] += 1
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception as exc:
            warning = f"读取任务文件失败 {file_path.name}: {exc}"
            report["warnings"].append(warning)
            report["tasks"].append(
                {
                    "task_id": task_id,
                    "file": str(file_path),
                    "changed": False,
                    "assets_copied": 0,
                    "warnings": [warning],
                }
            )
            continue

        markdown_value = payload.get("markdown")
        if not markdown_value:
            report["tasks"].append(
                {
                    "task_id": task_id,
                    "file": str(file_path),
                    "changed": False,
                    "assets_copied": 0,
                    "warnings": [],
                }
            )
            continue

        task_warnings: list[str] = []
        copied_count = 0
        changed = False

        if isinstance(markdown_value, str):
            updated_markdown, copied_count, task_warnings = migrate_markdown(
                task_id=task_id,
                markdown=markdown_value,
                apply_changes=apply_changes,
                copied_sources=copied_sources,
            )
            changed = updated_markdown != markdown_value
            if changed and apply_changes:
                payload["markdown"] = updated_markdown
                file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            primary_markdown = updated_markdown
        elif isinstance(markdown_value, list):
            updated_list = []
            for item in markdown_value:
                if not isinstance(item, dict) or not isinstance(item.get("content"), str):
                    updated_list.append(item)
                    continue
                updated_content, copied_inc, item_warnings = migrate_markdown(
                    task_id=task_id,
                    markdown=item["content"],
                    apply_changes=apply_changes,
                    copied_sources=copied_sources,
                )
                copied_count += copied_inc
                task_warnings.extend(item_warnings)
                if updated_content != item["content"]:
                    changed = True
                new_item = dict(item)
                new_item["content"] = updated_content
                updated_list.append(new_item)
            if changed and apply_changes:
                payload["markdown"] = updated_list
                file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            primary_markdown = updated_list[0]["content"] if updated_list and isinstance(updated_list[0], dict) else ""
        else:
            task_warnings.append("markdown 字段格式不支持，已跳过")
            primary_markdown = ""

        if apply_changes and primary_markdown:
            note_path = get_task_note_path(task_id)
            note_path.write_text(primary_markdown, encoding="utf-8")

        if changed:
            report["tasks_changed"] += 1
        report["assets_copied"] += copied_count
        report["warnings"].extend(task_warnings)
        report["tasks"].append(
            {
                "task_id": task_id,
                "file": str(file_path),
                "changed": changed,
                "assets_copied": copied_count,
                "warnings": task_warnings,
            }
        )

    if apply_changes:
        removed_count = 0
        for source in sorted(copied_sources):
            try:
                if source.exists():
                    source.unlink()
                    removed_count += 1
            except OSError as exc:
                report["warnings"].append(f"删除旧文件失败 {source}: {exc}")
        report["old_files_removed"] = removed_count

    if report_path is None:
        report_name = f"migration_report_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
        report_path = PROJECT_ROOT / "data" / "notes" / report_name
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("迁移完成")
    print(f"模式: {report['mode']}")
    print(f"扫描任务数: {report['tasks_total']}")
    print(f"发生改写任务数: {report['tasks_changed']}")
    print(f"复制图片数量: {report['assets_copied']}")
    print(f"删除旧文件数量: {report['old_files_removed']}")
    print(f"警告数量: {len(report['warnings'])}")
    print(f"报告文件: {report_path}")


def main():
    parser = argparse.ArgumentParser(description="Migrate note markdown image references into task assets folders.")
    parser.add_argument("--apply", action="store_true", help="Apply changes. Default mode is dry-run.")
    parser.add_argument("--task-id", type=str, default=None, help="Migrate only one task id.")
    parser.add_argument("--report-path", type=str, default=None, help="Optional report output path.")
    args = parser.parse_args()

    report_path = Path(args.report_path).resolve() if args.report_path else None
    run_migration(apply_changes=args.apply, target_task_id=args.task_id, report_path=report_path)


if __name__ == "__main__":
    main()
