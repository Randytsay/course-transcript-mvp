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
- GCS ``buckets.get`` (metadata)
No model generation is ever invoked.
"""
from __future__ import annotations

import json
from typing import Any

READONLY_SCOPE = "https://www.googleapis.com/auth/cloud-platform.read-only"


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
        cred, scopes=[READONLY_SCOPE]
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

    headers = {"Authorization": f"Bearer {token}"}
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

    # 2) Vertex AI endpoint + permission (locations.get — read-only, free)
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

    # 3) GCS bucket metadata if configured
    bucket = str(meta.get("gcs_bucket") or "")
    if bucket:
        try:
            resp = requests.get(
                f"https://storage.googleapis.com/storage/v1/b/{bucket}",
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
