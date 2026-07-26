"""Transactional subtitle editor API."""

from fastapi import APIRouter, HTTPException

from ..models.schemas import (
    EditorHistoryRequest,
    EditorOperationResponse,
    SegmentDraftUpdate,
    SegmentOperationRequest,
)
from ..services.editor import (
    EditorServiceError,
    discard_draft,
    execute_operation,
    get_draft,
    history_step,
    save_draft,
)


router = APIRouter(prefix="/api/projects", tags=["editor"])


def _run(callable_, *args):
    try:
        return callable_(*args)
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
    draft = _run(get_draft, project_id)
    if not draft:
        raise HTTPException(409, detail={
            "code": "DRAFT_EMPTY", "message": "没有待保存的字幕草稿",
            "suggestion": "", "details": {}, "recoverable": True,
        })
    request = SegmentOperationRequest(
        expected_revision=draft["base_revision"],
        operation="update_many",
        items=draft["items"],
    )
    result = _run(execute_operation, project_id, request)
    discard_draft(project_id)
    return result


@router.delete("/{project_id}/draft")
def delete_segment_draft(project_id: str):
    _run(discard_draft, project_id)
    return {"message": "字幕草稿已放弃"}
