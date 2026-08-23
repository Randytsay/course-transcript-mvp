"""OpenRouter correction provider (realtime + official beta Batch API).

Batch uses POST /api/beta/batches with endpoint=/v1/chat/completions and
per-window custom_id — NOT a wrapped realtime loop.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from .base import (
    ExecutionMode,
    ProviderCapabilities,
    ProviderError,
    ProviderId,
)

BASE_URL = "https://openrouter.ai/api/v1"
BATCH_URL = "https://openrouter.ai/api/beta/batches"


class OpenRouterCorrectionProvider:
    id = ProviderId.OPENROUTER
    display_name = "OpenRouter"
    default_model = "google/gemini-3.7-flash"

    def __init__(self, *, api_key: str, model: str | None = None, http=None):
        if not api_key:
            raise ProviderError("auth", "OpenRouter API key 未設定")
        self.api_key = api_key
        self.model = model or self.default_model
        self._http = http  # injected for tests: (method,url,headers,payload)->(status,json)

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_realtime=True,
            # Batch availability is MODEL-specific; the generic capability only
            # says the provider HAS an official batch API. Per-model support is
            # checked via model_supports_batch() before BATCH may be offered.
            supports_batch=True,
            supports_native_schema=False,  # prompt-forced JSON + strict validation
            supports_model_listing=True,
            pricing_known=False,           # only when models API returns pricing
        )

    def model_supports_batch(self, model: str) -> tuple[bool, str]:
        """Confirm batch availability for THIS model from provider metadata.

        OpenRouter exposes batch-capable models via ':batch' model variants
        or explicit metadata. If we cannot confirm -> BATCH disabled with a
        reason. Never enable batch on unverified models.
        """
        try:
            models = self.list_models()
        except ProviderError as exc:
            return False, f"無法取得 OpenRouter model 資料：{exc.safe_message}"
        ids = {m.get("id") for m in models if m.get("id")}
        if f"{model}:batch" in ids:
            return True, ""
        # check metadata flag if present
        for m in models:
            if m.get("id") == model and m.get("batch_supported"):
                return True, ""
        return False, (f"OpenRouter 未確認 model「{model}」支援批次；"
                       "僅有官方 :batch 變體或明確標示的 model 才能使用 Batch")

    # -- headers -------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _call(self, method: str, url: str, payload: dict[str, Any] | None = None):
        if self._http:
            return self._http(method, url, self._headers(), payload)
        import requests
        r = requests.request(method, url, headers=self._headers(),
                             json=payload, timeout=60)
        try:
            body = r.json()
        except Exception:
            body = {}
        return r.status_code, body

    # -- validation (read-only; no paid generation) ---------------------------

    def validate_credentials(self) -> dict[str, Any]:
        """Use the models list endpoint to verify the key without generation."""
        status, body = self._call("GET", f"{BASE_URL}/models")
        if status == 401:
            raise ProviderError("auth", "OpenRouter API key 無效（401）")
        if status != 200:
            raise ProviderError("unreachable", f"OpenRouter 無法連線（HTTP {status}）")
        models = [m.get("id") for m in (body.get("data") or []) if m.get("id")]
        return {"ok": True, "model_count": len(models), "models": models}

    def list_models(self) -> list[dict[str, Any]]:
        status, body = self._call("GET", f"{BASE_URL}/models")
        if status != 200:
            raise ProviderError("unreachable", f"OpenRouter models 查詢失敗（HTTP {status}）")
        out = []
        for m in body.get("data") or []:
            pricing = m.get("pricing") or {}
            out.append({
                "id": m.get("id"),
                "name": m.get("name"),
                "pricing_prompt": pricing.get("prompt"),
                "pricing_completion": pricing.get("completion"),
                "batch_supported": bool(m.get("batch_supported")),
                "batch_discount_documented": False,  # only set from official docs
            })
        return out

    # -- realtime ---------------------------------------------------------------

    def realtime_generate(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }
        status, body = self._call("POST", f"{BASE_URL}/chat/completions", payload)
        if status == 401:
            raise ProviderError("auth", "OpenRouter API key 無效")
        if status != 200:
            kind = "rate_limit" if status == 429 else "unknown"
            raise ProviderError(kind, f"OpenRouter 即時呼叫失敗（HTTP {status}）")
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("invalid_response", "OpenRouter 回應格式異常") from exc
        if not isinstance(content, str):
            raise ProviderError("invalid_response", "OpenRouter 回應內容非文字")
        return content

    # -- OFFICIAL batch ----------------------------------------------------------

    def build_batch_requests(self, windows: list[dict[str, Any]],
                             glossary: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """One request object per correction window, deterministic custom_id."""
        from .base import build_user_prompt

        requests_out = []
        for w in windows:
            requests_out.append({
                "custom_id": w["window_id"],           # deterministic id
                "body": {
                    "model": self.model,
                    "messages": [{"role": "user",
                                  "content": build_user_prompt(w["segments"], glossary)}],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.2,
                },
            })
        return requests_out

    def submit_batch(self, windows: list[dict[str, Any]],
                     glossary: list[dict[str, Any]]) -> str:
        """Submit the OFFICIAL OpenRouter batch; returns provider batch id.

        Official contract:
        POST /api/beta/batches
        { "endpoint": "/v1/chat/completions", "model": "<base model>",
          "requests": [ {"custom_id": ..., "body": {...}} ] }
        """
        requests_payload = self.build_batch_requests(windows, glossary)
        payload = {
            "endpoint": "/v1/chat/completions",
            "model": self.model,
            "requests": requests_payload,
        }
        status, body = self._call("POST", BATCH_URL, payload)
        if status == 401:
            raise ProviderError("auth", "OpenRouter API key 無效")
        if status not in (200, 201):
            raise ProviderError("batch_failed",
                                f"OpenRouter Batch 提交失敗（HTTP {status}）")
        batch_id = body.get("id") if isinstance(body, dict) else None
        if not batch_id:
            raise ProviderError("batch_failed", "OpenRouter Batch 回應缺少 id")
        return str(batch_id)

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        status, body = self._call("GET", f"{BATCH_URL}/{batch_id}")
        if status == 404:
            return {"status": "expired", "body": body}
        if status != 200:
            raise ProviderError("batch_failed",
                                f"OpenRouter Batch 查詢失敗（HTTP {status}）")
        state = str(body.get("status", "")).lower()
        terminal_map = {"completed": "completed", "failed": "failed",
                        "cancelled": "cancelled", "expired": "expired"}
        return {"status": terminal_map.get(state, "processing"),
                "raw_state": state, "body": body}

    def cancel_batch(self, batch_id: str) -> bool:
        status, _ = self._call("POST", f"{BATCH_URL}/{batch_id}/cancel")
        return status in (200, 201, 204)

    def fetch_results(self, batch_body: dict[str, Any]) -> list[dict[str, Any]]:
        """Read results inline from the completed batch response.

        Current OpenRouter Batch API returns `results` inline on the batch
        object when completed — no output_file_id / file download.
        """
        results = batch_body.get("results")
        if isinstance(results, list):
            return results
        if isinstance(results, dict) and isinstance(results.get("data"), list):
            return results["data"]
        raise ProviderError(
            "batch_failed",
            "Batch 完成但回應沒有內嵌 results；請確認官方 API 契約")
