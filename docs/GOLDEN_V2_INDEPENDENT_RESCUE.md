# Independent Chirp recovery after a Gemini refusal

The owner authorized recovery of `251025_.MP3`, job
`v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be`, normalized source SHA
`2069df0d67444a30e5243b27236c8f387292e9776e4e5eda65aca80c7f71a9fb`,
within the existing USD 2.10 cap, without a third model or Drive publication.

`scripts/golden_v2_independent_rescue.py` is a trusted operator entry point.
It verifies the existing signed grant, provider/pricing, fresh Drive version,
immutable primary/raw checksums, source bytes and usage-derived primary cost.
Only a known empty `REQUIRES_REVIEW` Gemini response with a recorded SAFETY
refusal qualifies. Unknown requests and existing Chirp operations cannot be
submitted again. The SQLite revision, budget, aggregate exposure and claim
barrier are checked atomically.

Gemini stays REVIEW_REQUIRED with its original evidence and recorded charge.
Only Chirp is claimed. Comparison, adjudication and Golden acceptance remain
blocked until the primary review is independently resolved. Chirp uses the
existing private, immutable chunk staging, dynamic-batch submit-once barrier,
saved operation recovery, billing evidence and acoustic completeness gate.
This path does not alter safety settings or retry a refused Gemini request.

Fresh Drive verification observed version 9 rather than approved version 7,
with the same file ID, size and MD5. Version drift is accepted only after
downloading the complete current media with stable before/after metadata and
matching its SHA-256 to the immutable approved original. This creates separate
revision evidence and retains the original preflight, quote and grant. Changed
bytes, changed identity or an unstable revision block paid dispatch.

Recovery-only invocation omits `--submit`; it never issues a new Speech POST.
Polling ends after 24 hours or a review/terminal result. Restart recovery from
saved operations; do not restart a submission command. No Drive writes occur.

Local validation: five focused recovery tests and 25 Chirp/dispatch/recovery
tests passed. Full unittest discovery passed 1083 tests, with two skipped
(the new raw-refusal test was added after that run and passed separately).
These tests do not establish live transcription completion.

Live attempt: the identical version-9 audio was downloaded and SHA verified.
Ten Chirp windows were staged. The first Speech POST returned an operation,
but the old gateway rejected its canonical numeric-project identity before
recording its name. One window remains SUBMITTING and nine remain PLANNED;
no repeat POST was issued. The project number was independently verified by
Cloud Resource Manager GET. The gateway now supports an explicitly configured
numeric alias and persists the raw operation receipt before identity checks.
This fix does not recover the previously lost name by itself.

Operation listing returned no matching operations. The runtime identity lacks
Cloud Logging read access (403), local gcloud requires reauthentication and
the VPS CLI has no active account. No IAM change was made. Recovery of this
accepted request remains unresolved; its charge is not yet accounted, and
all further paid submissions remain held pending independent reconciliation.

Follow-up: Google Speech V2 ListOperations requires the literal filter
`is_v2(name)`; an empty filter returns the wrong operation collection.
See https://docs.cloud.google.com/speech-to-text/docs/operations.
Bounded pagination of that collection uniquely matched the existing input,
output prefix and Chirp model. The original operation is DONE and its raw
protobuf/identity proof is saved separately. The operator reconciliation path
can bind only that exact request to SUBMITTED, without submitting another
request, changing source/approval or asserting a zero cost. Restart then uses
the existing planned-only claim rotation and saved-operation recovery.

## Verified continuation

- Runtime code SHA: `b63d4e15149b38ba5a6d887f203488c9e2265ed4`.
- Independent container: `golden-v2-independent-rescue-b63d4e1`, running.
- First operation was recovered without another POST: 2551 words, provider
  billed-duration derived cost 5 cents, DONE in SQLite. Gemini remains at
  24 cents and REVIEW_REQUIRED; combined known usage is 29 cents, not an invoice.
- Nine remaining PLANNED windows were submitted once and are now SUBMITTED
  with durable operation names and raw operation receipts. No SUBMITTING
  ambiguity remains. Approved job ceiling stays 210 cents.
- Source revision 9 was byte-identical to approved revision 7 by full SHA-256;
  separate proof retained, original approval/quote/preflight unchanged.
- Recovery polls existing operations only, bounded to 24 hours. After all
  windows are DONE it runs acoustic QA and renders checksum-bound raw review
  candidates (`candidate.srt`, `candidate.txt`, manifest). Candidate export
  requires complete raw evidence and strict deterministic Chirp rendering;
  it does not approve semantics, mark Golden PASS or write Drive.
- Local full unittest discovery: 1090 tests, two skipped. ARM64 image-contained
  focused suite: 27 passed. GitHub exact-head checks should be queried anew.

Resume diagnosis by reading this container's bounded logs and the shared
ledger. Do not start `--submit` again. `--resume-planned` applies only when
known operations coexist with never-submitted PLANNED windows; ordinary
recovery omits both flags. The chat API's old generic paid-worker heartbeat
does not describe this separate exact-source recovery process.
