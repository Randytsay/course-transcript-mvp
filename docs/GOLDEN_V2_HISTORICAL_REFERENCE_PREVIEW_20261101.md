# Golden V2: historical reference evidence preview (development / read-only)

This is a deliberately separate continuation after PR #161. It is **not a
Production historical backfill**, does not migrate or change existing 2022
Golden V2 jobs, and does not authorize a new cloud operation. It can audit
owner-verified historical Drive TXT/SRT snapshots against a verified **existing
acoustic review gap manifest** in an isolated local workspace.

## Initial Drive inventory (2026-11-01, read-only)

The 2022/12/17 清淨心, 12/24 齋醮, and 12/31 正法傳承 source folders each contain:
- one original lecture MP3;
- one TXT reference transcript;
- an original SRT;
- an additional manually edited SRT.

**Important:** The original and manually corrected SRT are competing views,
not automatic accuracy rankings. The updated link-preview logic imports the
uniquely matched TXT as a review hint, while both plausible SRT versions are
marked `requires_manual_selection` and *neither* is silently selected.

## What the isolated historical preview does

Inputs, all supplied by a trusted operator after separate checks:
1. Exact source-bound and gap-manifest SHA and canonical `v2p_*` job ID.
2. Original acoustic review `review_manifest.json` from already existing
   private 20221217 clip staging, copied read-only after verified provenance.
3. A local `plan.json` listing explicit roles (BASE_SRT, REVIEWED_SRT,
   REFERENCE_TXT), local byte paths, exact SHA-256, Drive file ID, folder ID
   and Drive version for *each selected file*. This is not proof by itself of
   a live Drive revision or authoritative speech content.

It verifies all these hashes and identities and writes a single new private
report (mode 0600; no overwrite), assessing which held gap intervals overlap
existing SRT cues, where SRT variants disagree, and which fixed-size TXT
anchors can be found. No audio proof, new ASR request, Drive GET/POST or
original Ledger write occurs inside the preview. Any absence of SRT captions
is a **candidate** requiring acoustic review, not an automatic new subtitle.

Example after trusted preparation (do not substitute example IDs for real
source-specific values):

```sh
python -m scripts.golden_v2_historical_reference_preview \
  --plan /private/reference-plan.json \
  --gap-manifest /private/qa-snapshot/review_manifest.json \
  --gap-manifest-sha <sha256-of-the-exact-gap-manifest> \
  --job-id <actual-canonical-v2p-id> \
  --source-sha <actual-immutable-normalized-flac-sha> \
  --output /private/reports/new-historical-reference-preview.json
```

Do not pass a mutable Production location as output. Operator must verify
source FLAC hash and live Drive source versions *outside this offline command*
before interpreting its result. Operator must not copy proprietary lecture
TXT/SRT/MP3 to GitHub or put tokens in evidence. The preview itself does **not**
validate GCP bills, GCP LRO completion, or a human listening decision.

## Next gates for 20221217 and the other two lessons

1. Independently verify the exact Production source SHA, frozen job revision,
   original folder ID and Drive current version/size/MD5 before any live
   backfill. Current WebCodex uid999 cannot read root-owned Production Ledger;
   this preview must use isolated copies and must not change ACLs.
2. Review 26 gap items with SRT-version differences/empty captions highlighted.
   Only actual audio listening or authorized independent acoustic evidence may
   resolve `REVIEW` or `SPEECH_CONFIRMED` concerns. Keep all 26 as pending until
   a reviewer or accepted audio-verification process records decisions.
3. The 20221224/31 jobs remain in provider PENDING. Their reference discovery
   is useful for subsequent QA but does not let them bypass provider/gating or
   original per-folder signed cost controls.
4. Prepare a *separately scoped* source-bound controlled backfill PR after
   independent review; never write historical reference into active Production
   or change immutable provider/artifact identity as a side effect of preview.

Neither this plan nor its local tests constitutes permission to deploy PR159,
PR161, reactivate Paid/Delivery workers, bill a model, or publish subtitles.
