import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ChatStandalone } from "./ChatStandalone";
import { AppShell } from "./components/AppShell";
import { restoreActiveTheme, installWebkitRepaintGuards } from "./stores/theme-store";
import "./theme/tokens.css";

// Apply the user's persisted theme (light/dark/etc.) on boot, the same as the
// desktop shell does in App.tsx. Without this the standalone chat PWA always
// renders the base dark tokens and ignores the user's chosen theme.
void restoreActiveTheme();
// WebKit blanks backdrop-filter surfaces when the tab is backgrounded then shown
// again; re-composite on return (same fix the desktop shell installs).
installWebkitRepaintGuards();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppShell>
      <ChatStandalone />
    </AppShell>
  </StrictMode>,
);
