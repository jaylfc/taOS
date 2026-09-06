"""Endpoint tests for tinyagentos/routes/guides.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import tinyagentos.routes.guides as guides_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SAMPLE_GUIDES = {
    "hardware_tiers": {
        "pi-16gb": {
            "label": "Raspberry Pi (16 GB)",
            "description": "ARM-based SBC with 16 GB RAM.",
            "icon": "cpu",
        },
        "nvidia-12gb": {
            "label": "NVIDIA GPU (12 GB)",
            "description": "Desktop GPU with 12 GB VRAM.",
            "icon": "monitor",
        },
        "cpu-only": {
            "label": "CPU Only",
            "description": "No dedicated GPU.",
            "icon": "server",
        },
    },
    "use_cases": {
        "chat": {
            "label": "Chat",
            "description": "Conversational AI.",
            "icon": "message-circle",
        },
        "coding": {
            "label": "Coding",
            "description": "Code generation.",
            "icon": "code",
        },
    },
    "recommendations": {
        "pi-16gb": {
            "chat": [
                {
                    "model": "Qwen3 1.7B (Q4_K_M)",
                    "reason": "Best quality for 16 GB RAM.",
                    "note": "~1.2 GB on disk.",
                },
            ],
            "coding": [
                {
                    "model": "Qwen3 1.7B (Q4_K_M)",
                    "reason": "Best coding model that fits in 16 GB.",
                },
            ],
        },
        "nvidia-12gb": {
            "chat": [
                {
                    "model": "Qwen3 8B (Q4_K_M)",
                    "reason": "Sweet spot for 12 GB VRAM.",
                },
            ],
        },
        "cpu-only": {
            "chat": [
                {
                    "model": "Qwen3 4B (Q4_K_M)",
                    "reason": "Best balance for CPU inference.",
                },
            ],
        },
    },
}


@pytest.fixture(autouse=True)
def _inject_guides(tmp_data_dir, monkeypatch):
    """Write a sample guides.yaml into the test data dir and reset cache."""
    import yaml as _yaml

    guides_path = tmp_data_dir / "guides.yaml"
    guides_path.write_text(_yaml.dump(_SAMPLE_GUIDES))
    monkeypatch.setattr(guides_mod, "_guides", None)
    monkeypatch.setattr(guides_mod, "_cached_data_dir", None)


# ---------------------------------------------------------------------------
# GET /api/guides/recommendations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recommendations_happy_path(client):
    resp = await client.get(
        "/api/guides/recommendations",
        params={"hardware": "pi-16gb", "use_case": "chat"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["hardware"] == "pi-16gb"
    assert data["use_case"] == "chat"
    assert isinstance(data["recommendations"], list)
    assert len(data["recommendations"]) > 0
    rec = data["recommendations"][0]
    assert "model" in rec
    assert "reason" in rec


@pytest.mark.asyncio
async def test_recommendations_nvidia_coding(client):
    resp = await client.get(
        "/api/guides/recommendations",
        params={"hardware": "pi-16gb", "use_case": "coding"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["use_case"] == "coding"
    assert data["recommendations"][0]["model"] == "Qwen3 1.7B (Q4_K_M)"


@pytest.mark.asyncio
async def test_recommendations_unknown_hardware(client):
    resp = await client.get(
        "/api/guides/recommendations",
        params={"hardware": "nonexistent", "use_case": "chat"},
    )
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert "nonexistent" in detail


@pytest.mark.asyncio
async def test_recommendations_unknown_use_case(client):
    resp = await client.get(
        "/api/guides/recommendations",
        params={"hardware": "pi-16gb", "use_case": "nonexistent"},
    )
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert "nonexistent" in detail


@pytest.mark.asyncio
async def test_recommendations_missing_hardware_param(client):
    resp = await client.get(
        "/api/guides/recommendations",
        params={"use_case": "chat"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_recommendations_missing_use_case_param(client):
    resp = await client.get(
        "/api/guides/recommendations",
        params={"hardware": "pi-16gb"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_recommendations_response_shape(client):
    resp = await client.get(
        "/api/guides/recommendations",
        params={"hardware": "pi-16gb", "use_case": "chat"},
    )
    assert resp.status_code == 200
    data = resp.json()
    for key in ("hardware", "use_case", "recommendations"):
        assert key in data
    rec = data["recommendations"][0]
    for key in ("model", "reason"):
        assert key in rec


# ---------------------------------------------------------------------------
# GET /api/guides/tiers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tiers_returns_200(client):
    resp = await client.get("/api/guides/tiers")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_tiers_shape(client):
    data = (await client.get("/api/guides/tiers")).json()
    assert "tiers" in data
    assert isinstance(data["tiers"], dict)


@pytest.mark.asyncio
async def test_list_tiers_contains_known_tiers(client):
    data = (await client.get("/api/guides/tiers")).json()
    tiers = data["tiers"]
    for tier_key in ("pi-16gb", "nvidia-12gb", "cpu-only"):
        assert tier_key in tiers, f"missing tier: {tier_key}"
        assert "label" in tiers[tier_key]
        assert "description" in tiers[tier_key]


@pytest.mark.asyncio
async def test_list_tiers_empty_when_no_guides(client, monkeypatch):
    monkeypatch.setattr(guides_mod, "_guides", None)
    monkeypatch.setattr(guides_mod, "_cached_data_dir", None)
    with patch.object(guides_mod, "_load_guides", return_value={}):
        resp = await client.get("/api/guides/tiers")
    assert resp.status_code == 200
    data = resp.json()
    assert data["tiers"] == {}


# ---------------------------------------------------------------------------
# GET /api/guides/use-cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_use_cases_returns_200(client):
    resp = await client.get("/api/guides/use-cases")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_use_cases_shape(client):
    data = (await client.get("/api/guides/use-cases")).json()
    assert "use_cases" in data
    assert isinstance(data["use_cases"], dict)


@pytest.mark.asyncio
async def test_list_use_cases_contains_known_cases(client):
    data = (await client.get("/api/guides/use-cases")).json()
    cases = data["use_cases"]
    for case_key in ("chat", "coding"):
        assert case_key in cases, f"missing use case: {case_key}"
        assert "label" in cases[case_key]
        assert "description" in cases[case_key]


@pytest.mark.asyncio
async def test_list_use_cases_empty_when_no_guides(client, monkeypatch):
    monkeypatch.setattr(guides_mod, "_guides", None)
    monkeypatch.setattr(guides_mod, "_cached_data_dir", None)
    with patch.object(guides_mod, "_load_guides", return_value={}):
        resp = await client.get("/api/guides/use-cases")
    assert resp.status_code == 200
    data = resp.json()
    assert data["use_cases"] == {}
