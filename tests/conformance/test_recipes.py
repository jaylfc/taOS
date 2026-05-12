"""C-tier conformance: replay every testable http block in docs/agents/recipes/*.md
against the test app's client fixture. Catches doc rot before users hit it.

Bash blocks are tagged bash-skip in the recipes; their content is documentation
for human readers but isn't tested here (the ASGI test transport has no real
socket for curl). The matching http block right below each bash-skip is what
the conformance runner actually replays.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.conformance._recipe_parser import extract_blocks


RECIPES_DIR = Path(__file__).resolve().parent.parent.parent / "docs" / "agents" / "recipes"


def _discover_recipe_blocks():
    cases = []
    if not RECIPES_DIR.exists():
        return cases
    for md in sorted(RECIPES_DIR.glob("*.md")):
        for block in extract_blocks(md):
            if block["lang"] != "http":
                continue  # bash blocks are documentation-only in Pass 1
            cases.append(
                pytest.param(
                    md.name,
                    block,
                    id=f"{md.name}:line{block['line_no']}",
                )
            )
    return cases


def _parse_http_block(code: str) -> tuple[str, str, dict[str, str], str | None]:
    """Parse a minimal HTTP/1.1 request block.

    Returns (method, path, headers, body). body is None when absent.
    """
    lines = code.strip().splitlines()
    request_line = lines[0]
    m = re.match(r"(\w+)\s+(\S+)", request_line)
    if not m:
        raise ValueError(f"unparseable http request line: {request_line!r}")
    method = m.group(1).upper()
    path = m.group(2)
    # Header lines until blank line
    headers: dict[str, str] = {}
    body_lines: list[str] = []
    in_body = False
    for line in lines[1:]:
        if not in_body and line.strip() == "":
            in_body = True
            continue
        if in_body:
            body_lines.append(line)
        else:
            if ":" not in line:
                continue
            k, _, v = line.partition(":")
            headers[k.strip()] = v.strip()
    body = "\n".join(body_lines).strip() if body_lines else None
    return method, path, headers, body


def _substitute_token(headers: dict[str, str], token: str) -> dict[str, str]:
    """Replace $TAOS_TOKEN placeholder in headers with the real token."""
    return {k: v.replace("$TAOS_TOKEN", token) for k, v in headers.items()}


@pytest.mark.parametrize("recipe_name,block", _discover_recipe_blocks())
@pytest.mark.asyncio
async def test_recipe_block_replays_successfully(recipe_name, block, client, app):
    """Replay the http block and assert no 5xx response.

    We don't assert a specific status code — recipes intentionally show
    happy paths AND error paths (e.g. agent_not_found 404 examples).
    The contract is "the server doesn't blow up": no 5xx.
    """
    store = app.state.agent_tokens_store
    plaintext, _ = await store.issue(
        agent_id="recipe-conf-runner",
        user_id="recipe-conf-user",
        scope=["*"],
    )

    method, path, headers, body = _parse_http_block(block["code"])
    headers = _substitute_token(headers, plaintext)

    request_kwargs: dict = {"headers": headers}
    if body:
        try:
            request_kwargs["json"] = json.loads(body)
        except json.JSONDecodeError:
            request_kwargs["content"] = body

    resp = await client.request(method, path, **request_kwargs)
    assert resp.status_code < 500, (
        f"recipe {recipe_name} line {block['line_no']} returned "
        f"{resp.status_code}: {resp.text[:300]}"
    )
