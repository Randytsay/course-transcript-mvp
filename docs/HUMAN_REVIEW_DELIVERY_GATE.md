# Human review and Drive delivery gate

## Invariant

`require_human_review=true` is a persisted business rule. It is independent of
how many review terms Gemini generates.

After local transcription, correction, export, QA, and validation:

- a review-required job must enter `awaiting_review`;
- no pipeline or delivery worker may mutate Drive;
- `review_terms_count=0` does not waive review;
- the job may become `completed` only after an authenticated human explicitly
  publishes the reviewed subtitle snapshot.

A human may confirm that no edits are required. Revision `0` is therefore a
valid reviewed snapshot and can be published explicitly.

## Non-review jobs

Jobs created with `require_human_review=false` retain automatic publication.
They may publish after QA and complete without entering `awaiting_review`.

## Fail-closed delivery worker

The retry worker may select:

- `completed` jobs with a pending or incomplete delivery transaction; or
- legacy `awaiting_review` jobs only when `require_human_review=false`.

It must never select an `awaiting_review` job that requires human review. A
second in-process check enforces this even if the candidate query changes.

## Explicit reviewed publication

The authenticated subtitle publish endpoint:

1. locks the exact editor revision, including revision `0`;
2. persists editor publication intent before Drive mutation;
3. publishes only SRT/TXT selected by the user;
4. marks stale pipeline delivery as superseded;
5. records one idempotent audit event;
6. transitions the job and, when applicable, its batch to `completed`.

Paid providers are never repeated by this publication flow.

## Regression requirements

Tests must prove:

- review-required jobs stop at `awaiting_review` with zero or more review terms;
- the production worker does not call automatic publication for those jobs;
- the delivery worker cannot select or complete a blocked review job;
- non-review jobs can still auto-publish and complete;
- revision-zero explicit publication is accepted and completes the job;
- background delivery cannot release the human-review gate.
