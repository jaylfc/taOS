"""BrowserApp v2 stores.

- BrowserStore  — regular SQLite, holds profiles/history/bookmarks/caps/push/windows
- BrowserCookieStore — SQLCipher-encrypted, holds cookies; per-user key

Both stores key every row on user_id for OS-grade multi-user isolation.
The query helpers refuse to operate without a user_id argument.
"""
from __future__ import annotations

from tinyagentos.base_store import BaseStore
from tinyagentos.routes.desktop_browser.schema import BROWSER_SCHEMA
# BrowserCookieStore lives in cookie_store.py; re-exported here for back-compat.
from tinyagentos.routes.desktop_browser.cookie_store import BrowserCookieStore  # noqa: F401

from tinyagentos.routes.desktop_browser.store_mixins.profiles import ProfilesMixin
from tinyagentos.routes.desktop_browser.store_mixins.windows import WindowsMixin
from tinyagentos.routes.desktop_browser.store_mixins.history import HistoryMixin
from tinyagentos.routes.desktop_browser.store_mixins.bookmarks import BookmarksMixin
from tinyagentos.routes.desktop_browser.store_mixins.pins import PinsMixin
from tinyagentos.routes.desktop_browser.store_mixins.capabilities import CapabilitiesMixin
from tinyagentos.routes.desktop_browser.store_mixins.drive_sessions import DriveSessionsMixin
from tinyagentos.routes.desktop_browser.store_mixins.site_permissions import SitePermissionsMixin
from tinyagentos.routes.desktop_browser.store_mixins.push import PushMixin


class BrowserStore(
    ProfilesMixin, WindowsMixin, HistoryMixin, BookmarksMixin, PinsMixin,
    CapabilitiesMixin, DriveSessionsMixin, SitePermissionsMixin, PushMixin,
    BaseStore,
):
    """Regular SQLite store: profiles, history, bookmarks, capabilities,
    push subscriptions, persisted browser-window state.

    Every accessor takes a user_id and refuses to operate without one.
    """
    SCHEMA = BROWSER_SCHEMA
