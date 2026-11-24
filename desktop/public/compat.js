/* taOS webview compatibility shim (#2082).
 *
 * The desktop bundle calls runtime APIs that postdate the engines in old
 * system webviews — scanning the built output found:
 *
 *   Object.hasOwn        Chromium 93   (7 emitted files)
 *   structuredClone      Chromium 98   (2 emitted files)
 *   Array/String .at     Chromium 92   (the local workaround's set)
 *
 * Ubuntu Touch's Morph webview is QtWebEngine 5.15 = Chromium 87, so the
 * first call threw a TypeError during startup and the SPA rendered a
 * silent blank screen. Lowering the Vite build target would not help on
 * its own: the target transpiles syntax, not API calls.
 *
 * This file feature-detects and polyfills the missing APIs, and installs
 * a window.onerror fallback that paints the failure on screen (the
 * issue's option 3) — on a phone there is no console, so a blank screen
 * is undiagnosable. It mirrors the workaround taOSmobile already injects
 * by hand.
 *
 * Like boot.js, this lives in an external same-origin public/ file rather
 * than an inline <script> because the app ships a strict
 * Content-Security-Policy (`script-src 'self'`). Every shell loads it as a
 * plain blocking script in <head>, ahead of the module bundle, and the
 * service worker precaches it alongside boot.js.
 *
 * MAINTENANCE: this file is copied to the output verbatim — Vite does not
 * transpile public/ files. It must stay parseable by the engines it
 * rescues, so write it in pre-ES2020 syntax: no arrow functions, no
 * template literals, no optional chaining, no ?? / ??. The guard test
 * src/__tests__/compat-bundle.test.ts executes the emitted file in a vm
 * context stripped of the modern APIs and checks the polyfills behave.
 */
(function () {
  "use strict";

  /* ---- Fail loudly instead of a blank screen --------------------------
   * Installed first, before the polyfills, so even a failure inside this
   * file is reported. The painter only fires while #root is still empty:
   * once the app has rendered, a runtime error must not pave over the UI,
   * so post-boot errors keep the default behaviour. */
  var pendingBootError = null;

  function paintBootError(message) {
    try {
      var root = document.getElementById("root");
      if (root && root.childNodes.length > 0) return;
      if (document.getElementById("taos-boot-error")) return;
      var box = document.createElement("div");
      box.id = "taos-boot-error";
      box.setAttribute(
        "style",
        "position:fixed;inset:0;z-index:2147483647;display:flex;" +
          "flex-direction:column;align-items:center;justify-content:center;" +
          "gap:12px;padding:24px;text-align:center;background:#141415;" +
          "color:#f2f2f7;font:17px system-ui,sans-serif;",
      );
      var title = document.createElement("div");
      title.setAttribute("style", "font-size:20px;font-weight:600;");
      title.textContent = "taOS couldn't start";
      var detail = document.createElement("div");
      detail.setAttribute(
        "style",
        "max-width:34em;white-space:pre-wrap;word-break:break-word;color:#c7c7cc;font-size:14px;",
      );
      detail.textContent = String(message);
      var hint = document.createElement("div");
      hint.setAttribute("style", "max-width:34em;color:#8e8e93;font-size:14px;");
      hint.textContent =
        "This browser or webview may be too old for taOS. " +
        "Updating the browser or system webview may fix it.";
      box.appendChild(title);
      box.appendChild(detail);
      box.appendChild(hint);
      (document.body || document.documentElement).appendChild(box);
    } catch (e) {}
  }

  function showBootError(message) {
    if (document.body) {
      paintBootError(message);
    } else if (pendingBootError === null) {
      // <body> not parsed yet (this script runs in <head>): remember the
      // first failure and paint it as soon as the document has a body.
      pendingBootError = message;
      document.addEventListener("DOMContentLoaded", function () {
        paintBootError(pendingBootError);
      });
    }
  }

  var previousOnError = window.onerror;
  window.onerror = function (message, source, lineno, colno, error) {
    var text =
      error && error.message
        ? error.name + ": " + error.message
        : String(message) + (source ? " (" + source + ":" + lineno + ")" : "");
    showBootError(text);
    if (typeof previousOnError === "function") {
      return previousOnError.apply(this, arguments);
    }
    return false;
  };

  /* ---- Object.hasOwn (Chromium 93) ------------------------------------
   * Per spec, hasOwn coerces the key and throws on null/undefined;
   * hasOwnProperty.call does both the same way. */
  if (typeof Object.hasOwn !== "function") {
    Object.defineProperty(Object, "hasOwn", {
      value: function hasOwn(obj, prop) {
        return Object.prototype.hasOwnProperty.call(obj, prop);
      },
      writable: true,
      enumerable: false,
      configurable: true,
    });
  }

  /* ---- Array.prototype.at / String.prototype.at (Chromium 92) -------- */
  function toIntegerOrInfinity(value) {
    var n = Number(value);
    if (n !== n) return 0; // NaN
    if (n === 0 || n === Infinity || n === -Infinity) return n;
    return (n < 0 ? -1 : 1) * Math.floor(Math.abs(n));
  }

  function atImpl(index) {
    var obj = Object(this);
    var len = toIntegerOrInfinity(obj.length);
    len = Math.min(Math.max(len, 0), 9007199254740991);
    var relativeIndex = toIntegerOrInfinity(index);
    var k = relativeIndex >= 0 ? relativeIndex : len + relativeIndex;
    if (k < 0 || k >= len) return undefined;
    return obj[k];
  }

  if (typeof Array.prototype.at !== "function") {
    Object.defineProperty(Array.prototype, "at", {
      value: atImpl,
      writable: true,
      enumerable: false,
      configurable: true,
    });
  }
  if (typeof String.prototype.at !== "function") {
    Object.defineProperty(String.prototype, "at", {
      value: atImpl,
      writable: true,
      enumerable: false,
      configurable: true,
    });
  }

  /* ---- structuredClone (Chromium 98) -----------------------------------
   * A fallback for the value shapes app state actually carries: plain
   * objects and arrays (cycles included), Date, RegExp, Map, Set,
   * ArrayBuffer and typed-array views, and the primitive wrappers.
   * Anything the platform clone would reject (functions, symbols) throws,
   * as do host objects this fallback cannot reproduce (Blob, File, …) —
   * failing loudly beats silently returning a wrong-shaped copy. */
  function dataCloneError() {
    var err = new Error("The value could not be cloned by the compat structuredClone.");
    err.name = "DataCloneError";
    return err;
  }

  function cloneValue(value, seen) {
    if (value === null) return null;
    var type = typeof value;
    if (type !== "object") {
      if (type === "function" || type === "symbol") throw dataCloneError();
      return value; // undefined, boolean, number, string, bigint
    }
    if (seen.has(value)) return seen.get(value);

    var copy;
    var i;
    var keys;

    if (value instanceof Date) {
      copy = new Date(value.getTime());
      seen.set(value, copy);
      return copy;
    }
    if (value instanceof RegExp) {
      copy = new RegExp(value.source, value.flags);
      copy.lastIndex = value.lastIndex;
      seen.set(value, copy);
      return copy;
    }
    if (value instanceof ArrayBuffer) {
      copy = value.slice(0);
      seen.set(value, copy);
      return copy;
    }
    if (typeof ArrayBuffer !== "undefined" && ArrayBuffer.isView(value)) {
      if (value instanceof DataView) {
        var bytes = new Uint8Array(value.buffer, value.byteOffset, value.byteLength).slice();
        copy = new DataView(bytes.buffer);
      } else {
        // Typed arrays: constructing from a same-kind array copies elements.
        copy = new value.constructor(value);
      }
      seen.set(value, copy);
      return copy;
    }
    if (value instanceof Map) {
      copy = new Map();
      seen.set(value, copy);
      value.forEach(function (v, k) {
        copy.set(cloneValue(k, seen), cloneValue(v, seen));
      });
      return copy;
    }
    if (value instanceof Set) {
      copy = new Set();
      seen.set(value, copy);
      value.forEach(function (v) {
        copy.add(cloneValue(v, seen));
      });
      return copy;
    }
    if (value instanceof Error) {
      copy = new Error(value.message);
      copy.name = value.name;
      seen.set(value, copy);
      return copy;
    }
    if (
      value instanceof Number ||
      value instanceof String ||
      value instanceof Boolean
    ) {
      copy = Object(value.valueOf());
      seen.set(value, copy);
      return copy;
    }
    if (typeof Blob !== "undefined" && value instanceof Blob) {
      throw dataCloneError();
    }
    // Arrays and plain objects. Structured clone drops the prototype, so
    // a fresh {} / [] is the right shape for class instances too. Object
    // .keys covers indices plus extra own props and preserves holes.
    copy = Array.isArray(value) ? new Array(value.length) : {};
    seen.set(value, copy);
    keys = Object.keys(value);
    for (i = 0; i < keys.length; i++) {
      copy[keys[i]] = cloneValue(value[keys[i]], seen);
    }
    return copy;
  }

  if (typeof window.structuredClone !== "function") {
    window.structuredClone = function structuredClone(value) {
      return cloneValue(value, new WeakMap());
    };
  }
})();
