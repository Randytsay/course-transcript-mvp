# Golden Course Transcript Workflow

Canonical operator/AI contract: see
`skills/golden-course-transcript/SKILL.md`.

The contract intentionally composes existing production primitives rather than
creating a second transcription pipeline:

`Drive discovery -> deterministic topic classification/profile routing -> existing job reconciliation -> Chirp 3 evidence ->
Completeness Gate/targeted repair -> CHATGPT_HANDOFF -> deterministic cleanup /
Golden Rules / rendering -> hardened validation -> safe Drive publication`.

For `market_america_training`, the fixed Chirp subtitle layer also triggers one
read-only ShopClaw terminology snapshot before model correction/handoff. The
snapshot is spelling evidence only and intentionally excludes prices, raw
catalog copy and unsupported claim expansion.

Version: **1.0**

This document is the stable handoff pointer for future ChatGPT/WebCodex sessions.
Changes to timing truth, completeness fail-closed behavior, Drive publication
safety, or evidence retention require a version bump and regression tests.

