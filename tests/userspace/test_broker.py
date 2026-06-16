import pytest
from tinyagentos.userspace.broker import handle_capability, FREE_CAPS, GATED_CAPS
from tinyagentos.userspace.data_store import UserspaceDataStore


async def _store(tmp_path):
    s = UserspaceDataStore(tmp_path / "d.db"); await s.init(); return s


@pytest.mark.asyncio
async def test_ungranted_gated_capability_denied(tmp_path):
    s = await _store(tmp_path)
    out = await handle_capability("todo", "app.memory.search", {"q": "x"},
                                  granted=[], data_store=s, app_dir=tmp_path / "todo", services={})
    assert out == {"error": "permission_denied", "capability": "app.memory.search"}
    await s.close()


@pytest.mark.asyncio
async def test_free_kv_capability_allowed_and_scoped(tmp_path):
    s = await _store(tmp_path)
    await handle_capability("todo", "app.kv.set", {"key": "k", "value": 1},
                            granted=[], data_store=s, app_dir=tmp_path / "todo", services={})
    out = await handle_capability("todo", "app.kv.get", {"key": "k"},
                                  granted=[], data_store=s, app_dir=tmp_path / "todo", services={})
    assert out["result"] == 1
    other = await handle_capability("evil", "app.kv.get", {"key": "k"},
                                    granted=[], data_store=s, app_dir=tmp_path / "evil", services={})
    assert other["result"] is None   # evil app cannot see todo's data
    await s.close()


@pytest.mark.asyncio
async def test_table_capabilities(tmp_path):
    s = await _store(tmp_path)
    ins = await handle_capability("todo", "app.table.insert", {"table": "t", "row": {"x": 1}},
                                  granted=[], data_store=s, app_dir=tmp_path / "todo", services={})
    assert isinstance(ins["result"], int)
    q = await handle_capability("todo", "app.table.query", {"table": "t"},
                                granted=[], data_store=s, app_dir=tmp_path / "todo", services={})
    assert q["result"][0]["x"] == 1
    await s.close()


@pytest.mark.asyncio
async def test_gated_capability_allowed_when_granted(tmp_path):
    s = await _store(tmp_path)

    class FakeMemory:
        async def search(self, q):
            return [{"text": "hit"}]

    out = await handle_capability("todo", "app.memory.search", {"q": "x"},
                                  granted=["app.memory"], data_store=s,
                                  app_dir=tmp_path / "todo", services={"memory": FakeMemory()})
    assert out["result"] == [{"text": "hit"}]
    await s.close()


@pytest.mark.asyncio
async def test_files_jailed_to_app_dir(tmp_path):
    s = await _store(tmp_path)
    (tmp_path / "todo" / "files").mkdir(parents=True)
    out = await handle_capability("todo", "app.files.read", {"path": "../../etc/passwd"},
                                  granted=[], data_store=s, app_dir=tmp_path / "todo", services={})
    assert out["error"] == "invalid_path"
    # legit write+read within the jail works
    await handle_capability("todo", "app.files.write", {"path": "note.txt", "content": "hi"},
                            granted=[], data_store=s, app_dir=tmp_path / "todo", services={})
    rd = await handle_capability("todo", "app.files.read", {"path": "note.txt"},
                                 granted=[], data_store=s, app_dir=tmp_path / "todo", services={})
    assert rd["result"] == "hi"
    await s.close()


@pytest.mark.asyncio
async def test_unknown_capability_rejected(tmp_path):
    s = await _store(tmp_path)
    out = await handle_capability("todo", "app.evil.hack", {}, granted=["app.evil"],
                                  data_store=s, app_dir=tmp_path / "todo", services={})
    assert out["error"] == "unknown_capability"
    await s.close()


def test_capability_sets():
    assert "app.net" in GATED_CAPS and "app.memory" in GATED_CAPS
    assert "app.kv" in FREE_CAPS and "app.net" not in FREE_CAPS


# --- Finding 1: app.net path traversal + header filtering ---

import types
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_app_net_rejects_dotdot_traversal(tmp_path):
    """.. segments in the path must be blocked before any HTTP call is made."""
    s = await _store(tmp_path)
    out = await handle_capability(
        "echo", "app.net", {"path": "../../secret"},
        granted=["app.net"], data_store=s, app_dir=tmp_path / "echo",
        services={"app_backend_url": "http://127.0.0.1:13042"},
    )
    assert out == {"error": "invalid_path"}
    await s.close()


@pytest.mark.asyncio
async def test_app_net_blocks_dangerous_headers_and_passes_safe_ones(tmp_path):
    """Host (and other blocked headers) must be stripped; X-Ok must reach the backend."""
    s = await _store(tmp_path)
    fake_resp = types.SimpleNamespace(status_code=200, text="ok")
    with patch("tinyagentos.userspace.broker.httpx.AsyncClient") as mock_client:
        mock_request = AsyncMock(return_value=fake_resp)
        mock_client.return_value.__aenter__ = AsyncMock(
            return_value=types.SimpleNamespace(request=mock_request)
        )
        mock_client.return_value.__aexit__ = AsyncMock(return_value=False)

        out = await handle_capability(
            "echo", "app.net", {"path": "/ping", "headers": {"Host": "evil", "X-Ok": "1"}},
            granted=["app.net"], data_store=s, app_dir=tmp_path / "echo",
            services={"app_backend_url": "http://127.0.0.1:13042"},
        )

    assert out == {"result": {"status": 200, "body": "ok"}}
    _, call_kwargs = mock_request.call_args
    sent_headers = call_kwargs.get("headers") or {}
    lower_keys = {k.lower() for k in sent_headers}
    assert "host" not in lower_keys, "Host header must be stripped"
    assert "x-ok" in lower_keys, "X-Ok header must be forwarded"
    await s.close()


@pytest.mark.asyncio
async def test_files_write_to_jail_root_rejected(tmp_path):
    # app.files.write with an empty path targets the jail root (a directory) and
    # must return invalid_path, not raise an uncaught IsADirectoryError (a 500).
    s = await _store(tmp_path)
    (tmp_path / "todo" / "files").mkdir(parents=True)
    out = await handle_capability("todo", "app.files.write", {"path": "", "content": "x"},
                                  granted=[], data_store=s, app_dir=tmp_path / "todo", services={})
    assert out["error"] == "invalid_path"
    await s.close()
