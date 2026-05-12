# taOS Agent-Facing Documentation

This directory is the canonical agent-facing documentation for taOS. Every
markdown file here is also ingested into the in-OS knowledge library
(`category="agent-docs"`), so it's searchable from both:

- **External agents** via the knowledge API
  (`GET /api/knowledge/search?category=agent-docs&q=…`).
- **The in-OS taOS helper agent** via the same knowledge query.

If you're reading this on disk, you can also browse the files directly.

## Layout

- **`getting-started.md`** — auth, your first API call, your first CLI command.
- **`concepts/`** — short conceptual articles. Start with `permissions.md`.
- **`recipes/`** — task-oriented walkthroughs. Each recipe shows the API call
  alongside the `taosctl` command, with the expected response and a way to
  verify success.
- **`audit-results.md`** — per-endpoint pass/fail tracker for the current Pass
  1 scope (the AgentsApp surface plus the `ui.notify` primitive).
- **`reference/`** *(Pass 2+)* — auto-generated API reference.

## Conventions

- **Every fenced `bash` or `http` block in a recipe is executed by the C-tier
  conformance test.** If an example shouldn't be tested (it contains a
  placeholder, or it would create real resources), tag the block as
  `bash-skip` / `http-skip`.
- **Recipes reference the endpoint docstring; they don't duplicate it.** The
  docstring in `tinyagentos/routes/*.py` is the single source of truth for
  "what this endpoint does." Recipes layer on the HOW for specific tasks.
- **Errors carry `fix` and `doc_url`.** When an agent hits an error, the
  response tells it what to do next AND where to learn more. This directory
  is where those links point.

## Pass 1 scope

Pass 1 lands per-agent API tokens, bearer auth, the AgentsApp REST audit
remediation, the `ui.notify` primitive, this documentation tree, and the
conformance test suite that keeps it all honest. The full plan and audit
results live alongside this README.
