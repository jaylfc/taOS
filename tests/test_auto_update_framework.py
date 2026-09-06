import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_poll_frameworks_populates_cache(monkeypatch):
    from tinyagentos.auto_update import poll_frameworks
    fake = {"tag": "T1", "sha": "a1a1a1a", "full_sha": "a1a1a1a...",
            "published_at": "x", "asset_url": "u"}
    monkeypatch.setattr(
        "tinyagentos.github_releases.fetch_latest_release",
        AsyncMock(return_value=fake),
    )
    manifests = {"openclaw": {"release_source": "github:a/b",
                              "release_asset_pattern": "x-{arch}.tgz"}}
    cache = {}
    await poll_frameworks(manifests, http_client=MagicMock(), arch="x86_64", cache=cache)
    assert cache["openclaw"] == fake


@pytest.mark.asyncio
async def test_poll_frameworks_keeps_last_good_on_failure(monkeypatch):
    from tinyagentos.auto_update import poll_frameworks
    monkeypatch.setattr(
        "tinyagentos.github_releases.fetch_latest_release",
        AsyncMock(side_effect=RuntimeError("rate limit")),
    )
    manifests = {"openclaw": {"release_source": "github:a/b",
                              "release_asset_pattern": "x-{arch}.tgz"}}
    cache = {"openclaw": {"tag": "OLD"}}
    await poll_frameworks(manifests, http_client=MagicMock(), arch="x86_64", cache=cache)
    assert cache["openclaw"]["tag"] == "OLD"


@pytest.mark.asyncio
async def test_poll_frameworks_skips_when_no_release_source(monkeypatch):
    from tinyagentos.auto_update import poll_frameworks
    called = AsyncMock()
    monkeypatch.setattr("tinyagentos.github_releases.fetch_latest_release", called)
    await poll_frameworks({"x": {}}, http_client=MagicMock(), arch="x86_64", cache={})
    called.assert_not_called()
