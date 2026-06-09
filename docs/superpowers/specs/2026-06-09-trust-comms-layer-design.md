# taOS Trust & Comms Layer — Design

**Date:** 2026-06-09
**Status:** Draft for review (Jay's direction captured; build sequencing proposed)
**Owners:** taOS leads (multi-user/permission/apps); @taOSmd owns the bus + memory + blob-serve internals. Collaborative.
**Supersedes:** the "just set registry_url" cutover. Auth is now a *designed feature*, not a config toggle.

## 1. What this is

The unified layer that answers three questions for every agent (internal or external) and every message:
- **Who are the people?** (Users / multi-user)
- **What can an agent do?** (Permissions)
- **Who is connected to whom?** (Relationships)

…and routes all communication through **one identity-bearing bus** (A2A) so identity + permissions + audit apply uniformly.

It builds on shipped pieces: agent registry (#710, identity + EdDSA-JWT + revocation feed), `relationships.py` `RelationshipManager` (groups + agent_permissions edges), the multi-user auth-context (`current_user`), governance lifecycle (#716/#718), per-app browser permissions (`browser-site-permissions-api.ts`), and the governance §3a non-dismissable approval-notification pattern.

## 2. The consent loop (request → notify → accept → mint → grant)

External agents (Claude Code sessions, Hermes — they run *outside* taOS) get access via a **desktop handshake, not a config file**:

1. User tells the agent "use my taOSmd on my taOS server."
2. Agent → `POST /api/agents/auth-requests` `{identity_claim, framework, requested_scopes[], requested_skills?[], reason, duration?, project_id?}` → `{request_id, status:"pending"}`. This raises a **non-dismissable desktop notification** (accept/deny, cannot be dismissed — must decide).
3. Agent polls `GET /api/agents/auth-requests/{request_id}` → `{status: pending|accepted|refused, …}` (poll now; push later).
4. On **accept**, taOS atomically: **mints identity** (registry canonical_id + EdDSA-JWT) → **creates a Relationship edge** (agent ↔ this instance / group) → **writes Permission grants** (the approved scopes/tiers) → returns `{canonical_id, token}` on the status endpoint.
5. Agent presents the token on `/a2a/send` + memory calls thereafter.

### Per-scope consent (Jay)
The notification is **iOS-style with per-scope toggles** — it lists each requested scope (memory r/w, A2A, specific skills, file paths, duration) and the user can grant a **subset**. The granted subset becomes the permission grants. (Not whole-request all-or-nothing.)

### Token model (proposed; @taOSmd enforces — pending their confirm)
Token = **identity only** (`sub=canonical_id`, `user_id`, `iss=taos-registry`). **All tiering** (scope / project / time-boxed / always-allow / always-block / revoke) lives in the **permission store + the inactive/revocation feed** @taOSmd polls. So time-boxed = a grant expiry that drops out of the "allowed" feed; project-scoped = a permission row; revoke = into the feed. Keeps the token simple and consistent with the chosen revocation-**list** model (not token-exp). Bus check = (valid token ⇒ identity) AND (agent allowed per permission/feed).

## 3. Permission tiers

A grant = `{agent, object (instance | resource | other-agent), scope, tier}`, **tier ∈ once · time-boxed(duration) · project-scoped(project_id) · always-allow · always-block**. Mirrors browser site-permissions (allow/block/ask) — reuse that pattern. The **notification is the primary decision surface** ("ask"); "always-*" persists so it stops prompting; the **Permissions app** is the management surface (review, revoke, change always→ask, see expiry). Flow: notification → record → app.

## 4. Three apps (separate, by Jay)

- **Users app / Settings section** — human multi-user: accounts, auth, admin/member roles, per-user access. Backend: `AuthManager` + the multi-user work (`project_multi_user`).
- **Permissions app** — what each agent/app may do: the tiered grants, capabilities, model/file access. Backend: `capabilities.py` + per-app permissions (#622) + the grant store.
- **Relationships app** — the connection graph: agent groups, who-relates-to-whom, external-agent ↔ instance connections. Backend: `relationships.py` `RelationshipManager` (seed exists).

The consent loop is the **writer** that touches all three (identity + relationship edge + permission grant); each app is a distinct management/viewing surface.

## 5. A2A as the unified comms backend (commit-all, phased migration)

**Target (Jay): A2A is taOS's *entire* comms backend** — chat, `channel_hub`, and the Messages app all migrate onto it. One substrate ⇒ every message carries an authenticated identity, governed by the trust layer, audited once.

### Content model (the enabling change)
Messages become structured **parts**: `{parts: [{type: text|image|file|audio|video, text? | blob_ref?, mime, name, size}]}`. Media is **never inline** — parts carry a `blob_ref`; the bus carries refs + metadata, not bytes. The `blob_ref` resolves to a file in the recipient agent's **workspace attachment folder** (see §6 — workspace-canonical, symlinked into the taOSmd store for indexing). (Also serves `project_tinyagentos_chat_canvas`.)

### channel_hub → edge connectors
External channels (WhatsApp/Telegram/Discord) stop being a parallel backend and become **edge connectors** that bridge in/out of the canonical A2A bus, translating attachments to/from blob refs.

### Migration is phased even though the target is total
Canonical bus + content model first; migrate agent-comms + new attachments, then chat, then bridge channel_hub, then Messages. No flag-day.

## 6. Multimodal memory (configured, hardware-gated; describe-then-index base)

Per Jay: **a configured option in taOSmd/memory settings, OFF by default, enabled only if capable hardware/model is present; model-selectable; on-demand or scheduled-bulk.** Two composable layers:

- **Base — describe-then-index (universal):** a media item lives in the agent's **workspace attachment folder** (§6); a **separate analyzer model** (cloud or local — vision for images, ASR for audio→transcript) writes a **text description/transcript alongside** it. That text is ingested into the existing text/vector memory (qmd `/embed` + `/vsearch`) and **points back** to the original file (resolved via the workspace→store symlink). Works with any text embedder; local-friendly; lossy but re-analyzable. **Small vision models run on the Pi**, so this works on-device, not only on GPU workers.
- **Upgrade — native multimodal embedding (optional, pluggable):** when enabled + hardware present, embed the item directly into a shared vector space. Cloud: Cohere Embed v4 / Voyage Multimodal 3.5 / Gemini / Jina Embeddings v4. Local: small vision models on the Pi, or SigLIP 2 / Nomic Embed Vision (shares space with `nomic-embed-text`) / Jina-CLIP-v2 on a GPU worker via qmd. Selected per recipe/hardware. Composes with the base (file + description + multimodal vector).

### Context logging + agent access (Jay, required)
- Every media item **logs its relationship/context**: which chat/DM/message it came from, when, from whom — so an agent can look back, retrieve the original, and use **its own multimodal capability** to view it.
- **Agents need a pathway to the storage bucket** to fetch raw bytes (not just the description).

### Storage location — workspace-canonical, symlinked into the store (Jay, decided)
**The agent's workspace is canonical** (it's inside the agent's container, where **storage quotas** are enforced). If files lived only in the taOSmd store the agent's quota wouldn't account for them — so we go the other way: **symlink the agent's workspace folder (from its container) *into* the taOSmd store**, so taOSmd can read/index/describe/embed the files without owning the bytes. The agent reads its files directly + locally (its own multimodal just opens them); the store references them through the symlink.

- **Chat attachments land in a dedicated folder inside the agent's workspace** (e.g. `workspace/attachments/…`, optionally per-chat subfolders), then symlinked into the store with the rest.
- Implementation: agents run in containers, so the workspace is a host-accessible bind-mount/volume; taOSmd (on the host) symlinks into that volume — no byte-copying.
- **A2A attachment delivery (§5 reconciled):** an attachment sent to an agent is delivered into the recipient's `workspace/attachments/` (counted against *its* quota); the message's `blob_ref` resolves to that workspace attachment; the store indexes it via the symlink. Shared/project files use the existing git-keyed project-shelf mechanism, not per-workspace.
- Access stays **permission-gated** (the Permissions layer + file-access notifications govern who/what may read a given path).

## 7. Standalone vs taOS-managed flag

@taOSmd adds `managed_by = standalone | taos` to taosmd config:
- **standalone** → auth/permissions managed in the terminal or taosmd's web dashboard (taosmd's domain). Default.
- **taos** → auth/permission UX lives primarily in the taOS app (consent + the three apps). taosmd defers accept/deny + permission surface to taOS.

**taOS-side signal:** when taOS provisions/owns a taosmd instance, it **writes `managed_by=taos`** (+ `registry_url` + the auth-request endpoint base) into the taosmd config at deploy time. Optional runtime handshake to confirm.

### 7a. Dashboard gating (managed_by-driven)
The same flag decides whether taosmd serves its **own web dashboard**:
- **standalone** → serve the dashboard (it's the only UI).
- **taos** → dashboard OFF by default; taosmd serves only the HTTP/MCP **data APIs** and the taOS apps render everything. Override: a taosmd config key `serve_dashboard=true` + a toggle in the taOS Memory/taOSmd app.

**View → app ownership** (so nothing is lost when the dash is off): memory browser → Memory/taOSmd app; projects/shelves → Projects app (+ Memory shelves view); recipes/config-profiles → Memory/taOSmd app (framework-manager surface); A2A channels → Messages/Comms app (post §5 migration); agent stats + observability → the Observability/Benchmarking app; permissions/relationships → the new Permissions + Relationships apps. taosmd side: add the config key + skip mounting `/ui` when off (data APIs stay up). taOS side: ensure the apps cover the full dash info-set before defaulting it off in managed mode.

## 8. Provisioning note

Switch taOS's taosmd dependency from the git-clone/editable path to **`pip install taosmd==0.3.0`** (PyPI, Trusted Publishing) — `pyproject.toml` + `install-server.sh` lists. `@jaylfc/qmd` npm stays pinned 2.6.0 (separate — the embedding serve).

## 9. Proposed build sequencing

Foundation (done): registry #710, RelationshipManager, auth-context, governance lifecycle, revocation/inactive feed.

1. **Consent loop + desktop notification** (the spine): request/status endpoints, the non-dismissable per-scope-toggle notification, accept→mint→relationship→grant, identity-only token issuance, `managed_by` flag. *Makes registry-auth enforceable + onboards external agents — highest leverage.* Collaborative with @taOSmd (token + feed).
2. **The three apps + tiered permissions**: Permissions app (grants/tiers + enforcement hooks on memory/bus), Relationships app (graph UI on the existing backend), Users app/Settings (multi-user). Per-app permissions (#622) folds in here.
3. **A2A unified comms + attachment storage**: the parts+blob-ref content model + the **workspace attachment folders symlinked into the taOSmd store** (shared infra for §5 *and* §6), migrate chat → A2A, bridge channel_hub as edge connectors, then Messages.
4. **Multimodal memory**: describe-then-index analyzer pipeline over the workspace attachments + the optional native-multimodal-embedding config (hardware-gated, model-selectable, on-demand/scheduled-bulk) + context logging.

Rationale: the **workspace-attachment storage (symlinked into the store) is shared** by comms-attachments (3) and multimodal-memory (4), so it's built once in phase 3 and phase 4 rides it. Consent loop (1) gates real enforcement; the apps (2) manage what it creates.

## 10. Decisions + open questions

**Resolved (Jay, 2026-06-09):**
- **Token model** (§2): identity-only token + all tiering in the permission store/feed. ✅ (Confirm final enforcement detail with @taOSmd.)
- **Media storage location** (§6): workspace-canonical (quota-enforced) + symlink workspace → taOSmd store; chat attachments in a dedicated workspace folder. ✅
- **Pi vision**: small vision models DO run on the Pi, so describe-then-index (and light native vision) can run on-device — the Pi tier is *not* limited to text-only. Heavier native multimodal embedding still prefers a GPU worker/cloud, selected per recipe/hardware. ✅

**Still open:**
- **Agent↔agent consent**: does cross-agent collaboration always need user consent, or auto-allow within the same group/project? (Lean: consent across user/project boundaries; auto within a shared group.)
- **Final token-enforcement detail** with @taOSmd (how the bus joins identity-token + permission-feed per request).
- **Quota mechanics**: how workspace quotas are measured/enforced across the symlink (the store must not double-count; quota is the workspace's).
