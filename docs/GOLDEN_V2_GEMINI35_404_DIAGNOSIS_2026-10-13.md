# Golden V2 Gemini 3.5 Transcribe: 404 model-readiness diagnosis

Date 2026-10-13. No further paid provider requests authorized or sent during this diagnosis.

## Proven evidence

- Project xenon-chain-506409-c3 Vertex AI API is ENABLED.
- Gemini Developer API (generativelanguage.googleapis.com) is DISABLED for this project, but earlier 3.5 attempts used **Vertex AI**; do not conflate the APIs or automatically enable a new billing channel.
- Two one-shot calls of Vertex AI \`gemini-3.5-transcribe\` with same owner-approved 240-second lecture: first \`ClientError\` unknown cause, second **HTTP 404 / NOT_FOUND**; both independently write immutable claims. Second returned error receipt, no transcript receipt. Do not resubmit either attempt.
- The second attempt used exactly the same decoded 240s WAV source as FLAC and \`GenerateContentConfig(audio_transcription_config=AudioTranscriptionConfig(mode="VERBATIM", language_codes=["zh-TW"]))\` at \`location="global"\`. Source format and GitHub OIDC worked; 404 is best treated as model lookup/availability until proven otherwise.
- Google official Agent Platform documentation says \`gemini-3.5-transcribe\` **GA** launched October 2026; \`gemini-3.5-transcribe-preview\` **Preview** launched August 2026.
  https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-5-transcribe
- Live Model Garden \`GET /v1beta1/publishers/google/models?pageSize=100&listAllVersions=true\`, with quota project header, returned **HTTP 200, 27 models, no nextPageToken**. The known working \`gemini-3.8-flash\` was listed; \`gemini-3.5-transcribe-preview\` was listed; the GA \`gemini-3.5-transcribe\` was NOT listed.
- Repeated the above using Golden V2's **actual GitHub OIDC dedicated service account**, with identical results and **no inference**: [read-only Github run 38386945951](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38386945951), log markers:
  \`MODEL_GARDEN_LIST_HTTP=200\`,
  \`CATALOG_VISIBLE_MODEL_COUNT=27\`,
  \`CATALOG_KNOWN_GOOD_LISTED=true\`,
  \`CATALOG_TRANSCRIBE_GA_LISTED=false\`,
  \`CATALOG_TRANSCRIBE_PREVIEW_LISTED=true\`,
  \`PAID_MODEL_REQUESTS=0\`.
- Public and dedicated SA query both agree. Catalog absence alone is **not** proof of model absence or of the inference 404 cause: with the correct `x-goog-user-project` header, read-only `GET /v1beta1/publishers/google/models/gemini-3.5-transcribe` returned **HTTP 200** (name, `launchStage=GA`) despite the GA model being absent from the list. `GET` for Preview also returned 200 (`PUBLIC_PREVIEW`) and known-good 3.8 returned 200 (`GA`). This makes it an API/listing asymmetry; a successful metadata GET does **not** establish successful inference authorization. Preserve the 404 as a serving/entitlement issue to escalate. Catalog omission is a deliberately conservative pre-dispatch safety gate, not definitive entitlement proof; a listed Preview model isn't a guaranteed successful inference.

## Corrective safety implementation

- \`scripts/golden_v2_model_catalog_gate.py\` performs **GET-only** model catalog inspection using workload ADC; fail-closed for pagination, invalid catalog or missing known-good control. \`--report-only\` produces status for CI.
- Both existing third-model dispatch scripts must call \`assert_model_available(PROJECT,MODEL)\` BEFORE creating an immutable paid request claim and BEFORE any provider inference. No automatic fallback from GA to Preview. No additional Cloud permissions, keys or API enablement.
- Unit tests in \`tests/test_golden_v2_model_catalog_gate.py\` verify GA-absent rejection, Preview visible but not entitled, and fail-closed behavior.
- Four-minute user-approved two-model result (Gemini 3.8 main text and Chirp acoustic timestamps) remains available; user chose 12/12 model preferences, confirmed "所以比如有長行" as accurate.
- All paid workflow toggles stay false. V1 remains untouched.

## Next trusted steps

1. If GA remains absent in this project, ask Google Cloud support about GA enablement/catalog rollout, including exact project id, 404 status, model, SDK version 2.29.0, global location, and WIF SA.
2. Optionally, after explicit **new once-only paid authorization**, test \`gemini-3.5-transcribe-preview\` via a NEW immutable claim, keeping all old GA attempts frozen. Set the preview model ID explicitly; preserve unknown charges for old GA attempts. Do not reuse old claim or relabel it.
3. Before each next paid request, check current Model Garden catalog and exact model ID, pricing, billing and limits, then verify actual inference via receipt.
