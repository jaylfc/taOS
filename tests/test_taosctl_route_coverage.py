"""Static gate: every taosctl command must call a real client method on a real route.

The taosctl command groups are thin wrappers over server API routes. Two classes
of bug slip past the per-command unit tests (which mock the client): calling a
client method that does not exist (e.g. client.put when the client has no put),
and calling an API path that no server route serves (always 404). This test
parses the command files with ast and checks both against the source of truth:
TaosClient's methods and the app's registered routes.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
import yaml

from tinyagentos.app import create_app
from tinyagentos.cli.taosctl.client import TaosClient

COMMANDS_DIR = pathlib.Path("tinyagentos/cli/taosctl/commands")
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def _client_methods() -> set[str]:
    return {m for m in HTTP_METHODS if callable(getattr(TaosClient, m, None))}


def _norm(path: str) -> str:
    """Drop the query string and collapse every {param} to {} so a command path
    and a route template compare equal regardless of the param name."""
    path = path.split("?", 1)[0]
    out, depth = [], 0
    for ch in path:
        if ch == "{":
            depth += 1
            if depth == 1:
                out.append("{}")
        elif ch == "}":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return "".join(out).rstrip("/") or "/"


def _literal_path(node: ast.AST) -> str | None:
    """Return the normalized path if the arg is a str literal or an f-string,
    else None (dynamic paths are skipped, not failed)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return _norm(node.value)
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            else:
                parts.append("{}")  # an interpolated path param
        return _norm("".join(parts))
    return None


def _client_calls():
    """Yield (file, lineno, method, path_or_None) for every client.<method>(...) call."""
    for pyfile in sorted(COMMANDS_DIR.glob("*.py")):
        tree = ast.parse(pyfile.read_text(), filename=str(pyfile))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                    and fn.value.id == "client" and fn.attr in HTTP_METHODS):
                continue
            path = _literal_path(node.args[0]) if node.args else None
            yield pyfile.name, node.lineno, fn.attr, path


@pytest.fixture(scope="module")
def routes(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("taosctl_routes")
    (tmp / "config.yaml").write_text(yaml.dump({
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [], "qmd": {"url": "http://localhost:7832"}, "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }))
    (tmp / ".setup_complete").touch()
    app = create_app(data_dir=tmp)
    pairs = set()
    for r in app.routes:
        for m in getattr(r, "methods", None) or []:
            pairs.add((m.lower(), _norm(getattr(r, "path", ""))))
    return pairs


def test_client_methods_exist():
    valid = _client_methods()
    bad = [f"{f}:{ln} client.{m}()" for f, ln, m, _ in _client_calls() if m not in valid]
    assert not bad, "taosctl calls a client method that does not exist: " + "; ".join(bad)


def test_command_paths_have_routes(routes):
    missing = []
    for f, ln, method, path in _client_calls():
        if path is None:
            continue  # dynamic path, cannot check statically
        if (method, path) not in routes:
            missing.append(f"{f}:{ln} {method.upper()} {path}")
    assert not missing, (
        "taosctl calls API paths with no matching server route:\n  " + "\n  ".join(missing)
    )
