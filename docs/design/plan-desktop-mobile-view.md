# Desktop Shell Mobile & Tablet View — Implementation Plan

**Status:** Implemented — this plan has landed; see the feature on `master` for the current state.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add responsive mobile and tablet modes to the desktop shell, inspired by Palm webOS card multitasking, Android navigation, iOS fluidity, and KDE Plasma Mobile. No edge gestures (avoids OS conflicts). Uses a pill-bar navigator and webOS-style card app switcher.

**Architecture:** Same React SPA, responsive variants per component driven by a `useDeviceMode()` hook. The shell chrome adapts; app components stay the same. Three modes: desktop (>=1024px pointer), tablet (>=768px touch), mobile (<768px).

**Tech Stack:** Same as desktop shell + CSS media queries + touch event handlers

---

## Design Philosophy

### No Edge Gestures
Edge swipes conflict with iOS (home, app switcher) and Android (back gesture). Everything operates via explicit UI elements — the pill bar and in-app buttons. This is a web app pretending to be an OS; we embrace that constraint rather than fighting it.

### The Pill Bar (replaces dock on mobile/tablet)
A slim translucent bar at the bottom with a draggable pill handle in the centre. Inspired by Palm webOS gesture area and iOS home indicator, but interactive:
- **Tap pill** → Home (launchpad)
- **Swipe pill up** → Card switcher (webOS-style)
- **Left icon** → Back (within app)  
- **Right icon** → Quick actions / notifications

The pill bar is visually integrated — same Soft Depth aesthetic as the dock, just minimal.

### Card App Switcher (webOS inspired)
Instead of a grid, open apps show as stacked cards in a horizontal carousel. Swipe horizontally to browse, flick a card upward to close it. This was Palm webOS's signature feature and remains the most intuitive touch app-switching UX ever designed.

### Visual Continuity
Same wallpapers, same accent colours, same app icons. The desktop dock "collapses" into the pill bar. The top bar becomes a minimal status bar. Windows become fullscreen cards. The identity stays consistent.

---

## File Structure

```
desktop/src/
├── hooks/
│   └── use-device-mode.ts           # Detects desktop/tablet/mobile
├── components/
│   ├── mobile/
│   │   ├── PillBar.tsx              # Bottom navigation pill
│   │   ├── CardSwitcher.tsx         # webOS-style card app switcher
│   │   ├── MobileTopBar.tsx         # Minimal status bar for mobile
│   │   └── MobileApp.tsx            # Fullscreen app wrapper (replaces Window)
│   └── App.tsx                      # Updated to render mobile/desktop variants
```

---

## Task 1: Device Mode Hook

**Files:**
- Create: `desktop/src/hooks/use-device-mode.ts`

- [ ] **Step 1: Create the hook**

```typescript
// desktop/src/hooks/use-device-mode.ts
import { useState, useEffect } from "react";

export type DeviceMode = "desktop" | "tablet" | "mobile";

export function useDeviceMode(): DeviceMode {
  const [mode, setMode] = useState<DeviceMode>(detectMode);

  useEffect(() => {
    const handler = () => setMode(detectMode());
    window.addEventListener("resize", handler);
    return () => window.removeEventListener("resize", handler);
  }, []);

  return mode;
}

function detectMode(): DeviceMode {
  const width = window.innerWidth;
  const isTouch = matchMedia("(pointer: coarse)").matches || navigator.maxTouchPoints > 0;

  if (width < 768) return "mobile";
  if (width < 1024 && isTouch) return "tablet";
  return "desktop";
}
```

- [ ] **Step 2: Commit**

```bash
git add desktop/src/hooks/use-device-mode.ts && git commit -m "add device mode detection hook: desktop/tablet/mobile"
```

---

## Task 2: Pill Bar

**Files:**
- Create: `desktop/src/components/mobile/PillBar.tsx`

- [ ] **Step 1: Create PillBar component**

The pill bar sits at the bottom of the screen on mobile/tablet. It has:
- A centre pill handle (28px wide, 4px tall, rounded) that responds to:
  - Tap → open launchpad
  - Swipe up (vertical drag > 50px) → open card switcher
- Left side: back button (ChevronLeft icon) — calls history.back() or closes current app
- Right side: notifications bell with badge count

Styling: semi-transparent background matching dock aesthetic, ~44px total height, safe area padding for notched devices (env(safe-area-inset-bottom)).

Touch handling: use `onTouchStart` / `onTouchMove` / `onTouchEnd` on the pill to detect swipe-up gesture. Track start Y position; if delta > 50px upward, trigger card switcher.

```tsx
// Signature
interface Props {
  onHome: () => void;
  onCardSwitcher: () => void;
  onBack: () => void;
}
export function PillBar({ onHome, onCardSwitcher, onBack }: Props)
```

- [ ] **Step 2: Commit**

```bash
git add desktop/src/components/mobile/PillBar.tsx && git commit -m "add PillBar: touch navigation with swipe-up card switcher"
```

---

## Task 3: Card Switcher

**Files:**
- Create: `desktop/src/components/mobile/CardSwitcher.tsx`

- [ ] **Step 1: Create CardSwitcher component**

Full-screen overlay showing open apps as horizontal cards (like webOS). Each card:
- Shows a scaled-down preview of the app (just the app name + icon for now, real preview later)
- Has the app title below
- Can be swiped upward to close (flick to dismiss)
- Can be tapped to switch to that app

Layout: cards are horizontally scrollable, 70% viewport width each, slightly overlapping, with perspective transform for depth. Active card is centred and larger.

When no apps are open, show "No apps open" with a launchpad button.

Close the switcher by tapping the background or selecting an app.

Touch handling:
- Horizontal scroll: native overflow-x scroll with snap points (CSS scroll-snap)
- Vertical flick to close: `onTouchStart`/`onTouchMove`/`onTouchEnd` per card. If upward delta > 100px, animate card flying up and remove from process store.

```tsx
interface Props {
  open: boolean;
  onClose: () => void;
  onSelectApp: (windowId: string) => void;
  onLaunchpad: () => void;
}
export function CardSwitcher({ open, onClose, onSelectApp, onLaunchpad }: Props)
```

- [ ] **Step 2: Commit**

```bash
git add desktop/src/components/mobile/CardSwitcher.tsx && git commit -m "add CardSwitcher: webOS-style card multitasking with flick-to-close"
```

---

## Task 4: Mobile Top Bar

**Files:**
- Create: `desktop/src/components/mobile/MobileTopBar.tsx`

- [ ] **Step 1: Create MobileTopBar**

A slim status bar for mobile/tablet. Shows:
- Left: back arrow (if an app is open) or TinyAgentOS logo (if on home)
- Centre: current app name
- Right: clock (HH:MM) + notification bell

Height: 28px. Semi-transparent, same as desktop top bar but slimmer.

```tsx
interface Props {
  currentAppName: string | null;
  onBack: () => void;
}
export function MobileTopBar({ currentAppName, onBack }: Props)
```

- [ ] **Step 2: Commit**

```bash
git add desktop/src/components/mobile/MobileTopBar.tsx && git commit -m "add MobileTopBar: minimal status bar for mobile/tablet"
```

---

## Task 5: Mobile App Wrapper

**Files:**
- Create: `desktop/src/components/mobile/MobileApp.tsx`

- [ ] **Step 1: Create MobileApp**

Replaces the Window component on mobile/tablet. Renders a single app fullscreen (no titlebar, no drag, no resize). The app component fills the entire space between the top bar and pill bar.

On tablet: can also render in split view (two apps side by side, 50/50). Split mode activated by a button in the card switcher.

```tsx
interface Props {
  appId: string;
  windowId: string;
}
export function MobileApp({ appId, windowId }: Props)
```

- [ ] **Step 2: Commit**

```bash
git add desktop/src/components/mobile/MobileApp.tsx && git commit -m "add MobileApp: fullscreen app wrapper for mobile/tablet"
```

---

## Task 6: Wire Mobile Mode into App.tsx

**Files:**
- Modify: `desktop/src/App.tsx`

- [ ] **Step 1: Update App.tsx to switch between desktop and mobile layouts**

Read the current App.tsx first. Then modify it:
- Import `useDeviceMode`
- Import mobile components: PillBar, CardSwitcher, MobileTopBar, MobileApp
- When mode is "desktop": render current layout (TopBar + Desktop + Dock + Launchpad)
- When mode is "mobile" or "tablet": render MobileTopBar + MobileApp (active app fullscreen) + PillBar + CardSwitcher + Launchpad
- Track `activeWindowId` state for mobile (which app is in the foreground)
- PillBar's onHome opens launchpad, onCardSwitcher opens card switcher, onBack closes active app

The mobile layout should use the wallpaper as background (same as desktop), with the current app rendered fullscreen between the top bar and pill bar.

```tsx
if (mode === "desktop") {
  return (
    <div className="h-screen w-screen flex flex-col overflow-hidden bg-shell-bg text-shell-text">
      <TopBar onSearchOpen={toggleSearch} />
      <Desktop />
      <Dock onLaunchpadOpen={toggleLaunchpad} />
      <Launchpad ... />
      <SearchPalette ... />
    </div>
  );
}

// Mobile/tablet
return (
  <div className="h-screen w-screen flex flex-col overflow-hidden text-shell-text" style={{ background: wallpaperStyle }}>
    <MobileTopBar currentAppName={...} onBack={...} />
    <div className="flex-1 relative overflow-hidden">
      {activeWindowId ? <MobileApp appId={...} windowId={...} /> : <MobileLaunchpadGrid />}
    </div>
    <PillBar onHome={...} onCardSwitcher={...} onBack={...} />
    <CardSwitcher ... />
    <Launchpad ... />
  </div>
);
```

- [ ] **Step 2: Rebuild and test**

```bash
cd /home/jay/tinyagentos/desktop && npx vite build
```

- [ ] **Step 3: Commit**

```bash
cd /home/jay/tinyagentos && git add desktop/src/ static/desktop/ && git commit -m "add mobile/tablet responsive mode: pill bar, card switcher, fullscreen apps"
```

---

## Task 7: Touch Targets + CSS

- [ ] **Step 1: Add mobile-specific CSS to tokens.css**

Add to `desktop/src/theme/tokens.css`:
```css
/* Touch targets */
@media (pointer: coarse) {
  button, [role="button"], a {
    min-height: 44px;
    min-width: 44px;
  }
}

/* Safe area for notched devices */
.safe-bottom {
  padding-bottom: env(safe-area-inset-bottom, 0px);
}

/* No hover effects on touch */
@media (hover: none) {
  .hover\:bg-shell-surface-hover:hover {
    background-color: transparent;
  }
}
```

- [ ] **Step 2: Rebuild, restart, commit**

```bash
cd /home/jay/tinyagentos && git add desktop/ static/desktop/ && git commit -m "add touch CSS: 44px targets, safe areas, no-hover on touch"
```
