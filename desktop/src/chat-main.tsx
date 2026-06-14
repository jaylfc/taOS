import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ChatStandalone } from "./ChatStandalone";
import { AppShell } from "./components/AppShell";
import { restoreActiveTheme } from "./stores/theme-store";
import "./theme/tokens.css";

// Apply the user's persisted theme (light/dark/etc.) on boot, the same as the
// desktop shell does in App.tsx. Without this the standalone chat PWA always
// renders the base dark tokens and ignores the user's chosen theme.
void restoreActiveTheme();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppShell>
      <ChatStandalone />
    </AppShell>
  </StrictMode>,
);
