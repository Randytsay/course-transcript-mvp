"""MiniMax correction provider (realtime only).

MiniMax M3 is used as a text-only correction engine behind the shared
correction router. This adapter deliberately does not implement a second
correction runtime and does not expose a fake Batch mode.

Important invariants:
- correction thinking is disabled for this deterministic transformation;
- the prompt asks for the canonical JSON array and the server validates it;
- no provider-specific ``response_format=json_object`` is sent because the
  canonical response is an array and MiniMax is declared non-native-schema;
- non-success HTTP responses are mapped to safe error kinds without retaining
  provider response text in the exception;
- when finish/usage metadata is present it is validated before content is
  accepted;
- runtime base URLs are restricted to MiniMax's fixed CN/global API hosts.
"""
from __future__ import annotations

from urllib.parse import urlparse
from typing import Any

from .base import (
    ProviderCapabilities,
    ProviderError,
    ProviderId,
)

GLOBAL_BASE_URL = "https://api.minimax.io/v1"
CN_BASE_URL = "https://api.minimaxi.com/v1"
DEFAULT_MODEL = "MiniMax-M3"
DEFAULT_MAX_COMPLETION_TOKENS = 4096
# The CN OpenAI-compatible /v1/chat/completions contract documents a hard
# max_completion_tokens ceiling of 2048. Keep the broader historical default
# for the global endpoint, but never send an invalid oversized request to CN.
CN_MAX_COMPLETION_TOKENS = 2048
_ALLOWED_API_HOSTS = frozenset({"api.minimax.io", "api.minimaxi.com"})


def _normalize_base_url(value: str | None) -> str:
    raw = (value or GLOBAL_BASE_URL).strip().rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_API_HOSTS:
        raise ProviderError("auth", "MiniMax API endpoint 不在允許的官方主機清單")
    if parsed.username or parsed.password or parsed.port:
        raise ProviderError("auth", "MiniMax API endpoint 格式不允許自訂認證或 port")
    path = parsed.path.rstrip("/")
    if path not in {"", "/v1"}:
        raise ProviderError("auth", "MiniMax API endpoint 只允許官方根路徑或 /v1")
    return f"https://{parsed.hostname}/v1"


class MiniMaxCorrectionProvider:
    id = ProviderId.MINIMAX
    display_name = "MiniMax"
    default_model = DEFAULT_MODEL
    # Only the hardened provider opts into per-window fallback semantics.
    # Generic/custom MiniMax-like clients keep the old strict single-request
    # contract unless they explicitly advertise the same capability.
    supports_window_fallback = True
    capabilities = ProviderCapabilities(
        supports_realtime=True,
        supports_batch=False,
        supports_native_schema=False,
        supports_model_listing=True,
        pricing_known=False,
        batch_note="MiniMax 官方目前未提供批次折扣 API，僅提供即時模式",
    )

    def __init__(self, *, api_key: str, model: str | None = None, http=None,
                 max_completion_tokens: int = DEFAULT_MAX_COMPLETION_TOKENS,
                 base_url: str | None = None):
        if not api_key:
            raise ProviderError("auth", "MiniMax API key 未設定")
        self.api_key = api_key
        self.model = model or self.default_model
        self._http = http
        self.base_url = _normalize_base_url(base_url)
        self.chat_url = f"{self.base_url}/chat/completions"
        self.models_url = f"{self.base_url}/models"
        requested_max = max(256, int(max_completion_tokens))
        self.max_completion_tokens = (
            min(requested_max, CN_MAX_COMPLETION_TOKENS)
            if self.base_url == CN_BASE_URL
            else requested_max
        )
        self.last_response_meta: dict[str, Any] = {}

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"}

    def _call(self, method: str, url: str, payload: dict[str, Any] | None = None):
        if self._http:
            return self._http(method, url, self._headers(), payload)
        import requests
        try:
            response = requests.request(
                method, url, headers=self._headers(), json=payload, timeout=60
            )
        except requests.Timeout as exc:
            raise ProviderError("timeout", "MiniMax 呼叫逾時") from exc
        except requests.RequestException as exc:
            raise ProviderError("unreachable", "MiniMax 網路連線失敗") from exc
        try:
            body = response.json()
        except Exception:
            body = {}
        return response.status_code, body

    @staticmethod
    def _provider_code(body: Any) -> str | None:
        """Extract only a bounded provider code; never return response text."""
        if not isinstance(body, dict):
            return None
        candidates = [
            body.get("code"),
            (body.get("error") or {}).get("code")
            if isinstance(body.get("error"), dict) else None,
            (body.get("base_resp") or {}).get("status_code")
            if isinstance(body.get("base_resp"), dict) else None,
        ]
        for value in candidates:
            if isinstance(value, (str, int)):
                code = str(value).strip()
                if code and len(code) <= 64:
                    return code
        return None

    def _raise_http_error(self, status: int, body: Any) -> None:
        code = self._provider_code(body)
        suffix = f"，provider code={code}" if code else ""
        if status in (401, 403):
            raise ProviderError("auth", f"MiniMax API key 無效或無權限{suffix}")
        if status == 429:
            raise ProviderError("rate_limit", f"MiniMax 額度或頻率限制{suffix}")
        if status in (408, 425, 500, 502, 503, 504):
            raise ProviderError("unreachable", f"MiniMax 暫時無法服務（HTTP {status}）{suffix}")
        if status in (400, 404, 409, 422):
            raise ProviderError("invalid_request", f"MiniMax 拒絕此請求（HTTP {status}）{suffix}")
        raise ProviderError("unknown", f"MiniMax 即時呼叫失敗（HTTP {status}）{suffix}")

    def validate_credentials(self) -> dict[str, Any]:
        """Free key verification via GET /v1/models (no paid generation)."""
        status, body = self._call("GET", self.models_url)
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
        """Call the OpenAI-compatible endpoint for deterministic correction."""
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "thinking": {"type": "disabled"},
            "reasoning_split": True,
            "max_completion_tokens": self.max_completion_tokens,
        }
        status, body = self._call("POST", self.chat_url, payload)
        if status != 200:
            self._raise_http_error(status, body)
        try:
            choice = body["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("invalid_response", "MiniMax 回應格式異常") from exc
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("invalid_response", "MiniMax 回應內容非文字或空白")

        finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
        if finish_reason is not None and finish_reason != "stop":
            if finish_reason == "length":
                raise ProviderError("output_limit", "MiniMax 輸出達 token 上限，拒絕採用截斷結果")
            raise ProviderError("invalid_response", f"MiniMax 非正常結束：{finish_reason}")

        usage = body.get("usage") if isinstance(body, dict) else None
        if usage is not None and not isinstance(usage, dict):
            raise ProviderError("invalid_response", "MiniMax usage metadata 格式異常")
        self.last_response_meta = {
            "finish_reason": finish_reason,
            "usage": usage,
        }
        return content

    # Batch intentionally NOT implemented — capability reports unsupported.
