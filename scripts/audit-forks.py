#!/usr/bin/env python3
"""Audit each tracked pin/fork against its upstream and emit a JSON report.

Tracked pins live in ``TRACKED`` below. For each entry the script asks
the GitHub API (for git_pin entries) or the npm registry (for npm_pin entries):

* git_pin: fork's pinned ref, parent.full_name, parent.default_branch,
           number of upstream commits the fork is missing on the parent branch
* npm_pin: pinned version in deploy script, latest upstream dist-tag

Output (stdout, JSON):

    {
      "generated_at": "...",
      "forks": [
        {
          "kind": "git_pin",
          "fork": "jaylfc/rkllama",
          "pin_file": "scripts/install-rknpu.sh",
          "pinned": "dadea413...",
          "upstream": "rkllama/rkllama",
          "upstream_branch": "main",
          "behind_by": 47,
          "ok": false,
          "reason": "47 commits behind upstream/main"
        }, ...
      ],
      "any_drift": true
    }

Run with ``GH_TOKEN`` set to a token that can read each fork's parent
(public repos only need the default GITHUB_TOKEN). No mutations.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]


def _parse_pin_value(line: str, pin_var: str | None = None, pin_regex: str | None = None) -> str | None:
    """Extract the deployed pin value from a line in a pin file.

    Supports:
    - ${VAR:-default} form: extracts the default value after ':-'
    - regex form: extracts the first capture group match
    - VAR="value" form: extracts the assigned value
    """
    m = re.search(r'\$\{[A-Za-z_][A-Za-z0-9_]*:-([^}]*)\}', line)
    if m:
        return m.group(1).strip()

    if pin_regex:
        m = re.search(pin_regex, line)
        if m:
            return m.group(1)

    if pin_var:
        pattern = r'^\s*' + re.escape(pin_var) + r'\s*=\s*["\']?([^"\'}]*)["\']?'
        m = re.match(pattern, line)
        if m:
            return m.group(1).strip()

    return None


def _read_pin(pin_file: str, pin_var: str | None = None, pin_regex: str | None = None) -> str | None:
    path = REPO_ROOT / pin_file
    try:
        with open(path) as f:
            for line in f:
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                if pin_var and pin_var in line:
                    value = _parse_pin_value(line, pin_var=pin_var)
                    if value is not None:
                        return value
                if pin_regex and re.search(pin_regex, line):
                    value = _parse_pin_value(line, pin_regex=pin_regex)
                    if value is not None:
                        return value
    except Exception:
        pass
    return None


# (kind, fork, pin_file, pin_var/pin_regex, upstream_package)
# New entry kinds: git_pin and npm_pin replace the old branch-tracking model.
TRACKED: list[dict] = [
    {"kind": "git_pin", "fork": "jaylfc/rkllama", "pin_file": "scripts/install-rknpu.sh", "pin_var": "RKLLAMA_REF"},
    {"kind": "npm_pin", "package": "@jaylfc/qmd", "upstream_package": "@tobilu/qmd", "pin_file": "scripts/install-server.sh", "pin_var": "qmd_npm_version"},
    {"kind": "npm_pin", "package": "openclaw", "upstream_package": "openclaw", "pin_file": "app-catalog/agents/openclaw/scripts/install.sh", "pin_regex": r"npm install -g [^\n]*openclaw@([0-9][0-9A-Za-z.+-]*)"},
]

API = "https://api.github.com"
NPM_REGISTRY = "https://registry.npmjs.org"


def _get(path: str) -> dict | list:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "taos-fork-audit"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(f"{API}{path}", headers=headers)
    with urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _safe_get(path: str) -> dict | list | None:
    try:
        return _get(path)
    except Exception as e:  # network / 404 / rate limit
        print(f"  warning: GET {path} failed: {e}", file=sys.stderr)
        return None


def _days_since(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - d).days
    except Exception:
        return None


def _npm_get(package: str) -> dict | None:
    quoted = quote(package, safe="")
    url = f"{NPM_REGISTRY}/{quoted}"
    try:
        req = Request(url, headers={"User-Agent": "taos-fork-audit"})
        with urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"  warning: GET {url} failed: {e}", file=sys.stderr)
        return None


def git_pin(entry: dict) -> dict:
    fork = entry["fork"]
    pin_file = entry["pin_file"]
    pin_var = entry.get("pin_var")
    pin_regex = entry.get("pin_regex")
    pinned = _read_pin(pin_file, pin_var=pin_var, pin_regex=pin_regex)
    out: dict = {
        "kind": "git_pin",
        "fork": fork,
        "pin_file": pin_file,
        "pinned": pinned,
        "upstream": None,
        "upstream_branch": None,
        "behind_by": None,
        "ok": False,
        "reason": "",
    }

    if not pinned:
        out["reason"] = "could not read pinned ref from pin file"
        return out

    repo = _safe_get(f"/repos/{fork}")
    if not isinstance(repo, dict):
        out["reason"] = "could not fetch fork repo"
        return out
    parent = repo.get("parent") or repo.get("source")
    if not parent:
        out["reason"] = "fork has no recorded parent on GitHub"
        return out
    upstream = parent["full_name"]
    out["upstream"] = upstream

    upstream_branch = parent.get("default_branch") or "main"
    out["upstream_branch"] = upstream_branch

    fork_owner = fork.split("/")[0]
    cmp = _safe_get(
        f"/repos/{upstream}/compare/{upstream_branch}...{fork_owner}:{pinned}"
    )
    if isinstance(cmp, dict):
        out["behind_by"] = cmp.get("behind_by", 0)
    else:
        out["reason"] = "could not determine drift (compare API failed)"
        return out

    out["ok"] = out["behind_by"] == 0
    if out["behind_by"] > 0:
        out["reason"] = f"{out['behind_by']} commits behind {upstream}/{upstream_branch}"
    else:
        out["reason"] = "in sync"
    return out


def npm_pin(entry: dict) -> dict:
    pin_file = entry["pin_file"]
    pin_var = entry.get("pin_var")
    pin_regex = entry.get("pin_regex")
    upstream_package = entry.get("upstream_package", entry.get("package"))
    pinned = _read_pin(pin_file, pin_var=pin_var, pin_regex=pin_regex)
    out: dict = {
        "kind": "npm_pin",
        "package": entry.get("package"),
        "pin_file": pin_file,
        "pinned": pinned,
        "upstream_package": upstream_package,
        "latest": None,
        "ok": False,
        "reason": "",
    }

    if not pinned:
        out["reason"] = "could not read pinned version from pin file"
        return out

    registry = _npm_get(upstream_package)
    if not isinstance(registry, dict):
        out["reason"] = "could not fetch upstream package from npm registry"
        return out
    latest = registry.get("dist-tags", {}).get("latest")
    out["latest"] = latest
    out["ok"] = pinned == latest
    if latest is None:
        out["reason"] = "could not determine latest upstream version"
    elif pinned == latest:
        out["reason"] = "in sync"
    else:
        out["reason"] = f"pinned {pinned} != latest {latest}"
    return out


def audit_one(entry: dict) -> dict:
    kind = entry.get("kind", "git_pin")
    if kind == "git_pin":
        return git_pin(entry)
    if kind == "npm_pin":
        return npm_pin(entry)
    return {"kind": kind, "ok": False, "reason": f"unknown kind {kind}"}


def main() -> int:
    forks = [audit_one(e) for e in TRACKED]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "forks": forks,
        "any_drift": any(not f["ok"] for f in forks),
    }
    json.dump(report, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
