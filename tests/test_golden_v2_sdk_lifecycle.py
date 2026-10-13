"""Real SDK serialization/transport regression; no credentials or network IO."""
import gc
from importlib.metadata import version, PackageNotFoundError
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.pipeline.golden_v2_runtime_services import paid_services


def isolated_sdk_available():
    try:
        return version("google-genai") == "2.29.0"
    except PackageNotFoundError:
        return False


@unittest.skipUnless(isolated_sdk_available(), "requires isolated V2 google-genai 2.29.0")
class SDKLifecycleTests(unittest.TestCase):
    def setUp(self):
        import httpx
        from google import genai
        from google.genai import types
        from google.oauth2.credentials import Credentials
        self.requests = []

        def respond(request):
            self.requests.append((request.url.path, json.loads(request.content)))
            if request.url.path.endswith(":countTokens"):
                return httpx.Response(200, json={"totalTokens": 100})
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": "測試逐字稿"}]},
                                "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 8}})

        def make_client():
            return genai.Client(enterprise=True, project="offline-test", location="global",
                credentials=Credentials(token="offline-test-token"),
                http_options=types.HttpOptions(api_version="v1",
                    client_args={"transport": httpx.MockTransport(respond)},
                    retry_options=types.HttpRetryOptions(attempts=1)))
        self.make_client = make_client

    def test_old_models_only_reference_closes_transport_before_request(self):
        client = self.make_client()
        models = client.models
        del client
        gc.collect()
        with self.assertRaisesRegex(RuntimeError, "client has been closed"):
            models.count_tokens(model="gemini-3.8-flash", contents="offline")
        self.assertEqual(self.requests, [])

    def test_factory_keeps_client_alive_through_count_and_generation(self):
        # A side-effect factory creates the client without a test-held owner
        # reference that would conceal the production lifecycle bug.
        def clients():
            return self.make_client(), SimpleNamespace(), SimpleNamespace()
        quote = {"estimated_stage_cents": {"gemini_primary": 160}}
        with patch("app.pipeline.golden_v2_runtime_services._clients", side_effect=clients), \
             patch("app.pipeline.golden_v2_runtime_services._root", return_value=Path("/unused")), \
             patch("app.pipeline.golden_v2_runtime_services.verify_job_grant", return_value=(quote, {})), \
             patch.dict("os.environ", {"GCS_BUCKET": "offline-test"}):
            services = paid_services({"stage": "gemini_primary"})
        gc.collect()
        models = services["stage_options"]["vertex_client"].models
        try:
            from google.genai import types
            response = models.generate_content(model="gemini-3.8-flash",
                contents=["offline", types.Part.from_uri(
                    file_uri="gs://offline-test/audio.flac", mime_type="audio/flac")],
                config=types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(thinking_level="MEDIUM")))
            self.assertEqual(response.text, "測試逐字稿")
            self.assertEqual([p.rsplit(":", 1)[-1] for p, _ in self.requests],
                             ["countTokens", "generateContent"])
            self.assertEqual(self.requests[1][1]["generationConfig"]["maxOutputTokens"], 65536)
        finally:
            if hasattr(models, "_client"):
                models._client.close()
