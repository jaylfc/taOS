from __future__ import annotations

import asyncio
import html
import json
import socket
from pathlib import Path
import logging
import os
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from tinyagentos.auth import (
    PIN_MAX_LEN,
    PIN_MIN_LEN,
    AuthStoreCorruptError,
    _PinAttemptLimiter,
    is_console_origin,
    validate_pin,
)
from tinyagentos.middleware.csrf import verify_csrf
from tinyagentos.routes.onscreen_keyboard import OSK_SCRIPT, osk_assets

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Brute-force rate limiter (in-memory, per-IP, fixed window)
# ---------------------------------------------------------------------------

_FAIL_COUNTER_MAX_KEYS = 10_000  # cap total tracked IPs to prevent unbounded growth


class _FailCounter:
    """Count failed attempts per key in a rolling window.

    Bounded to avoid memory leaks:
    - Expired entries (all timestamps outside the window) are dropped on access.
    - Total key count is capped at ``_FAIL_COUNTER_MAX_KEYS``; oldest-accessed
      entries are evicted first (LRU via OrderedDict).

    Thread-safe: all mutating operations are protected by a Lock.
    """

    def __init__(self, max_attempts: int = 5, window_seconds: int = 600):
        self._max = max_attempts
        self._window = window_seconds
        # key → list of failure timestamps; OrderedDict for LRU eviction
        self._log: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = threading.Lock()

    def _prune(self, key: str) -> None:
        """Must be called with self._lock held."""
        cutoff = time.monotonic() - self._window
        if key not in self._log:
            return
        self._log[key] = [t for t in self._log[key] if t > cutoff]
        if not self._log[key]:
            # All timestamps expired — drop the entry entirely
            del self._log[key]
        else:
            # Keep active entry fresh in LRU order
            self._log.move_to_end(key)

    def _ensure_capacity(self) -> None:
        """Must be called with self._lock held."""
        while len(self._log) >= _FAIL_COUNTER_MAX_KEYS:
            self._log.popitem(last=False)  # evict oldest-accessed

    def is_limited(self, key: str) -> bool:
        with self._lock:
            self._prune(key)
            return len(self._log.get(key, [])) >= self._max

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._prune(key)
            if key not in self._log:
                self._ensure_capacity()
                self._log[key] = []
            self._log[key].append(time.monotonic())
            self._log.move_to_end(key)

    def reset(self, key: str) -> None:
        with self._lock:
            self._log.pop(key, None)

    def count(self, key: str) -> int:
        """Current failure count for the key within the window."""
        with self._lock:
            self._prune(key)
            return len(self._log.get(key, []))


_login_limiter = _FailCounter(max_attempts=5, window_seconds=600)
_complete_limiter = _FailCounter(max_attempts=5, window_seconds=600)

# Hard ceiling: at/above this we reject BEFORE verifying the password, which
# bounds BOTH brute-force guesses and the bcrypt cost per window+IP. Kept just
# above the soft limit (5): a user who fat-fingers a few times then types the
# right password still gets in (within the first ~10 attempts), while an attacker
# is throttled to at most this many guesses+hashes per 10-minute window per IP.
# Letting a correct password through inherently requires checking it, so a small
# increase over the soft limit is the necessary cost of not locking out real
# users -- keep this tight, not large.
_LOGIN_HARD_MAX = 10
_LOCKOUT_MSG = "Too many failed attempts. Wait a few minutes, then sign in with your correct password."

# Self-contained HTML pages for the auth flow.
#
# These are deliberately JS-free and CDN-free so they work on any device
# even when the SPA bundle is broken or stale. After successful submit
# the server redirects to /desktop where the SPA takes over.
_AUTH_BASE_STYLE = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: env(safe-area-inset-top, 16px) env(safe-area-inset-right, 16px) env(safe-area-inset-bottom, 16px) env(safe-area-inset-left, 16px);
  background: linear-gradient(160deg, #141415 0%, #1a1a1d 45%, #202024 100%);
  color: rgba(255, 255, 255, 0.85);
  font: 14px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
.card {
  width: 100%;
  max-width: 380px;
  padding: 28px 24px;
  border: 1px solid rgba(255,255,255,0.10);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.04);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
}
.brand {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  margin-bottom: 22px;
}
/* The on-screen keyboard takes roughly half of a 600px panel, so while it is
   open the card sheds decorative height to keep its actions above the keys.
   The body is scrollable in that state either way, but a sign-in the user has
   to scroll to reach is a sign-in a kiosk user will not find. */
body.osk-open .card { padding: 16px 20px 12px; }
body.osk-open label.field { margin-bottom: 6px; }
body.osk-open .brand { margin-bottom: 8px; gap: 6px; }
body.osk-open .brand h1.wordmark { font-size: 26px; }
body.osk-open .brand p { display: none; }
/* The wordmark IS the brand mark on these pages: the product name set as type,
   carrying the card visually on its own. It replaces an earlier drawn glyph —
   a rounded square with a centre dot and an X through it — which on a sign-in
   screen read as an error badge or a close affordance rather than a logo.
   Plain ASCII in the page's own font stack, so there is no webfont to fetch
   and no code point that can land as TOFU on a device missing a covering font,
   which is what the JS-free, CDN-free auth pages need. */
.brand h1.wordmark {
  margin: 0;
  font-size: 34px;
  font-weight: 600;
  letter-spacing: -0.02em;
  line-height: 1.1;
}
.brand p { margin: 0; font-size: 12px; color: rgba(255,255,255,0.5); text-align: center; }
label.field {
  display: block;
  margin-bottom: 12px;
}
label.field > span {
  display: block;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: rgba(255,255,255,0.4);
  margin-bottom: 4px;
}
input[type="text"], input[type="password"], input[type="email"] {
  width: 100%;
  padding: 10px 14px;
  border-radius: 10px;
  border: 1px solid rgba(255,255,255,0.10);
  background: #171717;
  color: rgba(255,255,255,0.85);
  font: inherit;
  outline: none;
}
input:focus { border-color: rgba(139,146,163,0.5); }
.field .hint {
  display: block;
  font-size: 10px;
  color: rgba(255,255,255,0.3);
  margin-top: 4px;
}
.checkbox {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: rgba(255,255,255,0.55);
  margin-top: 14px;
}
button[type="submit"] {
  width: 100%;
  margin-top: 18px;
  padding: 11px 14px;
  border: 0;
  border-radius: 10px;
  background: #8b92a3;
  color: #fff;
  font: inherit;
  font-weight: 600;
  cursor: pointer;
  transition: filter 120ms;
}
button[type="submit"]:hover { filter: brightness(1.1); }
button[type="submit"]:disabled { opacity: 0.4; cursor: not-allowed; }
.error {
  margin: 0 0 12px;
  padding: 10px 12px;
  border-radius: 8px;
  background: rgba(239, 68, 68, 0.15);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: #fca5a5;
  font-size: 12px;
  text-align: center;
}
"""


_PIN_PANEL_STYLE = """
.pin-panel { margin-bottom: 14px; }
.pin-panel[hidden], .pw-panel[hidden] { display: none; }
/* The error paragraph is a live region, so it has to exist before it has
   anything to say — but `.error` carries a red background and border, and an
   empty one renders as a bare red slab above the keypad on a page that has not
   failed at anything yet. */
#pin-error:empty { display: none; }
/* The primary action is type=button (a submit would post the password form),
   so it misses `button[type="submit"]` styling entirely and lands as a ~21px
   native button — on a touchscreen, under the 44px this keyboard's own floor
   requires. */
#pin-submit {
  width: 100%; margin-top: 6px; padding: 12px 14px; min-height: 44px;
  border: 0; border-radius: 10px;
  background: #8b92a3; color: #fff;
  font: inherit; font-weight: 600; cursor: pointer;
  transition: filter 120ms;
}
#pin-submit:hover { filter: brightness(1.1); }
#pin-submit:focus-visible { outline: 3px solid #4c9aff; outline-offset: 2px; }
.pin-dots { display: flex; gap: 10px; justify-content: center; margin: 8px 0 14px; }
body.osk-open .pin-dots { margin: 2px 0 8px; }
.pin-dot {
  width: 14px; height: 14px; border-radius: 50%;
  border: 1px solid rgba(255,255,255,0.45); background: transparent;
}
.pin-dot[data-filled="1"] { background: #4c9aff; border-color: #4c9aff; }
.method-switch {
  display: block; width: 100%; margin-top: 12px; padding: 12px;
  min-height: 44px; background: none; border: none; cursor: pointer;
  color: #9ecbff; font: inherit; text-decoration: underline;
}
.method-switch:focus-visible { outline: 3px solid #4c9aff; outline-offset: 2px; }
"""


# Phone lock-screen chrome. Only ever rendered for a console-local request that
# already qualifies for PIN sign-in, so a LAN browser still gets the plain card.
_LOCK_SCREEN_STYLE = """
/* A handset is tall, and a sign-in card floating in the middle of 2400px of
   glass reads as a web page, not as an OS. The lock screen splits the height
   the way iOS and Android do: status up top, passcode down by the thumb. */
/* THE COLUMN WIDTH. Everything stacked down the middle of this screen -- the
   agent islands, the notification groups, the view row, the stats card -- is
   one column, and it was six copies of the same number. A token, so widening
   the islands cannot leave the row above them at the old width.

   It is a cap, not a width: the elements are `width: 100%` under it. On
   spacewar it BINDS, which is why this is the knob that moves them. Measured
   on the device rather than assumed: sway drives DSI-1 at 1080x2400 with
   `"scale": 2.0`, so the page gets a 540px CSS viewport, and .lockscreen's
   10px side padding leaves 520px of content. Widening the padding instead
   would only have moved the gutters -- the cap would still have held the
   cards at their old width. */
:root { --ls-card-w: 436px; }
/* THE CAMERA LINE. The status row -- the taOS wordmark and the battery -- sits
   level with the middle of the punch-hole camera, so the top edge reads as one
   line of hardware and text rather than as text floating above a hole.

   MEASURED, not nudged. There is no cutout information anywhere on this device
   (nothing under /sys/firmware/devicetree/base, nothing in the pmaports device
   package), so the number comes from the panel's own vendor description: the
   stock Nothing OS overlay FrameworksResCommon_Sys_Spacewar.apk carries

     config_mainBuiltInDisplayCutout = M89.3,42.31 m0,26.19 a26.19,26.19 0 1,0
                                       52.38,0 a26.19,26.19 0 1,0 -52.38,0 Z @left

   which is a circle of r=26.19 centred at (115.49, 68.50) in PHYSICAL pixels
   from the top-left of the panel -- Android's path is in px unless it ends
   @dp, and @left only moves the origin. The identical string is in LineageOS's
   spacewar device tree, so it is two independent sources, not one dump.

   68.50 physical px / 2 = 34.25 CSS px, because sway drives DSI-1 at
   "scale": 2.0 (measured on the handset, same reading as --ls-card-w rests on).
   The row is given an explicit height so "centred" is arithmetic rather than a
   guess about line-height: its centre is padding-top + half of it. */
:root { --ls-cam-centre-y: 34.25px; --ls-status-h: 20px; }
/* display:block, NOT a flex row. The base stylesheet makes <body> a centring
   flex container for the sign-in card, and the on-screen keyboard appends its
   panel, toggle and live region to <body> -- under a flex ROW those become
   SIBLING FLEX ITEMS of the lock screen, squeezing it to a fraction of the
   width (its max-width never binds) and spilling a stray control above the
   clock. As a block the lock screen owns the full width and those appended
   elements sit out of flow where they belong. */
body.lockscreen-on {
  display: block; padding: 0; overflow: hidden;
  /* This is an OS screen, not a web page. Without these a press-and-hold on an
     island does what a browser does -- starts a text selection and raises the
     copy/share callout -- which is exactly the gesture the islands bind, so the
     long press fought the selection every time. Selection stays ON inside the
     conversation, where copying a message is a reasonable thing to want. */
  -webkit-user-select: none; user-select: none;
  -webkit-touch-callout: none;
}
.ls-msg, .ls-compose-input { -webkit-user-select: text; user-select: text; }
/* The on-screen keyboard's layout override (padding-bottom + flex-start) is for
   the password form. The lock screen carries its own keypad and never opens it,
   but be explicit so a stray .osk-open cannot re-anchor the screen to the top. */
body.lockscreen-on.osk-open { display: block; padding-bottom: 0 !important; overflow-y: hidden; }
.lockscreen {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100vh;
  height: 100dvh;
  /* Top padding puts the status row's CENTRE on the camera line; it is not a
     margin chosen by eye. See --ls-cam-centre-y. */
  padding: calc(env(safe-area-inset-top, 0px) + var(--ls-cam-centre-y) - var(--ls-status-h) / 2)
           10px
           calc(env(safe-area-inset-bottom, 0px) + 18px);
  gap: 16px;
}
/* The clock keeps its distance from the status bar rather than the screen top. */
.lockscreen .ls-head { margin-top: 4vh; }
/* Top block: the glanceable half. */
.ls-head {
  display: flex; flex-direction: column; align-items: center; gap: 4px;
  align-self: stretch;
  /* min-height:0 so this block is allowed to shrink instead of pushing the
     keypad off-screen; a flex item's default min-height:auto refuses to. */
  min-height: 0;
}
.ls-time {
  font-size: clamp(56px, 17vw, 88px);
  font-weight: 250;
  line-height: 1;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
  color: #fff;
}
.ls-date { font-size: 16px; font-weight: 500; color: rgba(255,255,255,0.62); }
/* Weather. It sits between the clock and the agent islands because that is the
   order the eye already reads this screen in: what time is it, what is it like
   outside, what are my agents doing. A fixed location (Liverpool) in Celsius
   and mph -- this device does not ask for geolocation, and a lock screen that
   prompted for it before sign-in would be asking the wrong question of the
   wrong person.

   Everything here is fetched SERVER-SIDE by /auth/lock-weather. A fetch from
   the page would hand the device's address to a third party on every
   lock-screen paint, from a surface that renders before anyone has signed in. */
.ls-weather {
  display: flex; align-items: center; gap: 12px;
  margin-top: 10px; padding: 0 4px;
  transition: filter 320ms cubic-bezier(0.32, 0.72, 0, 1), opacity 320ms ease;
}
.lockscreen:not([data-sheet="none"]) .ls-weather { filter: blur(7px); opacity: 0.55; }
/* No pill, no card. Up here this is a continuation of the clock -- a bordered
   box around it would read as the first of a row of widgets and pull the eye
   down off the time. */
.ls-weather-icon { flex: none; width: 34px; height: 34px; color: rgba(255,255,255,0.92); }
.ls-weather-icon svg { width: 100%; height: 100%; display: block; }
.ls-weather-icon svg [fill="none"], .ls-weather-icon svg path, .ls-weather-icon svg circle {
  fill: none; stroke: currentColor; stroke-width: 1.7;
  stroke-linecap: round; stroke-linejoin: round;
}
.ls-weather-read { display: flex; flex-direction: column; align-items: flex-start; gap: 1px; }
.ls-weather-now { display: flex; align-items: baseline; gap: 9px; }
.ls-weather-temp {
  font-size: 26px; font-weight: 300; line-height: 1;
  letter-spacing: -0.01em; font-variant-numeric: tabular-nums; color: #fff;
}
.ls-weather-label { font-size: 15px; font-weight: 500; color: rgba(255,255,255,0.74); }
.ls-weather-sub {
  font-size: 13px; color: rgba(255,255,255,0.52);
  font-variant-numeric: tabular-nums;
}
/* Status bar. The product name and the battery are STATUS, not content: they
   belong on the top edge where a phone puts them, not stacked under the date
   competing with the clock. The device's hostname is gone -- it told the
   person holding their own phone something they already know. */
.ls-statusbar {
  /* Three columns, brand in the middle: a flex row would re-centre the brand
     every time the battery string changed width (9% -> 100%). */
  display: grid; grid-template-columns: 1fr auto 1fr; align-items: center;
  align-self: stretch; flex: none;
  /* An explicit height, so the camera line above is arithmetic and not a bet on
     what line-height resolves to. align-items:center already centres the text
     inside it. */
  min-height: var(--ls-status-h);
  width: 100%; padding: 0 6px; gap: 10px;
  transition: filter 320ms cubic-bezier(0.32, 0.72, 0, 1), opacity 320ms ease;
}
.lockscreen:not([data-sheet="none"]) .ls-statusbar { filter: blur(7px); opacity: 0.55; }
/* Text, not chips. Up here these are a status line the eye skips over; a
   bordered translucent pill around each one turns the top edge into two
   buttons that cannot be pressed. */
.ls-statusbar .ls-widget {
  padding: 0; border: 0; background: none;
  backdrop-filter: none; -webkit-backdrop-filter: none;
  font-size: 14px; color: rgba(255,255,255,0.58);
}
.ls-statusbar .ls-widget b { color: rgba(255,255,255,0.80); }
.ls-brand { grid-column: 2; justify-self: center; }
.ls-brand b { font-weight: 700; }
#ls-battery { grid-column: 3; justify-self: end; margin-right: 4px; }
/* Widgets are CLIENT-SIDE only (clock, battery) plus the device's own name.
   Nothing here reads the account or its data: this surface is shown BEFORE
   authentication, so anything account-derived would be a pre-auth leak. */
.ls-widget {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 7px 12px; border-radius: 999px;
  border: 1px solid rgba(255,255,255,0.10);
  background: rgba(255,255,255,0.05);
  font-size: 13px; color: rgba(255,255,255,0.70);
  backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
}
.ls-widget b { font-weight: 600; color: rgba(255,255,255,0.88); }
/* Agent islands. Each running agent is its own floating pill -- a row of
   identical cards would make three agents look like a list of settings; a
   detached island reads as a thing that is alive and can speak up on its own.
   Elevation is declared ONCE, as a shadow: no hairline border under it. */
.ls-islands {
  display: flex; flex-direction: column; align-items: center; gap: 14px;
  width: 100%; align-self: stretch; margin-top: 16px;
}
/* #ls-agents is the box the islands are appended INTO. Without a width of its
   own it is a shrink-to-fit block inside a centre-aligned flex column, so every
   island's width:100% and max-width resolved against its CONTENT box -- the cap
   could never bind and side padding changed nothing. It carries the stack. */
.ls-agents {
  display: flex; flex-direction: column; align-items: center; gap: 14px;
  width: 100%; align-self: stretch;
  min-height: 0;
}
/* THE SCROLL SEAM. The agent stack used to be the scrolling element itself.
   With a second stack (notifications) under it that is wrong twice over: the
   two would scroll independently -- islands sliding under a pinned pile of
   banners -- and the notifications, being outside the only scrollable box,
   would push the passcode sheet off the bottom of a full screen instead of
   scrolling.

   So the scroll moves up one level, to the box that holds BOTH stacks. They
   travel together as one feed, and this is still the only part of the lock
   screen allowed to overflow: min-height:0 keeps it shrinking rather than
   growing the column, so the unlock bar and the passcode keep their room. */
.ls-feed {
  display: flex; flex-direction: column; align-items: center; gap: 14px;
  width: 100%; align-self: stretch;
  min-height: 0;
  overflow-y: auto;
  /* A visible scrollbar on a lock screen reads as a web page. */
  scrollbar-width: none;
  -ms-overflow-style: none;
  overscroll-behavior: contain;
}
.ls-feed::-webkit-scrollbar { width: 0; height: 0; display: none; }
/* The cut edge. With the bar hidden, a scrolling feed ends in a card sliced
   clean in half against the unlock bar, which reads as a rendering fault rather
   than as more content. A fade says "this continues".
   Applied only while the feed ACTUALLY overflows -- an unconditional mask would
   eat the bottom of the last card on a device with one agent and no
   notifications, where there is nothing to scroll to. */
.ls-feed[data-fade="bottom"] {
  -webkit-mask-image: linear-gradient(to bottom, #000 calc(100% - 34px), transparent 100%);
  mask-image: linear-gradient(to bottom, #000 calc(100% - 34px), transparent 100%);
}
.ls-feed[data-fade="top"] {
  -webkit-mask-image: linear-gradient(to bottom, transparent 0, #000 26px);
  mask-image: linear-gradient(to bottom, transparent 0, #000 26px);
}
.ls-feed[data-fade="both"] {
  -webkit-mask-image: linear-gradient(to bottom, transparent 0, #000 26px, #000 calc(100% - 34px), transparent 100%);
  mask-image: linear-gradient(to bottom, transparent 0, #000 26px, #000 calc(100% - 34px), transparent 100%);
}
/* THE VIEW ROW. Seven icons that choose what the feed shows.

   It sits OUTSIDE .ls-feed, above it, and that placement is load-bearing twice
   over. It must not scroll away with the feed it steers. And .ls-feed is the
   region where an upward drag is vetoed from unlocking the phone (see the
   touchstart veto in the script): a row INSIDE the feed would mean a swipe that
   begins on a view icon cannot unlock, which is the bug that veto exists to
   avoid re-creating somewhere else. */
.ls-views {
  display: flex; align-items: center; justify-content: center;
  gap: 2px; flex: 0 0 auto;
  margin-top: 18px;
  /* The row is fixed chrome; only the feed under it is allowed to overflow. */
  width: 100%; max-width: var(--ls-card-w); align-self: center;
}
.ls-view-tab {
  -webkit-appearance: none; appearance: none;
  background: none; border: 0; padding: 7px 8px 5px;
  display: flex; flex-direction: column; align-items: center; gap: 4px;
  flex: 1 1 0; min-width: 0;
  cursor: pointer; color: rgba(255,255,255,0.42);
  -webkit-tap-highlight-color: transparent;
  border-radius: 12px;
  transition: color 160ms ease, transform 160ms cubic-bezier(0.32,0.72,0,1);
}
.ls-view-tab:hover { color: rgba(255,255,255,0.68); }
.ls-view-tab:active { transform: scale(0.92); }
.ls-view-tab:focus-visible { outline: 3px solid #4c9aff; outline-offset: 2px; }
.ls-view-tab[aria-selected="true"] { color: rgba(255,255,255,0.96); }
/* Stroke, not fill: these are line-art marks in the same set as the framework
   badges. currentColor is what makes the selected state a single colour change
   rather than seven hand-tuned icon states. */
.ls-view-icon {
  fill: none; stroke: currentColor; stroke-width: 1.7;
  stroke-linecap: round; stroke-linejoin: round;
  display: block; flex: 0 0 auto;
}
/* The selected marker. A 3px dot rather than an underline or a pill: the row
   sits directly above a feed of rounded cards, and a filled pill behind the
   icon would read as an eighth card. */
.ls-view-dot {
  width: 3px; height: 3px; border-radius: 50%;
  background: currentColor; opacity: 0;
  transition: opacity 160ms ease;
}
.ls-view-tab[aria-selected="true"] .ls-view-dot { opacity: 1; }
/* A view with something waiting carries a dot at rest, in the attention amber
   the islands already use, so "there is mail" does not need a number. */
.ls-view-tab[data-badge="1"]:not([aria-selected="true"]) .ls-view-dot {
  opacity: 1; background: #ffb020;
}
@media (prefers-reduced-motion: reduce) {
  .ls-view-tab, .ls-view-dot { transition: none; }
  .ls-view-tab:active { transform: none; }
}

/* PANEL VISIBILITY -- deliberately explicit, and NOT left to [hidden].
   .ls-islands and .ls-notifs each declare display:flex, and an author rule beats
   the user-agent's [hidden]{display:none} whatever its specificity, so `hidden`
   alone does NOT hide a populated panel here. That has been harmless while the
   two panels were only ever hidden when empty; with seven panels holding real
   content it would show all of them at once. The attribute stays (it is what
   assistive tech reads) and this gives it teeth.

   TWO attributes, because there are two owners. `hidden` means "this panel has
   nothing in it" and belongs to the 15s poll, which already sets it on the
   agents and alerts panels as their content comes and goes. `data-off` means
   "this is not the view you chose" and belongs to the switcher. If the switcher
   drove `hidden` instead, the very next poll would un-hide the agents panel
   underneath whichever view the user was actually on. Shown only when NEITHER
   is set. */
.ls-feed > [data-view][hidden],
.ls-feed > [data-view][data-off] { display: none; }
/* The five panels this row introduces. The two that predate it keep their own
   layout rules above. */
.ls-panel {
  display: flex; flex-direction: column; align-items: center; gap: 12px;
  width: 100%; align-self: stretch;
}
/* A panel with nothing in it yet says so, rather than showing the user a blank
   screen and leaving them to wonder whether it failed to load. */
.ls-empty {
  display: flex; flex-direction: column; align-items: center; gap: 6px;
  padding: 34px 18px; text-align: center;
  color: rgba(255,255,255,0.38);
  font-size: 14px; line-height: 1.45;
}
.ls-empty b { display: block; font-weight: 600; color: rgba(255,255,255,0.62); font-size: 15px; }

/* THE STATS CARD. One card in the same material as an island, so the system
   readings read as another thing this screen shows rather than as a settings
   page that wandered in. */
.ls-stat-card {
  width: 100%; max-width: var(--ls-card-w);
  padding: 16px 18px;
  display: flex; flex-direction: column; gap: 14px;
  background: rgba(255,255,255,0.06);
  -webkit-backdrop-filter: blur(18px) saturate(1.3);
  backdrop-filter: blur(18px) saturate(1.3);
  border-radius: 22px;
  box-shadow: 0 10px 30px rgba(0,0,0,0.42);
  animation: ls-island-in 520ms cubic-bezier(0.16, 1, 0.3, 1) backwards;
}
.ls-stat { display: flex; flex-direction: column; gap: 7px; }
.ls-stat-top { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.ls-stat-label { font-size: 13px; color: rgba(255,255,255,0.56); }
/* Tabular figures: without them a changing percentage makes the whole row
   twitch left and right every poll. */
.ls-stat-value {
  font-size: 15px; font-weight: 600; color: rgba(255,255,255,0.94);
  font-variant-numeric: tabular-nums;
}
.ls-stat-track {
  height: 4px; border-radius: 999px; overflow: hidden;
  background: rgba(255,255,255,0.12);
}
.ls-stat-fill {
  height: 100%; border-radius: 999px;
  background: rgba(255,255,255,0.82);
  transition: width 420ms cubic-bezier(0.16, 1, 0.3, 1);
}
/* The honesty line. It is small, but it is the point of this panel: it says
   what the number is and what the hardware refuses to tell us. */
.ls-stat-note {
  font-size: 11.5px; line-height: 1.45;
  color: rgba(255,255,255,0.38);
  max-width: var(--ls-card-w); text-align: center;
}
.ls-stat-card .ls-stat-note { text-align: left; }
.ls-chips {
  display: flex; flex-wrap: wrap; gap: 7px; justify-content: center;
  width: 100%; max-width: var(--ls-card-w);
}
.ls-chip {
  font-size: 12px; padding: 5px 11px; border-radius: 999px;
  background: rgba(255,255,255,0.07);
  color: rgba(255,255,255,0.52);
  display: inline-flex; align-items: center; gap: 6px;
}
/* The dot IS the state; the word next to it is the same fact in text, for
   anyone who cannot tell the two greens apart. */
.ls-chip::before {
  content: ""; width: 6px; height: 6px; border-radius: 50%;
  background: rgba(255,255,255,0.28);
}
.ls-chip[data-on="1"] { color: rgba(255,255,255,0.78); }
.ls-chip[data-on="1"]::before { background: #46d17f; }
@media (prefers-reduced-motion: reduce) {
  .ls-stat-card { animation: none; }
  .ls-stat-fill { transition: none; }
}

/* NOTIFICATIONS. Collated the way a phone does it: one stack per source, the
   newest banner on top and the rest of that source's banners tucked behind it
   as peeking edges. A flat list of every notification would bury the agent
   islands under mail, and the islands are what this screen is for.

   Pressing a stack fans it out in place. That is ALL a press does: this screen
   renders before sign-in, so there is nothing here to open into. */
.ls-notifs {
  display: flex; flex-direction: column; align-items: center; gap: 12px;
  width: 100%; align-self: stretch;
}
.ls-notif-group {
  position: relative;
  width: 100%; max-width: var(--ls-card-w);
  animation: ls-island-in 520ms cubic-bezier(0.16, 1, 0.3, 1) backwards;
}
/* Collapsed: only the newest card is in flow, so the group is exactly one card
   tall and the ones behind it cannot change its height however long they are.
   The padding is the gap the peeking edges show through. */
.ls-notif-group:not([data-open="1"]) { padding-bottom: 13px; }
.ls-notif-group:not([data-open="1"]) .ls-notif { position: relative; z-index: 2; }
.ls-notif-group:not([data-open="1"]) .ls-notif ~ .ls-notif {
  position: absolute; left: 0; right: 0; top: 0; height: 100%;
  overflow: hidden; pointer-events: none;
}
.ls-notif-group:not([data-open="1"]) .ls-notif:nth-child(2) {
  transform: translateY(7px) scale(0.955); opacity: 0.85; z-index: 1;
}
.ls-notif-group:not([data-open="1"]) .ls-notif:nth-child(3) {
  transform: translateY(13px) scale(0.912); opacity: 0.55; z-index: 0;
}
/* A fourth card would peek out from under a stack that already reads as deep.
   The count on the newest card is what says how many there really are. */
.ls-notif-group:not([data-open="1"]) .ls-notif:nth-child(n+4) { opacity: 0; z-index: 0; }
.ls-notif-group[data-open="1"] { display: flex; flex-direction: column; gap: 8px; }
.ls-notif {
  display: flex; align-items: flex-start; gap: 10px;
  padding: 10px 13px;
  border-radius: 20px;
  text-align: left;
  background: rgba(30, 30, 34, 0.92);
  box-shadow: 0 6px 18px -6px rgba(0, 0, 0, 0.75);
  backdrop-filter: blur(24px) saturate(1.3);
  -webkit-backdrop-filter: blur(24px) saturate(1.3);
  transition: transform 340ms cubic-bezier(0.32, 0.72, 0, 1),
              opacity 260ms ease;
}
.ls-notif-group:focus-visible { outline: 3px solid #4c9aff; outline-offset: 4px; border-radius: 22px; }
.ls-notif-group:focus { outline: none; }
/* The app tile. A monogram on a tinted square is the shape a phone uses for an
   app, and it keeps every source the same size whether it has a real glyph or
   just a letter. */
.ls-notif-tile {
  flex: none; width: 30px; height: 30px; border-radius: 9px;
  display: flex; align-items: center; justify-content: center;
  font-size: 13px; font-weight: 700; color: #fff;
  background: var(--ls-n, #4c9aff);
}
.ls-notif-tile svg { width: 17px; height: 17px; fill: none; stroke: #fff; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.ls-notif-body { min-width: 0; flex: 1; }
.ls-notif-meta {
  display: flex; align-items: baseline; gap: 6px;
  font-size: 11px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase;
  color: rgba(255,255,255,0.45);
}
.ls-notif-app { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ls-notif-when { margin-left: auto; flex: none; text-transform: none; letter-spacing: 0; font-weight: 500; }
.ls-notif-title {
  margin-top: 2px;
  font-size: 14px; font-weight: 600; color: #fff;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.ls-notif-text {
  margin-top: 1px;
  font-size: 13px; line-height: 1.35; color: rgba(255,255,255,0.68);
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden;
}
.ls-notif-group[data-open="1"] .ls-notif-text { -webkit-line-clamp: 6; }
/* The pile count. Only meaningful while the stack is closed -- once it is fanned
   out the cards themselves are the count. */
.ls-notif-count {
  flex: none; padding: 1px 7px; border-radius: 999px;
  background: rgba(255,255,255,0.13); color: rgba(255,255,255,0.72);
  font-size: 11px; font-weight: 600; letter-spacing: 0;
}
.ls-notif-group[data-open="1"] .ls-notif-count { display: none; }
.ls-island {
  display: flex; align-items: center; gap: 10px;
  width: 100%; max-width: var(--ls-card-w);
  padding: 7px 16px 7px 7px;
  border-radius: 999px;
  background: rgba(30, 30, 34, 0.92);
  box-shadow: 0 6px 18px -6px rgba(0, 0, 0, 0.75);
  backdrop-filter: blur(24px) saturate(1.3);
  -webkit-backdrop-filter: blur(24px) saturate(1.3);
  /* Entrance: already-visible default, one authored moment, exponential ease. */
  animation: ls-island-in 520ms cubic-bezier(0.16, 1, 0.3, 1) backwards;
}
.ls-island:nth-child(2) { animation-delay: 70ms; }
.ls-island:nth-child(3) { animation-delay: 140ms; }
@keyframes ls-island-in {
  from { opacity: 0; transform: translateY(6px) scale(0.96); filter: blur(3px); }
  to   { opacity: 1; transform: none; filter: none; }
}
/* The avatar and the harness mark sit as a pair, the mark tucked over the
   avatar's edge the way a platform badge does -- two separate circles side by
   side read as two unrelated buttons. */
.ls-marks { position: relative; flex: none; width: 52px; height: 34px; }
.ls-avatar {
  position: absolute; inset: 0 auto 0 0;
  width: 34px; height: 34px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 13px; font-weight: 600; letter-spacing: 0.01em; color: #fff;
  background: linear-gradient(145deg, var(--ls-a, #4c9aff), var(--ls-b, #2f6fd0));
}
.ls-fw {
  position: absolute; right: 0; bottom: -1px;
  width: 22px; height: 22px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  background: #0f0f12;
  /* The ring is the separation from the avatar behind it, not decoration. */
  box-shadow: 0 0 0 2px rgba(30, 30, 34, 0.92);
  color: rgba(255, 255, 255, 0.86);
}
.ls-fw svg { width: 15px; height: 15px; }
/* A real store logo fills the badge; the drawn marks are inset because they are
   line art and need the breathing room a solid mark does not. */
.ls-fw-img { background: #fff; overflow: hidden; }
.ls-fw-img img { width: 100%; height: 100%; object-fit: cover; display: block; }
/* A photo fills the circle edge to edge; the monogram gradient stays behind it
   as the loading ground rather than a grey box. */
.ls-avatar-img { width: 100%; height: 100%; border-radius: 50%; object-fit: cover; display: block; }
/* The OS's own agent carries the product mark. A mark is not a portrait: no
   circular crop, no gradient ground and no monogram behind it -- those exist to
   make arbitrary photos and initials sit together, and cropping a landscape
   wordmark into a 34px circle would cut the word in half. It gets a wider slot
   and is contained inside it, while the harness badge stays exactly where it is
   on every other island. */
/* Same circle, same size as every other avatar -- a row of pills whose first
   mark is a different shape and size reads as a mis-render, not as emphasis.
   The only differences are the ground (a flat dark disc rather than the
   per-name gradient, which is there to make INITIALS legible) and `contain`,
   because the wordmark is landscape and `cover` would crop it to "aO". */
.ls-island[data-system="1"] .ls-avatar { background: #0f0f12; }
.ls-island[data-system="1"] .ls-avatar-img { object-fit: contain; }
.ls-sprite { position: absolute; width: 0; height: 0; overflow: hidden; }
/* One stroke weight and one cap style across the marks. */
.ls-fw svg, .ls-sprite {
  fill: none; stroke: currentColor; stroke-width: 1.7;
  stroke-linecap: round; stroke-linejoin: round;
}
.ls-body { min-width: 0; flex: 1 1 auto; }
.ls-name {
  font-size: 13.5px; font-weight: 600; color: rgba(255,255,255,0.92);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  letter-spacing: -0.01em;
}
.ls-status {
  font-size: 11.5px; color: rgba(255,255,255,0.52);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
/* Live pip: present only while the agent is actually doing something. */
.ls-pip {
  flex: none; width: 7px; height: 7px; border-radius: 50%;
  background: #3ddc84;
}
.ls-island[data-state="idle"] .ls-pip { background: rgba(255,255,255,0.28); }
/* ATTENTION. The island itself breathes -- a ring that grows out of its own
   silhouette, so it is legible from across a room without reading a word. */
.ls-island[data-attention="1"] {
  animation: ls-island-in 520ms cubic-bezier(0.16, 1, 0.3, 1) backwards,
             ls-attention 2.6s ease-out 520ms infinite;
}
.ls-island[data-attention="1"] .ls-pip { background: #ffb020; }
.ls-island[data-attention="1"] .ls-status { color: rgba(255, 176, 32, 0.92); }
@keyframes ls-attention {
  0%   { box-shadow: 0 6px 18px -6px rgba(0,0,0,0.75), 0 0 0 0 rgba(255,176,32,0.45); }
  70%  { box-shadow: 0 6px 18px -6px rgba(0,0,0,0.75), 0 0 0 10px rgba(255,176,32,0); }
  100% { box-shadow: 0 6px 18px -6px rgba(0,0,0,0.75), 0 0 0 0 rgba(255,176,32,0); }
}
@media (prefers-reduced-motion: reduce) {
  .ls-island, .ls-island[data-attention="1"] { animation: none; }
  .ls-island[data-attention="1"] { outline: 2px solid rgba(255,176,32,0.7); outline-offset: 2px; }
}
/* Scheduled tasks stay quieter than the agents: they are context, not actors. */
.ls-tasks { width: 100%; align-self: stretch; }
.ls-tasks:not(:empty) {
  display: flex; flex-direction: column; align-items: center; gap: 4px;
  margin-top: 6px; width: 100%;
}
.ls-task {
  display: flex; justify-content: space-between; gap: 10px;
  padding: 0 18px; font-size: 11.5px; color: rgba(255,255,255,0.42);
}
.ls-task-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ls-empty { font-size: 13px; color: rgba(255,255,255,0.40); padding: 2px 0; }
/* The spacer, not a margin: it collapses first when the viewport is short, so
   the keypad stays reachable on a small phone instead of being pushed off. */
.ls-spacer { flex: 1 1 auto; min-height: 8px; }
.ls-foot { display: flex; flex-direction: column; align-items: center; gap: 10px; align-self: stretch; flex: none; }
.ls-hint { margin: 0; font-size: 14px; color: rgba(255,255,255,0.55); }
/* Keypad: 3 columns, targets well above the 44px minimum because this is the
   one control on the device that must work with a thumb, in the dark. */
.ls-pad {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
  width: 100%;
  max-width: 300px;
  margin-top: 4px;
}
.ls-key {
  aspect-ratio: 1 / 1;
  max-height: 74px;
  border-radius: 50%;
  border: 1px solid rgba(255,255,255,0.10);
  background: rgba(255,255,255,0.07);
  color: #fff;
  font: 300 30px/1 inherit;
  font-variant-numeric: tabular-nums;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  transition: background 90ms ease, transform 90ms ease;
}
.ls-key:active { background: rgba(255,255,255,0.20); transform: scale(0.94); }
.ls-key:focus-visible { outline: 3px solid #4c9aff; outline-offset: 2px; }
/* Backspace and the password escape are actions, not digits: no filled pill, so
   the ten digits stay the obvious targets. */
.ls-key[data-action] { background: none; border-color: transparent; font-size: 22px; }
.ls-key[data-action]:active { background: rgba(255,255,255,0.12); }
.ls-key.ls-key-blank { visibility: hidden; pointer-events: none; }
/* The lock screen supplies its own dots/keypad, so the card chrome that the
   plain sign-in page needs is not wanted here. */
.lockscreen .pin-panel:not([hidden]) { display: contents; }
.lockscreen #pin-submit {
  width: 100%; max-width: 300px; margin-top: 4px;
  border-radius: 999px; background: rgba(255,255,255,0.14);
  border: 1px solid rgba(255,255,255,0.12); color: #fff;
}
.lockscreen .pin-panel label.field { display: none; }
.lockscreen .pin-dots { margin: 0; }
.lockscreen .pin-dot { width: 12px; height: 12px; }
.lockscreen .error { margin: 0; min-height: 18px; text-align: center; }
.lockscreen .method-switch { margin-top: 2px; }
.lockscreen .pw-panel { width: 100%; max-width: 320px; }
/* No keyboard and no keyboard BUTTON on the passcode screen: the keypad is the
   only input this screen takes, and a floating keyboard FAB over a lock screen
   reads as a stray browser control. The toggle comes back with the password
   form, which does need typing -- lock-screen.js drops .lockscreen-on when the
   user switches to it. */
body.lockscreen-on .osk-toggle { display: none !important; }
/* ---------------------------------------------------------------------------
   SHEETS. The lock screen has one resting state and three things that can rise
   over it: the passcode, an agent conversation and a decision. They are all the
   same object -- a bottom sheet -- so they share geometry, scrim and dismissal
   and differ only in content.

   data-sheet on .lockscreen is the single source of truth for which one is up.
   Everything else (the scrim, the blur, the unlock bar, which sheet is
   translated into view) is derived from it, so there is no state to get out of
   sync and no way to have two sheets open at once.
   --------------------------------------------------------------------------- */
.lockscreen { position: relative; }
/* The resting screen: no passcode, no keypad. Same reasoning as a phone --
   the glanceable half is what the screen is FOR, and the way in is one
   affordance at the bottom rather than a permanent keypad.

   The passcode shell is taken OUT OF FLOW to do this. Translating it while it
   still occupied its row would leave a keypad-sized hole above the unlock bar,
   pushing the bar into the middle of the screen; as a fixed sheet it shares the
   geometry of the other two and the resting layout is simply head + unlock. */
.lockscreen .ls-foot {
  position: fixed; left: 0; right: 0; bottom: var(--ls-kb, 0px); z-index: 50;
  width: 100%; max-width: 520px; margin: 0 auto;
  max-height: min(88dvh, 720px, calc(100dvh - var(--ls-kb, 0px) - 24px));
  padding: 14px 14px calc(env(safe-area-inset-bottom, 0px) + 18px);
  border-radius: 26px 26px 0 0;
  background: rgba(24, 24, 27, 0.86);
  backdrop-filter: blur(34px) saturate(1.4);
  -webkit-backdrop-filter: blur(34px) saturate(1.4);
  box-shadow: 0 -12px 40px -12px rgba(0,0,0,0.8);
}
.lockscreen:not([data-sheet="passcode"]) .ls-foot {
  transform: translateY(101%);
  pointer-events: none;
}
/* The head recedes while a sheet is up: blurred and slightly shrunk, so the
   sheet reads as being IN FRONT rather than as a panel pasted on. */
.lockscreen:not([data-sheet="none"]) .ls-head {
  filter: blur(7px);
  transform: scale(0.965);
  opacity: 0.55;
  pointer-events: none;
}
.ls-head {
  transition: filter 320ms cubic-bezier(0.32, 0.72, 0, 1),
              transform 320ms cubic-bezier(0.32, 0.72, 0, 1),
              opacity 320ms ease;
  will-change: filter, transform;
}
/* The scrim darkens what is behind the sheet AND is the tap-to-dismiss target,
   so a sheet can always be closed by tapping away from it. */
.ls-scrim {
  position: fixed; inset: 0; z-index: 40;
  background: rgba(0, 0, 0, 0.42);
  opacity: 0;
  transition: opacity 320ms ease;
}
.lockscreen:not([data-sheet="none"]) ~ .ls-scrim,
.ls-scrim[data-on="1"] { opacity: 1; }
.ls-scrim[hidden] { display: none; }

/* The way in. A real button, not a swipe-only gesture: a screen whose only
   unlock is a drag is unusable to anyone who cannot make that drag, and a
   kiosk has no other way in. The swipe is the shortcut, the button is the
   guarantee. */
.ls-unlock {
  display: flex; flex-direction: column; align-items: center; gap: 2px;
  align-self: stretch; flex: none;
  padding-bottom: 2px;
  transition: opacity 240ms ease, transform 240ms ease;
}
.lockscreen:not([data-sheet="none"]) .ls-unlock {
  opacity: 0; transform: translateY(12px); pointer-events: none;
}
.ls-unlock-btn {
  display: flex; flex-direction: column; align-items: center; gap: 10px;
  width: 100%; padding: 14px 0 6px;
  background: none; border: 0; color: inherit; font: inherit;
  cursor: pointer; -webkit-tap-highlight-color: transparent;
}
.ls-unlock-btn:focus-visible { outline: 3px solid #4c9aff; outline-offset: 4px; border-radius: 16px; }
/* Why the keypad just appeared. Without it, tapping "Stop agent" and getting a
   passcode reads as the phone having re-locked itself rather than as the
   action waiting on the unlock. role=status so it is announced, not just seen. */
.ls-unlock-note {
  font-size: 13px; line-height: 1.4; text-align: center;
  color: rgba(255,255,255,0.62);
  padding: 0 24px 8px;
}
.ls-unlock-note[hidden] { display: none; }
.ls-unlock-label {
  font-size: 13.5px; font-weight: 500; letter-spacing: 0.01em;
  color: rgba(255,255,255,0.62);
}
/* The home-indicator bar. It breathes upward once every few seconds -- the
   hint that the gesture goes UP, without a word of instruction. */
.ls-grabber {
  display: block; width: 116px; height: 5px; border-radius: 999px;
  background: rgba(255,255,255,0.42);
}
.ls-unlock-btn .ls-grabber { animation: ls-nudge 3.4s ease-in-out infinite; }
@keyframes ls-nudge {
  0%, 62%, 100% { transform: translateY(0); opacity: 0.55; }
  74%           { transform: translateY(-5px); opacity: 1; }
}

/* Shared sheet geometry. Fixed to the bottom edge so the keyboard, the scrim
   and the sheet all reference the same edge; --ls-kb is the measured height of
   the on-screen keyboard, so a raised keyboard lifts the sheet instead of
   burying its input. */
.ls-sheet {
  position: fixed; left: 0; right: 0; bottom: var(--ls-kb, 0px); z-index: 50;
  display: flex; flex-direction: column;
  /* Subtract --ls-kb: the sheet's bottom edge is already raised by the
     keyboard, so a cap measured from the full viewport lets the box run off
     the TOP of the screen. When that happened the message list -- a
     flex:1/min-height:0 child -- collapsed to zero and the thread rendered
     with every bubble invisible. */
  max-height: min(76dvh, 640px, calc(100dvh - var(--ls-kb, 0px) - 24px));
  margin: 0 auto; width: 100%; max-width: 520px;
  padding: 8px 14px calc(env(safe-area-inset-bottom, 0px) + 14px);
  border-radius: 26px 26px 0 0;
  background: rgba(24, 24, 27, 0.86);
  backdrop-filter: blur(34px) saturate(1.4);
  -webkit-backdrop-filter: blur(34px) saturate(1.4);
  box-shadow: 0 -12px 40px -12px rgba(0,0,0,0.8);
  transform: translateY(101%);
  transition: transform 380ms cubic-bezier(0.32, 0.72, 0, 1), bottom 180ms ease;
}
.ls-sheet[hidden] { display: none; }
.lockscreen[data-sheet="chat"] ~ #ls-chat,
.lockscreen[data-sheet="decision"] ~ #ls-decision { transform: translateY(0); }
/* The passcode sheet is the sign-in shell itself, so it gets the same motion
   rather than a second implementation of "a sheet". */
.lockscreen .ls-foot {
  transition: transform 380ms cubic-bezier(0.32, 0.72, 0, 1), bottom 180ms ease;
}
.ls-sheet-head {
  display: grid; grid-template-columns: 1fr auto; align-items: center;
  gap: 10px; padding: 0 2px 10px;
}
.ls-sheet-head .ls-grabber {
  grid-column: 1 / -1; justify-self: center; margin: 2px 0 12px;
}
.ls-sheet-title { display: flex; align-items: center; gap: 10px; min-width: 0; }
.ls-sheet-avatar {
  flex: none; width: 38px; height: 38px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 14px; font-weight: 600; color: #fff; overflow: hidden;
  background: linear-gradient(145deg, var(--ls-a, #4c9aff), var(--ls-b, #2f6fd0));
}
.ls-sheet-avatar img { width: 100%; height: 100%; object-fit: cover; display: block; }
.ls-sheet-avatar[data-system="1"] { background: #0f0f12; }
.ls-sheet-avatar[data-system="1"] img { object-fit: contain; }
.ls-sheet-name {
  font-size: 15px; font-weight: 600; color: rgba(255,255,255,0.94);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.ls-sheet-sub { font-size: 11.5px; color: rgba(255,255,255,0.48); }
.ls-sheet-close {
  flex: none; width: 32px; height: 32px; border-radius: 50%;
  border: 0; background: rgba(255,255,255,0.10); color: rgba(255,255,255,0.75);
  font-size: 15px; line-height: 1; cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}
.ls-sheet-close:active { background: rgba(255,255,255,0.20); }
.ls-sheet-close:focus-visible { outline: 3px solid #4c9aff; outline-offset: 2px; }

/* Conversation. Scrolls on its own so the composer never leaves the thumb. */
.ls-msgs {
  flex: 1 1 auto; min-height: 0; overflow-y: auto;
  display: flex; flex-direction: column; gap: 5px;
  padding: 2px 2px 10px;
  scrollbar-width: none; -ms-overflow-style: none;
  overscroll-behavior: contain;
}
.ls-msgs::-webkit-scrollbar { width: 0; height: 0; display: none; }
.ls-day {
  align-self: center; margin: 12px 0 6px;
  font-size: 11px; font-weight: 600; letter-spacing: 0.04em;
  text-transform: uppercase; color: rgba(255,255,255,0.34);
}
.ls-msg {
  max-width: 82%; padding: 9px 13px; border-radius: 19px;
  font-size: 14.5px; line-height: 1.38;
  overflow-wrap: anywhere;
  animation: ls-msg-in 260ms cubic-bezier(0.16, 1, 0.3, 1) backwards;
}
@keyframes ls-msg-in {
  from { opacity: 0; transform: translateY(6px) scale(0.98); }
  to   { opacity: 1; transform: none; }
}
/* The two voices are told apart by SIDE and GROUND, not by a label: a name on
   every bubble is noise in a conversation with exactly two participants. */
.ls-msg[data-role="agent"] {
  align-self: flex-start; border-bottom-left-radius: 7px;
  background: rgba(255,255,255,0.10); color: rgba(255,255,255,0.92);
}
.ls-msg[data-role="user"] {
  align-self: flex-end; border-bottom-right-radius: 7px;
  background: #0a84ff; color: #fff;
}
/* Consecutive bubbles from the same voice tighten into one group. */
.ls-msg[data-role="agent"] + .ls-msg[data-role="agent"] { border-bottom-left-radius: 19px; margin-top: -2px; }
.ls-msg[data-role="user"] + .ls-msg[data-role="user"] { border-bottom-right-radius: 19px; margin-top: -2px; }
.ls-compose { display: flex; align-items: flex-end; gap: 8px; padding-top: 4px; }
.ls-compose-input {
  flex: 1 1 auto; min-width: 0;
  padding: 11px 15px; border-radius: 22px;
  border: 1px solid rgba(255,255,255,0.12);
  background: rgba(255,255,255,0.07);
  color: #fff; font: inherit; font-size: 15px;
}
.ls-compose-input::placeholder { color: rgba(255,255,255,0.38); }
.ls-compose-input:focus { outline: none; border-color: rgba(255,255,255,0.28); }
.ls-send {
  flex: none; width: 40px; height: 40px; border-radius: 50%;
  border: 0; background: #0a84ff; color: #fff; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  -webkit-tap-highlight-color: transparent;
  transition: opacity 140ms ease, transform 90ms ease;
}
.ls-send:disabled { opacity: 0.35; cursor: default; }
.ls-send:not(:disabled):active { transform: scale(0.92); }
.ls-send:focus-visible { outline: 3px solid #4c9aff; outline-offset: 2px; }
.ls-send svg { width: 19px; height: 19px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }

/* Decision. Short by design: a question, what it is worth knowing, two ways
   out. Anything longer belongs in the Decisions app behind the passcode. */
.ls-decision-body { padding: 4px 4px 16px; }
.ls-decision-q {
  font-size: 19px; line-height: 1.32; font-weight: 500;
  letter-spacing: -0.01em; color: #fff; margin: 0 0 8px;
}
.ls-decision-meta { font-size: 12.5px; color: rgba(255,255,255,0.46); margin: 0; }
.ls-decision-note {
  margin: 14px 0 0; padding: 10px 12px; border-radius: 12px;
  background: rgba(255,176,32,0.10);
  font-size: 12.5px; line-height: 1.45; color: rgba(255,196,96,0.92);
}
.ls-decision-acts { display: flex; gap: 10px; padding-top: 4px; }
.ls-act {
  flex: 1 1 0; padding: 14px 10px; border-radius: 16px;
  border: 1px solid rgba(255,255,255,0.12);
  font: inherit; font-size: 15px; font-weight: 600; color: #fff;
  background: rgba(255,255,255,0.08); cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  transition: transform 90ms ease, background 140ms ease;
}
.ls-act:active { transform: scale(0.97); }
.ls-act:focus-visible { outline: 3px solid #4c9aff; outline-offset: 2px; }
.ls-act[data-act="approve"] { background: #1f8f4e; border-color: transparent; }
.ls-act[data-act="deny"] { background: rgba(255,255,255,0.09); }
.ls-decision-done {
  padding: 10px 4px 6px; text-align: center;
  font-size: 14px; color: rgba(255,255,255,0.72);
}

/* Dictation button. Quiet until touched -- it sits on every island, so a filled
   control would turn the stack into a row of buttons. */
.ls-mic {
  flex: none; width: 30px; height: 30px; margin-left: 2px;
  border-radius: 50%; border: 0; padding: 0;
  background: rgba(255,255,255,0.07); color: rgba(255,255,255,0.62);
  display: flex; align-items: center; justify-content: center; cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  transition: background 140ms ease, color 140ms ease, transform 90ms ease;
}
.ls-mic svg { width: 15px; height: 15px; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; }
.ls-mic:active { transform: scale(0.9); background: rgba(255,255,255,0.18); color: #fff; }
.ls-mic:focus-visible { outline: 3px solid #4c9aff; outline-offset: 2px; }

/* Voice sheet. The waveform is the whole interface: it is the only thing that
   proves the microphone is actually hearing you, so it gets the room. */
.ls-voice-body { display: flex; flex-direction: column; align-items: center; gap: 14px; padding: 10px 4px 18px; }
.ls-wave { width: 100%; max-width: 420px; height: 120px; display: block; }
.ls-voice-text {
  margin: 0; min-height: 44px; text-align: center;
  font-size: 17px; line-height: 1.35; color: rgba(255,255,255,0.92);
}
.ls-voice-text:empty::before {
  content: "Say something\2026"; color: rgba(255,255,255,0.32);
}
.ls-voice-text[data-error="1"] { font-size: 14px; color: rgba(255,176,32,0.92); }
.ls-voice-acts { display: flex; gap: 10px; padding-top: 2px; }
.lockscreen[data-sheet="voice"] ~ #ls-voice { transform: translateY(0); }

/* Force-touch feel: the island sinks under the finger, then pops as it opens.
   Without the sink there is no feedback that a HOLD is doing anything, and the
   gesture reads as an unresponsive tap. */
.ls-island { cursor: pointer; -webkit-tap-highlight-color: transparent; transition: transform 160ms cubic-bezier(0.32, 0.72, 0, 1); }
.ls-island[data-press="1"] { transform: scale(0.955); }
.ls-island[data-press="2"] { transform: scale(1.035); transition-duration: 220ms; }
/* The avatar is the long-press target for the agent menu, so selection and the
   copy/share callout must never arm on it. body.lockscreen-on already turns
   selection off screen-wide, which makes this redundant TODAY -- it is here
   because the island carries an agent's name and status, text there is a
   reasonable thing to want to copy, and the day someone re-enables selection on
   the island the way .ls-msg does, this is what keeps the press target from
   turning back into a text handle. */
.ls-avatar {
  -webkit-user-select: none; user-select: none;
  -webkit-touch-callout: none;
}
/* The avatar reads as its own control once it has its own gesture. */
.ls-island[data-press="avatar"] .ls-avatar { transform: scale(1.08); }
.ls-avatar { transition: transform 200ms cubic-bezier(0.32, 0.72, 0, 1); }

/* THE AGENT MENU. Anchored over the island rather than dropped from the top of
   the screen: it acts on THAT agent, and a sheet that covers the islands would
   hide which one you pressed. */
.ls-menu {
  position: fixed; z-index: 60;
  min-width: 208px; max-width: min(300px, calc(100vw - 32px));
  padding: 6px;
  background: rgba(26, 26, 30, 0.92);
  -webkit-backdrop-filter: blur(22px) saturate(1.4);
  backdrop-filter: blur(22px) saturate(1.4);
  border-radius: 18px;
  box-shadow: 0 18px 48px rgba(0,0,0,0.58);
  display: flex; flex-direction: column; gap: 2px;
  transform-origin: top center;
  animation: ls-menu-in 180ms cubic-bezier(0.16, 1, 0.3, 1);
}
.ls-menu[hidden] { display: none; }
@keyframes ls-menu-in {
  from { opacity: 0; transform: scale(0.9) translateY(-6px); }
  to { opacity: 1; transform: none; }
}
.ls-menu-head {
  padding: 8px 12px 6px; display: flex; flex-direction: column; gap: 1px;
}
.ls-menu-name { font-size: 14px; font-weight: 600; color: rgba(255,255,255,0.94); }
.ls-menu-sub { font-size: 12px; color: rgba(255,255,255,0.46); }
.ls-menu-item {
  -webkit-appearance: none; appearance: none; background: none; border: 0;
  display: flex; align-items: center; gap: 10px;
  padding: 11px 12px; border-radius: 12px;
  font: inherit; font-size: 15px; text-align: left;
  color: rgba(255,255,255,0.92); cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}
.ls-menu-item:hover, .ls-menu-item:focus-visible { background: rgba(255,255,255,0.09); }
.ls-menu-item:focus-visible { outline: 2px solid #4c9aff; outline-offset: -2px; }
.ls-menu-item[data-danger="1"] { color: #ff6b6b; }
/* Disabled rather than absent: an agent that is already stopped should still
   show "Stop", greyed, so the menu does not change shape under the finger. */
.ls-menu-item[disabled] { opacity: 0.38; cursor: default; }
.ls-menu-item[disabled]:hover { background: none; }
.ls-menu-sep { height: 1px; margin: 4px 8px; background: rgba(255,255,255,0.09); }
@media (prefers-reduced-motion: reduce) {
  .ls-menu { animation: none; }
  .ls-avatar { transition: none; }
}
.ls-island:focus-visible { outline: 3px solid #4c9aff; outline-offset: 3px; }

@media (prefers-reduced-motion: reduce) {
  .ls-sheet, .ls-foot, .ls-head, .ls-unlock, .ls-scrim, .ls-island { transition: none; }
  .ls-msg { animation: none; }
  .ls-unlock-btn .ls-grabber { animation: none; }
  .ls-notif-group { animation: none; }
  .ls-notif { transition: none; }
  .lockscreen:not([data-sheet="none"]) .ls-head { filter: none; }
}
/* Landscape: the keypad and the clock sit side by side or neither fits. */
@media (orientation: landscape) and (max-height: 560px) {
  .lockscreen { flex-direction: row; align-items: center; gap: 24px; padding-top: 12px; }
  .ls-head { flex: 1 1 0; }
  .ls-spacer { display: none; }
  /* .ls-foot is a fixed bottom sheet, not a flex child, so it needs a height
     cap here rather than a flex ratio: at this height the keypad must scroll
     inside the sheet instead of growing past the top of the screen. */
  .lockscreen .ls-foot { max-height: 92dvh; overflow-y: auto; }
  .ls-sheet { max-height: 88dvh; }
  .ls-time { font-size: clamp(40px, 9vw, 64px); }
  .ls-widgets { margin-top: 10px; }
  .ls-key { max-height: 52px; }
}
"""

# Plain (non-f) string: interpolated into the page as a value, so braces here
# must not be doubled.
_PIN_PANEL_SCRIPT = r"""
(function () {
  "use strict";
  // Deferred to DOMContentLoaded on purpose. This script is inline and runs
  // during parsing, but the on-screen keyboard appends its panel on
  // DOMContentLoaded -- so calling taosOSK.enable() here at parse time would
  // "show" a panel that is not in the document yet and the keypad would never
  // appear. The OSK block is emitted BEFORE this one, so its listener is
  // registered first and has already run by the time we get here.
  function init() {
  var pinPanel = document.getElementById("pin-panel");
  var pwPanel  = document.getElementById("pw-panel");
  if (!pinPanel || !pwPanel) return;

  var input   = document.getElementById("pin-input");
  var dots    = document.getElementById("pin-dots");
  var err     = document.getElementById("pin-error");
  var submit  = document.getElementById("pin-submit");
  var toPw    = document.getElementById("use-password");
  var toPin   = document.getElementById("use-pin");
  var nextUrl = pinPanel.getAttribute("data-next") || "/desktop";
  var user    = pinPanel.getAttribute("data-username") || "";
  var busy    = false;

  function paint() {
    var n = input.value.length;
    var kids = dots.children;
    for (var i = 0; i < kids.length; i++) {
      kids[i].setAttribute("data-filled", i < n ? "1" : "0");
    }
  }

  input.addEventListener("input", function () {
    // Digits only: the keypad cannot produce anything else, but a physical
    // keyboard can, and a stray letter would fail server-side validation with
    // a confusing "incorrect PIN".
    input.value = input.value.replace(/\D/g, "");
    paint();
    err.textContent = "";
  });

  function fail(msg) {
    err.textContent = msg;
    input.value = "";
    paint();
  }

  submit.addEventListener("click", function () {
    if (busy) return;
    var pin = input.value;
    if (pin.length < 4) { fail("Enter your PIN."); return; }
    busy = true;
    fetch("/auth/pin-login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: user || undefined, pin: pin })
    }).then(function (r) {
      return r.json().then(function (body) { return { status: r.status, body: body }; });
    }).then(function (res) {
      busy = false;
      if (res.status === 200 && res.body && res.body.ok) {
        window.location.assign(nextUrl);
        return;
      }
      // 404 means PIN sign-in is not available from here at all; say so plainly
      // rather than leaving the user tapping at a keypad that cannot work.
      if (res.status === 404) {
        fail("PIN sign-in is not available on this device. Use your password.");
        return;
      }
      // Read `detail` as well as `error`: a FastAPI HTTPException raised by a
      // dependency (rather than returned by the handler) serialises as
      // {"detail": ...}, and blaming the PIN for a failure that had nothing to
      // do with it strands a kiosk user with no way to tell what is wrong.
      var reason = res.body && (res.body.error || res.body.detail);
      fail(reason || "Sign-in failed. Try again, or use your password.");
    }).catch(function () {
      busy = false;
      fail("Could not reach taOS. Check the connection and try again.");
    });
  });

  function swap(showPin) {
    pinPanel.hidden = !showPin;
    pwPanel.hidden = showPin;
    err.textContent = "";
    if (showPin) {
      input.value = "";
      paint();
      // The lock screen draws its own keypad (#ls-pad), so the shared keyboard
      // must stay shut: enabling it here opened a full QWERTY over the passcode
      // pad and covered the lower third of the phone. Only the page WITHOUT a
      // keypad needs the shared keyboard to type a PIN at all.
      if (document.getElementById("ls-pad")) {
        if (window.taosOSK) window.taosOSK.disable();
      } else if (window.taosOSK) {
        window.taosOSK.enable();
        window.taosOSK.focusField(input);
      } else {
        input.focus();
      }
    } else {
      var pw = pwPanel.querySelector("input[type=password]");
      if (pw && window.taosOSK) window.taosOSK.focusField(pw);
      else if (pw) pw.focus();
    }
  }

  if (toPw)  toPw.addEventListener("click", function () { swap(false); });
  if (toPin) toPin.addEventListener("click", function () { swap(true); });

  // PROGRESSIVE ENHANCEMENT, and it is load-bearing. The server renders the
  // PASSWORD form visible and the PIN panel hidden; only here, once every
  // element resolved and the handlers are attached, do we swap to the PIN
  // view. Hiding the password form server-side instead would brick the console
  // whenever this file does not run -- a CSP refusal, a cache miss, JS off --
  // leaving a keypad that cannot submit and no other way in. That is the exact
  // lockout PIN sign-in was built to remove, so it must not be reintroduced by
  // the fix. swap(true) also opens the keypad, which is the whole point on a
  // keyboard-less panel: the user should not have to find a toggle first.
  swap(true);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
"""



#: The lock-screen keypad. A plain grid of buttons rather than a re-use of the
#: shared on-screen keyboard: that one is a full text keyboard docked to the
#: bottom of the viewport, and a passcode pad wants ten large round targets
#: under the thumb. Digits carry aria-labels because the visible glyph alone is
#: ambiguous to a screen reader announcing a grid of buttons.
_KEYPAD_HTML = """
      <div class="ls-pad" id="ls-pad" role="group" aria-label="PIN keypad">
        <button type="button" class="ls-key" data-digit="1">1</button>
        <button type="button" class="ls-key" data-digit="2">2</button>
        <button type="button" class="ls-key" data-digit="3">3</button>
        <button type="button" class="ls-key" data-digit="4">4</button>
        <button type="button" class="ls-key" data-digit="5">5</button>
        <button type="button" class="ls-key" data-digit="6">6</button>
        <button type="button" class="ls-key" data-digit="7">7</button>
        <button type="button" class="ls-key" data-digit="8">8</button>
        <button type="button" class="ls-key" data-digit="9">9</button>
        <span class="ls-key ls-key-blank" aria-hidden="true"></span>
        <button type="button" class="ls-key" data-digit="0">0</button>
        <button type="button" class="ls-key" data-action="back" aria-label="Delete">&#9003;</button>
      </div>
"""


def _pin_panel_html(next_url: str, keypad: bool = False) -> str:
    """The PIN entry panel, shown only when the request is console-local.

    Rendered HIDDEN. /auth/pin-panel.js reveals it (and hides the password
    form) once it has wired itself up; see the note on the swap in that script.
    """
    safe_next = html.escape(next_url or "/desktop")
    # The lock screen draws its own keypad, so the shared on-screen keyboard must
    # not also open: two keypads fight for the same input, and the OSK's layout
    # override top-anchors the screen. inputmode="none" also stops the
    # compositor's Wayland keyboard (squeekboard) from appearing over the pad.
    osk_mode = "none" if keypad else "numeric"
    osk_attr = "" if keypad else 'data-osk-submit="pin-submit"'
    keypad_html = _KEYPAD_HTML if keypad else ""

    # No username is sent with a PIN: this panel is only ever rendered for a
    # single-user store, because AuthManager.has_pin(None) refuses to guess
    # which account a PIN belongs to on a multi-user one.
    return f"""
    <div class="pin-panel" id="pin-panel" data-next="{safe_next}" data-username="" hidden>
      <label class="field">
        <span>PIN</span>
        <input type="password" id="pin-input" inputmode="{osk_mode}" autocomplete="off"
               {osk_attr} aria-describedby="pin-error"
               maxlength="12" required>
      </label>
      <div class="pin-dots" id="pin-dots" aria-hidden="true">
        <span class="pin-dot"></span><span class="pin-dot"></span>
        <span class="pin-dot"></span><span class="pin-dot"></span>
      </div>
      <p class="error" id="pin-error" role="alert"></p>
      {keypad_html}
      <button type="button" id="pin-submit">Sign in with PIN</button>
      <button type="button" class="method-switch" id="use-password">
        Use my password instead
      </button>
    </div>
    """




#: Framework marks, drawn as geometry rather than shipped as logo files: the
#: lock screen must render with no network and no asset pipeline, and a glyph or
#: emoji standing in for an icon set is not an icon set. One stroke weight and
#: one cap style across all three so they read as a family at 18px. These are
#: stylised marks for the harness a taOS agent runs on, not the vendors' logos.
_FRAMEWORK_SPRITE = """
      <svg class="ls-sprite" aria-hidden="true" focusable="false" width="0" height="0">
        <defs>
          <symbol id="fw-hermes" viewBox="0 0 24 24">
            <!-- winged helm: a dome with two upswept wings -->
            <path d="M7.5 15.5a4.5 4.5 0 0 1 9 0" />
            <path d="M6 15.5h12" />
            <path d="M16.5 11.5c1.6-1.1 3-1.4 4.5-1.1-1 1.3-2.3 2.2-4 2.6" />
            <path d="M7.5 11.5C5.9 10.4 4.5 10.1 3 10.4c1 1.3 2.3 2.2 4 2.6" />
          </symbol>
          <symbol id="fw-openclaw" viewBox="0 0 24 24">
            <!-- three tapered talons converging on a palm arc -->
            <path d="M8 4.5v7" />
            <path d="M12 3.5v8" />
            <path d="M16 4.5v7" />
            <path d="M6.5 11.5a5.5 5.5 0 0 0 11 0" />
          </symbol>
          <symbol id="fw-deepseek" viewBox="0 0 24 24">
            <!-- breaching whale: body arc, tail fluke, spout -->
            <path d="M3.5 14.5c3.2 2.6 7.2 3.4 11 2.1 2.6-.9 4.4-2.8 5.2-5.4" />
            <path d="M19.7 11.2c.9.5 1.4 1.4 1.3 2.5-1-.3-1.8-.9-2.3-1.7" />
            <path d="M8.2 16.8c-.6 1.2-1.7 2-3.1 2.2.2-1.3.9-2.3 2-2.9" />
            <path d="M12.5 9.2c.6-1.2 1.6-2 3-2.3" />
          </symbol>
          <symbol id="fw-omp" viewBox="0 0 24 24">
            <!-- OMP is "oh-my-pi" and ships no logo of its own, so this is an
                 AUTHORED mark in the same line-art set as the others, not a
                 vendor logo: a pi glyph, its legs standing on a base rule. -->
            <path d="M5.5 7.5h13" />
            <path d="M9 7.5v9" />
            <path d="M15 7.5v7a2 2 0 0 0 2.8 1.8" />
            <path d="M6 16.5h6" />
          </symbol>
        </defs>
      </svg>
"""


# The view switcher's icons. A SECOND sprite rather than four more symbols in
# the one above: that sprite is the framework badges an agent carries, and these
# are chrome the user steers with. Merging them would make "which symbols may I
# delete when a framework is dropped" unanswerable.
#
# Authored in the same line-art set as the framework marks -- 24x24, stroked,
# no fills -- so the row does not read as icons borrowed from somewhere else.
_VIEW_SPRITE = """
      <svg class="ls-sprite" aria-hidden="true" focusable="false" width="0" height="0">
        <defs>
          <symbol id="lv-agents" viewBox="0 0 24 24">
            <!-- two overlapping islands: the stack this screen is built around -->
            <rect x="3" y="6" width="14" height="6" rx="3" />
            <rect x="7" y="13" width="14" height="6" rx="3" />
          </symbol>
          <symbol id="lv-phone" viewBox="0 0 24 24">
            <!-- handset -->
            <path d="M8.2 4.5c.7 0 1.3.4 1.5 1.1l.8 2.3c.2.6 0 1.3-.5 1.7l-1.1.9a11 11 0 0 0 4.6 4.6l.9-1.1c.4-.5 1.1-.7 1.7-.5l2.3.8c.7.2 1.1.8 1.1 1.5v2.1c0 .9-.8 1.6-1.7 1.5A14.5 14.5 0 0 1 4.6 6.2C4.5 5.3 5.2 4.5 6.1 4.5z" />
          </symbol>
          <symbol id="lv-mailbox" viewBox="0 0 24 24">
            <!-- envelope: the flap is a separate stroke so it reads at 18px -->
            <rect x="3" y="6" width="18" height="12" rx="2.5" />
            <path d="M3.8 7.6 12 13l8.2-5.4" />
          </symbol>
          <symbol id="lv-apps" viewBox="0 0 24 24">
            <!-- four tiles -->
            <rect x="4" y="4" width="6.5" height="6.5" rx="1.8" />
            <rect x="13.5" y="4" width="6.5" height="6.5" rx="1.8" />
            <rect x="4" y="13.5" width="6.5" height="6.5" rx="1.8" />
            <rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.8" />
          </symbol>
          <symbol id="lv-alerts" viewBox="0 0 24 24">
            <!-- exclamation in a ring. The dot is a 0-length line with a round
                 cap: a filled circle would be the only solid shape in the set. -->
            <circle cx="12" cy="12" r="8.5" />
            <path d="M12 7.5v5" />
            <path d="M12 16.2v0" />
          </symbol>
          <symbol id="lv-stats" viewBox="0 0 24 24">
            <!-- three bars on a baseline -->
            <path d="M4 20h16" />
            <path d="M7.5 20v-5.5" />
            <path d="M12 20V8" />
            <path d="M16.5 20v-8.5" />
          </symbol>
          <symbol id="lv-settings" viewBox="0 0 24 24">
            <!-- cog: an octagonal rosette, not a 12-tooth gear, which turns to
                 mud at 18px on a phone -->
            <circle cx="12" cy="12" r="3" />
            <path d="M12 2.8v2.4M12 18.8v2.4M21.2 12h-2.4M5.2 12H2.8" />
            <path d="m18.5 5.5-1.7 1.7M7.2 16.8l-1.7 1.7M18.5 18.5l-1.7-1.7M7.2 7.2 5.5 5.5" />
          </symbol>
        </defs>
      </svg>
"""


def _device_label() -> str:
    """The handset's own name, for the lock-screen chip.

    Falls back to the product name: a lock screen that renders an empty chip
    because the host has no resolvable name looks broken, and the name is
    cosmetic here.
    """
    try:
        name = socket.gethostname().split(".")[0].strip()
    except OSError:
        name = ""
    return name or "taOS"


# The view row, defined ONCE. The tab markup below and the script's view list
# are both generated from this, because two hand-maintained lists of the same
# seven views is a drift waiting to happen: a tab with no panel renders a blank
# screen, and a panel with no tab is unreachable.
#
# Order is Jay's: agents first and agents default -- this is the resting screen,
# and the islands are what it is for.
#
# The panel id is carried here rather than derived as "ls-" + key, because two
# of the seven panels predate this row and keep their own names: the agents
# panel is #ls-activity (#ls-agents is the box INSIDE it) and the alerts panel
# is #ls-notifs. Deriving the id would have pointed aria-controls at the inner
# agents box and at an #ls-alerts that does not exist.
_LOCK_VIEWS = (
    ("agents", "Agents", "lv-agents", "ls-activity"),
    ("phone", "Phone", "lv-phone", "ls-phone"),
    ("mailbox", "Mailbox", "lv-mailbox", "ls-mailbox"),
    ("apps", "Apps", "lv-apps", "ls-apps"),
    ("alerts", "Alerts", "lv-alerts", "ls-notifs"),
    ("stats", "System", "lv-stats", "ls-stats"),
    ("settings", "Settings", "lv-settings", "ls-settings"),
)
_LOCK_DEFAULT_VIEW = "agents"


def _view_tabs_html() -> str:
    """The seven view tabs.

    Each is a real <button> in a tablist rather than a styled <div>: this row is
    the only way to reach five of the seven panels, so it has to be operable by
    keyboard and announced as a tab, not as decoration.

    aria-selected is set here for the default and thereafter maintained by the
    script, so the markup is already correct before any JS runs.
    """
    out = []
    for key, label, icon, panel in _LOCK_VIEWS:
        selected = "true" if key == _LOCK_DEFAULT_VIEW else "false"
        # Only the selected tab is in the tab order. A tablist takes ONE tab
        # stop and is then traversed with arrow keys; seven tab stops would put
        # six dead ends between the clock and the unlock button.
        tabindex = "0" if key == _LOCK_DEFAULT_VIEW else "-1"
        out.append(
            f'<button type="button" class="ls-view-tab" id="ls-tab-{key}"'
            f' role="tab" data-view="{key}" aria-selected="{selected}"'
            f' aria-controls="{panel}" tabindex="{tabindex}"'
            f' title="{label}" aria-label="{label}">'
            f'<svg class="ls-view-icon" aria-hidden="true" focusable="false"'
            f' width="22" height="22"><use href="#{icon}"></use></svg>'
            f'<span class="ls-view-dot" aria-hidden="true"></span>'
            f"</button>"
        )
    return "\n        ".join(out)


def _lock_head_html() -> str:
    """Opening half of the lock screen: clock, date and the widget row.

    Emitted as the page's first element and closed by the caller, so the
    passcode shell below it is the SAME markup the plain card path renders --
    the lock screen is chrome around the sign-in, not a second implementation
    of it.

    The clock renders empty and is filled by /auth/lock-screen.js: a
    server-rendered time would be the SERVER's clock and, worse, frozen at page
    load, so a phone left on the lock screen would show a stale time.
    """
    return f"""
  <div class="lockscreen" id="lockscreen">
    <div class="ls-statusbar">
      <span class="ls-widget ls-brand"><b>taOS</b></span>
      <span class="ls-widget" id="ls-battery" hidden></span>
    </div>
    <div class="ls-head">
      <div class="ls-time" id="ls-time" role="timer" aria-live="off">&nbsp;</div>
      <div class="ls-date" id="ls-date"></div>
      <div class="ls-weather" id="ls-weather" role="group" aria-label="Weather" hidden></div>
      <div class="ls-views" id="ls-views" role="tablist" aria-label="Lock screen views">
        {_view_tabs_html()}
      </div>
      <div class="ls-feed" id="ls-feed" data-view="agents">
        <div class="ls-islands" id="ls-activity" data-view="agents"
             role="tabpanel" aria-labelledby="ls-tab-agents" aria-label="Agent activity" hidden>
          <div class="ls-agents" id="ls-agents"></div>
          <div class="ls-tasks" id="ls-tasks"></div>
        </div>
        <div class="ls-notifs" id="ls-notifs" data-view="alerts"
             role="tabpanel" aria-labelledby="ls-tab-alerts" aria-label="Notifications" hidden></div>
        <div class="ls-panel" id="ls-phone" data-view="phone"
             role="tabpanel" aria-labelledby="ls-tab-phone" aria-label="Phone" hidden></div>
        <div class="ls-panel" id="ls-mailbox" data-view="mailbox"
             role="tabpanel" aria-labelledby="ls-tab-mailbox" aria-label="Mailbox" hidden></div>
        <div class="ls-panel" id="ls-apps" data-view="apps"
             role="tabpanel" aria-labelledby="ls-tab-apps" aria-label="Apps" hidden></div>
        <div class="ls-panel" id="ls-stats" data-view="stats"
             role="tabpanel" aria-labelledby="ls-tab-stats" aria-label="System" hidden></div>
        <div class="ls-panel" id="ls-settings" data-view="settings"
             role="tabpanel" aria-labelledby="ls-tab-settings" aria-label="Settings" hidden></div>
      </div>
      {_FRAMEWORK_SPRITE}
      {_VIEW_SPRITE}
    </div>
    <div class="ls-spacer"></div>
    <div class="ls-unlock" id="ls-unlock">
      <div class="ls-unlock-note" id="ls-unlock-note" role="status" hidden></div>
      <button type="button" class="ls-unlock-btn" id="ls-unlock-btn"
              aria-expanded="false" aria-controls="ls-foot">
        <span class="ls-grabber"></span>
        <span class="ls-unlock-label">Swipe up to unlock</span>
      </button>
    </div>"""


def _lock_tail_html() -> str:
    """Closing half: the scrim and the two sheets that rise over the screen.

    Emitted AFTER </div> so the sheets are siblings of .lockscreen, not children
    of it. That matters: the head is blurred while a sheet is open, and a child
    would inherit that filter -- a blurred conversation is not a conversation.
    A filter on an ancestor also establishes a containing block, which would
    pin these fixed sheets to the lock screen's box instead of the viewport.

    Both sheets are rendered empty and hidden. They are console-only chrome, and
    everything inside them is written by /auth/lock-screen.js from data the
    server only serves to the device's own screen.
    """
    return """</div>
  <div class="ls-scrim" id="ls-scrim" hidden></div>
  <section class="ls-sheet" id="ls-chat" role="dialog" aria-modal="true"
           aria-labelledby="ls-chat-name" hidden>
    <header class="ls-sheet-head">
      <span class="ls-grabber"></span>
      <div class="ls-sheet-title">
        <div class="ls-sheet-avatar" id="ls-chat-avatar" aria-hidden="true"></div>
        <div>
          <div class="ls-sheet-name" id="ls-chat-name"></div>
          <div class="ls-sheet-sub" id="ls-chat-sub"></div>
        </div>
      </div>
      <button type="button" class="ls-sheet-close" id="ls-chat-close" aria-label="Close conversation">&#10005;</button>
    </header>
    <div class="ls-msgs" id="ls-msgs" role="log" aria-live="polite" tabindex="0"></div>
    <div class="ls-compose">
      <input type="text" class="ls-compose-input" id="ls-compose-input"
             placeholder="Message" autocomplete="off" autocapitalize="sentences"
             aria-label="Message" data-osk-submit="ls-send" maxlength="500">
      <button type="button" class="ls-send" id="ls-send" aria-label="Send message" disabled>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12h15M13 6l6 6-6 6"/></svg>
      </button>
    </div>
  </section>
  <section class="ls-sheet" id="ls-decision" role="dialog" aria-modal="true"
           aria-labelledby="ls-decision-q" hidden>
    <header class="ls-sheet-head">
      <span class="ls-grabber"></span>
      <div class="ls-sheet-title">
        <div class="ls-sheet-avatar" id="ls-decision-avatar" aria-hidden="true"></div>
        <div>
          <div class="ls-sheet-name" id="ls-decision-name"></div>
          <div class="ls-sheet-sub">Needs your decision</div>
        </div>
      </div>
      <button type="button" class="ls-sheet-close" id="ls-decision-close" aria-label="Close">&#10005;</button>
    </header>
    <div class="ls-decision-body">
      <p class="ls-decision-q" id="ls-decision-q"></p>
      <p class="ls-decision-meta" id="ls-decision-meta"></p>
      <p class="ls-decision-note" id="ls-decision-note" hidden></p>
    </div>
    <div class="ls-decision-acts" id="ls-decision-acts">
      <button type="button" class="ls-act" data-act="deny">Deny</button>
      <button type="button" class="ls-act" data-act="approve">Approve</button>
    </div>
    <p class="ls-decision-done" id="ls-decision-done" hidden></p>
  </section>
  <section class="ls-sheet ls-sheet-voice" id="ls-voice" role="dialog" aria-modal="true"
           aria-labelledby="ls-voice-title" hidden>
    <header class="ls-sheet-head">
      <span class="ls-grabber"></span>
      <div class="ls-sheet-title">
        <div class="ls-sheet-avatar" id="ls-voice-avatar" aria-hidden="true"></div>
        <div>
          <div class="ls-sheet-name" id="ls-voice-title"></div>
          <div class="ls-sheet-sub" id="ls-voice-state">Listening\u2026</div>
        </div>
      </div>
      <button type="button" class="ls-sheet-close" id="ls-voice-close" aria-label="Cancel dictation">&#10005;</button>
    </header>
    <div class="ls-voice-body">
      <canvas class="ls-wave" id="ls-wave" width="600" height="120" aria-hidden="true"></canvas>
      <p class="ls-voice-text" id="ls-voice-text" aria-live="polite"></p>
    </div>
    <div class="ls-voice-acts">
      <button type="button" class="ls-act" id="ls-voice-cancel">Cancel</button>
      <button type="button" class="ls-act" data-act="approve" id="ls-voice-send" disabled>Send</button>
    </div>
  </section>"""


# Plain (non-f) string: braces are JavaScript, not format fields.
_LOCK_SCREEN_SCRIPT = r"""
(function () {
  "use strict";
  // The view list is generated from _LOCK_VIEWS at serve time, so the tab row
  // and this script cannot disagree about which views exist.
/*__LOCK_VIEWS__*/
  // Lock-screen chrome only: the clock, the battery chip and the keypad. PIN
  // submission, the dots and the error line stay in /auth/pin-panel.js -- this
  // script types into the same #pin-input and lets that one do the rest, so
  // there is exactly one implementation of "what happens when a PIN is entered".
  function init() {
    var timeEl = document.getElementById("ls-time");
    var dateEl = document.getElementById("ls-date");

    function tick() {
      var now = new Date();
      // Locale-driven: a 24h phone shows 24h. hour12 is left to the locale
      // rather than forced, because forcing it is wrong in half the world.
      if (timeEl) {
        timeEl.textContent = now.toLocaleTimeString([], {
          hour: "numeric", minute: "2-digit"
        });
      }
      if (dateEl) {
        dateEl.textContent = now.toLocaleDateString([], {
          weekday: "long", day: "numeric", month: "long"
        });
      }
      // Re-align to the top of the next minute instead of polling every second:
      // the display only changes once a minute and this is a battery-powered
      // device sitting on this screen whenever it is idle.
      var ms = (60 - now.getSeconds()) * 1000 - now.getMilliseconds();
      setTimeout(tick, ms > 0 ? ms : 60000);
    }
    tick();

    // Battery: navigator.getBattery is not universal (and is absent on desktop
    // Firefox), so the chip stays hidden unless the API actually answers.
    var batEl = document.getElementById("ls-battery");
    if (batEl && navigator.getBattery) {
      navigator.getBattery().then(function (bat) {
        function paint() {
          var pct = Math.round(bat.level * 100);
          batEl.textContent = (bat.charging ? "⚡ " : "") + pct + "%";
          batEl.hidden = false;
        }
        paint();
        bat.addEventListener("levelchange", paint);
        bat.addEventListener("chargingchange", paint);
      }).catch(function () { /* no battery info: leave the chip hidden */ });
    }

    // Agent activity. Re-fetched on a timer because a lock screen is a LIVE
    // surface: it is what the phone shows while it sits there, so a card that
    // only reflects page-load time is wrong within a minute.
    var screenEl = document.getElementById("lockscreen");
    var card = document.getElementById("ls-activity");
    var agentsEl = document.getElementById("ls-agents");
    var tasksEl = document.getElementById("ls-tasks");

    // Deterministic hue per agent, so an agent keeps its colour between
    // refreshes and between boots. A random palette would reshuffle the lock
    // screen every 15 seconds.
    function hueFor(name) {
      var h = 0;
      for (var i = 0; i < name.length; i++) { h = (h * 31 + name.charCodeAt(i)) % 360; }
      return h;
    }

    function initials(name) {
      var words = name.trim().split(/\s+/);
      if (!words[0]) return "?";
      if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
      return (words[0][0] + words[words.length - 1][0]).toUpperCase();
    }

    var FRAMEWORKS = { hermes: 1, openclaw: 1, deepseek: 1, omp: 1 };
    var RESTING = ["", "stopped", "idle", "exited", "error"];

    function island(agent) {
      var name = agent.name || "agent";
      var status = agent.status || "idle";
      var busy = RESTING.indexOf(status.trim().toLowerCase()) === -1;

      var el = document.createElement("div");
      el.className = "ls-island";
      // Stable identity across repaints. The 15s poll rebuilds this list, and
      // without a key there is no way to put keyboard focus back on the island
      // the user was actually on -- an index would silently move the focus to a
      // different agent whenever the list reorders.
      el.setAttribute("data-agent", name);
      el.setAttribute("data-state", busy ? "busy" : "idle");
      if (agent.attention) el.setAttribute("data-attention", "1");
      // An island OPENS something, so it is a button, not a list item: it has
      // to be reachable by tab and operable by Enter, not only by a press.
      el.setAttribute("role", "button");
      el.setAttribute("tabindex", "0");
      el.setAttribute("aria-label", (agent.attention && agent.decision)
        ? name + " needs a decision: " + (agent.decision.question || "")
        : name + ", " + status + ". Open conversation.");
      // The handlers read the whole record off the element rather than
      // re-looking it up by name: names are not unique keys, and a repaint
      // between the press and the open would make an index stale.
      el.__agent = agent;
      if (agent.system) el.setAttribute("data-system", "1");

      var marks = document.createElement("div");
      marks.className = "ls-marks";

      var av = document.createElement("div");
      av.className = "ls-avatar";
      var hue = hueFor(name);
      av.style.setProperty("--ls-a", "hsl(" + hue + " 62% 58%)");
      av.style.setProperty("--ls-b", "hsl(" + ((hue + 28) % 360) + " 58% 38%)");
      // A photo when one is configured; the monogram is the fallback, so a
      // missing file degrades to initials rather than a broken image frame.
      if (agent.avatar) {
        var img = document.createElement("img");
        img.className = "ls-avatar-img";
        img.alt = "";
        img.src = agent.avatar;
        img.addEventListener("error", function () {
          img.remove();
          // The product mark is shipped in /static, so a failure here is a
          // broken install rather than a missing optional portrait. Initials
          // are the fallback for a PERSON; "TA" in a circle is not the OS.
          if (!agent.system) av.textContent = initials(name);
        });
        av.appendChild(img);
      } else {
        av.textContent = initials(name);
      }
      marks.appendChild(av);

      var fw = String(agent.framework || "").toLowerCase();
      if (agent.framework_icon) {
        // The App Store's own artwork, when this framework ships one.
        var badge = document.createElement("div");
        badge.className = "ls-fw ls-fw-img";
        var logo = document.createElement("img");
        logo.alt = "";
        logo.src = agent.framework_icon;
        badge.appendChild(logo);
        marks.appendChild(badge);
      } else if (FRAMEWORKS[fw]) {
        var badge = document.createElement("div");
        badge.className = "ls-fw";
        var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        var use = document.createElementNS("http://www.w3.org/2000/svg", "use");
        // setAttribute, not the xlink-prefixed form: plain href on <use>
        // resolves in every browser this ships to and xlink is deprecated.
        use.setAttribute("href", "#fw-" + fw);
        svg.appendChild(use);
        badge.appendChild(svg);
        marks.appendChild(badge);
      }
      el.appendChild(marks);

      var body = document.createElement("div");
      body.className = "ls-body";
      var n = document.createElement("div");
      n.className = "ls-name";
      n.textContent = name;                 // textContent, never innerHTML
      var s = document.createElement("div");
      s.className = "ls-status";
      s.textContent = status;
      body.appendChild(n); body.appendChild(s);
      el.appendChild(body);

      var pip = document.createElement("span");
      pip.className = "ls-pip";
      el.appendChild(pip);

      // Dictation, next to the live pip: the fastest way to say something to an
      // agent from a locked phone is to say it. Its own button rather than a
      // gesture on the island, because it does something DIFFERENT from opening
      // the conversation and must not be reachable by accident.
      var mic = document.createElement("button");
      mic.type = "button";
      mic.className = "ls-mic";
      mic.setAttribute("aria-label", "Dictate a message to " + name);
      mic.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true">'
        + '<rect x="9" y="3" width="6" height="11" rx="3"/>'
        + '<path d="M5.5 11.5a6.5 6.5 0 0 0 13 0"/><path d="M12 18v3"/></svg>';
      el.appendChild(mic);
      return el;
    }

    function paintActivity(data) {
      // A repaint while a sheet is open would destroy the very island the sheet
      // was opened from -- dropping its record, restarting every entrance
      // animation and scrolling the list under the user's finger. The poll
      // keeps running; the next tick after the sheet closes paints.
      var openSheetName = screenEl ? screenEl.getAttribute("data-sheet") : "none";
      if (openSheetName && openSheetName !== "none") return;

      // Same reasoning for the agent menu: it is anchored to an island and is
      // ABOUT that island, so a repaint would leave a menu floating over a
      // rebuilt list, still holding the record of an element that no longer
      // exists. It is not a sheet, so the check above does not cover it.
      if (menuEl) return;

      // Wiping agentsEl destroys whichever island holds keyboard focus, and the
      // browser drops focus to the body. At a 15s poll that means a keyboard or
      // switch-access user is thrown back to the top of the page every 15
      // seconds -- and since the islands and their mic buttons come BEFORE the
      // notification stacks in tab order, they could never tab far enough to
      // reach a stack at all. Remember where focus was and put it back.
      var focusKey = null;
      var focusWasMic = false;
      var active = document.activeElement;
      if (active && agentsEl.contains(active)) {
        var owner = active.closest ? active.closest(".ls-island") : null;
        if (owner) {
          focusKey = owner.getAttribute("data-agent");
          focusWasMic = active !== owner;
        }
      }

      agentsEl.textContent = "";
      tasksEl.textContent = "";
      var agents = data.agents || [];
      var tasks = data.tasks || [];
      if (!agents.length && !tasks.length) { card.hidden = true; return; }

      for (var i = 0; i < agents.length; i++) {
        agentsEl.appendChild(island(agents[i]));
      }
      for (var j = 0; j < tasks.length; j++) {
        var row = document.createElement("div");
        row.className = "ls-task";
        var tn = document.createElement("span");
        tn.className = "ls-task-name";
        tn.textContent = tasks[j].name || "task";
        var tw = document.createElement("span");
        tw.textContent = tasks[j].agent
          ? tasks[j].agent + " \u00b7 " + tasks[j].schedule
          : tasks[j].schedule;
        row.appendChild(tn); row.appendChild(tw);
        tasksEl.appendChild(row);
      }
      if (data.task_total > tasks.length) {
        var more = document.createElement("div");
        more.className = "ls-task";
        more.textContent = "+" + (data.task_total - tasks.length) + " more scheduled";
        tasksEl.appendChild(more);
      }
      card.hidden = false;

      // Put focus back on the same agent, and on the same control within it.
      // Only when the element is still there: if that agent has gone away,
      // leaving focus on the body is correct -- moving it to a neighbour would
      // aim the user's next Enter at an agent they never selected.
      if (focusKey !== null) {
        var again = agentsEl.querySelector(
          '.ls-island[data-agent="' + (window.CSS && CSS.escape
            ? CSS.escape(focusKey) : focusKey.replace(/["\\]/g, "\\$&")) + '"]');
        if (again) {
          var target = focusWasMic ? (again.querySelector(".ls-mic") || again) : again;
          target.focus();
        }
      }

      syncFeedFade();
    }

    function pollActivity() {
      fetch("/auth/lock-widgets", { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { if (d) paintActivity(d); })
        .catch(function () { /* offline or off-console: leave the card as is */ });
    }
    if (card) {
      pollActivity();
      setInterval(pollActivity, 15000);
    }

    // -----------------------------------------------------------------------
    // WEATHER. The reading comes from /auth/lock-weather, which does the call
    // out to the forecast API itself. This page never talks to a third party:
    // it renders before sign-in, and a fetch from here would announce the
    // device to an outside host every time the screen lit up.
    //
    // This file knows how to DRAW eight shapes and nothing else. Which shape a
    // given sky gets is policy and lives in the route, so the code that decides
    // "overcast" and the code that decides which icon that is stay together.
    // -----------------------------------------------------------------------
    var weatherEl = document.getElementById("ls-weather");
    var WEATHER_ICONS = {
      clear: '<circle cx="12" cy="12" r="4.1"/><path d="M12 2.6v2.3M12 19.1v2.3M4.3 4.3l1.6 1.6M18.1 18.1l1.6 1.6M2.6 12h2.3M19.1 12h2.3M4.3 19.7l1.6-1.6M18.1 5.9l1.6-1.6"/>',
      night: '<path d="M20.2 14.4A8.3 8.3 0 0 1 9.6 3.8a8.5 8.5 0 1 0 10.6 10.6z"/>',
      partly: '<path d="M8 6.2V4.4M4.3 7.9L3 6.6M12.4 7.9l1.3-1.3M3.2 12.2H1.4"/><path d="M11 10.4a4 4 0 1 0-6.4 3"/><path d="M17.5 20H8.4a3.9 3.9 0 0 1-.5-7.8 5.2 5.2 0 0 1 10 1 3.4 3.4 0 0 1-.4 6.8z"/>',
      cloud: '<path d="M17.5 19.5H8.2a4.2 4.2 0 0 1-.5-8.4 5.6 5.6 0 0 1 10.7 1.1 3.7 3.7 0 0 1-.9 7.3z"/>',
      fog: '<path d="M17.2 14.4H8.3a3.9 3.9 0 0 1-.5-7.7 5.2 5.2 0 0 1 10 1 3.4 3.4 0 0 1-.6 6.7z"/><path d="M4.5 18h15M7 21.3h10"/>',
      drizzle: '<path d="M17.2 14.4H8.3a3.9 3.9 0 0 1-.5-7.7 5.2 5.2 0 0 1 10 1 3.4 3.4 0 0 1-.6 6.7z"/><path d="M9 17.6l-.8 2.2M13 17.6l-.8 2.2M17 17.6l-.8 2.2"/>',
      rain: '<path d="M17.2 13.8H8.3a3.9 3.9 0 0 1-.5-7.7 5.2 5.2 0 0 1 10 1 3.4 3.4 0 0 1-.6 6.7z"/><path d="M8.6 16.6L7 21.4M12.8 16.6l-1.6 4.8M17 16.6l-1.6 4.8"/>',
      snow: '<path d="M17.2 13.8H8.3a3.9 3.9 0 0 1-.5-7.7 5.2 5.2 0 0 1 10 1 3.4 3.4 0 0 1-.6 6.7z"/><path d="M8.4 17.6v3.6M6.8 18.5l3.2 1.8M10 18.5l-3.2 1.8M15.6 17.6v3.6M14 18.5l3.2 1.8M17.2 18.5L14 20.3"/>',
      storm: '<path d="M17.2 13.4H8.3a3.9 3.9 0 0 1-.5-7.7 5.2 5.2 0 0 1 10 1 3.4 3.4 0 0 1-.6 6.7z"/><path d="M13.2 15.6l-3.4 4.1h3.2l-1.3 3.1"/>'
    };

    function paintWeather(d) {
      if (!weatherEl || !d || typeof d.temp !== "number") return;
      weatherEl.textContent = "";

      var icon = document.createElement("div");
      icon.className = "ls-weather-icon";
      icon.setAttribute("aria-hidden", "true");
      // innerHTML with a path from THIS FILE's own table, selected by a key --
      // no server string is ever parsed as markup here. Everything the route
      // sends is written with textContent below.
      icon.innerHTML = '<svg viewBox="0 0 24 24">'
        + (WEATHER_ICONS[d.icon] || WEATHER_ICONS.cloud) + "</svg>";

      var read = document.createElement("div");
      read.className = "ls-weather-read";
      var now = document.createElement("div");
      now.className = "ls-weather-now";
      var temp = document.createElement("span");
      temp.className = "ls-weather-temp";
      temp.textContent = Math.round(d.temp) + "°";
      var label = document.createElement("span");
      label.className = "ls-weather-label";
      label.textContent = d.label || "";
      now.appendChild(temp); now.appendChild(label);

      var sub = document.createElement("div");
      sub.className = "ls-weather-sub";
      var bits = [];
      if (typeof d.high === "number" && typeof d.low === "number") {
        bits.push("H:" + Math.round(d.high) + "°  L:" + Math.round(d.low) + "°");
      }
      if (typeof d.wind === "number") bits.push(Math.round(d.wind) + " mph");
      if (d.place) bits.push(d.place);
      sub.textContent = bits.join("  ·  ");

      read.appendChild(now); read.appendChild(sub);
      weatherEl.appendChild(icon); weatherEl.appendChild(read);
      // The icon is decorative, so the group carries the reading in words.
      weatherEl.setAttribute("aria-label",
        "Weather in " + (d.place || "") + ": " + Math.round(d.temp)
        + " degrees celsius, " + (d.label || "") + ". " + sub.textContent);
      weatherEl.hidden = false;
    }

    function pollWeather() {
      fetch("/auth/lock-weather", { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { if (d) paintWeather(d); })
        // No error text on a lock screen: an unreachable forecast shows as no
        // weather line at all, which is what the screen looked like before.
        .catch(function () { /* offline: leave the row hidden */ });
    }
    if (weatherEl) {
      pollWeather();
      // The route caches for 15 minutes, so a tighter poll would only re-read
      // the same answer. This is the interval that actually moves the number.
      setInterval(pollWeather, 15 * 60 * 1000);
    }

    // -----------------------------------------------------------------------
    // NOTIFICATIONS. One stack per source, collated the way a phone does it.
    //
    // SCRIPTED DEMO CONTENT ONLY, and the route enforces that: this screen
    // renders before sign-in, so real mail or real messages here would be
    // readable by whoever picked the phone up. There is no code path from this
    // stack to a real inbox, which is why there is no gate here to get wrong.
    // -----------------------------------------------------------------------
    var feedEl = document.getElementById("ls-feed");

    // Does the feed actually have somewhere to scroll? Measured, never assumed:
    // on a device with one agent and no notifications the feed still fills the
    // middle of the screen but has nothing to move. TWO behaviours hang off
    // this -- the cut-edge fade below and the unlock-swipe veto near the
    // gestures -- and they must agree, so the 4px tolerance is defined once.
    function feedOverflows() {
      return !!feedEl && feedEl.scrollHeight - feedEl.clientHeight > 4;
    }

    // How far the feed can still travel toward its end, RIGHT NOW. Overflow
    // asks whether the feed is taller than its viewport; this asks where it is
    // scrolled, and they are different questions: a feed scrolled to its bottom
    // overflows and yet cannot move. Both the fade and the unlock veto need
    // this one, so like the overflow tolerance it is defined once.
    //
    // A feed that cannot scroll at all reads as zero room here, which is what
    // it should: it is already at its end.
    function feedScrollRoom() {
      return feedEl ? feedEl.scrollHeight - feedEl.clientHeight - feedEl.scrollTop : 0;
    }

    // Which edges of the feed are cut. Measured rather than assumed: whether
    // this device's screen can hold its agents and notifications at once
    // depends on how many of each it has and how tall the panel is.
    function syncFeedFade() {
      if (!feedEl) return;
      var over = feedOverflows();
      var atTop = feedEl.scrollTop <= 2;
      var atEnd = feedScrollRoom() <= 4;
      var fade = "none";
      if (over) fade = atTop ? "bottom" : (atEnd ? "top" : "both");
      feedEl.setAttribute("data-fade", fade);
    }
    if (feedEl) {
      feedEl.addEventListener("scroll", syncFeedFade, { passive: true });
      window.addEventListener("resize", syncFeedFade);
      syncFeedFade();
    }

    // -----------------------------------------------------------------------
    // THE VIEW SWITCHER. The icon row above the feed chooses which panel the
    // feed shows. Agents is the default and the resting state.
    //
    // It marks the inactive panels with data-off and NEVER touches `hidden`:
    // `hidden` is the poll's way of saying a panel is empty, and the two would
    // otherwise overwrite each other every 15 seconds. See the CSS.
    // -----------------------------------------------------------------------
    var viewsEl = document.getElementById("ls-views");
    var currentView = VIEW_DEFAULT;

    function viewTabs() {
      return viewsEl ? [].slice.call(viewsEl.querySelectorAll(".ls-view-tab")) : [];
    }

    function showView(key, focusTab) {
      if (!VIEWS[key]) key = VIEW_DEFAULT;
      currentView = key;

      var tabs = viewTabs();
      for (var i = 0; i < tabs.length; i++) {
        var on = tabs[i].getAttribute("data-view") === key;
        tabs[i].setAttribute("aria-selected", on ? "true" : "false");
        // One tab stop for the whole row: the arrow keys move within it.
        tabs[i].setAttribute("tabindex", on ? "0" : "-1");
        if (on && focusTab) tabs[i].focus();
      }

      if (feedEl) {
        var panels = feedEl.querySelectorAll("[data-view]");
        for (var j = 0; j < panels.length; j++) {
          if (panels[j].getAttribute("data-view") === key) {
            panels[j].removeAttribute("data-off");
          } else {
            panels[j].setAttribute("data-off", "1");
          }
        }
        feedEl.setAttribute("data-view", key);
        // A new panel is a different height, so the old scroll offset is
        // meaningless and the cut-edge fade is now describing content that is
        // no longer on screen. Reset, then re-measure -- do not assume.
        feedEl.scrollTop = 0;
      }

      renderView(key);
      syncFeedFade();
    }

    // Panels that are built on demand rather than polled. Agents and alerts
    // are already kept current by the poll and want nothing here.
    function renderView(key) {
      // Numbers that are not on screen are not worth a request every 3s, and
      // the CPU reading is a DELTA -- polling it while hidden would hand the
      // stats view a first sample taken minutes ago.
      if (key === "stats") startStats(); else stopStats();
      if (key !== "agents" && key !== "alerts" && key !== "stats") renderPlaceholder(key);
    }

    // -----------------------------------------------------------------------
    // THE STATS VIEW.
    //
    // ⛔ THE RULE FOR THIS PANEL: a reading the hardware does not expose is
    // shown as "--", never as a number. Probed on the handset before any of
    // this was designed -- the cDSP (the "NPU") reports state and nothing else,
    // and the GPU's devfreq node has no `load` file. An "NPU usage 34%" here
    // would be invented. This is the first screen the phone shows anyone.
    // -----------------------------------------------------------------------
    var statsEl = document.getElementById("ls-stats");
    var statsTimer = null;

    function stopStats() {
      if (statsTimer) { window.clearInterval(statsTimer); statsTimer = null; }
    }

    function startStats() {
      stopStats();
      pollStats();
      statsTimer = window.setInterval(pollStats, 3000);
    }

    function pollStats() {
      fetch("/auth/lock-stats", { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { if (d) paintStats(d); })
        .catch(function () { /* a lock screen does not show network errors */ });
    }

    function statRow(label, value, pct) {
      var row = document.createElement("div");
      row.className = "ls-stat";
      var top = document.createElement("div");
      top.className = "ls-stat-top";
      var l = document.createElement("span");
      l.className = "ls-stat-label";
      l.textContent = label;
      var v = document.createElement("span");
      v.className = "ls-stat-value";
      v.textContent = value;
      top.appendChild(l); top.appendChild(v);
      row.appendChild(top);
      // A bar ONLY when there is a real percentage behind it. A meter drawn at
      // zero because nothing was measured looks exactly like a meter drawn at
      // zero because the thing is idle.
      if (typeof pct === "number") {
        var track = document.createElement("div");
        track.className = "ls-stat-track";
        var fill = document.createElement("div");
        fill.className = "ls-stat-fill";
        fill.style.width = Math.max(0, Math.min(100, pct)) + "%";
        track.appendChild(fill);
        row.appendChild(track);
      }
      return row;
    }

    function gib(kb) { return (kb / 1048576).toFixed(1) + " GB"; }

    function paintStats(d) {
      if (!statsEl) return;
      statsEl.textContent = "";
      // Same as the placeholders: `hidden` meant "empty", and it no longer is.
      statsEl.hidden = false;

      var card = document.createElement("div");
      card.className = "ls-stat-card";

      var cores = d.cpu_cores ? " · " + d.cpu_cores + " cores" : "";
      card.appendChild(statRow(
        "CPU" + cores,
        typeof d.cpu_percent === "number" ? d.cpu_percent.toFixed(0) + "%" : "--",
        typeof d.cpu_percent === "number" ? d.cpu_percent : null
      ));

      if (d.memory) {
        card.appendChild(statRow(
          "Memory",
          gib(d.memory.used_kb) + " / " + gib(d.memory.total_kb),
          d.memory.percent
        ));
      } else {
        card.appendChild(statRow("Memory", "--"));
      }

      // GPU: LABELLED AS FREQUENCY, because that is what it is. The bar is the
      // clock against its own maximum, and the caption says so -- a parked GPU
      // is not "23% used".
      if (d.gpu && d.gpu.freq_hz) {
        var mhz = Math.round(d.gpu.freq_hz / 1000000);
        var maxMhz = d.gpu.max_freq_hz ? Math.round(d.gpu.max_freq_hz / 1000000) : 0;
        card.appendChild(statRow(
          "GPU clock",
          maxMhz ? mhz + " / " + maxMhz + " MHz" : mhz + " MHz",
          maxMhz ? (mhz * 100 / maxMhz) : null
        ));
        if (typeof d.gpu.active_percent === "number") {
          var note = document.createElement("div");
          note.className = "ls-stat-note";
          note.textContent = "Above idle clock " + d.gpu.active_percent.toFixed(0)
            + "% of uptime. The GPU reports no utilisation counter.";
          card.appendChild(note);
        }
      } else {
        card.appendChild(statRow("GPU clock", "--"));
      }

      statsEl.appendChild(card);

      // The remote processors, as state chips. This is where the NPU lives, and
      // running/offline is genuinely all it reports.
      if (d.dsps && d.dsps.length) {
        var chips = document.createElement("div");
        chips.className = "ls-chips";
        for (var i = 0; i < d.dsps.length; i++) {
          var c = document.createElement("span");
          c.className = "ls-chip";
          var up = String(d.dsps[i].state || "") === "running";
          c.setAttribute("data-on", up ? "1" : "0");
          c.textContent = (d.dsps[i].name || "dsp") + " · " + (d.dsps[i].state || "unknown");
          chips.appendChild(c);
        }
        statsEl.appendChild(chips);
        var why = document.createElement("div");
        why.className = "ls-stat-note";
        why.textContent = "Accelerators report running or offline only — no usage counter exists for them.";
        statsEl.appendChild(why);
      }

      // "Nobody asked" and "none loaded" are different answers.
      var models = document.createElement("div");
      models.className = "ls-stat-note";
      models.textContent = d.models
        ? (d.models.length ? d.models.join(", ") : "No models loaded.")
        : "Loaded models are not reported by this device.";
      statsEl.appendChild(models);

      syncFeedFade();
    }

    // The four views that have no data source yet say so plainly, once.
    var PLACEHOLDERS = {
      phone: ["Phone", "Calls and dialler are not wired up on this device yet."],
      mailbox: ["Mailbox", "No mail account is connected to this device yet."],
      apps: ["Apps", "Installed apps will appear here."],
      settings: ["Settings", "Unlock to change settings."]
    };

    function renderPlaceholder(key) {
      var host = document.getElementById("ls-" + key);
      var text = PLACEHOLDERS[key];
      if (!host || !text || host.firstChild) return;
      var wrap = document.createElement("div");
      wrap.className = "ls-empty";
      var b = document.createElement("b");
      b.textContent = text[0];
      var p = document.createElement("span");
      p.textContent = text[1];
      wrap.appendChild(b); wrap.appendChild(p);
      host.appendChild(wrap);
      // `hidden` on these panels means "nothing in it yet", which is true in
      // the markup and false from here on. Leaving it set would hide the panel
      // even while its own tab is selected.
      host.hidden = false;
    }

    if (viewsEl) {
      viewsEl.addEventListener("click", function (ev) {
        var tab = ev.target.closest(".ls-view-tab");
        if (!tab) return;
        showView(tab.getAttribute("data-view"), false);
      });

      // Arrow-key traversal is what makes this a tablist rather than seven
      // buttons. Home/End because the row is long enough for it to matter.
      viewsEl.addEventListener("keydown", function (ev) {
        var tabs = viewTabs();
        if (!tabs.length) return;
        var at = -1;
        for (var i = 0; i < tabs.length; i++) {
          if (tabs[i].getAttribute("data-view") === currentView) { at = i; break; }
        }
        if (at < 0) return;
        var to = -1;
        if (ev.key === "ArrowRight") to = (at + 1) % tabs.length;
        else if (ev.key === "ArrowLeft") to = (at - 1 + tabs.length) % tabs.length;
        else if (ev.key === "Home") to = 0;
        else if (ev.key === "End") to = tabs.length - 1;
        if (to < 0) return;
        ev.preventDefault();
        showView(tabs[to].getAttribute("data-view"), true);
      });

      showView(VIEW_DEFAULT, false);
    }

    var notifsEl = document.getElementById("ls-notifs");
    var NOTIF_GLYPHS = {
      mail: '<path d="M3 6.5h18v11H3z"/><path d="M3.4 7l8.6 6 8.6-6"/>',
      phone: '<path d="M6.2 3.5l2.4 4-1.9 2a11 11 0 0 0 5.8 5.8l2-1.9 4 2.4v3.1a1.7 1.7 0 0 1-1.9 1.7A16.5 16.5 0 0 1 3.4 5.4 1.7 1.7 0 0 1 5.1 3.5z"/>',
      sms: '<path d="M4 4.5h16v11H8.5L4 19z"/><path d="M8 8.6h8M8 11.6h5"/>'
    };

    // Which stacks the user has fanned out, kept OUTSIDE the paint so a repaint
    // does not fold the pile the user just opened.
    var notifOpen = {};
    // Every rendered time label, so the minutes can tick without rebuilding the
    // DOM -- a rebuild would restart every entrance animation on a screen the
    // user may be looking at.
    var notifClocks = [];

    function whenText(ts) {
      var secs = (Date.now() / 1000) - ts;
      if (secs < 60) return "now";
      var mins = Math.round(secs / 60);
      if (mins < 60) return mins + "m ago";
      var hrs = Math.round(mins / 60);
      if (hrs < 24) return hrs + "h ago";
      var days = Math.round(hrs / 24);
      return days <= 1 ? "Yesterday" : days + "d ago";
    }

    function notifCard(group, item) {
      var el = document.createElement("div");
      el.className = "ls-notif";

      var tile = document.createElement("div");
      tile.className = "ls-notif-tile";
      tile.setAttribute("aria-hidden", "true");
      // Only a colour literal is ever taken from the payload, and only after it
      // is checked -- an unchecked value here would be written into a style.
      if (/^#[0-9a-fA-F]{3,8}$/.test(group.tint || "")) {
        tile.style.setProperty("--ls-n", group.tint);
      }
      if (group.glyph && NOTIF_GLYPHS[group.glyph]) {
        tile.innerHTML = '<svg viewBox="0 0 24 24">' + NOTIF_GLYPHS[group.glyph] + "</svg>";
      } else {
        tile.textContent = (group.mono || group.app || "?").slice(0, 2);
      }

      var body = document.createElement("div");
      body.className = "ls-notif-body";

      var meta = document.createElement("div");
      meta.className = "ls-notif-meta";
      var app = document.createElement("span");
      app.className = "ls-notif-app";
      app.textContent = group.app || "";
      meta.appendChild(app);
      // The pile count rides on the newest card, where the eye already is. CSS
      // hides it once the stack is fanned out, when the cards are the count.
      if (group.items.length > 1 && item === group.items[0]) {
        var count = document.createElement("span");
        count.className = "ls-notif-count";
        count.textContent = group.items.length;
        meta.appendChild(count);
      }
      var when = document.createElement("span");
      when.className = "ls-notif-when";
      when.textContent = whenText(item.at);
      notifClocks.push({ el: when, at: item.at });
      meta.appendChild(when);

      var title = document.createElement("div");
      title.className = "ls-notif-title";
      title.textContent = item.title || "";          // textContent, never innerHTML
      var text = document.createElement("div");
      text.className = "ls-notif-text";
      text.textContent = item.text || "";

      body.appendChild(meta); body.appendChild(title);
      if (item.text) body.appendChild(text);
      el.appendChild(tile); el.appendChild(body);
      return el;
    }

    function notifGroup(group) {
      var el = document.createElement("div");
      el.className = "ls-notif-group";
      // A stack OPENS, so it is a button: reachable by tab, operable by Enter,
      // same rule the islands follow.
      el.setAttribute("role", "button");
      el.setAttribute("tabindex", "0");
      var open = notifOpen[group.source] ? "1" : "0";
      el.setAttribute("data-open", open);
      el.setAttribute("aria-expanded", open === "1" ? "true" : "false");
      el.setAttribute("aria-label", group.items.length > 1
        ? group.app + ", " + group.items.length + " notifications. "
          + (open === "1" ? "Collapse." : "Show all.")
        : group.app + ": " + (group.items[0].title || ""));

      for (var i = 0; i < group.items.length; i++) {
        el.appendChild(notifCard(group, group.items[i]));
      }

      function toggle() {
        var nowOpen = el.getAttribute("data-open") !== "1";
        // Single-item stacks have nothing to fan out.
        if (group.items.length < 2) return;
        notifOpen[group.source] = nowOpen;
        el.setAttribute("data-open", nowOpen ? "1" : "0");
        el.setAttribute("aria-expanded", nowOpen ? "true" : "false");
        el.setAttribute("aria-label", group.app + ", " + group.items.length
          + " notifications. " + (nowOpen ? "Collapse." : "Show all."));
        syncFeedFade();
      }
      el.addEventListener("click", toggle);
      el.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter" || ev.key === " " || ev.key === "Spacebar") {
          ev.preventDefault();
          toggle();
        }
      });
      return el;
    }

    function paintNotifications(data) {
      if (!notifsEl) return;
      // Same rule as the islands: never rebuild under an open sheet.
      var sheetNow = screenEl ? screenEl.getAttribute("data-sheet") : "none";
      if (sheetNow && sheetNow !== "none") return;
      var groups = (data && data.groups) || [];
      notifsEl.textContent = "";
      notifClocks = [];
      if (!groups.length) { notifsEl.hidden = true; return; }
      for (var i = 0; i < groups.length; i++) {
        if (!groups[i].items || !groups[i].items.length) continue;
        notifsEl.appendChild(notifGroup(groups[i]));
      }
      notifsEl.hidden = !notifsEl.firstChild;
      syncFeedFade();
    }

    function pollNotifications() {
      fetch("/auth/lock-notifications", { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { if (d) paintNotifications(d); })
        // 404 is the ordinary answer with demo content off: no stack, no error.
        .catch(function () { /* leave the stack as it is */ });
    }
    if (notifsEl) {
      pollNotifications();
      setInterval(pollNotifications, 15 * 60 * 1000);
      // The minutes move far faster than the content does, so they are retouched
      // in place rather than by re-rendering the stack.
      setInterval(function () {
        for (var i = 0; i < notifClocks.length; i++) {
          notifClocks[i].el.textContent = whenText(notifClocks[i].at);
        }
      }, 60000);
    }

    // Switching to the password form STAYS on the lock screen.
    //
    // This used to drop .lockscreen-on to get the ordinary card and the shared
    // keyboard back. On a handset kiosk that is wrong in three visible ways at
    // once: the page loses `overflow:hidden` and grows a chromium SCROLLBAR
    // down the edge, the keyboard's floating toggle button reappears over the
    // screen, and the whole lock screen visually falls apart mid-sign-in. The
    // password form is just another thing that rises in the passcode sheet, so
    // raise the keyboard for it and leave the chrome alone.
    var toPw = document.getElementById("use-password");
    if (toPw) {
      toPw.addEventListener("click", function () {
        openSheet("passcode");
        var pw = document.querySelector("#pw-panel input[type=password]");
        if (pw && window.taosOSK) {
          window.taosOSK.enable();
          window.taosOSK.focusField(pw);
        }
        window.setTimeout(syncKeyboard, 60);
      });
    }

    // Coming BACK to the PIN closes the keyboard again: the keypad is this
    // screen's input method and the two must never both be up.
    var toPin = document.getElementById("use-pin");
    if (toPin) {
      toPin.addEventListener("click", function () {
        if (window.taosOSK) window.taosOSK.disable();
        setKeyboardOffset(0);
      });
    }

    // -----------------------------------------------------------------------
    // SHEETS. One state variable (data-sheet on .lockscreen) drives the scrim,
    // the head blur, the unlock bar and which sheet is raised. Every open and
    // close goes through openSheet/closeSheet so those can never disagree.
    // -----------------------------------------------------------------------
    var scrim     = document.getElementById("ls-scrim");
    var unlockBar = document.getElementById("ls-unlock");
    var unlockBtn = document.getElementById("ls-unlock-btn");
    var chatSheet = document.getElementById("ls-chat");
    var decSheet  = document.getElementById("ls-decision");
    var lastFocus = null;

    if (screenEl) screenEl.setAttribute("data-sheet", "none");

    var voiceSheet = document.getElementById("ls-voice");

    function sheetEl(name) {
      if (name === "chat") return chatSheet;
      if (name === "decision") return decSheet;
      if (name === "voice") return voiceSheet;
      if (name === "passcode") return document.getElementById("ls-foot");
      return null;
    }

    function openSheet(name) {
      if (!screenEl) return;
      var current = screenEl.getAttribute("data-sheet");
      if (current === name) return;
      // Remember where the user was so closing returns them there rather than
      // dropping focus to the top of the document.
      if (current === "none") lastFocus = document.activeElement;
      // Only ever one sheet: hide whatever was up before revealing the next.
      if (current && current !== "none") {
        var prev = sheetEl(current);
        if (prev && prev.id !== "ls-foot") prev.hidden = true;
      }
      var el = sheetEl(name);
      if (el) el.hidden = false;
      if (scrim) scrim.hidden = false;
      // The attribute is set on the NEXT frame when the sheet was hidden a
      // moment ago: a transform transition on an element that was display:none
      // in the same frame has no start value and the sheet would appear
      // instantly instead of sliding.
      requestAnimationFrame(function () {
        screenEl.setAttribute("data-sheet", name);
      });
      if (unlockBtn) unlockBtn.setAttribute("aria-expanded", name === "passcode" ? "true" : "false");
    }

    function closeSheet() {
      if (!screenEl) return;
      var current = screenEl.getAttribute("data-sheet");
      if (!current || current === "none") return;
      screenEl.setAttribute("data-sheet", "none");
      if (unlockBtn) unlockBtn.setAttribute("aria-expanded", "false");
      // The composer must not keep the keyboard up over a closed sheet, and the
      // microphone must never outlive the sheet that opened it.
      if (window.taosOSK) window.taosOSK.disable();
      stopVoice();
      setKeyboardOffset(0);
      var el = sheetEl(current);
      // Wait out the slide before hiding, or the sheet vanishes mid-animation.
      // hidden (not display) so it also leaves the accessibility tree.
      window.setTimeout(function () {
        if (screenEl.getAttribute("data-sheet") !== "none") return;
        if (el && el.id !== "ls-foot") el.hidden = true;
        if (scrim) scrim.hidden = true;
      }, 400);
      if (lastFocus && lastFocus.focus) { try { lastFocus.focus(); } catch (e) {} }
      lastFocus = null;
    }

    // Tap-to-dismiss on the scrim, but only a real tap: a drag that began on the
    // sheet and ended over the scrim is not a dismissal.
    if (scrim) {
      var sx = 0, sy = 0;
      scrim.addEventListener("pointerdown", function (ev) { sx = ev.clientX; sy = ev.clientY; });
      scrim.addEventListener("click", function (ev) {
        if (Math.abs(ev.clientX - sx) > 12 || Math.abs(ev.clientY - sy) > 12) return;
        closeSheet();
      });
    }
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") closeSheet();
    });
    var chatClose = document.getElementById("ls-chat-close");
    if (chatClose) chatClose.addEventListener("click", closeSheet);
    var decClose = document.getElementById("ls-decision-close");
    if (decClose) decClose.addEventListener("click", closeSheet);

    // -----------------------------------------------------------------------
    // KEYBOARD OFFSET. The sheets are anchored to the bottom edge and the
    // on-screen keyboard is a fixed panel on that same edge, so without this
    // the composer sits UNDER the keys. The OSK reserves its space as body
    // padding, which does nothing for a fixed element -- so measure the panel
    // itself rather than duplicating its height as a guess that drifts when the
    // keyboard switches between its letter, symbol and numeric layers.
    // -----------------------------------------------------------------------
    function setKeyboardOffset(px) {
      document.documentElement.style.setProperty("--ls-kb", (px || 0) + "px");
    }

    var oskPanel = document.querySelector(".osk");
    function syncKeyboard() {
      if (!oskPanel) { oskPanel = document.querySelector(".osk"); }
      if (!oskPanel || oskPanel.hidden) { setKeyboardOffset(0); return; }
      setKeyboardOffset(oskPanel.offsetHeight);
      // Switching keyboard layers (letters/symbols/numeric) changes its height
      // and therefore the sheet's, so re-anchor the thread each time.
      if (screenEl && screenEl.getAttribute("data-sheet") === "chat") bottomOut();
    }
    if (window.ResizeObserver) {
      var ro = new ResizeObserver(syncKeyboard);
      if (oskPanel) ro.observe(oskPanel);
      // The panel is appended on DOMContentLoaded, which may not have happened
      // yet when this runs, so pick it up once it exists.
      var mo = new MutationObserver(function () {
        var found = document.querySelector(".osk");
        if (found && found !== oskPanel) { oskPanel = found; ro.observe(found); }
        syncKeyboard();
      });
      mo.observe(document.body, { childList: true, attributes: true, attributeFilter: ["hidden", "class"], subtree: true });
    }

    // -----------------------------------------------------------------------
    // UNLOCK. A button AND a swipe, not a swipe alone: the gesture is the
    // shortcut for someone who knows it, the button is what makes the phone
    // openable by someone who does not (or cannot make the drag at all).
    // -----------------------------------------------------------------------
    function openPasscode() {
      openSheet("passcode");
      var pinInput = document.getElementById("pin-input");
      // Focus the field so a physical keyboard types into it, but do NOT raise
      // the shared on-screen keyboard: this screen draws its own keypad.
      if (pinInput) { try { pinInput.focus({ preventScroll: true }); } catch (e) { pinInput.focus(); } }
    }
    if (unlockBtn) unlockBtn.addEventListener("click", openPasscode);

    // Two SEPARATE gestures, deliberately not one handler on the body.
    //
    // A single body-wide handler that read any downward drag as "dismiss" made
    // the conversation impossible to scroll: dragging down through older
    // messages IS a downward drag, so the sheet closed under the finger. The
    // dismiss gesture now lives ONLY on the sheet's header -- the grabber is
    // the handle, which is what the grabber is for -- and the unlock swipe is
    // only armed while nothing is open.
    // `veto` decides, FROM THE TOUCH THAT STARTED THE GESTURE, that this drag
    // is not ours at all. It is deliberately latched at touchstart and never
    // re-read: over a long drag the finger travels far from where it landed, so
    // touchend's target is a different element than the one the user began on.
    // Reading the end would misjudge exactly the gesture we mean to exclude.
    function swipe(surface, onUp, onDown, guard, veto) {
      var y0 = null, x0 = null, moved = false, vetoed = false;
      surface.addEventListener("touchstart", function (ev) {
        var t = ev.touches[0];
        y0 = t.clientY; x0 = t.clientX; moved = false;
        vetoed = !!(veto && veto(ev));
      }, { passive: true });
      surface.addEventListener("touchmove", function (ev) {
        if (y0 === null) return;
        var t = ev.touches[0];
        if (Math.abs(t.clientY - y0) > 10 || Math.abs(t.clientX - x0) > 10) moved = true;
      }, { passive: true });
      surface.addEventListener("touchend", function (ev) {
        if (y0 === null) return;
        var t = ev.changedTouches[0];
        var dy = t.clientY - y0, dx = t.clientX - x0;
        var dead = vetoed;
        y0 = null; x0 = null; vetoed = false;
        if (dead) return;
        // Vertical intent, and a long one. The threshold is deliberately well
        // past a scroll flick, and a drag more horizontal than vertical is
        // never a dismiss.
        if (!moved || Math.abs(dy) < 90 || Math.abs(dx) > Math.abs(dy) * 0.6) return;
        if (guard && !guard()) return;
        if (dy < 0 && onUp) onUp();
        else if (dy > 0 && onDown) onDown();
      }, { passive: true });
    }

    // Unlock: only from the resting screen, so it can never fight a sheet.
    //
    // Swipe-up-to-unlock is armed over the WHOLE resting screen on purpose --
    // this is a phone in a hand, and a user should not have to find a target.
    // The feed is the single exception: reading to the end of a long
    // notification list is one long upward drag, which is indistinguishable
    // from an unlock by distance alone, so the reader was being thrown into the
    // keypad. `overscroll-behavior: contain` cannot help; it stops scroll
    // CHAINING, not an ancestor's JS listener.
    //
    // The exception is narrowed to the case that actually collides: a feed that
    // still has somewhere to go IN THE DIRECTION THIS DRAG WOULD TAKE IT. An
    // upward swipe is both the unlock gesture and the gesture that scrolls the
    // feed toward its end, so the feed has a claim on it only while it can
    // still move that way.
    //
    // Asking overflow alone was the bug (tsk-36i6ed): at the bottom of a long
    // feed an upward drag cannot scroll -- there is nowhere left -- and was
    // vetoed anyway, so nothing happened at all over most of the glass. That is
    // the ordinary flow: read to the end, then swipe up to unlock. Room is
    // read once, at touchstart, and it is a tolerance rather than an equality
    // because scrollTop, clientHeight and scrollHeight are all fractional under
    // a non-integer device pixel ratio and never sum exactly.
    //
    // A feed that cannot scroll at all has no room either, so it still unlocks
    // across the whole screen -- the device with one agent and no
    // notifications, which is the first screen a new user ever sees.
    swipe(document.body, openPasscode, null, function () {
      return !screenEl || screenEl.getAttribute("data-sheet") === "none";
    }, function (ev) {
      var t = ev.target;
      if (!t || !t.closest || !t.closest(".ls-feed")) return false;
      return feedScrollRoom() > 4;
    });
    // Dismiss: only by dragging the sheet's own header.
    var chatHead = chatSheet ? chatSheet.querySelector(".ls-sheet-head") : null;
    if (chatHead) swipe(chatHead, null, closeSheet);
    var decHead = decSheet ? decSheet.querySelector(".ls-sheet-head") : null;
    if (decHead) swipe(decHead, null, closeSheet);
    var footEl = document.getElementById("ls-foot");
    if (footEl) swipe(footEl, null, closeSheet);

    // -----------------------------------------------------------------------
    // CONVERSATION SHEET.
    // -----------------------------------------------------------------------
    var msgsEl    = document.getElementById("ls-msgs");
    var composer  = document.getElementById("ls-compose-input");
    var sendBtn   = document.getElementById("ls-send");
    var chatName  = document.getElementById("ls-chat-name");
    var chatSub   = document.getElementById("ls-chat-sub");
    var chatAv    = document.getElementById("ls-chat-avatar");
    var chatAgent = null;

    function slugFor(name) {
      // Must match the server's _avatar_slug exactly or the thread 404s.
      var out = "";
      var lower = String(name).trim().toLowerCase();
      for (var i = 0; i < lower.length; i++) {
        var ch = lower[i];
        if (/[a-z0-9]/.test(ch)) out += ch;
        else if (out && out[out.length - 1] !== "-") out += "-";
      }
      return out.replace(/^-+|-+$/g, "");
    }

    function fillAvatar(box, agent) {
      box.textContent = "";
      // Same rule as the island: the product mark is contained, not cropped.
      if (agent.system) box.setAttribute("data-system", "1");
      else box.removeAttribute("data-system");
      var hue = hueFor(agent.name || "agent");
      box.style.setProperty("--ls-a", "hsl(" + hue + " 62% 58%)");
      box.style.setProperty("--ls-b", "hsl(" + ((hue + 28) % 360) + " 58% 38%)");
      if (agent.avatar) {
        var img = document.createElement("img");
        img.alt = ""; img.src = agent.avatar;
        img.addEventListener("error", function () {
          img.remove();
          if (!agent.system) box.textContent = initials(agent.name || "agent");
        });
        box.appendChild(img);
      } else {
        box.textContent = initials(agent.name || "agent");
      }
    }

    function dayLabel(d) {
      var today = new Date(); today.setHours(0, 0, 0, 0);
      var that = new Date(d.getTime()); that.setHours(0, 0, 0, 0);
      var days = Math.round((today - that) / 86400000);
      if (days === 0) return "Today";
      if (days === 1) return "Yesterday";
      if (days < 7) return d.toLocaleDateString([], { weekday: "long" });
      return d.toLocaleDateString([], { day: "numeric", month: "long" });
    }

    function bubble(msg) {
      var b = document.createElement("div");
      b.className = "ls-msg";
      b.setAttribute("data-role", msg.role === "user" ? "user" : "agent");
      b.textContent = msg.text || "";     // textContent, never innerHTML
      return b;
    }

    function renderThread(messages) {
      msgsEl.textContent = "";
      var lastDay = "";
      for (var i = 0; i < messages.length; i++) {
        var m = messages[i];
        var when = new Date((m.at || 0) * 1000);
        var label = dayLabel(when);
        if (label !== lastDay) {
          var sep = document.createElement("div");
          sep.className = "ls-day";
          sep.textContent = label;
          msgsEl.appendChild(sep);
          lastDay = label;
        }
        msgsEl.appendChild(bubble(m));
      }
      // Open at the newest message, the way every messaging app does. Set
      // directly rather than scrollIntoView so it does not also scroll the page
      // behind the sheet.
      //
      // Twice, on the next frame: setting scrollTop in the same frame the
      // bubbles were appended measures a scrollHeight that layout has not
      // finished computing, and the thread opens with its last message clipped
      // behind the composer. The second frame catches the reflow that wrapping
      // the final bubble causes. Measured on the device.
      bottomOut();
    }

    // Anchor the thread to its newest message. Repeated across frames because
    // the height it is measuring against keeps changing underneath it: bubbles
    // wrap on layout, and the keyboard shortens the sheet a moment later.
    function bottomOut() {
      if (!msgsEl) return;
      msgsEl.scrollTop = msgsEl.scrollHeight;
      requestAnimationFrame(function () {
        msgsEl.scrollTop = msgsEl.scrollHeight;
        requestAnimationFrame(function () {
          msgsEl.scrollTop = msgsEl.scrollHeight;
        });
      });
    }

    function openChat(agent) {
      chatAgent = agent;
      chatName.textContent = agent.name || "agent";
      chatSub.textContent = agent.demo ? "Demo conversation" : (agent.status || "");
      fillAvatar(chatAv, agent);
      msgsEl.textContent = "";
      composer.value = "";
      if (sendBtn) sendBtn.disabled = true;
      openSheet("chat");

      // The keyboard comes up with the sheet: this is a conversation, and the
      // reason to open one is to say something. Waiting for a tap on the field
      // costs a tap and leaves the sheet looking like a read-only transcript.
      window.setTimeout(function () {
        if (!screenEl || screenEl.getAttribute("data-sheet") !== "chat") return;
        if (window.taosOSK) {
          window.taosOSK.enable();
          window.taosOSK.focusField(composer);
        }
        window.setTimeout(function () {
          syncKeyboard();
          // Raising the keyboard SHORTENS the sheet, so the scroll position
          // computed for the full-height sheet is no longer the bottom. Without
          // this the thread opens parked mid-conversation with the newest
          // message behind the composer.
          bottomOut();
        }, 60);
      }, 420);

      fetch("/auth/lock-thread/" + encodeURIComponent(slugFor(agent.name || "")), {
        credentials: "same-origin"
      }).then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (!d || !d.messages || !d.messages.length) {
            var empty = document.createElement("div");
            empty.className = "ls-day";
            empty.textContent = "No messages yet";
            msgsEl.appendChild(empty);
            return;
          }
          renderThread(d.messages);
        })
        .catch(function () { /* leave the thread empty rather than erroring */ });
    }

    if (composer) {
      composer.addEventListener("input", function () {
        if (sendBtn) sendBtn.disabled = !composer.value.trim();
      });
      // Typing is the ONE place on this screen that wants the full keyboard.
      // The lock screen keeps the shared OSK disabled for the passcode (it has
      // its own keypad), so it has to be turned back on here and off again on
      // close -- which closeSheet does.
      composer.addEventListener("focus", function () {
        if (window.taosOSK) {
          window.taosOSK.enable();
          window.taosOSK.focusField(composer);
        }
        window.setTimeout(syncKeyboard, 60);
      });
    }

    function send() {
      if (!composer) return;
      var text = composer.value.trim();
      if (!text) return;
      var now = Date.now() / 1000;
      msgsEl.appendChild(bubble({ role: "user", text: text, at: now }));
      composer.value = "";
      if (sendBtn) sendBtn.disabled = true;
      msgsEl.scrollTop = msgsEl.scrollHeight;
      // A reply only comes back in a demo thread. Outside demo mode there is no
      // agent on the other end of this sheet -- the lock screen is pre-auth and
      // deliberately cannot reach the chat store -- so inventing a reply would
      // be telling the user something happened when nothing did.
      if (!chatAgent || !chatAgent.demo) return;
      window.setTimeout(function () {
        if (!screenEl || screenEl.getAttribute("data-sheet") !== "chat") return;
        msgsEl.appendChild(bubble({
          role: "agent",
          text: "Got it — I'll pick that up.",
          at: Date.now() / 1000
        }));
        msgsEl.scrollTop = msgsEl.scrollHeight;
      }, 900);
    }
    if (sendBtn) sendBtn.addEventListener("click", send);
    if (composer) {
      composer.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter") { ev.preventDefault(); send(); }
      });
    }

    // -----------------------------------------------------------------------
    // DECISION SHEET. Approve/Deny on a LOCKED screen is deliberately limited:
    // a demo prompt resolves in place, but a real decision carries an id and
    // answering it is an authenticated write. The lock screen does not hold a
    // session, so the honest move is to take the intent, raise the passcode and
    // let the answer happen as the signed-in user -- never to accept a decision
    // from whoever happens to be holding the phone.
    // -----------------------------------------------------------------------
    var decQ     = document.getElementById("ls-decision-q");
    var decMeta  = document.getElementById("ls-decision-meta");
    var decNote  = document.getElementById("ls-decision-note");
    var decName  = document.getElementById("ls-decision-name");
    var decAv    = document.getElementById("ls-decision-avatar");
    var decActs  = document.getElementById("ls-decision-acts");
    var decDone  = document.getElementById("ls-decision-done");

    function openDecision(agent) {
      var d = agent.decision || {};
      decName.textContent = agent.name || "agent";
      fillAvatar(decAv, agent);
      decQ.textContent = d.question || "This agent needs a decision.";
      decMeta.textContent = d.priority && d.priority !== "normal"
        ? d.priority.charAt(0).toUpperCase() + d.priority.slice(1) + " priority"
        : "";
      decNote.hidden = !!d.id;
      decNote.textContent = d.id ? "" : "Demo prompt — nothing is actually approved.";
      decActs.hidden = false;
      decDone.hidden = true;
      decActs.setAttribute("data-decision-id", d.id || "");
      openSheet("decision");
    }

    if (decActs) {
      decActs.addEventListener("click", function (ev) {
        var btn = ev.target.closest(".ls-act");
        if (!btn) return;
        var approved = btn.getAttribute("data-act") === "approve";
        var id = decActs.getAttribute("data-decision-id") || "";
        if (!id) {
          // Demo prompt: resolve in place and say so.
          decActs.hidden = true;
          decDone.hidden = false;
          decDone.textContent = approved ? "Approved (demo)" : "Denied (demo)";
          window.setTimeout(closeSheet, 1100);
          return;
        }
        // Real decision: this needs a session, so send the user to the passcode
        // and let them answer it signed in.
        decActs.hidden = true;
        decDone.hidden = false;
        decDone.textContent = "Unlock to " + (approved ? "approve" : "deny") + " this.";
        window.setTimeout(openPasscode, 700);
      });
    }

    // -----------------------------------------------------------------------
    // THE AGENT MENU. Long-pressing an island's AVATAR opens it.
    //
    // ⚠ NOTHING HERE ACTS ON THE AGENT. This screen renders BEFORE sign-in, so
    // a "Stop agent" that stopped an agent would let anyone holding the locked
    // phone kill the work on it. The menu collects the INTENT and then asks for
    // the passcode -- which is exactly what the decision sheet above already
    // does ("Unlock to approve this"), so this is that rule applied again
    // rather than a new one. The intent is handed over only after the unlock.
    // -----------------------------------------------------------------------
    var menuEl = null;
    var menuReturn = null;

    // What the user asked for before unlocking. Read by the console once it is
    // signed in; deliberately not sent anywhere from this screen.
    window.__lsPendingAgentAction = null;

    // Used as a scroll/resize listener, where the argument is an Event. It must
    // be its own function: passing closeAgentMenu straight to addEventListener
    // hands it the Event as `restoreFocus`, which is truthy, so every scroll
    // would yank focus back to the island.
    function dismissAgentMenu() { closeAgentMenu(false); }

    function closeAgentMenu(restoreFocus) {
      if (!menuEl) return;
      menuEl.remove();
      menuEl = null;
      document.removeEventListener("pointerdown", onDocPointer, true);
      document.removeEventListener("keydown", onMenuKey, true);
      window.removeEventListener("resize", dismissAgentMenu);
      if (feedEl) feedEl.removeEventListener("scroll", dismissAgentMenu);
      // Focus goes back where it came from, or the island the menu was about
      // becomes unreachable by keyboard after the menu closes.
      if (restoreFocus && menuReturn && document.contains(menuReturn)) {
        try { menuReturn.focus(); } catch (e) {}
      }
      menuReturn = null;
    }

    function onDocPointer(ev) {
      if (menuEl && !menuEl.contains(ev.target)) closeAgentMenu(false);
    }

    function onMenuKey(ev) {
      if (!menuEl) return;
      if (ev.key === "Escape") { ev.preventDefault(); closeAgentMenu(true); return; }
      if (ev.key !== "Tab") return;
      // The menu is modal while it is open: Tab must not walk out of it and
      // leave an open menu behind with focus somewhere on the screen under it.
      var items = [].slice.call(menuEl.querySelectorAll(".ls-menu-item:not([disabled])"));
      if (!items.length) return;
      var first = items[0], last = items[items.length - 1];
      if (ev.shiftKey && document.activeElement === first) {
        ev.preventDefault(); last.focus();
      } else if (!ev.shiftKey && document.activeElement === last) {
        ev.preventDefault(); first.focus();
      }
    }

    // Hand the action to the passcode. The label tells the user what they are
    // unlocking FOR -- an unexplained keypad after a menu tap reads as the
    // phone having simply locked itself again.
    function requireUnlock(agent, action, label) {
      window.__lsPendingAgentAction = {
        agent: agent.name || "", action: action, at: Date.now()
      };
      closeAgentMenu(false);
      var note = document.getElementById("ls-unlock-note");
      if (note) {
        note.textContent = "Unlock to " + label;
        note.hidden = false;
      }
      openPasscode();
    }

    function menuItem(label, opts) {
      opts = opts || {};
      var b = document.createElement("button");
      b.type = "button";
      b.className = "ls-menu-item";
      b.textContent = label;
      if (opts.danger) b.setAttribute("data-danger", "1");
      if (opts.disabled) b.disabled = true;
      else if (opts.onClick) b.addEventListener("click", opts.onClick);
      return b;
    }

    function openAgentMenu(island) {
      var agent = island.__agent || {};
      closeAgentMenu(false);
      menuReturn = island;

      var m = document.createElement("div");
      m.className = "ls-menu";
      m.setAttribute("role", "menu");
      m.setAttribute("aria-label", (agent.name || "Agent") + " actions");

      var head = document.createElement("div");
      head.className = "ls-menu-head";
      var nm = document.createElement("div");
      nm.className = "ls-menu-name";
      nm.textContent = agent.name || "agent";
      var sub = document.createElement("div");
      sub.className = "ls-menu-sub";
      var model = agent.model || "";
      // The model belongs here when we know it: "Change model" with no idea
      // what the current one is asks the user to choose blind.
      sub.textContent = model
        ? (agent.status || "idle") + " · " + model
        : (agent.status || "idle");
      head.appendChild(nm); head.appendChild(sub);
      m.appendChild(head);

      m.appendChild(menuItem("Open conversation", {
        onClick: function () { closeAgentMenu(false); openChat(agent); }
      }));

      var sep = document.createElement("div");
      sep.className = "ls-menu-sep";
      m.appendChild(sep);

      m.appendChild(menuItem("Change model", {
        onClick: function () {
          requireUnlock(agent, "change-model", "change " + (agent.name || "this agent") + "'s model");
        }
      }));

      // An agent that is already at rest cannot be stopped. Shown greyed rather
      // than removed so the menu does not change shape between two agents.
      var resting = RESTING.indexOf(String(agent.status || "").trim().toLowerCase()) !== -1;
      m.appendChild(menuItem("Stop agent", {
        danger: true,
        disabled: resting,
        onClick: function () {
          requireUnlock(agent, "stop", "stop " + (agent.name || "this agent"));
        }
      }));

      document.body.appendChild(m);
      menuEl = m;

      // Anchored to the island, then pulled back inside the viewport. Measured
      // after insertion: the menu's height depends on which items it got.
      var r = island.getBoundingClientRect();
      var mr = m.getBoundingClientRect();
      var gap = 8;
      var left = r.left + (r.width - mr.width) / 2;
      left = Math.max(12, Math.min(left, window.innerWidth - mr.width - 12));
      // Below the island, unless it would fall off the bottom -- then above it.
      var top = r.bottom + gap;
      if (top + mr.height > window.innerHeight - 12) {
        top = Math.max(12, r.top - mr.height - gap);
      }
      m.style.left = Math.round(left) + "px";
      m.style.top = Math.round(top) + "px";

      var firstItem = m.querySelector(".ls-menu-item:not([disabled])");
      if (firstItem) { try { firstItem.focus(); } catch (e) {} }

      // Capture phase: an outside press must close the menu BEFORE it reaches
      // whatever is underneath, or the tap that dismisses also opens something.
      document.addEventListener("pointerdown", onDocPointer, true);
      document.addEventListener("keydown", onMenuKey, true);
      window.addEventListener("resize", dismissAgentMenu);
      // The menu is positioned in viewport coordinates against an island that
      // scrolls, so a scrolling feed would leave it pointing at nothing.
      if (feedEl) feedEl.addEventListener("scroll", dismissAgentMenu);
    }

    // -----------------------------------------------------------------------
    // ISLAND PRESS. Press-and-hold opens the conversation, the way a long press
    // expands a notification on a phone. A short tap opens too -- the decision
    // when the island is asking for one, the conversation otherwise -- because
    // an island that looks pressable and does nothing on a tap reads as broken.
    // The sink-then-pop is what tells the finger the HOLD is being received.
    // -----------------------------------------------------------------------
    var HOLD_MS = 420;
    (function () {
      var timer = null, held = false, startY = 0, startX = 0, pressed = null;
      var onAvatar = false;

      function clear() {
        if (timer) { window.clearTimeout(timer); timer = null; }
        if (pressed) { pressed.removeAttribute("data-press"); }
        pressed = null;
        onAvatar = false;
      }

      function open(el) {
        var agent = el.__agent;
        if (!agent) return;
        if (agent.attention && agent.decision) openDecision(agent);
        else openChat(agent);
      }

      agentsEl.addEventListener("pointerdown", function (ev) {
        // The mic is its own control sitting inside the island; without this the
        // island's press handler would open the conversation underneath it.
        if (ev.target.closest(".ls-mic")) return;
        var el = ev.target.closest(".ls-island");
        if (!el) return;
        held = false;
        startY = ev.clientY; startX = ev.clientX;
        pressed = el;
        // WHERE the press began, latched HERE and never re-read. A long press
        // that starts on the avatar opens the agent's menu; anywhere else on
        // the island it opens the conversation, as it always has.
        //
        // Latched at pointerdown for the same reason the unlock-swipe veto is:
        // the finger moves during a 420ms hold, and deciding at the END would
        // make the gesture mean different things depending on where a fingertip
        // happened to settle. The user pressed the avatar or they did not.
        onAvatar = !!ev.target.closest(".ls-avatar");
        el.setAttribute("data-press", onAvatar ? "avatar" : "1");
        timer = window.setTimeout(function () {
          held = true;
          if (!onAvatar) el.setAttribute("data-press", "2");
          // Haptics where the platform offers them: a long press that only
          // changes pixels does not feel like a press.
          if (navigator.vibrate) { try { navigator.vibrate(12); } catch (e) {} }
          window.setTimeout(function () { el.removeAttribute("data-press"); }, 200);
          if (onAvatar) openAgentMenu(el);
          else openChat(el.__agent || {});
        }, HOLD_MS);
      });

      // A drag is a scroll of the agent list, not a press.
      agentsEl.addEventListener("pointermove", function (ev) {
        if (!pressed) return;
        if (Math.abs(ev.clientY - startY) > 10 || Math.abs(ev.clientX - startX) > 10) clear();
      });

      agentsEl.addEventListener("pointerup", function (ev) {
        if (ev.target.closest(".ls-mic")) return;
        var el = pressed;
        clear();
        if (held) { held = false; return; }   // the hold already opened it
        if (!el) return;
        var target = ev.target.closest(".ls-island");
        if (target === el) open(el);
      });

      agentsEl.addEventListener("pointercancel", clear);
      agentsEl.addEventListener("pointerleave", clear);

      // Keyboard parity: an island is a button, so Enter and Space must open it.
      agentsEl.addEventListener("keydown", function (ev) {
        if (ev.key !== "Enter" && ev.key !== " ") return;
        var el = ev.target.closest(".ls-island");
        if (!el) return;
        ev.preventDefault();
        open(el);
      });
    })();

    // -----------------------------------------------------------------------
    // DICTATION. The waveform is driven by the REAL microphone through an
    // AnalyserNode, not by a canned animation: a fake waveform that moves while
    // the mic is muted or denied is worse than no waveform, because it tells
    // the user they are being heard when they are not.
    //
    // Transcription is a separate capability from capture. Where the browser
    // has SpeechRecognition it is used; where it does not, the sheet says so
    // plainly instead of listening forever into nothing.
    // -----------------------------------------------------------------------
    var waveCanvas = document.getElementById("ls-wave");
    var voiceText  = document.getElementById("ls-voice-text");
    var voiceState = document.getElementById("ls-voice-state");
    var voiceTitle = document.getElementById("ls-voice-title");
    var voiceAv    = document.getElementById("ls-voice-avatar");
    var voiceSend  = document.getElementById("ls-voice-send");
    var voiceAgent = null;
    var mediaStream = null, audioCtx = null, analyser = null, waveRAF = null;
    var recog = null, finalText = "";

    function drawWave(level) {
      if (!waveCanvas) return;
      var ctx = waveCanvas.getContext("2d");
      var w = waveCanvas.width, h = waveCanvas.height, mid = h / 2;
      ctx.clearRect(0, 0, w, h);
      var bars = 48, gap = 3, bw = (w - gap * (bars - 1)) / bars;
      for (var i = 0; i < bars; i++) {
        // A travelling envelope so the bars read as a moving waveform rather
        // than a level meter; scaled by the ACTUAL measured level.
        var phase = (Date.now() / 260) + i * 0.38;
        var env = 0.32 + 0.68 * Math.abs(Math.sin(phase));
        var mag = Math.max(2, level * env * mid * 1.9);
        var x = i * (bw + gap);
        ctx.fillStyle = "rgba(10,132,255," + (0.45 + 0.55 * env) + ")";
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(x, mid - mag, bw, mag * 2, bw / 2);
        else ctx.rect(x, mid - mag, bw, mag * 2);
        ctx.fill();
      }
    }

    function pumpWave() {
      if (!analyser) return;
      var buf = new Uint8Array(analyser.frequencyBinCount);
      analyser.getByteTimeDomainData(buf);
      // RMS around the 128 midpoint: a peak reading spikes on a single click
      // and makes a quiet room look loud.
      var sum = 0;
      for (var i = 0; i < buf.length; i++) {
        var v = (buf[i] - 128) / 128;
        sum += v * v;
      }
      drawWave(Math.min(1, Math.sqrt(sum / buf.length) * 3.2));
      waveRAF = requestAnimationFrame(pumpWave);
    }

    function stopVoice() {
      if (waveRAF) { cancelAnimationFrame(waveRAF); waveRAF = null; }
      if (recog) { try { recog.onend = null; recog.abort(); } catch (e) {} recog = null; }
      if (mediaStream) {
        mediaStream.getTracks().forEach(function (t) { try { t.stop(); } catch (e) {} });
        mediaStream = null;
      }
      if (audioCtx) { try { audioCtx.close(); } catch (e) {} audioCtx = null; }
      analyser = null;
    }

    function voiceFail(msg) {
      stopVoice();
      if (voiceState) voiceState.textContent = "Not available";
      if (voiceText) { voiceText.setAttribute("data-error", "1"); voiceText.textContent = msg; }
      drawWave(0);
    }

    function openVoice(agent) {
      voiceAgent = agent;
      finalText = "";
      if (voiceTitle) voiceTitle.textContent = agent.name || "agent";
      if (voiceAv) fillAvatar(voiceAv, agent);
      if (voiceText) { voiceText.removeAttribute("data-error"); voiceText.textContent = ""; }
      if (voiceState) voiceState.textContent = "Listening\u2026";
      if (voiceSend) voiceSend.disabled = true;
      openSheet("voice");

      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        voiceFail("This device has no microphone available to the browser.");
        return;
      }
      navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
        if (!screenEl || screenEl.getAttribute("data-sheet") !== "voice") {
          stream.getTracks().forEach(function (t) { t.stop(); });
          return;
        }
        mediaStream = stream;
        var AC = window.AudioContext || window.webkitAudioContext;
        audioCtx = new AC();
        analyser = audioCtx.createAnalyser();
        analyser.fftSize = 1024;
        audioCtx.createMediaStreamSource(stream).connect(analyser);
        pumpWave();

        var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SR) {
          // Capture works, transcription does not. Say exactly that rather than
          // leaving a waveform moving under a caption that never appears.
          if (voiceState) voiceState.textContent = "Dictation unavailable";
          if (voiceText) {
            voiceText.setAttribute("data-error", "1");
            voiceText.textContent = "This build has no speech recognition, so it cannot turn speech into text. Type instead.";
          }
          return;
        }
        recog = new SR();
        recog.continuous = true;
        recog.interimResults = true;
        recog.lang = navigator.language || "en-GB";
        recog.onresult = function (ev) {
          var interim = "";
          for (var i = ev.resultIndex; i < ev.results.length; i++) {
            var chunk = ev.results[i][0].transcript;
            if (ev.results[i].isFinal) finalText += chunk;
            else interim += chunk;
          }
          if (voiceText) voiceText.textContent = (finalText + interim).trim();
          if (voiceSend) voiceSend.disabled = !(finalText + interim).trim();
        };
        recog.onerror = function (ev) {
          voiceFail(ev && ev.error === "not-allowed"
            ? "Microphone access was refused."
            : "Dictation stopped. Type instead.");
        };
        try { recog.start(); } catch (e) { /* already running */ }
      }).catch(function () {
        voiceFail("Microphone access was refused, so nothing is being recorded.");
      });
    }

    if (agentsEl) {
      agentsEl.addEventListener("click", function (ev) {
        var mic = ev.target.closest(".ls-mic");
        if (!mic) return;
        ev.preventDefault();
        ev.stopPropagation();
        var el = mic.closest(".ls-island");
        if (el && el.__agent) openVoice(el.__agent);
      });
    }

    var voiceClose = document.getElementById("ls-voice-close");
    if (voiceClose) voiceClose.addEventListener("click", closeSheet);
    var voiceCancel = document.getElementById("ls-voice-cancel");
    if (voiceCancel) voiceCancel.addEventListener("click", closeSheet);
    if (voiceSend) {
      voiceSend.addEventListener("click", function () {
        var text = (voiceText ? voiceText.textContent : "").trim();
        var agent = voiceAgent;
        stopVoice();
        if (!text || !agent) { closeSheet(); return; }
        // Hand the dictation to the conversation rather than sending it from
        // here: the thread is where a message belongs, and it is then visible
        // as having been said.
        openChat(agent);
        window.setTimeout(function () {
          if (composer) {
            composer.value = text;
            composer.dispatchEvent(new Event("input", { bubbles: true }));
          }
        }, 60);
      });
    }

    // Keypad -> the existing PIN input.
    var pad = document.getElementById("ls-pad");
    var input = document.getElementById("pin-input");
    if (!pad || !input) return;

    function emit() {
      // The pin-panel script paints the dots from an "input" event, so the
      // keypad must raise one -- assigning .value alone fires nothing.
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }

    pad.addEventListener("click", function (ev) {
      var key = ev.target.closest(".ls-key");
      if (!key) return;
      var digit = key.getAttribute("data-digit");
      if (digit !== null) {
        var max = parseInt(input.getAttribute("maxlength") || "12", 10);
        if (input.value.length < max) {
          input.value += digit;
          emit();
        }
        return;
      }
      if (key.getAttribute("data-action") === "back") {
        input.value = input.value.slice(0, -1);
        emit();
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
"""


def _login_page(
    error: str = "",
    multi_user: bool = False,
    next_url: str = "",
    pin_available: bool = False,
) -> str:
    err = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
    pwd_placeholder = "Password or invite code" if multi_user else "Password"
    autologin_default = "" if multi_user else "checked"
    username_field = '''
        <label class="field">
          <span>Username or email</span>
          <input type="text" name="username" autocomplete="username" autofocus required>
        </label>
        ''' if multi_user else ""
    next_field = f'<input type="hidden" name="next" value="{html.escape(next_url)}">' if next_url else ""
    # The PIN panel and the "use a PIN instead" switch exist only when this
    # request is console-local AND a PIN is set. Off-console the page is exactly
    # what it has always been, so a LAN browser is never shown a method it would
    # be refused (and never learns that a PIN exists on this box).
    # pin_available is already console-only, so the lock screen never reaches a
    # LAN browser: off-console this page stays exactly the card it has always been.
    lock_screen = pin_available
    pin_panel = _pin_panel_html(next_url, keypad=lock_screen) if pin_available else ""
    pin_switch = (
        '<button type="button" class="method-switch" id="use-pin">Use my PIN instead</button>'
        if pin_available else ""
    )
    pin_script = '<script src="/auth/pin-panel.js" defer></script>' if pin_available else ""
    # The lock screen replaces the card entirely: a passcode screen that still
    # draws a bordered panel in the middle of a 2400px phone reads as a web page.
    # The device's own name is the only server-supplied value on it -- everything
    # else (clock, battery) is read client-side, so nothing account-derived is
    # rendered before the user has authenticated.
    body_class = "lockscreen-on" if lock_screen else ""
    shell_class = "ls-foot" if lock_screen else "card"
    shell_id = ' id="ls-foot"' if lock_screen else ""
    brand = "" if lock_screen else (
        '<div class="brand">\n'
        '      <h1 class="wordmark">taOS</h1>\n'
        '      <p>Sign in to continue</p>\n'
        '    </div>'
    )
    lock_head = _lock_head_html() if lock_screen else ""
    lock_foot = _lock_tail_html() if lock_screen else ""
    lock_script = '<script src="/auth/lock-screen.js" defer></script>' if lock_screen else ""
    lock_style = f"<style>{_LOCK_SCREEN_STYLE}</style>" if lock_screen else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>Sign in — taOS</title>
<style>{_AUTH_BASE_STYLE}</style>
<style>{_PIN_PANEL_STYLE}</style>
{lock_style}
</head>
<body class="{body_class}">
  {lock_head}
  <div class="{shell_class}"{shell_id}>
    {brand}
    {err}
    {pin_panel}
    <form class="pw-panel" id="pw-panel" method="POST" action="/auth/login">
      {username_field}
      {next_field}
      <label class="field">
        <span>Password</span>
        <input type="password" name="password" autocomplete="current-password" placeholder="{pwd_placeholder}" {'' if multi_user else 'autofocus'} required>
      </label>
      <label class="checkbox">
        <input type="checkbox" name="auto_login" value="1" {autologin_default}>
        Stay signed in on this device
      </label>
      <button type="submit">Sign in</button>
      {pin_switch}
    </form>
  </div>
  {lock_foot}
{osk_assets()}
{pin_script}
{lock_script}
</body>
</html>
"""


def _setup_page(error: str = "", pin_offered: bool = False) -> str:
    err = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
    # Offered only when the installer is sitting at the machine's own screen —
    # a PIN is refused anywhere else (see is_console_origin), so offering it to
    # a LAN browser would hand the user a sign-in method that cannot work and
    # would tell that browser a PIN exists on this box. It stays optional: the
    # password is always set, so nobody can lock themselves out by skipping it,
    # and it can be added later from Settings.
    pin_field = f"""
    <label class="field">
      <span>PIN for this screen (optional)</span>
      <input type="password" name="pin" id="setup-pin" inputmode="numeric"
             autocomplete="off" maxlength="{PIN_MAX_LEN}" pattern="[0-9]*">
      <span class="hint">{PIN_MIN_LEN}-{PIN_MAX_LEN} digits, for signing in on
        this device's own screen — handy on a touchscreen with no keyboard.
        Your password still works everywhere.</span>
    </label>
    """ if pin_offered else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>Welcome — taOS</title>
<style>{_AUTH_BASE_STYLE}</style>
</head>
<body>
  <form class="card" method="POST" action="/auth/setup">
    <div class="brand">
      <h1 class="wordmark">taOS</h1>
      <p>Welcome — set up your account to get started.</p>
    </div>
    {err}
    <label class="field">
      <span>Username</span>
      <input type="text" name="username" autocomplete="username" autofocus required>
    </label>
    <label class="field">
      <span>Full name</span>
      <input type="text" name="full_name" autocomplete="name" required>
    </label>
    <label class="field">
      <span>Email</span>
      <input type="email" name="email" autocomplete="email">
      <span class="hint">Optional today, used for cloud services later.</span>
    </label>
    <label class="field">
      <span>Password</span>
      <input type="password" name="password" autocomplete="new-password" minlength="8" required>
      <span class="hint">At least 8 characters.</span>
    </label>
    {pin_field}
    <label class="checkbox">
      <input type="checkbox" name="auto_login" value="1" checked>
      Stay signed in on this device
    </label>
    <button type="submit">Get started</button>
  </form>
{osk_assets()}
</body>
</html>
"""


def _require_admin(request: Request) -> tuple[bool, JSONResponse | None]:
    """Check that the session belongs to an admin. Returns (ok, error_response)."""
    auth_mgr = request.app.state.auth
    token = request.cookies.get("taos_session", "")
    if not token:
        return False, JSONResponse({"error": "forbidden"}, status_code=403)
    user = auth_mgr.session_user(token)
    if not user or not user.get("is_admin"):
        return False, JSONResponse({"error": "forbidden"}, status_code=403)
    return True, None


def _require_self(request: Request, username: str) -> tuple[bool, JSONResponse | None]:
    """Check that the session belongs to *username*. Returns (ok, error_response)."""
    auth_mgr = request.app.state.auth
    token = request.cookies.get("taos_session", "")
    if not token:
        return False, JSONResponse({"error": "forbidden"}, status_code=403)
    user = auth_mgr.session_user(token)
    if not user or user.get("username") != username:
        return False, JSONResponse({"error": "forbidden"}, status_code=403)
    return True, None


async def _json_object(request: Request) -> tuple[dict | None, JSONResponse | None]:
    """Read a JSON request body that must be an object. Returns (body, error_response).

    Parsing alone is not enough: request.json() happily returns null, [], 1 or
    "x" -- all valid JSON, none of them a mapping. A bare body.get() on any of
    those raises AttributeError, so the caller gets a 500 for what is plainly a
    malformed request. /auth/login, /auth/setup and /auth/complete are all
    session-exempt, so that 500 is reachable by anyone who can reach the port.
    """
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        # Deliberately narrow. request.json() READS the body before it parses
        # it, so a blanket `except Exception` also swallows body-read failures
        # (a client disconnecting mid-upload raises ClientDisconnect here) and
        # reports them to the caller as "your JSON was malformed". That is a
        # false accusation, and it hides a transport fault behind a 400 that
        # nobody investigates. A read failure is not the client's syntax error,
        # so let it propagate.
        return None, JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return None, JSONResponse({"error": "invalid JSON body"}, status_code=400)
    return body, None


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = "", next: str = ""):
    """Server-rendered login page. Works without JavaScript — the SPA
    takes over once the user is signed in and lands on /desktop."""
    auth_mgr = request.app.state.auth
    # If the install isn't configured yet, send them to setup instead of
    # showing a useless login form.
    if not auth_mgr.is_configured():
        return RedirectResponse("/auth/setup", status_code=303)
    if error == "rate_limit":
        err_text = _LOCKOUT_MSG
    elif error:
        err_text = "Incorrect username or password."
    else:
        err_text = ""
    # Only allow relative paths starting with / to prevent open redirect
    safe_next = next if (next.startswith("/") and not next.startswith("//")) else ""
    # Same rule as /auth/status and /auth/pin-login: offer the keypad only where
    # it would actually be accepted.
    try:
        pin_available = _request_is_console(request) and auth_mgr.has_pin()
    except AuthStoreCorruptError:
        pin_available = False
    return HTMLResponse(_login_page(
        err_text,
        multi_user=auth_mgr.is_multi_user(),
        next_url=safe_next,
        pin_available=pin_available,
    ))


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request, error: str = ""):
    """Server-rendered first-run setup page. Same robustness rationale as
    /auth/login. Once a user exists this page redirects to login."""
    auth_mgr = request.app.state.auth
    if auth_mgr.is_configured():
        return RedirectResponse("/auth/login", status_code=303)
    err_text = ""
    if error:
        err_text = {
            "username": "Username is required.",
            "password": "Password must be at least 8 characters.",
            "pin": f"A PIN must be {PIN_MIN_LEN}-{PIN_MAX_LEN} digits, or left blank.",
        }.get(error, "Setup failed. Please try again.")
    return HTMLResponse(_setup_page(err_text, pin_offered=_request_is_console(request)))


@router.post("/login")
async def login(request: Request):
    """Sign in. Accepts JSON or form-encoded.

    JSON body: ``{username?, password, auto_login?}``. Returns the user
    profile and sets a session cookie.

    For pending users (invite code supplied), returns
    ``needs_onboarding: true`` and creates a session so the
    OnboardingScreen can complete the profile.

    Form body: legacy password-only login (kept for backward compat).
    """
    auth_mgr = request.app.state.auth
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "")

    content_type = request.headers.get("content-type", "")

    # Only the HARD ceiling rejects before we verify the password. Below it we
    # always check, so a correct password succeeds even after earlier typos --
    # the soft lockout (applied on failure below) gates further WRONG attempts,
    # not the legitimate user. This is the footgun that funneled a locked-out
    # user into creating a duplicate account.
    if _login_limiter.count(client_ip) >= _LOGIN_HARD_MAX:
        if "application/json" in content_type:
            return JSONResponse({"error": _LOCKOUT_MSG}, status_code=429)
        return RedirectResponse("/auth/login?error=rate_limit", status_code=303)
    if "application/json" in content_type:
        body, body_err = await _json_object(request)
        if body_err:
            return body_err
        username = (body.get("username") or "").strip() or None
        password = body.get("password") or ""

        ok, user_record = auth_mgr.check_password(password, username=username)
        if not ok:
            _login_limiter.record_failure(client_ip)
            if _login_limiter.is_limited(client_ip):
                return JSONResponse({"error": _LOCKOUT_MSG}, status_code=429)
            return JSONResponse({"error": "invalid credentials"}, status_code=401)

        _login_limiter.reset(client_ip)

        # Determine long_lived. In multi-user mode default to False when
        # auto_login is not explicitly set.
        if "auto_login" in body:
            long_lived = bool(body["auto_login"])
        else:
            long_lived = not auth_mgr.is_multi_user()

        # Pending user: invite code accepted as password
        if user_record and user_record.get("pending_invite"):
            token = auth_mgr.create_session(user_id=user_record["id"], long_lived=long_lived, user_agent=user_agent)
            resp = JSONResponse({
                "ok": True,
                "needs_onboarding": True,
                "user": auth_mgr._public_user(user_record),
            })
            if long_lived:
                resp.set_cookie(
                    "taos_session", token, httponly=True, samesite="strict",
                    max_age=auth_mgr.session_ttl_for(True),
                )
            else:
                resp.set_cookie("taos_session", token, httponly=True, samesite="strict")
            return resp

        user_id = user_record["id"] if user_record else ""
        if user_record:
            auth_mgr.update_last_login(user_id)
        token = auth_mgr.create_session(user_id=user_id, long_lived=long_lived, user_agent=user_agent)
        pub = auth_mgr._public_user(user_record) if user_record else auth_mgr.get_user()
        resp = JSONResponse({"ok": True, "user": pub})
        if long_lived:
            resp.set_cookie(
                "taos_session", token, httponly=True, samesite="strict",
                max_age=auth_mgr.session_ttl_for(True),
            )
        else:
            resp.set_cookie("taos_session", token, httponly=True, samesite="strict")
        return resp

    # Form-encoded path — used by the no-JS HTML login page.
    form = await request.form()
    username = (form.get("username") or "").strip() or None
    password = form.get("password", "")
    long_lived = bool(form.get("auto_login"))
    next_url = str(form.get("next", "") or "")
    # Validate next_url to prevent open redirect
    if not (next_url.startswith("/") and not next_url.startswith("//")):
        next_url = ""

    ok, user_record = auth_mgr.check_password(password, username=username)
    if not ok:
        _login_limiter.record_failure(client_ip)
        next_qs = f"&next={next_url}" if next_url else ""
        err = "rate_limit" if _login_limiter.is_limited(client_ip) else "1"
        return RedirectResponse(f"/auth/login?error={err}{next_qs}", status_code=303)

    _login_limiter.reset(client_ip)

    if user_record and user_record.get("pending_invite"):
        # Pending user — create their session, then send to /desktop. The
        # SPA's LoginGate will see needs_onboarding via /auth/status and
        # render the invite-completion screen.
        token = auth_mgr.create_session(user_id=user_record["id"], long_lived=long_lived, user_agent=user_agent)
    else:
        user_id = user_record["id"] if user_record else ""
        if user_record:
            auth_mgr.update_last_login(user_id)
        token = auth_mgr.create_session(user_id=user_id, long_lived=long_lived, user_agent=user_agent)

    destination = next_url or "/desktop"
    response = RedirectResponse(destination, status_code=303)
    if long_lived:
        response.set_cookie(
            "taos_session", token, httponly=True, samesite="strict",
            max_age=auth_mgr.session_ttl_for(True),
        )
    else:
        response.set_cookie("taos_session", token, httponly=True, samesite="strict")
    return response


def _request_is_console(request: Request) -> bool:
    """Whether this request may use PIN sign-in at all.

    Thin wrapper so every PIN route asks the question exactly one way. The rule
    itself lives in ``auth.is_console_origin`` and is unit-tested there without
    needing a request object.
    """
    return is_console_origin(
        request.client.host if request.client else None, request.headers
    )


#: PIN attempts are throttled separately from passwords, keyed by user id.
#: Sharing ``_login_limiter`` would let PIN failures lock a user out of the
#: password path -- and since every console request arrives from the same
#: loopback address, an IP-keyed counter would be one shared bucket for all.
_pin_limiter = _PinAttemptLimiter()


@router.get("/osk.js")
async def osk_script(request: Request):
    """Serve the on-screen keyboard as a same-origin script.

    taOS sends `script-src 'self'`, which refuses inline <script> blocks. The
    keyboard therefore CANNOT be inlined into the auth pages -- doing so renders
    correct-looking HTML whose script the browser silently drops, so the page
    looks right and the keyboard simply never appears.
    """
    return Response(
        content=OSK_SCRIPT,
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/pin-panel.js")
async def pin_panel_script(request: Request):
    """Serve the PIN panel behaviour. Same CSP reasoning as /auth/osk.js."""
    return Response(
        content=_PIN_PANEL_SCRIPT,
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=300"},
    )



_LOCK_VIEWS_TOKEN = "/*__LOCK_VIEWS__*/"


def _lock_screen_js() -> str:
    """The lock-screen script with its view list generated in.

    The seven views are declared once, in _LOCK_VIEWS, and reach the script
    here instead of being re-typed in JavaScript. A view present in one list and
    not the other is the failure this avoids: a tab with no panel shows a blank
    feed, and a panel with no tab cannot be reached at all.

    The token MUST be present. Without this check a rename upstream would
    silently serve a script whose VIEWS is undefined -- the switcher would throw
    on the first tap and the lock screen would look merely unresponsive, which
    is a much harder thing to trace back to here than an error at startup.
    """
    if _LOCK_VIEWS_TOKEN not in _LOCK_SCREEN_SCRIPT:
        raise RuntimeError(
            "lock-screen.js is missing the %s marker: the view list has "
            "nowhere to go and the switcher would fail at runtime."
            % _LOCK_VIEWS_TOKEN
        )
    keys = ", ".join('"%s": 1' % key for key, _label, _icon, _panel in _LOCK_VIEWS)
    preamble = '  var VIEWS = {%s};\n  var VIEW_DEFAULT = "%s";' % (
        keys,
        _LOCK_DEFAULT_VIEW,
    )
    return _LOCK_SCREEN_SCRIPT.replace(_LOCK_VIEWS_TOKEN, preamble)


@router.get("/lock-screen.js")
async def lock_screen_script(request: Request):
    """Serve the lock-screen chrome. Same CSP reasoning as /auth/osk.js."""
    return Response(
        content=_lock_screen_js(),
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=300"},
    )





#: Where the App Store keeps framework/brand artwork. The lock screen reuses
#: those files rather than carrying a second copy, so a logo updated for the
#: store is updated here too.
_STORE_ICON_DIRS = ("static/store-icons", "static/store-icons/brands")


def _framework_icon(framework: str) -> str:
    """URL of the App Store icon for a framework, or "" when none is shipped.

    Resolved server-side so the page only ever points at a file that exists: the
    client falls back to its drawn mark immediately instead of after a 404.
    /static/ is already served and already exempt from auth, so this adds no new
    pre-auth surface.
    """
    fw = "".join(c for c in framework.lower() if c.isalnum())
    if not fw:
        return ""
    root = Path(__file__).resolve().parent.parent.parent
    for folder in _STORE_ICON_DIRS:
        for ext in ("svg", "png", "jpg", "webp"):
            rel = f"{folder}/{fw}.{ext}"
            if (root / rel).is_file():
                return "/" + rel
    return ""


def _avatar_url(name: str) -> str:
    """URL for this agent's avatar, or "" when no image is installed.

    Checked server-side so the page never points an <img> at a 404 -- the client
    falls back to a monogram, and it should do that from the start rather than
    after a failed request paints a broken frame.
    """
    slug = _avatar_slug(name)
    if not slug:
        return ""
    if not (Path(LOCK_AVATAR_DIR) / f"{slug}.jpg").is_file():
        return ""
    return f"/auth/lock-avatar/{slug}"


@router.get("/lock-widgets")
async def lock_widgets(request: Request):
    """Agent activity + scheduled tasks for the lock screen. Console-only.

    This is rendered BEFORE sign-in, which is exactly why it is narrow: it
    returns NAMES, STATUSES AND COUNTS and nothing else. The agent config is
    never serialised here -- it carries per-agent LLM keys, and this endpoint is
    reachable without a session. The console gate is the second half of that
    containment: a LAN browser gets 403 and learns nothing, so the exposure is
    the same one a phone lock screen already makes to whoever is holding it.
    """
    if not _request_is_console(request):
        return JSONResponse({"error": "console only"}, status_code=403)

    agents: list[dict] = []
    try:
        configured = request.app.state.config.agents or []
    except AttributeError:
        configured = []
    # Container status is best-effort: on a host with no container runtime the
    # import or the call raises, and a lock screen that 500s because the phone
    # has no LXC is worse than one that simply shows no status.
    status_by_name: dict[str, str] = {}
    try:
        from tinyagentos.containers import list_containers

        for c in await list_containers(prefix="taos-agent-"):
            status_by_name[c.name.removeprefix("taos-agent-")] = c.status
    except Exception:  # noqa: BLE001 - any runtime absence degrades to "no status"
        status_by_name = {}

    for entry in configured:
        name = entry.get("name") if isinstance(entry, dict) else str(entry)
        if not name:
            continue
        framework = ""
        if isinstance(entry, dict):
            framework = str(entry.get("framework") or entry.get("harness") or "")
        agents.append({
            "name": str(name),
            "framework": framework.lower(),
            "framework_icon": _framework_icon(framework),
            "status": status_by_name.get(str(name), ""),
            "avatar": _avatar_url(str(name)),
        })

    # The OS's own agent is pinned to the top and is not one of the configured
    # ones: it is part of the device rather than something the user added. It
    # carries the product mark rather than a monogram, and the OMP harness badge
    # like any other agent -- it runs on OMP (oh-my-pi) over ACP, see
    # tinyagentos/adapters/omp_adapter.py.
    # Its "status" says WHERE it is, not that it is busy -- this endpoint runs
    # pre-auth and has no cheap, truthful way to read the agent's activity.
    agents.insert(0, {
        "name": "taOS Agent",
        "framework": "omp",
        "framework_icon": _framework_icon("omp"),
        "status": "On device",
        "avatar": "/static/taos-logo.png",
        "system": True,
    })

    tasks: list[dict] = []
    try:
        scheduler = request.app.state.scheduler
        for task in await scheduler.list_tasks():
            item = task if isinstance(task, dict) else {}
            tasks.append({
                "name": str(item.get("name", "") or ""),
                "schedule": str(item.get("schedule", "") or ""),
                "agent": str(item.get("agent_name", "") or ""),
            })
    except Exception:  # noqa: BLE001 - no scheduler on this host: show no tasks
        tasks = []

    # Demo override. OFF unless TAOS_LOCK_DEMO_AGENTS is set, and it only ever
    # ADDS named placeholders to this one read-only lock-screen endpoint -- it
    # writes nothing, creates no agents and changes no other surface. It exists
    # so a demo machine can show a populated lock screen without standing up
    # three real container-backed agents first; anything it lists is a
    # placeholder, not a running process.
    demo = os.environ.get("TAOS_LOCK_DEMO_AGENTS", "").strip()
    if demo:
        existing = {a["name"] for a in agents}
        for raw in demo.split(","):
            # "Name", "Name:framework" or "Name:framework:status text"
            parts = [seg.strip() for seg in raw.split(":")]
            label = parts[0] if parts else ""
            if label and label not in existing:
                agents.append({
                    "name": label,
                    "framework": parts[1].lower() if len(parts) > 1 and parts[1] else "",
                    "framework_icon": _framework_icon(parts[1] if len(parts) > 1 else ""),
                    "status": parts[2] if len(parts) > 2 and parts[2] else "running",
                    "avatar": _avatar_url(label),
                    # Marked at creation so nothing downstream has to work out
                    # which of these entries is a placeholder by elimination.
                    "demo": True,
                })

    # Pending decisions. An agent that is blocked waiting on a human is the one
    # thing on this screen that is actually ASKING for something, so it gets the
    # attention ring -- everything else here is status. Best-effort for the same
    # reason as the container statuses: a host with no decision store should
    # show a lock screen, not a 500.
    #
    # Only the question and its options cross the pre-auth boundary, never the
    # decision's context or notes: the question is a one-line prompt the holder
    # of the phone needs in order to know the phone wants them, while the
    # context is free text an agent may have filled with anything.
    pending: list[dict] = []
    try:
        store = request.app.state.decision_store
        pending = await store.list(status="pending", limit=20)
    except Exception:  # noqa: BLE001 - no decision store on this host: no ring
        pending = []

    # The store returns newest-first. If an agent has asked twice, the question
    # to surface is the one that has been WAITING longest, so walk oldest-first
    # and keep the first hit per agent.
    by_agent: dict[str, dict] = {}
    for d in reversed(pending):
        agent_key = str(d.get("from_agent") or "").strip().lower()
        if agent_key and agent_key not in by_agent:
            by_agent[agent_key] = d

    def _attach(agent: dict) -> None:
        d = by_agent.get(agent["name"].strip().lower())
        if not d:
            return
        options = d.get("options") or []
        labels = [
            str(o.get("label") or o.get("value") or "")
            for o in options
            if isinstance(o, dict)
        ]
        agent["attention"] = True
        agent["decision"] = {
            "id": str(d.get("id") or ""),
            "question": str(d.get("question") or ""),
            "priority": str(d.get("priority") or "normal"),
            "options": [lbl for lbl in labels if lbl][:4],
        }

    for agent in agents:
        _attach(agent)

    # Demo decision. Same single flag as the demo agents, and it is attached to
    # an agent that is already a placeholder -- it never marks a REAL agent as
    # waiting on a human, because a fabricated ring on a real agent would be a
    # lie about the state of the machine. It carries no decision id, which is
    # what the client uses to tell a demo prompt from an answerable one.
    if demo and not any(a.get("attention") for a in agents):
        want = os.environ.get("TAOS_LOCK_DEMO_DECISION_AGENT", "").strip().lower()
        target = None
        for a in agents:
            if not a.get("demo") or a.get("system"):
                continue
            if want and a["name"].strip().lower() != want:
                continue
            target = a
            break
        if target is not None:
            target["attention"] = True
            target["decision"] = {
                "id": "",
                "question": os.environ.get(
                    "TAOS_LOCK_DEMO_DECISION",
                    "Approve \u00a31,340 for the second Raspberry Pi order?",
                ),
                "priority": "normal",
                "options": ["Approve", "Deny"],
                "demo": True,
            }

    # Anything with a status that is not an explicit resting word is doing
    # something -- the demo statuses are free text ("Drafting replies"), so an
    # equality test against "running" would report every busy agent as idle.
    resting = {"", "stopped", "idle", "exited", "error"}
    running = sum(
        1 for a in agents
        if not a.get("system") and a["status"].strip().lower() not in resting
    )
    return JSONResponse({
        "agents": agents[:6],
        "agent_total": len(agents),
        "agent_running": running,
        "tasks": tasks[:4],
        "task_total": len(tasks),
        "threads": bool(demo),
    })



#: Where lock-screen agent avatars are read from. One flat directory of
#: "<slug>.jpg" files, slug being the agent name lowercased with non-alphanumerics
#: collapsed to "-". Overridable so a packaged install can point it at its own
#: data dir rather than this default.
LOCK_AVATAR_DIR = os.environ.get("TAOS_LOCK_AVATAR_DIR", "/var/lib/taos/lock-avatars")


def _avatar_slug(name: str) -> str:
    """Slug for an agent name, restricted to characters that cannot traverse.

    Anything outside [a-z0-9-] is dropped rather than escaped: this value is
    used to build a filesystem path, so a conservative whitelist is the control
    that keeps "../" and absolute paths out, not a sanitiser that tries to spot
    bad input.
    """
    out = []
    for ch in name.strip().lower():
        if ch.isalnum() and ch.isascii():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")


@router.get("/lock-avatar/{slug}")
async def lock_avatar(slug: str, request: Request):
    """Serve one lock-screen avatar. Console-only, same reasoning as the widgets.

    The slug is re-derived through the same whitelist before it touches the
    filesystem, so a crafted request cannot address a file outside the avatar
    directory even if the router hands us a path-shaped segment.
    """
    if not _request_is_console(request):
        return JSONResponse({"error": "console only"}, status_code=403)

    safe = _avatar_slug(slug)
    if not safe:
        return JSONResponse({"error": "not found"}, status_code=404)

    path = Path(LOCK_AVATAR_DIR) / f"{safe}.jpg"
    try:
        data = path.read_bytes()
    except OSError:
        return JSONResponse({"error": "not found"}, status_code=404)
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


#: Demo conversation scripts for the lock-screen chat sheet, keyed by avatar
#: slug. These exist so a DEMO device can show a populated thread; they are not
#: an agent's real conversation and never touch the chat store.
#:
#: The lock screen renders BEFORE sign-in, so serving an agent's real messages
#: here would hand whoever is holding the phone the contents of every
#: conversation on it. That is why this endpoint serves SCRIPTED TEXT ONLY and
#: 404s when demo mode is off: there is no code path from the lock screen to a
#: real transcript, so there is nothing to get the gate wrong about.
#:
#: Each entry is (days_ago, hh, mm, role, text). Fixed offsets rather than
#: stored timestamps so the thread always reads as "the last few weeks",
#: whenever the demo happens to be run.
_DEMO_THREADS: dict[str, tuple[tuple[int, int, int, str, str], ...]] = {
    "taos-agent": (
        (18, 9, 15, "user", "What can you actually do?"),
        (18, 9, 15, "agent", "I run this device. Open and arrange apps, manage your projects and files, remember things for you, and talk to the other agents on here. If you ask me for something one of them is better at, I hand it over."),
        (16, 21, 30, "user", "the phone felt warm earlier"),
        (16, 21, 31, "agent", "CPU sat at 78\u00b0C for about 20 minutes during the model download. It is back to 41\u00b0C now. Nothing was throttled."),
        (13, 8, 45, "agent", "Storage is at 71%. The biggest single item is the model cache at 9.2 GB. I can clear the unused ones whenever you want."),
        (13, 8, 50, "user", "not yet"),
        (10, 19, 5, "user", "can you put the calendar next to the chat"),
        (10, 19, 5, "agent", "Done \u2014 calendar on the left, chat on the right, split even."),
        (7, 11, 20, "agent", "An update is available: beta.52. It is a packaging fix plus the install reliability work. Nothing on this device depends on it, so it can wait for you."),
        (4, 15, 40, "user", "remind me what we changed on the kiosk"),
        (4, 15, 41, "agent", "The compositor moved from cage to sway so the screen can genuinely power off after 30 seconds. Before that it could only dim, which left the panel lit and touch live."),
        (1, 8, 0, "agent", "Backup completed overnight. 4 of 4 sets, nothing skipped."),
        (0, 7, 35, "agent", "Everything is healthy this morning. Battery 82%, no pending updates, five agents running."),
    ),
    "personal-assistant": (
        (19, 8, 12, "user", "Morning. What does today look like?"),
        (19, 8, 12, "agent", "Three things. Dentist at 11:40, the Hargreaves call moved to 15:00, and your sister's flight lands 19:25. I left a gap either side of the call."),
        (19, 8, 13, "user", "Can you push the dentist?"),
        (19, 8, 15, "agent", "Moved to Thursday 09:15 \u2014 they had a cancellation. Confirmed by text."),
        (17, 20, 41, "agent", "Heads up: the car insurance renews on the 3rd at \u00a3612, up from \u00a3489. I pulled four quotes, cheapest comparable is \u00a3501. Want me to lay them out?"),
        (17, 20, 44, "user", "Yes but not tonight"),
        (17, 20, 44, "agent", "Filed it for Saturday morning."),
        (14, 9, 2, "user", "Did I ever reply to Marcus?"),
        (14, 9, 3, "agent", "No. His last message was the 28th asking about the workshop dates. Draft ready if you want it."),
        (14, 9, 5, "user", "Send it"),
        (14, 9, 5, "agent", "Sent."),
        (11, 13, 30, "agent", "Your passport expires in 5 months. Several countries want 6, so if anything is booked after March it needs renewing now. The form takes about 20 minutes."),
        (8, 7, 55, "user", "Remind me to call mum"),
        (8, 7, 55, "agent", "Every Sunday at 18:00, or just this week?"),
        (8, 7, 56, "user", "every sunday"),
        (8, 7, 56, "agent", "Done. First one this Sunday."),
        (5, 16, 20, "agent", "The Hargreaves invoice is 21 days overdue. I have a polite chase drafted and a firmer one. Neither is sent."),
        (2, 10, 11, "user", "How much did we spend on groceries last month?"),
        (2, 10, 11, "agent", "\u00a3418, across 14 shops. That is \u00a367 up on the month before, almost all of it two big Ocado orders on the 8th and the 22nd."),
        (0, 7, 40, "agent", "Good morning. Quiet day \u2014 one call at 14:00 and the parcel is out for delivery."),
    ),
    "social-media-manager": (
        (20, 11, 5, "agent", "The Tuesday build-in-public post did well: 2.4k views, 41 replies. The thread format is clearly beating the single post."),
        (20, 11, 9, "user", "do more of those"),
        (20, 11, 9, "agent", "Switching the Tuesday and Friday slots to threads. I will keep the Monday one short."),
        (18, 15, 44, "agent", "Someone with 80k followers quoted the roadmap post and called it \u201cthe first self-hosted thing I would actually give my parents\u201d. Worth a reply?"),
        (18, 15, 50, "user", "reply but dont be weird about it"),
        (18, 15, 52, "agent", "Replied: thanked them, no pitch, no link."),
        (15, 9, 30, "agent", "Drafted 6 posts for next week. Three are screenshots, two are short clips, one is the release note. All queued, none published."),
        (12, 18, 2, "user", "why did engagement drop friday"),
        (12, 18, 3, "agent", "Posted at 16:40 local, which is 11:40 for most of the audience \u2014 the worst slot in the data. The 09:00 slot averages 3.1x that."),
        (12, 18, 4, "user", "move everything to 9"),
        (12, 18, 4, "agent", "Moved."),
        (9, 12, 15, "agent", "A reply is getting heated on the pricing post. Two people arguing, neither with us. I have not engaged."),
        (9, 12, 20, "user", "leave it"),
        (6, 8, 48, "agent", "The clip of the phone booting into taOS is the best-performing asset this month by a distance. I can cut three more from the same recording."),
        (6, 8, 49, "user", "yes"),
        (3, 14, 0, "agent", "Follower count crossed 5,000 this morning. Up 640 in three weeks, mostly from the two thread posts."),
        (1, 10, 30, "agent", "Next week's queue is full and reviewed. Nothing goes out without your yes on the release post."),
        (0, 9, 10, "agent", "Morning. One post scheduled for 09:00, already out."),
    ),
    "accountant": (
        (21, 10, 0, "agent", "Q3 books are reconciled. Two transactions I could not categorise \u2014 \u00a3240 on the 14th and \u00a389 on the 22nd, both card, no description."),
        (21, 10, 6, "user", "240 was the monitor, 89 was a domain renewal"),
        (21, 10, 6, "agent", "Filed as equipment and software. Both allowable."),
        (16, 9, 20, "agent", "VAT return is due in 11 days. The figure is \u00a34,182.60. I have it prepared but not submitted."),
        (16, 9, 40, "user", "does that account for the refund"),
        (16, 9, 42, "agent", "It does \u2014 the \u00a3610 refund on the 9th is netted off. Without it the figure would be \u00a34,304.60."),
        (13, 17, 12, "agent", "Your accountant's fee invoice arrived, \u00a3750. It is 40% higher than last year with no explanation attached. Want me to ask?"),
        (13, 17, 30, "user", "ask, politely"),
        (13, 17, 31, "agent", "Asked."),
        (10, 11, 45, "agent", "They replied: the increase is the extra payroll run you added in June. Legitimate. I have approved it for payment on the due date."),
        (7, 8, 30, "agent", "Cash position: \u00a318,420 available, \u00a36,900 committed in the next 30 days. Comfortable."),
        (4, 15, 55, "user", "can we afford the second Pi order"),
        (4, 15, 56, "agent", "Yes. \u00a31,340 leaves \u00a310,180 of headroom after the VAT payment. It is a capital purchase, so it also reduces this year's profit."),
        (4, 15, 58, "user", "order it"),
        (4, 15, 58, "agent", "That is a payment, so it needs your explicit approval in the Decisions app. Raised."),
        (1, 9, 15, "agent", "Two invoices went out this morning, \u00a32,100 total. Both 14-day terms."),
        (0, 8, 5, "agent", "Nothing overdue today. VAT is the only live deadline."),
    ),
    "sales-manager": (
        (20, 9, 40, "agent", "Pipeline is 11 live conversations. Four are warm, two have budget confirmed, the rest are early."),
        (18, 14, 10, "agent", "Westbrook asked for a 20% discount on a 12-seat deal. That is below the floor you set. I have not answered."),
        (18, 14, 22, "user", "offer 10 and annual prepay"),
        (18, 14, 23, "agent", "Sent. Framed as a prepay discount rather than a price cut, so it does not reset their anchor."),
        (15, 11, 30, "agent", "Westbrook accepted. 12 seats, annual, signed. That is the biggest deal this quarter."),
        (15, 11, 31, "user", "nice"),
        (12, 16, 45, "agent", "Two deals have gone quiet for 9 days. Both went silent right after the security questionnaire, which suggests the questionnaire is the problem, not the price."),
        (12, 16, 50, "user", "what do they ask that we fail"),
        (12, 16, 52, "agent", "SOC 2 and a documented incident response. We have neither. Every other question we pass."),
        (9, 10, 5, "agent", "Drafted a one-page security summary that answers 80% of the questionnaire honestly and says plainly what we do not have yet. Ready for your review."),
        (5, 13, 20, "agent", "The honest summary unstuck one of the two. They booked a call for Thursday."),
        (2, 9, 55, "agent", "Renewals: three accounts come up in the next 6 weeks, \u00a38,400 combined. All three have used the product in the last 7 days, so none look at risk."),
        (0, 8, 30, "agent", "One call today at 15:00. Brief is in your inbox."),
    ),
    "customer-service": (
        (21, 8, 0, "agent", "Inbox cleared overnight. 14 tickets, 11 resolved, 3 escalated to you."),
        (19, 12, 30, "agent", "Same install error from four different people this week \u2014 the installer dies silently partway through when the SSH session drops. I filed it rather than answering each one."),
        (19, 12, 40, "user", "good catch"),
        (16, 9, 10, "agent", "The fix shipped. I replied to all four with the release and an apology. Two have already confirmed it works."),
        (14, 15, 20, "agent", "A user is unhappy about the beta cadence \u2014 says updates break things too often. It reads as a real complaint, not noise."),
        (14, 15, 35, "user", "what do you suggest"),
        (14, 15, 37, "agent", "Offer them the stable channel and be honest that beta is beta. I would not promise a slower cadence."),
        (14, 15, 38, "user", "do that"),
        (11, 10, 0, "agent", "They moved to stable and thanked us. Ticket closed."),
        (8, 17, 45, "agent", "Median first reply is now 22 minutes, down from 3 hours when I started. 96% resolved without escalation."),
        (5, 11, 12, "agent", "Someone asked for a refund outside the window. That is a money decision, so it is yours \u2014 raised in Decisions, not answered."),
        (2, 9, 30, "agent", "Quiet week. 6 tickets, all resolved, none escalated."),
        (0, 7, 50, "agent", "Two tickets open, both answered and waiting on the user."),
    ),
}

#: Shown when a demo agent has no scripted thread of its own. Deliberately
#: short: a generic filler thread pretending to be weeks of history would be
#: more misleading than an obviously new conversation.
_DEMO_THREAD_FALLBACK: tuple[tuple[int, int, int, str, str], ...] = (
    (2, 9, 30, "agent", "I am set up and running. Nothing to report yet."),
    (0, 8, 15, "agent", "Still nothing that needs you. I will speak up when there is."),
)


def _demo_enabled() -> bool:
    """Whether the lock screen's demo content is switched on.

    One flag governs the placeholder agents, their scripted threads and the
    demo decision, so a machine cannot end up showing invented conversations
    while believing it is in its real state.
    """
    return bool(os.environ.get("TAOS_LOCK_DEMO_AGENTS", "").strip())


def _demo_notifications_enabled() -> bool:
    """Whether the lock screen's notification stacks are switched on.

    Narrower than _demo_enabled and OFF by default: the stacks are being
    redesigned, and the screen reads better meanwhile as clock, weather and
    agent islands alone. Requires BOTH flags rather than replacing the master
    one, so switching off TAOS_LOCK_DEMO_AGENTS still takes down everything
    invented on this pre-sign-in screen in a single move.
    """
    if not _demo_enabled():
        return False
    return bool(os.environ.get("TAOS_LOCK_DEMO_NOTIFICATIONS", "").strip())


def _demo_thread(slug: str) -> list[dict]:
    """Build one scripted thread as absolute timestamps relative to now."""
    script = _DEMO_THREADS.get(slug, _DEMO_THREAD_FALLBACK)
    now = datetime.now()
    out: list[dict] = []
    for days_ago, hh, mm, role, text in script:
        when = (now - timedelta(days=days_ago)).replace(
            hour=hh, minute=mm, second=0, microsecond=0
        )
        out.append({"role": role, "text": text, "at": when.timestamp()})
    return out


@router.get("/lock-thread/{slug}")
async def lock_thread(slug: str, request: Request):
    """Scripted conversation for the lock screen's chat sheet. Console-only.

    DEMO CONTENT ONLY. This never reads the chat store: the lock screen is
    pre-authentication, so a real transcript served here would be readable by
    anyone holding the phone. With demo mode off there is no thread to serve
    and the answer is 404, which is also what an unknown agent gets.
    """
    if not _request_is_console(request):
        return JSONResponse({"error": "console only"}, status_code=403)
    if not _demo_enabled():
        return JSONResponse({"error": "not found"}, status_code=404)

    safe = _avatar_slug(slug)
    if not safe:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"slug": safe, "messages": _demo_thread(safe), "demo": True})


#: The lock screen's weather is FIXED to Liverpool, in Celsius and mph.
#:
#: Not a preference and not a lookup: this surface renders BEFORE sign-in, so
#: there is no account to read a home town from, and the alternative -- asking
#: the browser for geolocation -- would put a permission prompt on a locked
#: phone, aimed at whoever is holding it. A constant is the honest answer.
_WEATHER_PLACE = "Liverpool"
_WEATHER_LAT = 53.4084
_WEATHER_LON = -2.9916

#: Open-Meteo needs no API key, which is the whole reason it is the source: a
#: key would have to live on the device, and this endpoint answers anyone
#: holding the phone.
_WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

#: A lock screen repaints every time the phone is woken. Without a cache a
#: pocketed handset would call the forecast API dozens of times an hour for a
#: number that changes four times a day.
_WEATHER_TTL_SECONDS = 900
_WEATHER_TIMEOUT_SECONDS = 6.0

_weather_cached: dict | None = None
_weather_cached_at: float = 0.0
#: Serialises the refresh, so a wake that paints several times does one call
#: rather than one per paint.
_weather_lock = asyncio.Lock()

#: WMO weather code -> (what to call it, which icon the page should draw).
#:
#: The icon name is decided HERE rather than in the script so the wording and
#: the picture cannot drift apart: the page owns eight shapes and is told which
#: one to use. Codes absent from this table fall back to plain cloud, which is
#: wrong-ish rather than blank.
_WMO_CONDITIONS: dict[int, tuple[str, str]] = {
    0: ("Clear", "clear"),
    1: ("Mainly clear", "clear"),
    2: ("Partly cloudy", "partly"),
    3: ("Overcast", "cloud"),
    45: ("Fog", "fog"),
    48: ("Freezing fog", "fog"),
    51: ("Light drizzle", "drizzle"),
    53: ("Drizzle", "drizzle"),
    55: ("Heavy drizzle", "drizzle"),
    56: ("Freezing drizzle", "drizzle"),
    57: ("Freezing drizzle", "drizzle"),
    61: ("Light rain", "rain"),
    63: ("Rain", "rain"),
    65: ("Heavy rain", "rain"),
    66: ("Freezing rain", "rain"),
    67: ("Freezing rain", "rain"),
    71: ("Light snow", "snow"),
    73: ("Snow", "snow"),
    75: ("Heavy snow", "snow"),
    77: ("Snow grains", "snow"),
    80: ("Light showers", "rain"),
    81: ("Showers", "rain"),
    82: ("Heavy showers", "rain"),
    85: ("Snow showers", "snow"),
    86: ("Snow showers", "snow"),
    95: ("Thunderstorm", "storm"),
    96: ("Thunderstorm", "storm"),
    99: ("Thunderstorm", "storm"),
}


def _weather_condition(code: int, is_day: bool) -> tuple[str, str]:
    """Name and icon for one WMO code, with the night variant of a clear sky."""
    label, icon = _WMO_CONDITIONS.get(int(code), ("Cloudy", "cloud"))
    if icon == "clear" and not is_day:
        return (label, "night")
    return (label, icon)


async def _fetch_weather() -> dict | None:
    """One call to the forecast API, shaped into what the lock screen draws.

    Returns None on any failure. Every caller treats that as "show no weather":
    a lock screen that renders an error string because a forecast host was slow
    is worse than one that simply has no weather line.
    """
    params = {
        "latitude": _WEATHER_LAT,
        "longitude": _WEATHER_LON,
        "current": "temperature_2m,apparent_temperature,is_day,weather_code,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min",
        "timezone": "Europe/London",
        "temperature_unit": "celsius",
        "wind_speed_unit": "mph",
        "forecast_days": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=_WEATHER_TIMEOUT_SECONDS) as client:
            resp = await client.get(_WEATHER_URL, params=params)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:  # noqa: BLE001 - offline, DNS, timeout, bad JSON
        logger.debug("lock-screen weather fetch failed: %s", exc)
        return None

    current = payload.get("current") or {}
    daily = payload.get("daily") or {}
    temp = current.get("temperature_2m")
    if temp is None:
        return None

    label, icon = _weather_condition(
        current.get("weather_code") or 0,
        bool(current.get("is_day", 1)),
    )

    def first(seq, default=None):
        return seq[0] if isinstance(seq, list) and seq else default

    return {
        "place": _WEATHER_PLACE,
        "temp": float(temp),
        "feels": current.get("apparent_temperature"),
        "label": label,
        "icon": icon,
        "wind": current.get("wind_speed_10m"),
        "high": first(daily.get("temperature_2m_max")),
        "low": first(daily.get("temperature_2m_min")),
        # Stated rather than implied: the page renders "°" and "mph" and should
        # not have to assume which system produced the numbers.
        "units": {"temperature": "celsius", "wind": "mph"},
    }


async def _weather_reading() -> dict | None:
    """The cached reading, refreshed at most every _WEATHER_TTL_SECONDS.

    A failed refresh keeps serving the last good reading rather than blanking
    the row: a stale temperature is still roughly true, and a line that
    disappears every time the phone's link drops looks broken.
    """
    global _weather_cached, _weather_cached_at

    now = time.time()
    if _weather_cached is not None and now - _weather_cached_at < _WEATHER_TTL_SECONDS:
        return _weather_cached

    async with _weather_lock:
        # Re-check under the lock: several paints can queue behind one refresh.
        now = time.time()
        if _weather_cached is not None and now - _weather_cached_at < _WEATHER_TTL_SECONDS:
            return _weather_cached
        fresh = await _fetch_weather()
        if fresh is not None:
            _weather_cached = fresh
            _weather_cached_at = now
    return _weather_cached


@router.get("/lock-weather")
async def lock_weather(request: Request):
    """Weather for the lock screen. Console-only, fetched SERVER-SIDE.

    The page deliberately does not call the forecast API itself. The lock screen
    paints before sign-in and every time the screen wakes, so a browser-side
    call would announce this device to a third-party host on every wake, from a
    surface nobody has authenticated to. Going through the server also means the
    result can be cached once for the device instead of per page load.

    Nothing here is account-derived: a fixed city, a public forecast, no key.
    """
    if not _request_is_console(request):
        return JSONResponse({"error": "console only"}, status_code=403)
    reading = await _weather_reading()
    if reading is None:
        # The page treats a non-200 as "no weather" and leaves the row hidden.
        return JSONResponse({"error": "unavailable"}, status_code=503)
    return JSONResponse(reading)


#: Scripted lock-screen notifications, collated per source.
#:
#: DEMO CONTENT, and it can never be anything else. The lock screen renders
#: BEFORE sign-in, so serving real mail, real messages or a real call log here
#: would hand the contents of the phone to whoever picked it up. This table is
#: the only thing /auth/lock-notifications can serve, which is why there is no
#: gate to get wrong: there is no code path from this screen to an inbox.
#:
#: Each item is (minutes_ago, title, text). Offsets rather than timestamps so
#: the stack always reads as "this morning", whenever the demo is run. The
#: tints are the colours the app tiles are drawn in; `glyph` picks one of the
#: page's three drawn marks and `mono` is the letterform used when there is no
#: glyph for that source.
_DEMO_NOTIFICATIONS: tuple[dict, ...] = (
    {
        "source": "mail",
        "app": "Mail",
        "glyph": "mail",
        "tint": "#2f6fd0",
        "items": (
            (12, "Hargreaves & Co", "Re: Thursday's site visit — 09:15 works for us. I'll bring the revised drawings."),
            (74, "Companies House", "Your confirmation statement is due on 3 October."),
            (221, "Liverpool FC", "Your ticket ballot result for Newcastle (H) is ready to view."),
        ),
    },
    {
        "source": "x",
        "app": "X",
        "mono": "X",
        "tint": "#3b3b42",
        "items": (
            (8, "@marcus_dev mentioned you", "what's the actual memory floor for running this on a 4GB board?"),
            (96, "12 posts from people you follow", "including 3 about on-device inference"),
        ),
    },
    {
        "source": "reddit",
        "app": "Reddit",
        "mono": "r",
        "tint": "#ff4500",
        "items": (
            (34, "r/selfhosted · 47 upvotes", "Someone replied to your comment on “Running an agent OS on a single board”."),
            (150, "r/LocalLLaMA", "Today's discussion thread is up."),
        ),
    },
    {
        "source": "phone",
        "app": "Phone",
        "glyph": "phone",
        "tint": "#34c759",
        "items": (
            (41, "Missed call", "2 missed calls"),
        ),
    },
    {
        "source": "sms",
        "app": "Messages",
        "glyph": "sms",
        "tint": "#25c05d",
        "items": (
            (19, "Sam", "are you still alright for Sunday?"),
            (310, "O2", "You've used 80% of your data allowance this month."),
        ),
    },
)


def _demo_notifications() -> list[dict]:
    """The scripted stacks, timestamped relative to now and newest-first.

    Sorted by each stack's newest item, the way a phone orders its notification
    list -- a fixed table order would leave an hours-old stack sitting above one
    that arrived a minute ago.
    """
    now = time.time()
    groups: list[dict] = []
    for spec in _DEMO_NOTIFICATIONS:
        items = [
            {
                "title": title,
                "text": text,
                "at": now - (minutes_ago * 60),
            }
            for minutes_ago, title, text in spec["items"]
        ]
        items.sort(key=lambda item: item["at"], reverse=True)
        groups.append({
            "source": spec["source"],
            "app": spec["app"],
            "glyph": spec.get("glyph", ""),
            "mono": spec.get("mono", ""),
            "tint": spec.get("tint", ""),
            "items": items,
            # Marked at construction so nothing downstream has to work out that
            # these are placeholders by elimination.
            "demo": True,
        })
    groups.sort(key=lambda group: group["items"][0]["at"], reverse=True)
    return groups


#: Previous /proc/stat reading, so CPU can be a PERCENTAGE. A single sample of
#: /proc/stat gives cumulative jiffies since boot; dividing those by uptime
#: yields the average load since the phone was switched on, which on a device
#: that has been up for days barely moves. Utilisation is a delta between two
#: readings or it is not utilisation.
_CPU_LAST: dict[str, float] = {}


def _read_cpu_percent() -> float | None:
    """Busy share of all cores since the previous call, or None on the first.

    None rather than 0.0 on the first call, and the caller shows "--": zero is a
    claim that the CPU is idle, and we do not know that yet. The lock screen
    polls, so the second call lands a few seconds later and has a real number.
    """
    try:
        with open("/proc/stat", "r", encoding="ascii") as fh:
            parts = fh.readline().split()
    except OSError:
        return None
    if len(parts) < 5 or parts[0] != "cpu":
        return None
    try:
        vals = [float(v) for v in parts[1:]]
    except ValueError:
        return None
    total = sum(vals)
    # idle + iowait. iowait is time the CPU had nothing to run, so counting it
    # as busy would show a phone reading from flash as a phone under load.
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0.0)

    prev_total = _CPU_LAST.get("total")
    prev_idle = _CPU_LAST.get("idle")
    _CPU_LAST["total"] = total
    _CPU_LAST["idle"] = idle
    if prev_total is None or prev_idle is None:
        return None
    d_total = total - prev_total
    d_idle = idle - prev_idle
    # A counter that did not move (or went backwards, which /proc/stat does
    # across a suspend) tells us nothing; do not divide by it.
    if d_total <= 0:
        return None
    pct = (1.0 - (d_idle / d_total)) * 100.0
    return max(0.0, min(100.0, round(pct, 1)))


def _read_memory() -> dict | None:
    """Total and in-use RAM in kB, from MemAvailable rather than MemFree.

    MemFree counts the page cache as used and would report a healthy Linux
    phone as almost out of memory.
    """
    want = ("MemTotal", "MemAvailable")
    found: dict[str, int] = {}
    try:
        with open("/proc/meminfo", "r", encoding="ascii") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                if key in want:
                    try:
                        found[key] = int(rest.split()[0])
                    except (IndexError, ValueError):
                        return None
                    if len(found) == len(want):
                        break
    except OSError:
        return None
    if len(found) != len(want) or found["MemTotal"] <= 0:
        return None
    total = found["MemTotal"]
    used = max(0, total - found["MemAvailable"])
    return {
        "total_kb": total,
        "used_kb": used,
        "percent": round(used * 100.0 / total, 1),
    }


#: The GPU's devfreq node on this SoC.
_GPU_DEVFREQ = "/sys/class/devfreq/3d00000.gpu"


def _read_gpu() -> dict | None:
    """GPU frequency, and an activity proxy derived from trans_stat.

    ⛔ THIS IS NOT A UTILISATION COUNTER, and the field names say so. Measured
    on the handset: this devfreq node has cur_freq, max_freq, governor and
    trans_stat, and NO `load` file. There is no busy-percent to read.

    So two honest numbers instead of one invented one:
      * freq_hz / max_freq_hz -- what the GPU is clocked at right now. A ratio
        of these is a FREQUENCY ratio; an idle GPU parked at its minimum is not
        "23% used". The UI labels it as frequency.
      * active_percent -- share of time since boot spent above the LOWEST
        frequency state, from trans_stat. That is a real measurement of a real
        thing, but it is time-above-idle-clock, not busy time, and it is
        cumulative since boot rather than current. Named to match.
    """
    out: dict = {}
    for key, name in (("freq_hz", "cur_freq"), ("max_freq_hz", "max_freq")):
        try:
            with open("%s/%s" % (_GPU_DEVFREQ, name), "r", encoding="ascii") as fh:
                out[key] = int(fh.read().strip())
        except (OSError, ValueError):
            pass
    try:
        with open("%s/trans_stat" % _GPU_DEVFREQ, "r", encoding="ascii") as fh:
            rows = fh.read().splitlines()
    except OSError:
        rows = []

    # trans_stat is a transition MATRIX with a time-in-state column; the data
    # rows start with a frequency (and a ':' marker on the current state) and
    # end with that state's total time in ms. Anything else is the header or
    # the total-transitions footer.
    states: list[tuple[int, int]] = []
    for row in rows:
        head, sep, _ = row.partition(":")
        if not sep:
            continue
        # The kernel marks the CURRENT state with a leading '*'. Parsing the
        # row without stripping it raises, which silently drops exactly one
        # row -- and on an idle phone the current state IS the lowest one, so
        # the idle time would vanish and every reading would look busier than
        # the GPU really is.
        try:
            freq = int(head.strip().lstrip("*").strip())
        except ValueError:
            continue
        cells = row.split()
        try:
            states.append((freq, int(cells[-1])))
        except (IndexError, ValueError):
            continue
    if states:
        total_ms = sum(ms for _f, ms in states)
        lowest = min(f for f, _ms in states)
        idle_ms = sum(ms for f, ms in states if f == lowest)
        if total_ms > 0:
            out["active_percent"] = round((total_ms - idle_ms) * 100.0 / total_ms, 1)
    return out or None


def _read_dsp_states() -> list[dict]:
    """The remote processors, as NAMED STATES. No utilisation, by necessity.

    ⛔ The cDSP -- the "NPU" -- exposes state, name, firmware, coredump and
    recovery, and NOTHING about how busy it is. Mainline remoteproc has no such
    counter, so an "NPU usage %" on this screen would be a number with nothing
    behind it. A gauge that is always making something up is worse than an
    honest running/offline chip, and this is the first screen the phone shows.
    """
    import glob

    out: list[dict] = []
    for path in sorted(glob.glob("/sys/class/remoteproc/remoteproc*")):
        entry: dict = {}
        for key in ("name", "state"):
            try:
                with open("%s/%s" % (path, key), "r", encoding="utf-8") as fh:
                    entry[key] = fh.read().strip()
            except OSError:
                pass
        if entry.get("name"):
            out.append(entry)
    return out


@router.get("/lock-stats")
async def lock_stats(request: Request):
    """System readings for the lock screen's stats view. Console-only.

    Same containment as the other lock endpoints: it renders pre-auth, so the
    console gate is what keeps it off the LAN, and what it returns is what the
    phone already shows anyone holding it -- load, memory, clock speeds. No
    process names, no paths, no config.

    Every field is optional. A reading the kernel does not expose is ABSENT
    rather than zero, and the UI shows "--" for it: a zero here would be a claim
    about the hardware that nothing measured.
    """
    if not _request_is_console(request):
        return JSONResponse({"error": "console only"}, status_code=403)

    payload: dict = {}

    cpu = _read_cpu_percent()
    if cpu is not None:
        payload["cpu_percent"] = cpu
    try:
        cores = os.cpu_count()
    except Exception:  # noqa: BLE001 - a missing core count is cosmetic
        cores = None
    if cores:
        payload["cpu_cores"] = cores

    mem = _read_memory()
    if mem:
        payload["memory"] = mem

    gpu = _read_gpu()
    if gpu:
        payload["gpu"] = gpu

    dsps = _read_dsp_states()
    if dsps:
        payload["dsps"] = dsps

    # Loaded models are NOT an OS reading. Measured on the handset: no ollama
    # binary and nothing listening on 11434, so there is no local runtime to
    # ask. They belong to the controller, and until it offers them this key is
    # absent rather than an empty list -- "none loaded" and "nobody asked" are
    # different answers and the UI says so.
    return JSONResponse(payload)


@router.get("/lock-notifications")
async def lock_notifications(request: Request):
    """Collated notification stacks for the lock screen. Console-only.

    DEMO CONTENT ONLY, and OFF BY DEFAULT while the stacks are redesigned: this
    needs TAOS_LOCK_DEMO_NOTIFICATIONS on top of TAOS_LOCK_DEMO_AGENTS, so the
    islands can stay up with no stacks under them. The master flag still governs
    everything invented on this screen, so a device cannot end up showing made-up
    mail while believing it is in its real state. With either flag off there is
    nothing to serve and the answer is 404 -- the page treats that as "no
    notifications" and renders no stack.
    """
    if not _request_is_console(request):
        return JSONResponse({"error": "console only"}, status_code=403)
    if not _demo_notifications_enabled():
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"groups": _demo_notifications(), "demo": True})


@router.post("/pin-login")
async def pin_login(request: Request):
    """Sign in with a PIN. Console-only.

    Refused outright unless the request comes from the device's own screen. The
    refusal is deliberately indistinguishable from "no PIN is set": telling a
    remote caller that PIN sign-in exists here, and that it is merely being
    denied to them, is free reconnaissance for a guesser.
    """
    auth_mgr = request.app.state.auth
    if not _request_is_console(request):
        return JSONResponse({"error": "PIN sign-in is not available"}, status_code=404)

    body, body_err = await _json_object(request)
    if body_err is not None:
        return body_err
    username = (body.get("username") or "").strip() or None
    pin = body.get("pin") or ""

    # Resolve the throttle key before verifying, so a wrong username cannot be
    # used to sidestep the delay by cycling keys.
    record = auth_mgr._pin_user(username)
    limiter_key = (record or {}).get("id") or f"unknown:{username or ''}"

    wait = _pin_limiter.retry_after(limiter_key)
    if wait > 0:
        return JSONResponse(
            {
                "error": f"Too many incorrect PINs. Try again in {wait} seconds.",
                "retry_after": wait,
            },
            status_code=429,
            headers={"Retry-After": str(wait)},
        )

    ok, user_record = auth_mgr.check_pin(pin, username=username)
    if not ok or user_record is None:
        _pin_limiter.record_failure(limiter_key)
        return JSONResponse({"error": "incorrect PIN"}, status_code=401)

    _pin_limiter.reset(limiter_key)
    auth_mgr.update_last_login(user_record["id"])
    # A PIN unlocks THIS device, so the session it mints is the long-lived kind
    # the kiosk needs to survive a reboot without a keyboard being found.
    token = auth_mgr.create_session(
        user_id=user_record["id"],
        long_lived=True,
        user_agent=request.headers.get("user-agent", ""),
    )
    resp = JSONResponse({"ok": True, "user": auth_mgr._public_user(user_record)})
    resp.set_cookie(
        "taos_session", token, httponly=True, samesite="strict",
        max_age=auth_mgr.session_ttl_for(True),
    )
    return resp


@router.post("/pin", dependencies=[Depends(verify_csrf)])
async def set_pin(request: Request):
    """Set or replace the signed-in user's PIN.

    Requires the account PASSWORD in the body even though the caller already
    holds a session. A PIN is a credential that unlocks the device, so minting
    one must cost the real credential -- otherwise anyone who walks up to an
    unlocked screen can quietly add a permanent way back in.
    """
    auth_mgr = request.app.state.auth
    token = request.cookies.get("taos_session", "")
    user_id = auth_mgr.validate_session(
        token, user_agent=request.headers.get("user-agent", "")
    ) if token else None
    if not user_id:
        return JSONResponse({"error": "not authenticated"}, status_code=401)

    body, body_err = await _json_object(request)
    if body_err is not None:
        return body_err

    user = auth_mgr.get_user_by_id(user_id)
    if not user:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    username = user.get("username", "")

    ok, _ = auth_mgr.check_password(body.get("password") or "", username=username)
    if not ok:
        return JSONResponse({"error": "incorrect password"}, status_code=403)

    try:
        validate_pin(body.get("pin") or "")
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    auth_mgr.set_pin(username, body["pin"])
    _pin_limiter.reset(user_id)
    return JSONResponse({"ok": True, "has_pin": True})


@router.delete("/pin", dependencies=[Depends(verify_csrf)])
async def delete_pin(request: Request):
    """Remove the signed-in user's PIN.

    No password required: turning a credential OFF only ever reduces what an
    attacker could reach, and demanding a typed password to disable PIN would
    be unperformable on the keyboard-less device this feature serves.
    """
    auth_mgr = request.app.state.auth
    token = request.cookies.get("taos_session", "")
    user_id = auth_mgr.validate_session(
        token, user_agent=request.headers.get("user-agent", "")
    ) if token else None
    if not user_id:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    user = auth_mgr.get_user_by_id(user_id)
    if not user:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    removed = auth_mgr.clear_pin(user.get("username", ""))
    _pin_limiter.reset(user_id)
    return JSONResponse({"ok": True, "removed": removed, "has_pin": False})


@router.post("/logout", dependencies=[Depends(verify_csrf)])
async def logout(request: Request):
    auth_mgr = request.app.state.auth
    token = request.cookies.get("taos_session")
    if token:
        auth_mgr.revoke_session(token)
    response = RedirectResponse("/auth/login", status_code=303)
    response.delete_cookie("taos_session")
    return response


@router.post("/lock", dependencies=[Depends(verify_csrf)])
async def lock(request: Request):
    """Revoke the current session and clear the cookie."""
    auth_mgr = request.app.state.auth
    token = request.cookies.get("taos_session")
    if token:
        auth_mgr.revoke_session(token)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("taos_session")
    return resp


async def _ensure_native_agent_identity(request: Request, user_id: str) -> None:
    """Mint this install's native agent identity, now that it has an owner.

    Called from BOTH setup paths (JSON and form).  They are two routes into the
    same event -- an install acquiring its first user -- and wiring only the one
    you happened to test is how a fresh install ends up with no agent identity
    while every test passes.  The paired tests below cover both.

    Never raises: an install whose agent identity failed to mint is degraded,
    not broken, and failing setup over it would strand the user on the setup
    page with an account that already exists.
    """
    try:
        from tinyagentos.native_agent_identity import ensure_native_agent_identity

        await ensure_native_agent_identity(
            registry=request.app.state.agent_registry,
            grants=request.app.state.agent_grants,
            data_dir=request.app.state.data_dir,
            signing_key_pem=request.app.state.agent_registry_keypair[0],
            user_id=user_id,
        )
    except Exception:
        logger.exception("native agent identity could not be minted at setup")


@router.post("/setup")
async def auth_setup(request: Request):
    """Onboard the first user. Only works when zero users exist.

    Accepts JSON or form-encoded.

    JSON body: ``{username, full_name, email, password}``. Returns the
    new user's public profile and sets a session cookie.

    Form body: legacy single-password setup (kept for backward compat).
    """
    auth_mgr = request.app.state.auth

    content_type = request.headers.get("content-type", "")
    user_agent = request.headers.get("user-agent", "")
    if "application/json" in content_type:
        body, body_err = await _json_object(request)
        if body_err:
            return body_err
        if auth_mgr.is_configured():
            return JSONResponse({"error": "already configured"}, status_code=409)
        username = (body.get("username") or "").strip()
        full_name = (body.get("full_name") or "").strip()
        email = (body.get("email") or "").strip()
        password = body.get("password") or ""
        if not username:
            return JSONResponse({"error": "username is required"}, status_code=400)
        if not password or len(password) < 8:
            return JSONResponse({"error": "password must be at least 8 characters"}, status_code=400)
        # Validated BEFORE the account is created: a bad PIN must not leave a
        # half-onboarded install behind, and /setup only works while zero users
        # exist, so a second attempt would answer 409 rather than retry.
        pin = (body.get("pin") or "").strip()
        if pin:
            if not _request_is_console(request):
                return JSONResponse(
                    {"error": "a PIN can only be set from this device's own screen"},
                    status_code=400,
                )
            try:
                pin = validate_pin(pin)
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
        try:
            user = auth_mgr.setup_user(username, full_name, email, password)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if pin:
            auth_mgr.set_pin(username, pin)
        long_lived = bool(body.get("auto_login", True))
        # Look up the newly created record to get the ID
        record = auth_mgr.find_user(username)
        user_id = record["id"] if record else ""
        auth_mgr.update_last_login(user_id)
        await _ensure_native_agent_identity(request, user_id)
        token = auth_mgr.create_session(user_id=user_id, long_lived=long_lived, user_agent=user_agent)
        resp = JSONResponse({"ok": True, "user": user})
        if long_lived:
            resp.set_cookie(
                "taos_session", token, httponly=True, samesite="strict",
                max_age=auth_mgr.session_ttl_for(True),
            )
        else:
            resp.set_cookie("taos_session", token, httponly=True, samesite="strict")
        return resp

    # Form-encoded path — used by the no-JS HTML setup page.
    if auth_mgr.is_configured():
        return RedirectResponse("/auth/login", status_code=303)
    form = await request.form()
    username = (form.get("username") or "").strip()
    full_name = (form.get("full_name") or "").strip()
    email = (form.get("email") or "").strip()
    password = form.get("password", "")
    long_lived = bool(form.get("auto_login"))

    if not username:
        return RedirectResponse("/auth/setup?error=username", status_code=303)
    if not password or len(password) < 8:
        return RedirectResponse("/auth/setup?error=password", status_code=303)
    # Same order as the JSON path: reject a bad PIN before creating the account.
    # Silently DROPPED off-console rather than refused — the field is not even
    # rendered there, so anything arriving in it was not typed by this user.
    pin = (form.get("pin") or "").strip()
    if pin and not _request_is_console(request):
        pin = ""
    if pin:
        try:
            pin = validate_pin(pin)
        except ValueError:
            return RedirectResponse("/auth/setup?error=pin", status_code=303)
    try:
        auth_mgr.setup_user(username, full_name, email, password)
    except ValueError:
        return RedirectResponse("/auth/setup?error=conflict", status_code=303)
    if pin:
        auth_mgr.set_pin(username, pin)

    record = auth_mgr.find_user(username)
    user_id = record["id"] if record else ""
    auth_mgr.update_last_login(user_id)
    await _ensure_native_agent_identity(request, user_id)
    token = auth_mgr.create_session(user_id=user_id, long_lived=long_lived, user_agent=user_agent)
    response = RedirectResponse("/desktop", status_code=303)
    if long_lived:
        response.set_cookie(
            "taos_session", token, httponly=True, samesite="strict",
            max_age=auth_mgr.session_ttl_for(True),
        )
    else:
        response.set_cookie("taos_session", token, httponly=True, samesite="strict")
    return response


@router.post("/complete")
async def complete_invite(request: Request):
    """Invited user completes their account setup.

    Body: ``{username, invite_code, full_name, email, password, auto_login?}``
    """
    auth_mgr = request.app.state.auth
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "")

    if _complete_limiter.is_limited(client_ip):
        return JSONResponse(
            {"error": "too many attempts, try again later"},
            status_code=429,
        )

    body, body_err = await _json_object(request)
    if body_err:
        return body_err

    username = (body.get("username") or "").strip()
    invite_code = (body.get("invite_code") or "").strip()
    full_name = (body.get("full_name") or "").strip()
    email = (body.get("email") or "").strip()
    password = body.get("password") or ""

    if not username or not invite_code:
        return JSONResponse({"error": "username and invite_code are required"}, status_code=400)
    if not password or len(password) < 8:
        return JSONResponse({"error": "password must be at least 8 characters"}, status_code=400)

    try:
        user = auth_mgr.complete_invite(username, invite_code, full_name, email, password)
    except ValueError as exc:
        _complete_limiter.record_failure(client_ip)
        return JSONResponse({"error": str(exc)}, status_code=400)

    _complete_limiter.reset(client_ip)
    long_lived = bool(body.get("auto_login", False))
    record = auth_mgr.find_user(username)
    user_id = record["id"] if record else ""
    auth_mgr.update_last_login(user_id)
    # Revoke any existing invite-phase sessions and create a fresh one
    auth_mgr.revoke_user_sessions(user_id)
    token = auth_mgr.create_session(user_id=user_id, long_lived=long_lived, user_agent=user_agent)
    resp = JSONResponse({"ok": True, "user": user})
    if long_lived:
        resp.set_cookie(
            "taos_session", token, httponly=True, samesite="strict",
            max_age=auth_mgr.session_ttl_for(True),
        )
    else:
        resp.set_cookie("taos_session", token, httponly=True, samesite="strict")
    return resp


@router.get("/status")
async def auth_status(request: Request):
    """Single endpoint the UI calls to decide what to render.

    Returns ``{configured, authenticated, user, multi_user, needs_onboarding}``.
    """
    auth_mgr = request.app.state.auth
    configured = auth_mgr.is_configured()
    # An unreadable store reports configured (see AuthManager.is_configured),
    # so tell the UI *why* it can neither sign in nor onboard instead of
    # leaving it to guess from a failing login.
    store_error = None
    try:
        auth_mgr._read_users()
    except AuthStoreCorruptError:
        store_error = "unreadable"
    token = request.cookies.get("taos_session", "")
    # Pass the request's User-Agent so the stolen-cookie binding check runs
    # here exactly as it does in the API middleware. Without it a session
    # whose UA hash no longer matches (browser auto-update rotated the UA)
    # reads authenticated here while every /api/* call 401s, and the SPA's
    # LoginGate remount-loops on that contradiction (the beta.46 PWA
    # refresh-loop, 2026-08-10).
    _ua = request.headers.get("user-agent", "")
    user_id = auth_mgr.validate_session(token, user_agent=_ua) if token else None
    authenticated = user_id is not None

    user = None
    needs_onboarding = False
    # get_user()/session_user() read the same store the probe just failed on,
    # so consulting them here would raise and turn this endpoint into a 500 --
    # exactly the answer the store_error field exists to replace.
    if configured and authenticated and store_error is None:
        user = auth_mgr.get_user(token=token)
        # Check if session user is pending
        if token:
            session_user = auth_mgr.session_user(token)
            if session_user and session_user.get("pending"):
                needs_onboarding = True

    # Whether the sign-in UI should offer a PIN keypad at all. This is the AND
    # of "a PIN exists" and "this request is on the console", so a LAN browser
    # is never told that PIN sign-in exists on this box -- it simply is not
    # offered one, which matches /auth/pin-login answering 404 off-console.
    # Reported only to callers who are not yet signed in; there is nothing for
    # a live session to do with it.
    pin_available = False
    if configured and store_error is None and not authenticated:
        try:
            pin_available = _request_is_console(request) and auth_mgr.has_pin()
        except AuthStoreCorruptError:
            pin_available = False

    return JSONResponse({
        "configured": configured,
        "authenticated": authenticated,
        "user": user,
        "multi_user": auth_mgr.is_multi_user(),
        "needs_onboarding": needs_onboarding,
        "store_error": store_error,
        "pin_available": pin_available,
    })


@router.get("/me")
async def auth_me(request: Request):
    """Return the current user's profile. 401 when not signed in."""
    auth_mgr = request.app.state.auth
    token = request.cookies.get("taos_session", "")
    if not token or auth_mgr.validate_session(
        token, user_agent=request.headers.get("user-agent", "")
    ) is None:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    user = auth_mgr.get_user(token=token)
    if user is None:
        return JSONResponse({"error": "no user configured"}, status_code=404)
    return JSONResponse({"user": user})


# ------------------------------------------------------------------ #
#  User management endpoints                                           #
# ------------------------------------------------------------------ #

@router.get("/users")
async def list_users(request: Request):
    """List all users. Admin only when multi-user."""
    auth_mgr = request.app.state.auth
    if auth_mgr.is_multi_user():
        ok, err = _require_admin(request)
        if not ok:
            return err
    return JSONResponse({"users": auth_mgr.list_users()})


@router.post("/users")
async def add_user(request: Request):
    """Admin: create a pending user invite. Returns {invite_code}."""
    ok, err = _require_admin(request)
    if not ok:
        return err
    body, body_err = await _json_object(request)
    if body_err:
        return body_err
    username = (body.get("username") or "").strip()
    if not username:
        return JSONResponse({"error": "username is required"}, status_code=400)
    auth_mgr = request.app.state.auth
    token = request.cookies.get("taos_session", "")
    caller = auth_mgr.session_user(token)
    caller_username = caller["username"] if caller else ""
    try:
        code = auth_mgr.add_user_invite(username, caller_username)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"ok": True, "username": username, "invite_code": code})


@router.post("/users/{username}/reset")
async def admin_reset_password(username: str, request: Request):
    """Admin: reset a user's password → new invite code."""
    ok, err = _require_admin(request)
    if not ok:
        return err
    auth_mgr = request.app.state.auth
    token = request.cookies.get("taos_session", "")
    caller = auth_mgr.session_user(token)
    caller_username = caller["username"] if caller else ""
    try:
        code = auth_mgr.admin_reset_password(username, caller_username)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"ok": True, "invite_code": code})


@router.delete("/users/{username}")
async def delete_user(username: str, request: Request):
    """Admin: remove a user."""
    ok, err = _require_admin(request)
    if not ok:
        return err
    auth_mgr = request.app.state.auth
    token = request.cookies.get("taos_session", "")
    caller = auth_mgr.session_user(token)
    caller_username = caller["username"] if caller else ""
    try:
        auth_mgr.delete_user(username, caller_username)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"ok": True})


@router.post("/users/{username}/profile")
async def update_profile(username: str, request: Request):
    """Self: update full_name and/or email."""
    ok, err = _require_self(request, username)
    if not ok:
        return err
    body, body_err = await _json_object(request)
    if body_err:
        return body_err
    full_name = body.get("full_name")
    email = body.get("email")
    auth_mgr = request.app.state.auth
    try:
        user = auth_mgr.update_profile(username, full_name, email)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"ok": True, "user": user})


@router.post("/users/{username}/password")
async def change_password(username: str, request: Request):
    """Self: change password (requires current password)."""
    ok, err = _require_self(request, username)
    if not ok:
        return err
    body, body_err = await _json_object(request)
    if body_err:
        return body_err
    current = body.get("current") or ""
    new_pw = body.get("new") or ""
    if not new_pw or len(new_pw) < 8:
        return JSONResponse({"error": "new password must be at least 8 characters"}, status_code=400)
    auth_mgr = request.app.state.auth
    changed = auth_mgr.change_password(username, current, new_pw)
    if not changed:
        return JSONResponse({"error": "current password is incorrect"}, status_code=401)
    return JSONResponse({"ok": True})
