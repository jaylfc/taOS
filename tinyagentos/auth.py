from __future__ import annotations
import hashlib
import json
import logging
import os
import secrets
import time
from collections.abc import Iterator
from pathlib import Path

from tinyagentos.shortcuts.capabilities import default_caps_for_admin, default_caps_for_new_user

logger = logging.getLogger(__name__)


class _PersistentSessions:
    """Dict-like wrapper that reads/writes sessions from a JSON file on every access.

    Session entries are dicts: {user_id, expires_at, long_lived}.
    Old float entries (single-user legacy) are tolerated and treated as the
    first user's session.
    """

    def __init__(self, path: Path):
        self._path = path

    def _load(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data))

    def __getitem__(self, key: str):
        return self._load()[key]

    def __setitem__(self, key: str, value) -> None:
        data = self._load()
        data[key] = value
        self._save(data)

    def __delitem__(self, key: str) -> None:
        data = self._load()
        del data[key]
        self._save(data)

    def __contains__(self, key: object) -> bool:
        return key in self._load()

    def __iter__(self) -> Iterator[str]:
        return iter(self._load())

    def get(self, key: str, default=None):
        return self._load().get(key, default)

    def pop(self, key: str, *args):
        data = self._load()
        result = data.pop(key, *args)
        self._save(data)
        return result

    def items(self):
        return list(self._load().items())


def hash_password(password: str, salt: str = "") -> str:
    if not salt:
        salt = secrets.token_hex(16)
    hashed = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return f"{salt}:{hashed}"


def verify_password(password: str, stored: str) -> bool:
    salt = stored.split(":")[0]
    return hash_password(password, salt) == stored


class AuthManager:
    """Multi-user auth manager.

    User records live in ``data/.auth_user.json``.  The envelope is::

        {
          "users": [...],
          "current_user_id": "<id>"
        }

    Each full user record::

        {
          "id", "username", "full_name", "email",
          "password_hash", "created_at", "last_login_at", "is_admin"
        }

    Pending (invited) users lack ``password_hash`` and carry
    ``pending_invite`` (8-digit numeric string) instead.

    The legacy ``.auth_password`` file is still honoured for installs
    that predate onboarding.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self._password_file = data_dir / ".auth_password"
        self._user_file = data_dir / ".auth_user.json"
        self._sessions_file = data_dir / ".auth_sessions"
        self._sessions = _PersistentSessions(self._sessions_file)
        self.session_ttl = 86400 * 7  # 7 days, default
        self.long_session_ttl = 86400 * 365  # 1 year for "stay signed in"

    # ------------------------------------------------------------------ #
    #  Profile storage helpers                                             #
    # ------------------------------------------------------------------ #

    def _read_users(self) -> dict:
        if self._user_file.exists():
            try:
                return json.loads(self._user_file.read_text())
            except (json.JSONDecodeError, OSError):
                return {"users": [], "current_user_id": None}
        return {"users": [], "current_user_id": None}

    def _write_users(self, data: dict) -> None:
        self._user_file.parent.mkdir(parents=True, exist_ok=True)
        self._user_file.write_text(json.dumps(data, indent=2))

    # ------------------------------------------------------------------ #
    #  Predicates                                                          #
    # ------------------------------------------------------------------ #

    def is_configured(self) -> bool:
        return bool(self._read_users().get("users")) or self._password_file.exists()

    def needs_onboarding(self) -> bool:
        return not self.is_configured()

    def is_multi_user(self) -> bool:
        """True when two or more fully-registered users exist."""
        users = self._read_users().get("users", [])
        active = [u for u in users if "password_hash" in u]
        return len(active) >= 2

    # ------------------------------------------------------------------ #
    #  Public user projection                                              #
    # ------------------------------------------------------------------ #

    def _public_user(self, record: dict) -> dict:
        raw_caps = record.get("capabilities")
        caps: list[str] = list(raw_caps) if raw_caps is not None else []
        return {
            "id": record.get("id", ""),
            "username": record.get("username", ""),
            "full_name": record.get("full_name", ""),
            "email": record.get("email", ""),
            "is_admin": bool(record.get("is_admin", False)),
            "pending": "pending_invite" in record,
            "last_login_at": record.get("last_login_at"),
            "created_at": record.get("created_at"),
            "capabilities": caps,
        }

    # ------------------------------------------------------------------ #
    #  User lookups                                                        #
    # ------------------------------------------------------------------ #

    def get_primary_user(self) -> dict | None:
        """Return the public profile of the primary/admin user (first in the list)."""
        users = self._read_users().get("users", [])
        if users:
            return self._public_user(users[0])
        return None

    def get_user_by_id(self, user_id: str) -> dict | None:
        """Return the public profile for *user_id*, or None if not found."""
        record = self._find_user_by_id(user_id)
        if record:
            return self._public_user(record)
        return None

    def find_user(self, username: str) -> dict | None:
        for u in self._read_users().get("users", []):
            if u.get("username") == username:
                return u
        return None

    def _find_user_by_id(self, user_id: str) -> dict | None:
        for u in self._read_users().get("users", []):
            if u.get("id") == user_id:
                return u
        return None

    def get_user(self, token: str | None = None) -> dict | None:
        """Return public profile.

        When *token* is given, return the user who owns that session.
        Otherwise fall back to the first user (back-compat).
        """
        if token:
            user_id = self.validate_session(token)
            if user_id:
                record = self._find_user_by_id(user_id)
                if record:
                    return self._public_user(record)
        users = self._read_users().get("users", [])
        if users:
            return self._public_user(users[0])
        if self._password_file.exists():
            return {"username": "admin", "full_name": "", "email": "", "legacy": True}
        return None

    def list_users(self) -> list[dict]:
        """Return public profiles for all users (admin and pending)."""
        users = self._read_users().get("users", [])
        result = []
        for u in users:
            pub = self._public_user(u)
            if pub["pending"]:
                pub["invite_code"] = u.get("pending_invite", "")
            result.append(pub)
        return result

    # ------------------------------------------------------------------ #
    #  First-user setup (admin path)                                       #
    # ------------------------------------------------------------------ #

    def setup_user(self, username: str, full_name: str, email: str, password: str) -> dict:
        users = self._read_users()
        if users.get("users"):
            raise ValueError("a user is already configured")
        if not username or not password:
            raise ValueError("username and password are required")
        record = {
            "id": secrets.token_urlsafe(8),
            "username": username,
            "full_name": full_name,
            "email": email,
            "password_hash": hash_password(password),
            "created_at": int(time.time()),
            "is_admin": True,
            "capabilities": list(default_caps_for_admin()),
        }
        users["users"] = [record]
        users["current_user_id"] = record["id"]
        self._write_users(users)
        return self._public_user(record)

    # ------------------------------------------------------------------ #
    #  Invite lifecycle                                                    #
    # ------------------------------------------------------------------ #

    def add_user_invite(self, username: str, invited_by_username: str) -> str:
        """Create a pending user and return the 8-digit invite code."""
        if not username:
            raise ValueError("username is required")
        data = self._read_users()
        for u in data.get("users", []):
            if u.get("username") == username:
                raise ValueError(f"username '{username}' is already taken")
        code = f"{secrets.randbelow(100_000_000):08d}"
        record = {
            "id": secrets.token_urlsafe(8),
            "username": username,
            "pending_invite": code,
            "invited_at": int(time.time()),
            "invited_by": invited_by_username,
            "is_admin": False,
        }
        data.setdefault("users", []).append(record)
        self._write_users(data)
        return code

    def complete_invite(
        self,
        username: str,
        invite_code: str,
        full_name: str,
        email: str,
        password: str,
    ) -> dict:
        """Convert a pending invite into a full user record."""
        if not password or len(password) < 4:
            raise ValueError("password must be at least 4 characters")
        data = self._read_users()
        users = data.get("users", [])
        target_idx = None
        for i, u in enumerate(users):
            if u.get("username") == username and u.get("pending_invite") == invite_code:
                target_idx = i
                break
        if target_idx is None:
            raise ValueError("invalid invite code or username")
        record = users[target_idx]
        record.pop("pending_invite", None)
        record.pop("invited_at", None)
        record.pop("invited_by", None)
        record["full_name"] = full_name
        record["email"] = email
        record["password_hash"] = hash_password(password)
        record["created_at"] = int(time.time())
        record["capabilities"] = list(default_caps_for_new_user())
        users[target_idx] = record
        data["users"] = users
        self._write_users(data)
        return self._public_user(record)

    def admin_reset_password(self, username: str, by_admin_username: str) -> str:
        """Re-issue an invite code, marking the user pending again."""
        caller = self.find_user(by_admin_username)
        if not caller or not caller.get("is_admin"):
            raise ValueError("caller is not an admin")
        if username == by_admin_username:
            raise ValueError("cannot reset your own password via admin reset")
        data = self._read_users()
        users = data.get("users", [])
        for i, u in enumerate(users):
            if u.get("username") == username:
                if "password_hash" not in u and "pending_invite" not in u:
                    raise ValueError("user record is malformed")
                code = f"{secrets.randbelow(100_000_000):08d}"
                u.pop("password_hash", None)
                u["pending_invite"] = code
                u["invited_at"] = int(time.time())
                u["invited_by"] = by_admin_username
                users[i] = u
                data["users"] = users
                self._write_users(data)
                # Revoke existing sessions
                self.revoke_user_sessions(u["id"])
                return code
        raise ValueError(f"user '{username}' not found")

    # ------------------------------------------------------------------ #
    #  Password ops                                                        #
    # ------------------------------------------------------------------ #

    def set_password(self, password: str) -> None:
        """Legacy code path — keeps existing tests + the simple-setup endpoint working."""
        self._password_file.parent.mkdir(parents=True, exist_ok=True)
        self._password_file.write_text(hash_password(password))

    def check_password(self, password: str, username: str | None = None) -> tuple[bool, dict | None]:
        """Verify credentials.

        Returns ``(ok, user_record)``. When a pending user's invite code is
        supplied as the password, returns the pending record so the route
        layer can set ``needs_onboarding=True``.

        Legacy bool return is no longer used but callers doing
        ``if auth_mgr.check_password(...)`` still work because
        ``(True, record)`` is truthy.
        """
        users = self._read_users().get("users", [])

        if users:
            candidates = users
            if username:
                candidates = [u for u in users if u.get("username") == username]
            for u in candidates:
                # Full user — verify password
                if "password_hash" in u:
                    if verify_password(password, u.get("password_hash", "")):
                        return (True, u)
                # Pending user — accept invite code as "password"
                elif "pending_invite" in u:
                    if u["pending_invite"] == password:
                        return (True, u)
            return (False, None)

        # Legacy file fallback
        if not self._password_file.exists():
            return (False, None)
        stored = self._password_file.read_text().strip()
        if verify_password(password, stored):
            return (True, None)
        return (False, None)

    def change_password(self, username: str, current_password: str, new_password: str) -> bool:
        """Self-change, requires current password."""
        if not new_password or len(new_password) < 4:
            return False
        data = self._read_users()
        users = data.get("users", [])
        for i, u in enumerate(users):
            if u.get("username") == username:
                if not verify_password(current_password, u.get("password_hash", "")):
                    return False
                u["password_hash"] = hash_password(new_password)
                users[i] = u
                data["users"] = users
                self._write_users(data)
                return True
        return False

    def update_profile(self, username: str, full_name: str | None, email: str | None) -> dict:
        """Update own profile fields."""
        data = self._read_users()
        users = data.get("users", [])
        for i, u in enumerate(users):
            if u.get("username") == username:
                if full_name is not None:
                    u["full_name"] = full_name
                if email is not None:
                    u["email"] = email
                users[i] = u
                data["users"] = users
                self._write_users(data)
                return self._public_user(u)
        raise ValueError(f"user '{username}' not found")

    def delete_user(self, username: str, by_admin_username: str) -> None:
        """Remove a user. Admin only, can't delete self, can't delete last admin."""
        caller = self.find_user(by_admin_username)
        if not caller or not caller.get("is_admin"):
            raise ValueError("caller is not an admin")
        if username == by_admin_username:
            raise ValueError("cannot delete yourself")
        data = self._read_users()
        users = data.get("users", [])
        target = None
        for u in users:
            if u.get("username") == username:
                target = u
                break
        if target is None:
            raise ValueError(f"user '{username}' not found")
        # Guard: don't remove the last admin
        if target.get("is_admin"):
            admins = [u for u in users if u.get("is_admin") and u.get("username") != username]
            if not admins:
                raise ValueError("cannot delete the last admin")
        # Revoke sessions then remove
        self.revoke_user_sessions(target["id"])
        data["users"] = [u for u in users if u.get("username") != username]
        self._write_users(data)

    # ------------------------------------------------------------------ #
    #  Sessions                                                            #
    # ------------------------------------------------------------------ #

    def create_session(self, user_id: str = "", long_lived: bool = False) -> str:
        token = secrets.token_urlsafe(32)
        ttl = self.long_session_ttl if long_lived else self.session_ttl
        self._sessions[token] = {
            "user_id": user_id,
            "expires_at": time.time() + ttl,
            "long_lived": long_lived,
        }
        return token

    def session_ttl_for(self, long_lived: bool = False) -> int:
        return self.long_session_ttl if long_lived else self.session_ttl

    def _get_session_entry(self, token: str) -> dict | None:
        entry = self._sessions.get(token)
        if entry is None:
            return None
        # Legacy: old entries were plain floats (expires_at)
        if isinstance(entry, (int, float)):
            return {"user_id": "", "expires_at": float(entry), "long_lived": False}
        return entry

    def validate_session(self, token: str) -> str | None:
        """Return user_id if the session is valid, else None."""
        entry = self._get_session_entry(token)
        if entry is None:
            return None
        if time.time() >= entry["expires_at"]:
            try:
                del self._sessions[token]
            except (KeyError, Exception):
                pass
            return None
        return entry.get("user_id", "") or ""

    def revoke_session(self, token: str) -> None:
        self._sessions.pop(token, None)

    def revoke_user_sessions(self, user_id: str) -> int:
        """Wipe all sessions for a user. Returns count revoked."""
        to_revoke = []
        for token, entry in self._sessions.items():
            if isinstance(entry, (int, float)):
                # Legacy entry with no user_id — skip unless user_id is empty string
                if user_id == "":
                    to_revoke.append(token)
            elif entry.get("user_id") == user_id:
                to_revoke.append(token)
        for token in to_revoke:
            self._sessions.pop(token, None)
        return len(to_revoke)

    def session_user(self, token: str) -> dict | None:
        """Return public profile of the user owning this session."""
        user_id = self.validate_session(token)
        if user_id is None:
            return None
        if user_id:
            record = self._find_user_by_id(user_id)
            if record:
                return self._public_user(record)
        # Legacy or no user_id — return first user
        return self.get_user()

    def cleanup_sessions(self) -> None:
        now = time.time()
        expired = []
        for token, entry in self._sessions.items():
            exp = entry["expires_at"] if isinstance(entry, dict) else float(entry)
            if now >= exp:
                expired.append(token)
        for t in expired:
            del self._sessions[t]

    # ------------------------------------------------------------------ #
    #  Local token (programmatic / script access)                         #
    # ------------------------------------------------------------------ #

    def local_token_path(self) -> Path:
        """Return the path to the persistent local auth token file."""
        return self.data_dir / ".auth_local_token"

    def get_local_token(self) -> str:
        """Return the local auth token, creating the file if it does not exist.

        The file is written with 0600 permissions so only the owner can read
        it. Possession of the file is treated as same-user-on-the-host trust.
        Never rotated automatically — delete the file to force regeneration.
        """
        path = self.local_token_path()
        if path.exists():
            return path.read_text().strip()
        token = secrets.token_urlsafe(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token)
        os.chmod(path, 0o600)
        logger.info("local auth token written to %s", path)
        return token

    def validate_local_token(self, presented: str) -> bool:
        """Return True if *presented* matches the on-disk local token.

        Always re-reads the file so rotating the file takes effect on the
        next request. Uses ``secrets.compare_digest`` to avoid timing leaks.
        """
        if not presented:
            return False
        path = self.local_token_path()
        if not path.exists():
            return False
        stored = path.read_text().strip()
        return secrets.compare_digest(presented, stored)

    def update_last_login(self, user_id: str) -> None:
        data = self._read_users()
        users = data.get("users", [])
        for i, u in enumerate(users):
            if u.get("id") == user_id:
                u["last_login_at"] = int(time.time())
                users[i] = u
                data["users"] = users
                self._write_users(data)
                return
