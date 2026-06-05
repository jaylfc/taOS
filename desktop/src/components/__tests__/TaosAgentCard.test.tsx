import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { TaosAgentCard } from "../TaosAgentCard";

// Mock the taos-agent-api helpers
vi.mock("@/lib/taos-agent-api", () => ({
  fetchTaosAgentConfig: vi.fn(),
  setTaosAgentModel: vi.fn(),
  setTaosAgentPermitted: vi.fn(),
  setTaosAgentPersona: vi.fn(),
}));

// Mock ModelPickerModal — it is always closed in these tests
vi.mock("@/components/ModelPickerModal", () => ({
  ModelPickerModal: () => null,
}));

import {
  fetchTaosAgentConfig,
  setTaosAgentPermitted,
} from "@/lib/taos-agent-api";

const BASE_CONFIG = {
  model: "ollama/llama3",
  permitted_models: ["ollama/llama3", "ollama/qwen3"],
  persona: "",
  key_masked: "sk-age\u2026key1",
  framework: "opencode" as const,
  system: true as const,
};

describe("<TaosAgentCard />", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetchTaosAgentConfig).mockResolvedValue({ ...BASE_CONFIG });
  });

  it("renders title, framework pill, and system badge", async () => {
    render(<TaosAgentCard />);
    await waitFor(() => expect(screen.getByText("taOS agent")).toBeInTheDocument());
    expect(screen.getByText("opencode")).toBeInTheDocument();
    expect(screen.getByText("System")).toBeInTheDocument();
  });

  it("renders current model from config", async () => {
    render(<TaosAgentCard />);
    // Wait for the card to load — the model appears in both the model row and chips,
    // so use getAllByText and assert at least one instance.
    await waitFor(() => {
      const matches = screen.getAllByText("ollama/llama3");
      expect(matches.length).toBeGreaterThan(0);
    });
  });

  it("renders the masked key", async () => {
    render(<TaosAgentCard />);
    await waitFor(() => expect(screen.getByLabelText("Masked API key")).toBeInTheDocument());
  });

  it("renders permitted model chips", async () => {
    render(<TaosAgentCard />);
    await waitFor(() => {
      // qwen3 only appears in the chips section — unambiguous
      expect(screen.getByText("ollama/qwen3")).toBeInTheDocument();
    });
    // llama3 appears at least once (chip)
    expect(screen.getAllByText("ollama/llama3").length).toBeGreaterThan(0);
  });

  it("remove button is disabled on current model chip", async () => {
    render(<TaosAgentCard />);
    await waitFor(() =>
      expect(screen.getByLabelText("Cannot remove current model ollama/llama3")).toBeInTheDocument()
    );
    const removeBtn = screen.getByLabelText("Cannot remove current model ollama/llama3");
    expect(removeBtn).toBeDisabled();
  });

  it("remove button is enabled for non-current models", async () => {
    render(<TaosAgentCard />);
    await waitFor(() =>
      expect(screen.getByLabelText("Remove ollama/qwen3 from taOS agent permitted models")).toBeInTheDocument()
    );
    const removeBtn = screen.getByLabelText("Remove ollama/qwen3 from taOS agent permitted models");
    expect(removeBtn).not.toBeDisabled();
  });

  it("removing a chip marks draft dirty and shows Save button", async () => {
    render(<TaosAgentCard />);
    await waitFor(() =>
      expect(screen.getByLabelText("Remove ollama/qwen3 from taOS agent permitted models")).toBeInTheDocument()
    );

    fireEvent.click(screen.getByLabelText("Remove ollama/qwen3 from taOS agent permitted models"));

    await waitFor(() =>
      expect(screen.getByLabelText("Save taOS agent permitted models")).toBeInTheDocument()
    );
  });

  it("clicking Save calls setTaosAgentPermitted with remaining models", async () => {
    vi.mocked(setTaosAgentPermitted).mockResolvedValue({
      permitted_models: ["ollama/llama3"],
      key_rescoped: true,
    });

    render(<TaosAgentCard />);
    await waitFor(() =>
      expect(screen.getByLabelText("Remove ollama/qwen3 from taOS agent permitted models")).toBeInTheDocument()
    );

    fireEvent.click(screen.getByLabelText("Remove ollama/qwen3 from taOS agent permitted models"));
    await waitFor(() =>
      expect(screen.getByLabelText("Save taOS agent permitted models")).toBeInTheDocument()
    );
    fireEvent.click(screen.getByLabelText("Save taOS agent permitted models"));

    await waitFor(() =>
      expect(setTaosAgentPermitted).toHaveBeenCalledWith(["ollama/llama3"])
    );
  });

  it("renders host-resident note", async () => {
    render(<TaosAgentCard />);
    await waitFor(() => expect(screen.getByText(/host-resident/i)).toBeInTheDocument());
  });

  it("renders error state when fetch fails", async () => {
    vi.mocked(fetchTaosAgentConfig).mockRejectedValue(new Error("Network error"));
    render(<TaosAgentCard />);
    await waitFor(() =>
      expect(screen.getByRole("alert")).toBeInTheDocument()
    );
    expect(screen.getByText(/network error/i)).toBeInTheDocument();
  });
});
