"""Verified SQLite backups with restart-safe restore staging.

A running backend must never overwrite its own live database.  Restore requests
therefore create a verified staging copy and a durable marker.  The next
backend process applies that copy atomically before opening SQLite or starting
workers.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import quote

from ..models import database


PENDING_RESTORE_MARKER = "pending-restore.json"
LAST_RESTORE_RECEIPT = "last-restore.json"
PENDING_RESTORE_PREFIX = "pending-restore-"
_REQUIRED_BACKUP_TABLES = {"projects", "segments", "tasks"}
_RESTORE_LOCK = threading.RLock()
logger = logging.getLogger(__name__)


def backup_directory() -> Path:
    path = Path(database.DB_PATH).parent / "backups"
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def recovery_directory() -> Path:
    path = Path(database.DB_PATH).parent / "recovery"
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_json_atomically(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            temporary.chmod(0o600)
            json.dump(payload, output, ensure_ascii=False, separators=(",", ":"))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _write_checksum(path: Path, digest: str) -> None:
    checksum_path = path.with_name(f"{path.name}.sha256")
    temporary = checksum_path.with_name(f".{checksum_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            temporary.chmod(0o600)
            output.write(f"{digest}  {path.name}\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, checksum_path)
        _fsync_directory(checksum_path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_atomically(source: Path, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with source.open("rb") as input_file, temporary.open("xb") as output_file:
            temporary.chmod(0o600)
            shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
            output_file.flush()
            os.fsync(output_file.fileno())
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _validate_database(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError("备份不存在")
    if path.is_symlink():
        raise ValueError("备份不能是符号链接")
    uri = f"file:{quote(str(path.resolve()))}?mode=ro"
    probe = None
    try:
        probe = sqlite3.connect(uri, uri=True)
        integrity = probe.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise ValueError("备份数据库已损坏")
        tables = {
            row[0]
            for row in probe.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        missing = sorted(_REQUIRED_BACKUP_TABLES - tables)
        if missing:
            raise ValueError(f"备份不是可恢复的字幕工厂数据库：缺少 {', '.join(missing)}")
    except sqlite3.Error as error:
        raise ValueError("备份不是有效的 SQLite 数据库") from error
    finally:
        if probe is not None:
            probe.close()


def _checksum_hash(path: Path) -> str | None:
    checksum_path = path.with_name(f"{path.name}.sha256")
    try:
        digest = checksum_path.read_text(encoding="utf-8").split()[0].lower()
        if len(digest) == 64 and all(character in "0123456789abcdef" for character in digest):
            return digest
    except (IndexError, OSError):
        pass
    return None


def _recorded_hash(path: Path) -> str | None:
    digest = _checksum_hash(path)
    if digest:
        return digest
    db = None
    try:
        db = database.get_db()
        row = db.execute(
            "SELECT database_hash FROM backup_records WHERE path=? ORDER BY created_at DESC LIMIT 1",
            (str(path),),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        if db is not None:
            db.close()
    return str(row[0]) if row and row[0] else None


def _kind_from_name(name: str) -> str:
    return name.split("-", 1)[0] if "-" in name else "unknown"


def _backup_payload(
    path: Path,
    *,
    kind: str | None = None,
    digest: str | None = None,
    created_at: str | None = None,
) -> dict:
    modified_at = time.strftime(
        "%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime)
    )
    return {
        "name": path.name,
        "kind": kind or _kind_from_name(path.name),
        "path": str(path),
        "size": path.stat().st_size,
        "hash": digest,
        "created_at": created_at or modified_at,
        "modified_at": modified_at,
    }


def create_backup(kind: str = "manual") -> dict:
    if kind not in {"manual", "daily", "weekly", "pre_restore"}:
        raise ValueError("未知备份类型")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = backup_directory() / f"{kind}-{stamp}-{uuid.uuid4().hex[:6]}.db"
    # Create the destination with private permissions before SQLite ever opens
    # it.  Applying chmod only after backup completion leaves a brief window in
    # which a permissive umask could expose subtitle metadata to other users.
    path.touch(mode=0o600, exist_ok=False)
    source = None
    destination = None
    try:
        source = database.get_db()
        destination = sqlite3.connect(path)
        source.backup(destination)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()
    path.chmod(0o600)
    try:
        _validate_database(path)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    digest, now = _hash(path), time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        _write_checksum(path, digest)
    except Exception:
        path.unlink(missing_ok=True)
        path.with_name(f"{path.name}.sha256").unlink(missing_ok=True)
        raise
    try:
        db = database.get_db()
        try:
            db.execute(
                "INSERT INTO backup_records(id,kind,path,database_hash,created_at) VALUES (?,?,?,?,?)",
                (str(uuid.uuid4()), kind, str(path), digest, now),
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        path.unlink(missing_ok=True)
        path.with_name(f"{path.name}.sha256").unlink(missing_ok=True)
        raise
    try:
        enforce_retention()
    except Exception:
        logger.exception("数据库备份保留策略执行失败")
    return _backup_payload(path, kind=kind, digest=digest, created_at=now)


def enforce_retention() -> None:
    removed: list[str] = []
    for kind, keep in (("daily", 7), ("weekly", 4)):
        files = sorted(backup_directory().glob(f"{kind}-*.db"), reverse=True)
        for path in files[keep:]:
            removed.append(str(path))
            path.unlink(missing_ok=True)
            path.with_name(f"{path.name}.sha256").unlink(missing_ok=True)
    if removed:
        db = database.get_db()
        try:
            db.executemany("DELETE FROM backup_records WHERE path=?", [(path,) for path in removed])
            db.commit()
        finally:
            db.close()


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
    records: dict[str, tuple[str, str, str]] = {}
    db = None
    try:
        db = database.get_db()
        records = {
            str(row[0]): (str(row[1]), str(row[2]), str(row[3]))
            for row in db.execute(
                "SELECT path,kind,database_hash,created_at FROM backup_records"
            )
        }
    except sqlite3.Error:
        records = {}
    finally:
        if db is not None:
            db.close()
    result: list[dict] = []
    for path in sorted(backup_directory().glob("*.db"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            record = records.get(str(path))
            digest = _checksum_hash(path) or (record[1] if record else None)
            result.append(_backup_payload(
                path,
                kind=record[0] if record else None,
                digest=digest,
                created_at=record[2] if record else None,
            ))
        except FileNotFoundError:
            continue
    return result


def pending_restore() -> dict | None:
    return _read_json(recovery_directory() / PENDING_RESTORE_MARKER)


def last_restore() -> dict | None:
    return _read_json(recovery_directory() / LAST_RESTORE_RECEIPT)


def restore_backup(name: str) -> dict:
    with _RESTORE_LOCK:
        return _stage_restore(name)


def _stage_restore(name: str) -> dict:
    """Verify and stage a backup for application on the next process start."""
    if Path(name).name != name:
        raise ValueError("备份名称无效")
    if (recovery_directory() / PENDING_RESTORE_MARKER).exists():
        raise RuntimeError("已有数据库恢复等待重启应用，请先完成该恢复")
    source_path = backup_directory() / name
    _validate_database(source_path)
    digest = _hash(source_path)
    recorded = _recorded_hash(source_path)
    if recorded and digest != recorded:
        raise ValueError("备份校验值已变化，已拒绝恢复")

    safety = create_backup("pre_restore")
    recovery = recovery_directory()
    for obsolete in recovery.glob(f"{PENDING_RESTORE_PREFIX}*.db"):
        try:
            obsolete.unlink(missing_ok=True)
        except OSError:
            logger.warning("无法清理过期恢复暂存文件：%s", obsolete)
    staging_name = f"{PENDING_RESTORE_PREFIX}{uuid.uuid4().hex}.db"
    staging_path = recovery / staging_name
    _copy_atomically(source_path, staging_path)
    try:
        _validate_database(staging_path)
        if _hash(staging_path) != digest:
            raise ValueError("恢复暂存副本校验失败")
        marker = {
            "version": 1,
            "source_name": name,
            "source_hash": digest,
            "staging_name": staging_name,
            "safety_backup": safety["path"],
            "staged_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        _write_json_atomically(recovery / PENDING_RESTORE_MARKER, marker)
    except Exception:
        staging_path.unlink(missing_ok=True)
        raise

    return {
        "pending": True,
        "requires_restart": True,
        "source_name": name,
        "source_hash": digest,
        "safety_backup": safety["path"],
        "staged_at": marker["staged_at"],
    }


def apply_pending_restore() -> dict | None:
    with _RESTORE_LOCK:
        return _apply_pending_restore()


def _apply_pending_restore() -> dict | None:
    """Atomically apply a staged restore before the database is initialized."""
    recovery = recovery_directory()
    marker_path = recovery / PENDING_RESTORE_MARKER
    marker = _read_json(marker_path)
    if marker is None:
        if marker_path.exists():
            raise RuntimeError("待恢复标记已损坏，已停止启动以保护数据")
        return None
    staging_name = str(marker.get("staging_name") or "")
    if (
        Path(staging_name).name != staging_name
        or not staging_name.startswith(PENDING_RESTORE_PREFIX)
        or not staging_name.endswith(".db")
    ):
        raise RuntimeError("待恢复文件名无效，已停止启动以保护数据")
    staging_path = recovery / staging_name
    try:
        _validate_database(staging_path)
    except (FileNotFoundError, ValueError) as error:
        raise RuntimeError(f"待恢复数据库无法使用：{error}") from error
    expected_hash = str(marker.get("source_hash") or "")
    if not expected_hash or _hash(staging_path) != expected_hash:
        raise RuntimeError("待恢复数据库校验失败，已停止启动以保护数据")

    live_path = Path(database.DB_PATH)
    live_path.parent.mkdir(parents=True, exist_ok=True)
    applying_path = live_path.with_name(f".{live_path.name}.restore-{uuid.uuid4().hex}.tmp")
    try:
        _copy_atomically(staging_path, applying_path)
        _validate_database(applying_path)
        if _hash(applying_path) != expected_hash:
            raise RuntimeError("待恢复数据库的原子副本校验失败")
        for suffix in ("-wal", "-shm"):
            Path(f"{live_path}{suffix}").unlink(missing_ok=True)
        os.replace(applying_path, live_path)
        _fsync_directory(live_path.parent)
    except Exception:
        applying_path.unlink(missing_ok=True)
        raise

    receipt = {
        "status": "applied",
        "source_name": marker.get("source_name"),
        "source_hash": expected_hash,
        "safety_backup": marker.get("safety_backup"),
        "staged_at": marker.get("staged_at"),
        "applied_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _write_json_atomically(recovery / LAST_RESTORE_RECEIPT, receipt)
    marker_path.unlink(missing_ok=True)
    staging_path.unlink(missing_ok=True)
    _fsync_directory(recovery)
    return receipt
