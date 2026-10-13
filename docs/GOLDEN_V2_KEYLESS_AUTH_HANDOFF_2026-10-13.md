# Golden V2 — Google Cloud keyless handoff (2026-10-13)

## Resources confirmed by live Google Cloud API

- Google Cloud project: `xenon-chain-506409-c3`, project number `608343797618`.
- Dedicated new service account: `golden-v2-smoke@xenon-chain-506409-c3.iam.gserviceaccount.com`.
- Project-level **predefined** roles granted to that service account: `roles/aiplatform.user` and `roles/speech.client`. These roles authorize model use but are **not** a dollar-denominated cloud-side hard cap.
- Dedicated private Cloud Storage bucket: `gs://xenon-chain-506409-c3-golden-v2-smoke`, location `US`, standard storage, uniform bucket-level IAM, `publicAccessPrevention=enforced`. The account has bucket-scoped `roles/storage.objectCreator` and `roles/storage.objectViewer` only, **not** deletion or project-wide GCS administrator roles. Existing V1 bucket `xenon-chain-506409-c3-course-transcript` is unchanged.
- No downloadable user-managed service-account JSON key was created or moved; no production V1 credential has been copied to a new container.

## GitHub Actions WIF — actual verified connection

- Pool `projects/608343797618/locations/global/workloadIdentityPools/golden-v2-gh`; provider `github-restricted` (issuer `https://token.actions.githubusercontent.com`).
- Provider mapping: `google.subject=assertion.sub`, `attribute.repository_id=assertion.repository_id`, `attribute.repository_owner_id=assertion.repository_owner_id`, `attribute.ref=assertion.ref`, `attribute.workflow_ref=assertion.workflow_ref`.
- Narrow trust **all requirements**:
  - Immutable GitHub Repository ID `1317518844` (not just the repo name).
  - Immutable GitHub owner ID `122517962`.
  - Exactly `refs/heads/codex/golden-v2-goal-completion`.
  - Exactly `Randytsay/course-transcript-mvp/.github/workflows/golden-v2-keyless-auth-smoke.yml@refs/heads/codex/golden-v2-goal-completion`.
- `roles/iam.workloadIdentityUser` is granted **on the dedicated SA** to principalSet `.../workloadIdentityPools/golden-v2-gh/attribute.repository_id/1317518844`; provider condition enforces the extra restrictions above.
- The workflow `.github/workflows/golden-v2-keyless-auth-smoke.yml` only obtains a **10-minute short-lived access token** using `google-github-actions/auth@v2`, GETs its private bucket object-list and Speech V2 recognizer list, checks HTTP 200, and prints result markers. It never uploads the audio, invokes a paid model, or writes Google Cloud state.
- First successful keyless validation: [run #38363139909](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38363139909). Logs: `KEYLESS_GOOGLE_AUTH=PASS`, `GCS_OBJECT_LIST_HTTP=200`, `SPEECH_V2_READ_HTTP=200`, `MODEL_INFERENCE_CALLS=0`, `GCS_UPLOADS=0`.
- Provider trust was **tightened after** that initial run by adding `workflow_ref` to the condition and mapping. A second CI run must verify the new restriction; initial success alone is not evidence the updated condition works.

## Remaining gates / scope boundary

1. This WIF path proves Google Cloud identity for **GitHub-hosted Actions only**. Oracle VPS's isolated Golden V2 canary still runs `--network none` without any dedicated credentials or OCI-issued OIDC provider. Do not claim native Oracle VPS Workload Identity Federation is configured.
2. Before the actual paid model trial, build a separately approved, bounded one-shot execution path. The current WIF provider deliberately permits **only the read-only test workflow**, not arbitrary paid workflows. Any change to allowed workflow requires an explicit security review and test.
3. Preserve existing real 4-minute excerpt (600–840s), SHA `17db78ae930d28be3de930467ac256166de54fa62bb2e9cd361cdb58587d5621`, immutable source lineage and create-once trial SQLite; Gemini 3.8 → Chirp 3 → Gemini 3.5 under the already approved aggregate **US$10** cap. No full 141-minute audio billing, no automatic retries of an ambiguous call. Reserve exact stage in SQLite before any external model POST.
4. Google provider usage-based cost estimates are not service-side hard caps. Review pricing, quota and verified model API availability before sending the first paid request.
5. No real model inference or GCS media write occurred during the IAM/WIF configuration. V1 remains unchanged, Draft PR #144 must not be merged as a substitute for G0–G8 acceptance.

**Security note:** Avoid attempting to reuse/copy V1 service-account keys and avoid loosening this provider to the entire repository or all branches.
