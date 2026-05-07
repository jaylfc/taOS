import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ChatStandalone } from "./ChatStandalone";
import { AppShell } from "./components/AppShell";
import "./theme/tokens.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppShell>
      <ChatStandalone />
    </AppShell>
  </StrictMode>,
);
