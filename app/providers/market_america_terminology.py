"""Read-only Market America terminology snapshot for one transcript job."""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import socket
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
JOB = DATA_DIR / "jobs" / os.environ.get("JOB_NAME", "voice_11386603-seg1")
OUTPUT = JOB / "market-america-terminology.json"
MAX_QUERY_CHARS = 12000
DEFAULT_TOKEN_FILE = DATA_DIR / "integrations" / "shopclaw-ma-terminology.token"
DEFAULT_SOCKET_FILE = DATA_DIR / "integrations" / "shopclaw-ma-terminology.sock"
DEFAULT_ENDPOINT = "https://shopclaw.linebot.ccwu.cc/internal/ma-product-terminology"


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_query() -> str:
    payload = json.loads((JOB / "subtitles.json").read_text(encoding="utf-8"))
    segments = payload.get("segments", []) if isinstance(payload, dict) else []
    text = "\n".join(
        str(item.get("raw_text") or item.get("text") or "").strip()
        for item in segments
        if isinstance(item, dict)
    ).strip()
    return text[:MAX_QUERY_CHARS]


def _safe_products(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("ok") is not True or payload.get("scope") != "market_america":
        raise RuntimeError("invalid_market_america_terminology_response")
    result: list[dict[str, Any]] = []
    for product in payload.get("products", []):
        if not isinstance(product, dict):
            continue
        facts = []
        for fact in product.get("facts", []):
            if not isinstance(fact, dict):
                continue
            kind = str(fact.get("kind") or "")
            text = str(fact.get("text") or "").strip()
            if kind not in {"ingredient", "usage", "feature", "approved_claim", "micro_knowledge"} or not text:
                continue
            facts.append({"kind": kind, "text": text[:1200], "fact_id": fact.get("fact_id")})
        result.append({
            "sku": str(product.get("sku") or "")[:120],
            "name": str(product.get("name") or "")[:300],
            "aliases": [str(v)[:300] for v in product.get("aliases", []) if str(v).strip()][:20],
            "knowledge_product_id": str(product.get("knowledge_product_id") or "")[:120],
            "facts": facts[:6],
        })
    return result[:12]


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: Path, *, timeout: float = 8.0) -> None:
        super().__init__("localhost", timeout=timeout)
        self.socket_path = str(socket_path)

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


def _request_remote(*, endpoint: str, token: str, socket_file: Path, body: bytes) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if socket_file.is_socket():
        conn = _UnixHTTPConnection(socket_file, timeout=8)
        try:
            conn.request("POST", "/internal/ma-product-terminology", body=body, headers=headers)
            response = conn.getresponse()
            raw = response.read()
            if response.status != 200:
                raise RuntimeError(f"shopclaw_terminology_http_{response.status}")
            return json.loads(raw.decode("utf-8"))
        finally:
            conn.close()
    request = urllib.request.Request(endpoint, data=body, method="POST", headers=headers)
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


def build_snapshot() -> dict[str, Any]:
    mode = os.environ.get("CONTENT_MODE", "").strip().lower()
    if mode != "market_america_training":
        payload = {"schema_version": 1, "generated_at": _iso(), "status": "not_applicable", "content_mode": mode, "products": []}
        OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    endpoint = os.environ.get("SHOPCLAW_MA_TERMINOLOGY_URL", DEFAULT_ENDPOINT).strip()
    token = os.environ.get("SHOPCLAW_MA_KNOWLEDGE_TOKEN", "").strip()
    token_file = Path(os.environ.get("SHOPCLAW_MA_TERMINOLOGY_TOKEN_FILE", str(DEFAULT_TOKEN_FILE)))
    socket_file = Path(os.environ.get("SHOPCLAW_MA_TERMINOLOGY_SOCKET", str(DEFAULT_SOCKET_FILE)))
    if not token and token_file.is_file():
        try:
            if token_file.stat().st_mode & 0o077:
                raise RuntimeError("terminology_token_file_permissions")
            token = token_file.read_text(encoding="utf-8").strip()
        except OSError:
            token = ""
    query = _load_query()
    base = {
        "schema_version": 1,
        "generated_at": _iso(),
        "content_mode": mode,
        "query_sha256": _sha256_text(query),
        "query_chars": len(query),
        "policy": "read_only_spelling_evidence_only_no_claim_injection",
    }
    if not endpoint or len(token) < 32:
        payload = {**base, "status": "unavailable", "reason": "shopclaw_terminology_not_configured", "products": []}
        OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    body = json.dumps({"query": query}, ensure_ascii=False).encode("utf-8")
    try:
        remote = _request_remote(endpoint=endpoint, token=token, socket_file=socket_file, body=body)
        products = _safe_products(remote)
        payload = {**base, "status": "ready", "products": products}
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, RuntimeError, http.client.HTTPException) as exc:
        payload = {**base, "status": "unavailable", "reason": type(exc).__name__, "products": []}
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def instruction_text() -> str:
    if not OUTPUT.is_file():
        return ""
    try:
        payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if payload.get("status") != "ready":
        return ""
    lines = [
        "ShopClaw Market America terminology evidence follows. Use it only to correct product names, aliases, ingredient/material names, and other clearly spoken terminology.",
        "Never add a product, ingredient, dosage, efficacy, health, income, or business-performance claim that is not supported by the transcript audio/context.",
    ]
    for product in payload.get("products", []):
        identity = " | ".join(v for v in [product.get("sku"), product.get("name")] if v)
        if identity:
            lines.append("Product: " + identity)
        aliases = product.get("aliases") or []
        if aliases:
            lines.append("Aliases: " + ", ".join(aliases))
        for fact in product.get("facts", []):
            if fact.get("kind") == "ingredient":
                lines.append("Approved ingredient evidence: " + str(fact.get("text") or ""))
    return "\n".join(lines)


def main() -> int:
    payload = build_snapshot()
    print("MA_TERMINOLOGY=" + str(payload.get("status")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
