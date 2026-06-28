"""Tests for the OpenCrabs adapter (drives `opencrabs run` non-interactively).

The adapter shells out via the module-level ``run_opencrabs`` callable, which
these tests rebind to a fake so no real binary is needed.
"""
import json

import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.adapters import opencrabs_adapter as mod


def _client():
    return AsyncClient(transport=ASGITransport(app=mod.app), base_url="http://test")


# --------------------------------------------------------------- build_argv

def test_build_argv_is_one_shot_json_run(monkeypatch):
    monkeypatch.setattr(mod, "OPENCRABS_BIN", "opencrabs")
    monkeypatch.setattr(mod, "OPENCRABS_PROFILE", "")
    assert mod.build_argv("hello there") == [
        "opencrabs", "run", "hello there", "--format", "json", "--auto-approve",
    ]


def test_build_argv_includes_profile_when_set(monkeypatch):
    monkeypatch.setattr(mod, "OPENCRABS_BIN", "/opt/opencrabs")
    monkeypatch.setattr(mod, "OPENCRABS_PROFILE", "agent-x")
    argv = mod.build_argv("hi")
    assert argv[:4] == ["/opt/opencrabs", "--profile", "agent-x", "run"]


# --------------------------------------------------------------- extract_reply

@pytest.mark.parametrize("payload,expected", [
    ({"response": "the answer"}, "the answer"),
    ({"content": "via content"}, "via content"),
    ({"output": "via output"}, "via output"),
    ({"message": {"content": "nested"}}, "nested"),
])
def test_extract_reply_known_shapes(payload, expected):
    assert mod.extract_reply(json.dumps(payload)) == expected


def test_extract_reply_skips_leading_progress_line():
    out = "\U0001f914 Processing...\n" + json.dumps({"response": "done"})
    assert mod.extract_reply(out) == "done"


def test_extract_reply_falls_back_to_raw_text():
    assert mod.extract_reply("just plain text") == "just plain text"


def test_extract_reply_empty():
    assert mod.extract_reply("") == ""
    assert mod.extract_reply("   \n ") == ""


# --------------------------------------------------------------- /message

@pytest.mark.asyncio
async def test_message_returns_extracted_reply(monkeypatch):
    captured = {}

    async def fake_run(argv):
        captured["argv"] = argv
        return 0, json.dumps({"response": "hi from opencrabs"}), ""

    monkeypatch.setattr(mod, "run_opencrabs", fake_run)
    async with _client() as client:
        resp = await client.post("/message", json={"text": "ping"})
    assert resp.status_code == 200
    assert resp.json()["content"] == "hi from opencrabs"
    assert captured["argv"][1:4] == ["run", "ping", "--format"]


@pytest.mark.asyncio
async def test_message_empty_text_short_circuits(monkeypatch):
    async def fake_run(argv):  # pragma: no cover - must not be called
        raise AssertionError("runner should not be invoked for empty text")

    monkeypatch.setattr(mod, "run_opencrabs", fake_run)
    async with _client() as client:
        resp = await client.post("/message", json={"text": "   "})
    assert resp.json()["content"] == ""


@pytest.mark.asyncio
async def test_message_nonzero_exit_surfaces_detail(monkeypatch):
    async def fake_run(argv):
        return 1, "", "No provider configured. Please complete onboarding."

    monkeypatch.setattr(mod, "run_opencrabs", fake_run)
    async with _client() as client:
        resp = await client.post("/message", json={"text": "ping"})
    content = resp.json()["content"]
    assert "failed" in content.lower()
    assert "No provider configured" in content


@pytest.mark.asyncio
async def test_message_missing_binary_is_graceful(monkeypatch):
    async def fake_run(argv):
        raise FileNotFoundError("opencrabs")

    monkeypatch.setattr(mod, "run_opencrabs", fake_run)
    async with _client() as client:
        resp = await client.post("/message", json={"text": "ping"})
    assert "not found" in resp.json()["content"].lower()


@pytest.mark.asyncio
async def test_health_reports_framework(monkeypatch):
    async with _client() as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["framework"] == "opencrabs"
