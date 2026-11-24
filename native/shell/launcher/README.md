# taos-launcher

The keyboard summoned search panel of the taOS native desktop (Super+Space), and `taos-search`, the same
engine on the command line. Engine first, faces second: `taos_search_core` (providers, ranking, calculator,
desktop entry parser, frecency, IPC) has no GUI in it; the launcher window and the CLI are two faces of it.

## Run

- `taos-launcher daemon` stays resident and hidden (the session supervisor runs it as component
  `taos-launcher`: add it to `TAOS_SHELL_COMPONENTS`). `taos-launcher toggle|show|hide|status` talks to it over
  `$XDG_RUNTIME_DIR/taos/launcher.sock`; `toggle` starts a daemon if none answers. One instance only.
- `taos-search [--json] [--timeout MS] [--limit N] "query"` prints ranked results (sections, actions) for TUI,
  SSH and agent use.
- sway: `sway/48-taos-launcher.conf` (binds only `Mod4+space`; the installer must move the stock
  `$mod+space floating toggle` to `Mod4+Shift+space`).

## Keys

Type to search. Up/Down (Ctrl+P/N) move. Enter runs. Tab or Right arrow (at end of text) shows the secondary
actions; Tab, Left or Escape goes back. Ctrl+1..9 picks a row (numbers show while Ctrl is held). Escape clears
the query, then closes. Power actions ask for a second Enter. Closes on focus loss or a click outside.

## Provider interface (`src/provider.h`)

```cpp
class Provider {
    QString id() const; QString name() const;
    int timeBudgetMs() const;          // default 150; overrun = cancelled, late results dropped
    bool available() const;            // false = skipped quietly (a CLI is absent)
    void prepare();                    // main thread, launcher about to show; must return fast
    QList<Result> query(const QString &text, const CancelToken &);   // worker thread; "" = suggestions
};
struct Result { id, provider, kind, title, subtitle, icon, score, QList<Action> actions; };  // actions[0] = Enter
struct Action { id, label, verb, args, data, confirm, stub; };
```

`query()` must be thread safe and poll the token in long loops. Ids are stable (`app:<desktop id>`,
`window:<id>`, `cmd:<id>`) so frecency and selection survive re-ranking. The engine applies a per-kind weight,
picks the Top hit and groups the rest. Third party providers will arrive through the plugin system; nothing
loads plugins yet.

## CLI contracts used

- `taos-window list --json` and `taos-window focus <id>` (given). Absent = Windows provider off.
- `taos-theme set [<theme>] [--mode dark|light|auto]` (exists in this tree).
- `taos-display scale OUTPUT PERCENT` (exists); the focused output comes from `swaymsg -t get_outputs`.
- ASSUMED, not in the tree yet: `taos-window mode tiling|desktop`, `taos-dock position bottom|left|right`,
  `taos-dock auto-hide on|off`, `taos-dock pin <desktop-id>`. Each action is only offered when its binary exists.
- Lock: `swaylock -f`, else `loginctl lock-session`. Log out: `swaymsg exit`. Restart/Shut down/Sleep: `systemctl`.

## Providers shipped

Apps (desktop entries, fuzzy, frecency in `~/.local/state/taos/launcher.json`), Open windows, Actions, Calculator,
Files (name search; walker is bounded and cancellable, index cached in `~/.cache/taos/launcher-files.txt`),
Settings (every entry is a marked stub until the Settings app exists), Agents (`@mentions`), Ask taOS.

## Ask taOS (stub)

The bottom row always offers "Ask taOS: <query>". No controller call is made. Hand-off order: `askCommand` from
the config (placeholder `{query}`); else a Messages/chat desktop entry (`org.taos.Messages.desktop`,
`taos-messages.desktop`, ...) is launched with the question copied to the clipboard; else the row says
"Agent chat is not set up yet".

## @agent mentions

`@` lists agents (name, status), typing filters, Tab or Enter completes, text after the mention is the message,
Enter sends. Several leading mentions go as one message only when the `ChatSender` says it supports several
recipients; otherwise the first agent gets it and the row says so. Per person config
`~/.config/taos/launcher.json`: `"mentionAction": "send" | "send_and_open"` (default `send`). Enter uses it;
Shift+Enter (or the Tab actions list: "Send only" / "Send and open chat") does the other; the right-hand hint
reads "Send" or "Send and open".

The code is written against `AgentDirectory` and `ChatSender` (`src/agents.h`, with fakes used by the tests).
The daemon runs with `UnavailableDirectory` and `UnavailableSender` because the client crate has no operation
for either (`native/taos-client/src/chat.rs` is a stub; only the raw `http` escape hatch exists, which the
launcher does not use). Missing client operations: list agents (GET /api/agents), find or create the direct
channel with an agent, post a message to a channel (POST /api/chat/messages), and a way to open a chat window
(none exists; the notification action payload would need to name the agent and channel).

## Tests and renders

`ctest -R launcher_test`: fuzzy ranking, desktop entry parsing, calculator (incl. malformed input), provider
time-boxing and streaming, IPC single instance and toggle, command availability, files walker bounds, mentions,
controller behaviour. `taos-launcher-shots <dir> [theme] [dark|light]` renders the panel offscreen.
