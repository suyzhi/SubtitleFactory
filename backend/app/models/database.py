"""
字幕工厂（简洁版）- 数据库初始化和管理
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

_INIT_DB_LOCK = threading.RLock()


def get_db(timeout: float = 30) -> sqlite3.Connection:
    """获取数据库连接（WAL 模式，线程安全）"""
    conn = sqlite3.connect(str(DB_PATH), timeout=timeout, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={max(0, int(timeout * 1000))}")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """初始化数据库表结构（含迁移）"""
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
            language TEXT DEFAULT 'auto',
            target_language TEXT DEFAULT 'zh',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
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
            priority INTEGER NOT NULL DEFAULT 0,
            resource_class TEXT NOT NULL DEFAULT 'io',
            max_attempts INTEGER NOT NULL DEFAULT 1,
            next_retry_at TEXT,
            details TEXT DEFAULT '{}',
            logs TEXT DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
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
            thinking_enabled INTEGER NOT NULL DEFAULT 0,
            has_api_key INTEGER NOT NULL DEFAULT 0,
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
    """)

    conn.commit()
    run_migrations(conn, Path(DB_PATH))
    conn.commit()
    conn.close()


def mark_interrupted_tasks():
    """Finalize every non-terminal worker left by the previous backend process."""
    conn = get_db()
    rows = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM tasks WHERE status IN ('pending','running','paused')"
        )
    ]
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

    for row in rows:
        previous_status = str(row.get("status") or "running")
        details = json_object(row.get("details"))
        details["interruption"] = {
            "previous_status": previous_status,
            "interrupted_at": now,
            "published_data_preserved": True,
        }
        suggestion = "项目、已生成字幕和已完成阶段均已保留；请重新开始任务"
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
            action
            for action in json_array(row.get("available_actions"))
            if isinstance(action, str)
        ]
        if row.get("project_id") and "retry" not in actions:
            actions.append("retry")
        recoverable = bool(actions)
        conn.execute(
            """UPDATE tasks
                  SET status='failed',error_code='APP_INTERRUPTED',error=?,
                      recoverable=?,available_actions=?,message='上次运行被中断，项目内容已保留',
                      details=?,logs=?,next_retry_at=NULL,updated_at=?
                WHERE id=?""",
            (
                error,
                int(recoverable),
                json.dumps(actions, ensure_ascii=False),
                json.dumps(details, ensure_ascii=False),
                json.dumps(logs, ensure_ascii=False),
                now,
                row["id"],
            ),
        )

    conn.execute(
        """UPDATE transcription_runs
              SET status='failed',error_code='APP_INTERRUPTED',
                  error_message='应用在转写期间退出；原有已发布字幕未被覆盖',
                  finished_at=?
            WHERE status IN ('pending','running')""",
        (now,),
    )
    conn.execute(
        """UPDATE ai_batch_results
              SET status='failed',error='应用在 AI 请求期间退出，未自动重试',updated_at=?
            WHERE status IN ('pending','running')""",
        (now,),
    )
    conn.commit()
    conn.close()
    return rows


def segment_to_dict(row) -> dict:
    """将 segments 行转为字典"""
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
        "is_draft": bool(row["is_draft"]) if "is_draft" in row_keys else False,
        "source_stage": (row["source_stage"] or "final") if "source_stage" in row_keys else "final",
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
            video_id = (parse_qs(parsed.query).get("v") or [""])[0]
        elif len(path_parts) >= 2 and path_parts[0] in {"embed", "live", "shorts"}:
            video_id = path_parts[1]

    return video_id if video_id and _YOUTUBE_VIDEO_ID.fullmatch(video_id) else None


def project_to_dict(row) -> dict:
    """将 projects 行转为字典"""
    return {
        "id": row["id"],
        "title": row["title"],
        "source_type": row["source_type"],
        "source_url": row["source_url"],
        "video_path": row["video_path"],
        "audio_path": row["audio_path"],
        "language": row["language"],
        "target_language": row["target_language"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "youtube_video_id": (
            _youtube_video_id(row["source_url"])
            if row["source_type"] == "youtube"
            else None
        ),
        "video_available": bool(row["video_path"] and os.path.isfile(row["video_path"])),
        "audio_available": bool(row["audio_path"] and os.path.isfile(row["audio_path"])),
    }
