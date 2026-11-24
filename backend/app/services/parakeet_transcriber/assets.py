"""Parakeet ONNX and Core ML model asset status, preparation, and repair."""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

from ...utils.config import MODELS_DIR, environment_path_overrides_enabled
from ...utils.task_manager import TaskCancelled, task_manager
from .assets_download import (
    _asset_paths,
    _download_file,
    _format_mib,
    _install_model_archive,
    _safe_extract_tar,
    ensure_parakeet_coreml_model,
)
from .constants import (
    _DOWNLOAD_LOCK,
    PARAKEET_ARCHIVE_BYTES,
    PARAKEET_ARCHIVE_URL,
    PARAKEET_COREML_DOWNLOAD_BYTES,
    PARAKEET_COREML_REPOSITORY,
    PARAKEET_COREML_REVISION,
    PARAKEET_COREML_SOURCE_URL,
    PARAKEET_EXTRACTED_ESTIMATE_BYTES,
    PARAKEET_MODEL_DIR_NAME,
    PARAKEET_MODEL_ID,
    PARAKEET_ONNX_MODEL_ID,
    PARAKEET_REQUIRED_FILES,
    SILERO_VAD_BYTES,
    SILERO_VAD_URL,
    ParakeetAssets,
    ParakeetModelError,
    facade_attr,
)
from .coreml import (
    bundled_coreml_cli,
    discover_coreml_runtime,
    managed_coreml_model_dir,
    validate_coreml_runtime_paths,
)

__all__ = [
    "_asset_paths",
    "_download_file",
    "_format_mib",
    "_install_model_archive",
    "_model_cache_is_valid",
    "_model_files_are_valid",
    "_safe_extract_tar",
    "ensure_parakeet_assets",
    "ensure_parakeet_coreml_model",
    "get_parakeet_model_status",
    "prepare_parakeet_model",
]


def _model_files_are_valid(assets: ParakeetAssets) -> bool:
    required_files = facade_attr("PARAKEET_REQUIRED_FILES", PARAKEET_REQUIRED_FILES)
    paths = {
        "encoder.int8.onnx": assets.encoder,
        "decoder.int8.onnx": assets.decoder,
        "joiner.int8.onnx": assets.joiner,
        "tokens.txt": assets.tokens,
    }
    return all(
        path.is_file() and path.stat().st_size >= required_files[name]
        for name, path in paths.items()
    )


def _model_cache_is_valid(assets: ParakeetAssets) -> bool:
    silero_bytes = facade_attr("SILERO_VAD_BYTES", SILERO_VAD_BYTES)
    return (
        _model_files_are_valid(assets)
        and assets.vad.is_file()
        and assets.vad.stat().st_size == silero_bytes
    )


def get_parakeet_model_status(
    model_id: str = PARAKEET_ONNX_MODEL_ID,
    *,
    cache_root: Path | None = None,
    coreml_model_dir: str | Path | None = None,
    coreml_cli_path: str | Path | None = None,
) -> dict:
    """Return a source-aware status for model manager and diagnostics APIs."""
    discover_fn = facade_attr("discover_coreml_runtime", discover_coreml_runtime)
    bundled_cli_fn = facade_attr("bundled_coreml_cli", bundled_coreml_cli)
    if model_id == PARAKEET_MODEL_ID:
        try:
            runtime = discover_fn(
                coreml_model_dir,
                coreml_cli_path,
                strict=False,
                allow_environment=(
                    coreml_model_dir is None
                    and coreml_cli_path is None
                    and environment_path_overrides_enabled()
                ),
                cache_root=cache_root,
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
                "repository": PARAKEET_COREML_REPOSITORY,
                "revision": PARAKEET_COREML_REVISION,
                "source_url": PARAKEET_COREML_SOURCE_URL,
                "error": "",
            }
        if coreml_model_dir is None and bundled_cli_fn() is not None:
            managed = managed_coreml_model_dir(cache_root)
            partial = (
                managed.exists()
                or managed.with_name(f".{PARAKEET_MODEL_ID}.downloading").exists()
            )
            return {
                "model_id": model_id,
                "ready": False,
                "source": "huggingface",
                "state": "invalid" if partial else "not_downloaded",
                "download_required": True,
                "download_bytes": facade_attr(
                    "PARAKEET_COREML_DOWNLOAD_BYTES", PARAKEET_COREML_DOWNLOAD_BYTES
                ),
                "repository": PARAKEET_COREML_REPOSITORY,
                "revision": PARAKEET_COREML_REVISION,
                "source_url": PARAKEET_COREML_SOURCE_URL,
                "error": "模型缓存不完整，可执行修复" if partial else "模型尚未下载",
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
                "error",
                "当前运行包缺少 Core ML 转写组件 parakeet-coreml，也未发现 Memo 的 Core ML 模型与 CLI",
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
    archive_bytes = facade_attr("PARAKEET_ARCHIVE_BYTES", PARAKEET_ARCHIVE_BYTES)
    silero_bytes = facade_attr("SILERO_VAD_BYTES", SILERO_VAD_BYTES)
    return {
        "model_id": model_id,
        "ready": ready,
        "source": "app_download" if ready or partial else "unavailable",
        "state": "ready" if ready else ("invalid" if partial else "not_downloaded"),
        "download_required": not ready,
        "download_bytes": archive_bytes + silero_bytes,
        "error": "" if ready else ("模型缓存不完整，可执行修复" if partial else "模型尚未下载"),
    }


def ensure_parakeet_assets(
    task_id: str,
    cache_root: Path | None = None,
    *,
    repair: bool = False,
) -> ParakeetAssets:
    """Return complete cached assets, downloading official files on first use."""
    tm = facade_attr("task_manager", task_manager)
    download_fn = facade_attr("_download_file", _download_file)
    archive_bytes = facade_attr("PARAKEET_ARCHIVE_BYTES", PARAKEET_ARCHIVE_BYTES)
    silero_bytes = facade_attr("SILERO_VAD_BYTES", SILERO_VAD_BYTES)

    live_root = Path(cache_root or MODELS_DIR).expanduser().resolve()
    live_root.mkdir(parents=True, exist_ok=True)
    live_assets = _asset_paths(live_root)
    assets = live_assets
    if _model_cache_is_valid(assets) and not repair:
        tm.update_task(
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
        preserve_existing = repair and _model_cache_is_valid(live_assets)
        root = (
            live_root / f".{PARAKEET_MODEL_DIR_NAME}.repair"
            if preserve_existing
            else live_root
        )
        root.mkdir(parents=True, exist_ok=True)
        assets = _asset_paths(root)
        if _model_cache_is_valid(live_assets) and not repair:
            return live_assets

        archive = root / f"{PARAKEET_MODEL_DIR_NAME}.tar.bz2"
        if repair:
            tm.update_task(
                task_id,
                step="repairing_model",
                progress=1,
                message="正在暂存目录重新下载并校验 Parakeet 模型...",
                details={
                    "model_download": {
                        "status": "repairing",
                        "model_id": PARAKEET_ONNX_MODEL_ID,
                    }
                },
            )
        estimated_needed = (
            archive_bytes
            + PARAKEET_EXTRACTED_ESTIMATE_BYTES
            + silero_bytes
            + 128 * 1024 * 1024
        )
        partial_bytes = sum(
            path.stat().st_size
            for path in (
                archive,
                archive.with_name(f"{archive.name}.part"),
                assets.vad,
                assets.vad.with_name(f"{assets.vad.name}.part"),
            )
            if path.is_file()
        )
        if shutil.disk_usage(root).free < max(0, estimated_needed - partial_bytes):
            raise ParakeetModelError(
                "模型下载空间不足；请至少保留约 1.3 GB 可用空间",
                "MODEL_DISK_SPACE_INSUFFICIENT",
                "释放磁盘空间后重试；现有可用模型不会被删除",
            )
        last_reported_percent = -1
        model_files_ready = _model_files_are_valid(assets)
        if not model_files_ready:
            tm.update_task(
                task_id,
                step="downloading_model",
                progress=2,
                message="首次使用 Parakeet：正在下载官方 int8 模型（约 465 MiB）...",
                details={
                    "model_download": {
                        "status": "downloading",
                        "model_id": PARAKEET_ONNX_MODEL_ID,
                        "downloaded_bytes": 0,
                        "total_bytes": archive_bytes,
                    }
                },
            )
            tm.add_log(
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
            tm.update_task(
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
                download_fn(
                    PARAKEET_ARCHIVE_URL,
                    archive,
                    archive_bytes,
                    report_model_progress,
                    lambda: tm.checkpoint(task_id),
                )
                tm.checkpoint(task_id)
                tm.update_task(
                    task_id,
                    step="extracting_model",
                    progress=19,
                    message="Parakeet 模型下载完成，正在校验并解压...",
                    details={
                        "model_download": {
                            "status": "extracting",
                            "model_id": PARAKEET_ONNX_MODEL_ID,
                            "downloaded_bytes": archive_bytes,
                            "total_bytes": archive_bytes,
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
                    tm.update_task(
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
                    lambda: tm.checkpoint(task_id),
                    report_extract_progress,
                )
                if not _model_files_are_valid(assets):
                    raise RuntimeError("模型解压校验失败，缓存不完整")
                archive.unlink(missing_ok=True)
                tm.checkpoint(task_id)

            tm.update_task(
                task_id,
                step="downloading_model",
                progress=22,
                message="正在准备 Parakeet 语音检测组件...",
            )

            def report_vad_progress(downloaded: int, total: int, _resumed: bool) -> None:
                tm.update_task(
                    task_id,
                    step="downloading_model",
                    progress=22 + min(1.5, downloaded / max(total, 1) * 1.5),
                    message="正在准备 Parakeet 语音检测组件...",
                )

            download_fn(
                SILERO_VAD_URL,
                assets.vad,
                silero_bytes,
                report_vad_progress,
                lambda: tm.checkpoint(task_id),
            )
            assets = _asset_paths(root)
            if not _model_cache_is_valid(assets):
                raise RuntimeError("模型文件校验失败，缓存不完整")
        except TaskCancelled:
            raise
        except Exception as exc:
            tm.update_task(
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
            tm.add_log(
                task_id,
                "error",
                "Parakeet 模型",
                "模型准备失败",
                detail=str(exc),
                suggestion="请检查网络和磁盘空间后重试；未完成文件会用于断点续传",
            )
            if isinstance(exc, ParakeetModelError):
                raise
            code = (
                "MODEL_INTEGRITY_FAILED"
                if any(word in str(exc) for word in ("校验", "损坏", "解压"))
                else "MODEL_INSTALL_FAILED"
            )
            raise ParakeetModelError(
                f"Parakeet 模型准备失败：{exc}",
                code,
                "请重试修复；原有可用模型仍然保留",
            ) from exc

        if preserve_existing:
            backup_model = live_assets.model_dir.with_name(
                f".{live_assets.model_dir.name}.backup-{uuid.uuid4().hex}"
            )
            backup_vad = live_assets.vad.with_name(
                f".{live_assets.vad.name}.backup-{uuid.uuid4().hex}"
            )
            try:
                os.replace(live_assets.model_dir, backup_model)
                os.replace(live_assets.vad, backup_vad)
                os.replace(assets.model_dir, live_assets.model_dir)
                os.replace(assets.vad, live_assets.vad)
            except OSError as exc:
                if not live_assets.model_dir.exists() and backup_model.exists():
                    os.replace(backup_model, live_assets.model_dir)
                if not live_assets.vad.exists() and backup_vad.exists():
                    os.replace(backup_vad, live_assets.vad)
                raise RuntimeError(f"Parakeet 模型原子替换失败：{exc}") from exc
            finally:
                if live_assets.model_dir.exists():
                    shutil.rmtree(backup_model, ignore_errors=True)
                if live_assets.vad.exists():
                    backup_vad.unlink(missing_ok=True)
                shutil.rmtree(root, ignore_errors=True)
            assets = live_assets

        tm.update_task(
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
        tm.add_log(
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
    tm = facade_attr("task_manager", task_manager)
    bundled_cli_fn = facade_attr("bundled_coreml_cli", bundled_coreml_cli)
    get_status_fn = facade_attr("get_parakeet_model_status", get_parakeet_model_status)
    ensure_coreml_fn = facade_attr("ensure_parakeet_coreml_model", ensure_parakeet_coreml_model)
    ensure_assets_fn = facade_attr("ensure_parakeet_assets", ensure_parakeet_assets)
    if model_id == PARAKEET_MODEL_ID:
        status = get_status_fn(
            model_id,
            cache_root=cache_root,
            coreml_model_dir=coreml_model_dir,
            coreml_cli_path=coreml_cli_path,
        )
        uses_managed_model = coreml_model_dir is None and bundled_cli_fn() is not None
        if status.get("download_required") or (repair and uses_managed_model):
            ensure_coreml_fn(task_id, cache_root, repair=repair)
            status = get_status_fn(
                model_id,
                cache_root=cache_root,
                coreml_model_dir=coreml_model_dir,
                coreml_cli_path=coreml_cli_path,
            )
        if not status["ready"]:
            raise RuntimeError(status["error"])
        tm.update_task(
            task_id,
            step="model_ready",
            progress=100,
            message="Parakeet Core ML 模型已准备完成",
            details={"model_status": status},
        )
        return status
    if model_id != PARAKEET_ONNX_MODEL_ID:
        raise ValueError(f"不支持的 Parakeet 模型：{model_id}")
    ensure_assets_fn(task_id, cache_root, repair=repair)
    status = get_status_fn(model_id, cache_root=cache_root)
    tm.update_task(
        task_id,
        step="model_ready",
        progress=100,
        message="Parakeet ONNX 模型已准备完成",
        details={"model_status": status},
    )
    return status
