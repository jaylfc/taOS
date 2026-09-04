import pytest


@pytest.mark.asyncio
class TestAgentDesktopLifecycle:
    async def test_status_before_install_is_not_installed(self, client):
        resp = await client.get("/api/agents/test-agent/desktop/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "not_installed"
        assert data["running"] is False

    async def test_install_returns_installed(self, client, monkeypatch):
        calls = []

        async def fake_exec(name, cmd, timeout=300):
            calls.append((name, list(cmd)))
            return (0, "OK")

        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        resp = await client.post("/api/agents/test-agent/desktop/install")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "installed"
        assert len(calls) == 1
        assert calls[0][0] == "taos-agent-test-agent"
        assert "apt-get install" in " ".join(calls[0][1])

    async def test_install_is_idempotent(self, client, monkeypatch):
        calls = []

        async def fake_exec(name, cmd, timeout=300):
            calls.append((name, list(cmd)))
            return (0, "OK")

        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        resp1 = await client.post("/api/agents/test-agent/desktop/install")
        assert resp1.status_code == 200
        resp2 = await client.post("/api/agents/test-agent/desktop/install")
        assert resp2.status_code == 200
        assert len(calls) == 1

    async def test_start_after_install(self, client, monkeypatch):
        calls = []

        async def fake_exec(name, cmd, timeout=300):
            calls.append((name, list(cmd)))
            return (0, "OK")

        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        resp = await client.post("/api/agents/test-agent/desktop/install")
        assert resp.status_code == 200

        resp = await client.post("/api/agents/test-agent/desktop/start")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "running"
        assert any("x11vnc" in " ".join(c[1]) for c in calls)

    async def test_full_lifecycle_mutates_status(self, client, monkeypatch):
        calls = []

        async def fake_exec(name, cmd, timeout=300):
            calls.append((name, list(cmd)))
            cmd_str = " ".join(cmd)
            if "apt-get" in cmd_str:
                return (0, "OK")
            if "pgrep" in cmd_str:
                return (0, "RUNNING")
            if "x11vnc" in cmd_str:
                return (0, "OK")
            if "pkill" in cmd_str:
                return (0, "OK")
            return (0, "OK")

        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        install = await client.post("/api/agents/test-agent/desktop/install")
        assert install.json()["state"] == "installed"

        start = await client.post("/api/agents/test-agent/desktop/start")
        assert start.json()["state"] == "running"

        status_running = await client.get("/api/agents/test-agent/desktop/status")
        assert status_running.json()["running"] is True
        assert status_running.json()["state"] == "running"

        stop = await client.post("/api/agents/test-agent/desktop/stop")
        assert stop.json()["state"] == "stopped"

        status_stopped = await client.get("/api/agents/test-agent/desktop/status")
        assert status_stopped.json()["running"] is False
        assert status_stopped.json()["state"] == "stopped"

    async def test_start_without_install_is_rejected(self, client):
        resp = await client.post("/api/agents/test-agent/desktop/start")
        assert resp.status_code == 409

    async def test_stop_when_not_running_is_idempotent(self, client):
        resp = await client.post("/api/agents/test-agent/desktop/stop")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "not_installed"

    async def test_status_probes_running_process(self, client, monkeypatch):
        calls = []

        async def fake_exec(name, cmd, timeout=300):
            calls.append((name, list(cmd)))
            cmd_str = " ".join(cmd)
            if "apt-get" in cmd_str:
                return (0, "OK")
            if "pgrep" in cmd_str:
                return (0, "RUNNING")
            if "x11vnc" in cmd_str:
                return (0, "OK")
            return (0, "OK")

        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        await client.post("/api/agents/test-agent/desktop/install")
        await client.post("/api/agents/test-agent/desktop/start")

        resp = await client.get("/api/agents/test-agent/desktop/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is True
        assert data["state"] == "running"

    async def test_status_detects_stopped_process(self, client, monkeypatch):
        calls = []

        async def fake_exec(name, cmd, timeout=300):
            calls.append((name, list(cmd)))
            cmd_str = " ".join(cmd)
            if "apt-get" in cmd_str:
                return (0, "OK")
            if "x11vnc" in cmd_str:
                return (0, "OK")
            if "pgrep" in cmd_str:
                return (0, "STOPPED")
            return (0, "OK")

        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        await client.post("/api/agents/test-agent/desktop/install")
        await client.post("/api/agents/test-agent/desktop/start")

        resp = await client.get("/api/agents/test-agent/desktop/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is False
        assert data["state"] == "stopped"

    async def test_default_agent_image_unchanged_without_desktop(self, client, monkeypatch):
        install_calls = []

        async def fake_exec(name, cmd, timeout=300):
            install_calls.append((name, list(cmd)))
            return (0, "OK")

        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        await client.get("/api/agents/test-agent/desktop/status")
        await client.post("/api/agents/test-agent/desktop/stop")
        assert len(install_calls) == 0


# ---------------------------------------------------------------------------
# Fold of the #2700 review: authorization, install retry, serialization, and
# stop-failure reporting on the agent-desktop lifecycle routes.
# ---------------------------------------------------------------------------

import asyncio

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from taos_test_csrf import csrf_event_hooks


def _add_member_user(app, username: str, password: str) -> str:
    """Inject a non-admin user into the auth store and return their user_id."""
    auth = app.state.auth
    invite_code = auth.add_user_invite(username, invited_by_username="admin")
    auth.complete_invite(
        username=username,
        invite_code=invite_code,
        full_name="Member User",
        email=f"{username}@test.local",
        password=password,
    )
    return auth.find_user(username)["id"]


@pytest_asyncio.fixture
async def desktop_owner_clients(app, tmp_data_dir):
    """Two non-admin clients plus a registry holding one agent owned by alice.

    Yields ``(alice_client, bob_client, agent_handle)``. The agent's registry
    row carries alice's user_id, so bob is a legitimately authenticated caller
    who does not own the agent.
    """
    if not app.state.auth.is_configured():
        app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    alice_uid = _add_member_user(app, "alice", "alicepass1")
    bob_uid = _add_member_user(app, "bob", "bobspass11")

    registry = app.state.agent_registry
    if registry._db is None:
        await registry.init()
    await registry.register(
        framework="claude-code",
        display_name="alices-desk",
        handle="alices-desk",
        user_id=alice_uid,
    )

    app.state._startup_complete = True
    transport = ASGITransport(app=app)
    alice_token = app.state.auth.create_session(user_id=alice_uid, long_lived=True)
    bob_token = app.state.auth.create_session(user_id=bob_uid, long_lived=True)
    async with AsyncClient(
        transport=transport, base_url="http://test",
        cookies={"taos_session": alice_token}, event_hooks=csrf_event_hooks(),
    ) as alice_c:
        async with AsyncClient(
            transport=transport, base_url="http://test",
            cookies={"taos_session": bob_token}, event_hooks=csrf_event_hooks(),
        ) as bob_c:
            yield alice_c, bob_c, "alices-desk"
    await registry.close()


@pytest.mark.asyncio
class TestAgentDesktopAuthorization:
    """Finding 1: authenticate is not authorize."""

    async def test_non_owner_cannot_install(self, desktop_owner_clients, monkeypatch):
        _alice, bob, handle = desktop_owner_clients
        calls = []

        async def fake_exec(name, cmd, timeout=300):
            calls.append((name, list(cmd)))
            return (0, "OK")

        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        resp = await bob.post(f"/api/agents/{handle}/desktop/install")
        assert resp.status_code == 403
        assert calls == [], "a non-owner must not reach apt inside another agent's container"

    async def test_non_owner_cannot_start_or_read_the_vnc_password(
        self, desktop_owner_clients, monkeypatch
    ):
        alice, bob, handle = desktop_owner_clients

        async def fake_exec(name, cmd, timeout=300):
            cmd_str = " ".join(cmd)
            if "pgrep" in cmd_str:
                return (0, "RUNNING")
            return (0, "READY")

        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        assert (await alice.post(f"/api/agents/{handle}/desktop/install")).status_code == 200

        resp = await bob.post(f"/api/agents/{handle}/desktop/start")
        assert resp.status_code == 403
        assert "vnc_password" not in resp.json()

    async def test_non_owner_cannot_stop_or_read_status(self, desktop_owner_clients):
        _alice, bob, handle = desktop_owner_clients
        assert (await bob.post(f"/api/agents/{handle}/desktop/stop")).status_code == 403
        assert (await bob.get(f"/api/agents/{handle}/desktop/status")).status_code == 403

    async def test_owner_is_allowed(self, desktop_owner_clients, monkeypatch):
        alice, _bob, handle = desktop_owner_clients

        async def fake_exec(name, cmd, timeout=300):
            return (0, "READY")

        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)
        resp = await alice.post(f"/api/agents/{handle}/desktop/install")
        assert resp.status_code == 200
        assert resp.json()["state"] == "installed"

    async def test_unregistered_agent_name_is_admin_only(self, desktop_owner_clients):
        """No authoritative record means no owner to compare against: fail closed."""
        _alice, bob, _handle = desktop_owner_clients
        resp = await bob.get("/api/agents/some-other-agent/desktop/status")
        assert resp.status_code == 403

    async def test_agent_name_must_be_a_valid_container_slug(self, client, monkeypatch):
        """A path segment that is not a container slug never reaches incus exec."""
        calls = []

        async def fake_exec(name, cmd, timeout=300):
            calls.append((name, list(cmd)))
            return (0, "OK")

        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        resp = await client.post("/api/agents/foo;rm -rf ~/desktop/install")
        assert resp.status_code == 400
        assert calls == []


@pytest.mark.asyncio
class TestAgentDesktopInstallRetry:
    """Finding 2: a failed install must not be a permanent dead end."""

    async def test_install_retries_after_a_transient_failure(self, client, monkeypatch):
        attempts = []

        async def fake_exec(name, cmd, timeout=300):
            attempts.append(" ".join(cmd))
            if len(attempts) == 1:
                return (100, "E: Could not get lock /var/lib/dpkg/lock-frontend")
            return (0, "OK")

        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        first = await client.post("/api/agents/test-agent/desktop/install")
        assert first.status_code == 500

        second = await client.post("/api/agents/test-agent/desktop/install")
        assert second.status_code == 200, "a transient apt failure must be retryable"
        assert second.json()["state"] == "installed"
        assert len(attempts) == 2, "the retry must actually re-run apt"

    async def test_start_is_rejected_while_install_never_completed(self, client, monkeypatch):
        async def fake_exec(name, cmd, timeout=300):
            if "apt-get" in " ".join(cmd):
                return (100, "apt exploded")
            return (0, "READY")

        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        assert (await client.post("/api/agents/test-agent/desktop/install")).status_code == 500
        resp = await client.post("/api/agents/test-agent/desktop/start")
        assert resp.status_code == 409, "start must not run against a half-installed container"


@pytest.mark.asyncio
class TestAgentDesktopSerialization:
    """Finding 3: lifecycle operations are serialized per agent."""

    async def test_concurrent_installs_run_apt_once(self, client, monkeypatch):
        apt_calls = []
        in_flight = 0
        max_in_flight = 0

        async def fake_exec(name, cmd, timeout=300):
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            try:
                await asyncio.sleep(0.05)
                if "apt-get" in " ".join(cmd):
                    apt_calls.append(name)
                return (0, "OK")
            finally:
                in_flight -= 1

        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        results = await asyncio.gather(
            client.post("/api/agents/test-agent/desktop/install"),
            client.post("/api/agents/test-agent/desktop/install"),
        )
        assert [r.status_code for r in results] == [200, 200]
        assert len(apt_calls) == 1, "concurrent installs must not both run apt"
        assert max_in_flight == 1, "lifecycle execs for one agent must not overlap"

    async def test_stop_racing_start_leaves_a_consistent_state(self, client, monkeypatch):
        async def fake_exec(name, cmd, timeout=300):
            cmd_str = " ".join(cmd)
            await asyncio.sleep(0.02)
            if "pgrep" in cmd_str:
                return (0, "RUNNING")
            return (0, "READY")

        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)
        await client.post("/api/agents/test-agent/desktop/install")

        start, stop = await asyncio.gather(
            client.post("/api/agents/test-agent/desktop/start"),
            client.post("/api/agents/test-agent/desktop/stop"),
        )
        assert start.status_code == 200
        assert stop.status_code == 200
        final = (await client.get("/api/agents/test-agent/desktop/status")).json()
        # Whichever order the two took, the last writer owns the state: a stop
        # that ran after a start reads 'stopped', never 'running' with the
        # start's processes torn down underneath it.
        assert final["state"] in ("running", "stopped")
        if stop.json()["state"] == "stopped" and start.json().get("state") == "running":
            assert final["state"] == "stopped"


@pytest.mark.asyncio
class TestAgentDesktopStopFailure:
    """Finding 4: a stop that failed is not a stop."""

    async def test_failed_stop_does_not_report_stopped(self, client, monkeypatch):
        async def fake_exec(name, cmd, timeout=300):
            cmd_str = " ".join(cmd)
            if "pkill" in cmd_str:
                return (1, "Error: Instance is not running")
            if "pgrep" in cmd_str:
                return (0, "RUNNING")
            return (0, "READY")

        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        await client.post("/api/agents/test-agent/desktop/install")
        await client.post("/api/agents/test-agent/desktop/start")

        resp = await client.post("/api/agents/test-agent/desktop/stop")
        assert resp.status_code == 500, "a failed pkill must not be reported as a clean stop"
        assert resp.json().get("state") != "stopped"

    async def test_failed_stop_records_the_error_state(self, client, monkeypatch):
        async def fake_exec(name, cmd, timeout=300):
            cmd_str = " ".join(cmd)
            if "pkill" in cmd_str:
                return (255, "Error: exec timed out")
            if "pgrep" in cmd_str:
                return (0, "RUNNING")
            return (0, "READY")

        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        await client.post("/api/agents/test-agent/desktop/install")
        await client.post("/api/agents/test-agent/desktop/start")
        await client.post("/api/agents/test-agent/desktop/stop")

        status = await client.get("/api/agents/test-agent/desktop/status")
        assert status.json()["state"] == "error"
        assert status.json()["running"] is False


@pytest.mark.asyncio
class TestAgentDesktopStartReadiness:
    """The start probe must observe the VNC server, not a wrapper shell."""

    async def test_start_probe_checks_the_vnc_port(self, client, monkeypatch):
        start_cmds = []

        async def fake_exec(name, cmd, timeout=300):
            cmd_str = " ".join(cmd)
            if "x11vnc -display" in cmd_str:
                start_cmds.append(cmd_str)
            return (0, "READY")

        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)
        await client.post("/api/agents/test-agent/desktop/install")
        await client.post("/api/agents/test-agent/desktop/start")

        assert start_cmds, "start must launch x11vnc"
        assert "/dev/tcp/127.0.0.1/5900" in start_cmds[0], (
            "readiness must probe the listening VNC port, not just any x11vnc process"
        )

    async def test_password_is_never_interpolated_into_the_shell_script(
        self, client, monkeypatch
    ):
        captured = []

        async def fake_exec(name, cmd, timeout=300):
            captured.append(list(cmd))
            return (0, "READY")

        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)
        await client.post("/api/agents/test-agent/desktop/install")
        resp = await client.post("/api/agents/test-agent/desktop/start")
        password = resp.json()["vnc_password"]

        vncpasswd = [c for c in captured if "vncpasswd" in " ".join(c)]
        assert vncpasswd, "start must set a VNC password"
        script = vncpasswd[0][2]
        assert password not in script, (
            "the password must be passed as an argv element, not interpolated into bash -c"
        )
        assert password in vncpasswd[0][3:]


@pytest.mark.asyncio
class TestAgentDesktopRegistryDegradation:
    """A registry outage may take access away; it must never grant any."""

    async def test_registry_failure_refuses_non_admins(self, desktop_owner_clients, monkeypatch):
        _alice, bob, handle = desktop_owner_clients
        registry = bob._transport.app.state.agent_registry

        async def boom(*_a, **_kw):
            raise RuntimeError("AgentRegistryStore not initialised")

        monkeypatch.setattr(registry, "get_by_handle", boom)
        assert (await bob.get(f"/api/agents/{handle}/desktop/status")).status_code == 403

    async def test_registry_failure_leaves_admins_where_they_already_were(
        self, client, monkeypatch
    ):
        """An admin is authorised for every agent with or without the registry,
        so the degraded path grants nothing a healthy registry would not."""
        async def boom(*_a, **_kw):
            raise RuntimeError("AgentRegistryStore not initialised")

        registry = client._transport.app.state.agent_registry
        monkeypatch.setattr(registry, "get_by_handle", boom)
        resp = await client.get("/api/agents/test-agent/desktop/status")
        assert resp.status_code == 200
        assert resp.json()["state"] == "not_installed"


@pytest.mark.asyncio
class TestAgentDesktopNameLength:
    async def test_a_long_registry_handle_is_still_accepted(self, client, monkeypatch):
        """Handles the registry accepts at registration must not 400 here."""
        async def fake_exec(name, cmd, timeout=300):
            return (0, "OK")

        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)
        handle = "a" + "b" * 61  # 62 chars, within the project-slug convention
        resp = await client.post(f"/api/agents/{handle}/desktop/install")
        assert resp.status_code == 200


@pytest.mark.asyncio
class TestAgentDesktopInstallClearsStaleError:
    async def test_install_after_a_failed_start_reports_installed(self, client, monkeypatch):
        async def fake_exec(name, cmd, timeout=300):
            cmd_str = " ".join(cmd)
            if "apt-get" in cmd_str:
                return (0, "OK")
            if "x11vnc -display" in cmd_str:
                return (1, "TIMEOUT")
            return (0, "OK")

        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        assert (await client.post("/api/agents/test-agent/desktop/install")).status_code == 200
        assert (await client.post("/api/agents/test-agent/desktop/start")).status_code == 500

        again = await client.post("/api/agents/test-agent/desktop/install")
        assert again.status_code == 200
        assert again.json()["state"] == "installed", (
            "install must not answer 200 with another call's error state"
        )
