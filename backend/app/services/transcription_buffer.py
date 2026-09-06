"""Batch draft writes without keeping a SQLite write transaction open during inference."""
import time

from ..models.database import get_db

_INSERT = """INSERT INTO transcription_segments
    (id,run_id,project_id,idx,start,end,text,timings_json,is_draft)
    VALUES (?,?,?,?,?,?,?,?,1)"""


class DraftWriter:
    def __init__(self, batch_size=8, interval=0.5):
        self.batch_size = batch_size
        self.interval = interval
        self.pending = []
        self.last_flush = 0.0
        self.connection = None

    def __enter__(self):
        self.connection = get_db()
        return self

    def append(self, row) -> bool:
        self.pending.append(row)
        if len(self.pending) >= self.batch_size or time.monotonic() - self.last_flush >= self.interval:
            self.flush()
            return True
        return False

    def flush(self):
        if self.pending:
            try:
                self.connection.executemany(_INSERT, self.pending)
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise
            self.pending.clear()
            self.last_flush = time.monotonic()

    def __exit__(self, *_):
        try:
            self.flush()
        finally:
            self.connection.close()
