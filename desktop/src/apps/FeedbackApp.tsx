import { useState, useEffect, useRef, useCallback } from "react";
import { Flag, ImagePlus, X, CheckCircle2, AlertCircle, Clock } from "lucide-react";
import { Button, Input, Label, Textarea } from "@/components/ui";

type FeedbackType = "bug" | "feature";

interface FeedbackItem {
  id: string;
  type: FeedbackType;
  title: string;
  body: string;
  app: string;
  created_at: string;
  has_screenshot: boolean;
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

function TypeToggle({
  value,
  onChange,
}: {
  value: FeedbackType;
  onChange: (v: FeedbackType) => void;
}) {
  return (
    <div
      className="inline-flex rounded-lg border border-shell-border bg-shell-bg-deep p-0.5"
      role="group"
      aria-label="Feedback type"
    >
      {(["bug", "feature"] as const).map((t) => (
        <button
          key={t}
          type="button"
          onClick={() => onChange(t)}
          className={[
            "rounded-md px-4 py-1.5 text-sm font-medium transition-colors",
            value === t
              ? "bg-shell-surface text-shell-text shadow-sm"
              : "text-shell-text-secondary hover:text-shell-text",
          ].join(" ")}
          aria-pressed={value === t}
        >
          {t === "bug" ? "Bug Report" : "Feature Request"}
        </button>
      ))}
    </div>
  );
}

function ScreenshotPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);

  function readFile(file: File) {
    const reader = new FileReader();
    reader.onload = (e) => {
      const result = e.target?.result;
      if (typeof result === "string") onChange(result);
    };
    reader.readAsDataURL(file);
  }

  function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) readFile(file);
    e.target.value = "";
  }

  function handlePaste(e: React.ClipboardEvent<HTMLDivElement>) {
    for (const item of Array.from(e.clipboardData.items)) {
      if (item.type.startsWith("image/")) {
        const file = item.getAsFile();
        if (file) {
          readFile(file);
          e.preventDefault();
          return;
        }
      }
    }
  }

  if (value) {
    return (
      <div className="relative inline-block" onPaste={handlePaste}>
        <img
          src={value}
          alt="Screenshot preview"
          className="max-h-40 rounded-lg border border-shell-border object-contain"
        />
        <button
          type="button"
          onClick={() => onChange("")}
          className="absolute -right-2 -top-2 rounded-full bg-shell-bg-deep p-0.5 text-shell-text-secondary ring-1 ring-shell-border hover:text-shell-text"
          aria-label="Remove screenshot"
        >
          <X size={14} />
        </button>
      </div>
    );
  }

  return (
    <div
      className="flex items-center gap-2"
      onPaste={handlePaste}
      tabIndex={0}
      aria-label="Screenshot paste target"
    >
      <input
        ref={fileRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={handleFile}
        aria-label="Choose screenshot file"
      />
      <button
        type="button"
        onClick={() => fileRef.current?.click()}
        className="flex items-center gap-1.5 rounded-lg border border-dashed border-shell-border px-3 py-2 text-sm text-shell-text-secondary hover:border-shell-border-strong hover:text-shell-text transition-colors"
      >
        <ImagePlus size={14} />
        Add screenshot
      </button>
      <span className="text-xs text-shell-text-tertiary">or paste from clipboard</span>
    </div>
  );
}

function TypeBadge({ type }: { type: FeedbackType }) {
  return (
    <span
      className={[
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
        type === "bug"
          ? "bg-red-500/10 text-red-400"
          : "bg-blue-500/10 text-blue-400",
      ].join(" ")}
    >
      {type === "bug" ? "Bug" : "Feature"}
    </span>
  );
}

export function FeedbackApp({ windowId: _windowId }: { windowId: string }) {
  const [fbType, setFbType] = useState<FeedbackType>("bug");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [screenshot, setScreenshot] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [history, setHistory] = useState<FeedbackItem[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(true);

  const loadHistory = useCallback(async () => {
    try {
      const res = await fetch("/api/feedback");
      if (res.ok) {
        setHistory(await res.json());
      }
    } catch {
      // History is non-critical; silently skip on network error.
    } finally {
      setLoadingHistory(false);
    }
  }, []);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(false);

    const trimmedTitle = title.trim();
    if (!trimmedTitle) {
      setError("Title is required.");
      return;
    }

    setSubmitting(true);
    try {
      const res = await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          type: fbType,
          title: trimmedTitle,
          body,
          screenshot,
        }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        const detail = data?.detail;
        const msg =
          typeof detail === "string"
            ? detail
            : Array.isArray(detail)
              ? detail.map((d: { msg?: string }) => d.msg).join(", ")
              : "Submission failed. Please try again.";
        setError(msg);
        return;
      }

      setSuccess(true);
      setTitle("");
      setBody("");
      setScreenshot("");
      await loadHistory();
    } catch {
      setError("Could not reach the server.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex h-full flex-col overflow-hidden bg-shell-bg">
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-shell-border px-5 py-4">
        <Flag size={18} className="text-accent" />
        <h1 className="text-base font-semibold text-shell-text">Feedback</h1>
      </div>

      <div className="flex flex-1 flex-col gap-0 overflow-y-auto">
        {/* Form section */}
        <form onSubmit={handleSubmit} className="flex flex-col gap-4 px-5 py-5">
          {/* Type toggle */}
          <div className="flex flex-col gap-1.5">
            <Label className="text-xs font-medium text-shell-text-secondary">Type</Label>
            <TypeToggle value={fbType} onChange={setFbType} />
          </div>

          {/* Title */}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="fb-title" className="text-xs font-medium text-shell-text-secondary">
              Title <span className="text-red-400">*</span>
            </Label>
            <Input
              id="fb-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={
                fbType === "bug"
                  ? "Short description of the issue"
                  : "What feature would you like?"
              }
              maxLength={300}
              className="bg-shell-surface border-shell-border text-shell-text placeholder:text-shell-text-tertiary"
              aria-required="true"
            />
          </div>

          {/* Description */}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="fb-body" className="text-xs font-medium text-shell-text-secondary">
              Description
            </Label>
            <Textarea
              id="fb-body"
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder={
                fbType === "bug"
                  ? "Steps to reproduce, what you expected, what actually happened..."
                  : "Describe the feature and why it would be useful..."
              }
              rows={4}
              maxLength={20000}
              className="resize-none bg-shell-surface border-shell-border text-shell-text placeholder:text-shell-text-tertiary"
            />
          </div>

          {/* Screenshot */}
          <div className="flex flex-col gap-1.5">
            <Label className="text-xs font-medium text-shell-text-secondary">
              Screenshot (optional)
            </Label>
            <ScreenshotPicker value={screenshot} onChange={setScreenshot} />
          </div>

          {/* Feedback messages */}
          {success && (
            <div
              className="flex items-center gap-2 rounded-lg border border-green-500/20 bg-green-500/10 px-4 py-3 text-sm text-green-400"
              role="status"
            >
              <CheckCircle2 size={16} className="shrink-0" />
              Thanks for the feedback!
            </div>
          )}
          {error && (
            <div
              className="flex items-center gap-2 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-3 text-sm text-red-400"
              role="alert"
            >
              <AlertCircle size={16} className="shrink-0" />
              {error}
            </div>
          )}

          <div className="flex justify-end">
            <Button type="submit" disabled={submitting} className="min-w-[100px]">
              {submitting ? "Sending..." : "Submit"}
            </Button>
          </div>
        </form>

        {/* Past submissions */}
        <div className="border-t border-shell-border px-5 py-4">
          <h2 className="mb-3 text-xs font-medium uppercase tracking-wide text-shell-text-tertiary">
            Your submissions
          </h2>

          {loadingHistory ? (
            <p className="text-sm text-shell-text-tertiary">Loading...</p>
          ) : history.length === 0 ? (
            <p className="text-sm text-shell-text-tertiary">No submissions yet.</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {history.map((item) => (
                <li
                  key={item.id}
                  className="flex items-start gap-3 rounded-lg border border-shell-border bg-shell-surface px-4 py-3"
                >
                  <div className="flex flex-1 flex-col gap-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <TypeBadge type={item.type} />
                      <span className="truncate text-sm font-medium text-shell-text">
                        {item.title}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 text-xs text-shell-text-tertiary">
                      <Clock size={11} className="shrink-0" />
                      {relativeTime(item.created_at)}
                      {item.has_screenshot && (
                        <span className="text-shell-text-secondary">
                          &middot; has screenshot
                        </span>
                      )}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
