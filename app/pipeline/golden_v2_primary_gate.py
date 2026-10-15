"""Fail-closed Gemini-first proof for NEW Chirp requests (provider-free)."""
import json
from pathlib import Path
from app.jobs.golden_v2_ledger import V2Conflict
from app.providers.golden_v2_audio_staging import file_sha256,duration_seconds
from app.providers.gemini_primary_audio import (
    PROMPT_VERSION, validate_staged_windows, assemble_windows,
    assemble_reviewed_overlaps, PrimaryTranscriptError)
from app.pipeline.golden_v2_primary_stage import _primary_audit_path

def require_complete_gemini_before_chirp(*,ledger,job_id,job_dir,source_sha):
    """Validate every window and complete stage evidence before Chirp POST."""
    try:
        state=ledger.status(job_id)
        stage=next(x for x in state["stages"] if x["name"]=="gemini_primary")
        sha=stage["evidence_sha"]
        if (state["source_sha"]!=source_sha or stage["state"]!="COMPLETE"
                or not isinstance(sha,str) or len(sha)!=64):
            raise V2Conflict("complete Gemini 3.8 Flash stage required before new Chirp")
        root=Path(job_dir)/"golden-v2"
        original=root/"primary-stage.json"
        original_sha=file_sha256(original) if original.is_file() and not original.is_symlink() else None
        reviewed=original_sha!=sha
        report_path=root/f"primary-stage-reviewed-{sha}.json" if reviewed else original
        if report_path.is_symlink() or not report_path.is_file() or file_sha256(report_path)!=sha:
            raise V2Conflict("Gemini primary completion SHA not backed by immutable report")
        report=json.loads(report_path.read_bytes())
        if (report.get("schema")!="golden-v2-primary-stage-v1"
                or report.get("job_id")!=job_id or report.get("source_sha256")!=source_sha
                or report.get("mode")!="gemini_3_8_audio_first"
                or report.get("ready_for_chirp") is not True):
            raise V2Conflict("Gemini primary proof is incomplete")
        audio=Path(job_dir)/"normalized.flac"
        lineage=root/"source-lineage.json"
        if (audio.is_symlink() or not audio.is_file() or file_sha256(audio)!=source_sha
                or lineage.is_symlink() or not lineage.is_file()
                or file_sha256(lineage)!=report.get("source_lineage_sha256")):
            raise V2Conflict("Gemini original normalized source/lineage mismatch")
        if json.loads(lineage.read_bytes()).get("normalized_sha256")!=source_sha:
            raise V2Conflict("Gemini source lineage not verified")
        windows,evidence=report.get("windows"),report.get("window_evidence")
        if (not isinstance(windows,list) or not windows or not isinstance(evidence,list)
                or len(windows)!=len(evidence)):
            raise V2Conflict("Gemini window/receipt count incomplete")
        duration=duration_seconds(audio)
        validate_staged_windows(windows,duration)
        if abs(float(windows[-1]["end_seconds"])-duration)>0.25:
            raise V2Conflict("Gemini main transcript omits audio tail")
        records=[]
        for w,receipt in zip(windows,evidence):
            identity=receipt["identity"]
            if (identity.get("model")!="gemini-3.8-flash"
                    or identity.get("prompt_version")!=PROMPT_VERSION
                    or identity.get("source_sha256")!=source_sha
                    or identity.get("window")!={k:w[k] for k in
                        ("index","start_seconds","end_seconds","gcs_uri","mime_type")}):
                raise V2Conflict("Gemini window identity or model differs")
            f=_primary_audit_path(Path(job_dir),identity)
            if f.is_symlink() or not f.is_file() or file_sha256(f)!=receipt["raw_audit_sha256"]:
                raise V2Conflict("Gemini raw audit missing or altered")
            raw=json.loads(f.read_bytes())
            if (raw.get("identity")!=identity or raw.get("status")!="COMPLETE"
                    or raw.get("finish_reason") not in ("STOP","FINISH_REASON_STOP")
                    or raw.get("finish_reason")!=receipt.get("finish_reason")
                    or raw.get("usage_metadata")!=receipt.get("usage_metadata")
                    or raw.get("human_review_required") is not False
                    or not isinstance(raw.get("text"),str) or not raw["text"].strip()):
                raise V2Conflict("Gemini raw audio window was not fully recognized")
            records.append(raw)
        if reviewed:
            audit=report.get("audio_review")
            if (not isinstance(audit,dict) or audit.get("review_scope")!="OVERLAP_SEAMS_ONLY"
                    or audit.get("original_primary_evidence_sha256")!=original_sha
                    or not audit.get("reviewer") or not audit.get("review_reference")
                    or audit.get("raw_window_audit_sha256")!=[x["raw_audit_sha256"] for x in evidence]):
                raise V2Conflict("Gemini seam audio review is not source-bound")
            baseline=json.loads(original.read_bytes())
            if (baseline.get("ready_for_chirp") is not False
                    or baseline.get("windows")!=windows
                    or baseline.get("window_evidence")!=evidence
                    or baseline.get("assembled")!=assemble_windows(windows,records,duration)):
                raise V2Conflict("Gemini pre-review evidence changed")
            assembled=assemble_reviewed_overlaps(
                windows,records,duration,seam_decisions=report["assembled"]["reviewed_seams"])
        else:
            assembled=assemble_windows(windows,records,duration)
        if (report.get("assembled")!=assembled
                or assembled.get("structural_coverage")!="PASS"
                or assembled.get("window_count")!=len(windows)
                or assembled.get("ready_for_chirp_comparison") is not True):
            raise V2Conflict("Gemini full contextual draft not yet approved for Chirp")
        return {"window_count":len(windows),"source_sha256":source_sha,
                "primary_sha256":sha,"structural_coverage":"PASS",
                "acoustic_coverage":assembled["acoustic_coverage"],
                "provider_calls_started":False,"ledger_modified":False}
    except V2Conflict:
        raise
    except (OSError, ValueError, TypeError, KeyError, IndexError,
            AttributeError,PrimaryTranscriptError) as exc:
        raise V2Conflict("Gemini primary complete evidence unavailable") from exc
