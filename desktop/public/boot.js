/* taOS pre-paint boot script.
 *
 * Applies the saved reduce-effects preference to <html data-perf> BEFORE the
 * first paint, so a low-end device never flashes the full effects on load (#58).
 *
 * This lives in an external same-origin file rather than an inline <script>
 * because the app ships a strict Content-Security-Policy (`script-src 'self'`,
 * see tinyagentos/middleware/security_headers.py). Inline scripts are blocked
 * by that policy — silently disabling the pre-paint application — while a
 * same-origin external script is allowed without relaxing the policy to
 * `'unsafe-inline'`. The reference in index.html is a plain blocking <script>
 * in <head>, so it still runs before first paint.
 *
 * First-run GPU auto-detect happens in-app via a frame-rate probe.
 */
(function () {
  try {
    if (localStorage.getItem("taos-reduce-effects") === "on") {
      document.documentElement.setAttribute("data-perf", "reduced");
    }
  } catch (e) {}
})();