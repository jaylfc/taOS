# Settings: parity with the web app (18 sections)

Native app id `my.taos.settings`, binary `taos-settings`.
Deep link: `taos-settings --section <id> [--setting <id>]` (second launch navigates the running window).

| Web section | Native | State |
| --- | --- | --- |
| Account | planned (Accounts and people) | not started |
| System info | About (device, OS, CPU, memory, storage, taOS version from /api/version, link to Activity) | working |
| Wi-Fi | Network and Bluetooth (bar's NmClient: switch, join, forget, disconnect) | working when NetworkManager runs |
| Bluetooth | same page (bar's BluezClient: power, pair, connect, forget) | working when BlueZ runs |
| Storage | About shows free/total only; full page planned | partial |
| Memory | planned | not started |
| Backup and restore | planned | not started |
| Updates | planned (status, check; install via controller route) | not started |
| Advanced | omitted (web text only) | n/a |
| Keyboard shortcuts | Desktop and windows (read only table) | working |
| Accessibility | reduce motion shown disabled (no per person store yet) | disabled |
| Desktop and dock | Dock (dock.json) and Desktop and windows (desktop.json default mode) | working |
| Users | planned | not started |
| Themes | Appearance (theme engine: mode and theme, live) | working |
| Logs | planned | not started |
| Notifications | planned | not started |
| Lock screen | planned | not started |
| Demo mode | planned, only when available | not started |
| Native first: Display | Displays (refresh, scale, recommended, 10 s keep or revert; resolution read only; arrangement diagram later) | working, partial |
| Native first: wallpaper, keyboard, navigation | planned | not started |

Missing from other components: see the commit report (window buttons key in appearance.json, a persisted reduce
motion key, a public resolution setter on DisplayController, a standalone shell models library).
