"""SQL schema constants for the BrowserApp v2 stores.

Two databases:

- BROWSER_SCHEMA — applied to the regular SQLite DB (browser.sqlite3).
  Holds profiles, history, bookmarks, agent capabilities, push
  subscriptions, and persisted browser-window state.

- COOKIE_SCHEMA — applied to the SQLCipher-encrypted DB
  (browser_cookies.sqlite3). Holds the cookie jar.

Every table keys on user_id for OS-grade multi-user isolation.
"""
from __future__ import annotations


BROWSER_SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
  user_id        TEXT NOT NULL,
  profile_id     TEXT NOT NULL,
  name           TEXT NOT NULL,
  color          TEXT,
  created_at     INTEGER NOT NULL,
  PRIMARY KEY (user_id, profile_id)
);

CREATE TABLE IF NOT EXISTS profile_init (
  user_id        TEXT NOT NULL PRIMARY KEY,
  initialized_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS history (
  user_id        TEXT NOT NULL,
  profile_id     TEXT NOT NULL,
  url            TEXT NOT NULL,
  title          TEXT,
  visited_at     INTEGER NOT NULL,
  visit_count    INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_history_search
  ON history (user_id, profile_id, url, title);

CREATE TABLE IF NOT EXISTS bookmarks (
  user_id        TEXT NOT NULL,
  profile_id     TEXT NOT NULL,
  bookmark_id    TEXT NOT NULL,
  folder_path    TEXT NOT NULL DEFAULT '/',
  url            TEXT NOT NULL,
  title          TEXT NOT NULL,
  created_at     INTEGER NOT NULL,
  PRIMARY KEY (user_id, profile_id, bookmark_id)
);

CREATE TABLE IF NOT EXISTS agent_capabilities (
  user_id        TEXT NOT NULL,
  profile_id     TEXT NOT NULL,
  agent_id       TEXT NOT NULL,
  host_pattern   TEXT NOT NULL,
  perms          TEXT NOT NULL,
  granted_at     INTEGER NOT NULL,
  PRIMARY KEY (user_id, profile_id, agent_id, host_pattern)
);

CREATE TABLE IF NOT EXISTS push_subscriptions (
  user_id        TEXT NOT NULL,
  device_id      TEXT NOT NULL,
  endpoint       TEXT NOT NULL,
  p256dh_key     TEXT NOT NULL,
  auth_key       TEXT NOT NULL,
  user_agent     TEXT,
  created_at     INTEGER NOT NULL,
  last_seen_at   INTEGER NOT NULL,
  PRIMARY KEY (user_id, device_id)
);

CREATE TABLE IF NOT EXISTS browser_windows (
  user_id        TEXT NOT NULL,
  window_id      TEXT NOT NULL,
  profile_id     TEXT NOT NULL,
  active_tab_id  TEXT,
  state          TEXT NOT NULL,
  updated_at     INTEGER NOT NULL,
  PRIMARY KEY (user_id, window_id)
);
"""


COOKIE_SCHEMA = """
CREATE TABLE IF NOT EXISTS cookies (
  user_id        TEXT NOT NULL,
  profile_id     TEXT NOT NULL,
  host           TEXT NOT NULL,
  path           TEXT NOT NULL,
  name           TEXT NOT NULL,
  value          TEXT NOT NULL,
  expires_at     INTEGER,
  http_only      INTEGER NOT NULL,
  secure         INTEGER NOT NULL,
  same_site      TEXT,
  PRIMARY KEY (user_id, profile_id, host, path, name)
);
CREATE INDEX IF NOT EXISTS idx_cookies_lookup
  ON cookies (user_id, profile_id, host);
"""
