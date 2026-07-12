# Account model: free username, paid chosen subdomains (design spec)

Status: DECIDED product model, DRAFT implementation plan. Encodes Jay's
decisions on the free/paid account split; the product decisions themselves are
settled and not up for re-litigation here. Author: taOS-dev.

## Why this exists

The taOS online account (taos.my) currently conflates three things: the account
itself (email + password), the taOSgo subscription (off-LAN access via the
relay + Headscale mesh), and a "reserved username" that today is presented as a
taOSgo perk and implicitly maps to `{username}.taos.my`. The Settings Account
panel (`desktop/src/apps/SettingsApp/AccountPanel.tsx`) literally renders
`{handle}.taos.my` under the username card and says the handle is "Included
with taOSgo". The mesh-join design (`docs/design/taosgo-mesh-join-foundation.md`)
and `tinyagentos/taosnet/mesh_credentials.py` both describe publishing to
`<label>.<handle>.taos.my`, again deriving the public hostname from the
username.

That coupling is wrong for where the product is going:

1. **The social side must be free.** A taOS username is the identity people use
   on the future hub.taos.my network and in community features (sharing apps,
   agents, profiles). Charging for identity kills the network before it starts.
   Creating a username costs nothing and grants full access to the social side.
2. **The paid side is infrastructure, not identity.** The taOSgo subscription
   buys remote/off-LAN access (the taOSgo relay + Headscale mesh) and the right
   to claim public subdomains under taos.my.
3. **Subdomains are chosen, not derived.** A paid user claims ANY available
   subdomain, for example `mybiz.taos.my` for a business page. They are not
   forced to `{username}.taos.my`, and one account can hold several subdomains
   (a personal page plus one or more business pages), each bound to a site the
   account owns.
4. **Private by default carries over.** A claimed subdomain exposes nothing
   until the user explicitly publishes to it. The relay's fail-closed
   `/api/relay/authorize-site` posture stays exactly as designed.

In one sentence: **username = free identity namespace; subdomain = paid,
user-chosen, claimable hostname namespace; the two are decoupled.**

## Non-goals

- **No pricing amounts.** The free/paid boundary is public and documented here;
  prices, tiers, and margins are private and never appear in this repo.
- **No hub.taos.my social architecture.** What the free username unlocks
  (profiles, feeds, community features) is a separate design. This doc only
  establishes that the username is the identity key those features will use.
- **No custom TLD domains** (bring-your-own-domain, taos-website #168). Same
  deferral as the mesh-join design.
- **No dynamic/backend site hosting.** A subdomain binds to a published static
  site in v1, matching Web Studio's current scope.
- **No change to local device accounts.** The controller's own user system
  (`/auth/status`, the OnboardingScreen local username) is untouched; this doc
  is about the taos.my cloud account only.

## Current state (verified in code)

- `tinyagentos/routes/account_proxy.py` proxies `me/login/register/logout` to
  taos.my `/api/auth/*` with cookie pass-through, plus the cluster-join flow.
  New account actions are added by extending its `_ACTIONS` table.
- `desktop/src/lib/account-client.ts` defines `Account { user_id, email,
  taosgo: TaosgoEntitlement, handle?: string | null }`. The `handle` field is
  documented as "not yet returned by /me".
- `desktop/src/apps/SettingsApp/AccountPanel.tsx` renders a `TaosgoCard`
  (subscription status, trial/renewal dates) and a `ReserveHandleCard` that
  shows `{handle}.taos.my` and markets the username as a taOSgo perk. Board
  task #110 tracks this section.
- `desktop/src/components/OnboardingScreen.tsx` handles the LOCAL device
  account only; cloud account creation during onboarding is board task #141.
- Web Studio publish (#167) is specced in
  `docs/design/taosgo-mesh-join-foundation.md`: the controller publishes with a
  host-bound `sites_token` (scope `sites:publish`) and taos.my derives the
  handle server-side from the token. That derivation is the main thing this
  model changes: the publish call must now name WHICH claimed subdomain.
- `tinyagentos/taosnet/mesh_credentials.py` persists
  `{account_id, host_id, tailnet_name, controller_token, sites_token,
  joined_at}` and its docstring references `<label>.<handle>.taos.my`.

## Data model

All records live in the taos.my account service (repo `jaylfc/taos-website`).
The controller never stores entitlement or namespace state; it only proxies and
renders what `/api/auth/me` and the subdomain endpoints return.

### Username namespace (free)

- One username per account, claimed at (or after) registration, at no cost.
- Charset `[a-z0-9]` plus internal hyphens, length 3 to 30, stored lowercase,
  unique across all accounts. Same validation shared with the reserved list.
- The username is the identity key for the social side (future hub.taos.my)
  and the display handle (`@name`) across taOS surfaces.
- Claiming a username does NOT reserve `{username}.taos.my`. The DNS namespace
  is separate (see below). Migration handles accounts that were promised the
  old behavior.
- Renames: out of scope for v1 (open question on policy; the conservative
  default is no self-serve rename once community features reference it).

### Subdomain namespace (paid, taOSgo)

Its own claimable global namespace, fully decoupled from usernames:

- `subdomain_claims`: `{id, account_id, name, status, claimed_at, lapsed_at,
  released_at, binding}` with `name` unique among non-released claims.
- `status` is one of `active`, `grace` (subscription lapsed, still held),
  `released` (row kept for history and cooldown enforcement).
- `binding` is what the subdomain serves: `null` (claimed, nothing published,
  the private-by-default state) or `{type: "site", host_id, site_ref}` for a
  published Web Studio site. The type field leaves room for future targets
  (for example remote-desktop routing) without a schema change.
- Multiple claims per account, capped (anti-squatting, see Claim flow).
- Same charset/length rules as usernames, same reserved list.

### Reserved names (shared blocklist)

One checked-in list in taos-website, enforced on BOTH namespaces (a name that
must never be a subdomain should not be a username either, and vice versa):

- Infrastructure: `www`, `api`, `hs`, `relay`, `mail`, `mx`, `smtp`, `imap`,
  `ns1`, `ns2`, `cdn`, `static`, `assets`.
- Product: `taos`, `taosgo`, `taosnet`, `hub`, `agenthub`, `store`, `app`,
  `apps`, `docs`, `blog`, `status`, `help`, `support`.
- Operational/abuse: `admin`, `root`, `system`, `auth`, `account`, `accounts`,
  `login`, `register`, `billing`, `settings`, `security`, `abuse`, `postmaster`,
  `webmaster`, `noreply`, `dev`, `staging`, `test`.
- Plus anything under 3 characters. The list is data, not code, so adding a
  name is a one-line change with a test asserting both namespaces reject it.

## Claim flow

All endpoints are on taos.my; the controller reaches them same-origin through
`account_proxy.py` (new `_ACTIONS` entries), so the client code never sees the
taos.my base URL and the session cookie round-trips as today.

1. **Availability check.** `GET /api/subdomains/check?name=x` returns
   `{available: bool, reason?: "taken" | "reserved" | "invalid" | "cooldown"}`.
   No auth needed for the check itself; it is also used pre-claim in the UI for
   inline feedback. Rate-limited to keep it from becoming an enumeration oracle.
2. **Claim.** `POST /api/subdomains/claim {name}`. Requires a signed-in session
   AND `taosgo.status` in (`trialing`, `active`). Server re-validates
   availability, the reserved list, and the per-account cap inside one
   transaction (the availability check is advisory; the claim is the authority).
   Returns the claim record. The subdomain now resolves to nothing: DNS exists
   under the wildcard but the relay refuses to route it until a binding exists.
3. **Bind/publish.** Publishing a Web Studio site (the `sites_token` call from
   the mesh-join design) now includes the target subdomain:
   `POST /api/sites/publish {subdomain, label?, port, host_id}` with
   `Authorization: Bearer <sites_token>`. taos.my verifies the token's account
   owns an `active` claim on that subdomain before creating the route. Unpublish
   clears the binding; the claim survives.
4. **Release.** `POST /api/subdomains/release {name}` by the owner. The row
   flips to `released`, the binding is dropped, the relay stops routing it
   immediately. A released name enters a cooldown window before anyone
   (including the releasing account) can re-claim it, so drop/re-grab griefing
   and accidental releases are both survivable.
5. **Subscription lapse.** When `taosgo.status` leaves (`trialing`, `active`),
   all of the account's claims flip to `grace`: the relay stops serving their
   bindings (fail closed, same posture as unpublished) but the names stay held
   for the grace window. Re-subscribing within the window flips them back to
   `active` with bindings intact. After the window they are released with the
   normal cooldown. Grace length is an open question; the mechanism is not.
6. **Squatting mitigation.** A per-account cap on non-released claims (small
   single-digit default, raised case by case). Combined with the paid gate,
   the reserved list, and the release cooldown, this keeps a warehouse-the-
   namespace play uneconomical without punishing the legitimate
   personal-plus-business case.

## Enforcement points

The paid entitlement is enforced in exactly one place, taos.my, and everything
else fails closed against it:

- **`POST /api/subdomains/claim`** (taos.my): the only gate that mints new
  namespace. Checks session + entitlement + cap + reserved list transactionally.
- **`POST /api/sites/publish`** (taos.my): checks the `sites_token` scope AND
  that the token's account holds an `active` claim on the named subdomain. A
  `grace` or `released` claim refuses the publish.
- **The taOSgo relay** (`/api/relay/authorize-site`, already live and
  fail-closed): authorizes a hostname only when an `active` claim with a
  binding exists. Lapse or release means the next authorization check refuses;
  no controller round-trip needed.
- **Mesh join** (existing consent-join flow in `account_proxy.py`): unchanged,
  already implicitly taosgo-gated because taos.my only issues join credentials
  to subscribed accounts.
- **The controller enforces nothing.** `account_proxy.py` stays a dumb
  cookie-passthrough; `AccountPanel.tsx` and Web Studio only render states the
  server returns. A modified client gains nothing because every mutating call
  re-checks server-side.

## Migration

Existing accounts may have the old username-derived reservation (the
`ReserveHandleCard` promise of `{handle}.taos.my`, "Included with taOSgo"):

1. Every existing `handle` becomes the account's free username as-is. No user
   action, no loss.
2. Accounts with `taosgo.status` in (`trialing`, `active`) at migration time
   get `{username}.taos.my` auto-claimed as their first subdomain claim
   (counting against the cap), preserving exactly what they were promised.
3. Accounts without an active subscription keep the username but no subdomain
   claim. To honor the old marketing, `{username}` is placed in a per-account
   hold: for a fixed window only that account can claim it. After the window
   it becomes generally available. Window length is an open question.
4. `/api/auth/me` starts returning `username` and `subdomains: [...]`;
   `handle` is kept as a deprecated alias for one release so an older client
   build keeps rendering (the frontend `Account` type marks it deprecated).

## UI touchpoints

- **Settings Account section (#110),
  `desktop/src/apps/SettingsApp/AccountPanel.tsx`:** split the current
  `ReserveHandleCard` into two cards. A free `UsernameCard` (claim/display
  `@username`, copy about the community/social side, no taOSgo mention, no
  `.taos.my` suffix shown). A `SubdomainsCard` under the `TaosgoCard` listing
  claimed subdomains with status (`active`/`grace`), a claim input with inline
  availability feedback, and release. When not subscribed it renders the
  claim UI disabled with a "part of taOSgo" pointer to the existing trial
  button. Update the section intro copy that currently bundles the username
  into the paid pitch. Types in `desktop/src/lib/account-client.ts`
  (`Account.username`, `Account.subdomains`, deprecate `handle`). Tests in
  `desktop/src/apps/SettingsApp/AccountPanel.test.tsx`.
- **Onboarding (#141), `desktop/src/components/OnboardingScreen.tsx`:** the
  cloud-account step (when it lands) offers the free username claim right
  after account creation, clearly labeled free, with the subdomain upsell
  deferred to Settings. Onboarding must never dead-end on the paid path.
- **Web Studio publish (#167), `desktop/src/apps/webstudio/ShareView.tsx` +
  `desktop/src/apps/webstudio/web-sites-api.ts`:** the publish action gains a
  subdomain picker fed by the account's claim list, replacing the assumption
  of a single `{handle}` target. Empty states, in order: not signed in, no
  taOSgo, no claimed subdomains (with a "claim one in Settings" link), mesh
  not joined. The publish caller (controller side, per the mesh-join design's
  Unit 3) passes the chosen subdomain through.
- **Docs/comments cleanup:** `tinyagentos/taosnet/mesh_credentials.py`
  docstring and `docs/design/taosgo-mesh-join-foundation.md` references to
  `<label>.<handle>.taos.my` become subdomain-based once the publish shape is
  final.

## Slice plan

Each slice is PR-sized, independently testable, and written so an external CLI
coding agent can implement it from the text alone. Slices 1 and 2 are in
`jaylfc/taos-website` (endpoint shapes above are the contract; that repo's
layout governs exact paths). Slices 3 to 7 are in this repo.

1. **taos-website: username namespace.** Reserved-names list (shared data
   file) + validation helper, `username` column unique on accounts, claim at
   registration or via `POST /api/account/username`, `/api/auth/me` returns
   `username` (and `handle` alias). Tests: uniqueness, reserved rejection,
   charset/length, alias present.
2. **taos-website: subdomain claims.** `subdomain_claims` table,
   `GET /api/subdomains/check`, `POST /api/subdomains/claim` (entitlement +
   cap + transactional availability), `POST /api/subdomains/release`
   (cooldown), lapse transition to `grace` and expiry to `released`,
   `/api/auth/me` returns `subdomains`. Update `/api/sites/publish` and
   `/api/relay/authorize-site` to key on an `active` claim. Migration per the
   Migration section. Tests: gate refusals (no sub, past_due), cap, cooldown,
   grace round-trip, relay fail-closed on grace/release.
3. **Controller proxy actions.** `tinyagentos/routes/account_proxy.py`: add
   `_ACTIONS`/routes for `GET /api/account/subdomains/check`,
   `POST /api/account/subdomains/claim`, `POST /api/account/subdomains/release`
   (same forwarding + rid-style validation of the `name` query/body field as a
   simple token before it reaches an upstream URL). Tests alongside the
   existing account_proxy tests: forwarding, 503 when unconfigured, name
   validation.
4. **Frontend types + Account panel.** `desktop/src/lib/account-client.ts`
   (`username`, `subdomains: SubdomainClaim[]`, deprecated `handle`; new
   `checkSubdomain`, `claimSubdomain`, `releaseSubdomain` helpers with the
   same degrade-to-state error style) and the `AccountPanel.tsx` card split
   described in UI touchpoints, with `AccountPanel.test.tsx` coverage for
   free-username copy, claim list rendering, disabled claim when
   unsubscribed, and grace badge.
5. **Onboarding username step.** In the #141 cloud-account onboarding work,
   add the free username claim step per UI touchpoints. Bounded to
   `desktop/src/components/OnboardingScreen.tsx` plus its test file.
6. **Web Studio subdomain picker.** `ShareView.tsx` + `web-sites-api.ts`
   changes per UI touchpoints; the controller publish caller (from the
   mesh-join Slice 3) sends `{subdomain, ...}`. Tests: picker states, publish
   payload shape, each empty state.
7. **Docs/comment sweep.** `mesh_credentials.py` docstring,
   `taosgo-mesh-join-foundation.md`, and any remaining `{handle}.taos.my`
   strings (there is one in `AccountPanel.tsx` removed by slice 4; grep for
   `handle}.taos.my` and `<handle>` to confirm none survive).

Ordering: 1 and 2 first (server of truth), then 3, then 4 to 6 in any order,
7 last. Slices 4 to 6 can land behind the existing degrade-gracefully client
pattern even if 1 to 3 lag, since every call already falls back to
`unavailable`.

## Open questions

1. **Grace period length on lapse.** How long a lapsed account's claims stay
   in `grace` before release. Needs to be long enough to survive a failed
   card, short enough that names recirculate.
2. **Release cooldown length.** How long a released name is unclaimable, and
   whether the releasing account gets a shorter (or zero) cooldown to undo
   mistakes.
3. **Per-account claim cap.** The exact default, and whether trialing accounts
   get a lower cap than active ones to blunt trial-cycling squatters.
4. **Cross-namespace impersonation.** Usernames and subdomains are separate
   namespaces by decision, so `alice` the username and `alice.taos.my` the
   subdomain can belong to different people. Do we need a soft protection
   (for example, warn or hold a subdomain matching an established username),
   or is the reserved list plus abuse reporting enough?
5. **Username renames.** Allowed at all? If so, does the old name enter a
   cooldown like released subdomains?
6. **Migration hold window** for unsubscribed accounts' `{username}` names
   (Migration item 3).
7. **Labels under a subdomain.** The mesh-join design used
   `<label>.<subdomain>` nesting for multiple sites per host. With multiple
   claimable subdomains per account, is one site per subdomain apex enough for
   v1 (simpler certs, simpler routing), with nesting deferred?
