from __future__ import annotations

import asyncio
import json
import logging
import time

from tinyagentos.base_store import BaseStore

# Canonical notification level vocabulary — single source of truth.
# The notify_user tool schema, the POST /api/notifications route, and the
# desktop toast/archive renderers all consume this set.
VALID_LEVELS = frozenset({"info", "success", "warning", "error"})

# Per-source delivery styles (#206): "banner" toasts and auto-dismisses,
# "alert" toasts and stays until dismissed (published with persistent=True),
# "none" is stored in the Notification Centre with no live fan-out.
DELIVERY_STYLES = ("banner", "alert", "none")
DEFAULT_DND = {"enabled": False, "start": "22:00", "end": "07:00"}


def _valid_hhmm(value) -> bool:
    """True for a zero-padded 24h "HH:MM" string."""
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        return False
    hh, mm = value[:2], value[3:]
    return hh.isdigit() and mm.isdigit() and int(hh) <= 23 and int(mm) <= 59


logger = logging.getLogger(__name__)


class OsIdConflict(Exception):
    """An OS notification id was reused with different content (a replay must be identical)."""

# Sources that should be pinned at the top of the notifications feed.
# These are grant/consent requests that must stay visible until acted upon.
PINNED_SOURCES = ("auth_requests", "agent_scope_requests")

NOTIF_SCHEMA = """
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    level TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    read INTEGER NOT NULL DEFAULT 0,
    source TEXT,
    archived INTEGER NOT NULL DEFAULT 0,
    data TEXT,
    user_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_notif_ts ON notifications(timestamp DESC);
CREATE TABLE IF NOT EXISTS notification_prefs (
    event_type TEXT PRIMARY KEY,
    muted INTEGER NOT NULL DEFAULT 0,
    style TEXT NOT NULL DEFAULT 'banner',
    show_centre INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS notification_dnd (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL DEFAULT 0,
    start TEXT NOT NULL DEFAULT '22:00',
    end TEXT NOT NULL DEFAULT '07:00'
);
CREATE TABLE IF NOT EXISTS notification_user_state (
    notification_id INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    read_at INTEGER,
    archived_at INTEGER,
    PRIMARY KEY (notification_id, user_id)
);
CREATE TABLE IF NOT EXISTS os_notification_ids (
    user_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    notification_id INTEGER NOT NULL,
    digest TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (user_id, client_id)
);
"""


def _serialize_row(r) -> dict:
    """Map a notifications row to the API dict, parsing the JSON `data` payload.

    Uses named column access via dict(zip(COLS, r)) so a schema column reorder
    cannot silently corrupt the mapping. The SELECT queries must select exactly
    these columns in this order.
    """
    COLS = ("id", "timestamp", "level", "title", "message", "read", "source", "data", "user_id")
    row = dict(zip(COLS, r))
    data = None
    if row["data"]:
        try:
            data = json.loads(row["data"])
        except (ValueError, TypeError):
            data = None
    return {
        "id": row["id"], "timestamp": row["timestamp"], "level": row["level"],
        "title": row["title"], "message": row["message"], "read": bool(row["read"]),
        "source": row["source"], "data": data, "user_id": row["user_id"],
    }


class NotificationStore(BaseStore):
    SCHEMA = NOTIF_SCHEMA

    EVENT_TYPES = [
        "worker.join", "worker.online", "worker.leave", "backend.up", "backend.down",
        "training.complete", "training.failed", "app.installed", "app.failed",
        "disk_quota", "task.claimed", "task.closed",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._webhook_notifier = None
        self._event_emitter = None  # async callable: (row: dict) -> None
        self._push_sender = None  # async callable: (row: dict) -> None
        self._push_tasks: set = set()  # strong refs so fire-and-forget tasks aren't GC'd
        self._os_lock = asyncio.Lock()  # serialises add_os (idempotency check + insert)
        self._transient_seq = 0  # synthetic negative ids for toast-only (unstored) rows

    def set_webhook_notifier(self, notifier) -> None:
        """Attach a WebhookNotifier to fire on every notification."""
        self._webhook_notifier = notifier

    def set_event_emitter(self, emitter) -> None:
        """Attach an async callable called with each new notification row dict.

        The emitter receives the same shape as the GET /api/notifications items
        (id, timestamp, level, title, message, read, source, data). Used to
        push new notifications to SSE subscribers without polling. Best-effort:
        a failing emitter never breaks add().
        """
        self._event_emitter = emitter

    def set_push_sender(self, sender) -> None:
        """Attach an async callable called with each new notification row dict.

        The sender delivers the notification as an OS-level PWA web-push so a
        user gets the banner even when the taOS app is closed. It receives the
        same row shape as the event emitter. Strictly best-effort and off the
        event loop: it is dispatched as a background task, and any error is
        swallowed so a push failure or a missing VAPID key never breaks add().
        """
        self._push_sender = sender

    async def _dispatch_push(self, row: dict) -> None:
        """Run the web-push sender, isolating every failure from add()."""
        try:
            await self._push_sender(row)
        except Exception:
            logger.warning("NotificationStore: web-push sender failed", exc_info=True)

    async def _post_init(self) -> None:
        # `archived` was added after the initial notifications ship. Guarded
        # ALTER so existing databases gain it without a destructive migration
        # (SQLite lacks ADD COLUMN IF NOT EXISTS before 3.37). Dismissing a
        # notification archives it (#62); nothing is hard-deleted by the user.
        cols = {row[1] for row in await (await self._db.execute("PRAGMA table_info(notifications)")).fetchall()}
        if "archived" not in cols:
            await self._db.execute(
                "ALTER TABLE notifications ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"
            )
            await self._db.commit()
        # `data` carries a JSON payload for structured notifications (e.g. an
        # agent auth-request's request_id + scopes). Guarded ALTER so existing
        # databases gain the column without a destructive migration.
        if "data" not in cols:
            await self._db.execute(
                "ALTER TABLE notifications ADD COLUMN data TEXT"
            )
            await self._db.commit()
        # `user_id` scopes notifications to a specific user. Guarded ALTER so
        # existing databases gain the column without a destructive migration.
        # NULL = broadcast (system-wide notice); non-NULL = scoped to that user.
        if "user_id" not in cols:
            await self._db.execute(
                "ALTER TABLE notifications ADD COLUMN user_id TEXT"
            )
            await self._db.commit()
        # `digest` on os_notification_ids: an earlier version of the table had no such column, and
        # CREATE TABLE IF NOT EXISTS leaves an existing table as it was. Guarded ALTER, like the above;
        # rows from before the column keep the empty default.
        id_cols = {row[1] for row in await (await self._db.execute("PRAGMA table_info(os_notification_ids)")).fetchall()}
        if "digest" not in id_cols:
            await self._db.execute("ALTER TABLE os_notification_ids ADD COLUMN digest TEXT NOT NULL DEFAULT ''")
            await self._db.commit()
        # `style` / `show_centre` on notification_prefs (#206): the mute slice
        # shipped with only `muted`; existing databases gain the delivery
        # columns via guarded ALTER, same pattern as the columns above.
        pref_cols = {row[1] for row in await (await self._db.execute("PRAGMA table_info(notification_prefs)")).fetchall()}
        if "style" not in pref_cols:
            await self._db.execute(
                "ALTER TABLE notification_prefs ADD COLUMN style TEXT NOT NULL DEFAULT 'banner'"
            )
            await self._db.commit()
        if "show_centre" not in pref_cols:
            await self._db.execute(
                "ALTER TABLE notification_prefs ADD COLUMN show_centre INTEGER NOT NULL DEFAULT 1"
            )
            await self._db.commit()
        # Create the per-user index if it doesn't exist yet. Cannot live in
        # the SCHEMA because the column is added via guarded ALTER after init.
        #
        # NOTE: On first boot after deployment this builds synchronously
        # inside _post_init (app startup).  On an instance with a large
        # ``notifications`` table the build takes a write lock on the table
        # and blocks notification writes for the duration.  This is a
        # one‑time cost — subsequent starts hit IF NOT EXISTS and return
        # immediately.  If this becomes a problem at scale, the index can
        # be built lazily after the server is already serving requests.
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id)"
        )
        await self._db.commit()
        # Ensure the notification_user_state table exists for per-user broadcast state.
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_user_state (
                notification_id INTEGER NOT NULL,
                user_id TEXT NOT NULL,
                read_at INTEGER,
                archived_at INTEGER,
                PRIMARY KEY (notification_id, user_id)
            );
            """
        )
        await self._db.commit()
        # Create an index on notification_id for efficient lookups.
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_notif_state_notif ON notification_user_state(notification_id)"
        )
        await self._db.commit()

    async def add(
        self,
        title: str,
        message: str,
        level: str = "info",
        source: str = "system",
        data: dict | None = None,
        user_id: str | None = None,
        webhook: bool = True,
        timestamp: int | None = None,
        os_client_id: str | None = None,
        os_digest: str = "",
    ) -> int | None:
        """Create a notification row and fan out to emitters + push.

        Returns the new row id, or None when the source is muted or its
        prefs keep it out of the Notification Centre (toast-only, #206).

        Args:
            user_id: Scope the notification to a specific user. When None
                (default), the notification is a broadcast delivered to every
                subscribed device (genuinely system-wide notices). When set to a
                user id, web-push delivery fans out only to that user's
                subscriptions.
            webhook: Also post to the configured webhooks (default). A caller
                writing one event as several per-user rows passes False on all
                but one row, so a webhook sees the event once.
            timestamp: Row time (epoch seconds); defaults to now. A buffered
                OS notification that reaches the controller late keeps the time
                the person actually saw it raised.
            os_client_id: The client-chosen id of an OS notification. It is
                recorded for ``user_id`` in the SAME transaction as the row, so
                a crash can never leave a row without its idempotency record.
        """
        pref = await self._get_event_pref(source)
        if pref["muted"]:
            return None
        ts = int(time.time()) if timestamp is None else int(timestamp)
        data_json = json.dumps(data) if data is not None else None
        # show_centre off (#206): the notification is toast-only — published
        # live but never stored, so it never reaches the Notification Centre.
        # OS-originated rows always store: their idempotency record must be
        # written in the same transaction as a real row.
        stored = pref["show_centre"] or os_client_id is not None
        row_id = None
        if stored:
            cursor = await self._db.execute(
                "INSERT INTO notifications (timestamp, level, title, message, source, data, user_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ts, level, title, message, source, data_json, user_id),
            )
            row_id = cursor.lastrowid
            if os_client_id is not None:
                await self._db.execute(
                    "INSERT INTO os_notification_ids (user_id, client_id, notification_id, digest) VALUES (?, ?, ?, ?)",
                    (user_id or "", os_client_id, row_id, os_digest),
                )
            await self._db.commit()
        # Fire webhook notifications in the background
        if webhook and self._webhook_notifier:
            try:
                await self._webhook_notifier.notify(title, message, level)
            except Exception as e:
                logger.warning(f"Webhook notification error: {e}")
        # Push to EventBus broadcast for SSE delivery — best-effort, never breaks add()
        if row_id is None:
            # Toast-only row: a unique negative id keeps the client's srv-<id>
            # keying collision-free against stored rows (and other transient
            # rows); read/archive calls against it are best-effort no-ops.
            self._transient_seq -= 1
            publish_id = self._transient_seq
        else:
            publish_id = row_id
        row = {
            "id": publish_id,
            "timestamp": ts,
            "level": level,
            "title": title,
            "message": message,
            "read": False,
            "source": source,
            "data": data,
            "user_id": user_id,
        }
        if pref["style"] == "alert":
            row["persistent"] = True
        # Style "none" and an active Do Not Disturb window both mean: stored
        # (above) but no live fan-out — no toast, no web-push (#206).
        deliver = pref["style"] != "none" and not await self.dnd_active()
        await self._publish(row, deliver=deliver)
        return row_id

    async def _publish(self, row: dict, *, deliver: bool = True) -> None:
        """Fan a row out to the SSE emitter and web-push. Best-effort.

        ``deliver=False`` skips the live fan-out entirely: the delivery
        style "none" and an active Do Not Disturb window both mean the
        notification lands in the Notification Centre silently (#206).
        """
        if not deliver:
            return
        if self._event_emitter:
            try:
                await self._event_emitter(row)
            except Exception:
                logger.warning("NotificationStore: event emitter failed", exc_info=True)
        # Fire the OS-level PWA web-push off the event loop: strictly
        # best-effort. Scheduled as a background task so a slow or failing push
        # (network round-trip) never stalls or breaks add().
        if self._push_sender:
            try:
                task = asyncio.create_task(self._dispatch_push(row))
                # Hold a strong ref until done: a bare create_task() can be
                # garbage-collected mid-flight (see asyncio.create_task docs).
                self._push_tasks.add(task)
                task.add_done_callback(self._push_tasks.discard)
            except Exception:
                logger.warning("NotificationStore: could not schedule web-push", exc_info=True)

    async def add_os(
        self,
        *,
        user_id: str,
        client_id: str,
        title: str,
        message: str,
        level: str,
        source: str,
        data: dict,
        timestamp: int | None = None,
        replaces: str | None = None,
        digest: str = "",
    ) -> tuple[int | None, bool]:
        """Store one OS-originated (freedesktop) notification for ``user_id``.

        Idempotent on ``(user_id, client_id)``: a retry of the same id returns
        the row the first call made and creates nothing (even if the person has
        since read or dismissed it). Ids are per person, so two people can use
        the same id. When ``replaces`` names an earlier client id of the same
        person the row is updated in place (and made unread again) instead of
        a new one being created. Webhooks never see OS rows.

        Raises ``OsIdConflict`` when ``client_id`` was used before with a different
        ``digest`` (the fields that matter); a true retry is byte-equivalent.
        Returns ``(row id, created)``; ``(None, False)`` when the source is muted.

        The id records live exactly as long as their rows: ``cleanup()`` prunes
        them together. The daemon stamps ``timestamp`` with the time the app raised
        the notification and the route clamps it to the last 30 days, which is the
        retention window, so a retry can only outlive its record after 30 days of
        failed delivery; it then yields one fresh row that ages out again.
        """
        async with self._os_lock:
            async with self._db.execute(
                "SELECT notification_id, digest FROM os_notification_ids WHERE user_id = ? AND client_id = ?",
                (user_id, client_id),
            ) as cur:
                found = await cur.fetchone()
            if found:
                if found[1] != digest:
                    raise OsIdConflict(client_id)
                return found[0], False
            if await self._is_event_muted(source):
                return None, False
            if replaces:
                async with self._db.execute(
                    "SELECT notification_id FROM os_notification_ids WHERE user_id = ? AND client_id = ?",
                    (user_id, replaces),
                ) as cur:
                    target = await cur.fetchone()
                if target:
                    ts = int(time.time()) if timestamp is None else int(timestamp)
                    upd = await self._db.execute(
                        "UPDATE notifications SET timestamp = ?, level = ?, title = ?, message = ?, "
                        "source = ?, data = ?, read = 0, archived = 0 WHERE id = ? AND user_id = ?",
                        (ts, level, title, message, source, json.dumps(data), target[0], user_id),
                    )
                    if upd.rowcount == 0:
                        # The row was removed (retention): the replacement must not be lost.
                        # Store it as a new row and point the old id at it.
                        rid = await self.add(
                            title, message, level=level, source=source, data=data, user_id=user_id,
                            webhook=False, timestamp=timestamp, os_client_id=client_id, os_digest=digest,
                        )
                        if rid is not None:
                            await self._db.execute(
                                "UPDATE os_notification_ids SET notification_id = ? WHERE user_id = ? AND client_id = ?",
                                (rid, user_id, replaces),
                            )
                            await self._db.commit()
                        return rid, rid is not None
                    await self._db.execute(
                        "INSERT INTO os_notification_ids (user_id, client_id, notification_id, digest) VALUES (?, ?, ?, ?)",
                        (user_id, client_id, target[0], digest),
                    )
                    await self._db.commit()
                    _pref = await self._get_event_pref(source)
                    await self._publish(
                        {
                            "id": target[0], "timestamp": ts, "level": level, "title": title,
                            "message": message, "read": False, "source": source, "data": data,
                            "user_id": user_id,
                        },
                        deliver=_pref["style"] != "none" and not await self.dnd_active(),
                    )
                    return target[0], False
            rid = await self.add(
                title, message, level=level, source=source, data=data, user_id=user_id,
                webhook=False, timestamp=timestamp, os_client_id=client_id, os_digest=digest,
            )
            return rid, rid is not None

    async def list(
        self,
        limit: int = 20,
        unread_only: bool = False,
        user_id: str | None = None,
        source: str | None = None,
    ) -> list[dict]:
        if user_id is not None and source is not None:
            raise ValueError("list() takes user_id or source, not both")
        if user_id is not None:
            # Per-user notifications: include both own notifications and broadcasts
            # First, fetch pinned grant requests (non-archived, ignoring read/unread_only)
            pinned_placeholders = ",".join("?" * len(PINNED_SOURCES))
            pinned_sql = (
                "SELECT n.id, n.timestamp, n.level, n.title, n.message, "
                "COALESCE(nus.read_at, n.read) as read, n.source, n.data, n.user_id "
                "FROM notifications n "
                "LEFT JOIN notification_user_state nus ON n.id = nus.notification_id AND nus.user_id = ? "
                f"WHERE (n.user_id IS NULL OR n.user_id = ?) AND n.archived = 0 AND (n.user_id IS NOT NULL OR nus.archived_at IS NULL) AND n.source IN ({pinned_placeholders}) "
                "ORDER BY n.timestamp DESC"
            )
            pinned_params: tuple = (user_id, user_id) + PINNED_SOURCES
            async with self._db.execute(pinned_sql, pinned_params) as cursor:
                pinned_rows = await cursor.fetchall()
            pinned_items = [_serialize_row(r) for r in pinned_rows]
            for item in pinned_items:
                item["pinned"] = True

            # Then fetch regular (non-pinned) notifications with the limit
            conds = [
                "(n.user_id IS NULL OR n.user_id = ?)",
                "n.archived = 0",
                f"(n.source IS NULL OR n.source NOT IN ({pinned_placeholders}))",
                "(n.user_id IS NOT NULL OR nus.archived_at IS NULL)",
            ]
            params_list = [user_id, user_id] + list(PINNED_SOURCES)
            if unread_only:
                conds.append("COALESCE(nus.read_at, n.read) = 0")
            sql = (
                "SELECT n.id, n.timestamp, n.level, n.title, n.message, "
                "COALESCE(nus.read_at, n.read) as read, n.source, n.data, n.user_id "
                "FROM notifications n "
                "LEFT JOIN notification_user_state nus ON n.id = nus.notification_id AND nus.user_id = ? "
                f"WHERE {' AND '.join(conds)} "
                "ORDER BY n.timestamp DESC LIMIT ?"
            )
            params = tuple(params_list + [limit])
            async with self._db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
            regular_items = [_serialize_row(r) for r in rows]
            for item in regular_items:
                item["pinned"] = False

            return pinned_items + regular_items
        else:
            # Internal/system caller: unfiltered by user.  ``source`` narrows
            # to one sender (the agent path of GET /api/notifications lists
            # only rows stamped ``agent:<canonical_id>``).
            conds = ["archived = 0"]
            source_params: tuple = ()
            if unread_only:
                conds.append("read = 0")
            if source is not None:
                conds.append("source = ?")
                source_params = (source,)
            sql = (
                "SELECT id, timestamp, level, title, message, read, source, data, user_id FROM notifications"
                f" WHERE {' AND '.join(conds)} ORDER BY timestamp DESC LIMIT ?"
            )
            params: tuple = source_params + (limit,)
            async with self._db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
            return [_serialize_row(r) for r in rows]

    async def list_archived(
        self,
        limit: int = 50,
        user_id: str | None = None,
    ) -> list[dict]:
        if user_id is not None:
            # Per-user notifications: include both own notifications and broadcasts
            # Archive state for broadcasts is stored in notification_user_state
            sql = (
                "SELECT n.id, n.timestamp, n.level, n.title, n.message, "
                "COALESCE(nus.read_at, n.read) as read, n.source, n.data, n.user_id "
                "FROM notifications n "
                "LEFT JOIN notification_user_state nus ON n.id = nus.notification_id AND nus.user_id = ? "
                "WHERE (n.user_id IS NULL OR n.user_id = ?) AND (n.archived = 1 OR nus.archived_at IS NOT NULL)"
                " ORDER BY n.timestamp DESC LIMIT ?"
            )
            params = (user_id, user_id, limit)
            async with self._db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
            return [_serialize_row(r) for r in rows]
        else:
            # Internal/system caller: unfiltered by user
            sql = (
                "SELECT id, timestamp, level, title, message, read, source, data, user_id FROM notifications"
                " WHERE archived = 1 ORDER BY timestamp DESC LIMIT ?"
            )
            params = (limit,)
            async with self._db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
            return [_serialize_row(r) for r in rows]

    async def unread_count(self, user_id: str | None = None) -> int:
        if user_id is not None:
            # For per-user notifications, include both own notifications and broadcasts
            # Read state for broadcasts is stored in notification_user_state
            sql = (
                "SELECT COUNT(*) FROM notifications n "
                "LEFT JOIN notification_user_state nus ON n.id = nus.notification_id AND nus.user_id = ? "
                "WHERE (n.user_id IS NULL OR n.user_id = ?) AND (n.user_id IS NOT NULL OR nus.archived_at IS NULL) AND COALESCE(nus.read_at, n.read) = 0"
            )
            params = (user_id, user_id)
        else:
            # Internal/system caller: unfiltered by user
            sql = (
                "SELECT COUNT(*) FROM notifications "
                "WHERE read = 0 AND archived = 0"
            )
            params = ()
        async with self._db.execute(sql, params) as cursor:
            row = await cursor.fetchone()
        return row[0] if row else 0

    async def mark_read(self, notif_id: int, user_id: str | None = None) -> int:
        if user_id is not None:
            # Per-user or broadcast notification for a specific user:
            # Update shared columns if it's a per-user notification
            # For broadcast notifications, update per-user state
            ts = int(time.time())
            cursor = await self._db.execute(
                "SELECT user_id FROM notifications WHERE id = ?", (notif_id,)
            )
            row = await cursor.fetchone()
            
            if row and row[0] is None:
                # This is a broadcast notification - upsert into per-user state
                await self._db.execute(
                    "INSERT OR REPLACE INTO notification_user_state (notification_id, user_id, read_at) VALUES (?, ?, ?)",
                    (notif_id, user_id, ts),
                )
                await self._db.commit()
                return 1
            else:
                # This is a per-user notification - update shared columns
                cursor = await self._db.execute(
                    "UPDATE notifications SET read = 1 WHERE id = ? AND (user_id IS NULL OR user_id = ?)",
                    (notif_id, user_id),
                )
                await self._db.commit()
                return cursor.rowcount
        else:
            # System/internal caller: update shared columns for broadcast notifications
            cursor = await self._db.execute(
                "UPDATE notifications SET read = 1 WHERE id = ?", (notif_id,)
            )
            await self._db.commit()
            return cursor.rowcount

    async def archive(self, notif_id: int, user_id: str | None = None) -> int:
        if user_id is not None:
            # Check if this is a broadcast notification
            cursor = await self._db.execute(
                "SELECT user_id FROM notifications WHERE id = ?", (notif_id,)
            )
            row = await cursor.fetchone()
            
            if row and row[0] is None:
                # This is a broadcast notification
                ts = int(time.time())
                await self._db.execute(
                    "INSERT OR REPLACE INTO notification_user_state (notification_id, user_id, archived_at) VALUES (?, ?, ?)",
                    (notif_id, user_id, ts),
                )
                await self._db.commit()
                # Do NOT update the shared archived column for broadcasts
                return 1
            else:
                # This is a per-user notification
                cursor = await self._db.execute(
                    "UPDATE notifications SET archived = 1 WHERE id = ? AND (user_id IS NULL OR user_id = ?)",
                    (notif_id, user_id),
                )
                await self._db.commit()
                return cursor.rowcount
        else:
            # Internal/system caller: unfiltered update
            cursor = await self._db.execute(
                "UPDATE notifications SET archived = 1 WHERE id = ?", (notif_id,)
            )
            await self._db.commit()
            return cursor.rowcount

    async def get(self, notif_id: int) -> dict | None:
        """Return a single notification row by id, or None if not found.

        Uses the existing _serialize_row for consistent mapping.
        """
        async with self._db.execute(
            "SELECT id, timestamp, level, title, message, read, source, data, user_id FROM notifications WHERE id = ?",
            (notif_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return _serialize_row(row)

    async def archive_by_source_ref(self, source: str, request_id) -> int:
        """Archive active notifications whose JSON `data.request_id` matches.

        Used to retire an agent auth-request notification once the request is
        terminally decided (approved/denied) so it leaves the active bell list
        and moves into History. Acting on it both reads and archives the row
        (#62: nothing is deleted). Idempotent: rows already archived are
        skipped; returns the number newly archived.
        """
        # Intentionally global: resolves by source + request_id, not by user.
        async with self._db.execute(
            "SELECT id, data FROM notifications WHERE source = ? AND archived = 0",
            (source,),
        ) as cursor:
            rows = await cursor.fetchall()
        target = str(request_id)
        ids = []
        for nid, raw in rows:
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if str(payload.get("request_id")) == target:
                ids.append(nid)
        if ids:
            # Intentionally global: source-ref resolution applies to the row,
            # not to a specific user.
            placeholders = ",".join("?" * len(ids))
            await self._db.execute(
                f"UPDATE notifications SET archived = 1, read = 1 WHERE id IN ({placeholders})",
                ids,
            )
            await self._db.commit()
        return len(ids)

    async def mark_all_read(self, user_id: str | None = None) -> int:
        if user_id is not None:
            # Mark all per-user notifications (both user-specific and broadcasts for that user) as read
            cursor = await self._db.execute(
                "UPDATE notifications SET read = 1 WHERE read = 0 AND (user_id IS NULL OR user_id = ?)",
                (user_id,),
            )
            await self._db.commit()
            
            # Also mark any per-user state as read for this user
            await self._db.execute(
                "UPDATE notification_user_state SET read_at = ? WHERE user_id = ?",
                (int(time.time()), user_id),
            )
            await self._db.commit()
            
            return cursor.rowcount
        else:
            # System/internal caller: mark all notifications as read
            # This updates the shared read column for both per-user and broadcast notifications
            cursor = await self._db.execute("UPDATE notifications SET read = 1 WHERE read = 0")
            await self._db.commit()
            return cursor.rowcount

    async def cleanup(self, max_age_days: int = 30) -> int:
        # Age out only old UNdismissed notifications. Archived rows are the
        # durable history a user explicitly dismissed (#62 / append-only #103),
        # so they are never GC'd here.
        # Intentionally global: retention/prune is a system-wide operation.
        cutoff = int(time.time()) - (max_age_days * 86400)
        cursor = await self._db.execute(
            "DELETE FROM notifications WHERE timestamp < ? AND archived = 0", (cutoff,)
        )
        # Drop the OS-notification id records of rows that are gone, with the rows.
        await self._db.execute(
            "DELETE FROM os_notification_ids WHERE notification_id NOT IN (SELECT id FROM notifications)"
        )
        await self._db.commit()
        return cursor.rowcount

    async def emit_event(
        self,
        event_type: str,
        title: str,
        message: str,
        level: str = "info",
        user_id: str | None = None,
        webhook: bool = True,
    ) -> None:
        if await self._is_event_muted(event_type):
            return
        await self.add(
            title, message, level=level, source=event_type, user_id=user_id, webhook=webhook
        )

    async def _get_event_pref(self, event_type: str) -> dict:
        """Full delivery pref for one event type, defaults applied.

        Default: unmuted, "banner" style, shown in the Notification Centre.
        Sources with no stored row — including non-event sources passed to
        add() — get the default, so their behaviour is unchanged.
        """
        async with self._db.execute(
            "SELECT muted, style, show_centre FROM notification_prefs WHERE event_type = ?",
            (event_type,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return {"muted": False, "style": "banner", "show_centre": True}
        style = row[1] if row[1] in DELIVERY_STYLES else "banner"
        return {"muted": bool(row[0]), "style": style, "show_centre": bool(row[2])}

    async def _is_event_muted(self, event_type: str) -> bool:
        return (await self._get_event_pref(event_type))["muted"]

    async def set_event_pref(
        self,
        event_type: str,
        *,
        muted: bool | None = None,
        style: str | None = None,
        show_centre: bool | None = None,
    ) -> dict:
        """Update one event type's delivery pref in place; returns the pref.

        Fields left as None keep their stored (or default) values — a
        muted-only write must not reset style/show_centre (#206).
        """
        if style is not None and style not in DELIVERY_STYLES:
            raise ValueError(f"unknown delivery style: {style!r}")
        pref = await self._get_event_pref(event_type)
        if muted is not None:
            pref["muted"] = bool(muted)
        if style is not None:
            pref["style"] = style
        if show_centre is not None:
            pref["show_centre"] = bool(show_centre)
        await self._db.execute(
            "INSERT OR REPLACE INTO notification_prefs (event_type, muted, style, show_centre) VALUES (?, ?, ?, ?)",
            (event_type, int(pref["muted"]), pref["style"], int(pref["show_centre"])),
        )
        await self._db.commit()
        return {"event_type": event_type, **pref}

    async def set_event_muted(self, event_type: str, muted: bool) -> None:
        await self.set_event_pref(event_type, muted=muted)

    async def get_event_prefs(self) -> list[dict]:
        async with self._db.execute(
            "SELECT event_type, muted, style, show_centre FROM notification_prefs"
        ) as cursor:
            rows = await cursor.fetchall()
        stored = {
            r[0]: {
                "muted": bool(r[1]),
                "style": r[2] if r[2] in DELIVERY_STYLES else "banner",
                "show_centre": bool(r[3]),
            }
            for r in rows
        }
        default = {"muted": False, "style": "banner", "show_centre": True}
        return [
            {"event_type": et, **stored.get(et, default)}
            for et in self.EVENT_TYPES
        ]

    # -- Do Not Disturb (#206) -------------------------------------------
    # One global row (id = 1). While enabled and the local wall clock is
    # inside the window, notifications are stored but not delivered live.
    # start == end means the whole day; start > end wraps past midnight.

    async def get_dnd(self) -> dict:
        async with self._db.execute(
            "SELECT enabled, start, end FROM notification_dnd WHERE id = 1"
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return dict(DEFAULT_DND)
        return {"enabled": bool(row[0]), "start": row[1], "end": row[2]}

    async def set_dnd(self, enabled: bool, start: str, end: str) -> dict:
        if not _valid_hhmm(start) or not _valid_hhmm(end):
            raise ValueError("DND start/end must be zero-padded HH:MM times")
        await self._db.execute(
            "INSERT OR REPLACE INTO notification_dnd (id, enabled, start, end) VALUES (1, ?, ?, ?)",
            (int(bool(enabled)), start, end),
        )
        await self._db.commit()
        return {"enabled": bool(enabled), "start": start, "end": end}

    async def dnd_active(self, now: str | None = None) -> bool:
        """Whether DND is in effect at local time ``now`` ("HH:MM")."""
        dnd = await self.get_dnd()
        if not dnd["enabled"]:
            return False
        if now is None:
            now = time.strftime("%H:%M")
        start, end = dnd["start"], dnd["end"]
        if start == end:
            return True
        if start < end:
            return start <= now < end
        return now >= start or now < end

    async def search(
        self,
        user_id: str,
        q: str | None = None,
        sources: list[str] | None = None,
        levels: list[str] | None = None,
        since: int | None = None,
        until: int | None = None,
        state: str = "all",
        before: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Search historic notifications for a user with filters and keyset paging.

        User scoping mirrors list_archived: own rows + broadcasts (n.user_id IS NULL),
        read/archived state from notification_user_state via LEFT JOIN.
        """
        if state not in ("all", "unread", "read", "archived", "active"):
            raise ValueError(f"invalid state: {state}")

        limit = max(1, min(limit, 200))

        conds = ["(n.user_id IS NULL OR n.user_id = ?)"]
        where_params: list = [user_id]

        if q is not None:
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conds.append("(n.title LIKE ? ESCAPE '\\' OR n.message LIKE ? ESCAPE '\\')")
            like_param = f"%{escaped}%"
            where_params.extend([like_param, like_param])

        if sources:
            placeholders = ",".join("?" * len(sources))
            conds.append(f"n.source IN ({placeholders})")
            where_params.extend(sources)

        if levels:
            placeholders = ",".join("?" * len(levels))
            conds.append(f"n.level IN ({placeholders})")
            where_params.extend(levels)

        if since is not None:
            conds.append("n.timestamp >= ?")
            where_params.append(since)

        if until is not None:
            conds.append("n.timestamp <= ?")
            where_params.append(until)

        if state == "unread":
            conds.append("COALESCE(nus.read_at, n.read) = 0")
        elif state == "read":
            conds.append("COALESCE(nus.read_at, n.read) != 0")
        elif state == "archived":
            conds.append("(n.archived = 1 OR nus.archived_at IS NOT NULL)")
        elif state == "active":
            conds.append("(n.archived = 0 AND nus.archived_at IS NULL)")

        if before is not None:
            try:
                before_ts, before_id = before.split(":", 1)
                before_ts = int(before_ts)
                before_id = int(before_id)
            except (ValueError, AttributeError):
                raise ValueError("invalid cursor")
            conds.append("(n.timestamp < ? OR (n.timestamp = ? AND n.id < ?))")
            where_params.extend([before_ts, before_ts, before_id])

        where_clause = " AND ".join(conds)

        sql = (
            "SELECT n.id, n.timestamp, n.level, n.title, n.message, "
            "COALESCE(nus.read_at, n.read) as read, n.source, n.data, n.user_id, "
            "CASE WHEN n.archived = 1 OR nus.archived_at IS NOT NULL THEN 1 ELSE 0 END as archived "
            "FROM notifications n "
            "LEFT JOIN notification_user_state nus ON n.id = nus.notification_id AND nus.user_id = ? "
            f"WHERE {where_clause} "
            "ORDER BY n.timestamp DESC, n.id DESC LIMIT ?"
        )
        # Params order: JOIN user_id, WHERE params..., limit
        full_params = [user_id] + where_params + [limit]

        async with self._db.execute(sql, tuple(full_params)) as cursor:
            rows = await cursor.fetchall()

        results = []
        for r in rows:
            row_dict = _serialize_row(r[:-1])
            row_dict["archived"] = bool(r[-1])
            results.append(row_dict)
        return results

    async def history_sources(self, user_id: str) -> list[str]:
        """Return distinct sources visible to the user, sorted."""
        sql = """
            SELECT DISTINCT n.source
            FROM notifications n
            LEFT JOIN notification_user_state nus ON n.id = nus.notification_id AND nus.user_id = ?
            WHERE (n.user_id IS NULL OR n.user_id = ?)
              AND n.source IS NOT NULL
            ORDER BY n.source
        """
        async with self._db.execute(sql, (user_id, user_id)) as cursor:
            rows = await cursor.fetchall()
        return [r[0] for r in rows]
