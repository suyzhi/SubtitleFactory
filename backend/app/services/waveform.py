"""Content-addressed multi-resolution waveform peak generation."""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
import wave
from array import array
from pathlib import Path

from ..models.database import get_db
from ..utils.config import PROJECTS_DIR


RESOLUTIONS = (1_000, 4_000, 16_000)


def audio_fingerprint(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mono_samples(source: wave.Wave_read) -> array:
    channels = source.getnchannels()
    sample_width = source.getsampwidth()
    if sample_width != 2:
        raise ValueError("波形仅支持 16-bit PCM WAV 音频")
    values = array("h")
    values.frombytes(source.readframes(source.getnframes()))
    if os.sys.byteorder != "little":
        values.byteswap()
    if channels == 1:
        return values
    mono = array("h")
    for offset in range(0, len(values), channels):
        frame = values[offset:offset + channels]
        mono.append(round(sum(frame) / len(frame)))
    return mono


def _peaks(samples: array, count: int) -> list[float]:
    if not samples:
        return []
    count = max(1, min(count, len(samples)))
    bucket = len(samples) / count
    result: list[float] = []
    for index in range(count):
        start = math.floor(index * bucket)
        end = max(start + 1, math.floor((index + 1) * bucket))
        peak = max(abs(value) for value in samples[start:end]) / 32768
        result.append(round(min(1.0, peak), 4))
    return result


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
