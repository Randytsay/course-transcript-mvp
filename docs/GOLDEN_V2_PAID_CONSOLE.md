# Golden V2 Owner Paid Approval Console

**Status: code-only candidate, not deployed.** Merge, production release, credential activation and individual provider approvals are separate authorized actions.

## Scope and controls

The dedicated owner console opens at /golden-v2/paid on an **optional** Golden V2 Docker Compose service bound only to localhost:8817.

It displays trusted quote-backed MP3 jobs for one exact configured Drive path prefix, split into eligible (at least 3,600 seconds), excluded short audio, and already approved or review states. It supports explicit manual selection of at most five jobs per interaction. It does NOT enumerate Drive on its own.

The Golden V2 **backend**, not only the UI, verifies:
- Trusted media duration >= 3600.0 seconds.
- Immutable quote SHA, audio source SHA, matching Drive file ID, name, size, version and MD5.
- Current job state and revision, exact quote-to-job binding and configured Drive batch scope.
- Conservative lifetime exposure of all approved jobs in this scope (including earlier short-audio pilots), with **US$100 (10,000 cents) absolute cumulative batch ceiling**.
- Existing per-job limit and separate worker aggregate cap remain active.

It calls the existing officially authenticated authorize-paid endpoint, which creates an immutable HMAC-signed owner-paid-grant and performs ledger compare-and-swap. No API or UI action autonomously submits paid provider requests; the paid worker will act only after actual authorized grants.

The Next.js server performs Cloudflare Access JWT RSA signature verification, exact issuer, audience, permitted email and expiry checks, in addition to the existing admin hostname and CSRF Origin checks. Secrets stay server-side. Forged Cloudflare Access headers are insufficient.

## Deploy / enable only through explicit Owner Gate-A

1. Review and merge passing CI; schedule the separately authenticated exact-SHA V2 release, with Stage-0 database backup, ledger drain, integrity and rollback validation. V1 stays unchanged.
2. In the protected V2 API environment file, enable:
   - COURSE_TRANSCRIPT_V2_PAID_CONSOLE_ENABLED=true
   - COURSE_TRANSCRIPT_V2_PAID_CONSOLE_SCOPE_PREFIX=gdrive:<full verified parent path of owner batch>/
   - Retain existing Owner status/write tokens, grant secret and paid API switches. The full Drive path must match verified v2_quotes source_path values. Do not put real tokens in Git or chat.
3. Provision a separate protected console env file (0600, outside Git):
   - COURSE_TRANSCRIPT_V2_ADMIN_UI_ENABLED=true
   - COURSE_TRANSCRIPT_V2_PAID_CONSOLE_ENABLED=true
   - ADMIN_PUBLIC_HOSTNAMES=<exact console domain>
   - COURSE_TRANSCRIPT_V2_STATUS_TOKEN=<existing scoped status token>
   - COURSE_TRANSCRIPT_V2_WRITE_TOKEN=<existing scoped write token>
   - COURSE_TRANSCRIPT_V2_PAID_CONSOLE_ACCESS_TEAM_DOMAIN=<team>.cloudflareaccess.com
   - COURSE_TRANSCRIPT_V2_PAID_CONSOLE_ACCESS_AUD=<Access app AUD value>
   - COURSE_TRANSCRIPT_V2_PAID_CONSOLE_ALLOWED_EMAILS=<owner email allowlist>
4. Place localhost:8817 behind Cloudflare Tunnel + Cloudflare Access policy restricted to the owner. Do not expose V2 API port 8816 or console port 8817 publicly.
5. With separately authorized release credentials, build exact SHA and start optional Compose profile owner-console, using V2_CHAT_OWNER_CONSOLE_ENV_FILE=<path to protected console env>. The normal V2 stack does not auto-start this profile.
6. Test read-only status, denied unauthenticated requests, exact eligibility and cost. Manually approve just one vetted 1h+ job and verify signed receipt + QUEUED state. Confirm Gemini/Chirp independently; AI-limited delivery and old-file archival require their own grants and verified Drive readback.

## Important restrictions

- Creating the interface **does not change ChatGPT's tool permissions**. A ChatGPT action blocked by platform safety checks must not be retried via an unapproved alternative.
- Old $6/folder auto-budget authorization is intentionally disabled for in-scope jobs while the new console is enabled.
- A local cost reservation is not an independently reconciled provider invoice; billing uncertainties must be checked before new approvals.
- No one may treat an AI-revised SRT as Golden QA PASS without real acoustic/semantic acceptance evidence.
- If Access team/audience/owner email, Owner API tokens, exact batch prefix, or feature enablement are absent, the UI fails closed.
