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
from app.providers.chirp_completeness_gate import (
    evaluate,
    build_auto_repair_patch_plan,
    evaluate as evaluate_completeness,
)
from app.pipeline.dynamic_worker_hardened import (
    _CHATGPT_HANDOFF_PREEXPORT_EVIDENCE,
    _CHIRP_COMPLETENESS_PREEXPORT_EVIDENCE,
    _preexport_artifact_evidence,
)
from app.pipeline.dynamic_worker_hardened import (
    _prepare_chirp_completeness_auto_repair,
    _update_chirp_completeness_auto_repair_marker,
)


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


def test_chirp_completeness_gate_downgrades_robust_vad_nonspeech(tmp_path: Path) -> None:
    job_dir = tmp_path / "gate-vad-nonspeech"
    _write_job(job_dir)
    payload = json.loads((job_dir / "subtitles.json").read_text("utf-8"))
    payload["segments"][1]["start_ms"] = 10_000
    payload["segments"][1]["end_ms"] = 12_000
    (job_dir / "subtitles.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (job_dir / "chunk-plan.json").write_text(
        json.dumps({"duration_seconds": 12.0, "chunks": []}), encoding="utf-8"
    )
    report = evaluate_completeness(
        job_dir,
        audibility_probe=lambda _start, _end: True,
        speech_probe=lambda _start, _end: {
            "engine": "webrtcvad",
            "mode0_speech_ratio": 0.05,
            "mode3_speech_ratio": 0.02,
            "rms_dbfs": -40.0,
            "robust_non_speech": True,
        },
    )
    assert report["status"] == "PASS"
    assert report["handoff_allowed"] is True
    warning = next(
        item for item in report["warnings"]
        if item["reason"] == "audible_gap_vad_verified_non_speech"
    )
    assert warning["recommended_action"] == "no_paid_retry_required"
    assert warning["vad"]["robust_non_speech"] is True


def test_chirp_completeness_gate_keeps_uncertain_vad_gap_blocking(tmp_path: Path) -> None:
    job_dir = tmp_path / "gate-vad-uncertain"
    _write_job(job_dir)
    payload = json.loads((job_dir / "subtitles.json").read_text("utf-8"))
    payload["segments"][1]["start_ms"] = 10_000
    payload["segments"][1]["end_ms"] = 12_000
    (job_dir / "subtitles.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (job_dir / "chunk-plan.json").write_text(
        json.dumps({"duration_seconds": 12.0, "chunks": []}), encoding="utf-8"
    )
    report = evaluate_completeness(
        job_dir,
        audibility_probe=lambda _start, _end: True,
        speech_probe=lambda _start, _end: {
            "engine": "webrtcvad",
            "mode0_speech_ratio": 0.35,
            "mode3_speech_ratio": 0.22,
            "rms_dbfs": -30.0,
            "robust_non_speech": False,
        },
    )
    assert report["status"] == "BLOCKED"
    blocker = next(item for item in report["blockers"] if item["reason"] == "audible_subtitle_gap")
    assert blocker["vad"]["robust_non_speech"] is False


def test_chirp_completeness_gate_vad_failure_remains_fail_closed(tmp_path: Path) -> None:
    job_dir = tmp_path / "gate-vad-unavailable"
    _write_job(job_dir)
    payload = json.loads((job_dir / "subtitles.json").read_text("utf-8"))
    payload["segments"][1]["start_ms"] = 10_000
    payload["segments"][1]["end_ms"] = 12_000
    (job_dir / "subtitles.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (job_dir / "chunk-plan.json").write_text(
        json.dumps({"duration_seconds": 12.0, "chunks": []}), encoding="utf-8"
    )
    report = evaluate_completeness(
        job_dir,
        audibility_probe=lambda _start, _end: True,
        speech_probe=lambda _start, _end: None,
    )
    assert report["status"] == "BLOCKED"
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


def test_completeness_auto_repair_merges_adjacent_blockers(tmp_path: Path) -> None:
    job_dir = tmp_path / "auto-repair"
    job_dir.mkdir()
    (job_dir / "chunk-plan.json").write_text(
        json.dumps(
            {
                "chunks": [
                    {
                        "chunk_index": 0,
                        "source_start_ms": 0,
                        "source_end_ms": 100_000,
                    },
                    {
                        "chunk_index": 1,
                        "source_start_ms": 90_000,
                        "source_end_ms": 200_000,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    report = {
        "handoff_allowed": False,
        "blockers": [
            {
                "reason": "audible_subtitle_gap",
                "gap_start_ms": 10_000,
                "gap_end_ms": 15_000,
            },
            {
                "reason": "audible_subtitle_gap",
                "gap_start_ms": 15_500,
                "gap_end_ms": 18_000,
            },
            {
                "reason": "uncovered_audio_tail",
                "start_ms": 180_000,
                "end_ms": 195_000,
            },
        ],
    }

    plan = build_auto_repair_patch_plan(job_dir, report)

    assert plan["status"] == "planned"
    assert plan["proposed_patch_count"] == 2
    assert len(plan["items"]) == 2
    first = plan["items"][0]
    assert first["parent_chunk_index"] == 0
    assert first["gap_start_ms"] == 10_000
    assert first["gap_end_ms"] == 18_000
    assert first["automatic_paid_retry"] is True


def test_completeness_auto_repair_fails_closed_over_duration_cap(
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "auto-repair-cap"
    job_dir.mkdir()
    (job_dir / "chunk-plan.json").write_text(
        json.dumps(
            {
                "chunks": [
                    {
                        "chunk_index": 0,
                        "source_start_ms": 0,
                        "source_end_ms": 1_000_000,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    report = {
        "handoff_allowed": False,
        "blockers": [
            {
                "reason": "audible_subtitle_gap",
                "gap_start_ms": 10_000,
                "gap_end_ms": 700_000,
            }
        ],
    }

    plan = build_auto_repair_patch_plan(
        job_dir,
        report,
        max_total_ms=600_000,
    )

    assert plan["status"] == "blocked"
    assert plan["items"] == []
    assert plan["auto_submit_blocked_reason"] == "repair_duration_cap_exceeded"


def test_current_residual_gap_uses_archived_patch_word_evidence(
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "gate-archived-patch-evidence"
    _write_job(job_dir)
    payload = json.loads((job_dir / "subtitles.json").read_text("utf-8"))
    payload["segments"][1]["start_ms"] = 10_000
    payload["segments"][1]["end_ms"] = 12_000
    (job_dir / "subtitles.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    (job_dir / "chunk-plan.json").write_text(
        json.dumps({"duration_seconds": 12.0, "chunks": []}),
        encoding="utf-8",
    )
    archive = job_dir / "targeted-patch-archives" / "round-1"
    archive.mkdir(parents=True)
    patch_index = 940001
    (archive / "chirp-targeted-patch-plan.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "patch_index": patch_index,
                        "source_start_ms": 0,
                        "source_end_ms": 12_000,
                        "gap_start_ms": 1_500,
                        "gap_end_ms": 10_000,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (archive / "chirp-targeted-patch-complete.json").write_text(
        json.dumps(
            {
                "verdicts": [
                    {
                        "patch_index": patch_index,
                        "status": "SUCCEEDED",
                        "target_gap_word_count": 7,
                        "operation_name": "projects/test/operations/residual",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    words_dir = job_dir / "chunks" / f"chunk-{patch_index:03d}"
    words_dir.mkdir(parents=True)
    (words_dir / "words.json").write_text(
        json.dumps(
            {
                "words": [
                    {"start_ms": 200, "end_ms": 900, "word": "前"},
                    {"start_ms": 10_000, "end_ms": 10_500, "word": "後"},
                ]
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_completeness(
        job_dir,
        audibility_probe=lambda _start, _end: True,
        speech_probe=lambda _start, _end: {
            "robust_non_speech": False,
            "mode0_speech_ratio": 0.9,
            "mode3_speech_ratio": 0.8,
            "rms_dbfs": -15.0,
        },
    )

    assert report["status"] == "PASS"
    warning = next(
        item
        for item in report["warnings"]
        if item["reason"] == "audible_gap_verified_nonlexical_by_targeted_patch_words"
    )
    assert warning["patch_evidence"]["patch_index"] == patch_index
    assert warning["patch_evidence"]["overlapping_word_count"] == 0


def test_audio_tail_uses_completed_patch_word_evidence(tmp_path: Path) -> None:
    job_dir = tmp_path / "gate-tail-patch-evidence"
    _write_job(job_dir)
    (job_dir / "chunk-plan.json").write_text(
        json.dumps({"duration_seconds": 12.0, "chunks": []}),
        encoding="utf-8",
    )
    patch_index = 940002
    (job_dir / "chirp-targeted-patch-plan.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "patch_index": patch_index,
                        "source_start_ms": 2_000,
                        "source_end_ms": 12_000,
                        "gap_start_ms": 3_200,
                        "gap_end_ms": 12_000,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (job_dir / "chirp-targeted-patch-complete.json").write_text(
        json.dumps(
            {
                "verdicts": [
                    {
                        "patch_index": patch_index,
                        "status": "SUCCEEDED",
                        "target_gap_word_count": 4,
                        "operation_name": "projects/test/operations/tail",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    words_dir = job_dir / "chunks" / f"chunk-{patch_index:03d}"
    words_dir.mkdir(parents=True)
    (words_dir / "words.json").write_text(
        json.dumps({"words": [{"start_ms": 2_200, "end_ms": 3_000, "word": "末"}]}),
        encoding="utf-8",
    )

    report = evaluate_completeness(
        job_dir,
        audibility_probe=lambda _start, _end: True,
    )

    assert report["status"] == "PASS"
    assert any(
        item["reason"] == "audio_tail_verified_nonlexical_by_targeted_patch_words"
        for item in report["warnings"]
    )


def test_completeness_auto_repair_allows_next_round_after_completed(tmp_path: Path) -> None:
    job_dir = tmp_path / "auto-repair-multi-round"
    job_dir.mkdir()
    (job_dir / "chunk-plan.json").write_text(
        json.dumps(
            {
                "chunks": [
                    {
                        "chunk_index": 0,
                        "source_start_ms": 0,
                        "source_end_ms": 60_000,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    report = {
        "handoff_allowed": False,
        "blockers": [
            {
                "reason": "audible_subtitle_gap",
                "gap_start_ms": 10_000,
                "gap_end_ms": 16_000,
            }
        ],
    }

    first = _prepare_chirp_completeness_auto_repair(job_dir, report)
    second = _prepare_chirp_completeness_auto_repair(job_dir, report)

    assert first is not None
    assert first["status"] == "planned"
    assert second is None
    _update_chirp_completeness_auto_repair_marker(
        job_dir,
        status="completed",
        provider_calls_started=True,
    )
    second_round = _prepare_chirp_completeness_auto_repair(job_dir, report)
    assert second_round is not None
    assert second_round["status"] == "planned"
    assert second_round["items"][0]["patch_index"] == 921001
    marker = json.loads(
        (job_dir / "chirp-completeness-auto-repair.json").read_text("utf-8")
    )
    assert marker["status"] == "prepared"
    assert marker["round"] == 2
    assert marker["max_rounds"] == 3
    assert marker["provider_calls_started"] is False


def test_completeness_auto_repair_migrates_legacy_submitted_marker_with_complete_evidence(tmp_path: Path) -> None:
    job_dir = tmp_path / "legacy-marker"
    job_dir.mkdir()
    (job_dir / "chunk-plan.json").write_text(
        json.dumps({"duration_seconds": 80.0, "chunks": [{"chunk_index": 0, "source_start_ms": 0, "source_end_ms": 80_000}]}),
        encoding="utf-8",
    )
    (job_dir / "chirp-completeness-auto-repair.json").write_text(
        json.dumps({"status": "submitted", "patch_count": 1, "provider_calls_started": True}),
        encoding="utf-8",
    )
    (job_dir / "chirp-targeted-patch-complete.json").write_text(
        json.dumps({
            "patch_decisions": [{"chunk_index": 920001, "applied": True}],
            "verdicts": [{"patch_index": 920001, "status": "SUCCEEDED"}],
        }),
        encoding="utf-8",
    )
    report = {"handoff_allowed": False, "blockers": [{"reason": "uncovered_audio_tail", "start_ms": 75_000, "end_ms": 80_000}]}
    plan = _prepare_chirp_completeness_auto_repair(job_dir, report)
    assert plan is not None
    assert plan["items"][0]["patch_index"] == 921001
    marker = json.loads((job_dir / "chirp-completeness-auto-repair.json").read_text("utf-8"))
    assert marker["round"] == 2


def test_completeness_auto_repair_reconciles_current_round_completion_evidence(tmp_path: Path) -> None:
    job_dir = tmp_path / "round-two-complete"
    job_dir.mkdir()
    (job_dir / "chunk-plan.json").write_text(
        json.dumps({"duration_seconds": 90.0, "chunks": [{"chunk_index": 0, "source_start_ms": 0, "source_end_ms": 90_000}]}),
        encoding="utf-8",
    )
    (job_dir / "chirp-completeness-auto-repair.json").write_text(
        json.dumps({"status": "submitted", "round": 2, "max_rounds": 3, "patch_count": 1, "provider_calls_started": True}),
        encoding="utf-8",
    )
    (job_dir / "chirp-targeted-patch-plan.json").write_text(
        json.dumps({"items": [{"patch_index": 921001}]}),
        encoding="utf-8",
    )
    (job_dir / "chirp-targeted-patch-complete.json").write_text(
        json.dumps({
            "patch_decisions": [{"chunk_index": 921001, "applied": True}],
            "verdicts": [{"patch_index": 921001, "status": "SUCCEEDED"}],
        }),
        encoding="utf-8",
    )
    report = {"handoff_allowed": False, "blockers": [{"reason": "short_audible_tail_requires_review", "start_ms": 86_000, "end_ms": 90_000}]}
    plan = _prepare_chirp_completeness_auto_repair(job_dir, report)
    assert plan is not None
    assert plan["items"][0]["patch_index"] == 922001
    marker = json.loads((job_dir / "chirp-completeness-auto-repair.json").read_text("utf-8"))
    assert marker["round"] == 3


def test_completeness_auto_repair_does_not_migrate_legacy_marker_without_complete_evidence(tmp_path: Path) -> None:
    job_dir = tmp_path / "legacy-marker-no-proof"
    job_dir.mkdir()
    (job_dir / "chunk-plan.json").write_text(
        json.dumps({"duration_seconds": 80.0, "chunks": [{"chunk_index": 0, "source_start_ms": 0, "source_end_ms": 80_000}]}),
        encoding="utf-8",
    )
    (job_dir / "chirp-completeness-auto-repair.json").write_text(
        json.dumps({"status": "submitted", "patch_count": 1, "provider_calls_started": True}),
        encoding="utf-8",
    )
    report = {"handoff_allowed": False, "blockers": [{"reason": "uncovered_audio_tail", "start_ms": 75_000, "end_ms": 80_000}]}
    assert _prepare_chirp_completeness_auto_repair(job_dir, report) is None


def test_short_audible_tail_with_zero_word_patch_evidence_is_nonblocking(tmp_path: Path) -> None:
    job_dir = tmp_path / "short-tail-zero-word"
    job_dir.mkdir()
    (job_dir / "chunk-plan.json").write_text(
        json.dumps({
            "duration_seconds": 20.0,
            "chunks": [{"chunk_index": 0, "source_start_ms": 0, "source_end_ms": 20_000}],
        }),
        encoding="utf-8",
    )
    (job_dir / "merged-words.json").write_text(
        json.dumps({"words": [{"word": "完", "start_ms": 5_000, "end_ms": 7_000}]}),
        encoding="utf-8",
    )
    (job_dir / "subtitles.json").write_text(
        json.dumps({"segments": [{"segment_id": 1, "start_ms": 5_000, "end_ms": 7_000, "text": "完"}]}),
        encoding="utf-8",
    )
    (job_dir / "chirp-targeted-patch-plan.json").write_text(
        json.dumps({"items": [{
            "patch_index": 922101, "parent_chunk_index": 0,
            "source_start_ms": 6_000, "source_end_ms": 20_000,
            "gap_start_ms": 7_000, "gap_end_ms": 20_000,
        }]}),
        encoding="utf-8",
    )
    (job_dir / "chirp-targeted-patch-complete.json").write_text(
        json.dumps({
            "patch_decisions": [{"chunk_index": 922101, "applied": True, "patch_words_inserted": 1}],
            "verdicts": [{"patch_index": 922101, "status": "SUCCEEDED", "target_gap_word_count": 0}],
        }),
        encoding="utf-8",
    )
    # emulate short audible probe only in first 3 seconds, silence after
    def audible(start, end):
        return True if end <= 10_000 else False
    report = evaluate(job_dir, audibility_probe=audible, speech_probe=lambda s,e: {"robust_non_speech": False})
    assert report["status"] == "PASS"
    assert not report["blockers"]
    assert any(w.get("reason") == "short_audio_tail_verified_nonlexical_by_targeted_patch_words" for w in report["warnings"])


def test_completeness_auto_repair_short_audible_tail_reaches_media_end(tmp_path: Path) -> None:
    job_dir = tmp_path / "auto-repair-short-tail"
    job_dir.mkdir()
    (job_dir / "chunk-plan.json").write_text(
        json.dumps({
            "duration_seconds": 75.257,
            "chunks": [{"chunk_index": 8, "source_start_ms": 60_000, "source_end_ms": 75_300}],
        }),
        encoding="utf-8",
    )
    report = {
        "handoff_allowed": False,
        "blockers": [{
            "reason": "short_audible_tail_requires_review",
            "start_ms": 73_500,
            "end_ms": 73_800,
        }],
    }
    plan = build_auto_repair_patch_plan(job_dir, report)
    assert plan["status"] == "planned"
    assert plan["items"][0]["source_end_ms"] == 75_257


def test_completeness_auto_repair_tail_reaches_media_end(tmp_path: Path) -> None:
    job_dir = tmp_path / "auto-repair-tail"
    job_dir.mkdir()
    (job_dir / "chunk-plan.json").write_text(
        json.dumps(
            {
                "duration_seconds": 75.0,
                "chunks": [
                    {
                        "chunk_index": 8,
                        "source_start_ms": 60_000,
                        "source_end_ms": 74_000,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    report = {
        "handoff_allowed": False,
        "blockers": [
            {
                "reason": "uncovered_audio_tail",
                "start_ms": 73_500,
                "end_ms": 75_000,
            }
        ],
    }
    plan = build_auto_repair_patch_plan(job_dir, report)
    assert plan["status"] == "planned"
    assert plan["items"][0]["source_end_ms"] == 75_000


def test_preexport_evidence_does_not_require_export_manifest(tmp_path: Path) -> None:
    job_dir = tmp_path / "preexport"
    _write_job(job_dir)
    (job_dir / "chirp-completeness-repair-plan.json").write_text(
        json.dumps({"status": "needs_review", "items": []}),
        encoding="utf-8",
    )
    evidence = _preexport_artifact_evidence(
        job_dir,
        _CHIRP_COMPLETENESS_PREEXPORT_EVIDENCE,
    )
    names = {item["name"] for item in evidence}
    assert "chirp-completeness.json" in names
    assert "subtitles.srt" in names
    assert "export-manifest.json" not in names


def test_handoff_preexport_evidence_accepts_nested_bundle_paths(tmp_path: Path) -> None:
    job_dir = tmp_path / "handoff-evidence"
    _write_job(job_dir)
    bundle = job_dir / "chatgpt-handoff"
    bundle.mkdir()
    for name in ("handoff-manifest.json", "chirp-raw.srt", "raw-transcript.txt"):
        (bundle / name).write_text("evidence\n", encoding="utf-8")
    evidence = _preexport_artifact_evidence(
        job_dir,
        _CHATGPT_HANDOFF_PREEXPORT_EVIDENCE,
    )
    names = {item["name"] for item in evidence}
    assert "chatgpt-handoff/handoff-manifest.json" in names
    assert "chatgpt-handoff/chirp-raw.srt" in names
    assert "chatgpt-handoff/raw-transcript.txt" in names
