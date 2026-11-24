// SPDX-License-Identifier: AGPL-3.0-or-later
// taOS Controller: the phone face of the taos-input engine.
// This page only presents: it sends raw touches, keys and text, and the
// engine decides what they mean (taps, drags, scrolls, workspace swipes).
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var app = $("app");

  // ---- storage (may be unavailable: private windows, blocked site data) ----
  var store = {
    get: function (k) { try { return localStorage.getItem("taos.input." + k); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem("taos.input." + k, v); return true; } catch (e) { return false; } },
    del: function (k) { try { localStorage.removeItem("taos.input." + k); } catch (e) { /* nothing to do */ } }
  };
  // The device key (raw bytes) lives in this origin's IndexedDB. WebCrypto, and so non-extractable keys, does not exist on plain HTTP.
  var memCreds = null; // used only when IndexedDB is refused, so pairing still works for this visit
  var idb = {
    open: function () {
      return new Promise(function (res, rej) {
        try {
          var r = indexedDB.open("taos-input", 1);
          r.onupgradeneeded = function () { r.result.createObjectStore("kv"); };
          r.onsuccess = function () { res(r.result); };
          r.onerror = function () { rej(r.error); };
        } catch (e) { rej(e); }
      });
    },
    run: function (mode, fn) {
      return idb.open().then(function (db) {
        return new Promise(function (res, rej) {
          var tx = db.transaction("kv", mode), out = fn(tx.objectStore("kv"));
          tx.oncomplete = function () { res(out && out.result); };
          tx.onerror = function () { rej(tx.error); };
        });
      });
    },
    get: function (k) { return idb.run("readonly", function (s) { return s.get(k); }).catch(function () { return null; }); },
    put: function (k, v) { return idb.run("readwrite", function (s) { return s.put(v, k); }); },
    del: function (k) { return idb.run("readwrite", function (s) { return s.delete(k); }).catch(function () { return null; }); }
  };
  var creds = null;

  var settings = {
    sens: parseFloat(store.get("sens")) || 2.0,
    natural: store.get("natural") !== "0",
    haptics: store.get("haptics") !== "0"
  };

  function view(name) { app.setAttribute("data-view", name); }

  // ---- layout: follow the visible area when the phone keyboard opens ----
  function fit() {
    var vv = window.visualViewport;
    app.style.setProperty("--vh", (vv ? vv.height : window.innerHeight) + "px");
    if (vv) { window.scrollTo(0, 0); }
  }
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", fit);
    window.visualViewport.addEventListener("scroll", fit);
  }
  window.addEventListener("resize", fit);
  fit();

  // ---- haptics where the browser allows (Android; iOS Safari has none) ----
  function buzz(ms) {
    if (settings.haptics && navigator.vibrate) { try { navigator.vibrate(ms); } catch (e) { /* ignore */ } }
  }

  // ---- pairing ----
  var codeEl = $("code"), nameEl = $("devname"), pairMsg = $("pair-msg"), pairGo = $("pair-go");
  function guessName() {
    var ua = navigator.userAgent || "";
    if (/iPhone/.test(ua)) { return "iPhone"; }
    if (/iPad/.test(ua)) { return "iPad"; }
    if (/Android/.test(ua)) { return "Android phone"; }
    return "Phone";
  }
  nameEl.value = store.get("name") || guessName();

  function pairError(text) { pairMsg.textContent = text; pairMsg.hidden = !text; }

  function submitPair(ev) {
    if (ev) { ev.preventDefault(); }
    var code = codeEl.value.replace(/\D/g, "");
    if (code.length !== 6) { pairError("Enter the six digit code."); return; }
    pairError("");
    pairGo.disabled = true;
    var name = nameEl.value.trim();
    TaosCrypto.pair(function (path, body) {
      return fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
        .then(function (r) { return r.json().catch(function () { return { ok: false, error: "unexpected answer from the desktop" }; }); })
        .then(function (j) {
          if (!j || !j.ok) { throw new Error((j && j.error) ? j.error.charAt(0).toUpperCase() + j.error.slice(1) + "." : "Could not pair."); }
          return j;
        });
    }, code, name).then(function (c) {
      c.name = name;
      creds = c;
      store.set("name", name);
      return idb.put("creds", c).catch(function () { memCreds = c; });
    }).then(function () {
      pairGo.disabled = false;
      codeEl.value = "";
      start();
    }).catch(function (e) {
      pairGo.disabled = false;
      var m = e && e.message ? e.message : "Could not pair.";
      pairError(/fetch|network|Failed/i.test(m) ? "Cannot reach the desktop. Check that this phone is on the same Wi-Fi." : m);
      codeEl.select();
    });
  }
  $("pair-form").addEventListener("submit", submitPair);
  codeEl.addEventListener("input", function () {
    codeEl.value = codeEl.value.replace(/\D/g, "").slice(0, 6);
    if (codeEl.value.length === 6) { submitPair(); }
  });

  // ---- connection ----
  var conn = null, wsOpen = false, retry = 0, retryTimer = null, everOpened = false, failures = 0;
  var statusEl = $("status"), statusText = $("status-text"), banner = $("banner");
  var rtt = null, pingN = 0, pingTimer = null, pingSent = {};
  var compositorUp = true;

  function setStatus(state, text) {
    statusEl.setAttribute("data-state", state);
    statusText.textContent = text;
  }

  function showBanner(text, bad) {
    banner.hidden = !text;
    banner.textContent = text || "";
    banner.className = "banner" + (bad ? " bad" : "");
  }

  function send(obj) {
    if (conn && wsOpen) { return conn.send(obj); }
    return false;
  }

  function connect() {
    clearTimeout(retryTimer);
    if (!creds) { view("pair"); return; }
    setStatus("connecting", failures ? "Reconnecting" : "Connecting");
    var mine;
    var handlers = {
      onopen: function () {
        wsOpen = true; everOpened = true; failures = 0; retry = 0;
        setStatus("connected", "Connected");
        showBanner("");
        send({ t: "cfg", sens: settings.sens, natural: settings.natural });
        sendMods();
        pingTimer = setInterval(ping, 2000);
        ping();
        keepAwake();
      },
      onmessage: function (m) {
        if (m.t === "hello") {
          compositorUp = m.compositor !== false;
          if (!compositorUp) { desktopDown(); }
        } else if (m.t === "haptic") {
          buzz(m.k === "drag" ? 18 : 8);
          $("pad").setAttribute("data-drag", m.k === "drag" ? "1" : "0");
        } else if (m.t === "pong") {
          if (pingSent[m.n]) {
            rtt = Math.round(performance.now() - pingSent[m.n]); delete pingSent[m.n];
            if (compositorUp) { setStatus("connected", "Connected \u00b7 " + rtt + " ms"); }
            $("rtt-line").textContent = "Connection: " + rtt + " ms to the desktop.";
          }
        } else if (m.t === "state") {
          compositorUp = !!m.compositor;
          if (compositorUp) { setStatus("connected", "Connected"); showBanner(""); } else { desktopDown(); }
        }
      },
      onclose: function () {
        if (conn !== mine) { return; }
        var was = wsOpen;
        wsOpen = false; clearInterval(pingTimer); pingSent = {};
        releaseUi();
        if (!was) { failures++; }
        scheduleRetry();
      }
    };
    try {
      mine = TaosCrypto.connect((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws", creds, WebSocket, handlers);
    } catch (e) { scheduleRetry(); return; }
    conn = mine;
  }

  function desktopDown() {
    setStatus("desktop-down", "Desktop not responding");
    showBanner("The desktop is not responding. Input resumes when it is back.", true);
  }

  function scheduleRetry() {
    var wait = Math.min(4000, 400 * Math.pow(1.6, retry++));
    if (failures >= 3 && !everOpened) {
      setStatus("offline", "Cannot connect");
      showBanner("Cannot connect. If this phone was removed from the desktop, pair it again in Settings.", true);
    } else if (!wsOpen) {
      setStatus(failures >= 2 ? "offline" : "connecting", failures >= 2 ? "Offline" : "Reconnecting");
    }
    retryTimer = setTimeout(connect, wait);
  }

  function ping() {
    var n = ++pingN; pingSent[n] = performance.now();
    var keys = Object.keys(pingSent);
    if (keys.length > 8) { delete pingSent[keys[0]]; }
    send({ t: "ping", n: n });
  }

  var wake = null;
  function keepAwake() {
    if (!navigator.wakeLock || wake) { return; }
    navigator.wakeLock.request("screen").then(function (l) { wake = l; l.addEventListener("release", function () { wake = null; }); }).catch(function () { /* optional */ });
  }
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") {
      keepAwake();
      if (!wsOpen && creds) { clearTimeout(retryTimer); connect(); }
    }
  });

  // ---- trackpad ----
  var pad = $("pad"), ringsEl = $("touches");
  var slots = {};   // touch identifier -> small slot number
  var rings = {};   // slot -> element

  function slotFor(id) {
    if (slots[id] !== undefined) { return slots[id]; }
    var used = {}; Object.keys(slots).forEach(function (k) { used[slots[k]] = 1; });
    var s = 0; while (used[s]) { s++; }
    slots[id] = s; return s;
  }

  function ring(slot, x, y) {
    var r = rings[slot];
    if (!r) { r = document.createElement("div"); r.className = "ring"; ringsEl.appendChild(r); rings[slot] = r; }
    r.style.transform = "translate(" + x + "px," + y + "px)";
  }

  function touchEvent(phase, ev) {
    ev.preventDefault();
    var box = pad.getBoundingClientRect();
    for (var i = 0; i < ev.changedTouches.length; i++) {
      var t = ev.changedTouches[i];
      var slot = slotFor(t.identifier);
      send({ t: "touch", p: phase, i: slot, x: Math.round(t.clientX * 10) / 10, y: Math.round(t.clientY * 10) / 10, ts: Math.round(performance.now() * 10) / 10 });
      if (phase === "up" || phase === "cancel") {
        if (rings[slot]) { rings[slot].remove(); delete rings[slot]; }
        delete slots[t.identifier];
      } else {
        ring(slot, t.clientX - box.left, t.clientY - box.top);
      }
    }
    if (phase === "down") { pad.classList.add("used"); }
    if (!ev.touches.length) { pad.setAttribute("data-drag", "0"); }
  }
  pad.addEventListener("touchstart", function (e) { touchEvent("down", e); }, { passive: false });
  pad.addEventListener("touchmove", function (e) { touchEvent("move", e); }, { passive: false });
  pad.addEventListener("touchend", function (e) { touchEvent("up", e); }, { passive: false });
  pad.addEventListener("touchcancel", function (e) { touchEvent("cancel", e); }, { passive: false });
  pad.addEventListener("contextmenu", function (e) { e.preventDefault(); });

  // A mouse or pen on a desktop browser behaves as one finger (useful for testing).
  var mouseDown = false;
  pad.addEventListener("pointerdown", function (e) {
    if (e.pointerType === "touch") { return; }
    mouseDown = true; pad.setPointerCapture(e.pointerId); pad.classList.add("used");
    send({ t: "touch", p: "down", i: 0, x: e.clientX, y: e.clientY, ts: performance.now() });
  });
  pad.addEventListener("pointermove", function (e) {
    if (mouseDown && e.pointerType !== "touch") { send({ t: "touch", p: "move", i: 0, x: e.clientX, y: e.clientY, ts: performance.now() }); }
  });
  function mouseUp(e) {
    if (!mouseDown || e.pointerType === "touch") { return; }
    mouseDown = false;
    send({ t: "touch", p: "up", i: 0, x: e.clientX, y: e.clientY, ts: performance.now() });
  }
  pad.addEventListener("pointerup", mouseUp);
  pad.addEventListener("pointercancel", mouseUp);

  function releaseUi() {
    Object.keys(rings).forEach(function (k) { rings[k].remove(); });
    rings = {}; slots = {}; mouseDown = false;
  }

  // ---- modifiers: tap arms for one key, double tap holds, tap again clears ----
  var mods = { ctrl: "off", alt: "off", shift: "off", super: "off" };
  var modButtons = {};
  Array.prototype.forEach.call(document.querySelectorAll("[data-mod]"), function (b) { modButtons[b.getAttribute("data-mod")] = b; });
  var lastModTap = {}, superHold = null;

  function paintMods() {
    Object.keys(mods).forEach(function (m) {
      var b = modButtons[m];
      b.setAttribute("data-state", mods[m]);
      b.setAttribute("aria-pressed", mods[m] === "off" ? "false" : "true");
    });
  }
  function modList() { return Object.keys(mods).filter(function (m) { return mods[m] !== "off"; }); }
  function sendMods() { send({ t: "mods", m: modList() }); }
  function afterKey() {
    var changed = false;
    Object.keys(mods).forEach(function (m) { if (mods[m] === "armed") { mods[m] = "off"; changed = true; } });
    if (changed) { paintMods(); sendMods(); }
  }
  function tapMod(m) {
    var now = Date.now();
    if (mods[m] === "armed" && now - (lastModTap[m] || 0) < 350) { mods[m] = "locked"; }
    else if (mods[m] === "off") { mods[m] = "armed"; }
    else { mods[m] = "off"; }
    lastModTap[m] = now;
    paintMods(); sendMods(); buzz(6);
  }
  Object.keys(modButtons).forEach(function (m) {
    var b = modButtons[m], long = false;
    b.addEventListener("pointerdown", function () {
      long = false;
      if (m === "super") {
        // Holding Super sends the Super key by itself (launcher, overview).
        superHold = setTimeout(function () { long = true; send({ t: "key", k: "Super" }); buzz(12); }, 450);
      }
    });
    function end() { clearTimeout(superHold); }
    b.addEventListener("pointerup", end); b.addEventListener("pointerleave", end); b.addEventListener("pointercancel", end);
    b.addEventListener("click", function () { if (long) { long = false; return; } tapMod(m); });
  });

  // ---- special keys, with repeat for the keys that repeat ----
  Array.prototype.forEach.call(document.querySelectorAll("[data-key]"), function (b) {
    var key = b.getAttribute("data-key"), repeat = b.classList.contains("repeat"), t1 = null, t2 = null;
    function fire() { send({ t: "key", k: key }); afterKey(); }
    function stop() { clearTimeout(t1); clearInterval(t2); b.classList.remove("down"); }
    b.addEventListener("pointerdown", function (e) {
      e.preventDefault(); b.classList.add("down"); buzz(6); fire();
      if (repeat) { t1 = setTimeout(function () { t2 = setInterval(fire, 55); }, 380); }
    });
    ["pointerup", "pointerleave", "pointercancel"].forEach(function (n) { b.addEventListener(n, stop); });
  });

  // ---- the phone keyboard ----
  var kbd = $("kbd"), kbdBtn = $("kbd-btn"), SENT = "​", composing = false, shown = "";
  function resetField() { kbd.value = SENT; try { kbd.setSelectionRange(1, 1); } catch (e) { /* ignore */ } }
  function cps(s) { return Array.from(s); }
  function sendText(s) { if (s) { send({ t: "text", s: s }); afterKey(); } }
  function key(k) { send({ t: "key", k: k }); afterKey(); }

  // Replace what was sent for the current composition with its new text.
  function applyComposition(now) {
    var a = cps(shown), b = cps(now), p = 0;
    while (p < a.length && p < b.length && a[p] === b[p]) { p++; }
    for (var i = p; i < a.length; i++) { send({ t: "key", k: "Backspace" }); }
    sendText(b.slice(p).join(""));
    shown = now;
  }

  kbdBtn.addEventListener("click", function () {
    if (document.activeElement === kbd) { kbd.blur(); } else { resetField(); kbd.focus(); }
  });
  kbd.addEventListener("focus", function () { kbdBtn.setAttribute("aria-pressed", "true"); resetField(); });
  kbd.addEventListener("blur", function () { kbdBtn.setAttribute("aria-pressed", "false"); composing = false; shown = ""; });
  kbd.addEventListener("compositionstart", function () { composing = true; shown = ""; });
  kbd.addEventListener("compositionend", function (e) {
    if (e.data !== undefined && e.data !== null && e.data !== shown) { applyComposition(e.data); }
    composing = false; shown = ""; resetField();
  });
  kbd.addEventListener("beforeinput", function (e) {
    if (composing || e.isComposing) { return; }
    switch (e.inputType) {
      case "insertText": e.preventDefault(); sendText(e.data); break;
      case "insertLineBreak": case "insertParagraph": e.preventDefault(); key("Enter"); break;
      case "deleteContentBackward": e.preventDefault(); key("Backspace"); break;
      case "deleteContentForward": e.preventDefault(); key("Delete"); break;
      case "deleteWordBackward": e.preventDefault(); send({ t: "key", k: "Backspace", m: ["ctrl"] }); break;
      case "insertFromPaste": case "insertFromDrop":
        e.preventDefault();
        sendText(e.data || (e.dataTransfer && e.dataTransfer.getData("text/plain")) || "");
        break;
      default: break; // anything else is picked up by the input handler below
    }
  });
  kbd.addEventListener("input", function () {
    var cur = kbd.value.split(SENT).join("");
    if (composing) { applyComposition(cur); return; }
    if (cur) { sendText(cur); }
    resetField();
  });
  kbd.addEventListener("keydown", function (e) {
    if (composing || e.isComposing) { return; }
    var named = { Enter: 1, Backspace: 1, Delete: 1, Escape: 1, Tab: 1, ArrowLeft: 1, ArrowRight: 1, ArrowUp: 1, ArrowDown: 1, Home: 1, End: 1, PageUp: 1, PageDown: 1 };
    if (named[e.key]) { e.preventDefault(); send({ t: "key", k: e.key, m: [e.ctrlKey ? "ctrl" : "", e.altKey ? "alt" : "", e.metaKey ? "super" : ""].filter(Boolean) }); afterKey(); }
    else if ((e.ctrlKey || e.metaKey || e.altKey) && e.key.length === 1) {
      e.preventDefault();
      send({ t: "key", k: e.key, m: [e.ctrlKey ? "ctrl" : "", e.altKey ? "alt" : "", e.metaKey ? "super" : ""].filter(Boolean) });
    }
  });

  // ---- settings ----
  var sheet = $("sheet"), sensEl = $("sens"), sensOut = $("sens-out"), natEl = $("natural"), hapEl = $("haptics");
  function paintSettings() {
    sensEl.value = settings.sens; sensOut.textContent = settings.sens.toFixed(1) + "×";
    natEl.checked = settings.natural; hapEl.checked = settings.haptics;
    hapEl.disabled = !navigator.vibrate;
  }
  $("open-settings").addEventListener("click", function () { paintSettings(); sheet.hidden = false; $("close-settings").focus(); });
  $("close-settings").addEventListener("click", function () { sheet.hidden = true; });
  sheet.addEventListener("click", function (e) { if (e.target === sheet) { sheet.hidden = true; } });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !sheet.hidden) { sheet.hidden = true; } });
  sensEl.addEventListener("input", function () {
    settings.sens = parseFloat(sensEl.value); sensOut.textContent = settings.sens.toFixed(1) + "×";
    store.set("sens", String(settings.sens)); send({ t: "cfg", sens: settings.sens });
  });
  natEl.addEventListener("change", function () {
    settings.natural = natEl.checked; store.set("natural", settings.natural ? "1" : "0"); send({ t: "cfg", natural: settings.natural });
  });
  hapEl.addEventListener("change", function () { settings.haptics = hapEl.checked; store.set("haptics", settings.haptics ? "1" : "0"); buzz(10); });
  $("forget").addEventListener("click", function () {
    idb.del("creds"); creds = null; memCreds = null; everOpened = false; failures = 0;
    sheet.hidden = true;
    if (conn) { var c = conn; conn = null; c.close(); }
    wsOpen = false; clearTimeout(retryTimer); clearInterval(pingTimer);
    view("pair"); pairError("");
  });

  function start() {
    if (!creds) { view("pair"); return; }
    view("control");
    failures = 0; retry = 0;
    if (conn) { var old = conn; conn = null; old.close(); }
    connect();
  }
  paintMods();
  idb.get("creds").then(function (c) { creds = c || memCreds; start(); });
})();
