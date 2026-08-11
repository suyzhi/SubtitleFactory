"""Explicitly authorized cloud transcription with Alibaba Fun-Realtime-ASR."""

from __future__ import annotations

import base64
import io
import json
import wave
from dataclasses import dataclass
from typing import Iterator
from urllib.parse import urlparse, urlunparse

import httpx

from ..models.database import get_db
from ..utils.task_manager import task_manager
from .ai_providers import get_provider


FUN_ASR_MODEL_ID = "fun-asr-realtime"
FUN_ASR_RUNTIME = "dashscope_cloud"
_MAX_CHUNK_FRAMES = 150 * 16_000


class CloudAsrError(RuntimeError):
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
        self.available_actions = ["retry"] if recoverable else []


@dataclass(frozen=True)
class CloudAsrSegment:
    start: float
    end: float
    text: str
    words: tuple[dict, ...] = ()


@dataclass(frozen=True)
class CloudAsrSession:
    segments: Iterator[CloudAsrSegment]
    audio_duration: float
    detected_language: str
    device: str = "阿里云百炼"
    compute_type: str = "Fun-Realtime-ASR 云端推理"
    model_label: str = "Fun-Realtime-ASR"
    progress_start: float = 5.0


def _authorization_granted() -> bool:
    db = get_db()
    try:
        row = db.execute(
            "SELECT provider_id,granted FROM cloud_authorizations WHERE capability='transcription'"
        ).fetchone()
    finally:
        db.close()
    return bool(row and row["granted"] and row["provider_id"] == "dashscope")


def _endpoint(base_url: str) -> str:
    parsed = urlparse((base_url or "").strip())
    host = (parsed.hostname or "").lower()
    official = host == "dashscope.aliyuncs.com" or host.endswith(
        ".cn-beijing.maas.aliyuncs.com"
    )
    if parsed.scheme != "https" or not official:
        raise CloudAsrError(
            "Fun-Realtime-ASR 仅允许连接阿里云百炼北京地域官方 HTTPS 地址",
            "CLOUD_ENDPOINT_UNSUPPORTED",
            "请把通义千问 Base URL 恢复为阿里云官方地址",
            recoverable=False,
        )
    path = parsed.path.rstrip("/")
    suffix = "/compatible-mode/v1"
    if path.endswith(suffix):
        path = path[: -len(suffix)]
    generation_path = "/api/v1/services/aigc/multimodal-generation/generation"
    if not path.endswith(generation_path):
        if path.endswith("/api/v1"):
            path = path[: -len("/api/v1")]
        path = f"{path}{generation_path}"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def fun_asr_status() -> dict:
    try:
        provider = get_provider("dashscope", include_secret=False)
    except Exception:
        provider = {}
    has_key = bool(provider.get("has_api_key"))
    enabled = bool(provider.get("enabled", True))
    authorized = _authorization_granted()
    endpoint_ok = False
    if provider.get("base_url"):
        try:
            _endpoint(str(provider["base_url"]))
            endpoint_ok = True
        except CloudAsrError:
            endpoint_ok = False
    ready = has_key and enabled and authorized and endpoint_ok
    if not has_key:
        reason = "请先在 AI 服务中保存通义千问（百炼）API Key"
        state = "needs_configuration"
    elif not enabled:
        reason = "通义千问服务当前已停用"
        state = "needs_configuration"
    elif not endpoint_ok:
        reason = "通义千问 Base URL 不是百炼北京地域官方地址"
        state = "invalid"
    elif not authorized:
        reason = "需要单独授权此云端转写能力；每次只上传当时项目的音频"
        state = "authorization_required"
    else:
        reason = "已授权此云端转写能力；授权会保留至撤销，每次只上传当时项目的音频"
        state = "ready"
    return {
        "model_id": FUN_ASR_MODEL_ID,
        "ready": ready,
        "source": "dashscope",
        "state": state,
        "download_required": False,
        "download_bytes": 0,
        "error": "" if ready else reason,
        "reason": reason,
        "provider_id": "dashscope",
        "cloud": True,
        "uploads_audio": True,
    }


def _wav_chunks(task_id: str, audio_path: str) -> tuple[float, Iterator[tuple[float, bytes]]]:
    try:
        source = wave.open(audio_path, "rb")
    except (OSError, wave.Error) as exc:
        raise CloudAsrError(
            f"无法读取待转写 WAV：{exc}",
            "AUDIO_INVALID",
            "请重新提取音频",
        ) from exc
    channels = source.getnchannels()
    sample_width = source.getsampwidth()
    sample_rate = source.getframerate()
    compression = source.getcomptype()
    frame_count = source.getnframes()
    if channels != 1 or sample_width != 2 or sample_rate != 16_000 or compression != "NONE":
        source.close()
        raise CloudAsrError(
            "Fun-Realtime-ASR 字幕模式需要 16kHz 16-bit PCM 单声道 WAV",
            "AUDIO_FORMAT",
            "请重新提取音频",
        )
    duration = frame_count / 16_000.0

    def generate() -> Iterator[tuple[float, bytes]]:
        offset_frames = 0
        try:
            while True:
                task_manager.checkpoint(task_id)
                raw = source.readframes(_MAX_CHUNK_FRAMES)
                if not raw:
                    break
                output = io.BytesIO()
                with wave.open(output, "wb") as chunk:
                    chunk.setnchannels(1)
                    chunk.setsampwidth(2)
                    chunk.setframerate(16_000)
                    chunk.writeframes(raw)
                yield offset_frames / 16_000.0, output.getvalue()
                offset_frames += len(raw) // 2
        finally:
            source.close()

    return duration, generate()


def _segment_from_payload(payload: dict, offset: float) -> CloudAsrSegment | None:
    if payload.get("code") or (
        payload.get("message") and not payload.get("output")
    ):
        raise CloudAsrError(
            f"百炼转写失败：{payload.get('message') or payload.get('code')}",
            "CLOUD_ASR_FAILED",
            "请检查百炼模型权限、余额和地域后重试",
        )
    output = payload.get("output") or {}
    sentence = output.get("sentence") or {}
    if not sentence or not sentence.get("sentence_end"):
        return None
    text = str(sentence.get("text") or "").strip()
    if not text:
        return None
    begin_ms = float(sentence.get("begin_time") or 0)
    end_ms = float(sentence.get("end_time") or begin_ms + 50)
    start = offset + begin_ms / 1000.0
    end = max(start + 0.05, offset + end_ms / 1000.0)
    words = []
    for item in sentence.get("words") or []:
        word_text = f"{item.get('text') or ''}{item.get('punctuation') or ''}"
        word_start = offset + float(item.get("begin_time") or 0) / 1000.0
        word_end = offset + float(item.get("end_time") or 0) / 1000.0
        if word_text and word_end > word_start:
            words.append({"text": word_text, "start": word_start, "end": word_end})
    return CloudAsrSegment(start=start, end=end, text=text, words=tuple(words))


def _request_chunk(
    task_id: str,
    client: httpx.Client,
    endpoint: str,
    api_key: str,
    offset: float,
    wav_bytes: bytes,
) -> Iterator[CloudAsrSegment]:
    payload = {
        "model": FUN_ASR_MODEL_ID,
        "input": {
            "messages": [{
                "role": "user",
                "content": [{
                    "audio": "data:audio/wav;base64,"
                    + base64.b64encode(wav_bytes).decode("ascii"),
                }],
            }],
        },
        "parameters": {"format": "wav", "vad_enabled": True},
        "resources": [],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-DashScope-SSE": "enable",
    }
    task_manager.checkpoint(task_id)
    try:
        with client.stream("POST", endpoint, headers=headers, json=payload) as response:
            if response.status_code >= 400:
                response.read()
                detail = str(response.text or "")[:500]
                code = (
                    "CLOUD_AUTH_FAILED"
                    if response.status_code in {401, 403}
                    else "CLOUD_RATE_LIMITED"
                    if response.status_code == 429
                    else "CLOUD_ASR_FAILED"
                )
                raise CloudAsrError(
                    f"百炼转写请求失败（HTTP {response.status_code}）：{detail}",
                    code,
                    "请检查百炼密钥、模型权限、余额和网络后重试",
                    recoverable=response.status_code not in {401, 403},
                )
            seen: set[tuple] = set()
            for line in response.iter_lines():
                task_manager.checkpoint(task_id)
                value = line.strip()
                if not value or value.startswith(("id:", "event:", ":")):
                    continue
                if value.startswith("data:"):
                    value = value[5:].strip()
                if not value.startswith("{"):
                    continue
                try:
                    event = json.loads(value)
                except json.JSONDecodeError:
                    continue
                segment = _segment_from_payload(event, offset)
                if segment is None:
                    continue
                identity = (round(segment.start, 3), round(segment.end, 3), segment.text)
                if identity in seen:
                    continue
                seen.add(identity)
                yield segment
    except CloudAsrError:
        raise
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise CloudAsrError(
            f"连接阿里云百炼失败：{exc}",
            "CLOUD_NETWORK_UNREACHABLE",
            "请检查网络或代理后重试；不会自动切换到其他模型",
        ) from exc


def create_fun_asr_session(
    task_id: str,
    audio_path: str,
    language: str,
) -> CloudAsrSession:
    if not _authorization_granted():
        raise CloudAsrError(
            "尚未授权 Fun-Realtime-ASR 上传当前项目音频",
            "CLOUD_AUTHORIZATION_REQUIRED",
            "请在转写模型中心阅读说明并授权",
            recoverable=False,
        )
    provider = get_provider("dashscope", include_secret=True)
    api_key = str(provider.get("api_key") or "")
    if not api_key or not provider.get("enabled", True):
        raise CloudAsrError(
            "通义千问（百炼）API Key 尚未配置或服务已停用",
            "CLOUD_PROVIDER_NOT_CONFIGURED",
            "请先在 AI 服务中保存百炼 API Key",
            recoverable=False,
        )
    endpoint = _endpoint(str(provider.get("base_url") or ""))
    audio_duration, chunks = _wav_chunks(task_id, audio_path)

    def generate() -> Iterator[CloudAsrSegment]:
        timeout = httpx.Timeout(connect=20.0, read=240.0, write=60.0, pool=20.0)
        with httpx.Client(timeout=timeout) as client:
            for offset, wav_bytes in chunks:
                yield from _request_chunk(
                    task_id, client, endpoint, api_key, offset, wav_bytes,
                )

    normalized = (language or "auto").lower()
    return CloudAsrSession(
        segments=generate(),
        audio_duration=audio_duration,
        detected_language=normalized or "auto",
    )
