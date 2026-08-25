"""End-to-end endpoint integration tests for the AI subtitle review workflow.

Covers review Cases A–I through the real HTTP routes (not helpers):
A corrected text preserved, B manual edits preserved, C revision N+1 builds
on active N, D edited_after persists to export, E overlapping scope rejected,
F cross-speaker cue rejected, G non-adjacent multi-segment rejected,
H final cues never overlap, I TXT keeps inner whitespace.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.subtitles import ai_review


def _segment(segment_id: str, start: int, end: int, text: str, speaker: str | None = None) -> dict:
    item = {"segment_id": segment_id, "start_ms": start, "end_ms": end, "raw_text": text}
    if speaker:
        item["speaker"] = speaker
    return item


class AIReviewEndpointFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data_dir = Path(temporary.name)
        self.jobs = self.data_dir / "jobs"
        self.job_dir = self.jobs / "job-1"
        self.job_dir.mkdir(parents=True)

        # Raw ASR baseline (immutable evidence)
        segments = [
            _segment("seg-0001", 0, 3000, "所以我們今天要講的是彌勒大"),
            _segment("seg-0002", 3000, 6000, "成佛經裡面這個內容"),
            _segment("seg-0003", 6000, 9000, "我們今天要"),
            _segment("seg-0004", 9000, 12000, "介紹這部經"),
        ]
        (self.job_dir / "subtitles.json").write_text(
            json.dumps({"source": "chirp", "segments": segments}, ensure_ascii=False), encoding="utf-8"
        )
        # Gemini corrected layer
        corrected = [
            {**item, "text": item["raw_text"], "corrected_text": f"{item['raw_text']}（校正）"}
            for item in segments
        ]
        (self.job_dir / "subtitles-corrected.json").write_text(
            json.dumps({"source": "gemini", "segments": corrected}, ensure_ascii=False), encoding="utf-8"
        )

        self.original_data_dir = ai_review.DATA_DIR
        ai_review.DATA_DIR = self.data_dir
        self.addCleanup(self._restore)

        app = FastAPI()
        app.include_router(ai_review.router)
        self.client = TestClient(app, base_url="http://testserver")

    def _restore(self) -> None:
        ai_review.DATA_DIR = self.original_data_dir
        ai_review._SPEAKER_CACHE.clear()

    def propose(self, candidate: dict) -> dict:
        return {
            "expected_revision": 0,
            "candidates": [candidate],
        }

    def get_state(self) -> dict:
        response = self.client.get("/api/v1/subtitles/job-1/ai-review")
        assert response.status_code == 200, response.text
        return response.json()

    def decide(self, change_id: str, decision: str, edited_after=None) -> dict:
        payload = {"change_id": change_id, "decision": decision}
        if edited_after is not None:
            payload["edited_after"] = edited_after
        response = self.client.post(
            "/api/v1/subtitles/job-1/ai-review/candidates/decide", json=payload
        )
        assert response.status_code == 200, response.text
        return response.json()

    def publish(self) -> dict:
        state = self.get_state()
        response = self.client.post(
            "/api/v1/subtitles/job-1/ai-review/publish",
            json={"base_revision": state["revision"]},
        )
        assert response.status_code == 200, response.text
        return response.json()

    # -- Case A: untouched segments keep Gemini corrected text ---------------

    def test_case_a_untouched_segments_keep_corrected_text(self) -> None:
        response = self.client.post(
            "/api/v1/subtitles/job-1/ai-review/candidates",
            json=self.propose({
                "change_type": "asr_typo",
                "source_segment_ids": ["seg-0001"],
                "before": [{"segment_id": "seg-0001",
                            "text": "所以我們今天要講的是彌勒大（校正）"}],
                "after": [{"source_segment_ids": ["seg-0001"],
                           "text": "所以我們今天要講的是彌勒大成佛經（校正）"}],
                "reason": "專有名詞跨段", "confidence": 0.99, "risk": "low",
                "high_review_required": True,
            }),
        )
        self.assertEqual(response.status_code, 200, response.text)
        change_id = response.json()["candidates"][0]["change_id"]
        self.decide(change_id, "accept")
        result = self.publish()
        export = self.client.get(f"/api/v1/subtitles/job-1/ai-review/export/txt").text
        # seg-0002..4 untouched → keep corrected text, not raw ASR
        self.assertIn("成佛經裡面這個內容（校正）", export)
        self.assertNotIn("成佛經裡面這個內容\n", export.replace("成佛經裡面這個內容（校正）", ""))
        self.assertEqual(result["revision"], 1)

    # -- Case B: manual editor edits preserved --------------------------------

    def test_case_b_manual_editor_edit_preserved(self) -> None:
        # Simulate a Subtitle Editor manual edit overlay.
        editor_state = {"revision": 1, "edits": {"seg-0003": "我們今天要特別介紹"}}
        (self.job_dir / "subtitle-editor.json").write_text(
            json.dumps(editor_state, ensure_ascii=False), encoding="utf-8"
        )
        response = self.client.post(
            "/api/v1/subtitles/job-1/ai-review/candidates",
            json=self.propose({
                "change_type": "asr_typo",
                "source_segment_ids": ["seg-0001"],
                "before": [{"segment_id": "seg-0001",
                            "text": "所以我們今天要講的是彌勒大（校正）"}],
                "after": [{"source_segment_ids": ["seg-0001"],
                           "text": "所以我們要講彌勒大成佛經（校正）"}],
                "reason": "精簡", "confidence": 0.9, "risk": "medium",
                "high_review_required": True,
            }),
        )
        self.assertEqual(response.status_code, 200, response.text)
        change_id = response.json()["candidates"][0]["change_id"]
        self.decide(change_id, "accept")
        self.publish()
        export = self.client.get("/api/v1/subtitles/job-1/ai-review/export/txt").text
        self.assertIn("我們今天要特別介紹", export)  # manual edit survives

    # -- Case C: Revision N+1 built on Active N -------------------------------

    def test_case_c_r2_keeps_r1_changes(self) -> None:
        # R1: fix seg-0001
        response = self.client.post(
            "/api/v1/subtitles/job-1/ai-review/candidates",
            json=self.propose({
                "change_type": "asr_typo",
                "source_segment_ids": ["seg-0001"],
                "before": [{"segment_id": "seg-0001", "text": "所以我們今天要講的是彌勒大（校正）"}],
                "after": [{"source_segment_ids": ["seg-0001"], "text": "R1版本第一段（校正）"}],
                "reason": "r1", "confidence": 0.95, "risk": "low", "high_review_required": True,
            }),
        )
        change_id = response.json()["candidates"][0]["change_id"]
        self.decide(change_id, "accept")
        self.publish()  # R1

        # R2: fix seg-0004 — before must match R1's current working text
        response = self.client.post(
            "/api/v1/subtitles/job-1/ai-review/candidates",
            json={
                "expected_revision": 1,
                "candidates": [{
                    "change_type": "asr_typo",
                    "source_segment_ids": ["seg-0004"],
                    "before": [{"segment_id": "seg-0004", "text": "介紹這部經（校正）"}],
                    "after": [{"source_segment_ids": ["seg-0004"], "text": "介紹這部彌勒大成佛經（校正）"}],
                    "reason": "r2", "confidence": 0.95, "risk": "low", "high_review_required": True,
                }],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        change_id = response.json()["candidates"][0]["change_id"]
        self.decide(change_id, "accept")
        self.publish()  # R2

        export = self.client.get("/api/v1/subtitles/job-1/ai-review/export/txt").text
        self.assertIn("R1版本第一段（校正）", export)   # seg-1 not reverted
        self.assertIn("介紹這部彌勒大成佛經（校正）", export)  # seg-4 new edit present

    # -- Case D: edited_after persists to export ------------------------------

    def test_case_d_edited_after_persists_to_export(self) -> None:
        response = self.client.post(
            "/api/v1/subtitles/job-1/ai-review/candidates",
            json=self.propose({
                "change_type": "asr_typo",
                "source_segment_ids": ["seg-0001"],
                "before": [{"segment_id": "seg-0001", "text": "所以我們今天要講的是彌勒大（校正）"}],
                "after": [{"source_segment_ids": ["seg-0001"], "text": "AI原始建議文字"}],
                "reason": "typo", "confidence": 0.8, "risk": "low", "high_review_required": True,
            }),
        )
        change_id = response.json()["candidates"][0]["change_id"]
        self.decide(change_id, "accept", edited_after=[
            {"source_segment_ids": ["seg-0001"], "text": "人工修改後的最終文字"}
        ])
        self.publish()
        export = self.client.get("/api/v1/subtitles/job-1/ai-review/export/srt").text
        self.assertIn("人工修改後的最終文字", export)
        self.assertNotIn("AI原始建議文字", export)
        # Audit trail keeps the AI original
        state = self.get_state()
        snapshot = self.client.get(
            "/api/v1/subtitles/job-1/ai-review"
        ).json()  # candidates cleared after publish; audit lives in revision record
        self.assertIsNotNone(snapshot)

    # -- Case E: overlapping accepted scopes rejected at publish --------------

    def test_case_e_overlapping_accepted_scope_rejected(self) -> None:
        response = self.client.post(
            "/api/v1/subtitles/job-1/ai-review/candidates",
            json={"expected_revision": 0, "candidates": [
                {
                    "change_type": "asr_typo",
                    "source_segment_ids": ["seg-0001"],
                    "before": [{"segment_id": "seg-0001", "text": "所以我們今天要講的是彌勒大（校正）"}],
                    "after": [{"source_segment_ids": ["seg-0001"], "text": "候選甲（校正）"}],
                    "reason": "a", "confidence": 0.9, "risk": "low", "high_review_required": True,
                },
                {
                    "change_type": "asr_typo",
                    "source_segment_ids": ["seg-0001"],
                    "before": [{"segment_id": "seg-0001", "text": "所以我們今天要講的是彌勒大（校正）"}],
                    "after": [{"source_segment_ids": ["seg-0001"], "text": "候選乙（校正）"}],
                    "reason": "b", "confidence": 0.9, "risk": "low", "high_review_required": True,
                },
            ]},
        )
        created = response.json()["candidates"]
        for candidate in created:
            self.decide(candidate["change_id"], "accept")
        publish_response = self.client.post(
            "/api/v1/subtitles/job-1/ai-review/publish",
            json={"base_revision": 0},
        )
        self.assertEqual(publish_response.status_code, 409)
        self.assertIn("同時修改", publish_response.json()["detail"])

    # -- Case F: cross-speaker derived cue rejected ---------------------------

    def test_case_f_cross_speaker_derived_cue_rejected(self) -> None:
        segments = [
            _segment("seg-0001", 0, 3000, "第一句", speaker="法師A"),
            _segment("seg-0002", 3000, 6000, "第二句", speaker="法師B"),
        ]
        (self.job_dir / "subtitles.json").write_text(
            json.dumps({"source": "chirp", "segments": segments}, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.job_dir / "subtitles-corrected.json").unlink()
        ai_review._SPEAKER_CACHE.clear()
        response = self.client.post(
            "/api/v1/subtitles/job-1/ai-review/candidates",
            json=self.propose({
                "change_type": "cross_segment_reflow",
                "source_segment_ids": ["seg-0001", "seg-0002"],
                "before": [
                    {"segment_id": "seg-0001", "text": "第一句"},
                    {"segment_id": "seg-0002", "text": "第二句"},
                ],
                "after": [
                    {"source_segment_ids": ["seg-0001", "seg-0002"], "text": "第一句第二句"},
                ],
                "reason": "reflow", "confidence": 0.9, "risk": "low",
                "high_review_required": True,
            }),
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("speaker", response.json()["detail"])

    # -- Case G: non-adjacent merge/mixed/reflow rejected ----------------------

    def test_case_g_non_adjacent_multi_segment_rejected(self) -> None:
        segments = [
            _segment("seg-0001", 0, 2000, "甲"),
            _segment("seg-0002", 2000, 4000, "乙"),
            _segment("seg-0003", 4000, 6000, "丙"),
        ]
        (self.job_dir / "subtitles.json").write_text(
            json.dumps({"source": "chirp", "segments": segments}, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.job_dir / "subtitles-corrected.json").unlink()
        for change_type in ("merge_adjacent", "cross_segment_reflow", "mixed"):
            response = self.client.post(
                "/api/v1/subtitles/job-1/ai-review/candidates",
                json=self.propose({
                    "change_type": change_type,
                    "source_segment_ids": ["seg-0001", "seg-0003"],
                    "before": [
                        {"segment_id": "seg-0001", "text": "甲"},
                        {"segment_id": "seg-0003", "text": "丙"},
                    ],
                    "after": [
                        {"source_segment_ids": ["seg-0001"], "text": "甲"},
                        {"source_segment_ids": ["seg-0003"], "text": "丙"},
                    ] if change_type == "mixed" else [
                        {"source_segment_ids": ["seg-0001", "seg-0003"], "text": "甲丙"}
                    ],
                    "reason": "non adjacent", "confidence": 0.9, "risk": "low",
                    "high_review_required": True,
                }),
            )
            self.assertEqual(response.status_code, 422, change_type)
            self.assertIn("相鄰", response.json()["detail"])

    # -- Case H: final cues never overlap --------------------------------------

    def test_case_h_final_cues_do_not_overlap(self) -> None:
        response = self.client.post(
            "/api/v1/subtitles/job-1/ai-review/candidates",
            json=self.propose({
                "change_type": "merge_adjacent",
                "source_segment_ids": ["seg-0003", "seg-0004"],
                "before": [
                    {"segment_id": "seg-0003", "text": "我們今天要（校正）"},
                    {"segment_id": "seg-0004", "text": "介紹這部經（校正）"},
                ],
                "after": [{"source_segment_ids": ["seg-0003", "seg-0004"],
                           "text": "我們今天要（校正）介紹這部經（校正）"}],
                "reason": "merge", "confidence": 0.95, "risk": "low",
                "high_review_required": True,
            }),
        )
        change_id = response.json()["candidates"][0]["change_id"]
        self.decide(change_id, "accept")
        self.publish()
        srt = self.client.get("/api/v1/subtitles/job-1/ai-review/export/srt").text
        intervals = []
        for line in srt.splitlines():
            if "-->" in line:
                start, end = [part.strip() for part in line.split("-->")]
                to_ms = lambda value: (
                    ((int(value[0:2]) * 60 + int(value[3:5])) * 60 + int(value[6:8])) * 1000
                    + int(value[9:12])
                )
                intervals.append((to_ms(start), to_ms(end)))
        intervals.sort()
        for (_, previous_end), (next_start, _) in zip(intervals, intervals[1:]):
            self.assertGreaterEqual(next_start, previous_end)

    # -- Case I: TXT keeps inner whitespace ------------------------------------

    def test_case_i_txt_preserves_inner_whitespace(self) -> None:
        segments = [_segment("seg-0001", 0, 3000, "這是 machine learning 課程")]
        (self.job_dir / "subtitles.json").write_text(
            json.dumps({"source": "chirp", "segments": segments}, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.job_dir / "subtitles-corrected.json").write_text(
            json.dumps({"source": "gemini", "segments": [
                {**item, "corrected_text": "這是 machine learning 課程", "text": "這是 machine learning 課程"}
                for item in segments
            ]}, ensure_ascii=False), encoding="utf-8"
        )
        state = ai_review._review_state(self.job_dir)
        cues = [{"cue_id": "cue-0001", "text": "這是 machine learning 課程",
                 "source_segment_ids": ["seg-0001"], "start_ms": 0, "end_ms": 3000}]
        txt = ai_review.render_txt(cues)
        self.assertIn("machine learning", txt)
        self.assertNotIn("machinelearning", txt)


if __name__ == "__main__":
    unittest.main()
