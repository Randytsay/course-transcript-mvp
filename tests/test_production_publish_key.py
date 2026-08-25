"""PR #86 final review fixes — production route integration tests.

Uses the REAL production app (app.api_hardened:app) for POST
/api/v1/subtitles/{id}/publish, not the legacy editor router.

Blocker 1: publish must reject a stale expected_publication_key with zero
side effects (no Drive write / no DB event / no history / no intent).
Blocker 2: history is keyed by publication_key; AI R1/R2 both mapping to
editor revision 0 produce distinct histories and distinct events.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.subtitles import canonical_state, editor, publish_status


def seg(sid: str, start: int, end: int, text: str) -> dict:
    return {"segment_id": sid, "start_ms": start, "end_ms": end, "raw_text": text}


class ProductionPublishKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        self.job = self.data / "jobs" / "job-1"
        self.job.mkdir(parents=True)
        segments = [seg("seg-0001", 0, 3000, "第一段原始"), seg("seg-0002", 3000, 6000, "第二段原始")]
        (self.job / "subtitles.json").write_text(
            json.dumps({"source": "chirp", "segments": segments}, ensure_ascii=False),
            encoding="utf-8",
        )
        corrected = [{**x, "text": x["raw_text"], "corrected_text": x["raw_text"] + "校"} for x in segments]
        (self.job / "subtitles-corrected.json").write_text(
            json.dumps({"source": "gemini", "segments": corrected}, ensure_ascii=False),
            encoding="utf-8",
        )
        self._write_db()
        self.old = (
            editor.DATA_DIR,
            editor.JOBS_DIR,
            canonical_state.__dict__.get("_sentinel", None),
        )
        editor.DATA_DIR = self.data
        editor.JOBS_DIR = self.data / "jobs"
        self.addCleanup(self.restore)
        # Import production app lazily so DATA_DIR patching applies first.
        from app.api_hardened import app as production_app  # noqa: E402

        from fastapi.testclient import TestClient

        self.client = TestClient(production_app)
        from app.subtitles import ai_review

        ai_review.DATA_DIR = self.data
        self.addCleanup(setattr, ai_review, "DATA_DIR", self.old[0] if False else ai_review.DATA_DIR)

    def restore(self) -> None:
        editor.DATA_DIR, editor.JOBS_DIR = self.old[0], self.old[1]

    def _write_db(self) -> None:
        database = self.data / "course-transcript.db"
        with sqlite3.connect(database) as connection:
            connection.executescript(
                """
                CREATE TABLE batches (
                    id TEXT PRIMARY KEY, status TEXT NOT NULL,
                    completed_count INTEGER DEFAULT 0,
                    failed_count INTEGER DEFAULT 0,
                    updated_at TEXT, revision INTEGER DEFAULT 0
                );
                CREATE TABLE jobs (
                    id TEXT PRIMARY KEY, batch_id TEXT,
                    source_path TEXT NOT NULL, status TEXT NOT NULL,
                    stage_detail TEXT, active_stage TEXT, progress REAL,
                    error TEXT, revision INTEGER DEFAULT 0, updated_at TEXT
                );
                CREATE TABLE job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL, event_type TEXT NOT NULL,
                    actor TEXT, payload_json TEXT NOT NULL, created_at TEXT
                );
                INSERT INTO batches(id, status) VALUES ('batch-job-1', 'completed');
                INSERT INTO jobs(id, batch_id, source_path, status)
                VALUES ('job-1', 'batch-job-1', 'gdrive:folder/job-1.mp4', 'awaiting_review');
                """
            )

    # -- helpers ---------------------------------------------------------------

    def build_ai_r1(self) -> str:
        state = self.client.get("/api/v1/subtitles/job-1/ai-review").json()
        propose = self.client.post(
            "/api/v1/subtitles/job-1/ai-review/candidates",
            json={
                "expected_revision": state["revision"],
                "candidates": [{
                    "change_type": "asr_typo",
                    "source_segment_ids": ["seg-0001"],
                    "before": [{"segment_id": "seg-0001", "text": "第一段原始校"}],
                    "after": [{"source_segment_ids": ["seg-0001"], "text": "AI版第一段"}],
                    "reason": "typo", "confidence": 0.95, "risk": "low",
                    "high_review_required": True,
                }],
            },
        )
        assert propose.status_code == 200, propose.text
        change_id = propose.json()["candidates"][0]["change_id"]
        decide = self.client.post(
            "/api/v1/subtitles/job-1/ai-review/candidates/decide",
            json={"change_id": change_id, "decision": "accept"},
        )
        assert decide.status_code == 200, decide.text
        published = self.client.post(
            "/api/v1/subtitles/job-1/ai-review/publish", json={"base_revision": 0}
        )
        assert published.status_code == 200, published.text
        return str(published.json()["publication_key"])

    def cue_edit_to_r2(self) -> str:
        edit = self.client.patch(
            "/api/v1/subtitles/job-1/ai-review/cue",
            json={
                "source_segment_ids": ["seg-0002"],
                "current_text": "第二段原始校",
                "new_text": "R2第二段修改",
            },
        )
        assert edit.status_code == 200, edit.text
        return str(edit.json()["publication_key"])

    def publish_via_production_route(self, publication_key: str | None):
        body: dict = {
            "expected_revision": 0,
            "output_formats": ["srt", "txt"],
        }
        if publication_key is not None:
            body["expected_publication_key"] = publication_key
        with patch(
            "app.subtitles.review_publish.publish_outputs"
        ) as mock_publish, patch(
            "app.subtitles.review_publish.base._job_record"
        ) as job_record:
            job_record.return_value = {
                "id": "job-1", "status": "awaiting_review",
                "source_path": "gdrive:folder/job-1.mp4", "source_name": "job-1",
            }
            mock_publish.return_value = {"status": "completed", "backup_count": 0, "files": {}}
            response = self.client.post("/api/v1/subtitles/job-1/publish", json=body)
            drive_calls = mock_publish.call_count
        return response, drive_calls

    def _counts(self) -> tuple[int, int, int]:
        """(db event count, history count, marker intent present)."""
        import sqlite3  # noqa: F401

        events = publish_status._publication_events(self.job)
        editor_state = json.loads(
            (self.job / "subtitle-editor.json").read_text(encoding="utf-8")
        ) if (self.job / "subtitle-editor.json").exists() else {"history": []}
        history = [
            item for item in editor_state.get("history", [])
            if isinstance(item, dict) and item.get("type") == "drive_publish"
        ]
        marker_exists = (self.job / "drive-delivery-state.json").exists()
        return len(events), len(history), marker_exists

    def _event_keys(self) -> list[str | None]:
        return [item.get("publication_key") for item in publish_status._publication_events(self.job)]

    def _history_keys(self) -> list[str | None]:
        editor_state = json.loads(
            (self.job / "subtitle-editor.json").read_text(encoding="utf-8")
        )
        return [
            item.get("publication_key")
            for item in editor_state["history"]
            if isinstance(item, dict) and item.get("type") == "drive_publish"
        ]

    # -- Blocker 1 --------------------------------------------------------------

    def test_stale_r1_key_publish_is_409_with_zero_side_effects(self) -> None:
        key_r1 = self.build_ai_r1()
        key_r2 = self.cue_edit_to_r2()
        self.assertNotEqual(key_r1, key_r2)

        before_events, before_history, _ = self._counts()

        response, drive_calls = self.publish_via_production_route(key_r1)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("Canonical subtitle changed", response.json()["detail"])
        self.assertEqual(drive_calls, 0)

        after_events, after_history, marker_created = self._counts()
        self.assertEqual(after_events - before_events, 0)
        self.assertEqual(after_history - before_history, 0)
        self.assertFalse(marker_created)

    def test_missing_key_on_ai_active_publish_is_409(self) -> None:
        self.build_ai_r1()
        before_events, before_history, _ = self._counts()
        response, drive_calls = self.publish_via_production_route(None)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(drive_calls, 0)
        after_events, after_history, _ = self._counts()
        self.assertEqual(after_events - before_events, 0)
        self.assertEqual(after_history - before_history, 0)

    def test_fresh_r2_key_publish_succeeds(self) -> None:
        self.build_ai_r1()
        key_r2 = self.cue_edit_to_r2()
        response, drive_calls = self.publish_via_production_route(key_r2)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(drive_calls, 1)
        self.assertEqual(response.json()["publication_key"], key_r2)

    # -- Blocker 2 ---------------------------------------------------------------

    def test_r1_and_r2_same_editor_revision_get_distinct_history_and_events(self) -> None:
        key_r1 = self.build_ai_r1()
        r1_response, _ = self.publish_via_production_route(key_r1)
        self.assertEqual(r1_response.status_code, 200, r1_response.text)

        key_r2 = self.cue_edit_to_r2()
        self.assertNotEqual(key_r1, key_r2)
        r2_response, _ = self.publish_via_production_route(key_r2)
        self.assertEqual(r2_response.status_code, 200, r2_response.text)

        event_keys = self._event_keys()
        history_keys = self._history_keys()
        self.assertEqual(event_keys.count(key_r1), 1)
        self.assertEqual(event_keys.count(key_r2), 1)
        self.assertEqual(history_keys.count(key_r1), 1)
        self.assertEqual(history_keys.count(key_r2), 1)

    def test_same_key_replay_has_zero_deltas(self) -> None:
        key_r1 = self.build_ai_r1()
        first, _ = self.publish_via_production_route(key_r1)
        self.assertEqual(first.status_code, 200)

        before_events = self._event_keys().count(key_r1)
        before_history = self._history_keys().count(key_r1)

        replay, drive_calls = self.publish_via_production_route(key_r1)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json().get("idempotent_replay"))
        self.assertEqual(drive_calls, 0)
        self.assertEqual(self._event_keys().count(key_r1), before_events)
        self.assertEqual(self._history_keys().count(key_r1), before_history)


if __name__ == "__main__":
    unittest.main()
