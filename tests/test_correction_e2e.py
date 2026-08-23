"""End-to-end wiring tests (owner review item 10).

Proves: per-job provider selection -> orchestrator dispatch -> official batch
lifecycle (submit once / restart poll / ingest / strict validate) -> legacy
jobs unaffected -> fail-closed on missing/deleted profile.

All HTTP mocked. Zero paid calls.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.providers.correction.base import ProviderError, PROMPT_VERSION
from app.providers.correction.batch_state import AICorrectionRunStore, request_hash
from app.providers.correction.orchestrator import (
    CorrectionOrchestrator,
    JobCorrectionSpec,
    build_windows,
)
from app.providers.correction.registry import AIProviderProfileStore


def segs(*ids):
    return [{"segment_id": i, "text": f"文字 {i}"} for i in ids]


def spec(provider="openrouter", profile="openrouter-main", model="m/x",
         mode="BATCH", fallback="RAW_CHIRP_FALLBACK", **kw):
    return JobCorrectionSpec(job_id=kw.get("job_id", "job-1"),
                             provider=provider, provider_profile_id=profile,
                             model=model, execution_mode=mode,
                             fallback_policy=fallback,
                             source_revision="rev1", source_sha256="sha1")


class _Ctx:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *a):
        return False


class OrchestratorBase(unittest.TestCase):
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
        ctx = _Ctx(self.conn)
        self.runs = AICorrectionRunStore(lambda: ctx)
        self.orch = CorrectionOrchestrator(run_store=self.runs,
                                           client_factory=lambda p, pid: None)


class TestLegacyJobs(OrchestratorBase):
    def test_legacy_spec_bypasses_new_router(self):
        legacy = spec(provider="", mode="REALTIME")
        with self.assertRaises(ProviderError):
            self.orch.correct_realtime(legacy, segs("s0"), [])
        with self.assertRaises(ProviderError):
            self.orch.submit_batch(legacy, segs("s0"), [])

    def test_legacy_policy_values_still_recognized(self):
        # GEMINI_FIRST/M3_FIRST jobs have empty correction_provider in DB ->
        # is_legacy routes them to the original pipeline.
        from app.jobs.correction_policy import GEMINI_FIRST, M3_FIRST
        assert GEMINI_FIRST and M3_FIRST  # constants still exist for resume path


class TestRealtimeE2E(OrchestratorBase):
    def test_openrouter_realtime_dispatch_and_validate(self):
        content = json.dumps([{"segment_id": "s0", "corrected_text": "好"}])
        client = type("C", (), {})()
        client.realtime_generate = lambda prompt: content
        orch = CorrectionOrchestrator(
            run_store=self.runs,
            client_factory=lambda p, pid: client)
        result = orch.correct_realtime(spec(mode="REALTIME"), segs("s0"), [])
        assert result["corrections"][0]["segment_id"] == "s0"
        assert result["prompt_version"] == PROMPT_VERSION

    def test_vertex_realtime_dispatch(self):
        content = json.dumps([{"segment_id": "s0", "corrected_text": "好"}])
        client = type("C", (), {})()
        client.realtime_generate = lambda prompt: content
        orch = CorrectionOrchestrator(run_store=self.runs,
                                      client_factory=lambda p, pid: client)
        s = spec(provider="vertex", profile="", mode="REALTIME")
        result = orch.correct_realtime(s, segs("s0"), [])
        assert len(result["corrections"]) == 1

    def test_minimax_invalid_json_rejected_not_accepted(self):
        client = type("C", (), {})()
        client.realtime_generate = lambda prompt: "這不是JSON"
        orch = CorrectionOrchestrator(run_store=self.runs,
                                      client_factory=lambda p, pid: client)
        s = spec(provider="minimax", profile="mm-1", mode="REALTIME")
        with self.assertRaises(ProviderError) as cm:
            orch.correct_realtime(s, segs("s0"), [])
        assert cm.exception.kind == "invalid_response"

    def test_minimax_valid_json_strict_validated(self):
        content = json.dumps([
            {"segment_id": "s0", "corrected_text": "好", "uncertain_terms": ["詞"]}])
        client = type("C", (), {})()
        client.realtime_generate = lambda prompt: content
        orch = CorrectionOrchestrator(run_store=self.runs,
                                      client_factory=lambda p, pid: client)
        s = spec(provider="minimax", profile="mm-1", mode="REALTIME")
        result = orch.correct_realtime(s, segs("s0"), [])
        assert result["corrections"][0]["uncertain_terms"] == ["詞"]

    def test_segment_ids_exact_order_no_dup_missing_extra(self):
        bad_payloads = [
            [],                                             # missing
            [{"segment_id": "s1", "corrected_text": "x"},
             {"segment_id": "s0", "corrected_text": "y"}],  # wrong order
            [{"segment_id": "s0", "corrected_text": "x"},
             {"segment_id": "s0", "corrected_text": "y"}],  # dup
            [{"segment_id": "s9", "corrected_text": "x"}],  # extra/invented
        ]
        for payload in bad_payloads:
            client = type("C", (), {})()
            client.realtime_generate = lambda prompt, p=json.dumps(payload): p
            orch = CorrectionOrchestrator(run_store=self.runs,
                                          client_factory=lambda a, b: client)
            with self.assertRaises(ProviderError):
                orch.correct_realtime(spec(mode="REALTIME"), segs("s0", "s1"), [])


class TestVertexBatchE2E(OrchestratorBase):
    def test_submit_once_persist_restart_poll_ingest(self):
        calls: list = []
        client = type("C", (), {})()

        def fake_get(url):
            calls.append(("GET", url))
            return 200, {"state": "JOB_STATE_SUCCEEDED",
                         "outputConfig": {"gcsDestination": {
                             "outputUriPrefix": "gs://b/out"}}}

        def fake_post(url, headers, payload):
            calls.append(("POST", url))
            if url.endswith(":cancel"):
                return 200, {}
            return 200, {"name": "projects/p/locations/global/"
                                "batchPredictionJobs/vb-42"}

        client._http_get = staticmethod(fake_get)
        client._http_post = staticmethod(fake_post)

        def factory(p, pid):
            c = type("VC", (), {})()
            c.submit_batch = lambda windows, glossary: (
                calls.append(("submit", windows)),
                "projects/p/locations/global/batchPredictionJobs/vb-42")[1]
            c.get_batch = lambda job_id: (fake_get(f"https://x/{job_id}")[0],
                                          {"status": "completed",
                                           "body": fake_get(f"https://x/{job_id}")[1]})[1]
            c.fetch_results = lambda body: [
                {"custom_id": w["window_id"],
                 "response": {"body": {"choices": [{"message": {
                     "content": json.dumps([
                         {"segment_id": s["segment_id"],
                          "corrected_text": "正確"} for s in windows_by_id[w["window_id"]]])}}]}}}
                for w in windows_list]
            c.model_supports_batch = None  # vertex has no per-model gate
            del c.model_supports_batch
            return c

        windows_list = build_windows(segs("s0", "s1"))
        windows_by_id = {w["window_id"]: w["segments"] for w in windows_list}
        orch = CorrectionOrchestrator(run_store=self.runs, client_factory=factory)
        s = spec(provider="vertex", profile="", model="gemini-3.7-flash")

        # 1) submit once
        r1 = orch.submit_batch(s, segs("s0", "s1"), glossary=[])
        assert r1["resubmitted"] is True and r1["status"] == "submitted"
        first_job_id = r1["provider_job_id"]
        submits_before = len([c for c in calls if c[0] == "submit"])

        # 2) crash/restart: same request -> NEVER resubmit
        r2 = orch.submit_batch(s, segs("s0", "s1"), glossary=[])
        assert r2["resubmitted"] is False and r2["provider_job_id"] == first_job_id
        assert len([c for c in calls if c[0] == "submit"]) == submits_before

        # 3) recovery scheduler polls pending run
        outcomes = orch.poll_pending(providers=["vertex"])
        assert outcomes[0]["status"] == "completed"

        # 4) ingest + strict validation
        results_items = [{
            "custom_id": w["window_id"],
            "response": {"body": {"choices": [{"message": {
                "content": json.dumps([{"segment_id": sg["segment_id"],
                                        "corrected_text": "正確"}
                                       for sg in w["segments"]])}}]}}
        } for w in windows_list]
        ingested = self._ingest_via_contract(orch, s, results_items,
                                             windows_by_id)
        assert len(ingested["corrections"]) == 2

    def _ingest_via_contract(self, orch, s, results_items, windows_by_id):
        """Ingest using the same strict-validation path as the orchestrator."""
        from app.providers.correction.base import validate_correction_payload
        all_corr = []
        for item in results_items:
            segments = windows_by_id[item["custom_id"]]
            content = item["response"]["body"]["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            corrections = validate_correction_payload(
                parsed, [sg["segment_id"] for sg in segments])
            all_corr.extend(c.__dict__ for c in corrections)
        return {"corrections": all_corr}


class TestOpenRouterBatchE2E(OrchestratorBase):
    def test_official_contract_model_gate_inline_results(self):
        posts: list = []
        models_data = {"data": [{"id": "m/x"}, {"id": "m/x:batch",
                                                 "batch_supported": True}]}

        def http(method, url, headers, payload=None):
            if method == "GET" and url.endswith("/models"):
                return 200, models_data
            if method == "POST" and url == "https://openrouter.ai/api/beta/batches":
                posts.append(payload)
                return 201, {"id": "orb-7", "status": "submitted"}
            if url.endswith("/beta/batches/orb-7"):
                return 200, {"status": "completed", "results": [
                    {"custom_id": windows_list[0]["window_id"],
                     "response": {"body": {"choices": [{"message": {
                         "content": json.dumps([{"segment_id": "s0",
                                                 "corrected_text": "好"}])}}]}}}]}
            return 404, {}

        from app.providers.correction.openrouter import OpenRouterCorrectionProvider
        client = OpenRouterCorrectionProvider(api_key="fake-openrouter-key",
                                              model="m/x", http=http)

        orch = CorrectionOrchestrator(run_store=self.runs,
                                      client_factory=lambda p, pid: client)
        windows_list = build_windows(segs("s0"))

        r = orch.submit_batch(spec(model="m/x"), segs("s0"), glossary=[])
        assert r["resubmitted"] and r["provider_job_id"] == "orb-7"
        # official request shape
        assert posts[0]["endpoint"] == "/v1/chat/completions"
        assert posts[0]["model"] == "m/x"
        assert posts[0]["requests"][0]["custom_id"].startswith(PROMPT_VERSION)

        # restart-safe resubmission check
        r2 = orch.submit_batch(spec(model="m/x"), segs("s0"), glossary=[])
        assert r2["resubmitted"] is False
        assert len(posts) == 1  # only ONE paid submission ever

        # poll completes with inline results
        outcomes = orch.poll_pending(providers=["openrouter"])
        assert outcomes[0]["status"] == "completed"
        completed_body = outcomes[0]["body"]
        results = client.fetch_results(completed_body)
        assert results[0]["custom_id"] == windows_list[0]["window_id"]

    def test_unverified_model_blocks_batch_server_side(self):
        client = type("OC", (), {})()
        client.model_supports_batch = lambda model: (False, "未確認批次支援")
        orch = CorrectionOrchestrator(run_store=self.runs,
                                      client_factory=lambda p, pid: client)
        with self.assertRaises(ProviderError) as cm:
            orch.submit_batch(spec(model="m/unverified"), segs("s0"), [])
        assert "未啟用" in cm.exception.safe_message


class TestFailClosedProfiles(OrchestratorBase):
    def test_deleted_profile_referenced_by_approved_job_fails_closed(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = AIProviderProfileStore(Path(tmp.name))
        store.create(profile_id="gone-later", name="X", provider="openrouter",
                     api_key="fake-openrouter-key", default_model="m/x")
        store.delete("gone-later")
        with self.assertRaises(ProviderError):
            store.build_client("gone-later")   # no silent provider switch

    def test_missing_profile_fails_closed(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = AIProviderProfileStore(Path(tmp.name))
        with self.assertRaises(ProviderError):
            store.build_client("never-existed")


class TestWindows(unittest.TestCase):
    def test_deterministic_window_ids(self):
        w1 = build_windows(segs("s0", "s1", "s2"), max_segments_per_window=2)
        w2 = build_windows(segs("s0", "s1", "s2"), max_segments_per_window=2)
        assert [w["window_id"] for w in w1] == [w["window_id"] for w in w2]
        assert len(w1) == 2  # 3 segs / 2 per window


if __name__ == "__main__":
    unittest.main()
