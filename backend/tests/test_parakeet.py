import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import uuid
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from fastapi import HTTPException

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault(
    "SUBTITLE_FACTORY_DATA_DIR",
    tempfile.mkdtemp(prefix="subtitle-factory-parakeet-tests-"),
)

from app.models.database import get_db, init_db
from app.api.projects import start_transcribe
from app.services import parakeet_transcriber as parakeet
from app.services.parakeet_transcriber import (
    PARAKEET_MODEL_DIR_NAME,
    PARAKEET_MODEL_ID,
    PARAKEET_ONNX_MODEL_ID,
    CoreMLRuntime,
    ParakeetAssets,
    ParakeetSegment,
)
from app.services.transcriber import SUPPORTED_TRANSCRIPTION_MODELS, transcribe_audio
from app.utils.task_manager import TaskCancelled, task_manager


class ParakeetModelCacheTests(unittest.TestCase):
    def test_complete_cache_never_contacts_download_server(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            assets = parakeet._asset_paths(root)
            assets.model_dir.mkdir(parents=True)
            for name, minimum_size in parakeet.PARAKEET_REQUIRED_FILES.items():
                with (assets.model_dir / name).open("wb") as output:
                    output.truncate(minimum_size)
            with assets.vad.open("wb") as output:
                output.truncate(parakeet.SILERO_VAD_BYTES)

            with patch.object(
                parakeet,
                "_download_file",
                side_effect=AssertionError("cache hit must not download"),
            ):
                cached = parakeet.ensure_parakeet_assets("offline-test", root)

            self.assertEqual(cached.model_dir.resolve(), assets.model_dir.resolve())
            self.assertTrue(parakeet._model_cache_is_valid(cached))

    def test_first_use_install_is_fully_mocked_and_offline(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source_archive = root / "fixture.tar.bz2"
            fixture_files = {
                "encoder.int8.onnx": b"encoder",
                "decoder.int8.onnx": b"decoder",
                "joiner.int8.onnx": b"joiner",
                "tokens.txt": b"tokens",
            }
            with tarfile.open(source_archive, "w:bz2") as bundle:
                for name, content in fixture_files.items():
                    info = tarfile.TarInfo(f"{PARAKEET_MODEL_DIR_NAME}/{name}")
                    info.size = len(content)
                    bundle.addfile(info, io.BytesIO(content))
            archive_size = source_archive.stat().st_size

            def fake_download(_url, destination, expected_size, callback, checkpoint):
                checkpoint()
                if destination.name == "silero_vad.onnx":
                    destination.write_bytes(b"vad")
                else:
                    shutil.copyfile(source_archive, destination)
                callback(expected_size, expected_size, False)

            with patch.dict(
                parakeet.PARAKEET_REQUIRED_FILES,
                {name: 1 for name in fixture_files},
                clear=True,
            ), patch.object(
                parakeet, "PARAKEET_ARCHIVE_BYTES", archive_size
            ), patch.object(
                parakeet, "SILERO_VAD_BYTES", 3
            ), patch.object(
                parakeet, "_download_file", side_effect=fake_download
            ):
                installed = parakeet.ensure_parakeet_assets("offline-test", root / "cache")
                self.assertTrue(parakeet._model_cache_is_valid(installed))

            self.assertEqual(installed.tokens.read_bytes(), b"tokens")

    def test_archive_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / "bad.tar.bz2"
            with tarfile.open(archive, "w:bz2") as bundle:
                info = tarfile.TarInfo("../outside.txt")
                info.size = 3
                bundle.addfile(info, io.BytesIO(b"bad"))
            destination = root / "extract"
            destination.mkdir()
            with self.assertRaisesRegex(RuntimeError, "路径越界"):
                parakeet._safe_extract_tar(archive, destination)
            self.assertFalse((root / "outside.txt").exists())


class ParakeetInferenceAdapterTests(unittest.TestCase):
    def test_coreml_runtime_discovery_honors_environment_overrides(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            model = root / "model"
            model.mkdir()
            for name in parakeet._COREML_REQUIRED_MODEL_ENTRIES:
                path = model / name
                if name.endswith(".mlmodelc"):
                    path.mkdir()
                else:
                    path.write_text("{}", encoding="utf-8")
            cli = root / "parakeet"
            cli.write_text("#!/bin/sh\n", encoding="utf-8")
            cli.chmod(0o755)
            with patch.dict(os.environ, {
                parakeet.PARAKEET_COREML_MODEL_ENV: str(model),
                parakeet.PARAKEET_COREML_CLI_ENV: str(cli),
            }):
                runtime = parakeet.discover_coreml_runtime()
            self.assertEqual(runtime.model_dir, model.resolve())
            self.assertEqual(runtime.cli_path, cli.resolve())

    def test_existing_coreml_runtime_prevents_onnx_download(self):
        runtime = CoreMLRuntime(Path("/memo/model"), Path("/memo/parakeet"))
        expected = SimpleNamespace(engine="coreml")
        with patch.object(parakeet, "discover_coreml_runtime", return_value=runtime), patch.object(
            parakeet, "_create_coreml_session", return_value=expected
        ) as coreml_session, patch.object(
            parakeet,
            "ensure_parakeet_assets",
            side_effect=AssertionError("Core ML availability must prevent ONNX download"),
        ):
            actual = parakeet.create_parakeet_session(
                "coreml-preferred", "/tmp/audio.wav", "auto", PARAKEET_ONNX_MODEL_ID
            )
        self.assertIs(actual, expected)
        coreml_session.assert_called_once()

    def test_coreml_command_progress_and_token_timing_parser(self):
        runtime = CoreMLRuntime(Path("/tmp/Memo model"), Path("/tmp/parakeet cli"))
        command = parakeet._build_coreml_command(
            runtime, "/tmp/input audio.wav", "/tmp/output dir", "result"
        )
        self.assertEqual(command[0], "/tmp/parakeet cli")
        self.assertEqual(command[1:3], ["--model", "/tmp/Memo model"])
        self.assertEqual(command[4], str(Path("/tmp/input audio.wav").resolve()))
        self.assertEqual(command[-2:], ["--output-filename", "result"])
        self.assertEqual(
            parakeet._parse_coreml_status_line('{"status":"progress","progress":49}')["progress"],
            49,
        )
        self.assertEqual(
            parakeet._parse_coreml_status_line("Transcribing 73% complete")["progress"],
            73,
        )

        payload = {
            "duration": 30,
            "text": "My God Ha ha ha.",
            "tokenTimings": [
                {"token": " My", "startTime": 13.12, "endTime": 13.6},
                {"token": " God", "startTime": 13.6, "endTime": 26.64},
                {"token": " Ha", "startTime": 26.64, "endTime": 26.72},
                {"token": " ha", "startTime": 26.72, "endTime": 26.8},
                {"token": " ha", "startTime": 26.8, "endTime": 27.12},
                {"token": ".", "startTime": 27.12, "endTime": 27.2},
            ],
        }
        segments, duration = parakeet._segments_from_coreml_json(payload)
        self.assertEqual(duration, 30)
        self.assertEqual([item.text for item in segments], ["My God", "Ha ha ha."])
        self.assertLessEqual(segments[0].end, 15.6)
        self.assertEqual(segments[1].start, 26.64)

    def test_coreml_runner_reads_json_without_real_inference(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime = CoreMLRuntime(root / "model", root / "parakeet")
            audio = root / "input.wav"
            audio.write_bytes(b"audio")
            captured = {}

            class FakeProcess:
                def __init__(self, stdout_text):
                    self.stdout = io.StringIO(stdout_text)

                def poll(self):
                    return 0

                def wait(self, timeout=None):
                    return 0

                def terminate(self):
                    raise AssertionError("completed process must not terminate")

            def fake_popen(command, **_kwargs):
                captured["command"] = command
                output_dir = Path(command[command.index("--output-dir") + 1])
                filename = command[command.index("--output-filename") + 1]
                output_path = output_dir / f"{filename}.json"
                output_path.write_text(
                    json.dumps({"duration": 1, "text": "Hello", "tokenTimings": []}),
                    encoding="utf-8",
                )
                return FakeProcess(
                    '{"status":"progress","progress":50}\n'
                    + json.dumps({"status": "success", "result": str(output_path)})
                    + "\n"
                )

            payload = parakeet._run_coreml_cli(
                "offline-coreml-runner", str(audio), runtime, fake_popen
            )
            self.assertEqual(payload["text"], "Hello")
            self.assertEqual(captured["command"][0], str(runtime.cli_path))

    def test_coreml_runner_terminates_then_kills_on_task_cancel(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime = CoreMLRuntime(root / "model", root / "parakeet")
            audio = root / "input.wav"
            audio.write_bytes(b"audio")

            class HangingProcess:
                def __init__(self):
                    self.stdout = io.StringIO("")
                    self.terminated = False
                    self.killed = False

                def poll(self):
                    return -9 if self.killed else None

                def terminate(self):
                    self.terminated = True

                def kill(self):
                    self.killed = True

                def wait(self, timeout=None):
                    if self.killed:
                        return -9
                    raise subprocess.TimeoutExpired("parakeet", timeout)

            process = HangingProcess()
            checkpoint_count = 0

            def cancelling_checkpoint(_task_id):
                nonlocal checkpoint_count
                checkpoint_count += 1
                if checkpoint_count >= 2:
                    raise TaskCancelled("cancel test")

            with patch.object(parakeet.task_manager, "checkpoint", side_effect=cancelling_checkpoint):
                with self.assertRaises(TaskCancelled):
                    parakeet._run_coreml_cli(
                        "cancel-coreml", str(audio), runtime, lambda *_args, **_kwargs: process
                    )
            self.assertTrue(process.terminated)
            self.assertTrue(process.killed)

    def test_transcribe_api_accepts_parakeet_without_starting_network_in_test(self):
        init_db()
        project_id = str(uuid.uuid4())
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        with tempfile.NamedTemporaryFile(suffix=".wav") as audio:
            with wave.open(audio.name, "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                output.writeframes(b"\0\0" * 16000)
            db = get_db()
            db.execute(
                "INSERT INTO projects "
                "(id,title,source_type,audio_path,language,target_language,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (project_id, "API Parakeet", "local", audio.name, "auto", "zh", now, now),
            )
            db.commit()
            db.close()

            fake_manager = SimpleNamespace(
                create_task=lambda _project_id, _kind: "parakeet-task",
                run_background=lambda *_args, **_kwargs: None,
            )
            with patch("app.api.projects.task_manager", fake_manager):
                response = start_transcribe(project_id, "auto", PARAKEET_MODEL_ID)

        self.assertEqual(response["task_id"], "parakeet-task")
        with self.assertRaises(HTTPException) as raised:
            start_transcribe("missing", "auto", "invented-model")
        self.assertEqual(raised.exception.status_code, 400)

    def test_official_nemo_transducer_adapter_yields_timed_vad_segment(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            audio_path = root / "audio.wav"
            with wave.open(str(audio_path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                output.writeframes(b"\0\0" * 16000)

            assets = ParakeetAssets(
                model_dir=root / "model",
                encoder=root / "model" / "encoder.int8.onnx",
                decoder=root / "model" / "decoder.int8.onnx",
                joiner=root / "model" / "joiner.int8.onnx",
                tokens=root / "model" / "tokens.txt",
                vad=root / "silero_vad.onnx",
            )
            captured = {}

            class FakeStream:
                def __init__(self):
                    self.result = SimpleNamespace(text="")

                def accept_waveform(self, sample_rate, samples):
                    captured["accepted_sample_rate"] = sample_rate
                    captured["accepted_samples"] = len(samples)

            class FakeRecognizer:
                def create_stream(self):
                    return FakeStream()

                def decode_stream(self, stream):
                    stream.result.text = "Hello from Parakeet."

            class FakeOfflineRecognizer:
                @classmethod
                def from_transducer(cls, **kwargs):
                    captured.update(kwargs)
                    return FakeRecognizer()

            class FakeSileroConfig:
                def __init__(self):
                    self.model = ""
                    self.threshold = 0.0
                    self.min_silence_duration = 0.0
                    self.min_speech_duration = 0.0
                    self.max_speech_duration = 0.0
                    self.window_size = 512

            class FakeVadConfig:
                def __init__(self):
                    self.silero_vad = FakeSileroConfig()
                    self.sample_rate = 0

            class FakeVad:
                def __init__(self, _config, buffer_size_in_seconds):
                    captured["vad_buffer_seconds"] = buffer_size_in_seconds
                    self._queue = []

                def accept_waveform(self, _samples):
                    pass

                def flush(self):
                    self._queue.append(
                        SimpleNamespace(start=1600, samples=np.ones(8000, dtype=np.float32))
                    )

                def empty(self):
                    return not self._queue

                @property
                def front(self):
                    return self._queue[0]

                def pop(self):
                    self._queue.pop(0)

            fake_sherpa = SimpleNamespace(
                OfflineRecognizer=FakeOfflineRecognizer,
                VadModelConfig=FakeVadConfig,
                VoiceActivityDetector=FakeVad,
            )

            with patch.object(parakeet, "discover_coreml_runtime", return_value=None), patch.object(
                parakeet, "ensure_parakeet_assets", return_value=assets
            ), patch.object(parakeet, "_import_sherpa_onnx", return_value=fake_sherpa):
                session = parakeet.create_parakeet_session(
                    "offline-inference-test", str(audio_path), "auto", PARAKEET_ONNX_MODEL_ID
                )
                segments = list(session.segments)

            self.assertEqual(captured["model_type"], "nemo_transducer")
            self.assertEqual(captured["provider"], "cpu")
            self.assertEqual(captured["accepted_sample_rate"], 16000)
            self.assertEqual(len(segments), 1)
            self.assertAlmostEqual(segments[0].start, 0.1)
            self.assertAlmostEqual(segments[0].end, 0.6)
            self.assertEqual(segments[0].text, "Hello from Parakeet.")

    def test_parakeet_is_an_api_allowed_model_and_uses_common_pipeline(self):
        self.assertIn(PARAKEET_MODEL_ID, SUPPORTED_TRANSCRIPTION_MODELS)
        init_db()
        project_id = str(uuid.uuid4())
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        db = get_db()
        db.execute(
            "INSERT INTO projects (id,title,source_type,language,target_language,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (project_id, "Parakeet", "local", "auto", "zh", now, now),
        )
        db.commit()
        db.close()

        session = SimpleNamespace(
            segments=iter([ParakeetSegment(0.0, 2.0, "Hello from Parakeet.")]),
            audio_duration=2.0,
            detected_language="auto",
            device="cpu",
            compute_type="int8 ONNX",
            model_label="Parakeet test adapter",
            progress_start=25.0,
        )
        task_id = task_manager.create_task(project_id, "transcribe")
        with patch(
            "app.services.transcriber.create_parakeet_session", return_value=session
        ):
            result = transcribe_audio(
                task_id,
                "/unused/in/offline/test.wav",
                project_id,
                "auto",
                PARAKEET_MODEL_ID,
            )

        db = get_db()
        rows = db.execute(
            "SELECT raw_text, source_stage, is_draft FROM segments WHERE project_id=? ORDER BY idx",
            (project_id,),
        ).fetchall()
        db.close()
        self.assertEqual([row["raw_text"] for row in rows], ["Hello from Parakeet."])
        self.assertEqual(rows[0]["source_stage"], "postprocessed")
        self.assertEqual(rows[0]["is_draft"], 0)
        self.assertEqual(result[0]["text"], "Hello from Parakeet.")
        self.assertEqual(task_manager.get_task(task_id)["details"]["model_id"], PARAKEET_MODEL_ID)

    def test_empty_transcription_keeps_existing_published_subtitles(self):
        init_db()
        project_id = str(uuid.uuid4())
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        db = get_db()
        db.execute(
            "INSERT INTO projects (id,title,source_type,language,target_language,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            (project_id, "Safe retry", "local", "auto", "zh", now, now),
        )
        db.execute(
            """INSERT INTO segments
               (id,project_id,idx,start,end,raw_text,clean_text,is_draft,source_stage)
               VALUES (?,?,?,?,?,?,?,0,'postprocessed')""",
            (str(uuid.uuid4()), project_id, 1, 0, 1, "Existing subtitle", "Existing subtitle"),
        )
        db.commit()
        db.close()
        session = SimpleNamespace(
            segments=iter([]), audio_duration=3.0, detected_language="auto",
            device="cpu", compute_type="int8", model_label="empty test", progress_start=5.0,
        )
        task_id = task_manager.create_task(project_id, "transcribe")
        with patch("app.services.transcriber.create_parakeet_session", return_value=session):
            with self.assertRaisesRegex(RuntimeError, "原有字幕已安全保留"):
                transcribe_audio(task_id, "/unused.wav", project_id, "auto", PARAKEET_MODEL_ID)
        db = get_db()
        rows = db.execute("SELECT raw_text FROM segments WHERE project_id=?", (project_id,)).fetchall()
        db.close()
        self.assertEqual([row["raw_text"] for row in rows], ["Existing subtitle"])


if __name__ == "__main__":
    unittest.main()
