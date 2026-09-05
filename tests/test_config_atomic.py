"""``save_config`` durability, and the class-level guard behind it (tsk-ckb3mb).

``config.yaml`` holds the entire install, and ``save_config`` used to write it
the way that produced the 2026-08-21 incident: ``Path.write_text`` into a
sibling temp file, then ``Path.replace``.  No ``fsync`` of the file, so the
bytes can still be in page cache when the rename lands durably; no ``fsync``
of the parent directory, so the rename itself can be lost.  That write runs on
the ``_pin_applied`` path at boot -- exactly the window in which a first-boot
power cut is most likely.

The temp name was deterministic too (``config.yaml.tmp``).  ``save_config`` is
also reachable outside ``save_config_locked``'s ``asyncio.Lock``, which is
per-event-loop and per-process anyway, so two writers could share one temp
inode and interleave their bytes.

A test that only asserts "``save_config`` writes readable YAML" passes on the
broken code.  These assert the durability calls themselves, plus the guard
that stops a tenth hand-rolled copy of the pattern from appearing.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import yaml

from tinyagentos.config import AppConfig, load_config, save_config

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE = _REPO_ROOT / "tinyagentos"
_ATOMIC_IO = _PACKAGE / "atomic_io.py"


@pytest.fixture()
def cfg(tmp_path: Path) -> tuple[AppConfig, Path]:
    path = tmp_path / "config.yaml"
    return load_config(path), path


def _record_promotions(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the source basename of every temp-file promotion.

    Patching ``os.replace`` covers both spellings: ``Path.replace`` delegates
    to it, so the assertion is about the temp *name*, not about which API the
    writer happens to use.
    """
    seen: list[str] = []
    real_os_replace = os.replace

    def os_replace(src, dst, *a, **kw):
        seen.append(os.path.basename(str(src)))
        return real_os_replace(src, dst, *a, **kw)

    monkeypatch.setattr(os, "replace", os_replace)
    return seen


class TestSaveConfigDurability:
    def test_save_config_fsyncs_the_file_and_the_parent_directory(
        self, cfg, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two fsyncs, not one: the temp file, then the directory.

        Syncing only the file still loses the rename; syncing only the
        directory still lets the target come back NUL-filled.
        """
        config, path = cfg
        calls: list[int] = []
        real_fsync = os.fsync

        def counting_fsync(fd: int) -> None:
            calls.append(fd)
            return real_fsync(fd)

        monkeypatch.setattr(os, "fsync", counting_fsync)
        save_config(config, path)

        assert len(calls) == 2, (
            f"expected os.fsync called 2 times, got {len(calls)} -- "
            "save_config must fsync the temp file and its parent directory"
        )

    def test_save_config_uses_a_randomised_temp_name(
        self, cfg, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A deterministic temp name lets two concurrent writers share one inode."""
        config, path = cfg
        seen = _record_promotions(monkeypatch)

        save_config(config, path)
        save_config(config, path)

        assert len(seen) == 2, f"expected two temp promotions, saw {seen}"
        assert seen[0] != seen[1], (
            f"assert {seen[0]!r} != {seen[1]!r}\n"
            "  (two concurrent writers shared one temp inode)"
        )

    def test_save_config_still_round_trips(self, cfg) -> None:
        """The durability work must not change what lands on disk."""
        config, path = cfg
        save_config(config, path)
        assert yaml.safe_load(path.read_text()) == config.to_dict()

    def test_save_config_creates_missing_parent_directories(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "deeper" / "config.yaml"
        config = load_config(path)
        save_config(config, path)
        assert path.exists()

    def test_save_config_leaves_no_temp_file_behind(self, cfg) -> None:
        config, path = cfg
        save_config(config, path)
        assert [p.name for p in path.parent.iterdir()] == ["config.yaml"]


# ---------------------------------------------------------------------------
# Class-level guard
# ---------------------------------------------------------------------------

# ``os.replace(tmp, target)`` / ``os.rename(tmp, target)`` and the bound-method
# spellings ``tmp.replace(target)`` / ``tmp.rename(target)``.  ``rename`` is in
# here deliberately: banning only ``replace`` would leave a one-word edit that
# re-introduces the whole defect.
_PROMOTION = re.compile(
    r"""
    (?:
        os\.(?:replace|rename)\(\s*(?P<arg>[A-Za-z_][\w.]*)\s*,   # os.replace(tmp, ...)
      | (?P<recv>[A-Za-z_][\w.]*)\.(?:replace|rename)\(           # tmp.replace(...)
    )
    """,
    re.VERBOSE,
)

# Only names that unambiguously denote a temp file, so an ordinary
# ``str.replace`` or a variable called ``template`` is never flagged.
_TEMP_NAME = re.compile(r"^(?:tmp|tmp_\w+|\w+_tmp)$")

# A promotion that genuinely cannot go through atomic_io -- a symlink swap, say
# -- carries this marker plus a reason on the same line.  The reason is
# required: an empty marker is not a waiver.
_EXEMPT = re.compile(r"#\s*atomic-io-exempt:\s*\S")


def _hand_rolled_promotions() -> list[str]:
    """Every ``<temp>.replace(...)`` / ``os.replace(<temp>, ...)`` in the package.

    Mirrors the shell check a reviewer would run::

        grep -rnE '(os\\.(replace|rename)\\(\\s*tmp|tmp[\\w.]*\\.(replace|rename)\\()' tinyagentos/
    """
    violations: list[str] = []
    for py in sorted(_PACKAGE.rglob("*.py")):
        if py == _ATOMIC_IO:
            continue
        for lineno, line in enumerate(py.read_text().splitlines(), start=1):
            if _EXEMPT.search(line):
                continue
            for match in _PROMOTION.finditer(line):
                name = match.group("arg") or match.group("recv")
                if _TEMP_NAME.match(name.rsplit(".", 1)[-1]):
                    rel = py.relative_to(_REPO_ROOT)
                    violations.append(f"{rel}:{lineno}: {line.strip()}")
                    break
    return violations


def test_no_module_outside_atomic_io_hand_rolls_temp_plus_replace() -> None:
    """``atomic_io`` is the one writer allowed to promote a temp file.

    Every hand-rolled copy re-derives the same five lines and every copy so far
    has dropped both fsyncs, so this is a class of defect, not nine unrelated
    ones. A promotion that cannot use ``atomic_io`` (swapping a symlink, say)
    is waived in place with ``# atomic-io-exempt: <reason>``.
    """
    violations = _hand_rolled_promotions()
    assert violations == [], (
        "tsk-ckb3mb: write through tinyagentos.atomic_io "
        "(atomic_write_text / atomic_write_bytes) instead of hand-rolling "
        "temp-file-plus-replace -- every hand-rolled copy has omitted the "
        "fsync of the file and of the parent directory. Violations:\n  "
        + "\n  ".join(violations)
    )


def test_the_guard_flags_a_freshly_reintroduced_copy(tmp_path: Path) -> None:
    """The guard must fail on a tenth copy, not merely pass on a clean tree."""
    reintroduced = (
        "tmp_path = path.with_suffix('.json.tmp')\n"
        "tmp_path.write_text(payload)\n"
        "tmp_path.replace(path)\n"
    )
    assert [
        line
        for line in reintroduced.splitlines()
        if _PROMOTION.search(line)
        and _TEMP_NAME.match(
            (
                lambda m: (m.group("arg") or m.group("recv")).rsplit(".", 1)[-1]
            )(_PROMOTION.search(line))
        )
    ] == ["tmp_path.replace(path)"]


def test_the_guard_ignores_ordinary_string_replace() -> None:
    """``template.replace(...)`` and ``s.replace(...)`` are not promotions."""
    benign = [
        "return template.replace('{name}', name)",
        "slug = value.replace(' ', '-')",
        "os.replace(part, dest)  # streamed download, not a temp copy",
    ]
    for line in benign:
        match = _PROMOTION.search(line)
        if match is None:
            continue
        name = (match.group("arg") or match.group("recv")).rsplit(".", 1)[-1]
        assert not _TEMP_NAME.match(name), f"false positive on {line!r}"
