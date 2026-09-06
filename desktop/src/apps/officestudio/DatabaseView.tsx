import { useCallback, useEffect, useRef, useState } from "react";
import { Plus, Save, Sparkles, X } from "lucide-react";
import {
  addColumn,
  addRow,
  blankTable,
  type CellValue,
  changeType,
  COLUMN_TYPES,
  type ColumnType,
  type DbTable,
  parseTableContent,
  removeColumn,
  removeRow,
  renameColumn,
  serializeTable,
  setCell,
} from "./db/table";

type OfficeDocListItem = {
  id: string;
  kind: string;
  title: string;
  updated_at?: number;
};

type OfficeDoc = OfficeDocListItem & {
  content: string;
};

function formatUpdated(ts?: number): string {
  if (!ts) return "Draft";
  const d = new Date(ts * 1000);
  return `Updated ${d.toLocaleString()}`;
}

export function DatabaseView() {
  const [docs, setDocs] = useState<OfficeDocListItem[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [title, setTitle] = useState("Untitled table");
  const [updatedAt, setUpdatedAt] = useState<number | undefined>();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [table, setTable] = useState<DbTable>(() => blankTable());

  // Monotonic token for the in-flight openDoc request. Bumping it invalidates
  // any pending open (on unmount, on a newer open, or on newDoc), so a slow
  // response can't land setState on an unmounted/closing view or clobber a
  // newer selection.
  const openReqRef = useRef(0);
  useEffect(
    () => () => {
      openReqRef.current += 1;
    },
    [],
  );

  const loadList = useCallback(async () => {
    const res = await fetch("/api/office/docs", { credentials: "include" });
    if (!res.ok) throw new Error("Could not load tables");
    const items = (await res.json()) as OfficeDocListItem[];
    setDocs(items.filter((d) => d.kind === "db"));
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await loadList();
        if (!cancelled) setError(null);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Load failed");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadList]);

  const openDoc = async (docId: string) => {
    const reqId = ++openReqRef.current;
    setError(null);
    try {
      const res = await fetch(`/api/office/docs/${encodeURIComponent(docId)}`, {
        credentials: "include",
      });
      if (!res.ok) throw new Error("Could not open table");
      const doc = (await res.json()) as OfficeDoc;
      // A newer open (or newDoc/unmount) superseded this request; drop it.
      if (openReqRef.current !== reqId) return;
      setActiveId(doc.id);
      setTitle(doc.title);
      setUpdatedAt(doc.updated_at);
      setTable(parseTableContent(doc.content));
    } catch (e) {
      if (openReqRef.current !== reqId) return;
      setError(e instanceof Error ? e.message : "Open failed");
    }
  };

  const newDoc = () => {
    // Invalidate any in-flight open so its response can't overwrite this fresh
    // table once it resolves.
    openReqRef.current += 1;
    setActiveId(null);
    setTitle("Untitled table");
    setUpdatedAt(undefined);
    setError(null);
    setTable(blankTable());
  };

  const saveDoc = async () => {
    setSaving(true);
    setError(null);
    let saved: OfficeDoc;
    try {
      const content = serializeTable(table);
      const payload = { kind: "db", title: title.trim() || "Untitled table", content };
      const url = activeId
        ? `/api/office/docs/${encodeURIComponent(activeId)}`
        : "/api/office/docs";
      const res = await fetch(url, {
        method: activeId ? "PUT" : "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error((body as { error?: string }).error || "Save failed");
      }
      saved = (await res.json()) as OfficeDoc;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
      setSaving(false);
      return;
    }
    // The save itself succeeded: clear the saving state and reflect the saved
    // doc before refreshing the list.
    setActiveId(saved.id);
    setTitle(saved.title);
    setUpdatedAt(saved.updated_at);
    setSaving(false);
    // Refresh the sidebar list separately; a refresh failure must not report
    // the (already successful) save as failed.
    try {
      await loadList();
    } catch {
      /* non-fatal: the save succeeded; the list refreshes on the next action */
    }
  };

  const renderCellInput = (
    columnId: string,
    type: ColumnType,
    rowId: string,
    columnName: string,
    rowIndex: number,
    value: CellValue | undefined,
  ) => {
    const label = `${columnName}, row ${rowIndex + 1}`;
    if (type === "checkbox") {
      return (
        <input
          type="checkbox"
          aria-label={label}
          checked={value === true}
          onChange={(e) => setTable((t) => setCell(t, rowId, columnId, e.target.checked))}
          className="h-4 w-4 accent-accent"
        />
      );
    }
    if (type === "number") {
      return (
        <input
          type="number"
          aria-label={label}
          value={typeof value === "number" ? String(value) : ""}
          onChange={(e) =>
            setTable((t) =>
              setCell(t, rowId, columnId, e.target.value === "" ? null : Number(e.target.value)),
            )
          }
          className="w-full border-0 bg-transparent text-[12.5px] text-shell-text outline-none"
        />
      );
    }
    if (type === "date") {
      return (
        <input
          type="date"
          aria-label={label}
          value={typeof value === "string" ? value : ""}
          onChange={(e) => setTable((t) => setCell(t, rowId, columnId, e.target.value))}
          className="w-full border-0 bg-transparent text-[12.5px] text-shell-text outline-none"
        />
      );
    }
    return (
      <input
        type="text"
        aria-label={label}
        value={typeof value === "string" ? value : ""}
        onChange={(e) => setTable((t) => setCell(t, rowId, columnId, e.target.value))}
        className="w-full border-0 bg-transparent text-[12.5px] text-shell-text outline-none placeholder:text-shell-text-tertiary"
      />
    );
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* view header */}
      <div className="flex h-[54px] flex-none items-center gap-3 border-b border-shell-border px-[22px]">
        <h2 className="text-[17px] font-bold tracking-[-0.02em]">Database</h2>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          aria-label="Table title"
          className="w-52 truncate border-0 bg-transparent text-[13px] font-semibold text-shell-text outline-none placeholder:text-shell-text-tertiary"
        />
        <span className="truncate text-[12px] text-shell-text-tertiary">
          {docs.length} table{docs.length === 1 ? "" : "s"} &middot;{" "}
          {activeId ? formatUpdated(updatedAt) : "New table"}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            onClick={newDoc}
            className="flex h-8 items-center gap-1.5 rounded-[9px] border border-shell-border px-3 text-[12px] font-semibold text-shell-text-secondary hover:bg-shell-surface-active focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          >
            <Plus size={14} />
            New
          </button>
          <button
            type="button"
            onClick={saveDoc}
            disabled={saving}
            className="flex h-8 items-center gap-1.5 rounded-[9px] bg-gradient-to-br from-accent to-accent/70 px-3.5 text-[12px] font-bold text-white disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          >
            <Save size={14} />
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </div>

      {/* toolbar: add column / add row */}
      <div className="flex h-[42px] flex-none items-center gap-1.5 overflow-x-auto border-b border-shell-border bg-shell-bg-deep px-3">
        <button
          type="button"
          onClick={() => setTable((t) => addColumn(t))}
          className="flex h-8 flex-none items-center gap-1.5 rounded-lg px-2.5 text-[12px] font-semibold text-shell-text-secondary hover:bg-shell-surface-active hover:text-shell-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
        >
          <Plus size={14} />
          Column
        </button>
        <button
          type="button"
          onClick={() => setTable((t) => addRow(t))}
          className="flex h-8 flex-none items-center gap-1.5 rounded-lg px-2.5 text-[12px] font-semibold text-shell-text-secondary hover:bg-shell-surface-active hover:text-shell-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
        >
          <Plus size={14} />
          Row
        </button>
      </div>

      {error && (
        <p className="border-b border-shell-border px-3.5 py-1.5 text-[12px] text-red-400" role="alert">
          {error}
        </p>
      )}

      <div className="flex min-h-0 flex-1">
        {/* documents sidebar */}
        <aside className="flex w-[200px] flex-none flex-col border-r border-shell-border bg-shell-bg-deep">
          <div className="border-b border-shell-border px-3 py-2 text-[11px] font-bold uppercase tracking-wide text-shell-text-tertiary">
            Tables
          </div>
          <div className="min-h-0 flex-1 overflow-auto p-2">
            {loading && (
              <p className="px-2 py-1 text-[12px] text-shell-text-tertiary">Loading...</p>
            )}
            {!loading && docs.length === 0 && (
              <p className="px-2 py-1 text-[12px] text-shell-text-tertiary">No saved tables yet</p>
            )}
            {docs.map((doc) => (
              <button
                key={doc.id}
                type="button"
                onClick={() => openDoc(doc.id)}
                className={`mb-1 w-full rounded-lg px-2 py-2 text-left text-[12px] transition-colors hover:bg-shell-surface-active focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
                  activeId === doc.id
                    ? "bg-shell-surface text-shell-text"
                    : "text-shell-text-secondary"
                }`}
              >
                <div className="truncate font-semibold">{doc.title}</div>
                <div className="truncate text-[10px] text-shell-text-tertiary">
                  {formatUpdated(doc.updated_at)}
                </div>
              </button>
            ))}
          </div>
        </aside>

        <div className="min-h-0 min-w-0 flex-1 overflow-auto p-[18px]">
          <div className="overflow-auto rounded-[13px] border border-shell-border">
            <table className="w-full border-collapse text-[12.5px]">
              <thead>
                <tr className="border-b border-shell-border bg-shell-bg-deep">
                  {table.columns.map((col) => (
                    <th
                      key={col.id}
                      scope="col"
                      className="min-w-[140px] border-r border-shell-border px-2 py-1.5 text-left align-top"
                    >
                      <div className="flex items-center gap-1">
                        <input
                          value={col.name}
                          onChange={(e) =>
                            setTable((t) => renameColumn(t, col.id, e.target.value))
                          }
                          aria-label="Column name"
                          className="min-w-0 flex-1 border-0 bg-transparent text-[12.5px] font-semibold text-shell-text outline-none"
                        />
                        {table.columns.length > 1 && (
                          <button
                            type="button"
                            aria-label={`Delete column ${col.name}`}
                            onClick={() => setTable((t) => removeColumn(t, col.id))}
                            className="flex h-5 w-5 flex-none items-center justify-center rounded text-shell-text-tertiary hover:bg-shell-surface-active hover:text-shell-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
                          >
                            <X size={12} />
                          </button>
                        )}
                      </div>
                      <select
                        value={col.type}
                        onChange={(e) =>
                          setTable((t) => changeType(t, col.id, e.target.value as ColumnType))
                        }
                        aria-label={`Column type for ${col.name}`}
                        className="mt-1 w-full rounded border-0 bg-transparent text-[10.5px] font-medium text-shell-text-tertiary outline-none"
                      >
                        {COLUMN_TYPES.map((ct) => (
                          <option key={ct.id} value={ct.id}>
                            {ct.label}
                          </option>
                        ))}
                      </select>
                    </th>
                  ))}
                  <th scope="col" className="w-9 flex-none" aria-hidden="true" />
                </tr>
              </thead>
              <tbody>
                {table.rows.length === 0 && (
                  <tr>
                    <td
                      colSpan={table.columns.length + 1}
                      className="px-2 py-3 text-center text-[12px] text-shell-text-tertiary"
                    >
                      No rows yet
                    </td>
                  </tr>
                )}
                {table.rows.map((row, i) => (
                  <tr key={row.id} className="border-b border-shell-border last:border-b-0">
                    {table.columns.map((col) => (
                      <td key={col.id} className="border-r border-shell-border px-2 py-1.5">
                        {renderCellInput(col.id, col.type, row.id, col.name, i, row.cells[col.id])}
                      </td>
                    ))}
                    <td className="px-1 py-1.5 text-center">
                      <button
                        type="button"
                        aria-label={`Delete row ${i + 1}`}
                        onClick={() => setTable((t) => removeRow(t, row.id))}
                        className="flex h-6 w-6 items-center justify-center rounded text-shell-text-tertiary hover:bg-shell-surface-active hover:text-shell-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
                      >
                        <X size={12} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* right sidebar */}
        <aside className="flex w-[262px] flex-none flex-col gap-3.5 border-l border-shell-border bg-shell-bg p-[18px]">
          <div
            className="rounded-[13px] border p-3"
            style={{
              borderColor: "rgba(139,146,163,0.35)",
              background:
                "radial-gradient(120% 130% at 12% 10%, rgba(139,146,163,0.35), transparent 60%), var(--color-shell-surface, rgba(255,255,255,0.045))",
            }}
          >
            <div className="flex items-center gap-1.5 text-[12.5px] font-bold text-shell-text">
              <Sparkles size={15} className="text-accent" />
              Ask your table
            </div>
            <p className="mt-1.5 text-[11.5px] leading-[1.45] text-shell-text-secondary">
              &ldquo;Which rows are missing a due date?&rdquo; taOS reads the table and answers, on
              your hardware.
            </p>
          </div>
        </aside>
      </div>
    </div>
  );
}
