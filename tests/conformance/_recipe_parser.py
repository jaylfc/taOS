"""Extract fenced code blocks from agent recipe markdown for conformance testing.

Convention:
- ```bash``` and ```http``` blocks are testable.
- ```bash-skip``` and ```http-skip``` blocks are explicitly NOT tested (use for
  examples with placeholders or destructive commands).
- Any other language tag is ignored.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TypedDict


class Block(TypedDict):
    lang: str
    code: str
    line_no: int


_FENCE_OPEN = re.compile(r"^```(\S+)\s*$")
_TESTABLE_LANGS = {"bash", "http"}


def extract_blocks(md_path: Path) -> list[Block]:
    """Walk a markdown file; return testable blocks in document order.

    line_no is 1-indexed and points at the opening fence so failure messages
    can deep-link into the recipe.
    """
    blocks: list[Block] = []
    lines = md_path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        m = _FENCE_OPEN.match(lines[i])
        if not m:
            i += 1
            continue
        lang = m.group(1)
        start_line = i + 1  # 1-indexed
        i += 1
        code_lines: list[str] = []
        while i < len(lines) and not lines[i].startswith("```"):
            code_lines.append(lines[i])
            i += 1
        # Skip closing fence (or trailing EOF). Only emit blocks whose lang
        # is in the testable set — bash-skip/http-skip and other tags drop.
        if lang in _TESTABLE_LANGS:
            blocks.append({
                "lang": lang,
                "code": "\n".join(code_lines),
                "line_no": start_line,
            })
        i += 1
    return blocks
