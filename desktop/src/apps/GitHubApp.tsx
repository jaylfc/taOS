import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import {
  Github,
  Star,
  GitFork,
  Bell,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  Search,
  Tag,
  MessageSquare,
  Download,
  BookMarked,
  Eye,
  AlertCircle,
  GitPullRequest,
  CircleDot,
  Package,
} from "lucide-react";
import { Switch, Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui";
import {
  fetchStarred,
  fetchNotifications,
  fetchRepo,
  fetchIssue,
  fetchReleases,
  getAuthStatus,
  saveToLibrary,
} from "@/lib/github";
import type {
  GitHubRepo,
  GitHubIssue,
  GitHubRelease,
  GitHubComment,
  GitHubAuthStatus,
} from "@/lib/github";
import { MobileSplitView } from "@/components/mobile/MobileSplitView";
import { useIsMobile } from "@/hooks/use-is-mobile";
import styles from "./GitHubApp.module.css";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type View = "list" | "detail";
type SidebarSection = "starred" | "notifications" | "watched";
type ContentType = "repos" | "issues" | "prs" | "releases";

interface DetailTarget {
  type: "repo" | "issue" | "release";
  repo?: GitHubRepo;
  issue?: GitHubIssue;
  release?: GitHubRelease;
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const formatDate = (iso: string): string => {
  if (!iso) return "";
  const d = new Date(iso);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return d.toLocaleDateString();
};

const formatBytes = (bytes: number): string => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
};

const stateClass = (state: string) => {
  if (state === "open") return styles.stateOpen;
  if (state === "merged") return styles.stateMerged;
  if (state === "closed") return styles.stateClosed;
  return styles.stateMerged;
};

/* Deterministic, low-saturation cover tint derived from the repo's
   language string. Content colouring (like the Projects card covers),
   not theme chrome — keeps the language-tinted DNA from the mock while
   working for any language without a hardcoded per-language palette. */
const coverBackground = (seed: string): string => {
  let h = 0;
  for (let i = 0; i < seed.length; i += 1) {
    h = (h * 31 + seed.charCodeAt(i)) % 360;
  }
  const hue = ((h % 360) + 360) % 360;
  return `radial-gradient(120% 120% at 30% 20%, hsl(${hue} 34% 26%), transparent 60%), linear-gradient(140deg, hsl(${hue} 28% 16%), hsl(${(hue + 24) % 360} 26% 11%))`;
};

/* ------------------------------------------------------------------ */
/*  CommentNode (recursive, collapsible at 3 levels)                  */
/* ------------------------------------------------------------------ */

function CommentNode({ comment, depth = 0 }: { comment: GitHubComment; depth?: number }) {
  const [collapsed, setCollapsed] = useState(depth >= 3);

  return (
    <div
      className={`${styles.comment} ${depth > 0 ? styles.commentNested : ""}`}
      style={{ marginLeft: depth > 0 ? `${depth * 12}px` : 0 }}
    >
      <div className={styles.commentHead}>
        <span className={styles.commentAuthor}>{comment.author}</span>
        <span className={styles.commentTime}>{formatDate(comment.created_at)}</span>
        {depth >= 3 && (
          <button
            className={styles.commentToggle}
            onClick={() => setCollapsed((v) => !v)}
            aria-expanded={!collapsed}
            aria-label={collapsed ? "Expand comment" : "Collapse comment"}
          >
            {collapsed ? "expand" : "collapse"}
          </button>
        )}
      </div>
      {!collapsed && (
        <>
          <p className={styles.commentBody}>{comment.body}</p>
          {Object.keys(comment.reactions ?? {}).length > 0 && (
            <div className={styles.reacts}>
              {Object.entries(comment.reactions).map(([emoji, count]) =>
                count > 0 ? (
                  <span key={emoji} className={styles.react} aria-label={`${emoji}: ${count}`}>
                    {emoji} {count}
                  </span>
                ) : null,
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  GitHubApp                                                          */
/* ------------------------------------------------------------------ */

export function GitHubApp({ windowId: _windowId }: { windowId: string }) {
  /* ---------- view state ---------- */
  // view is kept for legacy section-switch effect below; navigation is now driven by selectedId/MobileSplitView
  const [, setView] = useState<View>("list");
  const [detail, setDetail] = useState<DetailTarget | null>(null);

  /* root container ref — scopes the Escape-to-back handler to this app's
     own subtree so it never fires while another window/app is focused */
  const rootRef = useRef<HTMLDivElement>(null);

  /* ---------- sidebar state ---------- */
  const [activeSection, setActiveSection] = useState<SidebarSection>("starred");
  const [contentType, setContentType] = useState<ContentType>("repos");
  const [filterStatus, setFilterStatus] = useState<string | null>(null);

  /* ---------- list state ---------- */
  const [starredRepos, setStarredRepos] = useState<GitHubRepo[]>([]);
  const [notifications, setNotifications] = useState<GitHubIssue[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [watched] = useState<GitHubRepo[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");

  /* ---------- detail state ---------- */
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailReleases, setDetailReleases] = useState<GitHubRelease[]>([]);
  const [monitorEnabled, setMonitorEnabled] = useState(false);
  const [savingToLib, setSavingToLib] = useState(false);
  const [savedToLib, setSavedToLib] = useState(false);

  /* ---------- auth state ---------- */
  const [authStatus, setAuthStatus] = useState<GitHubAuthStatus>({ authenticated: false });

  /* ---------- mobile ---------- */
  const isMobile = useIsMobile();

  /* ---------------------------------------------------------------- */
  /*  Initial data loading                                             */
  /* ---------------------------------------------------------------- */

  const loadAuth = useCallback(async () => {
    const status = await getAuthStatus();
    setAuthStatus(status);
  }, []);

  const loadStarred = useCallback(async () => {
    setLoading(true);
    const result = await fetchStarred();
    setStarredRepos(result.repos);
    setLoading(false);
  }, []);

  const loadNotifications = useCallback(async () => {
    setLoading(true);
    const result = await fetchNotifications();
    setNotifications(result.notifications);
    setUnreadCount(result.unread_count);
    setLoading(false);
  }, []);

  useEffect(() => {
    loadAuth();
    loadStarred();
    loadNotifications();
  }, [loadAuth, loadStarred, loadNotifications]);

  /* ---------------------------------------------------------------- */
  /*  Section switching                                                */
  /* ---------------------------------------------------------------- */

  useEffect(() => {
    setView("list");
    setDetail(null);
    setSearch("");
    if (activeSection === "starred" || activeSection === "watched") {
      loadStarred();
    } else if (activeSection === "notifications") {
      loadNotifications();
    }
  }, [activeSection, loadStarred, loadNotifications]);

  /* ---------------------------------------------------------------- */
  /*  Open detail                                                      */
  /* ---------------------------------------------------------------- */

  const openRepoDetail = useCallback(async (repo: GitHubRepo) => {
    setView("detail");
    setDetail({ type: "repo", repo });
    setSavedToLib(false);
    setMonitorEnabled(false);
    setDetailLoading(true);
    const [releases, full] = await Promise.all([
      fetchReleases(repo.owner, repo.name),
      fetchRepo(repo.owner, repo.name),
    ]);
    setDetailReleases(releases);
    if (full) {
      setDetail({ type: "repo", repo: full });
    }
    setDetailLoading(false);
  }, []);

  const openIssueDetail = useCallback(async (issue: GitHubIssue) => {
    setView("detail");
    setDetail({ type: "issue", issue });
    setSavedToLib(false);
    setDetailLoading(true);
    const [owner, repoName] = issue.repo.split("/");
    if (owner && repoName) {
      const full = await fetchIssue(owner, repoName, issue.number);
      if (full) setDetail({ type: "issue", issue: full });
    }
    setDetailLoading(false);
  }, []);

  const openReleaseDetail = useCallback((release: GitHubRelease, repoFullName: string) => {
    setView("detail");
    setDetail({ type: "release", release: { ...release, repo: repoFullName } as GitHubRelease & { repo: string } });
    setSavedToLib(false);
  }, []);

  const goBack = useCallback(() => {
    setView("list");
    setDetail(null);
    setDetailReleases([]);
  }, []);

  /* Escape returns to the list from anywhere in an open detail view.
     Skips list view (no detail open) and editable targets (inputs,
     textareas, contentEditable) so it never hijacks closing a field.
     Scoped to this app's own DOM subtree: taOS is a multi-window desktop,
     so the handler must bail when focus is in another window/app. */
  useEffect(() => {
    if (!detail) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const root = rootRef.current;
      if (!root) return;
      const target = e.target as HTMLElement | null;
      // Only act when focus / the event originates within the GitHub app.
      const insideApp =
        (target ? root.contains(target) : false) || root.contains(document.activeElement);
      if (!insideApp) return;
      if (target) {
        const tag = target.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA" || target.isContentEditable) return;
      }
      goBack();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [detail, goBack]);

  /* ---------- selectedId for MobileSplitView ---------- */
  const selectedId = useMemo((): string | null => {
    if (!detail) return null;
    if (detail.type === "repo" && detail.repo) return `repo:${detail.repo.owner}/${detail.repo.name}`;
    if (detail.type === "issue" && detail.issue) return `issue:${detail.issue.repo}#${detail.issue.number}`;
    if (detail.type === "release" && detail.release) return `release:${detail.release.tag}`;
    return null;
  }, [detail]);

  /* ---------------------------------------------------------------- */
  /*  Save to library                                                  */
  /* ---------------------------------------------------------------- */

  const handleSaveToLibrary = useCallback(async (url: string) => {
    setSavingToLib(true);
    const result = await saveToLibrary(url);
    setSavingToLib(false);
    if (result) setSavedToLib(true);
  }, []);

  /* ---------------------------------------------------------------- */
  /*  Filtered list items                                              */
  /* ---------------------------------------------------------------- */

  const activeItems = useMemo(() => {
    if (activeSection === "starred" || activeSection === "watched") {
      const repos = activeSection === "watched" ? watched : starredRepos;
      return repos.filter((r) => {
        if (!search) return true;
        const q = search.toLowerCase();
        return (
          r.name.toLowerCase().includes(q) ||
          r.owner.toLowerCase().includes(q) ||
          r.description?.toLowerCase().includes(q)
        );
      });
    }
    if (activeSection === "notifications") {
      return notifications.filter((n) => {
        if (!search) return true;
        const q = search.toLowerCase();
        return n.title.toLowerCase().includes(q) || n.repo.toLowerCase().includes(q);
      });
    }
    return [];
  }, [activeSection, starredRepos, watched, notifications, search]);

  const listHeading =
    activeSection === "notifications" ? "Notifications" : activeSection === "watched" ? "Watched" : "Starred";

  /* ---------------------------------------------------------------- */
  /*  Sidebar UI                                                       */
  /* ---------------------------------------------------------------- */

  const sidebarUI = (
    <nav className={styles.rail} aria-label="GitHub Browser navigation">
      <div className={styles.railScroll}>
        <div className={styles.brand}>
          <Github size={18} aria-hidden="true" />
          <b>GitHub</b>
        </div>

        {/* --- Sources --- */}
        <section aria-label="Sources">
          <p className={styles.cap}>Sources</p>
          <div className={styles.navGroup}>
            <button
              type="button"
              aria-pressed={activeSection === "starred"}
              onClick={() => setActiveSection("starred")}
              className={`${styles.navItem} ${activeSection === "starred" ? styles.active : ""}`}
            >
              <Star size={14} aria-hidden="true" />
              Starred
            </button>
            <button
              type="button"
              aria-pressed={activeSection === "notifications"}
              onClick={() => setActiveSection("notifications")}
              className={`${styles.navItem} ${activeSection === "notifications" ? styles.active : ""}`}
            >
              <Bell size={14} aria-hidden="true" />
              Notifications
              {unreadCount > 0 && (
                <span className={styles.count} aria-label={`${unreadCount} unread`}>
                  {unreadCount}
                </span>
              )}
            </button>
            <button
              type="button"
              aria-pressed={activeSection === "watched"}
              onClick={() => setActiveSection("watched")}
              className={`${styles.navItem} ${activeSection === "watched" ? styles.active : ""}`}
            >
              <Eye size={14} aria-hidden="true" />
              Watched
            </button>
          </div>
        </section>

        {/* --- Content Type --- */}
        <section aria-label="Content type">
          <p className={styles.cap}>Content</p>
          <div className={styles.navGroup}>
            {(
              [
                { id: "repos" as ContentType, label: "Repos", icon: Github },
                { id: "issues" as ContentType, label: "Issues", icon: CircleDot },
                { id: "prs" as ContentType, label: "Pull Requests", icon: GitPullRequest },
                { id: "releases" as ContentType, label: "Releases", icon: Package },
              ] as const
            ).map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                type="button"
                aria-pressed={contentType === id}
                onClick={() => setContentType(id)}
                className={`${styles.navItem} ${contentType === id ? styles.active : ""}`}
              >
                <Icon size={14} aria-hidden="true" />
                {label}
              </button>
            ))}
          </div>
        </section>

        {/* --- Status --- */}
        <section aria-label="Status filter">
          <p className={styles.cap}>Status</p>
          <div className={styles.pillrow}>
            {(["open", "closed", "merged"] as const).map((s) => {
              const active = filterStatus === s;
              return (
                <button
                  key={s}
                  type="button"
                  aria-pressed={active}
                  onClick={() => setFilterStatus((prev) => (prev === s ? null : s))}
                  className={`${styles.filterpill} ${active ? styles.on : ""}`}
                >
                  {s}
                </button>
              );
            })}
          </div>
        </section>
      </div>

      {/* Auth status at bottom */}
      <div className={styles.acct}>
        {authStatus.authenticated ? (
          <>
            <p className={styles.acctWho}>@{authStatus.username}</p>
            <p className={styles.acctMeth}>{authStatus.method ?? "connected"}</p>
          </>
        ) : (
          <button
            type="button"
            className={styles.connect}
            onClick={() => {
              /* links to Secrets app — no-op in UI, user can navigate manually */
            }}
            aria-label="Connect GitHub account"
          >
            Connect GitHub
          </button>
        )}
      </div>
    </nav>
  );

  /* ---------------------------------------------------------------- */
  /*  Auth Banner                                                      */
  /* ---------------------------------------------------------------- */

  const authBanner = !authStatus.authenticated ? (
    <div className={styles.authbanner} role="banner" aria-label="GitHub authentication notice">
      <AlertCircle size={14} aria-hidden="true" />
      <span>Connect GitHub for starred repos and notifications.</span>
      <button type="button" className={styles.authbannerLink} aria-label="Open Secrets app to connect GitHub">
        Connect
      </button>
    </div>
  ) : null;

  /* ---------------------------------------------------------------- */
  /*  Repo card                                                        */
  /* ---------------------------------------------------------------- */

  const repoCard = (repo: GitHubRepo) => {
    const isSel = selectedId === `repo:${repo.owner}/${repo.name}`;
    return (
      <button
        type="button"
        key={`${repo.owner}/${repo.name}`}
        className={`${styles.repocard} ${isSel ? styles.sel : ""}`}
        onClick={() => openRepoDetail(repo)}
        aria-label={`Open ${repo.owner}/${repo.name}`}
      >
        <div className={styles.cover} style={{ background: coverBackground(repo.language || repo.name) }}>
          {repo.language && <span className={styles.coverLang}>{repo.language}</span>}
        </div>
        <div className={styles.cardBody}>
          <div className={styles.repoTitle}>
            <span className={styles.own}>{repo.owner}/</span>
            {repo.name}
          </div>
          {repo.description && <p className={styles.repoDesc}>{repo.description}</p>}
          <div className={styles.meta}>
            <span className={styles.metaItem} aria-label={`${repo.stars} stars`}>
              <Star size={11} aria-hidden="true" />
              {repo.stars.toLocaleString()}
            </span>
            <span className={styles.metaItem} aria-label={`${repo.forks} forks`}>
              <GitFork size={11} aria-hidden="true" />
              {repo.forks.toLocaleString()}
            </span>
            <span className={styles.metaAgo}>{formatDate(repo.updated_at)}</span>
          </div>
          {repo.topics.length > 0 && (
            <div className={styles.topics} aria-label="Topics">
              {repo.topics.slice(0, 4).map((t) => (
                <span key={t} className={styles.topic}>
                  {t}
                </span>
              ))}
            </div>
          )}
        </div>
      </button>
    );
  };

  /* ---------------------------------------------------------------- */
  /*  Issue card                                                       */
  /* ---------------------------------------------------------------- */

  const issueCard = (issue: GitHubIssue) => (
    <button
      type="button"
      key={`${issue.repo}#${issue.number}`}
      className={styles.issuecard}
      onClick={() => openIssueDetail(issue)}
      aria-label={`Open ${issue.is_pull_request ? "PR" : "issue"}: ${issue.title}`}
    >
      <div className={styles.issueTop}>
        {issue.is_pull_request ? (
          <GitPullRequest size={14} className={`${styles.gi} ${styles.giPr}`} aria-hidden="true" />
        ) : (
          <CircleDot size={14} className={`${styles.gi} ${styles.giOpen}`} aria-hidden="true" />
        )}
        <div className={styles.issueTitle}>{issue.title}</div>
        <span className={`${styles.state} ${stateClass(issue.state)}`} aria-label={`Status: ${issue.state}`}>
          {issue.state}
        </span>
      </div>
      <div className={styles.issueRepo}>
        {issue.repo} · #{issue.number}
      </div>
      {issue.labels.length > 0 && (
        <div className={styles.labels} aria-label="Labels">
          {issue.labels.map((label) => (
            <span key={label} className={styles.lbl}>
              {label}
            </span>
          ))}
        </div>
      )}
      <div className={styles.issueFoot}>
        <span className={styles.issueFootItem}>
          <MessageSquare size={11} aria-hidden="true" />
          {issue.comments.length}
        </span>
        <span>{issue.author}</span>
        <span className={styles.issueFootAgo}>{formatDate(issue.created_at)}</span>
      </div>
    </button>
  );

  /* ---------------------------------------------------------------- */
  /*  Release card                                                     */
  /* ---------------------------------------------------------------- */

  const releaseCard = (release: GitHubRelease, repoFullName = "") => (
    <button
      type="button"
      key={release.tag}
      className={styles.releasecard}
      onClick={() => openReleaseDetail(release, repoFullName)}
      aria-label={`Open release ${release.tag}`}
    >
      <div className={styles.releaseTop}>
        <div>
          <div className={styles.releaseTag}>
            <Tag size={13} aria-hidden="true" />
            {release.tag}
          </div>
          {repoFullName && <div className={styles.releaseRepo}>{repoFullName}</div>}
        </div>
        {release.prerelease && <span className={styles.pre}>pre-release</span>}
      </div>
      <div className={styles.releaseMeta}>
        {release.assets.length > 0
          ? `${formatDate(release.published_at)} · ${release.assets.length} assets`
          : formatDate(release.published_at)}
      </div>
    </button>
  );

  /* ---------------------------------------------------------------- */
  /*  List View                                                        */
  /* ---------------------------------------------------------------- */

  const listViewUI = (
    <main className={styles.listcol} aria-label="GitHub content list">
      <div className={styles.listhead}>
        <h2>{listHeading}</h2>
        <span className={styles.listN}>
          {activeSection === "notifications" && unreadCount > 0 ? `${unreadCount} unread` : activeItems.length}
        </span>
        <div className={styles.search}>
          <Search size={13} aria-hidden="true" />
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search…"
            aria-label="Search GitHub content"
          />
        </div>
      </div>

      <div className={styles.list} role="list" aria-label="GitHub items">
        {loading ? (
          <>
            <div className={`${styles.skel} taos-shimmer`} aria-hidden="true" />
            <div className={`${styles.skel} taos-shimmer`} aria-hidden="true" />
            <div className={`${styles.skel} taos-shimmer`} aria-hidden="true" />
            <span className="sr-only" role="status" aria-live="polite">
              Loading…
            </span>
          </>
        ) : activeItems.length === 0 ? (
          <div className={styles.empty}>
            <Github size={36} aria-hidden="true" />
            <p>{search ? "No results for your search" : "Nothing here yet"}</p>
          </div>
        ) : activeSection === "notifications" ? (
          (activeItems as GitHubIssue[]).map((item) => (
            <div key={`${item.repo}#${item.number}`} role="listitem">
              {issueCard(item)}
            </div>
          ))
        ) : (
          (activeItems as GitHubRepo[]).map((item) => (
            <div key={`${item.owner}/${item.name}`} role="listitem">
              {repoCard(item)}
            </div>
          ))
        )}
      </div>
    </main>
  );

  /* ---------------------------------------------------------------- */
  /*  Repo Detail View                                                 */
  /* ---------------------------------------------------------------- */

  const repoDetailUI = (repo: GitHubRepo) => {
    const repoUrl = `https://github.com/${repo.owner}/${repo.name}`;
    const latestRelease = detailReleases[0] ?? null;

    return (
      <main className={styles.detail} aria-label={`${repo.owner}/${repo.name} detail`}>
        <div className={styles.detailScroll}>
          <div className={styles.dhead}>
            {/* Hide back button on mobile — MobileSplitView nav bar handles back */}
            {!isMobile && (
              <button
                type="button"
                className={styles.back}
                onClick={goBack}
                aria-label="Back to list"
              >
                <ChevronLeft size={14} aria-hidden="true" />
                Back
              </button>
            )}

            <h2 className={styles.dtitle}>
              <span className={styles.own}>{repo.owner}/</span>
              {repo.name}
            </h2>
            {repo.description && <p className={styles.ddesc}>{repo.description}</p>}

            <div className={styles.badges}>
              <span className={styles.badge} aria-label={`${repo.stars} stars`}>
                <Star size={11} aria-hidden="true" />
                {repo.stars.toLocaleString()} stars
              </span>
              <span className={styles.badge} aria-label={`${repo.forks} forks`}>
                <GitFork size={11} aria-hidden="true" />
                {repo.forks.toLocaleString()} forks
              </span>
              {repo.language && <span className={`${styles.badge} ${styles.badgeLang}`}>{repo.language}</span>}
              {repo.license && <span className={styles.badge}>{repo.license}</span>}
            </div>

            {repo.topics.length > 0 && (
              <div className={styles.topics} aria-label="Topics">
                {repo.topics.map((t) => (
                  <span key={t} className={styles.topic}>
                    {t}
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* README */}
          {repo.readme_content && (
            <div className={styles.section}>
              <p className={styles.sectionCap}>Readme</p>
              <div className={styles.readme}>
                <pre>{detailLoading ? "Loading…" : repo.readme_content}</pre>
              </div>
            </div>
          )}

          {/* Latest release */}
          {latestRelease && (
            <div className={styles.section}>
              <p className={styles.sectionCap}>Latest Release</p>
              {releaseCard(latestRelease, `${repo.owner}/${repo.name}`)}
            </div>
          )}

          {/* Monitor toggle */}
          <div className={styles.toggleRow}>
            <label htmlFor={`monitor-${repo.name}`}>Monitor releases</label>
            <Switch
              id={`monitor-${repo.name}`}
              checked={monitorEnabled}
              onCheckedChange={setMonitorEnabled}
              aria-label="Monitor releases for this repository"
            />
          </div>

          {/* Action bar */}
          <div className={styles.actions}>
            <button
              type="button"
              className={`${styles.btn} ${styles.btnGhost}`}
              onClick={() => window.open(repoUrl, "_blank", "noopener,noreferrer")}
              aria-label="Open on GitHub"
            >
              <ExternalLink size={13} aria-hidden="true" />
              Open on GitHub
            </button>
            <button
              type="button"
              className={`${styles.btn} ${savedToLib ? "" : styles.btnPrimary}`}
              onClick={() => handleSaveToLibrary(repoUrl)}
              disabled={savingToLib || savedToLib}
              aria-label={savedToLib ? "Saved to library" : "Save to Library"}
            >
              <BookMarked size={13} aria-hidden="true" />
              {savedToLib ? "Saved" : savingToLib ? "Saving…" : "Save to Library"}
            </button>
          </div>
        </div>
      </main>
    );
  };

  /* ---------------------------------------------------------------- */
  /*  Issue Detail View                                                */
  /* ---------------------------------------------------------------- */

  const issueDetailUI = (issue: GitHubIssue) => {
    const issueUrl = `https://github.com/${issue.repo}/${issue.is_pull_request ? "pull" : "issues"}/${issue.number}`;

    return (
      <main className={styles.detail} aria-label={`Issue ${issue.number} detail`}>
        <div className={styles.detailScroll}>
          <div className={styles.dhead}>
            {/* Hide back button on mobile — MobileSplitView nav bar handles back */}
            {!isMobile && (
              <button
                type="button"
                className={styles.back}
                onClick={goBack}
                aria-label="Back to list"
              >
                <ChevronLeft size={14} aria-hidden="true" />
                Back
              </button>
            )}

            <div className={styles.dtitleRow}>
              {issue.is_pull_request ? (
                <GitPullRequest size={16} className={`${styles.gi} ${styles.giPr}`} aria-hidden="true" />
              ) : (
                <CircleDot size={16} className={`${styles.gi} ${styles.giOpen}`} aria-hidden="true" />
              )}
              <h2 className={styles.dtitle}>{issue.title}</h2>
              <span className={`${styles.state} ${stateClass(issue.state)}`} aria-label={`Status: ${issue.state}`}>
                {issue.state}
              </span>
            </div>

            <p className={styles.dmeta}>
              {issue.repo} · {issue.author} · #{issue.number} · {formatDate(issue.created_at)}
            </p>

            {issue.labels.length > 0 && (
              <div className={styles.labels} aria-label="Labels">
                {issue.labels.map((label) => (
                  <span key={label} className={styles.lbl}>
                    {label}
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* Tabs */}
          <div className={styles.section}>
            <Tabs defaultValue="discussion">
              <TabsList className={styles.tabsList}>
                <TabsTrigger value="discussion" className={styles.tabsTrigger}>
                  Discussion
                </TabsTrigger>
                <TabsTrigger value="history" className={styles.tabsTrigger}>
                  History
                </TabsTrigger>
                <TabsTrigger value="metadata" className={styles.tabsTrigger}>
                  Metadata
                </TabsTrigger>
              </TabsList>

              {/* Discussion tab */}
              <TabsContent value="discussion">
                {issue.body && (
                  <div className={styles.readme} style={{ maxHeight: "none", marginBottom: issue.comments.length > 0 ? 14 : 0 }}>
                    <pre className={styles.issueBody}>{detailLoading ? "Loading…" : issue.body}</pre>
                  </div>
                )}
                {issue.comments.length > 0 && (
                  <div aria-label="Comments">
                    <p className={styles.sectionCap}>
                      {issue.comments.length} comment{issue.comments.length !== 1 ? "s" : ""}
                    </p>
                    {issue.comments.map((comment, idx) => (
                      <CommentNode key={idx} comment={comment} depth={0} />
                    ))}
                  </div>
                )}
                {!issue.body && issue.comments.length === 0 && (
                  <p className={styles.tabsEmpty}>No description or comments.</p>
                )}
              </TabsContent>

              {/* History tab */}
              <TabsContent value="history">
                <p className={styles.tabsEmpty}>Issue history not available in this view.</p>
              </TabsContent>

              {/* Metadata tab */}
              <TabsContent value="metadata">
                <div className={styles.metaList}>
                  {[
                    { label: "Number", value: `#${issue.number}` },
                    { label: "State", value: issue.state },
                    { label: "Author", value: issue.author },
                    { label: "Repo", value: issue.repo },
                    { label: "Type", value: issue.is_pull_request ? "Pull Request" : "Issue" },
                    { label: "Created", value: formatDate(issue.created_at) },
                  ].map(({ label, value }) => (
                    <div key={label} className={styles.metaRow}>
                      <span className={styles.metaKey}>{label}</span>
                      <span className={styles.metaVal}>{value}</span>
                    </div>
                  ))}
                </div>
              </TabsContent>
            </Tabs>
          </div>

          {/* Action bar */}
          <div className={`${styles.actions} ${styles.actionsTop}`}>
            <button
              type="button"
              className={`${styles.btn} ${styles.btnGhost}`}
              onClick={() => window.open(issueUrl, "_blank", "noopener,noreferrer")}
              aria-label="Open on GitHub"
            >
              <ExternalLink size={13} aria-hidden="true" />
              Open on GitHub
            </button>
            <button
              type="button"
              className={`${styles.btn} ${savedToLib ? "" : styles.btnPrimary}`}
              onClick={() => handleSaveToLibrary(issueUrl)}
              disabled={savingToLib || savedToLib}
              aria-label={savedToLib ? "Saved to library" : "Save to Library"}
            >
              <BookMarked size={13} aria-hidden="true" />
              {savedToLib ? "Saved" : savingToLib ? "Saving…" : "Save to Library"}
            </button>
          </div>
        </div>
      </main>
    );
  };

  /* ---------------------------------------------------------------- */
  /*  Release Detail View                                              */
  /* ---------------------------------------------------------------- */

  const releaseDetailUI = (release: GitHubRelease & { repo?: string }) => {
    const repoFullName = release.repo ?? "";
    const releaseUrl = repoFullName
      ? `https://github.com/${repoFullName}/releases/tag/${encodeURIComponent(release.tag)}`
      : "#";

    return (
      <main className={styles.detail} aria-label={`Release ${release.tag} detail`}>
        <div className={styles.detailScroll}>
          <div className={styles.dhead}>
            {/* Hide back button on mobile — MobileSplitView nav bar handles back */}
            {!isMobile && (
              <button
                type="button"
                className={styles.back}
                onClick={goBack}
                aria-label="Back to list"
              >
                <ChevronLeft size={14} aria-hidden="true" />
                Back
              </button>
            )}

            <div className={styles.dtitleRow}>
              <Tag size={16} className={`${styles.gi} ${styles.giPr}`} aria-hidden="true" />
              <h2 className={styles.dtitle}>{release.tag}</h2>
              {release.prerelease && <span className={styles.pre}>pre-release</span>}
            </div>
            {repoFullName && <p className={styles.dmeta}>{repoFullName}</p>}
            <p className={styles.dmeta}>
              {release.author} · {formatDate(release.published_at)}
            </p>
          </div>

          {/* Release notes */}
          {release.body && (
            <div className={styles.section}>
              <p className={styles.sectionCap}>Release Notes</p>
              <div className={styles.readme} style={{ maxHeight: "none" }}>
                <pre>{release.body}</pre>
              </div>
            </div>
          )}

          {/* Assets */}
          {release.assets.length > 0 && (
            <div className={styles.section}>
              <p className={styles.sectionCap}>Assets ({release.assets.length})</p>
              <div role="list" aria-label="Release assets">
                {release.assets.map((asset) => (
                  <div key={asset.name} className={styles.asset} role="listitem">
                    <Download size={11} aria-hidden="true" />
                    <span className={styles.assetName}>{asset.name}</span>
                    <span className={styles.assetMeta}>{formatBytes(asset.size)}</span>
                    <span className={styles.assetMeta} aria-label={`${asset.download_count} downloads`}>
                      {asset.download_count.toLocaleString()} dl
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Action bar */}
          <div className={styles.actions}>
            <button
              type="button"
              className={`${styles.btn} ${styles.btnGhost}`}
              onClick={() => window.open(releaseUrl, "_blank", "noopener,noreferrer")}
              aria-label="Open on GitHub"
            >
              <ExternalLink size={13} aria-hidden="true" />
              Open on GitHub
            </button>
            <button
              type="button"
              className={`${styles.btn} ${savedToLib ? "" : styles.btnPrimary}`}
              onClick={() => handleSaveToLibrary(releaseUrl)}
              disabled={savingToLib || savedToLib || releaseUrl === "#"}
              aria-label={savedToLib ? "Saved to library" : "Save to Library"}
            >
              <BookMarked size={13} aria-hidden="true" />
              {savedToLib ? "Saved" : savingToLib ? "Saving…" : "Save to Library"}
            </button>
          </div>
        </div>
      </main>
    );
  };

  /* ---------------------------------------------------------------- */
  /*  Detail dispatch                                                  */
  /* ---------------------------------------------------------------- */

  const detailUI = detail ? (() => {
    if (detail.type === "repo" && detail.repo) return repoDetailUI(detail.repo);
    if (detail.type === "issue" && detail.issue) return issueDetailUI(detail.issue);
    if (detail.type === "release" && detail.release)
      return releaseDetailUI(detail.release as GitHubRelease & { repo?: string });
    return null;
  })() : null;

  /* ---------------------------------------------------------------- */
  /*  Detail title for mobile nav bar                                  */
  /* ---------------------------------------------------------------- */

  const detailTitle = useMemo(() => {
    if (!detail) return "";
    if (detail.type === "repo" && detail.repo) return `${detail.repo.owner}/${detail.repo.name}`;
    if (detail.type === "issue" && detail.issue) return detail.issue.title;
    if (detail.type === "release" && detail.release) return detail.release.tag;
    return "";
  }, [detail]);

  /* ---------------------------------------------------------------- */
  /*  Mobile iOS-style list pane (sidebar sections + item list)        */
  /* ---------------------------------------------------------------- */

  const mobileListPane = (
    <div className={styles.mobileRoot}>
      {authBanner}
      <div className={styles.mscroll}>
        {/* Sources */}
        <p className={styles.mgroupCap}>Sources</p>
        <div className={styles.minset}>
          {(
            [
              { id: "starred" as SidebarSection, label: "Starred", icon: Star, badge: null as number | null },
              {
                id: "notifications" as SidebarSection,
                label: "Notifications",
                icon: Bell,
                badge: unreadCount as number | null,
              },
              { id: "watched" as SidebarSection, label: "Watched", icon: Eye, badge: null as number | null },
            ]
          ).map(({ id, label, icon: Icon, badge }) => (
            <button
              key={id}
              type="button"
              onClick={() => setActiveSection(id)}
              aria-pressed={activeSection === id}
              aria-label={label}
              className={`${styles.mrow} ${activeSection === id ? styles.on : ""}`}
            >
              <Icon size={15} className={styles.lead} aria-hidden="true" />
              <span className={styles.mrowLabel}>{label}</span>
              {badge != null && badge > 0 && (
                <span className={styles.mrowCount} aria-label={`${badge} unread`}>
                  {badge}
                </span>
              )}
              <ChevronRight size={14} className={styles.chev} aria-hidden="true" />
            </button>
          ))}
        </div>

        {/* Content type segmented control */}
        <p className={styles.mgroupCap}>Content</p>
        <div className={styles.mseg}>
          {(
            [
              { id: "repos" as ContentType, label: "Repos", icon: Github },
              { id: "issues" as ContentType, label: "Issues", icon: CircleDot },
              { id: "prs" as ContentType, label: "PRs", icon: GitPullRequest },
              { id: "releases" as ContentType, label: "Releases", icon: Package },
            ] as const
          ).map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              onClick={() => setContentType(id)}
              aria-pressed={contentType === id}
              aria-label={label}
              className={contentType === id ? styles.on : ""}
            >
              <Icon size={13} aria-hidden="true" />
              {label}
            </button>
          ))}
        </div>

        {/* Items list */}
        <p className={styles.mgroupCap}>{listHeading}</p>

        <div className={styles.msearch}>
          <Search size={14} aria-hidden="true" />
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search…"
            aria-label="Search GitHub content"
          />
        </div>

        {loading ? (
          <div className={styles.statusline} role="status" aria-live="polite">
            Loading…
          </div>
        ) : activeItems.length === 0 ? (
          <div className={styles.statusline}>{search ? "No results for your search" : "Nothing here yet"}</div>
        ) : (
          <div className={styles.minset} role="list" aria-label="GitHub items">
            {activeSection === "notifications"
              ? (activeItems as GitHubIssue[]).map((item) => (
                  <button
                    key={`${item.repo}#${item.number}`}
                    type="button"
                    role="listitem"
                    onClick={() => openIssueDetail(item)}
                    aria-label={`Open ${item.is_pull_request ? "PR" : "issue"}: ${item.title}`}
                    className={styles.mrow}
                  >
                    {item.is_pull_request ? (
                      <GitPullRequest size={13} className={`${styles.gi} ${styles.giPr}`} aria-hidden="true" />
                    ) : (
                      <CircleDot size={13} className={`${styles.gi} ${styles.giOpen}`} aria-hidden="true" />
                    )}
                    <div className={styles.missueMain}>
                      <div className={styles.missueTitle}>{item.title}</div>
                      <div className={styles.missueRepo}>{item.repo}</div>
                    </div>
                    <ChevronRight size={14} className={styles.chev} aria-hidden="true" />
                  </button>
                ))
              : (activeItems as GitHubRepo[]).map((item) => (
                  <button
                    key={`${item.owner}/${item.name}`}
                    type="button"
                    role="listitem"
                    onClick={() => openRepoDetail(item)}
                    aria-label={`Open ${item.owner}/${item.name}`}
                    className={styles.mrow}
                  >
                    <div className={styles.mrepoMain}>
                      <div className={styles.mrepoTitle}>
                        <span className={styles.own}>{item.owner}/</span>
                        {item.name}
                      </div>
                      {item.description && <div className={styles.mrepoDesc}>{item.description}</div>}
                      <div className={styles.mrepoMeta}>
                        <span aria-label={`${item.stars} stars`}>
                          <Star size={10} aria-hidden="true" /> {item.stars.toLocaleString()}
                        </span>
                        <span aria-label={`${item.forks} forks`}>
                          <GitFork size={10} aria-hidden="true" /> {item.forks.toLocaleString()}
                        </span>
                        {item.language && <span>{item.language}</span>}
                      </div>
                    </div>
                    <ChevronRight size={14} className={styles.chev} aria-hidden="true" />
                  </button>
                ))}
          </div>
        )}
      </div>
    </div>
  );

  /* ---------------------------------------------------------------- */
  /*  Root layout                                                      */
  /* ---------------------------------------------------------------- */

  // Hide toolbar on mobile when detail is open — MobileSplitView nav bar is shown instead
  const showToolbar = !isMobile || selectedId === null;

  return (
    <div
      ref={rootRef}
      tabIndex={-1}
      className={`${styles.root} flex flex-col h-full min-h-0 overflow-hidden bg-shell-bg text-shell-text select-none relative`}
    >
      {/* Mobile-only toolbar — hidden when detail is shown (MobileSplitView nav handles it).
          On desktop the left rail carries the GitHub brand, so no extra toolbar. */}
      {showToolbar && isMobile && (
        <div className="flex items-center gap-2 px-4 py-3 border-b border-white/5 shrink-0">
          <Github size={15} className="text-accent shrink-0" aria-hidden="true" />
          <h1 className="text-sm font-semibold">GitHub</h1>
        </div>
      )}

      {/* MobileSplitView — stacks on mobile, splits on desktop.
          On desktop the "list" pane carries both the rail and the list column,
          so its width spans the rail (208) plus the list column (~320). */}
      <MobileSplitView
        selectedId={selectedId}
        onBack={goBack}
        listTitle="GitHub"
        detailTitle={detailTitle}
        listWidth={208 + 320}
        list={
          isMobile ? (
            mobileListPane
          ) : (
            <div className="flex h-full overflow-hidden">
              {sidebarUI}
              <div className="flex-1 flex flex-col overflow-hidden">
                {authBanner}
                {listViewUI}
              </div>
            </div>
          )
        }
        detail={
          detailUI ?? (!isMobile ? <div className={styles.emptyDetail}>Select an item to view details</div> : null)
        }
      />
    </div>
  );
}
