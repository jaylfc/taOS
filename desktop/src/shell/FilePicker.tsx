import { useEffect, useRef, useState } from "react";
import { VfsBrowser } from "./VfsBrowser";

export type FileSelection =
  | { source: "disk"; file: File }
  | { source: "workspace"; path: string }
  | { source: "agent-workspace"; slug: string; path: string };

type Source = "disk" | "workspace" | "agent-workspace";

export function FilePicker({
  sources,
  accept,
  multi = false,
  onPick,
  onCancel,
}: {
  sources: Source[];
  accept?: string;
  multi?: boolean;
  onPick: (selections: FileSelection[]) => void;
  onCancel: () => void;
}) {
  const [activeTab, setActiveTab] = useState<Source>(sources[0] ?? "disk");
  const [queued, setQueued] = useState<FileSelection[]>([]);
  const [agents, setAgents] = useState<{ name: string }[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (sources.includes("agent-workspace")) {
      fetch("/api/agents")
        .then((r) => r.json())
        .then((list) => setAgents(Array.isArray(list) ? list : []))
        .catch(() => {});
    }
  }, [sources]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); onCancel(); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onCancel]);

  const onDiskFiles = (files: FileList | null) => {
    if (!files) return;
    const selections: FileSelection[] = [];
    for (const f of Array.from(files)) {
      selections.push({ source: "disk", file: f });
    }
    setQueued((prev) => multi ? [...prev, ...selections] : selections);
  };

  // VfsBrowser sends the full current selection on each toggle, so we
  // replace — not append — the VFS-sourced subset of the queue. Disk
  // items stay untouched because they come from a different source.
  const onWorkspacePick = (path: string | string[]) => {
    const paths = Array.isArray(path) ? path : [path];
    const selections: FileSelection[] = paths.map((p) => ({ source: "workspace", path: p }));
    setQueued((prev) => {
      if (!multi) return selections;
      return [...prev.filter((s) => s.source !== "workspace"), ...selections];
    });
  };

  const onAgentWorkspacePick = (path: string | string[]) => {
    if (!selectedAgent) return;
    const paths = Array.isArray(path) ? path : [path];
    const selections: FileSelection[] = paths.map((p) => ({
      source: "agent-workspace" as const, slug: selectedAgent, path: p,
    }));
    setQueued((prev) => {
      if (!multi) return selections;
      return [...prev.filter((s) => s.source !== "agent-workspace"), ...selections];
    });
  };

  const confirm = () => onPick(queued);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Pick a file"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
    >
      <div className="bg-shell-surface border border-white/10 rounded-xl w-[720px] h-[540px] flex flex-col overflow-hidden">
        <div role="tablist" className="flex border-b border-white/10">
          {sources.includes("disk") && (
            <button
              role="tab"
              aria-selected={activeTab === "disk"}
              className={`px-4 py-2 text-sm ${activeTab === "disk" ? "border-b-2 border-sky-400" : "opacity-70"}`}
              onClick={() => setActiveTab("disk")}
            >
              Disk
            </button>
          )}
          {sources.includes("workspace") && (
            <button
              role="tab"
              aria-selected={activeTab === "workspace"}
              className={`px-4 py-2 text-sm ${activeTab === "workspace" ? "border-b-2 border-sky-400" : "opacity-70"}`}
              onClick={() => setActiveTab("workspace")}
            >
              My workspace
            </button>
          )}
          {sources.includes("agent-workspace") && (
            <button
              role="tab"
              aria-selected={activeTab === "agent-workspace"}
              className={`px-4 py-2 text-sm ${activeTab === "agent-workspace" ? "border-b-2 border-sky-400" : "opacity-70"}`}
              onClick={() => setActiveTab("agent-workspace")}
            >
              Agent workspaces
            </button>
          )}
        </div>

        <div className="flex-1 overflow-hidden">
          {activeTab === "disk" && (
            <div className="p-6 flex items-center justify-center">
              <input
                ref={fileInputRef}
                type="file"
                className="hidden"
                multiple={multi}
                accept={accept}
                onChange={(e) => onDiskFiles(e.target.files)}
              />
              <button
                onClick={() => fileInputRef.current?.click()}
                className="px-4 py-2 bg-sky-500/20 text-sky-200 rounded"
              >
                Choose files from disk
              </button>
              {queued.filter((q) => q.source === "disk").length > 0 && (
                <div className="ml-6 text-xs text-shell-text-tertiary">
                  {queued.length} file(s) queued
                </div>
              )}
            </div>
          )}

          {activeTab === "workspace" && (
            <VfsBrowser root="/workspaces/user" onSelect={onWorkspacePick} multi={multi} />
          )}

          {activeTab === "agent-workspace" && (
            <div className="h-full flex flex-col">
              <div className="p-2 border-b border-white/10">
                <select
                  value={selectedAgent ?? ""}
                  onChange={(e) => setSelectedAgent(e.target.value || null)}
                  className="bg-white/5 border border-white/10 rounded px-2 py-1 text-sm"
                >
                  <option value="">Pick an agent…</option>
                  {agents.map((a) => (
                    <option key={a.name} value={a.name}>@{a.name}</option>
                  ))}
                </select>
              </div>
              {selectedAgent && (
                <VfsBrowser
                  root={`/workspaces/agent/${selectedAgent}`}
                  onSelect={onAgentWorkspacePick}
                  multi={multi}
                />
              )}
            </div>
          )}
        </div>

        <div className="border-t border-white/10 p-2 flex items-center justify-end gap-2 text-sm">
          <span className="opacity-60 mr-auto">{queued.length} selected</span>
          <button onClick={onCancel} className="px-3 py-1 opacity-70 hover:opacity-100">
            Cancel
          </button>
          <button
            onClick={confirm}
            disabled={queued.length === 0}
            className="px-3 py-1 bg-sky-500/30 text-sky-200 rounded disabled:opacity-40"
          >
            Select ({queued.length})
          </button>
        </div>
      </div>
    </div>
  );
}
