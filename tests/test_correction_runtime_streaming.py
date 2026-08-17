from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.jobs.correction_policy import M3_FIRST
from app.providers.correction_routing import M3QuotaState
from app.providers.correction_runtime_streaming import StreamingCorrectionRuntime
from app.providers.minimax_quota import MiniMaxQuotaSnapshot


class FakeQuota:
    def get_quota(self, *, force_refresh: bool) -> MiniMaxQuotaSnapshot:
        return MiniMaxQuotaSnapshot(
            M3QuotaState.AVAILABLE,
            "2026-08-17T00:00:00+00:00",
            source_pool="general",
            interval_remaining=10,
            weekly_remaining=20,
        )

    def invalidate(self) -> None:
        pass


class FakeStreamingM3:
    max_output_tokens = 4096
    reasoning_split = True
    correction_thinking_mode = "disabled"
    streaming_enabled = True
    stream_deadline_seconds = 75.0

    def correct_window(self, items, terms, *, context):
        sid = str(items[0]["segment_id"])
        return {sid: {"segment_id": sid, "corrected_text": "M3文字"}}


class StreamingRuntimeTests(unittest.TestCase):
    def test_manifest_persists_streaming_transport_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "correction-routing.json"
            runtime = StreamingCorrectionRuntime(
                requested_policy=M3_FIRST,
                m3_feature_enabled=True,
                quota_check_enabled=True,
                quota_client=FakeQuota(),  # type: ignore[arg-type]
                m3_client=FakeStreamingM3(),  # type: ignore[arg-type]
                gemini_corrector=lambda items, terms: {},
                manifest_path=manifest_path,
            )
            self.assertEqual(runtime.active_provider.value, "minimax-m3")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertTrue(manifest["m3_streaming_enabled"])
            self.assertEqual(manifest["m3_transport"], "streaming_v2")
            self.assertEqual(manifest["m3_stream_deadline_seconds"], 75.0)
            self.assertEqual(manifest["m3_thinking_mode"], "disabled")
            self.assertTrue(manifest["m3_reasoning_split"])
            self.assertTrue(manifest["chirp_raw_immutable"])
            self.assertTrue(manifest["timestamps_immutable"])


if __name__ == "__main__":
    unittest.main()
