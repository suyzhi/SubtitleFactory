"""Frozen desktop backend entry point."""

import multiprocessing
import os
import signal
import sys
import threading
from typing import BinaryIO, Callable

import uvicorn


PARENT_WATCHDOG_ENV = "SUBTITLE_FACTORY_PARENT_WATCHDOG"


def _terminate_managed_process_group() -> None:
    """Ask the frozen sidecar and its helpers to stop after the App disappears."""
    if os.name == "posix":
        try:
            # Tauri launches the backend as its own process-group leader.  A
            # group signal also reaches FFmpeg/model helpers that would
            # otherwise be re-parented when the desktop process crashes.
            os.killpg(os.getpgrp(), signal.SIGTERM)
            return
        except OSError:
            pass
    os.kill(os.getpid(), signal.SIGTERM)


def _watch_parent_pipe(
    stream: BinaryIO,
    terminate: Callable[[], None] = _terminate_managed_process_group,
) -> None:
    """Block until Tauri's private stdin pipe closes, then terminate safely."""
    try:
        while stream.read(1):
            # The parent deliberately never writes. Reading remains useful if
            # a future launcher sends a heartbeat byte before handing off.
            pass
    except (OSError, ValueError):
        # A closed/invalid descriptor is equivalent to EOF: ownership ended.
        pass
    terminate()


def _start_parent_watchdog() -> threading.Thread | None:
    if os.getenv(PARENT_WATCHDOG_ENV, "").strip().lower() != "stdin":
        return None
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    worker = threading.Thread(
        target=_watch_parent_pipe,
        args=(stream,),
        name="subtitle-factory-parent-watchdog",
        daemon=True,
    )
    worker.start()
    return worker


if __name__ == "__main__":
    multiprocessing.freeze_support()
    _start_parent_watchdog()
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=int(os.getenv("SUBTITLE_FACTORY_PORT", "8000")),
        log_level="info",
    )
