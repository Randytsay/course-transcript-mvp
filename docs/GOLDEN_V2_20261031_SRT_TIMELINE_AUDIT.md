# 2026-10-31 《金剛果論》31～32 — SRT timeline incident, DEV-only gate

## Verified from the actual 2026-11-04 Drive delivery

Source folder (same original parent): https://drive.google.com/drive/folders/14YfBNRbSsXaUcdEhy5GaSmOJLDHDjL55

Drive contains the original `261031_.MP3`, earlier `261031_逐字稿.txt`, and `261031_AI校對_含待複核.txt` plus `261031_AI校對_含待複核.srt`. The AI-limited files were created 2026-11-04. Do not overwrite or republish them without Owner Gate D. File presence is **not** Golden QA.

An independent local UTF-8 parse of the **actual 212,451-byte SRT** found:

| Evidence | Observation |
| --- | --- |
| Number of parsed sequential SubRip cues | 2,879 |
| Malformed blocks, overlap, start-after-end | 0, 0, 0 |
| Start / end | 00:00:01,000 / 02:55:15,680 |
| Text consistency | Concatenated SRT visible characters **exactly match** `261031_AI校對_含待複核.txt` with only whitespace stripped; not proof of audio accuracy |
| Text > 42 Unicode visible characters | 3 cues: #212 (656), #420 (60), #1607 (61) |
| Very short cues | 217 cues below 300 milliseconds |
| #212 timeline | 00:13:25,400–00:13:29,000 for **656** characters; next cue #213 starts at 00:20:19,200, a **410.20-second** gap |
| Another severe gap | after #420 00:29:49,920 to #421 00:32:53,440 (**183.52 s**); after #421 00:32:54,020 to #422 00:35:35,280 (**161.26 s**) |
| Known Golden status in the 2026-11-06 AGY read-only inventory | Job `v2p_6c8bfcd842315946abbac8d257f0bb70`: `NEEDS_REVIEW`, revision 4, Chirp `REVIEW_REQUIRED`, `golden_qa=PENDING` |

The severe gaps here total at least 12 minutes 35 seconds in these three intervals alone. **Gaps are time windows without visible subtitles, not independently proven missing spoken words**. The existing corrected TXT text is already mirrored in SRT, so re-running paid transcription is **not** justified by this formatting evidence.

## New independent, provider-free guard

`scripts/golden_v2_offline_srt_timeline_qa.py` validates cue numbering, nonoverlapping time order, disproportionate character rates, overlong cues, gaps and tiny cues. It checks **exact transcript-string preservation** against the corrected TXT after removing whitespace. It may optionally write a **review-only** SRT candidate by merging tiny adjacent cues ONLY if the intervening gap is at most 250 ms and combined visible characters at most 42. It never adjusts a timestamp, extrapolates a 656-character block across 410 seconds, drops or rewrites text, calls paid ASR, touches Drive or claims Golden PASS.

Every successful report and CLI exit remains deliberately **NEEDS_AUDIO_REVIEW / nonzero**, even when benign fixes reduce the short-cue count. Negative regression tests prove that 410-second gaps cannot be filled automatically, text is unchanged, bad indexing/overlap fails closed, and source files are never overwritten.

### Offline use (private DEV input only, not from Production release)

```bash
python3 scripts/golden_v2_offline_srt_timeline_qa.py \
  --srt "./261031_AI校對_含待複核.srt" \
  --reference-txt "./261031_AI校對_含待複核.txt" \
  --report-output "./261031_時間軸查核_非驗收.json" \
  --draft-output "./261031_僅合併短字幕_禁止發布.srt"
```

**Exit status 2 is expected:** this tool is NOT a Golden acceptance gate. Private lecture content/audio is not stored in this GitHub repo; only code, synthetic tests and aggregate evidence are. Existing Drive files are not overwritten.

## What remains before a correct final transcript and subtitles

1. Inspect existing source-bound Whisper/Chirp/recognition window artifacts and segmentation/merge source of the 20261031 pipeline, **read-only**, especially the three severe gap ranges; compare provider text/timing against the original MP3/FLAC duration; do not invent timestamps or spend new provider money.
2. Align the 656-character block and the two long gap regions with **audio-supported** timestamps, and check all 217 short cues. Record immutable source and result SHA, which text moved and why, and any unresolved human-review intervals.
3. Create a **new versioned** TXT/SRT as a private candidate; assert exact text coverage, monotonic timing, readable cue lengths/rates, no silent gaps containing unaddressed source speech. Report `REVIEW_REQUIRED` if acoustic evidence is incomplete.
4. Only with valid Golden QA and separate owner-authorized Drive publication grant may it create files in the source folder. Avoid overwriting the existing `_含待複核` outputs.

Refs: [Issue #160](https://github.com/Randytsay/course-transcript-mvp/issues/160), [read-only AGY inventory](https://github.com/Randytsay/course-transcript-mvp/issues/173#issuecomment-6681086336), [Master plan](GOLDEN_V2_GO_LIVE_MASTER_PLAN.md).

## 2026-11-07 AGY-LOCAL-004 subsequent evidence and review-only manifest

- [AGY-LOCAL-004 result](https://github.com/Randytsay/course-transcript-mvp/issues/173#issuecomment-6706738290) was accepted for **local inventory only** in [REVIEW #173](https://github.com/Randytsay/course-transcript-mvp/issues/173#issuecomment-6706803619). `chirp-merged.json` is reported to contain **29,769** source-bound words and about **92.8% time coverage**. These are operator-reported counts, not an independent re-read of the protected source by this PR.
- **Exact three major SRT gaps** are 410.20s + 183.52s + 161.26s = **754.98 seconds**. Separately, AGY proposed padded human listening windows totaling about **837 seconds**. The two numbers refer to **different scopes**, must never be substituted for one another.
- **Critical exception:** AGY also cites a provider-raw word at **1,942.840 seconds**, inside the SRT cue 420→421 gap (**1,789.920–1,973.440 seconds**). This means one cannot simply assert every provider payload is empty throughout that full visible gap. The manifest explicitly flags `PROVIDER_ANCHOR_WITHIN_SRT_GAP` if any source-bound word overlaps a gap, so the contradiction is not hidden.
- `chirp-merged.json` may have coverage gaps despite audible waveform energy: acoustic peak and RMS activity **do not establish which words were spoken**. Neither interpolation nor re-timing is authorized by waveform energy alone.

### Review interval manifest from the existing SRT (zero cloud calls)

```bash
python3 scripts/golden_v2_offline_srt_timeline_qa.py \
  --srt "./261031_AI校對_含待複核.srt" \
  --reference-txt "./261031_AI校對_含待複核.txt" \
  --audio-duration-ms 10517199 \
  --review-intervals-output "./261031_audio_review_intervals_PRIVATE.json"
```

The output contains for each **SRT gap ≥8 seconds**: exact start/end and duration, cue indices, **separately padded** ±3s suggested listening window, and `provider_evidence_state=PROVIDER_WORD_EVIDENCE_NOT_SUPPLIED` by default. The caller must not misinterpret omission of private timing words as zero recognition. The original TXT/SRT are never modified, no clip is extracted and the program always returns **exit 2** (no Golden PASS).

If a properly authorized operator later has metadata-only provider word bounds available **locally**, the optional input JSON is:
```json
{
  "schema": "golden-v2-source-bound-word-timing-evidence-v1",
  "source_sha256": "<the exact preverified normalized audio SHA-256>",
  "complete_source_word_inventory": false,
  "words": [{"start_ms": 1000, "end_ms": 1100}]
}
```

Set `complete_source_word_inventory=true` **only** if the operator independently verified that the word metadata includes the complete existing provider artifact for this precise source. This flag is an operator assertion, not independently cryptographic proof of speech coverage. Supply `--expected-source-sha` and `--provider-words-json` together. A mismatched SHA or invalid time range fails closed. Even a complete inventory with zero words produces `EVIDENCE_MISSING_NO_PROVIDER_WORD_ANCHOR`, **not** proof that the audio was silent or license to invent timestamps.

**No actual audio alignment, human acoustic listening, billable model call, Production mutation or Drive upload has been authorized/performed by this documentation or PR.**
