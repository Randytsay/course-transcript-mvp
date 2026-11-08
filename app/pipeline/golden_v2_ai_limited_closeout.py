"""Separate limited-AI delivery receipts; NEVER mutate Golden QA or Golden job status.

Machine/lexical uncertainty is a disclosed limitation, not a fabricated PASS.
Publishing is a separately authorized, provider-free, source-bound operation.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.subtitles.editor_hardened import parse_srt_strict

SCHEMA = "golden-v2-limited-ai-delivery-v1"
TIER = "AI_REVIEWED_WITH_OPEN_ITEMS_NOT_GOLDEN"
_NAME = re.compile(r"^[^/\\\x00-\x1f]{1,180}$")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _wire(obj: dict) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_file(path: Path, limit: int) -> bytes:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise V2Conflict("missing, symlinked or oversized delivery evidence")
    return path.read_bytes()


def _schema(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS v2_ai_limited_deliveries (
            job_id TEXT NOT NULL,
            manifest_sha TEXT NOT NULL,
            source_sha TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('PREPARED','DELIVERED')),
            manifest_json TEXT NOT NULL,
            remote_receipt_json TEXT,
            prepared_at TEXT NOT NULL,
            delivered_at TEXT,
            PRIMARY KEY(job_id,manifest_sha),
            FOREIGN KEY(job_id) REFERENCES v2_jobs(id)
        )
    """)
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS v2_ai_limited_one_delivered "
               "ON v2_ai_limited_deliveries(job_id) WHERE state='DELIVERED'")


def prepare_limited_manifest(
    *, root: Path, job_id: str, srt_path: Path, txt_path: Path,
    triage_path: Path, remote_names: tuple[str, str],
) -> dict:
    """Produce an immutable proposal from actual source, SRT, TXT and unresolved QA.

    Not a Golden QA bypass, not permission to publish, and not a status update.
    """
    root = Path(root)
    ledger = GoldenV2Ledger.open_existing(root / "ledger.sqlite")
    state = ledger.status(job_id)
    if (state["status"] != "NEEDS_REVIEW"
            or state.get("approved_cents") is None):
        raise V2Conflict("only verified paid jobs held for review can be proposed")
    jobdir = root / "jobs" / job_id
    if not jobdir.is_dir() or jobdir.is_symlink():
        raise V2Conflict("invalid local job directory")
    preflight = json.loads(_check_file(jobdir / "source-preflight.json", 262144))
    source_audio = jobdir / "normalized.flac"
    if (source_audio.is_symlink() or not source_audio.is_file()
            or source_audio.stat().st_size > 2_500_000_000):
        raise V2Conflict("invalid normalized source audio")
    # Streaming hash: multi-hour FLACs must not occupy a full process heap.
    digest = hashlib.sha256()
    with source_audio.open("rb") as audio_stream:
        for chunk in iter(lambda: audio_stream.read(1024 * 1024), b""):
            digest.update(chunk)
    source_sha = digest.hexdigest()
    if source_sha != state["source_sha"] or preflight.get("source_sha256") != source_sha:
        raise V2Conflict("normalized audio is not job source")
    operations = ledger.chirp_windows(job_id)
    if not operations or any(row["status"] != "DONE" or
                             not row["result_sha"] or
                             row["actual_cents"] is None
                             for row in operations):
        raise V2Conflict("unfinished, charged unknown or missing provider receipt")
    if (not isinstance(remote_names, tuple) or len(remote_names) != 2
            or not all(isinstance(n, str) and _NAME.fullmatch(n) for n in remote_names)
            or remote_names[0] == remote_names[1]
            or not remote_names[0].endswith(".srt")
            or not remote_names[1].endswith(".txt")):
        raise V2Conflict("unsafe or incomplete target filenames")
    # Only evidence stored inside this exact immutable job may be used.
    paths = [Path(srt_path), Path(txt_path), Path(triage_path)]
    if any(not path.resolve().is_relative_to(jobdir.resolve()) for path in paths):
        raise V2Conflict("delivery evidence must stay inside the bound job")
    if ("_AI校對_含待複核" not in remote_names[0]
            or remote_names[1] != remote_names[0][:-4]+".txt"):
        raise V2Conflict("AI-limited filenames must explicitly disclose open QA items")
    srt_raw = _check_file(paths[0], 20_000_000)
    txt_raw = _check_file(paths[1], 20_000_000)
    triage_raw = _check_file(paths[2], 10_000_000)
    cues, _ = parse_srt_strict(srt_raw.decode("utf-8-sig"))
    transcript = "\n".join(c["text"] for c in cues) + "\n"
    if txt_raw.decode("utf-8-sig").replace("\r\n", "\n") != transcript:
        raise V2Conflict("SRT and TXT do not contain identical ordered words")
    triage = json.loads(triage_raw)
    if (triage.get("job_id") != job_id or
            triage.get("srt_sha256") != _sha(srt_raw) or
            triage.get("total_acoustic_gaps") != len(triage.get("all_acoustic_gaps", []))):
        raise V2Conflict("QA/triage does not match source SRT and job")
    # Unknown gaps remain explicit; never call them PASS or silence.
    classifications = {}
    for gap in triage["all_acoustic_gaps"]:
        kind = gap.get("acoustic_disposition")
        if kind not in {"SPEECH_CONFIRMED", "NONSPEECH_CONFIRMED", "REVIEW"}:
            raise V2Conflict("unknown acoustic classification")
        classifications[kind] = classifications.get(kind, 0) + 1
    manifest = {
        "schema": SCHEMA, "quality_tier": TIER, "job_id": job_id,
        "ledger_revision": state["revision"],
        "golden_job_status": "NEEDS_REVIEW",
        "golden_qa_claim": "NOT_CLAIMED",
        "human_listening_claim": "NOT_CLAIMED",
        "source_sha256": source_sha,
        "source_file_id": preflight["drive_observation"]["file_id"],
        "source_version": str(preflight["drive_observation"]["version"]),
        "srt_sha256": _sha(srt_raw), "txt_sha256": _sha(txt_raw),
        "triage_sha256": _sha(triage_raw),
        "provider_result_shas": [r["result_sha"] for r in operations],
        "cue_count": len(cues),
        "acoustic_classification_counts": classifications,
        "open_items": [
            {"start_ms": g["start_ms"], "end_ms": g["end_ms"],
             "disposition": g["acoustic_disposition"],
             "classification": g["classification"]}
            for g in triage["all_acoustic_gaps"]
            if g["acoustic_disposition"] == "REVIEW" or
            (g["acoustic_disposition"] == "SPEECH_CONFIRMED" and
             g["classification"] != "ALREADY_HAS_SRT_TEXT_CHECK_SEMANTIC")
        ],
        "output_names": list(remote_names),
        "source_srt_filename": Path(srt_path).name,
        "source_txt_filename": Path(txt_path).name,
        "source_triage_filename": Path(triage_path).name,
        "limitations": [
            "Unresolved acoustic/semantic evidence has not been certified.",
            "SRT/TXT are readable AI-assisted drafts, not Golden PASS.",
            "No human listening was attested.",
            "This record does not change original Golden pipeline stage states.",
        ],
        "publication_granted": False,
    }
    manifest["manifest_sha256"] = _sha(_wire(manifest))
    return manifest


def register_proposal(root: Path, manifest: dict) -> dict:
    """CAS-register a proposal without altering provider or Golden stage tables."""
    ledger = GoldenV2Ledger.open_existing(Path(root) / "ledger.sqlite")
    if manifest.get("schema") != SCHEMA or manifest.get("quality_tier") != TIER:
        raise V2Conflict("invalid delivery proposal")
    canonical = dict(manifest)
    digest = canonical.pop("manifest_sha256", None)
    if digest != _sha(_wire(canonical)) or canonical.get("publication_granted") is not False:
        raise V2Conflict("forged or altered proposal manifest")
    state = ledger.status(canonical["job_id"])
    if (state["revision"] != canonical["ledger_revision"] or
            state["source_sha"] != canonical["source_sha256"] or
            state["status"] != "NEEDS_REVIEW"):
        raise V2Conflict("stale Golden ledger revision / source")
    with ledger._tx() as db:
        _schema(db)
        locked_job = db.execute(
            "SELECT revision,source_sha,status FROM v2_jobs WHERE id=?",
            (canonical["job_id"],)
        ).fetchone()
        if (locked_job is None or
                locked_job["revision"] != canonical["ledger_revision"] or
                locked_job["source_sha"] != canonical["source_sha256"] or
                locked_job["status"] != "NEEDS_REVIEW"):
            raise V2Conflict("concurrent Golden job revision changed")
        delivered = db.execute(
            "SELECT manifest_sha FROM v2_ai_limited_deliveries "
            "WHERE job_id=? AND state='DELIVERED'", (canonical["job_id"],)
        ).fetchone()
        if delivered and delivered["manifest_sha"] != digest:
            raise V2Conflict("different delivery already finalized")
        existing = db.execute(
            "SELECT state,manifest_json FROM v2_ai_limited_deliveries "
            "WHERE job_id=? AND manifest_sha=?", (canonical["job_id"], digest)
        ).fetchone()
        content = _wire(manifest).decode()
        if existing and existing["manifest_json"] != content:
            raise V2Conflict("proposal collision")
        if existing is None:
            db.execute(
                "INSERT INTO v2_ai_limited_deliveries "
                "(job_id,manifest_sha,source_sha,state,manifest_json,prepared_at) "
                "VALUES(?,?,?,'PREPARED',?,?)",
                (canonical["job_id"], digest, canonical["source_sha256"],
                 content, _now())
            )
    return {"job_id": canonical["job_id"], "proposal_sha256": digest,
            "state": existing["state"] if existing else "PREPARED",
            "golden_stage_changed": False}


def finalize_readback(
    *, root: Path, manifest: dict, remote_files: list[dict],
    owner_grant: dict, signed_grant_verifier, readback_verifier,
) -> dict:
    """Only authenticated owner path may call after actual Drive readback.

    readback_verifier(name, drive_file_id, expected_sha256) MUST fetch the
    actual remote bytes and verify digest under the same exclusive lock.
    """
    digest = manifest["manifest_sha256"]
    if not isinstance(owner_grant, dict) or not callable(signed_grant_verifier) or not callable(readback_verifier):
        raise V2Conflict("trusted owner grant and remote verifier required")
    required = {
        "job_id": manifest["job_id"], "manifest_sha256": digest,
        "quality_tier": TIER,
        "source_sha256": manifest["source_sha256"],
        "output_names": manifest["output_names"],
        "approve_delivery": True,
    }
    if (any(owner_grant.get(k) != v for k, v in required.items())
            or not str(owner_grant.get("actor") or "").strip()
            or not str(owner_grant.get("reference") or "").strip()
            or not signed_grant_verifier(owner_grant)):
        raise V2Conflict("separately signed, source-bound owner grant required")
    expected = dict(zip(manifest["output_names"],
                        [manifest["srt_sha256"], manifest["txt_sha256"]]))
    if (not isinstance(remote_files, list) or len(remote_files) != 2 or
            {f.get("name") for f in remote_files if isinstance(f, dict)} != set(expected)):
        raise V2Conflict("delivery files differ from authorized manifest")
    for item in remote_files:
        name, drive_id = item["name"], item.get("id")
        if (not isinstance(drive_id, str) or not drive_id.strip() or
                not readback_verifier(name, drive_id, expected[name])):
            raise V2Conflict("Drive readback checksum/identity failed")
    ledger = GoldenV2Ledger.open_existing(Path(root) / "ledger.sqlite")
    with ledger._tx() as db:
        _schema(db)
        row = db.execute("SELECT * FROM v2_ai_limited_deliveries WHERE "
                         "job_id=? AND manifest_sha=?",
                         (manifest["job_id"], digest)).fetchone()
        job = db.execute("SELECT * FROM v2_jobs WHERE id=?",
                         (manifest["job_id"],)).fetchone()
        if (row is None or row["manifest_json"] != _wire(manifest).decode()
                or job is None or job["source_sha"] != manifest["source_sha256"]
                or job["revision"] != manifest["ledger_revision"]
                or job["status"] != "NEEDS_REVIEW"):
            raise V2Conflict("stale or mismatched prepared delivery")
        receipt = {"schema": SCHEMA, "quality_tier": TIER,
                   "job_id": manifest["job_id"], "manifest_sha256": digest,
                   "files": remote_files, "readback_verified": True,
                   "owner_grant_sha256": _sha(_wire(owner_grant)),
                   "golden_status_unchanged": "NEEDS_REVIEW"}
        saved = _wire(receipt).decode()
        if row["state"] == "DELIVERED":
            if row["remote_receipt_json"] != saved:
                raise V2Conflict("non-idempotent receipt conflicts")
        else:
            db.execute("UPDATE v2_ai_limited_deliveries SET state='DELIVERED',"
                       "remote_receipt_json=?,delivered_at=? WHERE "
                       "job_id=? AND manifest_sha=? AND state='PREPARED'",
                       (saved, _now(), manifest["job_id"], digest))
    return receipt
