"""Frozen desktop backend entry point."""

import io
import multiprocessing
import os
import signal
import sys
import threading
from typing import BinaryIO, Callable

import uvicorn


PARENT_WATCHDOG_ENV = "SUBTITLE_FACTORY_PARENT_WATCHDOG"
RUNTIME_VERIFY_ARGUMENT = "--verify-runtime"


def _verify_runtime_dependencies() -> None:
    """Load and minimally exercise native dependencies in the frozen bundle.

    Importing the Python package alone is not sufficient for release QA: the
    failure we need to catch is a missing or mis-routed Mach-O dependency after
    PyInstaller has rewritten ``@rpath`` entries.  Keep this check model-free so
    release builds can run it deterministically without downloads.
    """
    import av
    import ctranslate2
    import faster_whisper
    import mlx.core as mx
    import mlx_qwen3_asr
    import mlx_whisper
    import numpy as np
    import onnxruntime
    from PIL import Image
    import pysubs2
    import scipy.signal
    import sherpa_onnx
    import tiktoken

    if not mx.is_available(mx.gpu):
        raise RuntimeError("MLX Metal GPU is unavailable")
    previous_device = mx.default_device()
    mx.set_default_device(mx.gpu)
    try:
        array = mx.array([1, 2, 3]) + 1
        mx.eval(array)
        if array.tolist() != [2, 3, 4]:
            raise RuntimeError("MLX runtime returned an unexpected result")
    finally:
        mx.set_default_device(previous_device)

    image = Image.new("RGB", (2, 2), color="black")
    image_buffer = io.BytesIO()
    image.save(image_buffer, format="PNG")
    if not image_buffer.getvalue().startswith(b"\x89PNG"):
        raise RuntimeError("Pillow runtime failed to encode PNG")

    resampled = scipy.signal.resample(np.array([0.0, 1.0]), 4)
    if np.asarray(resampled).shape != (4,):
        raise RuntimeError("SciPy runtime returned an unexpected result")
    encoding = tiktoken.Encoding(
        name="runtime-smoke",
        pat_str=r"(?s).+",
        mergeable_ranks={b"ok": 0},
        special_tokens={},
    )
    if encoding.encode("ok") != [0]:
        raise RuntimeError("tiktoken runtime failed to encode text")
    if not isinstance(pysubs2.SSAFile(), pysubs2.SSAFile):
        raise RuntimeError("pysubs2 runtime failed to create a subtitle file")
    if "CoreMLExecutionProvider" not in onnxruntime.get_available_providers():
        raise RuntimeError("ONNX Runtime does not expose Core ML")
    if ctranslate2.get_cuda_device_count() != 0:
        raise RuntimeError("macOS release unexpectedly reported a CUDA device")

    # Keep explicit references so static analyzers and PyInstaller cannot treat
    # an otherwise import-only dependency as unused.
    versions = {
        "av": av.__version__,
        "faster_whisper": faster_whisper.__version__,
        "mlx_qwen3_asr": mlx_qwen3_asr.__version__,
        "mlx_whisper": mlx_whisper.__version__,
        "sherpa_onnx": sherpa_onnx.__version__,
    }
    print("冻结运行时自检通过：" + "、".join(f"{key} {value}" for key, value in versions.items()))


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


def main() -> None:
    multiprocessing.freeze_support()
    if sys.argv[1:] == [RUNTIME_VERIFY_ARGUMENT]:
        _verify_runtime_dependencies()
        return
    _start_parent_watchdog()
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=int(os.getenv("SUBTITLE_FACTORY_PORT", "8000")),
        log_level="info",
    )


if __name__ == "__main__":
    main()
