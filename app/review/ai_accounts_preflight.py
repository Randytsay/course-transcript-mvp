"""Read-only preflight checks against a candidate Vertex profile.

Definition of preflight here: "do we have enough confidence to put this
profile into production?" — every check must either PASS with evidence or
FAIL. There is no silent skip. Network/SDK unavailability is reported as
``status=unavailable`` with ``ok=false`` (fail closed); production switches
require the checks to actually pass.

All calls are read-only and free:
- service-account token mint via google-auth with a proper transport Request
- Cloud Resource Manager ``projects.get`` (project visibility)
- Vertex AI ``projects.locations`` get (endpoint + permission)
- Speech-to-Text ``recognizers.list`` in the production ``us`` region
- Cloud Resource Manager ``projects.testIamPermissions`` for the exact
  ``speech.recognizers.recognize`` permission used by paid Chirp submission
- GCS ``objects.list`` (pipeline bucket access)
No model generation is ever invoked.
"""
from __future__ import annotations

import json
from typing import Any

# Cloud Billing's ``projects.getBillingInfo`` endpoint does not accept the
# read-only OAuth scope, even though the request itself is read-only.  IAM
# still limits the service account to the read-only/project and bucket roles
# configured for the profile; this broader OAuth scope only allows Google API
# auth to route the metadata request correctly.
PREFLIGHT_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
# Kept as an alias for callers/tests that imported the old constant.
READONLY_SCOPE = PREFLIGHT_SCOPE


def vertex_endpoint_host(location: str) -> str:
    """Official Vertex endpoint host for a location.

    'global' uses aiplatform.googleapis.com; regional locations use
    {location}-aiplatform.googleapis.com.
    """
    location = (location or "global").strip()
    if location == "global":
        return "aiplatform.googleapis.com"
    return f"{location}-aiplatform.googleapis.com"


class PreflightUnavailable(Exception):
    """Raised when checks cannot run (SDK/network) — treated as FAIL."""


def _mint_token(cred: dict[str, Any], request: Any) -> str:
    """Refresh a service-account token using google-auth's Request transport."""
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_info(
        cred, scopes=[PREFLIGHT_SCOPE]
    )
    creds.refresh(request)  # raises google.auth exceptions on failure
    if not creds.token:
        raise PreflightUnavailable("token mint returned empty token")
    return creds.token


def run_live_checks(cred: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    checks: dict[str, str] = {}
    project_id = str(meta.get("project_id") or cred.get("project_id") or "")

    if cred.get("project_id") != meta.get("project_id"):
        return {
            "ok": False,
            "checks": {"credential": "project mismatch"},
            "errors": [f"project mismatch: credential={cred.get('project_id')} "
                       f"metadata={meta.get('project_id')}"],
        }

    try:
        import google.auth.transport.requests as gatr

        request = gatr.Request()
    except Exception as exc:  # SDK missing -> fail closed
        return {"ok": False, "status": "unavailable",
                "errors": [f"google-auth SDK 無法使用: {exc.__class__.__name__}"],
                "checks": {}}

    try:
        token = _mint_token(cred, request)
        checks["token_mint"] = "ok"
    except Exception as exc:
        return {"ok": False,
                "errors": [f"憑證無法取得 token（refresh 失敗）: {exc}"],
                "checks": {"token_mint": f"fail ({exc.__class__.__name__})"}}

    # Service-account calls made from a local credential do not otherwise carry
    # a quota/billing project.  Supplying it is required for Cloud Resource
    # Manager, Cloud Billing, and Service Usage APIs, and keeps quota charges
    # attached to the candidate project being checked.
    headers = {
        "Authorization": f"Bearer {token}",
        "x-goog-user-project": project_id,
    }
    import requests

    # 1) project visibility
    try:
        resp = requests.get(
            f"https://cloudresourcemanager.googleapis.com/v1/projects/{project_id}",
            headers=headers, timeout=10,
        )
        if resp.status_code == 200:
            checks["project_visible"] = "ok"
        else:
            errors.append(f"專案 {project_id} 無法檢視 (HTTP {resp.status_code})")
            checks["project_visible"] = f"fail http {resp.status_code}"
    except requests.RequestException as exc:
        errors.append(f"無法連線驗證專案權限: {exc.__class__.__name__}")
        checks["project_visible"] = "unavailable"

    # 1b) Billing linkage and required service APIs (read-only metadata calls).
    try:
        resp = requests.get(
            f"https://cloudbilling.googleapis.com/v1/projects/{project_id}/billingInfo",
            headers=headers, timeout=10,
        )
        body = resp.json() if hasattr(resp, "json") else {}
        billing_enabled = bool(resp.status_code == 200 and body.get("billingEnabled"))
        if billing_enabled:
            checks["billing_enabled"] = "ok"
        else:
            errors.append(f"專案 {project_id} 尚未確認 Cloud Billing 已啟用")
            checks["billing_enabled"] = f"fail http {resp.status_code}"
    except (requests.RequestException, ValueError, AttributeError) as exc:
        errors.append(f"無法連線驗證 Cloud Billing: {exc.__class__.__name__}")
        checks["billing_enabled"] = "unavailable"

    for service, label in (
        ("speech.googleapis.com", "chirp_api"),
        ("aiplatform.googleapis.com", "vertex_api"),
    ):
        try:
            resp = requests.get(
                "https://serviceusage.googleapis.com/v1/projects/"
                f"{project_id}/services/{service}",
                headers=headers, timeout=10,
            )
            body = resp.json() if hasattr(resp, "json") else {}
            enabled = resp.status_code == 200 and body.get("state") == "ENABLED"
            if enabled:
                checks[label] = "ok"
            else:
                errors.append(f"{label} 尚未啟用或無法確認 (HTTP {resp.status_code})")
                checks[label] = f"fail http {resp.status_code}"
        except (requests.RequestException, ValueError, AttributeError) as exc:
            errors.append(f"無法連線驗證 {label}: {exc.__class__.__name__}")
            checks[label] = "unavailable"

    # 2) Speech-to-Text data-plane IAM. Service Usage only proves that the API
    # is enabled; it does not prove this service account can use recognizers.
    try:
        resp = requests.get(
            f"https://us-speech.googleapis.com/v2/projects/{project_id}"
            "/locations/us/recognizers",
            params={"pageSize": 1},
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 200:
            checks["speech_access"] = "ok"
        else:
            errors.append(
                "Cloud Speech IAM 存取失敗 "
                f"(HTTP {resp.status_code})；請確認服務帳戶具有 Cloud Speech Client"
            )
            checks["speech_access"] = f"fail http {resp.status_code}"
    except requests.RequestException as exc:
        errors.append(f"無法連線驗證 Cloud Speech IAM: {exc.__class__.__name__}")
        checks["speech_access"] = "unavailable"

    # 2b) Exact permission used by BatchRecognize. A recognizers.list success
    # alone is not sufficient evidence that paid recognition can be submitted.
    if checks.get("speech_access") == "ok":
        try:
            resp = requests.post(
                f"https://cloudresourcemanager.googleapis.com/v1/projects/{project_id}:testIamPermissions",
                headers={**headers, "Content-Type": "application/json"},
                json={"permissions": ["speech.recognizers.recognize"]},
                timeout=10,
            )
            body = resp.json() if hasattr(resp, "json") else {}
            granted = set(body.get("permissions") or []) if resp.status_code == 200 else set()
            if "speech.recognizers.recognize" in granted:
                checks["speech_recognize_permission"] = "ok"
            else:
                errors.append(
                    "Cloud Speech 辨識權限不足；服務帳戶缺少 "
                    "speech.recognizers.recognize（Cloud Speech Client）"
                )
                checks["speech_recognize_permission"] = (
                    f"fail http {resp.status_code}" if resp.status_code != 200 else "missing"
                )
        except requests.RequestException as exc:
            errors.append(f"無法驗證 Cloud Speech 辨識權限: {exc.__class__.__name__}")
            checks["speech_recognize_permission"] = "unavailable"

    # 3) Vertex AI endpoint + permission (locations.get — read-only, free)
    # "global" uses the official global endpoint, NOT "global-aiplatform...".
    location = str(meta.get("location") or "global")
    vertex_host = vertex_endpoint_host(location)
    try:
        resp = requests.get(
            f"https://{vertex_host}/v1/projects/{project_id}"
            f"/locations/{location}",
            headers=headers, timeout=10,
        )
        if resp.status_code == 200:
            checks["vertex_access"] = "ok"
        else:
            errors.append(f"Vertex AI 存取失敗 (HTTP {resp.status_code}) — "
                          "401/403 表示權限不足，404 表示 location 或 API 未啟用")
            checks["vertex_access"] = f"fail http {resp.status_code}"
    except requests.RequestException as exc:
        errors.append(f"無法連線 Vertex AI 端點: {exc.__class__.__name__}")
        checks["vertex_access"] = "unavailable"

    # 4) GCS object-list access if configured.  The pipeline needs to list and
    # write objects; bucket metadata (storage.buckets.get) is intentionally not
    # required by the service-account role set and can be denied even when the
    # bucket is fully usable by the pipeline.
    bucket = str(meta.get("gcs_bucket") or "")
    if bucket:
        try:
            resp = requests.get(
                f"https://storage.googleapis.com/storage/v1/b/{bucket}/o",
                params={"maxResults": 1},
                headers=headers, timeout=10,
            )
            if resp.status_code == 200:
                checks["bucket_access"] = "ok"
            else:
                errors.append(f"GCS bucket {bucket} 無法存取 (HTTP {resp.status_code})")
                checks["bucket_access"] = f"fail http {resp.status_code}"
        except requests.RequestException as exc:
            errors.append(f"無法連線 GCS 驗證 bucket: {exc.__class__.__name__}")
            checks["bucket_access"] = "unavailable"

    return {"ok": not errors, "errors": errors, "checks": checks}
