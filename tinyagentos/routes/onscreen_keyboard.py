"""A taOS-rendered on-screen keyboard for the server-rendered auth pages.

WHY THIS EXISTS. taOS targets wall-mounted and desk touchscreens with no
peripherals. Under ``chromium --kiosk`` on a bare X session there is no desktop
environment and no IME to summon, so tapping a text field summons nothing at
all: the sign-in and first-run pages become unusable and the device cannot be
set up or signed into from its own screen. Windows solves this with the
accessibility button on its login screen; taOS has to draw the keyboard itself.

DESIGN RULES, each of which is load-bearing:

* **Progressive enhancement only.** ``/auth/login`` and ``/auth/setup`` are
  deliberately server-rendered and work with JavaScript disabled. Everything
  here is added by script at runtime, so with JS off both pages are byte-for-byte
  what they were before and a physical keyboard still works.
* **Focus must never leave the field.** Every key cancels the pointer event
  before the browser can move focus, otherwise the first tap would blur the
  input and the second would type into nothing.
* **Keys are ``type="button"``.** A bare ``<button>`` inside a form defaults to
  ``submit``; a keyboard whose every key submits the login form would be worse
  than no keyboard.
* **No key preview.** Phone keyboards pop up a magnified glyph on press. On a
  wall-mounted panel that renders the password to anyone in the room.
"""
from __future__ import annotations

#: Minimum touch target. 44px is the smallest reliably-hittable target for a
#: finger; the keys below are larger, and this is the floor they must not cross.
_MIN_TOUCH_PX = 44

OSK_STYLE = """
.osk-toggle {
  position: fixed; right: 16px; bottom: 16px; z-index: 60;
  width: 56px; height: 56px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 24px; line-height: 1; cursor: pointer;
  border: 1px solid rgba(255,255,255,0.18);
  background: rgba(28,28,32,0.92); color: #f5f5f7;
  box-shadow: 0 6px 20px rgba(0,0,0,0.35);
}
.osk-toggle:focus-visible { outline: 3px solid #4c9aff; outline-offset: 2px; }
.osk-toggle[aria-pressed="true"] { background: #4c9aff; color: #0b0b0d; }

.osk {
  position: fixed; left: 0; right: 0; bottom: 0; z-index: 55;
  padding: 10px 10px calc(10px + env(safe-area-inset-bottom, 0px));
  background: rgba(20,20,24,0.98);
  border-top: 1px solid rgba(255,255,255,0.14);
  box-shadow: 0 -8px 28px rgba(0,0,0,0.4);
  user-select: none; -webkit-user-select: none;
}
.osk[hidden] { display: none; }
.osk-row { display: flex; gap: 6px; justify-content: center; margin-bottom: 6px; }
.osk-row:last-child { margin-bottom: 0; }

.osk-key {
  flex: 1 1 0; min-width: 44px; min-height: 52px;
  font-size: 18px; font-family: inherit; line-height: 1;
  display: flex; align-items: center; justify-content: center;
  border-radius: 8px; cursor: pointer;
  border: 1px solid rgba(255,255,255,0.14);
  background: #2c2c33; color: #f5f5f7;
  -webkit-tap-highlight-color: transparent;
}
.osk-key:active { background: #4c9aff; color: #0b0b0d; }
.osk-key:focus-visible { outline: 3px solid #4c9aff; outline-offset: 1px; }
.osk-key[data-wide="1"] { flex: 1.6 1 0; }
.osk-key[data-wide="2"] { flex: 3.2 1 0; }
.osk-key[aria-pressed="true"] { background: #3a3a44; border-color: #4c9aff; }
.osk-key[data-accent="1"] { background: #3a3a44; }

/* Numeric keypad: a 3-wide grid, centred, with larger targets than the
   full keyboard because a PIN is entered under time pressure at a wall. */
.osk[data-layout="numeric"] { padding-bottom: calc(14px + env(safe-area-inset-bottom, 0px)); }
.osk[data-layout="numeric"] .osk-row { max-width: 320px; margin-left: auto; margin-right: auto; }
.osk[data-layout="numeric"] .osk-key { min-height: 62px; font-size: 24px; }

/* Keep the whole card reachable above the keyboard.
   The 46vh is only a fallback for the first frame: the script replaces it with
   the panel's MEASURED height, because a guess is either wasted space or — on a
   600px pi-top panel, where the numeric pad is 291px — a card whose buttons sit
   underneath the keys.
   `flex-start` matters: the auth pages centre their card in a flex body, and a
   card taller than the space left over is centred THROUGH the keyboard, so its
   actions end up behind the keys with no way to scroll to them. */
body.osk-open { padding-bottom: 46vh; align-items: flex-start; overflow-y: auto; }

@media (prefers-reduced-motion: no-preference) {
  .osk-key { transition: background-color 90ms ease; }
}
"""


# NOTE: kept as a plain (non-f) string. The auth pages are f-strings, so this is
# interpolated as a value — braces here must never be doubled.
OSK_SCRIPT = r"""
(function () {
  "use strict";

  var LETTERS = [
    ["q","w","e","r","t","y","u","i","o","p"],
    ["a","s","d","f","g","h","j","k","l"],
    ["shift","z","x","c","v","b","n","m","back"],
    ["sym",",","space",".","enter"]
  ];
  var SYMBOLS = [
    ["1","2","3","4","5","6","7","8","9","0"],
    ["-","/",":",";","(",")","$","&","@","\""],
    ["more",".",",","?","!","'","back"],
    ["abc","space","enter"]
  ];
  var MORE = [
    ["[","]","{","}","#","%","^","*","+","="],
    ["_","\\","|","~","<",">","€","£","¥","•"],
    ["sym",".",",","?","!","'","back"],
    ["abc","space","enter"]
  ];
  var NUMERIC = [["1","2","3"],["4","5","6"],["7","8","9"],["back","0","enter"]];

  var SPECIAL = {
    shift: { label: "⇧", aria: "Shift", wide: "1", accent: "1" },
    back:  { label: "⌫", aria: "Backspace", wide: "1", accent: "1" },
    enter: { label: "⏎", aria: "Enter", wide: "1", accent: "1" },
    space: { label: " ", aria: "Space", wide: "2" },
    sym:   { label: "?123", aria: "Symbols", wide: "1", accent: "1" },
    more:  { label: "#+=", aria: "More symbols", wide: "1", accent: "1" },
    abc:   { label: "ABC", aria: "Letters", wide: "1", accent: "1" }
  };

  var target = null;      // the input the keys type into
  var layer = "letters";  // letters | symbols | more | numeric
  var shift = false;
  var caps = false;
  var lastShiftTap = 0;
  var enabled = false;

  var panel = document.createElement("div");
  panel.className = "osk";
  panel.setAttribute("role", "group");
  panel.setAttribute("aria-label", "On-screen keyboard");
  panel.hidden = true;

  var toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "osk-toggle";
  toggle.setAttribute("aria-pressed", "false");
  toggle.setAttribute("aria-label", "Show on-screen keyboard");
  toggle.title = "On-screen keyboard";
  toggle.textContent = "⌨";

  var live = document.createElement("div");
  live.setAttribute("aria-live", "polite");
  live.className = "visually-hidden";
  live.style.cssText =
    "position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap";

  function readPref() {
    try { return window.localStorage.getItem("taos.osk") === "1"; }
    catch (e) { return false; }
  }
  function writePref(on) {
    try { window.localStorage.setItem("taos.osk", on ? "1" : "0"); }
    catch (e) { /* private mode / blocked storage — the keyboard still works */ }
  }

  function isTypable(el) {
    if (!el || el.tagName !== "INPUT") return false;
    return ["text", "password", "email", "number", "tel", "search", "url"]
      .indexOf((el.type || "text").toLowerCase()) !== -1;
  }

  function layoutFor(el) {
    if (!el) return "letters";
    var mode = (el.getAttribute("inputmode") || "").toLowerCase();
    var type = (el.type || "").toLowerCase();
    if (mode === "numeric" || mode === "tel" || type === "tel" || type === "number") {
      return "numeric";
    }
    return "letters";
  }

  function rowsFor(name) {
    if (name === "numeric") return NUMERIC;
    if (name === "symbols") return SYMBOLS;
    if (name === "more") return MORE;
    return LETTERS;
  }

  function keyFace(k) {
    if (SPECIAL[k]) return SPECIAL[k].label;
    return (shift || caps) ? k.toUpperCase() : k;
  }

  function render() {
    panel.textContent = "";
    panel.setAttribute("data-layout", layer);
    var rows = rowsFor(layer);
    for (var r = 0; r < rows.length; r++) {
      var row = document.createElement("div");
      row.className = "osk-row";
      for (var c = 0; c < rows[r].length; c++) {
        var k = rows[r][c];
        var meta = SPECIAL[k];
        var b = document.createElement("button");
        b.type = "button";
        b.className = "osk-key";
        b.setAttribute("data-key", k);
        b.textContent = keyFace(k);
        b.setAttribute("aria-label", meta ? meta.aria : keyFace(k));
        if (meta && meta.wide) b.setAttribute("data-wide", meta.wide);
        if (meta && meta.accent) b.setAttribute("data-accent", "1");
        if (k === "shift") b.setAttribute("aria-pressed", (caps || shift) ? "true" : "false");
        row.appendChild(b);
      }
      panel.appendChild(row);
    }
    // Switching between the letters, symbol and numeric layers changes the
    // panel's height, so the space reserved for it has to change with it.
    if (!panel.hidden) fit();
  }

  function insert(text) {
    if (!target) return;
    var start = target.selectionStart, end = target.selectionEnd;
    // Writing .value directly bypasses the maxlength the browser enforces for
    // real typing, so honour it here or the keypad can overrun the field --
    // the setup PIN is maxlength=12 and the numeric pad could enter a 13th
    // digit. Clip to whatever room is left after the replaced selection.
    var max = parseInt(target.getAttribute("maxlength"), 10);
    if (max > 0) {
      var selLen = (start === null || start === undefined) ? 0 : (end - start);
      var room = max - (target.value.length - selLen);
      if (room <= 0) return;
      if (text.length > room) text = text.slice(0, room);
    }
    // Some input types (email, number) throw or report null for selection.
    if (start === null || start === undefined) {
      target.value += text;
    } else {
      var v = target.value;
      target.value = v.slice(0, start) + text + v.slice(end);
      var pos = start + text.length;
      try { target.setSelectionRange(pos, pos); } catch (e) {}
    }
    target.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function backspace() {
    if (!target) return;
    var start = target.selectionStart, end = target.selectionEnd;
    if (start === null || start === undefined) {
      target.value = target.value.slice(0, -1);
    } else if (start !== end) {
      target.value = target.value.slice(0, start) + target.value.slice(end);
      try { target.setSelectionRange(start, start); } catch (e) {}
    } else if (start > 0) {
      target.value = target.value.slice(0, start - 1) + target.value.slice(start);
      try { target.setSelectionRange(start - 1, start - 1); } catch (e) {}
    }
    target.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function submitFrom(el) {
    if (!el) return;
    // A PIN panel handles its own submit; otherwise fall back to the form.
    var custom = el.getAttribute("data-osk-submit");
    if (custom) {
      var handler = document.getElementById(custom);
      if (handler) { handler.click(); return; }
    }
    var form = el.form;
    if (!form) return;
    if (typeof form.requestSubmit === "function") form.requestSubmit();
    else form.submit();
  }

  function press(k) {
    if (k === "shift") {
      var now = Date.now();
      if (now - lastShiftTap < 400) { caps = !caps; shift = false; }
      else { shift = !shift; }
      lastShiftTap = now;
      render();
      live.textContent = caps ? "Caps lock on" : (shift ? "Shift on" : "Shift off");
      return;
    }
    if (k === "sym")   { layer = "symbols"; render(); return; }
    if (k === "more")  { layer = "more";    render(); return; }
    if (k === "abc")   { layer = "letters"; render(); return; }
    if (k === "back")  { backspace(); return; }
    if (k === "space") { insert(" "); return; }
    if (k === "enter") { submitFrom(target); return; }

    insert((shift || caps) ? k.toUpperCase() : k);
    if (shift && !caps) { shift = false; render(); }
  }

  // pointerdown, not click: cancel BEFORE the browser moves focus off the
  // input. Without this the first tap blurs the field and the second types
  // into nothing.
  panel.addEventListener("pointerdown", function (ev) {
    var btn = ev.target.closest ? ev.target.closest(".osk-key") : null;
    if (!btn) return;
    ev.preventDefault();
    press(btn.getAttribute("data-key"));
  });

  // Keyboard and assistive-tech users reach the keys by Tab/Enter, which never
  // fires pointerdown — so honour click too, but only when it did not come
  // from the pointer path above (detail === 0 for synthesised activation).
  panel.addEventListener("click", function (ev) {
    var btn = ev.target.closest ? ev.target.closest(".osk-key") : null;
    if (!btn || ev.detail !== 0) return;
    ev.preventDefault();
    press(btn.getAttribute("data-key"));
  });

  // Reserve exactly the space the keyboard occupies. Measured, not guessed:
  // the numeric pad and the full keyboard are different heights, and so is the
  // same layout on a 600px panel versus a desktop window.
  function fit() {
    if (panel.hidden) { document.body.style.paddingBottom = ""; return; }
    var h = panel.offsetHeight;
    document.body.style.paddingBottom = h ? h + "px" : "";
  }

  function show() {
    if (!enabled || !target) return;
    var wanted = layoutFor(target);
    if (wanted !== layer) { layer = wanted; }
    render();
    panel.hidden = false;
    document.body.classList.add("osk-open");
    fit();
    reveal();
  }

  // Bring the focused field clear of the keys. NOT scrollIntoView: that
  // measures against the viewport, and the panel is position:fixed OVER the
  // viewport — so "already visible" includes the strip the keyboard is sitting
  // on, and a field level with the top row is left tucked behind it. Measured
  // against the panel's own edge instead.
  function reveal() {
    if (!target || panel.hidden || !target.getBoundingClientRect) return;
    var box = target.getBoundingClientRect();
    var floor = panel.getBoundingClientRect().top - 12;
    if (box.bottom > floor) window.scrollBy(0, box.bottom - floor);
    else if (box.top < 12) window.scrollBy(0, box.top - 12);
  }

  function hide() {
    panel.hidden = true;
    document.body.classList.remove("osk-open");
    document.body.style.paddingBottom = "";
  }

  document.addEventListener("focusin", function (ev) {
    if (!isTypable(ev.target)) return;
    target = ev.target;
    if (enabled) show();
  });

  // THERE IS DELIBERATELY NO HIDE-ON-BLUR. Closing the keyboard when focus
  // moves to something untypable looks obviously right and silently breaks
  // every button on the page: tapping one focuses it on POINTERDOWN, which
  // blurs the field, which used to hide the panel, which un-reserved ~276px and
  // reflowed the card out from under the finger — so mouseup landed elsewhere
  // and Chromium never fired `click`. Measured on the pi-top viewport: a tap on
  // "Sign in with PIN" produced pointerdown and NO click, i.e. PIN sign-in was
  // dead to touch, which is the entire feature. The panel now tracks the user's
  // toggle only, the way the Windows on-screen keyboard does, so nothing moves
  // under a finger mid-tap. `target` is kept for the same reason: keys go on
  // typing into the last field the user was in, as they do on Windows.
  window.addEventListener("resize", function () { if (enabled) fit(); });

  function setEnabled(on, announce) {
    enabled = on;
    toggle.setAttribute("aria-pressed", on ? "true" : "false");
    toggle.setAttribute(
      "aria-label", on ? "Hide on-screen keyboard" : "Show on-screen keyboard"
    );
    writePref(on);
    if (on) {
      if (!target) {
        var first = document.querySelector(
          "input[autofocus], form input[type=password], form input[type=text]"
        );
        if (first) { target = first; first.focus(); }
      }
      show();
    } else {
      hide();
    }
    if (announce) live.textContent = on ? "On-screen keyboard shown" : "On-screen keyboard hidden";
  }

  toggle.addEventListener("pointerdown", function (ev) { ev.preventDefault(); });
  toggle.addEventListener("click", function () { setEnabled(!enabled, true); });

  document.addEventListener("DOMContentLoaded", function () {
    document.body.appendChild(live);
    document.body.appendChild(panel);
    document.body.appendChild(toggle);
    // Restore the user's choice. A device with no keyboard has no other way to
    // type, so once they turn it on it must still be on after a reboot.
    if (readPref()) setEnabled(true, false);
  });

  // Expose a tiny hook so the PIN panel can request the numeric pad directly.
  window.taosOSK = {
    focusField: function (el) {
      if (!isTypable(el)) return;
      target = el;
      el.focus();
      if (enabled) show();
    },
    enable: function () { setEnabled(true, false); }
  };
})();
"""


#: Path the keyboard script is served from. It MUST be a real same-origin URL,
#: not an inline <script>: taOS sends `script-src 'self'`, so an inline block is
#: silently refused by the browser and the keyboard never appears. That failure
#: is invisible to any test that only checks the HTML contains the script —
#: the markup is present and correct, and the CSP drops it at execution time.
#: Never "fix" this by adding 'unsafe-inline' to the policy.
OSK_SCRIPT_PATH = "/auth/osk.js"


def osk_assets() -> str:
    """Style + script tags to drop into a server-rendered auth page.

    Returned as one chunk so a page cannot accidentally ship the script without
    the styles and render an unstyled pile of buttons over the form.

    ``defer`` matters: deferred scripts run in document order after parsing and
    before ``DOMContentLoaded``, so the keyboard is always initialised before
    any page script that wants to drive it.
    """
    return (
        f"<style>{OSK_STYLE}</style>\n"
        f'<script src="{OSK_SCRIPT_PATH}" defer></script>'
    )
