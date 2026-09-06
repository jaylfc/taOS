"""Tests for tinyagentos/gpg_verify.py — GPG signature verification."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.gpg_verify import (
    DEFAULT_GPG_PREFS,
    GPG_PREF_NAMESPACE,
    GpgPrefs,
    GpgVerificationResult,
    _parse_fingerprint,
    _parse_key_id,
    resolve_gpg_prefs,
    verify_commit,
    verify_remote_commit,
    verify_tag,
)


# ── dataclass / parse tests ────────────────────────────────────────────────


class TestGpgPrefs:
    def test_defaults(self):
        prefs = GpgPrefs()
        assert prefs.enabled is False
        assert prefs.required is False
        assert prefs.key_fingerprint is None

    def test_from_empty_dict(self):
        prefs = GpgPrefs.from_dict({})
        assert prefs.enabled is False
        assert prefs.required is False
        assert prefs.key_fingerprint is None

    def test_from_none(self):
        prefs = GpgPrefs.from_dict(None)
        assert prefs.enabled is False

    def test_from_partial_dict(self):
        prefs = GpgPrefs.from_dict({"enabled": True})
        assert prefs.enabled is True
        assert prefs.required is False
        assert prefs.key_fingerprint is None

    def test_from_full_dict(self):
        prefs = GpgPrefs.from_dict({
            "enabled": True,
            "required": True,
            "key_fingerprint": "AAAA BBBB CCCC DDDD EEEE  FFFF 0000 1111 2222 3333",
        })
        assert prefs.enabled is True
        assert prefs.required is True
        assert prefs.key_fingerprint == "AAAA BBBB CCCC DDDD EEEE  FFFF 0000 1111 2222 3333"


class TestParseFingerprint:
    def test_parses_primary_key_from_subkey_signature(self):
        """When a subkey signs, the last VALIDSIG field is the primary key fpr."""
        output = (
            "[GNUPG:] NEWSIG\n"
            "[GNUPG:] VALIDSIG 9999888877776666555544443333222211110000 2024-01-01 1704067200 0 4 0 1 8 00 AAAABBBBCCCCDDDDEEEEFFFF0000111122223333\n"
        )
        fp = _parse_fingerprint(output)
        # Should return the primary key (last field), not the subkey
        assert fp == "AAAABBBBCCCCDDDDEEEEFFFF0000111122223333"

    def test_parses_fingerprint_when_primary_key_signs_directly(self):
        """When the primary key signs, there are only 11 fields — use parts[2]."""
        output = (
            "[GNUPG:] NEWSIG\n"
            "[GNUPG:] VALIDSIG AAAABBBBCCCCDDDDEEEEFFFF0000111122223333 2024-01-01 1704067200 0 4 0 1 8 00\n"
        )
        fp = _parse_fingerprint(output)
        # 11 fields → parts[2] IS the primary key
        assert fp == "AAAABBBBCCCCDDDDEEEEFFFF0000111122223333"

    def test_returns_none_when_no_validsig(self):
        output = (
            "gpg: Can't check signature: No public key\n"
            "gpg: Signature made Mon 01 Jan 2024 using RSA key ABCDEF1234567890\n"
        )
        assert _parse_fingerprint(output) is None

    def test_returns_none_for_empty(self):
        assert _parse_fingerprint("") is None

    def test_parses_fingerprint_when_primary_key_signs_with_extra_fields(self):
        """When primary key signs but GPG emits fprlen + algo name (13 fields),
        parts[-1] is not a fingerprint but parts[2] IS the primary key."""
        output = (
            "[GNUPG:] NEWSIG\n"
            "[GNUPG:] VALIDSIG AAAABBBBCCCCDDDDEEEEFFFF0000111122223333 2024-01-01 1704067200 0 4 0 1 8 00 20 EDDSA\n"
        )
        fp = _parse_fingerprint(output)
        # parts[-1] = "EDDSA" (not a fingerprint), parts[2] = primary key fpr
        assert fp == "AAAABBBBCCCCDDDDEEEEFFFF0000111122223333"


class TestParseKeyId:
    def test_parses_rsa_key(self):
        output = "gpg: Signature made Mon 01 Jan 2024 using RSA key ABCDEF1234567890\n"
        assert _parse_key_id(output) == "ABCDEF1234567890"

    def test_parses_eddsa_key(self):
        output = "gpg: Signature made Mon 01 Jan 2024 using EDDSA key FEDCBA0987654321\n"
        assert _parse_key_id(output) == "FEDCBA0987654321"

    def test_returns_none_when_no_key(self):
        assert _parse_key_id("some random output") is None

    def test_parses_key_id_from_validsig_primary_key_signs_directly(self):
        """When primary key signs directly (11 fields), extract from parts[2]."""
        output = (
            "[GNUPG:] NEWSIG\n"
            "[GNUPG:] VALIDSIG AAAABBBBCCCCDDDDEEEEFFFF0000111122223333 2024-01-01 1704067200 0 4 0 1 8 00\n"
        )
        # 11 fields → parts[2] IS the primary key
        assert _parse_key_id(output) == "0000111122223333"

    def test_parses_key_id_from_validsig_subkey_signs(self):
        """When subkey signs (12 fields), extract from primary-key fpr for consistency."""
        output = (
            "[GNUPG:] NEWSIG\n"
            "[GNUPG:] VALIDSIG 9999888877776666555544443333222211110000 2024-01-01 1704067200 0 4 0 1 8 00 AAAABBBBCCCCDDDDEEEEFFFF0000111122223333\n"
        )
        # 12 fields → primary-key fpr is in parts[-1], key_id from primary key
        assert _parse_key_id(output) == "0000111122223333"

    def test_validsig_fallback_does_not_overwrite_human_readable(self):
        """Human-readable 'using RSA key' takes priority over VALIDSIG fallback."""
        output = (
            "gpg: Signature made … using RSA key ABCDEF1234567890\n"
            "[GNUPG:] NEWSIG\n"
            "[GNUPG:] VALIDSIG AAAABBBBCCCCDDDDEEEEFFFF0000111122223333 2024-01-01 1704067200 0 4 0 1 8 00\n"
        )
        assert _parse_key_id(output) == "ABCDEF1234567890"

    def test_parses_key_id_from_validsig_primary_key_with_extra_fields(self):
        """When primary key signs with extra GPG fields (fprlen+algo), derive
        key ID from parts[2] (primary key)."""
        output = (
            "[GNUPG:] NEWSIG\n"
            "[GNUPG:] VALIDSIG AAAABBBBCCCCDDDDEEEEFFFF0000111122223333 2024-01-01 1704067200 0 4 0 1 8 00 20 EDDSA\n"
        )
        # parts[-1] = "EDDSA", parts[2] = primary key fpr
        assert _parse_key_id(output) == "0000111122223333"


# ── verify_commit tests (mocked subprocess) ────────────────────────────────


SAMPLE_GOOD_OUTPUT = (
    "gpg: Signature made Mon 01 Jan 2024 12:00:00 UTC\n"
    'gpg:                using RSA key ABCDEF1234567890\n'
    'gpg: Good signature from "Test User <test@example.com>" [ultimate]\n'
    "Primary key fingerprint: AAAA BBBB CCCC DDDD EEEE  FFFF 0000 1111 2222 3333\n"
    "[GNUPG:] NEWSIG\n"
    # Subkey signing — last field is the primary-key fingerprint
    "[GNUPG:] VALIDSIG 9999888877776666555544443333222211110000 2024-01-01 1704067200 0 4 0 1 8 00 AAAABBBBCCCCDDDDEEEEFFFF0000111122223333\n"
)

SAMPLE_BAD_OUTPUT = (
    "gpg: Can't check signature: No public key\n"
    "gpg: Signature made Mon 01 Jan 2024 12:00:00 UTC\n"
    'gpg:                using RSA key ABCDEF1234567890\n'
)

SAMPLE_MISMATCH_OUTPUT = (
    "gpg: Signature made Mon 01 Jan 2024 12:00:00 UTC\n"
    'gpg:                using RSA key ABCDEF1234567890\n'
    'gpg: Good signature from "Other User <other@example.com>" [unknown]\n'
    "Primary key fingerprint: 9999 8888 7777 6666 5555  4444 3333 2222 1111 0000\n"
    "[GNUPG:] NEWSIG\n"
    # Subkey signing — primary key is 9999…, which differs from expected
    "[GNUPG:] VALIDSIG FFFFEEEEDDDDCCCCBBBBAAAA9999888877776666 2024-01-01 1704067200 0 4 0 1 8 00 9999888877776666555544443333222211110000\n"
)


def _fake_proc(returncode=0, stdout=""):
    """Return a mock Process-like object with communicate() and returncode."""
    async def _communicate():
        return stdout.encode() if stdout else b"", b""
    mock = MagicMock()
    mock.communicate = _communicate
    mock.returncode = returncode
    return mock


@pytest.mark.asyncio
async def test_verify_commit_success(monkeypatch):
    """A valid signature with no fingerprint requirement returns ok=True."""
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        AsyncMock(return_value=_fake_proc(returncode=0, stdout=SAMPLE_GOOD_OUTPUT)),
    )

    result = await verify_commit(Path("/tmp"), "abc1234")
    assert result.ok is True
    assert "valid signature" in result.status


@pytest.mark.asyncio
async def test_verify_commit_no_public_key(monkeypatch):
    """A missing public key returns ok=False."""
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        AsyncMock(return_value=_fake_proc(returncode=1, stdout=SAMPLE_BAD_OUTPUT)),
    )

    result = await verify_commit(Path("/tmp"), "abc1234")
    assert result.ok is False


@pytest.mark.asyncio
async def test_verify_commit_empty_sha():
    """Empty SHA returns ok=False immediately."""
    result = await verify_commit(Path("/tmp"), "")
    assert result.ok is False
    assert "no commit SHA" in result.status


@pytest.mark.asyncio
async def test_verify_commit_fingerprint_match(monkeypatch):
    """When fingerprint matches, ok=True."""
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        AsyncMock(return_value=_fake_proc(returncode=0, stdout=SAMPLE_GOOD_OUTPUT)),
    )

    result = await verify_commit(
        Path("/tmp"), "abc1234",
        expected_fingerprint="AAAABBBBCCCCDDDDEEEEFFFF0000111122223333",
    )
    assert result.ok is True


@pytest.mark.asyncio
async def test_verify_commit_fingerprint_mismatch(monkeypatch):
    """When fingerprint doesn't match, ok=False even if signature is valid."""
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        AsyncMock(return_value=_fake_proc(returncode=0, stdout=SAMPLE_MISMATCH_OUTPUT)),
    )

    result = await verify_commit(
        Path("/tmp"), "abc1234",
        expected_fingerprint="AAAABBBBCCCCDDDDEEEEFFFF0000111122223333",
    )
    assert result.ok is False
    assert "fingerprint mismatch" in result.status.lower()


@pytest.mark.asyncio
async def test_verify_commit_no_fingerprint_in_output(monkeypatch):
    """When fingerprint is required but output has none, ok=False."""
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        AsyncMock(return_value=_fake_proc(returncode=0, stdout="gpg: Good signature\n")),
    )

    result = await verify_commit(
        Path("/tmp"), "abc1234",
        expected_fingerprint="AAAABBBBCCCCDDDDEEEEFFFF0000111122223333",
    )
    assert result.ok is False
    assert "could not determine" in result.status.lower()


# ── verify_tag tests ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_tag_success(monkeypatch):
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        AsyncMock(return_value=_fake_proc(returncode=0, stdout=SAMPLE_GOOD_OUTPUT)),
    )

    result = await verify_tag(Path("/tmp"), "v1.0.0")
    assert result.ok is True


@pytest.mark.asyncio
async def test_verify_tag_empty_name():
    result = await verify_tag(Path("/tmp"), "")
    assert result.ok is False
    assert "no tag name" in result.status


@pytest.mark.asyncio
async def test_verify_tag_bad_signature(monkeypatch):
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        AsyncMock(return_value=_fake_proc(returncode=1, stdout=SAMPLE_BAD_OUTPUT)),
    )

    result = await verify_tag(Path("/tmp"), "v1.0.0")
    assert result.ok is False


# ── verify_remote_commit tests ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_remote_disabled():
    """When GPG is disabled, returns ok=True with skip message."""
    prefs = GpgPrefs(enabled=False)
    result = await verify_remote_commit(Path("/tmp"), "abc1234", prefs)
    assert result.ok is True
    assert "disabled" in result.status.lower()


@pytest.mark.asyncio
async def test_verify_remote_enabled_no_fingerprint():
    """When enabled but no fingerprint, verification fails — a pinned
    fingerprint is required to prevent accepting any key in the keyring."""
    prefs = GpgPrefs(enabled=True, key_fingerprint=None)
    result = await verify_remote_commit(Path("/tmp"), "abc1234", prefs)
    assert result.ok is False
    assert "no key fingerprint" in result.status.lower()


@pytest.mark.asyncio
async def test_verify_remote_enabled_key_import_fails(monkeypatch):
    """When key import fails, returns ok=False."""
    async def fake_import(fingerprint, keyserver="hkps://keys.openpgp.org"):
        return False

    async def fake_list_keys(*args, **kwargs):
        class FakeProc:
            returncode = 1
        return FakeProc()

    monkeypatch.setattr(
        "tinyagentos.gpg_verify.import_key", fake_import,
    )
    monkeypatch.setattr(
        "tinyagentos.gpg_verify._run", fake_list_keys,
    )

    prefs = GpgPrefs(enabled=True, key_fingerprint="AAAABBBBCCCCDDDDEEEEFFFF0000111122223333")
    result = await verify_remote_commit(Path("/tmp"), "abc1234", prefs)
    assert result.ok is False
    assert "import" in result.status.lower()


# ── resolve_gpg_prefs tests ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_gpg_prefs_defaults():
    """When store returns None, use defaults."""
    store = MagicMock()
    store.get_preference = AsyncMock(return_value=None)
    prefs = await resolve_gpg_prefs(store)
    assert prefs.enabled is False
    assert prefs.required is False
    assert prefs.key_fingerprint is None


@pytest.mark.asyncio
async def test_resolve_gpg_prefs_with_data():
    """When store returns saved data, merge with defaults."""
    store = MagicMock()
    store.get_preference = AsyncMock(return_value={
        "enabled": True,
        "key_fingerprint": "AAAA BBBB",
    })
    prefs = await resolve_gpg_prefs(store)
    assert prefs.enabled is True
    assert prefs.required is False  # from defaults
    assert prefs.key_fingerprint == "AAAA BBBB"


@pytest.mark.asyncio
async def test_resolve_gpg_prefs_store_error():
    """On store error, fall back to defaults."""
    store = MagicMock()
    store.get_preference = AsyncMock(side_effect=Exception("db down"))
    prefs = await resolve_gpg_prefs(store)
    assert prefs.enabled is False
