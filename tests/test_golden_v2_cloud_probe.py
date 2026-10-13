"""No credentials or network: provider metadata probe contracts."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from app.pipeline.golden_v2_cloud_probe import probe_read_only


class CloudProbeTests(unittest.TestCase):
    def test_get_only_and_sanitize_secrets(self):
        calls = []
        secret = "local-test-token-never-log"
        def read(url, *, headers, timeout):
            self.assertEqual(headers["Authorization"], "Bearer " + secret)
            self.assertEqual(timeout, 15)
            calls.append(url)
            return SimpleNamespace(status_code=200)
        report = probe_read_only(
            project="test-example-project", bearer=secret, http_get=read,
            bucket="test-private-bucket",
        )
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(url.startswith("https://") for url in calls))
        self.assertIn("/locations/global/", calls[0])
        self.assertIn("/locations/us/", calls[2])
        self.assertNotIn(secret, json.dumps(report))
        self.assertNotIn("test-example-project", json.dumps(report))
        self.assertFalse(report["safe_to_charge"])
        self.assertFalse(report["provider_inference_capability_verified"])
        self.assertFalse(report["provider_calls_started"])
        self.assertFalse(report["gcs_mutation_started"])

    def test_get_failure_does_not_authorize(self):
        def forbidden(*_, **__):
            raise PermissionError("sensitive token in fake error")
        report = probe_read_only(
            project="test-example-project", bearer="secret-but-test-only",
            http_get=forbidden,
        )
        self.assertEqual(len(report["results"]), 3)
        self.assertTrue(all(item["error_type"] == "PermissionError"
                            for item in report["results"].values()))
        self.assertNotIn("sensitive token", json.dumps(report))
        self.assertFalse(report["safe_to_charge"])

    def test_bad_project_and_bucket_fail_closed(self):
        for project in ("../../../etc", "HI", "hello@cloud", ""):
            with self.assertRaises(ValueError):
                probe_read_only(
                    project=project, bearer="test-token-long",
                    http_get=lambda *_args, **_kwargs: None,
                )
        with self.assertRaises(ValueError):
            probe_read_only(
                project="test-example-project", bearer="test-token-long",
                bucket="../unsafe", http_get=lambda *_args, **_kwargs: None,
            )

    def test_cli_default_cannot_read_adc(self):
        script = Path(__file__).resolve().parents[1] / "scripts/golden_v2_cloud_probe.py"
        with TemporaryDirectory() as wd:
            output = subprocess.run(
                [sys.executable, str(script)],
                cwd=wd, capture_output=True, text=True, timeout=20,
                env={"PATH": "/usr/bin:/bin"},
            )
        self.assertEqual(output.returncode, 2, output.stderr)
        report = json.loads(output.stdout)
        self.assertEqual(report["mode"], "OFFLINE_NO_CREDENTIALS")
        self.assertFalse(report["safe_to_charge"])


if __name__ == "__main__":
    unittest.main()