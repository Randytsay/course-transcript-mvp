from app.pipeline.dynamic_worker_hardened import (
    _LOCAL_QA_REPAIR_EVIDENCE,
    _local_qa_repair_evidence,
)


def test_chatgpt_handoff_does_not_require_provider_review_terms() -> None:
    required = _local_qa_repair_evidence({"workflow_mode": "CHATGPT_HANDOFF"})
    assert "review-terms.json" not in required
    assert "subtitles-corrected.json" in required
    assert "cleanup-review.json" in required


def test_standard_workflow_keeps_review_terms_gate() -> None:
    required = _local_qa_repair_evidence({"workflow_mode": "FULL_AUTO"})
    assert required == _LOCAL_QA_REPAIR_EVIDENCE
    assert "review-terms.json" in required
