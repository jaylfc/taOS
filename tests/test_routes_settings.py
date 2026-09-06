import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestUpdateAlwaysRestarts:
    """Contract: /api/settings/update ALWAYS triggers a restart on success.

    No pref state can suppress the restart — the toggles were removed and the
    endpoint unconditionally calls _do_restart after a successful install.
    """

    @pytest.mark.asyncio
    async def test_update_always_restarts_after_successful_install(self, client):
        """A successful update must return status='restarting' regardless of prefs."""
        import types

        # Patch the entire subprocess machinery so no real git/pip is invoked,
        # and patch _do_restart so it doesn't actually touch the process.
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.communicate = AsyncMock(return_value=(b"Already up to date.\n", b""))

        restart_called = []

        async def _fake_restart(_app_state):
            restart_called.append(True)

        with (
            patch(
                "tinyagentos.routes.settings.asyncio.create_subprocess_exec",
                return_value=fake_proc,
            ),
            patch(
                "tinyagentos.routes.settings._run_capture",
                new=AsyncMock(return_value=(0, "ok")),
            ),
            patch(
                "tinyagentos.desktop_rebuild.rebuild_desktop_bundle_if_stale",
                new=AsyncMock(
                    return_value=MagicMock(rebuilt=False, success=True, message="current")
                ),
            ),
            patch(
                "tinyagentos.routes.system._do_restart",
                new=_fake_restart,
            ),
            patch("tinyagentos.restart_orchestrator.write_pending_restart"),
        ):
            resp = await client.post("/api/settings/update")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "restarting", (
            f"Expected status='restarting' but got {data!r}. "
            "Restart must always happen after a successful install."
        )


class TestSettingsRoutes:
    @pytest.mark.asyncio
    async def test_get_config(self, client):
        resp = await client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "yaml" in data

    @pytest.mark.asyncio
    async def test_get_storage(self, client):
        resp = await client.get("/api/settings/storage")
        assert resp.status_code == 200
        data = resp.json()
        assert "storage" in data

    @pytest.mark.asyncio
    async def test_save_platform_settings(self, client):
        resp = await client.put("/api/settings/platform", json={
            "poll_interval": 60,
            "retention_days": 14,
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "saved"

    @pytest.mark.asyncio
    async def test_llm_proxy_status(self, client):
        resp = await client.get("/api/settings/llm-proxy")
        assert resp.status_code == 200
        data = resp.json()
        assert "running" in data
        assert "port" in data

    @pytest.mark.asyncio
    async def test_get_notification_prefs(self, client):
        resp = await client.get("/api/settings/notification-prefs")
        assert resp.status_code == 200
        data = resp.json()
        assert "prefs" in data
        assert isinstance(data["prefs"], list)
        assert len(data["prefs"]) > 0
        assert "event_type" in data["prefs"][0]
        assert "muted" in data["prefs"][0]

    @pytest.mark.asyncio
    async def test_toggle_notification_pref(self, client):
        resp = await client.post(
            "/api/settings/notification-prefs/worker.join",
            json={"muted": True},
        )
        assert resp.status_code == 200
        assert resp.json()["muted"] is True
        # Verify it persisted
        resp = await client.get("/api/settings/notification-prefs")
        prefs = resp.json()["prefs"]
        worker_join = [p for p in prefs if p["event_type"] == "worker.join"]
        assert worker_join[0]["muted"] is True

    @pytest.mark.asyncio
    async def test_get_backup_schedule_default_off(self, client):
        resp = await client.get("/api/settings/backup-schedule")
        assert resp.status_code == 200
        assert resp.json()["frequency"] == "off"

    @pytest.mark.asyncio
    async def test_set_backup_schedule_daily(self, client):
        resp = await client.put("/api/settings/backup-schedule", json={"frequency": "daily"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "enabled"
        assert resp.json()["frequency"] == "daily"
        # Verify it persisted
        resp = await client.get("/api/settings/backup-schedule")
        assert resp.json()["frequency"] == "daily"

    @pytest.mark.asyncio
    async def test_disable_backup_schedule(self, client):
        # Enable first
        await client.put("/api/settings/backup-schedule", json={"frequency": "weekly"})
        # Then disable
        resp = await client.put("/api/settings/backup-schedule", json={"frequency": "off"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "disabled"
        resp = await client.get("/api/settings/backup-schedule")
        assert resp.json()["frequency"] == "off"

    @pytest.mark.asyncio
    async def test_get_container_runtime(self, client):
        resp = await client.get("/api/settings/container-runtime")
        assert resp.status_code == 200
        data = resp.json()
        assert "active" in data
        assert "detected" in data
        assert "configured" in data

    @pytest.mark.asyncio
    async def test_set_container_runtime(self, client):
        resp = await client.put(
            "/api/settings/container-runtime",
            content='{"runtime": "docker"}',
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

    @pytest.mark.asyncio
    async def test_set_apple_runtime(self, client):
        resp = await client.put(
            "/api/settings/container-runtime",
            content='{"runtime": "apple"}',
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

    @pytest.mark.asyncio
    async def test_set_invalid_runtime(self, client):
        resp = await client.put(
            "/api/settings/container-runtime",
            content='{"runtime": "invalid"}',
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_webhooks_crud(self, client):
        resp = await client.post("/api/settings/webhooks", json={
            "url": "https://example.com/hook",
            "type": "generic",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "added"
        resp = await client.get("/api/settings/webhooks")
        assert resp.status_code == 200
        assert len(resp.json()["webhooks"]) == 1
        resp = await client.delete("/api/settings/webhooks/0")
        assert resp.status_code == 200
        assert resp.json()["status"] == "removed"


class TestRunCaptureHelper:
    """Helper used by /api/settings/update to surface pip + smoke-test output.

    The previous implementation piped pip output to DEVNULL, so a failed
    install left no breadcrumbs and users grey-screened on next restart.
    These tests pin the helper's contract.
    """

    @pytest.mark.asyncio
    async def test_captures_stdout_and_returncode_on_success(self):
        from tinyagentos.routes.settings import _run_capture

        rc, out = await _run_capture(["sh", "-c", "echo hello-from-helper"])
        assert rc == 0
        assert "hello-from-helper" in out

    @pytest.mark.asyncio
    async def test_captures_stderr_and_returncode_on_failure(self):
        from tinyagentos.routes.settings import _run_capture

        rc, out = await _run_capture(["sh", "-c", "echo boom-on-stderr 1>&2; exit 7"])
        assert rc == 7
        assert "boom-on-stderr" in out


class TestRunCaptureTimeout:
    """The timeout fix for #327. Without it, pip / npm / smoke test could
    hang the /api/settings/update route forever on a slow mirror."""

    @pytest.mark.asyncio
    async def test_command_within_timeout_succeeds(self):
        from tinyagentos.routes.settings import _run_capture

        rc, out = await _run_capture(
            ["sh", "-c", "echo fast"], timeout=5.0,
        )
        assert rc == 0
        assert "fast" in out

    @pytest.mark.asyncio
    async def test_command_exceeding_timeout_returns_marker(self):
        from tinyagentos.routes.settings import _run_capture

        rc, out = await _run_capture(
            ["sh", "-c", "sleep 5"], timeout=0.5,
        )
        assert rc == -1
        assert "TIMEOUT" in out

    @pytest.mark.asyncio
    async def test_timeout_kills_subprocess_no_orphan(self):
        """After timeout, the subprocess must be terminated — not leaked."""
        import time

        from tinyagentos.routes.settings import _run_capture

        # Long sleep that would pin the subprocess open if not killed.
        start = time.monotonic()
        rc, _out = await _run_capture(
            ["sh", "-c", "sleep 30"], timeout=0.3,
        )
        elapsed = time.monotonic() - start
        assert rc == -1
        # Should return promptly after timeout, not after the full sleep.
        assert elapsed < 5.0, (
            f"_run_capture took {elapsed:.1f}s — subprocess wasn't killed cleanly"
        )


class TestUpdateCheckVersion:
    """update-check endpoint must return the installed version from tinyagentos.__version__."""

    @pytest.mark.asyncio
    async def test_current_version_matches_package(self, client):
        """current_version in the response must equal tinyagentos.__version__."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        import tinyagentos

        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.communicate = AsyncMock(return_value=(b"", b""))

        async def fake_rev_parse(*_args, **_kwargs):
            p = MagicMock()
            p.communicate = AsyncMock(return_value=(b"abc123\n", b""))
            return p

        with (
            patch(
                "tinyagentos.routes.settings.asyncio.create_subprocess_exec",
                side_effect=fake_rev_parse,
            ),
            patch(
                "tinyagentos.auto_update.remote_is_strictly_ahead",
                new=AsyncMock(return_value=False),
            ),
        ):
            resp = await client.get("/api/settings/update-check")

        assert resp.status_code == 200
        data = resp.json()
        assert data["current_version"] == tinyagentos.__version__, (
            f"Expected {tinyagentos.__version__!r} but got {data['current_version']!r}"
        )


class TestRebuildResultStructured:
    """Issue #327: rebuild_desktop_bundle_if_stale returns a structured
    RebuildResult so callers don't have to string-match the message field."""

    @pytest.mark.asyncio
    async def test_skip_returns_success_true(self, tmp_path):
        """When there's no desktop/, the rebuild is a successful no-op."""
        from tinyagentos.desktop_rebuild import rebuild_desktop_bundle_if_stale

        result = await rebuild_desktop_bundle_if_stale(tmp_path, force=True)
        assert result.rebuilt is False
        # No desktop dir → nothing to do; that's a successful skip, not a failure.
        # (force=True makes it skip the staleness check; it still skips because
        # there's no package.json.)
        assert result.success is True
        assert "package.json" in result.message or "current" in result.message.lower()

    @pytest.mark.asyncio
    async def test_result_fields_present(self, tmp_path):
        """Sanity-check the dataclass surface so future refactors don't drift."""
        from tinyagentos.desktop_rebuild import RebuildResult

        r = RebuildResult(rebuilt=True, success=False, message="x")
        assert r.rebuilt is True
        assert r.success is False
        assert r.message == "x"


class TestMemoryUrlSettings:
    """Contract: GET /api/settings/memory-url returns {url, is_local, reachable}
    and PUT probes the new target before returning (#1911)."""

    @pytest.mark.asyncio
    async def test_get_returns_default_url(self, client):
        """Default URL is localhost:7900, is_local=True, reachable depends on env."""
        resp = await client.get("/api/settings/memory-url")
        assert resp.status_code == 200
        data = resp.json()
        assert data["url"] == "http://localhost:7900"
        assert data["is_local"] is True
        assert isinstance(data["reachable"], bool)

    @pytest.mark.asyncio
    async def test_get_returns_is_local_true_for_localhost(self, client, app):
        """Setting the config URL to a loopback address must report is_local=True."""
        app.state.config.memory_url = "http://127.0.0.1:7900"
        resp = await client.get("/api/settings/memory-url")
        assert resp.status_code == 200
        data = resp.json()
        assert data["url"] == "http://127.0.0.1:7900"
        assert data["is_local"] is True

    @pytest.mark.asyncio
    async def test_get_returns_is_local_false_for_remote(self, client, app):
        """A remote URL must report is_local=False."""
        app.state.config.memory_url = "https://memory.example.com:7900"
        resp = await client.get("/api/settings/memory-url")
        assert resp.status_code == 200
        data = resp.json()
        assert data["url"] == "https://memory.example.com:7900"
        assert data["is_local"] is False

    @pytest.mark.asyncio
    async def test_put_sets_url_and_probes(self, client, app):
        """PUT persists the URL to config and returns probe result."""
        resp = await client.put(
            "/api/settings/memory-url",
            json={"url": "http://192.168.1.100:7900"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["url"] == "http://192.168.1.100:7900"
        assert data["is_local"] is False  # 192.168.x.x is private LAN, not local
        assert isinstance(data["reachable"], bool)
        # Verify it persisted on config
        assert app.state.config.memory_url == "http://192.168.1.100:7900"

    @pytest.mark.asyncio
    async def test_put_rejects_empty_url(self, client):
        """Empty URL must return 400."""
        resp = await client.put(
            "/api/settings/memory-url",
            json={"url": ""},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_put_rejects_non_http_scheme(self, client):
        """A non-http scheme must return 400."""
        resp = await client.put(
            "/api/settings/memory-url",
            json={"url": "ftp://localhost:7900"},
        )
        assert resp.status_code == 400
        assert "http" in resp.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_get_reflects_put(self, client, app):
        """After PUT, GET must return the new URL."""
        await client.put(
            "/api/settings/memory-url",
            json={"url": "https://taosmd.local:8443"},
        )
        resp = await client.get("/api/settings/memory-url")
        assert resp.status_code == 200
        data = resp.json()
        assert data["url"] == "https://taosmd.local:8443"
        assert data["is_local"] is False

    @pytest.mark.asyncio
    async def test_is_local_url_helper(self):
        """Unit-test _is_local_url directly."""
        from tinyagentos.routes.settings import _is_local_url

        assert _is_local_url("http://localhost:7900") is True
        assert _is_local_url("http://127.0.0.1:7900") is True
        assert _is_local_url("http://[::1]:7900") is True
        assert _is_local_url("http://10.0.0.1:7900") is False   # private LAN — not local machine
        assert _is_local_url("http://192.168.1.1:7900") is False  # private LAN — not local machine
        assert _is_local_url("https://example.com:7900") is False
        assert _is_local_url("http://8.8.8.8:7900") is False  # public IP

    @pytest.mark.asyncio
    async def test_put_probes_and_reports_unreachable(self, client):
        """When taosmd is not running, reachable must be False."""
        resp = await client.put(
            "/api/settings/memory-url",
            json={"url": "http://localhost:19999"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["reachable"] is False  # no taosmd on that port

    @pytest.mark.asyncio
    async def test_put_strips_trailing_slash(self, client, app):
        """Trailing slash on the URL should be stripped."""
        resp = await client.put(
            "/api/settings/memory-url",
            json={"url": "http://localhost:7900/"},
        )
        assert resp.status_code == 200
        assert resp.json()["url"] == "http://localhost:7900"
        assert app.state.config.memory_url == "http://localhost:7900"

    @pytest.mark.asyncio
    async def test_put_rejects_url_without_hostname(self, client):
        """A URL without a valid hostname must return 400 (hostname check)."""
        # path-only: http:///path  (hostname is empty string)
        resp = await client.put(
            "/api/settings/memory-url",
            json={"url": "http:///path"},
        )
        assert resp.status_code == 400

        # port-only: http://:7900  (hostname is empty, netloc non-empty — regression for netloc→hostname fix)
        resp = await client.put(
            "/api/settings/memory-url",
            json={"url": "http://:7900"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_put_response_includes_is_local_and_reachable(self, client):
        """PUT response must include is_local and reachable alongside url."""
        resp = await client.put(
            "/api/settings/memory-url",
            json={"url": "https://remote.example.com:7900"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "url" in data
        assert "is_local" in data
        assert "reachable" in data
        assert data["is_local"] is False  # remote hostname

    @pytest.mark.asyncio
    async def test_put_rejects_malformed_json(self, client):
        """Malformed JSON body must return 422 (Pydantic validation), not 500."""
        resp = await client.put(
            "/api/settings/memory-url",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422
