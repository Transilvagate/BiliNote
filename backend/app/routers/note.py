# app/routers/note.py
import io
import json
import os
import uuid
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import quote, urlparse

import httpx
from dataclasses import asdict

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, field_validator

from app.db.video_task_dao import get_task_by_video
from app.enmus.exception import NoteErrorEnum
from app.enmus.note_enums import DownloadQuality
from app.enmus.task_status_enums import TaskStatus
from app.exceptions.note import NoteError
from app.services.markdown_bundle_export import MarkdownBundleExporter
from app.services.note import NoteGenerator, logger
from app.services.task_serial_executor import task_serial_executor
from app.utils.response import ResponseWrapper as R
from app.utils.task_assets import get_task_assets_dir, safe_join_under
from app.utils.url_parser import extract_video_id
from app.validators.video_url_validator import is_supported_video_url

# from app.services.downloader import download_raw_audio
# from app.services.whisperer import transcribe_audio

router = APIRouter()


class RecordRequest(BaseModel):
    video_id: str
    platform: str


class VideoRequest(BaseModel):
    video_url: str
    platform: str
    quality: DownloadQuality
    screenshot: Optional[bool] = False
    link: Optional[bool] = False
    model_name: str
    provider_id: str
    task_id: Optional[str] = None
    format: Optional[list] = []
    style: str = None
    extras: Optional[str]=None
    formal_transcript_style: Literal[
        "auto",
        "single_narration",
        "lead_plus_support",
        "multi_dialogue",
    ] = "auto"
    transcript_source: Literal["auto", "bbdown", "asr"] = "auto"
    video_understanding: Optional[bool] = False
    video_interval: Optional[int] = 0
    grid_size: Optional[list] = []
    force_refresh_transcript: Optional[bool] = None

    @field_validator("video_url")
    def validate_supported_url(cls, v):
        url = str(v)
        parsed = urlparse(url)
        if parsed.scheme in ("http", "https"):
            # 是网络链接，继续用原有平台校验
            if not is_supported_video_url(url):
                raise NoteError(code=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.code,
                                message=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.message)

        return v


class ExportMarkdownRequest(BaseModel):
    title: str
    markdown: str


NOTE_OUTPUT_DIR = os.getenv("NOTE_OUTPUT_DIR", "note_results")
UPLOAD_DIR = "uploads"


def save_note_to_file(task_id: str, note):
    os.makedirs(NOTE_OUTPUT_DIR, exist_ok=True)
    with open(os.path.join(NOTE_OUTPUT_DIR, f"{task_id}.json"), "w", encoding="utf-8") as f:
        json.dump(asdict(note), f, ensure_ascii=False, indent=2)


def run_note_task(task_id: str, video_url: str, platform: str, quality: DownloadQuality,
                  link: bool = False, screenshot: bool = False, model_name: str = None, provider_id: str = None,
                  _format: list = None, style: str = None, extras: str = None, formal_transcript_style: str = "auto",
                  transcript_source: str = "auto",
                  video_understanding: bool = False,
                  video_interval=0, grid_size=None, force_refresh_transcript: bool = False
                  ):
    if grid_size is None:
        grid_size = []

    if not model_name or not provider_id:
        raise HTTPException(status_code=400, detail="请选择模型和提供者")

    def _execute_note_task():
        return NoteGenerator().generate(
            video_url=video_url,
            platform=platform,
            quality=quality,
            task_id=task_id,
            model_name=model_name,
            provider_id=provider_id,
            link=link,
            _format=_format,
            style=style,
            extras=extras,
            formal_transcript_style=formal_transcript_style,
            transcript_source=transcript_source,
            screenshot=screenshot,
            video_understanding=video_understanding,
            video_interval=video_interval,
            grid_size=grid_size,
            force_refresh_transcript=force_refresh_transcript,
        )

    logger.info(f"任务进入执行队列 (task_id={task_id})")
    note = task_serial_executor.run(_execute_note_task)
    logger.info(f"Note generated: {task_id}")
    if not note or not note.markdown:
        logger.warning(f"任务 {task_id} 执行失败，跳过保存")
        return
    save_note_to_file(task_id, note)

    # 自动建立向量索引（用于 AI 问答），失败不影响笔记生成
    try:
        from app.services.vector_store import VectorStoreManager
        VectorStoreManager().index_task(task_id)
    except Exception as e:
        logger.warning(f"向量索引失败（不影响笔记）: {e}")


@router.post('/delete_task')
def delete_task(data: RecordRequest):
    try:
        # TODO: 待持久化完成
        # NoteGenerator().delete_note(video_id=data.video_id, platform=data.platform)
        return R.success(msg='删除成功')
    except Exception as e:
        return R.error(msg=e)


@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    file_location = os.path.join(UPLOAD_DIR, file.filename)

    with open(file_location, "wb+") as f:
        f.write(await file.read())

    # 假设你静态目录挂载了 /uploads
    return R.success({"url": f"/uploads/{file.filename}"})


@router.post("/generate_note")
def generate_note(data: VideoRequest, background_tasks: BackgroundTasks):
    try:

        video_id = extract_video_id(data.video_url, data.platform)
        # if not video_id:
        #     raise HTTPException(status_code=400, detail="无法提取视频 ID")
        # existing = get_task_by_video(video_id, data.platform)
        # if existing:
        #     return R.error(
        #         msg='笔记已生成，请勿重复发起',
        #
        #     )
        if data.task_id:
            # 如果传了task_id，说明是重试！
            task_id = data.task_id
            force_refresh_transcript = (
                data.force_refresh_transcript
                if data.force_refresh_transcript is not None
                else True
            )
            # 更新之前的状态
            NoteGenerator()._update_status(
                task_id,
                TaskStatus.PENDING,
                message="任务重试中",
                detail="已提交重试任务，正在重新拉取字幕与转写",
                source="system",
                diagnostics={"force_refresh_transcript": force_refresh_transcript},
            )
            logger.info(f"重试模式，复用已有 task_id={task_id}")
        else:
            # 正常新建任务
            task_id = str(uuid.uuid4())
            force_refresh_transcript = bool(data.force_refresh_transcript)
            NoteGenerator()._update_status(
                task_id,
                TaskStatus.PENDING,
                message="任务排队中",
                detail="任务已提交，等待开始处理",
                source="system",
                diagnostics={"force_refresh_transcript": force_refresh_transcript},
            )

        background_tasks.add_task(run_note_task, task_id, data.video_url, data.platform, data.quality, data.link,
                                  data.screenshot, data.model_name, data.provider_id, data.format, data.style,
                                  data.extras, data.formal_transcript_style, data.transcript_source,
                                  data.video_understanding, data.video_interval, data.grid_size,
                                  force_refresh_transcript)
        return R.success({"task_id": task_id})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/task_status/{task_id}")
def get_task_status(task_id: str):
    status_path = os.path.join(NOTE_OUTPUT_DIR, f"{task_id}.status.json")
    result_path = os.path.join(NOTE_OUTPUT_DIR, f"{task_id}.json")

    # 优先读状态文件
    if os.path.exists(status_path):
        with open(status_path, "r", encoding="utf-8") as f:
            status_content = json.load(f)

        status = status_content.get("status", TaskStatus.PENDING.value)
        payload = {
            "status": status,
            "message": status_content.get("message", ""),
            "detail": status_content.get("detail", ""),
            "task_id": task_id,
            "step": status_content.get("step"),
            "source": status_content.get("source"),
            "started_at": status_content.get("started_at"),
            "updated_at": status_content.get("updated_at"),
            "elapsed_ms": status_content.get("elapsed_ms"),
            "events": status_content.get("events", []),
            "diagnostics": status_content.get("diagnostics", {}),
        }
        if status_content.get("error"):
            payload["error"] = status_content.get("error")

        if status == TaskStatus.SUCCESS.value:
            # 成功状态的话，继续读取最终笔记内容
            if os.path.exists(result_path):
                with open(result_path, "r", encoding="utf-8") as rf:
                    result_content = json.load(rf)
                payload["result"] = result_content
                return R.success(payload)
            else:
                # 理论上不会出现，保险处理
                payload.update({
                    "status": TaskStatus.PENDING.value,
                    "message": "任务完成，但结果文件未找到",
                })
                return R.success(payload)

        if status == TaskStatus.FAILED.value:
            if not payload.get("error"):
                payload["error"] = {"reason_code": "TASK_FAILED", "retryable": True}
            return R.success(payload)

        # 处理中状态
        return R.success(payload)

    # 没有状态文件，但有结果
    if os.path.exists(result_path):
        with open(result_path, "r", encoding="utf-8") as f:
            result_content = json.load(f)
        return R.success({
            "status": TaskStatus.SUCCESS.value,
            "result": result_content,
            "task_id": task_id,
            "message": "任务完成",
        })

    # 什么都没有，默认PENDING
    return R.success({
        "status": TaskStatus.PENDING.value,
        "message": "任务排队中",
        "detail": "任务尚未开始，请稍候",
        "task_id": task_id,
        "events": [],
        "diagnostics": {},
    })


@router.get("/tasks/{task_id}/assets/{asset_path:path}")
def get_task_asset(task_id: str, asset_path: str):
    assets_dir = get_task_assets_dir(task_id, create=False)
    try:
        file_path = safe_join_under(assets_dir, asset_path)
    except ValueError:
        raise HTTPException(status_code=404, detail="图片不存在")

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="图片不存在")

    return FileResponse(str(file_path))


@router.post("/tasks/{task_id}/export-md")
def export_markdown_with_assets(task_id: str, payload: ExportMarkdownRequest):
    exporter = MarkdownBundleExporter(task_id=task_id, title=payload.title, markdown=payload.markdown)
    result = exporter.build()
    for item in result.warnings:
        logger.warning(f"[export-md] task_id={task_id}: {item}")

    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(result.zip_filename)}",
    }
    return StreamingResponse(io.BytesIO(result.zip_bytes), media_type="application/zip", headers=headers)


@router.get("/image_proxy")
async def image_proxy(request: Request, url: str):
    headers = {
        "Referer": "https://www.bilibili.com/",
        "User-Agent": request.headers.get("User-Agent", ""),
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)

            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail="图片获取失败")

            content_type = resp.headers.get("Content-Type", "image/jpeg")
            return StreamingResponse(
                resp.aiter_bytes(),
                media_type=content_type,
                headers={
                    "Cache-Control": "public, max-age=86400",  #  缓存一天
                    "Content-Type": content_type,
                }
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
