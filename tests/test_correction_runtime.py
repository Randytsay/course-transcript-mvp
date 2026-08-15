from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.jobs.correction_policy import M3_FIRST
from app.providers.correction_routing import CorrectionProvider, M3QuotaState, ProviderFailureKind
from app.providers.correction_runtime import CorrectionRuntime
from app.providers.minimax_provider import MiniMaxProviderError
from app.providers.minimax_quota import MiniMaxQuotaSnapshot


ITEM = {"segment_id": "s1", "start_ms": 0, "end_ms": 1000, "raw_text": "原始文字"}


class FakeQuota:
    def __init__(self) -> None:
        self.invalidations = 0

    def get_quota(self, *, force_refresh: bool) -> MiniMaxQuotaSnapshot:
        self.force_refresh = force_refresh
        return MiniMaxQuotaSnapshot(M3QuotaState.AVAILABLE, "2026-08-16T00:00:00+00:00", source_pool="general", interval_remaining=10, weekly_remaining=20)

    def invalidate(self) -> None:
        self.invalidations += 1


class FakeM3:
    def __init__(self) -> None:
        self.calls = 0

    def correct_window(self, items: list[dict[str, object]], terms: list[dict[str, object]], *, context: str) -> dict[str, dict[str, object]]:
        self.calls += 1
        if self.calls == 2:
            raise MiniMaxProviderError("quota exhausted", kind=ProviderFailureKind.USAGE_LIMIT)
        return {str(items[0]["segment_id"]): {"segment_id": str(items[0]["segment_id"]), "corrected_text": "M3文字"}}

    def extract_terms(self, items: list[dict[str, object]], *, context: str) -> list[dict[str, object]]:
        return [{"canonical": "術語", "variants": ["術語"], "confidence": "high"}]


class RuntimeRoutingTests(unittest.TestCase):
    def test_m3_terminology_is_written_and_manifested(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            quota = FakeQuota()
            m3 = FakeM3()
            runtime = CorrectionRuntime(
                requested_policy=M3_FIRST,
                m3_feature_enabled=True,
                quota_check_enabled=True,
                quota_client=quota,  # type: ignore[arg-type]
                m3_client=m3,  # type: ignore[arg-type]
                gemini_corrector=lambda items, terms: {},
                manifest_path=Path(directory) / "correction-routing.json",
            )
            terms = runtime.generate_terms([ITEM], lambda items: [])
            self.assertEqual(terms[0]["canonical"], "術語")
            glossary = Path(directory) / "glossary/global-terms.json"
            payload = json.loads(glossary.read_text(encoding="utf-8"))
            self.assertEqual(payload["provider"], "minimax")
            self.assertEqual(payload["usage_metadata"]["billing_mode"], "token_plan")
            manifest = json.loads((Path(directory) / "correction-routing.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["terminology_provider"], "minimax-m3")
            self.assertEqual(manifest["terminology_term_count"], 1)

    def test_usage_limit_switches_once_and_never_reenters_m3(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            quota = FakeQuota()
            m3 = FakeM3()
            gemini_calls: list[int] = []

            def gemini(items: list[dict[str, object]], terms: list[dict[str, object]]) -> dict[str, dict[str, object]]:
                gemini_calls.append(1)
                return {str(items[0]["segment_id"]): {"segment_id": str(items[0]["segment_id"]), "corrected_text": "Gemini文字"}}

            runtime = CorrectionRuntime(
                requested_policy=M3_FIRST,
                m3_feature_enabled=True,
                quota_check_enabled=True,
                quota_client=quota,  # type: ignore[arg-type]
                m3_client=m3,  # type: ignore[arg-type]
                gemini_corrector=gemini,
                manifest_path=Path(directory) / "correction-routing.json",
            )
            self.assertEqual(runtime.active_provider, CorrectionProvider.MINIMAX_M3)
            runtime.correct_window([ITEM], [])
            runtime.correct_window([ITEM], [])
            runtime.correct_window([ITEM], [])
            self.assertEqual(m3.calls, 2)
            self.assertEqual(len(gemini_calls), 2)
            self.assertEqual(quota.invalidations, 1)
            manifest = json.loads((Path(directory) / "correction-routing.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["provider_switches"]), 1)
            self.assertEqual(manifest["segment_counts"]["gemini-3.7-flash"], 2)


if __name__ == "__main__":
    unittest.main()
