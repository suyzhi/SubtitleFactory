"""
字幕工厂（简洁版）- 项目 API 路由包入口

按职责拆分为：
- common: 共享路径、数据库与依赖访问辅助
- models: 本地转写模型目录、运行设备选择与准备
- crud: 项目基础 CRUD
- media: 本地视频导入、下载与音频提取
- workflow: 一键字幕工作流、AI 整理与 AI 翻译
- subtitles: 字幕段读取与 SRT 导出/下载
"""

from fastapi import APIRouter

from ...models.database import get_db
from ...services.app_settings import get_effective_app_settings as get_app_settings
from ...services.transcriber import get_transcription_model_status, transcribe_audio
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
    VIDEO_EXTENSIONS,
    _active_task_conflict,
    _audio_preflight,
    _path_is_managed,
    _project_row,
    _purge_project_files,
    _purge_project_record,
    _raise_active_task_conflict,
    _remove_managed_path,
    download_video,
    logger,
    normalize_youtube_url,
    remove_managed_download_file,
)
from .crud import (
    create_project,
    delete_project,
    get_project,
    list_projects,
)
from .media import (
    _do_download,
    _do_extract_audio,
    import_local_video,
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
    prepare_transcription_model,
    transcription_models,
)
from .subtitles import (
    download_export,
    export_subtitles,
    get_segments,
)
from .workflow import (
    _do_clean,
    _do_transcribe,
    _do_translate,
    _do_workflow,
    start_clean,
    start_translate,
    start_workflow,
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
    "PROJECTS_DIR",
    "RUNTIME_LABELS",
    "SUBTITLES_DIR",
    "VIDEO_EXTENSIONS",
    "_active_task_conflict",
    "_audio_preflight",
    "_do_clean",
    "_do_download",
    "_do_extract_audio",
    "_do_prepare_transcription_model",
    "_do_transcribe",
    "_do_translate",
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
    "create_project",
    "delete_project",
    "download_export",
    "download_video",
    "export_subtitles",
    "get_app_settings",
    "get_db",
    "get_project",
    "get_segments",
    "get_transcription_model_status",
    "import_local_video",
    "list_projects",
    "logger",
    "normalize_youtube_url",
    "prepare_transcription_model",
    "remove_managed_download_file",
    "router",
    "start_clean",
    "start_translate",
    "start_workflow",
    "task_manager",
    "transcribe_audio",
    "transcription_models",
]
