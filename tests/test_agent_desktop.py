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
