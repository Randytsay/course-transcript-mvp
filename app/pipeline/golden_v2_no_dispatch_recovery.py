"""Auditable recovery for a provider call proven not to have reached Google.

This path is intentionally narrower than ordinary provider reconciliation.
It only reopens a zero-usage Gemini primary stage after a complete Google API
request-count window has aged past the documented visibility delay.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline import golden_v2_diagnostics as _diag

__DIAG_DEFAULT_STAGE = "gemini_primary"


def _current_source_location():
    """Return the (file, line) of the immediate caller one frame up.

    Used by ``_raise_diagnosed`` to attach the precise raise site to the
    diagnostic record without editing every existing raise statement.
    """
    import sys
    frame = sys._getframe(1)
    return frame.f_code.co_filename.split("/")[-1], frame.f_lineno


def _raise_diagnosed(reason, *, stage=__DIAG_DEFAULT_STAGE, context=None, cause=None):
    """Raise ``V2Conflict`` with a structured ``.diagnostic`` attribute.

    The diagnostic record is built by ``golden_v2_diagnostics`` and is safe
    to log: secrets are redacted, long strings are truncated, and a
    ``trace_id`` is included for correlation. This helper never widens
    authority, never alters the Ledger, and never bypasses Provider Raw
    evidence; it only enriches the exception that is raised.

    ``cause`` is the optional underlying exception (e.g. a ``json`` parse
    error). It is recorded on the diagnostic context but never written
    into a V2Conflict ``__cause__`` so the original stack is preserved
    by Python's normal exception chaining on the caller side.
    """
    file, line = _current_source_location()
    ctx = dict(context or {})
    if cause is not None:
        ctx["cause_type"] = type(cause).__name__
        ctx["cause_message"] = str(cause)[:200]
    try:
        code = _diag.error_code_for(reason)
        record = _diag.make_record(
            error_code=code, reason=reason, stage=stage,
            source=(file, line), context=ctx,
        )
    except Exception:
        # Diagnostics are best-effort; never let them mask a real V2Conflict.
        record = {"error_code": "GVR-ND-000", "reason": reason, "stage": stage,
                  "file": file, "line": line,
                  "context": ctx, "trace_id": ""}
    if cause is not None:
        exc = V2Conflict(reason)
        exc.__cause__ = cause
    else:
        exc = V2Conflict(reason)
    exc.diagnostic = record
    raise exc
from app.providers.gemini_primary_audio import _paid_window_slot_digest

SCHEMA = "golden-v2-no-dispatch-evidence-v1"
METRIC_TYPE = "serviceruntime.googleapis.com/api/request_count"
SERVICES = ("aiplatform.googleapis.com", "generativelanguage.googleapis.com")
NON_GENERATION_METHODS = frozenset({
    "google.cloud.aiplatform.v1beta1.ModelGardenService.GetPublisherModel",
    "google.cloud.aiplatform.v1.LlmUtilityService.CountTokens",
})
VISIBILITY_DELAY_SECONDS = 1800


def _parse_time(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("invalid evidence timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return result.astimezone(timezone.utc)


def _time_string(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _monitoring_window(created_at: str, stage_updated_at: str) -> tuple[str, str]:
    stage_end = _parse_time(stage_updated_at)
    earliest_bound = _parse_time(created_at)
    # The monitoring window covers the execution attempt leading up to stage_updated_at.
    # While created_at provides the earliest possible bound (for jobs executed immediately),
    # queued jobs may have been created days earlier. An attempt cannot exceed the maximum
    # stage duration (e.g. 15 minutes before failure).
    start_dt = max(earliest_bound, stage_end - timedelta(minutes=15))
    start = start_dt.replace(second=0, microsecond=0) - timedelta(minutes=1)
    end = stage_end + timedelta(seconds=120)
    end = end.replace(second=0, microsecond=0) + (timedelta(minutes=1) if end.second or end.microsecond else timedelta())
    return _time_string(start), _time_string(end)


def _query_filter(service: str) -> str:
    return (f'metric.type="{METRIC_TYPE}" AND resource.type="consumed_api" '
            f'AND resource.labels.service="{service}"')


def _fetch_request_metrics(*, project: str, start: str, end: str,
                           session: Any) -> list[dict[str, Any]]:
    responses = []
    for service in SERVICES:
        params = {
            "filter": _query_filter(service), "interval.startTime": start,
            "interval.endTime": end, "view": "FULL", "pageSize": 1000,
        }
        pages = []
        page_token = None
        for _ in range(10):
            if page_token:
                params["pageToken"] = page_token
            response = session.get(
                f"https://monitoring.googleapis.com/v3/projects/{project}/timeSeries",
                params=params, timeout=30,
            )
            body = response.json()
            pages.append({"http_status": response.status_code, "body": body})
            if response.status_code != 200:
                raise RuntimeError("Cloud Monitoring request-count query failed")
            page_token = body.get("nextPageToken")
            if not page_token:
                break
        else:
            raise RuntimeError("Cloud Monitoring returned too many pages")
        responses.append({"service": service, "filter": _query_filter(service),
                          "start": start, "end": end, "pages": pages})
    return responses


def _verify_metric_window(evidence: dict[str, Any], *, project: str,
                          start: str, end: str, captured_at: str) -> None:
    if (evidence.get("schema") != SCHEMA or evidence.get("api_project") != project
            or evidence.get("metric_type") != METRIC_TYPE
            or evidence.get("window_start") != start or evidence.get("window_end") != end
            or evidence.get("visibility_delay_seconds") != VISIBILITY_DELAY_SECONDS
            or evidence.get("captured_at") != captured_at):
        _raise_diagnosed("no-dispatch evidence scope or schema mismatch")
    if _parse_time(captured_at) < _parse_time(end) + timedelta(seconds=VISIBILITY_DELAY_SECONDS):
        _raise_diagnosed("Cloud Monitoring visibility window is not complete")
    services = evidence.get("request_count_queries")
    if (not isinstance(services, list) or len(services) != len(SERVICES)
            or {item.get("service") for item in services} != set(SERVICES)):
        _raise_diagnosed("incomplete Cloud Monitoring service coverage")
    observed_methods: set[str] = set()
    for item in services:
        service = item.get("service")
        if (item.get("filter") != _query_filter(service) or item.get("start") != start
                or item.get("end") != end or not isinstance(item.get("pages"), list)
                or not item["pages"]):
            _raise_diagnosed("Cloud Monitoring query parameters mismatch")
        for page_index, page in enumerate(item["pages"]):
            if page.get("http_status") != 200 or not isinstance(page.get("body"), dict):
                _raise_diagnosed("Cloud Monitoring response is incomplete")
            if page_index == len(item["pages"]) - 1 and page["body"].get("nextPageToken"):
                _raise_diagnosed("Cloud Monitoring response was not fully paged")
            # Cloud Monitoring's protobuf JSON omits empty repeated fields.
            # Treat an omitted timeSeries as an empty response, while still
            # rejecting any present value with the wrong shape.
            series = page["body"].get("timeSeries", [])
            if not isinstance(series, list):
                _raise_diagnosed("invalid Cloud Monitoring time series")
            for item_series in series:
                resource = item_series.get("resource", {})
                labels = resource.get("labels", {})
                if (resource.get("type") != "consumed_api"
                        or labels.get("service") != service
                        or labels.get("project_id") != project):
                    _raise_diagnosed("Cloud Monitoring resource identity mismatch")
                method = labels.get("method")
                if not isinstance(method, str) or method not in NON_GENERATION_METHODS:
                    _raise_diagnosed("unrecognized API method in the paid-call window")
                points = item_series.get("points", [])
                if not isinstance(points, list):
                    _raise_diagnosed("invalid Cloud Monitoring point list")
                for point in points:
                    point_end = point.get("interval", {}).get("endTime")
                    if not isinstance(point_end, str):
                        _raise_diagnosed("Cloud Monitoring point has no end time")
                    if _parse_time(start) <= _parse_time(point_end) <= _parse_time(end):
                        value = point.get("value", {}).get("int64Value")
                        if not isinstance(value, str) or not value.isdigit():
                            _raise_diagnosed("Cloud Monitoring request count is invalid")
                        if int(value) > 0:
                            observed_methods.add(method)
    # A known request inside the window demonstrates that this metric stream
    # was live; an entirely empty response alone is not accepted as proof.
    if "google.cloud.aiplatform.v1beta1.ModelGardenService.GetPublisherModel" not in observed_methods:
        _raise_diagnosed("no positive Vertex request point to validate metric coverage")


def _write_immutable(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink() or path.is_symlink():
        _raise_diagnosed("symlinked no-dispatch evidence path")
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return hashlib.sha256(encoded).hexdigest()


def _write_content_addressed(directory: Path, payload: dict[str, Any]) -> tuple[Path, str]:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    path = directory / f"{digest}.json"
    if directory.is_symlink() or path.parent.is_symlink():
        _raise_diagnosed("symlinked content-addressed evidence directory")
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != encoded:
            _raise_diagnosed("content-addressed evidence path already contains different data")
        return path, digest
    try:
        _write_immutable(path, payload)
    except FileExistsError:
        if path.is_symlink() or not path.is_file() or path.read_bytes() != encoded:
            _raise_diagnosed("concurrent content-addressed evidence collision")
    return path, digest


def _load_and_verify_evidence(path: Path, *, expected_sha: str,
                              job_id: str, stage: str, source_sha: str,
                              created_at: str, stage_updated_at: str,
                              project: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        _raise_diagnosed("no-dispatch evidence file unavailable")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        _raise_diagnosed("no-dispatch evidence checksum mismatch")
    try:
        evidence = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        _raise_diagnosed("malformed no-dispatch evidence", cause=exc)
    start, end = _monitoring_window(created_at, stage_updated_at)
    if (not isinstance(evidence, dict) or evidence.get("job_id") != job_id
            or evidence.get("stage") != stage or evidence.get("source_sha256") != source_sha
            or evidence.get("job_created_at") != created_at
            or evidence.get("stage_unknown_at") != stage_updated_at):
        _raise_diagnosed("no-dispatch evidence is not bound to this task")
    _verify_metric_window(evidence, project=project, start=start, end=end,
                          captured_at=evidence.get("captured_at"))
    return evidence


def _archive_attempt(job_dir: Path, evidence: dict[str, Any], evidence_sha: str) -> Path:
    relative = evidence.get("prior_attempt", {}).get("relative_path")
    expected_sha = evidence.get("prior_attempt", {}).get("sha256")
    if not isinstance(relative, str) or not isinstance(expected_sha, str):
        _raise_diagnosed("no-dispatch evidence lacks the prior attempt binding")
    audit_path = job_dir / relative
    parent = job_dir / "gemini-primary"
    if (audit_path.parent != parent or parent.is_symlink() or not parent.is_dir()
            or audit_path.is_symlink()):
        _raise_diagnosed("invalid primary audit path")
    archive_dir = parent / "reconciled"
    if archive_dir.is_symlink():
        _raise_diagnosed("symlinked primary reconciliation archive")
    archive_dir.mkdir(mode=0o700, exist_ok=True)
    archive_path = archive_dir / f"{audit_path.stem}.not-submitted.{evidence_sha}.json"
    if audit_path.exists():
        raw = audit_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected_sha:
            _raise_diagnosed("unknown primary audit changed since evidence capture")
        try:
            prior = json.loads(raw)
        except (UnicodeError, ValueError) as exc:
            _raise_diagnosed("unknown primary audit is malformed", cause=exc)
        if (prior.get("status") != "OUTCOME_UNKNOWN"
                or prior.get("identity", {}).get("source_sha256") != evidence.get("source_sha256")):
            _raise_diagnosed("primary audit is not the proven unknown attempt")
        identity = prior.get("identity")
        identity_sha = hashlib.sha256(json.dumps(
            identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if identity_sha != evidence["prior_attempt"].get("identity_sha256"):
            _raise_diagnosed("primary audit identity differs from no-dispatch evidence")
        if archive_path.exists() or archive_path.is_symlink():
            _raise_diagnosed("primary attempt archive already exists")
        os.rename(audit_path, archive_path)
        for directory in (parent, archive_dir):
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    elif archive_path.is_file() and not archive_path.is_symlink():
        archived_raw = archive_path.read_bytes()
        if hashlib.sha256(archived_raw).hexdigest() != expected_sha:
            _raise_diagnosed("archived unknown primary audit checksum mismatch")
        archived = json.loads(archived_raw)
        identity = archived.get("identity")
        identity_sha = hashlib.sha256(json.dumps(
            identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if (archived.get("status") != "OUTCOME_UNKNOWN"
                or identity.get("source_sha256") != evidence.get("source_sha256")
                or identity_sha != evidence["prior_attempt"].get("identity_sha256")):
            _raise_diagnosed("archived primary attempt identity mismatch")
    else:
        _raise_diagnosed("unknown primary audit was neither active nor safely archived")
    return archive_path


def _load_bound_attempt(job_dir: Path, evidence: dict[str, Any],
                        evidence_sha: str, source_sha: str) -> tuple[Path, dict[str, Any], str]:
    prior = evidence.get("prior_attempt", {})
    relative, expected_sha = prior.get("relative_path"), prior.get("sha256")
    if not isinstance(relative, str) or not isinstance(expected_sha, str):
        _raise_diagnosed("saved no-dispatch proof lacks its primary audit binding")
    primary_dir = job_dir / "gemini-primary"
    active = job_dir / relative
    if active.parent != primary_dir or not active.name.endswith(".json"):
        _raise_diagnosed("saved no-dispatch proof primary audit path is invalid")
    archived = primary_dir / "reconciled" / f"{active.stem}.not-submitted.{evidence_sha}.json"
    candidates = [path for path in (active, archived) if path.is_file() and not path.is_symlink()]
    if len(candidates) != 1:
        _raise_diagnosed("expected exactly one active or archived primary attempt")
    raw = candidates[0].read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        _raise_diagnosed("saved no-dispatch proof references changed raw primary evidence")
    try:
        record = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        _raise_diagnosed("saved no-dispatch proof raw attempt is malformed", cause=exc)
    identity = record.get("identity")
    if not isinstance(identity, dict):
        _raise_diagnosed("saved no-dispatch proof raw attempt has no identity")
    identity_sha = hashlib.sha256(json.dumps(
        identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if (record.get("status") != "OUTCOME_UNKNOWN"
            or identity.get("model") != "gemini-3.8-flash"
            or identity.get("source_sha256") != source_sha
            or identity.get("window", {}).get("index") != 0
            or identity.get("window", {}).get("start_seconds") != 0
            or identity_sha != prior.get("identity_sha256")
            or active.name != _paid_window_slot_digest(identity) + ".json"):
        _raise_diagnosed("saved no-dispatch proof prior attempt identity mismatch")
    return candidates[0], record, expected_sha


def reconcile_primary_no_dispatch(*, ledger: GoldenV2Ledger, job_id: str,
                                  artifact_root: Path, project: str,
                                  session: Any, actor: str = "vps-control-plane-no-dispatch") -> dict[str, Any]:
    """Capture control-plane evidence, archive the raw barrier, then reopen one stage."""
    root = Path(artifact_root).resolve()
    job_dir = root / "jobs" / job_id
    if (Path(artifact_root).is_symlink() or (root / "jobs").is_symlink()
            or job_dir.is_symlink() or not job_dir.is_dir()
            or job_dir.resolve().parent != (root / "jobs").resolve()):
        _raise_diagnosed("isolated V2 job artifact directory unavailable")
    status = ledger.status(job_id)
    job = status
    stage = next((item for item in status["stages"] if item["name"] == "gemini_primary"), None)
    if (status["status"] != "NEEDS_RECONCILIATION" or stage is None
            or stage["state"] != "OUTCOME_UNKNOWN" or int(stage["actual_cents"] or 0) != 0):
        _raise_diagnosed("job is not an eligible zero-usage unknown primary attempt")

    ambiguous_path = job_dir / "golden-v2" / "primary-ambiguous.json"
    golden_dir = job_dir / "golden-v2"
    if (golden_dir.is_symlink() or not golden_dir.is_dir()
            or ambiguous_path.is_symlink() or not ambiguous_path.is_file()
            or hashlib.sha256(ambiguous_path.read_bytes()).hexdigest() != stage["evidence_sha"]):
        _raise_diagnosed("immutable primary ambiguity evidence checksum mismatch")
    ambiguous = json.loads(ambiguous_path.read_text(encoding="utf-8"))
    if (ambiguous.get("job_id") != job_id or ambiguous.get("source_sha256") != job["source_sha"]
            or ambiguous.get("completed_records") != []
            or ambiguous.get("unresolved_window_index") != 0):
        _raise_diagnosed("primary ambiguity artifact does not match the unknown first window")

    primary_dir = job_dir / "gemini-primary"
    if primary_dir.is_symlink() or not primary_dir.is_dir():
        _raise_diagnosed("primary audit directory unavailable")
    start, end = _monitoring_window(job["created_at"], stage["updated_at"])
    evidence_path = job_dir / "golden-v2" / "no-dispatch-reconciliation.json"
    revalidation_path = None
    revalidation_sha = None
    if evidence_path.exists() or evidence_path.is_symlink():
        if evidence_path.is_symlink():
            _raise_diagnosed("symlinked no-dispatch evidence path")
        evidence_bytes = evidence_path.read_bytes()
        evidence_sha = hashlib.sha256(evidence_bytes).hexdigest()
        evidence = _load_and_verify_evidence(
            evidence_path, expected_sha=evidence_sha, job_id=job_id,
            stage="gemini_primary", source_sha=job["source_sha"],
            created_at=job["created_at"], stage_updated_at=stage["updated_at"],
            project=project,
        )
        audit_path, prior_record, prior_sha = _load_bound_attempt(
            job_dir, evidence, evidence_sha, job["source_sha"],
        )
        fresh_queries = _fetch_request_metrics(
            project=project, start=start, end=end, session=session,
        )
        revalidation = dict(evidence)
        revalidation["captured_at"] = _time_string(datetime.now(timezone.utc))
        revalidation["request_count_queries"] = fresh_queries
        revalidation["revalidates_evidence_sha256"] = evidence_sha
        _verify_metric_window(
            revalidation, project=project, start=start, end=end,
            captured_at=revalidation["captured_at"],
        )
        revalidation_path, revalidation_sha = _write_content_addressed(
            job_dir / "golden-v2" / "no-dispatch-revalidations", revalidation,
        )
    else:
        candidates = []
        for path in primary_dir.glob("*.json"):
            if path.is_symlink() or not path.is_file():
                _raise_diagnosed("unexpected symlink or non-file in primary audit directory")
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (UnicodeError, ValueError) as exc:
                _raise_diagnosed("malformed primary audit prevents safe retry", cause=exc)
            if (record.get("status") == "OUTCOME_UNKNOWN"
                    and record.get("identity", {}).get("source_sha256") == job["source_sha"]):
                candidates.append((path, record))
        if len(candidates) != 1:
            _raise_diagnosed("expected exactly one source-bound unknown primary audit")
        audit_path, prior_record = candidates[0]
        identity = prior_record.get("identity")
        if (not isinstance(identity, dict)
                or identity.get("model") != "gemini-3.8-flash"
                or identity.get("window", {}).get("index") != ambiguous["unresolved_window_index"]
                or identity.get("window", {}).get("start_seconds") != 0):
            _raise_diagnosed("unknown primary audit model identity mismatch")
        expected_slot = _paid_window_slot_digest(identity) + ".json"
        if audit_path.name != expected_slot:
            _raise_diagnosed("unknown primary audit filename does not match immutable identity")
        prior_sha = hashlib.sha256(audit_path.read_bytes()).hexdigest()
        responses = _fetch_request_metrics(project=project, start=start, end=end, session=session)
        evidence = {
            "schema": SCHEMA, "job_id": job_id, "stage": "gemini_primary",
            "source_sha256": job["source_sha"], "job_created_at": job["created_at"],
            "stage_unknown_at": stage["updated_at"], "api_project": project,
            "metric_type": METRIC_TYPE, "window_start": start, "window_end": end,
            "visibility_delay_seconds": VISIBILITY_DELAY_SECONDS,
            "captured_at": _time_string(datetime.now(timezone.utc)),
            "prior_attempt": {"relative_path": audit_path.relative_to(job_dir).as_posix(),
                              "sha256": prior_sha, "identity_sha256": hashlib.sha256(
                                  json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()},
            "prior_ambiguity": {"relative_path": ambiguous_path.relative_to(job_dir).as_posix(),
                                "sha256": stage["evidence_sha"]},
            "request_count_queries": responses,
        }
        _verify_metric_window(evidence, project=project, start=start, end=end,
                              captured_at=evidence["captured_at"])
        evidence_sha = _write_immutable(evidence_path, evidence)
        revalidation_path, revalidation_sha = evidence_path, evidence_sha

    # Re-verify immediately before the filesystem and ledger transitions.
    evidence = _load_and_verify_evidence(
        evidence_path, expected_sha=evidence_sha, job_id=job_id,
        stage="gemini_primary", source_sha=job["source_sha"],
        created_at=job["created_at"], stage_updated_at=stage["updated_at"],
        project=project,
    )
    if revalidation_path is None or revalidation_sha is None:
        _raise_diagnosed("fresh no-dispatch evidence revalidation is missing")
    if (revalidation_path.is_symlink() or not revalidation_path.is_file()
            or hashlib.sha256(revalidation_path.read_bytes()).hexdigest() != revalidation_sha):
        _raise_diagnosed("fresh no-dispatch revalidation checksum mismatch")
    if revalidation_path != evidence_path:
        revalidated = _load_and_verify_evidence(
            revalidation_path, expected_sha=revalidation_sha, job_id=job_id,
            stage="gemini_primary", source_sha=job["source_sha"],
            created_at=job["created_at"], stage_updated_at=stage["updated_at"],
            project=project,
        )
        if revalidated.get("revalidates_evidence_sha256") != evidence_sha:
            _raise_diagnosed("fresh metrics are not bound to the original evidence")
    commit_proof = {
        "schema": "golden-v2-no-dispatch-commit-proof-v1",
        "job_id": job_id, "stage": "gemini_primary",
        "source_sha256": job["source_sha"],
        "initial_evidence_sha256": evidence_sha,
        "revalidation_evidence_sha256": revalidation_sha,
    }
    if "owner_authorization=" in actor:
        commit_proof["owner_authorization_reference"] = actor.split("owner_authorization=")[1].strip()
    commit_proof_path, commit_proof_sha = _write_content_addressed(
        job_dir / "golden-v2" / "no-dispatch-commit-proofs", commit_proof,
    )
    archived_paths: list[Path] = []

    def archive_attempt() -> None:
        archived_paths.append(_archive_attempt(job_dir, evidence, evidence_sha))

    ledger.reconcile_unsubmitted_provider_call(
        job_id, stage="gemini_primary", actor=actor, evidence_sha=commit_proof_sha,
        attempt_archiver=archive_attempt,
        evidence_verifier=lambda **proof: (
            proof["job_id"] == job_id
            and proof["stage"] == "gemini_primary"
            and proof["source_sha"] == job["source_sha"]
            and proof["created_at"] == job["created_at"]
            and proof["stage_updated_at"] == stage["updated_at"]
            and hashlib.sha256(commit_proof_path.read_bytes()).hexdigest() == proof["evidence_sha"]
            and json.loads(commit_proof_path.read_text(encoding="utf-8")) == commit_proof
            and hashlib.sha256(evidence_path.read_bytes()).hexdigest() == evidence_sha
            and hashlib.sha256(revalidation_path.read_bytes()).hexdigest() == revalidation_sha
        ),
    )
    if len(archived_paths) != 1:
        _raise_diagnosed("raw primary attempt was not archived exactly once")
    archive_path = archived_paths[0]
    return {"job_id": job_id, "stage": "gemini_primary", "status": "REOPENED_PENDING",
            "evidence_sha256": commit_proof_sha,
            "initial_evidence_sha256": evidence_sha,
            "revalidation_evidence_sha256": revalidation_sha,
            "revalidation_artifact": revalidation_path.relative_to(job_dir).as_posix(),
            "archived_attempt": archive_path.relative_to(job_dir).as_posix(),
            "provider_calls_started": False, "drive_mutation_started": False}
