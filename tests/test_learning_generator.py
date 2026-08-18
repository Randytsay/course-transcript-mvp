from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.learning.generator import _normalize_pack, generate_study_pack
from app.learning.source import LearningSourceStore
from app.learning.store import LearningStore
from app.review.admin_store import ReviewAdminStore
from app.review.baseline import ensure_import_baseline
from app.review.store import ReviewConflict, ReviewNotFound, ReviewStore


class LearningGeneratorTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "course-transcript.db"
        review = ReviewStore(self.database)
        review.upsert_video(
            youtube_video_id="video-1",
            playlist_id="playlist-1",
            title="彌勒大成佛經 第 1 講",
            duration_ms=20_000,
        )
        review.import_subtitle_segments(
            youtube_video_id="video-1",
            segments=[
                {"segment_index": 1, "start_ms": 0, "end_ms": 5_000, "text": "佛告阿難"},
                {"segment_index": 2, "start_ms": 5_000, "end_ms": 10_000, "text": "彌勒大成佛經"},
            ],
        )
        self.admin = ReviewAdminStore(self.database)
        self.baseline = ensure_import_baseline(
            self.admin,
            youtube_video_id="video-1",
            triggered_by="owner@example.test",
        )
        self.store = LearningStore(self.database)
        self.source = LearningSourceStore(self.database)

    def test_generation_fails_closed_until_owner_approves_learning_source(self) -> None:
        with self.assertRaises(ReviewNotFound):
            generate_study_pack(
                self.store,
                youtube_video_id="video-1",
                actor="owner@example.test",
            )

    def test_normalizer_drops_unsupported_items_and_repairs_stable_ids(self) -> None:
        segments = [
            {"segment_index": 1, "start_ms": 0, "end_ms": 5_000, "text": "佛告阿難"},
            {"segment_index": 2, "start_ms": 5_000, "end_ms": 10_000, "text": "彌勒大成佛經"},
        ]
        content, citations = _normalize_pack(
            {
                # Missing valid evidence means the overview is retained only as an empty shell.
                "overview": {"title": "不應採用", "summary": "沒有可驗證來源", "source_segment_indexes": [999]},
                "detailed_notes": [{"heading": "經名", "points": ["彌勒大成佛經"], "source_segment_indexes": [2]}],
                "quick_review_10m": [],
                "quick_review_3m": [],
                "key_points": [{"text": "佛告阿難", "source_segment_indexes": [1]}],
                "qa": [
                    {"question": "沒有答案", "answer": "", "source_segment_indexes": [1]},
                    {"question": "本堂經名？", "answer": "彌勒大成佛經", "source_segment_indexes": [2]},
                ],
                "flashcards": [
                    {"id": "dup", "front": "經名？", "back": "彌勒大成佛經", "source_segment_indexes": [2]},
                    {"id": "dup", "front": "開頭？", "back": "佛告阿難", "source_segment_indexes": [1]},
                    {"front": "空答案", "back": "", "source_segment_indexes": [1]},
                ],
                "quiz": [
                    {"id": "quiz-1", "question": "合法題", "choices": ["甲", "乙"], "answer_index": 1, "source_segment_indexes": [1]},
                    {"id": "bad", "question": "答案索引錯誤", "choices": ["甲", "乙"], "answer_index": 4, "source_segment_indexes": [1]},
                    {"id": "bad-2", "question": "選項不足", "choices": ["甲"], "answer_index": 0, "source_segment_indexes": [1]},
                ],
                "glossary": [{"term": "彌勒", "explanation": "", "source_segment_indexes": [2]}],
            },
            segments,
        )
        self.assertEqual(content["overview"]["summary"], "")
        self.assertEqual(len(content["qa"]), 1)
        self.assertEqual(len(content["flashcards"]), 2)
        self.assertEqual(content["flashcards"][0]["id"], "dup")
        self.assertEqual(content["flashcards"][1]["id"], "card-2")
        self.assertEqual(len(content["quiz"]), 1)
        self.assertEqual(content["quiz"][0]["answer_index"], 1)
        self.assertEqual(content["glossary"], [])
        self.assertEqual({item["segment_index"] for item in citations}, {1, 2})

    @patch("app.learning.generator._vertex_json")
    def test_generation_uses_approved_version_and_server_rebuilds_citations(self, vertex_json) -> None:
        self.source.approve_latest(
            youtube_video_id="video-1",
            actor="owner@example.test",
        )
        vertex_json.return_value = {
            "overview": {
                "title": "第一講",
                "summary": "介紹本堂重點",
                "source_segment_indexes": [1],
            },
            "detailed_notes": [
                {
                    "heading": "經名",
                    "points": ["彌勒大成佛經"],
                    "source_segment_indexes": [2, 999],
                }
            ],
            "quick_review_10m": [],
            "quick_review_3m": [{"text": "記住經名", "source_segment_indexes": [2]}],
            "key_points": [{"text": "佛告阿難", "source_segment_indexes": [1]}],
            "qa": [],
            "flashcards": [
                {
                    "id": "card-1",
                    "front": "本堂經名？",
                    "back": "彌勒大成佛經",
                    "source_segment_indexes": [2],
                }
            ],
            "quiz": [],
            "glossary": [],
        }
        result = generate_study_pack(
            self.store,
            youtube_video_id="video-1",
            actor="owner@example.test",
        )
        self.assertTrue(result["generated"])
        artifact = result["artifact"]
        self.assertEqual(artifact["subtitle_version_id"], self.baseline["id"])
        self.assertEqual(artifact["source_sha256"], self.baseline["content_sha256"])
        self.assertEqual(
            artifact["content"]["detailed_notes"][0]["source_segment_indexes"],
            [2],
        )
        citation = next(item for item in artifact["citations"] if item["segment_index"] == 2)
        self.assertEqual(citation["start_ms"], 5_000)
        self.assertEqual(citation["text"], "彌勒大成佛經")

        # A duplicate request for the same source/prompt must not spend another model call.
        again = generate_study_pack(
            self.store,
            youtube_video_id="video-1",
            actor="owner@example.test",
        )
        self.assertFalse(again["generated"])
        vertex_json.assert_called_once()

    @patch("app.learning.generator._vertex_json")
    def test_new_subtitle_version_blocks_generation_until_owner_reapproves(self, vertex_json) -> None:
        self.source.approve_latest(
            youtube_video_id="video-1",
            actor="owner@example.test",
        )
        with self.admin.transaction() as connection:
            connection.execute(
                "UPDATE review_subtitle_segments SET working_text = ?, revision = revision + 1 WHERE youtube_video_id = ? AND segment_index = 2",
                ("彌勒大成佛經。", "video-1"),
            )
            latest = self.admin._snapshot_video(
                connection,
                youtube_video_id="video-1",
                actor="owner@example.test",
                source="test",
                source_ref=None,
            )
        with self.assertRaises(ReviewConflict):
            generate_study_pack(
                self.store,
                youtube_video_id="video-1",
                actor="owner@example.test",
                force=True,
            )
        vertex_json.assert_not_called()

        self.source.approve_latest(youtube_video_id="video-1", actor="owner@example.test")
        self.assertTrue(self.source.status("video-1")["source_is_latest"])
        vertex_json.return_value = {
            "overview": {"title": "新版", "summary": "新版來源", "source_segment_indexes": [1]},
            "detailed_notes": [{"heading": "經名", "points": ["新版核定來源"], "source_segment_indexes": [2]}],
            "quick_review_10m": [],
            "quick_review_3m": [],
            "key_points": [{"text": "重點", "source_segment_indexes": [2]}],
            "qa": [], "flashcards": [], "quiz": [], "glossary": [],
        }
        generated = generate_study_pack(
            self.store,
            youtube_video_id="video-1",
            actor="owner@example.test",
            force=True,
        )
        self.assertEqual(generated["artifact"]["subtitle_version_id"], latest["id"])
        vertex_json.assert_called_once()


if __name__ == "__main__":
    unittest.main()
