"""
字幕工厂（简洁版）- 字幕段读取与 SRT 导出/下载路由
"""

import os

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from ...models.database import segment_to_dict
from ...models.schemas import ExportRequest
from ...services.subtitle_exporter import export_srt, get_subtitle_path
from .common import get_db

router = APIRouter()


@router.get("/projects/{project_id}/segments")
def get_segments(project_id: str, after_idx: int = 0):
    """获取项目的所有字幕段。after_idx>0 时只返回比该索引新的字幕。"""
    db = get_db()
    if after_idx > 0:
        rows = db.execute(
            "SELECT * FROM segments WHERE project_id = ? AND idx > ? ORDER BY idx",
            (project_id, after_idx),
        ).fetchall()
        max_row = db.execute(
            "SELECT MAX(idx) as max_idx FROM segments WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        latest_idx = max_row["max_idx"] if max_row and max_row["max_idx"] else after_idx
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


@router.post("/projects/{project_id}/export")
def export_subtitles(project_id: str, req: ExportRequest):
    """导出单语或双语 SRT 字幕"""
    db = get_db()
    row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    segments_rows = db.execute(
        "SELECT * FROM segments WHERE project_id = ? ORDER BY idx",
        (project_id,),
    ).fetchall()
    db.close()

    if not row:
        raise HTTPException(404, "项目不存在")
    if not segments_rows:
        raise HTTPException(400, "没有字幕数据")

    segments = [segment_to_dict(r) for r in segments_rows]
    fmt = req.format
    if fmt == "srt":
        out = get_subtitle_path(project_id, "srt")
        export_srt(
            segments,
            out,
            bilingual=req.bilingual,
            primary_lang=req.primary_language,
        )
    elif fmt == "srt-bilingual":
        out = get_subtitle_path(project_id, "bilingual.srt")
        export_srt(
            segments,
            out,
            bilingual=True,
            primary_lang=req.primary_language,
        )
    else:
        raise HTTPException(400, f"不支持的导出格式: {fmt}")

    return {"path": out, "message": f"字幕已导出: {fmt}"}


@router.get("/projects/{project_id}/export/download")
def download_export(
    project_id: str,
    fmt: str = Query("srt", pattern="^(srt|srt-bilingual)$"),
):
    """下载已导出的 SRT 字幕文件"""
    ext = fmt.replace("srt-bilingual", "bilingual.srt")
    filepath = get_subtitle_path(project_id, ext)
    if not os.path.exists(filepath):
        raise HTTPException(404, "导出文件不存在，请先导出")
    return FileResponse(filepath, filename=os.path.basename(filepath))
