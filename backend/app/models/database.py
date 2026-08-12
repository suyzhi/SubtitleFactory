"""
字幕工厂 - 数据库初始化和管理
"""

import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from ..utils.config import DB_PATH
from .migrations import run_migrations
from ..security import signed_media_url


_INIT_DB_LOCK = threading.RLock()


def get_db(timeout: float = 30) -> sqlite3.Connection:
    """获取数据库连接（WAL模式，线程安全）"""
    # Playlist production deliberately overlaps downloads, transcription and
    # network AI work.  Their short write transactions can occasionally meet
    # at the same instant, so wait for the current writer instead of turning a
    # harmless contention window into a failed video stage.
    conn = sqlite3.connect(str(DB_PATH), timeout=timeout, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={max(0, int(timeout * 1000))}")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """初始化数据库表结构（含迁移）"""
    # Several project-library requests can arrive together during startup.
    # Serialize schema setup inside one backend process so two worker threads
    # cannot both observe a half-migrated database and apply the same seed row.
    with _INIT_DB_LOCK:
        _init_db_unlocked()


def _init_db_unlocked():
    """Initialize the database while the caller holds ``_INIT_DB_LOCK``."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '未命名项目',
            source_type TEXT NOT NULL DEFAULT 'youtube',
            source_url TEXT,
            video_path TEXT,
            audio_path TEXT,
            thumbnail_url TEXT,
            thumbnail_path TEXT,
            group_name TEXT,
            media_mode TEXT NOT NULL DEFAULT 'local',
            language TEXT DEFAULT 'auto',
            target_language TEXT DEFAULT 'zh',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted_at TEXT
        );

        CREATE TABLE IF NOT EXISTS segments (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            idx INTEGER NOT NULL,
            start REAL NOT NULL,
            end REAL NOT NULL,
            raw_text TEXT DEFAULT '',
            clean_text TEXT DEFAULT '',
            translated_text TEXT DEFAULT '',
            speaker TEXT DEFAULT '',
            locked INTEGER DEFAULT 0,
            is_draft INTEGER DEFAULT 0,
            source_stage TEXT DEFAULT 'final',
            transcription_run_id TEXT,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_segments_project ON segments(project_id);
        CREATE INDEX IF NOT EXISTS idx_segments_idx ON segments(project_id, idx);

        CREATE TABLE IF NOT EXISTS transcription_runs (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            task_id TEXT,
            model TEXT NOT NULL,
            language TEXT DEFAULT 'auto',
            status TEXT NOT NULL DEFAULT 'pending',
            attempt INTEGER NOT NULL DEFAULT 1,
            error_code TEXT,
            error_message TEXT,
            segments_count INTEGER NOT NULL DEFAULT 0,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_transcription_runs_project
            ON transcription_runs(project_id, started_at DESC);

        CREATE TABLE IF NOT EXISTS transcription_segments (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            idx INTEGER NOT NULL,
            start REAL NOT NULL,
            end REAL NOT NULL,
            text TEXT NOT NULL,
            timings_json TEXT NOT NULL DEFAULT '[]',
            is_draft INTEGER DEFAULT 1,
            FOREIGN KEY (run_id) REFERENCES transcription_runs(id) ON DELETE CASCADE,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_transcription_segments_run
            ON transcription_segments(run_id, idx);

        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            type TEXT NOT NULL,
            status TEXT NOT NULL,
            step TEXT DEFAULT '',
            progress REAL DEFAULT 0,
            message TEXT DEFAULT '',
            error TEXT,
            error_code TEXT,
            recoverable INTEGER DEFAULT 0,
            available_actions TEXT DEFAULT '[]',
            parent_task_id TEXT,
            attempt INTEGER DEFAULT 1,
            details TEXT DEFAULT '{}',
            logs TEXT DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ai_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            provider TEXT NOT NULL DEFAULT 'deepseek',
            base_url TEXT NOT NULL DEFAULT 'https://api.deepseek.com/v1',
            api_key TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT 'deepseek-chat',
            updated_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS app_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            settings_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS ai_provider_configs (
            provider_id TEXT PRIMARY KEY,
            base_url TEXT NOT NULL,
            api_key TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            last_test_status TEXT DEFAULT '',
            last_test_at TEXT DEFAULT '',
            last_latency_ms INTEGER DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS ai_batch_results (
            task_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            operation TEXT NOT NULL,
            batch_index INTEGER NOT NULL,
            input_fingerprint TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            result_json TEXT DEFAULT '[]',
            attempts INTEGER NOT NULL DEFAULT 0,
            error TEXT DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (task_id, batch_index)
        );

        CREATE TABLE IF NOT EXISTS imported_models (
            id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            family TEXT NOT NULL,
            version TEXT DEFAULT '',
            format TEXT NOT NULL,
            path TEXT NOT NULL,
            cli_path TEXT,
            runtimes_json TEXT NOT NULL DEFAULT '[]',
            fingerprint TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ready',
            last_error TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS segment_revisions (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            operation TEXT NOT NULL,
            segments_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_revisions_project ON segment_revisions(project_id, created_at DESC);
    """)
    # 迁移：为旧表添加 is_draft 和 source_stage 列（如果不存在）
    try:
        conn.execute("ALTER TABLE segments ADD COLUMN is_draft INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # 列已存在
    try:
        conn.execute("ALTER TABLE segments ADD COLUMN source_stage TEXT DEFAULT 'final'")
        # 旧数据标记为 final
        conn.execute("UPDATE segments SET source_stage = 'final' WHERE source_stage IS NULL")
    except sqlite3.OperationalError:
        pass  # 列已存在
    try:
        conn.execute("ALTER TABLE segments ADD COLUMN transcription_run_id TEXT")
    except sqlite3.OperationalError:
        pass
    for column in ("thumbnail_url", "thumbnail_path", "group_name", "deleted_at"):
        try:
            conn.execute(f"ALTER TABLE projects ADD COLUMN {column} TEXT")
        except sqlite3.OperationalError:
            pass
    try:
        conn.execute(
            "ALTER TABLE projects ADD COLUMN media_mode TEXT NOT NULL DEFAULT 'local'"
        )
    except sqlite3.OperationalError:
        pass
    for column, definition in [
        ("last_test_status", "TEXT DEFAULT ''"),
        ("last_test_at", "TEXT DEFAULT ''"),
        ("last_latency_ms", "INTEGER DEFAULT 0"),
    ]:
        try:
            conn.execute(f"ALTER TABLE ai_settings ADD COLUMN {column} {definition}")
        except sqlite3.OperationalError:
            pass

    # Migrate the single v0.2 AI configuration into its provider card once.
    legacy = conn.execute("SELECT * FROM ai_settings WHERE id=1").fetchone()
    if legacy and not conn.execute("SELECT 1 FROM ai_provider_configs LIMIT 1").fetchone():
        conn.execute(
            """INSERT OR IGNORE INTO ai_provider_configs
               (provider_id,base_url,api_key,model,updated_at,last_test_status,last_test_at,last_latency_ms)
               VALUES (?,?,?,?,?,?,?,?)""",
            (legacy["provider"], legacy["base_url"], legacy["api_key"], legacy["model"],
             legacy["updated_at"], legacy["last_test_status"], legacy["last_test_at"], legacy["last_latency_ms"]),
        )

    conn.commit()
    run_migrations(conn, Path(DB_PATH))
    conn.close()
    print(f"[DB] 数据库已初始化: {DB_PATH}")


def mark_interrupted_tasks():
    """Finalize every non-terminal worker left by the previous backend process.

    Task rows are the public history, while several feature tables keep their
    own staging state.  Recovery happens before this process can submit new
    work, so every still-active row belongs to the previous process and can be
    reconciled in one transaction without racing a live worker.
    """
    conn = get_db()
    rows = [dict(row) for row in conn.execute(
        "SELECT * FROM tasks WHERE status IN ('pending','running','paused')"
    )]
    now = time.strftime("%Y-%m-%d %H:%M:%S")

    def json_object(value) -> dict:
        try:
            decoded = json.loads(value or "{}")
            return decoded if isinstance(decoded, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}

    def json_array(value) -> list:
        try:
            decoded = json.loads(value or "[]")
            return decoded if isinstance(decoded, list) else []
        except (TypeError, ValueError, json.JSONDecodeError):
            return []

    settings_tasks = {"prepare_model", "prepare_speaker_models"}
    for row in rows:
        previous_status = str(row.get("status") or "running")
        details = json_object(row.get("details"))
        details["interruption"] = {
            "previous_status": previous_status,
            "interrupted_at": now,
            "published_data_preserved": True,
        }
        if previous_status == "paused":
            suggestion = "该任务在退出前已暂停；请检查参数后手动重新开始，应用不会擅自继续"
            error = "应用退出时任务仍处于暂停状态"
        else:
            suggestion = "项目、已发布字幕和已完成阶段均已保留；请从对应工作区重新开始"
            error = "应用在任务执行期间退出"
        details["failure_suggestion"] = suggestion

        logs = json_array(row.get("logs"))
        logs.append({
            "time": now,
            "level": "warning",
            "step": str(row.get("step") or "startup_recovery"),
            "message": "检测到上次运行被中断",
            "detail": error,
            "suggestion": suggestion,
        })

        actions = [
            action for action in json_array(row.get("available_actions"))
            if isinstance(action, str)
        ]
        if row.get("type") in settings_tasks:
            if "open_settings" not in actions:
                actions.append("open_settings")
        elif row.get("project_id") and "retry" not in actions:
            actions.append("retry")
        recoverable = bool(actions)
        conn.execute(
            """UPDATE tasks
                  SET status='failed',error_code='APP_INTERRUPTED',error=?,
                      recoverable=?,available_actions=?,message='上次运行被中断，项目内容已保留',
                      details=?,logs=?,next_retry_at=NULL,updated_at=?
                WHERE id=?""",
            (
                error, int(recoverable), json.dumps(actions, ensure_ascii=False),
                json.dumps(details, ensure_ascii=False),
                json.dumps(logs, ensure_ascii=False), now, row["id"],
            ),
        )

    # A transcription run is an unpublished staging area.  Preserve its draft
    # segments for diagnostics, but never leave the run claiming to be live.
    conn.execute(
        """UPDATE transcription_runs
              SET status='failed',error_code='APP_INTERRUPTED',
                  error_message='应用在转写期间退出；原有已发布字幕未被覆盖',
                  finished_at=?
            WHERE status IN ('pending','running')""",
        (now,),
    )
    # Network AI calls can have an uncertain remote outcome after a crash.
    # Mark their local staging rows failed so a later user-confirmed retry can
    # reuse only known-successful cached batches.
    conn.execute(
        """UPDATE ai_batch_results
              SET status='failed',error='应用在 AI 请求期间退出，未自动重试',updated_at=?
            WHERE status IN ('pending','running')""",
        (now,),
    )
    # Reconcile feature-owned state as well as the task row.  These tables are
    # initialized before startup recovery runs, so no newly-created task can be
    # mistaken for an interrupted worker.
    conn.execute(
        """UPDATE clip_renders
              SET status='failed',error='应用在渲染期间退出',
                  updated_at=?
            WHERE status IN ('pending','running')""",
        (now,),
    )
    conn.execute(
        """UPDATE clip_sets SET status='failed',updated_at=?
            WHERE status='pending'""",
        (now,),
    )
    conn.execute(
        """UPDATE content_pack_sections
              SET status='failed',error='应用在生成期间退出',
                  updated_at=?
            WHERE status IN ('pending','generating')""",
        (now,),
    )
    conn.execute(
        """UPDATE content_packs
              SET status=CASE
                    WHEN EXISTS(
                        SELECT 1 FROM content_pack_sections s
                        WHERE s.pack_id=content_packs.id AND s.status='ready'
                    ) THEN 'partial' ELSE 'failed' END,
                  updated_at=?
            WHERE status IN ('pending','generating')""",
        (now,),
    )
    conn.commit()
    conn.close()
    return rows


def segment_to_dict(row) -> dict:
    """将 segments 行转为字典"""
    is_draft = False
    source_stage = "final"
    try:
        is_draft = bool(row["is_draft"])
        source_stage = row["source_stage"] or "final"
    except (IndexError, KeyError):
        pass
    row_keys = set(row.keys())
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "index": row["idx"],
        "start": row["start"],
        "end": row["end"],
        "raw_text": row["raw_text"],
        "clean_text": row["clean_text"],
        "translated_text": row["translated_text"],
        "speaker": row["speaker"],
        "speaker_id": row["speaker_id"] if "speaker_id" in row_keys else None,
        "locked": bool(row["locked"]),
        "is_draft": is_draft,
        "source_stage": source_stage,
    }


_YOUTUBE_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _youtube_video_id(source_url: str | None) -> str | None:
    """Extract a stable video id from common YouTube URL shapes."""
    if not source_url:
        return None
    candidate_url = source_url.strip()
    if "://" not in candidate_url:
        candidate_url = f"https://{candidate_url}"
    parsed = urlparse(candidate_url)
    hostname = (parsed.hostname or "").lower()
    video_id = None

    if hostname in {"youtu.be", "www.youtu.be"}:
        video_id = parsed.path.strip("/").split("/", 1)[0]
    elif hostname == "youtube.com" or hostname.endswith(".youtube.com"):
        path_parts = [part for part in parsed.path.split("/") if part]
        if path_parts and path_parts[0] == "watch":
            video_id = (parse_qs(parsed.query).get("v") or [None])[0]
        elif len(path_parts) >= 2 and path_parts[0] in {"embed", "live", "shorts"}:
            video_id = path_parts[1]

    return video_id if video_id and _YOUTUBE_VIDEO_ID.fullmatch(video_id) else None


def _youtube_thumbnail_url(source_url: str | None) -> str | None:
    """Derive a stable public thumbnail for common legacy YouTube URLs."""
    video_id = _youtube_video_id(source_url)
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else None


def project_to_dict(row) -> dict:
    """将 projects 行转为字典"""
    row_keys = set(row.keys())
    source_thumbnail_url = row["thumbnail_url"] if "thumbnail_url" in row_keys else None
    thumbnail_path = row["thumbnail_path"] if "thumbnail_path" in row_keys else None
    group_name = row["group_name"] if "group_name" in row_keys else None
    deleted_at = row["deleted_at"] if "deleted_at" in row_keys else None
    media_mode = row["media_mode"] if "media_mode" in row_keys else "local"
    if media_mode not in {"local", "web"}:
        media_mode = "local"
    thumbnail_url = source_thumbnail_url
    if not thumbnail_url and row["source_type"] == "youtube":
        thumbnail_url = _youtube_thumbnail_url(row["source_url"])
    thumbnail_access_url = None
    if thumbnail_path and os.path.isfile(thumbnail_path):
        thumbnail_url = f"/api/projects/{row['id']}/thumbnail"
        thumbnail_access_url = signed_media_url(thumbnail_url)

    video_url = None
    if row["video_path"] and os.path.isfile(row["video_path"]):
        video_url = signed_media_url(f"/api/projects/{row['id']}/video")

    return {
        "id": row["id"],
        "title": row["title"],
        "source_type": row["source_type"],
        "source_url": row["source_url"],
        "video_path": row["video_path"],
        "video_url": video_url,
        "audio_path": row["audio_path"],
        "thumbnail_url": thumbnail_url,
        "thumbnail_access_url": thumbnail_access_url,
        "group_name": group_name,
        "language": row["language"],
        "target_language": row["target_language"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "deleted_at": deleted_at,
        "edit_revision": int(row["edit_revision"] or 0) if "edit_revision" in row_keys else 0,
        "media_status": (row["media_status"] or "ready") if "media_status" in row_keys else "ready",
        "media_mode": media_mode,
        "youtube_video_id": (
            _youtube_video_id(row["source_url"])
            if row["source_type"] == "youtube" else None
        ),
        "video_available": bool(row["video_path"] and os.path.isfile(row["video_path"])),
        "audio_available": bool(row["audio_path"] and os.path.isfile(row["audio_path"])),
    }
