import { MessageCircle, FolderKanban, Inbox, Bot } from "lucide-react";
import type { ReactNode } from "react";

export type ChatTab = "chats" | "projects" | "decisions" | "agents";

const TABS: { id: ChatTab; label: string; icon: ReactNode; appId: string }[] = [
  { id: "chats", label: "Chats", icon: <MessageCircle size={20} strokeWidth={1.8} />, appId: "messages" },
  { id: "projects", label: "Projects", icon: <FolderKanban size={20} strokeWidth={1.8} />, appId: "projects" },
  { id: "decisions", label: "Decisions", icon: <Inbox size={20} strokeWidth={1.8} />, appId: "decisions" },
  { id: "agents", label: "Agents", icon: <Bot size={20} strokeWidth={1.8} />, appId: "agents" },
];

interface Props {
  active: ChatTab;
  onSelect: (tab: ChatTab) => void;
}

/**
 * Bottom tab bar for the chat PWA on mobile. The Chats tab is the local
 * MessagesApp view; the other three are sibling platform apps reached via
 * deep link to the desktop shell (`/desktop?app=<id>`), so the chat PWA
 * stays a focused comms surface without re-implementing them inline.
 */
export function MobileChatTabs({ active, onSelect }: Props) {
  return (
    <nav
      aria-label="Section"
      data-testid="mobile-chat-tabs"
      className="shrink-0 flex items-stretch justify-around"
      style={{
        height: 56,
        backgroundColor: "var(--color-shell-bg-glass, var(--color-dock-bg))",
        borderTop: "1px solid var(--color-shell-border)",
        backdropFilter: "blur(20px) saturate(180%)",
        WebkitBackdropFilter: "blur(20px) saturate(180%)",
        paddingBottom: "env(safe-area-inset-bottom, 0px)",
      }}
    >
      {TABS.map((tab) => {
        const isActive = tab.id === active;
        return (
          <button
            key={tab.id}
            type="button"
            onClick={() => onSelect(tab.id)}
            aria-current={isActive ? "page" : undefined}
            aria-label={tab.label}
            data-testid={`mobile-chat-tab-${tab.id}`}
            className="flex-1 flex flex-col items-center justify-center gap-0.5 min-w-0 px-2 active:opacity-60 transition-opacity"
            style={{
              color: isActive ? "var(--color-accent, rgb(100, 180, 255))" : "var(--color-shell-text-tertiary, rgba(255,255,255,0.55))",
            }}
          >
            <span className="flex items-center justify-center" aria-hidden="true">{tab.icon}</span>
            <span className="text-[10px] font-medium leading-none truncate w-full text-center">{tab.label}</span>
          </button>
        );
      })}
    </nav>
  );
}

export const CHAT_TAB_APP_IDS: Record<ChatTab, string> = TABS.reduce(
  (acc, t) => ({ ...acc, [t.id]: t.appId }),
  {} as Record<ChatTab, string>,
);
