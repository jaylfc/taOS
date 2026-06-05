/**
 * Unit tests for TaosAgentDetailPanel — the settings screen for the taOS
 * system agent, wired to /api/taos-agent/* endpoints.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import React from "react";

// Mock taos-agent-api
vi.mock("@/lib/taos-agent-api", () => ({
  fetchTaosAgentConfig: vi.fn(),
  setTaosAgentModel: vi.fn(),
  setTaosAgentPermitted: vi.fn(),
  setTaosAgentPersona: vi.fn(),
}));
vi.mock("@/components/ModelPickerModal", () => ({
  ModelPickerModal: () => null,
}));
vi.mock("@/components/ui", () => ({
  Button: ({ children, onClick, "aria-label": ariaLabel, ...rest }:
    React.ButtonHTMLAttributes<HTMLButtonElement> & { children?: React.ReactNode }) => (
    <button onClick={onClick} aria-label={ariaLabel} {...rest}>{children}</button>
  ),
  Tabs: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TabsContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TabsList: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TabsTrigger: ({ children }: { children: React.ReactNode }) => <button>{children}</button>,
}));

import {
  fetchTaosAgentConfig,
  setTaosAgentPermitted,
  setTaosAgentPersona,
} from "@/lib/taos-agent-api";
import { TaosAgentDetailPanel } from "../agents/TaosAgentDetailPanel";

const BASE_CONFIG = {
  model: "ollama/llama3",
  permitted_models: ["ollama/llama3", "ollama/qwen3"],
  persona: "Be helpful.",
  key_masked: "sk-test…key",
  framework: "opencode" as const,
  system: true as const,
};

describe("<TaosAgentDetailPanel />", () => {
  const onClose = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetchTaosAgentConfig).mockResolvedValue({ ...BASE_CONFIG });
  });

  it("renders Settings and Persona tab triggers", async () => {
    render(<TaosAgentDetailPanel onClose={onClose} />);
    await waitFor(() =>
      expect(screen.getByText("Settings")).toBeInTheDocument()
    );
    expect(screen.getByText("Persona")).toBeInTheDocument();
  });

  it("shows the current model", async () => {
    render(<TaosAgentDetailPanel onClose={onClose} />);
    // ollama/llama3 appears in both the model display row and the permitted chips
    await waitFor(() => {
      const matches = screen.getAllByText("ollama/llama3");
      expect(matches.length).toBeGreaterThan(0);
    });
  });

  it("shows permitted model chips", async () => {
    render(<TaosAgentDetailPanel onClose={onClose} />);
    await waitFor(() =>
      expect(screen.getByText("ollama/qwen3")).toBeInTheDocument()
    );
  });

  it("remove button is disabled for the current model chip", async () => {
    render(<TaosAgentDetailPanel onClose={onClose} />);
    await waitFor(() =>
      expect(screen.getByLabelText("Cannot remove current model ollama/llama3")).toBeDisabled()
    );
  });

  it("remove button is enabled for non-current permitted models", async () => {
    render(<TaosAgentDetailPanel onClose={onClose} />);
    await waitFor(() =>
      expect(
        screen.getByLabelText("Remove ollama/qwen3 from taOS agent permitted models")
      ).not.toBeDisabled()
    );
  });

  it("persona textarea pre-fills with config persona", async () => {
    render(<TaosAgentDetailPanel onClose={onClose} />);
    await waitFor(() => {
      const ta = screen.getByLabelText("taOS agent persona — system-prompt override");
      expect((ta as HTMLTextAreaElement).value).toBe("Be helpful.");
    });
  });

  it("Save (persona) button appears only after editing persona", async () => {
    render(<TaosAgentDetailPanel onClose={onClose} />);
    await waitFor(() =>
      screen.getByLabelText("taOS agent persona — system-prompt override")
    );
    // Initially no save button
    expect(screen.queryByLabelText("Save taOS agent persona")).toBeNull();
    // Edit the textarea
    fireEvent.change(screen.getByLabelText("taOS agent persona — system-prompt override"), {
      target: { value: "Be terse." },
    });
    await waitFor(() =>
      expect(screen.getByLabelText("Save taOS agent persona")).toBeInTheDocument()
    );
  });

  it("calls setTaosAgentPersona when Save (persona) is clicked", async () => {
    vi.mocked(setTaosAgentPersona).mockResolvedValue({ persona: "Be terse." });
    render(<TaosAgentDetailPanel onClose={onClose} />);
    await waitFor(() =>
      screen.getByLabelText("taOS agent persona — system-prompt override")
    );
    fireEvent.change(screen.getByLabelText("taOS agent persona — system-prompt override"), {
      target: { value: "Be terse." },
    });
    await waitFor(() => screen.getByLabelText("Save taOS agent persona"));
    fireEvent.click(screen.getByLabelText("Save taOS agent persona"));
    await waitFor(() =>
      expect(setTaosAgentPersona).toHaveBeenCalledWith("Be terse.")
    );
  });

  it("calls setTaosAgentPermitted when removing a model and saving", async () => {
    vi.mocked(setTaosAgentPermitted).mockResolvedValue({
      permitted_models: ["ollama/llama3"],
      key_rescoped: false,
    });
    render(<TaosAgentDetailPanel onClose={onClose} />);
    await waitFor(() =>
      screen.getByLabelText("Remove ollama/qwen3 from taOS agent permitted models")
    );
    fireEvent.click(
      screen.getByLabelText("Remove ollama/qwen3 from taOS agent permitted models")
    );
    await waitFor(() =>
      screen.getByLabelText("Save taOS agent permitted models")
    );
    fireEvent.click(screen.getByLabelText("Save taOS agent permitted models"));
    await waitFor(() =>
      expect(setTaosAgentPermitted).toHaveBeenCalledWith(["ollama/llama3"])
    );
  });

  it("calls onClose when Close button is clicked", async () => {
    render(<TaosAgentDetailPanel onClose={onClose} />);
    await waitFor(() => screen.getByRole("button", { name: /close detail panel/i }));
    fireEvent.click(screen.getByRole("button", { name: /close detail panel/i }));
    expect(onClose).toHaveBeenCalled();
  });

  it("shows an error alert when fetchTaosAgentConfig fails", async () => {
    vi.mocked(fetchTaosAgentConfig).mockRejectedValue(new Error("Network error"));
    render(<TaosAgentDetailPanel onClose={onClose} />);
    await waitFor(() =>
      expect(screen.getAllByRole("alert").length).toBeGreaterThan(0)
    );
    expect(screen.getAllByText(/network error/i).length).toBeGreaterThan(0);
  });
});
