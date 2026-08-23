"""V2 acceptance tests for the AI/Vertex Account Profile Manager.

Covers: crash-safe activation + fault injection, fail-closed preflight
(mocked google-auth refresh path), client cannot skip preflight, runtime
states (CONFIGURED / PENDING_RESTART / ACTIVE), project mismatch, secret
leakage = 0, persistent-path contract, and compose contract checks.
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.review.ai_accounts_store import AIAccountError, AIAccountStore


def sa(project: str, local: str = "sa") -> dict:
    return {
        "type": "service_account",
        "project_id": project,
        "private_key": f"-----BEGIN PRIVATE KEY-----\n{local}\n-----END PRIVATE KEY-----\n",
        "client_email": f"{local}@{project}.iam.gserviceaccount.com",
    }


class Base(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.dir = self.base / "ai-accounts"          # persistent host dir (simulated)
        self.target = self.base / "mounted" / "gcp-sa.json"
        self.audit: list[dict] = []
        self.store = AIAccountStore(
            accounts_dir=self.dir,
            target_key_path=self.target,
            audit_callback=lambda *, actor, action, entity_id, payload=None: (
                self.audit.append({"actor": actor, "action": action,
                                   "entity_id": entity_id, "payload": payload})
            ),
            preflight=lambda cred, meta: {"ok": True, "errors": [], "checks": {}},
        )

    def add(self, name: str, project: str, location="global", bucket="", local="a",
            credit_type="unknown", billing_label="", credit_note="",
            credit_status="unknown", trial_started_at="", trial_expires_at=""):
        return self.store.save_profile(
            name=name, sa_json=sa(project, local), location=location,
            gcs_bucket=bucket, actor="owner",
            credit_type=credit_type, billing_label=billing_label,
            credit_note=credit_note, credit_status=credit_status,
            trial_started_at=trial_started_at, trial_expires_at=trial_expires_at,
        )


class TestCrashSafeActivation(Base):
    """Fault injection at each stage of _activate."""

    def _activate_with_crash_after(self, stage: str):
        self.add("p1", "proj-a")
        self.store._activate_for_tests("p1")
        self.add("p2", "proj-b", location="asia-east1", bucket="b-b", local="z")

        real_write = self.store._write_atomic
        crash_after: dict[str, bool] = {"credential": False, "env": False,
                                        "before-commit": False}
        # map artifact name -> which stage crashes right after it
        trigger = {"credential": "gcp-sa.json", "env": "ai-active.env",
                   "before-commit": "ai-active.env"}

        def crashing_write(path, content, mode=None):
            name = Path(path).name
            real_write(path, content, mode)
            if stage in trigger and name == trigger[stage] and not crash_after[stage]:
                crash_after[stage] = True
                raise KeyboardInterrupt  # simulate SIGKILL right after this write

        with mock.patch.object(self.store, "_write_atomic", side_effect=crashing_write):
            with self.assertRaises(KeyboardInterrupt):
                self.store._activate_for_tests("p2")

    def test_crash_after_credential_replace_detected_and_repaired(self) -> None:
        self._activate_with_crash_after("credential")
        # mounted credential = proj-b but commit pointer still p1/proj-a
        assert json.loads(self.target.read_text())["project_id"] == "proj-b"
        assert self.store.get_active_name() == "p1"
        report = self.store.reconcile()
        # reconcile re-applies committed profile artifacts from source of truth
        assert report["repaired"] is True or report["consistent"] is True
        mounted_after = json.loads(self.target.read_text())
        assert mounted_after["project_id"] == "proj-a"  # restored to committed profile

    def test_crash_after_env_replace_detected_and_repaired(self) -> None:
        self._activate_with_crash_after("env")
        # env file now contains proj-b (crashed after writing it) — inconsistent
        # with the committed profile p1/proj-a
        report = self.store.reconcile()
        assert report.get("repaired") or report["consistent"]
        assert "GOOGLE_CLOUD_PROJECT=proj-a" in self.env_file_text()  # restored

    def test_crash_before_commit_leaves_previous_active(self) -> None:
        self._activate_with_crash_after("before-commit")
        assert self.store.get_active_name() == "p1"  # commit pointer never written
        report = self.store.reconcile()
        assert report["consistent"] or report["repaired"]

    def env_file_text(self) -> str:
        return (self.dir / "ai-active.env").read_text()


class TestPartialStateDetection(Base):
    def test_inconsistent_state_not_reported_as_healthy(self) -> None:
        """credential=B, active=A must NOT be treated as consistent/ACTIVE."""
        self.add("a", "proj-a")
        self.store._activate_for_tests("a")
        self.add("b", "proj-b", local="z")
        # manually swap credential behind the manager's back (simulates partial state)
        cred_b = (self.dir / "profiles" / "b" / "credential.json").read_bytes()
        self.target.write_bytes(cred_b)
        status = self.store.runtime_status()
        # after auto-reconcile inside runtime_status it is repaired; either way
        # it must never claim ACTIVE while files disagreed.
        doc = self.store.verify_runtime()
        assert status["status"] in ("PENDING_RESTART",) or doc["verified"] is False \
            or status.get("reconciled_this_request")


class TestPreflightFailClosed(Base):
    def _store_with_preflight(self, fn):
        return AIAccountStore(
            accounts_dir=self.dir,
            target_key_path=self.target,
            audit_callback=lambda **k: None,
            preflight=fn,
        )

    def test_token_refresh_failure_fails_preflight(self) -> None:
        def failing(cred, meta):
            return {"ok": False, "errors": ["憑證無法取得 token（refresh 失敗）: bad key"],
                    "checks": {"token_mint": "fail"}}
        store = self._store_with_preflight(failing)
        self.add("x", "proj-a")
        checks = store.run_preflight("x")
        assert checks["ok"] is False and "token" in json.dumps(checks)

    def test_vertex_401_403_404_all_fail(self) -> None:
        for code in (401, 403, 404):
            def failing(cred, meta, code=code):
                return {"ok": False,
                        "errors": [f"Vertex AI 存取失敗 (HTTP {code})"],
                        "checks": {"vertex_access": f"fail http {code}"}}
            store = self._store_with_preflight(failing)
            self.add("v", "proj-a")
            assert store.run_preflight("v")["ok"] is False, f"HTTP {code} must FAIL"

    def test_vertex_200_passes(self) -> None:
        def ok(cred, meta):
            return {"ok": True, "errors": [],
                    "checks": {"token_mint": "ok", "project_visible": "ok",
                               "vertex_access": "ok"}}
        store = self._store_with_preflight(ok)
        self.add("good", "proj-a")
        assert store.run_preflight("good")["ok"] is True

    def test_network_unavailable_fails_closed(self) -> None:
        def unavailable(cred, meta):
            return {"ok": False, "status": "unavailable",
                    "errors": ["無法連線 Vertex AI 端點: ConnectionError"],
                    "checks": {"vertex_access": "unavailable"}}
        store = self._store_with_preflight(unavailable)
        self.add("n", "proj-a")
        result = store.run_preflight("n")
        assert result["ok"] is False  # network down != pass

    def test_project_mismatch_structural_fail(self) -> None:
        """credential.project_id != metadata.project_id → ok=false + mismatch error."""
        self.add("mm", "proj-a")
        meta_path = self.dir / "profiles" / "mm" / "metadata.json"
        meta = json.loads(meta_path.read_text())
        meta["project_id"] = "proj-other"
        meta_path.write_text(json.dumps(meta))
        checks = self.store.run_preflight("mm")
        assert checks["ok"] is False
        assert any("project mismatch" in str(e) for e in checks["errors"])

    def test_failed_preflight_blocks_switch(self) -> None:
        def failing(cred, meta):
            return {"ok": False, "errors": ["Vertex AI 存取失敗 (HTTP 403)"], "checks": {}}
        store = self._store_with_preflight(failing)
        self.add("blocked", "proj-a")
        with self.assertRaises(AIAccountError):
            store.switch(name="blocked", actor="owner", confirm=True)


class TestNoClientBypass(Base):
    def test_switch_signature_has_no_skip_parameter(self) -> None:
        import inspect
        sig = inspect.signature(AIAccountStore.switch)
        assert "skip_preflight" not in sig.parameters

    def test_api_request_model_rejects_skip_preflight(self) -> None:
        from app.review.admin import AIAccountActionRequest
        import pydantic
        with self.assertRaises(pydantic.ValidationError):
            AIAccountActionRequest(name="x", confirm=True, skip_preflight=True)


class TestRuntimeStates(Base):
    def test_configured_before_activation(self) -> None:
        self.add("c", "proj-a")
        assert self.store.runtime_status()["status"] == "CONFIGURED"

    def test_pending_restart_then_active(self) -> None:
        self.add("r", "proj-a")
        self.store._activate_for_tests("r")
        status = self.store.runtime_status()
        assert status["status"] == "PENDING_RESTART"

        # Simulate container restart picking up new values:
        with mock.patch.dict(os.environ, {
            "GOOGLE_CLOUD_PROJECT": "proj-a",
            "GOOGLE_CLOUD_LOCATION": "global",
        }):
            verification = self.store.verify_runtime()
        assert verification["verified"] is True
        assert set(verification["checks"]) >= {"client_email", "project_id",
                                               "location", "google_cloud_project_env"}

    def test_verify_reports_mismatch_after_recreate_missing(self) -> None:
        self.add("m", "proj-a")
        self.store._activate_for_tests("m")
        # environment not yet updated (no recreate): verification fails honestly
        with mock.patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "old-project"}):
            v = self.store.verify_runtime()
        assert v["verified"] is False


class TestContracts(Base):
    def test_profile_manager_writes_persistent_mounted_path(self) -> None:
        """The store writes to the host-mounted dir, not container-ephemeral tmp."""
        self.add("persist", "proj-a")
        self.store._activate_for_tests("persist")
        # all state lives under accounts_dir / target path given at construction
        assert (self.dir / "profiles" / "persist" / "credential.json").exists()
        assert (self.dir / "ai-active.env").exists()
        assert self.target.exists()

    def test_secret_leakage_zero(self) -> None:
        self.add("leak", "proj-a")
        self.store._activate_for_tests("leak")
        dumped = json.dumps(self.store.list_profiles()) + \
                 json.dumps(self.store.runtime_status()) + \
                 json.dumps(self.audit)
        assert "BEGIN PRIVATE KEY" not in dumped
        assert "private_key" not in dumped

    def test_audit_records_profiles_and_projects(self) -> None:
        self.add("au", "proj-a")
        self.store._activate_for_tests("au")
        events = [a for a in self.audit if a["action"] == "ai_profile_switched"]
        assert events and events[-1]["payload"]["new_project_id"] == "proj-a"


class TestCreditMetadata(Base):
    """Items 10–20: billing/credit metadata is advisory, never authoritative."""

    def test_google_ai_pro_monthly_profile_metadata(self) -> None:
        self.add("ai-pro-1", "proj-a", credit_type="google_ai_pro_monthly",
                 billing_label="AI Pro 帳戶 1")
        meta = self.store.load_metadata("ai-pro-1")
        assert meta["credit_type"] == "google_ai_pro_monthly"
        assert meta["billing_label"] == "AI Pro 帳戶 1"
        assert AIAccountStore.CREDIT_TYPE_LABELS["google_ai_pro_monthly"] == "Google AI Pro 每月額度"

    def test_gcp_free_trial_profile_metadata(self) -> None:
        self.add("trial-1", "proj-a", credit_type="gcp_free_trial",
                 trial_started_at="2026-08-01", trial_expires_at="2026-11-20")
        meta = self.store.load_metadata("trial-1")
        assert meta["trial_expires_at"] == "2026-11-20"
        assert meta["credit_type"] == "gcp_free_trial"

    def test_trial_expiration_warning_levels(self) -> None:
        from datetime import date, timedelta
        today = date.today()
        cases = [
            (today + timedelta(days=45), "ok"),
            (today + timedelta(days=15), "soft"),
            (today + timedelta(days=5), "warn"),
            (today - timedelta(days=2), "danger"),
        ]
        for exp, expect_level in cases:
            warn = AIAccountStore.trial_warning(
                {"credit_type": "gcp_free_trial",
                 "trial_expires_at": exp.isoformat()})
            assert warn["level"] == expect_level, f"{exp} -> {warn}"

    def test_expired_profile_still_registered(self) -> None:
        """Date expiry never auto-deletes a profile."""
        self.add("trial-old", "proj-a", credit_type="gcp_free_trial",
                 trial_expires_at="2020-01-01")
        assert (self.dir / "profiles" / "trial-old").exists()

    def test_manual_exhausted_state(self) -> None:
        self.add("spent", "proj-a")
        result = self.store.update_credit_status(name="spent",
                                                 credit_status="exhausted",
                                                 actor="owner")
        assert result["credit_status"] == "exhausted"
        assert self.store.load_metadata("spent")["credit_status"] == "exhausted"
        assert any(a["action"] == "ai_profile_credit_status" for a in self.audit)

    def test_credit_metadata_contains_no_secrets(self) -> None:
        self.add("sec-credit", "proj-a", credit_type="gcp_free_trial",
                 billing_label="secret", credit_note="key note")
        dumped = json.dumps(self.store.list_profiles())
        assert "BEGIN PRIVATE KEY" not in dumped and "private_key" not in dumped

    def test_sa_json_cannot_override_credit_metadata(self) -> None:
        """credit fields come from the API, never parsed from the SA JSON."""
        sa_with_junk = sa("proj-a")
        sa_with_junk["credit_type"] = "paid"
        sa_with_junk["credit_status"] = "exhausted"
        self.store.save_profile(name="no-junk", sa_json=sa_with_junk, actor="owner")
        meta = self.store.load_metadata("no-junk")
        assert meta["credit_type"] == "unknown"  # untouched by JSON
        assert meta["credit_status"] == "unknown"

    def test_credit_metadata_cannot_override_project_id(self) -> None:
        """project_id always comes from the credential JSON, never metadata."""
        self.add("locked", "proj-a", credit_type="gcp_free_trial")
        meta_path = self.dir / "profiles" / "locked" / "metadata.json"
        meta = json.loads(meta_path.read_text())
        meta["project_id"] = "proj-evil"
        meta_path.write_text(json.dumps(meta))
        checks = self.store.run_preflight("locked")
        assert checks["ok"] is False
        assert any("project mismatch" in str(e) for e in checks["errors"])

    def test_unknown_credit_does_not_display_fake_amount(self) -> None:
        self.add("unk", "proj-a")
        meta = self.store.load_metadata("unk")
        # API never fabricates a remaining-credit number
        assert "remaining" not in json.dumps(meta)
        assert meta.get("credit_status") == "unknown"

    def test_invalid_credit_type_rejected(self) -> None:
        with self.assertRaises(AIAccountError):
            self.store.save_profile(name="bad-type", sa_json=sa("proj-a"),
                                    credit_type="fraudulent", actor="owner")

    def test_invalid_trial_date_rejected(self) -> None:
        with self.assertRaises(AIAccountError):
            self.store.save_profile(name="bad-date", sa_json=sa("proj-a"),
                                    credit_type="gcp_free_trial",
                                    trial_expires_at="next-month", actor="owner")

    def test_sorted_profiles_order(self) -> None:
        self.add("active1", "proj-a")
        self.store._activate_for_tests("active1")
        self.add("exhausted1", "proj-b", credit_type="google_ai_pro_monthly",
                 credit_status="exhausted", local="b")
        self.add("available1", "proj-c", credit_status="available", local="c")
        self.add("pro1", "proj-d", credit_type="google_ai_pro_monthly", local="d")
        self.add("trial-late", "proj-e", credit_type="gcp_free_trial",
                 trial_expires_at="2027-01-01", local="e")
        self.add("trial-soon", "proj-f", credit_type="gcp_free_trial",
                 trial_expires_at="2026-09-01", local="f")
        items = self.store.list_profiles()
        ordered = AIAccountStore.sorted_profiles(items)
        names = [i["name"] for i in ordered]
        # active first
        assert names[0] == "active1"
        # available before ai-pro
        assert names.index("available1") < names.index("pro1")
        # trial-soon before trial-late
        assert names.index("trial-soon") < names.index("trial-late")
        # exhausted last
        assert names.index("exhausted1") == len(names) - 1

    def test_no_automatic_rotation_logic(self) -> None:
        """Store has no method that auto-switches on quota/error."""
        import inspect
        methods = [n for n, _ in inspect.getmembers(AIAccountStore, inspect.isfunction)]
        assert not any("auto" in m.lower() or "rotate" in m.lower() for m in methods)
        # switch always requires explicit owner confirm
        self.add("noauto", "proj-a")
        result = self.store.switch(name="noauto", actor="owner", confirm=False)
        assert result["stage"] == "preflight"          # nothing activated
        assert self.store.get_active_name() is None


class TestComposeContract(unittest.TestCase):
    """Repo-managed compose wiring: parse YAML, no docker needed (secretless CI)."""

    def _load(self, name):
        import yaml
        return yaml.safe_load(open(Path(__file__).parents[1] / name))

    def test_dev_compose_api_wiring(self) -> None:
        api = self._load("docker-compose.yml")["services"]["api"]
        paths = [e.get("path") for e in api["env_file"]]
        assert any("ai-active.env" in p for p in paths), paths
        assert api["environment"]["GOOGLE_APPLICATION_CREDENTIALS"] == "/run/secrets/gcp-sa.json"
        assert "AI_ACCOUNTS_DIR" in api["environment"]
        mounts = [v for v in api["volumes"] if "ai-accounts" in v]
        assert mounts, "api must mount the ai-accounts subtree"
        # writable mount of ONLY that subtree (not :ro, not whole secrets/)
        for m in mounts:
            assert "/opt/course-transcript/secrets:" not in m.replace("secrets/ai-accounts", "")

    def test_dev_compose_pipeline_wiring(self) -> None:
        pw = self._load("docker-compose.yml")["services"]["pipeline-worker"]
        paths = [e.get("path") for e in pw["env_file"]]
        assert any("ai-active.env" in p for p in paths), paths
        assert pw["environment"]["GOOGLE_APPLICATION_CREDENTIALS"] == "/run/secrets/gcp-sa.json"

    def test_release_compose_wiring(self) -> None:
        services = self._load("docker-compose.release.yml")["services"]
        api = services["api"]
        paths = [e.get("path") for e in api["env_file"]]
        assert any("ai-active.env" in p for p in paths)
        assert api["environment"]["GOOGLE_APPLICATION_CREDENTIALS"] == "/run/secrets/gcp-sa.json"
        pw = services["pipeline-worker"]
        pw_paths = [e.get("path") for e in pw["env_file"]]
        assert any("ai-active.env" in p for p in pw_paths)

    def test_no_docker_socket_anywhere(self) -> None:
        for name in ("docker-compose.yml", "docker-compose.release.yml"):
            d = self._load(name)
            for svc, cfg in d["services"].items():
                vols = cfg.get("volumes") or []
                for v in vols:
                    assert "docker.sock" not in v, f"{name}:{svc} mounts docker.sock"


if __name__ == "__main__":
    unittest.main()
