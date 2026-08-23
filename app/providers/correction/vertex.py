"""Vertex AI correction provider (realtime + official BatchPrediction).

Auth comes from the Vertex Account Profile Manager (runtime credential at
GOOGLE_APPLICATION_CREDENTIALS) — no API keys here.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any

from .base import (
    ExecutionMode,
    ProviderCapabilities,
    ProviderError,
    ProviderId,
)

DEFAULT_MODEL = "gemini-3.7-flash"


def _location() -> str:
    return os.environ.get("GOOGLE_CLOUD_LOCATION", "global").strip() or "global"


def _project() -> str:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    if not project:
        raise ProviderError("unreachable", "尚未設定 GOOGLE_CLOUD_PROJECT（請先在 AI 帳戶管理切換）")
    return project


def _endpoint_host(location: str) -> str:
    return "aiplatform.googleapis.com" if location == "global" \
        else f"{location}-aiplatform.googleapis.com"


class VertexCorrectionProvider:
    id = ProviderId.VERTEX
    display_name = "Google Vertex AI"
    default_model = DEFAULT_MODEL
    # Gemini 3.7 Flash on Vertex supports native batch prediction.
    capabilities = ProviderCapabilities(
        supports_realtime=True,
        supports_batch=True,
        supports_native_schema=True,
        supports_model_listing=False,
        pricing_known=True,
    )

    def __init__(self, *, model: str = DEFAULT_MODEL,
                 gcs_bucket: str | None = None, http_get=None, http_post=None):
        self.model = model
        self.gcs_bucket = gcs_bucket or os.environ.get("GCS_BUCKET", "")
        self._http_get = http_get      # injection for tests; (url, headers)->(status,json)
        self._http_post = http_post    # (url, headers, payload)->(status,json)

    # -- realtime ----------------------------------------------------------

    def realtime_generate(self, prompt: str) -> str:
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(vertexai=True, project=_project(),
                                  location=_location())
            response = client.models.generate_content(
                model=self.model, contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", temperature=0.2),
            )
            return response.text or ""
        except Exception as exc:
            raise ProviderError("unknown",
                                f"Vertex 即時呼叫失敗: {exc.__class__.__name__}") from exc

    # -- official batch ------------------------------------------------------

    def submit_batch(self, *, input_gcs_uri: str, display_name: str) -> str:
        """Create a real BatchPrediction job from a GCS JSONL input."""
        location = _location()
        host = _endpoint_host(location)
        url = (f"https://{host}/v1/projects/{_project()}/locations/{location}"
               f"/batchPredictionJobs")
        payload = {
            "displayName": display_name[:60],
            "model": f"publishers/google/models/{self.model}",
            "inputConfig": {
                "instancesFormat": "jsonl",
                "gcsSource": {"uris": [input_gcs_uri]},
            },
            "outputConfig": {
                "predictionsFormat": "jsonl",
                "gcsDestination": {
                    "outputUriPrefix": f"gs://{self.gcs_bucket}/ai-batch/{display_name}/out"},
            },
        }
        status, body = self._post(url, payload)
        if status not in (200, 201):
            raise ProviderError("batch_failed", f"Vertex Batch 建立失敗 (HTTP {status})")
        name = body.get("name") if isinstance(body, dict) else None
        if not name:
            raise ProviderError("batch_failed", "Vertex Batch 回應缺少 job name")
        return str(name)

    def get_batch(self, job_name: str) -> dict[str, Any]:
        location = _location()
        host = _endpoint_host(location)
        url = f"https://{host}/v1/{job_name}"
        status, body = self._get(url)
        if status != 200:
            raise ProviderError("batch_failed", f"Vertex Batch 查詢失敗 (HTTP {status})")
        state = str(body.get("state", ""))
        mapping = {
            "JOB_STATE_SUCCEEDED": "completed",
            "JOB_STATE_FAILED": "failed",
            "JOB_STATE_CANCELLED": "cancelled",
            "JOB_STATE_EXPIRED": "expired",
        }
        return {"status": mapping.get(state, "processing"),
                "raw_state": state, "body": body}

    def cancel_batch(self, job_name: str) -> bool:
        location = _location()
        host = _endpoint_host(location)
        url = f"https://{host}/v1/{job_name}:cancel"
        status, _ = self._post(url, {})
        return status in (200, 204)

    def output_gcs_uri(self, job_body: dict[str, Any]) -> str | None:
        dest = (job_body.get("outputConfig") or {}).get("gcsDestination") or {}
        uris = dest.get("outputUriPrefix") or []
        if isinstance(uris, str):
            return uris
        return uris[0] if uris else None

    # -- http helpers (injected in tests) ------------------------------------

    def _headers(self) -> dict[str, str]:
        import google.auth.transport.requests as gatr
        from google.oauth2 import service_account

        cred_path = os.environ.get(
            "GOOGLE_APPLICATION_CREDENTIALS", "/run/ai-runtime/gcp-sa.json")
        creds = service_account.Credentials.from_service_account_file(
            cred_path, scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(gatr.Request())
        return {"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"}

    def _get(self, url: str):
        if self._http_get:
            return self._http_get(url, self._headers())
        import requests
        r = requests.get(url, headers=self._headers(), timeout=30)
        return r.status_code, _safe_json(r)

    def _post(self, url: str, payload: dict[str, Any]):
        if self._http_post:
            return self._http_post(url, self._headers(), payload)
        import requests
        r = requests.post(url, headers=self._headers(), json=payload, timeout=60)
        return r.status_code, _safe_json(r)


def _safe_json(resp) -> Any:
    try:
        return resp.json()
    except Exception:
        return {}
