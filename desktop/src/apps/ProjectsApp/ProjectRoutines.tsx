import { useEffect, useState } from "react";
import { projectsApi, type Project, type ProjectMember, type Routine } from "@/lib/projects";

// Project-scoped Routines & Schedules panel: a routine fires on a cron
// schedule, an inbound webhook, or a manual/API trigger, auto-creating a task
// on this project's board and best-effort waking the assignee agent. Kept
// inside ProjectsApp (not the global TasksApp) because routines are always
// scoped to one project's board.

const CRON_PRESETS: { label: string; expr: string }[] = [
  { label: "Every hour", expr: "0 * * * *" },
  { label: "Daily at 3am", expr: "0 3 * * *" },
  { label: "Every 15 min", expr: "*/15 * * * *" },
];

function relativeTime(ts: number | null): string {
  if (!ts) return "never";
  const diff = Math.max(0, Date.now() - ts * 1000);
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function untilTime(ts: number | null): string {
  if (!ts) return "—";
  const diff = ts * 1000 - Date.now();
  if (diff <= 0) return "due now";
  const mins = Math.round(diff / 60_000);
  if (mins < 60) return `in ${mins}m`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `in ${hrs}h`;
  return `in ${Math.round(hrs / 24)}d`;
}

export function ProjectRoutines({ project }: { project: Project }) {
  const [items, setItems] = useState<Routine[]>([]);
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [assigneeId, setAssigneeId] = useState("");
  const [triggerKind, setTriggerKind] = useState<"cron" | "webhook" | "api">("cron");
  const [cronExpr, setCronExpr] = useState("0 3 * * *");
  const [submitting, setSubmitting] = useState(false);

  const refresh = () =>
    projectsApi.routines
      .list(project.id)
      .then(setItems)
      .catch(() => setError("Could not load routines for this project."));

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    projectsApi.routines
      .list(project.id)
      .then((rows) => {
        if (!cancelled) setItems(rows);
      })
      .catch(() => {
        if (!cancelled) setError("Could not load routines for this project.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    projectsApi.members
      .list(project.id)
      .then((rows) => {
        if (!cancelled) setMembers(rows);
      })
      .catch(() => {
        if (!cancelled) setMembers([]);
      });
    return () => {
      cancelled = true;
    };
  }, [project.id]);

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = title.trim();
    if (!trimmed || submitting) return;
    setSubmitting(true);
    try {
      await projectsApi.routines.create(project.id, {
        title: trimmed,
        body_template: body.trim(),
        assignee_id: assigneeId || null,
        trigger_kind: triggerKind,
        cron_expr: triggerKind === "cron" ? cronExpr : null,
      });
      setTitle("");
      setBody("");
      setAssigneeId("");
      setError(null);
      await refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setSubmitting(false);
    }
  };

  const toggleEnabled = async (r: Routine) => {
    try {
      await projectsApi.routines.update(project.id, r.id, { enabled: !r.enabled });
      await refresh();
    } catch (err) {
      setError(String(err));
    }
  };

  const runNow = async (r: Routine) => {
    try {
      await projectsApi.routines.trigger(project.id, r.id);
      await refresh();
    } catch (err) {
      setError(String(err));
    }
  };

  const remove = async (r: Routine) => {
    try {
      await projectsApi.routines.remove(project.id, r.id);
      await refresh();
    } catch (err) {
      setError(String(err));
    }
  };

  return (
    <section className="flex flex-col gap-4">
      <form
        onSubmit={create}
        className="flex flex-col gap-2 rounded-xl border border-shell-border bg-shell-surface p-3.5"
        aria-label="Create routine"
      >
        <label className="text-sm text-shell-text-secondary">
          Title
          <input
            aria-label="Routine title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="e.g. Weekly report"
            required
            className="mt-1 w-full rounded bg-shell-bg-deep px-2 py-1 text-sm text-shell-text outline-none focus:ring-2 focus:ring-accent-line"
          />
        </label>
        <label className="text-sm text-shell-text-secondary">
          Task body
          <textarea
            aria-label="Routine task body"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={2}
            className="mt-1 w-full rounded bg-shell-bg-deep px-2 py-1 text-sm text-shell-text outline-none focus:ring-2 focus:ring-accent-line"
          />
        </label>
        <div className="flex flex-wrap gap-3">
          <label className="flex-1 text-sm text-shell-text-secondary">
            Assignee
            <select
              aria-label="Assignee"
              value={assigneeId}
              onChange={(e) => setAssigneeId(e.target.value)}
              className="mt-1 w-full rounded bg-shell-bg-deep px-2 py-1 text-sm text-shell-text"
            >
              <option value="">Unassigned</option>
              {members.map((m) => (
                <option key={m.member_id} value={m.member_id}>
                  {m.member_id}
                </option>
              ))}
            </select>
          </label>
          <label className="flex-1 text-sm text-shell-text-secondary">
            Trigger
            <select
              aria-label="Trigger kind"
              value={triggerKind}
              onChange={(e) => setTriggerKind(e.target.value as "cron" | "webhook" | "api")}
              className="mt-1 w-full rounded bg-shell-bg-deep px-2 py-1 text-sm text-shell-text"
            >
              <option value="cron">Schedule (cron)</option>
              <option value="webhook">Webhook</option>
              <option value="api">API / manual only</option>
            </select>
          </label>
        </div>
        {triggerKind === "cron" && (
          <div className="flex flex-col gap-2">
            <label className="text-sm text-shell-text-secondary">
              Cron schedule
              <input
                aria-label="Cron expression"
                value={cronExpr}
                onChange={(e) => setCronExpr(e.target.value)}
                placeholder="0 3 * * *"
                required
                className="mt-1 w-full rounded bg-shell-bg-deep px-2 py-1 font-mono text-sm text-shell-text outline-none focus:ring-2 focus:ring-accent-line"
              />
            </label>
            <div className="flex flex-wrap gap-2">
              {CRON_PRESETS.map((p) => (
                <button
                  key={p.expr}
                  type="button"
                  onClick={() => setCronExpr(p.expr)}
                  className="rounded-full bg-accent/10 px-2 py-0.5 text-xs font-medium text-accent hover:bg-accent/20"
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>
        )}
        {triggerKind === "webhook" && (
          <p className="text-xs text-shell-text-tertiary">
            A unique webhook URL is generated once the routine is created.
          </p>
        )}
        {error && (
          <div role="alert" className="text-xs text-red-400">
            {error}
          </div>
        )}
        <div className="flex justify-end">
          <button
            type="submit"
            disabled={submitting}
            className="rounded bg-accent/10 px-3 py-1 text-sm font-medium text-accent hover:bg-accent/20 disabled:opacity-50"
          >
            {submitting ? "Creating…" : "Create routine"}
          </button>
        </div>
      </form>

      {loading ? (
        <p className="text-sm text-shell-text-tertiary">Loading routines…</p>
      ) : items.length === 0 ? (
        <p className="text-sm text-shell-text-secondary">No routines for this project yet.</p>
      ) : (
        <ul className="flex flex-col gap-2" aria-label="Project routines">
          {items.map((r) => {
            const webhookUrl =
              r.trigger_kind === "webhook" && r.webhook_token
                ? `${window.location.origin}/api/webhooks/routines/${r.webhook_token}`
                : null;
            return (
              <li
                key={r.id}
                className="flex flex-col gap-1.5 rounded-xl border border-shell-border bg-shell-surface p-3.5"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="inline-flex items-center rounded-full bg-accent/10 px-2 py-0.5 text-xs font-medium capitalize text-accent">
                    {r.trigger_kind}
                  </span>
                  <p className="text-sm font-medium text-shell-text">{r.title}</p>
                  <label className="ml-auto flex items-center gap-1.5 text-xs text-shell-text-secondary">
                    <input
                      type="checkbox"
                      checked={!!r.enabled}
                      onChange={() => toggleEnabled(r)}
                      aria-label={`Toggle enabled for ${r.title}`}
                    />
                    Enabled
                  </label>
                </div>
                {r.trigger_kind === "cron" && (
                  <p className="text-xs text-shell-text-tertiary">
                    <code>{r.cron_expr}</code> · last fired {relativeTime(r.last_fired)} · next fire{" "}
                    {untilTime(r.next_fire)}
                  </p>
                )}
                {webhookUrl && (
                  <p className="truncate text-xs text-shell-text-tertiary" title={webhookUrl}>
                    {webhookUrl}
                  </p>
                )}
                {r.assignee_id && (
                  <p className="text-xs text-shell-text-tertiary">Assignee: {r.assignee_id}</p>
                )}
                <div className="flex justify-end gap-3 text-xs">
                  <button
                    type="button"
                    onClick={() => runNow(r)}
                    className="text-accent hover:underline"
                  >
                    Run now
                  </button>
                  <button
                    type="button"
                    onClick={() => remove(r)}
                    className="text-red-400 hover:underline"
                    aria-label={`Delete ${r.title}`}
                  >
                    Delete
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

export default ProjectRoutines;
