# Golden V2 — AUTO_ACCEPTED_WITH_REVIEW (nonhuman closure)

## Owner-approved quality policy (2026-11-01)

**Deliver the strongest evidence-backed SRT/TXT automatically, disclose uncertain
source-speech issues in a separate optional human-review report, and do not let
lack of human listening indefinitely block closure.**

This is an **independent, named quality tier**. The existing `Golden PASS`
contract, primary model stages, original audio and editor files are not
silently downgraded, repaired, impersonated, or marked complete.

### Required machine checks

- Exact original job/source SHA; verify real normalized FLAC SHA-256, not just a
  previously recorded boolean; pin source identity and original Drive revision.
- Strict parse of all timed SRT cues, chronological order, non-overlap, no blank
  events and final timestamp within the source duration. No inference of words
  from silence or from the article-like TXT reference.
- Byte-exact full ordered SRT → TXT check (same words, no rephrasing), SHA-256
  evidence for both outputs.
- No new paid recognition needed to generate the limited-tier output. Existing
  Chirp/Gemini state and unsolved acoustic evidence stay accurately reported.
- Variants: compare prior original SRT, human-edited SRT, optional article TXT;
  report disagreements and 3+ second gaps as **candidates**, not transcript
  errors or silence/speech proof.

When the hard checks pass, emit `AUTO_ACCEPTED_WITH_REVIEW` and a separate
machine-readable `golden-v2-auto-acceptance-v1` report plus readable optional
human review recommendations. Even if audio QA has `REVIEW` or the strict
Chirp stage is still PROCESSING, human review is NOT a gate for this tier.

If hard checks fail, emit `AUTO_BLOCKED_TECHNICAL` and **do not** close.
Golden PASS continues to require its own sound/time/semantic evidence and
should never be set merely because auto-accepted files exist.

### Durable independent closure

`app/pipeline/golden_v2_auto_closeout.py` holds a source/revision-bound
`v2_auto_acceptance_deliveries` independent table. A freshly SHA-checked
`PREPARED` proposal may become `AUTO_DELIVERED` **only after** exact file
contents are uploaded as new noncolliding Drive names to their original
parent, original Drive audio version/MD5 is verified under a shared lock,
owner publication authorization is separately authenticated, and actual
remote bytes read back with matching SHA. The entire original `v2_jobs` and
`v2_stages` Golden provider/QA ledger remains unchanged.

**Important:** This branch provides the preparation/finalization contract and
an offline report CLI, not yet the completed, deployed owner-API/Drive bridge.
No active Production ledger AUTO_DELIVERED claim should be made until the
bridge is implemented, code reviewed, exact-SHA deployed and verified.
Do not call `finalize_auto_acceptance` with untrusted callbacks: both source
revision and remote SHA verification must execute in the authenticated,
shared-locked Drive delivery path.

### Offline tool usage

```
python -m scripts.golden_v2_auto_acceptance \
  --job-id <canonical-job-id> \
  --source-sha <live-normalized-flac-sha256> \
  --audio /private/verified-job/normalized.flac \
  --srt /private/verified-job/selected.srt \
  --txt /private/verified-job/selected.txt \
  --title '20221217 清淨心' \
  --chirp-stage RUNNING \
  --reference-txt /private/reference.txt \
  --original-srt /private/original.srt \
  --acoustic-review-json /private/20221217-review_manifest.json \
  --output-json /private/new-auto-qa.json \
  --output-report /private/new-human-review.md
```

The CLI reads the *exact* source FLAC and validates its hash, checks subtitles,
and writes two create-only local reports. It does NOT submit provider requests,
update Production, upload to Drive or grant itself permission to publish.

### Existing 2022 three-lesson interim implementation

An independent local machine pass on the existing human-edited SRT candidates
verified the already-registered FLAC hashes by a read-only live check. New
files preserve the prior human edits. On 20221217 one 120ms cue overlap was
boundedly trimmed (previous cue end adjusted to next start) without changing
spoken-text content. On 20221231 sixteen empty cue entries were already
omitted from the prepared candidate and no replacement words were guessed.

The same original course folders contain non-overwriting candidate SRT/TXT,
new auto-acceptance ZIP packages with an independent JSON report and human
recommendations. Original Golden QA is still PENDING; 20221217 has acoustic
REVIEW gaps, 20221224 and 20221231 Chirp remain PENDING. This is independent
automatic **artifact acceptance**, not successful completion of those paid
provider tasks or Golden QA.
