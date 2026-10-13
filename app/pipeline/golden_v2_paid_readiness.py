"""Read-only, fail-closed preflight before connecting a V2 paid-stage runner.

This is an *inspection* contract, not authorization to charge money.
It never creates/claims a stage, initializes SQLite, calls a provider, or
uploads/downloads Drive/GCS. An actual executor must independently validate
current provider capabilities, trusted prices, explicit owner approval, and
the current live Drive revision before acquiring an irreversible paid claim.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from app.jobs.golden_v2_ledger import GoldenV2Ledger, STAGES, V2Conflict
from app.pipeline.golden_v2_no_dispatch_recovery import (
    _load_and_verify_evidence, _load_bound_attempt,
)
from app.providers.gemini_primary_audio import _paid_window_slot_digest

_JOB = re.compile(r"v2p?_[0-9a-f]{32}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_PAID = frozenset(("gemini_primary", "chirp_independent", "third_optional"))
_RECEIPTS = {
    "gemini_primary": "gemini-primary",
    "third_optional": "gemini-third",
}


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_file(parent: Path, filename: str) -> Path:
    path = parent / filename
    if path.is_symlink() or not path.is_file() or path.resolve().parent != parent:
        raise ValueError(f"missing or unsafe {filename}")
    return path


def _safe_json(parent: Path, filename: str) -> dict[str, Any]:
    path = _safe_file(parent, filename)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{filename} must be an object")
    return data


def _verified_unsubmitted_primary_reconciliation(
    *, job_dir: Path, job_id: str, source_sha: str, stage_evidence_sha: str | None,
    created_at: str,
    reconciliations: list[sqlite3.Row],
) -> bool:
    """Recognize only the exact immutable no-dispatch archive as non-receipt.

    The prior ambiguity marker and archived raw audit remain in place. They
    stop blocking the one authorized retry only when the ledger reconciliation,
    commit proof, Monitoring evidence, source identity, and archived bytes all
    agree. Any extra or changed artifact keeps paid readiness blocked.
    """
    try:
        if len(reconciliations) != 1:
            return False
        reconciliation = reconciliations[0]
        evidence_sha = reconciliation["evidence_sha"]
        if (reconciliation["actor"] != "vps-control-plane-no-dispatch"
                or reconciliation["actual_cents"] != 0
                or not isinstance(evidence_sha, str)
                or not _SHA.fullmatch(evidence_sha)):
            return False

        golden_dir = job_dir / "golden-v2"
        if (golden_dir.is_symlink() or not golden_dir.is_dir()
                or golden_dir.resolve().parent != job_dir.resolve()):
            return False
        proof_dir = golden_dir / "no-dispatch-commit-proofs"
        if proof_dir.is_symlink() or not proof_dir.is_dir():
            return False
        proof_path = _safe_file(proof_dir, f"{evidence_sha}.json")
        if _sha_file(proof_path) != evidence_sha:
            return False
        proof = json.loads(proof_path.read_text(encoding="utf-8"))
        if (not isinstance(proof, dict)
                or set(proof) != {"schema", "job_id", "stage", "source_sha256",
                                  "initial_evidence_sha256", "revalidation_evidence_sha256"}
                or proof.get("schema") != "golden-v2-no-dispatch-commit-proof-v1"
                or proof.get("job_id") != job_id
                or proof.get("stage") != "gemini_primary"
                or proof.get("source_sha256") != source_sha):
            return False
        initial_sha = proof.get("initial_evidence_sha256")
        revalidation_sha = proof.get("revalidation_evidence_sha256")
        if (not isinstance(initial_sha, str) or not _SHA.fullmatch(initial_sha)
                or not isinstance(revalidation_sha, str) or not _SHA.fullmatch(revalidation_sha)):
            return False

        evidence_path = _safe_file(golden_dir, "no-dispatch-reconciliation.json")
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        if not isinstance(evidence, dict):
            return False
        unknown_at = evidence.get("stage_unknown_at")
        project = evidence.get("api_project")
        if not isinstance(unknown_at, str) or not isinstance(project, str) or not project:
            return False
        evidence = _load_and_verify_evidence(
            evidence_path, expected_sha=initial_sha, job_id=job_id,
            stage="gemini_primary", source_sha=source_sha,
            created_at=created_at, stage_updated_at=unknown_at, project=project,
        )

        if revalidation_sha != initial_sha:
            revalidation_dir = golden_dir / "no-dispatch-revalidations"
            if revalidation_dir.is_symlink() or not revalidation_dir.is_dir():
                return False
            revalidation_path = _safe_file(revalidation_dir, f"{revalidation_sha}.json")
            revalidated = _load_and_verify_evidence(
                revalidation_path, expected_sha=revalidation_sha, job_id=job_id,
                stage="gemini_primary", source_sha=source_sha,
                created_at=created_at, stage_updated_at=unknown_at, project=project,
            )
            if revalidated.get("revalidates_evidence_sha256") != initial_sha:
                return False

        ambiguity = evidence.get("prior_ambiguity")
        if (not isinstance(ambiguity, dict)
                or ambiguity.get("relative_path") != "golden-v2/primary-ambiguous.json"
                or not isinstance(ambiguity.get("sha256"), str)
                or not _SHA.fullmatch(ambiguity["sha256"])
                or ambiguity.get("sha256") != stage_evidence_sha):
            return False
        ambiguity_path = job_dir / "golden-v2" / "primary-ambiguous.json"
        if (ambiguity_path.is_symlink() or not ambiguity_path.is_file()
                or _sha_file(ambiguity_path) != ambiguity["sha256"]):
            return False
        ambiguity_record = json.loads(ambiguity_path.read_text(encoding="utf-8"))
        if (not isinstance(ambiguity_record, dict)
                or ambiguity_record.get("schema") != "golden-v2-ambiguous-primary-v1"
                or ambiguity_record.get("job_id") != job_id
                or ambiguity_record.get("source_sha256") != source_sha
                or ambiguity_record.get("completed_records") != []
                or ambiguity_record.get("unresolved_window_index") != 0):
            return False

        audit, record, _ = _load_bound_attempt(
            job_dir, evidence, initial_sha, source_sha,
        )
        identity = record.get("identity")
        prior = evidence.get("prior_attempt")
        active_name = _paid_window_slot_digest(identity) + ".json" if isinstance(identity, dict) else ""
        if (not isinstance(identity, dict) or not isinstance(prior, dict)
                or prior.get("relative_path") != f"gemini-primary/{active_name}"
                or identity.get("model") != "gemini-3.8-flash"
                or identity.get("source_sha256") != source_sha
                or identity.get("window", {}).get("index") != 0
                or identity.get("window", {}).get("start_seconds") != 0):
            return False
        primary_dir = job_dir / "gemini-primary"
        archive_dir = primary_dir / "reconciled"
        expected_archive = archive_dir / (
            f"{Path(active_name).stem}.not-submitted.{initial_sha}.json"
        )
        if (audit != expected_archive or audit.name != expected_archive.name
                or primary_dir.is_symlink()
                or archive_dir.is_symlink() or not archive_dir.is_dir()
                or {path.name for path in primary_dir.iterdir()} != {"reconciled"}
                or list(archive_dir.iterdir()) != [expected_archive]):
            return False
        return True
    except (OSError, UnicodeError, ValueError, TypeError, KeyError,
            json.JSONDecodeError, V2Conflict, OverflowError):
        return False


def inspect_paid_readiness(
    *, database: Path, artifact_root: Path, job_id: str,
    max_approved_cents: int = 1000,
    expected_source_sha: str | None = None,
) -> dict[str, Any]:
    """Produce a non-secret, read-only assessment of the next paid stage.

    A clean outcome is *still not* permission to start a billable request.
    Missing or changed evidence must be repaired by a separate reviewed flow,
    not by this function or an automatic ASR retry.
    """
    if not isinstance(job_id, str) or not _JOB.fullmatch(job_id):
        raise ValueError("invalid Golden V2 job id")
    if type(max_approved_cents) is not int or not 1 <= max_approved_cents <= 100000:
        raise ValueError("invalid independent owner cost ceiling")
    if expected_source_sha is not None and (
        not isinstance(expected_source_sha, str)
        or not _SHA.fullmatch(expected_source_sha)
    ):
        raise ValueError("invalid expected source SHA")

    blockers: list[str] = []
    stage: str | None = None
    approved_cents: int | None = None
    source_sha: str | None = None
    snapshot_verified = False
    unsubmitted_primary_reconciled = False
    root = Path(artifact_root).expanduser()
    db_path = Path(database).expanduser()
    if (root.is_symlink() or db_path.is_symlink()
            or not root.is_dir() or not db_path.is_file()
            or db_path.resolve().parent != root.resolve()):
        blockers.append("ISOLATED_LEDGER_NOT_READY")
    else:
        root = root.resolve()
        try:
            with closing(sqlite3.connect(
                f"{db_path.resolve().as_uri()}?mode=ro", uri=True,
            )) as db:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA query_only=ON")
                db.execute("BEGIN")
                job = db.execute(
                    "SELECT id,source_sha,mode,status,created_at,estimated_cents,"
                    "approved_cents,approval_reference FROM v2_jobs WHERE id=?",
                    (job_id,),
                ).fetchone()
                if job is None:
                    blockers.append("JOB_NOT_FOUND")
                else:
                    source_sha = job["source_sha"]
                    approved_cents = job["approved_cents"]
                    rows = db.execute(
                        "SELECT name,state,estimate_cents,actual_cents,evidence_sha FROM "
                        "v2_stages WHERE job_id=? ORDER BY ord", (job_id,),
                    ).fetchall()
                    first = next((row for row in rows if row["state"]
                                  not in {"COMPLETE", "SKIPPED"}), None)
                    stage = first["name"] if first else None
                    if stage == "chirp_independent" and db.execute(
                        "SELECT 1 FROM v2_chirp_operations WHERE job_id=? LIMIT 1",
                        (job_id,),
                    ).fetchone():
                        blockers.append("EXISTING_CHIRP_OPERATION_RECONCILE")
                    if job["status"] not in {"QUEUED", "PROCESSING"}:
                        blockers.append("OWNER_APPROVAL_NOT_ACTIVE")
                    if (type(approved_cents) is not int
                            or approved_cents > max_approved_cents
                            or approved_cents < job["estimated_cents"]
                            or not job["approval_reference"]):
                        blockers.append("BUDGET_APPROVAL_NOT_VERIFIED")
                    if tuple(row["name"] for row in rows) != STAGES:
                        blockers.append("STAGE_SCHEMA_MISMATCH")
                    if first is None or stage not in _PAID or first["state"] != "PENDING":
                        blockers.append("NOT_A_PENDING_PAID_STAGE")
                    elif first["estimate_cents"] <= 0:
                        blockers.append("PAID_STAGE_ESTIMATE_NOT_POSITIVE")
                    if (type(approved_cents) is int and
                            sum(int(row["actual_cents"] or 0) for row in rows)
                            + (int(first["estimate_cents"]) if first is not None else 0)
                            > approved_cents):
                        blockers.append("APPROVED_BUDGET_EXHAUSTED")
                    if expected_source_sha is not None and source_sha != expected_source_sha:
                        blockers.append("SOURCE_SHA_MISMATCH")
                    if not job_id.startswith("v2p_"):
                        blockers.append("SOURCE_INTAKE_REGISTRATION_REQUIRED")
                    if stage in _PAID:
                        jobs_root = root / "jobs"
                        job_dir = jobs_root / job_id
                        if (jobs_root.is_symlink() or not jobs_root.is_dir()
                                or jobs_root.resolve() != jobs_root
                                or job_dir.is_symlink() or not job_dir.is_dir()
                                or job_dir.resolve().parent != jobs_root):
                            blockers.append("JOB_ARTIFACT_DIRECTORY_INVALID")
                        else:
                            job_dir = job_dir.resolve()
                            try:
                                pre = _safe_json(job_dir, "source-preflight.json")
                                lineage_path = _safe_file(job_dir, "source-lineage.json")
                                lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
                                original = _safe_file(job_dir, "source.original")
                                normalized = _safe_file(job_dir, "normalized.flac")
                                observed = lineage.get("drive_observation") if isinstance(lineage, dict) else None
                                preflight_sha = _sha_file(job_dir / "source-preflight.json")
                                if (not isinstance(lineage, dict)
                                        or not isinstance(observed, dict)
                                        or GoldenV2Ledger.preflight_job_id(preflight_sha) != job_id
                                        or pre.get("schema") != "golden-v2-source-preflight-v1"
                                        or pre.get("source_sha256") != source_sha
                                        or pre.get("mode") != job["mode"]
                                        or pre.get("owner_approval_required") is not True
                                        or pre.get("estimates_are_authorization") is not False
                                        or pre.get("estimates_are_provider_quotes") is not False
                                        or pre.get("source_lineage_sha256") != _sha_file(lineage_path)
                                        or pre.get("estimated_stage_cents") != {
                                            r["name"]: r["estimate_cents"] for r in rows
                                        }
                                        or lineage.get("normalized_sha256") != source_sha
                                        or lineage.get("original_sha256") != _sha_file(original)
                                        or pre.get("original_sha256") != _sha_file(original)
                                        or pre.get("drive_observation") != observed
                                        or observed.get("provider") != "google_drive"
                                        or not isinstance(observed.get("file_id"), str)
                                        or not observed["file_id"].strip()
                                        or not str(observed.get("version", "")).strip()
                                        or observed.get("downloaded_sha256") != _sha_file(original)
                                        or observed.get("size_bytes") != original.stat().st_size
                                        or _sha_file(normalized) != source_sha):
                                    blockers.append("SOURCE_LINEAGE_VERIFICATION_FAILED")
                                else:
                                    snapshot_verified = True
                            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                                blockers.append("SOURCE_PREFLIGHT_MISSING_OR_CORRUPT")

                            receipt_dirname = _RECEIPTS.get(stage)
                            if receipt_dirname:
                                if stage == "gemini_primary":
                                    reconciliation_rows = db.execute(
                                        "SELECT actor,evidence_sha,actual_cents,verified_at "
                                        "FROM v2_reconciliations "
                                        "WHERE job_id=? AND stage_name=?",
                                        (job_id, stage),
                                    ).fetchall()
                                    unsubmitted_primary_reconciled = (
                                        _verified_unsubmitted_primary_reconciliation(
                                            job_dir=job_dir, job_id=job_id,
                                            source_sha=source_sha,
                                            stage_evidence_sha=first["evidence_sha"],
                                            created_at=job["created_at"],
                                            reconciliations=reconciliation_rows,
                                        )
                                    )
                                hold = "primary" if stage == "gemini_primary" else "third"
                                for filename in (
                                    f"{hold}-ambiguous.json",
                                    f"{stage}-window-cost-hold.json",
                                ):
                                    marker = job_dir / "golden-v2" / filename
                                    if ((marker.exists() or marker.is_symlink())
                                            and not (stage == "gemini_primary"
                                                     and unsubmitted_primary_reconciled
                                                     and filename == "primary-ambiguous.json")):
                                        blockers.append("PAID_OUTCOME_HOLD_RECONCILE")
                                folder = job_dir / receipt_dirname
                                if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
                                    blockers.append("PAID_RECEIPT_DIRECTORY_UNSAFE")
                                elif folder.is_dir():
                                    try:
                                        if (any(folder.iterdir())
                                                and not (stage == "gemini_primary"
                                                         and unsubmitted_primary_reconciled)):
                                            # A prior completed/ambiguous paid call
                                            # cannot silently become a new request.
                                            blockers.append("EXISTING_PAID_RECEIPTS_RECONCILE")
                                    except OSError:
                                        blockers.append("PAID_RECEIPT_DIRECTORY_UNSAFE")
        except sqlite3.Error:
            blockers.append("LEDGER_UNREADABLE")

    return {
        "schema": "golden-v2-paid-readiness-v1",
        "job_id": job_id,
        "source_sha256": source_sha,
        "next_stage": stage,
        "approved_cents": approved_cents,
        "blockers": sorted(set(blockers)),
        "source_snapshot_ready": snapshot_verified,
        "live_drive_revision_verified": False,
        "provider_capability_verified": False,
        "provider_pricing_verified": False,
        "provider_calls_allowed": False,
        "provider_calls_started": False,
        "drive_mutation_started": False,
        "ledger_modified": False,
        "read_only": True,
        "unsubmitted_primary_reconciled": unsubmitted_primary_reconciled,
        "next_action": "REVIEW_PROVIDER_AND_LIVE_SOURCE" if not blockers
                       else "BLOCKED_REQUIRE_RECONCILIATION",
    }
