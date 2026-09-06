import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import rehypeSlug from "rehype-slug";
import "highlight.js/styles/github-dark.css";

export function HelpPanel({ onClose }: { onClose: () => void }) {
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ac = new AbortController();
    fetch("/api/docs/chat-guide", { signal: ac.signal })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => setMarkdown(data.markdown ?? ""))
      .catch((e) => {
        if ((e as Error).name === "AbortError") return;
        setError(e instanceof Error ? e.message : "failed");
      });
    return () => ac.abort();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-label="Chat guide"
      aria-modal="true"
      className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center"
      onClick={onClose}
      data-testid="help-panel-backdrop"
    >
      <div
        className="bg-shell-surface border border-white/10 rounded-lg shadow-xl w-[720px] max-w-[95vw] max-h-[85vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between px-4 py-3 border-b border-white/10 flex-shrink-0">
          <h2 className="text-sm font-semibold">Chat guide</h2>
          <button onClick={onClose} aria-label="Close" className="text-lg leading-none opacity-60 hover:opacity-100">×</button>
        </header>
        <div className="flex-1 overflow-y-auto px-6 py-4 help-panel-content text-sm text-shell-text-secondary leading-relaxed">
          {error && (
            <div role="alert" className="text-xs text-red-300 bg-red-500/10 border border-red-500/30 rounded px-2 py-2">
              Failed to load guide: {error}
            </div>
          )}
          {!error && markdown === null && (
            <div className="text-xs text-shell-text-tertiary">Loading…</div>
          )}
          {!error && markdown !== null && (
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              rehypePlugins={[rehypeSlug, rehypeHighlight]}
              components={{
                h1: ({ node, ...props }) => <h1 className="text-lg font-bold text-white mt-4 mb-3" {...props} />,
                h2: ({ node, ...props }) => <h2 className="text-base font-semibold text-white mt-6 mb-2" {...props} />,
                h3: ({ node, ...props }) => <h3 className="text-sm font-semibold text-white mt-4 mb-2" {...props} />,
                a: ({ node, href, ...props }) => {
                  // Internal/fragment links open in-modal; external links open in a new tab.
                  const external = typeof href === "string" && /^(https?:)?\/\//i.test(href);
                  return (
                    <a
                      {...props}
                      href={href}
                      className="text-sky-300 hover:text-sky-200 underline"
                      target={external ? "_blank" : undefined}
                      rel={external ? "noopener noreferrer" : undefined}
                    />
                  );
                },
                code: ({ node, className, children, ...props }) => (
                  // react-markdown v10 no longer passes an `inline` prop; distinguish
                  // via the presence of a language-* class on block code (added by
                  // rehype-highlight + the CodeMirror fenced fence), falling back to
                  // inline styling when absent.
                  className && /language-/.test(className) ? (
                    <code className={className} {...props}>{children}</code>
                  ) : (
                    <code className="bg-white/5 px-1 rounded text-xs" {...props}>{children}</code>
                  )
                ),
                pre: ({ node, ...props }) => (
                  <pre className="bg-black/40 border border-white/10 rounded-md p-3 my-3 overflow-x-auto text-xs" {...props} />
                ),
                table: ({ node, ...props }) => (
                  <div className="overflow-x-auto my-3">
                    <table className="min-w-full border-collapse text-xs" {...props} />
                  </div>
                ),
                thead: ({ node, ...props }) => <thead className="bg-white/5" {...props} />,
                th: ({ node, ...props }) => <th className="border border-white/10 px-3 py-1 text-left font-semibold text-white" {...props} />,
                td: ({ node, ...props }) => <td className="border border-white/10 px-3 py-1 align-top" {...props} />,
                ul: ({ node, ...props }) => <ul className="list-disc pl-5 my-2 space-y-1" {...props} />,
                ol: ({ node, ...props }) => <ol className="list-decimal pl-5 my-2 space-y-1" {...props} />,
                blockquote: ({ node, ...props }) => <blockquote className="border-l-2 border-white/20 pl-3 my-2 text-shell-text-tertiary italic" {...props} />,
                hr: () => <hr className="my-4 border-white/10" />,
                p: ({ node, ...props }) => <p className="my-2" {...props} />,
              }}
            >
              {markdown}
            </ReactMarkdown>
          )}
        </div>
      </div>
    </div>
  );
}
