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

## The app menu

Open the launcher with nothing typed and it shows the app menu (the search is still one keystroke away: type and the
menu gives way to results). One model and one engine serve every look; the styles below only draw it.

- **All apps**: every desktop entry the session knows (host apps, the built in taOS apps `my.taos.*`, app group apps
  `taos-appgroups-*` with their group shown quietly, web apps `taos-webapp-*`), with the icon the dock uses
  (`iconprovider.cpp`: absolute path, icon theme, lower case retry, a bare hicolor tree, `/usr/share/pixmaps`), grouped by
  category (Internet, Development, Office, Graphics, Media, Games, System, taOS, Other; freedesktop categories are mapped in
  `appmenu.cpp`) or A-Z. New, removed and edited apps show up within a moment (`QFileSystemWatcher` over the applications
  folders, the files and any new sub folder, debounced).
- **Pinned**: `~/.config/taos/startmenu.json` `{"pinned": ["firefox.desktop", ...]}`. Until the person pins something the dock's
  list (`dock.json` `pinned`) is the starting point, so the two begin the same; after that the menu has its own list and never
  rewrites the dock's. "Keep in Dock" in the context menu is `taos-dock pin`. Pins of apps that are not installed are kept (a
  reinstall brings them back) and skipped when drawing.
- **Recent**: the most used apps by the existing frecency, never a pinned one.
- **Footer**: the person (name from the account, `~/.face` or the AccountsService icon when there is one), Settings, Files,
  Actions and Power. Power lists Sleep, Sign out, Restart, Shut down (and Lock); the ones that end the session ask for a second
  Enter. The commands are the ones search offers (`CommandsProvider`), there is one definition.
- **Context menu** (right click, long press, Menu key, Tab): Open, Pin or Unpin, Keep in Dock, App info, Move to group (app group
  apps: `taos-appgroups move <app> --to group:<name>`, which exists only where that engine has `move`), Uninstall (app group apps:
  `taos-appgroups remove`, web apps: `taos-webapp remove`; host packages belong to the package manager and have no row). Uninstall asks twice.
- **Keyboard**: type to search at once; arrows walk the menu (the text field keeps the focus, the cursor is `menunav.cpp`);
  Enter opens; Tab or the Menu key shows the context menu; Escape closes the topmost layer (confirmation, sheet, power list, a level of
  the action menu) and then the launcher.
- **System action menu** (type `>` or press Super+Alt+Space or the Actions button): a short hierarchy, Capture, Toggle, Style,
  Setup, Install, Help (Keyboard shortcuts: `taos-overview help`), System, each a list you filter by typing; Enter opens a group or runs an action; Backspace on an empty filter or Escape goes
  up. Every row is backed by an existing engine and is offered only when its program exists (grim and slurp, nmcli, bluetoothctl,
  taos-theme, taos-settings, taos-store, the session commands). Not offered because no engine exists yet: Do Not Disturb, night light,
  screen recording, wallpaper, accounts, app groups setup, updates. The idea of a keyboard first hierarchical menu with a filter at
  every level comes from the Omarchy menu (github.com/basecamp/omarchy, MIT, DHH); no code or text is taken from it.

### Styles

`layout.launcher` in the theme names the renderer; the engine and the keyboard are the same for all. `--style` (or
`TAOS_LAUNCHER_STYLE`) forces one whatever the theme says; a narrow surface (under 640 px) is always the phone drawer.

| style | what it draws |
|---|---|
| `centered` | a full panel: pinned grid and recent on the left, every app on the right |
| `anchored` | the classic two pane start menu at the taskbar's start button: every app (what you use first, then by category) left, pinned grid right |
| `ring` | the pinned apps on a ring with the name in the middle and an "All apps" view; the ring theme's accent and data face |
| `text` | the calm home: the time and a few favourite names in large type, no icons; type to find anything; swipe up for every app, down for the action menu, double tap to lock. Behaviour inspired by Olauncher (github.com/tanujnotes/Olauncher, GPL-3.0): nothing of its code, text or design is used. `taos-launcher daemon --style text` makes it a phone home screen |
| phone | automatic below 640 px: the whole screen, pinned grid, every app by letter, compact footer |

### For agents (the same menu without a window)

    taos-search menu [pinned|recent|categories|letters] [--json]     taos-search pin|unpin ID     taos-search move-pin ID N
    taos-search actions [GROUP...] [--json]                          taos-search run ACTION [confirm]
    taos-launcher menu | pin ID | unpin ID | move-pin ID N | actions [GROUP...] | run ACTION [confirm] | show-actions   (to the running daemon)

Power style actions need the word `confirm`; an agent says it on purpose.

### Starting apps

Every program the launcher or the dock starts goes through `common/applaunch.cpp`: a clean app environment (no
`QT_WAYLAND_SHELL_INTEGRATION`, no `TAOS_SHELL_*` except the env file the app wrappers source, nothing the component published as its own)
and its own systemd user scope (`taos-app-<name>-<time>`) when the session has a user manager, so an app is neither a layer surface by
inheritance nor a child of the dock.

## Tests and renders

`ctest -R launcher_test`: fuzzy ranking, desktop entry parsing, calculator (incl. malformed input), provider
time-boxing and streaming, IPC single instance and toggle, command availability, files walker bounds, mentions,
controller behaviour. `taos-launcher-shots <dir> [theme] [dark|light] [style] [WxH]` renders the menu and the search offscreen (fixtures: `tests/fixtures/menu-apps`, icons in `menu-theme`).
