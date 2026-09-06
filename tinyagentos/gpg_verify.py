"""GPG signature verification for git-based auto-update.

Provides defense-in-depth verification of commit and tag signatures before
applying git-fetched updates. Uses ``git verify-commit`` and ``git verify-tag``
to check GPG signatures against a user-configured key fingerprint.

Preferences live under the ``gpg`` namespace (``/api/preferences/gpg``):

- ``enabled`` (bool, default False): master switch — when False, no verification
  is attempted even if a fingerprint is configured.
- ``required`` (bool, default False): when True, a failed verification blocks the
  update entirely; when False, the update proceeds with a warning.
- ``key_fingerprint`` (str | None): 40-char GPG key fingerprint; when set, only
  signatures from this key are accepted.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── input validation ──────────────────────────────────────────────────────

# A valid GPG key fingerprint is 40 hex characters, optionally with spaces.
# We strip spaces before validating because gpg accepts both forms.
import re as _re
_FINGERPRINT_RE = _re.compile(r'^[0-9A-Fa-f]{40}$')


def _validate_fingerprint(fp: str) -> str | None:
    """Return the normalised (uppercase, no spaces) fingerprint if valid, else None."""
    if not fp or not isinstance(fp, str):
        return None
    cleaned = fp.replace(" ", "").upper()
    if _FINGERPRINT_RE.match(cleaned):
        return cleaned
    return None


def _validate_keyserver(url: str) -> bool:
    """True when *url* looks like an hkps:// keyserver URL.

    Only TLS (hkps://) is accepted — plaintext hkp:// would allow MITM of
    imported keys.
    """
    if not url or not isinstance(url, str):
        return False
    if not url.startswith("hkps://"):
        return False
    # Reject anything that looks like shell injection / flag injection.
    if any(c in url for c in ("\n", "\r", "\t", ";", "|", "&", "$", "`", "'", '"')):
        return False
    return True

# ── preferences ────────────────────────────────────────────────────────────

GPG_PREF_NAMESPACE = "gpg"

DEFAULT_GPG_PREFS: dict = {
    "enabled": False,
    "required": False,
    "key_fingerprint": None,
}


@dataclass
class GpgPrefs:
    """Parsed GPG preferences with defaults."""
    enabled: bool = False
    required: bool = False
    key_fingerprint: Optional[str] = None

    @classmethod
    def from_dict(cls, raw: dict | None) -> GpgPrefs:
        if not raw:
            return cls()
        return cls(
            enabled=bool(raw.get("enabled", False)),
            required=bool(raw.get("required", False)),
            key_fingerprint=raw.get("key_fingerprint") or None,
        )


# ── result types ────────────────────────────────────────────────────────────

@dataclass
class GpgVerificationResult:
    """Outcome of a signature verification attempt.

    ``ok`` is True when the signature is valid and (if a fingerprint was
    configured) matches the expected key.  ``status`` is a human-readable
    one-line summary for logging / notification.
    """
    ok: bool = False
    status: str = ""
    fingerprint: Optional[str] = None
    key_id: Optional[str] = None
    raw_output: str = ""


# ── subprocess helpers ─────────────────────────────────────────────────────

async def _run(args: list[str], cwd: Path) -> tuple[int, str]:
    """Run a subprocess safely (no shell) and return (returncode, output)."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(cwd),
    )
    stdout, _ = await proc.communicate()
    return proc.returncode or 0, (stdout.decode() if stdout else "")


def _parse_fingerprint(output: str) -> Optional[str]:
    """Extract the primary-key fingerprint from ``git verify-*`` output.

    Parses the machine-readable ``[GNUPG:] VALIDSIG`` status line (emitted
    when ``git verify-commit --raw`` passes ``--status-fd=1`` to gpg).  This
    status output is locale-independent, unlike the human-readable ``Primary
    key fingerprint:`` line that can be translated.

    The VALIDSIG line has the form::

        [GNUPG:] VALIDSIG <fpr> <date> <ts> <expire> <sigver> <reserved> <pkalgo> <hashalgo> <sigclass> [<primary-key-fpr>]

    When the signing key is a **subkey**, an extra 40-char hex field
    (the primary-key fingerprint) is appended at the end.  When the
    primary key signs directly there are exactly 11 fields and
    ``<fpr>`` *is* the primary key.

    Returns the primary-key fingerprint (no spaces), or None.
    """
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("[GNUPG:] VALIDSIG"):
            parts = stripped.split()
            # parts[0] = "[GNUPG:]", parts[1] = "VALIDSIG", parts[2] = signing-fpr
            if len(parts) >= 3:
                # When the last field is a 40-char hex fingerprint, a subkey
                # signed and the last field is the primary-key fingerprint.
                # GPG can also append fprlen + pubkey-algo-name after the
                # primary fpr, so 12+ fields alone is not a reliable subkey
                # signal — only the fingerprint shape of parts[-1] matters.
                if len(parts[-1]) == 40 and _FINGERPRINT_RE.match(parts[-1]):
                    return parts[-1]
                # Otherwise the signing key IS the primary key (whether or
                # not extra fields like fprlen / algo name are present).
                if len(parts[2]) == 40 and _FINGERPRINT_RE.match(parts[2]):
                    return parts[2]
    return None


def _parse_key_id(output: str) -> Optional[str]:
    """Extract the short key id from ``git verify-*`` output.

    Returns the 16-char key id (e.g. ``1234567890ABCDEF``) or None.

    Parses both human-readable ``gpg:`` lines (available without ``--raw``)
    and machine-readable ``[GNUPG:] VALIDSIG`` status lines (available with
    ``--raw``).  When ``--raw`` is used, the human-readable lines are
    suppressed; in that case the long key id is extracted from the signing-key
    fingerprint in the VALIDSIG line (last 16 hex chars of the fingerprint).
    """
    for line in output.splitlines():
        stripped = line.strip()
        if "using RSA key" in stripped or "using EDDSA key" in stripped or "using ECDSA key" in stripped:
            # "gpg: Signature made … using RSA key 1234567890ABCDEF…"
            parts = stripped.split()
            for i, p in enumerate(parts):
                if p in ("key", "key-ID") and i + 1 < len(parts):
                    return parts[i + 1].rstrip(",…")
            # Fallback: find the longest hex chunk after "key"
            after_key = stripped.split("key", 1)
            if len(after_key) > 1:
                candidates = [w.rstrip(",…") for w in after_key[1].split() if len(w.rstrip(",…")) >= 8]
                if candidates:
                    return candidates[0]

    # Fallback for --raw mode: extract the long key id from the VALIDSIG line.
    # The VALIDSIG line has the form:
    #   [GNUPG:] VALIDSIG <signing-fpr> <date> <ts> ... [<primary-fpr>]
    # When a subkey signs, the last field carries the primary-key fingerprint.
    # Derive the key ID from it for consistency with _parse_fingerprint.
    # Otherwise the signing key IS the primary key; derive from parts[2].
    # The standard GPG long key id is the last 16 hex chars of the fingerprint.
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("[GNUPG:] VALIDSIG"):
            parts = stripped.split()
            if len(parts) >= 3:
                # When the last field is a valid fingerprint, a subkey signed.
                # Derive key ID from the primary-key fingerprint for consistency.
                if len(parts[-1]) == 40 and _FINGERPRINT_RE.match(parts[-1]):
                    return parts[-1][-16:].upper()
                # Otherwise the signing key IS the primary key.
                signing_fpr = parts[2]
                if len(signing_fpr) == 40 and _FINGERPRINT_RE.match(signing_fpr):
                    return signing_fpr[-16:].upper()

    return None


# ── public API ──────────────────────────────────────────────────────────────

async def verify_commit(
    project_dir: Path,
    commit_sha: str,
    expected_fingerprint: Optional[str] = None,
) -> GpgVerificationResult:
    """Verify the GPG signature on a git commit.

    Uses ``git verify-commit <sha>``.  Returns a ``GpgVerificationResult``
    with ``ok=True`` when the commit has a valid signature.  When
    *expected_fingerprint* is provided, the signature must also match that
    key (spaces in the fingerprint are stripped before comparison).

    If git or gpg is not available, the result carries ``ok=False`` with a
    descriptive status message.
    """
    if not commit_sha or not commit_sha.strip():
        return GpgVerificationResult(ok=False, status="no commit SHA provided")

    rc, out = await _run(["git", "verify-commit", "--raw", commit_sha.strip()], project_dir)

    fingerprint = _parse_fingerprint(out)
    key_id = _parse_key_id(out)

    if rc != 0:
        msg = out.strip().split("\n")[-1] if out.strip() else "signature verification failed"
        return GpgVerificationResult(
            ok=False,
            status=f"commit {commit_sha[:7]}: {msg}",
            fingerprint=fingerprint,
            key_id=key_id,
            raw_output=out,
        )

    # Signature is valid — check fingerprint if configured
    if expected_fingerprint:
        expected = _validate_fingerprint(expected_fingerprint)
        if not expected:
            return GpgVerificationResult(
                ok=False,
                status=f"commit {commit_sha[:7]}: invalid expected fingerprint",
            )
        actual = (fingerprint or "").upper()
        if not actual:
            return GpgVerificationResult(
                ok=False,
                status=(
                    f"commit {commit_sha[:7]}: valid signature but could not "
                    f"determine signing key fingerprint"
                ),
                key_id=key_id,
                raw_output=out,
            )
        if actual != expected:
            return GpgVerificationResult(
                ok=False,
                status=(
                    f"commit {commit_sha[:7]}: valid signature but key "
                    f"fingerprint mismatch (expected {expected[:16]}…, "
                    f"got {actual[:16]}…)"
                ),
                fingerprint=actual,
                key_id=key_id,
                raw_output=out,
            )

    return GpgVerificationResult(
        ok=True,
        status=f"commit {commit_sha[:7]}: valid signature{f' (key {key_id})' if key_id else ''}",
        fingerprint=fingerprint,
        key_id=key_id,
        raw_output=out,
    )


async def verify_tag(
    project_dir: Path,
    tag_name: str,
    expected_fingerprint: Optional[str] = None,
) -> GpgVerificationResult:
    """Verify the GPG signature on a git tag.

    Uses ``git verify-tag <tag>``.  Works identically to ``verify_commit``
    but operates on annotated/signed tags.
    """
    if not tag_name or not tag_name.strip():
        return GpgVerificationResult(ok=False, status="no tag name provided")

    rc, out = await _run(["git", "verify-tag", "--raw", tag_name.strip()], project_dir)

    fingerprint = _parse_fingerprint(out)
    key_id = _parse_key_id(out)

    if rc != 0:
        msg = out.strip().split("\n")[-1] if out.strip() else "tag signature verification failed"
        return GpgVerificationResult(
            ok=False,
            status=f"tag {tag_name}: {msg}",
            fingerprint=fingerprint,
            key_id=key_id,
            raw_output=out,
        )

    if expected_fingerprint:
        expected = _validate_fingerprint(expected_fingerprint)
        if not expected:
            return GpgVerificationResult(
                ok=False,
                status=f"tag {tag_name}: invalid expected fingerprint",
            )
        actual = (fingerprint or "").upper()
        if not actual:
            return GpgVerificationResult(
                ok=False,
                status=(
                    f"tag {tag_name}: valid signature but could not "
                    f"determine signing key fingerprint"
                ),
                key_id=key_id,
                raw_output=out,
            )
        if actual != expected:
            return GpgVerificationResult(
                ok=False,
                status=(
                    f"tag {tag_name}: valid signature but key "
                    f"fingerprint mismatch (expected {expected[:16]}…, "
                    f"got {actual[:16]}…)"
                ),
                fingerprint=actual,
                key_id=key_id,
                raw_output=out,
            )

    return GpgVerificationResult(
        ok=True,
        status=f"tag {tag_name}: valid signature{f' (key {key_id})' if key_id else ''}",
        fingerprint=fingerprint,
        key_id=key_id,
        raw_output=out,
    )


async def import_key(fingerprint: str, keyserver: str = "hkps://keys.openpgp.org") -> bool:
    """Import a GPG public key from a keyserver.

    Returns True if the import succeeded (or the key was already present).
    This is a best-effort operation — failures are logged and return False.

    The *fingerprint* and *keyserver* are validated before reaching gpg argv
    to prevent shell / flag injection through user-controlled preference values.
    """
    # Validate inputs before they touch gpg argv.
    clean_fp = _validate_fingerprint(fingerprint)
    if not clean_fp:
        logger.warning("gpg_verify: refusing to import — invalid fingerprint %r", fingerprint)
        return False
    if not _validate_keyserver(keyserver):
        logger.warning("gpg_verify: refusing to import — invalid keyserver %r", keyserver)
        return False
    try:
        rc, out = await _run(
            ["gpg", "--keyserver", keyserver, "--recv-keys", clean_fp],
            Path.cwd(),
        )
        if rc == 0:
            logger.info("gpg_verify: imported key %s from %s", fingerprint, keyserver)
            return True
        # "not changed" is also success (already present)
        if "not changed" in out.lower():
            logger.debug("gpg_verify: key %s already present", fingerprint)
            return True
        logger.warning("gpg_verify: import failed for %s: %s", fingerprint, out.strip()[:200])
        return False
    except FileNotFoundError:
        logger.warning("gpg_verify: gpg binary not found — cannot import key")
        return False
    except Exception:
        logger.exception("gpg_verify: unexpected error importing key %s", fingerprint)
        return False


async def ensure_key_available(fingerprint: str) -> bool:
    """Ensure a GPG key is available in the local keyring.

    Returns True if the key is available or was successfully imported.

    The *fingerprint* is validated before reaching gpg argv.
    """
    clean_fp = _validate_fingerprint(fingerprint)
    if not clean_fp:
        logger.warning("gpg_verify: refusing to list/import — invalid fingerprint %r", fingerprint)
        return False
    try:
        rc, _ = await _run(["gpg", "--list-keys", clean_fp], Path.cwd())
        if rc == 0:
            return True
    except Exception:
        pass
    return await import_key(clean_fp)


async def resolve_gpg_prefs(settings_store) -> GpgPrefs:
    """Load GPG preferences from the settings store.

    Falls back to ``DEFAULT_GPG_PREFS`` on any error.
    """
    try:
        raw = await settings_store.get_preference("user", GPG_PREF_NAMESPACE)
        return GpgPrefs.from_dict({**DEFAULT_GPG_PREFS, **(raw or {})})
    except Exception:
        logger.debug("gpg_verify: pref read failed, using defaults")
        return GpgPrefs()


async def verify_remote_commit(
    project_dir: Path,
    commit_sha: str,
    prefs: GpgPrefs,
) -> GpgVerificationResult:
    """Verify a remote commit according to user preferences.

    When GPG is not enabled, returns an ok-but-skipped result.  When enabled,
    imports the configured key (if not already present) and verifies the
    commit signature.

    Callers should inspect ``result.ok`` and ``prefs.required`` to decide
    whether to block or warn.
    """
    if not prefs.enabled:
        return GpgVerificationResult(ok=True, status="GPG verification disabled — skipped")

    fingerprint = prefs.key_fingerprint
    # Require a pinned fingerprint when verification is enabled. Without one,
    # any valid signature from any key in the server keyring would be accepted,
    # collapsing trust to "some key present".
    if not fingerprint:
        return GpgVerificationResult(
            ok=False,
            status="GPG verification enabled but no key fingerprint configured — cannot verify",
        )

    key_ok = await ensure_key_available(fingerprint)
    if not key_ok:
        return GpgVerificationResult(
            ok=False,
            status=(
                f"cannot verify: failed to import GPG key "
                f"{fingerprint[:16]}…"
            ),
        )

    return await verify_commit(project_dir, commit_sha, fingerprint)
