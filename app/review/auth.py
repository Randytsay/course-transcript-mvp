"""Reviewer authentication boundary for Google and LINE Login.

Provider credentials and tokens remain server-side. Browsers receive only an
opaque HttpOnly session cookie plus a per-session CSRF value exposed by `/me`.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from .auth_store import ReviewAuthError, ReviewAuthExpired, ReviewAuthStore
from .store import ReviewConflict, ReviewNotFound, ReviewStore

router = APIRouter(prefix="/api/v1/review/auth", tags=["review-auth"])
DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
COOKIE_NAME = "review_session"
HTTP_TIMEOUT_SECONDS = 10

_review_store_cache: tuple[Path, ReviewStore] | None = None
_auth_store_cache: tuple[Path, ReviewAuthStore] | None = None


class AuthStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["login", "link"] = "login"
    return_to: str = Field(default="/review", min_length=1, max_length=512)


def _database_path() -> Path:
    return DATA_DIR / "course-transcript.db"


def _review_store() -> ReviewStore:
    global _review_store_cache
    path = _database_path()
    if _review_store_cache is None or _review_store_cache[0] != path:
        _review_store_cache = (path, ReviewStore(path))
    return _review_store_cache[1]


def _auth_store() -> ReviewAuthStore:
    global _auth_store_cache
    path = _database_path()
    # Initialize ReviewStore first because auth tables have review_users FKs.
    _review_store()
    if _auth_store_cache is None or _auth_store_cache[0] != path:
        _auth_store_cache = (path, ReviewAuthStore(path))
    return _auth_store_cache[1]


def _public_origin() -> str:
    value = (
        os.environ.get("REVIEW_PUBLIC_ORIGIN")
        or os.environ.get("COURSE_TRANSCRIPT_PUBLIC_ORIGIN")
        or "http://localhost:3000"
    ).strip().rstrip("/")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path:
        raise RuntimeError("REVIEW_PUBLIC_ORIGIN must be an origin without a path")
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("Reviewer OAuth requires HTTPS outside localhost")
    return value


def _redirect_uri(provider: str) -> str:
    return f"{_public_origin()}/api/v1/review/auth/{provider}/callback"


def _provider_credentials(provider: str) -> tuple[str, str]:
    if provider == "google":
        client_id = os.environ.get("REVIEW_GOOGLE_CLIENT_ID", "").strip()
        secret = os.environ.get("REVIEW_GOOGLE_CLIENT_SECRET", "").strip()
    elif provider == "line":
        client_id = os.environ.get("REVIEW_LINE_CHANNEL_ID", "").strip()
        secret = os.environ.get("REVIEW_LINE_CHANNEL_SECRET", "").strip()
    else:
        raise HTTPException(status_code=404, detail="Unknown login provider")
    if not client_id or not secret:
        raise HTTPException(status_code=503, detail=f"{provider} login is not configured")
    return client_id, secret


def _provider_configured(provider: str) -> bool:
    try:
        _provider_credentials(provider)
        return True
    except HTTPException:
        return False


def _validate_origin(request: Request) -> None:
    origin = request.headers.get("Origin", "").rstrip("/")
    if origin != _public_origin():
        raise HTTPException(status_code=403, detail="Invalid request origin")


def _csrf_for_token(token: str) -> str:
    digest = hmac.new(
        token.encode("utf-8"),
        b"course-transcript-review-csrf-v1",
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _session_token(request: Request) -> str:
    return request.cookies.get(COOKIE_NAME, "").strip()


def _require_session(request: Request, *, mutation: bool = False) -> dict[str, Any]:
    token = _session_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Reviewer login required")
    try:
        session = _auth_store().get_session(token)
    except (ReviewAuthError, ReviewAuthExpired) as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ReviewConflict as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if mutation:
        _validate_origin(request)
        supplied = request.headers.get("X-Review-CSRF", "")
        if not supplied or not hmac.compare_digest(supplied, _csrf_for_token(token)):
            raise HTTPException(status_code=403, detail="Invalid reviewer CSRF token")
    return session


def require_reviewer_session(request: Request, *, mutation: bool = False) -> dict[str, Any]:
    """Public helper for future reviewer APIs."""
    return _require_session(request, mutation=mutation)


def _set_session_cookie(response: RedirectResponse | JSONResponse, token: str, max_age: int) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=_public_origin().startswith("https://"),
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: JSONResponse) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/", samesite="lax")


def _authorization_url(provider: str, flow: dict[str, Any]) -> str:
    client_id, _ = _provider_credentials(provider)
    common = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": _redirect_uri(provider),
        "state": flow["state"],
        "nonce": flow["nonce"],
        "code_challenge": flow["code_challenge"],
        "code_challenge_method": "S256",
    }
    if provider == "google":
        endpoint = "https://accounts.google.com/o/oauth2/v2/auth"
        common.update({"scope": "openid email profile", "prompt": "select_account"})
    elif provider == "line":
        endpoint = "https://access.line.me/oauth2/v2.1/authorize"
        common.update({"scope": "openid profile email"})
    else:
        raise HTTPException(status_code=404, detail="Unknown login provider")
    return f"{endpoint}?{urllib.parse.urlencode(common)}"


def _post_form(url: str, payload: dict[str, str]) -> dict[str, Any]:
    data = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        # Do not leak provider token responses into the public error body.
        raise ReviewAuthError(f"Identity provider rejected the request ({exc.code})") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ReviewAuthError("Identity provider is temporarily unavailable") from exc
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ReviewAuthError("Identity provider returned an invalid response") from exc
    if not isinstance(parsed, dict):
        raise ReviewAuthError("Identity provider returned an invalid response")
    return parsed


def _exchange_code(provider: str, *, code: str, flow: dict[str, Any]) -> dict[str, Any]:
    client_id, secret = _provider_credentials(provider)
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _redirect_uri(provider),
        "client_id": client_id,
        "client_secret": secret,
        "code_verifier": str(flow["code_verifier"]),
    }
    endpoint = (
        "https://oauth2.googleapis.com/token"
        if provider == "google"
        else "https://api.line.me/oauth2/v2.1/token"
    )
    tokens = _post_form(endpoint, payload)
    if not isinstance(tokens.get("id_token"), str):
        raise ReviewAuthError("Identity provider did not return an ID token")
    return tokens


def _verify_google_id_token(id_token_value: str, *, client_id: str, nonce: str) -> dict[str, Any]:
    try:
        from google.auth.transport.requests import Request as GoogleRequest
        from google.oauth2 import id_token as google_id_token

        claims = google_id_token.verify_oauth2_token(
            id_token_value,
            GoogleRequest(),
            audience=client_id,
        )
    except Exception as exc:  # google-auth raises several verification subclasses.
        raise ReviewAuthError("Google ID token verification failed") from exc
    if claims.get("nonce") != nonce:
        raise ReviewAuthError("Google login nonce mismatch")
    if not claims.get("sub"):
        raise ReviewAuthError("Google identity is missing a subject")
    return dict(claims)


def _verify_line_id_token(id_token_value: str, *, client_id: str, nonce: str) -> dict[str, Any]:
    claims = _post_form(
        "https://api.line.me/oauth2/v2.1/verify",
        {"id_token": id_token_value, "client_id": client_id},
    )
    if claims.get("nonce") != nonce:
        raise ReviewAuthError("LINE login nonce mismatch")
    if claims.get("aud") != client_id or not claims.get("sub"):
        raise ReviewAuthError("LINE ID token audience or subject is invalid")
    return claims


def _identity_from_code(provider: str, *, code: str, flow: dict[str, Any]) -> dict[str, str | None]:
    client_id, _ = _provider_credentials(provider)
    tokens = _exchange_code(provider, code=code, flow=flow)
    raw_id_token = str(tokens["id_token"])
    claims = (
        _verify_google_id_token(raw_id_token, client_id=client_id, nonce=str(flow["nonce"]))
        if provider == "google"
        else _verify_line_id_token(raw_id_token, client_id=client_id, nonce=str(flow["nonce"]))
    )
    subject = str(claims.get("sub", "")).strip()
    email = str(claims.get("email", "")).strip() or None
    display_name = str(claims.get("name", "")).strip() or email or f"{provider} reviewer"
    picture = str(claims.get("picture", "")).strip() or None
    return {
        "subject": subject,
        "email": email,
        "display_name": display_name,
        "avatar_url": picture,
    }


def _identity_summaries(user_id: str) -> list[dict[str, Any]]:
    with _review_store().connect() as connection:
        rows = connection.execute(
            """
            SELECT provider, email, created_at, last_login_at
            FROM review_auth_identities
            WHERE user_id = ? ORDER BY provider
            """,
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


@router.get("/providers")
def providers() -> dict[str, Any]:
    return {
        "providers": {
            "google": {"configured": _provider_configured("google")},
            "line": {"configured": _provider_configured("line")},
        },
        "session_cookie": {"http_only": True, "same_site": "lax"},
    }


@router.post("/{provider}/start")
def start_auth(provider: str, payload: AuthStartRequest, request: Request) -> dict[str, str]:
    provider = provider.lower()
    _provider_credentials(provider)
    _validate_origin(request)
    user_id: str | None = None
    if payload.action == "link":
        user_id = str(_require_session(request, mutation=True)["user_id"])
    elif _session_token(request):
        try:
            _require_session(request)
        except HTTPException:
            pass
        else:
            raise HTTPException(status_code=409, detail="Already logged in; use link to add another provider")
    try:
        flow = _auth_store().create_oauth_flow(
            provider=provider,
            action=payload.action,
            user_id=user_id,
            return_to=payload.return_to,
        )
    except (ValueError, ReviewConflict, ReviewNotFound) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"authorization_url": _authorization_url(provider, flow)}


@router.get("/{provider}/callback")
def auth_callback(
    provider: str,
    request: Request,
    state: str = "",
    code: str = "",
    error: str = "",
) -> RedirectResponse:
    provider = provider.lower()
    _provider_credentials(provider)
    if error:
        raise HTTPException(status_code=401, detail=f"{provider} login was not completed")
    if not state or not code:
        raise HTTPException(status_code=422, detail="OAuth callback is incomplete")
    try:
        flow = _auth_store().consume_oauth_flow(provider=provider, state=state)
        identity = _identity_from_code(provider, code=code, flow=flow)
        if flow["action"] == "link":
            session = _require_session(request)
            if str(session["user_id"]) != str(flow["user_id"]):
                raise ReviewAuthError("Link flow session changed")
            _review_store().link_identity(
                user_id=str(flow["user_id"]),
                provider=provider,
                provider_subject=str(identity["subject"]),
                email=identity["email"],
            )
            return RedirectResponse(url=str(flow["return_to"]), status_code=303)

        user = _review_store().get_or_create_user_for_identity(
            provider=provider,
            provider_subject=str(identity["subject"]),
            display_name=str(identity["display_name"]),
            email=identity["email"],
            avatar_url=identity["avatar_url"],
        )
        created = _auth_store().create_session(user_id=str(user["id"]))
        response = RedirectResponse(url=str(flow["return_to"]), status_code=303)
        max_age = max(1, int(os.environ.get("REVIEW_SESSION_TTL_DAYS", "30"))) * 86400
        _set_session_cookie(response, created["token"], max_age=max_age)
        return response
    except ReviewAuthExpired as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ReviewAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ReviewConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/me")
def me(request: Request) -> dict[str, Any]:
    session = _require_session(request)
    token = _session_token(request)
    return {
        "user": {
            "id": session["user_id"],
            "display_name": session["display_name"],
            "avatar_url": session["avatar_url"],
            "role": session["role"],
        },
        "identities": _identity_summaries(str(session["user_id"])),
        "csrf_token": _csrf_for_token(token),
        "session_expires_at": session["expires_at"],
    }


@router.post("/logout")
def logout(request: Request) -> JSONResponse:
    _require_session(request, mutation=True)
    token = _session_token(request)
    _auth_store().revoke_session(token)
    response = JSONResponse({"logged_out": True})
    _clear_session_cookie(response)
    return response
