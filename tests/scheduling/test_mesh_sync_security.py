"""Security regression tests for mesh_sync: SSRF + SQL injection (issue #644)."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from tinyagentos.scheduling.mesh_sync import MeshSync, is_safe_url


# ---------------------------------------------------------------------------
# SSRF — is_safe_url
# ---------------------------------------------------------------------------


def _mock_getaddrinfo(ip: str):
    """Return a getaddrinfo stub that resolves any hostname to *ip*."""

    def _stub(host, port, *args, **kwargs):
        return [(None, None, None, None, (ip, port or 80))]

    return _stub


class TestIsSafeUrl:
    def test_cloud_metadata_rejected_by_default(self):
        """169.254.169.254 must be rejected when allow_private=False (default)."""
        with patch("socket.getaddrinfo", _mock_getaddrinfo("169.254.169.254")):
            assert is_safe_url("http://169.254.169.254/latest/meta-data/") is False

    def test_cloud_metadata_rejected_even_with_allow_private(self):
        """169.254.169.254 is always blocked, even when allow_private=True."""
        with patch("socket.getaddrinfo", _mock_getaddrinfo("169.254.169.254")):
            assert is_safe_url("http://169.254.169.254/", allow_private=True) is False

    def test_loopback_rejected_by_default(self):
        with patch("socket.getaddrinfo", _mock_getaddrinfo("127.0.0.1")):
            assert is_safe_url("http://127.0.0.1/", allow_private=False) is False

    def test_loopback_rejected_even_with_allow_private(self):
        with patch("socket.getaddrinfo", _mock_getaddrinfo("127.0.0.1")):
            assert is_safe_url("http://127.0.0.1/", allow_private=True) is False

    def test_public_ip_allowed_by_default(self):
        with patch("socket.getaddrinfo", _mock_getaddrinfo("93.184.216.34")):
            assert is_safe_url("https://example.com/", allow_private=False) is True

    def test_public_ip_rejected_with_allow_private_false(self):
        """Public IPs should still pass — they're not private ranges."""
        with patch("socket.getaddrinfo", _mock_getaddrinfo("8.8.8.8")):
            assert is_safe_url("https://dns.google/", allow_private=False) is True

    def test_rfc1918_10_rejected_by_default(self):
        with patch("socket.getaddrinfo", _mock_getaddrinfo("10.0.0.1")):
            assert is_safe_url("http://10.0.0.1:6969/", allow_private=False) is False

    def test_rfc1918_192168_rejected_by_default(self):
        with patch("socket.getaddrinfo", _mock_getaddrinfo("192.168.1.100")):
            assert is_safe_url("http://192.168.1.100/", allow_private=False) is False

    def test_rfc1918_172_rejected_by_default(self):
        with patch("socket.getaddrinfo", _mock_getaddrinfo("172.16.0.5")):
            assert is_safe_url("http://172.16.0.5/", allow_private=False) is False

    def test_rfc1918_allowed_with_allow_private(self):
        """LAN workers on RFC1918 are permitted when allow_private=True."""
        with patch("socket.getaddrinfo", _mock_getaddrinfo("192.168.6.123")):
            assert is_safe_url("http://192.168.6.123:6969/", allow_private=True) is True

    def test_empty_url_rejected(self):
        assert is_safe_url("") is False

    def test_no_hostname_rejected(self):
        assert is_safe_url("not-a-url") is False


# ---------------------------------------------------------------------------
# SQL injection — import_delta column validation
# ---------------------------------------------------------------------------


def _make_db_with_table(table: str, columns: list[str]) -> sqlite3.Connection:
    """Create an in-memory SQLite DB with the given table and columns."""
    col_defs = ", ".join(f"{c} TEXT" for c in columns)
    db = sqlite3.connect(":memory:")
    db.execute(f"CREATE TABLE {table} ({col_defs})")
    db.commit()
    return db


@pytest.mark.asyncio
async def test_import_delta_rejects_unknown_column():
    """A peer record with a column not in the local schema must be dropped."""
    mesh = MeshSync(db_path=":memory:")
    # table must be in SYNCABLE_TABLES — use kg_triples
    table = "kg_triples"
    db = _make_db_with_table(table, ["subject", "predicate", "object", "created_at"])

    malicious_record = {
        "subject": "foo",
        "predicate": "bar",
        "object": "baz",
        "created_at": "1.0",
        # attacker-controlled column name attempting injection
        "injected); DROP TABLE kg_triples; --": "evil",
    }

    imported = await mesh.import_delta(db, table, [malicious_record])

    assert imported == 0, "Malicious record should be rejected"
    # Table must still exist and be empty
    rows = db.execute(f"SELECT * FROM {table}").fetchall()
    assert rows == []


@pytest.mark.asyncio
async def test_import_delta_accepts_valid_record():
    """A clean peer record with only known columns should be imported."""
    mesh = MeshSync(db_path=":memory:")
    table = "kg_triples"
    db = _make_db_with_table(table, ["subject", "predicate", "object", "created_at"])

    good_record = {
        "subject": "alice",
        "predicate": "knows",
        "object": "bob",
        "created_at": "1.0",
    }

    imported = await mesh.import_delta(db, table, [good_record])

    assert imported == 1
    rows = db.execute(f"SELECT subject FROM {table}").fetchall()
    assert rows[0][0] == "alice"


@pytest.mark.asyncio
async def test_import_delta_partial_batch_skips_bad_records():
    """Good records in a batch are imported; bad records are skipped."""
    mesh = MeshSync(db_path=":memory:")
    table = "kg_triples"
    db = _make_db_with_table(table, ["subject", "predicate", "object", "created_at"])

    records = [
        {"subject": "a", "predicate": "p", "object": "o", "created_at": "1.0"},
        {
            "subject": "a",
            "predicate": "p",
            "object": "o",
            "created_at": "1.0",
            "EVIL) --": "bad",
        },
        {"subject": "b", "predicate": "q", "object": "r", "created_at": "2.0"},
    ]

    imported = await mesh.import_delta(db, table, records)

    assert imported == 2  # only the two clean records


@pytest.mark.asyncio
async def test_import_delta_unknown_table_returns_zero():
    """Records for a table not in SYNCABLE_TABLES are silently dropped."""
    mesh = MeshSync(db_path=":memory:")
    db = sqlite3.connect(":memory:")
    imported = await mesh.import_delta(db, "not_a_real_table", [{"col": "val"}])
    assert imported == 0


@pytest.mark.asyncio
async def test_import_delta_empty_schema_returns_zero():
    """If the local DB has no schema for the table, import returns 0."""
    mesh = MeshSync(db_path=":memory:")
    # DB with no tables at all
    db = sqlite3.connect(":memory:")
    imported = await mesh.import_delta(db, "kg_triples", [{"subject": "x"}])
    assert imported == 0
