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
Messages become structured **parts**: `{parts: [{type: text|image|file|audio|video, text? | blob_ref?, mime, name, size}]}`. Media is **never inline** — parts carry a `blob_ref` into a content-addressed **blob/bucket store**; the bus carries refs + metadata, the blob store streams bytes. (Also serves `project_tinyagentos_chat_canvas`.)

### channel_hub → edge connectors
External channels (WhatsApp/Telegram/Discord) stop being a parallel backend and become **edge connectors** that bridge in/out of the canonical A2A bus, translating attachments to/from blob refs.

### Migration is phased even though the target is total
Canonical bus + content model first; migrate agent-comms + new attachments, then chat, then bridge channel_hub, then Messages. No flag-day.

## 6. Multimodal memory (configured, hardware-gated; describe-then-index base)

Per Jay: **a configured option in taOSmd/memory settings, OFF by default, enabled only if capable hardware/model is present; model-selectable; on-demand or scheduled-bulk.** Two composable layers:

- **Base — describe-then-index (universal):** a media item lands in a **folder bucket** (the same content-addressed blob store as §5). A **separate analyzer model** (cloud or local — vision for images, ASR for audio→transcript) writes a **text description/transcript alongside** in the bucket. That text is ingested into the existing text/vector memory (qmd `/embed` + `/vsearch`) and **points back** to the original item. Works with any text embedder; local-friendly; lossy but re-analyzable.
- **Upgrade — native multimodal embedding (optional, pluggable):** when enabled + hardware present, embed the item directly into a shared vector space. Cloud: Cohere Embed v4 / Voyage Multimodal 3.5 / Gemini / Jina Embeddings v4. Local (GPU worker, via qmd): SigLIP 2 / Nomic Embed Vision (shares space with `nomic-embed-text`) / Jina-CLIP-v2. Compose with the base (store blob + description + multimodal vector).

### Context logging + agent access (Jay, required)
- Every media item **logs its relationship/context**: which chat/DM/message it came from, when, from whom — so an agent can look back, retrieve the original, and use **its own multimodal capability** to view it.
- **Agents need a pathway to the storage bucket** to fetch raw bytes (not just the description).

### Storage location — memory bucket vs agent workspace (recommendation; confirm with Jay)
The memory folder/bucket lives **outside** an agent's container; the workspace is inside it.
**Recommendation:** the **canonical store is the memory bucket** (shared, content-addressed, dedup), accessed via the **virtual NAS** pathway, **permission-gated** (file-access governed by the Permissions layer + file-access notifications). For an item sent to an agent (e.g. an image in a DM), it is stored canonically in the bucket and a **permissioned symlink/view** is surfaced into that agent's **workspace** so its own multimodal can read the file directly. One canonical store; per-agent permissioned views; access governed by the same consent/permission/notification system. (Open: exact NAS mount semantics + whether symlink or virtual-FS view — flag for Jay.)

## 7. Standalone vs taOS-managed flag

@taOSmd adds `managed_by = standalone | taos` to taosmd config:
- **standalone** → auth/permissions managed in the terminal or taosmd's web dashboard (taosmd's domain). Default.
- **taos** → auth/permission UX lives primarily in the taOS app (consent + the three apps). taosmd defers accept/deny + permission surface to taOS.

**taOS-side signal:** when taOS provisions/owns a taosmd instance, it **writes `managed_by=taos`** (+ `registry_url` + the auth-request endpoint base) into the taosmd config at deploy time. Optional runtime handshake to confirm.

## 8. Provisioning note

Switch taOS's taosmd dependency from the git-clone/editable path to **`pip install taosmd==0.3.0`** (PyPI, Trusted Publishing) — `pyproject.toml` + `install-server.sh` lists. `@jaylfc/qmd` npm stays pinned 2.6.0 (separate — the embedding serve).

## 9. Proposed build sequencing

Foundation (done): registry #710, RelationshipManager, auth-context, governance lifecycle, revocation/inactive feed.

1. **Consent loop + desktop notification** (the spine): request/status endpoints, the non-dismissable per-scope-toggle notification, accept→mint→relationship→grant, identity-only token issuance, `managed_by` flag. *Makes registry-auth enforceable + onboards external agents — highest leverage.* Collaborative with @taOSmd (token + feed).
2. **The three apps + tiered permissions**: Permissions app (grants/tiers + enforcement hooks on memory/bus), Relationships app (graph UI on the existing backend), Users app/Settings (multi-user). Per-app permissions (#622) folds in here.
3. **A2A unified comms + blob store**: the parts+blob-ref content model + the content-addressed **blob/bucket store** (shared infra for §5 *and* §6), migrate chat → A2A, bridge channel_hub as edge connectors, then Messages.
4. **Multimodal memory**: describe-then-index analyzer pipeline on the bucket + the optional native-multimodal-embedding config (hardware-gated, model-selectable, on-demand/scheduled-bulk) + context logging + the workspace/NAS access model.

Rationale: the **blob/bucket store is shared** by comms-attachments (3) and multimodal-memory (4), so it's built once in phase 3 and phase 4 rides it. Consent loop (1) gates real enforcement; the apps (2) manage what it creates.

## 10. Open questions (for Jay / @taOSmd)

- **Token model** (§2): identity-only + permission-store tiering (recommended) vs scope/exp in the token — @taOSmd's call since they enforce.
- **Media storage location** (§6): canonical-bucket + permissioned workspace symlink/view via virtual NAS (recommended) vs workspace-primary — confirm the NAS/FS semantics with Jay.
- **Agent↔agent consent**: does cross-agent collaboration always need user consent, or auto-allow within the same group/project? (Lean: consent across user/project boundaries; auto within a shared group.)
- **Native multimodal on the Pi**: low-power NPU likely can't host SigLIP2/Nomic Vision well → native multimodal runs on a GPU worker or cloud; the Pi-only tier uses describe-then-index. Confirm acceptable.
