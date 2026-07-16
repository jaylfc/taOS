import { useEffect, useRef, useState } from "react";

interface MermaidBlockProps {
  code: string;
  theme?: "default" | "dark" | "forest" | "neutral";
}

// Mermaid is heavy (~600 kB). Load it lazily via a dynamic `import()` so it
// never lands in the DocViewer entry chunk; it is only fetched the first time
// a diagram is actually rendered. DocViewer imports this module through
// `React.lazy`, keeping the wrapper itself out of the entry chunk too.
let initialized = false;

export default function MermaidBlock({ code, theme = "dark" }: MermaidBlockProps) {
  const [svg, setSvg] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    const source = code.replace(/\n+$/, "");

    (async () => {
      try {
        const mermaid = (await import("mermaid")).default as typeof import("mermaid").default;
        if (!initialized) {
          mermaid.initialize({ startOnLoad: false, theme, securityLevel: "strict" });
          initialized = true;
        }
        const id = `mermaid-${Math.random().toString(36).slice(2)}`;
        const { svg: rendered } = await mermaid.render(id, source);
        if (!cancelled) setSvg(rendered);
      } catch (e: unknown) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Failed to render diagram");
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [code, theme]);

  if (error) {
    return (
      <pre data-mermaid-error="true" className="mermaid-error">
        {error}
      </pre>
    );
  }

  return (
    <div ref={ref} className="mermaid-block" data-mermaid="true" dangerouslySetInnerHTML={{ __html: svg }} />
  );
}
