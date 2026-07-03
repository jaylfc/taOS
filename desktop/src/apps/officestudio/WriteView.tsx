import { useCallback, useEffect, useState } from "react";
import {
  Sparkles,
  AlignLeft,
  AlignCenter,
  Pencil,
  Scissors,
  ArrowRight,
  AlignJustify,
  Plus,
  Save,
} from "lucide-react";

const AI_OPTIONS: { label: string; desc: string; Icon: typeof Sparkles }[] = [
  { label: "Rewrite", desc: "Clearer, same meaning", Icon: Pencil },
  { label: "Shorten", desc: "Tighten the selection", Icon: Scissors },
  { label: "Continue writing", desc: "Pick up where you left off", Icon: ArrowRight },
  { label: "Change tone", desc: "Friendly, formal, punchy", Icon: AlignJustify },
];

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

export function WriteView() {
  const [docs, setDocs] = useState<OfficeDocListItem[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [title, setTitle] = useState("Untitled document");
  const [content, setContent] = useState("");
  const [updatedAt, setUpdatedAt] = useState<number | undefined>();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadList = useCallback(async () => {
    const res = await fetch("/api/office/docs", { credentials: "include" });
    if (!res.ok) throw new Error("Could not load documents");
    const items = (await res.json()) as OfficeDocListItem[];
    setDocs(items.filter((d) => d.kind === "write"));
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
    setError(null);
    try {
      const res = await fetch(`/api/office/docs/${encodeURIComponent(docId)}`, {
        credentials: "include",
      });
      if (!res.ok) throw new Error("Could not open document");
      const doc = (await res.json()) as OfficeDoc;
      setActiveId(doc.id);
      setTitle(doc.title);
      setContent(doc.content);
      setUpdatedAt(doc.updated_at);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Open failed");
    }
  };

  const newDoc = () => {
    setActiveId(null);
    setTitle("Untitled document");
    setContent("");
    setUpdatedAt(undefined);
    setError(null);
  };

  const saveDoc = async () => {
    setSaving(true);
    setError(null);
    try {
      const payload = { kind: "write", title: title.trim() || "Untitled document", content };
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
      const saved = (await res.json()) as OfficeDoc;
      setActiveId(saved.id);
      setTitle(saved.title);
      setContent(saved.content);
      setUpdatedAt(saved.updated_at);
      await loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex h-[46px] flex-none items-center gap-1.5 border-b border-shell-border bg-shell-bg-deep px-4">
        <div className="flex h-8 items-center gap-2 rounded-lg border border-shell-border bg-shell-surface px-3 text-[12px] font-semibold text-shell-text-secondary">
          Sohne <span className="text-shell-text-tertiary">&#9662;</span>
        </div>
        <div className="mx-1.5 h-5 w-px bg-shell-border" />
        <button
          type="button"
          aria-label="Bold"
          className="flex h-8 w-8 items-center justify-center rounded-lg text-[14px] font-extrabold text-shell-text-secondary hover:bg-shell-surface-active hover:text-shell-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
        >
          B
        </button>
        <button
          type="button"
          aria-label="Italic"
          className="flex h-8 w-8 items-center justify-center rounded-lg text-[14px] italic text-shell-text-secondary hover:bg-shell-surface-active hover:text-shell-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
        >
          I
        </button>
        <button
          type="button"
          aria-label="Underline"
          className="flex h-8 w-8 items-center justify-center rounded-lg text-[14px] underline text-shell-text-secondary hover:bg-shell-surface-active hover:text-shell-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
        >
          U
        </button>
        <div className="mx-1.5 h-5 w-px bg-shell-border" />
        <button
          type="button"
          aria-label="Align left"
          className="flex h-8 w-8 items-center justify-center rounded-lg text-shell-text-secondary hover:bg-shell-surface-active hover:text-shell-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
        >
          <AlignLeft size={16} />
        </button>
        <button
          type="button"
          aria-label="Align center"
          className="flex h-8 w-8 items-center justify-center rounded-lg text-shell-text-secondary hover:bg-shell-surface-active hover:text-shell-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
        >
          <AlignCenter size={16} />
        </button>
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

      <div className="flex min-h-0 flex-1">
        <aside className="flex w-[200px] flex-none flex-col border-r border-shell-border bg-shell-bg-deep">
          <div className="border-b border-shell-border px-3 py-2 text-[11px] font-bold uppercase tracking-wide text-shell-text-tertiary">
            Documents
          </div>
          <div className="min-h-0 flex-1 overflow-auto p-2">
            {loading && (
              <p className="px-2 py-1 text-[12px] text-shell-text-tertiary">Loading...</p>
            )}
            {!loading && docs.length === 0 && (
              <p className="px-2 py-1 text-[12px] text-shell-text-tertiary">No saved docs yet</p>
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

        <div className="flex flex-1 justify-center overflow-auto bg-shell-bg-deep px-0 py-7">
          <div
            className="min-h-[660px] w-[540px] rounded-[4px] px-14 py-[52px]"
            style={{
              background: "#f7f7f9",
              boxShadow: "0 16px 40px -14px rgba(0,0,0,0.5)",
              color: "#23232a",
            }}
          >
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              aria-label="Document title"
              className="mb-1.5 w-full border-0 bg-transparent font-extrabold leading-tight tracking-tight outline-none"
              style={{ fontSize: 27, letterSpacing: "-0.02em" }}
            />
            <p className="mb-5 text-[12px]" style={{ color: "#5a5a66" }}>
              {formatUpdated(updatedAt)}
            </p>
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              aria-label="Document body"
              placeholder="Start writing..."
              className="min-h-[420px] w-full resize-none border-0 bg-transparent text-[13.5px] leading-[1.75] outline-none"
              style={{ color: "#33333c" }}
            />
            {error && (
              <p className="mt-3 text-[12px] text-red-400" role="alert">
                {error}
              </p>
            )}
          </div>
        </div>

        <aside className="flex w-[262px] flex-none flex-col gap-3 border-l border-shell-border bg-shell-bg p-[18px]">
          <div className="flex items-center gap-2 text-[14px] font-bold">
            <Sparkles size={16} className="text-accent" />
            Assist
          </div>

          {AI_OPTIONS.map(({ label, desc, Icon }) => (
            <button
              key={label}
              type="button"
              className="flex items-center gap-3 rounded-xl border border-shell-border bg-shell-surface px-3 py-[11px] text-left transition-colors hover:border-shell-border-strong hover:bg-shell-surface-active focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
            >
              <Icon size={16} className="shrink-0 text-accent" />
              <div>
                <div className="text-[12.5px] font-semibold text-shell-text">{label}</div>
                <div className="text-[10.5px] text-shell-text-tertiary">{desc}</div>
              </div>
            </button>
          ))}

          <div className="mt-auto min-h-[70px] rounded-xl border border-shell-border-strong bg-shell-surface p-3 text-[12px] text-shell-text-tertiary">
            Ask for any change to the selected paragraph&hellip;
          </div>
        </aside>
      </div>
    </div>
  );
}