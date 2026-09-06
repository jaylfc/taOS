// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { TaosAssistantSettings } from "./TaosAssistantSettings";
import { useTaosAgentStore } from "@/stores/taos-agent-store";

vi.mock("@/lib/models", () => ({
  loadAgentModels: vi.fn(),
}));

import { loadAgentModels } from "@/lib/models";

const mockFetch = vi.fn();

function setupFetch() {
  mockFetch.mockImplementation((url: string) => {
    if (url === "/api/models") {
      return Promise.resolve({
        ok: true,
        json: async () => ({ models: [] }),
      });
    }
    if (url === "/api/taos-agent/settings") {
      return Promise.resolve({ ok: true });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });
  vi.stubGlobal("fetch", mockFetch);
}

function resetStore() {
  useTaosAgentStore.setState({
    isOpen: false,
    messages: [],
    model: null,
    streaming: false,
    settingsOpen: false,
  });
}

describe("TaosAssistantSettings", () => {
  beforeEach(() => {
    resetStore();
    setupFetch();
    vi.mocked(loadAgentModels).mockResolvedValue([]);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("renders nothing when open is false", () => {
    const { container } = render(
      <TaosAssistantSettings open={false} onClose={vi.fn()} />
    );
    expect(container.firstChild).toBeNull();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("renders the dialog with title when open is true", () => {
    render(<TaosAssistantSettings open={true} onClose={vi.fn()} />);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("taOS agent — Settings")).toBeInTheDocument();
  });

  it("shows the current model name in the header when set", () => {
    useTaosAgentStore.setState({ model: "gpt-4o" });
    render(<TaosAssistantSettings open={true} onClose={vi.fn()} />);
    expect(screen.getByText("gpt-4o")).toBeInTheDocument();
  });

  it("does not show a model name span when no model is set", () => {
    useTaosAgentStore.setState({ model: null });
    render(<TaosAssistantSettings open={true} onClose={vi.fn()} />);
    expect(screen.getByText("taOS agent — Settings")).toBeInTheDocument();
    expect(screen.queryByText("gpt-4o")).not.toBeInTheDocument();
  });

  it("calls onClose when the close button is clicked", () => {
    const onClose = vi.fn();
    render(<TaosAssistantSettings open={true} onClose={onClose} />);
    const closeBtn = screen.getByRole("button", { name: /close settings/i });
    fireEvent.click(closeBtn);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when the backdrop is clicked", () => {
    const onClose = vi.fn();
    render(<TaosAssistantSettings open={true} onClose={onClose} />);
    const backdrop = screen.getByRole("dialog");
    fireEvent.click(backdrop);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("loads the unified agent model list on open", async () => {
    render(<TaosAssistantSettings open={true} onClose={vi.fn()} />);

    await waitFor(() => {
      expect(loadAgentModels).toHaveBeenCalledTimes(1);
    });
  });

  it("sends PATCH to /api/taos-agent/settings with selected model and closes", async () => {
    const onClose = vi.fn();
    useTaosAgentStore.setState({ model: "old-model" });

    vi.mocked(loadAgentModels).mockResolvedValue([
      { key: "controller:llama-3", id: "llama-3", name: "Llama 3", host: "controller", hostKind: "controller" },
    ]);

    render(<TaosAssistantSettings open={true} onClose={onClose} />);

    await waitFor(() => {
      expect(screen.getByText("Llama 3")).toBeInTheDocument();
    });

    const modelBtn = screen.getByText("Llama 3");
    fireEvent.click(modelBtn);

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        "/api/taos-agent/settings",
        expect.objectContaining({
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ model: "llama-3" }),
        })
      );
    });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(useTaosAgentStore.getState().model).toBe("llama-3");
  });
});
