"""MiniMax correction provider (realtime only).

Wraps the existing validated MiniMax adapter. MiniMax M3 has no officially
documented discounted Batch API, so supports_batch is False — the UI shows
realtime only and never a fake 'batch discount'.
"""
from __future__ import annotations

from typing import Any

from .base import (
    ProviderCapabilities,
    ProviderError,
    ProviderId,
)

BASE_URL = "https://api.minimax.io/v1"
CHAT_URL = f"{BASE_URL}/chat/completions"   # current OpenAI-compatible route
MODELS_URL = f"{BASE_URL}/models"           # free key verification
DEFAULT_MODEL = "MiniMax-M3"


class MiniMaxCorrectionProvider:
    id = ProviderId.MINIMAX
    display_name = "MiniMax"
    default_model = DEFAULT_MODEL
    capabilities = ProviderCapabilities(
        supports_realtime=True,
        supports_batch=False,          # no official batch documented -> not offered
        supports_native_schema=False,  # prompt-forced JSON + strict validation
        supports_model_listing=True,
        pricing_known=False,           # token-plan / manual metadata only
        batch_note="MiniMax 官方目前未提供批次折扣 API，僅提供即時模式",
    )

    def __init__(self, *, api_key: str, model: str | None = None, http=None):
        if not api_key:
            raise ProviderError("auth", "MiniMax API key 未設定")
        self.api_key = api_key
        self.model = model or self.default_model
        self._http = http  # (method,url,headers,payload)->(status,json)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"}

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

    def validate_credentials(self) -> dict[str, Any]:
        """Free key verification via GET /v1/models (no paid generation).

        200 -> key verified + models listed
        401/403 -> FAIL
        network/5xx -> UNAVAILABLE / FAIL
        """
        status, body = self._call("GET", MODELS_URL)
        if status in (401, 403):
            raise ProviderError("auth", "MiniMax API key 無效（驗證失敗）")
        if status >= 500:
            raise ProviderError("unreachable", f"MiniMax 無法連線（HTTP {status}）")
        if status != 200:
            raise ProviderError("unreachable", f"MiniMax models 驗證失敗（HTTP {status}）")
        models: list[str] = []
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, list):
            models = [str(m["id"]) for m in data
                      if isinstance(m, dict) and m.get("id")]
        return {"ok": True, "key_verified": True,
                "models": models,
                "note": "API Key 已驗證（唯讀 /v1/models，未產生費用）"}

    def realtime_generate(self, prompt: str) -> str:
        """OpenAI-compatible /v1/chat/completions.

        No native JSON schema assumed: prompt forces JSON; caller must run
        strict canonical-schema validation on the parsed output.
        """
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        status, body = self._call("POST", CHAT_URL, payload)
        if status in (401, 403):
            raise ProviderError("auth", "MiniMax API key 無效或無權限")
        if status == 429:
            raise ProviderError("rate_limit", "MiniMax 額度或頻率限制")
        if status != 200:
            raise ProviderError("unknown", f"MiniMax 即時呼叫失敗（HTTP {status}）")
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("invalid_response", "MiniMax 回應格式異常") from exc
        if not isinstance(content, str):
            raise ProviderError("invalid_response", "MiniMax 回應內容非文字")
        return content

    # Batch intentionally NOT implemented — capability reports unsupported.
