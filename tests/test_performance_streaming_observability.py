from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.jobs import performance_enhanced


class StreamingPerformanceObservabilityTests(unittest.TestCase):
    def test_streaming_transport_is_promoted_from_job_routing_evidence(self) -> None:
        base_summary = {
            "stageAttempts": [],
            "stageTotals": [],
            "activeStageDurationMs": 0,
            "audioDurationMs": 0,
            "chunks": [],
            "geminiCalls": [],
            "accounting": {},
        }
        routing = {
            "requested_policy": "M3_FIRST",
            "initial_provider": "minimax-m3",
            "initial_route_reason": "m3_available",
            "provider_switches": [],
            "segment_counts": {"minimax-m3": 10},
            "m3_quota_state_at_start": "available",
            "m3_reasoning_split": True,
            "m3_thinking_mode": "disabled",
            "m3_streaming_enabled": True,
            "m3_stream_deadline_seconds": 75.0,
            "m3_transport": "streaming_v2",
            "m3_max_output_tokens": 4096,
        }
        with tempfile.TemporaryDirectory() as directory, patch.object(
            performance_enhanced.base,
            "build_performance_summary",
            return_value=base_summary,
        ), patch.object(
            performance_enhanced.base,
            "_read_json",
            return_value=routing,
        ):
            summary = performance_enhanced.build_performance_summary(
                Path(directory) / "jobs.db",
                Path(directory),
                "job-1",
            )
        self.assertEqual(summary["correctionRouting"]["m3Transport"], "streaming_v2")
        self.assertTrue(summary["correctionRouting"]["m3StreamingEnabled"])
        self.assertEqual(summary["correctionRouting"]["m3StreamDeadlineSeconds"], 75.0)
        self.assertEqual(summary["observability"]["m3Transport"], "streaming_v2")
        self.assertTrue(summary["observability"]["m3StreamingEnabled"])
        self.assertEqual(summary["observability"]["m3StreamDeadlineSeconds"], 75.0)


if __name__ == "__main__":
    unittest.main()
