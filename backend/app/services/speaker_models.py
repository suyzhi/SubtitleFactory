"""Managed offline speaker-diarization model download."""

from __future__ import annotations

import shutil
import tarfile
from pathlib import Path

import httpx

from ..utils.config import MODELS_DIR
from ..utils.task_manager import task_manager


ROOT = Path(MODELS_DIR) / "speaker-diarization"
SEGMENTATION_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
EMBEDDING_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
SEGMENTATION_PATH = ROOT / "segmentation" / "model.onnx"
EMBEDDING_PATH = ROOT / "embedding" / "model.onnx"


def status() -> dict:
    return {
        "ready": SEGMENTATION_PATH.is_file() and SEGMENTATION_PATH.stat().st_size > 1_000_000
                 and EMBEDDING_PATH.is_file() and EMBEDDING_PATH.stat().st_size > 1_000_000,
        "segmentation_model": str(SEGMENTATION_PATH) if SEGMENTATION_PATH.is_file() else None,
        "embedding_model": str(EMBEDDING_PATH) if EMBEDDING_PATH.is_file() else None,
        "managed_directory": str(ROOT),
    }


def _download(task_id: str, url: str, destination: Path, progress_start: float, progress_end: float) -> Path:
    part = destination.with_suffix(destination.suffix + ".part")
    part.parent.mkdir(parents=True, exist_ok=True)
    existing = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={existing}-"} if existing else {}
    with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=120) as response:
        if response.status_code == 416:
            part.replace(destination); return destination
        response.raise_for_status()
        if existing and response.status_code != 206:
            existing = 0
        total = existing + int(response.headers.get("Content-Length", "0") or 0)
        mode = "ab" if existing and response.status_code == 206 else "wb"
        written = existing
        with part.open(mode) as output:
            for chunk in response.iter_bytes(1024 * 1024):
                task_manager.checkpoint(task_id); output.write(chunk); written += len(chunk)
                if total:
                    progress = progress_start + written / total * (progress_end - progress_start)
                    task_manager.update_task(task_id, step="speaker_models", progress=progress, message="正在下载离线说话人模型")
    if part.stat().st_size < 1_000_000:
        raise RuntimeError("说话人模型下载不完整")
    part.replace(destination)
    return destination


def prepare(task_id: str):
    ROOT.mkdir(parents=True, exist_ok=True)
    archive = ROOT / "segmentation.tar.bz2"
    if not SEGMENTATION_PATH.is_file():
        try:
            _download(task_id, SEGMENTATION_URL, archive, 2, 48)
        except httpx.HTTPError as cause:
            error = RuntimeError("说话人分割模型下载失败")
            error.error_code = "MODEL_DOWNLOAD_FAILED"; error.recoverable = True
            error.available_actions = ["retry", "open_settings"]
            raise error from cause
        extract_root = ROOT / "segmentation-extracting"
        shutil.rmtree(extract_root, ignore_errors=True); extract_root.mkdir(parents=True)
        with tarfile.open(archive, "r:bz2") as bundle:
            for member in bundle.getmembers():
                if member.issym() or member.islnk():
                    raise RuntimeError("说话人模型压缩包包含不安全链接")
                target = (extract_root / member.name).resolve()
                if extract_root.resolve() not in target.parents and target != extract_root.resolve():
                    raise RuntimeError("说话人模型压缩包路径无效")
            bundle.extractall(extract_root)
        model = next(extract_root.rglob("model.onnx"), None)
        if not model or model.stat().st_size < 1_000_000:
            raise RuntimeError("分割模型压缩包缺少 model.onnx")
        SEGMENTATION_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(model), SEGMENTATION_PATH)
        shutil.rmtree(extract_root, ignore_errors=True); archive.unlink(missing_ok=True)
    if not EMBEDDING_PATH.is_file():
        download = ROOT / "embedding.onnx"
        try:
            _download(task_id, EMBEDDING_URL, download, 50, 98)
        except httpx.HTTPError as cause:
            error = RuntimeError("说话人嵌入模型下载失败")
            error.error_code = "MODEL_DOWNLOAD_FAILED"; error.recoverable = True
            error.available_actions = ["retry", "open_settings"]
            raise error from cause
        EMBEDDING_PATH.parent.mkdir(parents=True, exist_ok=True)
        download.replace(EMBEDDING_PATH)
    task_manager.update_task(task_id, step="speaker_models_ready", progress=100, message="离线说话人模型已就绪", details=status())
    return status()
