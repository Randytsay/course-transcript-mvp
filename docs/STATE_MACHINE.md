# State machine

Non-paid:

`preflight → queued` when the estimate is known and inside the configured
auto-authorization and project-budget guardrails.

Exceptional / guarded path:

`preflight → awaiting_confirmation → queued`

The second transition requires explicit exact-cost approval. Unknown estimates,
over-threshold batches, disabled auto-authorization, and project-budget
conflicts never enter paid processing automatically.

Approved worker:

`queued → downloading → normalizing → transcribing → segmenting → correcting
→ exporting → quality_check → awaiting_review|completed`

Control/error:

- pausable pipeline states → `paused` → user resume → `queued`
- any failed stage → `failed` → revision-checked retry of recorded stage →
  `queued`

Completed evidence makes stages idempotent. A provider/format failure does not
invalidate earlier raw evidence. Jobs that require human review stop at
`awaiting_review`; jobs without that gate can reach `completed` through the
hardened completion/publication path.
