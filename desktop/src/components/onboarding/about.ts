/**
 * "About you" answers, and what setup builds from them: the personal
 * assistant's persona (soul_md) and the three prompts its first chat offers.
 * Built here, client-side, so the first chat needs no extra server round trip.
 */

export const HELP_TOPICS = [
  { id: "email", label: "Email and calendar", prompt: "What's on my calendar this week?" },
  { id: "research", label: "Research", prompt: "Find the latest news on a topic I care about." },
  { id: "writing", label: "Writing", prompt: "Help me write a short note to a friend." },
  { id: "planning", label: "Plans and to-dos", prompt: "Help me plan my week." },
  { id: "learning", label: "Learning", prompt: "Teach me something new in five minutes." },
  { id: "coding", label: "Coding", prompt: "Explain what a piece of code does." },
  { id: "home", label: "Home and devices", prompt: "What can you control on this device?" },
  { id: "health", label: "Health and fitness", prompt: "Suggest a 20 minute workout." },
] as const;

export type HelpTopic = (typeof HELP_TOPICS)[number]["id"];
export type Tone = "warm" | "brief" | "formal";
export type Autonomy = "ask" | "act";

export interface AboutYou {
  help: HelpTopic[];
  tone: Tone;
  autonomy: Autonomy;
}

export const DEFAULT_ABOUT: AboutYou = { help: [], tone: "warm", autonomy: "ask" };

export const TONES: { id: Tone; label: string; detail: string; soul: string }[] = [
  { id: "warm", label: "Friendly", detail: "Warm and conversational.", soul: "Be warm and conversational." },
  { id: "brief", label: "Brief", detail: "Short answers, straight to the point.", soul: "Keep answers short and get straight to the point." },
  { id: "formal", label: "Professional", detail: "Clear, polite and precise.", soul: "Be clear, polite and precise, in a professional register." },
];

export const AUTONOMY: { id: Autonomy; label: string; detail: string; soul: string }[] = [
  { id: "ask", label: "Ask me first", detail: "Check with me before doing anything for me.", soul: "Ask before taking any action on the user's behalf." },
  { id: "act", label: "Just do it", detail: "Go ahead, then tell me what you did.", soul: "Take routine actions without asking, then say what you did." },
];

/** Reads a stored value defensively: answers saved by another build may not fit. */
export function parseAbout(raw: unknown): AboutYou | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const r = raw as Record<string, unknown>;
  const ids = HELP_TOPICS.map((t) => t.id as string);
  return {
    help: Array.isArray(r.help) ? (r.help.filter((h) => ids.includes(h as string)) as HelpTopic[]) : [],
    tone: TONES.some((t) => t.id === r.tone) ? (r.tone as Tone) : DEFAULT_ABOUT.tone,
    autonomy: AUTONOMY.some((a) => a.id === r.autonomy) ? (r.autonomy as Autonomy) : DEFAULT_ABOUT.autonomy,
  };
}

export function buildSoulMd(name: string, about: AboutYou): string {
  const who = name.trim() || "the user";
  const topics = HELP_TOPICS.filter((t) => about.help.includes(t.id)).map((t) => t.label.toLowerCase());
  const lines = [
    `You are ${who}'s personal assistant on their taOS device.`,
    topics.length ? `They mostly want help with: ${topics.join(", ")}.` : "",
    TONES.find((t) => t.id === about.tone)!.soul,
    AUTONOMY.find((a) => a.id === about.autonomy)!.soul,
  ];
  return lines.filter(Boolean).join("\n");
}

/** Three prompts: the picked topics first, in list order, then general ones. */
export function suggestedPrompts(about: AboutYou): string[] {
  const picked = HELP_TOPICS.filter((t) => about.help.includes(t.id)).map((t) => t.prompt);
  const fill = ["planning", "research", "learning"].map(
    (id) => HELP_TOPICS.find((t) => t.id === id)!.prompt,
  );
  return [...new Set([...picked, ...fill])].slice(0, 3);
}
