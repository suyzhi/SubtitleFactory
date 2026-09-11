"""One leased CPU model, reused between jobs and released after idle time."""
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from threading import RLock, Timer
from typing import Callable


def memory_is_tight() -> bool:
    """Read the OS availability estimate, without allocating test memory."""
    try:
        if sys.platform == "darwin":
            result = subprocess.run(["/usr/bin/memory_pressure", "-Q"], capture_output=True, text=True, timeout=2)
            match = re.search(r"System-wide memory free percentage:\s*(\d+)%", result.stdout)
            return bool(match and int(match.group(1)) < 10)
        if sys.platform == "win32":
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return (stat.ullAvailPhys / stat.ullTotalPhys) < 0.1
            return False
        values = dict(line.split(":", 1) for line in Path('/proc/meminfo').read_text().splitlines())
        return int(values['MemAvailable'].split()[0]) / int(values['MemTotal'].split()[0]) < .1
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, AttributeError):
        return False


def model_identity(target: str, device: str, compute_type: str) -> tuple:
    path = Path(target).expanduser()
    if not path.exists():
        return (target, device, compute_type)
    files = [path] if path.is_file() else [path / name for name in ('model.bin', 'config.json', 'tokenizer.json')]
    stamps = []
    for file in files:
        if file.exists():
            stat = file.stat()
            stamps.append((str(file.resolve()), stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
    return (str(path.resolve()), device, compute_type, tuple(stamps))


class ModelPool:
    def __init__(self, idle_seconds: float = 300, pressure_check: Callable = memory_is_tight):
        self.idle_seconds = idle_seconds
        self.pressure_check = pressure_check
        self._released_at = 0.0
        self._lock = RLock()
        self._key = None
        self._model = None
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
