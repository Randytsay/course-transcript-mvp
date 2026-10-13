# Decisions

This log records durable Project-local decisions. Cross-project lessons are referenced through `GLOBAL_GUARDRAILS.md`.

## 2026-09-27 — Chirp 3 owns subtitle timing

**Decision:** Chirp 3 word-level timestamps are the authoritative timing source. ChatGPT / Gemini may correct text and semantics only.

**Why:** Model-generated or proportionally interpolated timestamps can drift and are not acceptable evidence.

**Consequence:** Final cue boundaries must derive deterministically from provider word boundaries. Raw provider timing evidence is retained.

## 2026-09-27 — Completeness must precede semantic correction

**Decision:** Acoustic/timeline completeness must pass before ChatGPT handoff. Reference transcripts may assist spelling/context but may not fill an acoustically unverified gap.

**Consequence:** FAIL → bounded targeted repair; unresolved / ambiguous → review. Never “repair” a missing audio span by copying reference text.

## 2026-09-29 — Reference-driven completeness is bounded and fail-closed

**Decision:** When a unique trusted sibling transcript exists, compare it to merged Chirp evidence and repair only high-confidence candidate spans with Standard Batch.

**Consequence:** First rerun must materially improve objective metrics before a second independent rerun. Inconsistency, incomplete evidence, or budget overflow requires review.

## 2026-10-04 — `completed` does not mean Golden

**Decision:** Pipeline completion alone is insufficient for Golden promotion.

**Golden requires:** coverage, lineage, terminology/material evidence as applicable, semantic evidence, final QA, publication freshness and canonical ingestion disposition.

## 2026-10-04 — Bulk first pass and repair use different provider strategies

**Decision:** canonical bulk first-pass ASR uses Dynamic Batch; retry/repair uses Standard Batch on the bounded failed/missing interval only.

**Consequence:** successful chunks are immutable reusable evidence. A retry must not invalidate or re-send them.

## 2026-10-04 — Coverage / VAD / QA are local-first

**Decision:** Use provider-free local analysis to decide whether a suspicious gap actually contains speech before spending on repair.

**Consequence:** silence and density anomalies alone are not transcription failures. Audible gaps, audible uncovered tails, broken timing, or incomplete provider evidence are the blockers.

## 2026-10-05 — Terminal provider operations are evidence, not retry targets

**Decision:** once a provider operation is terminal and its bounded output-propagation grace is exhausted, archive it. A replacement repair gets a new attempt/patch identity and isolated output prefix.

**Consequence:** do not poll forever and do not route a changed repair window back to a dead operation.

## 2026-10-05 — Repair and retry must preserve cost accounting

**Decision:** terminal reservations are settled against recorded usage. Retry/repair must revalidate the remaining approved budget.

**Consequence:** cancellation/failure does not imply zero cost; stale reservations must be reconciled rather than silently leaked.

## 2026-10-05 — Human reference transcripts are preserved

**Decision:** do not overwrite the original human `*逐字稿.txt` as if it were the corrected output.

**Consequence:** publish corrected transcript/report as audited sidecars and retain source/reference digests. Where the Golden publication convention uses `<source>_逐字稿.txt`, safe replacement must preserve prior versions through the old-version suffix policy.

## 2026-10-05 — Downstream publication failure must not repeat upstream AI work

**Decision:** Drive delivery retries reuse existing validated local artifacts.

**Consequence:** a Drive/network failure may retry the idempotent publication transaction, but must not rerun Chirp or semantic correction.

## 2026-10-07 — Canonical-first corpus construction

**Decision:** confirmed duplicate/segment/alternate/derived media do not become separate knowledge objects. Only strict-Golden canonical entries may be ingested into the second brain.

**Status:** accepted operating policy; some implementation remains on the unmerged media-inventory/lineage branch and dirty worktree, so code support must be verified before claiming production completion.

## 2026-10-11 — Golden V2 uses Gemini 3.8 primary transcript before independent Chirp

**Original decision:** Gemini 3.8 Flash is the full-audio primary transcript model before independent Chirp 3. The earlier wording was overly broad about text-only correction and is clarified below. V2 must obtain a complete Gemini 3.8 full-audio transcript first; Chirp 3 then independently transcribes original audio and retains authoritative word timing. Gemini 3.5 Transcribe is an optional third independent recognizer. ChatGPT/WebCodex resolves disagreements against original audio, assembles SRT and corrected TXT, then runs Golden QA.

**Status:** approved design and acceptance baseline, **not implemented/deployed evidence**. Keep V1 3.7 raw/model history intact until proven migration. See `docs/GOLDEN_V2_ARCHITECTURE_PLAN.md` and `docs/GOLDEN_V2_ACCEPTANCE_AND_ROADMAP.md`. Initial measurement uses quality-first three-way comparison; first live testing stays behind explicit per-source cost approval and a proposed USD 10 aggregate test cap. Final production switch needs owner approval.

**Reason:** user-validated Gemini audio semantics for Taiwanese + scripture combined with Chirp word-timing and independent acoustic completeness checks.

## 2026-10-12 — Keep Gemini audio recognition and text correction on separate models

**Decision:** Use Vertex AI `gemini-3.8-flash` only for full-audio primary recognition. Use Vertex AI `gemini-3.7-flash` only for text-only correction after ASR. Keep adapters, prompts, schemas, and model identities separate; never send audio to text correction or use Gemini 2.5. Preserve each raw provider response and its actual model ID. This clarification supersedes any broader wording in the 2026-10-11 decision; it does not change the 3.8 full-audio → Chirp 3 recognition order.

## 2026-10-12 — Superseding correction-model migration decision (3.8)

**Decision (latest):** Use Vertex AI `gemini-3.8-flash` for **both** complete original-audio recognition and subsequent **text-only ASR segment correction**, each through a separate adapter/prompt/schema/input contract. Do not use 3.7 for new text-correction calls. Preserve historical 3.7 model IDs/raw data for audit. This supersedes the earlier 2026-10-12 separate-model-ID decision, **not** the separate-role architecture.

**Implementation status:** Development-only on `codex/golden-v2-goal-completion` / Draft PR #144. Text-correction defaults, MiniMax fallback, proxy, costs, UI and model policy tests have been updated; actual Vertex capability/role, usage and production cutover are still unverified. Never rewrite existing in-flight jobs without explicit migration/approval.

## 2026-10-13 — Explicit 3.8-only Gemini configuration

The owner explicitly instructed: 「拿掉所有 3.7 的設定，只用 3.8」.
New Gemini audio-primary and text-correction configuration targets 3.8;
remaining owner intake/provider UI, API status, fake-provider and OpenRouter
defaults are aligned. Direct new Vertex/OpenRouter submissions using a
nonapproved Gemini model fail closed. Historical 3.7 raw, submitted operations
and cost catalogs remain immutable and readable; missing model evidence must
not be relabeled 3.8. No production setting or runtime was switched by this
development change. Chirp timing remains independent and authoritative.

## Deployment invariant — exact SHA only

Production deployment must use an immutable reviewed release SHA. A dirty live or development working tree is never a release source. Production status must be verified by runtime revision evidence, not directory names or conversational claims.
