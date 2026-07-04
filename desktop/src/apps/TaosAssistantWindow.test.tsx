import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { TaosAssistantWindow } from "./TaosAssistantWindow";
import { useTaosAgentStore } from "@/stores/taos-agent-store";
import { useProcessStore } from "@/stores/process-store";

// Mock child that opens a modal — keeps the test surface narrow, mirrors
// TaosAssistantPanel.test.tsx's approach.
vi.mock("@/components/TaosAssistantSettings", () => ({
  TaosAssistantSettings: ({ open }: { open: boolean }) =>
    open ? <div data-testid="settings-modal" /> : null,
}));

function resetStores() {
  useTaosAgentStore.setState({
    isOpen: false,
    messages: [],
    model: "qwen3",
    streaming: false,
    settingsOpen: false,
  });
  useProcessStore.setState({ windows: [] });
}

describe("TaosAssistantWindow", () => {
  beforeEach(() => {
    resetStores();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("renders the chat shell when launched directly (no popOut prop) — regression for #1615", () => {
    // Direct Launchpad/Dock launch calls openWindow("taos-agent", size) with
    // no props, so windows never carries a matching entry with
    // props.popOut. The window must still show its UI, not go blank.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ model: "qwen3" }) }),
    );

    render(<TaosAssistantWindow windowId="win-1" />);

    expect(screen.getByText("taOS agent")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /message taOS agent/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /send message/i })).toBeInTheDocument();
  });

  it("renders the chat shell when opened via the pop-out button (props.popOut)", () => {
    useProcessStore.setState({
      windows: [
        {
          id: "win-2",
          appId: "taos-agent",
          position: { x: 0, y: 0 },
          size: { w: 420, h: 640 },
          zIndex: 1,
          minimized: false,
          maximized: false,
          snapped: null,
          focused: true,
          props: { popOut: true },
          launchNonce: 0,
        },
      ],
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ model: "qwen3" }) }),
    );

    render(<TaosAssistantWindow windowId="win-2" />);

    expect(screen.getByText("taOS agent")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /message taOS agent/i })).toBeInTheDocument();
  });

  it("surfaces the runtime error inline instead of leaving the dialog blank", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (typeof url === "string" && url.includes("/api/taos-agent/settings")) {
          return Promise.resolve({ ok: true, json: async () => ({ model: "qwen3" }) });
        }
        // /api/taos-agent/chat — simulate the runtime-unavailable 503.
        return Promise.resolve({
          ok: false,
          body: null,
          text: async () =>
            JSON.stringify({ error: "opencode is not installed. Install it with: curl -fsSL https://opencode.ai/install | bash" }),
        });
      }),
    );

    render(<TaosAssistantWindow windowId="win-3" />);

    const textarea = screen.getByRole("textbox", { name: /message taOS agent/i });
    fireEvent.change(textarea, { target: { value: "hello" } });
    fireEvent.click(screen.getByRole("button", { name: /send message/i }));

    // The shell stays up (input still present) and the error is shown inline.
    await waitFor(() => {
      expect(screen.getByText(/opencode is not installed/i)).toBeInTheDocument();
    });
    expect(screen.getByRole("textbox", { name: /message taOS agent/i })).toBeInTheDocument();
  });
});
