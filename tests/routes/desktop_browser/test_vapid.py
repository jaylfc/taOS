"""Tests for VAPID keypair bootstrap."""
from __future__ import annotations

import base64
import os
import time

import pytest


class TestLoadOrCreateVapidKeypair:
    def test_first_call_generates_and_persists(self, tmp_path):
        from tinyagentos.routes.desktop_browser.vapid import load_or_create_vapid_keypair

        pub, priv = load_or_create_vapid_keypair(tmp_path)

        pem_path = tmp_path / "vapid.pem"
        assert pem_path.exists()
        assert isinstance(pub, str)
        assert isinstance(priv, str)

        # Uncompressed P-256 point: 0x04 prefix + 32 bytes X + 32 bytes Y = 65 bytes.
        # base64url without padding → 87 chars; decode must yield exactly 65 bytes.
        decoded = base64.urlsafe_b64decode(pub + "==")  # pad generously before decode
        assert len(decoded) == 65
        assert decoded[0] == 0x04

    def test_second_call_returns_same_keys(self, tmp_path):
        from tinyagentos.routes.desktop_browser.vapid import load_or_create_vapid_keypair

        pub1, priv1 = load_or_create_vapid_keypair(tmp_path)

        pem_path = tmp_path / "vapid.pem"
        mtime_before = pem_path.stat().st_mtime

        time.sleep(0.01)

        pub2, priv2 = load_or_create_vapid_keypair(tmp_path)

        assert pub2 == pub1
        assert priv2 == priv1
        assert pem_path.stat().st_mtime == mtime_before

    @pytest.mark.skipif(os.name == "nt", reason="POSIX file modes not applicable on Windows")
    def test_file_mode_is_0600(self, tmp_path):
        from tinyagentos.routes.desktop_browser.vapid import load_or_create_vapid_keypair

        load_or_create_vapid_keypair(tmp_path)

        pem_path = tmp_path / "vapid.pem"
        assert os.stat(pem_path).st_mode & 0o777 == 0o600
