# Contributing to TinyAgentOS

Welcome — and thanks for your interest in contributing. TinyAgentOS is a self-hosted AI agent platform for low-power hardware. Before diving in, please read the [README](README.md) for a project overview.

> **Note:** The project is in early development. APIs and interfaces may change. That is fine — contributions of all sizes are welcome.

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
5. Open a pull request against `main`

Keep pull requests focused. One feature or fix per PR is easier to review.

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

### Templates

- Use Pico CSS utility classes — do not introduce other CSS frameworks
- Use htmx attributes (`hx-get`, `hx-target`, `hx-swap`) for dynamic updates
- Write semantic HTML — use the right element for the job (`<nav>`, `<main>`, `<section>`, etc.)
- ARIA labels are required on interactive elements without visible text labels

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
  routes/              # One module per feature area (77 route modules)
  templates/           # Minimal: only agent_debugger.html remains (frontend is a React SPA)
  channel_hub/         # Framework-agnostic messaging (6 connectors + message router)
  adapters/            # Framework adapters (15 adapters, ~25 lines each)
  cluster/             # Distributed compute (worker registration, task routing, optimiser)
  worker/              # Cross-platform worker apps (system tray, Android, iOS)
  stores/              # Data access layer (SQLite via aiosqlite)
app-catalog/           # YAML manifests for installable apps (108 apps)
tests/                 # pytest test suite (~3,590 tests)
```

Routes are registered in `app.py`. Each route module imports its own store. Templates use htmx for partial page updates — full-page navigations are rare.

---

## Contact

Questions not suited for a GitHub issue? Email jaylfc25@gmail.com.
