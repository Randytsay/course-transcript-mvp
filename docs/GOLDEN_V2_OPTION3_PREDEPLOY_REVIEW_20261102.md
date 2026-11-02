# Golden V2 Drive Option 3 — independent review and offline spike (2026-11-02)

**Status: PROTOTYPE ONLY / NO PRODUCTION AUTHORIZATION / NO REAL DRIVE CALLS.**

## Verified deployment observations

- The `cdf9e73dccd3a2b2c28d43c158a04b6f1d814f76` release directory exists on VPS and embeds the deliberate `V2Conflict('auto publication to Drive is disabled pending concurrency-safe reconciliation')` guard. Keep it enabled.
- Deployed recovery document explicitly says real Google Drive publication remains disabled. This is intentional, not a missing environment flag.
- GitHub PR #164 is still draft with remote `head_sha=a18fd13186e73add4ef1c03aa8fdb5beb5cd25a5`, not `cdf9e73`. The latter cannot currently be fetched as a GitHub commit ref; the deployed tree needs a reviewable provenance record before release acceptance.
- VPS is running golden-v2 intake, worker, delivery, but no independent API service in compose. Docker OCI revision label observed `a18fd131...` despite release directory being `cdf9e73...`; require exact runtime-byte-to-review-commit attestation instead of assuming label proves provenance.
- Root-only protected backup and claimed restore drill must be verified by authorized operator. The current owner operator status has no V2 deploy/rollback allowlist action.

## This draft PR's bounded deliverable

`app/pipeline/golden_v2_multipart_pilot.py` is a **pure** request builder and remote verifier. It does not call HTTP, change the publication fence, post data, consume cloud models, or unlock the deployed guard. It builds `POST /upload/drive/v3/files?uploadType=multipart` with exact pre-generated ID, parent, name, job, manifest SHA, size and content SHA. Google Drive supports multipart binary uploads for small files, as well as pre-generated ID semantics; this alone does not prove the full recovery protocol.

`verify_remote_exact` requires exact ID, parent, pending name, appProperties, MIME, size, md5, version and downloaded SHA-256. For a lost response, 409, or 5xx, a verified remote observation is necessary; 404 / inaccessible / unknown content remains `HOLD_FOR_RECONCILIATION` with NO re-upload and NO new ID. A verified remote observation **never** authorizes actual publication absent valid owner grant, committed fence, source version validation, and Drive publication lock.

The fake/inert 8 standard-library tests verify positive behavior, 409/404 handling, content/owner tampering and unsupported files. A full integration implementation remains to be built only after the deployed `cdf9e73` fence is made reviewable; **do not cherry-pick over the fence or remove it**.

## Mandatory production tests before enabling a real gateway

| Failure window | Required behavior |
|---|---|
| ID reservation / intent not committed | no network POST; unchanged job/ledger |
| Fence committed, request never known to start | no assumption about historical POST; preserve fence and reconcile |
| Multipart request accepted, response lost | read exact ID, metadata and all media bytes; only then verified |
| 409 Conflict | remote exact-ID identity and SHA required; 409 alone is not success |
| 404 / credentials revoked / deleted object | fail closed; 404 not proof of never-created; do not issue replacement ID |
| Bytes/MD5 mismatch, empty or moved object | fail closed with immutable forensic evidence, no automatic overwrite |
| Source Drive revision / MD5 / parent changes | fail closed before and after staged writes |
| Two cooperating workers | shared source lock + durable SQLite fence; no double allocation |
| External Drive editor changes final name | no claim of exclusive remote writer; suspend and reconcile |
| Crash after 1–3 of four promotions | preserve IDs / fence; no `AUTO_DELIVERED`; report partial visibility |
| Drive publish complete, SQLite receipt commit fails | replay read-only proof against all four IDs before receipt; no new upload |
| Re-run already complete delivery | read-only remote/ledger proof; zero upload/promote operations |

**Non-achievable by this module:** Atomic visibility of four Drive files and a SQLite receipt; prevention of arbitrary external editors; authoritative 404-vs-deletion history; original audio transcription completeness; source-bound paid ASR repairs. A source-locked exclusive-writer trust model with explicit operator audit is required where provider compare-and-set semantics do not exist.

## Human handoff and decision boundaries

1. Import/publish the actual `cdf9e73` release source plus provenance (commit ancestry, blob digest, redacted independent security review) to a separately reviewable PR; preserve existing #164 draft for history, do not force-push.
2. Hermes or a reviewable development agent must rebase this **inert spike** onto that actual reviewed fence and implement true Drive integration there, not on obsolete a18 directly.
3. Run all fault-injection, restart-after-crash, SQLite trigger, source drift, grant, and race tests with synthetic gateway. Require separate source-scoped owner approval and one single folder canary only after privileged V2-specific deploy/rollback verified.
4. Do not substitute legacy Path B, do not assert `confirm_listened=true` without a human, do not pay Gemini/Chirp without existing signed source-budget authorization.
5. Keep `AUTO_ACCEPTED_WITH_REVIEW` separate from `GOLDEN PASS`; successful machine artifacts cannot be called `AUTO_DELIVERED` until all four Drive IDs read back SHA and the independent ledger receipt exists.

## References

- https://developers.google.com/workspace/drive/api/guides/manage-uploads
- https://developers.google.com/workspace/drive/api/guides/handle-errors
- https://developers.google.com/workspace/drive/api/reference/rest/v3/files/create
