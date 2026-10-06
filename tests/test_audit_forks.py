from __future__ import annotations

import importlib.util
import json
import re
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "audit-forks.py"

spec = importlib.util.spec_from_file_location("audit_forks", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _expected_from_file(path, pattern):
    with open(path) as f:
        content = f.read()
    m = re.search(pattern, content)
    return m.group(1) if m else None


def _line_matching(path, pattern):
    with open(path) as f:
        for line in f:
            if re.search(pattern, line):
                return line.strip()
    return None


class TestParsePinValue:
    def test_rkllama(self):
        line = _line_matching(
            REPO_ROOT / "scripts" / "install-rknpu.sh",
            r'RKLLAMA_REF="\$\{',
        )
        expected = _expected_from_file(
            REPO_ROOT / "scripts" / "install-rknpu.sh",
            r'RKLLAMA_REF="\$\{[^}]*:-([^}]+)}"',
        )
        result = mod._parse_pin_value(line, pin_var="RKLLAMA_REF")
        assert result == expected
        assert result
        assert not result.startswith("-")

    def test_qmd(self):
        line = _line_matching(
            REPO_ROOT / "scripts" / "install-server.sh",
            r'qmd_npm_version="\$\{',
        )
        expected = _expected_from_file(
            REPO_ROOT / "scripts" / "install-server.sh",
            r'qmd_npm_version="\$\{[^}]*:-([^}]+)}"',
        )
        result = mod._parse_pin_value(line, pin_var="qmd_npm_version")
        assert result == expected
        assert result
        assert not result.startswith("-")

    def test_openclaw(self):
        line = _line_matching(
            REPO_ROOT / "app-catalog" / "agents" / "openclaw" / "scripts" / "install.sh",
            r"openclaw@",
        )
        expected = _expected_from_file(
            REPO_ROOT / "app-catalog" / "agents" / "openclaw" / "scripts" / "install.sh",
            r"openclaw@([0-9][^ \"']*)",
        )
        result = mod._parse_pin_value(line, pin_regex="openclaw@([0-9][^ \"']*)")
        assert result == expected
        assert result
        assert not result.startswith("-")


class TestGitPin:
    def test_behind_by_3_gives_ok_false(self):
        entry = {
            "kind": "git_pin",
            "fork": "jaylfc/rkllama",
            "pin_file": "scripts/install-rknpu.sh",
            "pin_var": "RKLLAMA_REF",
        }

        def mock_safe_get(path):
            if "/repos/jaylfc/rkllama" in path and "/compare/" not in path:
                return {
                    "full_name": "jaylfc/rkllama",
                    "parent": {"full_name": "rkllama/rkllama", "default_branch": "main"},
                }
            if "/compare/" in path:
                return {"behind_by": 3}
            return None

        with patch.object(mod, "_safe_get", side_effect=mock_safe_get):
            result = mod.git_pin(entry)

        assert result["ok"] is False
        assert result["behind_by"] == 3


class TestNpmPin:
    def test_latest_equals_pinned_gives_ok_true(self):
        entry = {
            "kind": "npm_pin",
            "package": "@jaylfc/qmd",
            "upstream_package": "@tobilu/qmd",
            "pin_file": "scripts/install-server.sh",
            "pin_var": "qmd_npm_version",
        }

        def mock_npm_get(package):
            return {"dist-tags": {"latest": "2.6.0"}}

        with patch.object(mod, "_npm_get", side_effect=mock_npm_get):
            result = mod.npm_pin(entry)

        assert result["ok"] is True
        assert result["pinned"] == "2.6.0"
        assert result["latest"] == "2.6.0"

    def test_latest_differs_gives_ok_false(self):
        entry = {
            "kind": "npm_pin",
            "package": "@jaylfc/qmd",
            "upstream_package": "@tobilu/qmd",
            "pin_file": "scripts/install-server.sh",
            "pin_var": "qmd_npm_version",
        }

        def mock_npm_get(package):
            return {"dist-tags": {"latest": "3.0.0"}}

        with patch.object(mod, "_npm_get", side_effect=mock_npm_get):
            result = mod.npm_pin(entry)

        assert result["ok"] is False
        assert result["pinned"] == "2.6.0"
        assert result["latest"] == "3.0.0"


class TestMain:
    def test_no_keyerror(self):
        def mock_safe_get(path):
            if "/repos/jaylfc/rkllama" in path and "/compare/" not in path:
                return {
                    "full_name": "jaylfc/rkllama",
                    "parent": {"full_name": "rkllama/rkllama", "default_branch": "main"},
                }
            if "/compare/" in path:
                return {"behind_by": 0}
            return None

        def mock_npm_get(package):
            if package == "@tobilu/qmd":
                return {"dist-tags": {"latest": "2.6.0"}}
            if package == "openclaw":
                return {"dist-tags": {"latest": "0.2.0"}}
            return None

        with patch.object(mod, "_safe_get", side_effect=mock_safe_get), \
             patch.object(mod, "_npm_get", side_effect=mock_npm_get):
            result = mod.main()

        assert result == 0
