"""Output rendering for taosctl. Default is human-readable; --json prints the
raw machine-readable JSON. Data goes to stdout, diagnostics to stderr."""
from __future__ import annotations

import json
import sys
from typing import Any


def render(data: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, default=str))
        return
    if data is None:
        return
    # Unwrap the common {"items": [...]} list envelope.
    if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
        data = data["items"]
    if isinstance(data, list):
        _render_table(data)
    elif isinstance(data, dict):
        _render_kv(data)
    else:
        print(data)


def _render_table(rows: list) -> None:
    if not rows:
        print("(none)")
        return
    if not all(isinstance(r, dict) for r in rows):
        for r in rows:
            print(r)
        return
    # Prefer a small set of identifying columns when present, else the first
    # few keys of the first row.
    preferred = ["id", "name", "slug", "status", "state", "title", "framework"]
    first = rows[0]
    cols = [c for c in preferred if c in first] or list(first.keys())[:5]
    widths = {c: max(len(c), *(len(_cell(r.get(c))) for r in rows)) for c in cols}
    header = "  ".join(c.upper().ljust(widths[c]) for c in cols)
    print(header)
    for r in rows:
        print("  ".join(_cell(r.get(c)).ljust(widths[c]) for c in cols))


def _render_kv(obj: dict) -> None:
    width = max((len(k) for k in obj), default=0)
    for k, v in obj.items():
        if isinstance(v, (dict, list)):
            v = json.dumps(v, default=str)
        print(f"{k.ljust(width)}  {v}")


def _cell(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, (dict, list)):
        return json.dumps(v, default=str)
    return str(v)


def error(msg: str) -> None:
    print(f"taosctl: {msg}", file=sys.stderr)
