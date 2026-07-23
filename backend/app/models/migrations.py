"""Versioned SQLite migrations for durable desktop data.

The legacy initializer still creates the 0.3.x baseline schema.  Migrations in
this module only describe changes introduced after that baseline so they can be
applied transactionally and audited independently.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Callable


CURRENT_SCHEMA_VERSION = 8


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split(None, 1)[0]
    if name not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def _migration_v1(conn: sqlite3.Connection) -> None:
    _add_column(conn, "projects", "edit_revision INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "projects", "media_status TEXT NOT NULL DEFAULT 'ready'")
    _add_column(conn, "segments", "speaker_id TEXT")

    for definition in (
        "priority INTEGER NOT NULL DEFAULT 0",
        "resource_class TEXT NOT NULL DEFAULT 'io'",
        "max_attempts INTEGER NOT NULL DEFAULT 1",
        "next_retry_at TEXT",
    ):
        _add_column(conn, "tasks", definition)

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS edit_operations (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            operation TEXT NOT NULL,
            before_json TEXT NOT NULL,
            after_json TEXT NOT NULL,
            base_revision INTEGER NOT NULL,
            result_revision INTEGER NOT NULL,
            undone INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_edit_operations_project
            ON edit_operations(project_id, result_revision DESC);

        CREATE TABLE IF NOT EXISTS segment_drafts (
            project_id TEXT PRIMARY KEY,
            base_revision INTEGER NOT NULL,
            draft_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS speakers (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            name TEXT NOT NULL,
            color TEXT NOT NULL DEFAULT '#5b8cff',
            external_key TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_speakers_project ON speakers(project_id);

        CREATE TABLE IF NOT EXISTS quality_issues (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            segment_id TEXT,
            rule_id TEXT NOT NULL,
            severity TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            message TEXT NOT NULL,
            suggestion TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY (segment_id) REFERENCES segments(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_quality_issues_project
            ON quality_issues(project_id, status, severity);

        CREATE TABLE IF NOT EXISTS glossaries (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            name TEXT NOT NULL,
            source_language TEXT NOT NULL DEFAULT 'auto',
            target_language TEXT NOT NULL DEFAULT 'zh',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS glossary_terms (
            id TEXT PRIMARY KEY,
            glossary_id TEXT NOT NULL,
            source_text TEXT NOT NULL,
            target_text TEXT NOT NULL DEFAULT '',
            case_sensitive INTEGER NOT NULL DEFAULT 0,
            whole_word INTEGER NOT NULL DEFAULT 1,
            do_not_translate INTEGER NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (glossary_id) REFERENCES glossaries(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_glossary_terms_glossary
            ON glossary_terms(glossary_id, source_text);

        CREATE TABLE IF NOT EXISTS translation_memory (
            id TEXT PRIMARY KEY,
            source_hash TEXT NOT NULL,
            source_language TEXT NOT NULL,
            target_language TEXT NOT NULL,
            source_text TEXT NOT NULL,
            target_text TEXT NOT NULL,
            origin TEXT NOT NULL DEFAULT 'machine',
            confirmed INTEGER NOT NULL DEFAULT 0,
            use_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(source_hash, source_language, target_language, target_text)
        );
        CREATE INDEX IF NOT EXISTS idx_translation_memory_lookup
            ON translation_memory(source_hash, source_language, target_language, confirmed DESC);

        CREATE TABLE IF NOT EXISTS style_templates (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            builtin INTEGER NOT NULL DEFAULT 0,
            settings_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS project_assets (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            path TEXT NOT NULL,
            fingerprint TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_project_assets_project
            ON project_assets(project_id, kind);

        CREATE TABLE IF NOT EXISTS watch_folders (
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            enabled INTEGER NOT NULL DEFAULT 1,
            workflow_json TEXT NOT NULL DEFAULT '{}',
            last_scan_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )


def _migration_v2(conn: sqlite3.Connection) -> None:
    _add_column(conn, "quality_issues", "details_json TEXT NOT NULL DEFAULT '{}'")
    for definition in (
        "audio_track_index INTEGER NOT NULL DEFAULT 0",
        "range_start REAL",
        "range_end REAL",
    ):
        _add_column(conn, "projects", definition)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cloud_authorizations (
            capability TEXT PRIMARY KEY,
            provider_id TEXT,
            granted INTEGER NOT NULL DEFAULT 0,
            disclosure_version TEXT NOT NULL,
            granted_at TEXT,
            revoked_at TEXT
        );
        CREATE TABLE IF NOT EXISTS backup_records (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            path TEXT NOT NULL,
            database_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )


def _migration_v3(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS watch_folder_files (
            watch_folder_id TEXT NOT NULL,
            path TEXT NOT NULL,
            size INTEGER NOT NULL,
            modified_ns INTEGER NOT NULL,
            fingerprint TEXT,
            stable_since TEXT NOT NULL,
            imported_project_id TEXT,
            PRIMARY KEY (watch_folder_id,path),
            FOREIGN KEY (watch_folder_id) REFERENCES watch_folders(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS batches (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            configuration_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS batch_items (
            id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL,
            project_id TEXT,
            source_path TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (batch_id) REFERENCES batches(id) ON DELETE CASCADE,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
        );
        """
    )


def _migration_v4(conn: sqlite3.Connection) -> None:
    """Fold legacy AI-clean revisions into the unified persistent editor history."""
    import json

    projects = conn.execute("SELECT DISTINCT project_id FROM segment_revisions").fetchall()
    columns = ("id","project_id","idx","start","end","raw_text","clean_text","translated_text","speaker","speaker_id","locked","is_draft","source_stage","transcription_run_id")
    for project in projects:
        project_id = project[0]
        current_rows = conn.execute("SELECT * FROM segments WHERE project_id=? ORDER BY idx", (project_id,)).fetchall()
        current = [{key: row[key] if key in row.keys() else None for key in columns} for row in current_rows]
        revision_row = conn.execute("SELECT edit_revision FROM projects WHERE id=?", (project_id,)).fetchone()
        revision = int(revision_row[0] or 0) if revision_row else 0
        legacy_rows = conn.execute("SELECT * FROM segment_revisions WHERE project_id=? ORDER BY created_at,rowid", (project_id,)).fetchall()
        for legacy in legacy_rows:
            operation_id = f"legacy:{legacy['id']}"
            if conn.execute("SELECT 1 FROM edit_operations WHERE id=?", (operation_id,)).fetchone(): continue
            revision += 1
            before = json.loads(legacy["segments_json"] or "[]")
            conn.execute(
                """INSERT INTO edit_operations
                   (id,project_id,operation,before_json,after_json,base_revision,result_revision,undone,created_at)
                   VALUES (?,?,?,?,?,?,?,0,?)""",
                (operation_id, project_id, legacy["operation"], json.dumps(before, ensure_ascii=False),
                 json.dumps(current, ensure_ascii=False), revision - 1, revision, legacy["created_at"]),
            )
        conn.execute("UPDATE projects SET edit_revision=MAX(edit_revision,?) WHERE id=?", (revision, project_id))


def _migration_v5(conn: sqlite3.Connection) -> None:
    _add_column(conn, "ai_settings", "has_api_key INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "ai_settings", "keychain_ref TEXT")
    _add_column(conn, "ai_provider_configs", "has_api_key INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "ai_provider_configs", "keychain_ref TEXT")
    conn.execute("UPDATE ai_settings SET has_api_key=CASE WHEN api_key<>'' THEN 1 ELSE has_api_key END")
    conn.execute("UPDATE ai_provider_configs SET has_api_key=CASE WHEN api_key<>'' THEN 1 ELSE has_api_key END")


def _migration_v6(conn: sqlite3.Connection) -> None:
    """Persist the effective style separately from reusable style templates."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS project_styles (
            project_id TEXT PRIMARY KEY,
            settings_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );
        """
    )


def _migration_v7(conn: sqlite3.Connection) -> None:
    """Keep word/token timestamps so subtitle splitting can use real speech boundaries."""
    _add_column(conn, "transcription_segments", "timings_json TEXT NOT NULL DEFAULT '[]'")


def _migration_v8(conn: sqlite3.Connection) -> None:
    """Persist YouTube playlist batches and their durable per-item stages."""
    for definition in (
        "kind TEXT NOT NULL DEFAULT 'local_files'",
        "source_url TEXT",
        "source_external_id TEXT",
        "title TEXT",
        "channel TEXT",
        "thumbnail_url TEXT",
        "last_synced_at TEXT",
        "paused INTEGER NOT NULL DEFAULT 0",
    ):
        _add_column(conn, "batches", definition)
    for definition in (
        "source_id TEXT",
        "source_url TEXT",
        "position INTEGER",
        "title TEXT",
        "duration REAL NOT NULL DEFAULT 0",
        "thumbnail_url TEXT",
        "source_state TEXT NOT NULL DEFAULT 'active'",
    ):
        _add_column(conn, "batch_items", definition)
    conn.executescript(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_playlist_batches_external_id
            ON batches(kind, source_external_id)
            WHERE kind='youtube_playlist' AND source_external_id IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_batch_items_source_id
            ON batch_items(batch_id, source_id)
            WHERE source_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_batch_items_project
            ON batch_items(project_id);
        CREATE TABLE IF NOT EXISTS batch_item_stages (
            item_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'waiting',
            task_id TEXT,
            attempt INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            error TEXT,
            configuration_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (item_id, stage),
            FOREIGN KEY (item_id) REFERENCES batch_items(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_batch_item_stages_task
            ON batch_item_stages(task_id);
        """
    )


MIGRATIONS: tuple[tuple[int, Callable[[sqlite3.Connection], None]], ...] = (
    (1, _migration_v1),
    (2, _migration_v2),
    (3, _migration_v3),
    (4, _migration_v4),
    (5, _migration_v5),
    (6, _migration_v6),
    (7, _migration_v7),
    (8, _migration_v8),
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
