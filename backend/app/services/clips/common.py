"""Shared constants, errors, and source loading for short-clip recommendations and rendering."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from typing import Any

from ...models.database import get_db

ASPECT_DIMENSIONS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}


class ClipError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str = "CLIP_OPERATION_FAILED",
        recoverable: bool = True,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.recoverable = recoverable
        self.available_actions = ["retry"] if recoverable else []
        self.details = details or {}


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _source(project_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    db = get_db()
    try:
        project = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        rows = [
            dict(row)
            for row in db.execute(
                """SELECT * FROM segments
                    WHERE project_id=? AND COALESCE(is_draft,0)=0 ORDER BY idx""",
                (project_id,),
            ).fetchall()
        ]
    finally:
        db.close()
    if not project:
        raise FileNotFoundError("项目不存在")
    if not rows:
        raise ClipError("项目还没有可用字幕", error_code="CLIP_SOURCE_EMPTY")
    return dict(project), rows


def _fingerprint(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(
            [
                [row["idx"], row["start"], row["end"], row.get("clean_text"), row.get("raw_text")]
                for row in rows
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def facade_attr(name: str, default: Any) -> Any:
    pkg = sys.modules.get(__package__ or "")
    return getattr(pkg, name, default) if pkg is not None else default
