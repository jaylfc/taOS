"""Tests for the admin prompt library and HTTP endpoint."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tinyagentos.routes.admin_prompts import _split_front_matter

_PROMPTS_DIR = Path(__file__).parent.parent / "tinyagentos" / "admin_prompts"

# ---------------------------------------------------------------------------
# Unit tests for _split_front_matter
# ---------------------------------------------------------------------------

def test_split_with_valid_front_matter():
    text = "---\nname: test\nversion: 1\n---\nBody text here.\n"
    meta, body = _split_front_matter(text)
    assert meta == {"name": "test", "version": 1}
    assert body == "Body text here.\n"


def test_split_without_front_matter():
    text = "Just a body, no front matter.\n"
    meta, body = _split_front_matter(text)
    assert meta == {}
    assert body == text


def test_split_malformed_front_matter():
    # YAML that parses to a non-dict value falls back to empty dict
    text = "---\n- item1\n- item2\n---\nBody.\n"
    meta, body = _split_front_matter(text)
    assert meta == {}
    assert body == "Body.\n"


def test_split_unclosed_front_matter():
    text = "---\nname: test\nno closing delimiter"
    meta, body = _split_front_matter(text)
    assert meta == {}
    assert body == text


def test_split_empty_front_matter():
    text = "---\n\n---\nBody.\n"
    meta, body = _split_front_matter(text)
    assert isinstance(meta, dict)
    assert body == "Body.\n"


# ---------------------------------------------------------------------------
# Content checks: every .md file must be well-formed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("md_file", sorted(_PROMPTS_DIR.glob("*.md")))
def test_prompt_file_structure(md_file):
    text = md_file.read_text()
    meta, body = _split_front_matter(text)

    # Front matter must parse to a dict
    assert isinstance(meta, dict), f"{md_file.name}: front matter is not a dict"

    # name field must match filename stem
    assert "name" in meta, f"{md_file.name}: missing 'name' in front matter"
    assert meta["name"] == md_file.stem, (
        f"{md_file.name}: name '{meta['name']}' does not match filename '{md_file.stem}'"
    )

    # summary must be non-empty
    assert meta.get("summary"), f"{md_file.name}: missing or empty 'summary'"

    # version must be present and numeric
    assert "version" in meta, f"{md_file.name}: missing 'version'"
    assert isinstance(meta["version"], int), f"{md_file.name}: version must be an int"

    # body must be non-empty
    assert body.strip(), f"{md_file.name}: body is empty"

    # required_variables must be a list if present
    if "required_variables" in meta:
        assert isinstance(meta["required_variables"], list), (
            f"{md_file.name}: required_variables must be a list"
        )


# ---------------------------------------------------------------------------
# HTTP route tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_prompts_returns_at_least_four(client):
    resp = await client.get("/api/admin-prompts")
    assert resp.status_code == 200
    data = resp.json()
    assert "prompts" in data
    assert len(data["prompts"]) >= 4
    for p in data["prompts"]:
        assert "name" in p
        assert "summary" in p
        assert "version" in p


@pytest.mark.asyncio
async def test_get_disk_audit_prompt(client):
    resp = await client.get("/api/admin-prompts/disk-audit")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "disk-audit"
    assert "body" in data
    body = data["body"]
    # Must contain steps 1–7
    for step in range(1, 8):
        assert f"{step}." in body or f"{step}. " in body, f"Step {step} not found in disk-audit body"
    # Must contain Reminders section
    assert "Reminders" in body


@pytest.mark.asyncio
async def test_get_nonexistent_prompt_returns_404(client):
    resp = await client.get("/api/admin-prompts/nonexistent")
    assert resp.status_code == 404
    assert "not found" in resp.json()["error"]


@pytest.mark.asyncio
async def test_path_traversal_blocked(client):
    # FastAPI normalises percent-encoded slashes before routing, so the request
    # either hits the guard (400) or is routed to a non-matching path (404).
    # Both outcomes mean the file is NOT served — the important invariant is
    # that we do not get 200 back.
    resp = await client.get("/api/admin-prompts/..%2Fapp")
    assert resp.status_code in (400, 404), (
        f"Expected 400 or 404 for path traversal attempt, got {resp.status_code}"
    )


@pytest.mark.asyncio
async def test_path_traversal_with_slash_blocked(client):
    resp = await client.get("/api/admin-prompts/foo/bar")
    # FastAPI will route this differently (no match), but if it reaches the handler it must be 400
    # In practice FastAPI returns 404 for unmatched path segments — either is acceptable
    assert resp.status_code in (400, 404)


@pytest.mark.asyncio
async def test_get_memory_audit_prompt(client):
    resp = await client.get("/api/admin-prompts/memory-audit")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "memory-audit"
    assert data["body"].strip()


@pytest.mark.asyncio
async def test_get_health_report_prompt(client):
    resp = await client.get("/api/admin-prompts/health-report")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "health-report"
    assert data["body"].strip()


@pytest.mark.asyncio
async def test_get_weekly_summary_prompt(client):
    resp = await client.get("/api/admin-prompts/weekly-summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "weekly-summary"
    assert data["body"].strip()


@pytest.mark.asyncio
async def test_list_prompt_bodies_not_included(client):
    """List endpoint must not include body fields."""
    resp = await client.get("/api/admin-prompts")
    assert resp.status_code == 200
    for p in resp.json()["prompts"]:
        assert "body" not in p
