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
- provider-confirmed content moderation rejection is a distinct, non-retryable
  ``content_rejected`` kind rather than a request-schema failure;
- content rejection classification requires both a specific policy marker and
  bounded structural provider evidence, so generic auth/permission messages
  containing words such as ``safety`` cannot be reclassified by substring alone;
- HTTP validation failures expose only bounded structural metadata such as
  provider code / parameter / validation location, never arbitrary messages;
- when finish/usage metadata is present it is validated before content is
  accepted;
- runtime base URLs are restricted to MiniMax's fixed CN/global API hosts.
"""
from __future__ import annotations

import re
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
_ALLOWED_API_HOSTS = frozenset({"api.minimax.io", "api.minimaxi.com"})
_SAFE_ATOM_RE = re.compile(r"^[A-Za-z0-9_.:\-\[\]]{1,64}$")
# Keep these markers specific to moderation/content-policy rejection. Generic
# words such as "safety", "sensitive", and "敏感" also appear in auth,
# gateway, and permission errors and therefore must not classify by themselves.
_CONTENT_REJECTION_MARKERS = (
    "content moderation",
    "moderation",
    "content policy",
    "prohibited",
    "内容安全",
    "内容审核",
    "审核拒绝",
    "違規",
    "违规",
    "风控拒绝",
)
_CONTENT_REJECTION_HTTP_STATUSES = frozenset({400, 403, 422})


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


def _safe_atom(value: Any) -> str | None:
    """Return a bounded identifier-like value, never arbitrary provider text."""
    if not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    return text if _SAFE_ATOM_RE.fullmatch(text) else None


def _safe_loc(value: Any) -> str | None:
    """Normalize validation locations without accepting arbitrary content."""
    if isinstance(value, (list, tuple)):
        atoms: list[str] = []
        for item in value[:8]:
            atom = _safe_atom(item)
            if atom is None:
                return None
            atoms.append(atom)
        text = ".".join(atoms)
        return text[:160] if text else None
    return _safe_atom(value)


def _known_message_candidates(body: Any) -> list[str]:
    """Read provider messages only for categorization; callers never expose them."""
    if not isinstance(body, dict):
        return []
    values: list[Any] = [body.get("message")]
    error = body.get("error")
    if isinstance(error, dict):
        values.append(error.get("message"))
    base_resp = body.get("base_resp")
    if isinstance(base_resp, dict):
        values.append(base_resp.get("status_msg"))
    detail = body.get("detail")
    if isinstance(detail, str):
        values.append(detail)
    elif isinstance(detail, list):
        for item in detail[:4]:
            if isinstance(item, dict):
                values.append(item.get("msg"))
                values.append(item.get("message"))
    return [value for value in values if isinstance(value, str)]


def _has_content_rejection_structure(body: Any) -> bool:
    """Require bounded provider structure in addition to message markers.

    Provider error ``type`` fields and ``base_resp.status_code`` are useful
    corroboration because they are structural fields rather than arbitrary
    message prose. We deliberately require at least one of these before a
    message marker may classify an error as ``content_rejected``.
    """
    if not isinstance(body, dict):
        return False

    error = body.get("error")
    if isinstance(error, dict) and _safe_atom(error.get("type")) is not None:
        return True
    if _safe_atom(body.get("type")) is not None:
        return True

    base_resp = body.get("base_resp")
    if (
        isinstance(base_resp, dict)
        and _safe_atom(base_resp.get("status_code")) is not None
    ):
        return True
    return False


def _is_content_rejection(body: Any) -> bool:
    if not _has_content_rejection_structure(body):
        return False
    for message in _known_message_candidates(body):
        lowered = message.lower()
        if any(marker in lowered for marker in _CONTENT_REJECTION_MARKERS):
            return True
    return False


def _safe_request_metadata(body: Any) -> list[str]:
    """Extract only allow-listed structural diagnostics from an error body."""
    if not isinstance(body, dict):
        return []
    result: list[str] = []

    def add(label: str, value: Any) -> None:
        atom = _safe_atom(value)
        if atom is not None:
            entry = f"{label}={atom}"
            if entry not in result:
                result.append(entry)

    error = body.get("error")
    if isinstance(error, dict):
        add("error_type", error.get("type"))
        add("param", error.get("param"))

    add("error_type", body.get("type"))
    add("param", body.get("param"))

    detail = body.get("detail")
    if isinstance(detail, list):
        for item in detail[:4]:
            if not isinstance(item, dict):
                continue
            loc = _safe_loc(item.get("loc"))
            kind = _safe_atom(item.get("type"))
            parts = []
            if loc:
                parts.append(f"loc:{loc}")
            if kind:
                parts.append(f"type:{kind}")
            if parts:
                entry = "validation=" + ",".join(parts)
                if entry not in result:
                    result.append(entry)

    if _is_content_rejection(body):
        result.append("category=content_rejected")

    return result[:6]


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
        self.max_completion_tokens = max(256, int(max_completion_tokens))
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
            code = _safe_atom(value)
            if code:
                return code
        return None

    def _raise_http_error(self, status: int, body: Any) -> None:
        safe_parts: list[str] = []
        code = self._provider_code(body)
        if code:
            safe_parts.append(f"provider code={code}")
        safe_parts.extend(_safe_request_metadata(body))
        suffix = "，" + "，".join(safe_parts) if safe_parts else ""

        # A provider-confirmed policy rejection is not a malformed request.
        # Treat it as a distinct, non-retryable window outcome so callers can
        # preserve raw Chirp without attempting to evade moderation by retrying,
        # splitting, rewriting, or changing endpoints/models.
        if status in _CONTENT_REJECTION_HTTP_STATUSES and _is_content_rejection(body):
            raise ProviderError(
                "content_rejected",
                f"MiniMax 內容審查拒絕請求（HTTP {status}）{suffix}",
            )
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
