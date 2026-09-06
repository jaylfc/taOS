import { useState, useEffect, useCallback, useRef } from "react";
import {
  ListChecks,
  Plus,
  Clock,
  Square,
  CheckSquare,
  ChevronLeft,
  Pencil,
  Trash2,
  Check,
  X,
  ChevronUp,
  ChevronDown,
  AlertCircle,
} from "lucide-react";
import { Button } from "@/components/ui";
import { withCsrf } from "@/lib/csrf";

// ---- Types ----

interface TodoList {
  id: string;
  owner_user_id: string;
  title: string;
  created_at: number;
  updated_at: number;
  archived_at: number | null;
}

interface TodoItem {
  id: string;
  list_id: string;
  text: string;
  done: boolean;
  position: number;
  due_at: number | null;
  remind_at: number | null;
  author: string;
  created_at: number;
  updated_at: number;
}

interface TodoDetail extends TodoList {
  items: TodoItem[];
}

// ---- Helpers ----

function relativeTime(ts: number): string {
  const diff = Date.now() - ts * 1000;
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

function formatDueDate(ts: number): string {
  const d = new Date(ts * 1000);
  const now = new Date();
  const isToday =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  const tomorrow = new Date(now);
  tomorrow.setDate(tomorrow.getDate() + 1);
  const isTomorrow =
    d.getFullYear() === tomorrow.getFullYear() &&
    d.getMonth() === tomorrow.getMonth() &&
    d.getDate() === tomorrow.getDate();

  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (isToday) return `Today ${time}`;
  if (isTomorrow) return `Tomorrow ${time}`;
  return d.toLocaleDateString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function isOverdue(ts: number): boolean {
  return ts * 1000 < Date.now();
}

// ---- TodoItemRow ----

function TodoItemRow({
  item,
  sectionItems,
  onToggleDone,
  onEditSave,
  onDelete,
  onMove,
}: {
  item: TodoItem;
  sectionItems: TodoItem[];
  onToggleDone: (id: string, done: boolean) => void;
  onEditSave: (id: string, text: string) => Promise<void>;
  onDelete: (id: string) => void;
  onMove: (id: string, direction: -1 | 1) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(item.text);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  async function save() {
    if (!draft.trim() || draft === item.text) {
      setEditing(false);
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      await onEditSave(item.id, draft.trim());
      setEditing(false);
    } catch (e) {
      setSaveError(
        e instanceof Error ? e.message : "Could not save the edit.",
      );
    } finally {
      setSaving(false);
    }
  }

  const overdue = item.due_at && isOverdue(item.due_at) && !item.done;
  const sectionIdx = sectionItems.findIndex((i) => i.id === item.id);

  return (
    <li className="group flex flex-col gap-1">
      <div
        className={[
          "flex items-start gap-2 rounded-lg border px-3 py-2.5",
          overdue
            ? "border-red-500/30 bg-red-500/5"
            : "border-shell-border bg-shell-surface",
        ].join(" ")}
      >
        {editing ? (
          <div className="flex flex-1 flex-col gap-2">
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              rows={2}
              maxLength={20000}
              aria-label="Edit task text"
              autoFocus
              className="resize-none rounded-lg border border-shell-border bg-shell-bg-deep px-3 py-2 text-sm text-shell-text placeholder:text-shell-text-tertiary focus:outline-none focus:ring-2 focus:ring-accent/40"
            />
            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => {
                  setDraft(item.text);
                  setSaveError(null);
                  setEditing(false);
                }}
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
              <p className="text-xs text-red-400" role="alert">
                {saveError}
              </p>
            )}
          </div>
        ) : (
          <>
            {/* Checkbox */}
            <button
              type="button"
              onClick={() => onToggleDone(item.id, !item.done)}
              aria-label={item.done ? "Mark task not done" : "Mark task done"}
              aria-pressed={item.done}
              className="mt-0.5 shrink-0 text-shell-text-tertiary transition-colors hover:text-accent"
            >
              {item.done ? (
                <CheckSquare size={16} className="text-accent" />
              ) : (
                <Square size={16} />
              )}
            </button>

            {/* Text + due date */}
            <div className="min-w-0 flex-1">
              <p
                className={[
                  "whitespace-pre-wrap text-sm",
                  item.done
                    ? "text-shell-text-tertiary line-through"
                    : "text-shell-text",
                ].join(" ")}
              >
                {item.text}
              </p>
              {item.due_at && (
                <span
                  className={[
                    "mt-0.5 inline-flex items-center gap-1 text-xs",
                    overdue
                      ? "font-medium text-red-400"
                      : "text-shell-text-tertiary",
                  ].join(" ")}
                >
                  <Clock size={10} className="shrink-0" />
                  {formatDueDate(item.due_at)}
                  {overdue && " · Overdue"}
                </span>
              )}
            </div>

            {/* Action buttons (visible on hover) */}
            <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
              {/* Move up */}
              <button
                type="button"
                onClick={() => onMove(item.id, -1)}
                disabled={sectionIdx <= 0}
                aria-label="Move task up"
                className="rounded p-1 text-shell-text-tertiary transition-colors hover:text-shell-text disabled:opacity-30"
              >
                <ChevronUp size={14} />
              </button>
              {/* Move down */}
              <button
                type="button"
                onClick={() => onMove(item.id, 1)}
                disabled={sectionIdx < 0 || sectionIdx >= sectionItems.length - 1}
                aria-label="Move task down"
                className="rounded p-1 text-shell-text-tertiary transition-colors hover:text-shell-text disabled:opacity-30"
              >
                <ChevronDown size={14} />
              </button>
              {/* Edit */}
              <button
                type="button"
                onClick={() => setEditing(true)}
                aria-label="Edit task"
                className="rounded-md p-1 text-shell-text-tertiary transition-colors hover:text-shell-text"
              >
                <Pencil size={14} />
              </button>
              {/* Delete */}
              <button
                type="button"
                onClick={() => onDelete(item.id)}
                aria-label="Delete task"
                className="rounded-md p-1 text-shell-text-tertiary transition-colors hover:text-red-400"
              >
                <Trash2 size={14} />
              </button>
            </div>
          </>
        )}
      </div>
      {item.author && (
        <span className="pl-1 text-[11px] text-shell-text-tertiary">
          {item.author} · {relativeTime(item.created_at)}
        </span>
      )}
    </li>
  );
}

// ---- TodoDetailPane ----

function TodoDetailPane({
  listId,
  onBack,
}: {
  listId: string;
  onBack: () => void;
}) {
  const [doc, setDoc] = useState<TodoDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newText, setNewText] = useState("");
  const [adding, setAdding] = useState(false);
  const pendingToggles = useRef<Set<string>>(new Set());

  const loadReqRef = useRef(0);
  const loadDoc = useCallback(async () => {
    const myReq = ++loadReqRef.current;
    try {
      const r = await fetch(`/api/todo/${listId}`);
      if (!r.ok) throw new Error("Could not load list.");
      const raw = await r.json();
      const data: TodoDetail = {
        ...raw,
        items: Array.isArray(raw.items) ? raw.items : [],
      };
      if (loadReqRef.current === myReq) {
        setDoc(data);
        setError(null);
      }
    } catch (e) {
      if (loadReqRef.current === myReq)
        setError(e instanceof Error ? e.message : "Could not load list.");
    } finally {
      if (loadReqRef.current === myReq) setLoading(false);
    }
  }, [listId]);

  useEffect(() => {
    setLoading(true);
    setError(null);
    setDoc(null);
    loadDoc();
  }, [loadDoc]);

  async function addItem() {
    if (!newText.trim() || !doc || adding) return;
    setAdding(true);
    try {
      const r = await fetch(
        `/api/todo/${doc.id}/items`,
        withCsrf({
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: newText.trim() }),
        }),
      );
      if (!r.ok) throw new Error("Could not add task.");
      setNewText("");
      setError(null);
      await loadDoc();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not add task.");
    } finally {
      setAdding(false);
    }
  }

  async function deleteItem(itemId: string) {
    if (!doc) return;
    try {
      const r = await fetch(
        `/api/todo/${doc.id}/items/${itemId}`,
        withCsrf({ method: "DELETE" }),
      );
      if (!r.ok) throw new Error("Could not delete task.");
      setError(null);
      setDoc((prev) =>
        prev
          ? { ...prev, items: prev.items.filter((i) => i.id !== itemId) }
          : prev,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not delete task.");
    }
  }

  async function editItem(itemId: string, text: string) {
    if (!doc) return;
    const r = await fetch(
      `/api/todo/${doc.id}/items/${itemId}`,
      withCsrf({
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      }),
    );
    if (!r.ok) throw new Error("Could not edit task.");
    setDoc((prev) =>
      prev
        ? {
            ...prev,
            items: prev.items.map((i) =>
              i.id === itemId ? { ...i, text } : i,
            ),
          }
        : prev,
    );
  }

  async function toggleDone(itemId: string, done: boolean) {
    if (!doc || pendingToggles.current.has(itemId)) return;
    pendingToggles.current.add(itemId);
    // Optimistic update
    setDoc((prev) =>
      prev
        ? {
            ...prev,
            items: prev.items.map((i) =>
              i.id === itemId ? { ...i, done } : i,
            ),
          }
        : prev,
    );
    try {
      const r = await fetch(
        `/api/todo/${doc.id}/items/${itemId}`,
        withCsrf({
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ done }),
        }),
      );
      if (!r.ok) throw new Error("Could not update task.");
      setError(null);
    } catch (e) {
      // Revert on failure
      setDoc((prev) =>
        prev
          ? {
              ...prev,
              items: prev.items.map((i) =>
                i.id === itemId ? { ...i, done: !done } : i,
              ),
            }
          : prev,
      );
      setError(e instanceof Error ? e.message : "Could not update task.");
    } finally {
      pendingToggles.current.delete(itemId);
    }
  }

  async function moveItem(itemId: string, direction: -1 | 1, sectionItems: TodoItem[]) {
    if (!doc) return;
    const docItems: TodoItem[] = Array.isArray(doc.items) ? doc.items : [];
    const sectionIdx = sectionItems.findIndex((i) => i.id === itemId);
    if (sectionIdx < 0) return;
    const newSectionIdx = sectionIdx + direction;
    if (newSectionIdx < 0 || newSectionIdx >= sectionItems.length) return;

    // Swap the two items within the full array by their ids
    const itemA = sectionItems[sectionIdx]!;
    const itemB = sectionItems[newSectionIdx]!;
    const fullIdxA = docItems.findIndex((i) => i.id === itemA.id);
    const fullIdxB = docItems.findIndex((i) => i.id === itemB.id);
    if (fullIdxA < 0 || fullIdxB < 0) return;

    const newItems = [...docItems];
    [newItems[fullIdxA], newItems[fullIdxB]] = [newItems[fullIdxB]!, newItems[fullIdxA]!];
    const reordered = newItems.map((item, i) => ({
      ...item,
      position: i,
    }));

    // Optimistic update
    setDoc((prev) => (prev ? { ...prev, items: reordered } : prev));

    try {
      const r = await fetch(
        `/api/todo/${doc.id}/items/reorder`,
        withCsrf({
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            items: reordered.map((i) => ({ id: i.id, position: i.position })),
          }),
        }),
      );
      if (!r.ok) throw new Error("Could not reorder tasks.");
      setError(null);
    } catch (e) {
      // Revert
      await loadDoc();
      setError(e instanceof Error ? e.message : "Could not reorder tasks.");
    }
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-sm text-shell-text-tertiary">Loading...</p>
      </div>
    );
  }

  if (!doc) {
    return (
      <div className="flex h-full flex-col">
        <button
          type="button"
          onClick={onBack}
          aria-label="Back to lists"
          className="flex items-center gap-1 px-4 py-3 text-sm text-shell-text-secondary transition-colors hover:text-shell-text md:hidden"
        >
          <ChevronLeft size={16} />
          Back
        </button>
        <div className="flex flex-1 items-center justify-center">
          <p className="text-sm text-red-400" role="alert">
            {error ?? "List not found."}
          </p>
        </div>
      </div>
    );
  }

  // Separate incomplete and completed items
  const items: TodoItem[] = Array.isArray(doc.items) ? doc.items : [];
  const incomplete = items.filter((i) => !i.done);
  const complete = items.filter((i) => i.done);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-shell-border px-4 py-3">
        <button
          type="button"
          onClick={onBack}
          aria-label="Back to lists"
          className="flex items-center gap-1 rounded-md text-shell-text-secondary transition-colors hover:text-shell-text md:hidden"
        >
          <ChevronLeft size={16} />
        </button>
        <h2 className="flex-1 truncate text-sm font-semibold text-shell-text">
          {doc.title}
        </h2>
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

          {/* Add task input */}
          <div className="flex gap-2">
            <input
              type="text"
              value={newText}
              onChange={(e) => setNewText(e.target.value)}
              placeholder="Add a task..."
              aria-label="New task text"
              maxLength={20000}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void addItem();
                }
              }}
              className="flex-1 rounded-lg border border-shell-border bg-shell-surface px-3 py-2 text-sm text-shell-text placeholder:text-shell-text-tertiary focus:outline-none focus:ring-2 focus:ring-accent/40"
            />
            <Button
              type="button"
              size="sm"
              onClick={addItem}
              disabled={adding || !newText.trim()}
              aria-label="Add task"
            >
              <Plus size={13} />
              {adding ? "Adding..." : "Add"}
            </Button>
          </div>

          {/* Incomplete items */}
          {incomplete.length > 0 && (
            <ul className="flex flex-col gap-2" aria-label="Incomplete tasks">
              {incomplete.map((item) => (
                <TodoItemRow
                  key={item.id}
                  item={item}
                  sectionItems={incomplete}
                  onToggleDone={toggleDone}
                  onEditSave={editItem}
                  onDelete={deleteItem}
                  onMove={(id, dir) => moveItem(id, dir, incomplete)}
                />
              ))}
            </ul>
          )}

          {/* Completed items (collapsed by default) */}
          {complete.length > 0 && (
            <div className="flex flex-col gap-2">
              <p className="text-xs font-medium text-shell-text-tertiary">
                Completed ({complete.length})
              </p>
              <ul className="flex flex-col gap-2" aria-label="Completed tasks">
                {complete.map((item) => (
                  <TodoItemRow
                    key={item.id}
                    item={item}
                    sectionItems={complete}
                    onToggleDone={toggleDone}
                    onEditSave={editItem}
                    onDelete={deleteItem}
                    onMove={(id, dir) => moveItem(id, dir, complete)}
                  />
                ))}
              </ul>
            </div>
          )}

          {/* Empty state */}
          {items.length === 0 && (
            <div className="flex flex-col items-center gap-2 py-10 text-center">
              <ListChecks size={28} className="text-shell-text-tertiary" />
              <p className="text-sm text-shell-text-secondary">
                Nothing here yet. Add your first task above.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ---- TodoListItem ----

function TodoListItem({
  list,
  selected,
  onClick,
}: {
  list: TodoList;
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
        <span className="truncate text-sm font-medium text-shell-text">
          {list.title}
        </span>
        <span className="flex items-center gap-1 text-xs text-shell-text-tertiary">
          <Clock size={10} className="shrink-0" />
          {relativeTime(list.updated_at)}
        </span>
      </button>
    </li>
  );
}

// ---- CreateTodoForm ----

function CreateTodoForm({
  onCreated,
  onCancel,
}: {
  onCreated: (list: TodoList) => void;
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
    if (!title.trim() || creating) return;
    setCreating(true);
    setError(null);
    try {
      const r = await fetch(
        "/api/todo",
        withCsrf({
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: title.trim() }),
        }),
      );
      if (!r.ok) throw new Error("Could not create list.");
      const doc: TodoList = await r.json();
      setCreating(false);
      onCreated(doc);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create list.");
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
        placeholder="List title..."
        aria-label="New list title"
        maxLength={255}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            void create();
          }
          if (e.key === "Escape") onCancel();
        }}
        className="w-full rounded-lg border border-shell-border bg-shell-surface px-3 py-2 text-sm text-shell-text placeholder:text-shell-text-tertiary focus:outline-none focus:ring-2 focus:ring-accent/40"
      />
      {error && (
        <p className="text-xs text-red-400" role="alert">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onCancel}
          disabled={creating}
          aria-label="Cancel"
        >
          Cancel
        </Button>
        <Button
          type="button"
          size="sm"
          onClick={create}
          disabled={creating || !title.trim()}
          aria-label="Create list"
        >
          {creating ? "Creating..." : "Create"}
        </Button>
      </div>
    </div>
  );
}

// ---- Main TodoApp ----

export function TodoApp({ windowId: _windowId }: { windowId: string }) {
  const [lists, setLists] = useState<TodoList[]>([]);
  const [loading, setLoading] = useState(true);
  const [listLoadError, setListLoadError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  const loadLists = useCallback(async () => {
    try {
      const r = await fetch("/api/todo");
      if (!r.ok) throw new Error("Could not load lists.");
      const data: unknown = await r.json();
      setLists(Array.isArray(data) ? (data as TodoList[]) : []);
      setListLoadError(null);
    } catch (e) {
      setListLoadError(e instanceof Error ? e.message : "Could not load lists.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadLists();
  }, [loadLists]);

  function handleCreated(doc: TodoList) {
    setLists((prev) => [doc, ...prev]);
    setSelectedId(doc.id);
    setShowCreate(false);
  }

  return (
    <div className="flex h-full overflow-hidden bg-shell-bg">
      {/* Left pane: list of todo lists */}
      <div
        className={[
          "flex flex-col border-r border-shell-border",
          selectedId
            ? "hidden md:flex md:w-72 lg:w-80"
            : "flex w-full md:w-72 lg:w-80",
        ].join(" ")}
      >
        {/* Header */}
        <div className="flex items-center gap-2 border-b border-shell-border px-4 py-4">
          <ListChecks size={17} className="text-accent" />
          <h1 className="flex-1 text-base font-semibold text-shell-text">
            Todo
          </h1>
          <button
            type="button"
            onClick={() => setShowCreate((v) => !v)}
            aria-label="New list"
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
            <CreateTodoForm
              onCreated={handleCreated}
              onCancel={() => setShowCreate(false)}
            />
          </div>
        )}

        {/* List body */}
        <div className="flex-1 overflow-y-auto px-3 py-3">
          {loading ? (
            <p className="text-sm text-shell-text-tertiary">Loading...</p>
          ) : listLoadError ? (
            <p className="text-sm text-red-400" role="alert">
              {listLoadError}
            </p>
          ) : lists.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-16 text-center">
              <ListChecks
                size={28}
                className="text-shell-text-tertiary"
              />
              <p className="text-sm text-shell-text-secondary">
                No lists yet.
              </p>
              <button
                type="button"
                onClick={() => setShowCreate(true)}
                className="text-sm text-accent transition-opacity hover:opacity-80"
              >
                Create one
              </button>
            </div>
          ) : (
            <ul className="flex flex-col gap-2" aria-label="Todo lists">
              {lists.map((list) => (
                <TodoListItem
                  key={list.id}
                  list={list}
                  selected={selectedId === list.id}
                  onClick={() => setSelectedId(list.id)}
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
          <TodoDetailPane
            key={selectedId}
            listId={selectedId}
            onBack={() => setSelectedId(null)}
          />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
            <ListChecks size={32} className="text-shell-text-tertiary" />
            <p className="text-sm text-shell-text-secondary">
              Select a list to get started.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
