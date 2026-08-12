import json
import sys
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import database, migrations
from app.services import clips, content_packs, project_packages, search_index
from app.utils.task_manager import TaskCancelled


class ContentReproductionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.patches = [
            patch.object(database, "DB_PATH", self.root / "factory.db"),
            patch.object(project_packages, "PROJECTS_DIR", self.root / "projects"),
            patch.object(project_packages, "EXPORTS_DIR", self.root / "exports"),
            patch.object(clips, "EXPORTS_DIR", self.root / "exports"),
            patch.object(content_packs, "EXPORTS_DIR", self.root / "exports"),
        ]
        for item in self.patches:
            item.start()
        database.init_db()
        self.project_id = "content-project"
        db = database.get_db()
        db.execute(
            """INSERT INTO projects(
                   id,title,source_type,language,target_language,created_at,updated_at,edit_revision
               ) VALUES (?,?,'local','zh','en','2026-07-01 09:00:00','2026-07-01 09:00:00',0)""",
            (self.project_id, "字幕搜索 C++ 2026"),
        )
        speaker_id = "speaker-one"
        db.execute(
            """INSERT INTO speakers(id,project_id,name,color,created_at,updated_at)
               VALUES (?,?,?,'#5599cc','now','now')""",
            (speaker_id, self.project_id, "小林"),
        )
        texts = [
            "今天讨论字幕搜索，以及 C++ 2026 的实际案例。",
            "第二部分解释如何建立全项目搜索索引。",
            "第三部分是一段完整而连续的观点。",
            "接下来讨论内容发布包和视频章节。",
            "最后讲解短视频裁切与多比例输出。",
            "这是一段自然结束的总结。",
        ]
        for index, text in enumerate(texts, 1):
            start = (index - 1) * 16.0
            db.execute(
                """INSERT INTO segments(
                       id,project_id,idx,start,end,raw_text,clean_text,translated_text,
                       speaker,speaker_id,locked,is_draft,source_stage
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,0,0,'final')""",
                (
                    f"segment-{index}", self.project_id, index, start, start + 15.0,
                    text, text, "Translated" if index == 1 else "", "小林", speaker_id,
                ),
            )
        db.commit()
        db.close()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.folder.cleanup()

    @staticmethod
    def _provider():
        return {
            "provider": "deepseek",
            "model": "content-test",
            "base_url": "https://example.invalid/v1",
            "api_key": "test",
        }

    @staticmethod
    def _content_response(_ai=None, *, user, **_kwargs):
        schema = user["schema"]
        if "chapters" in schema:
            return {"chapters": [{
                "start_index": 1, "end_index": 6,
                "title": "完整章节", "summary": "覆盖全部字幕",
            }]}
        if "overview" in schema:
            return {"overview": "这是事实保真的摘要。", "key_points": ["要点一", "要点二"]}
        if "quotes" in schema:
            return {"quotes": [{"segment_index": 3, "text": "第三部分是一段完整而连续的观点。", "reason": "观点完整"}]}
        if "titles" in schema:
            return {
                "titles": ["字幕工厂内容再生产"],
                "description": "根据字幕生成的简介。",
                "chapter_text": "00:00:00 完整章节",
                "tags": ["字幕", "内容创作"],
            }
        if "show_notes" in schema:
            return {"title": "字幕工厂", "intro": "节目简介", "show_notes": "节目笔记"}
        return {
            "xiaohongshu": {"title": "标题", "body": "正文", "tags": "#字幕"},
            "wechat": {"title": "标题", "body": "正文", "tags": "字幕"},
            "generic": {"title": "标题", "body": "正文", "tags": "字幕"},
        }

    def test_migration_and_search_index_cover_text_filters_and_updates(self):
        db = database.get_db()
        self.assertEqual(
            db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0],
            migrations.CURRENT_SCHEMA_VERSION,
        )
        db.close()
        self.assertGreaterEqual(search_index.search_segments("字幕")["total"], 2)
        self.assertGreaterEqual(search_index.search_segments("字幕搜索")["total"], 1)
        self.assertGreaterEqual(search_index.search_segments("2026")["total"], 1)
        self.assertGreaterEqual(search_index.search_segments("C++")["total"], 1)
        self.assertEqual(search_index.search_segments("字")["total"], 0)
        self.assertEqual(
            search_index.search_segments("字幕", source_language="en")["total"], 0
        )

        db = database.get_db()
        db.execute(
            "UPDATE segments SET clean_text='全新的同步关键词' WHERE id='segment-2'"
        )
        db.execute(
            "UPDATE speakers SET name='林老师' WHERE id='speaker-one'"
        )
        db.execute(
            "UPDATE segments SET speaker='林老师' WHERE speaker_id='speaker-one'"
        )
        db.commit()
        db.close()
        self.assertEqual(search_index.search_segments("同步关键词")["total"], 1)
        self.assertEqual(search_index.search_segments("林老师")["total"], 6)

        db = database.get_db()
        db.execute(
            "UPDATE projects SET deleted_at='2026-07-30 12:00:00' WHERE id=?",
            (self.project_id,),
        )
        db.commit()
        db.close()
        self.assertEqual(search_index.search_segments("字幕")["total"], 0)
        status = search_index.rebuild_search_index()
        self.assertEqual(status["indexed"], status["source"])

    def test_content_pack_generation_revision_conflict_export_and_staleness(self):
        with self.assertRaises(content_packs.ContentPackError) as incomplete:
            content_packs.create_content_pack(
                self.project_id, "译文包", "translated", "zh", False
            )
        self.assertEqual(incomplete.exception.error_code, "TRANSLATION_INCOMPLETE")
        self.assertLess(incomplete.exception.details["coverage"], 1)

        with (
            patch.object(content_packs, "assigned_provider", return_value=self._provider()),
            patch.object(content_packs.task_manager, "run_background"),
        ):
            pack, _task_id = content_packs.create_content_pack(
                self.project_id, "完整发布包", "original", "zh", False
            )
        with (
            patch.object(content_packs, "assigned_provider", return_value=self._provider()),
            patch.object(content_packs, "_call_json", side_effect=self._content_response),
        ):
            content_packs.generate_content_pack("test-task", pack["id"])

        generated = content_packs._load_pack(pack["id"])
        self.assertEqual(generated["status"], "ready")
        self.assertEqual({item["status"] for item in generated["sections"]}, {"ready"})
        summary = next(item for item in generated["sections"] if item["kind"] == "summary")
        edited = content_packs.update_section(
            pack["id"], "summary",
            {"overview": "人工摘要", "key_points": ["人工要点"]},
            summary["revision"],
        )
        edited_summary = next(item for item in edited["sections"] if item["kind"] == "summary")
        self.assertEqual(edited_summary["content"]["overview"], "人工摘要")
        with self.assertRaises(content_packs.ContentPackError) as conflict:
            content_packs.update_section(
                pack["id"], "summary", {"overview": "冲突"}, summary["revision"]
            )
        self.assertEqual(conflict.exception.error_code, "CONTENT_REVISION_CONFLICT")

        output = content_packs.export_content_pack(pack["id"])
        self.assertTrue(output.resolve().is_relative_to((self.root / "exports").resolve()))
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(
                {
                    "README.md", "summary.txt", "chapters.txt", "youtube.md",
                    "podcast.md", "social/xiaohongshu.md", "social/wechat.md",
                    "social/generic.md", "data.json",
                },
                set(archive.namelist()),
            )
            payload = json.loads(archive.read("data.json"))
            self.assertEqual(payload["pack"]["source_revision"], 0)
            self.assertIn("人工摘要", archive.read("summary.txt").decode())

        db = database.get_db()
        db.execute(
            "UPDATE projects SET edit_revision=1 WHERE id=?", (self.project_id,)
        )
        db.commit()
        db.close()
        stale = content_packs._load_pack(pack["id"])
        self.assertTrue(stale["stale"])
        self.assertEqual(
            next(item for item in stale["sections"] if item["kind"] == "summary")["content"]["overview"],
            "人工摘要",
        )

    def _create_clip_set(self):
        with (
            patch.object(clips, "assigned_provider", return_value=self._provider()),
            patch.object(clips.task_manager, "run_background"),
        ):
            set_id, _task_id = clips.create_clip_set(
                self.project_id, "候选集合", 3, 30, 90
            )
        with (
            patch.object(clips, "assigned_provider", return_value=self._provider()),
            patch.object(
                clips,
                "_call_json",
                side_effect=lambda *_args, **kwargs: {
                    "selections": [
                        {
                            "candidate_key": item["candidate_key"],
                            "title": f"候选 {index + 1}",
                            "hook": "开场钩子",
                            "reason": "主题完整",
                            "score": 90 - index,
                        }
                        for index, item in enumerate(kwargs["user"]["candidates"][:3])
                    ]
                },
            ),
        ):
            clips.recommend_clips("test-task", set_id)
        return set_id

    def test_clip_candidates_layouts_render_reuse_and_cancel_cleanup(self):
        set_id = self._create_clip_set()
        detail = clips.get_clip_set(set_id)
        self.assertEqual(len(detail["candidates"]), 3)
        self.assertTrue(all(30 <= item["end"] - item["start"] <= 90 for item in detail["candidates"]))
        candidate = detail["candidates"][0]
        original_layout = next(
            item for item in candidate["layouts"] if item["aspect_ratio"] == "9:16"
        )
        with patch.dict(clips.ASPECT_DIMENSIONS, {"9:16": (320, 568)}):
            caption_assets = clips._subtitle_overlay_paths(
                "caption-test", candidate, original_layout, self.project_id
            )
        self.assertTrue(caption_assets)
        for asset in caption_assets:
            path = Path(asset["path"])
            self.assertIn(path.read_bytes()[:4], {b"II*\x00", b"MM\x00*"})
            path.unlink()
        updated = clips.update_candidate(
            candidate["id"],
            title="人工确认候选",
            start=candidate["start"] + .1,
            end=candidate["end"] - .1,
            selected=True,
            expected_revision=candidate["revision"],
            confirm_current_source=False,
        )
        candidate = updated["candidates"][0]
        layout = next(item for item in candidate["layouts"] if item["aspect_ratio"] == "9:16")
        updated = clips.update_layout(
            candidate["id"], "9:16",
            enabled=True, composition="crop", focal_x=.25, focal_y=.75,
            subtitle_mode="off", style={}, expected_revision=layout["revision"],
        )
        candidate = updated["candidates"][0]
        layout = next(item for item in candidate["layouts"] if item["aspect_ratio"] == "9:16")
        graph = clips._filter_graph(1080, 1920, layout, None)
        self.assertIn("crop=1080:1920", graph)
        self.assertIn("*0.2500", graph)
        blur_graph = clips._filter_graph(
            1080, 1920, {**layout, "composition": "blur"}, None
        )
        self.assertIn("gblur=sigma=24:steps=2", blur_graph)
        self.assertNotIn("boxblur", blur_graph)
        overlay_graph = clips._filter_graph(
            1080, 1920, layout, None,
            [{"path": "/tmp/caption.png", "start": 1.2, "end": 4.5}],
        )
        self.assertIn("movie=filename='/tmp/caption.png'", overlay_graph)
        self.assertIn("between(t,1.200,4.500)", overlay_graph)

        video = self.root / "source.mp4"
        video.write_bytes(b"placeholder")
        db = database.get_db()
        db.execute(
            "UPDATE projects SET video_path=? WHERE id=?", (str(video), self.project_id)
        )
        db.commit()
        db.close()
        with patch.object(clips.task_manager, "run_background"):
            task_id, render_ids = clips.create_render_batch(
                self.project_id,
                [{"candidate_id": candidate["id"], "aspect_ratio": "9:16"}],
                False,
            )
        self.assertTrue(task_id)
        self.assertEqual(len(render_ids), 1)
        output = self.root / "existing.mp4"
        output.write_bytes(b"validated")
        db = database.get_db()
        db.execute(
            "UPDATE clip_renders SET status='success',path=? WHERE id=?",
            (str(output), render_ids[0]),
        )
        db.commit()
        db.close()
        with patch.object(clips.task_manager, "run_background"):
            reused_task, reused_ids = clips.create_render_batch(
                self.project_id,
                [{"candidate_id": candidate["id"], "aspect_ratio": "9:16"}],
                False,
            )
        self.assertEqual(reused_task, "")
        self.assertEqual(reused_ids, render_ids)

        second_layout = next(item for item in candidate["layouts"] if item["aspect_ratio"] == "1:1")
        jobs = []
        db = database.get_db()
        for index, item in enumerate((layout, second_layout), 1):
            render_id = f"cancel-render-{index}"
            db.execute(
                """INSERT INTO clip_renders(
                       id,project_id,candidate_id,aspect_ratio,configuration_fingerprint,
                       status,created_at,updated_at
                   ) VALUES (?,?,?,?,?,'pending','now','now')""",
                (render_id, self.project_id, candidate["id"], item["aspect_ratio"], f"cancel-{index}"),
            )
            jobs.append({
                "render_id": render_id,
                "candidate": candidate,
                "layout": item,
                "fingerprint": f"cancel-{index}",
            })
        db.commit()
        db.close()
        with (
            patch.object(clips, "resolve_ffmpeg_path", return_value=SimpleNamespace(path=Path("/usr/bin/false"))),
            patch.object(clips, "_run_process", side_effect=TaskCancelled()),
        ):
            with self.assertRaises(TaskCancelled):
                clips.render_batch("cancel-task", {
                    "id": self.project_id,
                    "video_path": str(video),
                    "source_type": "local",
                    "source_url": None,
                }, jobs)
        db = database.get_db()
        statuses = {
            row["id"]: row["status"]
            for row in db.execute(
                "SELECT id,status FROM clip_renders WHERE id LIKE 'cancel-render-%'"
            )
        }
        db.close()
        self.assertEqual(set(statuses.values()), {"cancelled"})

    def test_clip_candidate_confirmation_clears_its_stale_state(self):
        set_id = self._create_clip_set()
        before = clips.get_clip_set(set_id)
        candidate = before["candidates"][0]
        self.assertFalse(candidate["stale"])

        db = database.get_db()
        db.execute(
            "UPDATE projects SET edit_revision=edit_revision+1 WHERE id=?",
            (self.project_id,),
        )
        db.commit()
        db.close()

        stale = clips.get_clip_set(set_id)["candidates"][0]
        self.assertTrue(stale["stale"])
        confirmed = clips.update_candidate(
            stale["id"],
            title=stale["title"],
            start=stale["start"],
            end=stale["end"],
            selected=True,
            expected_revision=stale["revision"],
            confirm_current_source=True,
        )["candidates"][0]
        self.assertFalse(confirmed["stale"])

        video = self.root / "confirmed-source.mp4"
        video.write_bytes(b"placeholder")
        db = database.get_db()
        db.execute(
            "UPDATE projects SET video_path=? WHERE id=?", (str(video), self.project_id)
        )
        db.commit()
        db.close()
        with patch.object(clips.task_manager, "run_background"):
            task_id, render_ids = clips.create_render_batch(
                self.project_id,
                [{"candidate_id": confirmed["id"], "aspect_ratio": "9:16"}],
                False,
            )
        self.assertTrue(task_id)
        self.assertEqual(len(render_ids), 1)

    def test_project_package_v2_roundtrip_keeps_text_and_clip_definitions(self):
        with (
            patch.object(content_packs, "assigned_provider", return_value=self._provider()),
            patch.object(content_packs.task_manager, "run_background"),
        ):
            pack, _ = content_packs.create_content_pack(
                self.project_id, "可迁移发布包", "original", "zh", False
            )
        db = database.get_db()
        db.execute(
            """UPDATE content_pack_sections
                  SET content_json='{"overview":"保留人工内容"}',status='ready'
                WHERE pack_id=? AND kind='summary'""",
            (pack["id"],),
        )
        db.commit()
        db.close()
        self._create_clip_set()

        package = project_packages.export_project_package(self.project_id)
        with zipfile.ZipFile(package) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["version"], 2)
            self.assertIn("data/content_packs.json", archive.namelist())
            self.assertIn("data/clip_layouts.json", archive.namelist())
        imported = project_packages.import_project_package(str(package))
        db = database.get_db()
        counts = {
            "packs": db.execute(
                "SELECT COUNT(*) FROM content_packs WHERE project_id=?", (imported["project_id"],)
            ).fetchone()[0],
            "sets": db.execute(
                "SELECT COUNT(*) FROM clip_sets WHERE project_id=?", (imported["project_id"],)
            ).fetchone()[0],
            "renders": db.execute(
                "SELECT COUNT(*) FROM clip_renders WHERE project_id=?", (imported["project_id"],)
            ).fetchone()[0],
        }
        copied = db.execute(
            """SELECT content_json FROM content_pack_sections s
                 JOIN content_packs p ON p.id=s.pack_id
                WHERE p.project_id=? AND s.kind='summary'""",
            (imported["project_id"],),
        ).fetchone()[0]
        db.close()
        self.assertEqual(counts, {"packs": 1, "sets": 1, "renders": 0})
        self.assertEqual(json.loads(copied)["overview"], "保留人工内容")

    def test_startup_recovery_finalizes_feature_owned_pending_state(self):
        with (
            patch.object(content_packs, "assigned_provider", return_value=self._provider()),
            patch.object(content_packs.task_manager, "run_background"),
        ):
            pack, _ = content_packs.create_content_pack(
                self.project_id, "中断发布包", "original", "zh", False
            )
        with (
            patch.object(clips, "assigned_provider", return_value=self._provider()),
            patch.object(clips.task_manager, "run_background"),
        ):
            clip_set_id, _ = clips.create_clip_set(
                self.project_id, "中断短片", 3, 30, 90
            )
        db = database.get_db()
        candidate_id = str(uuid.uuid4())
        db.execute(
            """INSERT INTO clip_candidates(
                   id,clip_set_id,title,hook,reason,score,start,end,
                   start_segment_index,end_segment_index,selected,revision,
                   source_confirmed_revision,sort_order,created_at,updated_at
               ) VALUES (?,?,?,'','',0,0,31,1,2,1,0,0,0,'now','now')""",
            (candidate_id, clip_set_id, "中断候选"),
        )
        db.execute(
            """INSERT INTO clip_renders(
                   id,project_id,candidate_id,aspect_ratio,configuration_fingerprint,
                   status,created_at,updated_at
               ) VALUES ('interrupted-render',?,?, '9:16','fingerprint','running','now','now')""",
            (self.project_id, candidate_id),
        )
        db.execute(
            "UPDATE content_packs SET status='generating' WHERE id=?", (pack["id"],)
        )
        db.execute(
            "UPDATE content_pack_sections SET status='generating' WHERE pack_id=?",
            (pack["id"],),
        )
        db.execute(
            """INSERT INTO tasks(
                   id,project_id,type,status,step,progress,message,recoverable,
                   available_actions,details,logs,created_at,updated_at
               ) VALUES (
                   'interrupted-paused',?,'translate','paused','translating',35,
                   '已暂停',0,'[]','{\"target_language\":\"en\"}',
                   '[{\"time\":\"before\",\"level\":\"info\",\"step\":\"translating\",\"message\":\"已暂停\"}]',
                   'before','before'
               )""",
            (self.project_id,),
        )
        db.execute(
            """INSERT INTO tasks(
                   id,project_id,type,status,step,progress,message,recoverable,
                   available_actions,details,logs,created_at,updated_at
               ) VALUES (
                   'interrupted-running',?,'transcribe','running','transcribing',42,
                   '正在转写',0,'[]','{\"model_id\":\"small\",\"runtime\":\"cpu\"}',
                   '[]','before','before'
               )""",
            (self.project_id,),
        )
        db.execute(
            """INSERT INTO tasks(
                   id,project_id,type,status,progress,message,details,logs,
                   available_actions,created_at,updated_at
               ) VALUES (
                   'already-complete',?,'render','success',100,'完成','{}','[]','[]','before','before'
               )""",
            (self.project_id,),
        )
        db.execute(
            """INSERT INTO transcription_runs(
                   id,project_id,task_id,model,status,started_at
               ) VALUES ('interrupted-run',?,'interrupted-running','small','running','before')""",
            (self.project_id,),
        )
        db.execute(
            """INSERT INTO transcription_segments(
                   id,run_id,project_id,idx,start,end,text,is_draft
               ) VALUES ('interrupted-draft','interrupted-run',?,1,0,1,'draft survives',1)""",
            (self.project_id,),
        )
        db.execute(
            """INSERT INTO ai_batch_results(
                   task_id,project_id,operation,batch_index,input_fingerprint,
                   status,result_json,attempts,error,updated_at
               ) VALUES (
                   'interrupted-paused',?,'translate',1,'fingerprint','running','[]',1,'','before'
               )""",
            (self.project_id,),
        )
        db.commit()
        db.close()

        interrupted = database.mark_interrupted_tasks()
        db = database.get_db()
        render = db.execute(
            "SELECT status,error FROM clip_renders WHERE id='interrupted-render'"
        ).fetchone()
        clip_status = db.execute(
            "SELECT status FROM clip_sets WHERE id=?", (clip_set_id,)
        ).fetchone()[0]
        pack_status = db.execute(
            "SELECT status FROM content_packs WHERE id=?", (pack["id"],)
        ).fetchone()[0]
        section_statuses = {
            row[0] for row in db.execute(
                "SELECT status FROM content_pack_sections WHERE pack_id=?", (pack["id"],)
            )
        }
        paused_task = db.execute(
            "SELECT * FROM tasks WHERE id='interrupted-paused'"
        ).fetchone()
        running_task = db.execute(
            "SELECT * FROM tasks WHERE id='interrupted-running'"
        ).fetchone()
        complete_task = db.execute(
            "SELECT status,message FROM tasks WHERE id='already-complete'"
        ).fetchone()
        run = db.execute(
            "SELECT * FROM transcription_runs WHERE id='interrupted-run'"
        ).fetchone()
        draft_count = db.execute(
            "SELECT COUNT(*) FROM transcription_segments WHERE run_id='interrupted-run'"
        ).fetchone()[0]
        ai_batch = db.execute(
            "SELECT status,error FROM ai_batch_results WHERE task_id='interrupted-paused'"
        ).fetchone()
        db.close()
        self.assertEqual((render["status"], render["error"]), ("failed", "应用在渲染期间退出"))
        self.assertEqual(clip_status, "failed")
        self.assertEqual(pack_status, "failed")
        self.assertEqual(section_statuses, {"failed"})
        self.assertIn("interrupted-paused", {item["id"] for item in interrupted})
        self.assertEqual(paused_task["status"], "failed")
        self.assertEqual(paused_task["error_code"], "APP_INTERRUPTED")
        self.assertTrue(paused_task["recoverable"])
        self.assertEqual(json.loads(paused_task["available_actions"]), ["retry"])
        paused_details = json.loads(paused_task["details"])
        self.assertEqual(paused_details["interruption"]["previous_status"], "paused")
        self.assertTrue(paused_details["interruption"]["published_data_preserved"])
        paused_logs = json.loads(paused_task["logs"])
        self.assertEqual(len(paused_logs), 2)
        self.assertEqual(paused_logs[-1]["message"], "检测到上次运行被中断")
        self.assertIn("不会擅自继续", paused_details["failure_suggestion"])
        self.assertEqual(running_task["status"], "failed")
        self.assertEqual(running_task["error_code"], "APP_INTERRUPTED")
        self.assertEqual((complete_task["status"], complete_task["message"]), ("success", "完成"))
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_code"], "APP_INTERRUPTED")
        self.assertIsNotNone(run["finished_at"])
        self.assertEqual(draft_count, 1)
        self.assertEqual(ai_batch["status"], "failed")
        self.assertIn("未自动重试", ai_batch["error"])
        self.assertEqual(database.mark_interrupted_tasks(), [])


if __name__ == "__main__":
    unittest.main()
