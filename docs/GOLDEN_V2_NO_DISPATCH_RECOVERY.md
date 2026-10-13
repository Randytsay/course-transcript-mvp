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
explicitly approved. The ledger allows this no-dispatch retry once per job and
stage; any later unknown outcome remains blocked for manual reconciliation.

Run from the V2 release repository with the isolated V2 ledger and artifact
mounts. Do not point it at the V1 database or artifact directory:

```sh
python scripts/golden_v2_reconcile_no_dispatch.py \
  --database /app/data/golden-v2/ledger.sqlite \
  --artifact-root /app/data/golden-v2 \
  --job-id JOB_ID \
  --project GOOGLE_CLOUD_PROJECT
```

Afterward, inspect the exact job status and evidence artifacts. Keep Drive
publication disabled until independent audio review and Golden acceptance are
complete.
