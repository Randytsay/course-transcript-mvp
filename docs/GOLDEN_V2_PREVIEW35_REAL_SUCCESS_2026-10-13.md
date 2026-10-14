# Golden Transcript V2 — Gemini 3.5 Transcribe Preview REAL success (2026-10-13)

## Owner authorization and limits

The owner expressly authorized **one** real paid test of `gemini-3.5-transcribe-preview` (NOT the GA model), restricted to the existing same **four-minute original excerpt 600–840 seconds**, within a **USD 2 application-stage reserve** and aggregate **USD 10** authorization. This does **not** constitute a Google Cloud invoice hard cap. The final billed charges for two older failed GA attempts remain unknown.

## Independently verified real provider output

- One-time [GitHub Actions run 38388749674](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38388749674) succeeded with `THIRD_GEMINI35_ONE_SHOT=SUCCESS`, provider `STATUS=COMPLETE`, no retries.
- Model: `gemini-3.5-transcribe-preview`; Vertex AI `global`, Google GenAI SDK `2.29.0`; `AudioTranscriptionConfig(mode=VERBATIM, language_codes=["zh-TW"])`; input as `types.Part.from_bytes(...,mime_type="audio/wav")`.
- Source exactly the same **240 seconds** as all other models, original FLAC SHA `17db78ae930d28be3de930467ac256166de54fa62bb2e9cd361cdb58587d5621`. Decoded-equivalent 16-kHz mono PCM16 WAV SHA `b199e203f5500f92ea9c2962ca7e20497b94fad737b28775bae7c52480568447`, length 7,680,156 bytes.
- Cloud CAS claim `gs://xenon-chain-506409-c3-golden-v2-smoke/golden-v2-smoke/<sourceSHA>/paid/third-gemini35-preview-future-consent.claim.json` persisted **BEFORE** provider POST; SHA256 `bd83cf22c65e927270c15ebd62de4cb466759a3e95fc1d0feeac683fcaa13e9b`.
- Cloud immutable raw provider receipt `gs://xenon-chain-506409-c3-golden-v2-smoke/golden-v2-smoke/<sourceSHA>/paid/third-gemini35-preview-future-consent.receipt.json`, read back and SHA256 `2d56ab1ed3743408e4a0ae86082a3d84f4a41210de62d3892bb006bf4c6f1430`. Separate error object absent (HTTP 404), confirming no failure receipt was written for this invocation.
- Real provider response: **813 transcript characters**; `finish_reason=STOP`; `prompt_token_count=6000` (AUDIO), `candidates_token_count=610` (TEXT), `total_token_count=6610`. Transcript SHA256 `91f101f11d570ddc9f832a177d439ac4c1bda8236256f9f35692e8e0be556a22`.
- Application cost estimate using scripted standard pricing and conservative round-up: **2 US cents**, not a verified Cloud Billing invoice. Prior *successful* Gemini 3.8 estimate 1 cent and Chirp 3 estimate 7 cents, for **10 cents** estimated across three completed paid provider responses. Two old GA failures are **excluded** because exact provider billing remains unknown.
- All paid workflow env flags switched back to **false** and preview-specific approval string cleared in commit `55a70b2ca7f60d886c6e19214d04fa7f85a260df` immediately after first success; its CI, Release Compose and disabled paid preview run all SUCCESS. **Never re-enable or rerun this spent claim**.

## Reconciliation without corrupting failed GA status

The two older GA `gemini-3.5-transcribe` claims and their second `HTTP 404/NOT_FOUND` error receipt remain immutable. Original VPS trial ledger stages:
- ordinal 0 `gemini-3.8-flash: COMPLETE, 1 cent`;
- ordinal 1 `chirp_3: COMPLETE, 7 cents`;
- ordinal 2 `gemini-3.5-transcribe: OUTCOME_UNKNOWN, cost unknown`.

To prevent erasing that audit history, the newly completed **Preview** stage is separately recorded in the same isolated SQLite as `external_preview_result`, row `gemini-3.5-transcribe-preview: COMPLETE, 2 cents`, with frozen original claim+receipt SHA. Cloud original evidence is copied with file modes `0600` to `smoke/receipts/external-preview35-gcs-claim.json` and `...gcs-receipt.json`. Any future V2 status/report integration must **show both** the old GA outcome as unknown and the successful Preview result, never relabel the old GA stage as if it succeeded.

## Text quality comparison (NOT an accuracy benchmark)

- Gemini 3.8: 910 text characters, semantic main draft.
- Chirp 3: 824 characters, 710 validated word-offset acoustic pieces and candidate timestamp authority.
- Gemini 3.5 Preview: 813 characters, independent third-party VERBATIM draft.

After stripping punctuation/whitespace, the Preview draft has **92.6% similarity** to Gemini 3.8 and **89.7% similarity** to Chirp 3. Neither figure is WER, CER, audio coverage or correctness. The owner's 12/12 segment **model preferences** have been collected. The owner explicitly confirmed `所以比如有長行` (Gemini 3.8) as more accurate; Preview produced `所以比喻有長行的`, while Chirp produced `所以這裡有長行的`. Therefore **do not replace** owner-confirmed vocabulary based on the new Preview draft. Further acoustic alignment, per-word QA and human approval are required for true Golden text/SRT.

Local owner Mac (not GitHub artifact): `~/Downloads/Golden_V2_4min_20261013_REVIEW/` contains `gemini35-preview-third-DRAFT.txt`, `Golden_V2_三模型真實比對_待驗收.md`, 12/12 listening JSON, preview GCS claim/receipt copies and preexisting audio+other model drafts. Complete nine-file review archive: `~/Downloads/Golden_V2_4min_20261013_三模型實測_人工驗收.zip` SHA `72c88ca9b78f03925bdf3168bffc126d3779f0b09af11fcd94095263e3c8133b`.

## Next gates

1. Merge the three real model **draft texts** and Chirp acoustic offsets with **owner-corrected language**, check all 240 seconds for omissions, sentence boundaries, timing and Buddhist proper nouns.
2. Produce a final **Golden CANDIDATE** TXT/SRT with QA evidence; require approval before overwriting Google Drive or publishing to Cisheng platform.
3. V2 full G0–G8 acceptance, full audio workload, backups/recovery and release gate remain outstanding. Draft PR #144 must remain unmerged for now. V1 API/frontend healthy and untouched.
