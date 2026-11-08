# Golden V2: progressive candidate delivery is NOT Golden acceptance

Owner decision: 2026-11-08. A few unresolved intervals must not block delivery of an OWNER-REVIEWABLE SRT/TXT. Manual listening is targeted to truly uncertain audio; it is not a blanket prerequisite to producing the draft. This does not turn bad acoustic evidence into a Golden PASS.

## Two independent tracks

- Candidate track: structurally valid source SRT + corrected TXT exact-text consistency -> reviewable candidate ready, with uncertainty flags and clear time windows. Status DRAFT_READY_WITH_REVIEW_FLAGS (or DRAFT_READY_FOR_OWNER_REVIEW). This is a SUCCESSFUL, nonblocking output event, not a technical failure. Deliver the new version under a new name; never overwrite the existing source. Source text stays intact; human-readable review-marker text is explicitly editorial, not transcribed speech.
- Golden QA track: source-bound word timing, speech-to-text fidelity, optional targeted human listening and acoustic review. If evidence is insufficient, keep Golden QA PENDING/REVIEW_REQUIRED; do not block reviewable candidate export. Publication or live release needs its own independent scope and consent.

## DEV-only implementation

Standalone offline module: scripts/golden_v2_progressive_reviewable_candidate.py.
Tests: tests/test_golden_v2_progressive_reviewable_candidate.py.

The program:
- Never contacts cloud/provider/network, downloads models, modifies Ledger/Production/Drive, or changes release status.
- Parses SRT and validates transcript text against TXT before creating any candidate.
- Safely merges adjacent very short cues while preserving source characters.
- Allows HIGH-UNCERTAINTY only provisional time allocation for collapsed cue text, with exact source timing in a machine-readable review manifest. All allocated times are NOT audio-forced-aligned.
- Marks remaining untranscribed SRT holes clearly as editorial REVIEW placeholders.
- Returns exit code 0 for a successfully produced draft even with OPEN REVIEW flags. Fail closed for invalid input, overlapping timing, text mismatch or attempted overwrite.
- ALWAYS reports golden_qa_pass=false and drive_publication_authorized=false.

## Actual Nov 8 source-folder delivery

20261031 《金剛果論》31–32 original folder:
https://drive.google.com/drive/folders/14YfBNRbSsXaUcdEhy5GaSmOJLDHDjL55

Versioned reviewable candidate SRT:
https://drive.google.com/file/d/1eNhbFSHAFZMaEBnKiYz1_amY9pD_EUjL/view

Eight issue windows, original existing text, explanations, owner-reply template:
https://drive.google.com/file/d/1waMjZG4bYX6WpgKkJJ5lU3ELgwv2sfYp/view

Nov 4 original TXT and SRT were not overwritten. The uploaded candidate used a temporary local transformation before the DEV module was integrated; the script here codifies this class of workflow, not a claim that the production service generated or deployed it. Local uploaded-candidate bytes were read back and SHA matched. Heuristic timings have not been acoustically verified. Owner authorized only this scope of nonoverwriting Drive write.

## Quality decision

Where original provider word timestamps are absent, report existing text before/after and precise review times; ask only for missing/incorrect phrases and timing if known. Do not claim silence from absence of words or infer exact spoken phrasing from waveform energy. When owner replies, apply corrections to a newly versioned local draft, preserve the original, repeat structural/text checks and present remaining uncertainties. Formal Golden PASS is independent and must be evidence-backed.
