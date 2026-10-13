# Vertex OpenAI-compatible proxy deployment

This directory contains only non-secret deployment configuration. On the VPS,
the service account, LiteLLM master key, and Cloudflare tunnel credentials are
kept under `secrets/`, `.env`, and `cloudflared/credentials.json` respectively;
all must be owner-readable only and must never be committed.

The public OpenAI-compatible endpoint is `https://vertex-api.ccwu.cc/v1`.
Available aliases are `gcp-flash` (the default Gemini 3.8 Flash model),
`gcp-pro`, and the backward-compatible `gcp-flash-38`; all active aliases
resolve to Vertex AI Gemini 3.8 Flash in this development configuration. This is not proof of a production proxy restart or model migration.

The production deployment uses the `systemd/` units so the proxy and Tunnel
restart independently of Docker. The Compose definition remains available as a
portable alternative.
