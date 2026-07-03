"""Static security analyzer for AI-authored App Studio app source.

App Studio builds web apps (HTML/CSS/JS/TS) from an LLM's output and runs
them in a sandboxed iframe (see SandboxedAppWindow.tsx: `sandbox
allow-scripts` only -- no allow-same-origin -- plus a locked-down CSP built
by userspace_apps.py). That sandbox is the real containment boundary. This
module is defense in depth in FRONT of it: a fast, server-side static scan
that catches the patterns an LLM is prone to writing (or that a malicious
prompt-injection could coax it into writing) before the source is ever
persisted or previewed -- secrets baked into client code, sandbox-escape
attempts, exfiltration, DOM XSS sinks, and so on.

analyze_app_source() is the single entry point. It must be called
server-side on every submitted source tree (install/publish and, ideally,
preview) so a modified or bypassed client can never skip the scan.

Detection approach
-------------------
Detection here is line-anchored regular expressions, not a full AST parse.
A real JS/TS parser (e.g. acorn) would resolve some ambiguity more
precisely -- a shadowed local variable named `eval`, multi-line template
literals, minified one-line bundles -- but it would also pull a Node.js
subprocess into the request path of a *security* module, adding an extra
runtime dependency/attack surface and a poor fit for taOS's "install must
just work" story on constrained hardware (Orange Pi etc). Every pattern
below is anchored to a realistic call/attribute syntax boundary (word
boundaries, required parens or quotes) to keep false positives low, and
each detector's docstring calls out its known blind spots. Treat this as a
high-signal triage layer, not a soundness guarantee.
"""

from __future__ import annotations

# NOTE: this file is the analyzer that DETECTS eval()/new Function()/etc in
# submitted app source -- every occurrence of "eval(" or similar below is
# inside a regex pattern, docstring, or finding message describing what to
# flag. Nothing in this module itself calls eval() or executes app-supplied
# strings as code.

import re
from dataclasses import dataclass
from typing import Literal

Severity = Literal["critical", "warning", "info"]


@dataclass(frozen=True)
class Finding:
    """A single static-analysis result.

    severity: "critical" (blocks publish), "warning" (surfaced, non-blocking),
    or "info" (fyi only).
    rule_id: stable machine-readable id for the detector that fired.
    file: the filename (key from the submitted `files` dict) the finding is in.
    line: 1-indexed line number within that file.
    message: human-readable explanation shown in the findings panel.
    """

    severity: Severity
    rule_id: str
    file: str
    line: int
    message: str

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "rule_id": self.rule_id,
            "file": self.file,
            "line": self.line,
            "message": self.message,
        }


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

_QUOTED = r"""['"`]"""


def _looks_dynamic(expr: str) -> bool:
    """Return True unless `expr` is a plain string literal with no interpolation.

    Used by the XSS-sink and inline-event-handler detectors to tell
    `el.innerHTML = "<b>static</b>"` (fine) from
    `el.innerHTML = "<b>" + name + "</b>"` or `` `<b>${name}</b>` `` or
    `el.innerHTML = userInput` (all dynamic -- flagged). A trailing
    semicolon is tolerated. Anything that isn't recognisably a single
    static literal is treated as dynamic -- this favours flagging over
    missing a real sink, which is the right bias for a security scanner.
    """
    e = expr.strip()
    if e.endswith(";"):
        e = e[:-1].strip()
    if re.fullmatch(r"'[^'\\]*'", e):
        return False
    if re.fullmatch(r'"[^"\\]*"', e):
        return False
    if re.fullmatch(r"`[^`$]*`", e):  # template literal with no ${...}
        return False
    return True


def _is_relative_or_allowed(url: str, allowed_hosts: frozenset[str]) -> bool:
    """Return True when `url` is same-origin-safe (relative) or explicitly allowlisted."""
    u = url.strip()
    if not u:
        return True
    # Relative paths, fragments, and anchors are same-origin -- safe.
    if u.startswith(("/", "./", "../", "#", "?")) or "://" not in u and not u.startswith("//"):
        return True
    m = re.match(r"^(?:[a-zA-Z][\w+.-]*:)?//([^/]+)", u)
    if not m:
        return True
    host = m.group(1).split("@")[-1].split(":")[0].lower()
    return host in allowed_hosts


def _iter_lines(content: str) -> list[str]:
    return content.splitlines()


# --------------------------------------------------------------------------- #
# Detectors
# --------------------------------------------------------------------------- #

_EVAL_LIKE_PATTERNS = [
    (re.compile(r"(?<!\.)\beval\s*\("), "Use of eval() executes arbitrary strings as code."),
    (re.compile(r"\bnew\s+Function\s*\("), "new Function(...) compiles a string into executable code, equivalent to eval()."),
    (re.compile(rf"\bset(?:Timeout|Interval)\s*\(\s*{_QUOTED}"),
     "setTimeout/setInterval called with a string first argument is an implied eval()."),
]


def detect_eval_like(filename: str, content: str) -> list[Finding]:
    """Flag eval(), new Function(...), and setTimeout/setInterval("string", ...).

    All three compile and execute a string as code at runtime, which is the
    classic way an LLM-authored (or prompt-injected) app smuggles in
    behaviour that a source review would otherwise catch. Blind spot: a
    local variable literally named `eval` used as a plain function call
    would false-positive; conversely `window["ev" + "al"]("...")` would be
    missed since we don't evaluate string concatenation.
    """
    findings: list[Finding] = []
    for i, line in enumerate(_iter_lines(content), start=1):
        for pattern, message in _EVAL_LIKE_PATTERNS:
            if pattern.search(line):
                findings.append(Finding("critical", "eval-like-execution", filename, i, message))
    return findings


_NET_CALL_PATTERNS = [
    re.compile(rf"\bfetch\s*\(\s*{_QUOTED}([^'\"`]+){_QUOTED}"),
    re.compile(rf"\.open\s*\(\s*{_QUOTED}[A-Za-z]+{_QUOTED}\s*,\s*{_QUOTED}([^'\"`]+){_QUOTED}"),
    re.compile(rf"\bnew\s+WebSocket\s*\(\s*{_QUOTED}([^'\"`]+){_QUOTED}"),
]

_ALLOWLISTED_HOSTS: frozenset[str] = frozenset()


def detect_network_exfil(filename: str, content: str,
                          allowed_hosts: frozenset[str] = _ALLOWLISTED_HOSTS) -> list[Finding]:
    """Flag fetch/XMLHttpRequest/WebSocket calls to a non-relative, non-allowlisted host.

    App Studio apps run sandboxed with no network access by default (the
    CSP's connect-src is 'self' unless the user grants a specific
    network:<origin> permission), so any absolute cross-origin call in the
    source is either dead code or an exfiltration attempt worth a human
    look before publish. Relative URLs (same-origin) are never flagged.
    Blind spot: a URL built purely from string concatenation or a runtime
    variable (`fetch(base + path)`) is not resolvable by regex and is
    missed.
    """
    findings: list[Finding] = []
    for i, line in enumerate(_iter_lines(content), start=1):
        for pattern in _NET_CALL_PATTERNS:
            for m in pattern.finditer(line):
                url = m.group(1)
                if not _is_relative_or_allowed(url, allowed_hosts):
                    findings.append(Finding(
                        "critical", "network-exfil", filename, i,
                        f"Network call to a non-relative, non-allowlisted host: {url!r}. "
                        "Sandboxed apps have no network access by default -- this looks "
                        "like an attempt to exfiltrate data or reach an external origin.",
                    ))
    return findings


_XSS_SINK_PATTERNS = [
    re.compile(r"\.(innerHTML|outerHTML)\s*=\s*(.+)"),
    re.compile(r"document\.write(?:ln)?\s*\(\s*(.+?)\)\s*;?\s*$"),
    re.compile(rf"\.insertAdjacentHTML\s*\(\s*{_QUOTED}[^'\"`]*{_QUOTED}\s*,\s*(.+?)\)\s*;?\s*$"),
]


def detect_dom_xss_sink(filename: str, content: str) -> list[Finding]:
    """Flag innerHTML/outerHTML/document.write/insertAdjacentHTML fed dynamic input.

    These DOM sinks parse their argument as HTML; when the argument is
    anything other than a fixed string literal (concatenation, a template
    literal with ${...}, or a bare variable) it is a classic DOM XSS vector.
    A pure static-string assignment (`el.innerHTML = "<b>Hi</b>"`) is not
    flagged. Blind spot: a static-looking literal that itself was built
    upstream from untrusted input (e.g. reassigned two lines earlier) is
    not traced -- this is a single-line heuristic, not dataflow analysis.
    """
    findings: list[Finding] = []
    for i, line in enumerate(_iter_lines(content), start=1):
        for pattern in _XSS_SINK_PATTERNS:
            m = pattern.search(line)
            if m and _looks_dynamic(m.group(m.lastindex or 1)):
                sink = "innerHTML/outerHTML" if ".innerHTML" in line or ".outerHTML" in line else (
                    "document.write" if "document.write" in line else "insertAdjacentHTML"
                )
                findings.append(Finding(
                    "critical", "dom-xss-sink", filename, i,
                    f"{sink} assigned/called with dynamic content -- a classic DOM XSS sink "
                    "if any part of the value is attacker-influenced.",
                ))
    return findings


_DANGEROUS_SCHEME_RE = re.compile(
    rf"{_QUOTED}\s*(javascript:|data:text/html|vbscript:)", re.IGNORECASE,
)


def detect_dangerous_url_scheme(filename: str, content: str) -> list[Finding]:
    """Flag javascript:, data:text/html, and vbscript: URL literals.

    These schemes execute script when navigated to or assigned as an href/
    location, and are a common way to smuggle script past markup-only
    filtering. Matches any quoted string starting with one of these
    schemes, regardless of surrounding context (href=, location=, a raw
    string constant). Blind spot: a scheme built via concatenation
    (`"java" + "script:"`) is not detected.
    """
    findings: list[Finding] = []
    for i, line in enumerate(_iter_lines(content), start=1):
        m = _DANGEROUS_SCHEME_RE.search(line)
        if m:
            findings.append(Finding(
                "critical", "dangerous-url-scheme", filename, i,
                f"Dangerous URL scheme {m.group(1)!r} found -- executes script when navigated "
                "to or assigned as a location/href.",
            ))
    return findings


_SET_ATTR_ON_RE = re.compile(rf"\.setAttribute\s*\(\s*{_QUOTED}(on[a-zA-Z]+){_QUOTED}")
_INLINE_HANDLER_INTERP_RE = re.compile(
    r"""on[a-zA-Z]+\s*=\s*['"][^'"]*\$\{""",
)


def detect_inline_event_handler_injection(filename: str, content: str) -> list[Finding]:
    """Flag programmatic/interpolated inline event handler (on*) attributes.

    Two patterns: (1) `el.setAttribute("onclick", ...)` -- wiring an inline
    handler attribute in code, which allows script injection if the
    attribute name or value ever derives from input; (2) a markup/template
    string containing `onXxx="...${...}"` -- an inline handler attribute
    built with string interpolation, the same shape as
    `` `<button onclick="go('${id}')">` `` where `id` is attacker-controlled.
    Blind spot: handlers built via multi-step concatenation instead of a
    single template-literal interpolation are not matched.
    """
    findings: list[Finding] = []
    for i, line in enumerate(_iter_lines(content), start=1):
        m = _SET_ATTR_ON_RE.search(line)
        if m:
            findings.append(Finding(
                "critical", "inline-event-handler-injection", filename, i,
                f"setAttribute used to wire an inline event handler ({m.group(1)}) -- "
                "unsafe if the handler name or body ever derives from user input.",
            ))
        if _INLINE_HANDLER_INTERP_RE.search(line):
            findings.append(Finding(
                "critical", "inline-event-handler-injection", filename, i,
                "Inline event handler attribute built with string interpolation -- "
                "classic HTML injection vector if the interpolated value is attacker-controlled.",
            ))
    return findings


_SECRET_PATTERNS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "an AWS access key ID"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"), "a private key block"),
    (re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}"), "a Slack token"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "a GitHub token"),
    (re.compile(
        rf"(?i)\b(api[_-]?key|secret|access[_-]?key|auth[_-]?token|token|password)\b\s*[:=]\s*"
        rf"{_QUOTED}([A-Za-z0-9_\-\.\/+=]{{16,}}){_QUOTED}",
    ), "a hardcoded credential-looking literal"),
]


def detect_hardcoded_secrets(filename: str, content: str) -> list[Finding]:
    """Flag literals that look like API keys, tokens, or private keys.

    Covers well-known token shapes (AWS, Slack, GitHub) plus a generic
    "keyword = long opaque string" pattern for api_key/secret/token/password
    style assignments. App Studio bundles ship as static client-side source
    served straight to the sandboxed iframe, so anything hardcoded here is
    world-readable -- there is no server boundary protecting it. Blind
    spot: this is pattern matching, not entropy analysis, so a long literal
    that happens to match the generic keyword pattern but is actually
    harmless (e.g. a UI copy string assigned to a variable named `token`)
    can false-positive; a real secret assigned to an unrelated-looking name
    can be missed.
    """
    findings: list[Finding] = []
    for i, line in enumerate(_iter_lines(content), start=1):
        for pattern, label in _SECRET_PATTERNS:
            if pattern.search(line):
                findings.append(Finding(
                    "critical", "hardcoded-secret", filename, i,
                    f"Line looks like it contains {label} -- secrets baked into client-side "
                    "app source are world-readable and must not ship.",
                ))
    return findings


_SANDBOX_ESCAPE_PATTERNS = [
    re.compile(r"\bwindow\.(parent|top|opener)\b"),
    re.compile(r"(?<!\.)\btop\.location\b"),
    re.compile(r"(?<!\.)\bparent\.postMessage\b"),
    re.compile(r"(?<!\.)\bopener\.\w+"),
]


def detect_sandbox_escape(filename: str, content: str) -> list[Finding]:
    """Flag access to window.parent/top/opener -- sandbox-escape attempts.

    The sandboxed iframe (`sandbox="allow-scripts"`, no allow-same-origin)
    already blocks same-origin access to these references at the browser
    level, but source that reaches for them is unambiguously trying to
    break out of the sandbox (reach the host desktop, the opener window, or
    the top-level frame) and is worth blocking on sight rather than relying
    solely on the browser boundary holding. Blind spot: `window.` prefix or
    the specific bare-identifier shapes above are required to avoid
    flagging an unrelated local variable named `parent`, `top`, or
    `opener` -- other access shapes are not matched.
    """
    findings: list[Finding] = []
    for i, line in enumerate(_iter_lines(content), start=1):
        for pattern in _SANDBOX_ESCAPE_PATTERNS:
            if pattern.search(line):
                findings.append(Finding(
                    "critical", "sandbox-escape-attempt", filename, i,
                    "Access to window.parent/top/opener -- an attempt to reach outside "
                    "the sandboxed iframe.",
                ))
    return findings


_ADD_MESSAGE_LISTENER_RE = re.compile(rf"addEventListener\s*\(\s*{_QUOTED}message{_QUOTED}")
_POSTMESSAGE_WILDCARD_RE = re.compile(rf"\.postMessage\s*\([^)]*,\s*{_QUOTED}\*{_QUOTED}")
# Anything that looks like the handler consulted the message's origin or
# sender identity: a dotted `.origin` access (event.origin,
# originalEvent.origin, ...), a destructured `{ origin }` (or `{ origin, ... }`)
# binding, or a `.source` check (validating the sender window reference, the
# same pattern this codebase's own SandboxedAppWindow.tsx broker uses instead
# of an origin string).
_ORIGIN_CHECK_RE = re.compile(r"\.origin\b|\{[^}]*\borigin\b[^}]*\}|\.source\b")
_ORIGIN_CHECK_WINDOW = 25


def detect_postmessage_no_origin_check(filename: str, content: str) -> list[Finding]:
    """Flag postMessage senders using "*" and message listeners with no origin check.

    (1) `x.postMessage(data, "*")` broadcasts to any origin regardless of who
    is actually listening. (2) A `window.addEventListener("message", ...)`
    handler that never references the origin/sender (`event.origin`,
    `originalEvent.origin`, a destructured `{ origin }`, or `event.source`)
    within a bounded window of following lines will accept and act on a
    message from any origin -- the same class of bug this codebase's own
    SandboxedAppWindow.tsx broker guards against by checking `e.source`.
    Blind spot: the origin check is a same-file line-proximity heuristic
    (looks `_ORIGIN_CHECK_WINDOW` lines ahead), so a check performed in a
    helper function defined elsewhere is not seen and would false-positive.
    """
    findings: list[Finding] = []
    lines = _iter_lines(content)
    for i, line in enumerate(lines, start=1):
        if _POSTMESSAGE_WILDCARD_RE.search(line):
            findings.append(Finding(
                "critical", "postmessage-no-origin-check", filename, i,
                "postMessage sent with target origin \"*\" -- broadcasts to any origin "
                "listening on the recipient window.",
            ))
        if _ADD_MESSAGE_LISTENER_RE.search(line):
            window_lines = lines[i - 1: i - 1 + _ORIGIN_CHECK_WINDOW]
            if not any(_ORIGIN_CHECK_RE.search(wl) for wl in window_lines):
                findings.append(Finding(
                    "critical", "postmessage-no-origin-check", filename, i,
                    "\"message\" event listener does not check event.origin -- it will "
                    "accept and act on messages from any origin.",
                ))
    return findings


_STORAGE_READ_RE = re.compile(
    r"\b(?:local|session)Storage\.getItem\s*\(|document\.cookie\b"
)
_NETWORK_CALL_RE = re.compile(r"\bfetch\s*\(|\.open\s*\(\s*['\"]|new\s+WebSocket\s*\(")
_STORAGE_EXFIL_WINDOW = 10


def detect_storage_exfil(filename: str, content: str) -> list[Finding]:
    """Flag localStorage/sessionStorage/cookie reads that sit close to a network call.

    Reading `localStorage.getItem(...)`, `sessionStorage.getItem(...)`, or
    `document.cookie` is completely normal on its own; it becomes an
    exfiltration pattern when a network call (fetch/XHR/WebSocket) shows up
    nearby in the same file, implying the read value is being shipped out.
    This is a same-file line-proximity heuristic
    (`_STORAGE_EXFIL_WINDOW` lines in either direction), not
    dataflow analysis -- it does not verify the read value is actually
    what's sent over the network, so an unrelated storage read and network
    call that both happen to live in the same small file can false-positive,
    while a read/send split across two files is missed entirely.
    """
    findings: list[Finding] = []
    lines = _iter_lines(content)
    storage_lines = [i for i, l in enumerate(lines) if _STORAGE_READ_RE.search(l)]
    network_lines = [i for i, l in enumerate(lines) if _NETWORK_CALL_RE.search(l)]
    if not storage_lines or not network_lines:
        return findings
    flagged: set[int] = set()
    for s in storage_lines:
        if any(abs(s - n) <= _STORAGE_EXFIL_WINDOW for n in network_lines):
            flagged.add(s)
    for s in sorted(flagged):
        findings.append(Finding(
            "critical", "storage-exfil", filename, s + 1,
            "localStorage/sessionStorage/cookie read sits near a network call in this "
            "file -- looks like stored data is being exfiltrated over the network.",
        ))
    return findings


_ALL_DETECTORS = [
    detect_eval_like,
    detect_network_exfil,
    detect_dom_xss_sink,
    detect_dangerous_url_scheme,
    detect_inline_event_handler_injection,
    detect_hardcoded_secrets,
    detect_sandbox_escape,
    detect_postmessage_no_origin_check,
    detect_storage_exfil,
]


def analyze_app_source(files: dict[str, str]) -> list[Finding]:
    """Run every detector over every file and return all findings.

    `files` maps filename (e.g. "index.html", "app.js") to its full text
    content. Findings are returned in a stable order: file (as given in the
    dict), then line number, then detector registration order -- so repeat
    calls against the same input are deterministic and diffable.
    """
    findings: list[Finding] = []
    for filename, content in files.items():
        for detector in _ALL_DETECTORS:
            findings.extend(detector(filename, content))
    file_order = {name: idx for idx, name in enumerate(files)}
    findings.sort(key=lambda f: (file_order[f.file], f.line))
    return findings


def has_critical(findings: list[Finding]) -> bool:
    """Return True if any finding is severity "critical"."""
    return any(f.severity == "critical" for f in findings)
