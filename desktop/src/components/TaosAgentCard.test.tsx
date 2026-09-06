// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { TaosAgentCard } from "./TaosAgentCard";

const mockConfig = {
  model: "claude-sonnet-4-20250514",
  permitted_models: ["claude-sonnet-4-20250514", "gpt-4o"],
  persona: "You are a helpful assistant.",
  key_masked: "sk-...abcd",
  framework: "opencode" as const,
  system: true as const,
};

vi.mock("@/lib/taos-agent-api", () => ({
  fetchTaosAgentConfig: vi.fn(),
  setTaosAgentModel: vi.fn(),
  setTaosAgentPermitted: vi.fn(),
  setTaosAgentPersona: vi.fn(),
}));

vi.mock("@/components/ModelPickerModal", () => ({
  ModelPickerModal: () => null,
}));

import { fetchTaosAgentConfig, setTaosAgentPermitted } from "@/lib/taos-agent-api";

describe("TaosAgentCard", () => {
  beforeEach(() => {
    vi.mocked(fetchTaosAgentConfig).mockResolvedValue(mockConfig);
  });

  it("renders the agent card header, model, and permitted models", async () => {
    render(<TaosAgentCard />);

    expect(await screen.findByText("taOS agent")).toBeInTheDocument();
    expect(screen.getAllByText("claude-sonnet-4-20250514").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("opencode")).toBeInTheDocument();
    expect(screen.getByText("System")).toBeInTheDocument();
    expect(screen.getByText("sk-...abcd")).toBeInTheDocument();
    expect(screen.getByText("Permitted models")).toBeInTheDocument();
    expect(screen.getByText("gpt-4o")).toBeInTheDocument();
  });

  it("shows a loading skeleton before config resolves", () => {
    vi.mocked(fetchTaosAgentConfig).mockReturnValue(new Promise(() => {}));
    render(<TaosAgentCard />);
    expect(screen.getByLabelText("Loading taOS agent")).toBeInTheDocument();
  });

  it("shows an error message when config fails to load", async () => {
    vi.mocked(fetchTaosAgentConfig).mockRejectedValue(new Error("network down"));
    render(<TaosAgentCard />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Failed to load taOS agent config: network down");
  });

  it("removes a permitted model and shows the save button", async () => {
    render(<TaosAgentCard />);
    await screen.findByText("taOS agent");

    fireEvent.click(screen.getByRole("button", { name: /remove gpt-4o from taos agent permitted models/i }));
    expect(screen.queryByText("gpt-4o")).not.toBeInTheDocument();

    const saveBtn = screen.getByRole("button", { name: /save taos agent permitted models/i });
    expect(saveBtn).toBeInTheDocument();

    vi.mocked(setTaosAgentPermitted).mockResolvedValue({
      permitted_models: ["claude-sonnet-4-20250514"],
      key_rescoped: false,
    });
    fireEvent.click(saveBtn);
    await waitFor(() => expect(setTaosAgentPermitted).toHaveBeenCalledWith(["claude-sonnet-4-20250514"]));
  });
});
