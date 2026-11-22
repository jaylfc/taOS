"""Opt-in gate and fixtures for the device-link e2e test bed (T0).

Everything under tests/e2e/device_link/ needs a running bed (bed.sh up: Headscale,
a local taos.my and a dozen containers), so it is OFF by default:

  * every test here gets the `device_link_e2e` marker, and
  * every test here is skipped unless TAOS_E2E_DEVICE_LINK=1.

So `pytest tests/` collects these tests and skips them; nothing starts, nothing
connects anywhere. With the variable set, a missing bed is an error, not a skip:
an opt-in run that silently measured nothing would look exactly like a pass.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ENV_VAR = "TAOS_E2E_DEVICE_LINK"
SKIP_REASON = (
    f"device-link e2e test bed is opt-in: set {ENV_VAR}=1 and bring the bed up "
    "with tests/e2e/device_link/bed.sh up (see tests/e2e/device_link/README.md)"
)


def enabled() -> bool:
    return os.environ.get(ENV_VAR) == "1"


def pytest_collection_modifyitems(config, items):
    for item in items:
        path = Path(str(item.path)).resolve()
        if HERE != path.parent and HERE not in path.parents:
            continue
        item.add_marker(pytest.mark.device_link_e2e)
        if not enabled():
            item.add_marker(pytest.mark.skip(reason=SKIP_REASON))


@pytest.fixture(scope="session")
def bed():
    """The running bed. Only reached when the opt-in variable is set."""
    from _device_link_bed import STATE, Bed

    if not (STATE / "ready").exists():
        pytest.fail(
            f"{ENV_VAR}=1 but the bed is not up (no {STATE}/ready). Run tests/e2e/device_link/bed.sh up first.",
            pytrace=False,
        )
    return Bed()
