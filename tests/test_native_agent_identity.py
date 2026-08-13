"""First-boot identity for the OS-native taOS agent.

The agent built into the OS authenticated as the OWNER (browser session or the
admin-equivalent ``.auth_local_token``), so its actions were indistinguishable
from the human's and could not be revoked without revoking the human. These
tests pin the identity it gets instead, and the properties that make it worth
having: per-install, owner-linked, not shared, conservative.
"""
import os
import stat

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_grants_store import AgentGrantsStore
from tinyagentos.agent_registry_store import (
    AgentRegistryStore,
    load_or_create_signing_keypair,
    verify_registry_token,
)
from tinyagentos.native_agent_identity import (
    NATIVE_AGENT_HANDLE_PREFIX,
    NATIVE_AGENT_ORIGIN,
    NATIVE_AGENT_SCOPES,
    ensure_native_agent_identity,
    native_agent_handle,
    token_path,
)


async def _stores(tmp_path):
    """Registry + grants + signing key over one tmp data_dir.

    A plain async helper rather than an async fixture, matching the convention
    in the sibling store tests.
    """
    registry = AgentRegistryStore(tmp_path / "agent_registry.db")
    await registry.init()
    grants = AgentGrantsStore(tmp_path / "agent_grants.db")
    await grants.init()
    keypair = load_or_create_signing_keypair(tmp_path)
    return registry, grants, tmp_path, keypair


async def _ensure(stores, user_id="user-1"):
    registry, grants, data_dir, keypair = stores
    return await ensure_native_agent_identity(
        registry=registry,
        grants=grants,
        data_dir=data_dir,
        signing_key_pem=keypair[0],
        user_id=user_id,
    )


@pytest.mark.asyncio
class TestNativeAgentIdentity:
    async def test_mints_an_owned_active_identity_with_a_bus_handle(self, tmp_path):
        stores = await _stores(tmp_path)
        _registry, grants, data_dir, _ = stores
        rec = await _ensure(stores)

        assert rec is not None
        assert rec["status"] == "active"          # no consent round-trip to run
        assert rec["origin"] == NATIVE_AGENT_ORIGIN
        install = (data_dir / ".install_id").read_text().strip()
        assert rec["handle"] == native_agent_handle(install)
        assert rec["handle"].startswith(NATIVE_AGENT_HANDLE_PREFIX)
        assert rec["user_id"] == "user-1"         # owner link
        # canonical_id sits under the reserved `taos-` prefix, which only an
        # in-process caller can claim.
        assert rec["canonical_id"].startswith("taos-agent-")

        scopes = {g["scope"] for g in await grants.list_grants(rec["canonical_id"])}
        assert scopes == set(NATIVE_AGENT_SCOPES)

    async def test_scopes_are_conservative(self, tmp_path):
        """A first-boot mint that quietly granted file or task access would be a
        silent privilege grant. Bus participation only; anything more goes
        through the user-mediated scope-request flow."""
        stores = await _stores(tmp_path)
        _, grants, _, _ = stores
        rec = await _ensure(stores)
        scopes = {g["scope"] for g in await grants.list_grants(rec["canonical_id"])}
        assert scopes == {"a2a_send", "a2a_receive"}
        for forbidden in ("files_write", "files_read", "tools_execute",
                          "memory_write", "project_tasks", "observatory_control"):
            assert forbidden not in scopes

    async def test_is_idempotent_across_restarts(self, tmp_path):
        """Runs on every start, so a second call must not fork a second identity
        or a second token."""
        stores = await _stores(tmp_path)
        registry, _, data_dir, _ = stores
        first = await _ensure(stores)
        token_first = token_path(data_dir).read_text()

        second = await _ensure(stores)

        assert second["canonical_id"] == first["canonical_id"]
        assert len(await registry.list_all()) == 1
        assert token_path(data_dir).read_text() == token_first

    async def test_token_is_written_0600_and_verifies(self, tmp_path):
        stores = await _stores(tmp_path)
        _, _, data_dir, keypair = stores
        rec = await _ensure(stores)

        path = token_path(data_dir)
        assert path.exists()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

        claims = verify_registry_token(path.read_text(), keypair[1])
        assert claims["sub"] == rec["canonical_id"]
        assert claims["user_id"] == "user-1"

    async def test_an_existing_token_file_is_never_rewritten(self, tmp_path):
        """The agent may already be running with that token. Rewriting it under
        a live process leaves it holding a credential nobody recognises."""
        stores = await _stores(tmp_path)
        _, _, data_dir, _ = stores
        token_path(data_dir).write_text("a-token-already-in-use")

        await _ensure(stores)

        assert token_path(data_dir).read_text() == "a-token-already-in-use"

    async def test_a_failed_token_write_does_not_pin_an_empty_credential(self, tmp_path):
        """A zero-byte token file must never count as "already minted".

        O_EXCL creates the file and fdopen truncates it, so a write that fails
        after that point (ENOSPC, quota, disk error) leaves an empty file --
        which is exactly the signal that tells the next boot the token exists.
        Left alone, one transient error pins the agent to an empty credential
        forever while every boot reports success.
        """
        stores = await _stores(tmp_path)
        _, _, data_dir, _ = stores

        # A file left behind by a crash between create and write, i.e. what an
        # earlier build (or a hard kill) leaves on disk.
        token_path(data_dir).write_text("")

        rec = await _ensure(stores)

        assert rec is not None
        assert token_path(data_dir).read_text().strip(), "empty token file was treated as minted"

    async def test_write_failure_removes_the_file_it_created(self, tmp_path, monkeypatch):
        """The failing write must clean up after itself, not leave the marker."""
        stores = await _stores(tmp_path)
        _, _, data_dir, _ = stores

        real_fdopen = os.fdopen

        def _boom(fd, *a, **kw):
            fh = real_fdopen(fd, *a, **kw)
            class _Failing:
                def __enter__(self_inner):
                    return self_inner
                def __exit__(self_inner, *exc):
                    fh.close()
                    return False
                def write(self_inner, _data):
                    raise OSError("ENOSPC")
            return _Failing()

        monkeypatch.setattr("tinyagentos.native_agent_identity.os.fdopen", _boom)

        await _ensure(stores)

        assert not token_path(data_dir).exists(), "empty token file left behind after a failed write"

    async def test_deferred_when_the_install_has_no_owner_yet(self, tmp_path):
        """user_id is immutable on the registry row, so minting before an owner
        exists would strand the identity ownerless for life. Skip and let the
        setup route call again."""
        stores = await _stores(tmp_path)
        registry, _, data_dir, _ = stores
        rec = await _ensure(stores, user_id="")

        assert rec is None
        assert await registry.list_all() == []
        assert not token_path(data_dir).exists()

    async def test_refuses_to_mint_without_an_install_anchor(self, tmp_path, monkeypatch):
        """install_id() swallows its own errors and returns "" -- fine for a
        telemetry ping, not fine here. A blank anchor is indistinguishable from
        the pre-v6 rows that legitimately have none, so the identity could never
        be listed or revoked as part of this install."""
        stores = await _stores(tmp_path)
        registry, _, data_dir, _ = stores
        monkeypatch.setattr(
            "tinyagentos.native_agent_identity.read_install_id", lambda _d: ""
        )

        rec = await _ensure(stores)

        assert rec is None
        assert await registry.list_all() == []
        assert not token_path(data_dir).exists()

    async def test_identity_is_anchored_to_this_install(self, tmp_path):
        """Per-install is the property that makes "revoke that machine"
        answerable."""
        stores = await _stores(tmp_path)
        registry, _, data_dir, _ = stores
        rec = await _ensure(stores)

        install = (data_dir / ".install_id").read_text().strip()
        assert install
        assert rec["install_id"] == install
        assert rec["canonical_id"].startswith(f"taos-agent-{install[:8]}-")

        found = await registry.list_for_install(install)
        assert [r["canonical_id"] for r in found] == [rec["canonical_id"]]

    async def test_a_new_install_mints_its_own_identity(self, tmp_path):
        """An image cloned to a new machine gets a new install id and mints its
        own identity rather than carrying the original's credential."""
        stores = await _stores(tmp_path)
        registry, _grants, data_dir, _keypair = stores
        first = await _ensure(stores)

        (data_dir / ".install_id").write_text("f" * 32)
        token_path(data_dir).unlink()

        second = await _ensure(stores)

        assert second["canonical_id"] != first["canonical_id"]
        assert second["install_id"] == "f" * 32
        assert len(await registry.list_all()) == 2
        # And the HANDLES differ. A bare "@taOS-agent" cannot survive here: the
        # partial unique index on (handle) WHERE status='active' rejects the
        # second insert outright, which is what this test caught.
        assert second["handle"] != first["handle"]
        assert second["handle"] == native_agent_handle("f" * 32)

    async def test_grants_are_reasserted_but_user_additions_are_kept(self, tmp_path):
        """Re-asserting the baseline must only ever ADD: a scope the user
        granted on top must survive a restart."""
        stores = await _stores(tmp_path)
        _, grants, _, _ = stores
        rec = await _ensure(stores)
        await grants.add_grant(rec["canonical_id"], "files_read")

        await _ensure(stores)

        scopes = {g["scope"] for g in await grants.list_grants(rec["canonical_id"])}
        assert scopes == {"a2a_send", "a2a_receive", "files_read"}


@pytest.mark.asyncio
class TestInstallIdColumn:
    async def test_legacy_rows_report_unknown_not_this_install(self, tmp_path):
        """Blank install_id means "unknown", never "this install" -- so a group
        revocation cannot scoop up identities minted before installs were
        tracked."""
        stores = await _stores(tmp_path)
        registry, _, data_dir, _ = stores
        legacy = await registry.register(framework="claude-code", display_name="old")
        assert legacy["install_id"] == ""

        await _ensure(stores)
        install = (data_dir / ".install_id").read_text().strip()

        found = await registry.list_for_install(install)
        assert legacy["canonical_id"] not in [r["canonical_id"] for r in found]

    async def test_list_for_install_refuses_a_blank_id(self, tmp_path):
        """Otherwise the blank-anchored legacy rows would all match at once."""
        stores = await _stores(tmp_path)
        registry, _, _, _ = stores
        await registry.register(framework="claude-code", display_name="old")
        assert await registry.list_for_install("") == []


# ---------------------------------------------------------------------------
# The setup routes: BOTH of them
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def setup_client(app):
    """An app with the registry stores live and NO user yet."""
    for attr in ("agent_registry", "agent_grants", "metrics"):
        store = getattr(app.state, attr)
        if store._db is None:
            await store.init()
    app.state._startup_complete = True

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c._app = app
        yield c

    for attr in ("agent_registry", "agent_grants", "metrics"):
        store = getattr(app.state, attr)
        if store._db is not None:
            await store.close()


async def _native_row(app):
    rows = await app.state.agent_registry.list_all()
    return next((r for r in rows if r["origin"] == NATIVE_AGENT_ORIGIN), None)


@pytest.mark.asyncio
class TestSetupMintsTheIdentity:
    """`/auth/setup` has TWO paths -- JSON and form-encoded -- and they are two
    routes into the same event: an install acquiring its first user. Wiring only
    the one you happened to test leaves a whole class of install (the no-JS HTML
    setup page, or the API-driven one) with no agent identity, while the suite
    stays green. Both are pinned here for that reason.
    """

    async def test_json_setup_path_mints_the_identity(self, setup_client):
        app = setup_client._app
        assert await _native_row(app) is None

        resp = await setup_client.post(
            "/auth/setup",
            json={"username": "admin", "full_name": "Admin", "email": "",
                  "password": "newpassword"},
        )
        assert resp.status_code == 200, resp.text

        rec = await _native_row(app)
        assert rec is not None, "JSON setup path did not mint the native identity"
        owner = app.state.auth.find_user("admin")
        assert rec["user_id"] == owner["id"]
        scopes = {g["scope"] for g in await app.state.agent_grants.list_grants(rec["canonical_id"])}
        assert scopes == set(NATIVE_AGENT_SCOPES)

    async def test_form_setup_path_mints_the_identity(self, setup_client):
        app = setup_client._app
        assert await _native_row(app) is None

        resp = await setup_client.post(
            "/auth/setup",
            data={"username": "admin", "full_name": "Admin", "email": "",
                  "password": "newpassword"},
            follow_redirects=False,
        )
        assert resp.status_code == 303, resp.text

        rec = await _native_row(app)
        assert rec is not None, "form setup path did not mint the native identity"
        owner = app.state.auth.find_user("admin")
        assert rec["user_id"] == owner["id"]

    async def test_setup_still_succeeds_when_the_mint_fails(self, setup_client, monkeypatch):
        """An install whose agent identity failed to mint is degraded, not
        broken. Failing setup over it would strand the user on the setup page
        with an account that already exists."""
        app = setup_client._app

        async def _boom(**_kwargs):
            raise RuntimeError("registry is having a bad day")

        monkeypatch.setattr(
            "tinyagentos.native_agent_identity.ensure_native_agent_identity", _boom
        )

        resp = await setup_client.post(
            "/auth/setup",
            json={"username": "admin", "full_name": "Admin", "email": "",
                  "password": "newpassword"},
        )
        assert resp.status_code == 200, resp.text
        assert app.state.auth.is_configured() is True
        assert await _native_row(app) is None


@pytest.mark.asyncio
class TestStartupRaceLogsTheTruth:
    """The loser of a startup race must not log that it wrote the token.

    A log that says "written" having written nothing is the same failure that
    let a zero-byte token file look like a successful mint on every later boot:
    the record of what happened disagrees with what happened, so the next reader
    debugs the wrong thing. The caller cannot derive this -- a _has_token
    snapshot taken before the write is False whether this process went on to
    write the file or another worker did it in the gap -- so _write_token
    reports it.
    """

    async def test_write_token_reports_it_did_not_write_an_existing_token(self, tmp_path):
        from tinyagentos.native_agent_identity import _write_token

        path = token_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("the-winners-token", encoding="utf-8")

        returned, created = _write_token(tmp_path, "the-losers-token")

        assert returned == path
        assert created is False, "reported writing a token it did not write"
        # The winner's credential is untouched: the agent may already be running
        # with it, so replacing it would strand a live process on a token nobody
        # recognises.
        assert path.read_text(encoding="utf-8") == "the-winners-token"

    async def test_write_token_reports_the_write_it_did_do(self, tmp_path):
        from tinyagentos.native_agent_identity import _write_token

        returned, created = _write_token(tmp_path, "a-fresh-token")

        assert returned == token_path(tmp_path)
        assert created is True
        assert returned.read_text(encoding="utf-8") == "a-fresh-token"

    async def test_race_loser_logs_already_present_not_written(self, tmp_path, caplog):
        """The caller's log follows _write_token's report, not a stale snapshot.

        Simulates losing the race in the exact window that matters: the token is
        absent when ensure_native_agent_identity checks (so it mints and tries to
        write), and another worker's file is there by the time _write_token runs.
        """
        import tinyagentos.native_agent_identity as mod

        stores = await _stores(tmp_path)
        winner = token_path(tmp_path)
        real_write = mod._write_token

        def _lose_the_race(data_dir, token):
            winner.parent.mkdir(parents=True, exist_ok=True)
            winner.write_text("another-workers-token", encoding="utf-8")
            return real_write(data_dir, token)

        mod._write_token = _lose_the_race
        try:
            with caplog.at_level("INFO", logger=mod.logger.name):
                await _ensure(stores)
        finally:
            mod._write_token = real_write

        messages = [r.getMessage() for r in caplog.records]
        wrote = [m for m in messages if "token written to" in m]
        present = [m for m in messages if "token already present at" in m]
        assert not wrote, f"claimed to have written a token it did not write: {wrote}"
        assert present, f"never reported the token it found: {messages}"
        assert winner.read_text(encoding="utf-8") == "another-workers-token"
