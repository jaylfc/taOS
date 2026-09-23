from __future__ import annotations

import asyncio
import html
import json
import math
import random
import re
import secrets
import socket
from pathlib import Path
import logging
import os
import threading
import time
import zlib
from collections import OrderedDict
from urllib.parse import urlparse
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from tinyagentos.auth import (
    PIN_MAX_LEN,
    PIN_MIN_LEN,
    AuthStoreCorruptError,
    _PinAttemptLimiter,
    is_console_origin,
    validate_pin,
)
from tinyagentos.atomic_io import atomic_write_text
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
/* 7px, not 4: Jay asked for the percentage 3px further left (it sat too close
   to the rounded corner). It is justify-self:end, so the RIGHT margin is what
   moves it -- padding or a transform would either move the brand with it or
   leave the real box where it was. */
#ls-battery { grid-column: 3; justify-self: end; margin-right: 7px; }
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

/* Command output from a device agent. df, free and ip all speak in columns,
   and a proportional font turns them into a wall of text -- the one place on
   this screen where monospace is the honest rendering rather than a style.
   pre-wrap because the lines are already wrapped by the board and re-wrapping
   them on whitespace would shuffle the columns anyway. */
.ls-bubble[data-mono="1"] {
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
  font-size: 12px; line-height: 1.35; white-space: pre-wrap;
  overflow-x: auto;
}

/* PRESSING THE ACTIVE CATEGORY CLEARS THE FEED AWAY. Jay asked for it, and it
   is the one thing a lock screen full of cards could not do: see the screen
   underneath without unlocking or waiting for it to blank.

   Faded, NOT display:none. The row of category icons has to stay exactly where
   it is so the same press brings the content back, and a display change would
   collapse the column and jump the row down the screen mid-animation.
   translateY gives the fade somewhere to go so it reads as the cards dropping
   away rather than the screen dimming.

   pointer-events is what makes it honest: an invisible feed must not swallow a
   touch. It also hands the unlock swipe back the whole screen, because the
   swipe's veto only fires for touches that start inside .ls-feed -- with the
   cards gone, a swipe up unlocks from anywhere, which is what an empty screen
   should do. */
.ls-feed {
  transition: opacity 260ms cubic-bezier(.2, .8, .2, 1),
              transform 260ms cubic-bezier(.2, .8, .2, 1);
}
.ls-feed[data-hidden="1"] {
  opacity: 0;
  transform: translateY(10px);
  pointer-events: none;
}
/* The tab that is holding its content hidden says so rather than looking
   identical to one that is showing it. */
.ls-view-tab[aria-expanded="false"] { opacity: .55; }
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

/* THE ROW. Phone, mailbox and decisions are all the same object -- a tinted
   source mark, a line about it, and how long ago -- so they are one shape in
   one material rather than three panels that happen to look similar. It is the
   notification card's material deliberately: on this screen a missed call and a
   notification ARE the same kind of thing. */
.ls-row {
  display: flex; align-items: flex-start; gap: 10px;
  width: 100%; max-width: var(--ls-card-w);
  padding: 10px 13px;
  border-radius: 20px;
  text-align: left;
  background: rgba(30, 30, 34, 0.92);
  box-shadow: 0 6px 18px -6px rgba(0, 0, 0, 0.75);
  backdrop-filter: blur(24px) saturate(1.3);
  -webkit-backdrop-filter: blur(24px) saturate(1.3);
  /* Entrance animation, `backwards` like the islands. The whole point of
     reconciling by key is that a row which persists across a repaint never
     re-enters this animation -- see the repaint tests. */
  animation: ls-island-in 520ms cubic-bezier(0.32, 0.72, 0, 1) backwards;
}
.ls-row-tile {
  flex: none; width: 30px; height: 30px; border-radius: 9px;
  display: flex; align-items: center; justify-content: center;
  font-size: 13px; font-weight: 700; color: #fff;
  background: var(--ls-n, #4c9aff);
}
.ls-row-tile svg { width: 17px; height: 17px; fill: none; stroke: #fff; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.ls-row-body { min-width: 0; flex: 1; }
.ls-row-meta {
  display: flex; align-items: baseline; gap: 6px;
  font-size: 11px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase;
  color: rgba(255,255,255,0.45);
}
.ls-row-app { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ls-row-when { margin-left: auto; flex: none; text-transform: none; letter-spacing: 0; font-weight: 500; }
.ls-row-title {
  margin-top: 2px;
  font-size: 14px; font-weight: 600; color: #fff;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.ls-row-sub {
  margin-top: 1px;
  font-size: 13px; line-height: 1.35; color: rgba(255,255,255,0.68);
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden;
}
/* In a unified list the SUBJECT is what the eye lands on after the sender, so
   it is brighter than the preview under it. */
.ls-row-subject { color: rgba(255,255,255,0.88); font-weight: 500; -webkit-line-clamp: 1; }
/* A missed call is the one row whose SOURCE line is the alarming part, so the
   red sits on "Missed call", not on the caller's name. */
.ls-row[data-kind="missed"] .ls-row-app { color: #ff6b6b; }
/* Unread, in the place a phone puts it: a dot on the leading edge of the row.
   It is drawn on the row rather than added as an element so marking something
   read is one attribute, not a DOM change. */
.ls-row[data-unread="1"] { border-left: 3px solid #4c9aff; padding-left: 10px; }

/* APPS. A grid, because these are the only things on the screen the user picks
   rather than reads. */
.ls-apps-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 10px;
  width: 100%; max-width: var(--ls-card-w);
}
.ls-app {
  display: flex; align-items: center; gap: 10px;
  padding: 12px 13px; border-radius: 20px;
  background: rgba(30, 30, 34, 0.92);
  box-shadow: 0 6px 18px -6px rgba(0, 0, 0, 0.75);
  animation: ls-island-in 520ms cubic-bezier(0.32, 0.72, 0, 1) backwards;
}
.ls-app-body { min-width: 0; flex: 1; }
.ls-app-name {
  font-size: 14px; font-weight: 600; color: #fff;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.ls-app-note {
  margin-top: 1px; font-size: 12px; line-height: 1.3; color: rgba(255,255,255,0.6);
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden;
}
/* The badge rides on the tile, the way it does on a home screen. The wrapper
   exists so the badge is a sibling of the mark rather than a child of it --
   the mark's contents are rewritten by the painter. */
.ls-app-tile { position: relative; flex: none; }
.ls-app-badge {
  position: absolute; top: -6px; right: -7px;
  min-width: 17px; height: 17px; padding: 0 4px; box-sizing: border-box;
  border-radius: 999px; background: #ff3b30; color: #fff;
  font-size: 11px; font-weight: 700; line-height: 17px; text-align: center;
  box-shadow: 0 0 0 2px rgba(20,20,22,0.92);
}

/* DECISIONS. The only rows on this screen the user ANSWERS, so they carry
   buttons and the buttons are the widest thing in the card. */
.ls-dec-actions { display: flex; gap: 8px; margin-top: 9px; }
.ls-dec-btn {
  flex: 1; padding: 8px 10px; border: 0; border-radius: 12px;
  font: inherit; font-size: 13px; font-weight: 600; color: #fff;
  background: rgba(255,255,255,0.12);
}
.ls-dec-btn[data-act="approve"] { background: rgba(48,209,88,0.22); color: #6ee787; }
.ls-dec-btn:focus-visible { outline: 3px solid #4c9aff; outline-offset: 2px; }
.ls-dec-done {
  margin-top: 9px; font-size: 13px; font-weight: 600; color: rgba(255,255,255,0.6);
}
.ls-row[data-answered="1"] .ls-dec-actions { display: none; }

/* PROJECTS. The tab that replaced settings, second in the row after the
   agents: this is a projects-focused OS, so what the agents are working ON
   belongs next to the agents themselves. Same row material as everything else
   here, plus the one quantity on this screen. */
.ls-proj-bar {
  margin-top: 8px; height: 4px; border-radius: 999px;
  background: rgba(255,255,255,0.14); overflow: hidden;
}
.ls-proj-fill {
  display: block; height: 100%; width: var(--ls-pct, 0%);
  border-radius: 999px; background: var(--ls-n, #4c9aff);
  /* The width is written by the painter on a node that PERSISTS across a
     repaint, so this animates from where it was rather than from zero. Had the
     rows been rebuilt, every bar would have re-run this from 0% every poll --
     the same flicker as the islands, in a different costume. */
  transition: width 420ms cubic-bezier(0.32, 0.72, 0, 1);
}
.ls-row[data-blocked="1"] .ls-proj-fill { background: #ffb020; }
/* Blocked: work that has stopped and is waiting on a person. It is the reason
   this panel is on a LOCK screen, so it is the one thing in the row that is
   allowed to shout. */
.ls-proj-flag {
  flex: none; padding: 1px 7px; border-radius: 999px;
  background: rgba(255,176,32,0.18); color: #ffb020;
  letter-spacing: 0.04em;
}
@media (prefers-reduced-motion: reduce) {
  .ls-row, .ls-app { animation: none; }
  .ls-proj-fill { transition: none; }
}

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
/* THE AGENT-MODEL SPLIT. The system's own load is the grey head of the bar and
   the agents' share the accent tail, so the two readings visibly add up. */
.ls-stat-fill[data-split="1"] {
  background: linear-gradient(90deg,
    rgba(255,255,255,0.55) 0, rgba(255,255,255,0.55) var(--ls-split, 0%),
    #4c9aff var(--ls-split, 0%), #4c9aff 100%);
}
.ls-stat[data-agent] .ls-stat-fill { background: #4c9aff; }
.ls-stat[data-agent="idle"] .ls-stat-label::after {
  content: "  \\00b7  idle"; color: rgba(255,255,255,0.45);
}
.ls-stat[data-agent="idle"] .ls-stat-value { color: rgba(255,255,255,0.68); }
.ls-spark {
  display: flex; align-items: flex-end; gap: 3px;
  height: 38px; margin-top: -4px;
}
.ls-spark-col {
  flex: 1 1 0; min-width: 0; border-radius: 2px 2px 1px 1px;
  background: rgba(255,255,255,0.22);
  display: flex; align-items: flex-end; overflow: hidden;
  transition: height 420ms cubic-bezier(0.16, 1, 0.3, 1);
}
.ls-spark-part {
  width: 100%; background: rgba(76,154,255,0.85);
  transition: height 420ms cubic-bezier(0.16, 1, 0.3, 1);
}
.ls-spark-col:last-child { background: rgba(255,255,255,0.4); }
.ls-spark-col:last-child .ls-spark-part { background: #4c9aff; }
@media (prefers-reduced-motion: reduce) {
  .ls-spark-col, .ls-spark-part { transition: none; }
}
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
  /* 18px, not 12: Jay, from the glass -- "the alert cards/banners need a little
     space between eachother vertically". A collapsed stack also carries 13px of
     padding-bottom for the cards peeking out behind it, so at 12px a
     single-item alert (which has nothing peeking) sat visually tighter against
     its neighbour than a stack did. */
  display: flex; flex-direction: column; align-items: center; gap: 18px;
  width: 100%; align-self: stretch;
}
/* The pending-decision list at the head of the alerts panel. It had NO rule at
   all, so its .ls-row cards -- which rely on a flex gap like every other list
   on this screen -- stacked flush against each other with nothing between
   them. It is the first thing in the panel, so that was the tightest spot on
   the screen. */
.ls-decisions {
  display: flex; flex-direction: column; align-items: center; gap: 10px;
  width: 100%;
}
.ls-decisions:empty { display: none; }

/* THE VOLUME BEZEL. Right edge, vertical, level with the rocker. */
.ls-vol {
  position: fixed; right: 10px; top: 50%; z-index: 80;
  transform: translate(120%, -50%);
  display: flex; flex-direction: column; align-items: center; gap: 10px;
  padding: 14px 10px; border-radius: 22px;
  background: rgba(24,24,27,0.86);
  backdrop-filter: blur(24px) saturate(1.3);
  -webkit-backdrop-filter: blur(24px) saturate(1.3);
  box-shadow: 0 12px 34px -10px rgba(0,0,0,0.85);
  opacity: 0;
  transition: transform 260ms cubic-bezier(0.32,0.72,0,1), opacity 200ms ease;
  pointer-events: none;     /* a heads-up, never a target */
}
.ls-vol[data-on="1"] { transform: translate(0, -50%); opacity: 1; }
.ls-vol-track {
  width: 8px; height: 150px; border-radius: 999px;
  background: rgba(255,255,255,0.18);
  display: flex; align-items: flex-end; overflow: hidden;
}
.ls-vol-fill {
  display: block; width: 100%; height: var(--ls-vol, 50%);
  border-radius: 999px; background: #fff;
  transition: height 140ms ease;
}
.ls-vol-num {
  font-size: 12px; font-weight: 600; color: rgba(255,255,255,0.8);
  font-variant-numeric: tabular-nums;
}
.ls-vol-note {
  max-width: 76px; font-size: 10px; line-height: 1.25; text-align: center;
  color: rgba(255,176,32,0.92);
}

/* THE AGENT CAROUSEL -- RADIAL, pivoting on the volume rocker.
 *
 * Jay: "left edge thumb pivot around the button". So the faces sit on an ARC
 * swept from the left edge at the rocker's height, not in a vertical strip.
 * The thumb stays on the button and the agents come to it, which is the whole
 * point of pivoting there rather than centring the arc on the screen.
 *
 * --ls-car-pivot is where the rocker is, as a share of screen height. 34% is a
 * STARTING GUESS, not a measurement -- unlike the camera cutout, there is no
 * vendor file that gives the button's position, so this is the one number here
 * that wants a human to look at it. It is a single custom property so nudging
 * it is a one-line change.
 */
:root { --ls-car-pivot: 34%; --ls-car-radius: 104px; }
.ls-carousel {
  position: fixed; left: 0; top: 0; bottom: 0; right: 0; z-index: 80;
  opacity: 0; pointer-events: none;
  transition: opacity 200ms ease;
}
.ls-carousel[data-on="1"] { opacity: 1; }
/* THE SCREEN BEHIND THE ARC IS BLURRED. Jay asked for it, and it earns its
   place: the faces are small, low-contrast circles sitting over a feed of cards
   and text, and without separation the focused one is genuinely hard to pick
   out at a glance -- which is the one thing a chooser driven by a physical key
   has to get right, because your eye is not already on the screen.
 *
 * Applied to .lockscreen, which is a SIBLING of the carousel and the scrim, so
 * neither the arc nor the dim gets blurred with it. The existing sheets blur
 * their chrome piecemeal (.ls-head, .ls-statusbar, .ls-weather) because a sheet
 * only covers the bottom; this covers the middle of the screen, so the whole
 * surface goes.
 *
 * NOT applied to the volume bezel, deliberately: that is a transient heads-up
 * for a key you are already holding, and blurring the entire screen to show a
 * volume level would be heavy-handed for it. */
.lockscreen[data-radial="1"] {
  filter: blur(7px);
  transition: filter 260ms ease;
}
/* Summoned onto a DARK panel: the lock screen is not blurred, it is gone. On
   OLED an unlit pixel emits nothing, so the faces sit on real black rather than
   on a dimmed photograph of a lock screen -- which is the effect Jay was after
   and the one thing an OLED does that no amount of blur imitates.
   visibility rather than display:none, so nothing reflows on the way in. */
.lockscreen[data-radial="dark"] {
  visibility: hidden;
  transition: none;
}
.lockscreen[data-radial="dark"] ~ .ls-scrim { background: #000; opacity: 1; }

/* BLANKED: what the panel holds in its scanout buffer while it is off.
 *
 * No transition on the way IN -- the panel is about to go down and there is no
 * time for one; the point is that the last painted frame is black. Coming back
 * it fades, so an ordinary wake rises out of black rather than snapping on,
 * which is both nicer and the same motion the arc uses.
 *
 * opacity, not visibility: it animates, and an OLED showing opacity 0 over a
 * black page body is emitting nothing anyway. */
.lockscreen[data-blanked="1"] {
  opacity: 0;
  transition: none;
}
/* SCREEN OFF / ON, ON TRUE BLACK. Jay: "close into the centre and expand out
 * from the centre animation to play on the true black of the oled screen? a
 * quick 0.5 second animation". Off: the screen squashes to a line through the
 * middle, then to a point, and is gone. On: the reverse. The body is already
 * black (ls-black) for both, and the lock screen carries the body's own grey
 * gradient while it moves, so the shrinking rectangle is the whole picture
 * and not a transparent cut-out. Transform and opacity only: this is a
 * full-screen layer on a phone GPU.
 *
 * taos-kiosk-screen waits for this before powering the output down -- the
 * page cannot paint once the panel is off -- so its last frame is black. */
.lockscreen[data-closing="1"], .lockscreen[data-opening="1"] {
  transform-origin: 50% 50%;
  background: linear-gradient(160deg, #141415 0%, #1a1a1d 45%, #202024 100%);
  will-change: transform, opacity;
}
.lockscreen[data-closing="1"] { animation: ls-tv-off 0.5s cubic-bezier(.55, 0, .8, .2) forwards; }
.lockscreen[data-opening="1"] { animation: ls-tv-on 0.5s cubic-bezier(.2, .8, .2, 1) both; }
@keyframes ls-tv-off {
  0%   { transform: scale(1, 1);       opacity: 1; }
  60%  { transform: scale(1, 0.006);   opacity: 1; }
  100% { transform: scale(0, 0.006);   opacity: 0; }
}
@keyframes ls-tv-on {
  0%   { transform: scale(0, 0.006);   opacity: 0; }
  40%  { transform: scale(1, 0.006);   opacity: 1; }
  100% { transform: scale(1, 1);       opacity: 1; }
}
@keyframes ls-tv-fade-out { to { opacity: 0; } }
@keyframes ls-tv-fade-in { from { opacity: 0; } }
@media (prefers-reduced-motion: reduce) {
  .lockscreen[data-closing="1"] { animation: ls-tv-fade-out 0.5s forwards; }
  .lockscreen[data-opening="1"] { animation: ls-tv-fade-in 0.5s both; }
}
/* THE BODY GOES BLACK TOO, and this is the bit I kept missing.
 *
 * `body` carries a dark GREY GRADIENT (#141415 -> #202024) for the ordinary
 * sign-in card, and .lockscreen has no background of its own. So hiding the
 * lock screen revealed that gradient, which is exactly what Jay reported twice:
 * "it shows the lock screen background grey", and "it even flashes sometimes on
 * rotary start/open" -- the flash being the frame where the lock screen was
 * hidden and the black scrim had not painted yet.
 *
 * Hiding a transparent layer over grey shows grey. The layer underneath has to
 * be black, so it is, in the same style recalculation -- there is no frame in
 * between for the gradient to appear in.
 *
 * Specificity does the work: body.ls-black (0,1,1) beats body (0,0,1), so no
 * !important is needed. */
body.ls-black { background: #000; }
.lockscreen {
  transition: opacity 320ms ease;
}
@media (prefers-reduced-motion: reduce) {
  .lockscreen { transition: none; }
}

/* EMERGING FROM THE BLACK. Jay: "it would be nice if the the rotary menu could
 * have an appear effect like fading into view out of the deep black oled
 * display."
 *
 * Slower and softer than the lit-screen case on purpose. Over a blurred lock
 * screen the arc only has to arrive; over true black it is the ONLY thing on
 * the panel, so the eye follows it completely and a 200ms snap reads as a
 * flash. This gives it time to resolve out of nothing.
 *
 * The scale grows from the PIVOT, not the centre, so it unfurls from under the
 * thumb rather than swelling out of the middle of a dark screen -- the pivot is
 * the whole conceit of this layout and the animation should say so.
 *
 * Opacity is eased out of zero slowly at first (the cubic starts shallow):
 * on OLED the first few percent of brightness off true black is the most
 * visible step there is, and a linear fade shows a hard edge appearing. */
.lockscreen[data-radial="dark"] ~ #ls-carousel {
  transform-origin: 0 var(--ls-car-pivot);
  transform: scale(0.9);
  transition: opacity 520ms cubic-bezier(0.4, 0, 0.2, 1),
              transform 620ms cubic-bezier(0.22, 1, 0.36, 1);
}
.lockscreen[data-radial="dark"] ~ #ls-carousel[data-on="1"] {
  transform: scale(1);
}
/* The faces arrive just behind the ring they sit on, so the arc reads as a
   thing that appeared and then filled, rather than everything at once. */
.lockscreen[data-radial="dark"] ~ #ls-carousel .ls-face {
  transition: transform 420ms cubic-bezier(0.32,0.72,0,1),
              opacity 480ms ease 90ms,
              box-shadow 180ms ease;
}
/* The banner last. It is text, and text arriving first on a black screen is
   what makes an animation feel like a page load. */
.lockscreen[data-radial="dark"] ~ #ls-carousel .ls-carousel-banner {
  transition: opacity 420ms ease 180ms;
}
.lockscreen[data-radial="dark"] ~ #ls-carousel:not([data-on="1"]) .ls-carousel-banner {
  opacity: 0;
}
@media (prefers-reduced-motion: reduce) {
  .lockscreen[data-radial="dark"] ~ #ls-carousel,
  .lockscreen[data-radial="dark"] ~ #ls-carousel[data-on="1"] {
    transform: none; transition: opacity 200ms ease;
  }
  .lockscreen[data-radial="dark"] ~ #ls-carousel .ls-face,
  .lockscreen[data-radial="dark"] ~ #ls-carousel .ls-carousel-banner {
    transition: none;
  }
}
@media (prefers-reduced-motion: reduce) {
  .lockscreen[data-radial="1"] { transition: none; }
}
/* The pivot itself: a zero-size origin on the left edge at the rocker's
   height. Every face is placed relative to THIS, so moving the pivot moves the
   whole arc and nothing else needs to know. */
.ls-carousel-strip {
  position: absolute; left: 0; top: var(--ls-car-pivot);
  width: 0; height: 0;
}
/* Each face rides the arc. The double rotation is what keeps a face UPRIGHT
   while sitting on a curve: rotate to its angle, push out along the radius,
   then rotate back by the same amount. Without the second rotation the avatars
   tilt, which on a ring of faces reads as a rendering fault rather than style. */
.ls-face {
  position: absolute; left: 0; top: 0;
  width: 46px; height: 46px; margin: -23px;
  border-radius: 50%;
  background: rgba(255,255,255,0.1) center/cover no-repeat;
  display: flex; align-items: center; justify-content: center;
  font-size: 14px; font-weight: 700; color: rgba(255,255,255,0.7);
  transform:
    rotate(var(--a, 0deg))
    translateX(var(--ls-car-radius))
    rotate(calc(-1 * var(--a, 0deg)))
    scale(var(--s, 0.82));
  opacity: var(--o, 0.35);
  transition: transform 300ms cubic-bezier(0.32,0.72,0,1), opacity 220ms ease,
              box-shadow 180ms ease;
}
.ls-face[data-focus="1"] {
  box-shadow: 0 0 0 2px rgba(255,255,255,0.9), 0 8px 22px -6px rgba(0,0,0,0.8);
  color: #fff;
}
/* A faint arc behind the faces, so the ring reads as one object rather than
   scattered dots. Drawn as a ring clipped to the pivot side. */
.ls-carousel-arc {
  position: absolute; left: 0; top: var(--ls-car-pivot);
  width: calc(var(--ls-car-radius) * 2); height: calc(var(--ls-car-radius) * 2);
  margin: calc(var(--ls-car-radius) * -1);
  border-radius: 50%;
  border: 1px solid rgba(255,255,255,0.1);
  pointer-events: none;
}
/* The banner sits OUTSIDE the arc, level with the pivot, so the name is beside
   the focused face rather than under the thumb. */
.ls-carousel-banner {
  position: absolute; top: var(--ls-car-pivot);
  left: calc(var(--ls-car-radius) + 46px);
  transform: translateY(-50%);
  max-width: 200px;
  padding: 12px 15px; border-radius: 20px;
  background: rgba(24,24,27,0.9);
  backdrop-filter: blur(26px) saturate(1.3);
  -webkit-backdrop-filter: blur(26px) saturate(1.3);
  box-shadow: 0 14px 40px -12px rgba(0,0,0,0.85);
}
.ls-carousel-name { font-size: 16px; font-weight: 700; color: #fff; }
.ls-carousel-role {
  margin-top: 1px; font-size: 11px; color: rgba(255,255,255,0.62);
  text-transform: uppercase; letter-spacing: 0.05em;
}
.ls-carousel-ptt { margin-top: 8px; font-size: 12px; color: rgba(255,255,255,0.45); }
.ls-carousel[data-talking="1"] .ls-carousel-ptt { color: #6ee787; font-weight: 600; }
.ls-carousel[data-talking="1"] .ls-face[data-focus="1"] {
  box-shadow: 0 0 0 3px #30d158;
  animation: ls-ptt 1.1s ease-in-out infinite;
}
@keyframes ls-ptt {
  0%, 100% { box-shadow: 0 0 0 3px #30d158; }
  50%      { box-shadow: 0 0 0 8px rgba(48,209,88,0.32); }
}
@media (prefers-reduced-motion: reduce) {
  .ls-vol, .ls-carousel, .ls-face, .ls-vol-fill { transition: none; }
  .ls-carousel[data-talking="1"] .ls-face[data-focus="1"] { animation: none; }
}

/* TORCH AND CAMERA, flanking the unlock bar.
 *
 * Round, dim, and the same size as each other: they are landmarks found by
 * position rather than read, which is why they sit at the edges with the bar
 * between them. Big targets because they are pressed with a thumb, often in
 * the dark -- the torch especially, which is the one control on this screen
 * someone reaches for precisely when they cannot see. */
.ls-unlock-row {
  display: flex; align-items: center; justify-content: center; gap: 14px;
  width: 100%; max-width: var(--ls-card-w); margin: 0 auto;
}
.ls-unlock-row .ls-unlock-btn { flex: 1; min-width: 0; }
.ls-quick {
  flex: none; width: 46px; height: 46px; padding: 0;
  border: 0; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  background: rgba(255,255,255,0.12); color: rgba(255,255,255,0.82);
  transition: background 200ms ease, color 200ms ease, transform 140ms ease;
}
.ls-quick svg {
  width: 21px; height: 21px; fill: none; stroke: currentColor;
  stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round;
}
.ls-quick:active { transform: scale(0.92); }
.ls-quick:focus-visible { outline: 3px solid #4c9aff; outline-offset: 3px; }
/* Lit: the torch inverts, the way it does on every phone, so its state is
   unmistakable from the corner of the eye in a dark room. */
.ls-quick[aria-pressed="true"] { background: #fff; color: #111; }
/* Unavailable rather than hidden. A missing control is a thing the user hunts
   for; a dimmed one answers the question. */
.ls-quick[disabled] { opacity: 0.38; }
.ls-quick[data-note]::after {
  content: attr(data-note);
  position: absolute; bottom: 54px; left: 50%; transform: translateX(-50%);
  white-space: nowrap; padding: 6px 10px; border-radius: 10px;
  background: rgba(24,24,27,0.94); color: rgba(255,255,255,0.8);
  font-size: 11px; font-weight: 500;
}
.ls-quick { position: relative; }

/* THE PULL-DOWN SHADE, from the TOP edge -- the one surface on this screen that
   does not come from the bottom, because that is where the gesture starts. It
   deliberately does NOT cover the whole screen: a shade that fills the display
   for one slider reads as a mode you have to escape, and the clock staying
   visible behind it is what makes it feel like a shade rather than a page. */
.ls-shade {
  position: fixed; top: 0; left: 0; right: 0; z-index: 70;
  padding: calc(env(safe-area-inset-top, 0px) + 8px) 12px 14px;
  transform: translateY(-101%);
  transition: transform 340ms cubic-bezier(0.32, 0.72, 0, 1);
}
.ls-shade[hidden] { display: none; }
.lockscreen[data-sheet="shade"] ~ #ls-shade { transform: translateY(0); }
.ls-shade-inner {
  position: relative;
  margin: 0 auto; width: 100%; max-width: var(--ls-card-w);
  padding: 16px 16px 20px;
  border-radius: 0 0 26px 26px;
  background: rgba(24, 24, 27, 0.9);
  box-shadow: 0 20px 50px -14px rgba(0, 0, 0, 0.9);
  backdrop-filter: blur(30px) saturate(1.3);
  -webkit-backdrop-filter: blur(30px) saturate(1.3);
}
.ls-shade-toggles {
  display: flex; gap: 10px; padding-bottom: 14px;
}
.ls-toggle {
  flex: 1; display: flex; flex-direction: column; align-items: center; gap: 6px;
  padding: 12px 6px; border: 0; border-radius: 20px;
  font: inherit; font-size: 11px; font-weight: 600;
  background: rgba(255,255,255,0.1); color: rgba(255,255,255,0.6);
  transition: background 200ms ease, color 200ms ease;
}
/* ON is a filled tile, not a tick: at a glance across a room the FILL is what
   reads, and these are glanced at rather than studied. */
.ls-toggle[aria-pressed="true"] { background: #4c9aff; color: #fff; }
.ls-toggle:focus-visible { outline: 3px solid #4c9aff; outline-offset: 2px; }
.ls-toggle svg { width: 22px; height: 22px; fill: none; stroke: currentColor;
                 stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
/* Mid-flight. A radio takes a moment to come up, and a switch that snapped back
   to its old position while waiting would read as having refused the tap. */
.ls-toggle[data-busy="1"] { opacity: 0.55; }
.ls-shade-row { display: flex; align-items: center; gap: 12px; }
.ls-shade-icon {
  flex: none; width: 22px; height: 22px;
  fill: none; stroke: rgba(255,255,255,0.8);
  stroke-width: 1.7; stroke-linecap: round;
}
.ls-shade-value {
  flex: none; min-width: 42px; text-align: right;
  font-size: 14px; font-weight: 600; color: rgba(255,255,255,0.75);
  font-variant-numeric: tabular-nums;
}
/* A tall track and a big thumb: this is dragged with a thumb in the dark, and
   it is the control someone reaches for precisely when they cannot see well. */
.ls-shade-slider {
  flex: 1; min-width: 0; height: 34px; margin: 0;
  -webkit-appearance: none; appearance: none; background: none;
}
.ls-shade-slider::-webkit-slider-runnable-track {
  height: 10px; border-radius: 999px; background: rgba(255,255,255,0.18);
}
.ls-shade-slider::-webkit-slider-thumb {
  -webkit-appearance: none; appearance: none;
  width: 26px; height: 26px; margin-top: -8px;
  border-radius: 50%; background: #fff;
  box-shadow: 0 2px 8px -1px rgba(0,0,0,0.6);
}
.ls-shade-slider:focus-visible { outline: 3px solid #4c9aff; outline-offset: 4px; border-radius: 999px; }
.ls-shade-note {
  margin-top: 10px; font-size: 12px; line-height: 1.35;
  color: rgba(255,176,32,0.9);
}
/* The grip, echoing the unlock grabber at the other end of the screen so the
   two read as the same vocabulary. */
.ls-shade-grip {
  position: absolute; left: 50%; bottom: 7px; transform: translateX(-50%);
  width: 38px; height: 4px; border-radius: 999px;
  background: rgba(255,255,255,0.28);
}
@media (prefers-reduced-motion: reduce) {
  .ls-shade { transition: none; }
}

/* THE POWER MENU. Jay: "I rather the power button menu be buttons centred on
   the screen against blurred background like iOS."
 *
 * So it is NOT a bottom sheet. It is a centred dialog that scales up out of the
 * blur -- the same shape iOS uses for an alert, and the right one here: this is
 * a modal question with four answers, not a drawer of content you might browse.
 * Centring also puts the targets under the thumb from either hand, which a
 * bottom sheet does not once it is five rows tall.
 *
 * The backdrop blur is already there: `.lockscreen:not([data-sheet="none"])`
 * blurs the chrome and `.ls-scrim` darkens behind it, both driven by the same
 * data-sheet attribute this rides on. */
.ls-modal {
  position: fixed; inset: 0; z-index: 60;
  display: flex; align-items: center; justify-content: center;
  padding: 24px;
  /* Not shown until the attribute says so. pointer-events:none while hidden so
     the invisible full-screen box cannot swallow a touch meant for the page --
     a modal that is closed but still eating input is indistinguishable from a
     frozen screen. */
  opacity: 0; pointer-events: none;
  transform: scale(0.92);
  transition: opacity 200ms ease, transform 260ms cubic-bezier(0.32, 0.72, 0, 1);
}
.ls-modal[hidden] { display: none; }
/* Set for the duration of a close that happens while the panel is powering
   down. Nothing is compositing then, so an animated close has nowhere to run
   and would replay on wake -- the user sees the menu close half a second after
   the screen comes back, which reads as the phone catching up with itself. */
.lockscreen[data-instant="1"] ~ .ls-modal,
.lockscreen[data-instant="1"] ~ .ls-shade,
.lockscreen[data-instant="1"] ~ .ls-sheet,
/* The volume surfaces too. They are siblings of .lockscreen, so hiding the
   lock screen never touched them -- a panel blanking mid-fade kept a half-lit
   arc in its buffer and showed it on the next wake. */
.lockscreen[data-instant="1"] ~ #ls-vol,
.lockscreen[data-instant="1"] ~ #ls-carousel,
.lockscreen[data-instant="1"] ~ #ls-carousel .ls-face,
.lockscreen[data-instant="1"] ~ #ls-carousel .ls-carousel-banner { transition: none; }
.lockscreen[data-sheet="power"] ~ #ls-power {
  opacity: 1; pointer-events: auto; transform: scale(1);
}
.ls-modal-card {
  width: 100%; max-width: var(--ls-card-w);
  display: flex; flex-direction: column; gap: 8px;
  padding: 20px 16px 14px;
  border-radius: 28px;
  background: rgba(28, 28, 30, 0.78);
  box-shadow: 0 24px 60px -12px rgba(0, 0, 0, 0.9);
  backdrop-filter: blur(34px) saturate(1.35);
  -webkit-backdrop-filter: blur(34px) saturate(1.35);
}
.ls-modal-title {
  text-align: center; font-size: 19px; font-weight: 700; color: #fff;
}
.ls-modal-sub {
  text-align: center; margin-top: -4px; padding-bottom: 4px;
  font-size: 12px; color: rgba(255,255,255,0.5);
}
/* Cancel, set apart from the actions above it. iOS puts the safe choice last
   and makes it the plainest thing on the card; the dangerous ones should never
   be what the thumb finds by default. */
.ls-modal-cancel {
  margin-top: 4px; padding: 13px; border: 0; border-radius: 16px;
  font: inherit; font-size: 16px; font-weight: 600;
  background: rgba(255,255,255,0.14); color: #fff;
}
.ls-modal-cancel:focus-visible { outline: 3px solid #4c9aff; outline-offset: 2px; }
@media (prefers-reduced-motion: reduce) {
  .ls-modal { transition: none; transform: none; }
  .lockscreen[data-sheet="power"] ~ #ls-power { transform: none; }
}

/* The action rows. Big targets: this is reached by feel, often in the dark,
   sometimes in a hurry, and it is the one surface here where picking the wrong
   row costs something. */
.ls-power-body { display: flex; flex-direction: column; gap: 8px; padding: 2px 0 4px; }
.ls-power-item {
  display: flex; align-items: center; gap: 13px;
  width: 100%; padding: 14px 15px; border: 0; border-radius: 18px;
  font: inherit; font-size: 16px; font-weight: 600; text-align: left;
  color: #fff; background: rgba(255,255,255,0.09);
}
.ls-power-item:focus-visible { outline: 3px solid #4c9aff; outline-offset: 2px; }
.ls-power-item[data-danger="1"] { color: #ff6b6b; }
/* Emergency is not "destructive", it is URGENT: the whole row carries the
   colour rather than just the label, so it is findable without reading. */
.ls-power-item[data-emergency="1"] {
  background: rgba(255,59,48,0.22); color: #ff8a80;
}
.ls-power-item[data-emergency="1"] .ls-power-glyph { background: rgba(255,59,48,0.28); }
.ls-power-glyph {
  flex: none; width: 30px; height: 30px; border-radius: 9px;
  display: flex; align-items: center; justify-content: center;
  font-size: 15px; background: rgba(255,255,255,0.10);
}
.ls-power-note {
  display: block; margin-top: 2px;
  font-size: 12px; font-weight: 500; color: rgba(255,255,255,0.55);
}
/* The confirm step for the two Jay asked to guard. It REPLACES the row rather
   than opening a second dialog: a nested modal on a lock screen is a place to
   get lost, and the question should sit where the answer was given. */
.ls-power-confirm {
  display: flex; flex-direction: column; gap: 8px;
  padding: 13px 15px; border-radius: 18px;
  background: rgba(255,59,48,0.14);
}
.ls-power-confirm-q { font-size: 14px; font-weight: 600; color: #fff; }
.ls-power-confirm-note { font-size: 12px; line-height: 1.35; color: rgba(255,255,255,0.68); }
.ls-power-confirm-row { display: flex; gap: 8px; margin-top: 2px; }
.ls-power-confirm-row button {
  flex: 1; padding: 10px; border: 0; border-radius: 12px;
  font: inherit; font-size: 14px; font-weight: 600;
  background: rgba(255,255,255,0.12); color: #fff;
}
.ls-power-confirm-row button[data-go="1"] { background: rgba(255,59,48,0.34); color: #ffb3ad; }
.ls-power-result {
  padding: 11px 15px; border-radius: 14px;
  background: rgba(255,255,255,0.08);
  font-size: 13px; line-height: 1.4; color: rgba(255,255,255,0.78);
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
  position: relative;
  font-size: 11.5px; color: rgba(255,255,255,0.52);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
/* A demo agent's task rotation (see _DEMO_TASK_SCRIPTS): a completion beat
   ("✓ ...") reads as just-finished rather than as the ongoing task, so
   it gets the same live green as the busy pip instead of the resting grey. */
.ls-island[data-done="1"] .ls-status { color: #3ddc84; }
/* The status-change slide: the OLD text is a `::before` ghost carrying
   `data-prev` (set immediately before the real text swaps, see
   animateStatusChange), sliding up and out while the real node -- already
   holding the NEW text -- slides in from below. Both on the same DOM node:
   the island is never rebuilt for a status change. */
.ls-status[data-prev]::before {
  content: attr(data-prev);
  position: absolute; inset: 0;
  color: inherit; background: inherit; pointer-events: none;
  animation: ls-status-out 380ms cubic-bezier(0.16, 1, 0.3, 1) forwards;
}
.ls-status.ls-status-slide {
  animation: ls-status-in 380ms cubic-bezier(0.16, 1, 0.3, 1) backwards;
}
@keyframes ls-status-out {
  from { transform: translateY(0); opacity: 1; }
  to   { transform: translateY(-100%); opacity: 0; }
}
@keyframes ls-status-in {
  from { transform: translateY(100%); opacity: 0; }
  to   { transform: translateY(0); opacity: 1; }
}
/* prefers-reduced-motion: a crossfade instead of a slide -- the ghost still
   fades out and the real text still fades in, just with no motion. */
.ls-status.ls-status-crossfade::before { animation: ls-status-out-fade 380ms ease forwards; }
.ls-status.ls-status-crossfade { animation: ls-status-in-fade 380ms ease backwards; }
@keyframes ls-status-out-fade { from { opacity: 1; } to { opacity: 0; } }
@keyframes ls-status-in-fade { from { opacity: 0; } to { opacity: 1; } }
/* A brief soft highlight on the whole island in the agent's own hue, so a
   task rotation reads as "this one, right now" even from across a room. */
.ls-island[data-status-pulse="1"] { animation: ls-status-pulse 700ms ease-out; }
@keyframes ls-status-pulse {
  0%   { box-shadow: 0 6px 18px -6px rgba(0,0,0,0.75), 0 0 0 0 transparent; }
  45%  { box-shadow: 0 6px 18px -6px rgba(0,0,0,0.75), 0 0 22px 3px var(--ls-a, #4c9aff); }
  100% { box-shadow: 0 6px 18px -6px rgba(0,0,0,0.75), 0 0 0 0 transparent; }
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
  .ls-status.ls-status-slide,
  .ls-status.ls-status-slide::before,
  .ls-island[data-status-pulse="1"] { animation: none; }
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
/* ⚠ EVERY sheet needs a line here. `.ls-sheet` rests at translateY(101%) and
   only the names listed are pulled up, while the backdrop blur is driven by the
   generic `:not([data-sheet="none"])` rules. So a sheet that is opened but not
   named here produces EXACTLY what Jay saw: "Power button blurs screen but no
   buttons show" -- the chrome reacts, the sheet stays off-screen, and nothing
   errors. Same shape as the panels that painted 354 rows while `hidden`: a new
   element added to a system whose visibility is a hand-written list of names. */
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
  content: "Say something\\2026"; color: rgba(255,255,255,0.32);
}
.ls-voice-acts { display: flex; gap: 10px; padding-top: 2px; }
/* The dictation dialog is a MODAL now, not a sheet, so it scales up out of the
   blur like the power menu instead of sliding from the bottom edge. Its reveal
   rule has to live beside the other modal one, or it opens invisibly -- the
   failure the sheet-name test exists to catch. */
.lockscreen[data-sheet="voice"] ~ #ls-voice {
  opacity: 1; pointer-events: auto; transform: scale(1);
}
.ls-modal-voice .ls-voice-head {
  display: flex; align-items: center; gap: 11px; padding-bottom: 4px;
}
.ls-modal-voice .ls-voice-who { min-width: 0; flex: 1; text-align: left; }
.ls-modal-voice .ls-sheet-close { flex: none; }
.ls-modal-voice .ls-voice-acts { padding-top: 4px; }

/* THE CHARGE OVERLAY. Product owner: "a charger connected notification,
 * screen on and animation for 3 seconds [later: "make the charge animation 10
 * seconds"] ... the charge screen emulates a
 * claude code like cli". A homage to a coding-agent terminal, NOT a brand: no
 * names, no marks, just the grammar -- a prompt, a spinner, a working slogan
 * and a count.
 *
 * Centred in the LOWER half (padding-top: 50%), so the clock above stays
 * readable when it plays over a lit lock screen. Over a dark panel the layer is
 * pure black instead of a dim, which is what the arc does from standby: on
 * OLED the terminal is then the only thing emitting light. */
.ls-charge {
  position: fixed; inset: 0; z-index: 90;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  padding: 50dvh 16px env(safe-area-inset-bottom, 0px);
  box-sizing: border-box;
  background: linear-gradient(to bottom, rgba(0,0,0,0) 25%, rgba(0,0,0,0.62) 70%);
  opacity: 0; pointer-events: none;
  transition: opacity 420ms cubic-bezier(0.4, 0, 0.2, 1);
}
.ls-charge[hidden] { display: none; }
.ls-charge[data-on="1"] { opacity: 1; pointer-events: auto; }
.ls-charge[data-dark="1"] { background: #000; }
.ls-charge-term {
  width: 100%; max-width: var(--ls-card-w, 420px); box-sizing: border-box;
  padding: 15px 18px 16px;
  background: #0c0c0e;
  border: 1px solid rgba(255,255,255,0.10);
  border-radius: 16px;
  box-shadow: 0 22px 60px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,255,255,0.04);
  font-family: ui-monospace, "SF Mono", "JetBrains Mono", "DejaVu Sans Mono",
               "Liberation Mono", Menlo, Consolas, monospace;
  font-size: 15px; line-height: 1.75; letter-spacing: 0;
  color: rgba(255,255,255,0.92);
  font-variant-numeric: tabular-nums;
  transform: translateY(12px) scale(0.985);
  transition: transform 560ms cubic-bezier(0.16, 1, 0.3, 1);
}
.ls-charge[data-on="1"] .ls-charge-term { transform: none; }
.ls-charge-line { display: flex; align-items: baseline; gap: 0.6ch; white-space: nowrap; min-width: 0; }
.ls-charge-prompt { color: rgba(255,255,255,0.45); }
.ls-charge-caret { color: #4c9aff; }
.ls-charge-dim { color: rgba(255,255,255,0.45); flex: none; }
.ls-charge-spin { color: #ff9e4a; flex: none; width: 1.2ch; text-align: center; }
/* The slogan shimmer: a lighter band swept through amber text. The text is the
   gradient's clip, so the band only exists where there are letters. */
.ls-charge-slogan {
  min-width: 0; overflow: hidden; text-overflow: ellipsis;
  color: #ff9e4a;
  background: linear-gradient(100deg, #ff9e4a 0%, #ff9e4a 38%, #ffe2c2 50%,
                              #ff9e4a 62%, #ff9e4a 100%);
  background-size: 260% 100%;
  -webkit-background-clip: text; background-clip: text;
  -webkit-text-fill-color: transparent;
  animation: ls-charge-shimmer 1.7s linear infinite;
}
@keyframes ls-charge-shimmer {
  from { background-position: 100% 0; }
  to   { background-position: 0% 0; }
}
.ls-charge-pct { color: rgba(255,255,255,0.92); flex: none; min-width: 6ch; }
.ls-charge-bar { letter-spacing: -0.02em; flex: none; }
.ls-charge-fill { color: #3ddc84; }
.ls-charge-rest { color: rgba(255,255,255,0.18); }
/* The last beat. Space reserved from the start, so the card does not grow a
   line under the thumb just before it goes. */
.ls-charge-done { color: #3ddc84; opacity: 0; transition: opacity 220ms ease; }
.ls-charge[data-done="1"] .ls-charge-done { opacity: 1; }
@media (prefers-reduced-motion: reduce) {
  .ls-charge, .ls-charge-term, .ls-charge-done { transition: none; }
  .ls-charge-term { transform: none; }
  .ls-charge-slogan { animation: none; background: none; -webkit-text-fill-color: #ff9e4a; }
}

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
/* THE LIVE ZONE: an incoming call (demo). Jay: "not a siri type orb but
   stylised rectangle widget type design that appears when needed adaptive to
   its content". So it is ONE card, in the same material and column as the
   islands, that exists only while there is a call and changes SHAPE with what
   it holds: compact while ringing, tall while the PA talks, a one-line pill as
   it ends.

   It takes the feed's place rather than sitting over it. The feed is hidden
   with its OWN attribute (data-call), never with `hidden`, `data-off` or
   `data-hidden` -- those three already have owners (the poll, the switcher and
   the collapse), and borrowing any of them would hand the feed back in the
   wrong state when the call ends. */
.ls-feed[data-call="1"] { display: none; }
.ls-feed[data-call="return"] { opacity: 0; transition: none; }
.ls-head[data-call] .ls-views { opacity: 0.28; pointer-events: none; transition: opacity 280ms ease; }

.ls-call {
  --ls-call-r: 28px;
  position: relative; flex: none;
  width: 100%; max-width: var(--ls-card-w);
  margin-top: 6px;
  padding: 1.5px;                    /* the edge the glow shows through */
  border-radius: var(--ls-call-r);
  overflow: hidden; isolation: isolate;
  background: rgba(255,255,255,0.07);
  box-shadow: 0 18px 40px -18px rgba(0,0,0,0.9), 0 2px 10px -4px rgba(0,0,0,0.6);
  transition: width 480ms cubic-bezier(0.34, 1.2, 0.5, 1),
              opacity 220ms ease;
  -webkit-tap-highlight-color: transparent;
  touch-action: manipulation;
}
.ls-call[hidden] { display: none; }
/* Laid out but unseen, so its size can be measured before it is revealed. */
.ls-call[data-phase="measure"] { position: absolute; visibility: hidden; transition: none; }
.ls-call[data-phase="grow"] { opacity: 0; transition: none; }
/* Its first size is a starting point, not a change: nothing to animate from. */
.ls-call[data-phase="measure"] .ls-call-card,
.ls-call[data-phase="grow"] .ls-call-card { transition: none; }
/* A ring the script moved focus to is for keyboard users; on the glass, where
   the finger is the pointer, it would read as a stray outline. */
.ls-call[data-kbd="0"] :focus-visible,
.ls-call[data-kbd="0"] :focus-visible .ls-call-dot { outline: none; }
.ls-call[data-phase="leave"] { opacity: 0; transition: opacity 120ms ease; }

/* THE EDGE. One conic gradient per voice, spun by the compositor (a transform
   animation, no per-frame paint) and faded by the script from the speech
   envelope -- opacity only. Square and oversized so the sweep covers the card
   at every height it morphs through. */
.ls-call-glow { position: absolute; inset: 0; z-index: 0; pointer-events: none; }
.ls-call-ring {
  position: absolute; left: 50%; top: 50%;
  width: 900px; height: 900px; margin: -450px 0 0 -450px;
  opacity: 0; will-change: transform, opacity;
  animation: ls-call-spin 5.2s linear infinite;
}
.ls-call-ring[data-tone="caller"] {
  background: conic-gradient(from 0deg,
    rgba(255,122,89,0) 0deg, #ff7a59 50deg, #ffb020 95deg, rgba(255,176,32,0) 150deg,
    rgba(255,79,139,0) 185deg, #ff4f8b 245deg, #ff7a59 300deg, rgba(255,122,89,0) 360deg);
}
.ls-call-ring[data-tone="pa"] {
  background: conic-gradient(from 90deg,
    rgba(76,154,255,0) 0deg, #4c9aff 55deg, #8b5cf6 110deg, rgba(139,92,246,0) 160deg,
    rgba(61,214,255,0) 190deg, #3dd6ff 250deg, #4c9aff 305deg, rgba(76,154,255,0) 360deg);
  animation-duration: 6.4s; animation-direction: reverse;
}
/* ONE spin rate for every state. It used to be faster while ringing, but a
   running animation's position is its elapsed time over its duration, so
   changing the duration at ringing -> PA snapped the sweep to a new angle in a
   single frame. Ringing reads through the pulse instead. */
@keyframes ls-call-spin { to { transform: rotate(360deg); } }

/* THE BODY. Solid rather than backdrop-blurred: the glow behind it moves every
   frame, and a backdrop filter over a moving backdrop is a full blur per frame
   on a phone GPU. The height is set by the script from the measured content,
   so the card MORPHS between states instead of snapping. */
.ls-call-card {
  position: relative; z-index: 1;
  border-radius: calc(var(--ls-call-r) - 1.5px);
  background: rgba(22, 22, 27, 0.955);
  overflow: hidden;
  transition: height 480ms cubic-bezier(0.34, 1.2, 0.5, 1);
}
/* The ambient wash: each voice's colour pooled at the top of the card. */
.ls-call-wash {
  position: absolute; inset: 0; pointer-events: none; opacity: 0;
  will-change: opacity;
}
.ls-call-wash[data-tone="caller"] {
  background: radial-gradient(120% 90px at 50% 0%, rgba(255,122,89,0.26), rgba(255,79,139,0.08) 60%, transparent 100%);
}
.ls-call-wash[data-tone="pa"] {
  background: radial-gradient(120% 110px at 50% 0%, rgba(76,154,255,0.28), rgba(139,92,246,0.10) 60%, transparent 100%);
}
.ls-call-views { position: relative; }
.ls-call-view {
  position: relative; display: flow-root;   /* contain the last child's margin, or the measure misses it */
  transition: opacity 240ms ease 110ms, transform 380ms cubic-bezier(0.16, 1, 0.3, 1) 60ms;
}
.ls-call-view:not([data-on="1"]) {
  position: absolute; left: 0; right: 0; top: 0;
  opacity: 0; transform: scale(0.97); pointer-events: none; visibility: hidden;
  transition: opacity 160ms ease, transform 200ms ease, visibility 0s linear 200ms;
}

/* RINGING. Compact: who, then the answers. */
.ls-call-caller { display: flex; align-items: center; gap: 14px; padding: 18px 20px 4px; }
.ls-call-avatar {
  position: relative; flex: none;
  width: 54px; height: 54px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 22px; font-weight: 600; color: #fff;
  background: linear-gradient(140deg, #ff9a62, #ff4f8b);
  box-shadow: inset 0 0 0 1px rgba(255,255,255,0.18);
}
.ls-call-avatar::after {
  content: ""; position: absolute; inset: -5px; border-radius: 50%;
  border: 2px solid rgba(255,138,101,0.6);
  animation: ls-call-halo 1.6s cubic-bezier(0.2, 0.7, 0.3, 1) infinite;
}
.ls-call:not([data-state="ringing"]) .ls-call-avatar::after { animation: none; opacity: 0; }
@keyframes ls-call-halo {
  0% { transform: scale(0.94); opacity: 0.9; }
  100% { transform: scale(1.32); opacity: 0; }
}
.ls-call-id { min-width: 0; display: flex; flex-direction: column; }
.ls-call-kicker {
  font-size: 12px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase;
  color: rgba(255,255,255,0.55);
}
.ls-call-name { font-size: 30px; font-weight: 600; line-height: 1.12; letter-spacing: -0.015em; color: #fff; }
.ls-call-label { font-size: 14px; color: rgba(255,255,255,0.55); }
.ls-call-row {
  display: flex; justify-content: space-around; align-items: flex-start;
  padding: 14px 18px 4px;
}
.ls-call-btn {
  -webkit-appearance: none; appearance: none; border: 0; background: none; padding: 0;
  display: flex; flex-direction: column; align-items: center; gap: 7px;
  min-width: 72px; color: rgba(255,255,255,0.78);
  font: inherit; font-size: 12.5px; font-weight: 500;
  cursor: pointer; -webkit-tap-highlight-color: transparent;
}
.ls-call-dot {
  width: 58px; height: 58px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  background: rgba(255,255,255,0.12);
  transition: transform 140ms cubic-bezier(0.32,0.72,0,1), background 160ms ease;
}
.ls-call-dot svg { width: 25px; height: 25px; fill: none; stroke: #fff; stroke-width: 1.9; stroke-linecap: round; stroke-linejoin: round; }
.ls-call-btn[data-kind="danger"] .ls-call-dot { background: #ff4d4f; }
.ls-call-btn[data-kind="go"] .ls-call-dot { background: #30c26a; }
.ls-call-btn:active .ls-call-dot { transform: scale(0.92); }
.ls-call-btn[aria-pressed="true"] .ls-call-dot { background: #fff; }
.ls-call-btn[aria-pressed="true"] .ls-call-dot svg { stroke: #16161b; }
.ls-call-btn:focus-visible { outline: none; }
.ls-call-btn:focus-visible .ls-call-dot { outline: 3px solid #4c9aff; outline-offset: 3px; }
/* The headline answer. Full width, the accent gradient, and a slow sheen, so
   it reads as THE thing to press without shouting over the call itself. */
.ls-call-pa {
  -webkit-appearance: none; appearance: none; border: 0;
  position: relative; overflow: hidden;
  display: flex; align-items: center; justify-content: center; gap: 9px;
  width: calc(100% - 32px); height: 54px; margin: 14px 16px 16px;
  border-radius: 999px;
  font: inherit; font-size: 16.5px; font-weight: 600; letter-spacing: 0.005em; color: #fff;
  background: linear-gradient(100deg, #3f7dff 0%, #4c9aff 38%, #7c5cff 100%);
  box-shadow: 0 10px 26px -10px rgba(76,154,255,0.75), inset 0 1px 0 rgba(255,255,255,0.25);
  cursor: pointer; -webkit-tap-highlight-color: transparent;
  transition: transform 140ms cubic-bezier(0.32,0.72,0,1);
}
.ls-call-pa::after {
  content: ""; position: absolute; top: 0; bottom: 0; left: -40%; width: 40%;
  background: linear-gradient(100deg, transparent, rgba(255,255,255,0.28), transparent);
  transform: skewX(-18deg);
  animation: ls-call-sheen 3.2s cubic-bezier(0.4, 0, 0.2, 1) 0.8s infinite;
}
@keyframes ls-call-sheen { 0% { left: -40%; } 45%, 100% { left: 120%; } }
.ls-call-pa svg { width: 20px; height: 20px; fill: none; stroke: #fff; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.ls-call-pa:active { transform: scale(0.98); }
.ls-call-pa:focus-visible { outline: 3px solid #fff; outline-offset: 3px; }

/* THE CONVERSATION: the PA talking, or you. */
.ls-call-head {
  display: flex; align-items: center; gap: 9px;
  padding: 16px 20px 10px;
  font-size: 15px; font-weight: 600; color: rgba(255,255,255,0.94);
}
.ls-call-live {
  flex: none; width: 8px; height: 8px; border-radius: 50%; background: #3ddc84;
  box-shadow: 0 0 0 0 rgba(61,220,132,0.55);
  animation: ls-call-live 1.8s ease-out infinite;
}
@keyframes ls-call-live {
  0% { box-shadow: 0 0 0 0 rgba(61,220,132,0.55); }
  70%, 100% { box-shadow: 0 0 0 8px rgba(61,220,132,0); }
}
.ls-call-title { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ls-call-timer {
  flex: none; font-size: 14px; font-weight: 500; color: rgba(255,255,255,0.55);
  font-variant-numeric: tabular-nums;
}
.ls-call-log {
  display: flex; flex-direction: column; gap: 8px;
  max-height: 318px; overflow-y: auto; padding: 4px 16px 12px;
  scrollbar-width: none; overscroll-behavior: contain;
  -webkit-mask-image: linear-gradient(to bottom, transparent 0, #000 14px);
  mask-image: linear-gradient(to bottom, transparent 0, #000 14px);
}
.ls-call-log::-webkit-scrollbar { display: none; }
.ls-call-log:empty { display: none; }
.ls-call-line {
  display: flex; flex-direction: column; align-items: flex-start; gap: 3px;
  max-width: 86%;
  animation: ls-call-line-in 420ms cubic-bezier(0.16, 1, 0.3, 1) backwards;
}
.ls-call-line[data-who="pa"] { align-self: flex-end; align-items: flex-end; }
@keyframes ls-call-line-in {
  from { opacity: 0; transform: translateY(8px) scale(0.97); }
  to { opacity: 1; transform: none; }
}
.ls-call-speaker {
  display: flex; align-items: center; gap: 6px; padding: 0 6px;
  font-size: 11px; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase;
  color: rgba(255,255,255,0.45);
}
.ls-call-bars { display: inline-flex; align-items: center; gap: 2px; height: 10px; opacity: 0; transition: opacity 200ms ease; }
.ls-call-bars i {
  display: block; width: 2.5px; height: 10px; border-radius: 2px;
  background: currentColor; transform: scaleY(0.3); transform-origin: 50% 50%;
}
.ls-call-line[data-speaking="1"] .ls-call-bars { opacity: 1; }
.ls-call-line[data-who="pa"] .ls-call-speaker { color: #8fbcff; }
.ls-call-line[data-who="caller"] .ls-call-speaker { color: #ffb09a; }
.ls-call-text {
  padding: 9px 13px; border-radius: 18px;
  font-size: 15px; line-height: 1.38; color: rgba(255,255,255,0.92);
  background: rgba(255,255,255,0.08);
  border-bottom-left-radius: 6px;
  min-height: 39px; min-width: 44px;
}
.ls-call-line[data-who="pa"] .ls-call-text {
  background: linear-gradient(135deg, rgba(76,154,255,0.30), rgba(124,92,255,0.26));
  border-bottom-left-radius: 18px; border-bottom-right-radius: 6px;
}
.ls-call-line[data-cut="1"] .ls-call-text::after { content: "\u2026"; color: rgba(255,255,255,0.45); }
.ls-call-mark {
  align-self: center; margin: 4px 0 2px; padding: 4px 11px; border-radius: 999px;
  font-size: 12px; font-weight: 600; color: #3ddc84;
  background: rgba(61,220,132,0.12);
  animation: ls-call-line-in 420ms cubic-bezier(0.16, 1, 0.3, 1) backwards;
}
.ls-call-ctls { position: relative; }
.ls-call-ctl { display: flex; gap: 10px; padding: 4px 16px 16px; }
.ls-call-ctl[data-for="live"] { justify-content: space-around; padding-top: 8px; }
.ls-call-ctl:not([data-on="1"]) { display: none; }
.ls-call-wide {
  -webkit-appearance: none; appearance: none; border: 0;
  flex: 1 1 0; height: 50px; border-radius: 999px;
  display: flex; align-items: center; justify-content: center; gap: 8px;
  font: inherit; font-size: 15.5px; font-weight: 600; color: #fff;
  background: rgba(255,255,255,0.12);
  cursor: pointer; -webkit-tap-highlight-color: transparent;
  transition: transform 140ms cubic-bezier(0.32,0.72,0,1);
}
.ls-call-wide svg { width: 19px; height: 19px; fill: none; stroke: currentColor; stroke-width: 1.9; stroke-linecap: round; stroke-linejoin: round; }
.ls-call-wide[data-kind="primary"] {
  background: linear-gradient(100deg, #3f7dff, #6d6bff);
  box-shadow: 0 8px 22px -10px rgba(76,154,255,0.8), inset 0 1px 0 rgba(255,255,255,0.22);
}
.ls-call-wide[data-kind="danger"] { background: rgba(255,77,79,0.16); color: #ff7b7d; }
.ls-call-wide:active { transform: scale(0.97); }
.ls-call-wide:focus-visible { outline: 3px solid #4c9aff; outline-offset: 2px; }

/* ENDED: one line, then gone. */
.ls-call-view[data-for="ended"] { text-align: center; white-space: nowrap; }
.ls-call-pill {
  display: inline-flex; align-items: center; justify-content: center; gap: 10px;
  padding: 13px 20px; white-space: nowrap;
  font-size: 14.5px; font-weight: 600; color: rgba(255,255,255,0.9);
}
.ls-call-pill-ico {
  flex: none; width: 26px; height: 26px; border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  background: rgba(255,255,255,0.12);
}
.ls-call-pill-ico svg { width: 16px; height: 16px; fill: none; stroke: #fff; stroke-width: 1.9; stroke-linecap: round; stroke-linejoin: round; }
.ls-call-pill[data-kind="event"] .ls-call-pill-ico { background: linear-gradient(135deg, #4c9aff, #7c5cff); }
.ls-call-pill-text { transition: opacity 200ms ease; }

/* THE LIQUID. A throwaway layer that exists only during the hand-over: blobs
   stand in for the cards, run together under an alpha-contrast (goo) filter
   into one droplet, and the droplet stretches into the card. The filter never
   touches the real cards -- a blur-and-threshold over text and backdrop-blurred
   glass reads as mud, and a filtered ancestor would break their backdrop. */
.ls-goo {
  position: fixed; z-index: 30; pointer-events: none;
  filter: url(#ls-goo-filter);
  transition: opacity 240ms ease;
}
.ls-goo-blob {
  position: absolute; left: 0; top: 0;
  background: #2b2c33;
  transform-origin: 50% 50%;
  will-change: transform;
}
.ls-feed [data-call-card] { will-change: transform, opacity; }
/* THE REMINDER the PA leaves in Alerts, in the PA's colours so it reads as
   something your assistant did rather than one more app notification. Its exit
   is the islands' entrance run backwards, so a dismissal looks like the same
   material leaving rather than a node being deleted. */
.ls-call-reminder {
  background: linear-gradient(180deg, rgba(76,154,255,0.16), rgba(124,92,255,0.05) 75%), rgba(30, 30, 34, 0.92);
  box-shadow: inset 0 0 0 1px rgba(125,160,255,0.35), 0 6px 18px -6px rgba(0, 0, 0, 0.75);
}
.ls-call-reminder-tile {
  flex: none; width: 30px; height: 30px; border-radius: 9px;
  display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg, #4c9aff, #7c5cff);
}
.ls-call-reminder-tile svg { width: 17px; height: 17px; fill: none; stroke: #fff; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.ls-call-reminder-meta {
  font-size: 11px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase;
  color: #8fbcff;
}
.ls-call-reminder-title { margin-top: 2px; font-size: 14px; font-weight: 600; color: #fff; }
.ls-call-reminder-text { margin-top: 2px; font-size: 13px; line-height: 1.38; color: rgba(255,255,255,0.72); }
.ls-call-reminder[data-leaving="1"] {
  animation: ls-call-out 300ms cubic-bezier(0.4, 0, 1, 1) forwards;
  pointer-events: none;
}
@keyframes ls-call-out {
  from { opacity: 1; transform: none; filter: none; }
  to   { opacity: 0; transform: translateY(6px) scale(0.96); filter: blur(3px); }
}
@media (prefers-reduced-motion: reduce) {
  .ls-call, .ls-call-card, .ls-call-view, .ls-call-view:not([data-on="1"]) { transition: opacity 180ms ease; }
  .ls-call-ring, .ls-call-avatar::after, .ls-call-pa::after, .ls-call-live,
  .ls-call-line, .ls-call-mark, .ls-call-reminder[data-leaving="1"] { animation: none; }
  .ls-call-ring { opacity: 0.55; }
  .ls-call-view:not([data-on="1"]) { transform: none; }
}
.lockscreen[data-blanked] .ls-call *, .lockscreen[data-blanked] .ls-call *::after { animation-play-state: paused; }
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
          <symbol id="lv-projects" viewBox="0 0 24 24">
            <!-- stacked layers, the shape of a thing with work under it. Not a
                 folder: a folder says "files", and a project here is a body of
                 work agents are moving, not a place documents are kept. -->
            <path d="M12 3.2 21 7.6 12 12 3 7.6z" />
            <path d="M3.4 12 12 16.3 20.6 12" />
            <path d="M3.4 16.4 12 20.7l8.6-4.3" />
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
    # Jay's order, from the glass: agents, projects, alerts, mailbox, phone,
    # stats -- then apps, which he asked to keep but did not place. Projects
    # sits second because this is a projects-focused OS and it replaced the
    # settings tab outright; pending decisions are not a tab of their own, they
    # ride at the top of ALERTS where they can be answered quickly.
    ("agents", "Agents", "lv-agents", "ls-activity"),
    ("projects", "Projects", "lv-projects", "ls-projects"),
    ("alerts", "Alerts", "lv-alerts", "ls-notifs"),
    ("mailbox", "Mailbox", "lv-mailbox", "ls-mailbox"),
    ("phone", "Phone", "lv-phone", "ls-phone"),
    ("stats", "System", "lv-stats", "ls-stats"),
    ("apps", "Apps", "lv-apps", "ls-apps"),
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
             role="tabpanel" aria-labelledby="ls-tab-alerts" aria-label="Alerts" hidden>
          <!-- Pending decisions live at the TOP OF ALERTS rather than in a tab
               of their own (Jay, from the glass: "thats where decisions will go
               for quick answering"). They are the only thing in this panel the
               user answers rather than reads, so they sit above the stacks. -->
          <div class="ls-decisions" id="ls-decisions"></div>
        </div>
        <div class="ls-panel" id="ls-phone" data-view="phone"
             role="tabpanel" aria-labelledby="ls-tab-phone" aria-label="Phone" hidden></div>
        <div class="ls-panel" id="ls-mailbox" data-view="mailbox"
             role="tabpanel" aria-labelledby="ls-tab-mailbox" aria-label="Mailbox" hidden></div>
        <div class="ls-panel" id="ls-apps" data-view="apps"
             role="tabpanel" aria-labelledby="ls-tab-apps" aria-label="Apps" hidden></div>
        <div class="ls-panel" id="ls-projects" data-view="projects"
             role="tabpanel" aria-labelledby="ls-tab-projects" aria-label="Projects" hidden></div>
        <div class="ls-panel" id="ls-stats" data-view="stats"
             role="tabpanel" aria-labelledby="ls-tab-stats" aria-label="System" hidden></div>
      </div>
      <!-- THE LIVE ZONE (incoming-call demo). Present only while a call is;
           /auth/lock-screen.js fills it from /auth/lock-call. Every word in it
           is scripted server-side: this screen renders before sign-in. -->
      <section class="ls-call" id="ls-call" aria-labelledby="ls-call-heading" hidden>
        <div class="ls-call-glow" aria-hidden="true">
          <span class="ls-call-ring" data-tone="caller"></span>
          <span class="ls-call-ring" data-tone="pa"></span>
        </div>
        <div class="ls-call-card" id="ls-call-card">
          <span class="ls-call-wash" data-tone="caller" aria-hidden="true"></span>
          <span class="ls-call-wash" data-tone="pa" aria-hidden="true"></span>
          <div class="ls-call-views">
            <div class="ls-call-view" data-for="ringing">
              <div class="ls-call-caller">
                <div class="ls-call-avatar" id="ls-call-avatar" aria-hidden="true">N</div>
                <div class="ls-call-id">
                  <div class="ls-call-kicker" id="ls-call-heading">Incoming call</div>
                  <div class="ls-call-name" id="ls-call-name">Naira</div>
                  <div class="ls-call-label" id="ls-call-label">mobile</div>
                </div>
              </div>
              <div class="ls-call-row">
                <button type="button" class="ls-call-btn" data-kind="danger" data-call-act="decline">
                  <span class="ls-call-dot"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.2 13.9c5-4.6 12.6-4.6 17.6 0l-1.8 2.6-3.6-1.2-.5-2.6a10 10 0 0 0-5.8 0l-.5 2.6-3.6 1.2z"/></svg></span>
                  Decline
                </button>
                <button type="button" class="ls-call-btn" data-call-act="voicemail">
                  <span class="ls-call-dot"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="6.8" cy="12.5" r="4"/><circle cx="17.2" cy="12.5" r="4"/><path d="M6.8 16.5h10.4"/></svg></span>
                  Voicemail
                </button>
                <button type="button" class="ls-call-btn" data-kind="go" data-call-act="answer">
                  <span class="ls-call-dot"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6.2 3.5l2.4 4-1.9 2a11 11 0 0 0 5.8 5.8l2-1.9 4 2.4v3.1a1.7 1.7 0 0 1-1.9 1.7A16.5 16.5 0 0 1 3.4 5.4 1.7 1.7 0 0 1 5.1 3.5z"/></svg></span>
                  Answer
                </button>
              </div>
              <button type="button" class="ls-call-pa" data-call-act="pa">
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 3.5l1.9 5.1 5.1 1.9-5.1 1.9L11 17.5l-1.9-5.1L4 10.5l5.1-1.9z"/><path d="M18.3 14.8l.8 2.1 2.1.8-2.1.8-.8 2.1-.8-2.1-2.1-.8 2.1-.8z"/></svg>
                Send to PA
              </button>
            </div>
            <div class="ls-call-view" data-for="talk">
              <div class="ls-call-head">
                <span class="ls-call-live" aria-hidden="true"></span>
                <span class="ls-call-title" id="ls-call-title"></span>
                <span class="ls-call-timer" id="ls-call-timer" role="timer" aria-live="off">0:00</span>
              </div>
              <div class="ls-call-log" id="ls-call-log" role="log" aria-live="polite"
                   aria-label="Call transcript"></div>
              <div class="ls-call-ctls">
                <div class="ls-call-ctl" data-for="pa">
                  <button type="button" class="ls-call-wide" data-kind="primary" data-call-act="takeover">
                    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6.2 3.5l2.4 4-1.9 2a11 11 0 0 0 5.8 5.8l2-1.9 4 2.4v3.1a1.7 1.7 0 0 1-1.9 1.7A16.5 16.5 0 0 1 3.4 5.4 1.7 1.7 0 0 1 5.1 3.5z"/></svg>
                    Take over
                  </button>
                  <button type="button" class="ls-call-wide" data-kind="danger" data-call-act="end">
                    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.2 13.9c5-4.6 12.6-4.6 17.6 0l-1.8 2.6-3.6-1.2-.5-2.6a10 10 0 0 0-5.8 0l-.5 2.6-3.6 1.2z"/></svg>
                    End call
                  </button>
                </div>
                <div class="ls-call-ctl" data-for="live">
                  <button type="button" class="ls-call-btn" data-call-toggle="mute" aria-pressed="false">
                    <span class="ls-call-dot"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M4 4l16 16"/></svg></span>
                    Mute
                  </button>
                  <button type="button" class="ls-call-btn" data-call-toggle="speaker" aria-pressed="false">
                    <span class="ls-call-dot"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/></svg></span>
                    Speaker
                  </button>
                  <button type="button" class="ls-call-btn" data-kind="danger" data-call-act="end">
                    <span class="ls-call-dot"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.2 13.9c5-4.6 12.6-4.6 17.6 0l-1.8 2.6-3.6-1.2-.5-2.6a10 10 0 0 0-5.8 0l-.5 2.6-3.6 1.2z"/></svg></span>
                    End
                  </button>
                </div>
              </div>
            </div>
            <div class="ls-call-view" data-for="ended">
              <div class="ls-call-pill" id="ls-call-pill" role="status">
                <span class="ls-call-pill-ico" id="ls-call-pill-ico" aria-hidden="true"></span>
                <span class="ls-call-pill-text" id="ls-call-outcome"></span>
              </div>
            </div>
          </div>
        </div>
      </section>
      <svg class="ls-goo-defs" width="0" height="0" aria-hidden="true" focusable="false"
           style="position:absolute;width:0;height:0">
        <filter id="ls-goo-filter" x="-20%" y="-20%" width="140%" height="140%"
                color-interpolation-filters="sRGB">
          <feGaussianBlur in="SourceGraphic" stdDeviation="9" result="blur"/>
          <feColorMatrix in="blur" mode="matrix"
                         values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 22 -10"/>
        </filter>
      </svg>
      {_FRAMEWORK_SPRITE}
      {_VIEW_SPRITE}
    </div>
    <div class="ls-spacer"></div>
    <div class="ls-unlock" id="ls-unlock">
      <div class="ls-unlock-note" id="ls-unlock-note" role="status" hidden></div>
      <!-- Torch and camera flank the unlock bar, where every phone puts them.
           They are OUTSIDE the unlock button, not inside it: a tap meant for
           the torch must never be read as a swipe toward the keypad. -->
      <div class="ls-unlock-row">
        <button type="button" class="ls-quick" id="ls-torch"
                aria-pressed="false" aria-label="Torch">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M9 2.6h6l-.7 3.2H9.7z"/>
            <path d="M9.7 5.8h4.6l.5 2.6-1 1.4v11.6h-3.6V9.8l-1-1.4z"/>
          </svg>
        </button>
        <button type="button" class="ls-unlock-btn" id="ls-unlock-btn"
                aria-expanded="false" aria-controls="ls-foot">
          <span class="ls-grabber"></span>
          <span class="ls-unlock-label">Swipe up to unlock</span>
        </button>
        <button type="button" class="ls-quick" id="ls-camera" aria-label="Camera">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M3.4 8.2h3l1.3-2h6.6l1.3 2h3a1.4 1.4 0 0 1 1.4 1.4v8.6a1.4 1.4 0 0 1-1.4 1.4H3.4A1.4 1.4 0 0 1 2 18.2V9.6a1.4 1.4 0 0 1 1.4-1.4z"/>
            <circle cx="12" cy="13.6" r="3.4"/>
          </svg>
        </button>
      </div>
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
  <!-- The power menu. Raised by HOLDING the power key: sway posts to
       /auth/lock-power-menu on loopback and the stream below brings it here.
       ⚠ This sheet is reachable BEFORE SIGN-IN, exactly as holding the physical
       key always was. Power off and Restart add nothing the hardware key did
       not already allow; "Stop all agents" and "Emergency call" DO ask for
       something more, which is why Jay asked for both to confirm first. -->
  <!-- THE VOLUME BEZEL. Vertical, on the RIGHT, because that is the side the
       rocker is on -- the readout should be next to the finger that caused it.
       It does NOT take a sheet slot: volume is a transient heads-up, and taking
       the sheet slot would mean nudging the volume dismissed an open menu. -->
  <div class="ls-vol" id="ls-vol" aria-hidden="true">
    <div class="ls-vol-track"><span class="ls-vol-fill" id="ls-vol-fill"></span></div>
    <div class="ls-vol-num" id="ls-vol-num">--</div>
    <div class="ls-vol-note" id="ls-vol-note" hidden></div>
  </div>

  <!-- THE AGENT CAROUSEL. Jay: "a carousel type animation slides out from the
       left of the screen where the buttons are with the agents avatars/faces",
       then: "left edge thumb pivot around the button". So it is RADIAL --
       faces on an arc swept from the left edge at the volume rocker's height,
       and the thumb stays on the button while the agents come to it. -->
  <div class="ls-carousel" id="ls-carousel" aria-hidden="true">
    <div class="ls-carousel-arc" aria-hidden="true"></div>
    <div class="ls-carousel-strip" id="ls-carousel-strip"></div>
    <div class="ls-carousel-banner" id="ls-carousel-banner">
      <div class="ls-carousel-name" id="ls-carousel-name"></div>
      <div class="ls-carousel-role" id="ls-carousel-role"></div>
      <div class="ls-carousel-ptt" id="ls-carousel-ptt">Hold a volume key to talk</div>
    </div>
  </div>

  <!-- THE PULL-DOWN SHADE. Jay: "We need a pull down area from the top of the
       screen for things like brightness". Swipe down from the top edge.
       "things like" is the brief, so this is a container with one control in it
       today rather than a brightness dialog -- the next toggle goes beside it
       without moving anything. -->
  <section class="ls-shade" id="ls-shade" role="dialog" aria-modal="true"
           aria-label="Quick settings" hidden>
    <div class="ls-shade-inner">
      <!-- Radio switches. Big round targets in a row, the way a phone's control
           centre does it, because these are hit with a thumb and not read. -->
      <div class="ls-shade-toggles" id="ls-shade-toggles"></div>
      <div class="ls-shade-row">
        <svg class="ls-shade-icon" viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="12" cy="12" r="4.2" />
          <path d="M12 2.6v2.6M12 18.8v2.6M21.4 12h-2.6M5.2 12H2.6" />
          <path d="m18.6 5.4-1.8 1.8M7.2 16.8l-1.8 1.8M18.6 18.6l-1.8-1.8M7.2 7.2 5.4 5.4" />
        </svg>
        <input type="range" class="ls-shade-slider" id="ls-brightness"
               min="4" max="100" step="1" value="60"
               aria-label="Screen brightness" />
        <span class="ls-shade-value" id="ls-brightness-value">--</span>
      </div>
      <div class="ls-shade-note" id="ls-shade-note" hidden></div>
      <span class="ls-shade-grip" aria-hidden="true"></span>
    </div>
  </section>

  <section class="ls-modal" id="ls-power" role="dialog" aria-modal="true"
           aria-labelledby="ls-power-title" hidden>
    <div class="ls-modal-card">
      <div class="ls-modal-title" id="ls-power-title">Power</div>
      <div class="ls-modal-sub" id="ls-power-sub">Hold the power key to reach this</div>
      <div class="ls-power-body" id="ls-power-body"></div>
      <button type="button" class="ls-modal-cancel" id="ls-power-close">Cancel</button>
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
  <!-- Dictation. A CENTRED DIALOG, not a bottom sheet: Jay, of the microphone
       on an agent island -- "instead of a slide up menu at the bottom can we
       have a dialog in the centre of the screen against a blur effect". Same
       .ls-modal shell as the power menu, so the two modal surfaces on this
       screen read as one thing rather than two designs.
       The grabber is gone with the sheet: it was the affordance for dragging a
       sheet down, and there is nothing to drag now. -->
  <section class="ls-modal ls-modal-voice" id="ls-voice" role="dialog" aria-modal="true"
           aria-labelledby="ls-voice-title" hidden>
    <div class="ls-modal-card">
      <div class="ls-voice-head">
        <div class="ls-sheet-avatar" id="ls-voice-avatar" aria-hidden="true"></div>
        <div class="ls-voice-who">
          <div class="ls-sheet-name" id="ls-voice-title"></div>
          <div class="ls-sheet-sub" id="ls-voice-state">Listening\u2026</div>
        </div>
        <button type="button" class="ls-sheet-close" id="ls-voice-close" aria-label="Cancel dictation">&#10005;</button>
      </div>
      <div class="ls-voice-body">
        <canvas class="ls-wave" id="ls-wave" width="600" height="120" aria-hidden="true"></canvas>
        <p class="ls-voice-text" id="ls-voice-text" aria-live="polite"></p>
      </div>
      <div class="ls-voice-acts">
        <button type="button" class="ls-act" id="ls-voice-cancel">Cancel</button>
        <button type="button" class="ls-act" data-act="approve" id="ls-voice-send" disabled>Send</button>
      </div>
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

    // An island's IMMUTABLE half, as a string. Everything here comes from
    // configuration rather than from runtime state, so a change is a
    // reconfiguration and not a tick: rare enough to rebuild the element for,
    // and not worth the code to mutate an avatar or a framework badge in place.
    // The agent's NAME is not in here because the name is the key itself.
    //
    // JSON.stringify rather than a joined string: an avatar is a URL and a
    // framework name is user-supplied, so any separator character I picked
    // could appear inside a value and make two different agents compare equal.
    function islandIdentity(agent) {
      return JSON.stringify([
        agent.avatar || "",
        agent.framework_icon || "",
        String(agent.framework || "").toLowerCase(),
        agent.system ? "1" : "",
      ]);
    }

    // Whether a status reads as a just-finished completion beat ("✓
    // Table booked at Dishoom") rather than an ongoing task -- shared between
    // paint and animation so the two never disagree about which one a status
    // is.
    function isDoneStatus(status) {
      return /^✓\s/.test(status || "");
    }

    // How long the status-change animation runs. The swap below happens at
    // roughly the halfway point of this same duration, so the two stay in
    // sync by construction rather than by two numbers that can drift apart.
    var STATUS_CHANGE_MS = 380;

    // Slide the OLD status up and out while the NEW one slides in from below,
    // on the SAME `.ls-status` node -- the island must never be rebuilt for
    // this (see reconcileIslands). `data-prev` carries the outgoing text for
    // a `::before` ghost to render and animate out; the real node's
    // textContent is swapped to the new status IMMEDIATELY and synchronously,
    // so a caller reading `.textContent` right after this call -- as the
    // repaint tests do -- sees the new value with no animation-timing
    // dependency. The ghost and the pulse attribute are cosmetic cleanup only
    // and are cleared after the animation via a sequence number, so a second
    // change arriving before the first finishes cannot clear the newer one's
    // state early.
    function animateStatusChange(el, s, status) {
      s.setAttribute("data-prev", s.textContent);
      s.textContent = status;
      var reduced = false;
      try {
        reduced = !!(window.matchMedia
          && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
      } catch (err) { reduced = false; }
      s.className = "ls-status" + (reduced ? " ls-status-crossfade" : " ls-status-slide");
      setAttrIfChanged(el, "data-status-pulse", "1");
      var seq = (s.__statusChangeSeq = (s.__statusChangeSeq || 0) + 1);
      setTimeout(function () {
        if (s.__statusChangeSeq !== seq) return; // superseded by a newer change
        s.removeAttribute("data-prev");
        s.className = "ls-status";
        el.removeAttribute("data-status-pulse");
      }, STATUS_CHANGE_MS);
    }

    // An island's MUTABLE half: everything a 15s poll can legitimately change.
    // Written in place, and only where the value actually differs, so that a
    // tick which changes nothing touches nothing.
    function applyAgent(el, agent) {
      var name = agent.name || "agent";
      var status = agent.status || "idle";
      var busy = RESTING.indexOf(status.trim().toLowerCase()) === -1;
      // The handlers read the whole record off the element rather than
      // re-looking it up by name, so this has to be refreshed even when every
      // visible field is unchanged.
      el.__agent = agent;
      setAttrIfChanged(el, "data-state", busy ? "busy" : "idle");
      if (agent.attention) setAttrIfChanged(el, "data-attention", "1");
      else if (el.hasAttribute("data-attention")) el.removeAttribute("data-attention");
      if (isDoneStatus(status)) setAttrIfChanged(el, "data-done", "1");
      else if (el.hasAttribute("data-done")) el.removeAttribute("data-done");
      setAttrIfChanged(el, "aria-label", (agent.attention && agent.decision)
        ? name + " needs a decision: " + (agent.decision.question || "")
        : name + ", " + status + ". Open conversation.");
      var s = el.querySelector(".ls-status");
      if (s && s.textContent !== status) animateStatusChange(el, s, status);
    }

    function setAttrIfChanged(el, attr, value) {
      if (el.getAttribute(attr) !== value) el.setAttribute(attr, value);
    }

    function island(agent) {
      var name = agent.name || "agent";
      var status = agent.status || "idle";
      var busy = RESTING.indexOf(status.trim().toLowerCase()) === -1;

      var el = document.createElement("div");
      el.className = "ls-island";
      el.setAttribute("data-identity", islandIdentity(agent));
      // Stable identity across repaints. The 15s poll rebuilds this list, and
      // without a key there is no way to put keyboard focus back on the island
      // the user was actually on -- an index would silently move the focus to a
      // different agent whenever the list reorders.
      // A device agent carries its OWN key (`device:<slug>`), so a plugged-in
      // board and a TAOS_LOCK_DEMO_AGENTS placeholder of the same name cannot
      // land on the same island and hand each other's payload to one node.
      el.setAttribute("data-agent", agent.key || name);
      el.setAttribute("data-state", busy ? "busy" : "idle");
      if (agent.attention) el.setAttribute("data-attention", "1");
      if (isDoneStatus(status)) el.setAttribute("data-done", "1");
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
      // Also set on the ISLAND itself, not only its avatar child: the status
      // pulse glow lives on `.ls-island` and a custom property set on a
      // descendant does not flow back up to its ancestor.
      el.style.setProperty("--ls-a", "hsl(" + hue + " 62% 58%)");
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

    // Bring the island list to match `agents` by CHANGING it, never by
    // rebuilding it.
    //
    // The old code did `agentsEl.textContent = ""` and appended six fresh
    // elements every 15 seconds. Every one of them was a new node, so every one
    // of them replayed `ls-island-in` -- a 520ms entrance animation with
    // staggered per-child delays. On the glass that is the whole list blinking
    // every fifteen seconds, which is what Jay reported, and it happened
    // whether or not a single byte of the payload had changed.
    //
    // Measured before the fix, over one poll with nothing touched: six of six
    // islands fired `animationstart`, and the first island was no longer the
    // same DOM node. Both of those are what the test asserts, because "the
    // names are still right" would have passed on the broken code too.
    //
    // Keyed by agent name: that is already the identity this list uses for
    // focus restoration, and an index would move an agent's island under the
    // user's finger the moment the list reordered.
    // Put exactly `els` inside `parent`, in this order, and remove whatever
    // else is in there -- WITHOUT touching an element that is already in
    // position. Re-inserting a node restarts its CSS animation, so a blind
    // appendChild of every child in order flickers exactly as badly as the
    // wipe it replaces. Every keyed list on this screen goes through here.
    function placeInOrder(parent, els) {
      var prev = null;
      for (var i = 0; i < els.length; i++) {
        var want = prev ? prev.nextSibling : parent.firstChild;
        if (els[i] !== want) parent.insertBefore(els[i], want);
        prev = els[i];
      }
      // Anything past the last wanted element is no longer in the payload.
      while (prev ? prev.nextSibling : parent.firstChild) {
        parent.removeChild(prev ? prev.nextSibling : parent.firstChild);
      }
    }

    // The child of `parent` carrying this key, created once if it is not
    // there yet. A new element comes back DETACHED -- placeInOrder puts it
    // where the payload says it goes.
    function partOf(parent, key, cls, tag) {
      var kids = parent.children;
      for (var i = 0; i < kids.length; i++) {
        if (kids[i].getAttribute("data-part") === key) return kids[i];
      }
      var el = document.createElement(tag || "div");
      el.className = cls;
      el.setAttribute("data-part", key);
      return el;
    }

    // Only write text that actually changed: assigning textContent replaces
    // the text node even when the string is identical.
    function setText(el, text) {
      if (el.textContent !== text) el.textContent = text;
      return el;
    }

    function reconcileIslands(agents) {
      var existing = {};
      var kids = agentsEl.children;
      for (var i = 0; i < kids.length; i++) {
        var key = kids[i].getAttribute("data-agent");
        if (key !== null) existing[key] = kids[i];
      }
      var want = [];
      for (var j = 0; j < agents.length; j++) {
        var agent = agents[j];
        var name = agent.name || "agent";
        // Look up by the SAME key island() writes to data-agent. Looking up by
        // name never matched a device island (key `device:<slug>`), so it was
        // rebuilt, entrance animation and all, on every poll.
        var key = agent.key || name;
        var el = Object.prototype.hasOwnProperty.call(existing, key)
          ? existing[key] : null;
        // A reconfigured agent -- new portrait, different framework -- is the
        // one case where the element itself is wrong rather than merely stale.
        if (el && el.getAttribute("data-identity") !== islandIdentity(agent)) {
          el.remove();
          el = null;
        }
        if (el) {
          applyAgent(el, agent);
        } else {
          el = island(agent);
        }
        // Claimed: a second agent sharing this name gets its own island
        // rather than the two of them fighting over one element.
        delete existing[key];
        want.push(el);
      }
      // Whatever the payload no longer lists has genuinely gone away, and
      // placeInOrder drops it.
      placeInOrder(agentsEl, want);
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

      tasksEl.textContent = "";
      var agents = data.agents || [];
      var tasks = data.tasks || [];
      if (!agents.length && !tasks.length) { card.hidden = true; return; }

      reconcileIslands(agents);
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

    // Default cadence when the server does not say otherwise (demo mode off,
    // or on but nothing scheduled to change).
    var WIDGETS_POLL_MS = 15000;
    var widgetsTimer = null;
    var widgetsPaused = false;

    // Schedules the NEXT fetch rather than re-arming a fixed interval, so a
    // rotating demo task's `refresh_in_ms` can pull the next poll in tighter
    // than 15s and land the fetch right as the status is due to change,
    // instead of the change sitting there stale for up to 15s.
    function scheduleWidgetsPoll(ms) {
      if (widgetsTimer) { clearTimeout(widgetsTimer); widgetsTimer = null; }
      if (widgetsPaused) return;
      widgetsTimer = setTimeout(pollActivity, ms || WIDGETS_POLL_MS);
    }

    function pollActivity() {
      fetch("/auth/lock-widgets", { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (d) paintActivity(d);
          scheduleWidgetsPoll(d && d.refresh_in_ms);
        })
        .catch(function () {
          // Offline or off-console: leave the card as is, but keep polling --
          // the network coming back is exactly when this needs to recover.
          scheduleWidgetsPoll();
        });
    }

    // The screen is off or the tab/app is backgrounded: a poll landing then
    // paints nothing anyone can see and just burns battery and radio, so this
    // pauses the schedule entirely rather than merely slowing it down.
    function pauseWidgetsPoll() {
      widgetsPaused = true;
      if (widgetsTimer) { clearTimeout(widgetsTimer); widgetsTimer = null; }
    }

    // Coming back: fetch immediately rather than waiting out whatever was
    // left of the paused schedule, so a lock screen that was just woken shows
    // this instant's state, not a stale one from before it went dark.
    function resumeWidgetsPoll() {
      if (!widgetsPaused) return;
      widgetsPaused = false;
      pollActivity();
    }

    if (card) {
      pollActivity();
      try {
        document.addEventListener("visibilitychange", function () {
          if (document.visibilityState === "hidden") pauseWidgetsPoll();
          else resumeWidgetsPoll();
        });
      } catch (err) { /* no document in this harness: keep polling on the timer */ }
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

      // Reached by a tap on another tab, by the arrow keys, or at startup.
      // Any of them means "show me this", so the feed comes back.
      setFeedHidden(false);
      renderView(key);
      syncFeedFade();
    }

    // Pressing the ACTIVE category hides the feed; pressing it again restores
    // it. Jay: "pressing on the active category icon on the lock screen hides
    // the notifications/banners etc."
    //
    // The state lives on the feed rather than in a variable so the CSS owns the
    // animation and nothing here has to know how long it takes.
    function setFeedHidden(hidden) {
      if (!feedEl) return;
      if (hidden) feedEl.setAttribute("data-hidden", "1");
      else feedEl.removeAttribute("data-hidden");
      // The whole point is that it is out of the way, so it must be out of the
      // way for a screen reader too -- a faded panel is still readable to one.
      feedEl.setAttribute("aria-hidden", hidden ? "true" : "false");
      var tabs = viewTabs();
      for (var i = 0; i < tabs.length; i++) {
        // Only the SELECTED tab carries the state: the others are not holding
        // anything hidden, and saying they are would be a lie to a reader.
        if (tabs[i].getAttribute("aria-selected") === "true") {
          tabs[i].setAttribute("aria-expanded", hidden ? "false" : "true");
        } else {
          tabs[i].removeAttribute("aria-expanded");
        }
      }
    }

    function feedIsHidden() {
      return !!(feedEl && feedEl.hasAttribute("data-hidden"));
    }

    // Panels that are built on demand rather than polled. Agents and alerts
    // are already kept current by the poll and want nothing here.
    function renderView(key) {
      // Numbers that are not on screen are not worth a request every 3s, and
      // the CPU reading is a DELTA -- polling it while hidden would hand the
      // stats view a first sample taken minutes ago.
      if (key === "stats") startStats(); else stopStats();
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

    // A stat row is built once and thereafter only its numbers change.
    //
    // The old code built a fresh one every poll, inside a freshly built
    // `.ls-stat-card`, after `statsEl.textContent = ""`. That is the islands'
    // disease one view over, except the stats poll runs every THREE seconds,
    // not fifteen, and `.ls-stat-card` carries the same 520ms `ls-island-in`
    // entrance. Measured in chromium at 540x1200 before the fix: two entrance
    // replays in 7.5s, and the card was a different, detached node each time.
    // That is the "the system stats widget flickers too" Jay reported in the
    // same breath as the islands.
    //
    // Keeping the row also makes `.ls-stat-fill`'s `transition: width 420ms`
    // mean something. A brand-new node has no previous width to travel from,
    // so every meter SNAPPED to its reading; kept in place, they glide.
    function statRow(parent, key, label, value, pct) {
      var row = partOf(parent, key, "ls-stat");
      var top = row.firstChild;
      if (!top) {
        top = document.createElement("div");
        top.className = "ls-stat-top";
        var l = document.createElement("span");
        l.className = "ls-stat-label";
        var v = document.createElement("span");
        v.className = "ls-stat-value";
        top.appendChild(l); top.appendChild(v);
        row.appendChild(top);
      }
      setText(top.firstChild, label);
      setText(top.lastChild, value);
      // A bar ONLY when there is a real percentage behind it. A meter drawn at
      // zero because nothing was measured looks exactly like a meter drawn at
      // zero because the thing is idle -- and a meter LEFT at its last reading
      // once the readings stop is worse still, because it goes on reporting a
      // measurement nobody is making. So the track goes when the number does.
      var track = row.lastChild === top ? null : row.lastChild;
      if (typeof pct === "number") {
        if (!track) {
          track = document.createElement("div");
          track.className = "ls-stat-track";
          var fill = document.createElement("div");
          fill.className = "ls-stat-fill";
          track.appendChild(fill);
          row.appendChild(track);
        }
        track.firstChild.style.width = Math.max(0, Math.min(100, pct)) + "%";
      } else if (track) {
        row.removeChild(track);
      }
      return row;
    }

    function statNote(parent, key, text) {
      return setText(partOf(parent, key, "ls-stat-note"), text);
    }

    function gib(kb) { return (kb / 1048576).toFixed(1) + " GB"; }

    function rate(kbps) {
      return kbps >= 1000 ? (kbps / 1000).toFixed(1) + " MB/s" : Math.round(kbps) + " KB/s";
    }

    // Split a stat row's meter into the OS's own share (first, grey) and the
    // agents' (the rest, accent). One element and a custom property, so the
    // row's structure -- and its reconcile-by-key identity -- is unchanged.
    function splitMeter(row, basePct, totalPct) {
      var track = row && row.lastChild;
      var fill = track && track.firstChild;
      if (!fill || typeof basePct !== "number" || !(totalPct > 0)) return;
      var at = Math.max(0, Math.min(100, basePct * 100 / totalPct));
      fill.style.setProperty("--ls-split", at.toFixed(1) + "%");
      setAttrIfChanged(fill, "data-split", "1");
    }

    // A stacked history: one column per sample, the whole column the system
    // total and its lower part the agents' share. Columns are made once and
    // then only resized, so the strip glides between polls instead of being
    // rebuilt.
    function statSpark(parent, key, totals, parts, scale) {
      var el = partOf(parent, key, "ls-spark");
      for (var i = 0; i < totals.length; i++) {
        var col = el.children[i];
        if (!col) {
          col = document.createElement("div");
          col.className = "ls-spark-col";
          var part = document.createElement("div");
          part.className = "ls-spark-part";
          col.appendChild(part);
          el.appendChild(col);
        }
        var tot = Math.max(0, totals[i] || 0);
        col.style.height = Math.max(4, Math.min(100, tot * 100 / scale)).toFixed(1) + "%";
        var p = parts && typeof parts[i] === "number" && tot > 0 ? parts[i] * 100 / tot : 0;
        col.firstChild.style.height = Math.max(0, Math.min(100, p)).toFixed(1) + "%";
      }
      while (el.children.length > totals.length) el.removeChild(el.lastChild);
      return el;
    }

    function paintStats(d) {
      if (!statsEl) return;
      // Same as the placeholders: `hidden` meant "empty", and it no longer is.
      statsEl.hidden = false;

      var parts = [];
      var card = partOf(statsEl, "card", "ls-stat-card");
      var rows = [];

      var cores = d.cpu_cores ? " · " + d.cpu_cores + " cores" : "";
      rows.push(statRow(card, "cpu",
        "CPU" + cores,
        typeof d.cpu_percent === "number" ? d.cpu_percent.toFixed(0) + "%" : "--",
        typeof d.cpu_percent === "number" ? d.cpu_percent : null
      ));
      // THE CORRELATION, made visible: with the agent model on, the CPU bar is
      // split into the OS's own load and the agents' share, the caption names
      // both, and the history under it stacks the same two series -- so an
      // agent's burst on the card below is seen landing in the total here.
      var sys = d.system && typeof d.cpu_percent === "number" ? d.system : null;
      if (sys) {
        splitMeter(rows[rows.length - 1], sys.base_cpu_percent, d.cpu_percent);
        rows.push(statNote(card, "cpu-split",
          "Agents " + sys.agents_cpu_percent.toFixed(0) + "%  \u00b7  System "
          + sys.base_cpu_percent.toFixed(0) + "%"));
        if (d.history && d.history.cpu) {
          // Scaled to the next 20% above the busiest bar, so a minute of real
          // movement fills the strip instead of wobbling in its bottom third.
          // It steps only when the load crosses a band, not every poll.
          var peak = Math.max.apply(null, d.history.cpu.concat([1]));
          rows.push(statSpark(card, "cpu-spark", d.history.cpu, d.history.agents_cpu,
            Math.min(100, Math.max(40, Math.ceil(peak / 20) * 20))));
        }
      }

      if (d.memory) {
        rows.push(statRow(card, "memory",
          "Memory",
          gib(d.memory.used_kb) + " / " + gib(d.memory.total_kb),
          d.memory.percent
        ));
        if (sys && typeof d.memory.agents_kb === "number" && d.memory.used_kb > 0) {
          splitMeter(rows[rows.length - 1],
            Math.max(0, d.memory.used_kb - d.memory.agents_kb) * 100 / d.memory.total_kb,
            d.memory.percent);
        }
      } else {
        rows.push(statRow(card, "memory", "Memory", "--"));
      }

      // GPU: LABELLED AS FREQUENCY, because that is what it is. The bar is the
      // clock against its own maximum, and the caption says so -- a parked GPU
      // is not "23% used".
      if (d.gpu && d.gpu.freq_hz) {
        var mhz = Math.round(d.gpu.freq_hz / 1000000);
        var maxMhz = d.gpu.max_freq_hz ? Math.round(d.gpu.max_freq_hz / 1000000) : 0;
        rows.push(statRow(card, "gpu",
          "GPU clock",
          maxMhz ? mhz + " / " + maxMhz + " MHz" : mhz + " MHz",
          maxMhz ? (mhz * 100 / maxMhz) : null
        ));
        if (typeof d.gpu.active_percent === "number") {
          rows.push(statNote(card, "gpu-note",
            "Above idle clock " + d.gpu.active_percent.toFixed(0) + "% of uptime"));
        }
      } else if (!sys) {
        rows.push(statRow(card, "gpu", "GPU clock", "--"));
      }

      // Throughput and thermals: sums and consequences of the same load.
      if (sys) {
        rows.push(statRow(card, "flow", "Throughput",
          Math.round(sys.tokens_per_s) + " tok/s  \u00b7  " + rate(sys.net_kbps)));
        rows.push(statRow(card, "thermal", "Temperature  \u00b7  power",
          sys.temp_c.toFixed(0) + " \u00b0C  \u00b7  " + sys.power_w.toFixed(1) + " W"));
      }

      placeInOrder(card, rows);
      parts.push(card);

      // The remote processors, as state chips. This is where the NPU lives, and
      // running/offline is genuinely all it reports.
      if (d.dsps && d.dsps.length) {
        var chips = partOf(statsEl, "chips", "ls-chips");
        var want = [];
        for (var i = 0; i < d.dsps.length; i++) {
          var name = d.dsps[i].name || "dsp";
          var state = d.dsps[i].state || "unknown";
          var c = partOf(chips, name, "ls-chip", "span");
          c.setAttribute("data-on", String(d.dsps[i].state || "") === "running" ? "1" : "0");
          want.push(setText(c, name + " · " + state));
        }
        placeInOrder(chips, want);
        parts.push(chips);
        parts.push(statNote(statsEl, "accel-note", "On-device accelerators"));
      }
      // With no DSPs the chips and their caption are simply absent from
      // `parts`, and placeInOrder takes them out.

      // PER-AGENT CPU / RAM / STORAGE. Jay: "in the stats it should show live
      // demo data for agents cpu, ram and storage usage".
      //
      // Its own card, below the hardware one, because these are a different
      // KIND of reading: the card above is the device, this is what is running
      // on it. Reconciled by agent name like everything else here, which
      // matters more than usual at a 3s poll -- a rebuilt row every three
      // seconds is the flicker bug with numbers in it.
      if (d.agents && d.agents.length) {
        var acard = partOf(statsEl, "agents", "ls-stat-card");
        var arows = [statNote(acard, "agents-head", "Agents")];
        for (var a = 0; a < d.agents.length; a++) {
          var ag = d.agents[a];
          var nm = ag.name || "agent";
          // One row per agent, all three readings on it: three rows per agent
          // would push a six-agent phone off the bottom of the panel.
          var third = typeof ag.tokens_per_s === "number"
            ? Math.round(ag.tokens_per_s) + " tok/s"
            : (ag.storage_mb >= 1024
                ? (ag.storage_mb / 1024).toFixed(1) + " GB"
                : Math.round(ag.storage_mb) + " MB");
          var arow = statRow(acard, "agent-" + nm,
            nm,
            ag.cpu_percent.toFixed(1) + "%  ·  "
              + Math.round(ag.ram_mb) + " MB  ·  " + third,
            // With the system model on, the bar is this agent's share of the
            // CPU the card above reports, so the rows add up to its blue
            // segment. Without it, the bar is the agent's own CPU reading.
            sys ? ag.cpu_percent * 100 / Math.max(1, d.cpu_percent) : ag.cpu_percent
          );
          setAttrIfChanged(arow, "data-agent", ag.busy === false ? "idle" : "busy");
          arows.push(arow);
        }
        placeInOrder(acard, arows);
        parts.push(acard);
      }

      // "Nobody asked" and "none loaded" are different answers -- and the
      // first is simply not shown, rather than printed as a caveat.
      if (d.models) {
        parts.push(statNote(statsEl, "models",
          d.models.length ? d.models.join(", ") : "No models loaded"));
      }

      placeInOrder(statsEl, parts);
      syncFeedFade();
    }

    // THE PLACEHOLDER TABLE IS GONE, and so is renderPlaceholder.
    //
    // It listed phone / mailbox / apps / settings as "no data source yet" and
    // said things like "Calls and dialler are not wired up on this device yet"
    // -- over a panel that now has ten missed calls in it. It also named a
    // `settings` panel that no longer exists, since Projects replaced it.
    //
    // Every view has a source now, and each panel renders its OWN empty state
    // (paintEmpty), which is both honest and specific: "No missed calls" rather
    // than "not wired up". A second, staler answer to the same question is
    // worse than none.
    if (viewsEl) {
      viewsEl.addEventListener("click", function (ev) {
        var tab = ev.target.closest(".ls-view-tab");
        if (!tab) return;
        var key = tab.getAttribute("data-view");
        // The ACTIVE one toggles; any other one switches to it, and switching
        // always brings the feed back -- asking for a different category means
        // asking to see it.
        if (key === currentView) {
          setFeedHidden(!feedIsHidden());
          return;
        }
        showView(key, false);
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
      sms: '<path d="M4 4.5h16v11H8.5L4 19z"/><path d="M8 8.6h8M8 11.6h5"/>',
      // Two rings joined by a bar: the mark every phone uses for voicemail.
      voicemail: '<circle cx="6.8" cy="13.5" r="4.3"/><circle cx="17.2" cy="13.5" r="4.3"/><path d="M6.8 17.8h10.4"/>',
      // A page of a calendar: what the PA leaves behind after a call (demo).
      calendar: '<rect x="3.5" y="5" width="17" height="15.5" rx="2.6"/><path d="M3.5 9.8h17M8 3v4M16 3v4"/>'
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
      // The paint collects these from the DOM afterwards, so a stack it left
      // untouched still gets its minutes retouched.
      when.setAttribute("data-at", item.at);
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

    // What a stack is CURRENTLY showing. Two payloads with the same signature
    // are the same notifications, so the stack on screen is already right and
    // must not be touched.
    function notifIdentity(group) {
      var parts = [group.app || "", group.glyph || "", group.mono || ""];
      for (var i = 0; i < group.items.length; i++) {
        var it = group.items[i];
        parts.push(String(it.at) + "" + (it.title || "")
          + "" + (it.text || ""));
      }
      return parts.join("");
    }

    function paintNotifications(data) {
      if (!notifsEl) return;
      // Same rule as the islands: never rebuild under an open sheet.
      var sheetNow = screenEl ? screenEl.getAttribute("data-sheet") : "none";
      if (sheetNow && sheetNow !== "none") return;
      var groups = (data && data.groups) || [];
      // The PA's reminder lives in this panel but is not a notification: it
      // comes from the call, and it goes when it is DISMISSED, not when a poll
      // finds no stacks. So an empty paint keeps it, node and all.
      var remEl = document.getElementById("ls-call-reminder");
      if (remEl && remEl.parentNode !== notifsEl) remEl = null;
      if (!groups.length) {
        if (remEl) notifsEl.removeChild(remEl);
        notifsEl.textContent = "";
        if (remEl) notifsEl.appendChild(remEl);
        notifClocks = [];
        notifsEl.hidden = !notifsEl.firstChild;
        return;
      }

      // Keyed by source, exactly like the islands, and for the same reason:
      // the old code wiped the whole stack and rebuilt it, and
      // `.ls-notif-group` carries the same 520ms `ls-island-in` entrance, so
      // every stack replayed its entrance on every poll. It also threw away
      // keyboard focus, and a stack IS a button.
      var existing = {};
      var kids = notifsEl.children;
      for (var i = 0; i < kids.length; i++) {
        var key = kids[i].getAttribute("data-source");
        if (key !== null) existing[key] = kids[i];
      }
      var want = [];
      for (var j = 0; j < groups.length; j++) {
        var group = groups[j];
        if (!group.items || !group.items.length) continue;
        var source = String(group.source);
        var el = Object.prototype.hasOwnProperty.call(existing, source)
          ? existing[source] : null;
        var identity = notifIdentity(group);
        // A stack whose notifications genuinely CHANGED is new content, and
        // new content is exactly what the entrance animation is for. A stack
        // that did not change keeps its node, so it does not animate.
        if (el && el.getAttribute("data-identity") !== identity) el = null;
        if (!el) {
          el = notifGroup(group);
          el.setAttribute("data-source", source);
          el.setAttribute("data-identity", identity);
        }
        delete existing[source];
        want.push(el);
      }
      // Pending decisions ride at the TOP of this panel -- they are the only
      // thing in it the user ANSWERS rather than reads. Included in `want`
      // rather than left where they sit, because placeInOrder removes
      // everything past the last wanted element: left out, the decisions would
      // be deleted by the next notification poll.
      var decEl = document.getElementById("ls-decisions");
      if (remEl) want.unshift(remEl);
      if (decEl && decEl.children.length) want.unshift(decEl);

      placeInOrder(notifsEl, want);

      // The minute labels are retouched in place on their own timer, so the
      // list of them is rebuilt from what is ACTUALLY on screen -- a stack
      // that was left alone still has its own `when` nodes, and they are not
      // the ones notifCard just pushed.
      notifClocks = [];
      var whens = notifsEl.querySelectorAll(".ls-notif-when[data-at]");
      for (var k = 0; k < whens.length; k++) {
        notifClocks.push({ el: whens[k], at: Number(whens[k].getAttribute("data-at")) });
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

    // THE SAFETY NET for data-blanked. If the screen-on event never arrives --
    // a dead stream, a restarted controller -- the page must not be left black
    // on a lit panel, which is indistinguishable from a broken phone. Any real
    // input clears it, and input is exactly what is happening when someone is
    // looking at a screen they expected to be showing something.
    ["touchstart", "keydown", "pointerdown"].forEach(function (evt) {
      document.addEventListener(evt, function () {
        if (screenEl && screenEl.hasAttribute("data-blanked")
            && !screenEl.hasAttribute("data-fromdark")) {
          screenEl.removeAttribute("data-blanked");
          setBlack(false);
        }
      }, { passive: true, capture: true });
    });

    // ------------------------------------------------------------------
    // TORCH AND CAMERA, either side of the unlock bar.
    // ------------------------------------------------------------------
    var torchBtn = document.getElementById("ls-torch");
    var cameraBtn = document.getElementById("ls-camera");

    function paintTorch(state) {
      if (!torchBtn) return;
      if (!state || typeof state.on !== "boolean") {
        // No torch on this device. Dimmed and inert rather than removed: a
        // missing control is something the user hunts for, a dimmed one
        // answers the question.
        torchBtn.disabled = true;
        return;
      }
      torchBtn.disabled = false;
      setAttrIfChanged(torchBtn, "aria-pressed", state.on ? "true" : "false");
    }

    if (torchBtn) {
      fetch("/auth/lock-torch", { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(paintTorch)
        .catch(function () { paintTorch(null); });

      torchBtn.addEventListener("click", function () {
        var want = torchBtn.getAttribute("aria-pressed") !== "true";
        // Optimistic, then corrected by the read-back: an LED is instant, so
        // waiting for the round trip would make a real control feel dead.
        setAttrIfChanged(torchBtn, "aria-pressed", want ? "true" : "false");
        fetch("/auth/lock-torch", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ on: want })
        }).then(function (r) { return r.ok ? r.json() : null; })
          // What the LED took, not what was asked -- the level is clamped well
          // below maximum, so the answer is not always the question.
          .then(function (d) { if (d) paintTorch(d); })
          .catch(function () { paintTorch(null); });
      });
    }

    if (cameraBtn) {
      // There IS a camera app now: taos-camerad serves it and taos-app-launch
      // opens it in its own window, on its own workspace, so it covers the
      // lock screen the way a camera shortcut does on any phone.
      //
      // The honest note stays for the FAILURE path, and for the same reason it
      // existed when there was no app at all: a shortcut that silently does
      // nothing is worse than one that admits it, because the user retries it.
      cameraBtn.addEventListener("click", function () {
        function note(text) {
          cameraBtn.setAttribute("data-note", text);
          window.setTimeout(function () {
            cameraBtn.removeAttribute("data-note");
          }, 1800);
        }
        fetch("/auth/lock-app", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ app: "camera" })
        })
          .then(function (r) { return r.json().catch(function () { return {}; }); })
          .then(function (d) {
            // The window takes a couple of seconds to map, so there is nothing
            // to show on success -- it simply appears over this screen.
            // A friendly line, never the raw launch error.
            if (!d || !d.ok) note("The camera couldn't open. Try again.");
          })
          .catch(function () { note("The camera couldn't open. Try again."); });
      });
    }

    // ------------------------------------------------------------------
    // THE VOLUME KEYS. Jay's spec, and all of the policy lives here because it
    // is all STATE -- the compositor only reports press and release.
    //
    //   up   from rest -> reveal the bezel. THE FIRST PRESS DOES NOT CHANGE THE
    //                     VOLUME. That is the point of it: on a phone with no
    //                     on-screen volume, the first press today changes a
    //                     level you cannot see. This makes the first press the
    //                     one that shows you what you are about to change.
    //   down from rest -> slide the agent carousel out of the LEFT edge.
    //   then           -> up/down move whichever surface is open.
    //   HOLD           -> walkie-talkie with the focused agent. MOCK: Jay,
    //                     "just for demo/mock purposes for now so we can play
    //                     around with designs and testing". No mic is opened,
    //                     nothing is recorded, nothing is sent.
    // ------------------------------------------------------------------
    var volEl = document.getElementById("ls-vol");
    var volFill = document.getElementById("ls-vol-fill");
    var volNum = document.getElementById("ls-vol-num");
    var volNote = document.getElementById("ls-vol-note");
    var carEl = document.getElementById("ls-carousel");
    var carStrip = document.getElementById("ls-carousel-strip");
    var carName = document.getElementById("ls-carousel-name");
    var carRole = document.getElementById("ls-carousel-role");
    var carPtt = document.getElementById("ls-carousel-ptt");

    var volPct = 50;          // last known level
    var volArmed = false;     // has the reveal press been spent?
    var volHideTimer = null;
    var carIndex = 0;
    var holdTimer = null;
    var talking = false;
    // Set when a press OPENS a surface, so that press's own release does not
    // then act on what it just opened. Jay: "the first click of the volume down
    // should not rotate the menu just make it appear" -- without this, the
    // release of the opening press sees an open arc and cycles it, so the arc
    // appeared already one agent along.
    var pressOpened = false;
    // Set when the hold timer fires, so the RELEASE can tell a tap from a hold.
    // Without it, a hold that did not manage to start talking -- the arc closed
    // under it, say -- would be read as a tap and advance the selection.
    var pressWasHold = false;
    // 600ms: past a deliberate press, short enough that holding to talk feels
    // immediate rather than like waiting for the phone to agree.
    //
    // Named for push-to-talk, and deliberately NOT the island press machinery's
    // own hold constant further down. test_lock_screen_views.py locates that
    // machinery by searching for its declaration, so a second declaration of
    // the same name earlier in the script makes it slice from here instead --
    // "SyntaxError: Unexpected end of input", five tests red, in code that was
    // itself perfectly valid. A landmark another file navigates by is part of
    // the interface whether it was meant to be or not, and that includes
    // repeating it in a comment: the first version of this note spelled the
    // other name out and broke the search all over again.
    var PTT_HOLD_MS = 600;

    function volShow() {
      if (!volEl) return;
      volEl.setAttribute("data-on", "1");
      restartIdleHide();
    }

    function hideAll() {
      // READ EVERYTHING FIRST, THEN TEAR DOWN.
      //
      // This function used to remove data-on at the top and then ask, further
      // down, whether data-on was set -- so the branch that records the parked
      // agent could never run. Jay: "the last used agent isnt always the first
      // one in the list." The fix for that was live code that never executed,
      // and the test asserting its order was satisfied by text that could not
      // fire. Presence is not effect, for the second time on this feature.
      var wasOpen = !!(carEl && carEl.getAttribute("data-on") === "1");
      // Darkness is read off the ELEMENT rather than the carDark variable. The
      // attribute is the thing the stylesheet actually acted on, so it cannot
      // disagree with what is on screen, and it cannot be cleared early by some
      // other path resetting a flag.
      // Read from data-fromdark, which BOTH surfaces set, rather than from
      // data-radial, which only the arc does -- that asymmetry is what left a
      // volume-only session on the lock screen.
      var wasDark = !!(screenEl && screenEl.hasAttribute("data-fromdark"));

      // WHERE YOU LEFT IT COUNTS AS USING IT -- recorded before anything is
      // dismantled, and only when the arc was genuinely open: hideAll also runs
      // for the volume bezel, which has no focused agent.
      if (wasOpen) {
        var parked = carAgents()[carIndex];
        if (parked && parked.name) {
          carUsed[parked.name] = Date.now();
          carFocusName = parked.name;
          carSave();
        }
      }

      if (volEl) volEl.removeAttribute("data-on");
      if (carEl) { carEl.removeAttribute("data-on"); carEl.removeAttribute("data-talking"); }
      // Let the next open re-arrange. Held only while the arc is visible.
      carOrder = null;

      // THE PANEL WAS DARK WHEN THIS STARTED, so put it back to dark rather
      // than revealing a lock screen nobody asked for. Jay: "if i PRESS the
      // volume down to reveal the menu but dont use it, it then leaves me on
      // the lock screen. the screen should be off."
      //
      // Keeping the page black is how that is done without the page needing a
      // way to power the panel down, which it has no business having. On OLED a
      // black frame emits nothing, so it reads as off, and swayidle blanks the
      // panel properly a moment later -- the volume key re-armed it, so the
      // timer is running.
      if (screenEl) {
        screenEl.removeAttribute("data-radial");
        screenEl.removeAttribute("data-fromdark");
        if (wasDark) { screenEl.setAttribute("data-blanked", "1"); setBlack(true); }
        else { screenEl.removeAttribute("data-blanked"); setBlack(false); }
      }
      carDark = false;
      if (scrim) {
        scrim.removeAttribute("data-on");
        // Only take the scrim away if nothing ELSE is using it. A sheet keeps it
        // up through its own rule, and hiding the element here would pull the
        // dim out from under an open menu.
        var sheetNow = screenEl ? screenEl.getAttribute("data-sheet") : "none";
        if (!sheetNow || sheetNow === "none") scrim.hidden = true;
      }
      volArmed = false;
      talking = false;
    }

    function restartIdleHide() {
      if (volHideTimer) window.clearTimeout(volHideTimer);
      // Long enough to nudge the level twice without it vanishing between
      // presses, short enough that it is gone before it becomes clutter.
      volHideTimer = window.setTimeout(hideAll, 2600);
    }

    function paintVolume(d) {
      if (!d || typeof d.percent !== "number") return;
      volPct = Math.round(d.percent);
      if (volFill) volFill.style.setProperty("--ls-vol", volPct + "%");
      setText(volNum, volPct + "%");
      // Said on the glass, because it is the difference between a broken
      // slider and an honest one: PipeWire answers, and has no sink behind it.
      if (volNote) {
        if (d.no_sink) {
          setText(volNote, "No audio output");
          volNote.hidden = false;
        } else {
          volNote.hidden = true;
        }
      }
    }

    function loadVolume() {
      fetch("/auth/lock-volume", { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { if (d) paintVolume(d); })
        .catch(function () { /* the bezel keeps its last reading */ });
    }

    function nudgeVolume(delta) {
      volPct = Math.max(0, Math.min(100, volPct + delta));
      if (volFill) volFill.style.setProperty("--ls-vol", volPct + "%");
      setText(volNum, volPct + "%");
      fetch("/auth/lock-volume", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ percent: volPct })
      }).then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { if (d) paintVolume(d); })
        .catch(function () { /* the bar has already moved; leave it */ });
    }

    // WHAT THE ARC REMEMBERS, and where.
    //
    // Jay: "we need the rotary chooser to remember its position, so a person
    // can leave their most used agent ready in walking talkie mode. Might be
    // best to have them auto arrange in order of last used too."
    //
    // Two SEPARATE pieces of state, because they answer different questions:
    //   carFocusName  which agent the arc opens on -- "where I left it"
    //   carUsed       when each agent was last talked to -- the sort order
    // They agree when the agent you parked on is the one you last used, and
    // diverge when you park on one without talking to it. Keeping them apart
    // is what makes that case behave.
    //
    // KEYED BY NAME, NEVER BY INDEX. Agents come and go, and the reordering
    // below moves them, so a remembered index would quietly point at a
    // different face -- exactly the sort of bug that looks like the feature
    // working until you notice it picked the wrong agent.
    //
    // localStorage because the kiosk profile is persistent
    // (--user-data-dir=/var/lib/taos-kiosk/chrome), so it survives a restart
    // without the pre-auth screen needing a write path to the server. Wrapped
    // because it throws in a private context and can come back empty.
    var CAR_STORE = "taos.ls.carousel";
    var carFocusName = null;
    var carUsed = {};
    // Black behind everything. Toggled with the lock screen's own hiding, never
    // separately: two flags for one visual state is how a grey frame gets in.
    // The screen-off / screen-on animation (ls-tv-off / ls-tv-on).
    var TV_MS = 500;
    var tvTimer = 0;
    function tvStop() {
      if (tvTimer) { window.clearTimeout(tvTimer); tvTimer = 0; }
      if (screenEl) {
        screenEl.removeAttribute("data-closing");
        screenEl.removeAttribute("data-opening");
      }
    }

    function setBlack(on) {
      if (document.body) document.body.classList.toggle("ls-black", !!on);
    }

    // Whether this showing of the arc began on a dark panel.
    var carDark = false;
    // The order the arc is CURRENTLY showing. Computed when it opens and held
    // while it is up: re-sorting on every repaint would shuffle the faces under
    // the thumb mid-cycle.
    var carOrder = null;

    try {
      var saved = JSON.parse(window.localStorage.getItem(CAR_STORE) || "{}");
      if (saved && typeof saved === "object") {
        carFocusName = typeof saved.focus === "string" ? saved.focus : null;
        carUsed = (saved.used && typeof saved.used === "object") ? saved.used : {};
      }
    } catch (err) {
      // No memory is a fine state to start in; it just opens on the first agent.
    }

    function carSave() {
      try {
        window.localStorage.setItem(CAR_STORE, JSON.stringify({
          focus: carFocusName, used: carUsed
        }));
      } catch (err) { /* nothing here is worth failing a keypress over */ }
    }

    function carLive() {
      // The islands are the source. The carousel must never show an agent the
      // screen behind it does not, and re-fetching would let the two disagree.
      var out = [];
      if (!agentsEl) return out;
      for (var i = 0; i < agentsEl.children.length; i++) {
        var el = agentsEl.children[i];
        var rec = el.__agent;
        if (rec && rec.name) out.push(rec);
      }
      return out;
    }

    // The order to show, most recently used first. Ties keep the islands' own
    // order, so agents that have never been talked to stay in the arrangement
    // the user already sees behind the arc rather than in an arbitrary one.
    function carArrange() {
      var live = carLive();
      var decorated = live.map(function (agent, index) {
        return { agent: agent, index: index, used: Number(carUsed[agent.name]) || 0 };
      });
      // OLDEST FIRST, MOST RECENT LAST -- and that is deliberate, not a slip.
      //
      // Jay: "the ordering of recently used needs reversing so i can press down
      // to get to my second most used agent quickly using the volume down
      // button." It follows from the two decisions already made: the arc OPENS
      // focused on the most recently used agent, and volume-down DECREMENTS the
      // index. With the most recent at the front, down had nowhere to go but
      // round the back to the least used one.
      //
      // With it at the END, down walks most-used -> second -> third, which is
      // the order someone actually reaches for, and up goes back the way it
      // came. The arrangement on screen is the same ring either way; what
      // changes is which direction the rocker travels through it.
      decorated.sort(function (a, b) {
        if (a.used !== b.used) return a.used - b.used;
        return a.index - b.index;
      });
      return decorated.map(function (d) { return d.agent; });
    }

    function carAgents() {
      // While the arc is open, the order is frozen -- see carOrder. The live
      // list is still consulted for agents that APPEARED or LEFT, so a poll
      // adding an agent does not leave a gap in the ring.
      var live = carLive();
      if (!carOrder) return carArrange();
      var names = {};
      for (var i = 0; i < live.length; i++) names[live[i].name] = live[i];
      var out = [];
      for (var j = 0; j < carOrder.length; j++) {
        var kept = names[carOrder[j].name];
        if (kept) { out.push(kept); delete names[kept.name]; }
      }
      // Anything new goes on the end rather than reshuffling what is on screen.
      for (var k = 0; k < live.length; k++) {
        if (names[live[k].name]) out.push(live[k]);
      }
      return out;
    }

    function paintCarousel() {
      var list = carAgents();
      if (!carStrip) return;
      if (!list.length) {
        setText(carName, "No agents");
        setText(carRole, "");
        return;
      }
      if (carIndex >= list.length) carIndex = 0;
      if (carIndex < 0) carIndex = list.length - 1;
      var want = [];
      // The arc rotates under a fixed pointer rather than a marker moving along
      // it: the focused face is always at 0deg -- straight out from the pivot,
      // level with the thumb -- and cycling swings the others past it. A marker
      // that travelled instead would walk the selection away from the button
      // the thumb is resting on, which is the one thing this layout is for.
      var STEP = 34;        // degrees between faces
      var SPAN = 2;         // how many either side stay visible
      for (var i = 0; i < list.length; i++) {
        var agent = list[i];
        var face = partOf(carStrip, agent.name, "ls-face");
        if (agent.avatar) {
          // Sanitised with split/join rather than a REGEX LITERAL. A regex
          // containing a quote breaks the JS extractor the tests use to lift
          // functions out of this script: it is quote-aware but not
          // regex-aware, so the quote inside the literal opens a string that
          // never closes and the capture runs off the end of the file.
          var safe = String(agent.avatar).split("\"").join("").split("\\").join("");
          var url = "url(\"" + safe + "\")";
          if (face.style.getPropertyValue("background-image") !== url) {
            face.style.setProperty("background-image", url);
          }
        } else {
          setText(face, (agent.name || "?").slice(0, 2));
        }
        // Offset from the focused one, wrapped the SHORT way round so a list of
        // six does not send a face the long way across the arc when the
        // selection passes the end.
        var off = i - carIndex;
        if (off > list.length / 2) off -= list.length;
        if (off < -list.length / 2) off += list.length;
        var away = Math.abs(off);
        // NEGATED: a lower index -- a more recently used agent -- sits BELOW
        // the pointer, so the face that volume-down brings up to the pointer is
        // the one that was visually below it. With the old sign, down moved the
        // ring toward older agents while the faces travelled the other way.
        face.style.setProperty("--a", (-off * STEP) + "deg");
        face.style.setProperty("--s", off === 0 ? "1.1" : (away === 1 ? "0.86" : "0.7"));
        // Past the span they fade out entirely rather than piling up behind the
        // visible ones, where they would show as a smudge on the arc.
        face.style.setProperty("--o", away === 0 ? "1"
          : (away <= SPAN ? String(0.62 - (away - 1) * 0.22) : "0"));
        setAttrIfChanged(face, "data-focus", off === 0 ? "1" : "0");
        want.push(face);
      }
      placeInOrder(carStrip, want);
      var focused = list[carIndex];
      setText(carName, focused.name || "");
      setText(carRole, focused.framework || focused.status || "agent");
      if (!talking) setText(carPtt, "Hold a volume key to talk");
    }

    function carShow() {
      if (!carEl) return;
      // Arrange ONCE, here. Held for as long as the arc is up so the faces do
      // not shuffle under the thumb between one key press and the next.
      carOrder = carArrange();
      // Open where it was left. By name, so a reorder or a departed agent
      // cannot leave this pointing at the wrong face; if that agent is gone,
      // fall back to the front of the arc rather than an arbitrary index.
      carIndex = 0;
      if (carFocusName) {
        for (var i = 0; i < carOrder.length; i++) {
          if (carOrder[i].name === carFocusName) { carIndex = i; break; }
        }
      }
      paintCarousel();
      carEl.setAttribute("data-on", "1");
      // Blur and dim what is behind, so the faces read against the feed --
      // or hide it outright when the arc was summoned onto a dark screen,
      // which on OLED means the faces float on real black.
      if (screenEl) screenEl.setAttribute("data-radial", carDark ? "dark" : "1");
      if (carDark) setBlack(true);
      if (scrim) { scrim.hidden = false; scrim.setAttribute("data-on", "1"); }
      restartIdleHide();
    }

    function armTalk() {
      pressWasHold = true;
      startTalking();
    }

    // Where the arc was left. Read back off the painted list rather than from
    // carIndex alone, so a wrap-around or a changed list cannot record a name
    // that is not the one under the pointer.
    function rememberFocus() {
      var list = carAgents();
      if (!list.length) return;
      var at = carIndex;
      if (at < 0) at = list.length - 1;
      if (at >= list.length) at = 0;
      var focused = list[at];
      if (focused && focused.name && focused.name !== carFocusName) {
        carFocusName = focused.name;
        carSave();
      }
    }

    function startTalking() {
      if (!carEl || carEl.getAttribute("data-on") !== "1") return;
      talking = true;
      carEl.setAttribute("data-talking", "1");
      // Talking to an agent is what "last used" MEANS, so it is recorded here
      // and not on mere focus: cycling past six agents to reach one would
      // otherwise rewrite the whole order on the way.
      var list = carAgents();
      var focused = list[carIndex];
      if (focused && focused.name) {
        carUsed[focused.name] = Date.now();
        carFocusName = focused.name;
        carSave();
      }
      // MOCK. No getUserMedia, no recorder, no upload. The on-screen words
      // read as a finished product (product owner: remove any demo text); the
      // absence of a microphone is asserted by the tests, not by a label.
      setText(carPtt, "Talking…");
      if (volHideTimer) window.clearTimeout(volHideTimer);
    }

    function stopTalking() {
      if (!talking) return;
      talking = false;
      if (carEl) carEl.removeAttribute("data-talking");
      setText(carPtt, "Sent");
      restartIdleHide();
    }

    function volumeKey(key, action, fromDark) {
      // Never over the passcode: a volume nudge must not cover the keypad
      // someone is typing a PIN into.
      var sheet = screenEl ? screenEl.getAttribute("data-sheet") : "none";
      if (sheet && sheet !== "none") return;

      var carOpen = carEl && carEl.getAttribute("data-on") === "1";
      var volOpen = volEl && volEl.getAttribute("data-on") === "1";

      if (action === "press") {
        if (holdTimer) { window.clearTimeout(holdTimer); holdTimer = null; }
        pressWasHold = false;
        pressOpened = false;

        if (!carOpen && !volOpen) {
          // From rest: which key was pressed decides which surface appears.
          // This press is spent on APPEARING -- its release must not also act.
          pressOpened = true;
          // SUMMONED FROM A DARK PANEL -- recorded for either surface, not just
          // the arc. Jay: "the same after changing the volume with the screen
          // off, when the volume slider goes away im left at the lock screen
          // instead of screen off." The bezel never set data-radial, so the
          // close read "not dark" and revealed the lock screen -- and the
          // screen-on handler, which only skipped clearing for the ARC, had
          // already revealed it the moment the panel woke.
          //
          // On the element rather than in a variable, because two separate
          // handlers need the answer and the attribute is the one thing that
          // cannot drift from what is on screen.
          // THE RULE, in Jay's words: "if opened from standby, back to
          // standby." And standby means THE PAGE WAS BLACK, not that the panel
          // was powered down.
          //
          // Those come apart, which is why this was intermittent. After a dark
          // session the page is left black while the panel is still ON --
          // swayidle has not reached its timeout yet. A second summon inside
          // that window asked the compositor, got "panel is on", and treated it
          // as an awake summon: the arc opened blurred over the lock screen and
          // the close revealed it. Jay: "sometimes ... im still being sent to
          // the lock screen instead of screen off."
          //
          // So the PAGE's own state decides, and the compositor's hint is only
          // a fallback -- it still matters for the first summon after a
          // controller restart, when the page has never seen a screen-off.
          var dark = !!fromDark
            || !!(screenEl && screenEl.hasAttribute("data-blanked"));
          if (screenEl) {
            if (dark) screenEl.setAttribute("data-fromdark", "1");
            else screenEl.removeAttribute("data-fromdark");
          }
          if (key === "up") { loadVolume(); volShow(); volArmed = true; return; }
          // Opened from a dark panel: the arc goes over black, not over the
          // whole lock screen. Jay: "it will look nice against the black oled
          // screen". The compositor woke the panel before telling us.
          carDark = dark;
          // carIndex is NOT reset here: carShow() restores where the arc was
          // left, which is the whole point of remembering it. Zeroing it first
          // would make every open land on the front regardless.
          carShow();
          // Holding down from rest opens the arc and then talks to whoever is
          // focused, which is what "press down then hold" should naturally do.
          holdTimer = window.setTimeout(armTalk, PTT_HOLD_MS);
          return;
        }

        restartIdleHide();
        if (carOpen) {
          // ⚠ CYCLING HAPPENS ON RELEASE, NOT HERE. Jay, from the glass:
          // "holding to talk doesnt work, it moves to the next agent and then
          // starts input capture" -- because this branch used to advance the
          // selection on the press and the hold timer then fired on top of it.
          // A press cannot be classified until it ends, so the only thing a
          // press may do here is start the clock.
          holdTimer = window.setTimeout(armTalk, PTT_HOLD_MS);
          return;
        }

        // The bezel is showing, so the keys move the level. Nudged on PRESS
        // rather than release: hold has no second meaning here, and a volume
        // key that waited for the release would feel laggy in the one place
        // people expect it to be immediate.
        nudgeVolume(key === "up" ? 5 : -5);
        return;
      }

      // RELEASE. This is where a press gets classified.
      if (holdTimer) { window.clearTimeout(holdTimer); holdTimer = null; }

      if (talking) {          // it was a hold: end the transmission, do not cycle
        stopTalking();
        return;
      }
      if (pressWasHold) {     // the hold fired but talking did not take
        pressWasHold = false;
        return;
      }
      if (pressOpened) {      // this press made the surface appear; that is all
        pressOpened = false;
        return;
      }
      if (carOpen) {          // a genuine tap: move one agent
        // DOWN walks TOWARD the front of the arc, which is the most recently
        // used agent. Jay: "currently to go to the last used agent on the
        // rotary I have to press volume up, can you change it so it's on
        // volume down." The arc's angles are flipped to match (see
        // paintCarousel), so "down" still means down on the glass -- swapping
        // the keys alone would have made the ring travel the wrong way.
        carIndex += (key === "up" ? 1 : -1);
        paintCarousel();
        rememberFocus();
        restartIdleHide();
      }
    }

    // ------------------------------------------------------------------
    // THE PULL-DOWN SHADE. Swipe down from the TOP EDGE.
    //
    // Jay: "We need a pull down area from the top of the screen for things like
    // brightness, implement brightness controls asap". Brightness is the one
    // setting that plainly belongs on a PRE-AUTH screen: someone holding an
    // unreadably dim phone has to be able to fix it before they can read the
    // PIN prompt.
    // ------------------------------------------------------------------
    var shadeEl = document.getElementById("ls-shade");
    var brightEl = document.getElementById("ls-brightness");
    var brightValEl = document.getElementById("ls-brightness-value");
    var shadeNote = document.getElementById("ls-shade-note");

    // The radio switches. Built once and then only their pressed state changes,
    // like everything else on this screen.
    var togglesEl = document.getElementById("ls-shade-toggles");
    var RADIO_GLYPHS = {
      wifi: '<path d="M2.6 9.2a14 14 0 0 1 18.8 0"/><path d="M5.8 12.6a9.4 9.4 0 0 1 12.4 0"/>'
          + '<path d="M9 16a4.8 4.8 0 0 1 6 0"/><path d="M12 19.4v0"/>',
      bluetooth: '<path d="M7.5 7.6 16.5 16 12 20V4l4.5 4-9 8.4"/>'
    };
    var RADIOS = [["wifi", "Wi‑Fi"], ["bluetooth", "Bluetooth"]];

    function paintRadios(state) {
      if (!togglesEl) return;
      var want = [];
      for (var i = 0; i < RADIOS.length; i++) {
        (function (key, label) {
          var btn = partOf(togglesEl, key, "ls-toggle", "button");
          if (btn.type !== "button") {
            btn.type = "button";
            btn.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true">'
              + RADIO_GLYPHS[key] + "</svg>";
            var cap = document.createElement("span");
            cap.textContent = label;
            btn.appendChild(cap);
            btn.addEventListener("click", function () {
              // Read the CURRENT pressed state rather than a captured one: the
              // handler outlives many repaints, and a stale closure would send
              // the same request forever.
              var now = btn.getAttribute("aria-pressed") === "true";
              setRadio(key, !now, btn);
            });
          }
          // Unknown (the reading failed) is NOT the same as off. The button is
          // disabled rather than shown convincingly in a state nobody measured.
          if (typeof state[key] === "boolean") {
            btn.disabled = false;
            setAttrIfChanged(btn, "aria-pressed", state[key] ? "true" : "false");
          } else {
            btn.disabled = true;
            setAttrIfChanged(btn, "aria-pressed", "false");
          }
          want.push(btn);
        })(RADIOS[i][0], RADIOS[i][1]);
      }
      placeInOrder(togglesEl, want);
    }

    function setRadio(key, on, btn) {
      // Optimistic, then corrected by the read-back. A radio takes a moment to
      // come up and a switch that did not move until it had would feel broken.
      setAttrIfChanged(btn, "aria-pressed", on ? "true" : "false");
      btn.setAttribute("data-busy", "1");
      fetch("/auth/lock-radios", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ radio: key, on: on })
      }).then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          btn.removeAttribute("data-busy");
          // The server reports what the RADIO did, not what was asked. If it
          // refused, the switch goes back -- which is the only honest thing a
          // switch can do.
          if (d) paintRadios(d);
        })
        .catch(function () {
          btn.removeAttribute("data-busy");
          loadRadios();
        });
    }

    function loadRadios() {
      fetch("/auth/lock-radios", { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { if (d) paintRadios(d); })
        .catch(function () { /* leave the switches as they are */ });
    }

    function showBrightness(reading) {
      if (!reading || typeof reading.percent !== "number") return;
      var pct = Math.round(reading.percent);
      // Only write the input's value when the user is NOT dragging it: a poll
      // landing mid-drag would yank the thumb back under the finger.
      if (document.activeElement !== brightEl) brightEl.value = String(pct);
      setText(brightValEl, pct + "%");
    }

    function loadBrightness() {
      fetch("/auth/lock-brightness", { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (d) return showBrightness(d);
          // 404 is the honest answer on a device with no backlight node.
          if (shadeNote) {
            setText(shadeNote, "Brightness is fixed on this display.");
            shadeNote.hidden = false;
          }
          if (brightEl) brightEl.disabled = true;
        })
        .catch(function () { /* leave the slider where it is */ });
    }

    // Throttled, not debounced. A drag fires `input` continuously and every one
    // of those is a sysfs write; throttling keeps the panel following the finger
    // (which debouncing would not), while capping the writes.
    var brightPending = null;
    var brightTimer = null;

    function pushBrightness() {
      brightTimer = null;
      if (brightPending === null) return;
      var want = brightPending;
      brightPending = null;
      fetch("/auth/lock-brightness", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ percent: want })
      }).then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          // The server reports what the PANEL took, not what was asked for --
          // the write's own result is not trustworthy on this hardware, and the
          // level is clamped away from full dark. Showing the read-back is how
          // the slider stops lying about a floor it cannot go below.
          if (d) setText(brightValEl, Math.round(d.percent) + "%");
        })
        .catch(function () { /* the panel is the feedback */ });
    }

    if (brightEl) {
      brightEl.addEventListener("input", function () {
        var pct = Number(brightEl.value);
        setText(brightValEl, pct + "%");
        brightPending = pct;
        if (!brightTimer) brightTimer = window.setTimeout(pushBrightness, 90);
      });
    }

    function openShade() {
      loadBrightness();
      loadRadios();
      openSheet("shade");
    }

    if (shadeEl) {
      // Down from the TOP EDGE only. The veto is what keeps this off the feed:
      // a downward drag anywhere else is a scroll, and stealing it would make
      // the panels unusable. 90px is a thumb's reach from the edge, measured
      // against the 540px-wide CSS viewport this device renders at.
      swipe(document.body, null, function () {
        if (screenEl && screenEl.getAttribute("data-sheet") === "none") openShade();
      }, null, function (ev) {
        var t = ev.touches[0];
        // Nor may a drag on the call card pull the shade down over it.
        if (ev.target && ev.target.closest && ev.target.closest(".ls-call")) return true;
        return !t || t.clientY > 90;      // vetoed unless it began up top
      });

      // Swipe back up, or tap the dimmed screen behind it, to put it away.
      swipe(shadeEl, function () { closeSheet(); }, null);
      shadeEl.addEventListener("click", function (ev) {
        if (ev.target === shadeEl) closeSheet();
      });
    }

    // ------------------------------------------------------------------
    // THE POWER MENU. Raised by HOLDING the power key, not by anything on
    // screen: sway owns that key and posts to /auth/lock-power-menu, which
    // arrives here over /auth/lock-events.
    //
    // A push, not a poll. The key is a physical button, so the menu has to be
    // up by the time the thumb lifts; this screen's fastest poll is 3s.
    // ------------------------------------------------------------------
    var powerSheet = document.getElementById("ls-power");
    var powerBody = document.getElementById("ls-power-body");
    var powerSub = document.getElementById("ls-power-sub");

    // label, verb, glyph, note, and whether Jay asked for a confirm step.
    var POWER_ITEMS = [
      ["Power off", "poweroff", "⏻", "", true],
      ["Restart", "reboot", "↻", "", true],
      ["Stop all agents", "stop-agents", "■", "Halts every running agent", true],
      ["Screenshot", "screenshot", "⌘", "Saves to the device", false],
      ["Emergency call", "emergency", "✢", "", true]
    ];

    function powerResult(text) {
      var el = document.createElement("div");
      el.className = "ls-power-result";
      el.textContent = text;                       // textContent, never innerHTML
      powerBody.appendChild(el);
    }

    function runPowerAction(verb, label) {
      fetch("/auth/lock-power-action", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: verb })
      }).then(function (r) { return r.json().catch(function () { return {}; }); })
        .then(function (d) {
          // Power off and restart never come back -- the phone is going down,
          // and a "done" message would be a lie either way.
          if (verb === "poweroff" || verb === "reboot") return;
          if (d && d.ok) {
            // No file path on the glass: it is an internal directory, not
            // somewhere the person holding the phone can go.
            powerResult(verb === "screenshot" ? "Screenshot saved." : label + ": done.");
          } else {
            // The failure TEXT, not a generic apology: "no supported format
            // found" is the difference between a bug report and a shrug, and
            // screenshot genuinely does fail on this compositor today.
            // A sentence a viewer can act on, never a raw exception or an
            // HTTP status. `message` is the server's own wording when it has
            // one worth saying (the emergency case); `detail` stays for logs.
            powerResult((d && d.message) || (label + " didn't go through. Try again."));
          }
        })
        .catch(function () { powerResult(label + " didn't go through. Try again."); });
    }

    function paintPowerMenu() {
      if (!powerBody) return;
      powerBody.textContent = "";
      for (var i = 0; i < POWER_ITEMS.length; i++) {
        (function (item) {
          var btn = document.createElement("button");
          btn.type = "button";
          btn.className = "ls-power-item";
          // Red: the three that cost something. Emergency call is red because
          // that is what it is FOR -- on every phone it is the one control you
          // should be able to find without reading, and colour is how.
          if (item[1] === "poweroff" || item[1] === "stop-agents"
              || item[1] === "emergency") {
            btn.setAttribute("data-danger", "1");
          }
          if (item[1] === "emergency") btn.setAttribute("data-emergency", "1");
          var glyph = document.createElement("span");
          glyph.className = "ls-power-glyph";
          glyph.setAttribute("aria-hidden", "true");
          glyph.textContent = item[2];
          var text = document.createElement("span");
          text.textContent = item[0];
          if (item[3]) {
            var note = document.createElement("span");
            note.className = "ls-power-note";
            note.textContent = item[3];
            text.appendChild(note);
          }
          btn.appendChild(glyph);
          btn.appendChild(text);
          btn.addEventListener("click", function () {
            if (!item[4]) return runPowerAction(item[1], item[0]);
            confirmPower(btn, item);
          });
          powerBody.appendChild(btn);
        })(POWER_ITEMS[i]);
      }
    }

    // THE PASSCODE GATE ON "STOP ALL AGENTS". Jay ruled it, and the shape is
    // the one this screen already uses twice: the agent menu "collects the
    // INTENT and then asks for the passcode", and the decision sheet says
    // "Unlock to approve this". This is that rule applied a third time rather
    // than a new one -- stopping a SINGLE agent already demanded an unlock, and
    // stopping all of them was the one place the rule was not applied.
    //
    // The verb is GATED, NOT REMOVED: _POWER_ACTIONS still carries it and
    // /auth/lock-power-action still answers it once a session exists. The gate
    // belongs in the page, which is where the locked screen is.
    //
    // Power off and restart stay pre-auth on their own argument: holding the
    // hardware key already took the phone down from this state, so the menu
    // adds no capability there. The key cannot drain every agent on the device.
    // That asymmetry IS the reason this is the one verb.
    //
    // Its own store rather than __lsPendingAgentAction: that one is keyed on an
    // agent, and this is the verb that is about all of them.
    window.__lsPendingPowerAction = null;

    function requirePasscodeForPower(item) {
      window.__lsPendingPowerAction = { action: item[1], at: Date.now() };
      // NOT closeSheet() first: openSheet already hides whatever sheet is up
      // before revealing the next, and closing first would leave closeSheet's
      // 400ms hide to find data-sheet already "passcode" and bail -- the power
      // sheet would stay in the tree, behind the keypad.
      var note = document.getElementById("ls-unlock-note");
      if (note) {
        // What they are unlocking FOR. An unexplained keypad straight after a
        // menu tap reads as the phone having simply re-locked itself.
        note.textContent = "Unlock to stop all agents";
        note.hidden = false;
      }
      openPasscode();
    }

    // Replace the row with its own question. Jay: "stop all agents and
    // emergency call needs confirmation".
    function confirmPower(btn, item) {
      var box = document.createElement("div");
      box.className = "ls-power-confirm";
      var q = document.createElement("div");
      q.className = "ls-power-confirm-q";
      q.textContent = item[0] + "?";
      var note = document.createElement("div");
      note.className = "ls-power-confirm-note";
      // Jay asked for the shutdown button to confirm too, after tapping it by
      // accident while testing. Restart gets the same treatment: on a phone
      // being demoed, an accidental restart costs the same minute.
      var NOTES = {
        "poweroff": "The phone switches off. It needs the power key to come back.",
        "reboot": "The phone restarts. Agents stop and come back with it.",
        "stop-agents": "Every running agent stops. Nobody is signed in, so this cannot be undone from here.",
        "emergency": "This phone can't place calls. Use another phone to reach emergency services."
      };
      note.textContent = NOTES[item[1]] || "";
      var row = document.createElement("div");
      row.className = "ls-power-confirm-row";
      var no = document.createElement("button");
      no.type = "button";
      no.textContent = "Cancel";
      var yes = document.createElement("button");
      yes.type = "button";
      yes.setAttribute("data-go", "1");
      yes.textContent = item[0];
      no.addEventListener("click", paintPowerMenu);
      yes.addEventListener("click", function () {
        box.remove();
        // JAY'S RULING: "Stop all agents" demands the passcode. It is the one
        // verb here that moves, and see requirePasscodeForPower for why.
        if (item[1] === "stop-agents") {
          // Put the menu back first: the confirm REPLACED this row, so leaving
          // it removed would mean the next time the power key is held the menu
          // is one item short.
          paintPowerMenu();
          requirePasscodeForPower(item);
          return;
        }
        runPowerAction(item[1], item[0]);
      });
      row.appendChild(no); row.appendChild(yes);
      box.appendChild(q); box.appendChild(note); box.appendChild(row);
      btn.replaceWith(box);
    }

    // ONE stream for the whole page, created on first ask.
    //
    // It used to be created inside `if (powerSheet)`, which quietly made every
    // other push on this screen -- screen state, volume keys, now the charger
    // -- depend on the power menu's markup being present. A listener that has
    // to work regardless asks here instead, and gets the same EventSource.
    var lockStreamShared = null;
    function lockEvents() {
      if (lockStreamShared) return lockStreamShared;
      if (typeof EventSource === "undefined") return null;
      try { lockStreamShared = new EventSource("/auth/lock-events"); }
      catch (err) { lockStreamShared = null; }
      return lockStreamShared;
    }

    if (powerSheet) {
      var powerClose = document.getElementById("ls-power-close");
      if (powerClose) powerClose.addEventListener("click", function () { closeSheet(); });

      // EventSource reconnects on its own after a drop, which matters here:
      // the controller restarts on every deploy and the page does not.
      try {
        var lockStream = lockEvents();
        // The panel is going dark. Put the sheet away NOW rather than leaving
        // it up behind a black screen for the next wake to land on.
        lockStream.addEventListener("screen-off", function () {
          if (!screenEl) return;
          var open = screenEl.getAttribute("data-sheet");
          // The passcode sheet is deliberately left alone: the panel blanking
          // on a timeout must not throw away a half-typed PIN.
          if (open !== "power" && open !== "shade") return;
          // CLOSE IT WITHOUT ANIMATING. Jay: "when I turn the screen back on I
          // see the menu close, it needs close when the screen turns off".
          // The close already fires at screen-off -- but the panel is powering
          // down, nothing is compositing, and the 340ms transition has nowhere
          // to run. It then plays on wake, so the menu appears to close half a
          // second after the screen returns. Suppressing the transition makes
          // the close land while the screen is dark, which is the only place it
          // can be invisible.
          screenEl.setAttribute("data-instant", "1");
          closeSheet();
        });

        // BLACKEN BEFORE THE PANEL GOES DOWN.
        //
        // Jay, twice: "the lock screen still flashes into view first." The
        // first fix told the page before waking the panel, which was the wrong
        // half of the problem -- THE PAGE CANNOT PAINT WHILE THE OUTPUT IS OFF.
        // Wayland stops delivering frame callbacks to a surface on a
        // powered-down output, which is the same reason the power menu's close
        // animation used to play on WAKE rather than while dark. So the DOM
        // change landed and the panel lit showing the stale frame still in the
        // scanout buffer: the lock screen exactly as it was when the screen
        // went off.
        //
        // The only frame that can be on a waking panel is the last one painted
        // BEFORE it blanked. So that frame is made black here, while there is
        // still a compositor listening.
        lockStream.addEventListener("screen-off", function () {
          if (!screenEl) return;
          // TAKE THE VOLUME SURFACES DOWN TOO, and without a fade.
          //
          // Jay: "if i change volume with screen off after using the rotary
          // menu the rotary menu flashes up first and vice versa." The
          // symmetry -- whichever was used LAST is what flashes -- is the tell:
          // it is the scanout buffer again.
          //
          // data-blanked hides .lockscreen, but the bezel and the arc are
          // SIBLINGS of it, not children. So a panel that blanked while one of
          // them was up left a last painted frame of black WITH that surface
          // still on it, and the next wake showed it before the new surface
          // could paint. Hiding the lock screen was never going to reach them.
          //
          // data-instant first, so their transitions do not run: a 200ms fade
          // has nowhere to go on a panel that is powering down in 120ms, and
          // an unfinished fade is exactly the half-lit ghost being described.
          // Showing the lock screen right now? Then it closes into the centre
          // (see ls-tv-off) instead of vanishing. Read before hideAll, which
          // changes what is showing.
          var closeIt = !screenEl.hasAttribute("data-blanked")
            && !screenEl.hasAttribute("data-fromdark");
          screenEl.setAttribute("data-instant", "1");
          hideAll();
          // After hideAll, which decides blackness for itself and would
          // otherwise clear what is set here.
          setBlack(true);
          tvStop();
          if (!closeIt) {
            screenEl.setAttribute("data-blanked", "1");
            setBlack(true);
          } else {
            screenEl.setAttribute("data-closing", "1");
            tvTimer = window.setTimeout(function () {
              tvTimer = 0;
              screenEl.setAttribute("data-blanked", "1");
              setBlack(true);
              screenEl.removeAttribute("data-closing");
            }, TV_MS);
          }
        });

        // And back. Un-blackened on wake -- but NOT when the arc is up, because
        // that is the case where black is the point.
        lockStream.addEventListener("screen-on", function () {
          if (!screenEl) return;
          // Anything summoned onto a dark panel keeps its black: the arc, and
          // the volume bezel just the same. Asking about the arc alone was what
          // let the lock screen appear behind the slider.
          if (screenEl.hasAttribute("data-fromdark")) return;
          // Out of black: expand from the centre (ls-tv-on), then hand the
          // background back to the body. A wake mid-close cancels the close.
          var openIt = screenEl.hasAttribute("data-blanked")
            || screenEl.hasAttribute("data-closing");
          tvStop();
          screenEl.removeAttribute("data-blanked");
          if (!openIt) { setBlack(false); return; }
          screenEl.setAttribute("data-opening", "1");
          tvTimer = window.setTimeout(function () {
            tvTimer = 0;
            screenEl.removeAttribute("data-opening");
            setBlack(false);
          }, TV_MS + 20);
        });

        // The widgets poll (agent islands, rotating demo tasks) has no
        // reason to run against a dark panel -- pause it there and catch up
        // the instant the screen wakes, same as visibilitychange does for a
        // backgrounded app.
        lockStream.addEventListener("screen-off", function () { pauseWidgetsPoll(); });
        lockStream.addEventListener("screen-on", function () { resumeWidgetsPoll(); });

        // One listener shape for all four, reading the payload rather than
        // relying on the event name to carry the screen state.
        var volKeys = [["up", "press"], ["up", "release"],
                       ["down", "press"], ["down", "release"]];
        for (var vk = 0; vk < volKeys.length; vk++) {
          (function (key, action) {
            lockStream.addEventListener("volume-" + key + "-" + action, function (ev) {
              var data = {};
              try { data = JSON.parse(ev.data || "{}"); } catch (err) { data = {}; }
              volumeKey(key, action, data.screen === "off");
            });
          })(volKeys[vk][0], volKeys[vk][1]);
        }
        lockStream.addEventListener("power-menu", function () {
          paintPowerMenu();
          if (powerSub) {
            powerSub.textContent = new Date().toLocaleTimeString([], {
              hour: "2-digit", minute: "2-digit"
            });
          }
          openSheet("power");
        });
      } catch (err) {
        // No stream means no menu, and the power key still toggles the screen.
        // That is the fallback direction this whole layer is built around.
      }
    }

    // ------------------------------------------------------------------
    // THE CHARGE OVERLAY. Product owner: "a charger connected notification,
    // screen on and animation for 3 seconds ... emulate a claude code session
    // where it shows a percentage and a working message like 'Drinking the
    // juice....'". A coding-agent CLI homage, unbranded.
    //
    // Pushed by /auth/lock-charge over the shared stream. The session watcher
    // posts BEFORE it wakes a dark panel, so when screen == "off" the terminal
    // is painted over pure black first and the waking panel's first frame is
    // the terminal -- the same trick the volume keys use to open the arc from
    // standby (data-fromdark + data-blanked + setBlack), mirrored here.
    // ------------------------------------------------------------------
    var CHARGE_SLOGANS = [
      "Drinking the juice…", "Stealing your power…",
      "Sipping electrons…", "Hoarding amperes…",
      "Borrowing some lightning…", "Recharging my social battery…",
      "Photosynthesising (indoors)…", "Refuelling the agents…",
      "Nom-nom-ing volts…", "Charging my crystals…",
      "Absorbing the grid…", "Topping up the vibes…"
    ];
    var CHARGE_SPIN = ["·", "✢", "✳", "✶", "✻", "✽"];
    var CHARGE_MS = 10000;         // on screen, before the fade starts (Jay: 10 s)
    var CHARGE_FADE_MS = 440;      // .ls-charge opacity transition, plus a frame
    var CHARGE_COUNT_MS = 7500;    // the count-up
    var CHARGE_DONE_MS = 8600;     // the green tick
    var CHARGE_SLOGAN_MS = 1600;
    var CHARGE_SPIN_MS = 120;
    var chargeEl = null, chargeRun = null;

    // The count-up and its 20-cell bar at `t` ms in. Pure, so it is tested
    // directly. Starts 15 points short of the real reading (never below 0) and
    // eases out onto it, so the number lands rather than stops. null for a
    // battery nobody could read: the overlay then shows no number at all.
    function chargeCount(pct, t) {
      if (typeof pct !== "number" || !isFinite(pct)) return null;
      var target = Math.max(0, Math.min(100, Math.round(pct)));
      var from = Math.max(0, target - 15);
      var k = Math.max(0, Math.min(1, (t || 0) / CHARGE_COUNT_MS));
      var eased = 1 - Math.pow(1 - k, 3);
      var shown = Math.round(from + (target - from) * eased);
      var cells = Math.round(shown / 5);
      var fill = "", rest = "";
      for (var i = 0; i < 20; i++) {
        if (i < cells) fill += "█"; else rest += "░";
      }
      return { percent: shown, fill: fill, rest: rest };
    }

    // Ping-pong through the glyphs: · ✢ ✳ ✶ ✻ ✽ ✻ ✶ ✳ ✢ · ...
    function chargeSpinGlyph(frame) {
      var n = CHARGE_SPIN.length, period = 2 * n - 2;
      var i = ((frame % period) + period) % period;
      return CHARGE_SPIN[i < n ? i : period - i];
    }

    // A random slogan that has not been shown this time round. `used` is the
    // list already shown, most recent last; once every slogan has been used it
    // starts again, but never with the one just shown.
    function pickSlogan(list, used, rand) {
      rand = rand || Math.random;
      var last = used.length ? used[used.length - 1] : null;
      var pool = [];
      for (var i = 0; i < list.length; i++) {
        if (used.indexOf(list[i]) === -1) pool.push(list[i]);
      }
      if (!pool.length) {
        for (var j = 0; j < list.length; j++) {
          if (list[j] !== last) pool.push(list[j]);
        }
      }
      if (!pool.length) return list[0];
      return pool[Math.min(pool.length - 1, Math.floor(rand() * pool.length))];
    }

    function chargeReduced() {
      try {
        return !!(window.matchMedia
          && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
      } catch (err) { return false; }
    }

    function chargeBuild() {
      if (chargeEl) return chargeEl;
      var el = document.createElement("div");
      el.className = "ls-charge";
      el.id = "ls-charge";
      el.setAttribute("role", "status");
      el.setAttribute("aria-live", "polite");
      el.hidden = true;
      el.innerHTML =
        '<div class="ls-charge-term">'
        + '<div class="ls-charge-line ls-charge-prompt">~/taos'
        + ' <span class="ls-charge-caret">❯</span> charge</div>'
        + '<div class="ls-charge-line">'
        + '<span class="ls-charge-spin" aria-hidden="true"></span>'
        + '<span class="ls-charge-slogan"></span>'
        + '<span class="ls-charge-dim">(⚡ charging)</span></div>'
        + '<div class="ls-charge-line ls-charge-meter">'
        + '<span class="ls-charge-pct"></span>'
        + '<span class="ls-charge-bar" aria-hidden="true">'
        + '<span class="ls-charge-fill"></span><span class="ls-charge-rest"></span>'
        + '</span></div>'
        + '<div class="ls-charge-line ls-charge-done"></div>'
        + '</div>';
      // A touch ends it early, like any phone's charging splash.
      el.addEventListener("touchstart", function () { chargeFinish(false); },
                          { passive: true });
      el.addEventListener("click", function () { chargeFinish(false); });
      document.body.appendChild(el);
      chargeEl = el;
      return el;
    }

    function chargePaint(run, t) {
      var el = chargeEl;
      if (!el) return;
      var q = function (c) { return el.querySelector("." + c); };
      // The spinner settles on its fullest glyph once the work is "done".
      setText(q("ls-charge-spin"), (run.done || run.reduced)
        ? "✻" : chargeSpinGlyph(Math.floor(t / CHARGE_SPIN_MS)));
      var at = Math.min(run.slogans.length - 1, Math.floor(t / CHARGE_SLOGAN_MS));
      setText(q("ls-charge-slogan"), run.slogans[at]);
      var c = chargeCount(run.percent, run.reduced ? CHARGE_COUNT_MS : t);
      if (c) {
        setText(q("ls-charge-pct"), "⚡ " + c.percent + "%");
        setText(q("ls-charge-fill"), c.fill);
        setText(q("ls-charge-rest"), c.rest);
      } else {
        setText(q("ls-charge-pct"), "⚡ charging");
        setText(q("ls-charge-fill"), "");
        setText(q("ls-charge-rest"), "");
      }
      setText(q("ls-charge-done"), c
        ? "✓ Charging · " + Math.round(Math.max(0, Math.min(100, run.percent))) + "%"
        : "✓ Charging");
    }

    function chargeTick() {
      var run = chargeRun;
      if (!run) return;
      var t = Date.now() - run.start;
      if (!run.done && t >= CHARGE_DONE_MS) {
        run.done = true;
        if (chargeEl) chargeEl.setAttribute("data-done", "1");
      }
      chargePaint(run, t);
      if (t < CHARGE_MS) run.raf = window.requestAnimationFrame(chargeTick);
    }

    // Take it down. `instant` is the screen-off case: nothing is compositing, so
    // a fade would only play on the next wake. `reveal` says whether to bring
    // the lock screen back up afterwards.
    function chargeFinish(instant, reveal) {
      var run = chargeRun;
      if (!run) return;
      chargeRun = null;
      if (run.raf) window.cancelAnimationFrame(run.raf);
      if (run.stop) window.clearTimeout(run.stop);
      var el = chargeEl;
      if (el) el.removeAttribute("data-on");
      var after = function () {
        if (el && !chargeRun) {
          el.hidden = true;
          el.removeAttribute("data-dark");
          el.removeAttribute("data-done");
        }
        // Dark start: the lock screen fades up only once the terminal has
        // gone, out of black -- unless something else summoned from the dark
        // (the arc, the bezel) is now up and owns that black.
        if (run.dark && reveal !== false && screenEl && !chargeRun) {
          var carUp = carEl && carEl.getAttribute("data-on") === "1";
          var volUp = volEl && volEl.getAttribute("data-on") === "1";
          if (!carUp && !volUp) {
            screenEl.removeAttribute("data-fromdark");
            screenEl.removeAttribute("data-blanked");
            setBlack(false);
          }
        }
      };
      if (instant || run.reduced) after();
      else window.setTimeout(after, CHARGE_FADE_MS);
    }

    function chargeShow(data) {
      if (!document.body) return;
      var el = chargeBuild();
      if (chargeRun) chargeFinish(true, false);
      var dark = data.screen === "off"
        || !!(screenEl && screenEl.hasAttribute("data-blanked"));
      var used = [];
      for (var s = 0; s < Math.ceil(CHARGE_MS / CHARGE_SLOGAN_MS); s++) {
        used.push(pickSlogan(CHARGE_SLOGANS, used));
      }
      var run = {
        start: Date.now(), percent: data.percent, dark: dark, done: false,
        reduced: chargeReduced(), slogans: used, raf: 0, stop: 0
      };
      if (dark && screenEl) {
        // What the volume keys do from standby: the lock screen is hidden and
        // the page black BEFORE the panel lights, and data-fromdark tells the
        // screen-on handler to leave that black alone.
        screenEl.setAttribute("data-fromdark", "1");
        screenEl.setAttribute("data-blanked", "1");
        setBlack(true);
        el.setAttribute("data-dark", "1");
      } else {
        el.removeAttribute("data-dark");
      }
      el.removeAttribute("data-done");
      el.hidden = false;
      // Force a style pass at opacity 0, or the fade-in has no start value.
      void el.offsetWidth;
      chargeRun = run;
      chargePaint(run, 0);
      // Next frame, so the opacity transition has a starting value to leave.
      window.requestAnimationFrame(function () {
        if (chargeRun === run) el.setAttribute("data-on", "1");
      });
      run.raf = window.requestAnimationFrame(chargeTick);
      // A timer as well as the frames: a panel that never lights delivers no
      // frames, and the overlay must still go.
      run.stop = window.setTimeout(function () {
        if (chargeRun === run) chargeFinish(false);
      }, CHARGE_MS);
    }

    (function () {
      var stream = lockEvents();
      if (!stream) return;
      stream.addEventListener("charger", function (ev) {
        var data = {};
        try { data = JSON.parse(ev.data || "{}") || {}; } catch (err) { data = {}; }
        chargeShow(data);
      });
      // The panel went dark mid-animation: drop it now, without a fade, and
      // leave the black the screen-off handler paints.
      stream.addEventListener("screen-off", function () {
        if (chargeRun) chargeFinish(true, false);
      });
    })();

    // ------------------------------------------------------------------
    // THE SCRIPTED PANELS: phone, mailbox, apps, projects, decisions.
    //
    // Five more pollers on a screen whose last bug was "every poller that wipes
    // its container and rebuilds makes the whole panel flicker". So not one of
    // these ever wipes. Every row is found by its payload key through partOf(),
    // updated in place through setText()/setAttrIfChanged(), and ordered with
    // placeInOrder() -- a row that persists across a repaint keeps its identity
    // and therefore never replays its entrance animation. That is the property
    // the repaint tests assert, and it is why these were built this way from
    // the first line rather than fixed afterwards five times over.
    //
    // Everything here is scripted demo content served by /auth/lock-panels,
    // which 404s unless the demo flags are on. This screen renders BEFORE
    // sign-in: there is no code path from any of it to a real account.
    // ------------------------------------------------------------------
    var panelEls = {
      phone: document.getElementById("ls-phone"),
      mailbox: document.getElementById("ls-mailbox"),
      apps: document.getElementById("ls-apps"),
      projects: document.getElementById("ls-projects"),
      // Not a panel of its own: this container lives INSIDE the alerts panel,
      // above the notification stacks.
      decisions: document.getElementById("ls-decisions")
    };
    // Every minute label currently on a panel, rebuilt from the DOM after each
    // paint so a row left untouched still gets its minutes retouched.
    var panelClocks = [];
    // User state that must OUTLIVE a repaint: which decisions they have
    // answered. Kept out here for the same reason notifOpen is -- a paint must
    // never undo what the user just did, and a poll lands whatever they are in
    // the middle of.
    var decAnswered = {};

    // The tile every row leads with. Written only when it changes, so a repaint
    // of an unchanged row touches no DOM at all.
    function paintTile(tile, spec) {
      // Only a colour literal is ever taken from the payload, and only after it
      // is checked -- an unchecked value here would be written into a style.
      if (/^#[0-9a-fA-F]{3,8}$/.test(spec.tint || "")
          && tile.style.getPropertyValue("--ls-n") !== spec.tint) {
        tile.style.setProperty("--ls-n", spec.tint);
      }
      var glyph = (spec.glyph && NOTIF_GLYPHS[spec.glyph]) ? spec.glyph : "";
      if (tile.getAttribute("data-glyph") !== glyph) {
        tile.setAttribute("data-glyph", glyph);
        // innerHTML only ever from NOTIF_GLYPHS, which is a literal in this
        // file. The payload chooses a key; it never supplies markup.
        tile.innerHTML = glyph ? '<svg viewBox="0 0 24 24">' + NOTIF_GLYPHS[glyph] + "</svg>" : "";
      }
      if (!glyph) setText(tile, (spec.mono || spec.app || "?").slice(0, 2));
    }

    // The head of a row: tile, APP · when, title, and up to two sub-lines.
    //
    // `extra` is a function given the body element and returning any further
    // parts to sit under the sub-lines. It exists because this function ends in
    // placeInOrder(), which REMOVES everything past the last wanted element --
    // a caller that appended its buttons afterwards would have them deleted on
    // the next repaint and silently rebuilt, which is the very rebuild all of
    // this is here to avoid.
    function paintRowHead(parent, item, cls, title, sub, subject, extra) {
      var row = partOf(parent, item.key, cls);
      var tile = partOf(row, "tile", "ls-row-tile");
      paintTile(tile, item);
      var body = partOf(row, "body", "ls-row-body");
      var meta = partOf(body, "meta", "ls-row-meta");
      var app = setText(partOf(meta, "app", "ls-row-app"), item.app || "");
      var parts = [app];
      if (item.at) {
        var when = setText(partOf(meta, "when", "ls-row-when"), whenText(item.at));
        when.setAttribute("data-at", item.at);
        parts.push(when);
      }
      placeInOrder(meta, parts);
      var bodyParts = [meta, setText(partOf(body, "title", "ls-row-title"), title)];
      if (subject) {
        bodyParts.push(setText(partOf(body, "subject", "ls-row-sub ls-row-subject"), subject));
      }
      if (sub) bodyParts.push(setText(partOf(body, "sub", "ls-row-sub"), sub));
      if (extra) bodyParts = bodyParts.concat(extra(body));
      placeInOrder(body, bodyParts);
      placeInOrder(row, [tile, body]);
      return row;
    }

    // "Nothing here" rather than a blank screen, so a panel that served no rows
    // is distinguishable from one that failed to load.
    function paintEmpty(el, head, line) {
      var empty = partOf(el, "empty", "ls-empty");
      var b = setText(partOf(empty, "head", "", "b"), head);
      var p = setText(partOf(empty, "line", "", "span"), line);
      placeInOrder(empty, [b, p]);
      placeInOrder(el, [empty]);
    }

    function paintPhone(items) {
      var el = panelEls.phone;
      if (!el) return;
      if (!items.length) return paintEmpty(el, "No missed calls", "The dialer and your agents' lines are quiet.");
      var want = [];
      for (var i = 0; i < items.length; i++) {
        var it = items[i];
        var row = paintRowHead(el, it, "ls-row ls-call", it.who || "", it.detail || "", "");
        setAttrIfChanged(row, "data-kind", it.kind || "missed");
        want.push(row);
      }
      placeInOrder(el, want);
    }

    function paintMailbox(items) {
      var el = panelEls.mailbox;
      if (!el) return;
      if (!items.length) return paintEmpty(el, "Nothing new", "Mail, messages and DMs all read.");
      var want = [];
      for (var i = 0; i < items.length; i++) {
        var it = items[i];
        // One stream, ordered by arrival, each line saying where it came from:
        // that IS the unified-inbox design, not decoration on top of one.
        var row = paintRowHead(el, it, "ls-row ls-msg", it.who || "", it.preview || "", it.subject || "");
        if (it.unread) setAttrIfChanged(row, "data-unread", "1");
        else if (row.hasAttribute("data-unread")) row.removeAttribute("data-unread");
        want.push(row);
      }
      placeInOrder(el, want);
    }

    function paintApps(items) {
      var el = panelEls.apps;
      if (!el) return;
      if (!items.length) return paintEmpty(el, "No apps", "Nothing is waiting in your apps.");
      var grid = partOf(el, "grid", "ls-apps-grid");
      var want = [];
      for (var i = 0; i < items.length; i++) {
        var it = items[i];
        var tile = partOf(grid, it.key, "ls-app");
        // The badge is a SIBLING of the mark, not a child of it: paintTile owns
        // the mark's contents outright -- it writes the monogram with setText,
        // which replaces every child -- so a badge parented there would be
        // wiped on the first paint of any tile without a glyph, which is all
        // four of these.
        var wrap = partOf(tile, "tile", "ls-app-tile");
        var mark = partOf(wrap, "mark", "ls-row-tile");
        paintTile(mark, it);
        var wrapParts = [mark];
        if (it.badge) {
          wrapParts.push(setText(partOf(wrap, "badge", "ls-app-badge", "span"), String(it.badge)));
        }
        placeInOrder(wrap, wrapParts);
        var body = partOf(tile, "body", "ls-app-body");
        var name = setText(partOf(body, "name", "ls-app-name"), it.app || "");
        var note = setText(partOf(body, "note", "ls-app-note"), it.note || "");
        placeInOrder(body, [name, note]);
        placeInOrder(tile, [wrap, body]);
        want.push(tile);
      }
      placeInOrder(grid, want);
      placeInOrder(el, [grid]);
    }

    function paintDecisions(items) {
      var el = panelEls.decisions;
      if (!el) return;
      // No "nothing to decide" card: this container is not a panel, it is the
      // head of the ALERTS panel, and an empty-state card sitting above the
      // notification stacks would be noise on the screen the user opened to
      // read the stacks. With nothing pending it empties and steps out of the
      // way entirely.
      if (!items.length) {
        placeInOrder(el, []);
        if (el.parentNode) el.parentNode.removeChild(el);
        return;
      }
      var want = [];
      for (var i = 0; i < items.length; i++) {
        var it = items[i];
        // The agent that is blocked is this row's "source", so a decision reads
        // as "who is waiting on me" in the same shape as everything else here.
        var spec = {
          key: it.key, app: it.agent || "agent", at: it.at,
          mono: (it.agent || "?").slice(0, 2), tint: "#ffb020"
        };
        var row = paintRowHead(el, spec, "ls-row ls-dec", it.title || "", it.detail || "", "",
          (function (key) {
            return function (body) {
              var answer = decAnswered[key];
              if (answer) {
                return [setText(partOf(body, "done", "ls-dec-done"),
                  answer === "approve" ? "Approved" : "Denied")];
              }
              var actions = partOf(body, "actions", "ls-dec-actions");
              var deny = partOf(actions, "deny", "ls-dec-btn", "button");
              var approve = partOf(actions, "approve", "ls-dec-btn ls-dec-approve", "button");
              // Wired once, when the elements are first created: re-binding on
              // every paint is how a reconciled list quietly grows duplicate
              // handlers and fires an action four times on the fourth repaint.
              if (deny.type !== "button") {
                deny.type = "button";
                deny.setAttribute("data-act", "deny");
                setText(deny, "Not now");
                approve.type = "button";
                approve.setAttribute("data-act", "approve");
                setText(approve, "Approve");
                actions.addEventListener("click", function (ev) {
                  var btn = ev.target.closest ? ev.target.closest(".ls-dec-btn") : null;
                  if (!btn) return;
                  // DEMO ONLY. This screen renders before sign-in, so an answer
                  // is remembered in the page and goes nowhere near an agent.
                  decAnswered[key] = btn.getAttribute("data-act");
                  paintDecisions(lastPanels.decisions || []);
                });
              }
              placeInOrder(actions, [deny, approve]);
              return [actions];
            };
          })(it.key));
        if (decAnswered[it.key]) setAttrIfChanged(row, "data-answered", "1");
        want.push(row);
      }
      placeInOrder(el, want);
      // Put the container back at the top of the alerts panel if a
      // notification paint has dropped it: paintNotifications ends in
      // placeInOrder(notifsEl, groups), which removes everything past the last
      // group, and the two run on independent timers. Without this the
      // decisions would survive in a detached node -- painted, correct, and
      // invisible, which is the worst of the three.
      if (notifsEl && el.parentNode !== notifsEl) {
        notifsEl.insertBefore(el, notifsEl.firstChild);
      }
      if (notifsEl) notifsEl.hidden = false;
    }

    function paintProjects(items) {
      var el = panelEls.projects;
      if (!el) return;
      if (!items.length) return paintEmpty(el, "No projects", "Nothing is on the go.");
      var want = [];
      for (var i = 0; i < items.length; i++) {
        var it = items[i];
        var row = partOf(el, it.key, "ls-row ls-project");
        // Blocked is the state this panel exists to surface: work that has
        // stopped and is waiting on a person.
        if (it.blocked) setAttrIfChanged(row, "data-blocked", "1");
        else if (row.hasAttribute("data-blocked")) row.removeAttribute("data-blocked");
        var tile = partOf(row, "tile", "ls-row-tile");
        paintTile(tile, it);
        var body = partOf(row, "body", "ls-row-body");
        var meta = partOf(body, "meta", "ls-row-meta");
        var who = setText(partOf(meta, "app", "ls-row-app"),
          it.agents === 1 ? "1 agent" : (it.agents || 0) + " agents");
        var metaParts = [who];
        if (it.blocked) {
          metaParts.push(setText(partOf(meta, "flag", "ls-proj-flag", "span"), "Blocked"));
        }
        if (it.at) {
          var when = setText(partOf(meta, "when", "ls-row-when"), whenText(it.at));
          when.setAttribute("data-at", it.at);
          metaParts.push(when);
        }
        placeInOrder(meta, metaParts);
        var name = setText(partOf(body, "title", "ls-row-title"), it.name || "");
        var note = setText(partOf(body, "sub", "ls-row-sub"), it.note || "");
        // The bar is the only quantity on this screen, so it is drawn rather
        // than written: a row of percentages reads as a spreadsheet.
        var bar = partOf(body, "bar", "ls-proj-bar");
        var fill = partOf(bar, "fill", "ls-proj-fill");
        var pct = Math.max(0, Math.min(100, Number(it.progress) || 0));
        if (fill.style.getPropertyValue("--ls-pct") !== pct + "%") {
          fill.style.setProperty("--ls-pct", pct + "%");
        }
        setAttrIfChanged(bar, "role", "progressbar");
        setAttrIfChanged(bar, "aria-valuenow", String(pct));
        setAttrIfChanged(bar, "aria-valuemin", "0");
        setAttrIfChanged(bar, "aria-valuemax", "100");
        setAttrIfChanged(bar, "aria-label", (it.name || "Project") + " progress");
        placeInOrder(bar, [fill]);
        placeInOrder(body, [meta, name, note, bar]);
        placeInOrder(row, [tile, body]);
        want.push(row);
      }
      placeInOrder(el, want);
    }

    // The last payload, so an in-page answer can repaint one panel without
    // waiting for the next poll.
    var lastPanels = {};

    function paintPanels(data) {
      lastPanels = data || {};
      // Clear the markup's `hidden` on every panel we paint.
      //
      // The panels are SERVER-RENDERED HIDDEN, and the view switcher only ever
      // toggles `data-off` -- it never touches `hidden`. The two panels that
      // predate this row get away with it because their own painters set
      // `hidden` themselves (notifsEl.hidden = !notifsEl.firstChild). These
      // four had nobody doing that, so `.ls-feed > [data-view][hidden]` kept
      // them at display:none no matter which tab was selected: fully painted,
      // 354 rows in the DOM, and invisible on the glass.
      //
      // Cleared here rather than per-painter so a panel showing its "nothing
      // here" card is still shown -- an empty panel the user selected must
      // render its empty state, not vanish.
      for (var p in panelEls) {
        if (panelEls[p] && p !== "decisions") panelEls[p].hidden = false;
      }
      paintPhone(data.phone || []);
      paintMailbox(data.mailbox || []);
      paintApps(data.apps || []);
      paintProjects(data.projects || []);
      paintDecisions(data.decisions || []);
      // Rebuilt from what is ACTUALLY on screen, for the same reason the
      // notification stacks do it: rows left untouched still own their labels.
      panelClocks = [];
      for (var name in panelEls) {
        if (!panelEls[name]) continue;
        var whens = panelEls[name].querySelectorAll(".ls-row-when[data-at]");
        for (var k = 0; k < whens.length; k++) {
          panelClocks.push({ el: whens[k], at: Number(whens[k].getAttribute("data-at")) });
        }
      }
      syncFeedFade();
    }

    function pollPanels() {
      fetch("/auth/lock-panels", { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        // A dead network answers the same way a flagged-off device does: with
        // nothing. Both are ordinary here, so both go down the same path.
        .catch(function () { return null; })
        // PAINT EVEN WITH NOTHING TO PAINT. A 404 is the ordinary answer with
        // the demo content off, and `paintPanels` is the ONLY thing that clears
        // the markup's `hidden` -- so skipping the call on that branch left all
        // four panels not empty but BLANK on every device that is not in demo
        // mode, which is every real one. `paintPanels({})` unhides them and
        // paints each "nothing here" card; `paintDecisions([])` detaches the
        // decisions head, which is right on a device with nothing pending.
        .then(function (d) { paintPanels(d || {}); });
    }
    if (panelEls.phone || panelEls.mailbox || panelEls.apps
        || panelEls.projects || panelEls.decisions) {
      pollPanels();
      // Scripted tables do not change, so this is slow on purpose: it exists to
      // pick the content up if the flag is turned on while the phone is sitting
      // on the lock screen, not to animate anything.
      setInterval(pollPanels, 15 * 60 * 1000);
      setInterval(function () {
        for (var i = 0; i < panelClocks.length; i++) {
          panelClocks[i].el.textContent = whenText(panelClocks[i].at);
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
      if (name === "power") return document.getElementById("ls-power");
      if (name === "shade") return document.getElementById("ls-shade");
      if (name === "passcode") return document.getElementById("ls-foot");
      return null;
    }

    function openSheet(name) {
      if (!screenEl) return;
      // Drop the no-animation flag set by a screen-off close. Cleared HERE
      // rather than on a timer, which would race the transition it suppresses.
      if (screenEl.hasAttribute("data-instant")) screenEl.removeAttribute("data-instant");
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
      // Before the sheet is revealed, so its single slide lands at the final
      // position rather than being pushed up again once the keyboard measures.
      if (name === "chat" || name === "passcode") preloadKeyboardOffset();
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
      // The device thread poll belongs to the open sheet. Its own tick also
      // checks, but that leaves one request after the sheet is gone.
      if (typeof stopDevicePoll === "function") stopDevicePoll();
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
    // The last MEASURED keyboard height, remembered so the next sheet can be
    // opened at its final position instead of being pushed there afterwards.
    //
    // Jay: "the thread slides up, then the keybard appears and pushes that app
    // creating almost a jerkiness motion. can the message thread and keybard
    // not be linked so they slide up as one animation?"
    //
    // They were two motions because the height was not KNOWN until the keyboard
    // had rendered: the sheet slid up over 380ms against --ls-kb:0, the OSK then
    // appeared, the ResizeObserver measured it, and the sheet's `bottom`
    // animated a second time. Nothing was wrong with either animation; the
    // trouble was that the first one ran against a number that was not final.
    //
    // Cached in localStorage because the height is a property of the DEVICE and
    // its layout, not of a visit -- so it is already right on the first open
    // after a restart, which is when a demo gets looked at.
    var KB_STORE = "taos.ls.kb";
    var kbCache = 0;
    try { kbCache = Number(window.localStorage.getItem(KB_STORE)) || 0; } catch (err) { kbCache = 0; }

    function setKeyboardOffset(px) {
      var value = px || 0;
      document.documentElement.style.setProperty("--ls-kb", value + "px");
      // Only remember a REAL measurement. Caching the zero we set on close
      // would defeat the whole thing on the very next open.
      if (value > 0 && value !== kbCache) {
        kbCache = value;
        try { window.localStorage.setItem(KB_STORE, String(value)); } catch (err) { /* fine */ }
      }
    }

    // Applied at the moment a keyboard-bearing sheet opens, so the sheet's one
    // transform lands where it will finally sit. If the measurement that
    // follows disagrees, the difference is a few pixels and the existing
    // `bottom` transition absorbs it -- which is also what handles the keyboard
    // switching between its letter, symbol and numeric layers.
    function preloadKeyboardOffset() {
      if (kbCache > 0) {
        document.documentElement.style.setProperty("--ls-kb", kbCache + "px");
      }
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
    // A feed that CANNOT SCROLL AT ALL is the third case, and it was wrong.
    // Room alone cannot tell it apart from a feed scrolled to its end: both
    // report zero. They are opposite situations, though. At the end of a long
    // feed the drag that got you there is finished and an upward swipe means
    // unlock. On a feed that never scrolled, an upward drag on a card is not
    // the end of anything -- it is the user pushing at the cards -- and
    // throwing them into the keypad is the same complaint as tsk-36i6ed from
    // the other side.
    //
    // MEASURED at the device's real viewport (540x1200, sway scale 2.0) with a
    // full lock screen of SIX agents: #ls-feed scrollHeight 394 == clientHeight
    // 394, so it does not overflow and never did. The feed is content-sized;
    // the islands fit. So this branch is not a corner case on a new user's
    // phone -- it is the ordinary state of the demo device, and it meant every
    // drag that started on an island opened the keypad.
    //
    // Overflow therefore comes back into the veto, but as a DISJUNCT rather
    // than the conjunct that was removed with tsk-36i6ed. That conjunct could
    // not change the answer -- the browser clamps scrollTop to 0 on a feed that
    // cannot scroll, so it was an unfailable arm. Here each arm decides a case
    // by itself and all three are reachable:
    //
    //   scrollable, at the top  -> room > 4      -> veto   (reading != unlock)
    //   scrollable, at the end  -> neither       -> unlock (tsk-36i6ed)
    //   cannot scroll at all    -> !overflows    -> veto   (cards are not a
    //                                                       hidden unlock pad)
    //
    // The gesture is not lost: the feed is 394px of a 1200px screen, so the
    // other two thirds of the glass still unlock on a single upward swipe.
    swipe(document.body, openPasscode, null, function () {
      return !screenEl || screenEl.getAttribute("data-sheet") === "none";
    }, function (ev) {
      var t = ev.target;
      // The call card is a surface of buttons: a drag that starts on it is
      // never an unlock, whatever the feed underneath would have said.
      if (t && t.closest && t.closest(".ls-call")) return true;
      // Nor on the PA's reminder card: it carries a button, and a thumb
      // that lands on Dismiss and slips must not open the keypad.
      if (t && t.closest && t.closest(".ls-call-reminder")) return true;
      if (!t || !t.closest || !t.closest(".ls-feed")) return false;
      return feedScrollRoom() > 4 || !feedOverflows();
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
      startDevicePoll(agent);
      chatName.textContent = agent.name || "agent";
      chatSub.textContent = agent.status || "";
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

      // A device agent's slug is GIVEN, never derived: the board chose it, the
      // phone validated it, and it is the key of the thread. Deriving one from
      // the display name would work right up until two boards differ only in
      // case.
      fetch("/auth/lock-thread/" + encodeURIComponent(threadSlug(agent)), {
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

    function threadSlug(agent) {
      return (agent && agent.slug) || slugFor((agent && agent.name) || "");
    }

    // Messages already on screen, keyed by id:seq. The device posts progress
    // lines as the work runs, so this thread GROWS while the sheet is open --
    // and a poller that rebuilt the list every two seconds would replay every
    // bubble's entrance animation, which is the flicker bug this screen has
    // already been through once. Append only what is new.
    var deviceSeen = {};
    var devicePoll = null;

    function paintDeviceMessages(messages) {
      var added = false;
      for (var i = 0; i < (messages || []).length; i++) {
        var m = messages[i];
        // ROLE IS PART OF THE KEY. /auth/lock-send puts the user's own words in
        // the thread under the id it just minted with seq 0, and the device's
        // FIRST message for that same id is also seq 0 ("working on it…").
        // Keyed on id:seq alone the two collide and the device's opening line
        // is silently dropped -- the sheet would sit on the user's question
        // with no sign the board had picked it up.
        var key = String(m.role || "") + ":" + String(m.id || "") + ":" + String(m.seq);
        if (deviceSeen[key]) continue;
        deviceSeen[key] = true;
        var el = bubble({ role: m.role, text: m.text, at: m.at });
        // Command output is a block of columns -- df, free, ip -- and a
        // proportional font turns it into a wall. The user's own words are
        // left alone.
        if (m.role !== "user") el.setAttribute("data-mono", "1");
        msgsEl.appendChild(el);
        added = true;
      }
      if (added) msgsEl.scrollTop = msgsEl.scrollHeight;
    }

    function stopDevicePoll() {
      if (devicePoll) { window.clearInterval(devicePoll); devicePoll = null; }
    }

    function startDevicePoll(agent) {
      stopDevicePoll();
      deviceSeen = {};
      if (!agent || !agent.device) return;
      var slug = threadSlug(agent);
      // 2s: the board posts progress while apt runs, and a slower poll makes a
      // working update look like a hung one.
      devicePoll = window.setInterval(function () {
        if (!screenEl || screenEl.getAttribute("data-sheet") !== "chat") {
          stopDevicePoll();
          return;
        }
        fetch("/auth/lock-thread/" + encodeURIComponent(slug),
              { credentials: "same-origin" })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (d) {
            if (!d) return;
            paintDeviceMessages(d.messages);
            // The board can be unplugged mid-conversation. Say so rather than
            // leaving a composer that silently posts into nothing.
            if (d.online === false) {
              chatSub.textContent = "Offline — unplugged";
              if (composer) composer.disabled = true;
              if (sendBtn) sendBtn.disabled = true;
            }
          })
          .catch(function () {});
      }, 2000);
    }

    function send() {
      if (!composer) return;
      var text = composer.value.trim();
      if (!text) return;
      var now = Date.now() / 1000;
      if (sendBtn) sendBtn.disabled = true;

      // A DEVICE agent is a real board with a real command path, so this is a
      // real send: the text goes to the phone, the phone relays it, and the
      // reply comes back through the thread poll rather than being invented
      // here. The bubble is NOT drawn locally -- the server puts the user's
      // words in the thread first, so the poll paints them, and drawing them
      // here as well would show everything the user typed twice.
      if (chatAgent && chatAgent.device) {
        composer.value = "";
        fetch("/auth/lock-send/" + encodeURIComponent(threadSlug(chatAgent)), {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: text })
        })
          .then(function (r) { return r.json().catch(function () { return {}; }); })
          .then(function (d) {
            if (!d || !d.ok) {
              msgsEl.appendChild(bubble({
                role: "agent",
                text: (d && (d.detail || d.error)) || "The device did not answer.",
                at: Date.now() / 1000
              }));
              msgsEl.scrollTop = msgsEl.scrollHeight;
            }
          })
          .catch(function () {})
          .then(function () { if (sendBtn) sendBtn.disabled = false; });
        return;
      }

      msgsEl.appendChild(bubble({ role: "user", text: text, at: now }));
      composer.value = "";
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
      // No caption: the screen reads as a finished product either way, and a
      // prompt with no id is still resolved in place below without a session.
      decNote.hidden = true;
      decNote.textContent = "";
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
          decDone.textContent = approved ? "Approved" : "Denied";
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
    // DICTATION, EMULATED. Product owner: "we need to remove any demo text,
    // like the voice note window says microphone access refused etc, this
    // should be animated waveform emulating voice input".
    //
    // So this sheet no longer asks for the microphone at all. That is a
    // security gain as much as a cosmetic one: the lock screen renders BEFORE
    // sign-in, and a getUserMedia call here put a live microphone one tap away
    // from whoever was holding the phone.
    //
    // The waveform is an emulated voice rather than a canned loop: words of
    // syllable-rate bursts (4-6 Hz) with a soft attack and a smooth decay,
    // random pauses between them, and a little jitter. It runs while the
    // sheet is "recording" and lies flat otherwise. Every value is a pure
    // function of time since the take began, so it is tested without a canvas.
    // -----------------------------------------------------------------------
    var waveCanvas = document.getElementById("ls-wave");
    var voiceText  = document.getElementById("ls-voice-text");
    var voiceState = document.getElementById("ls-voice-state");
    var voiceTitle = document.getElementById("ls-voice-title");
    var voiceAv    = document.getElementById("ls-voice-avatar");
    var voiceSend  = document.getElementById("ls-voice-send");
    var voiceAgent = null;
    var waveRAF = null, voiceRun = null;
    var VOICE_LEAD_MS = 380;       // a breath before the first word
    var VOICE_ROOM = 0.02;         // the floor while recording: never dead flat
    var VOICE_PHRASES = [
      "Can you give me a quick update on where things are?",
      "Let me know as soon as that is done.",
      "What is next on your list today?",
      "Remind me about this later this afternoon.",
      "Go ahead with that and send me a summary."
    ];

    function drawWave(level) {
      if (!waveCanvas) return;
      var ctx = waveCanvas.getContext("2d");
      var w = waveCanvas.width, h = waveCanvas.height, mid = h / 2;
      ctx.clearRect(0, 0, w, h);
      var bars = 48, gap = 3, bw = (w - gap * (bars - 1)) / bars;
      for (var i = 0; i < bars; i++) {
        // A travelling envelope so the bars read as a moving waveform rather
        // than a level meter.
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

    // A stable 0..1 per (take, word, channel). sin-hash rather than
    // Math.random() so a given moment of a take always looks the same.
    function voiceHash(seed, k) {
      var x = Math.sin((seed + 1) * 12.9898 + k * 78.233) * 43758.5453;
      return x - Math.floor(x);
    }

    // Word `k` of a take: its length, the pause after it, its syllable rate
    // and how loudly it is said.
    function voiceWord(seed, k) {
      var pause = voiceHash(seed + 7, k);
      return {
        dur: 240 + voiceHash(seed, k) * 460,              // 240-700 ms
        // Mostly short gaps, now and then a longer think.
        gap: 70 + pause * pause * 460,
        rate: 4 + voiceHash(seed + 3, k) * 2,              // 4-6 Hz syllables
        loud: 0.6 + 0.4 * voiceHash(seed + 11, k)
      };
    }

    // The envelope at `t` ms into a take of `words` words: 0..1. Exactly 0
    // when not recording -- the flat line is the "idle" state.
    function voiceEnvelope(t, recording, seed, words) {
      if (!recording || !(t >= 0)) return 0;
      var at = VOICE_LEAD_MS;
      if (t < at) return VOICE_ROOM;
      var n = typeof words === "number" ? words : 400;
      for (var k = 0; k < n; k++) {
        var w = voiceWord(seed || 0, k);
        if (t < at + w.dur) {
          var tw = t - at;
          var syl = Math.abs(Math.sin(Math.PI * w.rate * tw / 1000));
          syl = 0.3 + 0.7 * Math.pow(syl, 0.8);
          var attack = Math.min(1, tw / 55);
          // The smooth decay: the last 150 ms of a word eases down to the
          // room floor instead of stopping.
          var release = Math.min(1, (w.dur - tw) / 150);
          release = release * release * (3 - 2 * release);
          var jitter = 0.92 + 0.08 * Math.sin(tw / 17 + k) * Math.sin(tw / 29 + seed);
          var level = w.loud * syl * attack * release * jitter;
          return Math.max(VOICE_ROOM, Math.min(1, level));
        }
        at += w.dur;
        if (t < at + w.gap) return VOICE_ROOM;
        at += w.gap;
      }
      return VOICE_ROOM;
    }

    // How many words of the take have been fully said by `t`. Drives the
    // transcript, so text lands in step with the bursts that "said" it.
    function voiceWordsDone(t, seed, words) {
      var at = VOICE_LEAD_MS, done = 0;
      for (var k = 0; k < words; k++) {
        var w = voiceWord(seed || 0, k);
        at += w.dur;
        if (t < at) break;
        done++;
        at += w.gap;
      }
      return done;
    }

    // The bars scroll: the newest sample is on the right, each bar to its left
    // is 40 ms older, so a word travels across the canvas as it is spoken.
    function drawVoiceWave(t, run) {
      if (!waveCanvas) return;
      var ctx = waveCanvas.getContext("2d");
      var w = waveCanvas.width, h = waveCanvas.height, mid = h / 2;
      ctx.clearRect(0, 0, w, h);
      var bars = 48, gap = 3, bw = (w - gap * (bars - 1)) / bars;
      for (var i = 0; i < bars; i++) {
        var ts = t - (bars - 1 - i) * 40;
        var lvl = voiceEnvelope(ts, ts < run.endAt, run.seed, run.words.length);
        // A little per-bar texture, so neighbouring bars are not identical.
        var shape = 0.72 + 0.28 * Math.abs(Math.sin(i * 1.7 + ts / 190));
        var mag = Math.max(2, lvl * shape * mid * 0.94);
        var x = i * (bw + gap);
        ctx.fillStyle = "rgba(10,132,255," + (0.45 + 0.55 * Math.min(1, lvl * 1.4)) + ")";
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(x, mid - mag, bw, mag * 2, bw / 2);
        else ctx.rect(x, mid - mag, bw, mag * 2);
        ctx.fill();
      }
    }

    function voiceTick() {
      var run = voiceRun;
      if (!run) return;
      var t = Date.now() - run.start;
      var said = voiceWordsDone(t, run.seed, run.words.length);
      if (said !== run.said) {
        run.said = said;
        if (voiceText) voiceText.textContent = run.words.slice(0, said).join(" ");
        if (voiceSend) voiceSend.disabled = said === 0;
      }
      if (said >= run.words.length && !run.finished) {
        run.finished = true;
        if (voiceState) voiceState.textContent = "Ready to send";
      }
      drawVoiceWave(t, run);
      // Keep drawing until the last word has scrolled off the canvas.
      if (t < run.endAt + 48 * 40) waveRAF = requestAnimationFrame(voiceTick);
      else { waveRAF = null; drawWave(0); }
    }

    function stopVoice() {
      if (waveRAF) { cancelAnimationFrame(waveRAF); waveRAF = null; }
      voiceRun = null;
    }

    function openVoice(agent) {
      stopVoice();
      voiceAgent = agent;
      if (voiceTitle) voiceTitle.textContent = agent.name || "agent";
      if (voiceAv) fillAvatar(voiceAv, agent);
      if (voiceText) voiceText.textContent = "";
      if (voiceState) voiceState.textContent = "Listening…";
      if (voiceSend) voiceSend.disabled = true;
      openSheet("voice");

      var seed = Math.floor(Math.random() * 1000);
      var phrase = VOICE_PHRASES[seed % VOICE_PHRASES.length];
      var words = phrase.split(" ");
      var end = VOICE_LEAD_MS;
      for (var k = 0; k < words.length; k++) {
        var w = voiceWord(seed, k);
        end += w.dur + (k < words.length - 1 ? w.gap : 0);
      }
      voiceRun = {
        start: Date.now(), seed: seed, words: words, said: -1,
        finished: false, endAt: end
      };
      drawWave(0);
      waveRAF = requestAnimationFrame(voiceTick);
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

    // -----------------------------------------------------------------------
    // THE LIVE ZONE: an incoming call, and "Send to PA". DEMO.
    //
    // Jay: the collapsible area is also the live zone. A call from Naira
    // collapses whatever cards are showing into one droplet, the droplet
    // stretches into a call card, and the headline answer hands the call to the
    // PA -- whose conversation then plays as a live transcript with Take over
    // and End call always in reach. When it ends the zone runs backwards and
    // the feed returns exactly as it was.
    //
    // Everything shown is scripted by /auth/lock-call; there is no phone line.
    //
    // Pure pieces first (callView, callOutcomeText, revealWords, callEnvelope,
    // callRingPulse, callTimerText, callBubble, reconcileTranscript): they are
    // executed under node by the test suite, so they take everything they need
    // as arguments.
    // -----------------------------------------------------------------------

    // What the zone should show for a snapshot. `view` picks the panel,
    // `controls` the pinned buttons, `log` whether the transcript is shown and
    // `tone` whose colour the edge glows in.
    function callView(snap) {
      var state = snap && snap.state;
      var who = (snap && snap.caller && snap.caller.name) || "Caller";
      if (state === "ringing") {
        return { view: "ringing", controls: null, log: false, tone: "caller", title: "" };
      }
      if (state === "pa") {
        return { view: "talk", controls: "pa", log: true, tone: "pa",
                 title: "Your PA is talking to " + who };
      }
      if (state === "live") {
        return { view: "talk", controls: "live", log: !!snap.taken_over, tone: "caller",
                 title: "On call with " + who };
      }
      if (state === "ended") {
        return { view: "ended", controls: null, log: false,
                 tone: snap.outcome === "pa-done" ? "pa" : "caller",
                 title: callOutcomeText(snap) };
      }
      return { view: null, controls: null, log: false, tone: null, title: "" };
    }

    // The one line an ended call leaves on screen before the zone folds away.
    function callOutcomeText(snap) {
      var outcome = snap && snap.outcome;
      if (outcome === "pa-done") return "Call ended · your PA took a message";
      if (outcome === "declined") return "Declined";
      if (outcome === "voicemail") return "Sent to voicemail";
      if (snap && snap.taken_over) return "Call ended · you took over";
      return "Call ended";
    }

    // The words of `text` already spoken at `progress` (0..1). Whole words
    // only -- a half-drawn word reads as a rendering fault -- and a word shows
    // as soon as it is begun, so the first appears the moment the line does.
    function revealWords(text, progress) {
      var words = String(text || "").split(" ");
      if (!(progress > 0)) return "";
      var n = Math.min(words.length, Math.ceil(progress * words.length));
      return words.slice(0, n).join(" ");
    }

    // A synthetic speech envelope, 0..1, at time `t` seconds into a line that
    // is `progress` of the way through. Two incommensurate syllable rates so it
    // never visibly loops, tapered in and out so a line starts and ends softly.
    function callEnvelope(t, progress) {
      var a = Math.abs(Math.sin(t * 2 * Math.PI * 3.1) * 0.62
                     + Math.sin(t * 2 * Math.PI * 5.3 + 1.3) * 0.38);
      var p = progress > 0 ? progress : 0;
      var taper = Math.max(0, Math.min(1, p * 7, (1 - p) * 7));
      return Math.max(0, Math.min(1, 0.22 + 0.78 * a * taper));
    }

    // The ring: two quick swells and a rest, like a ringtone's cadence.
    function callRingPulse(t) {
      var ph = (t % 2.2);
      var a = Math.exp(-Math.pow((ph - 0.18) / 0.13, 2));
      var b = Math.exp(-Math.pow((ph - 0.62) / 0.13, 2));
      return Math.max(a, b);
    }

    // The ended pill's second line once the PA has set a reminder: the same
    // words as the card it leaves in Alerts, cut to one line.
    function callReminderPill(note) {
      var n = note || {};
      return (n.title || "Reminder added") + " \u00b7 " + (n.event || "Call") + " \u00b7 " + (n.time || "");
    }

    // THE GLOW'S LOW-PASS. The edge used to take the speech envelope's value
    // straight, and that envelope moves at syllable rate: measured in chromium
    // at 540x1200, the ring's opacity stepped by up to 0.48 in ONE frame and by
    // more than 0.08 on 99 of 230 frames while the PA spoke. That is a strobe,
    // and it was Jay's "flickery". The glow now eases toward the envelope --
    // quick to rise, slower to fall, the way a VU meter reads -- measured in
    // milliseconds rather than frames, so a dropped frame cannot become a jump.
    function callGlowStep(prev, target, dtMs) {
      var dt = Math.max(0, Math.min(dtMs || 0, 100));
      var tau = target > prev ? 160 : 360;
      return prev + (target - prev) * (1 - Math.exp(-dt / tau));
    }

    function callTimerText(ms) {
      var s = Math.max(0, Math.floor((ms || 0) / 1000));
      var m = Math.floor(s / 60);
      s = s % 60;
      return m + ":" + (s < 10 ? "0" : "") + s;
    }

    // One transcript bubble. Built once per line and then only ever updated in
    // place: the reveal and the speaking state touch its text and attributes,
    // never the node.
    function callBubble(line, index) {
      var el = document.createElement("div");
      el.className = "ls-call-line";
      el.setAttribute("data-line", String(index));
      el.setAttribute("data-who", line.who === "pa" ? "pa" : "caller");
      var head = document.createElement("div");
      head.className = "ls-call-speaker";
      var name = document.createElement("span");
      name.textContent = line.who === "pa" ? "Your PA" : (line.name || "Caller");
      var bars = document.createElement("span");
      bars.className = "ls-call-bars";
      bars.setAttribute("aria-hidden", "true");
      for (var i = 0; i < 4; i++) bars.appendChild(document.createElement("i"));
      head.appendChild(name);
      head.appendChild(bars);
      var text = document.createElement("div");
      text.className = "ls-call-text";
      el.appendChild(head);
      el.appendChild(text);
      return el;
    }

    // Bring the transcript up to date WITHOUT repainting it. Bubbles are keyed
    // by line index: a line already on screen keeps its node (so it does not
    // replay its entrance), a new line is appended, and only text that
    // actually changed is written. The line being spoken is left for the
    // animation loop to reveal word by word. Returns how many bubbles it added.
    function reconcileTranscript(logEl, lines, speakingLine, tookOver, callerName) {
      var have = {};
      var marker = null;
      for (var i = 0; i < logEl.children.length; i++) {
        var kid = logEl.children[i];
        var key = kid.getAttribute("data-line");
        if (key !== null) have[key] = kid;
        else if (kid.getAttribute("data-mark") === "takeover") marker = kid;
      }
      var added = 0;
      for (var j = 0; j < lines.length; j++) {
        var line = lines[j];
        var el = have[String(j)];
        if (!el) {
          el = callBubble({ who: line.who, name: callerName }, j);
          logEl.appendChild(el);
          added += 1;
        }
        var speaking = j === speakingLine;
        if (speaking) {
          if (el.getAttribute("data-speaking") !== "1") el.setAttribute("data-speaking", "1");
        } else {
          if (el.hasAttribute("data-speaking")) el.removeAttribute("data-speaking");
          var cut = typeof line.upto === "number";
          var want = cut ? revealWords(line.text, line.upto) : line.text;
          var textEl = el.children[1];
          if (textEl && textEl.textContent !== want) textEl.textContent = want;
          if (cut && el.getAttribute("data-cut") !== "1") el.setAttribute("data-cut", "1");
        }
      }
      if (tookOver && !marker) {
        marker = document.createElement("div");
        marker.className = "ls-call-mark";
        marker.setAttribute("data-mark", "takeover");
        marker.textContent = "You took over";
        logEl.appendChild(marker);
        added += 1;
      }
      return added;
    }

    var callEl = document.getElementById("ls-call");
    if (callEl) {
      var callCard = document.getElementById("ls-call-card");
      var callLog = document.getElementById("ls-call-log");
      var callTitle = document.getElementById("ls-call-title");
      var callTimer = document.getElementById("ls-call-timer");
      var callOutcome = document.getElementById("ls-call-outcome");
      var callPill = document.getElementById("ls-call-pill");
      var callPillIco = document.getElementById("ls-call-pill-ico");
      var callHead = callEl.parentNode;
      var callRings = callEl.querySelectorAll(".ls-call-ring");
      var callWashes = callEl.querySelectorAll(".ls-call-wash");
      var CALL_ICONS = {
        end: '<path d="M3.2 13.9c5-4.6 12.6-4.6 17.6 0l-1.8 2.6-3.6-1.2-.5-2.6a10 10 0 0 0-5.8 0l-.5 2.6-3.6 1.2z"/>',
        voicemail: NOTIF_GLYPHS.voicemail,
        calendar: NOTIF_GLYPHS.calendar
      };
      var reduceMotion = !!(window.matchMedia
        && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

      var callOff = false;          // the demo is switched off: stop asking
      var callTimerId = null;
      var callSnap = null;          // the last snapshot applied
      var callAt = 0;               // performance.now() when it arrived
      var callId = null;            // the call the zone is showing (or last saw)
      var callShown = false;        // is the zone up
      var callPhase = "none";       // none | entering | shown | ended | leaving
      var callPending = null;       // a snapshot that arrived mid-choreography
      var callBusy = false;         // an action request is in flight
      var callSaved = null;         // what the feed looked like before the call
      var callEndTimers = [];
      var callFrame = null;
      var callToneW = { pa: 0, caller: 0 };
      var callGlow = 0.3;           // the edge's smoothed amplitude
      var callLastFrame = 0;
      var callLastSec = -1;
      var callSpeakEl = null;

      var callNow = function () {
        return (window.performance && performance.now) ? performance.now() : Date.now();
      };

      // The views and the pinned control rows, switched by attribute.
      var callViews = {};
      [].forEach.call(callEl.querySelectorAll(".ls-call-view"), function (v) {
        callViews[v.getAttribute("data-for")] = v;
      });
      var callCtls = {};
      [].forEach.call(callEl.querySelectorAll(".ls-call-ctl"), function (c) {
        callCtls[c.getAttribute("data-for")] = c;
      });
      var callActive = null;

      // THE MORPH. The card's size is always an explicit number taken from the
      // content it now holds, so a change of state is a transition between two
      // measured sizes rather than a jump.
      var callFullWidth = function () {
        var cap = callHead ? callHead.clientWidth : 436;
        var css = parseFloat(getComputedStyle(callEl).maxWidth) || 436;
        return Math.min(cap, css);
      };
      var callFit = function () {
        // Not while hidden: a hidden card measures as nothing, and that
        // "size" would be the start of the next entrance's transition.
        if (!callActive || callEl.hidden) return;
        var h = callActive.offsetHeight;
        callCard.style.height = h + "px";
        var w = callFullWidth();
        if (callActive === callViews.ended && callPill) {
          // A one-line pill: as wide as its words, never wider than the column.
          w = Math.min(w, Math.ceil(callPill.offsetWidth) + 3);
        }
        callEl.style.width = w + "px";
      };
      if (window.ResizeObserver) {
        var callRO = new ResizeObserver(function () { callFit(); });
        for (var cv in callViews) callRO.observe(callViews[cv]);
      }
      window.addEventListener("resize", callFit);

      var callSwitch = function (name) {
        var next = callViews[name] || null;
        if (next === callActive) return;
        for (var k in callViews) {
          if (callViews[k] === next) callViews[k].setAttribute("data-on", "1");
          else callViews[k].removeAttribute("data-on");
        }
        callActive = next;
        callFit();
      };

      // Keyboard or finger, whichever was used last: the focus ring is only
      // drawn for a keyboard.
      callEl.setAttribute("data-kbd", "0");
      document.addEventListener("keydown", function () { callEl.setAttribute("data-kbd", "1"); }, true);
      document.addEventListener("touchstart", function () { callEl.setAttribute("data-kbd", "0"); },
        { passive: true, capture: true });
      var callFocus = function (sel) {
        var el = callEl.querySelector(sel);
        if (el && el.focus) { try { el.focus({ preventScroll: true }); } catch (e) {} }
      };

      // Paint the zone for a snapshot. Idempotent: an unchanged poll writes
      // nothing (the transcript reconcile guarantees it for the bubbles, and
      // the rest are attribute and text writes guarded by comparison).
      var paintCall = function (snap) {
        var v = callView(snap);
        var prevView = callEl.getAttribute("data-view");
        var prevCtl = callEl.getAttribute("data-controls");
        setAttrIfChanged(callEl, "data-state", snap.state);
        if (snap.caller) {
          var nameEl = document.getElementById("ls-call-name");
          var labelEl = document.getElementById("ls-call-label");
          var avEl = document.getElementById("ls-call-avatar");
          setText(nameEl, snap.caller.name);
          setText(labelEl, snap.caller.label);
          setText(avEl, (snap.caller.name || "?").charAt(0));
          setAttrIfChanged(callEl, "aria-label", "Call from " + snap.caller.name);
        }
        if (v.view === "talk") {
          setText(callTitle, v.title);
          for (var c in callCtls) {
            if (c === v.controls) callCtls[c].setAttribute("data-on", "1");
            else callCtls[c].removeAttribute("data-on");
          }
          var lines = snap.transcript || [];
          var speakingLine = snap.speaking ? snap.speaking.line : -1;
          var added = reconcileTranscript(callLog, v.log ? lines : [], speakingLine,
            !!snap.taken_over, snap.caller && snap.caller.name);
          callSpeakEl = speakingLine >= 0
            ? callLog.querySelector('[data-line="' + speakingLine + '"]') : null;
          if (added && callLog.scrollTo) {
            callLog.scrollTo({ top: callLog.scrollHeight, behavior: reduceMotion ? "auto" : "smooth" });
          }
        }
        if (v.view === "ended") {
          setText(callOutcome, v.title);
          if (callPill && callPill.getAttribute("data-kind") !== "outcome") {
            callPill.setAttribute("data-kind", "outcome");
            if (callPillIco) callPillIco.innerHTML = '<svg viewBox="0 0 24 24">' + CALL_ICONS.end + "</svg>";
          }
        }
        // Only on a CHANGE. A poll lands every 300ms, and every write here is a
        // style invalidation on the ancestor of the spinning edge.
        setAttrIfChanged(callEl, "data-view", v.view || "");
        setAttrIfChanged(callEl, "data-controls", v.controls || "");
        callSwitch(v.view);
        // Move focus only when the thing under the finger changed, so a poll
        // never steals it: the headline answer while ringing, Take over while
        // the PA talks, End once you are on the line.
        if (v.view !== prevView || v.controls !== prevCtl) {
          if (v.view === "ringing") callFocus('[data-call-act="pa"]');
          else if (v.controls === "pa") callFocus('[data-call-act="takeover"]');
          else if (v.controls === "live") callFocus('.ls-call-ctl[data-for="live"] [data-call-act="end"]');
        }
      };

      // THE ANIMATION LOOP: the edge glow, the wash, the speaking bars, the
      // word-by-word reveal and the timer. Opacity and transform writes only.
      // It runs only while the zone is up AND the panel is lit.
      var callLoopOn = function () {
        return callShown && callPhase !== "leaving"
          && !(screenEl && screenEl.hasAttribute("data-blanked"))
          && !document.hidden;
      };
      var callTick = function () {
        callFrame = null;
        if (!callLoopOn() || !callSnap) return;
        var now = callNow();
        var t = now / 1000;
        var snap = callSnap;
        var v = callView(snap);
        var sp = snap.speaking;
        var amp = 0.3;
        var tone = v.tone || "caller";
        var progress = 0;
        if (sp) {
          var line = (snap.transcript || [])[sp.line];
          var dur = line ? line.dur_ms : 3000;
          progress = Math.min(1, sp.progress + (now - callAt) / dur);
          tone = sp.who === "pa" ? "pa" : "caller";
          amp = progress < 1 ? callEnvelope(t, progress) : 0.3;
          if (callSpeakEl && line) {
            var tEl = callSpeakEl.children[1];
            var want = revealWords(line.text, progress);
            if (tEl && tEl.textContent !== want) tEl.textContent = want;
            var bars = callSpeakEl.children[0] && callSpeakEl.children[0].children[1];
            if (bars) {
              for (var b = 0; b < bars.children.length; b++) {
                var k = progress < 1 ? callEnvelope(t + b * 0.11, progress) : 0.2;
                bars.children[b].style.transform = "scaleY(" + (0.25 + 0.75 * k).toFixed(3) + ")";
              }
            }
          }
        } else if (snap.state === "ringing") {
          amp = 0.35 + 0.65 * callRingPulse(t);
        } else if (snap.state === "pa") {
          amp = 0.3 + 0.08 * Math.sin(t * 2.4);          // between lines: a breath
        } else if (snap.state === "live") {
          amp = 0.34 + 0.1 * Math.sin(t * 1.6);
        } else {
          amp = 0.2;
        }
        // Both by elapsed time, never by frame count: the bars above keep the
        // raw envelope (they are MEANT to flutter), the edge and the wash get
        // the smoothed one, and a change of speaker is a cross-fade of colour.
        var dt = callLastFrame ? now - callLastFrame : 16;
        callLastFrame = now;
        callGlow = callGlowStep(callGlow, amp, dt);
        var fade = 1 - Math.exp(-Math.max(0, Math.min(dt, 100)) / 380);
        callToneW.pa += ((tone === "pa" ? 1 : 0) - callToneW.pa) * fade;
        callToneW.caller += ((tone === "caller" ? 1 : 0) - callToneW.caller) * fade;
        for (var r = 0; r < callRings.length; r++) {
          var w = callToneW[callRings[r].getAttribute("data-tone")] || 0;
          callRings[r].style.opacity = (w * (0.45 + 0.55 * callGlow)).toFixed(3);
        }
        for (var q = 0; q < callWashes.length; q++) {
          var ww = callToneW[callWashes[q].getAttribute("data-tone")] || 0;
          callWashes[q].style.opacity = (ww * (0.4 + 0.6 * callGlow)).toFixed(3);
        }
        if (typeof snap.elapsed_ms === "number" && callTimer) {
          var ms = snap.elapsed_ms + (now - callAt);
          var sec = Math.floor(ms / 1000);
          if (sec !== callLastSec) { callLastSec = sec; callTimer.textContent = callTimerText(ms); }
        }
        callFrame = requestAnimationFrame(callTick);
      };
      var callLoop = function () {
        if (callFrame === null && callLoopOn()) {
          callLastFrame = 0;                 // a resumed loop starts from rest
          callFrame = requestAnimationFrame(callTick);
        }
      };
      document.addEventListener("visibilitychange", callLoop);

      // THE LIQUID. The cards the feed is showing right now, measured.
      var callCards = function () {
        if (!feedEl || feedIsHidden()) return [];
        var fr = feedEl.getBoundingClientRect();
        if (fr.height < 4) return [];
        var out = [];
        var panels = feedEl.children;
        for (var i = 0; i < panels.length; i++) {
          var p = panels[i];
          if (p.hidden || p.hasAttribute("data-off")) continue;
          var cand = p.id === "ls-activity"
            ? [].slice.call(p.querySelectorAll(".ls-agents > *, .ls-tasks > *"))
            : [].slice.call(p.children);
          for (var j = 0; j < cand.length; j++) {
            var c = cand[j];
            var r = c.getBoundingClientRect();
            if (r.height < 8 || r.width < 8) continue;
            if (r.bottom < fr.top || r.top > fr.bottom) continue;   // scrolled away
            out.push({ el: c, r: r });
            if (out.length >= 10) return out;
          }
        }
        return out;
      };
      var callRadius = function (el, r) {
        var rad = parseFloat(getComputedStyle(el).borderTopLeftRadius) || 20;
        return Math.min(rad, r.height / 2, r.width / 2);
      };
      var gooLayer = function (box) {
        var g = document.createElement("div");
        g.className = "ls-goo";
        g.setAttribute("aria-hidden", "true");
        g.style.left = box.left + "px"; g.style.top = box.top + "px";
        g.style.width = box.width + "px"; g.style.height = box.height + "px";
        document.body.appendChild(g);
        return g;
      };
      var gooBlob = function (layer, box, x, y, w, h, radius) {
        var b = document.createElement("div");
        b.className = "ls-goo-blob";
        b.style.width = w + "px"; b.style.height = h + "px";
        b.style.left = (x - box.left) + "px"; b.style.top = (y - box.top) + "px";
        b.style.borderRadius = radius + "px";
        layer.appendChild(b);
        return b;
      };
      var unionBox = function (rects, pad) {
        var l = Infinity, t = Infinity, r = -Infinity, b = -Infinity;
        for (var i = 0; i < rects.length; i++) {
          l = Math.min(l, rects[i].left); t = Math.min(t, rects[i].top);
          r = Math.max(r, rects[i].right); b = Math.max(b, rects[i].bottom);
        }
        l -= pad; t -= pad; r += pad; b += pad;
        return { left: l, top: t, width: r - l, height: b - t, right: r, bottom: b };
      };
      var later = function (fn, ms) { var id = window.setTimeout(fn, ms); callEndTimers.push(id); return id; };
      var clearLater = function () {
        for (var i = 0; i < callEndTimers.length; i++) window.clearTimeout(callEndTimers[i]);
        callEndTimers = [];
      };
      var DROP = 60;                 // the droplet's diameter
      var EASE_IN = "cubic-bezier(0.55, 0, 0.25, 1)";
      var SPRING = "cubic-bezier(0.34, 1.25, 0.5, 1)";

      // In: the cards run together into a droplet, the droplet stretches into
      // the call card. `done` fires once the card is showing.
      var callEnter = function (snap, done) {
        callPhase = "entering";
        callSaved = {
          scroll: feedEl ? feedEl.scrollTop : 0,
          focus: document.activeElement
        };
        if (callHead) callHead.setAttribute("data-call", "1");
        if (viewsEl) viewsEl.setAttribute("inert", "");
        // Lay the card out unseen so the droplet knows where it is going.
        callEl.hidden = false;
        callEl.setAttribute("data-phase", "measure");
        paintCall(snap);
        callFit();
        var cards = reduceMotion ? [] : callCards();
        // Where the card WILL be, measured rather than predicted: put the
        // layout into its end state, read the rect, and put it back, all in
        // one task -- the browser never paints the in-between.
        var target = null;
        if (feedEl) feedEl.setAttribute("data-call", "1");
        callEl.setAttribute("data-phase", "grow");
        target = callEl.getBoundingClientRect();
        if (feedEl) feedEl.removeAttribute("data-call");
        callEl.setAttribute("data-phase", "measure");
        var cx = target.left + target.width / 2;
        var cy = target.top + Math.min(target.height, 180) / 2;

        var reveal = function () {
          if (feedEl) feedEl.setAttribute("data-call", "1");
          callEl.setAttribute("data-phase", "grow");
          callFit();
          return callEl.getBoundingClientRect();
        };

        if (reduceMotion) {
          if (feedEl) feedEl.setAttribute("data-call", "return");     // opacity 0
          later(function () {
            reveal();
            requestAnimationFrame(function () {
              callEl.removeAttribute("data-phase");
              callPhase = "shown"; done();
            });
          }, 180);
          return;
        }

        var rects = [target];
        for (var i = 0; i < cards.length; i++) rects.push(cards[i].r);
        var box = unionBox(rects, 40);
        var layer = gooLayer(box);
        var blobs = [];
        for (var n = 0; n < cards.length; n++) {
          var c = cards[n];
          var rad = callRadius(c.el, c.r);
          var bl = gooBlob(layer, box, c.r.left, c.r.top, c.r.width, c.r.height, rad);
          blobs.push({ b: bl, r: c.r });
        }
        // The droplet itself, at the card's heart, from nothing.
        var drop = gooBlob(layer, box, cx - DROP / 2, cy - DROP / 2, DROP, DROP, DROP / 2);
        drop.style.transform = "scale(0)";
        void layer.offsetWidth;                                  // commit the start

        var STAG = 42, RUN = 560;
        for (var m = 0; m < blobs.length; m++) {
          var o = blobs[m], r = o.r;
          var dx = cx - (r.left + r.width / 2), dy = cy - (r.top + r.height / 2);
          var sx = DROP / r.width, sy = DROP / r.height;
          var tr = "translate(" + dx.toFixed(1) + "px," + dy.toFixed(1) + "px) scale("
            + sx.toFixed(3) + "," + sy.toFixed(3) + ")";
          o.b.style.transition = "transform " + RUN + "ms " + EASE_IN + " " + (m * STAG) + "ms, border-radius "
            + RUN + "ms ease " + (m * STAG) + "ms";
          o.b.style.transform = tr;
          // 50% of the unscaled box is an ellipse that the scale turns into a
          // circle, so each card arrives as a drop, not a squashed tile.
          o.b.style.borderRadius = "50%";
          // The real card goes with it, and fades as the liquid takes over.
          var el = cards[m].el;
          el.setAttribute("data-call-card", "1");
          el.style.transition = "transform " + RUN + "ms " + EASE_IN + " " + (m * STAG) + "ms, opacity 200ms ease "
            + (m * STAG + 60) + "ms";
          el.style.transform = "translate(" + (dx * 0.35).toFixed(1) + "px," + (dy * 0.35).toFixed(1) + "px) scale(0.9)";
          el.style.opacity = "0";
        }
        var gathered = cards.length ? RUN + (cards.length - 1) * STAG : 120;
        drop.style.transition = "transform " + Math.max(320, gathered - 160) + "ms " + SPRING
          + " " + (cards.length ? 200 : 0) + "ms";
        drop.style.transform = "scale(1)";

        later(function () {
          // Merged. The feed leaves the layout (its own attribute; its nodes
          // and every state they carry stay exactly as they were) and the
          // cards' borrowed styles are handed back while nobody can see them.
          for (var k = 0; k < cards.length; k++) {
            var e = cards[k].el;
            e.style.transition = ""; e.style.transform = ""; e.style.opacity = "";
            e.removeAttribute("data-call-card");
          }
          for (var q = 0; q < blobs.length; q++) blobs[q].b.remove();
          var R = reveal();
          // The droplet stretches into the card, with a little overshoot.
          drop.style.transition = "left 460ms " + SPRING + ", top 460ms " + SPRING + ", width 460ms "
            + SPRING + ", height 460ms " + SPRING + ", border-radius 460ms ease";
          drop.style.left = (R.left - box.left) + "px";
          drop.style.top = (R.top - box.top) + "px";
          drop.style.width = R.width + "px";
          drop.style.height = R.height + "px";
          drop.style.borderRadius = "28px";
          later(function () {
            callEl.removeAttribute("data-phase");               // fades in
            layer.style.opacity = "0";
            later(function () { layer.remove(); }, 260);
            callPhase = "shown";
            done();
          }, 400);
        }, gathered + 30);
      };

      // Out: the card shrinks back to a droplet, which splits into the cards
      // it came from. The feed comes back with the same nodes, view, scroll
      // and collapsed state it had -- nothing was rebuilt, only un-hidden.
      var callLeave = function () {
        callPhase = "leaving";
        clearLater();
        var R = callEl.getBoundingClientRect();
        var finish = function () {
          callEl.hidden = true;
          callEl.removeAttribute("data-phase");
          callEl.removeAttribute("data-state");
          callEl.removeAttribute("data-view");
          callEl.removeAttribute("data-controls");
          callShown = false;
          callPhase = "none";
          if (callHead) callHead.removeAttribute("data-call");
          if (viewsEl) viewsEl.removeAttribute("inert");
          if (callSaved && callSaved.focus && callSaved.focus !== document.body
              && callSaved.focus.focus) {
            try { callSaved.focus.focus({ preventScroll: true }); } catch (e) {}
          } else if (document.activeElement && callEl.contains(document.activeElement)) {
            document.activeElement.blur();
          }
          callSaved = null;
          if (callPending) { var p = callPending; callPending = null; applyCall(p); }
        };
        var restoreFeed = function () {
          if (!feedEl) return;
          feedEl.setAttribute("data-call", "return");               // in layout, unseen
          if (callSaved) feedEl.scrollTop = callSaved.scroll;
          syncFeedFade();
        };

        if (reduceMotion || R.width < 4) {
          callEl.setAttribute("data-phase", "leave");
          later(function () {
            callEl.hidden = true;
            restoreFeed();
            requestAnimationFrame(function () {
              if (feedEl) feedEl.removeAttribute("data-call");
              finish();
            });
          }, 140);
          return;
        }

        var cx = R.left + R.width / 2, cy = R.top + R.height / 2;
        var box = { left: 0, top: 0, width: window.innerWidth, height: window.innerHeight };
        var layer = gooLayer(box);
        var drop = gooBlob(layer, box, R.left, R.top, R.width, R.height, Math.min(28, R.height / 2));
        void layer.offsetWidth;
        callEl.setAttribute("data-phase", "leave");
        drop.style.transition = "left 380ms " + EASE_IN + ", top 380ms " + EASE_IN + ", width 380ms "
          + EASE_IN + ", height 380ms " + EASE_IN + ", border-radius 380ms ease";
        drop.style.left = (cx - DROP / 2) + "px"; drop.style.top = (cy - DROP / 2) + "px";
        drop.style.width = DROP + "px"; drop.style.height = DROP + "px";
        drop.style.borderRadius = (DROP / 2) + "px";

        later(function () {
          callEl.hidden = true;
          restoreFeed();
          var cards = callCards();
          var STAG = 36, RUN = 540;
          var blobs = [];
          for (var i = 0; i < cards.length; i++) {
            var r = cards[i].r;
            var b = gooBlob(layer, box, r.left, r.top, r.width, r.height, callRadius(cards[i].el, r));
            var dx = cx - (r.left + r.width / 2), dy = cy - (r.top + r.height / 2);
            b.style.transform = "translate(" + dx.toFixed(1) + "px," + dy.toFixed(1) + "px) scale("
              + (DROP / r.width).toFixed(3) + "," + (DROP / r.height).toFixed(3) + ")";
            b.setAttribute("data-r", b.style.borderRadius);
            b.style.borderRadius = "50%";
            blobs.push(b);
          }
          void layer.offsetWidth;
          for (var j = 0; j < blobs.length; j++) {
            blobs[j].style.transition = "transform " + RUN + "ms " + SPRING + " " + (j * STAG)
              + "ms, border-radius " + RUN + "ms ease " + (j * STAG) + "ms";
            blobs[j].style.transform = "none";
            blobs[j].style.borderRadius = blobs[j].getAttribute("data-r");
          }
          drop.style.transition = "transform 420ms " + EASE_IN + " 120ms";
          drop.style.transform = "scale(0)";
          var spread = cards.length ? RUN + (cards.length - 1) * STAG : 420;
          later(function () {
            if (feedEl) feedEl.removeAttribute("data-call");      // the real cards return
            layer.style.opacity = "0";
            later(function () { layer.remove(); finish(); }, 250);
          }, spread - 60);
        }, 390);
      };

      // Mark the event the PA added, so it is the first thing seen when the
      // alerts come into view, and flag the alerts tab if they are not showing.
      // THE REMINDER the PA leaves, as a card at the top of Alerts with a
      // Dismiss button. Driven by the call snapshot (polled every 2s even when
      // no call is up), so a dismissal on the server -- or a reset -- takes it
      // down, and nothing but a finished PA call puts it up.
      var callReminder = null;          // the card node, built once per reminder
      var callReminderAt = null;        // which reminder it is showing
      var callReminderGone = null;      // one the user dismissed, awaiting the server
      var alertsTab = function () {
        return viewsEl ? viewsEl.querySelector('.ls-view-tab[data-view="alerts"]') : null;
      };
      var buildReminder = function (note) {
        var el = document.createElement("div");
        el.className = "ls-row ls-call-reminder";
        el.id = "ls-call-reminder";
        el.setAttribute("role", "group");
        el.setAttribute("aria-label", (note.title || "Reminder") + ". " + (note.body || ""));
        var tile = document.createElement("div");
        tile.className = "ls-call-reminder-tile";
        tile.setAttribute("aria-hidden", "true");
        tile.innerHTML = '<svg viewBox="0 0 24 24">' + NOTIF_GLYPHS.calendar + "</svg>";
        var body = document.createElement("div");
        body.className = "ls-row-body";
        var meta = document.createElement("div");
        meta.className = "ls-call-reminder-meta";
        meta.textContent = (note.from || "Your PA") + " \u00b7 now";
        var title = document.createElement("div");
        title.className = "ls-call-reminder-title";
        title.textContent = note.title || "";
        var text = document.createElement("div");
        text.className = "ls-call-reminder-text";
        text.textContent = note.body || "";
        var acts = document.createElement("div");
        acts.className = "ls-dec-actions";
        var dismiss = document.createElement("button");
        dismiss.type = "button";
        dismiss.className = "ls-dec-btn";
        dismiss.setAttribute("data-act", "dismiss");
        dismiss.textContent = "Dismiss";
        acts.appendChild(dismiss);
        body.appendChild(meta); body.appendChild(title); body.appendChild(text); body.appendChild(acts);
        el.appendChild(tile); el.appendChild(body);
        dismiss.addEventListener("click", function () {
          callReminderGone = callReminderAt;
          leaveReminder();
          fetch("/auth/lock-call/dismiss", { method: "POST", credentials: "same-origin" })
            .catch(function () { /* the next poll will show it again if it stuck */ })
            .then(function () { pollCall(); });
        });
        return el;
      };
      // Out with the reverse of the entrance every card on this screen has.
      var leaveReminder = function () {
        var el = callReminder;
        if (!el) return;
        callReminder = null;
        callReminderAt = null;
        el.setAttribute("data-leaving", "1");
        var tab = alertsTab();
        if (tab) tab.removeAttribute("data-badge");
        var gone = function () {
          if (el.parentNode) el.parentNode.removeChild(el);
          if (notifsEl && !notifsEl.firstChild) notifsEl.hidden = true;
          syncFeedFade();
        };
        window.setTimeout(gone, reduceMotion ? 0 : 320);
      };
      var syncReminder = function (note) {
        if (!notifsEl) return;
        if (note && callReminderGone !== null && note.at === callReminderGone) return;
        if (!note) {
          callReminderGone = null;
          if (callReminder) leaveReminder();
          return;
        }
        if (callReminder && callReminderAt === note.at) return;      // unchanged
        if (callReminder) { callReminder.remove(); callReminder = null; }
        callReminder = buildReminder(note);
        callReminderAt = note.at;
        // Under the pending decisions, above the notification stacks.
        var dec = document.getElementById("ls-decisions");
        var ref = (dec && dec.parentNode === notifsEl) ? dec.nextSibling : notifsEl.firstChild;
        notifsEl.insertBefore(callReminder, ref);
        notifsEl.hidden = false;
        if (currentView !== "alerts") {
          var tab = alertsTab();
          if (tab) tab.setAttribute("data-badge", "1");
        }
        syncFeedFade();
      };
      if (viewsEl) {
        viewsEl.addEventListener("click", function () {
          window.setTimeout(function () {
            if (currentView !== "alerts") return;
            var tab = alertsTab();
            if (tab) tab.removeAttribute("data-badge");
          }, 0);
        });
      }

      // The ended pill, then the fold. A finished PA call shows its outcome,
      // then the event it added, before the zone goes.
      var callEnded = function (snap) {
        callPhase = "ended";
        var hold = 1900;
        if (snap.outcome === "pa-done") {
          later(function () {
            if (callOutcome) callOutcome.style.opacity = "0";
            later(function () {
              setText(callOutcome, callReminderPill(snap.notification));
              if (callPill) callPill.setAttribute("data-kind", "event");
              if (callPillIco) callPillIco.innerHTML = '<svg viewBox="0 0 24 24">' + CALL_ICONS.calendar + "</svg>";
              if (callOutcome) callOutcome.style.opacity = "";
              callFit();
            }, 200);
          }, 1500);
          hold = 4300;
        }
        later(callLeave, hold);
      };

      var applyCall = function (snap) {
        if (!snap || !snap.state) return;
        syncReminder(snap.notification || null);
        if (callPhase === "entering" || callPhase === "leaving") {
          callPending = snap;
          // Keep the content current underneath an entrance; an exit is final.
          if (callPhase === "entering" && callSnap && snap.call_id === callSnap.call_id) {
            callSnap = snap; callAt = callNow(); paintCall(snap);
          }
          return;
        }
        var active = snap.state === "ringing" || snap.state === "pa" || snap.state === "live";
        var prevId = callId;
        callSnap = snap; callAt = callNow();
        if (!callShown) {
          callId = snap.call_id;
          // A call that had already ended when we first saw it is history,
          // not something to animate.
          if (!active) return;
          callShown = true;
          if (callOutcome) callOutcome.style.opacity = "";
          if (prevId !== snap.call_id) callLog.textContent = "";
          callEnter(snap, function () {
            var p = callPending; callPending = null;
            if (p) applyCall(p);
            callLoop();
          });
          return;
        }
        if (snap.call_id !== callId) {
          callId = snap.call_id;
          callLog.textContent = "";                // a genuinely new call
        }
        if (snap.state === "idle") { callLeave(); return; }
        if (callPhase === "ended") return;           // the pill is already folding away
        paintCall(snap);
        if (snap.state === "ended") callEnded(snap);
        callLoop();
      };

      var callSchedule = function () {
        if (callOff) return;
        if (callTimerId) window.clearTimeout(callTimerId);
        var active = callShown && callPhase !== "ended" && callPhase !== "leaving";
        callTimerId = window.setTimeout(pollCall, active ? 300 : 2000);
      };
      var pollCall = function () {
        callTimerId = null;
        if (callOff) return;
        fetch("/auth/lock-call", { credentials: "same-origin" })
          .then(function (r) {
            if (r.status === 404 || r.status === 403) { callOff = true; return null; }
            return r.ok ? r.json() : null;
          })
          .then(function (d) { if (d) applyCall(d); })
          .catch(function () { /* offline: try again on the next tick */ })
          .then(callSchedule);
      };

      var callAct = function (action) {
        if (callBusy) return;
        callBusy = true;
        fetch("/auth/lock-call/action", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action: action })
        }).then(function (r) { return r.json().catch(function () { return null; }); })
          .then(function (d) {
            callBusy = false;
            // A 409 carries only the state; fetch the whole picture instead.
            if (d && d.transcript) applyCall(d); else pollCall();
          })
          .catch(function () { callBusy = false; });
      };

      callEl.addEventListener("click", function (ev) {
        var btn = ev.target.closest ? ev.target.closest("button") : null;
        if (!btn || !callEl.contains(btn)) return;
        if (callPhase !== "shown") return;
        var act = btn.getAttribute("data-call-act");
        if (act) { callAct(act); return; }
        if (btn.hasAttribute("data-call-toggle")) {
          // Visual only: there is no audio path in a demo.
          btn.setAttribute("aria-pressed", btn.getAttribute("aria-pressed") === "true" ? "false" : "true");
        }
      });

      // The push is a nudge to look NOW; the poll is what makes it reliable.
      // The page's ONE shared stream: a second EventSource would hold a second
      // connection open to the controller for the same events.
      var callStream = lockEvents();
      if (callStream) {
        callStream.addEventListener("call", function () { if (!callOff) pollCall(); });
        callStream.addEventListener("screen-on", function () {
          // The blanked flag is cleared by the stream's other listener; look
          // once it has been.
          window.setTimeout(callLoop, 0);
        });
      }
      pollCall();
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
        headers={
            # no-cache, NOT no-store: the browser may keep the copy, it just has
            # to revalidate, so an unchanged script still costs a 304.
            #
            # max-age=300 was five minutes of the kiosk running code that had
            # already been replaced. Twice this cost real time: a fix was
            # deployed, the service restarted, the page still ran the old
            # script, and the bug looked unfixed -- once badly enough that the
            # cause was "ruled out" on a grep that could not tell the two
            # builds apart. This screen is iterated on against the glass, so
            # staleness is the expensive failure and a revalidation is cheap.
            "Cache-Control": "no-cache",
        },
    )


@router.get("/pin-panel.js")
async def pin_panel_script(request: Request):
    """Serve the PIN panel behaviour. Same CSP reasoning as /auth/osk.js."""
    return Response(
        content=_PIN_PANEL_SCRIPT,
        media_type="application/javascript",
        headers={
            # no-cache, NOT no-store: the browser may keep the copy, it just has
            # to revalidate, so an unchanged script still costs a 304.
            #
            # max-age=300 was five minutes of the kiosk running code that had
            # already been replaced. Twice this cost real time: a fix was
            # deployed, the service restarted, the page still ran the old
            # script, and the bug looked unfixed -- once badly enough that the
            # cause was "ruled out" on a grep that could not tell the two
            # builds apart. This screen is iterated on against the glass, so
            # staleness is the expensive failure and a revalidation is cheap.
            "Cache-Control": "no-cache",
        },
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
        headers={
            # no-cache, NOT no-store: the browser may keep the copy, it just has
            # to revalidate, so an unchanged script still costs a 304.
            #
            # max-age=300 was five minutes of the kiosk running code that had
            # already been replaced. Twice this cost real time: a fix was
            # deployed, the service restarted, the page still ran the old
            # script, and the bug looked unfixed -- once badly enough that the
            # cause was "ruled out" on a grep that could not tell the two
            # builds apart. This screen is iterated on against the glass, so
            # staleness is the expensive failure and a revalidation is cheap.
            "Cache-Control": "no-cache",
        },
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
            path = root / rel
            if path.is_file():
                # Versioned by mtime. A replaced icon kept its URL, and the
                # kiosk's disk cache (which outlives a browser restart) went
                # on showing the old one: Jay saw the Nous wordmark after the
                # Hermes mascot had shipped.
                return "/%s?v=%d" % (rel, int(path.stat().st_mtime))
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


#: Scripted "current task" follow-ons for the lock screen's demo agent
#: islands. Keyed by the demo agent's NAME exactly as it appears in
#: TAOS_LOCK_DEMO_AGENTS. Jay: "it would be nice if the agents' current task
#: changed whilst being on the lock screen" -- an agent with no entry here
#: just holds its configured status forever; its task cycle is itself alone,
#: so it never enters the round-robin below and never gets a `next_change_ms`.
_DEMO_TASK_SCRIPTS: dict[str, list[str]] = {
    "Personal Assistant": [
        "✓ Morning brief ready",
        "Rescheduling your 3pm with Dana",
        "Booking a table for Friday, 7:30",
        "✓ Table booked at Dishoom",
    ],
    "Social Media Manager": [
        "✓ 3 posts scheduled",
        "Replying to 12 comments",
        "Drafting Thursday's launch thread",
        "Reviewing reel analytics",
    ],
    "Accountant": [
        "✓ 41 invoices reconciled",
        "Categorising card spend",
        "Chasing 2 overdue invoices",
        "Preparing the VAT summary",
    ],
    "Sales Manager": [
        "✓ 4 follow-ups sent",
        "Updating the pipeline",
        "Booking a demo with Northwind",
        "Drafting a proposal for Acme",
    ],
    "Customer Service": [
        "✓ Inbox cleared",
        "Answering 3 new tickets",
        "Processing a refund for #4821",
        "Tagging feedback for the roadmap",
    ],
}

#: How long a normal scripted status holds before its turn ends.
_DEMO_TASK_DWELL_S = 11.0
#: A completion beat ("✓ ...") reads as just-finished rather than as the
#: ongoing task, so its turn is a much shorter beat before moving on.
_DEMO_TASK_DONE_DWELL_S = 3.0


def _demo_task_clock() -> float:
    """Wall-clock seconds. A function, not a bare `time.time()` call, so a
    test can substitute a fixed or stepped clock without sleeping in real
    time to observe the rotation."""
    return time.time()


def _demo_task_cycle(name: str, configured_status: str) -> list[str]:
    """The full rotation for a demo agent: its configured status first, then
    any scripted follow-ons for that name. No script -> a cycle of exactly
    one entry, which never changes and never enters the round-robin below."""
    script = _DEMO_TASK_SCRIPTS.get(name)
    if not script:
        return [configured_status]
    return [configured_status, *script]


def _demo_task_dwell(status: str) -> float:
    """Seconds a status holds once it is this agent's turn."""
    return (
        _DEMO_TASK_DONE_DWELL_S
        if status.startswith("✓ ")
        else _DEMO_TASK_DWELL_S
    )


#: One entry per turn: (agent name, status shown during the turn, the turn's
#: start offset within the schedule's period, and its dwell).
_DemoTurn = tuple[str, str, float, float]


def _demo_rotation_schedule(
    rotating: list[tuple[str, list[str]]],
) -> tuple[list[_DemoTurn], float]:
    """The round-robin schedule shared by every rotating demo agent.

    Independent per-agent clocks were the first thing tried here, each with
    its own phase offset -- and they cannot deliver "never two agents change
    within 2s of each other": with 5 agents x 5 states cycling every ~40s,
    the birthday-paradox math means SOME pair lands under 2s apart no matter
    how the offsets are chosen (verified by exhaustive search: the best
    achievable minimum gap tops out under 2s). A round-robin fixes this by
    construction instead of by tuning: agents take turns in list order, one
    state each, and only the agent whose turn it is changes -- so the gap
    between ANY two changes, anywhere, is always exactly one turn's dwell
    (>= _DEMO_TASK_DONE_DWELL_S, comfortably over 2s), never a coincidence of
    two independent clocks landing close together.

    One "round" gives every rotating agent exactly one turn, in order; the
    schedule repeats once every agent has looped back to its own first state
    at the same instant, i.e. after lcm(len(cycle) for each agent) rounds.
    """
    lengths = [len(cycle) for _, cycle in rotating]
    rounds = 1
    for length in lengths:
        rounds = rounds * length // math.gcd(rounds, length)
    turns: list[_DemoTurn] = []
    t = 0.0
    for round_ in range(rounds):
        for name, cycle in rotating:
            status = cycle[round_ % len(cycle)]
            dwell = _demo_task_dwell(status)
            turns.append((name, status, t, dwell))
            t += dwell
    return turns, t


def _demo_agent_rotation_state(
    turns: list[_DemoTurn], period: float, name: str, now: float
) -> tuple[str, float]:
    """(current status, seconds until it next changes) for one agent's own
    turns within the shared schedule `turns` built by _demo_rotation_schedule.

    Time-based, not poll-counted: recomputed fresh from `now` every call, so
    the server stays authoritative -- a poll landing late or early never
    desyncs from what the rotation says right now, and a client-side
    animation can never be reverted by a poll that catches a change mid-air.
    """
    own = [(status, start, dwell) for turn_name, status, start, dwell in turns
           if turn_name == name]
    local = now % period
    current = own[-1]  # default: still holding from the previous lap's last turn
    for status, start, dwell in own:
        if start <= local:
            current = (status, start, dwell)
        else:
            break
    idx = own.index(current)
    next_start = own[(idx + 1) % len(own)][1]
    remaining = (next_start - local) if next_start > local else (period - local) + next_start
    return current[0], remaining


def _demo_refresh_in_ms(next_change_candidates: list[int]) -> int | None:
    """The top-level `refresh_in_ms`: soonest scheduled change plus a small
    margin so it lands on or just after the change rather than just before
    it, clamped so a pathological value can never make the client hammer the
    endpoint or go silent for the rest of the lock screen's timeout.

    None when nothing is scheduled to change, so the client keeps its own
    15s default instead of being told to poll on an arbitrary cadence.
    """
    if not next_change_candidates:
        return None
    return max(1000, min(15000, min(next_change_candidates) + 150))


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
    # Its status reads "Idle" (Jay; it used to say "On device", i.e. WHERE it
    # is). This endpoint runs pre-auth and has no cheap, truthful way to read
    # the agent's activity, and "Idle" is also a resting status to the page,
    # so the island draws at rest.
    agents.insert(0, {
        "name": "taOS Agent",
        "framework": "omp",
        "framework_icon": _framework_icon("omp"),
        "status": "Idle",
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

        # Rotating "current task": each demo agent with a script (see
        # _DEMO_TASK_SCRIPTS) cycles through it turn by turn in a shared
        # round-robin, list order, instead of sitting on its configured
        # status forever. Computed fresh from the clock on every request --
        # the SERVER is authoritative, so a 15s poll landing while the client
        # is mid-animation can never revert what the client is showing, and a
        # late or early poll just sees whatever the rotation says right now
        # rather than drifting out of sync with it.
        rotating = [
            (a["name"], _demo_task_cycle(a["name"], a["status"]))
            for a in agents
            if a.get("demo") and len(_demo_task_cycle(a["name"], a["status"])) > 1
        ]
        if rotating:
            now = _demo_task_clock()
            turns, period = _demo_rotation_schedule(rotating)
            rotating_names = {name for name, _ in rotating}
            for agent in agents:
                if agent["name"] not in rotating_names:
                    continue
                status, remaining = _demo_agent_rotation_state(
                    turns, period, agent["name"], now
                )
                agent["status"] = status
                agent["next_change_ms"] = int(round(remaining * 1000))

    # Live device agents: physical boards that are plugged in right now.
    #
    # MERGED HERE rather than served from their own endpoint because the lock
    # screen already polls this one and reconciles the islands by key -- a
    # second list would mean a second poll and two painters racing over the
    # same row. Their keys are namespaced (`device:<slug>`) so a board can
    # never collide with a TAOS_LOCK_DEMO_AGENTS placeholder of the same name.
    if _device_agents_enabled():
        for entry in _device_live():
            agents.append(_device_island(entry))

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
    # NO cap on the islands (Jay: "there shouldnt be a cap"); the feed scrolls.
    # A cap of six silently cut off a plugged-in board, which is appended last,
    # whenever five demo agents were configured. New agents go at the BOTTOM,
    # in arrival order (Jay; custom ordering comes later).
    visible = agents
    payload = {
        "agents": visible,
        "agent_total": len(agents),
        "agent_running": running,
        "tasks": tasks[:4],
        "task_total": len(tasks),
        "threads": bool(demo),
    }
    # Only agents actually SENT can schedule the client's next fetch -- a
    # rotation on an agent not sent would tell the client to poll
    # sooner for a change it could never paint anyway.
    refresh_in_ms = _demo_refresh_in_ms(
        [a["next_change_ms"] for a in visible if "next_change_ms" in a]
    )
    if refresh_in_ms is not None:
        payload["refresh_in_ms"] = refresh_in_ms
    return JSONResponse(payload)



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


def _demo_panels_enabled() -> bool:
    """Whether the scripted phone/mailbox/apps/decisions/settings panels are on.

    Same two-flag shape as the stacks, and for the same reason: the master flag
    must remain the one move that takes down everything invented on this
    pre-sign-in screen. It also means a device can run the agent islands -- the
    part of this screen that shows REAL state -- with none of the scripted
    inbox content beside them.
    """
    if not _demo_enabled():
        return False
    return bool(os.environ.get("TAOS_LOCK_DEMO_PANELS", "").strip())


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
    # A DEVICE thread is real, not scripted, so it is checked first and on its
    # own flag. The sheet polls one endpoint either way -- it should not have
    # to know whether the agent it is talking to is a board or a placeholder.
    if _device_agents_enabled() and _DEVICE_SLUG_RE.match(slug or ""):
        live = {e["slug"] for e in _device_live()}
        with _DEVICE_LOCK:
            thread = list(_DEVICE_THREADS.get(slug, []))
        if slug in live or thread:
            return JSONResponse({
                "slug": slug, "messages": thread, "device": True, "demo": True,
                # The sheet greys the composer when the board has gone.
                "online": slug in live,
            })

    if not _demo_enabled():
        return JSONResponse({"error": "not found"}, status_code=404)

    safe = _avatar_slug(slug)
    if not safe:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"slug": safe, "messages": _demo_thread(safe), "demo": True})


# ---------------------------------------------------------------------------
# DEVICE AGENTS: a physical board that appears on the lock screen while it is
# plugged in, and disappears when it is not.
#
# Jay's taOSusb demo. A Pi Zero on a USB expansion board runs a real agent
# (PicoClaw), heartbeats here every 5s, and is asked to do things from the
# lock screen's chat sheet. @taOS-dev owns the device half; this is the phone
# half, and the contract between them is TAOSUSB-DEMO.md.
#
# ⛔ THE THING TO KEEP HOLD OF: this screen renders BEFORE SIGN-IN, so what is
# built here lets someone holding a LOCKED phone make a physical device run a
# command. That is deliberate, it is what Jay asked for, and it is why every
# route below is (a) behind its own flag, off on a real device, (b) marked
# `demo: true` in every payload, (c) console-only, and (d) useless without the
# pairing token.
#
# ⚠ THE DEVICE-SIDE CONTROL IS NOT AN ALLOWLIST. The spec promised one and I
# repeated it here; @taOS-dev then measured that PicoClaw v0.3.1 cannot do it
# (`custom_allow_patterns` only EXEMPTS from its deny list, and the real
# allowlist field is unreachable from config). The control is the OS instead,
# and it is stronger: the agent runs unprivileged with no sudo under a
# hardened unit, and the single privileged action -- the OS update -- goes
# through a root drop box, the same shape as /run/taos-power here. Recorded
# because "there is an allowlist" is the kind of comfortable sentence that
# outlives the thing it describes.
#
# STATE IS IN MEMORY AND THAT IS CORRECT. A device agent exists only while it
# is heartbeating; a restart of the controller should forget every board, not
# resurrect one that was unplugged an hour ago.

#: Contract numbers, agreed with @taOS-dev on bus 4475/4480. The device beats
#: every 5s; two missed beats is gone. The 8s is NOT arbitrary -- the shot list
#: wants the island gone ~15s after the board is unplugged, and the lock
#: screen's widgets poll is the other half of that budget.
_DEVICE_LIVENESS_SECS = 8.0

#: Slugs are REJECTED, not sanitised. The slug lands in a URL path and is the
#: key of a thread, so two boards that differ only in case or punctuation must
#: not quietly become one conversation.
_DEVICE_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")

#: Device islands live in their OWN key namespace. The lock screen reconciles
#: islands by key, so a board called "taosusb" and a TAOS_LOCK_DEMO_AGENTS
#: entry of the same name would otherwise fight over one island -- each poll
#: would hand the other's payload to the same node.
_DEVICE_KEY_PREFIX = "device:"

#: Thread caps. `apt upgrade` on a Pi Zero emits thousands of lines, and this
#: is a pre-auth screen holding the output in the phone's memory. The device
#: trims first (a few progress lines and a summary, 4 KB per message) -- these
#: are the backstop for when it does not.
_DEVICE_THREAD_LINES = 200
_DEVICE_THREAD_BYTES = 64 * 1024
_DEVICE_MESSAGE_BYTES = 4 * 1024
#: What may be RELAYED to the board. @taOS-dev's agentd 400s above 2000, so
#: sending more is a round trip that can only fail.
_DEVICE_SEND_CHARS = 2000
#: Ceiling on the per-board dedup set.
_DEVICE_SEEN_MAX = 2000
_DEVICE_TRIM_MARKER = "…earlier output trimmed"

#: Live boards and their threads. Guarded because heartbeats, messages and the
#: lock screen's polls all arrive on different requests.
_DEVICE_LOCK = threading.Lock()
_DEVICE_AGENTS: dict[str, dict] = {}
_DEVICE_THREADS: dict[str, list[dict]] = {}
#: (id, seq) pairs already appended, per slug. A device that retries a POST it
#: already delivered must not print the same progress line twice.
_DEVICE_SEEN: dict[str, set] = {}


def _device_agents_enabled() -> bool:
    """Its OWN flag, not the demo-agents one.

    Device agents are a bigger exposure than a scripted island -- they carry a
    real command path to real hardware -- so turning the scripted demo on must
    never turn this on as a side effect.
    """
    return bool(os.environ.get("TAOS_LOCK_DEMO_DEVICE_AGENTS", "").strip())


def _device_token() -> str | None:
    """The pairing token, from a file the drop-in points at.

    NOT an environment variable: the value would then be readable in
    /proc/<pid>/environ by anything running as the user, and it is the only
    thing standing between "someone on the Wi-Fi" and an island on a locked
    phone. @taOS-dev generates it at provisioning; the device reads its own
    copy from /etc/taosusb/pair.token.
    """
    path = os.environ.get("TAOS_DEVICE_AGENT_TOKEN_FILE", "").strip()
    if not path:
        return None
    try:
        token = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


def _device_authorised(request: Request) -> bool:
    """Constant-time compare against the pairing token.

    A missing token file is a REFUSAL, never an open door: a misconfigured
    phone that accepted unauthenticated heartbeats would look exactly like a
    working one right up until someone else's board appeared on the screen.
    """
    want = _device_token()
    if not want:
        return False
    header = request.headers.get("authorization", "")
    prefix = "bearer "
    if not header.lower().startswith(prefix):
        return False
    return secrets.compare_digest(header[len(prefix):].strip(), want)


def _device_live(now: float | None = None) -> list[dict]:
    """Boards heard from within the liveness window, newest first."""
    now = time.time() if now is None else now
    with _DEVICE_LOCK:
        return [
            dict(entry) for entry in _DEVICE_AGENTS.values()
            if now - entry.get("last_seen", 0.0) <= _DEVICE_LIVENESS_SECS
        ]


def _device_island(entry: dict) -> dict:
    """One live board, in the shape the lock screen's islands already speak.

    `link` is rendered, never inferred. The device measures it from
    /sys/class/udc/*/state: "usb" means a computer really enumerated the
    gadget, "power" means it is on a charger with nobody plugged in. Guessing
    here would turn a measurement back into a story.
    """
    link = entry.get("link") or ""
    status = "Online · USB" if link == "usb" else "Online"
    if link == "power":
        # Jay: "power only" -> "idle". Lowercase "idle" is also a RESTING
        # status to the page, so the island draws as at rest, not busy.
        status = "Idle"
    return {
        "key": _DEVICE_KEY_PREFIX + entry["slug"],
        "name": entry.get("name") or entry["slug"],
        "framework": entry.get("framework", ""),
        "framework_icon": _framework_icon(entry.get("framework", "")),
        "status": status,
        "avatar": _avatar_url(entry.get("name") or entry["slug"]),
        # Both true, and both load-bearing: `device` is what makes the page
        # send to this agent for real instead of drawing a bubble, and `demo`
        # is what marks the whole surface as demo content.
        "device": True,
        "demo": True,
        "slug": entry["slug"],
    }


def _device_thread_append(slug: str, message: dict) -> None:
    """Append one message, then hold the thread to its caps.

    Trimmed from the FRONT with a marker rather than silently: a sheet that
    quietly loses the beginning of a health check reads as the agent having
    answered something else.
    """
    with _DEVICE_LOCK:
        thread = _DEVICE_THREADS.setdefault(slug, [])
        thread.append(message)
        trimmed = False
        while len(thread) > _DEVICE_THREAD_LINES:
            thread.pop(0)
            trimmed = True
        total = sum(len(m.get("text", "")) for m in thread)
        while total > _DEVICE_THREAD_BYTES and len(thread) > 1:
            total -= len(thread.pop(0).get("text", ""))
            trimmed = True
        if trimmed and thread and thread[0].get("text") != _DEVICE_TRIM_MARKER:
            thread.insert(0, {
                "role": "system", "text": _DEVICE_TRIM_MARKER,
                "at": int(time.time()), "id": "", "seq": -1,
            })


@router.post("/device-agent/heartbeat")
async def device_agent_heartbeat(request: Request):
    """A board saying it is here. Bearer token, demo flag.

    NOT console-only: this arrives over the network from the board, which is
    the whole point. The token is therefore the entire gate, which is why a
    missing token file refuses rather than opens.
    """
    if not _device_agents_enabled():
        return JSONResponse({"error": "not found"}, status_code=404)
    if not _device_authorised(request):
        return JSONResponse({"error": "unauthorised"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    slug = str(body.get("slug", "")).strip()
    if not _DEVICE_SLUG_RE.match(slug):
        return JSONResponse({"error": "bad slug"}, status_code=400)
    # THE URL IS PINNED TO THE CALLER, AND REBUILT FROM PARTS.
    #
    # @taOS-dev demonstrated the hole rather than arguing it: a heartbeat
    # claiming url="http://127.0.0.1:<port>/internal/admin-action?x=" made the
    # phone POST to "/internal/admin-action?x=/chat" -- the query swallows the
    # path this code used to append -- against a LOCAL service, carrying the
    # bearer token. That is an SSRF with credentials, from a pre-auth surface.
    #
    # Two defences, because one of them is only a filter. The host must be the
    # address the heartbeat actually came from, so a board can only ever name
    # itself; and the stored value is REBUILT from scheme/host/port, so there
    # is no attacker-controlled string left to concatenate a path onto.
    url = str(body.get("url", "")).strip()
    parsed = urlparse(url)
    peer = getattr(getattr(request, "client", None), "host", None)
    if parsed.scheme != "http" or not parsed.hostname:
        return JSONResponse({"error": "bad url"}, status_code=400)
    if parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        return JSONResponse({"error": "url must have no path or query"}, status_code=400)
    if parsed.username or parsed.password:
        return JSONResponse({"error": "bad url"}, status_code=400)
    if peer and parsed.hostname != peer:
        # A board may only point at itself. The address it connects FROM is
        # the one fact here nobody on the other end gets to choose.
        return JSONResponse({"error": "url must match the caller"}, status_code=400)
    try:
        port = parsed.port or 80
    except ValueError:
        return JSONResponse({"error": "bad url"}, status_code=400)
    url = "http://%s:%d" % (parsed.hostname, port)
    with _DEVICE_LOCK:
        _DEVICE_AGENTS[slug] = {
            "slug": slug,
            "name": str(body.get("name", "")).strip() or slug,
            "framework": str(body.get("framework", "")).strip().lower(),
            "url": url,
            "link": str(body.get("link", "")).strip().lower(),
            "last_seen": time.time(),
        }
    return JSONResponse({"ok": True, "slug": slug, "demo": True})


@router.post("/device-agent/message")
async def device_agent_message(request: Request):
    """Progress lines and final replies, posted back by the board."""
    if not _device_agents_enabled():
        return JSONResponse({"error": "not found"}, status_code=404)
    if not _device_authorised(request):
        return JSONResponse({"error": "unauthorised"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    slug = str(body.get("slug", "")).strip()
    if not _DEVICE_SLUG_RE.match(slug):
        return JSONResponse({"error": "bad slug"}, status_code=400)
    with _DEVICE_LOCK:
        known = slug in _DEVICE_AGENTS
    if not known:
        # A board we have never heard heartbeat from cannot write to a thread.
        return JSONResponse({"error": "unknown device"}, status_code=404)
    msg_id = str(body.get("id", "")).strip()
    try:
        seq = int(body.get("seq", 0))
    except (TypeError, ValueError):
        return JSONResponse({"error": "seq must be an integer"}, status_code=400)
    # Truncated HERE as well as on the device. The device promises 4 KB per
    # message; this is what happens when a promise meets a bug.
    text = str(body.get("text", ""))[:_DEVICE_MESSAGE_BYTES]

    with _DEVICE_LOCK:
        seen = _DEVICE_SEEN.setdefault(slug, set())
        if len(seen) > _DEVICE_SEEN_MAX:
            # Bounded: this grows once per message for the life of a board, and
            # a board that is plugged in all day would otherwise be a slow leak
            # on a phone. Clearing wholesale (rather than evicting) can only
            # ever re-admit a retry of something older than the last 2000
            # messages, which cannot still be in flight.
            seen.clear()
        if (msg_id, seq) in seen:
            # Idempotent by contract: a retried delivery is a no-op, not a
            # second copy of the same line.
            return JSONResponse({"ok": True, "duplicate": True, "demo": True})
        seen.add((msg_id, seq))
    _device_thread_append(slug, {
        "role": "agent", "text": text, "at": int(time.time()),
        "id": msg_id, "seq": seq, "done": bool(body.get("done")),
    })
    return JSONResponse({"ok": True, "demo": True})


@router.post("/lock-send/{slug}")
async def lock_send(slug: str, request: Request):
    """Relay what was typed on the lock screen to the board. Console-only.

    Returns as soon as the board has ACCEPTED the text. It must not wait for
    the work: `apt update` on a Zero takes 30-90s and an upgrade takes many
    minutes, and a lock screen whose send button hangs for a minute reads as
    broken. The replies come back through /device-agent/message.
    """
    if not _request_is_console(request):
        return JSONResponse({"error": "console only"}, status_code=403)
    if not _device_agents_enabled():
        return JSONResponse({"error": "not found"}, status_code=404)
    if not _DEVICE_SLUG_RE.match(slug or ""):
        return JSONResponse({"error": "not found"}, status_code=404)
    live = {entry["slug"]: entry for entry in _device_live()}
    entry = live.get(slug)
    if entry is None:
        # Unplugged between the island being drawn and the send landing.
        return JSONResponse({"error": "device is not online"}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = str(body.get("text", "")).strip()
    if not text:
        return JSONResponse({"error": "text required"}, status_code=400)
    if len(text) > _DEVICE_SEND_CHARS:
        # Refused here rather than sent and bounced: the board answers 400
        # above this, and a round trip that can only fail is worse than a
        # straight answer.
        return JSONResponse(
            {"error": "text too long", "max": _DEVICE_SEND_CHARS}, status_code=400
        )

    token = _device_token()
    msg_id = secrets.token_hex(8)
    # The user's own words go into the thread FIRST, so the sheet shows what
    # was asked even if the board never answers.
    _device_thread_append(slug, {
        "role": "user", "text": text[:_DEVICE_MESSAGE_BYTES],
        "at": int(time.time()), "id": msg_id, "seq": 0,
    })
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                entry["url"] + "/chat",
                json={"id": msg_id, "text": text},
                headers={"Authorization": "Bearer %s" % token} if token else {},
            )
    except httpx.HTTPError as exc:
        # The board's own failure, said out loud: "no route to host" is a
        # different problem from "the agent refused it".
        return JSONResponse(
            {"error": "device did not answer", "detail": str(exc)}, status_code=502
        )
    if resp.status_code >= 400:
        return JSONResponse(
            {"error": "device refused", "status": resp.status_code}, status_code=502
        )
    return JSONResponse({"ok": True, "id": msg_id, "slug": slug, "demo": True})


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
        "source": "agent",
        "app": "Agents",
        "mono": "ta",
        "tint": "#4c9aff",
        "items": (
            (6, "Accountant finished reconciling", "September invoices are matched. 2 need your eye."),
            (52, "Social Media Manager posted", "3 scheduled posts went out this morning."),
        ),
    },
    {
        "source": "system",
        "app": "System",
        "mono": "sy",
        "tint": "#8e8e93",
        "items": (
            (23, "Battery health check passed", "Capacity 94%. Next check in 30 days."),
            (140, "taOS updated to build 412", "Lock screen panels and the new Projects view."),
        ),
    },
    {
        "source": "security",
        "app": "Security",
        "mono": "se",
        "tint": "#ff9f0a",
        "items": (
            (88, "New sign-in on the desktop app", "From your usual network. Tap if this was not you."),
        ),
    },
    {
        "source": "backup",
        "app": "Backup",
        "mono": "bk",
        "tint": "#30d158",
        "items": (
            (300, "Nightly backup completed", "412 MB in 41s. Nothing skipped."),
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


#: The four panels the view row reaches and nothing had ever put anything in:
#: phone, mailbox, apps and decisions, plus the settings sheet. Same rule as the
#: notification stacks and for the same reason -- THIS SCREEN RENDERS BEFORE
#: SIGN-IN, so every line here is scripted and server-side and there is no code
#: path from any of it to a real account. A "helpful" wiring of the mailbox to
#: the user's actual inbox would be a pre-auth leak, not a feature.
#:
#: `at` offsets are minutes-ago rather than timestamps, so the phone reads as
#: having had a plausible morning whenever the demo is run.
#:
#: Phone numbers are drawn from Ofcom's 07700 900xxx drama range, which is
#: reserved for fiction and can never reach a real subscriber.
_DEMO_PHONE: tuple[dict, ...] = (
    {
        "key": "call-kenwright",
        "kind": "missed",
        "app": "Phone",
        "who": "Dave Kenwright",
        "detail": "Mobile · 07700 900461",
        "minutes": 22,
        "glyph": "phone",
        "tint": "#34c759",
    },
    {
        "key": "call-wa-brightside",
        "kind": "missed",
        # Jay named the app by the name it carries on the phone.
        "app": "WA+",
        "who": "Brightside Joinery",
        "detail": "WhatsApp Business · voice call",
        "minutes": 47,
        "mono": "WA",
        "tint": "#25d366",
    },
    {
        "key": "call-twilio-agent",
        "kind": "missed",
        "app": "Twilio",
        "who": "taOS agent line",
        # The one entry that is about the product rather than the person: an
        # agent holds a phone number and something rang it while the user was
        # away. That is the whole point of the demo.
        "detail": "Inbound · 07700 900118 · agent was mid-task",
        "minutes": 63,
        "mono": "TW",
        "tint": "#f22f46",
    },
    {
        "key": "call-wa-ellis",
        "kind": "missed",
        "app": "WA+",
        "who": "Ellis & Daughters",
        "detail": "WhatsApp Business · 2 calls",
        "minutes": 140,
        "mono": "WA",
        "tint": "#25d366",
    },
    {
        "key": "call-dentist",
        "kind": "missed",
        "app": "Phone",
        "who": "Mersey Dental Practice",
        "detail": "Mobile · 07700 900233",
        "minutes": 8,
        "glyph": "phone",
        "tint": "#34c759",
    },
    {
        "key": "call-wa-northlight",
        "kind": "missed",
        "app": "WA+",
        "who": "Northlight Systems",
        "detail": "WhatsApp Business · video call",
        "minutes": 35,
        "mono": "WA",
        "tint": "#25d366",
    },
    {
        "key": "call-twilio-outbound",
        "kind": "missed",
        "app": "Twilio",
        "who": "taOS agent line",
        "detail": "Callback requested · 07700 900874",
        "minutes": 112,
        "mono": "TW",
        "tint": "#f22f46",
    },
    {
        "key": "call-unknown",
        "kind": "missed",
        "app": "Phone",
        "who": "No caller ID",
        "detail": "Mobile · 2 calls",
        "minutes": 171,
        "glyph": "phone",
        "tint": "#34c759",
    },
    {
        "key": "voicemail-brightside",
        "kind": "voicemail",
        "app": "Voicemail",
        "who": "Brightside Joinery",
        "detail": "1:12 · \u201c\u2026chasing the invoice, give us a ring\u2026\u201d",
        "minutes": 210,
        "glyph": "voicemail",
        "tint": "#8e8e93",
    },
    {
        "key": "voicemail-hargreaves",
        "kind": "voicemail",
        "app": "Voicemail",
        "who": "Hargreaves & Co",
        "detail": "0:38 · “…bringing the revised drawings Thursday…”",
        "minutes": 96,
        "glyph": "voicemail",
        "tint": "#8e8e93",
    },
)

#: Unified messaging, explicitly the BlackBerry Hub shape Jay asked for: mail,
#: SMS, X DMs and LinkedIn in ONE stream ordered by arrival. The per-item source
#: is the design, not decoration -- a unified list that does not say where each
#: line came from is just a worse inbox.
_DEMO_MAILBOX: tuple[dict, ...] = (
    {
        "key": "mail-hargreaves",
        "source": "mail",
        "app": "Mail",
        "who": "Hargreaves & Co",
        "subject": "Re: Thursday's site visit",
        "preview": "09:15 works for us. I'll bring the revised drawings.",
        "minutes": 12,
        "glyph": "mail",
        "tint": "#2f6fd0",
        "unread": True,
    },
    {
        "key": "dm-x-marcus",
        "source": "x",
        "app": "X",
        "who": "@marcus_dev",
        "subject": "Direct message",
        "preview": "what's the actual memory floor for running this on a 4GB board?",
        "minutes": 26,
        "mono": "X",
        "tint": "#3b3b42",
        "unread": True,
    },
    {
        "key": "sms-sam",
        "source": "sms",
        "app": "Messages",
        "who": "Sam",
        "subject": "SMS",
        "preview": "are you still alright for Sunday?",
        "minutes": 19,
        "glyph": "sms",
        "tint": "#25c05d",
        "unread": True,
    },
    {
        "key": "li-recruiter",
        "source": "linkedin",
        "app": "LinkedIn",
        "who": "Priya Raman",
        "subject": "InMail",
        "preview": "Saw the on-device agent work — are you open to a conversation?",
        "minutes": 88,
        "mono": "in",
        "tint": "#0a66c2",
        "unread": True,
    },
    {
        "key": "mail-lfc",
        "source": "mail",
        "app": "Mail",
        "who": "Liverpool FC",
        "subject": "Ticket ballot result: Newcastle (H)",
        "preview": "Your ballot result is ready to view.",
        "minutes": 68,
        "glyph": "mail",
        "tint": "#2f6fd0",
        "unread": True,
    },
    {
        "key": "mail-companies-house",
        "source": "mail",
        "app": "Mail",
        "who": "Companies House",
        "subject": "Confirmation statement due 3 October",
        "preview": "No action needed if your details are unchanged.",
        "minutes": 74,
        "glyph": "mail",
        "tint": "#2f6fd0",
        "unread": False,
    },
    {
        "key": "li-post",
        "source": "linkedin",
        "app": "LinkedIn",
        "who": "Northlight Systems",
        "subject": "Message",
        "preview": "Thanks for the walkthrough yesterday — sending the write-up over.",
        "minutes": 190,
        "mono": "in",
        "tint": "#0a66c2",
        "unread": False,
    },
    {
        "key": "dm-x-agentdev",
        "source": "x",
        "app": "X",
        "who": "@agentops",
        "subject": "Direct message",
        "preview": "Would you do a walkthrough of the lock screen for the newsletter?",
        "minutes": 44,
        "mono": "X",
        "tint": "#3b3b42",
        "unread": True,
    },
    {
        "key": "mail-stripe",
        "source": "mail",
        "app": "Mail",
        "who": "Payments",
        "subject": "Payout of \u00a32,410.00 is on its way",
        "preview": "Expected in your account on Thursday.",
        "minutes": 51,
        "glyph": "mail",
        "tint": "#2f6fd0",
        "unread": True,
    },
    {
        "key": "sms-dentist",
        "source": "sms",
        "app": "Messages",
        "who": "Mersey Dental",
        "subject": "SMS",
        "preview": "Reminder: appointment Friday 11:20. Reply C to confirm.",
        "minutes": 63,
        "glyph": "sms",
        "tint": "#25c05d",
        "unread": True,
    },
    {
        "key": "li-northlight",
        "source": "linkedin",
        "app": "LinkedIn",
        "who": "Dan Mercer",
        "subject": "Message",
        "preview": "Good to meet you Tuesday \u2014 sending the pilot scope across.",
        "minutes": 121,
        "mono": "in",
        "tint": "#0a66c2",
        "unread": False,
    },
    {
        "key": "mail-hosting",
        "source": "mail",
        "app": "Mail",
        "who": "Hetzner",
        "subject": "Scheduled maintenance, Sunday 02:00\u201304:00 UTC",
        "preview": "One reboot expected. No action required.",
        "minutes": 240,
        "glyph": "mail",
        "tint": "#2f6fd0",
        "unread": False,
    },
    {
        "key": "dm-x-liverpool",
        "source": "x",
        "app": "X",
        "who": "@anfieldwatch",
        "subject": "Direct message",
        "preview": "spare for Newcastle if you still want one",
        "minutes": 275,
        "mono": "X",
        "tint": "#3b3b42",
        "unread": False,
    },
    {
        "key": "sms-o2",
        "source": "sms",
        "app": "Messages",
        "who": "O2",
        "subject": "SMS",
        "preview": "You've used 80% of your data allowance this month.",
        "minutes": 310,
        "glyph": "sms",
        "tint": "#25c05d",
        "unread": False,
    },
)

#: The four apps Jay named. A badge is a count; `note` is the one line the tile
#: shows underneath, because a grid of bare icons on a lock screen says nothing
#: a user could act on.
_DEMO_APPS: tuple[dict, ...] = (
    {
        "key": "app-instagram",
        "app": "Instagram",
        "mono": "ig",
        "tint": "#c13584",
        "badge": 7,
        "note": "3 DMs, 4 mentions",
    },
    {
        "key": "app-reddit",
        "app": "Reddit",
        "mono": "r",
        "tint": "#ff4500",
        "badge": 12,
        "note": "Reply on “Running an agent OS”",
    },
    {
        "key": "app-bank",
        "app": "Bank",
        "mono": "£",
        "tint": "#1b7f5a",
        "badge": 1,
        # A balance would be the one genuinely sensitive-looking line on a
        # pre-auth screen, so the tile says a payment needs a look and no more.
        "note": "Card payment needs approval",
    },
    {
        "key": "app-youtube",
        "app": "YouTube",
        "mono": "▶",
        "tint": "#ff0000",
        "badge": 3,
        "note": "3 new from your subscriptions",
    },

    {
        "key": "app-whatsapp",
        "app": "WhatsApp",
        "mono": "wa",
        "tint": "#25d366",
        "badge": 14,
        "note": "4 chats, 2 business",
    },
    {
        "key": "app-x",
        "app": "X",
        "mono": "X",
        "tint": "#3b3b42",
        "badge": 9,
        "note": "12 posts from people you follow",
    },
    {
        "key": "app-photos",
        "app": "Photos",
        "mono": "ph",
        "tint": "#ff9f0a",
        "badge": 0,
        "note": "Yesterday's shots ready",
    },
    {
        "key": "app-calendar",
        "app": "Calendar",
        "mono": "16",
        "tint": "#ff453a",
        "badge": 2,
        "note": "Site visit 09:15 Thursday",
    },
)

#: Pending approvals waiting on the user. These are the lock screen's reason to
#: exist: an agent got far enough to need a human and stopped. Each carries the
#: agent that is blocked, so the panel reads as "who is waiting on me".
_DEMO_DECISIONS: tuple[dict, ...] = (
    {
        "key": "dec-invoice",
        "title": "Pay Brightside Joinery invoice",
        "detail": "£1,840.00 · matches quote BJ-2291 · due Friday",
        "agent": "finance",
        "minutes": 31,
    },
    {
        "key": "dec-reply",
        "title": "Send drafted reply to Hargreaves & Co",
        "detail": "Confirms 09:15 Thursday and asks for parking details",
        "agent": "inbox",
        "minutes": 54,
    },
    {
        "key": "dec-refund",
        "title": "Approve \u00a3120 refund to Ellis & Daughters",
        "detail": "Duplicate charge on order ED-8841 \u00b7 confirmed by the bank feed",
        "agent": "finance",
        "minutes": 18,
    },
    {
        "key": "dec-hire",
        "title": "Book the Thursday site survey",
        "detail": "09:15 slot held \u00b7 confirms to Hargreaves and blocks your morning",
        "agent": "diary",
        "minutes": 42,
    },
    {
        "key": "dec-spend",
        "title": "Renew the Twilio number for 12 months",
        "detail": "\u00a38.50/mo \u00b7 the agent line drops if it lapses on the 28th",
        "agent": "ops",
        "minutes": 96,
    },
    {
        "key": "dec-post",
        "title": "Publish the Northlight case study",
        "detail": "Drafted and proofed \u00b7 goes to the site and LinkedIn",
        "agent": "comms",
        "minutes": 150,
    },
    {
        "key": "dec-deploy",
        "title": "Deploy taos-website build 412",
        "detail": "All checks green · changes the pricing page copy",
        "agent": "builder",
        "minutes": 120,
    },
)

#: PROJECTS -- the tab that replaced settings (Jay: "makes sense as its a
#: projects focused os"). It sits second in the row, right after the agents.
#:
#: A project is a body of work with agents on it, so each row says how far along
#: it is, how many agents are working it, and the one thing that happened most
#: recently. `blocked` is the state the lock screen exists to surface: work that
#: has stopped and is waiting on a person.
#:
#: Read-only, and deliberately so. This replaced a panel of pre-auth ACTIONS
#: ("stop all agents" reachable by anyone holding the phone), and swapping it
#: for content removed that exposure rather than moving it somewhere else.
_DEMO_PROJECTS: tuple[dict, ...] = (
    {
        "key": "prj-brightside",
        "name": "Brightside Joinery fit-out",
        "note": "Quote accepted · scheduling the survey",
        "progress": 72,
        "agents": 3,
        "blocked": True,
        "mono": "BJ",
        "tint": "#ffb020",
        "minutes": 31,
    },
    {
        "key": "prj-taos-site",
        "name": "taOS website relaunch",
        "note": "Build 412 green · pricing copy rewritten",
        "progress": 88,
        "agents": 2,
        "blocked": False,
        "mono": "tw",
        "tint": "#4c9aff",
        "minutes": 54,
    },
    {
        "key": "prj-handset",
        "name": "Handset launch build",
        "note": "Lock screen panels landed · splash handover next",
        "progress": 64,
        "agents": 4,
        "blocked": False,
        "mono": "hd",
        "tint": "#30d158",
        "minutes": 12,
    },
    {
        "key": "prj-accounts",
        "name": "Year end accounts",
        "note": "Waiting on two receipts · filing due 3 October",
        "progress": 40,
        "agents": 1,
        "blocked": True,
        "mono": "ya",
        "tint": "#bf5af2",
        "minutes": 190,
    },
    {
        "key": "prj-ellis",
        "name": "Ellis & Daughters shopfit",
        "note": "Survey booked \u00b7 materials list out for pricing",
        "progress": 22,
        "agents": 2,
        "blocked": False,
        "mono": "ED",
        "tint": "#ff9f0a",
        "minutes": 78,
    },
    {
        "key": "prj-voice",
        "name": "Agent voice comms",
        "note": "Spec only \u00b7 walkie-talkie over the volume keys",
        "progress": 8,
        "agents": 1,
        "blocked": True,
        "mono": "vc",
        "tint": "#ff375f",
        "minutes": 25,
    },
    {
        "key": "prj-splash",
        "name": "Boot splash handover",
        "note": "Wordmark holds to first paint \u00b7 fade tuning left",
        "progress": 80,
        "agents": 1,
        "blocked": False,
        "mono": "bs",
        "tint": "#5e5ce6",
        "minutes": 420,
    },
    {
        "key": "prj-northlight",
        "name": "Northlight pilot",
        "note": "Write-up drafted, ready to send",
        "progress": 95,
        "agents": 1,
        "blocked": False,
        "mono": "np",
        "tint": "#64d2ff",
        "minutes": 300,
    },
)


def _demo_panels() -> dict:
    """Every scripted panel, timestamped relative to now and newest-first.

    One payload for all five rather than an endpoint each: they are all the same
    switch, they are all static tables, and five pollers on one screen is five
    chances to repaint something the user is reading. The client paints each
    panel from its own key, so a panel the payload omits is simply empty.
    """
    now = time.time()

    def stamped(rows: tuple[dict, ...]) -> list[dict]:
        out = []
        for spec in rows:
            item = dict(spec)
            if "minutes" in item:
                item["at"] = now - (item.pop("minutes") * 60)
            # Marked at construction, like the stacks: nothing downstream should
            # have to work out that these are placeholders by elimination.
            item["demo"] = True
            out.append(item)
        return out

    phone = stamped(_DEMO_PHONE)
    phone.sort(key=lambda item: item["at"], reverse=True)
    mailbox = stamped(_DEMO_MAILBOX)
    mailbox.sort(key=lambda item: item["at"], reverse=True)
    decisions = stamped(_DEMO_DECISIONS)
    decisions.sort(key=lambda item: item["at"], reverse=True)
    # Projects lead with whatever moved most recently, the way the rest of this
    # screen does -- except that anything BLOCKED comes first regardless. A
    # project waiting on a person is the reason to look at this panel, and it
    # going quiet is precisely what would sink it to the bottom of a pure
    # recency sort.
    projects = stamped(_DEMO_PROJECTS)
    projects.sort(key=lambda item: (item["blocked"], item["at"]), reverse=True)
    return {
        "phone": phone,
        "mailbox": mailbox,
        # Apps are a fixed grid: their order is the author's, not the clock's.
        "apps": stamped(_DEMO_APPS),
        "decisions": decisions,
        "projects": projects,
    }


def _demo_agent_names() -> list[str]:
    """The demo agent labels, parsed exactly as /auth/lock-widgets parses them.

    Keyed off the SAME env var rather than a second list, so the stats panel and
    the islands can never disagree about who is running. A separate table here
    would drift the first time Jay edited one drop-in and not the other.
    """
    demo = os.environ.get("TAOS_LOCK_DEMO_AGENTS", "").strip()
    names: list[str] = []
    for raw in demo.split(","):
        parts = [seg.strip() for seg in raw.split(":")]
        if parts and parts[0] and parts[0] not in names:
            names.append(parts[0])
    return names


#: Island statuses that mean "not working". The same set lock_widgets uses to
#: count running agents, so the stats panel and the islands agree about who is
#: busy -- an agent the islands show idle must not be the one burning CPU here.
_SIM_RESTING = frozenset({"", "stopped", "idle", "exited", "error"})

#: The handset the model is shaped for: a Snapdragon 778G with 8 GB. MemTotal
#: on an 8 GB phone reads a little under 8 GiB once the carve-outs are taken.
_SIM_MEM_TOTAL_KB = 7_650_000

#: Seconds between the history samples the sparklines draw -- the poll cadence,
#: so one poll moves the history along by one bar.
_SIM_STEP = 3.0
_SIM_HISTORY = 20

#: The real CPU reading, smoothed across polls. Blended in as the OS's own base
#: load so the phone's actual activity still shows through, without a single
#: noisy /proc/stat delta making the whole panel jump.
_SIM_BASE_EMA: dict[str, float] = {}


def _demo_agent_specs() -> list[tuple[str, bool]]:
    """(name, busy) per demo agent, parsed exactly as /auth/lock-widgets does.

    Busy is the island's own answer: a free-text status ("Drafting replies") is
    working, a resting word is not, and an agent with no status is "running".
    """
    demo = os.environ.get("TAOS_LOCK_DEMO_AGENTS", "").strip()
    out: list[tuple[str, bool]] = []
    seen: set[str] = set()
    for raw in demo.split(","):
        parts = [seg.strip() for seg in raw.split(":")]
        name = parts[0] if parts else ""
        if not name or name in seen:
            continue
        seen.add(name)
        status = parts[2] if len(parts) > 2 and parts[2] else "running"
        out.append((name, status.strip().lower() not in _SIM_RESTING))
    return out


def _sim_lattice(seed: int, i: int) -> float:
    """A stable pseudo-random value in [-1, 1] for integer lattice point i."""
    return (zlib.crc32(b"%d:%d" % (seed, i)) / 0xFFFFFFFF) * 2.0 - 1.0


def _sim_noise(x: float, seed: int) -> float:
    """Smooth 1-D value noise in [-1, 1].

    Continuous in x (smoothstep between lattice values), so a value sampled
    3 s apart can only have moved so far. That is the whole point of deriving
    the readings from TIME rather than from random() per request: a poll can
    never land on a number unrelated to the previous one.
    """
    i = math.floor(x)
    f = x - i
    u = f * f * (3.0 - 2.0 * f)
    a = _sim_lattice(seed, i)
    return a + (_sim_lattice(seed, i + 1) - a) * u


def _sim_activity(t: float, seed: int, busy: bool) -> float:
    """How hard an agent is working at time t, 0..1.

    Busy agents work in swells (a slow ~20 s drift) with bursts on top (~6 s),
    so they sometimes pause to think and sometimes run flat out. Idle agents sit
    near the floor with a faint heartbeat.
    """
    if busy:
        swell = 0.5 + 0.5 * _sim_noise(t / 18.0, seed)
        burst = 0.5 + 0.5 * _sim_noise(t / 5.5, seed + 101)
        a = 0.12 + 0.5 * swell + 0.38 * burst
    else:
        a = 0.03 + 0.05 * (0.5 + 0.5 * _sim_noise(t / 17.0, seed + 202))
    return max(0.0, min(1.0, a))


def _sim_agent(name: str, busy: bool, t: float) -> dict:
    """One agent's readings at time t, all driven by the same activity."""
    seed = zlib.crc32(name.encode("utf-8", "replace"))
    a = _sim_activity(t, seed, busy)
    # Memory follows the work, but slowly: a lagged mean of the last minute.
    a_slow = sum(_sim_activity(t - k * 8.0, seed, busy) for k in range(8)) / 8.0
    peak_cpu = 6.0 + (seed % 7)                  # % of the whole device
    return {
        "name": name,
        "busy": busy,
        "activity": a,
        "cpu_percent": 0.3 + a * peak_cpu,
        "ram_mb": 150.0 + (seed % 230) + a_slow * (120.0 + (seed >> 8) % 200),
        "tokens_per_s": a * (12.0 + (seed >> 4) % 20) if busy else a * 2.0,
        "requests_per_min": a * (5.0 + (seed >> 12) % 9),
        "net_kbps": 2.0 + a * (50.0 + (seed >> 16) % 140),
        # Storage creeps, a day's worth over a day, and never shrinks.
        "storage_mb": 35.0 + (seed % 420) + ((t % 86400) / 86400.0) * 14.0,
    }


def _sim_system_cpu(t: float, specs: list[tuple[str, bool]], base: float) -> tuple[float, float]:
    """(system CPU %, sum of agent shares) at time t."""
    agents = sum(_sim_agent(n, b, t)["cpu_percent"] for n, b in specs)
    return min(97.0, base + agents), agents


def _stats_model(
    now: float,
    specs: list[tuple[str, bool]],
    real_cpu: float | None = None,
    real_mem: dict | None = None,
) -> dict:
    """The stats panel's one coherent model: agents and system from one clock.

    Everything is a function of `now` and the agent list, so the numbers are
    CONSISTENT rather than merely plausible each on their own:

      system CPU     = OS base load + the sum of every agent's CPU share
      memory used    = OS base + the sum of every agent's RSS
      tokens, network = the sum of the agents'
      temperature     follows CPU with a long lag, power with a short one

    The OS base is the phone's REAL CPU reading when there is one (smoothed
    across polls), so what the phone is actually doing still shows through.
    """
    if real_cpu is not None:
        prev = _SIM_BASE_EMA.get("cpu")
        ema = real_cpu if prev is None else prev + 0.35 * (real_cpu - prev)
        _SIM_BASE_EMA["cpu"] = ema
        measured = max(2.5, min(30.0, 2.5 + 0.5 * ema))
    else:
        measured = None

    def base_at(ts: float) -> float:
        # With no real reading the OS load is simulated on the same clock, so
        # the history bars are exactly what earlier polls printed.
        if measured is not None:
            return measured
        return 4.0 + 1.5 * (0.5 + 0.5 * _sim_noise(ts / 11.0, 7))

    base = base_at(now)

    agents = [_sim_agent(n, b, now) for n, b in specs]
    cpu, agents_cpu = _sim_system_cpu(now, specs, base)

    total_kb = _SIM_MEM_TOTAL_KB
    frac = 0.26
    if real_mem and real_mem.get("total_kb"):
        frac = max(0.18, min(0.40, real_mem.get("used_kb", 0) / real_mem["total_kb"]))
    agents_kb = sum(a["ram_mb"] for a in agents) * 1024.0
    used_kb = min(total_kb * 0.94, total_kb * frac + agents_kb)

    def lagged(tau: float) -> float:
        # An exponentially weighted look back over the last few taus.
        num = den = 0.0
        for k in range(10):
            w = math.exp(-k / 3.0)
            ts = now - k * tau / 3.0
            num += w * _sim_system_cpu(ts, specs, base_at(ts))[0]
            den += w
        return num / den

    temp_c = 31.0 + 0.3 * lagged(25.0)
    power_w = 0.8 + 0.075 * lagged(4.0)

    history_cpu: list[float] = []
    history_agents: list[float] = []
    history_tokens: list[float] = []
    per_agent_hist: dict[str, list[float]] = {a["name"]: [] for a in agents}
    for k in range(_SIM_HISTORY - 1, -1, -1):
        ts = now - k * _SIM_STEP
        snap = [_sim_agent(n, b, ts) for n, b in specs]
        a_cpu = sum(s["cpu_percent"] for s in snap)
        history_cpu.append(round(min(97.0, base_at(ts) + a_cpu), 1))
        history_agents.append(round(a_cpu, 1))
        history_tokens.append(round(sum(s["tokens_per_s"] for s in snap), 1))
        for s in snap:
            per_agent_hist[s["name"]].append(round(s["cpu_percent"], 1))

    out_agents = []
    for a in agents:
        out_agents.append({
            "name": a["name"],
            "busy": a["busy"],
            "cpu_percent": round(a["cpu_percent"], 1),
            "ram_mb": int(a["ram_mb"]),
            "storage_mb": round(a["storage_mb"], 1),
            "tokens_per_s": round(a["tokens_per_s"], 1),
            "requests_per_min": round(a["requests_per_min"], 1),
            "history": per_agent_hist[a["name"]],
            # Marked at construction, like every other invented row on this
            # screen, so nothing downstream has to deduce it.
            "demo": True,
        })

    return {
        "cpu_percent": round(cpu, 1),
        "memory": {
            "total_kb": int(total_kb),
            "used_kb": int(used_kb),
            "percent": round(used_kb * 100.0 / total_kb, 1),
            "agents_kb": int(agents_kb),
        },
        "agents": out_agents,
        "system": {
            "base_cpu_percent": round(base, 1),
            "agents_cpu_percent": round(agents_cpu, 1),
            "tokens_per_s": round(sum(a["tokens_per_s"] for a in agents), 1),
            "requests_per_min": round(sum(a["requests_per_min"] for a in agents), 1),
            "net_kbps": round(6.0 + sum(a["net_kbps"] for a in agents), 1),
            "temp_c": round(temp_c, 1),
            "power_w": round(power_w, 2),
        },
        "history": {
            "step_s": _SIM_STEP,
            "cpu": history_cpu,
            "agents_cpu": history_agents,
            "tokens_per_s": history_tokens,
        },
        "demo": True,
    }


def _demo_agent_usage() -> list[dict]:
    """Per-agent readings from the shared stats model, at the current time.

    Jay asked for "live demo data for agents cpu, ram and storage usage", and
    the product owner then for numbers that CORRELATE with the system's: both
    now come from _stats_model, so an agent's CPU share is part of the system
    CPU the panel shows beside it.
    """
    return _stats_model(time.time(), _demo_agent_specs())["agents"]


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

    # Per-agent CPU / RAM / storage. Jay: "in the stats it should show live demo
    # data for agents cpu, ram and storage usage".
    #
    # DEMO ONLY, and gated on the master demo flag, because taOS does not
    # measure per-agent resource use on this handset yet. The key is absent
    # rather than an empty list when the flag is off -- "no agents running" and
    # "nothing is measuring agents" are different answers, and this endpoint
    # already draws that distinction for every hardware reading above.
    if _demo_enabled():
        specs = _demo_agent_specs()
        if specs:
            # ONE model for the agents and the system, so the totals are the
            # agents' sums plus the OS's own load rather than two unrelated
            # sets of numbers. The real CPU and memory readings feed it as the
            # base, which is how the phone's actual activity still shows.
            model = _stats_model(time.time(), specs, cpu, mem)
            payload["cpu_percent"] = model["cpu_percent"]
            payload["memory"] = model["memory"]
            payload["agents"] = model["agents"]
            payload["system"] = model["system"]
            payload["history"] = model["history"]

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


# ---------------------------------------------------------------------------
# THE INCOMING-CALL DEMO. "Send to PA".
#
# Jay: an incoming call from Naira lands in the lock screen's live zone, and
# the headline answer is to hand it to the personal assistant, who takes a
# message while the user watches the transcript and can take over at any
# moment. When the PA finishes, a calendar event has been added.
#
# DEMO CONTENT, and it can never be anything else. This screen renders BEFORE
# sign-in, so the caller, the script and the calendar event are all fixed
# strings in this file. There is no code path from here to a phone line, a
# contact list or a calendar: the "event" exists only as a notification this
# process serves until it is reset. The number is from Ofcom's 07700 900xxx
# drama range, reserved for fiction.
#
# Behind its OWN flag, TAOS_LOCK_DEMO_CALL, independent of the others: the call
# is triggered by the demo daemon on the handset over loopback, and a device can
# run it with or without the rest of the scripted content.
# ---------------------------------------------------------------------------

#: Who calls. A fictional number, and a label rather than a contact record.
_CALL_CALLER: dict = {
    "name": "Naira",
    "label": "mobile",
    "number": "07700 900318",
}

#: The script. The PA's opening line is Jay's, verbatim, and must stay so.
_CALL_SCRIPT: tuple[tuple[str, str], ...] = (
    ("pa", "Hi, and thank you for calling Jason's phone. This is his personal "
           "assistant speaking — can I take a message?"),
    ("caller", "Oh hi! Could you ask him to give me a call back? Any time after "
               "five is fine."),
    ("pa", "Of course. Does half past five work for you?"),
    ("caller", "Half five's perfect, thank you."),
    ("pa", "Lovely — I've added it to his calendar, and he'll call you at "
           "5:30. Have a good afternoon!"),
    ("caller", "You too, bye!"),
)

#: Speech pacing. 200 words a minute: Jay, from the glass, "the agent needs to
#: appear to be talking slightly faster" -- it was 165 (360ms a word), which
#: read as the PA reading aloud. Both voices move together so the exchange
#: keeps its rhythm. The floor keeps a two-word line from flashing past; the
#: gap is the breath between turns and the lead-in is the PA picking up.
_CALL_MS_PER_WORD = 300
_CALL_MIN_LINE_MS = 900
_CALL_GAP_MS = 520
_CALL_LEAD_MS = 700

#: What the PA leaves behind: a reminder, in its own voice, shown at the top of
#: the Alerts panel until the user dismisses it. Jay: "reminder to call naira
#: at time etc added to calendar, I will remind you closer to the time". It is
#: never written anywhere -- the "calendar" is this dict.
_CALL_REMINDER: dict = {
    "kind": "reminder",
    "from": "Your PA",
    "title": "Reminder added",
    "body": "Reminder to call Naira at 5:30 pm added to your calendar. "
            "I'll remind you closer to the time.",
    "event": "Call Naira",
    "time": "5:30 pm",
}

#: Every action the page may send. Anything else is a 400; one of these at the
#: wrong moment is a 409.
_CALL_ACTIONS = frozenset({"answer", "decline", "voicemail", "pa", "takeover", "end"})


def _call_timeline() -> list[dict]:
    """The script as offsets from the moment the PA picked up.

    Computed, not tabled: each line lasts as long as its words take to say, so
    editing a line cannot leave its timing describing the old one.
    """
    out: list[dict] = []
    at = _CALL_LEAD_MS
    for who, text in _CALL_SCRIPT:
        words = len(text.split())
        dur = max(_CALL_MIN_LINE_MS, words * _CALL_MS_PER_WORD)
        out.append({"who": who, "text": text, "at_ms": at, "dur_ms": dur})
        at += dur + _CALL_GAP_MS
    return out


def _call_demo_enabled() -> bool:
    """Whether the incoming-call demo is switched on. Its own flag, alone."""
    return bool(os.environ.get("TAOS_LOCK_DEMO_CALL", "").strip())


class _LockCall:
    """One scripted call, as a small state machine. In memory, one per process.

    idle -> ringing -> pa | live (answered) | ended (declined, voicemail)
    pa   -> live (taken over) | ended (pa-done, ended-by-user)
    live -> ended (ended-by-user)

    There is NO background thread. The PA's progress is a pure function of the
    clock and the moment the PA picked up, and the pa -> ended(pa-done) step is
    taken lazily by whichever read notices the script has run out. That keeps
    the state inspectable at any instant and lets a test drive it with a fake
    clock instead of sleeping through thirty seconds of dialogue.
    """

    def __init__(self, clock=time.monotonic, wall=time.time):
        self._clock = clock
        self._wall = wall
        self._lock = threading.Lock()
        self._timeline = _call_timeline()
        self._script_ms = (self._timeline[-1]["at_ms"] + self._timeline[-1]["dur_ms"])
        self.notification: dict | None = None
        self._reset_locked()

    # -- internals, all called with the lock held --------------------------
    def _reset_locked(self) -> None:
        self.state = "idle"
        self.outcome: str | None = None
        self.call_id = getattr(self, "call_id", 0)
        self._since = self._clock()
        self._since_wall = self._wall()
        self._pa_started: float | None = None
        self._connected: float | None = None
        self._taken_over_ms: int | None = None
        self._ended_ms: int | None = None

    def _enter(self, state: str, outcome: str | None = None) -> None:
        self.state = state
        self.outcome = outcome
        self._since = self._clock()
        self._since_wall = self._wall()

    def _pa_ms(self) -> int:
        if self._pa_started is None:
            return 0
        return int(round((self._clock() - self._pa_started) * 1000))

    def _advance(self) -> None:
        """Take the lazy step: a PA whose script has run out has finished."""
        if self.state == "pa" and self._pa_ms() >= self._script_ms:
            # Backdated to the instant the last line ended, not to whenever
            # somebody happened to look.
            self._ended_ms = self._script_ms
            self._enter("ended", "pa-done")
            self.notification = {
                **_CALL_REMINDER,
                "at": self._wall(),
                "demo": True,
            }

    def _transcript(self) -> tuple[list[dict], dict | None]:
        """Lines heard so far, and who is speaking right now (or None)."""
        if self._pa_started is None:
            return [], None
        if self.state == "pa":
            now = self._pa_ms()
        elif self._taken_over_ms is not None:
            now = self._taken_over_ms
        elif self._ended_ms is not None:
            now = self._ended_ms
        else:
            now = self._script_ms
        lines: list[dict] = []
        speaking = None
        for index, line in enumerate(self._timeline):
            if line["at_ms"] > now:
                break
            entry = dict(line)
            end = line["at_ms"] + line["dur_ms"]
            if now < end:
                progress = (now - line["at_ms"]) / line["dur_ms"]
                if self.state == "pa":
                    speaking = {"who": line["who"], "line": index,
                                "progress": round(progress, 4)}
                else:
                    # Cut off mid-sentence by a take-over or a hang-up: the
                    # page reveals only what had been said.
                    entry["upto"] = round(progress, 4)
            lines.append(entry)
        return lines, speaking

    def _snapshot(self) -> dict:
        transcript, speaking = self._transcript()
        connected_ms = None
        if self._connected is not None and self.state in ("pa", "live"):
            connected_ms = int(round((self._clock() - self._connected) * 1000))
        return {
            "demo": True,
            "call_id": self.call_id,
            "state": self.state,
            "caller": dict(_CALL_CALLER) if self.state != "idle" else None,
            "outcome": self.outcome,
            "since": self._since_wall,
            "elapsed_ms": connected_ms,
            "taken_over": self._taken_over_ms is not None,
            "transcript": transcript,
            "speaking": speaking,
            "notification": dict(self.notification) if self.notification else None,
        }

    # -- the public surface --------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            self._advance()
            return self._snapshot()

    def ring(self) -> tuple[int, dict]:
        """Start a call. Idempotent while it is already ringing."""
        with self._lock:
            self._advance()
            if self.state == "ringing":
                return 200, self._snapshot()
            if self.state in ("pa", "live"):
                return 409, {"error": "a call is already in progress",
                             "state": self.state}
            # From idle or from a finished call. A finished call's transcript
            # goes; the calendar event it added stays until reset.
            self.call_id += 1
            self._pa_started = None
            self._connected = None
            self._taken_over_ms = None
            self._ended_ms = None
            self._enter("ringing")
            return 201, self._snapshot()

    def reset(self) -> dict:
        with self._lock:
            self._reset_locked()
            self.notification = None
            return self._snapshot()

    def dismiss(self) -> tuple[int, dict]:
        """The user put the reminder away. 409 when there is none to put away."""
        with self._lock:
            self._advance()
            if self.notification is None:
                return 409, {"error": "nothing to dismiss", "state": self.state}
            self.notification = None
            return 200, self._snapshot()

    def act(self, action: str) -> tuple[int, dict]:
        if action not in _CALL_ACTIONS:
            return 400, {"error": "unknown action"}
        with self._lock:
            # Advance FIRST: a take-over tapped after the PA had already
            # finished is a take-over of nothing, and must not un-end the call.
            self._advance()
            state = self.state
            if state == "ringing" and action == "answer":
                self._connected = self._clock()
                self._enter("live")
            elif state == "ringing" and action == "decline":
                self._enter("ended", "declined")
            elif state == "ringing" and action == "voicemail":
                self._enter("ended", "voicemail")
            elif state == "ringing" and action == "pa":
                self._pa_started = self._clock()
                self._connected = self._pa_started
                self._enter("pa")
            elif state == "pa" and action == "takeover":
                self._taken_over_ms = self._pa_ms()
                self._enter("live")
            elif state == "pa" and action == "end":
                self._ended_ms = self._pa_ms()
                self._enter("ended", "ended-by-user")
            elif state == "live" and action == "end":
                self._enter("ended", "ended-by-user")
            else:
                return 409, {"error": "not allowed now", "state": state,
                             "action": action}
            return 200, self._snapshot()


_LOCK_CALL = _LockCall()


def _call_gate(request: Request):
    """The two refusals every call route shares, in the order they matter."""
    if not _request_is_console(request):
        return JSONResponse({"error": "console only"}, status_code=403)
    if not _call_demo_enabled():
        return JSONResponse({"error": "not found"}, status_code=404)
    return None


@router.get("/lock-call")
async def lock_call(request: Request):
    """The call as the lock screen should draw it right now. Console-only.

    Polled by the page (every ~2s idle, ~300ms during a call) because the push
    stream is not guaranteed to exist; a 404 means the demo is off and the page
    stops asking.
    """
    refused = _call_gate(request)
    if refused is not None:
        return refused
    return JSONResponse(_LOCK_CALL.snapshot())


@router.post("/lock-call/ring")
async def lock_call_ring(request: Request):
    """Ring the phone: Naira calling. Console-only; the demo daemon's button."""
    refused = _call_gate(request)
    if refused is not None:
        return refused
    status, body = _LOCK_CALL.ring()
    if status < 300:
        # No content on the stream -- the page fetches /auth/lock-call itself.
        body["delivered"] = _push_lock_event("call", {"state": body["state"]})
    return JSONResponse(body, status_code=status)


@router.post("/lock-call/reset")
async def lock_call_reset(request: Request):
    """Back to idle, and the calendar notification goes with it. Console-only."""
    refused = _call_gate(request)
    if refused is not None:
        return refused
    body = _LOCK_CALL.reset()
    body["delivered"] = _push_lock_event("call", {"state": body["state"]})
    return JSONResponse(body)


@router.post("/lock-call/dismiss")
async def lock_call_dismiss(request: Request):
    """Put away the reminder the PA left. Console-only.

    Cleared SERVER-side, so the next poll does not bring it back; 409 when
    there is nothing to dismiss, so a double tap is visible as such.
    """
    refused = _call_gate(request)
    if refused is not None:
        return refused
    status, body = _LOCK_CALL.dismiss()
    if status == 200:
        body["delivered"] = _push_lock_event("call", {"state": body["state"]})
    return JSONResponse(body, status_code=status)


@router.post("/lock-call/action")
async def lock_call_action(request: Request):
    """Answer, decline, voicemail, send to the PA, take over, or end.

    400 for anything that is not one of those six, 409 for one of them at the
    wrong moment -- a take-over while it is still ringing, say.
    """
    refused = _call_gate(request)
    if refused is not None:
        return refused
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "body must be JSON"}, status_code=400)
    if not isinstance(body, dict) or not isinstance(body.get("action"), str):
        return JSONResponse({"error": "action required"}, status_code=400)
    status, out = _LOCK_CALL.act(body["action"].strip())
    if status == 200:
        out["delivered"] = _push_lock_event("call", {"state": out["state"]})
    return JSONResponse(out, status_code=status)


#: Where the privileged helper looks for a power request. The controller runs as
#: `taos` and CANNOT power the handset off itself -- logind answers "challenge"
#: to that user, and a challenge on a device with no keyboard and no polkit
#: agent is a refusal. It drops a verb here instead and taos-power.path (root)
#: acts on it. The directory is 0700 taos:taos, so this is not a channel anyone
#: else can shout down.
_POWER_REQUEST = "/run/taos-power/request"

#: Listeners on /auth/lock-events. The power key is a PHYSICAL button: the menu
#: has to be on screen by the time the user's thumb lifts, so this is a push.
#: The lock screen's fastest poll is 3s and its panels are 15 MINUTES -- a menu
#: that arrives on a poll is a broken menu.
_LOCK_EVENT_WAITERS: set = set()


def _push_lock_event(kind: str, payload: dict | None = None) -> int:
    """Fan an event out to every open lock-screen stream. Returns the count.

    The count is returned rather than discarded so the caller -- and the test --
    can tell "delivered to nobody" from "delivered", which are the same silence
    otherwise.
    """
    delivered = 0
    for queue in list(_LOCK_EVENT_WAITERS):
        try:
            queue.put_nowait((kind, payload or {}))
            delivered += 1
        except Exception:
            # A full or closed queue is one dead listener, not a reason to drop
            # the event for everyone else.
            _LOCK_EVENT_WAITERS.discard(queue)
    return delivered


@router.get("/lock-events")
async def lock_events(request: Request):
    """Server-sent events for the lock screen. Console-only.

    Carries only UI signals the device itself raises -- today, "the power key
    was held". It deliberately carries no content: everything on this screen is
    fetched by its own endpoint, and this stream renders before sign-in, so it
    must never become a second way to read anything.
    """
    if not _request_is_console(request):
        return JSONResponse({"error": "console only"}, status_code=403)

    queue: asyncio.Queue = asyncio.Queue(maxsize=8)
    _LOCK_EVENT_WAITERS.add(queue)

    async def stream():
        try:
            # An immediate byte, so the browser's EventSource resolves its
            # connection rather than sitting in CONNECTING until the first real
            # event -- which could be hours.
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    kind, payload = await asyncio.wait_for(queue.get(), timeout=20)
                except asyncio.TimeoutError:
                    # A comment line. Without it a silent stream is
                    # indistinguishable from a dead one, and the socket is free
                    # to be reaped by anything in between.
                    yield ": keepalive\n\n"
                    continue
                # The payload rides in `data` rather than being baked into the
                # event NAME. Encoding it in the name meant one listener per
                # combination -- four volume events became eight the moment the
                # screen state joined them -- and each new dimension doubled it.
                yield "event: %s\ndata: %s\n\n" % (kind, json.dumps(payload))
        finally:
            _LOCK_EVENT_WAITERS.discard(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


#: The panel's backlight. Discovered rather than hardcoded: the node is named
#: after the DSI controller (ae94000.dsi.0 on this handset), so a different
#: panel or a kernel rename would break a literal path.
_BACKLIGHT_DIR = "/sys/class/backlight"


def _backlight_path() -> str | None:
    """The first backlight device's directory, or None if there is no panel."""
    try:
        names = sorted(os.listdir(_BACKLIGHT_DIR))
    except OSError:
        return None
    return os.path.join(_BACKLIGHT_DIR, names[0]) if names else None


def _read_brightness() -> dict | None:
    """Current and maximum backlight level, or None if unreadable.

    Returns raw levels rather than a percentage, and the client does the
    arithmetic: the maximum here is 4095, and rounding through a 0-100 integer
    on the way in and out would make the slider jump under the finger.
    """
    base = _backlight_path()
    if not base:
        return None
    try:
        with open(os.path.join(base, "brightness")) as handle:
            current = int(handle.read().strip())
        with open(os.path.join(base, "max_brightness")) as handle:
            maximum = int(handle.read().strip())
    except (OSError, ValueError):
        return None
    if maximum <= 0:
        return None
    return {"current": current, "max": maximum,
            "percent": round(current * 100.0 / maximum, 1)}


def _write_brightness(level: int) -> dict | None:
    """Set the backlight, then READ IT BACK and report what the panel took.

    ⚠ THE WRITE'S OWN RESULT CANNOT BE TRUSTED HERE, measured on the device:
    writing through a shell reported "write error: Invalid argument" for three
    different values in a row while the level plainly changed -- the final read
    showed the last value written. The driver accepts the value and then errors
    on close, so the exit status describes the REQUEST and not the STATE. The
    only honest answer is the read-back, which is what this returns.

    Refuses to go fully dark. A slider that can reach 0 on a phone with no
    hardware brightness key leaves a screen that is on, unreadable, and looks
    broken -- and the way back is a control the user can no longer see.
    """
    base = _backlight_path()
    if not base:
        return None
    reading = _read_brightness()
    if not reading:
        return None
    floor = max(1, int(reading["max"] * 0.04))
    level = max(floor, min(reading["max"], int(level)))
    try:
        with open(os.path.join(base, "brightness"), "w") as handle:
            handle.write(str(level))
    except OSError:
        # Deliberately NOT a failure return: the value may well have landed.
        # The read-back below is the measurement.
        pass
    return _read_brightness()


def _read_radios() -> dict:
    """WiFi and Bluetooth state. Reading needs no privilege; writing does.

    WiFi is read from NetworkManager because NM owns it, and Bluetooth from
    rfkill because that is what the switch actually sets. Reading each from the
    thing that controls it means the switch can never show a state its own
    write would not produce.
    """
    import subprocess

    state: dict = {}
    try:
        got = subprocess.run(
            ["nmcli", "-t", "-f", "WIFI", "g"],
            capture_output=True, text=True, timeout=4,
        )
        if got.returncode == 0:
            state["wifi"] = got.stdout.strip().lower().startswith("enabled")
    except Exception:
        pass
    try:
        got = subprocess.run(
            ["rfkill", "-n", "-o", "TYPE,SOFT,HARD", "list", "bluetooth"],
            capture_output=True, text=True, timeout=4,
        )
        if got.returncode == 0 and got.stdout.strip():
            fields = got.stdout.split()
            # "bluetooth unblocked unblocked" -- on only when NEITHER block is
            # set. A hard block is a physical kill switch and software cannot
            # clear it, so a switch that ignored it would be a lie.
            state["bluetooth"] = ("blocked" not in fields[1:3])
    except Exception:
        pass
    return state


@router.get("/lock-radios")
async def lock_radios(request: Request):
    """Current WiFi/Bluetooth state. Console-only."""
    if not _request_is_console(request):
        return JSONResponse({"error": "console only"}, status_code=403)
    return JSONResponse(_read_radios())


#: Radio verbs the drop box will accept, mapped from what the page sends.
def _write_power_request(verb: str) -> None:
    """Drop a verb for the root helper, durably and in one place.

    atomic_io gives what the hand-rolled temp+replace here did not: an fsync,
    which matters on a verb that powers the machine off, and a RANDOM temp
    name opened O_EXCL. All three callers used to share the fixed name
    `<request>.part`, so two near-simultaneous taps could interleave and let
    the camera button turn Wi-Fi off. (@taOS-dev, reviewing #3108.)

    ⚠ IT WILL ALSO CREATE THE PARENT, AND THIS PARENT MUST NOT BE CREATED
    HERE. /run/taos-power is 0700 taos:taos by design, and a directory this
    process made under the default umask would be 0755 -- widening the single
    boundary taos-power-apply's trust argument rests on ("the directory is
    0700 owned by taos, so that means the controller and root"). A missing
    drop box means the helper is not installed, which is worth saying out
    loud rather than papering over: every caller already promised a 503.
    """
    parent = Path(_POWER_REQUEST).parent
    if not parent.is_dir():
        raise FileNotFoundError("drop box directory is missing: %s" % parent)
    atomic_write_text(Path(_POWER_REQUEST), verb)


_RADIO_VERBS = {
    ("wifi", True): "wifi-on",
    ("wifi", False): "wifi-off",
    ("bluetooth", True): "bt-on",
    ("bluetooth", False): "bt-off",
}


@router.post("/lock-radios")
async def set_lock_radios(request: Request):
    """Turn WiFi or Bluetooth on or off. Console-only.

    Goes through the same root drop box as the power menu: NetworkManager
    answers `no` to enable-disable-wifi for this user, and /dev/rfkill is not
    writable by it either.

    ⚠ Turning WiFi off from here can cut the only route to a headless handset.
    That is correct for a switch a PERSON flicks -- every phone allows it -- and
    it is exactly why the verb list is closed and nothing automated writes it.
    """
    if not _request_is_console(request):
        return JSONResponse({"error": "console only"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    radio = str(body.get("radio", "")).strip()
    want = body.get("on")
    if radio not in ("wifi", "bluetooth") or not isinstance(want, bool):
        return JSONResponse({"error": "radio and on required"}, status_code=400)

    verb = _RADIO_VERBS[(radio, want)]
    try:
        _write_power_request(verb)
    except OSError as exc:
        return JSONResponse(
            {"error": "request failed", "detail": str(exc)}, status_code=503
        )
    # The helper is triggered by a systemd path unit, so it runs a moment after
    # the file lands. Wait, then report the READ-BACK rather than the request --
    # a switch that reports what it asked for is a switch that lies when the
    # radio refuses.
    await asyncio.sleep(1.2)
    return JSONResponse(_read_radios())


@router.post("/lock-volume-key")
async def lock_volume_key(request: Request):
    """A volume key went down or came up. Console-only.

    Posted by the compositor. The BEHAVIOUR is not decided here: the page owns
    which surface is open, which agent is focused and whether the first press
    has been spent, and splitting that across a route and a page would give two
    places a different idea of whether the slider is showing.
    """
    if not _request_is_console(request):
        return JSONResponse({"error": "console only"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    key = str(body.get("key", "")).strip()
    action = str(body.get("action", "")).strip()
    if key not in ("up", "down") or action not in ("press", "release"):
        return JSONResponse({"error": "bad key or action"}, status_code=400)
    # Whether the panel was dark when the key went down. The compositor knows
    # and the page does not, and it changes how the arc is drawn: over black
    # rather than over the whole lock screen.
    screen = "off" if str(body.get("screen", "")).strip() == "off" else "on"
    delivered = _push_lock_event(
        "volume-%s-%s" % (key, action), {"screen": screen}
    )
    return JSONResponse({"ok": True, "delivered": delivered})


def _read_volume() -> dict | None:
    """Current output volume via PipeWire, or None if there is nothing to ask.

    ⚠ MEASURED ON THIS HANDSET: PipeWire is running and answers
    `wpctl get-volume @DEFAULT_AUDIO_SINK@` with 1.00, but `wpctl status` lists
    NO SINKS AND NO SOURCES. So the number is real and there is nothing behind
    it -- setting it would succeed and make nothing louder. `no_sink` is
    reported rather than hidden, because a volume slider that silently drives
    nothing is worse than one that says so.
    """
    import subprocess

    env = dict(os.environ, XDG_RUNTIME_DIR="/run/user/%d" % os.getuid())
    try:
        got = subprocess.run(
            ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
            capture_output=True, text=True, timeout=4, env=env,
        )
    except Exception:
        return None
    if got.returncode != 0:
        return None
    # "Volume: 0.75" or "Volume: 0.75 [MUTED]"
    parts = got.stdout.split()
    level = None
    for index, token in enumerate(parts):
        if token.rstrip(":").lower() == "volume" and index + 1 < len(parts):
            try:
                level = float(parts[index + 1])
            except ValueError:
                level = None
            break
    if level is None:
        return None
    sinks = False
    try:
        status = subprocess.run(
            ["wpctl", "status"], capture_output=True, text=True, timeout=4, env=env
        ).stdout
        after = status.split("Sinks:", 1)
        # A populated list has an id line under the heading; an empty one goes
        # straight to the next section.
        sinks = bool(after[1:] and any(
            ch.isdigit() for ch in after[1].split("Sources:", 1)[0]
        ))
    except Exception:
        sinks = False
    return {
        "percent": round(level * 100, 1),
        "muted": "MUTED" in got.stdout.upper(),
        "no_sink": not sinks,
    }


@router.get("/lock-volume")
async def lock_volume(request: Request):
    """Current output volume. Console-only."""
    if not _request_is_console(request):
        return JSONResponse({"error": "console only"}, status_code=403)
    reading = _read_volume()
    if reading is None:
        return JSONResponse({"error": "no audio"}, status_code=404)
    return JSONResponse(reading)


@router.post("/lock-volume")
async def set_lock_volume(request: Request):
    """Set the output volume. Console-only. Returns the READ-BACK."""
    if not _request_is_console(request):
        return JSONResponse({"error": "console only"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        percent = max(0.0, min(100.0, float(body.get("percent"))))
    except (TypeError, ValueError):
        return JSONResponse({"error": "percent required"}, status_code=400)

    import subprocess

    env = dict(os.environ, XDG_RUNTIME_DIR="/run/user/%d" % os.getuid())
    try:
        subprocess.run(
            ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "%.2f" % (percent / 100.0)],
            capture_output=True, text=True, timeout=4, env=env,
        )
    except Exception:
        pass
    after = _read_volume()
    if after is None:
        return JSONResponse({"error": "no audio"}, status_code=404)
    return JSONResponse(after)


#: The torch. Discovered rather than hardcoded: this handset's node is
#: `white:flash`, but the name is the driver's and another panel or kernel would
#: call it something else.
_LEDS_DIR = "/sys/class/leds"

#: Torch brightness as a share of the LED's maximum. NOT full: this is a CAMERA
#: FLASH LED being held on continuously, which is not what it was designed for,
#: and whether this driver limits the current is not something this code can
#: see. 40% is bright enough to be a torch and well inside what the part will
#: take indefinitely.
_TORCH_SHARE = 0.4


def _torch_path() -> str | None:
    """The first flash/torch LED, or None if this device has none."""
    try:
        names = sorted(os.listdir(_LEDS_DIR))
    except OSError:
        return None
    for name in names:
        low = name.lower()
        if "flash" in low or "torch" in low:
            return os.path.join(_LEDS_DIR, name)
    return None


def _read_torch() -> dict | None:
    base = _torch_path()
    if not base:
        return None
    try:
        with open(os.path.join(base, "brightness")) as handle:
            current = int(handle.read().strip())
        with open(os.path.join(base, "max_brightness")) as handle:
            maximum = int(handle.read().strip())
    except (OSError, ValueError):
        return None
    return {"on": current > 0, "level": current, "max": maximum}


@router.get("/lock-torch")
async def lock_torch(request: Request):
    """Whether the torch is lit. Console-only."""
    if not _request_is_console(request):
        return JSONResponse({"error": "console only"}, status_code=403)
    reading = _read_torch()
    if reading is None:
        return JSONResponse({"error": "no torch"}, status_code=404)
    return JSONResponse(reading)


@router.post("/lock-torch")
async def set_lock_torch(request: Request):
    """Light or extinguish the torch. Console-only.

    No root helper needed, unlike the radios: this handset's LED node is
    world-writable (root:feedbackd, rw-rw-rw-), so the controller can drive it
    as itself. Measured before relying on it.

    Returns the READ-BACK, for the same reason brightness does: what the LED
    took is the only honest answer.
    """
    if not _request_is_console(request):
        return JSONResponse({"error": "console only"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    want = body.get("on")
    if not isinstance(want, bool):
        return JSONResponse({"error": "on required"}, status_code=400)
    base = _torch_path()
    reading = _read_torch()
    if not base or reading is None:
        return JSONResponse({"error": "no torch"}, status_code=404)
    level = int(reading["max"] * _TORCH_SHARE) if want else 0
    try:
        with open(os.path.join(base, "brightness"), "w") as handle:
            handle.write(str(level))
    except OSError as exc:
        return JSONResponse(
            {"error": "torch write failed", "detail": str(exc)}, status_code=503
        )
    after = _read_torch()
    return JSONResponse(after or {"error": "unreadable"})


@router.get("/lock-brightness")
async def lock_brightness(request: Request):
    """Current backlight level. Console-only."""
    if not _request_is_console(request):
        return JSONResponse({"error": "console only"}, status_code=403)
    reading = _read_brightness()
    if reading is None:
        return JSONResponse({"error": "no backlight"}, status_code=404)
    return JSONResponse(reading)


@router.post("/lock-brightness")
async def set_lock_brightness(request: Request):
    """Set the backlight. Console-only.

    Reachable before sign-in, like the rest of this screen. Brightness is the
    one setting where that is plainly right: someone holding an unreadably dim
    phone has to be able to fix it without first reading the PIN prompt.
    """
    if not _request_is_console(request):
        return JSONResponse({"error": "console only"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    raw = body.get("level", body.get("percent"))
    if raw is None:
        return JSONResponse({"error": "level or percent required"}, status_code=400)
    reading = _read_brightness()
    if reading is None:
        return JSONResponse({"error": "no backlight"}, status_code=404)
    try:
        if "level" in body:
            level = int(raw)
        else:
            level = int(float(raw) * reading["max"] / 100.0)
    except (TypeError, ValueError):
        return JSONResponse({"error": "not a number"}, status_code=400)
    after = _write_brightness(level)
    if after is None:
        return JSONResponse({"error": "backlight write failed"}, status_code=503)
    return JSONResponse(after)


@router.post("/lock-screen-off")
async def lock_screen_off(request: Request):
    """The panel is being powered down. Put any open sheet away. Console-only.

    Jay: "if I turn the screen off on the power menu it should also dismiss the
    menu". Without this the menu is still up behind a dark screen, so the next
    wake lands on a stale power menu the user has to dismiss before they can do
    anything -- and on a lock screen that reads as the phone being stuck.

    Posted by taos-kiosk-power as it powers the output off, so it covers every
    route to a dark screen that goes through that script rather than only the
    power key.
    """
    if not _request_is_console(request):
        return JSONResponse({"error": "console only"}, status_code=403)
    return JSONResponse({"ok": True, "delivered": _push_lock_event("screen-off")})


@router.post("/lock-screen-on")
async def lock_screen_on(request: Request):
    """The panel is coming back up. Un-blacken the page. Console-only.

    The pair to /auth/lock-screen-off, and the reason both exist: the page
    cannot paint while the output is off, so whatever is on a waking panel is
    the last frame painted before it blanked. That frame is deliberately black,
    and this is what takes it away again.
    """
    if not _request_is_console(request):
        return JSONResponse({"error": "console only"}, status_code=403)
    return JSONResponse({"ok": True, "delivered": _push_lock_event("screen-on")})


#: Where the battery is read from. A module attribute rather than a literal in
#: the reader, so a test can point it at a fake sysfs tree instead of the host's;
#: the env override does the same for a desktop rig with no battery at all.
_POWER_SUPPLY_DIR = os.environ.get("TAOS_POWER_SUPPLY_DIR", "/sys/class/power_supply")

#: The kernel's own words for a battery's state. Anything else in the file is
#: passed on as "" rather than echoed: this goes out on a pre-auth stream.
_BATTERY_STATUSES = ("Charging", "Discharging", "Full", "Not charging", "Unknown")


def _read_battery(root: str | None = None) -> dict:
    """The first power_supply of type Battery: its capacity and status.

    ``percent`` is None when there is no battery or its capacity cannot be read,
    and the charge overlay then says "charging" with no number -- a 0% shown for
    a battery nobody measured would read as a phone about to die.
    """
    base = root if root is not None else _POWER_SUPPLY_DIR
    out: dict = {"percent": None, "status": ""}
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return out
    for name in names:
        node = os.path.join(base, name)
        try:
            with open(os.path.join(node, "type"), "r", encoding="ascii") as fh:
                kind = fh.read().strip()
        except (OSError, UnicodeDecodeError):
            continue
        if kind != "Battery":
            continue
        try:
            with open(os.path.join(node, "capacity"), "r", encoding="ascii") as fh:
                out["percent"] = max(0, min(100, int(fh.read().strip())))
        except (OSError, ValueError, UnicodeDecodeError):
            out["percent"] = None
        try:
            with open(os.path.join(node, "status"), "r", encoding="ascii") as fh:
                status = fh.read().strip()
            out["status"] = status if status in _BATTERY_STATUSES else ""
        except (OSError, UnicodeDecodeError):
            pass
        return out
    return out


@router.post("/lock-charge")
async def lock_charge(request: Request):
    """A charger was plugged in. Play the charge animation. Console-only.

    Product owner: "we need a charger connected notification, screen on and
    animation for 3 seconds". Posted by the handset's session watcher on every
    plug-in, with ``{"screen": "on"|"off"}`` saying whether the panel was lit.
    When it was dark the watcher wakes it 150 ms later, and the page has by then
    painted the overlay over pure black, so the first lit frame is the terminal
    rather than a flash of lock screen.

    The battery reading is taken HERE, from sysfs, rather than trusted from the
    body: the watcher only knows a plug event happened, and a pre-auth route
    that relayed a caller's percentage would be a way to paint any number on
    the phone.
    """
    if not _request_is_console(request):
        return JSONResponse({"error": "console only"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be an object"}, status_code=400)
    screen = body.get("screen")
    if screen not in ("on", "off"):
        return JSONResponse({"error": "screen must be on or off"}, status_code=400)
    battery = _read_battery()
    _push_lock_event("charger", {
        "percent": battery["percent"],
        "status": battery["status"],
        "screen": screen,
    })
    return Response(status_code=204)


@router.post("/lock-power-menu")
async def lock_power_menu(request: Request):
    """The power key was held. Raise the menu on the lock screen. Console-only.

    Posted by the compositor (taos-kiosk-power-hold) on loopback. sway owns the
    key -- a logind config this image deliberately ships without once made a
    short press shut the phone down outright -- so the long press has to reach
    the page from outside it.
    """
    if not _request_is_console(request):
        return JSONResponse({"error": "console only"}, status_code=403)
    delivered = _push_lock_event("power-menu")
    return JSONResponse({"ok": True, "delivered": delivered})


#: What the power menu is allowed to do. A closed set, named here rather than
#: derived from the request: this menu is reachable BEFORE sign-in, so the list
#: of things a stranger holding the phone can trigger has to be readable in one
#: place.
_POWER_ACTIONS = ("poweroff", "reboot", "stop-agents", "screenshot", "emergency")

#: Apps the lock screen may open, and the drop-box verb that opens each.
#:
#: A CLOSED MAP, not a name the page hands over: whatever ends up in the drop
#: box is run as root by taos-power-apply, so the page must never be able to
#: name the command. It chooses from this list or it gets a 400.
#:
#: Deliberately NOT part of _POWER_ACTIONS. That set is the power menu's five
#: verbs and @taOS-dev asked for it to stay closed and exactly that; opening an
#: app is not a power action and putting it there would blur what that list
#: means. The privileged channel underneath is shared, the vocabulary is not.
_LOCK_APPS = {"camera": "app-camera"}


@router.post("/lock-power-action")
async def lock_power_action(request: Request):
    """Carry out a power-menu choice. Console-only.

    Jay asked for confirmation on "stop all agents" and "emergency call"; that
    confirm step lives in the page, because it is a question about intent and
    the answer never needs to leave the device.
    """
    if not _request_is_console(request):
        return JSONResponse({"error": "console only"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    action = str(body.get("action", "")).strip()
    if action not in _POWER_ACTIONS:
        return JSONResponse({"error": "unknown action"}, status_code=400)

    if action in ("poweroff", "reboot"):
        try:
            # atomic_write_text: fires-on-existence is preserved, an fsync is
            # gained on the verb that powers the machine off, and the shared
            # fixed temp name is gone. See lock_app for the full reasoning.
            _write_power_request(action)
        except OSError as exc:
            return JSONResponse(
                {"error": "power request failed", "detail": str(exc)}, status_code=503
            )
        return JSONResponse({"ok": True, "action": action})

    if action == "stop-agents":
        orchestrator = getattr(request.app.state, "orchestrator", None)
        if orchestrator is None:
            return JSONResponse({"error": "orchestrator unavailable"}, status_code=503)
        try:
            # The same call /api/system/prepare-shutdown makes, deliberately:
            # "stop all agents" from the lock screen and the systemd stop hook
            # should drain agents the same way, or one of the two paths is
            # quietly doing something else.
            report = await orchestrator.prepare("all", "lock-screen-power-menu")
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
        return JSONResponse({"ok": True, "action": action, "report": report})

    if action == "screenshot":
        return JSONResponse(_take_screenshot())

    # Emergency call. There is no dialer on this handset and no telephony stack
    # behind it, so this says so rather than pretending: a menu entry that
    # silently does nothing in an emergency is worse than one that is honest.
    return JSONResponse(
        {"ok": False, "action": "emergency", "demo": True,
         "detail": "No dialer is configured on this device.",
         "message": "This phone can't place calls. Use another phone to "
                    "reach emergency services."}
    )


def _take_screenshot() -> dict:
    """Grab the screen with grim, into /var/lib/taos-kiosk/screenshots.

    ⚠ grim currently FAILS on this compositor with "no supported format found"
    -- measured on the device. That is why this reports the error text instead
    of a bare False: the next person needs to know the capture was attempted
    and what refused it, not just that no file appeared.
    """
    import subprocess

    target_dir = "/var/lib/taos-kiosk/screenshots"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = "%s/%s.png" % (target_dir, stamp)
    try:
        os.makedirs(target_dir, exist_ok=True)
        proc = subprocess.run(
            ["grim", path], capture_output=True, text=True, timeout=15
        )
    except Exception as exc:
        return {"ok": False, "action": "screenshot", "detail": str(exc)}
    if proc.returncode != 0 or not os.path.exists(path):
        return {
            "ok": False, "action": "screenshot",
            "detail": (proc.stderr or proc.stdout or "grim failed").strip()[:200],
        }
    return {"ok": True, "action": "screenshot", "path": path,
            "bytes": os.path.getsize(path)}


@router.get("/lock-panels")
async def lock_panels(request: Request):
    """Scripted contents of the phone, mailbox, apps, projects and decisions
    panels. Console-only, demo-only.

    Gated exactly like the notification stacks: TAOS_LOCK_DEMO_PANELS on top of
    the master TAOS_LOCK_DEMO_AGENTS flag, so a real device shows five empty
    panels rather than an invented inbox, and one flag takes the whole lot down.
    404 with either flag off; the page treats that as "nothing to show".

    Everything served here is READ-ONLY content. The panel that used to carry
    actions was replaced by projects; the actions themselves moved to the power
    menu, where "stop all agents" is now gated behind the passcode rather than
    being reachable by anyone holding the phone. Relocated AND gated, not
    removed -- poweroff and reboot stay pre-auth because the hardware key
    already does both from this screen.
    """
    if not _request_is_console(request):
        return JSONResponse({"error": "console only"}, status_code=403)
    if not _demo_panels_enabled():
        return JSONResponse({"error": "not found"}, status_code=404)
    payload = _demo_panels()
    payload["demo"] = True
    return JSONResponse(payload)


@router.post("/lock-app")
async def lock_app(request: Request):
    """Open one of the lock screen's apps. Console-only.

    THE PRE-AUTH QUESTION, answered rather than assumed: this runs before
    anyone signs in, so the only apps that may be listed here are ones that a
    stranger holding the phone may already reach. The camera qualifies on every
    phone ever made, and taOS's camera app shows a viewfinder and the photos
    taken from it -- it is not a door into a signed-in user's files, because at
    this point there is no signed-in user.

    The app is launched by the same root drop box the power menu uses. The page
    picks a NAME from a closed map; the verb that reaches root is never anything
    the page said.
    """
    if not _request_is_console(request):
        return JSONResponse({"error": "console only"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    app = str(body.get("app", "")).strip()
    verb = _LOCK_APPS.get(app)
    if verb is None:
        return JSONResponse({"error": "unknown app"}, status_code=400)
    try:
        # atomic_write_text, not a hand-rolled temp+replace. The watcher fires
        # on the path EXISTING, so a partial write could be read as a verb that
        # was never finished -- and the helper also fsyncs, which matters on a
        # verb that powers the machine off, and uses a RANDOM temp name with
        # O_EXCL. All three writers here shared the fixed name
        # `_POWER_REQUEST + ".part"`, so two near-simultaneous taps could let
        # the camera button turn Wi-Fi off. (@taOS-dev, reviewing #3108.)
        _write_power_request(verb)
    except OSError as exc:
        return JSONResponse(
            {"error": "launch failed", "detail": str(exc)}, status_code=503
        )
    return JSONResponse({"ok": True, "app": app})


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
