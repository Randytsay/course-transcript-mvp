# Do Not Repeat

These are known failure patterns for Course Transcript. Treat this file as a pre-flight checklist before changing transcription, retry, Drive publication, learning-platform background work, or deployment.

## 1. Do not rerun the whole course to repair one gap

Preserve successful chunks and repair only verified missing/broken bounded intervals.

## 2. Do not poll a terminal provider operation forever

After terminal state plus bounded output-propagation grace, archive the attempt. A new repair gets a new identity and output prefix.

## 3. Do not treat silence or density anomaly as proof of missing speech

Use local audio/VAD/evidence. Repair only when audible speech or broken provider/timing evidence proves a real gap.

## 4. Do not use a reference transcript to manufacture missing audio

Reference text helps spelling/context and ordered comparison. It cannot establish timing or authorize speech that acoustic evidence did not establish.

## 5. Do not throw away normalized audio before Completeness/repair is settled

Retaining normalized audio avoids repeated Drive download/transcoding during bounded repair.

## 6. Do not process masters, segments, containers and alternates as independent knowledge by default

Resolve lineage first. Only strict-Golden canonical objects should enter the second brain.

## 7. Do not call a pipeline `completed` and assume it is Golden

Golden needs acoustic coverage, lineage, semantic evidence, QA, publication freshness and canonical ingestion disposition.

## 8. Do not retry Drive publication by recomputing subtitles

Delivery retries materialized local artifacts only.

## 9. Do not overwrite original human reference material without preserving provenance

Keep original references and digests. Corrected transcript/report outputs are separate audited artifacts; any formal-name replacement uses safe backup/versioning.

## 10. Do not leak or mis-settle cost reservations

Failure/cancellation can still have accrued cost. Settle usage and revalidate budget before retry.

## 11. Do not let high-frequency heartbeat/polling loops write unchanged state continuously

Write-based datastore quota can be exhausted even with little user traffic. Dedupe, coalesce, rate-limit and monitor writes.

## 12. Do not deploy from a dirty worktree

Use exact-SHA immutable release artifacts and verify the runtime revision after cutover.

## 13. Do not erase old failed evidence to make current state look clean

Historical BLOCKED/FAILED manifests remain audit evidence. Current truth comes from revalidation and newer evidence, not deletion of the old record.
