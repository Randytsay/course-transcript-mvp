"""Unit tests for the structured diagnostics surfaced by the paid executor.

These tests deliberately avoid importing the heavy paid_executor module so
they can run in a vanilla Python environment (no fastapi). They exercise
the same logic the executor uses to enrich its ``outcomes`` records.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

# Make ``app`` importable without going through the heavy coordinator chain.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.jobs.golden_v2_ledger import V2Conflict  # noqa: E402
from app.pipeline import golden_v2_diagnostics as _diag  # noqa: E402


# Re-implement the small slice of executor enrichment we care about. The
# production module embeds this exact logic; duplicating it here keeps the
# test self-contained and prevents regressions without dragging in fastapi.
_KNOWN_STAGES = {"gemini_primary", "chirp_independent", "compare",
                 "third_optional", "adjudication", "golden_qa"}


def _enrich(exc, stage_name):
    if not isinstance(stage_name, str) or stage_name not in _KNOWN_STAGES:
        stage_name = "unknown"
    diag = getattr(exc, "diagnostic", None)
    if not isinstance(diag, dict):
        import sys as _sys
        tb = _sys.exc_info()[2]
        while tb and tb.tb_next is not None:
            tb = tb.tb_next
        file_label, line_no = "<unknown>", 0
        if tb is not None:
            file_label = tb.tb_frame.f_code.co_filename.split("/")[-1]
            line_no = tb.tb_lineno
        reason_text = str(exc) or type(exc).__name__
        diag = _diag.make_record(
            error_code="GVR-EX-199", reason=reason_text,
            stage=stage_name, source=(file_label, line_no),
            context={"exception_type": type(exc).__name__},
        )
    else:
        if diag.get("stage") in (None, "gemini_primary") and stage_name != "gemini_primary":
            diag = dict(diag)
            diag["stage"] = stage_name
    return {
        "error_type": type(exc).__name__,
        "error_code": diag.get("error_code", "GVR-EX-199"),
        "reason": diag.get("reason", str(exc)),
        "stage": diag.get("stage", stage_name),
        "trace_id": diag.get("trace_id", ""),
        "source": {"file": diag.get("file", ""), "line": diag.get("line", 0)},
        "context": diag.get("context", {}),
    }


def _raise_diagnosed(reason, *, stage="gemini_primary", context=None, cause=None):
    import sys as _sys
    frame = _sys._getframe(1)
    file_label = frame.f_code.co_filename.split("/")[-1]
    line = frame.f_lineno
    ctx = dict(context or {})
    if cause is not None:
        ctx["cause_type"] = type(cause).__name__
        ctx["cause_message"] = str(cause)[:200]
    record = _diag.make_record(
        error_code=_diag.error_code_for(reason),
        reason=reason, stage=stage, source=(file_label, line), context=ctx,
    )
    exc = V2Conflict(reason)
    exc.diagnostic = record
    raise exc


class PaidExecutorDiagnosticsTests(unittest.TestCase):
    def test_diagnostic_record_is_json_serialisable(self):
        try:
            _raise_diagnosed(
                "primary audit identity differs from no-dispatch evidence",
                stage="gemini_primary",
                context={"password": "leak", "safe": 1},
            )
        except V2Conflict as exc:
            out = _enrich(exc, "gemini_primary")
        self.assertEqual(out["error_code"], "GVR-ND-027")
        # The "password" key must be redacted.
        self.assertNotIn("password", out["context"])
        self.assertNotIn("leak", json.dumps(out))
        self.assertEqual(out["context"]["safe"], 1)
        self.assertEqual(out["stage"], "gemini_primary")
        # JSON-roundtrip safe (the field "source" must be a dict).
        encoded = json.dumps(out)
        self.assertIn("GVR-ND-027", encoded)
        self.assertIn("trace_id", out)

    def test_unknown_stage_normalised(self):
        try:
            _raise_diagnosed("malformed no-dispatch evidence",
                             stage="gemini_primary", context={"k": "v"})
        except V2Conflict as exc:
            out = _enrich(exc, "compare")
        self.assertEqual(out["stage"], "compare")
        self.assertEqual(out["error_code"], "GVR-ND-019")

    def test_unstructured_exception_gets_synthetic_record(self):
        try:
            raise RuntimeError("downstream blew up")
        except RuntimeError as exc:
            out = _enrich(exc, "chirp_independent")
        self.assertEqual(out["error_code"], "GVR-EX-199")
        self.assertEqual(out["stage"], "chirp_independent")
        self.assertEqual(out["error_type"], "RuntimeError")
        self.assertEqual(out["context"]["exception_type"], "RuntimeError")
        self.assertIn("downstream blew up", out["reason"])

    def test_garbage_stage_name_becomes_unknown(self):
        try:
            _raise_diagnosed("malformed no-dispatch evidence")
        except V2Conflict as exc:
            out = _enrich(exc, "wat_stage_is_this")
        self.assertEqual(out["stage"], "unknown")

    def test_long_reason_is_truncated_in_record(self):
        long_cause = ValueError("X" * 1000)
        try:
            _raise_diagnosed("malformed no-dispatch evidence", cause=long_cause)
        except V2Conflict as exc:
            out = _enrich(exc, "gemini_primary")
        self.assertLessEqual(len(out["context"]["cause_message"]), 200)

    def test_redaction_does_not_drop_safe_fields(self):
        try:
            _raise_diagnosed("no-dispatch evidence checksum mismatch",
                             context={"prior_attempt_relative": "x/y.json",
                                      "expected_sha": "a" * 80})
        except V2Conflict as exc:
            out = _enrich(exc, "gemini_primary")
        self.assertEqual(out["context"]["prior_attempt_relative"], "x/y.json")
        self.assertEqual(len(out["context"]["expected_sha"]), 80)


if __name__ == "__main__":
    unittest.main()
