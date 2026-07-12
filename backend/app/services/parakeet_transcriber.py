"""Independent Parakeet runtimes: App-managed ONNX and optional Core ML.

The ONNX model is downloaded only into the App data directory. Memo's Core ML
runtime is detected as an optional external accelerator and is never treated as
a release dependency or silently substituted for an explicitly selected model.
"""

from __future__ import annotations

import json
import logging
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from ..utils.config import MODELS_DIR, environment_path_overrides_enabled
from ..utils.task_manager import TaskCancelled, task_manager
from .runtime_diagnostics import validate_runtime_executable

logger = logging.getLogger(__name__)

PARAKEET_MODEL_ID = "parakeet-tdt-0.6b-v3-coreml"
PARAKEET_ONNX_MODEL_ID = "parakeet-tdt-0.6b-v3-int8"
PARAKEET_MODEL_DIR_NAME = f"sherpa-onnx-nemo-{PARAKEET_ONNX_MODEL_ID}"
PARAKEET_DISPLAY_NAME = "Parakeet TDT 0.6B v3 (sherpa-onnx int8)"
PARAKEET_COREML_DISPLAY_NAME = "Parakeet TDT 0.6B v3 (Core ML)"
PARAKEET_COREML_MODEL_ENV = "PARAKEET_COREML_MODEL_DIR"
PARAKEET_COREML_CLI_ENV = "PARAKEET_COREML_CLI"
PARAKEET_ARCHIVE_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    f"{PARAKEET_MODEL_DIR_NAME}.tar.bz2"
)
SILERO_VAD_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    "silero_vad.onnx"
)

# Exact release-asset byte sizes. Besides making progress deterministic, this
# prevents a proxy/login HTML page or a truncated response from becoming cache.
PARAKEET_ARCHIVE_BYTES = 487_170_055
SILERO_VAD_BYTES = 643_854
PARAKEET_EXTRACTED_ESTIMATE_BYTES = 671_000_000

# Sizes are deliberately lower bounds: they catch incomplete extraction while
# allowing an upstream-compatible re-export to remain usable.
PARAKEET_REQUIRED_FILES = {
    "encoder.int8.onnx": 600_000_000,
    "decoder.int8.onnx": 10_000_000,
    "joiner.int8.onnx": 5_000_000,
    "tokens.txt": 50_000,
}

PARAKEET_SUPPORTED_LANGUAGES = frozenset(
    {
        "bg", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "de",
        "el", "hu", "it", "lv", "lt", "mt", "pl", "pt", "ro", "sk",
        "sl", "es", "sv", "ru", "uk",
    }
)

_DOWNLOAD_LOCK = threading.Lock()
_PUNCTUATION_ONLY = re.compile(r"^[\W_]+$", re.UNICODE)
_TEXT_PROGRESS = re.compile(r"(?<!\d)(100|\d{1,2})\s*%")
_SENTENCE_ENDINGS = frozenset(".!?。！？")
_COREML_REQUIRED_MODEL_ENTRIES = (
    "Encoder.mlmodelc",
    "Decoder.mlmodelc",
    "Preprocessor.mlmodelc",
    "JointDecision.mlmodelc",
    "parakeet_v3_vocab.json",
)


@dataclass(frozen=True)
class CoreMLRuntime:
    model_dir: Path
    cli_path: Path
    source: str = "external_detected"


@dataclass(frozen=True)
class ParakeetAssets:
    model_dir: Path
    encoder: Path
    decoder: Path
    joiner: Path
    tokens: Path
    vad: Path


@dataclass(frozen=True)
class ParakeetSegment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class ParakeetSession:
    segments: Iterator[ParakeetSegment]
    audio_duration: float
    detected_language: str
    device: str = "cpu"
    compute_type: str = "int8 ONNX"
    model_label: str = PARAKEET_DISPLAY_NAME
    progress_start: float = 25.0


def _valid_coreml_model_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    for name in _COREML_REQUIRED_MODEL_ENTRIES:
        candidate = path / name
        if name.endswith(".mlmodelc"):
            if not candidate.is_dir():
                return False
        elif not candidate.is_file():
            return False
    return True


def _valid_coreml_cli(path: Path) -> bool:
    if not path.is_file() or not os.access(path, os.X_OK):
        return False
    return validate_runtime_executable(
        path,
        name="parakeet",
        source="external_detected",
        version_args=("--help",),
    ).available


def discover_coreml_runtime(
    model_dir: str | Path | None = None,
    cli_path: str | Path | None = None,
    *,
    strict: bool = True,
    allow_environment: bool | None = None,
) -> CoreMLRuntime | None:
    """Find Memo's Core ML runtime without embedding a user-specific path.

    Explicit App-setting paths are authoritative. Environment paths are a
    development/advanced diagnostic feature and are ignored by normal frozen
    releases. ``strict=False`` is useful for startup fallback/status checks.
    """
    if allow_environment is None:
        allow_environment = environment_path_overrides_enabled()
    environment_model = os.getenv(PARAKEET_COREML_MODEL_ENV) if allow_environment else None
    environment_cli = os.getenv(PARAKEET_COREML_CLI_ENV) if allow_environment else None
    explicit_model = model_dir if model_dir is not None else environment_model
    explicit_cli = cli_path if cli_path is not None else environment_cli
    source = "custom_path" if model_dir is not None or cli_path is not None else "external_detected"

    if explicit_model:
        resolved_model = Path(explicit_model).expanduser().resolve()
        if not _valid_coreml_model_dir(resolved_model):
            if strict:
                raise RuntimeError("所选 Core ML 模型目录无效或文件不完整")
            return None
    else:
        resolved_model = None

    if explicit_cli:
        resolved_cli = Path(explicit_cli).expanduser().resolve()
        if not _valid_coreml_cli(resolved_cli):
            if strict:
                raise RuntimeError("所选 Parakeet CLI 不可执行或与当前架构不兼容")
            return None
    else:
        resolved_cli = None

    if sys.platform == "darwin":
        memo_root = Path.home() / "Library" / "Application Support" / "Memo"
        if resolved_model is None:
            candidate = memo_root / "models" / PARAKEET_MODEL_ID
            if _valid_coreml_model_dir(candidate):
                resolved_model = candidate.resolve()
        if resolved_cli is None:
            candidate = memo_root / "plugins" / "parakeet-cli" / "parakeet"
            if _valid_coreml_cli(candidate):
                resolved_cli = candidate.resolve()

    if resolved_cli is None and allow_environment:
        executable = shutil.which("parakeet")
        if executable and _valid_coreml_cli(Path(executable)):
            resolved_cli = Path(executable).resolve()

    if explicit_model and resolved_cli is None:
        if strict:
            raise RuntimeError("已选择 Core ML 模型，但没有配套的可执行 Parakeet CLI")
        return None
    if explicit_cli and resolved_model is None:
        if strict:
            raise RuntimeError("已选择 Parakeet CLI，但没有配套的完整 Core ML 模型")
        return None

    if resolved_model is not None and resolved_cli is not None:
        return CoreMLRuntime(model_dir=resolved_model, cli_path=resolved_cli, source=source)
    return None


def validate_coreml_runtime_paths(
    model_dir: str | Path | None,
    cli_path: str | Path | None,
) -> dict:
    """Validate file-picker values without exposing them in logs or defaults."""
    try:
        runtime = discover_coreml_runtime(
            model_dir, cli_path, strict=True, allow_environment=False,
        )
    except RuntimeError as exc:
        return {
            "ok": False,
            "source": "unavailable",
            "error": str(exc),
            "model_files_valid": bool(model_dir and _valid_coreml_model_dir(Path(model_dir).expanduser())),
            "cli_valid": bool(cli_path and _valid_coreml_cli(Path(cli_path).expanduser())),
        }
    if runtime is None:
        return {
            "ok": False, "source": "unavailable",
            "error": "需要同时选择 Core ML 模型目录和 Parakeet CLI",
            "model_files_valid": False, "cli_valid": False,
        }
    return {
        "ok": True,
        "source": runtime.source,
        "error": "",
        "model_files_valid": True,
        "cli_valid": True,
    }


def _build_coreml_command(
    runtime: CoreMLRuntime,
    audio_path: str | Path,
    output_dir: str | Path,
    output_filename: str = "transcription",
) -> list[str]:
    return [
        str(runtime.cli_path),
        "--model", str(runtime.model_dir),
        "--input", str(Path(audio_path).resolve()),
        "--output-dir", str(Path(output_dir).resolve()),
        "--output-format", "json",
        "--output-filename", output_filename,
    ]


def _redact_runtime_paths(message: str, runtime: CoreMLRuntime) -> str:
    """Keep user-selected local paths out of persisted task logs."""
    return (
        str(message)
        .replace(str(runtime.model_dir), "<Core ML model>")
        .replace(str(runtime.cli_path), "<Parakeet CLI>")
    )


def _parse_coreml_status_line(line: str) -> dict | None:
    value = line.strip()
    if not value:
        return None
    try:
        event = json.loads(value)
        if isinstance(event, dict):
            return event
    except json.JSONDecodeError:
        pass
    match = _TEXT_PROGRESS.search(value)
    if match:
        return {"status": "progress", "progress": int(match.group(1)), "message": value}
    return {"status": "message", "message": value}


def _float_time(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _segments_from_coreml_json(payload: dict) -> tuple[list[ParakeetSegment], float]:
    """Turn Memo token timings into sentence/gap-bounded subtitle segments."""
    duration = max(0.0, _float_time(payload.get("duration"), 0.0))
    raw_timings = payload.get("tokenTimings")
    timings: list[dict] = []
    if isinstance(raw_timings, list):
        for item in raw_timings:
            if not isinstance(item, dict):
                continue
            token = str(item.get("token") or "")
            if not token:
                continue
            start = max(0.0, _float_time(item.get("startTime"), 0.0))
            end = max(start + 0.04, _float_time(item.get("endTime"), start + 0.04))
            timings.append({"token": token, "start": start, "end": end})

    if not timings:
        text = str(payload.get("text") or "").strip()
        if not text or _PUNCTUATION_ONLY.fullmatch(text):
            return [], duration
        end = duration if duration > 0 else max(1.0, len(text) / 12.0)
        return [ParakeetSegment(0.0, end, text)], max(duration, end)

    groups: list[list[dict]] = []
    current: list[dict] = []
    previous_start: float | None = None
    for timing in timings:
        # Token end times can bridge a long silence; consecutive token starts
        # provide a more reliable boundary for the Core ML JSON format.
        if current and previous_start is not None and timing["start"] - previous_start > 2.0:
            groups.append(current)
            current = []
        current.append(timing)
        previous_start = timing["start"]
        if timing["token"].rstrip()[-1:] in _SENTENCE_ENDINGS:
            groups.append(current)
            current = []
            previous_start = None
    if current:
        groups.append(current)

    segments: list[ParakeetSegment] = []
    for group in groups:
        text = "".join(item["token"] for item in group).strip()
        if not text or _PUNCTUATION_ONLY.fullmatch(text):
            continue
        start = group[0]["start"]
        last = group[-1]
        # A token end can be the next token start after many silent seconds.
        # Cap a final token's display tail while preserving normal durations.
        end = min(last["end"], last["start"] + 2.0)
        end = max(start + 0.08, end)
        if duration:
            end = min(duration, end)
        if end > start:
            segments.append(ParakeetSegment(start=start, end=end, text=text))
    return segments, duration


def _terminate_coreml_process(process: Any, timeout: float = 1.5) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout)


def _run_coreml_cli(
    task_id: str,
    audio_path: str,
    runtime: CoreMLRuntime,
    popen_factory: Callable[..., Any] | None = None,
) -> dict:
    """Run Memo's CLI while consuming progress and honoring cancellation."""
    popen_factory = popen_factory or subprocess.Popen
    task_manager.checkpoint(task_id)
    with tempfile.TemporaryDirectory(prefix="subtitle-factory-parakeet-coreml-") as folder:
        output_dir = Path(folder).resolve()
        output_filename = "transcription"
        expected_output = output_dir / f"{output_filename}.json"
        command = _build_coreml_command(runtime, audio_path, output_dir, output_filename)
        process = popen_factory(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        output_queue: queue.Queue[Any] = queue.Queue()
        finished_reading = object()
        lines: list[str] = []
        reported_output: Path | None = None

        def read_output() -> None:
            try:
                if process.stdout is not None:
                    for line in process.stdout:
                        output_queue.put(line)
            finally:
                output_queue.put(finished_reading)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        reader_done = False
        try:
            while True:
                task_manager.checkpoint(task_id)
                try:
                    item = output_queue.get(timeout=0.1)
                except queue.Empty:
                    item = None
                if item is finished_reading:
                    reader_done = True
                elif isinstance(item, str):
                    line = item.strip()
                    if line:
                        lines.append(line)
                        lines = lines[-80:]
                    event = _parse_coreml_status_line(item)
                    if event and event.get("status") == "progress":
                        progress = max(0, min(100, int(_float_time(event.get("progress"), 0))))
                        task_manager.update_task(
                            task_id,
                            step="transcribing",
                            progress=5 + progress * 0.75,
                            message=f"Parakeet Core ML 正在转写：{progress}%",
                            details={
                                "coreml": {
                                    "status": "running",
                                    "progress": progress,
                                    "source": runtime.source,
                                }
                            },
                        )
                    elif event and event.get("status") == "success" and event.get("result"):
                        reported_output = Path(str(event["result"])).expanduser().resolve()
                if reader_done and process.poll() is not None:
                    break

            return_code = process.wait(timeout=5)
        except TaskCancelled:
            _terminate_coreml_process(process)
            raise
        except Exception:
            _terminate_coreml_process(process)
            raise
        finally:
            if process.stdout is not None:
                process.stdout.close()
            reader.join(timeout=1)

        if return_code != 0:
            detail = "\n".join(lines[-12:]) or f"退出码 {return_code}"
            raise RuntimeError(f"Parakeet Core ML CLI 执行失败：{detail}")

        result_path = reported_output if reported_output and reported_output.is_file() else expected_output
        try:
            result_path.relative_to(output_dir)
        except ValueError as exc:
            raise RuntimeError("Parakeet Core ML CLI 返回了输出目录之外的文件") from exc
        if not result_path.is_file():
            detail = "\n".join(lines[-12:])
            raise RuntimeError(f"Parakeet Core ML CLI 未生成 JSON 结果：{detail}")
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"无法读取 Parakeet Core ML JSON 结果：{exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Parakeet Core ML JSON 结果格式无效")
        return payload


def _asset_paths(cache_root: Path) -> ParakeetAssets:
    model_dir = cache_root / PARAKEET_MODEL_DIR_NAME
    return ParakeetAssets(
        model_dir=model_dir,
        encoder=model_dir / "encoder.int8.onnx",
        decoder=model_dir / "decoder.int8.onnx",
        joiner=model_dir / "joiner.int8.onnx",
        tokens=model_dir / "tokens.txt",
        vad=cache_root / "silero_vad.onnx",
    )


def _model_files_are_valid(assets: ParakeetAssets) -> bool:
    paths = {
        "encoder.int8.onnx": assets.encoder,
        "decoder.int8.onnx": assets.decoder,
        "joiner.int8.onnx": assets.joiner,
        "tokens.txt": assets.tokens,
    }
    return all(
        path.is_file() and path.stat().st_size >= PARAKEET_REQUIRED_FILES[name]
        for name, path in paths.items()
    )


def _model_cache_is_valid(assets: ParakeetAssets) -> bool:
    return (
        _model_files_are_valid(assets)
        and assets.vad.is_file()
        and assets.vad.stat().st_size == SILERO_VAD_BYTES
    )


def get_parakeet_model_status(
    model_id: str = PARAKEET_ONNX_MODEL_ID,
    *,
    cache_root: Path | None = None,
    coreml_model_dir: str | Path | None = None,
    coreml_cli_path: str | Path | None = None,
) -> dict:
    """Return a source-aware status for model manager and diagnostics APIs."""
    if model_id == PARAKEET_MODEL_ID:
        try:
            runtime = discover_coreml_runtime(
                coreml_model_dir,
                coreml_cli_path,
                strict=False,
                allow_environment=(
                    coreml_model_dir is None
                    and coreml_cli_path is None
                    and environment_path_overrides_enabled()
                ),
            )
        except RuntimeError:
            runtime = None
        if runtime:
            return {
                "model_id": model_id,
                "ready": True,
                "source": runtime.source,
                "state": "ready",
                "download_required": False,
                "error": "",
            }
        validation = None
        if coreml_model_dir is not None or coreml_cli_path is not None:
            validation = validate_coreml_runtime_paths(coreml_model_dir, coreml_cli_path)
        return {
            "model_id": model_id,
            "ready": False,
            "source": "unavailable",
            "state": "unavailable",
            "download_required": False,
            "error": (validation or {}).get(
                "error", "未发现外部 Core ML 模型与配套 CLI",
            ),
        }
    if model_id != PARAKEET_ONNX_MODEL_ID:
        return {
            "model_id": model_id,
            "ready": False,
            "source": "unavailable",
            "state": "unavailable",
            "download_required": False,
            "error": "不支持的 Parakeet 模型",
        }
    assets = _asset_paths(Path(cache_root or MODELS_DIR).expanduser().resolve())
    ready = _model_cache_is_valid(assets)
    partial = assets.model_dir.exists() or assets.vad.exists()
    return {
        "model_id": model_id,
        "ready": ready,
        "source": "app_download" if ready or partial else "unavailable",
        "state": "ready" if ready else ("invalid" if partial else "not_downloaded"),
        "download_required": not ready,
        "download_bytes": PARAKEET_ARCHIVE_BYTES + SILERO_VAD_BYTES,
        "error": "" if ready else ("模型缓存不完整，可执行修复" if partial else "模型尚未下载"),
    }


def _format_mib(byte_count: int) -> str:
    return f"{byte_count / (1024 * 1024):.1f} MiB"


def _download_file(
    url: str,
    destination: Path,
    expected_size: int,
    progress_callback: Callable[[int, int, bool], None],
    checkpoint: Callable[[], None],
) -> None:
    """Download atomically with HTTP Range resume and cooperative cancellation."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f"{destination.name}.part")

    if destination.is_file() and destination.stat().st_size == expected_size:
        progress_callback(expected_size, expected_size, False)
        return
    if destination.exists():
        destination.unlink()

    existing = partial.stat().st_size if partial.is_file() else 0
    if existing > expected_size:
        partial.unlink()
        existing = 0
    if existing == expected_size:
        os.replace(partial, destination)
        progress_callback(expected_size, expected_size, True)
        return

    checkpoint()
    headers = {
        "Accept": "application/octet-stream",
        "User-Agent": "SubtitleFactory/0.1 sherpa-onnx-model-downloader",
    }
    if existing:
        headers["Range"] = f"bytes={existing}-"

    request = urllib.request.Request(url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=60)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"无法连接官方模型下载地址：{exc}") from exc

    try:
        status = getattr(response, "status", 200)
        resumed = bool(existing and status == 206)
        if existing and not resumed:
            existing = 0
        mode = "ab" if resumed else "wb"
        downloaded = existing
        progress_callback(downloaded, expected_size, resumed)
        with partial.open(mode) as output:
            while True:
                checkpoint()
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                progress_callback(downloaded, expected_size, resumed)
        checkpoint()
    except TaskCancelled:
        # Keep the .part file so the next task can resume this large download.
        raise
    except OSError as exc:
        raise RuntimeError(f"写入模型缓存失败（请检查磁盘空间）：{exc}") from exc
    finally:
        response.close()

    actual_size = partial.stat().st_size if partial.is_file() else 0
    if actual_size != expected_size:
        raise RuntimeError(
            "模型下载不完整："
            f"收到 {_format_mib(actual_size)}，预期 {_format_mib(expected_size)}；"
            "可再次开始任务以断点续传"
        )
    os.replace(partial, destination)


def _safe_extract_tar(
    archive: Path,
    destination: Path,
    checkpoint: Callable[[], None] | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> None:
    """Extract a bzip2 tar safely and remain cancellable during large files."""
    checkpoint = checkpoint or (lambda: None)
    progress_callback = progress_callback or (lambda _done, _total: None)
    destination_resolved = destination.resolve()
    extracted_bytes = 0
    # Stream mode avoids a full, non-cancellable scan of the 465 MiB bzip2 file.
    with tarfile.open(archive, mode="r|bz2") as bundle:
        for member in bundle:
            checkpoint()
            if member.issym() or member.islnk() or member.isdev():
                raise RuntimeError(f"模型压缩包包含不安全条目：{member.name}")
            target = (destination / member.name).resolve()
            try:
                target.relative_to(destination_resolved)
            except ValueError as exc:
                raise RuntimeError(f"模型压缩包路径越界：{member.name}") from exc
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise RuntimeError(f"模型压缩包包含不支持的条目：{member.name}")
            source = bundle.extractfile(member)
            if source is None:
                raise RuntimeError(f"无法读取模型压缩包条目：{member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, target.open("wb") as output:
                while True:
                    checkpoint()
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    extracted_bytes += len(chunk)
                    progress_callback(extracted_bytes, PARAKEET_EXTRACTED_ESTIMATE_BYTES)


def _install_model_archive(
    archive: Path,
    cache_root: Path,
    checkpoint: Callable[[], None] | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> ParakeetAssets:
    assets = _asset_paths(cache_root)
    staging = cache_root / f".{PARAKEET_MODEL_DIR_NAME}.extracting"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        _safe_extract_tar(archive, staging, checkpoint, progress_callback)
        source = staging / PARAKEET_MODEL_DIR_NAME
        if not source.is_dir():
            candidates = [path for path in staging.iterdir() if path.is_dir()]
            if len(candidates) != 1:
                raise RuntimeError("官方模型压缩包目录结构不符合预期")
            source = candidates[0]
        if assets.model_dir.exists():
            shutil.rmtree(assets.model_dir)
        os.replace(source, assets.model_dir)
    except tarfile.ReadError as exc:
        archive.unlink(missing_ok=True)
        raise RuntimeError("模型压缩包已损坏，请重新开始任务下载") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return _asset_paths(cache_root)


def ensure_parakeet_assets(
    task_id: str,
    cache_root: Path | None = None,
    *,
    repair: bool = False,
) -> ParakeetAssets:
    """Return complete cached assets, downloading official files on first use."""
    root = Path(cache_root or MODELS_DIR).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    assets = _asset_paths(root)
    if _model_cache_is_valid(assets) and not repair:
        task_manager.update_task(
            task_id,
            details={
                "model_download": {
                    "status": "cached",
                    "model_id": PARAKEET_ONNX_MODEL_ID,
                    "cache_dir": str(assets.model_dir),
                }
            },
        )
        return assets

    with _DOWNLOAD_LOCK:
        assets = _asset_paths(root)
        if _model_cache_is_valid(assets) and not repair:
            return assets

        archive = root / f"{PARAKEET_MODEL_DIR_NAME}.tar.bz2"
        if repair:
            task_manager.update_task(
                task_id,
                step="repairing_model",
                progress=1,
                message="正在清理 Parakeet 模型缓存并重新校验...",
                details={
                    "model_download": {
                        "status": "repairing",
                        "model_id": PARAKEET_ONNX_MODEL_ID,
                    }
                },
            )
            shutil.rmtree(assets.model_dir, ignore_errors=True)
            shutil.rmtree(root / f".{PARAKEET_MODEL_DIR_NAME}.extracting", ignore_errors=True)
            assets.vad.unlink(missing_ok=True)
            archive.unlink(missing_ok=True)
            archive.with_name(f"{archive.name}.part").unlink(missing_ok=True)
            assets.vad.with_name(f"{assets.vad.name}.part").unlink(missing_ok=True)
            assets = _asset_paths(root)
        last_reported_percent = -1
        model_files_ready = _model_files_are_valid(assets)
        if not model_files_ready:
            task_manager.update_task(
                task_id,
                step="downloading_model",
                progress=2,
                message="首次使用 Parakeet：正在下载官方 int8 模型（约 465 MiB）...",
                details={
                    "model_download": {
                        "status": "downloading",
                        "model_id": PARAKEET_ONNX_MODEL_ID,
                        "downloaded_bytes": 0,
                        "total_bytes": PARAKEET_ARCHIVE_BYTES,
                    }
                },
            )
            task_manager.add_log(
                task_id,
                "info",
                "Parakeet 模型",
                "首次使用需从 k2-fsa 官方发布页获取模型，后续将直接使用本地缓存",
                detail="下载约 465 MiB，解压后约占 640 MiB",
            )

        def report_model_progress(downloaded: int, total: int, resumed: bool) -> None:
            nonlocal last_reported_percent
            percent = min(100, int(downloaded * 100 / max(total, 1)))
            if percent == last_reported_percent and downloaded < total:
                return
            last_reported_percent = percent
            resume_text = "（断点续传）" if resumed and downloaded < total else ""
            task_manager.update_task(
                task_id,
                step="downloading_model",
                progress=2 + percent * 0.16,
                message=(
                    f"正在下载 Parakeet 模型{resume_text}：{percent}% · "
                    f"{_format_mib(downloaded)} / {_format_mib(total)}"
                ),
                details={
                    "model_download": {
                        "status": "downloading",
                        "model_id": PARAKEET_ONNX_MODEL_ID,
                        "downloaded_bytes": downloaded,
                        "total_bytes": total,
                        "resumed": resumed,
                    }
                },
            )

        try:
            if not model_files_ready:
                _download_file(
                    PARAKEET_ARCHIVE_URL,
                    archive,
                    PARAKEET_ARCHIVE_BYTES,
                    report_model_progress,
                    lambda: task_manager.checkpoint(task_id),
                )
                task_manager.checkpoint(task_id)
                task_manager.update_task(
                    task_id,
                    step="extracting_model",
                    progress=19,
                    message="Parakeet 模型下载完成，正在校验并解压...",
                    details={
                        "model_download": {
                            "status": "extracting",
                            "model_id": PARAKEET_ONNX_MODEL_ID,
                            "downloaded_bytes": PARAKEET_ARCHIVE_BYTES,
                            "total_bytes": PARAKEET_ARCHIVE_BYTES,
                        }
                    },
                )
                last_extract_percent = -1

                def report_extract_progress(extracted: int, estimate: int) -> None:
                    nonlocal last_extract_percent
                    percent = min(100, int(extracted * 100 / max(estimate, 1)))
                    if percent == last_extract_percent:
                        return
                    last_extract_percent = percent
                    task_manager.update_task(
                        task_id,
                        step="extracting_model",
                        progress=19 + percent * 0.03,
                        message=f"正在解压 Parakeet 模型：{percent}%",
                        details={
                            "model_download": {
                                "status": "extracting",
                                "model_id": PARAKEET_ONNX_MODEL_ID,
                                "extracted_bytes": extracted,
                                "estimated_extracted_bytes": estimate,
                            }
                        },
                    )

                assets = _install_model_archive(
                    archive,
                    root,
                    lambda: task_manager.checkpoint(task_id),
                    report_extract_progress,
                )
                if not _model_files_are_valid(assets):
                    raise RuntimeError("模型解压校验失败，缓存不完整")
                archive.unlink(missing_ok=True)
                task_manager.checkpoint(task_id)

            task_manager.update_task(
                task_id,
                step="downloading_model",
                progress=22,
                message="正在准备 Parakeet 语音检测组件...",
            )

            def report_vad_progress(downloaded: int, total: int, _resumed: bool) -> None:
                task_manager.update_task(
                    task_id,
                    step="downloading_model",
                    progress=22 + min(1.5, downloaded / max(total, 1) * 1.5),
                    message="正在准备 Parakeet 语音检测组件...",
                )

            _download_file(
                SILERO_VAD_URL,
                assets.vad,
                SILERO_VAD_BYTES,
                report_vad_progress,
                lambda: task_manager.checkpoint(task_id),
            )
            assets = _asset_paths(root)
            if not _model_cache_is_valid(assets):
                raise RuntimeError("模型文件校验失败，缓存不完整")
        except TaskCancelled:
            raise
        except Exception as exc:
            task_manager.update_task(
                task_id,
                step="model_error",
                message="Parakeet 模型准备失败",
                details={
                    "model_download": {
                        "status": "error",
                        "model_id": PARAKEET_ONNX_MODEL_ID,
                        "error": str(exc),
                    }
                },
            )
            task_manager.add_log(
                task_id,
                "error",
                "Parakeet 模型",
                "模型准备失败",
                detail=str(exc),
                suggestion="请检查网络和磁盘空间后重试；未完成文件会用于断点续传",
            )
            raise RuntimeError(f"Parakeet 模型准备失败：{exc}") from exc

        task_manager.update_task(
            task_id,
            step="loading_model",
            progress=24,
            message="Parakeet 模型已缓存，正在加载推理引擎...",
            details={
                "model_download": {
                    "status": "ready",
                    "model_id": PARAKEET_ONNX_MODEL_ID,
                    "cache_dir": str(assets.model_dir),
                }
            },
        )
        task_manager.add_log(
            task_id,
            "info",
            "Parakeet 模型",
            "官方 int8 模型已就绪并写入本地缓存",
        )
        return assets


def prepare_parakeet_model(
    task_id: str,
    model_id: str = PARAKEET_ONNX_MODEL_ID,
    *,
    repair: bool = False,
    cache_root: Path | None = None,
    coreml_model_dir: str | Path | None = None,
    coreml_cli_path: str | Path | None = None,
) -> dict:
    """Prepare/repair a model for the model-manager background endpoint."""
    if model_id == PARAKEET_MODEL_ID:
        status = get_parakeet_model_status(
            model_id,
            coreml_model_dir=coreml_model_dir,
            coreml_cli_path=coreml_cli_path,
        )
        if not status["ready"]:
            raise RuntimeError(status["error"])
        task_manager.update_task(
            task_id,
            step="model_ready",
            progress=100,
            message="外部 Core ML 模型已通过校验",
            details={"model_status": status},
        )
        return status
    if model_id != PARAKEET_ONNX_MODEL_ID:
        raise ValueError(f"不支持的 Parakeet 模型：{model_id}")
    ensure_parakeet_assets(task_id, cache_root, repair=repair)
    status = get_parakeet_model_status(model_id, cache_root=cache_root)
    task_manager.update_task(
        task_id,
        step="model_ready",
        progress=100,
        message="Parakeet ONNX 模型已准备完成",
        details={"model_status": status},
    )
    return status


def _import_sherpa_onnx() -> Any:
    try:
        import sherpa_onnx
    except ImportError as exc:
        raise RuntimeError(
            "缺少 Parakeet 推理组件 sherpa-onnx；请重新安装应用或运行 pip install -r backend/requirements.txt"
        ) from exc
    return sherpa_onnx


def _wave_info(audio_path: str) -> tuple[float, int, int]:
    try:
        with wave.open(audio_path, "rb") as audio:
            channels = audio.getnchannels()
            sample_width = audio.getsampwidth()
            sample_rate = audio.getframerate()
            frame_count = audio.getnframes()
            compression = audio.getcomptype()
    except (OSError, wave.Error) as exc:
        raise RuntimeError(f"无法读取待转写 WAV 音频：{exc}") from exc
    if channels != 1 or sample_width != 2 or compression != "NONE":
        raise RuntimeError("Parakeet 需要 16-bit PCM 单声道 WAV，请重新提取音频")
    if sample_rate != 16000:
        raise RuntimeError("Parakeet 字幕模式需要 16kHz 音频，请重新提取音频")
    return frame_count / max(sample_rate, 1), sample_rate, frame_count


def _iter_vad_segments(
    task_id: str,
    audio_path: str,
    recognizer: Any,
    vad_path: Path,
    sherpa_onnx: Any,
    audio_duration: float,
) -> Iterator[ParakeetSegment]:
    """Follow sherpa-onnx's official VAD subtitle flow, yielding incrementally."""
    import numpy as np

    config = sherpa_onnx.VadModelConfig()
    config.silero_vad.model = str(vad_path)
    config.silero_vad.threshold = 0.2
    config.silero_vad.min_silence_duration = 0.25
    config.silero_vad.min_speech_duration = 0.25
    config.silero_vad.max_speech_duration = 8.0
    config.sample_rate = 16000
    window_size = int(config.silero_vad.window_size)
    vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=100)

    pending = np.empty(0, dtype=np.float32)
    with wave.open(audio_path, "rb") as audio:
        frames_per_read = 16000 * 2
        while True:
            task_manager.checkpoint(task_id)
            raw = audio.readframes(frames_per_read)
            is_eof = not raw
            if raw:
                samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
                pending = np.concatenate((pending, samples))
                while pending.size >= window_size:
                    vad.accept_waveform(pending[:window_size])
                    pending = pending[window_size:]
            else:
                if pending.size:
                    padded = np.zeros(window_size, dtype=np.float32)
                    padded[: pending.size] = pending
                    vad.accept_waveform(padded)
                    pending = np.empty(0, dtype=np.float32)
                vad.flush()

            while not vad.empty():
                task_manager.checkpoint(task_id)
                speech = vad.front
                start = float(speech.start) / 16000.0
                speech_samples = np.asarray(speech.samples, dtype=np.float32).copy()
                vad.pop()

                stream = recognizer.create_stream()
                stream.accept_waveform(16000, speech_samples)
                recognizer.decode_stream(stream)
                task_manager.checkpoint(task_id)
                text = str(stream.result.text or "").strip()
                if not text or _PUNCTUATION_ONLY.fullmatch(text):
                    continue
                end = min(audio_duration, start + speech_samples.size / 16000.0)
                if end > start:
                    yield ParakeetSegment(start=start, end=end, text=text)

            if is_eof:
                break


def _create_onnx_session(
    task_id: str,
    audio_path: str,
    normalized_language: str,
) -> ParakeetSession:
    assets = ensure_parakeet_assets(task_id)
    task_manager.checkpoint(task_id)
    task_manager.update_task(
        task_id,
        step="loading_model",
        progress=24,
        message="正在加载 Parakeet int8 ONNX 模型...",
    )
    try:
        sherpa_onnx = _import_sherpa_onnx()
        thread_default = max(1, min(4, os.cpu_count() or 2))
        num_threads = max(1, int(os.getenv("PARAKEET_NUM_THREADS", str(thread_default))))
        recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(assets.encoder),
            decoder=str(assets.decoder),
            joiner=str(assets.joiner),
            tokens=str(assets.tokens),
            model_type="nemo_transducer",
            num_threads=num_threads,
            provider="cpu",
            decoding_method="greedy_search",
            sample_rate=16000,
            feature_dim=80,
            debug=False,
        )
        audio_duration, _, _ = _wave_info(audio_path)
    except TaskCancelled:
        raise
    except Exception as exc:
        task_manager.update_task(
            task_id,
            step="model_error",
            message="Parakeet 推理引擎加载失败",
        )
        task_manager.add_log(
            task_id,
            "error",
            "Parakeet 模型",
            "推理引擎加载失败",
            detail=str(exc),
            suggestion="请重新安装应用；若缓存损坏，可删除数据目录中的 models 后重试",
        )
        raise RuntimeError(f"Parakeet 推理引擎加载失败：{exc}") from exc

    segments = _iter_vad_segments(
        task_id,
        audio_path,
        recognizer,
        assets.vad,
        sherpa_onnx,
        audio_duration,
    )
    detected_language = normalized_language if normalized_language != "auto" else "auto"
    return ParakeetSession(
        segments=segments,
        audio_duration=audio_duration,
        detected_language=detected_language,
    )


def _create_coreml_session(
    task_id: str,
    audio_path: str,
    normalized_language: str,
    runtime: CoreMLRuntime,
) -> ParakeetSession:
    task_manager.update_task(
        task_id,
        step="loading_model",
        progress=3,
        message="正在加载外部 Parakeet Core ML 模型...",
        details={
            "coreml": {
                "status": "ready",
                "source": runtime.source,
            },
            "model_download": {
                "status": "external_runtime",
                "model_id": PARAKEET_MODEL_ID,
            },
        },
    )
    task_manager.add_log(
        task_id,
        "info",
        "Parakeet Core ML",
        "已加载外部 Core ML 模型与 CLI",
    )
    try:
        payload = _run_coreml_cli(task_id, audio_path, runtime)
        segments, audio_duration = _segments_from_coreml_json(payload)
        if audio_duration <= 0:
            audio_duration, _, _ = _wave_info(audio_path)
    except TaskCancelled:
        raise
    except Exception as exc:
        safe_error = _redact_runtime_paths(str(exc), runtime)
        task_manager.update_task(
            task_id,
            step="model_error",
            message="Parakeet Core ML 转写失败",
            details={"coreml": {"status": "error", "error": safe_error}},
        )
        task_manager.add_log(
            task_id,
            "error",
            "Parakeet Core ML",
            "本地 Core ML 转写失败",
            detail=safe_error,
            suggestion="请重新选择完整的 Core ML 模型目录与兼容当前架构的 CLI",
        )
        raise RuntimeError(f"Parakeet Core ML 转写失败：{safe_error}") from exc

    task_manager.update_task(
        task_id,
        step="transcribing",
        progress=82,
        message=f"Parakeet Core ML 识别完成，正在导入 {len(segments)} 条时间轴字幕...",
        details={
            "coreml": {
                "status": "success",
                "progress": 100,
                "segments": len(segments),
                "source": runtime.source,
            }
        },
    )
    detected_language = normalized_language if normalized_language != "auto" else "auto"
    return ParakeetSession(
        segments=iter(segments),
        audio_duration=audio_duration,
        detected_language=detected_language,
        device="Core ML",
        compute_type="Core ML",
        model_label=PARAKEET_COREML_DISPLAY_NAME,
        progress_start=82.0,
    )


def create_parakeet_session(
    task_id: str,
    audio_path: str,
    language: str = "auto",
    model_id: str = PARAKEET_MODEL_ID,
    coreml_model_dir: str | Path | None = None,
    coreml_cli_path: str | Path | None = None,
) -> ParakeetSession:
    normalized_language = (language or "auto").lower()
    if normalized_language != "auto" and normalized_language not in PARAKEET_SUPPORTED_LANGUAGES:
        raise ValueError(
            "Parakeet TDT v3 不支持所选源语言；请选择自动检测或其支持的欧洲语言（例如英语）"
        )
    if model_id not in {PARAKEET_MODEL_ID, PARAKEET_ONNX_MODEL_ID}:
        raise ValueError(f"不支持的 Parakeet 模型：{model_id}")

    if model_id == PARAKEET_MODEL_ID:
        runtime = discover_coreml_runtime(
            coreml_model_dir,
            coreml_cli_path,
            strict=False,
            allow_environment=(
                coreml_model_dir is None
                and coreml_cli_path is None
                and environment_path_overrides_enabled()
            ),
        )
        if runtime is None:
            error = RuntimeError("外部 Parakeet Core ML 模型或 CLI 当前不可用")
            error.error_code = "MODEL_RUNTIME_MISSING"
            error.recoverable = True
            error.available_actions = ["choose_fallback", "open_settings"]
            error.suggestion = "请选择 Whisper Small，或重新选择完整的 Core ML 模型与 CLI"
            raise error
        return _create_coreml_session(
            task_id, audio_path, normalized_language, runtime
        )
    return _create_onnx_session(task_id, audio_path, normalized_language)
