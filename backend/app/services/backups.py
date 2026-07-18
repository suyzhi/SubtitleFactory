"""Verified SQLite backups with daily/weekly retention."""

from __future__ import annotations

import hashlib
import sqlite3
import time
import uuid
from pathlib import Path

from ..models import database


def backup_directory() -> Path:
    path = Path(database.DB_PATH).parent / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup(kind: str = "manual") -> dict:
    if kind not in {"manual", "daily", "weekly", "pre_restore"}:
        raise ValueError("未知备份类型")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = backup_directory() / f"{kind}-{stamp}-{uuid.uuid4().hex[:6]}.db"
    source = database.get_db()
    destination = sqlite3.connect(path)
    try:
        source.backup(destination)
    finally:
        destination.close(); source.close()
    check = sqlite3.connect(path)
    try:
        if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            path.unlink(missing_ok=True)
            raise RuntimeError("备份完整性检查失败")
    finally:
        check.close()
    digest, now = _hash(path), time.strftime("%Y-%m-%d %H:%M:%S")
    db = database.get_db()
    try:
        db.execute(
            "INSERT INTO backup_records(id,kind,path,database_hash,created_at) VALUES (?,?,?,?,?)",
            (str(uuid.uuid4()), kind, str(path), digest, now),
        )
        db.commit()
    finally:
        db.close()
    enforce_retention()
    return {"kind": kind, "path": str(path), "hash": digest, "created_at": now, "size": path.stat().st_size}


def enforce_retention() -> None:
    for kind, keep in (("daily", 7), ("weekly", 4)):
        files = sorted(backup_directory().glob(f"{kind}-*.db"), reverse=True)
        for path in files[keep:]:
            path.unlink(missing_ok=True)


def scheduled_backup() -> list[dict]:
    today = time.strftime("%Y%m%d")
    created = []
    if not any(backup_directory().glob(f"daily-{today}-*.db")):
        created.append(create_backup("daily"))
    year_week = time.strftime("%Y%W")
    weekly = [path for path in backup_directory().glob("weekly-*.db") if time.strftime("%Y%W", time.localtime(path.stat().st_mtime)) == year_week]
    if not weekly:
        created.append(create_backup("weekly"))
    return created


def list_backups() -> list[dict]:
    result = []
    for path in sorted(backup_directory().glob("*.db"), key=lambda item: item.stat().st_mtime, reverse=True):
        result.append({
            "name": path.name, "path": str(path), "size": path.stat().st_size,
            "modified_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime)),
        })
    return result


def restore_backup(name: str) -> dict:
    if Path(name).name != name:
        raise ValueError("备份名称无效")
    source_path = backup_directory() / name
    if not source_path.is_file():
        raise FileNotFoundError("备份不存在")
    probe = sqlite3.connect(source_path)
    try:
        if probe.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("备份数据库已损坏")
    finally:
        probe.close()
    safety = create_backup("pre_restore")
    source = sqlite3.connect(source_path)
    destination = database.get_db()
    try:
        source.backup(destination)
    finally:
        destination.close(); source.close()
    database.init_db()
    return {"restored": name, "safety_backup": safety["path"]}
