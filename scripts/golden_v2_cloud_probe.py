"""Explicit, read-only GCP metadata probe; never sends a paid inference call."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pipeline.golden_v2_cloud_probe import probe_read_only


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=os.environ.get("GOOGLE_CLOUD_PROJECT"))
    parser.add_argument("--bucket", default=None)
    parser.add_argument("--check-live", action="store_true",
                        help="Use operator runtime ADC for GET-only metadata.")
    args = parser.parse_args()
    if not args.check_live:
        print(json.dumps({
            "schema": "golden-v2-cloud-metadata-probe-v1",
            "mode": "OFFLINE_NO_CREDENTIALS",
            "provider_inference_capability_verified": False,
            "safe_to_charge": False, "provider_calls_started": False,
        }, indent=2))
        return 2
    if not args.project:
        parser.error("--project or GOOGLE_CLOUD_PROJECT required")
    try:
        import google.auth
        from google.auth.transport.requests import Request
        import requests
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        credentials.refresh(Request())
        report = probe_read_only(
            project=args.project, bearer=credentials.token,
            http_get=requests.get, bucket=args.bucket,
        )
    except Exception as exc:
        # Never disclose exception body, paths, project ID or credentials.
        report = {
            "schema": "golden-v2-cloud-metadata-probe-v1",
            "mode": "READ_ONLY_PROBE_BLOCKED",
            "error_type": type(exc).__name__,
            "provider_inference_capability_verified": False,
            "safe_to_charge": False, "provider_calls_started": False,
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("mode") == "READ_ONLY_GET_METADATA" else 2


if __name__ == "__main__":
    raise SystemExit(main())