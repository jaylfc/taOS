# Parity: Activity (`dashboard`, app id `my.taos.dashboard`)

Spec: the internal port spec for `dashboard`. Code: `native/apps/activity/`
(`src/` C++ engine, feed and formatters; `qml/` the face; `tests/`; `screenshots/`).
Build: `cmake -S native/apps/activity -B build -G Ninja -DCMAKE_PREFIX_PATH=<Qt 6.12> -DTAOS_CARGO_OFFLINE=ON`.

Design: `ActivityEngine` and `ModelActivityFeed` are the service layer (controller API through
the foundation `Client`, `Client::http`, `Client::openStream`); QML holds view state only
(scroll, expanded cards, the three feed filters). The graph history is a 60-sample in-memory
view buffer built from the polls; it is never saved and the web app keeps nothing richer.

| Spec item | Status |
|---|---|
| Header: title, system line, Restart AI services (admin only), Refresh | done |
| CPU card: overall %, load average, per-core bars "C{n} {GHz}", live graph | done |
| NPU card (only if type present): freq, TOPS, per-core bars, debugfs hint when no cores | done |
| Memory: usage bar, swap bar if any, ZRAM rows if any, graph | done |
| GPU: one card per GPU the controller reports (`gpus[]`, `gpu.devices[]`, or the single `gpu` block); load and VRAM graphs; "Stats unavailable" | done in the app. The controller reports ONE GPU only, see Gaps |
| Disk: usage, used/total GB, read/write rates, graph | done |
| Thermal: zones in C, hot/warm/normal as colour AND word, graph of hottest | done |
| Network: per-interface rx/tx, aggregate graph | done |
| Loaded Models (n): local rows (host defaults to controller) + every worker backend `loaded_models` (strings or objects); exact empty string | done |
| Cluster workers: refresh button, tier chip, status pill, backend chips, current and latent capability chips, exact empty state; docs link opens in the user's browser | done |
| Worker status from heartbeat age (60 s / 300 s); `status` string when `last_heartbeat` missing (reduced and `cluster_read` tiers) | done |
| Scheduler: counters (errors red, rejected amber), resource tiles, last 8 tasks coloured by status | done |
| Top Processes: pid, user, name, CPU %, RSS | done |
| Model Activity: history seed, SSE merged by `seq` (never replaced), 200 cap, filters (worker, model grow from seen; event types from server), live dot, event labels, duration and tokens with rate, relative time | done |
| Model activity over the session cookie; local token shows "Sign in to see model activity" | done (tested fake and real) |
| First batch: four calls in parallel, only `/api/activity` decides success, others keep last good, error string kept | done |
| Cadence: 2 s batch (tasks `limit=8`), 10 s workers, manual refresh, refresh on window-active | done |
| Pause when hidden (timers stop, stream closed); resume refreshes at once | done (improvement over the web app, per Native notes) |
| States: spinner, "Activity data unavailable" + error, each empty state with the spec strings | done |
| Restart dialog: confirm, running, done (per-unit results, "Restart results" list), error; 403 text "Admin access is required to restart AI services."; else server `error`; else "The restart request failed."; refresh after success | done |
| Phone: one column, collapsible cards, Model Activity and Top Processes as pushed pages with Back | done (below 640 px wide) |
| Accessibility: all spec labels, dialog role, graphs expose a text summary (now, average, peak), bars expose their value as text, no per-poll announcements (only state changes: live/offline, errors, restart result) | done |
| Reduced motion (`TaosStyle.reduceMotion`): graph slide, bar and pulse animations off | done |
| Inter font | set as the family; Inter is not installed on this host, screenshots use the fallback |

## Gaps and notes
* **Multiple GPUs (controller change needed).** `tinyagentos/routes/activity.py:142-148` returns a
  single `gpu` object (VRAM only for nvidia via `system_stats.py:152`, load only for Mali via
  `system_stats.py:269`; `hardware.py` detects one GPU). The btop machine with three GPUs would
  show one here and in the web app. The app already renders N cards from `activity.gpus[]` entries
  shaped `{type, model, load{load_percent,freq_hz}, vram_percent, vram_used_mb, vram_total_mb}`
  (tested with a 3 GPU fixture). The controller must send that list; the app never probes GPUs.
* **Confirmation text.** The web text hardcodes "rkllama and qmd". The route
  (`routes/system.py:244-279`, `_managed_ai_units` at `:216`) restarts `qmd.service` plus every
  store backend with `lifecycle.auto_manage` that is installed on this node. There is no read
  endpoint listing them, so the native text says that exactly (qmd, any other managed backend such
  as rkllama, this node only, controller/desktop/agents/workers untouched). A read endpoint would
  let the dialog name the actual units.
* Restart route and payload are the web app's: `POST /api/system/ai-stack/restart`, no body
  (`desktop/src/apps/ActivityApp.aiStack.tsx:38`).
* Calls use `Client::http` (raw JSON) rather than the typed `dashboard.*` ops, because the typed
  `Activity` struct drops unknown fields (a `gpus` list) and the ops have no model-activity or
  restart entry. The SSE stream is opened with `Client::openStream` from app C++ (QML cannot
  create streams). No foundation API change.
* Per-worker hardware tiles inside the Scheduler card (web-only extra, not in the spec list) are
  deferred; each worker's hardware summary line is on its Cluster tile.
* Glass blur does not render offscreen; the translucent fill, sheen and hairline do. Verify blur on
  taosdesktop.
* Tests: `tst_fmt` (16), `tst_engine` (22, fake controller), `tst_activity_real` (real controller,
  skipped unless `TAOS_REAL_URL`). Screenshots: `screenshots/`, made by `activity-shots`.
