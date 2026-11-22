"""Regression coverage for reusable model/media state and workflow reliability."""
import time
import uuid
from pathlib import Path

import pytest

from app.models import database
from app.services.model_pool import ModelPool, model_identity
from app.services.transcription_buffer import DraftWriter


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "isolated.db")
    database.init_db()
    db = database.get_db()
    db.execute(
        "INSERT INTO projects(id,title,source_type,created_at,updated_at) VALUES ('p','QA','local','now','now')"
    )
    db.execute(
        "INSERT INTO segments(id,project_id,idx,start,end,raw_text,clean_text) VALUES ('old','p',1,0,2,'raw','Initial line')"
    )
    db.execute(
        "INSERT INTO transcription_runs(id,project_id,model,status,started_at) VALUES ('run','p','small','running','now')"
    )
    db.commit()
    db.close()
    return "p"


def test_pool_reuses_releases_on_pressure_and_never_reuses_changed_file(tmp_path):
    pressure = [False]
    pool = ModelPool(idle_seconds=300, pressure_check=lambda: pressure[0])
    model = tmp_path / "model.bin"
    model.write_bytes(b"first")
    key = model_identity(str(model), "cpu", "int8")
    with pool.lease(key, object) as (first, reused):
        assert not reused
    with pool.lease(key, object) as (second, reused):
        assert reused and first is second
    model.write_bytes(b"changed content")
    with pool.lease(model_identity(str(model), "cpu", "int8"), object) as (
        third,
        reused,
    ):
        assert not reused and third is not first
    pressure[0] = True
    pool._expire(pool._generation)
    assert pool._model is None
    pool.clear()


def test_pool_idle_expiry_and_exception_release():
    pool = ModelPool(idle_seconds=0.01, pressure_check=lambda: False)
    with pytest.raises(RuntimeError):
        with pool.lease(("a",), object):
            raise RuntimeError("cancel")
    time.sleep(0.03)
    assert pool._model is None
    pool.clear()


def test_draft_batch_flushes_on_cancellation_without_open_transaction(project):
    def row(i):
        return (str(uuid.uuid4()), "run", project, i, float(i), float(i + 1), "draft", "[]")

    with pytest.raises(RuntimeError):
        with DraftWriter(batch_size=8, interval=60) as writer:
            writer.append(row(1))
            assert not writer.connection.in_transaction
            writer.append(row(2))
            raise RuntimeError("cancel")
    db = database.get_db()
    assert db.execute("SELECT COUNT(*) FROM transcription_segments").fetchone()[0] == 2
    db.close()


def test_real_faster_whisper_word_records_are_supported():
    from types import SimpleNamespace

    from faster_whisper.transcribe import Word

    from app.services.transcriber import _segment_word_timings

    segment = SimpleNamespace(
        words=[Word(start=0.1, end=0.5, word=" Hello", probability=0.99)]
    )
    assert _segment_word_timings(segment, 2) == [
        {"text": " Hello", "start": 2.1, "end": 2.5}
    ]
    assert (
        _segment_word_timings(
            SimpleNamespace(words=[{"word": "ok", "start": 0, "end": 1}])
        )[0]["text"]
        == "ok"
    )


def test_stage_scheduler_overlaps_preparation_but_serializes_inference(project):
    from threading import Event

    from app.utils.task_manager import TaskManager

    manager = TaskManager(max_workers=3)
    inference_entered, release, prepared, second_entered = (
        Event(),
        Event(),
        Event(),
        Event(),
    )
    one = manager.create_task(project, "workflow")
    two = manager.create_task(project, "workflow")

    def first(task_id):
        with manager.resource_slot(task_id, "ml"):
            inference_entered.set()
            assert release.wait(3)

    def second(task_id):
        with manager.resource_slot(task_id, "ffmpeg"):
            prepared.set()
        with manager.resource_slot(task_id, "ml"):
            second_entered.set()

    try:
        manager.run_background(one, first)
        assert inference_entered.wait(2)
        manager.run_background(two, second)
        assert prepared.wait(2)
        assert not second_entered.is_set()
        release.set()
        assert second_entered.wait(2)
    finally:
        release.set()
        manager.shutdown()


def test_audio_cache_checks_source_track_range_and_output(project, tmp_path, monkeypatch):
    import shutil
    import subprocess
    import wave

    from app.services import audio_extractor
    from app.utils.task_manager import TaskManager

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg unavailable")
    video = tmp_path / "two-tracks.mkv"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=3",
            "-map",
            "0:a",
            "-map",
            "1:a",
            "-c:a",
            "pcm_s16le",
            str(video),
        ],
        check=True,
    )
    manager = TaskManager()
    task = manager.create_task(project, "extract_audio")
    monkeypatch.setattr(audio_extractor, "task_manager", manager)
    monkeypatch.setattr(audio_extractor, "AUDIO_DIR", tmp_path / "audio")
    try:
        output = Path(audio_extractor.extract_audio(task, str(video), project, 0, 0, 1))
        stamp = output.stat().st_mtime_ns
        first = output.read_bytes()
        audio_extractor.extract_audio(task, str(video), project, 0, 0, 1)
        assert output.stat().st_mtime_ns == stamp
        assert manager.get_task(task)["details"]["audio_cache_hit"]
        audio_extractor.extract_audio(task, str(video), project, 1, 0, 1)
        assert output.read_bytes() != first
        audio_extractor.extract_audio(task, str(video), project, 1, 1, 3)
        with wave.open(str(output)) as reader:
            assert abs(reader.getnframes() / 16000 - 2) < 0.01
        output.write_bytes(b"invalid cached file")
        audio_extractor.extract_audio(task, str(video), project, 1, 1, 3)
        assert not manager.get_task(task)["details"]["audio_cache_hit"]
        video.touch()
        audio_extractor.extract_audio(task, str(video), project, 1, 1, 3)
        assert not manager.get_task(task)["details"]["audio_cache_hit"]
    finally:
        manager.shutdown()


def test_workflow_retry_reuses_completed_inference_and_text_steps(
    project, tmp_path, monkeypatch
):
    from app.api import projects
    from app.utils.task_manager import TaskManager

    audio = tmp_path / "prepared.wav"
    audio.write_bytes(b"prepared")
    video = tmp_path / "source.mp4"
    video.write_bytes(b"source")
    db = database.get_db()
    db.execute(
        "UPDATE projects SET video_path=?,audio_path=? WHERE id='p'",
        (str(video), str(audio)),
    )
    db.commit()
    db.close()
    manager = TaskManager()
    monkeypatch.setattr(projects, "task_manager", manager)
    calls = []
    monkeypatch.setattr(projects, "_do_extract_audio", lambda *a, **k: calls.append("prepare"))
    monkeypatch.setattr(projects, "_do_transcribe", lambda *a, **k: calls.append("transcribe"))
    monkeypatch.setattr(projects, "_do_clean", lambda *a, **k: calls.append("clean"))

    def translate(*a, **k):
        calls.append("translate")
        if calls.count("translate") == 1:
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(projects, "_do_translate", translate)
    options = dict(enable_clean=True, enable_translate=True, text_processing_consent=True)
    task = manager.create_task(project, "workflow")
    try:
        with pytest.raises(RuntimeError):
            projects._do_workflow(task, project, "small", "en", None, "cpu", options)
        prior = manager.get_task(task)["details"]
        assert prior["stages"] == {
            "download": "success",
            "extract_audio": "success",
            "transcribe": "success",
            "clean": "success",
            "translate": "failed",
        }
        next_task = manager.create_task(project, "workflow")
        projects._do_workflow(next_task, project, "small", "en", None, "cpu", options, prior)
        assert calls == ["prepare", "transcribe", "clean", "translate", "prepare", "translate"]
        assert manager.get_task(next_task)["details"]["stages"]["translate"] == "success"
    finally:
        manager.shutdown()


def test_cancelled_worker_finalizes_run_and_flushes_drafts(project):
    from threading import Event

    from app.utils.task_manager import TaskManager

    manager = TaskManager()
    task = manager.create_task(project, "workflow")
    entered = Event()

    def worker(task_id):
        db = database.get_db()
        db.execute(
            "UPDATE transcription_runs SET task_id=?,status='running' WHERE id='run'",
            (task_id,),
        )
        db.commit()
        db.close()
        manager.update_task(
            task_id,
            details={
                "stages": {"download": "success", "transcribe": "running"},
                "is_generating_segments": True,
            },
        )
        entered.set()
        while True:
            manager.checkpoint(task_id)
            time.sleep(0.01)

    try:
        future = manager.run_background(task, worker)
        assert entered.wait(2)
        manager.cancel_task(task)
        future.result(timeout=2)
        state = manager.get_task(task)
        assert state["details"]["stages"]["transcribe"] == "cancelled"
        assert (
            state["details"]["worker_stopped"]
            and not state["details"]["is_generating_segments"]
        )
        db = database.get_db()
        assert (
            db.execute("SELECT status FROM transcription_runs WHERE id='run'").fetchone()[0]
            == "cancelled"
        )
        db.close()
    finally:
        manager.shutdown()
