from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tinyagentos.base_store import BaseStore

# Canonical notification level vocabulary — single source of truth.
# The notify_user tool schema, the POST /api/notifications route, and the
# desktop toast/archive renderers all consume this set.
VALID_LEVELS = frozenset({"info", "success", "warning", "error"})

# Per-source delivery styles (#206): "banner" toasts and auto-dismisses,
# "alert" toasts and stays until dismissed (published with persistent=True),
# "none" is stored in the Notification Centre with no live fan-out.
DELIVERY_STYLES = ("banner", "alert", "none")
DEFAULT_DND = {"enabled": False, "start": "22:00", "end": "07:00", "timezone": "UTC"}


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
    user_id TEXT,
    show_centre INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_notif_ts ON notifications(timestamp DESC);
CREATE TABLE IF NOT EXISTS notification_prefs (
    user_id TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL,
    muted INTEGER NOT NULL DEFAULT 0,
    style TEXT NOT NULL DEFAULT 'banner',
    show_centre INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (user_id, event_type)
);
CREATE TABLE IF NOT EXISTS notification_dnd (
    user_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    start TEXT NOT NULL DEFAULT '22:00',
    end TEXT NOT NULL DEFAULT '07:00',
    timezone TEXT NOT NULL DEFAULT 'UTC'
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
        # `show_centre` marks rows their owner's pref kept out of the
        # Notification Centre at insert time (#206): stored, but hidden
        # from the Centre listing. Guarded ALTER; rows from before the
        # column keep the visible default.
        if "show_centre" not in cols:
            await self._db.execute(
                "ALTER TABLE notifications ADD COLUMN show_centre INTEGER NOT NULL DEFAULT 1"
            )
            await self._db.commit()
        # `digest` on os_notification_ids: an earlier version of the table had no such column, and
        # CREATE TABLE IF NOT EXISTS leaves an existing table as it was. Guarded ALTER, like the above;
        # rows from before the column keep the empty default.
        id_cols = {row[1] for row in await (await self._db.execute("PRAGMA table_info(os_notification_ids)")).fetchall()}
        if "digest" not in id_cols:
            await self._db.execute("ALTER TABLE os_notification_ids ADD COLUMN digest TEXT NOT NULL DEFAULT ''")
            await self._db.commit()
        # Per-person delivery prefs (#206): the shipped prefs table keys
        # rows by event_type alone (mute was a global setting). SQLite
        # cannot change a primary key in place, so a table without a
        # user_id column is rebuilt with the composite key. Existing rows
        # are preserved under the legacy '' owner, where they no longer
        # apply to anyone: a global mute has no person to attribute it
        # to, and attributing it to one person would recreate the global
        # setting under a personal name.
        pref_cols = {row[1] for row in await (await self._db.execute("PRAGMA table_info(notification_prefs)")).fetchall()}
        if pref_cols and "user_id" not in pref_cols:
            style_expr = "style" if "style" in pref_cols else "'banner'"
            centre_expr = "show_centre" if "show_centre" in pref_cols else "1"
            # An interrupted earlier run can leave the staging table on
            # disk (the CREATE below commits in autocommit mode, before
            # the copy does). It holds nothing the migration depends on
            # -- the source table is intact until the DROP/RENAME commit
            # -- so drop any leftover and rerun clean.
            await self._db.execute("DROP TABLE IF EXISTS notification_prefs_new")
            await self._db.execute(
                "CREATE TABLE notification_prefs_new ("
                "user_id TEXT NOT NULL DEFAULT '', event_type TEXT NOT NULL, "
                "muted INTEGER NOT NULL DEFAULT 0, "
                "style TEXT NOT NULL DEFAULT 'banner', "
                "show_centre INTEGER NOT NULL DEFAULT 1, "
                "PRIMARY KEY (user_id, event_type))"
            )
            await self._db.execute(
                "INSERT OR IGNORE INTO notification_prefs_new "
                "(user_id, event_type, muted, style, show_centre) "
                f"SELECT '', event_type, muted, {style_expr}, {centre_expr} "
                "FROM notification_prefs"
            )
            await self._db.execute("DROP TABLE notification_prefs")
            await self._db.execute(
                "ALTER TABLE notification_prefs_new RENAME TO notification_prefs"
            )
            await self._db.commit()
        # The schema-column guard (scripts/check_schema_column_migrations.py)
        # requires every column the CREATE TABLE gained to also appear as a
        # guarded ALTER in this file. The rebuild above is the real migration
        # for legacy tables -- SQLite cannot change a primary key in place --
        # and it runs FIRST, so these ALTERs can never suppress it: by this
        # point every real path (rebuilt table, fresh CREATE, or a table that
        # already had the composite key) has all three columns, and the
        # PRAGMA-guarded ALTERs are no-ops there. They still earn their keep
        # on a table that has the composite key but somehow lost a column.
        pref_cols = {row[1] for row in await (await self._db.execute("PRAGMA table_info(notification_prefs)")).fetchall()}
        if pref_cols and "user_id" not in pref_cols:
            await self._db.execute(
                "ALTER TABLE notification_prefs ADD COLUMN user_id TEXT NOT NULL DEFAULT ''"
            )
            await self._db.commit()
        if pref_cols and "style" not in pref_cols:
            await self._db.execute(
                "ALTER TABLE notification_prefs ADD COLUMN style TEXT NOT NULL DEFAULT 'banner'"
            )
            await self._db.commit()
        if pref_cols and "show_centre" not in pref_cols:
            await self._db.execute(
                "ALTER TABLE notification_prefs ADD COLUMN show_centre INTEGER NOT NULL DEFAULT 1"
            )
            await self._db.commit()
        # Same move for notification_dnd if an early single-row shape is
        # present (it held no per-person data worth preserving).
        dnd_cols = {row[1] for row in await (await self._db.execute("PRAGMA table_info(notification_dnd)")).fetchall()}
        if dnd_cols and "user_id" not in dnd_cols:
            await self._db.execute("DROP TABLE notification_dnd")
            await self._db.execute(
                "CREATE TABLE notification_dnd ("
                "user_id TEXT PRIMARY KEY, "
                "enabled INTEGER NOT NULL DEFAULT 0, "
                "start TEXT NOT NULL DEFAULT '22:00', "
                "end TEXT NOT NULL DEFAULT '07:00', "
                "timezone TEXT NOT NULL DEFAULT 'UTC')"
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

        Returns the new row id, or None when the source is muted for the
        recipient. A centre-off source still stores its row, marked
        show_centre = 0; list() filters marked rows out of the owner's
        Centre view, so nothing is lost (#206).

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
        pref = await self._pref_for(user_id, source)
        if pref["muted"]:
            return None
        ts = int(time.time()) if timestamp is None else int(timestamp)
        data_json = json.dumps(data) if data is not None else None
        # show_centre off (#206): the row is STILL stored -- nothing is
        # lost -- but marked show_centre = 0, and list() filters marked
        # rows out of the owner's Centre view. The live toast below is
        # unaffected. (OS-originated rows store either way: their
        # idempotency record must commit in the same transaction.)
        cursor = await self._db.execute(
            "INSERT INTO notifications (timestamp, level, title, message, source, data, user_id, show_centre) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, level, title, message, source, data_json, user_id, 1 if pref["show_centre"] else 0),
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
        # Fan out to the SSE emitter + web-push -- best-effort, never breaks add()
        row = {
            "id": row_id,
            "timestamp": ts,
            "level": level,
            "title": title,
            "message": message,
            "read": False,
            "source": source,
            "data": data,
            "user_id": user_id,
            "show_centre": pref["show_centre"],
        }
        if pref["style"] == "alert":
            row["persistent"] = True
        # Style "none" and an active Do Not Disturb window both mean: stored
        # (above) but no live fan-out -- no toast, no web-push (#206).
        deliver = pref["style"] != "none" and not (
            user_id and await self.dnd_active(user_id)
        )
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
            if user_id and await self._is_event_muted(user_id, source):
                return None, False
            if replaces:
                async with self._db.execute(
                    "SELECT notification_id FROM os_notification_ids WHERE user_id = ? AND client_id = ?",
                    (user_id, replaces),
                ) as cur:
                    target = await cur.fetchone()
                if target:
                    ts = int(time.time()) if timestamp is None else int(timestamp)
                    _pref = await self._pref_for(user_id, source)
                    upd = await self._db.execute(
                        "UPDATE notifications SET timestamp = ?, level = ?, title = ?, message = ?, "
                        "source = ?, data = ?, read = 0, archived = 0, show_centre = ? WHERE id = ? AND user_id = ?",
                        (ts, level, title, message, source, json.dumps(data), 1 if _pref["show_centre"] else 0, target[0], user_id),
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
                    _row = {
                        "id": target[0], "timestamp": ts, "level": level, "title": title,
                        "message": message, "read": False, "source": source, "data": data,
                        "user_id": user_id, "show_centre": _pref["show_centre"],
                    }
                    if _pref["style"] == "alert":
                        _row["persistent"] = True
                    await self._publish(
                        _row,
                        deliver=_pref["style"] != "none"
                        and not (user_id and await self.dnd_active(user_id)),
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
            # Per-person delivery prefs (#206) are enforced in SQL, before
            # LIMIT, so hidden rows cannot starve a page:
            #   * rows marked show_centre = 0 at insert time (their owner's
            #     centre-off pref) never appear in the Centre listing -- the
            #     marking is insert-time, so a later pref change neither
            #     hides nor resurrects stored rows;
            #   * broadcast rows are shared and carry no per-person marking,
            #     so they are filtered per reader by the reader's CURRENT
            #     muted / centre-off sources (the hidden predicate below).
            # The reader's own unmarked rows are never read-filtered.
            hidden = await self._hidden_sources(user_id)
            hidden_placeholders = ",".join("?" * len(hidden))
            hidden_params: tuple = tuple(sorted(hidden))
            hidden_pred = ""
            if hidden:
                hidden_pred = (
                    f" AND (n.user_id IS NOT NULL OR n.source IS NULL OR n.source NOT IN ({hidden_placeholders}))"
                )
            # First, fetch pinned grant requests (non-archived, ignoring read/unread_only)
            pinned_placeholders = ",".join("?" * len(PINNED_SOURCES))
            pinned_sql = (
                "SELECT n.id, n.timestamp, n.level, n.title, n.message, "
                "COALESCE(nus.read_at, n.read) as read, n.source, n.data, n.user_id "
                "FROM notifications n "
                "LEFT JOIN notification_user_state nus ON n.id = nus.notification_id AND nus.user_id = ? "
                f"WHERE (n.user_id IS NULL OR n.user_id = ?) AND n.archived = 0 AND (n.user_id IS NOT NULL OR nus.archived_at IS NULL) AND n.source IN ({pinned_placeholders}) AND n.show_centre = 1"
                + hidden_pred
                + " ORDER BY n.timestamp DESC"
            )
            pinned_params: tuple = (user_id, user_id) + PINNED_SOURCES + hidden_params
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
                "n.show_centre = 1",
            ]
            params_list = [user_id, user_id] + list(PINNED_SOURCES)
            if hidden:
                conds.append(
                    f"(n.user_id IS NOT NULL OR n.source IS NULL OR n.source NOT IN ({hidden_placeholders}))"
                )
                params_list += sorted(hidden)
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
            # Read state for broadcasts is stored in notification_user_state.
            # The count applies the same filtering as list(), so the badge
            # never counts rows the Centre cannot show: rows marked
            # show_centre = 0 at insert time are excluded by their marking,
            # and broadcast rows by the reader's current hidden (muted /
            # centre-off) sources. Own unmarked rows are always counted,
            # including rows stored before a preference change.
            hidden = await self._hidden_sources(user_id)
            hidden_pred = ""
            hidden_params: tuple = ()
            if hidden:
                hidden_placeholders = ",".join("?" * len(hidden))
                hidden_pred = (
                    f" AND (n.user_id IS NOT NULL OR n.source IS NULL OR n.source NOT IN ({hidden_placeholders}))"
                )
                hidden_params = tuple(sorted(hidden))
            sql = (
                "SELECT COUNT(*) FROM notifications n "
                "LEFT JOIN notification_user_state nus ON n.id = nus.notification_id AND nus.user_id = ? "
                "WHERE (n.user_id IS NULL OR n.user_id = ?) AND (n.user_id IS NOT NULL OR nus.archived_at IS NULL) AND COALESCE(nus.read_at, n.read) = 0 AND n.show_centre = 1"
                + hidden_pred
            )
            params = (user_id, user_id) + hidden_params
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
        if user_id and await self._is_event_muted(user_id, event_type):
            return
        await self.add(
            title, message, level=level, source=event_type, user_id=user_id, webhook=webhook
        )

    async def _get_event_pref(self, user_id: str, event_type: str) -> dict:
        """One person's delivery pref for one event type (defaults if unset)."""
        async with self._db.execute(
            "SELECT muted, style, show_centre FROM notification_prefs "
            "WHERE user_id = ? AND event_type = ?",
            (user_id, event_type),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return {"muted": False, "style": "banner", "show_centre": True}
        return {"muted": bool(row[0]), "style": row[1], "show_centre": bool(row[2])}

    async def _pref_for(self, user_id: str | None, event_type: str) -> dict:
        """The delivery pref governing a notification for its recipient.

        Broadcast/system notifications (no recipient) are governed by the
        defaults: delivery settings are personal, and with no person there
        is nobody whose settings could apply.
        """
        if not user_id:
            return {"muted": False, "style": "banner", "show_centre": True}
        return await self._get_event_pref(user_id, event_type)

    async def _is_event_muted(self, user_id: str, event_type: str) -> bool:
        return (await self._get_event_pref(user_id, event_type))["muted"]

    async def live_delivery_allowed(self, user_id: str, source: str) -> bool:
        """Whether a live (toast / web-push) delivery of a ``source``
        notification may reach ``user_id`` right now.

        Personal rows are decided in add() against the recipient's prefs;
        a BROADCAST row has no recipient at insert time, so the fan-out
        points that know the recipients -- the SSE stream and the
        web-push sender -- consult this per person instead. Muted and
        style "none" suppress live delivery, as does an active Do Not
        Disturb window; centre visibility does NOT (it governs the
        Centre listing, not the toast).
        """
        pref = await self._get_event_pref(user_id, source)
        if pref["muted"] or pref["style"] == "none":
            return False
        return not await self.dnd_active(user_id)

    async def live_delivery_style(self, user_id: str, source: str) -> str:
        """``user_id``'s delivery style for ``source`` ("banner" default).

        The fan-out points consult this alongside live_delivery_allowed()
        to mark a broadcast frame persistent for recipients whose style
        is "alert": broadcast rows are inserted with the default pref,
        so add() cannot set the flag for them (#206).
        """
        return (await self._get_event_pref(user_id, source))["style"]

    async def _hidden_sources(self, user_id: str) -> set[str]:
        """Sources this user muted or hid from their Notification Centre."""
        async with self._db.execute(
            "SELECT event_type FROM notification_prefs "
            "WHERE user_id = ? AND (muted = 1 OR show_centre = 0)",
            (user_id,),
        ) as cur:
            return {row[0] for row in await cur.fetchall()}

    async def get_event_prefs(self, user_id: str) -> list[dict]:
        """Every event type with this user's prefs merged over the defaults."""
        async with self._db.execute(
            "SELECT event_type, muted, style, show_centre "
            "FROM notification_prefs WHERE user_id = ?",
            (user_id,),
        ) as cur:
            rows = {
                r[0]: {"muted": bool(r[1]), "style": r[2], "show_centre": bool(r[3])}
                for r in await cur.fetchall()
            }
        defaults = {"muted": False, "style": "banner", "show_centre": True}
        return [
            {"event_type": event_type, **rows.get(event_type, defaults)}
            for event_type in sorted(self.EVENT_TYPES)
        ]

    async def export_prefs(self) -> dict:
        """Every user's delivery prefs + DND, grouped by user (backup
        export, #620).

        Reads the prefs tables directly -- the per-user getters would need
        a user list this store does not own. Only stored rows are exported
        (no defaults are materialised), and no notification content is
        included: event types, delivery style, centre visibility, DND.
        """
        export: dict[str, dict] = {}
        async with self._db.execute(
            "SELECT user_id, event_type, muted, style, show_centre "
            "FROM notification_prefs ORDER BY user_id, event_type"
        ) as cur:
            pref_rows = await cur.fetchall()
        for user_id, event_type, muted, style, show_centre in pref_rows:
            entry = export.setdefault(user_id, {"events": []})
            entry["events"].append({
                "event_type": event_type,
                "muted": bool(muted),
                "style": style,
                "show_centre": bool(show_centre),
            })
        async with self._db.execute(
            "SELECT user_id, enabled, start, end, timezone "
            "FROM notification_dnd ORDER BY user_id"
        ) as cur:
            dnd_rows = await cur.fetchall()
        for user_id, enabled, start, end, timezone in dnd_rows:
            entry = export.setdefault(user_id, {"events": []})
            entry["dnd"] = {
                "enabled": bool(enabled),
                "start": start,
                "end": end,
                "timezone": timezone,
            }
        return export

    async def set_event_pref(
        self,
        user_id: str,
        event_type: str,
        *,
        muted: bool | None = None,
        style: str | None = None,
        show_centre: bool | None = None,
    ) -> dict:
        """Partially update one person's delivery pref for an event type.

        Only the fields passed are written: a single upsert whose conflict
        clause touches only the specified columns, so two overlapping
        partial updates cannot restore each other's stale values the way
        a read-modify-write of the whole row could. The insert side uses
        defaults for unspecified fields, which only matters for a
        brand-new row.
        """
        if style is not None and style not in DELIVERY_STYLES:
            raise ValueError(f"unknown delivery style: {style!r}")
        if muted is None and style is None and show_centre is None:
            return {
                "event_type": event_type,
                **await self._get_event_pref(user_id, event_type),
            }
        assignments = []
        if muted is not None:
            assignments.append("muted = excluded.muted")
        if style is not None:
            assignments.append("style = excluded.style")
        if show_centre is not None:
            assignments.append("show_centre = excluded.show_centre")
        await self._db.execute(
            "INSERT INTO notification_prefs "
            "(user_id, event_type, muted, style, show_centre) "
            "VALUES (?, ?, ?, ?, ?) "
            f"ON CONFLICT(user_id, event_type) DO UPDATE SET {', '.join(assignments)}",
            (
                user_id,
                event_type,
                int(bool(muted)) if muted is not None else 0,
                style if style is not None else "banner",
                int(bool(show_centre)) if show_centre is not None else 1,
            ),
        )
        await self._db.commit()
        return {
            "event_type": event_type,
            **await self._get_event_pref(user_id, event_type),
        }

    async def set_event_muted(self, user_id: str, event_type: str, muted: bool) -> None:
        await self.set_event_pref(user_id, event_type, muted=muted)

    async def get_dnd(self, user_id: str) -> dict:
        """One person's Do Not Disturb settings (defaults if unset)."""
        async with self._db.execute(
            "SELECT enabled, start, end, timezone FROM notification_dnd "
            "WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return dict(DEFAULT_DND)
        return {
            "enabled": bool(row[0]),
            "start": row[1],
            "end": row[2],
            "timezone": row[3],
        }

    async def set_dnd(
        self,
        user_id: str,
        *,
        enabled: bool,
        start: str,
        end: str,
        timezone: str = "UTC",
    ) -> dict:
        """Persist one person's DND window, evaluated in their timezone."""
        if not _valid_hhmm(start) or not _valid_hhmm(end):
            raise ValueError("DND start/end must be zero-padded HH:MM times")
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            raise ValueError(f"unknown timezone: {timezone!r}") from None
        await self._db.execute(
            "INSERT OR REPLACE INTO notification_dnd "
            "(user_id, enabled, start, end, timezone) VALUES (?, ?, ?, ?, ?)",
            (user_id, int(bool(enabled)), start, end, timezone),
        )
        await self._db.commit()
        return {
            "enabled": bool(enabled),
            "start": start,
            "end": end,
            "timezone": timezone,
        }

    async def dnd_active(
        self, user_id: str, now: datetime | None = None
    ) -> bool:
        """Whether DND is in effect for this person at ``now``.

        ``now`` is an aware datetime (default: the current instant); the
        window is compared in the person's own timezone, never server
        time. A window whose start equals its end is active all day; a
        window whose start is after its end wraps midnight.
        """
        dnd = await self.get_dnd(user_id)
        if not dnd["enabled"]:
            return False
        if now is None:
            now = datetime.now(dt_timezone.utc)
        try:
            tz = ZoneInfo(dnd["timezone"])
        except (ZoneInfoNotFoundError, ValueError):
            tz = dt_timezone.utc
        hhmm = now.astimezone(tz).strftime("%H:%M")
        start, end = dnd["start"], dnd["end"]
        if start == end:
            return True
        if start < end:
            return start <= hhmm < end
        return hhmm >= start or hhmm < end

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
