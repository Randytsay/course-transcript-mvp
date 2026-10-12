"""Provider-free concurrent paid-call barriers for both V2 Gemini recognizers.

Exercises exclusive reservation with real filesystem writes and two threads.
No cloud connections or paid calls are made; fake models only.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import json
import unittest
from unittest.mock import Mock, patch

from app.providers import gemini_primary_audio as primary_adapter

from app.providers.gemini_primary_audio import (
    PrimaryTranscriptError, run_window,
)
from app.providers.gemini_third_transcribe import run_third_window


def sample_window() -> dict:
    return {
        "index": 0, "start_seconds": 0, "end_seconds": 30,
        "gcs_uri": "gs://private-audio/known-immutable.flac",
        "mime_type": "audio/flac",
    }


def fake_response():
    return SimpleNamespace(
        text="復次文殊師利，男身具七寶。",
        candidates=[SimpleNamespace(finish_reason="STOP")],
        usage_metadata={"prompt_token_count": 10, "candidates_token_count": 20},
        parts=[],
    )


class PaidWindowBarrierTests(unittest.TestCase):
    def _args(self, kind: str, *, root: Path, client) -> dict:
        args = {
            "client": client, "staged_window": sample_window(),
            "source_sha256": "a" * 64, "job_dir": root,
            "approval_token": "mock-verified-owner",
            "approval_validator": lambda token, sha, model, window: (
                token == "mock-verified-owner" and sha == "a" * 64
                and model in {"gemini-3.8-flash", "gemini-3.5-transcribe"}
                and window == sample_window()
            ),
            "part_factory": lambda uri, mime: {"uri": uri, "mime": mime},
        }
        if kind == "third":
            args.update(enabled=True, config_factory=lambda options: options)
        return args

    @staticmethod
    def _run(kind: str, args: dict):
        return run_window(**args) if kind == "primary" else run_third_window(**args)

    def test_parallel_submissions_have_exactly_one_provider_call(self):
        for kind in ("primary", "third"):
            with self.subTest(kind=kind), TemporaryDirectory() as tmp:
                started, release = Event(), Event()

                def provider(**kwargs):
                    started.set()
                    if not release.wait(timeout=5):
                        raise TimeoutError("test could not release provider")
                    return fake_response()

                api = Mock(side_effect=provider)
                client = SimpleNamespace(models=SimpleNamespace(generate_content=api))
                args = self._args(kind, root=Path(tmp), client=client)
                with ThreadPoolExecutor(max_workers=2) as pool:
                    first = pool.submit(self._run, kind, args)
                    self.assertTrue(started.wait(timeout=5), "first request did not start")
                    try:
                        with self.assertRaises(PrimaryTranscriptError):
                            self._run(kind, args)
                    finally:
                        release.set()
                    result = first.result(timeout=10)
                self.assertEqual(result["status"], "COMPLETE")
                self.assertEqual(api.call_count, 1)
                self.assertEqual(self._run(kind, args), result)
                self.assertEqual(api.call_count, 1)
                audits = list(Path(tmp).rglob("*.json"))
                self.assertEqual(len(audits), 1)

    def test_corrupt_or_incomplete_existing_reservation_never_retries(self):
        for kind in ("primary", "third"):
            with self.subTest(kind=kind), TemporaryDirectory() as tmp:
                api = Mock(return_value=fake_response())
                client = SimpleNamespace(models=SimpleNamespace(generate_content=api))
                args = self._args(kind, root=Path(tmp), client=client)
                self._run(kind, args)
                record_path, = Path(tmp).rglob("*.json")
                for artifact in ('', '{"identity":', '{"status":"REQUEST_STARTED"}'):
                    with self.subTest(kind=kind, artifact=artifact):
                        record_path.write_text(artifact, encoding="utf-8")
                        with self.assertRaises(PrimaryTranscriptError):
                            self._run(kind, args)
                        self.assertEqual(api.call_count, 1)

    def test_same_audio_cannot_be_rebilled_by_changing_prompt_or_options(self):
        for kind in ("primary", "third"):
            with self.subTest(kind=kind), TemporaryDirectory() as tmp:
                api = Mock(return_value=fake_response())
                client = SimpleNamespace(models=SimpleNamespace(generate_content=api))
                args = self._args(kind, root=Path(tmp), client=client)
                first = self._run(kind, args)
                self.assertEqual(first["status"], "COMPLETE")
                changed = dict(args)
                if kind == "primary":
                    changed["reference_terms"] = "同一錄音不同的經文提示"
                else:
                    changed["custom_vocabulary"] = ["文殊師利"]
                with self.assertRaises(PrimaryTranscriptError):
                    self._run(kind, changed)
                self.assertEqual(api.call_count, 1)
                self.assertEqual(len(list(Path(tmp).rglob("*.json"))), 1)

    def test_historical_paid_receipt_filename_blocks_new_model_call(self):
        for kind in ("primary", "third"):
            with self.subTest(kind=kind), TemporaryDirectory() as tmp:
                api = Mock(return_value=fake_response())
                client = SimpleNamespace(models=SimpleNamespace(generate_content=api))
                args = self._args(kind, root=Path(tmp), client=client)
                recorded = self._run(kind, args)
                current, = Path(tmp).rglob("*.json")
                old_digest = hashlib.sha256(
                    json.dumps(recorded["identity"], ensure_ascii=False,
                               sort_keys=True).encode("utf-8")
                ).hexdigest()
                archived = current.with_name(f"{old_digest}.json")
                archived.write_bytes(current.read_bytes())
                current.unlink()
                with self.assertRaises(PrimaryTranscriptError):
                    self._run(kind, args)
                self.assertEqual(api.call_count, 1)
                self.assertEqual(list(archived.parent.glob("*.json")), [archived])

    def test_malformed_historical_receipt_fails_before_paid_call(self):
        for kind in ("primary", "third"):
            with self.subTest(kind=kind), TemporaryDirectory() as tmp:
                api = Mock(return_value=fake_response())
                client = SimpleNamespace(models=SimpleNamespace(generate_content=api))
                args = self._args(kind, root=Path(tmp), client=client)
                folder = Path(tmp) / ("gemini-primary" if kind == "primary" else "gemini-third")
                folder.mkdir()
                (folder / "old-incomplete.json").write_text("{", encoding="utf-8")
                with self.assertRaises(PrimaryTranscriptError):
                    self._run(kind, args)
                api.assert_not_called()

    def test_paid_slot_and_completion_fsync_parent_directory(self):
        with TemporaryDirectory() as tmp:
            api = Mock(return_value=fake_response())
            client = SimpleNamespace(models=SimpleNamespace(generate_content=api))
            args = self._args("primary", root=Path(tmp), client=client)
            with patch.object(
                primary_adapter, "_fsync_audit_directory",
                wraps=primary_adapter._fsync_audit_directory,
            ) as sync:
                self._run("primary", args)
            audit_dir = Path(tmp) / "gemini-primary"
            self.assertGreaterEqual(
                sum(call.args[0] == audit_dir for call in sync.call_args_list), 2,
                "reservation and COMPLETE rename must both fsync the audit directory",
            )
            api.assert_called_once()

    def test_audit_directory_fsync_failure_blocks_paid_request(self):
        for kind in ("primary", "third"):
            with self.subTest(kind=kind), TemporaryDirectory() as tmp:
                api = Mock(return_value=fake_response())
                client = SimpleNamespace(models=SimpleNamespace(generate_content=api))
                args = self._args(kind, root=Path(tmp), client=client)
                folder = Path(tmp) / ("gemini-primary" if kind == "primary" else "gemini-third")
                real_sync = primary_adapter._fsync_audit_directory

                def sync_then_fail(path):
                    if path == folder:
                        raise OSError("simulated directory fsync failure")
                    return real_sync(path)

                with patch.object(primary_adapter, "_fsync_audit_directory", side_effect=sync_then_fail):
                    with self.assertRaises(OSError):
                        self._run(kind, args)
                api.assert_not_called()
                self.assertEqual(len(list(folder.glob("*.json"))), 1)
                with self.assertRaises(PrimaryTranscriptError):
                    self._run(kind, args)
                api.assert_not_called()

    def test_approval_rejection_creates_no_barrier(self):
        for kind in ("primary", "third"):
            with self.subTest(kind=kind), TemporaryDirectory() as tmp:
                api = Mock()
                client = SimpleNamespace(models=SimpleNamespace(generate_content=api))
                args = self._args(kind, root=Path(tmp), client=client)
                args["approval_validator"] = lambda *unused: False
                with self.assertRaises(PrimaryTranscriptError):
                    self._run(kind, args)
                api.assert_not_called()
                self.assertFalse(list(Path(tmp).rglob("*.json")))


if __name__ == "__main__":
    unittest.main()
