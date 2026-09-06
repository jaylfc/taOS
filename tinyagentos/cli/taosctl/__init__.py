"""taosctl: a kubectl-style CLI over the taOS REST API.

Shell-driveable surface so coding-CLI agents, scripts, and humans can do
anything a taOS user can do without poking the UI. Subcommands map 1:1 to API
verbs: ``taosctl <noun> <verb> [args]``. Noun modules live in
``tinyagentos/cli/taosctl/commands/`` and are auto-discovered, so a new noun is
a new file with no central registry edit (conflict-free to add in parallel).

Run via the ``taosctl`` console script or directly:

    python -m tinyagentos.cli.taosctl <noun> <verb> ...
"""
from __future__ import annotations

__version__ = "0.1.0"
