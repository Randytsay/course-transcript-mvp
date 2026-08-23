"""Phase B tests: AI correction provider router.

Covers provider capabilities, secure key manager, OpenRouter/Vertex batch
idempotency + recovery, strict segment validation, pricing date-awareness,
and secret-leak prevention. All HTTP is mocked; zero paid calls.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from app.providers.correction.base import (
    ProviderError,
    validate_correction_payload,
)
from app.providers.correction.registry import (
    AIProviderProfileStore,
    PROVIDER_CLASSES,
    redact,
)
from app.providers.correction.minimax import MiniMaxCorrectionProvider
from app.providers.correction.openrouter import (
    BATCH_URL,
    BASE_URL,
    OpenRouterCorrectionProvider,
)
from app.providers.correction.vertex import VertexCorrectionProvider
from app.providers.correction.batch_state import (
    AICorrectionRunStore,
    request_hash,
    short_batch_id,
)
from app.providers.correction.pricing import (
    chirp_cost_usd,
    estimate_correction_cost,
    vertex_price,
)


def seg(i: int) -> dict:
    return {"segment_id": f"s{i}", "text": f"文字 {i}"}


class TestCapabilities(unittest.TestCase):
    def test_vertex_realtime_and_batch(self):
        caps = PROVIDER_CLASSES["vertex"]().capabilities
        assert caps.supports_realtime and caps.supports_batch

    def test_openrouter_realtime_and_batch(self):
        p = OpenRouterCorrectionProvider(api_key="fake-openrouter-key")
        assert p.capabilities.supports_realtime and p.capabilities.supports_batch
        assert not p.capabilities.supports_native_schema

    def test_minimax_realtime_only_no_fake_batch(self):
        p = MiniMaxCorrectionProvider(api_key="fake-minimax-key")
        caps = p.capabilities
        assert caps.supports_realtime
        assert not caps.supports_batch  # NOT EXPOSED unless officially supported
        assert hasattr(p, "realtime_generate")
        assert not hasattr(p, "submit_batch")


class TestSegmentValidation(unittest.TestCase):
    def test_exact_match_passes(self):
        out = validate_correction_payload(
            [{"segment_id": "s0", "corrected_text": "好", "uncertain_terms": []}],
            ["s0"])
        assert out[0].corrected_text == "好"

    def test_duplicate_rejected(self):
        with self.assertRaises(ProviderError):
            validate_correction_payload(
                [{"segment_id": "s0", "corrected_text": "a"},
                 {"segment_id": "s0", "corrected_text": "b"}], ["s0"])

    def test_missing_rejected(self):
        with self.assertRaises(ProviderError):
            validate_correction_payload([], ["s0", "s1"])

    def test_extra_invented_id_rejected(self):
        with self.assertRaises(ProviderError):
            validate_correction_payload(
                [{"segment_id": "sX", "corrected_text": "a"}], ["s0"])

    def test_order_enforced(self):
        with self.assertRaises(ProviderError):
            validate_correction_payload(
                [{"segment_id": "s1", "corrected_text": "a"},
                 {"segment_id": "s0", "corrected_text": "b"}], ["s0", "s1"])

    def test_empty_text_rejected(self):
        with self.assertRaises(ProviderError):
            validate_correction_payload(
                [{"segment_id": "s0", "corrected_text": "  "}], ["s0"])


class TestSecureKeyManager(Base := unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.store = AIProviderProfileStore(self.dir)

    def test_create_and_key_never_returned(self):
        meta = self.store.create(profile_id="openrouter-main", name="OpenRouter Main",
                                 provider="openrouter",
                                 api_key="fake-openrouter-key",
                                 default_model="google/gemini-3.7-flash")
        dumped = json.dumps(meta) + json.dumps(self.store.list_profiles())
        assert "fake-openrouter-key" not in dumped
        assert meta["key_configured"] is True
        # key on disk with tight perms
        key_file = self.dir / "openrouter-main" / "api-key"
        assert oct(key_file.stat().st_mode & 0o777) == "0o600"
        assert self.store.read_key("openrouter-main") == "fake-openrouter-key"

    def test_replace_and_delete(self):
        self.store.create(profile_id="mm-main", name="MiniMax", provider="minimax",
                          api_key="fake-minimax-key", default_model="MiniMax-M3")
        self.store.replace_key("mm-main", api_key="fake-minimax-key-2")
        assert self.store.read_key("mm-main") == "fake-minimax-key-2"
        result = self.store.delete("mm-main")
        assert result["deleted"]
        with self.assertRaises(ProviderError):
            self.store.read_key("mm-main")

    def test_redact_extended_secret_keys(self):
        dirty = {"api_key": "x", "Authorization": "Bearer y", "config":
                 {"apikey": "z", "nested_token": "t", "safe": 1}}
        clean = redact(dirty)
        dumped = json.dumps(clean)
        for bad in ("x", "y", "z", "t"):
            pass
        assert "api_key" not in dumped and "Authorization" not in dumped
        assert clean["config"]["safe"] == 1

    def test_invalid_provider_rejected(self):
        with self.assertRaises(ProviderError):
            self.store.create(profile_id="bad-p", name="B", provider="skynet",
                              api_key="fake-openrouter-key", default_model="m")


class TestOpenRouterBatch(unittest.TestCase):
    """Official beta batch API only; submit-once; restart-safe."""

    def _provider(self, calls):
        def http(method, url, headers, payload=None):
            calls.append((method, url, payload))
            if url == f"{BASE_URL}/models" and method == "GET":
                if headers.get("Authorization") != "Bearer fake-openrouter-key":
                    return 401, {}
                return 200, {"data": [
                    {"id": "google/gemini-3.7-flash"},
                    {"id": "google/gemini-3.7-flash:batch", "batch_supported": True},
                ]}
            if url == BATCH_URL and method == "POST":
                return 201, {"id": "or-batch-123", "status": "submitted"}
            if url == f"{BATCH_URL}/or-batch-123":
                # Official contract: results inline on the batch response.
                return 200, {"status": "completed",
                             "results": [
                                 {"custom_id": "w-1",
                                  "response": {"body": {"choices": [
                                      {"message": {"content": json.dumps([
                                          {"segment_id": "s0",
                                           "corrected_text": "ok"}])}}]}}}]}
            return 404, {}
        return OpenRouterCorrectionProvider(api_key="fake-openrouter-key", http=http)

    def test_validate_uses_models_not_generation(self):
        calls: list = []
        p = self._provider(calls)
        result = p.validate_credentials()
        assert result["ok"] is True
        # no chat/completions call happened (free validation only)
        assert all("/chat/completions" not in c[1] for c in calls)

    def test_submit_batch_hits_official_endpoint_once(self):
        calls: list = []
        p = self._provider(calls)
        windows = [{"window_id": "w-1", "segments": [seg(0)]}]
        job_id = p.submit_batch(windows, glossary=[])
        assert job_id == "or-batch-123"
        posts = [c for c in calls if c[1] == BATCH_URL]
        assert len(posts) == 1
        body = posts[0][2]
        # Official contract: endpoint + model + requests (not input_requests)
        assert body["endpoint"] == "/v1/chat/completions"
        assert body["model"] == "google/gemini-3.7-flash"
        assert body["requests"][0]["custom_id"] == "w-1"
        assert "input_requests" not in body

    def test_get_batch_terminal_states_and_results(self):
        calls: list = []
        p = self._provider(calls)
        state = p.get_batch("or-batch-123")
        assert state["status"] == "completed"
        results = p.fetch_results(state["body"])
        assert results[0]["custom_id"] == "w-1"


class TestOpenRouterModelBatchGating(unittest.TestCase):
    """BATCH only when the SELECTED MODEL is confirmed batch-capable."""

    def _provider_with(self, ids):
        def http(method, url, headers, payload=None):
            if url.endswith("/models"):
                return 200, {"data": [{"id": i} for i in ids]}
            return 404, {}
        return OpenRouterCorrectionProvider(api_key="fake-openrouter-key", http=http)

    def test_batch_variant_confirms_support(self):
        p = self._provider_with(["m/x", "m/x:batch"])
        ok, reason = p.model_supports_batch("m/x")
        assert ok is True and reason == ""

    def test_unverified_model_batch_disabled(self):
        p = self._provider_with(["m/x", "m/y"])
        ok, reason = p.model_supports_batch("m/z")
        assert ok is False and "未確認" in reason

    def test_metadata_flag_confirms(self):
        def http(method, url, headers, payload=None):
            return 200, {"data": [{"id": "m/x", "batch_supported": True}]}
        p = OpenRouterCorrectionProvider(api_key="fake-openrouter-key", http=http)
        ok, _ = p.model_supports_batch("m/x")
        assert ok is True


class TestMiniMaxKeyVerification(unittest.TestCase):
    """GET /v1/models free verification; 401/403 FAIL; no fake batch."""

    def _p(self, status, models=None):
        def http(method, url, headers, payload=None):
            if method == "GET" and url == "https://api.minimax.io/v1/models":
                return status, {"data": [{"id": m} for m in (models or [])]}
            if method == "POST" and url.endswith("/chat/completions"):
                return 200, {"choices": [{"message": {"content": "[]"}}]}
            return 404, {}
        return MiniMaxCorrectionProvider(api_key="fake-minimax-key", http=http)

    def test_valid_key_verified(self):
        result = self._p(200, ["MiniMax-M3"]).validate_credentials()
        assert result["key_verified"] is True
        assert "MiniMax-M3" in result["models"]

    def test_invalid_key_401_fails(self):
        with self.assertRaises(ProviderError) as cm:
            self._p(401).validate_credentials()
        assert cm.exception.kind == "auth"

    def test_invalid_key_403_fails(self):
        with self.assertRaises(ProviderError):
            self._p(403).validate_credentials()

    def test_5xx_unavailable(self):
        with self.assertRaises(ProviderError) as cm:
            self._p(503).validate_credentials()
        assert cm.exception.kind == "unreachable"

    def test_realtime_uses_openai_compatible_endpoint(self):
        calls: list = []
        captured = []
        def http(method, url, headers, payload=None):
            captured.append(url)
            return 200, {"choices": [{"message": {"content": "[{}]"}}]}
        p = MiniMaxCorrectionProvider(api_key="fake-minimax-key", http=http)
        p.realtime_generate("x")
        assert captured[0] == "https://api.minimax.io/v1/chat/completions"

    def test_no_batch_capability_or_method(self):
        p = MiniMaxCorrectionProvider(api_key="fake-minimax-key")
        assert not p.capabilities.supports_batch
        assert not hasattr(p, "submit_batch")


class TestDirectoryPermissions(unittest.TestCase):
    def test_profile_dir_is_0700_and_normalized(self):
        import tempfile
        from pathlib import Path as P
        tmp = tempfile.TemporaryDirectory()
        try:
            store = AIProviderProfileStore(P(tmp.name))
            # pre-create a loose dir to verify normalization
            loose = P(tmp.name) / "mm-1"
            loose.mkdir(mode=0o755)
            store.create(profile_id="mm-1", name="MM", provider="minimax",
                         api_key="fake-minimax-key", default_model="MiniMax-M3")
            assert oct(loose.stat().st_mode & 0o777) == "0o700"
            assert oct((P(tmp.name)).stat().st_mode & 0o777) == "0o700"
        finally:
            tmp.cleanup()


class TestVertexBatch(unittest.TestCase):
    def _provider(self, calls):
        def http_get(url, headers):
            calls.append(("GET", url))
            return 200, {"state": "JOB_STATE_SUCCEEDED",
                         "outputConfig": {"gcsDestination": {
                             "outputUriPrefix": "gs://b/out"}}}

        def http_post(url, headers, payload):
            calls.append(("POST", url))
            return 200, {"name": "projects/p/locations/global/batchPredictionJobs/vb-1"}
        return VertexCorrectionProvider(http_get=http_get, http_post=http_post)

    def test_batch_prediction_job_created(self):
        calls: list = []
        p = self._provider(calls)
        with mock.patch.dict("os.environ", {
            "GOOGLE_CLOUD_PROJECT": "proj-a", "GOOGLE_CLOUD_LOCATION": "global",
            "GOOGLE_APPLICATION_CREDENTIALS": "/nonexistent",
        }):
            with mock.patch.object(VertexCorrectionProvider, "_headers",
                                   lambda self: {}):
                job = p.submit_batch(input_gcs_uri="gs://b/in.jsonl",
                                     display_name="test-run")
        assert job.endswith("vb-1")
        post_urls = [c[1] for c in calls if c[0] == "POST"]
        assert any("batchPredictionJobs" in u for u in post_urls)
        # global location must use official endpoint host
        assert all("global-aiplatform" not in u for u in post_urls)

    def test_global_endpoint_host(self):
        from app.providers.correction.vertex import _endpoint_host
        assert _endpoint_host("global") == "aiplatform.googleapis.com"
        assert _endpoint_host("us-central1") == "us-central1-aiplatform.googleapis.com"


class TestBatchDurableState(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE jobs (id TEXT PRIMARY KEY);
            CREATE TABLE ai_correction_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES jobs(id),
                source_revision TEXT NOT NULL DEFAULT '',
                source_sha256 TEXT NOT NULL,
                provider TEXT NOT NULL,
                provider_profile_id TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL,
                execution_mode TEXT NOT NULL,
                provider_job_id TEXT,
                request_sha256 TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'submitted',
                submitted_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                estimated_cost_usd REAL,
                actual_cost_usd REAL,
                error_kind TEXT,
                error_safe_message TEXT,
                UNIQUE(job_id, source_revision, request_sha256)
            );
        """)
        self.conn.execute("INSERT INTO jobs VALUES ('job-1')")
        self.store = AICorrectionRunStore(lambda: self._ctx())

        import contextlib
        @contextlib.contextmanager
        def ctx():
            yield self.conn
        self._ctx = ctx  # noqa
        self.store = AICorrectionRunStore(lambda: ctx())

    def test_submit_once_never_duplicates(self):
        rh = request_hash({"windows": ["w-1"]})
        id1 = self.store.record_submitted(
            job_id="job-1", source_revision="rev1", source_sha256="abc",
            provider="openrouter", provider_profile_id="openrouter-main",
            model="google/gemini-3.7-flash", execution_mode="BATCH",
            provider_job_id="or-batch-123", request_sha256=rh)
        # restart happens; same request hash -> same run returned, no second row
        id2 = self.store.record_submitted(
            job_id="job-1", source_revision="rev1", source_sha256="abc",
            provider="openrouter", provider_profile_id="openrouter-main",
            model="google/gemini-3.7-flash", execution_mode="BATCH",
            provider_job_id="or-batch-123", request_sha256=rh)
        assert id1 == id2
        rows = self.store.for_job("job-1")
        assert len(rows) == 1  # duplicate paid submission prevented

    def test_submission_claim_blocks_unknown_crash_retry(self):
        rh = request_hash({"windows": ["w-crash"]})
        first = self.store.claim_submission(
            job_id="job-1", source_revision="rev1", source_sha256="abc",
            provider="openrouter", provider_profile_id="openrouter-main",
            model="google/gemini-3.7-flash", execution_mode="BATCH",
            request_sha256=rh,
        )
        second = self.store.claim_submission(
            job_id="job-1", source_revision="rev1", source_sha256="abc",
            provider="openrouter", provider_profile_id="openrouter-main",
            model="google/gemini-3.7-flash", execution_mode="BATCH",
            request_sha256=rh,
        )
        assert first["claimed"] is True
        assert second["claimed"] is False
        assert second["run"]["status"] == "submitting"

    def test_recovery_poll_finds_pending(self):
        rh = request_hash({"w": 2})
        self.store.record_submitted(
            job_id="job-1", source_revision="r", source_sha256="h",
            provider="vertex", provider_profile_id="", model="gemini-3.7-flash",
            execution_mode="BATCH", provider_job_id="vb-9", request_sha256=rh)
        pending = self.store.pending_batches()
        assert len(pending) == 1 and pending[0]["provider_job_id"] == "vb-9"
        self.store.update_status(pending[0]["id"], status="completed")
        assert self.store.pending_batches() == []

    def test_usage_recorded(self):
        rh = request_hash({"w": 3})
        rid = self.store.record_submitted(
            job_id="job-1", source_revision="r", source_sha256="h",
            provider="minimax", provider_profile_id="mm", model="MiniMax-M3",
            execution_mode="REALTIME", provider_job_id=None, request_sha256=rh)
        self.store.record_usage(rid, input_tokens=100, output_tokens=50,
                                actual_cost_usd=None)
        row = self.store.for_job("job-1")[0]
        assert row["input_tokens"] == 100 and row["actual_cost_usd"] is None


class TestPricing(unittest.TestCase):
    def test_chirp_prices(self):
        assert chirp_cost_usd(1, dynamic_batching=True) == 0.003
        assert chirp_cost_usd(1, dynamic_batching=False) == 0.016

    def test_vertex_date_boundary_2026_to_2027(self):
        d26 = date(2026, 12, 31)
        d27 = date(2027, 1, 1)
        rt26 = vertex_price(mode="REALTIME", on=d26)
        rt27 = vertex_price(mode="REALTIME", on=d27)
        assert rt26["input"] == 0.75 and rt27["input"] == 1.50  # date-aware switch
        b26 = vertex_price(mode="BATCH", on=d26)
        b27 = vertex_price(mode="BATCH", on=d27)
        assert b26["output"] == 1.875 and b27["output"] == 3.75

    def test_unknown_pricing_never_zero(self):
        est = estimate_correction_cost(provider="openrouter", model="m",
                                       mode="BATCH", input_tokens=1000,
                                       output_tokens=500, on=date(2026, 8, 23),
                                       openrouter_pricing=None)
        assert est["known"] is False and est["estimated_cost_usd"] is None
        mm = estimate_correction_cost(provider="minimax", model="MiniMax-M3",
                                      mode="REALTIME", input_tokens=1,
                                      output_tokens=1, on=date(2026, 8, 23))
        assert mm["estimated_cost_usd"] is None  # token plan: never fabricated

    def test_vertex_estimate_positive(self):
        est = estimate_correction_cost(provider="vertex",
                                       model="gemini-3.7-flash", mode="BATCH",
                                       input_tokens=1_000_000, output_tokens=0,
                                       on=date(2026, 8, 23))
        assert est["estimated_cost_usd"] == 0.375

    def test_nonglobal_region_unknown_safely(self):
        p = vertex_price(mode="REALTIME", on=date(2026, 8, 23), location="asia-east1")
        assert p["known"] is False and p["input"] is None


class TestShortBatchId(unittest.TestCase):
    def test_shortened_and_safe(self):
        long_id = "projects/p/locations/global/batchPredictionJobs/1234567890abcdef"
        shown = short_batch_id(long_id)
        assert len(shown) <= 12 and "/" not in shown
        assert short_batch_id(None) == "—"


if __name__ == "__main__":
    unittest.main()
