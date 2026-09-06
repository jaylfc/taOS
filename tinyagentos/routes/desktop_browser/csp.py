"""Content-Security-Policy headers for proxied responses.

Every page served through the proxy is sandboxed by a strict CSP so
that the proxied site's JavaScript cannot:

- Reach our own (taOS) APIs from inside the proxied origin
- Submit forms to third parties (bypassing our cookie jar)
- Load resources directly from third-party origins (leaking the user's
  real IP and bypassing our proxy)

`default-src 'self'` constrains the proxied page to only load
resources from the proxy origin (us). All other directives inherit
this default unless overridden. `form-action 'self'` ensures form
submissions also stay within the proxy.

The CSP is applied by PR 3's proxy fetch implementation when it
returns the rewritten HTML. PR 2 only provides this builder so the
test surface lands ahead of the consumer.
"""
from __future__ import annotations


_DIRECTIVES = (
    # default-src is the catch-all; everything else inherits unless
    # explicitly named below.
    "default-src 'self'",
    # img-src is widened to data: so inline image data URIs (which are
    # extremely common) work. Cross-origin https: was previously
    # allowed but removed — external images must be rewritten by the
    # rewriter to flow back through the proxy. Without this, the
    # proxied page could leak the user's real IP via direct image fetches.
    "img-src 'self' data:",
    # Stylesheets may use data: for inline font references.
    "style-src 'self' 'unsafe-inline' data:",
    # No inline JS, no eval. All JS must be served from us (the proxy).
    "script-src 'self'",
    # Explicit `connect-src` so XHR/fetch/EventSource/WebSocket from
    # proxied JS can only reach the proxy origin (us). Browser fallback
    # to default-src for connect-src has historically been inconsistent.
    "connect-src 'self'",
    # Explicit `worker-src` blocks proxied pages from registering
    # service workers / web workers pointing at third-party origins.
    "worker-src 'self'",
    # Explicit `frame-src` so proxied pages can only iframe other
    # proxied content (sub-frames also routed through us).
    "frame-src 'self'",
    # object-src is set explicitly because browser fallback to default-src
    # is inconsistent across versions. Block all plugin embeds (Flash,
    # Java, legacy <object> tags) regardless of source.
    "object-src 'none'",
    # base-uri 'self' prevents a malicious <base href="..."> tag in the
    # proxied page from redirecting relative URL resolution to an
    # attacker-controlled origin (which would bypass our rewriter).
    "base-uri 'self'",
    # Fonts may come from data: (inline). External fonts must be
    # rewritten by the proxy rewriter to flow back through the proxy.
    "font-src 'self' data:",
    # Form submissions may not target third parties.
    "form-action 'self'",
)


def proxied_response_csp(
    shell_origin: str | None = None, *, upgrade_insecure: bool = False
) -> str:
    """Return the strict CSP header value for proxied HTML responses.

    ``frame-ancestors`` is computed here rather than baked into the static
    directives: the proxied page is embedded by the taOS shell, which lives
    on a *different* origin (the main port) than the proxy origin. Plain
    ``frame-ancestors 'self'`` would block that framing entirely. We allow
    ``'self'`` plus the shell origin (same host, main port) when known, so
    only the taOS shell — not arbitrary third parties — can embed it,
    preserving the clickjacking defence on the user-facing /proxy URL.

    ``shell_origin`` should be ``scheme://host[:port]`` of the main taOS
    origin. When ``None`` (single-port fallback, where the proxy is served
    from the main origin itself), ``'self'`` alone is correct.

    ``upgrade_insecure`` adds ``upgrade-insecure-requests``. This MUST only be
    set when the proxy origin is served over HTTPS: rewritten subresources
    point at the proxy origin (``http://host:proxy_port/...`` on a plain-HTTP
    LAN deploy), and this directive would force-upgrade them to ``https://``
    that the HTTP-only origin can't serve — breaking every stylesheet, script,
    and image. Pass the request scheme through so HTTP deploys stay functional.
    """
    ancestors = "frame-ancestors 'self'"
    if shell_origin:
        ancestors += f" {shell_origin}"
    directives = [*_DIRECTIVES, ancestors]
    if upgrade_insecure:
        directives.append("upgrade-insecure-requests")
    return "; ".join(directives)
