import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { TaosAssistantPanel } from "./TaosAssistantPanel";
import { useTaosAgentStore } from "@/stores/taos-agent-store";

// Mock child that opens a modal — keeps the test surface narrow
vi.mock("./TaosAssistantSettings", () => ({
  TaosAssistantSettings: ({ open }: { open: boolean }) =>
    open ? <div data-testid="settings-modal" /> : null,
}));

// Mock fetch so settings load doesn't throw in jsdom
const mockFetch = vi.fn().mockResolvedValue({
  ok: true,
  json: async () => ({ model: null }),
});
vi.stubGlobal("fetch", mockFetch);

function resetStore() {
  useTaosAgentStore.setState({
    isOpen: true,
    messages: [],
    model: null,
    streaming: false,
  });
}

describe("TaosAssistantPanel", () => {
  beforeEach(() => {
    resetStore();
    mockFetch.mockClear();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders nothing when closed", () => {
    useTaosAgentStore.setState({ isOpen: false });
    const { container } = render(<TaosAssistantPanel />);
    expect(container.firstChild).toBeNull();
  });

  it("shows title when open", () => {
    render(<TaosAssistantPanel />);
    expect(screen.getByRole("dialog", { name: /taOS agent/i })).toBeInTheDocument();
    expect(screen.getByText("taOS agent")).toBeInTheDocument();
  });

  it("shows empty state with pick-model button when no model", () => {
    render(<TaosAssistantPanel />);
    expect(screen.getByText("Pick a model to get started")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /choose a model/i })).toBeInTheDocument();
  });

  it("shows message input disabled when no model", () => {
    render(<TaosAssistantPanel />);
    const textarea = screen.getByRole("textbox", { name: /message taOS agent/i });
    expect(textarea).toBeDisabled();
  });

  it("shows hint when model is set and no messages", () => {
    useTaosAgentStore.setState({ model: "qwen3" });
    render(<TaosAssistantPanel />);
    expect(screen.getByText(/ask me anything about taOS/i)).toBeInTheDocument();
  });

  it("renders user and assistant messages", () => {
    useTaosAgentStore.setState({
      model: "qwen3",
      messages: [
        { role: "user", content: "Hello there", ts: 1 },
        { role: "assistant", content: "Hi! How can I help?", ts: 2 },
      ],
    });
    render(<TaosAssistantPanel />);
    expect(screen.getByText("Hello there")).toBeInTheDocument();
    expect(screen.getByText("Hi! How can I help?")).toBeInTheDocument();
  });

  it("close button calls closePanel", () => {
    render(<TaosAssistantPanel />);
    const closeBtn = screen.getByRole("button", { name: /close taOS agent/i });
    fireEvent.click(closeBtn);
    expect(useTaosAgentStore.getState().isOpen).toBe(false);
  });

  it("settings cog opens settings modal", () => {
    render(<TaosAssistantPanel />);
    const cogBtn = screen.getByRole("button", { name: /assistant settings/i });
    fireEvent.click(cogBtn);
    expect(screen.getByTestId("settings-modal")).toBeInTheDocument();
  });
});
