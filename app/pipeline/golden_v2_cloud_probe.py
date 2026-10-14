"""Opt-in, GET-only Vertex/Speech/GCS environment probe for Golden V2.

This does not issue generate_content, Speech BatchRecognize, GCS writes, or
modify IAM. Inference capability and actual billing are *not* established by
successful metadata GET responses. Auth stays in the trusted operator runtime.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Callable

_PROJECT = re.compile(r"[a-z][a-z0-9-]{4,61}[a-z0-9]\Z")
_BUCKET = re.compile(r"[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]\Z")


def probe_read_only(
    *,
    project: str,
    bearer: str,
    http_get: Callable[..., Any],
    bucket: str | None = None,
    timeout_seconds: int = 15,
) -> dict[str, Any]:
    """Probe narrowly-scoped metadata URLs; do not expose bearer/project ID."""
    if not isinstance(project, str) or not _PROJECT.fullmatch(project):
        raise ValueError("invalid operator-selected cloud project")
    if not isinstance(bearer, str) or len(bearer) < 8:
        raise ValueError("operator ADC bearer is unavailable")
    if bucket is not None and (not isinstance(bucket, str)
                               or not _BUCKET.fullmatch(bucket)):
        raise ValueError("invalid private staging bucket")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 30:
        raise ValueError("invalid bounded cloud read timeout")
    endpoints = {
        "gemini_3_8_model_get": (
            f"https://aiplatform.googleapis.com/v1/projects/{project}/"
            "locations/global/publishers/google/models/gemini-3.8-flash"
        ),
        "gemini_3_5_transcribe_model_get": (
            f"https://aiplatform.googleapis.com/v1/projects/{project}/"
            "locations/global/publishers/google/models/gemini-3.5-transcribe"
        ),
        "speech_v2_us_recognizers_list": (
            f"https://us-speech.googleapis.com/v2/projects/{project}/"
            "locations/us/recognizers?pageSize=1"
        ),
    }
    if bucket is not None:
        # Listing objects and checking exact IAM permissions are meaningful
        # for this acceptance workload even when storage.buckets.get is denied.
        endpoints["gcs_objects_list"] = (
            f"https://storage.googleapis.com/storage/v1/b/{bucket}/o?maxResults=1"
        )
        endpoints["gcs_test_iam_permissions"] = (
            f"https://storage.googleapis.com/storage/v1/b/{bucket}/iam/testPermissions"
            "?permissions=storage.objects.create"
            "&permissions=storage.objects.get"
            "&permissions=storage.objects.list"
            "&permissions=storage.objects.delete"
        )
    results: dict[str, Any] = {}
    for key, url in endpoints.items():
        try:
            response = http_get(
                url, headers={"Authorization": "Bearer " + bearer},
                timeout=timeout_seconds,
            )
            status = getattr(response, "status_code", None)
            results[key] = {
                "http_status": status if type(status) is int else None,
                "metadata_accessible": status == 200,
            }
            if key == "gcs_test_iam_permissions" and status == 200:
                # The provider returns only permission strings; exclude
                # arbitrary response bodies and never log identity/secret.
                try:
                    payload = response.json()
                    actual = payload.get("permissions", [])
                    approved = (actual if isinstance(actual, list)
                                and all(isinstance(x, str) for x in actual)
                                else [])
                    results[key]["object_permissions"] = {
                        perm: perm in approved for perm in (
                            "storage.objects.create",
                            "storage.objects.get",
                            "storage.objects.list",
                            "storage.objects.delete",
                        )
                    }
                except (TypeError, ValueError, AttributeError):
                    results[key]["object_permissions"] = None
        except Exception as exc:
            # Error type only: provider exception text might include tokens.
            results[key] = {
                "http_status": None, "metadata_accessible": False,
                "error_type": type(exc).__name__,
            }
    return {
        "schema": "golden-v2-cloud-metadata-probe-v1",
        "project_sha256": hashlib.sha256(project.encode()).hexdigest(),
        "results": results,
        "mode": "READ_ONLY_GET_METADATA",
        "provider_inference_capability_verified": False,
        "actual_billing_pricing_verified": False,
        "provider_calls_started": False,
        "gcs_mutation_started": False,
        "drive_mutation_started": False,
        "safe_to_charge": False,
    }