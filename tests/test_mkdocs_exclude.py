"""RED test for R2-33: replace mkdocs-exclude plugin with native exclude_docs.

The mkdocs-exclude plugin (1.0.2, last release 2019) is referenced in
site/docs/mkdocs.yml but is not installed in the test environment.
Before the fix, building the docs site fails because mkdocs cannot find
the ``exclude`` plugin. After the fix, the config uses the native
``exclude_docs`` key (available since MkDocs 1.5) and the build succeeds
without any third-party exclude plugin.

The test reads the project's mkdocs.yml, massages it into a form that
mkdocs can load in the test environment (rewriting the docs_dir, stripping
python/name tags, and removing the pymdownx.emoji extension that needs
callables), and then runs ``mkdocs build``. It asserts:

* the build exits successfully,
* pages matching the exclude patterns are absent from the built site.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SITE_DOCS_DIR = REPO_ROOT / "site" / "docs"
MKdocs_YML = SITE_DOCS_DIR / "mkdocs.yml"


def _massage_config_for_test(raw: str, docs_dir: str) -> str:
    """Rewrite mkdocs.yml so it can build in an isolated temp dir.

    * Replace ``docs_dir`` with the given local path.
    * Replace ``!!python/name:...`` tags with plain strings.
    * Drop the ``pymdownx.emoji`` extension (needs callables unavailable here).
    """
    raw = re.sub(r"!!python/name:([^\n]+)", r"\1", raw)
    raw = re.sub(r"^docs_dir:.*$", f"docs_dir: {docs_dir}", raw, flags=re.MULTILINE)

    lines = raw.splitlines()
    cleaned = []
    skip_emoji_block = False
    for line in lines:
        if "pymdownx.emoji" in line:
            skip_emoji_block = True
            continue
        if skip_emoji_block:
            if line.startswith("  ") or line.startswith("\t"):
                continue
            skip_emoji_block = False
        cleaned.append(line)
    return "\n".join(cleaned)


def _write_minimal_docs(root: Path) -> None:
    (root / "getting-started.md").write_text("# Getting started\n")
    (root / "design").mkdir()
    (root / "design" / "abstraction-layers.md").write_text("# Design\n")
    (root / "strategy").mkdir()
    (root / "strategy" / "secret.md").write_text("# Strategy (should be excluded)\n")
    (root / "business").mkdir()
    (root / "business" / "secret.md").write_text("# Business (should be excluded)\n")
    (root / "internal").mkdir()
    (root / "internal" / "secret.md").write_text("# Internal (should be excluded)\n")
    (root / "private").mkdir()
    (root / "private" / "secret.md").write_text("# Private (should be excluded)\n")
    (root / "draft.private.md").write_text("# Private draft (should be excluded)\n")
    (root / "deploy").mkdir()
    (root / "deploy" / "platform-lxc-internal.md").write_text(
        "# Internal deploy (should be excluded)\n"
    )


class TestMkdocsExcludeReplacement:
    def test_build_succeeds_without_mkdocs_exclude_plugin(self):
        raw = MKdocs_YML.read_text()
        config = _massage_config_for_test(raw, docs_dir="docs")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            docs_root = tmp_path / "docs"
            docs_root.mkdir()
            _write_minimal_docs(docs_root)

            config_path = tmp_path / "mkdocs.yml"
            config_path.write_text(config)

            site_dir = tmp_path / "site"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "mkdocs",
                    "build",
                    "--config-file",
                    str(config_path),
                    "--site-dir",
                    str(site_dir),
                ],
                cwd=tmp_path,
                capture_output=True,
                text=True,
            )

            assert result.returncode == 0, (
                f"mkdocs build failed:\n{result.stdout}\n{result.stderr}"
            )

            # Assert on the path relative to the site root, not the file name:
            # mkdocs builds with use_directory_urls, so every page lands as
            # index.html and the *name* never carries the excluded segment.
            built = sorted(
                p.relative_to(site_dir).as_posix() for p in site_dir.rglob("*.html")
            )

            for forbidden in [
                "secret",
                "platform-lxc-internal",
                "draft.private",
            ]:
                assert not any(forbidden in path for path in built), (
                    f"Excluded page '{forbidden}' found in built site: {built}"
                )
