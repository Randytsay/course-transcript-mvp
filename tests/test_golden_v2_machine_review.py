"""Offline, synthetic fixtures only: no provider, Drive, or live ledger access."""
from __future__ import annotations

from array import array
import hashlib
import importlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
import wave


def wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def wav_bytes(pcm):
    target = io.BytesIO()
    with wave.open(target, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(pcm)
    return target.getvalue()


class MachineReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.evidence = self.root / "evidence"
        self.evidence.mkdir()
        self.audio = self.root / "normalized.flac"
        self.pcm = array("h", ((i * 17) % 30000 - 15000
                               for i in range(5 * 16000))).tobytes()
        self.set_source(self.pcm)
        self.merged = {
            "source_sha": self.source_sha, "timing_authority": "chirp_3",
            "duration_ms": 5000, "words": [
                {"word": "復次", "start_ms": 1000, "end_ms": 1500},
                {"word": "法門", "start_ms": 2000, "end_ms": 2600},
            ],
        }
        self.merged_sha = sha(wire(self.merged))
        self.operations = {}
        self.verify_calls = []
        self.review = {"candidate_verdicts": [{
            "candidate_index": 0, "candidate_sha256": "c" * 64,
            "verdict": "KEEP_CHIRP", "heard_quote": "復次",
            "reason": "The first phrase is heard as 復次, not 復此.",
        }]}
        self.receipt = self.make_receipt(500, 3000, self.review)
        self.receipt_sha = self.put("receipt", wire(self.receipt))

    def set_source(self, pcm):
        subprocess.run([
            "ffmpeg", "-nostdin", "-v", "error", "-f", "s16le", "-ar", "16000",
            "-ac", "1", "-i", "pipe:0", "-c:a", "flac", "-y", str(self.audio),
        ], input=pcm, check=True, capture_output=True, timeout=30)
        self.source_sha = sha(self.audio.read_bytes())

    def put(self, kind, data, suffix="json"):
        digest = sha(data)
        (self.evidence / f"{kind}-{digest}.{suffix}").write_bytes(data)
        return digest

    def make_receipt(self, start, end, review):
        clip_pcm = self.pcm[start * 32:end * 32]
        clip_sha = self.put("clip", wav_bytes(clip_pcm), "wav")
        prompt_sha = self.put("prompt", b"FAKE TEST: listen to attached audio; review exact candidates.", "txt")
        op_id = f"fake-operation-{len(self.operations)}"
        usage = {"promptTokenCount": 1788, "candidatesTokenCount": 885}
        raw = {
            "responseId": op_id, "modelVersion": "fake-audio-review-model",
            "usageMetadata": usage,
            "candidates": [{"finishReason": "STOP", "content": {
                "parts": [{"text": wire(review).decode()}]}}],
        }
        raw_sha = self.put("raw", wire(raw))
        receipt = {
            "schema": "golden-v2-machine-audio-receipt-v1",
            "source_sha256": self.source_sha, "merged_sha256": self.merged_sha,
            "model": raw["modelVersion"], "operation_id": op_id,
            "raw_response_sha256": raw_sha, "prompt_sha256": prompt_sha,
            "usage": usage, "clip": {
                "sha256": clip_sha, "pcm_sha256": sha(clip_pcm),
                "start_ms": start, "end_ms": end,
            },
        }
        operation = {
            "status": "SUCCEEDED", "operation_id": op_id,
            "model": raw["modelVersion"], "source_sha256": self.source_sha,
            "merged_sha256": self.merged_sha, "input_audio_sha256": clip_sha,
            "raw_response_sha256": raw_sha, "prompt_sha256": prompt_sha, "usage": usage,
        }
        self.operations[op_id] = {
            **operation, "operation_evidence_sha256": sha(wire(operation)),
        }
        return receipt

    def verifier(self, operation):
        self.verify_calls.append(operation)
        return self.operations.get(operation["operation_id"])

    def module(self):
        name = "app.pipeline.golden_v2_machine_review"
        self.assertIsNotNone(importlib.util.find_spec(name),
                             "machine evidence verifier has not been implemented")
        return importlib.import_module(name)

    def load(self, **overrides):
        options = {
            "evidence_root": self.evidence, "receipt_sha256": self.receipt_sha,
            "normalized_audio": self.audio, "source_sha256": self.source_sha,
            "merged_sha256": self.merged_sha, "operation_verifier": self.verifier,
        }
        options.update(overrides)
        return self.module().load_verified_receipt(**options)

    def test_rejects_symlinks_at_every_content_addressed_artifact(self):
        names = [
            f"receipt-{self.receipt_sha}.json",
            f"raw-{self.receipt['raw_response_sha256']}.json",
            f"prompt-{self.receipt['prompt_sha256']}.txt",
            f"clip-{self.receipt['clip']['sha256']}.wav",
        ]
        for name in names:
            with self.subTest(name=name):
                path = self.evidence / name
                outside = self.root / name
                path.rename(outside)
                path.symlink_to(outside)
                try:
                    with self.assertRaises(self.module().MachineReviewError):
                        self.load()
                finally:
                    path.unlink()
                    outside.rename(path)

    def test_rejects_symlinked_evidence_roots_and_source(self):
        link = self.root / "linked"
        link.symlink_to(self.evidence, target_is_directory=True)
        with self.assertRaises(self.module().MachineReviewError):
            self.load(evidence_root=link)
        source_link = self.root / "source-link.flac"
        source_link.symlink_to(self.audio)
        with self.assertRaises(self.module().MachineReviewError):
            self.load(normalized_audio=source_link)

    def test_rejects_nonliteral_sha_tokens_before_any_file_lookup(self):
        for token in ("../elsewhere", "a" * 63, "A" * 64, "a" * 64 + "/x", None, True):
            with self.subTest(token=token):
                with self.assertRaisesRegex(self.module().MachineReviewError, "SHA"):
                    self.load(receipt_sha256=token)

    def test_loads_raw_audio_receipt_bound_to_real_pcm_and_verified_operation(self):
        result = self.load()
        self.assertEqual(result["receipt_sha256"], self.receipt_sha)
        self.assertEqual(result["review"], self.review)
        self.assertEqual(result["reviewed_audio_window_ms"], [500, 3000])
        self.assertEqual(result["operation_evidence_sha256"],
                         self.operations[self.receipt["operation_id"]]["operation_evidence_sha256"])
        self.assertEqual(self.verify_calls[0]["input_audio_sha256"],
                         self.receipt["clip"]["sha256"])
        self.assertEqual(result["actor_kind"], "MACHINE_AUDIO_REVIEW")
        self.assertIs(result["human_listening_attested"], False)
        self.assertIs(result["model_timing_authority"], False)


if __name__ == "__main__":
    unittest.main()
