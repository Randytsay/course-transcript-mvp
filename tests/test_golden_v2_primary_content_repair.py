"""Offline synthetic receipts only; no provider clients, ledger or live data."""
import copy
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.jobs.golden_v2_ledger import V2Conflict
from app.providers.gemini_primary_audio import (
    INSTRUCTION, MODEL, PROMPT_VERSION, _paid_window_slot_digest,
)
from app.pipeline.golden_v2_pricing import PRICE_VERSION, gemini_usage_cents
from app.providers.golden_v2_versioned_artifacts import write_versioned

MODULE = "app.pipeline.golden_v2_primary_content_repair"
BOUNDS = [(2690, 3590), (3580, 4480), (4470, 5370), (5360, 5390)]
ORIGINAL_BOUNDS = [(0, 2700), (2690, 5390), (5380, 8080),
                   (8070, 10770), (10760, 10781.82)]


def wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


class PrimaryContentRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = sha(b"synthetic audio, not a production recording")
        self.job = "v2p_synthetic_content_repair"
        self.original_records = []
        self.original_evidence = []
        windows = []
        for index, (start, end) in enumerate(ORIGINAL_BOUNDS):
            raw = self.make_raw(index, start, end, f"原窗{index}完整內容。")
            if index == 1:
                # Regression: legacy COMPLETE is not proof of STOP/completeness.
                raw["finish_reason"] = "MAX_TOKENS"
                raw["raw_response"]["candidates"][0]["finish_reason"] = "MAX_TOKENS"
            self.original_records.append(raw)
            windows.append(raw["identity"]["window"])
            self.original_evidence.append(self.store_raw(raw))
        lineage = {"schema": "golden-v2-source-lineage-v1",
                   "normalized_sha256": self.source,
                   "original_sha256": sha(b"synthetic original")}
        lineage_sha = self.store("golden-v2/source-lineage.json", lineage)
        self.original = {
            "schema": "golden-v2-primary-stage-v1", "job_id": self.job,
            "source_sha256": self.source,
            "source_lineage_sha256": lineage_sha,
            "mode": "gemini_3_8_audio_first", "windows": windows,
            "window_evidence": self.original_evidence,
            "assembled": {"text": "legacy truncated assembly is NOT reference"},
            "actual_cents": gemini_usage_cents(self.original_records),
            "ready_for_chirp": False, "golden_approved": False,
        }
        self.original_sha = self.store("golden-v2/primary-stage.json", self.original)
        self.replacement_shas = []
        self.replacements = []
        self.replacement_raws = []
        for index, (start, end) in enumerate(BOUNDS):
            # Native request indices intentionally differ from assembly order.
            raw = self.make_raw(40 + index, start, end, f"修復窗{index}完整內容。")
            evidence = self.store_raw(raw)
            request = {
                "prompt": INSTRUCTION, "reference_terms": "",
                "config": {"thinking_config": {"thinking_level": "MEDIUM"}},
                "clip": {"source_sha256": self.source,
                         "clip_sha256": sha(f"synthetic clip {index}".encode()),
                         **{k: raw["identity"]["window"][k] for k in
                            ("start_seconds", "end_seconds", "gcs_uri", "mime_type")}},
            }
            identity = {
                "model": MODEL, "source_sha256": self.source,
                "window": copy.deepcopy(raw["identity"]["window"]),
                "prompt_sha256": sha(request["prompt"].encode()),
                "config_sha256": sha(wire(request["config"])),
            }
            receipt = {
                "schema": "golden-v2-primary-content-repair-receipt-v1",
                "job_id": self.job,
                "original_primary_evidence_sha256": self.original_sha,
                "replaced_window_index": 1, "identity": identity,
                "request": request, "raw_evidence": evidence,
                "actual_cents": gemini_usage_cents([raw]),
                "price_version": PRICE_VERSION,
            }
            self.replacements.append(receipt)
            self.replacement_raws.append(raw)
            self.replacement_shas.append(self.store_receipt(receipt))

    def api(self):
        self.assertIsNotNone(importlib.util.find_spec(MODULE),
                             "pure primary content repair module is missing")
        return importlib.import_module(MODULE)

    def make_raw(self, index, start, end, text):
        window = {"index": index, "start_seconds": start, "end_seconds": end,
                  "gcs_uri": f"gs://synthetic-private/clip-{index}.flac",
                  "mime_type": "audio/flac"}
        return {
            "identity": {"model": MODEL, "prompt_version": PROMPT_VERSION,
                         "source_sha256": self.source, "window": window,
                         "context_sha256": sha(b"")},
            "status": "COMPLETE", "text": text, "finish_reason": "STOP",
            "human_review_required": False,
            "usage_metadata": {"prompt_token_count": 100,
                               "candidates_token_count": 20, "thoughts_token_count": 0},
            "raw_response": {"candidates": [{"finish_reason": "STOP",
                              "content": {"parts": [{"text": text}]}}]},
        }

    def store(self, relative, payload):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        data = wire(payload)
        path.write_bytes(data)
        return sha(data)

    def store_raw(self, raw):
        digest = self.store(
            f"gemini-primary/{_paid_window_slot_digest(raw['identity'])}.json", raw)
        return {"identity": copy.deepcopy(raw["identity"]), "raw_audit_sha256": digest,
                "finish_reason": raw["finish_reason"],
                "usage_metadata": copy.deepcopy(raw["usage_metadata"])}

    def store_receipt(self, receipt):
        digest = sha(wire(receipt))
        self.store(f"golden-v2/primary-content-repair-receipts/{digest}.json", receipt)
        return digest

    def options(self):
        return {"job_dir": self.root, "job_id": self.job, "source_sha": self.source,
                "original_sha": self.original_sha, "replaced_window_index": 1,
                "replacement_bounds": BOUNDS}

    def build(self, **kwargs):
        args = {**self.options(), "replacement_receipt_shas": self.replacement_shas,
                "seam_receipt_shas": []}
        args.update(kwargs)
        return self.api().build_primary_content_repair(**args)

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def test_missing_seams_holds_without_dropping_or_reidentifying_raw_text(self):
        before = self.snapshot()
        report = self.build()
        self.assertEqual(report["schema"], "golden-v2-primary-content-repair-v1")
        self.assertEqual(report["original_primary_evidence_sha256"], self.original_sha)
        self.assertFalse(report["ready_for_chirp"])
        self.assertFalse(report["golden_approved"])
        self.assertEqual(report["status"], "HOLD_SEAM_EVIDENCE_REQUIRED")
        expected = [self.original_records[0], *self.replacement_raws,
                    *self.original_records[2:]]
        self.assertEqual(report["assembled"]["text"],
                         "\n\n".join(raw["text"] for raw in expected))
        self.assertEqual(len(report["assembled"]["review_items"]), 7)
        self.assertEqual(report["retained_window_evidence"],
                         [self.original_evidence[i] for i in (0, 2, 3, 4)])
        self.assertEqual(report["replacement_receipt_sha256"], self.replacement_shas)
        self.assertEqual(report["replacement_receipts"], self.replacements)
        self.assertEqual([w["index"] for w in report["windows"]], [0, 40, 41, 42, 43, 2, 3, 4])
        self.assertEqual(self.snapshot(), before, "builder must never write evidence")

    def test_raw_sha_missing_and_symlink_fail_closed(self):
        path = self.root / "gemini-primary" / (
            _paid_window_slot_digest(self.original_records[0]["identity"]) + ".json")
        pristine = path.read_bytes()
        changed = copy.deepcopy(self.original_records[0])
        changed["text"] = "unapproved text"
        path.write_bytes(wire(changed))
        with self.assertRaises(V2Conflict):
            self.build()
        path.unlink()
        with self.assertRaises(V2Conflict):
            self.build()
        target = self.root / "outside-raw.json"
        target.write_bytes(pristine)
        path.symlink_to(target)
        with self.assertRaises(V2Conflict):
            self.build()


if __name__ == "__main__":
    unittest.main()
