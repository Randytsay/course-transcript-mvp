"""Independent machine-verifiable acceptance tier for TXT/SRT delivery.

The tier can close without a person listening, but NEVER certifies source
speech was exhaustively recognized or mutates the strict Golden PASS gate.
"""
from __future__ import annotations

from bisect import bisect_left
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


def _nonspeech_probes_valid(item: dict, start: int, end: int) -> bool:
    if 'reason' in item and item['reason'] != 'local_segmented_audio_evidence':
        return False
    probes=item.get('probes')
    if not isinstance(probes,list) or not probes:
        return False
    if 'slice_count' in item and (type(item['slice_count']) is not int
                                  or item['slice_count'] != len(probes)):
        return False
    cursor=start
    for probe in probes:
        if not isinstance(probe,dict):
            return False
        pstart,pend=probe.get('start_ms'),probe.get('end_ms')
        if (type(pstart) is not int or type(pend) is not int or pstart!=cursor
                or not start<=pstart<pend<=end
                or probe.get('disposition')!='NONSPEECH_CONFIRMED'
                or ('reason' in probe and probe['reason'] not in ('non_speech_evidence', 'local_segmented_audio_evidence'))
                or ('acoustic_disposition' in probe and
                    probe['acoustic_disposition'] != probe['disposition'])):
            return False
        volume=probe.get('max_volume_dbfs')
        vad=probe.get('vad')
        # Finite plausible dBFS only; reject malformed measurements even if
        # another field also claims non-speech. FFmpeg's digital-silence floor
        # is -91 dBFS, so -200 leaves ample headroom for other implementations.
        if volume is not None and (type(volume) not in (int, float) or not -200 <= volume <= 0):
            return False
        if vad is not None and not _vad_nonspeech_evidence_consistent(vad, pend-pstart):
            return False
        # PCM resampling/downmix can move the peak slightly; it cannot explain
        # an RMS tens of dB above the original peak.
        if vad is not None and volume is not None and vad['rms_dbfs'] > volume + 1:
            return False
        if not ((volume is not None and volume<=-65)
                or (vad is not None and vad['robust_non_speech'] is True)):
            return False
        cursor=pend
    return cursor==end


def _vad_nonspeech_evidence_consistent(vad: Any, duration_ms: int) -> bool:
    """Recompute VAD from rounded producer measurements and frame counts.

    Sidecar thresholds may be stricter, never looser than producer defaults.
    RMS is rounded to 0.01 dB; a threshold-straddling value is not proof.
    """
    if (not isinstance(vad, dict) or vad.get('engine') != 'webrtcvad'
            or type(vad.get('sample_rate_hz')) is not int or vad['sample_rate_hz'] != 16000
            or type(vad.get('frame_ms')) is not int or vad['frame_ms'] != 30
            or type(vad.get('frame_count')) is not int
            or not 10 <= vad['frame_count'] <= (duration_ms + 29)//30 + 1
            or type(vad.get('robust_non_speech')) is not bool
            or type(vad.get('robust_speech')) is not bool or vad['robust_speech']):
        return False
    thresholds=vad.get('thresholds')
    if not isinstance(thresholds,dict):
        return False
    limits=(('mode0_max',0,0.15),('mode3_max',0,0.08),('rms_max_dbfs',-120,-35))
    if any(type(thresholds.get(k)) not in (int,float) or not low <= thresholds[k] <= high
           for k,low,high in limits):
        return False
    speech_limits=(('mode0_speech_min',0,1),('mode3_speech_min',0,1),
                   ('rms_speech_min_dbfs',-120,0))
    if any(type(thresholds.get(k)) not in (int,float) or not low <= thresholds[k] <= high
           for k,low,high in speech_limits):
        return False
    count=vad['frame_count']
    ratios=[]
    for key in ('mode0_speech_ratio','mode3_speech_ratio'):
        value=vad.get(key)
        if type(value) not in (int,float) or not 0 <= value <= 1:
            return False
        hits=round(value*count)
        if round(hits/count,4) != value:
            return False
        ratios.append(hits/count)
    rms=vad.get('rms_dbfs')
    if type(rms) not in (int,float) or not -120 <= rms <= 0:
        return False
    derived=(ratios[0] < thresholds['mode0_max'] and ratios[1] < thresholds['mode3_max']
             and rms + 0.005 <= thresholds['rms_max_dbfs'])
    if (ratios[0] >= thresholds['mode0_speech_min']
            and ratios[1] >= thresholds['mode3_speech_min']
            and rms - 0.005 >= thresholds['rms_speech_min_dbfs']):
        return False
    if vad['robust_non_speech']:
        return derived
    return not derived


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
    valid_duration=type(source_duration_ms) is int and 1000 <= source_duration_ms <= 24*3600*1000
    if not valid_duration:
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
    if cues and valid_duration and cues[-1]["end_ms"] > source_duration_ms + 1000:
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
        if valid_duration and source_duration_ms-cursor >= 3000:
            windows.append({"start_ms":cursor,"end_ms":source_duration_ms,
                            "following_cue_index":None})
    acoustic=[]
    if acoustic_review_items is not None and not isinstance(acoustic_review_items, list):
        failures.append('invalid_acoustic_review_items')
    for item in acoustic_review_items if isinstance(acoustic_review_items, list) else []:
        if not isinstance(item, dict):
            failures.append('invalid_acoustic_review_items')
            continue
        if 'index' in item and (type(item['index']) is not int or item['index'] < 0):
            failures.append('invalid_acoustic_review_index')
            continue
        start=item.get("gap_start_ms",item.get("start_ms"))
        end=item.get("gap_end_ms",item.get("end_ms"))
        if (not valid_duration or type(start) is not int or type(end) is not int or not 0 <= start < end <= source_duration_ms
                or any(name in item and (type(item[name]) is not int or item[name] != value)
                       for name,value in (("start_ms",start),("gap_start_ms",start),
                                          ("end_ms",end),("gap_end_ms",end)))):
            failures.append("invalid_acoustic_review_interval")
            continue
        disposition=item.get("disposition",item.get("acoustic_disposition","REVIEW"))
        invalid_disposition = (disposition not in ("NONSPEECH_CONFIRMED","SPEECH_CONFIRMED","REVIEW")
            or any(name in item and item[name] != disposition
                   for name in ('disposition','acoustic_disposition')))
        if invalid_disposition:
            failures.append("invalid_acoustic_disposition")
            disposition = 'REVIEW'
        invalid_evidence = disposition == "NONSPEECH_CONFIRMED" and not _nonspeech_probes_valid(item,start,end)
        missing_sidecar = item.get('reason')=='sidecar_inventory_missing'
        if invalid_evidence or missing_sidecar:
            disposition = "REVIEW"
        acoustic.append({"index":item.get("index"),"start_ms":start,"end_ms":end,
                         "disposition":disposition,
                         "decision":("SIDECAR_INVENTORY_MISSING" if missing_sidecar else
                                     "INVALID_ACOUSTIC_DISPOSITION" if invalid_disposition else
                                     "INVALID_NONSPEECH_EVIDENCE" if invalid_evidence else
                                     "LOCAL_NONSPEECH_EVIDENCE" if disposition=="NONSPEECH_CONFIRMED"
                                     else "UNRESOLVED_NO_HUMAN_ATTESTATION")})
    # Consolidate duplicate coordinates conservatively: uncertain or conflicting
    # evidence always wins over non-speech, regardless of input order.
    grouped={}
    for item in acoustic:
        key=(item['start_ms'],item['end_ms'])
        previous=grouped.get(key)
        if previous is None:
            grouped[key]=item
        elif previous['disposition'] != item['disposition'] or previous['decision'] != item['decision']:
            decisions={previous['decision'],item['decision']}
            grouped[key]={**previous,'disposition':'REVIEW',
                          'decision':('SIDECAR_INVENTORY_MISSING' if 'SIDECAR_INVENTORY_MISSING' in decisions
                                      else 'INVALID_NONSPEECH_EVIDENCE' if 'INVALID_NONSPEECH_EVIDENCE' in decisions
                                      else 'CONFLICTING_ACOUSTIC_EVIDENCE')}
    acoustic=list(grouped.values())
    pending=sorted((x['start_ms'],x['end_ms']) for x in acoustic
                   if x['disposition']!='NONSPEECH_CONFIRMED')
    starts=[start for start,_ in pending]
    max_ends=[]
    for _,end in pending:
        max_ends.append(max(end,max_ends[-1] if max_ends else end))
    for item in acoustic:
        if item['disposition']=='NONSPEECH_CONFIRMED':
            pos=bisect_left(starts,item['end_ms'])
            if pos and max_ends[pos-1]>item['start_ms']:
                item['disposition']='REVIEW'
                item['decision']='OVERLAPPING_UNRESOLVED_EVIDENCE'
    original_diffs = []
    if original_srt_bytes is not None:
        old, old_failures = _check_cues(original_srt_bytes)
        if old_failures:
            original_diffs.append({"type":"INVALID_REFERENCE_SRT", "note":"Reference-only format failure; do not reject current clean output"})
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
            reference_match={"accuracy_certification":False,"warning":"INVALID_OPTIONAL_REFERENCE_TXT_ENCODING"}
    # A source-matched local non-speech classification is a reason to skip
    # a review recommendation for that bounded gap, NOT Golden speech completeness.
    non_speech={(x['start_ms'],x['end_ms']) for x in acoustic
                if x['disposition']=='NONSPEECH_CONFIRMED'}
    open_windows=[x for x in windows if (x['start_ms'],x['end_ms']) not in non_speech]
    unresolved_acoustic=[x for x in acoustic if x['disposition']!='NONSPEECH_CONFIRMED']
    classified={(x['start_ms'],x['end_ms']) for x in acoustic
                if x['decision'] not in ('INVALID_NONSPEECH_EVIDENCE','SIDECAR_INVENTORY_MISSING')}
    missing_inventory={(x['start_ms'],x['end_ms']) for x in acoustic
                       if x['decision']=='SIDECAR_INVENTORY_MISSING'}
    unclassified=[x for x in windows if (x['start_ms'],x['end_ms']) not in classified]
    open_intervals={(x['start_ms'],x['end_ms']) for x in unresolved_acoustic + unclassified}
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
                  "subtitle_timebase":"FAIL" if not valid_duration or "subtitles_extend_past_source_audio" in blockers else "PASS"},
        "artifact_shas":{"srt_sha256":_sha(srt_bytes),"txt_sha256":_sha(txt_bytes)},
        "subtitle_cue_count":len(cues),"subtitle_last_end_ms":cues[-1]["end_ms"] if cues else None,
        "subtitle_gaps_over_3s_count":len(windows),
        "subtitle_gap_reporting_min_ms":3000,
        "subtitle_gaps_over_3s":windows,
        "acoustic_review_items":acoustic,
        "acoustic_nonspeech_confirmed_count":len(non_speech),
        "acoustic_unclassified_gap_count":len(unclassified),
        "acoustic_missing_sidecar_inventory_count":len(missing_inventory),
        "acoustic_open_item_count":len(open_intervals),
        "acoustic_triage_status":("NOT_RUN" if acoustic_review_items is None else
                                   "PARTIAL" if unclassified or missing_inventory or 'invalid_acoustic_review_items' in blockers else "LOCAL_ONLY"),
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
             f"- 未分類字幕空檔：{result.get('acoustic_unclassified_gap_count','未知')}",
             f"- 聲學分流狀態：{result.get('acoustic_triage_status','未知')}",
             f"- 字幕空檔報告門檻：{result.get('subtitle_gap_reporting_min_ms', 3000)} ms（含等於門檻）",
             (f"- 聲學側車門檻：{result['acoustic_triage_gap_min_ms']} ms（含等於門檻）；"
              f"較短的字幕空檔有 {result['acoustic_triage_excluded_short_gap_count']} 段未納入聲學分流，並非已確認無語音。"
              if result.get('acoustic_triage_gap_min_ms') is not None else
              '- 聲學側車未執行；字幕空檔並非已確認無語音。'),
             f"- 已有聲學非語音證據可略過：{result.get('acoustic_nonspeech_confirmed_count',0)}",
             f"- 舊版與輸出文字差異：{len(result['original_srt_disagreements'])}",
             f"- Gemini/Chirp 比對狀態：{result.get('semantic_status','NOT_RUN_OR_NOT_ATTESTED')}",
             f"- 語意差異候選：{len(result.get('semantic_review_items',[]))}",
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
        if x.get('type')=='INVALID_REFERENCE_SRT':
            lines.append('- 原始參考 SRT 含空白或格式錯誤，僅作參考；未影響輸出 SRT 技術驗收')
            continue
        if 'start_ms' not in x:
            lines.append(f"- 分段數差異：{x['original_count']} → {x['output_count']}")
            continue
        lines += [f"- {fmt(x['start_ms'])}：原版「{x['original']}」；輸出「{x['output']}」"]
    if result.get('semantic_review_items'):
        lines += ['', '### Gemini/Chirp 或參考稿/Chirp 語意差異（前 30 項）', '']
        for x in result['semantic_review_items'][:30]:
            start=x.get('source_time_start_ms')
            time=fmt(start) if isinstance(start,int) else '未定位'
            lines.append(f"- {time}｜{x['kind']}｜Chirp「{x['chirp_excerpt']}」"
                         f"／參考「{x['reference_excerpt']}」；待複核")
    lines += ['', '## 級別邊界', '',
              '本次可完成的是無須真人介入的**來源與字幕結構／逐字稿一致性之自動驗收**，而非聲稱所有語音已逐字覆核。',
              '如需認證為 Golden PASS，仍須取得符合嚴格聲學及語意 Gate 的獨立證據。',
              '本報告不是付費模型請求、人工已聽證明或舊 Golden Ledger 結案宣告。', '']
    return '\n'.join(lines)
