---
title: Store Classification Reference
description: Verified per-store classification of all 79 BaseStore subclasses by Per-User, Shared, and Hot-Path-Write axes, for the Postgres-before-cloud migration assessment.
---

# Store Classification Reference

Single source of truth for the per-store classification that the Postgres-before-cloud
assessment (#2597) needs. It replaces `specs/final_migration_assessment.md`,
`specs/store_inventory_clean.md`, `specs/migration_assessment.md`,
`specs/store_inventory.md` and the root `Postgres_Migration_Assessment.md`,
each of which classified the same 78 stores and disagreed on 37 of them. The
Postgres engine seam already exists on `dev` (`tinyagentos/base_store.py`,
`class Engine(Enum)` with `Engine.POSTGRES`; `_init_postgres` is dispatched at
`base_store.py:49`). This card is design and assessment only -- no migration code,
no driver dependency, and nothing here unblocks onboarding users onto pre-migration
SQLite state.

## Column rules

A row is checkable, not trustable: each column derives from the store's `SCHEMA`
(the `CREATE TABLE` definitions) and write path. Read a column, then confirm it
against the source.

1. **Per-User Data** (`per_user`): `true` iff the schema defines a `user_id`,
   `owner_user_id`, or `created_by_user_id` column. The column's presence means
   rows carry a user association. Nullability does not disqualify; a nullable
   `user_id` still marks rows as user-scoped when set.

2. **Shared Across Users** (`shared`): `true` iff the store is *not* partitioned to
   a single owning user, i.e. when the schema has **no** `user_id`/`owner_user_id`
   column (global namespace), **or** any such column is **nullable** (NULL denotes a
   broadcast/general row), **or** the only user column is a creator stamp
   (`created_by_user_id`), **or** the store has an explicit cross-user sharing
   association table (e.g. `shared_doc_members`). A store with a NOT-NULL
   `user_id`/`owner_user_id` partition key and no sharing table is **not** shared.

3. **Hot-Path Write** (`hot_path`): `true` iff the store is written frequently during
   normal runtime operation -- every conversation turn, event emission, metric,
   notification, token/credential issuance, execution receipt, task-queue or
   heartbeat cycle -- rather than only at first boot, setup, or admin/config time.
   Concurrency-handling code (`asyncio.Lock`, `BEGIN IMMEDIATE`, `create_task`-driven
   writes, conditional `UPDATE ... WHERE` on shared rows) *corroborates* but does not
   by itself define hot-path status.

> Key tension this resolves: a row can be both `per_user=true` and `shared=true`.
A nullable `user_id` is per-user on set rows and broadcast on NULL rows; a
`created_by_user_id` creator stamp is per-user in provenance but not a tenant
partition; an explicit sharing table is owned yet shared. These four stores are the
hybrids: `NotificationStore`, `ReceiptStore`, `SharedDocsStore`, `UserSharesStore`.

## Credential-bearing stores (handle first)

These stores persist security material. Moving any of them to Postgres is blocked
on secret/key-management continuity -- this assessment classifies them but does
**not** decide whether they migrate (the two shipped drafts answered that twice,
differently, for `AgentTokensStore` and `AuthRequestsStore`). The classification
below is the resolved, schema-grounded triple for each.

| Store | File | Per-User | Shared | Hot-Path Write | Material |
|-------|------|----------|--------|----------------|----------|
| AgentTokensStore | `tinyagentos/agent_tokens_store.py` | false | true | true | Persists plaintext agent tokens (column `token`). |
| AuthRequestsStore | `tinyagentos/auth_requests_store.py` | false | true | false | Persists plaintext auth-request tokens (column `token`); both shipped drafts classify this store differently. |
| GitHubIdentitiesStore | `tinyagentos/github_identities.py` | false | true | false | Persists plaintext OAuth tokens (column `token`). |
| MailAccountStore | `tinyagentos/mail_store.py` | true | false | false | Persists mail credentials (columns `secret_name`, `username`). |
| NotificationPushStore | `tinyagentos/notifications_push.py` | true | false | false | Persists VAPID/subscription keys (columns `p256dh`, `auth`). |
| SecretsStore | `tinyagentos/secrets.py` | false | true | false | Persists plaintext secret values (column `value`). |

Two additional stores persist only **hashed** key/secret material (non-reversible),
so exposure does not yield a usable credential, but they are part of the key-
management surface and should be migrated with the secret stores above:

| Store | File | Column | Per-User | Shared | Hot-Path Write |
|-------|------|--------|----------|--------|----------------|
| AgentModelKeyStore | `tinyagentos/agent_model_key_store.py` | `key_hash` | false | true | false |
| PasswordResetStore | `tinyagentos/password_reset_store.py` | `token_hash` | true | false | false |

## Classification table (79 stores)

Row count == store count. Do not trust a headline number; it is the count of rows
in this table. A reviewer can diff this table's `Store` column against the live
class set and get an empty result both directions:

```
git grep -hoE 'class +\w+\(BaseStore[,)]' -- tinyagentos |
  sed -E 's/^class +(\w+)\(BaseStore[,)].*/\1/' | sort > /tmp/classes.txt
# diff /tmp/classes.txt against the Store column of this table -- expect empty diff
```

Equivalently, the store count must equal the live class count (asserted, not hardcoded):

```
test "$(git grep -hoE 'class +\w+\(BaseStore[,)]' -- tinyagentos | wc -l)" = "$(grep -cE '^\| [0-9]+ \|' docs/design/store-classification-reference.md)"
```

| # | Store | File | Per-User Data | Shared Across Users | Hot-Path Write | Notes |
|---|-------|------|---------------|---------------------|----------------|-------|
| 1 | AgentGrantsStore | `tinyagentos/agent_grants_store.py` | false | true | true | No `user_id` column; uses `asyncio.Lock` (_write_lock). System-level grants. |
| 2 | AgentMessageStore | `tinyagentos/agent_messages.py` | false | true | true | No `user_id`; `from_agent`/`to_agent`. Writes every LLM message. |
| 3 | AgentModelKeyStore | `tinyagentos/agent_model_key_store.py` | false | true | false | `issuing_user` column (not `user_id`). Infrequent admin writes. |
| 4 | AgentRegistryStore | `tinyagentos/agent_registry_store.py` | true | false | true | `user_id TEXT NOT NULL DEFAULT ''`; `asyncio.Lock` (_reporting_lock). Runtime agent lifecycle. |
| 5 | AgentScopeRequestsStore | `tinyagentos/agent_scope_requests_store.py` | false | true | false | No `user_id`. Admin request flow. |
| 6 | AgentTokensStore | `tinyagentos/agent_tokens_store.py` | false | true | true | No `user_id`; `asyncio.Lock` + `BEGIN IMMEDIATE`. Token issuance at runtime. |
| 7 | AppGrantsStore | `tinyagentos/app_grants_store.py` | true | false | false | `user_id TEXT NOT NULL`. Admin grant flow. |
| 8 | AuthRequestsStore | `tinyagentos/auth_requests_store.py` | false | true | false | No `user_id`. Short-lived auth state. |
| 9 | BoardAuditLog | `tinyagentos/board_audit.py` | false | true | false | No `user_id`. Audit-only; append path infrequent. |
| 10 | BrokerStore | `tinyagentos/broker/store.py` | false | true | false | No `user_id`. Grant-scoped. |
| 11 | CanvasStore | `tinyagentos/chat/canvas.py` | false | true | false | No `user_id`. Project-scoped canvas. |
| 12 | CapabilityMap | `tinyagentos/cluster/capability_map.py` | false | true | false | No `user_id`. Cluster topology; infrequent updates. |
| 13 | ChannelStore | `tinyagentos/channels.py` | false | true | false | No `user_id`. System channel config. |
| 14 | ChatChannelStore | `tinyagentos/chat/channel_store.py` | true | false | false | `chat_read_positions.user_id TEXT NOT NULL`. Channel membership is per-user. |
| 15 | ChatMessageStore | `tinyagentos/chat/message_store.py` | false | true | true | No `user_id`; `author_id`/`author_type`. Messages are hot-path. Uses `asyncio.Lock` (_reaction_lock). |
| 16 | ClientLogStore | `tinyagentos/client_log_store.py` | true | false | true | `user_id TEXT NOT NULL`. High-frequency client logging. |
| 17 | ClusterPairingStore | `tinyagentos/cluster/pairing_store.py` | false | true | false | No `user_id`. Setup-time only. |
| 18 | CodingSessionStore | `tinyagentos/coding_sessions/store.py` | false | true | false | No `user_id`. Session lifecycle. |
| 19 | CodingWorkspaceStore | `tinyagentos/coding_workspaces.py` | false | true | false | No `user_id`. Workspace config. |
| 20 | ContactsStore | `tinyagentos/contacts_store.py` | false | true | false | No `user_id`; has sharing-association patterns. Hub-level contacts. |
| 21 | ConversionManager | `tinyagentos/conversion.py` | false | true | false | No `user_id`. Background conversion jobs. |
| 22 | DecisionStore | `tinyagentos/decisions/decision_store.py` | true | false | false | `user_id TEXT NOT NULL DEFAULT ''`. Per-agent decision tracking; not high-frequency. |
| 23 | DesignStore | `tinyagentos/design_docs.py` | false | true | false | No `user_id`. Project-scoped designs. |
| 24 | DesktopSettingsStore | `tinyagentos/desktop_settings.py` | true | false | false | `user_id TEXT NOT NULL` (PK). Per-user preferences. |
| 25 | DevicePairRequestsStore | `tinyagentos/device_pair_requests_store.py` | false | true | false | No `user_id`; `asyncio.Lock`. Pairing-time flow only. |
| 26 | DeviceStore | `tinyagentos/device_store.py` | true | false | false | `user_id TEXT NOT NULL`. Device registration; low write frequency. |
| 27 | DocReviewStore | `tinyagentos/projects/doc_review_store.py` | false | true | false | No `user_id`. Project-scoped reviews. |
| 28 | ExpertAgentStore | `tinyagentos/expert_agents.py` | false | true | false | No `user_id`. System-registered expert agents. |
| 29 | ExecutionPolicyStore | `tinyagentos/governance/policy_store.py` | false | true | false | No `user_id`. System policy; admin-time only. |
| 30 | FeedbackStore | `tinyagentos/feedback_store.py` | true | false | false | `user_id TEXT NOT NULL`. User feedback submissions. |
| 31 | GitHubIdentitiesStore | `tinyagentos/github_identities.py` | false | true | false | No `user_id`. System auth state. |
| 32 | HubStore | `tinyagentos/hub/store.py` | false | true | false | No `user_id`. Hub social graph. |
| 33 | InstallRegistryStore | `tinyagentos/install_registry.py` | false | true | false | No `user_id`. Installation tracking; low write frequency. |
| 34 | InstalledAppsStore | `tinyagentos/installed_apps.py` | false | true | false | No `user_id`. System app catalog. |
| 35 | KnowledgeStore | `tinyagentos/knowledge_store.py` | true | false | false | `user_id TEXT NOT NULL DEFAULT ''`. Per-user knowledge bases. |
| 36 | LibraryStore | `tinyagentos/library_store.py` | false | true | false | No `user_id`. Shared library content. |
| 37 | LicenseAcceptancesStore | `tinyagentos/license_acceptances_store.py` | true | false | false | `user_id TEXT NOT NULL`. License acceptance tracking. |
| 38 | LoraStore | `tinyagentos/lora_store.py` | false | true | false | No `user_id`. System ML model store. |
| 39 | MCPServerStore | `tinyagentos/mcp/registry.py` | false | true | false | No `user_id`. System MCP registry. |
| 40 | MailAccountStore | `tinyagentos/mail_store.py` | true | false | false | `user_id TEXT NOT NULL DEFAULT ''`. Per-user mail accounts. |
| 41 | MemberStore | `tinyagentos/council/member_store.py` | false | true | false | No `user_id`. Council member config. |
| 42 | MetricsStore | `tinyagentos/metrics.py` | false | true | true | No `user_id`. High-frequency metrics aggregation. |
| 43 | NotificationPushStore | `tinyagentos/notifications_push.py` | true | false | false | `user_id TEXT NOT NULL`. Push subscription per user. |
| 44 | NotificationStore | `tinyagentos/notifications.py` | true | true | true | `user_id TEXT` (**nullable** — NULL means general/broadcast notification). High-frequency runtime. Uses `create_task`. |
| 45 | OfficeDocStore | `tinyagentos/office_docs.py` | false | true | false | No `user_id`. Project-scoped docs. |
| 46 | PasswordResetStore | `tinyagentos/password_reset_store.py` | true | false | false | `user_id TEXT NOT NULL`. Temporary reset requests. |
| 47 | PeerOutboxStore | `tinyagentos/chat/peer_outbox.py` | false | true | false | No `user_id`. Peer delivery queue. |
| 48 | ProjectCanvasStore | `tinyagentos/projects/canvas/store.py` | false | true | false | No `user_id`. Project-scoped canvas elements. |
| 49 | ProjectElementStore | `tinyagentos/projects/element_store.py` | false | true | false | No `user_id`. Project-scoped elements. |
| 50 | ProjectInviteStore | `tinyagentos/projects/invite_store.py` | false | true | false | No `user_id`. Project invite tokens. |
| 51 | ProjectListEntriesStore | `tinyagentos/projects/lists_store.py` | false | true | false | No `user_id`. Project-scoped list entries. |
| 52 | ProjectListsStore | `tinyagentos/projects/lists_store.py` | false | true | false | No `user_id`. Project-scoped list metadata. |
| 53 | ProjectNotesStore | `tinyagentos/projects/notes_store.py` | false | true | false | No `user_id`. Project-scoped notes. |
| 54 | ProjectStore | `tinyagentos/projects/project_store.py` | true | false | false | `user_id TEXT NOT NULL DEFAULT ''`. Per-user project ownership. |
| 55 | ProjectTaskStore | `tinyagentos/projects/task_store.py` | false | true | true | No `user_id`. Uses `create_task` for async writes. Task queue hot-path. |
| 56 | ReceiptStore | `tinyagentos/receipt_store.py` | true | true | true | `created_by_user_id TEXT NOT NULL DEFAULT ''`. Runtime execution receipts; high volume. |
| 57 | RelationshipManager | `tinyagentos/relationships.py` | false | true | false | No `user_id`. Agent group relationships. |
| 58 | RoleRegistry | `tinyagentos/council/role_registry.py` | false | true | false | No `user_id`. Council role definitions. |
| 59 | RoutineStore | `tinyagentos/projects/routines_store.py` | false | true | false | No `user_id`. Project-scoped routines. |
| 60 | SecretsStore | `tinyagentos/secrets.py` | false | true | false | No `user_id`. System secrets — **credential-bearing**. |
| 61 | SharedDocsStore | `tinyagentos/notes/shared_docs_store.py` | true | true | false | `owner_user_id TEXT NOT NULL` + explicit `shared_doc_members` table. Hybrid: per-user ownership + cross-user sharing. |
| 62 | SharedFolderManager | `tinyagentos/shared_folders.py` | false | true | false | No `user_id`. System folder sharing config. |
| 63 | SkillStore | `tinyagentos/skills.py` | false | true | false | No `user_id`. System skill catalog. |
| 64 | SongStore | `tinyagentos/music_songs.py` | false | true | false | No `user_id`. System music catalog. |
| 65 | StoreSubmissionStore | `tinyagentos/store_submissions.py` | false | true | false | No `user_id`. Store app submission queue. |
| 66 | StreamingSessionStore | `tinyagentos/streaming.py` | false | true | false | No `user_id`. System session tracking. |
| 67 | StrikeStore | `tinyagentos/projects/strike_store.py` | false | true | false | No `user_id`. Project-scoped task strikes. |
| 68 | SystemEventStore | `tinyagentos/events/store.py` | false | true | true | No `user_id`. High-frequency event logging. |
| 69 | TaskScheduler | `tinyagentos/scheduler/task_scheduler.py` | false | true | false | No `user_id`. Cron-style scheduling; low write frequency. |
| 70 | ThemeStore | `tinyagentos/themes/store.py` | false | true | false | No `user_id`. System theme catalog. |
| 71 | TodoStore | `tinyagentos/todo/todo_store.py` | true | false | false | `owner_user_id TEXT NOT NULL`. Per-user todo lists. |
| 72 | TrainingManager | `tinyagentos/training.py` | false | true | true | No `user_id`. Background training job lifecycle. |
| 73 | UserMemoryStore | `tinyagentos/user_memory.py` | true | false | false | `user_id TEXT NOT NULL`. Per-user memory chunks. |
| 74 | UserSharesStore | `tinyagentos/user_shares_store.py` | true | true | true | `owner_user_id TEXT NOT NULL` + explicit sharing table. Uses `asyncio.Lock`. Runtime share creation. |
| 75 | UserspaceAppStore | `tinyagentos/userspace/store.py` | false | true | false | No `user_id`. System app registry. |
| 76 | UserspaceDataStore | `tinyagentos/userspace/data_store.py` | false | true | false | No `user_id`. App-scoped KV store. |
| 77 | VideoJobStore | `tinyagentos/video_jobs.py` | false | true | false | No `user_id`. Background video jobs. |
| 78 | WebSiteStore | `tinyagentos/web_sites.py` | false | true | false | No `user_id`. System website catalog. |
| 79 | WorkerRegistryStore | `tinyagentos/cluster/worker_registry_store.py` | false | true | true | No `user_id`. Cluster worker heartbeats/registrations; runtime path. |

## Tallies (derived from the table above)

| Column | true | false |
|--------|------|-------|
| Per-User Data | 20 | 59 |
| Shared Across Users | 63 | 16 |
| Hot-Path Write | 14 | 65 |
| **Total stores** | **79** | |

> The total is the table's row count, verified by the `test` command above against
`git grep`. `ProjectListEntriesStore` (row 51) is included; the prior drafts omitted it
and asserted 78, which is why this document derives the count from the table.

