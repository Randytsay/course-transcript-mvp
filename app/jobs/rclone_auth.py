"""Keep rclone's Google Drive access usable with a read-only config mount.

The production rclone config is intentionally mounted read-only.  rclone can
therefore not persist an OAuth refresh to that file, so every subprocess gets
an access token refreshed in memory through ``RCLONE_DRIVE_TOKEN`` instead.
No credential is written to disk, logged, or put in a command-line argument.
"""
from __future__ import annotations

import configparser
import json
import logging
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from urllib.request import Request, urlopen


LOGGER = logging.getLogger(__name__)
_TOKEN_LOCK = threading.Lock()
_TOKEN_CACHE: tuple[str, float] | None = None
_REFRESH_MARGIN_SECONDS = 300


def _config_path() -> str:
    return os.environ.get("RCLONE_CONFIG", "/run/secrets/rclone.conf")


def _remote_name() -> str:
    return os.environ.get("COURSE_TRANSCRIPT_DRIVE_REMOTE", "gdrive").strip() or "gdrive"


def _expiry_timestamp(value: object) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _load_config() -> tuple[dict[str, object], str, str, str] | None:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with open(_config_path(), encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, configparser.Error):
        return None
    remote = _remote_name()
    if not parser.has_section(remote):
        return None
    section = parser[remote]
    try:
        token = json.loads(section.get("token", ""))
    except json.JSONDecodeError:
        return None
    if not isinstance(token, dict):
        return None
    client_id = section.get("client_id", "").strip()
    client_secret = section.get("client_secret", "").strip()
    refresh_token = str(token.get("refresh_token", "")).strip()
    if not client_id or not client_secret or not refresh_token:
        return None
    return token, client_id, client_secret, refresh_token


def _safe_token_payload(access_token: str, token_type: object, expiry: float) -> dict[str, str]:
    return {
        "access_token": access_token,
        "token_type": str(token_type or "Bearer"),
        "expiry": datetime.fromtimestamp(expiry, UTC).isoformat(),
    }


def _refresh_token(
    *, client_id: str, client_secret: str, refresh_token: str
) -> tuple[str, float]:
    body = urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    request = Request(
        "https://oauth2.googleapis.com/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            payload = json.load(response)
    except Exception as exc:  # pragma: no cover - provider/network-specific
        raise RuntimeError("Google Drive OAuth refresh failed") from exc
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise RuntimeError("Google Drive OAuth refresh returned no access token")
    try:
        expires_in = max(60, int(payload.get("expires_in", 3600)))
    except (TypeError, ValueError):
        expires_in = 3600
    expiry = time.time() + expires_in
    return json.dumps(
        _safe_token_payload(str(payload["access_token"]), payload.get("token_type"), expiry),
        separators=(",", ":"),
    ), expiry


def _token_json() -> str | None:
    global _TOKEN_CACHE
    loaded = _load_config()
    if loaded is None:
        return None
    token, client_id, client_secret, refresh_token = loaded
    now = time.time()
    with _TOKEN_LOCK:
        if _TOKEN_CACHE is not None and _TOKEN_CACHE[1] - now > _REFRESH_MARGIN_SECONDS:
            return _TOKEN_CACHE[0]
        access_token = str(token.get("access_token", "")).strip()
        expiry = _expiry_timestamp(token.get("expiry"))
        if access_token and expiry is not None and expiry - now > _REFRESH_MARGIN_SECONDS:
            value = json.dumps(
                _safe_token_payload(access_token, token.get("token_type"), expiry),
                separators=(",", ":"),
            )
            _TOKEN_CACHE = (value, expiry)
            return value
        value, expiry = _refresh_token(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
        )
        _TOKEN_CACHE = (value, expiry)
        return value


def rclone_environment() -> dict[str, str]:
    """Return a subprocess environment with a refreshed Drive token.

    Missing local configuration is deliberately non-fatal so unit tests and
    non-Drive commands retain their normal environment.  rclone will report a
    normal, redacted command error if a real Drive operation cannot authenticate.
    """
    environment = os.environ.copy()
    try:
        token = _token_json()
    except RuntimeError:
        LOGGER.warning("Google Drive OAuth refresh unavailable; using mounted token")
        return environment
    if token:
        environment["RCLONE_DRIVE_TOKEN"] = token
    return environment


def _reset_token_cache() -> None:
    """Reset process-local state for tests and controlled worker reloads."""
    global _TOKEN_CACHE
    with _TOKEN_LOCK:
        _TOKEN_CACHE = None
