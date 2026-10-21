"""RED-FIRST test: git fetch error output must be surfaced in fetch_error,
not lost to DEVNULL and reported as 'unknown error'.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.routes import settings as settings_mod


class TestUpdateCheckFetchError:
    @pytest.mark.asyncio
    async def test_fetch_error_is_surfaced_not_unknown(self):
        """When git fetch fails, its error output must appear in fetch_error.

        The fetch subprocess must request stdout=PIPE so its error text can be
        captured into fetch_out and surfaced to the caller.  With DEVNULL the
        output is discarded and the response reads 'unknown error'.
        """
        branch = "dev"

        async def _fake_create_subprocess_exec(*args, **kwargs):
            stdout = kwargs.get("stdout")
            fake_proc = MagicMock()
            fake_proc.returncode = 1
            if stdout == asyncio.subprocess.PIPE:
                fake_proc.communicate = AsyncMock(
                    return_value=(b"fatal: unable to access\n", b"")
                )
            else:
                fake_proc.communicate = AsyncMock(
                    return_value=(None, None)
                )
            return fake_proc

        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    desktop_settings=SimpleNamespace(),
                ),
            ),
        )

        with (
            patch.object(
                settings_mod, "resolve_tracked_branch",
                new=AsyncMock(return_value=branch),
            ),
            patch("tinyagentos.update_preflight.check_preflight", return_value=[]),
            patch(
                "tinyagentos.routes.settings.asyncio.create_subprocess_exec",
                side_effect=_fake_create_subprocess_exec,
            ),
        ):
            result = await settings_mod.check_for_updates(request)

        assert result["has_updates"] is False
        assert result["fetch_error"] is not None
        assert "unable to access" in result["fetch_error"]
