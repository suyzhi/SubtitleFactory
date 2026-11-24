"""Verified resumable downloads, legacy discovery, and storage lifecycle for catalog models."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

from ...models.database import get_db
from ...utils.config import MLX_MODELS_DIR, QWEN_MLX_MODELS_DIR, WHISPER_MODELS_DIR
from ...utils.task_manager import TaskCancelled, task_manager
from ...version import product_user_agent
from .definitions import (
    QWEN_ASR_CATALOG_BY_ID,
    CatalogFile,
    ModelDownloadError,
    ModelVariant,
    facade_attr,
    model_definition,
    variant_for,
)

_MANIFEST_NAME = ".subtitle-factory-manifest.json"
_DOWNLOAD_LOCK = threading.Lock()


def managed_model_dir(model_id: str, runtime: str) -> Path:
    if model_id in QWEN_ASR_CATALOG_BY_ID:
        root = facade_attr("QWEN_MLX_MODELS_DIR", QWEN_MLX_MODELS_DIR)
    else:
        root = (
            facade_attr("MLX_MODELS_DIR", MLX_MODELS_DIR)
            if runtime == "mlx"
            else facade_attr("WHISPER_MODELS_DIR", WHISPER_MODELS_DIR)
        )
    return root / model_id


def _manifest_identity(variant: ModelVariant) -> dict:
    return {
        "schema": 1,
        "runtime": variant.runtime,
        "repository": variant.repository,
        "revision": variant.revision,
        "files": [
            {"name": item.name, "size": item.size, "sha256": item.sha256}
            for item in variant.files
        ],
    }


def _read_manifest(path: Path) -> dict | None:
    try:
        return json.loads((path / _MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _files_have_expected_sizes(path: Path, variant: ModelVariant) -> bool:
    return path.is_dir() and all(
        (path / item.name).is_file() and (path / item.name).stat().st_size == item.size
        for item in variant.files
    )


def managed_model_ready(model_id: str, runtime: str) -> bool:
    variant = facade_attr("variant_for", variant_for)(model_id, runtime)
    path = facade_attr("managed_model_dir", managed_model_dir)(model_id, runtime)
    return (
        _files_have_expected_sizes(path, variant)
        and _read_manifest(path) == _manifest_identity(variant)
    )


def find_legacy_model(model_id: str, runtime: str) -> Path | None:
    """Locate an existing compatible cache without moving or deleting it."""
    if model_id in QWEN_ASR_CATALOG_BY_ID:
        root = facade_attr("QWEN_MLX_MODELS_DIR", QWEN_MLX_MODELS_DIR)
    else:
        root = (
            facade_attr("MLX_MODELS_DIR", MLX_MODELS_DIR)
            if runtime == "mlx"
            else facade_attr("WHISPER_MODELS_DIR", WHISPER_MODELS_DIR)
        )
    if not root.is_dir():
        return None
    if model_id in QWEN_ASR_CATALOG_BY_ID:
        weight_names = (
            "model.safetensors",
            "model-00001-of-00002.safetensors",
        )
    else:
        weight_names = (
            ("weights.npz", "weights.safetensors") if runtime == "mlx" else ("model.bin",)
        )
    for weight_name in weight_names:
        for weight in root.rglob(weight_name):
            parent = weight.parent
            if model_id not in str(parent).lower():
                continue
            if (parent / "config.json").is_file():
                if runtime == "mlx" or (parent / "tokenizer.json").is_file():
                    return parent.resolve()
    return None


def resolve_local_model(model_id: str, runtime: str) -> Path | None:
    if facade_attr("managed_model_ready", managed_model_ready)(model_id, runtime):
        return facade_attr("managed_model_dir", managed_model_dir)(model_id, runtime).resolve()
    return facade_attr("find_legacy_model", find_legacy_model)(model_id, runtime)


def runtime_model_status(model_id: str, runtime: str) -> dict:
    variant = facade_attr("variant_for", variant_for)(model_id, runtime)
    managed = facade_attr("managed_model_ready", managed_model_ready)(model_id, runtime)
    legacy = None if managed else facade_attr("find_legacy_model", find_legacy_model)(model_id, runtime)
    ready = managed or legacy is not None
    path = facade_attr("managed_model_dir", managed_model_dir)(model_id, runtime)
    partial = path.exists() or path.with_name(f".{model_id}.{runtime}.downloading").exists()
    return {
        "model_ready": ready,
        "download_required": not ready,
        "download_bytes": variant.download_bytes,
        "repository": variant.repository,
        "revision": variant.revision,
        "source_url": variant.source_url,
        "source": "app_download" if managed else ("legacy_cache" if legacy else "huggingface"),
        "status": "ready" if ready else ("invalid" if partial else "not_downloaded"),
    }


def _sha256(path: Path, checkpoint: Callable[[], None]) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            checkpoint()
            chunk = source.read(4 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _verify_file(path: Path, item: CatalogFile, checkpoint: Callable[[], None]) -> None:
    actual_size = path.stat().st_size if path.is_file() else 0
    if actual_size != item.size:
        raise ModelDownloadError(
            f"模型文件大小不匹配：{item.name}",
            "MODEL_INTEGRITY_FAILED",
            suggestion="来源可能已变更；请稍后重试或运行来源验证",
        )
    if _sha256(path, checkpoint) != item.sha256:
        raise ModelDownloadError(
            f"模型文件校验失败：{item.name}",
            "MODEL_INTEGRITY_FAILED",
            suggestion="请执行修复；原有可用模型仍然保留",
        )


def _download_file(
    variant: ModelVariant,
    item: CatalogFile,
    destination: Path,
    checkpoint: Callable[[], None],
    report: Callable[[int, bool], None],
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f"{destination.name}.part")
    if destination.is_file() and destination.stat().st_size == item.size:
        try:
            _verify_file(destination, item, checkpoint)
            report(item.size, False)
            return
        except ModelDownloadError:
            destination.unlink(missing_ok=True)
    existing = partial.stat().st_size if partial.is_file() else 0
    if existing > item.size:
        partial.unlink()
        existing = 0
    url = (
        f"https://huggingface.co/{variant.repository}/resolve/{variant.revision}/"
        f"{urllib.parse.quote(item.name)}?download=true"
    )
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
            timeout=60,
        )
    except urllib.error.HTTPError as exc:
        code = "MODEL_SOURCE_CHANGED" if exc.code in {404, 410} else "MODEL_NETWORK_UNREACHABLE"
        suggestion = (
            "固定提交或文件已不可访问；请运行来源验证并更新应用"
            if code == "MODEL_SOURCE_CHANGED"
            else "请检查网络或代理后重试"
        )
        raise ModelDownloadError(
            f"无法获取模型文件 {item.name}：HTTP {exc.code}",
            code,
            suggestion=suggestion,
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ModelDownloadError(
            f"无法连接模型来源：{exc}",
            "MODEL_NETWORK_UNREACHABLE",
            suggestion="请检查网络或代理后重试；已下载部分可断点续传",
        ) from exc
    try:
        status = getattr(response, "status", 200)
        resumed = bool(existing and status == 206)
        if existing and not resumed:
            existing = 0
        mode = "ab" if resumed else "wb"
        downloaded = existing
        report(downloaded, resumed)
        with partial.open(mode) as output:
            while True:
                checkpoint()
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                report(downloaded, resumed)
    except TaskCancelled:
        raise
    except OSError as exc:
        raise ModelDownloadError(
            f"写入模型暂存目录失败：{exc}",
            "MODEL_DISK_SPACE_INSUFFICIENT",
            suggestion="请释放磁盘空间后重试；已下载部分会保留",
        ) from exc
    finally:
        response.close()
    if not partial.is_file() or partial.stat().st_size != item.size:
        raise ModelDownloadError(
            f"模型文件下载不完整：{item.name}",
            "MODEL_NETWORK_UNREACHABLE",
            suggestion="请重试，下载器会从现有进度继续",
        )
    os.replace(partial, destination)
    try:
        _verify_file(destination, item, checkpoint)
    except ModelDownloadError:
        destination.unlink(missing_ok=True)
        raise


def _write_manifest(path: Path, variant: ModelVariant) -> None:
    payload = json.dumps(
        _manifest_identity(variant),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    temporary = path / f"{_MANIFEST_NAME}.{uuid.uuid4().hex}.tmp"
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, path / _MANIFEST_NAME)


def prepare_catalog_model(
    task_id: str,
    model_id: str,
    runtime: str,
    *,
    repair: bool = False,
) -> dict:
    """Prepare a pinned CPU or MLX model without risking the current install."""
    tm = facade_attr("task_manager", task_manager)
    shutil_mod = facade_attr("shutil", shutil)
    variant = facade_attr("variant_for", variant_for)(model_id, runtime)
    final = facade_attr("managed_model_dir", managed_model_dir)(model_id, runtime)
    resolve_fn = facade_attr("resolve_local_model", resolve_local_model)
    status_fn = facade_attr("runtime_model_status", runtime_model_status)
    download_fn = facade_attr("_download_file", _download_file)

    if not repair:
        existing = resolve_fn(model_id, runtime)
        if existing:
            status = status_fn(model_id, runtime)
            tm.update_task(
                task_id,
                step="model_ready",
                progress=100,
                message="模型已就绪",
                details={"model_status": status},
            )
            return status

    with _DOWNLOAD_LOCK:
        if not repair:
            existing = resolve_fn(model_id, runtime)
            if existing:
                return status_fn(model_id, runtime)
        final.parent.mkdir(parents=True, exist_ok=True)
        staging = final.with_name(f".{model_id}.{runtime}.downloading")
        staging.mkdir(parents=True, exist_ok=True)
        downloaded_before = sum(
            min((staging / f"{item.name}.part").stat().st_size, item.size)
            if (staging / f"{item.name}.part").is_file()
            else (item.size if (staging / item.name).is_file() else 0)
            for item in variant.files
        )
        required_free = max(0, variant.download_bytes - downloaded_before) + 256 * 1024 * 1024
        try:
            free = shutil_mod.disk_usage(final.parent).free
        except OSError as exc:
            raise ModelDownloadError(
                f"无法检查模型磁盘空间：{exc}",
                "MODEL_DISK_SPACE_INSUFFICIENT",
                suggestion="请检查模型目录是否可写",
            ) from exc
        if free < required_free:
            raise ModelDownloadError(
                "模型下载空间不足",
                "MODEL_DISK_SPACE_INSUFFICIENT",
                suggestion=(
                    f"至少需要额外 {required_free / 1024 ** 3:.1f} GB 可用空间；不会删除现有模型"
                ),
            )
        completed = 0
        resumed_any = downloaded_before > 0
        total = variant.download_bytes
        tm.update_task(
            task_id,
            step="downloading_model",
            progress=1,
            message="正在准备经过校验的模型下载...",
            details={
                "model_download": {
                    "status": "downloading",
                    "model_id": model_id,
                    "runtime": runtime,
                    "downloaded_bytes": downloaded_before,
                    "total_bytes": total,
                    "resumed": resumed_any,
                    "verification": "pending",
                }
            },
        )
        for item in variant.files:
            tm.checkpoint(task_id)
            prior_complete = completed

            def report(
                file_bytes: int,
                resumed: bool,
                prior_complete: int = prior_complete,
                item: CatalogFile = item,
            ) -> None:
                current = min(total, prior_complete + file_bytes)
                percent = current * 100 / max(total, 1)
                tm.update_task(
                    task_id,
                    step="downloading_model",
                    progress=min(88, 1 + percent * 0.87),
                    message=(
                        f"正在下载 {model_id} · {runtime.upper()}：{percent:.1f}% · "
                        f"{current / 1024 ** 2:.1f} / {total / 1024 ** 2:.1f} MiB"
                    ),
                    details={
                        "model_download": {
                            "status": "downloading",
                            "model_id": model_id,
                            "runtime": runtime,
                            "current_file": item.name,
                            "downloaded_bytes": current,
                            "total_bytes": total,
                            "resumed": resumed_any or resumed,
                            "verification": "pending",
                        }
                    },
                )

            download_fn(
                variant,
                item,
                staging / item.name,
                lambda: tm.checkpoint(task_id),
                report,
            )
            completed += item.size

        tm.update_task(
            task_id,
            step="verifying_model",
            progress=90,
            message="下载完成，正在逐文件校验大小与 SHA-256...",
            details={
                "model_download": {
                    "status": "verifying",
                    "model_id": model_id,
                    "runtime": runtime,
                    "downloaded_bytes": total,
                    "total_bytes": total,
                    "resumed": resumed_any,
                    "verification": "running",
                }
            },
        )
        for index, item in enumerate(variant.files):
            _verify_file(staging / item.name, item, lambda: tm.checkpoint(task_id))
            tm.update_task(
                task_id,
                progress=90 + ((index + 1) / len(variant.files)) * 7,
            )
        _write_manifest(staging, variant)

        backup = final.with_name(f".{model_id}.{runtime}.backup-{uuid.uuid4().hex}")
        try:
            if final.exists():
                os.replace(final, backup)
            os.replace(staging, final)
        except OSError as exc:
            if backup.exists() and not final.exists():
                os.replace(backup, final)
            raise ModelDownloadError(
                f"无法原子安装模型：{exc}",
                "MODEL_INSTALL_FAILED",
                suggestion="请检查模型目录权限；原有模型已保留",
            ) from exc
        finally:
            if backup.exists() and final.exists():
                shutil.rmtree(backup, ignore_errors=True)

        status = status_fn(model_id, runtime)
        tm.update_task(
            task_id,
            step="model_ready",
            progress=100,
            message="模型下载并校验完成",
            details={
                "model_download": {
                    "status": "ready",
                    "model_id": model_id,
                    "runtime": runtime,
                    "downloaded_bytes": total,
                    "total_bytes": total,
                    "resumed": resumed_any,
                    "verification": "passed",
                },
                "model_status": status,
            },
        )
        return status


def prepare_whisper_model(
    task_id: str,
    model_id: str,
    runtime: str,
    *,
    repair: bool = False,
) -> dict:
    """Backward-compatible wrapper for callers that prepare Whisper models."""
    return prepare_catalog_model(task_id, model_id, runtime, repair=repair)


def remove_catalog_model(model_id: str) -> dict:
    """Remove only App-managed catalog files, never a discovered legacy cache."""
    definition = model_definition(model_id)
    if not definition:
        raise ModelDownloadError(
            "不支持移除此模型",
            "MODEL_REMOVE_UNSUPPORTED",
            suggestion="请重新选择模型",
            recoverable=False,
        )
    db = get_db()
    try:
        rows = db.execute(
            """SELECT details FROM tasks
               WHERE status IN ('pending','running','paused')
                 AND type IN ('transcribe','workflow','prepare_model')"""
        ).fetchall()
    finally:
        db.close()
    for row in rows:
        try:
            details = json.loads(row["details"] or "{}")
        except (TypeError, ValueError):
            details = {}
        resolution = details.get("model_resolution") or {}
        if details.get("model_id") == model_id or resolution.get("model_id") == model_id:
            raise ModelDownloadError(
                "有转写或模型任务正在运行，暂时不能移除模型",
                "MODEL_IN_USE",
                suggestion="任务完成或终止后再试",
            )

    dir_fn = facade_attr("managed_model_dir", managed_model_dir)
    targets: set[Path] = set()
    for runtime in definition.variants:
        final = dir_fn(model_id, runtime)
        targets.add(final)
        targets.add(final.with_name(f".{model_id}.{runtime}.downloading"))
    removed_bytes = 0
    for target in targets:
        if target.is_symlink() or target.is_file():
            removed_bytes += target.stat().st_size
            target.unlink()
        elif target.is_dir():
            removed_bytes += sum(path.stat().st_size for path in target.rglob("*") if path.is_file())
            shutil.rmtree(target, ignore_errors=False)
    return {
        "model_id": model_id,
        "removed": True,
        "removed_bytes": removed_bytes,
        "message": "模型文件已移除，可随时重新下载",
    }
