# GitHub Browser

## Overview

A GitHub browsing and monitoring app for taOS. Browse starred repos, notifications, and specific items (repos, issues, PRs, releases). Save items to the Knowledge Base and monitor for updates. Releases are prioritised for monitoring — starring a repo auto-watches for new releases. Manual overrides for monitoring content, frequency, and scope.

Build order: #6 in the Knowledge Capture Pipeline.

---

## Architecture

```
desktop/src/
├── apps/GitHubBrowserApp.tsx       # Main app component
└── lib/github.ts                   # Typed fetch wrappers for /api/github/*

tinyagentos/
├── knowledge_fetchers/github.py    # GitHubFetcher: repos, issues, PRs, releases
└── routes/github.py                # /api/github/* endpoints
```

Registered in `app-registry.ts` as:
- `id: "github-browser"`
- `name: "GitHub"`
- `icon: "github"`
- `category: "platform"`
- `launchpadOrder: 15`
- `singleton: true`
- `pinned: false`
- `defaultSize: { w: 1000, h: 650 }`
- `minSize: { w: 550, h: 400 }`

---

## Auth

Three methods, managed centrally in the Secrets app:

### OAuth Flow (primary)

"Connect GitHub" button in the GitHub Browser app or Secrets app. Standard OAuth web flow — popup/redirect to github.com, user approves scopes (`repo`, `read:user`, `notifications`), token returned to taOS. Stored in SecretsStore. This is the primary path for users who access taOS via browser without CLI access.

### Personal Access Token (manual)

In Secrets app: "Add GitHub" → shows instructions for creating a PAT at github.com/settings/tokens with required scopes listed. User pastes token. Stored in SecretsStore.

### `gh` CLI Auto-detect (bonus)

If `gh auth status` shows authenticated on the host, use that token automatically. Zero setup for CLI users who SSH into the machine. Detected at startup, token extracted via `gh auth token`.

---

## Layout

Same two-state pattern: `list` (default) and `detail`.

### List View

Sidebar filters:
- Starred Repos
- Notifications (unread count badge)
- Watched (items being monitored)
- Content type: Repos, Issues, PRs, Releases
- Categories (from Knowledge Base)
- Status

Item cards vary by content type:
- **Repo:** name, owner, description, star count, language badge, last updated
- **Issue/PR:** title, repo name, state (open/closed/merged) with colour, labels, comment count, author
- **Release:** tag name, repo name, date, asset count, pre-release badge

All cards: category pills, monitoring badge, "Save to Library" button.

### Detail View

Full replacement with back button. Content varies by type:

**Repo detail:**
- Header: owner/name, description, star count, fork count, language, license
- README rendered (markdown)
- Latest release card (tag, date, release notes preview)
- Top open issues list (title, labels, comment count)
- "Monitor releases" toggle (default on when saved)
- Action bar: Open on GitHub, Save to Library, Monitor, Delete

**Issue/PR detail:**
- Header: title, state badge, repo name, author, created date, labels
- Body (markdown rendered)
- Comment thread (threaded, collapsible at 3 levels, same pattern as Reddit Client)
- For PRs: files changed count, additions/deletions stats
- Tabs: Discussion (default), History (monitoring diffs), Metadata (labels, assignees, milestones, timeline events)
- Action bar: Open on GitHub, Save to Library, Monitor, Delete

**Release detail:**
- Header: tag, repo name, date, author
- Release notes (markdown rendered)
- Assets list (name, size, download count)
- Action bar: Open on GitHub, Save to Library, Delete

---

## Monitoring

### Release Monitoring (default)

When a repo is saved to the Knowledge Base, it auto-monitors for new releases at 6h base frequency. New releases trigger a Knowledge Base notification and create a new KnowledgeItem.

### Issue/PR Monitoring (explicit)

Only monitored when explicitly saved. Tracks: new comments, status changes (opened→closed→merged), label changes, assignee changes. Snapshots capture state at each poll for diff view.

### Manual Overrides

Per-item controls:
- What to monitor: releases only, issues, PRs, comments, all activity
- Poll frequency: custom interval or use defaults
- Pin: override decay, always poll at set frequency
- Stop: manual disable only (30-day floor from Monitor Service applies)

All monitoring tasks visible in the Routines app (#193).

---

## Backend: GitHubFetcher

File: `tinyagentos/knowledge_fetchers/github.py`

Uses GitHub REST API v3 with token from SecretsStore.

### Functions

- `fetch_repo(owner, repo, token, http_client) -> dict` — repo metadata + README content
- `fetch_issue(owner, repo, number, token, http_client) -> dict` — issue/PR body + comment thread
- `fetch_releases(owner, repo, token, http_client, limit=10) -> list[dict]` — recent releases with notes
- `fetch_starred(token, http_client, page=1) -> list[dict]` — user's starred repos
- `fetch_notifications(token, http_client) -> list[dict]` — unread notifications
- `extract_metadata(data, content_type) -> dict` — maps to KnowledgeItem metadata

### URL Parser

`parse_github_url(url) -> (owner, repo, content_type, number?)` — routes `github.com/owner/repo` to repo fetcher, `github.com/owner/repo/issues/123` to issue fetcher, `github.com/owner/repo/releases` to releases fetcher, etc.

### IngestPipeline Wire-In

```python
if source_type == "github":
    from tinyagentos.knowledge_fetchers.github import parse_github_url, fetch_repo, fetch_issue, fetch_releases
    owner, repo, content_type, number = parse_github_url(url)
    # Route to appropriate fetcher based on content_type
```

### Snapshot Diffs

For monitored issues/PRs: `snapshot_diff` compares comment count, state, labels between polls. New comments captured with full text. State transitions shown in History tab.

---

## Backend: GitHub API Routes

File: `tinyagentos/routes/github.py`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/github/starred` | User's starred repos (paginated) |
| GET | `/api/github/notifications` | Unread notifications |
| GET | `/api/github/repo/{owner}/{repo}` | Repo metadata + README |
| GET | `/api/github/repo/{owner}/{repo}/issues` | Issues list (paginated, filterable) |
| GET | `/api/github/repo/{owner}/{repo}/issues/{number}` | Single issue/PR + comments |
| GET | `/api/github/repo/{owner}/{repo}/releases` | Releases list |
| GET | `/api/github/auth/status` | Auth status (method, username, scopes) |
| GET | `/api/github/auth/start` | Redirect to GitHub OAuth |
| GET | `/api/github/auth/callback` | Exchange code, store token |

---

## Frontend: lib/github.ts

Types:
```typescript
GitHubRepo { owner: string; name: string; description: string; stars: number; forks: number; language: string; license: string; updated_at: string; topics: string[] }
GitHubIssue { number: number; title: string; state: string; author: string; body: string; labels: string[]; comments: GitHubComment[]; created_at: string; repo: string }
GitHubComment { author: string; body: string; created_at: string; reactions: Record<string, number> }
GitHubRelease { tag: string; name: string; body: string; author: string; published_at: string; assets: { name: string; size: number; download_count: number }[]; prerelease: boolean }
GitHubAuthStatus { authenticated: boolean; username?: string; method?: string }
```

Functions:
```typescript
fetchStarred(page?: number): Promise<{ repos: GitHubRepo[]; hasMore: boolean }>
fetchNotifications(): Promise<GitHubNotification[]>
fetchRepo(owner: string, repo: string): Promise<GitHubRepo | null>
fetchIssues(owner: string, repo: string, state?: string, page?: number): Promise<{ issues: GitHubIssue[]; hasMore: boolean }>
fetchIssue(owner: string, repo: string, number: number): Promise<GitHubIssue | null>
fetchReleases(owner: string, repo: string): Promise<GitHubRelease[]>
getAuthStatus(): Promise<GitHubAuthStatus>
saveToLibrary(url: string): Promise<{ id: string; status: string } | null>
```

---

## Non-Goals

- Code browsing / file tree navigation (future, evolving toward full client)
- Code review / PR approval / commenting (read-only)
- Repository creation or management
- GitHub Actions / CI status (future)
- Gist support
