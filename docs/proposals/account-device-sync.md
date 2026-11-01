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

### The sync unit

The unit that syncs is **(host, OS user)**, not the host. A self-hosted taOS
box can have several OS users, each linked to their own taos.my account. The
roster, replication and Secrets key wrapping are all per OS user on a host, so
two people sharing one family machine never end up in the same account's
roster. "Device" below means one OS user on one host.

### Where the data lives

The source of truth is **the user's own instances**. They replicate to each
other peer-to-peer over the mesh (the Headscale network taos.my already
provisions). taos.my does three jobs and no more:

1. identity (sign-in, the account),
2. the device roster (which instances belong to this account),
3. an off-LAN relay, so two devices on different networks can still reach each other.

The relay only forwards traffic; it does not store data. An optional
encrypted backup blob on taos.my is a later add-on that Jay must approve.

**The cost of this choice:** a relay that stores nothing can only connect
devices that are online at the same time. A phone and a desktop that are never
on together never sync. That is the real case for the alternative in Decision
1: taos.my keeps an encrypted copy, or a store-and-forward queue of
ciphertext, so one device can drop changes off and the other can collect them
later.

### Data classes

| Class | Syncs? | Conflict rule | Notes |
|---|---|---|---|
| Identity (account, OS username, display name, avatar) | yes | taos.my is authoritative | Set once in onboarding step 3. |
| Device roster | yes | taos.my is authoritative | Adding a device needs approval from an existing one. |
| Agents (config, persona, model and gateway assignment) | yes | last writer wins per record | `canonical_id` is minted once and adopted by other devices (see below). |
| Memory (taOSmd episodes and facts) | yes | union-merge by record id; the rule for two edits to the same fact is **taosmd-dev to define**, using taOSmd's own temporal and supersede model | Memory is mostly appended, so a union loses nothing. |
| Secrets | yes, end-to-end encrypted | last writer wins per secret | Per-device-only flag, see below. |
| Settings (appearance, notifications, layout) | yes, per class | last writer wins per key | Some keys stay per device: display scale, kiosk, hardware. |
| Files, models, app data | no | none | Too large, and already reachable through the cluster. |
| Hardware state (paired peripherals, radios, device creds) | no | none | Belongs to one machine. |

### Clocks and deletes

Every synced record carries a hybrid logical clock (HLC) timestamp and an
origin device id. A delete writes a tombstone instead of removing the row, so
a device that was offline cannot bring a deleted record back. A tombstone is
removed once every device in the roster has acknowledged it. Removing a device
from the roster counts as its acknowledgement, so a lost or retired device
cannot hold tombstones forever. A TTL bound applies as well. A device that
comes back after the TTL must re-sync in full; it cannot replay its old
changes.

### Agent identity

The defect is minting twice, not the id format. The `canonical_id` format
stays `{slug}-{YYYYMMDD}-{HHMMSS}[-{2hex}]`. An id is minted **once**, on the
device that creates the agent, and replicated. A joining device **adopts** the
account's ids during approval and never mints its own copy. That needs no id
migration. It also keeps `_slug_globs` and every consumer of the current
shape working, and it keeps the account id out of handles, A2A `from` and
memory namespaces.

"taOS Agent" works the same way: the first device mints its id, and every
later device adopts it. Changing its harness or model on any device keeps its
name, memory namespace and history. taosmd-dev must confirm that taOSmd
namespacing holds when the same `canonical_id` writes memory from two hosts
at once.

### Secrets key model (Keychain style)

- Each account has one symmetric key for Secrets. It is never sent to taos.my
  in plain text.
- Each device holds its own key pair. The account key is stored wrapped
  (encrypted) once for each device in the roster.
- A new device is approved from a device the user already has, with the same
  approval shape as cluster join (a code shown on both screens). Approval
  wraps the account key for the new device. The code has the cluster-join
  protections: single use, a short TTL and an attempt cap (as in
  `pairing_store.py` `_EXPIRY_SECS` and `_MAX_ATTEMPTS`).
- The relay and any backup only ever hold ciphertext.
- A secret can be marked **this device only**: hardware credentials, or a
  local API key that only makes sense on one machine. Those never sync.
- Losing every device loses the Secrets unless a recovery key was saved. The
  onboarding should offer one, and must say plainly what happens without it.

**Remove a device.** A removed or lost device still holds the account key, so
removal must:

1. drop the device from the roster (which also counts as its tombstone
   acknowledgement),
2. rotate the account Secrets key and re-wrap it for the remaining devices,
3. re-encrypt the existing secrets under the new key, so the removed device
   cannot read anything synced after it was removed. Secrets it already held
   should be treated as exposed, and Settings should offer to rotate the
   underlying credentials (API keys).

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
   peer-to-peer, with taos.my as identity, roster and relay only. Cost: two
   devices that are never online at the same time never sync. Alternative:
   taos.my keeps an encrypted copy, or a store-and-forward queue of
   ciphertext, so offline devices still catch up; taos.my then holds the
   user's data, encrypted.
2. **Secrets.** Recommended: end-to-end encrypted sync with a per-device-only
   flag and an optional recovery key. Alternatives: Secrets never sync, or sync
   with no recovery path.
3. **Conflicts.** Recommended: HLC timestamps with tombstones; last writer wins
   for config and settings; union-merge for memory. Alternative: last writer
   wins everywhere, which is simpler but can drop memory written on two
   devices at once.

taOS-dev will put these to Jay as one multiple-choice question after review.

## Build order once ruled

1. Adopt-on-join for `canonical_id`: a joining device takes the account's
   ids instead of minting. No id migration.
2. HLC + tombstone columns on the synced stores, keyed per (host, OS user).
3. Device roster and the approve-this-device flow (taos.my + core).
4. Replication over the mesh, then the relay path.
5. Secrets key wrapping.
6. Onboarding second-device screens. The "Sign in to your taOS account" screen
   can be built now against a stub; the device-approval step waits for 3.
