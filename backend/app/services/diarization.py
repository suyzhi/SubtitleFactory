"""Offline sherpa-onnx speaker diarization and subtitle mapping."""

from __future__ import annotations

import time
import uuid
import wave
from array import array

from ..models.database import get_db
from ..models.schemas import SegmentOperationItem, SegmentOperationRequest
from ..utils.task_manager import task_manager
from .editor import execute_operation


COLORS = ("#5b8cff", "#e46f91", "#55b98f", "#d99c48", "#9b78df", "#4da9c7")


def _load_audio(path: str):
    with wave.open(path, "rb") as source:
        if source.getsampwidth() != 2 or source.getnchannels() != 1:
            raise ValueError("说话人识别需要 16-bit 单声道 WAV")
        samples = array("h"); samples.frombytes(source.readframes(source.getnframes()))
        return [value / 32768 for value in samples], source.getframerate()


def _run_sherpa(audio_path: str, segmentation_model: str, embedding_model: str, num_speakers: int | None):
    try:
        import sherpa_onnx
    except ImportError as error:
        raise RuntimeError("当前运行包缺少 sherpa-onnx 说话人识别模块") from error
    segmentation = sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
        pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
            model=segmentation_model,
        ),
    )
    embedding = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=embedding_model)
    clustering = sherpa_onnx.FastClusteringConfig(
        num_clusters=num_speakers or -1, threshold=0.5,
    )
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=segmentation, embedding=embedding, clustering=clustering,
        min_duration_on=0.3, min_duration_off=0.5,
    )
    if not config.validate():
        raise RuntimeError("说话人模型配置无效，请重新选择分割与嵌入模型")
    diarizer = sherpa_onnx.OfflineSpeakerDiarization(config)
    samples, sample_rate = _load_audio(audio_path)
    if sample_rate != diarizer.sample_rate:
        raise ValueError(f"说话人识别要求 {diarizer.sample_rate}Hz 音频，当前为 {sample_rate}Hz")
    result = diarizer.process(samples)
    if hasattr(result, "sort_by_start_time"):
        result = result.sort_by_start_time()
    return [{"start": float(item.start), "end": float(item.end), "speaker": int(item.speaker)} for item in result]


def diarize_project(
    task_id: str, project_id: str, segmentation_model: str,
    embedding_model: str, num_speakers: int | None = None,
):
    db = get_db()
    project = db.execute("SELECT audio_path,edit_revision FROM projects WHERE id=?", (project_id,)).fetchone()
    db.close()
    if not project or not project["audio_path"]:
        raise FileNotFoundError("项目音频不存在")
    task_manager.update_task(task_id, step="speaker_diarization", progress=10, message="正在本地分析说话人")
    turns = _run_sherpa(project["audio_path"], segmentation_model, embedding_model, num_speakers)
    labels = sorted({turn["speaker"] for turn in turns})
    db = get_db(); speaker_ids = {}
    try:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        existing = {row["external_key"]: row["id"] for row in db.execute(
            "SELECT id,external_key FROM speakers WHERE project_id=?", (project_id,)
        ) if row["external_key"] is not None}
        for label in labels:
            identifier = existing.get(str(label)) or str(uuid.uuid4()); speaker_ids[label] = identifier
            db.execute("""INSERT INTO speakers(id,project_id,name,color,external_key,created_at,updated_at)
                          VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at""",
                       (identifier, project_id, f"说话人 {label + 1}", COLORS[label % len(COLORS)], str(label), now, now))
        db.commit()
        segments = db.execute("SELECT * FROM segments WHERE project_id=? ORDER BY idx", (project_id,)).fetchall()
    finally: db.close()
    items = []; uncertain = []
    for segment in segments:
        overlaps = {}
        duration = max(0.001, float(segment["end"]) - float(segment["start"]))
        for turn in turns:
            overlap = max(0, min(float(segment["end"]), turn["end"]) - max(float(segment["start"]), turn["start"]))
            overlaps[turn["speaker"]] = overlaps.get(turn["speaker"], 0) + overlap
        if overlaps:
            label, amount = max(overlaps.items(), key=lambda item: item[1])
            if amount / duration >= 0.55:
                items.append(SegmentOperationItem(index=segment["idx"], speaker_id=speaker_ids[label]))
            else:
                items.append(SegmentOperationItem(index=segment["idx"], speaker_id=None))
            if amount / duration < 0.75 or len([value for value in overlaps.values() if value > duration * .2]) > 1:
                uncertain.append(segment)
        else:
            items.append(SegmentOperationItem(index=segment["idx"], speaker_id=None))
    result = execute_operation(project_id, SegmentOperationRequest(
        expected_revision=int(project["edit_revision"] or 0), operation="update_many", items=items,
    ))
    db = get_db(); now = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        db.execute("DELETE FROM quality_issues WHERE project_id=? AND rule_id='speaker_uncertain'", (project_id,))
        for segment in uncertain:
            db.execute("""INSERT INTO quality_issues
                (id,project_id,segment_id,rule_id,severity,fingerprint,message,suggestion,status,details_json,created_at,updated_at)
                VALUES (?,?,?,'speaker_uncertain','warning',?,'说话人识别置信度较低','请试听并确认说话人','open','{}',?,?)""",
                (str(uuid.uuid4()), project_id, segment["id"], str(uuid.uuid4()), now, now))
        db.commit()
    finally: db.close()
    task_manager.update_task(task_id, step="speaker_done", progress=100, message=f"识别到 {len(labels)} 位说话人")
    return result
