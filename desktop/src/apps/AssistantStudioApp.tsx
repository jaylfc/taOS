import { useState, useEffect, useCallback, useMemo } from "react";
import { ConfirmDialog } from "../components/ConfirmDialog";
import {
  LayoutDashboard,
  NotebookPen,
  CalendarDays,
  ListTodo,
  MessagesSquare,
  PenTool,
  FolderKanban,
  UserRound,
  Plus,
  Check,
  Trash2,
  ExternalLink,
} from "lucide-react";

/* ------------------------------------------------------------------ */
/*  Assistant Studio - the workspace for your personal assistant (PA)   */
/*                                                                     */
/*  Pick a registered agent to be your PA, then work out of a single    */
/*  hub: Overview, Journal, Calendar / time, Tasks, Comms, Canvas, and   */
/*  a Deliverables (files / reports) area. Journal / Tasks / Calendar    */
/*  events / Deliverables persist locally per PA so switching PA swaps   */
/*  the whole workspace. Comms and Canvas open the live taOS surfaces.   */
/* ------------------------------------------------------------------ */

type StudioView =
  | "overview"
  | "journal"
  | "calendar"
  | "tasks"
  | "comms"
  | "canvas"
  | "deliverables";

const RAIL: { id: StudioView; label: string; icon: typeof LayoutDashboard }[] = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "journal", label: "Journal", icon: NotebookPen },
  { id: "calendar", label: "Calendar", icon: CalendarDays },
  { id: "tasks", label: "Tasks", icon: ListTodo },
  { id: "comms", label: "Comms", icon: MessagesSquare },
  { id: "canvas", label: "Canvas", icon: PenTool },
  { id: "deliverables", label: "Deliverables", icon: FolderKanban },
];

interface Agent {
  name: string;
  display_name?: string;
  handle?: string;
  framework?: string;
}

interface JournalEntry {
  id: string;
  ts: number;
  body: string;
}
interface Task {
  id: string;
  title: string;
  due?: string;
  done: boolean;
}
interface CalEvent {
  id: string;
  date: string;
  title: string;
}
interface Deliverable {
  id: string;
  title: string;
  status: "draft" | "in-progress" | "delivered";
  link?: string;
}

const PA_KEY = "taos.assistantStudio.pa";
const nsKey = (pa: string, kind: string) => `taos.assistantStudio.${pa}.${kind}`;

function loadJSON<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}
function saveJSON(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* quota / private mode: non-fatal */
  }
}
const rid = () => Math.random().toString(36).slice(2, 10);
const fmtDate = (ts: number) =>
  new Date(ts).toLocaleDateString(undefined, { month: "short", day: "numeric" });

export function AssistantStudioApp({ windowId: _windowId }: { windowId: string }) {
  const [view, setView] = useState<StudioView>("overview");
  const [agents, setAgents] = useState<Agent[]>([]);
  const [pa, setPa] = useState<string>(() => localStorage.getItem(PA_KEY) || "");
  const [loadingAgents, setLoadingAgents] = useState(true);

  // Load the registered agents so the user can pick a PA. Best effort: a fetch
  // failure just leaves the picker with whatever PA was already chosen.
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch("/api/agents");
        const data = res.ok ? await res.json() : [];
        const list: Agent[] = Array.isArray(data) ? data : data.agents || [];
        if (!alive) return;
        setAgents(list);
        // Default the PA to Hermes when nothing is chosen yet.
        const first = list[0];
        if (!localStorage.getItem(PA_KEY) && first) {
          const hermes = list.find((a) =>
            (a.name || a.handle || "").toLowerCase().includes("hermes"),
          );
          const chosen = (hermes || first).name;
          setPa(chosen);
          localStorage.setItem(PA_KEY, chosen);
        }
      } catch {
        /* offline / no agents: keep the current PA */
      } finally {
        if (alive) setLoadingAgents(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const choosePa = (name: string) => {
    setPa(name);
    localStorage.setItem(PA_KEY, name);
  };

  const labelFor = useCallback(
    (name: string) => {
      const a = agents.find((x) => x.name === name);
      return a?.display_name || a?.handle || name;
    },
    [agents],
  );

  // Every surface in this app is scoped to the assigned PA, so changing it
  // swaps the whole workspace at once. Confirm first — but only for a real
  // CHANGE: the first assignment has no previous PA to move away from, so
  // interrupting it would be a prompt with nothing at stake.
  const [pendingPa, setPendingPa] = useState<string | null>(null);

  const requestPaChange = (name: string) => {
    if (!name || name === pa || !pa) {
      choosePa(name);
      return;
    }
    setPendingPa(name);
  };

  const paAgent = useMemo(
    () => agents.find((a) => a.name === pa),
    [agents, pa],
  );
  const paLabel = paAgent?.display_name || paAgent?.handle || pa || "no PA selected";

  return (
    <div className="flex h-full w-full bg-zinc-950 text-zinc-100">
      {/* Not `danger`: switching PA is reversible and deletes nothing, so a red
          destructive button would overstate it. The point is that the change is
          easy to make by accident and re-scopes every panel at once. */}
      <ConfirmDialog
        open={pendingPa !== null}
        title="Change your personal assistant?"
        message={
          `Notes, tasks, calendar and deliverables here are all scoped to the assigned PA. ` +
          `Switching from ${labelFor(pa)} to ${labelFor(pendingPa ?? "")} moves the whole ` +
          `workspace over to ${labelFor(pendingPa ?? "")}. Nothing is deleted, and you can switch back.`
        }
        confirmLabel="Change PA"
        cancelLabel="Keep current PA"
        onConfirm={() => {
          if (pendingPa) choosePa(pendingPa);
          setPendingPa(null);
        }}
        onCancel={() => setPendingPa(null)}
      />
      {/* Left rail */}
      <nav
        className="flex w-44 flex-shrink-0 flex-col border-r border-zinc-800 bg-zinc-900/60"
        aria-label="Assistant Studio sections"
      >
        <div className="flex items-center gap-2 px-3 py-3 border-b border-zinc-800">
          <UserRound className="h-4 w-4 text-sky-400" aria-hidden />
          <span className="text-sm font-semibold">Assistant Studio</span>
        </div>
        <ul className="flex-1 overflow-y-auto py-1">
          {RAIL.map((r) => {
            const Icon = r.icon;
            const active = view === r.id;
            return (
              <li key={r.id}>
                <button
                  type="button"
                  onClick={() => setView(r.id)}
                  aria-current={active ? "page" : undefined}
                  className={`flex w-full items-center gap-2 px-3 py-2 text-sm ${
                    active
                      ? "bg-sky-500/15 text-sky-300 border-l-2 border-sky-400"
                      : "text-zinc-300 hover:bg-zinc-800/60 border-l-2 border-transparent"
                  }`}
                >
                  <Icon className="h-4 w-4" aria-hidden />
                  {r.label}
                </button>
              </li>
            );
          })}
        </ul>
        {/* PA picker */}
        <div className="border-t border-zinc-800 p-3">
          <label
            htmlFor="pa-select"
            className="block text-[11px] uppercase tracking-wide text-zinc-500 mb-1"
          >
            Your PA
          </label>
          <select
            id="pa-select"
            value={pa}
            onChange={(e) => requestPaChange(e.target.value)}
            className="w-full rounded bg-zinc-800 px-2 py-1.5 text-sm text-zinc-100 border border-zinc-700 focus:border-sky-500 outline-none"
            aria-label="Select the agent to act as your personal assistant"
          >
            {!pa && <option value="">Select an agent</option>}
            {agents.map((a) => (
              <option key={a.name} value={a.name}>
                {a.display_name || a.handle || a.name}
              </option>
            ))}
          </select>
          {loadingAgents && (
            <p className="mt-1 text-[11px] text-zinc-500">Loading agents...</p>
          )}
        </div>
      </nav>

      {/* Active surface */}
      <main className="flex-1 overflow-y-auto">
        {view === "overview" && (
          <OverviewView pa={pa} paLabel={paLabel} onNavigate={setView} />
        )}
        {view === "journal" && <JournalView pa={pa} />}
        {view === "calendar" && <CalendarView pa={pa} />}
        {view === "tasks" && <TasksView pa={pa} />}
        {view === "comms" && <CommsView paLabel={paLabel} />}
        {view === "canvas" && <CanvasView />}
        {view === "deliverables" && <DeliverablesView pa={pa} />}
      </main>
    </div>
  );
}

/* ---------- shared header ---------- */
function Header({ title, sub }: { title: string; sub?: string }) {
  return (
    <div className="border-b border-zinc-800 px-6 py-4">
      <h2 className="text-lg font-semibold">{title}</h2>
      {sub && <p className="mt-0.5 text-sm text-zinc-400">{sub}</p>}
    </div>
  );
}

/* ---------- Overview ---------- */
function OverviewView({
  pa,
  paLabel,
  onNavigate,
}: {
  pa: string;
  paLabel: string;
  onNavigate: (v: StudioView) => void;
}) {
  const tasks = pa ? loadJSON<Task[]>(nsKey(pa, "tasks"), []) : [];
  const open = tasks.filter((t) => !t.done);
  const journal = pa ? loadJSON<JournalEntry[]>(nsKey(pa, "journal"), []) : [];
  const today = new Date().toISOString().slice(0, 10);
  const dueToday = open.filter((t) => t.due === today);

  return (
    <div>
      <Header title="Overview" sub={`Your personal assistant: ${paLabel}`} />
      <div className="grid grid-cols-1 gap-4 p-6 sm:grid-cols-3">
        <Stat label="Open tasks" value={open.length} onClick={() => onNavigate("tasks")} />
        <Stat label="Due today" value={dueToday.length} onClick={() => onNavigate("calendar")} />
        <Stat label="Journal entries" value={journal.length} onClick={() => onNavigate("journal")} />
      </div>
      <div className="px-6 pb-6">
        <h3 className="mb-2 text-sm font-medium text-zinc-300">Today</h3>
        {dueToday.length === 0 ? (
          <p className="text-sm text-zinc-500">Nothing due today.</p>
        ) : (
          <ul className="space-y-1">
            {dueToday.map((t) => (
              <li key={t.id} className="text-sm text-zinc-200">
                - {t.title}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
function Stat({ label, value, onClick }: { label: string; value: number; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-4 text-left hover:border-sky-600"
    >
      <div className="text-2xl font-semibold tabular-nums">{value}</div>
      <div className="text-sm text-zinc-400">{label}</div>
    </button>
  );
}

/* ---------- Journal ---------- */
function JournalView({ pa }: { pa: string }) {
  const key = nsKey(pa, "journal");
  const [entries, setEntries] = useState<JournalEntry[]>(() => loadJSON(key, []));
  const [draft, setDraft] = useState("");
  useEffect(() => setEntries(loadJSON(key, [])), [key]);

  const add = useCallback(() => {
    if (!draft.trim() || !pa) return;
    const next = [{ id: rid(), ts: Date.now(), body: draft.trim() }, ...entries];
    setEntries(next);
    saveJSON(key, next);
    setDraft("");
  }, [draft, entries, key, pa]);

  const remove = (id: string) => {
    const next = entries.filter((e) => e.id !== id);
    setEntries(next);
    saveJSON(key, next);
  };

  return (
    <div>
      <Header title="Journal" sub="Notes and daily log, kept per PA." />
      <div className="p-6">
        <div className="mb-4">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={pa ? "What happened, decisions, follow-ups..." : "Select a PA first"}
            disabled={!pa}
            aria-label="New journal entry"
            className="h-24 w-full resize-y rounded border border-zinc-700 bg-zinc-900 p-3 text-sm outline-none focus:border-sky-500 disabled:opacity-50"
          />
          <div className="mt-2 flex justify-end">
            <button
              type="button"
              onClick={add}
              disabled={!draft.trim() || !pa}
              className="flex items-center gap-1 rounded bg-sky-600 px-3 py-1.5 text-sm font-medium hover:bg-sky-500 disabled:opacity-40"
            >
              <Plus className="h-4 w-4" aria-hidden /> Add entry
            </button>
          </div>
        </div>
        <ul className="space-y-3">
          {entries.map((e) => (
            <li key={e.id} className="rounded border border-zinc-800 bg-zinc-900/60 p-3">
              <div className="mb-1 flex items-center justify-between">
                <span className="text-xs text-zinc-500">
                  {new Date(e.ts).toLocaleString()}
                </span>
                <button
                  type="button"
                  onClick={() => remove(e.id)}
                  aria-label="Delete entry"
                  className="text-zinc-500 hover:text-red-400"
                >
                  <Trash2 className="h-4 w-4" aria-hidden />
                </button>
              </div>
              <p className="whitespace-pre-wrap text-sm text-zinc-200">{e.body}</p>
            </li>
          ))}
          {entries.length === 0 && (
            <li className="text-sm text-zinc-500">No entries yet.</li>
          )}
        </ul>
      </div>
    </div>
  );
}

/* ---------- Tasks ---------- */
function TasksView({ pa }: { pa: string }) {
  const key = nsKey(pa, "tasks");
  const [tasks, setTasks] = useState<Task[]>(() => loadJSON(key, []));
  const [title, setTitle] = useState("");
  const [due, setDue] = useState("");
  useEffect(() => setTasks(loadJSON(key, [])), [key]);

  const persist = (next: Task[]) => {
    setTasks(next);
    saveJSON(key, next);
  };
  const add = () => {
    if (!title.trim() || !pa) return;
    persist([{ id: rid(), title: title.trim(), due: due || undefined, done: false }, ...tasks]);
    setTitle("");
    setDue("");
  };
  const toggle = (id: string) =>
    persist(tasks.map((t) => (t.id === id ? { ...t, done: !t.done } : t)));
  const remove = (id: string) => persist(tasks.filter((t) => t.id !== id));

  return (
    <div>
      <Header title="Tasks" sub="What you have delegated or need to track." />
      <div className="p-6">
        <div className="mb-4 flex flex-wrap gap-2">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && add()}
            placeholder={pa ? "New task" : "Select a PA first"}
            disabled={!pa}
            aria-label="New task title"
            className="min-w-[200px] flex-1 rounded border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-sm outline-none focus:border-sky-500 disabled:opacity-50"
          />
          <input
            type="date"
            value={due}
            onChange={(e) => setDue(e.target.value)}
            disabled={!pa}
            aria-label="Task due date"
            className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm outline-none focus:border-sky-500 disabled:opacity-50"
          />
          <button
            type="button"
            onClick={add}
            disabled={!title.trim() || !pa}
            className="flex items-center gap-1 rounded bg-sky-600 px-3 py-1.5 text-sm font-medium hover:bg-sky-500 disabled:opacity-40"
          >
            <Plus className="h-4 w-4" aria-hidden /> Add
          </button>
        </div>
        <ul className="space-y-1">
          {tasks.map((t) => (
            <li
              key={t.id}
              className="flex items-center gap-3 rounded border border-zinc-800 bg-zinc-900/60 px-3 py-2"
            >
              <button
                type="button"
                onClick={() => toggle(t.id)}
                aria-label={t.done ? "Mark task not done" : "Mark task done"}
                className={`flex h-5 w-5 items-center justify-center rounded border ${
                  t.done ? "border-emerald-500 bg-emerald-500/20 text-emerald-400" : "border-zinc-600"
                }`}
              >
                {t.done && <Check className="h-3.5 w-3.5" aria-hidden />}
              </button>
              <span className={`flex-1 text-sm ${t.done ? "text-zinc-500 line-through" : "text-zinc-200"}`}>
                {t.title}
              </span>
              {t.due && <span className="text-xs text-zinc-500">{t.due}</span>}
              <button
                type="button"
                onClick={() => remove(t.id)}
                aria-label="Delete task"
                className="text-zinc-500 hover:text-red-400"
              >
                <Trash2 className="h-4 w-4" aria-hidden />
              </button>
            </li>
          ))}
          {tasks.length === 0 && <li className="text-sm text-zinc-500">No tasks yet.</li>}
        </ul>
      </div>
    </div>
  );
}

/* ---------- Calendar / time ---------- */
function CalendarView({ pa }: { pa: string }) {
  const evKey = nsKey(pa, "events");
  const [events, setEvents] = useState<CalEvent[]>(() => loadJSON(evKey, []));
  const [date, setDate] = useState("");
  const [title, setTitle] = useState("");
  useEffect(() => setEvents(loadJSON(evKey, [])), [evKey]);

  const tasks = pa ? loadJSON<Task[]>(nsKey(pa, "tasks"), []) : [];
  const agenda = useMemo(() => {
    const items: { date: string; label: string; kind: string }[] = [
      ...events.map((e) => ({ date: e.date, label: e.title, kind: "event" })),
      ...tasks
        .filter((t) => t.due && !t.done)
        .map((t) => ({ date: t.due as string, label: t.title, kind: "task" })),
    ];
    return items.sort((a, b) => a.date.localeCompare(b.date));
  }, [events, tasks]);

  const add = () => {
    if (!date || !title.trim() || !pa) return;
    const next = [...events, { id: rid(), date, title: title.trim() }];
    setEvents(next);
    saveJSON(evKey, next);
    setTitle("");
    setDate("");
  };

  return (
    <div>
      <Header title="Calendar and time" sub="Upcoming events and task due dates." />
      <div className="p-6">
        <div className="mb-4 flex flex-wrap gap-2">
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            disabled={!pa}
            aria-label="Event date"
            className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm outline-none focus:border-sky-500 disabled:opacity-50"
          />
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && add()}
            placeholder={pa ? "Event title" : "Select a PA first"}
            disabled={!pa}
            aria-label="Event title"
            className="min-w-[200px] flex-1 rounded border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-sm outline-none focus:border-sky-500 disabled:opacity-50"
          />
          <button
            type="button"
            onClick={add}
            disabled={!date || !title.trim() || !pa}
            className="flex items-center gap-1 rounded bg-sky-600 px-3 py-1.5 text-sm font-medium hover:bg-sky-500 disabled:opacity-40"
          >
            <Plus className="h-4 w-4" aria-hidden /> Add
          </button>
        </div>
        <ul className="space-y-1">
          {agenda.map((a, i) => (
            <li
              key={i}
              className="flex items-center gap-3 rounded border border-zinc-800 bg-zinc-900/60 px-3 py-2 text-sm"
            >
              <span className="w-16 text-zinc-400">
                {fmtDate(new Date(a.date + "T00:00:00").getTime())}
              </span>
              <span className="flex-1 text-zinc-200">{a.label}</span>
              <span
                className={`rounded px-1.5 py-0.5 text-[11px] ${
                  a.kind === "event"
                    ? "bg-sky-500/15 text-sky-300"
                    : "bg-amber-500/15 text-amber-300"
                }`}
              >
                {a.kind}
              </span>
            </li>
          ))}
          {agenda.length === 0 && (
            <li className="text-sm text-zinc-500">Nothing scheduled.</li>
          )}
        </ul>
      </div>
    </div>
  );
}

/* ---------- Comms ---------- */
function CommsView({ paLabel }: { paLabel: string }) {
  const openChat = () => {
    // The taOS agent chat panel is toggled via a global event the shell listens
    // for; fall back to opening the Messages app if that is not wired.
    window.dispatchEvent(new CustomEvent("taos:open-agent-chat"));
  };
  return (
    <div>
      <Header title="Comms" sub={`Talk to ${paLabel} and route messages.`} />
      <div className="space-y-4 p-6">
        <p className="text-sm text-zinc-400">
          Your PA is reachable through the taOS agent chat and the Messages app.
          Direct requests, briefs, and quick questions go here.
        </p>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={openChat}
            className="flex items-center gap-2 rounded bg-sky-600 px-3 py-2 text-sm font-medium hover:bg-sky-500"
          >
            <MessagesSquare className="h-4 w-4" aria-hidden /> Open chat with your PA
          </button>
        </div>
      </div>
    </div>
  );
}

/* ---------- Canvas ---------- */
function CanvasView() {
  return (
    <div>
      <Header title="Canvas" sub="A shared visual space with your PA." />
      <div className="p-6">
        <p className="text-sm text-zinc-400">
          Use the project Canvas for whiteboarding with your PA - notes, links,
          diagrams, and images your assistant can read and add to. Open a project
          and switch to its Canvas tab to collaborate there.
        </p>
      </div>
    </div>
  );
}

/* ---------- Deliverables ---------- */
function DeliverablesView({ pa }: { pa: string }) {
  const key = nsKey(pa, "deliverables");
  const [items, setItems] = useState<Deliverable[]>(() => loadJSON(key, []));
  const [title, setTitle] = useState("");
  const [link, setLink] = useState("");
  useEffect(() => setItems(loadJSON(key, [])), [key]);

  const persist = (next: Deliverable[]) => {
    setItems(next);
    saveJSON(key, next);
  };
  const add = () => {
    if (!title.trim() || !pa) return;
    persist([
      { id: rid(), title: title.trim(), link: link.trim() || undefined, status: "draft" },
      ...items,
    ]);
    setTitle("");
    setLink("");
  };
  const cycle = (id: string) =>
    persist(
      items.map((d) =>
        d.id === id
          ? {
              ...d,
              status:
                d.status === "draft"
                  ? "in-progress"
                  : d.status === "in-progress"
                    ? "delivered"
                    : "draft",
            }
          : d,
      ),
    );
  const remove = (id: string) => persist(items.filter((d) => d.id !== id));

  const statusColor: Record<Deliverable["status"], string> = {
    draft: "bg-zinc-600/30 text-zinc-300",
    "in-progress": "bg-amber-500/15 text-amber-300",
    delivered: "bg-emerald-500/15 text-emerald-300",
  };

  return (
    <div>
      <Header title="Deliverables" sub="Files, reports, and outputs from your PA." />
      <div className="p-6">
        <div className="mb-4 flex flex-wrap gap-2">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder={pa ? "Deliverable title" : "Select a PA first"}
            disabled={!pa}
            aria-label="Deliverable title"
            className="min-w-[200px] flex-1 rounded border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-sm outline-none focus:border-sky-500 disabled:opacity-50"
          />
          <input
            value={link}
            onChange={(e) => setLink(e.target.value)}
            placeholder="Link (optional)"
            disabled={!pa}
            aria-label="Deliverable link"
            className="min-w-[160px] rounded border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-sm outline-none focus:border-sky-500 disabled:opacity-50"
          />
          <button
            type="button"
            onClick={add}
            disabled={!title.trim() || !pa}
            className="flex items-center gap-1 rounded bg-sky-600 px-3 py-1.5 text-sm font-medium hover:bg-sky-500 disabled:opacity-40"
          >
            <Plus className="h-4 w-4" aria-hidden /> Add
          </button>
        </div>
        <ul className="space-y-1">
          {items.map((d) => (
            <li
              key={d.id}
              className="flex items-center gap-3 rounded border border-zinc-800 bg-zinc-900/60 px-3 py-2"
            >
              <span className="flex-1 text-sm text-zinc-200">{d.title}</span>
              {d.link && (
                <a
                  href={d.link}
                  target="_blank"
                  rel="noreferrer"
                  aria-label="Open deliverable link"
                  className="text-sky-400 hover:text-sky-300"
                >
                  <ExternalLink className="h-4 w-4" aria-hidden />
                </a>
              )}
              <button
                type="button"
                onClick={() => cycle(d.id)}
                aria-label="Cycle status"
                className={`rounded px-2 py-0.5 text-[11px] ${statusColor[d.status]}`}
              >
                {d.status}
              </button>
              <button
                type="button"
                onClick={() => remove(d.id)}
                aria-label="Delete deliverable"
                className="text-zinc-500 hover:text-red-400"
              >
                <Trash2 className="h-4 w-4" aria-hidden />
              </button>
            </li>
          ))}
          {items.length === 0 && (
            <li className="text-sm text-zinc-500">No deliverables yet.</li>
          )}
        </ul>
      </div>
    </div>
  );
}
