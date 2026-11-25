"""Desktop single sign-on: uid -> person over a unix socket (tinyagentos/desktop_sso.py)."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from tinyagentos.auth import AuthManager
from tinyagentos import desktop_sso
from tinyagentos.desktop_sso import DESKTOP_USER_AGENT, DesktopSessionService, load_links

ME = os.getuid()


@pytest.fixture
def env():
    d = Path(tempfile.mkdtemp(prefix="sso"))
    d.chmod(0o755)
    data = d / "data"
    data.mkdir()
    auth = AuthManager(data)
    # two people: an owner and a member
    auth.setup_user("alice", "Alice", "a@example.test", "pw-alice-1234")
    yield d, auth
    shutil.rmtree(d, ignore_errors=True)


def _two_people(auth):
    # find_user is what the service uses; create the member the same way the app does
    from tinyagentos.auth import hash_password
    data = auth._read_users()
    users = data.get("users", [])
    if not any(u["username"] == "bob" for u in users):
        users.append({"id": "usr_bob", "username": "bob", "full_name": "Bob", "email": "b@example.test",
                      "password_hash": hash_password("pw-bob-12345"), "created_at": 0, "is_admin": False})
        data["users"] = users
        auth._write_users(data)


def _links(d: Path, text: str, mode=0o644) -> Path:
    p = d / "account-links"
    p.write_text(text)
    p.chmod(mode)
    return p


async def _ask(sock: Path, req) -> dict:
    r, w = await asyncio.open_unix_connection(str(sock))
    w.write((json.dumps(req) if not isinstance(req, bytes) else req.decode()).encode() + b"\n")
    await w.drain()
    out = json.loads(await r.readline())
    w.close()
    return out


async def _service(d, auth, links_text, uid_of=None):
    links = _links(d, links_text)
    kw = {"trusted_uid": ME}
    if uid_of is not None:
        kw["peer_uid_fn"] = lambda s: uid_of[0]
    svc = DesktopSessionService(auth, links, d / "s.sock", **kw)
    await svc.start()
    return svc


@pytest.mark.asyncio
async def test_a_linked_uid_gets_its_own_persons_session_with_the_real_kernel_uid(env):
    d, auth = env
    svc = await _service(d, auth, f"{ME} alice\n")
    try:
        out = await _ask(d / "s.sock", {"op": "session"})
        assert out["username"] == "alice"
        uid = auth.validate_session(out["session"], user_agent=DESKTOP_USER_AGENT)
        assert uid == auth.find_user("alice")["id"]
        # the session is bound to the desktop client string, not usable from a browser UA
        assert auth.validate_session(out["session"], user_agent="Mozilla/5.0") is None
        assert (d / "s.sock").stat().st_mode & 0o777 == 0o666
    finally:
        await svc.stop()


@pytest.mark.asyncio
async def test_an_unmapped_uid_is_refused_and_gets_no_session(env):
    d, auth = env
    svc = await _service(d, auth, "4242 alice\n")  # the real caller (ME) is not linked
    try:
        out = await _ask(d / "s.sock", {"op": "session"})
        assert out == {"error": "unlinked"}
    finally:
        await svc.stop()


@pytest.mark.asyncio
async def test_a_uid_cannot_ask_for_someone_elses_session(env):
    d, auth = env
    _two_people(auth)
    who = [2001]
    svc = await _service(d, auth, "2001 bob\n1000 alice\n", who)
    try:
        # naming alice, or any other field, changes nothing: the kernel uid decides
        out = await _ask(d / "s.sock", {"op": "session", "username": "alice", "uid": 1000, "user": "alice"})
        assert out["username"] == "bob"
        assert auth.validate_session(out["session"], user_agent=DESKTOP_USER_AGENT) == "usr_bob"
        who[0] = 1000
        out = await _ask(d / "s.sock", {"op": "session"})
        assert out["username"] == "alice"
    finally:
        await svc.stop()


@pytest.mark.asyncio
async def test_a_container_uid_and_root_get_nothing(env):
    d, auth = env
    who = [100999]  # a container's host-mapped uid is never in the link file
    svc = await _service(d, auth, "0 alice\n1000 alice\n", who)
    try:
        assert (await _ask(d / "s.sock", {"op": "session"})) == {"error": "unlinked"}
        who[0] = 0  # root is never linkable, even if the file says so
        assert (await _ask(d / "s.sock", {"op": "session"})) == {"error": "unlinked"}
    finally:
        await svc.stop()


@pytest.mark.asyncio
async def test_renewal_revokes_the_previous_session_and_release_revokes_the_current(env):
    d, auth = env
    svc = await _service(d, auth, f"{ME} alice\n")
    try:
        a = (await _ask(d / "s.sock", {"op": "session"}))["session"]
        b = (await _ask(d / "s.sock", {"op": "session"}))["session"]
        assert a != b
        assert auth.validate_session(a, user_agent=DESKTOP_USER_AGENT) is None
        assert auth.validate_session(b, user_agent=DESKTOP_USER_AGENT)
        assert (await _ask(d / "s.sock", {"op": "release"})) == {"ok": True}
        assert auth.validate_session(b, user_agent=DESKTOP_USER_AGENT) is None
    finally:
        await svc.stop()


@pytest.mark.asyncio
async def test_unlinking_takes_effect_at_the_next_request_and_drops_the_old_session(env):
    d, auth = env
    svc = await _service(d, auth, f"{ME} alice\n")
    try:
        tok = (await _ask(d / "s.sock", {"op": "session"}))["session"]
        _links(d, "# nobody\n")
        assert (await _ask(d / "s.sock", {"op": "session"})) == {"error": "unlinked"}
        assert auth.validate_session(tok, user_agent=DESKTOP_USER_AGENT) is None
    finally:
        await svc.stop()


@pytest.mark.asyncio
async def test_garbage_and_unknown_ops_are_refused(env):
    d, auth = env
    svc = await _service(d, auth, f"{ME} alice\n")
    try:
        assert (await _ask(d / "s.sock", {"op": "admin"})) == {"error": "bad_request"}
        assert (await _ask(d / "s.sock", b"not json")) == {"error": "bad_request"}
    finally:
        await svc.stop()


def test_a_link_file_others_can_write_or_a_non_root_owner_is_ignored(env):
    d, _ = env
    p = _links(d, "1000 alice\n", 0o666)
    assert load_links(p, trusted_uid=ME) == {}          # group/other writable
    p.chmod(0o644)
    assert load_links(p, trusted_uid=ME) == {1000: "alice"}
    assert load_links(p, trusted_uid=ME + 1) == {}      # wrong owner (the controller's own uid is not root)
    d.chmod(0o777)
    assert load_links(p, trusted_uid=ME) == {}          # directory others can write
    d.chmod(0o755)
    link = d / "lnk"
    link.symlink_to(p)
    assert load_links(link, trusted_uid=ME) == {}       # a symlink is never trusted


def test_the_parser_skips_bad_lines_and_duplicate_uids(env):
    d, _ = env
    p = _links(d, "1000 alice\n1000 bob\nabc carol\n1001 d;rop\n# c\n1002 erin # note\n")
    assert load_links(p, trusted_uid=ME) == {1000: "alice", 1002: "erin"}


def test_root_helper_links_and_unlinks_and_refuses_root_and_non_root(env, capsys):
    d, _ = env
    p = d / "account-links"
    assert desktop_sso.main(["link", "1000", "alice"], links_path=str(p), trusted_uid=ME) == 0
    assert desktop_sso.main(["link", "1001", "bob"], links_path=str(p), trusted_uid=ME) == 0
    assert desktop_sso.main(["unlink", "1000"], links_path=str(p), trusted_uid=ME) == 0
    assert load_links(p, trusted_uid=ME) == {1001: "bob"}
    assert p.stat().st_mode & 0o022 == 0
    assert desktop_sso.main(["link", "0", "alice"], links_path=str(p), trusted_uid=ME) == 2
    assert desktop_sso.main(["link", "1000", "alice"], links_path=str(p), trusted_uid=ME + 1) == 1  # not root


@pytest.mark.asyncio
async def test_a_second_session_kind_for_the_same_uid_does_not_revoke_the_desktop(env):
    d, auth = env
    svc = await _service(d, auth, f"{ME} alice\n")
    try:
        desk = (await _ask(d / "s.sock", {"op": "session", "kind": "desktop"}))["session"]
        ssh = (await _ask(d / "s.sock", {"op": "session", "kind": "cli"}))["session"]
        assert auth.validate_session(desk, user_agent=DESKTOP_USER_AGENT)  # an ssh login must not kick the desktop
        assert auth.validate_session(ssh, user_agent=DESKTOP_USER_AGENT)
        assert (await _ask(d / "s.sock", {"op": "release", "kind": "cli"})) == {"ok": True}
        assert auth.validate_session(desk, user_agent=DESKTOP_USER_AGENT)
        assert (await _ask(d / "s.sock", {"op": "session", "kind": "Bad Kind!"})) == {"error": "bad_request"}
    finally:
        await svc.stop()


@pytest.mark.asyncio
async def test_the_link_file_may_appear_after_the_socket_started(env):
    d, auth = env
    svc = DesktopSessionService(auth, d / "later", d / "s.sock", trusted_uid=ME)
    await svc.start()
    try:
        assert (await _ask(d / "s.sock", {"op": "session"})) == {"error": "unlinked"}
        _links(d, f"{ME} alice\n").rename(d / "later")
        assert (await _ask(d / "s.sock", {"op": "session"}))["username"] == "alice"
    finally:
        await svc.stop()


def test_the_default_socket_is_in_a_directory_any_local_account_can_traverse():
    unit = (Path(__file__).resolve().parent.parent / "systemd" / "tinyagentos.service").read_text()
    assert str(Path(desktop_sso.DEFAULT_SOCKET).parent) == "/run/taos-sso"
    assert "taos-sso" in unit.split("RuntimeDirectory=")[1].splitlines()[0]   # the unit creates it
    assert "chmod 0755 /run/taos-sso" in unit                                 # world-traversable, unlike /run/taos (0750)


@pytest.mark.asyncio
async def test_the_socket_and_its_directory_are_reachable_by_another_account(env):
    d, auth = env
    sockdir = d / "taos-sso"
    sockdir.mkdir(mode=0o755)
    sockdir.chmod(0o755)
    svc = DesktopSessionService(auth, d / "none", sockdir / "s.sock", trusted_uid=ME)
    await svc.start()
    try:
        # other (non-owner, non-group) permission bits: traverse the directory, connect to the socket
        assert (sockdir.stat().st_mode & 0o005) == 0o005
        assert ((sockdir / "s.sock").stat().st_mode & 0o006) == 0o006
    finally:
        await svc.stop()


def _owner_file(d, who):
    f = d / "owner-linux-user"
    f.write_text(f"{who}\n")
    f.chmod(0o644)
    return f


@pytest.mark.asyncio
async def test_the_install_owners_linux_account_is_linked_with_no_link_command(env):
    d, auth = env
    _two_people(auth)
    owner = _owner_file(d, ME)
    svc = DesktopSessionService(auth, d / "none", d / "s.sock", trusted_uid=ME, owner_file=owner)
    await svc.start()
    try:
        assert (await _ask(d / "s.sock", {"op": "session"}))["username"] == "alice"  # the primary person, not bob
        owner.chmod(0o666)  # a file others can write is ignored
        assert (await _ask(d / "s.sock", {"op": "session"})) == {"error": "unlinked"}
    finally:
        await svc.stop()


@pytest.mark.asyncio
async def test_the_owner_file_never_links_another_account(env):
    d, auth = env
    owner = _owner_file(d, 4242)
    svc = DesktopSessionService(auth, d / "none", d / "s.sock", trusted_uid=ME, owner_file=owner, peer_uid_fn=lambda s: 2001)
    await svc.start()
    try:
        assert (await _ask(d / "s.sock", {"op": "session"})) == {"error": "unlinked"}
    finally:
        await svc.stop()


@pytest.mark.asyncio
async def test_an_offline_only_install_gets_its_session_with_no_network_and_no_online_account(env, monkeypatch):
    """The desktop credential is purely local: Linux uid -> local taOS person on this controller.
    No taOSgo / online account exists in this setup and every non-unix socket is refused."""
    import socket as _socket

    real_connect = _socket.socket.connect

    def only_unix(self, addr):
        if self.family != _socket.AF_UNIX:
            raise OSError("network is off in this test")
        return real_connect(self, addr)

    monkeypatch.setattr(_socket.socket, "connect", only_unix)
    monkeypatch.setattr(_socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(OSError("network is off in this test")))
    d, auth = env
    svc = DesktopSessionService(auth, d / "none", d / "s.sock", trusted_uid=ME, owner_file=_owner_file(d, ME))
    await svc.start()
    try:
        first = await _ask(d / "s.sock", {"op": "session"})
        assert first["username"] == "alice" and auth.validate_session(first["session"], user_agent=DESKTOP_USER_AGENT)
        again = await _ask(d / "s.sock", {"op": "session"})  # renewal, still offline
        assert again["session"] != first["session"] and auth.validate_session(again["session"], user_agent=DESKTOP_USER_AGENT)
    finally:
        await svc.stop()
