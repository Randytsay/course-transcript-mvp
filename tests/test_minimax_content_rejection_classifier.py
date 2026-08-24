from __future__ import annotations

import unittest

from app.providers.correction.base import ProviderError
from app.providers.correction.minimax import MiniMaxCorrectionProvider


def _error_for(body: dict, *, status: int) -> ProviderError:
    def http(method, url, headers, payload=None):
        return status, body

    provider = MiniMaxCorrectionProvider(
        api_key="fake-minimax-key",
        base_url="https://api.minimaxi.com",
        http=http,
    )
    try:
        provider.realtime_generate("TOP_SECRET_TRANSCRIPT")
    except ProviderError as exc:
        return exc
    raise AssertionError("expected ProviderError")


class MiniMaxContentRejectionClassifierTests(unittest.TestCase):
    def test_403_safety_word_in_auth_message_remains_auth(self):
        exc = _error_for(
            {
                "error": {
                    "type": "permission_error",
                    "message": "credential rejected by safety gateway",
                }
            },
            status=403,
        )
        self.assertEqual(exc.kind, "auth")
        self.assertNotIn("category=content_rejected", exc.safe_message)

    def test_403_sensitive_word_in_auth_message_remains_auth(self):
        exc = _error_for(
            {
                "error": {
                    "type": "permission_error",
                    "message": "sensitive credential permission denied",
                }
            },
            status=403,
        )
        self.assertEqual(exc.kind, "auth")
        self.assertNotIn("category=content_rejected", exc.safe_message)

    def test_403_content_marker_without_structured_evidence_remains_auth(self):
        exc = _error_for(
            {"message": "request rejected by content policy"},
            status=403,
        )
        self.assertEqual(exc.kind, "auth")
        self.assertNotIn("category=content_rejected", exc.safe_message)

    def test_422_content_marker_with_error_type_is_content_rejected(self):
        exc = _error_for(
            {
                "message": "Content moderation rejected request",
                "type": "unprocessable_entity_error",
                "error": {"type": "error"},
            },
            status=422,
        )
        self.assertEqual(exc.kind, "content_rejected")
        self.assertIn("category=content_rejected", exc.safe_message)

    def test_422_content_marker_with_base_resp_status_is_content_rejected(self):
        exc = _error_for(
            {
                "message": "Content moderation rejected request",
                "base_resp": {"status_code": 1008},
            },
            status=422,
        )
        self.assertEqual(exc.kind, "content_rejected")
        self.assertIn("provider code=1008", exc.safe_message)
        self.assertIn("category=content_rejected", exc.safe_message)

    def test_422_generic_safety_word_with_structure_remains_invalid_request(self):
        exc = _error_for(
            {
                "error": {
                    "type": "invalid_request_error",
                    "message": "safety validation failed for request schema",
                }
            },
            status=422,
        )
        self.assertEqual(exc.kind, "invalid_request")
        self.assertNotIn("category=content_rejected", exc.safe_message)


if __name__ == "__main__":
    unittest.main()
