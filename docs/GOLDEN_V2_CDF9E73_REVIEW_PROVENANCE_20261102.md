# Golden V2 deployed guarded-source reconstruction for independent review

**Do not treat this new Git commit as the original cdf9e73 commit.** It is a fresh Git review artifact rebuilt by copying 15 specific code/test/documentation paths from the already deployed source tree at /opt/course-transcript-v2-chat/releases/cdf9e73dccd3a2b2c28d43c158a04b6f1d814f76/. Each copied file's SHA-256 was compared byte-for-byte with that read-only source. Exact hashes are in GOLDEN_V2_CDF9E73_REVIEW_MANIFEST_20261102.json.

The reviewed parent is PR #164, SHA a18fd13186e73add4ef1c03aa8fdb5beb5cd25a5. The running Docker OCI revision label was observed as a18fd13 while the active release directory was named cdf9e73. This mismatch requires an operator's exact-image/rootless-byte attestation. Reconstructing a new Git commit cannot restore historical ancestry or constitute post-hoc production authorization.

**Security intent:** The imported change deliberately DISABLES real Drive auto publication by raising a V2Conflict until concurrency-safe reconciliation is proven. Keep that guard intact. A passing CI or review is not authorization for Drive, paid inference, unsupported Golden PASS or privileged deployment. Drive GET 404 is ambiguous; metadata POST then media PATCH is not one atomic create.

**Independent review scope:** Compare all copied code and QA tests, schema fences, intent persistence, source/parent binding, synthetic fake gateway crash recovery. Confirm exact live source hashes against the manifest; review any historical owner grants and cloud cost separately. Preserve original backups without chmod/overwrite.

**Follow-up sequence:** (1) review this reconstruction and provenance, (2) review separate inert exact-ID Multipart spike PR #165, (3) integrate it into reviewed guarded design with no automatic 404-retry, (4) test process crashes, external editors, race and source drift, (5) independently approve source-bound release with V2-specific rollback, (6) separately approve one real-Drive Canary, and (7) claim AUTO_DELIVERED only with four remote IDs, matching bytes and independent SQLite receipt.

No Production changes or new cloud charges were made while preparing this review tree.
