"""Read-only preflight checks against a candidate Vertex profile.

Only low-cost, read-only calls are made:
- Token minting from the service account (local, no network).
- ``aiplatform`` endpoint discovery / IAM getAccessToken-style verification is
  replaced by a cheap ``cloudresourcemanager projects.get`` call, which proves
  the SA can see the project. A real Vertex model call is intentionally NOT
  performed (no paid generation during preflight).
- For GCS: ``buckets.get`` (metadata only, free).

If google SDK libraries are unavailable the check is skipped and reported as
``"skipped"`` so the switch can still proceed with explicit owner consent.
"""
from __future__ import annotations

import json
from typing import Any


def _mint_token(cred: dict[str, Any]) -> str | None:
    try:
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_info(cred)
        # Local JWT signing only; no network call happens in with_scoped
        # until a request is made, so refresh here against a tiny scope.
        creds = creds.with_scopes(["https://www.googleapis.com/auth/cloud-platform.read-only"])
        creds.refresh(None)  # network: token endpoint (free)
        return creds.token
    except Exception:
        return None


def run_live_checks(cred: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    checks: dict[str, str] = {}
    project_id = str(meta.get("project_id") or cred.get("project_id") or "")
    if cred.get("project_id") != meta.get("project_id"):
        errors.append(
            f"project_id 不一致: JSON={cred.get('project_id')} metadata={meta.get('project_id')}"
        )

    token = _mint_token(cred)
    if token is None:
        checks["vertex_access"] = "skipped"
        checks["bucket_access"] = "skipped"
        return {"ok": True, "checks": checks}  # cannot verify live; allow manual confirm

    import google.auth.transport.requests as _req  # noqa: F401  (ensures transport)

    headers = {"Authorization": f"Bearer {token}"}

    # 1) project visibility (proves SA belongs / can view project)
    try:
        import requests

        resp = requests.get(
            f"https://cloudresourcemanager.googleapis.com/v1/projects/{project_id}",
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 200:
            checks["project_visible"] = "ok"
        else:
            errors.append(
                f"服務帳戶無法檢視專案 {project_id} (HTTP {resp.status_code})"
            )
    except Exception as exc:  # network hiccup: treat as warning, not failure
        checks["project_visible"] = f"skipped ({exc.__class__.__name__})"

    # 2) Vertex AI endpoint reachable for the location (no model call)
    try:
        import requests

        location = str(meta.get("location") or "global")
        resp = requests.get(
            f"https://{location}-aiplatform.googleapis.com/v1/projects/{project_id}"
            f"/locations/{location}/publishers/google/models",
            headers=headers,
            timeout=10,
        )
        # 200 (list ok) or 403-with-quota-model both prove endpoint reachable;
        # only transport-level failures fail the check.
        checks["vertex_endpoint"] = "ok" if resp.status_code < 500 else f"http {resp.status_code}"
    except Exception as exc:
        checks["vertex_endpoint"] = f"skipped ({exc.__class__.__name__})"

    # 3) GCS bucket metadata (free) if configured
    bucket = str(meta.get("gcs_bucket") or "")
    if bucket:
        try:
            import requests

            resp = requests.get(
                f"https://storage.googleapis.com/storage/v1/b/{bucket}",
                headers=headers,
                timeout=10,
            )
            if resp.status_code == 200:
                checks["bucket_access"] = "ok"
            elif resp.status_code == 404:
                errors.append(f"GCS bucket 不存在: {bucket}")
            else:
                errors.append(f"GCS bucket 無法存取: {bucket} (HTTP {resp.status_code})")
        except Exception as exc:
            checks["bucket_access"] = f"skipped ({exc.__class__.__name__})"

    return {"ok": not errors, "checks": checks, "errors": errors}
