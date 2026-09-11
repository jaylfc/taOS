import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { copyText } from "@/lib/clipboard";

interface Props {
  code: string;
  language?: string;
}

export function CodeBlock({ code }: Props) {
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);

  const handleCopy = async () => {
    const ok = await copyText(code);
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } else {
      setCopyError(true);
      setTimeout(() => setCopyError(false), 2000);
    }
  };

  return (
    <div className="relative group my-1.5 rounded-lg bg-shell-bg-deep border border-white/10 overflow-x-auto">
      <button
        onClick={handleCopy}
        aria-label={copyError ? "Copy failed" : copied ? "Copied" : "Copy code"}
        className="absolute top-1.5 right-1.5 p-1 rounded opacity-0 group-hover:opacity-100 focus:opacity-100 bg-shell-surface border border-white/10 text-shell-text-secondary hover:text-shell-text transition-opacity"
      >
        {copied ? <Check size={12} /> : <Copy size={12} />}
      </button>
      {copyError && (
        <span className="absolute top-1.5 right-10 text-[10px] text-red-400 bg-red-900/30 px-1 rounded">
          Copy failed
        </span>
      )}
      <pre className="text-[12px] font-mono text-shell-text-secondary p-3 pr-8 whitespace-pre-wrap break-words select-text">
        {code}
      </pre>
    </div>
  );
}
