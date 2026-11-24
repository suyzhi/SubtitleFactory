"""Download, verification, extraction, status, and removal for managed sherpa-onnx models."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

from ...utils.config import MODELS_DIR
from ...utils.task_manager import TaskCancelled, task_manager
from ...version import product_user_agent
from ..parakeet_transcriber import (
    SILERO_VAD_BYTES,
    SILERO_VAD_SHA256,
    SILERO_VAD_URL,
)
from ..sherpa_catalog import (
    MANAGED_SHERPA_BY_ID,
    ManagedModelFile,
    ManagedSherpaModel,
)
from .types import _DOWNLOAD_LOCK, _MANIFEST_NAME, ManagedModelError, facade_attr


def managed_model_dir(model_id: str) -> Path:
    models_dir = facade_attr("MODELS_DIR", MODELS_DIR)
    return models_dir / "sherpa-onnx" / model_id


def _staging_dir(model_id: str) -> Path:
    return facade_attr("managed_model_dir", managed_model_dir)(model_id).with_name(
        f".{model_id}.downloading"
    )


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
    root = facade_attr("managed_model_dir", managed_model_dir)(model_id)
    return (
        root.is_dir()
        and _read_manifest(root) == _manifest_identity(definition)
        and all(
            (root / item.name).is_file() and (root / item.name).stat().st_size == item.size
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
    ready = facade_attr("managed_model_ready", managed_model_ready)(model_id)
    error = ""
    if ready and deep:
        try:
            root_dir = facade_attr("managed_model_dir", managed_model_dir)(model_id)
            for item in definition.files:
                _verify_file(root_dir / item.name, item)
        except ManagedModelError as exc:
            ready = False
            error = str(exc)
    root = facade_attr("managed_model_dir", managed_model_dir)(model_id)
    partial = root.exists() or _staging_dir(model_id).exists()
    models_dir = facade_attr("MODELS_DIR", MODELS_DIR)
    return {
        "model_id": model_id,
        "ready": ready,
        "source": "app_download" if ready or partial else "github",
        "state": "ready" if ready else ("invalid" if partial else "not_downloaded"),
        "download_required": not ready,
        "download_bytes": definition.archive_size
        + (0 if (models_dir / "silero_vad.onnx").is_file() else SILERO_VAD_BYTES),
        "installed_bytes": definition.installed_bytes,
        "error": error
        or ("" if ready else ("模型缓存不完整，可执行修复" if partial else "模型尚未下载")),
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
        "User-Agent": product_user_agent("verified-model-downloader"),
    }
    if existing:
        headers["Range"] = f"bytes={existing}-"
    checkpoint()
    try:
        response = urllib.request.urlopen(
            urllib.request.Request(url, headers=headers),
            timeout=120,
        )
    except urllib.error.HTTPError as exc:
        code = (
            "MODEL_SOURCE_CHANGED" if exc.code in {404, 410} else "MODEL_NETWORK_UNREACHABLE"
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
    models_dir = facade_attr("MODELS_DIR", MODELS_DIR)
    tm = facade_attr("task_manager", task_manager)
    download_fn = facade_attr("_download_verified_file", _download_verified_file)
    destination = models_dir / "silero_vad.onnx"
    download_fn(
        url=SILERO_VAD_URL,
        destination=destination,
        expected_size=SILERO_VAD_BYTES,
        expected_sha256=SILERO_VAD_SHA256,
        checkpoint=lambda: tm.checkpoint(task_id),
        report=lambda *_args: None,
    )
    return destination


def prepare_managed_model(
    task_id: str,
    model_id: str,
    *,
    repair: bool = False,
) -> dict:
    tm = facade_attr("task_manager", task_manager)
    models_dir = facade_attr("MODELS_DIR", MODELS_DIR)
    is_ready = facade_attr("managed_model_ready", managed_model_ready)
    status_fn = facade_attr("managed_model_status", managed_model_status)
    download_fn = facade_attr("_download_verified_file", _download_verified_file)
    extract_fn = facade_attr("_safe_extract_required", _safe_extract_required)
    ensure_vad_fn = facade_attr("_ensure_vad", _ensure_vad)

    definition = MANAGED_SHERPA_BY_ID.get(model_id)
    if not definition:
        raise ManagedModelError(
            "此模型不支持由 App 下载",
            "MODEL_PREPARE_UNSUPPORTED",
            "请选择模型目录中的托管模型",
            recoverable=False,
        )
    if not repair and is_ready(model_id):
        return status_fn(model_id)
    if not definition.files or not definition.archive_sha256:
        raise ManagedModelError(
            "模型来源清单不完整",
            "MODEL_SOURCE_CHANGED",
            "请更新字幕工厂",
            recoverable=False,
        )
    final = facade_attr("managed_model_dir", managed_model_dir)(model_id)
    archive = models_dir / ".downloads" / definition.archive_name
    with _DOWNLOAD_LOCK:
        if not repair and is_ready(model_id):
            return status_fn(model_id)
        final.parent.mkdir(parents=True, exist_ok=True)
        partial = archive.with_name(f"{archive.name}.part")
        downloaded_before = partial.stat().st_size if partial.is_file() else 0
        required_free = (
            max(0, definition.archive_size - downloaded_before)
            + definition.installed_bytes
            + 256 * 1024 * 1024
        )
        if shutil.disk_usage(final.parent).free < required_free:
            raise ManagedModelError(
                "模型下载空间不足",
                "MODEL_DISK_SPACE_INSUFFICIENT",
                f"至少需要额外 {required_free / 1024 ** 3:.1f} GB 可用空间",
            )
        tm.update_task(
            task_id,
            step="downloading_model",
            progress=1,
            message=f"正在下载 {definition.name}...",
            details={
                "model_download": {
                    "status": "downloading",
                    "model_id": model_id,
                    "downloaded_bytes": downloaded_before,
                    "total_bytes": definition.archive_size,
                    "verification": "pending",
                }
            },
        )

        def report_download(downloaded: int, total: int, resumed: bool) -> None:
            tm.update_task(
                task_id,
                step="downloading_model",
                progress=min(70, 1 + downloaded / max(total, 1) * 69),
                message=(
                    f"正在下载 {definition.name}："
                    f"{downloaded / 1024 ** 2:.1f} / {total / 1024 ** 2:.1f} MiB"
                ),
                details={
                    "model_download": {
                        "status": "downloading",
                        "model_id": model_id,
                        "downloaded_bytes": downloaded,
                        "total_bytes": total,
                        "resumed": resumed,
                        "verification": "pending",
                    }
                },
            )

        download_fn(
            url=definition.archive_url,
            destination=archive,
            expected_size=definition.archive_size,
            expected_sha256=definition.archive_sha256,
            checkpoint=lambda: tm.checkpoint(task_id),
            report=report_download,
        )
        staging = _staging_dir(model_id)
        shutil.rmtree(staging, ignore_errors=True)
        tm.update_task(
            task_id,
            step="extracting_model",
            progress=72,
            message="压缩包校验通过，正在安全解压模型文件...",
        )

        def report_extract(extracted: int, total: int) -> None:
            tm.update_task(
                task_id,
                progress=min(88, 72 + extracted / max(total, 1) * 16),
            )

        extract_fn(
            archive,
            staging,
            definition,
            lambda: tm.checkpoint(task_id),
            report_extract,
        )
        tm.update_task(
            task_id,
            step="verifying_model",
            progress=89,
            message="正在逐文件校验模型...",
        )
        for index, item in enumerate(definition.files):
            _verify_file(staging / item.name, item, lambda: tm.checkpoint(task_id))
            tm.update_task(
                task_id,
                progress=89 + (index + 1) / max(len(definition.files), 1) * 7,
            )
        ensure_vad_fn(task_id)
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
        status = status_fn(model_id)
        tm.update_task(
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
    models_dir = facade_attr("MODELS_DIR", MODELS_DIR)
    definition = MANAGED_SHERPA_BY_ID.get(model_id)
    if not definition:
        raise ManagedModelError(
            "只能移除 App 托管模型",
            "MODEL_REMOVE_UNSUPPORTED",
            "自定义或外部模型不会被字幕工厂删除",
            recoverable=False,
        )
    try:
        from ...models.database import get_db

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
    root = facade_attr("managed_model_dir", managed_model_dir)(model_id)
    staging = _staging_dir(model_id)
    archive = models_dir / ".downloads" / definition.archive_name
    partial = archive.with_name(f"{archive.name}.part")
    targets = (root, staging, archive, partial)
    removed_bytes = 0
    for target in targets:
        if target.is_dir():
            removed_bytes += sum(path.stat().st_size for path in target.rglob("*") if path.is_file())
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


def recommended_ready_model(language: str) -> str | None:
    is_ready = facade_attr("managed_model_ready", managed_model_ready)
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
        if definition and definition.automatic and is_ready(model_id):
            return model_id
    for definition in MANAGED_SHERPA_BY_ID.values():
        if (
            definition.automatic
            and (normalized in definition.languages or "*" in definition.languages)
            and is_ready(definition.id)
        ):
            return definition.id
    return None
