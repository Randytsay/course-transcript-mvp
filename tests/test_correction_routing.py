from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.jobs.correction_policy import (
    GEMINI_FIRST,
    M3_FIRST,
    get_job_correction_policy,
    normalize_correction_policy,
    set_job_correction_policy,
)
from app.jobs.store import JobStore
from app.providers.correction_routing import (
    CorrectionProvider,
    M3QuotaState,
    ProviderFailureKind,
    choose_initial_route,
    decide_provider_failure,
)


class CorrectionPolicyTests(unittest.TestCase):
    def test_default_and_normalization(self) -> None:
        self.assertEqual(normalize_correction_policy(None), GEMINI_FIRST)
        self.assertEqual(normalize_correction_policy("m3_first"), M3_FIRST)
        with self.assertRaises(ValueError):
            normalize_correction_policy("random-model")

    def test_policy_persists_separately_from_job_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory) / "test.db")
            now = "2026-08-15T00:00:00+00:00"
            with store.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO source_previews(
                        id, source_path, source_name, size_bytes, inspected_by,
                        inspected_at, expires_at, consumed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "preview-1",
                        "gdrive:test.mp3",
                        "test.mp3",
                        1,
                        "tester",
                        now,
                        "2099-01-01T00:00:00+00:00",
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO jobs(
                        id, preview_id, source_path, source_name, source_size_bytes,
                        language_code, profile, enable_gemini_correction,
                        enable_subtitles, require_human_review, status,
                        created_by, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "job-1",
                        "preview-1",
                        "gdrive:test.mp3",
                        "test.mp3",
                        1,
                        "cmn-Hant-TW",
                        "highest_accuracy",
                        1,
                        1,
                        0,
                        "queued",
                        "tester",
                        now,
                        now,
                    ),
                )
            self.assertEqual(get_job_correction_policy(store, "job-1"), GEMINI_FIRST)
            set_job_correction_policy(
                store,
                job_id="job-1",
                policy=M3_FIRST,
                actor="tester",
            )
            self.assertEqual(get_job_correction_policy(store, "job-1"), M3_FIRST)


class CorrectionRoutingTests(unittest.TestCase):
    def test_gemini_first_never_consumes_m3(self) -> None:
        decision = choose_initial_route(
            requested_policy=GEMINI_FIRST,
            m3_feature_enabled=True,
            m3_quota_state=M3QuotaState.AVAILABLE,
        )
        self.assertEqual(decision.provider, CorrectionProvider.GEMINI)
        self.assertIsNone(decision.fallback_provider)

    def test_m3_first_requires_feature_and_known_quota(self) -> None:
        disabled = choose_initial_route(
            requested_policy=M3_FIRST,
            m3_feature_enabled=False,
            m3_quota_state=M3QuotaState.AVAILABLE,
        )
        self.assertEqual(disabled.provider, CorrectionProvider.GEMINI)
        self.assertEqual(disabled.reason, "m3_feature_disabled")

        unknown = choose_initial_route(
            requested_policy=M3_FIRST,
            m3_feature_enabled=True,
            m3_quota_state=M3QuotaState.UNKNOWN,
        )
        self.assertEqual(unknown.provider, CorrectionProvider.GEMINI)
        self.assertEqual(unknown.reason, "m3_quota_unknown")

        available = choose_initial_route(
            requested_policy=M3_FIRST,
            m3_feature_enabled=True,
            m3_quota_state=M3QuotaState.AVAILABLE,
        )
        self.assertEqual(available.provider, CorrectionProvider.MINIMAX_M3)
        self.assertEqual(available.fallback_provider, CorrectionProvider.GEMINI)

    def test_usage_limit_switches_rest_of_job_to_gemini(self) -> None:
        decision = decide_provider_failure(
            CorrectionProvider.MINIMAX_M3,
            ProviderFailureKind.USAGE_LIMIT,
        )
        self.assertTrue(decision.switch_to_gemini_for_rest_of_job)
        self.assertFalse(decision.retry_same_provider)

    def test_rate_limit_retries_before_switching(self) -> None:
        decision = decide_provider_failure(
            CorrectionProvider.MINIMAX_M3,
            ProviderFailureKind.RATE_LIMIT,
        )
        self.assertTrue(decision.retry_same_provider)
        self.assertFalse(decision.switch_to_gemini_for_rest_of_job)

    def test_auth_failure_is_not_silently_hidden(self) -> None:
        decision = decide_provider_failure(
            CorrectionProvider.MINIMAX_M3,
            ProviderFailureKind.AUTHENTICATION,
        )
        self.assertTrue(decision.fail_closed)
        self.assertFalse(decision.switch_to_gemini_for_rest_of_job)


if __name__ == "__main__":
    unittest.main()
