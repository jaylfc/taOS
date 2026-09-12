"""Tests for chat attachment auto-registration and ref-resolve.

Covers:
- attachment appears in project files after from-path with slug
- same file sent twice yields one project-files entry (idempotent)
- resolve returns registered / not-registered / unknown
- non-owner actor gets existence-hiding 404 from both new paths
- the five original AttachmentRecord fields still round-trip
"""
from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from taos_test_csrf import csrf_event_hooks


def _make_client(app, user_id: str) -> AsyncClient:
    token = app.state.auth.create_session(user_id=user_id, long_lived=True)
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    )


class TestAutoRegister:
    @pytest.mark.asyncio
    async def test_attachment_appears_in_project_files(self, client):
        app = client._transport.app
        data_dir = app.state.data_dir

        resp = await client.post("/api/projects", json={
            "name": "AutoRegProj", "slug": "autoregproj", "description": "test",
        })
        assert resp.status_code == 200, resp.text

        ws_dir = data_dir / "agent-workspaces" / "user"
        ws_dir.mkdir(parents=True, exist_ok=True)
        src = ws_dir / "notes.txt"
        src.write_text("# hello world")

        r = await client.post("/api/chat/attachments/from-path", json={
            "path": "/workspaces/user/notes.txt",
            "source": "workspace",
            "slug": "autoregproj",
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["in_files_state"] == "registered"
        assert body["project_slug"] == "autoregproj"

        r = await client.get("/api/projects/autoregproj/files")
        assert r.status_code == 200, r.text
        names = [e["name"] for e in r.json()]
        assert "notes.txt" in names

    @pytest.mark.asyncio
    async def test_same_file_twice_yields_one_entry(self, client):
        app = client._transport.app
        data_dir = app.state.data_dir

        await client.post("/api/projects", json={
            "name": "IdemProj", "slug": "idemproj", "description": "test",
        })

        ws_dir = data_dir / "agent-workspaces" / "user"
        ws_dir.mkdir(parents=True, exist_ok=True)
        src = ws_dir / "doc.txt"
        src.write_text("idempotent content")

        for _ in range(2):
            r = await client.post("/api/chat/attachments/from-path", json={
                "path": "/workspaces/user/doc.txt",
                "source": "workspace",
                "slug": "idemproj",
            })
            assert r.status_code == 200, r.text

        r = await client.get("/api/projects/idemproj/files")
        assert r.status_code == 200, r.text
        entries = r.json()
        assert len(entries) == 1
        assert entries[0]["name"] == "doc.txt"


class TestRefResolve:
    @pytest.mark.asyncio
    async def test_resolve_returns_registered_for_registered_ref(self, client):
        app = client._transport.app
        data_dir = app.state.data_dir

        await client.post("/api/projects", json={
            "name": "ResolveProj", "slug": "resolveproj", "description": "test",
        })

        ws_dir = data_dir / "agent-workspaces" / "user"
        ws_dir.mkdir(parents=True, exist_ok=True)
        src = ws_dir / "resolve_me.txt"
        src.write_text("resolve content")

        r = await client.post("/api/chat/attachments/from-path", json={
            "path": "/workspaces/user/resolve_me.txt",
            "source": "workspace",
            "slug": "resolveproj",
        })
        assert r.status_code == 200, r.text
        stored_name = r.json()["url"].split("/")[-1]

        r = await client.get(
            f"/api/chat/attachments/resolve?filename={stored_name}&slug=resolveproj"
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["in_files_state"] == "registered"
        assert body["filename"] == "resolve_me.txt"
        assert body["size"] == len("resolve content")

    @pytest.mark.asyncio
    async def test_resolve_returns_not_registered_for_unregistered_ref(self, client):
        app = client._transport.app

        await client.post("/api/projects", json={
            "name": "UnregProj", "slug": "unregproj", "description": "test",
        })

        chat_files = app.state.data_dir / "chat-files"
        chat_files.mkdir(parents=True, exist_ok=True)
        stored_name = "unreg-file.txt"
        (chat_files / stored_name).write_text("unregistered content")

        r = await client.get(
            f"/api/chat/attachments/resolve?filename={stored_name}&slug=unregproj"
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["in_files_state"] == "not-registered"

    @pytest.mark.asyncio
    async def test_resolve_returns_unknown_for_unresolvable_ref(self, client):
        await client.post("/api/projects", json={
            "name": "UnknownProj", "slug": "unknownproj", "description": "test",
        })

        r = await client.get(
            "/api/chat/attachments/resolve?filename=nonexistent.txt&slug=unknownproj"
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["in_files_state"] == "unknown"


class TestSecurity404:
    @pytest.mark.asyncio
    async def test_non_owner_gets_404_from_auto_register(self, client):
        app = client._transport.app

        await client.post("/api/projects", json={
            "name": "OwnerProj", "slug": "ownerproj", "description": "test",
        })

        bob_invite = app.state.auth.add_user_invite("bob", "admin")
        app.state.auth.complete_invite("bob", bob_invite, "Bob", "", "bobpass12")
        bob_rec = app.state.auth.find_user("bob")
        assert bob_rec is not None

        data_dir = app.state.data_dir
        ws_dir = data_dir / "agent-workspaces" / "user"
        ws_dir.mkdir(parents=True, exist_ok=True)
        (ws_dir / "secret.txt").write_text("secret")

        async with _make_client(app, bob_rec["id"]) as bob_client:
            r = await bob_client.post("/api/chat/attachments/from-path", json={
                "path": "/workspaces/user/secret.txt",
                "source": "workspace",
                "slug": "ownerproj",
            })
        assert r.status_code == 404, r.text
        assert r.json()["error"] == "not found"

    @pytest.mark.asyncio
    async def test_non_owner_gets_404_from_resolve(self, client):
        app = client._transport.app

        await client.post("/api/projects", json={
            "name": "ResolveOwner", "slug": "resolveowner", "description": "test",
        })

        alice_invite = app.state.auth.add_user_invite("alice", "admin")
        app.state.auth.complete_invite("alice", alice_invite, "Alice", "", "alicepass12")
        alice_rec = app.state.auth.find_user("alice")
        assert alice_rec is not None

        chat_files = app.state.data_dir / "chat-files"
        chat_files.mkdir(parents=True, exist_ok=True)
        (chat_files / "existing.txt").write_text("data")

        async with _make_client(app, alice_rec["id"]) as alice_client:
            r = await alice_client.get(
                "/api/chat/attachments/resolve?filename=existing.txt&slug=resolveowner"
            )
        assert r.status_code == 404, r.text
        assert r.json()["error"] == "not found"


class TestAttachmentRecordRoundTrip:
    @pytest.mark.asyncio
    async def test_existing_fields_round_trip(self, client):
        app = client._transport.app
        data_dir = app.state.data_dir

        await client.post("/api/projects", json={
            "name": "RoundTrip", "slug": "roundtrip", "description": "test",
        })

        ws_dir = data_dir / "agent-workspaces" / "user"
        ws_dir.mkdir(parents=True, exist_ok=True)
        src = ws_dir / "roundtrip.txt"
        src.write_text("roundtrip")

        r = await client.post("/api/chat/attachments/from-path", json={
            "path": "/workspaces/user/roundtrip.txt",
            "source": "workspace",
            "slug": "roundtrip",
        })
        assert r.status_code == 200, r.text
        body = r.json()

        assert body["filename"] == "roundtrip.txt"
        assert body["mime_type"] == "text/plain"
        assert body["size"] == len("roundtrip")
        assert body["url"].startswith("/api/chat/files/")
        assert body["source"] == "workspace"
