# Remote-Desktop Vision — App Audit

This document audits every existing taOS app and several cross-cutting concerns
against the **remote-desktop framing** of the product:

> taOS is a server-hosted desktop. The Pi (or other host) is the user's
> "actual machine"; phone, laptop, work computer are *terminals* into it.
> Server is the source of truth for sessions, cookies, files, agents.

The audit was triggered during the BrowserApp v2 brainstorm, when the framing
crystallised. Recording it here so future app brainstorms inherit the
conclusions and don't relitigate them.

---

## Per-App Verdict

### ✅ Already aligned — no rework

These apps are already server-side and the remote-desktop framing requires no
changes to their model:

- **AgentsApp** — agents in LXC, server-side runtime
- **ProjectsApp** — Kanban + canvas, server-side data
- **MemoryApp** — memory store
- **TasksApp** — server-side tasks
- **SecretsApp** — credentials live on the host
- **MCPApp** — MCP server config
- **ModelsApp** — model browser, host-side
- **ProvidersApp** — LLM provider config
- **ClusterApp** — cluster management
- **StoreApp** — app marketplace
- **AgentBrowsersApp** — Playwright headless browsers, host-side
- **ChannelsApp** — channel config
- **TerminalApp** — terminals into the host
- **ImagesApp** — server-side image generation (sd-cpp, image-gen)
- **MessagesApp** — chat data is server-side

### ⚠️ Open — bidirectional file transfer is the question

These apps are server-side but the **user-device ↔ host file transfer** UX is
still undefined:

- **FilesApp** — files live on the host (correct). What's missing is an
  obvious affordance for "I want this file on my MacBook to read offline" and
  for "upload these photos from my phone into taOS Files." Both directions.
- **ImportApp** — same pattern: "import from this device" needs a first-class
  flow, not a workaround through the browser.
- **LibraryApp** — books/media on the host, consumed on phone. Streaming vs
  download-for-offline is a meaningful UX choice and currently undefined.

These three apps share a primitive that doesn't exist today: a system-wide
**bidirectional file-transfer service**. Worth designing once, used everywhere.

### ✅ Keep as standalone — agent-content management, not browsing

Initially flagged as possible "fold into BrowserApp" candidates. **Wrong call.**
These are agent-content-management apps, not browsing surfaces:

- **GitHubApp** — assigns specific repos to specific agents (per-agent repo
  permissions, agent-managed repo state). Not a browsing surface.
- **XApp** — routes specific authors' tweets to specific agents (per-agent
  author subscriptions, content distribution to agents). Not a browsing
  surface.
- **RedditApp** — same pattern: per-agent subreddit/feed assignment.
- **YouTubeApp** — same pattern: per-agent channel assignment.

**Pattern across all four:** they are *content distribution* apps that route
external content sources to specific agents. The user's own browsing of those
sites stays in the BrowserApp; these apps exist for *agents* to consume from
those sources with proper per-agent scoping.

The apps may borrow renderers from BrowserApp internally (proxied iframes for
preview), but the *primary reason they exist* is agent-content routing.

---

## Cross-Cutting Concerns — answered

### 1. Login + multi-device authentication — **answered**

- **Local LAN access:** username + password. Simple, no surprises.
- **Remote access:** out of scope for the local-first core — handled by a
  separate, optional remote-relay service, not a free built-in tunnel.
- **Adding a new device** to an existing taOS host: pairing-code mechanism.
  The same primitive that already exists for adding workers to the cluster
  is reused for pairing user devices.

**No further design work needed for the auth model itself.** The remote-relay
service is its own design problem (separate, server-side product), out of scope
for the BrowserApp work.

### 2. Multi-user — full OS-style separation

taOS supports multiple humans on one host (family scenario, shared homelab,
small office). Separation is **OS-grade**, not application-level:

- Each user has their **own files, agents, browser profiles, secrets, tasks,
  projects, memory, message channels** — full isolation.
- Cross-user sharing is explicit and opt-in (e.g. shared family Library, a
  shared "House" message channel).
- Browser profiles (Personal / Work) are *within* a user account, not a
  substitute for user separation.

**Implication for every app:** the tenant key for every store is `(user_id,
…)` not just `(…)`. Profile and per-agent capability grants are *nested
inside* the user.

### 3. Native shells — the Mac app is a host installer

The Mac app (planned via Apple Containerization, notarised .app) is **a way to
install taOS as the host on a Mac** — i.e. the user's Mac becomes the server.
It is not a "Mac client" for connecting to a remote taOS.

- Connecting to a taOS host (whether local Mac, Pi, or via the remote-relay
  service) is always via the **PWA** in any modern browser. There is no separate
  "client app" to build.
- Mobile install is the same PWA, installed to the home screen. iOS 16.4+ is
  the floor.

**No native client app track is on the roadmap.** Existing memory
(`project_taos_mac_app.md`) refers specifically to the host-installer Mac app.

### 4. Offline behavior — answered

- **Default:** no offline. The user device is a thin renderer; disconnect from
  the host = no taOS.
- **User-managed exception:** user sets up a VPN themselves (Tailscale,
  WireGuard, etc.) — works because the host is reachable.
- **Remote-relay exception:** the optional remote-relay service covers many
  "I'm not on the LAN" cases. Thin-client offline caching could live there too,
  but that's a question for that service, not the core platform.

**No offline design work needed in the core platform.**

### 5. Push notifications — **still open**

Cross-device push ("agent finished my report → ping my phone, ping my laptop
even if I'm not currently in taOS") is a **missing primitive**.

Web Push works in PWAs (iOS 16.4+, Android, desktop Chrome/Safari). Server-side,
the host needs a push-subscription registry per (user, device) and a way to
deliver events to the right subset of subscriptions.

**Recommended:** design as part of the next foundations brainstorm (alongside
file transfer below).

### 6. Bidirectional file transfer — **still open**

Used by Files, Import, Library, Browser downloads, and agent outputs. Should
be one mechanism, not five. Currently each app would invent its own.

**Recommended:** design as part of the next foundations brainstorm.

### 7. Clipboard / hand-off — **opportunistic, low priority**

"Copy on Mac, paste on iPhone" via taOS clipboard primitive. Cute, useful,
not load-bearing. Defer.

---

## Open Items — recommended sequencing

In priority order:

1. **BrowserApp v2** — *current brainstorm*. Trailblazer that establishes the
   patterns: server-side state, profiles, cookie-aware proxy, agent capability
   grants, sticky agent presence, push events from server to UI.

2. **Remote-Desktop Foundations** — next brainstorm. Three primitives the
   audit identified as missing or under-specified:
   - **Push notifications** (#5 above) — server → device, multi-device,
     per-user.
   - **Bidirectional file transfer** (#6 above) — used by Files / Import /
     Library / Browser downloads / agent outputs.
   - **Multi-user storage tenancy** — confirm the `(user_id, …)` keying is
     consistent across every existing store; refactor any that aren't.

3. **App revisits** (separate brainstorms, only as needed):
   - **FilesApp** — adopt the file-transfer primitive, add upload-from-device,
     download-to-device, share affordances.
   - **ImportApp** — same.
   - **LibraryApp** — same, plus streaming-vs-download UX choice.
   - **GitHubApp / XApp / RedditApp / YouTubeApp** — confirm per-agent content
     routing model, which is a different design conversation per app.

4. **Remote-relay service** — out of scope for the core platform brainstorms;
   a separate, optional service.

5. **Clipboard hand-off** (#7) — deferred. Revisit if user demand surfaces.

---

## Items already in `~/.claude` memory

The following framings are now in user-memory so future planning sessions
inherit them automatically:

- `project_remote_desktop_vision.md` — the framing itself
- (existing) `project_taos_mac_app.md` — Mac app as host installer
- (existing) `feedback_no_sensitive_data.md` — never commit IPs/credentials

Updates to memory may be required after Foundations brainstorm:

- A `project_multi_user.md` recording the OS-style user-separation decision
- A `project_pro_remote_relay.md` recording the remote-access model

These will be added when the Foundations brainstorm runs.
