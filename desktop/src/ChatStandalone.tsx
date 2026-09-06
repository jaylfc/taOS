import { Suspense, lazy, useState, useCallback } from "react";
import { InstallPromptBanner } from "./shell/InstallPromptBanner";
import { useIsMobile } from "./hooks/use-is-mobile";
import { MobileChatTabs, CHAT_TAB_APP_IDS, type ChatTab } from "./components/mobile/MobileChatTabs";

const MessagesApp = lazy(() => import("./apps/MessagesApp").then((m) => ({ default: m.MessagesApp })));

export function ChatStandalone() {
  const isMobile = useIsMobile();
  const [tab, setTab] = useState<ChatTab>("chats");

  const handleSelectTab = useCallback((next: ChatTab) => {
    if (next === "chats") {
      setTab("chats");
      return;
    }
    // Sibling platform apps live in the desktop shell; deep-link there so the
    // chat PWA stays a focused comms surface rather than re-implementing
    // Projects/Decisions/Agents inline.
    const appId = CHAT_TAB_APP_IDS[next];
    const target = `/desktop?app=${encodeURIComponent(appId)}`;
    if (typeof window !== "undefined") {
      window.location.href = target;
    }
  }, []);

  return (
    <div
      className="w-screen flex flex-col overflow-hidden"
      style={{
        // 100dvh exactly fills the visible standalone area; 100vh (h-screen)
        // resolves to the larger viewport in an installed iOS PWA and leaves
        // dead space at the bottom.
        height: "100dvh",
        backgroundColor: "var(--color-shell-bg)",
        paddingTop: "env(safe-area-inset-top, 0px)",
      }}
    >
      <InstallPromptBanner />
      <div className="flex-1 min-h-0 overflow-hidden">
        <Suspense fallback={
          <div className="flex items-center justify-center h-full" style={{ color: "rgba(255,255,255,0.4)" }}>
            Loading…
          </div>
        }>
          <MessagesApp windowId="standalone-chat" title="taOS talk" />
        </Suspense>
      </div>
      {isMobile && <MobileChatTabs active={tab} onSelect={handleSelectTab} />}
    </div>
  );
}
