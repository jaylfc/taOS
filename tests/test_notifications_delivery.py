"""Delivery-style, centre-visibility and Do-Not-Disturb behaviour (#206).

The mute slice of #206 landed earlier (per-event-type `muted` in
notification_prefs). These tests cover the rest of the per-source controls:
delivery style (banner / alert / none), show-in-Notification-Centre, and Do
Not Disturb with a schedule. Delivery settings are PERSONAL: prefs and DND
are keyed by user, enforced for a notification's recipient at insert /
publish time, and — for broadcast rows, which are shared — per reader in
list(). DND windows are evaluated in the owner's own timezone.
"""
from datetime import datetime
from datetime import timezone as dt_timezone

import pytest
import pytest_asyncio

from tinyagentos.notifications import NotificationStore

U1 = "u1"
U2 = "u2"


def at(hour, minute=0):
    """A fixed UTC instant on 2026-01-15 (a Thursday) for DND window tests."""
    return datetime(2026, 1, 15, hour, minute, tzinfo=dt_timezone.utc)


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
        prefs = await store.get_event_prefs(U1)
        assert len(prefs) == len(NotificationStore.EVENT_TYPES)
        for pref in prefs:
            assert pref["muted"] is False
            assert pref["style"] == "banner"
            assert pref["show_centre"] is True

    async def test_set_event_pref_round_trip(self, store):
        await store.set_event_pref(U1, "worker.join", style="alert", show_centre=False)
        pref = next(
            p for p in await store.get_event_prefs(U1) if p["event_type"] == "worker.join"
        )
        assert pref["style"] == "alert"
        assert pref["show_centre"] is False
        assert pref["muted"] is False

    async def test_set_event_muted_preserves_style_and_centre(self, store):
        # set_event_muted historically used INSERT OR REPLACE, which would
        # reset the newer columns to defaults; it must update in place.
        await store.set_event_pref(U1, "worker.join", style="none", show_centre=False)
        await store.set_event_muted(U1, "worker.join", True)
        pref = next(
            p for p in await store.get_event_prefs(U1) if p["event_type"] == "worker.join"
        )
        assert pref["muted"] is True
        assert pref["style"] == "none"
        assert pref["show_centre"] is False

    async def test_set_event_pref_rejects_unknown_style(self, store):
        with pytest.raises(ValueError):
            await store.set_event_pref(U1, "worker.join", style="loud")


@pytest.mark.asyncio
class TestDeliveryStyle:
    async def test_banner_default_publishes_without_persistent(self, store, published):
        await store.emit_event("worker.join", "Worker joined", "w1 connected", user_id=U1)
        items = await store.list(limit=10)
        assert len(items) == 1
        assert len(published) == 1
        assert not published[0].get("persistent")

    async def test_alert_publishes_persistent(self, store, published):
        await store.set_event_pref(U1, "backend.down", style="alert")
        await store.emit_event(
            "backend.down", "Backend down", "llama.cpp stopped", user_id=U1
        )
        items = await store.list(limit=10)
        assert len(items) == 1
        assert len(published) == 1
        assert published[0].get("persistent") is True

    async def test_none_stores_but_does_not_publish(self, store, published):
        await store.set_event_pref(U1, "task.claimed", style="none")
        await store.emit_event("task.claimed", "Task claimed", "t-1", user_id=U1)
        items = await store.list(limit=10)
        assert len(items) == 1
        assert published == []

    async def test_muted_still_suppresses_everything(self, store, published):
        await store.set_event_pref(
            U1, "worker.join", muted=True, style="alert", show_centre=True
        )
        await store.emit_event("worker.join", "Worker joined", "w1 connected", user_id=U1)
        assert await store.list(limit=10) == []
        assert published == []


@pytest.mark.asyncio
class TestShowCentre:
    async def test_centre_off_stores_but_hides_from_centre(self, store, published):
        # Jay's ruling on #206: "Show in Centre off" keeps the notification
        # -- it is stored, just filtered out of the Centre view, so nothing
        # is lost. The live toast still publishes, with the real row id.
        await store.set_event_pref(U1, "app.installed", show_centre=False)
        result = await store.add(
            "App installed", "Store app ready", source="app.installed", user_id=U1
        )
        assert result is not None  # stored: a real row id
        # Kept in the store (the system/history view sees it)...
        assert [i["id"] for i in await store.list(limit=10)] == [result]
        # ...but filtered out of the owner's Centre listing.
        assert await store.list(limit=10, user_id=U1) == []
        # Another user is unaffected by U1's pref.
        assert len(await store.list(limit=10, user_id=U2)) == 0  # not theirs
        assert len(published) == 1
        assert published[0]["title"] == "App installed"
        assert published[0]["id"] == result
        assert not published[0].get("transient")

    async def test_centre_off_os_notification_still_stores(self, store):
        # OS-originated rows always stored (their idempotency record commits
        # with the row); a centre-off pref marks the row hidden, it does not
        # drop it.
        await store.set_event_pref(U1, "backend.down", show_centre=False)
        rid, created = await store.add_os(
            user_id=U1,
            client_id="os-centre-off",
            title="Backend down",
            message="stopped",
            level="error",
            source="backend.down",
            data={},
            digest="d-centre-off",
        )
        assert created is True
        assert rid is not None
        assert await store.list(limit=10, user_id=U1) == []
        assert len(await store.list(limit=10)) == 1

    async def test_centre_off_alert_still_persistent(self, store, published):
        await store.set_event_pref(U1, "app.failed", style="alert", show_centre=False)
        await store.emit_event("app.failed", "Install failed", "boom", user_id=U1)
        # Stored (system view) but hidden from the owner's Centre.
        assert len(await store.list(limit=10)) == 1
        assert await store.list(limit=10, user_id=U1) == []
        assert len(published) == 1
        assert published[0].get("persistent") is True

    async def test_centre_on_default_stores(self, store, published):
        result = await store.add(
            "Hello", "world", source="app.installed", user_id=U1
        )
        assert result is not None
        assert len(await store.list(limit=10)) == 1


@pytest.mark.asyncio
class TestDoNotDisturb:
    async def test_dnd_defaults_to_disabled(self, store):
        dnd = await store.get_dnd(U1)
        assert dnd == {
            "enabled": False,
            "start": "22:00",
            "end": "07:00",
            "timezone": "UTC",
        }

    async def test_dnd_round_trip(self, store):
        await store.set_dnd(U1, enabled=True, start="21:30", end="06:45")
        assert await store.get_dnd(U1) == {
            "enabled": True,
            "start": "21:30",
            "end": "06:45",
            "timezone": "UTC",
        }

    async def test_dnd_rejects_invalid_times(self, store):
        with pytest.raises(ValueError):
            await store.set_dnd(U1, enabled=True, start="25:00", end="07:00")
        with pytest.raises(ValueError):
            await store.set_dnd(U1, enabled=True, start="22:00", end="7am")

    async def test_dnd_rejects_unknown_timezone(self, store):
        with pytest.raises(ValueError):
            await store.set_dnd(
                U1, enabled=True, start="22:00", end="07:00", timezone="Mars/Olympus"
            )

    async def test_dnd_same_day_window(self, store):
        await store.set_dnd(U1, enabled=True, start="09:00", end="17:00")
        assert await store.dnd_active(U1, now=at(8, 59)) is False
        assert await store.dnd_active(U1, now=at(9, 0)) is True
        assert await store.dnd_active(U1, now=at(12, 0)) is True
        assert await store.dnd_active(U1, now=at(17, 0)) is False

    async def test_dnd_wrapping_window(self, store):
        await store.set_dnd(U1, enabled=True, start="22:00", end="07:00")
        assert await store.dnd_active(U1, now=at(21, 59)) is False
        assert await store.dnd_active(U1, now=at(22, 0)) is True
        assert await store.dnd_active(U1, now=at(23, 30)) is True
        assert await store.dnd_active(U1, now=at(6, 59)) is True
        assert await store.dnd_active(U1, now=at(7, 0)) is False
        assert await store.dnd_active(U1, now=at(12, 0)) is False

    async def test_dnd_disabled_never_active(self, store):
        await store.set_dnd(U1, enabled=False, start="00:00", end="00:00")
        assert await store.dnd_active(U1, now=at(12, 0)) is False

    async def test_dnd_all_day_when_start_equals_end(self, store):
        await store.set_dnd(U1, enabled=True, start="08:00", end="08:00")
        assert await store.dnd_active(U1, now=at(0, 0)) is True
        assert await store.dnd_active(U1, now=at(15, 0)) is True

    async def test_dnd_blocks_live_delivery_but_stores(self, store, published):
        await store.set_dnd(U1, enabled=True, start="08:00", end="08:00")
        await store.emit_event("worker.join", "Worker joined", "w1", user_id=U1)
        assert len(await store.list(limit=10)) == 1
        assert published == []

    async def test_dnd_off_delivers_live(self, store, published):
        await store.set_dnd(U1, enabled=False, start="08:00", end="08:00")
        await store.emit_event("worker.join", "Worker joined", "w1", user_id=U1)
        assert len(published) == 1


@pytest.mark.asyncio
class TestDndTimezone:
    """The window is read in the owner's timezone, not server time."""

    async def test_window_evaluated_in_owner_timezone(self, store):
        # 2026-06-15 10:30 UTC is 06:30 in New York (EDT): inside a
        # 22:00-07:00 window there, outside it in UTC.
        instant = datetime(2026, 6, 15, 10, 30, tzinfo=dt_timezone.utc)
        await store.set_dnd(
            U1, enabled=True, start="22:00", end="07:00", timezone="America/New_York"
        )
        await store.set_dnd(U2, enabled=True, start="22:00", end="07:00", timezone="UTC")
        assert await store.dnd_active(U1, now=instant) is True
        assert await store.dnd_active(U2, now=instant) is False


@pytest.mark.asyncio
class TestPerUserIsolation:
    """One person's settings never affect another person's delivery."""

    async def test_mute_applies_only_to_the_owner(self, store, published):
        await store.set_event_muted(U1, "worker.join", True)
        await store.emit_event("worker.join", "Joined", "for u1", user_id=U1)
        await store.emit_event("worker.join", "Joined", "for u2", user_id=U2)
        items = await store.list(limit=10)
        assert [i["message"] for i in items] == ["for u2"]
        assert len(published) == 1

    async def test_style_applies_only_to_the_owner(self, store, published):
        await store.set_event_pref(U1, "backend.down", style="none")
        await store.emit_event("backend.down", "Down", "u1 copy", user_id=U1)
        await store.emit_event("backend.down", "Down", "u2 copy", user_id=U2)
        assert len(await store.list(limit=10)) == 2  # both stored
        assert len(published) == 1  # only u2's fanned out

    async def test_dnd_applies_only_to_the_owner(self, store, published):
        await store.set_dnd(U1, enabled=True, start="08:00", end="08:00")
        await store.emit_event("worker.join", "Joined", "u1", user_id=U1)
        await store.emit_event("worker.join", "Joined", "u2", user_id=U2)
        assert len(await store.list(limit=10)) == 2
        assert len(published) == 1
        assert published[0]["message"] == "u2"

    async def test_broadcast_hidden_from_muting_reader_only(self, store):
        # Broadcast rows are shared: a reader's own mute hides the row from
        # their list, and from nobody else's.
        await store.add("News", "for everyone", source="worker.join")
        await store.set_event_muted(U1, "worker.join", True)
        assert await store.list(limit=10, user_id=U1) == []
        assert len(await store.list(limit=10, user_id=U2)) == 1

    async def test_broadcast_hidden_from_centre_off_reader_only(self, store):
        await store.add("News", "for everyone", source="worker.join")
        await store.set_event_pref(U1, "worker.join", show_centre=False)
        assert await store.list(limit=10, user_id=U1) == []
        assert len(await store.list(limit=10, user_id=U2)) == 1

    async def test_prefs_are_independent_records(self, store):
        await store.set_event_pref(U1, "worker.join", style="alert", muted=True)
        pref_u2 = next(
            p for p in await store.get_event_prefs(U2) if p["event_type"] == "worker.join"
        )
        assert pref_u2 == {
            "event_type": "worker.join",
            "muted": False,
            "style": "banner",
            "show_centre": True,
        }


@pytest.mark.asyncio
class TestReviewRegressions:
    async def test_centre_off_row_published_with_its_real_id(self, store, published):
        # Centre-off rows are stored now (Jay's ruling), so the live event
        # carries the real row id and is NOT marked transient: when the
        # client's next poll returns the row, the merge keys line up.
        await store.set_event_pref(U1, "app.installed", show_centre=False)
        rid = await store.add("App installed", "ready", source="app.installed", user_id=U1)
        assert rid is not None
        assert len(published) == 1
        assert published[0]["id"] == rid
        assert not published[0].get("transient")

    async def test_stored_row_published_without_transient(self, store, published):
        await store.add("Hello", "world", source="app.installed", user_id=U1)
        assert len(published) == 1
        assert not published[0].get("transient")

    async def test_os_replace_publishes_persistent_for_alert_style(
        self, store, published
    ):
        await store.set_event_pref(U1, "backend.down", style="alert")
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
            store.set_event_pref(U1, "worker.join", style="alert"),
            store.set_event_pref(U1, "worker.join", muted=True),
            store.set_event_pref(U1, "worker.join", show_centre=False),
        )
        pref = next(
            p for p in await store.get_event_prefs(U1) if p["event_type"] == "worker.join"
        )
        assert pref["style"] == "alert"
        assert pref["muted"] is True
        assert pref["show_centre"] is False


@pytest.mark.asyncio
class TestBroadcastLiveDelivery:
    """Broadcast rows get their per-recipient live check at fan-out (CR).

    Personal rows are decided in add() against the recipient's prefs.
    A broadcast has no recipient at insert time, so the fan-out points
    that know the recipients (SSE stream, web-push) consult
    live_delivery_allowed() per person. These tests pin that decision.
    """

    async def test_default_allows_live_delivery(self, store):
        assert await store.live_delivery_allowed(U1, "worker.join") is True

    async def test_mute_blocks_live_delivery(self, store):
        await store.set_event_muted(U1, "worker.join", True)
        assert await store.live_delivery_allowed(U1, "worker.join") is False
        assert await store.live_delivery_allowed(U2, "worker.join") is True

    async def test_style_none_blocks_but_alert_allows(self, store):
        await store.set_event_pref(U1, "worker.join", style="none")
        assert await store.live_delivery_allowed(U1, "worker.join") is False
        await store.set_event_pref(U1, "worker.join", style="alert")
        assert await store.live_delivery_allowed(U1, "worker.join") is True

    async def test_dnd_blocks_live_delivery(self, store):
        await store.set_dnd(U1, enabled=True, start="08:00", end="08:00")
        assert await store.live_delivery_allowed(U1, "worker.join") is False

    async def test_centre_off_does_not_block_the_toast(self, store):
        # Centre visibility governs the Centre, not the live toast (#206).
        await store.set_event_pref(U1, "worker.join", show_centre=False)
        assert await store.live_delivery_allowed(U1, "worker.join") is True


@pytest.mark.asyncio
class TestListAndCountFiltering:
    async def test_hidden_broadcasts_do_not_starve_the_limit(self, store):
        # The newest `limit` rows are broadcasts from a source this reader
        # hid; an older visible row must still be returned (the hidden
        # filter applies in SQL, before LIMIT -- CodeRabbit, PR #4507).
        await store.add(
            "Visible", "older", source="task.closed", user_id=U1, timestamp=1000
        )
        for i in range(3):
            await store.add(
                "Hidden", f"b{i}", source="worker.join", timestamp=2000 + i
            )
        await store.set_event_pref(U1, "worker.join", show_centre=False)
        items = await store.list(limit=3, user_id=U1)
        assert [i["title"] for i in items] == ["Visible"]
        # A reader who did not hide the source still sees the broadcasts.
        titles_u2 = [i["title"] for i in await store.list(limit=3, user_id=U2)]
        assert titles_u2 == ["Hidden", "Hidden", "Hidden"]

    async def test_unread_count_excludes_hidden_broadcasts(self, store):
        # unread_count() applies the same hidden-source rule as list():
        # broadcast rows from a source the reader muted/hid are not
        # counted; the reader's own rows always are (CodeRabbit, PR #4507).
        await store.add("B1", "x", source="worker.join")
        await store.add("Own", "x", source="worker.join", user_id=U1)
        await store.set_event_muted(U1, "worker.join", True)
        assert await store.unread_count(U1) == 1  # own row only
        assert await store.unread_count(U2) == 1  # the broadcast

    async def test_unread_count_excludes_centre_off_own_rows(self, store):
        # Badge coherence with the J2 design: a stored-but-Centre-hidden
        # row is not counted as unread for the Centre badge.
        await store.set_event_pref(U1, "app.installed", show_centre=False)
        await store.add("Hidden own", "x", source="app.installed", user_id=U1)
        await store.add("Visible own", "x", source="task.closed", user_id=U1)
        assert await store.unread_count(U1) == 1


@pytest.mark.asyncio
class TestPrefsUpgrade:
    """Upgrade from a pre-per-user notification_prefs table (Jay's ask).

    The shipped table keyed prefs by event_type alone. Opening a store on
    such a database must rebuild it with the composite (user_id, event_type)
    key, preserving rows under the legacy '' owner — where they apply to
    nobody, since a global pref has no person to belong to.
    """

    async def _store_on(self, tmp_path, ddl, rows):
        import sqlite3

        path = tmp_path / "upgrade.db"
        conn = sqlite3.connect(path)
        conn.execute(ddl)
        conn.executemany(
            "INSERT INTO notification_prefs VALUES (%s)" % ", ".join("?" * len(rows[0])),
            rows,
        )
        conn.commit()
        conn.close()
        s = NotificationStore(path)
        await s.init()
        return s

    async def test_upgrade_from_mute_only_table(self, tmp_path):
        s = await self._store_on(
            tmp_path,
            "CREATE TABLE notification_prefs ("
            "event_type TEXT PRIMARY KEY, muted INTEGER NOT NULL DEFAULT 0)",
            [("worker.join", 1)],
        )
        try:
            async with s._db.execute("PRAGMA table_info(notification_prefs)") as cur:
                cols = {row[1] for row in await cur.fetchall()}
            assert "user_id" in cols
            legacy = next(
                p for p in await s.get_event_prefs("") if p["event_type"] == "worker.join"
            )
            assert legacy["muted"] is True
            assert legacy["style"] == "banner"
            assert legacy["show_centre"] is True
            # ...but the legacy global mute does not leak onto a person.
            person = next(
                p for p in await s.get_event_prefs(U1) if p["event_type"] == "worker.join"
            )
            assert person["muted"] is False
        finally:
            await s.close()

    async def test_upgrade_reruns_after_an_interrupted_migration(self, tmp_path):
        # The migration CREATEs notification_prefs_new in autocommit mode;
        # an interruption after that CREATE leaves the staging table on
        # disk. The next start must drop the leftover and rerun, not fail
        # with "table already exists" (CodeRabbit, PR #4507).
        import sqlite3

        path = tmp_path / "interrupted.db"
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE notification_prefs ("
            "event_type TEXT PRIMARY KEY, muted INTEGER NOT NULL DEFAULT 0)"
        )
        conn.execute("INSERT INTO notification_prefs VALUES ('worker.join', 1)")
        conn.execute(
            "CREATE TABLE notification_prefs_new ("
            "user_id TEXT NOT NULL DEFAULT '', event_type TEXT NOT NULL, "
            "muted INTEGER NOT NULL DEFAULT 0, "
            "style TEXT NOT NULL DEFAULT 'banner', "
            "show_centre INTEGER NOT NULL DEFAULT 1, "
            "PRIMARY KEY (user_id, event_type))"
        )
        conn.commit()
        conn.close()
        s = NotificationStore(path)
        await s.init()  # must not raise
        try:
            legacy = next(
                p for p in await s.get_event_prefs("") if p["event_type"] == "worker.join"
            )
            assert legacy["muted"] is True
        finally:
            await s.close()

    async def test_upgrade_from_delivery_columns_table(self, tmp_path):
        s = await self._store_on(
            tmp_path,
            "CREATE TABLE notification_prefs ("
            "event_type TEXT PRIMARY KEY, muted INTEGER NOT NULL DEFAULT 0, "
            "style TEXT NOT NULL DEFAULT 'banner', "
            "show_centre INTEGER NOT NULL DEFAULT 1)",
            [("worker.join", 0, "none", 0)],
        )
        try:
            legacy = next(
                p for p in await s.get_event_prefs("") if p["event_type"] == "worker.join"
            )
            assert legacy["style"] == "none"
            assert legacy["show_centre"] is False
        finally:
            await s.close()


    async def test_own_row_survives_a_later_mute(self, store):
        # Delivery prefs govern delivery, not history: a row stored for a
        # user before they muted the source stays in their list. (Broadcast
        # rows are the exception — shared, so filtered per reader above.)
        await store.add(
            "Old", "stored before the mute", source="worker.join", user_id=U1
        )
        await store.set_event_muted(U1, "worker.join", True)
        items = await store.list(limit=10, user_id=U1)
        assert [i["title"] for i in items] == ["Old"]
