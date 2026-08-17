"""Publish an immutable review subtitle version back to the existing YouTube track."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from .youtube_import import HTTP_TIMEOUT_SECONDS, YouTubeImportError, _owner_access_token

UPLOAD_ENDPOINT = "https://www.googleapis.com/upload/youtube/v3/captions"
MAX_CAPTION_BYTES = 100 * 1024 * 1024


class YouTubePublishError(RuntimeError):
    pass


def _multipart_body(*, caption_track_id: str, srt_text: str, boundary: str) -> bytes:
    metadata = json.dumps({"id": caption_track_id}, ensure_ascii=False).encode("utf-8")
    media = srt_text.encode("utf-8")
    if len(media) > MAX_CAPTION_BYTES:
        raise YouTubePublishError("Caption file exceeds YouTube's 100 MB upload limit")
    parts = [
        f"--{boundary}\r\n".encode(),
        b"Content-Type: application/json; charset=UTF-8\r\n\r\n",
        metadata,
        b"\r\n",
        f"--{boundary}\r\n".encode(),
        b"Content-Type: application/octet-stream\r\n\r\n",
        media,
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    return b"".join(parts)


def _send_caption_update(
    *,
    caption_track_id: str,
    srt_text: str,
    access_token: str,
) -> dict[str, Any]:
    boundary = f"review-caption-{uuid.uuid4().hex}"
    body = _multipart_body(
        caption_track_id=caption_track_id,
        srt_text=srt_text,
        boundary=boundary,
    )
    query = urllib.parse.urlencode({"part": "id", "uploadType": "multipart"})
    request = urllib.request.Request(
        f"{UPLOAD_ENDPOINT}?{query}",
        data=body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": f"multipart/related; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
        method="PUT",
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:1200]
        except OSError:
            detail = ""
        if exc.code in {401, 403}:
            raise YouTubePublishError(
                "YouTube owner authorization is not allowed to update this caption track"
            ) from exc
        if exc.code == 404:
            raise YouTubePublishError("YouTube caption track no longer exists") from exc
        raise YouTubePublishError(
            f"YouTube caption update failed ({exc.code}){': ' + detail if detail else ''}"
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise YouTubePublishError("YouTube caption update endpoint is unavailable") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise YouTubePublishError("YouTube caption update returned invalid JSON") from exc
    if not isinstance(payload, dict) or str(payload.get("id") or "") != caption_track_id:
        raise YouTubePublishError("YouTube caption update returned an unexpected track response")
    return payload


def publish_caption_version(*, caption_track_id: str, srt_text: str) -> dict[str, Any]:
    try:
        access_token = _owner_access_token()
    except YouTubeImportError as exc:
        raise YouTubePublishError(str(exc)) from exc
    return _send_caption_update(
        caption_track_id=caption_track_id,
        srt_text=srt_text,
        access_token=access_token,
    )
