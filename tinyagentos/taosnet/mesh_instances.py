"""Per-person userspace mesh daemons: slot allocator, argv, serve mappings, CLI.

Design 5.0 and 3.9 of the device-link plan. Each person on a computer gets
their own ``tailscaled --tun=userspace-networking`` (never a kernel tun), their
own state directory and socket, and a numbered slot ``n`` (0..49) that fixes
four loopback ports:

    27700 + n   device-link listener (node TCP 6977 is forwarded here)
    27750 + n   relay listener       (node TCP 6969 is forwarded here, never the
                                      controller's own 6969)
    27800 + n   the person's SOCKS5 proxy
    27850 + n   the person's outbound HTTP proxy

Every listener binds the literal 127.0.0.1 (measured: "localhost" resolved to
::1 inside a node).

This module is INERT. Nothing imports it, install.sh and the packaging do not
mention it, and no instance exists until someone runs this CLI::

    python -m tinyagentos.taosnet.mesh_instances \
        {allocate,release,purge,status,repair,argv,exec,check-ports,serve,
         install-template}

Only ``install-template`` needs root (an owner step: it refuses unless the
effective uid is 0 and prints what it did). It never enables or starts
anything. ``allocate``, ``exec`` and the rest run as the service account.

Privilege model today (before people are Linux accounts)
--------------------------------------------------------
tailscaled's LocalAPI accepts writes (up, serve, down) only from the uid that
runs the daemon, or from the superuser; any other non-root uid can still READ
status because the socket is created world accessible. So every instance runs
as the controller's unprivileged service account (``SERVICE_USER``, "taos"),
never as the superuser. State lives in ``<state_root>/<person>`` (0700) and the
socket in ``<run_root>/<person>.sock``; once the socket appears it is chmod
0600 (a short-lived watcher forked by ``exec`` does this, because ``exec``
replaces itself with tailscaled), which closes the read-only status leak to
other local users.

Migration (documented, not implemented)
---------------------------------------
When per-person Linux accounts exist (taos-accountd), the owner step writes a
drop-in ``taos-mesh@<person>.service.d/user.conf`` with ``User=<person>``,
chowns that person's state directory, and the controller drives the socket
through that person's own service. The slot then moves from ``slots.json`` to
``account_links.slot`` (design T1), which becomes the source of truth; this
allocator file hands its slots over and is retired.

Port checks
-----------
``allocate`` checks all four ports of a NEW slot are free before handing it
out. ``exec`` and ``check-ports`` check only the two ports tailscaled itself
binds (socks, http proxy): the other two belong to the controller's
listeners, which may already be up when systemd restarts the daemon.

Files and failure rules
-----------------------
``<state_root>/slots.json`` is the allocator's only file, guarded by
``fcntl.flock`` on ``<state_root>/.slots.lock`` for every read-modify-write and
replaced atomically. A corrupt, empty or half-written file is NEVER treated as
empty (that would hand a live slot out twice): it is refused with an error
naming the file, and ``repair`` rebuilds it from the ``slot`` marker each
person's state directory carries. A missing file next to existing person
directories is refused the same way.
"""
from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
import pwd
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

SERVICE_USER = "taos"
SERVICE_GROUP = "taos"

DEFAULT_STATE_ROOT = "/var/lib/taos/mesh"
DEFAULT_RUN_ROOT = "/run/taos/mesh"
ENV_STATE_ROOT = "TAOS_MESH_STATE_ROOT"
ENV_RUN_ROOT = "TAOS_MESH_RUN_ROOT"

SLOT_COUNT = 50
DEVICE_LINK_BASE = 27700
RELAY_BASE = 27750
SOCKS_BASE = 27800
HTTP_PROXY_BASE = 27850
NODE_DEVICE_LINK_PORT = 6977
NODE_RELAY_PORT = 6969

LOOPBACK = "127.0.0.1"
UNIT_NAME = "taos-mesh@.service"
PYTHON_TOKEN = "@PYTHON@"

_PERSON_RE = re.compile(r"^[a-z][a-z0-9-]{0,30}$")
_RESERVED = frozenset({"root", "taos", "admin", "sudo", "relay"})
_SLOTS_FILE = "slots.json"
_LOCK_FILE = ".slots.lock"
_MARKER = "slot"


class SlotsFileError(RuntimeError):
    """slots.json is unreadable, empty, half-written or inconsistent."""


class SlotsExhausted(RuntimeError):
    """No free slot is left."""


class DaemonAlive(RuntimeError):
    """The person's daemon still answers on its socket."""


# ---------------------------------------------------------------- paths


def validate_person(person: object) -> str:
    """Return ``person`` if it is a legal id, else raise ValueError.

    Called before any path is built or any filesystem access happens.
    """
    if not isinstance(person, str) or not _PERSON_RE.fullmatch(person):
        raise ValueError(f"invalid person id {person!r}: must match [a-z][a-z0-9-]{{0,30}}")
    if person in _RESERVED:
        raise ValueError(f"person id {person!r} is reserved")
    return person


def state_root(override: Optional[str] = None) -> Path:
    return Path(override or os.environ.get(ENV_STATE_ROOT) or DEFAULT_STATE_ROOT)


def run_root(override: Optional[str] = None) -> Path:
    return Path(override or os.environ.get(ENV_RUN_ROOT) or DEFAULT_RUN_ROOT)


def state_dir(person: str, root: Optional[str] = None) -> Path:
    return state_root(root) / validate_person(person)


def socket_for(person: str, run: Optional[str] = None) -> Path:
    """Per-person LocalAPI socket path. Sits beside ``mesh.py``'s single
    ``TAOS_TAILSCALE_SOCKET`` (the owner's entry), which is left untouched."""
    return run_root(run) / f"{validate_person(person)}.sock"


# ---------------------------------------------------------------- ports


def ports_for_slot(slot: int) -> dict[str, int]:
    if not isinstance(slot, int) or isinstance(slot, bool) or not 0 <= slot < SLOT_COUNT:
        raise ValueError(f"slot out of range 0..{SLOT_COUNT - 1}: {slot!r}")
    return {
        "device_link": DEVICE_LINK_BASE + slot,
        "relay": RELAY_BASE + slot,
        "socks": SOCKS_BASE + slot,
        "http_proxy": HTTP_PROXY_BASE + slot,
    }


def _port_is_free(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((LOOPBACK, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def busy_ports(slot: int, which: tuple[str, ...] = ("device_link", "relay", "socks", "http_proxy")) -> list[int]:
    p = ports_for_slot(slot)
    return [p[k] for k in which if not _port_is_free(p[k])]


_DAEMON_PORTS = ("socks", "http_proxy")


# ---------------------------------------------------------------- slots file


def _slots_path(root: Path) -> Path:
    return root / _SLOTS_FILE


def _parse_slots(text: str, path: Path) -> dict[str, int]:
    def bad(why: str) -> SlotsFileError:
        return SlotsFileError(
            f"{path} is not usable ({why}); refusing to treat it as empty. "
            f"Inspect it, then run 'repair' to rebuild it from the person directories."
        )

    if not text.strip():
        raise bad("empty")
    try:
        data = json.loads(text)
    except ValueError as e:
        raise bad(f"not valid JSON: {e}") from None
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("slots"), dict):
        raise bad("unexpected structure")
    out: dict[str, int] = {}
    for person, slot in data["slots"].items():
        try:
            validate_person(person)
        except ValueError:
            raise bad(f"bad person id {person!r}") from None
        if not isinstance(slot, int) or isinstance(slot, bool) or not 0 <= slot < SLOT_COUNT:
            raise bad(f"bad slot for {person!r}")
        out[person] = slot
    if len(set(out.values())) != len(out):
        raise bad("a slot is assigned twice")
    return out


def _person_dirs(root: Path) -> list[str]:
    try:
        names = sorted(os.listdir(root))
    except FileNotFoundError:
        return []
    out = []
    for n in names:
        if (root / n).is_dir():
            try:
                validate_person(n)
            except ValueError:
                continue
            out.append(n)
    return out


def _read_slots(root: Path) -> dict[str, int]:
    """Read without locking or writing (replacement is atomic)."""
    path = _slots_path(root)
    try:
        text = path.read_text()
    except FileNotFoundError:
        dirs = [d for d in _person_dirs(root) if (root / d / _MARKER).exists()]
        if dirs:
            raise SlotsFileError(
                f"{path} is missing but person directories exist ({', '.join(dirs)}); "
                f"refusing to start from an empty allocation. Run 'repair'."
            ) from None
        return {}
    return _parse_slots(text, path)


def _fsync_dir(d: Path) -> None:
    fd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(path: Path, data: str, mode: int) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
            os.fchmod(fh.fileno(), mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _fsync_dir(path.parent)


def _write_slots(root: Path, slots: dict[str, int]) -> None:
    body = json.dumps({"version": 1, "slots": dict(sorted(slots.items()))}, indent=2) + "\n"
    _atomic_write(_slots_path(root), body, 0o644)


def _mkdirs(path: Path, mode: int, parent_mode: int = 0o755) -> None:
    """Create ``path`` and any missing parents with EXPLICIT modes: each created
    component is chmod-ed, because mkdir's mode is subject to the umask. The
    final component gets ``mode``, created parents get ``parent_mode``. Existing
    directories are left as they are."""
    path = Path(path)
    missing = []
    p = path
    while not p.exists():
        missing.append(p)
        if p.parent == p:
            break
        p = p.parent
    for d in reversed(missing):
        want = mode if d == path else parent_mode
        try:
            d.mkdir(mode=want)
        except FileExistsError:
            continue
        os.chmod(d, want)


class _Locked:
    """Exclusive flock on a separate lock file, for read-modify-write."""

    def __init__(self, root: Path):
        self.root = root

    def __enter__(self) -> Path:
        _mkdirs(self.root, 0o700)
        self.fh = open(self.root / _LOCK_FILE, "a")
        fcntl.flock(self.fh, fcntl.LOCK_EX)
        return self.root

    def __exit__(self, *exc) -> None:
        try:
            fcntl.flock(self.fh, fcntl.LOCK_UN)
        finally:
            self.fh.close()


def _write_marker(root: Path, person: str, slot: int) -> None:
    d = root / person
    _mkdirs(d, 0o700)
    _atomic_write(d / _MARKER, f"{slot}\n", 0o600)


# ---------------------------------------------------------------- public API


def lookup(person: str, root: Optional[str] = None) -> Optional[int]:
    """The person's slot, or None. Read-only: never locks, never writes."""
    validate_person(person)
    return _read_slots(state_root(root)).get(person)


def _require_slot(person: str, root: Optional[str]) -> int:
    slot = lookup(person, root)
    if slot is None:
        raise LookupError(f"{person!r} has no slot; run 'allocate {person}' first")
    return slot


def allocate(person: str, root: Optional[str] = None) -> dict:
    """Idempotent: the same person always gets the same slot. A new person gets
    the lowest free slot whose four ports are free on 127.0.0.1."""
    validate_person(person)
    with _Locked(state_root(root)) as r:
        slots = _read_slots(r)
        if person in slots:
            _write_marker(r, person, slots[person])
            return {"person": person, "slot": slots[person], "new": False,
                    "skipped": [], "ports": ports_for_slot(slots[person])}
        used = set(slots.values())
        skipped = []
        for n in range(SLOT_COUNT):
            if n in used:
                continue
            busy = busy_ports(n)
            if busy:
                skipped.append({"slot": n, "busy_ports": busy})
                continue
            slots[person] = n
            _write_slots(r, slots)
            _write_marker(r, person, n)
            return {"person": person, "slot": n, "new": True,
                    "skipped": skipped, "ports": ports_for_slot(n)}
        detail = "; ".join(f"slot {s['slot']} busy {s['busy_ports']}" for s in skipped)
        raise SlotsExhausted(
            f"no free slot for {person!r} (0..{SLOT_COUNT - 1} all taken or busy"
            + (f": {detail}" if detail else "") + ")"
        )


def daemon_alive(person: str, run: Optional[str] = None) -> bool:
    """True when the socket path exists and a connect() to it succeeds."""
    sock = socket_for(person, run)
    if not sock.exists():
        return False
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(2)
    try:
        s.connect(str(sock))
        return True
    except OSError:
        return False
    finally:
        s.close()


def release(person: str, root: Optional[str] = None, run: Optional[str] = None) -> dict:
    validate_person(person)
    with _Locked(state_root(root)) as r:
        slots = _read_slots(r)
        if person not in slots:
            raise LookupError(f"{person!r} has no slot")
        if daemon_alive(person, run):
            raise DaemonAlive(f"{person!r} daemon is still running on {socket_for(person, run)}; stop it first")
        slot = slots.pop(person)
        _write_slots(r, slots)
        try:
            os.unlink(r / person / _MARKER)
        except FileNotFoundError:
            pass
        return {"person": person, "released": slot,
                "note": f"the state directory {r / person} and the node key in it remain; "
                        f"run 'purge {person}' to remove them"}


def purge(person: str, root: Optional[str] = None, run: Optional[str] = None) -> dict:
    """Remove a released person's state directory, node key included. Refuses
    while the daemon is alive or the person still holds a slot."""
    validate_person(person)
    with _Locked(state_root(root)) as r:
        if daemon_alive(person, run):
            raise DaemonAlive(f"{person!r} daemon is still running on {socket_for(person, run)}; stop it first")
        if person in _read_slots(r):
            raise RuntimeError(f"{person!r} still holds a slot; run 'release {person}' first")
        d = r / person
        if not d.exists() and not d.is_symlink():
            raise LookupError(f"nothing to purge: {d} does not exist")
        if d.is_symlink() or not d.is_dir():
            raise RuntimeError(f"{d} is not a plain directory; refusing")
        removed = sorted(str(x.relative_to(r)) for x in d.rglob("*")) + [person]
        shutil.rmtree(d)
        return {"person": person, "removed_dir": str(d), "removed": removed}


def repair(root: Optional[str] = None) -> dict:
    """Rebuild slots.json from the ``slot`` marker in each person directory.
    A bad or clashing marker aborts without writing; the old file is kept as
    slots.json.bad."""
    with _Locked(state_root(root)) as r:
        found: dict[str, int] = {}
        skipped = []
        for d in _person_dirs(r):
            m = r / d / _MARKER
            if not m.exists():
                skipped.append(d)
                continue
            try:
                n = int(m.read_text().strip())
                ports_for_slot(n)
            except (ValueError, OSError):
                raise SlotsFileError(f"{m} does not hold a valid slot; fix or remove it") from None
            found[d] = n
        clash = sorted(d for d, n in found.items() if list(found.values()).count(n) > 1)
        if clash:
            raise SlotsFileError(f"slot markers clash for {', '.join(clash)}; resolve by hand")
        old = _slots_path(r)
        if old.exists():
            shutil.copyfile(old, r / (_SLOTS_FILE + ".bad"))
        _write_slots(r, found)
        return {"rebuilt": dict(sorted(found.items())), "skipped_no_marker": skipped}


# ---------------------------------------------------------------- argv


def ports(person: str, root: Optional[str] = None) -> dict[str, int]:
    return ports_for_slot(_require_slot(person, root))


def argv(person: str, root: Optional[str] = None, run: Optional[str] = None) -> list[str]:
    """The exact tailscaled argv of design 5.0."""
    p = ports(person, root)
    return [
        "tailscaled",
        "--tun=userspace-networking",
        "--no-logs-no-support",
        f"--statedir={state_dir(person, root)}",
        f"--socket={socket_for(person, run)}",
        f"--socks5-server={LOOPBACK}:{p['socks']}",
        f"--outbound-http-proxy-listen={LOOPBACK}:{p['http_proxy']}",
    ]


def serve_argv(person: str, root: Optional[str] = None, run: Optional[str] = None) -> list[list[str]]:
    """The two ``tailscale serve`` commands. Node 6969 maps ONLY to the relay
    listener (27750 + n), never to the controller's own 6969."""
    p = ports(person, root)
    sock = f"--socket={socket_for(person, run)}"
    return [
        ["tailscale", sock, "serve", "--bg", f"--tcp={NODE_DEVICE_LINK_PORT}",
         f"tcp://{LOOPBACK}:{p['device_link']}"],
        ["tailscale", sock, "serve", "--bg", f"--tcp={NODE_RELAY_PORT}",
         f"tcp://{LOOPBACK}:{p['relay']}"],
    ]


def apply_serve(person: str, root: Optional[str] = None, run: Optional[str] = None,
                runner: Callable = subprocess.run) -> list[list[str]]:
    """Run the serve commands. Only the instance's user or the superuser can
    drive the LocalAPI, so refuse anyone else. Never called automatically."""
    cmds = serve_argv(person, root, run)
    sock = socket_for(person, run)
    euid = os.geteuid()
    if euid != 0:
        try:
            owner = os.stat(sock).st_uid
        except OSError as e:
            raise PermissionError(f"cannot stat {sock}: {e}") from None
        if owner != euid:
            raise PermissionError(f"{sock} belongs to uid {owner}; only that user or the superuser may drive it")
    for c in cmds:
        runner(c, check=True, timeout=30)
    return cmds


# ---------------------------------------------------------------- exec


def check_ports(person: str, root: Optional[str] = None) -> list[int]:
    """Busy ports among the two tailscaled itself binds."""
    return busy_ports(_require_slot(person, root), _DAEMON_PORTS)


def _ensure_dirs(person: str, root: Optional[str], run: Optional[str]) -> None:
    sd = state_dir(person, root)
    if not sd.exists():
        _mkdirs(sd, 0o700)
    elif sd.stat().st_uid == os.geteuid():
        os.chmod(sd, 0o700)
    rr = run_root(run)
    if not rr.exists():
        _mkdirs(rr, 0o755)


def _secure_socket(sock: Path, timeout: float = 60.0, settle: float = 5.0) -> None:
    """Wait for the socket, chmod it 0600, and keep it there for a few seconds
    in case tailscaled widens it again just after creating it."""
    end = time.monotonic() + timeout
    while time.monotonic() < end and not sock.is_socket():
        time.sleep(0.05)
    if not sock.is_socket():
        return
    settle_end = time.monotonic() + settle
    while time.monotonic() < settle_end:
        try:
            if (sock.stat().st_mode & 0o777) != 0o600:
                os.chmod(sock, 0o600)
        except OSError:
            pass
        time.sleep(0.05)


def _remove_stale_socket(sock: Path) -> None:
    if sock.exists() or sock.is_symlink():
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(str(sock))
            raise DaemonAlive(f"{sock} is live; refusing to start a second daemon")
        except OSError:
            sock.unlink()
        finally:
            s.close()


def exec_daemon(person: str, root: Optional[str] = None, run: Optional[str] = None) -> None:
    """Validate, check ports, ensure dirs, fork the socket-securing watcher, then
    replace this process with tailscaled."""
    validate_person(person)
    cmd = argv(person, root, run)
    busy = check_ports(person, root)
    if busy:
        raise OSError(errno.EADDRINUSE, f"ports busy on {LOOPBACK}: {busy}")
    _ensure_dirs(person, root, run)
    sock = socket_for(person, run)
    _remove_stale_socket(sock)
    if os.fork() == 0:  # watcher
        try:
            os.setsid()
            _secure_socket(sock)
        finally:
            os._exit(0)
    os.execvp(cmd[0], cmd)


# ---------------------------------------------------------------- owner step


def _unit_source() -> Path:
    return Path(__file__).resolve().parents[2] / "systemd" / UNIT_NAME


def render_unit(python: str, source: Optional[Path] = None) -> str:
    if not python.startswith("/") or re.search(r"[\s%$\"'\\]", python):
        raise ValueError(f"unusable python path for a unit file: {python!r}")
    text = (source or _unit_source()).read_text()
    if PYTHON_TOKEN not in text:
        raise ValueError("unit template has no python placeholder")
    return text.replace(PYTHON_TOKEN, python)


def _user_exists(name: str) -> bool:
    try:
        pwd.getpwnam(name)
        return True
    except KeyError:
        return False


def install_template(*, unit_dir: Path = Path("/etc/systemd/system"),
                     runner: Callable = subprocess.run,
                     python: Optional[str] = None,
                     state: Optional[str] = None,
                     run: Optional[str] = None,
                     user_exists: Callable[[str], bool] = _user_exists,
                     chown: Callable = shutil.chown,
                     source: Optional[Path] = None) -> list[str]:
    """OWNER STEP. Refuses unless the effective uid is 0. Creates the service
    user if missing, writes the unit, runs daemon-reload. Enables and starts
    nothing. Returns the lines describing what it did."""
    if os.geteuid() != 0:
        raise PermissionError("install-template is an owner step: run it as the superuser")
    python = python or sys.executable
    text = render_unit(python, source)
    done = []
    if not user_exists(SERVICE_USER):
        runner(["useradd", "-r", "-M", "-s", "/usr/sbin/nologin", SERVICE_USER], check=True)
        done.append(f"created system user {SERVICE_USER}")
    else:
        done.append(f"system user {SERVICE_USER} already exists")
    sroot = state_root(state)
    if not sroot.exists():
        _mkdirs(sroot, 0o700)
        chown(sroot, SERVICE_USER, SERVICE_GROUP)
        done.append(f"created {sroot} owned by {SERVICE_USER}, mode 0700")
    target = unit_dir / UNIT_NAME
    target.write_text(text)
    os.chmod(target, 0o644)
    done.append(f"wrote {target} (python {python})")
    runner(["systemctl", "daemon-reload"], check=True)
    done.append("ran systemctl daemon-reload")
    done.append("nothing was enabled or started")
    done.append("next, per person <p>:")
    done.append(f"  as {SERVICE_USER}: {python} -m tinyagentos.taosnet.mesh_instances allocate <p>")
    done.append("  then:    systemctl enable --now taos-mesh@<p>")
    return done


# ---------------------------------------------------------------- CLI


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="mesh_instances", description=__doc__.split("\n")[0])
    ap.add_argument("--state-root", default=None, help=f"default {DEFAULT_STATE_ROOT} or ${ENV_STATE_ROOT}")
    ap.add_argument("--run-root", default=None, help=f"default {DEFAULT_RUN_ROOT} or ${ENV_RUN_ROOT}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("allocate", "release", "purge", "argv", "exec", "check-ports", "serve"):
        sub.add_parser(name).add_argument("person")
    st = sub.add_parser("status")
    st.add_argument("person", nargs="?")
    sub.add_parser("repair")
    sub.add_parser("install-template")
    return ap


def main(args: Optional[list[str]] = None) -> int:
    ns = _build_parser().parse_args(args)
    root, run = ns.state_root, ns.run_root
    try:
        if ns.cmd == "allocate":
            out = allocate(ns.person, root)
            if out["skipped"]:
                print("skipped busy slots: " + json.dumps(out["skipped"]), file=sys.stderr)
            print(json.dumps(out))
        elif ns.cmd == "release":
            print(json.dumps(release(ns.person, root, run)))
        elif ns.cmd == "purge":
            out = purge(ns.person, root, run)
            print(f"removed {out['removed_dir']} ({len(out['removed'])} entries, node key included)")
            print(json.dumps(out))
        elif ns.cmd == "status":
            slots = _read_slots(state_root(root))
            names = [validate_person(ns.person)] if ns.person else sorted(slots)
            print(json.dumps({p: {"slot": slots.get(p),
                                  "ports": ports_for_slot(slots[p]) if p in slots else None,
                                  "alive": daemon_alive(p, run)} for p in names}))
        elif ns.cmd == "repair":
            print(json.dumps(repair(root)))
        elif ns.cmd == "argv":
            print(json.dumps(argv(ns.person, root, run)))
        elif ns.cmd == "check-ports":
            busy = check_ports(ns.person, root)
            if busy:
                print(f"ports busy on {LOOPBACK}: {busy}", file=sys.stderr)
                return 1
            print("ok")
        elif ns.cmd == "serve":
            print(json.dumps(apply_serve(ns.person, root, run)))
        elif ns.cmd == "exec":
            exec_daemon(ns.person, root, run)
        elif ns.cmd == "install-template":
            for line in install_template(state=root, run=run):
                print(line)
    except (ValueError, LookupError, RuntimeError, SlotsFileError, SlotsExhausted, DaemonAlive,
            PermissionError, OSError, subprocess.SubprocessError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
