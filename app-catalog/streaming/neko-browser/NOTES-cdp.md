# taOS Neko CDP image — recipe notes + Pi test plan

## What this image fixes

The stock `ghcr.io/m1k1o/neko/chromium:latest` ships Chromium 146 with two
CDP blockers:

1. `/etc/chromium/policies/managed/policies.json` sets
   `DeveloperToolsAvailability: 2` — CDP flat-sessions are silently rejected.
2. Chromium 146 has a flat-session bug; CDP does not work even with the policy
   patched.

`Dockerfile.cdp` applies the proven fix:

| Step | What | Why |
|---|---|---|
| 1 | Upgrade Chromium to >=148 via `trixie-security` | Fixes the flat-session CDP bug present in 146 |
| 2 | Patch `policies.json` to `DeveloperToolsAvailability: 0` | Re-enables DevTools / CDP |
| 3 | Drop-in `/etc/chromium.d/taos-cdp`: `--remote-debugging-port=9222 --remote-debugging-address=127.0.0.1 --remote-allow-origins=* --touch-events=enabled` | Binds CDP to loopback only (security) |
| 4 | Drop-in `/etc/chromium.d/taos-shm`: `--enable-dev-shm-usage` | Lets Chromium use the larger /dev/shm from `--shm-size=4g` |

**Security:** CDP is bound to `127.0.0.1` only — never `0.0.0.0`.
Access from the taOS host is via `docker exec` / a host-local port tunnel,
not a publicly-exposed port.

---

## Image name

```
ghcr.io/jaylfc/taos-neko-cdp:latest
```

Architectures: `linux/arm64` (primary — RK3588), `linux/amd64` (x86 nodes).

---

## Pi test plan (RK3588, arm64)

### Prerequisites

- Pi running taOS (Orange Pi 5 Plus, arm64).
- Docker installed and running.
- At least 4 GB RAM free for the container.

### Step 1 — pull the image

```bash
docker pull ghcr.io/jaylfc/taos-neko-cdp:latest
docker inspect ghcr.io/jaylfc/taos-neko-cdp:latest | grep -i arch
# expect: "Architecture": "arm64"
```

### Step 2 — spin the container

```bash
docker run -d --rm \
  --name taos-neko-cdp-test \
  --shm-size=4g \
  -p 18080:8080 \
  -e NEKO_MEMBER_MULTIUSER_USER_PASSWORD=testuser \
  -e NEKO_MEMBER_MULTIUSER_ADMIN_PASSWORD=testadmin \
  -e NEKO_WEBRTC_EPR=59100-59110 \
  -e NEKO_WEBRTC_NAT1TO1=127.0.0.1 \
  -e NEKO_DESKTOP_SCREEN=1280x720@30 \
  --device /dev/mpp_service \
  --device /dev/dri \
  --device /dev/rga \
  ghcr.io/jaylfc/taos-neko-cdp:latest
```

### Step 3 — confirm Chromium version >=148

```bash
docker exec taos-neko-cdp-test chromium --version
# expect: Chromium 148.x.x.x  (or higher)
```

### Step 4 — confirm CDP is reachable

CDP is bound to `127.0.0.1` inside the container, so use `docker exec`:

```bash
docker exec taos-neko-cdp-test \
  curl -sf http://127.0.0.1:9222/json/version | python3 -m json.tool
# expect: JSON with "Browser": "Chrome/148..." and "webSocketDebuggerUrl"
```

### Step 5 — confirm policy patch (DeveloperToolsAvailability=0)

```bash
docker exec taos-neko-cdp-test \
  cat /etc/chromium/policies/managed/policies.json
# expect: {"DeveloperToolsAvailability": 0}
```

### Step 6 — open a CDP page session (flat-session smoke)

```bash
# List targets
docker exec taos-neko-cdp-test \
  curl -sf http://127.0.0.1:9222/json/list
# expect: JSON array with at least one entry (the default Chromium tab)
```

### Step 7 — open the Neko stream in a browser

Navigate to `http://<pi-ip>:18080` in a browser on the same LAN.
Log in with `testuser` / `testuser`. Confirm the Chromium stream appears and
is interactive.

### Step 8 — clean up

```bash
docker stop taos-neko-cdp-test
```

---

## Known unknowns (flag for Jay)

- **Exact Chromium version:** `Dockerfile.cdp` installs the current
  `trixie-security` chromium at build time without pinning an exact version.
  This means the version baked into the image depends on what trixie-security
  has at the moment the CI workflow ran. The `docker exec chromium --version`
  step above reveals the exact version. If it is <148, the trixie-security
  pool has not yet landed 148 on arm64 — check the pool and pin explicitly.
  At the time of writing (2026-06), trixie-security carries 148.x on both
  amd64 and arm64, but this should be verified on the first build.

- **rkmpp HW-encode:** this image uses software encode (same as the
  validated stock base). The rkmpp GStreamer layer (#624) is a follow-on that
  extends this image; the CDP fix is independent and ships first.

- **CDP inside taOS launcher:** `browser_container.py` sets `cdp_url =
  "http://127.0.0.1:9222"` for RK3588 sessions. The taOS backend currently
  does not publish CDP port 9222 from the container to the host — wiring the
  host-side port binding or docker-exec tunnel is the next step in the agent
  automation layer (tracked separately).
