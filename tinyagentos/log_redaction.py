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
    "id_token", "refresh_token", "access_token", "jwt", "assertion",
)
_KEY_ALT = "|".join(sorted((re.escape(k) for k in _SENSITIVE_KEYS), key=len, reverse=True))

# key = value  /  key: value  /  "key": "value". The value runs until a quote,
# whitespace, or comma; brackets/URLs are kept in the value so a token is not
# truncated mid-string (leaving a readable tail). Capped to avoid runaway
# matches on pathological single-line input.
_KV_RE = re.compile(
    r'(?P<pre>["\']?(?:' + _KEY_ALT + r')["\']?\s*[:=]\s*)'
    r'(?P<quote>["\']?)(?P<val>[^\s,"\']{1,4096})(?P=quote)',
    re.IGNORECASE,
)

# --key value  (CLI flag form, space-separated).
_FLAG_RE = re.compile(r'(?P<pre>--(?:' + _KEY_ALT + r')\s+)(?P<val>\S+)', re.IGNORECASE)

# Auth SCHEME words that legitimately follow "authorization:"; the real secret
# is the NEXT token (handled by the bearer rule), so the KV rule must not treat
# the scheme word itself as the value and stop there, leaving the token exposed.
_AUTH_SCHEMES = {"bearer", "basic", "digest", "token", "negotiate"}

# Authorization: Bearer <token>  (header form). The value class includes the
# base64/base64url trailing set (+ / =) so AWS session tokens and OAuth2
# access tokens are masked whole, not truncated at the first + or /.
_BEARER_RE = re.compile(r'(?P<pre>bearer\s+)(?P<val>[A-Za-z0-9._\-+/=]{8,})', re.IGNORECASE)

# Bare secret shapes that carry no key= prefix. JWTs (three base64url segments)
# are common in logs with no Bearer word, so they get their own rule.
_TOKEN_SHAPE_RE = re.compile(
    r'(?:'
    r'\bsk-[A-Za-z0-9._\-]{16,}'          # OpenAI / sk-taos
    r'|\bsk_(?:live|test)_[0-9A-Za-z]{16,}'  # Stripe
    r'|\bgh[posru]_[A-Za-z0-9]{20,}'      # GitHub PAT
    r'|\bxox[baprs]-[A-Za-z0-9\-]{10,}'   # Slack
    r'|\bAKIA[0-9A-Z]{16}\b'              # AWS access key id
    r'|\bAIza[0-9A-Za-z_\-]{35}\b'        # Google API key
    r'|\bnpm_[A-Za-z0-9]{20,}'            # npm token
    r'|\bSG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}\b'  # SendGrid
    # JWT: base64url segments, but tolerate non-strict encoders that emit
    # standard base64 (+ / =) so a real token is masked whole, not truncated
    # at the first +, / or = padding char.
    r'|\beyJ[A-Za-z0-9_\-+/=]{8,}\.eyJ[A-Za-z0-9_\-+/=]{6,}\.[A-Za-z0-9_\-+/=]{6,}'  # JWT
    r')'
)

# PEM private-key blocks (SSH keys materialized on deploy, TLS keys). Keep the
# BEGIN/END markers so a key logged inside a stack trace still shows WHAT was
# redacted; only the base64 body is masked.
_PEM_RE = re.compile(
    r'(?P<begin>-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----).*?(?P<end>-----END [A-Z0-9 ]*PRIVATE KEY-----)',
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
    text = _PEM_RE.sub(lambda m: m.group("begin") + PLACEHOLDER + m.group("end"), text)
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
    # (e.g. a plain-looking API key logged without a key= prefix). Mask the
    # WHOLE surrounding non-whitespace token, not just the substring, so a
    # secret that is a prefix of a longer identifier (request_id=<secret>0000)
    # does not leak the trailing part or produce garbled "[REDACTED]0000"
    # output. Longest first so a value containing a shorter one is fully masked.
    if known_values:
        for val in sorted((v for v in known_values if v), key=len, reverse=True):
            if len(val) < _MIN_KNOWN_VALUE_LEN:
                continue
            text = re.sub(r'\S*' + re.escape(val) + r'\S*', PLACEHOLDER, text)

    return text


def redact_lines(lines: "list[str]", known_values: "list[str] | None" = None) -> "list[str]":
    """Redact a list of log lines (convenience for the paged log reader).

    Coerces non-string elements to str so one malformed line (e.g. a None from
    a buggy upstream parser) cannot take down the whole page response.
    """
    return [redact(line if isinstance(line, str) else str(line), known_values) for line in lines]
