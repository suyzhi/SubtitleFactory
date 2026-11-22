"""
字幕工厂 - 项目 API 路由包入口

将原 2,500+ 行单文件路由按职责拆分为：
- common: 共享路径、数据库与依赖访问辅助
- models: 转写模型目录、运行设备选择与准备
- crud: 项目 CRUD、回收站、分组与媒体模式
- media: 下载、本地导入、音轨/音频提取与媒体文件流
- workflow: 转写、一键工作流、AI 整理与翻译
- subtitles: 字幕导入、转写候选、字幕段与导出/压制
"""

from fastapi import APIRouter

from ...models.database import get_db
from ...services.app_settings import get_effective_app_settings as get_app_settings
from ...services.cloud_asr import fun_asr_status
from ...services.transcriber import get_transcription_model_status, transcribe_audio
from ...services.video_thumbnail import generate_video_thumbnail
from ...utils.config import (
    AUDIO_DIR,
    DATA_DIR,
    DOWNLOADS_DIR,
    EXPORTS_DIR,
    PROJECTS_DIR,
    SUBTITLES_DIR,
)
from ...utils.task_manager import task_manager
from . import crud, media, models, subtitles, workflow
from .common import (
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    _active_task_conflict,
    _audio_preflight,
    _path_is_managed,
    _project_row,
    _purge_project_files,
    _purge_project_record,
    _raise_active_task_conflict,
    _remove_managed_path,
    download_audio_source,
    download_video,
    get_video_info,
    logger,
    normalize_youtube_url,
    remove_managed_download_file,
)
from .crud import (
    create_project,
    delete_project,
    empty_project_trash,
    get_project,
    list_projects,
    materialize_project_video,
    restore_project,
    trash_project,
    update_project,
    update_project_group,
    update_project_media_mode,
)
from .media import (
    _do_download,
    _do_extract_audio,
    _do_prepare_audio,
    get_media_info,
    get_thumbnail_file,
    get_video_file,
    import_local_video,
    playback_info,
    preview_media_track,
    start_download,
    start_extract_audio,
    start_prepare_audio,
    update_media_selection,
)
from .models import (
    RUNTIME_LABELS,
    _do_prepare_transcription_model,
    _qwen_presentation,
    _resolve_model,
    _runtime_available,
    _runtime_ids,
    _runtime_options,
    _select_runtime,
    _whisper_presentation,
    delete_imported_model,
    delete_transcription_model_files,
    import_local_model,
    imported_models,
    prepare_transcription_model,
    scan_local_models,
    transcription_models,
    validate_imported_model,
    validate_transcription_model,
)
from .subtitles import (
    _do_video_export,
    accept_transcription_candidate,
    download_export,
    export_subtitles,
    get_segments,
    import_subtitles_file,
    transcription_candidate,
    transcription_candidates,
    update_segment,
)
from .workflow import (
    _do_clean,
    _do_prepared_transcription,
    _do_transcribe,
    _do_translate,
    _do_workflow,
    retry_transcription,
    start_clean,
    start_transcribe,
    start_translate,
    start_workflow,
    undo_clean,
)

router = APIRouter(prefix="/api")
router.include_router(models.router)
router.include_router(crud.router)
router.include_router(media.router)
router.include_router(workflow.router)
router.include_router(subtitles.router)

__all__ = [
    "AUDIO_DIR",
    "DATA_DIR",
    "DOWNLOADS_DIR",
    "EXPORTS_DIR",
    "IMAGE_EXTENSIONS",
    "PROJECTS_DIR",
    "RUNTIME_LABELS",
    "SUBTITLES_DIR",
    "VIDEO_EXTENSIONS",
    "_active_task_conflict",
    "_audio_preflight",
    "_do_clean",
    "_do_download",
    "_do_extract_audio",
    "_do_prepare_audio",
    "_do_prepare_transcription_model",
    "_do_prepared_transcription",
    "_do_transcribe",
    "_do_translate",
    "_do_video_export",
    "_do_workflow",
    "_path_is_managed",
    "_project_row",
    "_purge_project_files",
    "_purge_project_record",
    "_qwen_presentation",
    "_raise_active_task_conflict",
    "_remove_managed_path",
    "_resolve_model",
    "_runtime_available",
    "_runtime_ids",
    "_runtime_options",
    "_select_runtime",
    "_whisper_presentation",
    "accept_transcription_candidate",
    "create_project",
    "delete_imported_model",
    "delete_project",
    "delete_transcription_model_files",
    "download_audio_source",
    "download_export",
    "download_video",
    "empty_project_trash",
    "export_subtitles",
    "fun_asr_status",
    "generate_video_thumbnail",
    "get_app_settings",
    "get_db",
    "get_media_info",
    "get_project",
    "get_segments",
    "get_thumbnail_file",
    "get_transcription_model_status",
    "get_video_file",
    "get_video_info",
    "import_local_model",
    "import_local_video",
    "import_subtitles_file",
    "imported_models",
    "list_projects",
    "logger",
    "materialize_project_video",
    "normalize_youtube_url",
    "playback_info",
    "prepare_transcription_model",
    "preview_media_track",
    "remove_managed_download_file",
    "restore_project",
    "retry_transcription",
    "router",
    "scan_local_models",
    "start_clean",
    "start_download",
    "start_extract_audio",
    "start_prepare_audio",
    "start_transcribe",
    "start_translate",
    "start_workflow",
    "task_manager",
    "transcribe_audio",
    "transcription_candidate",
    "transcription_candidates",
    "transcription_models",
    "trash_project",
    "undo_clean",
    "update_media_selection",
    "update_project",
    "update_project_group",
    "update_project_media_mode",
    "update_segment",
    "validate_imported_model",
    "validate_transcription_model",
]
