#!/usr/bin/env python3
"""Audit each tracked fork against its upstream and emit a JSON report.

Tracked forks live in ``TRACKED`` below. For each entry the script asks
the GitHub API:

* fork's sync-branch HEAD sha + commit date
* upstream parent's branch HEAD sha + commit date
* fork's latest GitHub release tag + published_at
* number of upstream commits the fork is missing on the sync branch
* days since the fork's last release

Output (stdout, JSON):

    {
      "generated_at": "...",
      "forks": [
        {
          "fork": "jaylfc/openclaw",
          "upstream": "openclaw/openclaw",
          "sync_branch": "taos-fork",
          "upstream_branch": "main",
          "fork_head": {"sha": "...", "date": "..."},
          "upstream_head": {"sha": "...", "date": "..."},
          "behind_by": 47,
          "fork_release": {"tag": "rolling", "published_at": "...",
                            "age_days": 20},
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
from urllib.parse import quote
from urllib.request import Request, urlopen


def _parse_pin_value(line: str, pin_var: str | None = None, pin_regex: str | None = None) -> str | None:
    """Extract the deployed pin value from a line in a pin file.

    Supports:
    - ${VAR:-default} form: extracts the default value after ':-'
    - regex form: extracts the first capture group match
    - VAR="value" form: extracts the assigned value
    """
    # 1. Try ${VAR:-default} form
    m = re.search(r'\$\{([^}:]*?)(?:[:][^}]*)?\}', line)
    if m:
        content = m.group(1)
        if ':' in content:
            default = content.split(':', 1)[1].strip()
            return default.strip()
        return content.strip()

    # 2. Try pin_regex form
    if pin_regex:
        m = re.search(pin_regex, line)
        if m:
            return m.group(1)

    # 3. Try VAR="value" form
    if pin_var:
        pattern = rf'^\s*{re.escape(pin_var)}\s*=\s*["\']?([^"\'}]*)["\']?'
        m = re.match(pattern, line)
        if m:
            return m.group(1).strip()

    return None


# (kind, fork, pin_file, pin_var/pin_regex, upstream_branch_on_parent)
# upstream_branch=None means "use the parent's default branch".
# New entry kinds: git_pin and npm_pin replace the old branch-tracking model.
TRACKED: list[dict] = [
    {"kind": "git_pin", "fork": "jaylfc/rkllama", "pin_file": "scripts/install-rknpu.sh", "pin_var": "RKLLAMA_REF"},
    {"kind": "npm_pin", "package": "@jaylfc/qmd", "upstream_package": "@tobilu/qmd", "pin_file": "scripts/install-server.sh", "pin_var": "qmd_npm_version"},
    {"kind": "npm_pin", "package": "openclaw", "upstream_package": "openclaw", "pin_file": "app-catalog/agents/openclaw/scripts/install.sh", "pin_regex": "openclaw@([0-9][^ \\"']*)"},
]

API = "https://api.github.com"


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


def audit_one(entry: dict) -> dict:
    fork = entry["fork"]
    sync_branch = entry["sync_branch"]
    requested_upstream_branch = entry.get("upstream_branch")
    out: dict = {
        "fork": fork,
        "sync_branch": sync_branch,
        "upstream": None,
        "upstream_branch": None,
        "fork_head": None,
        "upstream_head": None,
        "behind_by": None,
        "fork_release": None,
        "ok": False,
        "reason": "",
    }

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

    upstream_branch = requested_upstream_branch or parent.get("default_branch") or "main"
    out["upstream_branch"] = upstream_branch

    fb = _safe_get(f"/repos/{fork}/branches/{quote(sync_branch, safe='')}")
    if isinstance(fb, dict) and fb.get("commit"):
        out["fork_head"] = {
            "sha": fb["commit"]["sha"],
            "date": fb["commit"].get("commit", {}).get("committer", {}).get("date"),
        }

    ub = _safe_get(f"/repos/{upstream}/branches/{quote(upstream_branch, safe='')}")
    if isinstance(ub, dict) and ub.get("commit"):
        out["upstream_head"] = {
            "sha": ub["commit"]["sha"],
            "date": ub["commit"].get("commit", {}).get("committer", {}).get("date"),
        }

    if out["fork_head"] and out["upstream_head"]:
        # `compare` returns commits unique to head (upstream) vs base (fork)
        cmp = _safe_get(
            f"/repos/{upstream}/compare/{out['fork_head']['sha']}...{out['upstream_head']['sha']}"
        )
        if isinstance(cmp, dict):
            out["behind_by"] = cmp.get("ahead_by", 0)

    rel = _safe_get(f"/repos/{fork}/releases/latest")
    if isinstance(rel, dict) and rel.get("tag_name"):
        out["fork_release"] = {
            "tag": rel["tag_name"],
            "published_at": rel.get("published_at"),
            "age_days": _days_since(rel.get("published_at")),
        }

    age = (out["fork_release"] or {}).get("age_days")
    issues = []
    # behind_by=None means the GH compare API failed — don't silently
    # downgrade that to "in sync". Surface as its own warning.
    if out["behind_by"] is None:
        issues.append("could not determine drift (compare API failed)")
    elif out["behind_by"] > 0:
        issues.append(f"{out['behind_by']} commits behind {upstream}/{upstream_branch}")
    if age is not None and age > 14:
        issues.append(f"latest release is {age} days old")
    out["ok"] = not issues
    out["reason"] = "; ".join(issues) if issues else "in sync"
    return out


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
