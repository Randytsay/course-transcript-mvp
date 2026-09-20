import unittest
from unittest import mock

from app.review import ai_accounts_preflight as preflight


class _Response:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class AIAccountSpeechPreflightTests(unittest.TestCase):
    def test_speech_data_plane_403_fails_before_paid_work(self) -> None:
        def fake_get(url, **kwargs):
            if "cloudbilling.googleapis.com" in url:
                return _Response(200, {"billingEnabled": True})
            if "serviceusage.googleapis.com" in url:
                return _Response(200, {"state": "ENABLED"})
            if "us-speech.googleapis.com" in url:
                return _Response(403)
            return _Response(200)

        credential = {"project_id": "proj-test"}
        metadata = {"project_id": "proj-test", "location": "global", "gcs_bucket": ""}

        with mock.patch.object(preflight, "_mint_token", return_value="token"), \
             mock.patch("requests.get", side_effect=fake_get):
            result = preflight.run_live_checks(credential, metadata)

        self.assertFalse(result["ok"])
        self.assertEqual(result["checks"]["speech_access"], "fail http 403")
        self.assertTrue(any("Cloud Speech Client" in item for item in result["errors"]))

    def test_missing_exact_recognize_permission_fails_closed(self) -> None:
        def fake_get(url, **kwargs):
            if "cloudbilling.googleapis.com" in url:
                return _Response(200, {"billingEnabled": True})
            if "serviceusage.googleapis.com" in url:
                return _Response(200, {"state": "ENABLED"})
            return _Response(200)

        def fake_post(url, **kwargs):
            self.assertIn(":testIamPermissions", url)
            self.assertEqual(
                kwargs["json"],
                {"permissions": ["speech.recognizers.recognize"]},
            )
            return _Response(200, {"permissions": []})

        credential = {"project_id": "proj-test"}
        metadata = {"project_id": "proj-test", "location": "global", "gcs_bucket": ""}

        with mock.patch.object(preflight, "_mint_token", return_value="token"), \
             mock.patch("requests.get", side_effect=fake_get), \
             mock.patch("requests.post", side_effect=fake_post):
            result = preflight.run_live_checks(credential, metadata)

        self.assertFalse(result["ok"])
        self.assertEqual(result["checks"]["speech_access"], "ok")
        self.assertEqual(result["checks"]["speech_recognize_permission"], "missing")
        self.assertTrue(any("speech.recognizers.recognize" in item for item in result["errors"]))


if __name__ == "__main__":
    unittest.main()
