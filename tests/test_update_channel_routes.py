import pytest
from tinyagentos.update_runner import UpdateResult


class TestBranchesRoute:
    @pytest.mark.asyncio
    async def test_branches_returns_list_and_current(self, client, monkeypatch):
        import tinyagentos.routes.settings as s

        async def fake_lsremote(project_dir):
            return ["master", "dev"]

        async def fake_current(store, project_dir):
            return "master"

        monkeypatch.setattr(s, "_remote_branches", fake_lsremote, raising=False)
        monkeypatch.setattr(s, "resolve_tracked_branch", fake_current, raising=False)

        r = await client.get("/api/settings/branches")
        assert r.status_code == 200
        body = r.json()
        assert set(body["branches"]) == {"master", "dev"}
        assert body["current"] == "master"


class TestUpdateChannelRoute:
    @pytest.mark.asyncio
    async def test_rejects_unknown_branch(self, client, monkeypatch):
        import tinyagentos.routes.settings as s

        async def fake_lsremote(project_dir):
            return ["master", "dev"]

        monkeypatch.setattr(s, "_remote_branches", fake_lsremote, raising=False)

        r = await client.post("/api/settings/update-channel", json={"branch": "nonexistent"})
        assert r.status_code == 400
        body = r.json()
        assert "unknown" in body["error"]

    @pytest.mark.asyncio
    async def test_rejects_flag_injection_branch(self, client, monkeypatch):
        import tinyagentos.routes.settings as s

        called = []

        async def fake_lsremote(project_dir):
            called.append(True)
            return ["master", "dev"]

        monkeypatch.setattr(s, "_remote_branches", fake_lsremote, raising=False)

        r = await client.post(
            "/api/settings/update-channel",
            json={"branch": "--upload-pack=touch /tmp/x"},
        )
        assert r.status_code == 400
        assert "invalid" in r.json()["error"]
        # Rejected by the format guard before we ever hit the remote.
        assert called == []

    @pytest.mark.asyncio
    async def test_noop_on_same_branch(self, client, monkeypatch):
        import tinyagentos.routes.settings as s

        switch_called = []

        async def fake_lsremote(project_dir):
            return ["master", "dev"]

        async def fake_resolve(store, project_dir):
            return "master"

        async def fake_switch(branch, project_dir):
            switch_called.append(branch)
            return UpdateResult(previous_sha="aaa", new_sha="bbb", recovery_tag="tag1", message="ok")

        monkeypatch.setattr(s, "_remote_branches", fake_lsremote, raising=False)
        monkeypatch.setattr(s, "resolve_tracked_branch", fake_resolve, raising=False)
        monkeypatch.setattr(s, "switch_to_branch", fake_switch, raising=False)

        r = await client.post("/api/settings/update-channel", json={"branch": "master"})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "unchanged"
        assert len(switch_called) == 0

    @pytest.mark.asyncio
    async def test_switches_and_saves_pref(self, client, app, monkeypatch, tmp_path):
        import tinyagentos.routes.settings as s

        # desktop_settings is not initialised by conftest (lifespan-only); do it here.
        ds = app.state.desktop_settings
        if ds._db is None:
            await ds.init()

        switch_args = []
        snapshot_args = []
        fake_snapshot_path = tmp_path / "pre-switch-test"
        fake_snapshot_path.mkdir()

        async def fake_lsremote(project_dir):
            return ["master", "dev"]

        async def fake_resolve(store, project_dir):
            return "master"

        def fake_snapshot(data_dir):
            snapshot_args.append(data_dir)
            return fake_snapshot_path

        async def fake_switch(branch, project_dir):
            switch_args.append(branch)
            return UpdateResult(previous_sha="aaa", new_sha="bbb", recovery_tag="tag1", message="ok")

        async def fake_pip_rebuild_restart(project_dir, target_sha):
            return 0, ""

        monkeypatch.setattr(s, "_remote_branches", fake_lsremote, raising=False)
        monkeypatch.setattr(s, "resolve_tracked_branch", fake_resolve, raising=False)
        monkeypatch.setattr(s, "snapshot_data_dir", fake_snapshot, raising=False)
        monkeypatch.setattr(s, "switch_to_branch", fake_switch, raising=False)
        monkeypatch.setattr(s, "_pip_rebuild_restart", fake_pip_rebuild_restart, raising=False)

        r = await client.post("/api/settings/update-channel", json={"branch": "dev"})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "switching"
        assert body["branch"] == "dev"
        assert switch_args == ["dev"]
        assert len(snapshot_args) == 1

        # Verify the pref was persisted by reading it back from the store directly.
        saved = await ds.get_preference("user", "auto-update")
        assert saved is not None
        assert saved.get("tracked_branch") == "dev"

        await ds.close()


class TestUpdateCheckFollowsTrackedBranch:
    @pytest.mark.asyncio
    async def test_update_check_follows_tracked_branch_pref(self, client, monkeypatch):
        """check_for_updates must resolve the tracked branch and compare against
        origin/<that branch> — not a hard-coded master."""
        import asyncio as _asyncio

        import tinyagentos.routes.settings as s

        resolve_calls = []
        refs_seen = []

        async def fake_resolve(store, project_dir):
            resolve_calls.append(True)
            return "my-feature"

        class _FakeProc:
            def __init__(self, out=b""):
                self._out = out

            async def communicate(self):
                return self._out, b""

        async def fake_exec(*args, **kwargs):
            # args = ("git", <subcommand>, ...). Record any ref argument so we
            # can assert the resolved branch flowed into the git comparison.
            refs_seen.extend(a for a in args if isinstance(a, str))
            return _FakeProc(b"deadbeef\n")

        async def fake_strictly_ahead(project_dir, local_sha, remote_sha):
            return False

        monkeypatch.setattr(s, "resolve_tracked_branch", fake_resolve, raising=False)
        monkeypatch.setattr(_asyncio, "create_subprocess_exec", fake_exec, raising=False)
        monkeypatch.setattr(
            "tinyagentos.auto_update.remote_is_strictly_ahead",
            fake_strictly_ahead,
            raising=False,
        )

        r = await client.get("/api/settings/update-check")
        assert r.status_code == 200
        assert resolve_calls, "check_for_updates did not resolve the tracked branch"
        # The resolved branch must reach the git ref comparison.
        assert "origin/my-feature" in refs_seen
        assert "my-feature" in refs_seen  # git fetch origin <branch>
