import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ModelPickerModal } from "./ModelPickerModal";

vi.mock("./ModelPickerFlow", () => ({
  ModelPickerFlow: vi.fn(() => <div data-testid="model-picker-flow" />),
}));

const mockModels = [
  { id: "llama-3-8b", name: "Llama 3 8B", hostKind: "controller" },
  { id: "gpt-4", name: "GPT-4", host: "openai", hostKind: "cloud" as const },
];

describe("ModelPickerModal", () => {
  it("renders nothing when open is false", () => {
    const { container } = render(
      <ModelPickerModal
        open={false}
        onClose={vi.fn()}
        models={mockModels}
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
        models={mockModels}
        modelsLoaded={true}
        onSelect={vi.fn()}
      />
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("Select Model")).toBeInTheDocument();
  });

  it("renders a custom title when provided", () => {
    render(
      <ModelPickerModal
        open={true}
        onClose={vi.fn()}
        models={mockModels}
        modelsLoaded={true}
        onSelect={vi.fn()}
        title="Pick a Model"
      />
    );
    expect(screen.getByText("Pick a Model")).toBeInTheDocument();
    expect(screen.queryByText("Select Model")).not.toBeInTheDocument();
  });

  it("calls onClose when the close button is clicked", () => {
    const onClose = vi.fn();
    render(
      <ModelPickerModal
        open={true}
        onClose={onClose}
        models={mockModels}
        modelsLoaded={true}
        onSelect={vi.fn()}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when the backdrop is clicked", () => {
    const onClose = vi.fn();
    render(
      <ModelPickerModal
        open={true}
        onClose={onClose}
        models={mockModels}
        modelsLoaded={true}
        onSelect={vi.fn()}
      />
    );
    fireEvent.click(screen.getByRole("dialog"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("does not propagate click events from the inner panel to onClose", () => {
    const onClose = vi.fn();
    render(
      <ModelPickerModal
        open={true}
        onClose={onClose}
        models={mockModels}
        modelsLoaded={true}
        onSelect={vi.fn()}
      />
    );
    const innerPanel = screen.getByRole("dialog").querySelector(".overflow-y-auto");
    if (innerPanel) {
      fireEvent.click(innerPanel);
    }
    expect(onClose).not.toHaveBeenCalled();
  });

  it("passes models and modelsLoaded to ModelPickerFlow", () => {
    const { ModelPickerFlow } = require("./ModelPickerFlow");
    render(
      <ModelPickerModal
        open={true}
        onClose={vi.fn()}
        models={mockModels}
        modelsLoaded={false}
        onSelect={vi.fn()}
      />
    );
    expect(ModelPickerFlow).toHaveBeenCalledWith(
      expect.objectContaining({
        models: mockModels,
        modelsLoaded: false,
      }),
      expect.anything()
    );
  });

  it("calls onSelect and onClose when ModelPickerFlow fires onSelect", () => {
    const onSelect = vi.fn();
    const onClose = vi.fn();
    const { ModelPickerFlow } = require("./ModelPickerFlow");

    render(
      <ModelPickerModal
        open={true}
        onClose={onClose}
        models={mockModels}
        modelsLoaded={true}
        onSelect={onSelect}
      />
    );

    const flowProps = ModelPickerFlow.mock.calls[0][0];
    flowProps.onSelect("gpt-4", mockModels[1]);

    expect(onSelect).toHaveBeenCalledWith("gpt-4", mockModels[1]);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when ModelPickerFlow fires onCancel", () => {
    const onClose = vi.fn();
    const { ModelPickerFlow } = require("./ModelPickerFlow");

    render(
      <ModelPickerModal
        open={true}
        onClose={onClose}
        models={mockModels}
        modelsLoaded={true}
        onSelect={vi.fn()}
      />
    );

    const flowProps = ModelPickerFlow.mock.calls[0][0];
    flowProps.onCancel();

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when Escape is pressed on the dialog", () => {
    const onClose = vi.fn();
    render(
      <ModelPickerModal
        open={true}
        onClose={onClose}
        models={mockModels}
        modelsLoaded={true}
        onSelect={vi.fn()}
      />
    );
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
