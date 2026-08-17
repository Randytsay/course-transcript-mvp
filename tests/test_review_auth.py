from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.review.auth as auth
from app.review.auth_store import ReviewAuthError, ReviewAuthStore
from app.review.store import ReviewStore


class ReviewAuthStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / "course-transcript.db"
        self.review = ReviewStore(self.database)
        self.user = self.review.get_or_create_user_for_identity(
            provider="google",
            provider_subject="google-subject",
            display_name="法專師姐",
            email="reviewer@example.test",
        )
        self.store = ReviewAuthStore(self.database)

    def test_oauth_state_is_one_time_and_pkce_is_persisted_server_side(self) -> None:
        flow = self.store.create_oauth_flow(
            provider="google",
            action="login",
            return_to="/review/video/abc",
        )
        consumed = self.store.consume_oauth_flow(
            provider="google",
            state=str(flow["state"]),
        )

        self.assertEqual(consumed["return_to"], "/review/video/abc")
        self.assertEqual(consumed["code_verifier"], flow["code_verifier"])
        self.assertNotEqual(flow["code_verifier"], flow["code_challenge"])
        with self.assertRaises(ReviewAuthError):
            self.store.consume_oauth_flow(
                provider="google",
                state=str(flow["state"]),
            )

    def test_session_cookie_value_is_not_stored_in_plaintext(self) -> None:
        created = self.store.create_session(user_id=self.user["id"])
        raw_token = created["token"]
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                "SELECT token_hash FROM review_sessions"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertNotEqual(row[0], raw_token)
        self.assertNotIn(raw_token, self.database.read_bytes().decode("latin-1", errors="ignore"))

    def test_revoked_session_is_immediately_invalid(self) -> None:
        created = self.store.create_session(user_id=self.user["id"])
        self.assertEqual(self.store.get_session(created["token"])["user_id"], self.user["id"])
        self.assertTrue(self.store.revoke_session(created["token"]))
        with self.assertRaises(ReviewAuthError):
            self.store.get_session(created["token"])


class ReviewAuthApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data_dir = Path(self.temporary.name)
        self.env = patch.dict(
            os.environ,
            {
                "REVIEW_PUBLIC_ORIGIN": "https://review.example.test",
                "REVIEW_GOOGLE_CLIENT_ID": "google-client",
                "REVIEW_GOOGLE_CLIENT_SECRET": "google-secret",
                "REVIEW_LINE_CHANNEL_ID": "line-client",
                "REVIEW_LINE_CHANNEL_SECRET": "line-secret",
                "REVIEW_SESSION_TTL_DAYS": "30",
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.original_data_dir = auth.DATA_DIR
        auth.DATA_DIR = self.data_dir
        auth._review_store_cache = None
        auth._auth_store_cache = None
        self.addCleanup(self._restore_auth_globals)
        app = FastAPI()
        app.include_router(auth.router)
        self.client = TestClient(app, base_url="https://review.example.test")

    def _restore_auth_globals(self) -> None:
        auth.DATA_DIR = self.original_data_dir
        auth._review_store_cache = None
        auth._auth_store_cache = None

    @staticmethod
    def _state_from_authorization_url(url: str) -> str:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        return query["state"][0]

    def _start(self, provider: str, *, action: str = "login", csrf: str | None = None):
        headers = {"Origin": "https://review.example.test"}
        if csrf:
            headers["X-Review-CSRF"] = csrf
        return self.client.post(
            f"/api/v1/review/auth/{provider}/start",
            headers=headers,
            json={"action": action, "return_to": "/review"},
        )

    def _complete_login(self, provider: str, identity: dict[str, str | None]):
        started = self._start(provider)
        self.assertEqual(started.status_code, 200)
        state = self._state_from_authorization_url(started.json()["authorization_url"])
        with patch.object(auth, "_identity_from_code", return_value=identity):
            response = self.client.get(
                f"/api/v1/review/auth/{provider}/callback",
                params={"state": state, "code": "provider-code"},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        return response

    def test_login_me_csrf_logout_round_trip(self) -> None:
        response = self._complete_login(
            "google",
            {
                "subject": "google-123",
                "email": "fa@example.test",
                "display_name": "法專師姐",
                "avatar_url": "https://example.test/avatar.png",
            },
        )
        set_cookie = response.headers.get("set-cookie", "")
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("Secure", set_cookie)
        self.assertIn("SameSite=lax", set_cookie)

        me = self.client.get("/api/v1/review/auth/me")
        self.assertEqual(me.status_code, 200)
        body = me.json()
        self.assertEqual(body["user"]["display_name"], "法專師姐")
        self.assertEqual([item["provider"] for item in body["identities"]], ["google"])
        csrf = body["csrf_token"]

        rejected = self.client.post(
            "/api/v1/review/auth/logout",
            headers={"Origin": "https://review.example.test"},
        )
        self.assertEqual(rejected.status_code, 403)

        logged_out = self.client.post(
            "/api/v1/review/auth/logout",
            headers={
                "Origin": "https://review.example.test",
                "X-Review-CSRF": csrf,
            },
        )
        self.assertEqual(logged_out.status_code, 200)
        self.assertTrue(logged_out.json()["logged_out"])
        self.assertEqual(self.client.get("/api/v1/review/auth/me").status_code, 401)

    def test_line_link_keeps_one_logical_user(self) -> None:
        self._complete_login(
            "google",
            {
                "subject": "google-123",
                "email": "fa@example.test",
                "display_name": "法專師姐",
                "avatar_url": None,
            },
        )
        before = self.client.get("/api/v1/review/auth/me").json()
        user_id = before["user"]["id"]
        started = self._start("line", action="link", csrf=before["csrf_token"])
        self.assertEqual(started.status_code, 200)
        state = self._state_from_authorization_url(started.json()["authorization_url"])

        with patch.object(
            auth,
            "_identity_from_code",
            return_value={
                "subject": "U-line-123",
                "email": None,
                "display_name": "LINE display name",
                "avatar_url": None,
            },
        ):
            linked = self.client.get(
                "/api/v1/review/auth/line/callback",
                params={"state": state, "code": "line-code"},
                follow_redirects=False,
            )
        self.assertEqual(linked.status_code, 303)
        after = self.client.get("/api/v1/review/auth/me").json()
        self.assertEqual(after["user"]["id"], user_id)
        self.assertEqual(
            {item["provider"] for item in after["identities"]},
            {"google", "line"},
        )
        with auth._review_store().connect() as connection:
            user_count = connection.execute("SELECT COUNT(*) FROM review_users").fetchone()[0]
        self.assertEqual(user_count, 1)

    def test_link_start_requires_current_session_csrf(self) -> None:
        self._complete_login(
            "google",
            {
                "subject": "google-123",
                "email": "fa@example.test",
                "display_name": "法專師姐",
                "avatar_url": None,
            },
        )
        response = self._start("line", action="link")
        self.assertEqual(response.status_code, 403)

    def test_login_start_rejects_cross_origin_request(self) -> None:
        response = self.client.post(
            "/api/v1/review/auth/google/start",
            headers={"Origin": "https://attacker.example"},
            json={"action": "login", "return_to": "/review"},
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
