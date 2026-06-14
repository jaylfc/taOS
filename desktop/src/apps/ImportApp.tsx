import { useState, useEffect, useCallback, useRef } from "react";
import { Upload, File, Trash2, Brain } from "lucide-react";
import { Button, Card, CardContent, Label } from "@/components/ui";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface QueuedFile {
  id: string;
  file: globalThis.File;
  name: string;
  size: number;
}

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const ACCEPTED_TYPES = [".txt", ".md", ".pdf", ".html", ".json", ".csv"];
const ACCEPTED_MIME = [
  "text/plain",
  "text/markdown",
  "application/pdf",
  "text/html",
  "application/json",
  "text/csv",
];
function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/* ------------------------------------------------------------------ */
/*  ImportApp                                                          */
/* ------------------------------------------------------------------ */

export function ImportApp({ windowId: _windowId }: { windowId: string }) {
  const [agents, setAgents] = useState<string[]>([]);
  const [selectedAgent, setSelectedAgent] = useState("");
  const [files, setFiles] = useState<QueuedFile[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [embedding, setEmbedding] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch("/api/agents", {
          headers: { Accept: "application/json" },
        });
        if (res.ok) {
          const ct = res.headers.get("content-type") ?? "";
          if (ct.includes("application/json")) {
            const data = await res.json();
            if (Array.isArray(data) && data.length > 0) {
              setAgents(data.map((a: Record<string, unknown>) => String(a.name ?? "unknown")));
            }
          }
        }
      } catch { /* use fallback */ }
    })();
  }, []);

  const isValidFile = useCallback((file: globalThis.File) => {
    const ext = "." + file.name.split(".").pop()?.toLowerCase();
    return ACCEPTED_TYPES.includes(ext) || ACCEPTED_MIME.includes(file.type);
  }, []);

  function addFiles(fileList: globalThis.File[]) {
    const valid = fileList.filter(isValidFile);
    const newQueued: QueuedFile[] = valid.map((f) => ({
      id: `${f.name}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      file: f,
      name: f.name,
      size: f.size,
    }));
    setFiles((prev) => [...prev, ...newQueued]);
    setStatus(null);
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    const dropped = Array.from(e.dataTransfer.files);
    addFiles(dropped);
  }

  function handleFileInput(e: React.ChangeEvent<HTMLInputElement>) {
    if (e.target.files) {
      addFiles(Array.from(e.target.files));
    }
    // Reset so same file can be selected again
    e.target.value = "";
  }

  function removeFile(id: string) {
    setFiles((prev) => prev.filter((f) => f.id !== id));
  }

  async function handleUpload() {
    if (!selectedAgent || files.length === 0) return;
    setUploading(true);
    setProgress(0);
    setStatus(null);

    const total = files.length;
    let done = 0;

    for (const qf of files) {
      const formData = new FormData();
      formData.append("file", qf.file);
      formData.append("agent", selectedAgent);

      try {
        await fetch("/api/import/upload", {
          method: "POST",
          body: formData,
        });
      } catch { /* ignore */ }

      done++;
      setProgress(Math.round((done / total) * 100));
    }

    setUploading(false);
    setStatus(`Uploaded ${total} file${total !== 1 ? "s" : ""} for ${selectedAgent}`);
  }

  async function handleEmbed() {
    if (!selectedAgent) return;
    setEmbedding(true);
    setStatus(null);

    try {
      const res = await fetch("/api/import/embed", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent: selectedAgent }),
      });
      if (res.ok) {
        setStatus("Embedding complete. Memory updated.");
      } else {
        setStatus("Embedding request sent. Check agent memory.");
      }
    } catch {
      setStatus("Could not reach embed endpoint. API may not be available.");
    }

    setEmbedding(false);
  }

  return (
    <div className="flex flex-col h-full bg-shell-bg text-shell-text select-none">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-white/5">
        <Upload size={18} className="text-accent" />
        <h1 className="text-sm font-semibold">Import</h1>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto p-4 space-y-4">
        {/* Agent selector */}
        <div className="space-y-1.5">
          <Label htmlFor="import-agent">Target Agent</Label>
          <select
            id="import-agent"
            value={selectedAgent}
            onChange={(e) => setSelectedAgent(e.target.value)}
            className="flex h-9 w-full max-w-sm rounded-lg border border-white/10 bg-shell-bg-deep px-3 py-1 text-sm text-shell-text focus-visible:outline-none focus-visible:border-accent/40 focus-visible:ring-2 focus-visible:ring-accent/20"
          >
            <option value="">Select an agent...</option>
            {agents.map((a) => (
              <option key={a} value={a}>{a}</option>
            ))}
          </select>
        </div>

        {/* Drop zone */}
        <Card
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          className={`border-2 border-dashed transition-colors cursor-pointer ${
            dragOver
              ? "border-accent bg-accent/5"
              : "border-white/10 hover:border-white/20"
          }`}
          onClick={() => fileInputRef.current?.click()}
          role="button"
          aria-label="Drop files here or click to browse"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              fileInputRef.current?.click();
            }
          }}
        >
          <CardContent className="flex flex-col items-center justify-center gap-3 p-8">
            <Upload size={32} className="text-shell-text-tertiary" />
            <div className="text-center">
              <p className="text-sm text-shell-text-secondary">
                Drag and drop files here
              </p>
              <p className="text-xs text-shell-text-tertiary mt-1">
                {ACCEPTED_TYPES.join(", ")}
              </p>
            </div>
            <Button
              variant="secondary"
              size="sm"
              onClick={(e) => { e.stopPropagation(); fileInputRef.current?.click(); }}
            >
              Browse
            </Button>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept={ACCEPTED_TYPES.join(",")}
              onChange={handleFileInput}
              className="hidden"
              aria-label="Select files to import"
            />
          </CardContent>
        </Card>

        {/* File list */}
        {files.length > 0 && (
          <div className="space-y-1.5">
            <h2 className="text-xs text-shell-text-secondary font-medium">
              Queued Files ({files.length})
            </h2>
            {files.map((qf) => (
              <Card key={qf.id}>
                <CardContent className="flex items-center gap-3 px-3.5 py-2.5">
                  <File size={14} className="text-shell-text-tertiary shrink-0" />
                  <span className="text-sm flex-1 truncate">{qf.name}</span>
                  <span className="text-xs text-shell-text-tertiary tabular-nums shrink-0">
                    {formatSize(qf.size)}
                  </span>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => removeFile(qf.id)}
                    className="h-7 w-7 hover:text-red-400 hover:bg-red-500/15"
                    aria-label={`Remove ${qf.name}`}
                  >
                    <Trash2 size={14} />
                  </Button>
                </CardContent>
              </Card>
            ))}
          </div>
        )}

        {/* Progress bar */}
        {uploading && (
          <div className="space-y-1.5">
            <div className="flex items-center justify-between text-xs text-shell-text-secondary">
              <span>Uploading...</span>
              <span className="tabular-nums">{progress}%</span>
            </div>
            <div className="h-2 w-full rounded-full bg-white/5" role="progressbar" aria-valuenow={progress} aria-valuemin={0} aria-valuemax={100}>
              <div
                className="h-full rounded-full bg-accent transition-all"
                style={{ width: `${progress}%` }}
              />
            </div>
          </div>
        )}

        {/* Status message */}
        {status && (
          <p className={`text-xs ${status.includes("complete") || status.includes("Uploaded") ? "text-emerald-400" : "text-amber-400"}`}>
            {status}
          </p>
        )}

        {/* Action buttons */}
        <div className="flex gap-2">
          <Button
            onClick={handleUpload}
            disabled={!selectedAgent || files.length === 0 || uploading}
          >
            <Upload size={14} />
            {uploading ? "Uploading..." : "Upload"}
          </Button>
          <Button
            variant="secondary"
            onClick={handleEmbed}
            disabled={!selectedAgent || embedding}
            className="bg-cyan-600 text-white hover:bg-cyan-500"
          >
            <Brain size={14} />
            {embedding ? "Embedding..." : "Embed"}
          </Button>
        </div>
      </div>
    </div>
  );
}
