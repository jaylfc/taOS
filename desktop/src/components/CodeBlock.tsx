import { useState } from "react";
import { Check, Copy } from "lucide-react";

interface Props {
  code: string;
  language?: string;
}

export function CodeBlock({ code }: Props) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // ignore
    }
  };

  return (
    <div className="relative group my-1.5 rounded-lg bg-shell-bg-deep border border-white/10 overflow-x-auto">
      <button
        onClick={handleCopy}
        aria-label={copied ? "Copied" : "Copy code"}
        className="absolute top-1.5 right-1.5 p-1 rounded opacity-0 group-hover:opacity-100 focus:opacity-100 bg-shell-surface border border-white/10 text-shell-text-secondary hover:text-shell-text transition-opacity"
      >
        {copied ? <Check size={12} /> : <Copy size={12} />}
      </button>
      <pre className="text-[12px] font-mono text-shell-text-secondary p-3 pr-8 whitespace-pre-wrap break-words select-text">
        {code}
      </pre>
    </div>
  );
}
