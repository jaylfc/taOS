"""Auto-discovery of taosctl noun modules.

Every module in this package (except those starting with ``_``) is a noun. A
noun module must expose:
  NOUN: str                      -- the subcommand word, e.g. "agents"
  register(subparsers) -> None   -- add its <noun> parser + verb subparsers,
                                    each verb's handler set via
                                    parser.set_defaults(func=handler), where
                                    handler(args, client) -> data | None

Adding a noun is adding a file here; no central list to edit, so parallel
contributors never collide on a shared registry.
"""
from __future__ import annotations

import importlib
import pkgutil
from typing import Iterator


def iter_noun_modules() -> Iterator[object]:
    import tinyagentos.cli.taosctl.commands as pkg
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"{pkg.__name__}.{info.name}")
        if hasattr(mod, "register") and hasattr(mod, "NOUN"):
            yield mod
