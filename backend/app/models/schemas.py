"""
字幕工厂 - Pydantic 数据模型
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Any, Literal, Optional, List


# ── Project ─────────────────────────────────────────────

class ProjectCreate(BaseModel):
    source_type: str = Field(default="youtube", pattern="^(youtube|local)$")
    source_url: Optional[str] = None
    title: Optional[str] = None
    language: str = "auto"
    target_language: str = "zh"


class ProjectResponse(BaseModel):
    id: str
    title: str
    source_type: str
    source_url: Optional[str] = None
    video_path: Optional[str] = None
    video_url: Optional[str] = None
    audio_path: Optional[str] = None
    thumbnail_url: Optional[str] = None
    thumbnail_access_url: Optional[str] = None
    group_name: Optional[str] = None
    language: str
    target_language: str
    created_at: str
    updated_at: str
    deleted_at: Optional[str] = None
    segments_count: int = 0
    edit_revision: int = 0
    media_status: str = "ready"


class ProjectUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=160)
    target_language: Optional[str] = None

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value


class ProjectGroupUpdate(BaseModel):
    group_name: Optional[str]

    @field_validator("group_name", mode="before")
    @classmethod
    def normalize_group_name(cls, value):
        """分组名保存前统一规范化；空值表示未分组。"""
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if len(normalized) > 40:
            raise ValueError("分组名最多 40 个字符")
        return normalized or None


# ── Segment ─────────────────────────────────────────────

class SegmentResponse(BaseModel):
    id: str
    project_id: str
    index: int
    start: float
    end: float
    raw_text: str
    clean_text: str
    translated_text: str
    speaker: str
    speaker_id: Optional[str] = None
    locked: bool


class SegmentUpdate(BaseModel):
    start: Optional[float] = Field(default=None, ge=0)
    end: Optional[float] = Field(default=None, gt=0)
    clean_text: Optional[str] = None
    translated_text: Optional[str] = None
    speaker_id: Optional[str] = None
    locked: Optional[bool] = None


class SegmentOperationItem(BaseModel):
    index: int = Field(ge=1)
    start: Optional[float] = Field(default=None, ge=0)
    end: Optional[float] = Field(default=None, gt=0)
    clean_text: Optional[str] = None
    translated_text: Optional[str] = None
    speaker_id: Optional[str] = None
    locked: Optional[bool] = None


class SegmentOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    operation: Literal[
        "update_many", "replace", "shift", "split", "merge", "assign_speaker"
    ]
    items: List[SegmentOperationItem] = Field(default_factory=list)
    indices: List[int] = Field(default_factory=list)
    include_locked: bool = False
    search: Optional[str] = None
    replacement: str = ""
    fields: List[Literal["clean_text", "translated_text"]] = Field(
        default_factory=lambda: ["clean_text", "translated_text"]
    )
    match_case: bool = True
    delta: float = 0
    split_index: Optional[int] = Field(default=None, ge=1)
    split_at: Optional[float] = Field(default=None, ge=0)
    text_offset: Optional[int] = Field(default=None, ge=0)
    speaker_id: Optional[str] = None


class SegmentDraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_revision: int = Field(ge=0)
    items: List[SegmentOperationItem]


class EditorHistoryRequest(BaseModel):
    expected_revision: int = Field(ge=0)


class EditorOperationResponse(BaseModel):
    revision: int
    operation_id: Optional[str] = None
    operation: str
    affected_count: int
    segments: List[dict[str, Any]]


# ── Task ────────────────────────────────────────────────

class TaskResponse(BaseModel):
    id: str
    project_id: Optional[str] = None
    type: str
    status: str  # pending | running | success | failed | cancelled
    step: str
    progress: float
    message: str
    error: Optional[str] = None
    created_at: str
    updated_at: str


# ── Export ──────────────────────────────────────────────

class ExportRequest(BaseModel):
    format: str = Field(default="srt", pattern="^(srt|vtt|ass|srt-bilingual|mp4|mkv)$")
    bilingual: bool = False
    primary_language: str = "original"  # original | translated
    style: Optional[dict[str, Any]] = None


# ── Processing ──────────────────────────────────────────

class ProcessingConfig(BaseModel):
    model: Literal[
        "tiny", "base", "small", "medium", "large-v3",
        "parakeet-tdt-0.6b-v3-coreml",
        "parakeet-tdt-0.6b-v3-int8",
    ] = "small"
    language: str = "auto"             # auto | en | zh | ja
    target_language: str = "zh"        # zh | en | ja | none
    enable_clean: bool = True
    enable_translate: bool = True
    bilingual: bool = False


class MediaSelectionUpdate(BaseModel):
    audio_track_index: int = Field(default=0, ge=0)
    range_start: Optional[float] = Field(default=None, ge=0)
    range_end: Optional[float] = Field(default=None, gt=0)

    @field_validator("range_end")
    @classmethod
    def validate_range(cls, value, info):
        start = info.data.get("range_start")
        if value is not None and start is not None and value <= start:
            raise ValueError("出点必须晚于入点")
        return value


class WorkflowRequest(BaseModel):
    model: str = "auto"
    language: str = "auto"
    runtime: Optional[str] = None
    source_url: Optional[str] = None
    stop_after: Literal["transcribe"] = "transcribe"


class TranscriptionRetryRequest(BaseModel):
    model: str = "small"
    language: str = "auto"
    runtime: Optional[str] = None


class ModelPrepareRequest(BaseModel):
    repair: bool = False


# ── AI settings ────────────────────────────────────────

class AISettingsUpdate(BaseModel):
    provider: str = "deepseek"
    base_url: str
    api_key: Optional[str] = None
    model: str


class AIConnectionTest(BaseModel):
    provider: str = "deepseek"
    base_url: str
    api_key: Optional[str] = None
    model: str


class AIProviderUpdate(BaseModel):
    base_url: str
    api_key: Optional[str] = None
    model: str
    enabled: bool = True


class AIAssignmentsUpdate(BaseModel):
    clean_provider_id: str
    translate_provider_id: str


class ModelScanRequest(BaseModel):
    root_path: str


class ModelImportRequest(BaseModel):
    path: str
    cli_path: Optional[str] = None
    display_name: Optional[str] = None


# ── App settings ───────────────────────────────────────

class AppSettingsUpdate(BaseModel):
    """Persisted runtime settings; interface-only theme state stays in the Web UI."""

    model_config = ConfigDict(extra="forbid")

    default_workflow: Optional[Literal["automatic", "manual"]] = None
    auto_save: Optional[bool] = None
    startup_behavior: Optional[Literal["restore_last", "project_library"]] = None
    default_model: Optional[str] = None
    source_language: Optional[str] = None
    custom_model_path: Optional[str] = None
    coreml_model_path: Optional[str] = None
    coreml_cli_path: Optional[str] = None
    translation_target_language: Optional[str] = None
    bilingual_order: Optional[Literal["original_first", "translated_first"]] = None
    favorite_languages: Optional[List[str]] = None
    download_quality: Optional[str] = None
    download_container: Optional[Literal["mp4", "mkv", "webm"]] = None
    ffmpeg_path: Optional[str] = None
    yt_dlp_path: Optional[str] = None
    download_directory: Optional[str] = None
    clean_provider_id: Optional[str] = None
    translate_provider_id: Optional[str] = None
    transcription_runtime_by_model: Optional[dict[str, str]] = None

    @field_validator(
        "default_model", "source_language", "translation_target_language",
        "download_quality", mode="before",
    )
    @classmethod
    def normalize_required_string(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator(
        "custom_model_path", "coreml_model_path", "coreml_cli_path",
        "ffmpeg_path", "yt_dlp_path", "download_directory", mode="before",
    )
    @classmethod
    def normalize_optional_path(cls, value):
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("favorite_languages")
    @classmethod
    def normalize_favorite_languages(cls, value):
        if value is None:
            return value
        result = []
        for item in value:
            language = item.strip() if isinstance(item, str) else ""
            if language and language not in result:
                result.append(language)
        return result[:20]


class PathValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "ffmpeg", "yt_dlp", "model", "coreml_model", "cli", "download_directory"
    ]
    path: str

    @field_validator("path", mode="before")
    @classmethod
    def normalize_path(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value
