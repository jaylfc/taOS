"""Desktop single sign-on: a local Linux account gets its own taOS session.

A native desktop session asks the controller, over a unix socket, for the taOS
session of the person who owns the Linux account it runs as. The controller
never trusts anything the client says about who it is: the caller's uid comes
from the kernel (SO_PEERCRED), and a root-owned link file maps uid -> taOS
username. There is no username in the request, so a uid can only ever receive
the session of the person it is linked to. An unlinked uid (including every
container uid, root, and any member's other accounts) gets a refusal and no
session. There is no shared token.

Link file (default /etc/taos/account-links), one ``<uid> <taos username>`` per
line, ``#`` comments. It is only honoured when owned by root and not writable
by group or other, in a directory the same holds for: the unprivileged
controller can read it but never change it. ``python -m tinyagentos.desktop_sso
link|unlink|list`` is the root helper that edits it.

Protocol: one JSON line in, one JSON line out, then close.
  {"op": "session", "kind": "desktop"}  -> {"session": "<token>", "user_agent": "...", "expires_in": N, "username": "..."}
  {"op": "release"}  -> {"ok": true}   (revokes the session this uid was last issued)
  One live session per (uid, kind): a different kind (an ssh login's "cli") never revokes the desktop's.
  errors             -> {"error": "<code>"}
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import socket
import stat
import struct
import sys
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

DEFAULT_LINKS = "/etc/taos/account-links"
DEFAULT_SOCKET = "/run/taos-sso/desktop-sso.sock"
# Sessions are bound to a User-Agent; every desktop client sends this exact string.
DESKTOP_USER_AGENT = "taOS-desktop-session/1"
_MAX_REQUEST = 4096
_USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _safe_root_owned(path: Path, trusted_uid: int) -> bool:
    """True when `path` and its directory belong to `trusted_uid` and nobody else can write them."""
    try:
        for p in (path, path.parent):
            st = p.lstat()
            if stat.S_ISLNK(st.st_mode) or st.st_uid != trusted_uid or st.st_mode & 0o022:
                return False
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def load_links(path: str | Path, trusted_uid: int = 0) -> dict[int, str]:
    """uid -> taOS username. Empty (and a warning) when the file is missing or not root-protected."""
    p = Path(path)
    if not p.exists():
        return {}
    if not _safe_root_owned(p, trusted_uid):
        logger.warning("desktop sso: %s is not owned by uid %s or is writable by others; ignoring it", p, trusted_uid)
        return {}
    links: dict[int, str] = {}
    for n, raw in enumerate(p.read_text().splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2 or not parts[0].isdigit() or not _USERNAME.match(parts[1]):
            logger.warning("desktop sso: %s line %d is not '<uid> <username>'; skipped", p, n)
            continue
        uid = int(parts[0])
        if uid == 0 or uid in links:  # root never; a uid maps to exactly one person
            logger.warning("desktop sso: %s line %d refused (root or duplicate uid)", p, n)
            continue
        links[uid] = parts[1]
    return links


def peer_uid(sock: socket.socket) -> int:
    pid, uid, gid = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
    return uid


class DesktopSessionService:
    def __init__(
        self,
        auth_mgr,
        links_path: str | Path = DEFAULT_LINKS,
        socket_path: str | Path = DEFAULT_SOCKET,
        *,
        trusted_uid: int = 0,
        peer_uid_fn: Callable[[socket.socket], int] = peer_uid,
    ):
        self.auth = auth_mgr
        self.links_path = Path(links_path)
        self.socket_path = Path(socket_path)
        self._trusted_uid = trusted_uid
        self._peer_uid = peer_uid_fn
        self._issued: dict[tuple[int, str], str] = {}
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        sp = self.socket_path
        if sp.is_socket():
            sp.unlink()
        elif sp.exists():
            raise RuntimeError(f"{sp} exists and is not a socket")
        self._server = await asyncio.start_unix_server(self._handle, path=str(sp))
        # Any local account may connect; what it gets is decided by its kernel-reported uid.
        os.chmod(sp, 0o666)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        try:
            self.socket_path.unlink()
        except OSError:
            pass

    # ---- request handling ------------------------------------------------
    def _reply_for(self, uid: int, req: dict) -> dict:
        op = req.get("op")
        kind = str(req.get("kind") or "desktop")
        if not re.fullmatch(r"[a-z0-9-]{1,16}", kind):
            return {"error": "bad_request"}
        key = (uid, kind)
        if op == "release":
            tok = self._issued.pop(key, None)
            if tok:
                self.auth.revoke_session(tok)
            return {"ok": True}
        if op != "session":
            return {"error": "bad_request"}
        # Re-read on every request: unlinking takes effect at the next renewal.
        username = load_links(self.links_path, self._trusted_uid).get(uid)
        if username is None:
            for k in [k for k in self._issued if k[0] == uid]:  # unlinked: every session of that uid goes
                self.auth.revoke_session(self._issued.pop(k))
            return {"error": "unlinked"}
        user = self.auth.find_user(username)
        if not user or user.get("pending_invite") or not user.get("id"):
            return {"error": "unlinked"}
        old = self._issued.pop(key, None)
        if old:
            self.auth.revoke_session(old)
        token = self.auth.create_session(user_id=user["id"], long_lived=False, user_agent=DESKTOP_USER_AGENT)
        self._issued[key] = token
        return {
            "session": token,
            "user_agent": DESKTOP_USER_AGENT,
            "expires_in": self.auth.session_ttl_for(False),
            "username": user["username"],
        }

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            sock = writer.get_extra_info("socket")
            uid = self._peer_uid(sock)
            try:
                line = await asyncio.wait_for(reader.readline(), 5)
                req = json.loads(line[:_MAX_REQUEST] or b"{}")
                if not isinstance(req, dict):
                    raise ValueError
                out = self._reply_for(uid, req)
            except (ValueError, asyncio.TimeoutError):
                out = {"error": "bad_request"}
            writer.write(json.dumps(out).encode() + b"\n")
            await writer.drain()
        except Exception:
            logger.exception("desktop sso: request failed")
        finally:
            writer.close()


# ---- root helper -------------------------------------------------------
def _write_links(path: Path, links: dict[int, str]) -> None:
    body = "# uid -> taOS person. Edited by `python -m tinyagentos.desktop_sso`; root only.\n"
    body += "".join(f"{u} {n}\n" for u, n in sorted(links.items()))
    from tinyagentos.atomic_io import atomic_write_text

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    atomic_write_text(path, body, mode=0o644)


def main(argv: list[str] | None = None, *, links_path: str | None = None, trusted_uid: int = 0) -> int:
    argv = sys.argv[1:] if argv is None else argv
    path = Path(links_path or os.environ.get("TAOS_ACCOUNT_LINKS", DEFAULT_LINKS))
    if not argv or argv[0] not in ("link", "unlink", "list"):
        print("usage: python -m tinyagentos.desktop_sso link <linux-user|uid> <taos-user> | unlink <linux-user|uid> | list", file=sys.stderr)
        return 2
    if argv[0] == "list":
        for u, n in sorted(load_links(path, trusted_uid).items()):
            print(f"{u} {n}")
        return 0
    if os.geteuid() != trusted_uid:
        print("this changes who may sign in to taOS: run it as root at the computer", file=sys.stderr)
        return 1
    try:
        import pwd
        who = argv[1]
        uid = int(who) if who.isdigit() else pwd.getpwnam(who).pw_uid
    except (IndexError, KeyError):
        print("unknown Linux account", file=sys.stderr)
        return 2
    if uid == 0:
        print("root cannot be linked", file=sys.stderr)
        return 2
    links = load_links(path, trusted_uid) if path.exists() else {}
    if argv[0] == "link":
        if len(argv) != 3 or not _USERNAME.match(argv[2]):
            print("link needs a taOS username", file=sys.stderr)
            return 2
        links[uid] = argv[2]
    else:
        links.pop(uid, None)
    _write_links(path, links)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
