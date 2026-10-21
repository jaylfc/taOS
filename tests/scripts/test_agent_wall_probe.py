"""Tests for scripts/agent_wall_probe.py."""
from __future__ import annotations

import importlib.util
import json
import socket
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "agent_wall_probe.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("agent_wall_probe", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["agent_wall_probe"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def probe_mod():
    return _load_module()


class TestAdminApi:
    def test_admin_api_pass_on_401(self, probe_mod):
        mock_resp = Mock()
        mock_resp.status = 401
        with patch("agent_wall_probe.http_get", return_value={"ok": True, "status": 401, "error": None}):
            res = probe_mod.check_admin_api("http://127.0.0.1:6969")
            assert res["id"] == "admin-api"
            assert res["status"] == "PASS"
            assert "401" in res["detail"]

    def test_admin_api_pass_on_403(self, probe_mod):
        with patch("agent_wall_probe.http_get", return_value={"ok": True, "status": 403, "error": None}):
            res = probe_mod.check_admin_api("http://127.0.0.1:6969")
            assert res["status"] == "PASS"

    def test_admin_api_fail_on_200(self, probe_mod):
        with patch("agent_wall_probe.http_get", return_value={"ok": True, "status": 200, "error": None}):
            res = probe_mod.check_admin_api("http://127.0.0.1:6969")
            assert res["status"] == "FAIL"


class TestIdmap:
    def test_idmap_pass_high_host_uid(self, probe_mod, tmp_path):
        (tmp_path / "proc" / "self").mkdir(parents=True)
        (tmp_path / "proc" / "self" / "uid_map").write_text("0 100000 65536\n")
        res = probe_mod.check_idmap(str(tmp_path))
        assert res["status"] == "PASS"

    def test_idmap_fail_low_host_uid(self, probe_mod, tmp_path):
        (tmp_path / "proc" / "self").mkdir(parents=True)
        (tmp_path / "proc" / "self" / "uid_map").write_text("0 0 65536\n")
        res = probe_mod.check_idmap(str(tmp_path))
        assert res["status"] == "FAIL"

    def test_idmap_unknown_unreadable(self, probe_mod, tmp_path):
        res = probe_mod.check_idmap(str(tmp_path / "nope"))
        assert res["status"] == "UNKNOWN"


class TestDevices:
    def test_devices_pass_none(self, probe_mod, tmp_path):
        res = probe_mod.check_devices(str(tmp_path))
        assert res["status"] == "PASS"

    def test_devices_fail_found(self, probe_mod, tmp_path):
        (tmp_path / "dev").mkdir()
        (tmp_path / "dev" / "nvidia0").write_text("")
        res = probe_mod.check_devices(str(tmp_path))
        assert res["status"] == "FAIL"
        assert "nvidia0" in res["detail"]


class TestControllerData:
    def test_controller_data_pass_none(self, probe_mod, tmp_path):
        res = probe_mod.check_controller_data(str(tmp_path))
        assert res["status"] == "PASS"

    def test_controller_data_fail_listable(self, probe_mod, tmp_path):
        (tmp_path / "opt" / "taos").mkdir(parents=True)
        (tmp_path / "opt" / "taos" / "file").write_text("x")
        res = probe_mod.check_controller_data(str(tmp_path))
        assert res["status"] == "FAIL"


class TestLimits:
    def test_limits_pass_bounded(self, probe_mod, tmp_path):
        (tmp_path / "sys" / "fs" / "cgroup").mkdir(parents=True)
        (tmp_path / "sys" / "fs" / "cgroup" / "memory.max").write_text("536870912\n")
        (tmp_path / "sys" / "fs" / "cgroup" / "pids.max").write_text("1024\n")
        res = probe_mod.check_limits(str(tmp_path))
        assert res["status"] == "PASS"

    def test_limits_fail_unlimited(self, probe_mod, tmp_path):
        (tmp_path / "sys" / "fs" / "cgroup").mkdir(parents=True)
        (tmp_path / "sys" / "fs" / "cgroup" / "memory.max").write_text("max\n")
        (tmp_path / "sys" / "fs" / "cgroup" / "pids.max").write_text("1024\n")
        res = probe_mod.check_limits(str(tmp_path))
        assert res["status"] == "FAIL"

    def test_limits_unknown_unreadable(self, probe_mod, tmp_path):
        res = probe_mod.check_limits(str(tmp_path / "nope"))
        assert res["status"] == "UNKNOWN"


class TestReach:
    def test_reach_pass_refused(self, probe_mod):
        with patch("agent_wall_probe.tcp_connect", return_value={"ok": True, "connected": False, "error": "refused"}):
            res = probe_mod.check_reach_target("example.invalid", 80)
            assert res["status"] == "PASS"

    def test_reach_pass_timeout(self, probe_mod):
        with patch("agent_wall_probe.tcp_connect", return_value={"ok": True, "connected": False, "error": "timeout"}):
            res = probe_mod.check_reach_target("example.invalid", 80)
            assert res["status"] == "PASS"

    def test_reach_fail_connected(self, probe_mod):
        with patch("agent_wall_probe.tcp_connect", return_value={"ok": True, "connected": True, "error": None}):
            res = probe_mod.check_reach_target("example.invalid", 80)
            assert res["status"] == "FAIL"


class TestInternetControl:
    def test_internet_control_pass(self, probe_mod):
        with patch("agent_wall_probe.http_get", return_value={"ok": True, "status": 200, "error": None}):
            res = probe_mod.check_internet_control("https://example.com")
            assert res["status"] == "PASS"

    def test_internet_control_unknown_on_fail(self, probe_mod):
        with patch("agent_wall_probe.http_get", return_value={"ok": False, "status": None, "error": "TimeoutError"}):
            res = probe_mod.check_internet_control("https://example.com")
            assert res["status"] == "UNKNOWN"


class TestShareAcl:
    def test_share_acl_skip(self, probe_mod):
        res = probe_mod.check_share_acl()
        assert res["status"] == "SKIP"


class TestIntegration:
    def test_exit_code_1_with_fail(self, probe_mod):
        # Mock everything to pass except admin-api returns FAIL
        with patch("agent_wall_probe.check_admin_api", return_value={"id": "admin-api", "status": "FAIL", "detail": "http_200"}):
            with patch("agent_wall_probe.check_idmap", return_value={"id": "idmap", "status": "PASS", "detail": "ok"}):
                with patch("agent_wall_probe.check_devices", return_value={"id": "devices", "status": "PASS", "detail": "none"}):
                    with patch("agent_wall_probe.check_controller_data", return_value={"id": "controller-data", "status": "PASS", "detail": "none"}):
                        with patch("agent_wall_probe.check_limits", return_value={"id": "limits", "status": "PASS", "detail": "bounded"}):
                            with patch("agent_wall_probe.check_internet_control", return_value={"id": "internet-control", "status": "PASS", "detail": "http_200"}):
                                with patch("agent_wall_probe.check_reach_target", return_value={"id": "reach:example.invalid:80", "status": "PASS", "detail": "refused"}):
                                    rc = probe_mod.main(["--target", "example.invalid:80"])
        assert rc == 1

    def test_exit_code_2_with_unknown_only(self, probe_mod):
        with patch("agent_wall_probe.check_admin_api", return_value={"id": "admin-api", "status": "UNKNOWN", "detail": "err"}):
            with patch("agent_wall_probe.check_idmap", return_value={"id": "idmap", "status": "PASS", "detail": "ok"}):
                with patch("agent_wall_probe.check_devices", return_value={"id": "devices", "status": "PASS", "detail": "none"}):
                    with patch("agent_wall_probe.check_controller_data", return_value={"id": "controller-data", "status": "PASS", "detail": "none"}):
                        with patch("agent_wall_probe.check_limits", return_value={"id": "limits", "status": "PASS", "detail": "bounded"}):
                            with patch("agent_wall_probe.check_internet_control", return_value={"id": "internet-control", "status": "PASS", "detail": "http_200"}):
                                with patch("agent_wall_probe.check_share_acl", return_value={"id": "share-acl", "status": "SKIP", "detail": "skip"}):
                                    rc = probe_mod.main([])
        assert rc == 2

    def test_exit_code_0_all_ok(self, probe_mod):
        with patch("agent_wall_probe.check_admin_api", return_value={"id": "admin-api", "status": "PASS", "detail": "403"}):
            with patch("agent_wall_probe.check_idmap", return_value={"id": "idmap", "status": "PASS", "detail": "ok"}):
                with patch("agent_wall_probe.check_devices", return_value={"id": "devices", "status": "PASS", "detail": "none"}):
                    with patch("agent_wall_probe.check_controller_data", return_value={"id": "controller-data", "status": "PASS", "detail": "none"}):
                        with patch("agent_wall_probe.check_limits", return_value={"id": "limits", "status": "PASS", "detail": "bounded"}):
                            with patch("agent_wall_probe.check_internet_control", return_value={"id": "internet-control", "status": "PASS", "detail": "http_200"}):
                                with patch("agent_wall_probe.check_share_acl", return_value={"id": "share-acl", "status": "SKIP", "detail": "skip"}):
                                    rc = probe_mod.main([])
        assert rc == 0

    def test_internet_control_failed_turns_reach_to_unknown(self, probe_mod, capsys):
        with patch("agent_wall_probe.check_admin_api", return_value={"id": "admin-api", "status": "PASS", "detail": "403"}):
            with patch("agent_wall_probe.check_idmap", return_value={"id": "idmap", "status": "PASS", "detail": "ok"}):
                with patch("agent_wall_probe.check_devices", return_value={"id": "devices", "status": "PASS", "detail": "none"}):
                    with patch("agent_wall_probe.check_controller_data", return_value={"id": "controller-data", "status": "PASS", "detail": "none"}):
                        with patch("agent_wall_probe.check_limits", return_value={"id": "limits", "status": "PASS", "detail": "bounded"}):
                            with patch("agent_wall_probe.check_internet_control", return_value={"id": "internet-control", "status": "UNKNOWN", "detail": "failed"}):
                                with patch("agent_wall_probe.check_reach_target", return_value={"id": "reach:example.invalid:80", "status": "PASS", "detail": "refused"}):
                                    rc = probe_mod.main(["--target", "example.invalid:80", "--json"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        reach = [r for r in data if r["id"].startswith("reach:")]
        assert len(reach) == 1
        assert reach[0]["status"] == "UNKNOWN"


class TestJsonOutput:
    def test_json_output(self, probe_mod, capsys):
        with patch("agent_wall_probe.check_admin_api", return_value={"id": "admin-api", "status": "PASS", "detail": "403"}):
            with patch("agent_wall_probe.check_idmap", return_value={"id": "idmap", "status": "PASS", "detail": "ok"}):
                with patch("agent_wall_probe.check_devices", return_value={"id": "devices", "status": "PASS", "detail": "none"}):
                    with patch("agent_wall_probe.check_controller_data", return_value={"id": "controller-data", "status": "PASS", "detail": "none"}):
                        with patch("agent_wall_probe.check_limits", return_value={"id": "limits", "status": "PASS", "detail": "bounded"}):
                            with patch("agent_wall_probe.check_internet_control", return_value={"id": "internet-control", "status": "PASS", "detail": "http_200"}):
                                with patch("agent_wall_probe.check_share_acl", return_value={"id": "share-acl", "status": "SKIP", "detail": "skip"}):
                                    rc = probe_mod.main(["--json"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)
