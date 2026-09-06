# taOS Mobile Redesign — Design Spec

## Overview

Redesign the taOS mobile home screen from a cramped 24-icon grid to a widget-first, customisable, multi-page layout with a persistent dock. Apps render as maximised windows with desktop-style title bars.

## Top Bar

- **Left**: "taOS" text — tap to go home (minimise all open windows)
- **Centre**: compact status indicators (CPU, RAM, NPU) — same data as current
- **Right**: search button + notification bell as frosted glass circular buttons (iOS 26 style — semi-transparent with backdrop blur and subtle border)
- Height: 44px
- Always visible — home screen and in-app

## Home Screen — Multi-Page Layout

- Multiple pages navigated by tapping page dots (no swipe gestures)
- Page dot indicator sits between content area and dock
- Each page is fully customisable — any mix of widgets and app shortcut icons
- All page customisation happens in Settings (no long-press edit mode)

### Default Pages

**Page 1 — Widgets**:
- Greeting widget: time-based greeting ("Good morning.", "Good afternoon.", "Good evening.") with summary subtitle (e.g. "3 agents running · 2 tasks queued")
- CPU widget: percentage + colour-coded bar
- Memory widget: percentage + colour-coded bar
- Agent Status widget: list of agents with status dots
- System Info widget: temperature, storage, model count

**Page 2 — All Apps**:
- App grid grouped by category (Platform, Utilities, Games)
- Category headers as section labels
- 4-column grid, same icon style as current (60px rounded square with gradient)

### Available Widgets

Reuse existing widget components, adapted for mobile layout:
1. **Greeting** (new) — time-based greeting + system summary
2. **Clock** — digital clock with date
3. **Agent Status** — agent names and status, polls every 15s
4. **System Stats** — CPU and RAM bars with colour coding
5. **Weather** — weather data
6. **Quick Notes** — sticky notes

## Dock

- Persistent bar at the bottom of the screen, always visible
- **4 customisable app icon slots** — default: Messages, Agents, Files, Store
- **Vertical divider** (1px, subtle)
- **Card switcher button** — toggles the card switcher overlay
- Icon size: ~44px rounded squares, no labels
- Active app indicated by a dot below its icon (same as desktop dock)
- Customisation: managed in Settings (pick which 4 apps), not drag-and-drop
- Background: dark semi-transparent with backdrop blur, matching current nav bar style

## App Windows

When an app is opened, it renders as a full-width maximised window:

- **Title bar**: 
  - Left: close button (red circle) and minimise button (yellow circle) — same style as desktop window decorations
  - Centre: app name
  - Right: empty spacer for balance
- **Content area**: full width, fills space between title bar and dock
- **No maximise button** — apps are always full-width on mobile
- **Minimise**: sends the app to the card switcher (disappears from view, stays in memory)
- **Close**: kills the window entirely
- Top bar and dock remain visible when an app is open

## Card Switcher

- Triggered by tapping the switcher button in the dock (toggles open/closed)
- Overlay with dark backdrop blur (z-index below dock so dock stays visible)
- Horizontally scrollable cards, snap-centre alignment
- Each card shows: app icon, app name, X button to close
- Tap a card to switch to that app
- Tap outside cards or tap switcher button again to close
- No swipe-to-dismiss — X button only
- "No apps open" state with prompt to open launchpad

## Settings — Home Screen Customisation

New settings section: "Home Screen"

- **Dock Apps**: pick which 4 apps appear in the dock (dropdown/picker for each slot)
- **Pages**: list of pages, each showing its contents
  - Add/remove pages
  - Add/remove widgets and app shortcuts to each page
  - Reorder items within a page
- Keep it simple — list-based UI, no drag-and-drop

## Removed Components

- `MobileBottomNav` — replaced by dock
- Old 24-icon home grid in App.tsx — replaced by multi-page system
- Safe area inset hacks — using `fixed inset-0` layout
- Swipe gestures throughout — too unreliable in web apps

## Technical Notes

- New branch: `mobile-redesign`
- Mobile layout detection stays the same (touch device + PWA mode)
- Desktop layout is unchanged — this only affects the mobile/tablet code path
- Reuse existing stores where possible (process-store, dock-store, widget-store)
- New store needed: `mobile-home-store` for page layout, widget placement, dock config
- Existing widget components (WidgetLayer.tsx) adapted for mobile grid — they currently use react-grid-layout which is drag-based; mobile version uses a simpler static grid sized to the widget type
