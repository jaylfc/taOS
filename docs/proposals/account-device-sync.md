# One account across your devices (proposal)

Status: PROPOSAL for review by taOS-dev (core) and taosmd-dev (memory). The
open choices in "Decisions for Jay" are not ruled yet. Author: taOSmobile-dev.

## Why this exists

Jay, 2026-11-01: "we also need to consider users who want the same account on
their mobile and desktop taOS instance, we need an icloud like eco system".

Today a person with taOS on a phone and on a desktop has two unrelated
installs. Each one has its own OS user, its own agents, memory, Secrets and
settings. Signing in on the second device starts again from nothing. The
onboarding work (taOSmobile PR #25, `docs/onboarding/fresh-user-walkthrough-20261031.md`)
has a "taOS account" step and a "this device" step. This note says what those
two steps must mean once a second device exists.

## What exists today (measured on origin/dev, 2026-11-01)

- **Cluster pairing has no user state.** `tinyagentos/cluster/pairing_store.py`
  holds a name, HMAC `signing_key`, url, platform and kind (`worker` or
  `device`) for each worker. That key authorises compute calls only.
- **The taos.my join path is host-scoped.** `tinyagentos/routes/account_proxy.py`
  (`/api/account/cluster/join/*`) stores mesh join credentials and scoped,
  host-bound tokens (`taosnet:passkey`, `sites_token`). It never carries agents,
  memory, Secrets or settings.
- **Agent ids are minted per host.** `agent_registry_store.mint_canonical_id`
  returns `{slug}-{YYYYMMDD}-{HHMMSS}` from the creating host's clock. Two
  devices that each create "taOS Agent" get two different ids, so the same agent
  would fork into two.

So cross-device sync is new work; there is nothing to retrofit.

## Already decided

- Identity is 1:1: one OS user maps to one taos.my account (Jay 2026-07-01,
  taOSgo spec). A linked account unlocks off-LAN remote access and
  cross-device sync. With no account, taOS stays LAN-only.
- For sharing compute: "cloud is the easy path, never load-bearing, P2P always
  works".
- taos.my routes to the user's own host and hosts nothing (#78).

## Proposal

### Where the data lives

The source of truth is **the user's own instances**. They replicate to each
other peer-to-peer over the mesh (the Headscale network taos.my already
provisions). taos.my does three jobs and no more:

1. identity (sign-in, the account),
2. the device roster (which instances belong to this account),
3. an off-LAN relay, so two devices on different networks can still reach each other.

The relay only forwards traffic; it does not store data. An optional
encrypted backup blob on taos.my is a later add-on that Jay must approve.

### Data classes

| Class | Syncs? | Conflict rule | Notes |
|---|---|---|---|
| Identity (account, OS username, display name, avatar) | yes | taos.my is authoritative | Set once in onboarding step 3. |
| Device roster | yes | taos.my is authoritative | Adding a device needs approval from an existing one. |
| Agents (config, persona, model and gateway assignment) | yes | last writer wins per record | `canonical_id` becomes account-scoped (see below). |
| Memory (taOSmd episodes and facts) | yes | union-merge by record id; an edit to the same fact is last writer wins, keeping the prior version | Memory is mostly appended, so a union loses nothing. |
| Secrets | yes, end-to-end encrypted | last writer wins per secret | Per-device-only flag, see below. |
| Settings (appearance, notifications, layout) | yes, per class | last writer wins per key | Some keys stay per device: display scale, kiosk, hardware. |
| Files, models, app data | no | none | Too large, and already reachable through the cluster. |
| Hardware state (paired peripherals, radios, device creds) | no | none | Belongs to one machine. |

### Clocks and deletes

Every synced record carries a hybrid logical clock (HLC) timestamp and an
origin device id. A delete writes a tombstone instead of removing the row, so
a device that was offline cannot bring a deleted record back. Tombstones are
removed only once every device in the roster has acknowledged them.

### Agent identity

An agent's `canonical_id` must be scoped to the account, not to the host, or
every device mints its own copy of the same agent. Proposal: mint it once on
the device that creates the agent, from the account id plus a random suffix,
and replicate it to the other devices. The system agent "taOS Agent" gets one
fixed id per account. Changing its harness or model on any device keeps its
name, memory namespace and history. This touches `agent_registry_store` and
taOSmd namespacing, so taosmd-dev must review it.

### Secrets key model (Keychain style)

- Each account has one symmetric key for Secrets. It is never sent to taos.my
  in plain text.
- Each device holds its own key pair. The account key is stored wrapped
  (encrypted) once for each device in the roster.
- A new device is approved from a device the user already has, with the same
  approval shape as cluster join (a code shown on both screens). Approval
  wraps the account key for the new device.
- The relay and any backup only ever hold ciphertext.
- A secret can be marked **this device only**: hardware credentials, or a
  local API key that only makes sense on one machine. Those never sync.
- Losing every device loses the Secrets unless a recovery key was saved. The
  onboarding should offer one, and must say plainly what happens without it.

### Onboarding flows

**First device** (step 3 "taOS account", step 4 "this device"):
the account is created or signed in at step 3. Step 4 says "This is your first
taOS device" and registers it in the roster. Skipping step 3 keeps the device
LAN-only, and Settings later offers "Sign in to sync your devices".

**Second device**: step 3 is "Sign in to your taOS account". After sign-in,
step 4 shows "Approve this device" with a code to confirm on a device the user
already has. Once approved, the device pulls identity, agents, memory, Secrets
and settings, and onboarding skips the steps that data already answers
(account, about you, the personal assistant, taOS Agent setup). It goes
straight to a "Welcome back" screen and then the user's existing chat.

**No second device to approve from**: sign-in still works. Agents, memory and
settings sync. Secrets stay locked until the recovery key is entered or
another device approves.

## Decisions for Jay

1. **Where sync data lives.** Recommended: on the user's own devices,
   peer-to-peer, with taos.my as identity, roster and relay only. Alternative:
   taos.my stores an encrypted copy as the hub.
2. **Secrets.** Recommended: end-to-end encrypted sync with a per-device-only
   flag and an optional recovery key. Alternatives: Secrets never sync, or sync
   with no recovery path.
3. **Conflicts.** Recommended: HLC timestamps with tombstones; last writer wins
   for config and settings; union-merge for memory. Alternative: last writer
   wins everywhere, which is simpler but can drop memory written on two
   devices at once.

taOS-dev will put these to Jay as one multiple-choice question after review.

## Build order once ruled

1. Account-scoped `canonical_id`, with a migration for existing agents.
2. HLC + tombstone columns on the synced stores.
3. Device roster and the approve-this-device flow (taos.my + core).
4. Replication over the mesh, then the relay path.
5. Secrets key wrapping.
6. Onboarding second-device screens. The "Sign in to your taOS account" screen
   can be built now against a stub; the device-approval step waits for 3.
