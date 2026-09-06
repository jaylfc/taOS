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

# Any name that *contains* a temp word, not just one spelled entirely as one.
# The anchored version let a one-word rename walk straight past the guard --
# ``tmp_path`` renamed to ``draft_tmpfile`` or ``intermediate_temp`` is still
# a temp file, and the whole point of widening this from ``tmp*`` to
# ``tmp*|temp*|temporary`` was to stop that kind of rename from re-opening
# the defect. The cost is a handful of ordinary words that happen to contain
# "tmp"/"temp" as a substring (``template``, ``attempt``, ``contempt``) --
# measured at zero real hits across ``tinyagentos/`` when this was widened,
# so the trade is worth it; any real hit is waived in place with
# ``# atomic-io-exempt: <reason>`` (see ``_EXEMPT`` below).
_TEMP_NAME = re.compile(r"tmp|temp", re.IGNORECASE)

# A promotion that genuinely cannot go through atomic_io -- a symlink swap, say
# -- carries this marker plus a reason on the same line.  The reason is
# required: an empty marker is not a waiver.
_EXEMPT = re.compile(r"#\s*atomic-io-exempt:\s*\S")


def _hand_rolled_promotions(package: Path = _PACKAGE) -> list[str]:
    """Every ``<temp>.replace(...)`` / ``os.replace(<temp>, ...)`` in *package*.

    Mirrors the shell check a reviewer would run::

        grep -rnE '(os\\.(replace|rename)\\(\\s*tmp|tmp[\\w.]*\\.(replace|rename)\\()' tinyagentos/

    *package* defaults to the real ``tinyagentos/`` tree; tests pass a
    ``tmp_path`` fixture to exercise the ``# atomic-io-exempt`` waiver against
    synthetic content without touching the real package.
    """
    violations: list[str] = []
    for py in sorted(package.rglob("*.py")):
        if py == _ATOMIC_IO:
            continue
        for lineno, line in enumerate(py.read_text().splitlines(), start=1):
            if _EXEMPT.search(line):
                continue
            for match in _PROMOTION.finditer(line):
                name = match.group("arg") or match.group("recv")
                if _TEMP_NAME.search(name.rsplit(".", 1)[-1]):
                    try:
                        rel = py.relative_to(_REPO_ROOT)
                    except ValueError:
                        rel = py.relative_to(package)
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
        and _TEMP_NAME.search(
            (
                lambda m: (m.group("arg") or m.group("recv")).rsplit(".", 1)[-1]
            )(_PROMOTION.search(line))
        )
    ] == ["tmp_path.replace(path)"]


def test_the_guard_flags_a_temp_word_hidden_inside_a_longer_name(tmp_path: Path) -> None:
    """A rename that keeps the temp word but stops being a whole part must still be caught.

    ``draft_tmpfile.replace(...)`` and ``intermediate_temp.replace(...)`` are
    exactly the one-word-rename dodge the anchored regex missed: neither
    ``tmpfile`` nor ``intermediate_temp`` is ``tmp``/``temp``/``temporary`` on
    the nose, or ``tmp_*``/``*_tmp`` on the nose, but both plainly still name
    a temp file.
    """
    reintroduced = (
        "draft_tmpfile = path.with_suffix('.json.tmp')\n"
        "draft_tmpfile.replace(path)\n"
        "intermediate_temp = path.with_suffix('.json.tmp')\n"
        "intermediate_temp.replace(path)\n"
    )
    flagged = [
        line
        for line in reintroduced.splitlines()
        if _PROMOTION.search(line)
        and _TEMP_NAME.search(
            (
                lambda m: (m.group("arg") or m.group("recv")).rsplit(".", 1)[-1]
            )(_PROMOTION.search(line))
        )
    ]
    assert flagged == ["draft_tmpfile.replace(path)", "intermediate_temp.replace(path)"]


@pytest.mark.parametrize(
    "line, promoted",
    [
        ("tmp.replace(path)", "tmp"),
        ("tmp_path.replace(path)", "tmp_path"),
        ("temp_path.replace(path)", "temp_path"),
        ("temp_file.replace(path)", "temp_file"),
        ("temporary.replace(path)", "temporary"),
        ("os.replace(temp, path)", "temp"),
        ("os.replace(config_temp, path)", "config_temp"),
        ("os.replace(config_tmp, path)", "config_tmp"),
        # Substring hits: the word is present but is not the whole name and
        # not a whole underscore-separated part either.
        ("temporary_file.replace(path)", "temporary_file"),
        ("partial_temp.replace(path)", "partial_temp"),
        ("intermediate_tmpfile.replace(path)", "intermediate_tmpfile"),
    ],
)
def test_the_guard_recognises_common_temp_variable_names(line: str, promoted: str) -> None:
    """``temp``-spelled names are temp files too, wherever the word sits.

    A guard that only knew ``tmp*`` let ``temp_path.write_text(...)`` followed
    by ``temp_path.replace(...)`` reintroduce the whole defect while staying
    green -- a one-word rename around the check. Anchoring the word to the
    whole name (or a whole underscore-separated part) has the same hole one
    level up: ``tmp_path`` renamed to ``intermediate_tmpfile`` walks straight
    past an anchored regex.
    """
    match = _PROMOTION.search(line)
    assert match is not None, f"promotion not detected in {line!r}"
    name = (match.group("arg") or match.group("recv")).rsplit(".", 1)[-1]
    assert name == promoted
    assert _TEMP_NAME.search(name), f"{name!r} not recognised as a temp file name"


def test_the_guard_ignores_ordinary_string_replace() -> None:
    """``value.replace(...)`` and a commented-past ``os.replace`` are not promotions."""
    benign = [
        "slug = value.replace(' ', '-')",
        "os.replace(part, dest)  # streamed download, not a temp copy",
    ]
    for line in benign:
        match = _PROMOTION.search(line)
        if match is None:
            continue
        name = (match.group("arg") or match.group("recv")).rsplit(".", 1)[-1]
        assert not _TEMP_NAME.search(name), f"false positive on {line!r}"


def test_the_guard_s_known_false_positive_needs_a_waiver(tmp_path: Path) -> None:
    """Widening from anchored to substring buys ~3 known false positives.

    ``template``, ``attempt`` and ``contempt`` all contain "temp" as a
    substring, so ``template.replace(...)`` -- ordinary ``str.replace``, not a
    temp-file promotion -- is flagged by the widened regex. Measured against
    the real ``tinyagentos/`` tree this hits zero call sites (nothing there
    names a string-replace receiver that way), but if one ever does, this is
    how it gets silenced: an ``# atomic-io-exempt: <reason>`` on that exact
    line, honoured only there.
    """
    unwaived = tmp_path / "unwaived.py"
    unwaived.write_text("greeting = template.replace('{name}', name)\n")
    assert _hand_rolled_promotions(tmp_path) == [
        f"unwaived.py:1: greeting = template.replace('{{name}}', name)"
    ]

    waived = tmp_path / "waived.py"
    unwaived.unlink()
    waived.write_text(
        "greeting = template.replace('{name}', name)  # atomic-io-exempt: ordinary str.replace\n"
    )
    assert _hand_rolled_promotions(tmp_path) == []
