"""Tests for the anonymous install-count ping added in #778.

Covers:
- ping sends the right query params (v + platform)
- network errors are swallowed silently
- TAOS_NO_UPDATE_PING=1 skips the ping
- update_ping_enabled=False skips the ping
- notification dedupe: same commit notifies once, new commit notifies again
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_http_client(status=200, json_body=None, raise_exc=None):
    """Return a mock httpx.AsyncClient-like object."""
    client = AsyncMock()
    if raise_exc is not None:
        client.get = AsyncMock(side_effect=raise_exc)
    else:
        resp = MagicMock()
        resp.status_code = status
        resp.json = MagicMock(return_value=json_body or {})
        client.get = AsyncMock(return_value=resp)
    return client


def _make_settings(prefs=None):
    store = AsyncMock()
    store.get_preference = AsyncMock(return_value=prefs or {})
    store.save_preference = AsyncMock()
    return store


def _make_notif():
    notif = AsyncMock()
    notif.emit_event = AsyncMock()
    return notif


# ---------------------------------------------------------------------------
# send_version_ping
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ping_sends_version_and_platform(monkeypatch):
    """Ping must include v= and platform= query params."""
    from tinyagentos.auto_update import send_version_ping
    import tinyagentos

    monkeypatch.setenv("TAOS_UPDATE_CHECK_URL", "http://test.local/version-check")
    monkeypatch.setattr(tinyagentos, "__version__", "1.2.3-test")

    client = _make_http_client(status=200, json_body={"latest_version": "1.2.3"})
    await send_version_ping(client)

    client.get.assert_called_once()
    _, kwargs = client.get.call_args
    params = kwargs.get("params", {})
    assert params.get("v") == "1.2.3-test"
    assert "platform" in params
    plat = params["platform"]
    assert "-" in plat  # should be "<sys.platform>-<machine>"


@pytest.mark.asyncio
async def test_ping_includes_persistent_install_id(monkeypatch, tmp_path):
    """With a data_dir, the ping carries a stable random install id that
    persists across calls (the historical-count key)."""
    from tinyagentos.auto_update import send_version_ping
    import tinyagentos

    monkeypatch.setenv("TAOS_UPDATE_CHECK_URL", "http://test.local/version-check")
    monkeypatch.setattr(tinyagentos, "__version__", "1.2.3-test")

    c1 = _make_http_client(status=200, json_body={})
    await send_version_ping(c1, tmp_path)
    id1 = c1.get.call_args[1]["params"].get("id")
    assert id1 and len(id1) >= 16
    assert (tmp_path / ".install_id").exists()

    c2 = _make_http_client(status=200, json_body={})
    await send_version_ping(c2, tmp_path)
    id2 = c2.get.call_args[1]["params"].get("id")
    assert id2 == id1  # stable across calls


@pytest.mark.asyncio
async def test_ping_without_data_dir_sends_no_id(monkeypatch):
    """No data_dir means no id param (and the call still succeeds)."""
    from tinyagentos.auto_update import send_version_ping
    import tinyagentos
    monkeypatch.setenv("TAOS_UPDATE_CHECK_URL", "http://test.local/version-check")
    monkeypatch.setattr(tinyagentos, "__version__", "1.2.3-test")
    c = _make_http_client(status=200, json_body={})
    await send_version_ping(c, None)
    assert "id" not in c.get.call_args[1]["params"]


@pytest.mark.asyncio
async def test_ping_tolerates_connection_error():
    """A network error must not propagate -- silently dropped."""
    import httpx
    from tinyagentos.auto_update import send_version_ping

    client = _make_http_client(raise_exc=httpx.ConnectError("no route"))
    # Should not raise
    await send_version_ping(client)


@pytest.mark.asyncio
async def test_ping_tolerates_timeout():
    """A timeout must not propagate."""
    import httpx
    from tinyagentos.auto_update import send_version_ping

    client = _make_http_client(raise_exc=httpx.TimeoutException("timed out"))
    await send_version_ping(client)


@pytest.mark.asyncio
async def test_ping_tolerates_bad_json():
    """A non-JSON 200 response must not propagate."""
    from tinyagentos.auto_update import send_version_ping

    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(side_effect=ValueError("no json"))
    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)

    await send_version_ping(client)  # must not raise


# ---------------------------------------------------------------------------
# Opt-out: env var
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_env_opt_out_skips_ping(monkeypatch):
    """TAOS_NO_UPDATE_PING=1 must prevent the ping from being sent."""
    from tinyagentos.auto_update import AutoUpdateService

    monkeypatch.setenv("TAOS_NO_UPDATE_PING", "1")

    ping_called = []

    async def _fake_ping(client, data_dir=None):
        ping_called.append(True)

    with patch("tinyagentos.auto_update.send_version_ping", side_effect=_fake_ping):
        settings = _make_settings({"check_enabled": True, "update_ping_enabled": True})
        notif = _make_notif()

        app_state = MagicMock()
        app_state.http_client = _make_http_client()

        svc = AutoUpdateService(
            project_dir=None,
            notif_store=notif,
            settings_store=settings,
            app_state=app_state,
        )

        # Patch out the git/framework parts so _run_once only tests the ping path
        with patch.object(svc, "_probe_remote", AsyncMock(return_value=None)):
            with patch("tinyagentos.auto_update.poll_frameworks", AsyncMock()):
                with patch("tinyagentos.frameworks.FRAMEWORKS", {}):
                    await svc._run_once()

    assert ping_called == [], "ping should not fire when TAOS_NO_UPDATE_PING=1"


@pytest.mark.asyncio
async def test_pref_opt_out_skips_ping(monkeypatch):
    """update_ping_enabled=False in prefs must prevent the ping."""
    from tinyagentos.auto_update import AutoUpdateService

    monkeypatch.delenv("TAOS_NO_UPDATE_PING", raising=False)

    ping_called = []

    async def _fake_ping(client, data_dir=None):
        ping_called.append(True)

    with patch("tinyagentos.auto_update.send_version_ping", side_effect=_fake_ping):
        settings = _make_settings({"check_enabled": True, "update_ping_enabled": False})
        notif = _make_notif()

        app_state = MagicMock()
        app_state.http_client = _make_http_client()

        svc = AutoUpdateService(
            project_dir=None,
            notif_store=notif,
            settings_store=settings,
            app_state=app_state,
        )

        with patch.object(svc, "_probe_remote", AsyncMock(return_value=None)):
            with patch("tinyagentos.auto_update.poll_frameworks", AsyncMock()):
                with patch("tinyagentos.frameworks.FRAMEWORKS", {}):
                    await svc._run_once()

    assert ping_called == [], "ping should not fire when update_ping_enabled=False"


@pytest.mark.asyncio
async def test_ping_fires_when_both_opts_enabled(monkeypatch):
    """Ping fires when neither opt-out is active."""
    from tinyagentos.auto_update import AutoUpdateService

    monkeypatch.delenv("TAOS_NO_UPDATE_PING", raising=False)

    ping_called = []

    async def _fake_ping(client, data_dir=None):
        ping_called.append(True)

    with patch("tinyagentos.auto_update.send_version_ping", side_effect=_fake_ping):
        settings = _make_settings({"check_enabled": True, "update_ping_enabled": True})
        notif = _make_notif()

        app_state = MagicMock()
        app_state.http_client = _make_http_client()

        svc = AutoUpdateService(
            project_dir=None,
            notif_store=notif,
            settings_store=settings,
            app_state=app_state,
        )

        with patch.object(svc, "_probe_remote", AsyncMock(return_value=None)):
            with patch("tinyagentos.auto_update.poll_frameworks", AsyncMock()):
                with patch("tinyagentos.frameworks.FRAMEWORKS", {}):
                    await svc._run_once()

    assert len(ping_called) == 1, "ping should fire exactly once per cycle"


# ---------------------------------------------------------------------------
# Notification dedupe
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dedupe_same_commit_notifies_once(monkeypatch):
    """The same remote commit must trigger at most one notification."""
    from tinyagentos.auto_update import AutoUpdateService

    monkeypatch.delenv("TAOS_NO_UPDATE_PING", raising=False)

    REMOTE = "aabbccdd" * 5  # fake 40-char SHA
    CURRENT = "11111111" * 5

    notif_count = []

    async def _fake_notify(current, new_commit):
        notif_count.append(new_commit)

    # First call: last_notified_commit is None -> should notify
    settings = _make_settings({
        "check_enabled": True,
        "update_ping_enabled": False,
        "last_notified_commit": None,
    })
    notif = _make_notif()
    svc = AutoUpdateService(
        project_dir=None,
        notif_store=notif,
        settings_store=settings,
        app_state=None,
    )
    svc._notify_available = _fake_notify

    with patch.object(svc, "_probe_remote", AsyncMock(return_value=REMOTE)):
        with patch.object(svc, "_current_commit", AsyncMock(return_value=CURRENT)):
            with patch("tinyagentos.auto_update.remote_is_strictly_ahead", AsyncMock(return_value=True)):
                with patch("tinyagentos.auto_update.poll_frameworks", AsyncMock()):
                    with patch("tinyagentos.frameworks.FRAMEWORKS", {}):
                        await svc._run_once()

    assert len(notif_count) == 1

    # Second call: last_notified_commit is now REMOTE -> should NOT notify again
    settings2 = _make_settings({
        "check_enabled": True,
        "update_ping_enabled": False,
        "last_notified_commit": REMOTE,
    })
    svc2 = AutoUpdateService(
        project_dir=None,
        notif_store=_make_notif(),
        settings_store=settings2,
        app_state=None,
    )
    notif_count2 = []

    async def _fake_notify2(current, new_commit):
        notif_count2.append(new_commit)

    svc2._notify_available = _fake_notify2

    with patch.object(svc2, "_probe_remote", AsyncMock(return_value=REMOTE)):
        with patch.object(svc2, "_current_commit", AsyncMock(return_value=CURRENT)):
            with patch("tinyagentos.auto_update.remote_is_strictly_ahead", AsyncMock(return_value=True)):
                with patch("tinyagentos.auto_update.poll_frameworks", AsyncMock()):
                    with patch("tinyagentos.frameworks.FRAMEWORKS", {}):
                        await svc2._run_once()

    assert len(notif_count2) == 0, "should not re-notify for the same commit"


@pytest.mark.asyncio
async def test_dedupe_new_commit_notifies_again(monkeypatch):
    """A new remote commit (different SHA) must fire a new notification."""
    from tinyagentos.auto_update import AutoUpdateService

    monkeypatch.delenv("TAOS_NO_UPDATE_PING", raising=False)

    OLD_REMOTE = "aabbccdd" * 5
    NEW_REMOTE = "eeff0011" * 5
    CURRENT = "11111111" * 5

    notif_count = []

    settings = _make_settings({
        "check_enabled": True,
        "update_ping_enabled": False,
        "last_notified_commit": OLD_REMOTE,  # already notified for OLD
    })
    notif = _make_notif()
    svc = AutoUpdateService(
        project_dir=None,
        notif_store=notif,
        settings_store=settings,
        app_state=None,
    )

    async def _fake_notify(current, new_commit):
        notif_count.append(new_commit)

    svc._notify_available = _fake_notify

    with patch.object(svc, "_probe_remote", AsyncMock(return_value=NEW_REMOTE)):
        with patch.object(svc, "_current_commit", AsyncMock(return_value=CURRENT)):
            with patch("tinyagentos.auto_update.remote_is_strictly_ahead", AsyncMock(return_value=True)):
                with patch("tinyagentos.auto_update.poll_frameworks", AsyncMock()):
                    with patch("tinyagentos.frameworks.FRAMEWORKS", {}):
                        await svc._run_once()

    assert len(notif_count) == 1
    assert notif_count[0] == NEW_REMOTE


# ---------------------------------------------------------------------------
# _ping_enabled_by_env helper
# ---------------------------------------------------------------------------

def test_ping_enabled_by_env_true(monkeypatch):
    from tinyagentos.auto_update import _ping_enabled_by_env
    monkeypatch.delenv("TAOS_NO_UPDATE_PING", raising=False)
    assert _ping_enabled_by_env() is True


def test_ping_enabled_by_env_false_1(monkeypatch):
    from tinyagentos.auto_update import _ping_enabled_by_env
    monkeypatch.setenv("TAOS_NO_UPDATE_PING", "1")
    assert _ping_enabled_by_env() is False


def test_ping_enabled_by_env_false_true(monkeypatch):
    from tinyagentos.auto_update import _ping_enabled_by_env
    monkeypatch.setenv("TAOS_NO_UPDATE_PING", "true")
    assert _ping_enabled_by_env() is False
