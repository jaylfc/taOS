/**
 * Two-line strip rendered between the last message and the composer.
 * Line 1: humans-typing. Line 2: agent-thinking.
 * Caller feeds in live arrays — they're empty when nothing is active
 * and the component renders nothing.
 */

type TypingPhase = "thinking" | "tool" | "reading" | "writing" | "searching" | "planning";

export interface AgentTyping {
  slug: string;
  phase?: TypingPhase | null;
  detail?: string | null;
}

export function TypingFooter({
  humans,
  agents,
  selfId = "user",
}: {
  humans: string[];
  agents: AgentTyping[];
  selfId?: string;
}) {
  const others = humans.filter((h) => h !== selfId);
  const hasHumans = others.length > 0;
  const hasAgents = agents.length > 0;
  if (!hasHumans && !hasAgents) return null;

  const humanLine = formatHumansTyping(others);

  return (
    <div
      aria-live="polite"
      className="px-4 pt-1 text-xs text-shell-text-tertiary flex flex-col gap-0.5"
    >
      {humanLine && <span>{humanLine}</span>}
      {agents.map((a) => {
        const { icon, text } = phaseLabel(a.phase, a.detail);
        return (
          <span key={a.slug} className="italic">
            {icon} @{a.slug} is {text}…
          </span>
        );
      })}
    </div>
  );
}

function formatHumansTyping(names: string[]): string | null {
  if (names.length === 0) return null;
  if (names.length === 1) return `${names[0]} is typing…`;
  if (names.length === 2) return `${names[0]} and ${names[1]} are typing…`;
  return `${names[0]} and ${names.length - 1} others are typing…`;
}

function phaseLabel(
  phase?: TypingPhase | null,
  detail?: string | null,
): { icon: string; text: string } {
  const d = detail
    ? detail.length > 40
      ? detail.slice(0, 39) + "…"
      : detail
    : null;
  switch (phase) {
    case "tool":
      return { icon: "🔧", text: d ? `using ${d}` : "using a tool" };
    case "reading":
      return { icon: "📖", text: d ? `reading ${d}` : "reading" };
    case "writing":
      return { icon: "✏️", text: d ? `writing ${d}` : "writing" };
    case "searching":
      return { icon: "🔍", text: d ? `searching ${d}` : "searching" };
    case "planning":
      return { icon: "📋", text: "planning" };
    case "thinking":
    default:
      return { icon: "💭", text: "thinking" };
  }
}
