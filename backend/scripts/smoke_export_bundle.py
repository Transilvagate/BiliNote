#!/usr/bin/env python
import io
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.markdown_bundle_export import MarkdownBundleExporter
from app.utils.task_assets import get_task_assets_dir, get_task_note_path


def assert_true(condition: bool, message: str):
    if not condition:
        raise AssertionError(message)


def run_smoke():
    task_id = "smoke_export_task"
    assets_dir = get_task_assets_dir(task_id)
    assets_dir.mkdir(parents=True, exist_ok=True)

    # Prepare local task asset.
    local_image = assets_dir / "local.jpg"
    local_image.write_bytes(b"local-image")

    # Prepare old static screenshot asset for compatibility check.
    old_static_dir = PROJECT_ROOT / "static" / "screenshots"
    old_static_dir.mkdir(parents=True, exist_ok=True)
    old_static_file = old_static_dir / "legacy.jpg"
    old_static_file.write_bytes(b"legacy-image")

    markdown = (
        "# Smoke Test\n\n"
        "![local](./assets/local.jpg)\n\n"
        "![legacy](/static/screenshots/legacy.jpg)\n\n"
        "![external](https://example.invalid/404.jpg)\n"
    )
    get_task_note_path(task_id).write_text(markdown, encoding="utf-8")

    exporter = MarkdownBundleExporter(task_id=task_id, title='导出:测试*标题', markdown=markdown)
    result = exporter.build()

    assert_true(result.zip_filename.endswith(".zip"), "zip 文件名不正确")
    assert_true(result.markdown_filename.endswith(".md"), "markdown 文件名不正确")

    zf = zipfile.ZipFile(io.BytesIO(result.zip_bytes))
    names = set(zf.namelist())
    assert_true("assets/" in names, "ZIP 中缺少 assets 目录")
    assert_true(result.markdown_filename in names, "ZIP 中缺少 markdown 文件")
    assert_true(any(name.startswith("assets/") and name != "assets/" for name in names), "ZIP 中应包含图片文件")

    exported_md = zf.read(result.markdown_filename).decode("utf-8")
    assert_true("./assets/" in exported_md, "导出 markdown 没有改写为相对路径")
    assert_true("https://example.invalid/404.jpg" in exported_md, "外链失败时应保留原引用")

    # No-image markdown should still keep assets directory.
    no_image_result = MarkdownBundleExporter(
        task_id=task_id,
        title="no_image",
        markdown="# No Image",
    ).build()
    zf_no_image = zipfile.ZipFile(io.BytesIO(no_image_result.zip_bytes))
    assert_true("assets/" in set(zf_no_image.namelist()), "无图导出也应包含空 assets 目录")

    print("smoke_export_bundle: PASS")


if __name__ == "__main__":
    run_smoke()
