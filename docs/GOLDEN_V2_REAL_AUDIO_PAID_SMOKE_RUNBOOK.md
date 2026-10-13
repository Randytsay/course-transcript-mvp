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
Flash and Gemini 3.5 Transcribe, a Speech V2 recognizer list GET in `us`,
and an optional private GCS bucket metadata GET. It returns only HTTP status,
credential-neutral project hash, and no token, project name or provider text.
**It does not check actual inference, paid GCS writes or real billing.**
Without `--check-live` it never accesses ADC/network. No one should copy
production service-account JSON, OAuth refresh tokens or environment secrets
to chat, Git, WebCodex logs or a new unaudited container.

## Safe next execution order

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