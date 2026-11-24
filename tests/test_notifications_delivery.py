"""Delivery-style, centre-visibility and Do-Not-Disturb behaviour (#206).

The mute slice of #206 landed earlier (per-event-type `muted` in
notification_prefs). These tests cover the rest of the per-source controls:
delivery style (banner / alert / none), show-in-Notification-Centre, and the
global Do Not Disturb schedule. Enforcement lives in NotificationStore.add /
_publish, which emit_event delegates to.
"""
import pytest
import pytest_asyncio

from tinyagentos.notifications import NotificationStore


@pytest_asyncio.fixture
async def store(tmp_path):
    s = NotificationStore(tmp_path / "notifications.db")
    await s.init()
    yield s
    await s.close()


@pytest_asyncio.fixture
async def published(store):
    """Capture every row fanned out to the live (toast) emitter."""
    rows = []

    async def _capture(row):
        rows.append(row)

    store.set_event_emitter(_capture)
    return rows


@pytest.mark.asyncio
class TestEventPrefShape:
    async def test_prefs_default_to_banner_and_centre_visible(self, store):
        prefs = await store.get_event_prefs()
        assert len(prefs) == len(NotificationStore.EVENT_TYPES)
        for pref in prefs:
            assert pref["muted"] is False
            assert pref["style"] == "banner"
            assert pref["show_centre"] is True

    async def test_set_event_pref_round_trip(self, store):
        await store.set_event_pref("worker.join", style="alert", show_centre=False)
        pref = next(
            p for p in await store.get_event_prefs() if p["event_type"] == "worker.join"
        )
        assert pref["style"] == "alert"
        assert pref["show_centre"] is False
        assert pref["muted"] is False

    async def test_set_event_muted_preserves_style_and_centre(self, store):
        # set_event_muted historically used INSERT OR REPLACE, which would
        # reset the newer columns to defaults; it must update in place.
        await store.set_event_pref("worker.join", style="none", show_centre=False)
        await store.set_event_muted("worker.join", True)
        pref = next(
            p for p in await store.get_event_prefs() if p["event_type"] == "worker.join"
        )
        assert pref["muted"] is True
        assert pref["style"] == "none"
        assert pref["show_centre"] is False

    async def test_set_event_pref_rejects_unknown_style(self, store):
        with pytest.raises(ValueError):
            await store.set_event_pref("worker.join", style="loud")


@pytest.mark.asyncio
class TestDeliveryStyle:
    async def test_banner_default_publishes_without_persistent(self, store, published):
        await store.emit_event("worker.join", "Worker joined", "w1 connected")
        items = await store.list(limit=10)
        assert len(items) == 1
        assert len(published) == 1
        assert not published[0].get("persistent")

    async def test_alert_publishes_persistent(self, store, published):
        await store.set_event_pref("backend.down", style="alert")
        await store.emit_event("backend.down", "Backend down", "llama.cpp stopped")
        items = await store.list(limit=10)
        assert len(items) == 1
        assert len(published) == 1
        assert published[0].get("persistent") is True

    async def test_none_stores_but_does_not_publish(self, store, published):
        await store.set_event_pref("task.claimed", style="none")
        await store.emit_event("task.claimed", "Task claimed", "t-1")
        items = await store.list(limit=10)
        assert len(items) == 1
        assert published == []

    async def test_muted_still_suppresses_everything(self, store, published):
        await store.set_event_pref(
            "worker.join", muted=True, style="alert", show_centre=True
        )
        await store.emit_event("worker.join", "Worker joined", "w1 connected")
        assert await store.list(limit=10) == []
        assert published == []


@pytest.mark.asyncio
class TestShowCentre:
    async def test_centre_off_publishes_but_does_not_store(self, store, published):
        await store.set_event_pref("app.installed", show_centre=False)
        result = await store.add(
            "App installed", "Store app ready", source="app.installed"
        )
        assert result is None  # nothing stored -> no row id
        assert await store.list(limit=10) == []
        assert len(published) == 1
        assert published[0]["title"] == "App installed"

    async def test_centre_off_alert_still_persistent(self, store, published):
        await store.set_event_pref("app.failed", style="alert", show_centre=False)
        await store.emit_event("app.failed", "Install failed", "boom")
        assert await store.list(limit=10) == []
        assert len(published) == 1
        assert published[0].get("persistent") is True

    async def test_centre_on_default_stores(self, store, published):
        result = await store.add("Hello", "world", source="app.installed")
        assert result is not None
        assert len(await store.list(limit=10)) == 1


@pytest.mark.asyncio
class TestDoNotDisturb:
    async def test_dnd_defaults_off(self, store):
        dnd = await store.get_dnd()
        assert dnd == {"enabled": False, "start": "22:00", "end": "07:00"}

    async def test_dnd_round_trip(self, store):
        await store.set_dnd(True, "21:30", "06:45")
        assert await store.get_dnd() == {
            "enabled": True,
            "start": "21:30",
            "end": "06:45",
        }

    async def test_dnd_rejects_bad_times(self, store):
        with pytest.raises(ValueError):
            await store.set_dnd(True, "25:00", "07:00")
        with pytest.raises(ValueError):
            await store.set_dnd(True, "22:00", "7am")

    async def test_dnd_window_same_day(self, store):
        await store.set_dnd(True, "09:00", "17:00")
        assert await store.dnd_active(now="08:59") is False
        assert await store.dnd_active(now="09:00") is True
        assert await store.dnd_active(now="12:00") is True
        assert await store.dnd_active(now="17:00") is False

    async def test_dnd_window_overnight_wraps(self, store):
        await store.set_dnd(True, "22:00", "07:00")
        assert await store.dnd_active(now="21:59") is False
        assert await store.dnd_active(now="22:00") is True
        assert await store.dnd_active(now="23:30") is True
        assert await store.dnd_active(now="06:59") is True
        assert await store.dnd_active(now="07:00") is False
        assert await store.dnd_active(now="12:00") is False

    async def test_dnd_disabled_never_active(self, store):
        await store.set_dnd(False, "00:00", "00:00")
        assert await store.dnd_active(now="12:00") is False

    async def test_dnd_equal_start_end_means_all_day(self, store):
        await store.set_dnd(True, "08:00", "08:00")
        assert await store.dnd_active(now="00:00") is True
        assert await store.dnd_active(now="15:00") is True

    async def test_dnd_suppresses_toast_but_stores(self, store, published):
        # Equal start/end = all day, so this is active at any wall-clock time.
        await store.set_dnd(True, "08:00", "08:00")
        await store.emit_event("worker.join", "Worker joined", "w1 connected")
        items = await store.list(limit=10)
        assert len(items) == 1
        assert published == []

    async def test_dnd_off_publishes_normally(self, store, published):
        await store.set_dnd(False, "08:00", "08:00")
        await store.emit_event("worker.join", "Worker joined", "w1 connected")
        assert len(published) == 1


@pytest.mark.asyncio
class TestReviewRegressions:
    async def test_centre_off_row_published_as_transient(self, store, published):
        # The Centre renders from the same client store the live events merge
        # into, so a toast-only row must be marked live-only at publish time.
        await store.set_event_pref("app.installed", show_centre=False)
        await store.add("App installed", "ready", source="app.installed")
        assert len(published) == 1
        assert published[0].get("transient") is True

    async def test_stored_row_published_without_transient(self, store, published):
        await store.add("Hello", "world", source="app.installed")
        assert len(published) == 1
        assert not published[0].get("transient")

    async def test_os_replace_publishes_persistent_for_alert_style(
        self, store, published
    ):
        await store.set_event_pref("backend.down", style="alert")
        rid, created = await store.add_os(
            user_id="u1",
            client_id="os-1",
            title="Backend down",
            message="stopped",
            level="error",
            source="backend.down",
            data={},
            digest="d1",
        )
        assert created is True
        rid2, created2 = await store.add_os(
            user_id="u1",
            client_id="os-2",
            title="Backend down",
            message="still stopped",
            level="error",
            source="backend.down",
            data={},
            replaces="os-1",
            digest="d2",
        )
        assert rid2 == rid
        assert created2 is False
        # The replacement publication is the last one; it must keep the
        # alert style's persistent flag, like the initial insert does.
        assert published[-1].get("persistent") is True

    async def test_concurrent_partial_updates_all_land(self, store):
        import asyncio

        await asyncio.gather(
            store.set_event_pref("worker.join", style="alert"),
            store.set_event_pref("worker.join", muted=True),
            store.set_event_pref("worker.join", show_centre=False),
        )
        pref = next(
            p for p in await store.get_event_prefs() if p["event_type"] == "worker.join"
        )
        assert pref["style"] == "alert"
        assert pref["muted"] is True
        assert pref["show_centre"] is False
