"""Separate, owner-granted AI-reviewed Drive delivery; never Golden publication.

All references are local immutable evidence from a source-bound V2 ledger.
This workflow is disabled until a dedicated signed grant exists. Unknown
provider work, an unresolved acoustic/semantic interval or an existing Drive
destination is a hard stop. No overwrite, public link or human claim.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from pathlib import Path

from app.jobs.drive_lock import drive_publish_lock
from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_ai_review_contract import (
    QUALITY_TIER, build_ai_review_certificate, validate_ai_publication_intent,
)
from app.pipeline.golden_v2_delivery import DriveDeliveryGateway, manifest_digest
from app.pipeline.golden_v2_quote_intake import wire
from app.pipeline.golden_v2_runtime_services import signed_grant
from app.providers.golden_v2_audio_staging import file_sha256
from app.providers.golden_v2_immutable import store_immutable_bytes
from app.providers.golden_v2_versioned_artifacts import resolve_versioned


def _json_pinned(path: Path, sha: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{64}", sha or ""):
        raise V2Conflict("missing pinned AI review evidence")
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise V2Conflict("unsafe AI review evidence")
    if file_sha256(path) != sha:
        raise V2Conflict("AI evidence bytes changed")
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise V2Conflict("AI evidence must be a JSON object")
    return result


def checked_ai_outputs(root: Path, job_id: str, *, require_attestation: bool = True):
    """Verify actual ledger Chirp PASS and machine QA's four exact hashes."""
    root = Path(root)
    if (not re.fullmatch(r"v2p_[0-9a-f]{32}", job_id)
            or root.is_symlink() or (root / "jobs").is_symlink()):
        raise V2Conflict("unsafe AI delivery root")
    job = root / "jobs" / job_id
    folder = job / "golden-v2"
    if job.is_symlink() or folder.is_symlink() or not folder.is_dir():
        raise V2Conflict("unsafe AI delivery job folder")
    state = GoldenV2Ledger.open_existing(root / "ledger.sqlite").status(job_id)
    chirp = next(s for s in state["stages"] if s["name"] == "chirp_independent")
    if chirp["state"] != "COMPLETE" or not chirp["evidence_sha"]:
        raise V2Conflict("Chirp evidence not complete; AI certification prohibited")
    merged_file = resolve_versioned(folder / "chirp-merged.json", chirp["evidence_sha"])
    merged = _json_pinned(merged_file, chirp["evidence_sha"])
    acoustic_sha = merged.get("acoustic_qa_sha256")
    if merged.get("acoustic_gate") != "PASS" or not re.fullmatch(r"[0-9a-f]{64}", acoustic_sha or ""):
        raise V2Conflict("acoustic verification not complete")
    acoustic = _json_pinned(folder / f"chirp-acoustic-qa-{acoustic_sha[:20]}.json", acoustic_sha)
    if (acoustic.get("status") != "PASS"
            or acoustic.get("source_sha256") != state["source_sha"]
            or not isinstance(merged.get("words"), list) or not merged["words"]
            or merged.get("source_sha") != state["source_sha"]
            or merged.get("timing_authority") != "chirp_3"):
        raise V2Conflict("actual provider/acoustic evidence differs")
    manifest_file = folder / "render-manifest.json"
    if manifest_file.is_symlink() or not manifest_file.is_file():
        raise V2Conflict("AI render manifest not present")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    render_sha = file_sha256(manifest_file)
    if (not isinstance(manifest, dict) or manifest.get("source_sha256") != state["source_sha"]
            or manifest_digest(manifest) != render_sha):
        raise V2Conflict("manifest source/content mismatch")
    qa_file = folder / "ai-review-report.json"
    if qa_file.is_symlink() or not qa_file.is_file():
        raise V2Conflict("trusted machine-review report not present")
    qa_sha = file_sha256(qa_file)
    report = _json_pinned(qa_file, qa_sha)
    if (report.get("job_id") != job_id
            or report.get("report_source") != "TRUSTED_MACHINE_QA"
            or report.get("acoustic_qa_sha256") != acoustic_sha
            or report.get("chirp_sha256") != chirp["evidence_sha"]
            or report.get("render_sha256") != render_sha):
        raise V2Conflict("machine quality report lacks exact evidence ancestry")
    cert = build_ai_review_certificate(
        job_id=job_id, source_sha256=state["source_sha"],
        chirp_sha256=chirp["evidence_sha"],
        acoustic_qa_sha256=acoustic_sha, render_sha256=render_sha,
        report=report,
    )
    # Certificate binds all machine metrics and report bytes; never accept a
    # client-supplied PASS assertion in place of this server reconstruction.
    if cert["report_sha256"] != qa_sha:
        raise V2Conflict("AI report uses noncanonical immutable encoding")
    path = folder / "ai-review-certificate.json"
    if path.is_symlink() or (path.exists() and _json_pinned(path, file_sha256(path)) != cert):
        raise V2Conflict("AI certificate changed since QA")
    # Preflight only; certificate created by dedicated, owner-authenticated
    # attestation API, never synthesized during delivery or owner grant.
    if require_attestation and not path.is_file():
        raise V2Conflict("AI certificate not yet attested")
    source_file = job / "source-preflight.json"
    if source_file.is_symlink():
        raise V2Conflict("source preflight changed")
    preflight = json.loads(source_file.read_text())
    if preflight["source_sha256"] != state["source_sha"]:
        raise V2Conflict("source SHA no longer approved")
    source = dict(preflight["drive_observation"])
    source_path = preflight["drive_source_path"]
    if "downloaded_md5" not in source:
        source["downloaded_md5"] = preflight.get("original_md5_checksum")
    stem = Path(source_path).stem
    names = [stem + "_AI校對.srt", stem + "_AI校對.txt"]
    paths = [
        resolve_versioned(folder / "final.srt", manifest["srt_sha256"]),
        resolve_versioned(folder / "final-corrected-transcript.txt", manifest["transcript_sha256"]),
    ]
    for path_out, key in zip(paths, ["srt_sha256", "transcript_sha256"]):
        if path_out.is_symlink() or path_out.stat().st_size > 10*1024**2 or file_sha256(path_out) != manifest[key]:
            raise V2Conflict("rendered AI output no longer matches manifest")
    from app.subtitles.editor_hardened import parse_srt_strict
    try:
        cues, _ = parse_srt_strict(paths[0].read_text(encoding="utf-8"))
        transcript = paths[1].read_text(encoding="utf-8")
    except (UnicodeError, ValueError) as exc:
        raise V2Conflict("invalid machine-reviewed SRT/TXT content") from exc
    if (len(cues) != cert["cue_count"]
            or cert["word_count"] != len(merged["words"])
            or "".join("".join(cue["text"].split()) for cue in cues) != "".join(transcript.split())
            or cues[-1]["end_ms"] > merged.get("duration_ms", 0)):
        raise V2Conflict("SRT cue counts, source timing or text consistency mismatch")
    return state, source, source_path, cert, dict(zip(names, paths))




def prepare_ai_quality_report(root: Path, job_id: str) -> dict:
    """Derive AI machine QA only from a ledger-approved Golden *machine* QA.

    A Golden final QA PASS is a machine artifact; it is not an assertion that
    any person listened to the recording. This emits an independently named,
    explicitly non-Golden report without contacting external services.
    It cannot rescue a held Gemini/Chirp/semantic stage by inventing PASS.
    """
    from app.pipeline.golden_v2_delivery import checked_outputs
    from app.subtitles.editor_hardened import parse_srt_strict
    root = Path(root)
    state, _, _, qa_sha, manifest, outputs = checked_outputs(root, job_id)
    job = root / "jobs" / job_id
    folder = job / "golden-v2"
    qa_path = resolve_versioned(folder / "golden-qa.json", qa_sha)
    machine_qa = _json_pinned(qa_path, qa_sha)
    if (machine_qa.get("status") != "PASS"
            or machine_qa.get("safe_publish_allowed") is not True
            or machine_qa.get("errors") not in ([], None)):
        raise V2Conflict("Golden machine QA did not prove all quality gates")
    stages = {s["name"]: s for s in state["stages"]}
    chirp_sha = stages["chirp_independent"]["evidence_sha"]
    if stages["chirp_independent"]["state"] != "COMPLETE":
        raise V2Conflict("Chirp cannot be substituted by machine QA")
    merged = _json_pinned(
        resolve_versioned(folder / "chirp-merged.json", chirp_sha), chirp_sha)
    acoustic_sha = merged.get("acoustic_qa_sha256")
    acoustic = _json_pinned(
        folder / f"chirp-acoustic-qa-{str(acoustic_sha)[:20]}.json", acoustic_sha)
    if (merged.get("source_sha") != state["source_sha"]
            or merged.get("acoustic_gate") != "PASS"
            or acoustic.get("status") != "PASS"
            or acoustic.get("source_sha256") != state["source_sha"]
            or acoustic.get("unexamined_gap_count", 0) != 0
            or acoustic.get("uncertain_count", 0) != 0):
        raise V2Conflict("Chirp acoustic coverage has unverified intervals")
    srt_file, txt_file = list(outputs.values())
    cues, _ = parse_srt_strict(srt_file.read_text(encoding="utf-8"))
    transcript = txt_file.read_text(encoding="utf-8")
    if (not cues or len(cues) != machine_qa.get("cue_count")
            or not isinstance(merged.get("words"), list) or not merged["words"]
            or "".join("".join(cue["text"].split()) for cue in cues) != "".join(transcript.split())
            or cues[-1]["end_ms"] > merged.get("duration_ms", 0)):
        raise V2Conflict("render and Chirp word boundaries cannot be attested")
    report = dict(
        job_id=job_id, report_source="TRUSTED_MACHINE_QA",
        source_sha256=state["source_sha"],
        chirp_sha256=chirp_sha,
        acoustic_qa_sha256=acoustic_sha,
        render_sha256=manifest_digest(manifest),
        golden_machine_qa_sha256=qa_sha,
        acoustic_gate="PASS", timeline_gate="PASS",
        srt_txt_consistency="PASS", semantic_gate="PASS",
        unverified_audio_intervals=0, missing_windows=0,
        unresolved_semantic_conflicts=0, unverified_srt_cues=0,
        cue_count=len(cues), word_count=len(merged["words"]),
    )
    stored = folder / "ai-review-report.json"
    store_immutable_bytes(stored, wire(report).encode("utf-8"))
    return dict(job_id=job_id, report_sha256=file_sha256(stored),
                quality_tier=QUALITY_TIER,
                machine_review="PASS", human_listening="NOT_CLAIMED")



def attest_ai_review(root: Path, job_id: str) -> dict:
    """Explicitly attest only a trusted QA report. Never authorizes delivery."""
    _, _, _, cert, _ = checked_ai_outputs(
        root, job_id, require_attestation=False)
    path = Path(root) / "jobs" / job_id / "golden-v2" / "ai-review-certificate.json"
    store_immutable_bytes(path, wire(cert).encode())
    return {"job_id": job_id, "quality_tier": QUALITY_TIER,
            "certificate_sha256": cert["certificate_sha256"],
            "report_sha256": cert["report_sha256"],
            "status": "AI_REVIEW_ATTESTED_NOT_GOLDEN",
            "delivery_authorized": False}


def authorize_ai_publication(*, root: Path, job_id: str, revision: int, 
                             certificate_sha256: str, filenames: list[str],
                             actor: str, review_reference: str) -> dict:
    state, _, _, cert, outputs = checked_ai_outputs(root, job_id)
    if (state["revision"] != revision or not review_reference.strip()
            or not actor.strip() or cert["certificate_sha256"] != certificate_sha256
            or list(outputs) != filenames):
        raise V2Conflict("AI evidence/owner publication revision changed")
    grant = {
        "quality_tier": QUALITY_TIER, "job_id": job_id, "revision": revision,
        "source_sha256": state["source_sha"],
        "certificate_sha256": cert["certificate_sha256"],
        "render_sha256": cert["render_sha256"],
        "actor": actor, "review_reference": review_reference,
        "owner_confirm": True, "filenames": filenames,
    }
    validate_ai_publication_intent(
        certificate=cert, grant=grant, filenames=filenames, existing_target_count=0)
    path = Path(root) / "jobs" / job_id / "owner-ai-publish-grant.json"
    envelope = wire(signed_grant(grant)).encode()
    store_immutable_bytes(path, envelope)
    return {"job_id": job_id, "status": "AI_PUBLICATION_AUTHORIZED",
            "quality_tier": QUALITY_TIER, "filenames": filenames}


def publish_granted_ai_outputs(*, root: Path, job_id: str, lock_root: Path, gateway=None):
    state, source, source_path, cert, outputs = checked_ai_outputs(root, job_id)
    job = Path(root) / "jobs" / job_id
    grant_file = job / "owner-ai-publish-grant.json"
    if grant_file.is_symlink() or not grant_file.is_file():
        raise V2Conflict("separate owner AI publication grant missing")
    envelope = json.loads(grant_file.read_text(encoding="utf-8"))
    payload = envelope["grant"]
    if not hmac.compare_digest(signed_grant(payload)["signature"], envelope["signature"]):
        raise V2Conflict("AI grant signature invalid")
    if payload["revision"] != state["revision"]:
        raise V2Conflict("AI publication owner revision changed")
    validate_ai_publication_intent(
        certificate=cert, grant=payload, filenames=list(outputs), existing_target_count=0)
    if Path(lock_root).is_symlink():
        raise V2Conflict("unsafe Drive publish lock")
    gateway = gateway or DriveDeliveryGateway()

    def check_source():
        live = gateway.get(source["file_id"])
        if (not live or live.get("trashed")
                or str(live.get("version")) != str(source["version"])
                or int(live.get("size", -1)) != source["size_bytes"]
                or live.get("md5Checksum") != source["downloaded_md5"]
                or len(live.get("parents", [])) != 1):
            raise V2Conflict("Drive source modified; AI publication held")
        return live["parents"][0]

    with drive_publish_lock(lock_root, source_path):
        parent = check_source()
        staged = []
        for name, path in outputs.items():
            content = path.read_bytes()
            sha = hashlib.sha256(content).hexdigest()
            suffix = "srt" if name.endswith(".srt") else "txt"
            intent = job / "golden-v2" / f"ai-{suffix}-delivery-intent.json"
            if intent.is_symlink():
                raise V2Conflict("unsafe saved AI delivery intent")
            if intent.exists():
                previous = json.loads(intent.read_text())
                if (previous["name"] != name or previous["sha256"] != sha
                        or previous["parent_id"] != parent):
                    raise V2Conflict("saved AI intent changed")
                file_id = previous["file_id"]
                pending = previous["pending_name"]
            else:
                if gateway.named(parent, name):
                    raise V2Conflict("AI sidecar target exists; never overwrite")
                file_id = gateway.generate_id()
                pending = "." + job_id + ".ai.pending." + suffix
                store_immutable_bytes(intent, wire({
                    "name": name, "sha256": sha, "parent_id": parent,
                    "file_id": file_id, "pending_name": pending}).encode())
            remote = gateway.get(file_id)
            allowed = {file_id}
            if any(x["id"] not in allowed for x in gateway.named(parent, name)):
                raise V2Conflict("AI target collision detected")
            if remote is None:
                check_source()
                gateway.create_pending(file_id, parent, pending, content, job_id)
                remote = gateway.get(file_id)
            if (not remote or remote.get("appProperties", {}).get("golden_v2_job") != job_id
                    or remote.get("parents") != [parent] or remote.get("trashed")
                    or remote.get("name") not in {pending, name}):
                raise V2Conflict("AI pending file is not owned by this job")
            observed = gateway.readback(file_id)
            if not isinstance(observed, bytes):
                raise V2Conflict("AI delivery readback missing")
            if hashlib.sha256(observed).hexdigest() != sha:
                if remote["name"] != pending or observed:
                    raise V2Conflict("published/edited AI file cannot be replaced")
                check_source()
                gateway.write_pending_content(file_id, content)
                if hashlib.sha256(gateway.readback(file_id)).hexdigest() != sha:
                    raise V2Conflict("AI pending content hash mismatch")
            staged.append((name, file_id, pending, sha, len(content)))
        delivered = []
        for name, file_id, pending, sha, size in staged:
            check_source()
            if any(item["id"] != file_id for item in gateway.named(parent, name)):
                raise V2Conflict("AI destination changed before promote")
            remote = gateway.get(file_id)
            if remote["name"] == pending:
                gateway.promote(file_id, name)
            elif remote["name"] != name:
                raise V2Conflict("AI pending file renamed externally")
            remote = gateway.get(file_id)
            if (remote["name"] != name
                    or hashlib.sha256(gateway.readback(file_id)).hexdigest() != sha
                    or any(item["id"] != file_id for item in gateway.named(parent, name))):
                raise V2Conflict("AI final Drive readback mismatch")
            delivered.append({"name": name, "file_id": file_id,
                              "sha256": sha, "bytes": size})
        check_source()
        receipt = {"schema": "golden-v2-ai-delivery-v1", "job_id": job_id,
                   "quality_tier": QUALITY_TIER,
                   "certificate_sha256": cert["certificate_sha256"],
                   "files": delivered, "status": "DELIVERED",
                   "public_links_created": False}
        store_immutable_bytes(job / "golden-v2" / "ai-drive-delivery.json", wire(receipt).encode())
        return receipt
