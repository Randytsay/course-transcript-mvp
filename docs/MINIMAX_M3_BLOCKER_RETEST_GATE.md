# MiniMax M3 blocker retest gate — RETIRED

This runbook is retired.

The 2026-08-24 `seg-0338` retest proved that the earlier CN-only
`max_completion_tokens=2048` workaround did **not** resolve the HTTP 422
`invalid_request`. Current MiniMax M3 OpenAI-compatible documentation allows
`MiniMax-M3` on `/v1/chat/completions` and allows the existing 4096 completion
limit, so the old 2048 premise must not be reused.

Use `docs/MINIMAX_M3_422_DIAGNOSTIC_GATE.md` for the next bounded live step.
That gate is designed to expose only safe structural diagnostics from a 422
response (for example validation location/type, parameter name, provider code,
or a categorical content-rejection marker) without persisting provider bodies,
transcript text, prompts, headers, or credentials.

Historical evidence retained for traceability:

- job: `09-20260510-20260808-154719-24752c`
- blocker segment: `seg-0338`
- current window at the failed retest: `corr-v2:rt:seg-0337..seg-0360`
- source SHA-256: `816f4fb9c113692bf2a0e26ad9422da11a2a416e586db604117e1902cdd19100`
- 2048 retest result: one provider call, `invalid_request`, raw-Chirp fallback,
  zero native route coverage

Do not run another paid call from this retired document.
