"""Tests for the Coding Studio agent tool-calling loop (#86).

Covers the dispatch substrate (unit) and the two HTTP endpoints that drive it.
"""
import pytest
import pytest_asyncio

from tinyagentos.agent_tools import coding_tools


# ---------------------------------------------------------------------------
# dispatch() unit tests (no HTTP)
# ---------------------------------------------------------------------------

def test_dispatch_write_then_read_roundtrip(tmp_path):
    r = coding_tools.dispatch(tmp_path, "write_file", {"path": "a/b.txt", "content": "hi"})
    assert r["ok"] is True
    r = coding_tools.dispatch(tmp_path, "read_file", {"path": "a/b.txt"})
    assert r == {"ok": True, "result": "hi"}


def test_dispatch_unknown_tool(tmp_path):
    r = coding_tools.dispatch(tmp_path, "delete_everything", {})
    assert r["ok"] is False
    assert "unknown tool" in r["error"]


def test_dispatch_missing_argument(tmp_path):
    r = coding_tools.dispatch(tmp_path, "write_file", {"path": "x.txt"})
    assert r["ok"] is False
    assert "missing argument" in r["error"]


def test_dispatch_jail_violation_is_caught(tmp_path):
    r = coding_tools.dispatch(tmp_path, "read_file", {"path": "../escape.txt"})
    assert r["ok"] is False
    assert "refused" in r["error"]


def test_dispatch_read_missing_file(tmp_path):
    r = coding_tools.dispatch(tmp_path, "read_file", {"path": "nope.txt"})
    assert r["ok"] is False
    assert r["error"] == "file not found"


def test_dispatch_read_binary_is_soft_error(tmp_path):
    (tmp_path / "blob.bin").write_bytes(b"\xff\xfe\x00\x01\x80")
    r = coding_tools.dispatch(tmp_path, "read_file", {"path": "blob.bin"})
    assert r["ok"] is False
    assert "UTF-8" in r["error"]


def test_dispatch_read_too_large_is_soft_error(tmp_path):
    (tmp_path / "big.txt").write_text("a" * (coding_tools.MAX_READ_BYTES + 1))
    r = coding_tools.dispatch(tmp_path, "read_file", {"path": "big.txt"})
    assert r["ok"] is False
    assert "limit" in r["error"]


def test_dispatch_list_dir_defaults_to_root(tmp_path):
    coding_tools.dispatch(tmp_path, "write_file", {"path": "one.txt", "content": "1"})
    r = coding_tools.dispatch(tmp_path, "list_dir", {})
    assert r["ok"] is True
    assert "one.txt" in r["result"]


def test_dispatch_file_exists(tmp_path):
    coding_tools.dispatch(tmp_path, "write_file", {"path": "here.txt", "content": "x"})
    assert coding_tools.dispatch(tmp_path, "file_exists", {"path": "here.txt"})["result"] is True
    assert coding_tools.dispatch(tmp_path, "file_exists", {"path": "gone.txt"})["result"] is False


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def coding_client(app, client):
    store = app.state.coding_workspaces
    if store._db is not None:
        await store.close()
    await store.init()
    yield client, app


@pytest.mark.asyncio
async def test_tools_endpoint_lists_schemas(coding_client):
    client, _app = coding_client
    r = await client.get("/api/coding/tools")
    assert r.status_code == 200
    names = {t["name"] for t in r.json()["tools"]}
    assert names == {"read_file", "write_file", "file_exists", "list_dir"}


@pytest.mark.asyncio
async def test_tool_endpoint_executes_against_workspace(coding_client):
    client, _app = coding_client
    ws = (await client.post("/api/coding/workspaces", json={"name": "loop"})).json()
    wid = ws["id"]

    r = await client.post(
        f"/api/coding/workspaces/{wid}/tool",
        json={"name": "write_file", "arguments": {"path": "main.py", "content": "print(1)"}},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    r = await client.post(
        f"/api/coding/workspaces/{wid}/tool",
        json={"name": "read_file", "arguments": {"path": "main.py"}},
    )
    assert r.json() == {"ok": True, "result": "print(1)"}


@pytest.mark.asyncio
async def test_tool_endpoint_jail_violation_is_soft_error(coding_client):
    client, _app = coding_client
    ws = (await client.post("/api/coding/workspaces", json={"name": "jail"})).json()
    r = await client.post(
        f"/api/coding/workspaces/{ws['id']}/tool",
        json={"name": "read_file", "arguments": {"path": "../../etc/passwd"}},
    )
    # Soft error (HTTP 200, ok=false) so the loop can recover.
    assert r.status_code == 200
    assert r.json()["ok"] is False


@pytest.mark.asyncio
async def test_tool_endpoint_unknown_workspace_404(coding_client):
    client, _app = coding_client
    r = await client.post(
        "/api/coding/workspaces/ws-nope/tool",
        json={"name": "list_dir", "arguments": {}},
    )
    assert r.status_code == 404
