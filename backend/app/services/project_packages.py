"""Versioned, checksummed .sfproject interchange packages."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from ..models.database import get_db
from ..utils.config import EXPORTS_DIR, PROJECTS_DIR


PACKAGE_VERSION = 1
MAX_ENTRY_SIZE = 50 * 1024**3
MAX_TOTAL_SIZE = 100 * 1024**3
REQUIRED_DATA = {
    "data/project.json", "data/segments.json", "data/speakers.json",
    "data/history.json", "data/glossaries.json", "data/glossary_terms.json",
}
MEDIA_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".wav", ".m4a", ".jpg", ".jpeg", ".png", ".webp"}


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def export_project_package(project_id: str, include_media: bool = False) -> Path:
    db = get_db()
    try:
        project_row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not project_row:
            raise FileNotFoundError("项目不存在")
        project = dict(project_row)
        segments = [dict(row) for row in db.execute("SELECT * FROM segments WHERE project_id=? ORDER BY idx", (project_id,))]
        speakers = [dict(row) for row in db.execute("SELECT * FROM speakers WHERE project_id=?", (project_id,))]
        history = [dict(row) for row in db.execute("SELECT * FROM edit_operations WHERE project_id=? ORDER BY result_revision", (project_id,))]
        glossaries = [dict(row) for row in db.execute("SELECT * FROM glossaries WHERE project_id=?", (project_id,))]
        glossary_ids = [row["id"] for row in glossaries]
        terms = []
        for glossary_id in glossary_ids:
            terms.extend(dict(row) for row in db.execute("SELECT * FROM glossary_terms WHERE glossary_id=?", (glossary_id,)))
        style_row = db.execute("SELECT settings_json FROM project_styles WHERE project_id=?", (project_id,)).fetchone()
        style = json.loads(style_row["settings_json"]) if style_row else None
    finally:
        db.close()

    for key in ("video_path", "audio_path", "thumbnail_path"):
        project[key] = None
    files = {
        "data/project.json": _json_bytes(project), "data/segments.json": _json_bytes(segments),
        "data/speakers.json": _json_bytes(speakers), "data/history.json": _json_bytes(history),
        "data/glossaries.json": _json_bytes(glossaries), "data/glossary_terms.json": _json_bytes(terms),
        "data/style.json": _json_bytes(style),
    }
    media_entries: list[tuple[str, Path]] = []
    if include_media:
        for kind, value in (("video", project_row["video_path"]), ("audio", project_row["audio_path"]), ("thumbnail", project_row["thumbnail_path"])):
            if value and Path(value).is_file():
                media_entries.append((f"media/{kind}{Path(value).suffix}", Path(value)))
    checksums = {name: _digest(content) for name, content in files.items()}
    for name, path in media_entries:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        checksums[name] = digest.hexdigest()
    manifest = {
        "format": "subtitle-factory-project", "version": PACKAGE_VERSION,
        "project_id": project_id, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "include_media": include_media, "checksums": checksums,
    }
    output = Path(EXPORTS_DIR) / f"{project_id}-{int(time.time())}.sfproject"
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        archive.writestr("manifest.json", _json_bytes(manifest))
        for name, content in files.items(): archive.writestr(name, content)
        for name, path in media_entries: archive.write(path, name)
    return output


def _validate_archive(archive: zipfile.ZipFile) -> dict:
    total = 0
    names = set()
    for info in archive.infolist():
        path = PurePosixPath(info.filename)
        if path.is_absolute() or ".." in path.parts or info.is_dir() and info.file_size:
            raise ValueError("项目包包含不安全路径")
        if info.file_size > MAX_ENTRY_SIZE:
            raise ValueError("项目包中的单个文件过大")
        if info.filename in names:
            raise ValueError("项目包包含重复路径")
        names.add(info.filename)
        total += info.file_size
        if total > MAX_TOTAL_SIZE:
            raise ValueError("项目包解压后容量过大")
    try:
        manifest = json.loads(archive.read("manifest.json"))
    except (KeyError, json.JSONDecodeError) as error:
        raise ValueError("项目包 manifest 无效") from error
    if manifest.get("format") != "subtitle-factory-project" or manifest.get("version") != PACKAGE_VERSION:
        raise ValueError("项目包版本不受支持")
    if not REQUIRED_DATA.issubset(names):
        raise ValueError("项目包缺少必要数据")
    expected_files = {name for name in names if name != "manifest.json" and not name.endswith("/")}
    checksums = manifest.get("checksums") or {}
    if set(checksums) != expected_files:
        raise ValueError("项目包校验清单不完整")
    for name, expected in checksums.items():
        digest = hashlib.sha256()
        with archive.open(name) as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise ValueError(f"项目包校验失败：{name}")
    return manifest


def import_project_package(path: str) -> dict:
    with zipfile.ZipFile(path) as archive:
        manifest = _validate_archive(archive)
        project = json.loads(archive.read("data/project.json"))
        segments = json.loads(archive.read("data/segments.json"))
        speakers = json.loads(archive.read("data/speakers.json"))
        history = json.loads(archive.read("data/history.json"))
        glossaries = json.loads(archive.read("data/glossaries.json"))
        terms = json.loads(archive.read("data/glossary_terms.json"))
        style = json.loads(archive.read("data/style.json")) if "data/style.json" in archive.namelist() else None
        old_id = project["id"]
        if len(segments) > 1_000_000:
            raise ValueError("项目包字幕数量异常")
        previous_end = -1.0
        for item in sorted(segments, key=lambda value: int(value.get("idx", 0))):
            start, end = float(item["start"]), float(item["end"])
            if start < 0 or end <= start or start < previous_end - 1e-6:
                raise ValueError("项目包包含非法或重叠时间码")
            previous_end = end
        db = get_db()
        try:
            exists = db.execute("SELECT 1 FROM projects WHERE id=?", (old_id,)).fetchone()
        finally:
            db.close()
        project_id = str(uuid.uuid4()) if exists else old_id
        destination = Path(PROJECTS_DIR) / project_id / "media"
        media_paths = {}
        for info in archive.infolist():
            if not info.filename.startswith("media/") or info.is_dir(): continue
            if Path(info.filename).suffix.lower() not in MEDIA_EXTENSIONS:
                raise ValueError("项目包包含不支持的媒体类型")
            destination.mkdir(parents=True, exist_ok=True)
            target = destination / Path(info.filename).name
            with archive.open(info) as source, target.open("wb") as output: shutil.copyfileobj(source, output)
            media_paths[Path(info.filename).stem] = str(target)

    now = time.strftime("%Y-%m-%d %H:%M:%S")
    project.update({
        "id": project_id, "video_path": media_paths.get("video"), "audio_path": media_paths.get("audio"),
        "thumbnail_path": media_paths.get("thumbnail"), "thumbnail_url": None,
        "media_status": "ready" if media_paths.get("video") or project.get("media_mode") == "web" else "relink_required",
        "media_mode": project.get("media_mode") if project.get("media_mode") in {"local", "web"} else "local",
        "updated_at": now,
    })
    project_columns = [
        "id", "title", "source_type", "source_url", "video_path", "audio_path", "thumbnail_url", "thumbnail_path",
        "group_name", "language", "target_language", "created_at", "updated_at", "deleted_at", "edit_revision", "media_status",
        "audio_track_index", "range_start", "range_end", "media_mode",
    ]
    glossary_map = {item["id"]: str(uuid.uuid4()) for item in glossaries}
    speaker_map = {item["id"]: str(uuid.uuid4()) for item in speakers}
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            f"INSERT INTO projects ({','.join(project_columns)}) VALUES ({','.join('?' for _ in project_columns)})",
            [project.get(column) for column in project_columns],
        )
        segment_columns = ["id","project_id","idx","start","end","raw_text","clean_text","translated_text","speaker","speaker_id","locked","is_draft","source_stage","transcription_run_id"]
        for item in segments:
            item["project_id"] = project_id; item["id"] = str(uuid.uuid4())
            if item.get("speaker_id"):
                item["speaker_id"] = speaker_map.get(item["speaker_id"])
            db.execute(f"INSERT INTO segments ({','.join(segment_columns)}) VALUES ({','.join('?' for _ in segment_columns)})", [item.get(key) for key in segment_columns])
        for item in speakers:
            item.update({"id": speaker_map[item["id"]], "project_id": project_id})
            db.execute("INSERT INTO speakers(id,project_id,name,color,external_key,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", [item.get(key) for key in ("id","project_id","name","color","external_key","created_at","updated_at")])
        for item in history:
            item.update({"id": str(uuid.uuid4()), "project_id": project_id})
            for field in ("before_json", "after_json"):
                item[field] = item[field].replace(old_id, project_id)
            db.execute("""INSERT INTO edit_operations(id,project_id,operation,before_json,after_json,base_revision,result_revision,undone,created_at)
                          VALUES (?,?,?,?,?,?,?,?,?)""", [item.get(key) for key in ("id","project_id","operation","before_json","after_json","base_revision","result_revision","undone","created_at")])
        for item in glossaries:
            old_glossary = item["id"]; item.update({"id": glossary_map[old_glossary], "project_id": project_id})
            db.execute("INSERT INTO glossaries(id,project_id,name,source_language,target_language,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", [item.get(key) for key in ("id","project_id","name","source_language","target_language","created_at","updated_at")])
        for item in terms:
            item.update({"id": str(uuid.uuid4()), "glossary_id": glossary_map[item["glossary_id"]]})
            columns=("id","glossary_id","source_text","target_text","case_sensitive","whole_word","do_not_translate","note","created_at","updated_at")
            db.execute(f"INSERT INTO glossary_terms({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", [item.get(key) for key in columns])
        if style is not None:
            db.execute(
                "INSERT INTO project_styles(project_id,settings_json,updated_at) VALUES (?,?,?)",
                (project_id, json.dumps(style, ensure_ascii=False), now),
            )
        db.commit()
    except Exception:
        db.rollback(); raise
    finally:
        db.close()
    return {"project_id": project_id, "source_project_id": old_id, "media_status": project["media_status"], "manifest": manifest}
