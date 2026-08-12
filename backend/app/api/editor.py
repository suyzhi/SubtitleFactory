"""Transactional subtitle editor API."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..models.schemas import (
    EditorHistoryRequest,
    EditorOperationResponse,
    SegmentDraftUpdate,
    SegmentOperationRequest,
)
from ..services.editor import (
    EditorServiceError,
    commit_draft,
    discard_draft,
    execute_operation,
    get_draft,
    history_step,
    save_draft,
)


router = APIRouter(prefix="/api/projects", tags=["editor"])


class DraftRebaseRequest(BaseModel):
    confirm: bool = False


def _run(callable_, *args, **kwargs):
    try:
        return callable_(*args, **kwargs)
    except EditorServiceError as error:
        raise HTTPException(error.status_code, detail=error.as_detail()) from error


@router.post("/{project_id}/segment-operations", response_model=EditorOperationResponse)
def apply_segment_operation(project_id: str, request: SegmentOperationRequest):
    return _run(execute_operation, project_id, request)


@router.post("/{project_id}/editor/undo", response_model=EditorOperationResponse)
def undo_editor_operation(project_id: str, request: EditorHistoryRequest):
    return _run(history_step, project_id, request.expected_revision, "undo")


@router.post("/{project_id}/editor/redo", response_model=EditorOperationResponse)
def redo_editor_operation(project_id: str, request: EditorHistoryRequest):
    return _run(history_step, project_id, request.expected_revision, "redo")


@router.get("/{project_id}/draft")
def read_segment_draft(project_id: str):
    return {"draft": _run(get_draft, project_id)}


@router.put("/{project_id}/draft")
def write_segment_draft(project_id: str, request: SegmentDraftUpdate):
    items = [item.model_dump(exclude_unset=True) for item in request.items]
    return {"draft": _run(save_draft, project_id, request.base_revision, items)}


@router.post("/{project_id}/draft/commit", response_model=EditorOperationResponse)
def commit_segment_draft(project_id: str):
    return _run(commit_draft, project_id)


@router.post("/{project_id}/draft/rebase", response_model=EditorOperationResponse)
def rebase_segment_draft(project_id: str, request: DraftRebaseRequest):
    if not request.confirm:
        raise HTTPException(400, detail={
            "code": "CONFIRMATION_REQUIRED",
            "message": "将旧草稿应用到当前字幕需要显式确认",
            "suggestion": "请先检查草稿行号和当前字幕内容",
            "details": {},
            "recoverable": True,
        })
    return _run(commit_draft, project_id, rebase=True)


@router.delete("/{project_id}/draft")
def delete_segment_draft(project_id: str):
    _run(discard_draft, project_id)
    return {"message": "字幕草稿已放弃"}
