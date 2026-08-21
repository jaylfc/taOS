from __future__ import annotations
import functools
import hashlib
import json
import logging
import os
import secrets
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
from fastapi import HTTPException, Request

from tinyagentos.atomic_io import atomic_write_text
from tinyagentos.shortcuts.capabilities import default_caps_for_admin, default_caps_for_new_user

_ph = PasswordHasher()

# Lock that makes the SHA-256→argon2 upgrade read-modify-write atomic across threads.
_hash_upgrade_lock = threading.Lock()

logger = logging.getLogger(__name__)


def _serialized(method):
    """Hold the instance's account-store lock for the whole method.

    Every mutator reads the store, edits the parsed dict and writes it back.
    Atomic writes make each *write* all-or-nothing, but two concurrent
    read-modify-write cycles still lose one of the two edits -- invite a user
    while another request renames one and the rename can vanish. Serialising
    the cycle is the missing half. Re-entrant so a mutator may call another.
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._users_lock:
            return method(self, *args, **kwargs)
    return wrapper


class AuthStoreCorruptError(RuntimeError):
    """Raised when the account store exists but cannot be parsed.

    A *missing* store means a fresh install; a *corrupt* one means the
    accounts are still there and we simply cannot read them.  Conflating the
    two is what turned a truncated ``.auth_user.json`` into a first-run
    onboarding screen on 2026-08-21 — an unauthenticated caller was one form
    submission away from claiming the box and overwriting the real users.
    Callers must fail closed on this rather than treat it as "no users".
    """


class _PersistentSessions:
    """Dict-like wrapper that reads/writes sessions from a JSON file on every access.

    Session entries are dicts: {user_id, expires_at, long_lived}.
    Old float entries (single-user legacy) are tolerated and treated as the
    first user's session.

    Thread-safe: all mutating operations (set, delete, pop) are protected by a
    lock to prevent lost updates from concurrent read-modify-write cycles.
    """

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()

    def _load(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
        self._prune_expired(data)
        return data

    def _save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._save_pruned(data)

    def _save_pruned(self, data: dict) -> None:
        self._prune_expired(data)
        atomic_write_text(self._path, json.dumps(data), mode=0o600)

    def _prune_expired(self, data: dict) -> None:
        now = time.time()
        expired_keys = [
            token for token, entry in data.items()
            if isinstance(entry, dict) and entry.get("expires_at") is not None and now >= entry.get("expires_at")
        ]
        for token in expired_keys:
            del data[token]

    def __getitem__(self, key: str):
        return self._load()[key]

    def __setitem__(self, key: str, value) -> None:
        with self._lock:
            data = self._load()
            data[key] = value
            self._save(data)

    def __delitem__(self, key: str) -> None:
        with self._lock:
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
        with self._lock:
            data = self._load()
            result = data.pop(key, *args)
            self._save(data)
            return result

    def items(self):
        return list(self._load().items())


def hash_password(password: str, salt: str = "") -> str:
    """Hash a password with argon2id.

    The ``salt`` parameter is accepted for backward-compatibility but ignored —
    argon2 manages its own salt internally.  Old callers that passed an explicit
    salt (e.g. legacy tests) will silently receive a fresh argon2 hash.
    """
    return _ph.hash(password)


def _hash_password_sha256(password: str, salt: str) -> str:
    """Internal: produce the old SHA-256 hash for migration verification only."""
    hashed = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return f"{salt}:{hashed}"


def verify_password(password: str, stored: str) -> bool:
    """Verify *password* against *stored* hash.

    Supports both the legacy ``<salt>:<sha256hex>`` format and the new argon2
    format (identified by the ``$argon2`` prefix).  Callers that need to know
    whether the hash was upgraded should use ``verify_and_maybe_rehash``.
    """
    if stored.startswith("$argon2"):
        try:
            return _ph.verify(stored, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
    # Legacy SHA-256 format: "<salt>:<hex>"
    parts = stored.split(":", 1)
    if len(parts) != 2:
        return False
    salt = parts[0]
    return _hash_password_sha256(password, salt) == stored


def verify_and_maybe_rehash(password: str, stored: str) -> tuple[bool, str | None]:
    """Verify *password* and return ``(ok, new_hash_or_None)``.

    If the stored hash is the legacy SHA-256 format and the password is correct,
    returns a fresh argon2 hash in the second element so the caller can
    transparently upgrade the stored value.  Returns ``(True, None)`` when the
    stored hash is already argon2.  Returns ``(False, None)`` on mismatch.
    """
    if stored.startswith("$argon2"):
        try:
            ok = _ph.verify(stored, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False, None
        # argon2-cffi can also signal that re-hash is needed (param changes)
        if ok and _ph.check_needs_rehash(stored):
            return True, _ph.hash(password)
        return ok, None
    # Legacy SHA-256
    if not verify_password(password, stored):
        return False, None
    return True, _ph.hash(password)


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
        # Serialises read-modify-write cycles on the account store; see
        # _serialized. Re-entrant so nested mutators do not self-deadlock.
        self._users_lock = threading.RLock()
        self.session_ttl = 86400 * 7  # 7 days, default
        self.long_session_ttl = 86400 * 30  # 30 days for "stay signed in"
        self._prune_sessions_on_startup()

    def _prune_sessions_on_startup(self) -> None:
        """Prune expired sessions at startup AND persist the shrunk file.

        _load() prunes in memory on every read and _save() prunes on every
        write, so correctness never depends on this -- but without a write-back
        here, a grown sessions file only shrinks on the next session mint.
        Load (which prunes) then save once, so startup leaves the file small."""
        if not self._sessions_file.exists():
            return
        try:
            before = len(json.loads(self._sessions_file.read_text()))
        except (json.JSONDecodeError, OSError):
            return
        data = self._sessions._load()
        self._sessions._save(data)
        removed = before - len(data)
        if removed:
            logger.info(
                "Pruned %s expired auth sessions at startup (%s -> %s live)",
                removed, before, len(data),
            )
        if len(data) > 50000:
            logger.warning(
                "Auth sessions count %s after pruning is still excessive; "
                "sessions are being minted faster than they expire", len(data),
            )

    # ------------------------------------------------------------------ #
    #  Profile storage helpers                                             #
    # ------------------------------------------------------------------ #

    def _read_users(self) -> dict:
        """Return the account store, or raise if it is present but unreadable.

        Only a *missing* file yields the empty store — see
        :class:`AuthStoreCorruptError` for why an unparseable one must not.
        """
        if self._user_file.exists():
            try:
                raw = self._user_file.read_text()
            except OSError as exc:
                raise AuthStoreCorruptError(
                    f"cannot read account store {self._user_file}: {exc}"
                ) from exc
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise AuthStoreCorruptError(
                    f"account store {self._user_file} is not valid JSON "
                    f"({len(raw)} bytes): {exc}"
                ) from exc
            if not isinstance(data, dict):
                raise AuthStoreCorruptError(
                    f"account store {self._user_file} is a "
                    f"{type(data).__name__}, expected an object"
                )
            # Every store this code has ever written carries a list-valued
            # "users". Anything else -- {}, {"users": null} -- parses as JSON
            # but would read as "no accounts", which is the exact conclusion
            # this class exists to prevent.
            if not isinstance(data.get("users"), list):
                raise AuthStoreCorruptError(
                    f"account store {self._user_file} has no 'users' list "
                    f"(found {type(data.get('users')).__name__})"
                )
            return data
        return {"users": [], "current_user_id": None}

    def _write_users(self, data: dict) -> None:
        atomic_write_text(
            self._user_file, json.dumps(data, indent=2), mode=0o600,
        )

    # ------------------------------------------------------------------ #
    #  Predicates                                                          #
    # ------------------------------------------------------------------ #

    def is_configured(self) -> bool:
        """True when this install already has an account.

        This is the single gate every onboarding path consults, so a store we
        cannot parse must answer True: the accounts exist, we just cannot read
        them.  Answering False would offer the create-your-account form to
        whoever asked.
        """
        try:
            users = self._read_users().get("users")
        except AuthStoreCorruptError:
            logger.error(
                "Account store %s is unreadable; refusing to report this "
                "install as unconfigured. Recover it with "
                "'taos recover-password' or restore the file from a backup.",
                self._user_file,
            )
            return True
        return bool(users) or self._password_file.exists()

    def needs_onboarding(self) -> bool:
        return not self.is_configured()

    def is_multi_user(self) -> bool:
        """True when two or more fully-registered users exist.

        A display hint (which login form to render), so an unreadable store
        degrades to the single-user answer rather than raising — the gate
        that matters, :meth:`is_configured`, already failed closed.
        """
        try:
            users = self._read_users().get("users", [])
        except AuthStoreCorruptError:
            return False
        active = [u for u in users if "password_hash" in u]
        return len(active) >= 2

    # ------------------------------------------------------------------ #
    #  Public user projection                                              #
    # ------------------------------------------------------------------ #

    def _public_user(self, record: dict) -> dict:
        raw_caps = record.get("capabilities")
        if raw_caps is not None:
            caps: list[str] = list(raw_caps)
        else:
            # Legacy record predates capabilities — apply sensible defaults so
            # upgrades don't silently lock users out of every shortcut.
            users = self._read_users().get("users", [])
            is_primary = bool(users) and users[0].get("id") == record.get("id")
            if is_primary or record.get("is_admin"):
                caps = list(default_caps_for_admin())
            else:
                caps = list(default_caps_for_new_user())
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

    def find_user_by_email(self, email: str) -> dict | None:
        """Return the user record whose email matches (case-insensitive), or None."""
        email_lower = email.lower()
        for u in self._read_users().get("users", []):
            stored = (u.get("email") or "").lower()
            if stored and stored == email_lower:
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

    @_serialized
    def setup_user(self, username: str, full_name: str, email: str, password: str) -> dict:
        users = self._read_users()
        if users.get("users"):
            raise ValueError("a user is already configured")
        if not username:
            raise ValueError("username and password are required")
        if not password or len(password) < 8:
            raise ValueError("password must be at least 8 characters")
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

    @_serialized
    def add_user_invite(self, username: str, invited_by_username: str) -> str:
        """Create a pending user and return a high-entropy invite code."""
        if not username:
            raise ValueError("username is required")
        data = self._read_users()
        for u in data.get("users", []):
            if u.get("username") == username:
                raise ValueError(f"username '{username}' is already taken")
        code = secrets.token_urlsafe(16)
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

    @_serialized
    def complete_invite(
        self,
        username: str,
        invite_code: str,
        full_name: str,
        email: str,
        password: str,
    ) -> dict:
        """Convert a pending invite into a full user record."""
        if not password or len(password) < 8:
            raise ValueError("password must be at least 8 characters")
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

    @_serialized
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
                code = secrets.token_urlsafe(16)
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
        atomic_write_text(
            self._password_file, hash_password(password), mode=0o600,
        )

    @_serialized
    def recover_password(self, new_password: str, username: str | None = None) -> str:
        """Offline recovery: force-set an account's password without the old one.

        For use by the ``recover-password`` CLI when the admin is locked out.
        Works across every store shape: sets the ``password_hash`` on the
        matching user record (found by ``username``, or the sole user in
        single-user mode), or writes the legacy ``.auth_password`` when there
        is no user record yet. All of that user's existing sessions are
        revoked so a leaked/forgotten-password session cannot survive the
        reset. Returns the username reset (or ``"(legacy)"``).
        """
        data = self._read_users()
        users = data.get("users", [])
        if users:
            if username:
                target_idx = next(
                    (i for i, u in enumerate(users) if u.get("username") == username),
                    None,
                )
                if target_idx is None:
                    known = ", ".join(u.get("username", "?") for u in users)
                    raise ValueError(
                        f"user '{username}' not found (known users: {known})"
                    )
            elif len(users) == 1:
                target_idx = 0
            else:
                names = ", ".join(u.get("username", "?") for u in users)
                raise ValueError(
                    f"multiple users exist; pass --username (one of: {names})"
                )
            user = users[target_idx]
            user["password_hash"] = hash_password(new_password)
            user.pop("pending_invite", None)  # a pending user becomes active
            users[target_idx] = user
            data["users"] = users
            self._write_users(data)
            if user.get("id"):
                self.revoke_user_sessions(user["id"])
            return user.get("username", "(unknown)")
        # No user records: legacy single-password store.
        self.set_password(new_password)
        return "(legacy)"

    def check_password(self, password: str, username: str | None = None) -> tuple[bool, dict | None]:
        """Verify credentials.

        *username* may be a username or an email address — the lookup tries
        username first, then email (case-insensitive).  The error response
        is identical in both cases so callers cannot infer which field was
        unrecognised.

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
                # Try exact username match first, then fall back to email lookup.
                by_username = [u for u in users if u.get("username") == username]
                if by_username:
                    candidates = by_username
                else:
                    # Treat the supplied value as an email address (case-insensitive).
                    email_lower = username.lower()
                    by_email = [
                        u for u in users
                        if (u.get("email") or "").lower() == email_lower and email_lower
                    ]
                    candidates = by_email if by_email else []
            for u in candidates:
                # Full user — verify password (with transparent SHA-256→argon2 upgrade)
                if "password_hash" in u:
                    ok, new_hash = verify_and_maybe_rehash(password, u.get("password_hash", ""))
                    if ok:
                        if new_hash:
                            # Upgrade the stored hash in-place.
                            # Lock ensures the read-modify-write is atomic when
                            # concurrent logins race on the same legacy hash.
                            with _hash_upgrade_lock:
                                data = self._read_users()
                                for i, ru in enumerate(data.get("users", [])):
                                    if ru.get("id") == u.get("id"):
                                        data["users"][i]["password_hash"] = new_hash
                                        break
                                self._write_users(data)
                            u["password_hash"] = new_hash
                        return (True, u)
                # Pending user — accept invite code as "password"
                elif "pending_invite" in u:
                    if secrets.compare_digest(u["pending_invite"], password):
                        return (True, u)
            return (False, None)

        # Legacy file fallback
        if not self._password_file.exists():
            return (False, None)
        stored = self._password_file.read_text().strip()
        ok, new_hash = verify_and_maybe_rehash(password, stored)
        if ok:
            if new_hash:
                self._password_file.write_text(new_hash)
            return (True, None)
        return (False, None)

    @_serialized
    def change_password(self, username: str, current_password: str, new_password: str) -> bool:
        """Self-change, requires current password."""
        if not new_password or len(new_password) < 8:
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

    @_serialized
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

    @_serialized
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

    def create_session(self, user_id: str = "", long_lived: bool = False, user_agent: str | None = None) -> str:
        token = secrets.token_urlsafe(32)
        ttl = self.long_session_ttl if long_lived else self.session_ttl
        entry: dict = {
            "user_id": user_id,
            "expires_at": time.time() + ttl,
            "long_lived": long_lived,
        }
        if user_agent:
            entry["user_agent_hash"] = hashlib.sha256(user_agent.encode()).hexdigest()
        self._sessions[token] = entry
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

    def validate_session(self, token: str, user_agent: str | None = None) -> str | None:
        """Return user_id if the session is valid, else None.

        When *user_agent* is provided and the session was created with a
        User-Agent hash, the hash is verified as a basic stolen-cookie check.
        Sessions created without a User-Agent hash (legacy) continue to
        validate regardless.
        """
        entry = self._get_session_entry(token)
        if entry is None:
            return None
        if time.time() >= entry["expires_at"]:
            try:
                del self._sessions[token]
            except (KeyError, Exception):
                pass
            return None
        # Client-binding check: only when the session was created with a
        # user_agent_hash AND the caller supplies a user_agent for comparison.
        stored_ua = entry.get("user_agent_hash")
        if stored_ua and user_agent:
            if not secrets.compare_digest(
                stored_ua, hashlib.sha256(user_agent.encode()).hexdigest()
            ):
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
        atomic_write_text(path, token, mode=0o600)
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


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


def get_current_user(request: Request) -> dict[str, Any]:
    """FastAPI dependency — return the current session user or raise 401.

    Reads the ``taos_session`` cookie.  The auth middleware already blocks
    unauthenticated requests before they reach route handlers, but this
    dependency also handles the case where the middleware was bypassed (e.g.
    direct TestClient calls without a cookie) and returns the capability-rich
    user dict needed for capability checks.
    """
    auth_mgr = request.app.state.auth
    token = request.cookies.get("taos_session", "")
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = auth_mgr.session_user(token)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user
