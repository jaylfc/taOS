"""taosctl entry point: build the parser from auto-discovered noun modules,
dispatch the chosen handler, render the result, and map failures to exit codes.

Exit codes (per the agent-friendliness design):
  0  success
  1  transport / local error (could not reach the server, bad usage)
  2  API error (server returned non-2xx; its message is printed to stderr)
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional

from tinyagentos.cli.taosctl import __version__, output
from tinyagentos.cli.taosctl.client import ApiError, TaosClient, TransportError
from tinyagentos.cli.taosctl.commands import iter_noun_modules


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="taosctl",
        description="kubectl-style CLI over the taOS REST API (taosctl <noun> <verb>).",
    )
    parser.add_argument("--version", action="version", version=f"taosctl {__version__}")
    parser.add_argument("--url", help="Server base URL (else TAOS_URL or config)")
    parser.add_argument("--token", help="API token (else TAOS_TOKEN or config)")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    nouns = parser.add_subparsers(dest="noun", required=True, metavar="<noun>")
    for mod in sorted(iter_noun_modules(), key=lambda m: m.NOUN):
        mod.register(nouns)
    return parser


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help(sys.stderr)
        return 1
    client = TaosClient(url=args.url, token=args.token)
    try:
        result = args.func(args, client)
    except ApiError as exc:
        output.error(f"API error ({exc.status}): {exc.message}")
        return 2
    except TransportError as exc:
        output.error(str(exc))
        return 1
    output.render(result, as_json=args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
