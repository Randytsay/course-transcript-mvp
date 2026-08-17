# Subtitle Review M1 — Google / LINE authentication

M1 adds a reviewer-facing authentication boundary without changing the existing
Cloudflare Access operator identity used by the transcription admin workspace.

## Runtime routes

- `GET /api/v1/review/auth/providers`
- `POST /api/v1/review/auth/google/start`
- `GET /api/v1/review/auth/google/callback`
- `POST /api/v1/review/auth/line/start`
- `GET /api/v1/review/auth/line/callback`
- `GET /api/v1/review/auth/me`
- `POST /api/v1/review/auth/logout`
- reviewer entry page: `/review`

The start endpoint returns the provider authorization URL. The browser then
navigates to Google or LINE and returns to the server-side callback.

## Environment

Configure these only in protected deployment environment/secrets, never in the
repository:

```text
REVIEW_PUBLIC_ORIGIN=https://<reviewer-facing-origin>
REVIEW_GOOGLE_CLIENT_ID=<google web client id>
REVIEW_GOOGLE_CLIENT_SECRET=<google web client secret>
REVIEW_LINE_CHANNEL_ID=<line login channel id>
REVIEW_LINE_CHANNEL_SECRET=<line login channel secret>
REVIEW_SESSION_TTL_DAYS=30
```

`REVIEW_PUBLIC_ORIGIN` must exactly match the browser origin. HTTPS is mandatory
outside localhost.

Register these provider callback URLs exactly:

```text
https://<reviewer-facing-origin>/api/v1/review/auth/google/callback
https://<reviewer-facing-origin>/api/v1/review/auth/line/callback
```

## Security contract

- OAuth Authorization Code flow is server-side.
- Google and LINE access/ID tokens are never persisted in browser storage.
- Both providers use a one-time `state`, OpenID `nonce`, and PKCE S256.
- OAuth state is stored only as SHA-256 in SQLite and expires after 10 minutes.
- Session cookies are opaque random values, `HttpOnly`, `SameSite=Lax`, and
  `Secure` on HTTPS origins.
- SQLite stores only the SHA-256 session-token digest.
- `/me` returns a CSRF token derived from the current opaque session token.
- reviewer mutation routes require both exact same-origin `Origin` and
  `X-Review-CSRF` validation.
- logout revokes the server-side session immediately.
- provider access tokens are discarded after verified identity extraction.

## Identity behavior

First successful provider login creates an active `reviewer` automatically.

A second provider must be added using `action=link` from an already authenticated
session. The OAuth flow is bound to that logical user and the callback requires
the same active reviewer session. This prevents a LINE identity from silently
being merged into another existing reviewer.

The reviewer page displays the linked providers. A user may therefore sign in on
another device with either provider after both identities have been explicitly
linked to the same logical account.

## Cloudflare Access boundary

The existing admin workspace may continue using Cloudflare Access. Reviewer auth
is intentionally app-managed and must not inherit the operator-only Access gate.
At the edge, use either:

1. a dedicated reviewer hostname routed to the same frontend/API services; or
2. an Access policy that permits the reviewer `/review` and
   `/api/v1/review/*` paths while keeping admin/transcription paths protected.

Do not disable the existing application-level operator checks for admin mutation
routes.

## Provider setup notes

Google:

- create a Web application OAuth client;
- add the exact callback URI above as an authorized redirect URI;
- the application requests `openid email profile` and validates the ID token
  audience plus nonce on the server.

LINE:

- create/use a LINE Login channel;
- add the exact callback URI;
- enable OpenID Connect scopes required by the channel;
- the application uses LINE Login v2.1 Authorization Code + PKCE S256 and calls
  LINE's Verify ID token endpoint before accepting the identity.

## M1 acceptance criteria

- [x] Google and LINE authorization URLs are produced server-side.
- [x] OAuth state is one-time and short-lived.
- [x] PKCE verifier remains server-side.
- [x] verified identity resolves/creates one logical reviewer.
- [x] explicit provider linking keeps the same reviewer ID.
- [x] session is revocable and opaque to JavaScript.
- [x] reviewer mutations use same-origin + CSRF checks.
- [x] `/review` supports login, account status, provider linking, and logout.
- [ ] real Google credential smoke test on reviewer-facing HTTPS origin.
- [ ] real LINE Login credential smoke test on reviewer-facing HTTPS origin.

The final two items intentionally require deployment credentials and provider
console redirect registration; they cannot be proven by repository CI alone.
