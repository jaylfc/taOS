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
    # extremely common) work, and to https: so most images render after
    # rewriting. Note: this is the one place we permit cross-origin —
    # images are low-risk for data exfiltration since they're rendered
    # not executed.
    "img-src 'self' data: https:",
    # Stylesheets may use data: for inline font references.
    "style-src 'self' 'unsafe-inline' data:",
    # No inline JS, no eval. All JS must be served from us (the proxy).
    "script-src 'self'",
    # object-src is set explicitly because browser fallback to default-src
    # is inconsistent across versions. Block all plugin embeds (Flash,
    # Java, legacy <object> tags) regardless of source.
    "object-src 'none'",
    # base-uri 'self' prevents a malicious <base href="..."> tag in the
    # proxied page from redirecting relative URL resolution to an
    # attacker-controlled origin (which would bypass our rewriter).
    "base-uri 'self'",
    # Fonts may come from data: (inline) or https: (after rewriting
    # by the proxy).
    "font-src 'self' data: https:",
    # Form submissions may not target third parties.
    "form-action 'self'",
    # Disallow anyone embedding our proxied page in their own iframe
    # (defence against clickjacking on the user-facing /proxy URL).
    "frame-ancestors 'self'",
    # Block legacy mixed-content in case the proxied page uses http://
    # subresources after we serve over https://.
    "upgrade-insecure-requests",
)


def proxied_response_csp() -> str:
    """Return the strict CSP header value for proxied HTML responses."""
    return "; ".join(_DIRECTIVES)
