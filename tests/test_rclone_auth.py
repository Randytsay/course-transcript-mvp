import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.jobs import rclone_auth


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, *args: object, **kwargs: object) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class RcloneAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        rclone_auth._reset_token_cache()

    def tearDown(self) -> None:
        rclone_auth._reset_token_cache()

    def _config(self, directory: Path, *, expiry: str, access_token: str) -> Path:
        path = directory / "rclone.conf"
        path.write_text(
            "[gdrive]\n"
            "type = drive\n"
            "client_id = test-client\n"
            "client_secret = test-secret\n"
            f'token = {json.dumps({"access_token": access_token, "token_type": "Bearer", "expiry": expiry, "refresh_token": "test-refresh"})}\n',
            encoding="utf-8",
        )
        return path

    def test_unexpired_token_is_passed_without_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._config(
                Path(temporary),
                expiry="2099-01-01T00:00:00+00:00",
                access_token="valid-access",
            )
            with patch.dict("os.environ", {"RCLONE_CONFIG": str(path)}, clear=False), patch(
                "app.jobs.rclone_auth.urlopen"
            ) as urlopen:
                environment = rclone_auth.rclone_environment()
            urlopen.assert_not_called()
            payload = json.loads(environment["RCLONE_DRIVE_TOKEN"])
            self.assertEqual(payload["access_token"], "valid-access")
            self.assertNotIn("refresh_token", payload)

    def test_expired_token_is_refreshed_in_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._config(
                Path(temporary),
                expiry="2020-01-01T00:00:00+00:00",
                access_token="expired-access",
            )
            with patch.dict("os.environ", {"RCLONE_CONFIG": str(path)}, clear=False), patch(
                "app.jobs.rclone_auth.urlopen",
                return_value=_Response({"access_token": "refreshed-access", "expires_in": 3600}),
            ) as urlopen:
                environment = rclone_auth.rclone_environment()
            urlopen.assert_called_once()
            payload = json.loads(environment["RCLONE_DRIVE_TOKEN"])
            self.assertEqual(payload["access_token"], "refreshed-access")
            self.assertNotIn("refresh_token", payload)

    def test_missing_config_keeps_environment_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            "os.environ", {"RCLONE_CONFIG": str(Path(temporary) / "missing.conf")}, clear=False
        ):
            environment = rclone_auth.rclone_environment()
        self.assertNotIn("RCLONE_DRIVE_TOKEN", environment)


if __name__ == "__main__":
    unittest.main()
