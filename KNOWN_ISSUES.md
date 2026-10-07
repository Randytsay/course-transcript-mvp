# Known Issues

Last reviewed: 2026-10-07

## Active — branch/main divergence

`feat/media-inventory-dedup` was observed ahead 6 / behind 16 relative to current main, with merge base `96f71cb...`.

**Risk:** copying files or merging blindly can lose newer main fixes or reintroduce old behavior.

**Required:** reconcile in an isolated worktree/branch, then run focused repair/Golden tests plus full regression before merge.

## Active — dirty WebCodex working copy

The active WebCodex project contains uncommitted media inventory, lineage, Golden finalization, semantic repair, publication and cost-reconciliation work.

**Risk:** reset/clean/stash or unrelated edits could destroy active evidence or work in progress.

**Required:** treat the working copy as protected. Use a separate branch/worktree for unrelated changes.

## Active — historical blocker state can be stale

Legacy `BLOCKED` / `needs_review` JSON may reflect an older audit, older evidence, or a pre-lineage view.

**Risk:** an Agent may spend on unnecessary ASR or report a resolved course as still blocked.

**Required:** re-download/normalize only when needed, run provider-free Coverage/VAD final revalidation first, then repair only verified audible residual gaps.

## Active — duplicate / alternate recordings can waste quota

Historical folders can contain master files, cut segments, audio/video versions and alternate recordings.

**Risk:** treating all files as independent sources duplicates paid ASR and creates duplicate knowledge objects.

**Required:** identity/lineage gate before paid processing. Ambiguous relationships remain blocked until local fingerprint/duration evidence resolves them.

## Active — production exact SHA is not encoded in repository docs

Repository main is not proof of the currently running release.

**Required:** use runtime revision labels / `runtime_revision_guard.py --expected-sha <sha>` during deployment or production verification.

## Active — accepted Golden v1.6/v1.7 policy is not fully reconciled into main

The newest workflow/Skill policy exists on `feat/media-inventory-dedup`, while main has newer independent fixes.

**Required:** reconcile rather than cherry-pick blindly. Do not advertise v1.7 behavior as production until merged and deployed.

## Watch — semantic display changes can make a prior publish stale

A previously completed/published job can become stale after semantic display cleanup or corrected text changes.

**Required:** compare current SRT/TXT hashes to published sidecars; republish through safe-publish when they differ.

## Watch — cost reservations can outlive work if terminal paths are mishandled

Cancellation, failure and provider-terminal paths may still have accrued cost.

**Required:** settle recorded usage, preserve active/retry reservations only when justified, and revalidate project/batch budget on retry.
