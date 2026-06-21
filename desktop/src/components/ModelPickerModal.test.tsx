import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ModelPickerModal } from "./ModelPickerModal";
import type { AgentModel } from "./ModelPickerFlow";

const sampleModels: AgentModel[] = [
  { id: "llama-3-8b", name: "Llama 3 8B", hostKind: "controller" },
  { id: "mistral-7b", name: "Mistral 7B", hostKind: "controller" },
];

describe("ModelPickerModal", () => {
  it("renders nothing when open is false", () => {
    const { container } = render(
      <ModelPickerModal
        open={false}
        onClose={vi.fn()}
        models={sampleModels}
        modelsLoaded={true}
        onSelect={vi.fn()}
      />
    );
    expect(container.innerHTML).toBe("");
  });

  it("renders the dialog with default title when open is true", () => {
    render(
      <ModelPickerModal
        open={true}
        onClose={vi.fn()}
        models={sampleModels}
        modelsLoaded={true}
        onSelect={vi.fn()}
      />
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("Select Model")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
  });

  it("renders a custom title when provided", () => {
    render(
      <ModelPickerModal
        open={true}
        onClose={vi.fn()}
        models={sampleModels}
        modelsLoaded={true}
        onSelect={vi.fn()}
        title="Pick a model for this agent"
      />
    );
    expect(screen.getByText("Pick a model for this agent")).toBeInTheDocument();
  });

  it("calls onClose when the close button is clicked", () => {
    const onClose = vi.fn();
    render(
      <ModelPickerModal
        open={true}
        onClose={onClose}
        models={sampleModels}
        modelsLoaded={true}
        onSelect={vi.fn()}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("calls onClose when the backdrop is clicked", () => {
    const onClose = vi.fn();
    render(
      <ModelPickerModal
        open={true}
        onClose={onClose}
        models={sampleModels}
        modelsLoaded={true}
        onSelect={vi.fn()}
      />
    );
    fireEvent.click(screen.getByRole("dialog"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("shows loading text when modelsLoaded is false", () => {
    render(
      <ModelPickerModal
        open={true}
        onClose={vi.fn()}
        models={[]}
        modelsLoaded={false}
        onSelect={vi.fn()}
      />
    );
    expect(screen.getByText("Loading models…")).toBeInTheDocument();
  });

  it("shows 'No models available.' when models is empty and modelsLoaded is true", () => {
    render(
      <ModelPickerModal
        open={true}
        onClose={vi.fn()}
        models={[]}
        modelsLoaded={true}
        onSelect={vi.fn()}
      />
    );
    expect(screen.getByText("No models available.")).toBeInTheDocument();
  });

  it("calls onSelect then onClose when a model is picked", () => {
    const onClose = vi.fn();
    const onSelect = vi.fn();
    render(
      <ModelPickerModal
        open={true}
        onClose={onClose}
        models={sampleModels}
        modelsLoaded={true}
        onSelect={onSelect}
      />
    );
    // ModelPickerFlow auto-selects the only source (local), so the list screen
    // renders model buttons. Click the first model.
    fireEvent.click(screen.getByText("Llama 3 8B"));
    expect(onSelect).toHaveBeenCalledWith("llama-3-8b", sampleModels[0]);
    expect(onClose).toHaveBeenCalledOnce();
  });
});
