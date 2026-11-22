"""Parakeet Core ML runtime discovery, validation, and CLI runner."""

from __future__ import annotations

import json
import math
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

from ...utils.config import MODELS_DIR, environment_path_overrides_enabled
from ...utils.task_manager import TaskCancelled, task_manager
from ..runtime_diagnostics import bundled_executable, validate_runtime_executable
from .constants import (
    _COREML_REQUIRED_MODEL_ENTRIES,
    _PUNCTUATION_ONLY,
    _SENTENCE_ENDINGS,
    _TEXT_PROGRESS,
    PARAKEET_COREML_CLI_ENV,
    PARAKEET_COREML_CLI_NAME,
    PARAKEET_COREML_MODEL_ENV,
    PARAKEET_MODEL_ID,
    CoreMLRuntime,
    ParakeetSegment,
    facade_attr,
)


def managed_coreml_model_dir(cache_root: Path | None = None) -> Path:
    return Path(cache_root or MODELS_DIR).expanduser() / PARAKEET_MODEL_ID


def bundled_coreml_cli() -> Path | None:
    return bundled_executable(PARAKEET_COREML_CLI_NAME)


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
    cache_root: Path | None = None,
) -> CoreMLRuntime | None:
    """Find a Core ML model plus runner without embedding a user-specific path."""
    if allow_environment is None:
        allow_environment = environment_path_overrides_enabled()
    environment_model = os.getenv(PARAKEET_COREML_MODEL_ENV) if allow_environment else None
    environment_cli = os.getenv(PARAKEET_COREML_CLI_ENV) if allow_environment else None
    explicit_model = model_dir if model_dir is not None else environment_model
    explicit_cli = cli_path if cli_path is not None else environment_cli
    source = (
        "custom_path" if model_dir is not None or cli_path is not None else "external_detected"
    )
    valid_cli_fn = facade_attr("_valid_coreml_cli", _valid_coreml_cli)
    bundled_cli_fn = facade_attr("bundled_coreml_cli", bundled_coreml_cli)
    path_cls = facade_attr("Path", Path)

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
        if not valid_cli_fn(resolved_cli):
            if strict:
                raise RuntimeError("所选 Parakeet CLI 不可执行或与当前架构不兼容")
            return None
    else:
        resolved_cli = None

    managed_model = False
    bundled_cli = False
    if resolved_model is None:
        candidate = managed_coreml_model_dir(cache_root)
        if _valid_coreml_model_dir(candidate):
            resolved_model = candidate.resolve()
            managed_model = True
    if resolved_cli is None:
        candidate = bundled_cli_fn()
        if candidate is not None and valid_cli_fn(candidate):
            resolved_cli = candidate.resolve()
            bundled_cli = True
    if managed_model and bundled_cli and source == "external_detected":
        source = "app_managed"

    if sys.platform == "darwin":
        memo_root = path_cls.home() / "Library" / "Application Support" / "Memo"
        if resolved_model is None:
            candidate = memo_root / "models" / PARAKEET_MODEL_ID
            if _valid_coreml_model_dir(candidate):
                resolved_model = candidate.resolve()
        if resolved_cli is None:
            candidate = memo_root / "plugins" / "parakeet-cli" / "parakeet"
            if valid_cli_fn(candidate):
                resolved_cli = candidate.resolve()

    if resolved_cli is None and allow_environment:
        executable = shutil.which("parakeet")
        if executable and valid_cli_fn(Path(executable)):
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
    discover_fn = facade_attr("discover_coreml_runtime", discover_coreml_runtime)
    valid_cli_fn = facade_attr("_valid_coreml_cli", _valid_coreml_cli)
    try:
        runtime = discover_fn(
            model_dir,
            cli_path,
            strict=True,
            allow_environment=False,
        )
    except RuntimeError as exc:
        return {
            "ok": False,
            "source": "unavailable",
            "error": str(exc),
            "model_files_valid": bool(
                model_dir and _valid_coreml_model_dir(Path(model_dir).expanduser())
            ),
            "cli_valid": bool(cli_path and valid_cli_fn(Path(cli_path).expanduser())),
        }
    if runtime is None:
        return {
            "ok": False,
            "source": "unavailable",
            "error": "需要同时选择 Core ML 模型目录和 Parakeet CLI",
            "model_files_valid": False,
            "cli_valid": False,
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
        "--model",
        str(runtime.model_dir),
        "--input",
        str(Path(audio_path).resolve()),
        "--output-dir",
        str(Path(output_dir).resolve()),
        "--output-format",
        "json",
        "--output-filename",
        output_filename,
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
            segment_timings = tuple(
                {"text": item["token"], "start": item["start"], "end": item["end"]}
                for item in group
            )
            segments.append(
                ParakeetSegment(start=start, end=end, text=text, timings=segment_timings)
            )
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
    tm = facade_attr("task_manager", task_manager)
    popen_factory = popen_factory or subprocess.Popen
    tm.checkpoint(task_id)
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
                tm.checkpoint(task_id)
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
                        tm.update_task(
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

        result_path = (
            reported_output if reported_output and reported_output.is_file() else expected_output
        )
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
