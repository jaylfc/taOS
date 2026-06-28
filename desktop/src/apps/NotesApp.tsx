import { useState, useEffect, useCallback, useRef } from "react";
import {
  StickyNote,
  ListChecks,
  Plus,
  Clock,
  Users,
  Bot,
  History,
  Pencil,
  Trash2,
  Check,
  X,
  Square,
  CheckSquare,
  AlertCircle,
  ChevronLeft,
  type LucideIcon,
} from "lucide-react";
import { Button, Textarea } from "@/components/ui";

// ---- Types ----

interface NoteDoc {
  id: string;
  kind: "note" | "list";
  title: string;
  updated_at: string;
  archived_at: string | null;
}

interface NoteEntry {
  id: string;
  text: string;
  done: boolean;
  author: string | null;
  created_at: string;
}

interface NoteMember {
  member_type: "user" | "agent";
  member_id: string;
  permission: "viewer" | "contributor" | "editor";
  action: "research" | "plan" | "critique" | "build" | "discuss" | null;
  standing_instruction: string | null;
}

interface NoteDetail {
  id: string;
  kind: "note" | "list";
  title: string;
  updated_at: string;
  entries: NoteEntry[];
  members: NoteMember[];
}

interface EntryRevision {
  rev_index: number;
  text: string;
  edited_at?: string;
  author?: string;
}

// ---- Helpers ----

function relativeTime(ts: string): string {
  const diff = Date.now() - Date.parse(ts);
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

const PERMISSION_LABELS: Record<NoteMember["permission"], string> = {
  viewer: "Viewer",
  contributor: "Contributor",
  editor: "Editor",
};

const ACTION_OPTIONS = [
  { value: "research", label: "Research" },
  { value: "plan", label: "Plan" },
  { value: "critique", label: "Critique" },
  { value: "build", label: "Build" },
  { value: "discuss", label: "Discuss" },
] as const;

// ---- Kind config ----
// Notes and lists share one component, one store, and one API; they differ only
// in copy, icon, and whether entries carry a done checkbox. A config object
// keeps both variants in sync instead of forking ~1000 lines.

interface DocKindConfig {
  kind: "note" | "list";
  appName: string; // header + listing aria
  icon: LucideIcon;
  noun: string; // doc-level aria: New {noun}, Create {noun}, Share {noun}
  titlePlaceholder: string;
  addPlaceholder: string;
  emptyDocs: string;
  emptyEntries: string;
  selectPrompt: string;
  showDone: boolean;
}

const NOTES_CONFIG: DocKindConfig = {
  kind: "note",
  appName: "Notes",
  icon: StickyNote,
  noun: "note",
  titlePlaceholder: "Note title...",
  addPlaceholder: "Add a note...",
  emptyDocs: "No notes yet.",
  emptyEntries: "Nothing here yet. Add your first note above.",
  selectPrompt: "Select a note to get started.",
  showDone: false,
};

const TODO_CONFIG: DocKindConfig = {
  kind: "list",
  appName: "Todo",
  icon: ListChecks,
  noun: "list",
  titlePlaceholder: "List title...",
  addPlaceholder: "Add a task...",
  emptyDocs: "No lists yet.",
  emptyEntries: "Nothing here yet. Add your first task above.",
  selectPrompt: "Select a list to get started.",
  showDone: true,
};

// ---- Sub-components ----

function MemberBadge({ member }: { member: NoteMember }) {
  return (
    <span
      className={[
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
        member.member_type === "agent"
          ? "bg-accent/10 text-accent"
          : "bg-shell-surface text-shell-text-secondary",
      ].join(" ")}
    >
      {member.member_type === "agent" ? (
        <Bot size={11} className="shrink-0" />
      ) : (
        <Users size={11} className="shrink-0" />
      )}
      {member.member_id}
    </span>
  );
}

// History panel for a single entry
function EntryHistoryPanel({ docId, entryId, onClose }: {
  docId: string;
  entryId: string;
  onClose: () => void;
}) {
  const [revisions, setRevisions] = useState<EntryRevision[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [viewingRev, setViewingRev] = useState<{ index: number; text: string } | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    fetch(`/api/notes/${docId}/entries/${entryId}/history`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("Could not load history."))))
      .then((data: EntryRevision[]) => {
        if (alive) setRevisions(Array.isArray(data) ? data : []);
      })
      .catch((e: Error) => { if (alive) setError(e.message); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [docId, entryId]);

  const viewReqRef = useRef(0);
  async function viewAt(index: number) {
    const myReq = ++viewReqRef.current;
    try {
      const r = await fetch(`/api/notes/${docId}/entries/${entryId}/history/at/${index}`);
      if (!r.ok) throw new Error("Could not load revision.");
      const data: EntryRevision = await r.json();
      if (viewReqRef.current === myReq) setViewingRev({ index, text: data.text });
    } catch (e) {
      if (viewReqRef.current === myReq)
        setError(e instanceof Error ? e.message : "Error loading revision.");
    }
  }

  return (
    <div
      className="flex flex-col gap-2 rounded-xl border border-shell-border bg-shell-bg-deep p-4"
      role="dialog"
      aria-label="Entry history"
    >
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-xs font-medium text-shell-text-secondary">
          <History size={13} className="shrink-0 text-accent" />
          Revision history
        </span>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close history"
          className="text-shell-text-tertiary transition-colors hover:text-shell-text"
        >
          <X size={14} />
        </button>
      </div>

      {loading && <p className="text-xs text-shell-text-tertiary">Loading...</p>}
      {error && (
        <p className="text-xs text-red-400" role="alert">{error}</p>
      )}

      {!loading && revisions.length === 0 && !error && (
        <p className="text-xs text-shell-text-tertiary">No revision history.</p>
      )}

      {revisions.length > 0 && (
        <ol className="flex flex-col gap-1.5">
          {revisions.map((rev) => (
            <li key={rev.rev_index} className="flex items-center justify-between gap-2">
              <div className="flex min-w-0 flex-col gap-0.5">
                <span className="text-xs text-shell-text-tertiary">
                  Rev {rev.rev_index + 1}
                  {rev.edited_at ? ` · ${relativeTime(rev.edited_at)}` : ""}
                  {rev.author ? ` · ${rev.author}` : ""}
                </span>
                <span className="truncate text-xs text-shell-text">{rev.text}</span>
              </div>
              <button
                type="button"
                onClick={() => viewAt(rev.rev_index)}
                aria-label={`View revision ${rev.rev_index + 1}`}
                className="shrink-0 text-xs text-accent transition-opacity hover:opacity-80"
              >
                View
              </button>
            </li>
          ))}
        </ol>
      )}

      {viewingRev && (
        <div className="mt-1 rounded-lg border border-shell-border bg-shell-surface px-3 py-2">
          <p className="mb-1 text-[11px] uppercase tracking-wide text-shell-text-tertiary">
            Rev {viewingRev.index + 1} (read-only)
          </p>
          <p className="whitespace-pre-wrap text-sm text-shell-text">{viewingRev.text}</p>
          <button
            type="button"
            onClick={() => setViewingRev(null)}
            className="mt-1.5 text-xs text-shell-text-tertiary transition-colors hover:text-shell-text"
          >
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}

// Share dialog rendered as an inline panel at the bottom of the detail pane
function SharePanel({ doc, onClose }: { doc: NoteDetail; onClose: () => void }) {
  const [members, setMembers] = useState<NoteMember[]>(doc.members ?? []);
  const [memberType, setMemberType] = useState<"user" | "agent">("user");
  const [memberId, setMemberId] = useState("");
  const [permission, setPermission] = useState<NoteMember["permission"]>("viewer");
  const [action, setAction] = useState<NoteMember["action"]>(null);
  const [instruction, setInstruction] = useState("");
  const [adding, setAdding] = useState(false);
  const [removingId, setRemovingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function addMember() {
    if (!memberId.trim()) return;
    setAdding(true);
    setError(null);
    try {
      const body: Record<string, unknown> = {
        member_type: memberType,
        member_id: memberId.trim(),
        permission,
        action: memberType === "agent" ? action : null,
        standing_instruction: memberType === "agent" && instruction.trim() ? instruction.trim() : null,
      };
      const r = await fetch(`/api/notes/${doc.id}/members`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error(typeof d?.detail === "string" ? d.detail : "Could not add member.");
      }
      const fresh: NoteMember = await r.json();
      setMembers((prev) => {
        const without = prev.filter((m) => m.member_id !== fresh.member_id);
        return [...without, fresh];
      });
      setMemberId("");
      setInstruction("");
      setAction(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not add member.");
    } finally {
      setAdding(false);
    }
  }

  async function removeMember(id: string) {
    setRemovingId(id);
    setError(null);
    try {
      const r = await fetch(`/api/notes/${doc.id}/members/${encodeURIComponent(id)}`, {
        method: "DELETE",
      });
      if (!r.ok) throw new Error("Could not remove member.");
      setMembers((prev) => prev.filter((m) => m.member_id !== id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not remove member.");
    } finally {
      setRemovingId(null);
    }
  }

  return (
    <div
      className="flex flex-col gap-4 rounded-xl border border-shell-border bg-shell-bg-deep p-4"
      role="dialog"
      aria-label={`Share ${doc.kind === "list" ? "list" : "note"}`}
    >
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-sm font-semibold text-shell-text">
          <Users size={15} className="shrink-0 text-accent" />
          Share
        </span>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close share panel"
          className="text-shell-text-tertiary transition-colors hover:text-shell-text"
        >
          <X size={15} />
        </button>
      </div>

      {/* Current members */}
      {members.length > 0 && (
        <div className="flex flex-col gap-2">
          <p className="text-[11px] uppercase tracking-wide text-shell-text-tertiary">Members</p>
          <ul className="flex flex-col gap-1.5">
            {members.map((m) => (
              <li
                key={m.member_id}
                className="flex items-center gap-2 rounded-lg border border-shell-border bg-shell-surface px-3 py-2"
              >
                <MemberBadge member={m} />
                <span className="text-xs text-shell-text-secondary">
                  {PERMISSION_LABELS[m.permission]}
                </span>
                {m.action && (
                  <span className="rounded-full bg-accent/10 px-2 py-0.5 text-[11px] font-medium text-accent capitalize">
                    {m.action}
                  </span>
                )}
                <button
                  type="button"
                  onClick={() => removeMember(m.member_id)}
                  disabled={removingId === m.member_id}
                  aria-label={`Remove ${m.member_id}`}
                  className="ml-auto text-shell-text-tertiary transition-colors hover:text-red-400 disabled:opacity-40"
                >
                  <Trash2 size={13} />
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Add member form */}
      <div className="flex flex-col gap-3">
        <p className="text-[11px] uppercase tracking-wide text-shell-text-tertiary">Add member</p>

        {/* Member type toggle */}
        <div
          className="flex rounded-lg border border-shell-border bg-shell-surface p-0.5"
          role="group"
          aria-label="Member type"
        >
          {(["user", "agent"] as const).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setMemberType(t)}
              aria-pressed={memberType === t}
              className={[
                "flex-1 rounded-md py-1.5 text-xs font-medium transition-colors capitalize",
                memberType === t
                  ? "bg-shell-bg-deep text-shell-text shadow-sm"
                  : "text-shell-text-secondary hover:text-shell-text",
              ].join(" ")}
            >
              {t === "agent" ? <span className="flex items-center justify-center gap-1"><Bot size={11} />{t}</span> : t}
            </button>
          ))}
        </div>

        {/* Member ID */}
        <input
          type="text"
          value={memberId}
          onChange={(e) => setMemberId(e.target.value)}
          placeholder={memberType === "agent" ? "agent-slug" : "user@example.com"}
          aria-label="Member ID or email"
          className="w-full rounded-lg border border-shell-border bg-shell-surface px-3 py-2 text-sm text-shell-text placeholder:text-shell-text-tertiary focus:outline-none focus:ring-2 focus:ring-accent/40"
        />

        {/* Permission: segmented control (3 levels) */}
        <div className="flex flex-col gap-1">
          <p className="text-[11px] text-shell-text-tertiary">Permission</p>
          <div
            className="flex rounded-lg border border-shell-border bg-shell-surface p-0.5"
            role="group"
            aria-label="Permission level"
          >
            {(["viewer", "contributor", "editor"] as const).map((p) => (
              <button
                key={p}
                type="button"
                onClick={() => setPermission(p)}
                aria-pressed={permission === p}
                className={[
                  "flex-1 rounded-md py-1.5 text-xs font-medium transition-colors",
                  permission === p
                    ? "bg-shell-bg-deep text-shell-text shadow-sm"
                    : "text-shell-text-secondary hover:text-shell-text",
                ].join(" ")}
              >
                {PERMISSION_LABELS[p]}
              </button>
            ))}
          </div>
        </div>

        {/* Agent-only: action picker + standing instruction */}
        {memberType === "agent" && (
          <>
            <div className="flex flex-col gap-1">
              <p className="text-[11px] text-shell-text-tertiary">Action</p>
              <div
                className="flex flex-wrap gap-1"
                role="group"
                aria-label="Agent action"
              >
                {ACTION_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => setAction(action === opt.value ? null : opt.value)}
                    aria-pressed={action === opt.value}
                    className={[
                      "rounded-full border px-2.5 py-1 text-xs font-medium transition-colors",
                      action === opt.value
                        ? "border-accent bg-accent/10 text-accent"
                        : "border-shell-border bg-shell-surface text-shell-text-secondary hover:text-shell-text",
                    ].join(" ")}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>

            <Textarea
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              placeholder="Standing instruction (optional)"
              rows={2}
              maxLength={4000}
              aria-label="Standing instruction"
              className="resize-none border-shell-border bg-shell-surface text-shell-text placeholder:text-shell-text-tertiary"
            />
          </>
        )}

        {error && (
          <div
            className="flex items-center gap-2 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-400"
            role="alert"
          >
            <AlertCircle size={13} className="shrink-0" />
            {error}
          </div>
        )}

        <Button
          type="button"
          onClick={addMember}
          disabled={adding || !memberId.trim()}
          className="self-end"
          size="sm"
        >
          {adding ? "Adding..." : "Add"}
        </Button>
      </div>
    </div>
  );
}

// Single entry row with inline edit and history affordance
function EntryRow({
  entry,
  docId,
  showDone,
  onDelete,
  onEditSave,
  onToggleDone,
}: {
  entry: NoteEntry;
  docId: string;
  showDone: boolean;
  onDelete: (id: string) => void;
  onEditSave: (id: string, text: string) => Promise<void>;
  onToggleDone: (id: string, done: boolean) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(entry.text);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);

  async function save() {
    if (!draft.trim() || draft === entry.text) {
      setEditing(false);
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      await onEditSave(entry.id, draft.trim());
      setEditing(false);
    } catch (e) {
      // Keep the editor open with the draft so the user can retry.
      setSaveError(e instanceof Error ? e.message : "Could not save the edit.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <li className="group flex flex-col gap-1">
      <div className="flex items-start gap-2 rounded-lg border border-shell-border bg-shell-surface px-3 py-2.5">
        {editing ? (
          <div className="flex flex-1 flex-col gap-2">
            <Textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              rows={2}
              maxLength={20000}
              aria-label="Edit entry text"
              autoFocus
              className="resize-none border-shell-border bg-shell-bg-deep text-shell-text placeholder:text-shell-text-tertiary"
            />
            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => { setDraft(entry.text); setSaveError(null); setEditing(false); }}
                disabled={saving}
                aria-label="Cancel edit"
              >
                <X size={13} />
                Cancel
              </Button>
              <Button
                type="button"
                size="sm"
                onClick={save}
                disabled={saving || !draft.trim()}
                aria-label="Save edit"
              >
                <Check size={13} />
                {saving ? "Saving..." : "Save"}
              </Button>
            </div>
            {saveError && (
              <p className="text-xs text-red-400" role="alert">{saveError}</p>
            )}
          </div>
        ) : (
          <>
            {showDone && (
              <button
                type="button"
                onClick={() => onToggleDone(entry.id, !entry.done)}
                aria-label={entry.done ? "Mark task not done" : "Mark task done"}
                aria-pressed={entry.done}
                className="mt-0.5 shrink-0 text-shell-text-tertiary transition-colors hover:text-accent"
              >
                {entry.done ? (
                  <CheckSquare size={16} className="text-accent" />
                ) : (
                  <Square size={16} />
                )}
              </button>
            )}
            <p
              className={[
                "min-w-0 flex-1 whitespace-pre-wrap text-sm",
                showDone && entry.done
                  ? "text-shell-text-tertiary line-through"
                  : "text-shell-text",
              ].join(" ")}
            >
              {entry.text}
            </p>
            <div className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
              <button
                type="button"
                onClick={() => setShowHistory((v) => !v)}
                aria-label="View entry history"
                aria-expanded={showHistory}
                className="rounded-md p-1 text-shell-text-tertiary transition-colors hover:text-shell-text"
              >
                <History size={14} />
              </button>
              <button
                type="button"
                onClick={() => setEditing(true)}
                aria-label="Edit entry"
                className="rounded-md p-1 text-shell-text-tertiary transition-colors hover:text-shell-text"
              >
                <Pencil size={14} />
              </button>
              <button
                type="button"
                onClick={() => onDelete(entry.id)}
                aria-label="Delete entry"
                className="rounded-md p-1 text-shell-text-tertiary transition-colors hover:text-red-400"
              >
                <Trash2 size={14} />
              </button>
            </div>
          </>
        )}
      </div>
      {showHistory && (
        <EntryHistoryPanel
          docId={docId}
          entryId={entry.id}
          onClose={() => setShowHistory(false)}
        />
      )}
      {entry.author && (
        <span className="pl-1 text-[11px] text-shell-text-tertiary">
          {entry.author} · {relativeTime(entry.created_at)}
        </span>
      )}
    </li>
  );
}

// Detail pane for a selected note
function NoteDetailPane({
  docId,
  config,
  onBack,
}: {
  docId: string;
  config: DocKindConfig;
  onBack: () => void;
}) {
  const [doc, setDoc] = useState<NoteDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newText, setNewText] = useState("");
  const [adding, setAdding] = useState(false);
  const [showShare, setShowShare] = useState(false);

  const loadReqRef = useRef(0);
  const loadDoc = useCallback(async () => {
    const myReq = ++loadReqRef.current;
    try {
      const r = await fetch(`/api/notes/${docId}`);
      if (!r.ok) throw new Error("Could not load note.");
      const data = await r.json();
      if (loadReqRef.current === myReq) setDoc(data);
    } catch (e) {
      if (loadReqRef.current === myReq)
        setError(e instanceof Error ? e.message : "Could not load note.");
    } finally {
      if (loadReqRef.current === myReq) setLoading(false);
    }
  }, [docId]);

  useEffect(() => {
    setLoading(true);
    setError(null);
    setDoc(null);
    setShowShare(false);
    loadDoc();
  }, [loadDoc]);

  async function addEntry() {
    if (!newText.trim() || !doc) return;
    setAdding(true);
    try {
      const r = await fetch(`/api/notes/${doc.id}/entries`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: newText.trim() }),
      });
      if (!r.ok) throw new Error("Could not add entry.");
      setNewText("");
      await loadDoc();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not add entry.");
    } finally {
      setAdding(false);
    }
  }

  async function deleteEntry(entryId: string) {
    if (!doc) return;
    try {
      const r = await fetch(`/api/notes/${doc.id}/entries/${entryId}`, { method: "DELETE" });
      if (!r.ok) throw new Error("Could not delete entry.");
      setDoc((prev) =>
        prev ? { ...prev, entries: prev.entries.filter((e) => e.id !== entryId) } : prev,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not delete entry.");
    }
  }

  async function editEntry(entryId: string, text: string) {
    if (!doc) return;
    const r = await fetch(`/api/notes/${doc.id}/entries/${entryId}/text`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!r.ok) throw new Error("Could not edit entry.");
    setDoc((prev) =>
      prev
        ? { ...prev, entries: prev.entries.map((e) => (e.id === entryId ? { ...e, text } : e)) }
        : prev,
    );
  }

  async function toggleDone(entryId: string, done: boolean) {
    if (!doc) return;
    // Optimistic: flip immediately, revert on failure.
    setDoc((prev) =>
      prev
        ? { ...prev, entries: prev.entries.map((e) => (e.id === entryId ? { ...e, done } : e)) }
        : prev,
    );
    try {
      const r = await fetch(`/api/notes/${doc.id}/entries/${entryId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ done }),
      });
      if (!r.ok) throw new Error("Could not update task.");
    } catch (e) {
      setDoc((prev) =>
        prev
          ? {
              ...prev,
              entries: prev.entries.map((el) =>
                el.id === entryId ? { ...el, done: !done } : el,
              ),
            }
          : prev,
      );
      setError(e instanceof Error ? e.message : "Could not update task.");
    }
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-sm text-shell-text-tertiary">Loading...</p>
      </div>
    );
  }

  if (error || !doc) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-sm text-red-400" role="alert">{error ?? "Note not found."}</p>
      </div>
    );
  }

  const members = doc.members ?? [];
  const hasMembers = members.length > 0;
  const hasAgentMembers = members.some((m) => m.member_type === "agent");
  const Icon = config.icon;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Detail header */}
      <div className="flex items-center gap-2 border-b border-shell-border px-4 py-3">
        <button
          type="button"
          onClick={onBack}
          aria-label="Back to notes list"
          className="flex items-center gap-1 rounded-md text-shell-text-secondary transition-colors hover:text-shell-text md:hidden"
        >
          <ChevronLeft size={16} />
        </button>
        <h2 className="flex-1 truncate text-sm font-semibold text-shell-text">{doc.title}</h2>
        <div className="flex shrink-0 items-center gap-2">
          {(hasMembers || hasAgentMembers) && (
            <div className="flex items-center gap-1">
              {hasAgentMembers && (
                <span
                  className="inline-flex items-center gap-0.5 rounded-full bg-accent/10 px-1.5 py-0.5 text-[10px] font-medium text-accent"
                  aria-label="Has agent members"
                >
                  <Bot size={10} />
                </span>
              )}
              {hasMembers && (
                <span
                  className="inline-flex items-center gap-0.5 rounded-full bg-shell-surface px-1.5 py-0.5 text-[10px] font-medium text-shell-text-secondary"
                  aria-label="Shared"
                >
                  <Users size={10} />
                  {members.length}
                </span>
              )}
            </div>
          )}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setShowShare((v) => !v)}
            aria-label={`Share ${config.noun}`}
            aria-expanded={showShare}
          >
            <Users size={13} />
            Share
          </Button>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto px-4 py-4">
        <div className="flex flex-col gap-4">
          {/* Error banner */}
          {error && (
            <div
              className="flex items-center gap-2 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-400"
              role="alert"
            >
              <AlertCircle size={13} className="shrink-0" />
              {error}
            </div>
          )}

          {/* Add entry input */}
          <div className="flex flex-col gap-2">
            <Textarea
              value={newText}
              onChange={(e) => setNewText(e.target.value)}
              placeholder={config.addPlaceholder}
              rows={2}
              maxLength={20000}
              aria-label="New entry text"
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault();
                  void addEntry();
                }
              }}
              className="resize-none border-shell-border bg-shell-surface text-shell-text placeholder:text-shell-text-tertiary"
            />
            <div className="flex justify-end">
              <Button
                type="button"
                size="sm"
                onClick={addEntry}
                disabled={adding || !newText.trim()}
                aria-label="Add entry"
              >
                <Plus size={13} />
                {adding ? "Adding..." : "Add"}
              </Button>
            </div>
          </div>

          {/* Entries */}
          {doc.entries.length > 0 ? (
            <ul className="flex flex-col gap-2" aria-label="Note entries">
              {doc.entries.map((entry) => (
                <EntryRow
                  key={entry.id}
                  entry={entry}
                  docId={doc.id}
                  showDone={config.showDone}
                  onDelete={deleteEntry}
                  onEditSave={editEntry}
                  onToggleDone={toggleDone}
                />
              ))}
            </ul>
          ) : (
            <div className="flex flex-col items-center gap-2 py-10 text-center">
              <Icon size={28} className="text-shell-text-tertiary" />
              <p className="text-sm text-shell-text-secondary">{config.emptyEntries}</p>
            </div>
          )}

          {/* Share panel */}
          {showShare && (
            <SharePanel
              doc={doc}
              onClose={() => setShowShare(false)}
            />
          )}
        </div>
      </div>
    </div>
  );
}

// Left-pane note list item
function NoteListItem({
  note,
  selected,
  onClick,
}: {
  note: NoteDoc;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        aria-selected={selected}
        className={[
          "flex w-full flex-col gap-1 rounded-xl border px-3.5 py-3 text-left transition-colors",
          selected
            ? "border-accent bg-accent/10"
            : "border-shell-border bg-shell-surface hover:border-shell-border-strong",
        ].join(" ")}
      >
        <span className="truncate text-sm font-medium text-shell-text">{note.title}</span>
        <span className="flex items-center gap-1 text-xs text-shell-text-tertiary">
          <Clock size={10} className="shrink-0" />
          {relativeTime(note.updated_at)}
        </span>
      </button>
    </li>
  );
}

// Create note modal/inline form
function CreateNoteForm({ config, onCreated, onCancel }: {
  config: DocKindConfig;
  onCreated: (note: NoteDoc) => void;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  async function create() {
    if (!title.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const r = await fetch("/api/notes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: config.kind, title: title.trim() }),
      });
      if (!r.ok) throw new Error(`Could not create ${config.noun}.`);
      const doc: NoteDoc = await r.json();
      onCreated(doc);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create note.");
      setCreating(false);
    }
  }

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-shell-border bg-shell-bg-deep p-3">
      <input
        ref={inputRef}
        type="text"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder={config.titlePlaceholder}
        aria-label={`New ${config.noun} title`}
        maxLength={255}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); void create(); }
          if (e.key === "Escape") onCancel();
        }}
        className="w-full rounded-lg border border-shell-border bg-shell-surface px-3 py-2 text-sm text-shell-text placeholder:text-shell-text-tertiary focus:outline-none focus:ring-2 focus:ring-accent/40"
      />
      {error && (
        <p className="text-xs text-red-400" role="alert">{error}</p>
      )}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel} disabled={creating} aria-label="Cancel">
          Cancel
        </Button>
        <Button type="button" size="sm" onClick={create} disabled={creating || !title.trim()} aria-label={`Create ${config.noun}`}>
          {creating ? "Creating..." : "Create"}
        </Button>
      </div>
    </div>
  );
}

// ---- Main app ----

function DocsApp({ config }: { config: DocKindConfig }) {
  const [notes, setNotes] = useState<NoteDoc[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  const loadNotes = useCallback(async () => {
    try {
      const r = await fetch("/api/notes");
      if (r.ok) {
        const data: unknown = await r.json();
        const all = Array.isArray(data) ? (data as NoteDoc[]) : [];
        // Notes and lists share one API; each app shows only its own kind.
        setNotes(all.filter((d) => d.kind === config.kind));
      }
    } catch {
      // Keep whatever was loaded.
    } finally {
      setLoading(false);
    }
  }, [config.kind]);

  useEffect(() => {
    loadNotes();
  }, [loadNotes]);

  function handleCreated(doc: NoteDoc) {
    setNotes((prev) => [doc, ...prev]);
    setSelectedId(doc.id);
    setShowCreate(false);
  }

  const Icon = config.icon;

  return (
    <div className="flex h-full overflow-hidden bg-shell-bg">
      {/* Left pane: doc list */}
      <div
        className={[
          "flex flex-col border-r border-shell-border",
          // On mobile, hide the list pane when a doc is selected
          selectedId ? "hidden md:flex md:w-72 lg:w-80" : "flex w-full md:w-72 lg:w-80",
        ].join(" ")}
      >
        {/* List header */}
        <div className="flex items-center gap-2 border-b border-shell-border px-4 py-4">
          <Icon size={17} className="text-accent" />
          <h1 className="flex-1 text-base font-semibold text-shell-text">{config.appName}</h1>
          <button
            type="button"
            onClick={() => setShowCreate((v) => !v)}
            aria-label={`New ${config.noun}`}
            aria-expanded={showCreate}
            className={[
              "flex h-8 w-8 items-center justify-center rounded-lg border transition-colors",
              showCreate
                ? "border-accent bg-accent/10 text-accent"
                : "border-shell-border text-shell-text-secondary hover:border-shell-border-strong hover:text-shell-text",
            ].join(" ")}
          >
            <Plus size={16} />
          </button>
        </div>

        {/* Create form */}
        {showCreate && (
          <div className="border-b border-shell-border px-3 py-3">
            <CreateNoteForm
              config={config}
              onCreated={handleCreated}
              onCancel={() => setShowCreate(false)}
            />
          </div>
        )}

        {/* List body */}
        <div className="flex-1 overflow-y-auto px-3 py-3">
          {loading ? (
            <p className="text-sm text-shell-text-tertiary">Loading...</p>
          ) : notes.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-16 text-center">
              <Icon size={28} className="text-shell-text-tertiary" />
              <p className="text-sm text-shell-text-secondary">{config.emptyDocs}</p>
              <button
                type="button"
                onClick={() => setShowCreate(true)}
                className="text-sm text-accent transition-opacity hover:opacity-80"
              >
                Create one
              </button>
            </div>
          ) : (
            <ul className="flex flex-col gap-2" aria-label={config.appName}>
              {notes.map((note) => (
                <NoteListItem
                  key={note.id}
                  note={note}
                  selected={selectedId === note.id}
                  onClick={() => setSelectedId(note.id)}
                />
              ))}
            </ul>
          )}
        </div>
      </div>

      {/* Right pane: detail */}
      <div
        className={[
          "flex-1 overflow-hidden",
          selectedId ? "flex flex-col" : "hidden md:flex md:flex-col",
        ].join(" ")}
      >
        {selectedId ? (
          <NoteDetailPane
            key={selectedId}
            docId={selectedId}
            config={config}
            onBack={() => setSelectedId(null)}
          />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
            <Icon size={32} className="text-shell-text-tertiary" />
            <p className="text-sm text-shell-text-secondary">{config.selectPrompt}</p>
          </div>
        )}
      </div>
    </div>
  );
}

export function NotesApp({ windowId: _windowId }: { windowId: string }) {
  return <DocsApp config={NOTES_CONFIG} />;
}

export function TodoApp({ windowId: _windowId }: { windowId: string }) {
  return <DocsApp config={TODO_CONFIG} />;
}
