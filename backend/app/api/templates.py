"""Reusable subtitle style templates."""

import json
import time
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..models.database import get_db


router = APIRouter(prefix="/api")

BUILTINS = (
    ("builtin-clean", "清爽底部", {"fontFamily": "PingFang SC", "fontSize": 22, "originalFontSize": 22, "translatedFontSize": 20, "verticalPosition": 88, "backgroundMode": "black", "shadow": True, "maxWidth": 85, "lineGap": 4, "mode": "bilingual_original_first", "safeArea": 8, "exportFormat": "srt", "videoPreset": "h264-fast"}),
    ("builtin-bilingual", "双语访谈", {"fontFamily": "PingFang SC", "fontSize": 22, "originalFontSize": 22, "translatedFontSize": 19, "verticalPosition": 84, "backgroundMode": "black", "shadow": True, "maxWidth": 82, "lineGap": 6, "mode": "bilingual_original_first", "safeArea": 10, "exportFormat": "ass", "videoPreset": "h264-quality"}),
)


class TemplateInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    settings: dict


class ProjectStyleInput(BaseModel):
    settings: dict


def _seed():
    db = get_db(); now = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        for identifier, name, settings in BUILTINS:
            db.execute(
                """INSERT INTO style_templates(id,name,builtin,settings_json,created_at,updated_at) VALUES (?,?,1,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name,settings_json=excluded.settings_json,updated_at=excluded.updated_at""",
                (identifier, name, json.dumps(settings, ensure_ascii=False), now, now),
            )
        db.commit()
    finally: db.close()


@router.get("/style-templates")
def list_templates():
    _seed(); db = get_db()
    try:
        rows = db.execute("SELECT * FROM style_templates ORDER BY builtin DESC,name").fetchall()
        return {"templates": [{**dict(row), "builtin": bool(row["builtin"]), "settings": json.loads(row["settings_json"])} for row in rows]}
    finally: db.close()


@router.post("/style-templates", status_code=201)
def create_template(request: TemplateInput):
    identifier, now = str(uuid.uuid4()), time.strftime("%Y-%m-%d %H:%M:%S")
    db = get_db()
    try:
        db.execute("INSERT INTO style_templates VALUES (?,?,0,?,?,?)", (identifier, request.name.strip(), json.dumps(request.settings, ensure_ascii=False), now, now)); db.commit()
        return {"id": identifier, "name": request.name, "builtin": False, "settings": request.settings}
    finally: db.close()


@router.put("/style-templates/{template_id}")
def update_template(template_id: str, request: TemplateInput):
    db = get_db()
    try:
        row = db.execute("SELECT builtin FROM style_templates WHERE id=?", (template_id,)).fetchone()
        if not row: raise HTTPException(404, "模板不存在")
        if row["builtin"]: raise HTTPException(409, "系统模板只读，请先复制")
        db.execute("UPDATE style_templates SET name=?,settings_json=?,updated_at=datetime('now','localtime') WHERE id=?", (request.name.strip(), json.dumps(request.settings, ensure_ascii=False), template_id)); db.commit()
        return {"id": template_id, "name": request.name, "settings": request.settings}
    finally: db.close()


@router.delete("/style-templates/{template_id}")
def delete_template(template_id: str):
    db = get_db()
    try:
        cursor = db.execute("DELETE FROM style_templates WHERE id=? AND builtin=0", (template_id,)); db.commit()
        if not cursor.rowcount: raise HTTPException(409, "系统模板不可删除或模板不存在")
        return {"deleted": True}
    finally: db.close()


@router.get("/projects/{project_id}/style")
def get_project_style(project_id: str):
    db = get_db()
    try:
        if not db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
            raise HTTPException(404, "项目不存在")
        row = db.execute("SELECT settings_json,updated_at FROM project_styles WHERE project_id=?", (project_id,)).fetchone()
        return {
            "settings": json.loads(row["settings_json"]) if row else None,
            "updated_at": row["updated_at"] if row else None,
        }
    finally:
        db.close()


@router.put("/projects/{project_id}/style")
def save_project_style(project_id: str, request: ProjectStyleInput):
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    db = get_db()
    try:
        if not db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
            raise HTTPException(404, "项目不存在")
        db.execute(
            """INSERT INTO project_styles(project_id,settings_json,updated_at) VALUES (?,?,?)
               ON CONFLICT(project_id) DO UPDATE SET settings_json=excluded.settings_json,updated_at=excluded.updated_at""",
            (project_id, json.dumps(request.settings, ensure_ascii=False), now),
        )
        db.commit()
        return {"settings": request.settings, "updated_at": now}
    finally:
        db.close()
