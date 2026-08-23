from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.providers.correction.base import ProviderError
from app.providers.correction.minimax import MiniMaxCorrectionProvider
from app.providers.correction.registry import (
    AIProviderProfileStore,
    LEGACY_MINIMAX_PROFILE_ID,
)


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


class TestProductionM3RoutingFields(unittest.TestCase):
    def _fields(self, *, enabled: str, policy: str, correction_enabled: bool = True):
        with patch.dict(os.environ, {"MINIMAX_M3_ENABLED": enabled}, clear=False):
            from app.api_ext import _production_correction_router_fields

            return _production_correction_router_fields(
                policy=policy,
                correction_enabled=correction_enabled,
            )

    def test_disabled_m3_first_remains_legacy_fail_closed(self):
        self.assertEqual(self._fields(enabled="false", policy="M3_FIRST"), {})

    def test_enabled_m3_first_pins_windowed_router(self):
        fields = self._fields(enabled="true", policy="M3_FIRST")
        self.assertEqual(fields["correction_provider"], "minimax")
        self.assertEqual(
            fields["correction_provider_profile_id"],
            LEGACY_MINIMAX_PROFILE_ID,
        )
        self.assertEqual(fields["correction_execution_mode"], "REALTIME")
        self.assertEqual(fields["correction_fallback_policy"], "RAW_CHIRP_FALLBACK")

    def test_gemini_first_never_pins_minimax_router(self):
        self.assertEqual(self._fields(enabled="true", policy="GEMINI_FIRST"), {})

    def test_correction_disabled_never_pins_minimax_router(self):
        self.assertEqual(
            self._fields(enabled="true", policy="M3_FIRST", correction_enabled=False),
            {},
        )


if __name__ == "__main__":
    unittest.main()
