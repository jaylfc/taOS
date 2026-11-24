# Parity: native Messages (taOStalk chat)

Source of truth: the web app at origin/dev. Paths below are relative to
`desktop/src/apps/` unless they start with `tinyagentos/`. `index.tsx` is
`MessagesApp/index.tsx`. Status: **first cut** (in the first commit), **later**
(planned, in this branch after the first cut), **n/a** (not applicable to a native
desktop app, or belongs to a different native app), **beyond** (the web app does not
do it). Code: `native/apps/messages/` (QML app and C++ engine), `native/taos-client/src/chat.rs`.

Transport facts the engine is built on:

* The live socket is `WS /ws/chat` (`tinyagentos/routes/chat.py:126`). It authenticates
  with the `taos_session` cookie only (`chat.py:129`, a bearer token is not read) and a
  refused handshake is HTTP 403 before accept (`chat.py:138`). The server fans a frame out
  only to sockets that sent `{"type":"join","channel_id"}` (`tinyagentos/chat/hub.py`
  `join`/`broadcast`), so the web app joins the SELECTED channel only (`index.tsx:1596-1630`)
  and re-joins after a reconnect (`index.tsx:1185`). Native does the same.
* Phase-2a typing frames (`chat.py:1021`) carry no `channel_id`, so native only shows them
  while the channel they were joined for is the open one (same constraint as the web app).
* Sending: the web app sends plain text over the socket and falls back to
  `POST /api/chat/messages` (`index.tsx:1890-1912`). Native always uses the POST (one path,
  returns the stored message, works while the socket is reconnecting); the broadcast of the
  same message arrives over the socket too and is de-duplicated by id.

## Channel list

| Web feature | Status | Route / event / code |
|---|---|---|
| Channel list `GET /api/chat/channels` (+ `?project_id=`), each with `last_preview`, `last_preview_author` | first cut | `chat.py:575-593`, `index.tsx:1011-1030` |
| Unread counts `GET /api/chat/unread` -> `{unread:{id:n}}` | first cut | `chat.py:894`, `index.tsx:1014` |
| Sections: Channels (topic + group), Agents-DMs, Direct Messages, Remote (`dm-remote`, only when present) | first cut | `index.tsx:2258-2264` |
| Agent DM bucketing live / suspended / archived (presence dot), orphan agent DMs archived only once agent lists loaded | first cut | `MessagesApp.agentSections.ts:1-225`, `index.tsx:2167` (ported 1:1 with its tests) |
| A2A coordination channels (`settings.kind == "a2a"`) stay under Direct Messages | first cut | `MessagesApp.agentSections.ts:176-179` |
| Presence dot live / working / idle; working from thinking frames | first cut | `buildAgentPresence`, `MessagesApp.agentSections.ts:88-110`; `index.tsx:2271-2284` |
| Collapse a section; unread or selected channels stay visible when collapsed | first cut | `index.tsx:1725-1736` |
| Unread badge per row, bumped by a live message in a non-open channel | first cut (beyond for non-open channels: native refreshes `/api/chat/unread` on a 20 s timer, on window activation and on reconnect, because the web app only hears the joined channel) | `index.tsx:1229-1240` |
| Row preview line and last-activity time | first cut | `index.tsx:116-120` (`last_preview`) |
| Project grouping of channels (standalone mode) | later | `index.tsx:2232-2248`, `GET /api/projects` |
| Project-scoped Messages (`scope.projectId`) | later (`--project <id>`) | `index.tsx:1011` |
| Archived channels section, restore, permanent delete | later | `GET /api/chat/channels?archived=true` `index.tsx:1047`; restore `index.tsx:2032`; delete `DELETE /api/chat/channels/{id}` `index.tsx:2055` |
| Create channel / DM / group | later | `POST /api/chat/channels` `index.tsx:2001`, `2867` |
| Channel settings panel (topic, members, mute, ephemeral TTL, archive) | later | `ChannelSettingsPanel.tsx`, `routes/chat_admin.py` |
| Channel quick switcher (Ctrl+K) | first cut | `ChannelSwitcher.tsx`, `index.tsx:1759-1762` |
| Connect wizard (external channels: Discord, Telegram...) | n/a (belongs to the Channels/Connectors app) | `ConnectWizard.tsx` |
| A2A bus viewer (`useBusChannels`, `A2aBusMessageView`) | later (A2A threads view) | `A2aBusPanel.tsx`, `index.tsx:911-914`, `2342` |
| Mobile layout (`MobileMessages`, `MobileChatList`) | n/a (desktop app; the window is responsive down to 360 px wide with a single pane) | `MessagesApp/MobileMessages.tsx` |

## Conversation

| Web feature | Status | Route / event / code |
|---|---|---|
| Load newest 50 messages `GET /api/chat/channels/{id}/messages?limit=50`, ascending | first cut | `chat.py:608-619`, `message_store.py:254-274`, `index.tsx:1103` |
| Older history (`before=<created_at seconds>`) | first cut (beyond: the web app loads 50 and stops; native pages up on scroll-to-top) | `chat.py:609-611` |
| Authors: user vs agent, `author_type`, name colour, avatar initial | first cut (clean transcript, no bubbles) | `MessageList.tsx:46-64`, `MessageAvatar.tsx` |
| Run grouping: same author and type, same local day, gap <= 5 min | first cut | `MessageList.tsx:65,399-406` |
| Day separators "Today", "Yesterday", "Wed 12 Nov" | first cut | `index.tsx:828-842` |
| Time: "now", "Nm", then clock time | first cut (tabular numerals; absolute time on hover/accessible name) | `index.tsx:268-276`, `toMs` `chat/time.ts:1-12` |
| Timestamps are epoch seconds, `< 1e12` means seconds | first cut | `chat/time.ts:6` |
| Markdown body: GFM subset, images disallowed, links open externally | first cut (own sanitising converter: escaped HTML, bold, italic, strike, inline code, links http/https/mailto, lists, quotes, headings as bold, tables as rows) | `index.tsx:319-375` |
| Fenced code blocks with copy button | first cut | `index.tsx:323-341`, `CopyButton` `MessageList.tsx:1324` |
| Long-message handling | beyond (collapsed past 24 lines or 1800 characters, "Show more") | n/a |
| `content_blocks`: text | first cut | `index.tsx:284-291` |
| `content_blocks`: thinking (collapsed by default, `collapsed:false` opens) | first cut | `index.tsx:344-380` |
| `content_blocks`: tool_call (`name`, `input_preview`, `status`, `result_preview`) | first cut (compact card; later: full trace view) | `index.tsx:299-302`, `ToolCallBlock` |
| `content_blocks`: status, question (`options[]`) | first cut (card; options as read-only chips) | `index.tsx:303-310` |
| `content_blocks`: decision (`decision_id`, answer through `POST /api/decisions/{id}/answer`) | later | `index.tsx:512-620` |
| Unknown block kind shows "unsupported block: kind" | first cut | `index.tsx:311-316` |
| Streaming agent replies: `message` (state streaming/pending), `message_delta` (`message_id`, `delta`), `message_state` (`complete`/`error`) | first cut | `index.tsx:1217-1283`, `chat.py:500-573` |
| Pending / streaming / error state marks on a message ("..." , streaming cursor, error line) | first cut | `MessageList.tsx:974-986` |
| `message_edit` (content, edited_at, metadata, content_blocks), "(edited)" mark | first cut (display); editing own message: later | `index.tsx:1305-1321`, `chat.py:717` |
| `message_delete` soft delete, tombstone row | first cut (display); delete action later | `index.tsx:1323-1333`, `MessageTombstone.tsx`, `chat.py:827` |
| Edit own message (`PATCH /api/chat/messages/{id}`; agent reply `PATCH .../agent-reply`) | later (no Up-arrow shortcut: the web app has none, `MessageInput.tsx` only handles Enter) | `chat.py:717,775`, `index.tsx:2076-2086` |
| Delete own message (`DELETE /api/chat/messages/{id}`), confirm | later | `chat.py:827`, `index.tsx:2088-2096` |
| Reactions (`reaction_update`, `POST/DELETE /api/chat/messages/{id}/reactions`) | later (display of `reactions` map in first cut) | `chat.py:948-993`, `index.tsx:1297-1304` |
| Pins (`GET /api/chat/channels/{id}/pins`, `POST/DELETE .../messages/{id}/pin`) | later | `chat.py:658-715`, `index.tsx:1667-1672` |
| Mark unread from a message (`POST .../read-cursor/rewind`) | later | `chat.py:906`, `index.tsx:2127-2135` |
| New-messages divider at the first unread | first cut | `index.tsx:1108-1117` |
| Scroll-to-bottom affordance with count of new messages while scrolled away | first cut | `index.tsx:1687-1722` |
| Auto-scroll only while near the bottom (< 60 px) | first cut | `index.tsx:1708-1716` |
| Deep link `?msg=<id>` scroll and highlight | later (`--message <id>`) | `index.tsx:1646-1665` |
| Canvas messages (`content_type: "canvas"`, `metadata.canvas_url`) | later (shows a titled link row in first cut) | `MessageList.tsx:632-660` |
| Copy text, copy link | first cut (copy text); copy link later | `index.tsx:2098-2110` |
| Empty state (a2a channel with no messages shows a hint) | first cut | `index.tsx:2580` |
| Loading skeletons, error state with retry | beyond (first cut) | n/a |
| Human typing frame `{type:"typing", kind:"human", slug}` expires in 3.5 s | first cut | `index.tsx:1193-1197` |
| Agent thinking frame `{type:"thinking", slug, state:"start"/"end", phase, detail}` | first cut | `index.tsx:1198-1215`, `chat.py:1029-1087`, `TypingFooter.tsx:1-81` |
| Legacy typing frame `{type:"typing", user_id, user_type:"agent"}` expires in 5 s | first cut | `index.tsx:1284-1296` |
| Typing footer, polite live region | first cut | `TypingFooter.tsx:21-45` |
| Stall watch: armed on send in an agent DM or on `@agent` mention, hint at 20 s, warning at 75 s, cleared by `message_state` complete/error | later (pure logic ported with tests in the first cut) | `MessagesApp.stallWatch.ts:1-75`, `index.tsx:1775-1800` |
| Receipts: `GET /api/chat/messages/{id}/receipts` for own messages, `receipt` frame, ticks sent/delivered/seen | later | `chat.py:1099-1130`, `index.tsx:1125-1141`, `1334-1355`, `MessageList.tsx:67-110` |
| `PATCH /api/chat/receipts` mark seen | later | `chat.py:1131`, `index.tsx:1144-1157` |
| Threads: reply count indicator, thread panel, `GET .../threads/{parent}/messages`, all-threads list | later | `chat.py:634-656`, `ThreadPanel.tsx`, `AllThreadsList.tsx`, `index.tsx:1749,2793` |
| Message search `GET /api/chat/search?q=&channel_id=` (min 2 chars) | later | `chat.py:855-892`, `SearchPanel.tsx` |
| Server-side author state (active / archived / removed agent author) | first cut | `index.tsx:150-171` |

## Composer

| Web feature | Status | Route / event / code |
|---|---|---|
| Multi-line input, Enter sends, Shift+Enter newline | first cut | `MessageInput.tsx:159` |
| Draft per channel kept while switching | first cut (in memory; later: persisted) | `index.tsx:818-824,1600-1618` |
| Send: `POST /api/chat/messages {channel_id, author_id:"user", author_type:"user", content, content_type:"text"}` returns the message | first cut | `chat.py:320-498`, `index.tsx:1896-1906` |
| Optimistic send, failure state and retry | beyond (first cut) | n/a |
| Send error line from `{"error": ...}` (400 slash guard, attachment errors) | first cut | `chat.py:34-62,371-374`, `index.tsx:1862-1868` |
| `@mention` autocomplete: channel members except `user`, plus `all`; prefix matches first then substring, at most 6; Up/Down/Enter/Tab/Escape | first cut | `index.tsx:2190-2218` |
| Slash commands: DM with a leading `/`, group needs `@agent /`; list from `GET /api/frameworks/slash-commands` | later (`/help` and server-guarded commands work in first cut because the text is sent as is) | `index.tsx:2176-2188`, `1677-1682`, `SlashMenu.tsx`; server `chat.py:34-62`, `/help` `chat.py:361-383` |
| `/help` returns `{ok, handled:"help", system_message}`, not a message | first cut (composer clears, the broadcast shows the system message) | `chat.py:361-383`, `index.tsx:1870-1883` |
| Typing indicator out (throttled 3 s) | first cut (over `POST /api/chat/channels/{id}/typing`, no socket write needed; the web uses a socket frame `index.tsx:1949-1952` plus the hook) | `chat.py:1007-1027` |
| Attachments: upload `POST /api/chat/upload`, from path `POST /api/chat/attachments/from-path`, send with `attachments[]`, max 10 | later | `lib/chat-attachments-api.ts:22,40`, `chat.py:385-407`, `index.tsx:1818-1850` |
| Attachment display (gallery, lightbox) | later | `AttachmentGallery.tsx`, `AttachmentLightbox.tsx` |
| Suggested prompts (first chat) | later | `first-chat.tsx:111-126` |
| First-chat flow: wait for the new agent DM to appear (`GET /api/agents/{a}/deploy-status`), open it once | later (`--agent <name>` opens an existing DM in the first cut) | `first-chat.tsx:25-110`, `index.tsx:1041` |
| Prefill from admin prompt (`taos:open-messages` event with `prefillPromptName`) | later | `index.tsx:1551-1580`, `GET /api/admin-prompts/{name}` |
| Cross-app open: `taos:open-messages {channelId}` | first cut (`--channel <id>`, `--agent <name>`, second launch switches) | `index.tsx:1551-1592` |
| Browser notifications for background channels | n/a (the shell owns desktop notifications; the controller's notification rows already exist) | `chat/useChatNotifications.ts` |

## Connection and data freshness

| Web feature | Status | Route / event / code |
|---|---|---|
| Reconnect with backoff: 1 s doubling to 30 s plus jitter, give up after 20 tries | first cut (native: 0.5 s doubling to 30 s, equal jitter, never gives up while signed in; a 401/403 handshake means sign-in required and waits for credentials) | `index.tsx:1359-1378`; `taos-client` `events::Backoff` |
| Rejoin the selected channel after reconnect, refetch messages | first cut (beyond: the web app does not refetch after a gap; native refetches the open conversation and the channel list on every reconnect so nothing is lost) | `index.tsx:1181-1186` |
| Connection banner while the controller is unreachable | first cut | `TaosWindow.connectionText` |
| Agent lists `GET /api/agents?include_guests=1` and `GET /api/agents/archived`, refetch on focus (10 s throttle) | first cut | `index.tsx:1066-1099,1425-1455` |
| Current user `GET /auth/me` -> `user.id`, `full_name`, `username` | first cut (`auth.me` op) | `index.tsx:1534-1545` |
| Own message = `author_id` equals the current user id | first cut | `index.tsx:991` |

## Summary

83 rows: 55 in the first cut (of which 6 do more than the web app), 25 later, 3 not applicable.
Rows marked "first cut (display); X later" count as first cut; the later part is listed in the next commits.
