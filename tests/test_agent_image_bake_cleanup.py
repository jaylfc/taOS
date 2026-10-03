import asyncio
import logging
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.agent_image import _bake_scripts_into_image, ensure_image_present, _sweep_stale_bake_containers

def _incus_list_semantics(names, filter_arg=None):
    """Simulate incus list --format=csv -c n filtering semantics.

    - No filter arg: return all names
    - Bare filter arg: return names matching re.fullmatch(filter, name) or re.match(filter, name)
    """
    if filter_arg is None:
        return "\n".join(names) + "\n"
    pattern = filter_arg
    matched = [n for n in names if re.fullmatch(pattern, n) or re.match(pattern, n)]
    return "\n".join(matched) + ("\n" if matched else "")

class TestBakeCleanup:
    """Tests for _bake_scripts_into_image cleanup behaviour."""

    @pytest.mark.asyncio
    async def test_concurrent_same_alias_imports_only_one_bakes(self):
        """Two concurrent ensure_image_present calls for the same alias must not
        race: only one import runs and only one bake temp container is deleted.

        The first bake's launch blocks on an event; the second reaches launch
        while the first is still mid-bake and fails with a name clash. Without
        a per-alias lock, both imports run and both finally blocks attempt to
        force-delete the same temp container.
        """
        alias = "taos-hermes-base"
        tmp_name = f"taos-bake-{alias}-tmp"
        launch_event = asyncio.Event()
        imported = False
        launched = []
        delete_calls = []
        import_calls = []
        launch_count = 0
        is_image_present_calls = 0

        async def _fake_launch(*args, **kwargs):
            launched.append(args)
            proc = MagicMock()
            nonlocal imported, is_image_present_calls, launch_count
            # Image is absent initially, present after the first import
            if args[:3] == ("incus", "image", "list"):
                if imported:
                    proc.returncode = 0
                    proc.communicate = AsyncMock(return_value=(b"taos-hermes-base\n", b""))
                else:
                    proc.returncode = 0
                    proc.communicate = AsyncMock(return_value=(b"", b""))
            elif args[0] == "curl":
                is_image_present_calls += 1
                if is_image_present_calls == 1:
                    await launch_event.wait()
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            elif args[:3] == ("incus", "image", "import"):
                imported = True
                import_calls.append(args)
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"imported\n", b""))
            elif args[:2] == ("incus", "list"):
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            elif args[:2] == ("incus", "launch"):
                launch_count += 1
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            elif args[:2] == ("incus", "stop"):
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            elif args[:3] == ("incus", "image", "delete"):
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            elif args[:2] == ("incus", "publish"):
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            elif args[:2] == ("incus", "delete") and "--force" in args:
                delete_calls.append(args)
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            else:
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.wait = AsyncMock(return_value=proc.returncode)
            proc.stdout = MagicMock()
            proc.stdout.close = MagicMock()
            return proc

        with patch("asyncio.create_subprocess_exec", new=_fake_launch), \
             patch("tinyagentos.containers.push_file", new=AsyncMock()), \
             patch("tinyagentos.containers.exec_in_container", new=AsyncMock()):
            tasks = [
                asyncio.create_task(ensure_image_present(alias=alias)),
                asyncio.create_task(ensure_image_present(alias=alias)),
            ]
            await asyncio.sleep(0.1)
            launch_event.set()
            results = await asyncio.gather(*tasks)

            # Only one import should have run
            assert len(import_calls) == 1, (
                f"Expected exactly 1 import, got {len(import_calls)}: {import_calls}"
            )
            # The temp container delete should have been issued exactly once
            delete_args_list = [c for c in delete_calls if c[2] == tmp_name]
            assert len(delete_args_list) == 1, (
                f"Expected exactly 1 delete for {tmp_name}, got {len(delete_args_list)}: {delete_args_list}"
            )
            assert delete_args_list[0] == ("incus", "delete", tmp_name, "--force")
            assert all(r is True for r in results)

    @pytest.mark.asyncio
    async def test_bake_logs_failed_tmp_delete(self, caplog):
        """When the final delete returns rc=1, a WARNING with the tmp name is logged."""
        alias = "taos-hermes-base"
        tmp_name = f"taos-bake-{alias}-tmp"
        launched = []

        async def _fake_launch(*args, **kwargs):
            launched.append(args)
            proc = MagicMock()
            # launch succeeds
            if args[:2] == ("incus", "launch"):
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            # stop succeeds
            elif args[:2] == ("incus", "stop"):
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            # image delete succeeds
            elif args[:2] == ("incus", "image"):
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            # publish succeeds
            elif args[:2] == ("incus", "publish"):
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            # FINAL delete fails with rc=1
            elif args[:2] == ("incus", "delete") and "--force" in args:
                proc.returncode = 1
                proc.communicate = AsyncMock(return_value=(b"delete failed: container busy", b""))
            else:
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.wait = AsyncMock(return_value=proc.returncode)
            proc.stdout = MagicMock()
            proc.stdout.close = MagicMock()
            return proc

        with patch("asyncio.create_subprocess_exec", new=_fake_launch), \
             patch("tinyagentos.containers.push_file", new=AsyncMock()), \
             patch("tinyagentos.containers.exec_in_container", new=AsyncMock()), \
             caplog.at_level(logging.WARNING):
            await _bake_scripts_into_image(alias)

        # Verify the delete was attempted
        delete_calls = [c for c in launched if c[:2] == ("incus", "delete") and "--force" in c]
        assert len(delete_calls) == 1, "final delete should be attempted"
        assert delete_calls[0][2] == tmp_name

        # Verify WARNING is logged with the tmp container name
        warning_logs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any(tmp_name in msg for msg in warning_logs), (
            f"Expected WARNING containing '{tmp_name}', got: {warning_logs}"
        )
        assert any("delete" in msg.lower() for msg in warning_logs), (
            f"Expected WARNING mentioning delete, got: {warning_logs}"
        )

    @pytest.mark.asyncio
    async def test_bake_sweeps_stale_tmp_before_launch(self):
        """When a stale tmp container exists, it is deleted before launch."""
        alias = "taos-hermes-base"
        tmp_name = f"taos-bake-{alias}-tmp"
        launched = []

        async def _fake_launch(*args, **kwargs):
            launched.append(args)
            proc = MagicMock()
            # incus list shows the stale tmp container exists
            if args[:2] == ("incus", "list"):
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(f"{tmp_name}\n".encode(), b""))
            # sweep delete should happen before launch
            elif args[:2] == ("incus", "delete") and "--force" in args:
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            # launch succeeds
            elif args[:2] == ("incus", "launch"):
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            # stop succeeds
            elif args[:2] == ("incus", "stop"):
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            # image delete succeeds
            elif args[:2] == ("incus", "image"):
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            # publish succeeds
            elif args[:2] == ("incus", "publish"):
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            # final delete succeeds
            elif args[:2] == ("incus", "delete") and "--force" in args:
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            else:
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.wait = AsyncMock(return_value=proc.returncode)
            proc.stdout = MagicMock()
            proc.stdout.close = MagicMock()
            return proc

        with patch("asyncio.create_subprocess_exec", new=_fake_launch), \
             patch("tinyagentos.containers.push_file", new=AsyncMock()), \
             patch("tinyagentos.containers.exec_in_container", new=AsyncMock()):
            await _bake_scripts_into_image(alias)

        # Find indices of key operations
        list_idx = next(i for i, c in enumerate(launched) if c[:2] == ("incus", "list"))
        sweep_delete_idx = next(i for i, c in enumerate(launched) if c[:2] == ("incus", "delete") and "--force" in c and c[2] == tmp_name)
        launch_idx = next(i for i, c in enumerate(launched) if c[:2] == ("incus", "launch"))

        # Sweep delete must run before launch
        assert sweep_delete_idx < launch_idx, (
            f"Sweep delete (idx {sweep_delete_idx}) must run before launch (idx {launch_idx})"
        )
        # List should run before sweep delete (to discover the stale container)
        assert list_idx < sweep_delete_idx, (
            f"incus list (idx {list_idx}) must run before sweep delete (idx {sweep_delete_idx})"
        )

    @pytest.mark.asyncio
    async def test_ensure_image_present_calls_sweep_on_startup(self):
        """ensure_image_present calls the sweep helper before bake."""
        alias = "taos-hermes-base"
        tmp_name = f"taos-bake-{alias}-tmp"
        launched = []

        async def _fake_launch(*args, **kwargs):
            launched.append(args)
            proc = MagicMock()
            # is_image_present: image not present
            if args[:3] == ("incus", "image", "list"):
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            # curl download
            elif args[0] == "curl":
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            # incus image import
            elif args[:3] == ("incus", "image", "import"):
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"imported\n", b""))
            # sweep: incus list shows stale tmp
            elif args[:2] == ("incus", "list"):
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(f"{tmp_name}\n".encode(), b""))
            # sweep delete
            elif args[:2] == ("incus", "delete") and "--force" in args:
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            # bake launch
            elif args[:2] == ("incus", "launch"):
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            # bake stop
            elif args[:2] == ("incus", "stop"):
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            # bake image delete
            elif args[:2] == ("incus", "image"):
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            # bake publish
            elif args[:2] == ("incus", "publish"):
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            # bake final delete
            elif args[:2] == ("incus", "delete") and "--force" in args:
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            else:
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.wait = AsyncMock(return_value=proc.returncode)
            proc.stdout = MagicMock()
            proc.stdout.close = MagicMock()
            return proc

        with patch("asyncio.create_subprocess_exec", new=_fake_launch), \
             patch("tinyagentos.containers.push_file", new=AsyncMock()), \
             patch("tinyagentos.containers.exec_in_container", new=AsyncMock()):
            await ensure_image_present(alias=alias, url="http://example.test/img.tar.gz")

        # Find indices: sweep delete (from ensure_image_present) should run before bake launch
        sweep_delete_indices = [i for i, c in enumerate(launched) if c[:2] == ("incus", "delete") and "--force" in c and c[2] == tmp_name]
        bake_launch_idx = next(i for i, c in enumerate(launched) if c[:2] == ("incus", "launch"))

        # At least one sweep delete (from ensure_image_present path) should run before bake launch
        assert any(idx < bake_launch_idx for idx in sweep_delete_indices), (
            f"Sweep delete from ensure_image_present should run before bake launch; "
            f"sweep indices: {sweep_delete_indices}, bake launch: {bake_launch_idx}"
        )

class TestSweepStaleBakeContainers:
    """Tests for _sweep_stale_bake_containers with incus REAL filter semantics."""

    @pytest.mark.asyncio
    async def test_sweep_deletes_real_bake_tmp_name(self, caplog):
        """Sweep deletes taos-bake-taos-hermes-base-tmp and does NOT delete naira/mary."""
        names = ["taos-bake-taos-hermes-base-tmp", "naira", "mary", "taos-bake-openclaw-base-tmp"]
        launched = []

        async def _fake_launch(*args, **kwargs):
            launched.append(args)
            proc = MagicMock()
            if args[:2] == ("incus", "list"):
                # No filter arg -> return all names (incus real semantics)
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(_incus_list_semantics(names).encode(), b""))
            elif args[:2] == ("incus", "delete") and "--force" in args:
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            else:
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.wait = AsyncMock(return_value=proc.returncode)
            proc.stdout = MagicMock()
            proc.stdout.close = MagicMock()
            return proc

        with patch("asyncio.create_subprocess_exec", new=_fake_launch), \
             caplog.at_level(logging.INFO):
            await _sweep_stale_bake_containers()

        # Should have called incus list with NO filter (all names)
        list_calls = [c for c in launched if c[:2] == ("incus", "list")]
        assert len(list_calls) == 1
        # The filter arg should NOT be present (no "taos-bake-*-tmp" arg)
        assert len(list_calls[0]) == 5  # incus, list, --format=csv, -c, n

        # Should have deleted only the bake tmp containers
        delete_calls = [c for c in launched if c[:2] == ("incus", "delete") and "--force" in c]
        deleted_names = [c[2] for c in delete_calls]
        assert "taos-bake-taos-hermes-base-tmp" in deleted_names
        assert "taos-bake-openclaw-base-tmp" in deleted_names
        assert "naira" not in deleted_names
        assert "mary" not in deleted_names

        # Verify INFO logs for deleted containers
        info_logs = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
        assert any("taos-bake-taos-hermes-base-tmp" in msg for msg in info_logs)
        assert any("taos-bake-openclaw-base-tmp" in msg for msg in info_logs)

    @pytest.mark.asyncio
    async def test_startup_sweep_runs_when_image_present(self, caplog):
        """ensure_image_present with is_image_present=True still runs the sweep once (local, no remote)."""
        alias = "taos-hermes-base"
        tmp_name = f"taos-bake-{alias}-tmp"
        names = [tmp_name, "other-container"]
        launched = []

        async def _fake_launch(*args, **kwargs):
            launched.append(args)
            proc = MagicMock()
            # is_image_present: image IS present (early return path)
            if args[:3] == ("incus", "image", "list"):
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"taos-hermes-base\n", b""))
            # sweep: incus list shows stale tmp (no filter -> all names)
            elif args[:2] == ("incus", "list"):
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(_incus_list_semantics(names).encode(), b""))
            # sweep delete
            elif args[:2] == ("incus", "delete") and "--force" in args:
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            else:
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.wait = AsyncMock(return_value=proc.returncode)
            proc.stdout = MagicMock()
            proc.stdout.close = MagicMock()
            return proc

        with patch("asyncio.create_subprocess_exec", new=_fake_launch), \
             caplog.at_level(logging.INFO):
            result = await ensure_image_present(alias=alias)

        assert result is True  # image was present, returned early but sweep still ran

        # Sweep should have run: list called without filter
        list_calls = [c for c in launched if c[:2] == ("incus", "list")]
        assert len(list_calls) == 1
        assert len(list_calls[0]) == 5  # no filter arg

        # Sweep delete should have been called for the tmp container
        delete_calls = [c for c in launched if c[:2] == ("incus", "delete") and "--force" in c]
        deleted_names = [c[2] for c in delete_calls]
        assert tmp_name in deleted_names

        # Should NOT have run curl or image import (early return)
        curl_calls = [c for c in launched if c[0] == "curl"]
        assert len(curl_calls) == 0
        import_calls = [c for c in launched if c[:3] == ("incus", "image", "import")]
        assert len(import_calls) == 0

    @pytest.mark.asyncio
    async def test_sweep_skips_inflight_bake_container(self, caplog):
        """Sweep skips a container that is currently in-flight (mid-bake)."""
        alias = "taos-hermes-base"
        in_flight_name = f"taos-bake-{alias}-tmp"
        other_name = "taos-bake-other-tmp"
        names = [in_flight_name, other_name]
        launched = []

        async def _fake_launch(*args, **kwargs):
            launched.append(args)
            proc = MagicMock()
            if args[:3] == ("incus", "image", "list"):
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"fingerprint-abc\n", b""))
            elif args[:2] == ("incus", "list"):
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(_incus_list_semantics(names).encode(), b""))
            elif args[:2] == ("incus", "delete") and "--force" in args:
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            else:
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.wait = AsyncMock(return_value=proc.returncode)
            proc.stdout = MagicMock()
            proc.stdout.close = MagicMock()
            return proc

        from tinyagentos import agent_image as ai
        ai._INFLIGHT_BAKES.add(in_flight_name)
        try:
            with patch("asyncio.create_subprocess_exec", new=_fake_launch), \
                 caplog.at_level(logging.INFO):
                result = await ensure_image_present(alias=alias)
        finally:
            ai._INFLIGHT_BAKES.discard(in_flight_name)

        assert result is True

        delete_calls = [c for c in launched if c[:2] == ("incus", "delete") and "--force" in c]
        deleted_names = [c[2] for c in delete_calls]
        assert in_flight_name not in deleted_names, (
            f"In-flight bake {in_flight_name} should not be deleted"
        )
        assert other_name in deleted_names, (
            f"Non-in-flight bake {other_name} should still be deleted"
        )

    @pytest.mark.asyncio
    async def test_sweep_is_non_fatal_without_incus(self, caplog):
        """Sweep with create_subprocess_exec raising FileNotFoundError returns None and logs a warning."""
        launched = []

        async def _fake_launch(*args, **kwargs):
            launched.append(args)
            raise FileNotFoundError("incus not found")

        with patch("asyncio.create_subprocess_exec", new=_fake_launch), \
             caplog.at_level(logging.WARNING):
            result = await _sweep_stale_bake_containers()

        assert result is None

        # Should log a warning about the failure
        warning_logs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("sweep" in msg.lower() for msg in warning_logs)
        assert any("incus" in msg.lower() or "file" in msg.lower() or "not found" in msg.lower() for msg in warning_logs)
