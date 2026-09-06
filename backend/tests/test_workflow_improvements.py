"""Regression coverage for candidate safety and reusable model/media state."""
import json
import time
import uuid
from pathlib import Path

import pytest

from app.models import database
from app.services.editor import EditorServiceError, history_step, import_segment_snapshot
from app.services.model_pool import ModelPool, model_identity
from app.services.transcription_buffer import DraftWriter


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'isolated.db')
    database.init_db()
    db = database.get_db()
    db.execute("INSERT INTO projects(id,title,source_type,created_at,updated_at) VALUES ('p','QA','local','now','now')")
    db.execute("INSERT INTO segments(id,project_id,idx,start,end,raw_text,clean_text,locked) VALUES ('old','p',1,0,2,'raw','Human correction',1)")
    db.execute("INSERT INTO transcription_runs(id,project_id,model,status,started_at,result_json) VALUES ('run','p','small','candidate','now',?)", (json.dumps([dict(start=0,end=2,text='New candidate')]),))
    db.commit(); db.close()
    return 'p'

def test_candidate_acceptance_is_atomic_undoable_and_keeps_metadata(project):
    result = import_segment_snapshot(project, 0, [], candidate_run_id='run')
    assert result['segments'][0]['clean_text'] == 'New candidate'
    db = database.get_db()
    assert db.execute('SELECT transcription_run_id FROM segments').fetchone()[0] == 'run'
    db.close()
    assert result['segments'][0]['source_stage'] == 'postprocessed'
    undo = history_step(project, 1, 'undo')
    assert undo['segments'][0]['clean_text'] == 'Human correction'
    assert undo['segments'][0]['locked']
    redo = history_step(project, 2, 'redo')
    assert redo['segments'][0]['clean_text'] == 'New candidate'
    db = database.get_db()
    assert db.execute('SELECT transcription_run_id FROM segments').fetchone()[0] == 'run'
    db.close()
    with pytest.raises(EditorServiceError):
        import_segment_snapshot(project, 3, [], candidate_run_id='run')

def test_candidate_rejects_stale_revision_and_saved_draft(project):
    with pytest.raises(EditorServiceError, match='字幕已发生变化'):
        import_segment_snapshot(project, 99, [], candidate_run_id='run')
    db = database.get_db()
    db.execute("INSERT INTO segment_drafts(project_id,base_revision,draft_json,updated_at) VALUES ('p',0,'[]','now')")
    db.commit();db.close()
    with pytest.raises(EditorServiceError) as error:
        import_segment_snapshot(project, 0, [], candidate_run_id='run')
    assert error.value.code == 'DRAFT_PENDING'
    db = database.get_db()
    assert db.execute("SELECT clean_text FROM segments").fetchone()[0] == 'Human correction'
    assert db.execute("SELECT status FROM transcription_runs").fetchone()[0] == 'candidate'
    db.close()

def test_pool_reuses_releases_on_pressure_and_never_reuses_changed_file(tmp_path):
    pressure = [False]
    pool = ModelPool(idle_seconds=300, pressure_check=lambda: pressure[0])
    model = tmp_path / 'model.bin'; model.write_bytes(b'first')
    key = model_identity(str(model), 'cpu','int8')
    with pool.lease(key, object) as (first,reused): assert not reused
    with pool.lease(key, object) as (second,reused): assert reused and first is second
    model.write_bytes(b'changed content')
    with pool.lease(model_identity(str(model),'cpu','int8'), object) as (third,reused): assert not reused and third is not first
    pressure[0] = True
    pool._expire(pool._generation)
    assert pool._model is None
    pool.clear()

def test_pool_idle_expiry_and_exception_release():
    pool = ModelPool(idle_seconds=.01, pressure_check=lambda: False)
    with pytest.raises(RuntimeError):
        with pool.lease(('a',),object): raise RuntimeError('cancel')
    time.sleep(.03)
    assert pool._model is None
    pool.clear()

def test_draft_batch_flushes_on_cancellation_without_open_transaction(project):
    def row(i):
        return (str(uuid.uuid4()),'run',project,i,float(i),float(i+1),'draft','[]')
    with pytest.raises(RuntimeError):
        with DraftWriter(batch_size=8,interval=60) as writer:
            writer.append(row(1))
            assert not writer.connection.in_transaction
            writer.append(row(2))
            raise RuntimeError('cancel')
    db = database.get_db()
    assert db.execute("SELECT COUNT(*) FROM transcription_segments").fetchone()[0] == 2
    db.close()


def test_real_faster_whisper_word_records_are_supported():
    from types import SimpleNamespace

    from faster_whisper.transcribe import Word

    from app.services.transcriber import _segment_word_timings
    segment = SimpleNamespace(words=[Word(start=.1,end=.5,word=' Hello',probability=.99)])
    assert _segment_word_timings(segment,2) == [{'text':' Hello','start':2.1,'end':2.5}]
    assert _segment_word_timings(SimpleNamespace(words=[{'word':'ok','start':0,'end':1}]))[0]['text'] == 'ok'


def test_stage_scheduler_overlaps_preparation_but_serializes_inference(project):
    from threading import Event

    from app.utils.task_manager import TaskManager
    manager = TaskManager(max_workers=3)
    inference_entered, release, prepared, second_entered = Event(), Event(), Event(), Event()
    one=manager.create_task(project,'workflow');two=manager.create_task(project,'workflow')
    def first(task_id):
        with manager.resource_slot(task_id,'ml'):
            inference_entered.set()
            assert release.wait(3)
    def second(task_id):
        with manager.resource_slot(task_id,'ffmpeg'):prepared.set()
        with manager.resource_slot(task_id,'ml'):second_entered.set()
    try:
        manager.run_background(one,first)
        assert inference_entered.wait(2)
        manager.run_background(two,second)
        assert prepared.wait(2)
        assert not second_entered.is_set()
        release.set()
        assert second_entered.wait(2)
    finally:
        release.set();manager.shutdown()


def test_audio_cache_checks_source_track_range_and_output(project,tmp_path,monkeypatch):
    import shutil
    import subprocess
    import wave

    from app.services import audio_extractor
    from app.utils.task_manager import TaskManager
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg: pytest.skip('FFmpeg unavailable')
    video=tmp_path/'two-tracks.mkv'
    subprocess.run([ffmpeg,'-v','error','-f','lavfi','-i','sine=frequency=440:duration=3','-f','lavfi','-i','sine=frequency=880:duration=3','-map','0:a','-map','1:a','-c:a','pcm_s16le',str(video)],check=True)
    manager=TaskManager();task=manager.create_task(project,'extract_audio')
    monkeypatch.setattr(audio_extractor,'task_manager',manager)
    monkeypatch.setattr(audio_extractor,'AUDIO_DIR',tmp_path/'audio')
    try:
        output=Path(audio_extractor.extract_audio(task,str(video),project,0,0,1))
        stamp=output.stat().st_mtime_ns; first=output.read_bytes()
        audio_extractor.extract_audio(task,str(video),project,0,0,1)
        assert output.stat().st_mtime_ns == stamp
        assert manager.get_task(task)['details']['audio_cache_hit']
        audio_extractor.extract_audio(task,str(video),project,1,0,1)
        assert output.read_bytes()!=first
        audio_extractor.extract_audio(task,str(video),project,1,1,3)
        with wave.open(str(output)) as reader: assert abs(reader.getnframes()/16000-2)<.01
        output.write_bytes(b'invalid cached file')
        audio_extractor.extract_audio(task,str(video),project,1,1,3)
        assert not manager.get_task(task)['details']['audio_cache_hit']
        video.touch()
        audio_extractor.extract_audio(task,str(video),project,1,1,3)
        assert not manager.get_task(task)['details']['audio_cache_hit']
    finally:manager.shutdown()


def test_workflow_retry_reuses_completed_inference_and_text_steps(project,tmp_path,monkeypatch):
    from app.api import projects
    from app.utils.task_manager import TaskManager
    audio=tmp_path/'prepared.wav';audio.write_bytes(b'prepared')
    video=tmp_path/'source.mp4';video.write_bytes(b'source')
    db=database.get_db();db.execute("UPDATE projects SET video_path=?,audio_path=? WHERE id='p'",(str(video),str(audio)));db.commit();db.close()
    manager=TaskManager();monkeypatch.setattr(projects,'task_manager',manager)
    calls=[]
    monkeypatch.setattr(projects,'_do_extract_audio',lambda *a,**k:calls.append('prepare'))
    monkeypatch.setattr(projects,'_do_transcribe',lambda *a,**k:calls.append('transcribe'))
    monkeypatch.setattr(projects,'_do_clean',lambda *a,**k:calls.append('clean'))
    def translate(*a,**k):
        calls.append('translate')
        if calls.count('translate')==1:raise RuntimeError('provider unavailable')
    monkeypatch.setattr(projects,'_do_translate',translate)
    options=dict(enable_clean=True,enable_translate=True,text_processing_consent=True)
    task=manager.create_task(project,'workflow')
    try:
        with pytest.raises(RuntimeError):projects._do_workflow(task,project,'small','en',None,'cpu',options)
        prior=manager.get_task(task)['details']
        assert prior['stages']=={'download':'success','extract_audio':'success','transcribe':'success','clean':'success','translate':'failed'}
        next_task=manager.create_task(project,'workflow')
        projects._do_workflow(next_task,project,'small','en',None,'cpu',options,prior)
        assert calls==['prepare','transcribe','clean','translate','prepare','translate']
        assert manager.get_task(next_task)['details']['stages']['translate']=='success'
    finally:manager.shutdown()


def test_library_readiness_uses_files_and_pagination_is_stable(project, tmp_path):
    from app.api.projects import list_projects
    media = tmp_path / 'video.mp4'; media.write_bytes(b'fixture')
    db = database.get_db()
    for i in range(5):
        db.execute("INSERT INTO projects(id,title,source_type,video_path,created_at,updated_at) VALUES (?, 'same', 'local', ?, 'now','now')", (f'page-{i}', str(media) if i < 3 else str(tmp_path/'missing.mp4')))
    db.commit(); db.close()
    first = list_projects(page=1,page_size=2,readiness='transcribe')
    second = list_projects(page=2,page_size=2,readiness='transcribe')
    assert first['total'] == 3 and first['pages'] == 2
    assert [r['id'] for r in first['projects'] + second['projects']] == ['page-0','page-1','page-2']
    assert list_projects(readiness='media')['total'] == 2
    assert list_projects(readiness='subtitles')['projects'][0]['id'] == project


def test_font_fallback_does_not_match_signpainter():
    import sys
    if sys.platform != 'darwin': pytest.skip('macOS system font regression')
    from PIL import ImageFont

    from app.services.clips import _font_path
    path = _font_path('Inter, "Helvetica Neue", sans-serif', False)
    assert ImageFont.truetype(path,22).getname()[0] == 'Helvetica Neue'


def test_cancelled_worker_finalizes_run_and_flushes_drafts(project):
    from threading import Event

    from app.utils.task_manager import TaskManager
    manager=TaskManager();task=manager.create_task(project,'workflow'); entered=Event()
    def worker(task_id):
        db=database.get_db();db.execute("UPDATE transcription_runs SET task_id=?,status='running' WHERE id='run'",(task_id,));db.commit();db.close()
        manager.update_task(task_id,details={'stages':{'download':'success','transcribe':'running'},'is_generating_segments':True})
        entered.set()
        while True:
            manager.checkpoint(task_id);time.sleep(.01)
    try:
        future=manager.run_background(task,worker)
        assert entered.wait(2)
        manager.cancel_task(task);future.result(timeout=2)
        state=manager.get_task(task)
        assert state['details']['stages']['transcribe']=='cancelled'
        assert state['details']['worker_stopped'] and not state['details']['is_generating_segments']
        db=database.get_db();assert db.execute("SELECT status FROM transcription_runs WHERE id='run'").fetchone()[0]=='cancelled';db.close()
    finally:manager.shutdown()


@pytest.mark.parametrize('status', ['cancelled', 'failed'])
def test_finalized_partial_transcription_is_reviewable_and_undoable(project, status):
    from app.api.projects import transcription_candidate, transcription_candidates
    db = database.get_db()
    db.execute("UPDATE transcription_runs SET status=?,finished_at=NULL,result_json=NULL WHERE id='run'", (status,))
    db.execute("""INSERT INTO transcription_segments(id,run_id,project_id,idx,start,end,text,is_draft)
                  VALUES ('partial','run',?,1,3,4,'Durable partial words',1)""", (project,))
    db.commit()
    assert transcription_candidates(project)['candidates'] == []
    with pytest.raises(EditorServiceError):
        import_segment_snapshot(project, 0, [], candidate_run_id='run')
    db.execute("UPDATE transcription_runs SET finished_at='now' WHERE id='run'")
    db.commit(); db.close()
    item = transcription_candidates(project)['candidates'][0]
    assert item['status'] == status and item['segments_count'] == 1
    result = transcription_candidate(project, 'run')
    assert result['partial'] and result['segments'][0]['text'] == 'Durable partial words'
    adopted = import_segment_snapshot(project, 0, [], candidate_run_id='run')
    assert adopted['segments'][0]['clean_text'] == 'Durable partial words'
    assert adopted['segments'][0]['source_stage'] == 'recovered_partial'
    assert history_step(project, adopted['revision'], 'undo')['segments'][0]['clean_text'] == 'Human correction'
    db = database.get_db()
    assert db.execute("SELECT text FROM transcription_segments WHERE id='partial'").fetchone()[0] == 'Durable partial words'
    db.close()
