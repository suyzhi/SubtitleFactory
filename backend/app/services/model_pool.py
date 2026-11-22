"""Leased local inference models, reused between jobs and released after idle time."""
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from threading import RLock, Timer
from typing import Any, Callable, Iterable


@lru_cache(maxsize=1)
def optimal_cpu_threads() -> int:
    """Return the performance-core count on Apple Silicon, avoiding slower E-cores."""
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["/usr/sbin/sysctl", "-n", "hw.perflevel0.physicalcpu"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            value = int((result.stdout or "").strip())
            if value > 0:
                return max(1, min(8, value))
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return max(1, min(4, os.cpu_count() or 2))


def memory_is_tight() -> bool:
    """Read the OS availability estimate, without allocating test memory."""
    try:
        if sys.platform == "darwin":
            result = subprocess.run(["/usr/bin/memory_pressure", "-Q"], capture_output=True, text=True, timeout=2)
            match = re.search(r"System-wide memory free percentage:\s*(\d+)%", result.stdout)
            return bool(match and int(match.group(1)) < 10)
        values = dict(line.split(":", 1) for line in Path('/proc/meminfo').read_text().splitlines())
        return int(values['MemAvailable'].split()[0]) / int(values['MemTotal'].split()[0]) < .1
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return False


def model_identity(
    target: str,
    device: str,
    compute_type: str,
    files: Iterable[str | Path] | None = None,
) -> tuple:
    path = Path(target).expanduser()
    if files is not None:
        tracked = [Path(item).expanduser() for item in files]
    elif not path.exists():
        return (target, device, compute_type)
    else:
        tracked = [path] if path.is_file() else [path / name for name in ("model.bin", "config.json", "tokenizer.json")]
    stamps = []
    for file in tracked:
        if file.exists():
            stat = file.stat()
            stamps.append((str(file.resolve()), stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
    resolved = str(path.resolve()) if path.exists() else target
    return (resolved, device, compute_type, tuple(stamps))


class ModelPool:
    def __init__(self, idle_seconds: float = 300, pressure_check: Callable = memory_is_tight):
        self.idle_seconds = idle_seconds
        self.pressure_check = pressure_check
        self._released_at = 0.0
        self._lock = RLock()
        self._key: tuple | None = None
        self._model: Any = None
        self._timer: Timer | None = None
        self._generation = 0

    @contextmanager
    def lease(self, key: tuple, loader: Callable):
        # A shared inference instance must never serve two jobs concurrently.
        with self._lock:
            self._generation += 1
            generation = self._generation
            if self._timer:
                self._timer.cancel()
                self._timer = None
            reused = self._model is not None and key == self._key
            if not reused:
                self._model = None
                self._key = None
                self._model = loader()
                self._key = key
            try:
                yield self._model, reused
            finally:
                self._released_at = time.monotonic()
                if self.pressure_check():
                    self._model = self._key = None
                else:
                    self._schedule(generation)

    def _schedule(self, generation):
        self._timer = Timer(min(15, self.idle_seconds), self._expire, args=(generation,))
        self._timer.daemon = True
        self._timer.start()

    def _expire(self, generation: int):
        with self._lock:
            if generation == self._generation:
                if time.monotonic() - self._released_at >= self.idle_seconds or self.pressure_check():
                    self._model = self._key = self._timer = None
                else:
                    self._schedule(generation)

    def clear(self):
        with self._lock:
            self._generation += 1
            if self._timer:
                self._timer.cancel()
                self._timer = None
            self._model = None
            self._key = None


cpu_model_pool = ModelPool()
sherpa_model_pool = ModelPool()
