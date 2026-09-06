import time

import pytest

from tinyagentos.cluster.capability_map import CapabilityMap


async def _store(tmp_path):
    s = CapabilityMap(tmp_path / "cap.db")
    await s.init()
    return s


def _node(node_id="n1", status="online", last_seen=1000):
    return {
        "node_id": node_id,
        "hostname": f"host-{node_id}",
        "cpu": {"arch": "aarch64", "cores": 8, "soc": "rk3588"},
        "ram_mb": 16384,
        "gpu": {"type": "mali", "vram_mb": 0, "cuda": False, "vulkan": True},
        "npu": {"type": "rknpu", "tops": 6},
        "status": status,
        "last_seen": last_seen,
    }


@pytest.mark.asyncio
async def test_upsert_get_roundtrip(tmp_path):
    s = await _store(tmp_path)
    out = await s.upsert(_node())
    assert out["node_id"] == "n1"
    assert out["cpu"]["soc"] == "rk3588"
    got = await s.get("n1")
    assert got["gpu"]["vulkan"] is True
    assert got["npu"]["tops"] == 6
    assert got["ram_mb"] == 16384
    await s.close()


@pytest.mark.asyncio
async def test_upsert_updates_not_duplicates(tmp_path):
    s = await _store(tmp_path)
    await s.upsert(_node(status="online"))
    await s.upsert(_node(status="draining"))
    assert (await s.get("n1"))["status"] == "draining"
    assert len(await s.list()) == 1
    await s.close()


@pytest.mark.asyncio
async def test_list_status_filter(tmp_path):
    s = await _store(tmp_path)
    await s.upsert(_node("n1", "online"))
    await s.upsert(_node("n2", "offline"))
    assert {n["node_id"] for n in await s.list(status="online")} == {"n1"}
    assert len(await s.list()) == 2
    await s.close()


@pytest.mark.asyncio
async def test_set_status(tmp_path):
    s = await _store(tmp_path)
    await s.upsert(_node())
    out = await s.set_status("n1", "draining")
    assert out["status"] == "draining"
    assert await s.set_status("missing", "online") is None
    await s.close()


@pytest.mark.asyncio
async def test_set_status_rejects_invalid(tmp_path):
    s = await _store(tmp_path)
    await s.upsert(_node())
    with pytest.raises(ValueError):
        await s.set_status("n1", "bogus")
    await s.close()


@pytest.mark.asyncio
async def test_upsert_rejects_invalid_status(tmp_path):
    s = await _store(tmp_path)
    with pytest.raises(ValueError):
        await s.upsert(_node(status="bogus"))
    await s.close()


@pytest.mark.asyncio
async def test_get_unknown_returns_none(tmp_path):
    s = await _store(tmp_path)
    assert await s.get("nope") is None
    await s.close()


@pytest.mark.asyncio
async def test_prune_stale(tmp_path):
    s = await _store(tmp_path)
    await s.upsert(_node("old", "online", last_seen=1000))
    await s.upsert(_node("fresh", "online", last_seen=int(time.time())))
    pruned = await s.prune_stale(older_than_s=3600)
    assert pruned == 1
    assert await s.get("old") is None
    assert await s.get("fresh") is not None
    await s.close()


@pytest.mark.asyncio
async def test_upsert_preserves_explicit_zero_last_seen(tmp_path):
    s = await _store(tmp_path)
    out = await s.upsert(_node(last_seen=0))
    assert out["last_seen"] == 0
    await s.close()


@pytest.mark.asyncio
async def test_upsert_requires_node_id(tmp_path):
    s = await _store(tmp_path)
    with pytest.raises(ValueError):
        await s.upsert({"hostname": "x"})
    await s.close()


@pytest.mark.asyncio
async def test_mark_stale_offline_preserves_row(tmp_path):
    s = CapabilityMap(tmp_path / "cap.db")
    await s.init()
    # Fresh online node (recent last_seen) + a stale online one + a draining one.
    await s.upsert({"node_id": "fresh", "status": "online"})
    await s.upsert({"node_id": "stale", "status": "online", "last_seen": 1})
    await s.upsert({"node_id": "drain", "status": "draining", "last_seen": 1})
    flipped = await s.mark_stale_offline(older_than_s=60)
    assert flipped == 1
    assert (await s.get("stale"))["status"] == "offline"
    assert (await s.get("fresh"))["status"] == "online"
    # draining is an admin intent; a stale sweep must not touch it.
    assert (await s.get("drain"))["status"] == "draining"
    # The row is preserved, not deleted.
    assert await s.get("stale") is not None
    await s.close()
