"""Storage engine: add a disk as a storage area, make an area a person's primary home.

The engine never formats anything itself. It asks the root helper
(scripts/taos-storage-helper, installed by scripts/install-storage-helper.sh and
reachable only through a sudoers entry for the service account), which re-runs
the safety classifier and re-checks the confirm string before it writes. The
engine owns the registry of storage areas, the per-person primary-home
assignment, capacity reporting and the consent flow for agents.

Registry file: ``<data_dir>/storage/registry.json`` (versioned, written
atomically, never silently reset: an unreadable registry is an error).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import subprocess
import threading
import time
from pathlib import Path

HELPER = "/usr/local/sbin/taos-storage-helper"
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,30}[a-z0-9]$")
PERSON_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
KINDS = ("private", "shared")
BY_ID_PREFIX = "/dev/disk/by-id/"
REGISTRY_VERSION = 1


class StorageError(Exception):
    """A request the engine refuses or could not complete; ``status`` is the HTTP status."""

    def __init__(self, message: str, status: int = 400, detail: dict | None = None):
        super().__init__(message)
        self.status = status
        self.detail = detail or {}


def validate_name(name) -> str:
    if not isinstance(name, str) or not NAME_RE.match(name) or "--" in name:
        raise StorageError("area name: 2-32 characters, lowercase letters, digits and single dashes, starting with a letter")
    return name


def validate_person(person) -> str:
    if not isinstance(person, str) or not PERSON_RE.match(person):
        raise StorageError("bad person id")
    return person


def validate_disk(disk) -> str:
    if (not isinstance(disk, str) or not disk.startswith(BY_ID_PREFIX) or "/" in disk[len(BY_ID_PREFIX):]
            or ".." in disk or "-part" in disk):
        raise StorageError("the disk must be a whole-disk /dev/disk/by-id path")
    return disk


class StorageEngine:
    def __init__(self, data_dir: Path, helper_argv: list | None = None, timeout: float = 3600.0):
        self.dir = Path(data_dir) / "storage"
        self.registry_path = self.dir / "registry.json"
        self.helper_argv = list(helper_argv) if helper_argv is not None else ["sudo", "-n", HELPER]
        self.timeout = timeout
        self._lock = threading.Lock()

    # ------------------------------------------------------------ registry

    def _load(self) -> dict:
        try:
            raw = self.registry_path.read_text()
        except FileNotFoundError:
            return {"version": REGISTRY_VERSION, "areas": {}, "primary": {}, "proposals": {}}
        except OSError as exc:
            raise StorageError(f"cannot read the storage registry: {exc}", 500)
        try:
            doc = json.loads(raw)
            if not isinstance(doc, dict) or doc.get("version") != REGISTRY_VERSION:
                raise ValueError("unexpected shape or version")
            for k in ("areas", "primary", "proposals"):
                if not isinstance(doc.get(k), dict):
                    raise ValueError(f"'{k}' missing")
            return doc
        except ValueError as exc:
            raise StorageError(f"the storage registry is damaged and was left untouched: {exc}", 500)

    def _save(self, doc: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / f".registry-{secrets.token_hex(4)}.tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(doc, f, indent=1, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.registry_path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    # ----------------------------------------------------------- the helper

    def _helper_sync(self, *args: str) -> dict:
        try:
            p = subprocess.run([*self.helper_argv, *args], capture_output=True, text=True,
                               timeout=self.timeout, check=False)
        except FileNotFoundError:
            raise StorageError("the storage helper is not installed on this machine "
                               "(run scripts/install-storage-helper.sh as root)", 503)
        except subprocess.TimeoutExpired:
            raise StorageError("the storage helper timed out; the disk state is unknown, check it before retrying", 504)
        try:
            out = json.loads(p.stdout)
            if not isinstance(out, dict):
                raise ValueError
        except ValueError:
            raise StorageError("the storage helper could not be run: " + (p.stderr.strip()[:300] or f"exit {p.returncode}"), 503)
        return out

    async def _helper(self, *args: str) -> dict:
        return await asyncio.to_thread(self._helper_sync, *args)

    async def helper_status(self) -> dict:
        return await self._helper("status")

    async def list_disks(self) -> dict:
        out = await self._helper("list")
        if not out.get("ok"):
            raise StorageError("cannot list disks safely: " + str(out.get("error")), 503)
        return {"disks": out["disks"]}

    # ---------------------------------------------------------------- areas

    def _area_view(self, a: dict) -> dict:
        view = dict(a)
        mount = a["mount"]
        try:
            mounted = os.path.ismount(mount)
            st = os.statvfs(mount) if mounted else None
        except OSError:
            mounted, st = None, None
        view["mounted"] = mounted
        view["total_bytes"] = st.f_frsize * st.f_blocks if st else None   # None = unknown, never 0
        view["free_bytes"] = st.f_frsize * st.f_bavail if st else None
        return view

    def list_areas(self) -> list:
        return [self._area_view(a) for a in self._load()["areas"].values()]

    async def add_area(self, disk: str, name: str, kind: str, confirm: str, requested_by: str) -> dict:
        validate_disk(disk)
        validate_name(name)
        if kind not in KINDS:
            raise StorageError("kind must be 'private' (btrfs + LUKS2) or 'shared' (VeraCrypt NTFS, shared with Windows)")
        if not isinstance(confirm, str) or not confirm:
            raise StorageError("type the disk's model and serial to confirm")
        if name in self._load()["areas"]:
            raise StorageError(f"a storage area called {name} already exists", 409)
        out = await self._helper("add", "--device", disk, "--name", name, "--kind", kind, "--confirm", confirm)
        if not out.get("ok"):
            status = 409 if out.get("refused") else 500
            raise StorageError(out.get("error") or "the helper failed", status, {
                k: out.get(k) for k in ("failed_step", "steps", "state", "note") if k in out})
        h = out["area"]
        area = {
            "name": name, "kind": kind, "mount": h["mount"], "size": h["size"], "fstype": h["fstype"],
            "encryption": h["encryption"], "disk_serial": h["serial"], "disk_model": h["model"],
            "disk_by_id": disk, "created": h["created"], "created_by": requested_by,
        }
        try:
            with self._lock:
                doc = self._load()
                doc["areas"][name] = area
                self._save(doc)
        except Exception as exc:
            raise StorageError(
                f"the disk WAS formatted and mounted at {h['mount']}, but taOS could not record it: {exc}. "
                "The root helper keeps its own record; do not format this disk again.", 500, {"area": area})
        return self._area_view(area)

    # -------------------------------------------------------------- primary

    async def set_primary(self, person: str, area: str, requested_by: str) -> dict:
        validate_person(person)
        validate_name(area)
        if area not in self._load()["areas"]:
            raise StorageError(f"no storage area called {area}", 404)
        out = await self._helper("prepare-home", "--area", area, "--person", person)
        if not out.get("ok"):
            raise StorageError(out.get("error") or "the helper failed", 409 if out.get("refused") else 500)
        with self._lock:
            doc = self._load()
            previous = doc["primary"].get(person)
            doc["primary"][person] = {"area": area, "path": out["path"], "set_by": requested_by, "set_at": time.time()}
            self._save(doc)
        res = self.primary_home(person)
        res["previous_area"] = previous["area"] if previous else None
        res["note"] = ("existing files were not moved; this only records the assignment and prepares the directory"
                       if previous else "directory created")
        return res

    def primary_home(self, person: str) -> dict | None:
        validate_person(person)
        doc = self._load()
        p = doc["primary"].get(person)
        if not p:
            return None
        a = doc["areas"].get(p["area"])
        view = self._area_view(a) if a else {"mounted": None, "total_bytes": None, "free_bytes": None}
        return {"person": person, "area": p["area"], "path": p["path"], "kind": a["kind"] if a else None,
                "mounted": view["mounted"], "total_bytes": view["total_bytes"], "free_bytes": view["free_bytes"]}

    # ------------------------------------------------------------ proposals

    def propose(self, action: str, params: dict, by: str, on_behalf_of: str | None) -> dict:
        if action == "add":
            params = {"disk": validate_disk(params.get("disk")), "name": validate_name(params.get("name")),
                      "kind": params.get("kind")}
            if params["kind"] not in KINDS:
                raise StorageError("bad kind")
        elif action == "set-primary":
            params = {"person": validate_person(params.get("person")), "area": validate_name(params.get("area"))}
        else:
            raise StorageError("action must be 'add' or 'set-primary'")
        pid = "sp-" + secrets.token_hex(4)
        with self._lock:
            doc = self._load()
            doc["proposals"][pid] = {"id": pid, "action": action, "params": params, "by": by,
                                      "on_behalf_of": on_behalf_of, "status": "pending", "created": time.time()}
            self._save(doc)
        return doc["proposals"][pid]

    def proposals(self) -> list:
        return sorted(self._load()["proposals"].values(), key=lambda p: p["created"])

    def _take(self, pid: str, new_status: str) -> dict:
        with self._lock:
            doc = self._load()
            p = doc["proposals"].get(pid)
            if not p:
                raise StorageError("no such proposal", 404)
            if p["status"] != "pending":
                raise StorageError(f"proposal is already {p['status']}", 409)
            p["status"] = new_status
            p["decided"] = time.time()
            self._save(doc)
            return dict(p)

    def reject(self, pid: str) -> dict:
        return self._take(pid, "rejected")

    async def approve(self, pid: str, confirm: str | None, approver: str) -> dict:
        """A person approves a pending proposal. Adding a disk still needs the typed confirmation."""
        p = self._load()["proposals"].get(pid)
        if not p:
            raise StorageError("no such proposal", 404)
        if p["status"] != "pending":
            raise StorageError(f"proposal is already {p['status']}", 409)
        if p["action"] == "add" and not confirm:
            raise StorageError("type the disk's model and serial to approve adding it")
        self._take(pid, "approved")
        try:
            if p["action"] == "add":
                result = await self.add_area(p["params"]["disk"], p["params"]["name"], p["params"]["kind"], confirm, approver)
            else:
                result = await self.set_primary(p["params"]["person"], p["params"]["area"], approver)
        except StorageError as exc:
            with self._lock:
                doc = self._load()
                # A refusal wrote nothing (e.g. a mistyped confirmation): the person may try again.
                doc["proposals"][pid]["status"] = "pending" if exc.status == 409 else "failed"
                doc["proposals"][pid]["error"] = str(exc)
                self._save(doc)
            raise
        return {"proposal": pid, "result": result}
