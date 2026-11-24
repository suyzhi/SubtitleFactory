"""Long-audio VAD streaming and sherpa-onnx recognizer sessions for managed models."""

from __future__ import annotations

import queue
import re
import threading
import time as time_module
import wave
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Iterator

from ...utils.task_manager import task_manager
from ..model_pool import model_identity, optimal_cpu_threads, sherpa_model_pool
from ..sherpa_catalog import MANAGED_SHERPA_BY_ID, ManagedSherpaModel
from .storage import (
    _ensure_vad,
    managed_model_dir,
    managed_model_ready,
    prepare_managed_model,
)
from .types import (
    _PUNCTUATION_ONLY,
    ManagedModelError,
    ManagedSegment,
    ManagedSession,
    facade_attr,
)


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
    root = facade_attr("managed_model_dir", managed_model_dir)(definition.id)
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
        "num_threads": optimal_cpu_threads(),
        "provider": provider,
        "debug": False,
    }
    if definition.adapter == "dolphin":
        return sherpa_onnx.OfflineRecognizer.from_dolphin_ctc(
            _find_path(paths, "model.int8.onnx"),
            _find_path(paths, "tokens.txt"),
            **common,
        )
    if definition.adapter == "omnilingual":
        return sherpa_onnx.OfflineRecognizer.from_omnilingual_asr_ctc(
            _find_path(paths, "model.int8.onnx"),
            _find_path(paths, "tokens.txt"),
            **common,
        )
    if definition.adapter == "qwen3":
        tokenizer = str(
            facade_attr("managed_model_dir", managed_model_dir)(definition.id) / "tokenizer"
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
            _find_path(paths, "model.int8.onnx"),
            _find_path(paths, "tokens.txt"),
            **common,
        )
    if definition.adapter == "fire_red_ctc":
        return sherpa_onnx.OfflineRecognizer.from_fire_red_asr_ctc(
            _find_path(paths, "model.int8.onnx"),
            _find_path(paths, "tokens.txt"),
            **common,
        )
    if definition.adapter == "telespeech":
        return sherpa_onnx.OfflineRecognizer.from_telespeech_ctc(
            _find_path(paths, "model.int8.onnx"),
            _find_path(paths, "tokens.txt"),
            **common,
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
            _find_path(paths, "model.int8.onnx"),
            _find_path(paths, "tokens.txt"),
            **common,
        )
    if definition.adapter == "nemo_ctc":
        return sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
            _find_path(paths, "model.int8.onnx"),
            _find_path(paths, "tokens.txt"),
            **common,
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
    timestamps = timestamps[: len(tokens)]
    timings: list[dict] = []
    for index, token in enumerate(tokens):
        text = str(token)
        if not text.strip():
            continue
        start = min(segment_end, segment_start + max(0.0, float(timestamps[index])))
        if index + 1 < len(timestamps):
            end = segment_start + max(
                float(timestamps[index + 1]), float(timestamps[index]) + 0.04
            )
        else:
            end = segment_end
        end = min(segment_end, max(start + 0.04, end))
        if end > start:
            timings.append(
                {
                    "text": text.replace("@@", ""),
                    "start": round(start, 4),
                    "end": round(end, 4),
                }
            )
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

    tm = facade_attr("task_manager", task_manager)
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
    frames_per_read = 16000 * 10
    with wave.open(audio_path, "rb") as audio:
        while True:
            tm.checkpoint(task_id)
            raw = audio.readframes(frames_per_read)
            eof = not raw
            if raw:
                samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
                pending = np.concatenate((pending, samples)) if pending.size else samples
                offset = 0
                limit = pending.size - window_size
                while offset <= limit:
                    vad.accept_waveform(pending[offset : offset + window_size])
                    offset += window_size
                pending = pending[offset:] if offset else pending
            else:
                if pending.size:
                    padded = np.zeros(window_size, dtype=np.float32)
                    padded[: pending.size] = pending
                    vad.accept_waveform(padded)
                    pending = np.empty(0, dtype=np.float32)
                vad.flush()
            while not vad.empty():
                tm.checkpoint(task_id)
                speech = vad.front
                start = float(speech.start) / 16000.0
                speech_samples = np.asarray(speech.samples, dtype=np.float32).copy()
                vad.pop()
                end = min(audio_duration, start + speech_samples.size / 16000.0)
                if end > start:
                    yield start, end, speech_samples
            if eof:
                break


def prefetch_vad_segments(
    source: Iterator[tuple[float, float, Any]],
    *,
    maxsize: int = 16,
    thread_name: str = "vad-prefetch",
) -> Iterator[tuple[float, float, Any]]:
    """Run Silero VAD ahead of ASR decoding on a bounded background thread."""
    sentinel = object()
    buffer: queue.Queue[Any] = queue.Queue(maxsize=maxsize)
    stop_event = threading.Event()
    error_holder: list[BaseException] = []

    def _put(item: Any) -> bool:
        while not stop_event.is_set():
            try:
                buffer.put(item, timeout=0.1)
                return True
            except queue.Full:
                continue
        return False

    def _producer() -> None:
        try:
            for item in source:
                if stop_event.is_set():
                    break
                if not _put(item):
                    break
        except BaseException as exc:
            error_holder.append(exc)
        finally:
            while True:
                try:
                    buffer.put(sentinel, timeout=0.1)
                    break
                except queue.Full:
                    if stop_event.is_set():
                        try:
                            buffer.get_nowait()
                        except queue.Empty:
                            pass

    worker = threading.Thread(target=_producer, daemon=True, name=thread_name)
    worker.start()
    try:
        while True:
            item = buffer.get()
            if item is sentinel:
                if error_holder:
                    raise error_holder[0]
                break
            yield item
    finally:
        stop_event.set()
        while True:
            try:
                buffer.get_nowait()
            except queue.Empty:
                break
        worker.join(timeout=2.0)


def _iter_vad_segments(
    task_id: str,
    audio_path: str,
    recognizer: Any,
    vad_path: Path,
    audio_duration: float,
    max_speech_seconds: float,
    timestamp_mode: str,
    lease_stack: ExitStack | None = None,
) -> Iterator[ManagedSegment]:
    tm = facade_attr("task_manager", task_manager)
    vad_iter = iter_vad_audio_segments(
        task_id,
        audio_path,
        vad_path,
        audio_duration,
        max_speech_seconds,
    )
    try:
        for start, end, speech_samples in prefetch_vad_segments(
            vad_iter, thread_name="sherpa-vad-prefetch"
        ):
            tm.checkpoint(task_id)
            stream = recognizer.create_stream()
            stream.accept_waveform(16000, speech_samples)
            recognizer.decode_stream(stream)
            result = stream.result
            text = re.sub(r"\s+", " ", str(result.text or "").replace("▁", " ")).strip()
            if not text or _PUNCTUATION_ONLY.fullmatch(text):
                continue
            yield ManagedSegment(
                start=start,
                end=end,
                text=text,
                timings=_timings_from_result(result, start, end, timestamp_mode),
            )
    finally:
        if lease_stack is not None:
            lease_stack.close()


def create_managed_session(
    task_id: str,
    audio_path: str,
    language: str,
    model_id: str,
    runtime: str,
    resources: ExitStack | None = None,
) -> ManagedSession:
    tm = facade_attr("task_manager", task_manager)
    is_ready = facade_attr("managed_model_ready", managed_model_ready)
    prepare_fn = facade_attr("prepare_managed_model", prepare_managed_model)
    ensure_vad_fn = facade_attr("_ensure_vad", _ensure_vad)

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
    if not is_ready(model_id):
        prepare_fn(task_id, model_id)
    vad_path = ensure_vad_fn(task_id)
    tm.update_task(
        task_id,
        step="loading_model",
        progress=24,
        message=f"正在加载 {definition.name}...",
    )
    provider = "coreml" if runtime == "coreml" else "cpu"
    owned_stack = ExitStack() if resources is None else None
    lease_target = resources if resources is not None else owned_stack
    assert lease_target is not None
    try:
        import sherpa_onnx

        lang_key = normalized if definition.adapter == "sense_voice" else ""
        pool_key = (
            "managed_sherpa",
            definition.id,
            definition.adapter,
            lang_key,
            optimal_cpu_threads(),
            id(sherpa_onnx.OfflineRecognizer),
            model_identity(
                str(facade_attr("managed_model_dir", managed_model_dir)(definition.id)),
                provider,
                "sherpa-onnx",
                files=_paths(definition).values(),
            ),
        )
        load_started = time_module.perf_counter()
        recognizer, reused = lease_target.enter_context(
            sherpa_model_pool.lease(
                pool_key,
                lambda: _create_recognizer(definition, provider, normalized),
            )
        )
        tm.update_task(
            task_id,
            details={
                "model_cache_hit": reused,
                "model_load_seconds": round(time_module.perf_counter() - load_started, 3),
            },
        )
        duration, sample_rate = _wave_info(audio_path)
        if sample_rate != 16000:
            raise ManagedModelError(
                "本地模型字幕模式需要 16kHz 音频",
                "AUDIO_FORMAT",
                "请重新提取音频",
            )
    except ManagedModelError:
        if owned_stack is not None:
            owned_stack.close()
        raise
    except Exception as exc:
        if owned_stack is not None:
            owned_stack.close()
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
            lease_stack=owned_stack.pop_all() if owned_stack is not None else None,
        ),
        audio_duration=duration,
        detected_language=normalized or "auto",
        device="Core ML" if runtime == "coreml" else "cpu",
        compute_type="int8 ONNX",
        model_label=definition.name,
    )
