"""Verified downloads and long-audio inference for managed sherpa-onnx models."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tarfile
import threading
import urllib.error
import urllib.request
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from ..utils.config import MODELS_DIR
from ..utils.task_manager import TaskCancelled, task_manager
from .parakeet_transcriber import (
    SILERO_VAD_BYTES,
    SILERO_VAD_SHA256,
    SILERO_VAD_URL,
)
from .sherpa_catalog import (
    MANAGED_SHERPA_BY_ID,
    ManagedModelFile,
    ManagedSherpaModel,
)


_MANIFEST_NAME = ".subtitle-factory-manifest.json"
_DOWNLOAD_LOCK = threading.Lock()
_PUNCTUATION_ONLY = re.compile(r"^[\W_]+$", re.UNICODE)


class ManagedModelError(RuntimeError):
    def __init__(
        self,
        message: str,
        error_code: str,
        suggestion: str,
        *,
        recoverable: bool = True,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.suggestion = suggestion
        self.recoverable = recoverable
        self.available_actions = ["retry", "repair"] if recoverable else []


@dataclass(frozen=True)
class ManagedSegment:
    start: float
    end: float
    text: str
    timings: tuple[dict, ...] = ()


@dataclass(frozen=True)
class ManagedSession:
    segments: Iterator[ManagedSegment]
    audio_duration: float
    detected_language: str
    device: str
    compute_type: str
    model_label: str
    progress_start: float = 25.0


def managed_model_dir(model_id: str) -> Path:
    return MODELS_DIR / "sherpa-onnx" / model_id


def _staging_dir(model_id: str) -> Path:
    return managed_model_dir(model_id).with_name(f".{model_id}.downloading")


def _manifest_identity(definition: ManagedSherpaModel) -> dict:
    return {
        "schema": 1,
        "model_id": definition.id,
        "package": definition.package,
        "archive_size": definition.archive_size,
        "archive_sha256": definition.archive_sha256,
        "asset_id": definition.asset_id,
        "asset_updated_at": definition.asset_updated_at,
        "files": [
            {"name": item.name, "size": item.size, "sha256": item.sha256}
            for item in definition.files
        ],
    }


def _read_manifest(path: Path) -> dict | None:
    try:
        return json.loads((path / _MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _sha256(path: Path, checkpoint: Callable[[], None] = lambda: None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            checkpoint()
            chunk = source.read(4 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _verify_file(
    path: Path,
    item: ManagedModelFile,
    checkpoint: Callable[[], None] = lambda: None,
) -> None:
    if not path.is_file() or path.stat().st_size != item.size:
        raise ManagedModelError(
            f"模型文件大小不匹配：{item.name}",
            "MODEL_INTEGRITY_FAILED",
            "请执行修复；原有可用模型仍然保留",
        )
    if _sha256(path, checkpoint) != item.sha256:
        raise ManagedModelError(
            f"模型文件 SHA-256 校验失败：{item.name}",
            "MODEL_INTEGRITY_FAILED",
            "请执行修复；原有可用模型仍然保留",
        )


def managed_model_ready(model_id: str) -> bool:
    definition = MANAGED_SHERPA_BY_ID.get(model_id)
    if not definition or not definition.files or not definition.archive_sha256:
        return False
    root = managed_model_dir(model_id)
    return (
        root.is_dir()
        and _read_manifest(root) == _manifest_identity(definition)
        and all(
            (root / item.name).is_file()
            and (root / item.name).stat().st_size == item.size
            for item in definition.files
        )
    )


def managed_model_status(model_id: str, *, deep: bool = False) -> dict:
    definition = MANAGED_SHERPA_BY_ID.get(model_id)
    if not definition:
        return {
            "model_id": model_id,
            "ready": False,
            "source": "unavailable",
            "state": "unavailable",
            "download_required": False,
            "error": "不支持的托管模型",
        }
    ready = managed_model_ready(model_id)
    error = ""
    if ready and deep:
        try:
            for item in definition.files:
                _verify_file(managed_model_dir(model_id) / item.name, item)
        except ManagedModelError as exc:
            ready = False
            error = str(exc)
    root = managed_model_dir(model_id)
    partial = root.exists() or _staging_dir(model_id).exists()
    return {
        "model_id": model_id,
        "ready": ready,
        "source": "app_download" if ready or partial else "github",
        "state": "ready" if ready else ("invalid" if partial else "not_downloaded"),
        "download_required": not ready,
        "download_bytes": definition.archive_size + (
            0 if (MODELS_DIR / "silero_vad.onnx").is_file() else SILERO_VAD_BYTES
        ),
        "installed_bytes": definition.installed_bytes,
        "error": error or (
            "" if ready else ("模型缓存不完整，可执行修复" if partial else "模型尚未下载")
        ),
    }


def _download_verified_file(
    *,
    url: str,
    destination: Path,
    expected_size: int,
    expected_sha256: str,
    checkpoint: Callable[[], None],
    report: Callable[[int, int, bool], None],
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f"{destination.name}.part")
    if destination.is_file() and destination.stat().st_size == expected_size:
        if _sha256(destination, checkpoint) == expected_sha256:
            report(expected_size, expected_size, False)
            return
        destination.unlink(missing_ok=True)
    existing = partial.stat().st_size if partial.is_file() else 0
    if existing > expected_size:
        partial.unlink()
        existing = 0
    headers = {
        "Accept": "application/octet-stream",
        "User-Agent": "SubtitleFactory/0.3.2 verified-model-downloader",
    }
    if existing:
        headers["Range"] = f"bytes={existing}-"
    checkpoint()
    try:
        response = urllib.request.urlopen(
            urllib.request.Request(url, headers=headers), timeout=120,
        )
    except urllib.error.HTTPError as exc:
        code = (
            "MODEL_SOURCE_CHANGED"
            if exc.code in {404, 410}
            else "MODEL_NETWORK_UNREACHABLE"
        )
        raise ManagedModelError(
            f"无法获取模型文件：HTTP {exc.code}",
            code,
            "请检查网络；固定来源失效时请更新字幕工厂",
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ManagedModelError(
            f"无法连接模型来源：{exc}",
            "MODEL_NETWORK_UNREACHABLE",
            "请检查网络或代理后重试；已下载部分可断点续传",
        ) from exc
    try:
        resumed = bool(existing and getattr(response, "status", 200) == 206)
        if existing and not resumed:
            existing = 0
        downloaded = existing
        report(downloaded, expected_size, resumed)
        with partial.open("ab" if resumed else "wb") as output:
            while True:
                checkpoint()
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                report(downloaded, expected_size, resumed)
    except TaskCancelled:
        raise
    except OSError as exc:
        raise ManagedModelError(
            f"写入模型缓存失败：{exc}",
            "MODEL_DISK_SPACE_INSUFFICIENT",
            "请释放磁盘空间后重试；已下载部分会保留",
        ) from exc
    finally:
        response.close()
    if not partial.is_file() or partial.stat().st_size != expected_size:
        raise ManagedModelError(
            "模型下载不完整",
            "MODEL_NETWORK_UNREACHABLE",
            "请重试，下载器会从现有进度继续",
        )
    os.replace(partial, destination)
    if _sha256(destination, checkpoint) != expected_sha256:
        destination.unlink(missing_ok=True)
        raise ManagedModelError(
            "模型压缩包 SHA-256 校验失败",
            "MODEL_INTEGRITY_FAILED",
            "请执行修复；现有可用模型不会被删除",
        )


def _safe_extract_required(
    archive: Path,
    destination: Path,
    definition: ManagedSherpaModel,
    checkpoint: Callable[[], None],
    report: Callable[[int, int], None],
) -> None:
    required = {item.name: item for item in definition.files}
    extracted = 0
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, mode="r|bz2") as bundle:
        for member in bundle:
            checkpoint()
            raw_path = Path(member.name)
            parts = raw_path.parts
            if raw_path.is_absolute() or ".." in parts:
                raise ManagedModelError(
                    f"模型压缩包包含越界路径：{member.name}",
                    "MODEL_INTEGRITY_FAILED",
                    "请更新字幕工厂的模型来源清单",
                    recoverable=False,
                )
            if member.issym() or member.islnk() or member.isdev():
                raise ManagedModelError(
                    f"模型压缩包包含不安全条目：{member.name}",
                    "MODEL_INTEGRITY_FAILED",
                    "请更新字幕工厂的模型来源清单",
                    recoverable=False,
                )
            if parts and parts[0] != definition.package:
                raise ManagedModelError(
                    f"模型压缩包根目录不匹配：{member.name}",
                    "MODEL_SOURCE_CHANGED",
                    "官方模型结构已变化，请更新字幕工厂",
                    recoverable=False,
                )
            relative = Path(*parts[1:]).as_posix() if len(parts) > 1 else ""
            item = required.get(relative)
            if item is None:
                continue
            source = bundle.extractfile(member)
            if source is None:
                raise ManagedModelError(
                    f"无法读取模型文件：{relative}",
                    "MODEL_INTEGRITY_FAILED",
                    "请重新下载模型",
                )
            target = (destination / relative).resolve()
            try:
                target.relative_to(destination.resolve())
            except ValueError as exc:
                raise ManagedModelError(
                    "模型压缩包路径越界",
                    "MODEL_INTEGRITY_FAILED",
                    "请更新字幕工厂的模型来源清单",
                    recoverable=False,
                ) from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, target.open("wb") as output:
                while True:
                    checkpoint()
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    extracted += len(chunk)
                    report(extracted, definition.installed_bytes)
    missing = [item.name for item in definition.files if not (destination / item.name).is_file()]
    if missing:
        raise ManagedModelError(
            f"模型压缩包缺少必要文件：{', '.join(missing)}",
            "MODEL_SOURCE_CHANGED",
            "官方模型结构已变化，请更新字幕工厂",
            recoverable=False,
        )


def _write_manifest(path: Path, definition: ManagedSherpaModel) -> None:
    temporary = path / f"{_MANIFEST_NAME}.{uuid.uuid4().hex}.tmp"
    temporary.write_text(
        json.dumps(
            _manifest_identity(definition),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    os.replace(temporary, path / _MANIFEST_NAME)


def _ensure_vad(task_id: str) -> Path:
    destination = MODELS_DIR / "silero_vad.onnx"
    _download_verified_file(
        url=SILERO_VAD_URL,
        destination=destination,
        expected_size=SILERO_VAD_BYTES,
        expected_sha256=SILERO_VAD_SHA256,
        checkpoint=lambda: task_manager.checkpoint(task_id),
        report=lambda *_args: None,
    )
    return destination


def prepare_managed_model(
    task_id: str,
    model_id: str,
    *,
    repair: bool = False,
) -> dict:
    definition = MANAGED_SHERPA_BY_ID.get(model_id)
    if not definition:
        raise ManagedModelError(
            "此模型不支持由 App 下载",
            "MODEL_PREPARE_UNSUPPORTED",
            "请选择模型目录中的托管模型",
            recoverable=False,
        )
    if not repair and managed_model_ready(model_id):
        return managed_model_status(model_id)
    if not definition.files or not definition.archive_sha256:
        raise ManagedModelError(
            "模型来源清单不完整",
            "MODEL_SOURCE_CHANGED",
            "请更新字幕工厂",
            recoverable=False,
        )
    final = managed_model_dir(model_id)
    archive = MODELS_DIR / ".downloads" / definition.archive_name
    with _DOWNLOAD_LOCK:
        if not repair and managed_model_ready(model_id):
            return managed_model_status(model_id)
        final.parent.mkdir(parents=True, exist_ok=True)
        partial = archive.with_name(f"{archive.name}.part")
        downloaded_before = partial.stat().st_size if partial.is_file() else 0
        required_free = max(
            0,
            definition.archive_size - downloaded_before,
        ) + definition.installed_bytes + 256 * 1024 * 1024
        if shutil.disk_usage(final.parent).free < required_free:
            raise ManagedModelError(
                "模型下载空间不足",
                "MODEL_DISK_SPACE_INSUFFICIENT",
                f"至少需要额外 {required_free / 1024 ** 3:.1f} GB 可用空间",
            )
        task_manager.update_task(
            task_id,
            step="downloading_model",
            progress=1,
            message=f"正在下载 {definition.name}...",
            details={"model_download": {
                "status": "downloading",
                "model_id": model_id,
                "downloaded_bytes": downloaded_before,
                "total_bytes": definition.archive_size,
                "verification": "pending",
            }},
        )

        def report_download(downloaded: int, total: int, resumed: bool) -> None:
            task_manager.update_task(
                task_id,
                step="downloading_model",
                progress=min(70, 1 + downloaded / max(total, 1) * 69),
                message=(
                    f"正在下载 {definition.name}："
                    f"{downloaded / 1024 ** 2:.1f} / {total / 1024 ** 2:.1f} MiB"
                ),
                details={"model_download": {
                    "status": "downloading",
                    "model_id": model_id,
                    "downloaded_bytes": downloaded,
                    "total_bytes": total,
                    "resumed": resumed,
                    "verification": "pending",
                }},
            )

        _download_verified_file(
            url=definition.archive_url,
            destination=archive,
            expected_size=definition.archive_size,
            expected_sha256=definition.archive_sha256,
            checkpoint=lambda: task_manager.checkpoint(task_id),
            report=report_download,
        )
        staging = _staging_dir(model_id)
        shutil.rmtree(staging, ignore_errors=True)
        task_manager.update_task(
            task_id,
            step="extracting_model",
            progress=72,
            message="压缩包校验通过，正在安全解压模型文件...",
        )

        def report_extract(extracted: int, total: int) -> None:
            task_manager.update_task(
                task_id,
                progress=min(88, 72 + extracted / max(total, 1) * 16),
            )

        _safe_extract_required(
            archive,
            staging,
            definition,
            lambda: task_manager.checkpoint(task_id),
            report_extract,
        )
        task_manager.update_task(
            task_id,
            step="verifying_model",
            progress=89,
            message="正在逐文件校验模型...",
        )
        for index, item in enumerate(definition.files):
            _verify_file(
                staging / item.name,
                item,
                lambda: task_manager.checkpoint(task_id),
            )
            task_manager.update_task(
                task_id,
                progress=89 + (index + 1) / max(len(definition.files), 1) * 7,
            )
        _ensure_vad(task_id)
        _write_manifest(staging, definition)
        backup = final.with_name(f".{model_id}.backup-{uuid.uuid4().hex}")
        try:
            if final.exists():
                os.replace(final, backup)
            os.replace(staging, final)
        except OSError as exc:
            if backup.exists() and not final.exists():
                os.replace(backup, final)
            raise ManagedModelError(
                f"无法原子安装模型：{exc}",
                "MODEL_INSTALL_FAILED",
                "请检查模型目录权限；原有模型已保留",
            ) from exc
        finally:
            if backup.exists() and final.exists():
                shutil.rmtree(backup, ignore_errors=True)
        archive.unlink(missing_ok=True)
        status = managed_model_status(model_id)
        task_manager.update_task(
            task_id,
            step="model_ready",
            progress=100,
            message=f"{definition.name} 已下载并校验完成",
            details={
                "model_download": {
                    "status": "ready",
                    "model_id": model_id,
                    "downloaded_bytes": definition.archive_size,
                    "total_bytes": definition.archive_size,
                    "verification": "passed",
                },
                "model_status": status,
            },
        )
        return status


def remove_managed_model(model_id: str) -> dict:
    definition = MANAGED_SHERPA_BY_ID.get(model_id)
    if not definition:
        raise ManagedModelError(
            "只能移除 App 托管模型",
            "MODEL_REMOVE_UNSUPPORTED",
            "自定义或外部模型不会被字幕工厂删除",
            recoverable=False,
        )
    try:
        from ..models.database import get_db

        db = get_db()
        active_run = db.execute(
            """SELECT id FROM transcription_runs
               WHERE status='running' AND model=? LIMIT 1""",
            (model_id,),
        ).fetchone()
        active = active_run
        if not active:
            rows = db.execute(
                """SELECT id, details FROM tasks
                   WHERE status IN ('pending','running','paused')
                     AND type IN ('transcribe','workflow','prepare_model')"""
            ).fetchall()
            for row in rows:
                try:
                    details = json.loads(row["details"] or "{}")
                except (TypeError, ValueError):
                    details = {}
                resolution = details.get("model_resolution") or {}
                if (
                    details.get("model_id") == model_id
                    or resolution.get("model_id") == model_id
                ):
                    active = row
                    break
        db.close()
    except Exception:
        active = None
    if active:
        raise ManagedModelError(
            "有转写或模型任务正在运行，暂时不能移除模型",
            "MODEL_IN_USE",
            "任务完成或终止后再试",
        )
    root = managed_model_dir(model_id)
    staging = _staging_dir(model_id)
    archive = MODELS_DIR / ".downloads" / definition.archive_name
    partial = archive.with_name(f"{archive.name}.part")
    targets = (root, staging, archive, partial)
    removed_bytes = 0
    for target in targets:
        if target.is_dir():
            removed_bytes += sum(
                path.stat().st_size for path in target.rglob("*") if path.is_file()
            )
            shutil.rmtree(target, ignore_errors=False)
        elif target.is_file():
            removed_bytes += target.stat().st_size
            target.unlink()
    return {
        "model_id": model_id,
        "removed": True,
        "removed_bytes": removed_bytes,
        "message": "模型文件已移除，可随时重新下载",
    }


def _wave_info(audio_path: str) -> tuple[float, int]:
    try:
        with wave.open(audio_path, "rb") as audio:
            channels = audio.getnchannels()
            sample_width = audio.getsampwidth()
            sample_rate = audio.getframerate()
            frames = audio.getnframes()
            compression = audio.getcomptype()
    except (OSError, wave.Error) as exc:
        raise ManagedModelError(
            f"无法读取待转写 WAV：{exc}",
            "AUDIO_INVALID",
            "请重新提取音频",
        ) from exc
    if channels != 1 or sample_width != 2 or compression != "NONE":
        raise ManagedModelError(
            "本地模型需要 16-bit PCM 单声道 WAV",
            "AUDIO_FORMAT",
            "请重新提取音频",
        )
    return frames / max(sample_rate, 1), sample_rate


def _paths(definition: ManagedSherpaModel) -> dict[str, str]:
    root = managed_model_dir(definition.id)
    return {item.name: str(root / item.name) for item in definition.files}


def _find_path(paths: dict[str, str], *patterns: str) -> str:
    for pattern in patterns:
        for name, value in paths.items():
            if Path(name).match(pattern):
                return value
    raise ManagedModelError(
        f"模型清单缺少推理文件：{' / '.join(patterns)}",
        "MODEL_INTEGRITY_FAILED",
        "请修复模型",
    )


def _create_recognizer(
    definition: ManagedSherpaModel,
    provider: str,
    language: str,
) -> Any:
    import sherpa_onnx

    paths = _paths(definition)
    common = {
        "num_threads": max(1, min(4, os.cpu_count() or 2)),
        "provider": provider,
        "debug": False,
    }
    if definition.adapter == "dolphin":
        return sherpa_onnx.OfflineRecognizer.from_dolphin_ctc(
            _find_path(paths, "model.int8.onnx"), _find_path(paths, "tokens.txt"), **common,
        )
    if definition.adapter == "omnilingual":
        return sherpa_onnx.OfflineRecognizer.from_omnilingual_asr_ctc(
            _find_path(paths, "model.int8.onnx"), _find_path(paths, "tokens.txt"), **common,
        )
    if definition.adapter == "qwen3":
        tokenizer = str(
            managed_model_dir(definition.id) / "tokenizer"
        )
        return sherpa_onnx.OfflineRecognizer.from_qwen3_asr(
            conv_frontend=_find_path(paths, "conv_frontend.onnx"),
            encoder=_find_path(paths, "encoder*.onnx"),
            decoder=_find_path(paths, "decoder*.onnx"),
            tokenizer=tokenizer,
            max_total_len=512,
            max_new_tokens=512,
            **common,
        )
    if definition.adapter == "moonshine":
        return sherpa_onnx.OfflineRecognizer.from_moonshine_v2(
            encoder=_find_path(paths, "encoder_model.ort"),
            decoder=_find_path(paths, "decoder_model_merged.ort"),
            tokens=_find_path(paths, "tokens.txt"),
            **common,
        )
    if definition.adapter == "paraformer":
        return sherpa_onnx.OfflineRecognizer.from_paraformer(
            _find_path(paths, "model.int8.onnx"), _find_path(paths, "tokens.txt"), **common,
        )
    if definition.adapter == "fire_red_ctc":
        return sherpa_onnx.OfflineRecognizer.from_fire_red_asr_ctc(
            _find_path(paths, "model.int8.onnx"), _find_path(paths, "tokens.txt"), **common,
        )
    if definition.adapter == "telespeech":
        return sherpa_onnx.OfflineRecognizer.from_telespeech_ctc(
            _find_path(paths, "model.int8.onnx"), _find_path(paths, "tokens.txt"), **common,
        )
    if definition.adapter == "wenet_ctc":
        return sherpa_onnx.OfflineRecognizer.from_wenet_ctc(
            _find_path(paths, "model.int8.onnx", "model.onnx"),
            _find_path(paths, "tokens.txt"),
            **common,
        )
    if definition.adapter == "sense_voice":
        normalized = "" if language in {"", "auto"} else language
        return sherpa_onnx.OfflineRecognizer.from_sense_voice(
            _find_path(paths, "model.int8.onnx"),
            _find_path(paths, "tokens.txt"),
            language=normalized,
            use_itn=True,
            **common,
        )
    if definition.adapter == "medasr":
        return sherpa_onnx.OfflineRecognizer.from_medasr_ctc(
            _find_path(paths, "model.int8.onnx"), _find_path(paths, "tokens.txt"), **common,
        )
    if definition.adapter == "nemo_ctc":
        return sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
            _find_path(paths, "model.int8.onnx"), _find_path(paths, "tokens.txt"), **common,
        )
    if definition.adapter == "zipformer_transducer":
        return sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=_find_path(paths, "encoder*.int8.onnx"),
            decoder=_find_path(paths, "decoder*.onnx"),
            joiner=_find_path(paths, "joiner*.int8.onnx"),
            tokens=_find_path(paths, "tokens.txt"),
            model_type="",
            **common,
        )
    if definition.adapter == "nemo_transducer":
        return sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=_find_path(paths, "encoder*.onnx"),
            decoder=_find_path(paths, "decoder*.onnx"),
            joiner=_find_path(paths, "joiner*.onnx"),
            tokens=_find_path(paths, "tokens.txt"),
            model_type="nemo_transducer",
            **common,
        )
    raise ManagedModelError(
        f"未实现的模型适配器：{definition.adapter}",
        "MODEL_RUNTIME_MISSING",
        "请更新字幕工厂",
        recoverable=False,
    )


def _timings_from_result(
    result: Any,
    segment_start: float,
    segment_end: float,
    timestamp_mode: str,
) -> tuple[dict, ...]:
    if timestamp_mode == "segment":
        return ()
    try:
        tokens = list(getattr(result, "tokens", None) or [])
        timestamps = list(getattr(result, "timestamps", None) or [])
    except UnicodeDecodeError:
        return ()
    if not tokens or len(timestamps) < len(tokens):
        return ()
    timestamps = timestamps[:len(tokens)]
    timings: list[dict] = []
    for index, token in enumerate(tokens):
        text = str(token)
        if not text.strip():
            continue
        start = min(segment_end, segment_start + max(0.0, float(timestamps[index])))
        if index + 1 < len(timestamps):
            end = segment_start + max(float(timestamps[index + 1]), float(timestamps[index]) + 0.04)
        else:
            end = segment_end
        end = min(segment_end, max(start + 0.04, end))
        if end > start:
            timings.append({
                "text": text.replace("@@", ""),
                "start": round(start, 4),
                "end": round(end, 4),
            })
    return tuple(timings)


def iter_vad_audio_segments(
    task_id: str,
    audio_path: str,
    vad_path: Path,
    audio_duration: float,
    max_speech_seconds: float,
) -> Iterator[tuple[float, float, Any]]:
    """Yield copied 16 kHz float32 speech spans from the shared Silero VAD."""
    import numpy as np
    import sherpa_onnx

    config = sherpa_onnx.VadModelConfig()
    config.silero_vad.model = str(vad_path)
    config.silero_vad.threshold = 0.2
    config.silero_vad.min_silence_duration = 0.25
    config.silero_vad.min_speech_duration = 0.25
    config.silero_vad.max_speech_duration = max_speech_seconds
    config.sample_rate = 16000
    window_size = int(config.silero_vad.window_size)
    vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=120)
    pending = np.empty(0, dtype=np.float32)
    with wave.open(audio_path, "rb") as audio:
        while True:
            task_manager.checkpoint(task_id)
            raw = audio.readframes(16000 * 2)
            eof = not raw
            if raw:
                samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
                pending = np.concatenate((pending, samples))
                while pending.size >= window_size:
                    vad.accept_waveform(pending[:window_size])
                    pending = pending[window_size:]
            else:
                if pending.size:
                    padded = np.zeros(window_size, dtype=np.float32)
                    padded[:pending.size] = pending
                    vad.accept_waveform(padded)
                vad.flush()
            while not vad.empty():
                task_manager.checkpoint(task_id)
                speech = vad.front
                start = float(speech.start) / 16000.0
                speech_samples = np.asarray(speech.samples, dtype=np.float32).copy()
                vad.pop()
                end = min(audio_duration, start + speech_samples.size / 16000.0)
                if end > start:
                    yield start, end, speech_samples
            if eof:
                break


def _iter_vad_segments(
    task_id: str,
    audio_path: str,
    recognizer: Any,
    vad_path: Path,
    audio_duration: float,
    max_speech_seconds: float,
    timestamp_mode: str,
) -> Iterator[ManagedSegment]:
    for start, end, speech_samples in iter_vad_audio_segments(
        task_id, audio_path, vad_path, audio_duration, max_speech_seconds,
    ):
        stream = recognizer.create_stream()
        stream.accept_waveform(16000, speech_samples)
        recognizer.decode_stream(stream)
        result = stream.result
        text = re.sub(
            r"\s+", " ", str(result.text or "").replace("▁", " ")
        ).strip()
        if not text or _PUNCTUATION_ONLY.fullmatch(text):
            continue
        yield ManagedSegment(
            start=start,
            end=end,
            text=text,
            timings=_timings_from_result(result, start, end, timestamp_mode),
        )


def create_managed_session(
    task_id: str,
    audio_path: str,
    language: str,
    model_id: str,
    runtime: str,
) -> ManagedSession:
    definition = MANAGED_SHERPA_BY_ID.get(model_id)
    if not definition:
        raise ManagedModelError(
            "不支持的托管转写模型",
            "MODEL_NOT_SUPPORTED",
            "请重新选择模型",
            recoverable=False,
        )
    normalized = (language or "auto").lower()
    if (
        normalized not in {"", "auto"}
        and "*" not in definition.languages
        and normalized not in definition.languages
    ):
        raise ManagedModelError(
            f"{definition.name} 不支持所选源语言",
            "MODEL_LANGUAGE_UNSUPPORTED",
            "请选择支持该语言的模型",
            recoverable=False,
        )
    if runtime not in definition.runtimes:
        raise ManagedModelError(
            "所选运行设备不支持当前模型",
            "RUNTIME_NOT_SUPPORTED",
            "请重新选择运行设备",
            recoverable=False,
        )
    if not managed_model_ready(model_id):
        prepare_managed_model(task_id, model_id)
    vad_path = _ensure_vad(task_id)
    task_manager.update_task(
        task_id,
        step="loading_model",
        progress=24,
        message=f"正在加载 {definition.name}...",
    )
    provider = "coreml" if runtime == "coreml" else "cpu"
    try:
        recognizer = _create_recognizer(definition, provider, normalized)
        duration, sample_rate = _wave_info(audio_path)
        if sample_rate != 16000:
            raise ManagedModelError(
                "本地模型字幕模式需要 16kHz 音频",
                "AUDIO_FORMAT",
                "请重新提取音频",
            )
    except ManagedModelError:
        raise
    except Exception as exc:
        raise ManagedModelError(
            f"{definition.name} 推理引擎加载失败：{exc}",
            "MODEL_RUNTIME_MISSING",
            "请修复模型或改用 CPU",
        ) from exc
    return ManagedSession(
        segments=_iter_vad_segments(
            task_id,
            audio_path,
            recognizer,
            vad_path,
            duration,
            definition.vad_max_speech_seconds,
            definition.timestamp_mode,
        ),
        audio_duration=duration,
        detected_language=normalized or "auto",
        device="Core ML" if runtime == "coreml" else "cpu",
        compute_type="int8 ONNX",
        model_label=definition.name,
    )


def recommended_ready_model(language: str) -> str | None:
    normalized = (language or "auto").lower()
    if normalized in {"", "auto"}:
        return None
    priorities = {
        "zh": (
            "paraformer-zh-2023-09-14",
            "fire-red-asr2-ctc-zh-en-int8-2026-02-25",
            "dolphin-base-ctc-multi-lang-int8-2025-04-02",
        ),
        "yue": (
            "wenetspeech-yue-u2pp-conformer-ctc-zh-en-cantonese-int8-2025-09-10",
            "fire-red-asr2-ctc-zh-en-int8-2026-02-25",
        ),
        "wuu": (
            "wenetspeech-wu-u2pp-conformer-ctc-zh-int8-2026-02-03",
            "fire-red-asr2-ctc-zh-en-int8-2026-02-25",
        ),
        "ja": (
            "nemo-parakeet-tdt-ctc-0.6b-ja-35000-int8",
            "moonshine-tiny-ja-quantized-2026-02-27",
        ),
        "ko": (
            "zipformer-korean-2024-06-24",
            "moonshine-tiny-ko-quantized-2026-02-27",
        ),
        "ru": ("nemo-transducer-punct-giga-am-v3-russian-2025-12-16",),
    }
    for model_id in priorities.get(normalized, ()):
        definition = MANAGED_SHERPA_BY_ID.get(model_id)
        if definition and definition.automatic and managed_model_ready(model_id):
            return model_id
    for definition in MANAGED_SHERPA_BY_ID.values():
        if (
            definition.automatic
            and (
                normalized in definition.languages
                or "*" in definition.languages
            )
            and managed_model_ready(definition.id)
        ):
            return definition.id
    return None
