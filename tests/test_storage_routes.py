"""Storage API through the real app, with a fake helper executable.

The fake helper is a real subprocess (so the engine's sudo-style call path,
argument handling and JSON parsing are exercised), but it only reads and writes
files under tmp_path: no device is ever involved.
"""
from __future__ import annotations

import json
import os
import sys
import textwrap

import pytest
from httpx import ASGITransport, AsyncClient

from taos_test_csrf import csrf_event_hooks
from tinyagentos.storage_engine import StorageEngine

DISK = "/dev/disk/by-id/ata-FAKEDRIVE_1000_FAKE-SERIAL-0001"
CONFIRM = "FAKEDRIVE 1000 FAKE-SERIAL-0001"

FAKE = textwrap.dedent('''
    import json, os, sys
    state = os.environ["FAKE_STATE"]
    st = json.load(open(state))
    st["calls"].append(sys.argv[1:])
    json.dump(st, open(state, "w"))
    a = sys.argv[1:]
    def opt(n): return a[a.index(n) + 1]
    if a[0] == "status":
        print(json.dumps({"ok": True, "tools": {}, "veracrypt": False, "shared_ready": False}))
    elif a[0] == "list":
        print(json.dumps({"ok": True, "error": None, "disks": st["disks"]}))
    elif a[0] == "add":
        if st.get("broken"):
            sys.stdout.write("this is not json"); sys.exit(3)
        if opt("--confirm") != "FAKEDRIVE 1000 FAKE-SERIAL-0001":
            print(json.dumps({"ok": False, "refused": True, "error": "the confirmation does not match", "steps": []})); sys.exit(1)
        if st.get("midfail"):
            print(json.dumps({"ok": False, "refused": False, "failed_step": "encrypt", "error": "cryptsetup exited 1",
                              "steps": ["partitioned", "key"], "state": "part-way"})); sys.exit(1)
        mount = os.path.join(st["mount_root"], opt("--name"))
        os.makedirs(mount, exist_ok=True)
        print(json.dumps({"ok": True, "steps": ["mounted"], "area": {
            "name": opt("--name"), "kind": opt("--kind"), "mount": mount, "size": 1000, "fstype": "btrfs",
            "encryption": "luks2", "serial": "FAKE-SERIAL-0001", "model": "FAKEDRIVE 1000", "created": 1.0}}))
    elif a[0] == "prepare-home":
        p = os.path.join(st["mount_root"], opt("--area"), "people", opt("--person"))
        os.makedirs(p, exist_ok=True)
        print(json.dumps({"ok": True, "path": p}))
''')


@pytest.fixture
def fake(tmp_path, app, monkeypatch):
    state = tmp_path / "fake-state.json"
    root = tmp_path / "srv"
    root.mkdir()
    state.write_text(json.dumps({"calls": [], "mount_root": str(root), "disks": [
        {"path": "/dev/sdb", "by_id": DISK, "class": "available", "reasons": [], "model": "FAKEDRIVE 1000",
         "serial": "FAKE-SERIAL-0001", "size": 1000, "confirm": CONFIRM},
        {"path": "/dev/sda", "by_id": "/dev/disk/by-id/ata-SYS", "class": "protected",
         "reasons": ["system disk (holds the running system)"], "model": "S", "serial": "1", "size": 9, "confirm": "S 1"},
    ]}))
    script = tmp_path / "fake-helper.py"
    script.write_text(FAKE)
    monkeypatch.setenv("FAKE_STATE", str(state))
    app.state.storage_engine = StorageEngine(tmp_path / "data", helper_argv=[sys.executable, str(script)])
    real = os.path.ismount
    monkeypatch.setattr(os.path, "ismount", lambda p: str(p).startswith(str(root)) or real(p))

    class F:
        def calls(self):
            return json.loads(state.read_text())["calls"]

        def set(self, **kw):
            d = json.loads(state.read_text())
            d.update(kw)
            state.write_text(json.dumps(d))

    return F()


async def _member(app, username="member"):
    auth_mgr = app.state.auth
    code = auth_mgr.add_user_invite(username, "admin")
    auth_mgr.complete_invite(username, code, "Test Member", "", "memberstorage123")
    rec = auth_mgr.find_user(username)
    token = auth_mgr.create_session(user_id=rec["id"], long_lived=True)
    c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                    cookies={"taos_session": token}, event_hooks=csrf_event_hooks())
    return c, rec["id"]


ADD = {"disk": DISK, "name": "photos", "kind": "private", "confirm": CONFIRM}


@pytest.mark.asyncio
async def test_disks_lists_class_and_reasons(client, fake):
    r = await client.get("/api/storage/disks")
    assert r.status_code == 200
    disks = {d["path"]: d for d in r.json()["disks"]}
    assert disks["/dev/sdb"]["class"] == "available"
    assert disks["/dev/sda"]["class"] == "protected" and disks["/dev/sda"]["reasons"]


@pytest.mark.asyncio
async def test_add_area_records_it_and_reports_capacity(client, fake):
    r = await client.post("/api/storage/areas", json=ADD)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "photos" and r.json()["encryption"] == "luks2"
    assert fake.calls()[-1][:1] == ["add"] and "--confirm" in fake.calls()[-1]
    areas = (await client.get("/api/storage/areas")).json()["areas"]
    assert [a["name"] for a in areas] == ["photos"]
    assert areas[0]["mounted"] is True and areas[0]["total_bytes"] > 0 and areas[0]["free_bytes"] is not None


@pytest.mark.asyncio
async def test_registry_survives_a_new_engine_instance(client, app, fake, tmp_path):
    await client.post("/api/storage/areas", json=ADD)
    again = StorageEngine(tmp_path / "data")
    assert [a["name"] for a in again.list_areas()] == ["photos"]


@pytest.mark.asyncio
async def test_wrong_confirm_is_refused_and_nothing_is_recorded(client, fake):
    r = await client.post("/api/storage/areas", json={**ADD, "confirm": "FAKEDRIVE 1000"})
    assert r.status_code == 409 and "confirmation" in r.json()["error"]
    assert (await client.get("/api/storage/areas")).json()["areas"] == []


@pytest.mark.asyncio
async def test_failure_part_way_reports_the_known_state(client, fake):
    fake.set(midfail=True)
    r = await client.post("/api/storage/areas", json=ADD)
    assert r.status_code == 500
    body = r.json()
    assert body["failed_step"] == "encrypt" and body["steps"] == ["partitioned", "key"] and body["state"]
    assert (await client.get("/api/storage/areas")).json()["areas"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("patch", [
    {"disk": "/dev/sdb"}, {"disk": "/dev/disk/by-id/x-part1"}, {"disk": "/dev/disk/by-id/../../sdb"},
    {"disk": "/dev/disk/by-id/a/b"}, {"name": "Bad Name"}, {"name": "../x"}, {"kind": "ext4"}, {"confirm": ""},
])
async def test_bad_input_is_rejected_before_the_helper_is_called(client, fake, patch):
    r = await client.post("/api/storage/areas", json={**ADD, **patch})
    assert r.status_code in (400, 422)
    assert fake.calls() == []


@pytest.mark.asyncio
async def test_duplicate_name_conflicts(client, fake):
    await client.post("/api/storage/areas", json=ADD)
    n = len(fake.calls())
    r = await client.post("/api/storage/areas", json=ADD)
    assert r.status_code == 409 and len(fake.calls()) == n


@pytest.mark.asyncio
async def test_missing_or_broken_helper_is_a_plain_503(client, app, fake, tmp_path):
    fake.set(broken=True)
    r = await client.post("/api/storage/areas", json=ADD)
    assert r.status_code == 503 and "could not be run" in r.json()["error"]
    app.state.storage_engine = StorageEngine(tmp_path / "data2", helper_argv=["/nonexistent/helper"])
    r = await client.get("/api/storage/disks")
    assert r.status_code == 503 and "not installed" in r.json()["error"]


@pytest.mark.asyncio
async def test_damaged_registry_is_an_error_not_a_reset(client, fake, tmp_path):
    reg = tmp_path / "data" / "storage"
    reg.mkdir(parents=True)
    (reg / "registry.json").write_text("{ not json")
    r = await client.get("/api/storage/areas")
    assert r.status_code == 500 and "damaged" in r.json()["error"]
    assert (reg / "registry.json").read_text() == "{ not json"


@pytest.mark.asyncio
async def test_set_primary_creates_the_directory_and_exposes_capacity(client, fake):
    await client.post("/api/storage/areas", json=ADD)
    r = await client.put("/api/storage/primary", json={"person": "person-1", "area": "photos"})
    assert r.status_code == 200, r.text
    assert r.json()["path"].endswith("/photos/people/person-1") and os.path.isdir(r.json()["path"])
    got = (await client.get("/api/storage/primary", params={"person": "person-1"})).json()
    assert got["area"] == "photos" and got["total_bytes"] > 0 and got["free_bytes"] is not None


@pytest.mark.asyncio
async def test_set_primary_unknown_area_and_bad_person(client, fake):
    assert (await client.put("/api/storage/primary", json={"person": "p1", "area": "nope"})).status_code == 404
    await client.post("/api/storage/areas", json=ADD)
    assert (await client.put("/api/storage/primary", json={"person": "../x", "area": "photos"})).status_code == 400
    assert not any(c[0] == "prepare-home" and "../x" in c for c in fake.calls())


@pytest.mark.asyncio
async def test_unmounted_area_reports_unknown_capacity_not_zero(client, fake, monkeypatch):
    await client.post("/api/storage/areas", json=ADD)
    await client.put("/api/storage/primary", json={"person": "p1", "area": "photos"})
    monkeypatch.setattr(os.path, "ismount", lambda p: False)
    got = (await client.get("/api/storage/primary", params={"person": "p1"})).json()
    assert got["mounted"] is False and got["total_bytes"] is None and got["free_bytes"] is None


@pytest.mark.asyncio
async def test_member_cannot_use_storage_but_reads_only_their_own_home(client, app, fake):
    member, member_id = await _member(app)
    async with member:
        for method, path, body in [("GET", "/api/storage/disks", None), ("GET", "/api/storage/areas", None),
                                   ("POST", "/api/storage/areas", ADD),
                                   ("PUT", "/api/storage/primary", {"person": member_id, "area": "photos"}),
                                   ("GET", "/api/storage/proposals", None)]:
            r = await member.request(method, path, json=body)
            assert r.status_code == 403, (method, path, r.status_code)
        assert (await member.get("/api/storage/primary", params={"person": "someone-else"})).status_code == 403
        assert (await member.get("/api/storage/primary")).status_code == 404
    assert fake.calls() == []


# ------------------------------------------------------------------ agents


async def _agent(app, name="test-agent"):
    token = app.state.auth.mint_agent_local_token(name)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"}, event_hooks=csrf_event_hooks())


@pytest.mark.asyncio
async def test_agent_cannot_add_or_assign_directly(client, app, fake):
    async with await _agent(app) as agent:
        assert (await agent.post("/api/storage/areas", json=ADD)).status_code == 403
        assert (await agent.put("/api/storage/primary", json={"person": "p", "area": "photos"})).status_code == 403
        assert (await agent.post("/api/storage/proposals/sp-x/approve", json={"confirm": CONFIRM})).status_code == 403
    assert fake.calls() == []


@pytest.mark.asyncio
async def test_agent_proposal_needs_a_person_to_approve_with_the_typed_confirm(client, app, fake):
    async with await _agent(app) as agent:
        r = await agent.post("/api/storage/proposals", json={"action": "add",
                             "params": {"disk": DISK, "name": "photos", "kind": "private"}})
        assert r.status_code == 200, r.text
        pid = r.json()["id"]
        assert r.json()["by"] == "test-agent" and r.json()["status"] == "pending"
    assert fake.calls() == []                                    # filing a proposal changes nothing
    assert (await client.post(f"/api/storage/proposals/{pid}/approve", json={})).status_code == 400
    wrong = await client.post(f"/api/storage/proposals/{pid}/approve", json={"confirm": "nope"})
    assert wrong.status_code == 409                              # refused: still pending, may retry
    assert [p["status"] for p in (await client.get("/api/storage/proposals")).json()["proposals"]] == ["pending"]
    ok = await client.post(f"/api/storage/proposals/{pid}/approve", json={"confirm": CONFIRM})
    assert ok.status_code == 200, ok.text
    assert [a["name"] for a in (await client.get("/api/storage/areas")).json()["areas"]] == ["photos"]
    again = await client.post(f"/api/storage/proposals/{pid}/approve", json={"confirm": CONFIRM})
    assert again.status_code == 409                              # cannot be replayed


@pytest.mark.asyncio
async def test_rejected_proposal_never_runs(client, app, fake):
    async with await _agent(app) as agent:
        pid = (await agent.post("/api/storage/proposals", json={"action": "add",
               "params": {"disk": DISK, "name": "photos", "kind": "private"}})).json()["id"]
    assert (await client.post(f"/api/storage/proposals/{pid}/reject")).status_code == 200
    assert (await client.post(f"/api/storage/proposals/{pid}/approve", json={"confirm": CONFIRM})).status_code == 409
    assert fake.calls() == []


@pytest.mark.asyncio
async def test_bad_proposals_are_rejected(client, fake):
    for body in ({"action": "format", "params": {}}, {"action": "add", "params": {"disk": "/dev/sdb", "name": "x1", "kind": "private"}},
                 {"action": "set-primary", "params": {"person": "../", "area": "photos"}}):
        r = await client.post("/api/storage/proposals", json=body)
        assert r.status_code == 400, body
