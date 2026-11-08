## Preview candidate built but NOT executed

The distinct \`gemini-3.5-transcribe-preview\` candidate uses the same approved 240-second WAV SHA and existing Gemini 3.8/Chirp evidence, but requires its **own** future user authorization before any paid provider request. The GitHub workflow has \`GOLDEN_V2_PREVIEW35_PAID_ENABLED="false"\` and blank \`GOLDEN_V2_PREVIEW35_OWNER_APPROVAL\`. Preview dispatch refuses paid execution absent a specific newly granted approval flag and a new create-only GCS claim. It does not replay either of the two older GA attempts, and it has not been tested by actual inference.

Preview model Garden metadata and list GET are HTTP 200. This does **not** prove preview inference entitlement. Preview output must be compared with user-reviewed audio and Golden candidate rather than automatically promoted.
