import { useState, useEffect, useCallback, useMemo } from "react";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Button, Card, Input, Label, Textarea } from "../components/ui";
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
/*                                                                     */
/*  STYLING RULE: this app is built from the shared UI kit (Button,     */
/*  Card, Input, Textarea, Label) and the semantic shell and accent      */
/*  tokens - never a raw Tailwind palette class or a hex literal. Raw    */
/*  palette colours are pinned to one scheme: they render identically    */
/*  under every theme, so an app that uses them silently stops           */
/*  following taOS Light and any installed theme. The rail, the frosted  */
/*  header bar and the pill badges deliberately mirror Settings and the  */
/*  App Store so the Studio reads as the same product.                   */
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

/* Row and badge shapes shared across the panels, so every list in the Studio
   has the same density, radius and hairline. */
const ROW_CLS = "flex items-center gap-3 px-3 py-2";
/* Card's own base leans on arbitrary white overlays (bg-white/[0.04]) that the
   light-scheme compatibility layer in tokens.css does not invert, so pin every
   Card here to the shell tokens, which do flip per theme. */
const SURFACE_CLS = "border-shell-border bg-shell-surface";
const BADGE_CLS =
  "inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium";
const EMPTY_CLS = "text-sm text-shell-text-tertiary";

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
    <div className="flex h-full w-full bg-shell-bg text-shell-text">
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
      {/* Left rail — same shape as the Settings sidebar: a recessed surface
          layer, one hairline, and icon-chipped rows. */}
      <nav
        className="flex w-52 shrink-0 flex-col border-r border-white/5 bg-shell-surface/30"
        aria-label="Assistant Studio sections"
      >
        <div
          className="flex shrink-0 items-center gap-2 border-b border-shell-border bg-shell-bg/95 px-3 py-3"
          style={{ backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)" }}
        >
          <UserRound className="h-4 w-4 text-accent" aria-hidden />
          <span className="text-sm font-semibold text-shell-text">Assistant Studio</span>
        </div>
        <ul className="flex-1 space-y-1 overflow-y-auto p-2">
          {RAIL.map((r) => {
            const Icon = r.icon;
            const active = view === r.id;
            return (
              <li key={r.id}>
                <Button
                  variant={active ? "secondary" : "ghost"}
                  onClick={() => setView(r.id)}
                  aria-current={active ? "page" : undefined}
                  className="h-auto w-full justify-start gap-3 py-2"
                >
                  <span
                    className={`rounded-md p-1.5 transition-colors ${
                      active ? "bg-accent/20 text-accent" : "bg-white/5"
                    }`}
                  >
                    <Icon className="h-4 w-4" aria-hidden />
                  </span>
                  {r.label}
                </Button>
              </li>
            );
          })}
        </ul>
        {/* PA picker */}
        <div className="border-t border-shell-border p-3">
          <Label htmlFor="pa-select" className="mb-1 block uppercase tracking-wide">
            Your PA
          </Label>
          <select
            id="pa-select"
            value={pa}
            onChange={(e) => requestPaChange(e.target.value)}
            className="h-9 w-full rounded-lg border border-white/10 bg-shell-bg-deep px-2 text-sm text-shell-text transition-colors focus-visible:border-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/20"
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
            <p className="mt-1 text-[11px] text-shell-text-tertiary">Loading agents...</p>
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

/* ---------- shared header ----------
   The frosted bar the rest of taOS uses (see StoreApp): a hairline shell
   border over a translucent shell background with a backdrop blur, pinned so
   content scrolling underneath stays legible. */
function Header({ title, sub }: { title: string; sub?: string }) {
  return (
    <div
      className="sticky top-0 z-10 shrink-0 border-b border-shell-border bg-shell-bg/95 px-6 py-4"
      style={{ backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)" }}
    >
      <h2 className="text-lg font-semibold tracking-[-0.01em] text-shell-text">{title}</h2>
      {sub && <p className="mt-0.5 text-sm text-shell-text-secondary">{sub}</p>}
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
        <h3 className="mb-2 text-sm font-medium text-shell-text-secondary">Today</h3>
        {dueToday.length === 0 ? (
          <p className={EMPTY_CLS}>Nothing due today.</p>
        ) : (
          <ul className="space-y-1">
            {dueToday.map((t) => (
              <li key={t.id} className="text-sm text-shell-text">
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
    <Card className={`${SURFACE_CLS} overflow-hidden transition-colors hover:border-accent/40 hover:bg-shell-surface-hover`}>
      <button
        type="button"
        onClick={onClick}
        className="w-full rounded-xl p-4 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
      >
        <div className="text-2xl font-semibold tabular-nums text-shell-text">{value}</div>
        <div className="text-sm text-shell-text-secondary">{label}</div>
      </button>
    </Card>
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
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={pa ? "What happened, decisions, follow-ups..." : "Select a PA first"}
            disabled={!pa}
            aria-label="New journal entry"
            className="h-24 resize-y"
          />
          <div className="mt-2 flex justify-end">
            <Button type="button" onClick={add} disabled={!draft.trim() || !pa} size="sm">
              <Plus className="h-4 w-4" aria-hidden /> Add entry
            </Button>
          </div>
        </div>
        <ul className="space-y-3">
          {entries.map((e) => (
            <li key={e.id}>
              <Card className={`${SURFACE_CLS} p-3`}>
                <div className="mb-1 flex items-center justify-between">
                  <span className="text-xs text-shell-text-tertiary">
                    {new Date(e.ts).toLocaleString()}
                  </span>
                  <DeleteButton onClick={() => remove(e.id)} label="Delete entry" />
                </div>
                <p className="whitespace-pre-wrap text-sm text-shell-text">{e.body}</p>
              </Card>
            </li>
          ))}
          {entries.length === 0 && <li className={EMPTY_CLS}>No entries yet.</li>}
        </ul>
      </div>
    </div>
  );
}

/* A delete affordance is repeated in three panels; keep one shape for it. */
function DeleteButton({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <Button
      variant="ghost"
      size="icon"
      type="button"
      onClick={onClick}
      aria-label={label}
      className="h-7 w-7 text-shell-text-tertiary transition-colors hover:text-red-400"
    >
      <Trash2 className="h-4 w-4" aria-hidden />
    </Button>
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
          <Input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && add()}
            placeholder={pa ? "New task" : "Select a PA first"}
            disabled={!pa}
            aria-label="New task title"
            className="min-w-[200px] flex-1"
          />
          <Input
            type="date"
            value={due}
            onChange={(e) => setDue(e.target.value)}
            disabled={!pa}
            aria-label="Task due date"
            className="w-auto"
          />
          <Button type="button" onClick={add} disabled={!title.trim() || !pa} size="sm">
            <Plus className="h-4 w-4" aria-hidden /> Add
          </Button>
        </div>
        <ul className="space-y-1">
          {tasks.map((t) => (
            <li key={t.id}>
              <Card className={`${SURFACE_CLS} ${ROW_CLS}`}>
                <button
                  type="button"
                  onClick={() => toggle(t.id)}
                  aria-label={t.done ? "Mark task not done" : "Mark task done"}
                  className={`flex h-5 w-5 shrink-0 items-center justify-center rounded border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
                    t.done
                      ? "border-emerald-500 bg-emerald-500/20 text-emerald-500"
                      : "border-white/20 hover:border-accent/40"
                  }`}
                >
                  {t.done && <Check className="h-3.5 w-3.5" aria-hidden />}
                </button>
                <span
                  className={`flex-1 text-sm ${
                    t.done ? "text-shell-text-tertiary line-through" : "text-shell-text"
                  }`}
                >
                  {t.title}
                </span>
                {t.due && <span className="text-xs text-shell-text-tertiary">{t.due}</span>}
                <DeleteButton onClick={() => remove(t.id)} label="Delete task" />
              </Card>
            </li>
          ))}
          {tasks.length === 0 && <li className={EMPTY_CLS}>No tasks yet.</li>}
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
          <Input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            disabled={!pa}
            aria-label="Event date"
            className="w-auto"
          />
          <Input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && add()}
            placeholder={pa ? "Event title" : "Select a PA first"}
            disabled={!pa}
            aria-label="Event title"
            className="min-w-[200px] flex-1"
          />
          <Button
            type="button"
            onClick={add}
            disabled={!date || !title.trim() || !pa}
            size="sm"
          >
            <Plus className="h-4 w-4" aria-hidden /> Add
          </Button>
        </div>
        <ul className="space-y-1">
          {agenda.map((a, i) => (
            <li key={i}>
              <Card className={`${SURFACE_CLS} ${ROW_CLS} text-sm`}>
                <span className="w-16 shrink-0 text-shell-text-secondary">
                  {fmtDate(new Date(a.date + "T00:00:00").getTime())}
                </span>
                <span className="flex-1 text-shell-text">{a.label}</span>
                <span
                  className={`${BADGE_CLS} ${
                    a.kind === "event"
                      ? "border-accent/20 bg-accent/10 text-accent"
                      : "border-amber-500/20 bg-amber-500/15 text-amber-500"
                  }`}
                >
                  {a.kind}
                </span>
              </Card>
            </li>
          ))}
          {agenda.length === 0 && <li className={EMPTY_CLS}>Nothing scheduled.</li>}
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
        <p className="text-sm text-shell-text-secondary">
          Your PA is reachable through the taOS agent chat and the Messages app.
          Direct requests, briefs, and quick questions go here.
        </p>
        <div className="flex gap-2">
          <Button type="button" onClick={openChat}>
            <MessagesSquare className="h-4 w-4" aria-hidden /> Open chat with your PA
          </Button>
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
        <p className="text-sm text-shell-text-secondary">
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
    draft: "border-white/15 bg-white/10 text-shell-text-secondary",
    "in-progress": "border-amber-500/20 bg-amber-500/15 text-amber-500",
    delivered: "border-emerald-500/20 bg-emerald-500/15 text-emerald-500",
  };

  return (
    <div>
      <Header title="Deliverables" sub="Files, reports, and outputs from your PA." />
      <div className="p-6">
        <div className="mb-4 flex flex-wrap gap-2">
          <Input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder={pa ? "Deliverable title" : "Select a PA first"}
            disabled={!pa}
            aria-label="Deliverable title"
            className="min-w-[200px] flex-1"
          />
          <Input
            value={link}
            onChange={(e) => setLink(e.target.value)}
            placeholder="Link (optional)"
            disabled={!pa}
            aria-label="Deliverable link"
            className="min-w-[160px] w-auto"
          />
          <Button type="button" onClick={add} disabled={!title.trim() || !pa} size="sm">
            <Plus className="h-4 w-4" aria-hidden /> Add
          </Button>
        </div>
        <ul className="space-y-1">
          {items.map((d) => (
            <li key={d.id}>
              <Card className={`${SURFACE_CLS} ${ROW_CLS}`}>
                <span className="flex-1 text-sm text-shell-text">{d.title}</span>
                {d.link && (
                  <a
                    href={d.link}
                    target="_blank"
                    rel="noreferrer"
                    aria-label="Open deliverable link"
                    className="rounded text-accent transition-colors hover:text-accent-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
                  >
                    <ExternalLink className="h-4 w-4" aria-hidden />
                  </a>
                )}
                <button
                  type="button"
                  onClick={() => cycle(d.id)}
                  aria-label="Cycle status"
                  className={`${BADGE_CLS} transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${statusColor[d.status]}`}
                >
                  {d.status}
                </button>
                <DeleteButton onClick={() => remove(d.id)} label="Delete deliverable" />
              </Card>
            </li>
          ))}
          {items.length === 0 && <li className={EMPTY_CLS}>No deliverables yet.</li>}
        </ul>
      </div>
    </div>
  );
}
