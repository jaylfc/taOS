"""Tests for the list_frameworks agent tool."""
import types

import pytest

from tinyagentos.tools.framework_tools import execute_list_frameworks


def _req():
    return types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace()),
        state=types.SimpleNamespace(user_id="user-1"),
    )


@pytest.mark.asyncio
async def test_lists_all_frameworks():
    res = await execute_list_frameworks({}, _req())
    assert res["ok"] is True
    assert res["count"] >= 1
    ids = {f["id"] for f in res["frameworks"]}
    # The adapter registry ships at least openclaw and hermes.
    assert "openclaw" in ids and "hermes" in ids
    for f in res["frameworks"]:
        assert set(f.keys()) == {"id", "name", "description", "verification_status"}


@pytest.mark.asyncio
async def test_verified_only_returns_verified_subset():
    all_res = await execute_list_frameworks({}, _req())
    verified_res = await execute_list_frameworks({"verified_only": True}, _req())
    assert verified_res["count"] <= all_res["count"]
    # tested and beta are the two verified tiers; nothing else may appear.
    assert all(
        f["verification_status"] in ("tested", "beta")
        for f in verified_res["frameworks"]
    )
    # hermes is tested and openclaw is beta, so verified_only must include both.
    ids = {f["id"] for f in verified_res["frameworks"]}
    assert "openclaw" in ids and "hermes" in ids
