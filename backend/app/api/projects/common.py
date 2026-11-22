"""
字幕工厂（简洁版）- 项目路由共享工具与依赖访问层
"""

import logging
import os
import shutil
import sys
import wave
from importlib import import_module
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from ...models.database import get_db as _default_get_db
from ...services.app_settings import (
    get_effective_app_settings as _default_get_app_settings,
)
from ...services.distribution import require_youtube_feature
from ...services.transcriber import (
    get_transcription_model_status as _default_get_transcription_model_status,
)
from ...services.transcriber import transcribe_audio as _default_transcribe_audio
from ...utils.config import (
    AUDIO_DIR as _DEFAULT_AUDIO_DIR,
)
from ...utils.config import (
    DATA_DIR as _DEFAULT_DATA_DIR,
)
from ...utils.config import (
    DOWNLOADS_DIR as _DEFAULT_DOWNLOADS_DIR,
)
from ...utils.config import (
    EXPORTS_DIR as _DEFAULT_EXPORTS_DIR,
)
from ...utils.config import (
    PROJECTS_DIR as _DEFAULT_PROJECTS_DIR,
)
from ...utils.config import (
    SUBTITLES_DIR as _DEFAULT_SUBTITLES_DIR,
)
from ...utils.task_manager import task_manager as _default_task_manager

logger = logging.getLogger("app.api.projects")

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".avi"}


def facade_attr(name: str, default: Any) -> Any:
    """Resolve an attribute via the ``app.api.projects`` package facade when loaded."""
    pkg = sys.modules.get(__package__ or "")
    return getattr(pkg, name, default) if pkg is not None else default


def get_db():
    return facade_attr("get_db", _default_get_db)()


def get_app_settings() -> dict:
    return facade_attr("get_app_settings", _default_get_app_settings)()


def get_task_manager():
    return facade_attr("task_manager", _default_task_manager)


def get_logger() -> logging.Logger:
    return facade_attr("logger", logger)


def get_model_status(*args, **kwargs) -> dict:
    fn = facade_attr(
        "get_transcription_model_status", _default_get_transcription_model_status
    )
    return fn(*args, **kwargs)


def run_transcribe_audio(*args, **kwargs):
    fn = facade_attr("transcribe_audio", _default_transcribe_audio)
    return fn(*args, **kwargs)


def data_dir() -> Path:
    return Path(facade_attr("DATA_DIR", _DEFAULT_DATA_DIR))


def projects_dir() -> Path:
    return Path(facade_attr("PROJECTS_DIR", _DEFAULT_PROJECTS_DIR))


def downloads_dir() -> Path:
    return Path(facade_attr("DOWNLOADS_DIR", _DEFAULT_DOWNLOADS_DIR))


def audio_dir() -> Path:
    return Path(facade_attr("AUDIO_DIR", _DEFAULT_AUDIO_DIR))


def subtitles_dir() -> Path:
    return Path(facade_attr("SUBTITLES_DIR", _DEFAULT_SUBTITLES_DIR))


def exports_dir() -> Path:
    return Path(facade_attr("EXPORTS_DIR", _DEFAULT_EXPORTS_DIR))


def _download_service():
    require_youtube_feature()
    return import_module("...services." + "downloader", __package__)


def download_video(*args, **kwargs):
    return _download_service().download_video(*args, **kwargs)


def normalize_youtube_url(*args, **kwargs):
    return _download_service().normalize_youtube_url(*args, **kwargs)


def remove_managed_download_file(*args, **kwargs):
    return _download_service().remove_managed_download_file(*args, **kwargs)


def _project_row(project_id: str):
    db = get_db()
    try:
        return db.execute(
            "SELECT * FROM projects WHERE id=?", (project_id,)
        ).fetchone()
    finally:
        db.close()


def _active_task_conflict(project_id: str) -> list[str]:
    return get_task_manager().active_task_ids(project_id)


def _raise_active_task_conflict(task_ids: list[str]) -> None:
    raise HTTPException(
        status_code=409,
        detail={
            "code": "ACTIVE_TASKS",
            "message": "项目正在处理；请先取消或等待任务完成后再删除项目",
            "task_ids": task_ids,
        },
    )


def _path_is_managed(path: Path) -> bool:
    """Only remove files below App-owned storage roots."""
    roots = {
        data_dir(),
        projects_dir(),
        downloads_dir(),
        audio_dir(),
        subtitles_dir(),
        exports_dir(),
    }
    resolved = path.resolve(strict=False)
    resolved_roots = {root.expanduser().resolve(strict=False) for root in roots}
    # A storage root itself is never deletable, even when it also lies below
    # another root (e.g. PROJECTS_DIR inside DATA_DIR).
    if resolved in resolved_roots:
        return False
    return any(resolved.is_relative_to(root) for root in resolved_roots)


def _remove_managed_path(path: Path) -> None:
    if not _path_is_managed(path) or not path.exists():
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _purge_project_files(row) -> None:
    """Delete only App-managed media, subtitles, and exports."""
    project_id = row["id"]
    candidates = {
        projects_dir() / project_id,
        downloads_dir() / project_id,
        audio_dir() / project_id,
        subtitles_dir() / project_id,
        exports_dir() / project_id,
    }
    for column in ("video_path", "audio_path"):
        value = row[column] if column in row.keys() else None
        if value:
            candidates.add(Path(value))
    for root in (subtitles_dir(), exports_dir()):
        if root.is_dir():
            candidates.update(
                item for item in root.iterdir() if item.name.startswith(f"{project_id}_")
            )
    for path in sorted(candidates, key=lambda item: len(item.parts), reverse=True):
        _remove_managed_path(path)


def _purge_project_record(project_id: str) -> None:
    db = get_db()
    try:
        db.execute(
            "DELETE FROM transcription_segments WHERE project_id=?", (project_id,)
        )
        db.execute("DELETE FROM transcription_runs WHERE project_id=?", (project_id,))
        db.execute("DELETE FROM segments WHERE project_id=?", (project_id,))
        db.execute("DELETE FROM ai_batch_results WHERE project_id=?", (project_id,))
        db.execute("DELETE FROM tasks WHERE project_id=?", (project_id,))
        db.execute("DELETE FROM projects WHERE id=?", (project_id,))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _audio_preflight(audio_path: str | None) -> dict:
    if not audio_path or not os.path.isfile(audio_path):
        return {"ok": False, "error_code": "AUDIO_MISSING", "message": "音频尚未提取"}
    try:
        with wave.open(audio_path, "rb") as source:
            duration = source.getnframes() / max(source.getframerate(), 1)
            valid_format = (
                source.getnchannels() == 1
                and source.getsampwidth() == 2
                and source.getframerate() == 16000
                and source.getcomptype() == "NONE"
            )
    except (OSError, EOFError, wave.Error) as exc:
        return {"ok": False, "error_code": "AUDIO_INVALID", "message": str(exc)}
    if duration <= 0.1:
        return {"ok": False, "error_code": "AUDIO_EMPTY", "message": "音频内容为空"}
    if not valid_format:
        return {
            "ok": False,
            "error_code": "AUDIO_FORMAT",
            "message": "音频需要重新提取为 16kHz 单声道 WAV",
        }
    free_bytes = shutil.disk_usage(Path(audio_path).parent).free
    if free_bytes < 512 * 1024 * 1024:
        return {
            "ok": False,
            "error_code": "DISK_FULL",
            "message": "可用磁盘空间不足 512 MiB",
        }
    return {"ok": True, "duration": round(duration, 2), "free_bytes": free_bytes}
