# MiniMax M3 Phase A readiness

This change hardens the code path before VPS/staging validation. It does **not** enable MiniMax M3 in production.

Implemented:
- active `/jobs/new` Drive API UI uses one preflight action and requires both `m3_enabled` and `minimax_configured` before M3 can be selected;
- MiniMax structured correction responses receive a bounded retry before one-way Gemini fallback;
- M3-first uses MiniMax terminology extraction instead of an empty glossary, with safe empty-glossary fallback and audit evidence;
- actual correction routing is normalized from `correction-routing.json` and written to pipeline manifests instead of hard-coded Gemini-only metadata;
- a deterministic, provider-independent `terminology-consistency.json` report is generated without modifying text, segments, or timestamps;
- the explicit `gemini-3.7-flash` safe-baseline marker remains in the production worker so model-drift regression guards continue to work;
- the fake-provider test path also emits terminology consistency evidence so isolated pipeline tests keep the same artifact contract;
- focused regression coverage is added for the above behaviors.

Production defaults remain:
- `CORRECTION_DEFAULT_POLICY=GEMINI_FIRST`
- `MINIMAX_M3_ENABLED=false`
- `MINIMAX_M3_QUOTA_CHECK_ENABLED=false`

Remaining Phase B gates require VPS/staging access: live quota regression, controlled quota-exhaustion pipeline E2E, real long-course Gemini 3.7 vs M3 A/B, production canary, and guarded cutover/rollback.