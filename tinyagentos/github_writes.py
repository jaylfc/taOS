"""taOS Agent GitHub writes: fork, push a branch, open a PR, file an issue.

Spec (taOS Agent full authority) sections 2 and 3. The agent acts as the user
with the user's own OAuth device-flow token (scope ``public_repo``), read by
the caller at the moment of a confirmed write and dropped after: this module
never stores, logs or returns it.

Guardrails, all mechanical:

- Secret scan first. Every file pushed, every commit message, PR title/body
  and issue title/body is scanned before ANY network call. A hit raises
  ``SecretScanRefused`` naming the matched kinds, never the matched value.
- The session must carry the taOS data dir: a write without one is refused,
  so the data-dir path check can never silently drop out.
- ``upstream`` must be a plain ``owner/repo``, and ``push_branch`` only writes
  to a repo that is a fork of it.
- Branches are always ``taos-agent/<slug>`` and PRs always target ``dev``.
- At most one open taOS Agent PR per repo per user: ``open_pr`` refuses with
  ``OpenPullRequestExists`` when the user already has one open on another
  ``taos-agent/*`` branch, and returns the existing PR for the same branch
  (pushing to the branch is how it gets updated).
- No destructive call exists here: no DELETE, no merge, and a branch update is
  a ``force: false`` fast-forward whose new commit is parented on the
  branch's current head.
- The PR and issue body end with the attribution line
  ``Filed by the taOS Agent on behalf of <login> (taOS <version>)``.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from tinyagentos import __version__

API_BASE = "https://api.github.com"
BRANCH_PREFIX = "taos-agent/"
PR_BASE = "dev"

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
_UPSTREAM_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/(?!\.\.?$)[A-Za-z0-9._-]{1,100}$")
_TIMEOUT = 30.0
# Forking is asynchronous: a new fork's git objects can 404 for a moment after
# the 202. The parent-commit read is retried on 404 with these pauses (about a
# minute in all) before the push gives up.
_FORK_READY_PAUSES = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0)


class SecretScanRefused(Exception):
    """The write was refused before any network call. ``kinds`` names what matched."""

    def __init__(self, kinds: list[str]):
        self.kinds = kinds
        super().__init__(f"secret scan refused the write: {', '.join(kinds)}")


class OpenPullRequestExists(Exception):
    """The user already has an open taOS Agent PR on this repo (on another branch)."""

    def __init__(self, url: str, number: int):
        self.url = url
        self.number = number
        super().__init__(f"an open taOS Agent pull request already exists: {url}")


@dataclass
class GitHubSession:
    """The user's token and login for one confirmed write. Never persisted."""

    token: str
    login: str
    http: httpx.AsyncClient
    data_dir: str | None
    api_base: str = API_BASE

    def __repr__(self) -> str:
        return f"GitHubSession(login={self.login!r})"


# ---------------------------------------------------------------------------
# Secret scan
# ---------------------------------------------------------------------------

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private_key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("api_key", re.compile(
        r"\b(?:sk-[A-Za-z0-9_-]{16,}"
        r"|gh[pousr]_[A-Za-z0-9]{20,}"
        r"|github_pat_[A-Za-z0-9_]{20,}"
        r"|AKIA[0-9A-Z]{16}"
        r"|AIza[0-9A-Za-z_-]{30,}"
        r"|xox[abpors]-[A-Za-z0-9-]{10,})"
    )),
    ("tailscale_hostname", re.compile(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.ts\.net\b", re.I)),
]
# Not part of a longer dotted number on either side, but a sentence-final full
# stop still counts as the end of the address.
_IPV4_RE = re.compile(
    r"(?<!\d)(?<!\d\.)(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?!\d|\.\d)"
)
# Loopback and the wildcard bind address say nothing about the user's network.
_HARMLESS_IPV4 = frozenset({"127.0.0.1", "0.0.0.0"})
_TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")
_ENV_ASSIGN_RE = re.compile(r"^\s*(?:export\s+)?[A-Z][A-Z0-9_]*\s*=\s*\S", re.M)


def secret_scan(text: str, *, path: str | None = None, data_dir: str | None = None) -> list[str]:
    """Return the sorted kinds of secret-shaped content in *text* (empty when clean)."""
    kinds: set[str] = set()
    for kind, pattern in _PATTERNS:
        if pattern.search(text):
            kinds.add(kind)
    for match in _IPV4_RE.finditer(text):
        if any(int(octet) > 255 for octet in match.groups()):
            continue
        addr = match.group(0)
        if addr in _HARMLESS_IPV4:
            continue
        if ipaddress.ip_address(addr) in _TAILSCALE_NET:
            kinds.add("tailscale_address")
        else:
            kinds.add("ipv4_address")
    if path is not None:
        name = path.rsplit("/", 1)[-1]
        env_named = (
            name in (".env", ".envrc") or name.startswith(".env.") or name.endswith(".env")
        )
        if env_named and _ENV_ASSIGN_RE.search(text):
            kinds.add("env_file")
    if data_dir and data_dir.rstrip("/"):
        # A relative data dir is also matched by its absolute spelling.
        markers = {data_dir.rstrip("/"), str(Path(data_dir).resolve()).rstrip("/")}
        if any(marker in text for marker in markers):
            kinds.add("data_dir_path")
    return sorted(kinds)


def _require_data_dir(gh: GitHubSession) -> None:
    if not (gh.data_dir or "").rstrip("/"):
        raise ValueError("data_dir is required for a GitHub write")


def _refuse_if_secret(gh: GitHubSession, items: list[tuple[str | None, str]]) -> None:
    _require_data_dir(gh)
    kinds: set[str] = set()
    for path, text in items:
        kinds.update(secret_scan(text, path=path, data_dir=gh.data_dir))
        if path is not None:
            kinds.update(secret_scan(path, data_dir=gh.data_dir))
    if kinds:
        raise SecretScanRefused(sorted(kinds))


# ---------------------------------------------------------------------------
# REST plumbing
# ---------------------------------------------------------------------------

def _branch(slug: str) -> str:
    if not isinstance(slug, str) or not _SLUG_RE.match(slug):
        raise ValueError("slug must be 1-60 chars of a-z, 0-9 and '-', not starting with '-'")
    return BRANCH_PREFIX + slug


def _check_upstream(upstream: str) -> None:
    if not isinstance(upstream, str) or not _UPSTREAM_RE.match(upstream):
        raise ValueError("upstream must be 'owner/repo'")


def _attribution(login: str) -> str:
    return f"Filed by the taOS Agent on behalf of {login} (taOS {__version__})"


def _with_attribution(body: str, login: str) -> str:
    body = (body or "").rstrip()
    line = _attribution(login)
    return f"{body}\n\n{line}\n" if body else f"{line}\n"


async def _call(
    gh: GitHubSession, method: str, path: str, *, json: dict | None = None,
    params: dict | None = None, ok_404: bool = False,
) -> dict | list | None:
    resp = await gh.http.request(
        method,
        f"{gh.api_base}{path}",
        json=json,
        params=params,
        headers={
            "Authorization": f"Bearer {gh.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=_TIMEOUT,
    )
    if ok_404 and resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def _fork_name(gh: GitHubSession, upstream: str) -> str:
    return f"{gh.login}/{upstream.split('/', 1)[1]}"


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

async def ensure_fork(gh: GitHubSession, upstream: str) -> str:
    """Return the user's fork of *upstream*, creating it when absent."""
    _check_upstream(upstream)
    _require_data_dir(gh)
    fork = _fork_name(gh, upstream)
    repo = await _call(gh, "GET", f"/repos/{fork}", ok_404=True)
    if repo is not None:
        parent = (repo.get("parent") or {}).get("full_name", "")
        if not repo.get("fork") or parent.lower() != upstream.lower():
            raise ValueError(f"{fork} exists and is not a fork of {upstream}")
        return repo["full_name"]
    created = await _call(gh, "POST", f"/repos/{upstream}/forks")
    return created.get("full_name", fork)


async def _fork_commit(gh: GitHubSession, fork: str, sha: str) -> dict:
    """Read *sha* from the fork, waiting (bounded) for a just-created fork."""
    path = f"/repos/{fork}/git/commits/{sha}"
    for pause in _FORK_READY_PAUSES:
        commit = await _call(gh, "GET", path, ok_404=True)
        if commit is not None:
            return commit
        await asyncio.sleep(pause)
    return await _call(gh, "GET", path)


async def push_branch(
    gh: GitHubSession, upstream: str, slug: str, files: dict[str, str], message: str
) -> str:
    """Commit *files* onto ``taos-agent/<slug>`` in the user's fork; return the branch.

    A new branch starts from upstream ``dev``. An existing branch is
    fast-forwarded (the commit's parent is its current head, ``force: false``).
    The target is checked to be the user's fork of *upstream* (created when
    absent) before anything is written.
    """
    _check_upstream(upstream)
    branch = _branch(slug)
    if not files:
        raise ValueError("nothing to push")
    _refuse_if_secret(gh, [*files.items(), (None, message)])

    fork = await ensure_fork(gh, upstream)
    current = await _call(gh, "GET", f"/repos/{fork}/git/ref/heads/{branch}", ok_404=True)
    if current is not None:
        parent = current["object"]["sha"]
    else:
        base = await _call(gh, "GET", f"/repos/{upstream}/git/ref/heads/{PR_BASE}")
        parent = base["object"]["sha"]
    parent_commit = await _fork_commit(gh, fork, parent)
    tree = await _call(gh, "POST", f"/repos/{fork}/git/trees", json={
        "base_tree": parent_commit["tree"]["sha"],
        "tree": [
            {"path": path, "mode": "100644", "type": "blob", "content": content}
            for path, content in files.items()
        ],
    })
    commit = await _call(gh, "POST", f"/repos/{fork}/git/commits", json={
        "message": message, "tree": tree["sha"], "parents": [parent],
    })
    if current is not None:
        await _call(gh, "PATCH", f"/repos/{fork}/git/refs/heads/{branch}",
                    json={"sha": commit["sha"], "force": False})
    else:
        await _call(gh, "POST", f"/repos/{fork}/git/refs",
                    json={"ref": f"refs/heads/{branch}", "sha": commit["sha"]})
    return branch


async def _open_taos_agent_pulls(gh: GitHubSession, upstream: str) -> list[dict]:
    mine = f"{gh.login.lower()}:{BRANCH_PREFIX}"
    found: list[dict] = []
    page = 1
    while True:
        pulls = await _call(gh, "GET", f"/repos/{upstream}/pulls",
                            params={"state": "open", "per_page": 100, "page": page})
        found.extend(
            p for p in pulls
            if (p.get("head") or {}).get("label", "").lower().startswith(mine)
        )
        if len(pulls) < 100:
            return found
        page += 1


async def open_pr(gh: GitHubSession, upstream: str, slug: str, title: str, body: str) -> dict:
    """Open a PR from ``<login>:taos-agent/<slug>`` into ``dev``; return ``{url, number}``."""
    _check_upstream(upstream)
    branch = _branch(slug)
    full_body = _with_attribution(body, gh.login)
    _refuse_if_secret(gh, [(None, title), (None, full_body)])

    pulls = await _open_taos_agent_pulls(gh, upstream)
    for pull in pulls:
        if pull["head"].get("ref") == branch:
            return {"url": pull["html_url"], "number": pull["number"]}
    if pulls:
        raise OpenPullRequestExists(pulls[0]["html_url"], pulls[0]["number"])

    created = await _call(gh, "POST", f"/repos/{upstream}/pulls", json={
        "title": title, "body": full_body,
        "head": f"{gh.login}:{branch}", "base": PR_BASE,
        "maintainer_can_modify": True,
    })
    return {"url": created["html_url"], "number": created["number"]}


async def create_issue(gh: GitHubSession, upstream: str, title: str, body: str) -> dict:
    """File an issue on *upstream* as the user; return ``{url, number}``."""
    _check_upstream(upstream)
    full_body = _with_attribution(body, gh.login)
    _refuse_if_secret(gh, [(None, title), (None, full_body)])
    created = await _call(gh, "POST", f"/repos/{upstream}/issues",
                          json={"title": title, "body": full_body})
    return {"url": created["html_url"], "number": created["number"]}
