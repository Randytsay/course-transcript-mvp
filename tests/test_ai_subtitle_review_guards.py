"""Regression guards for PR #86 AI subtitle review invariants."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from app.subtitles import ai_review


class AIReviewGuardRegressionTests(unittest.TestCase):
    def _index(self) -> dict[str, dict]:
        return {
            "seg-0001": {
                "segment_id": "seg-0001", "raw_text": "甲", "working_text": "甲",
                "start_ms": 0, "end_ms": 3000, "_index": 0,
            },
            "seg-0002": {
                "segment_id": "seg-0002", "raw_text": "乙", "working_text": "乙",
                "start_ms": 3000, "end_ms": 6000, "_index": 1,
            },
        }

    def _write_baseline(self, directory: Path) -> None:
        """Seed the immutable source evidence required by revision validation."""
        segments = [
            {
                "segment_id": "seg-0001", "raw_text": "甲", "text": "甲",
                "start_ms": 0, "end_ms": 3000,
            },
            {
                "segment_id": "seg-0002", "raw_text": "乙", "text": "乙",
                "start_ms": 3000, "end_ms": 6000,
            },
        ]
        (directory / "subtitles.json").write_text(
            json.dumps({"source": "chirp", "segments": segments}, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_same_candidate_reused_lineage_is_rejected(self) -> None:
        proposal = ai_review.CandidateProposal(
            change_type="cross_segment_reflow",
            source_segment_ids=["seg-0001", "seg-0002"],
            before=[
                {"segment_id": "seg-0001", "text": "甲"},
                {"segment_id": "seg-0002", "text": "乙"},
            ],
            after=[
                {"source_segment_ids": ["seg-0001"], "text": "甲"},
                {"source_segment_ids": ["seg-0001", "seg-0002"], "text": "乙"},
            ],
            reason="regression for reused lineage", confidence=0.9,
            risk="medium", high_review_required=True,
        )
        with self.assertRaises(HTTPException) as caught:
            ai_review._validate_candidate(proposal, self._index())
        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("不可出現在多個", caught.exception.detail)

    def test_final_cue_overlap_is_rejected(self) -> None:
        cues = [
            {"text": "甲", "source_segment_ids": ["seg-0001"], "start_ms": 0, "end_ms": 3000},
            {"text": "乙", "source_segment_ids": ["seg-0002"], "start_ms": 2500, "end_ms": 6000},
        ]
        with self.assertRaises(HTTPException) as caught:
            ai_review._validate_final_cues(cues)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("時間重疊", caught.exception.detail)

    def test_partial_edit_of_active_multi_source_cue_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self._write_baseline(directory)
            state = {
                "revision": 1, "active_revision": 1, "candidates": [],
                "revisions": [{
                    "revision": 1,
                    "cues": [{
                        "cue_id": "cue-0001", "text": "甲乙",
                        "source_segment_ids": ["seg-0001", "seg-0002"],
                        "start_ms": 0, "end_ms": 6000,
                    }],
                }],
            }
            accepted = [{
                "change_id": "cand-partial", "source_segment_ids": ["seg-0001"],
                "after": [{"source_segment_ids": ["seg-0001"], "text": "甲改"}],
            }]
            with self.assertRaises(HTTPException) as caught:
                ai_review._resolve_cues(directory, state, accepted)
            self.assertEqual(caught.exception.status_code, 409)
            self.assertIn("部分 lineage", caught.exception.detail)

    def test_full_lineage_edit_of_active_multi_source_cue_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self._write_baseline(directory)
            state = {
                "revision": 1, "active_revision": 1, "candidates": [],
                "revisions": [{
                    "revision": 1,
                    "cues": [{
                        "cue_id": "cue-0001", "text": "甲乙",
                        "source_segment_ids": ["seg-0001", "seg-0002"],
                        "start_ms": 0, "end_ms": 6000,
                    }],
                }],
            }
            accepted = [{
                "change_id": "cand-full",
                "source_segment_ids": ["seg-0001", "seg-0002"],
                "after": [{
                    "source_segment_ids": ["seg-0001", "seg-0002"],
                    "text": "甲乙改",
                }],
            }]
            cues = ai_review._resolve_cues(directory, state, accepted)
            self.assertEqual(len(cues), 1)
            self.assertEqual(cues[0]["text"], "甲乙改")
            self.assertEqual(cues[0]["source_segment_ids"], ["seg-0001", "seg-0002"])


if __name__ == "__main__":
    unittest.main()
