# State machine

Non-paid:

`preflight → awaiting_confirmation`

Explicit exact-cost approval:

`awaiting_confirmation → queued`

Approved worker:

`queued → downloading → normalizing → transcribing → segmenting → correcting
→ exporting → quality_check → awaiting_review`

Control/error:

- pausable pipeline states → `paused` → user resume → `queued`
- any failed stage → `failed` → revision-checked retry of recorded stage →
  `queued`

Completed evidence makes stages idempotent. A provider/format failure does not
invalidate earlier raw evidence. `awaiting_review` is the current terminal
state; `completed` is reserved for future human approval plus separately
authorized Drive upload.
