# Audit: tinyagentos → taos Rename Blast Radius

**Generated:** 2026-07-17
**Task:** taOS #1937 C0
**Purpose:** Scoping document — no file changes, just counting and mapping.

> **Note on search scope:** This document contains many `tinyagentos` occurrences.
> All search/count commands in this audit exclude `docs/audit/tinyagentos-rename-blast-radius.md`
> so the audit file itself does not inflate totals.

---

## 1. Executive Summary

| Metric | Count |
|--------|-------|
| **Total raw occurrences** of `tinyagentos` | **6,600** |
| **Unique files touched** | **1,183** |
| **Python files in package** | 587 |
| **Test files** | ~310 |
| **`from/import tinyagentos` statements** | 2,869 |
| **`mock.patch("tinyagentos...")` strings** | 1,001 |
| **CLI invocation references** (`python -m tinyagentos`) | 36 |
| **systemd unit references** | 15+ |
| **CI (GitHub Actions) references** | 15 |
| **README references** | ~35 |
| **Docs (markdown/rst, excluding README)** | 1,485 |
| **Install scripts** (`scripts/`) | 202 |
| **os-build references** | 43 |
| **Desktop SPA references** | 30+ |
| **App catalog references** | 15 |
| **Config (YAML/TOML, excluding pyproject.toml)** | ~20 |
| **Templates/HTML** (`site/public/`, `landing/`) | ~30 |

---

## 2. Breakdown by Category

### 2.1 Python Package — THE ROOT CHANGE

The package **directory** is `tinyagentos/` and the package **name** in `pyproject.toml` is `"tinyagentos"`. Renaming the package means:

- **Directory rename:** `tinyagentos/` → `taos/`
- **Every `from tinyagentos.X import Y`** in 587 package files + 310 test files + any external scripts
- **Every `import tinyagentos`** statement
- **Every `mock.patch("tinyagentos...")`** string in test files (1,001 occurrences)
- **pyproject.toml `[project] name`** field

**Optimistic note:** The `[project.scripts]` section already has `taos` aliases registered:
```toml
taos = "tinyagentos.app:main"        # already exists
taos-gui = "tinyagentos.app:gui"
taos-worker-ctl = "tinyagentos.cli.worker:main"
taosctl = "tinyagentos.cli.taosctl.__main__:main"
```
The entry points `tinyagentos` and `tinyagentos-worker` would need migration, but the `taos`-prefixed aliases already point at the current package path. Post-rename, they'd point at `taos.app:main` instead.

### 2.2 pyproject.toml

**Critical fields:**
```toml
[project]
name = "tinyagentos"                              # → "taos"

[project.scripts]
tinyagentos = "tinyagentos.app:main"              # → taos = "taos.app:main"
tinyagentos-worker = "tinyagentos.worker.__main__:main"  # already aliased

[tool.setuptools.packages.find]
include = ["tinyagentos*"]                        # → ["taos*"]
```
The `taos` and `taosctl` entry points already exist — only the legacy `tinyagentos`/`tinyagentos-worker` names and the package include pattern need updating.

### 2.3 systemd Units

**Files affected:**

| File | References |
|------|-----------|
| `tinyagentos.service` (repo root) | WorkingDirectory, ExecStart — uses `tinyagentos.app:create_app` |
| `systemd/tinyagentos.service` | Same, template form |
| `systemd/tinyagentos-disk-quota.service` | After/Wants + ExecStart path (`/opt/tinyagentos/...`) |
| `systemd/tinyagentos-disk-quota.timer` | Unit name reference |
| `systemd/tinyagentos-host-firewall.service` | Before + Description + script paths |
| `systemd/tinyagentos-host-firewall.timer` | Unit name reference |
| `scripts/systemd/tinyagentos.service` | Full template with `TAOS_PYTHON -m tinyagentos` |
| `os-build/.../tinyagentos.service` | Install-path variant |
| `app-catalog/.../tinyagentos-recycle-sweep.service` | Unit name |
| `app-catalog/.../tinyagentos-recycle-sweep.timer` | Unit name |

**Special concern:** Unit filenames themselves contain `tinyagentos` — renaming them means also updating `Wants=`/`After=`/`Before=`/`Unit=` directives in related units. The `scripts/install-server.sh` installer generates these units dynamically, so it has its own set of `sed`/template substitutions that reference `tinyagentos`.

### 2.4 CLI Invocations

36 references to `python -m tinyagentos` / `python -m tinyagentos.worker` / `uv run tinyagentos`:

- `scripts/install-server.sh` (lines: 1906, 1957, 2128) — ExecStart templates, manual-run instructions
- `scripts/install-worker.sh` (lines: 491, 526, 579, 664, 1295, 1390, 1419, 1501) — enrollment, systemd ExecStart, benchmark runner
- `tinyagentos/__main__.py` — docstring: `python -m tinyagentos`
- `tinyagentos/worker/agent.py` (lines: 440, 542, 596) — pairing instructions in error messages
- `tinyagentos/worker/pair.py`, `enroll.py`, `browser_main.py` — docstrings with CLI examples
- `tinyagentos/cli/worker.py`, `taosctl/__init__.py` — docstrings
- `tinyagentos/disk_quota.py` — CLI entry docstring + usage string
- `tinyagentos/services/sdcpp_server.py` — docstring
- `tinyagentos/worker/README.md` — examples
- `docs/superpowers/plans/` — 5 files with CLI examples

### 2.5 CI (GitHub Actions)

`.github/workflows/ci.yml`:
- Line 83: `uv run --no-sync python -c "from tinyagentos.app import create_app; print('OK')"`
- Line 104: `uv run --no-sync python -m compileall tinyagentos/ -q`

`.github/workflows/build-agent-images.yml`:
- 8 references — path triggers (`tinyagentos/scripts/install_hermes.sh`), image repo name (`jaylfc/tinyagentos-images`), comments about the separate images repo

**Note:** The image repo `jaylfc/tinyagentos-images` is a separate GitHub repo — its name may or may not change with the rename. That's a maintainer decision.

### 2.6 Documentation

**1,485 occurrences** in `.md`/`.rst` files (excluding README.md):

- `docs/superpowers/plans/` — heavy reference for CLI examples, paths
- `docs/` — various guides and docs
- `site/docs/mkdocs.yml` — site_url (`docs.tinyagentos.com`), repo_url, repo_name
- `tinyagentos/worker/README.md` — CLI examples

**README.md** has ~35 references covering:
- Install commands (`curl ... raw.githubusercontent.com/jaylfc/tinyagentos/master/scripts/install-server.sh`)
- Systemd unit paths and names
- File paths (`~/tinyagentos/`, `~/.local/share/tinyagentos-worker/`)
- Worker commands
- Documentation links

### 2.7 Install Scripts

**202 occurrences** in `scripts/`:

- `scripts/install-server.sh` — the main controller installer. Generates systemd units, sets up directories, runs `python -m tinyagentos`, references paths like `/opt/tinyagentos/`, `~/tinyagentos/`
- `scripts/install-worker.sh` — worker installer. References `tinyagentos-worker.service`, `~/.local/share/tinyagentos-worker/`, enrollment commands
- Various other install scripts

**High risk:** Install scripts are user-facing and stable. Changing paths/names here could break existing installations. The scripts already use `TAOS_*` env vars extensively — the transition may be to keep `TAOS_` prefix but change `tinyagentos` package references.

### 2.8 Desktop SPA

**30+ references** in `desktop/src/`:

| File | Type | Reference |
|------|------|-----------|
| `package.json` | name field | `"tinyagentos-desktop"` |
| `lib/browser-site-permissions-api.ts` | comment | `tinyagentos/routes/...` |
| `lib/userspace-apps.ts` | comment | `tinyagentos/userspace/...` |
| `apps/TextEditorApp.tsx` | localStorage key | `"tinyagentos-notes"` |
| `apps/TerminalApp.tsx` | localStorage key | `"tinyagentos.terminal.recentSsh"` |
| `components/Desktop.tsx` | localStorage key | `"tinyagentos-snippets"` |
| `components/widgets/QuickNotesWidget.tsx` | localStorage key | `"tinyagentos-quick-notes"` |
| `apps/MessagesApp.tsx` | URL | `github.com/jaylfc/tinyagentos/...` |
| Various apps | comments | Backend path references |

**Legacy localStorage keys:** The `tinyagentos-*` keys are user-facing data — changing them would lose user data unless migration is handled. These may need to stay as-is or include a migration step.

### 2.9 App Catalog

**15 references** in `app-catalog/`:

- `services/llama-cpp/manifest.yaml` — comments referencing `tinyagentos.installers.port_allocator`, `tinyagentos.cluster.capabilities`
- `plugins/image-generation-tool/manifest.yaml` — `homepage: github.com/jaylfc/tinyagentos`, `package: tinyagentos`
- `agents/openclaw/scripts/install.sh` — systemd unit generation (`tinyagentos-recycle-sweep`)
- `_common/scripts/recycle-bin-install.sh` — systemd unit generation

### 2.10 Config Files (non-pyproject)

| File | Reference |
|------|-----------|
| `docs/doc-gate.toml` | 3 path patterns: `tinyagentos/routes/*.py`, `tinyagentos/installers/*`, `tinyagentos/auth_middleware.py` |
| `site/docs/mkdocs.yml` | site_url, repo_url, repo_name |
| `app-catalog/plugins/image-generation-tool/manifest.yaml` | package field, homepage URL |

### 2.11 Templates / HTML

**~30 references** in `site/public/index.html` and `landing/index.html`:

- URL references: `tinyagentos.com`, `docs.tinyagentos.com`, `github.com/jaylfc/tinyagentos`
- Install commands: `curl ... jaylfc/tinyagentos/master/scripts/install-server.sh`
- These are the public-facing marketing site — URLs may need redirects.

### 2.12 os-build

**43 references** — board-specific build images (`os-build/userpatches/overlay/etc/systemd/system/tinyagentos.service`), paths like `/opt/tinyagentos/`.

---

## 3. Special Cases

### 3.1 Already-existing taos aliases

The `taos` and `taosctl` entry points already exist in `pyproject.toml`. The rename doesn't need to invent new CLI names — it needs to make the existing `taos` entry point primary and deprecate `tinyagentos`.

### 3.2 GitHub repo name

The repo is `github.com/jaylfc/tinyagentos`. If the repo is also renamed to `github.com/jaylfc/taos`, GitHub creates a redirect. But URLs embedded in:
- README install commands (`raw.githubusercontent.com/jaylfc/tinyagentos/master/scripts/install-server.sh`)
- Documentation links
- Desktop SPA links
- App catalog `homepage` fields

...would need updating. The `raw.githubusercontent.com` URLs are particularly critical — they're in copy-paste install commands.

### 3.3 External references (outside repo)

- **PyPI:** The package is published as `tinyagentos` on PyPI. A rename would need a new `taos` package + deprecation notice on the old one.
- **Docker Hub / GHCR:** Any container images tagged with `tinyagentos`
- **tinyagentos.com / docs.tinyagentos.com:** External domains. Would need redirects.
- **Third-party docs, blog posts, YouTube videos:** Out of scope — they'll refer to the old name indefinitely.

### 3.4 localStorage keys in Desktop SPA

These are stored in users' browsers:
- `tinyagentos-notes`
- `tinyagentos-quick-notes`
- `tinyagentos.terminal.recentSsh`
- `tinyagentos-snippets`

Migrating these requires reading the old key, writing to a new key, then deleting the old key — a one-time migration on app load.

### 3.5 doc-gate.toml

This build-time config watches specific paths:
```toml
when_changed = ["tinyagentos/routes/*.py"]
when_changed = ["tinyagentos/installers/*", "scripts/install*"]
when_changed = ["tinyagentos/auth_middleware.py"]
```
These are path triggers — must be updated to `taos/` equivalents.

---

## 4. Conflict Analysis with Open PRs

**30 open PRs** on `jaylfc/tinyagentos:dev` as of 2026-07-17:

| PR | Author | Area | Conflict Risk |
|----|--------|------|---------------|
| #1946 hognek | feat(todo): TodoApp component | Routes, desktop | HIGH — touches routes/ |
| #1945 hognek | test(user_shares): store + route tests | Tests | HIGH — any test file |
| #1944 hognek | feat(todo): TodoStore + /api/todo | Stores, routes | HIGH — new files with imports |
| #1935 hognek | docs(design): Notes/Todo split | Docs only | LOW — markdown only |
| #1934 hognek | fix(catalog): install scripts | App catalog, scripts | HIGH — install scripts |
| #1933 hognek | feat(desktop): SPA version check | Desktop | MED — desktop refs |
| #1932 hognek | feat(github): GitHub App flow | Routes | HIGH — routes |
| #1931 hognek | feat(settings): taOSmd connection test | Routes | HIGH |
| #1930 hognek | fix(notifications): archive persist | Routes, desktop | HIGH |
| #1929 hognek | feat(desktop): notification archive | Desktop | MED |
| #1928 hognek | feat(cluster): persistence + split brain | Cluster | HIGH |
| #1927 hognek | feat(update): GPG signature verify | Update | MED |
| #1926 hognek | feat(browser): mobile viewport | Browser | MED |
| #1925 hognek | fix(auth): XSS hardening | Auth | HIGH |
| #1924 hognek | feat(store): Ed25519 signing | Store | HIGH |
| #1922 jaylfc | fix(csrf): websocket regression | Auth, routes | HIGH |
| #1921 jaylfc | feat(agents): scope requests | Agents, routes | HIGH |
| #1919 jaylfc | fix(security): XSS in invite | Routes | HIGH |
| #1918 jaylfc | feat(agents): external agent invites | Agents, routes | HIGH |
| #1917 jaylfc | fix(notifications): archive persist | Routes | HIGH |
| #1912 hognek | fix(app): double-init | app.py | HIGH — touches app.py |
| #1910 hognek | feat(worker): self-update + rollback | Worker | HIGH |
| #1908 hognek | feat(user_shares): share routes | Routes | HIGH |
| #1907 hognek | feat(worker): WorkerUpdateService | Worker | HIGH |
| #1905 hognek | fix(scheduling): WAL mode | Stores | HIGH |
| #1903 hognek | feat(cluster): worker drain | Cluster | HIGH |
| #1902 hognek | feat(wallpaper): Wallhaven proxy | Routes, desktop | HIGH |
| #1895 hognek | feat(desktop): user wallpapers | Desktop, routes | HIGH |
| #1877 hognek | feat(desktop): MessagesApp extract | Desktop | MED |
| #1875 hognek | ci: ruff + npm audit | CI | LOW — CI config only |

**Finding:** The majority of open PRs (23/30) have HIGH conflict risk because they touch files
inside `tinyagentos/` that would need import/path updates. 5 have MEDIUM risk (desktop,
browser, update, and CI changes touching fewer Python files), and 2 have LOW risk
(#1935 design doc, #1875 CI config).

**Strategy:** The rename is so pervasive that it MUST be done in a coordinated "flag day" where all open PRs are either merged or rebased first. There's no practical way to do it incrementally without merge conflicts on every open branch.

---

## 5. Execution Plan Notes

### Phase 0: Merge or close all open PRs
The rename touches 1,183 files. Any open PR that touches a path being renamed
(`tinyagentos/` → `taos/`) or references `tinyagentos` in strings, imports, or
configuration will conflict. PRs that only touch completely new files or files
with no `tinyagentos` references (like CI-only changes) are lower risk — but
given the package's pervasive import footprint, most open PRs will conflict.

### Phase 1: Directory rename + imports
1. `git mv tinyagentos/ taos/`
2. Update all `from tinyagentos` → `from taos` imports (2,869 occurrences)
3. Update all `mock.patch("tinyagentos...")` strings (1,001 occurrences)
4. Update `pyproject.toml` name + entry points + package include
5. This step alone is the bulk of the work — it's mechanical but needs careful verification

### Phase 2: Non-Python references
1. systemd units (filenames + contents)
2. Install scripts (paths, CLI commands, directory names)
3. CI workflows
4. Documentation
5. Desktop SPA (package.json, localStorage keys with migration, comments)
6. App catalog

### Phase 3: External / URL references
1. README install commands
2. Landing page / site HTML
3. mkdocs.yml
4. GitHub repo rename (maintainer action)

### Phase 4: Cleanup
1. Remove legacy `tinyagentos`/`tinyagentos-worker` entry points
2. Verify no stale references with `grep -r "tinyagentos" --exclude-dir=.git --exclude="docs/audit/tinyagentos-rename-blast-radius.md"`
3. Update PyPI package (new `taos` package, deprecate `tinyagentos`)

---

## 6. Estimated Effort

| Phase | Scope | Estimated files | Risk |
|-------|-------|-----------------|------|
| 0: PR drain | Merge/close 30 PRs | N/A | Coordination |
| 1: Package rename | 587 .py files + 310 tests | ~900 | Mechanical, testable |
| 2: Non-Python refs | Systemd, scripts, CI, docs | ~150 | Some manual review |
| 3: URLs/external | README, site, mkdocs | ~20 | URL breakage |
| 4: Cleanup | Entry points, final grep | ~10 | Low |

**Total files touched: ~1,080 files** (some files have both Python and non-Python refs, hence less than the 1,183 unique count).
