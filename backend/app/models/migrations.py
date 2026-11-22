"""Versioned SQLite migrations for durable desktop data."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Callable

CURRENT_SCHEMA_VERSION = 3


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split(None, 1)[0]
    if name not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def _migration_v1(conn: sqlite3.Connection) -> None:
    for definition in (
        "priority INTEGER NOT NULL DEFAULT 0",
        "resource_class TEXT NOT NULL DEFAULT 'io'",
        "max_attempts INTEGER NOT NULL DEFAULT 1",
        "next_retry_at TEXT",
    ):
        _add_column(conn, "tasks", definition)


def _migration_v2(conn: sqlite3.Connection) -> None:
    """Keep word/token timestamps so subtitle splitting can use real speech boundaries."""
    _add_column(conn, "transcription_segments", "timings_json TEXT NOT NULL DEFAULT '[]'")


def _migration_v3(conn: sqlite3.Connection) -> None:
    """Track configured API keys and per-provider thinking toggle."""
    _add_column(conn, "ai_provider_configs", "has_api_key INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "ai_provider_configs", "thinking_enabled INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        "UPDATE ai_provider_configs SET has_api_key=CASE WHEN api_key<>'' THEN 1 ELSE has_api_key END"
    )


MIGRATIONS: tuple[tuple[int, Callable[[sqlite3.Connection], None]], ...] = (
    (1, _migration_v1),
    (2, _migration_v2),
    (3, _migration_v3),
)


def _backup(conn: sqlite3.Connection, db_path: Path, version: int) -> Path | None:
    if not db_path.exists() or db_path.stat().st_size == 0:
        return None
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"schema-v{version}-{stamp}.db"
    destination = sqlite3.connect(target)
    try:
        conn.backup(destination)
    finally:
        destination.close()
    return target


def run_migrations(conn: sqlite3.Connection, db_path: Path) -> list[int]:
    """Apply every pending migration transactionally and return its versions."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               version INTEGER PRIMARY KEY,
               applied_at TEXT NOT NULL
           )"""
    )
    conn.commit()
    applied = {
        int(row[0]) for row in conn.execute("SELECT version FROM schema_migrations")
    }
    completed: list[int] = []
    for version, migration in MIGRATIONS:
        if version in applied:
            continue
        backup_path = _backup(conn, db_path, version)
        try:
            conn.execute("BEGIN IMMEDIATE")
            migration(conn)
            conn.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (version, time.strftime("%Y-%m-%d %H:%M:%S")),
            )
            conn.commit()
            completed.append(version)
        except Exception:
            conn.rollback()
            if backup_path and backup_path.is_file():
                source = sqlite3.connect(backup_path)
                try:
                    source.backup(conn)
                    conn.commit()
                finally:
                    source.close()
            raise
    return completed
