# Trying sign-in with a real identity provider (Keycloak)

The tests sign in through a stand-in provider. To see the whole flow against a
real one, run Keycloak locally. Everything here is for trying it out on your own
machine: the passwords are made up and only work on `localhost`.

## 1. Start Keycloak

```bash
make keycloak
```

This runs `quay.io/keycloak/keycloak:26.4` (about 250 MB on first pull) in dev
mode on <http://localhost:8180>, with the realm `einvoice` from
[`deploy/keycloak/einvoice-realm.json`](../deploy/keycloak/einvoice-realm.json):

- client `einvoice` (confidential, PKCE S256 required), redirect URI
  `http://localhost:8013/auth/oidc/callback`;
- `anna` / `kc-test-anna-password`: email `anna@example.com`, verified — added in
  einvoice-agent as a reviewer, so she gets in;
- `mallory` / `kc-test-mallory-password`: verified, but nobody added her here, so
  she is refused (`?signin=unknown_user`);
- `otto` / `kc-test-otto-password`: claims the admin's email `admin@example.test`,
  but Keycloak has not verified it, so he is refused (`?signin=unverified_email`).

Keycloak's own console: <http://localhost:8180/admin> (`admin` / `kc-test-admin`).

## 2. Start the API and the review UI

The browser-test server keeps everything in memory and adds the test accounts.
With `OIDC_ISSUER` set it uses the real provider instead of its stand-in:

```bash
OIDC_ISSUER=http://localhost:8180/realms/einvoice OIDC_CLIENT_ID=einvoice OIDC_CLIENT_SECRET=kc-test-client-secret OIDC_REDIRECT_URI=http://localhost:8013/auth/oidc/callback OIDC_NAME=Keycloak uv run python -m tests.ui_server --auth on --port 8013 --ui-origin http://localhost:3013 --sample ui/e2e/.tmp-signin/rechnung.pdf
```

```bash
NEXT_PUBLIC_API_URL=http://localhost:8013 pnpm --dir ui exec next dev -H localhost -p 3013
```

Open <http://localhost:3013> and choose **Sign in with Keycloak**.

## 3. Stop

```bash
make keycloak-down
```

## What was checked this way (2026-11-20)

In Chromium against Keycloak 26.4: the authorization request carries
`response_type=code`, `openid email profile`, PKCE S256 and the registered
redirect URI; `anna` comes back signed in as "Anna Example · Reviewer" and can
sign out; `mallory` lands on `?signin=unknown_user`; `otto` lands on
`?signin=unverified_email`. No change to the sign-in code was needed.
