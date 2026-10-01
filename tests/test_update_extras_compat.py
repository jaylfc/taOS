"""Every extra a SHIPPED updater has requested must stay defined.

An update is run by the code already on the box, not by the code being
installed. Before #3313 the updater called ``uv sync --frozen --extra proxy``
and uv refuses an extra that pyproject does not define, so removing ``proxy``
left every existing install unable to update. An extra may be emptied but
never deleted while an updater that names it is still in the field.
"""

import os
import re
import subprocess
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Extras passed by updater releases that are deployed somewhere. Append only.
SHIPPED_UPDATER_EXTRAS = ("proxy", "ble")


def test_shipped_updater_extras_defined_in_pyproject():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    defined = set(data["project"]["optional-dependencies"])
    missing = [e for e in SHIPPED_UPDATER_EXTRAS if e not in defined]
    assert not missing, f"extras requested by shipped updaters are undefined: {missing}"


def test_shipped_updater_extras_in_lockfile():
    # `uv sync --frozen` reads the lock, so the lock must provide them too.
    lock = (ROOT / "uv.lock").read_text()
    m = re.search(r"^provides-extras = \[(.*?)\]", lock, re.M)
    assert m, "uv.lock has no provides-extras line"
    provided = set(re.findall(r'"([^"]+)"', m.group(1)))
    missing = [e for e in SHIPPED_UPDATER_EXTRAS if e not in provided]
    assert not missing, f"uv.lock does not provide extras shipped updaters request: {missing}"


def test_proxy_extra_stays_empty():
    # Kept only for old updaters; it must not bring LiteLLM back.
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert data["project"]["optional-dependencies"]["proxy"] == []



# --- Rule (b): SELF-MAINTAINING LIST ---


def _pyproject_extras() -> set[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return set(data["project"]["optional-dependencies"])


def _uvlock_provides_extras() -> set[str]:
    lock = (ROOT / "uv.lock").read_text()
    m = re.search(r"^provides-extras = \[(.*?)\]", lock, re.M)
    assert m, "uv.lock has no provides-extras line"
    return set(re.findall(r'"([^"]+)"', m.group(1)))


@pytest.mark.parametrize(
    ("device_class", "taos_extras_ble", "expected"),
    [
        ("mobile", None, ("ble",)),
        ("mobile", "1", ("ble",)),
        ("mobile", "0", ()),
        ("mobile", "true", ("ble",)),
        ("mobile", "false", ()),
        (None, None, ()),
        (None, "1", ("ble",)),
        (None, "0", ()),
        (None, "true", ("ble",)),
        (None, "false", ()),
    ],
)
def test_compute_update_extras_all_branches(device_class, taos_extras_ble, expected):
    from tinyagentos.routes.settings import _compute_update_extras

    env = {}
    if taos_extras_ble is not None:
        env["TAOS_EXTRAS_BLE"] = taos_extras_ble
    with patch("tinyagentos.routes.settings._detect_device_class", return_value=device_class):
        with patch.dict(os.environ, env, clear=True):
            result = _compute_update_extras()
    assert result == expected
    pyproject_extras = _pyproject_extras()
    lock_extras = _uvlock_provides_extras()
    for extra in result:
        assert extra in SHIPPED_UPDATER_EXTRAS, (
            f"extra {extra!r} returned by _compute_update_extras is not in SHIPPED_UPDATER_EXTRAS"
        )
        assert extra in pyproject_extras, (
            f"extra {extra!r} returned by _compute_update_extras is not defined in pyproject.toml"
        )
        assert extra in lock_extras, (
            f"extra {extra!r} returned by _compute_update_extras is not in uv.lock provides-extras"
        )
