# Theme token inventory

A survey of every hardcoded color, shadow, and radius in `desktop/src`, organised by UI surface, so a future theme engine can swap each one for a CSS custom property.

## How to read this

Columns:

- **Where**: the file and component that owns the value.
- **What**: the role of the value (for example "title bar background").
- **Current value**: the exact string in the source (hex, rgba, or Tailwind class).
- **Proposed token name**: a `--taos-<surface>-<role>` custom property. Values reused across many places share ONE token, with every usage location listed under it.

Two systems already exist in the codebase and are NOT re-inventoried here as "hardcoded":

1. The shell token layer in `desktop/src/theme/tokens.css` (`@theme` block): `--color-shell-bg`, `--color-shell-surface`, `--color-accent`, `--color-dock-bg`, `--shadow-window`, `--spacing-window-radius`, and so on. Window chrome, the dock, the top bar, and the context menu already consume these. They are the destination, not the work.
2. The board token layer (`:root` in the same file): `--board-*` properties scoped to the Projects Kanban board.

This inventory targets the values that have NOT yet been routed through either token layer: raw Tailwind opacity utilities (`bg-white/5`, `border-white/10`), arbitrary hex classes (`bg-[#1a1a2e]`), and inline `style={{ color: "rgba(...)" }}` literals.

A naming note: the existing tokens are spelled `--color-shell-*` and `--shadow-*`. The proposed `--taos-*` names below are intentionally parallel suggestions for the new theme engine. When the engine lands, the cleaner move is probably to extend the existing `--color-shell-*` family rather than introduce a second prefix. The proposed names are kept as written in the job brief so the mapping intent is clear.

---

## Shared tokens (used across many surfaces)

These five values dominate the codebase. Each should become a single token, not one per call site. Counts are app-wide occurrences (excluding test files).

| Where (file, component) | What | Current value (class) | Proposed token name |
| --- | --- | --- | --- |
| App-wide (187 uses), e.g. `ModelPickerModal.tsx:39`, `SettingsApp.tsx:441`, `TopBar.tsx:53` (`border-white/10`) | Standard hairline border | `border-white/10` -> `rgba(255,255,255,0.1)` | `--taos-line` |
| App-wide (172 uses), e.g. `ModelPickerModal.tsx:42`, `SettingsApp.tsx:181/434/511/752` (`border-white/5`) | Faint divider / table row border | `border-white/5` -> `rgba(255,255,255,0.05)` | `--taos-line-faint` |
| App-wide (142 uses), e.g. `SettingsApp.tsx:767`, `Launchpad.tsx:101` (`bg-white/5`) | Subtle surface fill | `bg-white/5` -> `rgba(255,255,255,0.05)` | `--taos-surface` |
| App-wide (75 uses), e.g. `Launchpad.tsx:101`, `LaunchpadIcon.tsx:20` (`bg-white/10`) | Raised surface / search field fill | `bg-white/10` -> `rgba(255,255,255,0.1)` | `--taos-surface-alt` |
| App-wide (25 uses), e.g. `ModelPickerModal.tsx:26` (`bg-black/60`) | Modal scrim / backdrop | `bg-black/60` -> `rgba(0,0,0,0.6)` | `--taos-scrim` |

Note: `bg-white/5` and `border-white/5` resolve to the same rgba but read as different roles (fill vs. line). They are split into two tokens above so a theme can tune them independently.

---

## Window chrome (title bar, controls, borders, shadows)

File: `desktop/src/components/Window.tsx`. Mostly tokenized already; the only hardcoded values are the traffic-light glyph color.

| Where (file, component) | What | Current value | Proposed token name |
| --- | --- | --- | --- |
| `Window.tsx:163` (motion.div) | Window body background | `var(--color-shell-bg)` (already a token) | n/a (already `--color-shell-bg`) |
| `Window.tsx:151-155` | Window radius + border + shadow | `var(--spacing-window-radius)`, `border-shell-border-strong` / `border-shell-border`, `var(--shadow-window)` / `var(--shadow-window-unfocused)` (already tokens) | n/a (already tokenized) |
| `Window.tsx:173` (titlebar) | Title bar background | `bg-shell-surface` (already a token) | n/a (already `--color-shell-surface`) |
| `Window.tsx:176/190/204` (traffic lights) | Close / minimize / maximize button fills | `bg-traffic-close` / `bg-traffic-minimize` / `bg-traffic-maximize` (already tokens) | n/a (already `--color-traffic-*`) |
| `Window.tsx:184,198,214,224` (traffic glyph SVGs) | Glyph stroke color inside the traffic lights | `rgba(0,0,0,0.55)` (inline `style={{ color }}`) | `--taos-window-control-glyph` |

---

## Dock

Files: `desktop/src/components/dock/MacosDock.tsx`, `desktop/src/components/DockIcon.tsx`. Fully tokenized; no hardcoded color values remain.

| Where (file, component) | What | Current value | Proposed token name |
| --- | --- | --- | --- |
| `MacosDock.tsx:22-24` | Dock background, border, shadow | `var(--color-dock-bg)`, `var(--color-dock-border)`, `var(--shadow-dock)` (already tokens) | n/a (already tokenized) |
| `MacosDock.tsx:18,29` | Dock radius + tile fill | `rounded-2xl`, `bg-shell-surface` / `hover:bg-shell-surface-active` (already tokens) | n/a |
| `MacosDock.tsx:41,47` | Vertical separator | `bg-shell-border` (already a token) | n/a |
| `DockIcon.tsx:24` | Icon tile fill + hover | `bg-shell-surface` / `hover:bg-shell-surface-active` (already tokens) | n/a |
| `DockIcon.tsx:30` | Running-app dot | `bg-accent` (already a token) | n/a (already `--color-accent`) |

There are other dock variants under `desktop/src/components/dock/` (`WindowsTaskbar.tsx` and others registered in `DockVariants.ts`). The active default is `macos-dock`; the Windows taskbar variant was not exhaustively inventoried here (see Gaps).

---

## Top bar

File: `desktop/src/components/TopBar.tsx`. Tokenized except the power-menu popover background.

| Where (file, component) | What | Current value | Proposed token name |
| --- | --- | --- | --- |
| `TopBar.tsx:108-110` | Top bar height, background, bottom border | `var(--spacing-topbar-h)`, `var(--color-topbar-bg)`, `var(--color-shell-border)` (already tokens) | n/a (already tokenized) |
| `TopBar.tsx:53` (PowerMenu content) | Power menu border | `border-white/10` | `--taos-line` (shared) |
| `TopBar.tsx:54` (PowerMenu content) | Power menu popover background | `rgba(28,26,44,0.96)` (inline `style`) | `--taos-popover-bg` |
| `TopBar.tsx:73` (PowerMenu separator) | Menu separator line | `bg-white/10` | `--taos-line` (shared) |
| `TopBar.tsx:156` (notification dot) | Unread badge dot | `bg-red-500` (Tailwind `#ef4444`) | `--taos-badge-alert` |

---

## Launchpad

Files: `desktop/src/components/Launchpad.tsx`, `desktop/src/components/LaunchpadIcon.tsx`.

| Where (file, component) | What | Current value | Proposed token name |
| --- | --- | --- | --- |
| `Launchpad.tsx:86` (overlay) | Full-screen launchpad backdrop | `bg-black/40` -> `rgba(0,0,0,0.4)` | `--taos-launchpad-scrim` |
| `Launchpad.tsx:101` (search bar) | Search field fill | `bg-white/10` | `--taos-surface-alt` (shared) |
| `Launchpad.tsx:101` (search bar) | Search field border | `border-white/10` | `--taos-line` (shared) |
| `Launchpad.tsx:105,117,125,139` | Icon / label / heading text | `text-shell-text-tertiary` (already a token) | n/a |
| `LaunchpadIcon.tsx:20` | App tile hover fill | `bg-white/5` | `--taos-surface` (shared) |

The launchpad scrim (`bg-black/40`) is a different opacity from the generic modal scrim (`bg-black/60`), so it gets its own token.

---

## Desktop background / wallpaper handling

Files: `desktop/src/components/Desktop.tsx`, `desktop/src/theme/tokens.css` (`.taos-wallpaper`).

| Where (file, component) | What | Current value | Proposed token name |
| --- | --- | --- | --- |
| `Desktop.tsx:118` | Desktop fallback background color | `wallpaperFallback` (dynamic, from `useThemeStore`) | n/a (already theme-driven, not hardcoded) |
| `Desktop.tsx:118` | Wallpaper image vars | `--wallpaper-desktop` / `--wallpaper-mobile` (set inline from theme store) | n/a (already theme-driven) |
| `tokens.css:57-69` (`.taos-wallpaper`) | Wallpaper background sizing | `var(--wallpaper-desktop)` / `var(--wallpaper-mobile)` | n/a (already token-driven) |

The desktop surface is already fully theme-driven (the fallback color and both wallpaper images come from the theme store). Nothing to tokenize here.

---

## Widgets (widget cards)

Files: `desktop/src/components/WidgetLayer.tsx` (card shell), `desktop/src/components/widgets/*.tsx` (content), `desktop/src/theme/tokens.css` (`react-grid-*` overrides). This surface is almost entirely inline-styled with hardcoded values and is the single biggest tokenization job after Messages.

| Where (file, component) | What | Current value | Proposed token name |
| --- | --- | --- | --- |
| `WidgetLayer.tsx:127` (widget card) | Widget card background | `rgba(20, 20, 35, 0.65)` (inline) | `--taos-widget-card-bg` |
| `WidgetLayer.tsx:131` (widget card) | Widget card border | `1px solid rgba(255,255,255,0.1)` (inline) | `--taos-line` (shared) |
| `WidgetLayer.tsx:123` (widget card) | Widget card radius | `borderRadius: 12` (inline) | `--taos-widget-card-radius` |
| `WidgetLayer.tsx:156,174` (close button) | Close button idle fill | `rgba(255,255,255,0.1)` (inline) | `--taos-surface-alt` (shared) |
| `WidgetLayer.tsx:165,173` (close button) | Close button idle glyph | `rgba(255,255,255,0.4)` (inline) | `--taos-ink-dim` |
| `WidgetLayer.tsx:169-170` (close button hover) | Close button hover fill + glyph | `rgba(239,68,68,0.6)` + `#fff` (inline) | `--taos-danger-soft` + `--taos-ink-on-accent` |
| `WidgetLayer.tsx:207,219,224` (add button) | Add-widget FAB background (idle / hover) | `rgba(20, 20, 35, 0.7)` / `rgba(40, 40, 60, 0.85)` (inline) | `--taos-widget-fab-bg` / `--taos-widget-fab-bg-hover` |
| `WidgetLayer.tsx:210` (add button) | Add-widget FAB border | `1px solid rgba(255,255,255,0.15)` (inline) | `--taos-line-strong` |
| `WidgetLayer.tsx:211` (add button) | Add-widget FAB glyph | `rgba(255,255,255,0.7)` (inline) | `--taos-ink` |
| `WidgetLayer.tsx:238` (picker popover) | Widget picker popover background | `rgba(20, 20, 35, 0.9)` (inline) | `--taos-widget-popover-bg` |
| `WidgetLayer.tsx:241` (picker popover) | Widget picker popover border | `1px solid rgba(255,255,255,0.15)` (inline) | `--taos-line-strong` |
| `WidgetLayer.tsx:265,272` (picker item) | Picker item text + hover fill | `rgba(255,255,255,0.8)` + `rgba(255,255,255,0.1)` (inline) | `--taos-ink` + `--taos-surface-alt` (shared) |
| `WidgetLayer.tsx:35` (unknown widget) | Unknown-widget placeholder text | `rgba(255,255,255,0.4)` (inline) | `--taos-ink-dim` (shared) |
| `widgets/ClockWidget.tsx:28,39,54` | Clock primary time text | `rgba(255,255,255,0.95)` (inline) | `--taos-ink-strong` |
| `widgets/ClockWidget.tsx:45,60` | Clock secondary date / weekday text | `rgba(255,255,255,0.45)` / `rgba(255,255,255,0.6)` (inline) | `--taos-ink-dim` |
| `widgets/ClockWidget.tsx:63` | Clock tertiary long-date text | `rgba(255,255,255,0.35)` (inline) | `--taos-ink-faint` |
| `tokens.css:73-86` (`react-grid-placeholder`) | Drag placeholder fill + border | `rgba(139, 146, 163, 0.2)` + `rgba(139, 146, 163, 0.4)` (`!important`) | `--taos-grid-placeholder-bg` + `--taos-grid-placeholder-border` |
| `tokens.css:85-86` (`react-resizable-handle`) | Resize handle stroke | `rgba(255,255,255,0.4)` | `--taos-ink-dim` (shared) |

Other widget content files (`SystemStatsWidget.tsx`, `AgentStatusWidget.tsx`, `WeatherWidget.tsx`, `QuickNotesWidget.tsx`, `GreetingWidget.tsx`) follow the same `rgba(255,255,255,0.x)` ink pattern for text. They were sampled, not enumerated row by row, because every value collapses to the shared `--taos-ink*` family above.

---

## Messages app (sidebar, message list, composer)

File: `desktop/src/apps/MessagesApp.tsx` (2717 lines). This file mixes Tailwind shell classes (toolbar, container) with a large body of inline `style={{}}` rgba literals in the channel list and badges. The mobile and desktop channel lists duplicate the same value set. Representative rows below; the desktop list (lines ~1410-1490) repeats the mobile values (lines ~1300-1387).

| Where (file, component) | What | Current value | Proposed token name |
| --- | --- | --- | --- |
| `MessagesApp.tsx:2303` (root) | App background + text | `bg-shell-base text-white` | n/a (`bg-shell-base` is a token; `text-white` -> `--taos-ink-strong`) |
| `MessagesApp.tsx:2306` (toolbar) | Toolbar bottom border | `border-white/[0.06]` | `--taos-line-faint` (shared) |
| `MessagesApp.tsx:2310,2326` (toolbar) | Toolbar title text | `text-white/90` / `text-white/80` | `--taos-ink` |
| `MessagesApp.tsx:1340-1341` (channel group) | Channel list group fill + border | `rgba(255,255,255,0.05)` + `rgba(255,255,255,0.08)` (inline) | `--taos-surface` (shared) + `--taos-line` (shared) |
| `MessagesApp.tsx:1358` (channel row, selected) | Selected channel highlight | `rgba(59,130,246,0.15)` (inline) | `--taos-msg-channel-active` |
| `MessagesApp.tsx:1360` (channel row) | Row divider | `rgba(255,255,255,0.06)` (inline) | `--taos-line-faint` (shared) |
| `MessagesApp.tsx:1373` (channel name) | Channel name text | `rgba(255,255,255,0.9)` (inline) | `--taos-ink` |
| `MessagesApp.tsx:1377` (unread badge) | Unread badge fill + text | `#3b82f6` + `#fff` (inline) | `--taos-accent-blue` + `--taos-ink-on-accent` |
| `MessagesApp.tsx:1381` (chevron) | Disclosure chevron | `rgba(255,255,255,0.25)` (inline) | `--taos-ink-faint` |
| `MessagesApp.tsx:1307` (status, connected) | "Connected" status dot + text | `#34d399` + `rgba(52,211,153,0.8)` (inline) | `--taos-status-ok` |
| `MessagesApp.tsx:1309` (status, connecting) | "Connecting" status dot + text | `#fbbf24` + `rgba(251,191,36,0.8)` (inline) | `--taos-status-warn` |
| `MessagesApp.tsx:1311` (status, offline) | "Offline" status dot + text | `#f87171` + `rgba(248,113,113,0.8)` (inline) | `--taos-status-error` |
| `MessagesApp.tsx:1323` (empty-state button) | CTA fill / border / text | `rgba(59,130,246,0.2)` / `rgba(59,130,246,0.3)` / `rgba(147,197,253,0.9)` (inline) | `--taos-accent-blue-soft` / `--taos-accent-blue-line` / `--taos-accent-blue-ink` |
| `MessagesApp.tsx:1317-1334` (empty state) | Empty-state heading / body / icon text | `rgba(255,255,255,0.7)` / `0.35` / `0.15` / `0.2` (inline) | `--taos-ink-dim` / `--taos-ink-faint` |
| `MessagesApp.tsx:255` (markdown link) | Link color in message body | `text-blue-400` (Tailwind `#60a5fa`) | `--taos-link` |
| `MessagesApp.tsx:2269` (composer) | Composer placeholder | `placeholder` attr only (no inline color); inherits shell text | n/a (no hardcoded color at the composer textarea) |

The composer surface itself draws its colors from shell tokens and Tailwind text utilities, not inline hex; the inline color work in this file is concentrated in the channel list and status indicators.

---

## Settings app

File: `desktop/src/apps/SettingsApp.tsx` (801 lines). Contains ZERO hardcoded hex or rgba literals. All color comes from shell tokens and Tailwind `white/N` opacity utilities, so it maps cleanly onto the shared tokens.

| Where (file, component) | What | Current value | Proposed token name |
| --- | --- | --- | --- |
| `SettingsApp.tsx:177` (info card) | Card fill + border | `bg-white/[0.04]` + `border-white/[0.06]` | `--taos-surface` (shared) + `--taos-line-faint` (shared) |
| `SettingsApp.tsx:181` (table row) | Row bottom border | `border-white/5` | `--taos-line-faint` (shared) |
| `SettingsApp.tsx:182` (table label) | Label text | `text-shell-text-secondary` (already a token) | n/a |
| `SettingsApp.tsx:441` (control) | Control borders | `border-white/10` / `border-white/20` | `--taos-line` (shared) / `--taos-line-strong` |
| `SettingsApp.tsx:495` (panel) | Panel fill + borders | `bg-white/[0.04]` + `border-white/[0.06]` + `border-white/[0.08]` | `--taos-surface` (shared) + `--taos-line-faint` + `--taos-line` |
| `SettingsApp.tsx:767` (row) | Hover / fill | `bg-white/5` | `--taos-surface` (shared) |

---

## Modals and popovers

| Where (file, component) | What | Current value | Proposed token name |
| --- | --- | --- | --- |
| `ModelPickerModal.tsx:26` (backdrop) | Modal scrim | `bg-black/60` | `--taos-scrim` (shared) |
| `ModelPickerModal.tsx:39` (panel) | Modal panel fill + border | `bg-shell-surface` (token) + `border-white/10` | n/a + `--taos-line` (shared) |
| `ModelPickerModal.tsx:42` (header) | Header bottom border | `border-white/5` | `--taos-line-faint` (shared) |
| `ContextMenu.tsx:103` (menu) | Context menu background | `rgba(30, 31, 50, 0.95)` (inline) | `--taos-popover-bg` (shared with TopBar power menu, value differs) |
| `ContextMenu.tsx:105` (menu) | Context menu shadow | `0 8px 32px rgba(0,0,0,0.5)` (inline) | `--taos-popover-shadow` |
| `ContextMenu.tsx:99,113` (menu) | Menu border + separator | `border-shell-border-strong` / `border-shell-border` (already tokens) | n/a |
| `ContextMenu.tsx:135` (menu item) | Item hover / focus fill | `hover:bg-white/8` / `focus:bg-white/8` | `--taos-surface-hover` |

Popover background note: the TopBar power menu (`rgba(28,26,44,0.96)`) and the ContextMenu (`rgba(30, 31, 50, 0.95)`) use slightly different dark-violet values for the same role. A theme engine should collapse them into one `--taos-popover-bg`; the two source values are recorded so the discrepancy is visible.

---

## Scrollbars

No surface defines styled scrollbar colors. The only scrollbar CSS found HIDES the scrollbar rather than recoloring it:

- `desktop/src/components/mobile/WorkspaceTabPills.tsx:31-32` (`[scrollbar-width:none]`, `[&::-webkit-scrollbar]:hidden`)
- `desktop/src/apps/BrowserApp/BookmarksBar.tsx:129` (`scrollbarWidth: "none"`)

There are no `::-webkit-scrollbar-thumb` color rules anywhere in `desktop/src`. Nothing to tokenize for scrollbars.

---

## Inline `style={{}}` color usages (the hard ones for later)

These are the values a class-based theme cannot reach without code changes, because they live in JS object literals rather than CSS classes. They are the priority migration targets.

| Where (file, component) | Inline color(s) |
| --- | --- |
| `Window.tsx:184,198,214,224` (traffic glyphs) | `color: "rgba(0,0,0,0.55)"` |
| `TopBar.tsx:54` (power menu) | `backgroundColor: "rgba(28,26,44,0.96)"` |
| `Desktop.tsx:118` | `backgroundColor: wallpaperFallback` (dynamic, fine) + wallpaper CSS vars |
| `ContextMenu.tsx:103,105` | `backgroundColor: "rgba(30, 31, 50, 0.95)"`, `boxShadow: "0 8px 32px rgba(0,0,0,0.5)"` |
| `WidgetLayer.tsx:127,131,156,165,169,170,174,207,210,211,219,224,238,241,265,272,35` | The full widget card / FAB / picker palette listed in the Widgets section, all inline rgba |
| `widgets/ClockWidget.tsx:28,39,45,54,60,63` | All clock text colors, inline rgba `rgba(255,255,255,0.x)` |
| `MessagesApp.tsx` (channel list, lines ~1305-1490; desktop list mirrors mobile) | All channel-list / status / badge / empty-state colors, inline hex + rgba |

The two heaviest inline-color surfaces are `WidgetLayer.tsx` and `MessagesApp.tsx`. Everything else is mostly Tailwind classes (reachable by a theme that overrides Tailwind's `white`/`black` palette or by find-and-replace to token-backed utilities).

---

## Summary

### Total distinct color values found

Counting raw hardcoded values that are NOT already routed through the `--color-shell-*` / `--shadow-*` / `--board-*` token layers, and excluding test fixtures and false positives:

- Distinct **hex** values in real UI code (after dropping `#356`, `#618`, `#312`, `#8942` which are PR references / an HTML entity, and `#abc123` from a test): roughly **30**. The recurring real ones are `#8b92a3`, `#6c8df0`, `#f5b86b`, `#3b82f6`, `#d2d2d7`, `#86868b`, `#151625`, `#1a1b2e`, `#1a1a2e`, `#f5f5f7`, `#fff`, `#ff5f57`, `#febc2e`, `#28c840`, `#fbbf24`, `#34d399`, `#f87171`, plus the Matrix-theme greens.
- Distinct **rgba** values: roughly **45**, dominated by the `rgba(255,255,255,0.0x..0.95)` ink ladder (about 20 distinct alpha steps) plus a handful of brand blues (`rgba(59,130,246,*)`), status colors, and dark panel fills.
- Distinct **Tailwind opacity classes** (`white/N`, `black/N`): roughly **30** (for example `border-white/10`, `bg-white/5`, `bg-black/60`).

Combined, that is on the order of **100 distinct hardcoded color strings**, but they collapse to far fewer semantic roles. The bulk are alpha variations of white-on-dark ink and surface fills that map to a small `--taos-ink*` / `--taos-surface*` / `--taos-line*` family.

### The 10 most reused values

By raw occurrence across `desktop/src` (Tailwind classes and rgba literals combined, tests excluded):

1. `border-white/10` (`rgba(255,255,255,0.1)`), 187 uses -> `--taos-line`
2. `border-white/5` (`rgba(255,255,255,0.05)`), 172 uses -> `--taos-line-faint`
3. `bg-white/5` (`rgba(255,255,255,0.05)`), 142 uses -> `--taos-surface`
4. `bg-white/10` (`rgba(255,255,255,0.1)`), 75 uses -> `--taos-surface-alt`
5. `rgba(255,255,255,0.08)` (inline + `bg-white/[0.08]`), 31+ uses -> `--taos-line` / `--taos-surface-alt`
6. `rgba(255,255,255,0.06)` (inline + `border-white/[0.06]`), 25+ uses -> `--taos-line-faint`
7. `bg-black/60` (`rgba(0,0,0,0.6)`), 25 uses -> `--taos-scrim`
8. `rgba(255,255,255,0.4)` (inline), 26 uses -> `--taos-ink-dim`
9. `rgba(255,255,255,0.45)` (inline), 19 uses -> `--taos-ink-dim`
10. `rgba(255,255,255,0.95)` (inline), 17 uses -> `--taos-ink-strong`

The headline finding: the entire app's color surface is, semantically, a white-on-dark opacity ladder plus a few accent and status colors. A theme engine that ships `--taos-ink*`, `--taos-surface*`, `--taos-line*`, `--taos-scrim`, one accent, and three status colors would cover the vast majority of these call sites.

### Surfaces whose styling lives somewhere unexpected

- **Window chrome, dock, top bar**: already mostly migrated to the `--color-shell-*` / `--shadow-*` tokens in `tokens.css`. The "inventory" for these is short because the work is largely done; only stray inline glyph/popover colors remain.
- **Widgets**: styled almost entirely inline in `WidgetLayer.tsx` and the `widgets/*` content files, NOT in CSS or Tailwind classes. This is the surface most resistant to a class-only theme.
- **react-grid-layout overrides**: the widget drag placeholder and resize-handle colors live in `tokens.css` (lines 73-87) as `.react-grid-*` global selectors with `!important`, not on any component. Easy to miss.
- **Messages app**: a single 2717-line file carries its own inline color palette (status greens/ambers/reds, accent blues) that no other surface reuses; it is effectively its own mini design system embedded in one component.
- **Settings app**: the opposite extreme. Zero hardcoded literals; it is the cleanest surface and maps onto shared tokens with no inline work at all.
- **Projects Kanban board**: not in the requested surface list, but worth flagging that it already has its OWN token layer (`--board-*` in `tokens.css` plus per-component `*.module.css` files under `apps/ProjectsApp/board/`), separate from the shell tokens. A unified theme engine will need to reconcile the `--board-*` family with the `--taos-*` / `--color-shell-*` family.

### Confidence and gaps

Honest gaps where I sampled rather than enumerated every line:

- **Widget content files** (`SystemStatsWidget`, `AgentStatusWidget`, `WeatherWidget`, `QuickNotesWidget`, `GreetingWidget`): sampled. Every value seen collapses to the `--taos-ink*` ladder, but individual rows were not transcribed.
- **MessagesApp desktop channel list** (lines ~1410-1490): confirmed to mirror the mobile list's value set; not transcribed line-by-line to avoid duplicate rows.
- **Non-default dock variants** (`WindowsTaskbar.tsx` and any other entries in `DockVariants.ts`): the active default `macos-dock` was inventoried fully; alternate variants were not.
- **Other app windows** (Browser, Store, Agents, Models, Memory, and the rest of `apps/`): out of the requested surface scope, so not inventoried. They contribute heavily to the app-wide `bg-white/5` / `border-white/10` counts above, which is why those shared-token totals are so high. If the theme engine needs full coverage, those apps are the long tail.

Every row in the per-surface tables above was read from the cited file and line and the value quoted exactly. Where a surface was too large to transcribe in full (Messages, widget content), that is stated rather than guessed.
