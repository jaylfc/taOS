# Contributing to TinyAgentOS

Welcome — and thanks for your interest in contributing. TinyAgentOS is a self-hosted AI agent platform for low-power hardware. Before diving in, please read the [README](README.md) for a project overview.

> **Note:** The project is in early development. APIs and interfaces may change. That is fine — contributions of all sizes are welcome.

---

## License & Contributor License Agreement

taOS is licensed under the **taOS Sustainable Use License** (source-available, not open source; see [`LICENSE`](LICENSE)) — free to use, modify, and self-host for personal use and for your own organisation's internal business purposes, with a separate commercial license required to sell it, host it as a paid service, or build it into a product you monetise.

To keep this sustainable, **all contributors must agree to the Contributor License Agreement ([`CLA.md`](CLA.md))** before their contributions are merged. The CLA grants JAN LABS LTD the right to include and **relicense** your contributions under the project's licenses; **you keep ownership of your work**. You sign once — on your first pull request, comment **"I have read the CLA Document and I hereby sign the CLA"** and the CLA check turns green; it then covers all your future contributions.

---

## Getting Started for Contributors

```bash
git clone https://github.com/jaylfc/tinyagentos.git
cd tinyagentos
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
# Build the desktop SPA — static/desktop/ is gitignored (generated artifact)
cd desktop && npm install && npm run build && cd ..
pytest tests/ -v
```

Python 3.10 or later and Node.js 22 or later are required.

For frontend development, use `cd desktop && npm run dev` — Vite serves with hot reload on port 5173.

---

## How to Contribute

### Bug Reports

Open a GitHub issue with:
- A clear title describing the problem
- Steps to reproduce
- Expected vs actual behaviour
- Platform and Python version

### Feature Requests

Open a GitHub issue describing:
- The use case you are trying to solve
- Why it belongs in the core project rather than a plugin or external tool

### Adding Apps to the Catalog

The app catalog is one of the easiest ways to contribute. See [Adding an App to the Catalog](#adding-an-app-to-the-catalog) below.

### Code Contributions

1. Fork the repository
2. Create a branch: `git checkout -b feat/my-feature`
3. Make your changes and add tests
4. Run `pytest tests/ -v` — all tests must pass
5. Open a pull request against **`dev`**, not `master`

Keep pull requests focused. One feature or fix per PR is easier to review.

> **Branches:** `master` is the stable branch that installs track, so it only
> receives tested changes promoted from `dev`. All contributions target `dev`.
> If you open a PR against `master` by mistake, no problem — we'll retarget it
> to `dev` (the commits and review carry over).

### Documentation

Documentation improvements are always welcome — typo fixes, clarifications, better examples. Open a PR directly.

---

## Adding an App to the Catalog

The catalog lives in `app-catalog/`. Each app has its own directory containing a `manifest.yaml`.

### Step 1 — Create the directory

```
app-catalog/
  agents/        # agent frameworks
  models/        # LLM models
  plugins/       # tools and plugins
  services/      # background services
```

Pick the appropriate category and create a directory named after your app's `id`:

```bash
mkdir app-catalog/agents/my-framework
```

### Step 2 — Write manifest.yaml

Use `app-catalog/agents/langroid/manifest.yaml` as a template:

```yaml
id: my-framework
name: My Framework
type: agent-framework        # agent-framework | model | plugin | service
version: 1.0.0
description: "One-line description of what this does"
homepage: https://github.com/example/my-framework
license: MIT

requires:
  ram_mb: 512                # minimum RAM in MB
  python: ">=3.10"

install:
  method: pip                # pip | script | docker
  package: my-framework

config_schema:
  - name: model
    type: model-select
    label: LLM Model
    required: true

hardware_tiers:
  arm-npu-16gb: full         # full | limited | unsupported
  arm-npu-32gb: full
  x86-cuda-12gb: full
  x86-vulkan-8gb: full
  cpu-only: limited
```

All fields except `config_schema` are required. The `hardware_tiers` block controls which hardware profiles see the app as recommended.

### Step 3 — Update catalog.yaml

Add an entry to `app-catalog/catalog.yaml` under the appropriate section:

```yaml
- id: my-framework
  type: agent-framework
  version: 1.0.0
  name: My Framework
  description: "One-line description matching your manifest"
```

### Step 4 — Open a PR

Submit a pull request. The CI will run the catalog tests automatically. Include a link to the upstream project in your PR description.

---

## Code Style

### Python

- Follow the patterns already in the codebase — there is no strict linter, but keep it readable
- One concern per module; avoid cross-importing between route files
- Use `async def` for route handlers; use `await` for all I/O

### Frontend

The UI is a React SPA (`desktop/`) built with Vite. Static assets are served from `static/desktop/` after `npm run build`. If you are adding a new UI surface:

- Follow existing React patterns in `desktop/src/` — no server-rendered templates for new features
- ARIA labels are required on interactive elements without visible text labels
- One concern per component; keep API calls in dedicated hooks or service files

### Tests

- Use pytest; fixtures live in `tests/conftest.py` — use them
- Mirror the module structure: `tinyagentos/routes/agents.py` -> `tests/test_agents.py`
- All PRs must pass CI before merge

### Commits

Use conventional commit style:

| Prefix | Use for |
|--------|---------|
| `feat:` | new feature |
| `fix:` | bug fix |
| `docs:` | documentation only |
| `refactor:` | code change with no behaviour change |
| `test:` | adding or updating tests |
| `chore:` | tooling, deps, CI |

Do not include AI tool attribution in commit messages.

---

## Testing

Run the full test suite:

```bash
pytest tests/ -v
```

Run a specific test file:

```bash
pytest tests/test_catalog_sync.py -v
```

The project has ~3,590 tests. CI runs against Python 3.12 and 3.13 on every pull request (two matrix jobs). Python 3.11 is added on the nightly scheduled run. A PR cannot be merged until all matrix jobs pass.

When adding a feature, add tests that cover the new behaviour. When fixing a bug, add a regression test.

---

## Architecture Overview

```
tinyagentos/
  app.py               # FastAPI application factory, lifespan, route registration
  config.py            # Platform config, hardware detection
  routes/              # One module per feature area (86 route modules)
  templates/           # Minimal: only agent_debugger.html remains (frontend is a React SPA)
  channel_hub/         # Framework-agnostic messaging (6 connectors + message router)
  adapters/            # Framework adapters (15 adapters, ~25 lines each)
  cluster/             # Distributed compute (worker registration, task routing, optimiser)
  worker/              # Cross-platform worker apps (system tray, Android, iOS)
  stores/              # Data access layer (SQLite via aiosqlite)
app-catalog/           # YAML manifests for installable apps (108 apps)
tests/                 # pytest test suite (~3,590 tests)
```

Routes are registered in `app.py`. Route modules access stores via `request.app.state` (dependency injection set up in the app lifespan) — they do not import stores directly. The frontend is a React SPA; `templates/` is minimal and only used for the agent debugger page.

---

## Contact

Questions not suited for a GitHub issue? Email jaylfc25@gmail.com.
