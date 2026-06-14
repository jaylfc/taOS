import { useState } from "react";

export const SENSITIVE_CAPS = new Set(["app.net", "app.agent", "app.llm", "app.memory"]);

const LABELS: Record<string, string> = {
  "app.net": "Network access (make web requests)",
  "app.agent": "Call your agents",
  "app.llm": "Use the AI model",
  "app.memory": "Read your memory",
};

interface Props {
  appName: string;
  requested: string[];
  onConfirm: (granted: string[]) => void;
  onCancel: () => void;
}

export function PermissionConsent({ appName, requested, onConfirm, onCancel }: Props) {
  const sensitive = requested.filter((c) => SENSITIVE_CAPS.has(c));
  const [granted, setGranted] = useState<Record<string, boolean>>({});

  const toggle = (cap: string) =>
    setGranted((g) => ({ ...g, [cap]: !g[cap] }));

  return (
    <div className="p-4 space-y-3" role="dialog" aria-label={`Permissions for ${appName}`}>
      <h2 className="text-sm font-semibold">{appName} wants permission to:</h2>
      <ul className="text-[13px] text-white/60">
        <li>• Store its own data (always allowed)</li>
        <li>• Show notifications (always allowed)</li>
      </ul>
      {sensitive.length > 0 && (
        <div className="space-y-2">
          {sensitive.map((cap) => (
            <label key={cap} className="flex items-center gap-2 text-[13px]">
              <input type="checkbox" aria-label={LABELS[cap] ?? cap}
                     checked={!!granted[cap]} onChange={() => toggle(cap)} />
              <span>⚠ {LABELS[cap] ?? cap}</span>
            </label>
          ))}
        </div>
      )}
      <div className="flex gap-2 justify-end">
        <button onClick={onCancel}>Cancel</button>
        <button onClick={() => onConfirm(sensitive.filter((c) => granted[c]))}>Install</button>
      </div>
    </div>
  );
}
