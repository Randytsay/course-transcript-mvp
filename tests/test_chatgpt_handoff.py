from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.jobs.costs import CostConfig, estimate_job_cost
from app.jobs.workflow_mode import (
    CHATGPT_HANDOFF,
    CHIRP_ONLY,
    FULL_AUTO,
    normalize_workflow_mode,
    uses_server_llm,
)
from app.providers.chatgpt_handoff import (
    import_corrected_segments,
    import_corrected_srt,
    write_bundle,
)
from app.providers.chirp_completeness_gate import evaluate as evaluate_completeness


def _raw_segments() -> list[dict[str, object]]:
    return [
        {
            "segment_id": "seg-0001",
            "start_ms": 0,
            "end_ms": 1500,
            "raw_text": "這是原始第一句",
            "text": "這是原始第一句",
        },
        {
            "segment_id": "seg-0002",
            "start_ms": 1700,
            "end_ms": 3200,
            "raw_text": "這是原始第二句",
            "text": "這是原始第二句",
        },
    ]


def _write_job(job_dir: Path) -> None:
    job_dir.mkdir(parents=True)
    segments = _raw_segments()
    (job_dir / "subtitles.json").write_text(
        json.dumps({"source": "chirp_3_merged", "segments": segments}, ensure_ascii=False),
        encoding="utf-8",
    )
    (job_dir / "subtitles.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,500\n這是原始第一句\n\n"
        "2\n00:00:01,700 --> 00:00:03,200\n這是原始第二句\n",
        encoding="utf-8",
    )
    (job_dir / "merged-words.json").write_text(
        json.dumps({"words": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    (job_dir / "chunk-plan.json").write_text(
        json.dumps({"duration_seconds": 3.2, "chunks": []}), encoding="utf-8"
    )
    (job_dir / "chirp-completeness.json").write_text(
        json.dumps({"status": "PASS", "handoff_allowed": True}), encoding="utf-8"
    )


def test_workflow_mode_contract() -> None:
    assert normalize_workflow_mode(None) == FULL_AUTO
    assert normalize_workflow_mode("chatgpt_handoff") == CHATGPT_HANDOFF
    assert normalize_workflow_mode(CHIRP_ONLY) == CHIRP_ONLY
    assert uses_server_llm(FULL_AUTO) is True
    assert uses_server_llm(CHATGPT_HANDOFF) is False
    assert uses_server_llm(CHIRP_ONLY) is False
    with pytest.raises(ValueError):
        normalize_workflow_mode("unknown")


def test_chirp_only_estimate_has_zero_gemini_tokens_and_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COURSE_TRANSCRIPT_CHIRP_USD_PER_MINUTE", "0.003")
    config = CostConfig.from_env()
    full = estimate_job_cost(7200, config, include_gemini=True)
    chirp_only = estimate_job_cost(7200, config, include_gemini=False)

    assert chirp_only.estimated_gemini_input_tokens == 0
    assert chirp_only.estimated_gemini_output_tokens == 0
    assert chirp_only.gemini_input_usd == 0
    assert chirp_only.gemini_output_usd == 0
    assert chirp_only.chirp_usd == full.chirp_usd
    assert chirp_only.estimated_total_usd < full.estimated_total_usd


def test_handoff_bundle_and_text_only_import(tmp_path: Path) -> None:
    job_dir = tmp_path / "job-1"
    _write_job(job_dir)

    manifest = write_bundle(
        job_dir,
        content_mode="general",
        document_context="reference context",
    )
    bundle = job_dir / "chatgpt-handoff"
    assert manifest["workflow_mode"] == CHATGPT_HANDOFF
    assert manifest["cue_count"] == 2
    assert manifest["timestamps_immutable"] is True
    assert (bundle / "chirp-raw.srt").is_file()
    assert (bundle / "segments.json").is_file()
    assert (bundle / "merged-words.json").is_file()
    assert (bundle / "raw-transcript.txt").is_file()
    assert (bundle / "handoff-manifest.json").is_file()

    audit = import_corrected_srt(
        job_dir,
        actor="test",
        srt_text=(
            "1\n00:00:00,000 --> 00:00:01,500\n校正後第一句\n\n"
            "2\n00:00:01,700 --> 00:00:03,200\n校正後第二句\n"
        ),
    )
    corrected = json.loads((job_dir / "subtitles-corrected.json").read_text("utf-8"))
    assert audit["cue_count"] == 2
    assert audit["timestamps_immutable"] is True
    assert corrected["segments"][0]["segment_id"] == "seg-0001"
    assert corrected["segments"][0]["start_ms"] == 0
    assert corrected["segments"][0]["end_ms"] == 1500
    assert corrected["segments"][0]["raw_text"] == "這是原始第一句"
    assert corrected["segments"][0]["corrected_text"] == "校正後第一句"
    assert corrected["segments"][1]["corrected_text"] == "校正後第二句"


def test_handoff_prefers_segment_edits_without_timestamps(tmp_path: Path) -> None:
    job_dir = tmp_path / "job-segment-edits"
    _write_job(job_dir)
    audit = import_corrected_segments(
        job_dir,
        actor="test",
        segment_edits=[
            {"segment_id": "seg-0001", "corrected_text": "第一句校正"},
            {"segment_id": "seg-0002", "corrected_text": "第二句校正"},
        ],
    )
    corrected = json.loads((job_dir / "subtitles-corrected.json").read_text("utf-8"))
    assert audit["schema_version"] == 2
    assert audit["timestamps_immutable"] is True
    assert corrected["segments"][0]["start_ms"] == 0
    assert corrected["segments"][0]["end_ms"] == 1500
    assert corrected["segments"][1]["start_ms"] == 1700
    assert corrected["segments"][1]["end_ms"] == 3200


def test_segment_edits_reject_missing_or_reordered_ids(tmp_path: Path) -> None:
    job_dir = tmp_path / "job-segment-order"
    _write_job(job_dir)
    with pytest.raises(ValueError, match="order/id changed"):
        import_corrected_segments(
            job_dir,
            actor="test",
            segment_edits=[
                {"segment_id": "seg-0002", "corrected_text": "錯序"},
                {"segment_id": "seg-0001", "corrected_text": "錯序"},
            ],
        )


def test_handoff_rejects_timestamp_change(tmp_path: Path) -> None:
    job_dir = tmp_path / "job-2"
    _write_job(job_dir)

    with pytest.raises(ValueError, match="changed timestamp"):
        import_corrected_srt(
            job_dir,
            actor="test",
            srt_text=(
                "1\n00:00:00,000 --> 00:00:01,400\n校正後第一句\n\n"
                "2\n00:00:01,700 --> 00:00:03,200\n校正後第二句\n"
            ),
        )


def test_handoff_rejects_cue_count_change(tmp_path: Path) -> None:
    job_dir = tmp_path / "job-3"
    _write_job(job_dir)

    with pytest.raises(ValueError, match="cue count changed"):
        import_corrected_srt(
            job_dir,
            actor="test",
            srt_text="1\n00:00:00,000 --> 00:00:01,500\n只有一句\n",
        )


def test_chirp_completeness_gate_passes_clean_short_job(tmp_path: Path) -> None:
    job_dir = tmp_path / "gate-pass"
    _write_job(job_dir)
    report = evaluate_completeness(job_dir, audibility_probe=lambda _start, _end: False)
    assert report["status"] == "PASS"
    assert report["handoff_allowed"] is True
    assert report["summary"]["blocker_count"] == 0


def test_chirp_completeness_gate_blocks_audible_mid_gap(tmp_path: Path) -> None:
    job_dir = tmp_path / "gate-block"
    _write_job(job_dir)
    payload = json.loads((job_dir / "subtitles.json").read_text("utf-8"))
    payload["segments"][1]["start_ms"] = 10_000
    payload["segments"][1]["end_ms"] = 12_000
    (job_dir / "subtitles.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (job_dir / "chunk-plan.json").write_text(
        json.dumps({"duration_seconds": 12.0, "chunks": []}), encoding="utf-8"
    )
    report = evaluate_completeness(job_dir, audibility_probe=lambda _start, _end: True)
    assert report["status"] == "BLOCKED"
    assert report["handoff_allowed"] is False
    assert any(item["reason"] == "audible_subtitle_gap" for item in report["blockers"])


def test_verified_nonlexical_targeted_patch_does_not_loop_forever(tmp_path: Path) -> None:
    job_dir = tmp_path / "gate-verified-no-words"
    _write_job(job_dir)
    payload = json.loads((job_dir / "subtitles.json").read_text("utf-8"))
    payload["segments"][1]["start_ms"] = 10_000
    payload["segments"][1]["end_ms"] = 12_000
    (job_dir / "subtitles.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (job_dir / "chunk-plan.json").write_text(
        json.dumps({"duration_seconds": 12.0, "chunks": []}), encoding="utf-8"
    )
    (job_dir / "chirp-targeted-patch-plan.json").write_text(
        json.dumps({
            "items": [{
                "patch_index": 910001,
                "gap_start_ms": 1500,
                "gap_end_ms": 10_000,
                "source_start_ms": 1000,
                "source_end_ms": 10_500,
            }]
        }),
        encoding="utf-8",
    )
    (job_dir / "chirp-targeted-patch-complete.json").write_text(
        json.dumps({
            "verdicts": [{
                "patch_index": 910001,
                "target_gap_word_count": 0,
                "operation_name": "projects/test/locations/test/operations/1",
            }]
        }),
        encoding="utf-8",
    )
    report = evaluate_completeness(job_dir, audibility_probe=lambda _start, _end: True)
    assert report["status"] == "PASS"
    assert any(
        item["reason"] == "audible_gap_verified_nonlexical_by_targeted_patch"
        for item in report["warnings"]
    )
