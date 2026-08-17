from __future__ import annotations

import concurrent.futures
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

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
        self.max_output_tokens = 3072
        self.reasoning_split = True

    def correct_window(self, items: list[dict[str, object]], terms: list[dict[str, object]], *, context: str) -> dict[str, dict[str, object]]:
        self.calls += 1
        if self.calls == 2:
            raise MiniMaxProviderError("quota exhausted", kind=ProviderFailureKind.USAGE_LIMIT)
        return {str(items[0]["segment_id"]): {"segment_id": str(items[0]["segment_id"]), "corrected_text": "M3文字"}}


class RuntimeRoutingTests(unittest.TestCase):
    def test_usage_limit_switches_once_and_never_reenters_m3(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            quota = FakeQuota()
            m3 = FakeM3()
            gemini_calls: list[int] = []

            def gemini(items: list[dict[str, object]], terms: list[dict[str, object]]) -> dict[str, dict[str, object]]:
                gemini_calls.append(1)
                return {str(items[0]["segment_id"]): {"segment_id": str(items[0]["segment_id"]), "corrected_text": "Gemini文字"}}

            with patch.dict(
                os.environ,
                {
                    "COURSE_TRANSCRIPT_RUNTIME_GIT_SHA": "a" * 40,
                    "COURSE_TRANSCRIPT_DOCKER_IMAGE_REVISION": "b" * 40,
                },
                clear=False,
            ):
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
            self.assertEqual(manifest["effective_m3_concurrency"], 1)
            self.assertEqual(manifest["effective_gemini_concurrency"], 1)
            self.assertEqual(manifest["runtime_git_sha"], "a" * 40)
            self.assertEqual(manifest["docker_image_revision"], "b" * 40)
            self.assertEqual(manifest["m3_max_output_tokens"], 3072)
            self.assertTrue(manifest["m3_reasoning_split"])

    def test_gemini_fallback_keeps_outer_window_parallelism(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            quota = FakeQuota()
            m3 = FakeM3()
            parallel_phase = threading.Event()
            both_inside = threading.Event()
            state_lock = threading.Lock()
            active = 0
            max_active = 0

            def gemini(items: list[dict[str, object]], terms: list[dict[str, object]]) -> dict[str, dict[str, object]]:
                nonlocal active, max_active
                if parallel_phase.is_set():
                    with state_lock:
                        active += 1
                        max_active = max(max_active, active)
                        if active >= 2:
                            both_inside.set()
                    try:
                        both_inside.wait(timeout=0.5)
                    finally:
                        with state_lock:
                            active -= 1
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

            # First M3 request succeeds; the second one fails and performs the
            # one-way switch. Only calls made after that switch are measured.
            runtime.correct_window([ITEM], [])
            runtime.correct_window([ITEM], [])
            self.assertEqual(runtime.active_provider, CorrectionProvider.GEMINI)
            parallel_phase.set()

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(runtime.correct_window, [ITEM], []),
                    pool.submit(runtime.correct_window, [ITEM], []),
                ]
                for future in futures:
                    future.result(timeout=2)

            self.assertEqual(max_active, 2)
            self.assertEqual(m3.calls, 2)
            manifest = json.loads((Path(directory) / "correction-routing.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["segment_counts"]["gemini-3.7-flash"], 3)
            self.assertEqual(manifest["effective_m3_concurrency"], 1)
            self.assertEqual(manifest["effective_gemini_concurrency"], 2)


if __name__ == "__main__":
    unittest.main()
