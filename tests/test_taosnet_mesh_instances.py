"""Per-person userspace mesh daemons: allocator, argv, serve mappings, unit, CLI.

Pure stdlib module under test; no tailscale binary is needed (a fake tailscaled
stands in where a process is launched). Every filesystem root is a tmp path.
"""
from __future__ import annotations

import json
import multiprocessing
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from tinyagentos.taosnet import mesh_instances as mi

REPO = Path(__file__).resolve().parent.parent
REAL_PORT_FREE = mi._port_is_free


@pytest.fixture
def roots(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    run = Path(tempfile.mkdtemp(prefix="mi-run-"))  # short: AF_UNIX path limit
    monkeypatch.setenv(mi.ENV_STATE_ROOT, str(state))
    monkeypatch.setenv(mi.ENV_RUN_ROOT, str(run))
    yield state, run
    for f in run.glob("*"):
        f.unlink()
    run.rmdir()


@pytest.fixture(autouse=True)
def ports_free(monkeypatch):
    """Do not depend on what else listens on this machine; tests that care
    restore the real probe."""
    monkeypatch.setattr(mi, "_port_is_free", lambda port: True)


def _real(monkeypatch):
    monkeypatch.setattr(mi, "_port_is_free", REAL_PORT_FREE)


# ------------------------------------------------ slots.json integrity


@pytest.mark.parametrize("content", [
    "",
    "   \n",
    "{",
    '{"version": 1, "slots": {"a": 0',
    "[]",
    "null",
    '{"version": 1, "slots": {"a": 0, "b": 0}}',
    '{"version": 1, "slots": {"A": 0}}',
    '{"version": 1, "slots": {"a": 99}}',
    '{"slots": {}}',
])
def test_unusable_slots_file_is_refused_not_treated_as_empty(roots, content):
    state, _ = roots
    f = state / "slots.json"
    f.write_text(content)
    with pytest.raises(mi.SlotsFileError) as e:
        mi.allocate("a")
    assert "slots.json" in str(e.value)
    assert f.read_text() == content, "a refused file must be left as found"
    with pytest.raises(mi.SlotsFileError):
        mi.lookup("a")


def test_missing_slots_file_beside_person_dirs_is_refused(roots):
    state, _ = roots
    mi.allocate("a")
    (state / "slots.json").unlink()
    with pytest.raises(mi.SlotsFileError):
        mi.allocate("b")


def test_fresh_state_root_is_a_normal_empty_start(roots):
    assert mi.allocate("a")["slot"] == 0


def test_repair_rebuilds_from_person_dirs_and_keeps_gaps(roots):
    state, _ = roots
    for p in ("a", "b", "c"):
        mi.allocate(p)
    mi.release("b")
    (state / "slots.json").write_text('{"version": 1, "slots": {"a": 0')  # half written
    with pytest.raises(mi.SlotsFileError):
        mi.allocate("d")
    out = mi.repair()
    assert out["rebuilt"] == {"a": 0, "c": 2}  # not renumbered 0 and 1
    assert (state / "slots.json.bad").read_text().startswith('{"version": 1, "slots": {"a": 0')
    assert mi.lookup("c") == 2
    assert mi.allocate("d")["slot"] == 1


def test_repair_refuses_clashing_markers(roots):
    state, _ = roots
    mi.allocate("a")
    mi.allocate("b")
    (state / "b" / "slot").write_text("0\n")
    (state / "slots.json").write_text("")
    with pytest.raises(mi.SlotsFileError):
        mi.repair()
    assert (state / "slots.json").read_text() == ""


# ------------------------------------------------ allocation


def test_allocate_is_idempotent(roots):
    first = mi.allocate("a")
    again = mi.allocate("a")
    assert first["slot"] == again["slot"] == 0
    assert first["new"] is True and again["new"] is False


def test_two_people_on_one_computer_get_zero_and_one(roots):
    assert mi.allocate("a")["slot"] == 0
    assert mi.allocate("f")["slot"] == 1
    assert mi.allocate("a")["slot"] == 0


def test_release_frees_the_slot_and_it_is_reused(roots):
    mi.allocate("a")
    mi.allocate("f")
    out = mi.release("a")
    assert out["person"] == "a" and out["released"] == 0
    assert "purge" in out["note"] and "node key" in out["note"] and "remain" in out["note"]
    assert mi.lookup("a") is None
    assert mi.allocate("g")["slot"] == 0
    with pytest.raises(LookupError):
        mi.release("a")


def test_release_refused_while_a_daemon_answers_on_its_socket(roots):
    _, run = roots
    mi.allocate("a")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(run / "a.sock"))
    srv.listen(1)
    try:
        with pytest.raises(mi.DaemonAlive):
            mi.release("a")
        assert mi.lookup("a") == 0
    finally:
        srv.close()
    # the socket file is still there but nothing answers: not alive
    assert (run / "a.sock").exists()
    assert mi.release("a")["released"] == 0


def test_busy_port_makes_allocation_skip_that_slot(roots, monkeypatch):
    _real(monkeypatch)
    blocker = socket.socket()
    try:
        blocker.bind((mi.LOOPBACK, mi.DEVICE_LINK_BASE))
    except OSError:
        pytest.skip("27700 already in use on this machine")
    blocker.listen(1)
    try:
        out = mi.allocate("a")
    finally:
        blocker.close()
    assert out["slot"] == 1
    assert out["skipped"] == [{"slot": 0, "busy_ports": [27700]}]
    assert mi.lookup("a") == 1


@pytest.mark.parametrize("which", ["device_link", "relay", "socks", "http_proxy"])
def test_each_of_the_four_ports_is_checked_for_a_new_slot(roots, monkeypatch, which):
    busy = mi.ports_for_slot(0)[which]
    monkeypatch.setattr(mi, "_port_is_free", lambda port: port != busy)
    assert mi.allocate("a")["slot"] == 1


def test_exhaustion_at_fifty(roots):
    for i in range(50):
        assert mi.allocate(f"u{i}")["slot"] == i
    with pytest.raises(mi.SlotsExhausted):
        mi.allocate("one-too-many")
    assert len(json.loads((roots[0] / "slots.json").read_text())["slots"]) == 50


def test_exhaustion_when_every_free_slot_is_busy(roots, monkeypatch):
    monkeypatch.setattr(mi, "_port_is_free", lambda port: False)
    with pytest.raises(mi.SlotsExhausted):
        mi.allocate("a")
    assert mi.lookup("a") is None


def _worker(state, person, barrier, q):
    os.environ[mi.ENV_STATE_ROOT] = state
    real_read = mi._read_slots

    def slow_read(root):  # widen the read-modify-write window
        out = real_read(root)
        time.sleep(0.05)
        return out

    mi._read_slots = slow_read
    barrier.wait()
    q.put((person, mi.allocate(person)["slot"]))


def test_eight_processes_allocating_at_once_get_distinct_slots(roots):
    state, _ = roots
    ctx = multiprocessing.get_context("fork")
    barrier, q = ctx.Barrier(8), ctx.Queue()
    people = [f"p{i}" for i in range(8)]
    procs = [ctx.Process(target=_worker, args=(str(state), p, barrier, q)) for p in people]
    for p in procs:
        p.start()
    got = dict(q.get(timeout=60) for _ in procs)
    for p in procs:
        p.join(30)
        assert p.exitcode == 0
    assert sorted(got.values()) == list(range(8))
    on_disk = json.loads((state / "slots.json").read_text())
    assert on_disk["slots"] == got
    assert stat.S_IMODE((state / "slots.json").stat().st_mode) == 0o644


def test_read_paths_write_nothing(roots):
    state, run = roots
    mi.allocate("a")
    snap = {p: p.stat().st_mtime_ns for p in state.rglob("*")}
    mi.lookup("a")
    mi.ports("a")
    mi.argv("a")
    mi.serve_argv("a")
    mi.check_ports("a")
    assert {p: p.stat().st_mtime_ns for p in state.rglob("*")} == snap


def test_allocate_leaves_a_private_state_dir_with_a_marker(roots):
    state, _ = roots
    mi.allocate("a")
    assert stat.S_IMODE((state / "a").stat().st_mode) == 0o700
    assert (state / "a" / "slot").read_text().strip() == "0"


# ------------------------------------------------ person ids


BAD_IDS = ["A", "Alice", "../x", "a/b", "a b", "", "1a", "-a", "a" * 32, "a.b", "a\x00b",
           "root", "taos", "admin", "sudo", "relay", None, 5]


@pytest.mark.parametrize("bad", BAD_IDS)
def test_invalid_person_refused_before_any_filesystem_access(tmp_path, monkeypatch, bad):
    state = tmp_path / "never-created" / "state"
    run = tmp_path / "never-created" / "run"
    monkeypatch.setenv(mi.ENV_STATE_ROOT, str(state))
    monkeypatch.setenv(mi.ENV_RUN_ROOT, str(run))
    calls = [
        lambda: mi.allocate(bad),
        lambda: mi.release(bad),
        lambda: mi.lookup(bad),
        lambda: mi.ports(bad),
        lambda: mi.argv(bad),
        lambda: mi.serve_argv(bad),
        lambda: mi.apply_serve(bad),
        lambda: mi.socket_for(bad),
        lambda: mi.state_dir(bad),
        lambda: mi.check_ports(bad),
        lambda: mi.exec_daemon(bad),
        lambda: mi.daemon_alive(bad),
    ]
    for call in calls:
        with pytest.raises(ValueError):
            call()
    assert not (tmp_path / "never-created").exists()


def test_valid_person_ids_pass():
    for ok in ("a", "jay", "a-b", "u0", "a" * 31):
        assert mi.validate_person(ok) == ok


def test_cli_rejects_a_bad_person(roots, capsys):
    assert mi.main(["allocate", "../x"]) == 1
    assert "invalid person id" in capsys.readouterr().err


# ------------------------------------------------ argv and serve


@pytest.mark.parametrize("n", [0, 1, 49])
def test_argv_is_the_design_5_0_command(roots, n):
    state, run = roots
    for i in range(n):
        mi.allocate(f"u{i}")
    mi.allocate("a")
    cmd = mi.argv("a")
    assert cmd == [
        "tailscaled",
        "--tun=userspace-networking",
        "--no-logs-no-support",
        f"--statedir={state}/a",
        f"--socket={run}/a.sock",
        f"--socks5-server=127.0.0.1:{27800 + n}",
        f"--outbound-http-proxy-listen=127.0.0.1:{27850 + n}",
    ]


def test_nothing_ever_says_localhost_or_a_kernel_tun(roots):
    mi.allocate("a")
    mi.allocate("f")
    for person in ("a", "f"):
        every = mi.argv(person) + [t for c in mi.serve_argv(person) for t in c]
        assert not any("localhost" in t for t in every)
        assert "--tun=userspace-networking" in mi.argv(person)
        assert not [t for t in every if t.startswith("--tun=") and t != "--tun=userspace-networking"]
        for t in every:
            for host in re.findall(r"(?:--[a-z0-9-]+=|tcp://)((?:\d+\.){3}\d+|[a-z]+):\d+", t):
                assert host == "127.0.0.1"


def test_the_port_probe_binds_the_literal_loopback_address(monkeypatch):
    binds = []
    real_bind = socket.socket.bind

    def spy(self, addr):
        binds.append(addr)
        return real_bind(self, addr)

    monkeypatch.setattr(socket.socket, "bind", spy)
    REAL_PORT_FREE(27799)
    assert binds == [("127.0.0.1", 27799)]


def test_ports_dict(roots):
    mi.allocate("a")
    mi.allocate("f")
    assert mi.ports("f") == {"device_link": 27701, "relay": 27751, "socks": 27801, "http_proxy": 27851}


@pytest.mark.parametrize("n", [0, 1, 7])
def test_serve_maps_6969_only_to_the_relay_listener(roots, n):
    state, run = roots
    for i in range(n):
        mi.allocate(f"u{i}")
    mi.allocate("a")
    link, relay = mi.serve_argv("a")
    sock = f"--socket={run}/a.sock"
    assert link == ["tailscale", sock, "serve", "--bg", "--tcp=6977", f"tcp://127.0.0.1:{27700 + n}"]
    assert relay == ["tailscale", sock, "serve", "--bg", "--tcp=6969", f"tcp://127.0.0.1:{27750 + n}"]
    for c in (link, relay):
        assert c[1] == sock, "every tailscale call names its socket"
    flat = " ".join(relay + link)
    assert "tcp://127.0.0.1:6969" not in flat
    assert "tcp://127.0.0.1:6977" not in flat
    assert not re.search(r"tcp://[^ ]*:69(69|77)\b", flat)


def test_apply_serve_runs_both_commands_for_the_socket_owner(roots):
    _, run = roots
    mi.allocate("a")
    (run / "a.sock").write_text("")  # owned by this uid
    seen = []
    cmds = mi.apply_serve("a", runner=lambda c, **kw: seen.append(c))
    assert seen == cmds == mi.serve_argv("a")


def test_apply_serve_refuses_a_stranger(roots, monkeypatch):
    _, run = roots
    mi.allocate("a")
    (run / "a.sock").write_text("")
    monkeypatch.setattr(os, "geteuid", lambda: os.stat(run / "a.sock").st_uid + 4242)
    seen = []
    with pytest.raises(PermissionError):
        mi.apply_serve("a", runner=lambda c, **kw: seen.append(c))
    assert seen == []


def test_socket_for_is_per_person_and_beside_the_owners_entry(roots):
    _, run = roots
    assert mi.socket_for("a") == run / "a.sock"
    assert mi.socket_for("f") == run / "f.sock"
    assert mi.socket_for("a", "/x/y") == Path("/x/y/a.sock")


# ------------------------------------------------ port checks at start


def test_check_ports_looks_only_at_the_daemons_own_ports(roots, monkeypatch):
    mi.allocate("a")
    p = mi.ports("a")
    # the controller's listeners are up: restart must still be possible
    monkeypatch.setattr(mi, "_port_is_free", lambda port: port not in (p["device_link"], p["relay"]))
    assert mi.check_ports("a") == []
    assert mi.main(["check-ports", "a"]) == 0
    monkeypatch.setattr(mi, "_port_is_free", lambda port: port != p["socks"])
    assert mi.check_ports("a") == [p["socks"]]
    assert mi.main(["check-ports", "a"]) == 1
    monkeypatch.setattr(mi, "_port_is_free", lambda port: port != p["http_proxy"])
    assert mi.check_ports("a") == [p["http_proxy"]]


def test_check_ports_uses_a_real_bind(roots, monkeypatch):
    mi.allocate("a")
    _real(monkeypatch)
    blocker = socket.socket()
    try:
        blocker.bind((mi.LOOPBACK, mi.ports("a")["socks"]))
    except OSError:
        pytest.skip("27800 already in use on this machine")
    blocker.listen(1)
    try:
        assert mi.check_ports("a") == [27800]
    finally:
        blocker.close()


def test_exec_requires_an_allocation(roots):
    with pytest.raises(LookupError):
        mi.exec_daemon("a")


_FAKE_TAILSCALED = """#!{python}
import os, socket, sys, time
args = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--") and "=" in a)
open(os.environ["FAKE_ARGV_LOG"], "w").write("\\n".join(sys.argv[1:]))
s = socket.socket(socket.AF_UNIX); s.bind(args["socket"]); s.listen(1)
os.chmod(args["socket"], 0o666)
time.sleep(0.4)
os.chmod(args["socket"], 0o666)  # widened again, just after creation
time.sleep(60)
"""


def test_exec_launches_tailscaled_and_closes_the_socket_to_0600(roots, tmp_path):
    state, run = roots
    mi.allocate("a")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "tailscaled"
    fake.write_text(_FAKE_TAILSCALED.format(python=sys.executable))
    fake.chmod(0o755)
    log = tmp_path / "argv.log"
    import shutil
    shutil.rmtree(state / "a")  # exec must (re)create the private state dir itself
    # a stale socket file from a dead daemon must not confuse the launch
    stale = socket.socket(socket.AF_UNIX)
    stale.bind(str(run / "a.sock"))
    stale.close()
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "FAKE_ARGV_LOG": str(log),
           mi.ENV_STATE_ROOT: str(state), mi.ENV_RUN_ROOT: str(run), "PYTHONPATH": str(REPO)}
    proc = subprocess.Popen([sys.executable, "-m", "tinyagentos.taosnet.mesh_instances", "exec", "a"],
                            env=env, cwd=REPO)
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not log.exists():
            time.sleep(0.05)
        assert log.exists(), "tailscaled was not launched"
        assert log.read_text().split("\n") == mi.argv("a")[1:]
        time.sleep(1.5)  # past the fake's second chmod 0666
        assert stat.S_IMODE((run / "a.sock").stat().st_mode) == 0o600
        assert proc.poll() is None
        assert stat.S_IMODE((state / "a").stat().st_mode) == 0o700
    finally:
        proc.kill()
        proc.wait()


# ------------------------------------------------ unit and owner step


UNIT = REPO / "systemd" / "taos-mesh@.service"


def test_rendered_unit(roots):
    py = "/opt/taos/venv/bin/python"
    text = mi.render_unit(py)
    assert mi.PYTHON_TOKEN not in text
    assert "\nUser=taos\n" in text and "\nGroup=taos\n" in text
    assert not re.search(r"root", text, re.I)
    assert "TS_DEBUG" not in text
    assert f"\nExecStartPre={py} -m tinyagentos.taosnet.mesh_instances check-ports %i\n" in text
    assert f"\nExecStart={py} -m tinyagentos.taosnet.mesh_instances exec %i\n" in text


@pytest.mark.parametrize("line", [
    "NoNewPrivileges=yes", "ProtectSystem=strict", "ProtectHome=read-only", "PrivateTmp=yes",
    "PrivateDevices=yes", "CapabilityBoundingSet=", "AmbientCapabilities=",
    "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK", "LockPersonality=yes",
    "RestrictRealtime=yes", "Restart=on-failure", "RuntimeDirectory=taos/mesh",
    "RuntimeDirectoryPreserve=yes", "RuntimeDirectoryMode=0750", "ReadWritePaths=/var/lib/taos/mesh",
])
def test_unit_carries_each_required_line_alone(line):
    lines = UNIT.read_text().splitlines()
    assert line in lines
    assert not any(l.startswith("StateDirectory") for l in lines)


@pytest.mark.parametrize("bad", ["/with space/python", "relative/python", "/a%b/python", "/a'b/python"])
def test_render_refuses_an_unusable_python_path(bad):
    with pytest.raises(ValueError):
        mi.render_unit(bad)


def test_install_template_refuses_when_not_root(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    calls = []
    with pytest.raises(PermissionError):
        mi.install_template(unit_dir=tmp_path, runner=lambda *a, **k: calls.append(a),
                            state=str(tmp_path / "s"), chown=lambda *a: calls.append(a))
    assert calls == []
    assert list(tmp_path.iterdir()) == []
    assert not (tmp_path / "s").exists()
    monkeypatch.setenv(mi.ENV_STATE_ROOT, str(tmp_path / "s"))
    assert mi.main(["install-template"]) == 1
    assert "owner step" in capsys.readouterr().err
    assert not (tmp_path / "s").exists()


def test_install_template_as_root_writes_the_unit_and_enables_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    calls, chowns = [], []
    units = tmp_path / "units"
    units.mkdir()
    lines = mi.install_template(
        unit_dir=units, runner=lambda c, **kw: calls.append(c), python="/opt/taos/venv/bin/python",
        state=str(tmp_path / "state"), user_exists=lambda n: False,
        chown=lambda p, u, g: chowns.append((str(p), u, g)))
    assert calls == [["useradd", "-r", "-M", "-s", "/usr/sbin/nologin", "taos"],
                     ["systemctl", "daemon-reload"]]
    assert not any(w in c for c in calls for w in ("enable", "start", "restart", "--now"))
    unit = (units / "taos-mesh@.service").read_text()
    assert "ExecStart=/opt/taos/venv/bin/python -m tinyagentos.taosnet.mesh_instances exec %i" in unit
    assert chowns == [(str(tmp_path / "state"), "taos", "taos")]
    text = "\n".join(lines)
    assert "nothing was enabled or started" in text
    assert "systemctl enable --now taos-mesh@<p>" in text
    assert "allocate <p>" in text


def test_install_template_leaves_an_existing_user_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    calls = []
    mi.install_template(unit_dir=tmp_path, runner=lambda c, **kw: calls.append(c), python="/p/python",
                        state=str(tmp_path), user_exists=lambda n: True, chown=lambda *a: None)
    assert calls == [["systemctl", "daemon-reload"]]


# ------------------------------------------------ directory modes, purge


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


@pytest.mark.parametrize("umask", [0o022, 0o000])
@pytest.mark.parametrize("order", ["allocate-first", "install-first"])
def test_every_created_state_directory_is_0700_whatever_the_umask_and_order(tmp_path, monkeypatch, umask, order):
    state = tmp_path / "var" / "lib" / "taos" / "mesh"
    monkeypatch.setenv(mi.ENV_STATE_ROOT, str(state))
    monkeypatch.setenv(mi.ENV_RUN_ROOT, str(tmp_path / "run"))
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    old = os.umask(umask)
    try:
        def install():
            mi.install_template(unit_dir=tmp_path, runner=lambda *a, **k: None, python="/p/python",
                                state=str(state), user_exists=lambda n: True, chown=lambda *a: None)
        if order == "allocate-first":
            mi.allocate("a")
            install()
        else:
            install()
            mi.allocate("a")
        mi.allocate("f")
    finally:
        os.umask(old)
    assert _mode(state) == 0o700
    assert _mode(state / "a") == 0o700 and _mode(state / "f") == 0o700


@pytest.mark.parametrize("umask", [0o022, 0o000])
def test_exec_created_dirs_are_explicit_too(roots, monkeypatch, umask):
    state, run = roots
    mi.allocate("a")
    import shutil
    shutil.rmtree(state / "a")
    run.rmdir() if not list(run.iterdir()) else None
    old = os.umask(umask)
    try:
        mi._ensure_dirs("a", None, None)
    finally:
        os.umask(old)
    assert _mode(state / "a") == 0o700
    assert _mode(run) == 0o755


def test_purge_refused_while_the_daemon_is_alive(roots):
    state, run = roots
    mi.allocate("a")
    mi.release("a")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(run / "a.sock"))
    srv.listen(1)
    try:
        with pytest.raises(mi.DaemonAlive):
            mi.purge("a")
    finally:
        srv.close()
    assert (state / "a").is_dir()


def test_purge_refused_while_the_person_holds_a_slot(roots):
    state, _ = roots
    mi.allocate("a")
    (state / "a" / "node.key").write_text("k")
    with pytest.raises(RuntimeError):
        mi.purge("a")
    assert (state / "a" / "node.key").exists()
    assert mi.main(["purge", "a"]) == 1


def test_purge_after_release_removes_the_state_dir_and_says_so(roots, capsys):
    state, _ = roots
    mi.allocate("a")
    mi.allocate("f")
    (state / "a" / "node.key").write_text("k")
    mi.release("a")
    assert (state / "a" / "node.key").exists(), "release alone keeps the node key"
    assert mi.main(["purge", "a"]) == 0
    out = capsys.readouterr().out
    assert "removed" in out and "node key" in out
    assert not (state / "a").exists()
    assert (state / "f").is_dir() and mi.lookup("f") == 1
    with pytest.raises(LookupError):
        mi.purge("a")


@pytest.mark.parametrize("bad", ["../x", "root", "A", ""])
def test_purge_validates_the_id_first(tmp_path, monkeypatch, bad):
    monkeypatch.setenv(mi.ENV_STATE_ROOT, str(tmp_path / "never"))
    with pytest.raises(ValueError):
        mi.purge(bad)
    assert not (tmp_path / "never").exists()


# ------------------------------------------------ inert by default


NEEDLES = ("taos-mesh@", "mesh_instances", "taos-mesh")


def _mentions(path: Path) -> list[str]:
    try:
        text = path.read_text()
    except (UnicodeDecodeError, OSError):
        return []
    return [n for n in NEEDLES if n in text]


def _files(root: Path, exclude: tuple[str, ...] = ()) -> list[Path]:
    return [p for p in sorted(root.rglob("*")) if p.is_file() and "__pycache__" not in p.parts
            and p.name not in exclude]


def test_the_scanner_can_fail(tmp_path):
    f = tmp_path / "install.sh"
    f.write_text("systemctl enable --now taos-mesh@a\n")
    assert _mentions(f)
    f.write_text("python -m tinyagentos.taosnet.mesh_instances allocate a\n")
    assert _mentions(f)
    f.write_text("systemctl enable tinyagentos\n")
    assert _mentions(f) == []


def test_install_sh_does_not_mention_the_mesh_instances():
    assert _mentions(REPO / "install.sh") == []


def test_packaging_does_not_mention_the_mesh_instances():
    files = _files(REPO / "packaging")
    assert files, "packaging/ should not be empty"
    assert {str(p.relative_to(REPO)): _mentions(p) for p in files if _mentions(p)} == {}


def _scan_targets(repo: Path) -> dict[str, list[Path]]:
    """Each key is its own scan, so each can fail alone."""
    dockerfiles = [p for p in repo.rglob("Dockerfile*")
                   if p.is_file() and ".git" not in p.parts and "node_modules" not in p.parts
                   and "tests/e2e/device_link" not in p.relative_to(repo).as_posix()]
    return {
        "Dockerfiles": sorted(dockerfiles),
        "scripts": _files(repo / "scripts"),
        "desktop": [p for p in _files(repo / "desktop") if "node_modules" not in p.parts],
        ".github": _files(repo / ".github"),
    }


@pytest.mark.parametrize("name", ["Dockerfiles", "scripts", "desktop", ".github"])
def test_wider_scans_find_nothing(name):
    files = _scan_targets(REPO)[name]
    assert files, f"{name} scan matched no files"
    assert {str(p.relative_to(REPO)): _mentions(p) for p in files if _mentions(p)} == {}


@pytest.mark.parametrize("name", ["Dockerfiles", "scripts", "desktop", ".github"])
def test_each_wider_scan_can_fail_alone(tmp_path, name):
    fake = tmp_path
    (fake / "Dockerfile.x").write_text("RUN true\n")
    for d in ("scripts", "desktop", ".github"):
        (fake / d).mkdir()
        (fake / d / "f.txt").write_text("nothing\n")
    clean = _scan_targets(fake)
    assert all(not _mentions(p) for ps in clean.values() for p in ps)
    victim = clean[name][0]
    victim.write_text(victim.read_text() + "systemctl start taos-mesh@a\n")
    for other, ps in _scan_targets(fake).items():
        assert bool([p for p in ps if _mentions(p)]) == (other == name)


def test_the_bed_dockerfile_is_exempt_from_the_dockerfile_scan(tmp_path):
    bed = tmp_path / "tests" / "e2e" / "device_link" / "images"
    bed.mkdir(parents=True)
    (bed / "Dockerfile").write_text("COPY mesh_instances\n")
    assert _scan_targets(tmp_path)["Dockerfiles"] == []


def test_the_app_does_not_mention_the_mesh_instances():
    files = _files(REPO / "tinyagentos", exclude=("mesh_instances.py",))
    assert len(files) > 100
    assert {str(p.relative_to(REPO)): _mentions(p) for p in files if _mentions(p)} == {}


def test_other_units_and_build_files_do_not_mention_the_mesh_instances():
    others = [p for p in _files(REPO / "systemd") if p.name != "taos-mesh@.service"]
    others += [REPO / "pyproject.toml"] + _files(REPO / "os-build")
    assert {str(p.relative_to(REPO)): _mentions(p) for p in others if _mentions(p)} == {}


def test_nothing_installs_systemd_units_by_glob():
    glob = re.compile(r"systemd/\*|systemd/\.|systemd/?\s*$|\*\.service|cp\s+-\w*r\w*\s+\S*systemd", re.M)
    targets = [REPO / "install.sh"] + _files(REPO / "packaging")
    assert {str(p.relative_to(REPO)): glob.findall(p.read_text()) for p in targets
            if p.suffix != ".png" and glob.search(p.read_text())} == {}
    # and the control: the pattern does catch a glob install
    assert glob.search("cp $INSTALL_DIR/systemd/*.service /etc/systemd/system/")
