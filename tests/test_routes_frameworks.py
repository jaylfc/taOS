import pytest

from tinyagentos.adapters import list_frameworks

VALID_STATUSES = {"tested", "beta", "experimental", "broken"}


@pytest.mark.asyncio
async def test_frameworks_endpoint_returns_list(client):
    resp = await client.get("/api/frameworks")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) > 0


@pytest.mark.asyncio
async def test_frameworks_each_entry_has_required_fields(client):
    resp = await client.get("/api/frameworks")
    assert resp.status_code == 200
    for entry in resp.json():
        assert "id" in entry, f"missing id in {entry}"
        assert "name" in entry, f"missing name in {entry}"
        assert "description" in entry, f"missing description in {entry}"
        assert "verification_status" in entry, f"missing verification_status in {entry}"


@pytest.mark.asyncio
async def test_frameworks_count_matches_registry(client):
    resp = await client.get("/api/frameworks")
    assert resp.status_code == 200
    assert len(resp.json()) == len(list_frameworks())


@pytest.mark.asyncio
async def test_frameworks_all_statuses_are_valid(client):
    resp = await client.get("/api/frameworks")
    assert resp.status_code == 200
    for entry in resp.json():
        assert entry["verification_status"] in VALID_STATUSES, (
            f"{entry['id']} has unknown status {entry['verification_status']!r}"
        )


@pytest.mark.asyncio
async def test_frameworks_openclaw_is_beta(client):
    resp = await client.get("/api/frameworks")
    assert resp.status_code == 200
    openclaw = next((e for e in resp.json() if e["id"] == "openclaw"), None)
    assert openclaw is not None, "openclaw adapter missing from response"
    assert openclaw["verification_status"] == "beta", (
        f"expected openclaw to be 'beta', got {openclaw['verification_status']!r}"
    )


@pytest.mark.asyncio
async def test_frameworks_at_least_one_experimental(client):
    resp = await client.get("/api/frameworks")
    assert resp.status_code == 200
    experimental = [e for e in resp.json() if e["verification_status"] == "experimental"]
    assert len(experimental) > 0, "no experimental frameworks found"


@pytest.mark.asyncio
async def test_frameworks_no_alpha_status(client):
    """Regression guard: 'alpha' was retired in favour of 'experimental'."""
    resp = await client.get("/api/frameworks")
    assert resp.status_code == 200
    alpha = [e for e in resp.json() if e["verification_status"] == "alpha"]
    assert alpha == [], (
        f"found entries still using retired 'alpha' status: {[e['id'] for e in alpha]}"
    )


@pytest.mark.asyncio
async def test_frameworks_hermes_is_tested(client):
    resp = await client.get("/api/frameworks")
    assert resp.status_code == 200
    hermes = next((e for e in resp.json() if e["id"] == "hermes"), None)
    assert hermes is not None, "hermes adapter missing from response"
    assert hermes["verification_status"] == "tested", (
        f"expected hermes to be 'tested', got {hermes['verification_status']!r}"
    )


@pytest.mark.asyncio
async def test_frameworks_known_adapters_present(client):
    resp = await client.get("/api/frameworks")
    assert resp.status_code == 200
    ids = {e["id"] for e in resp.json()}
    for expected in ("smolagents", "generic", "hermes", "openclaw", "langroid", "omp"):
        assert expected in ids, f"expected adapter {expected!r} missing from response"
