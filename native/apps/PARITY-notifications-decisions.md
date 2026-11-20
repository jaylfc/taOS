# Parity: native Notifications and Decisions

Specs: `notifications.md` and `decisions.md` (taos-team native specs), including their
"Changed since" and "Native notes" sections. Status: **done**, or **deferred** with the reason.
Code: `native/apps/` (apps `notifications/`, `decisions/`; shared `common/`).

Layers: `NotificationFeed` (C++, `common/notificationfeed.*`, QML type `Taos.Data.NotificationFeed`) is the
store the bar's bell will read. `Taos.Apps` (`common/qml/Taos/Apps`) is the shared QML module: consent card,
sign-in card, glass panels, buttons, fields, motion. Both apps are QML only; they hold no state the
controller would not also hold, apart from unsent text and a short optimistic overlay while a POST is in flight.

## Notifications

| Spec item | Status | Notes |
|---|---|---|
| Pinned rows first (`auth_requests`, `agent_scope_requests`), then newest first | done | `NotificationProxy` sorts; pinned never trimmed |
| 100 cap on the non-pinned remainder | done | per `notification-store.ts` code (the spec text says the whole list); the server also only returns 20 non-pinned rows |
| Row: unread dot, title, 2-line body, relative time, source, dismiss | done | relative time updates every 30 s |
| `GET /api/notifications` (never `hx-request`), non-array = empty | done | foundation `notifications.list` |
| Read, read-all | done | `notifications.mark_read`, `notifications.mark_all_read` |
| Archive with `kept: pending-request` | done | foundation `notifications.archive` -> outcome; a kept row stays, marked read |
| Dismiss: pinned only marked read, others archived and hidden at once | done | optimistic overlay, dropped when the POST fails |
| Clear all keeps pending consent rows | done | `notifications.clear` |
| Merge on refetch ("OR the local read flag") | done, differently | web workaround not copied: the server answer is applied; the overlay lives only until the refetch that follows the POST |
| `notif-N` local-origin rows | not applicable | no local-origin rows; native-raised notices should be `POST /api/notifications` rows |
| Source/target routing table | done | `routeFor`, from `server-notifications.ts` code (explicit source lists), `my.taos.<id>`; unknown = mark read only |
| Row tap opens the target app | done, shell handoff deferred | emits `openAppRequested(appId, props)` and logs `taos: open-app request ...`; the shell does the launch |
| Live updates | done | `LiveQuery` on `notification.added` (one shared `/api/os/events` stream), refetch on every reconnect |
| Paused banner | done | "paused" chip, accessible name "live updates paused"; connection banner when the controller is unreachable |
| Filters by level / source | done, beyond the web app | the web app has none; level pills, source drop-down and an unread toggle |
| Consent block: vocabulary gate, 10 s timeout, Allow disabled until known | done | `ConsentCard`; a Retry button is added |
| Consent: project picker, requested project preselected and labelled, not-found message | done | |
| Consent: inline project create `POST /api/projects {name, slug}` with the web slug function | done | `Logic.slugifyWithFallback` is a port of `slug.ts` |
| Consent: narrow-not-widen diff, Requested and Granted lists, alert on difference | done | |
| Consent: deny reason (when required), lead checkbox, `lead_assigned` check, "lead unconfirmed" terminal state | done | |
| Consent errors inline (`detail`, "Request failed") and refetch after a failure | done | the controller's own sentence is shown; any failure refetches the request lists (409 included) |
| Shows what is granted before Allow (agent, scopes, project or OS-wide, duration) | done | "What approving grants" block |
| Sign-in code entry | done | host allowlist re-checked client side; Open uses `Qt.openUrlExternally` |
| Native system-notification toggle (replaces Web Push) | deferred | needs a shell-owned notification service (freedesktop Notifications / feedbackd) and a place to keep the preference; the web push routes are cookie-only. Nothing is faked in the UI |
| Setup checklist card | deferred | shared shell component (`app-inventory.md` Onboarding); not in the app |
| View history | done as a request | emits `openAppRequested("my.taos.notification-history")`; that app is not built yet |
| Empty state, error state, loading state | done | error state with retry; a failed refresh keeps the last list and says so |
| Screen reader announcement of new pinned requests | partial | rows carry accessible names including "unread"; the toast and error text use the alert role. A dedicated live announcement is deferred (needs a Qt accessibility announce API in the foundation) |
| A11y: dismiss label "Dismiss: {title}", consent group "Consent actions", Allow/Deny names spell out scopes and project, alerts | done | |
| Keyboard: Up/Down between rows, Enter/Space open, Delete dismiss, `M` mark read, Tab through row, dismiss, actions | done | |
| Motion with reduced-motion | done | every duration comes from `Look`, zero when `TaosStyle.reduceMotion` |

## Decisions

| Spec item | Status | Notes |
|---|---|---|
| Three tabs (Pending (N), Archive, Withdrawn) with real tab semantics | done | `TabBar`/`TabButton`, role PageTab; Archive and Withdrawn are fetched only while their tab is open |
| Header, pending badge, "paused" tag | done | |
| Access requests section (chip, framework, claim, quoted reason, scope chips, consent) | done | `GET /api/agents/auth-requests?status=pending` |
| Scope requests section (chip, project, name and id, reason, one checkbox per scope, required deny reason, effective binding) | done | `GET /api/agents/scope-requests?status=pending`; legend "Scopes to grant", labels "Grant {scope}" |
| 409 on approve: refetch, the card disappears | done | any failed consent call refetches the list |
| Decision card: agent badge, Blocking badge, project chip, relative time (mixed formats), question, gate details, context, notes, type label | done | `GateGrantDetails` ported |
| single_select, multi_select, approve_deny, free_text | done | Other row with a textarea (20000), answer note (500), submit rules as the web app |
| Recommended tag and rationale | done | |
| Answer POST with `error ?? detail` text | done | the controller's `{"error"}` sentence is shown verbatim; `source:"native"` is sent, `answered_by` is left empty |
| Add note (500, Enter submits) | done | |
| Archive and Withdrawn cards, lazy supersession history, "No earlier versions." | done | `decisions.history` on first expand |
| States: Loading on first load only, silent refresh, empty states, failed refresh keeps the list | done | |
| Live: `decision.answered`, `decision.note`, `decision.withdrawn`, plus `notification.added`; refetch on window focus | done | `notification.added` is added because the controller emits no event when a decision or request is created |
| Focus moves to the next pending card after an answer | done | |
| Device-bearer phone build hides gate answering | not applicable | the app uses a session; the controller refuses gate answers from a device bearer with its own 409 text, which is shown |
| Typed-but-unsent text is the only local state; nothing cached across launches | done | |
| Desktop master-detail layout | deferred | optional in the spec; single column used on every width |
| Shell badge for the pending count | deferred | spec says "if one is added"; `LiveQuery` count is available to the shell |

## Shared

| Item | Status | Notes |
|---|---|---|
| Foundation API changes | none | `Taos.http` is used for the consent, project, vocabulary and request-list routes, `decisions.list/history` and `notifications.*` ops for the rest |
| Decisions answer through `decisions.answer` op | not used | the op drops `other` when `value` is a string and cannot send `source`; the answer goes through `Taos.http` with the web body plus `source` |
| Inter | partial | declared as the family; not installed on the test host, so screenshots show the fallback face. Bundle the font files with the shell theme |
| Glass | partial | tint, veil, hairlines and a window backdrop are drawn; the blur comes from the compositor and does not render offscreen |
