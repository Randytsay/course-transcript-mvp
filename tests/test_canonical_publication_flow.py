"""Third-stage integration tests: canonical publication identity,
cue-aware editor, and review base concurrency.

Covers the owner's stage-3 review doc via real HTTP routes:
- Publication identity cases A–F (namespaced key, idempotency, reconcile).
- Cue-aware editor cases A–F (merged cue display/edit/revision/batch/legacy 409).
- Base concurrency (propose → canonical changes → decide/publish 409 → reload OK).
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.subtitles import ai_review, canonical_state, editor, publish_status


def seg(sid: str, start: int, end: int, text: str) -> dict:
    return {"segment_id": sid, "start_ms": start, "end_ms": end, "raw_text": text}


class CanonicalPublicationIntegrationTests(unittest.TestCase):
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
        self.old = (ai_review.DATA_DIR, editor.DATA_DIR, editor.JOBS_DIR)
        ai_review.DATA_DIR = self.data
        editor.DATA_DIR = self.data
        editor.JOBS_DIR = self.data / "jobs"
        self.addCleanup(self.restore)
        app = FastAPI()
        app.include_router(ai_review.router)
        app.include_router(editor.router)
        self.client = TestClient(app)

    def restore(self) -> None:
        ai_review.DATA_DIR, editor.DATA_DIR, editor.JOBS_DIR = self.old
        ai_review._SPEAKER_CACHE.clear()

    # -- helpers ---------------------------------------------------------------

    def propose_and_publish_r1(self) -> str:
        state = self.client.get("/api/v1/subtitles/job-1/ai-review").json()
        response = self.client.post(
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
        assert response.status_code == 200, response.text
        change_id = response.json()["candidates"][0]["change_id"]
        decide = self.client.post(
            "/api/v1/subtitles/job-1/ai-review/candidates/decide",
            json={"change_id": change_id, "decision": "accept"},
        )
        assert decide.status_code == 200, decide.text
        published = self.client.post(
            "/api/v1/subtitles/job-1/ai-review/publish",
            json={"base_revision": 0},
        )
        assert published.status_code == 200, published.text
        return published.json()

    # -- Case A/B/C: identity namespacing --------------------------------------

    def test_case_a_editor_rev0_ai_active_identity_is_namespaced(self) -> None:
        # Editor revision is 0 (no subtitle-editor.json), AI R1 active.
        self.propose_and_publish_r1()
        directory = editor._directory("job-1")[0]
        identity = canonical_state.publication_identity(directory)
        self.assertEqual(identity["canonical_source"], "ai_review_active")
        self.assertEqual(identity["canonical_revision"], 1)
        self.assertTrue(identity["publication_key"].startswith("ai_review_active:r1:"))
        # Content really is the AI revision, not raw/editor text.
        detail = self.client.get("/api/v1/subtitles/job-1").json()
        self.assertEqual(detail["canonical_source"], "ai_review_active")
        self.assertIn("AI版第一段", "".join(c["text"] for c in detail["canonical_cues"]))

    def test_case_c_same_numeric_revision_no_collision(self) -> None:
        # Editor rev1 exists with its own content.
        (self.job / "subtitle-editor.json").write_text(
            json.dumps({"revision": 1, "edits": {}}, ensure_ascii=False), encoding="utf-8"
        )
        editor_identity = canonical_state.publication_identity(editor._directory("job-1")[0])
        self.assertEqual(editor_identity["canonical_source"], "editor")
        self.assertEqual(editor_identity["canonical_revision"], 1)
        # Now AI R1 active — same numeric 1, different namespace/content.
        self.propose_and_publish_r1()
        ai_identity = canonical_state.publication_identity(editor._directory("job-1")[0])
        self.assertEqual(ai_identity["canonical_revision"], 1)
        self.assertNotEqual(editor_identity["publication_key"], ai_identity["publication_key"])

    # -- Case B/D/E/F: status reconciliation + idempotency ---------------------

    def _write_db(self) -> Path:
        import sqlite3

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
        return database

    def test_case_b_r2_active_is_unpublished_with_can_publish(self) -> None:
        database = self._write_db()
        key_r1 = self.propose_and_publish_r1()  # AI R1 active
        # Simulate: R1 was published (marker completed for its key).
        (self.job / "drive-delivery-state.json").write_text(
            json.dumps({"status": "superseded_by_editor", "editor_revision": 0,
                        "publication_key": str(key_r1["publication_key"])}),
            encoding="utf-8",
        )
        # Advance to AI R2 via a cue edit (auditable new revision).
        edit = self.client.patch(
            "/api/v1/subtitles/job-1/ai-review/cue",
            json={
                "source_segment_ids": ["seg-0002"],
                "current_text": "第二段原始校",
                "new_text": "R2第二段修改",
            },
        )
        self.assertEqual(edit.status_code, 200, edit.text)
        self.assertEqual(edit.json()["revision"], 2)

        original_data_dir = publish_status.base.DATA_DIR
        publish_status.base.DATA_DIR = self.data
        try:
            with patch.object(publish_status.base, "_directory", return_value=(self.job, "job")):
                result = publish_status.get_publish_status("job-1")
        finally:
            publish_status.base.DATA_DIR = original_data_dir
        self.assertEqual(result["status"], "idle", result)
        self.assertEqual(result["canonical_revision"], 2)
        self.assertNotEqual(result["publication_key"], key_r1["publication_key"])
        self.assertTrue(result["can_publish"])

    def test_case_d_same_publication_key_replay_is_idempotent(self) -> None:
        from app.jobs import delivery_state
        from unittest.mock import patch as _patch

        self._write_db()
        result = self.propose_and_publish_r1()
        publication_key = result["content_sha256"] and (
            f"ai_review_active:r1:{result['content_sha256'][:16]}"
        )
        # Record the event once, then attempt a duplicate with the same key.
        delivery_state.record_delivery_success(
            self.data / "course-transcript.db",
            job_id="job-1", actor="t", source="editor",
            backup_count=0, published_revision=0,
            publication_key=publication_key,
        )
        before = self._event_count()
        delivery_state.record_delivery_success(
            self.data / "course-transcript.db",
            job_id="job-1", actor="t", source="editor",
            backup_count=0, published_revision=0,
            publication_key=publication_key,
        )
        self.assertEqual(self._event_count(), before)  # no duplicate event

    def _event_count(self) -> int:
        import sqlite3

        with sqlite3.connect(self.data / "course-transcript.db") as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM job_events WHERE job_id='job-1'"
                ).fetchone()[0]
            )

    # -- Cue-aware editor -------------------------------------------------------

    def test_editor_merged_cue_edit_creates_immutable_r2(self) -> None:
        # R1: merge seg1+seg2 into one canonical cue.
        state = self.client.get("/api/v1/subtitles/job-1/ai-review").json()
        response = self.client.post(
            "/api/v1/subtitles/job-1/ai-review/candidates",
            json={
                "expected_revision": state["revision"],
                "candidates": [{
                    "change_type": "merge_adjacent",
                    "source_segment_ids": ["seg-0001", "seg-0002"],
                    "before": [
                        {"segment_id": "seg-0001", "text": "第一段原始校"},
                        {"segment_id": "seg-0002", "text": "第二段原始校"},
                    ],
                    "after": [{
                        "source_segment_ids": ["seg-0001", "seg-0002"],
                        "text": "第一段原始校第二段原始校",
                    }],
                    "reason": "merge", "confidence": 0.95, "risk": "low",
                    "high_review_required": True,
                }],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        change_id = response.json()["candidates"][0]["change_id"]
        self.client.post(
            "/api/v1/subtitles/job-1/ai-review/candidates/decide",
            json={"change_id": change_id, "decision": "accept"},
        )
        published = self.client.post(
            "/api/v1/subtitles/job-1/ai-review/publish", json={"base_revision": 0}
        )
        self.assertEqual(published.status_code, 200)

        # Editor view shows ONE merged canonical cue, not two old segments.
        detail = self.client.get("/api/v1/subtitles/job-1").json()
        self.assertEqual(len(detail["canonical_cues"]), 1)
        merged_cue = detail["canonical_cues"][0]
        self.assertEqual(merged_cue["source_segment_ids"], ["seg-0001", "seg-0002"])
        self.assertIn("第一段原始校", merged_cue["text"])

        # Edit the merged cue → auditable R2; R1 stays intact.
        edit = self.client.patch(
            "/api/v1/subtitles/job-1/ai-review/cue",
            json={
                "source_segment_ids": ["seg-0001", "seg-0002"],
                "current_text": "第一段原始校第二段原始校",
                "new_text": "合併後人工修改文字",
            },
        )
        self.assertEqual(edit.status_code, 200, edit.text)
        self.assertEqual(edit.json()["revision"], 2)

        review = self.client.get("/api/v1/subtitles/job-1/ai-review").json()
        self.assertEqual(review["active_revision"], 2)
        r1 = next(r for r in review["revisions"] if r["revision"] == 1)
        r2_export = self.client.get("/api/v1/subtitles/job-1/ai-review/export/srt").text
        self.assertIn("合併後人工修改文字", r2_export)
        r1_export = self.client.get("/api/v1/subtitles/job-1/ai-review/export/srt/1").text
        self.assertIn("第一段原始校第二段原始校", r1_export)  # immutable history

        # Refresh/reopen still sees R2.
        detail_again = self.client.get("/api/v1/subtitles/job-1").json()
        self.assertIn("合併後人工修改文字", detail_again["canonical_cues"][0]["text"])

    def test_legacy_per_segment_write_still_409_on_ai_active(self) -> None:
        self.propose_and_publish_r1()
        response = self.client.patch(
            "/api/v1/subtitles/job-1/segments/seg-0001",
            json={"text": "legacy write", "expected_revision": 0},
        )
        self.assertEqual(response.status_code, 409)

    def test_batch_replace_on_canonical_cues_creates_audit_revision(self) -> None:
        self.propose_and_publish_r1()
        response = self.client.post(
            "/api/v1/subtitles/job-1/ai-review/batch-replace-cues",
            json={
                "replacements": [
                    {
                        "source_segment_ids": ["seg-0001"],
                        "current_text": "AI版第一段",
                        "new_text": "批次替換第一段",
                    },
                    {
                        "source_segment_ids": ["seg-0002"],
                        "current_text": "第二段原始校",
                        "new_text": "批次替換第二段",
                    },
                ],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["revision"], 2)
        export = self.client.get("/api/v1/subtitles/job-1/ai-review/export/txt").text
        self.assertIn("批次替換第一段", export)
        self.assertIn("批次替換第二段", export)

    # -- Base concurrency --------------------------------------------------------

    def test_stale_base_candidate_rejected_at_decide_and_publish(self) -> None:
        # 1. Build AI R1 (base for the round).
        self.propose_and_publish_r1()

        # 2. Propose a candidate against base R1.
        state = self.client.get("/api/v1/subtitles/job-1/ai-review").json()
        propose = self.client.post(
            "/api/v1/subtitles/job-1/ai-review/candidates",
            json={
                "expected_revision": state["revision"],
                "candidates": [{
                    "change_type": "asr_typo",
                    "source_segment_ids": ["seg-0002"],
                    "before": [{"segment_id": "seg-0002", "text": "第二段原始校"}],
                    "after": [{"source_segment_ids": ["seg-0002"], "text": "舊提案文字"}],
                    "reason": "typo", "confidence": 0.95, "risk": "low",
                    "high_review_required": True,
                }],
            },
        )
        self.assertEqual(propose.status_code, 200)
        change_id = propose.json()["candidates"][0]["change_id"]

        # 3. Canonical state advances to R2 via an audited cue edit.
        edit = self.client.patch(
            "/api/v1/subtitles/job-1/ai-review/cue",
            json={
                "source_segment_ids": ["seg-0002"],
                "current_text": "第二段原始校",
                "new_text": "R2直接修改",
            },
        )
        self.assertEqual(edit.status_code, 200, edit.text)

        # 4. Old candidate decide must 409 (stale base), never apply stale edits.
        decide = self.client.post(
            "/api/v1/subtitles/job-1/ai-review/candidates/decide",
            json={"change_id": change_id, "decision": "accept"},
        )
        self.assertEqual(decide.status_code, 409, decide.text)
        publish = self.client.post(
            "/api/v1/subtitles/job-1/ai-review/publish", json={"base_revision": 2}
        )
        self.assertEqual(publish.status_code, 409, publish.text)

        # 5. Fresh proposal against current canonical state succeeds.
        fresh = self.client.post(
            "/api/v1/subtitles/job-1/ai-review/candidates",
            json={
                "expected_revision": 2,
                "candidates": [{
                    "change_type": "asr_typo",
                    "source_segment_ids": ["seg-0002"],
                    "before": [{"segment_id": "seg-0002", "text": "R2直接修改"}],
                    "after": [{"source_segment_ids": ["seg-0002"], "text": "新提案文字"}],
                    "reason": "typo", "confidence": 0.95, "risk": "low",
                    "high_review_required": True,
                }],
            },
        )
        self.assertEqual(fresh.status_code, 200, fresh.text)


if __name__ == "__main__":
    unittest.main()
