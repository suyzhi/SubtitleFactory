"""
字幕工厂（简洁版）- Pydantic 请求数据模型
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    source_type: str = Field(default="youtube", pattern="^(youtube|local)$")
    source_url: Optional[str] = None
    title: Optional[str] = None
    language: str = "auto"
    target_language: str = "zh"


class ExportRequest(BaseModel):
    format: str = Field(default="srt", pattern="^(srt|srt-bilingual)$")
    bilingual: bool = False
    primary_language: Literal["original", "translated"] = "original"


class WorkflowRequest(BaseModel):
    model: str = "auto"
    language: str = "auto"
    runtime: Optional[str] = None
    source_url: Optional[str] = None
    enable_clean: bool = False
    enable_translate: bool = False
    target_language: str = "zh"
    clean_target_length: int = Field(default=42, ge=16, le=100)
    text_processing_consent: bool = False
    resume_task_id: Optional[str] = None


class ModelPrepareRequest(BaseModel):
    runtime: Optional[str] = None
    repair: bool = False


class AIProviderUpdate(BaseModel):
    base_url: str
    api_key: Optional[str] = None
    model: str
    enabled: bool = True
    thinking_enabled: Optional[bool] = None


class AIAssignmentsUpdate(BaseModel):
    clean_provider_id: str
    translate_provider_id: str
