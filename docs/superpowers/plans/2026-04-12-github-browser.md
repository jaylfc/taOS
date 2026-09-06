# GitHub Browser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the GitHub Browser app — OAuth/PAT/CLI auth, browse starred repos and notifications, save items to Knowledge Base, auto-monitor releases with manual overrides.

**Architecture:** `GitHubFetcher` backend (GitHub REST API v3), FastAPI routes at `/api/github/*`, `GitHubBrowserApp.tsx` frontend (list/detail with content-type-specific detail views), `lib/github.ts` API helpers.

**Tech Stack:** Python, httpx, FastAPI, React, TypeScript, Tailwind, Vitest, pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-04-12-github-browser-design.md`

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `tinyagentos/knowledge_fetchers/github.py` | GitHubFetcher — repos, issues, PRs, releases via GitHub API |
| Create | `tests/test_knowledge_fetcher_github.py` | Backend fetcher tests |
| Create | `tinyagentos/routes/github.py` | `/api/github/*` endpoints + OAuth flow |
| Create | `tests/test_routes_github.py` | Route-level tests |
| Modify | `tinyagentos/knowledge_ingest.py` | Wire github fetcher into _download() |
| Modify | `tinyagentos/app.py` | Include github router |
| Create | `desktop/src/lib/github.ts` | TypeScript types + fetch wrappers |
| Create | `desktop/tests/github.test.ts` | Frontend API helper tests |
| Create | `desktop/src/apps/GitHubBrowserApp.tsx` | Main app component |
| Modify | `desktop/src/registry/app-registry.ts` | Register github-browser entry |

---

## Task 1: GitHubFetcher Backend + Tests

**Files:**
- Create: `tinyagentos/knowledge_fetchers/github.py`
- Create: `tests/test_knowledge_fetcher_github.py`

- [ ] **Step 1: Write failing tests**

Test `fetch_repo`, `fetch_issue`, `fetch_releases`, `fetch_starred`, `parse_github_url`, `extract_metadata` with mocked httpx responses (use respx).

- [ ] **Step 2: Implement GitHubFetcher**

Key functions:
- `parse_github_url(url) -> (owner, repo, content_type, number?)` — routes URLs to correct fetcher
- `fetch_repo(owner, repo, token, http_client) -> dict` — repo metadata + README
- `fetch_issue(owner, repo, number, token, http_client) -> dict` — issue/PR body + comments
- `fetch_releases(owner, repo, token, http_client, limit=10) -> list[dict]` — releases with notes
- `fetch_starred(token, http_client, page=1) -> list[dict]` — starred repos
- `fetch_notifications(token, http_client) -> list[dict]` — unread notifications
- `extract_metadata(data, content_type) -> dict` — maps to KnowledgeItem metadata

All use `Authorization: Bearer {token}` header. Token retrieved from SecretsStore or `gh auth token` fallback.

- [ ] **Step 3: Wire into IngestPipeline**

Add `if source_type == "github"` branch — parse URL, route to correct fetcher.

- [ ] **Step 4: Run tests, commit**

```bash
git commit -m "feat(github): add GitHubFetcher with URL parser and tests"
```

---

## Task 2: GitHub API Routes + OAuth Flow

**Files:**
- Create: `tinyagentos/routes/github.py`
- Create: `tests/test_routes_github.py`
- Modify: `tinyagentos/app.py`

Endpoints:
- `GET /api/github/starred` — paginated starred repos
- `GET /api/github/notifications` — unread notifications
- `GET /api/github/repo/{owner}/{repo}` — repo + README
- `GET /api/github/repo/{owner}/{repo}/issues` — issues list
- `GET /api/github/repo/{owner}/{repo}/issues/{number}` — single issue/PR + comments
- `GET /api/github/repo/{owner}/{repo}/releases` — releases
- `GET /api/github/auth/status` — auth status (method, username)
- `GET /api/github/auth/start` — redirect to GitHub OAuth
- `GET /api/github/auth/callback` — exchange code, store token in SecretsStore

Auth detection order: SecretsStore token → `gh auth token` subprocess → unauthenticated.

- [ ] **Steps: Write tests, implement routes, wire into app.py, run tests, commit**

```bash
git commit -m "feat(github): add API routes with OAuth flow"
```

---

## Task 3: Frontend API Helpers + Tests

**Files:**
- Create: `desktop/src/lib/github.ts`
- Create: `desktop/tests/github.test.ts`

Types: `GitHubRepo`, `GitHubIssue`, `GitHubComment`, `GitHubRelease`, `GitHubAuthStatus`
Functions: `fetchStarred`, `fetchNotifications`, `fetchRepo`, `fetchIssues`, `fetchIssue`, `fetchReleases`, `getAuthStatus`, `saveToLibrary`

- [ ] **Steps: Write tests, implement, run tests, commit**

```bash
git commit -m "feat(github): add frontend API types and helpers with tests"
```

---

## Task 4: GitHubBrowserApp Component + Registration

**Files:**
- Create: `desktop/src/apps/GitHubBrowserApp.tsx`
- Modify: `desktop/src/registry/app-registry.ts`

Two-state layout (list/detail).

**List view:** Sidebar (Starred, Notifications with unread badge, Watched, content type filter: Repos/Issues/PRs/Releases, Categories, Status). Cards vary by type — repos show stars+language, issues show state badge+labels, releases show tag+date.

**Detail view varies by content type:**
- **Repo:** README rendered, latest release, top issues, "Monitor releases" toggle
- **Issue/PR:** Body + threaded comment tree (collapsible, same as Reddit). PR shows files changed count. Tabs: Discussion, History, Metadata
- **Release:** Release notes, assets list

Action bar on all: Open on GitHub, Save to Library, Monitor (with override controls for content type, frequency, pin), Delete.

Auth banner if not authenticated: "Connect GitHub" button → OAuth flow or link to Secrets app for PAT.

Registration: `id: "github-browser"`, `icon: "github"`, `launchpadOrder: 15`

- [ ] **Steps: Create component, register, build check, test, commit**

```bash
git commit -m "feat(github): add GitHub Browser app with full UI"
```

---

## Task 5: Manual Testing

- [ ] Verify auth detection (unauthenticated, PAT, OAuth)
- [ ] Browse starred repos
- [ ] Click repo → detail with README
- [ ] Save repo, verify monitoring starts for releases
- [ ] Browse issues, click one → threaded comments
- [ ] Test notification list
- [ ] Test mobile layout and ARIA
