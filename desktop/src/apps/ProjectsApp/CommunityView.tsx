import { useEffect, useState } from "react";
import { projectsApi, type CommunitySnapshot, type CommunityTask, type ContributorStat, type ActivityFeedItem, type CommunityStats } from "@/lib/projects";
import styles from "./ProjectsApp.module.css";

function StatusBadge({ status }: { status: string }) {
  const colorMap: Record<string, string> = {
    open: "var(--board-status-ready, #64d2ff)",
    claimed: "var(--board-status-claimed, #ffb340)",
    "in-progress": "var(--board-status-claimed, #ffb340)",
    closed: "var(--board-status-closed, #30d158)",
    cancelled: "var(--color-traffic-close, #ff5f57)",
  };
  const color = colorMap[status] ?? "var(--color-shell-text-secondary, #6b7280)";
  return (
    <span
      className={styles.statusBadge}
      style={{ backgroundColor: color, color: "#fff" }}
    >
      {status}
    </span>
  );
}

function KanbanColumn({
  title,
  tasks,
}: {
  title: string;
  tasks: CommunityTask[];
}) {
  return (
    <div className={styles.kanbanColumn}>
      <h3 className={styles.kanbanColTitle}>
        {title} <span className={styles.kanbanCount}>{tasks.length}</span>
      </h3>
      <ul className={styles.kanbanList}>
        {tasks.map((t) => (
          <li key={t.id} className={styles.kanbanCard}>
            <div className={styles.kanbanCardTitle}>{t.title}</div>
            {t.claimed_by && (
              <div className={styles.kanbanClaimant} title={t.claimed_by}>
                {t.claimed_by}
              </div>
            )}
            {t.labels && t.labels.length > 0 && (
              <div className={styles.kanbanLabels}>
                {t.labels.map((l) => (
                  <span key={l} className={styles.kanbanLabel}>{l}</span>
                ))}
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Leaderboard({ contributors }: { contributors: ContributorStat[] }) {
  if (contributors.length === 0) {
    return <p className="text-sm text-shell-text-secondary">No contributions yet.</p>;
  }
  const top = contributors.slice(0, 10);
  const maxTotal = top[0]?.total ?? 1;
  return (
    <div className={styles.leaderboard}>
      <table className={styles.leaderboardTable}>
        <thead>
          <tr>
            <th>#</th>
            <th>Contributor</th>
            <th>Claims</th>
            <th>Closes</th>
            <th>Total</th>
          </tr>
        </thead>
        <tbody>
          {top.map((c, i) => (
            <tr key={c.actor}>
              <td className={styles.rank}>{i + 1}</td>
              <td className={styles.actor} title={c.actor}>{c.actor}</td>
              <td>{c.claims}</td>
              <td>{c.closes}</td>
              <td>
                <div className={styles.barWrap}>
                  <div
                    className={styles.bar}
                    style={{ width: `${Math.round((c.total / maxTotal) * 100)}%` }}
                  />
                  <span className={styles.barLabel}>{c.total}</span>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ActivityFeed({ items }: { items: ActivityFeedItem[] }) {
  if (items.length === 0) {
    return <p className="text-sm text-shell-text-secondary">No recent activity.</p>;
  }
  return (
    <ul className={styles.feed}>
      {items.map((ev) => (
        <li key={ev.id} className={styles.feedItem}>
          <span className={styles.feedEvent}>{ev.event}</span>
          <span className={styles.feedActor} title={ev.actor}>{ev.actor}</span>
          <time className={styles.feedTs}>
            {new Date(ev.ts).toLocaleString()}
          </time>
        </li>
      ))}
    </ul>
  );
}

export function CommunityView({ projectId }: { projectId: string }) {
  const [data, setData] = useState<CommunitySnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    projectsApi.community
      .snapshot(projectId)
      .then((snap) => {
        if (!cancelled) {
          setData(snap);
          setError(null);
        }
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // Poll /community/stats every 30 s for live leaderboard + status counts.
  useEffect(() => {
    let cancelled = false;
    const poll = () => {
      projectsApi.community
        .stats(projectId)
        .then((stats: CommunityStats) => {
          if (cancelled) return;
          setData((prev) => {
            if (!prev) return prev;
            return {
              ...prev,
              status_counts: stats.status_counts,
              contributors: stats.leaderboard,
            };
          });
        })
        .catch(() => {
          // silent — keep last good snapshot
        });
    };
    const interval = setInterval(poll, 30_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [projectId]);

  if (loading) {
    return <div className="text-sm text-shell-text-secondary p-4">Loading community view…</div>;
  }
  if (error) {
    return <div className="text-sm text-red-500 p-4">Failed to load community view: {error}</div>;
  }
  if (!data) {
    return <div className="text-sm text-shell-text-secondary p-4">No community data available.</div>;
  }

  const tasksByStatus: Record<string, CommunityTask[]> = {};
  for (const t of data.tasks) {
    const s = t.status;
    if (!tasksByStatus[s]) tasksByStatus[s] = [];
    tasksByStatus[s].push(t);
  }

  const statusOrder = ["open", "claimed", "in-progress", "closed", "cancelled"];
  const knownColumns = statusOrder.filter((s) => tasksByStatus[s]?.length);
  const otherColumns = Object.keys(tasksByStatus).filter((s) => !statusOrder.includes(s));
  const columns = [...knownColumns, ...otherColumns];

  return (
    <div className={styles.communityView}>
      {/* Overview Stats */}
      <section className={styles.overviewStats}>
        <h2>Overview</h2>
        <div className={styles.statGrid}>
          {Object.entries(data.status_counts).map(([status, count]) => (
            <div key={status} className={styles.statCard}>
              <span className={styles.statCount}>{count}</span>
              <span className={styles.statLabel}>
                <StatusBadge status={status} />
              </span>
            </div>
          ))}
          <div className={styles.statCard}>
            <span className={styles.statCount}>{data.tasks.length}</span>
            <span className={styles.statLabel}>Total</span>
          </div>
        </div>
      </section>

      {/* Leaderboard */}
      <section className={styles.leaderboardSection}>
        <h2>Leaderboard</h2>
        <Leaderboard contributors={data.contributors} />
      </section>

      {/* Live Kanban (read-only) */}
      <section className={styles.liveKanban}>
        <h2>Board</h2>
        {columns.length > 0 ? (
          <div className={styles.kanbanGrid}>
            {columns.map((status) => (
              <KanbanColumn
                key={status}
                title={status}
                tasks={tasksByStatus[status] ?? []}
              />
            ))}
          </div>
        ) : (
          <p className="text-sm text-shell-text-secondary">No tasks on this board yet.</p>
        )}
      </section>

      {/* Recent Activity */}
      <section className={styles.activitySection}>
        <h2>Recent Activity</h2>
        <ActivityFeed items={data.recent_activity} />
      </section>
    </div>
  );
}
