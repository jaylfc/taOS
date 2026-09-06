# Messages PWA Phase 1 — design

**Status:** Approved 2026-04-20.

## Goal

Make taOS talk feel native on phones. Stack nav, full-screen thread takeover, bottom sheet actions, keyboard-aware composer, install-to-home-screen prompt. Everything runs from the existing `/chat-pwa` PWA entry; desktop behavior is preserved.

## Non-goals (deferred to later phases)

- Service worker, offline shell caching, stale-while-revalidate. **Phase 2.**
- Push notifications.
- Pull-to-refresh.
- Native app store distribution.
- iOS App Clip / Android Instant App.

## Baseline — what already exists

- `desktop/chat.html` — dedicated PWA entry with manifest, favicon, Apple meta tags.
- `desktop/src/chat-main.tsx` → `ChatStandalone.tsx` → lazy `MessagesApp`.
- `static/manifest-chat.json` — valid PWA manifest (`display: standalone`, 192/512 icons with `any maskable`).
- `tinyagentos/routes/desktop.py:189` — serves `/chat-pwa` + SPA fallback at `/chat-pwa/{rest:path}`.
- `desktop/src/hooks/use-is-mobile.ts` — `<768px` breakpoint (Tailwind `md`).
- `desktop/src/components/mobile/MobileSplitView.tsx` — stack nav for channel list → message view.

## Decisions

1. **Mobile breakpoint:** `useIsMobile()` (< 768px). Reuse existing hook.
2. **Thread panel on mobile:** full-screen takeover (stack nav).
3. **Overflow menu on mobile:** bottom sheet.
4. **Channel nav on mobile:** existing `MobileSplitView` stack pattern. No bottom tabs.
5. **Install prompt:** auto-banner on first mobile visit, dismissible, 30-day suppression.

## Architecture

### New shell primitives

**`desktop/src/shell/BottomSheet.tsx`**
- Modal dialog sliding up from the bottom.
- Props: `{open, onClose, children, labelledBy?, dragHandle?: boolean = true}`.
- Backdrop: `bg-black/60` covering full viewport, click = `onClose`.
- Sheet: `fixed bottom-0 inset-x-0`, `max-h-[85vh]`, safe-area inset padding on bottom.
- Drag handle: 40×4px pill at top. Pointer events move the sheet; release past 80px threshold triggers `onClose`; release under threshold snaps back.
- Esc key closes (listener scoped to the sheet).
- `role="dialog"`, `aria-modal="true"`, `aria-labelledby` when provided.
- Focus trap: first focusable element gets focus on open; Tab cycles inside the sheet.

**`desktop/src/shell/InstallPromptBanner.tsx`**
- Top banner ("Install taOS talk for quick access • Install / Not now").
- Only renders when:
  - `useIsMobile()` is true (it's a phone-size viewport), AND
  - A `beforeinstallprompt` event has fired (browser considers install possible), AND
  - `window.matchMedia("(display-mode: standalone)")` is false (not already installed), AND
  - `localStorage["taos-install-dismissed"]` is not set, or older than 30 days.
- Install click → `event.prompt()`, await `event.userChoice`, hide regardless of outcome.
- "Not now" click → set `localStorage["taos-install-dismissed"] = String(Date.now())`, hide.
- Uses `role="region"` with `aria-label="Install prompt"`.

**`desktop/src/hooks/use-visual-viewport.ts`**
- Returns `{height: number, keyboardInset: number}`.
- Subscribes to `window.visualViewport.resize` and `scroll` events.
- `keyboardInset = max(0, window.innerHeight - visualViewport.height - visualViewport.offsetTop)`.
- SSR-safe fallback: returns `{height: window.innerHeight || 0, keyboardInset: 0}`.

### Mobile-conditional changes in MessagesApp

All changes gated on `const isMobile = useIsMobile();`.

**ThreadPanel (mobile):**
- When `openThread && isMobile`, render `<ThreadPanel>` as a full-screen takeover: `fixed inset-0 z-50 bg-shell-surface flex flex-col`.
- Header gets a back button (`◀`) that calls `closeThread()`. Channel name + thread author shown.
- The rest of the MessagesApp UI (channel header, message list, composer) is hidden while the takeover is up.
- When desktop, keeps existing right-side slide-over (unchanged).

To avoid touching `ThreadPanel` internals, wrap in a `<MobileThreadTakeover>` that reuses `ThreadPanel`'s body but swaps the outer chrome. Alternative: pass an `isFullscreen` prop to ThreadPanel. **Choose:** `isFullscreen` prop — one file, cleaner diff.

**MessageOverflowMenu (mobile):**
- When `overflowMenu && isMobile`, render the menu inside `<BottomSheet>`.
- Menu items keep their role="menuitem" and keyboard nav.
- Row height increases to `py-3` for thumb-size targets.
- Desktop path unchanged (dropdown anchored at cursor).

**Composer (mobile keyboard handling):**
- The composer row (`<div className="px-4 py-3 border-t ...">`) gets `style={{ paddingBottom: \`max(env(safe-area-inset-bottom), ${keyboardInset}px)\` }}`.
- `keyboardInset` from `use-visual-viewport`. When keyboard is up, composer floats above it; when down, respects the home-bar safe area.
- The message list bottom padding adds the same value so content doesn't scroll under the composer.
- Only applied on mobile — desktop stays with its current padding.

### ChatStandalone / chat.html updates

**`desktop/chat.html`** — add iOS splash screen meta tags:
```html
<link rel="apple-touch-startup-image" media="(device-width: 430px) and (device-height: 932px) and (-webkit-device-pixel-ratio: 3) and (orientation: portrait)" href="/static/splash-iphone-15-pro-max.png" />
<link rel="apple-touch-startup-image" media="(device-width: 393px) and (device-height: 852px) and (-webkit-device-pixel-ratio: 3) and (orientation: portrait)" href="/static/splash-iphone-15-pro.png" />
<link rel="apple-touch-startup-image" media="(device-width: 428px) and (device-height: 926px) and (-webkit-device-pixel-ratio: 3) and (orientation: portrait)" href="/static/splash-iphone-14-plus.png" />
<link rel="apple-touch-startup-image" media="(device-width: 390px) and (device-height: 844px) and (-webkit-device-pixel-ratio: 3) and (orientation: portrait)" href="/static/splash-iphone-14.png" />
```

Splash images generated from the 1024×1024 icon, centered on the app background color `#1a1b2e`. Plan includes the generation step.

**`desktop/src/ChatStandalone.tsx`:**
- Mount `<InstallPromptBanner>` inside the root div, above `<Suspense>`.
- Confirm `paddingTop: "env(safe-area-inset-top, 0px)"` is present (already is).

### Backend

No backend changes needed for Phase 1.

## Install prompt — detailed flow

```
App mounts → isMobile=true
         → useEffect: listen for `beforeinstallprompt`, call preventDefault,
           stash event in state
         → if dismissedAt < now - 30 days and standalone=false and stashedEvent
           → render banner

User clicks Install
         → event.prompt()
         → await event.userChoice  (resolved with { outcome: "accepted" | "dismissed" })
         → clear stashed event (browsers drop it anyway after use)
         → hide banner

User clicks Not now
         → localStorage["taos-install-dismissed"] = Date.now().toString()
         → hide banner

Later mounts within 30 days
         → banner suppressed

After 30 days
         → if event fires again, banner reappears
```

## Safe-area inset audit

Root container already uses `paddingTop: env(safe-area-inset-top, 0px)` in `ChatStandalone`. Additional inset usage:
- Composer: `paddingBottom: max(env(safe-area-inset-bottom), keyboardInset)`.
- Mobile thread takeover: `paddingTop: env(safe-area-inset-top)` on the takeover header.
- Bottom sheet: `paddingBottom: env(safe-area-inset-bottom)`.

## Error handling

- **`beforeinstallprompt` never fires** (iOS Safari, Firefox Android): banner never shows. No error, no console noise.
- **`event.prompt()` rejects** (already shown, or fires too fast): catch, dismiss state, move on.
- **`visualViewport` undefined** (rare on older Android WebViews): hook returns `{height: window.innerHeight, keyboardInset: 0}`; composer uses safe-area-bottom fallback only.
- **BottomSheet drag-to-dismiss on a pointer that cancels** (finger leaves screen): `pointercancel` listener snaps back.
- **Thread takeover opened on desktop then resized to mobile**: existing Phase 2b-1 thread panel already uses mobile-conditional rendering at open time; on resize, we accept a single re-render to swap layout (no cross-layout transition animation).

## Testing

**vitest (component):**
- `BottomSheet.test.tsx`:
  - renders children and drag handle
  - backdrop click calls onClose
  - Esc key calls onClose
  - pointer drag past threshold calls onClose
  - `open={false}` renders nothing
- `InstallPromptBanner.test.tsx`:
  - returns null when standalone matches
  - returns null when no `beforeinstallprompt` event stashed
  - returns null when `taos-install-dismissed` is recent
  - clicking Install calls `event.prompt()`
  - clicking Not now writes localStorage and hides
  - after 30 days elapsed, reappears when event fires again
- `use-visual-viewport.test.ts`:
  - returns `{height, keyboardInset: 0}` when keyboard is down
  - returns `keyboardInset > 0` when viewport shrinks

**Playwright mobile-viewport E2E** (gated on `TAOS_E2E_URL`):
- Sets viewport to 375×667 (iPhone SE).
- Thread takeover: hover/tap message → Reply in thread → assert full-screen takeover, back button returns to channel.
- Overflow bottom sheet: tap `⋯` → assert bottom sheet visible → tap Cancel / swipe dismiss → sheet closes.
- Install banner: on fresh localStorage, emulate `beforeinstallprompt` → assert banner visible → tap Not now → assert hidden.

## Rollout

Single PR off `feat/messages-pwa` (branched from master). Self-contained — no backend changes, no schema migration, no bundle breakage risk.

Backward compatibility: all changes are additive under `isMobile` conditionals; desktop behavior is unchanged.

## Out of scope (future phases)

- **Phase 2:** Service worker + offline shell caching (Workbox or hand-rolled). Stale-while-revalidate for bundles. Offline fallback screen.
- **Phase 3:** Push notifications (requires VAPID keys + backend subscription endpoint).
- **Later:** Pull-to-refresh on channel list, swipe-to-reply gesture, native share sheet integration, voice message recording.
