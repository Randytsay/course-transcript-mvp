from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from app.review.youtube_publish import (
    YouTubePublishError,
    _multipart_body,
    _send_caption_update,
)


class _FakeResponse:
    def __init__(self, payload: dict):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.payload


class YouTubeCaptionPublishTests(unittest.TestCase):
    def test_multipart_body_contains_only_track_metadata_and_frozen_srt_media(self) -> None:
        body = _multipart_body(
            caption_track_id="caption-123",
            srt_text="1\n00:00:00,000 --> 00:00:02,000\n佛告阿難\n",
            boundary="boundary-test",
        )
        decoded = body.decode("utf-8")
        self.assertIn('"id": "caption-123"', decoded)
        self.assertIn("Content-Type: application/json; charset=UTF-8", decoded)
        self.assertIn("Content-Type: application/octet-stream", decoded)
        self.assertIn("佛告阿難", decoded)
        self.assertTrue(decoded.endswith("--boundary-test--\r\n"))

    def test_update_uses_put_upload_endpoint_part_id_and_multipart(self) -> None:
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            captured["content_type"] = request.headers.get("Content-type")
            captured["authorization"] = request.headers.get("Authorization")
            captured["data"] = request.data
            captured["timeout"] = timeout
            return _FakeResponse({"id": "caption-123"})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = _send_caption_update(
                caption_track_id="caption-123",
                srt_text="1\n00:00:00,000 --> 00:00:02,000\n佛告阿難\n",
                access_token="access-token",
            )
        self.assertEqual(result["id"], "caption-123")
        self.assertEqual(captured["method"], "PUT")
        self.assertIn("/upload/youtube/v3/captions?", captured["url"])
        self.assertIn("part=id", captured["url"])
        self.assertIn("uploadType=multipart", captured["url"])
        self.assertTrue(captured["content_type"].startswith("multipart/related; boundary="))
        self.assertEqual(captured["authorization"], "Bearer access-token")
        self.assertIn(b"caption-123", captured["data"])

    def test_rejects_caption_larger_than_youtube_limit_before_network(self) -> None:
        with patch("app.review.youtube_publish.MAX_CAPTION_BYTES", 10):
            with self.assertRaises(YouTubePublishError):
                _multipart_body(
                    caption_track_id="caption-123",
                    srt_text="x" * 11,
                    boundary="boundary-test",
                )

    def test_unexpected_track_response_fails_closed(self) -> None:
        with patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse({"id": "different-caption"}),
        ):
            with self.assertRaises(YouTubePublishError):
                _send_caption_update(
                    caption_track_id="caption-123",
                    srt_text="1\n00:00:00,000 --> 00:00:02,000\n佛告阿難\n",
                    access_token="access-token",
                )


if __name__ == "__main__":
    unittest.main()
