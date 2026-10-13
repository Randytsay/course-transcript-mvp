# Golden V2 — 4-minute real-audio paid smoke: isolated operator handoff

This runbook is a **pending execution** handoff, **not proof of a paid smoke**.
The user agreed on 2026-10-13 to spend up to **USD 10 in total** to test the
**derived 4-minute excerpt only**, not the full 2h21m Drive recording, and not
to merge the V2 Draft PR or publish sidecars to Drive.

## Exact scope and immutable evidence (VPS only)

- Repo: `Randytsay/course-transcript-mvp`, Draft PR #144.
- Last verified real-source program revision: `ad01f987d0408439c2efad055fbee8314ccc30c8`.
- Independent VPS data root:
  `/srv/ai-workspace/projects/webcodex-vps-poc/golden-v2-real-audio-test-ad01f98/data`.
- Full source Drive file ID: `17DmsY7k1TbXfg2S0udoVqvX26lSoKBpY`,
  version `33`, downloaded SHA256
  `97ad073bfdbab1228d06861b1b96b089a2e8df8d0f368e664c230bb6f86a9ef9`.
- Original source preflight V2 job:
  `v2p_b075478d5d505ee49c330016c6d12023`
  (**AWAITING_APPROVAL**; original job cost estimates are **zero and not
  provider quotes**; DO NOT authorize or charge the full source from that job).
- Derived excerpt at `data/smoke/real-excerpt-4min.flac`:
  original interval [600, 840] seconds, 240 seconds, 16 kHz mono,
  SHA256 `17db78ae930d28be3de930467ac256166de54fa62bb2e9cd361cdb58587d5621`.
  **It does not have an independent Drive ID**, and must not be assigned the
  full original source ID/version as its own provenance.
- Parent source preflight, original source bytes, clip and local signed-by-hash
  handoff JSON must agree. This is a **local source-verification boundary**,
  not a cryptographic claim of provider identity.
- Existing file `data/smoke/real-audio-offline-smoke.json` captures source
  SHA, preflight, excerpt, and isolated staging settings.
- A single-clip plan is persisted create-once at
  `data/smoke/approved-smoke-scope-pending-cloud.json`. Its explicit
  `paid_execution_enabled=false` is intentional. It is **not** an executable
  Ledger claim or OAuth authority.

## Budget cap (USD cents)

Global/user limit **1000¢ = USD 10**. Three separate preliminary, untrusted
per-stage upper reservations (not price quotes): Gemini 3.8 audio 200¢;
Chirp 3 200¢; Gemini 3.5 Transcribe 200¢; unallocated contingency 400¢,
**not** automatically spendable. Successful real API usage must be reconciled
to actual provider pricing and usage metadata; **Google billing can be delayed
and an application cap is not a hard provider-side budget**. Do not send
any paid request until trusted prices, endpoint compatibility and quotas
are verified. The user specifically approved one **4-minute** test, never
unbounded retries, 141-minute ASR, or all available jobs.

### Durable one-clip trial state (2026-10-13 development)

`scripts/golden_v2_smoke_trial.py --root /path/to/independent/data --init
--owner-reference chat-approved-20261013-four-minute-usd10` creates an
**isolated** SQLite trial ledger once; `--status` displays stages. The CLI
cannot make a provider call, access credentials, or mark stages approved.
It rechecks both the full Drive source SHA and exact derived 4-minute sample
SHA, source preflight, source version and the immutable USD 10 plan. Each
future **authorized** stage's SQLite `REQUEST_STARTED` state must commit
*before* its single external call. The stage cannot be retried after an
ambiguous result, restart or timeout. Over-cap charge blocks the next stage.
`SingleClipSmokeLedger.execute_one` currently needs an injected provider
adapter and **independent trusted owner + capability/pricing verifiers**.
The development tests use fake providers; no real provider is wired.
This narrow acceptance ledger is **not yet integrated** with the general V2
paid worker and does not make cloud access safe by itself.

## Read-only cloud capability probe

Only an **operator-controlled trusted workload runtime** with existing Google
Cloud application-default credentials can run:

```bash
python scripts/golden_v2_cloud_probe.py \
  --project YOUR_PROJECT_ID \
  --bucket YOUR_PRIVATE_V2_STAGING_BUCKET \
  --check-live
```

This makes **GET-only** Vertex publisher model metadata calls for Gemini 3.8
Flash and Gemini 3.5 Transcribe, a regional Speech V2 recognizer list GET at
`us-speech.googleapis.com`, and optional private GCS object-list and
object IAM permission checks. Existing trusted runtime authenticated checks
of the intended bucket reported `storage.objects.create/get/list/delete=true`,
while `storage.buckets.get=false`: bucket-metadata 403 does not, on its own,
block using objects. The model-metadata GETs returned HTTP 404 and are
**inconclusive about paid inference** (not proof that the models are disabled).
The tool returns only HTTP status,
credential-neutral project hash, and no token, project name or provider text.
**It does not check actual inference, paid GCS writes or real billing.**
Without `--check-live` it never accesses ADC/network. No one should copy
production service-account JSON, OAuth refresh tokens or environment secrets
to chat, Git, WebCodex logs or a new unaudited container.

## Safe next execution order

### 2026-10-13 — Google SDK compatibility and executable adapters

The production application image pins `google-genai>=1,<2` and actually
contains `google-genai==1.75.0`. Its `AudioTranscriptionConfig` only has
`language_codes`, and its `GenerateContentConfig` has NO
`audio_transcription_config`. **Do not attempt the third recognizer there**.
The isolated trial image is built with `Dockerfile.golden-v2-smoke` using
Google GenAI `2.29.0`. Verified with actual installed SDK schemas:
`AudioTranscriptionConfig(mode="VERBATIM", word_timestamp=False)` succeeds.
The paid SDK adapter checks the schema BEFORE the API call and fails closed
when run under the production 1.x SDK. **Never upgrade V1 production to fix a
V2-only transcription feature.**

New modules `app/providers/golden_v2_smoke_providers.py`,
`app/pipeline/golden_v2_smoke_gcs.py`, and
`app/pipeline/golden_v2_smoke_dispatch.py` implement:

1. Derived-clip-only URI namespace
   `gs://PRIVATE_BUCKET/golden-v2-smoke/CLIP_SHA/clip.flac`; rejects
   unrelated Drive files and arbitrary GCS paths.
2. `stage_clip_once()`: parent/original/excerpt local provenance check,
   trusted owner and bucket verifiers, a create-once fsynced upload-intent
   BEFORE GCS mutation, and `if_generation_match=0` upload with no retries.
   On upload uncertainty the intent blocks automatic replay.
3. `verify_staged_clip()`: read actual GCS object bytes at its exact
   generation and verify SHA and size, not merely an untrusted GCS URL.
4. Gemini 3.8 / Gemini 3.5 real `generate_content` adapters and Chirp 3
   `BatchRecognize` using `us` with inline response and word timestamps.
   All requests use `SingleClipSmokeLedger.execute_one()`, which commits
   `REQUEST_STARTED` before the paid API call. No automatic retry.
5. Reconcile **reported token usage** to full-price conservative USD cents:
   Gemini 3.8 Global standard rate (through 2026-12-31)
   **$0.75 per million input / $3.75 per million output**;
   Gemini 3.5 Transcribe **$2 per million audio input /
   $12 per million text output**; Chirp 3 Standard
   **$0.016/audio minute**. Each stage rounds UP to whole cents.
   These are application estimates from known public rates, NOT an
   invoice or hard service-side billing cap. Additional storage costs may
   apply, and Google usage/billing can be delayed. Check actual prices and
   region/account discounts before authorization.

Official references:

- https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing
- https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-5-transcribe
- https://cloud.google.com/speech-to-text/pricing
- https://docs.cloud.google.com/speech-to-text/v2/docs/chirp-model

The modules are now backed by **actual SDK request-shape tests with mocked
network/provider clients**. This is transport integration, not proof of
Cloud IAM/model inference entitlement. There is NO `--execute` CLI that
silently supplies owner/pricing verifiers, and no accepted run can copy V1
credentials to the isolated container. Real GCS staging and model POST remain
blocked until a separately authorized trusted V2 workload identity can be
provisioned and its current-region capabilities verified.

The **trusted V2 operator** should use the dedicated SDK image and an
independent workload identity, construct the Agent Platform client via
`genai.Client(enterprise=True, project=PROJECT_ID, location="global")`
(subject to actual model-access verification) and the Speech V2 client via
`SpeechClient(client_options=ClientOptions(api_endpoint="us-speech.googleapis.com"))`.
The operational sequence is:
`stage_clip_once` → `verify_staged_clip` →
`execute_trusted_smoke_stage(model="gemini-3.8-flash")` →
`execute_trusted_smoke_stage(model="chirp_3")` →
`execute_trusted_smoke_stage(model="gemini-3.5-transcribe")`.
Each function expects actual trusted owner, bucket, billing and model
capability verifiers; **do not replace these with lambdas that always return
True** outside the isolated fake-provider unit tests. After any ambiguous
step, stop and reconcile; never reset or delete the SQLite claim/receipt to
get another attempt.

1. Prepare a **dedicated restricted V2 test identity** and private staging
   bucket in a trusted operator runtime (or use a separately authorized
   workload identity, never copy V1 secrets). Verify the three Google Cloud
   APIs are enabled and the chosen locations support required features.
2. Perform the GET-only cloud probe, then a separate **pricing/region/capability
   check** against the actual billed account. A 200 metadata status is NOT
   proof that paid inference works.
3. Implement/verify an independent **derived-clip source identity** bound to
   the parent Drive SHA, exact [600,840] interval, deterministic local clip SHA,
   owner authorization reference and restricted test bucket. It must fail
   closed if either original or clip provenance changes.
4. Using this exact source/clip in a distinct approved paid stage, allocate
   cost reservations atomically, first persist an fsynced O_EXCL window request
   barrier, then issue **one** Gemini 3.8 audio inference. Save raw response,
   exact model ID, usage and invoice basis. If ambiguous, STOP; never blindly
   retry the same source/model/window. No full-length job may start.
5. Only after verified complete primary result and reconciled cost, run one
   independent bounded Chirp 3 BatchRecognize on the SAME short clip; preserve
   operation ID and Chirp word timing, then optional Gemini 3.5 Transcribe.
6. Produce local side-by-side comparison with TXT/SRT and a human audio review
   checklist. **No Drive publication or V1 switch** until separate acceptance.

## Current blockers (not silently waived by payment consent)

- No trusted operator-accessible V2 cloud identity or isolated bucket has yet
  been proven by a live probe. An attempted credential mount from this chat
  execution environment was **blocked by platform security checks**; it was
  not bypassed and no secrets were copied.
- The standard V2 preflight requires a real Google Drive observation tied to
  the media bytes. The derived excerpt has **only parent-source provenance**
  so cannot be charged as the full original source or enrolled by forging an
  independent Drive source ID.
- No live Gemini 3.8, Chirp 3, Gemini 3.5 request, GCS write or paid cost
  settlement has occurred. Formal G0–G8 remain without a full PASS.