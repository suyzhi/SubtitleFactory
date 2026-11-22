"""Lightweight native/NumPy replacement for mlx_whisper's numba + scipy timing ops.

mlx_whisper only imports ``numba`` (pulling ~124 MiB of llvmlite/LLVM) and
``scipy`` (~33 MiB) inside ``mlx_whisper.timing`` for three small routines:
``median_filter``, ``dtw_cpu``, and ``backtrace``.

Installing this shim before importing ``mlx_whisper`` allows the frozen macOS
sidecar to exclude ``numba``, ``llvmlite``, and ``scipy`` completely while
keeping word-level timestamps identical and free of JIT warmup overhead.
"""

from __future__ import annotations

import ctypes
import os
import sys
import types
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np


def _candidate_library_paths() -> list[Path]:
    candidates: list[Path] = []
    env_path = os.getenv("SUBTITLE_FACTORY_MLX_TIMING_LIB", "").strip()
    if env_path:
        candidates.append(Path(env_path))

    resource_dir = os.getenv("SUBTITLE_FACTORY_RESOURCE_DIR", "").strip()
    if resource_dir:
        candidates.append(Path(resource_dir) / "lib" / "libmlx_timing.dylib")

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / "lib" / "libmlx_timing.dylib")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "lib" / "libmlx_timing.dylib")
            candidates.append(Path(meipass) / "libmlx_timing.dylib")

    backend_root = Path(__file__).resolve().parents[2]
    candidates.append(backend_root / "runtime" / "lib" / "libmlx_timing.dylib")
    candidates.append(
        backend_root.parent / "frontend" / "src-tauri" / "backend-runtime" / "lib" / "libmlx_timing.dylib"
    )
    return candidates


@lru_cache(maxsize=1)
def _load_native_lib() -> ctypes.CDLL | None:
    for path in _candidate_library_paths():
        if not path.is_file():
            continue
        try:
            lib = ctypes.CDLL(str(path))
            lib.mlx_dtw_cpu.argtypes = [
                ctypes.POINTER(ctypes.c_float),
                ctypes.c_int64,
                ctypes.c_int64,
                ctypes.POINTER(ctypes.c_int64),
                ctypes.POINTER(ctypes.c_int64),
            ]
            lib.mlx_dtw_cpu.restype = ctypes.c_int64
            lib.mlx_median_filter_1d.argtypes = [
                ctypes.POINTER(ctypes.c_float),
                ctypes.POINTER(ctypes.c_float),
                ctypes.c_int64,
                ctypes.c_int64,
                ctypes.c_int64,
            ]
            lib.mlx_median_filter_1d.restype = ctypes.c_int
            return lib
        except OSError:
            continue
    return None


def has_native_mlx_timing() -> bool:
    return _load_native_lib() is not None


def median_filter(x: np.ndarray, filter_width: int) -> np.ndarray:
    """Drop-in replacement for ``mlx_whisper.timing.median_filter``."""
    pad_width = filter_width // 2
    if x.shape[-1] <= pad_width:
        return x

    ndim = x.ndim
    arr = x[None, None, :] if ndim <= 2 else x

    assert (
        filter_width > 0 and filter_width % 2 == 1
    ), "`filter_width` should be an odd number"

    pad_spec = [(0, 0)] * (arr.ndim - 1) + [(pad_width, pad_width)]
    padded = np.ascontiguousarray(np.pad(arr, pad_spec, mode="reflect"), dtype=np.float32)

    lib = _load_native_lib()
    if lib is not None:
        length = arr.shape[-1]
        outer = int(np.prod(arr.shape[:-1])) if arr.ndim > 1 else 1
        result = np.empty(arr.shape, dtype=np.float32)
        rc = lib.mlx_median_filter_1d(
            padded.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            result.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            outer,
            length,
            filter_width,
        )
        if rc != 0:
            raise RuntimeError(f"native mlx_median_filter_1d failed with code {rc}")
    else:
        windows = np.lib.stride_tricks.sliding_window_view(padded, filter_width, axis=-1)
        result = np.median(windows, axis=-1).astype(np.float32, copy=False)

    if ndim <= 2:
        result = result[0, 0]
    return result


def backtrace(trace: np.ndarray) -> np.ndarray:
    """Drop-in replacement for ``mlx_whisper.timing.backtrace``."""
    i = trace.shape[0] - 1
    j = trace.shape[1] - 1
    trace[0, :] = 2
    trace[:, 0] = 1

    result: list[tuple[int, int]] = []
    while i > 0 or j > 0:
        result.append((i - 1, j - 1))
        step = trace[i, j]
        if step == 0:
            i -= 1
            j -= 1
        elif step == 1:
            i -= 1
        elif step == 2:
            j -= 1
        else:
            raise ValueError("Unexpected trace[i, j]")

    return np.array(result, dtype=np.int64)[::-1, :].T


def dtw_cpu(x: np.ndarray) -> np.ndarray:
    """Drop-in replacement for ``mlx_whisper.timing.dtw_cpu``."""
    arr = np.ascontiguousarray(x, dtype=np.float32)
    if arr.ndim != 2:
        raise ValueError(f"dtw_cpu expects a 2D array, got shape {arr.shape}")
    n, m = arr.shape
    lib = _load_native_lib()
    if lib is not None and n > 0 and m > 0:
        max_len = n + m
        out_rows = np.empty(max_len, dtype=np.int64)
        out_cols = np.empty(max_len, dtype=np.int64)
        count = lib.mlx_dtw_cpu(
            arr.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            n,
            m,
            out_rows.ctypes.data_as(ctypes.POINTER(ctypes.c_int64)),
            out_cols.ctypes.data_as(ctypes.POINTER(ctypes.c_int64)),
        )
        if count < 0:
            raise RuntimeError(f"native mlx_dtw_cpu failed with code {count}")
        return np.vstack((out_rows[:count], out_cols[:count]))

    cost = np.full((n + 1, m + 1), np.inf, dtype=np.float32)
    trace = np.full((n + 1, m + 1), -1, dtype=np.int8)
    cost[0, 0] = 0.0
    for j in range(1, m + 1):
        for i in range(1, n + 1):
            c0 = cost[i - 1, j - 1]
            c1 = cost[i - 1, j]
            c2 = cost[i, j - 1]
            if c0 < c1 and c0 < c2:
                c, t = c0, 0
            elif c1 < c0 and c1 < c2:
                c, t = c1, 1
            else:
                c, t = c2, 2
            cost[i, j] = arr[i - 1, j - 1] + c
            trace[i, j] = t
    return backtrace(trace)


def _shim_medfilt(volume: np.ndarray, kernel_size: Any = 3) -> np.ndarray:
    """Minimal scipy.signal.medfilt shim for mlx_whisper's 1D last-axis call."""
    if isinstance(kernel_size, int):
        width = kernel_size
    else:
        width = int(kernel_size[-1])
    pad_width = width // 2
    arr = np.asarray(volume, dtype=np.float32)
    if width == 1 or arr.size == 0:
        return arr.copy()
    pad_spec = [(0, 0)] * (arr.ndim - 1) + [(pad_width, pad_width)]
    padded = np.ascontiguousarray(np.pad(arr, pad_spec, mode="constant", constant_values=0.0), dtype=np.float32)
    windows = np.lib.stride_tricks.sliding_window_view(padded, width, axis=-1)
    return np.median(windows, axis=-1).astype(np.float32, copy=False)


def _install_optional_dependency_stubs() -> None:
    """Provide minimal stubs for numba/scipy only when absent from the bundle."""
    if "numba" not in sys.modules:
        try:
            __import__("numba")
        except ImportError:
            numba_mod = types.ModuleType("numba")

            def _noop_jit(*args: Any, **kwargs: Any):
                if len(args) == 1 and callable(args[0]) and not kwargs:
                    return args[0]

                def _decorator(fn: Any) -> Any:
                    return fn

                return _decorator

            numba_mod.jit = _noop_jit  # type: ignore[attr-defined]
            numba_mod.njit = _noop_jit  # type: ignore[attr-defined]
            sys.modules["numba"] = numba_mod

    if "scipy.signal" not in sys.modules:
        try:
            __import__("scipy.signal")
        except ImportError:
            scipy_mod = sys.modules.get("scipy")
            if scipy_mod is None:
                scipy_mod = types.ModuleType("scipy")
                scipy_mod.__path__ = []  # type: ignore[attr-defined]
                sys.modules["scipy"] = scipy_mod
            signal_mod = types.ModuleType("scipy.signal")
            signal_mod.medfilt = _shim_medfilt  # type: ignore[attr-defined]
            scipy_mod.signal = signal_mod  # type: ignore[attr-defined]
            sys.modules["scipy.signal"] = signal_mod


def install_mlx_timing_shim() -> Any:
    """Ensure mlx_whisper.timing uses our native/NumPy implementation."""
    _install_optional_dependency_stubs()
    import mlx_whisper.timing as timing_mod

    timing_mod.median_filter = median_filter
    timing_mod.backtrace = backtrace
    timing_mod.dtw_cpu = dtw_cpu
    return timing_mod
