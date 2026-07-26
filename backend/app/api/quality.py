"""Subtitle quality issue workflow."""

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..services.quality import list_issues, scan, set_issue_status
from ..services.ai_quality import generate_quality_preview
from ..models.database import get_db
from ..models.schemas import SegmentOperationItem, SegmentOperationRequest
from ..services.editor import EditorServiceError, execute_operation


router = APIRouter(prefix="/api")


class QualityScanRequest(BaseModel):
    rules: dict[str, Any] = Field(default_factory=dict)


class IssueStatusRequest(BaseModel):
    status: str = Field(pattern="^(open|ignored|resolved)$")


class QualityFixRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    apply: bool = False


def _require_quality_cloud_authorization():
    db = get_db()
    try: row = db.execute("SELECT granted FROM cloud_authorizations WHERE capability='quality'").fetchone()
    finally: db.close()
    if not row or not row["granted"]:
        raise HTTPException(403, detail={"code": "CLOUD_AUTHORIZATION_REQUIRED", "message": "请先授权 AI 质检云端增强", "suggestion": "在智能工具中查看上传说明并授权"})


@router.post("/projects/{project_id}/quality/scan")
def scan_quality(project_id: str, request: QualityScanRequest):
    try:
        issues = scan(project_id, request.rules)
    except FileNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    return {"issues": issues, "total": len(issues)}


@router.get("/projects/{project_id}/quality/issues")
def quality_issues(project_id: str, status: str = Query("open", pattern="^(open|ignored|resolved|all)$")):
    issues = list_issues(project_id, status)
    return {"issues": issues, "total": len(issues)}


@router.patch("/projects/{project_id}/quality/issues/{issue_id}")
def update_quality_issue(project_id: str, issue_id: str, request: IssueStatusRequest):
    if not set_issue_status(project_id, issue_id, request.status):
        raise HTTPException(404, "质检问题不存在")
    return {"id": issue_id, "status": request.status}


def _fix_operation(project_id: str, issue_id: str, expected_revision: int):
    import json
    import re
    import textwrap

    db = get_db()
    try:
        issue = db.execute("""SELECT q.*,s.idx,s.clean_text,s.raw_text,s.translated_text,s.start,s.end
                              FROM quality_issues q JOIN segments s ON s.id=q.segment_id
                              WHERE q.id=? AND q.project_id=?""", (issue_id, project_id)).fetchone()
        if not issue: raise HTTPException(404, "质检问题不存在")
        details = json.loads(issue["details_json"] or "{}")
        if issue["rule_id"] == "line_length":
            source = issue["clean_text"] or issue["raw_text"] or ""
            replacement = "\n".join(textwrap.wrap(source, width=42, break_long_words=False, break_on_hyphens=False))
            return SegmentOperationRequest(expected_revision=expected_revision, operation="update_many", items=[SegmentOperationItem(index=issue["idx"], clean_text=replacement)]), {"before": source, "after": replacement}
        if issue["rule_id"] == "number_mismatch":
            source_numbers = details.get("source", []); target = issue["translated_text"] or ""; cursor = iter(source_numbers)
            replacement = re.sub(r"\d+(?:[.,]\d+)?", lambda match: str(next(cursor, match.group(0))), target)
            return SegmentOperationRequest(expected_revision=expected_revision, operation="update_many", items=[SegmentOperationItem(index=issue["idx"], translated_text=replacement)]), {"before": target, "after": replacement}
        if issue["rule_id"] == "duplicate" and issue["idx"] > 1:
            return SegmentOperationRequest(expected_revision=expected_revision, operation="merge", indices=[issue["idx"] - 1, issue["idx"]]), {"before": "两条重复字幕", "after": "合并为一条"}
        raise HTTPException(409, detail={"code": "NO_DETERMINISTIC_FIX", "message": "该问题需要人工确认或 AI 预览修复"})
    finally: db.close()


@router.post("/projects/{project_id}/quality/issues/{issue_id}/fix")
def fix_quality_issue(project_id: str, issue_id: str, request: QualityFixRequest):
    operation, preview = _fix_operation(project_id, issue_id, request.expected_revision)
    if not request.apply:
        return {"preview": preview, "operation": operation.model_dump(), "applied": False}
    try: result = execute_operation(project_id, operation)
    except EditorServiceError as error: raise HTTPException(error.status_code, detail=error.as_detail()) from error
    set_issue_status(project_id, issue_id, "resolved")
    return {"preview": preview, "applied": True, "editor": result}


@router.post("/projects/{project_id}/quality/issues/{issue_id}/ai-fix")
def ai_fix_quality_issue(project_id: str, issue_id: str, request: QualityFixRequest):
    _require_quality_cloud_authorization()
    db = get_db()
    try:
        row = db.execute(
            """SELECT q.*,s.idx,s.clean_text,s.raw_text,s.translated_text,s.locked
               FROM quality_issues q JOIN segments s ON s.id=q.segment_id
               WHERE q.id=? AND q.project_id=?""", (issue_id, project_id),
        ).fetchone()
        if not row: raise HTTPException(404, "质检问题不存在")
        if row["locked"]: raise HTTPException(409, "锁定字幕不能使用 AI 修复")
        details = json.loads(row["details_json"] or "{}")
        if not request.apply:
            preview = generate_quality_preview(dict(row))
            details["ai_preview"] = preview
            details["ai_preview_revision"] = request.expected_revision
            db.execute("UPDATE quality_issues SET details_json=?,updated_at=datetime('now','localtime') WHERE id=?", (json.dumps(details, ensure_ascii=False), issue_id))
            db.commit()
            return {"preview": preview, "applied": False}
        preview = details.get("ai_preview")
        if not preview or details.get("ai_preview_revision") != request.expected_revision:
            raise HTTPException(409, detail={"code": "AI_PREVIEW_REQUIRED", "message": "请先生成当前版本的 AI 修复预览"})
        item = SegmentOperationItem(index=row["idx"], clean_text=preview["after"]["clean_text"], translated_text=preview["after"]["translated_text"])
    finally:
        db.close()
    try: result = execute_operation(project_id, SegmentOperationRequest(expected_revision=request.expected_revision, operation="update_many", items=[item]))
    except EditorServiceError as error: raise HTTPException(error.status_code, detail=error.as_detail()) from error
    set_issue_status(project_id, issue_id, "resolved")
    return {"preview": preview, "applied": True, "editor": result}
