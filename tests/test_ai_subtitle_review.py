"""AI subtitle organization review workflow tests."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app.subtitles import ai_review


def _segment(segment_id: str, start: int, end: int, text: str, speaker: str | None = None) -> dict:
    item = {
        "segment_id": segment_id,
        "start_ms": start,
        "end_ms": end,
        "raw_text": text,
    }
    if speaker:
        item["speaker"] = speaker
    return item


def _proposal(**overrides) -> ai_review.CandidateProposal:
    base = dict(
        change_type="asr_typo",
        source_segment_ids=["seg-0001"],
        before=[{"segment_id": "seg-0001", "text": "所以我們今天要講的是彌勒大"}],
        after=[{"source_segment_ids": ["seg-0001"], "text": "所以我們今天要講的是彌勒大。"}],
        reason="標點",
        confidence=0.99,
        risk="low",
        high_review_required=True,
    )
    base.update(overrides)
    return ai_review.CandidateProposal(**base)


class AIReviewWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.jobs = Path(temporary.name) / "jobs"
        self.job_dir = self.jobs / "job-1"
        (self.job_dir / "glossary").mkdir(parents=True)
        segments = [
            _segment("seg-0001", 0, 3000, "所以我們今天要講的是彌勒大"),
            _segment("seg-0002", 3000, 6000, "成佛經裡面這個內容"),
            _segment("seg-0003", 6000, 9000, "我們今天要"),
            _segment("seg-0004", 9000, 12000, "介紹這部經"),
            _segment("seg-0005", 12000, 15000, "阿彌彌彌陀佛"),
        ]
        payload = {"source": "chirp", "segments": [
            {**item, "text": item["raw_text"]} for item in segments
        ]}
        (self.job_dir / "subtitles.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        self.subtitle_id = "job-1"
        self.state_path = self.job_dir / "ai-subtitle-review.json"

    # -- helpers ------------------------------------------------------------

    def baseline(self) -> list[dict]:
        return ai_review.baseline_segments(self.job_dir)

    def index(self) -> dict[str, dict]:
        return {
            item["segment_id"]: {**item, "_index": position}
            for position, item in enumerate(self.baseline())
        }

    def propose(self, proposal) -> dict:
        return ai_review._validate_candidate(proposal, self.index())

    def load_state(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    # -- tests ----------------------------------------------------------------

    def test_single_segment_typo_candidate_is_validated(self) -> None:
        record = self.propose(_proposal())
        self.assertEqual(record["change_type"], "asr_typo")
        self.assertEqual(record["status"], "pending")
        self.assertTrue(record["high_review_required"])

    def test_cross_segment_reflow_adjacent_word_split(self) -> None:
        record = self.propose(
            _proposal(
                change_type="cross_segment_reflow",
                source_segment_ids=["seg-0001", "seg-0002"],
                before=[
                    {"segment_id": "seg-0001", "text": "所以我們今天要講的是彌勒大"},
                    {"segment_id": "seg-0002", "text": "成佛經裡面這個內容"},
                ],
                after=[
                    {"source_segment_ids": ["seg-0001"], "text": "所以我們今天要講的是"},
                    {"source_segment_ids": ["seg-0001", "seg-0002"], "text": "彌勒大成佛經裡面這個內容"},
                ],
                reason="完整專有名詞被 ASR boundary 切開",
            )
        )
        self.assertEqual(len(record["after"]), 2)
        self.assertEqual(record["after"][1]["source_segment_ids"], ["seg-0001", "seg-0002"])

    def test_proper_noun_not_split_across_cues(self) -> None:
        """The canonical term 彌勒大成佛經 must stay within a single cue."""
        record = self.propose(
            _proposal(
                change_type="cross_segment_reflow",
                source_segment_ids=["seg-0001", "seg-0002"],
                before=[
                    {"segment_id": "seg-0001", "text": "所以我們今天要講的是彌勒大"},
                    {"segment_id": "seg-0002", "text": "成佛經裡面這個內容"},
                ],
                after=[
                    {"source_segment_ids": ["seg-0001"], "text": "所以我們今天要講的是彌勒大成佛經"},
                    {"source_segment_ids": ["seg-0002"], "text": "裡面這個內容"},
                ],
                reason="term kept whole",
            )
        )
        for cue in record["after"]:
            if "彌勒大成" in cue["text"]:
                self.assertIn("彌勒大成佛經", cue["text"])

    def test_merge_adjacent_creates_single_spanning_cue(self) -> None:
        record = self.propose(
            _proposal(
                change_type="merge_adjacent",
                source_segment_ids=["seg-0003", "seg-0004"],
                before=[
                    {"segment_id": "seg-0003", "text": "我們今天要"},
                    {"segment_id": "seg-0004", "text": "介紹這部經"},
                ],
                after=[
                    {"source_segment_ids": ["seg-0003", "seg-0004"], "text": "我們今天要介紹這部經"}
                ],
                reason="短句合併",
            )
        )
        self.assertEqual(len(record["after"]), 1)

    def test_merge_different_speakers_rejected(self) -> None:
        directory = self.job_dir
        payload = {"source": "chirp", "segments": [
            _segment("seg-0001", 0, 3000, "第一句", speaker="法師A"),
            _segment("seg-0002", 3000, 6000, "第二句", speaker="法師B"),
        ]}
        (directory / "subtitles.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(HTTPException) as caught:
            self.propose(
                _proposal(
                    change_type="merge_adjacent",
                    source_segment_ids=["seg-0001", "seg-0002"],
                    before=[
                        {"segment_id": "seg-0001", "text": "第一句"},
                        {"segment_id": "seg-0002", "text": "第二句"},
                    ],
                    after=[{"source_segment_ids": ["seg-0001", "seg-0002"], "text": "第一句第二句"}],
                    reason="x",
                )
            )
        self.assertIn("speaker", caught.exception.detail)

    def test_split_does_not_invent_timestamps(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            self.propose(
                _proposal(
                    change_type="split_for_readability",
                    source_segment_ids=["seg-0005"],
                    before=[{"segment_id": "seg-0005", "text": "阿彌彌彌陀佛"}],
                    after=[
                        {"source_segment_ids": ["seg-0005"], "text": "阿彌", "start_ms": 1000},
                        {"source_segment_ids": ["seg-0005"], "text": "彌彌陀佛", "start_ms": 2500},
                    ],
                    reason="readability",
                )
            )
        self.assertIn("timestamp", caught.exception.detail)

    def test_source_baseline_immutable_after_all_operations(self) -> None:
        original = (self.job_dir / "subtitles.json").read_bytes()
        state = ai_review._review_state(self.job_dir)
        cues = ai_review._resolve_cues(
            self.job_dir,
            state,
            [self.propose(_proposal(change_type="asr_typo"))],
        )
        self.assertTrue(cues)
        self.assertEqual((self.job_dir / "subtitles.json").read_bytes(), original)
        baseline_now = ai_review.baseline_segments(self.job_dir)
        self.assertEqual(baseline_now[0]["raw_text"], "所以我們今天要講的是彌勒大")

    def test_invented_segment_id_rejected(self) -> None:
        with self.assertRaises(HTTPException):
            self.propose(
                _proposal(source_segment_ids=["seg-9999"],
                          before=[{"segment_id": "seg-9999", "text": "??"}])
            )

    def test_missing_source_mapping_rejected(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            self.propose(
                _proposal(
                    after=[{"source_segment_ids": [], "text": "佛告阿難。"}],
                )
            )
        self.assertIn("source_segment_ids", caught.exception.detail)

    def test_reflow_must_be_adjacent(self) -> None:
        directory = self.job_dir
        payload = {"source": "chirp", "segments": [
            _segment("seg-0001", 0, 2000, "甲"),
            _segment("seg-0002", 2000, 4000, "乙"),
            _segment("seg-0003", 4000, 6000, "丙"),
        ]}
        (directory / "subtitles.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(HTTPException) as caught:
            self.propose(
                _proposal(
                    change_type="cross_segment_reflow",
                    source_segment_ids=["seg-0001", "seg-0003"],
                    before=[
                        {"segment_id": "seg-0001", "text": "甲"},
                        {"segment_id": "seg-0003", "text": "丙"},
                    ],
                    after=[
                        {"source_segment_ids": ["seg-0001"], "text": "甲"},
                        {"source_segment_ids": ["seg-0003"], "text": "丙"},
                    ],
                    reason="non adjacent",
                )
            )
        self.assertIn("相鄰", caught.exception.detail)

    def test_reflow_preserves_total_content(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            self.propose(
                _proposal(
                    change_type="cross_segment_reflow",
                    source_segment_ids=["seg-0001", "seg-0002"],
                    before=[
                        {"segment_id": "seg-0001", "text": "所以我們今天要講的是彌勒大"},
                        {"segment_id": "seg-0002", "text": "成佛經裡面這個內容"},
                    ],
                    after=[{"source_segment_ids": ["seg-0001", "seg-0002"], "text": "完全不同的內容"}],
                    reason="content changed without correction candidate",
                )
            )
        self.assertIn("總字詞", caught.exception.detail)

    def test_accept_then_publish_creates_revision_without_touching_active_beforehand(self) -> None:
        record = self.propose(_proposal())
        state = ai_review._review_state(self.job_dir)
        state["candidates"] = [dict(record)]
        record["status"] = "accepted"
        ai_review._save_state(self.job_dir, state)
        saved = self.load_state()
        self.assertIsNone(saved.get("active_revision"))
        cues = ai_review._resolve_cues(self.job_dir, saved, [record])
        self.assertTrue(all("start_ms" in cue for cue in cues))
        self.assertTrue(all(cue["source_segment_ids"] for cue in cues))

    def test_reject_leaves_active_revision_untouched(self) -> None:
        state = ai_review._review_state(self.job_dir)
        candidate = self.propose(_proposal())
        candidate["status"] = "rejected"
        state["candidates"] = [candidate]
        ai_review._save_state(self.job_dir, state)
        self.assertIsNone(self.load_state().get("active_revision"))
        self.assertEqual(ai_review.baseline_segments(self.job_dir)[0]["raw_text"], "所以我們今天要講的是彌勒大")

    def test_publish_and_rollback_keep_full_history(self) -> None:
        candidate = self.propose(_proposal())
        candidate["status"] = "accepted"
        state = ai_review._review_state(self.job_dir)
        state["candidates"] = [candidate]
        cues = ai_review._resolve_cues(self.job_dir, state, [candidate])
        digest = "a" * 64
        state["revisions"].append({
            "revision": 1, "created_at": ai_review._iso(), "created_by": "t",
            "source": "ai_subtitle_review_publish", "accepted_change_ids": [candidate["change_id"]],
            "rejected_change_ids": [], "cues": cues, "content_sha256": digest,
        })
        state["active_revision"] = 1
        state["revision"] = 1
        ai_review._save_state(self.job_dir, state)

        target = ai_review._revision_by_number(state, 1)
        rollback_record = {
            "revision": 2, "rolled_back_from": 1,
            "cues": [dict(cue) for cue in target["cues"]],
            "content_sha256": target["content_sha256"],
        }
        state["revisions"].append(rollback_record)
        state["active_revision"] = 2
        state["revision"] = 2
        ai_review._save_state(self.job_dir, state)

        loaded = self.load_state()
        self.assertEqual(loaded["active_revision"], 2)
        self.assertEqual(len(loaded["revisions"]), 2)
        self.assertEqual(loaded["revisions"][0]["cues"], loaded["revisions"][1]["cues"])

    def test_exports_render_from_active_revision(self) -> None:
        cues = [
            {"cue_id": "cue-0001", "text": "佛告阿難。", "source_segment_ids": ["seg-0001"],
             "start_ms": 0, "end_ms": 3000},
        ]
        srt = ai_review.render_srt(cues)
        vtt = ai_review.render_vtt(cues)
        txt = ai_review.render_txt(cues)
        self.assertIn("00:00:00,000 --> 00:00:03,000", srt)
        self.assertTrue(vtt.startswith("WEBVTT"))
        self.assertIn("00:00:00.000 --> 00:00:03.000", vtt)
        self.assertEqual(txt.strip(), "佛告阿難。")
        docx_bytes = ai_review.render_docx_bytes(cues)
        self.assertTrue(docx_bytes.startswith(b"PK"))

    def test_lineage_preserved_in_resolved_cues(self) -> None:
        reflow = self.propose(
            _proposal(
                change_type="cross_segment_reflow",
                source_segment_ids=["seg-0001", "seg-0002"],
                before=[
                    {"segment_id": "seg-0001", "text": "所以我們今天要講的是彌勒大"},
                    {"segment_id": "seg-0002", "text": "成佛經裡面這個內容"},
                ],
                after=[
                    {"source_segment_ids": ["seg-0001"], "text": "所以我們今天要講的是"},
                    {"source_segment_ids": ["seg-0001", "seg-0002"], "text": "彌勒大成佛經裡面這個內容"},
                ],
                reason="reflow",
            )
        )
        reflow["status"] = "accepted"
        state = ai_review._review_state(self.job_dir)
        cues = ai_review._resolve_cues(self.job_dir, state, [reflow])
        lineage = {tuple(cue["source_segment_ids"]) for cue in cues}
        self.assertIn(("seg-0001",), lineage)
        self.assertIn(("seg-0001", "seg-0002"), lineage)
        merged_cue = next(cue for cue in cues if len(cue["source_segment_ids"]) == 2)
        self.assertEqual(merged_cue["start_ms"], 3000 - 3000)
        self.assertEqual(merged_cue["end_ms"], 6000)


if __name__ == "__main__":
    unittest.main()
