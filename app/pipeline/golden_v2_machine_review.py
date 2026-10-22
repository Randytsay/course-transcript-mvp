"""Read-only machine audio evidence; never an owner-listening attestation.

Receipts are native Gemini STOP responses, not text-only model votes. The
trusted caller supplies a provider-operation verifier backed by its operation
journal; a local JSON file alone cannot prove a request really included audio.
This module makes no provider/Drive/ledger calls and writes no artifacts.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path
import subprocess
from typing import Any, Callable
import wave

RECEIPT_SCHEMA = "golden-v2-machine-audio-receipt-v1"
BUNDLE_SCHEMA = "golden-v2-machine-audio-bundle-v1"
ACTOR_KIND = "MACHINE_AUDIO_REVIEW"


class MachineReviewError(ValueError):
    """Missing, invalid, stale, or unverified machine evidence: fail closed."""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_token(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise MachineReviewError("literal lowercase SHA256 required")
    return value


@contextmanager
def _open_regular(path: Path):
    """Walk with dirfds: reject symlink ancestors and final-component races."""
    path = Path(path).absolute()
    if ".." in path.parts:
        raise MachineReviewError("unsafe artifact path")
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            next_directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                     dir_fd=directory)
            os.close(directory)
            directory = next_directory
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise MachineReviewError("artifact must be a regular file")
            yield stream
    except OSError as exc:
        raise MachineReviewError("missing or unsafe artifact") from exc
    finally:
        os.close(directory)


def _bound_bytes(root: Path, kind: str, digest: str, suffix: str = "json") -> bytes:
    _sha_token(digest)
    with _open_regular(Path(root) / f"{kind}-{digest}.{suffix}") as stream:
        data = stream.read()
    if _digest(data) != digest:
        raise MachineReviewError("content SHA mismatch")
    return data


def load_verified_receipt(
    *, evidence_root: Path, receipt_sha256: str, normalized_audio: Path,
    source_sha256: str, merged_sha256: str,
    operation_verifier: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Verify one physical <=120s PCM clip and an independently trusted operation.

    Fixed filenames: receipt-<sha>.json, raw-<sha>.json, prompt-<sha>.txt,
    clip-<sha>.wav. Input clips must be mono 16kHz signed 16-bit PCM WAV.
    Operation verifiers return the exact binding passed in plus a SHA of their
    trusted operation evidence; they must verify actual audio submission and
    successful completion, not echo receipt claims. No default verifier exists.
    """
    receipt = json.loads(_bound_bytes(evidence_root, "receipt", receipt_sha256))
    if (receipt.get("schema") != RECEIPT_SCHEMA
            or receipt.get("source_sha256") != source_sha256
            or receipt.get("merged_sha256") != merged_sha256):
        raise MachineReviewError("receipt source/merged binding mismatch")
    clip = receipt["clip"]
    start, end = clip["start_ms"], clip["end_ms"]
    with wave.open(io.BytesIO(_bound_bytes(evidence_root, "clip", clip["sha256"], "wav"))) as stream:
        if (stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) != (1, 2, 16000):
            raise MachineReviewError("clip must be 16kHz mono PCM")
        pcm = stream.readframes(stream.getnframes())
    if _digest(pcm) != clip["pcm_sha256"] or len(pcm) != (end - start) * 32:
        raise MachineReviewError("clip PCM certificate mismatch")
    with _open_regular(normalized_audio) as source:
        source_digest = hashlib.file_digest(source, "sha256").hexdigest()
        if source_digest != _sha_token(source_sha256):
            raise MachineReviewError("normalized source SHA mismatch")
        source.seek(0)
        result = subprocess.run([
            "ffmpeg", "-nostdin", "-v", "error", "-ss", f"{start / 1000:.3f}",
            "-i", f"/proc/self/fd/{source.fileno()}", "-t", f"{(end - start) / 1000:.3f}",
            "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1",
        ], capture_output=True, check=True, timeout=90, pass_fds=(source.fileno(),))
    if result.stdout != pcm:
        raise MachineReviewError("clip PCM differs from normalized source interval")
    prompt = _bound_bytes(evidence_root, "prompt", receipt["prompt_sha256"], "txt")
    raw = json.loads(_bound_bytes(evidence_root, "raw", receipt["raw_response_sha256"]))
    operation = {
        "status": "SUCCEEDED", "operation_id": receipt["operation_id"],
        "model": receipt["model"], "source_sha256": source_sha256,
        "merged_sha256": merged_sha256, "input_audio_sha256": clip["sha256"],
        "raw_response_sha256": receipt["raw_response_sha256"],
        "prompt_sha256": receipt["prompt_sha256"], "usage": receipt["usage"],
    }
    if not callable(operation_verifier):
        raise MachineReviewError("trusted provider operation verifier required")
    verified = operation_verifier(operation)
    if (not isinstance(verified, dict)
            or any(verified.get(key) != value for key, value in operation.items())
            or not verified.get("operation_evidence_sha256")):
        raise MachineReviewError("provider operation is not verified")
    review = json.loads(raw["candidates"][0]["content"]["parts"][0]["text"])
    return {
        **receipt, "receipt_sha256": receipt_sha256, "review": review,
        "reviewed_audio_window_ms": [start, end],
        "operation_evidence_sha256": verified["operation_evidence_sha256"],
        "actor_kind": ACTOR_KIND, "human_listening_attested": False,
        "model_timing_authority": False,
    }
