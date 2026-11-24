# Device-link test bed (T0)

A local, multi-container bed for the "my devices" work: presence, notification
mirroring, clipboard, and the multi-tenancy proofs around them. This directory is
**test-only** and **opt-in**. It is never part of a normal test run, it starts
nothing by itself, and it connects to nothing outside the machine that runs it.

## What it is

`bed.sh up` builds and starts, on one docker host:

| Piece | What |
|---|---|
| `headscale` | Headscale pinned to **0.29.1** (the version taos.my deploys), embedded DERP, `node.expiry: 0`, plain-http login server, and the taos.my repo's own `policy.hujson` (copied from the taos.my tree at run time, never committed here) |
| `taosmy` | a local taos.my built from a checkout of the taos.my repo (you point at it, nothing is copied), seeded with three accounts through its operator CLI (`user create`, `entitlement grant`); no mail, no Stripe |
| `a-host1`, `a-host2`, `a-host3`, `a-phone` | person A: three hosts and a phone |
| `b-host`, `b-phone` | person B |
| `shared-s`, `f-phone` | a shared computer hosting A (owner) and F (an invited member), each with their own Linux user and their own node; F's phone |
| `relay` | a node tagged `tag:relay` under `taos-relay`: the network position of the browser relay (policy: `*:6969` only). No relay software. |
| `headscale-personal`, `personal` | a second Headscale and a container with a **kernel-tun** tailscaled: the stand-in for a person's own tailnet. `a-host1` and `a-phone` also carry a kernel-tun instance on it. |

Every taOS node runs the taOS controller (from the taOS ref under test) and **one
userspace tailscaled per hosted person**, exactly as design section 5.0 says:
`--tun=userspace-networking`, own statedir and socket, SOCKS5 at `27800+n`, joined
with `--accept-dns=false`, every CLI call with `--socket`, and the mappings

```
tailscale --socket=... serve --tcp=6977 tcp://127.0.0.1:27700+n    device-link
tailscale --socket=... serve --tcp=6969 tcp://127.0.0.1:27750+n    relay remote access
```

Until the real per-person listeners exist (PR T3) each loopback port is held by a
tiny stand-in (`images/bed-stub.py`, answers 404 with an `X-Bed-Listener` header).
It lets the network-layer tests be real today, and it cannot satisfy a test that
needs controller behaviour. Start with `TAOS_BED_STUBS=0` once T3 has merged.

Preauth keys and the Headscale API key are minted inside the bed at run time into
`.state/` (git-ignored). **Nothing needs a secret.** The compose network is
`internal: true` (no route out of the host) and every tailscaled runs with
`--no-logs-no-support`, so nothing in the bed talks to anything outside it. The
Headscale address prefixes are appended to the configs at run time by `bed.sh`, so no
address literal is committed.

## What it needs

- A Linux host with **docker** and the **compose v2 plugin** (BuildKit; the node
  image uses a named build context). This will be provisioned later as one
  Proxmox LXC with `nesting=1` (and `keyctl=1`) and docker inside; **do not assume
  an address**: the tests never use one. Any Linux host with docker works the same.
- `/dev/net/tun` and `NET_ADMIN` for the three kernel-tun containers (`a-host1`,
  `a-phone`, `personal`). In the Proxmox LXC this means the LXC itself must have
  `/dev/net/tun` passed through (a device rule plus the node; test no rule, the
  rule alone, and the rule plus an explicit allow). Every other container gets
  neither, on purpose: a userspace-only node must work without them.
- `bash`, `git`, `python3` on the host that runs `bed.sh`; `pytest` and `pyyaml`
  for the tests.
- A checkout (or any directory) of the **taos.my** repo at the commit to test.

## How to run it

```sh
cd tests/e2e/device_link

# which taOS tree and which taos.my tree
export TAOS_MY_REPO=/path/to/taos-website        # a clone; TAOS_MY_REF defaults to origin/main
#   or: export TAOS_MY_DIR=/path/to/a/taos.my/tree
#   TAOS_SRC_DIR / TAOS_REF pick the taOS tree (default: `git archive` of HEAD of this
#   repo, so uncommitted changes are not in the image)

./bed.sh up          # build, start, mint keys, seed, join every node (several minutes the first time)
./bed.sh status

TAOS_E2E_DEVICE_LINK=1 pytest tests/e2e/device_link -v   # from the repo root

./bed.sh down        # removes every container, network, volume and .state/
```

Other commands: `bed.sh exec NODE CMD...`, `hs`, `hsp`, `my`, `policy repo|allow-all`
(MT5's deliberate misconfiguration), `stop|start SERVICE...` (the taos.my and
Headscale down cases), `nodes`. `BED_TIME_SCALE` (default 60) is passed to the
controllers as `TAOS_DEVICE_LINK_TIME_SCALE`; the tests' `bed.sleep()` divides by it.

## Opt-in: how it stays out of normal runs

- `conftest.py` adds the `device_link_e2e` marker (registered in `pyproject.toml`)
  to **every** test in this directory, and skips all of them unless
  `TAOS_E2E_DEVICE_LINK=1`. A plain `pytest tests/` collects them and skips them;
  `-m "not device_link_e2e"` deselects them. CI's command already passes
  `--ignore=tests/e2e`.
- With the variable set and no bed up, the real tests **error** instead of
  skipping: an opt-in run that measured nothing must not look like a pass.

## What the tests are

- **Real today** (20): the bed itself (`test_device_link_bed.py`: Headscale
  version, `node.expiry`, nodes registered and never expiring, userspace instances,
  no kernel state, the serve mappings, per-person slots and socket ownership,
  SOCKS5 outbound) and the pure-network layers
  (`test_device_link_mesh_isolation.py`: **MT3**, **MT4**, **SC1**, **MT11** the
  personal tailnet cannot reach 6977, **MTU2** at bed level: forwarded relay
  traffic never lands on the console port, and the policy-swap lever MT5 needs).
- **Waiting for code** (74): every other section 11 id (FD, SK, RS, RC, SL, R, F,
  N, C, L, MT, MTU, SC, ID) is a test marked
  `xfail(strict=True, reason="needs <PR id>")`. Strict means the moment the PR
  lands the test starts passing, the run fails with XPASS, and the marker has to
  come off. Their bodies are drafts: each states its red and its must-succeed
  control side by side, using the design's route names where section 4.1 and 5.1
  give them. Request and response shapes are **placeholders** that the owning PR
  adjusts, and so are the pieces the design does not specify (the family-leave route,
  the lock-state injection, the removal-request route). Helpers for a capability the
  bed does not have yet (a mail sink for the emailed code, a known account password
  because the operator CLI seeds a random one, a taos.my session or controller
  token, a device-key signer, a plaintext tap, a non-linked local member) fail with
  the PR that has to supply it, so no test can pass by skipping a step. A bed that
  is not up is a SKIP ("bed not up"), decided before the xfail marks are evaluated,
  so an xfail only ever means "waiting for that PR".
- Mutation checks for MT5, MT6, MT9 (remove the guard in a scratch branch, the red
  must go green-for-the-attacker) are run by hand, as the design says.

## What each xfail waits for

The ids are the PR plan of the device design (`native/specs/devices-presence-and-mirroring-20261122.md`
in the team notes). Each xfail names its id; this is what lands it:

| id | repo | lands |
|---|---|---|
| M1 | taos.my | roster columns, first device (password + emailed code, empty roster only), later-device join with `key_mac` and no mesh key before the vouch |
| M2 | taos.my | recovery (24 h wait, cancel, wipe), the session-lock exemption for the same account's enrolled devices, roster routes |
| T1 | taOS | account links, device keys, pairing secret, enrolment with the resumable ticket and Skip, approve; remote-connection console lock |
| T2 | taOS | roster cache, two-device removal notices, `/roster-event`, `/tombstones`, device keys and persons in the revocation feed |
| T3 | taOS | per-person loopback device-link listeners reached by `serve` from 6977, the remote-marked relay listener, outbound through the mesh |
| T4 | taOS | presence beats, `GET /api/my-devices`, rename and remove with the lock gate (423), the My devices tab |
| T5 | taOS | notification mirroring core, settings, rate limits, the locked-phone summary |
| T6 | taOS native | `taos-notifyd` and the OS notification route |
| T7 | taOS (+ native) | clipboard routes and helper, lock gate both ways |
| T9 | taOS | the per-person userspace mesh daemon template, slots and `serve` mappings |
| P2 | taOSmobile | the phone's presence source (battery, screen state) |

## Not in this PR

First run 2026-11-23 on a throwaway Debian 13 LXC (Docker 26.1, nesting, /dev/net/tun
passed through): `bed.sh up` succeeds and the 20 real tests pass (three runs in a row);
the 74 strict xfails all still fail, waiting for their PRs. Known gaps: the controller image installs taOS with
`pip install -e` and is untested; the tailscale `serve --bg` flags, the Headscale
JSON field names (`nodes list -o json`) and the controller's `/auth/lock-state`
answer are taken from the two-tailscaled test and the design, to be confirmed on
the first run; there is no mail sink yet (M1); the 6969 listener of the controller
is still `0.0.0.0` (existing behaviour; MT11 asserts only 6977); the Headscale node
JSON field names (`tags`, `expiry`, `user.name`) and the `tailscale serve status
--json` shape are confirmed on the first run.
