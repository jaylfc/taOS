"""Redaction for operator-facing log output (Logs app, bug-report bundle).

Every log line that leaves the box through the system-logs API passes through
`redact()` first. The threat is a well-meaning operator copying a log bundle
into a public GitHub issue and leaking a live credential that happened to be
logged by a dependency, a stack trace, or an env dump.

Design choices:
- Pure functions, no I/O, exhaustively tested. Nothing in the logs path may
  bypass this.
- Redact by PATTERN (key=value, bearer tokens, connection strings, private-key
  blocks, high-entropy provider-key shapes) AND by KNOWN SECRET VALUE (the
  literal values from the secrets store, so a secret logged verbatim is caught
  even if it does not match a generic shape).
- Fail closed on the value side: an empty or too-short known value is ignored
  rather than redacting everything.
- Never widen a match to swallow surrounding context; replace only the secret
  span with a fixed placeholder so the log stays readable.
"""
from __future__ import annotations

import re

PLACEHOLDER = "[REDACTED]"

# Keys whose value must be masked when they appear as key=value / key: value /
# "key": "value" / --key value. Case-insensitive, matched as whole words so
# "monkey" does not trip "key".
_SENSITIVE_KEYS = (
    "password", "passwd", "secret", "token", "api_key", "apikey", "api-key",
    "access_key", "access-key", "secret_key", "secret-key", "private_key",
    "private-key", "client_secret", "client-secret", "authorization", "auth",
    "bearer", "session", "cookie", "credential", "credentials", "passphrase",
)
_KEY_ALT = "|".join(sorted((re.escape(k) for k in _SENSITIVE_KEYS), key=len, reverse=True))

# key = value  /  key: value  /  "key": "value"  (value ends at quote,
# whitespace, comma, or line end).
_KV_RE = re.compile(
    r'(?P<pre>["\']?(?:' + _KEY_ALT + r')["\']?\s*[:=]\s*)'
    r'(?P<quote>["\']?)(?P<val>[^\s,"\'}{]+)(?P=quote)',
    re.IGNORECASE,
)

# --key value  (CLI flag form, space-separated).
_FLAG_RE = re.compile(r'(?P<pre>--(?:' + _KEY_ALT + r')\s+)(?P<val>\S+)', re.IGNORECASE)

# Auth SCHEME words that legitimately follow "authorization:"; the real secret
# is the NEXT token (handled by the bearer rule), so the KV rule must not treat
# the scheme word itself as the value and stop there, leaving the token exposed.
_AUTH_SCHEMES = {"bearer", "basic", "digest", "token", "negotiate"}

# Authorization: Bearer <token>  (header form; the key-value rule catches the
# "authorization=" form, this catches the header " Bearer <tok>" shape).
_BEARER_RE = re.compile(r'(?P<pre>bearer\s+)(?P<val>[A-Za-z0-9._\-]{8,})', re.IGNORECASE)

# Provider-key shapes that are secrets on their own with no key= prefix:
# sk-..., sk-taos-..., ghp_/gho_/ghs_ (GitHub), xoxb-/xoxp- (Slack), AKIA... (AWS).
_TOKEN_SHAPE_RE = re.compile(
    r'\b(?:'
    r'sk-[A-Za-z0-9._\-]{16,}'
    r'|gh[posru]_[A-Za-z0-9]{20,}'
    r'|xox[baprs]-[A-Za-z0-9\-]{10,}'
    r'|AKIA[0-9A-Z]{16}'
    r')\b'
)

# PEM private-key blocks (SSH keys materialized on deploy, TLS keys).
_PEM_RE = re.compile(
    r'-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----',
    re.DOTALL,
)

# postgres://user:pass@host, mysql://..., redis://..., amqp:// -- mask the
# password component only.
_CONN_STR_RE = re.compile(r'(?P<pre>[a-zA-Z][a-zA-Z0-9+.\-]*://[^:/\s]+:)(?P<val>[^@\s]+)(?P<post>@)')

# A known secret value shorter than this is not masked: too likely to be a
# common substring and cause runaway redaction of unrelated text.
_MIN_KNOWN_VALUE_LEN = 6


def redact(text: str, known_values: "list[str] | None" = None) -> str:
    """Return `text` with credential-shaped spans replaced by PLACEHOLDER.

    known_values: exact secret strings (e.g. from the secrets store) to mask
    wherever they appear verbatim, in addition to the pattern rules.
    """
    if not text:
        return text

    # Structural rules first (they anchor on keys/prefixes, least likely to
    # over-match), then the bare token shapes.
    text = _PEM_RE.sub(PLACEHOLDER, text)
    text = _CONN_STR_RE.sub(lambda m: m.group("pre") + PLACEHOLDER + m.group("post"), text)
    # Bearer BEFORE the key-value rule so "authorization: Bearer <tok>" has its
    # token masked; the KV rule then leaves the bare scheme word alone.
    text = _BEARER_RE.sub(lambda m: m.group("pre") + PLACEHOLDER, text)
    text = _FLAG_RE.sub(lambda m: m.group("pre") + PLACEHOLDER, text)

    def _kv_repl(m: "re.Match[str]") -> str:
        if m.group("val").lower() in _AUTH_SCHEMES:
            return m.group(0)  # e.g. "authorization: Bearer" -> leave for bearer rule
        return m.group("pre") + PLACEHOLDER

    text = _KV_RE.sub(_kv_repl, text)
    text = _TOKEN_SHAPE_RE.sub(PLACEHOLDER, text)

    # Known literal secret values last: mask any that survived the shape rules
    # (e.g. a plain-looking API key logged without a key= prefix). Longest
    # first so a value that contains a shorter one is fully masked.
    if known_values:
        for val in sorted((v for v in known_values if v), key=len, reverse=True):
            if len(val) < _MIN_KNOWN_VALUE_LEN:
                continue
            text = text.replace(val, PLACEHOLDER)

    return text


def redact_lines(lines: "list[str]", known_values: "list[str] | None" = None) -> "list[str]":
    """Redact a list of log lines (convenience for the paged log reader)."""
    return [redact(line, known_values) for line in lines]
