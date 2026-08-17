# `app.review`

Persistence-only collaboration domain for the Buddhist YouTube subtitle review
experience.

M0 intentionally has no FastAPI routes and no OAuth provider calls. Importing
this package only initializes/uses its own `review_*` SQLite tables through
`ReviewStore`; it does not weaken the existing Cloudflare Access operator gate.

See `docs/SUBTITLE_REVIEW_M0.md` for the product and data contract.
