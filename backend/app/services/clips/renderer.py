"""FFmpeg batch clip rendering, output verification, and render lifecycle management."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from ...models.database import get_db
from ...utils.config import EXPORTS_DIR
from ...utils.task_manager import TaskCancelled, task_manager
from ..ffmpeg_encoding import select_h264_encoder_args
from ..runtime_diagnostics import resolve_ffmpeg_path, resolve_ffprobe_path
from .common import ASPECT_DIMENSIONS, ClipError, _now, _source, facade_attr
from .overlays import _filter_graph, _subtitle_overlay_paths


def _run_process(task_id: str, command: list[str], duration: float) -> None:
    del duration
    tm = facade_attr("task_manager", task_manager)
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as error_log:
        process = subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=error_log, text=True
        )
        stopped = False
        try:
            while process.poll() is None:
                state = tm.get_task(task_id) or {}
                if state.get("status") == "paused" and not stopped:
                    os.kill(process.pid, signal.SIGSTOP)
                    stopped = True
                try:
                    tm.checkpoint(task_id)
                except TaskCancelled:
                    if stopped:
                        os.kill(process.pid, signal.SIGCONT)
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    raise
                if stopped:
                    os.kill(process.pid, signal.SIGCONT)
                    stopped = False
                time.sleep(0.2)
            if process.returncode:
                error_log.seek(0)
                stderr = error_log.read()[-1000:]
                raise ClipError(
                    f"短片渲染失败：{stderr}",
                    error_code="CLIP_RENDER_FAILED",
                    details={"ffmpeg_tail": stderr},
                )
        finally:
            if process.poll() is None:
                process.kill()


def _probe(path: Path, width: int, height: int, expected_duration: float) -> dict[str, Any]:
    ffprobe = facade_attr("resolve_ffprobe_path", resolve_ffprobe_path)()
    if not ffprobe:
        raise ClipError("短片验证缺少 FFprobe", error_code="DOWNLOAD_RUNTIME_MISSING")
    result = subprocess.run(
        [
            str(ffprobe.path),
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode:
        raise ClipError("FFprobe 无法读取短片输出", error_code="CLIP_VALIDATION_FAILED")
    payload = json.loads(result.stdout)
    streams = payload.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    duration = float((payload.get("format") or {}).get("duration") or 0)
    if (
        not video
        or not audio
        or int(video.get("width") or 0) != width
        or int(video.get("height") or 0) != height
    ):
        raise ClipError("短片流或画面尺寸验证失败", error_code="CLIP_VALIDATION_FAILED")
    if abs(duration - expected_duration) > max(0.3, 1 / 24):
        raise ClipError("短片时长验证失败", error_code="CLIP_VALIDATION_FAILED")
    return {"width": width, "height": height, "duration": duration, "size": path.stat().st_size}


def _configuration(candidate: dict[str, Any], layout: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    config = {
        "renderer_version": 3,
        "candidate_id": candidate["id"],
        "start": candidate["start"],
        "end": candidate["end"],
        "aspect_ratio": layout["aspect_ratio"],
        "composition": layout["composition"],
        "focal_x": layout["focal_x"],
        "focal_y": layout["focal_y"],
        "subtitle_mode": layout["subtitle_mode"],
        "style": layout.get("style") or {},
    }
    return (
        hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        config,
    )


def create_render_batch(
    project_id: str, items: list[dict[str, str]], confirm_stale: bool
) -> tuple[str, list[str]]:
    tm = facade_attr("task_manager", task_manager)
    project, _rows = _source(project_id)
    render_ids: list[str] = []
    jobs: list[dict[str, Any]] = []
    db = get_db()
    try:
        for request in items:
            candidate_id, aspect = request["candidate_id"], request["aspect_ratio"]
            row = db.execute(
                """SELECT c.*,cs.project_id,cs.source_revision,p.edit_revision current_project_revision
                     FROM clip_candidates c JOIN clip_sets cs ON cs.id=c.clip_set_id
                     JOIN projects p ON p.id=cs.project_id
                    WHERE c.id=? AND cs.project_id=?""",
                (candidate_id, project_id),
            ).fetchone()
            layout_row = db.execute(
                "SELECT * FROM clip_layouts WHERE candidate_id=? AND aspect_ratio=?",
                (candidate_id, aspect),
            ).fetchone()
            if not row or not layout_row or aspect not in ASPECT_DIMENSIONS:
                raise ClipError("短片候选或布局不存在", error_code="CLIP_NOT_FOUND", recoverable=False)
            candidate = dict(row)
            if int(
                candidate["source_confirmed_revision"]
                if candidate["source_confirmed_revision"] is not None
                else -1
            ) != int(candidate["current_project_revision"]):
                if not confirm_stale:
                    raise ClipError(
                        "字幕已更新，请重新预览并确认短片范围",
                        error_code="CLIP_SOURCE_STALE",
                        details={"candidate_id": candidate_id},
                    )
                db.execute(
                    "UPDATE clip_candidates SET source_confirmed_revision=?,updated_at=? WHERE id=?",
                    (candidate["current_project_revision"], _now(), candidate_id),
                )
            layout = {**dict(layout_row), "style": json.loads(layout_row["style_json"] or "{}")}
            fingerprint, _config = _configuration(candidate, layout)
            existing = db.execute(
                """SELECT * FROM clip_renders
                    WHERE configuration_fingerprint=? AND status='success'
                    ORDER BY updated_at DESC LIMIT 1""",
                (fingerprint,),
            ).fetchone()
            if existing and existing["path"] and Path(existing["path"]).is_file():
                render_ids.append(existing["id"])
                continue
            render_id, now = str(uuid.uuid4()), _now()
            db.execute(
                """INSERT INTO clip_renders(
                       id,project_id,candidate_id,aspect_ratio,configuration_fingerprint,
                       status,created_at,updated_at
                   ) VALUES (?,?,?,?,?,'pending',?,?)""",
                (render_id, project_id, candidate_id, aspect, fingerprint, now, now),
            )
            render_ids.append(render_id)
            jobs.append(
                {
                    "render_id": render_id,
                    "candidate": candidate,
                    "layout": layout,
                    "fingerprint": fingerprint,
                }
            )
        db.commit()
    finally:
        db.close()
    if not jobs:
        return "", render_ids
    task_id = tm.create_task(project_id, "clip_render_batch", resource_class="ffmpeg")
    tm.update_task(
        task_id,
        details={
            "clip_set_id": str(jobs[0]["candidate"].get("clip_set_id") or "") if jobs else "",
            "render_ids": list(render_ids),
        },
    )
    db = get_db()
    try:
        db.executemany(
            "UPDATE clip_renders SET task_id=?,updated_at=? WHERE id=?",
            [(task_id, _now(), item["render_id"]) for item in jobs],
        )
        db.commit()
    finally:
        db.close()
    tm.run_background(task_id, render_batch, project, jobs)
    return task_id, render_ids


def render_batch(task_id: str, project: dict[str, Any], jobs: list[dict[str, Any]]) -> None:
    tm = facade_attr("task_manager", task_manager)
    exports_dir = facade_attr("EXPORTS_DIR", EXPORTS_DIR)
    video_path = project.get("video_path")
    if not video_path or not Path(video_path).is_file():
        if project.get("source_type") != "youtube" or not project.get("source_url"):
            raise ClipError("视频文件不存在且无法重新下载", error_code="MEDIA_NOT_AVAILABLE")
        tm.update_task(
            task_id,
            step="materialize_video",
            progress=1,
            message="正在下载并保留本地视频以生成短片",
        )
        from ...api.projects import _do_download

        _do_download(
            task_id,
            project["id"],
            project["source_url"],
            preserve_metadata=True,
            progress_start=2,
            progress_end=25,
            completed_progress=25,
            materialization_reason="clip_render",
        )
        project, _rows = _source(project["id"])
        video_path = project.get("video_path")
    if not video_path or not Path(video_path).is_file():
        raise ClipError("本地视频准备失败", error_code="MEDIA_NOT_AVAILABLE")
    ffmpeg = facade_attr("resolve_ffmpeg_path", resolve_ffmpeg_path)()
    if not ffmpeg:
        raise ClipError("短片渲染缺少 FFmpeg", error_code="DOWNLOAD_RUNTIME_MISSING")
    output_root = Path(exports_dir) / "clips" / project["id"]
    output_root.mkdir(parents=True, exist_ok=True)
    video_codec_args, video_codec_name = select_h264_encoder_args(ffmpeg.path)
    successes: list[str] = []
    failures: list[str] = []
    for position, job in enumerate(jobs, 1):
        render_id = job["render_id"]
        candidate, layout = job["candidate"], job["layout"]
        width, height = ASPECT_DIMENSIONS[layout["aspect_ratio"]]
        target = (
            output_root
            / f"{candidate['id']}-{layout['aspect_ratio'].replace(':', 'x')}-{job['fingerprint'][:10]}.mp4"
        )
        temporary = target.with_suffix(f".{uuid.uuid4().hex}.part.mp4")
        subtitle_path = None
        subtitle_overlays: list[dict[str, Any]] = []
        db = get_db()
        try:
            db.execute(
                "UPDATE clip_renders SET status='running',updated_at=? WHERE id=?",
                (_now(), render_id),
            )
            db.commit()
        finally:
            db.close()
        try:
            if layout["subtitle_mode"] != "off":
                subtitle_overlays = _subtitle_overlay_paths(
                    task_id, candidate, layout, project["id"]
                )
            filter_graph = _filter_graph(width, height, layout, subtitle_path, subtitle_overlays)
            command = [
                str(ffmpeg.path),
                "-ss",
                f"{float(candidate['start']):.3f}",
                "-i",
                str(video_path),
                "-t",
                f"{float(candidate['end']) - float(candidate['start']):.3f}",
                "-filter_complex",
                filter_graph,
                "-map",
                "[v]",
                "-map",
                "0:a?",
                *video_codec_args,
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                "-y",
                str(temporary),
            ]
            tm.update_task(
                task_id,
                step="clip_rendering",
                progress=25 + (position - 1) / len(jobs) * 70,
                message=f"正在渲染短片 {position}/{len(jobs)} · {layout['aspect_ratio']}",
                details={
                    "render_id": render_id,
                    "candidate_id": candidate["id"],
                    "aspect_ratio": layout["aspect_ratio"],
                    "video_codec": video_codec_name,
                },
            )
            facade_attr("_run_process", _run_process)(
                task_id, command, float(candidate["end"]) - float(candidate["start"])
            )
            metadata = _probe(
                temporary, width, height, float(candidate["end"]) - float(candidate["start"])
            )
            digest = hashlib.sha256()
            with temporary.open("rb") as rendered:
                for chunk in iter(lambda: rendered.read(1024 * 1024), b""):
                    digest.update(chunk)
            checksum = digest.hexdigest()
            os.replace(temporary, target)
            db = get_db()
            try:
                db.execute(
                    """UPDATE clip_renders
                          SET path=?,status='success',error=NULL,width=?,height=?,duration=?,
                              size=?,checksum=?,updated_at=? WHERE id=?""",
                    (
                        str(target),
                        metadata["width"],
                        metadata["height"],
                        metadata["duration"],
                        metadata["size"],
                        checksum,
                        _now(),
                        render_id,
                    ),
                )
                db.commit()
            finally:
                db.close()
            successes.append(render_id)
        except TaskCancelled:
            temporary.unlink(missing_ok=True)
            db = get_db()
            try:
                db.execute(
                    "UPDATE clip_renders SET status='cancelled',updated_at=? WHERE id=?",
                    (_now(), render_id),
                )
                remaining_ids = [pending["render_id"] for pending in jobs[position:]]
                if remaining_ids:
                    placeholders = ",".join("?" for _ in remaining_ids)
                    db.execute(
                        f"""UPDATE clip_renders
                               SET status='cancelled',updated_at=?
                             WHERE status='pending' AND id IN ({placeholders})""",
                        (_now(), *remaining_ids),
                    )
                db.commit()
            finally:
                db.close()
            raise
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            failures.append(render_id)
            db = get_db()
            try:
                db.execute(
                    "UPDATE clip_renders SET status='failed',error=?,updated_at=? WHERE id=?",
                    (str(exc)[:1000], _now(), render_id),
                )
                db.commit()
            finally:
                db.close()
            tm.add_log(
                task_id,
                "warning",
                "clip_rendering",
                "一个短片输出失败",
                detail=str(exc),
                suggestion="已成功的其他比例仍可下载，可单独重试失败项",
            )
        finally:
            if subtitle_path:
                Path(subtitle_path).unlink(missing_ok=True)
            for overlay in subtitle_overlays:
                Path(overlay["path"]).unlink(missing_ok=True)
    tm.update_task(
        task_id,
        step="clip_render_done",
        progress=100,
        status="partial" if failures else "running",
        message=f"短片渲染完成：{len(successes)} 成功，{len(failures)} 失败",
        details={"successful_renders": successes, "failed_renders": failures},
    )


def get_render(render_id: str) -> dict[str, Any]:
    db = get_db()
    try:
        row = db.execute("SELECT * FROM clip_renders WHERE id=?", (render_id,)).fetchone()
        if not row:
            raise FileNotFoundError("短片输出不存在")
        return dict(row)
    finally:
        db.close()


def delete_render(render_id: str) -> bool:
    db = get_db()
    try:
        row = db.execute("SELECT path FROM clip_renders WHERE id=?", (render_id,)).fetchone()
        if not row:
            return False
        if row["path"]:
            Path(row["path"]).unlink(missing_ok=True)
        db.execute("DELETE FROM clip_renders WHERE id=?", (render_id,))
        db.commit()
        return True
    finally:
        db.close()
