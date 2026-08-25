from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.subtitles import ai_review, canonical_state, editor, publish_status, review_publish


def seg(sid: str, start: int, end: int, text: str) -> dict:
    return {"segment_id": sid, "start_ms": start, "end_ms": end, "raw_text": text}


class ReviewerV2FailClosedTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name); self.job = self.data / "jobs" / "job-1"; self.job.mkdir(parents=True)
        segments = [seg("seg-0001", 0, 3000, "甲"), seg("seg-0002", 3000, 6000, "乙")]
        (self.job / "subtitles.json").write_text(json.dumps({"segments": segments}, ensure_ascii=False), encoding="utf-8")
        corrected = [{**x, "text": x["raw_text"], "corrected_text": x["raw_text"] + "校"} for x in segments]
        (self.job / "subtitles-corrected.json").write_text(json.dumps({"segments": corrected}, ensure_ascii=False), encoding="utf-8")
        self.old = (ai_review.DATA_DIR, editor.DATA_DIR, editor.JOBS_DIR)
        ai_review.DATA_DIR = self.data; editor.DATA_DIR = self.data; editor.JOBS_DIR = self.data / "jobs"
        self.addCleanup(self.restore)
        app = FastAPI(); app.include_router(ai_review.router); app.include_router(editor.router)
        self.client = TestClient(app)

    def restore(self) -> None:
        ai_review.DATA_DIR, editor.DATA_DIR, editor.JOBS_DIR = self.old
        ai_review._SPEAKER_CACHE.clear()

    def revision(self, n: int) -> dict:
        return {"revision": n, "created_at": "x", "source": "test", "content_sha256": "x", "cues": [
            {"cue_id": "cue-0001", "text": "甲校", "source_segment_ids": ["seg-0001"], "start_ms": 0, "end_ms": 3000},
            {"cue_id": "cue-0002", "text": "乙校", "source_segment_ids": ["seg-0002"], "start_ms": 3000, "end_ms": 6000},
        ]}

    def write_review(self, active=None, revisions=None, candidates=None, revision=0) -> None:
        (self.job / "ai-subtitle-review.json").write_text(json.dumps({"revision": revision, "active_revision": active, "revisions": revisions or [], "candidates": candidates or []}, ensure_ascii=False), encoding="utf-8")

    def test_missing_declared_active_never_falls_back(self) -> None:
        self.write_review(active=7, revision=7)
        with self.assertRaises(HTTPException) as ctx: canonical_state.canonical_cues(self.job)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_editor_write_blocked_when_ai_active(self) -> None:
        self.write_review(active=1, revisions=[self.revision(1)], revision=1)
        r = self.client.patch("/api/v1/subtitles/job-1/segments/seg-0001", json={"text": "X", "expected_revision": 0})
        self.assertEqual(r.status_code, 409)

    def test_batch_preview_blocked_while_candidates_exist(self) -> None:
        self.write_review(candidates=[{"change_id": "c", "status": "pending"}])
        r = self.client.post("/api/v1/subtitles/replace/preview", json={"search": "甲", "replacement": "丙", "subtitle_ids": ["job-1"]})
        self.assertEqual(r.status_code, 409)

    def test_before_shape_cannot_mix_segment_and_cue(self) -> None:
        working = ai_review.working_segments(self.job); idx = {x["segment_id"]: {**x, "_index": i} for i, x in enumerate(working)}
        cues, _ = canonical_state.canonical_cues(self.job)
        proposal = ai_review.CandidateProposal(change_type="mixed", source_segment_ids=["seg-0001", "seg-0002"], before=[{"source_segment_ids": ["seg-0001"], "text": "甲校"}, {"segment_id": "seg-0002", "text": "乙校"}], after=[{"source_segment_ids": ["seg-0001"], "text": "甲校"}, {"source_segment_ids": ["seg-0002"], "text": "乙校"}], reason="x", confidence=.9, risk="low")
        with self.assertRaises(HTTPException) as ctx: ai_review._validate_candidate(proposal, idx, working_cues=cues)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_cue_reflow_cannot_bypass_conservation(self) -> None:
        merged = {"revision": 1, "created_at": "x", "source": "test", "content_sha256": "x", "cues": [{"cue_id": "cue-0001", "text": "甲校乙校", "source_segment_ids": ["seg-0001", "seg-0002"], "start_ms": 0, "end_ms": 6000}]}
        self.write_review(active=1, revisions=[merged], revision=1)
        working = ai_review.working_segments(self.job); idx = {x["segment_id"]: {**x, "_index": i} for i, x in enumerate(working)}
        cues, _ = canonical_state.canonical_cues(self.job)
        proposal = ai_review.CandidateProposal(change_type="cross_segment_reflow", source_segment_ids=["seg-0001", "seg-0002"], before=[{"source_segment_ids": ["seg-0001", "seg-0002"], "text": "甲校乙校"}], after=[{"source_segment_ids": ["seg-0001", "seg-0002"], "text": "任意改寫"}], reason="x", confidence=.9, risk="low")
        with self.assertRaises(HTTPException) as ctx: ai_review._validate_candidate(proposal, idx, working_cues=cues)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_rollback_blocked_with_candidates(self) -> None:
        self.write_review(active=2, revisions=[self.revision(1), {**self.revision(2), "revision": 2}], candidates=[{"change_id": "c", "status": "pending"}], revision=2)
        r = self.client.post("/api/v1/subtitles/job-1/ai-review/revisions/1/rollback")
        self.assertEqual(r.status_code, 409)

    def test_historical_export_requires_full_source_coverage(self) -> None:
        bad = {"revision": 1, "created_at": "x", "source": "legacy", "content_sha256": "x", "cues": [{"cue_id": "cue-0001", "text": "甲", "source_segment_ids": ["seg-0001"], "start_ms": 0, "end_ms": 3000}]}
        self.write_review(active=2, revisions=[bad, {**self.revision(2), "revision": 2}], revision=2)
        r = self.client.get("/api/v1/subtitles/job-1/ai-review/export/srt/1")
        self.assertEqual(r.status_code, 409)

    def test_publish_status_blocks_editor_revision_for_ai_active(self) -> None:
        self.write_review(active=1, revisions=[self.revision(1)], revision=1)
        with patch.object(publish_status.base, "_directory", return_value=(self.job, "job")):
            result = publish_status.get_publish_status("job-1")
        self.assertEqual(result["status"], "ambiguous"); self.assertFalse(result["can_publish"])

    def test_review_publish_blocks_editor_revision_for_ai_active(self) -> None:
        self.write_review(active=1, revisions=[self.revision(1)], revision=1)
        request = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b"", "scheme": "http", "server": ("test", 80), "client": ("test", 1)})
        with patch.object(review_publish.base, "_mutation_actor", return_value="tester"), patch.object(review_publish.base, "_directory", return_value=(self.job, "job")):
            with self.assertRaises(HTTPException) as ctx: review_publish.publish_reviewed("job-1", review_publish.PublishReviewedRequest(expected_revision=0), request)
        self.assertEqual(ctx.exception.status_code, 409)


if __name__ == "__main__": unittest.main()
