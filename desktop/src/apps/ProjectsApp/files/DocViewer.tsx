import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSlug from "rehype-slug";
import rehypeHighlight from "rehype-highlight";
import { Button } from "@/components/ui";
import { OutlinePane, type Heading } from "./OutlinePane";
import styles from "./DocViewer.module.css";

// Lazy-loaded so the (heavy) mermaid diagram renderer stays out of the entry
// chunk for every document that does not contain a diagram.
const MermaidBlock = lazy(() => import("./MermaidBlock"));

interface DocViewerProps {
  url: string;
  title?: string;
  onClose?: () => void;
}

function codeToString(children: React.ReactNode): string {
  if (children == null) return "";
  if (typeof children === "string") return children;
  if (Array.isArray(children)) {
    return children.map((c) => (typeof c === "string" ? c : "")).join("");
  }
  return "";
}

export function DocViewer({ url, title, onClose }: DocViewerProps) {
  const [markdown, setMarkdown] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [headings, setHeadings] = useState<Heading[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [showOutline, setShowOutline] = useState(false);

  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setMarkdown("");
    (async () => {
      try {
        const res = await fetch(url, { credentials: "include" });
        if (!res.ok) throw new Error(`Failed to load document (${res.status})`);
        const text = await res.text();
        if (!cancelled) setMarkdown(text);
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load document");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [url]);

  // Build the outline from the rendered headings so the ids match exactly what
  // rehype-slug assigned (no slug algorithm duplication needed).
  useEffect(() => {
    const root = bodyRef.current;
    if (!root) return;
    const nodes = root.querySelectorAll<HTMLHeadingElement>("h1, h2, h3, h4, h5, h6");
    const found: Heading[] = [];
    nodes.forEach((node) => {
      const id = node.id;
      const text = node.textContent ?? "";
      if (!id) return;
      const level = Number(node.tagName.charAt(1));
      found.push({ id, text, level });
    });
    setHeadings(found);
    setActiveId(found[0]?.id ?? null);
  }, [markdown]);

  const scrollToHeading = useCallback(
    (id: string) => {
      const el = document.getElementById(id);
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "start" });
        setActiveId(id);
      }
      setShowOutline(false);
    },
    [],
  );

  useEffect(() => {
    const root = bodyRef.current;
    if (!root) return;
    const onScroll = () => {
      const nodes = root.querySelectorAll<HTMLHeadingElement>("h1, h2, h3, h4, h5, h6");
      let current: string | null = null;
      nodes.forEach((node) => {
        if (node.getBoundingClientRect().top <= 120) current = node.id;
      });
      if (current) setActiveId(current);
    };
    root.addEventListener("scroll", onScroll, { passive: true });
    return () => root.removeEventListener("scroll", onScroll);
  }, [markdown]);

  const components: Components = {
    // Keep the real <pre> for normal code, but unwrap it for mermaid so the
    // diagram block (a <div>) is not nested inside an invalid <pre>.
    pre({ children }) {
      const child = Array.isArray(children) ? children[0] : children;
      const childClassName =
        child && typeof child === "object" && "props" in child
          ? ((child as React.ReactElement<{ className?: string }>).props.className ?? "")
          : "";
      if (typeof childClassName === "string" && childClassName.includes("language-mermaid")) {
        return <>{children}</>;
      }
      return <pre>{children}</pre>;
    },
    code({ className, children, ...props }) {
      const match = /language-(\w+)/.exec(className ?? "");
      const lang = match?.[1];
      const text = codeToString(children);
      if (lang === "mermaid") {
        return (
          <Suspense fallback={<div className={styles.mermaidFallback}>Loading diagram…</div>}>
            <MermaidBlock code={text.replace(/\n+$/, "")} />
          </Suspense>
        );
      }
      return (
        <code className={className} {...props}>
          {children}
        </code>
      );
    },
  };

  return (
    <div className={styles.viewer} role="dialog" aria-label={title ?? "Document viewer"}>
      <div className={styles.toolbar}>
        <div className={styles.toolbarTitle} title={title}>
          {title ?? "Document"}
        </div>
        <div className={styles.toolbarActions}>
          {headings.length > 0 && (
            <Button
              variant="ghost"
              size="sm"
              className={styles.outlineToggle}
              onClick={() => setShowOutline((v) => !v)}
            >
              Outline
            </Button>
          )}
          {onClose && (
            <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close document">
              Close
            </Button>
          )}
        </div>
      </div>

      <div className={styles.layout}>
        <aside
          className={
            styles.outline +
            (showOutline ? " " + styles.outlineOpen : "")
          }
        >
          <OutlinePane
            headings={headings}
            activeId={activeId}
            onSelect={scrollToHeading}
            onClose={() => setShowOutline(false)}
          />
        </aside>

        <div className={styles.body} ref={bodyRef}>
          {loading && <div className={styles.status}>Loading…</div>}
          {error && <div className={styles.statusError}>{error}</div>}
          {!loading && !error && markdown.length === 0 && (
            <div className={styles.status}>This document is empty.</div>
          )}
          {!loading && !error && markdown.length > 0 && (
            <article className={styles.markdown}>
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeSlug, rehypeHighlight]}
                components={components}
              >
                {markdown}
              </ReactMarkdown>
            </article>
          )}
        </div>
      </div>
    </div>
  );
}
