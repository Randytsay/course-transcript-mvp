# Golden V2 — Chirp 20221217 local Ledger snapshot evidence diff

**Status: a read-only developer artifact, not permission to run a Production operation.**

Purpose: help ChatGPT verify the discrepancy between the 2026-11-06 current Ledger inventory (**16 local Chirp DONE rows / 78¢ at revision 5**) and Issue #160's earlier **34 externally reported SUCCEEDED operations / 136¢ at revision 4**. These numbers are different *types* of evidence and have **not been reconciled**. They must not be assumed to represent removed operations or duplicate charges.

## Execute only under a separately approved AGY-LOCAL read-only task

With **existing authorized read privileges** to both DB paths, and after verifying that both DBs use SQLite DELETE journal mode without WAL/SHM sidecars:

```bash
python3 scripts/golden_v2_chirp_ledger_snapshot_diff.py \
  --current /opt/course-transcript-v2-chat/data/golden-v2/ledger.sqlite \
  --previous /opt/course-transcript-v2-chat/backups/pr159-20261029T071713Z/ledger.sqlite \
  --job-id v2p_cce7a9147a495027bd16f5619ad17b8d
```

The prior SQLite backup path is an example of the existing PR159 backup, **not evidence that it contains 34 rows**. Repeat a separate comparison with PR158 only if authorized and the file exists. If historical DBs do not contain the target job, report **BLOCKED** for that source and investigate immutable local provider evidence separately.

### What the tool guarantees

- Python standard library only; opens both pre-existing DBs using SQLite URI **`mode=ro`**, `PRAGMA query_only=ON`, and an explicit **`BEGIN` transaction for all queries within each database**. Never modifies a Job, stage, approval, claim or cost.
- Fails closed if either database is missing, a symlink, empty, has WAL/SHM files, or does not use DELETE journal mode.
- Only reads one exact canonical Job ID; prints aggregates and differences in **operation names** without emitting raw operation names, source SHA, request IDs, cloud resource IDs, credentials or transcripts. The two databases have **separate read transactions** and do not share a global atomic snapshot.
- Records local stage total independently from the sum of known operation costs. Missing name or cost, duplicates, source drift and stage mismatches remain explicit blockers.
- Returns `LOCAL_EVIDENCE_DIFFERS` or `LOCAL_EVIDENCE_MATCHES_NOT_CLOUD_VERIFIED`. Even local agreement **never** proves external Speech-to-Text LRO completion, GCP Cloud Billing charges, or Golden PASS.
- Does **not** open GCP, perform any HTTP request, copy a database, extract the 12 GB archive, or clean the VPS disk. Avoid exposing raw CLI output from unrelated commands to this public repository.

### Acceptance criteria for AGY-LOCAL-002 v2

1. Supply sanitized evidence from the **current DB** versus PR159/PR158 historical SQLite files if accessible. List each snapshot's revision/status, counted named/unnamed operations, known local cost, cross-snapshot overlap counts and source identity match.
2. If the previous backups do not corroborate the historical 34, state this and the exact missing provenance **without manufacturing a reconciliation**.
3. Distinguish the previously **declared** `jobs.tar.gz` checksum from an **actually recomputed** archive SHA. No extraction or new file is required for this task.
4. Preserve GCP cloud operations and billing as `UNKNOWN`; no paid POST or Drive write.
5. Post `RESULT AGY-LOCAL-002 v2` at [Issue #173](https://github.com/Randytsay/course-transcript-mvp/issues/173). ChatGPT independently reviews and replies with `REVIEW` before proceeding.

Ref: [Issue #160](https://github.com/Randytsay/course-transcript-mvp/issues/160), [Issue #173](https://github.com/Randytsay/course-transcript-mvp/issues/173). Production deployment and full cold restore remain NO-GO.
