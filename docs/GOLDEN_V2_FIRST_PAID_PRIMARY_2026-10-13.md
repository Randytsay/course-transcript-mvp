# Golden V2: immutable 4-minute GitHub primary smoke

Owner authorized **only** original 600–840 seconds, local FLAC SHA
\`17db78ae930d28be3de930467ac256166de54fa62bb2e9cd361cdb58587d5621\`,
combined US$10 total, not the full 141-minute Drive source.

- Dedicated first-stage model \`gemini-3.8-flash\`, reservation USD 2.
- Private GCS audio uploaded once at
  \`gs://xenon-chain-506409-c3-golden-v2-smoke/golden-v2-smoke/<SHA>/clip.flac\`.
  Source download and SHA verified, actual storage generation 1791882898950171.
- Dedicated workflow \`.github/workflows/golden-v2-paid-primary-smoke.yml\`
  starts with \`GOLDEN_V2_PAID_ENABLED="false"\`. Before first paid run,
  perform a no-charge preflight, verify WIF specific \`workflow_ref\`, CI and
  preview output, then explicitly review and change to \`true\` in that
  exact workflow. A push touching only Python code does NOT trigger paid action.
- The script checks fixed GitHub repo numerical ID, owner ID, branch and
  workflow ref, exact 4min bytes and SHA, and USD cents cap.
- Atomic GCS \`if_generation_match=0\` claim MUST succeed before single provider
  call; no automatic retries (Google GenAI SDK max attempts=1). Claim
  survives runtime crash, trigger repeat, ambiguous request or missing receipt.
  Repeated run stops at claim creation and must **never** re-issue paid request.
- Receipt is saved once in private GCS with raw provider response, model text,
  usage counters, conservative cost calculation. No transcript/token/credentials
  in GitHub logs.
- Gemini estimated application cents are NOT a Google hard billing cap;
  verify provider usage, invoice and corrections before next paid stage.
- The VPS SQLite \`clip-trial.sqlite\` remains pending until validated Cloud
  claim and receipt are manually reconciled. Never launch VPS paid caller in
  parallel. The special GitHub claim is cloud-side, separate from VPS SQLite.
- This is first-stage only; no Chirp, third model, full-length job, Drive
  mutation, V1 cutover or PR merge is authorized by this implementation.
