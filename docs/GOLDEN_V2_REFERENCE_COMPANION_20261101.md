# Golden V2: same-folder reference transcript (development only)

## Intended behaviour

On **new** Google Drive link previews, discover only `.txt` and `.srt` files in
exactly the same immediate folder as an audio/video source. Pair by normalized
lesson filename/topic or unique eight-digit course date. An exact-name or
higher-scoring per-format candidate takes priority; duplicate equally ranked
TXT or SRT, a shared candidate across lessons, unavailable metadata, or changed
Drive revisions are **not** silently selected. Direct-file links also scan
siblings using bounded GET-only pagination. Preview identity includes any
selected reference metadata (file ID, version, path, size, MD5).

Quote and batch source intake download matching small UTF-8 references using
bounded **GET-only** Drive media access, verify before/after metadata, MD5,
SHA-256, and create immutable job-local reference bytes plus a source-bound
manifest. Original Drive TXT/SRT, media, and published captions remain intact.
No reference is sent to a paid model.

At the ordinary local comparison stage, compare the pinned reference text to
verified Chirp words and include `human_reference_hint` in the immutable
`engine-comparison.json`. Differences include Chirp word timing/evidence IDs
when supported. These hints are **REVIEW_ONLY** and are never allowed to
replace words, create timestamps, fake human listening, or turn Acoustic QA or
Golden QA to PASS. The existing Gemini/Chirp comparison and gate remain the
sole gate inputs. A tampered reference is a hard error on comparison or QA
rebuild.

## Limitations / deployment gate

- This is a **new-intake** enhancement. Already-registered 2022 jobs do not
  automatically re-ingest sidecar references. They require a separate,
  version-pinned, controlled historical backfill after verifying original
  Drive reference identity and present Production job/ledger state.
- Automatic association requires unambiguous filenames; a generic `逐字稿.txt`
  or competing document copies require an explicit owner choice.
- Only UTF-8 TXT and SRT files up to 2 MiB are supported. Google Docs and
  `.docx` are not converted automatically.
- New code is staged on a PR based on the PR159 development branch. No merge,
  Production deployment, new paid recognizer calls, worker restarts, Ledger
  mutations or Drive publication is authorized by this change.
- Golden QA for 20221217 still needs audited cloud operation/billing evidence
  and actual audio review of unresolved gaps. Text agreement alone is not an
  acoustic proof.
