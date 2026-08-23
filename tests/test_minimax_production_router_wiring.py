from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.jobs.correction_policy import GEMINI_FIRST, M3_FIRST
from app.providers.correction.base import ProviderError
from app.providers.correction.minimax import MiniMaxCorrectionProvider
from app.providers.correction.registry import (
    AIProviderProfileStore,
    LEGACY_MINIMAX_PROFILE_ID,
)
from app.providers.correction_routing import M3QuotaState
from app.providers.minimax_quota import MiniMaxQuotaSnapshot


class TestMiniMaxEndpointSelection(unittest.TestCase):
    def test_cn_base_normalizes_to_v1(self):
        calls = []

        def http(method, url, headers, payload=None):
            calls.append((method, url))
            return 200, {
                "choices": [{"message": {"content": "[]"}, "finish_reason": "stop"}],
                "usage": {},
            }

        provider = MiniMaxCorrectionProvider(
            api_key="fake",
            base_url="https://api.minimaxi.com",
            http=http,
        )
        provider.realtime_generate("x")
        self.assertEqual(calls[0][1], "https://api.minimaxi.com/v1/chat/completions")

    def test_global_base_remains_supported(self):
        provider = MiniMaxCorrectionProvider(
            api_key="fake",
            base_url="https://api.minimax.io/v1",
            http=lambda *args: (200, {"data": []}),
        )
        self.assertEqual(provider.models_url, "https://api.minimax.io/v1/models")

    def test_unapproved_host_is_rejected(self):
        with self.assertRaises(ProviderError) as cm:
            MiniMaxCorrectionProvider(
                api_key="fake",
                base_url="https://example.com/v1",
            )
        self.assertEqual(cm.exception.kind, "auth")


class TestLegacyMiniMaxTokenPlanProfile(unittest.TestCase):
    def test_reserved_profile_reads_existing_host_secret_without_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            key_file = root / "minimax-api-key"
            key_file.write_text("token-plan-secret\n", encoding="utf-8")
            store = AIProviderProfileStore(root / "profiles")
            with patch.dict(
                os.environ,
                {
                    "MINIMAX_API_KEY_FILE": str(key_file),
                    "MINIMAX_API_BASE_URL": "https://api.minimaxi.com",
                    "MINIMAX_M3_MODEL": "MiniMax-M3",
                },
                clear=False,
            ):
                client = store.build_client(LEGACY_MINIMAX_PROFILE_ID)
            self.assertIsInstance(client, MiniMaxCorrectionProvider)
            self.assertEqual(client.base_url, "https://api.minimaxi.com/v1")
            self.assertEqual(client.api_key, "token-plan-secret")
            self.assertFalse((root / "profiles" / LEGACY_MINIMAX_PROFILE_ID).exists())

    def test_reserved_profile_missing_secret_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = AIProviderProfileStore(root / "profiles")
            with patch.dict(
                os.environ,
                {"MINIMAX_API_KEY_FILE": str(root / "missing")},
                clear=False,
            ):
                with self.assertRaises(ProviderError) as cm:
                    store.build_client(LEGACY_MINIMAX_PROFILE_ID)
            self.assertEqual(cm.exception.kind, "auth")


class TestCorrectionTimeQuota(unittest.TestCase):
    def test_gemini_first_never_checks_minimax_quota(self):
        import app.providers.windowed_m3_policy as policy

        with patch.object(policy, "MiniMaxQuotaClient") as quota_client:
            snapshot = policy._quota_snapshot(GEMINI_FIRST)
        self.assertEqual(snapshot.state, M3QuotaState.UNKNOWN)
        self.assertEqual(snapshot.reason, "gemini_requested")
        quota_client.assert_not_called()

    def test_disabled_m3_does_not_check_quota(self):
        import app.providers.windowed_m3_policy as policy

        with patch.dict(os.environ, {"MINIMAX_M3_ENABLED": "false"}, clear=False):
            with patch.object(policy, "MiniMaxQuotaClient") as quota_client:
                snapshot = policy._quota_snapshot(M3_FIRST)
        self.assertEqual(snapshot.state, M3QuotaState.UNKNOWN)
        self.assertEqual(snapshot.reason, "m3_feature_disabled")
        quota_client.assert_not_called()

    def test_quota_check_disabled_is_fail_closed_without_network(self):
        import app.providers.windowed_m3_policy as policy

        with patch.dict(
            os.environ,
            {
                "MINIMAX_M3_ENABLED": "true",
                "MINIMAX_M3_QUOTA_CHECK_ENABLED": "false",
            },
            clear=False,
        ):
            with patch.object(policy, "MiniMaxQuotaClient") as quota_client:
                snapshot = policy._quota_snapshot(M3_FIRST)
        self.assertEqual(snapshot.state, M3QuotaState.UNKNOWN)
        self.assertEqual(snapshot.reason, "quota_check_disabled")
        quota_client.assert_not_called()

    def test_enabled_m3_forces_fresh_quota_at_correction_time(self):
        import app.providers.windowed_m3_policy as policy

        expected = MiniMaxQuotaSnapshot(
            M3QuotaState.AVAILABLE,
            "2026-08-23T15:00:00+00:00",
            source_pool="MiniMax-M3",
            interval_remaining=10,
            weekly_remaining=100,
        )
        with patch.dict(
            os.environ,
            {
                "MINIMAX_M3_ENABLED": "true",
                "MINIMAX_M3_QUOTA_CHECK_ENABLED": "true",
            },
            clear=False,
        ):
            with patch.object(policy, "MiniMaxQuotaClient") as quota_cls:
                quota_cls.return_value.get_quota.return_value = expected
                snapshot = policy._quota_snapshot(M3_FIRST)
        self.assertEqual(snapshot.state.value, "available")
        self.assertEqual(snapshot.checked_at, expected.checked_at)
        self.assertEqual(snapshot.source_pool, expected.source_pool)
        self.assertEqual(snapshot.interval_remaining, expected.interval_remaining)
        self.assertEqual(snapshot.weekly_remaining, expected.weekly_remaining)
        quota_cls.return_value.get_quota.assert_called_once_with(force_refresh=True)

    def test_quota_check_error_becomes_unknown_without_leaking_error(self):
        import app.providers.windowed_m3_policy as policy

        with patch.dict(
            os.environ,
            {
                "MINIMAX_M3_ENABLED": "true",
                "MINIMAX_M3_QUOTA_CHECK_ENABLED": "true",
            },
            clear=False,
        ):
            with patch.object(policy, "MiniMaxQuotaClient") as quota_cls:
                quota_cls.return_value.get_quota.side_effect = RuntimeError("secret provider body")
                snapshot = policy._quota_snapshot(M3_FIRST)
        self.assertEqual(snapshot.state, M3QuotaState.UNKNOWN)
        self.assertEqual(snapshot.reason, "quota_check_failed")
        self.assertNotIn("secret", snapshot.reason)


class TestCorrectionTimeDispatch(unittest.TestCase):
    def _ctx(self):
        return {
            "job_id": "job-1",
            "data_dir": "/tmp/data",
            "correction_provider": "",
            "correction_provider_profile_id": "",
            "correction_model": "",
            "correction_execution_mode": "REALTIME",
            "correction_fallback_policy": "RAW_CHIRP_FALLBACK",
            "source_revision": "sha",
            "source_sha256": "sha",
            "segments": [{"segment_id": "s1", "text": "原文"}],
            "raw_segments": [{"segment_id": "s1", "text": "原文"}],
            "glossary": [],
        }

    def test_available_m3_uses_windowed_router_not_legacy_runtime(self):
        import app.providers.windowed_m3_policy as policy
        import app.providers.correction_runtime_bridge as bridge
        import app.providers.correct_text_legacy_hardened as legacy

        quota = MiniMaxQuotaSnapshot(M3QuotaState.AVAILABLE, "now")
        result = {
            "correction_status": "completed_realtime",
            "correction_corrections": [
                {"segment_id": "s1", "corrected_text": "校正", "uncertain_terms": []}
            ],
            "correction_raw_response": {},
            "correction_prompt_version": "corr-v2",
            "correction_fallback_segment_ids": [],
            "correction_window_results": [],
            "correction_provider_circuit_opened": False,
        }
        with patch.dict(
            os.environ,
            {
                "CORRECTION_REQUESTED_POLICY": "M3_FIRST",
                "MINIMAX_M3_ENABLED": "true",
            },
            clear=False,
        ):
            with patch.object(policy, "_quota_snapshot", return_value=quota), \
                 patch.object(policy, "_job_context", return_value=(self._ctx(), Path("/tmp/job"))), \
                 patch.object(policy, "_write_route_evidence"), \
                 patch.object(policy, "_finalize_route_evidence"), \
                 patch.object(bridge, "run_module", return_value=result) as run_module, \
                 patch.object(bridge, "_write_corrected_outputs") as writer, \
                 patch.object(legacy, "main", return_value=0) as legacy_main:
                rc = policy.main()
        self.assertEqual(rc, 0)
        legacy_main.assert_not_called()
        run_module.assert_called_once()
        routed_ctx = run_module.call_args.kwargs["ctx"]
        self.assertEqual(routed_ctx["correction_provider"], "minimax")
        self.assertEqual(
            routed_ctx["correction_provider_profile_id"],
            LEGACY_MINIMAX_PROFILE_ID,
        )
        writer.assert_called_once()

    def test_unknown_quota_uses_gemini_legacy_path(self):
        import app.providers.windowed_m3_policy as policy
        import app.providers.correction_runtime_bridge as bridge
        import app.providers.correct_text_legacy_hardened as legacy

        quota = MiniMaxQuotaSnapshot(M3QuotaState.UNKNOWN, "now", reason="quota_check_failed")
        with patch.dict(
            os.environ,
            {
                "CORRECTION_REQUESTED_POLICY": "M3_FIRST",
                "MINIMAX_M3_ENABLED": "true",
            },
            clear=False,
        ):
            with patch.object(policy, "_quota_snapshot", return_value=quota), \
                 patch.object(policy, "_job_context", return_value=(self._ctx(), Path("/tmp/job"))), \
                 patch.object(policy, "_write_route_evidence"), \
                 patch.object(policy, "_finalize_route_evidence"), \
                 patch.object(bridge, "run_module") as run_module, \
                 patch.object(legacy, "main", return_value=0) as legacy_main:
                rc = policy.main()
        self.assertEqual(rc, 0)
        legacy_main.assert_called_once()
        run_module.assert_not_called()

    def test_unavailable_quota_uses_gemini_legacy_path(self):
        import app.providers.windowed_m3_policy as policy
        import app.providers.correction_runtime_bridge as bridge
        import app.providers.correct_text_legacy_hardened as legacy

        quota = MiniMaxQuotaSnapshot(M3QuotaState.UNAVAILABLE, "now", reason="no_allowance")
        with patch.dict(
            os.environ,
            {
                "CORRECTION_REQUESTED_POLICY": "M3_FIRST",
                "MINIMAX_M3_ENABLED": "true",
            },
            clear=False,
        ):
            with patch.object(policy, "_quota_snapshot", return_value=quota), \
                 patch.object(policy, "_job_context", return_value=(self._ctx(), Path("/tmp/job"))), \
                 patch.object(policy, "_write_route_evidence"), \
                 patch.object(policy, "_finalize_route_evidence"), \
                 patch.object(bridge, "run_module") as run_module, \
                 patch.object(legacy, "main", return_value=0) as legacy_main:
                rc = policy.main()
        self.assertEqual(rc, 0)
        legacy_main.assert_called_once()
        run_module.assert_not_called()

    def test_gemini_first_uses_legacy_without_m3_generation(self):
        import app.providers.windowed_m3_policy as policy
        import app.providers.correction_runtime_bridge as bridge
        import app.providers.correct_text_legacy_hardened as legacy

        quota = MiniMaxQuotaSnapshot(M3QuotaState.UNKNOWN, "", reason="gemini_requested")
        with patch.dict(
            os.environ,
            {
                "CORRECTION_REQUESTED_POLICY": "GEMINI_FIRST",
                "MINIMAX_M3_ENABLED": "true",
            },
            clear=False,
        ):
            with patch.object(policy, "_quota_snapshot", return_value=quota), \
                 patch.object(policy, "_job_context", return_value=(self._ctx(), Path("/tmp/job"))), \
                 patch.object(policy, "_write_route_evidence"), \
                 patch.object(policy, "_finalize_route_evidence"), \
                 patch.object(bridge, "run_module") as run_module, \
                 patch.object(legacy, "main", return_value=0) as legacy_main:
                rc = policy.main()
        self.assertEqual(rc, 0)
        legacy_main.assert_called_once()
        run_module.assert_not_called()


class TestCompatibilityEntrypoint(unittest.TestCase):
    def test_hardened_entry_uses_windowed_policy_when_m3_enabled(self):
        import app.providers.correct_text_hardened as hardened
        import app.providers.windowed_m3_policy as policy

        with patch.dict(os.environ, {"MINIMAX_M3_ENABLED": "true"}, clear=False):
            with patch.object(policy, "main", return_value=1) as routed, \
                 patch.object(hardened, "_with_consistency", side_effect=lambda rc: rc):
                rc = hardened.main()
        self.assertEqual(rc, 1)
        routed.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
