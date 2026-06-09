# SP-A — Agent Registry: Canonical Identity & Signed Tokens

**Status:** held (pending taOSmd co-design integration)
**PR:** #705 (`feat/agent-registry-sp-a`)
**Owner:** taOS

---

## Overview

The Agent Registry gives every agent a cryptographically-verifiable identity.
A taOS instance mints a canonical ID and issues a signed JWT (EdDSA/Ed25519) at
agent registration time.  The A2A bus (taOSmd) verifies the token independently
— without importing any tinyagentos code — by fetching the public key from a
single unauthenticated endpoint and running a standard Ed25519 signature check.

---

## JWT Claim Set

Every token issued by `POST /api/agents/registry/register` has the following
payload:

| Claim       | Type   | Description                                              |
|-------------|--------|----------------------------------------------------------|
| `sub`       | string | Canonical agent ID (immutable, e.g. `hermes-20260609-120000`) |
| `iss`       | string | Fixed: `"taos-registry"`                                |
| `iat`       | int    | Unix timestamp of issuance                              |
| `user_id`   | string | Owning user at registration time (may be empty string)  |
| `framework` | string | Agent framework at registration time (e.g. `"openclaw"`, `"hermes"`) |

### JWT Header

The header is always exactly:

```json
{"alg":"EdDSA","typ":"JWT"}
```

This matches the JOSE/RFC 8037 compact EdDSA JWT standard.  Any conforming
Ed25519 JWT library can verify the token without knowing anything about taOS.

### Token Format

Standard three-part compact JWT:

```
base64url(header) . base64url(payload) . base64url(signature)
```

Signature covers `base64url(header).base64url(payload)` (UTF-8 bytes), signed
with Ed25519.  No padding characters (`=`) in any part.

---

## Endpoints

### `POST /api/agents/registry/register`

Registers an agent, mints a canonical ID, and issues a signed token.

**Request body (JSON):**

```json
{
  "framework":    "openclaw",
  "display_name": "My Agent",
  "user_id":      "user-42",
  "origin":       "taos-deployed",
  "handle":       "@myagent",
  "role":         "coder",
  "capabilities": ["code-generation"]
}
```

**Response:**

```json
{
  "canonical_id": "my-agent-20260609-120000",
  "token":        "<header>.<payload>.<sig>",
  "record":       { ... full DB record ... }
}
```

The token payload will contain `user_id` and `framework` as-provided in the
request body.

---

### `GET /api/agents/registry/pubkey`

Returns the registry's Ed25519 public key in PEM format.

**Auth:** none — intentionally open so the A2A bus can fetch on its own schedule.

**Response:**

```json
{
  "alg":        "EdDSA",
  "format":     "PEM",
  "public_key": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----\n"
}
```

---

### `GET /api/agents/registry`

List all registry entries (active and revoked).

### `GET /api/agents/registry/{canonical_id}`

Fetch a single entry.  Returns 404 if not found.

### `DELETE /api/agents/registry/{canonical_id}`

Revoke an entry (sets `revoked_at`; does not delete the row).

---

## Bus-Side Verification (taOSmd)

The A2A bus verifies agent tokens without importing tinyagentos code:

1. **Fetch the public key** once (cache until restart or on 401):
   ```
   GET http://<taos-host>/api/agents/registry/pubkey
   → public_key (PEM)
   ```

2. **Split** the Bearer token into `header.payload.sig`.

3. **Verify the header** is `{"alg":"EdDSA","typ":"JWT"}`.

4. **Verify the signature** using the Ed25519 public key over
   `header_b64url.payload_b64url` (standard cryptography lib / JOSE library).

5. **Decode the payload** (base64url → JSON) and extract claims.

### `sub == from` check

On any bus or memory operation that carries an agent identity, the bus must
assert:

```
token.claims["sub"] == message["from"]
```

This prevents one agent from forging messages as another.

### Bearer-Token Presentation

Agents present their registry token on bus and memory operations via the
standard HTTP `Authorization` header:

```
Authorization: Bearer <compact-jwt>
```

### Opt-in Verification

The bus skips signature verification when no public key is configured (i.e.
`pubkey_url` / `pubkey_pem` not set).  This allows standalone / free-tier
taOSmd deployments to function without a running taOS registry.  When a pubkey
IS configured, verification is mandatory and failures return 401.

---

## Signing Key Lifecycle

- The Ed25519 keypair is generated once on first boot.
- The private key is stored at `<data_dir>/agent_registry_signing.pem` (mode
  0600).  It never leaves the taOS host.
- The public key is served unauthenticated via `/api/agents/registry/pubkey`.
- Key rotation is out of scope for v1 (tracked separately).

---

## Canonical ID Format

```
{slug}-{YYYYMMDD}-{HHMMSS}
```

- `slug` is derived from `display_name` (falling back to `framework`) by
  lowercasing and replacing non-alphanumeric runs with `-`.
- Same-slug same-second collisions get a 2-char hex suffix: `…-01`, `…-02`, …
- Once issued, the `canonical_id` is immutable.

---

## Out of Scope (v1)

- Token expiry / refresh
- Key rotation
- Per-agent capability enforcement on the bus
- Revocation propagation to the bus (bus must re-check or re-fetch)

## Revocation (v1 limitation + hardening path)

Because the bus verifies tokens **self-contained** (signature against the
published `pubkey`, no per-request registry round-trip — this keeps taosmd
standalone and fast), a token issued before an agent is removed from the
registry **still verifies**. So v1 has **no real-time revocation**: deleting a
registry record stops new tokens but does not invalidate already-issued ones.

Each token carries a unique `jti` (token id) so a revocation list can target
individual tokens later without changing the token shape.

Hardening options (a **Jay decision**; @taOSmd leans **B**):

- **A — short `exp` + refresh:** tokens carry a short expiry; the bus checks
  `exp`; agents re-register to refresh. Revocation = stop refreshing.
- **B — registry revocation list:** the registry exposes
  `GET /api/agents/registry/revoked` (revoked `jti`/`sub`); the bus polls it
  periodically (not per request) and rejects listed tokens.

v1 ships long-lived tokens with revocation documented as the above limitation.
