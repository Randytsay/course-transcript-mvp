"""Owner-only YouTube playlist/caption import for the reviewer workspace.

Reviewer Google/LINE sessions are deliberately not accepted here. The route uses
existing Cloudflare Access operator identity and a separate channel-owner Google
OAuth refresh token with YouTube caption-management scope.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api import _mutation_actor
from app.subtitles.editor_hardened import parse_srt_strict

from .store import ReviewConflict, ReviewStore

router = APIRouter(prefix="/api/v1/review-admin/youtube", tags=["review-youtube-import"])
DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
HTTP_TIMEOUT_SECONDS = 15


class YouTubeImportError(RuntimeError):
    pass


class YouTubeSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    playlist_id: str | None = Field(default=None, min_length=1, max_length=128)
    apply: bool = False
    max_videos: int = Field(default=50, ge=1, le=50)
    youtube_video_ids: list[str] | None = Field(default=None, max_length=50)


def _store() -> ReviewStore:
    return ReviewStore(DATA_DIR / "course-transcript.db")


def _read_refresh_token() -> str:
    direct = os.environ.get("YOUTUBE_OWNER_REFRESH_TOKEN", "").strip()
    if direct:
        return direct
    path = Path(
        os.environ.get(
            "YOUTUBE_OWNER_REFRESH_TOKEN_FILE",
            "/run/secrets/youtube-owner-refresh-token",
        )
    )
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise YouTubeImportError("YouTube owner refresh token is not configured") from exc
    if not raw:
        raise YouTubeImportError("YouTube owner refresh token is empty")
    if raw.startswith("{"):
        try:
            payload = json.loads(raw)
            raw = str(payload.get("refresh_token", "")).strip()
        except (json.JSONDecodeError, AttributeError) as exc:
            raise YouTubeImportError("YouTube owner refresh token file is invalid") from exc
    if not raw:
        raise YouTubeImportError("YouTube owner refresh token is missing")
    return raw


def _credentials() -> tuple[str, str, str]:
    client_id = os.environ.get("YOUTUBE_OWNER_CLIENT_ID", "").strip()
    client_secret = os.environ.get("YOUTUBE_OWNER_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise YouTubeImportError("YouTube owner OAuth client is not configured")
    return client_id, client_secret, _read_refresh_token()


def _form_post(url: str, payload: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise YouTubeImportError(f"Google OAuth token refresh failed ({exc.code})") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise YouTubeImportError("Google OAuth token endpoint is unavailable") from exc
    try:
        result = json.loads(body)
    except json.JSONDecodeError as exc:
        raise YouTubeImportError("Google OAuth token endpoint returned invalid JSON") from exc
    if not isinstance(result, dict) or not isinstance(result.get("access_token"), str):
        raise YouTubeImportError("Google OAuth token refresh returned no access token")
    return result


def _owner_access_token() -> str:
    client_id, client_secret, refresh_token = _credentials()
    result = _form_post(
        GOOGLE_TOKEN_ENDPOINT,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    return str(result["access_token"])


def _authorized_request(
    url: str,
    *,
    access_token: str,
    accept: str = "application/json",
) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": accept,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise YouTubeImportError(
                "YouTube owner authorization cannot access the requested playlist/captions"
            ) from exc
        raise YouTubeImportError(f"YouTube API request failed ({exc.code})") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise YouTubeImportError("YouTube API is temporarily unavailable") from exc


def _get_json(path: str, params: dict[str, str], *, access_token: str) -> dict[str, Any]:
    url = f"{YOUTUBE_API}/{path}?{urllib.parse.urlencode(params)}"
    raw = _authorized_request(url, access_token=access_token)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise YouTubeImportError("YouTube API returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise YouTubeImportError("YouTube API returned an invalid response")
    return payload


def _normalize_video_ids(values: list[str] | None) -> list[str]:
    if not values:
        return []
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        video_id = str(value).strip()
        if not video_id or video_id in seen:
            continue
        seen.add(video_id)
        unique.append(video_id)
    return unique


def _playlist_items(
    *,
    playlist_id: str,
    access_token: str,
    max_videos: int,
    youtube_video_ids: list[str] | None = None,
) -> list[dict[str, str]]:
    requested = _normalize_video_ids(youtube_video_ids)
    requested_set = set(requested)
    if requested and len(requested) > max_videos:
        raise ValueError("youtube_video_ids cannot contain more items than max_videos")

    items: list[dict[str, str]] = []
    found_ids: set[str] = set()
    page_token = ""
    while True:
        if requested_set and requested_set.issubset(found_ids):
            break
        if not requested_set and len(items) >= max_videos:
            break
        params = {
            "part": "snippet,contentDetails,status",
            "playlistId": playlist_id,
            "maxResults": "50" if requested_set else str(min(50, max_videos - len(items))),
        }
        if page_token:
            params["pageToken"] = page_token
        payload = _get_json("playlistItems", params, access_token=access_token)
        for item in payload.get("items", []):
            if not isinstance(item, dict):
                continue
            snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
            details = (
                item.get("contentDetails")
                if isinstance(item.get("contentDetails"), dict)
                else {}
            )
            status = item.get("status") if isinstance(item.get("status"), dict) else {}
            video_id = str(details.get("videoId") or "").strip()
            title = str(snippet.get("title") or "").strip()
            if not video_id or not title or status.get("privacyStatus") == "private":
                continue
            if requested_set and video_id not in requested_set:
                continue
            items.append({"youtube_video_id": video_id, "title": title})
            found_ids.add(video_id)
            if not requested_set and len(items) >= max_videos:
                break
        page_token = str(payload.get("nextPageToken") or "")
        if not page_token:
            break

    if requested:
        by_id = {item["youtube_video_id"]: item for item in items}
        return [by_id[video_id] for video_id in requested if video_id in by_id]
    return items


def _language_preferences() -> list[str]:
    values = os.environ.get(
        "YOUTUBE_REVIEW_CAPTION_LANGUAGES",
        "zh-TW,zh-Hant,zh",
    )
    return [item.strip() for item in values.split(",") if item.strip()]


def _caption_score(item: dict[str, Any], preferences: list[str]) -> tuple[int, int, int, str]:
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    language = str(snippet.get("language") or "")
    try:
        language_rank = preferences.index(language)
    except ValueError:
        language_rank = len(preferences) + 1
        for index, preferred in enumerate(preferences):
            if language.lower().startswith(preferred.lower() + "-"):
                language_rank = index + 1
                break
    track_kind = str(snippet.get("trackKind") or "standard")
    standard_penalty = 0 if track_kind == "standard" else 1
    serving_penalty = 0 if snippet.get("status") == "serving" else 1
    return (
        language_rank,
        standard_penalty,
        serving_penalty,
        str(snippet.get("lastUpdated") or ""),
    )


def _select_caption(video_id: str, *, access_token: str) -> dict[str, Any] | None:
    payload = _get_json(
        "captions",
        {"part": "id,snippet", "videoId": video_id},
        access_token=access_token,
    )
    candidates = [
        item
        for item in payload.get("items", [])
        if isinstance(item, dict)
        and isinstance(item.get("snippet"), dict)
        and not bool(item["snippet"].get("isDraft"))
        and item["snippet"].get("status") != "failed"
    ]
    if not candidates:
        return None
    preferences = _language_preferences()
    candidates.sort(key=lambda item: _caption_score(item, preferences))
    selected = candidates[0]
    snippet = selected["snippet"]
    if preferences and _caption_score(selected, preferences)[0] > len(preferences):
        return None
    return selected


def _download_srt(caption_id: str, *, access_token: str) -> str:
    url = f"{YOUTUBE_API}/captions/{urllib.parse.quote(caption_id)}?tfmt=srt"
    raw = _authorized_request(url, access_token=access_token, accept="text/plain")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise YouTubeImportError("Downloaded YouTube caption is not UTF-8 SRT") from exc
    if not text.strip():
        raise YouTubeImportError("Downloaded YouTube caption is empty")
    return text


def _has_segments(store: ReviewStore, video_id: str) -> bool:
    with store.connect() as connection:
        row = connection.execute(
            """
            SELECT 1 FROM review_subtitle_segments
            WHERE youtube_video_id = ? LIMIT 1
            """,
            (video_id,),
        ).fetchone()
    return row is not None


def sync_playlist(
    *,
    playlist_id: str,
    access_token: str,
    max_videos: int,
    apply: bool,
    actor: str,
    youtube_video_ids: list[str] | None = None,
) -> dict[str, Any]:
    requested = _normalize_video_ids(youtube_video_ids)
    if requested and len(requested) > max_videos:
        raise ValueError("youtube_video_ids cannot contain more items than max_videos")
    store = _store()
    playlist = _playlist_items(
        playlist_id=playlist_id,
        access_token=access_token,
        max_videos=max_videos,
        youtube_video_ids=requested or None,
    )
    returned_ids = {item["youtube_video_id"] for item in playlist}
    missing_requested = [video_id for video_id in requested if video_id not in returned_ids]

    results: list[dict[str, Any]] = []
    imported = 0
    skipped_existing = 0
    missing_caption = 0
    failed = 0

    for video in playlist:
        video_id = video["youtube_video_id"]
        if _has_segments(store, video_id):
            skipped_existing += 1
            results.append(
                {
                    **video,
                    "status": "existing",
                    "note": "review segments already exist; initial sync never overwrites them",
                }
            )
            continue
        try:
            caption = _select_caption(video_id, access_token=access_token)
            if caption is None:
                missing_caption += 1
                results.append({**video, "status": "no_matching_caption"})
                continue
            snippet = caption["snippet"]
            summary = {
                **video,
                "caption_track_id": str(caption.get("id") or ""),
                "caption_language": str(snippet.get("language") or ""),
                "caption_name": str(snippet.get("name") or "") or None,
                "caption_track_kind": str(snippet.get("trackKind") or "standard"),
            }
            if not apply:
                results.append({**summary, "status": "ready"})
                continue

            srt = _download_srt(summary["caption_track_id"], access_token=access_token)
            parsed, stats = parse_srt_strict(srt)
            segments = [
                {
                    "segment_index": index,
                    "start_ms": int(item["start_ms"]),
                    "end_ms": int(item["end_ms"]),
                    "text": str(item["text"]),
                }
                for index, item in enumerate(parsed, 1)
            ]
            duration_ms = max(item["end_ms"] for item in segments)
            store.upsert_video(
                youtube_video_id=video_id,
                playlist_id=playlist_id,
                title=video["title"],
                duration_ms=duration_ms,
                caption_track_id=summary["caption_track_id"],
                caption_language=summary["caption_language"] or "zh-TW",
                caption_name=summary["caption_name"],
            )
            store.import_subtitle_segments(
                youtube_video_id=video_id,
                segments=segments,
            )
            imported += 1
            results.append(
                {
                    **summary,
                    "status": "imported",
                    "segment_count": len(segments),
                    "duration_ms": duration_ms,
                    "parse_stats": stats,
                }
            )
        except (YouTubeImportError, ReviewConflict, ValueError, HTTPException) as exc:
            failed += 1
            detail = getattr(exc, "detail", str(exc))
            results.append(
                {
                    **video,
                    "status": "failed",
                    "error": detail if isinstance(detail, str) else "caption parse rejected",
                }
            )

    return {
        "playlist_id": playlist_id,
        "apply": apply,
        "actor": actor,
        "playlist_items": len(playlist),
        "requested_video_ids": requested,
        "missing_requested_video_ids": missing_requested,
        "imported": imported,
        "skipped_existing": skipped_existing,
        "missing_caption": missing_caption,
        "failed": failed,
        "results": results,
    }


@router.post("/sync")
def sync_youtube_playlist(payload: YouTubeSyncRequest, request: Request) -> dict[str, Any]:
    actor = _mutation_actor(request)
    playlist_id = (
        payload.playlist_id
        or os.environ.get("YOUTUBE_REVIEW_PLAYLIST_ID", "").strip()
    )
    if not playlist_id:
        raise HTTPException(status_code=422, detail="YouTube review playlist ID is required")
    requested = _normalize_video_ids(payload.youtube_video_ids)
    if requested and len(requested) > payload.max_videos:
        raise HTTPException(
            status_code=422,
            detail="Selected video count cannot exceed max_videos",
        )
    try:
        access_token = _owner_access_token()
        return sync_playlist(
            playlist_id=playlist_id,
            access_token=access_token,
            max_videos=payload.max_videos,
            apply=payload.apply,
            actor=actor,
            youtube_video_ids=requested or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except YouTubeImportError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
