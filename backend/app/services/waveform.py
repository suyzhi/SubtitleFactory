"""Content-addressed multi-resolution waveform peak generation."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
import wave
from functools import lru_cache
from pathlib import Path

import numpy as np

from ..models.database import get_db
from ..utils.config import PROJECTS_DIR

RESOLUTIONS = (1_000, 4_000, 16_000)


def audio_fingerprint(path: str) -> str:
    resolved = str(Path(path).resolve())
    stat = os.stat(resolved)
    return _cached_fingerprint(resolved, stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


_FINGERPRINT_SAMPLE_BYTES = 1024 * 1024


@lru_cache(maxsize=128)
def _cached_fingerprint(path: str, *identity: int) -> str:
    # The stat identity in the cache key already detects any in-place change.
    # Hashing three sampled windows keeps huge WAVs from being read end-to-end
    # on first request while still content-addressing distinct files.
    size = int(identity[2]) if len(identity) > 2 else os.path.getsize(path)
    digest = hashlib.sha256()
    # Fold the stat identity in: ``cache_path`` is derived from this digest, so
    # any size/mtime/ctime/inode change must yield a new digest even when the
    # change sits outside the sampled windows below.
    digest.update(",".join(str(int(value)) for value in identity).encode("ascii"))
    digest.update(str(size).encode("ascii"))
    with open(path, "rb") as source:
        if size <= 3 * _FINGERPRINT_SAMPLE_BYTES:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        else:
            middle = max(0, size // 2 - _FINGERPRINT_SAMPLE_BYTES // 2)
            for offset in (0, middle, size - _FINGERPRINT_SAMPLE_BYTES):
                source.seek(offset)
                digest.update(source.read(_FINGERPRINT_SAMPLE_BYTES))
    return digest.hexdigest()


def _mono_samples(source: wave.Wave_read) -> np.ndarray:
    """Read a 16-bit WAV as native int16 mono samples, downmixing in NumPy.

    Frames are consumed in bounded chunks so stereo/multichannel input never
    requires an int32 or float64 copy of the full file. The previous
    ``round(sum(frame) / len(frame))`` semantics are preserved exactly by
    dividing in float64 and using NumPy's round-half-to-even.
    """
    channels = source.getnchannels()
    if source.getsampwidth() != 2:
        raise ValueError("波形仅支持 16-bit PCM WAV 音频")
    total_frames = source.getnframes()
    mono = np.empty(total_frames, dtype=np.int16)
    chunk_frames = 1 << 20
    written = 0
    while written < total_frames:
        raw = source.readframes(min(chunk_frames, total_frames - written))
        if not raw:
            break
        values = np.frombuffer(raw, dtype="<i2")
        frames = values.size // channels
        if frames == 0:
            break
        if channels == 1:
            mono[written:written + frames] = values
        else:
            block = values.reshape(frames, channels)
            mixed = np.rint(block.sum(axis=1, dtype=np.int64) / channels)
            mono[written:written + frames] = mixed.astype(np.int16)
        written += frames
    if written == total_frames:
        return mono
    return mono[:written]


def _peaks(samples, count: int) -> list[float]:
    # Accepts either the native int16 ndarray returned by ``_mono_samples`` or a
    # legacy ``array('h')`` (kept for callers that build PCM directly).
    values = samples if isinstance(samples, np.ndarray) else np.frombuffer(samples, dtype=np.int16)
    if values.size == 0:
        return []
    count = max(1, min(count, values.size))
    # Reduce each bucket in native code; widen only the small result arrays so
    # -32768 cannot overflow on abs().
    starts = np.floor(np.arange(count) * (values.size / count)).astype(np.intp)
    high = np.maximum.reduceat(values, starts).astype(np.int32)
    low = np.minimum.reduceat(values, starts).astype(np.int32)
    peaks = np.maximum(high, -low) / 32768
    return [round(float(value), 4) for value in peaks]


def get_waveform(project_id: str, requested_points: int = 4_000) -> dict:
    db = get_db()
    try:
        project = db.execute(
            "SELECT audio_path,range_start FROM projects WHERE id=? AND deleted_at IS NULL", (project_id,)
        ).fetchone()
    finally:
        db.close()
    if not project:
        raise FileNotFoundError("项目不存在")
    audio_path = project["audio_path"]
    if not audio_path or not os.path.isfile(audio_path):
        raise FileNotFoundError("音频尚未提取")

    fingerprint = audio_fingerprint(audio_path)
    cache_dir = Path(PROJECTS_DIR) / project_id / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"waveform-{fingerprint[:20]}.json"
    if cache_path.is_file():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        with wave.open(audio_path, "rb") as source:
            rate = source.getframerate()
            frames = source.getnframes()
            samples = _mono_samples(source)
        payload = {
            "fingerprint": fingerprint,
            "duration": frames / max(rate, 1),
            "sample_rate": rate,
            "resolutions": {str(count): _peaks(samples, count) for count in RESOLUTIONS},
        }
        temporary = cache_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        temporary.replace(cache_path)
        db = get_db()
        try:
            db.execute(
                "DELETE FROM project_assets WHERE project_id=? AND kind='waveform'",
                (project_id,),
            )
            db.execute(
                """INSERT INTO project_assets
                   (id,project_id,kind,path,fingerprint,metadata_json,created_at)
                   VALUES (?,?,?,?,?,? ,datetime('now','localtime'))""",
                (str(uuid.uuid4()), project_id, "waveform", str(cache_path), fingerprint,
                 json.dumps({"resolutions": RESOLUTIONS})),
            )
            db.commit()
        finally:
            db.close()

    available = sorted(int(value) for value in payload["resolutions"])
    selected = min(available, key=lambda value: abs(value - requested_points))
    return {
        "fingerprint": payload["fingerprint"],
        "duration": payload["duration"],
        "offset": float(project["range_start"] or 0),
        "sample_rate": payload["sample_rate"],
        "points": selected,
        "peaks": payload["resolutions"][str(selected)],
    }
