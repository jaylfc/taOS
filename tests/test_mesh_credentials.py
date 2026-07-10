"""Host-local mesh credential store + the cluster-join poll-intercept that
populates it (taOSgo Slice 1). The persisted service tokens must land 0600
server-side and never appear in the browser-facing poll body."""
from __future__ import annotations

import json
import os
import stat

import pytest

from tinyagentos.taosnet import mesh_credentials, passkey_client


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
    # Clear any env overrides so the file is the source of truth.
    monkeypatch.delenv("TAOS_CONTROLLER_TOKEN", raising=False)
    monkeypatch.delenv("TAOS_SITES_TOKEN", raising=False)
    return tmp_path


_READY = {
    "status": "ready",
    "account_id": "acct_1",
    "host_id": "host_9",
    "tailnet_name": "jason",
    "headscale_preauth_key": "SINGLE-USE-SECRET",
    "controller_token": "ctl.jwt.aaa",
    "sites_token": "sites.jwt.bbb",
    "joined_at": "2026-07-10T00:00:00Z",
}


class TestStore:
    def test_round_trip(self, data_dir):
        mesh_credentials.save_mesh_credentials(_READY)
        assert mesh_credentials.get_controller_token() == "ctl.jwt.aaa"
        assert mesh_credentials.get_sites_token() == "sites.jwt.bbb"
        assert mesh_credentials.get_host_id() == "host_9"
        assert mesh_credentials.has_mesh_credentials() is True

    def test_preauth_key_is_not_persisted(self, data_dir):
        mesh_credentials.save_mesh_credentials(_READY)
        on_disk = json.loads((data_dir / "mesh_credentials.json").read_text())
        assert "headscale_preauth_key" not in on_disk
        assert on_disk["controller_token"] == "ctl.jwt.aaa"

    def test_file_is_0600(self, data_dir):
        mesh_credentials.save_mesh_credentials(_READY)
        mode = stat.S_IMODE(os.stat(data_dir / "mesh_credentials.json").st_mode)
        assert mode == 0o600

    def test_missing_required_fields_raise(self, data_dir):
        with pytest.raises(ValueError):
            mesh_credentials.save_mesh_credentials({"controller_token": "x"})  # no host_id
        with pytest.raises(ValueError):
            mesh_credentials.save_mesh_credentials({"host_id": "h"})  # no controller_token

    def test_idempotent_resave(self, data_dir):
        mesh_credentials.save_mesh_credentials(_READY)
        mesh_credentials.save_mesh_credentials(_READY)  # re-poll: same data, no error
        assert mesh_credentials.get_controller_token() == "ctl.jwt.aaa"

    def test_none_before_join(self, data_dir):
        assert mesh_credentials.get_controller_token() is None
        assert mesh_credentials.get_sites_token() is None
        assert mesh_credentials.has_mesh_credentials() is False

    def test_env_override_wins(self, data_dir, monkeypatch):
        mesh_credentials.save_mesh_credentials(_READY)
        monkeypatch.setenv("TAOS_CONTROLLER_TOKEN", "env-override")
        assert mesh_credentials.get_controller_token() == "env-override"

    def test_clear(self, data_dir):
        mesh_credentials.save_mesh_credentials(_READY)
        mesh_credentials.clear()
        assert mesh_credentials.get_controller_token() is None
        mesh_credentials.clear()  # no-op when already gone

    def test_corrupt_non_dict_file_is_treated_as_absent(self, data_dir):
        # An external edit / corruption leaving a non-object must not raise from
        # the getters (which would break the headless passkey fetch).
        (data_dir / "mesh_credentials.json").write_text("[1, 2, 3]")
        assert mesh_credentials.get_controller_token() is None
        assert mesh_credentials.get_sites_token() is None
        assert mesh_credentials.has_mesh_credentials() is False

    def test_passkey_client_delegates(self, data_dir):
        mesh_credentials.save_mesh_credentials(_READY)
        # The download-manager import path reads the persisted token now.
        assert passkey_client.get_controller_token() == "ctl.jwt.aaa"


class TestPollIntercept:
    def _resp(self, body, status=200, media="application/json"):
        from fastapi import Response

        content = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
        return Response(content=content, status_code=status, media_type=media)

    def test_ready_payload_persists_and_strips_tokens(self, data_dir):
        from tinyagentos.routes.account_proxy import _persist_join_credentials

        out, _ji = _persist_join_credentials(self._resp(_READY))
        # Persisted server-side.
        assert mesh_credentials.get_controller_token() == "ctl.jwt.aaa"
        assert mesh_credentials.get_sites_token() == "sites.jwt.bbb"
        # Stripped from the browser-facing body.
        browser = json.loads(out.body)
        assert "controller_token" not in browser
        assert "sites_token" not in browser
        # Non-secret status fields survive for the UI.
        assert browser["status"] == "ready"
        assert browser["tailnet_name"] == "jason"

    def test_pending_payload_untouched(self, data_dir):
        from tinyagentos.routes.account_proxy import _persist_join_credentials

        pending = {"status": "pending"}
        out, _ji = _persist_join_credentials(self._resp(pending))
        assert json.loads(out.body) == pending
        assert mesh_credentials.has_mesh_credentials() is False

    def test_non_200_untouched(self, data_dir):
        from tinyagentos.routes.account_proxy import _persist_join_credentials

        out, _ji = _persist_join_credentials(self._resp({"error": "nope"}, status=403))
        assert out.status_code == 403
        assert mesh_credentials.has_mesh_credentials() is False

    def test_non_json_untouched(self, data_dir):
        from tinyagentos.routes.account_proxy import _persist_join_credentials

        out, _ji = _persist_join_credentials(self._resp(b"<html>oops</html>", media="text/html"))
        assert out.body == b"<html>oops</html>"
        assert mesh_credentials.has_mesh_credentials() is False

    def test_tokens_stripped_even_when_save_fails(self, data_dir):
        # controller_token present but host_id missing -> save_mesh_credentials
        # raises. The token must STILL be stripped (never leaked to compensate).
        from tinyagentos.routes.account_proxy import _persist_join_credentials

        bad = {"status": "ready", "controller_token": "ctl.leak", "sites_token": "s.leak"}
        out, _ji = _persist_join_credentials(self._resp(bad))
        browser = json.loads(out.body)
        assert "controller_token" not in browser and "sites_token" not in browser
        assert mesh_credentials.has_mesh_credentials() is False  # save did fail

    def test_json_without_content_type_is_still_stripped(self, data_dir):
        # A JSON body served without a Content-Type header must not bypass
        # stripping (parsing does not depend on media_type).
        from tinyagentos.routes.account_proxy import _persist_join_credentials

        out, _ji = _persist_join_credentials(self._resp(_READY, media=None))
        browser = json.loads(out.body)
        assert "controller_token" not in browser
        assert mesh_credentials.get_controller_token() == "ctl.jwt.aaa"

    def test_preauth_key_stripped_and_join_intent_returned(self, data_dir):
        # Slice 2: the single-use preauth key is stripped from the browser body
        # (consumed server-side) and surfaced as a join intent for the caller.
        from tinyagentos.routes.account_proxy import _persist_join_credentials

        out, join_intent = _persist_join_credentials(self._resp(_READY))
        browser = json.loads(out.body)
        assert "headscale_preauth_key" not in browser
        assert join_intent == {"preauth_key": "SINGLE-USE-SECRET", "hostname": "host_9"}

    def test_preauth_only_no_service_token_still_stripped(self, data_dir):
        # A ready payload carrying ONLY the preauth key (no service tokens) must
        # still fire + strip it -- the "ALWAYS strip" guarantee cannot hinge on a
        # service token being present.
        from tinyagentos.routes.account_proxy import _persist_join_credentials

        pre_only = {"status": "ready", "host_id": "h", "headscale_preauth_key": "PK"}
        out, join_intent = _persist_join_credentials(self._resp(pre_only))
        assert "headscale_preauth_key" not in json.loads(out.body)
        assert join_intent == {"preauth_key": "PK", "hostname": "h"}
        assert mesh_credentials.has_mesh_credentials() is False  # no controller token

    def test_no_preauth_means_no_join_intent(self, data_dir):
        from tinyagentos.routes.account_proxy import _persist_join_credentials

        no_pre = {k: v for k, v in _READY.items() if k != "headscale_preauth_key"}
        _out, join_intent = _persist_join_credentials(self._resp(no_pre))
        assert join_intent is None
