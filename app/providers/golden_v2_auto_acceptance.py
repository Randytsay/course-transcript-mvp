"""Independent machine-verifiable acceptance tier for TXT/SRT delivery.

The tier can close without a person listening, but NEVER certifies source
speech was exhaustively recognized or mutates the strict Golden PASS gate.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from hashlib import sha256
import json
import re
import unicodedata
from typing import Any

from fastapi import HTTPException
from app.subtitles.editor_hardened import parse_srt_strict

SCHEMA = "golden-v2-auto-acceptance-v1"
TIER = "AUTO_ACCEPTED_WITH_REVIEW"


def _sha(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def _normalize(value: str) -> str:
    return "".join(c.casefold() for c in unicodedata.normalize("NFKC", value)
                   if c.isalnum())


def _check_cues(raw: bytes) -> tuple[list[dict], list[str]]:
    failures: list[str] = []
    try:
        cues, _ = parse_srt_strict(raw.decode("utf-8-sig"))
    except (UnicodeError, ValueError, KeyError, TypeError, HTTPException):
        return [], ["invalid_strict_srt"]
    if not cues:
        return [], ["no_subtitle_cues"]
    previous_start, previous_end = -1, -1
    for i, cue in enumerate(cues):
        start, end = cue["start_ms"], cue["end_ms"]
        if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end:
            failures.append("invalid_cue_time")
        if start < previous_start or start < previous_end:
            failures.append("nonmonotonic_or_overlapping_cues")
        previous_start, previous_end = start, end
        if not str(cue.get("corrected_text") or cue.get("text") or "").strip():
            failures.append("blank_cue")
    return cues, sorted(set(failures))


def _text(cue: dict) -> str:
    return str(cue.get("corrected_text") or cue.get("text") or "")


def evaluate_auto_acceptance(*, job_id: str, source_sha256: str,
                             source_audio_sha_verified: bool,
                             source_duration_ms: int,
                             srt_bytes: bytes, txt_bytes: bytes,
                             reference_txt_bytes: bytes | None,
                             original_srt_bytes: bytes | None,
                             acoustic_review_items: list[dict] | None,
                             chirp_stage: str,
                             external_provider_reconciled: bool = False,
                             source_revision_verified: bool = False) -> dict[str, Any]:
    """Produce an auditable *independent* automatic acceptance decision.

    Machine acceptance is limited to source binding, format, ordered SRT/TXT,
    timebase, and full disclosure of remaining risks. Its nonhuman output may
    be delivered as a machine-checked draft; it does NOT imply Golden PASS.
    """
    failures: list[str] = []
    if not re.fullmatch(r"v2p_[0-9a-f]{32}", job_id):
        failures.append("invalid_job_id")
    if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        failures.append("invalid_source_sha")
    if not source_audio_sha_verified:
        failures.append("unverified_source_audio_sha")
    if type(source_duration_ms) is not int or not 1000 <= source_duration_ms <= 24*3600*1000:
        failures.append("unverified_source_duration")
    if not srt_bytes or len(srt_bytes) > 20_000_000 or not txt_bytes or len(txt_bytes) > 20_000_000:
        failures.append("missing_or_oversized_subtitle_artifacts")
    cues, cue_failures = _check_cues(srt_bytes) if srt_bytes else ([], ["no_srt"])
    failures.extend(cue_failures)
    transcript = "\n".join(_text(c) for c in cues) + "\n" if cues else ""
    try:
        supplied = txt_bytes.decode("utf-8-sig").replace("\r\n", "\n")
        if supplied != transcript:
            failures.append("txt_does_not_match_srt_in_order")
    except UnicodeError:
        failures.append("invalid_utf8_txt")
    if cues and 1000 <= source_duration_ms and cues[-1]["end_ms"] > source_duration_ms + 1000:
        failures.append("subtitles_extend_past_source_audio")
    # Long silent/speech-uncertain intervals are report candidates, NOT proof
    # of actual missing speech. Never use these to invent words.
    windows=[]
    if cues:
        cursor=0
        for i,cue in enumerate(cues):
            if cue["start_ms"] - cursor >= 3000:
                windows.append({"start_ms":cursor,"end_ms":cue["start_ms"],
                                "following_cue_index":i+1})
            cursor=max(cursor,cue["end_ms"])
        if source_duration_ms-cursor >= 3000:
            windows.append({"start_ms":cursor,"end_ms":source_duration_ms,
                            "following_cue_index":None})
    acoustic=[]
    for item in acoustic_review_items or []:
        start=item.get("gap_start_ms",item.get("start_ms"))
        end=item.get("gap_end_ms",item.get("end_ms"))
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= source_duration_ms:
            failures.append("invalid_acoustic_review_interval")
            continue
        disposition=item.get("disposition",item.get("acoustic_disposition","REVIEW"))
        if disposition not in ("NONSPEECH_CONFIRMED","SPEECH_CONFIRMED","REVIEW"):
            failures.append("invalid_acoustic_disposition")
            continue
        acoustic.append({"index":item.get("index"),"start_ms":start,"end_ms":end,
                         "disposition":disposition,
                         "decision":("LOCAL_NONSPEECH_EVIDENCE" if disposition=="NONSPEECH_CONFIRMED"
                                     else "UNRESOLVED_NO_HUMAN_ATTESTATION")})
    original_diffs = []
    if original_srt_bytes is not None:
        old, old_failures = _check_cues(original_srt_bytes)
        if old_failures:
            failures.append("invalid_original_reference_srt")
        elif len(old) != len(cues):
            # Different segmentation is allowed, but must be clearly disclosed.
            original_diffs.append({"type":"CUE_COUNT_DIFFERS","original_count":len(old),
                                   "output_count":len(cues)})
        else:
            for i,(left,right) in enumerate(zip(old,cues)):
                if _normalize(_text(left)) != _normalize(_text(right)):
                    original_diffs.append({"index":i+1,"start_ms":right["start_ms"],
                        "end_ms":right["end_ms"],"original":_text(left)[:100],
                        "output":_text(right)[:100]})
    reference_match = None
    if reference_txt_bytes is not None:
        try:
            reference = _normalize(reference_txt_bytes.decode("utf-8-sig"))
            output = _normalize(transcript)
            # Similarity is contextual only; never an independent accuracy score.
            reference_match = {
                "normalized_text_similarity_context_only":round(
                    SequenceMatcher(None,reference[:4000],output[:4000],autojunk=False).ratio(),4),
                "reference_sha256":_sha(reference_txt_bytes),
                "accuracy_certification":False,
                "comparison_scope":"first_4000_normalized_characters",
            }
        except UnicodeError:
            failures.append("invalid_reference_txt_encoding")
    # A source-matched local non-speech classification is a reason to skip
    # a review recommendation for that bounded gap, NOT Golden speech completeness.
    non_speech={(x['start_ms'],x['end_ms']) for x in acoustic
                if x['disposition']=='NONSPEECH_CONFIRMED'}
    open_windows=[x for x in windows if (x['start_ms'],x['end_ms']) not in non_speech]
    unresolved_acoustic=[x for x in acoustic if x['disposition']!='NONSPEECH_CONFIRMED']
    # Text content may be complete-looking even if an acoustic region is omitted.
    # Those uncertainty types are always disclosed, not silently resolved.
    blockers=sorted(set(failures))
    decision=TIER if not blockers else "AUTO_BLOCKED_TECHNICAL"
    return {
        "schema":SCHEMA, "status":decision,"quality_tier":TIER,
        "can_close_without_human":not blockers,
        "formal_golden_pass":False,"golden_qa_status":"NOT_CLAIMED",
        "audio_fully_listened":False,"source_sha256":source_sha256,
        "source_audio_sha_verified":source_audio_sha_verified,
        "source_duration_ms":source_duration_ms,
        "job_id":job_id,"chirp_stage":chirp_stage,
        "external_provider_reconciled":external_provider_reconciled,
        "source_revision_verified":source_revision_verified,
        "checks":{"srt_structure":"PASS" if not cue_failures else "FAIL",
                  "srt_txt_ordered_match":"FAIL" if "txt_does_not_match_srt_in_order" in blockers else "PASS",
                  "source_audio_sha":"PASS" if source_audio_sha_verified else "FAIL",
                  "subtitle_timebase":"FAIL" if "subtitles_extend_past_source_audio" in blockers else "PASS"},
        "artifact_shas":{"srt_sha256":_sha(srt_bytes),"txt_sha256":_sha(txt_bytes)},
        "subtitle_cue_count":len(cues),"subtitle_last_end_ms":cues[-1]["end_ms"] if cues else None,
        "subtitle_gaps_over_3s_count":len(windows),
        "subtitle_gaps_over_3s":windows,
        "acoustic_review_items":acoustic,
        "acoustic_nonspeech_confirmed_count":len(non_speech),
        "acoustic_open_item_count":len(unresolved_acoustic),
        "acoustic_triage_status":"LOCAL_ONLY" if acoustic_review_items is not None else "NOT_RUN",
        "reference_txt_comparison":reference_match,
        "original_srt_disagreements":original_diffs,
        "technical_blockers":blockers,
        "human_review_advisory":{
           "mandatory_for_auto_closeout":False,
           "recommended": bool(unresolved_acoustic or open_windows or original_diffs or not external_provider_reconciled),
           "notes":["Source speech completeness was NOT independently certified.",
                    "SRT/TXT machine checks do not certify exhaustive transcription; origin may be human-edited or ASR.",
                    "Unresolved acoustic gaps and semantic differences remain advisory.",
                    "Do not represent this quality tier as Golden PASS."],
        },
        "no_new_paid_inference":True,
        "no_original_overwrite_allowed":True,
    }


def render_human_review_advisory(result: dict, *, lesson_title: str) -> str:
    """An optional, non-blocking human review handoff; no unearned QA claims."""
    if result.get('schema') != SCHEMA or result.get('status') not in {
        TIER, 'AUTO_BLOCKED_TECHNICAL'}:
        raise ValueError('invalid auto acceptance result')
    lines = [f'# {lesson_title}｜自動驗收與建議人工複核報告', '',
             f"- 自動驗收：**{result['status']}**",
             '- Golden 正式聲學驗收：**未宣稱通過**',
             '- 新增人工覆聽：無（本報告由系統產生）',
             f"- Job：`{result['job_id']}`",
             f"- 音訊 SHA-256：`{result['source_sha256']}`",
             f"- SRT 段落：{result['subtitle_cue_count']}",
             f"- 3 秒以上字幕空檔：{result['subtitle_gaps_over_3s_count']}",
             f"- 聲學待核對區間：{result.get('acoustic_open_item_count',len(result['acoustic_review_items']))}",
             f"- 已有聲學非語音證據可略過：{result.get('acoustic_nonspeech_confirmed_count',0)}",
             f"- 舊版與輸出文字差異：{len(result['original_srt_disagreements'])}",
             f"- Chirp 階段：{result['chirp_stage']}",
             '- 舊 Chirp 雲端作業對帳：'+('已獨立完成' if result['external_provider_reconciled'] else '未完成'),
             '', '## 自動驗收檢查', '']
    for key,value in result['checks'].items():
        lines.append(f'- `{key}`：{value}')
    if result['technical_blockers']:
        lines += ['', '## 技術性阻擋', '']
        lines += [f'- {v}' for v in result['technical_blockers']]
    lines += ['', '## 建議人工核對（不影響本級別自動結案）', '',
              '下列是需優先複核的候選，不代表已證實漏字，也不應自動把原版文字蓋回人工校正版。', '']
    def fmt(ms):
        return f'{ms//3600000:02d}:{ms//60000%60:02d}:{ms//1000%60:02d}.{ms%1000:03d}'
    for item in [v for v in result['acoustic_review_items'] if v['disposition']!='NONSPEECH_CONFIRMED'][:60]:
        lines.append(f"- 聲學：{fmt(item['start_ms'])}–{fmt(item['end_ms'])}；"
                     f"{item['disposition']}；仍待音訊核對")
    lines += ['', '### 優先檢查字幕空檔（最長的前 30 段）', '']
    dismissed={(x['start_ms'],x['end_ms']) for x in result['acoustic_review_items']
               if x['disposition']=='NONSPEECH_CONFIRMED'}
    windows=sorted([x for x in result['subtitle_gaps_over_3s']
                    if (x['start_ms'],x['end_ms']) not in dismissed],
                   key=lambda v:v['end_ms']-v['start_ms'],reverse=True)[:30]
    lines += [f"- {fmt(x['start_ms'])}–{fmt(x['end_ms'])}（{(x['end_ms']-x['start_ms'])/1000:.1f}s）"
              for x in windows]
    lines += ['', '### 文字差異（前 30 項；完整清單見 JSON）', '']
    for x in result['original_srt_disagreements'][:30]:
        if 'start_ms' not in x:
            lines.append(f"- 分段數差異：{x['original_count']} → {x['output_count']}")
            continue
        lines += [f"- {fmt(x['start_ms'])}：原版「{x['original']}」；輸出「{x['output']}」"]
    lines += ['', '## 級別邊界', '',
              '本次可完成的是無須真人介入的**來源與字幕結構／逐字稿一致性之自動驗收**，而非聲稱所有語音已逐字覆核。',
              '如需認證為 Golden PASS，仍須取得符合嚴格聲學及語意 Gate 的獨立證據。',
              '本報告不是付費模型請求、人工已聽證明或舊 Golden Ledger 結案宣告。', '']
    return '\n'.join(lines)
