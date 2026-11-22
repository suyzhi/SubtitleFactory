"""
字幕工厂 - 字幕段管理、转写候选、字幕导入与导出/压制路由
"""

import json
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from ...models.database import segment_to_dict
from ...models.schemas import (
    ExportRequest,
    SegmentOperationItem,
    SegmentOperationRequest,
    SegmentUpdate,
)
from ...services.distribution import require_youtube_feature
from ...services.editor import EditorServiceError, execute_operation, import_segment_snapshot
from ...services.subtitle_exporter import export_ass, export_srt, export_vtt, get_subtitle_path
from ...services.subtitle_importer import parse_subtitle
from ...services.transcription_results import candidate_result
from ...services.video_renderer import burn_subtitles
from .common import (
    _active_task_conflict,
    _project_row,
    exports_dir,
    facade_attr,
    get_db,
    get_task_manager,
    projects_dir,
)
from .media import _do_download

router = APIRouter()


@router.post("/projects/{project_id}/subtitles/import")
async def import_subtitles_file(
    project_id: str,
    file: UploadFile = File(...),
    expected_revision: int = Form(...),
):
    extension = Path(file.filename or "").suffix.lower()
    if extension not in {".srt", ".vtt", ".ass"}:
        raise HTTPException(422, "仅支持 SRT、VTT 和 ASS 字幕")
    payload = await file.read(10 * 1024 * 1024 + 1)
    if len(payload) > 10 * 1024 * 1024:
        raise HTTPException(413, "字幕文件不能超过 10 MB")
    try:
        cues = parse_subtitle(payload, file.filename or f"subtitle{extension}")
        result = import_segment_snapshot(project_id, expected_revision, cues)
    except ValueError as error:
        raise HTTPException(
            422,
            detail={
                "code": "SUBTITLE_IMPORT_INVALID",
                "message": str(error),
                "suggestion": "请修正字幕格式后重试",
                "recoverable": True,
            },
        ) from error
    except EditorServiceError as error:
        raise HTTPException(error.status_code, detail=error.as_detail()) from error

    import_dir = projects_dir() / project_id / "imports"
    import_dir.mkdir(parents=True, exist_ok=True)
    original_path = import_dir / f"{result['operation_id']}{extension}"
    original_path.write_bytes(payload)
    db = get_db()
    try:
        db.execute(
            """INSERT INTO project_assets(id,project_id,kind,path,metadata_json,created_at)
               VALUES (?,?,?,?,?,datetime('now','localtime'))""",
            (
                str(uuid.uuid4()),
                project_id,
                "subtitle_original",
                str(original_path),
                json.dumps({"filename": Path(file.filename or "subtitle").name, "format": extension[1:]}),
            ),
        )
        db.commit()
    finally:
        db.close()
    return result


@router.get("/projects/{project_id}/transcription-candidates")
def transcription_candidates(project_id: str):
    db = get_db()
    try:
        rows = db.execute(
            """SELECT id,model,language,status,
            CASE WHEN status='candidate' THEN segments_count ELSE
                (SELECT COUNT(*) FROM transcription_segments s WHERE s.run_id=r.id AND s.project_id=r.project_id)
            END AS segments_count,started_at,finished_at
            FROM transcription_runs r WHERE project_id=? AND
            (status='candidate' OR (status IN ('cancelled','failed') AND finished_at IS NOT NULL
                AND EXISTS (SELECT 1 FROM transcription_segments s WHERE s.run_id=r.id AND s.project_id=r.project_id)))
            ORDER BY started_at DESC,id DESC""",
            (project_id,),
        ).fetchall()
        return {"candidates": [dict(row) for row in rows]}
    finally:
        db.close()


@router.get("/projects/{project_id}/transcription-candidates/{run_id}")
def transcription_candidate(project_id: str, run_id: str):
    db = get_db()
    try:
        result = candidate_result(db, project_id, run_id)
        if result is None:
            raise HTTPException(404, "候选结果不存在")
        return {"segments": result[0], "partial": result[1]}
    finally:
        db.close()


@router.post("/projects/{project_id}/transcription-candidates/{run_id}/accept")
def accept_transcription_candidate(project_id: str, run_id: str, expected_revision: int):
    try:
        return import_segment_snapshot(project_id, expected_revision, [], candidate_run_id=run_id)
    except EditorServiceError as error:
        raise HTTPException(error.status_code, detail=error.as_detail()) from error


@router.get("/projects/{project_id}/segments")
def get_segments(project_id: str, after_idx: int = 0):
    """获取项目的所有字幕段。after_idx>0 时只返回比该索引新的字幕（增量模式）。"""
    db = get_db()
    if after_idx > 0:
        rows = db.execute(
            "SELECT * FROM segments WHERE project_id = ? AND idx > ? ORDER BY idx",
            (project_id, after_idx),
        ).fetchall()
        # 获取当前最大 idx
        max_row = db.execute(
            "SELECT MAX(idx) as max_idx FROM segments WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        latest_idx = max_row["max_idx"] if max_row and max_row["max_idx"] else after_idx

        # 检查是否还有 is_draft=1 的 segments
        draft_row = db.execute(
            "SELECT COUNT(*) as cnt FROM segments WHERE project_id = ? AND is_draft = 1",
            (project_id,),
        ).fetchone()
        has_more = draft_row["cnt"] > 0 if draft_row else False
    else:
        rows = db.execute(
            "SELECT * FROM segments WHERE project_id = ? ORDER BY idx",
            (project_id,),
        ).fetchall()
        latest_idx = len(rows)
        has_more = False

    db.close()

    segments = [segment_to_dict(r) for r in rows]

    result = {
        "segments": segments,
        "total": len(rows),
    }
    if after_idx > 0:
        result["latest_idx"] = latest_idx
        result["has_more"] = has_more

    return result


@router.patch("/projects/{project_id}/segments/{segment_index}")
def update_segment(project_id: str, segment_index: int, update: SegmentUpdate):
    """兼容旧客户端的单条编辑接口；内部进入统一原子编辑引擎。"""
    db = get_db()
    project = db.execute("SELECT edit_revision FROM projects WHERE id=?", (project_id,)).fetchone()
    db.close()
    if not project:
        raise HTTPException(404, "项目不存在")
    values = update.model_dump(exclude_unset=True)
    item = SegmentOperationItem(index=segment_index, **values)
    try:
        result = execute_operation(
            project_id,
            SegmentOperationRequest(
                expected_revision=int(project["edit_revision"] or 0),
                operation="update_many",
                items=[item],
                include_locked=set(values) == {"locked"},
            ),
        )
    except EditorServiceError as error:
        raise HTTPException(error.status_code, detail=error.as_detail()) from error
    return next(segment for segment in result["segments"] if segment["index"] == segment_index)


def _do_video_export(
    task_id: str,
    project_id: str,
    subtitle_path: str,
    output_path: str,
    fmt: str,
    bilingual: bool,
    style_settings: dict | None,
):
    tm = get_task_manager()
    row = _project_row(project_id)
    if not row:
        raise RuntimeError("项目不存在")
    stages = {"download_video": "waiting", "render_video": "waiting"}
    video_path = row["video_path"]
    if not video_path or not os.path.isfile(video_path):
        if row["source_type"] != "youtube" or not row["source_url"]:
            raise RuntimeError("视频文件不存在，且项目没有可重新下载的链接")
        stages["download_video"] = "running"
        tm.update_task(
            task_id,
            step="materialize_video",
            progress=1,
            message="首次视频导出：正在下载并保留本地视频",
            details={"stages": stages, "format": fmt},
        )
        facade_attr("_do_download", _do_download)(
            task_id,
            project_id,
            row["source_url"],
            preserve_metadata=True,
            progress_start=2,
            progress_end=52,
            completed_progress=52,
            materialization_reason="video_export",
        )
        row = _project_row(project_id)
        video_path = row["video_path"] if row else None
    if not video_path or not os.path.isfile(video_path):
        raise RuntimeError("本地视频准备失败，无法压制字幕")
    stages["download_video"] = "success"
    stages["render_video"] = "running"
    tm.update_task(
        task_id,
        step="rendering",
        progress=55,
        message="本地视频已就绪，正在压制字幕",
        details={"stages": stages, "format": fmt},
    )
    burn_subtitles(
        task_id,
        video_path,
        subtitle_path,
        output_path,
        project_id,
        bilingual=bilingual,
        style_settings=style_settings,
    )
    stages["render_video"] = "success"
    tm.update_task(
        task_id,
        step="render_done",
        progress=95,
        message="视频压制完成",
        details={"stages": stages, "format": fmt},
    )


@router.post("/projects/{project_id}/export")
def export_subtitles(project_id: str, req: ExportRequest):
    """导出字幕或压制视频"""
    db = get_db()
    row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    segments_rows = db.execute(
        "SELECT * FROM segments WHERE project_id = ? ORDER BY idx",
        (project_id,),
    ).fetchall()
    style_row = db.execute(
        "SELECT settings_json FROM project_styles WHERE project_id=?", (project_id,)
    ).fetchone()
    db.close()

    if not row:
        raise HTTPException(404, "项目不存在")
    if not segments_rows:
        raise HTTPException(400, "没有字幕数据")

    segments = [segment_to_dict(r) for r in segments_rows]

    fmt = req.format
    bilingual = req.bilingual
    style_settings = req.style
    if style_settings is None and style_row:
        try:
            style_settings = json.loads(style_row["settings_json"])
        except json.JSONDecodeError:
            style_settings = None

    if fmt == "srt":
        out = get_subtitle_path(project_id, "srt")
        export_srt(segments, out, bilingual=bilingual, primary_lang=req.primary_language)
    elif fmt == "vtt":
        out = get_subtitle_path(project_id, "vtt")
        export_vtt(segments, out, bilingual=bilingual, primary_lang=req.primary_language)
    elif fmt == "ass":
        out = get_subtitle_path(project_id, "ass")
        export_ass(
            segments,
            out,
            bilingual=bilingual,
            primary_lang=req.primary_language,
            settings=style_settings,
        )
    elif fmt == "srt-bilingual":
        out = get_subtitle_path(project_id, "bilingual.srt")
        export_srt(segments, out, bilingual=True, primary_lang=req.primary_language)
    elif fmt in {"mp4", "mkv"}:
        if row["source_type"] == "youtube" and (
            not row["video_path"] or not os.path.isfile(row["video_path"])
        ):
            require_youtube_feature()
        if _active_task_conflict(project_id):
            raise HTTPException(
                409,
                detail={
                    "code": "MEDIA_TASK_ACTIVE",
                    "message": "项目已有任务正在运行，完成后再导出视频",
                },
            )
        if (not row["video_path"] or not os.path.exists(row["video_path"])) and (
            row["source_type"] != "youtube" or not row["source_url"]
        ):
            raise HTTPException(400, "视频文件不存在，且项目没有可重新下载的链接")

        # 先导出 ASS 字幕
        ass_path = get_subtitle_path(project_id, "ass")
        export_ass(
            segments,
            ass_path,
            bilingual=bilingual,
            primary_lang=req.primary_language,
            settings=style_settings,
        )

        # 后台压制
        tm = get_task_manager()
        task_id = tm.create_task(project_id, "render")
        tm.update_task(
            task_id,
            details={
                "format": fmt,
                "bilingual": bilingual,
                "style": style_settings or {},
            },
        )
        output_path = os.path.join(exports_dir(), f"{project_id}_hardsub.{fmt}")
        tm.run_background(
            task_id,
            _do_video_export,
            project_id,
            ass_path,
            output_path,
            fmt,
            bilingual,
            style_settings,
        )
        return {"task_id": task_id, "message": f"{fmt.upper()} 视频导出任务已创建"}
    else:
        raise HTTPException(400, f"不支持的导出格式: {fmt}")

    return {"path": out, "message": f"字幕已导出: {fmt}"}


@router.get("/projects/{project_id}/export/download")
def download_export(
    project_id: str,
    fmt: str = Query("srt", pattern="^(srt|vtt|ass|srt-bilingual|mp4|mkv)$"),
):
    """下载已导出的字幕文件"""
    if fmt in {"mp4", "mkv"}:
        filepath = os.path.join(exports_dir(), f"{project_id}_hardsub.{fmt}")
    else:
        ext = fmt.replace("srt-bilingual", "bilingual.srt")
        filepath = get_subtitle_path(project_id, ext)
    if not os.path.exists(filepath):
        raise HTTPException(404, "导出文件不存在，请先导出")
    return FileResponse(filepath, filename=os.path.basename(filepath))
