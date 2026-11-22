"""Parakeet ONNX and Core ML model asset validation, download, extraction, and repair."""

from __future__ import annotations

import hashlib
import os
import shutil
import tarfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

from ...utils.config import MODELS_DIR, environment_path_overrides_enabled
from ...utils.task_manager import TaskCancelled, task_manager
from ...version import product_user_agent
from .constants import (
    _DOWNLOAD_LOCK,
    PARAKEET_ARCHIVE_BYTES,
    PARAKEET_ARCHIVE_SHA256,
    PARAKEET_ARCHIVE_URL,
    PARAKEET_COREML_BASE_URL,
    PARAKEET_COREML_DOWNLOAD_BYTES,
    PARAKEET_COREML_FILES,
    PARAKEET_COREML_REPOSITORY,
    PARAKEET_COREML_REVISION,
    PARAKEET_COREML_SOURCE_URL,
    PARAKEET_EXTRACTED_ESTIMATE_BYTES,
    PARAKEET_MODEL_DIR_NAME,
    PARAKEET_MODEL_ID,
    PARAKEET_ONNX_MODEL_ID,
    PARAKEET_REQUIRED_FILES,
    SILERO_VAD_BYTES,
    SILERO_VAD_SHA256,
    SILERO_VAD_URL,
    ParakeetAssets,
    ParakeetModelError,
    facade_attr,
)
from .coreml import (
    _valid_coreml_model_dir,
    bundled_coreml_cli,
    discover_coreml_runtime,
    managed_coreml_model_dir,
    validate_coreml_runtime_paths,
)


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
            # The runner ships with the App; only the official model is missing.
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


def _format_mib(byte_count: int) -> str:
    return f"{byte_count / (1024 * 1024):.1f} MiB"


def _download_file(
    url: str,
    destination: Path,
    expected_size: int,
    progress_callback: Callable[[int, int, bool], None],
    checkpoint: Callable[[], None],
    expected_sha256: str | None = None,
) -> None:
    """Download atomically with HTTP Range resume and cooperative cancellation."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f"{destination.name}.part")

    expected_sha256 = expected_sha256 or {
        PARAKEET_ARCHIVE_URL: PARAKEET_ARCHIVE_SHA256,
        SILERO_VAD_URL: SILERO_VAD_SHA256,
    }.get(url)

    def verified(path: Path) -> bool:
        if not path.is_file() or path.stat().st_size != expected_size:
            return False
        if not expected_sha256:
            return True
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while True:
                checkpoint()
                chunk = source.read(4 * 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest() == expected_sha256

    if verified(destination):
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
        if verified(destination):
            progress_callback(expected_size, expected_size, True)
            return
        destination.unlink(missing_ok=True)
        existing = 0

    checkpoint()
    headers = {
        "Accept": "application/octet-stream",
        "User-Agent": product_user_agent("sherpa-onnx-model-downloader"),
    }
    if existing:
        headers["Range"] = f"bytes={existing}-"

    request = urllib.request.Request(url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=60)
    except urllib.error.HTTPError as exc:
        if exc.code in {404, 410}:
            raise ParakeetModelError(
                f"官方模型来源已变化：HTTP {exc.code}",
                "MODEL_SOURCE_CHANGED",
                "请更新字幕工厂或重新运行模型来源验证",
            ) from exc
        raise ParakeetModelError(
            f"无法连接官方模型下载地址：HTTP {exc.code}",
            "MODEL_NETWORK_UNREACHABLE",
            "请检查网络或代理后重试；已下载部分可断点续传",
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ParakeetModelError(
            f"无法连接官方模型下载地址：{exc}",
            "MODEL_NETWORK_UNREACHABLE",
            "请检查网络或代理后重试；已下载部分可断点续传",
        ) from exc

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
        raise ParakeetModelError(
            f"写入模型缓存失败：{exc}",
            "MODEL_DISK_SPACE_INSUFFICIENT",
            "请释放磁盘空间后重试；现有可用模型不会被删除",
        ) from exc
    finally:
        response.close()

    actual_size = partial.stat().st_size if partial.is_file() else 0
    if actual_size != expected_size:
        raise ParakeetModelError(
            "模型下载不完整："
            f"收到 {_format_mib(actual_size)}，预期 {_format_mib(expected_size)}；"
            "可再次开始任务以断点续传",
            "MODEL_NETWORK_UNREACHABLE",
            "请重试，下载器会从现有进度继续",
        )
    os.replace(partial, destination)
    if not verified(destination):
        destination.unlink(missing_ok=True)
        raise ParakeetModelError(
            "模型完整性校验失败：官方文件 SHA-256 与发布清单不一致",
            "MODEL_INTEGRITY_FAILED",
            "请执行修复；现有可用模型仍然保留",
        )


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


def ensure_parakeet_coreml_model(
    task_id: str,
    cache_root: Path | None = None,
    *,
    repair: bool = False,
) -> Path:
    """Download the pinned official Core ML model into the App data directory."""
    tm = facade_attr("task_manager", task_manager)
    download_fn = facade_attr("_download_file", _download_file)
    coreml_files = facade_attr("PARAKEET_COREML_FILES", PARAKEET_COREML_FILES)
    coreml_download_bytes = facade_attr(
        "PARAKEET_COREML_DOWNLOAD_BYTES", PARAKEET_COREML_DOWNLOAD_BYTES
    )

    root = Path(cache_root or MODELS_DIR).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = managed_coreml_model_dir(root)
    if _valid_coreml_model_dir(target) and not repair:
        return target

    with _DOWNLOAD_LOCK:
        if _valid_coreml_model_dir(target) and not repair:
            return target
        staging = root / f".{PARAKEET_MODEL_ID}.downloading"
        staging.mkdir(parents=True, exist_ok=True)
        if target.is_dir():
            for relative, _size, _digest in coreml_files:
                source, destination = target / relative, staging / relative
                if source.is_file() and not destination.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        os.link(source, destination)
                    except OSError:
                        pass
        present = sum(
            size for relative, size, _ in coreml_files if (staging / relative).is_file()
        )
        if shutil.disk_usage(root).free < max(0, coreml_download_bytes - present) + 64 * 1024 * 1024:
            raise ParakeetModelError(
                "模型下载空间不足；请至少保留约 550 MB 可用空间",
                "MODEL_DISK_SPACE_INSUFFICIENT",
                "释放磁盘空间后重试；现有可用模型不会被删除",
            )
        tm.update_task(
            task_id,
            step="downloading_model",
            progress=2,
            message=f"正在从 Hugging Face 下载 Parakeet Core ML 模型（约 {_format_mib(coreml_download_bytes)}）...",
            details={
                "model_download": {
                    "status": "downloading",
                    "model_id": PARAKEET_MODEL_ID,
                    "downloaded_bytes": 0,
                    "total_bytes": coreml_download_bytes,
                    "repository": PARAKEET_COREML_REPOSITORY,
                    "revision": PARAKEET_COREML_REVISION,
                }
            },
        )
        tm.add_log(
            task_id,
            "info",
            "Parakeet Core ML",
            f"从官方仓库 {PARAKEET_COREML_REPOSITORY} 获取模型，版本已固定并逐文件校验 SHA-256",
        )
        completed = 0
        last_percent = -1
        try:
            for relative, size, digest in coreml_files:

                def report(
                    downloaded: int, _total: int, resumed: bool, done: int = completed
                ) -> None:
                    nonlocal last_percent
                    overall = done + downloaded
                    percent = min(100, int(overall * 100 / coreml_download_bytes))
                    if percent == last_percent:
                        return
                    last_percent = percent
                    resume_text = "（断点续传）" if resumed else ""
                    tm.update_task(
                        task_id,
                        step="downloading_model",
                        progress=2 + percent * 0.9,
                        message=(
                            f"正在下载 Parakeet Core ML 模型{resume_text}：{percent}% · "
                            f"{_format_mib(overall)} / {_format_mib(coreml_download_bytes)}"
                        ),
                        details={
                            "model_download": {
                                "status": "downloading",
                                "model_id": PARAKEET_MODEL_ID,
                                "downloaded_bytes": overall,
                                "total_bytes": coreml_download_bytes,
                                "resumed": resumed,
                            }
                        },
                    )

                download_fn(
                    PARAKEET_COREML_BASE_URL + relative,
                    staging / relative,
                    size,
                    report,
                    lambda: tm.checkpoint(task_id),
                    expected_sha256=digest,
                )
                completed += size
            if not _valid_coreml_model_dir(staging):
                raise RuntimeError("Core ML 模型文件校验后仍不完整")
        except TaskCancelled:
            raise
        except Exception as exc:
            tm.add_log(
                task_id,
                "error",
                "Parakeet Core ML",
                "模型下载失败",
                detail=str(exc),
                suggestion="请检查网络（需要能访问 huggingface.co）和磁盘空间后重试；已下载部分会断点续传",
            )
            if isinstance(exc, ParakeetModelError):
                raise
            raise ParakeetModelError(
                f"Parakeet Core ML 模型准备失败：{exc}",
                "MODEL_INSTALL_FAILED",
                "请重试修复；原有可用模型仍然保留",
            ) from exc

        backup = target.with_name(f".{target.name}.backup-{uuid.uuid4().hex}")
        try:
            if target.exists():
                os.replace(target, backup)
            os.replace(staging, target)
        except OSError as exc:
            if not target.exists() and backup.exists():
                os.replace(backup, target)
            raise RuntimeError(f"Core ML 模型原子替换失败：{exc}") from exc
        finally:
            if target.exists():
                shutil.rmtree(backup, ignore_errors=True)
        tm.add_log(task_id, "info", "Parakeet Core ML", "官方 Core ML 模型已下载并通过校验")
        return target


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
