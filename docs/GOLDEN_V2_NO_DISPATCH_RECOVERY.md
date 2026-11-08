# Golden V2 unsubmitted Gemini request recovery

This procedure is for one exact Golden V2 job whose Gemini 3.8 primary stage
is `OUTCOME_UNKNOWN` even though the provider request was never submitted. It
does not turn a zero ledger value into proof of zero cost. Recovery requires
the raw attempt, the matching first-window ambiguity record, and a complete
Cloud Monitoring request-count window that contains no inference method.

The verifier queries both `aiplatform.googleapis.com` and
`generativelanguage.googleapis.com` after the documented 30-minute visibility
delay. It rejects any method outside the narrow non-generation allowlist and
requires a positive Vertex `GetPublisherModel` point to show that the Vertex
metric stream was reporting during the window. If a prior recovery stopped
after preserving evidence or archiving the attempt, the verifier fetches a new
complete window and saves a separate content-addressed revalidation before it
continues.

The command archives the original unknown raw attempt byte-for-byte, records a
content-addressed proof in the ledger, and returns only the Gemini primary
stage to `PENDING`. It makes no model request and does not write to Drive. The
running paid worker can claim the stage immediately afterward, so use this
only when the exact source, model, and maximum task cost have already been
explicitly approved. The ledger allows one standard no-dispatch recovery per
job and stage. One additional recovery requires a non-empty owner
authorization reference and is recorded in both the commit proof and ledger
actor. Each recovery keeps its own content-addressed Monitoring evidence and
raw-attempt archive. The total is capped at two recoveries; any later unknown
outcome remains blocked.

Run from the V2 release repository with the isolated V2 ledger and artifact
mounts. Do not point it at the V1 database or artifact directory:

```sh
python scripts/golden_v2_reconcile_no_dispatch.py \
  --database /app/data/golden-v2/ledger.sqlite \
  --artifact-root /app/data/golden-v2 \
  --job-id JOB_ID \
  --project GOOGLE_CLOUD_PROJECT
```

For the single additional recovery only, include the exact owner authorization
reference supplied for that task:

```sh
python scripts/golden_v2_reconcile_no_dispatch.py \
  --database /app/data/golden-v2/ledger.sqlite \
  --artifact-root /app/data/golden-v2 \
  --job-id JOB_ID \
  --project GOOGLE_CLOUD_PROJECT \
  --owner-authorization-reference 'OWNER_APPROVAL_REFERENCE'
```

Afterward, inspect the exact job status and evidence artifacts. Keep Drive
publication disabled until independent audio review and Golden acceptance are
complete.

## SDK client lifetime defect (2026-10-14)

The isolated google-genai 2.29.0 runtime closes its owned HTTP transport in
`Client.__del__`. The former runtime factory retained only `vertex.models`,
so returning from `paid_services` destroyed the owning client. The next
`count_tokens` call raised `RuntimeError: Cannot send a request, as the client
has been closed.` before HTTP dispatch. `BudgetedModels` now retains the
owning client for the lifetime of the stage services.

`test_golden_v2_sdk_lifecycle.py` uses the real pinned SDK and an SDK-owned
HTTP client with MockTransport. On the original deployment the factory test
reproduces the closed-client error; with the fix it completes countTokens and
generateContent. Both tests ran in a network-disabled container with no live
credentials. The general V1 suite skips this isolated-SDK test; a dedicated CI
job installs 2.29.0 and runs it. This does not prove a historical request was
unbilled and does not authorize reopening an unknown attempt or publishing
subtitles. The bounded recovery ledger permits one standard recovery and at
most one additional recovery with explicit owner authorization.
