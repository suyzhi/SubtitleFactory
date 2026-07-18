import os
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
os.environ["SUBTITLE_FACTORY_DATA_DIR"] = tempfile.mkdtemp(prefix="subtitle-factory-tests-")

from app.models.database import get_db, init_db
from app.services.subtitle_cleaner import (
    _build_semantic_batches,
    _compose_final_segments,
    _fingerprint,
    _validate_batch_results,
    _validate_grouped_results,
    _commit_restructured_segments,
    _call_llm_group,
    clean_subtitles,
    retry_clean_batch,
    undo_last_clean,
)
from app.services.subtitle_exporter import export_ass, export_srt
from app.services.subtitle_translator import _call_llm_translate
from app.services.video_renderer import burn_subtitles
from app.services.transcriber import _post_process_segments
from app.services.ai_settings import get_ai_settings, save_ai_settings
from app.utils.task_manager import TaskManager, task_manager


class TimestampSegmentationTests(unittest.TestCase):
    def test_short_adjacent_segment_merges_without_crossing_silence(self):
        output, merged, _ = _post_process_segments([
            {"start": 0.0, "end": 2.0, "text": "Hello"},
            {"start": 2.1, "end": 2.5, "text": "world"},
            {"start": 5.0, "end": 5.4, "text": "isolated"},
        ])
        self.assertEqual(output[0]["text"], "Hello world")
        self.assertGreaterEqual(merged, 1)
        self.assertEqual(output[-1]["start"], 5.0)

    def test_long_unpunctuated_text_and_duration_are_preserved(self):
        text = "这是一个没有标点但是非常非常长的字幕文本需要按照可读长度进行可靠拆分"
        output, _, split = _post_process_segments([
            {"start": 0.0, "end": 16.0, "text": text},
        ])
        self.assertEqual(split, 0)
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]["text"], text)
        self.assertEqual(output[0]["end"], 16.0)

    def test_overlaps_are_normalized_and_indices_are_stable(self):
        output, _, _ = _post_process_segments([
            {"start": 0.0, "end": 2.2, "text": "one"},
            {"start": 1.9, "end": 3.0, "text": "two"},
            {"start": 3.2, "end": 4.5, "text": "three"},
        ])
        self.assertEqual([item["index"] for item in output], list(range(1, len(output) + 1)))
        for previous, current in zip(output, output[1:]):
            self.assertLessEqual(previous["end"], current["start"])
            self.assertGreater(current["end"], current["start"])

    def test_long_complete_english_sentence_is_not_split(self):
        output, _, split = _post_process_segments([{
            "start": 0.0,
            "end": 10.0,
            "text": "This complete English sentence is intentionally longer than forty two display characters.",
            "timings": [
                {"text": "This", "start": 0.0, "end": 0.4},
                {"text": " complete", "start": 0.4, "end": 1.0},
                {"text": " sentence", "start": 8.0, "end": 8.5},
                {"text": ".", "start": 8.5, "end": 10.0},
            ],
        }])
        self.assertEqual(split, 0)
        self.assertEqual(len(output), 1)
        self.assertGreater(len(output[0]["text"]), 42)
        self.assertEqual(output[0]["end"], 10.0)

    def test_short_complete_sentence_is_not_merged(self):
        output, merged, _ = _post_process_segments([
            {"start": 0.0, "end": 0.6, "text": "Yes."},
            {"start": 0.7, "end": 2.0, "text": "The next sentence starts here."},
        ])
        self.assertEqual(merged, 0)
        self.assertEqual([item["text"] for item in output], [
            "Yes.", "The next sentence starts here.",
        ])


class AIResultValidationTests(unittest.TestCase):
    def test_cleaner_passes_user_target_length_to_ai(self):
        batch = [
            {"idx": 1, "start": 0.0, "end": 1.0, "raw_text": "Hello"},
            {"idx": 2, "start": 1.0, "end": 2.0, "raw_text": "world"},
        ]
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"choices": [{"message": {"content": '[{"ids":["1","2"],"clean_text":"Hello world."}]'}}]},
        )
        ai = {"model": "test", "base_url": "http://example.test/v1", "api_key": "secret"}
        with patch("httpx.post", return_value=response) as post:
            result = _call_llm_group(batch, ai, 68)
        prompt = post.call_args.kwargs["json"]["messages"][0]["content"]
        self.assertIn("68 个显示字符", prompt)
        self.assertIn("不是长度限制", prompt)
        self.assertIn("必须保留完整句并允许超长", prompt)
        self.assertEqual(result[0]["ids"], ["1", "2"])

    def test_deepseek_cleaner_requests_strict_json_object(self):
        batch = [{"idx": 1, "start": 0.0, "end": 1.0, "raw_text": "Hello"}]
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"choices": [{"finish_reason": "stop", "message": {
                "content": '{"groups":[{"ids":["1"],"clean_text":"Hello."}]}'
            }}]},
        )
        ai = {"provider": "deepseek", "model": "deepseek-chat", "base_url": "http://example.test/v1", "api_key": "secret"}
        with patch("httpx.post", return_value=response) as post:
            result = _call_llm_group(batch, ai)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertIn("required_output_schema", payload["messages"][1]["content"])
        self.assertEqual(result[0]["ids"], ["1"])

    def test_cleaner_length_finish_adaptively_splits_and_recovers(self):
        batch = [
            {"idx": idx, "start": float(idx - 1), "end": float(idx), "raw_text": text}
            for idx, text in enumerate(["One.", "Two.", "Three.", "Four."], 1)
        ]
        responses = [
            SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"choices": [{"finish_reason": "length", "message": {"content": "{"}}]},
            ),
            SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"choices": [{"finish_reason": "stop", "message": {"content":
                    '{"groups":[{"ids":["1"],"clean_text":"One."},{"ids":["2"],"clean_text":"Two."}]}'
                }}]},
            ),
            SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"choices": [{"finish_reason": "stop", "message": {"content":
                    '{"groups":[{"ids":["3"],"clean_text":"Three."},{"ids":["4"],"clean_text":"Four."}]}'
                }}]},
            ),
        ]
        ai = {"provider": "deepseek", "model": "test", "base_url": "http://example.test/v1", "api_key": "secret"}
        diagnostics = {}
        with patch("httpx.post", side_effect=responses):
            result = _call_llm_group(batch, ai, diagnostics=diagnostics)
        self.assertEqual([group["ids"] for group in result], [["1"], ["2"], ["3"], ["4"]])
        self.assertEqual(diagnostics["request_attempts"], 3)
        self.assertEqual(diagnostics["length_recoveries"], 1)
        self.assertEqual(diagnostics["adaptive_splits"], 1)

    def test_cleaner_invalid_json_retries_once_then_splits(self):
        batch = [
            {"idx": idx, "start": float(idx - 1), "end": float(idx), "raw_text": text}
            for idx, text in enumerate(["One.", "Two.", "Three.", "Four."], 1)
        ]
        invalid = lambda: SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"choices": [{"finish_reason": "stop", "message": {"content": "not-json"}}]},
        )
        left = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"choices": [{"finish_reason": "stop", "message": {"content":
                '{"groups":[{"ids":["1","2"],"clean_text":"One. Two."}]}'
            }}]},
        )
        right = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"choices": [{"finish_reason": "stop", "message": {"content":
                '{"groups":[{"ids":["3","4"],"clean_text":"Three. Four."}]}'
            }}]},
        )
        ai = {"provider": "deepseek", "model": "test", "base_url": "http://example.test/v1", "api_key": "secret"}
        diagnostics = {}
        with patch("httpx.post", side_effect=[invalid(), invalid(), left, right]), patch("time.sleep"):
            result = _call_llm_group(batch, ai, diagnostics=diagnostics)
        self.assertEqual([value for group in result for value in group["ids"]], ["1", "2", "3", "4"])
        self.assertEqual(diagnostics["request_attempts"], 4)
        self.assertEqual(diagnostics["adaptive_splits"], 1)

    def test_semantic_batches_are_bounded_to_32_rows(self):
        rows = [
            {"idx": idx, "raw_text": f"Sentence {idx}.", "locked": 0}
            for idx in range(1, 66)
        ]
        batches = _build_semantic_batches(rows)
        self.assertEqual([len(batch) for batch in batches], [32, 32, 1])

    def test_translator_sends_bounded_context_but_validates_only_items(self):
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"choices": [{"message": {"content": '[{"id":"2","translated_text":"当前句"}]'}}]},
        )
        client = patch("httpx.Client").start()
        self.addCleanup(patch.stopall)
        client.return_value.__enter__.return_value.post.return_value = response
        ai = {"model": "test", "base_url": "http://example.test/v1", "api_key": "secret"}
        result = _call_llm_translate(
            [{"id": "2", "text": "it works"}], "translate", ai,
            [{"id": "1", "text": "the pipeline"}], [{"id": "3", "text": "next topic"}],
        )
        payload = client.return_value.__enter__.return_value.post.call_args.kwargs["json"]
        supplied = __import__("json").loads(payload["messages"][1]["content"])
        self.assertEqual(supplied["context_before"][0]["id"], "1")
        self.assertEqual(supplied["context_after"][0]["id"], "3")
        self.assertEqual(result, [{"id": "2", "translated_text": "当前句"}])

    def test_reorders_valid_ai_results_to_input_order(self):
        batch = [{"id": "1"}, {"id": "2"}]
        parsed = [{"id": "2", "translated_text": "二"}, {"id": "1", "translated_text": "一"}]
        self.assertEqual(
            _validate_batch_results(batch, parsed, "translated_text"),
            [{"id": "1", "translated_text": "一"}, {"id": "2", "translated_text": "二"}],
        )

    def test_rejects_missing_or_duplicate_ids(self):
        batch = [{"id": "1"}, {"id": "2"}]
        with self.assertRaises(ValueError):
            _validate_batch_results(batch, [{"id": "1", "clean_text": "a"}], "clean_text")
        with self.assertRaises(ValueError):
            _validate_batch_results(batch, [
                {"id": "1", "clean_text": "a"},
                {"id": "1", "clean_text": "again"},
                {"id": "2", "clean_text": "b"},
            ], "clean_text")

    def test_grouped_cleanup_joins_adjacent_fragments(self):
        batch = [
            {"idx": 1, "start": 0.0, "end": 1.1},
            {"idx": 2, "start": 1.1, "end": 2.4},
            {"idx": 3, "start": 2.6, "end": 4.0},
        ]
        result = _validate_grouped_results(batch, [
            {"ids": ["1", "2"], "clean_text": "I want to show you this."},
            {"ids": ["3"], "clean_text": "Next sentence."},
        ])
        self.assertEqual(result[0]["ids"], ["1", "2"])

    def test_grouped_cleanup_rejects_reordering_but_allows_long_complete_sentence(self):
        batch = [
            {"idx": 1, "start": 0.0, "end": 1.0, "raw_text": "This is one complete sentence"},
            {"idx": 2, "start": 1.0, "end": 13.5, "raw_text": "that remains intact even when it is much longer than the target length"},
        ]
        with self.assertRaises(ValueError):
            _validate_grouped_results(batch, [{"ids": ["2", "1"], "clean_text": "bad"}])
        clean_text = (
            "This is one complete sentence that remains intact even when it is much "
            "longer than the target length."
        )
        result = _validate_grouped_results(
            batch, [{"ids": ["1", "2"], "clean_text": clean_text}]
        )
        self.assertEqual(result, [{"ids": ["1", "2"], "clean_text": clean_text}])

    def test_creative_rewrite_falls_back_to_joined_raw_text(self):
        batch = [
            {"idx": 1, "raw_text": "The launch was delayed"},
            {"idx": 2, "raw_text": "because the engine failed twice"},
        ]
        result = _validate_grouped_results(batch, [{
            "ids": ["1", "2"],
            "clean_text": "Everything went wonderfully and the mission succeeded.",
        }])
        self.assertEqual(
            result[0]["clean_text"],
            "The launch was delayed because the engine failed twice",
        )

    def test_cleanup_rewrite_is_undoable(self):
        init_db()
        project_id = str(__import__("uuid").uuid4())
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        db = get_db()
        db.execute(
            "INSERT INTO projects (id,title,source_type,language,target_language,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            (project_id, "group test", "local", "en", "zh", now, now),
        )
        original = []
        for idx, text in enumerate(["I want to", "show you this", "Locked line"], 1):
            row = {
                "id": str(__import__("uuid").uuid4()), "project_id": project_id, "idx": idx,
                "start": float(idx - 1), "end": float(idx), "raw_text": text, "clean_text": text,
                "translated_text": "旧译文", "speaker": "", "locked": 1 if idx == 3 else 0,
                "is_draft": 0, "source_stage": "postprocessed",
            }
            original.append(row)
            db.execute(
                "INSERT INTO segments (id,project_id,idx,start,end,raw_text,clean_text,translated_text,speaker,locked,is_draft,source_stage) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple(row[key] for key in ["id","project_id","idx","start","end","raw_text","clean_text","translated_text","speaker","locked","is_draft","source_stage"]),
            )
        db.commit(); db.close()

        final = _compose_final_segments(original, [{"ids": ["1", "2"], "clean_text": "I want to show you this."}])
        self.assertEqual(len(final), 2)
        self.assertEqual(final[0]["end"], 2.0)
        self.assertEqual(final[0]["translated_text"], "")
        self.assertEqual(final[1]["locked"], 1)
        _commit_restructured_segments(project_id, original, _fingerprint(original), final)
        self.assertEqual(undo_last_clean(project_id), 3)
        db = get_db()
        restored = db.execute("SELECT * FROM segments WHERE project_id=? ORDER BY idx", (project_id,)).fetchall()
        db.close()
        self.assertEqual([row["raw_text"] for row in restored], ["I want to", "show you this", "Locked line"])

    def test_failed_clean_batch_retry_only_reconciles_stored_range(self):
        init_db()
        project_id = str(__import__("uuid").uuid4())
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        db = get_db()
        db.execute(
            "INSERT INTO projects (id,title,source_type,language,target_language,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            (project_id, "single batch retry", "local", "en", "zh", now, now),
        )
        rows = []
        for idx, text in enumerate(["Keep this.", "Merge", "these", "Keep that."], 1):
            row = {
                "id": str(__import__("uuid").uuid4()), "project_id": project_id, "idx": idx,
                "start": float(idx - 1), "end": float(idx), "raw_text": text, "clean_text": text,
                "translated_text": "", "speaker": "", "locked": 0, "is_draft": 0,
                "source_stage": "postprocessed",
            }
            rows.append(row)
            db.execute(
                "INSERT INTO segments (id,project_id,idx,start,end,raw_text,clean_text,translated_text,speaker,locked,is_draft,source_stage) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple(row[key] for key in ["id","project_id","idx","start","end","raw_text","clean_text","translated_text","speaker","locked","is_draft","source_stage"]),
            )
        original_task = task_manager.create_task(project_id, "clean")
        retry_task = task_manager.create_task(project_id, "clean", max_attempts=1)
        fingerprint = __import__("json").dumps({
            "segments": _fingerprint(rows[1:3]), "provider": "deepseek",
            "model": "deepseek-chat", "target_length": 42,
        }, ensure_ascii=False, sort_keys=True)
        db.execute(
            """INSERT INTO ai_batch_results
               (task_id,project_id,operation,batch_index,input_fingerprint,status,result_json,attempts,error,updated_at)
               VALUES (?,?,?,?,?,'failed','[]',3,'invalid json',?)""",
            (original_task, project_id, "clean", 2, fingerprint, now),
        )
        db.commit(); db.close()

        provider = {"provider": "deepseek", "model": "deepseek-chat", "base_url": "http://example.test/v1", "api_key": "secret"}
        with patch("app.services.subtitle_cleaner.assigned_provider", return_value=provider), patch(
            "app.services.subtitle_cleaner._call_llm_group",
            return_value=[{"ids": ["2", "3"], "clean_text": "Merge these."}],
        ) as call:
            retry_clean_batch(retry_task, original_task, 2)

        call.assert_called_once()
        self.assertEqual([row["idx"] for row in call.call_args.args[0]], [2, 3])
        db = get_db()
        published = db.execute(
            "SELECT raw_text,clean_text FROM segments WHERE project_id=? ORDER BY idx", (project_id,),
        ).fetchall()
        batch_status = db.execute(
            "SELECT status FROM ai_batch_results WHERE task_id=? AND batch_index=2", (original_task,),
        ).fetchone()["status"]
        db.close()
        self.assertEqual([row["raw_text"] for row in published], ["Keep this.", "Merge these", "Keep that."])
        self.assertEqual(published[1]["clean_text"], "Merge these.")
        self.assertEqual(batch_status, "success")


class TaskPauseTests(unittest.TestCase):
    def test_worker_pauses_and_resumes_at_checkpoint(self):
        manager = TaskManager(max_workers=1)
        task_id = manager.create_task("project", "translate")
        checkpoints = []

        def worker(runtime_task_id):
            for index in range(4):
                manager.wait_if_paused(runtime_task_id)
                checkpoints.append(index)
                time.sleep(0.08)

        manager.run_background(task_id, worker)
        deadline = time.time() + 2
        while len(checkpoints) < 1 and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(manager.pause_task(task_id))
        paused_count = len(checkpoints)
        time.sleep(0.2)
        self.assertLessEqual(len(checkpoints), paused_count + 1)
        self.assertEqual(manager.get_task(task_id)["status"], "paused")
        self.assertTrue(manager.resume_task(task_id))
        deadline = time.time() + 2
        while manager.get_task(task_id)["status"] != "success" and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(manager.get_task(task_id)["status"], "success")
        self.assertEqual(checkpoints, [0, 1, 2, 3])
        manager.shutdown()


class TaskCancellationTests(unittest.TestCase):
    def test_pending_task_is_cancelled_before_worker_starts(self):
        manager = TaskManager(max_workers=1)
        blocker_started = threading.Event()
        release_blocker = threading.Event()
        pending_started = threading.Event()

        def blocker(_task_id):
            blocker_started.set()
            release_blocker.wait(2)

        first_id = manager.create_task("project", "first")
        first_future = manager.run_background(first_id, blocker)
        self.assertTrue(blocker_started.wait(1))

        pending_id = manager.create_task("project", "pending")
        manager.run_background(pending_id, lambda _task_id: pending_started.set())
        self.assertTrue(manager.cancel_task(pending_id))
        self.assertEqual(manager.get_task(pending_id)["status"], "cancelled")
        self.assertEqual(manager.get_task(pending_id)["message"], "任务已终止")

        release_blocker.set()
        first_future.result(timeout=2)
        time.sleep(0.05)
        self.assertFalse(pending_started.is_set())
        manager.shutdown()

    def test_running_task_stops_at_next_checkpoint(self):
        manager = TaskManager(max_workers=1)
        started = threading.Event()
        release = threading.Event()
        passed_checkpoint = threading.Event()

        def worker(runtime_task_id):
            started.set()
            release.wait(2)
            manager.checkpoint(runtime_task_id)
            passed_checkpoint.set()

        task_id = manager.create_task("project", "running")
        future = manager.run_background(task_id, worker)
        self.assertTrue(started.wait(1))
        self.assertTrue(manager.cancel_task(task_id))
        release.set()
        future.result(timeout=2)
        self.assertFalse(passed_checkpoint.is_set())
        self.assertEqual(manager.get_task(task_id)["status"], "cancelled")
        # Late worker updates cannot revive the terminal task.
        manager.update_task(task_id, status="success", progress=100)
        self.assertEqual(manager.get_task(task_id)["status"], "cancelled")
        manager.shutdown()

    def test_cancelling_paused_task_wakes_worker(self):
        manager = TaskManager(max_workers=1)
        started = threading.Event()
        enter_checkpoint = threading.Event()
        passed_checkpoint = threading.Event()

        def worker(runtime_task_id):
            started.set()
            enter_checkpoint.wait(2)
            manager.checkpoint(runtime_task_id)
            passed_checkpoint.set()

        task_id = manager.create_task("project", "paused")
        future = manager.run_background(task_id, worker)
        self.assertTrue(started.wait(1))
        self.assertTrue(manager.pause_task(task_id))
        enter_checkpoint.set()
        time.sleep(0.05)
        self.assertEqual(manager.get_task(task_id)["status"], "paused")
        self.assertTrue(manager.cancel_task(task_id))
        future.result(timeout=2)
        self.assertFalse(passed_checkpoint.is_set())
        self.assertEqual(manager.get_task(task_id)["status"], "cancelled")
        manager.shutdown()

    def test_cancelled_ai_cleanup_does_not_commit_in_memory_partial_results(self):
        init_db()
        project_id = str(__import__("uuid").uuid4())
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        db = get_db()
        db.execute(
            "INSERT INTO projects (id,title,source_type,language,target_language,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            (project_id, "cancel clean", "local", "en", "zh", now, now),
        )
        original_texts = ["I want to", "keep every original word"]
        for idx, text in enumerate(original_texts, 1):
            db.execute(
                """INSERT INTO segments
                   (id,project_id,idx,start,end,raw_text,clean_text,translated_text,speaker,locked,is_draft,source_stage)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (str(__import__("uuid").uuid4()), project_id, idx, idx - 1, idx,
                 text, text, "", "", 0, 0, "postprocessed"),
            )
        db.commit()
        db.close()

        manager = TaskManager(max_workers=1)
        llm_started = threading.Event()
        release_llm = threading.Event()

        def fake_group(_batch, _ai, _target_length, task_id=None, diagnostics=None):
            llm_started.set()
            release_llm.wait(2)
            return [{
                "ids": ["1", "2"],
                "clean_text": "I want to keep every original word.",
            }]

        task_id = manager.create_task(project_id, "clean")
        with patch("app.services.subtitle_cleaner.task_manager", manager), patch(
            "app.services.subtitle_cleaner.get_ai_settings",
            return_value={
                "api_key": "secret", "provider": "test", "model": "test",
                "base_url": "http://example.test/v1",
            },
        ), patch("app.services.subtitle_cleaner._call_llm_group", side_effect=fake_group):
            future = manager.run_background(task_id, clean_subtitles, project_id, 24)
            self.assertTrue(llm_started.wait(1))
            self.assertTrue(manager.cancel_task(task_id))
            release_llm.set()
            future.result(timeout=2)

        db = get_db()
        rows = db.execute(
            "SELECT raw_text, clean_text FROM segments WHERE project_id=? ORDER BY idx",
            (project_id,),
        ).fetchall()
        revision_count = db.execute(
            "SELECT COUNT(*) FROM segment_revisions WHERE project_id=?", (project_id,)
        ).fetchone()[0]
        db.close()
        self.assertEqual([row["raw_text"] for row in rows], original_texts)
        self.assertEqual([row["clean_text"] for row in rows], original_texts)
        self.assertEqual(revision_count, 0)
        self.assertEqual(manager.get_task(task_id)["status"], "cancelled")
        manager.shutdown()


class PersistenceAndExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_ai_secret_is_masked_on_read(self):
        saved = save_ai_settings("custom", "http://127.0.0.1:11434/v1", "local-model", "secret")
        self.assertTrue(saved["has_api_key"])
        self.assertEqual(saved["api_key"], "")
        self.assertEqual(get_ai_settings(include_secret=True)["api_key"], "secret")

    def test_bilingual_srt_preserves_timestamps_and_order(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "out.srt"
            export_srt([{
                "index": 1, "start": 1.234, "end": 3.456,
                "raw_text": "Hello", "clean_text": "Hello!", "translated_text": "你好！",
            }], str(path), bilingual=True, primary_lang="translated")
            content = path.read_text(encoding="utf-8")
            self.assertIn("00:00:01,234 --> 00:00:03,456", content)
            self.assertIn("你好！\nHello!", content)

    def test_ass_export_works_with_installed_pysubs2_api(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "out.ass"
            export_ass([{
                "index": 1, "start": 0.0, "end": 1.5,
                "raw_text": "Hello", "clean_text": "Hello", "translated_text": "你好",
            }], str(path), bilingual=True)
            content = path.read_text(encoding="utf-8-sig")
            self.assertIn("[V4+ Styles]", content)
            self.assertIn("你好", content)

    def test_video_renderer_supports_mp4_and_mkv_containers(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "input.mp4"; video.write_bytes(b"video")
            subtitle = Path(folder) / "sub.ass"; subtitle.write_text("subtitle", encoding="utf-8")
            commands = []

            def fake_run(command, **_kwargs):
                commands.append(command)
                Path(command[-1]).write_bytes(b"rendered")
                return SimpleNamespace(returncode=0, stderr="")

            with patch(
                "app.services.video_renderer.resolve_ffmpeg_path",
                return_value=SimpleNamespace(path=Path("/app/bin/ffmpeg"), source="bundled"),
            ), patch("app.services.video_renderer.subprocess.run", side_effect=fake_run):
                burn_subtitles("test-mp4", str(video), str(subtitle), str(Path(folder) / "out.mp4"))
                burn_subtitles("test-mkv", str(video), str(subtitle), str(Path(folder) / "out.mkv"))
            self.assertIn("+faststart", commands[0])
            self.assertNotIn("+faststart", commands[1])
            self.assertEqual(commands[0][-1], str(Path(folder) / "out.mp4"))
            self.assertEqual(commands[1][-1], str(Path(folder) / "out.mkv"))


if __name__ == "__main__":
    unittest.main()
